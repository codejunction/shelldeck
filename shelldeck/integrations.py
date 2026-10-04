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
    Integration("claude", "hook", True, True), Integration("codex", "hook", True, True, "hooks, plus its session logs without them; approval prompts from the screen"),
    Integration("copilot", "hook", True, True), Integration("cursor", "hook", True, True),
    Integration("opencode", "plugin", True, True), Integration("pi", "plugin", True, True),
    Integration("omp", "plugin", True, True), Integration("devin", "hook", False, True, "session id only; state from the screen"),
    Integration("droid", "hook", False, True, "session id only; state from the screen"),
    Integration("kimi", "hook", True, True, "needs Kimi Code 0.14+"), Integration("kilo", "plugin", True, True),
    Integration("hermes", "plugin", False, True, "session id only; state from the screen"), Integration("qodercli", "hook", False, True, "session id only; state from the screen"),
    Integration("qwen", "hook", False, True, "session id only; state from the screen"), Integration("letta", "hook", False, True, "session id only; experimental upstream"),
    Integration("mastracode", "hook", True, True), Integration("grok", "hook", False, True, "session id only; state from the screen"),
    Integration("antigravity", "hook", False, True, "session id only; state from the screen"), Integration("amp", "screen", False, False),
    Integration("kiro", "screen", False, False), Integration("maki", "screen", False, False),
    Integration("gemini", "hook", True, False), Integration("cline", "screen", False, False),
    Integration("command", "native", True, False), Integration("crush", "native", True, False),
    Integration("muse", "native", True, False), Integration("prime", "native", True, False),
)

BY_AGENT = {item.agent: item for item in INTEGRATIONS}


