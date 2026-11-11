"""Five things the improve loop (ade/improve.py) does, the items 1 to 5 below: (1) a sandbox pair one of whose runs
failed at start is dropped without asking the pairwise judge, which reads both runs and cannot read one the workspace
never made, so the proposal is scored on its other pairs and the account's loop goes on; (2) a change pruned for more
than MAX_EDITS edits whose text holds a run's own input word for word is listed in `tried` with no value, its reason
kept; (3) the agent's row on the agents' page says when the loop last reviewed it and why it passed it over (the page's
own tests, in ui/src/components/Agents.test.tsx); (4) a proposal refused at the owner's tap for the agent's skills is
marked refused with the limit's reason, and its alert ends; and (5) the digest is measured again when the review's run
is sent, one grown past DIGEST_MAX not sent, the agent passed over with its session and cooldown undone."""

import json
import time

from test_agents_app import A, agent, owner, local  # noqa: F401  (fixtures)
from test_agent_quality_app import made, me, quiet, verdict
from test_agent_improve_app import (DAY, change, proposals, review_now, review_run, row, signal_run, store_now, taken,
                                    turn_on)
from test_improve_limits import FULL, FULL_AT_APPROVAL, SKIPPED, approve, hold, held, skill, wide_runs
from skills_readme import text as file_text
from conftest import manual_or_skip


def sandbox(user_id, a, outcomes, now):
    """A proposal in the sandbox with each input's control and treatment ended, as test_agent_improve_app's scored()
    makes one: `outcomes` [(control, treatment, group)], each True or False for a done run the judge passes or fails,
    "start" for a run that failed at start, here one the app never sent, so never made in the workspace, "machine"
    for one the machine ended after it was made there, or "error_result" for one that failed on its own account. Its
    inputs, the runs that failed at start, and which runs pass."""
    from proofpane_open.ade import agents
    with store_now() as db:
        db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, kind, target, current, proposed, "
                   "hypothesis, evidence, edits, state, created_at) VALUES ('p1p2p3p4p5p6', ?, ?, 'change', 'brief', "
                   "'\"a\"', '\"b\"', 'h', '{}', 1, 'sandbox', ?)", (user_id, a["id"], now))
        inputs, never_made, passing = [], set(), {}
        for n, (c, t, group) in enumerate(outcomes):
            ids = {}
            for arm, ok in (("control", c), ("treatment", t)):
                ids[arm] = agents.new_run(db, row(a["id"]), {"kind": "sandbox", "arm": arm}, now, loop=True)
                state, cause = ("failed", ok) if ok in ("start", "machine", "error_result") else ("done", None)
                db.execute("UPDATE ade_agent_runs SET state = ?, cause = ?, cost_est_usd = 0.1, ended_at = ? WHERE "
                           "run_id = ?", (state, cause, now, ids[arm]))
                if ok == "start":
                    never_made.add(ids[arm])
                passing[ids[arm]] = ok is True
            inputs.append({"run_id": f"{n:012x}", "group": group, "control": ids["control"],
                           "treatment": ids["treatment"], "first": "control" if n % 2 else "treatment"})
        db.execute("UPDATE ade_improve_proposals SET sandbox = ?", (json.dumps({"inputs": inputs}),))
    return inputs, never_made, passing


def the_workspace_s_judge(never_made, passing, asked):
    """A stand-in for the workspace's pairwise judge that, as its judge_pair does, reads both runs of the pair it is
    given: one the workspace never made, a run that failed at start, is no run of its own, and the call is refused."""
    from proofpane_open.ade import workspace_client

    def call(endpoint, method, path, body=None, timeout=None):
        assert path == "/agents/judge-pair"
        asked.append((body["a"], body["b"]))
        if body["a"] in never_made or body["b"] in never_made:
            raise workspace_client.WorkspaceCallError(404, "No such run.")
        grade = lambda run: verdict(0.9 if passing[run] else 0.3)  # noqa: E731
        return {"A": grade(body["a"]), "B": grade(body["b"]), "cost_est_usd": 0.001}
    return call


