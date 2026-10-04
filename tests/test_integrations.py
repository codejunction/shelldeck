from shelldeck import integrations


def test_registry_covers_herdr_agent_catalog():
    expected = {"claude", "codex", "copilot", "cursor", "opencode", "pi", "omp", "devin", "droid", "kimi", "kilo", "hermes", "qodercli", "qwen", "letta", "mastracode", "grok", "antigravity", "amp", "kiro", "maki", "gemini", "cline", "command", "crush", "muse", "prime"}
    assert expected == set(integrations.BY_AGENT)


def test_catalog_marks_available_agents_without_changing_capabilities():
    rows = {item["agent"]: item for item in integrations.catalog({"codex"})}
    assert rows["codex"]["available"] is True
    assert rows["codex"]["session_restore"] is True
    assert rows["amp"]["available"] is False
    assert rows["amp"]["kind"] == "screen"


import io  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

from shelldeck import hook  # noqa: E402


@pytest.fixture
def homes(tmp_path, monkeypatch):
    for env, sub in (("CLAUDE_CONFIG_DIR", "claude"), ("GEMINI_DIR", "gemini"), ("CURSOR_DIR", "cursor"), ("COPILOT_HOME", "copilot"), ("XDG_CONFIG_HOME", "xdg")):
        monkeypatch.setenv(env, str(tmp_path / sub))
    return tmp_path


UNRELATED = {
    "claude": ("claude/settings.json", {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}),
    "gemini": ("gemini/settings.json", {"theme": "x", "hooks": {"AfterAgent": [{"matcher": "*", "hooks": [{"type": "command", "command": "notify"}]}]}}),
    "cursor": ("cursor/hooks.json", {"version": 1, "hooks": {"stop": [{"command": "other"}]}}),
}


@pytest.mark.parametrize("agent", ["claude", "gemini", "cursor"])
def test_json_hook_install_is_idempotent_and_keeps_other_config(homes, agent):
    rel, original = UNRELATED[agent]
    file = homes / rel
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps(original))
    assert integrations.status(agent)["status"] == "not_installed"
    integrations.install(agent)
    once = json.loads(file.read_text())
    assert integrations.install(agent)["status"] == "installed"
    assert json.loads(file.read_text()) == once  # installing twice changes nothing
    ours = integrations.INSTALLERS[agent].installed_commands()
    assert set(ours) == set(integrations.INSTALLERS[agent].events())
    assert (file.parent / (file.name + ".shelldeck-backup")).exists()
    integrations.uninstall(agent)
    assert json.loads(file.read_text()) == original  # only ours removed
    assert integrations.status(agent)["status"] == "not_installed"


def test_unreadable_config_is_never_touched(homes):
    file = homes / "claude" / "settings.json"
    file.parent.mkdir(parents=True)
    file.write_text("{ // comments\n}")
    with pytest.raises(ValueError):
        integrations.install("claude")
    assert file.read_text() == "{ // comments\n}"


def test_outdated_when_interpreter_moved(homes):
    integrations.install("gemini")
    file = homes / "gemini" / "settings.json"
    file.write_text(file.read_text().replace(integrations.sys.executable.replace("\\", "/"), "/gone/python"))
    assert integrations.status("gemini")["status"] == "outdated"
    assert integrations.install("gemini")["status"] == "installed"


@pytest.mark.parametrize("agent,name", [("copilot", "copilot/hooks/shelldeck.json"), ("opencode", "xdg/opencode/plugins/shelldeck.js")])
def test_own_file_install_restores_what_was_there(homes, agent, name):
    file = homes / name
    file.parent.mkdir(parents=True)
    file.write_text("theirs")
    integrations.install(agent)
    assert integrations.status(agent)["status"] == "installed"
    if agent == "copilot":
        hooks = json.loads(file.read_text())["hooks"]
        assert hooks["agentStop"][0]["command"].endswith("-m shelldeck.hook copilot agentStop")
    else:
        assert "x-shelldeck-report-token" in file.read_text() and "permission.asked" in file.read_text()
    integrations.uninstall(agent)
    assert file.read_text() == "theirs"


def test_unsupported_agent():
    assert integrations.status("amp")["status"] == "unsupported"
    with pytest.raises(KeyError):
        integrations.install("amp")


def test_hook_maps_events_to_lifecycle():
    assert hook.state_for("claude", {"hook_event_name": "PreToolUse"}) == ("working", None)
    assert hook.state_for("claude", {"hook_event_name": "PermissionRequest"}) == ("blocked", "approval")
    assert hook.state_for("claude", {"hook_event_name": "Notification", "notification_type": "permission_prompt"}) == ("blocked", "approval")
    assert hook.state_for("claude", {"hook_event_name": "Notification", "message": "Claude is waiting for your input"}) == ("idle", None)
    assert hook.state_for("gemini", {"hook_event_name": "AfterAgent"}) == ("done", None)
    assert hook.state_for("cursor", {"hook_event_name": "nope"}) == (None, None)
    assert hook.state_for("copilot", {"hook_event_name": "errorOccurred", "recoverable": True}) == (None, None)
    body = hook.report("claude", {"hook_event_name": "Stop", "session_id": "abc"})
    assert body["state"] == "done" and body["resume_argv"] == ["claude", "--resume", "abc"]
    assert "resume_argv" not in hook.report("gemini", {"hook_event_name": "BeforeTool", "session_id": "x"})


def test_hook_main_never_fails_and_replies(monkeypatch, capsys):
    sent = []
    monkeypatch.setenv("SHELLDECK_AGENT_REPORT_TOKEN", "t")
    monkeypatch.setattr(hook, "send", lambda body, token: sent.append(body) or (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"hook_event_name": "beforeSubmitPrompt"})))
    assert hook.main(["cursor"]) == 0
    assert capsys.readouterr().out == '{"continue":true}' and sent[0]["state"] == "working"
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert hook.main(["copilot", "agentStop"]) == 0 and sent[-1]["state"] == "done"
    monkeypatch.delenv("SHELLDECK_AGENT_REPORT_TOKEN")
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert hook.main(["claude"]) == 0 and len(sent) == 2  # outside shelldeck: nothing sent
