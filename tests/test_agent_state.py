import pytest

from shelldeck.agent_state import AgentSessionReference, DisplayMetadata, Report, State, Status, resolve


def report(source: str, state: State, *, at: float = 10, expires: float = 20) -> Report:
    return Report(source, Status(state, source=source), expires, at)


def test_resolve_prefers_authority_then_newest_report():
    assert resolve([report("heuristic:screen", State.WORKING), report("integration:opencode", State.IDLE)], 11).state is State.IDLE
    assert resolve([report("heuristic:old", State.WORKING, at=10), report("heuristic:new", State.BLOCKED, at=11)], 12).state is State.BLOCKED


def test_resolve_ignores_expired_and_unknown_sources_have_lowest_priority():
    expired = report("integration:codex", State.BLOCKED, expires=10)
    fallback = report("manifest:codex", State.IDLE)
    assert resolve([expired, fallback], 10).state is State.IDLE
    assert resolve([report("other:tool", State.WORKING)], 11).state is State.WORKING
    assert resolve([], 11) == Status(State.UNKNOWN)


def test_status_requires_a_valid_blocked_reason():
    assert Status(State.BLOCKED, blocked_reason="approval").blocked_reason == "approval"
    with pytest.raises(ValueError, match="invalid"):
        Status(State.BLOCKED, blocked_reason="not-a-reason")
    with pytest.raises(ValueError, match="requires"):
        Status(State.IDLE, blocked_reason="approval")


def test_session_reference_and_display_metadata_are_typed_values():
    reference = AgentSessionReference("codex", "thread-1", ("codex", "resume", "thread-1"))
    metadata = DisplayMetadata(title="Review auth", tokens=(("model", "gpt"),))
    assert reference.resume_argv[0] == "codex"
    assert metadata.tokens == (("model", "gpt"),)


from shelldeck.agent_state import Registry, ReportError, heuristic, parse_report, to_dict  # noqa: E402


def payload(**kw):
    return {"source": "integration:opencode", "agent": "opencode", "state": "working", **kw}


def test_parse_report_validates_and_caps():
    r, meta, ref = parse_report(payload(ttl_ms=10**9, metadata={"title": "x" * 500, "tokens": {"model": "m"}}), "opencode", now=0)
    assert r.expires_at == 120 and len(meta.title) == 120 and meta.tokens == (("model", "m"),) and ref is None
    for bad, code in ((payload(agent="codex"), "agent_mismatch"), (payload(state="approval"), "invalid_state"),
                      (payload(source="heuristic:x"), "invalid_source"), (payload(ttl_ms=0), "invalid_ttl"),
                      (payload(blocked_reason="approval"), "invalid_blocked_reason"),
                      (payload(metadata={"tokens": {"Bad Key": 1}}), "invalid_tokens")):
        with pytest.raises(ReportError, match=code):
            parse_report(bad, "opencode")


def test_resume_argv_allow_list():
    _, _, ref = parse_report(payload(agent_session_id="s1", resume_argv=["opencode", "--session", "s1"]), "opencode")
    assert ref.resume_argv == ("opencode", "--session", "s1")
    for argv, code in ((["/bin/sh", "-c", "x"], "invalid_resume_argv"), (["codex", "resume"], "resume_agent_mismatch"),
                       (["opencode", "a\nb"], "invalid_resume_argv"), (["opencode"] * 40, "invalid_resume_argv")):
        with pytest.raises(ReportError, match=code):
            parse_report(payload(agent_session_id="s1", resume_argv=argv), "opencode")
    with pytest.raises(ReportError, match="resume_not_allowed"):
        parse_report(payload(source="custom:tool", agent_session_id="s1", resume_argv=["opencode"]), "opencode")


def test_registry_falls_back_when_integration_expires_and_metadata_is_presentation_only():
    reg = Registry()
    reg.put("t", heuristic("approval", 0, ttl=100))
    r, meta, _ = parse_report(payload(state="idle", ttl_ms=5000, metadata={"state_label": "reviewing"}), "opencode", now=0)
    reg.put("t", r, meta)
    assert to_dict(reg.status("t", 1), reg.metadata("t", 1))["state"] == "idle"
    assert reg.metadata("t", 1).state_label == "reviewing"
    st = reg.status("t", 6)  # integration expired: the screen heuristic decides again
    assert (st.state, st.blocked_reason) == (State.BLOCKED, "approval")
    assert reg.metadata("t", 6) == DisplayMetadata()