def test_a_pair_whose_other_run_failed_at_start_is_scored_without_the_judge_and_the_account_s_loop_goes_on(
        client, owner, monkeypatch):
    """Item 1: five signal pairs pass with the change and fail without it, five comparison pairs pass both ways, and
    two signal pairs each hold a run that failed at start, the control's in one and the treatment's in the other, its
    other run done. The judge is asked about the ten pairs whose runs both ended done and about neither of the two, so
    the proposal is scored on the ten, eligible, with both listed among its pairs, the run that failed at start and
    the one never judged each saying nothing; and the account's next review is queued, where before the judge's
    refusal of either pair left the proposal in its sandbox and the account's reviews and sandboxes waiting on it. A
    third signal pair holds a run the machine ended after the workspace made it, which the judge could read; its pair
    says nothing either way (_outcome), and the judge is not asked about it either. A sixth comparison pair's
    treatment failed on its own account (an error result): that says something, so the judge is asked about the pair,
    and it counts, a regression, the one a change may have and stay eligible."""
    from proofpane_open import store
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    turn_on(client, owner, a)
    signal_run(client, owner, user_id, a, now - 600)
    outcomes = [(False, True, "signal")] * 5 + [("start", True, "signal"), (True, "start", "signal"),
                                                (True, "machine", "signal")] + [(True, True, "comparison")] * 5 + \
        [(True, "error_result", "comparison")]
    inputs, never_made, passing = sandbox(user_id, a, outcomes, now)
    asked = []
    call = the_workspace_s_judge(never_made, passing, asked)
    state = improve.score_sandbox(store.connect, {"id": user_id}, "http://workspace", call, now)
    p = proposals()["p1p2p3p4p5p6"]
    assert (state, p["state"]) == ("eligible", "eligible")
    assert len(asked) == 11 and not {run for pair in asked for run in pair} & never_made
    assert {inputs[13]["control"], inputs[13]["treatment"]} in [set(pair) for pair in asked]
    assert not [pair for pair in asked if {inputs[7]["control"], inputs[7]["treatment"]} == set(pair)]
    result = json.loads(p["result"])
    assert (result["wins"], result["losses"], result["regressions"]) == (5, 0, 1)
    dropped = [g for g in result["pairs"] if g["run_id"] in {i["run_id"] for i in inputs[5:8]}]
    assert [(g["control"], g["treatment"]) for g in dropped] == [(None, None)] * 3
    own = [g for g in result["pairs"] if g["run_id"] == inputs[13]["run_id"]]
    assert [(g["control"], g["treatment"]) for g in own] == [(True, False)]
    with store_now() as db:
        judged = db.execute("SELECT COUNT(*) FROM ade_improve_sessions WHERE kind = 'judge'").fetchone()[0]
    assert judged == 11
    queued = review_now(user_id, now)
    with store_now() as db:
        got = db.execute("SELECT agent_id, trigger FROM ade_agent_runs WHERE run_id = ?", (queued,)).fetchone()
    assert got is not None and (got["agent_id"], json.loads(got["trigger"])["kind"]) == (a["id"], "review")



def test_a_sandbox_whose_every_pair_said_nothing_is_not_evaluated_and_says_why(client, owner, monkeypatch):
    """Item 1, every pair cut short: five signal pairs and five comparison pairs, the
    treatment of each failed at start. None is asked of the judge; the proposal is not evaluated, its reason the one the
    sign test's minimum gives with the pairs that said nothing named after it, since the runs were replayed and it was
    the machine that left the pairs with nothing to say, and its result counts them."""
    from proofpane_open import store
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    inputs, never_made, passing = sandbox(user_id, a, [(False, "start", "signal")] * 5 +
                                          [(True, "start", "comparison")] * 5, now)
    asked = []
    state = improve.score_sandbox(store.connect, {"id": user_id}, "http://workspace",
                                  the_workspace_s_judge(never_made, passing, asked), now)
    p = proposals()["p1p2p3p4p5p6"]
    assert (state, p["state"], asked) == ("not_evaluated", "not_evaluated", [])
    assert p["reason"] == ("fewer than 5 replayable signal runs (0): 10 of its 10 pairs said nothing, the machine "
                           "having ended a run of each")
    assert json.loads(p["result"])["dropped"] == 10

