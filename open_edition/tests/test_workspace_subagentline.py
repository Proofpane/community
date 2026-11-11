"""The sub-agent status line a claude terminal runs (subagentline.py): it keeps each agent Claude Code shows, by
its id, with its name or else its description cut to 80 characters, and nothing else of the input; it prints nothing,
so Claude Code keeps its own rows; outside a session, or in one of its shells, it writes nothing."""

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT
from proofpane_workspace import subagentline

SCRIPT = os.path.join(ROOT, "proofpane_workspace", "subagentline.py")
SECRET_WORDS = "the reply holds sk-ant-api03-must-never-be-kept"

# a row as https://code.claude.com/docs/en/statusline gives one ("Task fields", read 2026-10-17), with what must not
# be kept in it: a label and a cwd, the session's id and its transcript's path in the base fields
TASK = {"id": "a1b2c3", "name": "reviewer", "type": "local_agent", "agentType": "Explore", "status": "running",
        "description": "Look through the repository for the parser", "label": SECRET_WORDS, "startTime": 1760700000123,
        "model": "claude-opus-5-5", "effort": "high", "contextWindowSize": 1000000, "tokenCount": 200600,
        "tokenSamples": [100, 200600], "cwd": "/home/ws/secret-folder"}
INPUT = {"session_id": "sess-123", "transcript_path": "/home/ws/.claude/projects/x/sess-123.jsonl", "cwd": "/home/ws/w",
         "hook_event_name": "SubagentStatusLine", "columns": 80, "tasks": [TASK]}


def _run(stdin, folder, shell=False):
    env = {"PATH": os.environ["PATH"], "HOME": str(folder)}
    if folder is not None:
        env["PP_SESSION_DIR"] = str(folder)
    if shell:
        env["PP_SHELL"] = "1"
    return subprocess.run([sys.executable, SCRIPT], input=stdin, capture_output=True, text=True, env=env, timeout=30)


def test_it_keeps_each_agent_and_prints_nothing(tmp_path):
    done = _run(json.dumps(INPUT), tmp_path)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""
    kept = json.loads((tmp_path / "subagents.json").read_text())
    (row,) = kept["agents"]
    assert {k: row[k] for k in ("id", "name", "type", "agent_type", "status", "started_at", "model", "effort",
                                "tokens", "ended_at")} == {
        "id": "a1b2c3", "name": "reviewer", "type": "local_agent", "agent_type": "Explore", "status": "running",
        "started_at": 1760700000, "model": "claude-opus-5-5", "effort": "high", "tokens": 200600, "ended_at": None}
    text = (tmp_path / "subagents.json").read_text()
    for word in ("secret-folder", "sess-123", "must-never-be-kept", "tokenSamples", "contextWindowSize", "label"):
        assert word not in text
    assert subagentline.read(str(tmp_path)) == kept["agents"]


def test_a_name_else_its_description_cut_to_80(tmp_path):
    long = "x" * 200
    tasks = [dict(TASK, id="n1", name=None, description="  Find\nthe\tparser \x1b[31mnow  "),
             dict(TASK, id="n2", name=long), dict(TASK, id="n3", name="", description=long)]
    tasks[0].pop("name")
    _run(json.dumps(dict(INPUT, tasks=tasks)), tmp_path)
    names = {r["id"]: r["name"] for r in subagentline.read(str(tmp_path))}
    assert names["n1"] == "Find the parser [31mnow"
    assert names["n2"] == "x" * 80 and names["n3"] == "x" * 80
    # cut where it is written, not only where it is read: no more of a model's words is kept on disk than is shown
    written = {r["id"]: r["name"] for r in json.loads((tmp_path / "subagents.json").read_text())["agents"]}
    assert written == names and "x" * 81 not in (tmp_path / "subagents.json").read_text()


def test_an_agent_no_longer_shown_keeps_its_last_row_and_an_end_is_dated_once(tmp_path):
    folder = str(tmp_path)
    first = subagentline.merge(None, [TASK, dict(TASK, id="b2")], now=100)
    second = subagentline.merge(first, [dict(TASK, status="completed")], now=105)
    third = subagentline.merge(second, [dict(TASK, status="completed", tokenCount=5)], now=110)
    rows = {r["id"]: r for r in third}
    assert rows["a1b2c3"]["ended_at"] == 105 and rows["a1b2c3"]["seen_at"] == 110 and rows["a1b2c3"]["tokens"] == 5
    assert rows["b2"]["status"] == "running" and rows["b2"]["seen_at"] == 100       # not shown since: as it was
    resumed = {r["id"]: r for r in subagentline.merge(third, [TASK], now=120)}
    assert resumed["a1b2c3"]["status"] == "running" and resumed["a1b2c3"]["ended_at"] is None
    assert os.listdir(folder) == []


