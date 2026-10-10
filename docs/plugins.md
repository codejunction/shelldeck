# Writing a shelldeck plugin

A plugin is a Python package that adds commands to shelldeck. A command can be used in three places:

- the `?` menu in a terminal: `?docker logs web`
- the CLI: `sd docker logs web`
- the *Plugins* page (sidebar, *More*), which has a *Run* button for each command

A plugin can also react to shelldeck's events, such as an agent finishing or a hand-off being created.

Plugins run inside the shelldeck server, with your user's permissions, like any other package you install. Install only plugins you trust.

## 1. Create the package

```text
shelldeck-hello/
├── pyproject.toml
└── shelldeck_hello.py
```

`pyproject.toml`:

```toml
[project]
name = "shelldeck-hello"
version = "0.1.0"
description = "Says hello from shelldeck"
requires-python = ">=3.12"
dependencies = ["shelldeck"]

# The entry point name ("hello") must be the same as the Plugin's name.
[project.entry-points."shelldeck.plugins"]
hello = "shelldeck_hello:plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

`shelldeck_hello.py`:

```python
from shelldeck.plugins import Plugin

plugin = Plugin("hello", "0.1.0")


@plugin.command("greet", "Say hello", usage="<name>")
def greet(args, ctx):
    return f"Hello, {' '.join(args)}! You are in {ctx['cwd']}"


@plugin.command("echo", "Type an echo command at the prompt", usage="[text]")
def echo(args, ctx):
    return {"input": "echo " + " ".join(args or ["hello"])}
```

The package description in `pyproject.toml` is shown on the *Plugins* page.

## 2. Install it and try it

Install the plugin into the same environment as shelldeck, then restart shelldeck:

```sh
uv tool install shelldeck --with ./shelldeck-hello        # or --with shelldeck-hello from PyPI
sd stop && sd
sd plugin list
sd hello greet Ada
```

While you develop, install the plugin in editable mode (`--with-editable ./shelldeck-hello`). Restart shelldeck (`sd stop && sd`) after each change: plugins are imported once, when the server starts.

If you run shelldeck from a source checkout, run `uv pip install -e ./shelldeck-hello` in that checkout instead.

## Commands

```python
@plugin.command(name, description="", usage="")
def handler(args: list[str], ctx: dict): ...
```

- `name`: lowercase letters, digits and dashes. The full command is `<plugin> <name>`.
- `description`: shown in the `?` menu, on the *Plugins* page and in `sd plugin list`.
- `usage`: describes the arguments. Put required arguments in angle brackets and optional ones in square brackets, for example `"<container> [--tail N]"`.
  - If `usage` has a `<required>` argument and the command is picked without arguments, the `?` menu fills in `docker logs ` so you can type the arguments. The *Run* button asks for them.
  - If `usage` only has `[optional]` arguments, the *Run* button asks for them but you can leave the field empty.
- `args`: the words typed after the command, already split on spaces: `?docker logs web --tail 50` gives `["web", "--tail", "50"]`. Quotes are not parsed.
- `ctx`: the terminal the command was run from:
  - `session_id`: the shelldeck terminal id. It is empty when the command came from the CLI outside a shelldeck terminal.
  - `cwd`: that terminal's current folder (or the project folder).
  - `shell`: the shell kind (`pwsh`, `cmd`, `bash`, `wsl`, ...).
  - `project`: the project folder.

### What a command returns

| Return value | What happens |
|---|---|
| a `str`, or `{"text": "..."}` | The text is shown in a dialog in the UI, or printed by the CLI. |
| `{"input": "docker logs -f web"}` | The command line is typed at the terminal's prompt **without pressing Enter**. The person reviews it and runs it. The CLI prints it. It is cut to one line of printable characters. |
| `None` | Nothing is shown. |

Use `{"input": ...}` for anything that changes state or runs for a long time. The person stays in control, and the command runs in their shell with its output in the terminal.

If a command raises an exception (even `SystemExit`), the error is shown to whoever ran it. It never stops shelldeck.

Handlers run on a worker thread, so blocking calls are fine. Keep them reasonably quick, because the person is waiting for the result. Use timeouts on subprocesses and network calls.

## Ask uses your plugins

When someone asks for a command (`?` then *Ask*, `Ctrl+I`, or `sd ask`), the agent's model sees every loaded plugin command, with its `usage` and `description`. If a plugin command does exactly what was asked, the model answers with that command instead of a shell command. For example, "follow the logs of the web container" becomes `docker logs web`. The menu then offers the plugin command, and Enter runs it; `sd ask` prints `sd docker logs web`. Only commands that really exist are accepted.

A clear `description` and an accurate `usage` are what lead the model to pick your command.

## Events

```python
@plugin.on("handoff.")          # every event whose type starts with "handoff."
def on_handoff(event):
    print(event["type"], event["data"])
```

| Type | `data` |
|---|---|
| `agent.detected` | `session_id`, `agent`, `generation` |
| `agent.state` | `session_id`, `agent`, `state` (`idle`, `working`, `blocked`, `done`), `reason`, `source` |
| `agent.exited` | same as `agent.state` |
| `agent.command` | `session_id`, `agent`, `command` |
| `agent.session_updated` | `session_id`, `agent`, `source` |
| `handoff.created` | `id`, `from`, `to`, `to_sid`, `agent` |
| `handoff.completed` | `id`, `status`, `to`, `to_sid` |
| `integration.changed` | `agent`, `status` |

Each event is delivered on its own thread. Errors in a handler are logged to `shelldeck.log` and don't affect other handlers or plugins.

## Managing plugins

- `sd plugin list`: each plugin's version and status, the error if it failed to load, and its commands.
- `sd plugin disable NAME` / `sd plugin enable NAME`: turn a plugin off or on without uninstalling it. A disabled plugin's code is not imported again. The *Plugins* page has the same switch. Only the machine running shelldeck can change it.
- `sd <plugin> <command> [args]` is short for `sd plugin run <plugin> <command> [args]`. A single word that is a folder still opens the folder (`sd ./project`).

## Troubleshooting

- **The plugin isn't listed.** It isn't installed in shelldeck's environment, or the entry point group isn't exactly `shelldeck.plugins`. Check with `uv tool list --show-with`, then restart shelldeck.
- **"Failed" with `ImportError`.** A dependency is missing. Add it to the plugin's `dependencies`.
- **"the entry point must be a shelldeck.plugins.Plugin named ..."**: the entry point must point at a `Plugin` object (not a class or a module), and its name must match the entry point's name.
- **Changes don't show up.** Restart shelldeck. Plugins are imported once, when the server starts.

## Reference plugin

`examples/shelldeck-docker` in the shelldeck repository has `docker ps`, `images`, `logs`, `restart` and `compose`. It shows both kinds of result: text from `ps`, and a typed command from `logs`.
