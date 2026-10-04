import json
import os
import queue
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import auth, db
from . import share as share_mod

app = typer.Typer(
    help="shelldeck: project-first multi-terminal. Run with no arguments to open the app.",
    no_args_is_help=False,
    add_completion=False,
)
schedule_app = typer.Typer(help="Scheduled jobs.")
task_app = typer.Typer(help="Personal task board.")
integration_app = typer.Typer(help="Agent integration capabilities.")
app.add_typer(schedule_app, name="schedule")
app.add_typer(task_app, name="task")
app.add_typer(integration_app, name="integration")

HOST = "127.0.0.1"
CFG = {"port": int(os.environ.get("SHELLDECK_PORT", "5455"))}
console = Console()


def _scheme() -> str:
    """http, or https when the server on this port was started with --cert."""
    try:
        info = json.loads((db.config_dir() / "server.json").read_text(encoding="utf-8"))
        return info["scheme"] if info.get("port") == CFG["port"] else "http"
    except (OSError, ValueError, KeyError):
        return "http"


def _url(path: str = "") -> str:
    return f"{_scheme()}://{HOST}:{CFG['port']}{path}"


def _open_url(req, timeout: float):
    # the CLI only talks to this machine; a self-signed certificate is expected there
    ctx = ssl._create_unverified_context() if req.full_url.startswith("https:") else None
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _api(path: str, method: str = "GET", payload: dict | None = None, timeout: float = 10) -> dict:
    """Call the local server. Exits with a readable message on failure."""
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Shelldeck-Token": auth.read_cli_token()}
    req = urllib.request.Request(_url(path), data=data, method=method, headers=headers)
    try:
        with _open_url(req, timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            code = body.get("error", e.reason) + (f": {body['detail']}" if body.get("detail") else "")
        except ValueError:
            code = e.reason
        if code in ("locked", "setup_required"):
            code = "the CLI token was rejected; restart shelldeck (`sd stop`, then `sd`)"
        typer.echo(f"error: {str(code).replace('_', ' ')}", err=True)
        raise typer.Exit(1) from None
    except OSError:
        typer.echo(f"shelldeck is not running on port {CFG['port']} (start it with `shelldeck`)", err=True)
        raise typer.Exit(1) from None


def _health() -> str | None:
    """'ok' if shelldeck answers, 'other' if something else holds the port, None if free."""
    try:
        with _open_url(urllib.request.Request(_url("/api/health")), 1.5) as r:
            return "ok" if json.loads(r.read().decode()).get("app") == "shelldeck" else "other"
    except urllib.error.HTTPError:
        return "other"
    except (OSError, ValueError):
        return None


def ensure_server() -> None:
    state = _health()
    if state == "ok":
        return
    if state == "other":
        typer.echo(f"port {CFG['port']} is used by another program; try `shelldeck --port 5456`", err=True)
        raise typer.Exit(1)
    # a hidden console of its own, not DETACHED_PROCESS: venv python.exe is a launcher that starts the real
    # python, and a console-less parent makes Windows open a new (visible) terminal window for that child
    detach = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    subprocess.Popen(
        [sys.executable, "-m", "shelldeck", "--port", str(CFG["port"]), "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **detach,
    )
    for _ in range(60):
        time.sleep(0.25)
        if _health() == "ok":
            return
    typer.echo(f"server did not start; see {db.config_dir() / 'shelldeck.log'}", err=True)
    raise typer.Exit(1)


def _browser() -> str | None:
    """Edge or Chrome, for a chromeless --app window."""
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates += [
                Path(base) / "Microsoft/Edge/Application/msedge.exe",
                Path(base) / "Google/Chrome/Application/chrome.exe",
            ]
    found = next((str(p) for p in candidates if p.exists()), None)
    names = ("msedge", "chrome", "microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser")
    return found or next((p for n in names if (p := shutil.which(n))), None)


def open_window(path: str = "/") -> None:
    url = _url(path)
    exe = _browser() if CFG.get("app") else None
    if not exe:
        webbrowser.open(url)
        return
    # default browser profile: a fresh --user-data-dir triggers Edge sign-in/sync prompts
    subprocess.Popen(
        [exe, f"--app={url}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    port: int = typer.Option(CFG["port"], "--port", "-p", envvar="SHELLDECK_PORT", help="Server port."),
    no_window: bool = typer.Option(False, "--no-window", help="Print the URL instead of opening the browser."),
    app_window: bool = typer.Option(False, "--app", help="Open a chromeless Edge/Chrome app window instead of a browser tab."),
):
    CFG["port"] = port
    CFG["no_window"] = no_window
    CFG["app"] = app_window
    if ctx.invoked_subcommand is None:
        web()


@app.command()
def web():
    """Start the server if needed and open shelldeck in the browser (same as no arguments)."""
    started = _health() is None
    ensure_server()
    _banner(_server_host(), "started" if started else "already running")
    _show("/")
    if _own_console():
        try:
            input("  Press Enter to close this window (shelldeck keeps running)")
        except (EOFError, KeyboardInterrupt):
            pass


def _own_console() -> bool:
    """True when Windows opened a console just for us (Win+R, Start menu, a shortcut): it closes when we exit."""
    if sys.platform != "win32" or not sys.stdin.isatty():
        return False
    import ctypes

    import psutil

    pids = (ctypes.c_uint * 64)()
    attached = set(pids[: ctypes.windll.kernel32.GetConsoleProcessList(pids, 64)])
    # climb our ancestors that share this console; the topmost one created it (a shell, or our own launcher)
    top = psutil.Process()
    while (parent := top.parent()) and parent.pid in attached:
        top = parent
    return top.name().lower() in ("sd.exe", "shelldeck.exe", "python.exe")


LOGO = (
    r"      _            _  _      _              _",
    r" ___ | |__    ___ | || |  __| |  ___   ___ | | __",
    r"/ __|| '_ \  / _ \| || | / _` | / _ \ / __|| |/ /",
    r"\__ \| | | ||  __/| || || (_| ||  __/| (__ |   <",
    r"|___/|_| |_| \___||_||_| \__,_| \___| \___||_|\_\ ",
)
LOGO_COLORS = ("#ddd6fe", "#c4b5fd", "#a78bfa", "#8b5cf6", "#7c3aed")  # violet accent, top to bottom


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("shelldeck")
    except PackageNotFoundError:
        return "dev"


def _server_host() -> str:
    """Bind address of the running server, from server.json (the detached server is always loopback)."""
    try:
        info = json.loads((db.config_dir() / "server.json").read_text(encoding="utf-8"))
        return info.get("host", HOST) if info.get("port") == CFG["port"] else HOST
    except (OSError, ValueError):
        return HOST


def _lan_ip() -> str | None:
    """This machine's LAN address: the source address of the default route (a UDP connect sends nothing)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return None


def _banner(host: str, status: str, scheme: str = "") -> None:
    """Logo, version and URLs. rich drops the colors when the output isn't an ANSI terminal (or NO_COLOR is set)."""
    console.print()
    for line, color in zip(LOGO, LOGO_COLORS, strict=True):
        console.print(f"  {line}", style=f"bold {color}", highlight=False, markup=False)
    console.print(f"\n  [bold]v{_version()}[/]  [dim]project-first multi-terminal - {status}[/]\n", highlight=False)
    scheme, port = scheme or _scheme(), CFG["port"]
    console.print(f"  [green]>[/]  [bold]Local:[/]    [cyan]{scheme}://127.0.0.1:{port}[/]", highlight=False)
    if host in LOOPBACK_HOSTS:
        console.print("  [dim]>  Network:  off  (share: sd serve --host 0.0.0.0 --cert C --key K)[/]", highlight=False)
    else:
        net = _lan_ip() if host in ("0.0.0.0", "::") else host
        console.print(f"  [green]>[/]  [bold]Network:[/]  [cyan]{scheme}://{net or host}:{port}[/]", highlight=False)
    console.print()


def _show(path: str) -> None:
    if CFG.get("no_window"):
        typer.echo(_url(path))
    else:
        open_window(path)


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address. Keep 127.0.0.1 unless you know why."),
    cert: Path = typer.Option(None, "--cert", exists=True, dir_okay=False, help="TLS certificate (PEM) for HTTPS."),
    key: Path = typer.Option(None, "--key", exists=True, dir_okay=False, help="TLS private key (PEM)."),
    insecure_http: bool = typer.Option(False, "--insecure-http", help="Allow plain HTTP on a non-local address (trusted network only)."),
):
    """Run the server in the foreground."""
    from .server import run

    if bool(cert) != bool(key):
        typer.echo("error: --cert and --key go together", err=True)
        raise typer.Exit(2)
    if host not in LOOPBACK_HOSTS and not cert and not insecure_http:
        typer.echo(
            f"error: refusing to serve plain HTTP on {host}: passwords and terminal traffic would cross the network unencrypted.\n"
            "  Safest: keep the default 127.0.0.1 and use an SSH tunnel: ssh -L 5455:127.0.0.1:5455 you@vm\n"
            "  Or serve HTTPS: --cert cert.pem --key key.pem   (or add --insecure-http on a trusted network)",
            err=True,
        )
        raise typer.Exit(2)
    db.init_db()
    _banner(host, "foreground, Ctrl+C stops", "https" if cert else "http")
    run(host=host, port=CFG["port"], certfile=str(cert) if cert else None, keyfile=str(key) if key else None)


@app.command("reset-password")
def reset_password(yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation.")):
    """EMERGENCY, on the host only: forget the password and sign out every browser."""
    if not yes and not typer.confirm("Forget the shelldeck password and sign out every browser?"):
        raise typer.Exit(1)
    db.init_db()
    code = auth.reset()
    typer.echo("Password removed and every login ended. Open shelldeck to create a new password.")
    typer.echo(f"From another machine, setup asks for this code: {code}")


@app.command("login-link")
def login_link():
    """EMERGENCY, on the host only: print a one-time URL (5 minutes) that logs a browser in."""
    ensure_server()
    path = _api("/api/auth/login-link", "POST", {})["path"]
    typer.echo(_url(path))
    typer.echo("One use, valid for 5 minutes. Through an SSH tunnel, open it on your machine as-is.")


def _show_share(state: dict) -> None:
    import segno

    url = state["link"]["url"]
    if sys.stdout.isatty():
        segno.make(url, error="l").terminal(compact=(sys.stdout.encoding or "").lower().startswith("utf"))
    typer.echo(f"\n  {url}\n")
    typer.echo("  Open it on the other device within 10 minutes. It works once; allow the device here or in shelldeck,")
    typer.echo("  then log in with your password. Another device: `sd share --new-link`. Stop: `sd share stop`.")


def _watch_share(stopped: threading.Event) -> None:
    """Ask here before a device that opened the link gets in (a host browser may answer first),
    and set `stopped` once the share is gone."""
    asks: queue.Queue = queue.Queue()
    asked: set[str] = set()
    answered: set[str] = set()  # answered at this prompt

    def poll() -> None:
        while True:
            try:
                state = _api("/api/share")
            except typer.Exit:
                state = None  # server busy or restarting; try again
            if state is not None:
                if not state["sharing"]:
                    stopped.set()
                    return
                ids = {p["id"] for p in state["pending"]}
                for p in state["pending"]:
                    if p["id"] not in asked:
                        asked.add(p["id"])
                        asks.put(p)
                for gone in asked - ids - answered:
                    answered.add(gone)
                    typer.echo(f"\n  device {gone} was answered in the browser (or expired)")
            time.sleep(3)

    def ask() -> None:
        while True:
            p = asks.get()
            if p["id"] in answered:
                continue
            typer.echo(f"\n  A device opened the link: {p['ip']}\n  {p['agent'][:100]}")
            allow = input("  Allow it? [y/N] ").strip().lower() in ("y", "yes")
            if p["id"] in answered:
                typer.echo("  already answered")
                continue
            answered.add(p["id"])
            try:
                _api("/api/share/decide", "POST", {"id": p["id"], "allow": allow})
                typer.echo("  allowed; it can log in with your password now" if allow else "  denied")
            except typer.Exit:
                typer.echo("  that request was already answered or expired")

    for target in (poll, ask):
        threading.Thread(target=target, daemon=True).start()


@app.command()
def share(
    action: str = typer.Argument(None, help="`stop` ends the running share."),
    new_link: bool = typer.Option(False, "--new-link", help="Print a fresh one-use link for the running share."),
):
    """Reach this shelldeck from another device: HTTPS Cloudflare quick tunnel, one-use QR link, host approval, then your password."""
    if action not in (None, "stop"):
        typer.echo(f"unknown action {action!r}; try `sd share` or `sd share stop`", err=True)
        raise typer.Exit(2)
    ensure_server()
    state = _api("/api/share")
    if action == "stop":
        _api("/api/share", "DELETE")
        typer.echo("sharing stopped" if state["sharing"] else "not sharing")
        return
    if new_link:
        if not state["sharing"]:
            typer.echo("not sharing; start with `sd share`", err=True)
            raise typer.Exit(1)
        _show_share(_api("/api/share/link", "POST", {}))
        return
    if not _api("/api/auth/status").get("has_password"):
        typer.echo("Set a password first: open shelldeck on this machine (`sd`), then run `sd share` again.", err=True)
        raise typer.Exit(1)
    if not state["strong_password"]:
        typer.echo(
            f"Sharing puts the login page on the internet, so it needs a password of {auth.SHARE_MIN_PASSWORD}+ characters.\n"
            "Change it in Settings (or, if it is already that long, log in once), then run `sd share` again.",
            err=True,
        )
        raise typer.Exit(1)
    if not state["terms_accepted"]:
        typer.echo("\nBefore you share:\n")
        for line in state["terms"]:
            typer.echo(f"  - {line}")
        try:
            ok = input("\nDo you accept these terms and share at your own risk? [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            ok = False
        if not ok:
            typer.echo("not sharing: the terms were not accepted", err=True)
            raise typer.Exit(1)
        _api("/api/share/terms", "POST", {"accept": True})
    if not state["cloudflared"]:
        typer.echo(f"cloudflared is not installed ({share_mod.HINT})", err=True)
        raise typer.Exit(1)
    started = not state["sharing"]
    if started:
        typer.echo("  starting the tunnel...")
        state = _api("/api/share", "POST", {}, timeout=40)
    if not state["link"]:  # attached to a running share whose link was used or expired
        state = _api("/api/share/link", "POST", {})
    _show_share(state)
    typer.echo("  Ctrl+C " + ("stops sharing and signs them out." if started else "leaves this share running (started elsewhere)."))
    stopped = threading.Event()
    try:
        _watch_share(stopped)
        while not stopped.wait(1):
            pass
        typer.echo("sharing was stopped")
        return
    except KeyboardInterrupt:
        pass
    if started and _health() == "ok":
        _api("/api/share", "DELETE")
        typer.echo("sharing stopped")


@app.command()
def stop():
    """Stop the background server (closes all terminals)."""
    if _health() != "ok":
        typer.echo("shelldeck is not running")
        return
    _api("/api/shutdown", "POST", {})
    typer.echo("stopped")


@app.command("open")
def open_(
    path: str = typer.Argument(".", help="Project folder."),
    shell: str = typer.Option("", "--shell", "-s", help="Shell kind, e.g. pwsh, cmd, wsl, bash, zsh (default from settings)."),
):
    """Add a folder as a project and open a terminal in it."""
    folder = str(Path(path).resolve())
    ensure_server()
    project = _api("/api/projects", "POST", {"path": folder})
    session = _api("/api/sessions", "POST", {"project_id": project["id"], "shell": shell})
    _show(f"/?session={session['id']}")


@app.command("list")
def list_sessions():
    """List terminals grouped by project."""
    projects = _api("/api/projects")["projects"]
    if not projects:
        typer.echo("no projects")
        return
    table = Table(box=None)
    for col in ("Project", "Terminal", "Shell", "ID", "Running"):
        table.add_column(col)
    for p in projects:
        if not p["sessions"]:
            table.add_row(p["name"], "-", "", "", "")
        for s in p["sessions"]:
            table.add_row(p["name"], s.get("name") or "", s.get("shell") or "default", s["id"], "yes" if s["alive"] else "no")
    console.print(table)


@app.command()
def info():
    """Show details about the shelldeck terminal you are in."""
    sid = os.environ.get("SHELLDECK_SESSION_ID")
    if not sid:
        typer.echo("not inside a shelldeck terminal", err=True)
        raise typer.Exit(1)
    s = _api(f"/api/sessions/{sid}")["session"]
    p = s.get("project") or {}
    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()
    for k, v in (
        ("Session", s.get("id", sid)),
        ("Name", s.get("name") or ""),
        ("Nick", s.get("nick") or ""),
        ("Parent", s.get("parent") or ""),
        ("Project", p.get("name", "")),
        ("Project path", p.get("path", "")),
        ("Working dir", s.get("cwd", "")),
        ("Shell", s.get("shell") or "default"),
        ("Size", f"{s.get('cols', 0)}x{s.get('rows', 0)}"),
        ("Created", s.get("created_at", "")),
    ):
        table.add_row(k, str(v))
    console.print(table)


def _session(target: str) -> dict:
    """A terminal by id, nick, name (case-insensitive) or id prefix."""
    sessions = _api("/api/sessions")["sessions"]
    t = target.casefold()
    hits = ([x for x in sessions if x["id"] == target] or [x for x in sessions if (x.get("nick") or "").casefold() == t]
            or [x for x in sessions if (x.get("name") or "").casefold() == t] or [x for x in sessions if x["id"].startswith(target)])
    if len(hits) != 1:
        typer.echo(f"error: {'no' if not hits else 'more than one'} terminal matches {target!r} (see `sd agents --all`)", err=True)
        raise typer.Exit(1)
    return hits[0]


@app.command()
def agents(all_: bool = typer.Option(False, "--all", "-a", help="Every project, not just the one this terminal is in.")):
    """AI agents running in shelldeck terminals (peers you can `sd peek` and `sd tell`)."""
    me = os.environ.get("SHELLDECK_SESSION_ID")
    data = _api("/api/agents")
    running = data["running"]
    if me and not all_:
        mine = next((r["project_id"] for r in _api("/api/sessions")["sessions"] if r["id"] == me), None)
        running = [r for r in running if r["project_id"] == mine]
    if not running:
        typer.echo("no AI agents running" + ("" if all_ or not me else " in this project (try --all)"))
    for r in running:
        you = "  (you)" if r["session_id"] == me else ""
        typer.echo(f"{r['nick'] or '-'}  {r['session_id']}  {r['label']}  model={r['model'] or '?'}  project={r['project']}  cwd={r['cwd']}{you}")
    if all_ and data.get("outside"):
        typer.echo("\noutside shelldeck:")
        for a in data["outside"]:
            typer.echo(f"pid {a['pid']}  {a['label']}  model={a['model'] or '?'}  cwd={a['cwd']}  started by {a['host'] or '?'}")
    if all_:
        typer.echo("\ninstalled: " + (", ".join(f"{a['command']} ({len(a['models'])} models)" for a in data["agents"] if a["installed"]) or "none"))


@integration_app.command("list")
def integration_list(json_: bool = typer.Option(False, "--json", help="Print machine-readable JSON.")):
    """List built-in integration coverage and lifecycle/resume capabilities."""
    rows = _api("/api/integrations")["integrations"]
    if json_:
        typer.echo(json.dumps(rows, indent=2))
        return
    table = Table("Agent", "Method", "Lifecycle", "Resume", "CLI", "Integration", "Notes")
    for item in rows:
        table.add_row(item["agent"], item["kind"], "yes" if item["lifecycle"] else "screen/fallback",
                      "yes" if item["session_restore"] else "no", "found" if item["available"] else "not found",
                      item["status"].replace("_", " "), item["notes"])
    console.print(table)


def _integration_rows(agent: str = "") -> list[dict]:
    rows = [r for r in _api("/api/integrations")["integrations"] if r.get("installable")]
    if agent and not any(r["agent"] == agent for r in rows):
        typer.echo(f"error: no installable integration for {agent!r} (have: {', '.join(r['agent'] for r in rows)})", err=True)
        raise typer.Exit(1)
    return [r for r in rows if not agent or r["agent"] == agent]


@integration_app.command("detect")
def integration_detect():
    """Agent CLIs found on this machine that have an installable integration."""
    for r in _integration_rows():
        typer.echo(f"{r['agent']:<10} {'found' if r['available'] else 'not found':<10} {r['status'].replace('_', ' ')}")


@integration_app.command("status")
def integration_status(agent: str = typer.Argument("", help="One agent (default: all installable).")):
    """Whether each lifecycle hook/plugin is installed, outdated (re-install) or not installed."""
    for r in _integration_rows(agent):
        typer.echo(f"{r['agent']:<10} {r['status'].replace('_', ' ')}")


@integration_app.command("install")
def integration_install(agents_: list[str] = typer.Argument(None, metavar="[AGENT]...", help="Agents (default: every one found).")):
    """Add shelldeck's lifecycle hook/plugin to agents' configs (merged; other hooks stay; a backup is kept)."""
    targets = agents_ or [r["agent"] for r in _integration_rows() if r["config_found"]]
    if not targets:
        typer.echo("no supported agent CLI found; name one: sd integration install claude")
    failed = False
    for a in targets:
        try:
            _integration_rows(a)
            r = _api(f"/api/integrations/{a}", "POST")
        except typer.Exit:  # the error is printed; carry on with the rest
            failed = True
            continue
        typer.echo(f"{a}: {r['status'].replace('_', ' ')} ({r['file']})" + ("; restart OpenCode to load it" if a == "opencode" else ""))
    if failed:
        raise typer.Exit(1)


@integration_app.command("uninstall")
def integration_uninstall(agents_: list[str] = typer.Argument(..., metavar="AGENT...")):
    """Remove only shelldeck's hook/plugin from agents' configs."""
    for a in agents_:
        _integration_rows(a)
        r = _api(f"/api/integrations/{a}", "DELETE")
        typer.echo(f"{a}: {r['status'].replace('_', ' ')} ({r['file']})")


@app.command()
def peek(
    target: str = typer.Argument(..., help="Terminal nick, id, id prefix or name."),
    lines: int = typer.Option(40, "--lines", "-n", help="How many of the last lines."),
):
    """Print the last lines of another terminal (e.g. to check another agent's progress)."""
    sys.stdout.reconfigure(errors="replace")  # TUI box drawing on a cp1252 console
    typer.echo(_api(f"/api/sessions/{_session(target)['id']}/screen?lines={lines}")["text"])


@app.command()
def tell(
    target: str = typer.Argument(..., help="Terminal nick, id, id prefix or name."),
    message: str = typer.Argument(..., help="Text to type into it."),
    raw: bool = typer.Option(False, "--raw", help="Send the text as is: no sender tag, no Enter."),
):
    """Type a message into another terminal and press Enter (agent-to-agent messaging)."""
    s = _session(target)
    me = os.environ.get("SHELLDECK_NICK") or os.environ.get("SHELLDECK_SESSION_ID")
    if not raw and me:
        message = f"[message from {me}; reply with: sd tell {me} \"...\"] {message}"
    _api(f"/api/sessions/{s['id']}/input", "POST", {"text": message, "enter": not raw})
    typer.echo(f"sent to {s.get('nick') or s.get('name') or s['id']}")


KEYS = {"enter": "\r", "esc": "\x1b", "tab": "\t", "up": "\x1b[A", "down": "\x1b[B", "left": "\x1b[D", "right": "\x1b[C", "space": " "}


@app.command()
def answer(
    target: str = typer.Argument(..., help="Terminal nick, id or name (e.g. your sub-agent)."),
    keys: list[str] = typer.Argument(..., help="Keys to press in order: text, or enter, esc, tab, up, down, left, right, space."),
):
    """Press keys in another terminal, e.g. to answer a sub-agent's approval menu: sd answer Maya 1."""
    s = _session(target)
    for k in keys:
        _api(f"/api/sessions/{s['id']}/input", "POST", {"text": KEYS.get(k.lower(), k), "enter": False})
        time.sleep(0.15)  # one key at a time: TUIs read a burst as a paste
    typer.echo(f"pressed {' '.join(keys)} in {s.get('nick') or s['id']}")


@app.command()
def close(
    target: str = typer.Argument(..., help="Terminal nick, id or name, e.g. a sub-agent whose work is finished."),
    force: bool = typer.Option(False, "--force", help="Close it even though a hand-off to it is still open."),
):
    """Close a terminal and whatever runs in it. Agents: only your own sub-agents, and only once the user agreed."""
    s = _session(target)
    me = os.environ.get("SHELLDECK_SESSION_ID")
    name = s.get("nick") or s["id"]
    if s["id"] == me:
        typer.echo("error: that is your own terminal", err=True)
        raise typer.Exit(1)
    if me and s.get("parent") != me:  # an agent may not close the user's (or another agent's) terminals
        typer.echo(f"error: {name} is not your sub-agent; ask the user to close it", err=True)
        raise typer.Exit(1)
    still = [h["id"] for h in _api(f"/api/handoffs?to={s['id']}&status=open")["handoffs"]]
    if still and not force:
        typer.echo(f"error: {name} still has hand-off {', '.join(still)} open (it runs sd done when finished); --force closes it anyway", err=True)
        raise typer.Exit(1)
    _api(f"/api/sessions/{s['id']}", "DELETE")
    typer.echo(f"closed {name}")


def _me() -> str:
    me = os.environ.get("SHELLDECK_SESSION_ID")
    if not me:
        typer.echo("error: run this inside a shelldeck terminal", err=True)
        raise typer.Exit(1)
    return me


@app.command()
def spawn(
    task: str = typer.Argument(..., help="The task, self-contained: files, expected result, how to check it."),
    agent: str = typer.Option("", "--agent", "-a", help="claude, codex, devin, gemini, qwen or opencode (default: the one you run)."),
    model: str = typer.Option("auto", "--model", "-m", help="small, medium, large, auto (by the task) or a model name."),
):
    """Start a sub-agent in a new terminal of this project and hand it a task."""
    if os.environ.get("SHELLDECK_PARENT"):
        typer.echo("error: sub-agents cannot spawn agents; ask your parent (sd tell)", err=True)
        raise typer.Exit(1)
    r = _api("/api/spawn", "POST", {"parent": _me(), "task": task, "agent": agent, "model": model})
    started = r["command"].split(' "')[0]  # the agent and model, without the long kickoff prompt
    typer.echo(f"spawned {r['session']['nick']} ({r['session']['id']}): {started}")
    typer.echo(f"hand-off {r['handoff']['id']}; they run `sd done` when finished and you get a message. Check on them: sd peek {r['session']['nick']}")


@app.command()
def handoff(
    target: str = typer.Argument(..., help="Terminal nick, id or name with an agent running."),
    task: str = typer.Argument(..., help="The task."),
):
    """Hand a task to an agent already running in another terminal (tracked in .shelldeck/handoff.md)."""
    s = _session(target)
    h = _api("/api/handoffs", "POST", {"from": os.environ.get("SHELLDECK_SESSION_ID", ""), "to": s["id"], "task": task})
    typer.echo(f"hand-off {h['id']} sent to {h['to_nick']}")


@app.command()
def done(
    handoff_id: str = typer.Argument(..., help="Hand-off id (see `sd handoffs`)."),
    summary: str = typer.Argument("", help="What you did, or why you couldn't."),
    failed: bool = typer.Option(False, "--failed", help="You could not finish it."),
):
    """Close a hand-off you were given; the sender is told."""
    h = _api(f"/api/handoffs/{handoff_id}/done", "POST", {"result": summary, "failed": failed})
    typer.echo(f"hand-off {h['id']} {h['status']}; {h['from_nick'] or 'the sender'} was told")


@app.command()
def handoffs(all_: bool = typer.Option(False, "--all", "-a", help="Every project, not just this terminal's.")):
    """Hand-offs between terminals and their status."""
    me = os.environ.get("SHELLDECK_SESSION_ID")
    project = "" if all_ or not me else next((r["project_id"] for r in _api("/api/sessions")["sessions"] if r["id"] == me), "")
    rows = _api(f"/api/handoffs?project_id={project}")["handoffs"]
    if not rows:
        typer.echo("no hand-offs")
    for h in rows:
        typer.echo(f"{h['id']}  {h['status']:<7} {h['from_nick'] or 'user'} -> {h['to_nick']}  {' '.join(h['task'].split())[:70]}")
        if h.get("result"):
            typer.echo(f"         result: {' '.join(h['result'].split())[:100]}")


@app.command("install-skill")
def install_skill(
    agents_: list[str] = typer.Argument(None, metavar="[AGENT]...", help="Agents to set up (default: every agent CLI on PATH)."),
    remove: bool = typer.Option(False, "--remove", help="Remove it again (and stop the server re-adding it on start)."),
):
    """Teach agent CLIs the sd team commands (a skill, or a block in their global instructions file)."""
    from . import team

    unknown = [a for a in agents_ or [] if a not in team.TARGETS]
    if unknown:
        typer.echo(f"error: unknown agent {', '.join(unknown)}; one of {', '.join(team.TARGETS)}", err=True)
        raise typer.Exit(1)
    for agent, path, status in team.install(agents_ or None, remove):
        typer.echo(f"{status:<9} {agent:<9} {path}")
    if not agents_:
        db.init_db()
        db.set_setting("agent_skills", "off" if remove else "on")


def _show_file(file: Path, mode: str) -> None:
    path = str(file.expanduser().resolve())
    ensure_server()
    if not _api("/api/fs/show", "POST", {"path": path, "mode": mode})["delivered"]:
        from urllib.parse import quote

        _show(f"/?file={quote(path)}&mode={mode}")
    typer.echo(f"opened {path} in shelldeck")


@app.command()
def edit(file: Path = typer.Argument(..., dir_okay=False, help="File to edit (created on save if missing).")):
    """Open a file in shelldeck's built-in editor."""
    _show_file(file, "edit")


@app.command()
def view(file: Path = typer.Argument(..., exists=True, dir_okay=False, help="File to view.")):
    """Open a file in shelldeck's viewer (markdown rendered, images shown)."""
    _show_file(file, "view")


@app.command()
def render(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="File to render."),
    theme: str = typer.Option("monokai", "--theme", help="Syntax theme."),
):
    """Pretty-print a code or markdown file."""
    from rich.markdown import Markdown  # ~100ms of imports, only needed here
    from rich.syntax import Syntax

    text = file.read_text(encoding="utf-8", errors="replace")
    if file.suffix.lower() in (".md", ".markdown"):
        console.print(Markdown(text))
    else:
        lexer = Syntax.guess_lexer(str(file), text)
        console.print(Syntax(text, lexer, theme=theme, line_numbers=True))


@app.command()
def search(
    query: str = typer.Argument(..., help='Search query, e.g. "lazy load" or "auth token validation"'),
    root: Path = typer.Option(Path("."), "--root", "-r", file_okay=False, exists=True, help="Codebase root."),
    top: int = typer.Option(6, "--top", "-t", help="Max files to return."),
    context: int = typer.Option(3, "--context", "-c", help="Context lines around each hit."),
    per_file: int = typer.Option(3, "--per-file", help="Max snippets per file."),
    ext: str = typer.Option(None, "--ext", "-e", help="Only these extensions, e.g. py,ts"),
    glob: str = typer.Option(None, "--glob", help='Only paths matching glob(s), e.g. "src/*.py"'),
    tests: bool = typer.Option(False, "--tests", help="Don't down-rank test files."),
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild the index from scratch."),
    fmt: str = typer.Option("text", "--format", "-f", help="text, json or paths."),
):
    """Search a codebase with fastcontext (LLM-free code search)."""
    from .addons import fast_context as fc  # heavy module, only needed here

    if fmt not in ("text", "json", "paths"):
        typer.echo("--format must be text, json or paths", err=True)
        raise typer.Exit(1)
    retriever = fc.Retriever(
        str(root.resolve()),
        reindex=reindex,
        context=context,
        per_file=per_file,
        include_tests=tests,
        exts=ext.split(",") if ext else None,
        globs=glob.split(",") if glob else None,
    )
    out = retriever.run(query, top_k=top)
    if fmt == "json":
        print(fc.format_json(out))
    elif fmt == "paths":
        print(fc.format_paths(out))
    else:
        _render_search(out, fc)


def _render_search(out: dict, fc) -> None:
    """fast_context's text format, with rich highlighting for the snippets."""
    from rich.markup import escape
    from rich.syntax import Syntax

    conf = out["confidence"]
    filled = int(conf["score"] / 10)
    console.print(f"[bold cyan]Query:[/] {escape(out['query'])}")
    console.print(f"[bold]Confidence:[/] {conf['score']:.0f}/100 \\[{'#' * filled}{'.' * (10 - filled)}] {conf['label']}"
                  f"   [dim]({out['turns']} turn(s), {out['elapsed_ms']} ms)[/]", highlight=False)
    for r in conf["reasons"]:
        console.print(f"  [dim]- {escape(r)}[/]")
    if conf.get("suggestion"):
        console.print(f"  [yellow]tip:[/] {escape(conf['suggestion'])}")
    if out["definitions"]:
        console.print("\n[bold]Definitions:[/]")
        for d in out["definitions"]:
            console.print(f"  [cyan]{escape(d['name'])}[/] ({d['kind']})  {escape(d['path'])}:{d['line']}")
    if not out["results"]:
        console.print("\n[yellow]No relevant code found.[/]")
    for i, s in enumerate(out["results"], 1):
        console.print(f"\n[bold]{i}.[/] [cyan]{escape(s.path)}[/]   confidence {s.confidence:.0f} ({fc.label_of(s.confidence)})")
        console.print(f"   [dim]matched: {escape(', '.join(dict.fromkeys(s.present)))}[/]")
        for sn in s.snippets:
            sym = f"  [{sn.symbol}]" if sn.symbol else ""
            console.print(f"   [dim]--- {escape(f'{s.path}:{sn.start}-{sn.end}{sym}')}[/]")
            console.print(Syntax("\n".join(sn.text), fc.language_of(s.path) or "text", line_numbers=True,
                                 start_line=sn.start, highlight_lines=set(sn.hit_lines)))


def _project_id(project: str) -> str:
    folder = Path(project)
    if folder.is_dir():
        return _api("/api/projects", "POST", {"path": str(folder.resolve())})["id"]
    return project  # assume an id


# ------------------------------------------------------------------ schedule


@schedule_app.command("list")
def schedule_list():
    table = Table(box=None)
    for col in ("ID", "Name", "Cron", "Status", "Next run", "Runs"):
        table.add_column(col)
    for j in _api("/api/schedule/jobs")["jobs"]:
        status = j["last_status"] if j["enabled"] else "disabled"
        table.add_row(j["id"], j["name"], j["cron"], status, j.get("next_run_on") or "-", f"{j['successful_runs']}/{j['total_runs']}")
    console.print(table)


@schedule_app.command("add")
def schedule_add(
    name: str = typer.Argument(...),
    command: str = typer.Argument(...),
    project: str = typer.Option(".", "--project", help="Folder or project id."),
    cron: str = typer.Option("0 * * * *", "--cron"),
    timezone: str = typer.Option("UTC", "--timezone"),
    timeout: int = typer.Option(60, "--timeout"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
):
    payload = {
        "name": name,
        "command": command,
        "project_id": _project_id(project),
        "cron": cron,
        "timezone": timezone,
        "timeout_seconds": timeout,
        "enabled": enabled,
    }
    typer.echo(f"created {_api('/api/schedule/jobs', 'POST', payload)['id']}")


@schedule_app.command("run")
def schedule_run(job_id: str):
    typer.echo(_api(f"/api/schedule/jobs/{job_id}/run", "POST", {})["status"])


@schedule_app.command("toggle")
def schedule_toggle(job_id: str):
    typer.echo(f"enabled={_api(f'/api/schedule/jobs/{job_id}/toggle', 'POST', {})['enabled']}")


@schedule_app.command("delete")
def schedule_delete(job_id: str):
    _api(f"/api/schedule/jobs/{job_id}", "DELETE")
    typer.echo("deleted")


@schedule_app.command("logs")
def schedule_logs(job_id: str, limit: int = typer.Option(20, "--limit")):
    for run in _api(f"/api/schedule/jobs/{job_id}/runs?limit={limit}")["runs"]:
        console.rule(f"{run['run_at']}  {run['status']}  {run.get('duration_ms')}ms")
        typer.echo(run.get("output") or run.get("error") or "")


# --------------------------------------------------------------------- tasks


@task_app.command("list")
def task_list(column: str = typer.Option(None, "--column")):
    table = Table(box=None)
    for col in ("ID", "Title", "Column", "Priority", "Due"):
        table.add_column(col)
    for t in _api("/api/tasks")["tasks"]:
        if column and t["column"] != column:
            continue
        table.add_row(t["id"], t["title"], t["column"], t["priority"], t.get("due_at") or "-")
    console.print(table)


@task_app.command("add")
def task_add(
    title: str = typer.Argument(...),
    column: str = typer.Option("backlog", "--column"),
    priority: str = typer.Option("medium", "--priority"),
    due: str = typer.Option(None, "--due", help="ISO date-time"),
    reminder: str = typer.Option(None, "--reminder", help="ISO date-time"),
    tags: str = typer.Option("", "--tags"),
):
    payload = {"title": title, "column": column, "priority": priority, "due_at": due, "reminder_at": reminder, "tags": tags}
    typer.echo(f"created {_api('/api/tasks', 'POST', payload)['id']}")


@task_app.command("move")
def task_move(task_id: str, column: str):
    typer.echo(f"moved to {_api(f'/api/tasks/{task_id}/move', 'POST', {'column': column})['column']}")


@task_app.command("delete")
def task_delete(task_id: str):
    _api(f"/api/tasks/{task_id}", "DELETE")
    typer.echo("deleted")


@task_app.command("alarms")
def task_alarms():
    for a in _api("/api/tasks/alarms")["alarms"]:
        typer.echo(f"{a['id']}  {a['title']}  due={a.get('due_at')}")


# ------------------------------------------------------------------ agent lifecycle (server-owned)

agent_app = typer.Typer(help="Agent lifecycle status, waits and integration reports.")
app.add_typer(agent_app, name="agent")


def _status_line(r: dict) -> str:
    reason = f" ({r['reason']})" if r.get("reason") else ""
    return f"{r.get('nick') or r['session_id']}  {r.get('agent') or '?'}  {r['state']}{reason}  source={r['source']}"


@agent_app.command("status")
def agent_status(
    target: str = typer.Argument("", help="Terminal nick, id or name (default: every agent terminal)."),
    json_: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
):
    """Lifecycle state (idle/working/blocked/done/exited) of agent terminals, and where it came from."""
    sid = _session(target)["id"] if target else ""
    rows = _api(f"/api/agent-status?target={sid}")["agents"]
    nicks = {s["id"]: s.get("nick") for s in _api("/api/sessions")["sessions"]}
    for r in rows:
        r["nick"] = nicks.get(r["session_id"])
    if json_:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo("no agent status" + (f" for {target}" if target else ""))
    for r in rows:
        typer.echo(_status_line(r))


def _duration(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", text.strip())
    if not m:
        raise typer.BadParameter("use e.g. 30s, 10m or 1h")
    return float(m[1]) * {"": 1, "s": 1, "m": 60, "h": 3600}[m[2]]


@agent_app.command("wait")
def agent_wait(
    target: str = typer.Argument(..., help="Terminal nick, id or name running an agent."),
    until: list[str] = typer.Option(["idle"], "--until", "-u", help="idle, working, blocked, done or exited (repeatable)."),
    timeout: str = typer.Option("10m", "--timeout", "-t", help="Give up after this long (30s, 10m, 1h)."),
    json_: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
):
    """Block until an agent reaches a state; exits 0 when reached, 2 on timeout, 3 if the agent exited or was replaced."""
    secs = _duration(timeout)
    r = _api("/api/agent-wait", "POST", {"session_id": _session(target)["id"], "until": until, "timeout": secs}, timeout=secs + 15)
    if json_:
        typer.echo(json.dumps(r, indent=2))
    else:
        st = r.get("status") or {}
        typer.echo(f"{r['result']}: {st.get('state', 'unknown')}" + (f" ({st['reason']})" if st.get("reason") else ""))
    raise typer.Exit({"reached": 0, "timeout": 2}.get(r["result"], 3))


@agent_app.command("resume")
def agent_resume(target: str = typer.Argument("", help="Terminal nick, id or name (default: list resumable sessions).")):
    """Start a terminal's stored agent session again (e.g. `claude --resume <id>` after a restart)."""
    if not target:
        rows = _api("/api/agent-sessions")["sessions"]
        nicks = {s["id"]: s.get("nick") for s in _api("/api/sessions")["sessions"]}
        if not rows:
            typer.echo("no stored agent sessions")
        for r in rows:
            note = "resumable" if r["can_resume"] else (r["error"] or "").replace("_", " ")
            typer.echo(f"{nicks.get(r['session_id']) or r['session_id']}  {r['agent']}  last {r['last_state']}  {note}")
        return
    r = _api(f"/api/sessions/{_session(target)['id']}/resume", "POST")
    typer.echo(f"resumed: {r['command'].split()[0]} ...")


@agent_app.command("report")
def agent_report(
    state: str = typer.Argument(..., help="idle, working, blocked, done or unknown."),
    source: str = typer.Option(..., "--source", help="Reporter id, e.g. integration:opencode or custom:mytool."),
    agent: str = typer.Option(..., "--agent", help="The agent kind running in this terminal (e.g. opencode)."),
    reason: str = typer.Option("", "--reason", help="With blocked: approval, question, authentication, tool_error, external_wait."),
    title: str = typer.Option("", "--title", help="Display-only work title."),
    label: str = typer.Option("", "--label", help="Display-only state label."),
    ttl: int = typer.Option(30000, "--ttl-ms", help="How long the report holds before falling back (max 120000)."),
):
    """Report lifecycle state from an integration inside a shelldeck terminal (uses its SHELLDECK_AGENT_REPORT_TOKEN)."""
    token = os.environ.get("SHELLDECK_AGENT_REPORT_TOKEN")
    if not token:
        typer.echo("error: run this inside a shelldeck terminal", err=True)
        raise typer.Exit(1)
    payload = {"source": source, "agent": agent, "state": state, "blocked_reason": reason or None, "ttl_ms": ttl,
               "metadata": {k: v for k, v in (("title", title), ("state_label", label)) if v} or None}
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Shelldeck-Token": auth.read_cli_token(), "X-Shelldeck-Report-Token": token}
    req = urllib.request.Request(_url("/api/agent-reports"), data=data, method="POST", headers=headers)
    try:
        with _open_url(req, 10) as r:
            typer.echo(json.loads(r.read().decode())["status"]["state"])
    except urllib.error.HTTPError as e:
        try:
            code = json.loads(e.read().decode()).get("error", e.reason)
        except ValueError:
            code = e.reason
        typer.echo(f"error: {code}", err=True)
        raise typer.Exit(1) from None


# ------------------------------------------------------------------ agent context (memory, decisions, recall)

knowledge_app = typer.Typer(help="Shared knowledge lifecycle (NEW, VERIFIED, STALE, REVIEWED, INVALIDATED).")
app.add_typer(knowledge_app, name="knowledge")


def _where(project: str = "") -> dict:
    """Which project a context command means: --project, else this terminal's session/cwd."""
    if project:
        return {"project": project}
    return {"session_id": os.environ.get("SHELLDECK_SESSION_ID", ""), "cwd": os.getcwd()}


def _qs(params: dict) -> str:
    return urllib.parse.urlencode({k: v for k, v in params.items() if v})


@app.command("context")
def context_(
    project: str = typer.Argument("", help="Another project's name (default: this folder's project)."),
    query: str = typer.Option("", "--query", "-q", help="Also pull related knowledge from other projects."),
    json_: bool = typer.Option(False, "--json", help="Print the full context package as JSON."),
):
    """The compact context an agent needs to continue: task, state, memory, decisions, git, hand-off, events."""
    out = _api("/api/context?" + _qs({**_where(project), "q": query}))
    typer.echo(json.dumps(out, indent=2) if json_ else out["text"])


@app.command()
def resume(json_: bool = typer.Option(False, "--json", help="Print JSON.")):
    """Where to pick up: this project's most recent incomplete task, its next action and recent context."""
    out = _api("/api/context?" + _qs(_where()))
    task = out.get("task")
    if json_:
        typer.echo(json.dumps(out, indent=2))
        return
    if not task or task["status"] in ("DONE", "CANCELLED"):
        typer.echo("no incomplete task in this project (start one: sd task update --task \"...\")")
        return
    typer.echo(out["text"])


@app.command()
def recall(
    query: str = typer.Argument(..., help="What you're looking for, e.g. \"authentication architecture\"."),
    project: str = typer.Option("", "--project", "-p", help="Rank this project first (default: this folder's)."),
    limit: int = typer.Option(10, "--limit", "-n"),
    json_: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """Search knowledge and decisions across every project. Results are reference material: adapt, don't copy."""
    rows = _api("/api/context/recall?" + _qs({**_where(project), "q": query, "limit": limit}))["results"]
    if json_:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo("nothing found")
    for i, r in enumerate(rows, 1):
        flag = f" [{r['status']}]" if r["status"] in ("STALE", "NEW") else ""
        typer.echo(f"{i}. {r['project']}  {r['title']}  ({r['type']}, relevance {r['relevance']}){flag}  {r['id']}")
        typer.echo(f"   {' '.join(r['snippet'].split())[:300]}")
        if r["sources"]:
            typer.echo(f"   sources: {', '.join(r['sources'])}")


@app.command()
def memory(
    action: str = typer.Argument("", help="Leave empty to list, or `search`."),
    query: str = typer.Argument("", help="With search: the query."),
    project: str = typer.Option("", "--project", "-p"),
    json_: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """This project's memory and decisions; `sd memory search <query>` searches every project."""
    if action == "search":
        recall(query or typer.prompt("query"), project, 10, json_)
        return
    if action:
        typer.echo("usage: sd memory [search <query>]", err=True)
        raise typer.Exit(1)
    out = _api("/api/context/memory?" + _qs(_where(project)))
    if json_:
        typer.echo(json.dumps(out, indent=2))
        return
    typer.echo(f"# {out['project']}")
    for k in out["memory"]:
        typer.echo(f"- [{k['id']} {k['status']} {k['type']}] {' '.join(k['content'].split())}")
    for d in out["decisions"]:
        typer.echo(f"- [{d['id']} decision] {d['title']}" + (f" (because {d['reason']})" if d.get("reason") else ""))
    if not out["memory"] and not out["decisions"]:
        typer.echo("(empty: add with sd remember / sd decide)")


def _record(kind: str, text: str, type_: str, topic: str, files: list[str], project: str, scope: str) -> None:
    k = _api("/api/context/memory", "POST", {**_where(project), "kind": kind, "text": text, "type": type_, "topic": topic,
                                             "files": files or [], "scope": scope})
    typer.echo(f"{k['id']} saved to {k['project']} memory ({k['status']})")


@app.command()
def remember(
    fact: str = typer.Argument(..., help="A durable fact about this project (not a transient note)."),
    type_: str = typer.Option("note", "--type", help="architecture, api, database, convention, gotcha, pattern, ..."),
    topic: str = typer.Option("", "--topic"),
    file: list[str] = typer.Option(None, "--file", "-f", help="Source file it came from (relative; tracked for staleness)."),
    project: str = typer.Option("", "--project", "-p"),
    scope: str = typer.Option("PROJECT", "--scope", help="PROJECT or GLOBAL."),
):
    """Add a fact to project memory (secrets are redacted; the same fact is merged, not duplicated)."""
    _record("remember", fact, type_, topic, file, project, scope)


@app.command()
def discover(
    finding: str = typer.Argument(..., help="Something you found out while working."),
    type_: str = typer.Option("discovery", "--type"),
    topic: str = typer.Option("", "--topic"),
    file: list[str] = typer.Option(None, "--file", "-f", help="Source file supporting it (relative)."),
    project: str = typer.Option("", "--project", "-p"),
):
    """Record a discovery (confidence 0.8 until verified)."""
    _record("discover", finding, type_, topic, file, project, "PROJECT")


@app.command()
def decide(
    title: str = typer.Argument(..., help="The decision, short."),
    reason: str = typer.Option("", "--reason", "-r"),
    alternatives: str = typer.Option("", "--alternatives", "-a"),
    consequence: str = typer.Option("", "--consequence", "-c"),
    project: str = typer.Option("", "--project", "-p"),
):
    """Record a settled decision so later agents don't reopen it."""
    d = _api("/api/context/decisions", "POST", {**_where(project), "title": title, "reason": reason, "alternatives": alternatives,
                                                "consequence": consequence})
    typer.echo(f"decision {d['id']} recorded")


@app.command("decisions")
def decisions_(project: str = typer.Option("", "--project", "-p")):
    """This project's decisions."""
    for d in _api("/api/context/memory?" + _qs(_where(project)))["decisions"]:
        typer.echo(f"{d['created_at'][:10]}  {d['id']}  {d['title']}" + (f"  (because {d['reason']})" if d.get("reason") else ""))


@task_app.command("update")
def task_update(
    status: str = typer.Argument("", help="TODO, IN_PROGRESS, BLOCKED, DONE or CANCELLED (optional)."),
    task: str = typer.Option("", "--task", help="The task title (starts a task if none is open)."),
    objective: str = typer.Option("", "--objective"),
    plan: str = typer.Option("", "--plan"),
    step: str = typer.Option("", "--step", help="What you're doing now."),
    next_action: str = typer.Option("", "--next", help="What the next agent should do first."),
    error: str = typer.Option("", "--error", help="The last error, if any."),
    tests: str = typer.Option("", "--tests", help="Test state, e.g. \"42 passed, 1 failing: test_x\"."),
    project: str = typer.Option("", "--project", "-p"),
):
    """Update the active agent task and current state (a checkpoint; git branch/commit/changes are captured)."""
    fields = {"task": task, "objective": objective, "plan": plan, "status": status, "step": step, "next_action": next_action,
              "last_error": error, "tests": tests}
    out = _api("/api/context/state", "POST", {**_where(project), **{k: v for k, v in fields.items() if v}})
    t = out.get("task") or {}
    typer.echo(f"{out['project']['name']}: {t.get('title', '(no task)')} [{t.get('status', '-')}]")


@knowledge_app.command("verify")
def knowledge_verify(
    knowledge_id: str = typer.Argument(..., help="Knowledge id (from sd memory / sd recall)."),
    status: str = typer.Option("VERIFIED", "--status", "-s", help="VERIFIED, REVIEWED, STALE or INVALIDATED."),
):
    """Mark knowledge verified against its sources (or reviewed/stale/invalidated)."""
    k = _api(f"/api/context/knowledge/{knowledge_id}/status", "POST", {"status": status})
    typer.echo(f"{k['id']} is now {k['status']}")


@app.command("projects")
def projects_(json_: bool = typer.Option(False, "--json")):
    """Projects known to the agent context store."""
    rows = _api("/api/context/projects")["projects"]
    if json_:
        typer.echo(json.dumps(rows, indent=2))
        return
    for r in rows:
        t = r.get("task") or {}
        typer.echo(f"{r['name']}  {r['path']}  knowledge={r['knowledge']}" + (f"  task: {t['title']} [{t['status']}]" if t else ""))


@app.command("project")
def project_(name: str = typer.Argument(...)):
    """One project's context (same as `sd context NAME`)."""
    typer.echo(_api("/api/context?" + _qs({"project": name}))["text"])


@app.command()
def relate(
    target: str = typer.Argument(..., help="The other project's name."),
    relation: str = typer.Option("related-to", "--relation", "-r", help="related-to, depends-on, uses-pattern, shares-database-with."),
    project: str = typer.Option("", "--project", "-p"),
):
    """Link this project to another, so recall ranks its knowledge higher."""
    r = _api("/api/context/relationships", "POST", {**_where(project), "target": target, "relation": relation})
    typer.echo(f"{r['source']} {r['relation']} {r['target']}")



def _commands() -> set[str]:
    """Registered command names (so the folder shorthand never shadows a new command)."""
    names = {c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands}
    return names | {g.name for g in app.registered_groups if g.name}


COMMANDS = _commands()  # every command is registered by now


def _main() -> None:
    # `shelldeck <folder>` is shorthand for `shelldeck open <folder>`
    args = sys.argv[1:]
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("--port", "-p") else 1
    if i < len(args) and args[i] not in COMMANDS:
        sys.argv.insert(i + 1, "open")
    app()


if __name__ == "__main__":
    _main()
