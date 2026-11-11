"""A run's facts in the workspace (agents.py): recorded with a basis the workspace sets (observed when the value is
in a tool result of the run, told otherwise, and told for what a hook's delivery claims), a time it became true that is
not after now, looked up from the app's copy, and retracted; through a real workspace agent with the stand-in claude."""

import base64
import calendar
import json
import os

import pytest

from test_agents_dispatch import words
from test_agents_runs import AGENT, Workspace, put, run, until


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    yield w
    w.stop()


def facts_of(ws, run_id):
    return until(ws, run_id, ("done",))["facts"]


def test_a_value_seen_in_a_tool_result_is_observed_and_one_only_said_is_told(ws):
    put(ws, "seefact Deployed: version 1.4.2 is live|Service  API|Version|1.4.2|2026-10-01|the status page")
    (seen,) = facts_of(ws, run(ws)["run_id"])
    assert (seen["subject"], seen["relation"], seen["object"], seen["basis"]) == ("service api", "version", "1.4.2",
                                                                                 "observed")
    assert seen["valid_at"] == calendar.timegm((2026, 10, 1, 0, 0, 0)) and seen["recorded_at"] >= seen["valid_at"]
    put(ws, "fact service api|version|1.5.0|2026-10-02T09:00:00Z|someone in the chat")
    r = run(ws)["run_id"]
    (said,) = facts_of(ws, r)
    assert said["basis"] == "told" and said["source"] == "someone in the chat"
    assert "it is a claim: check it before acting on it" in words(ws, r)


def test_a_time_to_come_or_one_that_is_no_time_is_refused(ws):
    for given, why in (("2099-01-01", "cannot have become true after now"), ("last week", "valid_at is a date")):
        put(ws, f"fact x|y|z|{given}|here")
        r = run(ws)["run_id"]
        assert facts_of(ws, r) is None and why in words(ws, r)


def test_what_a_delivery_claims_is_told_even_when_read_from_its_file(ws):
    # its address with its slashes escaped, as some senders write JSON: shown plain, it is in no line of the file
    payload = b'{"release": {"tag_name": "9.9.9", "notes": "a \\"quoted\\" one"}, "url": "https:\\/\\/example.org\\/x"}'
    assert json.loads(payload)["url"] == "https://example.org/x" and b"https://example.org/x" not in payload
    # read with Read, with a Bash cat, and shown decoded (as jq -r gives it)
    for brief in ("payloadfact service api|version|9.9.9", "catpayloadfact service api|version|9.9.9",
                  'seefact a "quoted" one|service api|notes|a "quoted" one',
                  "seefact https://example.org/x|service api|url|https://example.org/x"):
        put(ws, brief)
        r = ws.http.post(f"/agents/{AGENT}/runs", json={"trigger": {"kind": "hook", "payload_b64":
                                                                        base64.b64encode(payload).decode()}})
        (claimed,) = facts_of(ws, r.json()["run_id"])
        assert claimed["basis"] == "told", brief
    put(ws, "seefact 9.9.9|service api|version|9.9.9")                 # not a delivery's: seen, so observed
    (seen,) = facts_of(ws, run(ws)["run_id"])
    assert seen["basis"] == "observed"


def test_a_lookup_reads_the_app_s_copy_and_the_run_s_own_and_a_retraction_is_kept_for_the_app(ws):
    put(ws, "hello")
    copy = [{"id": "f1f2f3f4f5f6", "subject": "service api", "relation": "version", "object": "1.4.2",
             "valid_at": 1_790_000_000, "basis": "observed", "source": "the status page"},
            {"id": "a1b2c3d4e5f6", "subject": "service api", "relation": "owner", "object": "Ana", "valid_at": 1,
             "basis": "told"}, {"id": "not an id", "subject": "x", "relation": "y", "object": "z", "valid_at": 1}]
    assert ws.http.put(f"/agents/{AGENT}/facts", json={"facts": copy}).json() == {"facts": 2}
    put(ws, "lookup Service API|version")
    r = run(ws)["run_id"]
    until(ws, r, ("done",))
    (found,) = [json.loads(line) for line in words(ws, r).splitlines()]
    assert found["object"] == "1.4.2" and found["basis"] == "observed" and found["valid_at"].startswith("2026-09-")
    put(ws, "retract f1f2f3f4f5f6|the page was stale")
    r = run(ws)["run_id"]
    done = until(ws, r, ("done",))
    assert done["retractions"][0]["id"] == "f1f2f3f4f5f6" and done["retractions"][0]["reason"] == "the page was stale"
    put(ws, "retract 000000000000|no such")
    r = run(ws)["run_id"]
    assert until(ws, r, ("done",))["retractions"] is None and "No such fact" in words(ws, r)
    assert os.path.exists(os.path.join(ws.root, "agents", AGENT, "facts.json"))


