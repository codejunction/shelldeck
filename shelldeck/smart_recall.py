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


def argv(agent: str, query: str) -> list[str] | None:
    """The agent's own non-interactive mode with its smallest model (claude -p, codex exec, gemini -p, devin -p)."""
    exe = shutil.which(agents.AGENTS[agent][1][0]) if agent in agents.AGENTS else None
    if not exe:
        return None
    model = SMALL.get(agent) or team.pick_model(agent, "small", query)
    prompt = _prompt(query)
    if agent == "claude":
        return [exe, "-p", *(["--model", model] if model else []), prompt]
    if agent == "gemini":
        return [exe, *(["-m", model] if model else []), "-p", prompt]
    if agent == "codex":
        return [exe, "exec", *(["-m", model] if model else []), prompt]
    if agent == "devin":
        return [exe, "-p", *(["--model", model] if model else []), "--", prompt]
    return None


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


def _cache(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS query_expansions (agent TEXT NOT NULL, query TEXT NOT NULL, terms TEXT NOT NULL,"
                 " created_at TEXT NOT NULL, PRIMARY KEY (agent, query))")


def expand(query: str, agent: str) -> list[str]:
    """Related keywords for a query from the agent's small model; cached; [] on any failure (search still works)."""
    q = " ".join(context.redact(query).split())[:200].lower()
    if not q:
        return []
    context.init()
    with context._connect() as conn:
        _cache(conn)
        row = conn.execute("SELECT terms FROM query_expansions WHERE agent = ? AND query = ?", (agent, q)).fetchone()
    if row:
        return json.loads(row["terms"])
    cmd = argv(agent, q)
    if not cmd:
        return []
    env = {k: v for k, v in os.environ.items() if k not in ("SHELLDECK_AGENT_REPORT_TOKEN", "SHELLDECK_SESSION_ID")}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL, env=env,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    terms = parse(r.stdout, q)
    if terms:
        with context._connect() as conn:
            _cache(conn)
            conn.execute("INSERT OR REPLACE INTO query_expansions VALUES (?, ?, ?, ?)",
                         (agent, q, json.dumps(terms), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return terms