def test_unknown_statuses_and_shapes_are_not_kept_as_they_came():
    row = subagentline.merge(None, [dict(TASK, status="paused", agentType=5, effort="HIGH", model={"a": 1},
                                         tokenCount=True, startTime="x")], now=1)[0]
    assert row["status"] is None and row["agent_type"] is None and row["effort"] is None and row["model"] is None
    assert row["tokens"] is None and row["started_at"] is None
    assert subagentline.merge(None, [dict(TASK, effort=4096)], now=1)[0]["effort"] == 4096
    assert subagentline.merge(None, [dict(TASK, id="../x"), dict(TASK, id=""), 5, None], now=1) == []


def test_at_most_max_agents_the_newest_kept():
    tasks = [dict(TASK, id=f"a{n}", startTime=(1000 + n) * 1000) for n in range(subagentline.MAX_AGENTS + 20)]
    rows = subagentline.merge(None, tasks, now=1)
    assert len(rows) == subagentline.MAX_AGENTS
    assert min(r["started_at"] for r in rows) == 1020


@pytest.mark.parametrize("given", ["not json", "[1]", "", json.dumps({"tasks": "x"})])
def test_input_it_cannot_use_writes_no_agents_and_no_error(tmp_path, given):
    done = _run(given, tmp_path)
    assert done.returncode == 0 and done.stdout == ""
    assert json.loads((tmp_path / "subagents.json").read_text())["agents"] == []


def test_outside_a_session_or_in_one_of_its_shells_it_writes_nothing(tmp_path):
    (tmp_path / "home").mkdir()
    done = subprocess.run([sys.executable, SCRIPT], input=json.dumps(INPUT), capture_output=True, text=True,
                          cwd=str(tmp_path), env={"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home")},
                          timeout=30)
    assert done.returncode == 0 and done.stdout == "" and sorted(os.listdir(tmp_path)) == ["home"]
    shell = _run(json.dumps(INPUT), tmp_path / "home", shell=True)
    assert shell.returncode == 0 and os.listdir(tmp_path / "home") == []


def test_the_file_is_read_back_only_as_a_regular_file_of_its_size(tmp_path):
    from test_workspace_sessionmap import within
    path = tmp_path / "subagents.json"
    path.write_text("x" * (subagentline.MAX_FILE + 1))
    assert subagentline.read(str(tmp_path)) is None
    path.unlink()
    os.mkfifo(path)                                     # a FIFO is not waited on: a read that waited fails, not hangs
    assert within(5, subagentline.read, str(tmp_path)) is None
    path.unlink()
    (tmp_path / "elsewhere.json").write_text(json.dumps({"agents": [dict(id="z")]}))
    os.symlink(tmp_path / "elsewhere.json", path)       # nor a link followed
    assert subagentline.read(str(tmp_path)) is None


def test_the_largest_file_it_can_write_is_read_back():
    wide = "\U0001F600" * 200                           # four bytes a character, cut to 80
    tasks = [dict(TASK, id=f"{n:0128d}"[:128], name=wide, agentType=wide, model=wide, startTime=(1000 + n) * 1000)
             for n in range(subagentline.MAX_AGENTS)]
    rows = subagentline.merge(None, tasks, now=1)
    text = json.dumps({"at": 1, "agents": rows}, ensure_ascii=False)
    assert len(text.encode("utf-8")) <= subagentline.MAX_FILE


@pytest.mark.parametrize("script", ["subagentline.py", "activity.py"])
def test_each_hook_script_imports_the_standard_library_alone(script):
    # each runs alone, with the system Python and -E -s, so a package import (runtree, say) would end it at its start
    import ast
    tree = ast.parse(open(os.path.join(ROOT, "proofpane_workspace", script), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module.split(".")[0] in sys.stdlib_module_names, (script, node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in sys.stdlib_module_names, (script, alias.name)


def test_a_longer_file_left_from_a_run_ended_mid_write_does_not_spoil_the_next(tmp_path):
    (tmp_path / "subagents.json.tmp").write_text("x" * 50000)          # what a run ended mid-write could leave
    done = _run(json.dumps(INPUT), tmp_path)
    assert done.returncode == 0
    assert [r["id"] for r in json.loads((tmp_path / "subagents.json").read_text())["agents"]] == ["a1b2c3"]


def test_a_row_of_another_kind_is_not_taken_for_an_agent_s():
    rows = subagentline.merge(None, [dict(TASK, id="w1", type="local_workflow"), dict(TASK, id="b1", type="bash"),
                                     dict(TASK, id="a1"), {k: v for k, v in TASK.items() if k != "type"} | {"id": "n1"}],
                              now=1)
    assert sorted(r["id"] for r in rows) == ["a1", "n1"]
