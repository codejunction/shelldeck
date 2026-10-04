import subprocess

import pytest
from fastapi.testclient import TestClient

from shelldeck import auth, context, server


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path / "home"))
    return tmp_path


def proj(root, name):
    d = root / name
    (d / "src").mkdir(parents=True)
    return context.project_for(str(d))


def test_secrets_are_redacted():
    text = "token=abc123 password: 'hunter2' key AKIAABCDEFGHIJKLMNOP ghp_" + "a" * 30 + " https://u:p@host/x Bearer abcdefghijklmnop"
    out = context.redact(text)
    for secret in ("abc123", "hunter2", "AKIAABCDEFGHIJKLMNOP", "ghp_", "u:p@", "abcdefghijklmnop"):
        assert secret not in out
    assert context.SENSITIVE_FILE.search(".env.local") and context.SENSITIVE_FILE.search("certs/server.pem")


def test_memory_dedup_scoping_and_recall(home):
    acme, john = proj(home, "acme-web"), proj(home, "john-web")
    k1 = context.record_memory(acme, "JWT contains tenant_id", type_="authentication", topic="auth")
    k2 = context.record_memory(acme, "jwt includes tenant_id.", type_="authentication")
    assert k1["id"] == k2["id"]  # same fact, merged
    other = context.record_memory(john, "JWT contains tenant_id")
    assert other["id"] != k1["id"]  # same wording in another project stays scoped to it
    context.record_memory(acme, "Refresh tokens are stored in Redis", type_="architecture")
    context.record_decision(acme, "Use Redis for refresh tokens", reason="TTL support")
    res = context.recall("refresh tokens redis", john)
    assert res and res[0]["project"] == "acme-web" and res[0]["relevance"] == 1.0
    assert {r["kind"] for r in res} >= {"knowledge", "decision"}
    # related projects rank higher; the current project ranks highest
    context.relate(john, "uses-pattern", acme)
    assert context.related_projects(john)[0]["name"] == "acme-web"
    with pytest.raises(ValueError):
        context.record_memory(acme, "x", type_="nonsense")


def test_stale_detection_and_verification(home):
    p = proj(home, "svc")
    src = home / "svc" / "src" / "token.py"
    src.write_text("v1")
    k = context.record_memory(p, "Tokens live in token.py", files=["src/token.py", ".env", "../outside.py"])
    assert [s["file_path"] for s in k["sources"]] == ["src/token.py"]  # secrets files and outside paths are skipped
    assert context.check_stale(p) == []
    src.write_text("v2")
    assert context.check_stale(p) == [k["id"]]
    assert context.get_knowledge(k["id"])["status"] == "STALE"
    v = context.set_status(k["id"], "verified")
    assert v["status"] == "VERIFIED" and v["confidence"] >= 0.95 and context.check_stale(p) == []


def test_state_task_package_projection_and_budget(home):
    root = home / "app"
    p = proj(home, "app")
    subprocess.run(["git", "init", "-q", str(root)], check=False)
    ctx = context.save_state(p, task="Implement auth", objective="OAuth login", step="callback", next_action="check refresh token",
                             last_error="401 with password=secret1")
    assert ctx["task"]["title"] == "Implement auth" and ctx["task"]["status"] == "IN_PROGRESS"
    assert "secret1" not in ctx["state"]["last_error"]
    assert "Next action: check refresh token" in ctx["text"]
    context.save_state(p, status="done")
    assert context.project_context(p)["task"]["status"] == "DONE"
    with pytest.raises(ValueError):
        context.save_state(p, status="bogus")
    for i in range(60):
        context.record_memory(p, f"fact number {i} " + "x" * 80)
    text = context.project_context(p)["text"]
    assert len(text) < context.DEFAULT_BUDGET_CHARS + 1500 and "more: sd memory" in text
    context.record_decision(p, "Keep sessions in cookies")
    context.project_files(p)
    folder = root / ".shelldeck"
    assert "Keep sessions in cookies" in (folder / "DECISIONS.md").read_text(encoding="utf-8")
    assert "fact number" in (folder / "MEMORY.md").read_text(encoding="utf-8")
    assert (folder / "STATE.md").exists() and (folder / "TASK.md").exists() and (folder / ".gitignore").read_text() == "*\n"
    assert "Context snapshot" in context.snapshot(p)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(server, "ALLOWED_HOSTS", {"testserver"})
    with TestClient(server.app) as c:
        c.headers["X-Shelldeck-Token"] = auth.read_cli_token()
        yield c


