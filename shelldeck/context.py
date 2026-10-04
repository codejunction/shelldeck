"""Persistent, local-first agent context: tasks, state, memory, decisions, events and cross-project recall.

SQLite (`<config>/context.db`, FTS5 when the build has it) is the source of truth; the Markdown files under
`<project>/.shelldeck/` are projections written from it. Everything here is deterministic: no LLM, no network.
Knowledge is scoped to the project it came from (conflicting facts in two projects stay two facts), secrets are
redacted before anything is stored, and source files are hashed so changed sources mark knowledge STALE.
"""

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import db

KNOWLEDGE_TYPES = frozenset({
    "architecture", "api", "database", "authentication", "deployment", "infrastructure", "configuration", "testing",
    "convention", "pattern", "bug", "gotcha", "decision", "dependency", "security", "performance", "domain", "workflow",
    "note", "discovery",
})
STATUSES = ("NEW", "VERIFIED", "STALE", "REVIEWED", "INVALIDATED")
TASK_STATUSES = ("TODO", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED")
EVENT_TYPES = frozenset({
    "SESSION_START", "SESSION_STOP", "PROMPT", "PLAN", "PLAN_UPDATE", "FILE_READ", "FILE_EDIT", "FILE_CREATE",
    "FILE_DELETE", "COMMAND", "COMMAND_RESULT", "TEST", "TEST_RESULT", "ERROR", "DISCOVERY", "DECISION", "NOTE",
    "AGENT_MESSAGE", "HANDOFF", "HANDOFF_RECEIVED", "TASK_START", "TASK_UPDATE", "TASK_COMPLETE",
})
RELATIONS = frozenset({"related-to", "depends-on", "uses-pattern", "shares-database-with"})
CONFIDENCE = {"remember": 0.9, "discover": 0.8, "verified": 0.95}
MAX_TEXT = 8000
MAX_EVENTS = 500  # per project; older events are compacted away (the summaries live in state/memory)
# share of the context budget (characters) per section of a rendered context package
BUDGET = {"task": 0.10, "state": 0.20, "memory": 0.20, "handoff": 0.20, "knowledge": 0.20, "events": 0.10}
DEFAULT_BUDGET_CHARS = 6000

# ---------------------------------------------------------------------------------------------- secrets

SENSITIVE_FILE = re.compile(r"(^|[\\/])(\.env(\..*)?|.*\.pem|.*\.key|credentials\..*|secrets\..*|id_rsa.*|id_ed25519.*)$", re.I)
_SECRETS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWTs
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@]+@"),  # credentials in URLs
]
_ASSIGN = re.compile(r"(?i)\b([A-Z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|cookie|credential)s?)"
                     r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)")


def redact(text: str) -> str:
    """Remove secrets before anything is stored or indexed."""
    for rx in _SECRETS:
        text = rx.sub("[REDACTED]", text)
    return _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)


def _clean(text, limit: int = MAX_TEXT) -> str:
    return redact(str(text or "")).strip()[:limit]


# ---------------------------------------------------------------------------------------------- storage


def path() -> Path:
    return db.config_dir() / "context.db"


