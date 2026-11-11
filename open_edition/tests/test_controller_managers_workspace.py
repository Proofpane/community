"""A requirement's own manager, the workspace's side: the cap; a plan's share and the scopes of the controller's bounds;
the managers' route and the attempts it takes or refuses; an attempt's chat, its five tools and its ends; and the
record's page that gives them. The workspace's own pieces, as test_controller_workspace.py tests them: a stand-in
terminal and claude, the chat's sends kept and nothing run."""

import asyncio
import base64
import json

import pytest

from proofpane_workspace import controller as ctl
from proofpane_workspace import task_shapes as shapes
from test_controller_workspace import (NOW, _task, mirror, on, page, plan, refused, start, take,  # noqa: F401
                                       uid, ws)


# ---- helpers ---------------------------------------------------------------------------------------------------------

def manager_packet(**extra):
    data = json.dumps(dict({"schema": ctl.MANAGER_SCHEMA, "instructions": {"by": "owner"}}, **extra)).encode()
    return base64.b64encode(data).decode(), shapes.tagged_digest(ctl.MANAGER_TAG, data)


def an_attempt(requirement, fence=1, n=1, aid=None, **over):
    b64, digest = manager_packet()
    return dict({"id": aid or uid(), "requirement": requirement, "fence": fence, "n": n, "packet_b64": b64,
                 "packet_digest": digest}, **over)


def send(w, attempt=None, version=1, managers=()):
    """The app's managers' route, as take_managers takes it: its answer."""
    return asyncio.run(w.controller.take_managers({"version": version, "managers": list(managers),
                                                   "attempt": attempt}))


def listed(m, *requirements, **state):
    """The owner's side with these requirements listed, open, not held and waiting for no go unless `state` says."""
    rows = [dict({"id": r, "state": "open", "hold": False, "waits_for_go": False}, **state) for r in requirements]
    return dict(m, version=m["version"] + 1, requirements=rows)


def managed(w, cap=3, share=2, own=False, **over):
    """The controller on, an app that sent the managers' route, and a requirement planned with a share, listed open by
    the owner's side: (the side, the requirement's id); with `own`, a requirement of the controller's own too, planned
    before the app ran managers, listed as well: (the side, the managed one's id, the controller's own's)."""
    m = on(w, cap=cap, **over)
    mine = plan(w, m) if own else None
    send(w)
    requirement = plan(w, m, share=share)
    m = listed(m, *(r for r in (requirement, mine) if r))
    take(w, m)
    return (m, requirement, mine) if own else (m, requirement)


def taken(w, requirement, **over):
    """An attempt sent and taken: its entry in the record."""
    a = an_attempt(requirement, **over)
    answer = send(w, a)
    assert answer["attempt"] == {"id": a["id"], "taken": True, "cause": None, "why": None}, answer
    return w.controller.record["attempts"][a["id"]]


# ---- the cap ---------------------------------------------------------------------------------------------------------

def test_a_mirror_s_cap_of_11_from_an_older_app_is_taken_as_10(ws):
    """mirror_of: an older app sends a cap of 1 to 11; the workspace takes it and
    reads 11 as 10, so that such an app is not refused while CAP_MOST is 10 on both sides."""
    assert (ctl.CAP_MOST, ctl.CAP_TAKEN) == (10, 11)
    assert ctl.mirror_of(mirror(cap=11))["cap"] == 10
    assert ctl.mirror_of(mirror(cap=10))["cap"] == 10
    on(ws, cap=11)
    assert ws.controller.record["mirror"]["cap"] == 10
    assert ws.controller.read_tree()["controller"]["cap"] == 10


@pytest.mark.parametrize("cap", [0, 12])
def test_a_mirror_s_cap_past_what_any_app_sends_is_refused(ws, cap):
    with pytest.raises(ctl.ControllerError) as e:
        ctl.mirror_of(mirror(cap=cap))
    assert str(e.value) == "The mirror's cap is not from 1 to 11."


# ---- a plan's share and the controller's scope -----------------------------------------------------------------------

def test_a_share_is_refused_before_the_app_runs_managers_and_required_after(ws):
    m = on(ws, cap=3)
    assert "plan without a share" in refused(plan, ws, m, share=2)
    before = plan(ws, m)                                # the controller's
    assert "share" not in ws.controller.record["plans"][before]
    assert "share" not in [a for a in page(ws)["actions"] if a["kind"] == "plan"][0]
    send(ws)
    assert ws.controller.record["managed_since"] is not None
    assert "share is a whole number from 1 to the cap (3)" in refused(plan, ws, m)
    assert "share is a whole number from 1 to the cap (3)" in refused(plan, ws, m, share=4)
    rid = plan(ws, m, share=3)
    assert ws.controller.record["plans"][rid]["share"] == 3
    assert [a["share"] for a in page(ws)["actions"] if a["kind"] == "plan" and a["requirement"] == rid] == [3]


def test_the_controller_s_start_stop_and_continue_on_a_managed_requirement_are_refused(ws):
    m, rid = managed(ws)
    assert refused(start, ws, rid) == "That requirement has a manager of its own, which starts its tasks."
    t = _task(ws, "waiting", requirement=rid, waits_until=NOW - 3600)
    for tool in (ws.controller.stop_task, ws.controller.continue_task):
        assert refused(tool, {"task": t["id"]}) == "That task is its requirement's manager's."


def test_the_cap_s_refusal_is_in_each_scope_s_words_and_the_controller_s_first_is_kept(ws, monkeypatch):
    """The controller is told how many of the open
    sessions are its own, the rest its managers'; `capped_since` is its first such refusal since its last start taken,
    which a later one does not move; and it is let go once no task the
    controller may not stop shares the cap, its reason gone."""
    m, rid, own = managed(ws, cap=2, share=2, own=True)
    attempt, _, _ = running(ws, monkeypatch, rid)
    got = [call(ws, attempt, "start_task", {"repo": "demo", "task": f"Task {i}."})[0] for i in range(2)]
    ws.controller.record["capped_since"] = None
    words = refused(start, ws, own)
    assert words == ("The cap is 2, and 2 sessions are open, 0 of them the Manager's own and the rest its "
                     "requirements' managers': stop one of your own you are done with, or wait.")
    first = ws.controller.record["capped_since"]
    assert first is not None
    ws.controller.record["capped_since"] = first - 100          # as if it came a while ago
    refused(start, ws, own)
    assert ws.controller.record["capped_since"] == first - 100  # a later refusal does not move it
    assert page(ws)["room"]["capped_since"] == first - 100
    for g in got:
        assert not call(ws, attempt, "stop_task", {"task": g["task"]})[1]
    assert page(ws)["room"]["capped_since"] is None             # no task it may not stop shares the cap now


def test_the_controller_s_capped_start_is_let_go_where_nothing_of_its_own_could_clear_it(ws, monkeypatch):
    """With no requirement of its own open on the owner's side, no start of
    the controller's own comes to clear `capped_since`, so it is let go, and the owner is not told it waits."""
    m, rid, own = managed(ws, cap=2, share=2, own=True)
    attempt, _, _ = running(ws, monkeypatch, rid)
    for i in range(2):
        assert not call(ws, attempt, "start_task", {"repo": "demo", "task": f"Task {i}."})[1]
    refused(start, ws, own)
    assert page(ws)["room"]["capped_since"] is not None
    take(ws, dict(listed(m, rid), version=m["version"] + 7))     # the owner's side lists its own no longer open
    assert page(ws)["room"]["capped_since"] is None


