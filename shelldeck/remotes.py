"""Remote systems: SSH terminals and RDP desktops.

SSH runs in a normal shelldeck terminal: the server types `ssh [-p N] [-i key] user@host` after the shell's first
prompt, so leaving ssh drops back to a local shell. RDP opens the OS's own client (mstsc on Windows, xfreerdp or
Remmina elsewhere) on the host machine. Every value is validated so the typed line needs no quoting beyond the
key path, and nothing user-supplied is ever passed to a shell as options. Passwords are never stored: ssh asks in
the terminal (or uses keys / ssh-agent), and the RDP client asks in its own window.
"""

import re
import shutil
import subprocess
from pathlib import Path
import sys

KINDS = ("ssh", "rdp")
DEFAULT_PORT = {"ssh": 22, "rdp": 3389}
HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9-]{1,63})*|\d{1,3}(?:\.\d{1,3}){3}|\[?[0-9A-Fa-f:]{2,45}\]?)$")
# plain (jdoe), LDAP/NTID-style (CORP\jdoe, jdoe@corp.example.com): passed with -l, quoted where the shell needs it
USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(?:\\[A-Za-z0-9][A-Za-z0-9._-]{0,63}|@[A-Za-z0-9][A-Za-z0-9.-]{0,252})?$")
NAME_MAX = 60
RDP_CLIENTS = ("xfreerdp3", "xfreerdp", "remmina")  # non-Windows, first found wins


class RemoteError(ValueError):
    """`str(e)` is a stable error code."""


def validate(raw: dict, project_ok=lambda pid: True) -> dict:
    """The stored fields of a remote from user input, or RemoteError."""
    kind = str(raw.get("kind") or "ssh")
    if kind not in KINDS:
        raise RemoteError("invalid_kind")
    host = str(raw.get("host") or "").strip()
    if not host or host.startswith("-") or not HOST.match(host):
        raise RemoteError("invalid_host")
    user = str(raw.get("user") or "").strip()
    if user and not USER.match(user):
        raise RemoteError("invalid_user")
    port = raw.get("port") or DEFAULT_PORT[kind]
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise RemoteError("invalid_port") from None
    if not 1 <= port <= 65535:
        raise RemoteError("invalid_port")
    identity = str(raw.get("identity") or "").strip() if kind == "ssh" else ""
    if identity and (identity.startswith("-") or '"' in identity or any(c in identity for c in "\r\n\t$`%!^&|<>;")):
        raise RemoteError("invalid_identity")
    project_id = str(raw.get("project_id") or "") or None
    if project_id and not project_ok(project_id):
        raise RemoteError("project_not_found")
    name = " ".join(str(raw.get("name") or "").split())[:NAME_MAX] or (f"{user}@{host}" if user else host)
    return {"name": name, "kind": kind, "host": host, "user": user, "port": port, "identity": identity, "project_id": project_id}


REMOTE_PATH = re.compile(r"^(?:~|/)[A-Za-z0-9._/~+@:,=-]*$")  # no spaces or quotes: one word in every local shell


def check_path(path: str) -> str:
    path = (path or "").strip() or "~"
    if path != "~" and (not REMOTE_PATH.match(path) or ".." in path.split("/")):
        raise RemoteError("invalid_remote_path")
    return path.rstrip("/") or "/"


def ssh_line(r: dict, path: str = "~", shell: str = "") -> str:
    """The command typed into the terminal's shell. Values were validated; only the key path may need quotes.
    With a folder, ssh -t runs `cd <folder> && exec $SHELL -l` there: single quotes keep $SHELL for the remote in
    pwsh, bash, zsh and fish; cmd has no single quotes but doesn't expand $ either, so it gets double quotes."""
    path = check_path(path)
    parts = ["ssh"]
    if path != "~":
        parts.append("-t")
    if r.get("port") and int(r["port"]) != 22:
        parts += ["-p", str(int(r["port"]))]
    if r.get("identity"):
        parts += ["-i", f'"{r["identity"]}"' if " " in r["identity"] else r["identity"]]
    if r.get("user"):
        user = r["user"]
        # a backslash is an escape in bash/zsh/fish: single-quote it there (pwsh too); cmd passes it as is
        parts += ["-l", user if "\\" not in user or shell == "cmd" else f"'{user}'"]
    parts.append(r["host"])
    if path != "~":
        q = '"' if shell == "cmd" else "'"
        parts.append(f"{q}cd {path} && exec $SHELL -l{q}")
    return " ".join(parts)


