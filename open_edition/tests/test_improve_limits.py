"""The improve loop holds an agent to what its workspace takes (ade/improve.py), the items 1 and 2 below: (1) a change
that adds a skill the agent does not hold is refused while the agent holds MAX_SKILLS (20), the most the workspace takes
for an agent, as it is taken (a run's input in it word for word refused so first), as its sandbox is planned and again
at the owner's approval, its reason naming the limit, while a change to a skill it holds and one that takes a skill away
are taken as before; and (2) no review is queued for an agent whose digest would still be longer than DIGEST_MAX after
every cut the digest makes: no session counted, no cooldown started, the reason kept on the agent's record
(`review_skipped`) and measured again the next day, the day's review going to the next agent by weight."""

import json
import re
import time

from test_agents_app import A, agent, owner, local  # noqa: F401  (fixtures)
from test_agent_quality_app import made, me, quiet
from test_agent_improve_app import (DAY, change, passing_run, plan, proposals, review_now, review_run, row, signal_run,
                                    store_now, taken, turn_on)
from skills_readme import text as file_text
from conftest import manual_or_skip

FULL = "it adds a skill to an agent holding {n}; an agent holds at most 20"
FULL_AT_APPROVAL = "It adds a skill to an agent holding {n}; an agent holds at most 20."
SKIPPED = ("its digest would be {size:,} characters with every brief version and skill it may leave out left out; "
           "the workspace takes at most 1,048,576")


def skill(word):
    return f"---\ndescription: {word} the new issues by area.\n---\n{word} each one.\n"


def hold(user_id, a, n):
    """The agent given n skills, as approved changes would have made them."""
    names = [f"skill-{i:02d}" for i in range(n)]
    with store_now() as db:
        for name in names:
            db.execute("INSERT INTO ade_agent_skills (agent_id, user_id, name, content, updated_at) VALUES (?,?,?,?,?)",
                       (a["id"], user_id, name, skill(name), int(time.time())))
    return names


def held(client, h, a):
    return [s["name"] for s in client.get(f"{A}/{a['id']}", headers=h).json()["skills"]]


def approve(client, h, pid):
    return client.post(f"{A}/improvements/{pid}/approve", headers=h)


def test_a_new_skill_for_an_agent_holding_twenty_is_refused_as_it_is_taken_and_an_edit_of_one_it_holds_is_not(
        client, owner, monkeypatch):
    """Item 1 at intake: the agent holds 20 skills; a review proposes a skill it does not hold and a new text for one
    it holds. The first is refused before any critic, its reason naming the limit, with no time of decision, as every
    refusal made as a change is taken (one of the count, not of the change, so no reason to refuse its twin once there
    is room); the second goes on to the critic as before. An agent holding 21, as an older release's loop could leave
    one, is refused a new skill too, and its reason says 21."""
    from proofpane_open.ade import shapes
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    names = hold(user_id, a, shapes.MAX_SKILLS)
    review_run(client, owner, user_id, a, [change("skill", skill("Sort"), name="sorting"),
                                          change("skill", skill("Tag"), name=names[0])], now)
    over = agent(client, owner, name="Over", brief="work")
    hold(user_id, over, shapes.MAX_SKILLS + 1)
    review_run(client, owner, user_id, over, [change("skill", skill("Sort"), name="sorting")], now)
    taken(user_id, now)
    got = {(p["agent_id"], p["name"]): p for p in proposals().values()}
    new, edit, past = got[(a["id"], "sorting")], got[(a["id"], names[0])], got[(over["id"], "sorting")]
    assert (new["state"], new["reason"], new["decided_at"]) == ("refused", FULL.format(n=20), None)
    assert (edit["state"], edit["reason"]) == ("critic", None)
    assert (past["state"], past["reason"]) == ("refused", FULL.format(n=21))