def test_the_controller_s_read_tree_gives_a_managed_requirement_s_state_and_counts_and_none_of_its_tasks(ws):
    """A managed requirement's share, its manager's state as the app last sent it ("not yet known" before
    it did), and its tasks' counts by state; never a task, its commit, file, text or check."""
    m, rid = managed(ws)
    _task(ws, "idle", requirement=rid)
    _task(ws, "done", requirement=rid)
    item = next(r for r in ws.controller.read_tree()["requirements"] if r["id"] == rid)
    assert "tasks" not in item and "more_tasks" not in item
    assert (item["share"], item["manager"]) == (2, "not yet known")
    assert (item["counts"]["idle"], item["counts"]["done"], item["counts"]["running"]) == (1, 1, 0)
    send(ws, version=2, managers=[{"requirement": rid, "share": 2, "state": "waiting", "waits": None,
                                    "waits_until": None, "attempts": 1}])
    item = next(r for r in ws.controller.read_tree()["requirements"] if r["id"] == rid)
    assert item["manager"] == "waiting"


def test_a_managed_task_s_change_and_reset_tell_the_controller_s_chat_nothing(ws):
    m, rid, mine = managed(ws, own=True)
    t = _task(ws, "working", requirement=rid)
    ws.controller.wakes.clear()
    with ws.controller._lock:
        ws.controller._set(t, "idle", NOW)
        ws.controller._set(t, "waiting", NOW, NOW - 600)
    assert ws.controller.wakes == {}
    own = _task(ws, "working", requirement=mine)
    with ws.controller._lock:
        ws.controller._set(own, "idle", NOW)
    assert list(ws.controller.wakes) == [own["id"]]


def test_a_task_s_changed_is_its_state_s_change_number_which_a_round_alone_does_not_move(ws):
    """A round of checks kept moves a task's `seq`; `changed`, which wakes its manager,
    moves with its state alone (`state_seq`). A task from an older record, without one, gives its `seq`."""
    m, rid = managed(ws)
    t = _task(ws, "working", requirement=rid)
    with ws.controller._lock:
        ws.controller._set(t, "idle", NOW)
    changed = next(x for x in page(ws)["tasks"] if x["id"] == t["id"])["changed"]
    with ws.controller._lock:
        ws.controller._keep_round(t, "a" * 40, [{"id": 1, "outcome": "passed"}])
    assert t["seq"] > changed
    assert next(x for x in page(ws)["tasks"] if x["id"] == t["id"])["changed"] == changed
    old = _task(ws, "idle", requirement=rid)
    assert next(x for x in page(ws)["tasks"] if x["id"] == old["id"])["changed"] == old["seq"]


# ---- the managers' route and the take --------------------------------------------------------------------------------

