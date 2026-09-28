import subprocess
import time
from pathlib import Path

from . import db, shells


def run_command(
    project_id: str,
    command: str,
    shell: str | None = None,
    timeout: int = 60,
) -> dict:
    started = time.perf_counter()
    project = db.get_project(project_id)
    if not project:
        return {
            "status": "failed",
            "exit_code": None,
            "output": "",
            "error": "project not found",
            "duration_ms": 0,
        }

    cwd = project.get("path")
    if not cwd or not Path(cwd).exists():
        return {
            "status": "failed",
            "exit_code": None,
            "output": "",
            "error": "project directory does not exist",
            "duration_ms": 0,
        }

    shell = shell or db.get_setting("default_shell", shells.default_kind())
    args = shells.command_argv(shell, command, db.get_setting("wsl_distro"), cwd)

    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        exit_code = proc.returncode
        status = "success" if exit_code == 0 else "failed"
        error = None if status == "success" else f"exit code {exit_code}"
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired carries bytes even with text=True
        output = "".join(
            x.decode(errors="replace") if isinstance(x, bytes) else x
            for x in (exc.stdout or "", exc.stderr or "")
        )
        exit_code = None
        status = "failed"
        error = f"timeout after {timeout}s"
    except OSError as exc:
        output = ""
        exit_code = None
        status = "failed"
        error = str(exc)

    duration_ms = int((time.perf_counter() - started) * 1000)

    return {
        "status": status,
        "exit_code": exit_code,
        "output": output,
        "error": error,
        "duration_ms": duration_ms,
    }
