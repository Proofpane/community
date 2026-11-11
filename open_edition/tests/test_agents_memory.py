"""An agent's memory in the workspace (memory.py, agents.py): a snapshot when a run changed it, the files a run used
noted, the sweep archiving what went unused for the agent's decay_runs runs (never MEMORY.md) and bringing a file back
on the owner's word, and a version written back; through a real workspace agent with the stand-in claude."""

import json
import os
import time

import pytest

from test_agents_runs import AGENT, Workspace, put, run, until


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    yield w
    w.stop()


def folder(ws):
    return os.path.join(ws.root, "agents", AGENT, "memory")


def ran(ws, brief, **over):
    put(ws, brief, **over)
    return until(ws, run(ws)["run_id"], ("done",))


def test_a_run_that_changed_the_memory_leaves_a_snapshot_and_one_that_did_not_none(ws):
    wrote = ran(ws, "memory write MEMORY.md=- [labels](labels.md) & write labels.md=bug, docs")
    assert wrote["memory_digest"] and wrote["memory_bytes"] > 0
    got = ws.http.get(f"/agents/runs/{wrote['run_id']}/memory").json()
    assert got["files"] == {"MEMORY.md": "- [labels](labels.md)", "labels.md": "bug, docs"}
    assert got["digest"] == wrote["memory_digest"]
    same = ran(ws, "memory read labels.md")
    assert same["memory_digest"] is None
    assert ws.http.get(f"/agents/runs/{same['run_id']}/memory").status_code == 400


def archived(ws):
    """A sweep now: what it archived, per agent (its answer also holds each agent's knowledge scan)."""
    return {agent: swept["archived"] for agent, swept in ws.http.post("/agents/sweep").json().items()}


def test_a_file_unused_for_its_runs_goes_to_the_archive_and_comes_back(ws):
    ran(ws, "memory write MEMORY.md=index & write a.md=kept & write b.md=old", decay_runs=2)
    ran(ws, "memory read a.md", decay_runs=2)
    assert archived(ws) == {AGENT: []}                                            # one run since b was used
    ran(ws, "memory read a.md", decay_runs=2)
    assert archived(ws) == {AGENT: ["b.md"]}
    listing = ws.http.get(f"/agents/{AGENT}/memory").json()
    assert sorted(f["path"] for f in listing["files"]) == ["MEMORY.md", "a.md"]       # the index never moves
    assert listing["archived"] == [{"path": "b.md", "bytes": 3}] and listing["runs"] == 3
    assert open(os.path.join(folder(ws), "archive", "b.md")).read() == "old"           # moved, not deleted
    back = ws.http.post(f"/agents/{AGENT}/memory/restore", json={"path": "b.md"})
    assert back.status_code == 200 and back.json()["restored"] == "b.md"
    assert open(os.path.join(folder(ws), "b.md")).read() == "old"
    assert archived(ws) == {AGENT: []}                                                 # used now, as it came back
    for bad in ("b.md", "../agent.json", "/etc/passwd", ""):
        assert ws.http.post(f"/agents/{AGENT}/memory/restore", json={"path": bad}).status_code == 400, bad
    put(ws, "hello", decay_runs=0)
    for _ in range(3):
        until(ws, run(ws)["run_id"], ("done",))
    assert archived(ws) == {AGENT: []}                                                 # 0: never


def test_a_version_written_back_makes_the_folder_its_files_and_nothing_else(ws):
    ran(ws, "memory write a.md=one & write notes/b.md=two")
    os.makedirs(os.path.join(folder(ws), "archive"), exist_ok=True)
    with open(os.path.join(folder(ws), "archive", "old.md"), "w") as fh:
        fh.write("kept apart")
    r = ws.http.put(f"/agents/{AGENT}/memory", json={"files": {"a.md": "uno", "c.md": "three"}})
    assert r.status_code == 200, r.text
    assert sorted(f["path"] for f in r.json()["listing"]["files"]) == ["a.md", "c.md"]
    assert open(os.path.join(folder(ws), "a.md")).read() == "uno"
    assert open(os.path.join(folder(ws), "archive", "old.md")).read() == "kept apart"     # the archive as it was
    assert open(os.path.join(folder(ws), "archive", "notes", "b.md")).read() == "two"     # left out: archived, kept
    for bad in ({"../x.md": "x"}, {"archive/x.md": "x"}, {"/tmp/x.md": "x"}, {"a.md": 3}, {"a.md": "x" * 70_000}):
        assert ws.http.put(f"/agents/{AGENT}/memory", json={"files": bad}).status_code == 400, bad
    put(ws, "wait 20")
    working = run(ws)["run_id"]
    until(ws, working, ("working",))
    assert ws.http.put(f"/agents/{AGENT}/memory", json={"files": {"a.md": "x"}}).status_code == 400
    ws.http.post(f"/agents/runs/{working}/stop")


