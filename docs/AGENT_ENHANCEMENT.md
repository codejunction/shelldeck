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


specification for  enhancement more to whats already above

# ShellDeck — Agent Continuity & Shared Context Specification

**Status:** Proposed  
**Project:** ShellDeck  
**Feature:** Agent Continuity, Handoff & Cross-Project Shared Context  
**Version:** 1.0

---

## 1. Overview

ShellDeck should evolve from an agent terminal/session manager into a **persistent context layer for AI agents**.

The goal is to allow agents to:

1. Continue work across agent switches without losing context.
2. Resume work after a terminal/session restart.
3. Hand work from one agent to another.
4. Maintain durable project-level state and memory.
5. Share useful knowledge between different repositories/projects.
6. Allow an agent working on one project to retrieve relevant knowledge from another project.
7. Preserve the reasoning, decisions, discoveries, tasks, and current state of previous agents without dumping entire historical conversations into the model context.

### Core principle

> **Agents are replaceable. Context is persistent.**

Claude can start a task, Codex can continue it, Gemini can review it, and another Claude session can resume it later — without requiring the user to manually reconstruct the context.

The same principle should work across projects.

For example:

```text
acme-web
    ↓
authentication architecture discovered
    ↓
stored as reusable knowledge
    ↓
john-web
    ↓
agent asks:
"Implement authentication similar to acme-web"
    ↓
ShellDeck retrieves relevant knowledge
    ↓
agent adapts it to john-web
```

---

# 2. Problem

The current handoff system primarily handles **task transfer between agents**.

This is useful, but insufficient for long-running multi-agent development.

An agent may know:

- what it was doing
- what files it changed
- what commands it ran
- what errors occurred
- what decisions were made
- what remains unfinished

But that information can disappear when:

- the agent exits
- another agent takes over
- the terminal is restarted
- the project changes
- another repository needs the same knowledge
- the user starts a new ShellDeck session

Simply storing the entire conversation is also not a good solution.

It creates:

- huge context windows
- unnecessary token consumption
- irrelevant historical information
- poor retrieval
- duplicated information
- stale information

ShellDeck therefore needs a **structured persistent context system**.

---

# 3. Goals

## 3.1 Primary goals

ShellDeck must provide:

### Agent continuity

An agent can stop and another agent can continue from exactly where the previous agent stopped.

### Persistent project state

The current state of a project survives sessions and agent changes.

### Persistent task plans

Tasks and plans survive agent restarts.

### Persistent memory

Important project knowledge survives individual sessions.

### Cross-project knowledge

Useful knowledge from one repository can be discovered and reused by another repository.

### Context-aware retrieval

Agents retrieve only the relevant context instead of loading the entire history.

### Agent-independent context

Context belongs to the **workspace/project**, not to Claude/Codex/Gemini/etc.

### Human-readable state

Important context must remain inspectable as Markdown.

### Machine-readable state

ShellDeck must maintain structured state in SQLite for efficient querying.

---

# 4. Non-Goals

The system should NOT initially attempt to:

- store every token of every agent conversation forever
- automatically share every piece of information between every project
- blindly copy architecture from one repository to another
- replace Git
- replace project documentation
- become a general-purpose vector database
- require an external cloud service
- require an LLM for basic state tracking
- put global context inside individual repositories

---

# 5. Core Concepts

The system should distinguish between:

```text
TASK
STATE
MEMORY
DECISION
SESSION
HANDOFF
KNOWLEDGE
RELATIONSHIP
EVENT
```

These are related but should not be conflated.

---

# 6. Context Architecture

ShellDeck should use a **two-layer architecture**.

```text
                         ShellDeck
                             │
                 ┌───────────┴───────────┐
                 │                       │
          Global Context DB        Project Projection
                 │                       │
        ~/.shelldeck/context.db    <repo>/.shelldeck/
                 │                       │
        ┌────────┼────────┐       ┌──────┼──────┐
        │        │        │       │      │      │
     Projects Knowledge Events   STATE  TASK  MEMORY
        │        │        │
        ├────────┼────────┤
        │        │        │
    acme-web  john-web  billing
```

## 6.1 Global ShellDeck state

Global context belongs under:

```text
~/.shelldeck/
```

