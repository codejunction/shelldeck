"""Smart recall without embeddings: an installed agent's smallest model turns a query into related keywords, and the
normal FTS search runs with them ("auth" -> oauth, jwt, login, session...).

Token-lean by design: only the query goes in (never stored memory), only a short keyword list comes back, the
cheapest model is used, and each (agent, query) is cached in context.db. Opt-in (`recall_agent` setting or
`sd recall --smart`); any failure falls back to plain keyword search.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from . import agents, context, team

PRIORITY = ("claude", "codex", "gemini", "devin")
TIMEOUT_S = 30
MAX_TERMS = 8
# the cheapest model each CLI accepts by name; codex/devin pick theirs from their own model lists ("small" tier)
SMALL = {"claude": "haiku", "gemini": "flash-lite"}
TERM = re.compile(r"^[\w][\w .+#/-]{0,39}$")


def _prompt(query: str) -> str:
    return (f"List up to {MAX_TERMS} short search keywords or synonyms a developer might use for: {query}\n"
            "Reply with the keywords only, comma-separated, no explanation.")


def small_model(agent: str) -> str | None:
    return SMALL.get(agent) or team.pick_model(agent, "small", "")


def argv(agent: str, query: str, model: str | None = None, prompt: str | None = None) -> list[str] | None:
    """The agent's own non-interactive mode (claude -p, codex exec, gemini -p, devin -p) with the chosen model,
    else its smallest one. A model name must be one plain word (team.SAFE_MODEL). `prompt` replaces the keyword one."""
    exe = shutil.which(agents.AGENTS[agent][1][0]) if agent in agents.AGENTS else None
    if not exe:
        return None
    if model and not team.SAFE_MODEL.match(model):
        return None
    model = model or small_model(agent)
    prompt = prompt or _prompt(query)
    if agent == "claude":
        return [exe, "-p", *(["--model", model] if model else []), prompt]
    if agent == "gemini":
        return [exe, *(["-m", model] if model else []), "-p", prompt]
    if agent == "codex":
        return [exe, "exec", *(["-m", model] if model else []), prompt]
    if agent == "devin":
        return [exe, "-p", *(["--model", model] if model else []), "--", prompt]
    return None


def choices() -> list[dict]:
    """Installed priority agents with their models (smallest first as the default), for the search box's pickers."""
    out = []
    for a in PRIORITY:
        if not shutil.which(agents.AGENTS[a][1][0]):
            continue
        small = small_model(a)
        models = [m for m in agents.models(a) if team.SAFE_MODEL.match(m)]
        out.append({"agent": a, "label": agents.AGENTS[a][0], "default": small, "models": ([small] if small else []) + [m for m in models if m != small]})
    return out


def pick(setting: str) -> str | None:
    """`recall_agent` -> the agent to use: a named one if installed, `auto` = the first installed priority agent."""
    if setting in PRIORITY:
        return setting if shutil.which(agents.AGENTS[setting][1][0]) else None
    if setting == "auto":
        return next((a for a in PRIORITY if shutil.which(agents.AGENTS[a][1][0])), None)
    return None


def parse(text: str, query: str) -> list[str]:
    """Plain keywords from the model's reply (anything else is dropped), without the query's own words."""
    own = set(re.findall(r"\w+", query.lower()))
    out: list[str] = []
    for raw in re.split(r"[,\n;]+", text or ""):
        t = raw.strip().strip("-*•'\"`.").strip().lower()
        if t and TERM.match(t) and t not in own and t not in out:
            out.append(t)
    return out[:MAX_TERMS]


def _run(cmd: list[str] | None) -> str | None:
    """The agent's reply, or None on any failure."""
    if not cmd:
        return None
    env = {k: v for k, v in os.environ.items() if k not in ("SHELLDECK_AGENT_REPORT_TOKEN", "SHELLDECK_SESSION_ID")}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL, env=env,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def command_line(text: str) -> str:
    """The one command in a reply: fences and backticks dropped, first line only, no control characters
    (it is typed into a terminal, so a CR or ESC would run or rewrite it)."""
    for line in (text or "").splitlines():
        line = "".join(c for c in line.strip().strip("`") if c.isprintable()).strip()
        if line and not line.startswith("```") and line.lower() not in ("powershell", "bash", "sh", "cmd", "pwsh", "zsh", "fish"):
            return line[:1000]
    return ""


def ask(question: str, shell: str, where: str, cwd: str, agent: str, model: str | None = None) -> str:
    """One shell command for a plain-language question, written for this terminal's shell and OS. Never run here."""
    q = " ".join(context.redact(question).split())[:500]
    prompt = (f"Write one {shell} command for {where}, run from the folder {cwd or 'unknown'}, that does this: {q}\n"
              "Reply with the command only, on one line: no explanation, no code fences.")
    return command_line(_run(argv(agent, q, model, prompt)) or "")


def _cache(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS query_expansions (agent TEXT NOT NULL, query TEXT NOT NULL, terms TEXT NOT NULL,"
                 " created_at TEXT NOT NULL, PRIMARY KEY (agent, query))")


def expand(query: str, agent: str, model: str | None = None) -> list[str]:
    """Related keywords for a query from the agent's model (its smallest by default); cached; [] on any failure."""
    q = " ".join(context.redact(query).split())[:200].lower()
    if not q:
        return []
    context.init()
    with context._connect() as conn:
        _cache(conn)
        key = f"{agent}:{model}" if model else agent
        row = conn.execute("SELECT terms FROM query_expansions WHERE agent = ? AND query = ?", (key, q)).fetchone()
    if row:
        return json.loads(row["terms"])
    out = _run(argv(agent, q, model))
    if out is None:
        return []
    terms = parse(out, q)
    if terms:
        with context._connect() as conn:
            _cache(conn)
            conn.execute("INSERT OR REPLACE INTO query_expansions VALUES (?, ?, ?, ?)",
                         (key, q, json.dumps(terms), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return terms
