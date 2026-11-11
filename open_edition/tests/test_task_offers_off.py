"""The teammate part switched off while hand-offs stand. While the part is off, each route that only ends or
undoes a hand-off answers and does what it does while the part is on: Withdraw, Decline, Take back, Give back, and
custody's End and Decline; the two reads say why and carry no point 13's text; and what starts or advances a hand-off
still refuses, Keep among it (a test in test_task_offers.py runs every route under the four ways the part is
off). Each test here makes its hand-off while the part is on, then turns the part off, as a change of the deployment's
settings would, and runs against the same fakes as test_task_offers.py."""

import pytest

from proofpane_open import store
from proofpane_open.ade import holds, task_offers

from test_task_offers import (C1, C3, MATE, OTHER, OWNER, T, THIRD, accepted, ask_custody, ended, hand, handed, offered,
                              setup_custody, setup_pressed, worked)
from test_task_offers import world  # noqa: F401  (fixture)

A1_TASKS = "/api/ade/agents/tasks"
AWAY = ("offering", "handed_over", "returning")


def turned_off(w, how):
    """The part turned off with what it made standing: for want of an operator, or by the deployment's setting. Why, in
    the words every refusal and read gives."""
    if how == "operator":
        w.mp.delenv("OPEN_EDITION_OPERATOR")
    else:
        w.mp.setenv("OPEN_EDITION_ADE_TASK_TEAMMATE", "off")
    words = task_offers.enabled(w.u(OWNER))
    assert words == (task_offers.OFF_OPERATOR if how == "operator" else task_offers.OFF_SETTING)
    return words


OFF_WAYS = pytest.mark.parametrize("how", ["operator", "setting"])


def post(w, who, path, body=None):
    return w.client.post(T + path, headers=w.h(who), json=body if body is not None else {})


@OFF_WAYS
@pytest.mark.parametrize("stage", ["held", "offered"])
def test_withdraw_while_off_ends_the_offer_and_brings_the_task_back(world, how, stage):
    """The owner withdraws an offer in its hold (its hold cancelled, so nothing of it is ever pushed) or one waiting for
    the teammate (gone from what they read), while the part is off: it ends withdrawn, naming the teammate no more, and
    the task is back, waiting as it waited."""
    task_id, _ = setup_pressed(world)
    made = world.offer_to(task_id) if stage == "held" else offered(world, task_id)
    offer_id = made["offer"]["id"]
    hold_id = world.offer(offer_id)["hold_id"]
    pushed = len(world.ws.of("/push"))
    turned_off(world, how)
    r = post(world, OWNER, f"/offers/{offer_id}/withdraw")
    assert r.status_code == 200 and r.json()["offer"]["state"] == "withdrawn", r.text
    offer = world.offer(offer_id)
    why = task_offers.CANCELLED if stage == "held" else task_offers.WITHDRAWN
    assert (offer["state"], offer["why"], offer["teammate_user"], offer["address"]) == ("withdrawn", why, None, None)
    assert world.get(OWNER, task_id)["state"] == "suspended"
    with store.connect() as db:
        assert (holds.get(db, hold_id)["cancelled_at"] is not None) == (stage == "held")
    world.running(OWNER, MATE)
    world.settle()                                      # its hold's time: nothing of it goes ahead
    assert world.offer(offer_id)["state"] == "withdrawn" and len(world.ws.of("/push")) == pushed
    assert pushed == (0 if stage == "held" else 1)
    assert world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"] == []


@OFF_WAYS
def test_decline_while_off_ends_the_offer_and_brings_the_owners_task_back(world, how):
    """The teammate reads an offer waiting for them while the part is off, with no point 13's text, and declines it:
    it ends declined, naming them no more, and the owner's task is back."""
    task_id, _ = setup_pressed(world)
    offer_id = offered(world, task_id)["offer"]["id"]
    words = turned_off(world, how)
    seen = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()
    assert seen["off"] == words and [(o["id"], o["terms"]) for o in seen["received"]] == [(offer_id, None)]
    r = post(world, MATE, f"/offers/{offer_id}/decline")
    assert r.status_code == 200 and r.json()["offer"]["state"] == "declined", r.text
    offer = world.offer(offer_id)
    assert (offer["state"], offer["why"], offer["teammate_user"], offer["address"]) == (
        "declined", task_offers.DECLINED, None, None)
    assert world.get(OWNER, task_id)["state"] == "suspended"
    assert world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"] == []


