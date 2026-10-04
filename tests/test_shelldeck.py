import asyncio
import sys
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from shelldeck import auth, db, server, share, shells
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


def test_agents(client, tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    from shelldeck import agents, stats

    # a node/python-installed agent is found by its package path, with the --model it was given
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "@anthropic-ai/claude-code", "--model", "opus"])
    try:
        time.sleep(0.5)
        assert agents.running({"me": os.getpid()}) == {"me": ("claude", "opus")}
        assert stats.collect({"me": os.getpid()})["sessions"]["me"]["agent"] == {"key": "claude", "label": "Claude Code", "model": "opus", "context": None}
    finally:
        child.kill()
    data = client.get("/api/agents").json()
    assert data["running"] == [] and {"claude", "codex", "devin"} <= {a["key"] for a in data["agents"]}
    assert isinstance(data["outside"], list) and isinstance(data["devin_sessions"], list)
    integrations = {item["agent"]: item for item in client.get("/api/integrations").json()["integrations"]}
    assert integrations["opencode"]["lifecycle"] is True
    assert integrations["amp"]["session_restore"] is False

    # Devin's session store: newest first, hidden ones skipped
    import sqlite3

    store = tmp_path / "devin" / "cli" / "sessions.db"
    store.parent.mkdir(parents=True)
    with sqlite3.connect(store) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT, working_directory TEXT, backend_type TEXT, model TEXT, "
                     "created_at INT, last_activity_at INT, title TEXT, hidden INT DEFAULT 0)")
        conn.executemany("INSERT INTO sessions VALUES (?, ?, 'Windsurf', 'swe-1-6', 1, ?, ?, ?)",
                         [("old-one", "D:/a", 5, "Old", 0), ("new-one", "D:/b", 9, "New", 0), ("gone", "D:/c", 7, "Hidden", 1)])
    conn.close()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert [(d["id"], d["cwd"]) for d in agents.devin_sessions()] == [("new-one", "D:/b"), ("old-one", "D:/a")]

    # peers read each other's screen and type into each other
    sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
    server.manager.scrollback.setdefault(sid, Scrollback()).add("\x1b[32mworking on auth\x1b[0m\r\ndone\r\n\r\n")
    assert client.get(f"/api/sessions/{sid}/screen", params={"lines": 1}).json()["text"] == "done"
    assert client.get("/api/sessions/nope/screen").status_code == 404
    assert client.post(f"/api/sessions/{sid}/input", json={"text": "hi"}).json()["error"] == "not_running"
    assert client.post(f"/api/sessions/{sid}/input", json={}).json()["error"] == "text_required"


