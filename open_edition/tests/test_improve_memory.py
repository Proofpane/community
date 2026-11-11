"""The improve loop remembers what it already tried (ade/improve.py): a review's digest carries `tried`, the agent's own
decided proposals, newest first and at most 20, each with what became of it, a rollback's naming the change it undoes;
a change equal to one of the agent's own proposals that was refused as written for one run, found not eligible or pruned
by its sandbox, rejected by the owner or undone by a rollback is refused as it is taken, before any critic is asked,
unless the owner has approved an equal one since; the digest's brief versions and skills are bounded in number, and in
size past what the workspace takes, as the manual says; a value refused for naming a run's own input word for word,
and any later change equal to it, is listed without its value; the review prompt says what `tried` is for; and the
workspace hands `tried` to the review session as the app gave it, with words that say what the digest holds."""

import json
import re
import secrets
import time

import pytest

from conftest import signed_up, manual_or_skip
from test_agents_app import A, agent, owner, local  # noqa: F401  (fixtures)
from test_agent_quality_app import made, me, quiet
from test_agent_improve_app import change, eligible_proposal, judged_runs, proposals, review_run, row, store_now, taken
from skills_readme import text as file_text

BASE_CLAUSE = ("Weigh the brief's versions and their numbers: a change tried before and undone is not proposed again "
               "unless the owner has approved it since.")
SENTENCE = ("The digest's `tried` lists what was proposed before and what became of each: a change equal to one "
            "refused as written for one run, found not eligible or pruned by its sandbox, rejected by the owner or "
            "undone by a rollback is refused as it is taken, unless the owner has approved an equal one since (an "
            "undone change counting from the rollback that undid it, whose entry names it), and one like such a "
            "change is proposed only when the runs since give a reason it would fare otherwise.")
REFUSING = ("refused", "pruned", "not_eligible", "rejected", "rolled_back")
DECIDED = ("refused", "pruned", "not_eligible", "not_evaluated", "expired", "eligible", "approved", "rejected",
           "rolled_back")
FIELDS = {"id", "kind", "rolls_back", "target", "name", "hypothesis", "state", "reason", "edits", "result", "decided_at",
          "proposed"}
SKILL = "---\ndescription: Label the new issues by area.\n---\nLabel each one.\n"


def put(user_id, agent_id, state, value, target="brief", name=None, decided_at=None, created_at=None, result=None,
        reason=None, hypothesis="It helps.", edits=1, pid=None, kind="change", rolls_back=None):
    """A proposal as the loop keeps one, with what became of it; what `tried` must not show is marked NOT-SHOWN."""
    pid = pid or secrets.token_hex(6)
    with store_now() as db:
        db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, review_run_id, kind, target, name, "
                   "current, proposed, hypothesis, evidence, edits, state, reason, sandbox, result, created_at, "
                   "decided_at, rolls_back) VALUES (?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, user_id, agent_id, kind, target, name, json.dumps("CURRENT NOT-SHOWN"), json.dumps(value),
                    hypothesis, json.dumps({"runs": ["EVIDENCE NOT-SHOWN"]}), edits, state, reason,
                    json.dumps({"inputs": "SANDBOX NOT-SHOWN"}), None if result is None else json.dumps(result),
                    int(time.time()) if created_at is None else created_at, decided_at, rolls_back))
    return pid


def tried_of(a):
    from proofpane_open.ade import improve
    with store_now() as db:
        return improve.digest(db, row(a["id"]))["tried"]


