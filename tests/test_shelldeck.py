import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from shelldeck import auth, db, server, shells
from shelldeck.pty import PtyManager, Scrollback

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    monkeypatch.setattr(server, "ALLOWED_HOSTS", {"testserver"})
    with TestClient(server.app) as c:
        c.headers["X-Shelldeck-Token"] = auth.read_cli_token()  # act as the host CLI
        yield c


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """A client with no CLI token: it must log in like a browser."""
    auth._failures.clear()
    monkeypatch.setitem(server._active, "h", None)
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    monkeypatch.setattr(server, "ALLOWED_HOSTS", {"testserver"})
    with TestClient(server.app) as c:
        yield c


def test_scrollback_keeps_tail():
    sb = Scrollback(limit=10)
    for chunk in ("abcd", "efgh", "ijkl"):
        sb.add(chunk)
    assert sb.text() == "cdefghijkl"
    sb.add("x" * 25)
    assert sb.text() == "x" * 10


def test_scrollback_survives_restart(tmp_path):
    m = PtyManager(store=tmp_path)
    m.scrollback["abc123"] = sb = Scrollback()
    sb.add("hello from before\r\n")
    m.save()
    restored = PtyManager(store=tmp_path)._restore("abc123").text()
    assert restored.startswith("hello from before\r\n") and "restored" in restored  # CRLF kept
    assert PtyManager(store=tmp_path)._restore("../evil").text() == ""
    m.snapshot("abc123", "rendered screen")
    assert sb.persisted() == "rendered screen"
    sb.add("x" * 5000)  # lots of output since the snapshot: fall back to raw
    assert sb.persisted().endswith("x" * 5000)
    m.prune(keep={"other"})
    assert not (tmp_path / "abc123.log").exists()


def test_stats(client):
    import os

    from shelldeck import stats

    r = stats.collect({"me": os.getpid(), "gone": 2**22 + 7})
    assert r["sessions"]["me"]["mem"] > 0 and r["sessions"]["me"]["procs"] >= 1
    assert "gone" not in r["sessions"]
    s = client.get("/api/stats").json()
    assert s["system"]["mem_total"] > 0 and 0 <= s["system"]["cpu"] <= 100 and s["sessions"] == {}


def test_agents(client, tmp_path):
    import os
    import subprocess
    import sys

    from shelldeck import agents, stats

    # a node/python-installed agent is found by its package path, with the --model it was given
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "@anthropic-ai/claude-code", "--model", "opus"])
    try:
        time.sleep(0.5)
        assert agents.running({"me": os.getpid()}) == {"me": ("claude", "opus")}
        assert stats.collect({"me": os.getpid()})["sessions"]["me"]["agent"] == {"key": "claude", "label": "Claude Code", "model": "opus"}
    finally:
        child.kill()
    data = client.get("/api/agents").json()
    assert data["running"] == [] and {"claude", "codex", "devin"} <= {a["key"] for a in data["agents"]}

    # peers read each other's screen and type into each other
    sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
    server.manager.scrollback.setdefault(sid, Scrollback()).add("\x1b[32mworking on auth\x1b[0m\r\ndone\r\n\r\n")
    assert client.get(f"/api/sessions/{sid}/screen", params={"lines": 1}).json()["text"] == "done"
    assert client.get("/api/sessions/nope/screen").status_code == 404
    assert client.post(f"/api/sessions/{sid}/input", json={"text": "hi"}).json()["error"] == "not_running"
    assert client.post(f"/api/sessions/{sid}/input", json={}).json()["error"] == "text_required"


