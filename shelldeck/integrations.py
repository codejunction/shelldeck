"""Built-in agent integration capability registry.

The registry is deliberately declarative: an agent can be detected and shown in
the UI even when its upstream CLI has no safe hook/plugin installation path.
`kind` describes how Shelldeck can integrate today; it must not be interpreted
as permission to edit an agent's configuration.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Integration:
    agent: str
    kind: str  # hook | plugin | native | screen
    lifecycle: bool
    session_restore: bool
    notes: str = ""


# Covers every agent listed by Herdr as built-in or self-reporting support.
# Hook/plugin installation is added only after its upstream format is verified;
# until then `screen` accurately communicates the available integration level.
INTEGRATIONS: tuple[Integration, ...] = (
    Integration("claude", "hook", False, True), Integration("codex", "hook", False, True),
    Integration("copilot", "hook", False, True), Integration("cursor", "hook", False, True),
    Integration("opencode", "plugin", True, True), Integration("pi", "plugin", True, True),
    Integration("omp", "plugin", True, True), Integration("devin", "hook", False, True),
    Integration("droid", "hook", False, True),
    Integration("kimi", "hook", True, True), Integration("kilo", "plugin", True, True),
    Integration("hermes", "plugin", False, True), Integration("qodercli", "hook", False, True),
    Integration("qwen", "hook", False, True), Integration("letta", "hook", False, True, "experimental upstream integration"),
    Integration("mastracode", "hook", True, True), Integration("grok", "hook", False, True),
    Integration("antigravity", "hook", False, True), Integration("amp", "screen", False, False),
    Integration("kiro", "screen", False, False), Integration("maki", "screen", False, False),
    Integration("gemini", "screen", False, False), Integration("cline", "screen", False, False),
    Integration("command", "native", True, False), Integration("crush", "native", True, False),
    Integration("muse", "native", True, False), Integration("prime", "native", True, False),
)

BY_AGENT = {item.agent: item for item in INTEGRATIONS}


def catalog(installed: set[str] | None = None) -> list[dict]:
    """Serializable integration capabilities, optionally marked by installed CLI."""
    installed = installed or set()
    return [{**asdict(item), "available": item.agent in installed} for item in INTEGRATIONS]