def test_tried_holds_the_agent_s_own_decided_proposals_newest_first_twenty_of_twenty_five(client, owner, monkeypatch):
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner, brief="work")
    b = agent(client, owner, name="Labeler", brief="label")
    other = me(client, signed_up(client, "second-admin@example.com"))
    now = int(time.time())
    long_value = "x" * 1000
    out_of_order = {"b": 1, "a": [2, {"d": 3, "c": 4}]}
    sandboxed = {"evaluated": True, "why": "5 of 5", "wins": 5, "losses": 0, "p": 0.03125, "regressions": 0,
                 "eligible": True, "pairs": [{"run_id": "r1r2r3r4r5r6"}], "cost_control": 0.1, "cost_treatment": None}
    watched = {"after": 10, "before": 19, "p": 0.0031}
    # oldest decided first, each made before the one decided before it, so that the order by when they were made is
    # the other way round; but two pairs decided at once and told apart by when they were made alone, 22 made after
    # 21 and 17 after 18. In the first pair the one made later was also put in later and has the higher id and the
    # state that sorts later; in the second, each of those three runs the other way. So the order the rows were put
    # in, the id and the state, each either way, put one of the pairs wrong, and the oldest made first puts both
    mine = []
    for n in range(25):
        decided = now - 5000 + {22: 21, 18: 17}.get(n, n) * 100
        made_at = {22: now - 9000 + 100, 17: now - 9000 + 50}.get(n, now - 9000 - n)
        state = {21: "approved", 22: "rolled_back", 17: "refused", 18: "rolled_back"}.get(n, DECIDED[n % len(DECIDED)])
        value = {24: long_value, 23: out_of_order}.get(n, f"work {n}")
        result = {24: sandboxed, 21: watched}.get(n)
        mine.append(put(user_id, a["id"], state, value, decided_at=decided, created_at=made_at, result=result,
                        reason=f"why {n}", hypothesis=f"It helps {n}.", edits=1 + n % 3,
                        pid={21: "000000000021", 22: "000000000022", 17: "0000000000b1", 18: "0000000000b2"}.get(n),
                        kind="rollback" if n == 21 else "change",                     # 21 the rollback of 22
                        rolls_back="000000000022" if n == 21 else None))
    put(user_id, a["id"], "critic", "work open 1", created_at=now)                    # still on their way
    put(user_id, a["id"], "waiting", "work open 2", decided_at=now + 50)
    put(user_id, a["id"], "sandbox", "work open 3", decided_at=now + 60)
    for n in range(3):                                                              # another agent's
        put(user_id, b["id"], "rejected", f"label {n}", decided_at=now + 100 + n)
    put(other, a["id"], "rejected", "work, another account's", decided_at=now + 200)  # another account's
    put(other, "f" * 12, "rejected", "work, another account's own", decided_at=now + 201)
    put(user_id, None, "rejected", "Review it otherwise.", target="review_prompt", kind="meta",   # the account's own
        decided_at=now + 300)                                                       # review prompt, no agent's
    tried = tried_of(a)
    assert [t["id"] for t in tried] == [mine[n] for n in (24, 23, 22, 21, 20, 19, 17, 18, *range(16, 4, -1))]
    assert all(set(t) == FIELDS for t in tried)
    assert [(t["kind"], t["rolls_back"]) for t in tried] == [("rollback", mine[22]) if t["id"] == mine[21] else
                                                             ("change", None) for t in tried]
    assert "NOT-SHOWN" not in json.dumps(tried)                                     # no evidence, current or sandbox
    with store_now() as db:
        rows = {p["id"]: p for p in db.execute("SELECT * FROM ade_improve_proposals")}
    for t in tried:
        p = rows[t["id"]]
        assert (t["target"], t["name"], t["hypothesis"], t["state"], t["reason"], t["edits"], t["decided_at"]) == \
            (p["target"], p["name"], p["hypothesis"], p["state"], p["reason"], p["edits"], p["decided_at"])
    by_id = {t["id"]: t for t in tried}
    assert by_id[mine[24]]["proposed"] == json.dumps(long_value)[:600] + " [402 characters cut]"
    assert by_id[mine[23]]["proposed"] == '{"a": [2, {"c": 4, "d": 3}], "b": 1}'          # its keys sorted
    assert by_id[mine[20]]["proposed"] == '"work 20"'
    assert by_id[mine[24]]["result"] == {"wins": 5, "losses": 0, "p": 0.03125, "regressions": 0, "cost_control": 0.1}
    assert by_id[mine[21]]["result"] == watched and by_id[mine[22]]["result"] == {}
    from proofpane_open.ade import improve
    with store_now() as db:
        assert improve.review_body(db, user_id, row(a["id"]))["digest"]["tried"] == tried


def test_a_proposal_with_no_decided_at_takes_its_place_in_tried_by_when_it_was_made(client, owner, monkeypatch):
    """A change refused or pruned as it was taken (_propose), and a rollback waiting for the owner's tap (watch), have
    no decided_at: each takes its place by created_at among the others, not after every decided one, and is listed
    with its decided_at as it is, null."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner, brief="work")
    now = int(time.time())
    older = put(user_id, a["id"], "pruned", "work 1", created_at=now - 6000, pid="000000000001")
    decided = put(user_id, a["id"], "rejected", "work 2", decided_at=now - 5000, created_at=now - 9000)
    waiting = put(user_id, a["id"], "eligible", "work 4", created_at=now - 3000, kind="rollback")
    newer = put(user_id, a["id"], "refused", "work 3", created_at=now - 100, pid="000000000002")
    assert [(t["id"], t["decided_at"]) for t in tried_of(a)] == [(newer, None), (waiting, None),
                                                                  (decided, now - 5000), (older, None)]


def test_tried_cuts_a_value_past_600_characters_of_its_json_and_names_how_many(client, owner, monkeypatch):
    """The value as _canonical gives it, json.dumps with its keys sorted: 600 characters are shown whole, 601 lose one
    character, and a value of 700 accented characters is cut counting the six characters of each one's escape, as the
    brief's form counts them (the cut splits the hundredth)."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner, brief="work")
    now = int(time.time())
    whole = put(user_id, a["id"], "rejected", "x" * 598, decided_at=now - 30)            # 600 with its quotes
    one = put(user_id, a["id"], "rejected", "y" * 599, decided_at=now - 20)              # 601
    accented = put(user_id, a["id"], "rejected", "\u00e9" * 700, decided_at=now - 10)    # 4202, escaped
    shown = {t["id"]: t["proposed"] for t in tried_of(a)}
    assert shown[whole] == '"' + "x" * 598 + '"'
    assert shown[one] == '"' + "y" * 599 + " [1 character cut]"
    assert shown[accented] == '"' + "\\u00e9" * 99 + "\\u00e" + " [3602 characters cut]"


