"""Plugins: installed Python packages that add commands and event handlers.

A package points an entry point in the `shelldeck.plugins` group at a `Plugin`:

    [project.entry-points."shelldeck.plugins"]
    docker = "shelldeck_docker:plugin"

    from shelldeck.plugins import Plugin
    plugin = Plugin("docker", "1.0.0")

    @plugin.command("ps", "Running containers")
    def ps(args, ctx):              # ctx: session_id, cwd, shell, project
        return {"input": "docker ps " + " ".join(args)}  # typed at the prompt, never run; a str is shown instead

    @plugin.on("agent.")            # every event whose type starts with this (server `_emit` types)
    def changed(event): ...

The command is then `?docker ps` in a terminal and `sd docker ps` in the CLI. Plugins are trusted code running
inside the server, like any package you install; `sd plugin disable <name>` turns one off.
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
        self.commands: dict[str, tuple[str, Callable]] = {}
        self.handlers: list[tuple[str, Callable]] = []

    def command(self, name: str, description: str = ""):
        """Decorator: `handler(args: list[str], ctx: dict) -> str | {"text"} | {"input"} | None`."""
        if not NAME.fullmatch(name):
            raise ValueError(f"command name must be lowercase letters, digits and dashes: {name!r}")

        def add(fn):
            self.commands[name] = (description, fn)
            return fn
        return add

    def on(self, prefix: str):
        """Decorator: `handler(event: {"type", "data"})` for events whose type starts with `prefix`."""
        def add(fn):
            self.handlers.append((prefix, fn))
            return fn
        return add


loaded: dict[str, Plugin] = {}
found: dict[str, str] = {}  # entry point name -> "loaded" | "disabled" | the load error


def load(disabled: set[str] = frozenset()) -> None:
    """Import every installed plugin except the disabled ones. A broken plugin is reported, never fatal."""
    loaded.clear()
    found.clear()
    for ep in entry_points(group=GROUP):
        if ep.name in disabled:
            found[ep.name] = "disabled"
            continue
        try:
            p = ep.load()
            if not isinstance(p, Plugin) or not NAME.fullmatch(p.name):
                raise TypeError("the entry point must be a shelldeck.plugins.Plugin with a lowercase name")
            if p.name in loaded:
                raise ValueError(f"another plugin already uses the name {p.name}")
            loaded[p.name] = p
            found[ep.name] = "loaded"
        except Exception as e:  # noqa: BLE001 - third-party import can raise anything
            found[ep.name] = f"{type(e).__name__}: {e}"
            log.warning("plugin %s failed to load", ep.name, exc_info=True)


def commands() -> list[dict]:
    return [{"name": f"{p.name} {c}", "plugin": p.name, "description": d}
            for p in loaded.values() for c, (d, _) in sorted(p.commands.items())]


def run(words: list[str], ctx: dict) -> dict:
    """`["docker", "ps", "-a"]` -> the handler's result as {"text"} or {"input"} (one printable line)."""
    from .smart_recall import command_line

    p = loaded.get(words[0]) if words else None
    if not p or len(words) < 2 or words[1] not in p.commands:
        raise KeyError(" ".join(words[:2]))
    out = p.commands[words[1]][1](words[2:], ctx)
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
                    fn({"type": type_, "data": data})
                except Exception:  # noqa: BLE001 - one plugin's bug must not break the others
                    log.warning("plugin %s failed on %s", p.name, type_, exc_info=True)
