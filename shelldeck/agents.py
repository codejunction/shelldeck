"""AI coding agents: which CLIs exist, which one runs in a terminal, and with what model."""

import json
import os
import re
import shutil
import sqlite3
import tomllib
from contextlib import closing
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
    # found, never launched: a devin(.exe) outside Devin's cli/ install folder (see identify)
    "devin_desktop": ("Devin desktop", (), (), ()),
}
# Approval menus and questions of Claude Code, Codex and Devin (strings from their binaries), plus [y/n].
# Keep in sync with QUESTION_RE in static/app.js.
QUESTION = re.compile(
    r"do you want to (?:proceed|make this edit|create|run|allow)|would you like to (?:proceed|run|make|grant|continue)"
    r"|yes, allow once|yes, and don't ask|allow (?:once|for this session)|do you trust the files|yes, i trust"
    r"|enter to (?:select|confirm|approve)|plan needs changes|\[y/n\]|\(y/n\)",
    re.I,
)
# helper processes of an agent, not an agent session: Claude's browser bridge, Electron children
HELPER_ARGS = ("--chrome-native-host", "--type=")
INTERPRETERS = {"node", "bun", "deno", "python", "python3", "pythonw", "py"}
_EXE = {name: key for key, a in AGENTS.items() for name in a[1]}
_seen: dict[int, tuple[str, str | None] | None] = {}  # pid -> (agent, model); a cmdline never changes
MODEL_FLAG = re.compile(r"^--?(?:m|model)(?:=(.+))?$")


def _flag_model(cmd: list[str]) -> str | None:
    for i, arg in enumerate(cmd):
        if m := MODEL_FLAG.match(arg):
            return m.group(1) or (cmd[i + 1] if i + 1 < len(cmd) else None)
    return None


def _exe_parts(p: psutil.Process) -> tuple[str, ...]:
    try:
        return tuple(x.lower() for x in Path(p.exe()).parts)
    except (psutil.Error, OSError):
        return ("cli",)  # unknown: assume the CLI


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
        if any(a.startswith(HELPER_ARGS) for a in cmd):
            key, cmd = None, []
        elif key == "devin" and "cli" not in (_exe_parts(p)):
            key = "devin_desktop"  # ponytail: path heuristic, unverified (no desktop install to test on)
        elif not key:
            joined = " ".join(cmd).replace("\\", "/")
            key = next((k for k, a in AGENTS.items() if any(m in joined for m in a[2])), None)
            # `python -m aider` / `node .../bin/codex`: last path part of the script
            script = next((i for i, a in enumerate(cmd) if i and not a.startswith("-")), None)
            if not key and script:
                key = _EXE.get(Path(cmd[script]).stem.lower())
            cmd = cmd[script + 1:] if script else []  # `python -m aider`: -m is not a model flag
    hit = (key, _flag_model(cmd or []) or _env_model(p, key) or _resumed_model(key, cmd or []) or default_model(key)) if key else None
    _seen[p.pid] = hit
    return hit


MODEL_ENV = {"claude": "ANTHROPIC_MODEL", "devin": "DEVIN_MODEL", "gemini": "GEMINI_MODEL"}


def _env_model(p: psutil.Process, key: str) -> str | None:
    """The model an env var picks for this process (e.g. `$env:DEVIN_MODEL = 'swe-1.6'; devin`)."""
    if var := MODEL_ENV.get(key):
        try:
            return p.environ().get(var) or None
        except psutil.Error:
            pass
    return None