def test_tried_is_empty_for_an_agent_with_no_proposals_and_lists_a_null_value_as_null(client, owner, monkeypatch):
    """An agent the loop has proposed nothing for has `tried` in its digest, empty; and a rollback of a change that
    added a skill proposes the skill's value before it, null, which `tried` lists as null."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner, brief="work")
    with store_now() as db:
        assert improve.digest(db, row(a["id"])).get("tried") == []
    gone = put(user_id, a["id"], "rejected", None, target="skill", name="labels", decided_at=int(time.time()),
               kind="rollback")
    assert [(t["id"], t["proposed"]) for t in tried_of(a)] == [(gone, "null")]


def test_a_change_the_first_checks_refuse_or_prune_keeps_their_reason_though_tried_before(client, owner, monkeypatch):
    """The refusal of a change tried before comes after the three checks, and only for one on its way to the critic: a
    change that changes nothing, or bundles more than three edits, keeps that reason however often it was tried."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="a\nb\nc\nd\ne\nf\ng")
    put(user_id, a["id"], "pruned", "A\nb\nC\nd\nE\nf\nG", decided_at=now - 3000, created_at=now - 3600)  # a sandbox
    put(user_id, a["id"], "refused", "a\nb\nc\nd\ne\nf\ng", decided_at=now - 3000, created_at=now - 3600)  # the critic
    review_run(client, owner, user_id, a, [change("brief", "A\nb\nC\nd\nE\nf\nG"),
                                          change("brief", "a\nb\nc\nd\ne\nf\ng")], now)
    got = taken(user_id, now)
    assert ("brief", "pruned", "it bundles 4 edits; a proposal makes at most 3") in got
    assert ("brief", "refused", "it changes nothing") in got


@pytest.mark.parametrize("state,kind", [(state, "change") for state in REFUSING] + [("rejected", "rollback")])
def test_a_change_equal_to_one_already_refused_rejected_or_rolled_back_is_refused_before_its_critic(
        client, owner, monkeypatch, state, kind):
    """For each of the five states; and a rollback the owner rejected counts as well, a proposal of the agent's like
    any other: a later change to the value it proposed is the one the owner turned down."""
    from proofpane_open import store
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    earlier = put(user_id, a["id"], state, "Label the issues by area.", decided_at=now - 3600, created_at=now - 7200,
                  kind=kind)
    review = review_run(client, owner, user_id, a, [change("brief", "Label the issues by area.")], now)
    taken(user_id, now)
    calls = []

    def call(endpoint, method, path, body=None, timeout=None):
        calls.append(path)
        return {"specific": False, "cost_est_usd": 0.004}
    improve.criticise(store.connect, {"id": user_id}, "http://workspace", call, now)
    (new,) = [p for p in proposals().values() if p["review_run_id"] == review]
    assert (new["state"], new["reason"]) == ("refused", f"tried before: proposal {earlier}, {state}")
    assert calls == []                                                             # no critic asked
    with store_now() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_improve_sessions").fetchone()[0] == 0   # no session counted
    assert proposals()[earlier]["state"] == state


def test_any_one_of_the_earlier_turned_down_changes_refuses_its_twin_and_the_newest_is_named(client, owner,
                                                                                            monkeypatch):
    """With more than one earlier change of the same target turned down, a twin of an older one is refused as surely as
    a twin of the newest; and where earlier ones are the same change, the reason names the one decided last. Of the
    three turned-down Cs, the one decided last is the middle one by when each was made, by the order they were put in,
    by id and by state, so that each of those four, either way, names another, as does the oldest decided first."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    first = put(user_id, a["id"], "rejected", "Label the issues, A.", decided_at=now - 5000)
    put(user_id, a["id"], "rejected", "Label the issues, B.", decided_at=now - 4000)          # newer, another change
    put(user_id, a["id"], "not_eligible", "Label the issues, C.", decided_at=now - 1400, created_at=now - 1500,
        pid="0000000000c3")
    last = put(user_id, a["id"], "rejected", "Label the issues, C.", decided_at=now - 1000, created_at=now - 3000,
               pid="0000000000c2")
    put(user_id, a["id"], "rolled_back", "Label the issues, C.", decided_at=now - 2000, created_at=now - 9000,
        pid="0000000000c1")
    runs = [review_run(client, owner, user_id, a, [change("brief", value)], now)
            for value in ("Label the issues, A.", "Label the issues, C.")]
    taken(user_id, now)
    got = {p["review_run_id"]: (p["state"], p["reason"]) for p in proposals().values() if p["review_run_id"]}
    assert [got[r] for r in runs] == [("refused", f"tried before: proposal {first}, rejected"),
                                      ("refused", f"tried before: proposal {last}, rejected")]


def test_of_equal_changes_turned_down_at_one_time_the_one_made_last_is_named(client, owner, monkeypatch):
    """Two pairs of equal changes, each pair turned down in the same second and told apart by when they were made
    alone. In the first pair the one made later was put in first and has the lower id and the state that sorts first;
    in the second, each of those three runs the other way. So the order they were put in, the id and the state, each
    either way, name the other one of a pair, as does the oldest made first."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    at = now - 1000
    d_last = put(user_id, a["id"], "not_eligible", "Label the issues, D.", decided_at=at, created_at=at - 100,
                 pid="0000000000d1")
    put(user_id, a["id"], "rolled_back", "Label the issues, D.", decided_at=at, created_at=at - 900, pid="0000000000d2")
    put(user_id, a["id"], "not_eligible", "Label the issues, E.", decided_at=at, created_at=at - 900,
        pid="0000000000e1")
    e_last = put(user_id, a["id"], "rolled_back", "Label the issues, E.", decided_at=at, created_at=at - 100,
                 pid="0000000000e2")
    runs = [review_run(client, owner, user_id, a, [change("brief", value)], now)
            for value in ("Label the issues, D.", "Label the issues, E.")]
    taken(user_id, now)
    got = {p["review_run_id"]: (p["state"], p["reason"]) for p in proposals().values() if p["review_run_id"]}
    assert [got[run] for run in runs] == [("refused", f"tried before: proposal {d_last}, not_eligible"),
                                          ("refused", f"tried before: proposal {e_last}, rolled_back")]