def _hostport(r: dict) -> str:
    host = r["host"]
    if ":" in host.strip("[]"):  # IPv6 needs brackets with a port
        host = f"[{host.strip('[]')}]"
    return f"{host}:{int(r['port'])}" if int(r["port"]) != 3389 else host


def rdp_argv(r: dict) -> list[str]:
    """The RDP client and its arguments on this OS, or RemoteError('rdp_client_not_found')."""
    if sys.platform == "win32":
        return ["mstsc", f"/v:{_hostport(r)}"]  # mstsc has no user flag; it asks (or uses saved credentials)
    for exe in RDP_CLIENTS:
        if found := shutil.which(exe):
            if exe == "remmina":
                from urllib.parse import quote  # noqa: PLC0415

                user = f"{quote(r['user'], safe='')}@" if r.get("user") else ""
                return [found, "-c", f"rdp://{user}{_hostport(r)}"]
            return [found, f"/v:{_hostport(r)}", *([f"/u:{r['user']}"] if r.get("user") else []), "/dynamic-resolution"]
    raise RemoteError("rdp_client_not_found")


def open_rdp(r: dict) -> list[str]:
    """Start the desktop client detached; returns the argv it ran."""
    argv = rdp_argv(r)
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=sys.platform != "win32", creationflags=0)  # noqa: S603 - fixed client, validated args
    except OSError as e:
        raise RemoteError("rdp_launch_failed") from e
    return argv


def clients() -> dict:
    """What this machine can do: ssh on PATH, which RDP client."""
    try:
        rdp = rdp_argv({"host": "x", "port": 3389})[0]
    except RemoteError:
        rdp = None
    return {"ssh": bool(shutil.which("ssh")), "rdp": rdp and ("mstsc" if sys.platform == "win32" else rdp.replace("\\", "/").rsplit("/", 1)[-1])}


def test_ssh(r: dict, timeout: float = 8) -> dict:
    """Can this machine reach the remote, and log in without a prompt? Never stores or sends a password, never
    changes known_hosts: {ok, step: network|auth|done, message}."""
    import socket

    host = r["host"].strip("[]")
    try:
        with socket.create_connection((host, int(r["port"])), timeout=timeout):
            pass
    except OSError as e:
        return {"ok": False, "step": "network", "message": f"Can't reach {r['host']}:{r['port']} ({e.strerror or e})."}
    if not shutil.which("ssh"):
        return {"ok": False, "step": "network", "message": "The port answers, but ssh isn't installed on this machine."}
    target = host
    nul = "NUL" if sys.platform == "win32" else "/dev/null"
    argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={int(timeout)}", "-o", "StrictHostKeyChecking=no",
            "-o", f"UserKnownHostsFile={nul}", "-o", "LogLevel=ERROR", "-p", str(int(r["port"]))]
    if r.get("identity"):
        argv += ["-i", str(Path(r["identity"]).expanduser())]
    if r.get("user"):
        argv += ["-l", r["user"]]  # argv, no shell: CORP\jdoe needs no quoting here
    try:
        out = subprocess.run([*argv, "--", target, "exit"], capture_output=True, text=True, timeout=timeout + 4, stdin=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "step": "auth", "message": "The port answers, but ssh timed out logging in."}
    err = (out.stderr or "").strip().splitlines()[-1:] or [""]
    if out.returncode == 0:
        return {"ok": True, "step": "done", "message": "Connected and logged in with your key or ssh-agent."}
    if "Permission denied" in err[0] or "publickey" in err[0]:
        return {"ok": True, "step": "auth", "message": "Reachable, and it asks for a password (LDAP/NTID or local account): "
                "ssh will prompt for it in the terminal. Add a key file to log in without typing it."}
    return {"ok": False, "step": "auth", "message": err[0][:200] or f"ssh exited with {out.returncode}."}
