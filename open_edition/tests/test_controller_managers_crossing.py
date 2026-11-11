"""A requirement's own manager across the app and the workspace:
the app's own sync, through the real workspace_client, against a real workspace agent, whose stand-in claude
(chat_stand_in.py) runs the controller's chat and each attempt of a manager's, an attempt calling the tools its
requirement's words name ("manager: ctool ..."), so that a field one side renames, or a shape one side changes, fails
here; and a restart of the workspace in the middle of a requirement, its management going on from its packet.
Nothing reaches a real claude."""

import json
import os
import sys
import types

import pytest

from proofpane_open import store
from proofpane_open.ade import controller
from test_controller import own
from test_controller_crossing import REAL_CALL, STAND_IN, agent, chat, record, said, until  # noqa: F401
from workspace_fixtures import Agent, make_repo

START = {"repo": "demo", "task": "Edit README.md so its first line is hello."}
# the owner's request: its first paragraph what is wanted, its second what the stand-in's attempt does
REQUEST = ("Make the README say hello.\n\nmanager: ctool start_task|" + json.dumps(START) +
           '\nmanager: ctool end_attempt|{"outcome": "waiting"}')


def owner(request=REQUEST):
    user = {"id": "u" + os.urandom(4).hex()}
    own(user["id"])
    controller.change_settings(user, {"account": "default", "cap": 3})
    controller.set_state(user, "on")
    controller.add_request(user, request)
    return user


def sync(agent, user):
    return controller.sync(user, agent.endpoint, call=REAL_CALL)


def planned(agent, user, share=1, words=REQUEST):
    """The first sync, which sends the managers' route (a plan then takes a share), the controller's chat started, and
    a plan with a share made through its own tool: the requirement's id."""
    sync(agent, user)
    sid = until(lambda: record(agent)["session"], lambda s: s is not None)
    until(lambda: chat(agent, sid), lambda log: any(e["kind"] == "result" for e in log["events"]))
    [req] = controller.view(user)["requests"]
    got = said(agent, sid, "ctool plan_requirement|" + json.dumps(
        {"request": req["id"], "title": "Hello README", "words": words, "repos": ["demo"], "share": share}))
    return json.loads(got)["requirement"]


def manager(user, rid):
    with store.connect() as db:
        m = controller._manager(db, user["id"], rid)
        return dict(m, state=controller.manager_state(m, m["requirement_state"]))


def attempts(user):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_controller_attempts WHERE user_id = ? ORDER BY n",
                                            (user["id"],)).fetchall()]


def test_a_managed_requirement_s_attempt_starts_its_task_and_ends_and_both_sides_agree_on_every_field(agent):
    user = owner()
    rid = planned(agent, user)
    held = record(agent)["managers_held"]
    assert held is not None                       # the managers' route reached the workspace at the first sync

    def synced():
        sync(agent, user)
        return attempts(user)
    # the plan taken (its manager ready), then an attempt queued and sent, the workspace taking it
    [a] = until(synced, lambda got: len(got) == 1 and got[0]["state"] in ("sent", "running", "ended"))
    assert (a["n"], a["fence"]) == (1, 1)
    # its chat runs the attempt: the task started with the attempt's id, fence and text, then its end, waiting
    page = until(lambda: record(agent), lambda p: any(x["state"] == "ended" for x in p["attempts"]))
    [x] = page["attempts"]
    assert (x["id"], x["requirement"], x["fence"], x["outcome"], x["cause"]) == (a["id"], rid, 1, "waiting", None)
    [start] = [s for s in page["actions"] if s["kind"] == "start"]
    assert (start["attempt"], start["fence"], start["text"]) == (a["id"], 1, START["task"])
    got = until(synced, lambda got: got[0]["state"] == "ended")
    assert (got[0]["outcome"], got[0]["cause"]) == ("waiting", None)
    assert manager(user, rid)["failures"] == 0
    view = controller.requirement_view(user, rid)
    [task] = [t for t in view["tasks"] if t["attempt"] == a["id"]]
    assert task["text"]["text"] == START["task"] and task["flag"] is None
    assert view["share"] == 1 and view["manager"]["attempts"] >= 1
    # the controller's own packet gives the managed requirement's state and counts, none of its tasks
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, user["id"])[0])
    [item] = packet["information"]["requirements"]
    # (the task's words are in the owner's request too, which carries the stand-in's script: what is checked is that
    # the packet gives none of the requirement's tasks)
    assert (item["share"], item["manager"]) == (1, manager(user, rid)["state"])
    assert "tasks" not in item and "more_tasks" not in item and sum(item["counts"].values()) >= 1
    # the room the real workspace's loop measured reaches the app: open, no
    # window of its account's holding the chat's model
    assert controller.view(user)["controller"]["room"]["window"] == "open"


# the owner's request of the restart's test: its attempt starts its task, then works on until it is cut short
REQUEST_WAIT = "Make the README say hello.\n\nmanager: ctool start_task|" + json.dumps(START) + "\nmanager: wait"