def catalog(installed: set[str] | None = None) -> list[dict]:
    """Serializable integration capabilities, optionally marked by installed CLI."""
    installed = installed or set()
    out = []
    for item in INTEGRATIONS:
        st = status(item.agent, item.agent in installed) if item.agent in INSTALLERS else {"status": "unsupported"}
        out.append({**asdict(item), "available": item.agent in installed or st.get("available", False), "installable": item.agent in INSTALLERS,
                    "config_found": bool(st.get("available")), "status": st["status"]})
    return out


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

    def __init__(self, agent: str, file, entry, top: dict | None = None, key: str | None = "hooks"):
        # key=None: events at the top level of the file (MastraCode's hooks.json)
        self.agent, self._file, self.entry, self.top, self.key = agent, file, entry, top or {}, key

    def _box(self, cfg: dict, create: bool = False):
        if self.key is None:
            return cfg
        if create:
            return cfg.setdefault(self.key, {})
        box = cfg.get(self.key)
        return box if isinstance(box, dict) else None

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
        hooks = self._box(cfg)
        if hooks is None:
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
        if changed and not hooks and self.key:
            del cfg[self.key]
        return changed

    def installed_commands(self) -> list[str]:
        try:
            hooks = self._box(_read_json(self.file, {})) or {}
        except ValueError:
            return []
        out = []
        for event, items in hooks.items() if isinstance(hooks, dict) else ():
            for i in items if isinstance(items, list) else ():
                if self._is_ours(i):
                    out.append(event)
        return out

    def install(self) -> dict:
        if not self.file.parent.is_dir():
            raise ValueError(f"{self.agent} config folder not found at {self.file.parent}; install {self.agent} first")
        cfg = _read_json(self.file, self.top)
        if self.key and cfg.get(self.key) is not None and not isinstance(cfg[self.key], dict):
            raise ValueError(f"{self.file} has an unexpected \"{self.key}\" value, so it wasn't changed")
        _backup(self.file)
        self._strip(cfg)
        for k, v in self.top.items():
            cfg.setdefault(k, v)
        hooks = self._box(cfg, create=True)
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
            cmds = [c for items in (self._box(_read_json(self.file, {})) or {}).values() if isinstance(items, list) for i in items if self._is_ours(i)
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
        root = self.file.parent.parent if self.file.parent.name in ("hooks", "plugins", "plugin", "extensions") else self.file.parent
        if not root.is_dir():
            raise ValueError(f"{self.agent} config folder not found at {root}; install {self.agent} first")
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


class CodexHooks(JsonHooks):
    """~/.codex/hooks.json plus `[features] hooks = true` in config.toml, which Codex needs to run hooks (as herdr
    installs it). Uninstall leaves the feature flag: with no hooks it does nothing, and the user may rely on it."""

    @property
    def config(self) -> Path:
        return self.file.with_name("config.toml")

    def install(self) -> dict:
        out = super().install()
        text = self.config.read_text(encoding="utf-8") if self.config.exists() else ""
        new = enable_codex_hooks(text)
        if new != text:
            _backup(self.config)
            _write(self.config, new)
        return out

    def state(self) -> tuple[bool, bool]:
        on, current = super().state()
        text = self.config.read_text(encoding="utf-8") if self.config.exists() else ""
        return on, current and enable_codex_hooks(text) == text


def enable_codex_hooks(text: str) -> str:
    """Set `hooks = true` in the top-level [features] table (line edit: comments and order are kept)."""
    lines = text.splitlines()
    in_features, header, hooks_at = False, None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("["):
            in_features = s.split("#", 1)[0].strip() == "[features]"
            if in_features and header is None:
                header = i
            continue
        if in_features and re.match(r"(hooks|codex_hooks)\s*=", s):
            hooks_at = hooks_at if hooks_at is not None else i
            if s.startswith("codex_hooks"):
                lines[i] = "hooks = true"
    if hooks_at is not None:
        lines[hooks_at] = "hooks = true"
    elif header is not None:
        lines.insert(header + 1, "hooks = true")
    else:
        lines += ([""] if lines and lines[-1].strip() else []) + ["[features]", "hooks = true"]
    return "\n".join(lines) + "\n"


def _devin_dir() -> Path:
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "devin"
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "devin"
    return Path.home() / ".config" / "devin"


def _cmd_entry(agent: str, timeout: int, matcher: str | None = None):
    """A Claude-shaped hook group: {matcher?, hooks: [{type: command, command, timeout}]}."""
    def entry(event: str) -> dict:
        group = {"hooks": [{"type": "command", "command": hook_command(agent), "timeout": timeout}]}
        return {"matcher": matcher, **group} if matcher else group
    return entry


class NamedBlock:
    """One shelldeck-owned key in a JSON file keyed by hook name (Antigravity CLI's hooks.json)."""

    def __init__(self, agent: str, file, render):
        self.agent, self._file, self.render = agent, file, render

    @property
    def file(self) -> Path:
        return self._file()

    def install(self) -> dict:
        if not self.file.parent.is_dir():
            raise ValueError(f"{self.agent} config folder not found at {self.file.parent}; install {self.agent} first")
        cfg = _read_json(self.file, {})
        _backup(self.file)
        cfg["shelldeck"] = self.render()
        _write(self.file, json.dumps(cfg, indent=2) + "\n")
        return {"file": str(self.file)}

    def uninstall(self) -> dict:
        if self.file.exists():
            cfg = _read_json(self.file, {})
            if cfg.pop("shelldeck", None) is not None:
                _write(self.file, json.dumps(cfg, indent=2) + "\n")
        return {"file": str(self.file)}

    def state(self) -> tuple[bool, bool]:
        try:
            block = _read_json(self.file, {}).get("shelldeck")
        except ValueError:
            return False, False
        return block is not None, block == self.render()


KIMI_BEGIN, KIMI_END = "# >>> shelldeck kimi integration", "# <<< shelldeck kimi integration"
# (event, matcher, state) as herdr installs them: AskUserQuestion is the agent asking you
KIMI_HOOKS = [("SessionStart", None, "session"), ("UserPromptSubmit", None, "working"),
              ("PreToolUse", "^(?!AskUserQuestion$).*$", "working"), ("PreToolUse", "^AskUserQuestion$", "blocked"),
              ("PostToolUse", "^AskUserQuestion$", "working"), ("PostToolUseFailure", "^AskUserQuestion$", "working"),
              ("SubagentStart", None, "working"), ("PreCompact", None, "working"), ("PermissionRequest", None, "blocked"),
              ("PermissionResult", None, "working"), ("Stop", None, "done"), ("Interrupt", None, "idle")]


def strip_kimi_block(text: str) -> str:
    out, inside = [], False
    for line in text.splitlines():
        if line.strip() == KIMI_BEGIN:
            inside = True
        elif line.strip() == KIMI_END and inside:
            inside = False
        elif not inside:
            out.append(line)
    body = "\n".join(out).rstrip("\n")
    return body + "\n" if body else ""


def kimi_block() -> str:
    tables = "".join(f"[[hooks]]\nevent = {json.dumps(e)}\n" + (f"matcher = {json.dumps(m)}\n" if m else "")
                     + f"command = {json.dumps(hook_command('kimi', st))}\ntimeout = 10\n\n" for e, m, st in KIMI_HOOKS)
    return f"{KIMI_BEGIN}\n{tables}{KIMI_END}\n"


class KimiToml:
    """A marked `[[hooks]]` block at the end of Kimi Code's config.toml (needs Kimi 0.14+, as herdr notes)."""

    agent = "kimi"

    @property
    def file(self) -> Path:
        return _home("KIMI_CODE_HOME", ".kimi-code") / "config.toml"

    def install(self) -> dict:
        if not self.file.parent.is_dir():
            raise ValueError(f"kimi config folder not found at {self.file.parent}; install kimi first")
        text = self.file.read_text(encoding="utf-8") if self.file.exists() else ""
        rest = strip_kimi_block(text)
        new = (rest + "\n" if rest else "") + kimi_block()
        if new != text:
            _backup(self.file)
            _write(self.file, new)
        return {"file": str(self.file)}

    def uninstall(self) -> dict:
        if self.file.exists():
            text = self.file.read_text(encoding="utf-8")
            if KIMI_BEGIN in text:
                _write(self.file, strip_kimi_block(text))
        return {"file": str(self.file)}

    def state(self) -> tuple[bool, bool]:
        text = self.file.read_text(encoding="utf-8") if self.file.exists() else ""
        if KIMI_BEGIN not in text:
            return False, False
        return True, text.endswith(kimi_block())


HERMES_PLUGIN = "shelldeck-agent-state"
HERMES_INIT = '''"""Hermes plugin installed by shelldeck (`sd integration install hermes`): reports the session id to the
shelldeck terminal it runs in, so the conversation can be resumed. Outside shelldeck it does nothing."""

# shelldeck-hermes-plugin v1
import json
import os
import urllib.request

_INTERACTIVE = {"cli", "tui", "desktop", "acp"}


def _report(**kwargs):
    token = os.environ.get("SHELLDECK_AGENT_REPORT_TOKEN")
    sid = kwargs.get("session_id")
    if not token or kwargs.get("platform") not in _INTERACTIVE or not isinstance(sid, str) or not sid or sid.startswith("-"):
        return
    body = {"source": "integration:hermes", "agent": "hermes", "agent_session_id": sid, "resume_argv": ["hermes", "--resume", sid]}
    req = urllib.request.Request("http://127.0.0.1:" + os.environ.get("SHELLDECK_PORT", "5455") + "/api/agent-reports",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Shelldeck-Report-Token": token})
    try:
        urllib.request.urlopen(req, timeout=1).read()
    except Exception:
        pass


def _observed(**kwargs):
    if kwargs.get("platform") == "cli":
        _report(**kwargs)


def register(ctx):
    ctx.register_hook("on_session_start", _report)
    ctx.register_hook("on_session_reset", _report)
    ctx.register_hook("pre_llm_call", _observed)
'''
HERMES_MANIFEST = f"name: {HERMES_PLUGIN}\nversion: \"1.0\"\ndescription: Report the Hermes session id to shelldeck\n"


def hermes_enable(text: str, on: bool) -> str:
    """Add/remove the plugin in config.yaml's `plugins.enabled` block list. Only simple layouts are edited;
    anything else raises ValueError so the file is left alone (herdr edits the same key)."""
    item = f"    - {HERMES_PLUGIN}"
    lines = text.splitlines()
    top = [i for i, line in enumerate(lines) if line.startswith("plugins:")]
    if not top:
        if not on:
            return text
        body = text.rstrip("\n")
        return (body + "\n" if body else "") + f"plugins:\n  enabled:\n{item}\n"
    i = top[0]
    if lines[i].strip() != "plugins:":
        raise ValueError("config.yaml has an inline `plugins:` value; add shelldeck-agent-state to plugins.enabled by hand")
    end = next((j for j in range(i + 1, len(lines)) if lines[j] and not lines[j].startswith((" ", "#"))), len(lines))
    en = next((j for j in range(i + 1, end) if lines[j].rstrip() == "  enabled:"), None)
    if on:
        if any(line.rstrip() == item for line in lines[i:end]):
            return text
        if en is None:
            if any(lines[j].startswith("  enabled") for j in range(i + 1, end)):
                raise ValueError("config.yaml lists plugins.enabled inline; add shelldeck-agent-state to it by hand")
            lines[i + 1:i + 1] = ["  enabled:", item]
        else:
            lines.insert(en + 1, item)
    else:
        lines = [line for j, line in enumerate(lines) if not (i < j < end and line.rstrip() == item)]
        k = next((j for j, line in enumerate(lines) if line.rstrip() == "  enabled:"), None)
        # drop an `enabled:` / `plugins:` we left empty
        if k is not None and (k + 1 >= len(lines) or not lines[k + 1].startswith("    ")):
            del lines[k]
            p = lines.index("plugins:") if "plugins:" in lines else None
            if p is not None and (p + 1 >= len(lines) or not lines[p + 1].startswith(" ")):
                del lines[p]
    out = "\n".join(lines)
    return out + "\n" if out else ""


class HermesPlugin:
    agent = "hermes"

    @property
    def root(self) -> Path:
        if os.environ.get("HERMES_HOME"):
            return Path(os.environ["HERMES_HOME"])
        if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
            return Path(os.environ["LOCALAPPDATA"]) / "hermes"
        return Path.home() / ".hermes"

    @property
    def file(self) -> Path:
        return self.root / "plugins" / HERMES_PLUGIN / "__init__.py"

    def install(self) -> dict:
        if not self.root.is_dir():
            raise ValueError(f"hermes config folder not found at {self.root}; install hermes first")
        cfg = self.root / "config.yaml"
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        new = hermes_enable(text, True)  # raises before anything is written
        _write(self.file, HERMES_INIT)
        _write(self.file.with_name("plugin.yaml"), HERMES_MANIFEST)
        if new != text:
            _backup(cfg)
            _write(cfg, new)
        return {"file": str(self.file)}

    def uninstall(self) -> dict:
        cfg = self.root / "config.yaml"
        if cfg.exists():
            text = cfg.read_text(encoding="utf-8")
            try:
                new = hermes_enable(text, False)
            except ValueError:
                new = text
            if new != text:
                _write(cfg, new)
        shutil.rmtree(self.file.parent, ignore_errors=True)
        return {"file": str(self.file)}

    def state(self) -> tuple[bool, bool]:
        if not self.file.exists():
            return False, False
        cfg = self.root / "config.yaml"
        enabled = cfg.exists() and f"    - {HERMES_PLUGIN}" in cfg.read_text(encoding="utf-8").splitlines()
        return True, enabled and self.file.read_text(encoding="utf-8") == HERMES_INIT


PI_EXTENSION = """// shelldeck-{agent}-extension v1
// Reports {agent}'s lifecycle to the shelldeck terminal it runs in (`sd integration install {agent}`).
// Event names follow herdr's {agent} extension. Outside a shelldeck terminal it does nothing.
export default function (pi) {{
  const env = (globalThis.process && process.env) || {{}}
  const token = env.SHELLDECK_AGENT_REPORT_TOKEN
  if (!token) return
  const url = "http://127.0.0.1:" + (env.SHELLDECK_PORT || "5455") + "/api/agent-reports"
  let root = false
  let sessionId
  const sessionOf = (ctx) => {{
    try {{ const id = ctx?.sessionManager?.getSessionId?.(); if (typeof id === "string" && id && !id.startsWith("-")) sessionId = id }} catch {{}}
  }}
  const send = (state, reason) => {{
    try {{
      const body = {{ source: "integration:{agent}", agent: "{agent}", state, blocked_reason: reason || null,
        ttl_ms: state === "working" ? 60000 : 120000 }}
      if (sessionId) {{ body.agent_session_id = sessionId; body.resume_argv = {resume} }}
      fetch(url, {{ method: "POST", headers: {{ "content-type": "application/json", "x-shelldeck-report-token": token }},
        body: JSON.stringify(body), signal: AbortSignal.timeout(1500) }}).catch(() => {{}})
    }} catch {{}}
  }}
  const start = (ctx) => {{ if ({gate}) {{ root = true; sessionOf(ctx) }} return root }}
  pi.on("session_start", (_e, ctx) => {{ if (start(ctx)) send(ctx?.isIdle?.() === false ? "working" : "idle") }})
  pi.on("agent_start", (_e, ctx) => {{ if (root || start(ctx)) {{ sessionOf(ctx); send("working") }} }})
{extra}}}
"""
PI_EXTRA = {
    "pi": '''  pi.on("agent_settled", (_e, ctx) => { if (root && ctx?.isIdle?.() === true) send("done") })
''',
    "omp": '''  pi.on("session_switch", (_e, ctx) => { if (start(ctx)) send("idle") })
  pi.on("tool_approval_requested", (_e, ctx) => { if (root || start(ctx)) send("blocked", "approval") })
  pi.on("tool_approval_resolved", () => { if (root) send("working") })
  pi.on("tool_execution_start", (e) => { if (root && e?.toolName === "ask") send("blocked", "question") })
  pi.on("tool_execution_end", (e) => { if (root && e?.toolName === "ask") send("working") })
  pi.on("agent_end", (e) => { if (root && e?.willContinue !== true) send("done") })
''',
}


def _pi_extension(agent: str) -> str:
    resume = '["pi", "--session", sessionId]' if agent == "pi" else '["omp", "--resume=" + sessionId]'
    gate = 'ctx?.mode === "tui"' if agent == "pi" else "ctx?.hasUI === true"  # interactive sessions only, as herdr gates them
    return PI_EXTENSION.format(agent=agent, resume=resume, gate=gate, extra=PI_EXTRA[agent])


def _pi_dir() -> Path:
    return (Path(os.environ["PI_CODING_AGENT_DIR"]) if os.environ.get("PI_CODING_AGENT_DIR") else Path.home() / ".pi" / "agent") / "extensions"


def _omp_dir() -> Path:
    if os.environ.get("PI_CODING_AGENT_DIR"):
        return Path(os.environ["PI_CODING_AGENT_DIR"]) / "extensions"
    return Path.home() / (os.environ.get("PI_CONFIG_DIR") or ".omp") / "agent" / "extensions"


class Extension(OwnFile):
    """A Pi/OMP extension file; installing needs the agent's own folder (the extensions dir's parent)."""

    def install(self) -> dict:
        if not self.file.parent.parent.is_dir():
            raise ValueError(f"{self.agent} config folder not found at {self.file.parent.parent}; install {self.agent} first")
        if self.agent == "omp" and _omp_dir() == _pi_dir():
            raise ValueError("pi and omp use the same extensions folder; set separate agent folders first")
        self.file.parent.mkdir(exist_ok=True)
        return super().install()


def _copilot_file() -> str:
    hooks = {e: [{"type": "command", "command": hook_command("copilot", e), "timeoutSec": 5}] for e in hook.EVENTS["copilot"]}
    return json.dumps({"version": 1, "hooks": hooks}, indent=2) + "\n"


OPENCODE_PLUGIN = """// {marker} v{version}
// Reports {agent}'s lifecycle (working / needs approval / done) to the shelldeck terminal it runs in.
// Installed by `sd integration install {agent}`; `sd integration uninstall {agent}` removes it.
// Outside a shelldeck terminal it does nothing, and nothing here changes what {agent} does.
export const ShelldeckPlugin = async () => {{
  const env = (globalThis.process && process.env) || {{}}
  const token = env.SHELLDECK_AGENT_REPORT_TOKEN
  const url = "http://127.0.0.1:" + (env.SHELLDECK_PORT || "5455") + "/api/agent-reports"
  const send = (state, sessionID, reason) => {{
    if (!token) return
    try {{
      const body = {{ source: "integration:{agent}", agent: "{agent}", state, blocked_reason: reason || null,
        ttl_ms: state === "working" ? 60000 : 120000 }}
      if (typeof sessionID === "string" && sessionID) {{
        body.agent_session_id = sessionID
        body.resume_argv = ["{agent}", "--session", sessionID]
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


def _opencode_plugin(agent: str = "opencode") -> str:
    """OpenCode's plugin; Kilo Code is an OpenCode fork with the same plugin events (herdr ships one asset shape for both)."""
    return OPENCODE_PLUGIN.format(marker=OPENCODE_MARKER if agent == "opencode" else f"shelldeck-{agent}-plugin", version=PLUGIN_VERSION, agent=agent)


def _xdg() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) if os.environ.get("XDG_CONFIG_HOME") else Path.home() / ".config"


