"""Agent teams: spawn sub-agents, hand tasks between terminals, and teach every agent CLI the `sd` commands."""

import re
import shutil
from pathlib import Path

from . import agents

# ---------------------------------------------------------------- model tiers

# ponytail: keyword + length heuristic; the parent agent usually knows better and passes --model small|medium|large
LARGE_WORDS = re.compile(r"\b(architect\w*|design|refactor\w*|migrat\w*|rewrite|security|concurren\w*|race|perf\w*|debug\w*|investigat\w*|root cause|across|end.to.end)\b", re.I)
SMALL_WORDS = re.compile(r"\b(typo|rename|format\w*|lint|comment|docstring|readme|bump|changelog|spelling|small|quick|trivial)\b", re.I)
TIER_PATTERNS = {"small": re.compile(r"haiku|mini|flash|lite|nano"), "large": re.compile(r"opus|\bpro\b|max|ultra")}
TIERS = ("small", "medium", "large")


def tier(task: str) -> str:
    """How demanding a task looks: small, medium or large."""
    words = len(task.split())
    if LARGE_WORDS.search(task) or words > 120:
        return "large"
    if SMALL_WORDS.search(task) and words < 40:
        return "small"
    return "medium"


def pick_model(key: str, model: str | None, task: str) -> str | None:
    """--model as given; auto/small/medium/large map onto the agent's own models (medium = its default)."""
    model = (model or "auto").strip()
    if model not in ("auto", *TIERS):
        return model
    want = tier(task) if model == "auto" else model
    pattern = TIER_PATTERNS.get(want)
    return next((m for m in agents.models(key) if pattern.search(m)), None) if pattern else None


# ------------------------------------------------------------------- spawning

# agent -> (model flag, args before the prompt). Only agents whose CLI takes an initial prompt.
SPAWN = {
    "claude": ("--model", ()),
    "codex": ("-m", ()),
    "devin": ("--model", ("--",)),
    "gemini": ("-m", ("-i",)),
    "qwen": ("-m", ("-i",)),
    "opencode": ("-m", ("--prompt",)),
}
SAFE_PROMPT = re.compile(r"^[\w .,:/-]+$")  # needs no quoting rules in pwsh, cmd, bash, zsh or fish
SAFE_MODEL = re.compile(r"^[\w.:/\[\]-]+$")


SAFE_PATH = re.compile(r'^[^"`$%!\n\r]+$')  # quoted in double quotes, nothing a shell expands inside them


def _arg(a: str) -> str:
    if re.fullmatch(r"[\w.:/=@+-]+", a):
        return a
    if not SAFE_PATH.match(a):
        raise ValueError("unsafe_argument")
    return f'"{a}"'


def launch_line(key: str, model: str | None = None, prompt: str | None = None, extra: list[str] | tuple = ()) -> str:
    """An agent's command line with its own flags: model, per-run settings (`extra`, e.g. Claude's --settings) and the
    initial prompt the way that CLI takes it (SPAWN: claude/codex positional, gemini/qwen -i, devin after --,
    opencode --prompt). The prompt must be SAFE_PROMPT so one double-quoted string works in every shell."""
    parts = [agents.AGENTS[key][1][0]]
    if model:
        if not SAFE_MODEL.match(model):
            raise ValueError("invalid_model")
        parts += [SPAWN[key][0] if key in SPAWN else "--model", model]
    parts += [_arg(a) for a in extra]
    if prompt:
        if key not in SPAWN:
            raise ValueError("no_prompt_flag")
        assert SAFE_PROMPT.match(prompt), prompt
        parts += [*SPAWN[key][1], f'"{prompt}"']
    return " ".join(parts)


def spawn_line(key: str, model: str | None, prompt: str, extra: list[str] | tuple = ()) -> str:
    """The command typed into the new terminal's shell."""
    return launch_line(key, model, prompt, extra)


def kickoff(nick: str, parent_nick: str, handoff_id: str) -> str:
    return (f"You are {nick}, a shelldeck sub-agent working for {parent_nick}. Your task is in "
            f".shelldeck/handoffs/{handoff_id}.md. Do it, then run: sd done {handoff_id} followed by a short summary in quotes. "
            f"Ask {parent_nick}, not the user, when you need a decision: sd tell {parent_nick}. You cannot spawn other agents.")


# ------------------------------------------------------------ handoff files


