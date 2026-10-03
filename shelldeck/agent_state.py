"""Canonical lifecycle state and authority selection for coding agents.

This module intentionally has no FastAPI, PTY, or database dependency.  The
server can use it for reports and heuristic fallbacks while tests exercise the
state contract in isolation.
"""

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic


class State(StrEnum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    DONE = "done"
    EXITED = "exited"


VALID_BLOCKED_REASONS = frozenset({"approval", "question", "authentication", "tool_error", "external_wait", "unknown"})

# Larger numbers outrank lower numbers. Sources in the same tier use the most
# recent report, which makes replacing an integration implementation safe.
SOURCE_PRIORITY = {
    "integration": 60,
    "custom": 50,
    "native": 40,
    "manifest": 30,
    "heuristic": 20,
}


@dataclass(frozen=True)
class Status:
    """The server-owned lifecycle state shown to clients."""

    state: State
    source: str = ""
    blocked_reason: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.blocked_reason and self.blocked_reason not in VALID_BLOCKED_REASONS:
            raise ValueError("invalid blocked reason")
        if self.blocked_reason and self.state is not State.BLOCKED:
            raise ValueError("blocked reason requires blocked state")


@dataclass(frozen=True)
class AgentSessionReference:
    """Validated native conversation information retained for a future resume."""

    agent: str
    session_id: str
    resume_argv: tuple[str, ...]


@dataclass(frozen=True)
class DisplayMetadata:
    """Presentation-only metadata; it never participates in state resolution."""

    title: str | None = None
    display_agent: str | None = None
    tokens: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Report:
    """A time-limited lifecycle assertion from one source."""

    source: str
    status: Status
    expires_at: float
    reported_at: float

    @property
    def category(self) -> str:
        return self.source.split(":", 1)[0]

    def expired(self, now: float | None = None) -> bool:
        return self.expires_at <= (monotonic() if now is None else now)


def resolve(reports: list[Report], now: float | None = None) -> Status:
    """Return the highest-authority, newest non-expired report or unknown."""
    current = monotonic() if now is None else now
    active = [report for report in reports if not report.expired(current)]
    if not active:
        return Status(State.UNKNOWN)
    selected = max(active, key=lambda report: (SOURCE_PRIORITY.get(report.category, 0), report.reported_at))
    return selected.status
