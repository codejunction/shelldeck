# Agent enhancement: implementation task plan

## Status

**Implementation in progress. Milestone 1 and 2 complete; Milestone 5 core (context store, recall, context CLI) complete; `sd agent status/wait` from Milestone 6 done. Agent-specific installers (3.2–3.4) and restore (4.2) still wait on upstream verification.**

### Progress log

- **2026-10-03 — Task 1.1 complete:** added the isolated lifecycle domain
  module and authority resolver with focused tests. Existing runtime behavior
  remains unchanged; Task 1.2 will adopt this contract in the server.
- **2026-10-04 — AGENT_ENHANCEMENT.md updated:** Added comprehensive knowledge
  system, cross-project retrieval, CLI commands, database design, and
  context persistence layers (sections 27-63).
- **2026-10-04 — Tasks 1.2, 2.1, 2.2 complete:** `_check_agents` now feeds the
  screen/activity heuristic into an `agent_state.Registry` and broadcasts
  `agent_state` with a structured `status` (legacy `state` kept). Push
  notifications fire only on a transition into `blocked`. Added
  `POST /api/agent-reports` (per-terminal `SHELLDECK_AGENT_REPORT_TOKEN`,
  validated source/agent/state/TTL/metadata/resume argv, stable error codes),
  `GET /api/agent-status`, and the event-driven `POST /api/agent-wait`, which
  pins the agent process generation. The browser defers to authoritative
  (integration/custom/native) server status; the Agents page shows state and
  source. CLI: `sd agent status|wait|report`.
- **2026-10-04 — Milestone 5 core complete:** `shelldeck/context.py` keeps
  `<config>/context.db` (projects, sessions, tasks, state, knowledge,
  decisions, events, relationships, source_references, FTS5 `search`) and
  writes STATE/TASK/MEMORY/DECISIONS.md projections in the background.
  Secrets are redacted and sensitive files are never referenced. Facts are
  de-duplicated per project and scoped (not merged) across projects. Changed
  sources mark knowledge STALE. Context packages have a budget. Hand-off files
  get a context snapshot. CLI: `sd context|resume|recall|memory|remember|
  discover|decide|decisions|projects|project|relate`, `sd task update`, and
  `sd knowledge verify`.
- **2026-10-04 — Task 5.5 complete:** the installed skill teaches the
  startup/during-work/pre-stop context workflow (`sd context`, `recall`,
  `remember`, `discover`, `decide`, `task update`, `agent status/wait`)
  without exposing the database.
- **2026-10-04 — Task 3.1 complete, 3.3/3.4 mostly complete, + Gemini, Cursor, Copilot:**
  following dotpals' adapters (github.com/Rikinshah787/dotpals, bridge/adapters),
  `integrations.INSTALLERS` installs lifecycle hooks for Claude Code, Gemini CLI,
  Cursor and Copilot CLI and an OpenCode plugin. Shared JSON configs are merged
  and only shelldeck-owned entries are touched; there is a one-time backup,
  atomic writes and an outdated status. `python -m shelldeck.hook <agent>` maps
  events to lifecycle state and reports with only the terminal token (local only).
  CLI: `sd integration detect|status|install|uninstall`; API: `POST/DELETE
  /api/integrations/{agent}`; the AI agents page has an Integrations section.
  Verified end to end on a real server and PTY (hook -> blocked/approval from
  `integration`, Claude native session stored). Still not verified against
  installed agent binaries in this environment; Codex has no hook (screen
  detection remains).
- **2026-10-04 — Task 3.2 (Codex) done the dotpals way:** no hook; Codex
  rollouts (`~/.codex/sessions`) give `native:codex` working/done and
  `codex resume <id>`, and Claude's `sessions/<pid>.json` gives busy plus
  `claude --resume <id>`. Native reports expire 15 s after the log goes quiet,
  so approval prompts fall back to screen detection. Native sources may now
  issue validated resume argv.
