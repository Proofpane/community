"""Searching the text of a session's worktree (workspace.search), through the real agent: what git sees there, tracked
or new and not ignored, text only; literal unless asked for an expression; bounded in count, length and time."""

import pytest

from workspace_fixtures import Agent, git, make_repo


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    repo = tmp_path / "ws" / "repos" / "demo"
    make_repo(str(repo))
    (repo / "app.py").write_text("def total(items):\n    return sum(items)  # Total of the items\n")
    (repo / ".gitignore").write_text("build/\n")
    (repo / "a:b.txt").write_text("a colon in the name, and total\n")
    git("add", "-A", cwd=str(repo))
    git("commit", "-q", "-m", "files", cwd=str(repo))
    yield a
    a.close()


def _session(agent, tmp_path):
    s = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Look"}).json()
    return s["id"], tmp_path / "ws" / "sessions" / s["id"] / "work"


def _search(agent, sid, q, **flags):
    params = {"q": q, **{k: "1" for k, v in flags.items() if v}}
    return agent.http.get(f"/sessions/{sid}/search", params=params)


def _found(r):
    assert r.status_code == 200, r.text
    return [(m["path"], m["line"]) for m in r.json()["matches"]]


def test_it_finds_text_in_tracked_and_new_files_but_not_ignored_ones(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "notes.md").write_text("first\nthe total is wrong\n")          # new, not yet added
    (work / "build").mkdir()
    (work / "build" / "out.txt").write_text("total\n")                     # ignored
    r = _search(agent, sid, "total")
    assert sorted(_found(r)) == [("a:b.txt", 1), ("app.py", 1), ("app.py", 2), ("notes.md", 2)]
    m = next(m for m in r.json()["matches"] if m["path"] == "notes.md")
    assert m == {"path": "notes.md", "line": 2, "column": 5, "text": "the total is wrong", "cut": False}
    assert r.json()["truncated"] is False and r.json()["query"] == "total"


def test_case_is_ignored_unless_asked(agent, tmp_path):
    sid, _ = _session(agent, tmp_path)
    assert ("app.py", 2) in _found(_search(agent, sid, "TOTAL"))
    assert _found(_search(agent, sid, "Total", case=True)) == [("app.py", 2)]


def test_a_query_is_literal_unless_it_is_an_expression(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "x.txt").write_text("abc\na.c\n")
    assert _found(_search(agent, sid, "a.c")) == [("x.txt", 2)]
    assert sorted(_found(_search(agent, sid, "^a.c$", regex=True))) == [("x.txt", 1), ("x.txt", 2)]
    assert _found(_search(agent, sid, "^a.c$")) == []                      # literally, no line is that
    assert _found(_search(agent, sid, "^def [a-z]+\\(", regex=True)) == [("app.py", 1)]


def test_a_query_that_looks_like_an_option_is_only_text(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "cli.txt").write_text("run it with --version\n")
    assert _found(_search(agent, sid, "--version")) == [("cli.txt", 1)]


def test_binary_files_are_left_out(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "blob.bin").write_bytes(b"total\0\x01\x02")
    assert ("blob.bin", 1) not in _found(_search(agent, sid, "total"))


def test_matches_and_lines_are_bounded(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "many.txt").write_text("needle\n" * 450)
    (work / "long.txt").write_text("needle " + "x" * 1000 + "\n")
    r = _search(agent, sid, "needle")
    assert r.status_code == 200 and len(r.json()["matches"]) == 400 and r.json()["truncated"] is True
    long = _search(agent, sid, "needle x").json()["matches"]
    assert long[0]["path"] == "long.txt" and len(long[0]["text"]) == 400 and long[0]["cut"] is True


@pytest.mark.parametrize("q", ["", "   ", "two\nlines", "x" * 201])
def test_a_query_is_one_short_line(agent, tmp_path, q):
    sid, _ = _session(agent, tmp_path)
    r = _search(agent, sid, q)
    assert r.status_code == 400 and "one line" in r.json()["detail"]


def test_an_expression_git_cannot_read_is_said_so(agent, tmp_path):
    sid, _ = _session(agent, tmp_path)
    r = _search(agent, sid, "a(b", regex=True)
    said = r.json()["detail"]
    assert r.status_code == 400 and said.startswith("That is not a regular expression git can read (")
    assert said.endswith("). Turn off the regular expression to look for the text as typed.")
    assert "fatal" not in said and "-e option" not in said and "'a(b'" not in said


def test_nothing_found_is_an_empty_list(agent, tmp_path):
    sid, _ = _session(agent, tmp_path)
    r = _search(agent, sid, "nowhere-in-this-repository")
    assert r.status_code == 200 and r.json()["matches"] == [] and r.json()["truncated"] is False


def test_only_a_session_with_a_worktree_is_searched(agent):
    r = agent.http.post("/sessions", json={"repo": None, "program": "shell", "task": "No repository"})
    assert _search(agent, r.json()["id"], "x").status_code == 400
    assert _search(agent, "aaaaaaaaaaaa", "x").status_code == 404


def test_a_search_that_runs_too_long_is_stopped(tmp_path, monkeypatch):
    import os
    import time
    from proofpane_workspace import workspace as module
    from proofpane_workspace.workspace import Workspace, WorkspaceError
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    record, _ = w.prepare("demo", "shell", "Look")
    slow = tmp_path / "slow-bin"
    slow.mkdir()
    (slow / "git").write_text("#!/bin/sh\nexec sleep 30\n")
    os.chmod(slow / "git", 0o755)
    monkeypatch.setenv("PATH", f"{slow}:{os.environ['PATH']}")
    monkeypatch.setattr(module, "SEARCH_TIMEOUT", 0.5)
    started = time.monotonic()
    with pytest.raises(WorkspaceError, match="took longer than"):
        w.search(record["id"], "anything")
    assert time.monotonic() - started < 10


def test_a_search_git_cannot_run_is_not_blamed_on_the_expression(agent, tmp_path):
    sid, _ = _session(agent, tmp_path)
    work = tmp_path / "ws" / "sessions" / sid / "work"
    (work / ".git").write_text("gitdir: /nowhere\n")                 # a worktree git cannot read
    r = _search(agent, sid, "a.c", regex=True)
    assert r.status_code == 400 and r.json()["detail"].startswith("git could not search")

