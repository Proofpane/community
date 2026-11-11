"""The improve loop in the app (improve.py): off until turned on; one review a day, to the agent with the most
signal, with a cooldown and a budget; a review's changes taken once, pruned, refused or on to the critic; the sandbox
planned from replayable signal and comparison runs and scored by the sign test, its comparison regressions and its
cost; the owner's approval making the change and its version; a rollback proposed on a significant drop alone; and
what it all cost."""

import json
import secrets
import time

import pytest

from test_agents_app import A, agent, owner, local, until  # noqa: F401  (fixtures)
from test_agent_quality_app import apply, ended, judged, made, me, quiet, verdict

DAY = 86400
BASE = "b" * 40


def store_now():
    from proofpane_open import store
    return store.connect()


def row(agent_id):
    with store_now() as db:
        return db.execute("SELECT * FROM ade_agents WHERE id = ?", (agent_id,)).fetchone()


def turn_on(client, h, a, **over):
    r = client.put(f"{A}/{a['id']}", headers=h, json=dict({"improve": True}, **over))
    assert r.status_code == 200 and r.json()["improve"] is True, r.text


def signal_run(client, h, user_id, a, at, **over):
    """An ended run of the agent with a thumbs down: a signal of weight 1.0."""
    r = made(client, h, a["id"])
    apply(user_id, ended(r, a["id"], at, **dict({"base": BASE, "metrics": {"replayable": True}}, **over)))
    client.post(f"{A}/runs/{r}/thumbs", headers=h, json={"value": "down"})
    return r


def passing_run(client, h, user_id, a, at):
    r = made(client, h, a["id"])
    apply(user_id, ended(r, a["id"], at, base=BASE, metrics={"replayable": True}))
    return r


def review_now(user_id, now=None):
    from proofpane_open.ade import agents, improve
    with store_now() as db:
        return improve.maybe_review(db, {"id": user_id}, now or int(time.time()), agents.new_run)


def test_the_loop_is_off_until_turned_on_and_a_day_s_review_goes_to_the_agent_with_the_most_signal(client, owner,
                                                                                                  monkeypatch):
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    most, less, off = (agent(client, owner, name=n) for n in ("Triage", "Labeler", "Off"))
    assert most["improve"] is False
    signal_run(client, owner, user_id, most, now - 60)
    r = made(client, owner, less["id"])
    apply(user_id, ended(r, less["id"], now - 60, metrics={"claims": {"flagged": True}}))   # 0.3
    for _ in range(3):
        signal_run(client, owner, user_id, off, now - 60)                                  # off: never reviewed
    assert review_now(user_id, now) is None                                                # none turned on
    turn_on(client, owner, most)
    turn_on(client, owner, less)
    with store_now() as db:                                  # reviewed long ago: last in the order, first by its signal
        db.execute("UPDATE ade_agents SET reviewed_at = ? WHERE id = ?", (now - 30 * DAY, most["id"]))
    first = review_now(user_id, now)
    with store_now() as db:
        kept = db.execute("SELECT * FROM ade_agent_runs WHERE run_id = ?", (first,)).fetchone()
        sessions = db.execute("SELECT kind, run_id FROM ade_improve_sessions").fetchall()
    assert (kept["agent_id"], kept["loop"], json.loads(kept["trigger"])["kind"]) == (most["id"], 1, "review")
    assert [(s["kind"], s["run_id"]) for s in sessions] == [("review", first)]
    assert review_now(user_id, now + 60) is None                                           # one a day
    signal_run(client, owner, user_id, most, now + 120)                                    # new, but in its cooldown
    paused = agent(client, owner, name="Paused")
    turn_on(client, owner, paused)
    for _ in range(3):
        signal_run(client, owner, user_id, paused, now + 120)
    client.post(f"{A}/{paused['id']}/pause", headers=owner)
    second = review_now(user_id, now - now % DAY + DAY + 60)
    with store_now() as db:
        assert db.execute("SELECT agent_id FROM ade_agent_runs WHERE run_id = ?", (second,)).fetchone()[0] == less["id"]
    assert row(most["id"])["reviewed_at"] == now and row(less["id"])["reviewed_at"] == now - now % DAY + DAY + 60
    listing = client.get(A, headers=owner).json()
    assert all(r["run_id"] not in (first, second) for g in listing["groups"] for x in g["agents"] for r in x["runs"])