def write_files(project_path: str, handoff: dict, all_handoffs: list[dict], snapshot: str = "") -> None:
    """<project>/.shelldeck/handoffs/<id>.md for the receiver and handoff.md as the index. Git ignores the folder."""
    root = Path(project_path) / ".shelldeck"
    (root / "handoffs").mkdir(parents=True, exist_ok=True)
    if not (root / ".gitignore").exists():
        (root / ".gitignore").write_text("*\n", encoding="utf-8")
    h = handoff
    (root / "handoffs" / f"{h['id']}.md").write_text(
        f"# Hand-off {h['id']}: {h['status']}\n\n"
        f"- From: {h['from_nick'] or 'you'} (terminal {h['from_sid'] or '-'})\n"
        f"- To: {h['to_nick']} (terminal {h['to_sid']}){_agent(h)}\n"
        f"- Created: {h['created_at'][:19].replace('T', ' ')} UTC\n\n"
        f"## Task\n\n{h['task'].strip()}\n\n"
        f"## When done\n\nRun `sd done {h['id']} \"<summary>\"` (add `--failed` if you could not do it). "
        f"Ask {h['from_nick'] or 'the sender'} with `sd tell {h['from_nick'] or h['from_sid']} \"...\"`.\n\n"
        f"## Result\n\n{(h.get('result') or '(pending)').strip()}\n{snapshot}",
        encoding="utf-8",
    )
    rows = "\n".join(
        f"| {x['id']} | {x['status']} | {x['from_nick'] or '-'} | {x['to_nick']} | {_cell(x['task'])} | {_cell(x.get('result') or '')} |"
        for x in all_handoffs
    )
    (root / "handoff.md").write_text(
        "# Hand-offs\n\nWritten by shelldeck; each task is in handoffs/<id>.md.\n\n"
        "| ID | Status | From | To | Task | Result |\n|---|---|---|---|---|---|\n" + rows + "\n",
        encoding="utf-8",
    )


def _agent(h: dict) -> str:
    return f", {agents.AGENTS[h['agent']][0]}" + (f" {h['model']}" if h.get("model") else "") if h.get("agent") in agents.AGENTS else ""


def _cell(text: str) -> str:
    text = " ".join(text.split()).replace("|", "/")
    return text[:80] + ("…" if len(text) > 80 else "")


def one_line(text: str, limit: int = 2000) -> str:
    """Typed into a TUI, a newline would submit early."""
    return " ".join(text.split())[:limit]


# -------------------------------------------------------------------- skills

SKILL = """---
name: shelldeck
description: Work with other AI agents in shelldeck terminals and keep shared project context. Use when SHELLDECK_SESSION_ID is set: at the start of work (sd context), when the user asks to spawn, delegate, hand off, run in parallel, ask or check on another agent, and before stopping.
---

# shelldeck agent team

You run in a shelldeck terminal when `SHELLDECK_SESSION_ID` is set. Every terminal has an id and a
person's name (`SHELLDECK_NICK`, e.g. Maya); commands take either.

- `sd agents` lists agent terminals in this project (`--all` for every project).
- `sd peek <name> -n 60` prints another terminal's screen, to check its progress.
- `sd tell <name> "message"` types a message into another terminal and presses Enter.
- `sd spawn "task" [--agent claude] [--model small|medium|large|<model>]` opens a sub-agent in a new
  terminal of this project and hands it the task. Pick the tier by difficulty: small for mechanical
  edits, medium for normal features, large for design, debugging or wide refactors. `auto` guesses.
  Split independent work into several spawns to run them in parallel.
- `sd handoff <name> "task"` hands a task to an agent that is already running.
- `sd handoffs` lists hand-offs and their status; each task is in `.shelldeck/handoffs/<id>.md`.
- `sd done <id> "summary"` closes a hand-off you were given (add `--failed` if you could not finish).
  The sender is told automatically; always run it when you finish.
- `sd close <name>` closes a sub-agent's terminal once its work is done (`--force` if a hand-off is still open).
- `sd answer <name> <keys>` presses keys in another terminal, e.g. to answer a sub-agent's approval
  menu: `sd answer Maya 1`, `sd answer Maya y enter`, `sd answer Maya esc`.

You are the control center for the sub-agents you spawn. Their questions and approval prompts come to
you as `[shelldeck] Your sub-agent ... is waiting on a question` messages, not to the user: read the
question (`sd peek <name>` for the full screen), decide, and answer with `sd answer` or `sd tell`.
Ask the user only when the decision is really theirs.
When a sub-agent's hand-off is done and you checked its work, ask the user whether to close it, and run
`sd close <name>` only after they agree (or when they tell you to close it).

## Shared context (it outlives you; the next agent continues from it)

At startup run `sd context`: it shows the active task, current state and next action, project memory,
decisions, your open hand-off and recent events. Continue from the next action instead of starting over.
- `sd recall "query"` searches knowledge and decisions from every project (e.g. "auth like acme-web").
  Treat results as reference: read the source files and adapt; never copy secrets or code blindly.
- `sd remember "fact" ["fact" ...] [--type architecture|api|convention|gotcha|...] [--file path]` (several in one call) and
  `sd discover "finding" --file path` store durable facts (not chat). `sd decide "decision" -r "reason"`
  records a settled decision; check `sd memory` first so you don't duplicate one.
- `sd task update [IN_PROGRESS|BLOCKED|DONE] --task "..." --step "..." --next "..." --tests "..." --error "..."`
  updates the task and state. Run it when the plan or step changes, after tests, and before you stop.
- `sd agent status [name]` shows agent states; `sd agent wait <name> --until done --timeout 10m` waits for one
  (exit 0 reached, 2 timeout, 3 it exited). `sd agent prompt <name> "text" --wait` sends a follow-up and waits for it.
- `sd agent cmd <name> compact` runs that agent's own slash command (compact, clear, model, status, review, ...;
  `sd agent cmd <agent> --list`). Commands and prompts are refused while the agent waits on a question.
- `sd switch <agent> --note "where I stopped"` hands this project's task to another agent in a new terminal; it
  starts from `sd context`. Use it only when the user asks.
Never put passwords, tokens or keys into these commands.

Rules:
- A sub-agent (`SHELLDECK_PARENT` is set) cannot spawn more agents, and asks its parent (`sd tell`), not the user.
- Give each spawn a self-contained task: files, expected result and how to check it.
- Do not reply to every message with another `sd tell`; finish with `sd done`.
"""