def _flat_entry(agent: str, timeout: int):
    return lambda e: {"type": "command", "command": hook_command(agent), "timeout": timeout, "description": "Report agent state to shelldeck"}


def _grok_file() -> str:
    return json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": hook_command("grok"), "timeout": 10}]}]}}, indent=2) + "\n"


def _antigravity_block() -> dict:
    return {e: [{"type": "command", "command": hook_command("antigravity"), "timeout": 10}] for e in hook.EVENTS["antigravity"]}


INSTALLERS = {
    "claude": JsonHooks("claude", lambda: _home("CLAUDE_CONFIG_DIR", ".claude") / "settings.json",
                        lambda e: {"hooks": [{"type": "command", "command": hook_command("claude"), "timeout": 5, "async": True}]}),
    "gemini": JsonHooks("gemini", lambda: _home("GEMINI_DIR", ".gemini") / "settings.json",
                        lambda e: {"matcher": "*", "hooks": [{"name": "shelldeck", "type": "command", "command": hook_command("gemini"), "timeout": 5000}]}),
    "cursor": JsonHooks("cursor", lambda: _home("CURSOR_DIR", ".cursor") / "hooks.json",
                        lambda e: {"command": hook_command("cursor"), "timeout": 5}, top={"version": 1}),
    # herdr's formats (src/integration/targets.rs); Qwen/Qoder/Droid/Devin report the session id only
    "codex": CodexHooks("codex", lambda: _home("CODEX_HOME", ".codex") / "hooks.json", _cmd_entry("codex", 10)),
    "qwen": JsonHooks("qwen", lambda: _home("QWEN_HOME", ".qwen") / "settings.json", _cmd_entry("qwen", 10_000, "*")),
    "qodercli": JsonHooks("qodercli", lambda: _home("QODER_CONFIG_DIR", ".qoder") / "settings.json", _cmd_entry("qodercli", 10, "*")),
    "droid": JsonHooks("droid", lambda: Path.home() / ".factory" / "settings.json", _cmd_entry("droid", 10)),
    "devin": JsonHooks("devin", lambda: _devin_dir() / "config.json", _cmd_entry("devin", 10)),
    "mastracode": JsonHooks("mastracode", lambda: Path.home() / ".mastracode" / "hooks.json", _flat_entry("mastracode", 10_000), key=None),
    "kimi": KimiToml(),
    "letta": JsonHooks("letta", lambda: Path.home() / ".letta" / "settings.json",
                       lambda e: {"hooks": [{"type": "command", "command": hook_command("letta"), "timeout": 10_000, "quiet": True}]}),
    "hermes": HermesPlugin(),
    "pi": Extension("pi", lambda: _pi_dir() / "shelldeck-agent-state.ts", lambda: _pi_extension("pi"), "shelldeck-pi-extension"),
    "omp": Extension("omp", lambda: _omp_dir() / "shelldeck-omp-agent-state.ts", lambda: _pi_extension("omp"), "shelldeck-omp-extension"),
    "antigravity": NamedBlock("antigravity", lambda: _home("ANTIGRAVITY_CLI_CONFIG_DIR", ".gemini", "config") / "hooks.json", _antigravity_block),
    "grok": OwnFile("grok", lambda: _home("GROK_HOME", ".grok") / "hooks" / "shelldeck.json", _grok_file, "-m shelldeck.hook grok"),
    "kilo": OwnFile("kilo", lambda: _xdg() / "kilo" / "plugin" / "shelldeck.js", lambda: _opencode_plugin("kilo"), "shelldeck-kilo-plugin"),
    "copilot": OwnFile("copilot", lambda: _home("COPILOT_HOME", ".copilot") / "hooks" / "shelldeck.json", _copilot_file, "-m shelldeck.hook copilot"),
    "opencode": OwnFile("opencode", lambda: _xdg() / "opencode" / "plugins" / "shelldeck.js", _opencode_plugin, OPENCODE_MARKER),
}


def _root(inst) -> Path:
    """The agent's own config folder (installing needs it to exist)."""
    f = inst.file
    if isinstance(inst, HermesPlugin):
        return inst.root
    return f.parent.parent if f.parent.name in ("hooks", "plugins", "plugin", "extensions") else f.parent


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
            "file": str(inst.file), "available": _root(inst).is_dir()}


def install(agent: str) -> dict:
    if agent not in INSTALLERS:
        raise KeyError(agent)
    return {**INSTALLERS[agent].install(), **status(agent)}


def uninstall(agent: str) -> dict:
    if agent not in INSTALLERS:
        raise KeyError(agent)
    return {**INSTALLERS[agent].uninstall(), **status(agent)}