def test_the_managers_route_is_read_to_its_bytes_and_refused_past_them(ws):
    import httpx
    from proofpane_workspace import agent
    from workspace_fixtures import SECRET

    async def put(raw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(ws, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            return await c.put("/controller/managers", content=raw, headers={"Content-Type": "application/json"})
    raw = json.dumps({"version": 3, "managers": [], "attempt": None}).encode()
    at = raw + b" " * (ctl.MANAGERS_MAX - len(raw))
    answer = asyncio.run(put(at))
    assert (answer.status_code, answer.json()) == (200, {"held": 3, "attempt": None})
    past = asyncio.run(put(at + b" "))
    assert (past.status_code, past.json()) == (400, {"detail": "The request is too large."})
    assert asyncio.run(put(b'{"version": 3}')).status_code == 400


def test_an_attempt_sent_twice_is_taken_once_and_one_refused_is_answered_the_same_its_fence_not_moved(ws):
    m, rid = managed(ws)
    a = an_attempt(rid, fence=1)
    first = send(ws, a)["attempt"]
    assert first["taken"] is True
    again = send(ws, a)["attempt"]
    assert again == first and len(ws.controller.record["attempts"]) == 1
    late = an_attempt(rid, fence=1)                          # its fence not above the highest taken
    no = send(ws, late)["attempt"]
    assert (no["taken"], no["cause"]) == (False, "fence")
    assert send(ws, late)["attempt"] == no
    assert ws.controller.record["managers"][rid] == {"fence": 1, "attempt": a["id"]}


@pytest.mark.parametrize("case,cause", [
    ("off", "off"), ("managed", "managed"), ("closed", "closed"), ("held", "held"), ("go", "go"),
    ("packet", "packet"), ("schema", "packet"), ("busy", "busy"), ("window", "window"), ("paid", "paid"),
    ("reserve", "reserve"), ("place", "place"),
])
def test_each_condition_of_the_take_refuses_an_attempt_with_its_cause(ws, monkeypatch, case, cause):
    m, rid = managed(ws)
    a = an_attempt(rid)
    if case == "off":
        take(ws, dict(listed(m, rid), state="off", version=m["version"] + 5))
    elif case == "managed":
        a = an_attempt(uid())                                # no plan of it with a share
    elif case == "closed":
        take(ws, listed(m, rid, state="done"))
    elif case == "held":
        take(ws, listed(m, rid, hold=True))
    elif case == "go":
        take(ws, listed(m, rid, waits_for_go=True))
    elif case == "packet":
        a = dict(a, packet_digest="0" * 64)
    elif case == "schema":
        data = json.dumps({"schema": "ade.controller.v1"}).encode()
        a = dict(a, packet_b64=base64.b64encode(data).decode(),
                 packet_digest=shapes.tagged_digest(ctl.MANAGER_TAG, data))
    elif case == "busy":
        taken(ws, rid)
        a = an_attempt(rid, fence=2)
    elif case == "window":
        ws.controller.rates["five_hour"] = {"status": "rejected", "at": NOW, "account": "default",
                                            "resets_at": NOW + 3600, "paid": False}
    elif case == "paid":
        ws.controller.rates["five_hour"] = {"status": "rejected", "at": NOW, "account": "default",
                                            "resets_at": NOW + 3600, "paid": True}
    elif case == "reserve":
        ws.controller.rates["seven_day"] = {"status": "allowed_warning", "at": NOW, "account": "default",
                                            "resets_at": NOW + 86400, "paid": False}
    elif case == "place":
        monkeypatch.setattr(ws, "running_programs", lambda: ws.max_sessions)
    if case in ("window", "paid", "reserve"):
        # the take judged at NOW, the clock these resets are counted from, not the clock when the case runs: a long
        # run reaches the case more than an hour after NOW, past a window's reset at NOW + 3600
        monkeypatch.setattr(ctl, "_now", lambda: NOW)
    answer = send(ws, a)["attempt"]
    assert (answer["taken"], answer["cause"]) == (False, cause), answer
    assert answer["why"]


def test_the_side_s_answer_at_an_attempt_s_take_refuses_it_with_its_kind(ws, monkeypatch):
    """cannot_run's answer at an attempt's take refuses it, `account` where the
    workspace has no account of the side's id (the owner's), `side` otherwise."""
    m, rid = managed(ws)
    monkeypatch.setattr(ws.controller, "cannot_run", lambda given: "The workspace has no Claude Agent SDK.")
    answer = send(ws, an_attempt(rid))["attempt"]
    assert (answer["taken"], answer["cause"]) == (False, "side")
    monkeypatch.setattr(ws.controller, "_cannot_run", lambda given: ("No account by that id.", "account"))
    answer = send(ws, an_attempt(rid))["attempt"]
    assert (answer["taken"], answer["cause"]) == (False, "account")


def test_a_window_the_account_s_status_line_shows_spent_refuses_an_attempt_at_its_take_and_its_chat_s_start(
        ws, monkeypatch):
    """The status lines are read off the agent's loop
    before the locks: the account's newest status line showing its window spent refuses an attempt taken while it was
    open, at its chat's start, waiting until its reset, and the next one at its take."""
    monkeypatch.setattr(ctl, "_now", lambda: NOW)      # at NOW, as the take's case above: its reset is NOW + 3600.5
    m, rid = managed(ws)
    attempt = taken(ws, rid)
    monkeypatch.setattr(ws.controller, "_newest",          # a reset the status line keeps as a float
                        lambda: {"five_hour": {"resets_at": NOW + 3600.5, "used_percentage": 100}})
    _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    asyncio.run(ws.controller.keep_attempts())
    assert (attempt["state"], attempt["outcome"], attempt["cause"]) == ("ended", "refused", "window")
    assert attempt["waits_until"] == NOW + 3600 and type(attempt["waits_until"]) is int     # as the app reads one
    answer = send(ws, an_attempt(rid, fence=2))["attempt"]
    assert (answer["taken"], answer["cause"]) == (False, "window"), answer


def test_an_attempt_sent_to_a_side_whose_account_the_workspace_lacks_is_refused_account(ws, monkeypatch):
    """A side the workspace took and refused for an account it does not have
    (412, kept as the record's refusal) refuses an attempt `account`, the owner's, through the real
    cannot_run, not `side`; a side refused for another reason refuses it `side`."""
    from proofpane_workspace import accounts as accounting
    from proofpane_workspace.workspace import WorkspaceError
    m, rid = managed(ws)
    real = ws.accounts.for_session

    def no_account(kind, account):
        raise accounting.AccountError("no such account")
    monkeypatch.setattr(ws.accounts, "for_session", no_account)
    with pytest.raises(ctl.NotHere):
        take(ws, dict(listed(m, rid), account="0123456789ab", version=m["version"] + 5))
    assert ws.controller.record["refused"]
    answer = send(ws, an_attempt(rid))["attempt"]
    assert (answer["taken"], answer["cause"]) == (False, "account"), answer
    # the account there again, the side refused for its chat's model and effort instead: `side`
    monkeypatch.setattr(ws.accounts, "for_session", real)

    def no_model(given):
        raise WorkspaceError("no such model")
    monkeypatch.setattr(ws, "chat_settings", no_model)
    with pytest.raises(ctl.NotHere):
        take(ws, dict(listed(m, rid), version=m["version"] + 6))
    assert ws.controller.record["refused"]
    answer = send(ws, an_attempt(rid))["attempt"]
    assert (answer["taken"], answer["cause"]) == (False, "side"), answer


def test_the_managers_states_are_kept_with_their_version_and_given_on_the_page(ws):
    m, rid = managed(ws)
    state = {"requirement": rid, "share": 2, "state": "ready", "waits": None, "waits_until": None, "attempts": 0}
    assert send(ws, version=7, managers=[state]) == {"held": 7, "attempt": None}
    assert ws.controller.record["manager_states"] == {rid: state}
    assert page(ws)["managers_held"] == 7


def test_a_managers_route_of_another_form_is_refused_naming_its_field(ws):
    for body, words in (({"version": 1, "managers": []}, "The managers' route has no attempt."),
                        ({"version": "1", "managers": [], "attempt": None}, "The managers' route's version is not"),
                        ({"version": 1, "managers": [{"requirement": "x"}], "attempt": None},
                         "The managers' route's managers[0].requirement is not")):
        with pytest.raises(ctl.ControllerError) as e:
            asyncio.run(ws.controller.take_managers(body))
        assert str(e.value).startswith(words), str(e.value)


# ---- an attempt's chat -----------------------------------------------------------------------------------------------

def _sends(w, monkeypatch):
    sent = []

    async def chat_send(sid, text, sender="owner"):
        sent.append((sid, text, sender))
        return {}
    monkeypatch.setattr(w, "chat_send", chat_send)
    return sent


def _closes(w, monkeypatch):
    closed = []

    async def close(sid):
        closed.append(sid)
    monkeypatch.setattr(w.controller, "_close_chat", close)
    return closed


def running(w, monkeypatch, requirement, **over):
    """An attempt taken and its chat started by the loop's round, its sends kept and its close stood in for: (its
    entry, the messages sent, the chats closed)."""
    attempt = taken(w, requirement, **over)
    sent, closed = _sends(w, monkeypatch), _closes(w, monkeypatch)
    asyncio.run(w.controller.keep_attempts())
    assert attempt["state"] == "running", attempt
    return attempt, sent, closed


def ended(w, attempt):
    """The loop's next round, which records a marked end."""
    asyncio.run(w.controller.keep_attempts())
    return attempt["state"], attempt["outcome"], attempt["cause"]


def call(w, attempt, name, args=None):
    """One of an attempt's five tools, as its chat calls it (the controller's attempt_tool, which the tools of its chat
    call, by ATTEMPT_CALLS): (the answer, whether it is a refusal)."""
    out = asyncio.run(w.controller.attempt_tool(attempt["id"], name, args or {}))
    text = out["content"][0]["text"]
    return (text, True) if out.get("is_error") else (json.loads(text), False)


def test_the_loop_starts_a_taken_attempt_s_chat_from_its_brief_and_packet_and_lets_the_packet_go(ws, monkeypatch):
    m, rid = managed(ws)
    attempt, sent, _ = running(ws, monkeypatch, rid)
    sid = attempt["session"]
    assert "packet" not in attempt and attempt["primed"] is True and attempt["started_at"]
    assert sid in ws.controller.record["attempt_sessions"]
    assert ws.controller.is_attempt(sid) and ws.controller.is_chat(sid)
    b64, _ = manager_packet()
    assert sent == [(sid, ctl.ATTEMPT_BRIEF + "\n\n" + base64.b64decode(b64).decode(), "workspace")]
    record = ws._read(sid)
    assert (record["program"], record["task"], record["repo"]) == ("chat", ctl.ATTEMPT_TITLE, None)


def test_an_attempt_s_chat_is_the_controller_s_but_for_its_mark_with_five_tools_and_its_bounds(ws, monkeypatch):
    """No PP_CONTROLLER_CHAT, never resumed, no built-in tool, no MCP server but its own,
    no settings read from files, its five tools, and its turns and spend bounded at its one start."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    c = ws.chat(attempt["session"])
    assert "PP_CONTROLLER_CHAT" not in c.env
    assert (c.resumes, c.base_tools, c.setting_sources, c.send_most) == (False, [], [], ctl.MESSAGE_MAX)
    assert c.mcp_plan() == {"off": [], "strict": True, "servers": []}
    assert c.options() == {"max_turns": ctl.ATTEMPT_TURNS, "max_budget_usd": ctl.ATTEMPT_SPEND_USD} == {
        "max_turns": 40, "max_budget_usd": 5}
    assert c.own_tools({})["allowed_tools"] == [f"mcp__proofpane__{t}" for t in (
        "start_task", "stop_task", "continue_task", "read_tree", "end_attempt")]


def test_an_attempt_s_claude_starts_once_and_its_chat_takes_no_message_settings_or_interrupt_from_the_page(
        ws, monkeypatch):
    from proofpane_workspace.workspace import Workspace, WorkspaceError
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    sid = attempt["session"]
    assert ws.controller.start_refusal(sid).startswith("An attempt of a requirement's manager runs once")
    for call_, words in ((Workspace.chat_send(ws, sid, "Do this instead."), ctl.NO_MESSAGE),
                         (ws.chat_choose(sid, {"model": "sonnet"}), ctl.NO_CONTROL),
                         (ws.chat_interrupt(sid), ctl.NO_CONTROL)):
        with pytest.raises(WorkspaceError) as e:
            asyncio.run(call_)
        assert str(e.value) == words


# ---- its tools -------------------------------------------------------------------------------------------------------

def test_an_attempt_starts_within_its_share_its_start_carrying_its_id_fence_and_text(ws, monkeypatch):
    m, rid = managed(ws, cap=3, share=1)
    attempt, _, _ = running(ws, monkeypatch, rid)
    got, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "Edit README.md."})
    assert not error, got
    assert ws.controller.record["tasks"][got["task"]]["attempt"] == attempt["id"]
    [action] = [a for a in page(ws)["actions"] if a["kind"] == "start"]
    assert (action["attempt"], action["fence"], action["text"], action["requirement"]) == (
        attempt["id"], 1, "Edit README.md.", rid)
    words, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "Two."})
    assert error and words == "The requirement has 1 sessions open, its share of the cap: stop one first."
    assert (attempt["refused"], attempt["refused_count"]) == (["share"], 1)


def test_a_managed_task_s_first_prompt_says_its_manager_wrote_its_task(ws, monkeypatch):
    """A managed requirement's task's first prompt opens with its manager and gives its part 2 as the requirement
    the controller wrote and the task its manager wrote, a model's words; the controller's own tasks' are as before."""
    from test_controller_workspace import _Term
    m, rid = managed(ws, cap=3, share=1)
    attempt, _, _ = running(ws, monkeypatch, rid)
    got, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "Edit README.md."})
    assert not error, got
    prompt = _Term.seen[-1].argv[-1]
    assert prompt.startswith("This session was started by the manager the owner's Manager keeps for one of its "
                             "requirements. What follows is in three marked parts.")
    assert ("Part 2, the requirement as the owner's Manager wrote it and this task as the requirement's manager wrote "
            "it: a model's words, not the owner's, which never set the owner's words aside, each a JSON string.") in prompt
    assert prompt.count("Edit README.md.") == 1


