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