def test_no_review_while_a_proposal_is_on_its_way_nor_past_the_week_s_budget(client, owner, monkeypatch):
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner)
    turn_on(client, owner, a)
    signal_run(client, owner, user_id, a, now - 60)
    with store_now() as db:
        for n in range(40):
            improve.session(db, user_id, a["id"], "replay" if n % 2 else "critic", now - 3600)
        assert improve.budget(db, user_id, now)["spent"] is True
    assert review_now(user_id, now) is None                                                # 40 sessions this week
    with store_now() as db:
        db.execute("UPDATE ade_improve_sessions SET at = ?", (now - 8 * DAY,))              # last week's
        improve.session(db, user_id, a["id"], "judge", now - 60, cost=20.0)                # 20 USD this week
        assert improve.budget(db, user_id, now) == {"sessions": 0, "usd": 20.0, "max_sessions": 40, "max_usd": 20.0,
                                                    "spent": True}
    assert review_now(user_id, now) is None
    with store_now() as db:
        db.execute("DELETE FROM ade_improve_sessions")
        db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, kind, target, state, created_at) VALUES "
                   "('p1p2p3p4p5p6', ?, ?, 'change', 'brief', 'waiting', ?)", (user_id, a["id"], now))
    assert review_now(user_id, now) is None                                                # one on its way
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET state = 'not_eligible'")
    assert review_now(user_id, now) is not None


def review_run(client, h, user_id, a, changes, at):
    """A review run of the agent, ended with its changes."""
    from proofpane_open.ade import agents
    with store_now() as db:
        run_id = agents.new_run(db, row(a["id"]), {"kind": "review", "at": at}, at, loop=True)
    apply(user_id, {"run_id": run_id, "agent_id": a["id"], "state": "done", "ended_at": at, "changes": changes})
    return run_id


def change(target, value, name=None, runs=()):
    return {"target": target, "name": name, "value": value, "hypothesis": "It helps.", "evidence": {"runs": list(runs)}}


def taken(user_id, now):
    from proofpane_open.ade import improve
    with store_now() as db:
        improve.take(db, user_id, now)
        return sorted((p["target"], p["state"], p["reason"] or "") for p in db.execute("SELECT * FROM ade_improve_proposals"))


def test_a_review_s_changes_are_taken_once_pruned_past_three_edits_and_the_prompt_changed_once_a_month(client, owner,
                                                                                                     monkeypatch):
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="a\nb\nc\nd\ne\nf\ng")
    review_run(client, owner, user_id, a, [change("brief", "a\nB\nc\nd\ne\nf\ng"),
                                          change("brief", "A\nb\nC\nd\nE\nf\nG")], now)
    review_run(client, owner, user_id, a, [change("effort", None) | {"value": "high"},
                                          change("review_prompt", "Review it closely.")], now)
    review_run(client, owner, user_id, a, [change("review_prompt", "Review it more closely."),
                                          change("brief", "a\nb\nc\nd\ne\nf\ng")], now)
    got = taken(user_id, now)
    assert ("brief", "critic", "") in got and ("effort", "critic", "") in got
    assert ("brief", "pruned", "it bundles 4 edits; a proposal makes at most 3") in got
    assert ("brief", "refused", "it changes nothing") in got
    assert ("review_prompt", "eligible", "") in got                                        # the owner's tap, no sandbox
    assert ("review_prompt", "refused", "one change to the review prompt a month") in got
    assert taken(user_id, now) == got                                                       # each once
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET created_at = ? WHERE target = 'review_prompt'", (now - 31 * DAY,))
    review_run(client, owner, user_id, a, [change("review_prompt", "Review it again.")], now)
    assert len([g for g in taken(user_id, now) if g[:2] == ("review_prompt", "eligible")]) == 2