def test_history_search_and_open(client, tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "evil.bat").write_text("echo")
    sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]

    # history: recorded over the socket, deduped per start time, searchable and filterable
    with client.websocket_connect(f"/ws/{sid}") as ws:
        for cmd, code in (("git status", 0), ("pytest -x", 1), ("pytest -x", 1)):
            ws.send_json({"type": "command", "cmd": cmd, "exit": code, "ms": 1200, "at": f"2026-01-01T00:00:0{code}Z"})
        ws.send_json({"type": "snapshot", "data": "hello\r\nerror: boom at src/app.py:3\r\n"})
    rows = client.get("/api/history").json()["commands"]
    assert [r["command"] for r in rows] == ["pytest -x", "git status"]
    assert [r["command"] for r in client.get("/api/history", params={"failed": True}).json()["commands"]] == ["pytest -x"]
    assert client.get("/api/history", params={"q": "stat"}).json()["commands"][0]["exit_code"] == 0

    # search across terminals reads saved output
    server.manager.scrollback.setdefault(sid, Scrollback()).add("hello\r\nerror: boom at src/app.py:3\r\n")
    res = client.get("/api/search", params={"q": "BOOM"}).json()["results"]
    assert res[0]["session_id"] == sid and "src/app.py:3" in res[0]["hits"][0]["text"]

    # clickable paths: only existing files, relative to the terminal's folder
    files = client.post("/api/fs/check", json={"session_id": sid, "paths": ["src/app.py", "nope.py", "../x"]}).json()["files"]
    assert files == ["src/app.py"]
    opened = []
    monkeypatch.setattr(server.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(server.subprocess, "Popen", lambda argv, **kw: opened.append(argv[-1]))
    assert client.post("/api/open", json={"session_id": sid, "path": "src/app.py", "line": 3}).status_code == 200
    assert opened[-1].startswith("vscode://file/") and opened[-1].endswith("src/app.py:3")
    client.put("/api/settings", json={"editor": "system"})
    assert client.post("/api/open", json={"session_id": sid, "path": "evil.bat"}).json()["error"] == "refusing_to_run_executable"
    assert client.put("/api/settings", json={"editor": "vim"}).status_code == 400
    client.delete(f"/api/sessions/{sid}")


def test_shell_integration_argv(monkeypatch):
    monkeypatch.delenv("PROMPT", raising=False)
    argv, env = shells.with_integration(["C:/x/pwsh.exe", "-NoLogo"])
    assert argv[:4] == ["C:/x/pwsh.exe", "-NoLogo", "-NoExit", "-EncodedCommand"] and env == {}
    argv, env = shells.with_integration(["/bin/bash", "--login", "-i"])
    assert argv[1] == "--rcfile" and argv[2].endswith("integration/bash.sh") and env == {"SHELLDECK_LOGIN": "1"}
    argv, env = shells.with_integration(["cmd.exe"])
    assert "133;A" in env["PROMPT"] and env["PROMPT"].count("$P$G") == 1
    assert shells.with_integration(["wsl.exe", "-d", "Ubuntu"]) == (["wsl.exe", "-d", "Ubuntu"], {})


@pytest.mark.skipif(not shells.WINDOWS, reason="WSL is Windows-only")
def test_wsl_argv():
    argv = shells.interactive_argv("wsl", "Ubuntu", r"C:\src")
    assert argv[1:] == ["-d", "Ubuntu", "--cd", r"C:\src"]
    cmd = shells.command_argv("wsl", "ls", None, r"C:\src")
    assert cmd[-4:] == ["--", "sh", "-lc", "ls"]
    assert shells.command_argv("cmd", "dir")[-2:] == ["/c", "dir"]


def test_rejects_foreign_origin_and_host(client):
    assert client.get("/api/projects", headers={"origin": "http://evil.com"}).status_code == 403
    assert client.get("/api/projects", headers={"host": "evil.com"}).status_code == 403
    assert client.get("/api/projects", headers=ORIGIN).status_code == 200


def test_ws_rejects_unknown_session_and_foreign_origin(client, tmp_path):
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/nope"):
        pass
    sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/ws/{sid}", headers={"origin": "http://evil.com"}
    ):
        pass