def test_two_new_skills_from_one_review_to_an_agent_holding_nineteen_cannot_both_land(client, owner, monkeypatch):
    """Item 1 at approval: one review proposes two skills to an agent holding 19; both are taken, as it holds 19 when
    each is, and both pass (made eligible here). The first approved lands, the agent's 20th; the second's approval is
    refused, its detail naming the limit: no skill, no version, and the proposal is
    refused with the limit's reason, no longer left eligible. A rollback that takes a skill away is approved at 20 as
    before; the second, refused, is not approved after it."""
    from proofpane_open.ade import shapes
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    hold(user_id, a, shapes.MAX_SKILLS - 1)
    review_run(client, owner, user_id, a, [change("skill", skill("Sort"), name="sorting"),
                                          change("skill", skill("Tag"), name="tagging")], now)
    taken(user_id, now)
    got = {p["name"]: p for p in proposals().values()}
    assert [got[n]["state"] for n in ("sorting", "tagging")] == ["critic", "critic"]
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET state = 'eligible'")          # past the critic and the sandbox
    first = approve(client, owner, got["sorting"]["id"])
    assert first.status_code == 200 and first.json()["state"] == "approved", first.text
    second = approve(client, owner, got["tagging"]["id"])
    assert (second.status_code, second.json().get("detail")) == (409, FULL_AT_APPROVAL.format(n=20)), second.text
    names = held(client, owner, a)
    assert len(names) == 20 and "sorting" in names and "tagging" not in names
    second_now = proposals()[got["tagging"]["id"]]
    assert (second_now["state"], second_now["reason"]) == ("refused", FULL.format(n=20))
    with store_now() as db:
        made = [v["target"] for v in db.execute("SELECT target FROM ade_agent_versions WHERE author = 'loop'")]
    assert made == ["skill:sorting"]
    undo = "r1r2r3r4r5r6"
    with store_now() as db:                         # the rollback watch() makes of the first: its skill taken away
        db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, kind, target, name, current, proposed, "
                   "hypothesis, evidence, edits, state, created_at, rolls_back) VALUES (?, ?, ?, 'rollback', 'skill', "
                   "'sorting', ?, 'null', 'Undo it.', '{}', 1, 'eligible', ?, ?)",
                   (undo, user_id, a["id"], json.dumps(skill("Sort")), now, got["sorting"]["id"]))
    assert approve(client, owner, undo).status_code == 200
    assert len(held(client, owner, a)) == 19 and "sorting" not in held(client, owner, a)
    third = approve(client, owner, got["tagging"]["id"])
    assert (third.status_code, third.json().get("detail")) == \
        (409, "Only a proposal that passed its sandbox, or a rollback, is approved."), third.text
    names = held(client, owner, a)
    assert len(names) == 19 and "tagging" not in names


def test_at_approval_what_the_agent_holds_then_decides_whether_a_change_adds_a_skill(client, owner, monkeypatch):
    """What the agent holds when the owner taps decides, not what it held when the change was taken: a change taken as
    a new skill is a change to one it holds once an equal-named skill has landed, and lands at 20; a change taken as a
    new text for one it holds adds it again once the owner has taken it away, and is refused at 20. So is a rollback
    that would put that skill back; one that takes it away again adds none, and is
    approved at 20."""
    from proofpane_open.ade import shapes
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    names = hold(user_id, a, shapes.MAX_SKILLS - 1)
    review_run(client, owner, user_id, a, [change("skill", skill("Sort"), name="sorting"),
                                          change("skill", skill("Tag"), name=names[0])], now)
    review_run(client, owner, user_id, a, [change("skill", skill("Order"), name="sorting"),
                                          change("skill", skill("Mark"), name="marking")], now)
    taken(user_id, now)
    got = {(p["name"], json.loads(p["proposed"])): p for p in proposals().values()}
    first, again = got[("sorting", skill("Sort"))], got[("sorting", skill("Order"))]
    edit, other = got[(names[0], skill("Tag"))], got[("marking", skill("Mark"))]
    assert [p["state"] for p in (first, again, edit, other)] == ["critic"] * 4
    with store_now() as db:
        db.execute("UPDATE ade_improve_proposals SET state = 'eligible'")
    assert approve(client, owner, first["id"]).status_code == 200                       # the 20th
    landed = approve(client, owner, again["id"])                                         # now a change to one it holds
    assert landed.status_code == 200, landed.text
    shown = {s["name"]: s["content"] for s in client.get(f"{A}/{a['id']}", headers=owner).json()["skills"]}
    assert len(shown) == 20 and shown["sorting"] == skill("Order")
    assert client.delete(f"{A}/{a['id']}/skills/{names[0]}", headers=owner).status_code == 200
    assert approve(client, owner, other["id"]).status_code == 200                       # 20 again, without names[0]
    refused = approve(client, owner, edit["id"])                                         # it would add names[0] back
    assert (refused.status_code, refused.json().get("detail")) == (409, FULL_AT_APPROVAL.format(n=20)), refused.text
    names_now = held(client, owner, a)
    assert len(names_now) == 20 and names[0] not in names_now
    back, gone = "b1b2b3b4b5b6", "g1g2g3g4g5g6"     # two rollbacks watch() could make for the skill the owner took
    with store_now() as db:                         # away: one puts it back, the other takes it away again
        for pid, value in ((back, skill(names[0])), (gone, None)):
            db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, kind, target, name, current, "
                       "proposed, hypothesis, evidence, edits, state, created_at) VALUES (?, ?, ?, 'rollback', "
                       "'skill', ?, ?, ?, 'Undo it.', '{}', 1, 'eligible', ?)",
                       (pid, user_id, a["id"], names[0], json.dumps(skill("Tag")), json.dumps(value), now))
    refused = approve(client, owner, back)                                               # a rollback adds one too
    assert (refused.status_code, refused.json().get("detail")) == (409, FULL_AT_APPROVAL.format(n=20)), refused.text
    rolled = proposals()[back]                      # and is refused for good: watch() proposes one rollback a change
    assert (rolled["state"], rolled["reason"], rolled["decided_at"]) == ("refused", FULL.format(n=20), None)
    assert approve(client, owner, back).status_code == 409
    taken_away = approve(client, owner, gone)                                            # it adds none
    assert taken_away.status_code == 200 and taken_away.json()["state"] == "approved", taken_away.text
    names_now = held(client, owner, a)
    assert len(names_now) == 20 and names[0] not in names_now


