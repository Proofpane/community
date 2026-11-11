"""The shell's summary (ade/summary.py), GET /api/ade/summary: the workspace's state, the counts the title bar's badges
show, and a stamp for each list the page keeps, which moves when its list does and says nothing of what it holds; no
word a session, machine or app sent, sealed or in the clear, is in it."""

import json
import threading
import time

import pytest

from conftest import signed_up
from proofpane_open import store
from proofpane_open.ade import agents, approvals, summary
from test_ade_link import paired, post
from test_workspace_fly import ADMIN as FLY_ADMIN, fake  # noqa: F401  the fake Fly API, a fixture
from workspace_fixtures import make_repo

STAMPS = {"sessions", "repos", "approvals", "proposals", "asks", "links", "history"}


@pytest.fixture
def local(monkeypatch, tmp_path):
    home = tmp_path / "home"     # no git identity of the machine's user in a test
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    from proofpane_open.ade import workspaces
    yield workspaces
    workspaces.shutdown()


def _read(client, h):
    r = client.get("/api/ade/summary", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def _user_id(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def test_the_summary_needs_the_account_s_sign_in(client):
    assert client.get("/api/ade/summary").status_code == 401
    assert client.get("/api/ade/summary", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_on_a_deployment_with_no_workspace_the_lists_of_the_channel_are_still_stamped(client, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_ADE", raising=False)
    h = signed_up(client)
    got = _read(client, h)
    assert got["workspace"] == {"enabled": False}
    assert got["asks"] is None and got["agents"] is None and got["proposals"] is None      # no connector either
    assert got["approvals"] == 0 and got["links"] == {"live": 0, "alerts": 0}
    assert set(got["stamps"]) == STAMPS
    assert {k for k, v in got["stamps"].items() if v is None} == {"sessions", "repos", "asks", "proposals"}
    assert all(len(v) == 16 and int(v, 16) >= 0 for v in got["stamps"].values() if v is not None)


def test_a_workspace_never_started_is_not_made_and_one_started_once_is_after_it_stops(client, local):
    h = signed_up(client)
    first = _read(client, h)["workspace"]
    assert first["running"] is False and first["made"] is False
    assert client.get("/api/ade/workspace", headers=h).json()["made"] is False
    assert client.post("/api/ade/workspace/start", headers=h).json()["made"] is True
    assert client.post("/api/ade/workspace/stop", headers=h).json()["running"] is False
    after = _read(client, h)["workspace"]
    assert after["running"] is False and after["made"] is True


def test_a_fly_workspace_is_made_once_its_machine_is(fake):
    from proofpane_open.ade import workspaces
    p = workspaces.for_user(FLY_ADMIN)
    first = p.status(FLY_ADMIN)
    assert first["made"] is False and first["running"] is False
    p.ensure(FLY_ADMIN)
    p.stop(FLY_ADMIN)
    stopped = p.status(FLY_ADMIN)
    assert stopped["made"] is True and stopped["running"] is False


def test_a_running_workspace_s_lists_are_stamped_and_a_stamp_moves_only_with_its_list(client, local, env):
    h = signed_up(client)
    stopped = _read(client, h)
    assert stopped["stamps"]["sessions"] is None and stopped["asks"] is None and stopped["agents"] == 0
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
    make_repo(str(env / "workspaces" / _user_id(client, h) / "repos" / "demo"))
    one = _read(client, h)
    assert one["workspace"]["running"] is True and one["asks"] == 0
    assert all(one["stamps"][k] for k in ("sessions", "repos", "asks"))
    again = _read(client, h)
    assert again["stamps"] == one["stamps"]                     # nothing changed: every stamp the same
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "shell", "task": "Look"})
    assert r.status_code == 200, r.text
    moved = _read(client, h)
    assert moved["stamps"]["sessions"] != one["stamps"]["sessions"]
    assert {k for k in STAMPS if moved["stamps"][k] != one["stamps"][k]} == {"sessions"}
    # each stamp is the digest of the list its own route serves
    assert moved["stamps"]["repos"] == summary.stamp(client.get("/api/ade/workspace/repos", headers=h).json())


def test_a_workspace_that_does_not_run_is_not_asked_for_its_lists(client, local, monkeypatch):
    from proofpane_open.ade import workspace_routes
    h = signed_up(client)
    asked = []
    for name in ("sessions", "repos", "asks"):
        monkeypatch.setattr(workspace_routes, name, lambda user, name=name: asked.append(name))
    got = _read(client, h)
    assert got["workspace"]["running"] is False and asked == []
    assert got["stamps"]["sessions"] is None and got["asks"] is None


def test_the_counts_are_what_the_badges_count(client, local):
    h = signed_up(client)
    uid = _user_id(client, h)
    _, token = approvals.create_token(uid, "the Mac session")
    identity = approvals.hook_identity(token)
    approvals.request(identity, "git push origin main")
    answered = approvals.request(identity, "git push origin other")
    approvals.decide(uid, answered["id"], False)
    before = _read(client, h)
    assert before["approvals"] == 1 and before["links"] == {"live": 0, "alerts": 0}
    s = paired(client, h)
    other = paired(client, h, name="cloud planner", kind="cloud")
    assert post(s, "question", "Should I push?", to="owner").status_code == 200
    assert post(s, "question", "Which branch?", to="cloud planner").status_code == 200    # not the owner's
    assert post(s, "step", "ran the tests").status_code == 200
    assert other.link_id != s.link_id
    got = _read(client, h)
    assert got["links"] == {"live": 2, "alerts": 1}          # the question to the owner; a step needs no one
    assert got["stamps"]["links"] != before["stamps"]["links"]
    # silent past two of its intervals, nothing it signed heard since: one more
    with store.connect() as db:
        db.execute("UPDATE ade_links SET last_heartbeat_at = ?, last_seen_at = ?, created_at = ? WHERE id = ?",
                   (int(time.time()) - 3 * 3600, int(time.time()) - 3 * 3600, int(time.time()) - 3 * 3600, s.link_id))
    assert _read(client, h)["links"] == {"live": 2, "alerts": 2}


def test_the_agents_count_is_the_tree_s_runs_waiting_for_the_owner(client, local):
    h = signed_up(client)
    uid = _user_id(client, h)
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                   ("a1", uid, "Triage", "Look at the issues.", now, now))
        for run, state, loop in (("r1", "waiting", 0), ("r2", "working", 0), ("r3", "waiting", 1), ("r4", "waiting", 0),
                                 ("r5", "done", 0)):
            db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, loop, queued_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", (run, "a1", uid, state, loop, now, now))
    from proofpane_open import auth
    assert _read(client, h)["agents"] == len(agents.listing(auth.user(uid), sync=False)["waiting"]) == 2


def test_proposals_are_counted_as_the_badge_counts_them_and_not_at_all_without_a_connector(client, monkeypatch):
    from proofpane_open import oauth
    from proofpane_open.ade import proposals
    h = signed_up(client)
    assert _read(client, h)["proposals"] is None
    monkeypatch.setattr(oauth, "enabled", lambda: True)
    waiting = [{"id": "p1", "state": "proposed", "kind": "instruction"},
               {"id": "p2", "state": "proposed", "kind": "approval"},     # counted as the held command it is on
               {"id": "p3", "state": "proposed", "kind": "stop"}]
    monkeypatch.setattr(proposals, "waiting", lambda user_id, now=None: list(waiting))
    got = _read(client, h)
    assert got["proposals"] == 2 and got["stamps"]["proposals"] == summary.stamp(waiting)


def test_no_word_a_session_sent_is_in_the_summary_sealed_or_not(client, local):
    h = signed_up(client)
    s = paired(client, h)
    words = ["Should-I-push-the-release-branch", "ran-the-migration-tests", "Fix-the-invoice-totals",
             "acme-billing-service", "e2e1.U0VBTEVELVRJVExF"]
    assert post(s, "question", words[0], to="owner").status_code == 200
    assert post(s, "step", words[1]).status_code == 200
    assert s.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"doing": words[1]}}).status_code == 200
    listed = s.call("PUT", "/history", {"sessions": [
        {"id": "s1", "title": words[2], "project": "/Users/me/code/" + words[3], "branch": "main",
         "started_at": 1, "last_at": 2},
        {"id": "s2", "title": "Second session", "project": "/Users/me/code/other", "branch": "dev",
         "started_at": 3, "last_at": 4}]})
    assert listed.status_code == 200, listed.text
    # a row as a sealed one is kept: its words inside an envelope, those beside it empty
    with store.connect() as db:
        db.execute("UPDATE ade_link_history SET sealed = ?, title = '', project = NULL, branch = NULL "
                   "WHERE session_id = 's2'", (words[4],))
    channel = client.get("/api/ade/link/sessions", headers=h).text
    history = client.get("/api/ade/link/history", headers=h).text
    assert all(w in channel + history for w in words if w != words[3]) and words[3] in history   # served there
    got = _read(client, h)
    text = json.dumps(got)
    assert not [w for w in words if w in text]
    assert "e2e1" not in text and "/Users/" not in text
    assert got["stamps"]["history"] == summary.stamp(json.loads(history))
    # the linked sessions' stamp leaves out the server's time, which every answer carries
    assert got["stamps"]["links"] == summary.stamp({k: v for k, v in json.loads(channel).items() if k != "server_time"})


