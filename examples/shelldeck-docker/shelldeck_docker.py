"""Reference shelldeck plugin: `?docker ps` / `sd docker ps` and friends.

Install next to shelldeck: `uv tool install shelldeck --with ./examples/shelldeck-docker`, then restart shelldeck.
"""

import shutil
import subprocess
import sys

from shelldeck.plugins import Plugin

plugin = Plugin("docker", "0.1.0")


def _docker(*args: str) -> str:
    exe = shutil.which("docker")
    if not exe:
        return "docker is not on PATH"
    try:
        r = subprocess.run([exe, *args], capture_output=True, encoding="utf-8", errors="replace", timeout=20,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"docker failed: {e}"
    return (r.stdout + r.stderr).strip() or "(no output)"


@plugin.command("ps", "Running containers")
def ps(args, ctx):
    return _docker("ps", *args)


@plugin.command("images", "Local images")
def images(args, ctx):
    return _docker("images", *args)


@plugin.command("logs", "Follow a container's logs in this terminal")
def logs(args, ctx):
    return {"input": "docker logs -f " + " ".join(args or ["<container>"])}


@plugin.command("restart", "Restart a container (typed, you press Enter)")
def restart(args, ctx):
    return {"input": "docker restart " + " ".join(args or ["<container>"])}


@plugin.command("compose", "docker compose in this project")
def compose(args, ctx):
    return {"input": "docker compose " + " ".join(args or ["up -d"])}
