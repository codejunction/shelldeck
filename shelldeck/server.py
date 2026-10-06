import asyncio
import collections
import functools
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote, urlsplit

import psutil
import segno
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent_commands, detection, smart_recall
from . import agent_state as lifecycle
from . import agents, auth, context, db, gitgraph, integrations, plugins, remotes, share, shells, stats, team
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
    "agent_resume": "ask",
    "ask_agent": "auto",  # auto | claude | codex | gemini | devin: who writes `?` / `sd ask` commands
    "ask_model": "",  # that agent's model; empty = its smallest
    "recall_agent": "off",  # off | auto | claude | codex | gemini | devin: whose small model widens `sd recall --smart`  # never | ask | auto: start a stored agent session again when its terminal comes back
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
# sid -> the shell is at its prompt (shell integration's 133;B seen, no Enter since). Sent to a browser on connect:
# replayed scrollback has no prompt marks, so a page that just loaded can't tell by itself (the `?` menu needs it)
at_prompt: dict[str, bool] = {}
# sub-agent terminal -> its output since the last question check, and when it last printed
ask_buf: dict[str, str] = {}
last_out_at: dict[str, float] = {}
QUIET_S = 2.5  # a question counts once the sub-agent's screen stops changing
BOX = re.compile("[\u2500-\u257f]+")  # rules and frames a TUI draws around its prompts
REASK_S = 120  # the same question redrawn within this long is not forwarded again
asked_before: dict[str, tuple[str, float]] = {}  # sub-agent -> (last question forwarded, when)
# agent "needs you" detection (mirrors updateAgentStates in static/app.js): every terminal's last output,
# the start and text of its current burst, and each agent terminal's state: working | approval | idle
out_at: dict[str, float] = {}
busy_since: dict[str, float] = {}
burst: dict[str, str] = {}
agent_state: dict[str, str] = {}
# server-owned lifecycle (agent_state.py): reports per terminal, the resolved status sent to clients, the agent
# kind and a generation that changes when a different agent process takes the terminal (pins waits)
reports = lifecycle.Registry()
agent_status: dict[str, dict] = {}
agent_kind: dict[str, tuple[str, int]] = {}
_generation = [0]
report_tokens: dict[str, str] = {}  # terminal -> SHELLDECK_AGENT_REPORT_TOKEN of its current process
REPORT_HEADER = "X-Shelldeck-Report-Token"
# server events for `sd events subscribe` / GET /api/events (SSE): a bounded in-memory log; ids only grow
EVENT_TYPES = ("agent.detected", "agent.state", "agent.command", "agent.exited", "agent.session_updated", "handoff.created", "handoff.completed",
               "integration.changed")
events_log: "collections.deque[dict]" = collections.deque(maxlen=500)
_event_seq = [0]
_event_waiters: list[asyncio.Event] = []
_state_changed: list[asyncio.Event] = []  # set and replaced on every status change; `sd agent wait` awaits it
AGENT_BUSY_S = 5  # output for at least this long counts as working (not a redraw or echo)
# a sub-agent's question relayed to its parent (_forward_question): on the parent's screen, not asking you
RELAYED = re.compile(r"\[shelldeck\]\s+Your\s+sub-agent[\s\S]*?really\s+their\s+call")
EXPO_PUSH = "https://exp.host/--/api/v2/push/send"
STOPPING = asyncio.Event()  # set at shutdown: dying terminals then don't message their parents
background: set[asyncio.Task] = set()  # fire-and-forget tasks, referenced so they aren't collected
# shell integration reports the working directory (see shelldeck/integration)
CWD_REPORT = re.compile(r"\x1b\]633;P;Cwd=([^\x07\x1b]+)(?:\x07|\x1b\\)")


def err(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


# The share: a cloudflared quick tunnel this server runs (share.py). _share holds the tunnel's public
# hostname, its one unused link token (None once used; kept so the host UI can show its QR) and when that
# expires, and one grant per browser that opened a link: sha256(cookie) -> {id, ip, agent, at,
# state: pending|ok|denied, session}. ponytail: the share runs until stopped, with no one watching;
# the UI shows a "Sharing" pill so it's never invisible.
_share: dict = {}
_share_lock = asyncio.Lock()  # held while cloudflared starts: "starting", and a second start waits
_share_sent: dict = {"key": None}  # the last share_state broadcast, so the watcher notices expiry
SHARE_COOKIE = "sd_share"
LINK_TTL = 600  # a link must be opened within 10 minutes, once
APPROVAL_TTL = 300  # a pending device the host didn't answer is dropped after 5 minutes
SELF_URL = {"url": "http://127.0.0.1:5455"}  # where the tunnel points; set by run()
CLIENT_HEADER = "x-shelldeck-client"  # "ios" asks for a remote login (see _login_response)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _share_on() -> bool:
    return bool(_share)


def _share_link_ok(token: str) -> bool:
    key = _share.get("token")
    return bool(key) and time.time() < _share["link_until"] and hmac.compare_digest(token, key)


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
    STOPPING.clear()
    db.init_db()
    if code := auth.init_auth():
        log.warning("no password yet; remote setup code: %s", code)
    await asyncio.to_thread(plugins.load, _plugins_off())
    await sched.start()
    sched.set_alarm_callback(_broadcast_alarm)
    await sched.reminder_service.sync_all()
    manager.store = db.config_dir() / "scrollback"
    manager.prune({s["id"] for s in db.list_sessions()})
    saver = asyncio.create_task(_save_scrollback())
    asker = asyncio.create_task(_watch_questions())
    watcher = asyncio.create_task(_watch_share())
    agent_watch = asyncio.create_task(_watch_agents())
    # every start refreshes the shelldeck skill of each agent CLI on PATH (dev/test homes skip it);
    # `sd install-skill --remove` turns this off
    if "SHELLDECK_HOME" not in os.environ and db.get_setting("agent_skills", "on") == "on":
        try:
            await asyncio.to_thread(team.install)
        except OSError:
            log.warning("installing agent skills failed", exc_info=True)
    log.info("shelldeck %s started", VERSION)
    yield
    saver.cancel()
    asker.cancel()
    watcher.cancel()
    agent_watch.cancel()
    STOPPING.set()
    if _share:
        await asyncio.to_thread(share.stop)
        _share.clear()
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


async def _watch_share() -> None:
    """Tell host browsers when the link or a pending device expires (every other change says so itself)."""
    while True:
        await asyncio.sleep(5)
        if _share and _share_key(_share_state()) != _share_sent["key"]:
            await _share_changed()


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
    """True when browser session `h` may act: it is the active one, or no live session is.
    Remote (iOS app) logins always may, and never take the slot."""
    if auth.remote(h):
        return True
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
    # an integration inside a terminal (hook script, OpenCode plugin) holds only its terminal's report token;
    # agent_report() checks it. Host-local only: never through a share tunnel or the network.
    report_only = path == "/api/agent-reports" and request.method == "POST" and REPORT_HEADER in request.headers and _is_local(request)
    if path.startswith("/api/") and not path.startswith(AUTH_EXEMPT) and not report_only:
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
    this browser's previous login is replaced and every other device goes idle. A remote login (the
    iOS app through an approved share grant) takes nothing over: the host browser stays in use."""
    if old := auth.session_hash(request.cookies.get(auth.COOKIE)):
        auth.end_session(old)
    g = _grant(request) if _via_share(request) else None
    remote = bool(g and g["state"] == "ok" and request.headers.get(CLIENT_HEADER, "").lower() == "ios")
    token = auth.new_session(f"{_client(request)} {request.headers.get('user-agent', '')}", _via(request),
                             "remote" if remote else "browser")
    h = auth.session_hash(token)
    if g:
        g["session"] = h  # revoking this login also voids the grant
    if not remote:
        _active["h"] = h
        await _close_session_sockets(lambda owner: owner != h and not auth.remote(owner), IN_USE)
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
        "in_use_elsewhere": bool(who and who[1] and not auth.remote(who[1]) and who[1] != _active["h"] and auth.alive(_active["h"])),
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


def _host(request: Request) -> bool:
    """The host: its CLI, or a browser on this machine that isn't coming through the tunnel."""
    return auth.cli_ok(request.headers.get(auth.TOKEN_HEADER)) or (_is_local(request) and not _via_share(request))


@functools.lru_cache(maxsize=4)
def _qr(url: str) -> str:
    return segno.make(url, error="l").svg_inline(scale=5, border=2, light="#fff")


def _share_state() -> dict:
    """ShareState (see plans/ios-app.md, API contract)."""
    host, token = _share.get("host"), _share.get("token")
    link = None
    if token and time.time() < _share["link_until"]:
        url = f"https://{host}/share/{token}"
        link = {"path": f"/share/{token}", "url": url, "expires_at": int(_share["link_until"]), "qr_svg": _qr(url)}
    return {
        "sharing": bool(_share), "starting": _share_lock.locked(), "url": f"https://{host}" if host else None,
        "link": link, "pending": _pending(), "strong_password": auth.strong_password(), "cloudflared": bool(share.command()),
        "terms_accepted": auth.terms_accepted(share.TERMS_VERSION), "terms": share.TERMS,
    }


def _share_key(state: dict) -> tuple:
    return state["sharing"], state["starting"], bool(state["link"]), tuple(p["id"] for p in state["pending"])


async def _share_changed() -> None:
    """Every host browser gets the new ShareState."""
    state = _share_state()
    _share_sent["key"] = _share_key(state)
    await _broadcast({"type": "share_state", "state": state}, host_only=True)


def _pending() -> list[dict]:
    """Devices waiting for the host; stale ones are dropped."""
    grants, now = _share.get("grants", {}), time.time()
    for k in [k for k, g in grants.items() if g["state"] == "pending" and now - g["at"] > APPROVAL_TTL]:
        del grants[k]
    return [{"id": g["id"], "ip": g["ip"], "agent": g["agent"], "at": g["at"]} for g in grants.values() if g["state"] == "pending"]


@app.post("/api/share")
async def share_start(request: Request):
    """Host only: start the tunnel (or return the running share) with a fresh link."""
    if not _host(request):
        return err("host_only", 403)
    if not auth.strong_password():
        return err("weak_password", 409)
    if not auth.terms_accepted(share.TERMS_VERSION):
        return err("terms_required", 409)
    if not share.command():
        return JSONResponse({"error": "no_cloudflared", "hint": share.HINT}, status_code=404)
    problem = None
    async with _share_lock:
        if not _share:
            await _share_changed()  # starting
            loop = asyncio.get_running_loop()

            def exited(host: str) -> None:  # cloudflared's reader thread
                loop.call_soon_threadsafe(_spawn, _tunnel_exited(host))

            try:
                host = await asyncio.to_thread(share.start, SELF_URL["url"], exited)
                _share.update(host=host, grants={})
                _new_share_link()
                log.info("sharing via %s", host)
            except TimeoutError:
                problem = err("tunnel_timeout", 504)
            except (OSError, RuntimeError):
                log.warning("cloudflared failed to start", exc_info=True)
                problem = err("tunnel_failed", 502)
    await _share_changed()
    return problem or _share_state()


