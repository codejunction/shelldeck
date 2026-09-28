import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import auth, db

app = typer.Typer(
    help="shelldeck: project-first multi-terminal. Run with no arguments to open the app.",
    no_args_is_help=False,
    add_completion=False,
)
schedule_app = typer.Typer(help="Scheduled jobs.")
task_app = typer.Typer(help="Personal task board.")
app.add_typer(schedule_app, name="schedule")
app.add_typer(task_app, name="task")

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


def _api(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    """Call the local server. Exits with a readable message on failure."""
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Shelldeck-Token": auth.read_cli_token()}
    req = urllib.request.Request(_url(path), data=data, method=method, headers=headers)
    try:
        with _open_url(req, 10) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            code = json.loads(e.read().decode()).get("error", e.reason)
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
        ("Project", p.get("name", "")),
        ("Project path", p.get("path", "")),
        ("Working dir", s.get("cwd", "")),
        ("Shell", s.get("shell") or "default"),
        ("Size", f"{s.get('cols', 0)}x{s.get('rows', 0)}"),
        ("Created", s.get("created_at", "")),
    ):
        table.add_row(k, str(v))
    console.print(table)


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
