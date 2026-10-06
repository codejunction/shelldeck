import subprocess
import sys
import time

import psutil
import pytest
from fastapi.testclient import TestClient

from shelldeck import auth, remotes, server, stats


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    monkeypatch.setattr(server, "ALLOWED_HOSTS", {"testserver"})
    with TestClient(server.app) as c:
        c.headers["X-Shelldeck-Token"] = auth.read_cli_token()
        yield c


def test_validate_rejects_anything_that_could_reach_a_shell():
    ok = remotes.validate({"kind": "ssh", "host": "srv.example.com", "user": "dev", "port": "2222"})
    assert ok["name"] == "dev@srv.example.com" and ok["port"] == 2222
    assert remotes.ssh_line(ok) == "ssh -p 2222 -l dev srv.example.com"
    assert remotes.ssh_line(remotes.validate({"host": "10.0.0.5", "identity": "~/keys/my key"})) == 'ssh -i "~/keys/my key" 10.0.0.5'
    # LDAP / NTID style accounts: CORP\\jdoe is single-quoted for bash/pwsh/fish, bare for cmd
    ntid = remotes.validate({"host": "vm1", "user": "CORP\\jdoe"})
    assert remotes.ssh_line(ntid, "~", "pwsh") == "ssh -l 'CORP\\jdoe' vm1" and remotes.ssh_line(ntid, "~", "cmd") == "ssh -l CORP\\jdoe vm1"
    # from a Windows host the folder command is quoted per local shell
    assert remotes.ssh_line(ok, "/srv/app", "pwsh") == "ssh -t -p 2222 -l dev srv.example.com 'cd /srv/app && exec $SHELL -l'"
    assert remotes.ssh_line(ok, "/srv/app", "cmd") == 'ssh -t -p 2222 -l dev srv.example.com "cd /srv/app && exec $SHELL -l"'
    assert remotes.ssh_line(remotes.validate({"host": "vm1", "user": "jdoe@corp.example.com"})) == "ssh -l jdoe@corp.example.com vm1"
    for bad, code in [({"host": "h", "user": "CORP\\j;d"}, "invalid_user"), ({"host": "h", "user": "a@b@c"}, "invalid_user"),
                      ({"host": "-oProxyCommand=x"}, "invalid_host"), ({"host": "a;rm -rf ~"}, "invalid_host"), ({"host": "h", "user": "a b"}, "invalid_user"),
                      ({"host": "h", "user": "-l"}, "invalid_user"), ({"host": "h", "port": 70000}, "invalid_port"), ({"host": "h", "kind": "vnc"}, "invalid_kind"),
                      ({"host": "h", "identity": "k; id"}, "invalid_identity"), ({"host": "h", "identity": "-F x"}, "invalid_identity")]:
        with pytest.raises(remotes.RemoteError) as e:
            remotes.validate(bad)
        assert str(e.value) == code


def test_rdp_argv_per_os(monkeypatch):
    r = remotes.validate({"kind": "rdp", "host": "win-box", "user": "ana", "port": 3390})
    monkeypatch.setattr(remotes.sys, "platform", "win32")
    assert remotes.rdp_argv(r) == ["mstsc", "/v:win-box:3390"]
    monkeypatch.setattr(remotes.sys, "platform", "linux")
    monkeypatch.setattr(remotes.shutil, "which", lambda exe: "/usr/bin/xfreerdp" if exe == "xfreerdp" else None)
    assert remotes.rdp_argv(r) == ["/usr/bin/xfreerdp", "/v:win-box:3390", "/u:ana", "/dynamic-resolution"]
    monkeypatch.setattr(remotes.shutil, "which", lambda exe: None)
    with pytest.raises(remotes.RemoteError):
        remotes.rdp_argv(r)


