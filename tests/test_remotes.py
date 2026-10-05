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
    assert remotes.ssh_line(ok) == "ssh -p 2222 dev@srv.example.com"
    assert remotes.ssh_line(remotes.validate({"host": "10.0.0.5", "identity": "~/keys/my key"})) == 'ssh -i "~/keys/my key" 10.0.0.5'
    for bad, code in [({"host": "-oProxyCommand=x"}, "invalid_host"), ({"host": "a;rm -rf ~"}, "invalid_host"), ({"host": "h", "user": "a b"}, "invalid_user"),
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
    started = []

    async def fake_start(sid, line):
        started.append((sid, line))

    monkeypatch.setattr(server, "_start_agent", fake_start)
    monkeypatch.setattr(server, "_attach", lambda s, r, c: None)
    work = tmp_path / "w"
    work.mkdir()
    pid = client.post("/api/projects", json={"path": str(work)}).json()["id"]
    assert client.post("/api/remotes", json={"host": "bad host"}).json()["error"] == "invalid_host"
    r = client.post("/api/remotes", json={"host": "srv", "user": "dev", "name": "Build box"}).json()
    assert r["kind"] == "ssh" and r["port"] == 22
    assert client.put(f"/api/remotes/{r['id']}", json={"host": "srv2", "user": "dev", "project_id": "nope"}).json()["error"] == "project_not_found"
    out = client.post(f"/api/remotes/{r['id']}/connect", json={"project_id": pid}).json()
    assert out["command"] == "ssh dev@srv" and started == [(out["session"]["id"], "ssh dev@srv")]
    assert out["session"]["name"] == "Build box" and out["session"]["project_id"] == pid
    assert client.get("/api/remotes").json()["remotes"][0]["last_used_at"]
    rdp = client.post("/api/remotes", json={"kind": "rdp", "host": "win"}).json()
    monkeypatch.setattr(remotes, "open_rdp", lambda r: ["mstsc", "/v:win"])
    assert client.post(f"/api/remotes/{rdp['id']}/connect").json() == {"kind": "rdp", "command": "mstsc /v:win"}
    monkeypatch.setattr(server, "_host", lambda request: False)
    assert client.post(f"/api/remotes/{rdp['id']}/connect").status_code == 403  # opens a window on the host only
    assert client.delete(f"/api/remotes/{r['id']}").json() == {"status": "ok"}
    assert client.delete(f"/api/remotes/{r['id']}").status_code == 404


def test_end_only_ends_processes_inside_the_terminal():
    # a stand-in shell with a child (and grandchild) that would run forever
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    shell = subprocess.Popen([sys.executable, "-c", f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{code!r}]); time.sleep(60)"])
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
        left = [p for p in psutil.Process(shell.pid).children(recursive=True) if p.status() != psutil.STATUS_ZOMBIE]
        assert not left  # its child went too (zombies: this stand-in shell doesn't reap, a real one does)
        assert shell.poll() is None  # the shell keeps running
    finally:
        shell.kill()


def test_cli_notes_reach_open_pages(client, monkeypatch):
    sent = []

    async def broadcast(msg, host_only=False):
        sent.append(msg)

    monkeypatch.setattr(server, "_broadcast", broadcast)
    n = client.post("/api/scratch", json={"body": "# From an agent\n"}).json()
    assert sent == [{"type": "scratch", "id": n["id"]}]
    assert client.get(f"/api/scratch/{n['id']}").json()["body"] == "# From an agent\n"