def test_team(client, tmp_path, monkeypatch):
    """Terminal nicks, sd spawn (without really starting a shell), hand-off files, done and orphaned hand-offs."""
    from shelldeck import team

    team._selfcheck()  # tiers, model picks, spawn line, skill install
    started = []

    async def fake_start(sid, line):
        started.append(line)

    monkeypatch.setattr(server, "_attach", lambda *a: None)
    monkeypatch.setattr(server, "_start_agent", fake_start)
    a = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()
    b = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()
    assert a["nick"] and b["nick"] and a["nick"] != b["nick"]
    assert client.post("/api/spawn", json={"parent": a["id"]}).json()["error"] == "task_required"
    assert client.post("/api/spawn", json={"parent": a["id"], "task": "x", "agent": "aider"}).json()["error"] == "cannot_spawn:aider"
    r = client.post("/api/spawn", json={"parent": a["id"], "task": "fix a typo in the readme", "agent": "claude", "model": "large"}).json()
    kid, h = r["session"], r["handoff"]
    assert kid["parent"] == a["id"] and r["model"] == "opus" and started == [r["command"]]
    assert r["command"].startswith(f'claude --model opus "You are {kid["nick"]}, a shelldeck sub-agent working for {a["nick"]}.')
    task_file = tmp_path / ".shelldeck" / "handoffs" / f"{h['id']}.md"
    assert "fix a typo in the readme" in task_file.read_text(encoding="utf-8")
    assert (tmp_path / ".shelldeck" / ".gitignore").read_text() == "*\n"
    # sub-agents can't spawn
    assert client.post("/api/spawn", json={"parent": kid["id"], "task": "more"}).status_code == 403
    # a hand-off needs an agent running in the target (typed text would run in a bare shell)
    assert client.post("/api/handoffs", json={"to": b["id"], "task": "x"}).status_code == 409
    done = client.post(f"/api/handoffs/{h['id']}/done", json={"result": "fixed it"}).json()
    assert done["status"] == "done" and "fixed it" in task_file.read_text(encoding="utf-8")
    assert client.post(f"/api/handoffs/{h['id']}/done", json={}).status_code == 409
    assert "| done |" in (tmp_path / ".shelldeck" / "handoff.md").read_text(encoding="utf-8")
    h2 = client.post("/api/spawn", json={"parent": a["id"], "task": "another"}).json()["handoff"]
    assert [x["id"] for x in server._orphan(h2["to_sid"])] == [h2["id"]]
    assert db.get_handoff(h2["id"])["status"] == "exited"

    # a sub-agent's question goes to its parent (here a plain shell, so it's broadcast to the user), once
    import asyncio

    sent = []

    async def fake_broadcast(msg):
        sent.append(msg)
        return 0

    monkeypatch.setattr(server, "_broadcast", fake_broadcast)
    monkeypatch.setattr(server, "QUIET_S", 1e9)  # keep the app's own watcher off these
    server.ask_buf[kid["id"]] = "\x1b[2J\x1b[5;1HRead 1 file\r\n\x1b[20;1H Do you want to proceed?\r\n \x1b[1m1. Yes\x1b[0m  2. No"
    server.ask_buf[b["id"]] = "just some output about approvals"
    assert asyncio.run(server._check_questions(quiet=0)) == [kid["id"]]
    assert sent[0]["type"] == "question" and sent[0]["nick"] == kid["nick"]
    assert asyncio.run(server._check_questions(quiet=0)) == []  # nothing new drawn since
    assert "Do you want to proceed? 1. Yes 2. No" in sent[0]["text"]
    server.ask_buf[kid["id"]] = "\x1b[20;1H Do you want to proceed?\r\n 1. Yes  2. No"
    assert asyncio.run(server._check_questions(quiet=0)) == []  # the same prompt redrawn
    server.ask_buf.clear()
    assert [x["id"] for x in client.get("/api/handoffs", params={"project_id": a["project_id"]}).json()["handoffs"]] == [h2["id"], h["id"]]


def test_file_editor_and_scratch(client, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_bytes(b"one\r\ntwo\r\n")
    r = client.get("/api/fs/file", params={"path": str(f)}).json()
    assert r["text"] == "one\ntwo\n" and r["crlf"] and not r["readonly"]
    # saving keeps CRLF, and refuses when the file changed on disk since it was read
    assert client.put("/api/fs/file", json={"path": str(f), "text": "uno\n", "mtime": r["mtime"], "crlf": True}).status_code == 200
    assert f.read_bytes() == b"uno\r\n"
    assert client.put("/api/fs/file", json={"path": str(f), "text": "stale", "mtime": r["mtime"]}).status_code == 409
    new = client.get("/api/fs/file", params={"path": str(tmp_path / "new.md")}).json()
    assert new["new"] and new["text"] == ""
    (tmp_path / "bin.dat").write_bytes(b"\0\1\2")
    assert client.get("/api/fs/file", params={"path": str(tmp_path / "bin.dat")}).json()["error"] == "binary_file"
    (tmp_path / "x.svg").write_text("<svg onload=alert(1)>")
    assert client.get("/api/fs/raw", params={"path": str(tmp_path / "x.svg")}).status_code == 415
    assert client.post("/api/fs/show", json={"path": str(f)}).json() == {"delivered": 0}

    n = client.post("/api/scratch", json={"body": "# Ideas"}).json()
    assert client.put(f"/api/scratch/{n['id']}", json={"body": "# Ideas\nmore"}).status_code == 200
    assert client.get("/api/scratch").json()["notes"][0]["body"] == "# Ideas\nmore"
    client.delete(f"/api/scratch/{n['id']}")
    assert client.get("/api/scratch").json()["notes"] == []
    assert client.put("/api/scratch/nope", json={"body": ""}).status_code == 404


def test_agent_context(tmp_path, monkeypatch):
    """Context window use read from Claude Code's and Codex's own logs (this process plays the agent)."""
    import json
    import os
    from pathlib import Path

    from shelldeck import agents

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agents._files.clear()
    pid = os.getpid()

    claude = tmp_path / ".claude"
    (claude / "sessions").mkdir(parents=True)
    (claude / "projects" / "D--x").mkdir(parents=True)
    (claude / "sessions" / f"{pid}.json").write_text(json.dumps({"sessionId": "s1", "status": "idle"}))
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 49000, "output_tokens": 990}
    lines = [
        {"type": "assistant", "message": {"model": "claude-sonnet-4-6", "usage": usage}},
        {"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-4-5", "usage": {"input_tokens": 5}}},
        {"type": "user", "message": {"content": "hi"}},
    ]
    (claude / "projects" / "D--x" / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    assert agents.context(pid, "claude") == {"used": 51000, "window": 200_000, "estimated": True, "state": "idle", "model": "claude-sonnet-4-6"}
    assert agents.CLAUDE_1M.search("claude-opus-5-5") and not agents.CLAUDE_1M.search("claude-opus-4-6")

    rollout = tmp_path / ".codex" / "sessions" / "2026" / "09" / "30" / "rollout-x.jsonl"
    rollout.parent.mkdir(parents=True)
    events = [
        {"type": "session_meta", "payload": {"cwd": os.getcwd()}},
        {"type": "event_msg", "payload": {"type": "task_started", "model_context_window": 258400}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"total_tokens": 64600}}}},
    ]
    rollout.write_text("\n".join(json.dumps(x) for x in events))
    assert agents.context(pid, "codex") == {"used": 64600, "window": 258400}
    assert agents.context(pid, "aider") is None


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


