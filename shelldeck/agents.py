"""AI coding agents: which CLIs exist, which one runs in a terminal, and with what model."""

import json
import re
import shutil
import tomllib
from pathlib import Path

import psutil

# key: (label, executable names, cmdline markers for node/python/bun installs, fallback models)
# Codex and opencode models come from their own caches (models()); the rest are static fallbacks.
# ponytail: static lists go stale; the --model flag or the agent's config still shows the real one
AGENTS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "claude": ("Claude Code", ("claude",), ("@anthropic-ai/claude-code",), ("fable", "opus", "sonnet", "haiku", "claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5")),
    "codex": ("Codex", ("codex",), ("@openai/codex",), ()),
    "gemini": ("Gemini CLI", ("gemini",), ("@google/gemini-cli",), ("gemini-2.5-pro", "gemini-2.5-flash")),
    "devin": ("Devin CLI", ("devin",), (), ()),
    "copilot": ("Copilot CLI", ("copilot",), ("@github/copilot",), ()),
    "cursor": ("Cursor Agent", ("cursor-agent",), (), ()),
    "opencode": ("opencode", ("opencode",), ("opencode-ai",), ()),
    "aider": ("Aider", ("aider",), ("aider",), ()),
    "amp": ("Amp", ("amp",), ("@sourcegraph/amp",), ()),
    "qwen": ("Qwen Code", ("qwen",), ("@qwen-code/qwen-code",), ()),
    "goose": ("Goose", ("goose",), (), ()),
    "droid": ("Factory Droid", ("droid",), (), ()),
    "crush": ("Crush", ("crush",), (), ()),
    "kiro": ("Kiro CLI", ("kiro-cli",), (), ()),
}
INTERPRETERS = {"node", "bun", "deno", "python", "python3", "pythonw", "py"}
_EXE = {name: key for key, a in AGENTS.items() for name in a[1]}
_seen: dict[int, tuple[str, str | None] | None] = {}  # pid -> (agent, model); a cmdline never changes
MODEL_FLAG = re.compile(r"^--?(?:m|model)(?:=(.+))?$")


def _flag_model(cmd: list[str]) -> str | None:
    for i, arg in enumerate(cmd):
        if m := MODEL_FLAG.match(arg):
            return m.group(1) or (cmd[i + 1] if i + 1 < len(cmd) else None)
    return None


def identify(p: psutil.Process) -> tuple[str, str | None] | None:
    """(agent key, model from --model or its config) when `p` is an agent CLI, else None."""
    if p.pid in _seen:
        return _seen[p.pid]
    name = p.name().lower().removesuffix(".exe")
    key, cmd = _EXE.get(name), None
    if key or name in INTERPRETERS or name.startswith("python"):  # python3.12 on Linux
        try:
            cmd = p.cmdline()
        except psutil.Error:
            cmd = []
        if not key:
            joined = " ".join(cmd).replace("\\", "/")
            key = next((k for k, a in AGENTS.items() if any(m in joined for m in a[2])), None)
            # `python -m aider` / `node .../bin/codex`: last path part of the script
            script = next((i for i, a in enumerate(cmd) if i and not a.startswith("-")), None)
            if not key and script:
                key = _EXE.get(Path(cmd[script]).stem.lower())
            cmd = cmd[script + 1:] if script else []  # `python -m aider`: -m is not a model flag
    hit = (key, _flag_model(cmd or []) or default_model(key)) if key else None
    _seen[p.pid] = hit
    return hit


def running(shells: dict[str, int]) -> dict[str, tuple[str, str | None]]:
    """session id -> (agent, model) for terminals (shell pid) with an agent running under them."""
    out = {}
    for sid, pid in shells.items():
        try:
            kids = psutil.Process(pid).children(recursive=True)
        except psutil.Error:
            continue
        for k in kids:
            try:
                if hit := identify(k):
                    out[sid] = hit
                    break
            except psutil.Error:
                continue
    return out


def forget(alive: set[int]) -> None:
    for pid in [p for p in _seen if p not in alive]:
        del _seen[pid]


def default_model(key: str) -> str | None:
    """The model an agent's own config picks when no --model flag is given."""
    home = Path.home()
    try:
        if key == "claude":
            return _json(home / ".claude" / "settings.json").get("model")
        if key == "codex":
            return tomllib.loads((home / ".codex" / "config.toml").read_text(encoding="utf-8")).get("model")
        if key == "gemini":
            return (_json(home / ".gemini" / "settings.json").get("model") or {}).get("name")
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def models(key: str) -> list[str]:
    """Models an agent offers: its own cache when it keeps one, else the static fallback."""
    home = Path.home()
    try:
        if key == "codex":  # refreshed by codex itself from OpenAI
            return [m["slug"] for m in _json(home / ".codex" / "models_cache.json")["models"] if m.get("visibility") == "list"]
        if key == "opencode":  # the models.dev catalog; only opencode's own provider and signed-in ones
            catalog, auth = _json(home / ".cache" / "opencode" / "models.json"), home / ".local" / "share" / "opencode" / "auth.json"
            providers = ["opencode", *(_json(auth) if auth.exists() else {})]
            return [f"{p}/{m}" for p in providers for m in catalog.get(p, {}).get("models", {})]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return list(AGENTS[key][3])


def catalog() -> list[dict]:
    """Every known agent, whether its CLI is on PATH, and the models it offers."""
    out = []
    for key, (label, exes, _, _) in AGENTS.items():
        path = next((p for e in exes if (p := shutil.which(e))), None)
        out.append({"key": key, "label": label, "command": exes[0], "installed": bool(path), "path": path,
                    "models": models(key) if path else list(AGENTS[key][3]), "default_model": default_model(key) if path else None})
    return out


if __name__ == "__main__":
    assert _flag_model(["claude", "--model", "opus"]) == "opus"
    assert _flag_model(["codex", "-m", "gpt-5-codex"]) == "gpt-5-codex"
    assert _flag_model(["x", "--model=sonnet"]) == "sonnet"
    assert _flag_model(["x", "--mode", "y"]) is None
    print("ok")
