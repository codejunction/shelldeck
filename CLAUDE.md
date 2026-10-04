# shelldeck

Project-first multi-terminal for Windows and Linux (formerly termy). FastAPI backend, real shells via ConPTY (`pywinpty`), SQLite persistence, and a plain HTML/CSS/JS frontend opened in the default browser. Published to PyPI as `shelldeck`, with CLI aliases `shelldeck` and `sd`. Repo `github.com/codejunction/shelldeck`, main branch `main` (fresh history from v0.0.1; the old `termy` repo is archived).

## Commands

```sh
uv sync
uv run shelldeck serve              # foreground server, http://127.0.0.1:5455
uv run shelldeck                    # background server + banner + browser tab (`sd web` is the same)
uv run pytest -q                    # includes a real PTY round trip (ConPTY or POSIX pty)
uv run ruff check                   # rules live in pyproject ([tool.ruff.lint])
uv build
uv tool install --force -e .        # global sd/shelldeck from this checkout (the user tests this way)
```

- Set `SHELLDECK_HOME=<dir>` to keep dev data out of `~/.config/shelldeck` (`.devhome/` is gitignored).
- Run dev servers on port 5466, so they don't collide with the user's real server on 5455.

## Product spec (decided)

- **Scope.** Windows and Linux; macOS should work through the POSIX backend but is untested. No MCP. The AI assistant, broadcast, pipe and mindmap were removed on purpose; don't re-add them unasked. The user has separate AI plans.
- **Shells.** Detected per OS in `shells.py`:
  - **Windows:** `pwsh` (default), `powershell`, `cmd`, `gitbash`, `wsl` (+ distro).
  - **Linux:** the entries in `/etc/shells` (skipping rbash/tmux/screen), with `$SHELL` as the default.
  - Always call `shells.kinds()` / `default_kind()`; never hardcode kinds. A stored default that's invalid on this OS falls back in `get_settings()`.
- **Projects.**
  - Added from the UI through the folder browser (`/api/fs/dirs`).
  - Each gets a palette slot (`projects.color`, 0–7, least-used on add) for the sidebar dot, the pane top line, and the terminal background tint.
  - Sidebar order lives in localStorage.
- **Layouts:**
  - **tiled** (default): a split tree. New terminals split the largest pane (sideways only if both halves stay ≥380px, else stacked). Panes have a fixed default `min-height: var(--pane-h, 340px)` and `#layout` scrolls vertically only.
  - Opening terminals must not animate (user request); only chrome such as the sidebar, views and dialogs animates.
  - **free**: floating windows with drag, grip resize, a scrolling canvas, and Tile all.
