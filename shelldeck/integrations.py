"""Built-in agent integration capability registry.

The registry is deliberately declarative: an agent can be detected and shown in
the UI even when its upstream CLI has no safe hook/plugin installation path.
`kind` describes how Shelldeck can integrate today; it must not be interpreted
as permission to edit an agent's configuration.
"""

import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import hook


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
    Integration("claude", "hook", True, True), Integration("codex", "native", True, True, "reads its session logs; approval prompts from the screen"),
    Integration("copilot", "hook", True, False), Integration("cursor", "hook", True, False),
    Integration("opencode", "plugin", True, True), Integration("pi", "plugin", True, True),
    Integration("omp", "plugin", True, True), Integration("devin", "hook", False, True),
    Integration("droid", "hook", False, True),
    Integration("kimi", "hook", True, True), Integration("kilo", "plugin", True, True),
    Integration("hermes", "plugin", False, True), Integration("qodercli", "hook", False, True),
    Integration("qwen", "hook", False, True), Integration("letta", "hook", False, True, "experimental upstream integration"),
    Integration("mastracode", "hook", True, True), Integration("grok", "hook", False, True),
    Integration("antigravity", "hook", False, True), Integration("amp", "screen", False, False),
    Integration("kiro", "screen", False, False), Integration("maki", "screen", False, False),
    Integration("gemini", "hook", True, False), Integration("cline", "screen", False, False),
    Integration("command", "native", True, False), Integration("crush", "native", True, False),
    Integration("muse", "native", True, False), Integration("prime", "native", True, False),
)

BY_AGENT = {item.agent: item for item in INTEGRATIONS}


def catalog(installed: set[str] | None = None) -> list[dict]:
    """Serializable integration capabilities, optionally marked by installed CLI."""
    installed = installed or set()
    return [{**asdict(item), "available": item.agent in installed, "installable": item.agent in INSTALLERS,
             **({"status": status(item.agent)["status"]} if item.agent in INSTALLERS else {"status": "unsupported"})} for item in INTEGRATIONS]


# ------------------------------------------------------------------ installers
# Hook/plugin installers for agents whose formats are documented upstream and exercised by dotpals (Claude Code,
# Gemini CLI, Cursor, Copilot CLI, OpenCode). Each one merges into the agent's config, keeps a one-time backup,
# writes atomically, and removes only entries whose command runs `-m shelldeck.hook <agent>` (or its own file).
# Only watching events are used: nothing here can approve, deny or block a tool.

PLUGIN_VERSION = 1
OPENCODE_MARKER = "shelldeck-opencode-plugin"


def hook_command(agent: str, event: str = "") -> str:
    """`"<python>" -m shelldeck.hook <agent> [event]`: absolute interpreter, so it works from any PATH."""
    exe = sys.executable.replace("\\", "/")
    return f'"{exe}" -m shelldeck.hook {agent}' + (f" {event}" if event else "")


def _ours(command, agent: str) -> bool:
    return bool(re.search(rf"-m shelldeck\.hook {re.escape(agent)}\b", str(command or "")))


def _python_of(command: str) -> str | None:
    m = re.match(r'\s*"([^"]+)"', command or "")
    return m[1] if m else None


def _read_json(file: Path, fallback: dict) -> dict:
    """Missing or empty -> fallback; unreadable -> ValueError, and the file is left alone."""
    if not file.exists():
        return json.loads(json.dumps(fallback))
    text = file.read_text(encoding="utf-8").lstrip("﻿")
    if not text.strip():
        return json.loads(json.dumps(fallback))
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError(f"{file} is not plain JSON, so it wasn't changed") from None
    if not isinstance(data, dict):
        raise ValueError(f"{file} is not a JSON object, so it wasn't changed")
    return data


def _backup(file: Path) -> None:
    bak = file.with_name(file.name + ".shelldeck-backup")
    if file.exists() and not bak.exists():
        shutil.copy2(file, bak)


def _write(file: Path, text: str) -> None:
    """Atomic: a half-written config would break the agent."""
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".shelldeck-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, file)


def _home(env: str, *default: str) -> Path:
    return Path(os.environ[env]) if os.environ.get(env) else Path.home().joinpath(*default)