def _spawn(coro) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    background.add(t)
    t.add_done_callback(background.discard)


async def _tunnel_exited(host: str) -> None:
    if _share.get("host") == host:
        log.warning("cloudflared exited; sharing stopped")
        await _share_stop()


def _new_share_link() -> None:
    """One link at a time: a new one voids the unused old one."""
    _share.update(token=secrets.token_urlsafe(32), link_until=time.time() + LINK_TTL)


@app.get("/api/share")
async def share_get(request: Request):
    """Host only: ShareState."""
    if not _host(request):
        return err("host_only", 403)
    return _share_state()


@app.post("/api/share/terms")
async def share_terms(request: Request, payload: dict):
    """Host only: accept the share terms (asked once, again when TERMS_VERSION changes)."""
    if not _host(request):
        return err("host_only", 403)
    if payload.get("accept") is not True:
        return err("accept_required")
    auth.accept_terms(share.TERMS_VERSION)
    return _share_state()


@app.post("/api/share/link")
async def share_link(request: Request):
    """Host only: a fresh link for another device on the running share."""
    if not _host(request):
        return err("host_only", 403)
    if not _share:
        return err("not_sharing", 404)
    _new_share_link()
    await _share_changed()
    return _share_state()


@app.post("/api/share/decide")
async def share_decide(request: Request, payload: dict):
    """Allow or deny a device that opened a link: the host CLI, or any logged-in browser not on the tunnel."""
    if _via_share(request):
        return err("host_only", 403)
    _pending()
    g = next((g for g in _share.get("grants", {}).values() if g["id"] == payload.get("id")), None)
    if not g or g["state"] != "pending":
        return err("not_found", 404)
    g["state"] = "ok" if payload.get("allow") is True else "denied"
    log.info("share device %s from %s %s", g["id"], g["ip"], "allowed" if g["state"] == "ok" else "denied")
    await _share_changed()
    return {"state": g["state"]}


@app.delete("/api/share")
async def share_stop(request: Request):
    if not _host(request):
        return err("host_only", 403)
    await _share_stop()
    return {"status": "ok"}


async def _share_stop() -> None:
    """Stop the tunnel and sign out every browser that logged in through it."""
    if not _share:
        return
    gone = db.delete_auth_sessions_via(f"share:{_share['host']}")
    _share.clear()
    await asyncio.to_thread(share.stop)
    await _revoke(gone)
    await _share_changed()


_WAIT_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>shelldeck</title><meta name="referrer" content="no-referrer">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#09090b;color:#e4e4e7;
font:16px/1.5 Inter,"Segoe UI",system-ui,sans-serif;text-align:center;padding:24px;box-sizing:border-box}
p{max-width:28em;margin:.4em auto}b{color:#a78bfa}.m{color:#a1a1aa;font-size:14px}</style>
<main><p><b>shelldeck</b></p><p id="s">Waiting for the host to allow this device…</p>
<p class="m" id="m">Allow it in shelldeck on the host, or in the <code>sd share</code> window.</p></main>
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
    """The QR link: works once, within LINK_TTL. It makes a pending grant that the host must allow (in a host
    browser or the `sd share` window); after that the usual password login takes over."""
    if not _via_share(request):
        return err("link_expired", 403)
    if g := _grant(request):  # this browser already opened a link (e.g. a reload)
        return RedirectResponse("/", status_code=303) if g["state"] == "ok" else _share_page(_WAIT_PAGE)
    if not _share_link_ok(token):
        return _share_page(_WAIT_PAGE.replace("poll();", 'say("This link has expired or was already used.", "Ask the host for a new one.");'), 403)
    _share["token"] = None  # one use
    cookie = secrets.token_urlsafe(32)
    _share["grants"][_sha(cookie)] = {
        "id": secrets.token_hex(4), "ip": _client(request), "agent": request.headers.get("user-agent", "")[:200],
        "at": time.time(), "state": "pending", "session": None,
    }
    log.info("share link opened from %s; waiting for the host", _client(request))
    await _share_changed()
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
            "id": h, "ip": ip, "agent": agent, "via": r["via"] or "local", "kind": r["kind"] or "browser",
            "created_at": r["created_at"], "last_seen": r["last_seen"],
            "active": h == _active["h"], "current": h == request.state.session, "sockets": online.get(h, 0),
        })
    on = _share_on()
    return {"devices": rows, "share": _share["host"] if on else None, "pending": _pending() if on and not _via_share(request) else []}


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
    db.delete_push_tokens(hashes)
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
        if key == "editor" and value not in ("vscode", "system", "shelldeck"):
            return err("invalid_editor")
        if key == "agent_resume" and value not in ("never", "ask", "auto"):
            return err("invalid_agent_resume")
        if key == "recall_agent" and value not in ("off", "auto", *smart_recall.PRIORITY):
            return err("invalid_recall_agent")
        if key == "ask_agent" and value not in ("auto", *smart_recall.PRIORITY):
            return err("invalid_ask_agent")
        if key == "ask_model" and value and not team.SAFE_MODEL.match(value):
            return err("invalid_ask_model")
    for key, value in payload.items():
        db.set_setting(key, str(value).strip())
    return get_settings()


@app.get("/api/stats")
async def get_stats():
    """System CPU/RAM/GPU plus usage per running terminal (shell and its child processes)."""
    return await asyncio.to_thread(stats.collect, _shell_pids())


@app.get("/api/sessions/{session_id}/processes")
async def session_processes(session_id: str):
    """The processes running in one terminal (below its shell), for sd ps."""
    proc = manager.get(session_id)
    if not proc or not proc.isalive():
        return err("not_running", 409)
    return {"shell_pid": proc.pid, "processes": await asyncio.to_thread(stats.processes, proc.pid)}


@app.post("/api/sessions/{session_id}/processes/{pid}/end")
async def end_process(session_id: str, pid: int, payload: dict | None = None):
    """End a process inside one terminal (and its children): terminate, then kill after 3s; `force` kills at once.
    Only that terminal's descendants qualify (use sd close for the terminal itself)."""
    proc = manager.get(session_id)
    if not proc or not proc.isalive():
        return err("not_running", 409)
    result = await asyncio.to_thread(stats.end, proc.pid, pid, bool((payload or {}).get("force")))
    if result in ("not_in_terminal", "gone"):
        return err(result, 404)
    log.info("process %s in %s %s", pid, session_id, result)
    return {"status": result}


def _shell_pids() -> dict[str, int]:
    return {sid: proc.pid for sid, proc in list(manager.procs.items()) if proc.isalive()}


@app.get("/api/agents")
async def list_agents():
    """Known AI coding agents (installed or not, with their models) and the terminals running one."""
    shells = _shell_pids()
    found = await asyncio.to_thread(agents.running, shells)
    by_id = {s["id"]: s for s in db.list_sessions_with_project()}
    running = [
        {"session_id": sid, "name": s.get("name"), "nick": s.get("nick"), "parent": s.get("parent"), "project_id": s.get("project_id"), "project": s.get("project_name"),
         "cwd": s.get("cwd"), "agent": key, "label": agents.AGENTS[key][0], "model": model, "state": agent_state.get(sid, "idle"),
         "status": agent_status.get(sid) or lifecycle.to_dict(lifecycle.Status(lifecycle.State.UNKNOWN)),
         "resumable": bool(db.get_agent_session(sid))}
        for sid, (key, model) in found.items() if (s := by_id.get(sid))
    ]
    return {
        "agents": await asyncio.to_thread(agents.catalog),
        "running": running,
        "outside": await asyncio.to_thread(agents.outside, _tree_pids(shells)),
        "devin_sessions": await asyncio.to_thread(agents.devin_sessions),
    }


@app.get("/api/integrations")
async def list_integrations():
    """Every supported agent integration and its available capability level."""
    installed = {item["key"] for item in await asyncio.to_thread(agents.catalog) if item["installed"]}
    return {"integrations": integrations.catalog(installed)}


@app.post("/api/integrations/{agent}")
async def install_integration(request: Request, agent: str):
    """Install shelldeck's lifecycle hook/plugin into an agent's config (host only: it writes files in your home)."""
    if not _host(request):
        return err("host_only", 403)
    try:
        out = await asyncio.to_thread(integrations.install, agent)
        _emit("integration.changed", {"agent": agent, "status": out["status"]})
        return out
    except KeyError:
        return err("unsupported_agent", 404)
    except (OSError, ValueError) as e:
        return JSONResponse({"error": "install_failed", "detail": str(e)}, status_code=409)


@app.delete("/api/integrations/{agent}")
async def uninstall_integration(request: Request, agent: str):
    if not _host(request):
        return err("host_only", 403)
    try:
        out = await asyncio.to_thread(integrations.uninstall, agent)
        _emit("integration.changed", {"agent": agent, "status": out["status"]})
        return out
    except KeyError:
        return err("unsupported_agent", 404)
    except (OSError, ValueError) as e:
        return JSONResponse({"error": "uninstall_failed", "detail": str(e)}, status_code=409)


def _tree_pids(shells: dict[str, int]) -> set[int]:
    """Every pid under a shelldeck shell (so `outside` skips them)."""
    pids = set(shells.values())
    for pid in shells.values():
        try:
            pids.update(p.pid for p in psutil.Process(pid).children(recursive=True))
        except psutil.Error:
            continue
    return pids


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


_git_counts: dict[str, tuple[float, int | None]] = {}  # path -> (checked at, changed files)
GIT_COUNT_S = 10  # the sidebar polls every 5s; git status at most every 10s per project


async def _git_count(path: str) -> int | None:
    """Changed files in a project's work tree for the sidebar, cached; None when git fails."""
    at, n = _git_counts.get(path, (0, None))
    if time.monotonic() - at < GIT_COUNT_S:
        return n
    _git_counts[path] = (time.monotonic(), n)  # one check at a time
    try:
        n = len((await asyncio.to_thread(gitgraph.changes, path))["files"])
    except gitgraph.GitError:
        n = None
    _git_counts[path] = (time.monotonic(), n)
    return n


