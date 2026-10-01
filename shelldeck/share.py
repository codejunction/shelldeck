"""The share tunnel: a cloudflared quick tunnel the server starts, watches and stops.

server.py owns the share state (links, grants, logins); this module only runs the process.
"""

import logging
import re
import shutil
import subprocess
import sys
import threading

log = logging.getLogger("shelldeck")

HINT = (
    "winget install Cloudflare.cloudflared" if sys.platform == "win32"
    else "see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
)
TUNNEL_URL = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")
READY = "Registered tunnel connection"
_proc: subprocess.Popen | None = None

# Shown before the first share (Share dialog, `sd share`); accepting is stored by auth.accept_terms().
# Bump TERMS_VERSION when the text changes in substance, so everyone is asked again.
TERMS_VERSION = 1
TERMS = [
    "Sharing puts this machine's shells on the internet through a Cloudflare tunnel. You share at your own risk.",
    "Anyone who gets a share link and your password controls your terminals. Keep links and the QR code private;"
    " never post them in chats, tickets or screenshots.",
    "Cloudflare ends the encryption and can see the traffic, including what you type and your password.",
    "Don't share from restricted environments such as a corporate, school or other private network, or a machine"
    " you don't own, unless its owner allows it. It can break their security policy.",
    "shelldeck comes with no warranty (MIT license). You are responsible for what happens through a share.",
]


def command() -> list[str] | None:
    """cloudflared's argv prefix, or None when it isn't installed (tests swap in a stub)."""
    exe = shutil.which("cloudflared")
    return [exe] if exe else None


def start(url: str, on_exit, timeout: float = 30) -> str:
    """Run a quick tunnel to `url` (this server) and return its public host once an edge connection is up.
    Blocking. on_exit(host) runs on a thread when cloudflared exits by itself or is stopped.
    Raises FileNotFoundError (not installed), TimeoutError, or RuntimeError (exited without a tunnel)."""
    global _proc
    cmd = command()
    if not cmd:
        raise FileNotFoundError("cloudflared")
    # the tunnel ends at our own loopback server, whose certificate (if any) is self-signed
    argv = cmd + ["tunnel", "--no-autoupdate", "--url", url] + (["--no-tls-verify"] if url.startswith("https:") else [])
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, errors="replace", **flags)
    _proc = proc
    found: dict = {"host": None, "up": False}
    ready = threading.Event()

    def read() -> None:
        # cloudflared logs to stderr: the URL first, then each edge connection. Keep draining it, or the pipe fills.
        for line in proc.stderr:
            if not found["host"] and (m := TUNNEL_URL.search(line)):
                found["host"] = m.group(1)
            elif found["host"] and READY in line and not found["up"]:
                found["up"] = True
                ready.set()
        proc.wait()
        ready.set()
        log.info("cloudflared exited (%s)", proc.returncode)
        if found["up"]:
            on_exit(found["host"])

    threading.Thread(target=read, daemon=True, name="cloudflared").start()
    if not ready.wait(timeout):
        stop(proc)
        raise TimeoutError("tunnel_timeout")
    if proc.poll() is not None:
        raise RuntimeError("cloudflared exited without a tunnel")
    return found["host"]


def stop(proc: subprocess.Popen | None = None) -> None:
    """Stop the tunnel (by default the current one)."""
    global _proc
    proc = proc or _proc
    if proc is _proc:
        _proc = None
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
