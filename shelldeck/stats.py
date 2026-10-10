"""System and per-terminal resource usage (psutil; GPU via nvidia-smi when present)."""

import shutil
import subprocess
import sys
import threading
import time

import psutil

from . import agents

NCPU = psutil.cpu_count() or 1
SMI = shutil.which("nvidia-smi")
# the detached server has no console; without this each nvidia-smi call flashes a window
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_procs: dict[int, psutil.Process] = {}  # kept between polls so cpu_percent has a baseline
_gpu: dict = {"at": 0.0, "data": None, "proc": None}


def _proc(pid: int) -> psutil.Process:
    p = _procs.get(pid)
    if p is None or not p.is_running():
        p = psutil.Process(pid)
        p.cpu_percent(None)
        _procs[pid] = p
    return p


def _watch_gpu(proc: subprocess.Popen) -> None:
    """Parse nvidia-smi's own 2s loop; stop it once nobody has asked for 30s."""
    for line in proc.stdout:
        if time.monotonic() - _gpu["at"] > 30:
            break
        try:
            name, util, used, total = [x.strip() for x in line.split(",")]
            _gpu["data"] = {"name": name, "util": float(util), "mem_used": int(used) << 20, "mem_total": int(total) << 20}
        except ValueError:
            continue
    proc.kill()
    _gpu["proc"] = None


def stop() -> None:
    if proc := _gpu.get("proc"):
        proc.kill()


def gpu() -> dict | None:
    """First NVIDIA GPU. ponytail: NVIDIA only; other GPUs show nothing.

    One long-lived `nvidia-smi -lms` instead of a call per poll: each launch costs ~400ms CPU."""
    if not SMI:
        return None
    _gpu["at"] = time.monotonic()
    if _gpu.get("proc") is None:
        try:
            proc = subprocess.Popen(
                [SMI, "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits", "-lms", "2000"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=NO_WINDOW,
            )
        except OSError:
            return None
        _gpu["proc"] = proc
        threading.Thread(target=_watch_gpu, args=(proc,), name="nvidia-smi", daemon=True).start()
    return _gpu["data"]


def tree(pid: int, seen: set[int]) -> dict | None:
    """CPU (% of the whole machine), memory and busiest child for a shell and everything it started."""
    try:
        _proc(pid)
    except psutil.Error:
        return None
    cpu = 0.0
    mem = count = 0
    top: tuple[str, float] | None = None
    agent: tuple[str, str | None] | None = None
    agent_pid = 0
    pids = []
    for p in [pid, *agents.descendants(pid)]:
        try:
            p = _proc(p)
            c = p.cpu_percent(None) / NCPU
            m = p.memory_info().rss
            name = p.name()
            if agent is None and p.pid != pid:  # outermost agent; its own children (MCP servers, tools) don't count
                agent, agent_pid = agents.identify(p), p.pid
        except psutil.Error:
            continue
        seen.add(p.pid)
        pids.append(p.pid)
        count += 1
        cpu += c
        mem += m
        if p.pid != pid and (top is None or c >= top[1]):
            top = (name, c)
    return {"cpu": round(cpu, 1), "mem": mem, "procs": count, "top": top[0] if top else None, "pids": pids,
            "agent": {"key": agent[0], "label": agents.AGENTS[agent[0]][0], "model": agent[1],
                      "context": agents.context(agent_pid, agent[0])} if agent else None}


def listening() -> dict[int, set[int]]:
    """pid -> TCP ports it listens on. One system-wide table per poll (not one per process)."""
    ports: dict[int, set[int]] = {}
    try:
        conns = psutil.net_connections("tcp")
    except (psutil.Error, OSError):  # macOS needs root for other users' sockets
        return ports
    for c in conns:
        if c.status == psutil.CONN_LISTEN and c.pid and c.laddr:
            ports.setdefault(c.pid, set()).add(c.laddr.port)
    return ports


def collect(sessions: dict[str, int]) -> dict:
    """sessions: session id -> shell pid."""
    seen: set[int] = set()
    per = {sid: t for sid, pid in sessions.items() if (t := tree(pid, seen))}
    listen = listening() if per else {}
    for t in per.values():
        t["ports"] = sorted({port for pid in t.pop("pids") for port in listen.get(pid, ())})
    for pid in [p for p in _procs if p not in seen]:
        del _procs[pid]
    agents.forget(seen)
    vm = psutil.virtual_memory()
    return {
        "system": {
            "cpu": psutil.cpu_percent(None),
            "mem_used": vm.total - vm.available,
            "mem_total": vm.total,
            "gpu": gpu(),
        },
        "sessions": per,
    }


def processes(shell_pid: int) -> list[dict]:
    """Every process a terminal's shell started (not the shell): pid, parent, name, CPU, memory, a short command line."""
    try:
        kids = _proc(shell_pid).children(recursive=True)
    except psutil.Error:
        return []
    out = []
    for p in kids:
        try:
            p = _proc(p.pid)
            with p.oneshot():
                cmd = " ".join(p.cmdline())
                out.append({"pid": p.pid, "ppid": p.ppid(), "name": p.name(), "cpu": round(p.cpu_percent(None) / NCPU, 1),
                            "mem": p.memory_info().rss, "started": p.create_time(), "cmd": cmd[:300]})
        except psutil.Error:
            continue
    return out


def end(shell_pid: int, pid: int, force: bool = False) -> str:
    """Stop one process under a terminal's shell, and its children. 'ended' | 'killed' | 'not_in_terminal' | 'gone'.
    Only descendants of that shell qualify: never the shell itself, shelldeck, or anything else on the machine."""
    try:
        shell = psutil.Process(shell_pid)
        if pid not in {p.pid for p in shell.children(recursive=True)}:
            return "not_in_terminal"
        target = psutil.Process(pid)
        group = [*target.children(recursive=True), target]
    except psutil.NoSuchProcess:
        return "gone"
    except psutil.Error:
        return "not_in_terminal"
    for p in group:
        try:
            p.kill() if force else p.terminate()
        except psutil.Error:
            pass
    deadline = time.monotonic() + 3
    while (alive := [p for p in group if _running(p)]) and time.monotonic() < deadline:
        time.sleep(0.1)
    if alive and not force:
        for p in alive:
            try:
                p.kill()
            except psutil.Error:
                pass
        return "killed"
    return "killed" if force else "ended"


def _running(p: psutil.Process) -> bool:
    """A stopped process its parent hasn't reaped yet (a zombie) counts as ended."""
    try:
        return p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False