@app.get("/api/projects")
async def list_projects():
    alive = set(manager.list_alive())
    projects = db.list_projects()
    for p in projects:
        if p.get("remote_id"):  # a folder on another machine: nothing to check locally
            p["exists"], p["has_git"], p["git_changes"] = True, False, None
            p["sessions"] = db.list_sessions(p["id"])
            for s in p["sessions"]:
                s["alive"] = s["id"] in alive
            continue
        p["exists"] = Path(p["path"]).is_dir()
        p["has_git"] = p["exists"] and gitgraph.has_git(p["path"])
        p["git_changes"] = await _git_count(p["path"]) if p["has_git"] else None
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


@app.get("/api/projects/{project_id}/git/changes")
async def project_git_changes(project_id: str):
    """Uncommitted changes: branch, ahead/behind and changed files with line counts."""
    project = db.get_project(project_id)
    if not project:
        return err("project_not_found", 404)
    try:
        return await asyncio.to_thread(gitgraph.changes, project["path"])
    except gitgraph.GitError as e:
        return err(f"git_status_failed:{e}")


@app.get("/api/projects/{project_id}/git/diff")
async def project_git_diff(project_id: str, file: str):
    """One changed file's diff (only files git status reports)."""
    project = db.get_project(project_id)
    if not project:
        return err("project_not_found", 404)
    try:
        return await asyncio.to_thread(gitgraph.diff, project["path"], file)
    except gitgraph.GitError as e:
        return err(f"git_diff_failed:{e}")


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


# ------------------------------------------------------------- remote systems


@app.get("/api/remotes")
async def list_remotes():
    return {"remotes": db.list_remotes(), "clients": await asyncio.to_thread(remotes.clients)}


@app.post("/api/remotes")
async def add_remote(payload: dict):
    """Save a machine; an SSH one gets a project for its root folder (/) right away, named after the machine."""
    try:
        r = db.save_remote(remotes.validate(payload, lambda pid: bool(db.get_project(pid))))
    except remotes.RemoteError as e:
        return err(str(e))
    if r["kind"] == "ssh":
        r["project_id_root"] = db.ensure_remote_project(r["id"], "/", r["name"])["id"]
    return r


@app.post("/api/remotes/test")
async def test_remote(payload: dict):
    """Try an SSH machine before saving it: network, then a login with the key/agent or the given password (used once, never kept)."""
    try:
        r = remotes.validate({**payload, "kind": "ssh"})
    except remotes.RemoteError as e:
        return err(str(e))
    password = str(payload.get("password") or "")[:256]  # this test only: never stored or logged
    return await asyncio.to_thread(remotes.test_ssh, r, 8, password)


@app.put("/api/remotes/{remote_id}")
async def update_remote(remote_id: str, payload: dict):
    if not db.get_remote(remote_id):
        return err("remote_not_found", 404)
    try:
        return db.save_remote(remotes.validate(payload, lambda pid: bool(db.get_project(pid))), remote_id)
    except remotes.RemoteError as e:
        return err(str(e))


@app.delete("/api/remotes/{remote_id}")
async def delete_remote(remote_id: str):
    """The machine and its projects; their terminals close."""
    if not db.get_remote(remote_id):
        return err("remote_not_found", 404)
    for p in db.remote_projects(remote_id):
        for sess in db.list_sessions(p["id"]):
            await _end_session(sess["id"])
            manager.forget(sess["id"])
        db.delete_project(p["id"])
    db.delete_remote(remote_id)
    return {"status": "ok"}


@app.post("/api/remotes/{remote_id}/connect")
async def connect_remote(request: Request, remote_id: str, payload: dict | None = None):
    """SSH: a new terminal (in the remote's project, else the given or first one) that runs ssh after its first prompt.
    RDP: the desktop client opens on the host machine, so only the host may ask for it."""
    r = db.get_remote(remote_id)
    if not r:
        return err("remote_not_found", 404)
    try:
        r = {**r, **remotes.validate(r)}  # re-check stored values before they reach a shell or a process
    except remotes.RemoteError as e:
        return err(str(e))
    if r["kind"] == "rdp":
        if not _host(request):
            return err("host_only", 403)
        try:
            argv = await asyncio.to_thread(remotes.open_rdp, r)
        except remotes.RemoteError as e:
            return err(str(e), 409)
        db.touch_remote(remote_id)
        return {"kind": "rdp", "command": " ".join(argv)}
    # opens in the machine's first project (its root, created with the machine), so it lists under that machine
    project = next(iter(db.remote_projects(remote_id)), None) or db.ensure_remote_project(remote_id, "/", r["name"])
    session = db.add_session(project["id"], cwd=str(Path.home()), shell="", name=r["name"])
    try:
        _attach(session, 30, 120)
    except Exception:  # noqa: BLE001 - spawn failures come from winpty/OS
        log.exception("remote terminal failed for %s", remote_id)
        return err("spawn_failed", 500)
    db.touch_remote(remote_id)
    return {"kind": "ssh", "session": session, "project_id": project["id"], "command": _remote_line(session)}


@app.post("/api/projects")
async def add_project(payload: dict):
    if rid := str(payload.get("remote_id") or ""):  # a folder on a saved SSH machine
        r = db.get_remote(rid)
        if not r or r["kind"] != "ssh":
            return err("remote_not_found", 404)
        try:
            path = remotes.check_path(str(payload.get("path") or ""))
        except remotes.RemoteError as e:
            return err(str(e))
        return db.ensure_remote_project(rid, path, None if path != "~" else r["name"])
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
    cwd = str(Path.home()) if project.get("remote_id") else str(payload.get("cwd") or project["path"])
    name = str(payload.get("name") or "").strip() or _default_name(project["id"], shell)
    session = db.add_session(project["id"], cwd=cwd, shell=shell, name=name)
    session["project_path"] = project["path"]
    return session


def _default_name(project_id: str, shell: str) -> str:
    kind = shell or get_settings()["default_shell"]
    same = [x for x in db.list_sessions(project_id) if (x["shell"] or get_settings()["default_shell"]) == kind]
    return f"{shells.label(kind)} {len(same) + 1}"


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
    if isinstance(exit_code, int) and session_id not in agent_kind:  # a person's command; agents report theirs via hooks
        _capture(session_id, "record_activity", {"command": command[:500], "ok": exit_code == 0})
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


CURSOR_FORWARD = re.compile(r"\x1b\[(\d*)C")


def _plain(output: str) -> str:
    """Terminal output as text. The rendered snapshot writes runs of blanks as cursor-forward, so those become spaces."""
    text = CURSOR_FORWARD.sub(lambda m: " " * min(int(m.group(1) or 1), 500), output.replace("\r\n", "\n"))
    return ANSI.sub("", text)


def _search(q: str, per_session: int = 20, total: int = 400) -> list[dict]:
    needle = q.casefold()
    results = []
    for s in db.list_sessions_with_project():
        text = _plain(manager.searchable(s["id"]))
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


def _abs(session_id: str, raw: str) -> Path | None:
    """A path as typed, relative to the session's current folder."""
    raw = raw.strip().strip("'\"")
    if not raw or len(raw) > 1000:
        return None
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path((db.get_session(session_id) or {}).get("cwd") or ".") / path
        return path.resolve()
    except (OSError, ValueError):
        return None


def _resolve(session_id: str, raw: str) -> Path | None:
    """A path from terminal output, relative to the session's current folder; None unless it is a file."""
    path = _abs(session_id, raw)
    return path if path and path.is_file() else None


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
    return _launch(target) or {"status": "ok", "path": str(path)}


def _launch(target: str) -> JSONResponse | None:
    """Hand a path or vscode:// URL to the OS, never a command line. An error response on failure."""
    try:
        if sys.platform == "win32":
            os.startfile(target)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target], start_new_session=True)
    except OSError as e:
        return err(f"open_failed:{e.strerror or e}")
    return None


@app.post("/api/projects/{project_id}/open")
async def open_project(project_id: str):
    """Open the project folder in VS Code, or the system's file manager."""
    project = db.get_project(project_id)
    if not project or not Path(project["path"]).is_dir():
        return err("project_not_found", 404)
    path = Path(project["path"])
    target = "vscode://file/" + quote(path.as_posix().lstrip("/"), safe="/:") if get_settings()["editor"] == "vscode" else str(path)
    return _launch(target) or {"status": "ok"}


# ------------------------------------------------------------ file editor

MAX_EDIT = 2 * 1024 * 1024
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
          ".bmp": "image/bmp", ".ico": "image/x-icon"}  # no SVG: opened directly it would run script on this origin
BOM = b"\xef\xbb\xbf"


def _read_file(path: Path) -> dict:
    if path.is_dir():
        return {"error": "is_a_directory"}
    if not path.exists():
        return {"path": str(path), "text": "", "mtime": None, "new": True} if path.parent.is_dir() else {"error": "file_not_found"}
    st = path.stat()
    if path.suffix.lower() in IMAGES:
        return {"path": str(path), "image": True, "size": st.st_size, "mtime": str(st.st_mtime_ns)}
    if st.st_size > MAX_EDIT:
        return {"error": "file_too_large"}
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return {"error": "binary_file"}
    bom = data.startswith(BOM)
    try:
        text, readonly = data[len(BOM) if bom else 0:].decode("utf-8"), False
    except UnicodeDecodeError:  # saving would re-encode it, so it stays read-only
        text, readonly = data.decode("utf-8", "replace"), True
    return {"path": str(path), "text": text.replace("\r\n", "\n"), "mtime": str(st.st_mtime_ns), "size": st.st_size,
            "crlf": b"\r\n" in data, "bom": bom, "readonly": readonly}


@app.get("/api/fs/file")
async def read_file(path: str = "", session_id: str = ""):
    """A text file for the built-in editor (relative paths start at the terminal's folder)."""
    target = _abs(session_id, path)
    if not target:
        return err("invalid_path")
    out = await asyncio.to_thread(_read_file, target)
    return err(out["error"], 404 if out["error"] == "file_not_found" else 400) if "error" in out else out


@app.put("/api/fs/file")
async def write_file(payload: dict):
    """Save from the built-in editor; refuses (409) when the file changed on disk since it was read, unless force."""
    target = _abs(str(payload.get("session_id") or ""), str(payload.get("path") or ""))
    text = payload.get("text")
    if not target or not isinstance(text, str) or len(text) > MAX_EDIT * 2:
        return err("invalid_file")
    if target.is_dir() or not target.parent.is_dir():
        return err("invalid_path")
    if target.exists() and not payload.get("force") and str(target.stat().st_mtime_ns) != payload.get("mtime"):
        return err("changed_on_disk", 409)
    text = text.replace("\r\n", "\n")
    data = (text.replace("\n", "\r\n") if payload.get("crlf") else text).encode("utf-8")
    try:
        target.write_bytes((BOM if payload.get("bom") else b"") + data)
    except OSError as e:
        return err(f"write_failed:{e.strerror or e}")
    return {"path": str(target), "mtime": str(target.stat().st_mtime_ns)}