def test_password_survives_reinstall(tmp_path, monkeypatch):
    """Set once: the password file outlives a lost database row, and 0.0.4's database copy is migrated."""
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    db.init_db()
    auth.set_password("longenough")
    db.delete_setting(auth._PASSWORD_KEY)  # the row an unclean shutdown lost
    assert auth.init_auth() is None and auth.check_password("longenough")
    (tmp_path / "password").unlink()
    db.set_setting(auth._PASSWORD_KEY, auth._hash_password("fromolder1"))  # only an old version's copy
    auth.init_auth()
    assert auth.check_password("fromolder1")
    auth.reset()
    assert not auth.has_password() and not (tmp_path / "password").exists()


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


FAKE_CLOUDFLARED = """import sys, time
sys.stderr.write("INF |  https://abc-def.trycloudflare.com  |\\n")
sys.stderr.write("INF Registered tunnel connection connIndex=0\\n")
sys.stderr.flush()
time.sleep(0 if "--exit" in sys.argv else 60)
"""


@pytest.fixture
def tunnel_stub(tmp_path, monkeypatch):
    """cloudflared stand-in: prints the URL and the ready line, then idles (or exits with --exit)."""
    stub = tmp_path / "cloudflared.py"
    stub.write_text(FAKE_CLOUDFLARED)
    argv = [sys.executable, str(stub)]
    monkeypatch.setattr(share, "command", lambda: argv)
    return argv


def _share_setup(b, password="long enough pass"):
    b.post("/api/auth/setup", json={"password": password, "confirm": password})
    cli = {"X-Shelldeck-Token": auth.read_cli_token()}
    auth.accept_terms(share.TERMS_VERSION)
    tunnel = {"host": "abc-def.trycloudflare.com", "cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.9"}
    return cli, tunnel


def _start(b, cli):
    state = b.post("/api/share", headers=cli).json()
    assert state["sharing"] and state["url"] == "https://abc-def.trycloudflare.com"
    return state["link"]["path"]


def _open_link(b, path, tunnel):
    r = b.get(path, headers=tunnel)
    return r, {**tunnel, "cookie": r.headers["set-cookie"].split(";")[0]}


def _allow(b, cli, allow=True):
    [p] = b.get("/api/share", headers=cli).json()["pending"]
    return b.post("/api/share/decide", json={"id": p["id"], "allow": allow}, headers=cli)