def test_an_equal_approval_since_lifts_the_refusal_and_one_before_does_not(client, owner, monkeypatch):
    """The owner's approval of a change lifts an earlier turn-down of an equal one, so its twin goes on to the
    critic; a turn-down after the approval stands, and the reason names it. Of the agent's equal proposals turned down
    or approved past their intake, the one decided last decides; a rolled-back proposal counts by when the rollback that
    undid it was approved, since it keeps the decided_at of its own approval. An equal proposal approved after one was
    rejected (V); a rollback approved that restored a value found not eligible before (R); one approved before an equal
    one was found not eligible (W); one rejected, approved, then rejected again (T); two equal proposals approved, the
    first of them then rolled back (X); and a proposal rolled back after a rollback of another change had restored its
    value (Y)."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    put(user_id, a["id"], "rejected", "Label the issues, V.", decided_at=now - 3000, created_at=now - 3600)
    put(user_id, a["id"], "approved", "Label the issues, V.", decided_at=now - 2000, created_at=now - 3500)
    put(user_id, a["id"], "not_eligible", "Label the issues, R.", decided_at=now - 3000, created_at=now - 3600)
    put(user_id, a["id"], "approved", "Label the issues, R.", decided_at=now - 2000, created_at=now - 2500,
        kind="rollback", rolls_back="f" * 12)
    put(user_id, a["id"], "approved", "Label the issues, W.", decided_at=now - 3000, created_at=now - 3600)
    wn = put(user_id, a["id"], "not_eligible", "Label the issues, W.", decided_at=now - 2000, created_at=now - 2500)
    put(user_id, a["id"], "rejected", "Label the issues, T.", decided_at=now - 3000, created_at=now - 3600)
    put(user_id, a["id"], "approved", "Label the issues, T.", decided_at=now - 2000, created_at=now - 2500)
    tr = put(user_id, a["id"], "rejected", "Label the issues, T.", decided_at=now - 1000, created_at=now - 1500)
    x1 = put(user_id, a["id"], "rolled_back", "Label the issues, X.", decided_at=now - 5000, created_at=now - 6000)
    put(user_id, a["id"], "approved", "Label the issues, X.", decided_at=now - 4000, created_at=now - 5900)
    put(user_id, a["id"], "approved", "Label the issues.", decided_at=now - 1000, created_at=now - 1500,
        kind="rollback", rolls_back=x1)                                     # the rollback that undid x1
    y1 = put(user_id, a["id"], "rolled_back", "Label the issues, Y.", decided_at=now - 5000, created_at=now - 6000)
    z1 = put(user_id, a["id"], "rolled_back", "Label the issues, Z.", decided_at=now - 4000, created_at=now - 4500)
    put(user_id, a["id"], "approved", "Label the issues, Y.", decided_at=now - 3000, created_at=now - 3500,
        kind="rollback", rolls_back=z1)                                     # the rollback of Z, restoring Y
    put(user_id, a["id"], "approved", "Label the issues.", decided_at=now - 500, created_at=now - 800,
        kind="rollback", rolls_back=y1)                                     # the rollback that undid y1
    runs = [review_run(client, owner, user_id, a, [change("brief", f"Label the issues, {n}.")], now)
            for n in "VRWTXY"]
    taken(user_id, now)
    got = {p["review_run_id"]: (p["state"], p["reason"]) for p in proposals().values() if p["review_run_id"]}
    assert [got[run] for run in runs] == [("critic", None), ("critic", None),
                                          ("refused", f"tried before: proposal {wn}, not_eligible"),
                                          ("refused", f"tried before: proposal {tr}, rejected"),
                                          ("refused", f"tried before: proposal {x1}, rolled_back"),
                                          ("refused", f"tried before: proposal {y1}, rolled_back")]


def test_another_agent_s_approval_a_rollback_made_before_an_approval_and_a_tie_are_read_as_the_rule_says(
        client, owner, monkeypatch):
    """The rule's other cases: another agent's approval, and a row of another account's, after
    the agent's own rejection does not lift it (P); a rollback made before an equal approval and approved after it
    dates the change it undid by its approval, not by when it was made (Q); of two equal turn-downs, the rolled-back
    one is named when the rollback that undid it was approved after the other was decided, though its own decided_at
    is older (M); and of an approval and a turn-down decided in the same second, the one made last decides: an
    approval made after an equal rejection (U), and a finding of not eligible made after an equal approval (S), the
    lower id of each pair on the one made first, so that the id alone would choose the other."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    other = me(client, signed_up(client, "second-admin@example.com"))
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    b = agent(client, owner, name="Labeler", brief="label")
    pr = put(user_id, a["id"], "rejected", "Label the issues, P.", decided_at=now - 3000, created_at=now - 3600)
    put(user_id, b["id"], "approved", "Label the issues, P.", decided_at=now - 2000, created_at=now - 2500)
    put(other, a["id"], "approved", "Label the issues, P.", decided_at=now - 1500, created_at=now - 1800)
    q1 = put(user_id, a["id"], "rolled_back", "Label the issues, Q.", decided_at=now - 6000, created_at=now - 6500)
    put(user_id, a["id"], "approved", "Label the issues.", decided_at=now - 1000, created_at=now - 3500,
        kind="rollback", rolls_back=q1)                                     # made before the approval below
    put(user_id, a["id"], "approved", "Label the issues, Q.", decided_at=now - 2000, created_at=now - 2500)
    m1 = put(user_id, a["id"], "rolled_back", "Label the issues, M.", decided_at=now - 5000, created_at=now - 5600)
    put(user_id, a["id"], "approved", "Label the issues.", decided_at=now - 1000, created_at=now - 1200,
        kind="rollback", rolls_back=m1)
    put(user_id, a["id"], "rejected", "Label the issues, M.", decided_at=now - 3000, created_at=now - 5500)
    put(user_id, a["id"], "rejected", "Label the issues, U.", decided_at=now - 2500, created_at=now - 2700,
        pid="0000000000c1")
    put(user_id, a["id"], "approved", "Label the issues, U.", decided_at=now - 2500, created_at=now - 2600,
        pid="0000000000c2")
    put(user_id, a["id"], "approved", "Label the issues, S.", decided_at=now - 2500, created_at=now - 2700,
        pid="0000000000c3")
    sn = put(user_id, a["id"], "not_eligible", "Label the issues, S.", decided_at=now - 2500, created_at=now - 2600,
             pid="0000000000c4")
    runs = [review_run(client, owner, user_id, a, [change("brief", f"Label the issues, {n}.")], now) for n in "PQMUS"]
    taken(user_id, now)
    got = {p["review_run_id"]: (p["state"], p["reason"]) for p in proposals().values() if p["review_run_id"]}
    assert [got[run] for run in runs] == [("refused", f"tried before: proposal {pr}, rejected"),
                                          ("refused", f"tried before: proposal {q1}, rolled_back"),
                                          ("refused", f"tried before: proposal {m1}, rolled_back"),
                                          ("critic", None),
                                          ("refused", f"tried before: proposal {sn}, not_eligible")]