def proposals():
    with store_now() as db:
        return {p["id"]: p for p in db.execute("SELECT * FROM ade_improve_proposals")}


def test_the_critic_refuses_a_change_naming_a_run_s_input_and_passes_one_that_does_not(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    asked = made(client, owner, a["id"])
    with store_now() as db:
        db.execute("UPDATE ade_agent_runs SET trigger = ? WHERE run_id = ?",
                   (json.dumps({"kind": "manual", "prompt": "Label issue 4242 as bug"}), asked))
    review_run(client, owner, user_id, a, [change("brief", "Label the issues. Label issue 4242 as bug", runs=[asked]),
                                          change("brief", "Label the issues by area.", runs=[asked])], now)
    review_run(client, owner, user_id, a, [change("brief", "Label the issues by SPECIFIC area.", runs=[asked])], now)
    taken(user_id, now)
    calls = []

    def call(endpoint, method, path, body=None, timeout=None):
        calls.append((path, body["change"]["value"], body["inputs"]))
        return {"specific": "SPECIFIC" in body["change"]["value"], "why": "it names one", "cost_est_usd": 0.004}
    improve.criticise(store.connect, {"id": user_id}, "http://workspace", call, now)
    states = {json.loads(p["proposed"]): (p["state"], p["reason"]) for p in proposals().values()}
    assert states["Label the issues. Label issue 4242 as bug"] == ("refused", "it names a run's own input word for word")
    assert states["Label the issues by area."] == ("waiting", None)
    assert states["Label the issues by SPECIFIC area."] == ("refused", "the critic: it names one")
    assert [c[1] for c in calls] == ["Label the issues by area.", "Label the issues by SPECIFIC area."]
    assert calls[0][2] == [asked]
    with store_now() as db:
        assert [(s["kind"], s["cost_est_usd"]) for s in db.execute("SELECT * FROM ade_improve_sessions")] == \
            [("critic", 0.004), ("critic", 0.004)]


def waiting_proposal(user_id, a, value, now, runs=()):
    with store_now() as db:
        db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, kind, target, current, proposed, "
                   "hypothesis, evidence, edits, state, created_at) VALUES ('p1p2p3p4p5p6', ?, ?, 'change', 'brief', ?, "
                   "?, 'h', ?, 1, 'waiting', ?)", (user_id, a["id"], json.dumps(row(a["id"])["brief"]),
                                                  json.dumps(value), json.dumps({"runs": list(runs)}), now))
    return "p1p2p3p4p5p6"


def plan(user_id, now):
    from proofpane_open.ade import agents, improve
    with store_now() as db:
        return improve.plan_sandbox(db, {"id": user_id}, now, agents.new_run)