- **2026-10-04 — Tasks 4.1 and 4.2 complete:** `agent_resume` setting
  (never/ask/auto, default ask), `GET /api/agent-sessions` (no native ids sent),
  `POST /api/sessions/{id}/resume`, `sd agent resume`, a *Resumable sessions*
  table and a Settings select. `_resume_plan` re-validates argv (bare executable,
  plain-word args only), the executable and the cwd. Auto-resume runs once per
  terminal after its shell starts and never into an open hand-off; failures are
  logged and the terminal stays a normal shell.
- **2026-10-04 — More agents (herdr formats):** Codex hooks (`hooks.json` +
  `[features] hooks = true`), session-id hooks for Qwen Code, Qoder CLI, Factory
  Droid and Devin CLI, and resume argv for Codex, Copilot, Cursor, Devin, Droid,
  Qwen and Qoder. Session-only reports keep lifecycle with screen detection.
  Installing needs the agent's config folder. 10 agents are installable (6 with
  exact lifecycle); the rest stay on screen detection.
- **2026-10-04 — Herdr's remaining hook agents:** Kimi Code (marked TOML
  `[[hooks]]` block, AskUserQuestion is reported as blocked: question), MastraCode
  (flat top-level hooks), Kilo Code (the OpenCode plugin pointed at Kilo), Grok
  (own hook file, session) and Antigravity CLI (named block, session). 15 agents
  are installable, 9 with exact lifecycle. Pi, OMP and Hermes (TS/Python plugin
  APIs) and Letta remain on screen detection.