def test_a_change_pruned_for_its_edits_that_holds_a_run_s_input_word_for_word_is_listed_without_its_value(
        client, owner, monkeypatch):
    """Item 2: three reviews each propose a new brief of four edits, one more than a proposal may make. The first's
    text holds an evidence run's own prompt word for word; the second's is the same text, drawn from no run; the
    third's holds no run's input. All three are pruned as they are taken, each keeping its reason; `tried` lists the
    first and its twin with no value, as it lists a change refused for holding a run's input, and the third with its
    value; the run's prompt is nowhere in it."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="one\ntwo\nthree\nfour\nfive\nsix\nseven")
    asked = made(client, owner, a["id"])
    with store_now() as db:
        db.execute("UPDATE ade_agent_runs SET trigger = ? WHERE run_id = ?",
                   (json.dumps({"kind": "manual", "prompt": "Label issue 4242 as bug"}), asked))
    quoted = "ONE\ntwo\nTHREE\nfour\nFIVE\nsix\nSEVEN: Label issue 4242 as bug"
    clean = "ONE\ntwo\nTHREE\nfour\nFIVE\nsix\nSEVEN, each by area"
    review_run(client, owner, user_id, a, [change("brief", quoted, runs=[asked])], now - 30)
    review_run(client, owner, user_id, a, [change("brief", quoted)], now - 20)
    review_run(client, owner, user_id, a, [change("brief", clean, runs=[asked])], now - 10)
    taken(user_id, now)
    pruned = "it bundles 4 edits; a proposal makes at most 3"
    got = sorted((p["state"], p["reason"], json.loads(p["proposed"])) for p in proposals().values())
    assert got == sorted([("pruned", pruned, quoted), ("pruned", pruned, quoted), ("pruned", pruned, clean)])
    with store_now() as db:
        listed = improve.digest(db, row(a["id"]))["tried"]
    shown = sorted(((t["state"], t["reason"], t["proposed"]) for t in listed), key=lambda t: (t[2] is not None, t[2]))
    assert shown == [("pruned", pruned, None), ("pruned", pruned, None), ("pruned", pruned, json.dumps(clean))]
    assert "4242" not in json.dumps(listed)



def test_a_pruned_change_kept_before_the_withheld_column_is_listed_without_its_value_and_one_whose_run_is_gone_too(
        client, owner, monkeypatch):
    """Item 2 in a store made before the column: there `withheld` reads nothing for
    every proposal kept before it, so a pruned one whose text holds one of its evidence runs' prompts is checked as
    `tried` reads it, and listed with no value, with its twin; one pruned whose text holds none keeps its value. A
    change pruned once the column is there is marked as it is taken, so it stays out of `tried` after its evidence run
    is gone, when no check could find the prompt again."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    old = agent(client, owner, name="Old", brief="one\ntwo\nthree\nfour\nfive\nsix\nseven")
    asked = made(client, owner, old["id"])
    with store_now() as db:
        db.execute("UPDATE ade_agent_runs SET trigger = ? WHERE run_id = ?",
                   (json.dumps({"kind": "manual", "prompt": "Label issue 4242 as bug"}), asked))
    quoted = "ONE\ntwo\nTHREE\nfour\nFIVE\nsix\nSEVEN: Label issue 4242 as bug"
    clean = "ONE\ntwo\nTHREE\nfour\nFIVE\nsix\nSEVEN, each by area"
    pruned = "it bundles 4 edits; a proposal makes at most 3"
    with store_now() as db:                         # kept with no word on the column, as a store made before it holds
        for pid, value, runs in (("o1o2o3o4o5o6", quoted, [asked]), ("t1t2t3t4t5t6", quoted, []),   # them
                                 ("c1c2c3c4c5c6", clean, [asked])):
            db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, kind, target, current, proposed, "
                       "hypothesis, evidence, edits, state, reason, created_at) VALUES (?, ?, ?, 'change', "
                       "'brief', ?, ?, 'h', ?, 4, 'pruned', ?, ?)",
                       (pid, user_id, old["id"], json.dumps(row(old["id"])["brief"]), json.dumps(value),
                        json.dumps({"runs": runs}), pruned, now - 100))
        listed = {t["id"]: t["proposed"] for t in improve.tried(db, row(old["id"]))}
    assert listed == {"o1o2o3o4o5o6": None, "t1t2t3t4t5t6": None, "c1c2c3c4c5c6": json.dumps(clean)}
    new = agent(client, owner, name="New", brief="one\ntwo\nthree\nfour\nfive\nsix\nseven")
    asked_new = made(client, owner, new["id"])
    with store_now() as db:
        db.execute("UPDATE ade_agent_runs SET trigger = ? WHERE run_id = ?",
                   (json.dumps({"kind": "manual", "prompt": "Label issue 4242 as bug"}), asked_new))
    review_run(client, owner, user_id, new, [change("brief", quoted, runs=[asked_new])], now - 10)
    taken(user_id, now)
    with store_now() as db:
        db.execute("DELETE FROM ade_agent_runs WHERE run_id = ?", (asked_new,))
        listed = [(t["state"], t["proposed"]) for t in improve.tried(db, row(new["id"]))]
    assert listed == [("pruned", None)]

