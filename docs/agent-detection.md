# Agent detection rules

When no integration reports an agent's state, shelldeck reads its screen. An agent whose output has gone quiet is
**blocked** if the end of its screen matches a rule.

- **Rules ship with shelldeck:** `shelldeck/agent_detection/default.toml` (versioned, updated with releases). No rules
  are downloaded.
- **Override per agent:** `<config>/agent-detection/<agent>.toml`. The config folder is `~/.config/shelldeck`, or
  `SHELLDECK_HOME`. The agent name is the kind shown by `sd agent status`, e.g. `claude`, `codex`, `gemini`, `devin`.

## Format

```toml
version = 1                    # required, a positive integer
disable = ["prompt.yes-no"]    # bundled rule ids to turn off for this agent

[[rules]]
id = "gemini.allow"            # same id as a bundled rule: replaces it
pattern = "Allow execution of" # Python regex, case-insensitive
reason = "approval"            # approval | question | authentication | tool_error | external_wait | unknown
agents = ["gemini"]            # optional; empty means every agent
```

When several rules match, the one matching latest on the screen wins, because the prompt shown now is at the bottom.

## When an override is wrong

An override that doesn't parse, has a bad regex or uses an unknown reason is **ignored as a whole**. The bundled rules
keep working, and the problem is reported:

```sh
sd agent explain Maya                               # "rules: bundled v1 ...; WARNING override ignored: ..."
sd agent explain --file screen.txt --agent gemini   # test rules against saved screen text
```

`sd agent explain` names the matched rule id, never the screen text.

The browser keeps its own copy of the bundled phrases (`QUESTION_RE` in `static/app.js`) for the tab-side check. Keep
the two in sync when you change the bundled rules.
