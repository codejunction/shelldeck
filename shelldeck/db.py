import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def config_dir() -> Path:
    p = Path(os.environ.get("SHELLDECK_HOME") or Path.home() / ".config" / "shelldeck")
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return config_dir() / "shelldeck.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _import_termy_db() -> None:
    """One-time copy of the old termy database, so projects/bookmarks/tasks carry over."""
    old = Path.home() / ".config" / "termy" / "termy.db"
    if "SHELLDECK_HOME" in os.environ or db_path().exists() or not old.exists():
        return
    shutil.copy2(old, db_path())
    log.info("imported %s", old)


def short_id() -> str:
    return uuid.uuid4().hex[:8]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    _import_termy_db()
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                cwd TEXT,
                shell TEXT,
                cols INTEGER,
                rows INTEGER,
                created_at TEXT NOT NULL,
                name TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS bookmarks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                command TEXT NOT NULL,
                project_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bookmarks_project ON bookmarks(project_id);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                board_column TEXT DEFAULT 'backlog',
                priority TEXT DEFAULT 'medium',
                due_at TEXT,
                reminder_at TEXT,
                reminder_acknowledged INTEGER DEFAULT 0,
                tags TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_column ON tasks(board_column);

            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_id TEXT NOT NULL,
                command TEXT NOT NULL,
                cron TEXT NOT NULL,
                timezone TEXT DEFAULT 'UTC',
                enabled INTEGER DEFAULT 1,
                timeout_seconds INTEGER DEFAULT 60,
                next_run_on TEXT,
                last_run_on TEXT,
                last_status TEXT DEFAULT 'never_run',
                last_error TEXT,
                last_duration_ms INTEGER,
                total_runs INTEGER DEFAULT 0,
                successful_runs INTEGER DEFAULT 0,
                failed_runs INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_project ON scheduled_jobs(project_id);

            CREATE TABLE IF NOT EXISTS job_runs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                output TEXT,
                error TEXT,
                duration_ms INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES scheduled_jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_job_runs_job_id ON job_runs(job_id);

            -- browser logins: sha256 of the cookie token, never the token itself
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                last_seen REAL NOT NULL,
                client TEXT,
                via TEXT,
                kind TEXT
            );

            -- Expo push tokens of logged-in app devices (session_hash = auth_sessions.token_hash)
            CREATE TABLE IF NOT EXISTS push_tokens (
                token TEXT PRIMARY KEY,
                session_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            -- command history (sessions/projects may be deleted later; keep the text)
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                project_id TEXT,
                cwd TEXT,
                command TEXT NOT NULL,
                exit_code INTEGER,
                duration_ms INTEGER,
                started_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_commands_started ON commands(started_at);

            -- scratchpad: one markdown body per note, the title is its first line
            CREATE TABLE IF NOT EXISTS scratch (
                id TEXT PRIMARY KEY,
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- agent hand-offs; <project>/.shelldeck/handoff.md is rendered from these
            CREATE TABLE IF NOT EXISTS handoffs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                from_sid TEXT,
                from_nick TEXT,
                to_sid TEXT,
                to_nick TEXT,
                agent TEXT,
                model TEXT,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_handoffs_project ON handoffs(project_id);

            -- native agent conversations reported by built-in integrations (resume_argv is validated argv, never shell text)
            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                agent TEXT NOT NULL,
                source TEXT NOT NULL,
                integration_version INTEGER,
                native_session_id TEXT,
                resume_argv_json TEXT,
                metadata_json TEXT,
                last_state TEXT NOT NULL DEFAULT 'unknown',
                last_seen_at TEXT NOT NULL,
                resume_enabled INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            DROP TABLE IF EXISTS note_tags;
            DROP TABLE IF EXISTS tags;
            DROP TABLE IF EXISTS mindmap_jobs;
            DROP TABLE IF EXISTS mindmap_index_jobs;
            DROP TABLE IF EXISTS mindmap_nodes;
            """
        )
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "name" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN name TEXT")
        for col in ("nick", "parent"):
            if col not in cols:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
        for (sid,) in conn.execute("SELECT id FROM sessions WHERE nick IS NULL ORDER BY created_at").fetchall():
            conn.execute("UPDATE sessions SET nick = ? WHERE id = ?", (_free_nick(conn), sid))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(auth_sessions)")}
        for col in ("via", "kind"):  # kind: browser | remote (the iOS app; never takes over)
            if col not in cols:
                conn.execute(f"ALTER TABLE auth_sessions ADD COLUMN {col} TEXT")
        conn.execute("DELETE FROM auth_sessions WHERE via LIKE 'share:%'")  # shares end with the server
        cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
        if "color" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN color INTEGER")
        missing = conn.execute("SELECT id FROM projects WHERE color IS NULL ORDER BY created_at").fetchall()
        for (pid,) in missing:
            conn.execute("UPDATE projects SET color = ? WHERE id = ?", (_next_color(conn), pid))


PROJECT_COLORS = 8


def _next_color(conn: sqlite3.Connection) -> int:
    """Least-used palette slot, so each new project gets a distinct color."""
    used = dict(conn.execute("SELECT color, COUNT(*) FROM projects WHERE color IS NOT NULL GROUP BY color").fetchall())
    return min(range(PROJECT_COLORS), key=lambda c: (used.get(c, 0), c))


def ensure_project(path: str) -> dict:
    path = str(Path(path).resolve())
    name = Path(path).name or "root"
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM projects WHERE path = ?", (path,)).fetchone()
        if row:
            return dict(row)
        pid = short_id()
        color = _next_color(conn)
        conn.execute(
            "INSERT INTO projects (id, path, name, created_at, color) VALUES (?, ?, ?, ?, ?)",
            (pid, path, name, _now(), color),
        )
        conn.commit()
        return {"id": pid, "path": path, "name": name, "created_at": _now(), "color": color}


def list_projects() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def get_project(project_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None


def rename_project(project_id: str, name: str) -> dict | None:
    with _connect() as conn:
        conn.execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id))
        conn.commit()
    return get_project(project_id)


# a person's name per terminal, so people and agents can say "ask Maya" instead of an id
NICKS = (
    "Ada", "Alan", "Amara", "Anya", "Arjun", "Asha", "Aiko", "Bea", "Bruno", "Chen", "Chiara", "Dara", "Dev", "Diego",
    "Elif", "Emil", "Esme", "Ezra", "Farah", "Felix", "Freya", "Gus", "Hana", "Hugo", "Ines", "Ivan", "Iris", "Jana",
    "Jonas", "Juno", "Kai", "Kamal", "Kenji", "Kira", "Lars", "Leila", "Lena", "Leo", "Lina", "Luca", "Maya", "Mateo",
    "Mei", "Milo", "Mira", "Nadia", "Nia", "Nico", "Noor", "Omar", "Oona", "Otto", "Priya", "Quinn", "Rafa", "Ravi",
    "Rosa", "Rumi", "Sana", "Sami", "Sven", "Tara", "Theo", "Tomas", "Uma", "Vera", "Wen", "Yara", "Yusuf", "Zara",
    "Zoe", "Ari", "Bo", "Cleo", "Dina", "Eli", "Fina", "Gil", "Hiro", "Ida", "Joss", "Kofi", "Lua", "Nell",
)


def _free_nick(conn: sqlite3.Connection) -> str:
    """A random name no terminal has yet (Maya2 once every name is taken)."""
    import random

    used = {r[0].casefold() for r in conn.execute("SELECT nick FROM sessions WHERE nick IS NOT NULL")}
    free = [n for n in NICKS if n.casefold() not in used]
    if free:
        return random.choice(free)
    base = random.choice(NICKS)
    return next(f"{base}{i}" for i in range(2, 10_000) if f"{base}{i}".casefold() not in used)


def add_session(
    project_id: str, cwd: str | None = None, shell: str | None = None, name: str | None = None, parent: str | None = None
) -> dict:
    sid = short_id()
    cwd = cwd or "."
    shell = shell or ""
    cols, rows = 120, 24
    with _connect() as conn:
        nick = _free_nick(conn)
        conn.execute(
            "INSERT INTO sessions (id, project_id, cwd, shell, cols, rows, created_at, name, nick, parent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, project_id, cwd, shell, cols, rows, _now(), name, nick, parent),
        )
        conn.commit()
    return {
        "id": sid,
        "project_id": project_id,
        "cwd": cwd,
        "shell": shell,
        "name": name,
        "nick": nick,
        "parent": parent,
        "cols": cols,
        "rows": rows,
    }


def list_sessions(project_id: str | None = None) -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if project_id:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_session(session_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def rename_session(session_id: str, name: str) -> dict | None:
    with _connect() as conn:
        conn.execute("UPDATE sessions SET name = ? WHERE id = ?", (name, session_id))
        conn.commit()
    return get_session(session_id)


def update_session_cwd(session_id: str, cwd: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE sessions SET cwd = ? WHERE id = ?", (cwd, session_id))
        conn.commit()


def update_session_size(session_id: str, cols: int, rows: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET cols = ?, rows = ? WHERE id = ?",
            (cols, rows, session_id),
        )
        conn.commit()


def delete_project(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM scheduled_jobs WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()


def list_sessions_with_project() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT s.id, s.name, s.nick, s.parent, s.shell, s.cwd, s.created_at, p.id as project_id, p.name as project_name, p.path as project_path "
            "FROM sessions s JOIN projects p ON s.project_id = p.id "
            "ORDER BY p.name, s.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def add_auth_session(token_hash: str, now: float, client: str, via: str = "local", kind: str = "browser") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, created_at, last_seen, client, via, kind) VALUES (?, ?, ?, ?, ?, ?)",
            (token_hash, now, now, client[:200], via, kind),
        )
        conn.commit()


def list_auth_sessions() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM auth_sessions ORDER BY last_seen DESC")]


def delete_auth_sessions_via(via: str) -> list[str]:
    """Delete the logins made through `via` (e.g. one share); returns their hashes."""
    with _connect() as conn:
        gone = [r[0] for r in conn.execute("SELECT token_hash FROM auth_sessions WHERE via = ?", (via,))]
        conn.execute("DELETE FROM auth_sessions WHERE via = ?", (via,))
        conn.commit()
        return gone


def add_push_token(token: str, session_hash: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO push_tokens (token, session_hash, created_at) VALUES (?, ?, ?)", (token, session_hash, time.time()))
        conn.commit()


def delete_push_token(token: str, session_hash: str) -> bool:
    with _connect() as conn:
        n = conn.execute("DELETE FROM push_tokens WHERE token = ? AND session_hash = ?", (token, session_hash)).rowcount
        conn.commit()
        return n > 0


def delete_push_tokens(session_hashes: list[str]) -> None:
    with _connect() as conn:
        conn.executemany("DELETE FROM push_tokens WHERE session_hash = ?", [(h,) for h in session_hashes])
        conn.commit()


def list_push_tokens() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM push_tokens")]


def get_auth_session(token_hash: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM auth_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        return dict(row) if row else None


def touch_auth_session(token_hash: str, now: float) -> None:
    with _connect() as conn:
        conn.execute("UPDATE auth_sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash))
        conn.commit()


def delete_auth_sessions(keep: str | None = None, only: str | None = None) -> None:
    """Delete one session (`only`), or all except `keep`."""
    with _connect() as conn:
        if only:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (only,))
        else:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash != ?", (keep or "",))
        conn.commit()


HISTORY_LIMIT = 20000


def add_command(session_id: str, project_id: str | None, cwd: str | None, command: str,
                exit_code: int | None, duration_ms: int | None, started_at: str) -> None:
    with _connect() as conn:
        last = conn.execute(
            "SELECT command, started_at FROM commands WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)
        ).fetchone()
        if last and last[0] == command and last[1] == started_at:
            return  # same command reported by a second window on this terminal
        conn.execute(
            "INSERT INTO commands (session_id, project_id, cwd, command, exit_code, duration_ms, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, project_id, cwd, command, exit_code, duration_ms, started_at),
        )
        conn.execute("DELETE FROM commands WHERE id <= (SELECT MAX(id) FROM commands) - ?", (HISTORY_LIMIT,))
        conn.commit()


def list_commands(q: str = "", project_id: str | None = None, failed: bool = False, limit: int = 300) -> list[dict]:
    sql = "SELECT * FROM commands WHERE command LIKE ? ESCAPE '\\'"
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    args: list = [like]
    if project_id:
        sql += " AND project_id = ?"
        args.append(project_id)
    if failed:
        sql += " AND exit_code IS NOT NULL AND exit_code != 0"
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def clear_commands() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM commands")
        conn.commit()


def list_bookmarks() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM bookmarks ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_bookmark(bookmark_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,)
        ).fetchone()
        return dict(row) if row else None


def add_bookmark(name: str, command: str, project_id: str | None = None) -> dict:
    bid = short_id()
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bookmarks (id, name, command, project_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (bid, name, command, project_id, now, now),
        )
        conn.commit()
    return {
        "id": bid,
        "name": name,
        "command": command,
        "project_id": project_id,
        "created_at": now,
        "updated_at": now,
    }


def update_bookmark(
    bookmark_id: str,
    name: str,
    command: str,
    project_id: str | None = None,
) -> dict | None:
    if not get_bookmark(bookmark_id):
        return None
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE bookmarks SET name = ?, command = ?, project_id = ?, updated_at = ? WHERE id = ?",
            (name, command, project_id, now, bookmark_id),
        )
        conn.commit()
    return get_bookmark(bookmark_id)


def delete_bookmark(bookmark_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
        conn.commit()


def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        conn.commit()


def delete_setting(key: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def add_task(
    title: str,
    description: str = "",
    board_column: str = "backlog",
    priority: str = "medium",
    due_at: str | None = None,
    reminder_at: str | None = None,
    tags: str | None = None,
) -> dict:
    tid = short_id()
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, description, board_column, priority, due_at, reminder_at, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tid,
                title,
                description,
                board_column,
                priority,
                due_at,
                reminder_at,
                tags,
                now,
                now,
            ),
        )
        conn.commit()
    return get_task(tid)


def get_task(task_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def list_tasks(board_column: str | None = None) -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if board_column:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE board_column = ? ORDER BY updated_at DESC",
                (board_column,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def update_task(
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    board_column: str | None = None,
    priority: str | None = None,
    due_at: str | None = None,
    reminder_at: str | None = None,
    reminder_acknowledged: int | None = None,
    tags: str | None = None,
) -> dict | None:
    if not get_task(task_id):
        return None
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE tasks SET title = COALESCE(?, title), description = COALESCE(?, description), board_column = COALESCE(?, board_column), priority = COALESCE(?, priority), due_at = COALESCE(?, due_at), reminder_at = COALESCE(?, reminder_at), reminder_acknowledged = COALESCE(?, reminder_acknowledged), tags = COALESCE(?, tags), updated_at = ? WHERE id = ?",
            (
                title,
                description,
                board_column,
                priority,
                due_at,
                reminder_at,
                reminder_acknowledged,
                tags,
                now,
                task_id,
            ),
        )
        conn.commit()
    return get_task(task_id)


def move_task(task_id: str, board_column: str) -> dict | None:
    return update_task(task_id, board_column=board_column)


def delete_task(task_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()


def ack_reminder(task_id: str) -> dict | None:
    return update_task(task_id, reminder_at=None, reminder_acknowledged=1)


def snooze_reminder(task_id: str, minutes: int = 15) -> dict | None:
    t = get_task(task_id)
    if not t:
        return None
    base = datetime.now(timezone.utc)
    if t.get("reminder_at"):
        try:
            base = datetime.fromisoformat(t["reminder_at"]).astimezone(timezone.utc)
        except ValueError:
            pass
    new_time = base + timedelta(minutes=minutes)
    return update_task(
        task_id, reminder_at=new_time.isoformat(), reminder_acknowledged=0
    )


def list_active_alarms(now_iso: str | None = None) -> list[dict]:
    now = now_iso or _now()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM tasks WHERE board_column != 'done' AND reminder_at <= ? AND (reminder_acknowledged = 0 OR reminder_acknowledged IS NULL) ORDER BY reminder_at ASC",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------


def add_job(
    name: str,
    project_id: str,
    command: str,
    cron: str,
    timezone: str = "UTC",
    enabled: bool = True,
    timeout_seconds: int = 60,
) -> dict:
    jid = short_id()
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO scheduled_jobs (id, name, project_id, command, cron, timezone, enabled, timeout_seconds, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                jid,
                name,
                project_id,
                command,
                cron,
                timezone,
                int(enabled),
                timeout_seconds,
                now,
                now,
            ),
        )
        conn.commit()
    return get_job(jid)


def get_job(job_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def list_jobs() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM scheduled_jobs ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def update_job(
    job_id: str,
    name: str | None = None,
    project_id: str | None = None,
    command: str | None = None,
    cron: str | None = None,
    timezone: str | None = None,
    enabled: bool | None = None,
    timeout_seconds: int | None = None,
    next_run_on: str | None = None,
    last_run_on: str | None = None,
    last_status: str | None = None,
    last_error: str | None = None,
    last_duration_ms: int | None = None,
) -> dict | None:
    if not get_job(job_id):
        return None
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET name = COALESCE(?, name), project_id = COALESCE(?, project_id), command = COALESCE(?, command), cron = COALESCE(?, cron), timezone = COALESCE(?, timezone), enabled = COALESCE(?, enabled), timeout_seconds = COALESCE(?, timeout_seconds), next_run_on = COALESCE(?, next_run_on), last_run_on = COALESCE(?, last_run_on), last_status = COALESCE(?, last_status), last_error = COALESCE(?, last_error), last_duration_ms = COALESCE(?, last_duration_ms), updated_at = ? WHERE id = ?",
            (
                name,
                project_id,
                command,
                cron,
                timezone,
                int(enabled) if enabled is not None else None,
                timeout_seconds,
                next_run_on,
                last_run_on,
                last_status,
                last_error,
                last_duration_ms,
                now,
                job_id,
            ),
        )
        conn.commit()
    return get_job(job_id)


def delete_job(job_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM job_runs WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
        conn.commit()


def toggle_job(job_id: str) -> dict | None:
    job = get_job(job_id)
    if not job:
        return None
    return update_job(job_id, enabled=not bool(job.get("enabled")))


def update_job_after_run(
    job_id: str,
    last_status: str,
    last_run_on: str,
    last_duration_ms: int,
    last_error: str | None,
    next_run_on: str | None,
    successful: bool,
) -> dict | None:
    if successful:
        col = "successful_runs"
    else:
        col = "failed_runs"
    now = _now()
    with _connect() as conn:
        conn.execute(
            f"UPDATE scheduled_jobs SET last_status = ?, last_run_on = ?, last_duration_ms = ?, last_error = ?, next_run_on = ?, total_runs = total_runs + 1, {col} = {col} + 1, updated_at = ? WHERE id = ?",
            (
                last_status,
                last_run_on,
                last_duration_ms,
                last_error,
                next_run_on,
                now,
                job_id,
            ),
        )
        conn.commit()
    return get_job(job_id)


def add_job_run(
    job_id: str,
    run_at: str,
    status: str,
    exit_code: int | None,
    output: str,
    error: str | None,
    duration_ms: int,
) -> dict:
    rid = short_id()
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO job_runs (id, job_id, run_at, status, exit_code, output, error, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, job_id, run_at, status, exit_code, output, error, duration_ms, now),
        )
        conn.commit()
    return {
        "id": rid,
        "job_id": job_id,
        "run_at": run_at,
        "status": status,
        "exit_code": exit_code,
        "output": output,
        "error": error,
        "duration_ms": duration_ms,
        "created_at": now,
    }


def list_job_runs(job_id: str, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM job_runs WHERE job_id = ? ORDER BY run_at DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM job_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------------ scratchpad


def list_scratch() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM scratch ORDER BY updated_at DESC")]


def add_scratch(body: str = "") -> dict:
    row = {"id": short_id(), "body": body, "created_at": _now(), "updated_at": _now()}
    with _connect() as conn:
        conn.execute("INSERT INTO scratch (id, body, created_at, updated_at) VALUES (:id, :body, :created_at, :updated_at)", row)
        conn.commit()
    return row


def update_scratch(note_id: str, body: str) -> bool:
    with _connect() as conn:
        n = conn.execute("UPDATE scratch SET body = ?, updated_at = ? WHERE id = ?", (body, _now(), note_id)).rowcount
        conn.commit()
    return n > 0


def delete_scratch(note_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM scratch WHERE id = ?", (note_id,))
        conn.commit()


# -------------------------------------------------------------------- handoffs


def add_handoff(**fields) -> dict:
    row = {"id": "h" + short_id()[:6], "status": "open", "result": None, "created_at": _now(), "updated_at": _now()} | fields
    with _connect() as conn:
        conn.execute(f"INSERT INTO handoffs ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})", row)
        conn.commit()
    return row


def get_handoff(handoff_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
        return dict(row) if row else None


def list_handoffs(project_id: str | None = None, to_sid: str | None = None, status: str | None = None) -> list[dict]:
    where, args = [], []
    for col, val in (("project_id", project_id), ("to_sid", to_sid), ("status", status)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    sql = "SELECT * FROM handoffs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC"
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args)]


def close_handoff(handoff_id: str, status: str, result: str | None) -> None:
    with _connect() as conn:
        conn.execute("UPDATE handoffs SET status = ?, result = ?, updated_at = ? WHERE id = ?", (status, result, _now(), handoff_id))
        conn.commit()


# -------------------------------------------------------------- agent sessions


def save_agent_session(session_id: str, agent: str, source: str, native_id: str, argv: list[str], state: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO agent_sessions (session_id, agent, source, native_session_id, resume_argv_json, last_state, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET agent = excluded.agent, source = excluded.source,"
            " native_session_id = excluded.native_session_id, resume_argv_json = excluded.resume_argv_json,"
            " last_state = excluded.last_state, last_seen_at = excluded.last_seen_at",
            (session_id, agent, source, native_id, json.dumps(argv), state, _now()))
        conn.commit()


def touch_agent_session(session_id: str, state: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE agent_sessions SET last_state = ?, last_seen_at = ? WHERE session_id = ?", (state, _now(), session_id))
        conn.commit()


def get_agent_session(session_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None