def test_the_sweep_runs_at_the_day_s_first_start(ws):
    ran(ws, "memory write a.md=x", decay_runs=1)
    ran(ws, "hello", decay_runs=1)
    with open(os.path.join(ws.root, "agents", "swept.json"), "w") as fh:
        json.dump({"day": "2026-01-01"}, fh)
    ws.stop()
    ws.start()
    deadline = time.time() + 20
    while not os.path.exists(os.path.join(folder(ws), "archive", "a.md")):
        assert time.time() < deadline
        time.sleep(0.1)
    assert json.load(open(os.path.join(ws.root, "agents", "swept.json")))["day"] == time.strftime("%Y-%m-%d",
                                                                                               time.gmtime())


def test_the_sweep_reads_the_knowledge_its_runs_left_and_tells_its_last(ws):
    ran(ws, "hello")
    work = os.path.join(ws.root, "agents", AGENT, "work")
    os.makedirs(os.path.join(work, "knowledge"), exist_ok=True)
    with open(os.path.join(work, "knowledge", "deploy.md"), "w") as fh:
        fh.write("---\nstatus: current\nas_of: 2025-01-01\nsource: the runbook\n---\nhow to deploy")
    swept = ws.http.post("/agents/sweep").json()[AGENT]["knowledge"]
    assert swept["current"] == 1 and swept["stale"] == [{"path": "knowledge/deploy.md", "verify_by": "2025-04-01",
                                                         "source": "the runbook"}]
    last = ws.http.get("/agents/sweep").json()
    assert last["day"] == time.strftime("%Y-%m-%d", time.gmtime()) and last["result"][AGENT]["knowledge"] == swept


def test_a_rollback_archives_what_its_version_could_not_keep_and_a_name_taken_gets_a_number(ws):
    ran(ws, "memory write a.md=one")
    big = "x" * 70_000                                  # past what a version keeps of a file
    with open(os.path.join(folder(ws), "big.md"), "w") as fh:
        fh.write(big)
    os.makedirs(os.path.join(folder(ws), "archive"), exist_ok=True)
    with open(os.path.join(folder(ws), "archive", "big.md"), "w") as fh:
        fh.write("an older big.md, archived before")
    assert ws.http.put(f"/agents/{AGENT}/memory", json={"files": {"a.md": "one"}}).status_code == 200
    archive = os.path.join(folder(ws), "archive")
    assert open(os.path.join(archive, "big.md")).read() == "an older big.md, archived before"
    assert open(os.path.join(archive, "big.1.md")).read() == big                       # nothing lost
    assert not os.path.exists(os.path.join(folder(ws), "big.md"))


def test_a_memory_folder_or_archive_that_is_a_link_is_left_as_it_is(ws, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "settings.json").write_text("{}")
    ran(ws, "memory write a.md=one")
    archive = os.path.join(folder(ws), "archive")
    os.symlink(elsewhere, archive)
    r = ws.http.put(f"/agents/{AGENT}/memory", json={"files": {"b.md": "two"}})
    assert r.status_code == 400 and "link" in r.json()["detail"]
    assert sorted(os.listdir(elsewhere)) == ["settings.json"]                         # nothing moved through it
    assert ws.http.post(f"/agents/{AGENT}/memory/restore", json={"path": "settings.json"}).status_code == 400
    os.unlink(archive)
    os.rename(folder(ws), folder(ws) + ".real")
    os.symlink(elsewhere, folder(ws))                   # the memory folder itself a link: read as empty
    assert ws.http.get(f"/agents/{AGENT}/memory").json()["files"] == []
    assert ws.http.put(f"/agents/{AGENT}/memory", json={"files": {"b.md": "two"}}).status_code == 400
    assert sorted(os.listdir(elsewhere)) == ["settings.json"]


def test_an_eval_question_does_not_age_the_memory(ws):
    ran(ws, "memory write a.md=one")
    counted = ws.http.get(f"/agents/{AGENT}/memory").json()["runs"]
    r = ws.http.post(f"/agents/{AGENT}/runs", json={"trigger": {"kind": "eval", "prompt": "What is the tag?"}})
    until(ws, r.json()["run_id"], ("done",))
    assert ws.http.get(f"/agents/{AGENT}/memory").json()["runs"] == counted        # a question, not one of its runs
    ran(ws, "hello")
    assert ws.http.get(f"/agents/{AGENT}/memory").json()["runs"] == counted + 1