def _phone_login(b, gate, tunnel, ios=False):
    headers = {**gate, "origin": "https://" + tunnel["host"], **({"X-Shelldeck-Client": "ios"} if ios else {})}
    r = b.post("/api/auth/login", json={"password": "long enough pass"}, headers=headers)
    assert r.status_code == 200
    return {**tunnel, "cookie": f"{gate['cookie']}; {r.headers['set-cookie'].split(';')[0]}"}


def test_share_gate_needs_link_approval_then_password(browser, tunnel_stub):
    b = browser
    cli, tunnel = _share_setup(b)
    assert b.get("/", headers=tunnel).status_code == 403  # not shared
    state = b.get("/api/share", headers=cli).json()
    assert state["sharing"] is False and state["link"] is None and state["cloudflared"] is True
    path = _start(b, cli)
    link = b.get("/api/share", headers=cli).json()["link"]
    assert link["qr_svg"].startswith("<svg") and link["url"].endswith(path) and link["expires_at"] > time.time()
    assert b.post("/api/share", headers=cli).json()["link"]["path"] == path  # already running: unchanged

    # the bare tunnel URL and a wrong token get nothing; the link waits for the host
    assert b.get("/", headers=tunnel).status_code == 403
    assert b.get("/share/wrong", headers=tunnel).status_code == 403
    r, gate = _open_link(b, path, tunnel)
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower() and "Waiting" in r.text
    assert b.get("/", headers=gate).status_code == 403  # pending
    assert b.get("/share/status", headers=gate).json() == {"state": "pending"}
    assert b.get(path, headers=tunnel).status_code == 403  # the link worked once
    assert b.get("/api/share", headers=cli).json()["link"] is None
    assert _allow(b, cli).json() == {"state": "ok"}
    assert b.get("/share/status", headers=gate).json() == {"state": "ok"}
    assert b.get("/", headers=gate).status_code == 200

    # the password is still required, and tunnel visitors are remote
    assert b.get("/api/projects", headers=gate).json() == {"error": "locked"}
    assert b.post("/api/auth/login-link", headers=gate).status_code == 403
    authed = _phone_login(b, gate, tunnel)
    assert b.get("/api/projects", headers=authed).status_code == 200
    devs = b.get("/api/devices", headers=authed).json()
    assert {d["via"] for d in devs["devices"]} == {"local", "share:" + tunnel["host"]} and devs["pending"] == []
    assert {d["kind"] for d in devs["devices"]} == {"browser"}  # no ios header: a browser login
    # share controls are host-only; deciding is refused on the tunnel too
    for method, url in (("get", "/api/share"), ("post", "/api/share"), ("post", "/api/share/link"), ("delete", "/api/share")):
        assert getattr(b, method)(url, headers=authed).json() == {"error": "host_only"}
    assert b.post("/api/share/decide", json={"id": "x", "allow": True}, headers=authed).json() == {"error": "host_only"}

    # stopping kills cloudflared, closes the gate and signs out tunnel logins
    proc = share._proc
    assert b.delete("/api/share", headers=cli).json() == {"status": "ok"}
    assert proc.poll() is not None and share._proc is None
    assert b.get("/", headers=authed).status_code == 403
    auth._cache.clear()
    assert b.get("/api/projects", headers={"cookie": authed["cookie"].split("; ")[1]}).status_code == 401


def test_share_host_browser_decides_and_gets_share_state(browser, tunnel_stub):
    b = browser
    cli, tunnel = _share_setup(b)
    assert b.post("/api/share").json()["sharing"]  # a local logged-in browser is the host too
    assert b.post("/api/share", headers={"x-forwarded-for": "10.0.0.5"}).json() == {"error": "host_only"}  # proxied/LAN
    path = b.post("/api/share/link").json()["link"]["path"]
    with b.websocket_connect("/ws/alarms") as host_ws:
        assert host_ws.receive_json()["type"] == "alarm_snapshot"
        _, gate = _open_link(b, path, tunnel)
        msg = host_ws.receive_json()
        assert msg["type"] == "share_state" and len(msg["state"]["pending"]) == 1
        pid = msg["state"]["pending"][0]["id"]
        assert b.get("/api/devices").json()["pending"][0]["id"] == pid
        assert b.post("/api/share/decide", json={"id": pid, "allow": True}).json() == {"state": "ok"}
        assert host_ws.receive_json()["state"]["pending"] == []
        assert b.post("/api/share/decide", json={"id": pid, "allow": True}).status_code == 404

        # the phone's alarm socket never gets share_state (it carries the link)
        phone = TestClient(server.app)
        authed = _phone_login(phone, gate, tunnel, ios=True)
        with phone.websocket_connect("/ws/alarms", headers=authed) as phone_ws:
            assert phone_ws.receive_json()["type"] == "alarm_snapshot"
            b.post("/api/share/link")
            assert host_ws.receive_json()["state"]["link"]
            phone_ws.send_text('{"type":"ping"}')
            assert phone_ws.receive_json() == {"type": "pong"}


