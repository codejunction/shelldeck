"""Pseudo-terminals: ConPTY (pywinpty) on Windows, stdlib pty on Linux/macOS."""

import codecs
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from . import shells

log = logging.getLogger(__name__)

SCROLLBACK_CHARS = 256 * 1024


class Scrollback:
    """Last `limit` chars of output, so a reconnecting client can redraw the screen."""

    def __init__(self, limit: int = SCROLLBACK_CHARS):
        self.limit = limit
        self.chunks: deque[str] = deque()
        self.size = 0
        self.dirty = False
        self.total = 0  # chars ever added, to tell how stale a snapshot is
        self.snapshot: tuple[int, str] | None = None  # (total at snapshot, client-rendered screen)

    def add(self, data: str) -> None:
        self.dirty = True
        self.total += len(data)
        self.chunks.append(data)
        self.size += len(data)
        while self.size - len(self.chunks[0]) >= self.limit:
            self.size -= len(self.chunks.popleft())
        if (extra := self.size - self.limit) > 0:
            self.chunks[0] = self.chunks[0][extra:]
            self.size = self.limit

    def text(self) -> str:
        return "".join(self.chunks)

    def persisted(self) -> str:
        """What to save for a restart: the client's rendered snapshot while it is fresh.

        Raw ConPTY output positions the cursor absolutely, so it replays garbled on a new screen.
        ponytail: staleness = output since the snapshot; a server-side emulator would drop this."""
        if self.snapshot and self.total - self.snapshot[0] < 4096:
            return self.snapshot[1]
        return self.text()


# --------------------------------------------------------------- backends
# Both expose: read() -> str (non-blocking), write(str), resize(rows, cols),
# isalive() -> bool, kill().


class WinProc:
    def __init__(self, argv: list[str], cwd: str | None, env: dict[str, str], rows: int, cols: int):
        import ctypes

        from winpty import PTY, Backend

        def short(path: str) -> str:
            if " " not in path:
                return path
            buf = ctypes.create_unicode_buffer(512)
            n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 512)
            return buf.value if n and buf.value else path

        # A detached server (CREATE_NEW_PROCESS_GROUP) ignores Ctrl+C and every shell inherits
        # that, so ^C would never interrupt a running command. Re-enable it before spawning.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
        self.pty = PTY(cols, rows, backend=Backend.ConPTY)
        cmdline = " " + subprocess.list2cmdline(argv[1:]) if len(argv) > 1 else None
        env_str = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"
        self.pty.spawn(short(argv[0]), cwd=cwd, env=env_str, cmdline=cmdline)
        self.pid = self.pty.pid

    def read(self) -> str:
        try:
            return self.pty.read(blocking=False)
        except Exception:  # noqa: BLE001 - winpty raises bare errors at EOF
            return ""

    def write(self, data: str) -> None:
        self.pty.write(data)

    def resize(self, rows: int, cols: int) -> None:
        self.pty.set_size(cols, rows)

    def isalive(self) -> bool:
        return self.pty.isalive()

    def kill(self) -> None:
        pid = self.pty.pid
        os.kill(pid, signal.SIGINT)
        time.sleep(0.1)
        if self.pty.isalive():
            os.kill(pid, signal.SIGTERM)


