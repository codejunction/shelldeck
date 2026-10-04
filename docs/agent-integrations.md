# Agent integrations

An **integration** is a hook or plugin that shelldeck adds to an agent's own config. It reports the agent's
lifecycle (working, blocked on approval, done, idle) straight to the terminal it runs in. It is not the **skill**:
the skill only teaches an agent the `sd` commands.

**Supported now: Claude Code, Codex, Gemini CLI and Devin CLI.** Each gets exact state (working, needs you, done) and
resume. These four are installed by `sd integration install` and shown first on the AI agents page.

Every other row below is a **preview, to be finished later**. It installs with `sd integration install <agent>`
or `--all`, and it is tested against config files only, not against the running agent.

Without an integration, shelldeck still detects the agent and reads its state from the screen. With one, the AI agents
page shows the state as coming from `integration`.

The formats below follow each agent's hook documentation, as connected by [dotpals](https://github.com/Rikinshah787/dotpals) (Claude, Gemini, Cursor, Copilot, OpenCode) and [herdr](https://github.com/herdrdev/herdr) (Codex, Qwen Code, Qoder, Droid, Devin and the resume commands). Only watching events are used: an integration can never
approve, deny or block a tool.

| Agent | What is installed | Where (env override) | Session/resume |
| --- | --- | --- | --- |
| Claude Code | Hook entries for `SessionStart`, `UserPromptSubmit`, `Pre/PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `Notification`, `SubagentStart`, `PreCompact`, `Stop`, `StopFailure` (all `async`) | `~/.claude/settings.json` (`CLAUDE_CONFIG_DIR`) | yes: `claude --resume <id>` |
| Gemini CLI (v0.26+) | Hook groups (`matcher: "*"`) for `SessionStart`, `BeforeAgent`, `Before/AfterTool`, `PreCompress`, `AfterAgent`, `Notification` (approval) | `~/.gemini/settings.json` (`GEMINI_DIR`) | yes: `gemini --resume <id>` (to confirm on a real install) |
| Cursor (editor and CLI) | `hooks.json` entries for the watching hooks (`sessionStart`, `beforeSubmitPrompt`, `after*`, `postToolUse*`, `subagentStop`, `preCompact`, `stop`) | `~/.cursor/hooks.json` (`CURSOR_DIR`) | yes: `cursor-agent --resume <id>` |
| GitHub Copilot CLI | Its own file; each event's command names the event, because Copilot's payloads don't | `~/.copilot/hooks/shelldeck.json` (`COPILOT_HOME`) | yes: `copilot --resume=<id>` |
| Codex | `hooks.json` entries for `SessionStart`, `UserPromptSubmit`, `Stop`, `Interrupt`, plus `hooks = true` under `[features]` in `config.toml` (left in place on uninstall) | `~/.codex/` (`CODEX_HOME`) | yes: `codex resume <id>` |
| Qwen Code | `SessionStart` (`matcher: "*"`): session id only | `~/.qwen/settings.json` (`QWEN_HOME`) | yes: `qwen --resume <id>` |
| Qoder CLI | `SessionStart` (`matcher: "*"`): session id only | `~/.qoder/settings.json` (`QODER_CONFIG_DIR`) | yes: `qodercli --resume <id>` |
| Factory Droid | `SessionStart`: session id only | `~/.factory/settings.json` | yes: `droid --resume <id>` |
| Devin CLI | `SessionStart`, `UserPromptSubmit`, `Pre/PostToolUse`, `PermissionRequest` (approval), `Stop` (done). Without an id in the payload, the session comes from Devin's `sessions.db` | `config.json` in `$XDG_CONFIG_HOME/devin`, `%APPDATA%\devin` or `~/.config/devin` | yes: `devin --resume <id>` |
| Kimi Code CLI (0.14+) | A marked `[[hooks]]` block (`# >>> shelldeck kimi integration`) appended to `config.toml`: prompts, tools, `AskUserQuestion` (blocked: question), permission request/result, stop, interrupt | `~/.kimi-code/` (`KIMI_CODE_HOME`) | yes: `kimi --session <id>` |
| MastraCode | Flat entries keyed by event at the top of `hooks.json` (agent start/end, tools, permissions, sub-agents, interrupt) | `~/.mastracode/hooks.json` | yes: `mastracode --thread <id>` |
| Antigravity CLI | One `shelldeck` block (`PreInvocation`) in `hooks.json`, which is keyed by hook name: session id only | `~/.gemini/config/` (`ANTIGRAVITY_CLI_CONFIG_DIR`) | yes: `agy --conversation <id>` |
| Grok CLI | Its own `hooks/shelldeck.json` (`SessionStart`): session id only | `~/.grok/` (`GROK_HOME`) | yes: `grok --resume <id>` |
| Kilo Code CLI | The OpenCode plugin, pointed at Kilo (`plugin/shelldeck.js`) | `~/.config/kilo/` (`XDG_CONFIG_HOME`) | yes: `kilo --session <id>` |
| Letta Code | `SessionStart` (`quiet`): session id only; the default conversation resumes per agent | `~/.letta/settings.json` | yes: `letta --conversation <id>` or `--conversation default --agent <id>` |
| Hermes Agent | A Python plugin, `plugins/shelldeck-agent-state/`, listed under `plugins.enabled` in `config.yaml`. An inline or unusual YAML layout is refused (add it by hand) | `~/.hermes/` (`HERMES_HOME`; `%LOCALAPPDATA%\hermes` on Windows) | yes: `hermes --resume <id>` |
| Pi | An extension, `extensions/shelldeck-agent-state.ts` (TUI sessions): session start, agent start, agent settled | `~/.pi/agent/` (`PI_CODING_AGENT_DIR`) | yes: `pi --session <id>` |
| OMP | An extension, `extensions/shelldeck-omp-agent-state.ts`: also tool approvals and the `ask` tool (blocked), and `agent_end` unless it will continue | `~/.omp/agent/` (`PI_CONFIG_DIR`, `PI_CODING_AGENT_DIR`) | yes: `omp --resume=<id>` |
| OpenCode | A plugin, `plugins/shelldeck.js`, that posts `chat.message`, `tool.execute.before`, `session.*` and `permission.*` | `~/.config/opencode/` (`XDG_CONFIG_HOME`) | yes: `opencode --session <id>` |