def test_remote_crud_and_ssh_connect(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_attach", lambda s, r, c: None)
    work = tmp_path / "w"
    work.mkdir()
    pid = client.post("/api/projects", json={"path": str(work)}).json()["id"]
    assert client.post("/api/remotes", json={"host": "bad host"}).json()["error"] == "invalid_host"
    r = client.post("/api/remotes", json={"host": "srv", "user": "dev", "name": "Build box"}).json()
    assert r["kind"] == "ssh" and r["port"] == 22
    assert client.put(f"/api/remotes/{r['id']}", json={"host": "srv2", "user": "dev", "project_id": "nope"}).json()["error"] == "project_not_found"
    out = client.post(f"/api/remotes/{r['id']}/connect").json()
    assert out["command"] == "ssh -t -l dev srv 'cd / && exec $SHELL -l'" and out["session"]["name"] == "Build box"
    home = next(p for p in client.get("/api/projects").json()["projects"] if p["id"] == out["project_id"])
    assert home["id"] == r["project_id_root"]  # created with the machine
    assert home["remote_id"] == r["id"] and home["remote_path"] == "/" and home["name"] == "Build box" and home["exists"]
    # a folder on that machine is a project of its own; its terminals ssh there and cd into it
    assert client.post("/api/projects", json={"remote_id": r["id"], "path": "/srv/app; rm -rf /"}).json()["error"] == "invalid_remote_path"
    app = client.post("/api/projects", json={"remote_id": r["id"], "path": "/srv/app/"}).json()
    assert app["name"] == "app" and app["remote_path"] == "/srv/app" and app["id"] != pid
    sess = {**client.post("/api/sessions", json={"project_id": app["id"]}).json(), "shell": "bash"}  # bash isn't a kind on Windows
    assert server._remote_line(sess) == "ssh -t -l dev srv 'cd /srv/app && exec $SHELL -l'"
    assert server._remote_line({**sess, "shell": "cmd"}) == 'ssh -t -l dev srv "cd /srv/app && exec $SHELL -l"'
    assert server._remote_line(client.post("/api/sessions", json={"project_id": pid}).json()) is None  # local project
    assert client.get("/api/remotes").json()["remotes"][0]["last_used_at"]
    rdp = client.post("/api/remotes", json={"kind": "rdp", "host": "win"}).json()
    monkeypatch.setattr(remotes, "open_rdp", lambda r: ["mstsc", "/v:win"])
    assert client.post(f"/api/remotes/{rdp['id']}/connect").json() == {"kind": "rdp", "command": "mstsc /v:win"}
    monkeypatch.setattr(server, "_host", lambda request: False)
    assert client.post(f"/api/remotes/{rdp['id']}/connect").status_code == 403  # opens a window on the host only
    assert client.delete(f"/api/remotes/{r['id']}").json() == {"status": "ok"}
    assert not [p for p in client.get("/api/projects").json()["projects"] if p.get("remote_id") == r["id"]]  # its projects went too
    assert client.delete(f"/api/remotes/{r['id']}").status_code == 404


def test_end_only_ends_processes_inside_the_terminal():
    # a stand-in shell with a child (and grandchild) that would run forever. On Windows a venv python.exe is a
    # launcher that starts the real interpreter as one more process, so use the base interpreter: one process a level
    py = getattr(sys, "_base_executable", sys.executable)
    code = f"import subprocess,time; subprocess.Popen([{py!r},'-c','import time; time.sleep(60)']); time.sleep(60)"
    shell = subprocess.Popen([py, "-c", f"import subprocess,time; subprocess.Popen([{py!r},'-c',{code!r}]); time.sleep(60)"])
    try:
        for _ in range(50):
            kids = psutil.Process(shell.pid).children(recursive=True)
            if len(kids) >= 2:
                break
            time.sleep(0.1)
        child = psutil.Process(shell.pid).children()[0].pid
        listed = {p["pid"] for p in stats.processes(shell.pid)}
        assert child in listed and shell.pid not in listed
        assert stats.end(shell.pid, shell.pid) == "not_in_terminal"  # never the shell itself
        assert stats.end(shell.pid, psutil.Process().pid) == "not_in_terminal"  # nor anything outside it
        assert stats.end(shell.pid, child) == "ended"
        left = [p for p in psutil.Process(shell.pid).children(recursive=True) if stats._running(p)]  # gone or zombie: ended
        assert not left  # its child went too (zombies: this stand-in shell doesn't reap, a real one does)
        assert shell.poll() is None  # the shell keeps running
    finally:
        for p in psutil.Process(shell.pid).children(recursive=True) if shell.poll() is None else []:
            p.kill()
        shell.kill()


def test_cli_notes_reach_open_pages(client, monkeypatch):
    sent = []

    async def broadcast(msg, host_only=False):
        sent.append(msg)

    monkeypatch.setattr(server, "_broadcast", broadcast)
    n = client.post("/api/scratch", json={"body": "# From an agent\n"}).json()
    assert sent == [{"type": "scratch", "id": n["id"]}]
    assert client.get(f"/api/scratch/{n['id']}").json()["body"] == "# From an agent\n"


def test_connection_test_steps(client, monkeypatch):
    import socket

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()  # nothing listens there now
    r = client.post("/api/remotes/test", json={"host": "127.0.0.1", "port": port, "user": "CORP\\jdoe"}).json()
    assert r["ok"] is False and r["step"] == "network"
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    try:
        class Done:
            returncode, stderr = 255, "CORP\\jdoe@127.0.0.1: Permission denied (publickey,password)."

        seen = []
        monkeypatch.setattr(remotes.shutil, "which", lambda exe: "/usr/bin/ssh")
        monkeypatch.setattr(remotes.subprocess, "run", lambda argv, **kw: seen.append(argv) or Done())
        r = client.post("/api/remotes/test", json={"host": "127.0.0.1", "port": srv.getsockname()[1], "user": "CORP\\jdoe"}).json()
        assert r["ok"] and r["step"] == "auth" and "password" in r["message"]  # LDAP/NTID: reachable, ssh asks in the terminal
        assert ["-l", "CORP\\jdoe"] == seen[0][seen[0].index("-l"):seen[0].index("-l") + 2] and "BatchMode=yes" in seen[0]
    finally:
        srv.close()


def test_password_reaches_ssh_only_through_askpass(monkeypatch):
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    seen = {}
    real_run = subprocess.run

    class Ok:
        returncode, stderr = 0, ""

    def run(argv, env=None, **kw):
        seen["argv"], seen["env"] = argv, env
        seen["helper"] = real_run([env["SSH_ASKPASS"]], capture_output=True, text=True, env=env, shell=sys.platform == "win32").stdout
        return Ok()

    try:
        monkeypatch.setattr(remotes.shutil, "which", lambda exe: "/usr/bin/ssh")
        monkeypatch.setattr(remotes.subprocess, "run", run)
        r = remotes.validate({"host": "127.0.0.1", "port": srv.getsockname()[1], "user": "CORP\\jdoe"})
        out = remotes.test_ssh(r, password='p@ss w$rd&|"x')
    finally:
        srv.close()
    assert out["ok"] and out["step"] == "done" and "password" in out["message"]
    assert "p@ss" not in " ".join(seen["argv"])  # never on the command line
    assert seen["env"]["SSH_ASKPASS_REQUIRE"] == "force" and "BatchMode=no" in seen["argv"]
    assert seen["helper"].rstrip("\r\n") == 'p@ss w$rd&|"x'
