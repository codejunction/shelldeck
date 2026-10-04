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
    for env, sub in (("CLAUDE_CONFIG_DIR", "claude"), ("GEMINI_DIR", "gemini"), ("CURSOR_DIR", "cursor"), ("COPILOT_HOME", "copilot"),
                     ("XDG_CONFIG_HOME", "xdg"), ("CODEX_HOME", "codex"), ("QWEN_HOME", "qwen"), ("QODER_CONFIG_DIR", "qoder")):
        monkeypatch.setenv(env, str(tmp_path / sub))
    monkeypatch.setattr(integrations.Path, "home", lambda: tmp_path / "home")
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
    (homes / "gemini").mkdir()
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


def test_install_needs_the_agent_installed(homes):
    with pytest.raises(ValueError, match="install qwen first"):
        integrations.install("qwen")
    with pytest.raises(ValueError, match="install copilot first"):
        integrations.install("copilot")


@pytest.mark.parametrize("agent,rel,matcher", [("qwen", "qwen/settings.json", "*"), ("qodercli", "qoder/settings.json", "*"),
                                               ("droid", "home/.factory/settings.json", None), ("devin", "xdg/devin/config.json", None)])
def test_session_only_agents(homes, agent, rel, matcher):
    file = homes / rel
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"keep": 1}))
    integrations.install(agent)
    cfg = json.loads(file.read_text())
    group = cfg["hooks"]["SessionStart"][0]
    assert group.get("matcher") == matcher and group["hooks"][0]["command"].endswith(f"-m shelldeck.hook {agent}")
    integrations.uninstall(agent)
    assert json.loads(file.read_text()) == {"keep": 1}
    body = hook.report(agent, {"hook_event_name": "SessionStart", "session_id": "s-9"})
    assert "state" not in body and body["agent_session_id"] == "s-9" and body["resume_argv"][-1].endswith("s-9")
    assert hook.report(agent, {"hook_event_name": "SessionStart"}) is None  # nothing to say without an id


def test_codex_hooks_and_feature_flag(homes):
    d = homes / "codex"
    d.mkdir()
    (d / "config.toml").write_text('model = "gpt-5"\n\n[features]\ncodex_hooks = true  # old name\nweb = true\n')
    integrations.install("codex")
    toml = (d / "config.toml").read_text()
    assert "hooks = true" in toml and "codex_hooks" not in toml and 'model = "gpt-5"' in toml and "web = true" in toml
    assert set(json.loads((d / "hooks.json").read_text())["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop", "Interrupt"}
    assert integrations.status("codex")["status"] == "installed"
    assert integrations.enable_codex_hooks("") == "[features]\nhooks = true\n"
    assert integrations.enable_codex_hooks("[features]\nhooks = false\n") == "[features]\nhooks = true\n"
    assert integrations.enable_codex_hooks("[tui]\nx = 1\n").endswith("\n\n[features]\nhooks = true\n")
    integrations.uninstall("codex")
    assert "hooks" not in json.loads((d / "hooks.json").read_text())
    assert hook.report("codex", {"hook_event_name": "Stop", "session_id": "t1"})["resume_argv"] == ["codex", "resume", "t1"]


def test_cursor_resume_uses_cursor_agent():
    from shelldeck import agent_state
    body = hook.report("cursor", {"hook_event_name": "stop", "conversation_id": "c1"})
    _, _, ref = agent_state.parse_report(body, "cursor")
    assert ref.resume_argv == ("cursor-agent", "--resume", "c1")


def test_kimi_toml_block_round_trip(homes, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(homes / "kimi"))
    (homes / "kimi").mkdir()
    cfg = homes / "kimi" / "config.toml"
    cfg.write_text('model = "k2"\n\n[[hooks]]\nevent = "Stop"\ncommand = "theirs"\n')
    integrations.install("kimi")
    integrations.install("kimi")
    text = cfg.read_text()
    assert text.count(integrations.KIMI_BEGIN) == 1 and 'command = "theirs"' in text
    assert '"^AskUserQuestion$"' in text and "-m shelldeck.hook kimi blocked" in text
    assert integrations.status("kimi")["status"] == "installed"
    integrations.uninstall("kimi")
    assert cfg.read_text() == 'model = "k2"\n\n[[hooks]]\nevent = "Stop"\ncommand = "theirs"\n'


def test_mastracode_flat_hooks(homes):
    d = homes / "home" / ".mastracode"
    d.mkdir(parents=True)
    (d / "hooks.json").write_text(json.dumps({"Stop": [{"type": "command", "command": "theirs"}]}))
    integrations.install("mastracode")
    cfg = json.loads((d / "hooks.json").read_text())
    assert "hooks" not in cfg and cfg["PermissionRequest"][0]["command"].endswith("-m shelldeck.hook mastracode")
    assert integrations.status("mastracode")["status"] == "installed"
    integrations.uninstall("mastracode")
    assert json.loads((d / "hooks.json").read_text()) == {"Stop": [{"type": "command", "command": "theirs"}]}


def test_antigravity_named_block_grok_and_kilo(homes, monkeypatch):
    monkeypatch.setenv("ANTIGRAVITY_CLI_CONFIG_DIR", str(homes / "agy"))
    monkeypatch.setenv("GROK_HOME", str(homes / "grok"))
    for d in ("agy", "grok", "xdg/kilo"):
        (homes / d).mkdir(parents=True)
    (homes / "agy" / "hooks.json").write_text(json.dumps({"mine": {"PreInvocation": []}}))
    for agent in ("antigravity", "grok", "kilo"):
        integrations.install(agent)
        assert integrations.status(agent)["status"] == "installed", agent
    assert set(json.loads((homes / "agy" / "hooks.json").read_text())) == {"mine", "shelldeck"}
    plugin = (homes / "xdg" / "kilo" / "plugin" / "shelldeck.js").read_text()
    assert '"integration:kilo"' in plugin and '["kilo", "--session", sessionID]' in plugin
    for agent in ("antigravity", "grok", "kilo"):
        integrations.uninstall(agent)
        assert integrations.status(agent)["status"] == "not_installed"
    assert json.loads((homes / "agy" / "hooks.json").read_text()) == {"mine": {"PreInvocation": []}}
    assert hook.report("antigravity", {"hook_event_name": "PreInvocation", "conversationId": "cv"})["resume_argv"] == ["agy", "--conversation", "cv"]


def test_forced_state_from_installed_command(monkeypatch):
    sent = []
    monkeypatch.setenv("SHELLDECK_AGENT_REPORT_TOKEN", "t")
    monkeypatch.setattr(hook, "send", lambda body, token: sent.append(body))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "k"})))
    hook.main(["kimi", "blocked"])
    assert sent[0]["state"] == "blocked" and sent[0]["blocked_reason"] == "question" and sent[0]["resume_argv"] == ["kimi", "--session", "k"]


