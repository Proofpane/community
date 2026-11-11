"""Agents through the app (ade/agents.py, agents_routes.py): kept in the app's store, so the list never starts a
machine; mirrored to the workspace and run there, with a real workspace agent behind the local provider and the
stand-in claude (chat_stand_in.py)."""

import json
import os
import sys
import time

import pytest

from conftest import ADMIN, signed_up
from workspace_fixtures import make_repo

A = "/api/ade/agents"
STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")


@pytest.fixture
def local(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    from proofpane_open.ade import workspaces
    yield workspaces
    workspaces.shutdown()


@pytest.fixture
def owner(client, local, env):
    h = signed_up(client)
    user_id = client.get("/api/auth/me", headers=h).json()["id"]
    make_repo(str(env / "workspaces" / user_id / "repos" / "demo"))
    return h


def agent(client, h, **over):
    body = dict({"name": "Triage", "brief": "hello", "repo": "demo"}, **over)
    r = client.post(A, headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def listing(client, h):
    r = client.get(A, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def run_of(client, h, run_id):
    for group in listing(client, h)["groups"]:
        for a in group["agents"]:
            for r in a["runs"]:
                if r["run_id"] == run_id:
                    return r, a
    raise AssertionError(f"run {run_id} not listed")


def made_runs(client, h, agent_id, n, monkeypatch):
    """`n` runs the app made of an agent, none sent: the rows the workspace's records are then copied to."""
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    return [client.post(f"{A}/{agent_id}/runs", headers=h, json={}).json()["run_id"] for _ in range(n)]


def until(client, h, run_id, states, timeout=60):
    deadline = time.time() + timeout
    while True:
        r, a = run_of(client, h, run_id)
        if r["state"] in states:
            return r, a
        assert time.time() < deadline, r
        time.sleep(0.2)


def test_every_route_needs_an_account(client, local):
    for method, path in (("GET", ""), ("POST", ""), ("GET", "/draft?session=aaaaaaaaaaaa"),
                         ("GET", "/aaaaaaaaaaaa"), ("PUT", "/aaaaaaaaaaaa"), ("POST", "/aaaaaaaaaaaa/pause"),
                         ("POST", "/aaaaaaaaaaaa/resume"), ("POST", "/aaaaaaaaaaaa/retire"),
                         ("POST", "/aaaaaaaaaaaa/runs"), ("POST", "/runs/aaaaaaaaaaaa/stop"),
                         ("POST", "/runs/aaaaaaaaaaaa/thumbs")):
        assert client.request(method, A + path).status_code == 401, path


def test_an_agent_is_kept_and_listed_without_starting_a_machine(client, owner, local):
    made = agent(client, owner, group="Ops")
    assert made["state"] == "idle" and made["brief_version"] == 1 and made["limits"]["max_steps"] == 200
    got = listing(client, owner)
    assert [g["name"] for g in got["groups"]] == ["Ops"] and got["groups"][0]["agents"][0]["id"] == made["id"]
    assert client.get("/api/ade/workspace", headers=owner).json()["running"] is False
    changed = client.put(f"{A}/{made['id']}", headers=owner, json={"brief": "hello again"}).json()
    assert changed["brief_version"] == 2 and changed["brief"] == "hello again"
    same = client.put(f"{A}/{made['id']}", headers=owner, json={"name": "Triage 2"}).json()
    assert same["brief_version"] == 2


def test_a_hundred_agents_are_listed_from_the_store_in_under_a_second(client, owner, local, monkeypatch):
    from proofpane_open import store
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    now = int(time.time())
    with store.connect() as db:
        for i in range(100):
            agent_id = f"{i:012x}"
            db.execute("INSERT INTO ade_agents (id, user_id, name, grp, brief, created_at, updated_at) VALUES "
                       "(?,?,?,?,?,?,?)", (agent_id, user_id, f"agent {i}", f"group {i % 7}", "a brief", now, now))
            for j in range(30):
                db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, sent, queued_at, "
                           "started_at, ended_at, cost_est_usd, updated_at) VALUES (?,?,?,?,1,?,?,?,?,?)",
                           (f"{i:06x}{j:06x}", agent_id, user_id, "done", now - j * 60, now - j * 60,
                            now - j * 60 + 30, 0.01, now))
    started = []
    monkeypatch.setattr(local.LocalProvider, "ensure", lambda self, user: started.append(1))
    t = time.monotonic()
    got = listing(client, owner)
    took = time.monotonic() - t
    agents = [a for g in got["groups"] for a in g["agents"]]
    assert len(agents) == 100 and len(got["groups"]) == 7 and all(len(a["runs"]) == 5 for a in agents)
    assert took < 1.0, took
    assert started == []


def test_run_now_starts_the_machine_and_the_run_s_record_comes_back(client, owner):
    made = agent(client, owner)
    r = client.post(f"{A}/{made['id']}/runs", headers=owner, json={"prompt": "the open issues"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "queued" and run["starting"] is True and run["trigger"]["prompt"] == "the open issues"
    done, a = until(client, owner, run["run_id"], ("done",))
    assert done["cost_est_usd"] == pytest.approx(0.0123) and done["tokens"]["input_tokens"] == 10
    assert done["base"] and done["sent"] is True
    assert a["totals"]["runs"] == 1 and a["totals"]["done"] == 1 and a["totals"]["success_rate"] == 1.0
    assert a["state"] == "idle"
    # the run is a session to the pane's routes
    log = client.get(f"/api/ade/workspace/sessions/{run['run_id']}/chat", headers=owner).json()
    assert log["events"][0]["text"].startswith("hello\n\nthe open issues\n\n")
    # and Verify takes its branch as the agent's, from the app's own record of it
    verified = client.post(f"/api/ade/workspace/sessions/{run['run_id']}/finish", headers=owner)
    assert verified.status_code == 200, verified.text
    assert (verified.json()["status"], verified.json()["reason"]) == ("skipped", "no change since the run's base")


def test_a_paused_or_retired_agent_does_not_run(client, owner):
    made = agent(client, owner)
    assert client.post(f"{A}/{made['id']}/pause", headers=owner).json()["state"] == "paused"
    r = client.post(f"{A}/{made['id']}/runs", headers=owner, json={})
    assert r.status_code == 409 and "paused" in r.json()["detail"]
    assert client.post(f"{A}/{made['id']}/resume", headers=owner).json()["state"] == "idle"
    retired = client.post(f"{A}/{made['id']}/retire", headers=owner).json()
    assert retired["state"] == "retired"
    assert listing(client, owner)["groups"][-1]["name"] == "Retired"
    assert client.post(f"{A}/{made['id']}/runs", headers=owner, json={}).status_code == 409
    assert client.put(f"{A}/{made['id']}", headers=owner, json={"brief": "x"}).status_code == 409


def test_a_run_not_yet_sent_is_stopped_here(client, owner, local, monkeypatch):
    made = agent(client, owner)
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    run = client.post(f"{A}/{made['id']}/runs", headers=owner, json={}).json()
    stopped = client.post(f"{A}/runs/{run['run_id']}/stop", headers=owner).json()
    assert (stopped["state"], stopped["reason"]) == ("stopped", "stopped by you")
    assert client.post(f"{A}/runs/{run['run_id']}/stop", headers=owner).status_code == 409


def test_thumbs_are_the_owner_s_label_on_a_run(client, owner, monkeypatch):
    made = agent(client, owner)
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    run = client.post(f"{A}/{made['id']}/runs", headers=owner, json={}).json()
    got = client.post(f"{A}/runs/{run['run_id']}/thumbs", headers=owner, json={"value": "down", "note": "off topic"})
    assert (got.json()["thumbs"], got.json()["thumbs_note"]) == ("down", "off topic")
    cleared = client.post(f"{A}/runs/{run['run_id']}/thumbs", headers=owner, json={}).json()
    assert (cleared["thumbs"], cleared["thumbs_note"]) == (None, None)
    assert client.post(f"{A}/runs/{run['run_id']}/thumbs", headers=owner, json={"value": "maybe"}).status_code == 422


def test_one_account_never_reaches_another_s(client, owner, monkeypatch):
    made = agent(client, owner)
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    run = client.post(f"{A}/{made['id']}/runs", headers=owner, json={}).json()
    other = signed_up(client, "second-admin@example.com")
    monkeypatch.setenv("OPEN_EDITION_ADMINS", f"{ADMIN},second-admin@example.com")
    assert listing(client, other)["groups"] == []
    for method, path in (("GET", f"/{made['id']}"), ("PUT", f"/{made['id']}"), ("POST", f"/{made['id']}/runs"),
                         ("POST", f"/{made['id']}/pause"), ("POST", f"/runs/{run['run_id']}/stop"),
                         ("POST", f"/runs/{run['run_id']}/thumbs")):
        r = client.request(method, A + path, headers=other, json={"brief": "x"} if method == "PUT" else {})
        assert r.status_code == 404, (path, r.text)


def test_a_record_from_the_workspace_is_held_to_its_account_and_its_shapes(client, owner):
    from proofpane_open.ade import agents
    from proofpane_open import store
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    other = signed_up(client, "second-admin@example.com")
    other_id = client.get("/api/auth/me", headers=other).json()["id"]
    import proofpane_open.ade.agents as module
    module._deliver_soon, kept = (lambda user, start=False: False), module._deliver_soon
    try:
        run_id = client.post(f"{A}/{made['id']}/runs", headers=owner, json={}).json()["run_id"]
    finally:
        module._deliver_soon = kept
    changed = agents._apply(other_id, [{"run_id": run_id, "agent_id": made["id"], "state": "done"}])
    assert changed == set()                                     # not that account's run
    # a run the app never made is not taken, whatever the workspace says of it
    assert agents._apply(user_id, [{"run_id": "c" * 12, "agent_id": made["id"], "state": "done"}]) == set()
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE run_id = ?", ("c" * 12,)).fetchone()[0] == 0
    agents._apply(user_id, [{"run_id": run_id, "agent_id": made["id"], "state": "nonsense", "base": "--output=x",
                             "cost_est_usd": "lots", "steps": -4, "tokens": {"input_tokens": "9", "x": 3},
                             "last_text": "x" * 9000}])
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()
    assert (row["state"], row["base"], row["cost_est_usd"], row["steps"]) == ("failed", None, None, 0)
    assert json.loads(row["tokens"]) == {"x": 3} and len(row["last_text"]) == 2000


def test_the_runs_come_back_a_page_at_a_time_and_a_new_volume_is_read_again(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    from proofpane_open import store
    made = agent(client, owner)
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"]}
    monkeypatch.setattr(agents, "_deliver_soon", lambda user, start=False: False)
    ids = [client.post(f"{A}/{made['id']}/runs", headers=owner, json={}).json()["run_id"] for _ in range(3)]
    pages = {0: {"seq": 2, "more": True, "runs": [{"run_id": ids[0], "agent_id": made["id"], "state": "done", "seq": 1},
                                                  {"run_id": ids[1], "agent_id": made["id"], "state": "done", "seq": 2}]},
             2: {"seq": 3, "more": False, "runs": [{"run_id": ids[2], "agent_id": made["id"], "state": "failed",
                                                     "seq": 3}]}}
    asked = []

    def call(endpoint, method, path, body=None, timeout=30):
        since = int(path.split("since=")[1])
        asked.append(since)
        return pages.get(since, {"seq": 3, "more": False, "runs": []})
    monkeypatch.setattr(agents.client, "call", call)
    agents._poll(user, "endpoint")
    assert asked == [0, 2]
    with store.connect() as db:
        states = [db.execute("SELECT state FROM ade_agent_runs WHERE run_id = ?", (i,)).fetchone()[0] for i in ids]
        assert states == ["done", "done", "failed"]
        assert db.execute("SELECT seq FROM ade_agent_sync WHERE user_id = ?", (user["id"],)).fetchone()[0] == 3
    # a workspace whose change numbers start again lower (a new volume) is read again from its start (an ended run
    # taking only what changes after its end: its folder pruned)
    pages = {3: {"seq": 1, "more": False, "runs": []},
             0: {"seq": 1, "more": False, "runs": [{"run_id": ids[0], "agent_id": made["id"], "state": "stopped",
                                                     "pruned": True, "seq": 1}]}}
    asked.clear()
    agents._poll(user, "endpoint")
    assert asked == [3, 0]
    with store.connect() as db:
        got = db.execute("SELECT state, pruned FROM ade_agent_runs WHERE run_id = ?", (ids[0],)).fetchone()
        assert (got["state"], got["pruned"]) == ("done", 1)
        assert db.execute("SELECT seq FROM ade_agent_sync WHERE user_id = ?", (user["id"],)).fetchone()[0] == 1


def test_a_run_that_ends_holding_its_agent_pauses_it_once(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    held = {"run_id": run_id, "agent_id": made["id"], "state": "blocked", "cause": "watch",
            "reason": "~/.bashrc changed while the run worked", "hold": "~/.bashrc changed while the run worked. Look."}
    agents._apply(user_id, [held])
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    assert got["state"] == "paused" and got["paused_reason"].startswith("~/.bashrc changed")
    client.post(f"{A}/{made['id']}/resume", headers=owner)
    agents._apply(user_id, [held])                                  # read again (a new volume): not paused again
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"
    done = dict(held, state="done", cause=None, reason=None)          # a kept worktree's instructions changed: done
    (second,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    agents._apply(user_id, [dict(done, run_id=second)])
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "paused"
    client.post(f"{A}/{made['id']}/resume", headers=owner)
    agents._apply(user_id, [dict(done, run_id=second)])
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"


LATE = ("The git settings of the repository other changed while an agent's run worked (hooks/post-checkout), so no "
        "git of the workspace's runs there until you resume an agent held for it. Look at what changed, then resume "
        "the agent.")


def test_a_hold_found_after_a_run_s_end_pauses_its_agent_once_for_each(client, owner, monkeypatch):
    """A hold of git's places found after a run's end reaches
    the app. The workspace reports it on the agent's latest run's record as `late_hold`, with the change number of that
    write: the app pauses the agent with its words, once for each such hold, whether it has the run as ended or not.
    The record's `hold` alone still moves nothing once the run has ended (a record written back over a later one)."""
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    first, second, third, fourth = made_runs(client, owner, made["id"], 4, monkeypatch)
    state = lambda: client.get(f"{A}/{made['id']}", headers=owner).json()  # noqa: E731

    def unmirrored():
        with store_connect() as db:
            return db.execute("SELECT hold_unmirrored FROM ade_agents WHERE id = ?", (made["id"],)).fetchone()[0]
    done = {"run_id": first, "agent_id": made["id"], "state": "done", "started_at": 50, "ended_at": 100, "seq": 7}
    agents._apply(user_id, [done])
    agents._apply(user_id, [dict(done, seq=8, hold=LATE)])                # its hold alone, after its end: nothing
    assert state()["state"] == "idle"
    # held to its shapes, and refused from a record rewritten by something else, with nothing taken for the run yet
    for control in ({"seq": 0, "words": LATE}, {"seq": True, "words": LATE}, {"seq": 11, "words": "  "},
                    {"seq": "12", "words": LATE}, {"seq": 1 << 63, "words": LATE}, {"words": LATE}, [11, LATE]):
        agents._apply(user_id, [dict(done, seq=11, late_hold=control)])
        assert state()["state"] == "idle", control
    agents._apply(user_id, [dict(done, seq=8, unverified=True, late_hold={"seq": 8, "words": LATE})])
    assert state()["state"] == "idle"
    late = dict(done, seq=9, hold=LATE, late_hold={"seq": 9, "words": LATE})
    agents._apply(user_id, [late])
    got = state()
    assert got["state"] == "paused" and got["paused_reason"] == LATE and unmirrored() == 1
    client.post(f"{A}/{made['id']}/resume", headers=owner)
    agents._apply(user_id, [late])                                       # read again: not paused again
    agents._apply(user_id, [dict(late, seq=10, pruned=True, bytes=0)])   # a later record, the same hold: nor here
    assert state()["state"] == "idle"
    agents._apply(user_id, [dict(late, seq=13, unverified=True, late_hold={"seq": 13, "words": LATE})])
    assert state()["state"] == "idle"                                    # a record rewritten by something else
    other = "Your git settings outside the repositories changed while an agent's run worked. Look."
    agents._apply(user_id, [dict(late, seq=14, late_hold={"seq": 14, "words": other})])
    assert state()["paused_reason"] == other                             # a later hold: paused again
    client.post(f"{A}/{made['id']}/resume", headers=owner)
    # the store missed the run's end: its end's hold and a later one, each once
    ended = {"run_id": second, "agent_id": made["id"], "state": "blocked", "cause": "watch", "reason": "held",
             "hold": "~/.bashrc changed while the run worked. Look.", "late_hold": {"seq": 21, "words": LATE},
             "seq": 21}
    agents._apply(user_id, [ended])
    assert state()["paused_reason"] == f"~/.bashrc changed while the run worked. Look. {LATE}"
    client.post(f"{A}/{made['id']}/resume", headers=owner)
    agents._apply(user_id, [ended])
    assert state()["state"] == "idle"
    # one whose end's hold says it already: once
    agents._apply(user_id, [dict(ended, run_id=third, hold=LATE, late_hold={"seq": 31, "words": LATE})])
    assert state()["paused_reason"] == LATE
    client.post(f"{A}/{made['id']}/resume", headers=owner)
    # an end's hold of 2,000 characters leaves the late words whole after it, its own cut and marked: they
    # came second, and the 2,000 characters cut them away entirely
    long = "Changed while the run worked: " + "x" * 1970
    agents._apply(user_id, [dict(ended, run_id=fourth, hold=long, late_hold={"seq": 41, "words": LATE})])
    got = state()["paused_reason"]
    assert got.endswith(f"... {LATE}") and got.startswith("Changed while the run worked: xxx") and len(got) == 2000


def test_an_end_s_hold_and_a_late_hold_are_joined_with_neither_cut_away():
    # both whole where they fit; else the longer one cut, and marked, to fit
    from proofpane_open.ade.agents import _joined
    assert _joined("an end.", "a late hold.") == "an end. a late hold."
    fits = ("a" * 1000, "b" * 999)
    assert _joined(*fits) == f"{fits[0]} {fits[1]}"
    assert _joined("a" * 2000, "b" * 300) == "a" * 1696 + "... " + "b" * 300
    assert _joined("a" * 300, "b" * 2000) == "a" * 300 + " " + "b" * 1696 + "..."
    assert _joined("a" * 2000, "b" * 2000) == "a" * 996 + "... " + "b" * 997 + "..."
    for first, later in (("a" * 1001, "b" * 999), ("a" * 5, "b" * 4000), ("a" * 4000, "b" * 5)):
        got = _joined(first, later)
        assert len(got) == 2000 and got.startswith("a") and got.rstrip(".").endswith("b"), (len(first), len(later))


def test_a_late_hold_on_a_run_another_run_started_pauses_its_agent_once(client, owner, monkeypatch):
    """A run's `seq` column in the store holds the change number of the last late hold
    taken for the run, and nothing else: _late_hold alone writes and reads it, a run's insert (queue, _adopt) leaves it
    empty, and _clean carries no `seq`. A run another run started, taken in by _adopt, takes its late hold once, from
    the branch of _apply for a run the store does not have as ended and from the one for a run it does."""
    from proofpane_open.ade import agents
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    top_agent, child_agent = agent(client, owner, may_dispatch=True), agent(client, owner, name="Labeler")
    (top,) = made_runs(client, owner, top_agent["id"], 1, monkeypatch)
    state = lambda: client.get(f"{A}/{child_agent['id']}", headers=owner).json()  # noqa: E731
    child = "c" * 12
    # the record's own change number past its late hold's, so the column shows which of the two it keeps
    done = {"run_id": child, "agent_id": child_agent["id"], "state": "done", "started_at": 50, "ended_at": 100,
            "seq": 9, "queued_at": int(time.time()), "trigger": {"kind": "dispatch", "parent_run_id": top, "depth": 1},
            "late_hold": {"seq": 5, "words": LATE}}
    agents._apply(user_id, [done])                      # taken in as queued, then its record: it ends here
    with store_connect() as db:
        assert tuple(db.execute("SELECT parent_run_id, seq FROM ade_agent_runs WHERE run_id = ?",
                                (child,)).fetchone()) == (top, 5)
    got = state()
    assert got["state"] == "paused" and got["paused_reason"] == LATE
    client.post(f"{A}/{child_agent['id']}/resume", headers=owner)
    agents._apply(user_id, [done])                      # had as ended: the same change number, not again
    assert state()["state"] == "idle"
    other = "Your git settings outside the repositories changed while an agent's run worked. Look."
    agents._apply(user_id, [dict(done, seq=12, late_hold={"seq": 10, "words": other})])
    assert state()["paused_reason"] == other            # a later one, from the branch for an ended run
    with store_connect() as db:
        assert tuple(db.execute("SELECT seq FROM ade_agent_runs WHERE run_id = ?", (child,)).fetchone()) == (10,)


def store_connect():
    from proofpane_open import store
    return store.connect()


def _mirrored(user_id, agent_id, during=None, field="paused"):
    """What one mirror sends the workspace for the agent: the `field` (its `paused`) of each PUT, in order (the
    workspace's calls stood in for); `during()` is done while its first PUT is sent."""
    from proofpane_open import auth
    from proofpane_open.ade import agents
    sent = []

    def call(endpoint, method, path, body=None, timeout=None):
        sent.append((method, path, body))
        if during is not None and (method, path) == ("PUT", f"/agents/{agent_id}") and len(
                [s for s in sent if s[:2] == (method, path)]) == 1:
            during()
        return {}
    with pytest.MonkeyPatch.context() as m:
        m.setattr(agents.client, "call", call)
        agents._mirror(auth.user(user_id), object())
    return [body[field] for method, path, body in sent if (method, path) == ("PUT", f"/agents/{agent_id}")]


HELD = "The machine stopped while its run worked. Look at what it left, then resume the agent."


def _held(run_id, agent_id):
    return {"run_id": run_id, "agent_id": agent_id, "state": "blocked", "cause": "machine",
            "reason": "machine stopped", "hold": HELD}


def _changed(agent_id):
    with store_connect() as db:
        db.execute("UPDATE ade_agents SET updated_at = updated_at + 5 WHERE id = ?", (agent_id,))


def test_a_hold_the_owner_resumed_before_any_mirror_reaches_the_workspace_as_paused_then_resumed(client, owner,
                                                                                                 monkeypatch):
    # the workspace keeps a held agent paused until a mirror says paused and then one says not
    # (proofpane_workspace/agents.put): a resume made before the app's next mirror, sent alone, would read there as a
    # mirror sent before the app knew of the hold, and every resume after it the same, the agent paused there for good
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    assert _mirrored(user_id, made["id"]) == [False]       # mirrored as a sync would, before its run ended
    # and in the second the hold is copied and resumed in (its mirror's time not before theirs), as a sync's own mirror
    # often is: what the agent's change time alone would not select again
    with store_connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 30 WHERE id = ?", (made["id"],))
    agents._apply(user_id, [_held(run_id, made["id"])])
    assert client.post(f"{A}/{made['id']}/resume", headers=owner).status_code == 200
    assert _mirrored(user_id, made["id"]) == [True, False]
    # told once: the next mirror sends nothing until the agent changes, and then the agent as it is
    assert _mirrored(user_id, made["id"]) == []
    _changed(made["id"])
    assert _mirrored(user_id, made["id"]) == [False]


def test_a_hold_copied_while_a_mirror_is_sent_is_told_at_the_next(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    first, second = made_runs(client, owner, made["id"], 2, monkeypatch)
    assert _mirrored(user_id, made["id"]) == [False]
    agents._apply(user_id, [_held(first, made["id"])])
    # the mirror that tells the first hold is being sent when the second run's end, holding the agent too, is copied
    assert _mirrored(user_id, made["id"], during=lambda: agents._apply(user_id, [_held(second, made["id"])])) == [True]
    # the owner resumes it before the next mirror: the workspace is told paused first again, for the second hold
    assert client.post(f"{A}/{made['id']}/resume", headers=owner).status_code == 200
    assert _mirrored(user_id, made["id"]) == [True, False]


def test_a_hold_not_yet_resumed_is_mirrored_paused_once(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    assert _mirrored(user_id, made["id"]) == [False]
    with store_connect() as db:             # mirrored in the second its hold is copied in, as in the test above
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 30 WHERE id = ?", (made["id"],))
    agents._apply(user_id, [_held(run_id, made["id"])])
    assert _mirrored(user_id, made["id"]) == [True]
    assert _mirrored(user_id, made["id"]) == []
    # the owner's resume, after the workspace was told: sent alone
    assert client.post(f"{A}/{made['id']}/resume", headers=owner).status_code == 200
    _changed(made["id"])
    assert _mirrored(user_id, made["id"]) == [False]


def _sent_runs(user_id, agent_id):
    """The runs one send posts to the workspace (its calls stood in for)."""
    from proofpane_open import auth
    from proofpane_open.ade import agents
    posted = []

    def call(endpoint, method, path, body=None, timeout=None):
        posted.append(body["run_id"])
        return {"run_id": body["run_id"], "agent_id": agent_id, "state": "queued"}
    with pytest.MonkeyPatch.context() as m:
        m.setattr(agents.client, "call", call)
        agents._send(auth.user(user_id), "http://workspace")
    return posted


def test_a_change_made_in_the_second_of_its_agent_s_last_mirror_is_told_and_its_runs_wait_for_it(client, owner,
                                                                                                    monkeypatch):
    """A mirror and then a change in one second, as the acceptance of a teammate's task and an edit after it, or a
    sync's mirror and what its poll copies (an MCP server's digest, a pause), often are: the change is told at the
    next mirror, and a run queued after it is sent only once it has been, never against the copy the workspace holds."""
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    second = int(time.time()) + 2
    monkeypatch.setattr(time, "time", lambda: second + 0.5)
    assert _mirrored(user_id, made["id"], field="brief") == ["hello"]
    r = client.put(f"{A}/{made['id']}", headers=owner, json={"brief": "Changed in the same second."})
    assert r.status_code == 200, r.text
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    assert _sent_runs(user_id, made["id"]) == []
    assert _mirrored(user_id, made["id"], field="brief") == ["Changed in the same second."]
    assert _sent_runs(user_id, made["id"]) == [run_id]
    assert _mirrored(user_id, made["id"], field="brief") == []


def test_a_change_made_while_a_mirror_is_sent_in_the_second_of_the_one_it_carries_is_told_at_the_next(client, owner,
                                                                                                         monkeypatch):
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    second = int(time.time()) + 2
    monkeypatch.setattr(time, "time", lambda: second + 0.5)

    def brief(words):
        r = client.put(f"{A}/{made['id']}", headers=owner, json={"brief": words})
        assert r.status_code == 200, r.text
    brief("First.")
    assert _mirrored(user_id, made["id"], during=lambda: brief("Second."), field="brief") == ["First."]
    assert _mirrored(user_id, made["id"], field="brief") == ["Second."]
    assert _mirrored(user_id, made["id"], field="brief") == []


def test_one_account_s_mirrors_take_turns(client, owner, monkeypatch):
    """The acceptance of a teammate's task mirrors the teammate's agents itself, outside a sync, which mirrors them
    too: two mirrors at once could each send a copy of an agent and the older reach the workspace last, the app
    counting the newer as there."""
    import threading
    from proofpane_open import auth
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user = auth.user(client.get("/api/auth/me", headers=owner).json()["id"])
    count = threading.Lock()
    in_flight, most = [0], [0]
    entered, release = threading.Event(), threading.Event()

    def call(endpoint, method, path, body=None, timeout=None):
        if (method, path) != ("PUT", f"/agents/{made['id']}"):
            return {}
        with count:
            in_flight[0] += 1
            most[0] = max(most[0], in_flight[0])
        entered.set()
        release.wait(10)
        with count:
            in_flight[0] -= 1
        return {}
    monkeypatch.setattr(agents.client, "call", call)
    first = threading.Thread(target=agents._mirror, args=(user, object()))
    first.start()
    assert entered.wait(10)
    second = threading.Thread(target=agents._mirror, args=(user, object()))
    second.start()
    time.sleep(0.5)                     # a second mirror let in would be sending its copy by now
    release.set()
    first.join(10)
    second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert most[0] == 1


@pytest.mark.parametrize("till", ["ahead", "past"])
def test_a_hold_copied_onto_an_agent_paused_till_a_time_holds_it_until_the_owner_resumes(client, owner, monkeypatch,
                                                                                       till):
    # a pause till a time (the day's estimated cost) lifts by itself when its time comes, and reads as lifted from
    # then: a hold copied onto it would go with it, and the workspace's hold be mirrored as resumed by nobody
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    now = int(time.time())
    with store_connect() as db:
        db.execute("UPDATE ade_agents SET paused = 1, paused_until = ?, paused_reason = 'the day' WHERE id = ?",
                   (now + 3600 if till == "ahead" else now - 60, made["id"]))
    agents._apply(user_id, [_held(run_id, made["id"])])
    monkeypatch.setattr(agents, "sync", lambda user, background=False: False)    # the round's syncs not asked here
    agents.poll_all(now=now + 7200)             # the pause's time come and gone
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    assert got["state"] == "paused" and got["paused_reason"] == HELD, got
    assert _mirrored(user_id, made["id"]) == [True]


def test_a_run_stopped_while_it_started_then_blocked_by_its_precheck_s_change_pauses_its_agent(client, owner,
                                                                                                monkeypatch):
    """The owner stopped it while it started (the store has it stopped); its precheck had changed a watched place, so
    the workspace ends it blocked, holding its agent, after: the app pauses the agent then, once."""
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    agents._apply(user_id, [{"run_id": run_id, "agent_id": made["id"], "state": "stopped", "reason": "stopped by you"}])
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"
    blocked = {"run_id": run_id, "agent_id": made["id"], "state": "blocked", "cause": "watch",
               "reason": "~/.bashrc changed", "hold": "~/.bashrc changed while the run started. Look."}
    agents._apply(user_id, [blocked])
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    assert got["state"] == "paused" and got["paused_reason"].startswith("~/.bashrc changed while the run started")
    client.post(f"{A}/{made['id']}/resume", headers=owner)
    agents._apply(user_id, [blocked])                               # read again: not paused again
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"


def test_a_run_stopped_while_it_started_then_held_by_its_start_s_own_refusal_pauses_its_agent(client, owner,
                                                                                               monkeypatch):
    """A task's attempt's start that finds its
    watched places changed ends it blocked with cause "resume" (check 4's refusal) and holds its agent, the hold on the
    record; where the owner had stopped the run while it started, the app skipped that record, which only a "watch"
    cause moved on, so the agent stayed held in the workspace while the app showed it not paused. A record blocked with
    no hold still changes nothing the store has of a stopped run."""
    from proofpane_open import store
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    agents._apply(user_id, [{"run_id": run_id, "agent_id": made["id"], "state": "stopped", "reason": "stopped by you"}])
    unheld = {"run_id": run_id, "agent_id": made["id"], "state": "blocked", "cause": "resume",
              "reason": "the watched places changed since the task's last attempt ended"}
    for control in (unheld, dict(unheld, hold="  ")):              # the control: no hold, nothing moves
        agents._apply(user_id, [control])
        with store.connect() as db:
            state = db.execute("SELECT state FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()[0]
        assert state == "stopped", control
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"
    held = dict(unheld, hold="The watched places changed since the task's last attempt ended. Look at what changed, "
                             "then resume the agent.")
    agents._apply(user_id, [held])
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    assert got["state"] == "paused" and got["paused_reason"].startswith("The watched places changed since"), got
    client.post(f"{A}/{made['id']}/resume", headers=owner)
    agents._apply(user_id, [held])                                  # read again: not paused again
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"


def test_a_run_s_brief_version_is_the_one_it_started_with_within_what_the_store_knows(client, owner, monkeypatch):
    """The workspace says which brief a run started with (an edit after it was queued is the one it ran); the app
    takes it only between the version it queued the run with and the agent's own, never back, never from a record the
    workspace could not verify."""
    from proofpane_open import store
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    assert client.put(f"{A}/{made['id']}", headers=owner, json={"brief": "New words."}).json()["brief_version"] == 2

    def stored():
        with store.connect() as db:
            return db.execute("SELECT brief_version FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()[0]
    view = {"run_id": run_id, "agent_id": made["id"], "state": "working"}
    for given in (3, 0, True, "2"):
        agents._apply(user_id, [dict(view, brief_version=given)])
        assert stored() == 1, given
    agents._apply(user_id, [dict(view, brief_version=2, unverified=True)])
    assert stored() == 1                                # an unverified record: how the run stands, nothing more
    agents._apply(user_id, [dict(view, brief_version=2)])
    assert stored() == 2
    agents._apply(user_id, [dict(view, brief_version=1)])
    assert stored() == 2                                # never back below


def test_a_run_the_owner_stops_while_it_is_being_sent_is_stopped_in_the_workspace_too(client, owner, monkeypatch):
    """The stop lands while the run is on its way: the store had it stopped, not sent, and the workspace queued it.
    It is stopped there too, or the workspace would run it and its record bring it back as running."""
    from proofpane_open import store
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"]}
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1 WHERE id = ?", (made["id"],))
    calls = []

    def call(endpoint, method, path, body=None, timeout=None):
        calls.append((method, path))
        if path.endswith("/runs"):
            assert agents.stop_run(user, run_id)["state"] == "stopped"            # the owner, meanwhile
            return {"run_id": run_id, "agent_id": made["id"], "state": "queued"}
        return {"run_id": run_id, "agent_id": made["id"], "state": "stopped", "reason": "stopped by you",
                "ended_at": int(time.time())}
    monkeypatch.setattr(agents.client, "call", call)
    agents._send(user, "http://workspace")
    assert calls == [("POST", f"/agents/{made['id']}/runs"), ("POST", f"/agents/runs/{run_id}/stop")]
    r, _ = run_of(client, owner, run_id)
    assert (r["state"], r["reason"]) == ("stopped", "stopped by you")
    with store.connect() as db:
        assert db.execute("SELECT sent FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()[0] == 1


def test_a_run_another_sync_read_working_is_not_stopped_by_its_send(client, owner, monkeypatch):
    """Its row moved on while it was being sent (another sync read it working from the workspace): no stop goes out.
    Only the owner's own local stop (a run stopped and never sent) is one."""
    from proofpane_open import store
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"]}
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1 WHERE id = ?", (made["id"],))
    calls = []

    def call(endpoint, method, path, body=None, timeout=None):
        calls.append((method, path))
        assert path.endswith("/runs"), path
        agents._apply(user["id"], [{"run_id": run_id, "agent_id": made["id"], "state": "working",
                                     "started_at": int(time.time())}], record_seq=False)          # the other sync
        return {"run_id": run_id, "agent_id": made["id"], "state": "working"}
    monkeypatch.setattr(agents.client, "call", call)
    agents._send(user, "http://workspace")
    assert calls == [("POST", f"/agents/{made['id']}/runs")] and run_of(client, owner, run_id)[0]["state"] == "working"


def test_two_senders_at_once_send_each_run_once(client, owner, monkeypatch):
    import threading
    from proofpane_open import store
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"]}
    runs = made_runs(client, owner, made["id"], 2, monkeypatch)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1 WHERE id = ?", (made["id"],))
    posted = []

    def call(endpoint, method, path, body=None, timeout=None):
        posted.append(body["run_id"])
        time.sleep(0.3)                                 # a workspace slow to answer
        return {"run_id": body["run_id"], "agent_id": made["id"], "state": "queued"}
    monkeypatch.setattr(agents.client, "call", call)
    senders = [threading.Thread(target=agents._send, args=(user, "http://workspace")) for _ in range(2)]
    for t in senders:
        t.start()
    for t in senders:
        t.join()
    assert sorted(posted) == sorted(runs)


def test_a_stop_that_finds_its_run_sent_just_now_stops_it_in_the_workspace(client, owner, monkeypatch):
    """The owner's stop read the run as not sent, and the send claimed it before the stop's own change: the stop
    goes to the workspace, where the run now is, rather than only to the store."""
    from proofpane_open import store
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"]}
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET sent = 1 WHERE run_id = ?", (run_id,))       # sent just now
    real, reads = agents._run_row, []

    def as_read_before(db, user_id, rid):
        row = real(db, user_id, rid)
        reads.append(rid)
        return dict(row, sent=0) if len(reads) == 1 else row                            # its first read: before
    monkeypatch.setattr(agents, "_run_row", as_read_before)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace")
    calls = []
    monkeypatch.setattr(agents.client, "call", lambda endpoint, method, path, body=None, timeout=None: calls.append(
        path) or {"run_id": run_id, "agent_id": made["id"], "state": "stopped", "reason": "stopped by you",
                  "ended_at": int(time.time())})
    assert agents.stop_run(user, run_id)["state"] == "stopped"
    assert calls == [f"/agents/runs/{run_id}/stop"]


def test_a_stop_that_finds_its_unsent_run_ended_just_now_says_so(client, owner, monkeypatch):
    """Read as queued and not sent, then failed at its send before the stop's own change: the run has ended, and the
    workspace, which never had it, is not asked."""
    from proofpane_open import store
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"]}
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET state = 'failed', reason = 'refused' WHERE run_id = ?", (run_id,))
    real, reads = agents._run_row, []

    def as_read_before(db, user_id, rid):
        row = real(db, user_id, rid)
        reads.append(rid)
        return dict(row, state="queued") if len(reads) == 1 else row
    monkeypatch.setattr(agents, "_run_row", as_read_before)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace")
    calls = []
    monkeypatch.setattr(agents.client, "call", lambda *args, **kwargs: calls.append(args) or {})
    with pytest.raises(agents.AgentError) as e:
        agents.stop_run(user, run_id)
    assert (e.value.status, str(e.value), calls) == (409, "This run has ended already.", [])


def test_an_ended_run_in_the_store_keeps_what_it_ended_with(client, owner, monkeypatch):
    """A run's earlier record can be written back over its last while the workspace is stopped (it keeps no copy
    then): once a run has ended in the store, a later record changes only that its folder was pruned and that its
    record was rewritten. Stopped while it started, then blocked by its precheck's watch, is the one way on."""
    from proofpane_open import store
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    run_id, stopped_id, machine_id = made_runs(client, owner, made["id"], 3, monkeypatch)

    def row(rid):
        with store.connect() as db:
            return db.execute("SELECT * FROM ade_agent_runs WHERE run_id = ?", (rid,)).fetchone()
    done = {"run_id": run_id, "agent_id": made["id"], "state": "done", "last_text": "the release is tagged",
            "started_at": 50, "ended_at": 100}
    agents._apply(user_id, [done])
    agents._apply(user_id, [dict(done, state="failed", reason="machine stopped", cause="machine", last_text="earlier",
                                 hold="~/.bashrc changed")])
    got = row(run_id)
    assert (got["state"], got["last_text"], got["pruned"]) == ("done", "the release is tagged", 0)
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] != "paused"
    agents._apply(user_id, [dict(done, pruned=True, bytes=0, rewritten=2)])
    got = row(run_id)
    assert (got["state"], got["pruned"], got["bytes"]) == ("done", 1, 0) and "2 times" in got["rewritten"]
    agents._apply(user_id, [dict(done, pruned=False, bytes=900)])
    assert (row(run_id)["pruned"], row(run_id)["bytes"]) == (1, 0)                    # never back
    for rid, cause, then in ((stopped_id, "watch", "blocked"), (machine_id, "machine", "stopped")):
        stopped = {"run_id": rid, "agent_id": made["id"], "state": "stopped", "reason": "stopped by you", "ended_at": 100}
        agents._apply(user_id, [stopped])
        # with no hold, only a watch cause reopens a run stopped while it started; a machine stop stays stopped
        agents._apply(user_id, [dict(stopped, state="blocked", cause=cause, reason="held")])
        assert row(rid)["state"] == then, cause
    # a non-blank hold reopens it whatever the cause: the workspace holds the agent until the owner resumes
    agents._apply(user_id, [{"run_id": machine_id, "agent_id": made["id"], "state": "blocked", "cause": "machine",
                             "reason": "held", "hold": "held", "ended_at": 100}])
    assert row(machine_id)["state"] == "blocked"


ADDRESS = {"name": "notes", "kind": "address", "transport": "http", "url": "https://mcp.example.org/mcp",
           "secret_headers": ["Authorization"]}


def test_an_agent_s_servers_are_checked_as_the_workspace_checks_them(client, owner):
    made = agent(client, owner, mcp_servers=[ADDRESS])
    assert made["mcp"]["servers"][0]["url"] == "https://mcp.example.org/mcp" and made["mcp"]["connectors"] is False
    for bad in ([{"name": "x", "kind": "package", "identifier": "left-pad", "version": "^1.0.0"}],
                [{"name": "proofpane", "kind": "repository"}], [dict(ADDRESS, url="http://evil.example/mcp")]):
        r = client.put(f"{A}/{made['id']}", headers=owner, json={"mcp_servers": bad})
        assert r.status_code == 400, bad
    r = client.put(f"{A}/{made['id']}", headers=owner, json={"connectors": True})
    assert r.json()["mcp"]["connectors"] is True and r.json()["mcp"]["servers"][0]["name"] == "notes"


def test_a_run_s_report_of_its_tools_is_taken_held_and_accepted(client, owner, monkeypatch):
    from proofpane_open.ade import agents, push
    made = agent(client, owner, mcp_servers=[ADDRESS])
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    first, second = made_runs(client, owner, made["id"], 2, monkeypatch)
    status = [{"name": "notes", "scope": "dynamic", "status": "connected", "tools": 3}]
    agents._apply(user_id, [{"run_id": first, "agent_id": made["id"], "state": "done", "mcp": status,
                             "mcp_seen": {"notes": "a" * 64, "other": "c" * 64}}])
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    assert got["mcp"]["accepted"] == {"notes": "a" * 64}                # its first tools: the ones it was added with
    assert got["tools"]["status"] == [dict(status[0], error=None)] and "other" not in got["tools"]["seen"]
    agents._apply(user_id, [{"run_id": second, "agent_id": made["id"], "state": "done",
                             "mcp_seen": {"notes": "b" * 64}, "mcp_changed": ["notes"]}])
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    assert got["mcp"]["held"] == ["notes"] and got["mcp"]["accepted"] == {"notes": "a" * 64}
    items = dict(push.items_for(user_id, [], int(__import__("time").time())))
    assert any(k.startswith(f"agent-tools:{made['id']}:notes:") for k in items)
    accepted = client.post(f"{A}/{made['id']}/tools/notes/accept", headers=owner).json()
    assert accepted["mcp"]["held"] == [] and accepted["mcp"]["accepted"] == {"notes": "b" * 64}
    assert client.post(f"{A}/{made['id']}/tools/nothing/accept", headers=owner).status_code == 404
    # a changed definition is a new server: what was accepted of the old one is not kept
    changed = client.put(f"{A}/{made['id']}", headers=owner, json={"mcp_servers": [dict(ADDRESS, url="https://other.example/mcp")]})
    assert changed.json()["mcp"]["accepted"] == {}


def test_a_proposal_lands_in_the_queue_and_adds_nothing(client, owner, monkeypatch):
    from proofpane_open.ade import agents, push
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    proposal = {"name": "io.example/notes", "description": "Notes.", "version": "1.0.0", "repository": None,
                "ways": [{"kind": "address", "transport": "http", "url": "https://mcp.example.org/mcp", "headers": []}],
                "why": "to read the notes"}
    for _ in range(2):                                                    # read twice: kept once
        agents._apply(user_id, [{"run_id": run_id, "agent_id": made["id"], "state": "done", "proposals": [proposal]}])
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    (kept,) = got["proposals"]
    assert kept["name"] == "io.example/notes" and kept["why"] == "to read the notes" and got["mcp"]["servers"] == []
    items = dict(push.items_for(user_id, [], int(__import__("time").time())))
    told = items[f"agent-proposal:{kept['id']}"]
    assert (told.title, told.url) == ("An agent needs a look", "/app/#agents") and "notes" not in repr(told)
    assert client.post(f"{A}/proposals/{kept['id']}", headers=owner, json={"state": "dismissed"}).status_code == 200
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["proposals"] == []
    assert client.post(f"{A}/proposals/{kept['id']}", headers=owner, json={"state": "added"}).status_code == 404


def test_the_search_asks_the_registry_for_the_latest_versions(client, owner, monkeypatch):
    import http.server
    import threading
    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mcp_registry_filesystem.json")
    asked = []

    class Registry(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            body = open(fixture, "rb").read()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("PP_MCP_REGISTRY", f"http://127.0.0.1:{server.server_address[1]}")
    try:
        got = client.get(f"{A}/mcp-search?q=filesystem", headers=owner).json()
    finally:
        server.shutdown()
    assert [r["name"] for r in got["servers"]][0] == "com.pulsemcp/remote-filesystem"
    assert got["next"] and "version=latest" in asked[0] and "search=filesystem" in asked[0]
    secret = [e for e in got["servers"][0]["ways"][0]["env"] if e["secret"]]
    assert [e["name"] for e in secret] == ["GCS_PRIVATE_KEY"]
    assert client.get(f"{A}/mcp-search?q=", headers=owner).status_code == 400


def test_a_secret_goes_to_the_workspace_and_never_into_the_app_s_store(client, owner, env):
    made = agent(client, owner, mcp_servers=[ADDRESS])
    r = client.put(f"{A}/{made['id']}/mcp-secrets", headers=owner, json={"secrets": {"notes": {"Authorization": "x"}}})
    assert r.status_code == 409 and "Start the workspace" in r.json()["detail"]
    assert client.post("/api/ade/workspace/start", headers=owner).json()["running"] is True
    r = client.put(f"{A}/{made['id']}/mcp-secrets", headers=owner,
                   json={"secrets": {"notes": {"Authorization": "Bearer s3cr3t-value"}}})
    assert r.status_code == 200 and r.json() == {"notes": ["Authorization"]}
    assert client.get(f"{A}/{made['id']}/mcp-secrets", headers=owner).json() == {"notes": ["Authorization"]}
    for path in env.rglob("*.db*"):
        assert b"s3cr3t-value" not in path.read_bytes(), path


def test_a_run_nobody_watches_its_precheck_and_its_budgets_are_the_agent_s(client, owner):
    made = agent(client, owner, unattended_mode="auto", precheck="test -n \"$(git log -1 --since=1.hour)\"",
                 max_budget_usd=2.5, est_usd_per_day=10)
    assert (made["unattended_mode"], made["max_budget_usd"], made["est_usd_per_day"]) == ("auto", 2.5, 10.0)
    from proofpane_open.ade import agents
    from proofpane_open import store
    with store.connect() as db:
        body = agents._mirror_body(agents._row(db, client.get("/api/auth/me", headers=owner).json()["id"],
                                               made["id"]), {"email": "a@example.org"})
    assert (body["unattended_mode"], body["max_budget_usd"]) == ("auto", 2.5) and body["precheck"].startswith("test")
    for bad in ({"unattended_mode": "default"}, {"max_budget_usd": 0}, {"est_usd_per_day": "lots"},
                {"precheck": "x" * 2001}):
        assert client.put(f"{A}/{made['id']}", headers=owner, json=bad).status_code == 400, bad


def test_past_its_day_s_estimated_cost_an_agent_pauses_until_the_next_day(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    made = agent(client, owner, est_usd_per_day=1)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    first, second = made_runs(client, owner, made["id"], 2, monkeypatch)
    now = int(time.time())
    agents._apply(user_id, [{"run_id": first, "agent_id": made["id"], "state": "done", "cost_est_usd": 0.6,
                             "queued_at": now}])
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"
    agents._apply(user_id, [{"run_id": second, "agent_id": made["id"], "state": "done", "cost_est_usd": 0.6,
                             "queued_at": now}])
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    assert got["state"] == "paused" and got["paused_reason"].startswith("Today's estimated cost passed its limit of $1.00")
    assert got["paused_until"] == now - now % 86400 + 86400
    assert client.post(f"{A}/{made['id']}/runs", headers=owner, json={}).status_code == 409
    agents.poll_all(now=got["paused_until"])                        # the next day: the pause is lifted
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"


def test_a_timed_pause_ends_at_its_time_and_yesterday_s_cost_pauses_nothing(client, owner):
    from proofpane_open.ade import agents
    from proofpane_open import store
    assert agents._paused({"paused": 1, "paused_until": 100}, now=99) is True
    assert agents._paused({"paused": 1, "paused_until": 100}, now=100) is False
    assert agents._paused({"paused": 1, "paused_until": None}, now=10 ** 10) is True
    made = agent(client, owner, est_usd_per_day=1)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_totals (agent_id, user_id, runs, done, failed, blocked, seconds, tokens, "
                   "cost_est_usd, today, runs_today, est_usd_today) VALUES (?,?,1,1,0,0,1,1,5,?,1,5)",
                   (made["id"], user_id, agents._today(now - 86400)))
        agents._day_limit(db, made["id"], now)              # yesterday's totals: nothing to pause today
    assert client.get(f"{A}/{made['id']}", headers=owner).json()["state"] == "idle"


def test_a_live_run_past_80_percent_of_its_context_is_told_once(client, owner, monkeypatch):
    from proofpane_open.ade import agents, push
    made = agent(client, owner, autocompact_window=300000, max_compactions=1)
    assert (made["autocompact_window"], made["max_compactions"]) == (300000, 1)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    context = {"model_window": 1000000, "compaction_window": 300000, "peak_tokens": 255000, "peak_pct": 0.85,
               "compactions": 1, "compaction_log": [{"at": 1, "trigger": "auto", "pre_tokens": 290000,
                                                     "post_tokens": 9000, "secret": "x"}],
               "fragmented": False, "retention": 0.5, "lost": ["docs/labels.md"], "evil": "<script>"}
    agents._apply(user_id, [{"run_id": run_id, "agent_id": made["id"], "state": "working", "context": context}])
    kept = client.get(f"{A}/{made['id']}", headers=owner).json()["runs"][0]["context"]
    assert kept["peak_pct"] == 0.85 and kept["lost"] == ["docs/labels.md"] and "evil" not in kept
    assert "secret" not in kept["compaction_log"][0]
    items = dict(push.items_for(user_id, [], int(time.time())))
    assert (items["agent-context:" + run_id].title, items["agent-context:" + run_id].url) == ("An agent needs a look",
                                                                                              "/app/#agents")
    agents._apply(user_id, [{"run_id": run_id, "agent_id": made["id"], "state": "done", "context": context}])
    assert not any(k.startswith("agent-context:") for k in dict(push.items_for(user_id, [], int(time.time()))))


def test_a_run_s_tree_is_copied_at_its_end_and_read_from_the_app_after(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"]}
    (run_id,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    assert client.get(f"{A}/runs/{run_id}/tree", headers=owner).status_code == 409    # no copy, no machine
    agents._apply(user["id"], [{"run_id": run_id, "agent_id": made["id"], "state": "done", "started_at": 1,
                                "ended_at": 2}])
    tree = {"turns": [{"text": "hello", "tools": [{"name": "Read", "target": "a.py", "ms": 3}]}], "requests": 1}
    asked, kept = [], [False]

    def call(endpoint, method, path, body=None, timeout=30):
        asked.append(path)
        return {"tree": tree, "copy": kept[0]}
    monkeypatch.setattr(agents.client, "call", call)
    agents._copy_trees(user, "endpoint")                                # not its kept copy yet: not taken
    assert client.get(f"{A}/runs/{run_id}/tree", headers=owner).status_code == 409
    kept[0] = True
    agents._copy_trees(user, "endpoint")
    agents._copy_trees(user, "endpoint")                                # copied once
    assert asked == [f"/agents/runs/{run_id}/tree"] * 2
    assert client.get(f"{A}/runs/{run_id}/tree", headers=owner).json() == {"tree": agents.clean_tree(tree), "copy": True}
    assert agents.clean_tree(tree)["turns"][0]["tools"][0] == {"id": "", "name": "Read", "target": "a.py", "ms": 3,
                                                               "error": False}


def test_runs_are_kept_90_days_with_their_trees(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    from proofpane_open import store
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    old, recent = made_runs(client, owner, made["id"], 2, monkeypatch)
    now = int(time.time())
    for run_id, ended in ((old, now - 91 * 86400), (recent, now - 89 * 86400)):
        agents._apply(user_id, [{"run_id": run_id, "agent_id": made["id"], "state": "done", "started_at": ended - 10,
                                 "ended_at": ended}])
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_trees (run_id, user_id, tree, copied_at) VALUES (?, ?, '{}', 1)",
                   (old, user_id))
    agents.poll_all(now=now)
    got = client.get(f"{A}/{made['id']}", headers=owner).json()
    assert [r["run_id"] for r in got["runs"]] == [recent] and got["totals"]["runs"] == 1
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_trees").fetchone()[0] == 0


def test_verify_holds_a_run_to_its_own_branch():
    from proofpane_open.ade import finishes
    report = {"session": "a" * 12, "branch": "pp/agent-" + "b" * 12, "base": "1" * 40, "head": "2" * 40,
              "label": "local/demo", "repo": "demo", "task": "Triage", "too_large": False, "diff": ""}
    assert finishes.check_report(report, "pp/agent-" + "b" * 12) is report
    with pytest.raises(finishes.BadReport):
        finishes.check_report(report)                               # a session's own is pp/<its id>
    with pytest.raises(finishes.BadReport):
        finishes.check_report(dict(report, branch="pp/agent-" + "c" * 12), "pp/agent-" + "b" * 12)
    assert finishes.check_report(dict(report, branch="pp/" + "a" * 12)) is not None


def test_keep_as_an_agent_carries_what_the_chat_had(client, owner, env):
    r = client.post("/api/ade/workspace/start", headers=owner)
    assert r.status_code == 200 and r.json()["running"] is True, r.text
    s = client.post("/api/ade/workspace/sessions", headers=owner,
                    json={"repo": "demo", "program": "chat", "task": "run echo hi",
                          "chat_settings": {"model": "sonnet"}})
    assert s.status_code == 200, s.text
    sid = s.json()["id"]
    deadline = time.time() + 30
    while True:
        log = client.get(f"/api/ade/workspace/sessions/{sid}/chat", headers=owner).json()
        if log["pending"]:
            break
        assert time.time() < deadline
        time.sleep(0.1)
    client.post(f"/api/ade/workspace/sessions/{sid}/chat/answer", headers=owner,
                json={"id": log["pending"][0], "decision": "allow"})

    def answered(decision):
        deadline = time.time() + 30
        while True:
            log = client.get(f"/api/ade/workspace/sessions/{sid}/chat", headers=owner).json()
            if log["pending"]:
                break
            assert time.time() < deadline
            time.sleep(0.1)
        client.post(f"/api/ade/workspace/sessions/{sid}/chat/answer", headers=owner,
                    json={"id": log["pending"][0], "decision": decision})
    deadline = time.time() + 30
    while client.get(f"/api/ade/workspace/sessions/{sid}/chat", headers=owner).json()["state"] != "idle":
        assert time.time() < deadline
        time.sleep(0.1)
    client.post(f"/api/ade/workspace/sessions/{sid}/chat", headers=owner, json={"text": "run echo refused"})
    answered("deny")                            # a call the owner refused is not offered as a rule
    got = client.get(f"{A}/draft?session={sid}", headers=owner)
    assert got.status_code == 200, got.text
    draft = got.json()
    assert (draft["repo"], draft["base_ref"], draft["brief"]) == ("demo", f"pp/{sid}", "run echo hi")
    assert draft["chat_settings"] == {"model": "sonnet"} and draft["candidates"] == ["Bash(echo hi)"]
    kept = agent(client, owner, name=draft["name"], brief=draft["brief"], base_ref=draft["base_ref"],
                 allow=draft["candidates"], from_session=sid)
    assert kept["from_session"] == sid and kept["allow"] == ["Bash(echo hi)"]


def test_a_run_that_waits_or_failed_is_told_once_and_a_new_question_again(client, owner, monkeypatch):
    from proofpane_open.ade import agents, push
    made = agent(client, owner, name="Release notes")
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    now = int(time.time())
    waits, failed, old = made_runs(client, owner, made["id"], 3, monkeypatch)
    agents._apply(user_id, [{"run_id": waits, "agent_id": made["id"], "state": "waiting",
                             "question": "Which tag?", "queued_at": now},
                            {"run_id": failed, "agent_id": made["id"], "state": "failed",
                             "reason": "the turn ended with an error", "ended_at": now, "queued_at": now},
                            {"run_id": old, "agent_id": made["id"], "state": "failed",
                             "reason": "long ago", "ended_at": now - 2 * 86400, "queued_at": now - 2 * 86400}])
    got = dict(agents.alerts(user_id, now))
    assert sorted(k.split(":")[0] for k in got) == ["agent-failed", "agent-question"]
    # in fixed words: never the agent's name, its question or why it failed (tests/test_push_words.py)
    (question,) = [v for k, v in got.items() if k.startswith("agent-question")]
    assert (question, got["agent-failed:" + failed]) == ("agent-waits", "agent-failed")
    items = dict(push.items_for(user_id, [], now))
    assert (items["agent-failed:" + failed].title, items["agent-failed:" + failed].url) == (
        "An agent's run failed", "/app/#agents")
    assert not [m for m in items.values() if "Release notes" in repr(m) or "Which tag" in repr(m)]
    agents._apply(user_id, [{"run_id": waits, "agent_id": made["id"], "state": "waiting", "question": "Which branch?"}])
    again = [k for k, _ in agents.alerts(user_id, now) if k.startswith("agent-question")]
    assert again and again[0] not in got                    # a new question is told again


def test_a_blocked_or_stopped_run_is_not_counted_against_its_agent(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    made = agent(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    now = int(time.time())
    ids = made_runs(client, owner, made["id"], 5, monkeypatch)
    agents._apply(user_id, [
        {"run_id": run_id, "agent_id": made["id"], "state": state, "queued_at": now, "started_at": now,
         "ended_at": now + 10, "cost_est_usd": 0.5}
        for run_id, state in zip(ids, ("done", "failed", "blocked", "stopped", "done"))])
    totals = client.get(f"{A}/{made['id']}", headers=owner).json()["totals"]
    assert (totals["runs"], totals["done"], totals["failed"], totals["blocked"]) == (5, 2, 1, 1)
    assert totals["success_rate"] == pytest.approx(2 / 3, abs=1e-4)     # done over done and failed: 2 of 3
    assert totals["seconds"] == 50 and totals["cost_est_usd"] == pytest.approx(2.5)