@app.get("/api/fs/raw")
async def raw_image(path: str = "", session_id: str = ""):
    """Image bytes for the viewer. Images only: anything else served here could run as a page on this origin."""
    target = _resolve(session_id, path)
    if not target or target.suffix.lower() not in IMAGES:
        return err("not_an_image", 415)
    return FileResponse(target, media_type=IMAGES[target.suffix.lower()])


@app.post("/api/fs/show")
async def show_file(payload: dict):
    """`sd edit` / `sd view`: open a file in the browser that is using shelldeck."""
    path, mode = str(payload.get("path") or ""), payload.get("mode") if payload.get("mode") in ("edit", "view") else "edit"
    if not path:
        return err("path_required")
    return {"delivered": await _broadcast({"type": "open_file", "path": path, "mode": mode})}


# ------------------------------------------------------------- scratchpad


@app.get("/api/scratch")
async def list_scratch():
    return {"notes": db.list_scratch()}


async def _scratch_changed(request: Request, note_id: str) -> None:
    """A note written by the CLI (an agent or a script) shows up in open Scratchpad pages; the page's own autosave doesn't echo."""
    if auth.cli_ok(request.headers.get(auth.TOKEN_HEADER)):
        await _broadcast({"type": "scratch", "id": note_id})


@app.get("/api/scratch/{note_id}")
async def get_scratch(note_id: str):
    note = next((n for n in db.list_scratch() if n["id"] == note_id), None)
    return note or err("note_not_found", 404)


@app.post("/api/scratch")
async def add_scratch(request: Request, payload: dict):
    note = db.add_scratch(str(payload.get("body") or "")[:MAX_EDIT])
    await _scratch_changed(request, note["id"])
    return note


@app.put("/api/scratch/{note_id}")
async def save_scratch(request: Request, note_id: str, payload: dict):
    if not db.update_scratch(note_id, str(payload.get("body") or "")[:MAX_EDIT]):
        return err("note_not_found", 404)
    await _scratch_changed(request, note_id)
    return {"status": "ok"}


@app.delete("/api/scratch/{note_id}")
async def delete_scratch(note_id: str):
    db.delete_scratch(note_id)
    return {"status": "ok"}


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
    return {"text": _screen_rows(session_id, lines)}


def _screen_rows(session_id: str, lines: int) -> str:
    rows = [line.rstrip() for line in _plain(manager.searchable(session_id)).split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows[-max(1, min(lines, 2000)):])


async def _watch_questions() -> None:
    """Sub-agents never ask the user: a question on a sub-agent's screen goes to its parent agent."""
    while True:
        await asyncio.sleep(1)
        try:
            await _check_questions()
        except Exception:  # noqa: BLE001 - keep watching
            log.warning("question check failed", exc_info=True)


async def _check_questions(quiet: float | None = None) -> list[str]:
    """Each sub-agent whose output went quiet: forward a question it drew since the last check. Returns their ids."""
    asked, quiet = [], QUIET_S if quiet is None else quiet
    for sid, buf in list(ask_buf.items()):
        if not buf or time.monotonic() - last_out_at.get(sid, 0) < quiet:
            continue
        ask_buf[sid] = ""
        # ponytail: raw output is cursor-addressed, so this is a regex over what was drawn, not the screen
        text = _plain(buf)[-6000:]
        hits = list(agents.QUESTION.finditer(text))
        if not hits:
            continue
        m = hits[-1]
        key = team.one_line(text[m.start():m.end() + 80])
        if asked_before.get(sid, ("", 0))[0] == key and time.monotonic() - asked_before[sid][1] < REASK_S:
            continue  # the same prompt redrawn (resize, status line), not a new question
        asked_before[sid] = (key, time.monotonic())
        asked.append(sid)
        # from the question on: its options follow it; box-drawing rules are noise
        await _forward_question(sid, team.one_line(BOX.sub(" ", text[m.start():m.start() + 700]), 500))
    return asked


def _next_state(sid: str, was: str, now: float) -> str:
    """working | approval | idle for one agent terminal; see updateAgentStates in static/app.js."""
    quiet = now - out_at.get(sid, 0) > QUIET_S
    if quiet:
        # ponytail: a regex over what the last burst drew (raw output is cursor-addressed), not xterm's screen;
        # an answered prompt is gone once the agent prints again
        key = agent_kind.get(sid, ("",))[0]
        return "approval" if detection.match(key, RELAYED.sub("", _plain(burst.get(sid, "")))[-6000:]) else "idle"
    if now - busy_since.get(sid, now) > AGENT_BUSY_S:
        return "working"
    return "idle" if was == "approval" else was


async def _watch_agents() -> None:
    """Every 2s: each agent terminal's state; changes go to every browser, approval also to push."""
    while True:
        await asyncio.sleep(2)
        try:
            await _check_agents()
        except Exception:  # noqa: BLE001 - keep watching
            log.warning("agent state check failed", exc_info=True)


def _legacy(status: dict) -> str:
    """The pre-lifecycle working | approval | idle value, kept for older UI code and /api/agents."""
    return {"blocked": "approval", "working": "working"}.get(status["state"], "idle")


def _emit(type_: str, data: dict) -> None:
    """Record an event and wake subscribers. Compact snapshots only: never screen text or native session ids."""
    _event_seq[0] += 1
    events_log.append({"id": _event_seq[0], "type": type_, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "data": data})
    for ev in _event_waiters:
        ev.set()
    _event_waiters.clear()
    if any(p.handlers for p in plugins.loaded.values()):
        # ponytail: a thread per event keeps slow plugin handlers off the event loop; a queue if events get busy
        threading.Thread(target=plugins.emit, args=(type_, data), daemon=True).start()


def _changed() -> None:
    for ev in _state_changed:
        ev.set()
    _state_changed.clear()


async def _publish(sid: str, status: dict, key: str | None) -> None:
    """Broadcast a status change; a transition into blocked also notifies (push for top-level agents)."""
    was = agent_status.get(sid)
    if was and {k: v for k, v in was.items() if k != "since"} == status:
        return
    # when the state itself began (unchanged state, new detail: keep the start), for elapsed time in the UI
    status = {**status, "since": was["since"] if was and was.get("state") == status["state"] and was.get("since") else time.time()}
    agent_status[sid] = status
    _changed()
    if not was or was.get("state") != status["state"]:
        _emit("agent.exited" if status["state"] == "exited" else "agent.state",
              {"session_id": sid, "agent": key or agent_kind.get(sid, ("",))[0], "state": status["state"], "reason": status.get("reason"),
               "source": status.get("source")})
    await _broadcast({"type": "agent_state", "session_id": sid, "state": _legacy(status), "status": status})
    if status["state"] != "blocked" or (was and was["state"] == "blocked") or not key:
        return
    s = db.get_session(sid)
    if s and not s.get("parent"):  # sub-agents ask their parent, not you
        project = db.get_project(s["project_id"]) if s.get("project_id") else None
        msg = {"title": f"{agents.AGENTS[key][0]} needs you", "body": f"{s.get('nick') or sid} · {project['name'] if project else ''}",
               "data": {"session_id": sid}}
        if tokens := [t["token"] for t in db.list_push_tokens() if auth.alive(t["session_hash"])]:
            threading.Thread(target=_push, args=(tokens, msg), daemon=True).start()


def _status_of(sid: str, now: float | None = None) -> dict:
    return lifecycle.to_dict(reports.status(sid, now), reports.metadata(sid, now))


native_seen: dict[str, str] = {}  # terminal -> native session id last stored
resumed: set[str] = set()  # terminals already auto-resumed in this server run
SAFE_ARG = re.compile(r"[A-Za-z0-9._:/=@+-]{1,200}")  # needs no quoting in pwsh, cmd, bash, zsh or fish


def _resume_plan(sid: str, auto: bool = False) -> tuple[str | None, str | None]:
    """(command line, None) when a stored agent session can be resumed in this terminal, else (None, error code).
    The line comes only from validated argv; nothing is rebuilt from terminal output."""
    rec = db.get_agent_session(sid)
    s = db.get_session(sid)
    if not rec or not s or not rec.get("resume_argv_json"):
        return None, "no_resume"
    try:
        argv = lifecycle.parse_resume_argv(json.loads(rec["resume_argv_json"]), rec["agent"])
    except (ValueError, lifecycle.ReportError):
        return None, "invalid_resume_argv"
    if not all(SAFE_ARG.fullmatch(a) for a in argv):
        return None, "invalid_resume_argv"
    if not shutil.which(argv[0]):
        return None, "executable_not_found"
    cwd = s.get("cwd") or ((db.get_project(s["project_id"]) or {}).get("path") if s.get("project_id") else None)
    if cwd and not Path(cwd).is_dir():
        return None, "cwd_missing"
    if auto and db.list_handoffs(to_sid=sid, status="open"):
        return None, "open_handoff"  # a hand-off is reviewed by a person, never resumed silently
    try:  # per-run hooks as the agent's own flag (Claude's --settings), placed before the resume args
        extra = [team._arg(a) for a in integrations.run_flags(rec["agent"])]
    except (ValueError, OSError):
        extra = []
    return " ".join([argv[0], *extra, *argv[1:]]), None  # argv[0] is the checked executable (cursor-agent, agy)


def _auto_resume(sid: str) -> None:
    """On a terminal's first spawn after a restart: resume its agent when the setting is auto and it's safe."""
    if sid in resumed or get_settings().get("agent_resume") != "auto":
        return
    resumed.add(sid)
    rec = db.get_agent_session(sid)
    if not rec or rec.get("last_state") == "exited":
        return
    line, why = _resume_plan(sid, auto=True)
    if not line:
        log.info("not resuming the agent in %s: %s", sid, why)
        return
    t = asyncio.get_running_loop().create_task(_start_agent(sid, line))
    background.add(t)
    t.add_done_callback(background.discard)


@app.get("/api/agent-sessions")
async def agent_sessions():
    """Stored agent sessions and whether each can be resumed now. Native ids are never sent."""
    running = set(agent_kind)
    rows = []
    for r in await asyncio.to_thread(db.list_agent_sessions):
        line, why = await asyncio.to_thread(_resume_plan, r["session_id"])
        rows.append({"session_id": r["session_id"], "agent": r["agent"], "source": r["source"].split(":", 1)[0], "last_state": r["last_state"],
                     "last_seen_at": r["last_seen_at"], "running": r["session_id"] in running, "can_resume": bool(line) and r["session_id"] not in running,
                     "error": "agent_running" if r["session_id"] in running else why})
    return {"sessions": rows, "mode": get_settings().get("agent_resume")}