class PosixProc:
    def __init__(self, argv: list[str], cwd: str | None, env: dict[str, str], rows: int, cols: int):
        import pty

        self.master, slave = pty.openpty()
        self._winsize(slave, rows, cols)
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")
        self.proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=env,
            start_new_session=True,  # own session + controlling tty
            close_fds=True,
        )
        os.close(slave)
        self.pid = self.proc.pid
        os.set_blocking(self.master, False)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.eof = False

    @staticmethod
    def _winsize(fd: int, rows: int, cols: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def read(self) -> str:
        if self.eof:
            return ""
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return ""
        except OSError:  # EIO: slave side closed
            data = b""
        if not data:
            self.eof = True
        return self.decoder.decode(data, final=not data)

    def write(self, data: str) -> None:
        os.write(self.master, data.encode())

    def resize(self, rows: int, cols: int) -> None:
        self._winsize(self.master, rows, cols)

    def isalive(self) -> bool:
        return not self.eof and self.proc.poll() is None

    def kill(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            self.proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
        os.close(self.master)


Proc = WinProc if sys.platform == "win32" else PosixProc


# ---------------------------------------------------------------- manager


# after replayed scrollback from a previous run: leave alt screen (1047: no cursor restore), reset style, show cursor
RESTORED = "\x1b[0m\x1b[?1047l\x1b[?25h\r\n\x1b[J\x1b[2m--- restored {when} ---\x1b[0m"


def _push_to_scrollback(rows: int) -> str:
    """Scroll everything on screen into scrollback and home the cursor.

    A new ConPTY paints at absolute rows of a screen it thinks is blank, which would
    overwrite restored lines still in view."""
    return "\x1b[999B" + "\r\n" * rows + "\x1b[H"


class PtyManager:
    def __init__(self, store: Path | None = None):
        self.procs: dict[str, Proc] = {}
        self.scrollback: dict[str, Scrollback] = {}
        self.wakes: dict[str, threading.Event] = {}
        self.store = store  # scrollback files, so a restart/reboot keeps terminal history

    def _file(self, session_id: str) -> Path | None:
        if not self.store or not session_id.replace("-", "").isalnum():
            return None
        return self.store / f"{session_id}.log"

    def _restore(self, session_id: str, rows: int = 24) -> Scrollback:
        sb = Scrollback()
        f = self._file(session_id)
        if f and f.exists():
            old = f.read_bytes().decode("utf-8", errors="replace")  # bytes: text mode drops the CR in CRLF
            if old:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
                sb.add(old + RESTORED.format(when=when) + _push_to_scrollback(rows))
        return sb

    def save(self) -> None:
        """Write changed scrollbacks to disk."""
        if not self.store:
            return
        self.store.mkdir(parents=True, exist_ok=True)
        for sid, sb in list(self.scrollback.items()):
            if sb.dirty and (f := self._file(sid)):
                sb.dirty = False
                f.write_bytes(sb.persisted().encode("utf-8"))

    def snapshot(self, session_id: str, data: str) -> None:
        """Store the client's serialized screen (xterm serialize addon) for the next restart."""
        if sb := self.scrollback.get(session_id):
            sb.snapshot = (sb.total, data[-SCROLLBACK_CHARS:])
            sb.dirty = True

    def searchable(self, session_id: str) -> str:
        """Terminal output as saved for restore: from memory while running, else from disk."""
        if sb := self.scrollback.get(session_id):
            return sb.persisted()
        f = self._file(session_id)
        return f.read_bytes().decode("utf-8", errors="replace") if f and f.exists() else ""

    def forget(self, session_id: str) -> None:
        if f := self._file(session_id):
            f.unlink(missing_ok=True)

    def prune(self, keep: set[str]) -> None:
        """Delete saved scrollback for sessions that no longer exist."""
        if self.store and self.store.exists():
            for f in self.store.glob("*.log"):
                if f.stem not in keep:
                    f.unlink(missing_ok=True)

    def create(
        self,
        session_id: str,
        cwd: str | None = None,
        shell: str | None = None,
        rows: int = 24,
        cols: int = 120,
        distro: str | None = None,
    ) -> Proc:
        proc = self.get(session_id)
        if proc:
            return proc
        start_dir = cwd if (cwd and Path(cwd).is_dir()) else None
        argv, extra = shells.with_integration(shells.interactive_argv(shell or "", distro, start_dir))
        env = os.environ.copy() | extra
        env["SHELLDECK_SESSION_ID"] = session_id
        proc = Proc(argv, start_dir, env, rows, cols)
        self.procs[session_id] = proc
        self.scrollback[session_id] = self._restore(session_id, rows)
        log.info("spawned %s for session %s in %s", argv, session_id, start_dir)
        return proc

    def get(self, session_id: str) -> Proc | None:
        proc = self.procs.get(session_id)
        if proc and not proc.isalive():
            return None
        return proc

    def history(self, session_id: str) -> str:
        sb = self.scrollback.get(session_id)
        return sb.text() if sb else ""

    def stream(self, session_id: str, emit) -> None:
        """Read the session's output on its own thread, calling emit(str) per chunk and emit(None) at exit.

        The thread polls non-blocking reads: pywinpty's blocking read holds data back and never
        returns at EOF. After output or a keystroke (write() wakes it) it polls with time.sleep,
        which is ~1ms on Windows; asyncio sleeps and Event.wait round up to ~16ms there. Once idle
        it waits on the event instead, so a quiet shell costs almost no CPU."""
        proc = self.procs.get(session_id)
        wake = self.wakes.setdefault(session_id, threading.Event())

        def run() -> None:
            idle = 0
            while proc is not None:
                try:
                    data = proc.read()
                except OSError:  # POSIX fd closed by kill()
                    break
                if data:
                    idle = 0
                    emit(data)
                    continue
                if not proc.isalive():
                    break
                idle += 1
                if idle <= 50:  # just active or typed into: ~1ms polls for ~75ms
                    time.sleep(0.001)
                elif wake.wait(0.02):  # idle: Event.wait is ~16ms-grained on Windows, fine here
                    wake.clear()
                    idle = 0
            emit(None)

        threading.Thread(target=run, name=f"pty-{session_id}", daemon=True).start()

    def record(self, session_id: str, data: str) -> None:
        if sb := self.scrollback.get(session_id):
            sb.add(data)

    def write(self, session_id: str, data: str) -> bool:
        proc = self.get(session_id)
        if not proc:
            return False
        try:
            proc.write(data)
            if wake := self.wakes.get(session_id):
                wake.set()
            return True
        except Exception:  # noqa: BLE001
            log.warning("write failed for session %s", session_id, exc_info=True)
            return False

    def resize(self, session_id: str, rows: int, cols: int) -> bool:
        proc = self.get(session_id)
        if not proc:
            return False
        try:
            proc.resize(rows, cols)
            return True
        except Exception:  # noqa: BLE001
            log.warning("resize failed for session %s", session_id, exc_info=True)
            return False

    def list_alive(self) -> list[str]:
        return [sid for sid in list(self.procs) if self.get(sid)]

    def terminate(self, session_id: str) -> None:
        self.scrollback.pop(session_id, None)
        if wake := self.wakes.pop(session_id, None):
            wake.set()
        proc = self.procs.pop(session_id, None)
        if not proc:
            return
        try:
            proc.kill()  # also releases the pty fd on POSIX, even if the shell already exited
        except OSError:
            log.warning("terminate failed for session %s", session_id, exc_info=True)