def test_a_list_the_workspace_could_not_give_has_no_stamp_and_the_rest_is_read(client, local, monkeypatch):
    from proofpane_open.ade import workspace_client, workspace_routes
    h = signed_up(client)
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 200

    def unreadable(user):
        raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)

    monkeypatch.setattr(workspace_routes, "sessions", unreadable)
    got = _read(client, h)
    assert got["workspace"]["running"] is True
    assert got["stamps"]["sessions"] is None and got["stamps"]["repos"] and got["stamps"]["approvals"]


def test_a_provider_that_cannot_say_leaves_the_workspace_null_with_why(client, local, monkeypatch):
    from proofpane_open.ade import workspaces
    h = signed_up(client)

    def failing(self, user):
        raise workspaces.WorkspaceAccess(502, "Fly: the machines API did not answer")

    monkeypatch.setattr(workspaces.LocalProvider, "status", failing)
    got = _read(client, h)
    assert got["workspace"] is None and got["workspace_error"] == "Fly: the machines API did not answer"
    assert got["agents"] is None and got["stamps"]["sessions"] is None and got["stamps"]["approvals"]


def test_another_account_s_counts_and_lists_are_not_in_mine(client, local):
    h = signed_up(client)
    other = signed_up(client, "second-admin@example.com")
    s = paired(client, other)
    assert post(s, "question", "Mine?", to="owner").status_code == 200
    _, token = approvals.create_token(_user_id(client, other), "theirs")
    approvals.request(approvals.hook_identity(token), "git push")
    got = _read(client, h)
    assert got["approvals"] == 0 and got["links"] == {"live": 0, "alerts": 0}
    theirs = _read(client, other)
    assert theirs["approvals"] == 1 and theirs["links"] == {"live": 1, "alerts": 1}
    assert theirs["stamps"]["links"] != got["stamps"]["links"]


