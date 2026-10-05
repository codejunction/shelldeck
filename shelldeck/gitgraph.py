"""Git commit graph for a project: log parsing, lane layout, checkout.

The lane-assignment algorithm mirrors what tig/gitk/vscode-git-graph do
internally: walk commits newest-first, keep a list of "lanes" each
waiting for a specific next commit hash, and let a commit take over the
leftmost lane already waiting for it (or open a new one). Merge commits
add lanes for their extra parents, reusing freed columns to keep the
graph narrow.
"""

import re
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


# ------------------------------------------------------------------ working-tree changes

MAX_DIFF = 200_000  # bytes of diff text sent to the UI
STATUS_NAME = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "U": "conflict", "?": "untracked", "T": "type changed"}


def changes(path: str) -> dict:
    """Uncommitted changes at a glance: branch, ahead/behind and each changed file with its line counts."""
    out = _run(path, ["status", "--porcelain=v1", "-z", "--branch", "--untracked-files=all"], timeout=10)
    branch, ahead, behind, files = "", 0, 0, []
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if not e:
            continue
        if e.startswith("## "):
            head = e[3:]
            branch = head.split("...")[0].replace("No commits yet on ", "")
            ahead = int(m[1]) if (m := re.search(r"ahead (\d+)", head)) else 0
            behind = int(m[1]) if (m := re.search(r"behind (\d+)", head)) else 0
            continue
        x, y, name = e[0], e[1], e[3:]
        old = None
        if "R" in (x, y) or "C" in (x, y):
            old, i = entries[i], i + 1  # -z puts the rename source in the next entry
        code = "?" if x == "?" else "U" if "U" in (x, y) or (x, y) in (("A", "A"), ("D", "D")) else (x if x != " " else y)
        files.append({"path": name, "old_path": old, "status": STATUS_NAME.get(code, "modified"), "staged": x not in (" ", "?"),
                      "unstaged": y not in (" ",), "added": None, "deleted": None})
    if files:
        counts: dict[str, tuple[int | None, int | None]] = {}
        try:
            numstat = _run(path, ["diff", "HEAD", "--numstat", "-z", "--no-renames"], timeout=10)
        except GitError:  # no commits yet: compare the index with the empty tree
            numstat = _run(path, ["diff", "--cached", "--numstat", "-z", "--no-renames"], timeout=10)
        for rec in numstat.split("\0"):
            parts = rec.split("\t")
            if len(parts) == 3:
                counts[parts[2]] = (None if parts[0] == "-" else int(parts[0]), None if parts[1] == "-" else int(parts[1]))
        for f in files:
            if f["path"] in counts:
                f["added"], f["deleted"] = counts[f["path"]]
            elif f["status"] == "untracked":
                f["added"], f["deleted"] = _line_count(Path(path) / f["path"]), 0
    return {"branch": branch, "ahead": ahead, "behind": behind, "files": files}


def _line_count(file: Path) -> int | None:
    try:
        data = file.read_bytes()[:MAX_DIFF]
    except OSError:
        return None
    return None if b"\0" in data else data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def diff(path: str, file: str) -> dict:
    """One changed file's diff against HEAD (staged and unstaged together); an untracked file shows as all added.
    Only paths `git status` reports are accepted, so nothing outside the repo's changes is read."""
    entry = next((f for f in changes(path)["files"] if f["path"] == file), None)
    if not entry:
        return {"file": file, "diff": "", "binary": False, "truncated": False, "missing": True}
    if entry["status"] == "untracked":
        target = Path(path) / file
        try:
            data = target.read_bytes()
        except OSError:
            data = b""
        if b"\0" in data[:8000]:
            return {"file": file, "diff": "", "binary": True, "truncated": False}
        text = data[:MAX_DIFF].decode("utf-8", "replace")
        body = "".join(f"+{line}\n" for line in text.splitlines())
        return {"file": file, "diff": f"--- /dev/null\n+++ b/{file}\n{body}", "binary": False, "truncated": len(data) > MAX_DIFF}
    try:
        out = _run(path, ["diff", "HEAD", "--no-color", "--no-ext-diff", "--", file], timeout=10)
    except GitError:
        out = _run(path, ["diff", "--cached", "--no-color", "--no-ext-diff", "--", file], timeout=10)
    return {"file": file, "diff": out[:MAX_DIFF], "binary": "Binary files" in out[:2000] and "@@" not in out, "truncated": len(out) > MAX_DIFF}