def _varint(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return n, i


def _resumed_model(key: str, cmd: list[str]) -> str | None:
    """`devin -r <id>` keeps the model of that session, stored in Devin's session store."""
    if key != "devin":
        return None
    sid = next((cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a in ("-r", "--resume")), None)
    return next((d["model"] for d in devin_sessions(500) if d["id"] == sid), None) if sid else None


def _proto(b: bytes):
    """(field, value) pairs of a protobuf message; length-delimited values stay bytes."""
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        wire = key & 7
        if wire == 0:
            v, i = _varint(b, i)
        elif wire == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif wire in (1, 5):
            n = 8 if wire == 1 else 4
            v, i = b[i:i + n], i + n
        else:
            raise ValueError(f"wire type {wire}")
        yield key >> 3, v


def devin_models(cache: Path) -> list[str]:
    """Names `devin --model` accepts, from the model_configs.bin Devin keeps next to its bin/ folder.

    Each entry (field 1) has a display name (1, "Claude Opus 4.7 Medium") and a base model (30.1,
    "Claude Opus 4.7"); the CLI takes the base name lowercased and dashed: claude-opus-4.7.
    ponytail: reverse-engineered format; on a parse error the page just shows no models."""
    names = []
    for field, entry in _proto(cache.read_bytes()):
        if field != 1:
            continue
        parts = dict(_proto(entry))
        base = dict(_proto(parts[30])).get(1) if 30 in parts else None
        name = (base or parts.get(1) or b"").decode("utf-8").strip().lower().replace(" ", "-")
        if name and name not in names:
            names.append(name)
    return sorted(names)


def _devin_windows() -> dict[str, int]:
    """Devin model id (sessions.model, e.g. swe-1-6) -> context window, from field 23.4 of each entry."""
    exe = shutil.which("devin")
    cache = Path(exe).resolve().parent.parent / "model_configs.bin" if exe else None
    if not cache or not cache.exists():
        return {}
    key = cache.stat().st_mtime
    if _cache.get("devin_windows", (None,))[0] != key:
        out = {}
        for field, entry in _proto(cache.read_bytes()):
            parts = dict(_proto(entry)) if field == 1 else {}
            if 22 in parts and 23 in parts and isinstance(size := dict(_proto(parts[23])).get(4), int):
                out[parts[22].decode("utf-8", "replace")] = size
        _cache["devin_windows"] = (key, out)
    return _cache["devin_windows"][1]


_cache: dict[str, tuple] = {}
_files: dict[tuple[int, str], Path] = {}  # (agent pid, agent) -> the session log it writes


def _tail_lines(path: Path, size: int = 512 * 1024) -> list[str]:
    """Last lines of a JSONL log, newest first (the first may be cut, so JSON errors are skipped)."""
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - size))
        return f.read().decode("utf-8", "replace").splitlines()[::-1]


# Claude logs no window size. 1M models per Devin's model catalog (Opus 4.7/4.8, every 5.x); the rest 200k.
# ponytail: static rule, marked estimated in the UI; update when new models ship
CLAUDE_1M = re.compile(r"claude-(?:opus-4-[78]|(?:opus|sonnet|fable)-5)|claude-5")


def _claude_context(p: psutil.Process) -> dict | None:
    home = Path.home() / ".claude"
    info = _json(home / "sessions" / f"{p.pid}.json")
    log = _files.get((p.pid, "claude"))
    if not log or log.stem != info.get("sessionId"):
        log = next((home / "projects").glob(f"*/{info['sessionId']}.jsonl"), None)
        if not log:
            return {"state": info.get("status")}
        _files[(p.pid, "claude")] = log
    for line in _tail_lines(log):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        u = (d.get("message") or {}).get("usage") if d.get("type") == "assistant" and not d.get("isSidechain") else None
        if u:
            used = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
            model = d["message"].get("model") or ""
            window = 1_000_000 if "[1m]" in model or CLAUDE_1M.search(model) or used > 200_000 else 200_000
            return {"used": used, "window": window, "estimated": True, "state": info.get("status"), "model": model}
    return {"state": info.get("status")}


