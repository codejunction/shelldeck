# Agent integrations

An **integration** is a hook or plugin that shelldeck adds to an agent's own config. It reports the agent's
lifecycle (working, blocked on approval, done, idle) straight to the terminal it runs in. It is not the **skill**:
the skill only teaches an agent the `sd` commands.

Without an integration, shelldeck still detects the agent and reads its state from the screen. With one, the AI agents
page shows the state as coming from `integration`.

The formats below follow each agent's hook documentation; they are the same mechanisms
[dotpals](https://github.com/Rikinshah787/dotpals) connects to. Only watching events are used: an integration can never
approve, deny or block a tool.

| Agent | What is installed | Where (env override) | Session/resume |
| --- | --- | --- | --- |
| Claude Code | Hook entries for `SessionStart`, `UserPromptSubmit`, `Pre/PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `Notification`, `SubagentStart`, `PreCompact`, `Stop`, `StopFailure` (all `async`) | `~/.claude/settings.json` (`CLAUDE_CONFIG_DIR`) | yes: `claude --resume <id>` |
| Gemini CLI (v0.26+) | Hook groups (`matcher: "*"`) for `SessionStart`, `BeforeAgent`, `Before/AfterTool`, `PreCompress`, `AfterAgent`, `Notification` | `~/.gemini/settings.json` (`GEMINI_DIR`) | no |
| Cursor (editor and CLI) | `hooks.json` entries for the watching hooks (`sessionStart`, `beforeSubmitPrompt`, `after*`, `postToolUse*`, `subagentStop`, `preCompact`, `stop`) | `~/.cursor/hooks.json` (`CURSOR_DIR`) | no |
| GitHub Copilot CLI | Its own file; each event's command names the event, because Copilot's payloads don't | `~/.copilot/hooks/shelldeck.json` (`COPILOT_HOME`) | no |
| OpenCode | A plugin, `plugins/shelldeck.js`, that posts `chat.message`, `tool.execute.before`, `session.*` and `permission.*` | `~/.config/opencode/` (`XDG_CONFIG_HOME`) | yes: `opencode --session <id>` |

Codex, Devin, Qwen, Amp and the rest keep using screen detection until their hook format has been checked (see
`sd integration list`).

## Commands

```sh
sd integration list            # every agent: method, lifecycle, resume, CLI found, integration status
sd integration detect          # installable agents found on this machine
sd integration install [AGENT...]   # default: every installable agent found
sd integration status [AGENT]
sd integration uninstall AGENT...
```

The AI agents page has the same controls under **Integrations**. Installing and removing are host-only
(`POST`/`DELETE /api/integrations/{agent}`).

## How it works

Hook agents run `"<python>" -m shelldeck.hook <agent> [event]` with the event JSON on stdin.
`<python>` is the absolute interpreter shelldeck runs on.

- It reads only the event name, the notification type and message, and the session id. Nothing else leaves the
  agent's process.
- It posts a report to `POST /api/agent-reports` with the terminal's `SHELLDECK_AGENT_REPORT_TOKEN`.
  - The server accepts that token on its own only from this machine, never through a share tunnel.
  - The token opens no other endpoint.
- Outside a shelldeck terminal it does nothing.
- It always exits 0 and prints only the "carry on" reply each agent expects (`{"continue":true}` for Cursor's
  `beforeSubmitPrompt`, `{}` for Gemini and Cursor).

Reports hold for 60 s (working) or 120 s (other states). After that the screen heuristic decides again, so an agent
that dies mid-turn never stays "working".

## Safety of config edits

- Shared configs (Claude, Gemini, Cursor) are merged:
  - Unrelated keys and hooks stay.
  - Only entries whose command contains `-m shelldeck.hook <agent>` are added or removed.
  - Installing twice changes nothing.
- A config that isn't plain JSON (comments, a bad edit) is left untouched, and the command reports an error.
- The first change keeps `<file>.shelldeck-backup`, and every write is atomic (temp file + rename).
- Own files (Copilot, OpenCode) put back whatever was there before on uninstall.
- **Outdated** means the hook's interpreter no longer exists (shelldeck was reinstalled elsewhere) or the event list
  changed. Reinstall to fix it.

## Troubleshooting

- *Status still says `screen`.*
  - The hook only reports inside shelldeck terminals.
  - OpenCode needs a restart after installing.
  - Gemini runs hooks only in trusted folders, and not when `hooksConfig.enabled` is false.
- *Check one event by hand* (inside a shelldeck terminal, with an agent running):
  `echo '{"hook_event_name":"PermissionRequest"}' | python -m shelldeck.hook claude`, then `sd agent status`.
