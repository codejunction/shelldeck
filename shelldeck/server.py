import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote, urlsplit

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import agents, auth, db, gitgraph, shells, stats
from . import scheduler as sched
from .pty import PtyManager

log = logging.getLogger("shelldeck")

try:
    VERSION = version("shelldeck")
except PackageNotFoundError:
    VERSION = "dev"

STATIC = Path(__file__).parent / "static"

# Requests must target one of these hosts (blocks DNS rebinding). None = any host.
ALLOWED_HOSTS: set[str] | None = {"127.0.0.1", "localhost", "[::1]"}

SETTINGS_DEFAULTS = {
    "default_shell": shells.default_kind(),
    "wsl_distro": "",
    "font_size": "13",
    "theme": "dark",
    "layout_mode": "tiled",
    "project_tint": "on",
    "terminal_theme": "default",
    "font_family": "",
    "editor": "vscode",
}
# keep in sync with TERMINAL_THEMES in static/app.js
TERMINAL_THEMES = ("default", "dracula", "one-dark", "nord", "gruvbox-dark", "solarized-dark", "solarized-light", "github-light")
FONT_FAMILY = re.compile(r"[\w ,.'\"-]{0,120}")


manager = PtyManager()
sockets: dict[str, set[WebSocket]] = {}
readers: dict[str, asyncio.Task] = {}
alarm_sockets: set[WebSocket] = set()
clipboard = {"data": ""}
socket_owner: dict[WebSocket, str | None] = {}  # open socket -> login session hash
last_cwd: dict[str, str] = {}
# shell integration reports the working directory (see shelldeck/integration)
CWD_REPORT = re.compile(r"\x1b\]633;P;Cwd=([^\x07\x1b]+)(?:\x07|\x1b\\)")