def test_share_stops_when_cloudflared_exits(browser, tunnel_stub):
    b = browser
    cli, _ = _share_setup(b)
    tunnel_stub.append("--exit")
    b.post("/api/share", headers=cli)
    for _ in range(100):
        if not b.get("/api/share", headers=cli).json()["sharing"]:
            break
        time.sleep(0.05)
    assert b.get("/api/share", headers=cli).json()["sharing"] is False


def test_share_errors(browser, monkeypatch):
    cli, _ = _share_setup(browser, password="short123")
    assert browser.get("/api/share", headers=cli).json()["strong_password"] is False
    assert browser.post("/api/share", headers=cli).json() == {"error": "weak_password"}
    browser.put("/api/auth/password", json={"current": "short123", "password": "long enough pass", "confirm": "long enough pass"})
    monkeypatch.setattr(share, "command", lambda: None)
    r = browser.post("/api/share", headers=cli)
    assert r.status_code == 404 and r.json()["error"] == "no_cloudflared" and r.json()["hint"] == share.HINT
    assert browser.post("/api/share/link", headers=cli).json() == {"error": "not_sharing"}


def test_share_needs_terms(browser, tunnel_stub):
    cli, tunnel = _share_setup(browser)
    (db.config_dir() / "share-consent").unlink()
    state = browser.get("/api/share", headers=cli).json()
    assert state["terms_accepted"] is False and any("own risk" in t for t in state["terms"])
    assert browser.post("/api/share", headers=cli).json() == {"error": "terms_required"}
    assert browser.post("/api/share/terms", json={}, headers=cli).json() == {"error": "accept_required"}
    assert browser.post("/api/share/terms", json={"accept": True}, headers=tunnel).status_code == 403
    assert browser.post("/api/share/terms", json={"accept": True}, headers=cli).json()["terms_accepted"] is True
    assert auth.terms_accepted(share.TERMS_VERSION) and not auth.terms_accepted(share.TERMS_VERSION + 1)  # a new version asks again
    assert browser.post("/api/share", headers=cli).json()["sharing"]
    browser.delete("/api/share", headers=cli)


def test_share_denied_expired_and_new_link(browser, monkeypatch, tunnel_stub):
    b = browser
    cli, tunnel = _share_setup(b)
    path = _start(b, cli)
    _, gate = _open_link(b, path, tunnel)
    assert _allow(b, cli, allow=False).json() == {"state": "denied"}
    assert b.get("/", headers=gate).status_code == 403
    assert b.get("/share/status", headers=gate).json() == {"state": "denied"}

    # a new link replaces the old one, and expires unopened
    old = b.post("/api/share/link", headers=cli).json()["link"]["path"]
    new = b.post("/api/share/link", headers=cli).json()["link"]["path"]
    assert b.get(old, headers=tunnel).status_code == 403
    now = time.time()
    monkeypatch.setattr(server.time, "time", lambda: now + server.LINK_TTL + 1)
    assert b.get("/api/share", headers=cli).json()["link"] is None
    assert b.get(new, headers=tunnel).status_code == 403