def test_a_sandbox_replays_its_inputs_twice_from_their_base_and_memory_and_too_few_evaluate_nothing(client, owner,
                                                                                                  monkeypatch):
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    signal = [signal_run(client, owner, user_id, a, now - 600 + n) for n in range(4)]
    signal_run(client, owner, user_id, a, now - 610, metrics={"replayable": False})        # not replayable: not an input
    comparison = [passing_run(client, owner, user_id, a, now - 300 + n) for n in range(6)]
    pid = waiting_proposal(user_id, a, "work GOOD", now)
    assert plan(user_id, now) is None
    assert (proposals()[pid]["state"], proposals()[pid]["reason"]) == \
        ("not_evaluated", "fewer than 5 replayable signal runs (4)")                      # five needed
    signal.append(signal_run(client, owner, user_id, a, now - 500))
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET state = 'waiting'")
        db.execute("INSERT INTO ade_agent_memory (agent_id, user_id, version, run_id, kind, digest, files, made_at) "
                   "VALUES (?, ?, 1, NULL, 'run', ?, ?, ?)", (a["id"], user_id, "d" * 64,
                                                              json.dumps({"MEMORY.md": "older"}), now - 5000))
        db.execute("INSERT INTO ade_agent_memory (agent_id, user_id, version, run_id, kind, digest, files, made_at) "
                   "VALUES (?, ?, 2, NULL, 'run', ?, ?, ?)", (a["id"], user_id, "d" * 64,
                                                              json.dumps({"MEMORY.md": "before", "img": None}), now - 4000))
        db.execute("INSERT INTO ade_agent_memory (agent_id, user_id, version, run_id, kind, digest, files, made_at) "
                   "VALUES (?, ?, 3, NULL, 'run', ?, ?, ?)", (a["id"], user_id, "d" * 64,
                                                              json.dumps({"MEMORY.md": "after its start"}), now - 100))
        improve.session(db, user_id, a["id"], "replay", now - 60)
        for _ in range(18):                                                               # 19 + 22 > 40: it waits
            improve.session(db, user_id, a["id"], "critic", now - 60)
    assert plan(user_id, now) is None and proposals()[pid]["state"] == "waiting"
    with store_now() as db:
        db.execute("DELETE FROM ade_improve_sessions")
    assert plan(user_id, now) == pid
    p = proposals()[pid]
    inputs = json.loads(p["sandbox"])["inputs"]
    assert p["state"] == "sandbox" and [i["group"] for i in inputs] == ["signal"] * 5 + ["comparison"] * 6
    assert {i["run_id"] for i in inputs} == set(signal) | set(comparison)               # up to 8 of each
    with store_now() as db:
        runs = {r["run_id"]: r for r in db.execute("SELECT * FROM ade_agent_runs WHERE loop = 1")}
        assert len(runs) == 22 and improve.budget(db, user_id, now)["sessions"] == 22
        treatment = improve.sandbox_body(db, runs[inputs[0]["treatment"]])
        control = improve.sandbox_body(db, runs[inputs[0]["control"]])
    assert (treatment["brief"], control["brief"]) == ("work GOOD", "work BAD")
    assert treatment["base"] == BASE and treatment["memory"] == {"MEMORY.md": "before"}
    assert {i["first"] for i in inputs} == {"control", "treatment"}                       # drawn, both ways
    assert plan(user_id, now) is None                                                     # one in the sandbox at a time


def scored(user_id, a, outcomes, costs=(0.1, 0.1), group_of=None, now=None):
    """A proposal in the sandbox with each input's control and treatment ended; `outcomes` [(control passes,
    treatment passes, group)], judged by a stand-in pairwise judge that passes what the outcome says. Its state."""
    from proofpane_open import store
    from proofpane_open.ade import agents, improve
    now = now or int(time.time())
    with store_now() as db:
        db.execute("DELETE FROM ade_improve_proposals")
        db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, kind, target, current, proposed, "
                   "hypothesis, evidence, edits, state, created_at) VALUES ('p1p2p3p4p5p6', ?, ?, 'change', 'brief', "
                   "'\"a\"', '\"b\"', 'h', '{}', 1, 'sandbox', ?)", (user_id, a["id"], now))
        inputs, passing = [], {}
        for n, (c, t, group) in enumerate(outcomes):
            ids = {}
            for arm, ok, cost in (("control", c, costs[0]), ("treatment", t, costs[1])):
                ids[arm] = agents.new_run(db, row(a["id"]), {"kind": "sandbox", "arm": arm}, now, loop=True)
                # None: blocked by the machine; "start": failed to start, the machine's doing, not the agent's
                state, cause = ("blocked", "machine") if ok is None else ("failed", "start") if ok == "start" else \
                    ("done", None)
                db.execute("UPDATE ade_agent_runs SET state = ?, cause = ?, cost_est_usd = ?, ended_at = ? WHERE "
                           "run_id = ?", (state, cause, cost, now, ids[arm]))
                passing[ids[arm]] = ok is True
            inputs.append({"run_id": f"{n:012x}", "group": group, "control": ids["control"],
                           "treatment": ids["treatment"], "first": "control" if n % 2 else "treatment"})
        db.execute("UPDATE ade_improve_proposals SET sandbox = ?", (json.dumps({"inputs": inputs}),))

    def call(endpoint, method, path, body=None, timeout=None):
        grade = lambda run: verdict(0.9 if passing[run] else 0.3)  # noqa: E731
        return {"A": grade(body["a"]), "B": grade(body["b"]), "cost_est_usd": 0.001}
    return improve.score_sandbox(store.connect, {"id": user_id}, "http://workspace", call, now)