- **Pane title.** Shows the last command (tracked from keystrokes, falling back to the wrap-joined prompt line in the xterm buffer), cut to 32 chars with "…".
- **Opening.** `sd` / `sd web` print a banner (ASCII logo in a violet gradient, version, Local and Network URLs; rich drops color on non-ANSI output and `NO_COLOR`) and open the default browser. Network is "off" unless the server binds a non-loopback host (server.json records `host`). Keep the banner ASCII-only: piped output on Windows is cp1252. When Windows opened a console just for `sd` (`_own_console()`: the topmost console-sharing ancestor is sd/shelldeck/python, not a shell), it waits for Enter so the window doesn't vanish.
- **Detached server.** `ensure_server` uses `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, never `DETACHED_PROCESS`: venv `python.exe` is a launcher, and its console-less child would open a visible terminal window.
- **App window (opt-in).** `--app` opens an Edge/Chrome `--app` window on the default profile. A fresh `--user-data-dir` triggers Edge sign-in prompts, so don't use one.
- **Visuals.**
  - T3 Code-like: zinc neutrals, violet accent, Inter/Segoe UI, Cascadia/Nerd Font terminal stack.
  - Monochrome logo: `static/icon.svg`, plus PNGs 32/192/512 and a web manifest.
  - Boot animation; motion throughout, disabled under `prefers-reduced-motion`.
- **Auth.** A password is mandatory: the first visit creates it (8+ chars). A remote first visit also needs the host's setup code. The hash lives in `<config>/password` (owner-only; also mirrored to the `auth_password_hash` setting for 0.0.4 and older, and migrated from it by `init_auth`), because a database write left in the WAL of a server that never exits cleanly got lost and reinstalls asked again. Only `reset()` removes it. Logins are per browser (cookie `sd_session`, sha256 in `auth_sessions`) and lock after 30 min idle; only non-GET requests and terminal input count as activity.
  - **One device at a time** (`server._active`, a session hash in memory): `_claim()` in the guard and both sockets lets a browser act only if it is the active one or no live one is (restarts and idle expiry free it). A login (`_login_response`) replaces this browser's old session, becomes active, and closes other sockets with 4423; idle browsers get `423 in_use` and a "Use here" lock screen (password). `/api/auth/*` stays exempt.
  - `auth_sessions.via` is `local` / `network` / `share:<host>`. Stopping or replacing a share deletes its rows (`_revoke`); `init_db` drops `share:%` rows on start. `/api/devices` lists live logins; `DELETE /api/devices/{hash|others}` revokes. UI: `static/devices.js` (sidebar Devices view).
  - The password changes only in Settings, with current + new + confirm, and the change signs out every other browser. There is no way to remove it.
  - The host CLI uses `cli-token` (the `X-Shelldeck-Token` header).
  - Emergency, host only: `sd login-link` and `sd reset-password`.
  - Share: the server runs cloudflared itself (`share.py`: `start`/`stop`, `CREATE_NO_WINDOW`). `POST /api/share` (host only, `_host()`: CLI token or a local browser not on the tunnel; `409 weak_password` unless `auth.strong_password()`, i.e. 12+ chars) starts it under `_share_lock` and returns ShareState (`_share_state()`: sharing, starting, url, link with `qr_svg`, pending, strong_password, cloudflared, terms). Host browsers get `share_state` over the alarm socket (`_broadcast(host_only=True)`); `static/share.js` is the Share dialog, the Sharing pill and Allow/Deny toasts.
    - **Link:** one unused link at a time (`_share["token"]`, kept so the host UI can show its QR), one use, `LINK_TTL` 10 min; `sd share --new-link` / *New link* → `POST /api/share/link`.
    - **Approval:** opening the link makes a pending grant (sha256 of a random `sd_share` cookie) and serves `_WAIT_PAGE`, which polls `/share/status`. Any non-tunnel browser (toast, Share dialog, Devices) or the `sd share` window (`Allow it? [y/N]`) answers via `POST /api/share/decide`; first answer wins. `_trusted()` refuses the tunnel host except `/share/*` unless the grant is `ok`; the password is still required.
    - **Lifetime:** no lease; a share runs until `DELETE /api/share` (`sd share stop`, *Stop sharing*, Ctrl+C in the `sd share` that started it), cloudflared exiting (`_tunnel_exited`) or server shutdown. Stopping signs tunnel logins out. Revoking a share login deletes its grant (`grant["session"]`).
    - **Terms:** `share.TERMS` / `TERMS_VERSION`; `POST /api/share` is `409 terms_required` until `POST /api/share/terms {accept:true}` (host only) writes `<config>/share-consent` (`auth.accept_terms`, owner-only like the password). Bump the version when the text changes in substance.
    - Tunnel requests are remote (`X-Forwarded-For`/`CF-Connecting-IP`). Cloudflare terminates TLS and sees plaintext; the README says so.
  - `guard` adds `SECURITY_HEADERS` (no framing, nosniff, no referrer) to every response and `Cache-Control: no-store` to `/api/*`.
  - `serve` refuses plain HTTP on non-loopback addresses without `--cert/--key` or `--insecure-http`.
  - The old seeded `nopassword` is deleted on start.
- **Bookmarks.** Insert by default; Shift+Enter or Shift+click runs.

## Settings (`/api/settings`, `SETTINGS_DEFAULTS` in server.py)

`default_shell` (any of `shells.kinds()`), `wsl_distro`, `font_size` (8–32), `theme` (dark|light|system), `layout_mode` (tiled|free), `project_tint` (on|off), `terminal_theme` (`TERMINAL_THEMES` in server.py, mirrored by `PRESETS` in app.js), `font_family` (free text, `FONT_FAMILY` regex), `editor` (vscode|shelldeck|system), `agent_resume` (never|ask|auto; `_resume_plan` validates stored argv against `SAFE_ARG`, `shutil.which` and the cwd, `_auto_resume` runs once per terminal per server run from `_attach` and skips open hand-offs). Validation lives in `write_settings`; adding a key means updating the defaults, the validation, `views.settingsDialog`, and the README table.

## Layout

- `shelldeck/server.py`: FastAPI app, containing:
  - Origin/Host guard middleware, auth, settings, `/api/shells`, projects, `/api/fs/dirs`, sessions (server-side names like "pwsh 2"), bookmarks, scheduler, tasks.
  - `/ws/{sid}` terminal socket, `/ws/alarms`, `/api/health`, `/api/shutdown`.
- `shelldeck/pty.py`: `PtyManager` (session id -> `Proc`) plus a per-session `Scrollback` (256 KB), replayed on reconnect.
  - `Proc` is `WinProc` (pywinpty ConPTY) on Windows and `PosixProc` elsewhere (stdlib `pty`, `start_new_session`, non-blocking master fd, incremental UTF-8 decode, `killpg` on close).
  - Both expose `read`, `write`, `resize`, `isalive` and `kill`.
- `shelldeck/shells.py`: shell kinds and their argv (interactive and one-shot), plus WSL distro listing (UTF-16 output). `with_integration()` adds shell integration per shell.
- `shelldeck/integration/`: shell integration scripts. They report `OSC 133;A/B/D;<exit>`, `633;E;<cmdline>` and `633;P;Cwd=<dir>`.
  - pwsh/powershell get `shelldeck.ps1` via `-EncodedCommand`, because execution policy blocks `.ps1` files.
  - cmd uses a `PROMPT` env var; bash uses `--rcfile bash.sh` (`SHELLDECK_LOGIN` emulates `--login`); zsh uses a `ZDOTDIR` shim; fish uses `-C source`. WSL has none.
- `shelldeck/db.py`: raw `sqlite3` with WAL. Schema lives in `init_db()`; new columns are added with `ALTER TABLE` guarded by `PRAGMA table_info`. Imports `~/.config/termy/termy.db` once and drops the old mindmap tables.
- `shelldeck/stats.py`: `/api/stats` data. psutil gives system CPU/RAM and per-terminal process trees; CPU is normalized to the whole machine, and `Process` objects are cached so `cpu_percent` has a baseline. GPU comes from one long-lived `nvidia-smi -lms 2000` (NVIDIA only, `CREATE_NO_WINDOW`), stopped after 30s unpolled. Don't go back to a launch per poll: each launch costs about 400ms of CPU. Shell pid is `Proc.pid`.
- `shelldeck/agents.py`: AI coding agent CLIs (`AGENTS`: label, exe names, npm/pip cmdline markers, fallback models). `identify()` matches a process (cached per pid), with the model from `--model`/`-m` or the agent's config; `models()` reads Codex's `models_cache.json` and opencode's models.dev cache. `stats.tree` returns `agent` per terminal (outermost agent process), which feeds the pane `ai-chip`, the sidebar badge and `static/agents.js` (AI agents page, launch, team prompt). `/api/agents`, `/api/sessions/{id}/screen` and `/api/sessions/{id}/input` back `sd agents/peek/tell`.
  - Model order: `--model` flag, env var (`MODEL_ENV`), `devin -r <id>`'s session, then the agent's config.
  - Devin: `devin_models()` parses `<install>/model_configs.bin` (protobuf; base name at field 30.1, lowercased and dashed, is what `--model` takes). `devin_sessions()` reads `sessions.db` read-only (`%APPDATA%/devin/cli`; Linux paths are guesses). A devin exe outside a `cli` folder counts as `devin_desktop` (unverified).
  - `context(pid, key)` (in `stats.tree`, so every 2s): Claude from `~/.claude/sessions/<pid>.json` (sessionId, `status` busy/idle) and the last non-sidechain `usage` in `projects/*/<id>.jsonl`, window from `CLAUDE_1M` (estimated); Codex from the newest rollout whose `session_meta.cwd` matches and that started after the process, latest `token_count.last_token_usage` and `model_context_window`; Devin from `sessions.db` message metrics, window from `model_configs.bin` field 23.4. Logs are cached per (pid, agent) in `_files`.
  - Needs-you (`updateAgentStates()` in app.js, run by the stats poller): `working` while Claude reports `busy` or output keeps flowing past `AGENT_BUSY_MS`; `approval` only when the screen is quiet for `AGENT_QUIET_MS` and `QUESTION_RE` (mirrors `agents.QUESTION`; prompt strings taken from the Claude, Codex and Devin binaries) matches the last `PROMPT_LINES` of the screen. There is no "waiting" state: an agent that finished doesn't alert. Entering `approval` calls `notify()` and, when it alerted, `views.chime()`, except for sub-agents. `screenTail` joins wrapped rows and drops relayed questions (`RELAYED_RE`), or a parent showing its sub-agent's question would alert too.
  - Sub-agent questions (server): `_pump` collects each sub-agent's output in `ask_buf`; `_watch_questions` checks it once quiet `QUIET_S`, and `_forward_question` types the question (cut from the drawn output, `BOX` rules stripped) into the parent when an agent runs there, else broadcasts `{"type":"question"}` for a toast. The same question redrawn within `REASK_S` isn't forwarded again. Keep the message's ending in sync with `RELAYED_RE`. Parents answer with `sd answer <name> <keys>` (`KEYS` in cli.py, one key per request).
  - `sd close <name>` (cli.py): from inside a terminal only the caller's own sub-agents, never itself; an open hand-off needs `--force`. The skill tells parents to ask the user first. A done hand-off's toast offers *Close* (`notify(..., extra)`).
  - `pty.create` sets `TERM_PROGRAM=shelldeck` (Devin's conhost warning) and drops `PARENT_AGENT_ENV` (a server started inside Claude Code would make every claude a transcript-less child session).
  - `_plain()` turns snapshot cursor-forwards (`ESC[nC`) into spaces before stripping ANSI, for peek and search.
  - `outside()` scans every process for agents not under a shelldeck shell; `HELPER_ARGS` skips helpers (Claude's `--chrome-native-host`, Electron `--type=`).
  - The page re-renders every 5s, so `keep()`/`restore()` hold the picked models, project and open model lists.
  - `screen` asks open browsers for a fresh xterm snapshot (`{"type":"snapshot"}` to the socket) and waits up to 1s, because raw TUI output is cursor-addressed. With no browser open it falls back to raw output.
  - `input` writes the text, waits 0.3s, then sends CR: TUIs take text plus CR in one burst as a paste.
- **Terminal nicks.** `sessions.nick` is a person's name from `db.NICKS`, unique across all terminals (`_free_nick`, backfilled in `init_db`); `_session()` in cli.py resolves id, nick, name, then id prefix. PTYs get `SHELLDECK_NICK` and, for sub-agents, `SHELLDECK_PARENT` (`_attach` passes `extra_env`). `run()` exports `SHELLDECK_PORT` (and an absolute `SHELLDECK_HOME` when set), so `sd` in a terminal reaches the server that owns it.
- `shelldeck/agent_state.py`: canonical lifecycle (`unknown|idle|working|blocked|done|exited`, `blocked_reason`), `Report`/`Registry` with source precedence (integration > custom > native > manifest > heuristic) and TTL expiry, and `parse_report()` validation (stable `ReportError` codes; resume argv must start with the agent's bare executable name and is accepted only from `integration:*`). The server owns state: `_check_agents` puts the old heuristic in as a `heuristic:*` report (`lifecycle.heuristic`, approval -> blocked/approval), resolves, and `_publish` broadcasts `agent_state` with `status` (legacy `state` kept) and pushes only on a transition into blocked. `POST /api/agent-reports` identifies the terminal only by `X-Shelldeck-Report-Token` (`report_tokens`, set as `SHELLDECK_AGENT_REPORT_TOKEN` in `_attach` and dropped when the PTY exits). `agent_kind` holds `(agent, generation)`; `/api/agent-wait` pins the generation and wakes on `_state_changed` events, never polls screens. The browser trusts server status only for authoritative sources (`AUTHORITATIVE` in app.js); otherwise its own screen heuristic stays. Statuses carry `since` (when the state began). `/ws/alarms` sends every current status on connect. `attentionOf()`/`ATTENTION` in app.js rank terminals (blocked > done-unseen > exited > working > idle); `S.doneUnseen` is cleared by `showSession`; `rollupChip` is the project's sidebar text.
- `shelldeck/integrations.py` + `shelldeck/hook.py`: lifecycle integrations. `PRIORITY` (claude, codex, gemini, devin) is the supported set: default `sd integration install`, listed first, verified end to end. Every other installer is `tier: later` (preview), and changes to it must not break the four. `INSTALLERS`: `JsonHooks` merges into shared JSON configs (claude settings.json, gemini settings.json, cursor hooks.json) and owns only entries whose command matches `-m shelldeck.hook <agent>`; `OwnFile` writes copilot's `hooks/shelldeck.json` and the OpenCode plugin (`OPENCODE_PLUGIN`). Both make a one-time `.shelldeck-backup`, write atomically, and refuse non-JSON configs. `hook.py` is stdlib-only (one interpreter start per event): `EVENTS` maps each agent's hook events to states, it posts with only the report token, always exits 0 and prints `REPLY`. `_guarded` lets `POST /api/agent-reports` through with just `X-Shelldeck-Report-Token` when `_is_local` (never a tunnel). Formats come from the agents' hook docs as used by dotpals; add an agent only after checking its docs. `CodexHooks` also sets `[features] hooks = true` in config.toml (`enable_codex_hooks`, line edit). Qwen/Qoder/Droid/Devin hooks are `SESSION` (session id only; no `state` in the report, so `parse_report` returns no Report). `hook.RESUME` holds the resume argv per agent (from herdr's agent_resume.rs); `agent_state.RESUME_EXE` allows `cursor-agent`/`agy`. Installing refuses when the agent's config folder is missing. Other shapes: `JsonHooks(key=None)` (MastraCode, flat events at the top level), `NamedBlock` (Antigravity, a `shelldeck` key), `KimiToml` (marked `[[hooks]]` block with explicit states: the hook takes `<agent> <state>` like herdr's, `STATES`), `OwnFile` for Grok's hook file and the Kilo plugin (`_opencode_plugin('kilo')`), `Extension` (Pi/OMP `.ts` extensions from `PI_EXTENSION`; OMP refuses Pi's folder), `HermesPlugin` (Python plugin + `hermes_enable` line edit of `plugins.enabled`, refusing layouts it doesn't know). Without hooks, `agents.native()` (`_codex_native` reads the rollout's newest `CODEX_EVENTS` entry, `_claude_native` reads `sessions/<pid>.json`) feeds `native:<agent>` reports via `agent_state.native_report` (working holds `NATIVE_WORKING_S` past the last event, so a prompt falls back to the screen), and `_save_native` stores the session id/resume argv once per id.
- **Native launch flags.** `team.launch_line(key, model, prompt, extra)` is the one command builder (spawn, `/api/agent-start` used by the page's Launch and `sd agent start`, resume). `integrations.run_flags(agent)` returns per-run hook flags: only Claude has one (`--settings <config>/agent-hooks/claude-settings.json`), and it's skipped when the global Claude hook is installed. Codex/Gemini/Devin hooks need `sd integration install`. Unsafe or long prompts go to `.shelldeck/prompts/<id>.md`.
- `shelldeck/agent_commands.py`: `COMMANDS` maps shelldeck actions to each agent's own slash commands (claude from its cli.js, codex from slash_command.rs, gemini from docs/reference/commands.md; devin `VERIFIED = False` until checked on a real install). `POST /api/sessions/{id}/agent-command` types it, refusing blocked/working agents unless `force`; arguments must match `SAFE_VALUE`.
- `shelldeck/context.py`: persistent agent context in `<config>/context.db` (separate from shelldeck.db; FTS5 `search` table with a LIKE fallback). Projects are keyed by resolved folder (the deepest shelldeck project containing cwd, else the git top level). Everything stored goes through `redact()`; `SENSITIVE_FILE` paths are never referenced. Knowledge is de-duplicated per project by `_norm` and never merged across projects. `check_stale` re-hashes `source_references`. `project_context` returns a `ContextPackage` dict plus budgeted `text`. `project_files` writes STATE/TASK/MEMORY/DECISIONS.md (server runs it on a thread via `_project_later`). Server endpoints are `/api/context*`, wrapped by `_ctx` (sqlite errors -> 503 `context_unavailable`, never a crash). `_handoff_files` appends `context.snapshot`.
- `shelldeck/team.py`: agent teams.
  - `tier()`/`pick_model()`: `--model auto|small|medium|large` maps onto the agent's own `models()` by regex (medium = its default).
  - `SPAWN` (agent -> model flag, args before the prompt) and `spawn_line()`/`kickoff()`: the kickoff prompt is limited to `SAFE_PROMPT` characters so one double-quoted string works in pwsh, cmd, bash, zsh and fish; the task itself lives in a file.
  - `write_files()`: `<project>/.shelldeck/handoffs/<id>.md` per hand-off plus the `handoff.md` index, rendered from the `handoffs` table (the DB is the source of truth); `.shelldeck/.gitignore` is `*`.
  - `install()`: the `SKILL` text as `~/.claude|.codex|.config/devin/skills/shelldeck/SKILL.md`, or a `<!-- SHELLDECK_START -->` block in other agents' global instruction files (`TARGETS`; only claude/codex/devin/gemini/opencode paths are verified). The server runs it on start unless `SHELLDECK_HOME` is set or the `agent_skills` setting is `off` (`sd install-skill --remove`).
  - Server: `/api/spawn` (refuses a parent that has a parent, i.e. sub-agents can't spawn; new session in the project root; `_start_agent` types the command after the shell's first `OSC 133;A`, up to 10s), `/api/handoffs` (target must run an agent, since typed text in a bare shell would execute), `/api/handoffs/{id}/done`. `_tell_sender` toasts every browser (`_broadcast` over the alarm socket) and types into the sender only when an agent runs there. `_pump`'s exit marks open hand-offs to that terminal `exited` (`_orphan`); `STOPPING` silences the messages at shutdown.
- **File editor.** `/api/fs/file` GET/PUT (2 MB cap, NUL = binary, non-UTF-8 is read-only, CRLF/BOM round-trip, `mtime` as a `st_mtime_ns` string and 409 `changed_on_disk` unless `force`), `/api/fs/raw` (raster images only: SVG or HTML served on this origin would run script), `/api/fs/show` (broadcasts `open_file` for `sd edit/view`; the CLI falls back to `/?file=` when no browser is listening). Relative paths start at the session's cwd (`_abs`).
- **Scratchpad.** `scratch` table (`id`, `body`, times; the title is the first line), `/api/scratch` CRUD, `static/scratch.js` (autosave 500ms, flushed when switching notes).
- `shelldeck/auth.py`: password, setup code, browser sessions (5s cache, idle expiry, throttled `last_seen` writes), CLI token, login rate limit, `reset()`. `scheduler.py` / `runner.py`: APScheduler cron jobs and reminders; jobs run via `subprocess.run` with the default shell.
- `shelldeck/gitgraph.py`: git log parsing + lane layout for the sidebar git popup (`static/gitgraph.js`), and `checkout` (refs starting with `-` refused). `has_git` flags projects in `/api/projects`.
- `shelldeck/addons/fast_context.py`: vendored single-file LLM-free code search behind `sd search` (own index in `~/.cache/fastcontext`, `FC_CACHE_DIR` overrides). Import it lazily; keep edits to it minimal.
- `shelldeck/cli.py`: Typer CLI.
  - The `_main` shim turns `sd <folder>` into `sd open <folder>`.
  - `ensure_server()` spawns a detached `serve` and polls `/api/health`.
  - It talks to the server only over HTTP.
- `shelldeck/static/`:
  - `index.html` (boot overlay, shell).
  - `app.css`.
  - `app.js`: state, `Term` class, layout tree and free canvas, sidebar, palette, shortcuts, boot.
  - `views.js`: bookmarks, scheduler, tasks, settings, add-project, alarms.
  - `devices.js`: Devices view (signed-in browsers, in use / idle, revoke).
  - `editor.js`: `openFile(path, {sid, mode, line})` dialog: textarea + line-number gutter, markdown preview, image view, Ctrl+S, dirty guard through `dialog().canClose`. Used by `sd edit/view`, the palette's *Open file…* and clickable paths when the `editor` setting is `shelldeck`.
  - `context.js`: Context view over `/api/context*` (project picker, task/state form, memory verify/invalidate, decisions, relationships, recall). Not polled, so forms keep their input.
  - `md.js`: small markdown renderer (escapes everything first; http(s) links and images only) for the viewer and the scratchpad.
  - The alarm socket (`/ws/alarms`) also carries `open_file`, `spawned` (app.js opens the sub-agent's pane and gives focus back) and `handoff` (toast + chime).
  - `history.js`: Command history view (`/api/history`, `commands` table, filled by `{"type":"command"}` socket messages from the UI) and the search-all dialog (`/api/search` over `PtyManager.searchable`).
  - Clickable paths: `pathLinks()` in app.js asks `/api/fs/check` which candidates exist (cached 10s, since `cd` changes them), and `/api/open` launches a `vscode://` URL or the default app via `os.startfile`/`xdg-open`, never a shell. It refuses executables.
  - Ports: `stats.listening()` maps pids to LISTEN ports; `S.ports` feeds the pane-header chips.
  - `monitor.js`: one 2s `/api/stats` poller (skipped while hidden), feeding the top-bar `#sysmon` meters and the Task manager view (`view-monitor`).
  - `ui.js`: api, icons, dialogs, menus, toasts, localStorage `store`.
  - `vendor/`: pinned xterm.js 5.5 + fit + web-links + webgl + search + serialize.
- `tests/`: `test_shelldeck.py` (API, security, settings, tasks, scheduler, static caching, real PTY round trip), `test_cli.py` (banner, web, browser/app window, serve guards, render, search), `test_gitgraph.py`.
- **Release plumbing.**
  - `install.ps1` / `install.sh`: one-line installers (uv, then `uv tool install shelldeck`; `SHELLDECK_SOURCE` overrides the source and CI feeds it the built wheel; `UV_NO_MODIFY_PATH` skips PATH edits).
  - `.github/workflows/ci.yml`: lint, a Windows+Ubuntu × py3.12–3.14 test matrix, and an installer smoke test.
  - `release.yml`: on a `v*` tag, reuses CI, checks the tag matches the pyproject version, then builds, publishes to PyPI (trusted publishing, environment `pypi`) and creates the GitHub release.
    - Manual run (`workflow_dispatch`, input `version`) publishes a release candidate from a `feature/` or `fix/` branch: `x.y.zrcN` only, newer than pyproject, patched into pyproject in the build (never committed), no tag or GitHub release. It lives in `release.yml` because the PyPI trusted publisher is bound to that file.
  - README screenshots live in `docs/assets/`. `docs/superpowers/` and `plans/` are local-only (gitignored).
- `chapters/`: spec corpus for a future agent platform, not implemented. `_ref_code/`: reference only.

## Terminal flow

1. `POST /api/sessions` creates and names the DB row.
2. The UI opens `/ws/{id}`. The server rejects unknown ids (4404) and foreign origins (1008), then replays scrollback as `{"type":"output","replay":true}`. The UI ignores xterm's auto-replies while replaying, or they get typed into the shell.
3. The first `resize` spawns the PTY and starts one `_pump` task per session, which fans output out to every socket.
   - Output is read on a thread per session (`PtyManager.stream`) and queued to `_pump`, which merges queued chunks.
   - The thread polls with `time.sleep(0.001)` after activity; `write()` wakes it. On Windows, asyncio sleeps and `Event.wait` have ~16ms resolution, which made echo take 16–32ms (now ~4ms).
   - pywinpty's blocking `read` holds data back and hangs at EOF, so don't use it.
4. When the shell exits, the server sends `{"type":"exit"}`, and the UI deletes the session.

- **Ctrl+C.** The detached server has `CREATE_NEW_PROCESS_GROUP`, which makes Windows ignore Ctrl+C in it and every child. `WinProc` calls `SetConsoleCtrlHandler(None, False)` before spawning; don't remove it.
- **Folder tracking.** `_pump` parses `633;P;Cwd=` from output and stores it in `sessions.cwd`, so a respawned shell opens where it left off.
- **Restore.** The UI sends `{"type":"snapshot"}` (xterm serialize addon, trailing cursor moves stripped) every 15s and on hide. `PtyManager.save()` writes `scrollback/<sid>.log` every 15s.
  - It saves the snapshot while fresh and falls back to raw output otherwise; raw ConPTY output is cursor-addressed and replays garbled.
  - On respawn the file is replayed, then the "restored" banner, then enough newlines to push it all into scrollback, because a new ConPTY paints absolute rows.
  - Read/write these files as bytes: text mode drops the CR in CRLF.

## Frontend conventions and gotchas

- **Caching.** `/static` is served with `Cache-Control: no-cache` (`RevalidatedStaticFiles`), so browsers revalidate and never run stale JS after an upgrade.
- **Dialogs.** `dialog()` focuses its first field, or the dialog itself, so Escape doesn't go to the terminal behind it.
- No build step and no inline handlers. Use `data-action` / `data-act` attributes with event delegation.
- **Layout state:**
  - Layout is `S.layout` (tree `{sid}` | `{dir, kids, sizes}`) or `S.free` (`sid -> {x,y,w,h,z}`), both in localStorage.
  - `leaves()` with no argument returns the visible sids for the current mode.
  - `renderLayout()` rebuilds the DOM and re-parents live xterm elements.
- **CSS names:**
  - Split containers use `split-row` / `split-col`. `row` / `col` are taken by button rows and kanban columns.
  - `#app` needs `grid-template-rows: minmax(0, 1fr)`, or overflowing panes stretch the whole page.
- **Animations:** terminals and sidebar rows don't animate on open. The sidebar re-renders every 5s, so new motion must not replay on refresh.
- **Touch screens** (`pointer: coarse`): `#keybar` (Esc/Tab/sticky Ctrl/arrows; `withCtrl()` in the `onData` path) and the DOM renderer instead of WebGL, whose canvas came up blank at DPR 3. Phone CSS is the `max-width: 560px` block at the end of app.css.
- **Keystroke tracking:** ignore focus reports `ESC[I` / `ESC[O`; they are not edits.
- **Shell pitfall:** in the Bash tool, large quoted heredocs sometimes fail to parse. Write patch scripts to `.devhome/*.py` and run them instead.

## Conventions

- Commits: Conventional Commits in caveman-commit style, authored by the user. No AI attribution or `Co-Authored-By` trailer.
- Branches: `main` is protected (`.github/rulesets/main.json`: PR only, all CI checks green, squash merge, linear history). Work on `feature/<name>` or `fix/<name>`, which CI's `branch name` check enforces, and open a PR into `main`. Never push to `main`. If you rename a CI job or matrix entry, update the required checks in the ruleset too.
- Keep code minimal: stdlib and existing deps first; mark deliberate shortcuts with `ponytail:` comments.
- Verify UI changes in a real browser (`playwright-cli`, Edge) before claiming done.
- Update `README.md` when user-visible behaviour changes.
- **Every release updates the docs in the same release PR, before tagging:**
  - `pyproject.toml` version, then `uv lock`.
  - `CHANGELOG.md`: a new `## [x.y.z] - YYYY-MM-DD` section (Added / Changed / Fixed / Removed) covering every merged change since the last tag (`git log vPREV..HEAD`), plus its link at the bottom.
  - `README.md`: new commands, options, settings, install or security behaviour; `CONTRIBUTING.md` if the workflow changed.
  - This file, for new modules, flows, gotchas or release steps.
  - Then merge, tag `vX.Y.Z` on `main`, and confirm the release run, PyPI and the GitHub release.