def wide_runs(client, h, user_id, a, n, at):
    """n runs of the agent with a thumbs down, each with 50 anomalies whose evidence is 300 characters outside the BMP,
    as the app keeps them from the workspace's record: each run's part of the digest is past 180,000 characters as
    json.dumps writes it, so 20 of them alone are past DIGEST_MAX."""
    for k in range(n):
        evidence = "\U0001F600" * 300
        signal_run(client, h, user_id, a, at + k, anomalies=[{"rule": "spend", "level": "warning", "at": at + k,
                                                              "evidence": evidence}] * 50)


def wide_agent(client, h, user_id, name, now):
    """An agent whose digest is longer than DIGEST_MAX until digest() leaves out its oldest versions: 25 versions of a
    brief of 4,000 characters of a script of the BMP and 25 skills of 16 KB, as the size test of test_improve_memory.py
    makes its case B."""
    from proofpane_open.ade import improve, shapes
    a = agent(client, h, name=name, brief="work")
    brief = "\u4e2d" * shapes.MAX_BRIEF
    text = "\u4e2d" * (shapes.MAX_SKILL // 3)
    with store_now() as db:
        for n in range(25):
            improve.version(db, a["id"], user_id, "brief", brief[:-2] + f"{n:02d}", "owner", now - 100 + n)
        db.execute("UPDATE ade_agents SET brief = ? WHERE id = ?", (brief, a["id"]))
        for n in range(25):
            db.execute("INSERT INTO ade_agent_skills (agent_id, user_id, name, content, updated_at) VALUES (?,?,?,?,?)",
                       (a["id"], user_id, f"skill-{n:02d}", text[:-2] + f"{n:02d}", now))
    return a


def test_no_review_for_an_agent_whose_digest_would_still_be_too_long_and_the_day_s_review_goes_to_the_next_by_weight(
        client, owner, monkeypatch):
    """Item 2: Big has the most signal (20 runs with a thumbs down) and runs whose part of the digest alone is past what
    the workspace takes, and so has Big2, next by weight (10 such runs); Wide has less (3), and a digest past it until
    its oldest versions are left out; Wide2 as much as Wide, read after it; Low has the least (1), and comes before
    Wide in the order the agents are read. No review is queued for Big or Big2: no run, no session, no cooldown, and
    each one's record says why, with the length its digest would have and the limit; the day's review goes to Wide,
    the next by weight, whose digest fits once cut, and of equal weights the one read first; neither Wide2 nor Low is
    reviewed."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    big = agent(client, owner, name="Big", brief="work")
    big2 = agent(client, owner, name="Big2", brief="work")
    wide = wide_agent(client, owner, user_id, "Wide", now)
    wide2 = agent(client, owner, name="Wide2", brief="work")
    low = agent(client, owner, name="Low", brief="work")
    for a in (big, big2, wide, wide2, low):
        turn_on(client, owner, a)
    wide_runs(client, owner, user_id, big, 20, now - 600)
    wide_runs(client, owner, user_id, big2, 10, now - 550)
    for k in range(3):
        signal_run(client, owner, user_id, wide, now - 500 + k)
        signal_run(client, owner, user_id, wide2, now - 450 + k)
    signal_run(client, owner, user_id, low, now - 400)
    with store_now() as db:                         # Low read before Wide, and Wide before Wide2: never reviewed, then
        for a, ago in ((wide, 30), (wide2, 20)):    # reviewed longer ago, then less long ago
            db.execute("UPDATE ade_agents SET reviewed_at = ? WHERE id = ?", (now - ago * DAY, a["id"]))
    queued = review_now(user_id, now)
    with store_now() as db:
        kept = db.execute("SELECT agent_id FROM ade_agent_runs WHERE run_id = ?", (queued,)).fetchone()
        reviews = [r["agent_id"] for r in db.execute("SELECT agent_id, trigger FROM ade_agent_runs WHERE loop = 1")
                   if json.loads(r["trigger"]).get("kind") == "review"]
        sessions = [(s["agent_id"], s["kind"], s["run_id"]) for s in db.execute("SELECT * FROM ade_improve_sessions")]
        sizes = [len(json.dumps(improve.digest(db, row(a["id"])))) for a in (big, big2, wide)]
    assert kept is not None and kept["agent_id"] == wide["id"]
    assert reviews == [wide["id"]] and sessions == [(wide["id"], "review", queued)]
    assert [row(a["id"])["reviewed_at"] for a in (big, big2, wide, low)] == [None, None, now, None]
    assert row(wide2["id"])["reviewed_at"] == now - 20 * DAY
    assert sizes[0] > improve.DIGEST_MAX and sizes[1] > improve.DIGEST_MAX >= sizes[2]
    for a, size in ((big, sizes[0]), (big2, sizes[1])):
        shown = client.get(f"{A}/{a['id']}", headers=owner).json()
        assert shown.get("review_skipped") == {"at": now, "reason": SKIPPED.format(size=size)}
        assert shown["reviewed_at"] is None
    assert client.get(f"{A}/{wide['id']}", headers=owner).json().get("review_skipped", "absent") is None


def test_an_agent_passed_over_is_measured_again_the_next_day_and_reviewed_once_its_digest_fits(client, owner,
                                                                                                monkeypatch):
    """Big alone has signal and a digest too long: no review at one in the morning, UTC; later that day it is not
    measured again (digest() is not called for it, and its record keeps the time it was), and an agent that becomes due
    in the afternoon is reviewed that day; at half past midnight the next day, before 24 hours have passed, it is
    measured again, and is still too long; once its newest 20 runs are ones that
    fit, it is reviewed the day after, its record no longer saying it was passed over."""
    from proofpane_open.ade import improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    first = now - now % DAY + DAY + 3600                    # one in the morning, UTC, of the next day
    big = agent(client, owner, name="Big", brief="work")
    small = agent(client, owner, name="Small", brief="work")
    for a in (big, small):
        turn_on(client, owner, a)
    wide_runs(client, owner, user_id, big, 20, now - 600)
    measured = []
    real = improve.digest
    monkeypatch.setattr(improve, "digest", lambda db, a: measured.append(a["id"]) or real(db, a))

    def skipped():
        return client.get(f"{A}/{big['id']}", headers=owner).json().get("review_skipped")
    assert review_now(user_id, first) is None
    assert (skipped() or {}).get("at") == first and measured == [big["id"]]
    assert review_now(user_id, first + 3600) is None                                     # not measured again that day
    assert skipped()["at"] == first and measured == [big["id"]]
    signal_run(client, owner, user_id, small, now - 100)                                 # due in the afternoon
    queued = review_now(user_id, first + 12 * 3600)
    with store_now() as db:
        got = db.execute("SELECT agent_id FROM ade_agent_runs WHERE run_id = ?", (queued,)).fetchone()
    assert got is not None and got[0] == small["id"]
    assert measured == [big["id"], small["id"]] and skipped()["at"] == first
    assert review_now(user_id, first + DAY - 1800) is None                              # the next day, before 24 hours
    assert skipped()["at"] == first + DAY - 1800 and measured[-1] == big["id"]
    for k in range(20):
        signal_run(client, owner, user_id, big, now - 300 + k)                            # its newest 20 fit
    queued = review_now(user_id, first + 2 * DAY)
    assert queued is not None
    with store_now() as db:
        sessions = [(s["agent_id"], s["kind"], s["run_id"]) for s in db.execute("SELECT * FROM ade_improve_sessions")]
    assert len(sessions) == 2 and sessions[-1] == (big["id"], "review", queued)
    assert row(big["id"])["reviewed_at"] == first + 2 * DAY and skipped() is None


def test_a_new_skill_whose_sandbox_comes_once_the_agent_holds_twenty_is_not_evaluated_before_any_run(client, owner,
                                                                                                      monkeypatch):
    """Item 1 at the sandbox: of two new skills from one review to an agent holding 19,
    the first is approved, the agent's 20th, while the second still waits for its sandbox, as the week's budget can make
    it wait. Its treatment arm would hold 21 skills, which the workspace's check_sandbox refuses, so the sandbox is not
    planned: the proposal is not evaluated, its reason naming the limit, before any run is queued or session counted,
    and the account's loop is not left waiting on it: the day's review is queued."""
    from proofpane_open.ade import shapes
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    turn_on(client, owner, a)
    hold(user_id, a, shapes.MAX_SKILLS - 1)
    for k in range(6):
        signal_run(client, owner, user_id, a, now - 900 + k)
        passing_run(client, owner, user_id, a, now - 800 + k)
    review_run(client, owner, user_id, a, [change("skill", skill("Sort"), name="sorting"),
                                          change("skill", skill("Tag"), name="tagging")], now)
    taken(user_id, now)
    got = {p["name"]: p for p in proposals().values()}
    with store_now() as db:                         # the first past its sandbox, the second past its critic
        db.execute("UPDATE ade_improve_proposals SET state = 'eligible' WHERE id = ?", (got["sorting"]["id"],))
        db.execute("UPDATE ade_improve_proposals SET state = 'waiting' WHERE id = ?", (got["tagging"]["id"],))
    assert approve(client, owner, got["sorting"]["id"]).status_code == 200                # the 20th
    assert plan(user_id, now) is None
    second = proposals()[got["tagging"]["id"]]
    assert (second["state"], second["reason"], second["decided_at"]) == ("not_evaluated", FULL.format(n=20), now)
    with store_now() as db:
        sandboxes = [r for r in db.execute("SELECT trigger FROM ade_agent_runs WHERE loop = 1")
                     if json.loads(r["trigger"]).get("kind") == "sandbox"]
        replays = db.execute("SELECT COUNT(*) FROM ade_improve_sessions WHERE kind = 'replay'").fetchone()[0]
    assert (len(sandboxes), replays) == (0, 0)
    assert review_now(user_id, now) is not None                                          # nothing left on its way


def test_a_new_skill_at_twenty_that_names_a_run_s_input_word_for_word_is_refused_so_and_kept_out_of_tried(
        client, owner, monkeypatch):
    """Item 1 at intake, with a run's own input word for word: refused for the count before any critic, a change
    would never meet the app's word-for-word check, and `tried` would carry a run's own input to the next review. At 20
    skills a new skill whose text holds an evidence run's prompt word for word is refused so as it is taken, and `tried`
    lists it without its value; one that holds none is refused for the count, its value listed."""
    from proofpane_open.ade import improve, shapes
    quiet(monkeypatch)
    user_id = me(client, owner)
    now = int(time.time())
    a = agent(client, owner, brief="work")
    hold(user_id, a, shapes.MAX_SKILLS)
    asked = made(client, owner, a["id"])
    with store_now() as db:
        db.execute("UPDATE ade_agent_runs SET trigger = ? WHERE run_id = ?",
                   (json.dumps({"kind": "manual", "prompt": "Label issue 4242 as bug"}), asked))
    review_run(client, owner, user_id, a, [change("skill", skill("Sort") + "Label issue 4242 as bug\n", name="sorting",
                                                  runs=[asked]),
                                           change("skill", skill("Tag"), name="tagging", runs=[asked])], now)
    taken(user_id, now)
    got = {p["name"]: p for p in proposals().values()}
    assert [(got[n]["state"], got[n]["reason"]) for n in ("sorting", "tagging")] == \
        [("refused", "it names a run's own input word for word"), ("refused", FULL.format(n=20))]
    with store_now() as db:
        listed = {t["name"]: t for t in improve.digest(db, row(a["id"]))["tried"]}
    assert (listed["sorting"]["proposed"], listed["tagging"]["proposed"]) == (None, json.dumps(skill("Tag")))
    assert "4242" not in json.dumps(listed)


def test_the_manual_says_what_the_loop_refuses_at_twenty_skills_and_when_no_review_is_queued():
    """The manual's paragraphs on the improve loop say both, with the number the code has, and the three places a new
    skill is refused."""
    from proofpane_open.ade import shapes
    manual_or_skip()
    readme = file_text("MANUAL.md")
    start = readme.index("**Improving it, bounded.**")
    loop = readme[start:readme.index("The pruner also", start)]
    assert re.findall(r"while it holds (\d+) skills", loop) == [str(shapes.MAX_SKILLS)]
    assert "`review_skipped`" in loop and "no review is queued" in loop
    assert "as its sandbox is planned" in loop
