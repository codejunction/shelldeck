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