def test_an_attempt_refused_by_the_cap_is_told_others_fill_it_and_keeps_the_cause(ws, monkeypatch):
    m, rid, mine = managed(ws, cap=2, share=2, own=True)
    _task(ws, "idle", requirement=mine)
    attempt, _, _ = running(ws, monkeypatch, rid)
    got, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "One."})
    assert not error, got
    words, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "Two."})
    assert error and words == ("The cap is 2, and 2 sessions are open, other requirements' or the Manager's among "
                               "them: end this attempt waiting.")
    assert attempt["refused"] == ["cap"]


def test_an_attempt_acts_on_its_requirement_s_tasks_alone(ws, monkeypatch):
    m, rid, mine = managed(ws, own=True)
    other = _task(ws, "waiting", requirement=mine, waits_until=NOW - 3600)
    attempt, _, _ = running(ws, monkeypatch, rid)
    for name in ("stop_task", "continue_task"):
        words, error = call(ws, attempt, name, {"task": other["id"]})
        assert error and words == "That task is not one of this requirement's.", (name, words)
    tree, error = call(ws, attempt, "read_tree")
    assert not error and [r["id"] for r in tree["requirements"]] == [rid]
    assert tree["attempt"] == {"id": attempt["id"], "n": 1, "fence": 1, "share": 2}
    assert tree["controller"]["open"] == 1


def test_an_attempt_s_stop_marks_who_stopped_and_one_that_does_not_go_through_lets_it_go(ws, monkeypatch):
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    got, _ = call(ws, attempt, "start_task", {"repo": "demo", "task": "One."})
    t = ws.controller.record["tasks"][got["task"]]
    real = ws.controller._stop_program

    async def stuck(task):
        return False
    monkeypatch.setattr(ws.controller, "_stop_program", stuck)
    words, error = call(ws, attempt, "stop_task", {"task": t["id"]})
    assert error and words == "The session's program did not stop: try again."
    assert "stopped_by" not in t and t["stopping"] is True
    monkeypatch.setattr(ws.controller, "_stop_program", real)
    got, error = call(ws, attempt, "stop_task", {"task": t["id"]})
    assert not error and t["state"] == "stopped" and t["stopped_by"] == attempt["id"]
    [stop] = [a for a in page(ws)["actions"] if a["kind"] == "stop"]
    assert (stop["attempt"], stop["fence"]) == (attempt["id"], 1)
    assert next(x for x in page(ws)["tasks"] if x["id"] == t["id"])["stopped_by"] == attempt["id"]


def test_end_attempt_answers_first_and_the_loop_records_its_end_its_tools_then_refusing_but_two(ws, monkeypatch):
    m, rid = managed(ws)
    attempt, _, closed = running(ws, monkeypatch, rid)
    words, error = call(ws, attempt, "end_attempt", {"outcome": "needs_owner"})
    assert error and words == "needs_owner needs a note: say what the owner is to decide or do."
    got, _ = call(ws, attempt, "start_task", {"repo": "demo", "task": "One."})
    words, error = call(ws, attempt, "end_attempt", {"outcome": "done"})
    assert error and words == "Its tasks are not all ended: stop each, or wait for it to end, first."
    words, error = call(ws, attempt, "end_attempt", {"outcome": "waiting", "note": "ghp_" + "a" * 36})
    assert error and "shaped like" in words
    got, error = call(ws, attempt, "end_attempt", {"outcome": "waiting", "note": "Waiting on its build."})
    assert (got, error) == ({"attempt": attempt["id"], "ended": "waiting"}, False)
    assert attempt["state"] == "running" and attempt["ending"]["outcome"] == "waiting"
    words, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "Two."})
    assert error and "has ended" in words
    assert ended(ws, attempt) == ("ended", "waiting", None)
    assert attempt["note"] == "Waiting on its build." and closed == [attempt["session"]]
    for name in ("start_task", "continue_task", "end_attempt"):
        words, error = call(ws, attempt, name, {"repo": "demo", "task": "x", "outcome": "done"})
        assert error and "has ended" in words, name
    tree, error = call(ws, attempt, "read_tree")
    assert not error and tree["requirements"][0]["id"] == rid


def test_a_newer_attempt_of_its_manager_refuses_every_tool_of_an_older(ws, monkeypatch):
    m, rid = managed(ws)
    first, _, _ = running(ws, monkeypatch, rid)
    call(ws, first, "end_attempt", {"outcome": "waiting"})
    ended(ws, first)
    second = taken(ws, rid, fence=2, n=2)
    assert ws.controller._current(second) and not ws.controller._current(first)
    for name in ("read_tree", "stop_task"):
        words, error = call(ws, first, name, {"task": uid()})
        assert error and words == ctl.NOT_CURRENT


# ---- its ends --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("event,outcome,cause", [
    ({"subtype": "success", "error": False}, "waiting", "turn"),
    ({"subtype": "error_max_turns", "error": True}, "failed", "turns"),
    ({"subtype": "error_max_budget_usd", "error": True}, "failed", "spend"),
    ({"subtype": "error_during_execution", "error": True}, "failed", "error"),
])
def test_an_attempt_s_turn_end_with_no_end_of_its_own_ends_it_so(ws, monkeypatch, event, outcome, cause):
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    ws.controller._attempt_turn_end(attempt["id"], event)
    assert ended(ws, attempt) == ("ended", outcome, cause)


def test_a_rate_limit_ends_an_attempt_waiting_for_its_window_and_never_marks_the_controller_s_chat(ws, monkeypatch):
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    ws.controller._attempt_rate(attempt["id"], {"status": "rejected", "rate_limit_type": "five_hour",
                                                "resets_at": NOW + 3600})
    assert ws.controller.cut_off is False
    assert ws.controller.rates["five_hour"]["status"] == "rejected"
    assert ended(ws, attempt) == ("ended", "waiting", "window") and attempt["waits_until"] == NOW + 3600


def test_a_compaction_and_a_lost_claude_each_end_an_attempt(ws, monkeypatch):
    class SystemMessage:
        subtype = "compact_boundary"
    m, rid = managed(ws)
    first, _, _ = running(ws, monkeypatch, rid)
    ws.controller._attempt_message(first["id"], SystemMessage())
    assert ended(ws, first) == ("ended", "waiting", "compacted")
    second = taken(ws, rid, fence=2, n=2)
    asyncio.run(ws.controller.keep_attempts())
    ws.controller._attempt_closed(second["id"], None, True)
    assert ended(ws, second) == ("ended", "failed", "lost")


