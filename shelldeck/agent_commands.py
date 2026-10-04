"""Each agent's own slash commands behind one set of shelldeck actions (`sd agent cmd Maya compact`, the AI agents page).

shelldeck types the agent's native command (Claude `/compact`, Gemini `/compress`, ...) into its terminal; it never
re-implements what the command does. Sources:
  claude  built-in command names in Claude Code 2.1.289's cli.js
  codex   codex-rs/tui/src/slash_command.rs (openai/codex)
  gemini  docs/reference/commands.md (google-gemini/gemini-cli)
  devin   Devin CLI docs as quoted by web search (commands & flags, essential commands) and other projects' Devin
          integrations; docs.devin.ai itself and the CLI were unreachable from the build box
"""

import re

# action -> (description, takes an argument)
ACTIONS: dict[str, tuple[str, bool]] = {
    "compact": ("summarize the conversation to free context", False),
    "clear": ("clear the conversation and start fresh", False),
    "new": ("start a new chat", False),
    "model": ("pick the model (or set it: model NAME where the agent accepts one)", True),
    "status": ("show session status / token usage", False),
    "resume": ("open the agent's session picker", False),
    "review": ("review the current changes", False),
    "init": ("create the agent's project instructions file", False),
    "memory": ("show or edit the agent's memory", False),
    "rename": ("rename the agent's session", True),
    "export": ("export the conversation", False),
    "diff": ("show the git diff", False),
    "hooks": ("view lifecycle hooks", False),
    "permissions": ("choose what the agent may do", False),
    "plan": ("switch to plan mode", False),
    "rewind": ("rewind the conversation", False),
    "quit": ("exit the agent", False),
}

# agent -> action -> native command (without arguments)
COMMANDS: dict[str, dict[str, str]] = {
    "claude": {"compact": "/compact", "clear": "/clear", "new": "/clear", "model": "/model", "status": "/status", "resume": "/resume",
               "review": "/review", "init": "/init", "memory": "/memory", "rename": "/rename", "export": "/export", "hooks": "/hooks",
               "permissions": "/permissions", "plan": "/plan", "rewind": "/rewind", "quit": "/exit"},
    "codex": {"compact": "/compact", "clear": "/clear", "new": "/new", "model": "/model", "status": "/status", "resume": "/resume",
              "review": "/review", "init": "/init", "rename": "/rename", "export": "/export", "diff": "/diff", "hooks": "/hooks",
              "permissions": "/permissions", "plan": "/plan", "quit": "/quit"},
    "gemini": {"compact": "/compress", "clear": "/clear", "new": "/clear", "model": "/model", "status": "/stats", "resume": "/resume",
               "init": "/init", "memory": "/memory show", "hooks": "/hooks", "permissions": "/permissions", "plan": "/plan",
               "rewind": "/rewind", "quit": "/quit"},
    "devin": {"compact": "/compact", "clear": "/clear", "new": "/new", "model": "/model", "status": "/context", "resume": "/resume",
              "plan": "/plan", "rewind": "/revert", "quit": "/exit"},
}
VERIFIED = {"claude": True, "codex": True, "gemini": True, "devin": True}
SAFE_VALUE = re.compile(r"[\w.:/@+-]{1,80}")  # an argument typed after the command: one plain word


def catalog(agent: str) -> list[dict]:
    """The actions this agent supports, with the native command each one types."""
    return [{"action": a, "command": c, "description": ACTIONS[a][0], "takes_arg": ACTIONS[a][1], "verified": VERIFIED.get(agent, False)}
            for a, c in COMMANDS.get(agent, {}).items()]


def line(agent: str, action: str, arg: str = "") -> str:
    """The text to type, e.g. `/compress` or `/rename reviewer`. ValueError with a stable code otherwise."""
    cmds = COMMANDS.get(agent)
    if not cmds:
        raise ValueError("no_native_commands")
    if action not in cmds:
        raise ValueError("unsupported_command")
    if arg:
        if not ACTIONS[action][1]:
            raise ValueError("no_argument")
        if not SAFE_VALUE.fullmatch(arg):
            raise ValueError("invalid_argument")
        return f"{cmds[action]} {arg}"
    return cmds[action]