**Without installing anything**, shelldeck also reads two agents' own logs (source `native`):

- **Codex:** the newest rollout in `~/.codex/sessions` for the terminal's folder.
  - `task_started`, a user message or a tool call means working; `task_complete` means done.
  - The session id gives `codex resume <id>`.
- **Claude Code:** `~/.claude/sessions/<pid>.json`. `busy` means working, and its session id gives `claude --resume <id>`.

A native "working" holds only 15 s past the last log event, so a prompt waiting for approval (the log goes quiet) is
picked up by screen detection again. An integration report outranks native, which outranks the screen.

**Session id only** (Qwen, Qoder, Droid, Grok, Antigravity, Letta, Hermes): herdr found these agents' hook events unreliable for lifecycle, so their hooks report only the session id, for resume. The state stays with screen detection.

Installing needs the agent's config folder (install and run the agent once first). Amp, Kiro, Maki, Cline, Aider, Goose, Crush and the rest keep using screen detection for now (see `sd integration list`).

## Native command-line flags

When shelldeck starts an agent (spawn, *Launch*, `sd agent start`, resume), it builds the command with that agent's own
flags (`team.launch_line`):

| Agent | Model | Initial prompt | Per-run hooks | Resume |
| --- | --- | --- | --- | --- |
| Claude Code | `--model` | positional `"prompt"` | `--settings <file>`: shelldeck's hook file in its config folder; nothing in `~/.claude` changes. Skipped when the global hook is installed | `claude --resume <id>` |
| Codex | `-m` | positional `"prompt"` | none: Codex reads hooks only from `hooks.json` (install), plus its own logs | `codex resume <id>` |
| Gemini CLI | `-m` | `-i "prompt"` | none: hooks only from `settings.json` (install) | `gemini --resume <id>` |
| Devin CLI | `--model` | `-- "prompt"` | none: hooks only from `config.json` (install), plus `sessions.db` | `devin --resume <id>` |

A prompt that is longer, or isn't plain words, is written to `.shelldeck/prompts/<id>.md`. The agent is then asked to
read that file, so no shell ever sees quotes or `$`.

Claude Code 2.1.289's `--help` confirms `--settings <file-or-json>`, the positional prompt and `--resume`. For the other
three, the flags come from their docs as used by dotpals and herdr.

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
