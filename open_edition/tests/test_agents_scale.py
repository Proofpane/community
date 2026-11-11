"""A workspace with many runs (agents.py's index): a poll reads only the runs changed since its change number, an
idle check and a start read none they do not need, and a run's record goes once the app no longer keeps it."""

import json
import os
import time
import types

from proofpane_workspace import agents as agentry

AGENT = "a1a2a3a4a5a6"
NOW = int(time.time())


def make(root, n, state="done", ended_ago=60, start=0):
    for i in range(start, start + n):
        run_id = f"{i:012x}"
        folder = os.path.join(root, "agents", AGENT, "runs", run_id)
        os.makedirs(folder)
        run = {"state": state, "queued_at": NOW - 10_000 + i, "seq": i + 1, "trigger": {"kind": "manual"},
               "ended_at": NOW - ended_ago if state in agentry.ENDED else None}
        record = {"id": run_id, "agent_id": AGENT, "program": "chat", "run": run}
        with open(os.path.join(folder, "session.json"), "w") as fh:
            # signed as a workspace made without its secret signs it, in this process
            json.dump(dict(record, mac=agentry._mac(record, agentry._PROCESS_KEY)), fh)


def counted(agents, monkeypatch):
    reads, real = [], agents.read
    monkeypatch.setattr(agents, "read", lambda run_id: reads.append(run_id) or real(run_id))
    return reads


def test_a_poll_reads_only_the_runs_changed_since_and_an_idle_check_reads_none(tmp_path, monkeypatch):
    make(str(tmp_path), 3000)
    make(str(tmp_path), 2, state="queued", start=3000)
    agents = agentry.Agents(types.SimpleNamespace(root=str(tmp_path)))
    reads = counted(agents, monkeypatch)
    got = agents.since(2990)
    assert [r["seq"] for r in got["runs"]] == list(range(2991, 3003)) and got["more"] is False
    assert len(reads) == 12                                            # the changed ones alone
    reads.clear()
    assert agents.queued() is True and agents.live_runs() == 0 and reads == []
    first = agents.since(0)
    assert (len(first["runs"]), first["more"], first["seq"]) == (agentry.PAGE, True, agentry.PAGE)
    assert len(reads) == agentry.PAGE                                  # a page, not all of them
    reads.clear()
    assert [r["id"] for r in agents.runs(states=("queued",))] == ["000000000bb8", "000000000bb9"] and len(reads) == 2


def test_a_run_s_record_goes_once_the_app_no_longer_keeps_it(tmp_path):
    make(str(tmp_path), 3, ended_ago=100 * 86400)
    make(str(tmp_path), 2, ended_ago=10 * 86400, start=3)
    agents = agentry.Agents(types.SimpleNamespace(root=str(tmp_path)))
    assert agents._forget_old(NOW) == ["000000000000", "000000000001", "000000000002"]
    runs = os.path.join(str(tmp_path), "agents", AGENT, "runs")
    assert sorted(os.listdir(runs)) == ["000000000003", "000000000004"]
    assert [r["run_id"] for r in agents.since(0)["runs"]] == ["000000000003", "000000000004"]
    assert agentry.Agents(types.SimpleNamespace(root=str(tmp_path))).since(0)["runs"][0]["run_id"] == "000000000003"