def err(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


# `sd share`: the tunnel's public hostname, the sha256 of its one unused link token (None once used),
# when that link expires, when the CLI last renewed the lease, and one grant per browser that opened
# a link: sha256(cookie) -> {id, ip, agent, at, state: pending|ok|denied, session}.
_share: dict = {}
SHARE_COOKIE = "sd_share"
SHARE_LEASE = 60  # seconds without a CLI heartbeat before the share closes itself
LINK_TTL = 600  # a link must be opened within 10 minutes, once
APPROVAL_TTL = 300  # a pending device the host didn't answer is dropped after 5 minutes


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _share_on() -> bool:
    """A share exists and its `sd share` process is still renewing it."""
    return bool(_share) and time.time() - _share["seen"] < SHARE_LEASE


def _share_link_ok(token: str) -> bool:
    key = _share.get("link")
    return bool(key) and time.time() < _share["link_until"] and hmac.compare_digest(_sha(token), key)


def _grant(conn) -> dict | None:
    cookie = conn.cookies.get(SHARE_COOKIE)
    return _share.get("grants", {}).get(_sha(cookie)) if cookie and _share else None


def _via_share(conn) -> bool:
    return _share_on() and conn.headers.get("host", "").rsplit(":", 1)[0] == _share["host"]


def _trusted(conn) -> bool:
    """Same-origin check. Browsers always send Origin cross-site; the CLI sends none.
    On the share tunnel's host only /share/ pages are open; the rest needs a host-approved grant."""
    headers = conn.headers
    host = headers.get("host", "")
    if _via_share(conn):
        g = _grant(conn)
        if not (conn.url.path.startswith("/share/") or (g and g["state"] == "ok")):
            return False
    elif ALLOWED_HOSTS is not None and host.rsplit(":", 1)[0] not in ALLOWED_HOSTS:
        return False
    origin = headers.get("origin")
    return not origin or urlsplit(origin).netloc == host


def _to_utc(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).isoformat()
    except ValueError:
        return iso


def get_settings() -> dict:
    s = {k: db.get_setting(k, v) for k, v in SETTINGS_DEFAULTS.items()}
    if s["default_shell"] not in shells.kinds():  # e.g. a Windows db opened on Linux
        s["default_shell"] = shells.default_kind()
    return s


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if code := auth.init_auth():
        log.warning("no password yet; remote setup code: %s", code)
    await sched.start()
    sched.set_alarm_callback(_broadcast_alarm)
    await sched.reminder_service.sync_all()
    manager.store = db.config_dir() / "scrollback"
    manager.prune({s["id"] for s in db.list_sessions()})
    saver = asyncio.create_task(_save_scrollback())
    reaper = asyncio.create_task(_share_reaper())
    log.info("shelldeck %s started", VERSION)
    yield
    saver.cancel()
    reaper.cancel()
    stats.stop()
    sched.shutdown()
    for task in list(readers.values()):
        task.cancel()
    manager.save()
    for sid in list(manager.procs):
        manager.terminate(sid)


async def _save_scrollback() -> None:
    # ponytail: periodic flush; a hard power-off loses at most the last 15s of output
    while True:
        await asyncio.sleep(15)
        try:
            await asyncio.to_thread(manager.save)
        except OSError:
            log.warning("saving scrollback failed", exc_info=True)


async def _share_reaper() -> None:
    """Close a share whose `sd share` stopped renewing it (killed, window closed, crashed)."""
    while True:
        await asyncio.sleep(10)
        if _share and not _share_on():
            log.info("share lease expired; closing it")
            await _share_stop()


class RevalidatedStaticFiles(StaticFiles):
    """Browsers must revalidate (a cheap 304), or they keep running old JS after an upgrade."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(lifespan=lifespan, title="shelldeck", version=VERSION)
app.mount("/static", RevalidatedStaticFiles(directory=STATIC), name="static")

AUTH_EXEMPT = ("/api/auth/", "/api/health")
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def _client(conn) -> str:
    return conn.client.host if conn.client else "?"


def _is_local(conn) -> bool:
    """A request from the host itself (not through a reverse proxy)."""
    proxied = "x-forwarded-for" in conn.headers or "cf-connecting-ip" in conn.headers  # e.g. `sd share`
    return _client(conn) in LOOPBACK and not proxied


def _who(conn) -> tuple[str, str | None] | None:
    """("cli", None) for the host CLI, ("browser", session hash) for a login, else None."""
    if auth.cli_ok(conn.headers.get(auth.TOKEN_HEADER)):
        return ("cli", None)
    h = auth.session_hash(conn.cookies.get(auth.COOKIE))
    return ("browser", h) if h else None


# One device uses shelldeck at a time; the others stay signed in but idle until they log in again.
_active: dict[str, str | None] = {"h": None}


def _claim(h: str) -> bool:
    """True when browser session `h` may act: it is the active one, or no live session is."""
    if _active["h"] != h and auth.alive(_active["h"]):
        return False
    _active["h"] = h
    return True


def _denied() -> JSONResponse:
    return err("locked" if auth.has_password() else "setup_required", 401)


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def guard(request: Request, call_next):
    response = await _guarded(request, call_next)
    response.headers.update(SECURITY_HEADERS)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"  # terminal output, history, devices
    return response


async def _guarded(request: Request, call_next):
    if not _trusted(request):
        return err("forbidden_origin", 403)
    path = request.url.path
    request.state.session = None
    if path.startswith("/api/") and not path.startswith(AUTH_EXEMPT):
        who = _who(request)
        if not who:
            return _denied()
        request.state.session = who[1]
        if who[1] and not _claim(who[1]):
            return err("in_use", 423)
        if who[1] and request.method != "GET":  # polling GETs are not activity
            auth.touch(who[1])
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return err("internal_error", 500)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
async def health():
    return {"app": "shelldeck", "version": VERSION}


_server: uvicorn.Server | None = None


@app.post("/api/shutdown")
async def shutdown():
    if _server:
        _server.should_exit = True
    return {"status": "stopping"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _via(request: Request) -> str:
    if _via_share(request):
        return f"share:{_share['host']}"
    return "local" if _is_local(request) else "network"


async def _login_response(request: Request, body: dict, res=None):
    """Start a login session (cookie on `res`, JSON body by default). It becomes the active device:
    this browser's previous login is replaced and every other device goes idle."""
    if old := auth.session_hash(request.cookies.get(auth.COOKIE)):
        auth.end_session(old)
    token = auth.new_session(f"{_client(request)} {request.headers.get('user-agent', '')}", _via(request))
    h = auth.session_hash(token)
    _active["h"] = h
    if _via_share(request) and (g := _grant(request)):
        g["session"] = h  # revoking this login also voids the grant
    await _close_session_sockets(lambda owner: owner != h, IN_USE)
    res = res or JSONResponse(body)
    res.set_cookie(
        auth.COOKIE, token, httponly=True, samesite="strict", path="/",
        secure=request.url.scheme == "https", max_age=30 * 24 * 3600,
    )
    return res


def _too_many(request: Request) -> JSONResponse | None:
    wait = auth.blocked_for(_client(request))
    if wait:
        return JSONResponse({"error": "too_many_attempts", "retry_after": wait}, status_code=429, headers={"Retry-After": str(wait)})
    return None


@app.get("/api/auth/status")
async def auth_status(request: Request):
    who = _who(request)
    if who and who[1]:
        auth.touch(who[1])
    return {
        "has_password": auth.has_password(),
        "setup_code_required": not auth.has_password() and not _is_local(request),
        "authenticated": bool(who),
        "in_use_elsewhere": bool(who and who[1] and who[1] != _active["h"] and auth.alive(_active["h"])),
        "idle_timeout": auth.LOCK_TIMEOUT_SECONDS,
        "min_length": auth.MIN_PASSWORD,
    }


@app.post("/api/auth/setup")
async def auth_setup(request: Request, payload: dict):
    """First run: create the password. Remote clients also need the code printed on the host."""
    if auth.has_password():
        return err("password_already_set", 409)
    if blocked := _too_many(request):
        return blocked
    if not _is_local(request):
        ok = hmac.compare_digest(str(payload.get("code", "")).strip().lower(), (auth.setup_code() or "").lower())
        auth.record_attempt(_client(request), ok)
        if not ok:
            return err("invalid_setup_code", 403)
    password, confirm = str(payload.get("password", "")), str(payload.get("confirm", ""))
    if problem := auth.validate_new(password, confirm):
        return err(problem)
    auth.set_password(password)
    log.info("password created from %s", _client(request))
    return await _login_response(request, {"status": "ok"})


@app.post("/api/auth/login")
async def auth_login(request: Request, payload: dict):
    if not auth.has_password():
        return err("setup_required", 401)
    if blocked := _too_many(request):
        return blocked
    ok = await asyncio.to_thread(auth.check_password, str(payload.get("password", "")))
    auth.record_attempt(_client(request), ok)
    if not ok:
        log.warning("failed login from %s", _client(request))
        return err("invalid_password", 401)
    return await _login_response(request, {"status": "ok"})


_login_links: dict[str, float] = {}  # one-time code -> expiry


@app.post("/api/auth/login-link")
async def auth_login_link(request: Request):
    """Host CLI only: a one-time URL that logs a browser in (emergency unlock)."""
    if not auth.cli_ok(request.headers.get(auth.TOKEN_HEADER)):
        return err("host_cli_only", 403)
    code = secrets.token_urlsafe(24)
    _login_links[code] = time.time() + 300
    return {"path": f"/api/auth/link/{code}"}


@app.post("/api/share")
async def share_start(request: Request, payload: dict):
    """Host CLI only: open the gate for an `sd share` tunnel host; returns its first link."""
    if not auth.cli_ok(request.headers.get(auth.TOKEN_HEADER)):
        return err("host_cli_only", 403)
    if not auth.strong_password():
        return err("weak_password", 409)
    host = str(payload.get("host", "")).lower()
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
        return err("invalid_host")
    await _share_stop()
    _share.update(host=host, seen=time.time(), grants={})
    log.info("sharing via %s", host)
    return {"path": _new_share_link()}


def _new_share_link() -> str:
    """One link at a time: a new one voids the unused old one."""
    token = secrets.token_urlsafe(32)
    _share.update(link=_sha(token), link_until=time.time() + LINK_TTL)
    return f"/share/{token}"


@app.get("/api/share")
async def share_state(request: Request):
    """Host CLI heartbeat: renews the lease and lists devices waiting for approval."""
    if not auth.cli_ok(request.headers.get(auth.TOKEN_HEADER)):
        return err("host_cli_only", 403)
    if not _share:
        return {"host": None, "strong_password": auth.strong_password()}
    now = time.time()
    _share["seen"] = now
    grants = _share["grants"]
    for k in [k for k, g in grants.items() if g["state"] == "pending" and now - g["at"] > APPROVAL_TTL]:
        del grants[k]
    pending = [{"id": g["id"], "ip": g["ip"], "agent": g["agent"]} for g in grants.values() if g["state"] == "pending"]
    return {"host": _share["host"], "pending": pending, "strong_password": auth.strong_password()}


@app.post("/api/share/link")
async def share_link(request: Request):
    """Host CLI only: a fresh link for another device on the running share."""
    if not auth.cli_ok(request.headers.get(auth.TOKEN_HEADER)):
        return err("host_cli_only", 403)
    if not _share_on():
        return err("not_sharing", 404)
    return {"path": _new_share_link()}


@app.post("/api/share/decide")
async def share_decide(request: Request, payload: dict):
    """Host CLI only: allow or deny a device that opened a link."""
    if not auth.cli_ok(request.headers.get(auth.TOKEN_HEADER)):
        return err("host_cli_only", 403)
    g = next((g for g in _share.get("grants", {}).values() if g["id"] == payload.get("id")), None)
    if not g or g["state"] != "pending":
        return err("not_found", 404)
    g["state"] = "ok" if payload.get("allow") is True else "denied"
    log.info("share device %s from %s %s", g["id"], g["ip"], "allowed" if g["state"] == "ok" else "denied")
    return {"state": g["state"]}


@app.delete("/api/share")
async def share_stop(request: Request):
    if not auth.cli_ok(request.headers.get(auth.TOKEN_HEADER)):
        return err("host_cli_only", 403)
    await _share_stop()
    return {"status": "ok"}


async def _share_stop() -> None:
    """Close the gate and sign out every browser that logged in through the tunnel."""
    if not _share:
        return
    gone = db.delete_auth_sessions_via(f"share:{_share['host']}")
    _share.clear()
    await _revoke(gone)


_WAIT_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>shelldeck</title><meta name="referrer" content="no-referrer">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#09090b;color:#e4e4e7;
font:16px/1.5 Inter,"Segoe UI",system-ui,sans-serif;text-align:center;padding:24px;box-sizing:border-box}
p{max-width:28em;margin:.4em auto}b{color:#a78bfa}.m{color:#a1a1aa;font-size:14px}</style>
<main><p><b>shelldeck</b></p><p id="s">Waiting for the host to allow this device…</p>
<p class="m" id="m">Answer the prompt in the <code>sd share</code> window on the host.</p></main>
<script>
const say = (s, m) => { document.getElementById("s").textContent = s; document.getElementById("m").textContent = m; };
async function poll() {
  let st = "none";
  try { st = (await (await fetch("/share/status", { cache: "no-store" })).json()).state; } catch {}
  if (st === "ok") return location.replace("/");
  if (st === "denied") return say("The host denied this device.", "Ask for a new link if that was a mistake.");
  if (st !== "pending") return say("This request expired.", "Ask the host for a new link.");
  setTimeout(poll, 2000);
}
poll();
</script>"""


def _share_page(body: str, status: int = 200):
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


@app.get("/share/status")
async def share_status(request: Request):
    """The waiting page's poll: pending, ok, denied, or none."""
    g = _grant(request) if _via_share(request) else None
    return JSONResponse({"state": g["state"] if g else "none"}, headers={"Cache-Control": "no-store"})


@app.get("/share/{token}")
async def share_open(request: Request, token: str):
    """The QR link: works once, within LINK_TTL. It makes a pending grant that the host must allow in
    the `sd share` window; after that the usual password login takes over."""
    if not _via_share(request):
        return err("link_expired", 403)
    if g := _grant(request):  # this browser already opened a link (e.g. a reload)
        return RedirectResponse("/", status_code=303) if g["state"] == "ok" else _share_page(_WAIT_PAGE)
    if not _share_link_ok(token):
        return _share_page(_WAIT_PAGE.replace("poll();", 'say("This link has expired or was already used.", "Ask the host for a new one.");'), 403)
    _share["link"] = None  # one use
    cookie = secrets.token_urlsafe(32)
    _share["grants"][_sha(cookie)] = {
        "id": secrets.token_hex(4), "ip": _client(request), "agent": request.headers.get("user-agent", "")[:200],
        "at": time.time(), "state": "pending", "session": None,
    }
    log.info("share link opened from %s; waiting for the host", _client(request))
    res = _share_page(_WAIT_PAGE)
    res.set_cookie(SHARE_COOKIE, cookie, httponly=True, samesite="lax", secure=True, path="/", max_age=24 * 3600)
    return res


@app.get("/api/auth/link/{code}")
async def auth_use_link(request: Request, code: str):
    expiry = _login_links.pop(code, 0)
    if expiry < time.time() or not auth.has_password():
        return err("link_expired", 403)
    return await _login_response(request, {}, RedirectResponse("/", status_code=303))


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Lock this browser: end its session and close its sockets."""
    h = auth.session_hash(request.cookies.get(auth.COOKIE))
    if h:
        auth.end_session(h)
        await _close_session_sockets(lambda owner: owner == h)
    res = JSONResponse({"status": "locked"})
    res.delete_cookie(auth.COOKIE, path="/")
    return res


@app.get("/api/devices")
async def devices(request: Request):
    """Browsers signed in: where from, when, and which one is in use."""
    online: dict[str, int] = {}
    for owner in socket_owner.values():
        if owner:
            online[owner] = online.get(owner, 0) + 1
    rows = []
    for r in db.list_auth_sessions():
        h = r["token_hash"]
        if not auth.alive(h):
            continue
        ip, _, agent = (r["client"] or "").partition(" ")
        rows.append({
            "id": h, "ip": ip, "agent": agent, "via": r["via"] or "local",
            "created_at": r["created_at"], "last_seen": r["last_seen"],
            "active": h == _active["h"], "current": h == request.state.session, "sockets": online.get(h, 0),
        })
    return {"devices": rows, "share": _share["host"] if _share_on() else None}


@app.delete("/api/devices/{device_id}")
async def revoke_device(request: Request, device_id: str):
    """Sign one browser out, or with id "others" every browser but this one."""
    if device_id == "others":
        return await _revoke([d["token_hash"] for d in db.list_auth_sessions() if d["token_hash"] != request.state.session])
    return await _revoke([device_id])


async def _revoke(hashes: list[str]) -> dict:
    grants = _share.get("grants", {})
    for k in [k for k, g in grants.items() if g["session"] in hashes]:
        del grants[k]  # a revoked share device needs a new link and approval
    for h in hashes:
        auth.end_session(h)
        if _active["h"] == h:
            _active["h"] = None
    await _close_session_sockets(lambda owner: owner in hashes)
    return {"revoked": len(hashes)}


@app.put("/api/auth/password")
async def auth_password(request: Request, payload: dict):
    """Change the password (logged-in browser only). Signs out every other browser."""
    h = auth.session_hash(request.cookies.get(auth.COOKIE))
    if not h:
        return _denied()
    if blocked := _too_many(request):
        return blocked
    ok = await asyncio.to_thread(auth.check_password, str(payload.get("current", "")))
    auth.record_attempt(_client(request), ok)
    if not ok:
        return err("wrong_current_password", 403)
    password, confirm = str(payload.get("password", "")), str(payload.get("confirm", ""))
    if problem := auth.validate_new(password, confirm):
        return err(problem)
    auth.set_password(password)
    auth.end_other_sessions(keep=h)
    await _close_session_sockets(lambda owner: owner != h)
    log.info("password changed from %s", _client(request))
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.get("/api/settings")
async def read_settings():
    return get_settings()


@app.put("/api/settings")
async def write_settings(payload: dict):
    for key, value in payload.items():
        if key not in SETTINGS_DEFAULTS:
            return err(f"unknown_setting:{key}")
        value = str(value).strip()
        if key == "default_shell" and value not in shells.kinds():
            return err("invalid_shell")
        if key == "font_size" and not (value.isdigit() and 8 <= int(value) <= 32):
            return err("invalid_font_size")
        if key == "theme" and value not in ("dark", "light", "system"):
            return err("invalid_theme")
        if key == "layout_mode" and value not in ("tiled", "free"):
            return err("invalid_layout_mode")
        if key == "project_tint" and value not in ("on", "off"):
            return err("invalid_project_tint")
        if key == "terminal_theme" and value not in TERMINAL_THEMES:
            return err("invalid_terminal_theme")
        if key == "font_family" and not FONT_FAMILY.fullmatch(value):
            return err("invalid_font_family")
        if key == "editor" and value not in ("vscode", "system"):
            return err("invalid_editor")
    for key, value in payload.items():
        db.set_setting(key, str(value).strip())
    return get_settings()


@app.get("/api/stats")
async def get_stats():
    """System CPU/RAM/GPU plus usage per running terminal (shell and its child processes)."""
    return await asyncio.to_thread(stats.collect, _shell_pids())


def _shell_pids() -> dict[str, int]:
    return {sid: proc.pid for sid, proc in list(manager.procs.items()) if proc.isalive()}


@app.get("/api/agents")
async def list_agents():
    """Known AI coding agents (installed or not, with their models) and the terminals running one."""
    found = await asyncio.to_thread(agents.running, _shell_pids())
    by_id = {s["id"]: s for s in db.list_sessions_with_project()}
    running = [
        {"session_id": sid, "name": s.get("name"), "project_id": s.get("project_id"), "project": s.get("project_name"),
         "cwd": s.get("cwd"), "agent": key, "label": agents.AGENTS[key][0], "model": model}
        for sid, (key, model) in found.items() if (s := by_id.get(sid))
    ]
    return {"agents": await asyncio.to_thread(agents.catalog), "running": running}


@app.get("/api/shells")
async def list_shells():
    return {
        "kinds": shells.kinds(),
        "labels": {k: shells.label(k) for k in shells.kinds()},
        "default": shells.default_kind(),
        "available": shells.available(),
        "wsl_distros": await asyncio.to_thread(shells.wsl_distros),
    }


# ---------------------------------------------------------------------------
# Projects + filesystem
# ---------------------------------------------------------------------------


@app.get("/api/projects")
async def list_projects():
    alive = set(manager.list_alive())
    projects = db.list_projects()
    for p in projects:
        p["exists"] = Path(p["path"]).is_dir()
        p["has_git"] = p["exists"] and gitgraph.has_git(p["path"])
        p["sessions"] = db.list_sessions(p["id"])
        for s in p["sessions"]:
            s["alive"] = s["id"] in alive
    return {"projects": projects}


@app.get("/api/projects/{project_id}/git/log")
async def project_git_log(project_id: str, limit: int = 300):
    project = db.get_project(project_id)
    if not project:
        return err("project_not_found", 404)
    try:
        return await asyncio.to_thread(gitgraph.get_log, project["path"], max(1, min(limit, 5000)))
    except gitgraph.GitError as e:
        return err(f"git_log_failed:{e}")


@app.post("/api/projects/{project_id}/git/checkout")
async def project_git_checkout(project_id: str, payload: dict):
    project = db.get_project(project_id)
    if not project:
        return err("project_not_found", 404)
    ref = str(payload.get("ref") or "").strip()
    if not ref:
        return err("ref_required")
    ok, message = await asyncio.to_thread(gitgraph.checkout, project["path"], ref)
    if not ok:
        return err(f"git_checkout_failed:{message}")
    return {"status": "ok", "message": message}


@app.post("/api/projects")
async def add_project(payload: dict):
    raw = str(payload.get("path") or "").strip()
    if not raw:
        return err("path_required")
    path = Path(raw).expanduser()
    if not path.is_dir():
        return err("not_a_directory")
    return db.ensure_project(str(path))


@app.patch("/api/projects/{project_id}")
async def rename_project(project_id: str, payload: dict):
    name = str(payload.get("name") or "").strip()
    if not name:
        return err("name_required")
    return db.rename_project(project_id, name) or err("project_not_found", 404)


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    if not db.get_project(project_id):
        return err("project_not_found", 404)
    for s in db.list_sessions(project_id):
        await _end_session(s["id"])
    for j in db.list_jobs():
        if j["project_id"] == project_id:
            sched.scheduler_service.remove(j["id"])
    db.delete_project(project_id)
    return {"status": "ok"}


@app.get("/api/fs/dirs")
async def list_dirs(path: str = ""):
    """Folder browser for the add-project dialog. Empty path = home + drives."""
    if not path:
        roots = [str(Path.home())]
        roots += os.listdrives() if hasattr(os, "listdrives") else ["/"]
        return {"path": "", "parent": None, "dirs": roots, "sep": os.sep}
    p = Path(path).expanduser()
    if not p.is_dir():
        return err("not_a_directory")
    try:
        dirs = sorted(
            (e.name for e in os.scandir(p) if e.is_dir() and not e.name.startswith(("$", "."))),
            key=str.lower,
        )
    except OSError:
        return err("permission_denied", 403)
    p = p.resolve()
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else "", "dirs": dirs, "sep": os.sep}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@app.get("/api/sessions")
async def list_sessions():
    alive = set(manager.list_alive())
    sessions = db.list_sessions_with_project()
    for s in sessions:
        s["alive"] = s["id"] in alive
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    session = db.get_session(session_id)
    if not session:
        return err("session_not_found", 404)
    session["project"] = db.get_project(session["project_id"]) or {}
    session["alive"] = manager.get(session_id) is not None
    return {"session": session}


@app.post("/api/sessions")
async def create_session(payload: dict):
    project_id = payload.get("project_id")
    if project_id:
        project = db.get_project(project_id)
        if not project:
            return err("project_not_found", 404)
    else:
        cwd = str(payload.get("cwd") or "").strip()
        if not cwd or not Path(cwd).is_dir():
            return err("not_a_directory")
        project = db.ensure_project(cwd)
    shell = str(payload.get("shell") or "").strip()
    if shell and shell not in shells.kinds():
        return err("invalid_shell")
    cwd = str(payload.get("cwd") or project["path"])
    name = str(payload.get("name") or "").strip()
    if not name:
        kind = shell or get_settings()["default_shell"]
        same = [x for x in db.list_sessions(project["id"]) if (x["shell"] or get_settings()["default_shell"]) == kind]
        name = f"{shells.label(kind)} {len(same) + 1}"
    session = db.add_session(project["id"], cwd=cwd, shell=shell, name=name)
    session["project_path"] = project["path"]
    return session


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, payload: dict):
    name = str(payload.get("name") or "").strip()
    if not name:
        return err("name_required")
    return db.rename_session(session_id, name) or err("session_not_found", 404)


def _record_command(session_id: str, msg: dict) -> None:
    command = str(msg.get("cmd") or "").strip()[:4000]
    started = str(msg.get("at") or "")[:40]
    if not command or not started:
        return
    exit_code = msg.get("exit")
    duration = msg.get("ms")
    session = db.get_session(session_id) or {}
    db.add_command(
        session_id, session.get("project_id"), session.get("cwd"), command,
        exit_code if isinstance(exit_code, int) else None,
        duration if isinstance(duration, int) and duration >= 0 else None,
        started,
    )


@app.get("/api/history")
async def history(q: str = "", project_id: str = "", failed: bool = False, limit: int = 300):
    return {"commands": db.list_commands(q.strip(), project_id or None, failed, max(1, min(limit, 2000)))}


@app.delete("/api/history")
async def clear_history():
    db.clear_commands()
    return {"status": "ok"}


# strip terminal control sequences so saved output can be searched as text
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|[\x00-\x08\x0b-\x1f\x7f]")


def _search(q: str, per_session: int = 20, total: int = 400) -> list[dict]:
    needle = q.casefold()
    results = []
    for s in db.list_sessions_with_project():
        text = ANSI.sub("", manager.searchable(s["id"]).replace("\r\n", "\n"))
        hits = [{"line": no, "text": line.strip()[:300]} for no, line in enumerate(text.split("\n")) if needle in line.casefold()]
        if hits:
            results.append({"session_id": s["id"], "count": len(hits), "hits": hits[-per_session:]})
            total -= min(len(hits), per_session)
            if total <= 0:
                break
    return results


@app.get("/api/search")
async def search_terminals(q: str = ""):
    """Search the saved output of every terminal (live or not)."""
    q = q.strip()
    if len(q) < 2:
        return {"results": []}
    return {"results": await asyncio.to_thread(_search, q[:200])}


def _resolve(session_id: str, raw: str) -> Path | None:
    """A path from terminal output, relative to the session's current folder; None unless it is a file."""
    raw = raw.strip().strip("'\"")
    if not raw or len(raw) > 1000:
        return None
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path((db.get_session(session_id) or {}).get("cwd") or ".") / path
        path = path.resolve()
        return path if path.is_file() else None
    except (OSError, ValueError):
        return None


@app.post("/api/fs/check")
async def check_paths(payload: dict):
    """Which of these path candidates are real files (drives clickable paths in terminal output)."""
    sid = str(payload.get("session_id") or "")
    raws = [str(x) for x in (payload.get("paths") or [])][:50]
    found = await asyncio.to_thread(lambda: [r for r in raws if _resolve(sid, r)])
    return {"files": found}


# the default app for these would run them rather than show them
EXECUTABLE = {".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi",
              ".msc", ".scr", ".hta", ".lnk", ".url", ".reg", ".cpl", ".jar", ".sh", ".app", ".desktop", ".appimage"}


def _editor_target(path: Path, line: int, col: int) -> str | None:
    if get_settings()["editor"] == "vscode":
        url = "vscode://file/" + quote(path.as_posix().lstrip("/"), safe="/:")
        return url + (f":{line}" + (f":{col}" if col else "") if line else "")
    if path.suffix.lower() in EXECUTABLE or (sys.platform != "win32" and os.access(path, os.X_OK)):
        return None
    return str(path)


@app.post("/api/open")
async def open_file(payload: dict):
    """Open a file from terminal output in VS Code (at the line) or the system's default app."""
    path = await asyncio.to_thread(_resolve, str(payload.get("session_id") or ""), str(payload.get("path") or ""))
    if not path:
        return err("file_not_found", 404)
    target = _editor_target(path, _clamp(payload.get("line"), 0, 10_000_000, 0), _clamp(payload.get("col"), 0, 100_000, 0))
    if not target:
        return err("refusing_to_run_executable")
    try:
        if sys.platform == "win32":
            os.startfile(target)  # a file path or vscode:// URL, never a command line
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target], start_new_session=True)
    except OSError as e:
        return err(f"open_failed:{e.strerror or e}")
    return {"status": "ok", "path": str(path)}