class JsonHooks:
    """Hooks in a shared JSON config: {"hooks": {Event: [entry, ...]}}."""

    def __init__(self, agent: str, file, entry, top: dict | None = None):
        self.agent, self._file, self.entry, self.top = agent, file, entry, top or {}

    @property
    def file(self) -> Path:
        return self._file()

    def events(self) -> list[str]:
        return [e for e, s in hook.EVENTS[self.agent].items() if s is not None or e in ("Notification", "notification")]

    def _is_ours(self, item) -> bool:
        if not isinstance(item, dict):
            return False
        cmds = [item.get("command")] + [h.get("command") for h in item.get("hooks", []) if isinstance(h, dict)]
        return any(_ours(c, self.agent) for c in cmds)

    def _strip(self, cfg: dict) -> bool:
        changed = False
        hooks = cfg.get("hooks")
        if not isinstance(hooks, dict):
            return False
        for event, items in list(hooks.items()):
            if not isinstance(items, list):
                continue
            kept = [i for i in items if not self._is_ours(i)]
            changed |= len(kept) != len(items)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        if changed and not hooks:
            del cfg["hooks"]
        return changed

    def installed_commands(self) -> list[str]:
        try:
            hooks = _read_json(self.file, {}).get("hooks") or {}
        except ValueError:
            return []
        out = []
        for event, items in hooks.items() if isinstance(hooks, dict) else ():
            for i in items if isinstance(items, list) else ():
                if self._is_ours(i):
                    out.append(event)
        return out

    def install(self) -> dict:
        cfg = _read_json(self.file, self.top)
        if cfg.get("hooks") is not None and not isinstance(cfg["hooks"], dict):
            raise ValueError(f"{self.file} has an unexpected \"hooks\" value, so it wasn't changed")
        _backup(self.file)
        self._strip(cfg)
        for k, v in self.top.items():
            cfg.setdefault(k, v)
        hooks = cfg.setdefault("hooks", {})
        for event in self.events():
            hooks.setdefault(event, []).append(self.entry(event))
        _write(self.file, json.dumps(cfg, indent=2) + "\n")
        return {"file": str(self.file)}

    def uninstall(self) -> dict:
        if not self.file.exists():
            return {"file": str(self.file)}
        cfg = _read_json(self.file, {})
        if self._strip(cfg):
            _write(self.file, json.dumps(cfg, indent=2) + "\n")
        return {"file": str(self.file)}

    def state(self) -> tuple[bool, bool]:
        """(installed, current): current = every event hooked and its interpreter still exists."""
        found = self.installed_commands()
        if not found:
            return False, False
        try:
            cmds = [c for items in (_read_json(self.file, {}).get("hooks") or {}).values() for i in items if self._is_ours(i)
                    for c in [i.get("command")] + [h.get("command") for h in i.get("hooks", []) if isinstance(h, dict)] if _ours(c, self.agent)]
        except (ValueError, AttributeError):
            cmds = []
        exe_ok = all((p := _python_of(c)) and Path(p).exists() for c in cmds) and bool(cmds)
        return True, exe_ok and set(found) == set(self.events())


class OwnFile:
    """A file shelldeck owns entirely (Copilot's hook file, OpenCode's plugin)."""

    def __init__(self, agent: str, file, render, marker: str):
        self.agent, self._file, self.render, self.marker = agent, file, render, marker

    @property
    def file(self) -> Path:
        return self._file()

    def install(self) -> dict:
        if self.file.exists() and self.marker not in self.file.read_text(encoding="utf-8", errors="replace"):
            _backup(self.file)
        _write(self.file, self.render())
        return {"file": str(self.file)}

    def uninstall(self) -> dict:
        if self.file.exists() and self.marker in self.file.read_text(encoding="utf-8", errors="replace"):
            self.file.unlink()
        bak = self.file.with_name(self.file.name + ".shelldeck-backup")
        if bak.exists() and not self.file.exists():
            os.replace(bak, self.file)
        return {"file": str(self.file)}

    def state(self) -> tuple[bool, bool]:
        if not self.file.exists():
            return False, False
        text = self.file.read_text(encoding="utf-8", errors="replace")
        if self.marker not in text:
            return False, False
        return True, text == self.render()


def _copilot_file() -> str:
    hooks = {e: [{"type": "command", "command": hook_command("copilot", e), "timeoutSec": 5}] for e in hook.EVENTS["copilot"]}
    return json.dumps({"version": 1, "hooks": hooks}, indent=2) + "\n"


