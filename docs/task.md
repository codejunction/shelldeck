# Agent enhancement: implementation task plan

## Status

**Planning complete. Implementation not started.**

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

- [ ] Add `shelldeck/agent_state.py`.
- [ ] Define canonical state values: `unknown`, `idle`, `working`, `blocked`,
  `done`, and `exited`.
- [ ] Define typed models for an agent status, report, report source, native
  session reference, and display metadata.
- [ ] Define one state-authority resolver with the precedence documented in the
  design spec.
- [ ] Move no UI behavior in this task; preserve the current heuristic outputs
  through a compatibility adapter.

**Primary files:** `shelldeck/server.py`, `shelldeck/agents.py`, new
`shelldeck/agent_state.py`, and new focused tests.

**Done when:** unit tests prove valid/invalid state handling, authority
precedence, source replacement, expiry, and fallback selection.

### Task 1.2: Make lifecycle state server-owned

- [ ] Add an in-memory report registry keyed by terminal session and report
  source.
- [ ] Change the server's agent watcher to resolve the canonical status rather
  than emitting only the current `working` / `approval` / `idle` result.
- [ ] Map the existing approval heuristic to `blocked` with reason `approval`.
- [ ] Emit structured `agent_state` alarm events containing state, source, and
  optional blocked reason.
- [ ] Return status/source information from `/api/agents`.
- [ ] Update `static/app.js` and `static/monitor.js` to consume server state;
  remove duplicate browser-side lifecycle decisions only after compatibility
  tests pass.

**Primary files:** `shelldeck/server.py`, `shelldeck/static/app.js`,
`shelldeck/static/monitor.js`, `tests/test_shelldeck.py`.

**Done when:** all tabs show the same state; expiry returns to a fallback state;
notifications occur only on a transition into blocked; and no-browser CLI use
continues to work.

## Milestone 2 — Secure reporting API

### Task 2.1: Add terminal-bound agent report ingestion

- [ ] Add `POST /api/agent-reports` with the schema in the design spec.
- [ ] Generate a unique report token for each live terminal and expose it only
  through that terminal's process environment.
- [ ] Validate token/session binding, agent kind, source, state, TTL, metadata
  size, and resume argv.
- [ ] Reject reports for closed, unknown, or mismatched terminal sessions.
- [ ] Ensure normal logs and API responses do not disclose native conversation
  IDs or terminal-bound tokens.
- [ ] Add a compact, documented error vocabulary for integration authors.

**Primary files:** `shelldeck/server.py`, `shelldeck/pty.py`,
`shelldeck/agent_state.py`, `tests/test_shelldeck.py`.

**Done when:** a report cannot update another terminal; malformed/expired input
is rejected; and a valid report produces an authoritative state transition.

### Task 2.2: Separate display metadata from state authority

- [ ] Support bounded title, display-agent/role, state-label, and token metadata
  fields on reports.
- [ ] Apply metadata with source ownership and expiry.
- [ ] Ensure metadata cannot change lifecycle semantic state, notifications,
  hand-off completion, or restore eligibility.
- [ ] Add API tests for stale metadata removal and source conflict behavior.

**Done when:** UI labels can change without affecting the semantic lifecycle
state or automation behavior.

## Milestone 3 — Integration manager

### Task 3.1: Establish the integration package and commands

- [ ] Create `shelldeck/integrations/` with a common integration interface.
- [ ] Implement discovery, install, uninstall, and status operations.
- [ ] Add `sd integration detect|list|install|uninstall|status`.
- [ ] Add authenticated API endpoints for the same operations.
- [ ] Add an Agents-page distinction between a Shelldeck skill and a runtime
  integration.
- [ ] Use atomic writes when available and manage only Shelldeck-owned config
  entries/files.

**Primary files:** new `shelldeck/integrations/`, `shelldeck/cli.py`,
`shelldeck/server.py`, `shelldeck/static/agents.js`, and integration tests.

**Done when:** an integration install/status/uninstall cycle is idempotent and
leaves unrelated configuration untouched.

### Task 3.2: Codex integration

- [ ] Verify the current supported Codex hook format against official docs and a
  real install before writing it.
- [ ] Install a Shelldeck-owned hook that reports the native session identity.
- [ ] Support validated Codex resume argv.
- [ ] Keep current screen/activity detection as lifecycle fallback.
- [ ] Test config preservation and uninstall with temporary `CODEX_HOME`.

**Done when:** Codex reports a session identity and can resume a compatible
conversation without affecting unrelated Codex hooks.

### Task 3.3: Claude Code integration

- [ ] Verify the current Claude Code hook schema and event names before writing
  an installer.
- [ ] Install/remove only Shelldeck-owned hook entries and scripts.
- [ ] Report native session identity; preserve screen detection for state.
- [ ] Test default and overridden Claude configuration directories.

