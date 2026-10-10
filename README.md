<div align="center">

<img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/shelldeck/static/icon.svg" width="88" alt="shelldeck logo">

# shelldeck

**Your terminals, organised by project. In the browser, on your own machine.**

Real shells (PowerShell, cmd, Git Bash, WSL, bash, zsh, fish) grouped by project,<br>
in split or free-floating panes that survive restarts, with your AI coding agents working side by side.

[![PyPI](https://img.shields.io/github/v/release/codejunction/shelldeck?color=8b5cf6&label=pypi)](https://pypi.org/project/shelldeck/)
[![Python](https://img.shields.io/pypi/pyversions/shelldeck?color=8b5cf6)](https://pypi.org/project/shelldeck/)
[![CI](https://github.com/codejunction/shelldeck/actions/workflows/ci.yml/badge.svg)](https://github.com/codejunction/shelldeck/actions/workflows/ci.yml)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-8b5cf6)](#install)
[![License: MIT](https://img.shields.io/badge/license-MIT-8b5cf6)](https://github.com/codejunction/shelldeck/blob/main/LICENSE)

[Install](#install) · [Features](#features) · [CLI](#cli) · [Remote access](#remote-access) · [Security](#security) · [Contributing](https://github.com/codejunction/shelldeck/blob/main/CONTRIBUTING.md)

<img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/screenshot.png" alt="shelldeck with four named terminals across projects: a git log, Claude Code waiting on you, Codex running a smart recall, and agent status per terminal" width="100%">

</div>

## Install

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/codejunction/shelldeck/main/install.ps1 | iex
```

**Linux** (macOS should work too, but is untested):

```sh
curl -fsSL https://raw.githubusercontent.com/codejunction/shelldeck/main/install.sh | sh
```

The installer sets up [uv](https://docs.astral.sh/uv/) if you don't have it, then installs shelldeck with its own Python 3.12+. Run it again to update to the latest release (or `uv tool upgrade shelldeck`); stop the server first with `sd stop` so Windows can replace `sd.exe`. Already use Python tooling? Any of these work too:

```sh
uv tool install shelldeck      # or: pipx install shelldeck, or: pip install shelldeck
uvx shelldeck                  # try it without installing
```

To try a release candidate, pin it: `uv tool install --force shelldeck==0.0.5rc3` (or `pip install shelldeck==0.0.5rc3`). Go back to the stable release with `uv tool install --force shelldeck`.

Requirements: Windows 10 1809+ (for ConPTY) or Linux, and a modern browser.

## Quick start

```sh
sd                 # start shelldeck in the background and open it in your browser
sd ~/code/api      # add a folder as a project and open a terminal in it
```

`sd` is short for `shelldeck`. The first visit asks you to create a password. After that, every terminal keeps running in the background until you close it, even when the browser tab is closed.

## Features

### Terminals that feel like editor panes

- **Real shells, detected per OS.** On Windows: `pwsh` (default), Windows PowerShell, cmd, Git Bash and WSL (pick a distro). On Linux: every shell in `/etc/shells`, defaulting to your `$SHELL`.
- **Tiled layout.** Split right or down, drag titles onto edges to split or onto a pane to swap, drag gutters to resize, double-click a title to maximize.
- **Free layout.** Every terminal becomes a floating window on a scrolling canvas. **Tile all** puts them back in a grid.
- **Nothing gets lost.** Closing the tab keeps terminals running, and reopening replays their output. After a restart or reboot, each terminal reopens in its last folder with its history above a "restored" marker.
- **Replay the last command** with `Ctrl+Alt+P` or the pane's replay button (refused while a command runs, or when an AI agent owns the terminal).
- **Run in the background.** The pane's eye button (`Ctrl+Alt+W`) takes a terminal off the screen while it keeps running; it stays in the sidebar, marked *running* while a command is busy, and a click brings it back. Closing a terminal with a running command asks whether to keep it in the background instead.
- **Shell integration** for pwsh, PowerShell, cmd, bash, zsh and fish. Each pane's title shows the last command (red if it failed), `Ctrl+Shift+Up`/`Down` jumps between prompts, and your own prompt (oh-my-posh, starship, …) still loads.

### Projects first

- **Projects sidebar.** Add folders with the built-in folder browser, then drag to reorder. Each project gets its own color on its sidebar dot, pane border and a subtle background tint.
- **Git changes at a glance.** A git project with uncommitted work shows a `±N` chip in the sidebar. Click it (or *Git changes* in the palette) to see the branch, ahead/behind, and every changed file with its added/removed lines; click a file for its diff.
- **Git graph.** The branch icon on every git project also opens its full commit graph, with branches, merges and tags drawn as colored lanes. Click a branch or tag, or right-click a commit, to check it out.
- **Clickable paths.** `src/app.py:12:5`, `C:\x\y.ts(3,4)` and Python tracebacks become links that open in VS Code at that line, or in the built-in editor.
- **Built-in editor and viewer.** `sd edit FILE` and `sd view FILE` open a file in shelldeck: line numbers, `Ctrl+S` to save (CRLF and BOM kept, and it won't overwrite a file that changed on disk without asking), rendered markdown and images. *Open file…* in the command palette does the same.
- **Open in editor.** A project's menu opens its folder in VS Code (or the file manager).
- **Port detection.** Start a dev server and a chip with its port appears on the pane. Click it to open the page.

### Built for long sessions

- **Command palette** (`Ctrl+Shift+P`) for terminals, bookmarks, layouts and themes.
- **Command history** with exit codes, durations and folders. Search it and re-run anything.
- **Search every terminal's output** (`Ctrl+Shift+F`), including terminals that aren't on screen.
- **Finished-command alerts.** When a long command finishes while you're looking elsewhere, you get a toast, or a desktop notification if you enable them.
- **Bookmarks** for commands you run often, global or per project.
- **Scheduler** for cron jobs that run in a project folder, with run history and logs.
- **Task board** with due dates and reminder alarms.
- **Task manager** with live CPU, RAM and GPU (NVIDIA) usage for the machine and for each terminal's process tree.
- **Radio.** The radio button in the top bar plays [cliamp](https://cliamp.stream) radio's channels (Lofi, Synthwave, Chiptunes, NCS...). Your browser fetches the list from `radio.cliamp.stream` and plays the stream itself, only after you pick a channel. The menu has a volume slider (remembered per browser).
- **Scratchpad** for quick markdown notes that belong to no project, with a preview, saved as you type. Agents (and scripts) add to it with `sd notes`.
- **Machines.** The picker above *New terminal* (on Windows and Linux alike) switches the sidebar between **Local** and your SSH machines; projects, terminals and the AI agents list follow it. *Add machine…* asks for host, user (plain, or LDAP/NTID like `CORP\jdoe` or `jdoe@corp.com`), port and an optional key file, with **Test connection** (network, then a real login with your key/agent, or with the password you type in the dialog for that test; it is never saved). Each machine starts with a project for its root folder `/`; the folder button adds more (`~/code/app`, `/srv/app`). Its terminals run `ssh -t ... 'cd <folder> && exec $SHELL -l'`, so a password is typed in the terminal and never stored. SSH machines are Linux targets (from Windows it uses the built-in OpenSSH client); Windows targets are RDP desktops, listed in the same picker and opened in mstsc. Agents inside an SSH session aren't detected yet.
- **Remote systems.** Save Linux servers (SSH) and Windows machines (RDP) under *More › Remote systems*. *Connect* opens an SSH host as a shelldeck terminal (exit ssh and you're back in a local shell), or a desktop in `mstsc` on Windows (xfreerdp or Remmina on Linux) on the host machine. Passwords are never stored: ssh asks in the terminal (keys and ssh-agent work), the RDP client asks in its window.

### AI agents, working as a team

- **AI agents.** A terminal running Claude Code, Codex, Devin CLI, Gemini CLI, Copilot CLI, Cursor Agent, opencode, Aider, Amp, Qwen Code, Goose, Droid, Crush or Kiro gets an **AI** chip in its header with the tool and model. The AI agents page lists the running agents and every known agent CLI with its models (Codex and opencode read their own model caches), and launches one with a chosen model.
- **Needs-you alerts.** Only when an agent actually asks something (a permission prompt or a question of Claude Code, Codex or Devin, or a `[y/n]`), its chip turns amber (*NEEDS YOU*), a chime plays and you get a toast, or a desktop notification when shelldeck is in the background. An agent that simply finished stays quiet, and nothing plays for the terminal you are looking at.
- **Context window.** Claude Code, Codex and Devin show how full their context window is: a percentage on the pane chip and a *CTX* meter in the top bar for the focused terminal. It is read from each agent's own session logs. Claude's window size is estimated from the model.
- **Tasks to agents.** Open a task and click *Send to agent* to type it into a running agent. The task moves to In progress.
- **Devin, everywhere.** Devin's model list comes from its own cache, so it matches what `devin --model` accepts. The model of a running Devin comes from `--model`, `DEVIN_MODEL` or the resumed session. The AI agents page also lists agents running outside shelldeck (the Devin desktop app, other terminal windows) and every Devin session from Devin's session store, with a *Resume* button that opens a terminal in the session's folder and runs `devin -r <id>`.
- **Every terminal has a name.** Each terminal gets a person's name (Maya, Kofi, …) shown in its header and the sidebar, so you and your agents can say `sd peek Maya` instead of an id.
- **Agents that talk to each other.** From inside a terminal, `sd agents` lists the other agents in the project, `sd peek` reads another terminal's screen and `sd tell` types a message into it, tagged with the sender so it can reply. *Copy team prompt* on the AI agents page gives you text to paste into each agent so it knows how.
- **Sub-agents and hand-offs.** `sd spawn "task" --model small|medium|large` opens a new terminal in the project with a sub-agent (Claude Code, Codex, Devin, Gemini, Qwen or opencode) already working on the task, picking a cheaper or stronger model by how hard the task is (`auto` guesses). It opens next to yours without taking the keyboard. `sd handoff Maya "task"` gives a task to an agent that is already running. The receiver runs `sd done <id> "summary"`, and the sender gets the summary typed in (when an agent runs there) plus a toast and chime. Every hand-off is written to the project's `.shelldeck/handoff.md` (git-ignored) and listed on the AI agents page. Sub-agents can't spawn more agents, and a terminal that closes mid-task marks its hand-off as exited.
- **The parent is the control center.** A sub-agent never alerts you. When one stops on a question or permission prompt, shelldeck types the question into its parent agent's terminal; the parent decides and answers with `sd answer Maya 1` (keys for the menu) or `sd tell`. Only when the parent is a plain shell does the question come to you. Once a sub-agent's work is done, the parent asks you and closes it with `sd close Maya`, or use *Close* on the hand-off toast.
- **One status, owned by the server.** Each agent terminal is `idle`, `working`, `blocked` (with a reason such as approval or question), `done` or `exited`. Every tab and the CLI see the same value, and the AI agents page shows where it came from: an integration report or screen/activity detection. `sd agent status` prints it. `sd agent wait Maya --until idle --timeout 10m` blocks until the agent gets there; it exits 2 on timeout and 3 if the agent exits or a different agent takes the terminal. `sd integration install` adds a lifecycle hook to **Claude Code, Codex, Gemini CLI and Devin CLI**, so they report working, needs-you and done exactly instead of being read off the screen, and can be resumed. Fifteen more agents (Cursor, Copilot, OpenCode, Kimi, Qwen and others) have preview integrations, installed with `sd integration install <agent>` or `--all` and to be finished later. Other hooks in those configs are kept, and `sd integration uninstall` removes only shelldeck's (see [docs/agent-integrations.md](docs/agent-integrations.md)). Other tools can report with `sd agent report` ([docs/agent-automation.md](docs/agent-automation.md)).
- **What needs you, first.** Each project in the sidebar shows its most urgent agent state as text: *needs you*, then *working*, then *done*. The AI agents page sorts agents the same way, shows how long each has been in its state, and *Open next* jumps to the next agent waiting on you, in any project. A finished agent stays *done* until you open its terminal.
- **Resume after a restart.** shelldeck remembers each terminal's agent session (from an integration or the agent's own logs: Claude Code, Codex, OpenCode). After a restart, *Resumable sessions* on the AI agents page or `sd agent resume Maya` starts it again in its terminal. The setting *Resume agent sessions after a restart* is `ask` by default; `auto` does it when the terminal opens, but never into an open hand-off, a missing folder or a missing CLI. Only stored argv of plain words is typed, never anything taken from the screen.
- **What happened, recorded for free.** Each agent's commands (with success or failure), test runs and edited files come in through its integration hooks, and your own shell commands through shell integration. They become the project's recent events and its *Tests* and *Last error* lines. Each agent run ends with `.shelldeck/sessions/<id>.md`: agent, times, files changed, commands, tests, errors, git. No model is involved and no tokens are spent. Command output, file contents and `.env`-style files are never recorded, and secrets are redacted.
- **Facts come from the agents.** No model runs inside shelldeck: agents record what they learn with `sd remember`, `sd decide` and `sd task update`. *Extract facts* in an agent's *Actions* menu on the AI agents page (or `sd agent extract Maya`) asks an agent to do it now, in one short turn. It never runs on its own, so it costs tokens only when you ask.
- **Smart search without embeddings.** `sd recall auth --smart` (or *smart* on the Context page, or the *Smart search* setting) asks an installed agent's smallest model for related keywords (authentication, oauth, jwt, session...) and searches with those too. It uses the agent's own non-interactive mode: `claude -p --model haiku`, `codex exec`, `gemini -p -m flash-lite` or `devin -p`. Only the query is sent, never your stored memory, and answers are cached, so each new query costs a few tokens once. If no agent is available, search uses keywords only.
- **Context page.** The sidebar's *Context* view reads like a status page: *Working on* (task, next step, tests passing or failing, last error, git; *Edit* when you need it), *Facts* ("may be out of date" when a source file changed; check or remove them on hover), *Decisions*, and *Recent activity* in plain words. The search box can widen a search with an installed agent and a model you choose (its cheapest by default).
- **`?` menu and Ask.** Type `?` on an empty shell prompt and a menu opens right at the cursor: shelldeck's commands and bookmarks, filtered as you type. Type a question instead ("top 5 processes by memory") and pick *Ask*: an installed agent's model (Settings: *Ask agent* and *Ask model*, default the first installed agent's smallest) writes one command for that terminal's shell and OS (PowerShell, cmd, Git Bash, WSL bash, zsh...). The menu stays open with a spinner until the command appears in it; Enter types it at the prompt without running it, Shift+Enter runs it, and editing the question asks again. `Ctrl+I` opens just the Ask box, like Devin Desktop's terminal Command. `?` and `Ctrl+I` only open the menu at a shell prompt with shell integration and no agent running, so vim, less, ssh and agents still get the key. `sd ask "..."` prints the command instead.
- **Context that outlives the agent.** Each project keeps a task, its current state and next action, memory (facts and discoveries), decisions and recent events in a local database (`context.db` next to the shelldeck config). The same context is written as Markdown to `.shelldeck/STATE.md`, `TASK.md`, `MEMORY.md` and `DECISIONS.md`. A new agent runs `sd context` and continues where the last one stopped. `sd recall "auth architecture"` searches knowledge from every project, with its source project and files. Secrets are redacted before anything is stored, `.env`/key files are never referenced, and knowledge whose source files changed is marked stale. Hand-off files include a context snapshot. All of it stays on this machine.
- **Agents know the commands.** On start, shelldeck installs a `shelldeck` skill for every agent CLI on PATH: a skill for Claude Code, Codex and Devin, and a marked block in the global instructions file of Gemini, opencode, Qwen, Amp, Droid, Copilot, Crush, Goose and Kiro. `sd install-skill --remove` takes it out and stops the reinstall; the AI agents page has an *Install skill* button per agent.

<img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/agents.png" alt="AI agents page: who needs you with Open next, each agent's live status and source, the hook integrations, and the available agent CLIs" width="100%">

<img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/context.png" alt="Context page: what the project is working on, its facts, decisions and recent agent activity" width="100%">

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/git-graph.png" alt="Git graph popup with branches, merges and tags"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/codejunction/shelldeck/main/docs/assets/task-manager.png" alt="Task manager with CPU, memory, GPU, AI agents and ports per terminal"></td>
  </tr>
  <tr>
    <td align="center"><sub>Git graph with one-click checkout</sub></td>
    <td align="center"><sub>Task manager, per terminal</sub></td>
  </tr>
</table>

### Plugins

A plugin is a Python package that adds commands. Its commands show up in the `?` menu (`?docker ps`, `?docker logs web`) and in the CLI (`sd docker ps`). A command either returns text, which shelldeck shows, or a command line, which shelldeck types at the prompt without pressing Enter. Plugins can also react to shelldeck's events (`agent.*`, `handoff.*`, ...). Ask knows the installed plugin commands too: "follow the logs of the web container" can come back as the plugin's `docker logs web`.

```python
# pyproject.toml: [project.entry-points."shelldeck.plugins"]  docker = "shelldeck_docker:plugin"
from shelldeck.plugins import Plugin

plugin = Plugin("docker", "0.1.0")

@plugin.command("logs", "Follow a container's logs", usage="<container>")
def logs(args, ctx):  # ctx: session_id, cwd, shell, project
    return {"input": "docker logs -f " + " ".join(args)}
```

Install a plugin into shelldeck's environment, for example `uv tool install shelldeck --with ./examples/shelldeck-docker` (the reference plugin), then restart shelldeck. The *Plugins* page (sidebar, *More*) shows each plugin's status (or why it failed to load), its commands with a *Run* button and the events it listens to, and turns plugins on or off; `sd plugin list` and `sd plugin disable NAME` do the same from the CLI. Plugins run inside the server with your permissions, like any package you install, so only install ones you trust. [Writing a plugin](docs/plugins.md) covers the API, arguments (`usage="<container>"`), results, events and troubleshooting.

### Secure by default

- **Password required.** You create it once; upgrades and reinstalls keep it (only `sd reset-password` removes it). Every browser logs in separately and locks after 30 minutes idle.
- **One device at a time.** Any number of browsers can stay signed in, but only one uses shelldeck; logging in on another takes over and idles the rest. The **Devices** page lists every signed-in browser (where from, last active, in use or idle) and revokes any of them.
- **Local only.** shelldeck listens on `127.0.0.1` and refuses requests from other websites.
- **Remote use** goes over an SSH tunnel or HTTPS. See [Remote access](#remote-access).

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
| Default shell | Windows: pwsh, powershell, cmd, gitbash, wsl · Linux: shells from `/etc/shells` | pwsh / `$SHELL` |
| WSL distribution | any installed distro, or the system default | system default |
| Theme | dark, light, system | dark |
| Terminal font size | 8–32 | 13 |
| Pane layout | tiled, free | tiled |
| Project colors | tint on, off | on |
| Terminal colors | default (follows theme), Dracula, One Dark, Nord, Gruvbox Dark, Solarized Dark, Solarized Light, GitHub Light | default |
| Terminal font | any installed monospace font; empty uses Cascadia / Nerd Font | empty |
| Open file paths with | VS Code (at the line), shelldeck's built-in editor, system default app | VS Code |
| Resume agent sessions after a restart | Ask, automatically, never | Ask |
| Smart search | off, first installed agent, Claude Code (haiku), Codex, Gemini CLI (flash-lite), Devin CLI | off |
| Password | created on first visit, changed here (current + new) | required |

</details>

## CLI

```text
sd [--port N] [--no-window] [--app]  start the server if needed, print the banner and open the browser
sd web                               same as plain `sd`
sd PATH                              shorthand for `sd open PATH`
sd open [PATH] [--shell wsl]         add PATH as a project and open a terminal in it
sd list                              terminals grouped by project
sd info                              details of the shelldeck terminal you're in
sd agents [--all]                    AI agents running in this project's terminals (--all: every project, agents outside shelldeck, installed CLIs)
sd integration list [--json]         built-in agent integration coverage and lifecycle/resume capabilities
sd peek TERMINAL [-n 40]             last lines of another terminal (its name like Maya, id, id prefix or title)
sd tell TERMINAL "MESSAGE" [--raw]   type a message into another terminal and press Enter
sd spawn "TASK" [--agent A] [--model small|medium|large|auto|NAME]   sub-agent in a new terminal, working on TASK
sd handoff TERMINAL "TASK"           hand a task to an agent already running in another terminal
sd done ID ["SUMMARY"] [--failed]    close a hand-off you were given; the sender is told
sd answer TERMINAL KEY...            press keys in another terminal, e.g. answer a sub-agent's prompt: 1, y enter, esc
sd close TERMINAL [--force]          close a terminal; from an agent only its own sub-agents (after you agree)
sd handoffs [--all]                  hand-offs in this project and their status
sd integration list|detect|status|install|uninstall [AGENT] [--all]   hooks for claude, codex, gemini, devin (+ 15 previews with --all)
sd agent status [TERMINAL] [--json]  lifecycle state (idle/working/blocked/done/exited) and its source
sd agent wait TERMINAL --until STATE [--timeout 10m]   block until an agent reaches a state
sd agent start AGENT ["PROMPT"] [-m MODEL] [-p PROJECT]   new terminal running an agent with its native flags
sd agent prompt TERMINAL "TEXT" [--wait] [--until done] [--timeout 10m]   send a prompt; refused while it's blocked
sd agent rename TERMINAL NAME        rename an agent's terminal
sd events subscribe [--types agent,handoff] [--since ID]   JSON lines: agent.*, handoff.*, integration.changed
sd agent cmd TERMINAL ACTION [ARG]   the agent's own slash command: compact -> /compact (Claude, Codex), /compress (Gemini)
sd agent explain TERMINAL [--json]   why an agent has its state: deciding source, report ages, screen inputs (no screen text)
sd agent explain --file SCREEN.txt [--agent A]   check saved screen text against the detection rules (docs/agent-detection.md)
sd agent resume [TERMINAL]           list stored agent sessions, or start one again (claude --resume, codex resume, opencode --session)
sd agent report STATE --source S --agent A   report state from an integration inside a terminal
sd init [PATH] [--task TEXT]         set up a project for agents now: add it to shelldeck, write .shelldeck/ (STATE, TASK, MEMORY, DECISIONS, AGENTS.md)
sd context [PROJECT] [-q QUERY]      task, state, next action, memory, decisions, hand-off, git, events
sd resume                            this project's unfinished task and where to pick it up
sd plugin list | enable NAME | disable NAME          installed plugins; `sd <plugin> <command> [ARGS]` runs one
sd ask "QUESTION" [--agent A [--model M]]          print one command for this terminal's shell and OS (never run)
sd recall QUERY [--smart | --agent A [--model M]]   search every project; an agent's model can add related keywords first
sd memory [search QUERY]             this project's memory and decisions
sd remember FACT [FACT...] / sd discover FINDING [--type T] [--file F]   add to project memory
sd agent extract TERMINAL            ask that agent to save what it learned this session (one short prompt)
sd decide TITLE [-r REASON]          record a settled decision (sd decisions lists them)
sd task update [STATUS] [--task --step --next --tests --error]   update the active task and state
sd knowledge verify ID [--status S]  mark knowledge VERIFIED, REVIEWED, STALE or INVALIDATED
sd switch AGENT [--note TEXT]        checkpoint this project's task and continue it with another agent in a new terminal
sd projects / sd project NAME / sd relate PROJECT   known projects, one project, link related projects
sd install-skill [AGENT...] [--remove]   teach agent CLIs the sd team commands (automatic on server start)
sd notes list|add|append|show|replace|delete   Scratchpad notes ("- bullets" are fine; - reads stdin)
sd top [--json]                      Task manager: machine CPU/RAM/GPU and each terminal's usage
sd ps [TERMINAL] [--json]            processes running in a terminal (default: this one)
sd kill TERMINAL PID [--force]       end a process inside a terminal, and its children
sd edit FILE / sd view FILE          open a file in shelldeck's editor / viewer
sd search QUERY [--root DIR]         LLM-free code search with ranked, highlighted snippets
sd render FILE                       pretty-print code or markdown
sd schedule list|add|run|toggle|delete|logs
sd task list|add|move|delete|alarms
sd serve [--host H]                  run the server in the foreground
sd stop                              stop the background server (closes all terminals)
sd share [stop] [--new-link]         share over an HTTPS Cloudflare tunnel: one-use QR link, host approval (needs cloudflared)
sd login-link                        emergency: one-time login URL (host only)
sd reset-password                    emergency: forget the password (host only)
```

- **Startup banner.** `sd` prints the version and the Local and Network URLs. When you start it from Win+R, the Start menu or a shortcut, its window stays open until you press Enter.
- **Port.** The default port is `5455`. Change it with `--port` or `SHELLDECK_PORT`.
- **Browser.** `--app` opens a chromeless Edge/Chrome window instead of a browser tab.
- **Inside terminals.** Inside a shelldeck terminal, `SHELLDECK_SESSION_ID`, `SHELLDECK_NICK` (its name) and `SHELLDECK_PORT` are set, plus `SHELLDECK_AGENT_REPORT_TOKEN` (a per-terminal secret for integration reports), and `SHELLDECK_PARENT` in a sub-agent's terminal.
- **Agent permissions.** Claude Code asks before running `sd done` and friends. To let sub-agents report back on their own, allow `Bash(sd:*)` in your Claude Code permissions.
- **Code search.** `sd search` builds a persistent index and returns ranked snippets with confidence scores. Useful options: `--ext py,ts`, `--glob "src/*"`, `--top N`, `--format text|json|paths` and `--reindex`.

## Remote access

Run shelldeck on a server or VM and use it from your laptop.

**SSH tunnel (recommended).** Nothing is exposed to the network.

```sh
sd serve                                  # on the VM: stays on 127.0.0.1:5455
ssh -N -L 5455:127.0.0.1:5455 you@vm      # on your machine, then open http://127.0.0.1:5455
```

**`sd share` (any device, no setup).** Needs [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) (`winget install Cloudflare.cloudflared`); no Cloudflare account.

```sh
sd share              # starts shelldeck if needed, prints a QR code and a link; Ctrl+C stops sharing
sd share --new-link   # another one-use link for the running share (a second device)
sd share stop         # end the running share
```

- **Encrypted in transit, not end to end.** The other device talks HTTPS to Cloudflare, which relays it through cloudflared's outbound encrypted tunnel to shelldeck on `127.0.0.1`. No ports are opened. Cloudflare ends the TLS connection, so it can see the traffic (terminal input and output, your password as you log in). For sensitive work use the SSH tunnel or Tailscale instead.
- **Three locks.** The random `trycloudflare.com` address alone is refused. The printed link works **once** and only for **10 minutes**; opening it makes the device wait until you allow it in a shelldeck browser on this machine (toast, Share dialog or Devices) or in the `sd share` window (`Allow it? [y/N]`). Then it still needs your password.
- **Your consent.** The first share shows the terms of sharing and needs you to accept them: you share at your own risk, keep links and QR codes private, and don't share from corporate or other restricted networks. The acceptance is kept in `share-consent` in the config folder (like the password) and asked again only when the terms change.
- **Long password.** Sharing needs a password of 12+ characters. An older password that long counts after your next login.
- **From the browser.** The **Share** button in the top bar does the same as `sd share` (only on this machine): it starts the tunnel, shows the QR code, and makes new links. A **Sharing** pill stays in the top bar while a share runs.
- **Phones.** On touch screens a key bar adds Esc, Tab, Ctrl (applies to the next letter), arrows and `| ~ / -`; dialogs open as bottom sheets.
- **Stopping.** The server runs the tunnel, so a share keeps going until you stop it: `sd share stop`, *Stop sharing* in the Share dialog, or Ctrl+C in the `sd share` that started it. Stopping closes the tunnel, voids the link and signs out every browser that logged in through it (they also vanish from **Devices**; a server restart clears them too). Revoking a shared device in **Devices** also voids its approval, so it needs a new link. Each `sd share` gets a new address and link.

**HTTPS on the VM's address,** with a real certificate (Tailscale `tailscale cert`, Let's Encrypt, your reverse proxy) or a self-signed one:

```sh
sd serve --host 0.0.0.0 --cert cert.pem --key key.pem
```

- **No plain HTTP.** shelldeck refuses plain HTTP on a non-local address, because passwords and terminal traffic would cross the network unencrypted. `--insecure-http` overrides that, for trusted networks only.
- **First visit.** A first visit from another machine also asks for a **setup code**, which `sd serve` prints on the server.
- **Dropped connections** (sleep, Wi-Fi change, VPN) reconnect on their own. The terminal redraws from the server's scrollback.

## Security

- **Local only.** The server binds to `127.0.0.1` and rejects HTTP and WebSocket requests whose `Host` or `Origin` isn't the app itself, so other websites can't reach your shells.
- **Passwords** are stored as PBKDF2-SHA256 (600k iterations). A login is a random token in an `HttpOnly`, `SameSite=Strict` cookie, and only its SHA-256 is stored. After 5 wrong passwords, each further try waits longer.
- **Devices.** Each login records where it came from (this machine, the network, or a share). Only one login is in use at a time; the others get `423` until they enter the password again. Revoke any login from the Devices page.
- **Headers.** Every response forbids framing (`X-Frame-Options: DENY`, `frame-ancestors 'none'`), sniffing and referrers, and API responses are never cached.
- **Sharing** adds a one-use, 10-minute link, approval on the host and a 12+ character password; see [Remote access](#remote-access).
- **The CLI** authenticates with a token file in the data folder, readable only by your account and rotated on every server start.
- **Forgot the password?** On the host machine, `sd login-link` prints a one-time login URL valid for 5 minutes, and `sd reset-password` removes the password. There is deliberately no way to do either from the browser.
- **Your data** lives in `~/.config/shelldeck/` (database, log and saved terminal history). Set `SHELLDECK_HOME` to move it.

Found a vulnerability? Please report it privately; see [SECURITY.md](https://github.com/codejunction/shelldeck/blob/main/SECURITY.md).

## Development

```sh
git clone https://github.com/codejunction/shelldeck && cd shelldeck
uv sync
uv run shelldeck --port 5466 serve     # dev server, http://127.0.0.1:5466
uv run pytest -q                       # includes a real PTY round trip (ConPTY or POSIX pty)
uv run ruff check
```

- **Stack.** FastAPI and uvicorn, `pywinpty` (ConPTY) on Windows or the stdlib `pty` elsewhere, and SQLite.
- **Frontend.** Plain HTML, CSS and JavaScript with vendored xterm.js, and no build step.
- **Contributing.** See [CONTRIBUTING.md](https://github.com/codejunction/shelldeck/blob/main/CONTRIBUTING.md) for the workflow and release steps.

## License

[MIT](https://github.com/codejunction/shelldeck/blob/main/LICENSE). xterm.js is MIT-licensed too (see `shelldeck/static/vendor/LICENSE-xterm.txt`).
