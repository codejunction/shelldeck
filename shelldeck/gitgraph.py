"""Git commit graph for a project: log parsing, lane layout, checkout.

The lane-assignment algorithm mirrors what tig/gitk/vscode-git-graph do
internally: walk commits newest-first, keep a list of "lanes" each
waiting for a specific next commit hash, and let a commit take over the
leftmost lane already waiting for it (or open a new one). Merge commits
add lanes for their extra parents, reusing freed columns to keep the
graph narrow.
"""

import subprocess
import sys
from pathlib import Path

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
FIELD_SEP = "\x1f"
RECORD_SEP = "\x1e"
FORMAT = FIELD_SEP.join(["%H", "%h", "%P", "%an", "%ad", "%s", "%D"]) + RECORD_SEP
# mirrors PROJECT_COLORS in app.js
PALETTE = ["#60a5fa", "#f472b6", "#34d399", "#fbbf24", "#a78bfa", "#f87171", "#22d3ee", "#fb923c"]


class GitError(Exception):
    pass


def _run(path: str, args: list[str], timeout: int = 15) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=path,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitError(str(e)) from e
    if proc.returncode != 0:
        raise GitError((proc.stderr or "").strip() or f"git {args[0]} failed")
    return proc.stdout


def parse_log(text: str) -> list[dict]:
    """Parse `git log --format=FORMAT` output into raw commit dicts."""
    commits = []
    for record in text.split(RECORD_SEP):
        record = record.strip("\n")
        if not record.strip():
            continue
        h, short, parents, author, date, subject, refs = record.split(FIELD_SEP)
        commits.append({
            "hash": h,
            "short": short,
            "parents": parents.split() if parents else [],
            "author": author,
            "date": date,
            "subject": subject,
            "refs": [r.strip() for r in refs.split(",") if r.strip()],
        })
    return commits


def build_graph(commits: list[dict]) -> None:
    """Assign `column`, `color`, `enter`, `exit`, and `through` to each commit in place.

    Each row is drawn in two halves, matching how vscode-git-graph/gitk render a row:
    - `enter`: lanes converging into this commit's node from above (`{from_col, color}`,
      curved unless from_col == column).
    - `exit`: lanes diverging from this commit's node downward, one per parent
      (`{to_col, color}`, curved unless to_col == column).
    - `through`: unrelated lanes that just pass straight through this row (`{col, color}`).
    """
    lanes: dict[int, dict] = {}  # column -> {"next": hash, "color": color}
    next_color = 0

    def new_color() -> str:
        nonlocal next_color
        c = PALETTE[next_color % len(PALETTE)]
        next_color += 1
        return c

    def free_column() -> int:
        return next((c for c in range(len(lanes) + 1) if c not in lanes), 0)

    for commit in commits:
        h = commit["hash"]
        matches = sorted(col for col, lane in lanes.items() if lane["next"] == h)
        if matches:
            col, color = matches[0], lanes[matches[0]]["color"]
        else:
            col, color = free_column(), new_color()

        enter, through = [], []
        for other_col, lane in list(lanes.items()):
            if other_col in matches:
                enter.append({"from_col": other_col, "color": lane["color"]})
                del lanes[other_col]
            elif other_col != col:
                through.append({"col": other_col, "color": lane["color"]})

        exit_ = []
        parents = commit["parents"]
        if parents:
            lanes[col] = {"next": parents[0], "color": color}
            exit_.append({"to_col": col, "color": color})
            for parent in parents[1:]:
                free, pcolor = free_column(), new_color()
                lanes[free] = {"next": parent, "color": pcolor}
                exit_.append({"to_col": free, "color": pcolor})
        else:
            lanes.pop(col, None)

        commit["column"] = col
        commit["color"] = color
        commit["enter"] = enter
        commit["exit"] = exit_
        commit["through"] = through


def get_log(path: str, max_commits: int = 300) -> dict:
    """Commit graph for the repo at `path`: local branches + HEAD."""
    try:
        branch = _run(path, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=5).strip()
    except GitError:  # unborn HEAD: a fresh `git init` with no commits
        return {"commits": [], "current_branch": "", "has_more": False}
    raw = _run(path, ["log", "HEAD", "--branches", "--date-order", f"--format={FORMAT}",
                      "--date=format:%Y-%m-%dT%H:%M:%S", "-n", str(max_commits + 1)])
    commits = parse_log(raw)
    has_more = len(commits) > max_commits
    commits = commits[:max_commits]
    build_graph(commits)
    return {"commits": commits, "current_branch": branch, "has_more": has_more}


def checkout(path: str, ref: str) -> tuple[bool, str]:
    """`git checkout <ref>`. git itself refuses an unsafe checkout on a dirty tree."""
    if ref.startswith("-"):  # would be parsed as an option (e.g. --force)
        return False, "invalid ref"
    try:
        out = _run(path, ["checkout", ref])
        return True, out.strip()
    except GitError as e:
        return False, str(e)


def has_git(path: str) -> bool:
    return (Path(path) / ".git").exists()
