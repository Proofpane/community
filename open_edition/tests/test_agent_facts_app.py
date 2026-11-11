"""An agent's fact ledger in the app (facts.py): facts taken from runs' records once each, held to their shapes, and
ruled on with no model call: a later functional value retires the earlier one (nothing deleted), a multi relation is
never retired by its relation alone, a time after the recording is refused, a told value never retires an observed one,
the same value twice keeps one; the owner retracts; the copy the workspace keeps is sent when the ledger changed, and
never read back."""

import json
import os
import time

from test_agents_app import A, agent, owner, local, until  # noqa: F401  (fixtures)

DAY = 86400


def me(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def ran(client, h, agent_id, monkeypatch):
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    return client.post(f"{A}/{agent_id}/runs", headers=h, json={}).json()["run_id"]


def fact(fid, obj, valid_at, basis="observed", subject="Service API", relation="version", recorded_at=None):
    return {"id": fid, "subject": subject, "relation": relation, "object": obj, "valid_at": valid_at,
            "recorded_at": recorded_at or valid_at + 60, "source": "a tool", "basis": basis}


def take(user_id, run_id, agent_id, *facts, retractions=()):
    from proofpane_open.ade import agents
    agents._apply(user_id, [{"run_id": run_id, "agent_id": agent_id, "state": "working", "facts": list(facts),
                             "retractions": list(retractions)}])


def ledger(client, h, agent_id):
    r = client.get(f"{A}/{agent_id}/facts", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_later_value_of_a_functional_relation_retires_the_earlier_and_nothing_is_deleted(client, owner, monkeypatch):
    made = agent(client, owner, relations={" Version ": "functional"})
    assert made["relations"] == {"version": "functional"}
    user_id, r = me(client, owner), ran(client, owner, made["id"], monkeypatch)
    take(user_id, r, made["id"], fact("a" * 12, "1.4.2", 1_000_000))
    take(user_id, r, made["id"], fact("b" * 12, "1.5.0", 2_000_000))
    got = ledger(client, owner, made["id"])
    (now,) = got["current"]
    (old,) = got["retired"]
    assert (now["object"], now["subject"]) == ("1.5.0", "service api")                 # its keys normalised
    assert (old["object"], old["invalid_at"], old["superseded_by"]) == ("1.4.2", 2_000_000, "b" * 12)
    assert old["expired_at"] == 2_000_060              # when the one that retired it was recorded: the workspace's clock
    # one recorded late but true earlier is retired at once by the one now current
    take(user_id, r, made["id"], fact("c" * 12, "1.4.9", 1_500_000))
    got = ledger(client, owner, made["id"])
    assert [f["object"] for f in got["current"]] == ["1.5.0"]
    late = [f for f in got["retired"] if f["id"] == "c" * 12][0]
    assert (late["invalid_at"], late["superseded_by"]) == (2_000_000, "b" * 12)
    assert late["expired_at"] == 2_000_060 >= late["recorded_at"]                    # never before it was recorded


def test_a_multi_relation_keeps_every_value_until_one_is_retracted(client, owner, monkeypatch):
    made = agent(client, owner, relations={"version": "functional"})               # maintainer: undeclared, multi
    user_id, r = me(client, owner), ran(client, owner, made["id"], monkeypatch)
    take(user_id, r, made["id"], fact("a" * 12, "Ana", 1_000_000, relation="maintainer"),
         fact("b" * 12, "Ben", 2_000_000, relation="maintainer"))
    assert sorted(f["object"] for f in ledger(client, owner, made["id"])["current"]) == ["Ana", "Ben"]
    take(user_id, r, made["id"], retractions=[{"id": "a" * 12, "reason": "moved on"}])
    got = ledger(client, owner, made["id"])
    assert [f["object"] for f in got["current"]] == ["Ben"] and got["retired"][0]["retracted"] == "moved on"


def test_a_value_true_after_it_was_recorded_is_refused(client, owner, monkeypatch):
    made = agent(client, owner)
    user_id, r = me(client, owner), ran(client, owner, made["id"], monkeypatch)
    take(user_id, r, made["id"], fact("a" * 12, "2.0", 5_000_000, recorded_at=4_000_000))
    got = ledger(client, owner, made["id"])
    assert got["current"] == [] and got["retired"] == []


def test_a_fact_recorded_past_the_app_s_time_is_refused_and_one_true_before_1970_is_kept(client, owner, monkeypatch):
    made = agent(client, owner, relations={"version": "functional"})
    user_id, r = me(client, owner), ran(client, owner, made["id"], monkeypatch)
    now = int(time.time())
    take(user_id, r, made["id"], fact("a" * 12, "9.9.9", now, recorded_at=now + 2 * 3600),      # from the future
         fact("b" * 12, "0.1", -14182940, recorded_at=now),                                     # 1969-07-20
         fact("c" * 12, "0.0", 0, subject="Other", recorded_at=now))                            # 1970-01-01 itself
    got = {f["id"]: f for f in ledger(client, owner, made["id"])["current"]}
    assert "a" * 12 not in got
    assert (got["b" * 12]["valid_at"], got["c" * 12]["valid_at"]) == (-14182940, 0)


def test_a_told_value_never_retires_an_observed_one_and_both_show_as_contradicting(client, owner, monkeypatch):
    made = agent(client, owner, relations={"version": "functional"})
    user_id, r = me(client, owner), ran(client, owner, made["id"], monkeypatch)
    take(user_id, r, made["id"], fact("a" * 12, "1.4.2", 1_000_000, basis="observed"))
    take(user_id, r, made["id"], fact("b" * 12, "9.9.9", 2_000_000, basis="told"))
    got = ledger(client, owner, made["id"])
    assert sorted(f["object"] for f in got["current"]) == ["1.4.2", "9.9.9"]
    assert {f["id"]: f["contradicts"] for f in got["contradictions"]} == {"a" * 12: "b" * 12, "b" * 12: "a" * 12}
    # an observation later than a telling retires it, as time says
    take(user_id, r, made["id"], fact("c" * 12, "1.5.0", 3_000_000, basis="observed"))
    current = {f["object"] for f in ledger(client, owner, made["id"])["current"]}
    assert current == {"1.5.0"}


def test_the_same_value_twice_keeps_one_and_an_observation_before_a_telling(client, owner, monkeypatch):
    made = agent(client, owner, relations={"owner": "functional"})
    user_id, r = me(client, owner), ran(client, owner, made["id"], monkeypatch)
    take(user_id, r, made["id"], fact("a" * 12, "Ana Smith", 1_000_000, basis="told", relation="owner"))
    take(user_id, r, made["id"], fact("b" * 12, "ana  smith", 1_100_000, basis="observed", relation="owner"))
    take(user_id, r, made["id"], fact("c" * 12, "ANA SMITH", 1_200_000, basis="told", relation="owner"))
    got = ledger(client, owner, made["id"])
    assert [f["id"] for f in got["current"]] == ["b" * 12]
    assert {f["id"]: f["superseded_by"] for f in got["retired"]} == {"a" * 12: "b" * 12, "c" * 12: "b" * 12}


def test_facts_are_taken_once_held_to_their_shapes_and_the_owner_retracts(client, owner, monkeypatch):
    from proofpane_open import store
    made = agent(client, owner)
    user_id, r = me(client, owner), ran(client, owner, made["id"], monkeypatch)
    forged = [fact("a" * 12, "x" * 900, 1_000_000), dict(fact("b" * 12, "ok", 1_000_000), basis="seen it"),
              dict(fact("not an id", "z", 1)), dict(fact("c" * 12, "z", 1), subject="bad\x07")]
    for _ in range(2):
        take(user_id, r, made["id"], *forged)
    with store.connect() as db:
        rows = {row["id"]: row for row in db.execute("SELECT * FROM ade_agent_facts")}
    assert set(rows) == {"a" * 12, "b" * 12}
    assert len(rows["a" * 12]["object"]) == 500 and rows["b" * 12]["basis"] == "told"
    got = client.post(f"{A}/{made['id']}/facts/{'b' * 12}/retract", headers=owner, json={"reason": "not so"})
    assert got.status_code == 200
    assert client.post(f"{A}/{made['id']}/facts/{'b' * 12}/retract", headers=owner, json={}).status_code == 404
    assert [f["retracted"] for f in ledger(client, owner, made["id"])["retired"]] == ["not so"]
    for bad in ({"version": "single"}, {"": "multi"}, "functional"):
        assert client.put(f"{A}/{made['id']}", headers=owner, json={"relations": bad}).status_code == 400, bad


def test_the_workspace_s_copy_is_sent_when_the_ledger_changed_and_never_read_back(client, owner, env):
    from proofpane_open import auth, store
    from proofpane_open.ade import agents
    made = agent(client, owner, brief="hello", relations={"version": "functional"})
    r = client.post(f"{A}/{made['id']}/runs", headers=owner, json={}).json()["run_id"]
    until(client, owner, r, ("done",))
    user = auth.user(me(client, owner))
    with store.connect() as db:                         # its fact from a run the app has not seen end
        row = db.execute("SELECT * FROM ade_agents WHERE id = ?", (made["id"],)).fetchone()
        working = agents.new_run(db, row, {"kind": "manual", "at": 1_000_000}, 1_000_000)
    take(user["id"], working, made["id"], fact("a" * 12, "1.4.2", 1_000_000))
    agents.sync(user)                                                          # the ledger changed: its copy is sent
    path = os.path.join(str(env), "workspaces", user["id"], "agents", made["id"], "facts.json")
    sent = json.load(open(path))
    assert [f["object"] for f in sent["facts"]] == ["1.4.2"] and isinstance(sent["mac"], str)
    with open(path, "w") as fh:                                                # a run changes the workspace's copy
        json.dump(dict(sent, facts=[{"id": "d" * 12, "subject": "service api", "relation": "version",
                                     "object": "6.6.6", "valid_at": 1, "basis": "observed"}]), fh)
    agents.sync(user)
    with store.connect() as db:
        assert [row["object"] for row in db.execute("SELECT object FROM ade_agent_facts")] == ["1.4.2"]   # never read back
    assert [f["object"] for f in json.load(open(path))["facts"]] == ["1.4.2"]       # the changed copy, sent again
    os.unlink(path)                                     # and one gone (a new machine's volume) is sent again too
    agents.sync(user)
    assert [f["object"] for f in json.load(open(path))["facts"]] == ["1.4.2"]