def test_settings_validation(client):
    assert client.get("/api/settings").json()["default_shell"] == shells.default_kind()
    assert client.put("/api/settings", json={"default_shell": "nope"}).status_code == 400
    assert client.put("/api/settings", json={"font_size": "99"}).status_code == 400
    kind = shells.kinds()[-1]
    r = client.put("/api/settings", json={"default_shell": kind, "font_size": "15"})
    assert r.json()["default_shell"] == kind and r.json()["font_size"] == "15"
    assert client.put("/api/settings", json={"terminal_theme": "nope"}).status_code == 400
    assert client.put("/api/settings", json={"font_family": "x;}</style>"}).status_code == 400
    r = client.put("/api/settings", json={"terminal_theme": "nord", "font_family": "JetBrains Mono"}).json()
    assert r["terminal_theme"] == "nord" and r["font_family"] == "JetBrains Mono"


def test_projects_and_fs(client, tmp_path):
    (tmp_path / "app").mkdir()
    assert client.post("/api/projects", json={"path": str(tmp_path / "missing")}).status_code == 400
    p = client.post("/api/projects", json={"path": str(tmp_path / "app")}).json()
    assert client.patch(f"/api/projects/{p['id']}", json={"name": "App"}).json()["name"] == "App"
    assert "app" in client.get("/api/fs/dirs", params={"path": str(tmp_path)}).json()["dirs"]
    projects = client.get("/api/projects").json()["projects"]
    assert [x["name"] for x in projects] == ["App"]
    assert projects[0]["has_git"] is False
    assert client.delete(f"/api/projects/{p['id']}").json() == {"status": "ok"}


def test_project_git_log_and_checkout(client, tmp_path):
    import os
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "first"], ["branch", "feature"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    p = client.post("/api/projects", json={"path": str(repo)}).json()
    assert client.get("/api/projects").json()["projects"][0]["has_git"] is True

    log = client.get(f"/api/projects/{p['id']}/git/log").json()
    assert log["current_branch"] == "main"
    assert log["commits"][0]["subject"] == "first"

    assert client.post(f"/api/projects/{p['id']}/git/checkout", json={"ref": "feature"}).json()["status"] == "ok"
    assert client.get(f"/api/projects/{p['id']}/git/log").json()["current_branch"] == "feature"
    assert client.post(f"/api/projects/{p['id']}/git/checkout", json={"ref": "no-such"}).status_code == 400
    assert client.post(f"/api/projects/{p['id']}/git/checkout", json={"ref": "--orphan=x"}).status_code == 400

    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty)], check=True, capture_output=True)
    e = client.post("/api/projects", json={"path": str(empty)}).json()
    assert client.get(f"/api/projects/{e['id']}/git/log").json()["commits"] == []


def test_bookmark_crud(client):
    b = client.post("/api/bookmarks", json={"name": "st", "command": "git status"}).json()
    assert client.put(f"/api/bookmarks/{b['id']}", json={"name": "st", "command": "git st"}).json()["command"] == "git st"
    assert client.post("/api/bookmarks", json={"name": ""}).status_code == 400
    assert client.delete(f"/api/bookmarks/{b['id']}").status_code == 200
    assert client.get("/api/bookmarks").json()["bookmarks"] == []