@OFF_WAYS
def test_take_back_while_off_brings_a_handed_task_home_through_the_round(world, how):
    """Take back while the part is off: the owner's task returning and the teammate's marked returned; the round, which
    has no switch of its own, carries the way back out: their last pushed work fetched by the owner's workspace and
    applied (tasks.take_return), the offer returned, the task with its owner again."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    task_offers.drive()                                 # their work pushed between their attempts
    turned_off(world, how)
    r = post(world, OWNER, f"/tasks/{task_id}/take-back")
    assert r.status_code == 200 and r.json()["task"]["state"] == "returning", r.text
    assert world.get(MATE, theirs)["returned"] == 1 and world.journal(task_id)[-1]["kind"] == "task.taken_back"
    task_offers.drive()
    assert world.offer(offer_id)["state"] == "returned"
    assert world.tasks.returns[-1][0] == task_id and world.tasks.returns[-1][1]["commit"] == C3
    assert world.get(OWNER, task_id)["state"] not in AWAY


@OFF_WAYS
def test_give_back_while_off_sends_a_custody_task_home_and_keep_is_refused(world, how):
    """The custodian gives a custody task back while the part is off: its way back starts and the round carries it
    out. Keep, which keeps a hand-off going, is refused then, with why."""
    task_id, _, _ = setup_custody(world)
    theirs = handed(world, task_id)["their_task"]
    ended(theirs)
    world.ws.commits.add(C1)
    words = turned_off(world, how)
    version = world.get(MATE, theirs)["state_version"]
    r = post(world, MATE, f"/tasks/{theirs}/keep", {"state_version": version})
    assert r.status_code == 409 and r.json() == {"detail": words, "off": "teammate"}
    assert world.get(MATE, theirs)["state_version"] == version
    r = post(world, MATE, f"/tasks/{theirs}/give-back", {"state_version": version})
    assert r.status_code == 200, r.text
    assert world.get(OWNER, task_id)["state"] == "returning" and world.get(MATE, theirs)["returned"] == 1
    task_offers.drive()
    assert world.tasks.returns[-1][0] == task_id and world.get(OWNER, task_id)["state"] not in AWAY


@pytest.mark.parametrize("by", ["owner", "custodian"])
def test_ending_custody_while_off_ends_it_and_a_hand_off_held_under_it(world, by):
    """Either side ends custody while the part is off, with the sign-in alone: it ends, and a custody hand-off held
    under it ends failed, the task back for the relay to decide again."""
    task_id, _, custody_id = setup_custody(world)
    _, hold = hand(world, task_id)                      # a custody hand-off in its hold, begun while the part was on
    turned_off(world, "operator")
    r = post(world, OWNER if by == "owner" else MATE, f"/custody/{custody_id}/end")
    assert r.status_code == 200 and r.json()["custody"]["state"] == "ended", r.text
    assert world.custody(custody_id)["ended_by"] == by
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "failed" and offer["why"] == task_offers.CUSTODY_ENDED_WHY
    assert world.get(OWNER, task_id)["state"] == "suspended"


def test_declining_a_custody_request_while_off_names_the_custodian_no_more(world):
    """A custody request read while the part is off carries no point 13's text, and the teammate declines it: it ends
    declined, its custodian and the typed address cleared."""
    world.user(MATE)                                    # someone's address, so the request reaches them
    custody_id = ask_custody(world)
    words = turned_off(world, "operator")
    seen = world.client.get(f"{T}/custody", headers=world.h(MATE)).json()
    assert seen["off"] == words and [(c["id"], c["terms"]) for c in seen["as_custodian"]] == [(custody_id, None)]
    r = post(world, MATE, f"/custody/{custody_id}/decline")
    assert r.status_code == 200 and r.json()["custody"]["state"] == "declined", r.text
    row = world.custody(custody_id)
    assert (row["state"], row["custodian_user"], row["address"]) == ("declined", None, None)


def test_the_reads_while_off_are_the_reads_while_on_with_why_and_no_point_13(world):
    """GET /offers and GET /custody while the part is off answer as while it is on, every list and view the same but
    point 13's text, which no received offer or custody request carries then, with `off`, why; while the part is on
    they carry no `off` at all."""
    task_id, _ = setup_pressed(world)
    offer_id = offered(world, task_id)["offer"]["id"]
    custody_id = ask_custody(world)
    reads = [(who, path) for who in (OWNER, MATE) for path in ("/offers", "/custody")]
    on = [world.client.get(T + path, headers=world.h(who)).json() for who, path in reads]
    assert not any("off" in got for got in on)
    mate_offers, mate_custody = on[2], on[3]
    assert [o["id"] for o in on[0]["made"]] == [offer_id] and on[1]["as_owner"]["id"] == custody_id
    assert mate_offers["received"][0]["terms"] is not None and mate_custody["as_custodian"][0]["terms"] is not None
    words = turned_off(world, "operator")
    off = [world.client.get(T + path, headers=world.h(who)).json() for who, path in reads]
    no_terms = lambda views: [dict(view, terms=None) for view in views]   # noqa: E731
    assert off[0] == dict(on[0], off=words) and off[1] == dict(on[1], off=words)
    assert off[2] == dict(mate_offers, received=no_terms(mate_offers["received"]), off=words)
    assert off[3] == dict(mate_custody, as_custodian=no_terms(mate_custody["as_custodian"]), off=words)


def test_while_off_what_ends_a_hand_off_is_still_404_for_another_users(world):
    """Answering while the part is off keeps each route's own check: another user's offer, task or custody is not
    theirs to end or undo, 404, and nothing changes."""
    task_id, _, custody_id = setup_custody(world)
    theirs = handed(world, task_id)["their_task"]
    offer_id = offered(world, world.task(OWNER, world.agent(OWNER)))["offer"]["id"]
    asked = ask_custody(world, owner=THIRD)
    turned_off(world, "operator")
    for path, body in ((f"/offers/{offer_id}/withdraw", None), (f"/offers/{offer_id}/decline", None),
                       (f"/tasks/{task_id}/take-back", None),
                       (f"/tasks/{theirs}/give-back", {"state_version": world.get(MATE, theirs)["state_version"]}),
                       (f"/custody/{asked}/decline", None), (f"/custody/{custody_id}/end", None)):
        r = post(world, OTHER, path, body)
        assert r.status_code == 404, (path, r.status_code, r.text)
    assert world.offer(offer_id)["state"] == "offered" and world.get(OWNER, task_id)["state"] == "handed_over"
    assert [world.custody(c)["state"] for c in (custody_id, asked)] == ["accepted", "asked"]


@pytest.mark.parametrize("stage", ["offered", "handed"])
def test_end_refuses_a_task_away_and_takes_it_once_withdraw_or_take_back_brings_it_home(world, stage):
    """End (tasks.end) refuses a task that is offering, handed over or returning, the part on or off ("Stop it, or
    take it back, first."): its own rule, which the part's switch leaves as it is. Were Withdraw and Take back refused
    while the part is off, nothing the owner could press would let such a task out; they answer, and End then takes
    it."""
    if stage == "offered":
        task_id, _ = setup_pressed(world)
        offer_id = offered(world, task_id)["offer"]["id"]
    else:
        task_id, _, _ = accepted(world)
    turned_off(world, "operator")

    def end():
        return world.client.post(f"{A1_TASKS}/{task_id}/end", headers=world.h(OWNER), json={
            "state_version": world.get(OWNER, task_id)["state_version"], "outcome": "stopped"})
    r = end()
    assert r.status_code == 412 and r.json()["detail"] == "Stop it, or take it back, first.", r.text
    if stage == "offered":
        assert post(world, OWNER, f"/offers/{offer_id}/withdraw").status_code == 200
    else:
        assert post(world, OWNER, f"/tasks/{task_id}/take-back").status_code == 200
        r = end()
        assert r.status_code == 412 and world.get(OWNER, task_id)["state"] == "returning", r.text
        task_offers.drive()
    assert world.get(OWNER, task_id)["state"] not in AWAY
    r = end()
    assert r.status_code == 200 and world.get(OWNER, task_id)["state"] == "stopped", r.text