@app.post("/api/sessions/{session_id}/resume")
async def resume_agent(session_id: str):
    """Start the terminal's stored agent session again (the user asked; works whatever the setting)."""
    if session_id in agent_kind:
        return err("agent_running", 409)
    line, why = await asyncio.to_thread(_resume_plan, session_id)
    if not line:
        return err(why or "no_resume", 409)
    if not manager.get(session_id):
        resumed.add(session_id)  # this resume replaces the auto one
        _attach(db.get_session(session_id), 30, 120)  # its pane resizes it when it opens
    await _start_agent(session_id, line)
    return {"command": line}


def _save_native(sid: str, key: str, nat: dict) -> None:
    """Store an agent's own session id and resume argv (validated like an integration's)."""
    try:
        argv = lifecycle.parse_resume_argv(nat.get("resume_argv"), key)
        db.save_agent_session(sid, key, f"native:{key}", str(nat["session_id"])[:200], list(argv), nat.get("state") or "unknown")
    except (lifecycle.ReportError, sqlite3.Error):
        log.debug("native session not saved", exc_info=True)


def _capture(sid: str, fn: str, *args, **kwargs) -> None:
    """Deterministic context capture (context.record_activity / start_session / end_session) for the terminal's
    project, on a thread: it never slows the agent or the event loop, and a failure is only logged."""
    def run():
        try:
            project = _ctx_project({"session_id": sid})
            if project:
                getattr(context, fn)(project, *args, **kwargs)
                if fn == "end_session":
                    context.project_files(project)
        except Exception:  # noqa: BLE001
            log.warning("context capture failed", exc_info=True)
    threading.Thread(target=run, daemon=True).start()


def _ctx_session(sid: str) -> str | None:
    """The context session id of the agent run in this terminal: terminal id + process generation."""
    kind = agent_kind.get(sid)
    return f"{sid}.{kind[1]}" if kind else None


def _remember_state(sid: str, state: str) -> None:
    """last_state of a stored native session; best effort."""
    try:
        db.touch_agent_session(sid, state)
    except sqlite3.Error:
        log.debug("agent session state not saved", exc_info=True)


def _detected(sid: str, key: str) -> None:
    """A new agent process in a terminal: new generation, context session, and none of the old one's reports."""
    _generation[0] += 1
    if sid in agent_kind:  # another agent took over the terminal: close the previous run first
        _capture(sid, "end_session", _ctx_session(sid))
    agent_kind[sid] = (key, _generation[0])
    _capture(sid, "start_session", _ctx_session(sid), key)
    _emit("agent.detected", {"session_id": sid, "agent": key, "generation": _generation[0]})
    reports.forget(sid)


async def _agent_key(sid: str) -> str | None:
    """The terminal's agent, detecting it now when the 2s watcher hasn't yet (a hook's first report, a fresh launch),
    so early reports aren't dropped when the watcher catches up."""
    if sid in agent_kind:
        return agent_kind[sid][0]
    key = await _agent_in(sid)
    if key and sid not in agent_kind:
        _detected(sid, key)
    return key


async def _check_agents() -> None:
    found = await asyncio.to_thread(agents.running, _shell_pids())
    now = time.monotonic()
    for sid in [s for s in agent_kind if s not in found]:
        _capture(sid, "end_session", _ctx_session(sid))
        agent_state.pop(sid, None)
        agent_kind.pop(sid)
        reports.forget(sid)
        native_seen.pop(sid, None)
        _remember_state(sid, "exited")
        await _publish(sid, {"state": "exited", "source": "process", "reason": None, "detail": None}, None)
        agent_status.pop(sid, None)
    natives = await asyncio.to_thread(agents.native, {sid: pid for sid, pid in _shell_pids().items() if sid in found})
    for sid, (key, _) in found.items():
        if agent_kind.get(sid, ("",))[0] != key:
            _detected(sid, key)
        was = agent_state.get(sid, "idle")
        agent_state[sid] = await asyncio.to_thread(_next_state, sid, was, now)
        reports.put_heuristic(sid, lifecycle.heuristic(agent_state[sid], now))
        if (nat := natives.get(sid)) and (rep := lifecycle.native_report(key, nat, now)):
            reports.put(sid, rep)
        if nat and nat.get("session_id") and native_seen.get(sid) != nat["session_id"]:
            native_seen[sid] = nat["session_id"]
            _save_native(sid, key, nat)
        status = _status_of(sid, now)
        if status["state"] != agent_status.get(sid, {}).get("state"):
            _remember_state(sid, status["state"])
        await _publish(sid, status, key)


@app.post("/api/agent-reports")
async def agent_report(request: Request, payload: dict):
    """Lifecycle/metadata/native-session report from an integration running inside a terminal. The terminal
    comes from its per-process report token, never from a session id in the body."""
    token = request.headers.get(REPORT_HEADER, "")
    sid = next((k for k, v in report_tokens.items() if token and hmac.compare_digest(v, token)), None)
    if not sid or not manager.get(sid):
        return err("invalid_report_token", 403)
    if payload.get("session_id") not in (None, sid):
        return err("session_mismatch", 403)
    key = await _agent_key(sid)
    if not key:
        return err("no_agent_running", 409)
    try:
        report, meta, ref = lifecycle.parse_report(payload, key)
        act = lifecycle.parse_activity(payload.get("activity"))
    except lifecycle.ReportError as e:
        log.info("agent report rejected: session=%s agent=%s error=%s", sid, key, e)
        return err(str(e), 422)
    source = str(payload["source"])
    if act:
        _capture(sid, "record_activity", act, agent=key, session_id=_ctx_session(sid))
    if report:
        reports.put(sid, report, meta)
    status = _status_of(sid)
    if ref:
        db.save_agent_session(sid, key, source, ref.session_id, list(ref.resume_argv), status["state"])
        _emit("agent.session_updated", {"session_id": sid, "agent": key, "source": source})
    elif status["state"] != agent_status.get(sid, {}).get("state"):
        _remember_state(sid, status["state"])  # the watcher only sees changes it made itself
    log.info("agent report: session=%s source=%s agent=%s state=%s", sid, source, key, status["state"] if report else "(session only)")
    await _publish(sid, status, key)
    return {"status": status}


@app.get("/api/agent-status")
async def agent_status_all(target: str = ""):
    """Server-owned lifecycle status of every agent terminal (or one)."""
    rows = [{"session_id": sid, "agent": agent_kind.get(sid, ("",))[0], "generation": agent_kind.get(sid, ("", 0))[1], **st}
            for sid, st in agent_status.items() if not target or sid == target]
    return {"agents": rows}


@app.get("/api/agent-commands/{agent}")
async def agent_command_list(agent: str):
    """The agent's own slash commands behind shelldeck's actions (compact, model, resume, ...)."""
    return {"agent": agent, "commands": agent_commands.catalog(agent)}


@app.post("/api/sessions/{session_id}/agent-command")
async def agent_command(session_id: str, payload: dict):
    """Type an agent's native command (e.g. Gemini's /compress for `compact`) into its terminal. Refused while the agent
    is blocked (it would answer the question) or working (most agents ignore or queue commands mid-turn) unless `force`."""
    key = await _agent_key(session_id)
    if not key:
        return err("no_agent_running", 409)
    try:
        text = agent_commands.line(key, str(payload.get("command") or ""), str(payload.get("arg") or ""))
    except ValueError as e:
        return err(str(e))
    state = (agent_status.get(session_id) or {}).get("state")
    if state in ("blocked", "working") and not payload.get("force"):
        return err(f"agent_{state}", 409)
    if not await _type(session_id, text):
        return err("not_running", 409)
    _emit("agent.command", {"session_id": session_id, "agent": key, "command": payload.get("command")})
    return {"agent": key, "typed": text}


# One turn, few tool calls: duplicates and secrets are handled server-side, so the agent needn't read memory first,
# and sd remember takes several facts at once. ponytail: wording tuned for brevity, not per agent.
EXTRACT_PROMPT = ("[shelldeck] Save what you learned this session to project memory, briefly: "
                  'sd remember "fact" "fact" ... (durable facts only, add --file for sources), '
                  'sd decide "decision" -r "reason" for settled choices, and '
                  'sd task update --step "..." --next "..." for where things stand. '
                  "Duplicates are merged and secrets removed automatically, so don't read memory first. Then stop.")


@app.post("/api/sessions/{session_id}/extract")
async def extract_facts(session_id: str):
    """On demand only: one prompt asking the agent to record its own session knowledge (no model runs in shelldeck).
    Refused while the agent is blocked or working, like any prompt."""
    key = await _agent_key(session_id)
    if not key:
        return err("no_agent_running", 409)
    state = (agent_status.get(session_id) or {}).get("state")
    if state in ("blocked", "working"):
        return err(f"agent_{state}", 409)
    if not await _type(session_id, EXTRACT_PROMPT):
        return err("not_running", 409)
    _emit("agent.command", {"session_id": session_id, "agent": key, "command": "extract"})
    return {"agent": key}


ASK_SHELL = {"pwsh": "PowerShell 7", "powershell": "Windows PowerShell 5.1", "cmd": "cmd.exe", "gitbash": "Git Bash",
             "wsl": "bash"}


