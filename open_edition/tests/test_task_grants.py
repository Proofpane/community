"""A task's grant in Proofpane Community: one record per task, the
owner's statement of its scope, signed with one of the account's passkeys at the task's creation and at each widening,
as a link command is signed; a widening an agent or a linked session asks for waits in the owner's list and takes
effect only once the owner signs it; a change without the owner's signature is refused; an attempt's message names the
grant's digest; and the task, the board and the owner's list show it. Driven through the app's routes and the tasks'
own code, against test_agent_tasks.py's fake workspace, with test_passkeys.py's software authenticator; payloads are
canonicalised here with the open core's canonicaliser, and a grant's digest is computed here from its words."""

import hashlib
import json
import sqlite3
import time
import uuid

import pytest
from fastapi import HTTPException

import ade_link
from conftest import signed_up
from proofpane_open import passkeys, store
from proofpane_open.ade import (agents, progress_board as board, signers,  # puts the open packages on sys.path
                                tasks, workspace_routes)
from proofpane_core.canonical import canonicalize
from proofpane_workspace import agents as wsagents
from test_agent_tasks import (ACC, A, T, finish, make_agent, make_task, own, owner, press, received, record,  # noqa: F401
                              row, run, task_of, view_of, ws)
from test_ade_link import paired, revoke as revoke_link
from test_passkeys import Authenticator, add, as_step, remove, unb64u
from test_task_attempt import _Live, _at_work, _in_loop
from test_task_grant_workspace import stand_in_sdk, tools_of

GRANTS = T + "/grant-widenings"
# what a grant's digest hashes before its canonical bytes, and a zero byte (ade/task_grants.py), written here from its
# words so that nothing here trusts the module to check itself
TAG = b"proofpane-task-grant/1"
RUNGS = {"merge": "L2", "deploy": "L2-high"}
WEEK = 7 * 86400


@pytest.fixture
def device(client, owner):
    h, _ = owner
    d = Authenticator()
    r = add(client, h, d)
    assert r.status_code == 200, r.text
    return d


@pytest.fixture
def no_sessions(monkeypatch):
    """The board with no workspace sessions and no other module's rows (test_ade_progress_board.py's `stopped`)."""
    def refuse(user):
        raise HTTPException(status_code=409, detail="The workspace is not running: start it first.")
    monkeypatch.setattr(workspace_routes, "sessions", refuse)
    monkeypatch.setattr(board, "_SOURCES", {})


def ask_digest(task):
    return "sha256:" + hashlib.sha256(task["goal"].encode("utf-8")).hexdigest()


def digest_of(p):
    return hashlib.sha256(TAG + b"\x00" + canonicalize(p)).hexdigest()


def nonce(client, h):
    r = client.post("/api/ade/link/nonce", headers=h, json={"purpose": "task-grant"})
    assert r.status_code == 200, r.text
    return r.json()


def own_repo(task, merge=("main",)):
    return {"repo": "demo", "push": ["pp/task-" + task["id"]], "merge": sorted(merge)}


def payload(client, h, task, version=1, prev="", n=None, **over):
    """A grant's payload as the page composes it around a nonce the server gave: the task's own repository with its
    own branch, nothing else, a lease of a week and the default rungs; `over` replaces a field, and DROP-free:
    a test that leaves a field out deletes it itself."""
    n = n or nonce(client, h)
    now = n["server_time"]
    p = {"v": 2, "deployment": n["deployment"], "purpose": "task-grant", "account": n["account"], "nonce": n["nonce"],
         "id": uuid.uuid4().hex, "task": task["id"], "version": version, "prev": prev, "ask": ask_digest(task),
         "requester": {"kind": "owner"}, "widening": "", "repositories": [own_repo(task)], "environments": [],
         "secrets": [], "egress": [], "lease_end": now + WEEK, "rungs": dict(RUNGS), "created_at": now,
         "expires_at": now + 600}
    p.update(over)
    return p


def sign(client, h, device, task_id, p, signed=None, **kw):
    """POST the grant `p` with `device`'s answer to the step challenge of `signed`'s canonical bytes (`p`'s own by
    default), as the page asks for it."""
    return client.post(f"{T}/{task_id}/grant", headers=h,
                       json={"payload": p, "credential": device.get(as_step(canonicalize(signed or p)), **kw)})


def granted(client, h, device, task, **over):
    """A grant signed for `task` (its first unless `over` says otherwise): its payload."""
    p = payload(client, h, task, **over)
    r = sign(client, h, device, task["id"], p)
    assert r.status_code == 200, r.text
    return p


def grant_of(client, h, task_id):
    return task_of(client, h, task_id)["grant"]


