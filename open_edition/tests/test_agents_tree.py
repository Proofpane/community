"""A run's tree and context at the workspace (agents.py, runtree.py): kept at its end beside the run, served from
there once the transcript is gone, and its context on the run's record. Through a real workspace agent with the
stand-in claude."""

import os
import shutil

import pytest

from test_agents_runs import AGENT, Workspace, log, put, run, until


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    yield w
    w.stop()


def test_a_run_s_tree_is_kept_at_its_end_and_served_after_its_transcript_goes(ws):
    put(ws, "subagent check the labels")
    r = run(ws)["run_id"]
    done = until(ws, r, ("done",))
    assert os.path.isfile(os.path.join(ws.root, "agents", AGENT, "runs", r, "tree.json"))
    got = ws.http.get(f"/agents/runs/{r}/tree").json()
    assert got["copy"] is True and got["tree"]["requests"] >= 1 and got["tree"]["turns"]
    context = done["context"]
    assert context["peak_tokens"] > 0 and context["compactions"] == 0 and context["fragmented"] is False
    shutil.rmtree(ws.home / ".claude" / "projects")                    # Claude Code's own clean-up, a month on
    assert ws.http.get(f"/agents/runs/{r}/tree").json()["tree"] == got["tree"]
    assert "Your context is bounded" in log(ws, r)["events"][0]["text"]


def test_a_live_run_s_tree_is_read_from_its_transcript(ws):
    put(ws, "ask Which tag?")
    r = run(ws)["run_id"]
    waiting = until(ws, r, ("waiting",))
    assert waiting["context"]["peak_tokens"] > 0                       # measured at its turn's end, before its own
    got = ws.http.get(f"/agents/runs/{r}/tree").json()
    assert got["copy"] is False and got["tree"]["turns"]
    assert ws.http.get("/agents/runs/aaaaaaaaaaaa/tree").status_code == 404


def test_an_agent_s_compaction_point_reaches_its_runs(ws):
    put(ws, "hello", autocompact_window=300000, max_compactions=1)
    r = run(ws)["run_id"]
    until(ws, r, ("done",))
    from test_agents_runs import starts
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" in starts(ws)[-1]["env"]
    assert ws.http.put(f"/agents/{AGENT}", json={"id": AGENT, "name": "T", "brief": "x",
                                                "autocompact_window": 50000}).status_code == 400