def test_an_attempt_past_its_time_is_ended_failed(ws, monkeypatch):
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    attempt["started_at"] -= ctl.ATTEMPT_SECONDS
    assert ended(ws, attempt) == ("ended", "failed", "time")


@pytest.mark.parametrize("how", ["stop", "close", "remove"])
def test_the_owner_s_stop_of_an_attempt_s_session_ends_it_stopped_owner(ws, monkeypatch, how):
    """The owner's Stop, Close or Remove of an attempt's session, each of which stops it through
    Workspace.stop and the controller's `stopping`, ends it `stopped` (`owner`), marked, waiting on no lock."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)

    async def held():
        # the start lock held, as Remove's route holds it and the loop holds it while an attempt's chat starts: the
        # owner's stop marks the end and waits on no lock
        async with ws.controller._start_lock():
            await asyncio.wait_for(getattr(ws, how)(attempt["session"]), 10)
    asyncio.run(held())
    assert attempt["ending"]["cause"] == "owner"
    assert ended(ws, attempt) == ("ended", "stopped", "owner")


@pytest.mark.parametrize("change,cause", [("off", "off"), ("stopped", "stopped"), ("end_all", "end_all"),
                                          ("hold", "held"), ("close", "closed"), ("account", "account"),
                                          ("side", "side")])
def test_the_owner_s_side_ends_a_running_attempt_with_its_cause_under_the_mirror_s_take(
        ws, monkeypatch, change, cause):
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    side = listed(m, rid)
    side["version"] += 5
    if change in ("off", "stopped"):
        side["state"] = change
    elif change == "end_all":
        side.update(end_all=m["end_all"] + 1, state="stopped")        # as the app's End all sends it
    elif change == "hold":
        side = listed(m, rid, hold=True)
    elif change == "close":
        side = listed(m, rid, state="done")
    elif change == "account":
        monkeypatch.setattr(ws.controller, "cannot_run", lambda given: None)
        side["account"] = "0123456789ab"
    elif change == "side":
        monkeypatch.setattr(ws.controller, "cannot_run", lambda given: "The chat cannot run with that model.")
    with pytest.raises(ctl.NotHere) if change == "side" else _nothing():
        take(ws, side)
    assert (attempt["state"], attempt["outcome"], attempt["cause"]) == ("ended", "stopped", cause)


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_packet_is_let_go_at_its_attempt_s_end_before_its_chat_started(ws):
    """The record holds at most one packet."""
    m, rid = managed(ws)
    attempt = taken(ws, rid)
    assert "packet" in attempt
    take(ws, dict(listed(m, rid), state="off", version=m["version"] + 9))
    assert (attempt["state"], attempt["outcome"], attempt["cause"]) == ("ended", "stopped", "off")
    assert "packet" not in attempt


def test_a_restart_ends_a_running_attempt_stopped_restart(ws, monkeypatch):
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    again = ctl.Controller(ws)
    a = again.record["attempts"][attempt["id"]]
    assert (a["state"], a["outcome"], a["cause"]) == ("ended", "stopped", "restart")
    assert "manager_states" not in again.record and again.record.get("managers_held") is None


def test_a_restart_records_an_attempt_s_marked_end_as_marked_its_note_kept(ws, monkeypatch):
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    call(ws, attempt, "end_attempt", {"outcome": "done", "note": "All of it is done."})
    again = ctl.Controller(ws)
    a = again.record["attempts"][attempt["id"]]
    assert (a["state"], a["outcome"], a["cause"], a["note"]) == ("ended", "done", None, "All of it is done.")


# ---- the page and the record -----------------------------------------------------------------------------------------

def test_an_attempt_is_listed_running_until_the_page_that_gives_its_end(ws, monkeypatch):
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    first = page(ws)
    [shown] = first["attempts"]
    assert (shown["id"], shown["state"], shown["outcome"]) == (attempt["id"], "running", None)
    with ws.controller._lock:
        for _ in range(ctl.PAGE + 1):                   # one more than a page holds: the page is cut
            ws.controller._act("question", question=uid(), requirement=None, text="q")
    ws.controller._attempt_turn_end(attempt["id"], {"subtype": "success"})
    ended(ws, attempt)
    cut = page(ws, first["seq"])
    assert cut["more"] is True and [a["state"] for a in cut["attempts"]] == ["running"]
    rest = page(ws, cut["seq"])
    [shown] = rest["attempts"]
    assert (shown["state"], shown["outcome"], shown["cause"]) == ("ended", "waiting", "turn")
    assert page(ws, rest["seq"])["attempts"] == []


def test_a_start_s_text_is_kept_until_the_app_reads_past_it_and_for_TEXTS_KEPT_unread_at_most(ws):
    with ws.controller._lock:
        acts = [ws.controller._act("start", task=uid(), text=f"task {i}") for i in range(ctl.TEXTS_KEPT + 1)]
        ws.controller._kept_texts()
    assert ["text" in a for a in acts] == [False] + [True] * ctl.TEXTS_KEPT
    page(ws, acts[5]["seq"])                            # the app read past the first six
    with ws.controller._lock:
        ws.controller._kept_texts()
    assert ["text" in a for a in acts] == [False] * 6 + [True] * (len(acts) - 6)
    assert all("text" not in a for a in page(ws)["actions"][:6])


def test_an_attempt_keeps_its_refusals_causes_once_and_eight_models_at_most(ws):
    m, rid = managed(ws)
    attempt = taken(ws, rid)
    with ws.controller._lock:
        for i in range(12):
            ws.controller._attempt_refused(attempt, "pace", f"model-{i % 10}")
        ws.controller._attempt_refused(attempt, "cap", None)
    assert (attempt["refused"], attempt["refused_count"]) == (["pace", "cap"], 13)
    assert attempt["refused_models"] == [f"model-{i}" for i in range(ctl.REFUSED_MODELS_MOST)]


def test_an_attempt_running_and_a_manager_waiting_for_a_known_reset_keep_the_machine_up(ws, monkeypatch):
    monkeypatch.setattr(ctl, "_now", lambda: NOW)      # at NOW: the manager waits for a reset at NOW + 600
    m, rid = managed(ws)
    assert ws.controller.busy() is False
    attempt, _, _ = running(ws, monkeypatch, rid)
    assert ws.controller.busy() is True
    call(ws, attempt, "end_attempt", {"outcome": "waiting"})
    ended(ws, attempt)
    assert ws.controller.busy() is False
    send(ws, version=2, managers=[{"requirement": rid, "share": 2, "state": "waiting", "waits": "window",
                                    "waits_until": NOW + 600, "attempts": 1}])
    assert ws.controller.busy() is True


def test_the_sessions_of_older_attempts_are_removed_each_id_let_go_once_its_removal_went_through(ws, monkeypatch):
    monkeypatch.setattr(ctl, "ATTEMPT_SESSIONS_KEPT", 1)
    m, rid = managed(ws)
    first, _, _ = running(ws, monkeypatch, rid)
    call(ws, first, "end_attempt", {"outcome": "waiting"})
    ended(ws, first)
    second = taken(ws, rid, fence=2, n=2)
    removed, fails = [], [True]

    async def remove(sid):
        if fails:
            fails.pop()
            raise OSError("its record is unreadable")
        removed.append(sid)
    monkeypatch.setattr(ws, "remove", remove)
    asyncio.run(ws.controller.keep_attempts())          # the second's chat started; the first's removal fails
    assert removed == [] and first["session"] in ws.controller.record["attempt_sessions"]
    assert ws.controller.is_attempt(first["session"])
    asyncio.run(ws.controller.keep_attempts())
    assert removed == [first["session"]]
    assert ws.controller.record["attempt_sessions"] == [second["session"]]


def test_the_page_at_its_largest_is_within_what_the_app_reads_of_an_answer(ws):
    """Actions to PAGE_BYTES, PAGE tasks' states and PAGE attempts' states, each at its largest (a
    round of ten checks not run, the longest of the workspace's words; an attempt's four refused causes, eight models
    of 100 characters and a note of NOTE_MAX backslashes, each two bytes in the page's JSON, the most a note that
    _model_text takes can be there), within workspace_client.MAX_REPLY, the
    bytes the app reads of an answer, as Starlette's JSONResponse writes the page (UTF-8, no spaces)."""
    from proofpane_open.ade import controller as app, workspace_client
    big = (1 << 53) - 1
    why = max(app.CHECK_NOT_RUN, key=len)
    end_why = max(app.CHECK_ENDS, key=len)
    rnd = {"round": 10, "head": "f" * 64, "at": big, "passed": False,
           "results": [{"id": i, "outcome": "not run", "why": why} for i in range(1, 11)]}
    with ws.controller._lock:
        rec = ws.controller._managed_record()
        for i in range(ctl.PAGE):
            t = {"id": uid(), "session": "a" * 12, "requirement": uid(), "repo": "demo", "base": None, "branch": None,
                 "model": "opus", "effort": "max", "account": "default", "state": "failed", "waits_until": big,
                 "ended_at": big, "started_at": big, "seq": ws.controller._next(), "checks": rnd, "why": end_why,
                 "needs_owner": True, "state_seq": big, "stopped_by": uid()}
            rec["tasks"][t["id"]] = t
        note = "\\" * ctl.NOTE_MAX
        assert ws.controller._model_text(note, ctl.NOTE_MAX, "The note") == note     # one the tool takes
        for i in range(ctl.PAGE):
            a = {"id": uid(), "requirement": uid(), "fence": big, "n": big, "taken": True, "state": "ended",
                 "outcome": "needs_owner", "cause": "compacted", "waits_until": big, "refused": ["cap", "sessions",
                 "pace", "share"], "refused_count": big, "refused_models": ["m" * 100] * 8, "note": note,
                 "session": "b" * 12, "started_at": big, "ended_at": big, "seq": ws.controller._next()}
            rec["attempts"][a["id"]] = a
        # four-byte characters, so that PAGE_BYTES ends the actions before PAGE does
        item = {"id": uid(), "kind": "question", "at": big, "question": uid(), "requirement": uid(),
                "text": "\U0001F600" * ctl.TASK_MAX}
        size = 0
        while size < ctl.PAGE_BYTES:
            rec["actions"].append(dict(item, id=uid(), seq=ws.controller._next()))
            size += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
    got = page(ws, 0)
    assert len(got["tasks"]) == ctl.PAGE and len(got["attempts"]) == ctl.PAGE and got["more"] is True
    assert len(json.dumps(got["actions"], ensure_ascii=False).encode("utf-8")) > ctl.PAGE_BYTES - 20_000
    # as the agent's answer writes it (Starlette's JSONResponse: UTF-8, as PAGE_BYTES measures the actions)
    data = json.dumps(got, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    assert len(data) <= workspace_client.MAX_REPLY, len(data)


def _closures(w, monkeypatch, attempt):
    """The five tools as the attempt's chat is given them (_attempt_tools), each its handler, by name."""
    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "create_sdk_mcp_server", lambda name, tools=None, **kw: {"tools": tools})
    got = w.controller._attempt_tools(attempt["id"])["mcp_servers"][ctl.OWN]["tools"]
    return {t.name: t.handler for t in got}