- **2026-10-04 — Letta and Hermes:** Letta (`SessionStart` session hook,
  resume via `--conversation [default --agent]`) and Hermes (Python plugin +
  `plugins.enabled` YAML edit that refuses layouts it doesn't know). 17 agents
  are installable. The OpenCode plugin was verified end to end inside a real
  terminal (permission.asked -> blocked/approval from `integration`). Pi/OMP
  remain.
- **2026-10-04 — Pi and OMP:** extensions in their `extensions/` folders,
  with event names and interactive-session gates from herdr's assets. All four
  JS plugins (OpenCode, Kilo, Pi, OMP) are run under Node in the tests with fake
  events, and each body passes server validation. 19 agents are installable,
  covering every agent herdr integrates.
- **2026-10-04 — Priority agents (user decision):** Claude Code, Codex, Gemini
  CLI and Devin CLI are the supported set; every other integration is marked
  "later" (preview: not in the default install, folded away in the UI). Devin
  now reports full lifecycle from its hooks, with its session id from
  `sessions.db` as a fallback (`agents._devin_native`). Gemini gets
  `gemini --resume <id>`. Each of the four was verified end to end on a real
  server + PTY with a fake agent process (hook -> integration state; native
  session stored).
- **2026-10-04 — Task 6.2 complete:** project rollups (text, not color alone),
  the agents list sorted by attention with elapsed time (`since` from the server),
  an *Open next* banner across projects, done-until-seen, and the current state
  sent to newly opened tabs. Checked in a browser with a real blocked agent
  (sidebar "needs you", banner, toast).
- **2026-10-04 — Task 7.2 complete:** `GET /api/agent-explain/{sid}` and
  `sd agent explain TARGET|--file F [--json]`. It shows the process generation,
  every report (decides / expired / age / time left), the heuristic inputs
  (quiet, burst, matched built-in question phrase) and integration/stored-session
  status, with a hint to install the integration when the screen decides. It
  never returns screen text or native ids. Checked live.
- **2026-10-04 — Task 6.1 complete:** `POST /api/agent-prompt` (refused while
  blocked; `--wait` pinned to the process generation and to a state that began
  after the prompt), `sd agent rename`, an in-memory event log and SSE
  `GET /api/events` with `Last-Event-ID`, and `sd events subscribe`. Checked live
  (detected -> idle -> session_updated -> blocked streamed).
- **2026-10-04 — Native CLI flags for the priority agents (user request):** a
  single `team.launch_line` passes model, initial prompt (claude/codex positional,
  gemini `-i`, devin `--`) and per-run hooks. Claude uses `--settings <file>`,
  verified in Claude Code 2.1.289's `--help`; the real CLI launched with it but
  stopped at login, so live hook delivery from it is still unverified. Codex,
  Gemini and Devin have no per-run hook flag, so they keep the installed hooks.
  New `POST /api/agent-start`, `sd agent start`; the page's Launch is now
  server-side; resume adds Claude's `--settings`.
- **2026-10-04 — Native slash commands (user request):** `agent_commands.py`
  maps compact/clear/new/model/status/resume/review/init/memory/rename/export/
  diff/hooks/permissions/plan/rewind/quit to each agent's own command, verified
  from Claude Code's bundle, Codex's slash_command.rs and Gemini's command docs.
  `sd agent cmd`, `POST /api/sessions/{id}/agent-command` (refused while
  blocked/working unless forced) and a *Command…* menu on the AI agents page,
  checked in a browser. Codex hooks gained PreToolUse/PostToolUse/
  PermissionRequest/PreCompact (from codex-rs/hooks); Gemini resume confirmed
  in its docs.
  - [~] Devin CLI: commands, flags and hooks cross-checked from web sources; running the real CLI is still pending. The
    environment blocks devin.ai (needs devin.ai / *.devin.ai allowed in the
    cloud environment's network settings).
- **2026-10-04 — Task 5.8 (UI) complete:** a *Context* view (sidebar and
  palette) with a project picker, an editable task/state, memory with status
  badges and Verify/Invalidate, decisions, related projects, recent events and
  cross-project search. Local-only storage is stated on the page. Checked in a
  browser (save, redaction, verify, search).
- **2026-10-04 — Task 7.1 complete:** bundled `agent_detection/default.toml`
  (one rule per prompt phrase, with id and reason), local per-agent overrides
  (replace / disable / add), invalid overrides ignored and reported in
  `sd agent explain` (rule id, manifest source/version, warning). The server's
  heuristic uses it; the wheel includes the file; docs/agent-detection.md added.
- **2026-10-04 — Remaining CLI/UI items:** `sd switch <agent>` (checkpoint +
  start the agent via `/api/agent-start` with a `sd context` kickoff), a
  *Message* action on the AI agents page (refused while blocked), and the skill
  now teaches agent prompt/cmd/wait/switch. Claude hook events were checked
  against Claude Code 2.1.289's bundle: `StopFailure` doesn't exist there and was
  removed. Checked live.
- **2026-10-04 — Fact extraction by the agents (user decision, the token-lean
  option):** no LLM in shelldeck. `POST /api/sessions/{id}/extract`,
  `sd agent extract` and an *Extract facts* button send one short prompt, on
  demand only and refused while working or blocked. The prompt tells the agent
  that dedup and redaction happen server-side, so it doesn't read memory first;
  `sd remember` takes several facts per call. Checked live (multi-fact save,
  near-duplicate merged).
- **2026-10-04 — Support matrix:** docs/agent-integrations.md lists each
  installable agent's tier, method, exact-state and resume support, and what
  its format was verified against; the CHANGELOG links to it.
- **2026-10-04 — Windows CI fix (0.0.7rc1 run):** a stale `heuristic:screen`
  blocked report could outlive the next `heuristic:activity` verdict when both
  had the same timestamp (Windows' ~16ms monotonic clock). `Registry.put_heuristic`
  now replaces the previous heuristic verdict.
- **2026-10-04 — 0.0.7rc1 published to PyPI** from `feature/agent-integrations`
  (release.yml manual run 37226585667; CI green on Windows and Ubuntu, py3.12–3.14).
  Checked by installing `shelldeck==0.0.7rc1` with `--prerelease allow`: the
  version, the `sd agent` commands and the bundled detection rules are present.
  No tag or GitHub release, as release candidates get none.
- **2026-10-04 — Deterministic capture (spec sections 13, 26–28, 40):** hooks
  attach the finished tool call (command + ok, or edited file). The server
  records COMMAND / TEST_RESULT / ERROR / FILE_EDIT events, updates Tests and
  Last error, opens a context session per agent run (terminal + generation) and
  writes `.shelldeck/sessions/<id>.md` with files, commands, tests, errors and
  git on exit. People's shell commands are captured too. No tokens are used;
  output, contents and sensitive files are never stored. Fixed a redaction bug
  that could corrupt stored event JSON. Checked live (fake agent, hook events,
  exit -> session file).
- **2026-10-04 — Devin CLI cross-checked from web sources (user request):**
  search results quoting the Devin docs and other projects' Devin integrations
  agree with shelldeck's flags (`-- "prompt"`, `--model`, `-r/--resume`), config
  path and hook events. Devin rejects unknown hook events, so a test now guards
  the installed set. Added its shell tool name `exec` for capture, and expanded
  and verified its slash commands. Running the real CLI is still blocked.
- **2026-10-04 — Smart recall without embeddings (user decision):** the
  `recall_agent` setting, `sd recall --smart` and a Context page toggle. An
  installed agent's smallest model, in its own non-interactive mode, expands the
  query into ≤8 keywords that are ORed into the FTS search. Token-lean: only the
  query is sent, the reply is short, results are cached per (agent, query), and
  it falls back to keywords. Checked live with the real Claude Code CLI: "auth"
  -> authentication, oauth, token, ... found the OAuth fact in ~5 s, then cached.
- **2026-10-04 — Claude verified with the real CLI:** `claude -p --settings
  <shelldeck hook file>` inside a shelldeck terminal sent real hook reports
  (SessionStart -> idle, UserPromptSubmit/Pre/PostToolUse -> working), its
  `echo hi` tool call was captured, and the session file was written on exit. A
  -p run exits right after Stop, so the final "done" can arrive after the
  process is gone (not an issue for interactive sessions).
- **2026-10-04 — 0.0.7rc2 published to PyPI** (release run 37227723591, CI green
  on Windows and Ubuntu, py3.12–3.14). Installed from PyPI and checked (version,
  `sd recall --smart`).
- **2026-10-04 — Docs:** README, CHANGELOG (Unreleased), CLAUDE.md,
  `docs/agent-automation.md` and `docs/agent-report.schema.json` describe the
  lifecycle, report API, waits and context commands.

The authoritative design is [AGENT_ENHANCEMENT.md](AGENT_ENHANCEMENT.md). This
file is the execution checklist for breaking that design into reviewable,
low-risk implementation pull requests. Complete phases in order unless a
dependency is explicitly removed through a design review.

## Delivery rules

- Keep each pull request scoped to one phase or one independently testable
  vertical slice.
- Preserve existing `sd spawn`, hand-off, parent escalation, and child-terminal
  safety behavior throughout the work.
- Ship no agent-specific hook/plugin installer without tested install, status,
  upgrade, and uninstall behavior on every supported platform.
- Treat lifecycle reports as untrusted input and validate them at the server
  boundary.
- Do not add a command to the installed Shelldeck skill until its CLI/API
  contract and tests are stable.
- Run `uv run ruff check` and `uv run pytest -q` before every implementation
  commit.

## Milestone 1 — Lifecycle foundation

### Task 1.1: Define the lifecycle domain model

- [x] Add `shelldeck/agent_state.py`.
- [x] Define canonical state values: `unknown`, `idle`, `working`, `blocked`,
  `done`, and `exited`.
- [x] Define typed models for an agent status, report, report source, native
  session reference, and display metadata.
- [x] Define one state-authority resolver with the precedence documented in the
  design spec.
- [x] Move no UI behavior in this task; preserve the current heuristic outputs
  through a compatibility adapter.

**Primary files:** `shelldeck/server.py`, `shelldeck/agents.py`, new
`shelldeck/agent_state.py`, and new focused tests.

**Done when:** unit tests prove valid/invalid state handling, authority
precedence, source replacement, expiry, and fallback selection.

### Task 1.2: Make lifecycle state server-owned

- [x] Add an in-memory report registry keyed by terminal session and report
  source.
- [x] Change the server's agent watcher to resolve the canonical status rather
  than emitting only the current `working` / `approval` / `idle` result.
- [x] Map the existing approval heuristic to `blocked` with reason `approval`.
- [x] Emit structured `agent_state` alarm events containing state, source, and
  optional blocked reason.
- [x] Return status/source information from `/api/agents`.
- [x] Update `static/app.js` and `static/monitor.js` to consume server state;
  remove duplicate browser-side lifecycle decisions only after compatibility
  tests pass.

**Primary files:** `shelldeck/server.py`, `shelldeck/static/app.js`,
`shelldeck/static/monitor.js`, `tests/test_shelldeck.py`.

**Done when:** all tabs show the same state; expiry returns to a fallback state;
notifications occur only on a transition into blocked; and no-browser CLI use
continues to work.

## Milestone 2 — Secure reporting API

### Task 2.1: Add terminal-bound agent report ingestion

- [x] Add `POST /api/agent-reports` with the schema in the design spec.
- [x] Generate a unique report token for each live terminal and expose it only
  through that terminal's process environment.
- [x] Validate token/session binding, agent kind, source, state, TTL, metadata
  size, and resume argv.
- [x] Reject reports for closed, unknown, or mismatched terminal sessions.
- [x] Ensure normal logs and API responses do not disclose native conversation
  IDs or terminal-bound tokens.
- [x] Add a compact, documented error vocabulary for integration authors.

**Primary files:** `shelldeck/server.py`, `shelldeck/pty.py`,
`shelldeck/agent_state.py`, `tests/test_shelldeck.py`.

**Done when:** a report cannot update another terminal; malformed/expired input
is rejected; and a valid report produces an authoritative state transition.

### Task 2.2: Separate display metadata from state authority

- [x] Support bounded title, display-agent/role, state-label, and token metadata
  fields on reports.
- [x] Apply metadata with source ownership and expiry.
- [x] Ensure metadata cannot change lifecycle semantic state, notifications,
  hand-off completion, or restore eligibility.
- [x] Add API tests for stale metadata removal and source conflict behavior.

**Done when:** UI labels can change without affecting the semantic lifecycle
state or automation behavior.

## Milestone 3 — Integration manager

### Task 3.1: Establish the integration package and commands

- [x] Create the integration layer (`shelldeck/integrations.py` installers + `shelldeck/hook.py`) with a common interface.
- [x] Implement discovery, install, uninstall, and status operations.
- [x] Add `sd integration detect|list|install|uninstall|status`.
- [x] Add authenticated API endpoints for the same operations.
- [x] Add an Agents-page distinction between a Shelldeck skill and a runtime
  integration.
- [x] Use atomic writes when available and manage only Shelldeck-owned config
  entries/files.

**Primary files:** new `shelldeck/integrations/`, `shelldeck/cli.py`,
`shelldeck/server.py`, `shelldeck/static/agents.js`, and integration tests.

**Done when:** an integration install/status/uninstall cycle is idempotent and
leaves unrelated configuration untouched.

### Task 3.2: Codex integration

- [x] Codex hook format taken from herdr's installer (`~/.codex/hooks.json` +
  `[features] hooks = true`); a real-install check is still pending (release checklist).
- [x] Report the native session identity (from Codex's own rollout log, as dotpals does; no hook needed).
- [x] Support validated Codex resume argv.
- [x] Keep current screen/activity detection as lifecycle fallback.
- [x] Tests with a temporary home and fake rollouts (nothing is written to Codex's config).

**Done when:** Codex reports a session identity and can resume a compatible
conversation without affecting unrelated Codex hooks.

### Task 3.3: Claude Code integration

- [x] Verify the current Claude Code hook schema and event names before writing
  an installer.
- [x] Install/remove only Shelldeck-owned hook entries and scripts.
- [x] Report native session identity; preserve screen detection for state.
- [x] Test default and overridden Claude configuration directories.

**Done when:** Claude Code installation is safe, idempotent, and reversible.

### Task 3.4: OpenCode integration

- [ ] Verify supported OpenCode plugin APIs and version compatibility.
- [x] Implement lifecycle-state and selected-session reporting in a
  Shelldeck-owned plugin.
- [x] Treat this integration as the first authoritative lifecycle proof case.
- [ ] Test permissions/questions, completion, interruptions, and multiple
  terminal sessions.

### Task 3.5: Gemini CLI, Cursor, Copilot CLI, Qwen, Qoder, Droid, Devin integrations

- [x] Hooks for Gemini CLI (`~/.gemini/settings.json`), Cursor (`~/.cursor/hooks.json`)
  and Copilot CLI (`~/.copilot/hooks/shelldeck.json`), in the formats dotpals uses.
- [x] Session-id hooks for Qwen Code, Qoder CLI, Factory Droid and Devin CLI (herdr formats).
- [x] Install/uninstall round-trip tests with unrelated config preserved.
- [ ] Manual check against installed binaries.
- [x] Kimi (TOML hooks), Kilo (plugin), MastraCode, Grok, Antigravity.
- [x] Hermes (Python plugin), Letta.
- [x] Pi/OMP (TypeScript extensions).

**Done when:** OpenCode reports `working`, `blocked`, and `idle` accurately and
can provide a resumable native session reference.

### Priority order for integrations (decided)

1. Claude Code, Codex, Gemini CLI, Devin CLI: supported, exact state and resume.
   - [x] Installers, hook mapping, resume argv, end-to-end test with a fake agent process.
   - [~] Manual check against the real binaries: Claude done (real CLI, per-run hooks, capture); Codex, Gemini and Devin pending (`gemini --resume <id>` especially).
2. Everything else (Cursor, Copilot, OpenCode, Kilo, Kimi, MastraCode, Pi, OMP,
   Qwen, Qoder, Droid, Grok, Antigravity, Letta, Hermes): preview, to finish
   after the remaining features. They are already installable on request.

## Milestone 4 — Persistence and safe restore

### Task 4.1: Persist supported native sessions

- [x] Add additive database migration(s) for `agent_sessions` (integration
  status table waits for the integration manager).
- [x] Persist only validated native-session information and bounded metadata.
- [x] Do not persist high-frequency heartbeat reports by default.
- [x] Add settings for `never`, `ask`, and `auto` native-session restore.
- [x] Default new users to `ask`.

**Primary files:** `shelldeck/db.py`, `shelldeck/server.py`, settings UI and
tests.

**Done when:** a valid native session survives a server restart in storage, and
invalid/missing resume data cannot be launched.

### Task 4.2: Restore sessions safely

- [x] Restore normal terminals as shells first.
- [x] Resume an agent only after its shell is ready and the integration is
  installed, compatible, and enabled.
- [x] Preserve terminal/layout records when cwd, executable, or integration is
  unavailable; provide a retry/error action rather than silently changing work.
- [x] Do not auto-complete or silently reassign an open hand-off during restore.
- [x] Add restart tests for success, disabled restore, invalid argv, missing cwd,
  and missing executable.

**Done when:** restart recovery is explicit, safe, and recoverable for both
supported agent sessions and ordinary terminals.

## Milestone 5 — Knowledge and context persistence

### Task 5.1: Database schema and context store

- [x] Add additive database migrations for `projects`, `sessions`, `tasks`,
  `handoffs`, `knowledge`, `decions`, `events`, `relationships`, and
  `source_references` tables (see design spec section 41).
- [x] Implement the context store (shipped as `shelldeck/context.py`) with SQLite FTS5 for full-text
  search over knowledge, decisions, and handoff content.
- [x] Add project registry with path, repository, and git metadata.
- [x] Ensure all writes are idempotent and migration-safe.

**Primary files:** `shelldeck/db.py`, new `shelldeck/context_store.py`,
migration scripts, tests.

**Done when:** schema applies cleanly; FTS search returns ranked results;
project registry tracks paths and git state.

### Task 5.2: Context manager and projection

- [x] Implement the context manager functions (in `shelldeck/context.py`) with:
  `load_context()`, `save_state()`, `create_checkpoint()`, `create_handoff()`,
  `resume_session()`, `record_event()`, `record_memory()`, `record_decision()`,
  `search_context()`, `project_context()`, `related_projects()`.
- [x] Add `ContextPackage` dataclass aggregating project, task, state, memory,
  decisions, handoff, related projects, relevant knowledge, git state, and
  recent events (section 47).
- [x] Implement Markdown projection: generate `STATE.md`, `TASK.md`,
  `MEMORY.md`, `DECISIONS.md`, `handoff.md` from SQLite on demand (section 43).
- [x] Add context budget enforcement with configurable allocation (section 48).
- [x] Implement deduplication and conflict detection for cross-project knowledge
  (sections 49, 51).

**Primary files:** new `shelldeck/context_manager.py`, `shelldeck/context_store.py`,
projection templates, tests.

**Done when:** an agent can receive a complete ContextPackage; Markdown
projections are accurate; budget prevents overflow; cross-project conflicts
are surfaced not merged.

### Task 5.3: Cross-project retrieval (sd recall)

- [x] Implement `sd recall "<query>"` with SQLite FTS5 ranking and project
  scoping.
- [x] Return structured results with project, relevance, source files, and
  knowledge snippet.
- [x] Add knowledge verification lifecycle: `NEW` → `VERIFIED` → `STALE`
  → `REVIEWED` with `INVALIDATED` option (section 32).
- [x] Implement source file hash tracking to auto-mark knowledge `STALE`
  when source files change (section 31).
- [x] Add `sd knowledge verify <id>` CLI command.

**Primary files:** `shelldeck/cli.py`, `shelldeck/context_store.py`,
`shelldeck/context_manager.py`, tests.

**Done when:** a query returns ranked cross-project results with source
attribution; stale detection works on file changes; verification CLI works.

### Task 5.4: Context CLI commands

- [x] Add `sd context [PROJECT]` — show current or named project context.
- [x] Add `sd memory [search <query>]` — show/search project memory.
- [x] Add `sd projects` and `sd project <name>` — list/show projects.
- [x] Add `sd handoff` / `sd handoffs` / `sd done <id>` — enhanced with
  full context snapshots (task, state, memory, decisions, git, tests, files,
  events, next action, related projects — section 44).
- [x] Add `sd resume` — resume most recent incomplete task.
- [x] Add `sd switch <agent>` — create handoff and switch active agent.
- [x] Add agent-accessible commands: `sd context`, `sd recall`, `sd memory`,
  `sd handoff`, `sd done`, `sd tell <agent>`, plus optional `sd remember`,
  `sd decide`, `sd discover`, `sd task update` (section 34).

**Primary files:** `shelldeck/cli.py`, `shelldeck/context_manager.py`, tests.

**Done when:** all CLI commands work and return structured output; handoff
content includes full context; agent commands are lightweight and usable.

### Task 5.5: Automatic agent skill extension

- [x] Extend the installed Shelldeck skill to teach agents the startup/
  during-work/pre-stop workflow (section 35):
  - Startup: read context, check active task, check STATE, check MEMORY,
    check handoff, continue from next action.
  - During work: update task progress, record discoveries/decisions,
    use `sd recall` when context missing.
  - Pre-stop: update STATE, summarize remaining work, record discoveries,
    create/update handoff.
- [x] Ensure skill does not require agents to understand internal database.

**Primary files:** skill template in `shelldeck/team.py`, tests.

**Done when:** a fresh agent following the skill can load context and
continue work without manual instruction.

### Task 5.6: Secret protection and git integration

- [x] Implement secret detection/redaction before indexing command output
  or files (section 39): API keys, passwords, tokens, private keys, cookies,
  credentials, `.env` values.
- [x] Exclude sensitive files by default (`.env*`, `*.pem`, `*.key`,
  `credentials.*`, `secrets.*`).
- [x] Add optional git metadata to context checkpoints: repository, branch,
  commit, dirty state, changed files (section 40).

**Primary files:** `shelldeck/context_store.py`, secret scanning module,
git module, tests.

**Done when:** secrets are never stored in knowledge; git state is captured
at checkpoints; sensitive files are excluded.

### Task 5.7: Background processing and performance

- [x] Add background worker infrastructure for knowledge extraction (Markdown projection and context calls run off the event loop; extraction is done by the agents on demand, no LLM in shelldeck),
  embedding generation, Markdown projection, event compaction, stale
  detection, and indexing (section 56).
- [x] Ensure agent terminal interaction never blocks on context processing
  (section 55): startup <100ms, recall <100ms, SQLite search <50ms.
- [x] Implement failure handling: context system failure logs warning but
  agent execution continues (section 57).

**Primary files:** new `shelldeck/workers.py`, `shelldeck/context_store.py`,
performance tests.

**Done when:** background tasks run without blocking PTY; performance
targets met; context failures don't crash agents.

### Task 5.8: Human-control UI and privacy

- [x] Add UI sections for Projects, Sessions, Tasks, Memory, Knowledge,
  Decisions, Relationships, Handoffs (section 52).
- [x] Ensure cross-project context never leaves machine unless explicitly
  configured (section 58).
- [x] Default all storage to local SQLite/files; optional future PostgreSQL,
  Qdrant, Chroma, Redis are opt-in (section 54).

**Primary files:** `shelldeck/static/agents.js`, new context UI components,
settings.

**Done when:** UI exposes all context dimensions; privacy defaults are local-only.

## Milestone 6 — Automation and operator experience

### Task 6.1: Event-driven agent commands

- [x] Add `sd agent status` with stable human and JSON forms.
- [x] Add server-owned `sd agent wait` with timeout, closure, and replacement
  process handling.
- [x] Add atomic `sd agent prompt --wait` semantics.
- [x] Add agent attach/rename commands (rename; manually started agents are attached automatically by process detection).
- [x] Add an event subscription endpoint/transport and `sd events subscribe`.
- [x] Update the installed skill only after these commands are tested.

**Done when:** supervisors can wait for an agent state transition without
polling screen text, and a new agent process cannot satisfy an old wait.

### Task 6.2: Attention queue and project rollups

- [x] Add a prioritized attention section to `static/agents.js`.
- [x] Display semantic state, source, reason, elapsed time, model/context,
  role/title, and integration health.
- [x] Add direct actions appropriate to the viewer's permissions (open, open next, message, native commands, resume; peek/answer via CLI): open, peek,
  answer, message, resume, diagnose, reassign, and close.
- [x] Add project sidebar rollups with accessible text/icon state indicators.
- [x] Keep completed agents visible until reviewed; do not re-alert unchanged
  state.

**Done when:** a user can identify and open the next agent needing a decision in
one click, including across projects.

## Milestone 7 — Maintainable fallback detection

### Task 7.1: Versioned detection manifests

- [x] Create bundled `shelldeck/agent_detection/` manifests for known agent
  screen states.
- [x] Add local config overrides under the Shelldeck config directory.
- [x] Make invalid local overrides non-fatal and visible in diagnostics.
- [x] Migrate existing approval/question regex behavior into a manifest or a
  clearly named compatibility rule.

### Task 7.2: Explain diagnostics

- [x] Add `sd agent explain TARGET` and JSON/file variants.
- [x] Show process identity, active authority, report age, manifest source and
  version, matched rule, and fallback reason.
- [x] Redact raw screen output by default.

**Done when:** maintainers can determine why a state was selected without adding
temporary logging or exposing private terminal content.

## Release checklist

- [x] Run the full unit/API suite on Linux and Windows. (0.0.7rc1 release run: Windows + Ubuntu, py3.12–3.14)
- [ ] Manually validate Codex, Claude Code, and OpenCode install/uninstall and
  lifecycle behavior.
- [ ] Validate parent/sub-agent question forwarding and hand-off completion.
- [ ] Validate restart behavior, remote SSH use, and share-mode auth boundaries.
- [ ] Verify UI state text/icons with keyboard navigation and a screen reader.
- [x] Update `README.md`, `CLAUDE.md`, `CHANGELOG.md`, and the dedicated agent
  integration/automation/detection docs described in the design spec.
- [x] Document knowledge system, cross-project retrieval, and context CLI.
- [x] Add release notes that list exact supported integrations and clearly state
  whether each supports detection, authoritative lifecycle state, and native
  restore.

## Dependency order

```text
Lifecycle domain model
  -> server-owned state
  -> secure reporting API
  -> integration manager
  -> Codex / Claude / OpenCode integrations
  -> persistence and safe restore
  -> knowledge and context persistence
  -> event-driven automation
  -> attention UI and project rollups
  -> detection manifests and explain diagnostics
```

Do not begin native restore before report validation and the integration manager
exist. Do not make an integration authoritative before its lifecycle events are
covered by tests. The attention UI can be prototyped earlier, but its final
behavior must use server-owned semantic status. Knowledge system depends on
context store and database schema.
