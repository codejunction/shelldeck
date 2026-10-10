"""The PTY host: a small long-lived process (sd-pty) that owns the shells, so the UI server (sd-ui) can
restart, e.g. for an update, without closing a terminal.

The UI talks to it over loopback TCP, one JSON message per line, authenticated by a token the
supervisor (`shelldeck serve`) puts in both environments. The host quits when its stdin closes: the
supervisor holds the other end, so a stop, or the supervisor dying, ends the shells too.

ponytail: one UI connection at a time (the newest wins); a second UI would need per-client fan-out.
"""

import asyncio
import hmac
import json
import logging
import os
import sys
import threading

from .pty import PtyManager, Scrollback

log = logging.getLogger(__name__)

PROTOCOL = 1  # bump on an incompatible message change: a newer sd-ui then asks for a full restart
RESTART_UI, RESTART_ALL = 75, 76  # sd-ui exit codes the supervisor acts on (0 = stop everything)
LIMIT = 64 * 1024 * 1024  # one line can carry a 256 KB scrollback per terminal
ADDR_ENV, TOKEN_ENV = "SHELLDECK_PTY_ADDR", "SHELLDECK_PTY_TOKEN"  # only the host and the UI get these, never a shell


def _line(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":")).encode() + b"\n"


# ---------------------------------------------------------------- host (sd-pty)


class Host:
    def __init__(self, token: str, store):
        self.token = token
        self.manager = PtyManager(store)
        self.env: dict[str, dict] = {}  # sid -> extra_env, handed to a reconnecting UI (report tokens)
        self.writer: asyncio.StreamWriter | None = None
        self.out: asyncio.Queue = asyncio.Queue()

    def _send(self, msg: dict) -> None:
        if self.writer and not self.writer.is_closing():
            self.writer.write(_line(msg))

    def _state(self) -> dict:
        m = self.manager
        return {sid: {"pid": m.procs[sid].pid, "text": sb.text(), "restored": sb.restored, "restored_at": sb.restored_at,
                      "env": self.env.get(sid, {})}
                for sid in m.list_alive() if (sb := m.scrollback.get(sid))}

    async def pump(self) -> None:
        """Every terminal's output, in order: recorded here (it outlives the UI) and sent on, merged per burst."""
        while True:
            items = [await self.out.get()]
            while not self.out.empty():
                items.append(self.out.get_nowait())
            merged: list[list] = []
            for sid, data in items:
                if data is not None and merged and merged[-1][0] == sid and merged[-1][1] is not None:
                    merged[-1][1] += data
                else:
                    merged.append([sid, data])
            for sid, data in merged:
                if data is None:
                    if self.writer:
                        self._send({"t": "exit", "sid": sid})
                    else:  # nobody to tell: drop it, a later UI starts a new shell for that terminal
                        self._terminate(sid)
                    continue
                self.manager.record(sid, data)
                self._send({"t": "out", "sid": sid, "d": data})
            if self.writer:
                try:
                    await self.writer.drain()
                except (ConnectionError, RuntimeError):
                    pass

    def _terminate(self, sid: str) -> None:
        self.env.pop(sid, None)
        self.manager.terminate(sid)

    def handle(self, msg: dict, loop) -> None:
        t, sid = msg.get("t"), str(msg.get("sid", ""))
        m = self.manager
        if t == "write":
            m.write(sid, msg["d"])
        elif t == "resize":
            m.resize(sid, msg["rows"], msg["cols"])
        elif t == "create":
            if m.get(sid):
                return
            try:
                proc = m.create(sid, cwd=msg.get("cwd"), shell=msg.get("shell"), rows=msg["rows"], cols=msg["cols"],
                                distro=msg.get("distro"), extra_env=msg.get("env"))
            except Exception as e:  # noqa: BLE001 - spawn failures come from winpty/OS
                log.exception("spawn failed for session %s", sid)
                self._send({"t": "exit", "sid": sid, "error": str(e)})
                return
            self.env[sid] = msg.get("env") or {}
            m.stream(sid, lambda d, s=sid: loop.call_soon_threadsafe(self.out.put_nowait, (s, d)))
            self._send({"t": "spawned", "sid": sid, "pid": proc.pid})
        elif t == "snapshot":
            m.snapshot(sid, msg["d"])
        elif t == "terminate":
            self._terminate(sid)

    async def serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.loads(await asyncio.wait_for(reader.readline(), 5))
        except (asyncio.TimeoutError, ValueError, ConnectionError):
            writer.close()
            return
        if not hmac.compare_digest(str(hello.get("token", "")), self.token):
            writer.close()
            return
        if self.writer:  # a restarted UI replaces the old connection
            self.writer.close()
        self.writer = writer
        loop = asyncio.get_running_loop()
        self._send({"t": "state", "proto": PROTOCOL, "pid": os.getpid(), "sessions": self._state()})
        try:
            while line := await reader.readline():
                try:
                    self.handle(json.loads(line), loop)
                except (ValueError, KeyError, TypeError):
                    log.warning("bad message from the UI", exc_info=True)
        except ConnectionError:
            pass
        finally:
            if self.writer is writer:
                self.writer = None
            writer.close()

    async def saver(self) -> None:
        # ponytail: periodic flush; a hard power-off loses at most the last 15s of output
        while True:
            await asyncio.sleep(15)
            try:
                await asyncio.to_thread(self.manager.save)
            except OSError:
                log.warning("saving scrollback failed", exc_info=True)

    def shutdown(self) -> None:
        self.manager.save()
        for sid in list(self.manager.procs):
            self.manager.terminate(sid)