@pytest.mark.parametrize("change", ["off", "stopped", "end_all", "hold", "close"])
def test_after_the_owner_s_side_ends_an_attempt_its_tools_refuse_but_read_tree_and_stop_task(ws, monkeypatch, change):
    """Off, Stop, End all, a Hold and the owner's close each end a running
    attempt, its tools then refusing but read_tree and stop_task, called as its chat calls them."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    got, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "One."})
    assert not error, got
    side = listed(m, rid)
    side["version"] += 5
    if change in ("off", "stopped"):
        side["state"] = change
    elif change == "end_all":
        side.update(end_all=m["end_all"] + 1, state="stopped")
    elif change == "hold":
        side = listed(m, rid, hold=True)
    else:
        side = listed(m, rid, state="done")
    take(ws, side)
    assert attempt["state"] == "ended"
    tools = _closures(ws, monkeypatch, attempt)

    def said(name, args):
        out = asyncio.run(tools[name](args))
        return out["content"][0]["text"], bool(out.get("is_error"))
    for name, args in (("start_task", {"repo": "demo", "task": "Two."}), ("continue_task", {"task": got["task"]}),
                       ("end_attempt", {"outcome": "waiting"})):
        assert said(name, args)[1] is True, name
    text, error = said("read_tree", {})
    assert not error and json.loads(text)["attempt"]["id"] == attempt["id"]
    text, error = said("stop_task", {"task": got["task"]})
    assert "This attempt has ended" not in text and ctl.NOT_CURRENT not in text
    assert not error or text == "That task has ended already."


def test_a_managed_task_s_round_of_checks_tells_the_controller_s_chat_nothing_and_its_own_task_s_does(ws):
    """A managed requirement's checks' results never reach the controller's chat (they are its manager's): a round
    kept for its task puts no wake among the chat's; one of the controller's own does."""
    m, rid, mine = managed(ws, own=True)
    t = _task(ws, "idle", requirement=rid)
    own = _task(ws, "idle", requirement=mine)
    ws.controller.wakes.clear()
    with ws.controller._lock:
        ws.controller._keep_round(t, "a" * 40, [{"id": 1, "outcome": "passed"}])
    assert ws.controller.wakes == {}
    with ws.controller._lock:
        ws.controller._keep_round(own, "b" * 40, [{"id": 1, "outcome": "passed"}])
    assert [k for k in ws.controller.wakes if own["id"] in k]


def test_a_managed_task_s_reset_passed_tells_the_controller_s_chat_nothing_and_its_own_task_s_does(ws, monkeypatch):
    """_observe's wake for a reset that passed is the controller's chat's for its own tasks alone: a managed task's
    wakes its manager through the app's sweep (its `reset` wake), never the chat."""
    import time
    from test_controller_workspace import _limit, _passed, _status
    m, rid, mine = managed(ws, own=True)
    attempt, _, _ = running(ws, monkeypatch, rid)
    got, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "One."})
    assert not error, got
    own = start(ws, mine)
    tasks = ws.controller.record["tasks"]
    for g in (got, own):
        _status(ws, g["session"], 100, NOW + 3600)
        _limit(ws, g["session"], "rate_limit", at=int(time.time()) + 1)
    ws.controller.refresh()
    assert tasks[got["task"]]["state"] == tasks[own["task"]]["state"] == "waiting"
    _passed(ws, (got, tasks[got["task"]]), (own, tasks[own["task"]]))
    ws.controller.wakes.clear()
    ws.controller.refresh()
    assert got["task"] not in ws.controller.wakes and ws.controller.wakes[own["task"]]["reset_passed"] is True


def test_the_controller_s_capped_start_is_kept_only_where_tasks_it_may_not_stop_share_the_cap(ws, monkeypatch):
    """With its own tasks alone in the cap, a refused start of the controller's
    keeps no `capped_since`, which would tell the owner it waits and count it among those waiting; with a managed
    task in it, it does."""
    m, rid, mine = managed(ws, cap=2, own=True)
    _task(ws, "idle", requirement=mine)
    _task(ws, "idle", requirement=mine)
    refused(ws.controller.start_task, {"requirement": mine, "repo": "demo", "task": "Three."})
    assert ws.controller.record.get("capped_since") is None
    t = next(t for t in ws.controller.record["tasks"].values() if t["state"] not in ctl.TASK_ENDED)
    with ws.controller._lock:
        ws.controller._set(t, "done", NOW)
    _task(ws, "idle", requirement=rid)
    refused(ws.controller.start_task, {"requirement": mine, "repo": "demo", "task": "Three."})
    assert ws.controller.record["capped_since"] is not None