def test_static_files_revalidate(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert client.get("/static/app.js", headers={"if-none-match": r.headers["etag"]}).status_code == 304


def test_task_board(client):
    t = client.post("/api/tasks", json={"title": "ship", "reminder_at": "2020-01-01T00:00:00Z"}).json()
    assert t["column"] == "backlog"
    assert client.post("/api/tasks", json={"title": " "}).status_code == 400
    assert client.post(f"/api/tasks/{t['id']}/move", json={"column": "doing"}).json()["column"] == "doing"
    assert client.put(f"/api/tasks/{t['id']}", json={"priority": "high"}).json()["priority"] == "high"
    assert [a["id"] for a in client.get("/api/tasks/alarms").json()["alarms"]] == [t["id"]]  # reminder is due
    assert client.post(f"/api/tasks/{t['id']}/ack").json()["reminder_acknowledged"] is True
    assert client.get("/api/tasks/alarms").json()["alarms"] == []
    assert client.delete(f"/api/tasks/{t['id']}").json() == {"status": "ok"}
    assert client.get(f"/api/tasks/{t['id']}").status_code == 404


def test_schedule_jobs(client, tmp_path):
    p = client.post("/api/projects", json={"path": str(tmp_path)}).json()
    job = {"name": "hi", "project_id": p["id"], "command": "echo hi", "cron": "0 3 * * *"}
    assert client.post("/api/schedule/jobs", json={**job, "cron": "nope"}).status_code == 400
    assert client.post("/api/schedule/jobs", json={**job, "project_id": "x"}).status_code == 404
    j = client.post("/api/schedule/jobs", json=job).json()
    assert j["enabled"] is True and j["next_run_on"]
    assert client.post(f"/api/schedule/jobs/{j['id']}/toggle").json()["enabled"] is False
    assert client.put(f"/api/schedule/jobs/{j['id']}", json={"cron": "*/5 * * * *"}).json()["cron"] == "*/5 * * * *"
    assert client.get("/api/schedule/jobs/nope/runs").status_code == 404
    assert client.delete(f"/api/schedule/jobs/{j['id']}").json() == {"status": "ok"}
    assert client.get("/api/schedule/jobs").json()["jobs"] == []


def test_runner_runs_in_project_folder(client, tmp_path):
    from shelldeck import runner

    p = client.post("/api/projects", json={"path": str(tmp_path)}).json()
    r = runner.run_command(p["id"], "echo shelldeck-ok", timeout=60)
    assert r["status"] == "success" and "shelldeck-ok" in r["output"]


def test_auth_setup_login_change_reset(browser, monkeypatch):
    b = browser
    # nothing works until a password exists; setup rules
    assert b.get("/api/projects").json() == {"error": "setup_required"}
    assert b.get("/api/auth/status").json()["setup_code_required"] is False  # the test client counts as local
    assert b.post("/api/auth/setup", json={"password": "short", "confirm": "short"}).json()["error"] == "password_too_short"
    assert b.post("/api/auth/setup", json={"password": "longenough", "confirm": "different"}).json()["error"] == "passwords_do_not_match"
    r = b.post("/api/auth/setup", json={"password": "longenough", "confirm": "longenough"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert b.get("/api/projects").status_code == 200
    assert b.post("/api/auth/setup", json={"password": "x" * 9, "confirm": "x" * 9}).status_code == 409

    # logout locks this browser; wrong password is refused; right one logs in
    b.post("/api/auth/logout")
    assert b.get("/api/projects").json() == {"error": "locked"}
    assert b.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert b.post("/api/auth/login", json={"password": "longenough"}).status_code == 200

    # change needs the current password + matching confirm, and signs out other browsers
    other = TestClient(server.app)
    other.post("/api/auth/login", json={"password": "longenough"})
    assert other.get("/api/projects").status_code == 200
    assert b.put("/api/auth/password", json={"current": "bad", "password": "newpassword", "confirm": "newpassword"}).status_code == 403
    assert b.put("/api/auth/password", json={"current": "longenough", "password": "newpassword", "confirm": "typo"}).json()["error"] == "passwords_do_not_match"
    assert b.put("/api/auth/password", json={"current": "longenough", "password": "newpassword", "confirm": "newpassword"}).status_code == 200
    assert b.get("/api/projects").status_code == 200
    auth._cache.clear()
    assert other.get("/api/projects").status_code == 401

    # the CLI token is not a browser login: it can't change the password
    assert b.put("/api/auth/password", headers={"cookie": "", "X-Shelldeck-Token": auth.read_cli_token()},
                 json={"current": "newpassword", "password": "x" * 9, "confirm": "x" * 9}).status_code == 401

    # emergency reset on the host: password and logins gone, remote setup needs the new code
    code = auth.reset()
    assert b.get("/api/projects").json() == {"error": "setup_required"}
    monkeypatch.setattr(server, "_is_local", lambda conn: False)
    assert b.post("/api/auth/setup", json={"password": "x" * 9, "confirm": "x" * 9, "code": "wrong"}).status_code == 403
    assert b.post("/api/auth/setup", json={"password": "x" * 9, "confirm": "x" * 9, "code": code}).status_code == 200


def test_login_rate_limit_and_link(browser, monkeypatch):
    b = browser
    b.post("/api/auth/setup", json={"password": "longenough", "confirm": "longenough"})
    b.post("/api/auth/logout")
    codes = [b.post("/api/auth/login", json={"password": f"bad{i}"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and 429 in codes[5:]
    # host CLI login link: one use, then expired; browsers can't mint one
    assert b.post("/api/auth/login-link").status_code == 403
    path = b.post("/api/auth/login-link", headers={"X-Shelldeck-Token": auth.read_cli_token()}).json()["path"]
    r = b.get(path, follow_redirects=False)
    assert r.status_code == 303 and auth.COOKIE in r.headers["set-cookie"]
    assert b.get(path, follow_redirects=False).status_code == 403


def test_websocket_needs_login(browser, tmp_path):
    b = browser
    b.post("/api/auth/setup", json={"password": "longenough", "confirm": "longenough"})
    sid = b.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
    with b.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    b.post("/api/auth/logout")
    with pytest.raises(WebSocketDisconnect), b.websocket_connect(f"/ws/{sid}"):
        pass


def test_terminal_roundtrip_and_replay(client, tmp_path):
    shell = "cmd" if shells.WINDOWS else "sh"
    sid = client.post("/api/sessions", json={"cwd": str(tmp_path), "shell": shell}).json()["id"]

    def read_until(ws, needle):
        buf = ""
        deadline = time.time() + 15
        while needle not in buf and time.time() < deadline:
            msg = ws.receive_json()
            buf += msg.get("data", "")
        return buf

    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "resize", "rows": 24, "cols": 80})
        ws.send_json({"type": "input", "data": "echo shelldeck-ok\r\n"})
        assert "shelldeck-ok" in read_until(ws, "shelldeck-ok\r\n")
        if shells.WINDOWS:  # cmd reports its directory through shell integration
            (tmp_path / "sub").mkdir()
            ws.send_json({"type": "input", "data": "cd sub\r\n"})
            read_until(ws, f"Cwd={tmp_path / 'sub'}")
            assert db.get_session(sid)["cwd"] == str(tmp_path / "sub")
    # reconnect: scrollback replays earlier output
    with client.websocket_connect(f"/ws/{sid}") as ws:
        assert "shelldeck-ok" in ws.receive_json()["data"]
    client.delete(f"/api/sessions/{sid}")


def test_sessions_get_default_names(client, tmp_path):
    p = client.post("/api/projects", json={"path": str(tmp_path)}).json()
    a, b = shells.kinds()[:2]
    names = [client.post("/api/sessions", json={"project_id": p["id"], "shell": k}).json()["name"] for k in (a, a, b)]
    assert names == [f"{shells.label(a)} 1", f"{shells.label(a)} 2", f"{shells.label(b)} 1"]
    assert client.post("/api/sessions", json={"project_id": p["id"], "shell": "nope"}).status_code == 400


def test_cli_folder_shorthand(monkeypatch):
    from shelldeck import cli

    seen = []
    monkeypatch.setattr(cli, "app", lambda: seen.append(list(cli.sys.argv)))
    for argv, want in (
        (["shelldeck", "C:/src"], ["shelldeck", "open", "C:/src"]),
        (["shelldeck", "-p", "5500", "."], ["shelldeck", "-p", "5500", "open", "."]),
        (["shelldeck", "list"], ["shelldeck", "list"]),
        (["shelldeck", "login-link"], ["shelldeck", "login-link"]),
        (["shelldeck"], ["shelldeck"]),
    ):
        monkeypatch.setattr(cli.sys, "argv", list(argv))
        cli._main()
        assert seen.pop() == want


def test_projects_get_distinct_colors(client, tmp_path):
    colors = []
    for i in range(3):
        (tmp_path / f"p{i}").mkdir()
        colors.append(client.post("/api/projects", json={"path": str(tmp_path / f"p{i}")}).json()["color"])
    assert colors == [0, 1, 2]
    assert client.put("/api/settings", json={"layout_mode": "free"}).json()["layout_mode"] == "free"
    assert client.put("/api/settings", json={"layout_mode": "grid"}).status_code == 400


def _share_setup(b, password="long enough pass"):
    b.post("/api/auth/setup", json={"password": password, "confirm": password})
    cli = {"X-Shelldeck-Token": auth.read_cli_token()}
    tunnel = {"host": "abc-def.trycloudflare.com", "cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.9"}
    return cli, tunnel


def _open_link(b, path, tunnel):
    r = b.get(path, headers=tunnel)
    return r, {**tunnel, "cookie": r.headers["set-cookie"].split(";")[0]}


def _allow(b, cli, allow=True):
    [p] = b.get("/api/share", headers=cli).json()["pending"]
    return b.post("/api/share/decide", json={"id": p["id"], "allow": allow}, headers=cli)


def test_share_gate_needs_link_approval_then_password(browser):
    b = browser
    cli, tunnel = _share_setup(b)
    assert b.get("/", headers=tunnel).status_code == 403  # not shared
    assert b.post("/api/share", json={"host": tunnel["host"]}).status_code == 403  # browsers can't open it
    assert b.get("/api/share").status_code == 403
    assert b.post("/api/share", json={"host": "bad host"}, headers=cli).status_code == 400
    path = b.post("/api/share", json={"host": tunnel["host"]}, headers=cli).json()["path"]

    # the bare tunnel URL and a wrong token get nothing; the link waits for the host
    assert b.get("/", headers=tunnel).status_code == 403
    assert b.get("/share/wrong", headers=tunnel).status_code == 403
    r, gate = _open_link(b, path, tunnel)
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower() and "Waiting" in r.text
    assert b.get("/", headers=gate).status_code == 403  # pending
    assert b.get("/share/status", headers=gate).json() == {"state": "pending"}
    assert b.get(path, headers=tunnel).status_code == 403  # the link worked once
    assert _allow(b, cli).json() == {"state": "ok"}
    assert b.get("/share/status", headers=gate).json() == {"state": "ok"}
    assert b.get("/", headers=gate).status_code == 200

    # the password is still required, and tunnel visitors are remote
    assert b.get("/api/projects", headers=gate).json() == {"error": "locked"}
    assert b.post("/api/auth/login-link", headers=gate).status_code == 403
    r = b.post("/api/auth/login", json={"password": "long enough pass"}, headers={**gate, "origin": "https://" + tunnel["host"]})
    assert r.status_code == 200
    login = r.headers["set-cookie"].split(";")[0]
    authed = {**tunnel, "cookie": f"{gate['cookie']}; {login}"}
    assert b.get("/api/projects", headers=authed).status_code == 200
    vias = {d["via"] for d in b.get("/api/devices", headers=authed).json()["devices"]}
    assert vias == {"local", "share:" + tunnel["host"]}

    # stopping closes the gate and signs out tunnel logins
    assert b.delete("/api/share", headers=cli).status_code == 200
    assert b.get("/", headers=authed).status_code == 403
    auth._cache.clear()
    assert b.get("/api/projects", headers={"cookie": login}).status_code == 401


def test_share_denied_expired_and_new_link(browser, monkeypatch):
    b = browser
    cli, tunnel = _share_setup(b)
    path = b.post("/api/share", json={"host": tunnel["host"]}, headers=cli).json()["path"]
    _, gate = _open_link(b, path, tunnel)
    assert _allow(b, cli, allow=False).json() == {"state": "denied"}
    assert b.get("/", headers=gate).status_code == 403
    assert b.get("/share/status", headers=gate).json() == {"state": "denied"}

    # a new link replaces the old one, and expires unopened
    old = b.post("/api/share/link", headers=cli).json()["path"]
    new = b.post("/api/share/link", headers=cli).json()["path"]
    assert b.get(old, headers=tunnel).status_code == 403
    now = time.time()
    monkeypatch.setattr(server.time, "time", lambda: now + server.LINK_TTL + 1)
    b.get("/api/share", headers=cli)  # heartbeat keeps the share itself alive
    assert b.get(new, headers=tunnel).status_code == 403


def test_share_lease_expires_without_heartbeat(browser, monkeypatch):
    b = browser
    cli, tunnel = _share_setup(b)
    path = b.post("/api/share", json={"host": tunnel["host"]}, headers=cli).json()["path"]
    _, gate = _open_link(b, path, tunnel)
    _allow(b, cli)
    assert b.get("/", headers=gate).status_code == 200
    now = time.time()
    monkeypatch.setattr(server.time, "time", lambda: now + server.SHARE_LEASE + 1)
    assert b.get("/", headers=gate).status_code == 403  # `sd share` died: the tunnel host is refused
    assert b.get("/api/devices").json()["share"] is None


def test_share_revoked_device_loses_its_grant(browser):
    b = browser
    cli, tunnel = _share_setup(b)
    path = b.post("/api/share", json={"host": tunnel["host"]}, headers=cli).json()["path"]
    _, gate = _open_link(b, path, tunnel)
    _allow(b, cli)
    phone = TestClient(server.app)
    r = phone.post("/api/auth/login", json={"password": "long enough pass"}, headers={**gate, "origin": "https://" + tunnel["host"]})
    h = auth.session_hash(r.cookies[auth.COOKIE])
    assert b.post("/api/auth/login", json={"password": "long enough pass"}).status_code == 200  # host takes over
    assert b.delete(f"/api/devices/{h}").json() == {"revoked": 1}
    assert b.get("/", headers=gate).status_code == 403


def test_share_needs_a_long_password(browser):
    cli, tunnel = _share_setup(browser, password="short123")
    assert browser.get("/api/share", headers=cli).json()["strong_password"] is False
    assert browser.post("/api/share", json={"host": tunnel["host"]}, headers=cli).json() == {"error": "weak_password"}


def test_security_headers(browser):
    r = browser.get("/api/auth/status")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in browser.get("/").headers["content-security-policy"]


def test_devices_one_active_takeover_and_revoke(browser, tmp_path):
    a = browser
    a.post("/api/auth/setup", json={"password": "longenough", "confirm": "longenough"})
    sid = a.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
    c = TestClient(server.app)
    assert c.post("/api/auth/login", json={"password": "longenough"}).status_code == 200

    # c logged in last, so c is in use and a is idle (API 423, sockets closed with 4423)
    assert c.get("/api/projects").status_code == 200
    assert a.get("/api/projects").status_code == 423
    assert a.get("/api/auth/status").json()["in_use_elsewhere"] is True
    with pytest.raises(WebSocketDisconnect) as ex, a.websocket_connect(f"/ws/{sid}"):
        pass
    assert ex.value.code == server.IN_USE
    devs = c.get("/api/devices").json()["devices"]
    assert len(devs) == 2 and [d["current"] for d in devs if d["active"]] == [True]

    # a takes over with the password; its old login is replaced, not duplicated
    assert a.post("/api/auth/login", json={"password": "longenough"}).status_code == 200
    assert a.get("/api/projects").status_code == 200 and c.get("/api/projects").status_code == 423
    assert len(a.get("/api/devices").json()["devices"]) == 2

    # a revokes c; c is signed out
    other = next(d["id"] for d in a.get("/api/devices").json()["devices"] if not d["current"])
    assert a.delete(f"/api/devices/{other}").json() == {"revoked": 1}
    assert c.get("/api/projects").status_code == 401
    assert [d["current"] for d in a.get("/api/devices").json()["devices"]] == [True]

    # "others" keeps this browser; once the active one logs out, an idle one may act again
    c.post("/api/auth/login", json={"password": "longenough"})
    assert c.delete("/api/devices/others").json() == {"revoked": 1}
    assert a.get("/api/projects").status_code == 401
    d = TestClient(server.app)
    d.post("/api/auth/login", json={"password": "longenough"})
    d.post("/api/auth/logout")
    assert c.get("/api/projects").status_code == 200