def test_five_of_five_differing_signal_pairs_are_eligible_and_two_comparison_regressions_are_not(client, owner,
                                                                                                monkeypatch):
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    comparison = [(True, True, "comparison")] * 5
    assert scored(user_id, a, [(False, True, "signal")] * 5 + comparison) == "eligible"
    result = json.loads(proposals()["p1p2p3p4p5p6"]["result"])
    assert (result["wins"], result["losses"], result["p"], result["regressions"]) == (5, 0, 0.03125, 0)
    assert scored(user_id, a, [(False, True, "signal")] * 6 + [(True, False, "signal")] + comparison) == "not_eligible"
    assert scored(user_id, a, [(False, True, "signal")] * 5 + [(True, False, "comparison")] +
                  comparison[:4]) == "eligible"                                            # one regression allowed
    assert scored(user_id, a, [(False, True, "signal")] * 5 + [(True, False, "comparison")] * 2 +
                  comparison[:3]) == "not_eligible"
    assert scored(user_id, a, [(True, True, "signal")] * 3 + [(False, False, "signal")] * 3 +
                  comparison) == "not_eligible"                                            # a change that does nothing
    assert scored(user_id, a, [(False, True, "signal")] * 5 + comparison, costs=(0.10, 0.13)) == "pruned"
    assert "30% more a run" in proposals()["p1p2p3p4p5p6"]["reason"]
    cut = scored(user_id, a, [(False, True, "signal")] * 4 + [(None, True, "signal")] + comparison)
    assert cut == "not_evaluated"                                                          # a pair cut short says nothing
    assert scored(user_id, a, [(False, True, "signal")] * 4 + [("start", True, "signal")] + comparison) == "not_evaluated"
    with store_now() as db:                                                                # its acceptance checks count
        db.execute("UPDATE ade_agents SET acceptance = ? WHERE id = ?", (json.dumps({"required_keys": ["x"]}), a["id"]))
    assert scored(user_id, a, [(False, True, "signal")] * 5 + comparison) == "not_eligible"     # none recorded passing
    assert json.loads(proposals()["p1p2p3p4p5p6"]["result"])["wins"] == 0


def eligible_proposal(user_id, a, target, value, now, name=None, current=None):
    pid = secrets.token_hex(6)
    with store_now() as db:
        db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, kind, target, name, current, proposed, "
                   "hypothesis, evidence, edits, state, created_at) VALUES (?, ?, ?, 'change', ?, ?, ?, ?, 'h', '{}', 1, "
                   "'eligible', ?)", (pid, user_id, a["id"], target, name, json.dumps(current), json.dumps(value), now))
    return pid