def test_a_twin_that_is_not_one_of_the_agent_s_own_refusals_goes_on_to_the_critic(client, owner, monkeypatch):
    quiet(monkeypatch)
    user_id = me(client, owner)
    other = me(client, signed_up(client, "second-admin@example.com"))
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    b = agent(client, owner, name="Labeler", brief="Sort the issues.")
    before = now - 3600
    twins = []
    for state in ("expired", "not_evaluated", "approved", "eligible", "critic", "waiting", "sandbox"):  # not refusals
        put(user_id, a["id"], state, f"Label the issues, {state}.", decided_at=before)
        twins.append(change("brief", f"Label the issues, {state}."))
    put(user_id, b["id"], "rejected", "Label the issues, the other agent's.", decided_at=before)
    twins.append(change("brief", "Label the issues, the other agent's."))
    put(other, a["id"], "rejected", "Label the issues, the other account's.", decided_at=before)
    twins.append(change("brief", "Label the issues, the other account's."))
    put(user_id, a["id"], "rejected", "Label the issues, another target.", target="model", decided_at=before)
    twins.append(change("brief", "Label the issues, another target."))
    put(user_id, a["id"], "rejected", SKILL, target="skill", name="labels", decided_at=before)
    twins.append(change("skill", SKILL, name="areas"))
    put(user_id, a["id"], "rejected", "Label the issues, by area.", decided_at=before)
    twins.append(change("brief", "Label the issues, by areas."))                    # one character more
    runs = [review_run(client, owner, user_id, a, [twin], now) for twin in twins]
    taken(user_id, now)
    got = {p["review_run_id"]: (p["state"], p["reason"]) for p in proposals().values() if p["review_run_id"]}
    assert [got[r] for r in runs] == [("critic", None)] * len(twins)


def test_a_change_refused_or_pruned_as_it_was_taken_is_no_reason_to_refuse_its_twin_later(client, owner, monkeypatch):
    """One refused or pruned as it was taken (it changed nothing, or bundled too many edits) was weighed against the
    value then current, never on its own; its twin reaches the check only once that value has changed, and then goes
    on to the critic. Both earlier decisions are the loop's own, made by take from a review and not written by hand, so
    the test fails if _propose ever gives them a decided_at."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="a\nb\nc\nd\ne\nf\ng")
    with store_now() as db:                               # the effort high, so that proposing it changes nothing
        db.execute("UPDATE ade_agents SET chat_settings = ? WHERE id = ?", (json.dumps({"effort": "high"}), a["id"]))
    review_run(client, owner, user_id, a, [change("effort", "high"), change("brief", "A\nb\nC\nd\nE\nf\nG")],
               now - 3600)
    assert {("effort", "refused", "it changes nothing"), ("brief", "pruned",
            "it bundles 4 edits; a proposal makes at most 3")} <= set(taken(user_id, now - 3600))
    with store_now() as db:                               # since then: no effort set, and a brief two edits away
        db.execute("UPDATE ade_agents SET brief = ?, chat_settings = ? WHERE id = ?",
                   ("A\nb\nC\nd\ne\nf\ng", json.dumps({}), a["id"]))
    runs = [review_run(client, owner, user_id, a, [twin], now)
            for twin in (change("effort", "high"), change("brief", "A\nb\nC\nd\nE\nf\nG"))]
    taken(user_id, now)
    got = {p["review_run_id"]: (p["state"], p["reason"]) for p in proposals().values() if p["review_run_id"]}
    assert [got[r] for r in runs] == [("critic", None), ("critic", None)]


def test_a_change_refused_as_tried_before_keeps_no_decided_at_and_its_twin_names_the_same_decision(client, owner,
                                                                                                   monkeypatch):
    """The third decision made as a change is taken: one refused as tried before is written with no decided_at, as the
    two above are, so that a later twin is refused naming the decision both repeat, not the first twin's refusal."""
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="Label the issues.")
    earlier = put(user_id, a["id"], "rejected", "Label the issues by area.", decided_at=now - 3600)
    first = review_run(client, owner, user_id, a, [change("brief", "Label the issues by area.")], now - 600)
    taken(user_id, now - 600)
    second = review_run(client, owner, user_id, a, [change("brief", "Label the issues by area.")], now)
    taken(user_id, now)
    got = {p["review_run_id"]: (p["state"], p["reason"], p["decided_at"]) for p in proposals().values()
           if p["review_run_id"]}
    assert [got[r] for r in (first, second)] == [("refused", f"tried before: proposal {earlier}, rejected", None)] * 2