def test_remote_login_does_not_take_over(browser, tunnel_stub, tmp_path):
    b = browser
    cli, tunnel = _share_setup(b)
    sid = b.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
    path = _start(b, cli)
    _, gate = _open_link(b, path, tunnel)
    _allow(b, cli)
    phone = TestClient(server.app)
    with b.websocket_connect(f"/ws/{sid}") as host_ws:
        authed = _phone_login(phone, gate, tunnel, ios=True)
        # the host browser stays in use, its socket stays open, and the phone works alongside
        assert b.get("/api/projects").status_code == 200 and phone.get("/api/projects", headers=authed).status_code == 200
        host_ws.send_text('{"type":"ping"}')
        assert host_ws.receive_json() == {"type": "pong"}
        assert b.get("/api/auth/status").json()["in_use_elsewhere"] is False
        assert phone.get("/api/auth/status", headers=authed).json()["in_use_elsewhere"] is False
    assert {d["kind"] for d in b.get("/api/devices").json()["devices"]} == {"browser", "remote"}

    # a host login afterwards doesn't lock the phone out either
    assert b.post("/api/auth/login", json={"password": "long enough pass"}).status_code == 200
    assert phone.get("/api/projects", headers=authed).status_code == 200

    # push tokens: the logged-in phone only; revoking the device drops them
    assert phone.post("/api/push", json={"token": "nope"}, headers=authed).status_code == 400
    assert b.post("/api/push", json={"token": "ExponentPushToken[abc]"}, headers=cli).status_code == 403
    assert phone.post("/api/push", json={"token": "ExponentPushToken[abc]"}, headers=authed).json() == {"status": "ok"}
    assert [t["token"] for t in db.list_push_tokens()] == ["ExponentPushToken[abc]"]
    assert phone.request("DELETE", "/api/push", json={"token": "ExponentPushToken[abc]"}, headers=authed).json() == {"status": "ok"}
    phone.post("/api/push", json={"token": "ExponentPushToken[abc]"}, headers=authed)
    remote = next(d["id"] for d in b.get("/api/devices").json()["devices"] if d["kind"] == "remote")
    assert b.delete(f"/api/devices/{remote}").json() == {"revoked": 1}
    assert db.list_push_tokens() == []
    assert b.get("/", headers=gate).status_code == 403


def test_ios_header_without_grant_is_a_browser_login(browser):
    b = browser
    b.post("/api/auth/setup", json={"password": "longenough", "confirm": "longenough"})
    c = TestClient(server.app)
    assert c.post("/api/auth/login", json={"password": "longenough"}, headers={"X-Shelldeck-Client": "ios"}).status_code == 200
    assert {d["kind"] for d in c.get("/api/devices").json()["devices"]} == {"browser"}
    assert b.get("/api/projects").status_code == 423  # it took over like any browser
    assert c.request("DELETE", "/api/push", json={"token": "ExponentPushToken[x]"}).status_code == 404


def test_agent_state_from_output_bursts(monkeypatch):
    sid, sent, pushed = "s1", [], []
    monkeypatch.setattr(server.agents, "running", lambda shells: {sid: ("claude", None)})
    monkeypatch.setattr(server.db, "get_session", lambda s: {"id": s, "nick": "Ada", "project_id": None, "parent": None})
    monkeypatch.setattr(server.db, "list_push_tokens", lambda: [{"token": "ExponentPushToken[a]", "session_hash": "h"}])
    monkeypatch.setattr(server.auth, "alive", lambda h: True)
    monkeypatch.setattr(server, "_push", lambda tokens, msg: pushed.append(msg))

    async def broadcast(msg, host_only=False):
        sent.append(msg["state"])

    monkeypatch.setattr(server, "_broadcast", broadcast)
    now = time.monotonic()
    server.agent_state.clear()
    server.out_at[sid], server.busy_since[sid] = now - 10, now - 30  # quiet after a long burst that asked
    server.burst[sid] = "\x1b[2Jworking...\r\n Do you want to proceed?\r\n 1. Yes"
    asyncio.run(server._check_agents())
    server.out_at[sid] = server.busy_since[sid] = time.monotonic()  # answered: a new burst, brief so far
    server.burst[sid] = "esc"
    asyncio.run(server._check_agents())
    server.busy_since[sid] = time.monotonic() - 10  # still printing 10s later
    asyncio.run(server._check_agents())
    server.out_at[sid] = time.monotonic() - 10  # quiet, nothing asked
    asyncio.run(server._check_agents())
    assert sent == ["approval", "idle", "working", "idle"]
    for _ in range(50):  # the push runs on a thread
        if pushed:
            break
        time.sleep(0.02)
    assert pushed == [{"title": "Claude Code needs you", "body": "Ada · ", "data": {"session_id": sid}}]
    for d in (server.out_at, server.busy_since, server.burst, server.agent_state):
        d.pop(sid, None)


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