@app.post("/api/ask")
async def ask(payload: dict):
    """`?` in a terminal / `sd ask`: one command for the terminal's shell and OS from an installed agent's small
    model. Only returned, never run: the UI types it without Enter."""
    q = str(payload.get("q") or "").strip()
    if not q:
        return err("empty_question")
    settings = get_settings()
    agent = smart_recall.pick(str(payload.get("agent") or settings["ask_agent"]))
    if not agent:
        return err("no_agent_installed", 409)
    s = db.get_session(str(payload.get("session_id") or "")) or {}
    kind = s.get("shell") or settings["default_shell"]
    where = "Linux (WSL)" if kind == "wsl" else {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
    model = str(payload.get("model") or "") or (None if payload.get("agent") else settings["ask_model"] or None)
    cmd = await asyncio.to_thread(smart_recall.ask, q, ASK_SHELL.get(kind, shells.label(kind)), where, s.get("cwd") or "", agent, model,
                                  plugins.commands())
    if cmd.startswith("?"):  # the model picked a plugin command: only one that exists counts
        words = cmd[1:].split()
        if any(c["name"] == " ".join(words[:2]) for c in plugins.commands()):
            return {"command": "", "plugin": words, "agent": agent}
        cmd = ""
    return {"command": cmd, "plugin": None, "agent": agent} if cmd else err("no_answer", 502)


def _plugins_off() -> set[str]:
    return {n for n in db.get_setting("plugins_disabled", "").split(",") if n}


@app.get("/api/plugins")
async def plugin_list():
    """Installed plugins (status, package, commands, events) and the commands of the loaded ones."""
    return {"plugins": plugins.listing(), "commands": plugins.commands()}


@app.post("/api/plugins/run")
async def plugin_run(payload: dict):
    """`?docker ps -a` / `sd docker ps -a`: a plugin command, run in the server with the terminal's context."""
    words = payload.get("words")
    if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
        return err("invalid_words")
    s = db.get_session(str(payload.get("session_id") or "")) or {}
    p = db.get_project(s["project_id"]) if s.get("project_id") else None
    ctx = {"session_id": s.get("id", ""), "cwd": s.get("cwd") or (p or {}).get("path", ""), "shell": s.get("shell") or "",
           "project": (p or {}).get("path", "")}
    try:
        return await asyncio.to_thread(plugins.run, words, ctx)
    except KeyError:
        return err("unknown_command", 404)
    except plugins.PluginError as e:  # the plugin's own error, shown to whoever ran it
        log.warning("plugin command %s failed: %s", " ".join(words[:2]), e)
        return JSONResponse({"error": "plugin_failed", "detail": str(e)}, status_code=500)


@app.post("/api/plugins/{name}")
async def plugin_toggle(name: str, payload: dict, request: Request):
    """Host only: enable or disable an installed plugin, then reload them all."""
    if not _host(request):
        return err("host_only", 403)
    if name not in plugins.found:
        return err("unknown_plugin", 404)
    off = _plugins_off() - {name} if payload.get("enabled") else _plugins_off() | {name}
    db.set_setting("plugins_disabled", ",".join(sorted(off)))
    await asyncio.to_thread(plugins.load, off)
    return await plugin_list()


@app.post("/api/agent-prompt")
async def agent_prompt(payload: dict):
    """Type a prompt into an agent and, with `wait`, block until it reaches `until` in a state that began after the
    prompt was sent. Refused while the agent is blocked (the text would answer its question)."""
    sid = str(payload.get("session_id") or "")
    text = str(payload.get("text") or "")[:20000]
    if not text.strip():
        return err("text_required")
    if not await _agent_key(sid):
        return err("no_agent_running", 409)
    if (agent_status.get(sid) or {}).get("state") == "blocked":
        return err("agent_blocked", 409)
    gen, sent = agent_kind[sid][1], time.time()
    if not await _type(sid, team.one_line(text)):
        return err("not_running", 409)
    if not payload.get("wait"):
        return {"result": "sent"}
    until = {u for u in (payload.get("until") or ["done", "idle"]) if u in WAIT_STATES}
    timeout = min(max(float(payload.get("timeout") or 600), 0.1), 3600)
    return await _wait_for(sid, gen, lambda st: st["state"] in until and st.get("since", 0) >= sent, timeout)


@app.get("/api/events")
async def events_stream(request: Request, types: str = "", since: int = 0):
    """Server-Sent Events: agent.* / handoff.* / integration.changed. Resume with Last-Event-ID or `since`."""
    want = tuple(t for t in types.split(",") if t) or EVENT_TYPES
    try:
        last = int(request.headers.get("last-event-id") or since or 0)
    except ValueError:
        last = 0
    if not since and "last-event-id" not in request.headers:
        last = _event_seq[0]  # a new subscriber gets events from now on

    async def stream():
        nonlocal last
        yield ": shelldeck events\n\n"
        while not await request.is_disconnected():
            for e in [e for e in list(events_log) if e["id"] > last]:
                last = e["id"]
                if e["type"].startswith(want):
                    yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"
            ev = asyncio.Event()
            _event_waiters.append(ev)
            try:
                await asyncio.wait_for(ev.wait(), 15)
            except TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/agent-explain/{session_id}")
async def agent_explain(session_id: str):
    """Why a terminal's agent has its state: process, every report and which one decides, the screen heuristic's
    inputs, integration and stored session. Never raw screen text or native session ids."""
    s = db.get_session(session_id)
    if not s:
        return err("session_not_found", 404)
    key, gen = agent_kind.get(session_id, (None, None))
    now = time.monotonic()
    text = _plain(burst.get(session_id, ""))
    hit = detection.match(key or "", RELAYED.sub("", text)[-6000:])
    manifest = detection.load(key or "")
    stored = await asyncio.to_thread(db.get_agent_session, session_id)
    integ = integrations.status(key) if key in integrations.INSTALLERS else {"status": "unsupported"}
    rows = lifecycle.explain(reports.reports.get(session_id, {}), now)
    status = agent_status.get(session_id)
    if not key:
        why = "no agent process detected in this terminal"
    elif not rows or not any(r["decides"] for r in rows):
        why = "no live report: state unknown"
    else:
        win = next(r for r in rows if r["decides"])
        why = f"{win['source']} decides (highest live priority {win['priority']})"
        if win["source"].startswith("heuristic") and integ["status"] != "installed" and key in integrations.INSTALLERS:
            why += f"; install the {key} integration for exact state (sd integration install {key})"
    return {
        "session_id": session_id, "nick": s.get("nick"), "agent": key, "generation": gen, "status": status, "why": why,
        "reports": rows,
        "heuristic": {"quiet_s": round(now - out_at[session_id], 1) if session_id in out_at else None,
                      "burst_s": round(now - busy_since[session_id], 1) if session_id in busy_since else None,
                      "question_rule": hit.id if hit else None, "rule_reason": hit.reason if hit else None,
                      "legacy_state": agent_state.get(session_id)},
        "manifest": {"source": manifest.source, "version": manifest.version, "rules": len(manifest.rules), "error": manifest.error},
        "integration": {"status": integ["status"], "tier": "priority" if key in integrations.PRIORITY else "later" if key else None},
        "stored_session": {"source": stored["source"], "last_state": stored["last_state"]} if stored else None,
    }


WAIT_STATES = {"idle", "working", "blocked", "done", "exited"}


@app.post("/api/agent-wait")
async def agent_wait(payload: dict):
    """Long-poll until a terminal's agent reaches one of `until` (or exits, or is replaced). Event-driven:
    it wakes on status changes, it doesn't read screens."""
    sid = str(payload.get("session_id") or "")
    until = {u for u in (payload.get("until") or []) if u in WAIT_STATES}
    timeout = min(max(float(payload.get("timeout") or 600), 0.1), 3600)
    if not until:
        return err("invalid_until")
    if not await _agent_key(sid):
        return err("no_agent_running", 409)
    return await _wait_for(sid, agent_kind[sid][1], lambda st: st["state"] in until, timeout)


async def _wait_for(sid: str, gen: int, reached, timeout: float) -> dict:
    """Wait (no polling) until reached(status) for the agent process `gen`; a replaced or exited agent ends it."""
    deadline = time.monotonic() + timeout
    while True:
        cur = agent_kind.get(sid)
        if not cur or cur[1] != gen:
            return {"result": "exited" if not cur else "replaced", "status": agent_status.get(sid)}
        st = agent_status.get(sid)
        if st and reached(st):
            return {"result": "reached", "status": st}
        left = deadline - time.monotonic()
        if left <= 0:
            return {"result": "timeout", "status": st}
        ev = asyncio.Event()
        _state_changed.append(ev)
        try:
            await asyncio.wait_for(ev.wait(), left)
        except TimeoutError:
            pass


def _push(tokens: list[str], msg: dict) -> None:
    """Expo push service; a thread. Only the nick, agent and project leave this machine, never screen text.
    ponytail: no retry queue; a failed push is logged and dropped."""
    body = json.dumps([{"to": t, "sound": "default", **msg} for t in tokens]).encode()
    req = urllib.request.Request(EXPO_PUSH, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except OSError:
        log.warning("push failed", exc_info=True)


@app.post("/api/push")
async def push_register(request: Request, payload: dict):
    """Register an Expo push token for this login (a logged-in device, not the CLI)."""
    token = str(payload.get("token", ""))
    if not request.state.session:
        return err("login_required", 403)
    if not re.fullmatch(r"Expo(nent)?PushToken\[[\w-]{1,200}\]", token):
        return err("invalid_token")
    db.add_push_token(token, request.state.session)
    return {"status": "ok"}


@app.delete("/api/push")
async def push_unregister(request: Request, payload: dict):
    if not request.state.session:
        return err("login_required", 403)
    if not db.delete_push_token(str(payload.get("token", "")), request.state.session):
        return err("not_found", 404)
    return {"status": "ok"}


async def _forward_question(sid: str, excerpt: str) -> None:
    """excerpt: the question and its options, as drawn."""
    kid = db.get_session(sid)
    if not kid or STOPPING.is_set():
        return
    h = next(iter(db.list_handoffs(to_sid=sid, status="open")), None)
    nick = kid.get("nick") or sid
    ho = f" (hand-off {h['id']})" if h else ""
    log.info("sub-agent %s is asking: %s", nick, excerpt[:200])
    if kid.get("parent") and await _agent_in(kid["parent"]):
        await _type(kid["parent"], team.one_line(
            f"[shelldeck] Your sub-agent {nick}{ho} is waiting on a question: {excerpt} "
            f"-- Decide and answer it with: sd answer {nick} <keys> (e.g. sd answer {nick} 1, or sd answer {nick} y enter); "
            # keep the ending: the UI's RELAYED_RE uses it to tell this apart from a question to the user
            f"sd peek {nick} shows its screen; ask the user only if it is really their call."))
    else:  # its parent is a plain shell: then the question is the user's
        await _broadcast({"type": "question", "session_id": sid, "nick": nick, "text": excerpt})


@app.post("/api/sessions/{session_id}/input")
async def session_input(session_id: str, payload: dict):
    """Type text into a running terminal, then Enter unless enter is false."""
    text = str(payload.get("text") or "")[:20000]
    if not text:
        return err("text_required")
    if not await _type(session_id, text, payload.get("enter", True)):
        return err("not_running", 409)
    return {"status": "ok"}


async def _type(session_id: str, text: str, enter: bool = True) -> bool:
    if not manager.write(session_id, text):
        return False
    if enter:
        await asyncio.sleep(0.3)  # TUIs (Claude Code, Codex) treat text+CR in one burst as a paste, not a submit
        manager.write(session_id, "\r")
    return True


# ------------------------------------------------------------- agent team


async def _agent_in(session_id: str) -> str | None:
    """The agent running in a terminal, if any."""
    proc = manager.get(session_id)
    found = await asyncio.to_thread(agents.running, {session_id: proc.pid}) if proc else {}
    return found[session_id][0] if session_id in found else None


def _handoff_files(handoff: dict) -> None:
    project = db.get_project(handoff["project_id"])
    try:
        if project:
            team.write_files(project["path"], handoff, db.list_handoffs(project["id"]), _snapshot(project["path"]))
    except OSError:
        log.warning("writing hand-off files failed", exc_info=True)


def _snapshot(path: str) -> str:
    """The project's context snapshot for a hand-off file; empty if the context store fails (never blocks a hand-off)."""
    try:
        p = context.project_for(path)
        return context.snapshot(p) if p else ""
    except Exception:  # noqa: BLE001 - context is an enhancement, not a dependency
        log.warning("context snapshot failed", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Agent context (context.py): tasks, state, memory, decisions, recall
# ---------------------------------------------------------------------------


def _ctx_project(payload: dict) -> dict | None:
    """The context project named by `project` (shelldeck or context project name), or containing `cwd`."""
    name = str(payload.get("project") or "").strip()
    if name:
        sd = next((p for p in db.list_projects() if p["name"].casefold() == name.casefold() or p["id"] == name), None)
        return context.project_for(sd["path"]) if sd else context.project_for(name=name)
    sid = str(payload.get("session_id") or "")
    s = db.get_session(sid) if sid else None
    cwd = str(payload.get("cwd") or "") or (s or {}).get("cwd")
    if not cwd and s and s.get("project_id"):
        cwd = (db.get_project(s["project_id"]) or {}).get("path")
    return context.project_for(cwd) if cwd else None


async def _ctx(fn, *args, **kwargs):
    """Run a context call off the event loop; failures become a 4xx/503, never a crash."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except LookupError as e:
        return err(str(e), 404)
    except ValueError as e:
        return err(str(e))
    except sqlite3.Error:
        log.warning("context store failed", exc_info=True)
        return err("context_unavailable", 503)


def _project_later(project: dict) -> None:
    """Markdown projection in the background; agent interaction never waits on it."""
    def run():
        try:
            context.project_files(project)
        except Exception:  # noqa: BLE001
            log.warning("context projection failed", exc_info=True)
    threading.Thread(target=run, daemon=True).start()


def _agent_for(payload: dict) -> str | None:
    sid = str(payload.get("session_id") or "")
    return agent_kind.get(sid, (None,))[0] or (str(payload.get("agent"))[:40] if payload.get("agent") else None)


@app.get("/api/context")
async def get_context(cwd: str = "", project: str = "", session_id: str = "", q: str = ""):
    p = await _ctx(_ctx_project, {"cwd": cwd, "project": project, "session_id": session_id})
    if not isinstance(p, dict):
        return p or err("project_not_found", 404)
    open_h = next(iter(db.list_handoffs(to_sid=session_id, status="open")), None) if session_id else None
    return await _ctx(context.project_context, p, q, open_h)


@app.post("/api/context/init")
async def init_context(payload: dict):
    """`sd init`: add the folder as a shelldeck project (if new) and write its `.shelldeck/` files now."""
    path = Path(str(payload.get("path") or "")).expanduser()
    if not path.is_dir():
        return err("not_a_directory")
    added = not any(Path(p["path"]).resolve() == path.resolve() for p in db.list_projects())
    if added and payload.get("add_project", True):
        db.ensure_project(str(path))
    out = await _ctx(context.init_project, str(path), str(payload.get("task") or "").strip() or None)
    if isinstance(out, dict):
        out["added"] = added and payload.get("add_project", True)
    return out


@app.get("/api/context/projects")
async def context_projects():
    return {"projects": await _ctx(context.list_projects)}


@app.get("/api/context/memory")
async def get_memory(cwd: str = "", project: str = "", session_id: str = ""):
    p = await _ctx(_ctx_project, {"cwd": cwd, "project": project, "session_id": session_id})
    if not isinstance(p, dict):
        return p or err("project_not_found", 404)
    return {"project": p["name"], "memory": await _ctx(context.memory, p), "decisions": await _ctx(context.decisions, p)}


@app.post("/api/context/memory")
async def add_memory(payload: dict):
    """`sd remember` / `sd discover`: a fact for this project's memory (secrets are redacted, duplicates merged)."""
    p = await _ctx(_ctx_project, payload)
    if not isinstance(p, dict):
        return p or err("project_not_found", 404)
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    k = await _ctx(context.record_memory, p, str(payload.get("text") or ""), kind="discover" if payload.get("kind") == "discover" else "remember",
                   type_=str(payload.get("type") or ("discovery" if payload.get("kind") == "discover" else "note")),
                   topic=str(payload.get("topic") or ""), title=str(payload.get("title") or ""), files=[str(f) for f in files[:20]],
                   agent=_agent_for(payload), session_id=str(payload.get("session_id") or "") or None, scope=str(payload.get("scope") or "PROJECT"))
    if isinstance(k, dict):
        _project_later(p)
    return k


@app.post("/api/context/decisions")
async def add_decision(payload: dict):
    p = await _ctx(_ctx_project, payload)
    if not isinstance(p, dict):
        return p or err("project_not_found", 404)
    d = await _ctx(context.record_decision, p, str(payload.get("title") or ""), str(payload.get("decision") or ""),
                   reason=str(payload.get("reason") or ""), alternatives=str(payload.get("alternatives") or ""),
                   consequence=str(payload.get("consequence") or ""), agent=_agent_for(payload))
    if isinstance(d, dict):
        _project_later(p)
    return d


@app.post("/api/context/state")
async def update_state(payload: dict):
    """`sd task update`: the active task and current state (a checkpoint; git state is captured too)."""
    p = await _ctx(_ctx_project, payload)
    if not isinstance(p, dict):
        return p or err("project_not_found", 404)
    fields = {k: (str(payload[k]) if payload.get(k) is not None else None) for k in
              ("task", "objective", "plan", "status", "step", "next_action", "last_error", "tests")}
    out = await _ctx(context.save_state, p, **fields, agent=_agent_for(payload), session_id=str(payload.get("session_id") or "") or None)
    if isinstance(out, dict):
        _project_later(p)
    return out


@app.get("/api/context/recall")
async def recall(q: str, cwd: str = "", project: str = "", session_id: str = "", limit: int = 10, smart: bool = False,
                 agent: str = "", model: str = ""):
    """Keyword search. `agent` (+ optional `model`) picks who adds related keywords first; `smart` or the
    recall_agent setting picks one automatically; agent=off forces plain keywords."""
    p = await _ctx(_ctx_project, {"cwd": cwd, "project": project, "session_id": session_id})
    p = p if isinstance(p, dict) else None
    setting = get_settings().get("recall_agent", "off")
    if agent:
        who = None if agent == "off" else smart_recall.pick(agent)
    else:
        who = smart_recall.pick(setting if setting != "off" else ("auto" if smart else "off"))
    extra = await asyncio.to_thread(smart_recall.expand, q, who, model or None) if who else []
    return {"results": await _ctx(context.recall, q, p, max(1, min(limit, 50)), True, extra), "expanded": extra, "agent": who,
            "model": (model or smart_recall.small_model(who)) if who else None}


@app.get("/api/context/recall-agents")
async def recall_agents():
    """Installed agents and their models, for choosing who widens a search."""
    return {"agents": await asyncio.to_thread(smart_recall.choices), "default": get_settings().get("recall_agent", "off")}


@app.post("/api/context/knowledge/{knowledge_id}/status")
async def knowledge_status(knowledge_id: str, payload: dict):
    return await _ctx(context.set_status, knowledge_id, str(payload.get("status") or "VERIFIED"))


@app.post("/api/context/relationships")
async def add_relationship(payload: dict):
    p = await _ctx(_ctx_project, payload)
    target = await _ctx(_ctx_project, {"project": payload.get("target")})
    if not isinstance(p, dict) or not isinstance(target, dict):
        return err("project_not_found", 404)
    return await _ctx(context.relate, p, str(payload.get("relation") or "related-to"), target)


async def _tell_sender(handoff: dict, text: str) -> None:
    """Toast in the browser, and a message typed into the sender's terminal when an agent runs there
    (in a bare shell the text would run as a command)."""
    await _broadcast({"type": "handoff", "handoff": handoff, "text": text})
    if handoff.get("from_sid") and await _agent_in(handoff["from_sid"]):
        await _type(handoff["from_sid"], team.one_line(text))


@app.get("/api/handoffs")
async def list_handoffs(project_id: str = "", to: str = "", status: str = ""):
    return {"handoffs": db.list_handoffs(project_id or None, to or None, status or None)}


@app.post("/api/handoffs")
async def create_handoff(payload: dict):
    """Hand a task to an agent already running in another terminal."""
    to = db.get_session(str(payload.get("to") or ""))
    sender = db.get_session(str(payload.get("from") or "")) or {}
    task = str(payload.get("task") or "").strip()[:20000]
    if not to:
        return err("session_not_found", 404)
    if not task:
        return err("task_required")
    key = await _agent_in(to["id"])
    if not key:
        return err("no_agent_running", 409)
    h = db.add_handoff(project_id=to["project_id"], from_sid=sender.get("id"), from_nick=sender.get("nick"),
                       to_sid=to["id"], to_nick=to["nick"], agent=key, model=None, task=task)
    await asyncio.to_thread(_handoff_files, h)
    _emit("handoff.created", {"id": h["id"], "from": h["from_nick"], "to": h["to_nick"], "to_sid": h["to_sid"], "agent": key})
    project = db.get_project(to["project_id"]) or {"path": "."}
    reply = f'when finished run: sd done {h["id"]} "<summary>"'
    await _type(to["id"], team.one_line(
        f"[hand-off {h['id']} from {sender.get('nick') or 'the user'}] {task} "
        f"(task file: {Path(project['path']) / '.shelldeck' / 'handoffs' / (h['id'] + '.md')}; {reply})"))
    return h


@app.post("/api/handoffs/{handoff_id}/done")
async def finish_handoff(handoff_id: str, payload: dict):
    """The receiver closes a hand-off; the sender hears about it."""
    h = db.get_handoff(handoff_id)
    if not h:
        return err("handoff_not_found", 404)
    if h["status"] != "open":
        return err("already_closed", 409)
    status = "failed" if payload.get("failed") else "done"
    result = str(payload.get("result") or "").strip()[:20000]
    db.close_handoff(handoff_id, status, result)
    h = db.get_handoff(handoff_id)
    await asyncio.to_thread(_handoff_files, h)
    _emit("handoff.completed", {"id": h["id"], "status": h["status"], "to": h["to_nick"], "to_sid": h["to_sid"]})
    await _tell_sender(h, f"[shelldeck] {h['to_nick']} {'finished' if status == 'done' else 'gave up on'} hand-off {h['id']}: {result or '(no summary)'}")
    return h


def _orphan(session_id: str) -> list[dict]:
    """A terminal ended: its open hand-offs can't finish."""
    gone = db.list_handoffs(to_sid=session_id, status="open")
    for h in gone:
        db.close_handoff(h["id"], "exited", "the terminal closed before the task was done")
        _handoff_files(db.get_handoff(h["id"]) or h)
    return gone


async def _orphaned(session_id: str) -> None:
    for h in await asyncio.to_thread(_orphan, session_id):
        _emit("handoff.completed", {"id": h["id"], "status": "exited", "to": h["to_nick"], "to_sid": h["to_sid"]})
        if not STOPPING.is_set():
            await _tell_sender(h, f"[shelldeck] {h['to_nick']}'s terminal closed before hand-off {h['id']} was done")


@app.post("/api/spawn")
async def spawn_agent(payload: dict):
    """A sub-agent in a new terminal of the parent's project, started on a hand-off. Sub-agents can't spawn."""
    parent = db.get_session(str(payload.get("parent") or ""))
    task = str(payload.get("task") or "").strip()[:20000]
    if not parent:
        return err("parent_not_found", 404)
    if parent.get("parent"):
        return err("subagents_cannot_spawn", 403)
    if not task:
        return err("task_required")
    key = str(payload.get("agent") or "").strip() or await _agent_in(parent["id"]) or "claude"
    if key not in team.SPAWN:
        return err(f"cannot_spawn:{key}")
    model = team.pick_model(key, payload.get("model"), task)
    if model and not team.SAFE_MODEL.match(model):
        return err("invalid_model")
    project = db.get_project(parent["project_id"])
    if not project or not Path(project["path"]).is_dir():
        return err("project_not_found", 404)
    shell = parent.get("shell") or ""
    # the project root, so the kickoff prompt's relative .shelldeck/ path works
    session = db.add_session(project["id"], cwd=project["path"], shell=shell, name=agents.AGENTS[key][0], parent=parent["id"])
    h = db.add_handoff(project_id=project["id"], from_sid=parent["id"], from_nick=parent.get("nick"), to_sid=session["id"],
                       to_nick=session["nick"], agent=key, model=model, task=task)
    await asyncio.to_thread(_handoff_files, h)
    line = team.spawn_line(key, model, team.kickoff(session["nick"], parent.get("nick") or "the user", h["id"]),
                           await asyncio.to_thread(integrations.run_flags, key))
    try:
        _attach(session, 30, 120)
    except Exception:  # noqa: BLE001 - spawn failures come from winpty/OS
        log.exception("spawn failed for session %s", session["id"])
        return err("spawn_failed", 500)
    starter = asyncio.create_task(_start_agent(session["id"], line))
    background.add(starter)
    starter.add_done_callback(background.discard)
    await _broadcast({"type": "spawned", "session_id": session["id"], "parent": parent["id"]})
    return {"session": session, "handoff": h, "agent": key, "model": model, "command": line}


@app.post("/api/agent-start")
async def start_agent(payload: dict):
    """A new terminal in a project running an agent, launched with its own command-line flags: model, per-run hooks
    (Claude's --settings) and the initial prompt (team.SPAWN). A prompt that isn't plain words goes to a file."""
    key = str(payload.get("agent") or "")
    if key not in agents.AGENTS or not agents.AGENTS[key][1]:
        return err("unknown_agent", 404)
    project = db.get_project(str(payload.get("project_id") or ""))
    if not project:
        cwd = str(payload.get("cwd") or "")
        sd = next((p for p in db.list_projects() if cwd and (Path(cwd).resolve() == Path(p["path"]).resolve()
                                                          or Path(p["path"]).resolve() in Path(cwd).resolve().parents)), None)
        project = sd
    if not project or not Path(project["path"]).is_dir():
        return err("project_not_found", 404)
    prompt = " ".join(str(payload.get("prompt") or "").split())[:20000]
    if prompt and key not in team.SPAWN:
        return err("no_prompt_flag")
    if prompt and (not team.SAFE_PROMPT.match(prompt) or len(prompt) > 400):
        folder = Path(project["path"]) / ".shelldeck" / "prompts"
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder.parent / ".gitignore").exists():
            (folder.parent / ".gitignore").write_text("*\n", encoding="utf-8")
        name = f"{db.short_id()}.md"
        (folder / name).write_text(str(payload.get("prompt")).strip() + "\n", encoding="utf-8")
        prompt = f"Read and do the task in .shelldeck/prompts/{name}"
    model = str(payload.get("model") or "") or None
    try:
        line = team.launch_line(key, model, prompt or None, await asyncio.to_thread(integrations.run_flags, key))
    except ValueError as e:
        return err(str(e))
    session = db.add_session(project["id"], cwd=project["path"], shell="", name=agents.AGENTS[key][0])
    try:
        _attach(session, 30, 120)
    except Exception:  # noqa: BLE001
        log.exception("agent start failed for session %s", session["id"])
        return err("spawn_failed", 500)
    starter = asyncio.create_task(_start_agent(session["id"], line))
    background.add(starter)
    starter.add_done_callback(background.discard)
    return {"session": session, "agent": key, "command": line}


async def _start_agent(session_id: str, line: str) -> None:
    """Type the agent command once the shell shows its first prompt (shell integration marks it; WSL has none)."""
    for _ in range(100):
        if "\x1b]133;A" in manager.history(session_id):
            break
        await asyncio.sleep(0.1)
    await _type(session_id, line)


@app.get("/api/skills")
async def skills_status():
    return {"skills": [{"agent": k, "label": agents.AGENTS[k][0], "installed": team.installed(k)} for k in team.TARGETS]}


@app.post("/api/skills")
async def skills_install(payload: dict):
    """Install (or remove) the shelldeck skill for these agents (default: every agent CLI on PATH)."""
    keys = [k for k in payload.get("agents") or [] if k in team.TARGETS] or None
    remove = bool(payload.get("remove"))
    done = await asyncio.to_thread(team.install, keys, remove)
    if not keys:
        db.set_setting("agent_skills", "off" if remove else "on")
    return {"results": [{"agent": a, "path": p, "status": st} for a, p, st in done]}


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
            now = time.monotonic()
            if now - out_at.get(session_id, 0) > QUIET_S:  # a new burst of output
                busy_since[session_id], burst[session_id] = now, ""
            burst[session_id] = (burst[session_id] + data)[-16000:]
            out_at[session_id] = now
            if session_id in ask_buf:
                ask_buf[session_id] = (ask_buf[session_id] + data)[-16000:]
                last_out_at[session_id] = time.monotonic()
            if "\x1b]133;B" in data:  # ponytail: a mark split across two reads is missed until the next prompt
                at_prompt[session_id] = True
            if (m := CWD_REPORT.findall(data)) and m[-1] != last_cwd.get(session_id):
                last_cwd[session_id] = m[-1]
                db.update_session_cwd(session_id, m[-1])
            await _send_all(session_id, {"type": "output", "data": data})
        log.info("session %s exited", session_id)
        at_prompt.pop(session_id, None)
        await _send_all(session_id, {"type": "exit"})
        for ws in list(sockets.pop(session_id, ())):
            await _close(ws)
    finally:
        readers.pop(session_id, None)
        for d in (out_at, busy_since, burst, report_tokens):
            d.pop(session_id, None)
        manager.terminate(session_id)
        try:
            t = asyncio.get_running_loop().create_task(_orphaned(session_id))
            background.add(t)
            t.add_done_callback(background.discard)
        except RuntimeError:  # loop closing
            pass


def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _remote_line(session: dict) -> str | None:
    """The ssh command for a terminal in a remote machine's project, or None for a local one."""
    project = db.get_project(session.get("project_id") or "") if session.get("project_id") else None
    if not project or not project.get("remote_id"):
        return None
    r = db.get_remote(project["remote_id"])
    if not r:
        return None
    try:
        r = {**r, **remotes.validate(r)}
        return remotes.ssh_line(r, project.get("remote_path") or "~", session.get("shell") or get_settings()["default_shell"])
    except remotes.RemoteError:
        log.warning("remote %s has invalid stored values", r["id"])
        return None


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
            extra_env={k: v for k, v in (("SHELLDECK_NICK", session.get("nick")), ("SHELLDECK_PARENT", session.get("parent")),
                                         ("SHELLDECK_AGENT_REPORT_TOKEN", report_tokens.setdefault(sid, secrets.token_urlsafe(24)))) if v},
        )
        if line := _remote_line(session):  # a remote project's terminal: ssh there, every time its shell starts
            t = asyncio.get_running_loop().create_task(_start_agent(sid, line))
            background.add(t)
            t.add_done_callback(background.discard)
        else:
            try:
                _auto_resume(sid)
            except Exception:  # noqa: BLE001 - a resume problem must never stop the shell from opening
                log.warning("agent auto-resume failed", exc_info=True)
    if session.get("parent"):
        ask_buf.setdefault(sid, "")  # watch this sub-agent for questions (_watch_questions)
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
        for sid, st in list(agent_status.items()):  # a tab that opens later sees current states too
            await ws.send_json({"type": "agent_state", "session_id": sid, "state": _legacy(st), "status": st})
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
        if session_id in at_prompt:
            await ws.send_text(json.dumps({"type": "prompt", "at": at_prompt[session_id]}))
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
                data = str(msg.get("data", ""))
                if "\r" in data and session_id in at_prompt:
                    at_prompt[session_id] = False
                manager.write(session_id, data)
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
    await _broadcast({"type": "alarm", "task": _serialize_task(task)})


async def _broadcast(message: dict, host_only: bool = False) -> int:
    """Send to every open browser (the alarm socket), or with host_only to those on this machine and not
    on the share tunnel; returns how many got it."""
    data, sent = json.dumps(message), 0
    for ws in list(alarm_sockets):
        if host_only and (_via_share(ws) or not _is_local(ws)):
            continue
        try:
            await ws.send_text(data)
            sent += 1
        except (RuntimeError, WebSocketDisconnect):
            alarm_sockets.discard(ws)
    return sent


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
    # terminals inherit these, so `sd` inside them reaches this server and its data folder
    os.environ["SHELLDECK_PORT"] = str(port)
    if "SHELLDECK_HOME" in os.environ:
        os.environ["SHELLDECK_HOME"] = str(db.config_dir().resolve())
    scheme = "https" if certfile else "http"
    SELF_URL["url"] = f"{scheme}://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
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