def test_a_proposal_refused_at_the_owner_s_tap_for_the_agent_s_skills_is_marked_refused_and_its_alert_ends(
        client, owner, monkeypatch):
    """Item 4: an agent holding 20 skills, and an eligible proposal of a 21st. The owner's tap is refused, its detail
    naming the limit as before; and now the proposal is refused with the limit's reason, not left eligible: never
    approved, no time of decision (a refusal of the count, not of the change, so no reason to refuse its twin once the
    agent has room), no skill and no version; the app's alert for a proposal ready for the owner's tap, which it raised,
    ends; and a second tap is refused as for any proposal past its sandbox. Once the owner has taken a skill away, a
    later review's equal change goes on to the critic, not refused as tried before."""
    from proofpane_open.ade import agents, shapes
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    names = hold(user_id, a, shapes.MAX_SKILLS)
    review_run(client, owner, user_id, a, [change("skill", skill("Sort"), name="sorting")], now - 60)
    taken(user_id, now - 60)
    (pid,) = proposals()
    with store_now() as db:
        # past the critic and the sandbox, as a proposal taken while the agent held 19 would be: no reason of intake's
        db.execute("UPDATE ade_improve_proposals SET state = 'eligible', reason = NULL")
    assert f"agent-improve:{pid}" in [key for key, _ in agents.alerts(user_id, now)]
    refused = approve(client, owner, pid)
    assert (refused.status_code, refused.json().get("detail")) == (409, FULL_AT_APPROVAL.format(n=20)), refused.text
    p = proposals()[pid]
    assert (p["state"], p["reason"], p["decided_at"], p["approved_at"]) == ("refused", FULL.format(n=20), None, None)
    assert f"agent-improve:{pid}" not in [key for key, _ in agents.alerts(user_id, now)]
    assert "sorting" not in held(client, owner, a)
    with store_now() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_versions WHERE author = 'loop'").fetchone()[0] == 0
    again = approve(client, owner, pid)
    assert (again.status_code, again.json().get("detail")) == \
        (409, "Only a proposal that passed its sandbox, or a rollback, is approved."), again.text
    shown = client.get(f"{A}/improvements", headers=owner).json()["proposals"]
    assert [(x["state"], x["reason"], x["expires_at"]) for x in shown if x["id"] == pid] == \
        [("refused", FULL.format(n=20), None)]
    assert client.delete(f"{A}/{a['id']}/skills/{names[0]}", headers=owner).status_code == 200
    review_run(client, owner, user_id, a, [change("skill", skill("Sort"), name="sorting")], now)
    taken(user_id, now)
    twin = [p for p in proposals().values() if p["id"] != pid]
    assert [(p["state"], p["reason"]) for p in twin] == [("critic", None)]