def test_letta_session_hook(homes):
    d = homes / "home" / ".letta"
    d.mkdir(parents=True)
    integrations.install("letta")
    group = json.loads((d / "settings.json").read_text())["hooks"]["SessionStart"][0]
    assert group["hooks"][0]["quiet"] is True and group["hooks"][0]["timeout"] == 10_000
    assert hook.report("letta", {"hook_event_name": "SessionStart", "conversation_id": "default", "agent_id": "agent-7"})["resume_argv"] == [
        "letta", "--conversation", "default", "--agent", "agent-7"]
    assert hook.report("letta", {"hook_event_name": "SessionStart", "conversation_id": "conv-2"})["resume_argv"] == ["letta", "--conversation", "conv-2"]
    assert hook.report("letta", {"hook_event_name": "SessionStart", "conversation_id": "default"}) is None
    integrations.uninstall("letta")
    assert json.loads((d / "settings.json").read_text()) == {}


@pytest.mark.parametrize("before", ["", "model: x\n", "model: x\nplugins:\n  enabled:\n    - other\n  dir: y\nui: z\n"])
def test_hermes_plugin_and_yaml(homes, monkeypatch, before):
    monkeypatch.setenv("HERMES_HOME", str(homes / "hermes"))
    (homes / "hermes").mkdir()
    cfg = homes / "hermes" / "config.yaml"
    cfg.write_text(before)
    integrations.install("hermes")
    integrations.install("hermes")
    assert cfg.read_text().count("- shelldeck-agent-state") == 1
    assert integrations.status("hermes")["status"] == "installed"
    assert (homes / "hermes" / "plugins" / "shelldeck-agent-state" / "plugin.yaml").exists()
    integrations.uninstall("hermes")
    assert cfg.read_text() == before and not (homes / "hermes" / "plugins" / "shelldeck-agent-state").exists()


def test_hermes_refuses_inline_yaml(homes, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(homes / "hermes"))
    (homes / "hermes").mkdir()
    (homes / "hermes" / "config.yaml").write_text("plugins: {enabled: [a]}\n")
    with pytest.raises(ValueError, match="by hand"):
        integrations.install("hermes")
    assert not (homes / "hermes" / "plugins").exists()