def _ended_attempt(w, rec, **over):
    a = dict({"id": uid(), "requirement": uid(), "fence": 1, "n": 1, "taken": True, "state": "ended",
              "outcome": "waiting", "cause": None, "refused": [], "refused_count": 0, "refused_models": [],
              "note": None, "session": None, "started_at": NOW, "ended_at": NOW, "seq": w.controller._next()}, **over)
    rec["attempts"][a["id"]] = a
    return a


def test_the_page_cuts_the_ended_attempts_as_it_cuts_the_tasks_ended_states_and_moves_its_seq(ws, monkeypatch):
    """At most PAGE less those not ended, the ended ones cut, the last one given among the page's
    cuts, so that it moves the page's `seq` and `more` and none falls below the next `since`."""
    monkeypatch.setattr(ctl, "PAGE", 3)
    with ws.controller._lock:
        rec = ws.controller._managed_record()
        made = [_ended_attempt(ws, rec) for _ in range(5)]
    got = page(ws, 0)
    assert [x["id"] for x in got["attempts"]] == [a["id"] for a in made[:3]]
    assert (got["more"], got["seq"]) == (True, made[2]["seq"])
    again = page(ws, got["seq"])
    assert [x["id"] for x in again["attempts"]] == [a["id"] for a in made[3:]] and again["more"] is False


def test_when_an_attempt_and_its_note_are_let_go(ws):
    """A note once the app has read past its attempt's end; a taken attempt KEPT_ATTEMPTS after its end once the app
    has read past it; a refused one KEPT_ATTEMPTS after its refusal, whatever the app has read."""
    old = NOW - ctl.KEPT_ATTEMPTS - 10
    with ws.controller._lock:
        rec = ws.controller._managed_record()
        read_old = _ended_attempt(ws, rec, note="a", ended_at=old)
        read_new = _ended_attempt(ws, rec, note="b")
        ws.controller.acked = read_new["seq"]               # the app has read past these two
        unread_old = _ended_attempt(ws, rec, note="c", ended_at=old)
        refused = _ended_attempt(ws, rec, taken=False, outcome="refused", cause="busy", ended_at=old)
        del refused["seq"]
        ws.controller._prune(NOW)
    attempts = ws.controller.record["attempts"]
    assert read_old["id"] not in attempts and refused["id"] not in attempts
    assert attempts[read_new["id"]]["note"] is None
    assert attempts[unread_old["id"]]["note"] == "c"


def test_the_controller_s_capped_start_is_let_go_at_its_next_start(ws, monkeypatch):
    m, rid, mine = managed(ws, cap=3, own=True)
    _task(ws, "idle", requirement=rid)
    ws.controller.record["capped_since"] = NOW - 60
    start(ws, mine)
    assert ws.controller.record.get("capped_since") is None
    assert page(ws)["room"]["capped_since"] is None


def test_an_attempt_s_timer_is_set_at_its_start_for_its_seconds_and_cancelled_at_its_end(ws, monkeypatch):
    """A timer set at its start pokes the loop at ATTEMPT_SECONDS, so that its end comes then and not up
    to a TICK later; its end cancels it."""
    import types

    class Loop:
        def __init__(self):
            self.later = []

        def call_later(self, delay, fn):
            handle = types.SimpleNamespace(cancelled=False)
            handle.cancel = lambda: setattr(handle, "cancelled", True)
            self.later.append((delay, fn, handle))
            return handle
    loop = Loop()
    ws.controller._loop = loop
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    [(delay, fn, handle)] = loop.later
    assert (delay, fn) == (ctl.ATTEMPT_SECONDS, ws.controller.poke) and handle.cancelled is False
    call(ws, attempt, "end_attempt", {"outcome": "waiting"})
    assert ended(ws, attempt) == ("ended", "waiting", None) and handle.cancelled is True


def test_a_note_reaches_no_task_s_first_prompt_nor_a_later_attempt(ws, monkeypatch):
    """A note reaches the owner alone, the workspace's half: an attempt's note is in no
    first prompt of a task its manager's next attempt starts, and in no message the workspace sends that attempt's
    chat."""
    from test_controller_workspace import _Term
    m, rid = managed(ws, cap=3, share=2)
    attempt, _, _ = running(ws, monkeypatch, rid)
    call(ws, attempt, "end_attempt", {"outcome": "needs_owner", "note": "Only for the owner."})
    ended(ws, attempt)
    nxt, sent, _ = running(ws, monkeypatch, rid, fence=2, n=2)
    got, error = call(ws, nxt, "start_task", {"repo": "demo", "task": "Edit README.md."})
    assert not error, got
    assert "Only for the owner." not in _Term.seen[-1].argv[-1]
    assert all("Only for the owner." not in str(x) for x in sent)


def test_a_claude_lost_while_its_attempt_starts_ends_it_lost_at_the_loop_s_next_round(ws, monkeypatch):
    """Its claude gone while the workspace sends its first message, before
    the loop makes it running, ends it `failed` (`lost`) at the next round, not past its time, so that it holds the
    owner's one lease no longer than that."""
    m, rid = managed(ws)
    attempt = taken(ws, rid)
    _closes(ws, monkeypatch)

    async def chat_send(sid, text, sender="owner"):
        ws.controller._attempt_closed(attempt["id"], None, True)       # its claude ends as the message goes
        return {}
    monkeypatch.setattr(ws, "chat_send", chat_send)
    asyncio.run(ws.controller.keep_attempts())
    assert attempt["ending"]["cause"] == "lost"
    assert ended(ws, attempt) == ("ended", "failed", "lost")


def test_a_done_marked_while_a_start_of_its_own_was_under_way_is_recorded_waiting(ws, monkeypatch):
    """end_attempt's done is judged under the record's lock alone, so a start
    of the same attempt's between its two checks is not yet among the tasks; recorded under the start lock, where its
    task is open by then, the end is `waiting`, which holds no place, not done."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    out, error = call(ws, attempt, "end_attempt", {"outcome": "done"})
    assert not error and attempt["ending"]["outcome"] == "done"
    _task(ws, "working", requirement=rid)                  # the start that was under way, its task written after
    assert ended(ws, attempt) == ("ended", "waiting", None)


def test_a_done_is_recorded_done_whatever_another_requirement_s_tasks_are(ws, monkeypatch):
    """Only a task of the attempt's own requirement, open when its end
    is recorded, makes its done `waiting`; another requirement's open task leaves it done."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    _task(ws, "working")                                   # another requirement's, open
    out, error = call(ws, attempt, "end_attempt", {"outcome": "done"})
    assert not error
    assert ended(ws, attempt) == ("ended", "done", None)


def test_a_first_message_its_chat_could_not_send_ends_the_attempt_failed_start_not_lost(ws, monkeypatch):
    """An attempt whose chat could not start: a send that fails calls the
    chat's on_closed before it raises (chat.py's send, by _lost, `ended` false); the attempt, still starting, is not
    marked lost, and the loop's start ends it `failed` (`start`)."""
    m, rid = managed(ws)
    attempt = taken(ws, rid)
    _closes(ws, monkeypatch)

    async def chat_send(sid, text, sender="owner"):
        ws.controller._attempt_closed(attempt["id"], RuntimeError("the send failed"), False)
        raise RuntimeError("the send failed")
    monkeypatch.setattr(ws, "chat_send", chat_send)
    asyncio.run(ws.controller.keep_attempts())
    assert (attempt["state"], attempt["outcome"], attempt["cause"]) == ("ended", "failed", "start")


