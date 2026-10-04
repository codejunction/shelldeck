"""Canonical lifecycle state and authority selection for coding agents.

This module intentionally has no FastAPI, PTY, or database dependency.  The
server can use it for reports and heuristic fallbacks while tests exercise the
state contract in isolation.
"""

import re
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
    state_label: str | None = None


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


# --- report validation (POST /api/agent-reports) -------------------------------------------------

MAX_TTL_MS = 120_000
DEFAULT_TTL_MS = 30_000
MAX_TEXT = 120
MAX_TOKENS = 8
MAX_ARGV = 16
MAX_ARGV_BYTES = 1024
_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")
_EXE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_SOURCE = re.compile(r"^[a-z]+:[a-z0-9_.-]{1,40}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class ReportError(ValueError):
    """A rejected report; `str(e)` is a stable error code for integration authors."""


def _text(value, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReportError(code)
    return _CTRL.sub(" ", value).strip()[:MAX_TEXT] or None


def parse_metadata(raw) -> DisplayMetadata:
    if raw is None:
        return DisplayMetadata()
    if not isinstance(raw, dict):
        raise ReportError("invalid_metadata")
    tokens = raw.get("tokens") or {}
    if not isinstance(tokens, dict) or len(tokens) > MAX_TOKENS:
        raise ReportError("invalid_tokens")
    pairs = []
    for k, v in tokens.items():
        if not isinstance(k, str) or not _KEY.match(k) or not isinstance(v, (str, int, float)):
            raise ReportError("invalid_tokens")
        pairs.append((k, _CTRL.sub(" ", str(v))[:40]))
    return DisplayMetadata(_text(raw.get("title"), "invalid_metadata"), _text(raw.get("display_agent"), "invalid_metadata"),
                           tuple(pairs), _text(raw.get("state_label"), "invalid_metadata"))


def parse_resume_argv(raw, agent: str) -> tuple[str, ...]:
    """Only an executable name (no path) followed by bounded, control-free arguments."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ARGV or not all(isinstance(a, str) for a in raw):
        raise ReportError("invalid_resume_argv")
    if not _EXE.match(raw[0]) or any(_CTRL.search(a) for a in raw) or sum(len(a.encode()) for a in raw) > MAX_ARGV_BYTES:
        raise ReportError("invalid_resume_argv")
    if raw[0].lower().removesuffix(".exe").removesuffix(".cmd") != agent:
        raise ReportError("resume_agent_mismatch")
    return tuple(raw)


def parse_report(payload: dict, agent: str, now: float | None = None) -> tuple[Report, DisplayMetadata, AgentSessionReference | None]:
    """Validate one report for a terminal whose running agent is `agent` (from process detection)."""
    if not isinstance(payload, dict):
        raise ReportError("invalid_payload")
    source = payload.get("source")
    if not isinstance(source, str) or not _SOURCE.match(source):
        raise ReportError("invalid_source")
    category = source.split(":", 1)[0]
    if category not in SOURCE_PRIORITY or category == "heuristic":
        raise ReportError("invalid_source")
    if payload.get("agent") != agent:
        raise ReportError("agent_mismatch")
    try:
        state = State(payload.get("state"))
    except ValueError:
        raise ReportError("invalid_state") from None
    reason = payload.get("blocked_reason")
    if reason is not None and (state is not State.BLOCKED or reason not in VALID_BLOCKED_REASONS):
        raise ReportError("invalid_blocked_reason")
    ttl = payload.get("ttl_ms", DEFAULT_TTL_MS)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        raise ReportError("invalid_ttl")
    ttl = min(ttl, MAX_TTL_MS)
    status = Status(state, source=source, blocked_reason=reason if state is State.BLOCKED else None,
                    detail=_text(payload.get("detail"), "invalid_detail"))
    t = monotonic() if now is None else now
    ref = None
    native = payload.get("agent_session_id")
    if native is not None or payload.get("resume_argv") is not None:
        if not isinstance(native, str) or not 1 <= len(native) <= 200 or _CTRL.search(native):
            raise ReportError("invalid_agent_session_id")
        if category != "integration":
            raise ReportError("resume_not_allowed")  # only built-in integrations may issue resume commands
        ref = AgentSessionReference(agent, native, parse_resume_argv(payload.get("resume_argv"), agent))
    return Report(source, status, t + ttl / 1000, t), parse_metadata(payload.get("metadata")), ref


class Registry:
    """In-memory reports per terminal; the server is the only owner of lifecycle state."""

    def __init__(self) -> None:
        self.reports: dict[str, dict[str, Report]] = {}
        self.meta: dict[str, dict[str, tuple[DisplayMetadata, float]]] = {}

    def put(self, sid: str, report: Report, meta: DisplayMetadata | None = None) -> None:
        self.reports.setdefault(sid, {})[report.source] = report
        if meta is not None and meta != DisplayMetadata():
            self.meta.setdefault(sid, {})[report.source] = (meta, report.expires_at)

    def status(self, sid: str, now: float | None = None) -> Status:
        t = monotonic() if now is None else now
        live = {k: r for k, r in self.reports.get(sid, {}).items() if not r.expired(t)}
        self.reports[sid] = live
        return resolve(list(live.values()), t)

    def metadata(self, sid: str, now: float | None = None) -> DisplayMetadata:
        """Metadata from the newest unexpired source; presentation only."""
        t = monotonic() if now is None else now
        live = {k: v for k, v in self.meta.get(sid, {}).items() if v[1] > t}
        self.meta[sid] = live
        return max(live.values(), key=lambda v: v[1])[0] if live else DisplayMetadata()

    def forget(self, sid: str) -> None:
        self.reports.pop(sid, None)
        self.meta.pop(sid, None)


def heuristic(legacy: str, now: float, ttl: float = 10) -> Report:
    """Compatibility adapter: the screen/activity watcher's working|approval|idle as a report."""
    if legacy == "approval":
        status = Status(State.BLOCKED, source="heuristic:screen", blocked_reason="approval")
    elif legacy == "working":
        status = Status(State.WORKING, source="heuristic:activity")
    else:
        status = Status(State.IDLE, source="heuristic:activity")
    return Report(status.source, status, now + ttl, now)


def to_dict(status: Status, meta: DisplayMetadata | None = None) -> dict:
    out = {"state": str(status.state), "source": status.source.split(":", 1)[0] if status.source else "unknown",
           "reason": status.blocked_reason, "detail": status.detail}
    if meta and meta != DisplayMetadata():
        out["meta"] = {"title": meta.title, "display_agent": meta.display_agent, "state_label": meta.state_label, "tokens": dict(meta.tokens)}
    return out