def test_an_equal_value_whose_keys_come_in_another_order_is_refused(client, owner, monkeypatch):
    """No target takes an object today (shapes.check_change), so the change is put to the loop's intake directly. The
    stored value has its inner keys out of order and the proposed one its outer keys, so that sorting one side only, or
    one level only, tells them apart; and a value with characters outside ASCII is compared as the stored one is kept,
    escaped."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner)
    earlier = put(user_id, a["id"], "rejected", {"a": [3], "b": {"y": 1, "x": 2}}, target="model", decided_at=now - 60)
    with store_now() as db:
        pid = improve._propose(db, user_id, row(a["id"]), None, {"target": "model", "name": None, "value": {
            "b": {"x": 2, "y": 1}, "a": [3]}, "hypothesis": "It helps."}, now)
    assert (proposals()[pid]["state"], proposals()[pid]["reason"]) == \
        ("refused", f"tried before: proposal {earlier}, rejected")
    accented = "Label the issues by r\u00e9gion and \u0394."
    word = put(user_id, a["id"], "rejected", accented, decided_at=now - 30)
    with store_now() as db:
        pid = improve._propose(db, user_id, row(a["id"]), None, {"target": "brief", "name": None, "value": accented,
                                                                 "hypothesis": "It helps."}, now)
    assert (proposals()[pid]["state"], proposals()[pid]["reason"]) == \
        ("refused", f"tried before: proposal {word}, rejected")


def test_a_change_to_the_review_prompt_is_not_held_to_what_was_tried(client, owner, monkeypatch):
    """A change to the review prompt (kind meta) is not checked: one equal to a review-prompt change the owner rejected,
    41 days ago so that the month's limit does not refuse it, goes on as at base, to the owner's tap."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner)
    prompt = improve.REVIEW_PROMPT + "\nName the runs each change is drawn from."
    put(user_id, None, "rejected", prompt, target="review_prompt", kind="meta", decided_at=now - 40 * 86400,
        created_at=now - 41 * 86400)
    with store_now() as db:
        pid = improve._propose(db, user_id, row(a["id"]), None, {"target": "review_prompt", "name": None,
                                                                 "value": prompt, "hypothesis": "It helps."}, now)
    p = proposals()[pid]
    assert (p["state"], p["reason"], p["kind"], p["agent_id"]) == ("eligible", None, "meta", None)


def test_the_review_prompt_says_what_tried_is_for_and_an_account_s_own_prompt_is_kept(client, owner, monkeypatch):
    """The sentence is added after the prompt's last clause, which still holds for the brief's versions the owner made
    and undid, which `tried` does not list, and now names the exception the sentence names."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    assert improve.REVIEW_PROMPT.endswith(BASE_CLAUSE + " " + SENTENCE)
    with store_now() as db:
        assert improve.review_body(db, user_id, row(a["id"]))["prompt"] == improve.REVIEW_PROMPT
        db.execute("INSERT INTO ade_improve_prompts (user_id, version, prompt, proposal_id, created_at) VALUES "
                   "(?, 2, ?, NULL, ?)", (user_id, "Review it my way.", int(time.time())))
        assert improve.review_body(db, user_id, row(a["id"]))["prompt"] == "Review it my way."


def test_the_workspace_hands_tried_to_the_review_session_as_the_app_gave_it(tmp_path):
    """What the workspace keeps of a review's trigger (improve.check_review), the digest.json its session reads, written
    from what it kept (agents.Agents._write_review, given the parts of the workspace it reads), and the words after
    the account's prompt that say what digest.json holds."""
    from proofpane_workspace import improve as improving
    from proofpane_workspace.agents import Agents
    from proofpane_workspace.workspace import UPLOADS
    tried = [{"id": "a1a2a3a4a5a6", "target": "brief", "name": None, "hypothesis": "It helps.", "state": "rejected",
              "reason": "not now", "edits": 1, "result": {"wins": 5}, "decided_at": 1, "proposed": '"work"'}]
    digest = {"runs": [], "brief_versions": [], "current": {"brief": "work"}, "tried": tried}
    kept = improving.check_review({"prompt": "Review it.", "digest": digest})
    assert kept["digest"]["tried"] == tried
    review_run_id = "b1b2b3b4b5b6"

    class Workspace:
        _index = {review_run_id: "c1c2c3c4c5c6"}
        _loops = {review_run_id}

        def _loop_file(self, run_id, name):                   # as kept beside the run: JSON, read back
            return json.loads(json.dumps(kept)) if (run_id, name) == (review_run_id, "review.json") else None

        def _kept_file(self, run_id, name):
            return None

        def _last_words(self, run_id):
            return ""
    work = tmp_path / "work"
    work.mkdir()
    Agents._write_review(Workspace(), review_run_id, str(work))
    with open(work / UPLOADS / improving.REVIEW_DIR / "digest.json", encoding="utf-8") as fh:
        assert json.load(fh)["tried"] == tried
    assert "the loop's latest decided proposals for it and what became of each, under the key tried" in \
        improving.REVIEW_TAIL
    assert "under the key tried, a value null where the app found a run's own input in it word for word" in \
        improving.REVIEW_TAIL
    assert "and what the digest left out of them, under the key left_out" in improving.REVIEW_TAIL