def test_agent_reports_are_terminal_bound_and_authoritative(client, monkeypatch):
    sent = []

    async def broadcast(msg, host_only=False):
        sent.append(msg)

    monkeypatch.setattr(server, "_broadcast", broadcast)

    class Live:
        pid = 1

        def isalive(self):
            return True

        def kill(self):
            pass

    for sid in ("ta", "tb"):
        monkeypatch.setitem(server.manager.procs, sid, Live())
        monkeypatch.setitem(server.report_tokens, sid, f"tok-{sid}")
        monkeypatch.setitem(server.agent_kind, sid, ("opencode", 1))
    body = {"source": "integration:opencode", "agent": "opencode", "state": "blocked", "blocked_reason": "question"}
    assert client.post("/api/agent-reports", json=body).status_code == 403  # no token
    assert client.post("/api/agent-reports", json=body, headers={"X-Shelldeck-Report-Token": "nope"}).status_code == 403
    # terminal A's token can't speak for terminal B
    assert client.post("/api/agent-reports", json={**body, "session_id": "tb"}, headers={"X-Shelldeck-Report-Token": "tok-ta"}).json()["error"] == "session_mismatch"
    assert client.post("/api/agent-reports", json={**body, "agent": "codex"}, headers={"X-Shelldeck-Report-Token": "tok-ta"}).json()["error"] == "agent_mismatch"
    r = client.post("/api/agent-reports", json=body, headers={"X-Shelldeck-Report-Token": "tok-ta"}).json()
    assert r["status"] == {"state": "blocked", "source": "integration", "reason": "question", "detail": None}
    assert sent[-1]["session_id"] == "ta" and sent[-1]["state"] == "approval"  # legacy value kept for old clients
    rows = client.get("/api/agent-status", params={"target": "ta"}).json()["agents"]
    assert rows[0]["state"] == "blocked" and "tb" not in {x["session_id"] for x in rows}
    # waits: reached at once, timeout, and a replaced agent process never satisfies an old wait
    assert client.post("/api/agent-wait", json={"session_id": "ta", "until": ["blocked"]}).json()["result"] == "reached"
    assert client.post("/api/agent-wait", json={"session_id": "ta", "until": ["idle"], "timeout": 0.2}).json()["result"] == "timeout"
    assert client.post("/api/agent-wait", json={"session_id": "zz", "until": ["idle"]}).status_code == 409
    for sid in ("ta", "tb"):
        server.reports.forget(sid)
        server.agent_status.pop(sid, None)


def test_agent_wait_wakes_on_change_and_replacement(monkeypatch):
    monkeypatch.setitem(server.agent_kind, "tw", ("codex", 7))
    monkeypatch.setitem(server.agent_status, "tw", {"state": "working", "source": "heuristic", "reason": None, "detail": None})

    async def go():
        waiter = asyncio.create_task(server.agent_wait({"session_id": "tw", "until": ["idle"], "timeout": 5}))
        await asyncio.sleep(0.05)
        await server._publish("tw", {"state": "idle", "source": "heuristic", "reason": None, "detail": None}, None)
        reached = await waiter
        waiter = asyncio.create_task(server.agent_wait({"session_id": "tw", "until": ["blocked"], "timeout": 5}))
        await asyncio.sleep(0.05)
        server.agent_kind["tw"] = ("codex", 8)  # another agent process took the terminal
        server._changed()
        return reached, await waiter

    async def quiet(msg, host_only=False):
        pass

    monkeypatch.setattr(server, "_broadcast", quiet)
    reached, replaced = asyncio.run(go())
    assert reached["result"] == "reached" and replaced["result"] == "replaced"


