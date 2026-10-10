"""The standalone (PyInstaller) build: <root>/versions/<ver>/{shelldeck,sd-pty,sd-ui,sd}[.exe] + _internal,
<root>/current.txt naming the version in use, and <root>/sd.cmd + <root>/sd shims that survive updates."""

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
EXE = ".exe" if sys.platform == "win32" else ""

SD_CMD = '@echo off\r\nsetlocal\r\nset /p v=<"%~dp0current.txt"\r\n"%~dp0versions\\%v%\\sd.exe" %*\r\n'
SD_SH = '#!/bin/sh\nd=$(dirname "$0")\nexec "$d/versions/$(cat "$d/current.txt")/sd" "$@"\n'


def here() -> Path:
    return Path(sys.executable).parent


def root() -> Path | None:
    """<root> when running from versions/<ver>/, else None (a plain unpacked build)."""
    return here().parent.parent if here().parent.name == "versions" else None


def sd() -> Path:
    """An `sd` that outlives this version (agent hooks are written into agents' configs), else the one next to us."""
    if (r := root()) and (shim := r / ("sd.cmd" if sys.platform == "win32" else "sd")).exists():
        return shim
    return here() / f"sd{EXE}"


def ensure_shims() -> None:
    """Write the root `sd` shims (sd.cmd for cmd/pwsh, sh `sd` for bash/Git Bash); both run versions/<current.txt>/sd."""
    if not (r := root()):
        return
    for name, text in (("sd.cmd", SD_CMD), ("sd", SD_SH)):
        if sys.platform != "win32" and name == "sd.cmd":
            continue
        f = r / name
        if not f.exists() or f.read_text(encoding="utf-8") != text:
            f.write_bytes(text.encode())
            f.chmod(0o755)