def test_a_done_marked_and_recorded_by_the_mirror_s_take_with_a_task_of_its_open_is_recorded_waiting(ws, monkeypatch):
    """The second test of a marked `done` is made wherever a mark is
    recorded, so the owner's Off, taken by the mirror before the loop's next round, records a done whose requirement
    has a task open after all (a start under way when it was judged) as `waiting`, as the loop would."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    out, error = call(ws, attempt, "end_attempt", {"outcome": "done"})
    assert not error and attempt["ending"]["outcome"] == "done"
    _task(ws, "working", requirement=rid)                  # the start that was under way, its task written after
    take(ws, dict(listed(m, rid), state="off", version=m["version"] + 5))
    assert (attempt["state"], attempt["outcome"], attempt["cause"]) == ("ended", "waiting", None)


def test_continue_s_oldest_first_looks_among_the_caller_s_own_tasks_alone_in_each_scope(ws):
    """continue_task's "oldest first" looks only
    among the caller's own tasks: for an attempt, its requirement's, so that an older waiting task of another
    requirement neither holds its continue back nor is named to it; for the controller, its own, so that its refusal
    never names a managed requirement's task. An older one of the caller's own is still continued first."""
    import time
    m, rid, own = managed(ws, cap=3, share=1, own=True)
    other = plan(ws, m, share=1)                    # another requirement, which a manager of its own keeps
    take(ws, dict(listed(m, rid, own, other), version=m["version"] + 3))
    attempt = taken(ws, rid)
    now = int(time.time())
    side = ws.controller.record["mirror"]

    def waiting(requirement, age):
        return _task(ws, "waiting", requirement=requirement, waits_until=now - 3600, limit_at=now - age)
    others = waiting(other, 10_000)                 # the oldest, the other manager's
    first = waiting(rid, 9_000)
    own_first = waiting(own, 8_000)
    second = waiting(rid, 7_000)
    own_second = waiting(own, 6_000)
    may = ws.controller._may_continue
    assert others and may(side, first, now, "attempt", attempt) is None
    assert may(side, second, now, "attempt", attempt) == f"Continue the oldest first: task {first['id']}."
    assert may(side, own_first, now, "controller") is None
    assert may(side, own_second, now, "controller") == f"Continue the oldest first: task {own_first['id']}."


def test_paid_usage_past_no_window_of_the_plan_s_ends_an_attempt_with_no_reset_known(ws, monkeypatch):
    """An event of paid usage the CLI gives against no window of the plan's
    (`overage`) carries the overage's own reset, a month out, not the window's, which rate_limited holds five hours with
    none; the attempt it cuts off ends waiting for its window with no reset known, so that the room meets the wait."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    ws.controller._attempt_rate(attempt["id"], {"status": "allowed", "rate_limit_type": "overage",
                                                "resets_at": NOW + 30 * 86400, "is_using_overage": True})
    assert ws.controller.rates["five_hour"]["paid"] is True
    assert ended(ws, attempt) == ("ended", "waiting", "window") and attempt.get("waits_until") is None


def test_an_attempt_s_rate_limit_is_kept_for_the_account_its_chat_runs_on(ws, monkeypatch):
    """An attempt's rate-limit event is kept in the rates of the account its
    chat runs on, not of the controller's chat, which can still hold the account the owner moved from."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    assert attempt["account"] == "default"
    ws.controller.record["chat"] = dict(ws.controller.record["chat"] or {}, account="old")     # not yet made again
    ws.controller._attempt_rate(attempt["id"], {"status": "rejected", "rate_limit_type": "five_hour",
                                                "resets_at": NOW + 3600})
    assert ws.controller.rates["five_hour"]["account"] == "default"


def test_an_attempt_s_start_refused_for_the_workspace_s_sessions_is_kept_on_it_as_sessions(ws, monkeypatch):
    """The `sessions` wait, the workspace's half: an attempt's
    start refused because MAX_SESSIONS programs run is kept on the attempt as `sessions`, which its end gives the app."""
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    monkeypatch.setattr(ws, "running_programs", lambda: ws.max_sessions)
    words, error = call(ws, attempt, "start_task", {"repo": "demo", "task": "One."})
    assert error and words == f"At most {ws.max_sessions} programs run at once in the workspace."
    assert (attempt["refused"], attempt["refused_count"]) == (["sessions"], 1)


def test_an_attempt_s_continue_leaves_the_day_s_last_lines_to_the_controller_s_own(ws, monkeypatch):
    """The workspace refuses an attempt's continue once the day's lines
    reach LINES_A_DAY less LINES_KEPT, lines the controller's own continue still has."""
    import time
    m, rid = managed(ws)
    attempt, _, _ = running(ws, monkeypatch, rid)
    monkeypatch.setattr(ws.controller, "refresh", lambda now=None: None)      # the record as the test leaves it
    monkeypatch.setattr(ctl, "LINES_A_DAY", ctl.LINES_KEPT + 3)
    now = int(time.time())
    t = _task(ws, "waiting", requirement=rid, waits_until=now - 3600)
    with ws.controller._lock:
        for _ in range(3):
            ws.controller.record["actions"].append({"id": uid(), "kind": "plan", "at": now, "seq": 0})
    words, error = call(ws, attempt, "continue_task", {"task": t["id"]})
    assert error and words == (f"The Manager has done as much as the app takes in a day ({ctl.LINES_KEPT + 3} "
                               f"lines, {ctl.LINES_KEPT} of the lines kept for its own).")


@pytest.mark.parametrize("held,room", [
    ({}, ("open", None)),
    ({"five_hour": {"status": "rejected", "resets_at": NOW + 3600}}, ("spent", NOW + 3600)),
    ({"five_hour": {"status": "rejected", "resets_at": None}}, ("spent", None)),
    ({"five_hour": {"status": "rejected", "resets_at": NOW + 3600, "paid": True}}, ("paid", NOW + 3600)),
    ({"seven_day": {"status": "allowed_warning", "resets_at": NOW + 86400}}, ("reserve", NOW + 86400)),
    ({"seven_day_sonnet": {"status": "rejected", "resets_at": NOW + 3600}}, ("spent", NOW + 3600)),
    ({"seven_day_opus": {"status": "rejected", "resets_at": NOW + 3600}}, ("open", None)),
    ({"status": {"five_hour": {"resets_at": NOW + 600, "used_percentage": 100}}}, ("spent", NOW + 600)),
], ids=["open", "spent", "spent-no-reset", "paid", "reserve", "the-chat-model-s-own-week", "another-model-s-week",
        "a-status-line-at-100"])
def test_the_page_s_room_is_the_window_the_loop_measured_for_the_controller_s_chat_model(ws, monkeypatch, held, room):
    """The room the page gives is
    the controller's account's window as the loop's round measures it, through tick, for a start of the chat's model
    (here not the tasks' first): spent, on paid usage or held by the weekly reserve, with its reset where known, and
    open only where nothing holds such a start; the one thing that meets a manager's wait for its window with no reset
    known."""
    on(ws, chat={"model": "sonnet", "effort": "high"})
    held = dict(held)
    status = held.pop("status", None)
    monkeypatch.setattr(ws.controller, "_newest", lambda: status)
    for kind, r in held.items():
        ws.controller.rates[kind] = dict({"at": NOW, "account": "default", "paid": False}, **r)
    # at NOW, the clock its cases' resets are counted from, not the clock when the case runs: late in a long shard
    # that is more than ten minutes on, past the status line's reset at NOW + 600
    ws.controller.tick(NOW)
    got = page(ws)["room"]
    assert (got["window"], got["until"]) == room