def test_context_api(client, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    k = client.post("/api/context/memory", json={"cwd": str(folder), "text": "API uses FastAPI", "type": "api"}).json()
    assert k["project"] == "proj" and k["status"] == "NEW"
    assert client.post("/api/context/memory", json={"cwd": str(folder), "text": ""}).json()["error"] == "text_required"
    client.post("/api/context/decisions", json={"cwd": str(folder), "title": "No ORM", "reason": "raw sqlite3"})
    client.post("/api/context/state", json={"cwd": str(folder), "task": "Ship", "next_action": "write docs"})
    ctx = client.get("/api/context", params={"cwd": str(folder)}).json()
    assert ctx["task"]["title"] == "Ship" and "No ORM" in ctx["text"]
    assert client.get("/api/context/recall", params={"q": "fastapi"}).json()["results"][0]["id"] == k["id"]
    assert client.post(f"/api/context/knowledge/{k['id']}/status", json={"status": "verified"}).json()["status"] == "VERIFIED"
    assert client.post("/api/context/knowledge/nope/status", json={}).status_code == 404
    assert client.get("/api/context", params={"project": "missing"}).status_code == 404
    assert [p["name"] for p in client.get("/api/context/projects").json()["projects"]] == ["proj"]


def test_deterministic_capture_and_session_file(home):
    p = proj(home, "cap")
    context.start_session(p, "t1.1", "claude")
    context.record_activity(p, {"command": "uv run pytest -q", "ok": False}, agent="claude", session_id="t1.1")
    context.record_activity(p, {"command": "grep -r TODO .", "ok": False}, agent="claude", session_id="t1.1")  # no match is not an error
    context.record_activity(p, {"command": "make build TOKEN=abc123", "ok": False}, agent="claude", session_id="t1.1")
    context.record_activity(p, {"file": "src/app.py", "change": "edit"}, agent="claude", session_id="t1.1")
    context.record_activity(p, {"file": ".env", "change": "edit"}, agent="claude", session_id="t1.1")  # never recorded
    ctx = context.project_context(p)
    assert ctx["state"]["tests"].startswith("FAILED: uv run pytest -q")
    assert ctx["state"]["last_error"] == "failed: make build TOKEN=[REDACTED]"
    types = [e["type"] for e in context.recent_events(p, 20)]
    assert "TEST_RESULT" in types and types.count("ERROR") == 1 and "COMMAND" in types and "FILE_EDIT" in types
    out = context.end_session(p, "t1.1")
    text = out.read_text()
    assert out.name == "t1.1.md" and "Agent: claude" in text and "- src/app.py" in text and ".env" not in text
    assert "FAILED: uv run pytest -q" in text and "abc123" not in text
    context.record_activity(p, {"command": "uv run pytest -q", "ok": True})
    assert context.project_context(p)["state"]["tests"].startswith("passed")


def test_recall_api_smart(client, tmp_path, monkeypatch):
    from shelldeck import smart_recall
    folder = tmp_path / "sm"
    folder.mkdir()
    client.post("/api/context/memory", json={"cwd": str(folder), "text": "Login uses OAuth"})
    monkeypatch.setattr(smart_recall, "pick", lambda s: "claude" if s in ("auto", "claude") else None)
    seen = []
    monkeypatch.setattr(smart_recall, "expand", lambda q, agent, model=None: seen.append((agent, model)) or ["oauth"])
    monkeypatch.setattr(smart_recall, "small_model", lambda agent: "haiku")
    plain = client.get("/api/context/recall", params={"q": "auth"}).json()
    assert plain["results"] == [] and plain["expanded"] == [] and plain["agent"] is None
    smart = client.get("/api/context/recall", params={"q": "auth", "smart": True}).json()
    assert smart["agent"] == "claude" and smart["expanded"] == ["oauth"] and smart["results"][0]["snippet"] == "Login uses OAuth"
    chosen = client.get("/api/context/recall", params={"q": "auth", "agent": "claude", "model": "sonnet"}).json()
    assert chosen["model"] == "sonnet" and seen[-1] == ("claude", "sonnet")
    assert client.get("/api/context/recall", params={"q": "auth", "agent": "off", "smart": True}).json()["agent"] is None
    assert client.put("/api/settings", json={"recall_agent": "nope"}).status_code == 400