Example:

```text
~/.shelldeck/
├── context.db
├── projects/
├── knowledge/
├── events/
└── embeddings/
```

This information must NOT be dependent on any individual Git repository.

---

# 7. Project-Level Context

Each project receives a local ShellDeck projection:

```text
<project>/
└── .shelldeck/
    ├── STATE.md
    ├── TASK.md
    ├── MEMORY.md
    ├── DECISIONS.md
    ├── AGENTS.md
    ├── sessions/
    ├── handoffs/
    └── index.md
```

These files are human-readable projections of the canonical state.

SQLite remains the source of truth.

---

# 8. STATE.md

`STATE.md` represents **what is happening right now**.

Example:

```markdown
# Current State

## Objective

Implement authentication for john-web.

## Current Step

Integrating OAuth callback handling.

## Current Branch

feature/authentication

## Last Commit

abc1234

## Working Tree

Modified:
- src/auth/login.ts
- src/auth/callback.ts

## Tests

Passing:
- auth/login.test.ts

Failing:
- auth/callback.test.ts

## Last Error

OAuth callback returns HTTP 401 when refresh token is missing.

## Next Action

Investigate refresh-token handling.

## Last Updated

2026-10-04T05:00:00Z

## Active Agent

claude

## Session

session_123
```

`STATE.md` should be updated automatically.

---

# 9. TASK.md

`TASK.md` represents the durable objective.

Example:

```markdown
# Task

## Objective

Implement authentication for john-web.

## Requirements

- OAuth login
- JWT authentication
- Refresh tokens
- Multi-tenant support

## Plan

1. Inspect existing authentication architecture.
2. Compare with acme-web.
3. Design john-web authentication layer.
4. Implement backend authentication.
5. Implement frontend session handling.
6. Add tests.
7. Run integration tests.

## Acceptance Criteria

- Users can log in.
- Sessions survive page reload.
- Refresh tokens work.
- Tenant information is preserved.
- Tests pass.

## Constraints

- Reuse existing john-web infrastructure.
- Do not copy acme-web implementation blindly.

## Status

IN_PROGRESS
```

The plan should be automatically maintained as work progresses.

---

# 10. MEMORY.md

`MEMORY.md` contains durable project knowledge.

Examples:

```markdown
# Project Memory

## Authentication

The application uses OAuth through auth-service.

JWT contains:

- user_id
- tenant_id
- roles

Refresh tokens are stored in Redis.

## Frontend

Authentication state is exposed through:

src/auth/session.ts

## Backend

Authentication middleware:

src/auth/middleware.ts

## Infrastructure

Redis is shared between API instances.

## Important Gotcha

The auth-service requires the tenant header to be present
before validating the JWT.
```

Memory should contain **facts and discoveries**, not transient conversation.

---

# 11. DECISIONS.md

`DECISIONS.md` stores architectural and implementation decisions.

Example:

```markdown
# Decisions

## 2026-10-04 — Use Redis for refresh tokens

### Decision

Refresh tokens will remain in Redis.

### Reason

Redis already exists in the infrastructure and provides
appropriate TTL support.

### Alternatives Considered

- PostgreSQL
- In-memory storage

### Consequence

Authentication depends on Redis availability.
```

This prevents future agents from repeatedly reconsidering already-settled decisions.

---

# 12. AGENTS.md

`AGENTS.md` contains ShellDeck-specific operating instructions for agents.

It can include:

- how to retrieve context
- how to update state
- how to create handoffs
- how to record decisions
- how to record discoveries
- how to ask another agent for information
- how to mark tasks complete

ShellDeck should automatically install/update the appropriate agent skill.

---

# 13. Sessions

Every agent execution should have a persistent session record.

Example:

```text
.shelldeck/sessions/
├── session_001.md
├── session_002.md
└── session_003.md
```

Each session contains:

```markdown
# Session

ID: session_003
Agent: claude
Project: john-web
Started: 2026-10-04T04:00:00Z
Ended: 2026-10-04T05:30:00Z

## Objective

Implement authentication.

## Work Performed

- inspected auth architecture
- modified middleware
- added tests
- fixed JWT validation

## Files Changed

- src/auth/middleware.ts
- tests/auth.test.ts

## Commands

- uv run pytest
- git diff

## Results

Tests: 42 passed

## Remaining Work

Frontend session handling.

## Handoff

handoff_004
```