def sync_in_a_fresh_app(agent, user):
    """The app's next sync in a process of its own, from its store alone: the app restarted, which keeps nothing of a
    manager's in memory."""
    import subprocess
    code = ("import sys, types; sys.path.insert(0, '.'); from proofpane_open.ade import controller, workspace_client; "
            f"e = types.SimpleNamespace(http={agent.base!r}, secret={agent.secret!r}, ssl=None); "
            f"controller.sync({{'id': {user['id']!r}}}, e, call=workspace_client.call)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-c", code], cwd=root, env=dict(os.environ), check=True, timeout=120,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_a_restart_of_the_app_in_the_middle_of_a_requirement_goes_on_from_its_store(agent):
    """A restart, the app's half: once the workspace has taken an attempt, the app's
    next syncs run in a process of their own each, a restart of the app every time, and take the attempt's start and
    end from the pages, its manager left as the end leaves it."""
    user = owner()
    rid = planned(agent, user)

    def synced():
        sync(agent, user)
        return attempts(user)
    [a] = until(synced, lambda got: len(got) == 1 and got[0]["state"] in ("sent", "running", "ended"))

    def fresh():
        sync_in_a_fresh_app(agent, user)
        return attempts(user)
    got = until(fresh, lambda got: got[0]["state"] == "ended", timeout=120)
    assert (got[0]["id"], got[0]["outcome"], got[0]["cause"]) == (a["id"], "waiting", None)
    view = controller.requirement_view(user, rid)
    [task] = [t for t in view["tasks"] if t["attempt"] == a["id"]]
    assert task["text"]["text"] == START["task"]
    assert manager(user, rid)["failures"] == 0


@pytest.mark.parametrize("reached", [True, False], ids=["its-answer-lost", "the-request-lost"])
def test_a_restart_of_the_app_with_an_attempt_sent_and_unanswered_sends_it_again_and_goes_on_from_its_store(agent,
                                                                                                         reached):
    """A restart, the app's half with an attempt unanswered:
    the managers' route's answer lost, once the workspace has taken the attempt or
    before the request reached it, the app restarts, and each sync after runs in a process of its own, from the store
    alone: an attempt the workspace took is moved on by the page that lists it, before the managers' route could send
    it again, and one it never had is sent again and taken; its start and end are taken from the pages, and, once the
    owner Resumes its manager, the next attempt is queued, built and sent, and the workspace runs it."""
    from proofpane_open.ade import workspace_client
    user = owner()
    rid = planned(agent, user)

    def lossy(endpoint, method, path, *rest, **kw):
        attempt = path == "/controller/managers" and rest and rest[0].get("attempt")
        if attempt and not reached:
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)     # lost on its way there
        got = REAL_CALL(endpoint, method, path, *rest, **kw)
        if attempt:
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)     # taken, its answer lost
        return got

    def lost():
        controller.sync(user, agent.endpoint, call=lossy)
        return attempts(user)
    [a] = until(lost, lambda got: len(got) == 1 and got[0]["sent_at"] is not None)
    assert a["state"] == "queued"                                   # sent, its answer never taken
    assert (a["id"] in [x["id"] for x in record(agent)["attempts"]]) is reached
    with store.connect() as db:
        assert controller._row(db, user["id"])["managers_error"] is not None

    def fresh():
        sync_in_a_fresh_app(agent, user)
        return attempts(user)
    got = until(fresh, lambda got: got[0]["state"] == "ended", timeout=120)
    assert (got[0]["id"], got[0]["outcome"], got[0]["cause"]) == (a["id"], "waiting", None)
    with store.connect() as db:
        assert controller._row(db, user["id"])["managers_error"] is None
    controller.resume(user, rid)        # the owner's, kept in the store, which the next process reads
    got = until(fresh, lambda got: len(got) == 2 and got[1]["state"] == "ended", timeout=120)
    assert (got[1]["n"], got[1]["fence"], got[1]["outcome"]) == (2, 2, "waiting")
    packet = json.loads(got[1]["packet"])
    assert {k: packet["information"]["attempts"][0][k] for k in ("n", "outcome", "cause")} == {
        "n": 1, "outcome": "waiting", "cause": None}
    assert "resume" in [w["kind"] for w in packet["information"]["woken_by"]]
    [x] = [x for x in record(agent)["attempts"] if x["id"] == got[1]["id"]]
    assert (x["state"], x["fence"], x["outcome"]) == ("ended", 2, "waiting")
    assert manager(user, rid)["failures"] == 0


def test_a_restart_of_the_workspace_while_an_attempt_runs_ends_it_and_the_next_packet_gives_that_end(tmp_path):
    """A restart, the workspace's half: an attempt running (its task started, its
    chat working on) when the workspace stops is ended `stopped` (`restart`) by the workspace that starts after it,
    counted among its manager's failures in a row; its manager is woken (`restart`, its wakes given back), and the next
    attempt's packet, built afresh from the store, gives that end."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    first = Agent(str(tmp_path / "ws"), str(home), env)
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"), origin="https://github.com/acme/demo.git")
        first.endpoint = types.SimpleNamespace(http=first.base, secret=first.secret, ssl=None)
        user = owner(REQUEST_WAIT)
        rid = planned(first, user, words=REQUEST_WAIT)

        def started():
            sync(first, user)
            page = record(first)
            return page if any(s.get("attempt") for s in page["actions"] if s["kind"] == "start") and \
                any(x["state"] == "running" for x in page["attempts"]) else None
        until(started, lambda page: page is not None)
        [a] = attempts(user)
    finally:
        first.close()
    os.remove(tmp_path / "ws" / "agent.json")
    second = Agent(str(tmp_path / "ws"), str(home), env)
    try:
        second.endpoint = types.SimpleNamespace(http=second.base, secret=second.secret, ssl=None)
        got = until(lambda: (sync(second, user), attempts(user))[1],
                    lambda got: len(got) == 2 and got[0]["state"] == "ended" and got[1]["packet"] is not None)
        assert (got[0]["id"], got[0]["outcome"], got[0]["cause"]) == (a["id"], "stopped", "restart")
        packet = json.loads(got[1]["packet"])
        assert {k: packet["information"]["attempts"][0][k] for k in ("n", "outcome", "cause")} == {
            "n": 1, "outcome": "stopped", "cause": "restart"}
        assert "restart" in [w["kind"] for w in packet["information"]["woken_by"]]
        assert manager(user, rid)["failures"] == 1 and manager(user, rid)["fence"] == 2
    finally:
        second.close()