def _codex_context(p: psutil.Process) -> dict | None:
    log = _files.get((p.pid, "codex"))
    if not log:
        # the newest rollout started in this folder after the process did
        start, cwd = p.create_time() - 5, os.path.normcase(p.cwd())
        logs = sorted((Path.home() / ".codex" / "sessions").glob("*/*/*/rollout-*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in logs[:30]:
            if f.stat().st_mtime < start:
                break
            with f.open(encoding="utf-8", errors="replace") as fh:
                meta = json.loads(fh.readline() or "{}").get("payload") or {}
            if os.path.normcase(meta.get("cwd") or "") == cwd:
                log = _files[(p.pid, "codex")] = f
                break
        if not log:
            return None
    window = used = None
    for line in _tail_lines(log):  # newest first: the latest usage and the latest window, whichever comes first
        try:
            payload = json.loads(line).get("payload") or {}
        except ValueError:
            continue
        info = payload.get("info") or {}
        window = window or payload.get("model_context_window") or info.get("model_context_window")
        if used is None and payload.get("type") == "token_count" and info.get("last_token_usage"):
            used = info["last_token_usage"].get("total_tokens") or 0
        if window and used is not None:
            break
    return {"used": used or 0, "window": window} if window else None


def _devin_context(p: psutil.Process) -> dict | None:
    db = _devin_db()
    if not db:
        return None
    cmd = p.cmdline()
    sid = next((cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a in ("-r", "--resume")), None)
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1)) as conn:
        if sid:
            row = conn.execute("SELECT id, model FROM sessions WHERE id = ?", (sid,)).fetchone()
        else:  # the newest session in this folder started after the process did
            row = conn.execute(
                "SELECT id, model FROM sessions WHERE working_directory = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
                (p.cwd(), int(p.create_time()) - 5),
            ).fetchone()
        if not row:
            return None
        msgs = conn.execute(
            "SELECT chat_message FROM message_nodes WHERE session_id = ? ORDER BY row_id DESC LIMIT 50", (row[0],)
        ).fetchall()
    window = _devin_windows().get(row[1])
    for (msg,) in msgs:
        m = (json.loads(msg).get("metadata") or {}).get("metrics") or {}
        if m.get("input_tokens") is not None:
            used = sum(m.get(k) or 0 for k in ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens"))
            return {"used": used, "window": window, "model": row[1]} if window else {"used": used, "model": row[1]}
    return {"used": 0, "window": window} if window else None


CONTEXT = {"claude": _claude_context, "codex": _codex_context, "devin": _devin_context}


def context(pid: int, key: str) -> dict | None:
    """How full the agent's context window is: {used, window, estimated?, state?} from its own logs."""
    if key not in CONTEXT:
        return None
    try:
        return CONTEXT[key](psutil.Process(pid))
    except (psutil.Error, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return None


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


def outside(inside: set[int]) -> list[dict]:
    """Agents running on this machine but not in a shelldeck terminal (desktop apps, other terminals).

    inside: pids already under a shelldeck shell. Only the outermost agent process counts."""
    found = {}
    for p in psutil.process_iter():
        if p.pid in inside:
            continue
        try:
            if hit := identify(p):
                found[p.pid] = (p, hit)
        except psutil.Error:
            continue
    out = []
    for pid, (p, (key, model)) in found.items():
        try:
            parent = p.parent()
            if parent and parent.pid in found:
                continue
            cwd = p.cwd()
            host = parent.name().removesuffix(".exe") if parent else ""
        except psutil.Error:
            cwd, host = "", ""
        out.append({"pid": pid, "agent": key, "label": AGENTS[key][0], "model": model, "cwd": cwd, "host": host})
    return sorted(out, key=lambda a: (a["label"], a["pid"]))


def _devin_db() -> Path | None:
    """Devin keeps every session (CLI and hosted by apps over ACP) in one sessions.db.

    ponytail: Windows path verified; the Linux/macOS locations are guesses."""
    roots = [Path(os.environ.get("APPDATA", "")), Path.home() / ".local" / "share", Path.home() / ".config"]
    return next((f for r in roots if (f := r / "devin" / "cli" / "sessions.db").exists()), None)


def devin_sessions(limit: int = 50) -> list[dict]:
    """Recent Devin sessions, newest first (read-only; Devin may be writing)."""
    db = _devin_db()
    if not db:
        return []
    try:
        with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1)) as conn:
            rows = conn.execute(
                "SELECT id, title, working_directory, model, backend_type, created_at, last_activity_at FROM sessions "
                "WHERE hidden = 0 ORDER BY last_activity_at DESC LIMIT ?", (limit,)
            ).fetchall()
    except sqlite3.Error:
        return []
    keys = ("id", "title", "cwd", "model", "backend", "created_at", "last_activity_at")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def forget(alive: set[int]) -> None:
    for pid in [p for p in _seen if p not in alive]:
        del _seen[pid]
    for k in [k for k in _files if k[0] not in alive]:
        del _files[k]


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
        if key == "devin" and (exe := shutil.which("devin")):  # <install>/bin/devin(.exe) -> <install>/model_configs.bin
            return devin_models(Path(exe).resolve().parent.parent / "model_configs.bin")
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeDecodeError):
        pass
    return list(AGENTS[key][3])


def catalog() -> list[dict]:
    """Every known agent, whether its CLI is on PATH, and the models it offers."""
    out = []
    for key, (label, exes, _, _) in AGENTS.items():
        if not exes:
            continue
        path = next((p for e in exes if (p := shutil.which(e))), None)
        out.append({"key": key, "label": label, "command": exes[0], "installed": bool(path), "path": path,
                    "models": models(key) if path else list(AGENTS[key][3]), "default_model": default_model(key) if path else None})
    return out


if __name__ == "__main__":
    assert _flag_model(["claude", "--model", "opus"]) == "opus"
    assert _flag_model(["codex", "-m", "gpt-5-codex"]) == "gpt-5-codex"
    assert _flag_model(["x", "--model=sonnet"]) == "sonnet"
    assert _flag_model(["x", "--mode", "y"]) is None
    # one Devin entry: display name (1) + base model (30.1) -> the name `devin --model` takes
    base = b"\x0a\x0fClaude Opus 4.7"
    entry = b"\x0a\x16Claude Opus 4.7 Medium" + b"\x18\x01" + b"\xf2\x01" + bytes([len(base)]) + base
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "m.bin").write_bytes(b"\x0a" + bytes([len(entry)]) + entry + b"\x0a\x0b\x0a\x09Kimi K2.6")
        assert devin_models(Path(d) / "m.bin") == ["claude-opus-4.7", "kimi-k2.6"]
    print("ok")