def test_tried_says_why_a_change_was_rolled_back_its_rollback_naming_it(client, owner, monkeypatch):
    """A change promoted, watched and undone by a rollback the owner approved: `tried` lists the rollback first, its
    kind `rollback`, the id of the change it undid, why (its hypothesis, the watch's passes before and after) and the
    watch's numbers; then the change, its kind `change`, undoing nothing, and its state `rolled_back`."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work BAD")
    judged_runs(client, owner, user_id, a, [True] * 19 + [False], now - 5000)
    promoted = eligible_proposal(user_id, a, "brief", "work GOOD", now - 4000, current="work BAD")
    client.post(f"{A}/improvements/{promoted}/approve", headers=owner)
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET approved_at = ? WHERE id = ?", (now - 3000, promoted))
    judged_runs(client, owner, user_id, a, [True] * 10 + [False] * 10, now - 2000)
    with store_now() as db:
        (rollback,) = improve.watch(db, user_id, now)
    r = client.post(f"{A}/improvements/{rollback}/approve", headers=owner)
    assert r.status_code == 200, r.text
    watch = improve.stats.WATCH
    shown = [{k: t.get(k) for k in ("id", "kind", "rolls_back", "state", "hypothesis")} for t in tried_of(a)]
    assert shown == [
        {"id": rollback, "kind": "rollback", "rolls_back": promoted, "state": "approved",
         "hypothesis": f"Undo it: its first {watch} graded runs passed 10 times, the {watch} before it 19."},
        {"id": promoted, "kind": "change", "rolls_back": None, "state": "rolled_back", "hypothesis": "h"}]
    assert {k for k in tried_of(a)[0]["result"]} == {"after", "before", "p"}


def test_the_digest_lists_the_brief_s_newest_twenty_versions_in_order(client, owner, monkeypatch):
    """An agent whose brief has 25 versions more than it was made with: the digest lists the newest 20, oldest
    first as before, each with its number and value."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner, brief="work")
    now = int(time.time())
    with store_now() as db:
        for n in range(25):
            improve.version(db, a["id"], user_id, "brief", f"work {n}", "owner", now - 100 + n)
        every = [r[0] for r in db.execute("SELECT version FROM ade_agent_versions WHERE agent_id = ? AND target = "
                                          "'brief' ORDER BY version", (a["id"],))]
        got = improve.digest(db, row(a["id"]))
    listed = got["brief_versions"]
    assert len(every) >= 25
    assert [(v["version"], v["brief"]) for v in listed] == [(n, f"work {n - every[-1] + 24}") for n in every[-20:]]
    assert got.get("left_out", {}).get("brief_versions") == len(every) - 20       # the older ones, counted