_fts: list[bool] = []


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(path(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init() -> None:
    """Create the schema (idempotent, additive)."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                repository TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                agent TEXT, started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL DEFAULT 'active');
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                session_id TEXT, title TEXT NOT NULL, objective TEXT, plan TEXT, status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state (project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
                step TEXT, next_action TEXT, last_error TEXT, tests TEXT, agent TEXT, session_id TEXT, git_json TEXT,
                updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS knowledge (id TEXT PRIMARY KEY, scope TEXT NOT NULL DEFAULT 'PROJECT',
                project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, type TEXT NOT NULL, topic TEXT, title TEXT NOT NULL,
                content TEXT NOT NULL, norm TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL DEFAULT 'NEW',
                created_by TEXT, session_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, verified_at TEXT,
                UNIQUE (project_id, norm));
            CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT, alternatives TEXT, consequence TEXT,
                created_by TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                session_id TEXT, agent TEXT, type TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT, importance INTEGER NOT NULL DEFAULT 1);
            CREATE INDEX IF NOT EXISTS idx_ctx_events ON events(project_id, id);
            CREATE TABLE IF NOT EXISTS relationships (id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
                relation TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                UNIQUE (source_type, source_id, relation, target_type, target_id));
            CREATE TABLE IF NOT EXISTS source_references (id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
                project_id TEXT, file_path TEXT NOT NULL, file_hash TEXT, line_start INTEGER, line_end INTEGER);
            """
        )
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(kind UNINDEXED, ref_id UNINDEXED, project_id UNINDEXED, title, body)")
            _fts[:] = [True]
        except sqlite3.OperationalError:  # ponytail: no FTS5 in this sqlite build; recall falls back to LIKE
            _fts[:] = [False]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def _norm(text: str) -> str:
    """Wording-insensitive key for de-duplication ("JWT contains tenant_id" == "jwt includes tenant_id.")."""
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return " ".join(w for w in words if w not in {"the", "a", "an", "is", "are", "contains", "includes", "has", "have"})


def _index(conn, kind: str, ref_id: str, project_id: str | None, title: str, body: str) -> None:
    if not _fts or not _fts[0]:
        return
    conn.execute("DELETE FROM search WHERE kind = ? AND ref_id = ?", (kind, ref_id))
    conn.execute("INSERT INTO search (kind, ref_id, project_id, title, body) VALUES (?, ?, ?, ?, ?)", (kind, ref_id, project_id, title, body))


# ---------------------------------------------------------------------------------------------- projects


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=3,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def git_state(root: str | Path) -> dict:
    """Branch, commit, dirty flag and changed files; empty when the folder isn't a git checkout."""
    root = Path(root)
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return {}
    changed = [line[3:] for line in _git(root, "status", "--porcelain").splitlines() if len(line) > 3]
    return {"branch": branch, "commit": _git(root, "rev-parse", "--short", "HEAD"), "dirty": bool(changed),
            "changed": [c for c in changed if not SENSITIVE_FILE.search(c)][:50], "repository": _git(root, "remote", "get-url", "origin")}


def project_for(cwd: str | None = None, name: str | None = None) -> dict | None:
    """The context project for a folder (registered on first use) or an existing one by name."""
    init()
    with _connect() as conn:
        if name:
            row = conn.execute("SELECT * FROM projects WHERE name = ? COLLATE NOCASE ORDER BY updated_at DESC", (name,)).fetchone()
            return dict(row) if row else None
        if not cwd:
            return None
        here = Path(cwd).resolve()
        # the deepest known project containing cwd: a shelldeck project, else one registered before
        try:
            known = [(Path(p["path"]), p["name"]) for p in db.list_projects()]
        except sqlite3.Error:  # no shelldeck database (CLI-only use): context still works by folder
            known = []
        known += [(Path(r["path"]), r["name"]) for r in conn.execute("SELECT path, name FROM projects")]
        hits = [(p, n) for p, n in known if here == p.resolve() or p.resolve() in here.parents]
        if hits:
            root, label = max(hits, key=lambda h: len(str(h[0])))
        else:
            top = _git(here, "rev-parse", "--show-toplevel")
            root = Path(top) if top else here
            label = root.name or str(root)
        key = str(root.resolve())
        row = conn.execute("SELECT * FROM projects WHERE path = ?", (key,)).fetchone()
        if row:
            return dict(row)
        now = _now()
        row = {"id": _id("p"), "name": label, "path": key, "repository": _git(root, "remote", "get-url", "origin") or None,
               "created_at": now, "updated_at": now}
        conn.execute("INSERT INTO projects VALUES (:id, :name, :path, :repository, :created_at, :updated_at)", row)
        return row


def list_projects() -> list[dict]:
    init()
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY updated_at DESC")]
        for r in rows:
            r["knowledge"] = conn.execute("SELECT COUNT(*) FROM knowledge WHERE project_id = ? AND status != 'INVALIDATED'", (r["id"],)).fetchone()[0]
            t = conn.execute("SELECT title, status FROM tasks WHERE project_id = ? ORDER BY updated_at DESC", (r["id"],)).fetchone()
            r["task"] = dict(t) if t else None
        return rows


def _touch(conn, project_id: str) -> None:
    conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now(), project_id))


# ---------------------------------------------------------------------------------------------- writes


def _payload(payload: dict | None) -> str:
    """Redact each value, then serialize: redacting serialized JSON could eat a closing quote (`TOKEN=abc"}`)."""
    clean = {k: _clean(v, 600) if isinstance(v, str) else v for k, v in (payload or {}).items()}
    return json.dumps(clean)[:4000]