async def _main(store) -> None:
    host = Host(os.environ.pop(TOKEN_ENV, ""), store)
    server = await asyncio.start_server(host.serve_client, "127.0.0.1", 0, limit=LIMIT)
    print(server.sockets[0].getsockname()[1], flush=True)  # the supervisor reads the port from the first line
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def watch_stdin() -> None:  # EOF: the supervisor stopped or died
        sys.stdin.buffer.read()
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=watch_stdin, daemon=True).start()
    tasks = [asyncio.create_task(host.pump()), asyncio.create_task(host.saver())]
    await stop.wait()
    for task in tasks:
        task.cancel()
    server.close()
    host.shutdown()


def run(store) -> None:
    asyncio.run(_main(store))


# ---------------------------------------------------------------- client (in sd-ui)


class RemoteProc:
    """What the UI knows of a shell in the host: its pid (None until spawned)."""

    def __init__(self, pid: int | None = None):
        self.pid = pid
        self.alive = True

    def isalive(self) -> bool:
        return self.alive


class PtyClient(PtyManager):
    """PtyManager's interface for the UI server, backed by the host. Scrollback is mirrored here, so reads
    (history, search, screen) stay local; changes are one-way messages."""

    def __init__(self, addr: str, token: str):
        super().__init__()
        self.addr, self.token = addr, token
        self.writer: asyncio.StreamWriter | None = None
        self.emits: dict[str, object] = {}
        self.env: dict[str, dict] = {}
        self.host_pid: int | None = None

    async def connect(self, on_alive, on_lost) -> None:
        """Connect, mirror the host's terminals (on_alive(sid) for each), then keep reading; on_lost() if the host goes."""
        host, port = self.addr.rsplit(":", 1)
        reader, self.writer = await asyncio.open_connection(host, int(port), limit=LIMIT)
        self.writer.write(_line({"t": "hello", "token": self.token, "proto": PROTOCOL}))
        state = json.loads(await reader.readline())
        if state.get("proto") != PROTOCOL:
            raise ConnectionError(f"pty host speaks protocol {state.get('proto')}, this UI {PROTOCOL}")
        self.host_pid = state.get("pid")
        for sid, s in state["sessions"].items():
            sb = Scrollback()
            sb.restored, sb.restored_at = s["restored"], s["restored_at"]
            if s["text"]:
                sb.add(s["text"])
            self.scrollback[sid], self.procs[sid], self.env[sid] = sb, RemoteProc(s["pid"]), s["env"]
            on_alive(sid)
        asyncio.get_running_loop().create_task(self._read(reader, on_lost))

    async def _read(self, reader: asyncio.StreamReader, on_lost) -> None:
        try:
            while line := await reader.readline():
                msg = json.loads(line)
                t, sid = msg["t"], msg.get("sid")
                if t == "out":
                    if sb := self.scrollback.get(sid):
                        sb.add(msg["d"])
                    if emit := self.emits.get(sid):
                        emit(msg["d"])
                elif t == "spawned":
                    if proc := self.procs.get(sid):
                        proc.pid = msg["pid"]
                elif t == "exit":
                    if proc := self.procs.get(sid):
                        proc.alive = False
                    if emit := self.emits.pop(sid, None):
                        emit(None)
        except (ConnectionError, ValueError):
            pass
        on_lost()

    def _send(self, msg: dict) -> None:
        if self.writer and not self.writer.is_closing():
            self.writer.write(_line(msg))

    def create(self, session_id, cwd=None, shell=None, rows=24, cols=120, distro=None, extra_env=None):
        if proc := self.get(session_id):
            return proc
        proc = self.procs[session_id] = RemoteProc()
        self.scrollback[session_id] = self._restore(session_id)
        self.env[session_id] = extra_env or {}
        self._send({"t": "create", "sid": session_id, "cwd": cwd, "shell": shell, "rows": rows, "cols": cols,
                    "distro": distro, "env": extra_env or {}})
        return proc

    def stream(self, session_id, emit) -> None:
        proc = self.procs.get(session_id)
        if proc is None or not proc.alive:
            emit(None)
            return
        self.emits[session_id] = emit

    def record(self, session_id, data) -> None:
        pass  # mirrored on receipt, so output with no pump attached is kept too

    def write(self, session_id, data) -> bool:
        if not self.get(session_id):
            return False
        self._send({"t": "write", "sid": session_id, "d": data})
        return True

    def resize(self, session_id, rows, cols) -> bool:
        if not self.get(session_id):
            return False
        self._send({"t": "resize", "sid": session_id, "rows": rows, "cols": cols})
        return True

    def snapshot(self, session_id, data) -> None:
        super().snapshot(session_id, data)
        self._send({"t": "snapshot", "sid": session_id, "d": data})

    def save(self) -> None:
        pass  # the host saves

    def terminate(self, session_id) -> None:
        self.scrollback.pop(session_id, None)
        self.env.pop(session_id, None)
        self.emits.pop(session_id, None)
        if self.procs.pop(session_id, None):
            self._send({"t": "terminate", "sid": session_id})


def read_port(proc) -> int:
    """The port a starting host prints first (supervisor side)."""
    line = proc.stdout.readline()
    if not line.strip().isdigit():
        raise RuntimeError("pty host did not start")
    return int(line)
