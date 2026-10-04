import json
import time
from datetime import datetime, timezone

from shelldeck import agent_state, agents


class Proc:
    def __init__(self, pid, cwd):
        self.pid, self._cwd = pid, cwd

    def create_time(self):
        return time.time() - 60

    def cwd(self):
        return self._cwd


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def test_codex_rollout_gives_state_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(agents.Path, "home", lambda: tmp_path)
    agents._files.clear()
    work = tmp_path / "proj"
    work.mkdir()
    day = tmp_path / ".codex" / "sessions" / "2026" / "10" / "04"
    day.mkdir(parents=True)
    log = day / "rollout-x.jsonl"
    now = time.time()
    lines = [{"type": "session_meta", "payload": {"id": "019a-thread", "cwd": str(work)}},
             {"timestamp": iso(now - 3), "type": "event_msg", "payload": {"type": "task_started"}},
             {"timestamp": iso(now - 1), "type": "response_item", "payload": {"type": "function_call", "name": "shell"}}]
    log.write_text("\n".join(map(json.dumps, lines)) + "\n")
    got = agents._codex_native(Proc(11, str(work)))
    assert got["state"] == "working" and got["age"] < 5 and got["resume_argv"] == ["codex", "resume", "019a-thread"]
    with log.open("a") as f:
        f.write(json.dumps({"timestamp": iso(now), "type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    assert agents._codex_native(Proc(11, str(work)))["state"] == "done"
    agents._files.clear()


def test_claude_session_file(tmp_path, monkeypatch):
    monkeypatch.setattr(agents.Path, "home", lambda: tmp_path)
    assert agents._claude_native(Proc(22, ".")) is None
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "22.json").write_text(json.dumps({"sessionId": "s-1", "status": "busy"}))
    got = agents._claude_native(Proc(22, "."))
    assert got["state"] == "working" and got["resume_argv"] == ["claude", "--resume", "s-1"]


def test_native_report_expires_so_a_quiet_log_falls_back_to_the_screen():
    r = agent_state.native_report("codex", {"state": "working", "age": 5}, now=100)
    assert r.source == "native:codex" and r.expires_at == 110
    assert agent_state.native_report("codex", {"state": "working", "age": 20}, now=100) is None
    assert agent_state.native_report("claude", {"state": None, "age": 0}, now=100) is None
    reg = agent_state.Registry()
    reg.put("t", agent_state.heuristic("approval", 100, ttl=100))
    reg.put("t", r)
    assert reg.status("t", 105).state is agent_state.State.WORKING
    assert reg.status("t", 111).state is agent_state.State.BLOCKED  # native expired: the screen's question shows
    _, _, ref = agent_state.parse_report({"source": "native:codex", "agent": "codex", "state": "working", "agent_session_id": "x",
                                          "resume_argv": ["codex", "resume", "x"]}, "codex")
    assert ref.resume_argv[0] == "codex"