**Done when:** Claude Code installation is safe, idempotent, and reversible.

### Task 3.4: OpenCode integration

- [ ] Verify supported OpenCode plugin APIs and version compatibility.
- [ ] Implement lifecycle-state and selected-session reporting in a
  Shelldeck-owned plugin.
- [ ] Treat this integration as the first authoritative lifecycle proof case.
- [ ] Test permissions/questions, completion, interruptions, and multiple
  terminal sessions.

**Done when:** OpenCode reports `working`, `blocked`, and `idle` accurately and
can provide a resumable native session reference.

## Milestone 4 — Persistence and safe restore

### Task 4.1: Persist supported native sessions

- [ ] Add additive database migration(s) for `agent_sessions` and integration
  status data.
- [ ] Persist only validated native-session information and bounded metadata.
- [ ] Do not persist high-frequency heartbeat reports by default.
- [ ] Add settings for `never`, `ask`, and `auto` native-session restore.
- [ ] Default new users to `ask`.

**Primary files:** `shelldeck/db.py`, `shelldeck/server.py`, settings UI and
tests.

**Done when:** a valid native session survives a server restart in storage, and
invalid/missing resume data cannot be launched.

### Task 4.2: Restore sessions safely

- [ ] Restore normal terminals as shells first.
- [ ] Resume an agent only after its shell is ready and the integration is
  installed, compatible, and enabled.
- [ ] Preserve terminal/layout records when cwd, executable, or integration is
  unavailable; provide a retry/error action rather than silently changing work.
- [ ] Do not auto-complete or silently reassign an open hand-off during restore.
- [ ] Add restart tests for success, disabled restore, invalid argv, missing cwd,
  and missing executable.

**Done when:** restart recovery is explicit, safe, and recoverable for both
supported agent sessions and ordinary terminals.

## Milestone 5 — Automation and operator experience

### Task 5.1: Event-driven agent commands

- [ ] Add `sd agent status` with stable human and JSON forms.
- [ ] Add server-owned `sd agent wait` with timeout, closure, and replacement
  process handling.
- [ ] Add atomic `sd agent prompt --wait` semantics.
- [ ] Add agent attach/rename commands for manually started agent processes.
- [ ] Add an event subscription endpoint/transport and `sd events subscribe`.
- [ ] Update the installed skill only after these commands are tested.

**Done when:** supervisors can wait for an agent state transition without
polling screen text, and a new agent process cannot satisfy an old wait.

### Task 5.2: Attention queue and project rollups

- [ ] Add a prioritized attention section to `static/agents.js`.
- [ ] Display semantic state, source, reason, elapsed time, model/context,
  role/title, and integration health.
- [ ] Add direct actions appropriate to the viewer's permissions: open, peek,
  answer, message, resume, diagnose, reassign, and close.
- [ ] Add project sidebar rollups with accessible text/icon state indicators.
- [ ] Keep completed agents visible until reviewed; do not re-alert unchanged
  state.

**Done when:** a user can identify and open the next agent needing a decision in
one click, including across projects.

## Milestone 6 — Maintainable fallback detection

### Task 6.1: Versioned detection manifests

- [ ] Create bundled `shelldeck/agent_detection/` manifests for known agent
  screen states.
- [ ] Add local config overrides under the Shelldeck config directory.
- [ ] Make invalid local overrides non-fatal and visible in diagnostics.
- [ ] Migrate existing approval/question regex behavior into a manifest or a
  clearly named compatibility rule.

### Task 6.2: Explain diagnostics

- [ ] Add `sd agent explain TARGET` and JSON/file variants.
- [ ] Show process identity, active authority, report age, manifest source and
  version, matched rule, and fallback reason.
- [ ] Redact raw screen output by default.

**Done when:** maintainers can determine why a state was selected without adding
temporary logging or exposing private terminal content.

## Release checklist

- [ ] Run the full unit/API suite on Linux and Windows.
- [ ] Manually validate Codex, Claude Code, and OpenCode install/uninstall and
  lifecycle behavior.
- [ ] Validate parent/sub-agent question forwarding and hand-off completion.
- [ ] Validate restart behavior, remote SSH use, and share-mode auth boundaries.
- [ ] Verify UI state text/icons with keyboard navigation and a screen reader.
- [ ] Update `README.md`, `CLAUDE.md`, `CHANGELOG.md`, and the dedicated agent
  integration/automation/detection docs described in the design spec.
- [ ] Add release notes that list exact supported integrations and clearly state
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
  -> event-driven automation
  -> attention UI and project rollups
  -> detection manifests and explain diagnostics
```

Do not begin native restore before report validation and the integration manager
exist. Do not make an integration authoritative before its lifecycle events are
covered by tests. The attention UI can be prototyped earlier, but its final
behavior must use server-owned semantic status.