def record_event(project: dict, type_: str, payload: dict | None = None, *, agent: str | None = None,
                 session_id: str | None = None, importance: int = 1) -> None:
    if type_ not in EVENT_TYPES:
        raise ValueError("invalid_event_type")
    with _connect() as conn:
        conn.execute("INSERT INTO events (project_id, session_id, agent, type, timestamp, payload, importance) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (project["id"], session_id, agent, type_, _now(), _payload(payload), max(1, min(int(importance), 5))))
        # compaction: keep the newest MAX_EVENTS; important ones (>= 3) survive longer
        conn.execute("DELETE FROM events WHERE project_id = ? AND importance < 3 AND id NOT IN "
                     "(SELECT id FROM events WHERE project_id = ? ORDER BY id DESC LIMIT ?)", (project["id"], project["id"], MAX_EVENTS))
        _touch(conn, project["id"])


def _hash(file: Path) -> str | None:
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def record_memory(project: dict, text: str, *, kind: str = "remember", type_: str = "note", topic: str = "", title: str = "",
                  files: list[str] | None = None, agent: str | None = None, session_id: str | None = None, scope: str = "PROJECT") -> dict:
    """Store a fact/discovery. The same fact (by normalized wording) in the same project is updated, not duplicated;
    the same wording in another project stays separate (knowledge is scoped to its source)."""
    content = _clean(text)
    if not content:
        raise ValueError("text_required")
    if type_ not in KNOWLEDGE_TYPES:
        raise ValueError("invalid_type")
    scope = scope.upper() if scope.upper() in ("GLOBAL", "PROJECT") else "PROJECT"
    title = _clean(title, 120) or content.splitlines()[0][:80]
    norm, now = _norm(content), _now()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM knowledge WHERE project_id = ? AND norm = ?", (project["id"], norm)).fetchone()
        if row:
            kid = row["id"]
            conn.execute("UPDATE knowledge SET updated_at = ?, confidence = MAX(confidence, ?), status = CASE WHEN status IN ('STALE', 'INVALIDATED') THEN 'NEW' ELSE status END WHERE id = ?",
                         (now, CONFIDENCE.get(kind, 0.8), kid))
        else:
            kid = _id("k")
            conn.execute("INSERT INTO knowledge (id, scope, project_id, type, topic, title, content, norm, confidence, status, created_by, session_id, created_at, updated_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?, ?, ?, ?)",
                         (kid, scope, project["id"], type_, _clean(topic, 60) or None, title, content, norm, CONFIDENCE.get(kind, 0.8), agent, session_id, now, now))
        root = Path(project["path"])
        for f in files or []:
            rel = str(f).strip()
            if not rel or SENSITIVE_FILE.search(rel):
                continue  # never reference secrets files
            full = (root / rel).resolve()
            if root.resolve() not in full.parents:
                continue
            conn.execute("DELETE FROM source_references WHERE knowledge_id = ? AND file_path = ?", (kid, rel))
            conn.execute("INSERT INTO source_references (id, knowledge_id, project_id, file_path, file_hash) VALUES (?, ?, ?, ?, ?)",
                         (_id("s"), kid, project["id"], rel, _hash(full)))
        _index(conn, "knowledge", kid, project["id"], title, f"{topic} {type_} {content}")
        _touch(conn, project["id"])
    record_event(project, "DISCOVERY" if kind == "discover" else "NOTE", {"knowledge": kid}, agent=agent, session_id=session_id, importance=3)
    return get_knowledge(kid)


def record_decision(project: dict, title: str, decision: str = "", *, reason: str = "", alternatives: str = "", consequence: str = "",
                    agent: str | None = None) -> dict:
    title = _clean(title, 200)
    if not title:
        raise ValueError("title_required")
    row = {"id": _id("d"), "project_id": project["id"], "title": title, "decision": _clean(decision) or title, "reason": _clean(reason) or None,
           "alternatives": _clean(alternatives) or None, "consequence": _clean(consequence) or None, "created_by": agent, "created_at": _now()}
    with _connect() as conn:
        conn.execute(f"INSERT INTO decisions ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})", row)
        _index(conn, "decision", row["id"], project["id"], title, f"{row['decision']} {row['reason'] or ''} {row['alternatives'] or ''}")
        _touch(conn, project["id"])
    record_event(project, "DECISION", {"decision": row["id"]}, agent=agent, importance=4)
    return row


def save_state(project: dict, *, task: str | None = None, objective: str | None = None, plan: str | None = None, status: str | None = None,
               step: str | None = None, next_action: str | None = None, last_error: str | None = None, tests: str | None = None,
               agent: str | None = None, session_id: str | None = None, git: bool = True, event: bool = True) -> dict:
    """Update the active task and the current state; a checkpoint. Only the fields given change."""
    if status and status.upper() not in TASK_STATUSES:
        raise ValueError("invalid_status")
    now = _now()
    with _connect() as conn:
        t = conn.execute("SELECT * FROM tasks WHERE project_id = ? AND status NOT IN ('DONE', 'CANCELLED') ORDER BY updated_at DESC", (project["id"],)).fetchone()
        if task and (not t or (t["title"] != _clean(task, 200))):
            if not t:
                tid = _id("t")
                conn.execute("INSERT INTO tasks (id, project_id, session_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'IN_PROGRESS', ?, ?)",
                             (tid, project["id"], session_id, _clean(task, 200), now, now))
                t = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
                record_type = "TASK_START"
            else:
                conn.execute("UPDATE tasks SET title = ? WHERE id = ?", (_clean(task, 200), t["id"]))
                record_type = "TASK_UPDATE"
        else:
            record_type = "TASK_UPDATE"
        if t:
            sets = {k: _clean(v) for k, v in (("objective", objective), ("plan", plan)) if v is not None}
            if status:
                sets["status"] = status.upper()
                if status.upper() == "DONE":
                    record_type = "TASK_COMPLETE"
            sets["updated_at"] = now
            conn.execute(f"UPDATE tasks SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), t["id"]))
            _index(conn, "task", t["id"], project["id"], _clean(task, 200) or t["title"], f"{objective or t['objective'] or ''} {plan or t['plan'] or ''}")
        cur = conn.execute("SELECT * FROM state WHERE project_id = ?", (project["id"],)).fetchone()
        st = dict(cur) if cur else {"project_id": project["id"]}
        for k, v in (("step", step), ("next_action", next_action), ("last_error", last_error), ("tests", tests), ("agent", agent), ("session_id", session_id)):
            if v is not None:
                st[k] = _clean(v, 2000) or None
        if git:
            st["git_json"] = json.dumps(git_state(project["path"]))
        st["updated_at"] = now
        cols = ["project_id", "step", "next_action", "last_error", "tests", "agent", "session_id", "git_json", "updated_at"]
        conn.execute(f"INSERT OR REPLACE INTO state ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [st.get(c) for c in cols])
        _touch(conn, project["id"])
    if not event:  # a capture-driven update (tests line, last error): the caller records its own event
        return {}
    record_event(project, record_type, {"step": step, "status": status}, agent=agent, session_id=session_id, importance=2)
    return project_context(project)


def set_status(knowledge_id: str, status: str) -> dict:
    """Knowledge lifecycle: NEW -> VERIFIED -> STALE -> REVIEWED (or INVALIDATED). Verifying re-hashes sources."""
    status = status.upper()
    if status not in STATUSES:
        raise ValueError("invalid_status")
    k = get_knowledge(knowledge_id)
    if not k:
        raise LookupError("knowledge_not_found")
    with _connect() as conn:
        conn.execute("UPDATE knowledge SET status = ?, updated_at = ?, verified_at = CASE WHEN ? IN ('VERIFIED', 'REVIEWED') THEN ? ELSE verified_at END,"
                     " confidence = CASE WHEN ? = 'VERIFIED' THEN MAX(confidence, 0.95) ELSE confidence END WHERE id = ?",
                     (status, _now(), status, _now(), status, knowledge_id))
        if status in ("VERIFIED", "REVIEWED"):
            proj = conn.execute("SELECT path FROM projects WHERE id = ?", (k["project_id"],)).fetchone()
            for ref in k["sources"]:
                h = _hash(Path(proj["path"]) / ref["file_path"]) if proj else None
                conn.execute("UPDATE source_references SET file_hash = ? WHERE knowledge_id = ? AND file_path = ?", (h, knowledge_id, ref["file_path"]))
    return get_knowledge(knowledge_id)


def check_stale(project: dict | None = None) -> list[str]:
    """Mark knowledge STALE whose source files changed or disappeared; returns the ids marked."""
    init()
    marked = []
    with _connect() as conn:
        sql = ("SELECT k.id, r.file_path, r.file_hash, p.path FROM knowledge k JOIN source_references r ON r.knowledge_id = k.id"
               " JOIN projects p ON p.id = k.project_id WHERE k.status IN ('NEW', 'VERIFIED', 'REVIEWED')")
        args: tuple = ()
        if project:
            sql += " AND k.project_id = ?"
            args = (project["id"],)
        for r in conn.execute(sql, args).fetchall():
            if r["id"] not in marked and _hash(Path(r["path"]) / r["file_path"]) != r["file_hash"]:
                marked.append(r["id"])
        for kid in marked:
            conn.execute("UPDATE knowledge SET status = 'STALE', updated_at = ? WHERE id = ?", (_now(), kid))
    return marked


def relate(project: dict, relation: str, target: dict) -> dict:
    if relation not in RELATIONS:
        raise ValueError("invalid_relation")
    if target["id"] == project["id"]:
        raise ValueError("self_relation")
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO relationships VALUES (?, 'project', ?, ?, 'project', ?)", (_id("r"), project["id"], relation, target["id"]))
    return {"source": project["name"], "relation": relation, "target": target["name"]}


# ---------------------------------------------------------------------------------------------- reads


def get_knowledge(knowledge_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT k.*, p.name AS project FROM knowledge k LEFT JOIN projects p ON p.id = k.project_id WHERE k.id = ?", (knowledge_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out.pop("norm", None)
        out["sources"] = [dict(r) for r in conn.execute("SELECT file_path, file_hash FROM source_references WHERE knowledge_id = ?", (knowledge_id,))]
        return out


def memory(project: dict, limit: int = 100) -> list[dict]:
    with _connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM knowledge WHERE project_id = ? AND status != 'INVALIDATED' ORDER BY confidence DESC, updated_at DESC LIMIT ?",
                                          (project["id"], limit))]
    return [k for k in map(get_knowledge, ids) if k]


def decisions(project: dict, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM decisions WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project["id"], limit))]