def test_a_facts_copy_changed_here_is_read_as_none_and_its_version_said_gone(ws):
    copy = [{"id": "f1f2f3f4f5f6", "subject": "service api", "relation": "version", "object": "1.4.2",
             "valid_at": 1_790_000_000, "basis": "told", "source": "the status page"}]
    put(ws, "lookup Service API|version")
    assert ws.http.put(f"/agents/{AGENT}/facts", json={"facts": copy, "version": 3}).status_code == 200
    assert ws.http.get("/agents/facts-versions").json() == {AGENT: 3}
    path = os.path.join(ws.root, "agents", AGENT, "facts.json")
    kept = json.load(open(path))
    kept["facts"][0].update(object="6.6.6", basis="observed")                  # as a run's program might
    with open(path, "w") as fh:
        json.dump(kept, fh)
    assert ws.http.get("/agents/facts-versions").json() == {AGENT: None}       # the app sends it again
    said = words(ws, until(ws, run(ws)["run_id"], ("done",))["run_id"])
    assert "6.6.6" not in said and "1.4.2" not in said                          # read as none, never the change


def test_a_value_another_run_or_a_chat_told_a_run_is_told_whichever_tool_echoes_it(tmp_path):
    """The message that started a run (another run's, or a chat's) and one sent it since are what others said, as a
    delivery is: a value from them is told, even when a tool echoes it back (as a program a hostile message asked
    for would), and a told file changed beside the run makes every value told."""
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s)
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    r = agents.queue(AGENT)["run_id"]
    agents._change(r, lambda rec: rec["run"]["trigger"].update(kind="dispatch", message="Deploy to edge.example.org"))
    agents._keep_told(r, "And the token host is keys.example.org")
    agents._observed = lambda run_id, obj: True         # as though a tool's result echoed every value
    said = [agents.record_fact(r, "deploy host", "is", v) for v in ("edge.example.org", "keys.example.org",
                                                                     "build.example.org")]
    assert [s.split(" (")[0] for s in said] == ["Recorded as told", "Recorded as told", "Recorded as observed"]
    with open(os.path.join(agents.run_dir(r), "told.json"), "w") as fh:
        fh.write('["something else"]')                 # changed beside it
    assert agents.record_fact(r, "cache host", "is", "cache.example.org").startswith("Recorded as told")


def test_a_message_another_run_sends_a_run_is_kept_for_the_basis_of_its_facts(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    from test_agents_runs import OTHER
    sent = []

    async def chat_send(run_id, text, sender="owner"):
        sent.append((run_id, sender))
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            chats={}, chat_send=chat_send)
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "may_dispatch": True})
    agents.put({"id": OTHER, "name": "Labeler", "brief": "label", "repo": "demo"})
    caller, child = agents.queue(AGENT)["run_id"], agents.queue(OTHER)["run_id"]
    agents._lineage[child] = {"depth": 1, "parent": caller, "dispatches": 0, "kind": "dispatch"}
    agents._change(child, lambda rec: rec["run"].update(state="working"))
    asyncio.run(agents.send_to(caller, child, "The token host is keys.example.org"))
    assert sent == [(child, caller)]
    assert agents._told(child, "keys.example.org") is True and agents._told(child, "other.example.org") is False