# The workspace's agent may not answer (a machine half up, an agent stopped). Its three lists are read
# at once beside the account's own, and the summary waits for them only so long.

class Stalled:
    """A workspace agent that does not answer: each read of a list waits until `go` is set (at most HOLD seconds, then
    fails), and answers the list with one item (the asks, one ask); every read asked for is counted in `calls`."""

    HOLD = 10

    def __init__(self):
        self.go = threading.Event()
        self.calls = []

    def reader(self, name):
        def read(user):
            self.calls.append(name)
            assert self.go.wait(self.HOLD), "the agent was never let go"
            return {"on": False, "asks": [{"id": "a1"}]} if name == "asks" else {name: [{"id": name}]}
        return read


LISTS = ("sessions", "repos", "asks")     # the workspace's own lists, which its agent gives
WAIT = 1.0                                # the summary's wait for them, in these tests


@pytest.fixture
def stalled(client, local, monkeypatch):
    from proofpane_open.ade import workspace_routes
    h = signed_up(client)
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
    agent = Stalled()
    for name in LISTS:
        monkeypatch.setattr(workspace_routes, name, agent.reader(name))
    monkeypatch.setattr(summary, "WORKSPACE_WAIT", WAIT, raising=False)
    yield h, agent
    agent.go.set()                  # the reads still waiting end with the test


def _waiting_approval(client, h):
    _, token = approvals.create_token(_user_id(client, h), "the Mac session")
    approvals.request(approvals.hook_identity(token), "git push origin main")


def test_an_agent_that_does_not_answer_holds_the_summary_no_longer_than_the_wait(client, stalled):
    h, agent = stalled
    _waiting_approval(client, h)
    began = time.monotonic()
    got = _read(client, h)
    took = time.monotonic() - began
    assert took < 2 * WAIT, took                                 # one wait for the three lists together
    assert got["workspace"]["running"] is True
    assert got["approvals"] == 1 and got["stamps"]["approvals"] and got["stamps"]["links"] and got["stamps"]["history"]
    assert {k for k in LISTS if got["stamps"][k] is None} == set(LISTS)
    assert got["asks"] is None
    assert sorted(agent.calls) == sorted(LISTS)                  # each list asked for once, all at once