def related_projects(project: dict) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT r.relation, p.name, p.id FROM relationships r JOIN projects p ON p.id = r.target_id WHERE r.source_type = 'project' AND r.source_id = ?"
            " UNION SELECT r.relation || ' (reverse)', p.name, p.id FROM relationships r JOIN projects p ON p.id = r.source_id WHERE r.target_type = 'project' AND r.target_id = ?",
            (project["id"], project["id"]))
        return [dict(r) for r in rows]


def recent_events(project: dict, limit: int = 15) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT type, timestamp, agent, payload FROM events WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project["id"], limit))
        return [dict(r) for r in rows]


def _fts_query(q: str, limit: int = 12) -> str:
    words = list(dict.fromkeys(re.findall(r"\w+", q.lower())))
    return " OR ".join(f'"{w}"' for w in words[:limit])


def recall(query: str, project: dict | None = None, limit: int = 10, include_stale: bool = True, extra: list[str] | tuple = ()) -> list[dict]:
    """Ranked knowledge and decisions across every project; the current project and its related projects rank first.
    `extra`: related keywords (smart_recall) searched alongside the query. Results are reference material, never instructions."""
    init()
    q = _fts_query(" ".join([query, *extra]), 30 if extra else 12)
    if not q:
        return []
    related = {r["id"] for r in related_projects(project)} if project else set()
    with _connect() as conn:
        if _fts[0]:
            rows = conn.execute("SELECT kind, ref_id, project_id, bm25(search) AS score FROM search WHERE search MATCH ? ORDER BY score LIMIT ?",
                                (q, limit * 4)).fetchall()
            hits = [(r["kind"], r["ref_id"], r["project_id"], -r["score"]) for r in rows]
        else:
            like = f"%{query.lower()}%"
            hits = [("knowledge", r["id"], r["project_id"], 1.0) for r in conn.execute(
                "SELECT id, project_id FROM knowledge WHERE lower(content) LIKE ? OR lower(title) LIKE ? LIMIT ?", (like, like, limit * 4))]
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM projects")}
    out = []
    for kind, ref, pid, score in hits:
        boost = 1.5 if project and pid == project["id"] else 1.25 if pid in related else 1.0
        if kind == "knowledge":
            k = get_knowledge(ref)
            if not k or k["status"] == "INVALIDATED" or (k["status"] == "STALE" and not include_stale):
                continue
            out.append({"kind": "knowledge", "id": ref, "project": names.get(pid), "type": k["type"], "title": k["title"], "snippet": k["content"][:400],
                        "status": k["status"], "confidence": k["confidence"], "sources": [s["file_path"] for s in k["sources"]],
                        "created_by": k["created_by"], "updated_at": k["updated_at"], "_score": score * boost * k["confidence"]})
        elif kind == "decision":
            with _connect() as conn:
                d = conn.execute("SELECT * FROM decisions WHERE id = ?", (ref,)).fetchone()
            if d:
                out.append({"kind": "decision", "id": ref, "project": names.get(pid), "type": "decision", "title": d["title"], "snippet": (d["decision"] or "")[:400],
                            "status": "DECIDED", "confidence": 0.9, "sources": [], "created_by": d["created_by"], "updated_at": d["created_at"], "_score": score * boost * 0.9})
        elif kind == "task":
            with _connect() as conn:
                t = conn.execute("SELECT * FROM tasks WHERE id = ?", (ref,)).fetchone()
            if t:
                out.append({"kind": "task", "id": ref, "project": names.get(pid), "type": "task", "title": t["title"], "snippet": (t["objective"] or "")[:400],
                            "status": t["status"], "confidence": 0.7, "sources": [], "created_by": None, "updated_at": t["updated_at"], "_score": score * boost * 0.7})
    out.sort(key=lambda r: r["_score"], reverse=True)
    out = out[:limit]
    top = out[0]["_score"] if out and out[0]["_score"] > 0 else 1
    for i, r in enumerate(out):
        score = r.pop("_score")
        # bm25 is near zero on a tiny index; fall back to rank order so a match never reads as 0
        r["relevance"] = round(score / top, 2) if score > 0 and score / top >= 0.05 else round(1 / (i + 2), 2)
    return out