def test_the_digest_shows_at_most_twenty_of_the_agent_s_skills_by_name(client, owner, monkeypatch):
    """An agent with 25 skills (as an older release's loop could leave one, before a new skill was refused while an
    agent holds 20): the digest's agent shows the first 20 by name, each as it is."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner, brief="work")
    now = int(time.time())
    names = [f"skill-{n:02d}" for n in range(25)]
    with store_now() as db:
        for name in reversed(names):                                            # put in against their order
            db.execute("INSERT INTO ade_agent_skills (agent_id, user_id, name, content, updated_at) VALUES (?,?,?,?,?)",
                       (a["id"], user_id, name, SKILL.replace("Label each one.", f"Label each one, {name}."), now))
        got = improve.digest(db, row(a["id"]))
    shown = got["current"]["skills"]
    assert list(shown) == names[:20]
    assert shown[names[0]] == SKILL.replace("Label each one.", f"Label each one, {names[0]}.")
    assert got.get("left_out", {}).get("skills") == names[20:]                # the rest, named


def test_the_manual_states_the_digest_s_bounds_as_the_code_has_them(client, owner, monkeypatch):
    """The manual's paragraph on the review states how many brief versions, skills and decided proposals the digest
    holds, how much of a listed value, and how long the digest may be; each number is read back here, so neither can
    move alone."""
    from proofpane_open.ade import improve
    manual_or_skip()
    readme = file_text("MANUAL.md")
    start = readme.index("The review is a run of its own")
    review = readme[start:readme.index("propose_change", start)]

    def stated(pattern):
        found = re.findall(pattern, review)
        assert len(found) == 1, (pattern, found)
        return int(found[0].replace(",", ""))
    assert stated(r"the newest (\d+) versions of its brief") == improve.DIGEST_VERSIONS
    assert stated(r"at most (\d+) of its skills") == improve.DIGEST_SKILLS
    assert stated(r"its last (\d+) decided proposals") == improve.DIGEST_TRIED
    assert stated(r"cut at (\d+) characters of that text") == improve.TRIED_CUT
    assert stated(r"at most ([\d,]+) characters as the workspace measures it") == getattr(improve, "DIGEST_MAX", None)


def test_past_what_the_workspace_takes_the_digest_leaves_out_the_oldest_versions_then_the_last_skills_and_says_so(
        client, owner, monkeypatch):
    """Past the counts: the workspace refuses a digest longer than its MAX_DIGEST
    as check_review measures it (json.dumps, which writes a character outside ASCII as six characters, or twelve
    outside the BMP), so the app leaves out the oldest of the brief's versions first, then the last of the skills by
    name, no more than it must, and `left_out` says how many versions and which skills. An agent with 25 versions and
    25 skills, each at its largest: in ASCII the counts alone cut it (A); in a script of the BMP the oldest of the 20
    versions go too (B); outside the BMP, with 20 decided proposals at their largest in `tried`, every version goes and
    then the last skills (C). check_review keeps each digest, and one more of what went last would not have fitted."""
    from proofpane_open.ade import improve, shapes
    from proofpane_workspace import improve as improving
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    assert getattr(improve, "DIGEST_MAX", None) == improving.MAX_DIGEST
    names = [f"skill-{n:02d}" for n in range(25)]
    seen = {}
    for case, ch in (("A", "a"), ("B", "\u4e2d"), ("C", "\U0001F600")):
        a = agent(client, owner, name=f"Wide {case}", brief="work")
        brief = ch * shapes.MAX_BRIEF
        skill = ch * (shapes.MAX_SKILL // len(ch.encode("utf-8")))
        with store_now() as db:
            for n in range(25):
                improve.version(db, a["id"], user_id, "brief", brief[:-2] + f"{n:02d}", "owner", now - 100 + n)
            db.execute("UPDATE ade_agents SET brief = ? WHERE id = ?", (brief, a["id"]))
            for name in names:
                db.execute("INSERT INTO ade_agent_skills (agent_id, user_id, name, content, updated_at) "
                           "VALUES (?,?,?,?,?)", (a["id"], user_id, name, skill[:-2] + name[-2:], now))
        if case == "C":
            for n in range(20):
                put(user_id, a["id"], "rejected", ch * 1000, decided_at=now - 500 + n, reason=ch * 300,
                    hypothesis=ch * 300)
        with store_now() as db:
            every = [r[0] for r in db.execute("SELECT version FROM ade_agent_versions WHERE agent_id = ? AND target = "
                                              "'brief' ORDER BY version", (a["id"],))]
            got = improve.review_body(db, user_id, row(a["id"]))["digest"]
            listed, shown = [v["version"] for v in got["brief_versions"]], list(got["current"]["skills"])
            assert len(json.dumps(got)) <= improving.MAX_DIGEST
            assert improving.check_review({"prompt": "Review it.", "digest": got})["digest"] == got
            assert listed == every[len(every) - len(listed):] and shown == names[:len(shown)]
            assert got.get("left_out") == {"brief_versions": len(every) - len(listed), "skills": names[len(shown):]}
            more = json.loads(json.dumps(got))                  # one more of what went last, put back
            if len(shown) < 20:
                more["current"]["skills"][names[len(shown)]] = skill[:-2] + names[len(shown)][-2:]
                more["left_out"]["skills"] = names[len(shown) + 1:]
            elif len(listed) < 20:
                v, value, author, at = db.execute("SELECT version, value, author, created_at FROM ade_agent_versions "
                                                  "WHERE agent_id = ? AND target = 'brief' AND version = ?",
                                                  (a["id"], every[len(every) - len(listed) - 1])).fetchone()
                more["brief_versions"].insert(0, {"version": v, "brief": json.loads(value), "author": author, "at": at,
                                                  "numbers": None})
                more["left_out"]["brief_versions"] -= 1
        seen[case] = (len(listed), len(shown), len(json.dumps(more)) > improving.MAX_DIGEST)
    assert seen == {"A": (20, 20, False), "B": (seen["B"][0], 20, True), "C": (0, seen["C"][1], True)}
    assert 0 < seen["B"][0] < 20 and 0 < seen["C"][1] < 20


def test_a_value_refused_for_naming_a_run_s_input_word_for_word_is_listed_without_it_and_so_is_its_twin(
        client, owner, monkeypatch):
    """A change refused because a run's own prompt is in it word for word is listed in `tried` with that reason and
    no value; so is the equal change a later review proposes, refused as it is taken as tried before, naming it,
    and an equal value proposed for another target, here a skill the owner rejected; so the
    prompt reaches no later review through `tried`. A change the critic refused keeps its value."""
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
                                          change("brief", "Label the issues by SPECIFIC area.", runs=[asked])], now)
    taken(user_id, now)

    def call(endpoint, method, path, body=None, timeout=None):
        return {"specific": True, "why": "it names one", "cost_est_usd": 0.004}
    improve.criticise(store.connect, {"id": user_id}, "http://workspace", call, now)
    word = next(p["id"] for p in proposals().values() if p["reason"] == "it names a run's own input word for word")
    review_run(client, owner, user_id, a, [change("brief", "Label the issues. Label issue 4242 as bug")], now + 60)
    taken(user_id, now + 60)
    put(user_id, a["id"], "rejected", "Label the issues. Label issue 4242 as bug", target="skill", name="labels",
        reason="not as a skill", decided_at=now + 90)
    listed = {t["reason"]: t for t in tried_of(a)}
    assert set(listed) == {"it names a run's own input word for word", "the critic: it names one",
                           f"tried before: proposal {word}, refused", "not as a skill"}
    assert [(listed[r]["state"], listed[r]["proposed"]) for r in ("it names a run's own input word for word",
                                                                  f"tried before: proposal {word}, refused",
                                                                  "not as a skill")] == \
        [("refused", None), ("refused", None), ("rejected", None)]
    assert "4242" not in json.dumps(tried_of(a))
    assert listed["the critic: it names one"]["proposed"] == '"Label the issues by SPECIFIC area."'