@app.get("/api/sessions/{session_id}/screen")
async def session_screen(session_id: str, lines: int = 60):
    """The last lines of a terminal as plain text (what an agent in another terminal reads)."""
    if not db.get_session(session_id):
        return err("session_not_found", 404)
    # raw ConPTY/TUI output is cursor-addressed; ask an open browser for its rendered screen first
    sb = manager.scrollback.get(session_id)
    if sb and (live := list(sockets.get(session_id, ()))):
        before = sb.snapshot
        for ws in live:
            try:
                await ws.send_text('{"type":"snapshot"}')
            except Exception:  # noqa: BLE001 - a closing socket
                pass
        for _ in range(20):
            if sb.snapshot is not before:
                break
            await asyncio.sleep(0.05)
    text = ANSI.sub("", manager.searchable(session_id).replace("\r\n", "\n"))
    rows = [line.rstrip() for line in text.split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    return {"text": "\n".join(rows[-max(1, min(lines, 2000)):])}


@app.post("/api/sessions/{session_id}/input")
async def session_input(session_id: str, payload: dict):
    """Type text into a running terminal, then Enter unless enter is false."""
    text = str(payload.get("text") or "")[:20000]
    if not text:
        return err("text_required")
    if not manager.write(session_id, text):
        return err("not_running", 409)
    if payload.get("enter", True):
        await asyncio.sleep(0.3)  # TUIs (Claude Code, Codex) treat text+CR in one burst as a paste, not a submit
        manager.write(session_id, "\r")
    return {"status": "ok"}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    await _end_session(session_id)
    db.delete_session(session_id)
    manager.forget(session_id)
    return {"status": "ok"}


async def _end_session(session_id: str) -> None:
    task = readers.pop(session_id, None)
    if task:
        task.cancel()
    manager.terminate(session_id)
    for ws in list(sockets.pop(session_id, ())):
        await _close(ws)


async def _close(ws: WebSocket, code: int = 1000) -> None:
    try:
        await ws.close(code=code)
    except (RuntimeError, WebSocketDisconnect):
        pass


IN_USE = 4423  # socket close code: another device took over


async def _close_session_sockets(match, code: int = 1008) -> None:
    """Close sockets whose login (session hash; None for the CLI) matches."""
    for ws, owner in list(socket_owner.items()):
        if owner and match(owner):
            await _close(ws, code)


# ---------------------------------------------------------------------------
# Clipboard + bookmarks
# ---------------------------------------------------------------------------


@app.post("/api/clipboard")
async def set_clipboard(payload: dict):
    clipboard["data"] = str(payload.get("data", ""))
    return {"status": "ok"}


@app.get("/api/clipboard")
async def get_clipboard():
    return clipboard


@app.get("/api/bookmarks")
async def list_bookmarks():
    return {"bookmarks": db.list_bookmarks()}


def _bookmark_fields(payload: dict) -> tuple[str, str, str | None] | JSONResponse:
    name = str(payload.get("name") or "").strip()
    command = str(payload.get("command") or "").strip()
    project_id = payload.get("project_id") or None
    if not name or not command:
        return err("name_and_command_required")
    if project_id and not db.get_project(project_id):
        return err("project_not_found", 404)
    return name, command, project_id


@app.post("/api/bookmarks")
async def create_bookmark(payload: dict):
    fields = _bookmark_fields(payload)
    if isinstance(fields, JSONResponse):
        return fields
    return db.add_bookmark(*fields)


@app.put("/api/bookmarks/{bookmark_id}")
async def update_bookmark(bookmark_id: str, payload: dict):
    if not db.get_bookmark(bookmark_id):
        return err("bookmark_not_found", 404)
    fields = _bookmark_fields(payload)
    if isinstance(fields, JSONResponse):
        return fields
    return db.update_bookmark(bookmark_id, *fields)


@app.delete("/api/bookmarks/{bookmark_id}")
async def delete_bookmark(bookmark_id: str):
    if not db.get_bookmark(bookmark_id):
        return err("bookmark_not_found", 404)
    db.delete_bookmark(bookmark_id)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# WebSockets
# ---------------------------------------------------------------------------


async def _send_all(session_id: str, message: dict) -> None:
    text = json.dumps(message)
    for ws in list(sockets.get(session_id, ())):
        try:
            await ws.send_text(text)
        except (RuntimeError, WebSocketDisconnect):
            sockets.get(session_id, set()).discard(ws)


async def _pump(session_id: str) -> None:
    """Single reader per session; fans output out to every attached socket."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(data: str | None) -> None:  # called from the reader thread
        try:
            loop.call_soon_threadsafe(queue.put_nowait, data)
        except RuntimeError:  # loop closed at shutdown
            pass

    manager.stream(session_id, emit)
    try:
        done = False
        while not done:
            data = await queue.get()
            if data is None:
                break
            # merge chunks that queued up while we were sending: fewer, larger messages
            while not queue.empty():
                more = queue.get_nowait()
                if more is None:
                    done = True
                    break
                data += more
            manager.record(session_id, data)
            if (m := CWD_REPORT.findall(data)) and m[-1] != last_cwd.get(session_id):
                last_cwd[session_id] = m[-1]
                db.update_session_cwd(session_id, m[-1])
            await _send_all(session_id, {"type": "output", "data": data})
        log.info("session %s exited", session_id)
        await _send_all(session_id, {"type": "exit"})
        for ws in list(sockets.pop(session_id, ())):
            await _close(ws)
    finally:
        readers.pop(session_id, None)
        manager.terminate(session_id)


def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _attach(session: dict, rows: int, cols: int) -> None:
    sid = session["id"]
    if manager.get(sid):
        manager.resize(sid, rows, cols)
    else:
        settings = get_settings()
        manager.create(
            sid,
            cwd=session.get("cwd"),
            shell=session.get("shell") or settings["default_shell"],
            rows=rows,
            cols=cols,
            distro=settings["wsl_distro"] or None,
        )
    if sid not in readers:
        readers[sid] = asyncio.create_task(_pump(sid))
    db.update_session_size(sid, cols, rows)


@app.websocket("/ws/alarms")
async def alarms_ws(ws: WebSocket):
    who = _who(ws) if _trusted(ws) else None
    if not who:
        await ws.close(code=1008)
        return
    if who[1] and not _claim(who[1]):
        await ws.close(code=IN_USE)
        return
    await ws.accept()
    alarm_sockets.add(ws)
    socket_owner[ws] = who[1]
    try:
        await _send_alarm_snapshot(ws)
        while True:
            if (await ws.receive_text()) == '{"type":"ping"}':
                await ws.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        alarm_sockets.discard(ws)
        socket_owner.pop(ws, None)


@app.websocket("/ws/{session_id}")
async def terminal_ws(ws: WebSocket, session_id: str):
    who = _who(ws) if _trusted(ws) else None
    if not who:
        await ws.close(code=1008)
        return
    if who[1] and not _claim(who[1]):
        await ws.close(code=IN_USE)
        return
    login = who[1]
    session = db.get_session(session_id)
    if not session:
        await ws.close(code=4404)
        return
    await ws.accept()
    sockets.setdefault(session_id, set()).add(ws)
    socket_owner[ws] = login
    try:
        if history := manager.history(session_id):
            await ws.send_text(json.dumps({"type": "output", "data": history, "replay": True}))
        while True:
            raw = await ws.receive_text()
            if login and not auth.session_hash(ws.cookies.get(auth.COOKIE)):
                await _close(ws, 1008)  # logged out, idle too long, or password changed elsewhere
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "ping":  # client heartbeat; not user activity
                await ws.send_text('{"type":"pong"}')
                continue
            if login and mtype == "input":
                auth.touch(login)
            if mtype == "resize":
                rows = _clamp(msg.get("rows"), 2, 500, 24)
                cols = _clamp(msg.get("cols"), 2, 1000, 120)
                try:
                    _attach(session, rows, cols)
                except Exception:  # noqa: BLE001 - spawn failures come from winpty/OS
                    log.exception("spawn failed for session %s", session_id)
                    await ws.send_text(json.dumps({"type": "error", "message": "failed to start shell"}))
            elif mtype == "input":
                manager.write(session_id, str(msg.get("data", "")))
            elif mtype == "snapshot":
                manager.snapshot(session_id, str(msg.get("data", "")))
            elif mtype == "command":
                _record_command(session_id, msg)
            elif mtype == "clipboard_set":
                clipboard["data"] = str(msg.get("data", ""))
            elif mtype == "clipboard_get":
                await ws.send_text(json.dumps({"type": "clipboard", "data": clipboard["data"]}))
    except WebSocketDisconnect:
        pass
    finally:
        sockets.get(session_id, set()).discard(ws)
        socket_owner.pop(ws, None)


async def _broadcast_alarm(task: dict) -> None:
    data = json.dumps({"type": "alarm", "task": _serialize_task(task)})
    for ws in list(alarm_sockets):
        try:
            await ws.send_text(data)
        except (RuntimeError, WebSocketDisconnect):
            alarm_sockets.discard(ws)


async def _send_alarm_snapshot(ws: WebSocket) -> None:
    alarms = [_serialize_task(t) for t in db.list_active_alarms()]
    await ws.send_text(json.dumps({"type": "alarm_snapshot", "alarms": alarms}))


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------


def _serialize_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "name": job["name"],
        "project_id": job["project_id"],
        "command": job["command"],
        "cron": job["cron"],
        "timezone": job.get("timezone", "UTC"),
        "enabled": bool(job.get("enabled")),
        "timeout_seconds": job.get("timeout_seconds", 60),
        "next_run_on": job.get("next_run_on"),
        "last_run_on": job.get("last_run_on"),
        "last_status": job.get("last_status", "never_run"),
        "last_error": job.get("last_error"),
        "last_duration_ms": job.get("last_duration_ms"),
        "total_runs": job.get("total_runs", 0),
        "successful_runs": job.get("successful_runs", 0),
        "failed_runs": job.get("failed_runs", 0),
    }


@app.get("/api/schedule/jobs")
async def list_schedule_jobs():
    return {"jobs": [_serialize_job(j) for j in db.list_jobs()]}


@app.post("/api/schedule/jobs")
async def create_schedule_job(payload: dict):
    name = (payload.get("name") or "").strip()
    project_id = payload.get("project_id") or ""
    command = (payload.get("command") or "").strip()
    cron = (payload.get("cron") or "").strip()
    timezone = (payload.get("timezone") or "UTC").strip()
    enabled = payload.get("enabled", True)
    timeout = int(payload.get("timeout_seconds", 60) or 60)

    if not name or not command or not cron:
        return err("name, command, and cron are required")
    if not db.get_project(project_id):
        return err("project_not_found", 404)
    try:
        sched.cron_trigger(cron, timezone)
    except ValueError as e:
        return err(str(e))

    job = db.add_job(name, project_id, command, cron, timezone, bool(enabled), timeout)
    await sched.scheduler_service.sync_job(job)
    return _serialize_job(db.get_job(job["id"]))


@app.get("/api/schedule/jobs/{job_id}")
async def get_schedule_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        return err("job_not_found", 404)
    return _serialize_job(job)


@app.put("/api/schedule/jobs/{job_id}")
async def update_schedule_job(job_id: str, payload: dict):
    job = db.get_job(job_id)
    if not job:
        return err("job_not_found", 404)

    name = payload.get("name")
    project_id = payload.get("project_id")
    command = payload.get("command")
    cron = payload.get("cron")
    timezone = payload.get("timezone")
    enabled = payload.get("enabled")
    timeout = payload.get("timeout_seconds")

    if cron is not None or timezone is not None:
        cron = (cron or job["cron"]).strip()
        tz = (timezone or job.get("timezone", "UTC")).strip()
        try:
            sched.cron_trigger(cron, tz)
        except ValueError as e:
            return err(str(e))

    if project_id is not None and not db.get_project(project_id):
        return err("project_not_found", 404)

    kwargs = {}
    if name is not None:
        kwargs["name"] = name.strip()
    if project_id is not None:
        kwargs["project_id"] = project_id
    if command is not None:
        kwargs["command"] = command.strip()
    if cron is not None:
        kwargs["cron"] = cron
    if timezone is not None:
        kwargs["timezone"] = timezone.strip()
    if enabled is not None:
        kwargs["enabled"] = bool(enabled)
    if timeout is not None:
        kwargs["timeout_seconds"] = int(timeout or 60)

    updated = db.update_job(job_id, **kwargs)
    if not updated:
        return err("job_not_found", 404)
    await sched.scheduler_service.sync_job(updated)
    return _serialize_job(db.get_job(job_id))


@app.delete("/api/schedule/jobs/{job_id}")
async def delete_schedule_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        return err("job_not_found", 404)
    sched.scheduler_service.remove(job_id)
    db.delete_job(job_id)
    return {"status": "ok"}


@app.post("/api/schedule/jobs/{job_id}/run")
async def run_schedule_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        return err("job_not_found", 404)
    sched.scheduler_service.run_now(job_id)
    return {"status": "queued", "job_id": job_id}


@app.post("/api/schedule/jobs/{job_id}/toggle")
async def toggle_schedule_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        return err("job_not_found", 404)
    updated = db.toggle_job(job_id)
    if not updated:
        return err("job_not_found", 404)
    await sched.scheduler_service.sync_job(updated)
    return _serialize_job(db.get_job(job_id))


@app.get("/api/schedule/jobs/{job_id}/runs")
async def list_schedule_job_runs(job_id: str, limit: int = 50):
    job = db.get_job(job_id)
    if not job:
        return err("job_not_found", 404)
    return {"runs": db.list_job_runs(job_id, limit)}


# ---------------------------------------------------------------------------
# Tasks (Kanban)
# ---------------------------------------------------------------------------


def _serialize_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "title": task["title"],
        "description": task.get("description", ""),
        "column": task.get("board_column", "backlog"),
        "priority": task.get("priority", "medium"),
        "due_at": task.get("due_at"),
        "reminder_at": task.get("reminder_at"),
        "reminder_acknowledged": bool(task.get("reminder_acknowledged")),
        "tags": task.get("tags", ""),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }


@app.get("/api/tasks/alarms")
async def list_task_alarms():
    return {"alarms": [_serialize_task(t) for t in db.list_active_alarms()]}


@app.get("/api/tasks")
async def list_tasks():
    return {"tasks": [_serialize_task(t) for t in db.list_tasks()]}


@app.post("/api/tasks")
async def create_task(payload: dict):
    title = (payload.get("title") or "").strip()
    if not title:
        return err("title_required")
    task = db.add_task(
        title=title,
        description=(payload.get("description") or "").strip(),
        board_column=(payload.get("column") or "backlog").strip(),
        priority=(payload.get("priority") or "medium").strip(),
        due_at=_to_utc(payload.get("due_at")),
        reminder_at=_to_utc(payload.get("reminder_at")),
        tags=(payload.get("tags") or "").strip(),
    )
    sched.reminder_service.sync_task(task["id"])
    return _serialize_task(task)


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = db.get_task(task_id)
    if not task:
        return err("task_not_found", 404)
    return _serialize_task(task)


@app.put("/api/tasks/{task_id}")
async def update_task(task_id: str, payload: dict):
    task = db.get_task(task_id)
    if not task:
        return err("task_not_found", 404)

    updated = db.update_task(
        task_id,
        title=payload.get("title"),
        description=payload.get("description"),
        board_column=payload.get("column"),
        priority=payload.get("priority"),
        due_at=_to_utc(payload.get("due_at")),
        reminder_at=_to_utc(payload.get("reminder_at")),
        reminder_acknowledged=payload.get("reminder_acknowledged"),
        tags=payload.get("tags"),
    )
    if not updated:
        return err("task_not_found", 404)
    sched.reminder_service.sync_task(task_id)
    return _serialize_task(updated)


@app.post("/api/tasks/{task_id}/move")
async def move_task(task_id: str, payload: dict):
    column = (payload.get("column") or "").strip()
    if not column:
        return err("column_required")
    updated = db.move_task(task_id, column)
    if not updated:
        return err("task_not_found", 404)
    sched.reminder_service.sync_task(task_id)
    return _serialize_task(updated)


@app.post("/api/tasks/{task_id}/ack")
async def ack_task_reminder(task_id: str):
    updated = db.ack_reminder(task_id)
    if not updated:
        return err("task_not_found", 404)
    sched.reminder_service.sync_task(task_id)
    return _serialize_task(updated)


@app.post("/api/tasks/{task_id}/snooze")
async def snooze_task_reminder(task_id: str, payload: dict):
    minutes = int(payload.get("minutes", 15) or 15)
    updated = db.snooze_reminder(task_id, minutes)
    if not updated:
        return err("task_not_found", 404)
    sched.reminder_service.sync_task(task_id)
    return _serialize_task(updated)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    task = db.get_task(task_id)
    if not task:
        return err("task_not_found", 404)
    sched.reminder_service.remove_task(task_id)
    db.delete_task(task_id)
    return {"status": "ok"}


def _setup_logging() -> None:
    handler = RotatingFileHandler(
        db.config_dir() / "shelldeck.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def run(host: str = "127.0.0.1", port: int = 5455, certfile: str | None = None, keyfile: str | None = None) -> None:
    global ALLOWED_HOSTS, _server
    if host in ("0.0.0.0", "::"):
        ALLOWED_HOSTS = None
    elif ALLOWED_HOSTS is not None:
        ALLOWED_HOSTS.add(host)
    _setup_logging()
    scheme = "https" if certfile else "http"
    (db.config_dir() / "server.json").write_text(json.dumps({"port": port, "scheme": scheme, "host": host}), encoding="utf-8")
    if code := (None if auth.has_password() else auth.setup_code()):
        where = "this machine's address" if host in ("0.0.0.0", "::") else host
        print(f"shelldeck: no password set yet. Open {scheme}://{where}:{port} to create one.", flush=True)
        print(f"shelldeck: remote setup code (only needed from another machine): {code}", flush=True)
    _server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=port, log_level="warning",
        ssl_certfile=certfile, ssl_keyfile=keyfile,
        ws_ping_interval=20, ws_ping_timeout=20,  # drop dead connections; the UI reconnects
    ))
    _server.run()
