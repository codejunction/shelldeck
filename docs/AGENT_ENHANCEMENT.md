# Agent enhancement

How shelldeck tracks, drives and remembers AI coding agents: the design, what shipped in 0.0.7, and what is still open.
The ideas come from [herdr](https://github.com/herdrdev/herdr) (explicit lifecycle authority, native resume, event
waits, detection manifests) and [dotpals](https://github.com/Rikinshah787/dotpals) (one hook adapter per agent CLI).
shelldeck adopts them on top of its own hand-off and team model, without replacing it.

Reference docs for users: [agent-integrations.md](agent-integrations.md) (support matrix, install),
[agent-automation.md](agent-automation.md) (events, waits, prompts), [agent-detection.md](agent-detection.md)
(screen rules and overrides), [agent-report.schema.json](agent-report.schema.json). Progress log: [task.md](task.md).

## Contents

1. [Goals and non-goals](#1-goals-and-non-goals)
2. [Status at a glance](#2-status-at-a-glance)
3. [Lifecycle state](#3-lifecycle-state)
4. [Integrations](#4-integrations)
5. [Native launch, commands and resume](#5-native-launch-commands-and-resume)
6. [Events and automation](#6-events-and-automation)
7. [Attention UI](#7-attention-ui)
8. [Shared context](#8-shared-context)
9. [Security and privacy](#9-security-and-privacy)
10. [Testing](#10-testing)
11. [Open work](#11-open-work)
12. [Definition of done](#12-definition-of-done)

## 1. Goals and non-goals

**Goals**

- Show what each agent is doing (working, needs you, done, exited) and *why* shelldeck thinks so.
- Prefer exact signals from the agent (hooks, its own session files) over reading the screen.
- Let scripts and other agents wait for a state change instead of polling terminal text.
- Resume an agent's own session after a restart, safely.
- Keep project knowledge (task, state, facts, decisions) outside any one agent, so the next agent continues where the
  last one stopped.
- Support as many agent CLIs as possible, with **Claude Code, Codex, Gemini CLI and Devin CLI** as the verified set.

**Non-goals**

- No model runs inside shelldeck. Facts are extracted by the agents; smart search borrows an agent's small model.
- No local embeddings, no MCP, no cloud sync. Everything stays on this machine.
- No re-implementing agent features: shelldeck types the agent's own slash commands and uses its own flags.
- Not re-adding the removed AI assistant, broadcast, pipe or mindmap.

## 2. Status at a glance

| Area | Status | Main code |
| --- | --- | --- |
| Server-owned lifecycle registry with source precedence | Shipped | `agent_state.py`, `server._check_agents`, `_publish` |
| Report endpoint with per-terminal token | Shipped | `POST /api/agent-reports`, `report_tokens` |
| Hook adapter (stdlib-only) | Shipped | `hook.py` |
| Installers: Claude, Codex, Gemini, Devin (verified) | Shipped | `integrations.py` (`PRIORITY`) |
| Installers: 15 more agents | Preview (`tier: later`) | `integrations.py` |
| Native readers without hooks (Claude, Codex, Devin) | Shipped | `agents.native()` |
| Per-run hook flags (Claude `--settings`) | Shipped | `integrations.run_flags`, `team.launch_line` |
| Native slash commands per agent | Shipped | `agent_commands.py` |
| Native resume (`agent_resume` never/ask/auto) | Shipped | `server._resume_plan`, `_auto_resume` |
| Detection manifests with local overrides, `explain` | Shipped | `detection.py`, `agent_detection/default.toml` |
| SSE events, `agent-wait`, `agent-prompt` | Shipped | `/api/events`, `/api/agent-wait`, `/api/agent-prompt` |
| Attention UI (rollups, Open next, done-until-seen) | Shipped | `static/app.js`, `static/agents.js` |
| Context store, projections, recall | Shipped | `context.py`, `/api/context*`, `static/context.js` |
| Deterministic capture from hooks | Shipped | `hook.activity()`, `server._capture` |
| Agent-driven fact extraction | Shipped | `/api/sessions/{id}/extract`, `EXTRACT_PROMPT` |
| Smart recall via an agent's small model | Shipped | `smart_recall.py` |
| `sd init` | Shipped | `context.init_project` |
| Context graph, conflict detection, compaction | Open | see [Open work](#11-open-work) |

## 3. Lifecycle state

States: `unknown`, `idle`, `working`, `blocked` (with `blocked_reason`, e.g. `approval`), `done`, `exited`.

Every signal becomes a `Report` in the `Registry`. The highest-priority live source wins:

```text
integration  >  custom  >  native  >  manifest  >  heuristic
(hooks)         (sd agent report)  (session files)  (screen rules)  (old screen guess)
```

- Reports expire by TTL, so a dead source falls back to the next one.
- A new heuristic verdict replaces the previous one (`put_heuristic`), which avoids timestamp ties on Windows' 16ms
  clock.
- `parse_report()` validates input and returns stable `ReportError` codes. A resume argv must start with the agent's
  bare executable name and is accepted only from `integration:*` sources.
- `agent_kind` holds `(agent, generation)`. A new agent in the same terminal starts a new generation, so waits and
  context sessions never mix two runs.
- `_publish` broadcasts `agent_state` (`status`, `since`, source) over `/ws/alarms` and SSE, and sends a push only on a
  transition into `blocked`.
- The browser trusts server status only for authoritative sources (`AUTHORITATIVE` in app.js). Otherwise its own
  screen heuristic stays.

`sd agent explain NAME` / `GET /api/agent-explain` shows the winning source, every live report and the screen rule
that matched.

## 4. Integrations

A hook calls `python -m shelldeck.hook <agent> [event|state]`. It reads the agent's event JSON from stdin, maps it with
`EVENTS` to a state, posts it with only `SHELLDECK_AGENT_REPORT_TOKEN`, prints the agent's expected `REPLY` and
always exits 0. One interpreter start per event, stdlib only.

| Tier | Agents | Notes |
| --- | --- | --- |
| Verified | Claude Code, Codex, Gemini CLI, Devin CLI | Default for `sd integration install`, tested end to end |
| Preview | Cursor, Copilot, OpenCode, Kilo, Qwen, Qoder, Droid, Kimi, Letta, Grok, Pi, OMP, Hermes, MastraCode, Antigravity | Formats from each agent's hook docs as used by dotpals |

Installer shapes: `JsonHooks` (merge into shared JSON, own only shelldeck's entries), `CodexHooks` (plus
`[features] hooks = true`), `OwnFile`, `NamedBlock`, `KimiToml`, `Extension`, `HermesPlugin`. All of them make a
one-time `.shelldeck-backup`, write atomically, refuse configs they can't parse, and refuse when the agent's config
folder is missing.

Without hooks, `agents.native()` reads the agent's own files: Claude `sessions/<pid>.json`, Codex rollouts
(`CODEX_EVENTS`), Devin `sessions.db`. These become `native:<agent>` reports.

CLI: `sd integration detect|list|status|install|uninstall`. UI: the Integrations section of the AI agents page.

## 5. Native launch, commands and resume

- **Launch.** `team.launch_line(key, model, prompt, extra)` builds every agent command (spawn, Launch button,
  `sd agent start`, resume). Claude gets per-run hooks through `--settings <config>/agent-hooks/claude-settings.json`
  unless the global hook is installed. Long or unsafe prompts go to `.shelldeck/prompts/<id>.md`.
- **Slash commands.** `agent_commands.COMMANDS` maps actions (compact, clear, model, status, resume, review, plan,
  rewind, quit, ...) to each agent's own command: Claude `/compact`, Gemini `/compress`, Devin `/revert`, and so on.
  `sd agent cmd NAME ACTION [ARG]` or the row's *Actions* menu types it, refusing blocked or working agents unless
  forced.
- **Resume.** Hooks and native readers store the agent's session id and resume argv (`hook.RESUME`, from herdr's
  `agent_resume.rs`). `agent_resume` decides: `never`, `ask` (a resume offer), or `auto` (once per terminal per server
  run). `_resume_plan` re-checks the argv against `SAFE_ARG`, `shutil.which` and the cwd before typing it.

## 6. Events and automation

| Primitive | Use |
| --- | --- |
| `GET /api/events` (SSE), `sd events subscribe` | Stream of `agent_state`, hand-off and question events |
| `GET /api/agent-wait`, `sd agent wait NAME --until done` | Block until a state, pinned to one generation, woken by events (no screen polling) |
| `POST /api/agent-prompt`, `sd agent prompt NAME "..."` | Send a prompt; refused while blocked |
| `POST /api/agent-reports`, `sd agent report` | Custom reports from scripts |
| `sd spawn`, `sd handoff`, `sd answer`, `sd close` | Existing team flow, unchanged |

Example: a Claude parent starts Devin for docs and Codex for tests with `sd spawn`, then
`sd agent wait <name> --until done` on each, instead of reading their screens.

## 7. Attention UI

- Terminals are ranked blocked > done-unseen > exited > working > idle (`ATTENTION`, `attentionOf()`).
- Project rows in the sidebar show a rollup chip (`needs you`, `working`, `done`).
- The AI agents page has a "needs you" banner with *Open next*, a status column with source and age, and one
  *Actions* menu per row (extract facts, message, native commands).
- `done` stays highlighted until you look at the terminal (`S.doneUnseen`).
- The sidebar footer keeps Terminals, AI agents and Context visible; the rest folds under *More*.

## 8. Shared context

A local store (`<config>/context.db`, SQLite + FTS5) keeps per project: the task and next action, current state,
facts, discoveries, decisions, sessions and events. Projects are keyed by resolved folder.

**Writing.**

- Deterministic capture: hook `activity` adds `{command, ok}` or `{file, change}` (never output or contents). Tests
  set the Tests line; failed lookups (grep etc.) aren't errors. Shell commands typed by people are captured too.
- Agents record what they learn with `sd remember`, `sd decide`, `sd task update`.
- *Extract facts* / `sd agent extract NAME` asks the agent to do it now in one short turn. On demand only.

**Reading.**

- `sd context` prints a budgeted context package for a new agent; `sd resume` / `sd switch` hand over.
- `sd recall QUERY` searches every project with sources. `--smart` (or `--agent/--model`) widens the query with
  keywords from an agent's smallest model (`claude -p --model haiku`, `gemini -m flash-lite -p`, `codex exec`,
  `devin -p`). Only the redacted query is sent, the reply is cut to 8 keywords, and results are cached.
- `.shelldeck/STATE.md`, `TASK.md`, `MEMORY.md`, `DECISIONS.md` and `sessions/<id>.md` are projections regenerated
  from the database after each write, so edit through the CLI or Context page. `AGENTS.md` keeps hand edits once its
  marker line is removed.
- `sd init` creates the project, the projections and `AGENTS.md` up front.

**Hygiene.** Everything stored goes through `redact()`; `.env` and key files are never referenced; facts are
de-duplicated per project; `check_stale` marks facts whose source files changed; `sd knowledge verify` re-checks them.

## 9. Security and privacy

- Reports authenticate by a per-terminal token, accepted alone only from local requests (never the share tunnel). The
  token is dropped when the PTY exits.
- Installers touch only entries they own and back up first. Resume argv is validated twice (on report and before
  typing).
- Typed text only goes to terminals running an agent; arguments must match `SAFE_VALUE`.
- Smart recall runs the agent as argv with no shell, a 30s timeout and no shelldeck tokens in its environment.
- No stored memory leaves the machine; only a smart-recall query goes to the agent you picked.

## 10. Testing

- Unit: `test_agent_state`, `test_integrations` (every installer, idempotent install/uninstall, backups, refusals,
  Devin's event list), `test_native`, `test_detection`, `test_context`, `test_smart_recall`.
- API and CLI: `test_shelldeck`, `test_cli` (reports, waits, SSE, resume plans, settings validation).
- Real runs: Claude CLI with per-run `--settings` hooks, smart recall with real haiku, end-to-end hook paths for the
  four verified agents.
- CI: Windows and Ubuntu, Python 3.12 to 3.14.

## 11. Open work

- Finish and verify the preview integrations one by one against each agent's current docs.
- Verify Devin's paths and commands on Linux and against docs.devin.ai directly (only cross-checked so far).
- Conflict detection between facts, and compaction of old events.
- Context graph linking projects, sessions, tasks, files, knowledge and hand-offs.
- Richer project relationships for cross-project recall (`sd relate` exists; ranking does not use it much yet).

## 12. Definition of done

1. A user can see why an agent has a status and where it came from. **Done** (`explain`, status source column).
2. A supported integration installs, reports and uninstalls without damaging config. **Done** for the four.
3. At least one agent reports exact state and resumes its native session safely. **Done** (Claude, Codex, Gemini,
   Devin).
4. Scripts and agents can wait for state changes without polling screens. **Done** (`agent-wait`, SSE).
5. Spawn, hand-off, parent escalation and terminal safety still work. **Done**, covered by the existing tests.
6. New behaviour is tested on the supported platforms. **Done** (CI matrix).
7. User docs state capability and privacy limits. **Done** (README, the docs above).
