"""System and per-terminal resource usage (psutil; GPU via nvidia-smi when present)."""

import shutil
import subprocess
import sys
import threading
import time

import psutil

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
        root = _proc(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return None
    cpu = 0.0
    mem = count = 0
    top: tuple[str, float] | None = None
    pids = []
    for p in procs:
        try:
            p = _proc(p.pid)
            c = p.cpu_percent(None) / NCPU
            m = p.memory_info().rss
            name = p.name()
        except psutil.Error:
            continue
        seen.add(p.pid)
        pids.append(p.pid)
        count += 1
        cpu += c
        mem += m
        if p.pid != pid and (top is None or c >= top[1]):
            top = (name, c)
    return {"cpu": round(cpu, 1), "mem": mem, "procs": count, "top": top[0] if top else None, "pids": pids}


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
