# Changelog

All notable changes to shelldeck are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [0.0.5] - 2026-10-01

### Added

- AI agents: a terminal running Claude Code, Codex, Devin CLI, Gemini CLI, Copilot CLI, Cursor Agent, opencode, Aider, Amp, Qwen Code, Goose, Droid, Crush or Kiro gets an **AI** chip with the tool and model. The AI agents page lists running agents and every known agent CLI with its models, and launches one with a chosen model.
- Needs-you alerts: when an agent asks a question or a permission prompt, its chip turns amber, a chime plays and you get a toast (or a desktop notification in the background). An agent that just finished stays quiet.
- Context window: a percentage on the agent's chip and a *CTX* meter in the top bar, read from the agent's own session logs (Claude Code, Codex, Devin).
- Devin: models from Devin's own cache, the running model from `--model`, `DEVIN_MODEL` or the resumed session, agents running outside shelldeck, and Devin's sessions with a *Resume* button.
- Every terminal has a name (Maya, Kofi, …) in its header and the sidebar.
- `sd agents`, `sd peek`, `sd tell`: agents in a project see and message each other. *Copy team prompt* on the AI agents page.
- `sd spawn "task" --model small|medium|large|auto`: a sub-agent in a new terminal, already working on the task. `sd handoff NAME "task"` gives a task to a running agent; `sd done ID "summary"` reports back to the sender. Hand-offs are listed on the AI agents page and in `.shelldeck/handoff.md`.
- Sub-agent questions go to the parent agent, not to you; the parent answers with `sd answer NAME KEYS` and closes finished sub-agents with `sd close NAME` (or *Close* on the hand-off toast).
- `sd install-skill`: shelldeck teaches every agent CLI on PATH the `sd` team commands on start (`--remove` undoes it).
- *Send to agent* on a task types it into a running agent.
- Built-in editor and viewer: `sd edit FILE`, `sd view FILE`, *Open file…* in the palette, and clickable paths when the `editor` setting is `shelldeck`. Markdown preview, images, `Ctrl+S`, and no silent overwrite of a file changed on disk.
- Scratchpad: markdown notes that belong to no project, saved as you type.
- Release candidates can be published to PyPI from a feature branch (manual `release.yml` run).

### Fixed

- Upgrades and reinstalls no longer ask for a new password: it is kept in its own file next to the database.

## [0.0.4] - 2026-09-29

### Added

- Share approval: opening a share link puts the device on a waiting page until you allow it in the `sd share` window (`Allow it? [y/N]`); then it still needs the password.
- `sd share --new-link` prints another one-use link for the running share.
- Security headers on every response (no framing, no MIME sniffing, no referrer) and `Cache-Control: no-store` on the API.

### Changed

- Share links work once and expire after 10 minutes unopened. A new link voids the unused old one.
- Sharing needs a password of 12+ characters (an existing one that long counts after the next login).
- Revoking a shared device in Devices also voids its approval, so it needs a new link.
- README: `sd share` is encrypted in transit but not end to end; Cloudflare can see the traffic.

### Fixed

- A share left open when `sd share` was killed or its window closed: the server now closes a share within a minute of its last heartbeat and signs its browsers out. Devices no longer shows "sharing via" a dead tunnel.

## [0.0.3] - 2026-09-29

### Added

- Devices page: every signed-in browser with where it came from (this machine, network, or a share), when it signed in, last activity, and whether it is in use; revoke one or sign out all others.
- One device at a time: other browsers stay signed in but idle; logging in on one takes over and shows the others an "In use on another device" screen.

### Changed

- Logging in again in the same browser replaces its old login instead of adding one.
- Share logins are stored with their share and removed when it stops, is replaced, or the server restarts.

### Fixed

- Re-running the one-line installer now upgrades an existing install to the latest release.

## [0.0.2] - 2026-09-29

### Added

- `sd share`: reach shelldeck from another device over an HTTPS Cloudflare quick tunnel (needs `cloudflared`, no account). It prints a QR code and a one-per-share link; the tunnel address is refused without that link, and your password is still required. Ctrl+C stops sharing and signs those browsers out.
- Phone key bar on touch screens: Esc, Tab, a sticky Ctrl, arrows and `| ~ / -`.

### Changed

- Touch devices use xterm's DOM renderer (WebGL came up blank on high-DPI phones).
- History rows and the Settings dialog stack into one column on narrow screens.
- Requests through a proxy that sets `CF-Connecting-IP` count as remote.
- CI actions updated: checkout v7, setup-uv v7, upload-artifact v7, download-artifact v8.

## [0.0.1] - 2026-09-28

First public release.

### Added

- Projects sidebar with real shells per project: pwsh, PowerShell, cmd, Git Bash and WSL on Windows; every shell in `/etc/shells` on Linux.
- Tiled split layout and a free-floating window layout, both persisted.
- Shell integration (prompts, exit codes, current folder) for pwsh, PowerShell, cmd, bash, zsh and fish.
- Session restore across restarts, reconnect with scrollback replay, and search across every terminal's output.
- Git graph per project with branch and commit checkout.
- Command history, bookmarks, cron scheduler, task board with reminders, and a task manager with CPU, RAM and GPU usage.
- Clickable file paths, listening-port chips, finished-command alerts and a command palette.
- Mandatory password with per-browser logins, idle lock, and HTTPS or SSH-tunnel remote access.
- `sd` CLI with a startup banner, `sd search` (LLM-free code search), and one-line installers for Windows and Linux.

[0.0.5]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.5
[0.0.4]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.4
[0.0.3]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.3
[0.0.2]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.2
[0.0.1]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.1
