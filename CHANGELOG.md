# Changelog

All notable changes to shelldeck are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

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

[0.0.1]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.1