BLOCK_START, BLOCK_END = "<!-- SHELLDECK_START -->", "<!-- SHELLDECK_END -->"
# agent -> where its CLI reads user-wide instructions: ("skill", skills dir) or ("block", instructions file)
# ponytail: only claude, codex and devin (skill dirs) and gemini/opencode (files) are verified on a real install
TARGETS = {
    "claude": ("skill", "~/.claude/skills"),
    "codex": ("skill", "~/.codex/skills"),
    "devin": ("skill", "~/.config/devin/skills"),
    "gemini": ("block", "~/.gemini/GEMINI.md"),
    "opencode": ("block", "~/.config/opencode/AGENTS.md"),
    "qwen": ("block", "~/.qwen/QWEN.md"),
    "amp": ("block", "~/.config/amp/AGENTS.md"),
    "droid": ("block", "~/.factory/AGENTS.md"),
    "copilot": ("block", "~/.copilot/copilot-instructions.md"),
    "crush": ("block", "~/.config/crush/CRUSH.md"),
    "goose": ("block", "~/.config/goose/.goosehints"),
    "kiro": ("block", "~/.kiro/steering/shelldeck.md"),
}


def _target(key: str) -> tuple[str, Path]:
    kind, where = TARGETS[key]
    path = Path(where.replace("~", str(Path.home()), 1))
    return kind, path / "shelldeck" / "SKILL.md" if kind == "skill" else path


def _block_text() -> str:
    body = SKILL.split("---\n", 2)[2].strip()
    return f"{BLOCK_START}\n{body}\n{BLOCK_END}"


def install(keys: list[str] | None = None, remove: bool = False) -> list[tuple[str, str, str]]:
    """Write (or remove) the shelldeck skill for each agent. Default: agents whose CLI is on PATH.
    Returns (agent, path, what happened)."""
    keys = keys or [k for k in TARGETS if any(shutil.which(e) for e in agents.AGENTS[k][1])]
    out = []
    for key in keys:
        kind, path = _target(key)
        if kind == "skill":
            if remove:
                shutil.rmtree(path.parent, ignore_errors=True)
                out.append((key, str(path.parent), "removed"))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(SKILL, encoding="utf-8")
            out.append((key, str(path), "installed"))
            continue
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        pattern = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.S)
        new = pattern.sub("", old).rstrip()
        if not remove:
            new = (new + "\n\n" if new else "") + _block_text()
        if new.strip() or path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new + "\n" if new else "", encoding="utf-8")
        out.append((key, str(path), "removed" if remove else "installed"))
    return out


def installed(key: str) -> bool:
    if key not in TARGETS:
        return False
    kind, path = _target(key)
    return path.exists() and (kind == "skill" or BLOCK_START in path.read_text(encoding="utf-8", errors="replace"))


def _selfcheck() -> None:
    assert tier("fix a typo in the readme") == "small"
    assert tier("refactor the auth module") == "large"
    assert tier("add a --json flag to sd list") == "medium"
    assert pick_model("claude", "large", "") == "opus"
    assert pick_model("claude", "small", "") == "haiku"
    assert pick_model("claude", "medium", "") is None
    assert pick_model("claude", "claude-opus-5-5", "") == "claude-opus-5-5"
    line = spawn_line("devin", "swe-1.6", kickoff("Maya", "Ada", "h1a2b3c"))
    assert line.startswith('devin --model swe-1.6 -- "You are Maya') and line.endswith('quotes. Ask Ada, not the user, when you need a decision: sd tell Ada. You cannot spawn other agents."'), line
    import tempfile
    from unittest import mock

    with tempfile.TemporaryDirectory() as d, mock.patch.object(Path, "home", lambda: Path(d)):
        f = Path(d) / ".gemini" / "GEMINI.md"
        f.parent.mkdir()
        f.write_text("mine\n", encoding="utf-8")
        install(["gemini", "claude"])
        install(["gemini"])  # idempotent: one block
        assert f.read_text(encoding="utf-8").count(BLOCK_START) == 1 and f.read_text(encoding="utf-8").startswith("mine")
        assert installed("claude") and installed("gemini")
        install(["gemini", "claude"], remove=True)
        assert f.read_text(encoding="utf-8") == "mine\n" and not installed("claude")


if __name__ == "__main__":
    _selfcheck()
    print("ok")
