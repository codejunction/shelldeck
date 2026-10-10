"""Plugins: installed Python packages that add commands and event handlers.

A package points an entry point in the `shelldeck.plugins` group at a `Plugin` of the same name:

    [project.entry-points."shelldeck.plugins"]
    docker = "shelldeck_docker:plugin"

    from shelldeck.plugins import Plugin
    plugin = Plugin("docker", "1.0.0")

    @plugin.command("logs", "Follow a container's logs", usage="<container>")
    def logs(args, ctx):            # args: the words after the command; ctx: session_id, cwd, shell, project
        return {"input": "docker logs -f " + " ".join(args)}  # typed at the prompt, never run; a str is shown instead

    @plugin.on("agent.")            # every event whose type starts with this (server `_emit` types)
    def changed(event): ...

The command is then `?docker logs web` in a terminal and `sd docker logs web` in the CLI. See docs/plugins.md. Plugins are trusted code running
inside the server, like any package you install; `sd plugin disable <name>` (or the Plugins page) turns one off.
"""

import logging
import re
from collections.abc import Callable
from importlib.metadata import entry_points

log = logging.getLogger("shelldeck")
NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
GROUP = "shelldeck.plugins"


class Plugin:
    def __init__(self, name: str, version: str = ""):
        self.name, self.version = name, version
        self.commands: dict[str, tuple[str, Callable, str]] = {}  # name -> (description, handler, usage)
        self.handlers: list[tuple[str, Callable]] = []

    def command(self, name: str, description: str = "", usage: str = ""):
        """Decorator: `handler(args: list[str], ctx: dict) -> str | {"text"} | {"input"} | None`. `usage` names the
        arguments (`"<container> [--tail N]"`); with it the UI asks for them before running."""
        if not NAME.fullmatch(name):
            raise ValueError(f"command name must be lowercase letters, digits and dashes: {name!r}")

        def add(fn):
            self.commands[name] = (description, fn, usage)
            return fn
        return add

    def on(self, prefix: str):
        """Decorator: `handler(event: {"type", "data"})` for events whose type starts with `prefix`."""
        def add(fn):
            self.handlers.append((prefix, fn))
            return fn
        return add


class PluginError(Exception):
    """A plugin's own failure (including sys.exit), so it can never stop the server."""


def _call(fn, *args):
    try:
        return fn(*args)
    except KeyboardInterrupt:
        raise
    except BaseException as e:  # noqa: BLE001 - SystemExit from plugin code must not end the server
        raise PluginError(f"{type(e).__name__}: {e}") from e


loaded: dict[str, Plugin] = {}
found: dict[str, dict] = {}  # entry point name -> {status: loaded|disabled|error, error, version, summary, package}


def load(disabled: set[str] = frozenset()) -> None:
    """Import every installed plugin except the disabled ones. A broken plugin is reported, never fatal."""
    loaded.clear()
    found.clear()
    for ep in entry_points(group=GROUP):
        meta = getattr(ep, "dist", None)
        info = {"status": "disabled", "error": "", "version": meta.version if meta else "",
                "summary": (meta.metadata.get("Summary") or "") if meta else "", "package": meta.name if meta else ""}
        found[ep.name] = info
        if ep.name in disabled:
            continue
        try:
            p = _call(ep.load)
            if not isinstance(p, Plugin) or p.name != ep.name:
                raise PluginError(f"the entry point must be a shelldeck.plugins.Plugin named {ep.name!r}")
            if not NAME.fullmatch(p.name):
                raise PluginError("a plugin name is lowercase letters, digits and dashes")
            loaded[p.name] = p
            info.update(status="loaded", version=p.version or info["version"])
        except PluginError as e:
            info.update(status="error", error=str(e))
            log.warning("plugin %s failed to load: %s", ep.name, e)


def listing() -> list[dict]:
    """Every installed plugin with its status, commands and the events it listens to."""
    out = []
    for name, info in sorted(found.items()):
        p = loaded.get(name)
        out.append({"name": name, **info,
                    "commands": [{"name": f"{name} {c}", "description": d, "usage": u} for c, (d, _, u) in sorted(p.commands.items())] if p else [],
                    "events": sorted({prefix for prefix, _ in p.handlers}) if p else []})
    return out


def commands() -> list[dict]:
    return [{"name": f"{p.name} {c}", "plugin": p.name, "description": d, "usage": u}
            for p in loaded.values() for c, (d, _, u) in sorted(p.commands.items())]


def run(words: list[str], ctx: dict) -> dict:
    """`["docker", "ps", "-a"]` -> the handler's result as {"text"} or {"input"} (one printable line).
    KeyError: no such command; PluginError: the plugin failed."""
    from .smart_recall import command_line

    p = loaded.get(words[0]) if words else None
    if not p or len(words) < 2 or words[1] not in p.commands:
        raise KeyError(" ".join(words[:2]))
    out = _call(p.commands[words[1]][1], words[2:], ctx)
    if isinstance(out, dict) and "input" in out:
        return {"input": command_line(str(out["input"]))}  # typed into a terminal: no CR or escapes
    if isinstance(out, dict):
        out = out.get("text")
    return {"text": "" if out is None else str(out)}


def emit(type_: str, data: dict) -> None:
    for p in list(loaded.values()):
        for prefix, fn in p.handlers:
            if type_.startswith(prefix):
                try:
                    _call(fn, {"type": type_, "data": data})
                except PluginError as e:  # one plugin's bug must not break the others
                    log.warning("plugin %s failed on %s: %s", p.name, type_, e)
