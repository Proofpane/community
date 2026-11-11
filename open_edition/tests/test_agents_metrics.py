"""A run's numbers at its end, through a real workspace agent with the stand-in claude (metrics.py, agents.py): its
refusals as its turns' results name them, the owner's asks and turns, and what it sent and was sent with other runs."""

import time

import pytest

from test_agents_dispatch import children, words
from test_agents_runs import AGENT, OTHER, Workspace, log, put, run, until


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    yield w
    w.stop()


def pending(ws, run_id, timeout=30):
    deadline = time.time() + timeout
    while True:
        got = log(ws, run_id)
        if got["pending"]:
            return got["pending"][0]
        assert time.time() < deadline, got
        time.sleep(0.05)


def test_a_refused_call_and_the_owner_s_ask_are_in_the_run_s_numbers(ws):
    put(ws, "run touch made")
    r = run(ws)["run_id"]
    until(ws, r, ("waiting",))
    time.sleep(1.1)                                             # a second waiting, as the record counts seconds
    assert ws.http.post(f"/sessions/{r}/chat/answer", json={"id": pending(ws, r), "decision": "deny"}).status_code == 200
    done = until(ws, r, ("done",))
    m = done["metrics"]
    assert (m["calls"], m["refused"], m["errors"]) == (1, 1, 0)
    assert m["tools"]["Bash"]["refused"] == 1
    assert m["together"]["permission_asks"] == 1 and m["together"]["denied"] == 1
    assert m["time"]["waiting_owner"] >= 1 and m["time"]["wall"] >= 1
    (last,) = [e for e in log(ws, r)["events"] if e["kind"] == "result"]
    assert last["api_ms"] == 4 and [d["name"] for d in last["denials"]] == ["Bash"]
    assert m["time"]["model"] == 0.004 and m["time"]["active"] == 0.005


def test_a_question_the_owner_answered_and_the_owner_s_turns_are_counted(ws):
    put(ws, "ask Which tag?")
    r = run(ws)["run_id"]
    until(ws, r, ("waiting",))
    time.sleep(1.1)
    assert ws.http.post(f"/sessions/{r}/chat", json={"text": "hello"}).status_code == 200
    done = until(ws, r, ("done",))
    together = done["metrics"]["together"]
    assert (together["questions"], together["answered"], together["owner_turns"]) == (1, 1, 1)
    assert done["metrics"]["time"]["waiting_owner"] >= 1


def test_the_owner_s_word_to_a_run_that_did_not_ask_is_a_turn_and_not_an_answer(ws):
    put(ws, "sleep 3")
    r = run(ws)["run_id"]
    until(ws, r, ("working",))
    sent = ws.http.post(f"/agents/runs/{r}/message", json={"text": "hello"})                 # kept till the turn ends
    assert sent.status_code == 200, sent.text
    together = until(ws, r, ("done",))["metrics"]["together"]
    assert (together["owner_turns"], together["answered"], together["questions"]) == (1, 0, 0)


def test_runs_together_count_what_each_sent_and_received(ws):
    put(ws, "dispatchsend Labeler|start|and also issue 9|30", may_dispatch=True)
    put(ws, "sleep 3", agent_id=OTHER, name="Labeler")
    top = run(ws)["run_id"]
    parent = until(ws, top, ("done",), timeout=60)["metrics"]["together"]
    (child,) = children(ws, top)
    child = until(ws, child["run_id"], ("done",))["metrics"]["together"]
    assert (parent["dispatches_sent"], parent["messages_sent"], parent["dispatches_received"]) == (1, 1, 0)
    assert (child["dispatches_received"], child["messages_received"], child["owner_turns"]) == (1, 1, 0)
    assert words(ws, top)