---

# 14. Handoff

Handoff should become a **context snapshot**, not merely a task message.

A handoff must contain:

```text
TASK
STATE
MEMORY
DECISIONS
FILES
GIT STATE
TEST STATE
ERRORS
NEXT ACTION
RISKS
RECENT EVENTS
```

Example:

```markdown
# Handoff

## From

claude / session_003

## To

codex

## Objective

Complete authentication implementation.

## Completed

- backend OAuth flow
- JWT middleware
- refresh-token storage

## Current State

Backend implementation is complete.

Frontend session persistence remains.

## Files Changed

- src/auth/middleware.ts
- src/auth/oauth.ts
- src/auth/token.ts

## Tests

42 passed.

## Known Issues

Frontend session expires after browser refresh.

## Next Action

Inspect frontend session hydration.

## Relevant Memory

Authentication architecture follows the auth-service pattern
documented in project memory.

## Related Projects

acme-web

## Related Knowledge

authentication architecture

## Git

Branch: feature/authentication
Commit: abc1234
Dirty: true
```

---

# 15. Automatic Handoff Creation

Every agent should maintain a handoff-ready state automatically.

The agent should NOT need to manually write a handoff from scratch.

ShellDeck should periodically update:

```text
TASK
STATE
MEMORY
DECISIONS
SESSION
```

When the agent exits or switches:

```text
SESSION_STOP
      ↓
capture state
      ↓
generate handoff snapshot
      ↓
persist to SQLite
      ↓
write Markdown projection
```

---

# 16. Agent Startup

When a new agent starts in a project:

```text
Agent starts
     ↓
ShellDeck detects project
     ↓
load project identity
     ↓
load active task
     ↓
load current state
     ↓
retrieve relevant memory
     ↓
retrieve relevant previous handoff
     ↓
retrieve relevant cross-project knowledge
     ↓
generate compact context
     ↓
inject into agent
```

The agent should receive a concise context package rather than the entire history.

Example:

```text
SHELLDECK CONTEXT

Project: john-web

Task:
Implement authentication.

Current state:
Backend complete. Frontend session persistence remains.

Previous agent:
Claude / session_003

Next action:
Inspect frontend session hydration.

Relevant knowledge:
- acme-web authentication architecture
- Redis refresh-token pattern

Known decisions:
- Redis used for refresh tokens

Related project:
acme-web
```

---

# 17. Agent Switching

Switching agents should be transparent.

Example:

```text
Claude
   ↓
handoff
   ↓
Codex
   ↓
continue
   ↓
Gemini
   ↓
review
   ↓
Claude
   ↓
finish
```

No agent should need to reconstruct the project manually.

---

# 18. Cross-Project Context

The major extension is **shared knowledge between repositories**.

Example:

```text
acme-web
    │
    │ discovers
    ▼
Authentication Architecture
    │
    ▼
Global ShellDeck Knowledge
    │
    │ relevant to
    ▼
john-web
```

An agent in `john-web` should be able to retrieve:

```text
"How does authentication work in acme-web?"
```

without switching projects.

---

# 19. Knowledge Objects

Shared knowledge should be stored as structured objects.

Example:

```json
{
  "id": "knowledge_auth_001",
  "type": "architecture",
  "topic": "authentication",
  "project": "acme-web",
  "title": "Authentication Architecture",
  "content": "OAuth is handled by auth-service...",
  "source_files": [
    "src/auth/middleware.ts",
    "src/auth/session.ts"
  ],
  "created_by": "claude",
  "created_at": "2026-10-04T05:00:00Z",
  "confidence": 0.94
}
```

Knowledge should preserve its source.

Agents should know:

```text
This information came from:

Project: acme-web
Files:
  src/auth/middleware.ts
  src/auth/session.ts

Last verified:
2026-10-04
```

---

# 20. Knowledge Types

Initial knowledge types:

```text
architecture
api
database
authentication
deployment
infrastructure
configuration
testing
convention
pattern
bug
gotcha
decision
dependency
security
performance
domain
workflow
```

Additional types can be added later.