@dataclass
class ContextPackage:
    project: dict
    task: dict | None = None
    state: dict | None = None
    memory: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    handoff: dict | None = None
    related_projects: list[dict] = field(default_factory=list)
    relevant_knowledge: list[dict] = field(default_factory=list)
    git_state: dict = field(default_factory=dict)
    recent_events: list[dict] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def project_context(project: dict, query: str = "", handoff: dict | None = None) -> dict:
    """Everything an agent needs to continue, as a ContextPackage dict (plus `text`, the budgeted rendering)."""
    with _connect() as conn:
        t = conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY CASE WHEN status IN ('DONE', 'CANCELLED') THEN 1 ELSE 0 END, updated_at DESC",
                         (project["id"],)).fetchone()
        st = conn.execute("SELECT * FROM state WHERE project_id = ?", (project["id"],)).fetchone()
    state = dict(st) if st else None
    git = json.loads(state.pop("git_json") or "{}") if state else {}
    stale = check_stale(project)
    mem = memory(project, 30)
    pkg = ContextPackage(project={k: project[k] for k in ("id", "name", "path")}, task=dict(t) if t else None, state=state, memory=mem,
                         decisions=decisions(project, 10), handoff=handoff, related_projects=related_projects(project),
                         relevant_knowledge=[r for r in recall(query, project, 6) if r["project"] != project["name"]] if query else [],
                         git_state=git, recent_events=recent_events(project, 10), stale=[k["title"] for k in mem if k["status"] == "STALE"] or stale)
    out = pkg.to_dict()
    out["text"] = render(pkg)
    return out