def test_approving_makes_the_change_and_its_version_rejecting_says_why_and_a_proposal_expires(client, owner,
                                                                                            monkeypatch):
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    brief = eligible_proposal(user_id, a, "brief", "work GOOD", now, current="work BAD")
    got = client.post(f"{A}/improvements/{brief}/approve", headers=owner)
    assert got.status_code == 200 and got.json()["state"] == "approved", got.text
    after = client.get(f"{A}/{a['id']}", headers=owner).json()
    assert (after["brief"], after["brief_version"]) == ("work GOOD", 2)
    with store_now() as db:
        versions = [(v["target"], v["version"], json.loads(v["value"]), v["author"], v["approver"])
                    for v in db.execute("SELECT * FROM ade_agent_versions ORDER BY created_at, version")]
    assert versions == [("brief", 1, "work BAD", "owner", None), ("brief", 2, "work GOOD", "loop", "owner")]
    skill = "---\ndescription: Label by area.\n---\nDo it.\n"
    other = eligible_proposal(user_id, a, "skill", skill, now, name="labels")
    client.post(f"{A}/improvements/{other}/approve", headers=owner)
    assert client.get(f"{A}/{a['id']}", headers=owner).json()["skills"] == [{"name": "labels", "content": skill}]
    assert client.post(f"{A}/improvements/{other}/approve", headers=owner).status_code == 409
    harder = eligible_proposal(user_id, a, "effort", "max", now)
    window = eligible_proposal(user_id, a, "autocompact_window", 300000, now)
    for pid in (harder, window):
        assert client.post(f"{A}/improvements/{pid}/approve", headers=owner).status_code == 200
    after = client.get(f"{A}/{a['id']}", headers=owner).json()
    assert (after["chat_settings"]["effort"], after["autocompact_window"]) == ("max", 300000)
    no = eligible_proposal(user_id, a, "effort", "max", now)
    told = client.post(f"{A}/improvements/{no}/reject", headers=owner, json={"reason": "too dear"}).json()
    assert (told["state"], told["reason"]) == ("rejected", "too dear")
    old = eligible_proposal(user_id, a, "model", "haiku", now - 15 * DAY)
    with store_now() as db:
        improve.expire(db, user_id, now)
    assert proposals()[old]["state"] == "expired"
    assert client.delete(f"{A}/{a['id']}/skills/labels", headers=owner).json()["skills"] == []


def test_two_approvals_at_once_make_one_change_and_one_version(client, owner, monkeypatch):
    """A double tap: two approvals of one proposal at once. The first claims it; the second finds it decided."""
    import threading
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    pid = eligible_proposal(user_id, a, "brief", "work GOOD", now, current="work BAD")
    made_it = improve.apply

    def slow(*args, **kwargs):
        time.sleep(0.5)                                 # the first tap's change being made as the second arrives
        return made_it(*args, **kwargs)
    monkeypatch.setattr(improve, "apply", slow)
    codes = []

    def tap():
        try:
            with store_now() as db:
                improve.approve(db, {"id": user_id}, pid, now)
            codes.append(200)
        except improve.ImproveError as e:
            codes.append(e.status)
    taps = [threading.Thread(target=tap) for _ in range(2)]
    for t in taps:
        t.start()
    for t in taps:
        t.join()
    assert sorted(codes) == [200, 409]
    with store_now() as db:
        made = [json.loads(v["value"]) for v in db.execute("SELECT * FROM ade_agent_versions WHERE author = 'loop'")]
    assert made == ["work GOOD"] and proposals()[pid]["state"] == "approved"


