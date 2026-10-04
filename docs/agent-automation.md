# Agent automation

Shelldeck owns each agent terminal's lifecycle state. Scripts, parent agents and integrations read and wait on that state
through the local authenticated API (the CLI sends the host token automatically).

## States

| State | Meaning |
| --- | --- |
| `unknown` | An agent is detected, but no source has reported yet. |
| `idle` | Ready for a prompt. |
| `working` | Running, reasoning or calling tools. |
| `blocked` | Needs input. `reason` is one of `approval`, `question`, `authentication`, `tool_error`, `external_wait`, `unknown`. |
| `done` | The task finished. An open hand-off still needs `sd done`; a report never closes one. |
| `exited` | The agent process left the terminal. |

Each status names its `source`:

- `integration`: a built-in hook or plugin.
- `custom`: a third-party reporter.
- `native`: a direct agent API.
- `manifest`: screen-detection rules.
- `heuristic`: output activity and the question regex.

The highest unexpired source wins. When it expires, the next one takes over.

## CLI

```sh
sd agent status [TERMINAL] [--json]
sd agent wait TERMINAL --until idle [--until blocked] [--timeout 10m] [--json]   # exit 0 reached, 2 timeout, 3 exited/replaced
sd agent report working --source custom:mytool --agent codex [--reason R] [--title T] [--label L] [--ttl-ms 30000]
```

## Prompts and events

- `sd agent prompt TERMINAL "text" [--wait] [--until done|idle|blocked] [--timeout 10m]` (`POST /api/agent-prompt`).
  - It is refused with `agent_blocked` while the agent waits on a question, because the text would answer it.
  - `--wait` counts only a state that began after the prompt was sent, and it is pinned to the agent process present
    when the prompt is sent.
  - Exit codes: 0 reached, 2 timeout, 3 exited or replaced.
- `sd events subscribe [--types agent.state,handoff] [--since ID]` (`GET /api/events`, Server-Sent Events, `Last-Event-ID`).
  - Event types: `agent.detected`, `agent.state`, `agent.exited`, `agent.session_updated`, `handoff.created`,
    `handoff.completed` and `integration.changed`.
  - Each event is `{id, type, at, data}` with a compact snapshot. Prompts, screen text and native ids are never included.
  - The server keeps the last 500 events in memory.

## Diagnostics

`sd agent explain TERMINAL` (`GET /api/agent-explain/{session_id}`) shows:

- the detected agent and its process generation;
- every report, highest authority first, with its age and time left, and which one decides;
- the screen heuristic's inputs: how long the screen has been quiet, how long the current output burst has run, and which built-in question phrase matched;
- the integration's install status and tier, and whether a native session is stored.

It never returns screen text or native session ids. `sd agent explain --file screen.txt` checks saved screen text
against the question rules locally.

## API

- `GET /api/agent-status[?target=<sid>]` returns `{agents: [{session_id, agent, generation, state, source, reason, detail, meta?}]}`.
- `POST /api/agent-wait {session_id, until: [states], timeout: seconds}` returns `{result: reached|timeout|exited|replaced, status}`.
  - The wait is pinned to the agent process present when it starts. A different agent later in the same terminal returns `replaced`.
  - It wakes on state changes; it doesn't poll screens.
- `POST /api/agent-reports` takes a report body (schema: [agent-report.schema.json](agent-report.schema.json)). It needs the
  `X-Shelldeck-Report-Token` header with the terminal's `SHELLDECK_AGENT_REPORT_TOKEN`, plus normal API auth.
  - The terminal comes from the token. A `session_id` in the body must match it.
  - A report holds for `ttl_ms` (default 30 s, max 120 s), so reporters must refresh it.
- Alarm socket messages: `{"type":"agent_state","session_id":...,"state":<legacy working|approval|idle>,"status":{...}}`.

### Report errors

All errors are `{"error": code}`.

| Code | Status | Meaning |
| --- | --- | --- |
| `invalid_report_token` | 403 | The token is missing, or its terminal is gone. |
| `session_mismatch` | 403 | The body names another terminal. |
| `no_agent_running` | 409 | No agent process runs in the terminal. |
| `agent_mismatch` | 422 | `agent` differs from the detected agent. |
| `invalid_source` | 422 | Not `integration:`/`custom:`/`native:`/`manifest:` + id. |
| `invalid_state`, `invalid_blocked_reason`, `invalid_ttl` | 422 | Field values are invalid. |
| `invalid_metadata`, `invalid_tokens`, `invalid_detail` | 422 | Metadata is malformed (text is capped at 120 chars, at most 8 tokens). |
| `invalid_agent_session_id`, `invalid_resume_argv`, `resume_agent_mismatch`, `resume_not_allowed` | 422 | Native session data was rejected. |

Metadata (title, display agent, state label, tokens) is presentation only. It never changes state, notifications,
hand-offs or restore.

Native sessions (`agent_session_id` + `resume_argv`) are accepted only from `integration:*` sources and stored in
`agent_sessions`. Automatic resume is not implemented yet; argv is never rebuilt from terminal output.

## Context commands

See the README's CLI list: `sd context`, `recall`, `memory`, `remember`, `discover`, `decide`, `task update`,
`knowledge verify`, `projects` and `relate`. The matching endpoints are `GET /api/context`, `GET /api/context/recall`,
`GET|POST /api/context/memory`, `POST /api/context/decisions`, `POST /api/context/state`,
`POST /api/context/knowledge/{id}/status`, `GET /api/context/projects` and `POST /api/context/relationships`. Each
takes `cwd`, `session_id` or `project` to choose the project.