def _cut(lines: list[str], budget: int) -> list[str]:
    out, used = [], 0
    for line in lines:
        if used + len(line) > budget:
            out.append("  ... (more: sd memory / sd recall)")
            break
        out.append(line)
        used += len(line) + 1
    return out


def render(pkg: ContextPackage, budget: int = DEFAULT_BUDGET_CHARS, shares: dict | None = None) -> str:
    """Compact text for an agent, each section held to its share of the budget (relevance order already applied)."""
    share = {k: int(budget * v) for k, v in (shares or BUDGET).items()}
    out = ["SHELLDECK CONTEXT", f"Project: {pkg.project['name']}  ({pkg.project['path']})"]
    if pkg.task:
        t = pkg.task
        out += _cut([f"Task [{t['status']}]: {t['title']}"] + ([f"  Objective: {t['objective']}"] if t.get("objective") else [])
                    + ([f"  Plan: {t['plan']}"] if t.get("plan") else []), share["task"])
    s = pkg.state or {}
    lines = [f"  {label}: {s[k]}" for k, label in (("step", "Current step"), ("next_action", "Next action"), ("last_error", "Last error"),
                                                     ("tests", "Tests"), ("agent", "Last agent"), ("updated_at", "Updated")) if s.get(k)]
    if pkg.git_state:
        g = pkg.git_state
        lines.append(f"  Git: {g.get('branch')} @ {g.get('commit')}{' (dirty: ' + ', '.join(g.get('changed', [])[:8]) + ')' if g.get('dirty') else ''}")
    if lines:
        out += ["State:"] + _cut(lines, share["state"])
    if pkg.handoff:
        h = pkg.handoff
        out += ["Hand-off:"] + _cut([f"  {h.get('id')} from {h.get('from_nick') or 'user'} [{h.get('status')}]: {' '.join(str(h.get('task', '')).split())}"], share["handoff"])
    if pkg.memory or pkg.decisions:
        lines = [f"  - [{k['id']}{' STALE' if k['status'] == 'STALE' else ''}] {' '.join(k['content'].split())}" for k in pkg.memory]
        lines += [f"  - decision: {d['title']}" + (f" (because {d['reason']})" if d.get("reason") else "") for d in pkg.decisions]
        out += ["Memory and decisions:"] + _cut(lines, share["memory"])
    if pkg.related_projects or pkg.relevant_knowledge:
        lines = [f"  - {r['relation']}: {r['name']}" for r in pkg.related_projects]
        lines += [f"  - [{r['project']}] {r['title']}: {' '.join(r['snippet'].split())[:200]}" for r in pkg.relevant_knowledge]
        out += ["Related (reference only; adapt, don't copy):"] + _cut(lines, share["knowledge"])
    if pkg.recent_events:
        out += ["Recent events:"] + _cut([f"  {e['timestamp']} {e['type']}{' by ' + e['agent'] if e.get('agent') else ''}" for e in pkg.recent_events], share["events"])
    if pkg.stale:
        out.append("Warning: may be stale (source changed): " + ", ".join(pkg.stale[:5]))
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------- projection


