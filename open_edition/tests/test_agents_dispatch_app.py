"""Agents together through the app (ade/agents.py): the runs one run (or a chat) starts in the workspace are taken
into the store within bounds the store sets, never the workspace; each run shows who started it and the totals of
what it started."""

import json
import time

import pytest

from test_agents_app import A, agent, listing, owner, local, run_of, until  # noqa: F401  (fixtures)


def me(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def made_run(client, h, agent_id, monkeypatch, trigger=None):
    """A run the app made, not sent; its trigger as given."""
    from proofpane_open import store
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    run_id = client.post(f"{A}/{agent_id}/runs", headers=h, json={}).json()["run_id"]
    if trigger is not None:
        with store.connect() as db:
            db.execute("UPDATE ade_agent_runs SET trigger = ? WHERE run_id = ?", (json.dumps(trigger), run_id))
    return run_id


def dispatched(run_id, agent_id, parent, depth=1, **over):
    return dict({"run_id": run_id, "agent_id": agent_id, "state": "queued", "queued_at": int(time.time()),
                 "trigger": {"kind": "dispatch", "parent_run_id": parent, "depth": depth}}, **over)


def row(run_id):
    from proofpane_open import store
    with store.connect() as db:
        return db.execute("SELECT * FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()


def test_an_agent_s_dispatch_fields_are_kept_checked_and_mirrored(client, owner):
    from proofpane_open.ade import agents
    from proofpane_open import store
    made = agent(client, owner, may_dispatch=True, accepts_dispatch=True, max_dispatch_per_run=3)
    assert (made["may_dispatch"], made["accepts_dispatch"], made["max_dispatch_per_run"]) == (True, True, 3)
    plain = agent(client, owner, name="Labeler")
    assert (plain["may_dispatch"], plain["accepts_dispatch"], plain["max_dispatch_per_run"]) == (False, False, 5)
    for width in (0, 21, True, "5"):
        r = client.put(f"{A}/{made['id']}", headers=owner, json={"max_dispatch_per_run": width})
        assert r.status_code == 400, width
    r = client.put(f"{A}/{made['id']}", headers=owner, json={"may_dispatch": False})
    assert r.status_code == 200 and r.json()["may_dispatch"] is False and r.json()["accepts_dispatch"] is True
    with store.connect() as db:
        body = agents._mirror_body(db.execute("SELECT * FROM ade_agents WHERE id = ?", (made["id"],)).fetchone(),
                                   {"email": "ana@example.org"})
    assert (body["may_dispatch"], body["accepts_dispatch"], body["max_dispatch_per_run"]) == (False, True, 3)


def test_a_run_started_by_a_known_run_is_taken_one_deeper_as_the_store_counts(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    top_agent, child_agent = agent(client, owner, may_dispatch=True), agent(client, owner, name="Labeler")
    third = agent(client, owner, name="Reviewer")
    top = made_run(client, owner, top_agent["id"], monkeypatch)
    # the workspace says depth 0 and gives the child before its parent's own record: the store says 1, and takes it
    child, grand = "c" * 12, "d" * 12
    changed = agents._apply(user_id, [dispatched(grand, third["id"], child, depth=0, queued_at=9_000_000_000),
                                      dispatched(child, child_agent["id"], top, depth=0, state="done",
                                                 cost_est_usd=0.25, started_at=10, ended_at=40, dispatches=1),
                                      {"run_id": top, "agent_id": top_agent["id"], "state": "working",
                                       "dispatches": 1, "waiting_on": child}])
    assert changed == {top_agent["id"], child_agent["id"], third["id"]}
    assert (row(child)["parent_run_id"], row(child)["depth"], row(child)["sent"]) == (top, 1, 1)
    assert (row(grand)["parent_run_id"], row(grand)["depth"]) == (child, 2)
    assert json.loads(row(child)["trigger"])["from_agent"] == top_agent["id"]
    assert row(grand)["queued_at"] <= time.time()                         # not a time to come
    shown, _ = run_of(client, owner, top)
    assert (shown["dispatches"], shown["waiting_on"]) == (1, child)
    assert shown["subtree"] == {"runs": 2, "cost_est_usd": 0.25, "seconds": 30, "open": 1, "failed": 0}
    assert run_of(client, owner, grand)[0]["subtree"] is None
    # three deep is past the bound, whatever the workspace says: kept, flagged, and counted in its parent's tree
    agents._apply(user_id, [dispatched("e" * 12, child_agent["id"], grand, depth=2)])
    assert (row("e" * 12)["depth"], row("e" * 12)["flag"]) == (3, "started 3 deep, past 2")


def test_runs_of_an_unknown_parent_are_not_taken_and_runs_past_their_bounds_are_kept_flagged(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    top_agent, other = agent(client, owner, may_dispatch=True), agent(client, owner, name="Labeler")
    hooked = made_run(client, owner, top_agent["id"], monkeypatch, trigger={"kind": "hook"})
    top = made_run(client, owner, top_agent["id"], monkeypatch)
    from conftest import signed_up
    other_id = me(client, signed_up(client, "second-admin@example.com"))
    stranger = agents.create({"id": other_id, "email": "second-admin@example.com"}, {"name": "Theirs", "brief": "hi"})
    for view in (dispatched("a" * 12, other["id"], "f" * 12),                    # a parent nobody made
                 dispatched("b" * 12, other["id"], hooked),                      # a hook's run starts none
                 dispatched("c" * 12, top_agent["id"], top),                     # nor its own agent's
                 dispatched("d" * 12, stranger["id"], top),                      # another account's agent
                 dict(dispatched("e" * 12, other["id"], top), trigger={"kind": "manual", "parent_run_id": top}),
                 dispatched(top, other["id"], top)):                              # its own parent
        agents._apply(user_id, [view])
    for run_id in ("a" * 12, "d" * 12, "e" * 12):
        assert row(run_id) is None, run_id
    # the workspace started these under a run the store knows: shown and counted, flagged, and told
    assert row("b" * 12)["flag"] == "started by a run a hook started, which may start none"
    assert row("c" * 12)["flag"] == "started by a run of its own agent"
    told = dict(agents.alerts(user_id))
    assert told["agent-flagged:" + "b" * 12] == "agent-look"      # told in fixed words; the page says why
    assert row(top)["agent_id"] == top_agent["id"] and row(top)["parent_run_id"] is None


def test_at_most_the_parent_agent_s_width_are_taken_under_one_run(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    top_agent = agent(client, owner, may_dispatch=True, max_dispatch_per_run=2)
    other = agent(client, owner, name="Labeler")
    top = made_run(client, owner, top_agent["id"], monkeypatch)
    agents._apply(user_id, [dispatched(f"{n:012x}", other["id"], top) for n in range(1, 26)])
    from proofpane_open import store
    with store.connect() as db:
        kept = db.execute("SELECT flag FROM ade_agent_runs WHERE parent_run_id = ? ORDER BY run_id", (top,)).fetchall()
    assert [k["flag"] for k in kept[:2]] == [None, None]                 # its width, taken as they are
    assert {k["flag"] for k in kept[2:]} == {"one more than the 2 runs its parent may start"}
    assert len(kept) == 2 + agents.FLAGGED_MAX                             # and no more, whatever the workspace says


def test_a_chat_s_runs_are_taken_only_once_its_owner_let_it(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    other = agent(client, owner, name="Labeler")
    chat = "9" * 12
    agents._apply(user_id, [dispatched("a" * 12, other["id"], chat)])
    assert row("a" * 12) is None
    agents.grant_chat(user_id, chat)
    agents._apply(user_id, [dispatched(chat, other["id"], chat)])           # never its own parent
    assert row(chat) is None
    agents._apply(user_id, [dispatched(f"{n:012x}", other["id"], chat, depth=2) for n in range(1, 8)])
    assert row(f"{1:012x}")["depth"] == 1
    from proofpane_open import store
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE parent_run_id = ?", (chat,)).fetchone()[0] == 5
        assert json.loads(row(f"{1:012x}")["trigger"])["from_agent"] is None


def test_through_the_workspace_a_run_starts_another_and_the_app_shows_both(client, owner):
    labeler = agent(client, owner, name="Labeler")
    triage = agent(client, owner, brief="dispatchwait Labeler|label issue 7|30", may_dispatch=True)
    top = client.post(f"{A}/{triage['id']}/runs", headers=owner, json={}).json()["run_id"]
    until(client, owner, top, ("done",))
    deadline = time.time() + 60
    while True:
        children = [r for g in listing(client, owner)["groups"] for a in g["agents"] if a["id"] == labeler["id"]
                    for r in a["runs"] if r["parent_run_id"] == top]
        if children and children[0]["state"] == "done":
            break
        assert time.time() < deadline, children
        time.sleep(0.2)
    (child,) = children
    assert child["depth"] == 1 and child["trigger"]["kind"] == "dispatch"
    assert child["trigger"]["from_agent"] == triage["id"]
    shown, _ = run_of(client, owner, top)
    assert shown["dispatches"] == 1 and shown["subtree"]["runs"] == 1 and shown["subtree"]["open"] == 0


def test_a_chat_let_start_runs_is_kept_before_the_workspace_hears_it(client, owner):
    assert client.post("/api/ade/workspace/start", headers=owner).json()["running"] is True
    s = client.post("/api/ade/workspace/sessions", headers=owner,
                    json={"repo": "demo", "program": "chat", "task": "hello"})
    assert s.status_code == 200, s.text
    sid = s.json()["id"]
    r = client.post(f"/api/ade/workspace/sessions/{sid}/chat/settings", headers=owner, json={"may_dispatch": True})
    assert r.status_code == 200 and r.json()["chosen"]["may_dispatch"] is True, r.text
    from proofpane_open import store
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_dispatch_chats WHERE session_id = ?", (sid,)).fetchone()[0] == 1
    r = client.post(f"/api/ade/workspace/sessions/{sid}/chat/settings", headers=owner, json={"model": "sonnet"})
    assert r.status_code == 200 and r.json()["chosen"] == {"model": "sonnet", "may_dispatch": True}
    # one made able from its start is kept too
    s = client.post("/api/ade/workspace/sessions", headers=owner,
                    json={"repo": "demo", "program": "chat", "task": "hello", "chat_settings": {"may_dispatch": True}})
    assert s.status_code == 200, s.text
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_dispatch_chats WHERE session_id = ?",
                          (s.json()["id"],)).fetchone()[0] == 1
    assert client.get(f"/api/ade/workspace/sessions/{s.json()['id']}", headers=owner).json()["chat_settings"] == \
        {"may_dispatch": True}


def test_a_run_past_what_its_parent_may_is_flagged_and_a_chat_s_grant_goes_when_turned_off(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    top_agent, other = agent(client, owner, may_dispatch=True), agent(client, owner, name="Labeler", may_dispatch=True)
    plain = agent(client, owner, name="Plain")
    top = made_run(client, owner, top_agent["id"], monkeypatch)
    not_let = made_run(client, owner, plain["id"], monkeypatch)
    ended = made_run(client, owner, top_agent["id"], monkeypatch)
    now = int(time.time())
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET state = 'done', ended_at = ? WHERE run_id = ?", (now - 600, ended))
    agents._apply(user_id, [dispatched("a" * 12, other["id"], not_let),        # its agent may not start runs
                            dispatched("b" * 12, other["id"], ended),          # ten minutes after its parent ended
                            dispatched("c" * 12, other["id"], top)])            # as it may
    agents._apply(user_id, [dispatched("d" * 12, top_agent["id"], "c" * 12, depth=2)])   # back to the first agent
    assert row("a" * 12)["flag"] == "started by a run whose agent may not start runs"
    assert row("b" * 12)["flag"] == "started under a run that had ended"
    assert row("c" * 12)["flag"] is None
    assert row("d" * 12)["flag"] == "started for an agent whose run started its caller, which it would wait behind"
    chat = "9" * 12
    agents.grant_chat(user_id, chat)
    agents.revoke_chat(user_id, chat)
    agents._apply(user_id, [dispatched("e" * 12, other["id"], chat)])
    assert row("e" * 12) is None                                               # its grant was taken back