---

# 21. Context Scopes

Context should have explicit scopes.

```text
GLOBAL
  ↓
COMPANY
  ↓
TEAM
  ↓
PRODUCT
  ↓
PROJECT
  ↓
SESSION
```

Example:

```text
GLOBAL
  Python conventions

COMPANY
  Authentication policy

PRODUCT
  Shared API architecture

PROJECT
  acme-web implementation

SESSION
  Current OAuth bug
```

Retrieval should respect scope.

---

# 22. Project Relationships

Projects should be able to reference each other.

Example:

```text
john-web
    ├── depends-on → auth-service
    ├── related-to → acme-web
    └── uses-pattern → acme-web/authentication
```

Another example:

```text
billing-api
    └── shares-database-with → reporting-api
```

This forms a **project/context graph**.

---

# 23. Context Graph

ShellDeck should maintain relationships between:

```text
Projects
Repositories
Agents
Sessions
Tasks
Files
Knowledge
Decisions
Handoffs
Events
```

Example:

```text
                 Authentication
                       │
            ┌──────────┴──────────┐
            │                     │
         acme-web              john-web
            │                     │
     auth middleware       auth implementation
            │                     │
            └────── pattern ─────┘
```

This graph allows ShellDeck to answer questions such as:

```text
Which projects use this authentication pattern?

Where was this decision made?

Which agent discovered this?

Which files support this knowledge?

What projects are related to john-web?

What did the previous agent discover?
```

---

# 24. Retrieval

Context retrieval should be **relevance-based**.

Never inject the entire global database.

Recommended retrieval order:

```text
1. Current session
2. Current task
3. Current project state
4. Current project memory
5. Project decisions
6. Related project knowledge
7. Global knowledge
```

For:

```text
"Implement authentication similar to acme-web"
```

retrieve:

```text
john-web authentication context
+
acme-web authentication knowledge
+
related architecture decisions
+
relevant source files
```

Do NOT retrieve unrelated information.

---

# 25. Search Architecture

Phase 1:

```text
SQLite
+
FTS5
+
metadata filtering
```

Phase 2:

```text
SQLite
+
FTS5
+
embeddings
```

Phase 3, if necessary:

```text
knowledge graph
+
vector search
+
semantic reranking
```

Do not introduce a vector database prematurely.

ShellDeck should remain lightweight and local-first.

---

# 26. Event System

Every meaningful agent action can produce an event.

Example:

```json
{
  "id": "event_001",
  "session_id": "session_003",
  "project_id": "john-web",
  "type": "FILE_EDIT",
  "timestamp": "2026-10-04T05:01:00Z",
  "payload": {
    "file": "src/auth/middleware.ts"
  }
}
```

Initial event types:

```text
SESSION_START
SESSION_STOP

PROMPT

PLAN
PLAN_UPDATE

FILE_READ
FILE_EDIT
FILE_CREATE
FILE_DELETE

COMMAND
COMMAND_RESULT

TEST
TEST_RESULT

ERROR

DISCOVERY
DECISION
NOTE

AGENT_MESSAGE

HANDOFF
HANDOFF_RECEIVED

TASK_START
TASK_UPDATE
TASK_COMPLETE
```

---

# 27. Event Storage

Events should be stored in SQLite.

Example:

```text
events
--------------------------------
id
project_id
session_id
agent
type
timestamp
payload
importance
```

Not every raw terminal byte needs to become a permanent event.

ShellDeck should capture **meaningful events**, not blindly store everything.

---

# 28. Deterministic vs LLM Extraction

The system should use a hybrid approach.

## Deterministic extraction

Use ShellDeck itself for:

```text
files changed
commands
exit codes
test results
git branch
git commit
git status
timestamps
session ID
agent
project
working directory
```

No LLM required.

## LLM-derived extraction

Use an LLM only for:

```text
objective
summary
discoveries
decisions
reasoning
important context
risks
remaining work
knowledge extraction
```

This reduces cost and improves reliability.

---

# 29. Checkpointing

ShellDeck should periodically create context checkpoints.

Possible triggers:

```text
Every N tool calls
Every N minutes
After important file changes
After tests
After errors
After decisions
After plan changes
Before agent shutdown
Before handoff
```