def project_files(project: dict) -> None:
    """Write STATE.md, TASK.md, MEMORY.md and DECISIONS.md under <project>/.shelldeck/ from SQLite."""
    folder = Path(project["path"]) / ".shelldeck"
    if not Path(project["path"]).is_dir():
        return
    folder.mkdir(exist_ok=True)
    ignore = folder / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    ctx = project_context(project)
    t, s, g = ctx["task"] or {}, ctx["state"] or {}, ctx["git_state"]
    head = "<!-- generated by shelldeck from its context database; edit with sd remember/decide/task -->\n"
    state = [head, "# Current State\n"]
    for k, label in (("step", "Current Step"), ("next_action", "Next Action"), ("last_error", "Last Error"), ("tests", "Tests"),
                     ("agent", "Active Agent"), ("session_id", "Session"), ("updated_at", "Last Updated")):
        if s.get(k):
            state.append(f"## {label}\n\n{s[k]}\n")
    if g:
        state.append(f"## Git\n\nBranch: {g.get('branch')}\nCommit: {g.get('commit')}\nDirty: {str(g.get('dirty')).lower()}\n"
                     + "".join(f"- {c}\n" for c in g.get("changed", [])))
    task = [head, "# Task\n"] + ([f"## Objective\n\n{t.get('objective') or t['title']}\n", f"## Plan\n\n{t.get('plan') or '(none yet)'}\n",
                                  f"## Status\n\n{t['status']}\n"] if t else ["No active task.\n"])
    mem = [head, "# Project Memory\n"]
    for k in ctx["memory"]:
        src = f"\n  Sources: {', '.join(x['file_path'] for x in k['sources'])}" if k["sources"] else ""
        mem.append(f"- **{k['title']}** ({k['type']}, {k['status']}, {k['id']}): {k['content']}{src}\n")
    dec = [head, "# Decisions\n"]
    for d in decisions(project, 200):
        dec.append(f"## {d['created_at'][:10]} — {d['title']}\n\n### Decision\n\n{d['decision']}\n"
                   + (f"\n### Reason\n\n{d['reason']}\n" if d.get("reason") else "")
                   + (f"\n### Alternatives Considered\n\n{d['alternatives']}\n" if d.get("alternatives") else "")
                   + (f"\n### Consequence\n\n{d['consequence']}\n" if d.get("consequence") else ""))
    for name, body in (("STATE.md", state), ("TASK.md", task), ("MEMORY.md", mem), ("DECISIONS.md", dec)):
        (folder / name).write_text("\n".join(body), encoding="utf-8")


def snapshot(project: dict) -> str:
    """The context section appended to a hand-off file: task, state, memory, decisions, git, recent events."""
    return "\n## Context snapshot\n\n```text\n" + project_context(project)["text"] + "\n```\n"