def test_a_digest_grown_past_the_limit_since_its_review_was_queued_is_not_sent_and_the_agent_is_passed_over(
        client, owner, monkeypatch):
    """Item 5: Big, reviewed 30 days ago, has the most signal and a digest that fits, and its review is queued; before
    the run is sent, 20 runs whose part of the digest alone is past what the workspace takes end. When the run is sent,
    its digest is measured again and found too long: it is not sent, it ends failed at start with the reason, Big is
    passed over as when its digest is too long at the queue (its record says when and why), and its review's session
    and cooldown are undone: no session, its last review again the one of 30 days ago. Small's review, next by weight,
    is the one the day then gives, and Big is not measured again that day."""
    from proofpane_open.ade import agents, improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    big = agent(client, owner, name="Big", brief="work")
    small = agent(client, owner, name="Small", brief="work")
    for a in (big, small):
        turn_on(client, owner, a)
    for k in range(3):
        signal_run(client, owner, user_id, big, now - 900 + k)
    signal_run(client, owner, user_id, small, now - 800)
    with store_now() as db:
        db.execute("UPDATE ade_agents SET reviewed_at = ? WHERE id = ?", (now - 30 * DAY, big["id"]))
    queued = review_now(user_id, now)
    with store_now() as db:
        assert db.execute("SELECT agent_id FROM ade_agent_runs WHERE run_id = ?", (queued,)).fetchone()[0] == big["id"]
    assert row(big["id"])["reviewed_at"] == now
    wide_runs(client, owner, user_id, big, 20, now - 300)
    with store_now() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
        size = len(json.dumps(improve.digest(db, row(big["id"]))))
    assert size > improve.DIGEST_MAX
    sent = []
    monkeypatch.setattr(agents.client, "call", the_workspace_taking_runs(sent, big["id"]))
    before = int(time.time())
    agents._send({"id": user_id}, "http://workspace")
    after = int(time.time())
    assert queued not in sent
    with store_now() as db:
        run = db.execute("SELECT state, cause, reason, sent FROM ade_agent_runs WHERE run_id = ?", (queued,)).fetchone()
        sessions = db.execute("SELECT COUNT(*) FROM ade_improve_sessions WHERE kind = 'review'").fetchone()[0]
    assert (run["state"], run["cause"], run["reason"], run["sent"]) == ("failed", "start", SKIPPED.format(size=size), 0)
    assert sessions == 0
    assert row(big["id"])["reviewed_at"] == now - 30 * DAY
    skipped = client.get(f"{A}/{big['id']}", headers=owner).json().get("review_skipped") or {}
    assert skipped.get("reason") == SKIPPED.format(size=size) and before <= skipped.get("at", 0) <= after
    measured = []
    real = improve.digest
    monkeypatch.setattr(improve, "digest", lambda db, a: measured.append(a["id"]) or real(db, a))
    second = review_now(user_id, after)
    with store_now() as db:
        got = db.execute("SELECT agent_id FROM ade_agent_runs WHERE run_id = ?", (second,)).fetchone()
    assert got is not None and got[0] == small["id"] and measured == [small["id"]]


def test_the_manual_says_what_the_loop_now_does_with_each_of_the_five():
    """The manual's paragraphs on the improve loop, read with their line breaks as spaces, say each of the five as the
    code now does it, and no longer what it did before."""
    manual_or_skip()
    readme = file_text("MANUAL.md")
    start = readme.index("**Improving it, bounded.**")
    loop = " ".join(readme[start:readme.index("The pruner also", start)].split())
    assert "is dropped without asking the judge" in loop                                              # item 1
    assert "a run the app failed at start before sending it was never made" in loop
    assert "a run that failed at start was never made" not in loop
    assert "pruned as it was taken for more than 3 edits whose text holds one of its evidence runs' prompts" in loop
    assert "which no page shows" not in loop and "on the agents' page" in loop                       # item 3
    assert "while the loop is on for it" in loop
    assert "leaves the proposal eligible" not in loop and "which refuses the proposal" in loop       # item 4
    assert "a rollback refused so is not proposed again" in loop
    assert "The digest is measured again when the review is sent" in loop                           # item 5
    assert "is refused by the workspace, as before" not in loop


def sized(n):
    """A digest whose json.dumps is n characters long."""
    return {"pad": "x" * (n - len(json.dumps({"pad": ""})))}


def the_workspace_taking_runs(sent, agent_id):
    """A stand-in for the workspace's calls as the app sends runs: each run sent is recorded; any other call, such as
    the stop of a run the owner stopped after it was sent, is answered too."""
    def call(endpoint, method, path, body=None, timeout=None):
        run_id = (body or {}).get("run_id")
        if run_id:
            sent.append(run_id)
        return {"run_id": run_id, "agent_id": agent_id, "state": "queued"}
    return call