def test_a_summary_while_the_reads_are_under_way_waits_on_them_and_asks_the_agent_nothing_more(client, stalled):
    h, agent = stalled
    _read(client, h)
    _read(client, h)
    assert sorted(agent.calls) == sorted(LISTS)


def test_a_read_that_finished_after_its_summary_stands_in_for_the_next_one_s_while_recent(client, stalled, monkeypatch):
    h, agent = stalled
    assert _read(client, h)["stamps"]["sessions"] is None
    agent.go.set()                                               # the agent answers, after the summary did
    _wait_for(lambda: all(summary._reads[(_user_id(client, h), k)].done() for k in LISTS))
    agent.go = threading.Event()                                 # and stalls again: the next reads do not finish
    got = _read(client, h)
    assert got["stamps"]["sessions"] == summary.stamp({"sessions": [{"id": "sessions"}]})
    assert got["stamps"]["repos"] == summary.stamp({"repos": [{"id": "repos"}]})
    assert got["asks"] == 1
    assert len(agent.calls) == 2 * len(LISTS)                    # those reads done, each list was asked for again
    monkeypatch.setattr(summary, "LATE_KEPT", 0.0)               # past what is kept: no stamp
    got = _read(client, h)
    assert {k for k in LISTS if got["stamps"][k] is None} == set(LISTS)
    assert got["asks"] is None


def test_a_list_whose_read_fails_has_no_stamp_whatever_an_earlier_read_said(client, local, monkeypatch):
    from proofpane_open.ade import workspace_client, workspace_routes
    h = signed_up(client)
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
    assert _read(client, h)["stamps"]["sessions"]

    def unreadable(user):
        raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)

    monkeypatch.setattr(workspace_routes, "sessions", unreadable)
    got = _read(client, h)
    assert got["stamps"]["sessions"] is None and got["stamps"]["repos"]


def test_one_account_s_reads_are_never_waited_on_or_stood_in_with_for_another(client, local, monkeypatch):
    from conftest import ADMIN
    from proofpane_open.ade import workspace_routes
    monkeypatch.setenv("OPEN_EDITION_ADMINS", f"{ADMIN},second-admin@example.com")
    a = signed_up(client)
    b = signed_up(client, "second-admin@example.com")
    for h in (a, b):
        assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
    a_id, b_id = _user_id(client, a), _user_id(client, b)
    held = {}                       # an account's id: the event its agent waits on; one not here answers at once

    def reader(name):
        def read(user):
            if user["id"] in held:
                assert held[user["id"]].wait(Stalled.HOLD), "the agent was never let go"
            items = [{"id": name, "for": user["id"]}]
            return {"on": False, "asks": items} if name == "asks" else {name: items}
        return read

    for name in LISTS:
        monkeypatch.setattr(workspace_routes, name, reader(name))
    monkeypatch.setattr(summary, "WORKSPACE_WAIT", WAIT, raising=False)

    def sessions_of(user_id):
        return summary.stamp({"sessions": [{"id": "sessions", "for": user_id}]})

    try:
        held[a_id] = threading.Event()                           # A's agent does not answer
        assert _read(client, a)["stamps"]["sessions"] is None
        assert _read(client, b)["stamps"]["sessions"] == sessions_of(b_id)   # B's read is B's own, not a wait on A's
        held.pop(a_id).set()                                     # A's answers, later than B's
        assert _read(client, a)["stamps"]["sessions"] == sessions_of(a_id)
        held[b_id] = threading.Event()                           # now B's does not answer
        assert _read(client, b)["stamps"]["sessions"] == sessions_of(b_id)   # B's own last read stands in, never A's
    finally:
        for event in held.values():
            event.set()


def test_a_fault_of_ours_in_a_list_s_read_is_not_taken_for_the_workspace_s(client, local, monkeypatch):
    from proofpane_open.ade import workspace_routes
    h = signed_up(client)
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 200

    def broken(user):
        raise RuntimeError("a fault in the route, not in the workspace")

    monkeypatch.setattr(workspace_routes, "sessions", broken)
    with pytest.raises(RuntimeError, match="a fault in the route"):     # the summary fails, as it always has
        client.get("/api/ade/summary", headers=h)


def _wait_for(done, within=10.0):
    end = time.monotonic() + within
    while not done():
        assert time.monotonic() < end, "not done in time"
        time.sleep(0.02)