def test_report_token_alone_is_enough_locally_but_not_over_a_tunnel(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    monkeypatch.setattr(server, "ALLOWED_HOSTS", {"testserver"})

    class Live:
        pid = 1

        def isalive(self):
            return True

        def kill(self):
            pass

    async def quiet(msg, host_only=False):
        pass

    monkeypatch.setattr(server, "_broadcast", quiet)
    monkeypatch.setitem(server.manager.procs, "tl", Live())
    monkeypatch.setitem(server.report_tokens, "tl", "tok-tl")
    monkeypatch.setitem(server.agent_kind, "tl", ("claude", 3))
    body = {"source": "integration:claude", "agent": "claude", "state": "working"}
    with TestClient(server.app) as c:  # no CLI token, no login
        monkeypatch.setattr(server, "_is_local", lambda conn: True)
        assert c.post("/api/agent-reports", json=body, headers={"X-Shelldeck-Report-Token": "tok-tl"}).json()["status"]["state"] == "working"
        assert c.get("/api/agent-status").status_code == 401  # the token opens nothing else
        monkeypatch.setattr(server, "_is_local", lambda conn: False)
        assert c.post("/api/agent-reports", json=body, headers={"X-Shelldeck-Report-Token": "tok-tl"}).status_code == 401
    server.reports.forget("tl")
    server.agent_status.pop("tl", None)


def test_resume_plan_is_safe(client, tmp_path, monkeypatch):
    work = tmp_path / "w"
    work.mkdir()
    pid = client.post("/api/projects", json={"path": str(work)}).json()["id"]
    sid = client.post("/api/sessions", json={"project_id": pid, "shell": shells.default_kind()}).json()["id"]
    monkeypatch.setattr(server.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    assert server._resume_plan(sid) == (None, "no_resume")
    db.save_agent_session(sid, "claude", "native:claude", "abc-123", ["claude", "--resume", "abc-123"], "working")
    assert server._resume_plan(sid) == ("claude --resume abc-123", None)
    rows = client.get("/api/agent-sessions").json()["sessions"]
    assert rows[0]["can_resume"] and "abc-123" not in str(rows)  # native ids stay on the server
    # a stored value that would need quoting (or could run more) is refused
    db.save_agent_session(sid, "claude", "native:claude", "x", ["claude", "--resume", "x; rm -rf ~"], "working")
    assert server._resume_plan(sid) == (None, "invalid_resume_argv")
    db.save_agent_session(sid, "claude", "native:claude", "x", ["/bin/claude", "x"], "working")
    assert server._resume_plan(sid) == (None, "invalid_resume_argv")
    db.save_agent_session(sid, "claude", "native:claude", "abc", ["claude", "--resume", "abc"], "working")
    monkeypatch.setattr(server.shutil, "which", lambda exe: None)
    assert server._resume_plan(sid) == (None, "executable_not_found")
    monkeypatch.setattr(server.shutil, "which", lambda exe: "/x")
    db.update_session_cwd(sid, str(tmp_path / "gone"))
    assert server._resume_plan(sid) == (None, "cwd_missing")
    db.update_session_cwd(sid, str(work))
    db.add_handoff(project_id=pid, from_sid=None, from_nick=None, to_sid=sid, to_nick="x", agent="claude", model=None, task="t")
    assert server._resume_plan(sid, auto=True) == (None, "open_handoff")  # auto never resumes into a hand-off
    assert server._resume_plan(sid)[0] == "claude --resume abc"  # a person asking may


def test_auto_resume_respects_setting(client, monkeypatch, tmp_path):
    started = []

    async def fake_start(sid, line):
        started.append((sid, line))

    work = tmp_path / "w2"
    work.mkdir()
    pid = client.post("/api/projects", json={"path": str(work)}).json()["id"]
    sid = client.post("/api/sessions", json={"project_id": pid, "shell": shells.default_kind()}).json()["id"]
    db.save_agent_session(sid, "codex", "native:codex", "t1", ["codex", "resume", "t1"], "done")
    monkeypatch.setattr(server.shutil, "which", lambda exe: "/x")
    monkeypatch.setattr(server, "_start_agent", fake_start)

    async def go(mode):
        server.resumed.discard(sid)
        db.set_setting("agent_resume", mode)
        server._auto_resume(sid)
        await asyncio.sleep(0)

    asyncio.run(go("ask"))
    assert started == []
    asyncio.run(go("auto"))
    assert started == [(sid, "codex resume t1")]
    asyncio.run(go("never"))
    assert len(started) == 1
    assert client.put("/api/settings", json={"agent_resume": "bogus"}).status_code == 400