# ---------------------------------------------------------------------------------------------- deterministic capture
# What agents and people run, captured from hooks and shell integration (no model, no tokens): commands with their
# outcome, test runs, file edits, and one session record per agent run.

TEST_CMD = re.compile(r"\b(pytest|py\.test|unittest|tox|nox|jest|vitest|mocha|playwright test|go test|cargo test|cargo nextest|"
                      r"dotnet test|mvn\b.*\btest|gradle\w*\b.*\btest|phpunit|rspec|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test)\b", re.I)
LOOKUP_CMD = re.compile(r"^\s*(grep|rg|ag|find|fd|ls|cat|head|tail|which|where|test|\[|git (?:status|diff|log|show|grep))\b")


def record_activity(project: dict, act: dict, *, agent: str | None = None, session_id: str | None = None) -> None:
    """One finished command or edit: an event; a test run also sets the project's Tests line; a failed command (not a
    look-up like grep, whose non-zero exit means "no match") is an ERROR event and the last error."""
    if "file" in act:
        path = act["file"]
        if SENSITIVE_FILE.search(path):
            return
        record_event(project, "FILE_CREATE" if act.get("change") == "write" else "FILE_EDIT", {"file": path}, agent=agent, session_id=session_id)
        return
    cmd, ok = _clean(act.get("command"), 500), bool(act.get("ok", True))
    if not cmd:
        return
    if TEST_CMD.search(cmd):
        record_event(project, "TEST_RESULT", {"command": cmd, "ok": ok}, agent=agent, session_id=session_id, importance=3)
        save_state(project, tests=f"{'passed' if ok else 'FAILED'}: {cmd} ({_now()[:16].replace('T', ' ')} UTC)",
                   agent=agent, session_id=session_id, git=False, event=False)
    elif not ok and not LOOKUP_CMD.search(cmd):
        record_event(project, "ERROR", {"command": cmd}, agent=agent, session_id=session_id, importance=3)
        save_state(project, last_error=f"failed: {cmd}", agent=agent, session_id=session_id, git=False, event=False)
    else:
        record_event(project, "COMMAND", {"command": cmd, "ok": ok}, agent=agent, session_id=session_id)


def start_session(project: dict, session_id: str, agent: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO sessions (id, project_id, agent, started_at) VALUES (?, ?, ?, ?)", (session_id, project["id"], agent, _now()))
    record_event(project, "SESSION_START", {}, agent=agent, session_id=session_id, importance=2)


def end_session(project: dict, session_id: str) -> Path | None:
    """Close an agent run and write `.shelldeck/sessions/<id>.md` from its events (commands, tests, files, errors)."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE sessions SET ended_at = ?, status = 'ended' WHERE id = ?", (_now(), session_id))
        events = [dict(r) for r in conn.execute("SELECT type, timestamp, payload FROM events WHERE session_id = ? ORDER BY id", (session_id,))]
    record_event(project, "SESSION_STOP", {}, agent=row["agent"], session_id=session_id, importance=2)
    load = [(e["type"], e["timestamp"], json.loads(e["payload"] or "{}")) for e in events]
    files = sorted({p["file"] for t, _, p in load if t in ("FILE_EDIT", "FILE_CREATE") and "file" in p})
    cmds = [p["command"] for t, _, p in load if t in ("COMMAND", "TEST_RESULT", "ERROR") and "command" in p]
    tests = [f"{'passed' if p.get('ok') else 'FAILED'}: {p['command']}" for t, _, p in load if t == "TEST_RESULT"]
    errors = [p["command"] for t, _, p in load if t == "ERROR"]
    g = git_state(project["path"])
    lines = [f"# Session {session_id}", "", f"Agent: {row['agent']}", f"Project: {project['name']}", f"Started: {row['started_at']}",
             f"Ended: {_now()}", ""]
    for title, items in (("Files changed", files), ("Commands", cmds[-40:]), ("Tests", tests[-10:]), ("Errors", errors[-10:])):
        if items:
            lines += [f"## {title}", "", *[f"- {i}" for i in items], ""]
    if g:
        lines += ["## Git", "", f"Branch: {g.get('branch')}", f"Commit: {g.get('commit')}", f"Dirty: {str(g.get('dirty')).lower()}", ""]
    folder = Path(project["path"]) / ".shelldeck" / "sessions"
    if not Path(project["path"]).is_dir():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    ignore = folder.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    out = folder / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', session_id)}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
