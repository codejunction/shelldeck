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
