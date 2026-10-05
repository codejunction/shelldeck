"""Agent hook -> shelldeck lifecycle report. Installed into agents' hook configs by `sd integration install`.

    python -m shelldeck.hook <agent> [event]

The agent runs this with its event as JSON on stdin. Outside a shelldeck terminal (no
SHELLDECK_AGENT_REPORT_TOKEN) it does nothing. It never blocks or changes the agent: it always exits 0 and
prints only what the agent expects for "carry on". Stdlib only, so each event costs one interpreter start.
Event names and payload fields follow each agent's hook docs (the same ones dotpals connects to):
  claude   https://docs.claude.com/en/docs/claude-code/hooks
  gemini   https://geminicli.com/docs/hooks/
  cursor   https://cursor.com/docs/hooks
  copilot  https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference
(OpenCode reports from its plugin, `integrations.OPENCODE_PLUGIN`, straight to the same endpoint.)
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

WORKING, IDLE, DONE, BLOCKED = "working", "idle", "done", "blocked"
SESSION = "session"  # report only the agent's session id (for resume); state stays with screen detection

# agent -> event -> state; None = ignore. Blocked carries "approval".
EVENTS: dict[str, dict[str, str | None]] = {
    "claude": {"SessionStart": IDLE, "UserPromptSubmit": WORKING, "PreToolUse": WORKING, "PostToolUse": WORKING,
               "PostToolUseFailure": WORKING, "PermissionRequest": BLOCKED, "SubagentStart": WORKING, "PreCompact": WORKING,
               "Stop": DONE, "SessionEnd": None, "Notification": None},  # names checked in Claude Code 2.1.289's cli.js
    "gemini": {"SessionStart": IDLE, "BeforeAgent": WORKING, "BeforeTool": WORKING, "AfterTool": WORKING, "PreCompress": WORKING,
               "AfterAgent": DONE, "Notification": BLOCKED, "SessionEnd": None},
    "cursor": {"sessionStart": IDLE, "beforeSubmitPrompt": WORKING, "afterShellExecution": WORKING, "afterFileEdit": WORKING,
               "afterMCPExecution": WORKING, "postToolUse": WORKING, "postToolUseFailure": WORKING, "subagentStop": WORKING,
               "afterAgentResponse": WORKING, "preCompact": WORKING, "stop": DONE, "sessionEnd": None},
    # formats and events as herdr installs them (github.com/herdrdev/herdr, src/integration)
    # codex-rs/hooks/src/events: PermissionRequest runs on the approval path, before the approval UI
    "codex": {"SessionStart": IDLE, "UserPromptSubmit": WORKING, "PreToolUse": WORKING, "PostToolUse": WORKING,
              "PermissionRequest": BLOCKED, "PreCompact": WORKING, "Stop": DONE, "Interrupt": IDLE, "SessionEnd": None},
    "qwen": {"SessionStart": SESSION},
    "qodercli": {"SessionStart": SESSION},
    "droid": {"SessionStart": SESSION},
    # Devin's hooks are Claude-shaped (events per its hooks docs). Devin rejects the whole hooks block if it holds an
    # unknown event (Notification, PreCompact, ...), so only these may be installed; its shell tool is `exec`
    "devin": {"SessionStart": IDLE, "UserPromptSubmit": WORKING, "PreToolUse": WORKING, "PostToolUse": WORKING,
              "PermissionRequest": BLOCKED, "Stop": DONE, "SessionEnd": None},
    "kimi": {"SessionStart": SESSION, "UserPromptSubmit": WORKING, "PreToolUse": WORKING, "PostToolUse": WORKING,
             "PostToolUseFailure": WORKING, "SubagentStart": WORKING, "PreCompact": WORKING, "PermissionRequest": BLOCKED,
             "PermissionResult": WORKING, "Stop": DONE, "Interrupt": IDLE},
    "mastracode": {"SessionStart": SESSION, "UserPromptSubmit": WORKING, "AgentStart": WORKING, "PreToolUse": WORKING,
                   "PermissionRequest": BLOCKED, "PermissionResult": WORKING, "SubagentStart": WORKING, "SubagentEnd": WORKING,
                   "Interrupt": IDLE, "AgentEnd": DONE, "Stop": DONE},
    "grok": {"SessionStart": SESSION},
    "letta": {"SessionStart": SESSION},
    "antigravity": {"PreInvocation": SESSION},
    "copilot": {"sessionStart": IDLE, "userPromptSubmitted": WORKING, "postToolUse": WORKING, "postToolUseFailure": WORKING,
                "notification": None, "errorOccurred": IDLE, "agentStop": DONE, "sessionEnd": None},
}
# what each agent wants on stdout so it carries on as if there were no hook
REPLY = {"cursor": lambda event: '{"continue":true}' if event == "beforeSubmitPrompt" else "{}", "gemini": lambda event: "{}"}
TTL_MS = {WORKING: 60_000, BLOCKED: 120_000, DONE: 120_000, IDLE: 120_000}
# agent -> resume argv for its session id (herdr's agent_resume.rs); None: no known resume command
RESUME = {
    "claude": lambda i: ["claude", "--resume", i], "codex": lambda i: ["codex", "resume", i],
    "gemini": lambda i: ["gemini", "--resume", i],  # docs/cli/session-management.md: --resume <session uuid>
    "copilot": lambda i: ["copilot", f"--resume={i}"], "devin": lambda i: ["devin", "--resume", i],
    "droid": lambda i: ["droid", "--resume", i], "qwen": lambda i: ["qwen", "--resume", i],
    "qodercli": lambda i: ["qodercli", "--resume", i], "cursor": lambda i: ["cursor-agent", "--resume", i],
    "kimi": lambda i: ["kimi", "--session", i], "mastracode": lambda i: ["mastracode", "--thread", i],
    "grok": lambda i: ["grok", "--resume", i], "antigravity": lambda i: ["agy", "--conversation", i],
    "hermes": lambda i: ["hermes", "--resume", i], "pi": lambda i: ["pi", "--session", i], "omp": lambda i: ["omp", f"--resume={i}"],
    # Letta's default conversation is per agent: "default:<agent id>"
    "letta": lambda i: ["letta", "--conversation", "default", "--agent", i[8:]] if i.startswith("default:") else ["letta", "--conversation", i],
}
STATES = {WORKING, IDLE, DONE, BLOCKED, SESSION}  # an explicit state in the installed command (matcher-specific hooks)
# Claude tools that stop and wait for the user (no PermissionRequest fires for them)
ASKS_USER = {"AskUserQuestion": "question", "ExitPlanMode": "approval"}
SESSION_KEYS = ("session_id", "sessionId", "conversation_id", "conversationId")


def state_for(agent: str, event: dict, forced: str | None = None) -> tuple[str | None, str | None]:
    """(state, blocked_reason) for one hook event, or (None, None) to ignore it."""
    if forced in STATES:
        asks = event.get("tool_name") == "AskUserQuestion"  # the agent asking you, not a permission prompt
        return forced, (("question" if asks else "approval") if forced == BLOCKED else None)
    name = event.get("hook_event_name") or ""
    state = EVENTS.get(agent, {}).get(name)
    if agent == "claude" and name == "PreToolUse" and event.get("tool_name") in ASKS_USER:
        return BLOCKED, ASKS_USER[event["tool_name"]]  # these tools wait on you; PostToolUse sets working again
    if agent == "claude" and name == "Notification":
        kind = event.get("notification_type") or ""
        msg = str(event.get("message") or "").lower()
        if kind == "permission_prompt" or "permission" in msg:
            return BLOCKED, "approval"
        if kind == "idle_prompt" or "waiting for your input" in msg:
            return IDLE, None
    if agent == "copilot" and name == "notification" and event.get("notification_type") == "permission_prompt":
        return BLOCKED, "approval"
    if agent == "copilot" and name == "errorOccurred" and event.get("recoverable"):
        return None, None
    return state, ("approval" if state == BLOCKED else None)


# tool-finished events whose payload names the tool and its input (Claude-shaped; Gemini's AfterTool; Copilot's
# toolName/toolArgs). Only the command or the edited path leaves the agent: never output or file contents.
AFTER_TOOL = {"PostToolUse": True, "PostToolUseFailure": False, "AfterTool": None, "postToolUse": True, "postToolUseFailure": False}
SHELL_TOOLS = {"bash", "shell", "exec", "exec_command", "local_shell", "run_shell_command", "powershell", "run_terminal_cmd", "execute"}
EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "write_file", "replace", "apply_patch", "create", "str_replace_editor"}


def activity(event: dict) -> dict | None:
    """{command, ok} or {file, change} for a finished tool call, else None."""
    name = event.get("hook_event_name") or ""
    if name not in AFTER_TOOL:
        return None
    tool = str(event.get("tool_name") or event.get("toolName") or "")
    args = event.get("tool_input") or event.get("toolArgs") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    if not isinstance(args, dict):
        return None
    ok = AFTER_TOOL[name]
    if ok is None:  # Gemini: the response says whether it failed
        resp = event.get("tool_response")
        ok = not (isinstance(resp, dict) and resp.get("error"))
    if tool.lower() in SHELL_TOOLS or (not tool and "command" in args):
        cmd = args.get("command") or args.get("cmd")
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        if isinstance(cmd, str) and cmd.strip():
            return {"command": cmd.strip()[:500], "ok": bool(ok)}
    if tool.lower() in EDIT_TOOLS:
        path = args.get("file_path") or args.get("path") or args.get("notebook_path")
        if isinstance(path, str) and path:
            return {"file": path[:300], "change": "write" if tool.lower() in ("write", "write_file", "create") else "edit"}
    return None


def report(agent: str, event: dict, forced: str | None = None) -> dict | None:
    """The report body for an event, with the native session for agents whose resume command is known."""
    state, reason = state_for(agent, event, forced)
    if not state:
        return None
    body: dict = {"source": f"integration:{agent}", "agent": agent}
    if state != SESSION:
        body |= {"state": state, "blocked_reason": reason, "ttl_ms": TTL_MS[state]}
    sid = next((v for k in SESSION_KEYS if isinstance(v := event.get(k), str) and v and not v.startswith("-")), None)
    if agent == "letta" and sid == "default":
        aid = event.get("agent_id")
        sid = f"default:{aid}" if isinstance(aid, str) and aid and not aid.startswith("-") else None
    if sid and agent in RESUME:
        body["agent_session_id"], body["resume_argv"] = sid, RESUME[agent](sid)
    elif state == SESSION:
        return None
    if act := activity(event):
        body["activity"] = act
    return body


def _url(path: str) -> str:
    home = Path(os.environ.get("SHELLDECK_HOME") or Path.home() / ".config" / "shelldeck")
    port = os.environ.get("SHELLDECK_PORT", "5455")
    scheme = "http"
    try:
        info = json.loads((home / "server.json").read_text(encoding="utf-8"))
        scheme = info["scheme"] if str(info.get("port")) == port else "http"
    except (OSError, ValueError, KeyError):
        pass
    return f"{scheme}://127.0.0.1:{port}{path}"


def send(body: dict, token: str) -> None:
    import ssl

    url = _url("/api/agent-reports")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Shelldeck-Report-Token": token})
    ctx = ssl._create_unverified_context() if url.startswith("https:") else None  # our own local server
    with urllib.request.urlopen(req, timeout=1.5, context=ctx) as r:
        r.read()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    agent = argv[0] if argv else ""
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        event = json.loads(raw or "{}")
        if not isinstance(event, dict):
            event = {}
    except ValueError:
        event = {}
    forced = argv[1] if len(argv) > 1 and argv[1] in STATES else None
    if len(argv) > 1 and not forced:  # Copilot's payloads don't name their event; the installed command does
        event.setdefault("hook_event_name", argv[1])
    token = os.environ.get("SHELLDECK_AGENT_REPORT_TOKEN")
    if token and agent in EVENTS:
        try:
            if body := report(agent, event, forced):
                send(body, token)
        except Exception:  # noqa: BLE001 - never break the agent
            pass
    if agent in REPLY:
        sys.stdout.write(REPLY[agent](event.get("hook_event_name") or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