def versions_of(client, h, task_id):
    r = client.get(f"{T}/{task_id}/grant", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["versions"]


def waiting(client, h):
    r = client.get(GRANTS, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["widenings"]


def send(user, ws, run_id):
    """The attempt message the app sends the workspace for `run_id` (agents._send_now), its agent mirrored first."""
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send_now(user, "http://workspace.test")
    return next(b["task"] for b in reversed(ws.sent) if b["run_id"] == run_id and "task" in b)


def asks(*items):
    """An attempt's record's asks for more in its task's grant, each {client_id, add, ...}."""
    return list(items)


def seen(user, run_id, state="working", **fields):
    """A run's record as the workspace holds it, its task part carrying `fields`, copied by the app's poll as the
    workspace's own view of it gives it (proofpane_workspace/agents.py, Agents.view, which reads nothing of the
    workspace's state): what that view leaves out of a task part never reaches the app, so an attempt's asks reach the
    owner's list only through it."""
    given = view_of(run_id, state, **fields)
    held = {k: given[k] for k in ("state", "cause", "reason", "started_at", "ended_at", "rate_limits")}
    record = {"id": run_id, "agent_id": given["agent_id"], "run": dict(held, task=given["task"])}
    view = wsagents.Agents.view(None, record)
    agents._apply(user["id"], [view], record_seq=False)
    return view


def one_of(items):
    """The one item of a list that holds one."""
    assert len(items) == 1, items
    return items[0]


def the_ask(client, h):
    """The one ask that waits in the owner's list."""
    return one_of(waiting(client, h))


def started(client, h, user, steps=2, agent_id=None):
    own(user, ACC["A"])
    agent_id = agent_id or make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, steps=steps)
    assert press(client, h, task["id"], "start").status_code == 200
    return task, agent_id, row(task["id"])["lease_run"]


# ---- an unsigned change, a widening that waits for its signature, an attempt's digest, and a control ------------

def test_a_grant_changed_without_the_owner_s_signature_is_refused(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    # a grant with no signature beside it: the route takes the owner's signature, and nothing in its place
    r = client.post(f"{T}/{task['id']}/grant", headers=h, json={"payload": {"purpose": "task-grant",
                                                                              "task": task["id"]}})
    assert r.status_code == 422 and "signed" in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"]) is None
    first = granted(client, h, device, task)
    before = grant_of(client, h, task["id"])
    assert before["version"] == 1 and before["digest"] == digest_of(first)
    wider = payload(client, h, task, version=2, prev=before["digest"], egress=["pypi.org"])
    # 1. the widened grant with no credential beside it
    r = client.post(f"{T}/{task['id']}/grant", headers=h, json={"payload": wider})
    assert r.status_code == 422, r.text
    # 2. the owner's own passkey's answer, but to the grant as it was: the host added after the signature
    r = sign(client, h, device, task["id"], wider, signed=dict(wider, egress=[]))
    assert r.status_code == 400 and "canonical" in r.json()["detail"], r.text
    # 3. another account's passkey signing it, sent with the owner's sign-in
    other = signed_up(client, "other@example.com")
    theirs = Authenticator(-8)
    assert add(client, other, theirs).status_code == 200
    r = sign(client, h, theirs, task["id"], wider)
    assert r.status_code == 401 and "not one of this account's passkeys" in r.json()["detail"], r.text
    # 4. the other account signing it as its own, with its own sign-in: the task is not its
    r = sign(client, other, theirs, task["id"], payload(client, other, task, version=2, prev=before["digest"],
                                                        egress=["pypi.org"]))
    assert r.status_code == 404, r.text
    # 5. the task's own change naming a grant: a grant changes with its owner's signature, never there
    r = client.put(f"{T}/{task['id']}", headers=h, json={"state_version": task_of(client, h, task["id"])["state_version"],
                                                         "grant": {"egress": ["pypi.org"]}})
    assert r.status_code == 400 and "grant" in r.json()["detail"], r.text
    # none of them changed it
    assert grant_of(client, h, task["id"]) == before
    assert [v["version"] for v in versions_of(client, h, task["id"])] == [1]
    # and the owner's own signature over the widened grant is what changes it
    r = sign(client, h, device, task["id"], wider)
    assert r.status_code == 200, r.text
    after = grant_of(client, h, task["id"])
    assert (after["version"], after["egress"], after["digest"]) == (2, ["pypi.org"], digest_of(wider))


def test_a_widening_an_agent_asks_for_waits_in_the_owner_s_list_and_takes_effect_only_once_signed(
        client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user, steps=4)
    assert send(user, ws, first).get("grant_digest") is None
    # the attempt's agent asks, through its record, for another of the workspace's repositories and a host
    seen(user, first, "working", grant_asks=asks({"client_id": "a" * 32, "add": {
        "repositories": [{"repo": "tools", "push": ["release"]}], "egress": ["pypi.org"]}}))
    listed = client.get(GRANTS, headers=h)
    assert listed.status_code == 200, listed.text
    asked = one_of(listed.json()["widenings"])
    assert (asked["task_id"], asked["state"], asked["base_version"]) == (task["id"], "waiting", 0)
    assert asked["requester"] == {"kind": "agent", "id": agent_id, "name": "Worker"}
    assert asked["scope"]["repositories"] == [own_repo(task, merge=()),
                                              {"repo": "tools", "push": ["pp/task-" + task["id"], "release"],
                                               "merge": []}]
    assert asked["scope"]["egress"] == ["pypi.org"] and asked["scope"]["rungs"] == RUNGS
    # it waits: the task has no grant, and the next attempt's message names none
    assert grant_of(client, h, task["id"]) is None
    assert [w["id"] for w in task_of(client, h, task["id"])["grant_widenings"]] == [asked["id"]]
    finish(user, first, 1, 1)
    second = row(task["id"])["lease_run"]
    assert send(user, ws, second).get("grant_digest") is None
    # the owner signs what it asked for: then, and only then, it is the task's grant
    v1 = payload(client, h, task, requester={"kind": "agent", "id": agent_id}, widening=asked["id"], **asked["scope"])
    r = sign(client, h, device, task["id"], v1)
    assert r.status_code == 200, r.text
    got = grant_of(client, h, task["id"])
    assert (got["version"], got["digest"], got["widening"]) == (1, digest_of(v1), asked["id"])
    assert got["requester"]["kind"] == "agent" and got["requester"]["id"] == agent_id
    assert waiting(client, h) == []
    # a second ask widens the grant now signed: it waits too, and the attempts go on under the grant as signed
    seen(user, second, "working", grant_asks=asks({"client_id": "b" * 32, "add": {
        "secrets": [{"name": "PYPI_UPLOAD", "lease_seconds": 3600}]}}))
    wider = the_ask(client, h)
    assert wider["base_version"] == 1 and wider["scope"]["secrets"] == [{"name": "PYPI_UPLOAD", "lease_seconds": 3600}]
    assert grant_of(client, h, task["id"])["version"] == 1
    finish(user, second, 2, 2)
    third = row(task["id"])["lease_run"]
    assert send(user, ws, third)["grant_digest"] == digest_of(v1)
    v2 = payload(client, h, task, version=2, prev=digest_of(v1), requester={"kind": "agent", "id": agent_id},
                 widening=wider["id"], **wider["scope"])
    assert sign(client, h, device, task["id"], v2).status_code == 200
    finish(user, third, 3, 3)
    fourth = row(task["id"])["lease_run"]
    message = send(user, ws, fourth)
    assert (message["grant_digest"], message["grant_version"]) == (digest_of(v2), 2)


def test_an_attempt_s_message_names_the_grant_s_digest(client, owner, ws, device):
    h, user = owner
    task, _, first = started(client, h, user)
    message = send(user, ws, first)
    assert "grant_digest" in message and message["grant_digest"] is None and message["grant_version"] is None
    p = granted(client, h, device, task)
    finish(user, first, 1, 1)
    second = row(task["id"])["lease_run"]
    message = send(user, ws, second)
    assert (message["grant_digest"], message["grant_version"]) == (digest_of(p), 1)
    # the digest is of the grant as kept: its canonical bytes, as the passkey signed them, under the grant's tag
    (kept,) = versions_of(client, h, task["id"])
    assert kept["payload"].encode("utf-8") == canonicalize(p)
    assert hashlib.sha256(TAG + b"\x00" + kept["payload"].encode("utf-8")).hexdigest() == message["grant_digest"]


def test_control_a_task_with_no_repository_needs_no_grant_to_start_as_today(client, owner, ws):
    """Control: an agent with no repository, its task started with no grant."""
    h, user = owner
    own(user, ACC["A"])
    r = client.post(A, headers=h, json={"name": "Writer", "brief": "Write it.", "account": ACC["A"]})
    assert r.status_code == 200, r.text
    task = make_task(client, h, r.json()["id"])
    assert press(client, h, task["id"], "start").status_code == 200
    lease = row(task["id"])["lease_run"]
    assert lease is not None
    message = send(user, ws, lease)
    assert message["id"] == task["id"] and message.get("grant_digest") is None


def test_an_attempt_s_own_ask_reaches_the_owner_s_list_through_the_workspace_s_view(client, owner, ws, device, tmp_path,
                                                                                    monkeypatch):
    """End to end: an attempt at work calls its own ask_grant (task_attempt.py), the
    record it emits is its run's record's task part (task_runs.emit), the workspace's view of that record is what the
    app's poll copies (agents.py, Agents.view), and the ask waits in the owner's list as the task's agent's."""
    stand_in_sdk(monkeypatch)
    h, user = owner
    task, agent_id, first = started(client, h, user)
    held = run(first)
    live = _Live(tmp_path, run_id=first, task_id=task["id"], agent_id=agent_id, fence=held["fence"],
                 step=held["task_step"], attempt=held["task_attempt"], backend=json.loads(held["backend"]),
                 branch="pp/task-" + task["id"])

    async def body():
        a = await live.up()
        _at_work(a)
        tools = tools_of(a)
        assert "ask_grant" in tools, sorted(tools)
        answer = await tools["ask_grant"].handler({"add": {"repositories": [{"repo": "tools", "push": ["release"]}],
                                                           "egress": ["pypi.org"]}})
        assert answer.get("is_error") is not True, answer
    _in_loop(body)
    emitted = live.records[-1][0]
    record = {"id": first, "agent_id": agent_id,
              "run": {"state": "working", "started_at": int(time.time()), "task": emitted["task"]}}
    agents._apply(user["id"], [wsagents.Agents.view(None, record)], record_seq=False)
    asked = the_ask(client, h)
    assert asked["requester"] == {"kind": "agent", "id": agent_id, "name": "Worker"}
    assert asked["adds"]["egress"] == ["pypi.org"] and asked["base_version"] == 0
    assert [r["repo"] for r in asked["scope"]["repositories"]] == ["demo", "tools"]
    # copied again, as the poll copies a record whose task part carries the same asks, it asks nothing more
    agents._apply(user["id"], [wsagents.Agents.view(None, record)], record_seq=False)
    assert [w["id"] for w in waiting(client, h)] == [asked["id"]]


# ---- what keeps a grant to its task, its account and its chain of versions -----------------------------------------

def test_a_grant_is_kept_to_its_own_task(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    one = make_task(client, h, make_agent(client, h, ACC["A"]))
    two = make_task(client, h, make_agent(client, h, ACC["A"]))
    # one's grant, sent to two's route
    r = sign(client, h, device, two["id"], payload(client, h, one))
    assert r.status_code == 422 and "another task" in r.json()["detail"], r.text
    # two's route, a grant naming two but its repository's branch one's
    r = sign(client, h, device, two["id"], payload(client, h, two, repositories=[own_repo(one)]))
    assert r.status_code == 422 and "pp/task-" + two["id"] in r.json()["detail"], r.text
    assert grant_of(client, h, one["id"]) is None and grant_of(client, h, two["id"]) is None


def test_a_grant_is_kept_to_its_chain_of_versions(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    # a second version with no first
    r = sign(client, h, device, task["id"], payload(client, h, task, version=2, prev="0" * 64))
    assert r.status_code == 409, r.text
    first = granted(client, h, device, task)
    # a first version again, after one
    r = sign(client, h, device, task["id"], payload(client, h, task))
    assert r.status_code == 409, r.text
    # the next version, naming another one before it
    r = sign(client, h, device, task["id"], payload(client, h, task, version=2, prev="0" * 64))
    assert r.status_code == 409, r.text
    # a version that skips one
    r = sign(client, h, device, task["id"], payload(client, h, task, version=3, prev=digest_of(first)))
    assert r.status_code == 409, r.text
    second = granted(client, h, device, task, version=2, prev=digest_of(first), egress=["pypi.org"])
    # the first, signed and accepted once, sent again with the very answer its passkey gave: refused
    r = sign(client, h, device, task["id"], first)
    assert r.status_code in (400, 409), r.text
    got = grant_of(client, h, task["id"])
    assert (got["version"], got["digest"]) == (2, digest_of(second))
    assert [v["version"] for v in versions_of(client, h, task["id"])] == [1, 2]


def test_a_nonce_signs_one_grant(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    n = nonce(client, h)
    first = granted(client, h, device, task, n=n)
    # a new payload, the next in the chain, around the nonce the first used up
    r = sign(client, h, device, task["id"], payload(client, h, task, version=2, prev=digest_of(first), n=n,
                                                    egress=["pypi.org"]))
    assert r.status_code == 400 and "nonce" in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"])["version"] == 1


def test_signing_moves_the_passkey_s_counter(client, owner, ws):
    h, user = owner
    counted = Authenticator(counter=10)
    assert add(client, h, counted).status_code == 200
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    p = payload(client, h, task)
    r = sign(client, h, counted, task["id"], p)
    assert r.status_code == 200, r.text
    with store.connect() as db:
        kept = db.execute("SELECT sign_count FROM passkeys WHERE user_id = ?", (user["id"],)).fetchone()[0]
    assert kept == counted.counter
    # an answer that carries a count the passkey has passed is refused
    later = payload(client, h, task, version=2, prev=digest_of(p), egress=["pypi.org"])
    r = sign(client, h, counted, task["id"], later, count=counted.counter)
    assert r.status_code == 401 and "counter" in r.json()["detail"], r.text


def test_a_grant_pins_the_task_s_goal(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    r = sign(client, h, device, task["id"], payload(client, h, task, ask="sha256:" + "0" * 64))
    assert r.status_code == 409 and "goal" in r.json()["detail"], r.text
    first = granted(client, h, device, task)
    assert grant_of(client, h, task["id"])["ask_current"] is True
    # the goal changed after it was signed: the grant still pins the goal it was signed for, and says so
    version = task_of(client, h, task["id"])["state_version"]
    assert client.put(f"{T}/{task['id']}", headers=h, json={"state_version": version,
                                                             "goal": "Make the file three lines long."}).status_code == 200
    changed = task_of(client, h, task["id"])
    assert changed["grant"]["ask"] == ask_digest(task) and changed["grant"]["ask_current"] is False
    # and the next version pins the goal as it is now
    r = sign(client, h, device, task["id"], payload(client, h, task, version=2, prev=digest_of(first)))
    assert r.status_code == 409, r.text
    assert sign(client, h, device, task["id"], payload(client, h, changed, version=2,
                                                       prev=digest_of(first))).status_code == 200


def test_a_grant_names_its_task_s_repository_with_the_task_s_own_branch(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    r = sign(client, h, device, task["id"], payload(client, h, task, repositories=[]))
    assert r.status_code == 422 and "demo" in r.json()["detail"], r.text
    r = sign(client, h, device, task["id"], payload(client, h, task, repositories=[
        {"repo": "demo", "push": ["main"], "merge": []}]))
    assert r.status_code == 422 and "pp/task-" + task["id"] in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"]) is None


def test_every_repository_a_grant_names_carries_the_task_s_own_branch(client, owner, ws, device):
    """For each repository a grant names, the branches it may push include its own pp/task-<id> always: every repository
    entry, not only the task's own. This test fails if the branch is required in the task's own repository alone."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    branch = "pp/task-" + task["id"]
    other = {"repo": "tools", "push": ["release"], "merge": []}
    r = sign(client, h, device, task["id"], payload(client, h, task, repositories=[own_repo(task), other]))
    assert r.status_code == 422 and "tools" in r.json()["detail"] and branch in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"]) is None
    p = granted(client, h, device, task, repositories=[own_repo(task), dict(other, push=sorted([branch, "release"]))])
    assert grant_of(client, h, task["id"])["repositories"] == p["repositories"]


def test_a_widening_s_other_repository_gains_the_task_s_own_branch_and_is_signed_with_it(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    branch = "pp/task-" + task["id"]
    mac = paired(client, h)
    r = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", {
        "client_id": "c2" * 16, "add": {"repositories": [{"repo": "tools", "push": ["release"]}]}})
    assert r.status_code == 200, r.text
    asked = the_ask(client, h)
    assert asked["scope"]["repositories"] == [own_repo(task), {"repo": "tools", "push": [branch, "release"],
                                                               "merge": []}]
    p = payload(client, h, task, version=2, prev=digest_of(v1), requester={"kind": "link", "id": mac.link_id},
                widening=asked["id"], **asked["scope"])
    assert sign(client, h, device, task["id"], p).status_code == 200
    assert grant_of(client, h, task["id"])["repositories"][1] == {"repo": "tools", "push": [branch, "release"],
                                                                  "merge": []}


def test_a_received_task_s_other_repository_gains_the_owner_s_task_s_branch(client, owner, ws, device):
    """A task received from a teammate pushes the owner's task's branch (tasks._branch_task): that is the branch every
    repository of its grant carries, never the received task's own id's."""
    h, user = owner
    own(user, ACC["A"])
    task_id = received(user, make_agent(client, h, ACC["A"]))
    mac = paired(client, h)
    r = mac.call("POST", f"/tasks/{task_id}/grant-widenings", {
        "client_id": "c3" * 16, "add": {"repositories": [{"repo": "tools", "push": ["release"]}]}})
    assert r.status_code == 200, r.text
    asked = the_ask(client, h)
    assert asked["scope"]["repositories"] == [
        {"repo": "demo", "push": ["pp/task-" + "9" * 32], "merge": []},
        {"repo": "tools", "push": ["pp/task-" + "9" * 32, "release"], "merge": []}]


@pytest.mark.parametrize("over, words", [
    ({"egress": ["pypi.org", "example.com"]}, "sorted"),
    ({"egress": ["Example.com"]}, "host"),
    ({"egress": ["pypi.org", "pypi.org"]}, "once"),
    ({"environments": [{"name": "production", "rung": "L1"}]}, "rung"),
    ({"rungs": {"merge": "L2"}}, "rungs"),
    ({"secrets": [{"name": "ghp_" + "a" * 36, "lease_seconds": 3600}]}, "secret"),
    ({"secrets": [{"name": "TOKEN", "lease_seconds": 10}]}, "lease"),
    ({"lease_end": 0}, "lease ends 60 seconds"),
    ({"lease_end": 4102444800}, "lease"),
    ({"requester": {"kind": "someone"}}, "requester is {kind: owner}"),
    ({"requester": {"kind": "someone", "id": "0" * 32}, "widening": "1" * 32}, "requester"),
    ({"widening": "not-an-id"}, "widening is the id"),
    ({"version": 2}, "prev is the digest"),
    ({"surplus": 1}, "exactly the fields"),
])
def test_a_grant_out_of_shape_is_refused_before_anything_is_kept(client, owner, ws, device, over, words):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    r = sign(client, h, device, task["id"], payload(client, h, task, **over))
    assert r.status_code == 422 and words in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"]) is None


def test_a_grant_missing_a_field_is_refused(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    for field in ("task", "version", "prev", "ask", "requester", "widening", "repositories", "environments", "secrets",
                  "egress", "lease_end", "rungs"):
        p = payload(client, h, task)
        del p[field]
        r = sign(client, h, device, task["id"], p)
        assert r.status_code == 422 and "missing " + field in r.json()["detail"], (field, r.text)
    assert grant_of(client, h, task["id"]) is None


def test_an_account_with_no_passkey_is_told_a_grant_takes_one(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    p = payload(client, h, task)
    r = sign(client, h, Authenticator(), task["id"], p)
    assert r.status_code == 412 and "passkey" in r.json()["detail"], r.text


def test_another_account_reads_signs_asks_and_refuses_nothing_of_a_task_not_its_own(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "c" * 32, "add": {"egress": ["pypi.org"]}}))
    asked = the_ask(client, h)
    other = signed_up(client, "other@example.com")
    assert client.get(f"{T}/{task['id']}/grant", headers=other).status_code == 404
    assert waiting(client, other) == []
    assert client.post(f"{GRANTS}/{asked['id']}/refuse", headers=other).status_code == 404
    theirs = paired(client, other, name="theirs")
    r = theirs.call("POST", f"/tasks/{task['id']}/grant-widenings", {"client_id": "d" * 32,
                                                                       "add": {"egress": ["example.com"]}})
    assert r.status_code == 404, r.text
    assert [w["id"] for w in waiting(client, h)] == [asked["id"]]


# ---- the widenings: asked, signed as asked, refused ----------------------------------------------------------------

def test_a_linked_session_asks_and_only_the_owner_s_passkey_applies_it(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    first = granted(client, h, device, task)
    mac = paired(client, h)
    body = {"client_id": "e" * 32, "add": {"environments": [{"name": "staging", "rung": "L2"}]}}
    r = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", body)
    assert r.status_code == 200, r.text
    made = r.json()["widening"]
    assert made["state"] == "waiting" and made["base_version"] == 1
    # asked again with the same client_id: the same ask, not a second one
    again = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", body)
    assert again.status_code == 200 and again.json()["widening"]["id"] == made["id"]
    asked = the_ask(client, h)
    assert asked["requester"] == {"kind": "link", "id": mac.link_id, "name": "mac"}
    assert asked["adds"]["environments"] == [{"name": "staging", "rung": "L2"}]
    # it waits: the grant is as the owner signed it
    assert grant_of(client, h, task["id"])["digest"] == digest_of(first)
    p = payload(client, h, task, version=2, prev=digest_of(first), requester={"kind": "link", "id": mac.link_id},
                widening=asked["id"], **asked["scope"])
    assert sign(client, h, device, task["id"], p).status_code == 200
    got = grant_of(client, h, task["id"])
    assert got["environments"] == [{"name": "staging", "rung": "L2"}] and got["requester"]["kind"] == "link"
    assert mac.call("POST", f"/tasks/{task['id']}/grant-widenings", body).json()["widening"]["state"] == "signed"


@pytest.mark.parametrize("field, value", [
    ("repositories", lambda t: [own_repo(t)]),
    ("environments", lambda t: []),
    ("secrets", lambda t: [{"name": "OTHER", "lease_seconds": 3600}]),
    ("egress", lambda t: ["example.com"]),
    ("lease_end", lambda t: None),
    ("rungs", lambda t: {"merge": "L3", "deploy": "L3"}),
])
def test_a_widening_is_signed_as_it_was_asked_for_and_as_nothing_else(client, owner, ws, device, field, value):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1 = granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "f" * 32, "add": {
        "repositories": [{"repo": "tools", "push": ["release"]}], "environments": [{"name": "staging", "rung": "L2"}],
        "egress": ["pypi.org"]}, "lease_end": int(time.time()) + 2 * WEEK}))
    asked = the_ask(client, h)
    scope = dict(asked["scope"])
    scope[field] = value(task) if field != "lease_end" else scope["lease_end"] + 60
    p = payload(client, h, task, version=2, prev=digest_of(v1), requester={"kind": "agent", "id": agent_id},
                widening=asked["id"], **scope)
    r = sign(client, h, device, task["id"], p)
    assert r.status_code == 409 and "asked" in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"])["version"] == 1 and waiting(client, h)[0]["state"] == "waiting"


def test_a_widening_is_signed_for_the_one_who_asked_for_it(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1 = granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "1" * 32, "add": {"egress": ["pypi.org"]}}))
    asked = the_ask(client, h)
    for requester in ({"kind": "owner"}, {"kind": "agent", "id": "0" * 12}, {"kind": "link", "id": "0" * 32}):
        p = payload(client, h, task, version=2, prev=digest_of(v1), requester=requester, widening=asked["id"],
                    **asked["scope"])
        r = sign(client, h, device, task["id"], p)
        assert r.status_code in (409, 422), (requester, r.text)
    # and a grant the owner composes names no widening and no one but the owner as its requester
    p = payload(client, h, task, version=2, prev=digest_of(v1), requester={"kind": "agent", "id": agent_id},
                egress=["pypi.org"])
    assert sign(client, h, device, task["id"], p).status_code == 422
    assert grant_of(client, h, task["id"])["version"] == 1


def test_a_widening_of_another_task_is_not_signed_on_this_one(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "2" * 32, "add": {"egress": ["pypi.org"]}}))
    asked = the_ask(client, h)
    other_agent = make_agent(client, h, ACC["A"])
    other = make_task(client, h, other_agent)
    o1 = granted(client, h, device, other)
    p = payload(client, h, other, version=2, prev=digest_of(o1), requester={"kind": "agent", "id": agent_id},
                widening=asked["id"], **dict(asked["scope"], repositories=[own_repo(other)]))
    r = sign(client, h, device, other["id"], p)
    assert r.status_code == 409 and "not this task's" in r.json()["detail"], r.text
    assert grant_of(client, h, other["id"])["version"] == 1 and waiting(client, h)[0]["state"] == "waiting"


def test_the_owner_refuses_a_widening_with_the_sign_in_alone(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1 = granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "3" * 32, "add": {"egress": ["pypi.org"]}}))
    asked = the_ask(client, h)
    r = client.post(f"{GRANTS}/{asked['id']}/refuse", headers=h)
    assert r.status_code == 200 and r.json()["widening"]["state"] == "refused", r.text
    assert waiting(client, h) == [] and grant_of(client, h, task["id"])["digest"] == digest_of(v1)
    assert client.post(f"{GRANTS}/{asked['id']}/refuse", headers=h).status_code == 409
    # a refused widening is not signed after it
    p = payload(client, h, task, version=2, prev=digest_of(v1), requester={"kind": "agent", "id": agent_id},
                widening=asked["id"], **asked["scope"])
    assert sign(client, h, device, task["id"], p).status_code == 409
    # and the record that carried it, copied again, asks nothing more
    seen(user, first, "working", grant_asks=asks({"client_id": "3" * 32, "add": {"egress": ["pypi.org"]}}))
    assert waiting(client, h) == []


def test_an_attempt_s_record_asks_once_and_a_late_record_asks_nothing(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    granted(client, h, device, task)
    ask = {"client_id": "4" * 32, "add": {"egress": ["pypi.org"]}}
    seen(user, first, "working", grant_asks=asks(ask))
    seen(user, first, "working", grant_asks=asks(ask))
    assert len(waiting(client, h)) == 1
    finish(user, first, 1, 1)
    # the first attempt's record, copied after its end and another attempt holding the lease: late, and asks nothing
    seen(user, first, "done", grant_asks=asks({"client_id": "5" * 32, "add": {"egress": ["example.com"]}}))
    assert [w["adds"]["egress"] for w in waiting(client, h)] == [["pypi.org"]]
    # the next attempt's record, naming a fence that is not the task's: late (tasks.take_record), and asks nothing
    second = row(task["id"])["lease_run"]
    seen(user, second, "working", fence=99, grant_asks=asks({"client_id": "6" * 32,
                                                                "add": {"egress": ["example.org"]}}))
    assert [w["adds"]["egress"] for w in waiting(client, h)] == [["pypi.org"]]


def test_an_ask_that_adds_nothing_or_is_out_of_shape_is_not_kept(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    granted(client, h, device, task, egress=["pypi.org"])
    record(user, first, "working", grant_asks=asks(
        {"client_id": "6" * 32, "add": {"egress": ["pypi.org"]}},                   # the grant gives it already
        {"client_id": "7" * 32, "add": {"egress": ["Not A Host"]}},
        {"client_id": "not-hex", "add": {"egress": ["example.com"]}},
        {"client_id": "8" * 32, "add": {"secrets": [{"name": "ghp_" + "b" * 36, "lease_seconds": 60}]}}))
    assert waiting(client, h) == []
    mac = paired(client, h)
    r = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", {"client_id": "9" * 32,
                                                                    "add": {"egress": ["pypi.org"]}})
    assert r.status_code == 422 and "nothing" in r.json()["detail"], r.text


def test_a_newer_grant_carries_a_waiting_widening_onto_itself(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1 = granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "a1" * 16, "add": {"egress": ["pypi.org"]}}))
    # the owner changes the grant meanwhile, by their own word
    v2 = granted(client, h, device, task, version=2, prev=digest_of(v1), secrets=[{"name": "TOKEN",
                                                                                   "lease_seconds": 600}])
    asked = the_ask(client, h)
    assert asked["base_version"] == 2
    assert asked["scope"]["secrets"] == [{"name": "TOKEN", "lease_seconds": 600}]
    assert asked["scope"]["egress"] == ["pypi.org"]
    p = payload(client, h, task, version=3, prev=digest_of(v2), requester={"kind": "agent", "id": agent_id},
                widening=asked["id"], **asked["scope"])
    assert sign(client, h, device, task["id"], p).status_code == 200
    assert grant_of(client, h, task["id"])["version"] == 3


def test_an_ended_task_s_grant_does_not_change_and_its_asks_end_with_it(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1 = granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "b1" * 16, "add": {"egress": ["pypi.org"]}}))
    record(user, first, "failed", reason="its own error")
    assert press(client, h, task["id"], "end", outcome="stopped").status_code == 200
    assert waiting(client, h) == []
    # nor is a linked session's ask of it kept
    mac = paired(client, h)
    r = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", {"client_id": "b2" * 16,
                                                                    "add": {"egress": ["example.com"]}})
    assert r.status_code == 412, r.text
    r = sign(client, h, device, task["id"], payload(client, h, task, version=2, prev=digest_of(v1),
                                                    egress=["pypi.org"]))
    assert r.status_code == 412, r.text


# ---- where the grant is shown: the task, the board, the owner's list and its count ---------------------------------

def test_the_task_shows_its_grant_and_what_a_grant_of_it_names(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    shown = task_of(client, h, task["id"])
    assert shown["grant"] is None and shown["grant_widenings"] == []
    assert shown["grant_for"] == {"repo": "demo", "branch": "pp/task-" + task["id"], "ask": ask_digest(task)}
    p = granted(client, h, device, task, environments=[{"name": "production", "rung": "L3"}],
                secrets=[{"name": "DEPLOY_KEY", "lease_seconds": 600}], egress=["pypi.org"])
    g = task_of(client, h, task["id"])["grant"]
    assert (g["version"], g["digest"], g["repositories"], g["environments"], g["secrets"], g["egress"], g["rungs"],
            g["lease_end"], g["requester"], g["widening"], g["ask"], g["ask_current"], g["lease_ended"]) == \
        (1, digest_of(p), [own_repo(task)], [{"name": "production", "rung": "L3"}],
         [{"name": "DEPLOY_KEY", "lease_seconds": 600}], ["pypi.org"], RUNGS, p["lease_end"],
         {"kind": "owner"}, None, ask_digest(task), True, False)
    assert g["passkey"] == "Ana's iPhone" and isinstance(g["signed_at"], int)
    listed = {t["id"]: t for t in client.get(T, headers=h).json()["tasks"]}
    assert listed[task["id"]]["grant"] == g


def test_each_version_is_kept_as_its_passkey_signed_it(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    v2 = granted(client, h, device, task, version=2, prev=digest_of(v1), egress=["pypi.org"])
    kept = versions_of(client, h, task["id"])
    assert [v["payload"].encode("utf-8") for v in kept] == [canonicalize(v1), canonicalize(v2)]
    assert [v["digest"] for v in kept] == [digest_of(v1), digest_of(v2)]
    for v in kept:
        credential = json.loads(v["signature"]) if isinstance(v["signature"], str) else v["signature"]
        client_data = json.loads(unb64u(credential["response"]["clientDataJSON"]))
        step = as_step(v["payload"].encode("utf-8"))["publicKey"]["challenge"]
        assert client_data["challenge"] == step


def test_the_board_says_the_grant_s_versions_its_asks_and_its_refusals(client, owner, ws, device, no_sessions):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1 = granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "c1" * 16, "add": {"egress": ["pypi.org"]}}))
    asked = the_ask(client, h)
    client.post(f"{GRANTS}/{asked['id']}/refuse", headers=h)
    rows = {r["id"]: r for r in client.get("/api/ade/board", headers=h).json()["rows"] if r["kind"] == "task"}
    words = [e["words"] for e in rows[task["id"]]["events"]]
    assert "Its grant signed with your passkey: version 1, " + digest_of(v1)[:12] in words
    assert "Its agent asked for more in its grant: it waits for your passkey" in words
    assert "An ask for more in its grant was refused" in words


def test_the_owner_s_count_of_what_waits_counts_a_widening(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    granted(client, h, device, task)
    before = client.get("/api/ade/summary", headers=h).json()
    seen(user, first, "working", grant_asks=asks({"client_id": "d1" * 16, "add": {"egress": ["pypi.org"]}}))
    after = client.get("/api/ade/summary", headers=h).json()
    assert after["approvals"] == before["approvals"] + 1
    assert after["stamps"]["approvals"] != before["stamps"]["approvals"]


# ---- what the cases above do not catch (each named in its docstring) -----------------------------------------------

def test_a_grant_is_signed_for_at_most_ten_minutes(client, owner, ws, device):
    """A grant waits for the server at most 600 seconds after it is made (link_commands.GRANT_LIFE_MAX); its own life is
    its lease_end. This test fails if the grant's own limit is taken out, a day allowed as for a command."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    n = nonce(client, h)
    r = sign(client, h, device, task["id"], payload(client, h, task, n=n, expires_at=n["server_time"] + 601))
    assert r.status_code == 422 and "60 to 600 seconds" in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"]) is None


def test_a_grant_whose_lease_has_ended_by_the_time_it_arrives_is_not_kept(client, owner, ws, device, monkeypatch):
    """A lease that ends before the server keeps its grant is refused, though its payload was in shape when it was made.
    This test fails if that check is taken out."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    n = nonce(client, h)
    p = payload(client, h, task, n=n, lease_end=n["server_time"] + 60)
    later = n["server_time"] + 120
    monkeypatch.setattr(time, "time", lambda: later)
    r = sign(client, h, device, task["id"], p)
    assert r.status_code == 422 and "lease has ended" in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"]) is None


def test_an_ask_of_a_grant_whose_lease_has_ended_names_a_new_lease_end(client, owner, ws, device, monkeypatch):
    """An ask that would leave its scope's lease ended is told to name a new one. Without that check, the scope's own
    bounds would refuse it in other words."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    n = nonce(client, h)
    granted(client, h, device, task, n=n, lease_end=n["server_time"] + 60)
    mac = paired(client, h)
    later = n["server_time"] + 120
    monkeypatch.setattr(time, "time", lambda: later)
    r = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", {"client_id": "e1" * 16,
                                                                    "add": {"egress": ["pypi.org"]}})
    assert r.status_code == 422 and "new lease_end" in r.json()["detail"], r.text
    r = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", {"client_id": "e2" * 16,
                                                                    "add": {"egress": ["pypi.org"]},
                                                                    "lease_end": later + WEEK})
    assert r.status_code == 200 and r.json()["widening"]["state"] == "waiting", r.text


def test_a_task_s_grant_has_at_most_five_asks_waiting(client, owner, ws, device):
    """This test fails if the task's limit is read as the account's."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    granted(client, h, device, task)
    mac = paired(client, h)
    for i in range(5):
        r = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", {"client_id": f"{i + 1:032x}",
                                                                        "add": {"egress": [f"h{i}.example.com"]}})
        assert r.status_code == 200, r.text
    r = mac.call("POST", f"/tasks/{task['id']}/grant-widenings", {"client_id": "f" * 32,
                                                                    "add": {"egress": ["h5.example.com"]}})
    assert r.status_code == 429 and "at most 5" in r.json()["detail"], r.text
    assert len(waiting(client, h)) == 5


def test_an_ask_that_widens_another_version_than_the_newest_is_not_signed(client, owner, ws, device):
    """Each version signed carries the asks that wait onto itself (task_grants._carry), so an ask that waits always
    widens the newest; one that does not (its row set back here, as a carry missed would leave it) is not signed.
    This test fails if that check is taken out."""
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1 = granted(client, h, device, task)
    seen(user, first, "working", grant_asks=asks({"client_id": "a3" * 16, "add": {"egress": ["pypi.org"]}}))
    v2 = granted(client, h, device, task, version=2, prev=digest_of(v1), secrets=[{"name": "TOKEN",
                                                                                   "lease_seconds": 600}])
    asked = the_ask(client, h)
    with store.connect() as db:
        db.execute("UPDATE ade_task_grant_widenings SET base_version = 1 WHERE id = ?", (asked["id"],))
    p = payload(client, h, task, version=3, prev=digest_of(v2), requester={"kind": "agent", "id": agent_id},
                widening=asked["id"], **asked["scope"])
    r = sign(client, h, device, task["id"], p)
    assert r.status_code == 409 and "another version" in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"])["version"] == 2


def test_an_agent_asks_only_in_its_own_task_s_grant(client, owner, ws, device):
    """No path today gives an ask another agent than the task's own (an attempt's record names its own run's agent);
    the check holds that should one be added. This test fails if that check is taken out."""
    from proofpane_open.ade import task_grants, tasks
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    other = make_agent(client, h, ACC["A"])
    with store.connect() as db:
        with pytest.raises(agents.AgentError) as e:
            task_grants.ask_in(db, user["id"], tasks.get(db, user["id"], task["id"]), {"kind": "agent", "id": other},
                               {"client_id": "a4" * 16, "add": {"egress": ["pypi.org"]}}, int(time.time()))
    assert e.value.status == 403 and "its own task" in str(e.value)
    assert waiting(client, h) == []


# ---- guards with no case above, and a carried ask's other repository ----------

def ask_of_link(s, task_id, client_id, **body):
    """A linked session's ask for more in a task's grant: {widening}, or the refusal."""
    return s.call("POST", f"/tasks/{task_id}/grant-widenings", dict({"client_id": client_id}, **body))


def test_a_received_task_s_grant_names_the_owner_s_task_s_branch_and_never_its_own_id(client, owner, ws, device):
    """A task received from a teammate pushes the owner's task's branch (tasks._branch_task), so its grant names that
    one. This test fails if the own branch is made from the received task's own id."""
    h, user = owner
    own(user, ACC["A"])
    task_id = received(user, make_agent(client, h, ACC["A"]))
    t = task_of(client, h, task_id)
    theirs = "pp/task-" + "9" * 32
    assert t["grant_for"]["branch"] == theirs
    r = sign(client, h, device, task_id, payload(client, h, t, repositories=[
        {"repo": "demo", "push": ["pp/task-" + task_id], "merge": []}]))
    assert r.status_code == 422 and theirs in r.json()["detail"], r.text
    p = granted(client, h, device, t, repositories=[{"repo": "demo", "push": [theirs], "merge": []}])
    assert grant_of(client, h, task_id)["repositories"] == p["repositories"]


def test_an_ask_past_its_day_waits_no_more_and_is_neither_signed_nor_refused(client, owner, ws, device):
    """This test fails if an ask's day is not read where its state is, nor where the owner's list and the task's view
    read the asks that wait."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    mac = paired(client, h)
    body = {"add": {"egress": ["pypi.org"]}}
    assert ask_of_link(mac, task["id"], "e3" * 16, **body).status_code == 200
    asked = the_ask(client, h)
    with store.connect() as db:
        db.execute("UPDATE ade_task_grant_widenings SET expires_at = ? WHERE id = ?", (int(time.time()) - 1, asked["id"]))
    assert waiting(client, h) == [] and task_of(client, h, task["id"])["grant_widenings"] == []
    p = payload(client, h, task, version=2, prev=digest_of(v1), requester={"kind": "link", "id": mac.link_id},
                widening=asked["id"], **asked["scope"])
    r = sign(client, h, device, task["id"], p)
    assert r.status_code == 409 and r.json().get("state") == "expired", r.text
    r = client.post(f"{GRANTS}/{asked['id']}/refuse", headers=h)
    assert r.status_code == 409 and r.json().get("state") == "expired", r.text
    assert ask_of_link(mac, task["id"], "e3" * 16, **body).json()["widening"]["state"] == "expired"
    assert grant_of(client, h, task["id"])["version"] == 1


def test_an_ended_task_s_ask_is_read_as_void_wherever_it_is_read(client, owner, ws, device):
    """This test fails if an ask of a task that ended is read as waiting."""
    h, user = owner
    task, agent_id, first = started(client, h, user)
    granted(client, h, device, task)
    mac = paired(client, h)
    body = {"add": {"egress": ["pypi.org"]}}
    assert ask_of_link(mac, task["id"], "e4" * 16, **body).status_code == 200
    asked = the_ask(client, h)
    record(user, first, "failed", reason="its own error")
    assert press(client, h, task["id"], "end", outcome="stopped").status_code == 200
    r = client.post(f"{GRANTS}/{asked['id']}/refuse", headers=h)
    assert r.status_code == 409 and r.json().get("state") == "void", r.text
    assert ask_of_link(mac, task["id"], "e4" * 16, **body).json()["widening"]["state"] == "void"


def test_an_account_has_at_most_fifty_asks_waiting_across_its_tasks(client, owner, ws, device):
    """Ten tasks of five asks each: the account's fifty-first ask, on an eleventh task, is refused. This test fails with
    no limit for the account."""
    h, user = owner
    own(user, ACC["A"])
    tasks_made = [make_task(client, h, make_agent(client, h, ACC["A"])) for _ in range(11)]
    mac = paired(client, h)
    for n, t in enumerate(tasks_made[:10]):
        for i in range(5):
            r = ask_of_link(mac, t["id"], f"{n * 5 + i + 1:032x}", add={"egress": [f"h{i}.example.com"]})
            assert r.status_code == 200, (n, i, r.text)
    r = ask_of_link(mac, tasks_made[10]["id"], "f" * 32, add={"egress": ["h0.example.com"]})
    assert r.status_code == 429 and "at most 50" in r.json()["detail"], r.text
    assert len(waiting(client, h)) == 50


def test_an_ask_asked_again_is_found_by_its_task_and_asker_with_its_client_id(client, owner, ws, device):
    """A client_id names an ask of one asker on one task: another linked session, or the same one on another task,
    asking with it makes an ask of its own. This test fails if the ask is found by the account and its client_id alone,
    which would give one session another's ask back, on any task."""
    h, user = owner
    own(user, ACC["A"])
    one = make_task(client, h, make_agent(client, h, ACC["A"]))
    two = make_task(client, h, make_agent(client, h, ACC["A"]))
    mac, pc = paired(client, h), paired(client, h, name="pc")
    first = ask_of_link(mac, one["id"], "e5" * 16, add={"egress": ["pypi.org"]}).json()["widening"]
    by_pc = ask_of_link(pc, one["id"], "e5" * 16, add={"egress": ["example.com"]}).json()["widening"]
    on_two = ask_of_link(mac, two["id"], "e5" * 16, add={"egress": ["example.org"]}).json()["widening"]
    assert len({first["id"], by_pc["id"], on_two["id"]}) == 3
    listed = {w["id"]: w for w in waiting(client, h)}
    assert (listed[by_pc["id"]]["requester"]["name"], listed[on_two["id"]]["task_id"]) == ("pc", two["id"])


def test_a_carried_ask_with_nothing_left_to_add_is_void(client, owner, ws, device):
    """The owner's own version gives what an ask asked for: the ask, carried onto it, has nothing left, and waits no
    more. This test fails if the carry keeps it."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    mac = paired(client, h)
    body = {"add": {"egress": ["pypi.org"]}}
    assert ask_of_link(mac, task["id"], "e6" * 16, **body).status_code == 200
    granted(client, h, device, task, version=2, prev=digest_of(v1), egress=["pypi.org"])
    assert waiting(client, h) == []
    assert ask_of_link(mac, task["id"], "e6" * 16, **body).json()["widening"]["state"] == "void"


def test_at_most_five_asks_are_taken_from_one_record(client, owner, ws, device):
    """The app's own bound, whatever a workspace sends (the record copied here as the app's poll takes it, past the
    workspace's view): the first five items of a record's asks are read, one out of shape among them, so four are kept
    and the sixth is not read. This test fails if every item is read."""
    h, user = owner
    task, agent_id, first = started(client, h, user)
    granted(client, h, device, task)
    items = [{"client_id": "not-hex", "add": {"egress": ["bad.example.com"]}}] + [
        {"client_id": f"{i + 1:032x}", "add": {"egress": [f"h{i}.example.com"]}} for i in range(5)]
    record(user, first, "working", grant_asks=items)
    assert sorted(w["adds"]["egress"][0] for w in waiting(client, h)) == [f"h{i}.example.com" for i in range(4)]


def test_a_carried_ask_s_other_repository_keeps_the_task_s_own_branch_and_is_signed_as_carried(client, owner, ws,
                                                                                                device):
    """An ask for another repository, carried onto a newer version: its scope still gives that repository the task's own
    branch, so it is signed as carried. This test fails if the carry makes the scope without the own branch, which the
    server would then refuse to sign."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    mac = paired(client, h)
    assert ask_of_link(mac, task["id"], "e7" * 16,
                       add={"repositories": [{"repo": "tools", "push": ["release"]}]}).status_code == 200
    v2 = granted(client, h, device, task, version=2, prev=digest_of(v1), egress=["pypi.org"])
    asked = the_ask(client, h)
    assert asked["base_version"] == 2 and asked["scope"]["repositories"][1] == {
        "repo": "tools", "push": ["pp/task-" + task["id"], "release"], "merge": []}
    p = payload(client, h, task, version=3, prev=digest_of(v2), requester={"kind": "link", "id": mac.link_id},
                widening=asked["id"], **asked["scope"])
    assert sign(client, h, device, task["id"], p).status_code == 200


# ---- a version whose passkey went ------------------------------------------------------

def passkey_named(user, name):
    with store.connect() as db:
        return db.execute("SELECT id FROM passkeys WHERE user_id = ? AND name = ?", (user["id"], name)).fetchone()["id"]


def test_a_version_a_removed_passkey_signed_is_withdrawn_and_the_grant_stands_at_the_one_before_it(
        client, owner, ws, device, no_sessions):
    """passkeys.remove's contract: a removed passkey leaves nothing it signed to take effect. The versions it signed are
    withdrawn in the removal's own transaction; the grant stands at the newest version a passkey still held signed, an
    attempt's message names that one, the task's view and its versions say which, and the next version follows the
    withdrawn one in the chain."""
    h, user = owner
    task, agent_id, first = started(client, h, user)
    laptop = Authenticator(-8)
    assert add(client, h, laptop, name="Laptop").status_code == 200
    v1 = granted(client, h, device, task)
    p2 = payload(client, h, task, version=2, prev=digest_of(v1), egress=["pypi.org"])
    assert sign(client, h, laptop, task["id"], p2).status_code == 200
    assert grant_of(client, h, task["id"])["version"] == 2
    assert remove(client, h, passkey_named(user, "Laptop")).status_code == 200
    g = grant_of(client, h, task["id"])
    assert (g["version"], g["digest"], g["egress"]) == (1, digest_of(v1), []), g
    assert [(w["version"], w["digest"], w["why"]) for w in g.get("withdrawn") or []] == [(2, digest_of(p2), "removed")]
    assert g.get("next") == {"version": 3, "prev": digest_of(p2)}
    assert [(v["version"], (v.get("withdrawn") or {}).get("why")) for v in versions_of(client, h, task["id"])] == \
        [(1, None), (2, "removed")]
    finish(user, first, 1, 1)
    message = send(user, ws, row(task["id"])["lease_run"])
    assert (message["grant_digest"], message["grant_version"]) == (digest_of(v1), 1)
    rows = {r["id"]: r for r in client.get("/api/ade/board", headers=h).json()["rows"] if r["kind"] == "task"}
    assert "Its grant's version 2 was withdrawn: the passkey that signed it was removed" in \
        [e["words"] for e in rows[task["id"]]["events"]]
    # the next version follows the withdrawn one, signed with a passkey the account still holds
    p3 = payload(client, h, task, version=3, prev=digest_of(p2), egress=["example.com"])
    assert sign(client, h, device, task["id"], p3).status_code == 200
    g = grant_of(client, h, task["id"])
    assert (g["version"], g["egress"], g["withdrawn"]) == (3, ["example.com"], [])


def test_removing_the_passkey_that_signed_every_version_leaves_no_grant_standing(client, owner, ws, device):
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1 = granted(client, h, device, task)
    assert remove(client, h, passkey_named(user, "Ana's iPhone")).status_code == 200
    g = grant_of(client, h, task["id"])
    assert g is not None and (g["version"], g["digest"]) == (None, None), g
    assert [(w["version"], w["why"]) for w in g["withdrawn"]] == [(1, "removed")]
    assert g["next"] == {"version": 2, "prev": digest_of(v1)}
    finish(user, first, 1, 1)
    message = send(user, ws, row(task["id"])["lease_run"])
    assert (message["grant_digest"], message["grant_version"]) == (None, None)


def test_high_security_turned_on_withdraws_what_a_passkey_that_is_not_a_hardware_key_signed(client, owner, ws, device,
                                                                                          no_sessions):
    """passkeys.high_security_in withdraws what the account's other passkeys signed that could still take effect: a
    version of a task's grant is such a thing."""
    h, user = owner
    task, agent_id, first = started(client, h, user)
    key = Authenticator(-8)
    assert add(client, h, key, name="Key").status_code == 200
    v1 = granted(client, h, device, task)                      # the device passkey: not a hardware key
    with store.connect() as db:
        db.execute("UPDATE passkeys SET tier = ? WHERE user_id = ? AND name = ?", (passkeys.HARDWARE, user["id"], "Key"))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        passkeys.high_security_in(db, user["id"], int(time.time()))
    g = grant_of(client, h, task["id"])
    assert g is not None and g["version"] is None, g
    assert [(w["version"], w["why"]) for w in g["withdrawn"]] == [(1, "high_security")]
    assert [(v["version"], (v.get("withdrawn") or {}).get("why")) for v in versions_of(client, h, task["id"])] == \
        [(1, "high_security")]
    rows = {r["id"]: r for r in client.get("/api/ade/board", headers=h).json()["rows"] if r["kind"] == "task"}
    assert "Its grant's version 1 was withdrawn: high security was turned on, and no hardware key signed it" in \
        [e["words"] for e in rows[task["id"]]["events"]]
    finish(user, first, 1, 1)
    assert send(user, ws, row(task["id"])["lease_run"])["grant_digest"] is None
    # the hardware key signs the next one
    assert sign(client, h, key, task["id"], payload(client, h, task, version=2, prev=digest_of(v1))).status_code == 200
    assert grant_of(client, h, task["id"])["version"] == 2


def test_an_ask_waiting_on_a_withdrawn_version_is_carried_onto_the_grant_that_stands(client, owner, ws, device):
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    laptop = Authenticator(-8)
    assert add(client, h, laptop, name="Laptop").status_code == 200
    v1 = granted(client, h, device, task)
    p2 = payload(client, h, task, version=2, prev=digest_of(v1), egress=["pypi.org"])
    assert sign(client, h, laptop, task["id"], p2).status_code == 200
    mac = paired(client, h)
    token = [{"name": "TOKEN", "lease_seconds": 600}]
    assert ask_of_link(mac, task["id"], "e8" * 16, add={"secrets": token}).status_code == 200
    assert the_ask(client, h)["scope"]["egress"] == ["pypi.org"]
    assert remove(client, h, passkey_named(user, "Laptop")).status_code == 200
    asked = the_ask(client, h)
    assert (asked["base_version"], asked["scope"]["egress"], asked["scope"]["secrets"]) == (2, [], token), asked
    p = payload(client, h, task, version=3, prev=digest_of(p2), requester={"kind": "link", "id": mac.link_id},
                widening=asked["id"], **asked["scope"])
    assert sign(client, h, device, task["id"], p).status_code == 200
    g = grant_of(client, h, task["id"])
    assert (g["version"], g["egress"], g["secrets"]) == (3, [], token)


def test_a_store_from_before_withdrawals_gains_their_columns_and_keeps_its_versions(tmp_path):
    """The two columns a withdrawal writes (withdrawn_at, withdrawn_why) are added (store.ADDED_COLUMNS), so that a
    store from before withdrawals gains them and keeps its versions as they were."""
    path = str(tmp_path / "before.db")
    with store.connect(path) as db:
        db.execute("INSERT INTO ade_task_grants (task_id, version, user_id, payload, signature, passkey_id, digest, "
                   "requester_kind, created_at) VALUES ('t1', 1, 'u1', '{}', '{}', 'k1', 'd1', 'owner', 5)")
        have = [r[1] for r in db.execute("PRAGMA table_info(ade_task_grants)")]
    assert have[-2:] == ["withdrawn_at", "withdrawn_why"], have
    old = sqlite3.connect(path)
    for column in ("withdrawn_at", "withdrawn_why"):
        old.execute(f"ALTER TABLE ade_task_grants DROP COLUMN {column}")
    old.commit()
    old.close()
    with store.connect(path) as db:
        have = [r[1] for r in db.execute("PRAGMA table_info(ade_task_grants)")]
        kept = db.execute("SELECT * FROM ade_task_grants WHERE task_id = 't1'").fetchone()
    assert have[-2:] == ["withdrawn_at", "withdrawn_why"], have
    assert (kept["digest"], kept["withdrawn_at"], kept["withdrawn_why"]) == ("d1", None, None)


def laptop_signed_v2(client, h, device, task):
    """Version 1 signed with the device passkey, version 2 with a second passkey, "Laptop", which then goes: (v1's
    payload, v2's payload)."""
    laptop = Authenticator(-8)
    assert add(client, h, laptop, name="Laptop").status_code == 200
    v1 = granted(client, h, device, task)
    p2 = payload(client, h, task, version=2, prev=digest_of(v1), egress=["pypi.org"])
    assert sign(client, h, laptop, task["id"], p2).status_code == 200
    return v1, p2


def test_an_ask_made_after_a_withdrawal_widens_the_grant_that_stands_and_follows_the_newest_kept(client, owner, ws,
                                                                                                 device):
    """ask_in after a withdrawal: the scope it widens is the grant that stands (version 1, not the withdrawn version 2),
    and the version that signs it follows the newest kept (version 3, naming version 2's digest)."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1, p2 = laptop_signed_v2(client, h, device, task)
    assert remove(client, h, passkey_named(user, "Laptop")).status_code == 200
    mac = paired(client, h)
    token = [{"name": "TOKEN", "lease_seconds": 600}]
    assert ask_of_link(mac, task["id"], "a3" * 16, add={"secrets": token}).status_code == 200
    asked = the_ask(client, h)
    assert (asked["base_version"], asked["base_digest"], asked["scope"]["egress"], asked["scope"]["secrets"]) == \
        (2, digest_of(p2), [], token), asked
    p = payload(client, h, task, version=3, prev=digest_of(p2), requester={"kind": "link", "id": mac.link_id},
                widening=asked["id"], **asked["scope"])
    assert sign(client, h, device, task["id"], p).status_code == 200
    assert (grant_of(client, h, task["id"])["version"], grant_of(client, h, task["id"])["egress"]) == (3, [])


def test_a_version_withdrawn_stays_withdrawn_for_its_first_reason(client, owner, ws, device, no_sessions):
    """A version withdrawn as its passkey was removed keeps that reason when high security is turned on after, and the
    second withdrawal journals only what it withdrew."""
    h, user = owner
    task, agent_id, first = started(client, h, user)
    v1, p2 = laptop_signed_v2(client, h, device, task)
    assert remove(client, h, passkey_named(user, "Laptop")).status_code == 200
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        passkeys.high_security_in(db, user["id"], int(time.time()) + 5)
    assert [(v["version"], (v.get("withdrawn") or {}).get("why")) for v in versions_of(client, h, task["id"])] == \
        [(1, "high_security"), (2, "removed")]
    rows = {r["id"]: r for r in client.get("/api/ade/board", headers=h).json()["rows"] if r["kind"] == "task"}
    words = [e["words"] for e in rows[task["id"]]["events"]]
    assert "Its grant's version 2 was withdrawn: the passkey that signed it was removed" in words
    assert "Its grant's version 1 was withdrawn: high security was turned on, and no hardware key signed it" in words
    assert not [w for w in words if w.startswith("Its grant's versions")], words


def test_removing_a_passkey_that_signed_a_deleted_task_s_grant_withdraws_the_rest(client, owner, ws, device):
    """A deleted task's versions go with the passkey too, and are read nowhere: the removal is not refused for them,
    and an open task's version it signed is withdrawn."""
    h, user = owner
    own(user, ACC["A"])
    laptop = Authenticator(-8)
    assert add(client, h, laptop, name="Laptop").status_code == 200
    agent_id = make_agent(client, h, ACC["A"])
    gone = make_task(client, h, agent_id)
    assert sign(client, h, laptop, gone["id"], payload(client, h, gone)).status_code == 200
    assert press(client, h, gone["id"], "end", outcome="stopped").status_code == 200
    r = client.request("DELETE", f"{T}/{gone['id']}", headers=h,
                       json={"state_version": task_of(client, h, gone["id"])["state_version"]})
    assert r.status_code == 200, r.text
    task = make_task(client, h, agent_id)
    assert sign(client, h, laptop, task["id"], payload(client, h, task)).status_code == 200
    assert remove(client, h, passkey_named(user, "Laptop")).status_code == 200
    assert grant_of(client, h, task["id"])["version"] is None


def test_six_hundred_versions_withdrawn_at_once_journal_the_newest_and_how_many(client, owner, ws, device,
                                                                                 no_sessions):
    """A journal event's payload has a bound (tasks.JOURNAL_PAYLOAD_MAX): a passkey that signed more versions than an
    event could name goes all the same, the event naming the newest of them and how many went."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    laptop = Authenticator(-8)
    assert add(client, h, laptop, name="Laptop").status_code == 200
    key = passkey_named(user, "Laptop")
    with store.connect() as db:
        for v in range(1, 601):
            db.execute("INSERT INTO ade_task_grants (task_id, version, user_id, payload, signature, passkey_id, "
                       "digest, requester_kind, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (task["id"], v, user["id"], "{}", "{}", key, f"{v:064x}", "owner", int(time.time())))
    assert remove(client, h, passkey_named(user, "Laptop")).status_code == 200
    g = grant_of(client, h, task["id"])
    assert (g["version"], len(g["withdrawn"]), g["next"]) == (None, 600, {"version": 601, "prev": f"{600:064x}"})
    rows = {r["id"]: r for r in client.get("/api/ade/board", headers=h).json()["rows"] if r["kind"] == "task"}
    assert "600 versions of its grant were withdrawn: the passkey that signed them was removed" in \
        [e["words"] for e in rows[task["id"]]["events"]]


# ---- a revoked linked session's asks --------------------------------------------------

def test_a_revoked_linked_session_s_asks_end_with_it_and_are_signed_no_more(client, owner, ws, device, no_sessions):
    """end_link: a link revoked takes its asks that wait with it, so the owner's passkey cannot apply what a session
    revoked for misbehaving, or for a lost machine, asked for; the board says the ask ended, and why."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    mac = paired(client, h)
    assert ask_of_link(mac, task["id"], "f4" * 16, add={"egress": ["pypi.org"]}).status_code == 200
    asked = the_ask(client, h)
    # a live session is named as an asker is, with nothing said of a revocation
    assert asked["requester"] == {"kind": "link", "id": mac.link_id, "name": "mac"}
    assert revoke_link(client, h, mac.link_id).status_code == 200
    assert waiting(client, h) == []
    with store.connect() as db:
        state = db.execute("SELECT state FROM ade_task_grant_widenings WHERE id = ?", (asked["id"],)).fetchone()[0]
    assert state == "void"
    p = payload(client, h, task, version=2, prev=digest_of(v1), requester={"kind": "link", "id": mac.link_id},
                widening=asked["id"], **asked["scope"])
    r = sign(client, h, device, task["id"], p)
    assert r.status_code == 409, r.text
    assert grant_of(client, h, task["id"])["version"] == 1
    rows = {r["id"]: r for r in client.get("/api/ade/board", headers=h).json()["rows"] if r["kind"] == "task"}
    assert "An ask for more in its grant ended: the linked session that asked was revoked" in \
        [e["words"] for e in rows[task["id"]]["events"]]


def test_an_ask_left_waiting_by_a_revoked_linked_session_is_said_revoked_and_not_signed(client, owner, ws, device):
    """A link revoked while its ask still waits (as a store from before end_link ended them keeps it): the owner's list
    says the session was revoked, the owner's passkey does not sign the ask, and the sign-in alone refuses it still."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    mac = paired(client, h)
    assert ask_of_link(mac, task["id"], "f5" * 16, add={"egress": ["pypi.org"]}).status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_links SET revoked_at = ? WHERE id = ?", (int(time.time()), mac.link_id))
    asked = the_ask(client, h)
    assert (asked["requester"]["name"], asked["requester"].get("revoked")) == ("mac", True), asked["requester"]
    p = payload(client, h, task, version=2, prev=digest_of(v1), requester={"kind": "link", "id": mac.link_id},
                widening=asked["id"], **asked["scope"])
    r = sign(client, h, device, task["id"], p)
    assert r.status_code == 409 and "revoked" in r.json()["detail"], r.text
    assert grant_of(client, h, task["id"])["version"] == 1
    assert client.post(f"{GRANTS}/{asked['id']}/refuse", headers=h).status_code == 200
    assert waiting(client, h) == []


def journal_kinds(client, h, task_id):
    r = client.get(f"{T}/{task_id}/journal", headers=h)
    assert r.status_code == 200, r.text
    return [e["kind"] for e in r.json()["events"]]


def test_a_revoked_session_ends_only_its_own_asks_and_journals_only_those_that_still_waited(client, owner, ws, device):
    """end_link ends the revoked session's asks alone: another session's ask waits on. It journals an ask that still
    waited, of a task still open; one past its day, or of a task that has ended, says nothing more."""
    h, user = owner
    own(user, ACC["A"])
    live, late, ended = (make_task(client, h, make_agent(client, h, ACC["A"])) for _ in range(3))
    mac, pc = paired(client, h), paired(client, h, name="pc")
    assert ask_of_link(mac, live["id"], "d1" * 16, add={"egress": ["pypi.org"]}).status_code == 200
    assert ask_of_link(pc, live["id"], "d2" * 16, add={"egress": ["example.com"]}).status_code == 200
    assert ask_of_link(mac, late["id"], "d3" * 16, add={"egress": ["pypi.org"]}).status_code == 200
    assert ask_of_link(mac, ended["id"], "d4" * 16, add={"egress": ["pypi.org"]}).status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_task_grant_widenings SET expires_at = ? WHERE task_id = ?",
                   (int(time.time()) - 1, late["id"]))
    assert press(client, h, ended["id"], "end", outcome="stopped").status_code == 200
    assert revoke_link(client, h, mac.link_id).status_code == 200
    assert [(w["task_id"], w["requester"]["name"]) for w in waiting(client, h)] == [(live["id"], "pc")]
    assert journal_kinds(client, h, live["id"]).count("task.grant_ask_ended") == 1
    for t in (late, ended):
        assert "task.grant_ask_ended" not in journal_kinds(client, h, t["id"]), t["id"]


# ---- asks the owner could not usefully sign -------------------------------------------

def made_with(task, v1, repos):
    """The scope an ask adding `repos` (each {repo, push, merge: []}) makes with version 1: every repository it names
    pushes the task's own branch."""
    branch = "pp/task-" + task["id"]
    named = [own_repo(task)] + [{"repo": r["repo"], "push": sorted(set(r["push"]) | {branch}), "merge": []}
                                for r in repos]
    return {"repositories": sorted(named, key=lambda r: r["repo"]), "environments": [], "secrets": [], "egress": [],
            "lease_end": v1["lease_end"], "rungs": v1["rungs"]}


def repos_of(task, v1, at_least):
    """Repositories for an ask whose scope with version 1 is at least `at_least` canonical bytes, by at most one branch
    more: branches of 100 characters, 19 to a repository (the 20th each is the task's own)."""
    repos = []
    while len(canonicalize(made_with(task, v1, repos))) < at_least:
        if not repos or len(repos[-1]["push"]) == 19:
            repos.append({"repo": f"r{len(repos):02d}", "push": [], "merge": []})
        repos[-1]["push"].append((f"b{len(repos[-1]['push']):02d}-" + "work-" * 20)[:100])
    return repos


def test_an_ask_whose_grant_would_pass_the_bytes_a_signature_carries_is_not_kept(client, owner, ws, device):
    """link_commands.PAYLOAD_MAX bounds the grant that signs an ask, all of it: an ask whose version could not be
    carried is refused as it is asked, not kept for the owner to meet the refusal after the passkey's prompt. The
    bound is the version's, not its scope's: a scope under it whose version is over it is refused."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    mac = paired(client, h)
    asker = {"kind": "link", "id": mac.link_id}
    repos = repos_of(task, v1, 19700)
    scope = made_with(task, v1, repos)
    over = payload(client, h, task, version=2, prev=digest_of(v1), requester=asker, widening="0" * 32, **scope)
    assert len(canonicalize(scope)) < 20000 < len(canonicalize(over))
    r = ask_of_link(mac, task["id"], "e1" * 16, add={"repositories": repos})
    assert r.status_code == 422 and "bytes" in r.json()["detail"], r.text
    assert waiting(client, h) == []
    # one well within them is kept, and the version that signs it is signed
    repos = repos_of(task, v1, 18000)
    assert ask_of_link(mac, task["id"], "e2" * 16, add={"repositories": repos}).status_code == 200
    asked = the_ask(client, h)
    assert asked["scope"] == made_with(task, v1, repos)
    p = payload(client, h, task, version=2, prev=digest_of(v1), requester=asker, widening=asked["id"],
                **asked["scope"])
    assert len(canonicalize(p)) <= 20000
    assert sign(client, h, device, task["id"], p).status_code == 200


def test_an_ask_that_adds_nothing_is_refused_whether_or_not_its_task_has_a_grant(client, owner, ws, device):
    """An empty ask, or one naming only what a first grant gives (the task's own repository and branch), is refused for
    a task with no grant as for one with a grant, so that none fills the task's asks with nothing."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    mac = paired(client, h)
    for i, add in enumerate(({}, {"repositories": [{"repo": "demo", "push": ["pp/task-" + task["id"]]}]})):
        r = ask_of_link(mac, task["id"], f"{i + 10}" * 16, add=add)
        assert r.status_code == 422 and "nothing" in r.json()["detail"], (add, r.text)
    assert waiting(client, h) == []
    # one that does add something to a first grant is kept
    assert ask_of_link(mac, task["id"], "e3" * 16, add={"egress": ["pypi.org"]}).status_code == 200
    assert len(waiting(client, h)) == 1


def test_the_same_ask_waiting_from_one_asker_is_not_kept_twice(client, owner, ws, device):
    """Identical asks from one asker are not each kept: the second is refused, naming the one that waits; another
    asker's same ask, and the same asker's other ask, are kept; the first asked again under its own client_id still
    says where it stands."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    granted(client, h, device, task)
    mac = paired(client, h)
    add = {"egress": ["pypi.org"]}
    first = ask_of_link(mac, task["id"], "c1" * 16, add=add)
    assert first.status_code == 200, first.text
    r = ask_of_link(mac, task["id"], "c2" * 16, add=add)
    assert r.status_code == 409 and first.json()["widening"]["id"] in r.json()["detail"], r.text
    assert len(waiting(client, h)) == 1
    other = paired(client, h, name="cloud")
    assert ask_of_link(other, task["id"], "c3" * 16, add=add).status_code == 200
    assert ask_of_link(mac, task["id"], "c4" * 16, add={"egress": ["example.com"]}).status_code == 200
    assert len(waiting(client, h)) == 3
    again = ask_of_link(mac, task["id"], "c1" * 16, add=add)
    assert again.status_code == 200 and again.json()["widening"]["id"] == first.json()["widening"]["id"], again.text



def test_an_ask_is_refused_where_the_longest_nonce_a_payload_may_carry_would_take_its_version_past_the_bytes(
        client, owner, ws, device):
    """The bound is held for the largest version that could sign the ask: its nonce as long as a payload's may be
    (passkeys.MAX_CHALLENGE_ID), so that no ask is kept that a longer nonce would leave unsignable. An ask whose version
    fits with this server's own nonce, and would not with one that long, is refused."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    mac = paired(client, h)
    n = nonce(client, h)
    slack = (passkeys.MAX_CHALLENGE_ID - len(n["nonce"])) // 2
    assert slack > 110, len(n["nonce"])                 # room for a branch of 100 characters, and more

    def version_of(scope):
        return payload(client, h, task, version=2, prev=digest_of(v1), n=n, requester={"kind": "link",
                       "id": mac.link_id}, widening="0" * 32, **scope)
    empty = made_with(task, v1, [])
    rest = len(canonicalize(version_of(empty))) - len(canonicalize(empty))     # the version's fields beside its scope
    repos = repos_of(task, v1, 20000 - slack - rest)
    here = version_of(made_with(task, v1, repos))
    longest = dict(here, nonce="n" * passkeys.MAX_CHALLENGE_ID)
    assert len(canonicalize(here)) <= 20000 < len(canonicalize(longest))
    r = ask_of_link(mac, task["id"], "e4" * 16, add={"repositories": repos})
    assert r.status_code == 422 and "bytes" in r.json()["detail"], r.text


def test_an_ask_carried_past_the_bytes_a_signature_carries_is_void(client, owner, ws, device):
    """An ask kept within the bound, carried onto a newer version that leaves too little room for what it adds, is
    void (check_signable as it is carried), never left waiting for a refusal after the passkey's prompt."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1 = granted(client, h, device, task)
    mac = paired(client, h)
    assert ask_of_link(mac, task["id"], "e6" * 16, add={"repositories": repos_of(task, v1, 17000)}).status_code == 200
    asked = the_ask(client, h)
    hosts = sorted(f"h{i:02d}." + "a" * 50 + ".example.com" for i in range(50))
    granted(client, h, device, task, version=2, prev=digest_of(v1), egress=hosts)
    assert waiting(client, h) == []
    with store.connect() as db:
        state = db.execute("SELECT state FROM ade_task_grant_widenings WHERE id = ?", (asked["id"],)).fetchone()[0]
    assert state == "void"


def test_the_same_ask_is_kept_again_once_the_first_no_longer_waits_and_on_another_task(client, owner, ws, device):
    """What makes two asks the same: one asker, one task, both waiting. The same asker's ask on another task is that
    task's own; once the first is refused, or past its day, the same ask is kept again."""
    h, user = owner
    own(user, ACC["A"])
    one, two = (make_task(client, h, make_agent(client, h, ACC["A"])) for _ in range(2))
    granted(client, h, device, one)
    mac = paired(client, h)
    add = {"egress": ["pypi.org"]}
    first = ask_of_link(mac, one["id"], "c5" * 16, add=add).json()["widening"]
    assert ask_of_link(mac, two["id"], "c6" * 16, add=add).status_code == 200
    assert client.post(f"{GRANTS}/{first['id']}/refuse", headers=h).status_code == 200
    second = ask_of_link(mac, one["id"], "c7" * 16, add=add)
    assert second.status_code == 200, second.text
    with store.connect() as db:
        db.execute("UPDATE ade_task_grant_widenings SET expires_at = ? WHERE id = ?",
                   (int(time.time()) - 1, second.json()["widening"]["id"]))
    third = ask_of_link(mac, one["id"], "c8" * 16, add=add)
    assert third.status_code == 200, third.text



# ---- the second screen parks no grant -------------------------------------------------

def test_a_task_s_grant_is_not_parked_for_a_second_screen_which_signs_none(client, owner, ws, device):
    """What the relay parks is what a signer signs (ade_link.py's SIGN_PURPOSES): no signer shows or signs a task's
    grant, so none is parked, which would hold one of the account's ten places and one of its network's three until
    its nonce ends. A grant is signed with a passkey in the browser, as before."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    r = client.post("/api/ade/sign-requests", headers=h, json={"payload": payload(client, h, task)})
    assert r.status_code == 422 and "purpose" in r.json()["detail"], r.text
    assert client.get("/api/ade/sign-requests", headers=h).json()["requests"] == []
    assert set(signers.PARKED) == set(ade_link.SIGN_PURPOSES)
    granted(client, h, device, task)



# ---- the board's row carries the grant ------------------------------------------------

def test_the_board_s_task_row_carries_its_grant_as_it_stands_however_many_events_follow(client, owner, ws, device,
                                                                                         no_sessions):
    """Past EVENTS_MAX newer events the journal's line of a version is gone from the row, so the row carries the grant
    itself, as grant_view reads it: the version that stands, the head of its digest, and whether it pins the task's
    goal as it stands; nothing for a task with none; no version where every one was withdrawn."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))

    def task_row():
        return {r["id"]: r for r in client.get("/api/ade/board", headers=h).json()["rows"]}[task["id"]]
    assert task_row().get("grant", "not on the row") is None
    laptop = Authenticator(-8)
    assert add(client, h, laptop, name="Laptop").status_code == 200
    v1 = granted(client, h, device, task)
    with store.connect() as db:
        for _ in range(board.EVENTS_MAX + 1):
            tasks.journal(db, user["id"], task["id"], "task.steps_changed", {"steps": 2}, now=int(time.time()))
    r = task_row()
    assert not [e for e in r["events"] if e["kind"] == "task.grant_signed"], r["events"]
    assert r.get("grant") == {"version": 1, "digest": digest_of(v1)[:12], "ask_current": True}, r
    version = task_of(client, h, task["id"])["state_version"]
    assert client.put(f"{T}/{task['id']}", headers=h, json={"state_version": version,
                                                             "goal": "Make the file three lines long."}).status_code == 200
    assert task_row().get("grant") == {"version": 1, "digest": digest_of(v1)[:12], "ask_current": False}
    # a version signed with the passkey that then goes: the row says the one that stands, or none
    changed = task_of(client, h, task["id"])
    p2 = payload(client, h, changed, version=2, prev=digest_of(v1))
    assert sign(client, h, laptop, task["id"], p2).status_code == 200
    assert task_row().get("grant") == {"version": 2, "digest": digest_of(p2)[:12], "ask_current": True}
    assert remove(client, h, passkey_named(user, "Laptop")).status_code == 200
    assert task_row().get("grant") == {"version": 1, "digest": digest_of(v1)[:12], "ask_current": False}
    assert remove(client, h, passkey_named(user, "Ana's iPhone")).status_code == 200
    assert task_row().get("grant") == {"version": None, "digest": None, "ask_current": None}
    # every row the board gives carries the field, in board.FIELDS's order with it last
    assert all(tuple(x) == board.FIELDS for x in client.get("/api/ade/board", headers=h).json()["rows"])



# ---- an ask against the grant as it stands --------------------------------------------

def test_an_ask_carries_the_grant_that_stands_and_the_goal_its_version_pins(client, owner, ws, device):
    """The owner's list shows an ask against the grant as it stands, each value it changes from what to what: the ask
    carries the scope of the version that stands (null where none does), and the task's goal as it stands, which the
    version that signs it pins."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    mac = paired(client, h)
    assert ask_of_link(mac, task["id"], "a5" * 16, add={"egress": ["pypi.org"]}).status_code == 200
    first = the_ask(client, h)
    assert (first.get("stands", "not said"), first.get("goal")) == (None, task["goal"]), first
    assert client.post(f"{GRANTS}/{first['id']}/refuse", headers=h).status_code == 200
    v1 = granted(client, h, device, task, environments=[{"name": "production", "rung": "L3"}],
                 rungs={"merge": "L3", "deploy": "L3"})
    assert ask_of_link(mac, task["id"], "a6" * 16, add={"environments": [{"name": "production", "rung": "L2"}]},
                       rungs={"merge": "L2", "deploy": "L2"}).status_code == 200
    asked = the_ask(client, h)
    assert asked.get("stands") == {k: v1[k] for k in ("repositories", "environments", "secrets", "egress", "lease_end",
                                                       "rungs")}, asked
    assert asked["scope"]["environments"] == [{"name": "production", "rung": "L2"}]
    assert asked.get("goal") == task["goal"]



def test_an_ask_s_grant_stands_after_a_withdrawal_as_the_version_that_stands(client, owner, ws, device):
    """After a withdrawal, the grant an ask is shown against is the version that stands (version 1), never the newest
    kept (the withdrawn version 2)."""
    h, user = owner
    own(user, ACC["A"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    v1, p2 = laptop_signed_v2(client, h, device, task)
    assert remove(client, h, passkey_named(user, "Laptop")).status_code == 200
    mac = paired(client, h)
    token = [{"name": "TOKEN", "lease_seconds": 600}]
    assert ask_of_link(mac, task["id"], "a7" * 16, add={"secrets": token}).status_code == 200
    asked = the_ask(client, h)
    assert asked.get("stands") == {k: v1[k] for k in ("repositories", "environments", "secrets", "egress", "lease_end",
                                                       "rungs")}, asked
