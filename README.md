<div align="center">

<img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/shelldeck/static/icon.svg" width="88" alt="shelldeck logo">

# shelldeck

**Your terminals, organised by project. In the browser, on your own machine.**

Real shells (PowerShell, cmd, Git Bash, WSL, bash, zsh, fish) grouped by project,<br>
in split or floating panes that survive restarts, with your AI coding agents working side by side.

[![PyPI](https://img.shields.io/github/v/release/codejunction/shelldeck?color=8b5cf6&label=pypi)](https://pypi.org/project/shelldeck/)
[![Python](https://img.shields.io/pypi/pyversions/shelldeck?color=8b5cf6)](https://pypi.org/project/shelldeck/)
[![CI](https://github.com/codejunction/shelldeck/actions/workflows/ci.yml/badge.svg)](https://github.com/codejunction/shelldeck/actions/workflows/ci.yml)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-8b5cf6)](#install)
[![License: MIT](https://img.shields.io/badge/license-MIT-8b5cf6)](https://github.com/codejunction/shelldeck/blob/main/LICENSE)

[Install](#install) · [Features](#features) · [CLI](#cli) · [Remote access](#remote-access) · [Security](#security) · [Contributing](https://github.com/codejunction/shelldeck/blob/main/CONTRIBUTING.md)

<img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/screenshot.png" alt="shelldeck with four named terminals in three projects: a git log, Claude Code, Codex asking to trust a folder, and sd top" width="100%">

</div>

## Install

| | Windows (PowerShell) | Linux |
|---|---|---|
| **Standalone** (default) | `irm https://raw.githubusercontent.com/codejunction/shelldeck/main/install.ps1 \| iex` | `curl -fsSL https://raw.githubusercontent.com/codejunction/shelldeck/main/install.sh \| sh` |
| **uv tool** | `& ([scriptblock]::Create((irm https://raw.githubusercontent.com/codejunction/shelldeck/main/install.ps1))) -Pip` | `curl -fsSL https://raw.githubusercontent.com/codejunction/shelldeck/main/install.sh \| sh -s -- --pip` |

- **Standalone:** a self-contained build, no Python or uv needed. It updates itself from *Settings › Updates*.
- **uv tool:** the Python package (`uv tool install shelldeck`, `pipx install shelldeck`, or `uvx shelldeck` to try it). Update with `uv tool upgrade shelldeck`. Plugins only work in this mode.
- macOS (untested) gets the Python package; there is no standalone build for it yet.
- Needs Windows 10 1809+ (ConPTY) or Linux x64, and a modern browser.

<details>
<summary><b>Standalone layout, installer options, updates</b></summary>

The installer downloads `shelldeck-<version>-windows-x64.zip` or `-linux-x64.tar.gz` from the latest [GitHub release](https://github.com/codejunction/shelldeck/releases), checks its `.sha256`, unpacks it into `%LOCALAPPDATA%\shelldeck` or `~/.local/share/shelldeck` and puts that folder on PATH:

```text
shelldeck/
  current.txt              the version in use, e.g. 0.0.12
  sd.cmd, sd               shims that run versions/<current>/sd, so `sd` and agent hooks survive updates
  versions/
    0.0.12/                shelldeck, sd-pty, sd-ui, sd (+ .exe on Windows) and _internal/
```

Environment variables: `SHELLDECK_VERSION=0.0.12` (a specific release, release candidates included), `SHELLDECK_ROOT=<dir>`, `SHELLDECK_NO_MODIFY_PATH=1`, `SHELLDECK_ARCHIVE=<file>` (a downloaded archive). Running the installer again installs the newest release next to the old one.

**Updates.** Standalone: *Settings › Updates* (or the *Update now* toast) downloads the new release, checks its sha256, unpacks it next to the running one and restarts only the web app; terminals and their programs keep running. uv: `uv tool upgrade shelldeck`, then `sd restart`. Release candidates: `SHELLDECK_VERSION=0.0.13rc1` with the installer, or `uv tool install --force shelldeck==0.0.13rc1`.

**Processes.** `shelldeck` supervises `sd-pty`, which owns the shells, and `sd-ui`, the web app, which restarts on its own. Task Manager shows all three. `sd restart` restarts only the web app; `sd stop` stops everything.

</details>

## Quick start

```sh
sd                 # start shelldeck in the background and open it in your browser
sd ~/code/api      # add a folder as a project and open a terminal in it
```

The first visit asks you to create a password. Terminals keep running in the background until you close them, even with the browser closed.

## Features

### Terminals

- **Real shells per OS.** Windows: `pwsh`, Windows PowerShell, cmd, Git Bash, WSL (any distro). Linux: every shell in `/etc/shells`.
- **Tiled or free layout.** Split, drag titles to split or swap, maximize; or floating windows on a canvas with *Tile all*.
- **Nothing gets lost.** Closing the tab keeps terminals running. After a restart or reboot each terminal reopens in its last folder with its previous text above a "restored" line. Updates restart only the UI, so shells don't even notice.
- **Shell integration** (pwsh, PowerShell, cmd, bash, zsh, fish): the pane title shows the last command (red if it failed), `Ctrl+Shift+Up/Down` jumps between prompts, and your own prompt still loads.
- **Background terminals** (`Ctrl+Alt+W`), **replay the last command** (`Ctrl+Alt+P`), **port chips** for dev servers, **clickable paths** (`src/app.py:12:5`, tracebacks) that open in VS Code or the built-in editor.

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/palette.png" alt="Command palette filtered to split commands"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/ask-menu.png" alt="The ? menu at the shell prompt offering Ask for a question"></td>
  </tr>
  <tr>
    <td align="center"><sub>Command palette (<code>Ctrl+Shift+P</code>)</sub></td>
    <td align="center"><sub><code>?</code> menu with Ask, right at the prompt</sub></td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/search-all.png" alt="Search all terminals dialog with highlighted matches"></td>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/editor.png" alt="Built-in editor previewing a markdown file"></td>
  </tr>
  <tr>
    <td align="center"><sub>Search every terminal's output (<code>Ctrl+Shift+F</code>)</sub></td>
    <td align="center"><sub>Built-in editor and markdown viewer (<code>sd edit</code>, <code>sd view</code>)</sub></td>
  </tr>
</table>

- **`?` menu and Ask.** Type `?` on an empty prompt for shelldeck's commands, bookmarks and plugin commands at the cursor. Type a question instead and *Ask* has an installed agent's model (Claude, Codex, Gemini or Devin; *Settings › AI agents*) write one command for that shell and OS. Enter types it, Shift+Enter runs it. `Ctrl+I` opens just the Ask box; `sd ask "..."` prints the command.

### Projects

- **Projects sidebar** with a color per project (sidebar dot, pane border, background tint), added through a folder browser and reordered by drag.
- **Git at a glance.** A `±N` chip on projects with uncommitted work; the git dialog shows changed files with their diffs, and the full commit graph with one-click checkout.
- **Machines.** The picker above *New terminal* switches between this computer and your SSH machines (Linux; key, agent or a password typed in the terminal, LDAP names like `CORP\jdoe` work). Windows machines open as RDP desktops. *Test connection* checks the network and the login. Passwords are never stored.

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/git-graph.png" alt="Git graph with branches, tags and authors"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/git-changes.png" alt="Git changes tab with added and removed lines per file"></td>
  </tr>
  <tr>
    <td align="center"><sub>Git graph with one-click checkout</sub></td>
    <td align="center"><sub>Uncommitted changes and diffs</sub></td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/machines.png" alt="Machine picker with local, an SSH machine and an RDP desktop"></td>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/remote-systems.png" alt="Remote systems page listing SSH and RDP machines"></td>
  </tr>
  <tr>
    <td align="center"><sub>Machine picker</sub></td>
    <td align="center"><sub>Remote systems (SSH and RDP)</sub></td>
  </tr>
</table>

### AI agents, working as a team

- **Detected automatically.** A terminal running Claude Code, Codex, Devin, Gemini, Copilot, Cursor Agent, opencode, Aider, Amp, Qwen, Goose, Droid, Crush or Kiro gets an **AI** chip with the model and how full its context window is.
- **Needs-you alerts.** Only when an agent asks something (a permission prompt, a question, a `[y/n]`): an amber chip, a chime and a toast or desktop notification. The sidebar and *Open next* rank agents by who needs you.
- **Exact state from hooks.** `sd integration install` hooks Claude Code, Codex, Gemini and Devin so they report `working` / `blocked` / `done` themselves, and can be resumed after a restart. 15 more agents have preview hooks. See [docs/agent-integrations.md](docs/agent-integrations.md).
- **Agents talk to each other.** Each terminal has a name (Maya, Kofi…). From inside one, `sd agents`, `sd peek`, `sd tell`, `sd spawn "task" --model small|medium|large` (a sub-agent in a new pane), `sd handoff` and `sd done`. A sub-agent's questions go to its parent agent, not to you. shelldeck installs a `shelldeck` skill so agents know these commands.
- **Context that outlives the agent.** Each project keeps a task, next step, facts, decisions and recent activity (commands, tests, edited files from the hooks; never output or secrets) in a local database and in `.shelldeck/*.md`. A new agent runs `sd context` and continues. `sd recall` searches every project; `--smart` adds keywords from an agent's smallest model. No model runs inside shelldeck.

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/agents.png" alt="AI agents page with running agents, integrations and available agent CLIs"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/context.png" alt="Context page with the task, facts, decisions and recent activity"></td>
  </tr>
  <tr>
    <td align="center"><sub>AI agents: status, integrations, launch with a model</sub></td>
    <td align="center"><sub>Context: what the project is working on</sub></td>
  </tr>
</table>

### Everything else

- **Task manager** (`Ctrl+Alt+M`, `sd top`): CPU, RAM and NVIDIA GPU for the machine and each terminal's process tree, with agents and ports.
- **Bookmarks** (insert, or Shift+Enter to run), **Scheduler** (cron jobs in a project folder, with logs), **Tasks** (a board with due dates, reminders and *Send to agent*), **Command history** with exit codes and durations, **Scratchpad** (markdown notes; agents add with `sd notes`).
- **Radio.** The top-bar radio plays [cliamp](https://cliamp.stream) channels; your browser streams them directly.
- **Devices.** Every signed-in browser, which one is in use, and *Sign out*.
- **Plugins.** Python packages add `?` menu and `sd` commands and react to events. See [Plugins](#plugins).

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/task-manager.png" alt="Task manager with CPU, memory, GPU and per-terminal usage"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/tasks.png" alt="Task board with backlog, to do, in progress and done"></td>
  </tr>
  <tr>
    <td align="center"><sub>Task manager, per terminal</sub></td>
    <td align="center"><sub>Task board</sub></td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/bookmarks.png" alt="Bookmarks list with commands per project"></td>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/scheduler.png" alt="Scheduler board with cron jobs"></td>
  </tr>
  <tr>
    <td align="center"><sub>Bookmarks</sub></td>
    <td align="center"><sub>Scheduler</sub></td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/history.png" alt="Command history with exit codes, durations and folders"></td>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/scratchpad.png" alt="Scratchpad note rendered as a markdown checklist"></td>
  </tr>
  <tr>
    <td align="center"><sub>Command history</sub></td>
    <td align="center"><sub>Scratchpad</sub></td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/settings.png" alt="Settings dialog with Appearance, Terminal, AI agents, Updates and Password sections"></td>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/radio.png" alt="Radio menu with cliamp channels and a volume slider"></td>
  </tr>
  <tr>
    <td align="center"><sub>Settings</sub></td>
    <td align="center"><sub>Radio</sub></td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/share.png" alt="Share dialog with the terms of sharing"></td>
    <td><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/devices.png" alt="Devices page with the signed-in browser"></td>
  </tr>
  <tr>
    <td align="center"><sub>Share over a Cloudflare tunnel</sub></td>
    <td align="center"><sub>Devices</sub></td>
  </tr>
</table>

### Plugins

A plugin is a Python package with a `shelldeck.plugins` entry point. Its commands show up in the `?` menu (`?docker logs web`) and the CLI (`sd docker logs web`), and return text to show or a command line to type.

```python
# pyproject.toml: [project.entry-points."shelldeck.plugins"]  docker = "shelldeck_docker:plugin"
from shelldeck.plugins import Plugin

plugin = Plugin("docker", "0.1.0")

@plugin.command("logs", "Follow a container's logs", usage="<container>")
def logs(args, ctx):  # ctx: session_id, cwd, shell, project
    return {"input": "docker logs -f " + " ".join(args)}
```

Install into shelldeck's environment (`uv tool install shelldeck --with ./examples/shelldeck-docker`) and restart. The *Plugins* page and `sd plugin list|enable|disable` manage them. Plugins run with your permissions; install only ones you trust. Guide: [docs/plugins.md](docs/plugins.md).

<details>
<summary><b>Keyboard shortcuts</b></summary>

| Keys | Action |
| --- | --- |
| `Ctrl+Shift+P` | Command palette (`Ctrl+K` also works outside a terminal) |
| `Ctrl+Alt+N` | New terminal in the current project |
| `Ctrl+Alt+\` / `Ctrl+Alt+-` | Split right / down |
| `Ctrl+Alt+Arrows` | Focus the pane in that direction |
| `Ctrl+Alt+1…9` | Focus pane 1–9 |
| `Ctrl+Alt+Enter` | Maximize / restore the pane |
| `Ctrl+Alt+Q` | Close the focused terminal (offers the background when a command runs) |
| `Ctrl+Alt+P` | Replay the last command |
| `Ctrl+Alt+W` | Send the terminal to the background (keeps running) |
| `Ctrl+Alt+B` | Bookmark picker (Space selects several, Shift+Enter runs) |
| `Ctrl+Alt+E` | Toggle the sidebar |
| `Ctrl+Alt+S` / `Ctrl+Alt+T` | Scheduler / Tasks |
| `Ctrl+Alt+M` | Task manager |
| `Ctrl+Alt+R` | Command history |
| `Ctrl+Shift+F` | Search all terminals |
| `?` / `Ctrl+I` (empty shell prompt) | Command menu with Ask / just the Ask box |
| `Ctrl+Alt+,` | Settings |
| `Ctrl+Alt+L` | Lock |
| `Ctrl+Alt+H` | Show shortcuts |
| `Ctrl+C` / `Ctrl+V` | Copy the selection (otherwise sends Ctrl+C) / paste |
| `Ctrl+F` | Find in the terminal |
| `Ctrl+Shift+Up` / `Down` | Jump to the previous / next prompt |

In the sidebar, Ctrl+click or middle-click a terminal to open it in a split. Right-click a project or terminal for its menu.

</details>

<details>
<summary><b>Settings</b></summary>

| Setting | Values | Default |
| --- | --- | --- |
| Theme | dark, light, system | dark |
| Terminal colors | default (follows theme), Dracula, One Dark, Nord, Gruvbox Dark, Solarized Dark, Solarized Light, GitHub Light | default |
| Terminal font / size | any installed monospace font / 8–32 | Cascadia or Nerd Font / 13 |
| Pane layout | tiled, free | tiled |
| Project colors | tint on, off | on |
| Default shell | Windows: pwsh, powershell, cmd, gitbash, wsl · Linux: shells from `/etc/shells` | pwsh / `$SHELL` |
| WSL distribution | any installed distro, or the system default | system default |
| Open file paths with | VS Code (at the line), shelldeck's editor, system default app | VS Code |
| Ask agent / Ask model | first installed, Claude Code, Codex, Gemini CLI, Devin CLI / that agent's models | first installed / its smallest |
| Smart search | off, first installed agent, Claude Code (haiku), Codex, Gemini CLI (flash-lite), Devin CLI | off |
| Resume agent sessions after a restart | ask, automatically, never | ask |
| Updates | installed and latest version, *Update* (standalone) | |
| Password | created on first visit, changed here (current + new) | required |

</details>

## CLI

```text
sd [--port N] [--no-window] [--app]  start the server if needed, print the banner and open the browser (= sd web)
sd PATH / sd open [PATH] [--shell S] add PATH as a project and open a terminal in it
sd list / sd info                    terminals grouped by project / the terminal you're in
sd restart / sd stop                 restart the web app (terminals keep running) / stop everything
sd serve [--host H]                  run the server in the foreground

sd agents [--all]                    AI agents in this project's terminals (--all: everywhere, plus installed CLIs)
sd peek TERMINAL [-n 40]             last lines of another terminal (by name like Maya, id or title)
sd tell TERMINAL "MESSAGE"           type a message into another terminal and press Enter
sd spawn "TASK" [--agent A] [--model small|medium|large|auto|NAME]   sub-agent in a new terminal
sd handoff TERMINAL "TASK" / sd done ID ["SUMMARY"] [--failed] / sd handoffs [--all]
sd answer TERMINAL KEY...            press keys in another terminal (1, y enter, esc)
sd close TERMINAL [--force]          close a terminal; from an agent only its own sub-agents
sd agent status|wait|start|prompt|rename|cmd|explain|resume|report|extract ...   agent lifecycle
sd integration list|detect|status|install|uninstall [AGENT] [--all]   lifecycle hooks
sd events subscribe [--types agent,handoff] [--since ID]   JSON lines of agent.*, handoff.* events

sd init [PATH] [--task TEXT]         set up .shelldeck/ (STATE, TASK, MEMORY, DECISIONS, AGENTS.md)
sd context [PROJECT] / sd resume     task, state, next action, memory, decisions, events
sd remember FACT... / sd discover FINDING / sd decide TITLE [-r REASON] / sd memory
sd task update [STATUS] [--task --step --next --tests --error]
sd recall QUERY [--smart | --agent A [--model M]]   search every project
sd switch AGENT [--note TEXT]        continue this project's task with another agent
sd ask "QUESTION"                    print one command for this terminal's shell and OS

sd notes list|add|append|show|replace|delete   Scratchpad
sd top / sd ps [TERMINAL] / sd kill TERMINAL PID [--force]   Task manager
sd edit FILE / sd view FILE          open a file in shelldeck's editor / viewer
sd search QUERY [--root DIR]         LLM-free code search with ranked snippets
sd render FILE                       pretty-print code or markdown
sd schedule list|add|run|toggle|delete|logs
sd task list|add|move|delete|alarms
sd plugin list|enable|disable|run    plugins; `sd <plugin> <command>` runs one
sd install-skill [AGENT...] [--remove]   teach agent CLIs the sd commands (automatic on start)
sd share [stop] [--new-link]         share over a Cloudflare tunnel (see Remote access)
sd login-link / sd reset-password    emergency, host only
```

- Default port `5455` (`--port` or `SHELLDECK_PORT`). `--app` opens a chromeless Edge/Chrome window.
- Inside a terminal: `SHELLDECK_SESSION_ID`, `SHELLDECK_NICK`, `SHELLDECK_PORT`, `SHELLDECK_AGENT_REPORT_TOKEN`, and `SHELLDECK_PARENT` for sub-agents.
- To let Claude Code sub-agents report back on their own, allow `Bash(sd:*)` in its permissions.

## Remote access

**SSH tunnel (recommended).** Nothing is exposed:

```sh
sd serve                                  # on the VM: stays on 127.0.0.1:5455
ssh -N -L 5455:127.0.0.1:5455 you@vm      # on your machine, then open http://127.0.0.1:5455
```

**`sd share`** (any device, no setup; needs [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), no account), or the **Share** button in the top bar:

```sh
sd share              # prints a QR code and a link; Ctrl+C stops sharing
sd share --new-link   # another one-use link for a second device
sd share stop
```

- Each link works **once**, for **10 minutes**, and the device then waits until you allow it on this machine. It still needs your password (12+ characters for sharing).
- Cloudflare ends TLS, so it can see the traffic. For sensitive work use SSH or Tailscale.
- The first share asks you to accept the terms. Stopping a share signs out every browser that came through it.

**HTTPS on the VM's address:** `sd serve --host 0.0.0.0 --cert cert.pem --key key.pem`. Plain HTTP on a non-local address is refused (`--insecure-http` overrides). A first visit from another machine also needs the setup code `sd serve` prints.

On phones, a key bar adds Esc, Tab, Ctrl and arrows, and dialogs open as bottom sheets.

## Security

- **Local only.** Binds to `127.0.0.1` and rejects requests whose `Host` or `Origin` isn't the app, so other websites can't reach your shells. Responses forbid framing, sniffing and referrers.
- **Password required.** PBKDF2-SHA256 (600k iterations); logins are `HttpOnly`, `SameSite=Strict` cookies stored as SHA-256; failed logins slow down; 30 minutes idle locks the browser.
- **One device at a time.** Logging in elsewhere takes over and idles the rest; the Devices page revokes any login.
- **The CLI** uses a token file readable only by your account, rotated on every start.
- **Forgot the password?** On the host: `sd login-link` (one-time URL, 5 minutes) or `sd reset-password`. Never from the browser.
- **Your data** lives in `~/.config/shelldeck/` (`SHELLDECK_HOME` moves it).

Found a vulnerability? Report it privately; see [SECURITY.md](https://github.com/codejunction/shelldeck/blob/main/SECURITY.md).

## Development

```sh
git clone https://github.com/codejunction/shelldeck && cd shelldeck
uv sync
uv run shelldeck --port 5466 serve     # dev server, http://127.0.0.1:5466
uv run pytest -q                       # includes a real PTY round trip
uv run ruff check
```

FastAPI and uvicorn, `pywinpty` (ConPTY) or the stdlib `pty`, SQLite, and plain HTML/CSS/JS with vendored xterm.js (no build step). See [CONTRIBUTING.md](https://github.com/codejunction/shelldeck/blob/main/CONTRIBUTING.md).

## License

[MIT](https://github.com/codejunction/shelldeck/blob/main/LICENSE). xterm.js is MIT-licensed too (see `shelldeck/static/vendor/LICENSE-xterm.txt`).
