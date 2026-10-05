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
import sys

KINDS = ("ssh", "rdp")
DEFAULT_PORT = {"ssh": 22, "rdp": 3389}
HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9-]{1,63})*|\d{1,3}(?:\.\d{1,3}){3}|\[?[0-9A-Fa-f:]{2,45}\]?)$")
USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
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


def ssh_line(r: dict) -> str:
    """The command typed into the terminal's shell. Values were validated; only the key path may need quotes."""
    parts = ["ssh"]
    if r.get("port") and int(r["port"]) != 22:
        parts += ["-p", str(int(r["port"]))]
    if r.get("identity"):
        parts += ["-i", f'"{r["identity"]}"' if " " in r["identity"] else r["identity"]]
    parts.append(f"{r['user']}@{r['host']}" if r.get("user") else r["host"])
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
                user = f"{r['user']}@" if r.get("user") else ""
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
