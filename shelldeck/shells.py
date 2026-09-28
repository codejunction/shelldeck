"""Shell kinds per OS and how to launch them.

Windows: pwsh (default), powershell, cmd, gitbash, wsl.
Linux/macOS: login shells from /etc/shells (bash, zsh, fish, ...); default = $SHELL.
"""

import base64
import os
import shlex
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

WINDOWS = sys.platform == "win32"
WIN_KINDS = ("pwsh", "powershell", "cmd", "gitbash", "wsl")
# /etc/shells entries that are not interactive shells
_NOT_SHELLS = {"rbash", "tmux", "screen", "nologin", "false", "git-shell"}


def _git_bash() -> str | None:
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        if base:
            for p in (Path(base) / "Git/bin/bash.exe", Path(base) / "Programs/Git/bin/bash.exe"):
                if p.exists():
                    return str(p)
    return None


@cache
def _posix_shells() -> dict[str, str]:
    """name -> path, from /etc/shells plus $SHELL, preferring common shells first."""
    found: dict[str, str] = {}
    candidates = [os.environ.get("SHELL", "")]
    try:
        candidates += Path("/etc/shells").read_text().splitlines()
    except OSError:
        pass
    candidates += ["/bin/bash", "/bin/sh"]
    for line in candidates:
        line = line.strip()
        name = os.path.basename(line)
        if not line or line.startswith("#") or name in _NOT_SHELLS or name in found:
            continue
        if os.access(line, os.X_OK):
            found[name] = line
    order = ["bash", "zsh", "fish"]
    return dict(sorted(found.items(), key=lambda kv: (kv[0] not in order, order.index(kv[0]) if kv[0] in order else 0, kv[0])))


def kinds() -> tuple[str, ...]:
    return WIN_KINDS if WINDOWS else tuple(_posix_shells())


def default_kind() -> str:
    if WINDOWS:
        return "pwsh"
    user = os.path.basename(os.environ.get("SHELL", ""))
    names = _posix_shells()
    return user if user in names else next(iter(names), "sh")


def exe(kind: str) -> str | None:
    if not WINDOWS:
        return _posix_shells().get(kind)
    if kind == "pwsh":
        return shutil.which("pwsh") or shutil.which("powershell")
    if kind == "powershell":
        return shutil.which("powershell")
    if kind == "cmd":
        return os.environ.get("COMSPEC") or shutil.which("cmd")
    if kind == "gitbash":
        return _git_bash()
    if kind == "wsl":
        return shutil.which("wsl")
    return None


def available() -> list[str]:
    return [k for k in kinds() if exe(k)]


def wsl_distros() -> list[str]:
    if not WINDOWS or not exe("wsl"):
        return []
    try:
        out = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=5, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    # wsl.exe prints UTF-16LE
    text = out.decode("utf-16-le", errors="ignore").replace("\x00", "")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _wsl_prefix(distro: str | None, cwd: str | None) -> list[str]:
    argv = [exe("wsl") or "wsl.exe"]
    if distro:
        argv += ["-d", distro]
    if cwd:
        argv += ["--cd", cwd]
    return argv


def _fallback() -> str:
    return exe(default_kind()) or ("cmd.exe" if WINDOWS else "/bin/sh")


def interactive_argv(kind: str, distro: str | None = None, cwd: str | None = None) -> list[str]:
    """argv for an interactive shell. Unknown non-empty kind = legacy literal command line."""
    kind = kind or default_kind()
    if kind == "wsl" and WINDOWS:
        return _wsl_prefix(distro, cwd)
    if kind in kinds():
        path = exe(kind) or _fallback()
        if kind in ("pwsh", "powershell"):
            return [path, "-NoLogo"]
        if kind == "gitbash":
            return [path, "--login", "-i"]
        return [path]
    return shlex.split(kind, posix=not WINDOWS) or [_fallback()]


INTEGRATION = Path(__file__).parent / "integration"


def with_integration(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    """Add shell integration (prompt/exit code/cwd OSC reports) to an interactive argv.

    Returns the new argv and extra environment. Unknown shells (and WSL) pass through."""
    name = os.path.basename(argv[0]).lower().removesuffix(".exe")
    rest = argv[1:]
    if name in ("pwsh", "powershell"):
        # inline, not a script path: execution policy can block .ps1 files but not -EncodedCommand
        code = base64.b64encode((INTEGRATION / "shelldeck.ps1").read_text(encoding="utf-8").encode("utf-16-le")).decode()
        return [argv[0], *rest, "-NoExit", "-EncodedCommand", code], {}
    if name == "cmd":
        prompt = os.environ.get("PROMPT") or "$P$G"
        mark = lambda s: f"$E]{s}$E\\"  # noqa: E731
        return argv, {"PROMPT": mark("633;P;Cwd=$P") + mark("133;D") + mark("133;A") + prompt + mark("133;B")}
    if name == "bash":
        env = {"SHELLDECK_LOGIN": "1"} if {"--login", "-l"} & set(rest) else {}
        rest = [a for a in rest if a not in ("--login", "-l", "-i")]
        rc = str(INTEGRATION / "bash.sh").replace("\\", "/")
        return [argv[0], "--rcfile", rc, "-i", *rest], env
    if name == "zsh":
        env = {"ZDOTDIR": str(INTEGRATION / "zsh")}
        if os.environ.get("ZDOTDIR"):
            env["SHELLDECK_USER_ZDOTDIR"] = os.environ["ZDOTDIR"]
        return argv, env
    if name == "fish":
        return [*argv, "-C", f"source '{INTEGRATION / 'shelldeck.fish'}'"], {}
    return argv, {}


def command_argv(kind: str, command: str, distro: str | None = None, cwd: str | None = None) -> list[str]:
    """argv that runs one command non-interactively and exits."""
    kind = kind if kind in kinds() else default_kind()
    if kind == "wsl" and WINDOWS:
        return [*_wsl_prefix(distro, cwd), "--", "sh", "-lc", command]
    path = exe(kind) or _fallback()
    base = os.path.basename(path).lower()
    if base.startswith("cmd"):
        return [path, "/c", command]
    if kind in ("pwsh", "powershell"):
        return [path, "-NoProfile", "-NonInteractive", "-Command", command]
    return [path, "-lc", command]


LABELS = {"pwsh": "pwsh", "powershell": "PowerShell", "cmd": "cmd", "gitbash": "Git Bash", "wsl": "WSL"}


def label(kind: str) -> str:
    return LABELS.get(kind, kind)