Example:

```text
Tool calls:
25
50
75
100
```

But checkpoints should also happen immediately for important events.

---

# 30. Context Compaction

Raw events should not remain in the agent context indefinitely.

Example:

```text
100 raw events
       ↓
summarization
       ↓
10 important events
       ↓
STATE + MEMORY + DECISIONS
```

The database retains history.

The model receives only the relevant compact representation.

This is critical for reducing token usage.

---

# 31. Stale Context Detection

Context can become stale.

For example:

```text
Memory says:

Redis stores refresh tokens.

But the implementation has changed.
```

ShellDeck should track source files.

Example:

```json
{
  "knowledge_id": "auth_001",
  "source_files": [
    {
      "path": "src/auth/token.py",
      "hash": "abc123"
    }
  ]
}
```

When the file changes:

```text
hash changed
    ↓
knowledge potentially stale
    ↓
mark:
STALE
```

The agent should see:

```text
⚠ Authentication knowledge may be stale.

Source changed:
src/auth/token.py
```

---

# 32. Knowledge Verification

Knowledge should have a lifecycle:

```text
NEW
  ↓
VERIFIED
  ↓
STALE
  ↓
REVIEWED
```

Optional:

```text
INVALIDATED
```

Agents can explicitly verify knowledge.

Example:

```bash
sd knowledge verify auth_001
```

---

# 33. CLI

ShellDeck should expose a simple CLI.

## Context

```bash
sd context
```

Show current context.

```bash
sd context acme-web
```

Show another project's context.

---

## Memory

```bash
sd memory
```

Show project memory.

```bash
sd memory search authentication
```

Search shared knowledge.

---

## Recall

```bash
sd recall "authentication architecture"
```

Search all relevant context.

Example:

```text
1. acme-web
   Authentication Architecture
   relevance: 0.94

2. john-web
   OAuth integration
   relevance: 0.88

3. auth-service
   JWT conventions
   relevance: 0.82
```

---

## Projects

```bash
sd projects
```

Show known projects.

```bash
sd project acme-web
```

Show project information.

---

## Handoffs

Existing commands should remain:

```bash
sd handoff
sd handoffs
sd done ID
```

But handoffs should now include the full context snapshot.

---

## Resume

```bash
sd resume
```

Resume the most recent incomplete task.

---

## Switch

```bash
sd switch codex
```

Create a handoff and switch the active agent.

---

# 34. Agent Commands

Agents should have access to:

```bash
sd context
sd recall "<query>"
sd memory
sd handoff
sd done
sd tell <agent>
```

Optional:

```bash
sd remember "<fact>"
sd decide "<decision>"
sd discover "<finding>"
sd task update "<status>"
```

These commands should be lightweight and easy for agents to use.

---

# 35. Automatic Agent Skill

ShellDeck already installs agent skills.

The skill should be extended to teach agents:

```text
At startup:
1. Read ShellDeck context.
2. Check active task.
3. Check STATE.
4. Check relevant MEMORY.
5. Check latest handoff.
6. Continue from next action.

During work:
- update task progress
- record important discoveries
- record decisions
- do not duplicate existing knowledge
- use sd recall when context is missing

Before stopping:
- update STATE
- summarize remaining work
- record important discoveries
- create/update handoff
```

The agent should not need to understand ShellDeck's internal database.

---

# 36. Example: Cross-Project Reuse

Suppose `acme-web` contains:

```text
OAuth
JWT
Redis refresh tokens
tenant_id
auth middleware
```

An agent working on `acme-web` discovers this.

ShellDeck stores:

```text
Knowledge:
Authentication Architecture

Project:
acme-web

Topics:
authentication
oauth
jwt
redis
multi-tenancy
```

Later:

```bash
cd john-web
sd recall "authentication architecture"
```

ShellDeck returns:

```text
ACME-WEB

Authentication uses auth-service.

JWT contains:
- user_id
- tenant_id
- roles

Refresh tokens are stored in Redis.

Frontend session:
src/auth/session.ts

Middleware:
src/auth/middleware.ts

Source:
acme-web
```

The agent can then inspect the source files and adapt the pattern.

---

# 37. Example: Natural-Language Workflow

User:

```text
Implement authentication similar to acme-web.
```

ShellDeck agent workflow:

```text
User request
    ↓
Understand task
    ↓
sd recall "authentication acme-web"
    ↓
retrieve knowledge
    ↓
inspect source references
    ↓
compare john-web architecture
    ↓
create implementation plan
    ↓
implement
    ↓
test
    ↓
record decisions
    ↓
update STATE
    ↓
update MEMORY
    ↓
create handoff if necessary
```

The user should not have to manually explain how `acme-web` works.

---

# 38. Important Safety Rule

Cross-project knowledge must be treated as **reference material**, not truth.

An agent must not blindly copy:

```text
code
credentials
secrets
environment variables
private keys
tokens
personal information
```

Knowledge extraction must exclude secrets and sensitive values.

When adapting knowledge:

```text
Source Project
      ↓
Reference Architecture
      ↓
Target Project Analysis
      ↓
Adaptation
```

Not:

```text
Source Project
      ↓
COPY
      ↓
Target Project
```

---

# 39. Secret Protection

Never store:

```text
API keys
passwords
tokens
private keys
cookies
credentials
.env values
secrets
```

in persistent shared knowledge.

Before indexing command output or files:

```text
secret detection
      ↓
redaction
      ↓
store
```

Sensitive files should be excluded by default.

Examples:

```text
.env
.env.*
*.pem
*.key
credentials.*
secrets.*
```

---

# 40. Git Integration

Every context checkpoint should optionally record:

```text
repository
branch
commit
dirty state
changed files
```

Example:

```text
Project: john-web

Branch:
feature/auth

Commit:
abc1234

Working tree:
DIRTY

Changed:
src/auth/middleware.ts
src/auth/session.ts
```

This provides agents with reliable state.

---

# 41. Database Design

Initial SQLite schema should include:

```text
projects
sessions
tasks
handoffs
knowledge
decisions
events
relationships
source_references
```

Conceptual schema:

```text
projects
---------
id
name
path
repository
created_at
updated_at


sessions
--------
id
project_id
agent
started_at
ended_at
status


tasks
-----
id
project_id
session_id
title
objective
plan
status
created_at
updated_at


handoffs
--------
id
project_id
session_id
from_agent
to_agent
task_id
snapshot
status
created_at


knowledge
---------
id
scope
project_id
type
topic
title
content
confidence
status
created_by
created_at
updated_at


decisions
---------
id
project_id
title
decision
reason
alternatives
created_at


events
------
id
project_id
session_id
type
timestamp
payload
importance


relationships
-------------
id
source_type
source_id
relation
target_type
target_id


source_references
-----------------
id
knowledge_id
project_id
file_path
file_hash
line_start
line_end
```

---

# 42. Source Attribution

Every important knowledge item should have provenance.

Example:

```text
Knowledge:
Authentication Architecture

Source:
acme-web

Files:
src/auth/middleware.ts
src/auth/session.ts

Discovered by:
Claude

Session:
session_003

Last verified:
2026-10-04
```

This allows agents and humans to validate information.

---

# 43. Markdown Projection

SQLite is canonical.

Markdown is generated from SQLite.

Example:

```text
SQLite
  ↓
context projection
  ↓
STATE.md
TASK.md
MEMORY.md
DECISIONS.md
handoff.md
```

Agents and users can therefore inspect the context without requiring a database tool.

---

# 44. Existing Handoff Compatibility

The existing ShellDeck handoff system should remain backward compatible.

Existing:

```text
.shelldeck/handoffs/<id>.md
```

continues to work.

However, the handoff content should be enhanced to include:

```text
task
state
memory
decisions
git
tests
files
events
next action
related projects
```

Existing:

```bash
sd handoffs
sd done
```

should continue working.

---

# 45. Architecture

Recommended internal architecture:

```text
                     ┌──────────────────┐
                     │   ShellDeck CLI  │
                     └────────┬─────────┘
                              │
                     ┌────────▼─────────┐
                     │ Context Manager  │
                     └────────┬─────────┘
                              │
              ┌───────────────┼────────────────┐
              │               │                │
       ┌──────▼──────┐ ┌─────▼─────┐ ┌────────▼────────┐
       │ Task Manager│ │ State Mgr │ │ Knowledge Manager│
       └──────┬──────┘ └─────┬─────┘ └────────┬────────┘
              │              │                │
              └──────────────┼────────────────┘
                             │
                     ┌───────▼────────┐
                     │ Context Store  │
                     │    SQLite      │
                     └───────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
          FTS Search      Events       Relationships
              │              │              │
              └──────────────┼──────────────┘
                             │
                     ┌───────▼────────┐
                     │ Agent Adapter  │
                     └────────────────┘
```

---

# 46. Context Manager

Introduce a central component:

```text
ContextManager
```

Responsibilities:

```text
load_context()
save_state()
create_checkpoint()
create_handoff()
resume_session()
record_event()
record_memory()
record_decision()
search_context()
project_context()
related_projects()
```

All agents should use this abstraction instead of directly manipulating files.

---

# 47. Context Package

The agent should receive a structured context package.

Conceptually:

```python
ContextPackage(
    project=...,
    task=...,
    state=...,
    memory=...,
    decisions=...,
    handoff=...,
    related_projects=...,
    relevant_knowledge=...,
    git_state=...,
    recent_events=...,
)
```

This package can then be rendered into the agent-specific prompt/skill format.

---

# 48. Context Budget

ShellDeck should enforce a context budget.

For example:

```text
Task                10%
Current State       20%
Relevant Memory     20%
Handoff             20%
Knowledge           20%
Recent Events       10%
```

The exact allocation should be configurable.

If too much information is available:

```text
relevance ranking
      ↓
deduplication
      ↓
summarization
      ↓
context budget
```

---

# 49. Deduplication

Avoid storing duplicate knowledge.

Example:

Agent A:

```text
JWT contains tenant_id.
```

Agent B later discovers:

```text
JWT includes tenant_id.
```

ShellDeck should detect that this is likely the same knowledge.

Instead of:

```text
Knowledge 1
Knowledge 2
Knowledge 3
Knowledge 4
```

maintain:

```text
Knowledge:
JWT contains tenant_id.

Sources:
acme-web
john-web
auth-service
```

---

# 50. Knowledge Confidence

Knowledge should have confidence.

Example:

```text
0.95 — verified from source code
0.80 — agent discovery
0.60 — inferred
0.30 — speculative
```

Agents should prefer high-confidence information.

---

# 51. Conflict Detection

Different projects may contain conflicting patterns.

Example:

```text
acme-web:
JWT expiry = 15 minutes

john-web:
JWT expiry = 60 minutes
```

ShellDeck should NOT merge these into a single global fact.

Instead:

```text
acme-web:
JWT expiry = 15m

john-web:
JWT expiry = 60m
```

Knowledge remains scoped to its source.

---

# 52. Human Control

Users should be able to inspect and modify context.

Commands:

```bash
sd context
sd memory
sd memory search <query>
sd projects
sd project <name>
sd decisions
sd handoffs
```

Future UI should expose:

```text
Projects
Sessions
Tasks
Memory
Knowledge
Decisions
Relationships
Handoffs
```

---

# 53. UI Concept

ShellDeck UI could eventually show:

```text
┌──────────────────────────────────────────────┐
│ ShellDeck                                    │
├───────────────┬──────────────────────────────┤
│ Projects      │ john-web                     │
│               │                              │
│ ● john-web    │ Task                         │
│ ○ acme-web    │ Implement authentication     │
│ ○ billing     │                              │
│               │ State                        │
│               │ Backend complete             │
│               │ Frontend pending              │
│               │                              │
│               │ Related Projects             │
│               │ → acme-web                    │
│               │ → auth-service               │
│               │                              │
│               │ Shared Knowledge             │
│               │ → OAuth architecture         │
│               │ → JWT conventions             │
│               │ → Redis token pattern        │
└───────────────┴──────────────────────────────┘
```

---

# 54. Local-First

The entire system should work locally.

Default:

```text
SQLite
local files
local FTS
local embeddings
```

No cloud dependency.

Optional future integrations can support:

```text
PostgreSQL
Qdrant
Chroma
Redis
remote ShellDeck context
```

But they should not be required.

---

# 55. Performance Requirements

The context system should be lightweight.

Target:

```text
Startup context load: <100ms
Simple recall: <100ms
SQLite search: <50ms typical
Checkpoint: asynchronous
Markdown projection: asynchronous
Embedding generation: asynchronous
```

Agent terminal interaction must never block on expensive context processing.

---

# 56. Background Processing

Use background workers for:

```text
knowledge extraction
embedding generation
Markdown projection
event compaction
stale detection
indexing
```

The agent interaction path should remain responsive.

---

# 57. Failure Handling

If the context system fails:

```text
agent execution MUST continue
```

Context should be treated as an enhancement, not a single point of failure.

Example:

```text
SQLite unavailable
      ↓
log warning
      ↓
agent continues
```

---

# 58. Privacy

Default behavior:

```text
local only
```

Cross-project context should never leave the machine unless explicitly configured.

---

# 59. Migration Strategy

Implement incrementally.

## Phase 1 — Refactor existing handoff

Extend current handoff system with:

```text
STATE
TASK
MEMORY
DECISIONS
GIT
TESTS
```

Maintain backwards compatibility.

---

## Phase 2 — Session persistence

Add:

```text
sessions
events
checkpoints
```

Automatically maintain session state.

---

## Phase 3 — Project memory

Add:

```text
MEMORY.md
DECISIONS.md
TASK.md
STATE.md
```

with SQLite backing.

---

## Phase 4 — Shared context

Introduce:

```text
~/.shelldeck/context.db
```

and global project registry.

---

## Phase 5 — Cross-project retrieval

Implement:

```bash
sd recall
```

with SQLite FTS5.

---

## Phase 6 — Project relationships

Add:

```text
relationships
```

and cross-project references.

---

## Phase 7 — Semantic retrieval

Add optional embeddings.

---

## Phase 8 — Context graph

Connect:

```text
Projects
Agents
Sessions
Tasks
Files
Knowledge
Decisions
Handoffs
```

---

# 60. Backward Compatibility

Existing ShellDeck functionality must continue working:

```text
agent spawning
PTY
scrollback
handoffs
agent skills
sd done
sd tell
team management
search
session management
```

The new context layer should be additive.

---

# 61. Success Criteria

The feature is successful when the following workflow works reliably:

### Scenario 1 — Agent switch

```text
Claude starts task
      ↓
works for 30 minutes
      ↓
Claude exits
      ↓
Codex starts
      ↓
Codex immediately understands current state
      ↓
continues work
```

### Scenario 2 — Restart

```text
Agent works
      ↓
ShellDeck closes
      ↓
ShellDeck restarts
      ↓
Agent resumes
      ↓
context is preserved
```

### Scenario 3 — Cross-project reuse

```text
Agent works on acme-web
      ↓
discovers authentication architecture
      ↓
knowledge stored
      ↓
switch to john-web
      ↓
ask:
"Implement authentication similar to acme-web"
      ↓
ShellDeck retrieves relevant knowledge
      ↓
agent adapts it
```

### Scenario 4 — Multiple agents

```text
Claude → implementation
Codex → debugging
Gemini → review
Claude → finalization
```

All agents share the same persistent context.

---

# 62. Final Product Definition

ShellDeck should ultimately become:

> **A persistent, local-first workspace for AI coding agents where tasks, state, memory, decisions, handoffs, and project knowledge survive across agents, sessions, and repositories.**

The fundamental model is:

```text
                    SHELLDECK
                        │
        ┌───────────────┼────────────────┐
        │               │                │
     AGENTS          PROJECTS         KNOWLEDGE
        │               │                │
   Claude/Codex     acme-web         architecture
   Gemini/etc       john-web         decisions
        │            billing          patterns
        │               │                │
        └───────────────┼────────────────┘
                        │
                 CONTEXT GRAPH
                        │
                ┌───────┴───────┐
                │               │
             HANDOFF         RECALL
                │               │
          Agent Continuity   Cross-Project
                              Context
```

The key architectural principle is:

> **Do not make the agent's conversation the source of truth. Make ShellDeck's persistent context the source of truth.**

Agents become interchangeable workers operating on a persistent shared workspace.

---

# 63. One-Line Vision

**ShellDeck: Run any agent, switch any agent, switch any project — your context follows you.**