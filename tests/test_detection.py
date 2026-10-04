from shelldeck import detection


def test_bundled_rules_match_known_prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    detection._cache.clear()
    assert detection.match("claude", "Do you want to proceed?\n 1. Yes").id == "permission.proceed"
    assert detection.match("codex", "Continue? [y/n]").reason == "question"
    assert detection.match("claude", "all done") is None
    # the prompt drawn last wins
    assert detection.match("claude", "Do you want to proceed?\n... later ...\nEnter to confirm").id == "menu.enter-to-confirm"
    m = detection.load("claude")
    assert m.source == "bundled" and m.version >= 1 and not m.error


def test_local_override_replaces_disables_and_adds(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    detection._cache.clear()
    d = tmp_path / "agent-detection"
    d.mkdir()
    (d / "gemini.toml").write_text('''version = 2
disable = ["prompt.yes-no"]
[[rules]]
id = "gemini.allow"
pattern = "Allow execution of"
reason = "approval"
''')
    m = detection.load("gemini")
    assert m.source.endswith("gemini.toml") and m.version == 2
    assert detection.match("gemini", "Allow execution of: rm -rf build?").id == "gemini.allow"
    assert detection.match("gemini", "Continue? [y/n]") is None  # disabled
    assert detection.match("claude", "Continue? [y/n]").id == "prompt.yes-no"  # other agents unchanged


def test_invalid_override_is_ignored_and_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path))
    detection._cache.clear()
    d = tmp_path / "agent-detection"
    d.mkdir()
    (d / "codex.toml").write_text('version = 1\n[[rules]]\nid = "bad"\npattern = "(unclosed"\n')
    m = detection.load("codex")
    assert m.source == "bundled" and "override ignored" in m.error and "bad" in m.error
    assert detection.match("codex", "Do you want to proceed?").id == "permission.proceed"  # bundled rules still work
    (d / "codex.toml").write_text("not = [toml")
    detection._cache.clear()
    assert "override ignored" in detection.load("codex").error
