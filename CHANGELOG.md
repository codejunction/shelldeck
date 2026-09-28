# Changelog

All notable changes to shelldeck are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

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

[0.0.3]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.3
[0.0.2]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.2
[0.0.1]: https://github.com/codejunction/shelldeck/releases/tag/v0.0.1