OPENCODE_PLUGIN = """// {marker} v{version}
// Reports OpenCode's lifecycle (working / needs approval / done) to the shelldeck terminal it runs in.
// Installed by `sd integration install opencode`; `sd integration uninstall opencode` removes it.
// Outside a shelldeck terminal it does nothing, and nothing here changes what OpenCode does.
export const ShelldeckPlugin = async () => {{
  const env = (globalThis.process && process.env) || {{}}
  const token = env.SHELLDECK_AGENT_REPORT_TOKEN
  const url = "http://127.0.0.1:" + (env.SHELLDECK_PORT || "5455") + "/api/agent-reports"
  const send = (state, sessionID, reason) => {{
    if (!token) return
    try {{
      const body = {{ source: "integration:opencode", agent: "opencode", state, blocked_reason: reason || null,
        ttl_ms: state === "working" ? 60000 : 120000 }}
      if (typeof sessionID === "string" && sessionID) {{
        body.agent_session_id = sessionID
        body.resume_argv = ["opencode", "--session", sessionID]
      }}
      fetch(url, {{ method: "POST", headers: {{ "content-type": "application/json", "x-shelldeck-report-token": token }},
        body: JSON.stringify(body), signal: AbortSignal.timeout(1500) }}).catch(() => {{}})
    }} catch {{}}
  }}
  return {{
    "chat.message": async (input) => {{ try {{ send("working", input.sessionID) }} catch {{}} }},
    "tool.execute.before": async (input) => {{ try {{ send("working", input.sessionID) }} catch {{}} }},
    event: async ({{ event }}) => {{
      try {{
        const id = (event.properties || {{}}).sessionID
        if (event.type === "session.created") send("idle", id)
        else if (event.type === "session.idle") send("done", id)
        else if (event.type === "session.error") send("idle", id)
        else if (event.type === "permission.asked") send("blocked", id, "approval")
        else if (event.type === "permission.replied") send("working", id)
      }} catch {{}}
    }},
  }}
}}
"""


def _opencode_plugin() -> str:
    return OPENCODE_PLUGIN.format(marker=OPENCODE_MARKER, version=PLUGIN_VERSION)


INSTALLERS = {
    "claude": JsonHooks("claude", lambda: _home("CLAUDE_CONFIG_DIR", ".claude") / "settings.json",
                        lambda e: {"hooks": [{"type": "command", "command": hook_command("claude"), "timeout": 5, "async": True}]}),
    "gemini": JsonHooks("gemini", lambda: _home("GEMINI_DIR", ".gemini") / "settings.json",
                        lambda e: {"matcher": "*", "hooks": [{"name": "shelldeck", "type": "command", "command": hook_command("gemini"), "timeout": 5000}]}),
    "cursor": JsonHooks("cursor", lambda: _home("CURSOR_DIR", ".cursor") / "hooks.json",
                        lambda e: {"command": hook_command("cursor"), "timeout": 5}, top={"version": 1}),
    "copilot": OwnFile("copilot", lambda: _home("COPILOT_HOME", ".copilot") / "hooks" / "shelldeck.json", _copilot_file, "-m shelldeck.hook copilot"),
    "opencode": OwnFile("opencode", lambda: (Path(os.environ["XDG_CONFIG_HOME"]) if os.environ.get("XDG_CONFIG_HOME") else Path.home() / ".config")
                        / "opencode" / "plugins" / "shelldeck.js", _opencode_plugin, OPENCODE_MARKER),
}


def status(agent: str, available: bool = False) -> dict:
    """installed | outdated (re-install to fix: moved interpreter, changed events) | not_installed | unsupported | error."""
    inst = INSTALLERS.get(agent)
    if not inst:
        return {"agent": agent, "status": "unsupported", "file": None}
    try:
        on, current = inst.state()
    except (OSError, ValueError) as e:
        return {"agent": agent, "status": "error", "file": str(inst.file), "detail": str(e)}
    return {"agent": agent, "status": "installed" if on and current else "outdated" if on else "not_installed",
            "file": str(inst.file), "available": available or inst.file.parent.exists()}


def install(agent: str) -> dict:
    if agent not in INSTALLERS:
        raise KeyError(agent)
    return {**INSTALLERS[agent].install(), **status(agent)}


def uninstall(agent: str) -> dict:
    if agent not in INSTALLERS:
        raise KeyError(agent)
    return {**INSTALLERS[agent].uninstall(), **status(agent)}