def test_the_owner_s_reject_during_the_critic_s_call_stands(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    review_run(client, owner, user_id, a, [change("brief", "Label the issues by area.")], now)
    taken(user_id, now)
    (pid,) = proposals()

    def call(endpoint, method, path, body=None, timeout=None):
        r = client.post(f"{A}/improvements/{pid}/reject", headers=owner, json={"reason": "not now"})
        assert r.status_code == 200, r.text                          # the owner, while the critic is asked
        return {"specific": False, "cost_est_usd": 0.004}
    improve.criticise(store.connect, {"id": user_id}, "http://workspace", call, now)
    assert (proposals()[pid]["state"], proposals()[pid]["reason"]) == ("rejected", "not now")
    approved = eligible_proposal(user_id, a, "brief", "Label them.", now)
    assert client.post(f"{A}/improvements/{approved}/approve", headers=owner).status_code == 200
    told = client.post(f"{A}/improvements/{approved}/reject", headers=owner, json={"reason": "late"})
    assert told.status_code == 409 and proposals()[approved]["state"] == "approved"   # a reject after: decided


@pytest.mark.parametrize("signals", [5, 4])           # enough to plan; too few, so it would be not_evaluated
def test_a_proposal_rejected_while_its_sandbox_is_planned_gets_no_runs(client, owner, monkeypatch, signals):
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    for n in range(signals):
        signal_run(client, owner, user_id, a, now - 600 + n)
    for n in range(6):
        passing_run(client, owner, user_id, a, now - 300 + n)
    pid = waiting_proposal(user_id, a, "work GOOD", now)
    looked = improve.runs_with_signals

    def rejected_meanwhile(db, agent_row, since, at):
        with store_now() as other:                      # the owner's reject, in a request of its own
            other.execute("UPDATE ade_improve_proposals SET state = 'rejected', reason = 'not now' WHERE id = ?",
                          (pid,))
        return looked(db, agent_row, since, at)
    monkeypatch.setattr(improve, "runs_with_signals", rejected_meanwhile)
    assert plan(user_id, now) is None
    with store_now() as db:
        sandboxes = db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE trigger LIKE '%sandbox%'").fetchone()[0]
    assert (proposals()[pid]["state"], sandboxes) == ("rejected", 0)


def test_a_run_under_way_when_a_change_is_approved_is_not_one_of_its_after_runs(client, owner, monkeypatch):
    """It started on the agent as it was: counted after (it ended after) it would weigh the old version as the new."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    judged_runs(client, owner, user_id, a, [True] * 20, now - 5000)
    promoted = eligible_proposal(user_id, a, "brief", "work GOOD", now - 4000, current="work BAD")
    client.post(f"{A}/improvements/{promoted}/approve", headers=owner)
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET approved_at = ? WHERE id = ?", (now - 3000, promoted))
    under_way = made(client, owner, a["id"])
    apply(user_id, ended(under_way, a["id"], now - 2990, started_at=now - 3010))
    judged(under_way, verdict(0.3))
    judged_runs(client, owner, user_id, a, [True] * 19, now - 2000)
    with store_now() as db:
        assert improve.watch(db, user_id, now) == []
        assert db.execute("SELECT watched_at FROM ade_improve_proposals WHERE id = ?", (promoted,)).fetchone()[0] is None


def test_the_digest_counts_a_brief_version_s_own_runs_not_the_loop_s(client, owner, monkeypatch):
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    signal_run(client, owner, user_id, a, now - 60)
    review_run(client, owner, user_id, a, [], now - 30)             # the loop's own run, ended done
    with store_now() as db:
        got = improve.digest(db, row(a["id"]))
    assert got["brief_versions"][0]["numbers"] == {"runs": 1, "done": 1, "judged": 0, "passed": 0}


def judged_runs(client, h, user_id, a, passes, start):
    for n, ok in enumerate(passes):
        r = made(client, h, a["id"])
        apply(user_id, ended(r, a["id"], start + n))
        judged(r, verdict(0.9 if ok else 0.3))


def test_a_significant_drop_after_a_promotion_makes_a_rollback_proposal_and_a_small_one_does_not(client, owner,
                                                                                               monkeypatch):
    from proofpane_open.ade import improve, quality
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    judged_runs(client, owner, user_id, a, [True] * 19 + [False], now - 5000)
    promoted = eligible_proposal(user_id, a, "brief", "work GOOD", now - 4000, current="work BAD")
    client.post(f"{A}/improvements/{promoted}/approve", headers=owner)
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET approved_at = ? WHERE id = ?", (now - 3000, promoted))
    r = made(client, owner, a["id"])
    apply(user_id, ended(r, a["id"], now - 2900))
    with store_now() as db:
        assert r in quality.due(db, user_id, limit=50)                                     # every run, judge off
    judged_runs(client, owner, user_id, a, [True] * 10 + [False] * 10, now - 2000)
    with store_now() as db:
        made_ = improve.watch(db, user_id, now)
    assert len(made_) == 1
    rollback = proposals()[made_[0]]
    assert (rollback["kind"], rollback["state"], json.loads(rollback["proposed"])) == ("rollback", "eligible", "work BAD")
    assert json.loads(rollback["result"])["before"] == 19
    client.post(f"{A}/improvements/{rollback['id']}/approve", headers=owner)
    assert client.get(f"{A}/{a['id']}", headers=owner).json()["brief"] == "work BAD"
    assert proposals()[promoted]["state"] == "rolled_back"
    c = agent(client, owner, name="New", brief="work BAD")                                  # 10 graded runs before
    judged_runs(client, owner, user_id, c, [True] * 10, now - 5000)
    early = eligible_proposal(user_id, c, "brief", "work GOOD", now - 4000, current="work BAD")
    client.post(f"{A}/improvements/{early}/approve", headers=owner)
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET approved_at = ? WHERE id = ?", (now - 3000, early))
    judged_runs(client, owner, user_id, c, [False] * 20, now - 2000)
    with store_now() as db:
        assert improve.watch(db, user_id, now) == []                                       # nothing to weigh it by
        assert db.execute("SELECT watched_at FROM ade_improve_proposals WHERE id = ?", (early,)).fetchone()[0] == now
    b = agent(client, owner, name="Labeler", brief="work BAD")
    judged_runs(client, owner, user_id, b, [True] * 19 + [False], now - 5000)
    small = eligible_proposal(user_id, b, "brief", "work GOOD", now - 4000, current="work BAD")
    client.post(f"{A}/improvements/{small}/approve", headers=owner)
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET approved_at = ? WHERE id = ?", (now - 3000, small))
    judged_runs(client, owner, user_id, b, [True] * 16 + [False] * 4, now - 2000)
    with store_now() as db:
        assert improve.watch(db, user_id, now) == []                                        # 16 of 20: not significant
        assert db.execute("SELECT watched_at FROM ade_improve_proposals WHERE id = ?", (small,)).fetchone()[0] == now


def test_the_cost_of_improvement_is_the_sum_of_the_loop_s_sessions(client, owner, monkeypatch):
    from proofpane_open.ade import agents, improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner)
    plain = made(client, owner, a["id"])
    apply(user_id, ended(plain, a["id"], now, cost_est_usd=0.5))
    with store_now() as db:
        loop = [agents.new_run(db, row(a["id"]), {"kind": kind}, now, loop=True) for kind in ("review", "sandbox", "sandbox")]
        for run_id, kind in zip(loop, ("review", "replay", "replay")):
            improve.session(db, user_id, a["id"], kind, now, run_id=run_id)
        improve.session(db, user_id, a["id"], "critic", now, cost=0.004)
        improve.session(db, user_id, a["id"], "judge", now, cost=0.006)
    apply(user_id, *(dict(ended(r, a["id"], now + 1, cost_est_usd=c), trigger={"kind": "sandbox"})
                     for r, c in zip(loop, (0.05, 0.02, 0.03))))
    with store_now() as db:
        spent = db.execute("SELECT SUM(cost_est_usd) FROM ade_improve_sessions").fetchone()[0]
    q = client.get(f"{A}/{a['id']}/metrics?days=7", headers=owner).json()["quality"]
    assert round(spent, 6) == 0.11 and q["cost_of_improvement"]["usd"] == round(spent, 6)
    assert q["cost_of_improvement"]["share"] == round(0.11 / 0.61, 4)
    totals = client.get(f"{A}/{a['id']}", headers=owner).json()["totals"]
    assert (totals["runs"], totals["cost_est_usd"]) == (1, 0.5)                             # the loop's runs apart


def test_a_review_carries_its_prompt_and_digest_and_a_sandbox_run_its_arm(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    signal = signal_run(client, owner, user_id, a, now - 60)
    turn_on(client, owner, a)
    review = review_now(user_id, now)
    with store_now() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    sent = []
    monkeypatch.setattr(agents.client, "call", lambda endpoint, method, path, body=None, timeout=None:
                        sent.append((path, body)) or {"run_id": body["run_id"], "agent_id": a["id"], "state": "queued"})
    agents._send({"id": user_id}, "http://workspace")
    (path, body), = [s for s in sent if s[1]["run_id"] == review]
    trigger = body["trigger"]
    assert trigger["kind"] == "review" and trigger["review"]["prompt"].startswith("Review this agent's recent runs")
    digest = trigger["review"]["digest"]
    assert digest["current"]["brief"] == "work BAD" and [r["run_id"] for r in digest["runs"]] == [signal]
    assert digest["runs"][0]["signals"] == ["thumbs_down"]
    assert [v["brief"] for v in digest["brief_versions"]] == ["work BAD"]
