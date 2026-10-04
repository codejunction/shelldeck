"""Versioned screen rules for the lifecycle fallback (`agent_detection/default.toml`) with per-agent local overrides
(`<config>/agent-detection/<agent>.toml`). An invalid override is ignored as a whole and reported, never fatal."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import db
from .agent_state import VALID_BLOCKED_REASONS

BUNDLED = Path(__file__).parent / "agent_detection" / "default.toml"


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    reason: str
    agents: tuple[str, ...] = ()  # empty: every agent


@dataclass(frozen=True)
class Manifest:
    rules: tuple[Rule, ...]
    source: str  # "bundled" or the override's path
    version: int
    error: str | None = None  # why an override was ignored


def _parse(data: dict, where: str) -> tuple[int, list[Rule], list[str]]:
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"{where}: version must be a positive integer")
    rules = []
    for i, r in enumerate(data.get("rules") or []):
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not isinstance(r.get("pattern"), str):
            raise ValueError(f"{where}: rule {i + 1} needs an id and a pattern")
        reason = r.get("reason", "question")
        if reason not in VALID_BLOCKED_REASONS:
            raise ValueError(f"{where}: rule {r['id']} has an unknown reason {reason!r}")
        try:
            pattern = re.compile(r["pattern"], re.I)
        except re.error as e:
            raise ValueError(f"{where}: rule {r['id']} pattern: {e}") from None
        agents = r.get("agents") or []
        if not isinstance(agents, list) or not all(isinstance(a, str) for a in agents):
            raise ValueError(f"{where}: rule {r['id']} agents must be a list of names")
        rules.append(Rule(r["id"], pattern, reason, tuple(agents)))
    disable = data.get("disable") or []
    if not isinstance(disable, list) or not all(isinstance(x, str) for x in disable):
        raise ValueError(f"{where}: disable must be a list of rule ids")
    return version, rules, disable


_cache: dict[str, tuple[float, Manifest]] = {}


def _override(agent: str) -> Path:
    return db.config_dir() / "agent-detection" / f"{agent}.toml"


def load(agent: str) -> Manifest:
    """The rules for one agent: bundled, then the local override on top (cached until the override file changes)."""
    file = _override(agent) if re.fullmatch(r"[a-z0-9_-]{1,40}", agent or "") else None
    mtime = file.stat().st_mtime if file and file.exists() else 0.0
    hit = _cache.get(agent)
    if hit and hit[0] == mtime:
        return hit[1]
    version, base, _ = _parse(tomllib.loads(BUNDLED.read_text(encoding="utf-8")), "bundled")
    rules = [r for r in base if not r.agents or agent in r.agents]
    manifest = Manifest(tuple(rules), "bundled", version)
    if mtime:
        try:
            v, extra, disable = _parse(tomllib.loads(file.read_text(encoding="utf-8")), str(file))
            by_id = {r.id: r for r in rules}
            by_id.update({r.id: r for r in extra if not r.agents or agent in r.agents})
            manifest = Manifest(tuple(r for r in by_id.values() if r.id not in disable), str(file), v)
        except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
            manifest = Manifest(tuple(rules), "bundled", version, f"override ignored: {e}")
    _cache[agent] = (mtime, manifest)
    return manifest


def match(agent: str, text: str) -> Rule | None:
    """The rule whose last match is latest on screen (the prompt shown now), or None."""
    best, at = None, -1
    for rule in load(agent).rules:
        found = list(rule.pattern.finditer(text))
        if found and found[-1].start() > at:
            best, at = rule, found[-1].start()
    return best
