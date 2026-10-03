# Agent enhancement plan

## Purpose

This document is the implementation plan for evolving Shelldeck's existing
agent-team features into a reliable, extensible agent-integration system. It is
informed by the agent integration model in [Herdr](https://github.com/herdrdev/herdr),
but it deliberately preserves Shelldeck's project-first browser UI and its
opinionated parent/sub-agent hand-off workflow.

The goal is **not** to replace agents, wrap their terminals, or make an agent
framework that owns project work. Shelldeck should continue to run the user's
normal shell and normal agent CLI. Its role is to identify the agent in a
terminal, understand its state when trustworthy information is available,
retain enough information to restore a supported conversation, and offer safe
coordination tools to people and to agents.

## Executive summary

Shelldeck already has a strong base:

- process and model detection for many coding-agent CLIs;
- a dedicated Agents page, terminal chips, agent context usage, and an
  outside-Shelldeck process view;
- durable hand-offs, sub-agent spawning, terminal nicknames, and parent-child
  ownership rules;
- `sd agents`, `peek`, `tell`, `answer`, `spawn`, `handoff`, `done`, and
  `close` commands; and
- an installable skill that teaches supported agents to use those commands.

The main gap is that current lifecycle state is inferred from output timing and
screen text. That is a useful fallback, but it cannot reliably represent every
agent TUI, permission prompt, or completed run. The recommended sequence is:

1. establish a typed, source-owned lifecycle/reporting contract;
2. add an integration manager and official integrations for Codex, Claude Code,
   and OpenCode;
3. persist native conversation references and resume approved agent sessions;
4. expose event-driven status/wait automation; and
5. improve the UI with state rollups, an attention queue, and integration
   health details.

## Product principles and non-goals

### Principles

1. **Real terminals remain authoritative for interaction.** An integration may
   report metadata and state, but the user must still be able to see and use
   the terminal normally.
2. **Prefer first-party lifecycle events over screen parsing.** A hook or plugin
   that says an agent is blocked is more reliable than a regex over terminal
   output. Screen parsing remains an essential fallback.
3. **Keep semantic state separate from presentation.** A label such as
   `reviewing auth` must not accidentally change whether an agent is treated as
   `working` or `blocked`.
4. **Agent coordination must be safe by default.** A sub-agent cannot create an
   unbounded tree, cannot close a peer's terminal, and escalates decisions to
   its parent before the user.
5. **Do not execute arbitrary restored commands.** Resume commands are supplied
   only by trusted built-in integrations, stored as validated argv arrays, and
   never reconstructed from terminal scrollback.
6. **The system must remain useful with no integrations installed.** Detection,
   manual launching, hand-offs, terminal access, and the heuristic fallback
   must continue to work.
7. **Local-first and privacy-aware.** Agent state and screen data stay on the
   Shelldeck host. Existing push notifications must continue to expose only the
   minimum nick/agent/project metadata.

### Non-goals for the first releases

- Building an MCP server or a general cloud agent platform.
- Replacing the existing hand-off files or making agents share a hidden global
  transcript.
- Automatically granting approval prompts or allowing agents to bypass an
  agent CLI's own permission model.
- A third-party plugin marketplace. A public reporting API comes first.
- Remote rule downloads. Local overrides and versioned bundled manifests are
  sufficient for the initial implementation.

## Current implementation map

This section is the starting point for implementation work. Read these files
before changing behavior.

| Area | Current location | Responsibility | Planned impact |
| --- | --- | --- | --- |
| Agent process identification | `shelldeck/agents.py` | `AGENTS`, process matching, model/config lookup, context use, running/outside discovery | Add agent capability metadata, integration registry lookup, and detection-manifest fallback helpers. |
| Agent team domain | `shelldeck/team.py` | model tiers, safe launch command construction, hand-off files, skill text/install | Extend the skill with lifecycle/wait commands; keep spawn and hand-off safety rules intact. |
| API and lifecycle loop | `shelldeck/server.py` | `/api/agents`, terminal input, state watcher, notifications, spawning, hand-offs | Add report ingestion, authority resolution, event subscriptions, waits, integration endpoints, and restore orchestration. |
| Persistence | `shelldeck/db.py` | SQLite schema and additive migrations | Add `agent_sessions`, `agent_reports`, integration state, and optional event retention. |
| Agent UI | `shelldeck/static/agents.js` | agent list, launch cards, skill controls, hand-off display | Add status/source/health columns, attention queue, integration controls, resume actions, and diagnostics. |
| Shared client state | `shelldeck/static/app.js` | terminal state/chips, alarms, current heuristic status | Replace the UI-only lifecycle calculation with server-supplied semantic state while retaining fallback display. |
| Stats poller | `shelldeck/static/monitor.js` | regular stats refresh and UI updates | Consume authoritative status snapshots and refresh rollups without duplicate state inference. |
| Tests | `tests/test_shelldeck.py`, `tests/test_cli.py` | API/server and CLI coverage | Add persistence, authority, expiry, restore, integration-file, event, and command tests. |

`CLAUDE.md` also contains the project-level behavioral notes for agent detection,
question forwarding, terminal environment variables, and the existing team
system. Treat it as implementation context, not as a substitute for tests.

## Target architecture

### Terminology

| Term | Definition |
| --- | --- |
| **Terminal session** | A Shelldeck terminal record and its currently attached PTY process. Identified by `session_id`. |
| **Agent instance** | A recognized coding-agent process currently controlling a terminal session. It may disappear when that process exits. |
| **Agent kind** | A stable lower-case identifier such as `codex`, `claude`, or `opencode`. |
| **Integration** | Shelldeck-owned code/configuration installed into an agent's supported hook/plugin mechanism. It may report session identity, lifecycle state, or both. |
| **Report** | A time-bounded assertion about an agent from a named source. |
| **Authority** | The report source currently allowed to determine semantic lifecycle state. |
| **Native session** | An agent's own conversation/thread/session ID plus a safe resume command. It is not a terminal transcript. |
| **Hand-off** | Shelldeck's durable assigned task record between terminals. It remains a separate concern from agent lifecycle. |

### Canonical lifecycle states

Use these values everywhere in the API, DB, CLI, WebSocket/alarm messages, and
UI. Do not use `approval` as a semantic state; it becomes a blocking reason.

| State | Meaning | Attention behavior |
| --- | --- | --- |
| `unknown` | An agent is detected but no trustworthy lifecycle source is available. | No interruptive alert. |
| `idle` | The agent is ready for another prompt or is paused without an outstanding request. | No alert. |
| `working` | The agent is executing, reasoning, running a tool, or awaiting tool output. | Show activity only. |
| `blocked` | The agent needs input, permission, a choice, or an external prerequisite. | Attention queue, browser notification, parent escalation when applicable. |
| `done` | The active task finished successfully, or the associated hand-off is complete. | Keep visible until viewed/reviewed. |
| `exited` | The agent process exited while a task or monitored session was active. | Attention queue with restart/reassign actions. |

`blocked_reason` is optional and has constrained values such as `approval`,
`question`, `authentication`, `tool_error`, `external_wait`, and `unknown`.
Keep a short, sanitized `detail` only when it is safe to present in the UI.

### State authority and fallback

For each active agent session, calculate state from the highest-precedence
unexpired source:

1. an enabled built-in integration reporting lifecycle state;
2. a validated future custom integration reporter;
3. a supported agent's direct local API event;
4. bundled or locally overridden screen-detection manifest;
5. existing output-activity and question heuristics;
6. `unknown`.

An integration source must identify both itself and the agent kind. Reports are
valid only for a live Shelldeck terminal session and expire unless refreshed.
If the source disappears or its TTL expires, resolve state again from the next
source; do not leave a terminal permanently `working`.

The server, rather than browser JavaScript, owns this decision. The browser
receives a state snapshot and state-change event. This avoids different tabs
showing different status and makes CLI waits deterministic.

### Reporting contract

Create an internal authenticated endpoint, initially only callable on the local
host by installed Shelldeck integration scripts:

```json
POST /api/agent-reports
{
  "session_id": "terminal-id",
  "source": "shelldeck:opencode",
  "agent": "opencode",
  "state": "working",
  "blocked_reason": null,
  "agent_session_id": "agent-native-conversation-id",
  "resume_argv": ["opencode", "--session", "agent-native-conversation-id"],
  "metadata": {
    "title": "Implement callback validation",
    "display_agent": "OpenCode: auth",
    "tokens": {"model": "...", "context": "42%"}
  },
  "ttl_ms": 30000
}
```

Validation requirements:

- resolve the terminal from a secret passed only into that terminal's process
  environment; do not trust a user-supplied terminal ID alone;
- validate the integration `source` against the installed integration and its
  expected agent kind;
- cap title, labels, detail, token count, key syntax, and values;
- accept lifecycle changes only from the active authority source;
- cap report TTL and reject non-positive values;
- log source, agent, session, state transition, and validation error, but never
  log prompts, screen output, or private conversation IDs at info level;
- validate `resume_argv`: executable name only for argv[0], no paths/control
  characters, bounded element count/bytes, and only allow a built-in
  integration to issue it initially.

The integration token should be random per terminal process. Put it in a
dedicated environment variable, e.g. `SHELLDECK_AGENT_REPORT_TOKEN`, and keep
it separate from the host CLI token. Revoking or closing a terminal invalidates
its token.

### Display metadata

Metadata changes presentation only. It may set:

- a concise work title;
- a display name/role such as `reviewer` or `test-runner`;
- user-visible state labels such as `reviewing migration` for `working`;
- bounded key/value chips, e.g. model, branch, context percentage, task ID, or
  elapsed time.

It must never alter lifecycle authority, hand-off completion, approval
notifications, or restore behavior. Metadata should receive the same source
and TTL treatment as reports so abandoned integrations cannot leave stale UI.

## Feature plan

### Phase 0: baseline and contracts

**Objective:** introduce types and tests without changing current user-visible
behavior.

1. Create a small `shelldeck/agent_state.py` module rather than allowing
   lifecycle constants and precedence rules to spread through `server.py` and
   `app.js`.
2. Define dataclasses or typed dictionaries for `AgentStatus`, `AgentReport`,
   `AgentSessionReference`, and `IntegrationStatus`.
3. Document a versioned JSON schema for report payloads under
   `docs/agent-report.schema.json` when implementation begins.
4. Move the existing `working` / `approval` / `idle` logic into an explicitly
   named heuristic fallback adapter. Map `approval` to `blocked` with reason
   `approval` only at the new boundary.
5. Add unit tests for state validation and authority selection before adding
   integrations.

**Acceptance criteria:** no behavior regression in agent spawning, hand-offs,
question forwarding, or the current Agents page; unit tests verify authority
ordering, source expiration, and invalid report rejection.

### Phase 1: server-owned lifecycle state

**Objective:** make semantic lifecycle state stable across browser tabs and
available to all clients.

1. Add an in-memory report registry keyed by terminal session and source.
2. Change `_check_agents()` in `shelldeck/server.py` to combine process
   discovery, report expiry, manifest/fallback detection, and hand-off state.
3. Broadcast a structured `agent_state` alarm event:

   ```json
   {"type":"agent_state","session_id":"...","status":{"state":"blocked","reason":"approval","source":"heuristic"}}
   ```

4. Return full status on `/api/agents`, including source, expiry, block reason,
   display metadata, and native-session availability.
5. Have `app.js` and `monitor.js` render server state; remove duplicate
   lifecycle decisions from the browser once compatibility is confirmed.
6. Keep current push notification and parent-forwarding behavior, but trigger
   it only on a transition into `blocked`, not each refresh.

**Acceptance criteria:** opening two browser tabs gives the same agent status;
an expired reporter falls back cleanly; a blocked parent/child flow still
forwards the question once; the status endpoint is safe when no browser exists.

### Phase 2: integration manager

**Objective:** make installation and health of official integrations explicit.

Implement an `shelldeck/integrations/` Python package. Each integration owns:

- agent key and integration version;
- discoverable agent config directory/environment variables;
- `detect()`, `install()`, `uninstall()`, `status()` operations;
- exact managed files and config entries;
- minimum supported CLI version when known;
- lifecycle/session capabilities;
- resume argv validation/creation; and
- tests using temporary home/config directories.

Expose:

```text
sd integration detect
sd integration list
sd integration install codex
sd integration uninstall codex
sd integration status [AGENT]
```

Add corresponding `GET/POST/DELETE /api/integrations` endpoints and an Agents
page section that displays detected, installed, outdated, unavailable, and
error states. The UI must clearly distinguish a **skill** (instructions for an
agent) from an **integration** (hook/plugin that reports runtime information).

Installers must make atomic changes where the platform permits, preserve other
hook entries, identify their own config blocks/files unambiguously, and remove
only what Shelldeck owns. A failed write must not leave an invalid JSON/TOML
configuration file behind.

#### Initial official integrations

1. **Codex** — highest priority because it is a primary Shelldeck agent,
   already detected and spawnable. Start with session identity/resume hook;
   use screen detection until a reliable lifecycle event is available.
2. **Claude Code** — session identity/resume hook plus existing screen fallback.
   Preserve unrelated hooks and do not weaken Claude's permission model.
3. **OpenCode** — prioritize a plugin that reports both selected session and
   lifecycle events. This is the best candidate for proving authoritative state.

Only add an agent integration after confirming the agent's current hook/plugin
format against official upstream documentation and a real installation. Do not
copy configuration assumptions from another agent.

**Acceptance criteria:** install/status/uninstall round trips are idempotent;
uninstall leaves unrelated configuration intact; stale/outdated integration
versions are visible; all integration file mutation has regression tests.

### Phase 3: native session persistence and restore

**Objective:** restore supported agent conversations after a Shelldeck server
restart without pretending that arbitrary process state survives.

Add a durable `agent_sessions` record with, at minimum:

| Field | Purpose |
| --- | --- |
| `session_id` | Shelldeck terminal session owner. |
| `agent` | Agent kind. |
| `source` / `integration_version` | Provenance and compatibility checks. |
| `native_session_id` | Opaque agent conversation/thread/session identifier. |
| `resume_argv_json` | Validated executable argv, never shell source. |
| `last_state` / `last_seen_at` | Restore/UI context. |
| `metadata_json` | Bounded optional display metadata. |
| `resume_enabled` | User choice for automatic restore. |

On startup:

1. Restore Shelldeck terminal records/layout as normal shells first.
2. Evaluate stored native sessions only if the matching integration is installed,
   compatible, and resume is enabled.
3. Start the validated argv in the restored terminal after its shell prompt is
   ready, using the existing safe starter pattern.
4. If a directory, executable, integration, or resume fails, preserve the
   terminal and present a clear error/retry action rather than silently running
   a fallback command.
5. Never auto-resume a hand-off into an unrelated/changed project directory;
   surface it for review instead.

Offer settings for `never`, `ask`, and `auto` restore behavior. Default to
`ask` for the first release, because resuming an agent can incur cost and may
act on a changed working tree.

**Acceptance criteria:** native session records survive restart; invalid argv is
not run; an unavailable executable yields a recoverable UI state; ordinary
shell restoration remains unchanged.

### Phase 4: event-driven agent automation

**Objective:** let a parent agent, script, or future integration coordinate
without polling terminal screens.

Build on Shelldeck's existing authenticated local HTTP API. Start with a
server-owned long-poll or Server-Sent Events endpoint; a WebSocket can follow if
the existing alarm socket can safely support request/response correlation.

Proposed commands:

```text
sd agent status [TARGET] [--json]
sd agent wait TARGET --until idle|blocked|done|exited --timeout 10m
sd agent prompt TARGET "message" [--wait done] [--timeout 10m]
sd agent attach TARGET --name reviewer
sd agent rename TARGET reviewer
sd events subscribe --types agent.state,handoff.completed
```

`prompt --wait` must submit the prompt and register the wait atomically. If an
agent is already blocked, return a specific error without sending input. Waits
must pin the current agent process identity so an unrelated later process in the
same terminal cannot satisfy an old wait.

Publish events such as `agent.detected`, `agent.state`, `agent.exited`,
`agent.session_updated`, `handoff.created`, `handoff.completed`, and
`integration.changed`. Include event IDs, timestamps, and a compact resource
snapshot; do not include screen text by default.

**Acceptance criteria:** waits do not poll; a replacement process cannot satisfy
an existing wait; timeouts and terminal closure are unambiguous; CLI JSON is
stable enough for the installed Shelldeck skill to rely on.

### Phase 5: attention-oriented UI

**Objective:** make it obvious what needs a human decision without manually
opening every terminal.

Enhance `shelldeck/static/agents.js` with:

- an **Attention** section ordered as blocked, done/review-ready, exited,
  working, idle, unknown;
- status, source, reason, elapsed time, role/title, model, context estimate,
  and integration health columns;
- direct actions: open, peek, answer, message, resume, retry integration,
  view diagnostic, reassign/close where allowed;
- clear source badges: `integration`, `screen`, `activity`, `unknown`;
- an integration card that is separate from the existing skill-installed badge;
- a history/detail dialog showing the last bounded state transitions and
  hand-off associations.

Add project-level visual rollups in the sidebar: `blocked` dominates `working`,
then `done`, with a count badge. Do not mark a whole project blocked merely
because an unassigned idle terminal exists. A completed agent should remain
visible until the user opens/reviews it, then become normal idle/history state.

Accessibility requirements: do not rely on color alone; use text/icon labels;
ensure state changes are announced appropriately without flooding screen
readers; respect `prefers-reduced-motion`.

**Acceptance criteria:** an operator with several projects can reach the next
decision in one click; current terminal behavior remains fast; attention state
does not repeatedly alert for an unchanged condition.

### Phase 6: detection manifests and diagnostics

**Objective:** make fallback detection maintainable as agent terminal UIs
change.

Create bundled versioned manifest files under `shelldeck/agent_detection/`.
Each manifest describes an existing recognized agent's screen evidence for
`working`, `blocked`, and `idle`, precedence, regions/line windows, and safely
redacted diagnostic labels. Load local overrides from
`~/.config/shelldeck/agent-detection/<agent>.toml` (respecting
`SHELLDECK_HOME`). Local overrides win; invalid overrides are ignored with a
clear warning and fallback to bundled rules.

Add:

```text
sd agent explain TARGET
sd agent explain --file screen.txt --agent codex --json
```

Diagnostics should show process identification, active authority, report age,
manifest source/version, matched rule ID, high-level evidence, and fallback
reason. Never show raw screen content in a default diagnostic response.

Do not implement remote manifest updates in this phase. Ship changes with
Shelldeck releases until a signed, compatible update design exists.

## Database migration outline

Follow the existing additive migration pattern in `db.init_db()`: create new
tables with `CREATE TABLE IF NOT EXISTS`, inspect columns with `PRAGMA
table_info`, and add columns only when missing. Do not rebuild or destructively
migrate existing session tables.

Suggested tables:

```sql
CREATE TABLE agent_sessions (
  session_id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  source TEXT NOT NULL,
  integration_version INTEGER,
  native_session_id TEXT,
  resume_argv_json TEXT,
  metadata_json TEXT,
  last_state TEXT NOT NULL DEFAULT 'unknown',
  last_seen_at TEXT NOT NULL,
  resume_enabled INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE integration_state (
  agent TEXT PRIMARY KEY,
  version INTEGER,
  installed_at TEXT,
  checked_at TEXT,
  status TEXT NOT NULL,
  detail TEXT
);
```

Keep transient, high-frequency reports in memory initially. If a compact
transition history is useful for diagnostics, add a bounded `agent_events`
table later with retention pruning; do not persist every heartbeat report.

## Security and privacy review

This work changes the control surface of terminals and agent configuration, so
perform a dedicated security review before Phase 2 lands.

- Reuse Shelldeck's existing authenticated API/host protections; agent reports
  require a terminal-bound secret in addition to browser/session auth policy.
- Never expose report endpoints through a shared tunnel without the normal
  authentication guard.
- Treat all hook/plugin input as untrusted: validate JSON shape, size, text,
  token keys, state values, IDs, and resume argv.
- Do not permit a report for terminal A to update terminal B.
- Do not write arbitrary shell strings into agent configuration files.
- Use atomic writes and backups for mutable JSON/TOML hook configuration.
- Redact native conversation IDs from normal logs, toasts, and non-host UI
  responses.
- Keep the existing rule that a child agent cannot close a non-child terminal;
  extend it to restart/reassign actions.
- Do not let `done` be inferred solely from a lifecycle report when an open
  hand-off exists. The worker must still use `sd done`, or the parent/user must
  explicitly resolve the task.
- Test Windows and POSIX quoting independently; resume commands use argv,
  while existing spawning uses deliberately constrained safe prompts.

## Testing plan

### Unit tests

- report schema/value/size validation;
- source precedence, source replacement, TTL expiry, and fallback selection;
- transition behavior and notification de-duplication;
- resume argv allow-list and invalid-value rejection;
- integration discovery/config path resolution on Windows and POSIX;
- atomic config mutation and uninstall ownership behavior;
- manifest parsing, override priority, invalid override fallback, and explain
  output redaction.

### API and persistence tests

- authenticated and rejected report submission;
- per-terminal report tokens cannot cross terminal boundaries;
- `/api/agents` exposes status without leaking private fields;
- lifecycle state persists as intended while transient heartbeats do not;
- restart restores a valid, enabled native session and leaves failure state
  visible for invalid/missing dependencies;
- wait timeout, state satisfaction, agent exit, and agent replacement cases;
- hand-off completion and lifecycle `done` remain distinct.

### End-to-end/manual matrix

Test on Linux and Windows with at least Codex, Claude Code, and OpenCode:

1. fresh install and a config that already contains unrelated hooks/plugins;
2. integration installation twice, status, upgrade, and uninstall;
3. agent starts normally without Shelldeck integration installed;
4. working, approval/question, idle, completion, interrupt, and process crash;
5. detach/reconnect and server restart;
6. a parent with several children and an approval prompt from one child;
7. remote/SSH and share mode authentication boundaries;
8. multiple browser tabs and no-browser CLI waits;
9. changed working directory, missing project directory, and missing agent
   executable during restore;
10. a screen-reader pass over status and attention UI.

Run the repository's standard checks after each implementation slice:

```sh
uv run ruff check
uv run pytest -q
```

## Documentation deliverables

When phases ship, update:

- `README.md`: user-facing agent capabilities, integration/resume limits, CLI
  examples, security cautions, and the distinction between skills/integrations;
- `CLAUDE.md`: architecture notes, state ownership, test expectations, and
  rules for changes to integrations;
- `CHANGELOG.md`: feature and migration notes;
- a new `docs/agent-integrations.md`: per-agent install/uninstall behavior,
  config locations, capabilities, troubleshooting, and compatibility matrix;
- a new `docs/agent-automation.md`: CLI/API event/wait contract and examples;
- a new `docs/agent-detection.md`: manifest format and local override guidance.

The installed Shelldeck skill must be updated in `shelldeck/team.py` only after
the corresponding commands are stable and tested. Avoid teaching agents a
command that is available only in a development build.

## Upstream research references

The following Herdr documentation informed this plan. Verify current upstream
formats before implementing a particular integration because agent CLIs evolve
quickly.

- [Herdr agents and state rollups](https://herdr.dev/docs/agents/)
- [Herdr integration install, lifecycle, and native resume behavior](https://herdr.dev/docs/integrations/)
- [Herdr agent automation primitives](https://herdr.dev/docs/agent-automation/)
- [Herdr socket API, reports, metadata, waits, and plugins](https://herdr.dev/docs/socket-api/)
- [Herdr session state and restore model](https://herdr.dev/docs/session-state/)
- [Herdr agent skill guidance](https://herdr.dev/docs/agent-skill/)

Herdr is a useful reference for explicit lifecycle authority, resumable native
sessions, event-driven waits, and diagnostic manifests. Shelldeck should adopt
those concepts in a way that complements—rather than replaces—its richer
hand-off/task coordination model.

## Definition of done

The enhancement is complete only when:

1. a user can see why an agent has a given status and whether it comes from an
   integration or fallback detection;
2. a supported integration can be installed, checked, upgraded, and removed
   without damaging user configuration;
3. at least one agent reports authoritative lifecycle state and resumes a
   native session safely after restart;
4. agents/scripts can wait for state changes without polling screen text;
5. existing spawn, hand-off, parent escalation, and terminal safety behavior
   remains intact;
6. all new behavior is covered by automated tests on the supported platforms;
   and
7. the user-facing documentation clearly states capability and privacy limits.
