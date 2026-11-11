"""A session's name (workspace.rename), and the rule that keeps it: a session's record is changed as it is on disk at
the time (workspace._update), so what one request writes after a slow step does not undo what another wrote meanwhile."""

import ast
import os
import threading

import pytest

from proofpane_workspace import workspace as workspace_module
from proofpane_workspace.workspace import NotFound, Workspace, WorkspaceError
from workspace_fixtures import Agent, git, make_repo

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    yield a
    a.close()


def _session(agent):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Fix the parser\n\nIn detail."})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_name_is_shown_and_an_empty_one_puts_the_task_back(agent):
    s = _session(agent)
    assert "title" not in s
    r = agent.http.post(f"/sessions/{s['id']}/title", json={"title": "  Parser   work \t first "})
    assert r.status_code == 200 and r.json()["title"] == "Parser work first"
    assert r.json()["task"] == "Fix the parser\n\nIn detail."          # the task is as it was
    listed = agent.http.get("/sessions").json()["sessions"]
    assert [x.get("title") for x in listed] == ["Parser work first"]
    r = agent.http.post(f"/sessions/{s['id']}/title", json={"title": "   "})
    assert r.status_code == 200 and "title" not in r.json()


@pytest.mark.parametrize("title", ["x" * 121, 7, None, ["a"]])
def test_a_name_is_short_text(agent, title):
    s = _session(agent)
    r = agent.http.post(f"/sessions/{s['id']}/title", json={"title": title})
    assert r.status_code == 400 and "at most 120" in r.json()["detail"]


def test_an_unknown_session_has_no_name_to_change(agent):
    assert agent.http.post("/sessions/aaaaaaaaaaaa/title", json={"title": "x"}).status_code == 404


def test_finish_writes_the_task_not_the_name(agent, tmp_path):
    s = _session(agent)
    agent.http.post(f"/sessions/{s['id']}/title", json={"title": "Just a label"})
    (tmp_path / "ws" / "sessions" / s["id"] / "work" / "new.txt").write_text("new\n")
    r = agent.http.post(f"/sessions/{s['id']}/finish", json={"author_name": "O", "author_email": "o@example.com"})
    assert r.status_code == 200, r.text
    subject = git("log", "-1", "--format=%s", s["branch"], cwd=str(tmp_path / "ws" / "repos" / "demo")).strip()
    assert subject == "Fix the parser"
    got = agent.http.get(f"/sessions/{s['id']}").json()
    assert got["title"] == "Just a label" and got["finished"]["head"]


def test_a_rename_during_a_slow_finish_is_kept(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    record, _ = w.prepare("demo", "shell", "Grow")
    with open(os.path.join(w._dir(record["id"]), "work", "grown.txt"), "w") as fh:
        fh.write("grown\n")
    committing, renamed = threading.Event(), threading.Event()
    real = workspace_module.git

    def slow(*args, **kw):
        if args and args[0] == "-c" and "commit" in args or args and args[0] == "commit":
            committing.set()
            assert renamed.wait(10)
        return real(*args, **kw)

    monkeypatch.setattr(workspace_module, "git", slow)
    done = {}
    t = threading.Thread(target=lambda: done.update(w.finish(record["id"], "O", "o@example.com")))
    t.start()
    assert committing.wait(10)
    w.rename(record["id"], "Renamed meanwhile")            # while finish is committing
    renamed.set()
    t.join(20)
    assert done["committed"] is True
    after = w.session(record["id"])
    assert after["title"] == "Renamed meanwhile" and after["finished"]["head"] == done["head"]


def test_a_session_record_is_only_written_through_update():
    # every change to an existing session's record goes through _update; only _prepare (which prepare runs, and which
    # discards a moved session's files whatever happens) writes a new one
    with open(os.path.join(HERE, "..", "proofpane_workspace", "workspace.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    writers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "_write" and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "self"):
                    writers.add(fn.name)
    assert writers == {"_update", "_prepare"}, writers


def test_rename_reads_the_record_it_changes(tmp_path):
    w = Workspace(str(tmp_path / "ws"))
    with pytest.raises(NotFound):
        w.rename("aaaaaaaaaaaa", "x")
    with pytest.raises(WorkspaceError):
        w.rename("aaaaaaaaaaaa", "x" * 121)