def test_at_the_send_a_review_stopped_meanwhile_is_left_as_its_stop_left_it_and_the_limit_itself_is_sent(
        client, owner, monkeypatch):
    """Item 5's edges. A review the owner stops while its run is being sent, after the
    run was read as queued and before the measurement, is not sent, and the measurement leaves it as the stop left it:
    no reason or cause written on it, its session and cooldown standing, the agent not marked passed over. A digest of
    exactly DIGEST_MAX characters, which the workspace takes, is sent; one a character longer is not."""
    from proofpane_open.ade import agents, improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    turn_on(client, owner, a)
    signal_run(client, owner, user_id, a, now - 600)
    queued = review_now(user_id, now)
    sent = []
    monkeypatch.setattr(agents.client, "call", the_workspace_taking_runs(sent, a["id"]))

    def stopped_meanwhile(db, agent):
        db.execute("UPDATE ade_agent_runs SET state = 'stopped' WHERE run_id = ?", (queued,))     # the owner's stop
        return sized(improve.DIGEST_MAX + 1)
    monkeypatch.setattr(improve, "digest", stopped_meanwhile)
    with store_now() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send({"id": user_id}, "http://workspace")
    with store_now() as db:
        run = db.execute("SELECT state, cause, reason FROM ade_agent_runs WHERE run_id = ?", (queued,)).fetchone()
        sessions = db.execute("SELECT COUNT(*) FROM ade_improve_sessions WHERE kind = 'review'").fetchone()[0]
    assert queued not in sent
    assert (run["state"], run["cause"], run["reason"]) == ("stopped", None, None)
    assert sessions == 1 and row(a["id"])["reviewed_at"] == now and row(a["id"])["review_skipped"] is None
    assert len(json.dumps(sized(improve.DIGEST_MAX))) == improve.DIGEST_MAX
    for size in (improve.DIGEST_MAX, improve.DIGEST_MAX + 1):
        monkeypatch.setattr(improve, "digest", lambda db, agent, size=size: sized(size))
        with store_now() as db:
            run_id = agents.new_run(db, row(a["id"]), {"kind": "review", "at": now, "reviewed_before": now}, now,
                                    loop=True)
            db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
        agents._send({"id": user_id}, "http://workspace")
        with store_now() as db:
            state = db.execute("SELECT state FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()[0]
        assert (run_id in sent, state) == ((True, "queued") if size == improve.DIGEST_MAX else (False, "failed"))


def test_a_review_an_older_release_queued_and_passed_over_at_the_send_keeps_its_cooldown(client, owner, monkeypatch):
    """Item 5 for a review an older release queued: its trigger holds no
    `reviewed_before`, so what the agent's reviewed_at was before it is not known. Passed over at the send, the run is
    not sent and its session is taken away, as for any, and the agent's reviewed_at stays as the review set it, not
    cleared, which would make the agent read as never reviewed."""
    from proofpane_open.ade import agents, improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    turn_on(client, owner, a)
    signal_run(client, owner, user_id, a, now - 600)
    with store_now() as db:
        db.execute("UPDATE ade_agents SET reviewed_at = ? WHERE id = ?", (now - 30 * DAY, a["id"]))
    queued = review_now(user_id, now)
    with store_now() as db:                         # its trigger as one queued before this change has it
        db.execute("UPDATE ade_agent_runs SET trigger = ? WHERE run_id = ?",
                   (json.dumps({"kind": "review", "at": now}), queued))
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    sent = []
    monkeypatch.setattr(agents.client, "call", the_workspace_taking_runs(sent, a["id"]))
    monkeypatch.setattr(improve, "digest", lambda db, agent: sized(improve.DIGEST_MAX + 1))
    agents._send({"id": user_id}, "http://workspace")
    with store_now() as db:
        state = db.execute("SELECT state FROM ade_agent_runs WHERE run_id = ?", (queued,)).fetchone()[0]
        sessions = db.execute("SELECT COUNT(*) FROM ade_improve_sessions WHERE kind = 'review'").fetchone()[0]
    assert (queued in sent, state, sessions) == (False, "failed", 0)
    assert row(a["id"])["reviewed_at"] == now
