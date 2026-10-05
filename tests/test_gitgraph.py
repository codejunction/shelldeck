import subprocess

import pytest

from shelldeck import gitgraph


def _commit(h, parents=(), refs=()):
    return {"hash": h, "short": h[:2], "parents": list(parents), "author": "a", "date": "2026-01-01", "subject": h, "refs": list(refs)}


def test_build_graph_linear_history_stays_in_one_lane():
    commits = [_commit("c", ["b"]), _commit("b", ["a"]), _commit("a", [])]
    gitgraph.build_graph(commits)
    assert [c["column"] for c in commits] == [0, 0, 0]
    assert len({c["color"] for c in commits}) == 1
    assert not any(c["through"] for c in commits)
    # nothing merges or branches, so every enter/exit stays in the same column
    assert all(e["from_col"] == c["column"] for c in commits for e in c["enter"])
    assert all(x["to_col"] == c["column"] for c in commits for x in c["exit"])


def test_build_graph_merge_converges_two_lanes():
    commits = [
        _commit("m", ["c1", "b1"]),
        _commit("c1", ["root"]),
        _commit("b1", ["root"]),
        _commit("root", []),
    ]
    gitgraph.build_graph(commits)
    m, c1, b1, root = commits
    assert m["column"] == 0
    assert c1["column"] == 0
    assert b1["column"] == 1  # second parent gets its own lane
    assert root["column"] == 0  # leftmost lane wins when both converge

    # both lanes converge into the root commit's node
    assert {e["from_col"] for e in root["enter"]} == {0, 1}
    assert not root["exit"]  # root has no parents, the lane ends here


def test_build_graph_reuses_freed_columns():
    # a second, unrelated branch tip should reuse column 1 once it's been freed
    commits = [
        _commit("m", ["c1", "b1"]),
        _commit("c1", ["root"]),
        _commit("b1", ["root"]),
        _commit("root", ["older"]),
        _commit("older", []),
    ]
    gitgraph.build_graph(commits)
    assert max(c["column"] for c in commits) == 1


def test_parse_log_round_trip():
    text = gitgraph.FIELD_SEP.join(["deadbeef", "dead", "cafe", "Ada Lovelace", "2026-01-01 00:00", "fix bug", "HEAD -> main, tag: v1"]) + gitgraph.RECORD_SEP
    commits = gitgraph.parse_log(text)
    assert commits == [{
        "hash": "deadbeef", "short": "dead", "parents": ["cafe"], "author": "Ada Lovelace",
        "date": "2026-01-01 00:00", "subject": "fix bug", "refs": ["HEAD -> main", "tag: v1"],
    }]


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True,
                        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
                             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
                             **__import__("os").environ})

    git("init", "-q", "-b", "main")
    (tmp_path / "f.txt").write_text("1")
    git("add", ".")
    git("commit", "-q", "-m", "first")
    (tmp_path / "f.txt").write_text("2")
    git("commit", "-q", "-am", "second")
    return tmp_path


def test_has_git(tmp_path, repo):
    assert gitgraph.has_git(str(repo))
    assert not gitgraph.has_git(str(tmp_path.parent))


def test_get_log_reads_real_repo(repo):
    result = gitgraph.get_log(str(repo))
    assert [c["subject"] for c in result["commits"]] == ["second", "first"]
    assert result["current_branch"] == "main"
    assert result["has_more"] is False
    assert result["commits"][0]["column"] == 0


def test_checkout_switches_branch(repo):
    subprocess.run(["git", "branch", "feature"], cwd=repo, check=True, capture_output=True)
    ok, _ = gitgraph.checkout(str(repo), "feature")
    assert ok
    assert gitgraph.get_log(str(repo))["current_branch"] == "feature"


def test_checkout_reports_git_error(repo):
    ok, message = gitgraph.checkout(str(repo), "no-such-branch")
    assert not ok
    assert message


def test_changes_and_diff(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    (tmp_path / "gone.txt").write_text("x\n")
    (tmp_path / "old.txt").write_text("r\n" * 20)
    git("add", ".")
    git("commit", "-qm", "init")
    (tmp_path / "a.txt").write_text("one\nTWO\nthree\n")
    (tmp_path / "gone.txt").unlink()
    (tmp_path / "new file.txt").write_text("n1\nn2\n")
    git("mv", "old.txt", "moved.txt")
    got = gitgraph.changes(str(tmp_path))
    by = {f["path"]: f for f in got["files"]}
    assert got["branch"] == "main"
    assert by["a.txt"]["status"] == "modified" and (by["a.txt"]["added"], by["a.txt"]["deleted"]) == (2, 1)
    assert by["gone.txt"]["status"] == "deleted"
    assert by["new file.txt"]["status"] == "untracked" and by["new file.txt"]["added"] == 2
    assert by["moved.txt"]["status"] == "renamed" and by["moved.txt"]["old_path"] == "old.txt" and by["moved.txt"]["staged"]
    d = gitgraph.diff(str(tmp_path), "a.txt")
    assert "+TWO" in d["diff"] and "-two" in d["diff"]
    assert gitgraph.diff(str(tmp_path), "new file.txt")["diff"].endswith("+n1\n+n2\n")
    assert gitgraph.diff(str(tmp_path), "../etc/passwd")["missing"]  # only files git reports


def test_changes_before_first_commit(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "x.py").write_text("print(1)\n")
    got = gitgraph.changes(str(tmp_path))
    assert got["branch"] == "main" and got["files"][0]["status"] == "untracked"
