"""Where an account's work lives: its
repository, its own machine, or a workspace this deployment runs for it, chosen in the account and kept there, through
GET and PUT /api/ade/storage.

Nothing here imports the module that keeps the choice: most tests read or put it through those routes first, and the
others read what a connection's answer, the ADE page's form or table, or a task's hold says, so where it is not built
a test fails on an assertion, never on an import. What the choice gates is read where the app already does it: each
provider's start, a schedule's firing and a hook's delivery, a task's automatic continuation, a run asked for by hand,
a teammate's hand-off and its way back, and a signed move, before its hold and as it ends; what it leaves when it
changes is what the account's rows hold, and nothing is deleted. Then a repository connection that writes
no records says so, and why, and a deployment
with no key-store key offers the repository choice for public repositories, board only; and an account that
is not an admin, nor the desktop app's own, choosing its own machine is asked once to turn end-to-end encryption on.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import time

import pytest

from conftest import ADMIN, signed_up
from proofpane_open import auth, desktop, store
from proofpane_open.ade import agents, holds, ledger, task_offers, tasks, triggers, workspaces

ST = "/api/ade/storage"
A = "/api/ade/agents"
MEMBER = "member@example.com"
KEY = base64.b64encode(bytes(range(32))).decode()
NODE = shutil.which("node")
HERE = os.path.dirname(os.path.abspath(__file__))

HOSTED_LABEL = "In a workspace this deployment runs for you"
REPOSITORY = ("Your code and the ADE's signed answers, windows and stops live in your own repository on GitHub; this "
              "deployment keeps, in its own database, your connection and the token you give it, sealed, the commands "
              "it holds for your approval, its Verify results and a key for each alert it has tried to send you, and "
              "keeps what it reads from GitHub in its memory.")
MACHINE = ("Your code and files stay where your linked sessions run, on your machine or in your own cloud sessions; this "
           "deployment keeps what those sessions send it (their status, questions, checkpoints, their list of sessions "
           "and, if you let them, their conversations) and the commands it holds for your approval.")
HOSTED_FLY = ("Your code, your files and your programs' sign-ins live on a machine this deployment runs for you; its "
              "operator can read them, and your share of the deployment's monthly cap, or the cap itself, can stop the "
              "machine.")
HOSTED_LOCAL = "Your code and files live in a workspace this app runs on its own machine, as the app's own user."


def me(client, h):
    return auth.user(client.get("/api/auth/me", headers=h).json()["id"])


def storage(client, h):
    r = client.get(ST, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def choose(client, h, choice):
    r = client.put(ST, headers=h, json={"choice": choice})
    assert r.status_code == 200, r.text
    return r.json()


def offered(view):
    return {c["choice"]: c for c in view["choices"]}


def refused_start(provider, user):
    """The start `provider.ensure` makes for `user`: the WorkspaceAccess it raised, or None where it started."""
    try:
        provider.ensure(user)
    except workspaces.WorkspaceAccess as e:
        return e
    return None


# ---- the local provider's agent, standing in ---------------------------------------------------------------------

class Agent:
    """A workspace agent as the local provider starts it (workspaces.LocalProvider.ensure), standing in: it writes the
    file the provider waits for, its pid and a port, and runs until it is ended; `starts` keeps each start's root."""
    starts = []

    def __init__(self, argv, **kwargs):
        root = argv[argv.index("--root") + 1]
        Agent.starts.append(root)
        self.pid, self.returncode = 900000 + len(Agent.starts), None
        with open(os.path.join(root, "agent.json"), "w", encoding="utf-8") as fh:
            json.dump({"pid": self.pid, "port": 9}, fh)

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


@pytest.fixture
def local(monkeypatch):
    """The local provider, as the desktop app and a single-user install run it, its agent the stand-in above."""
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setattr(workspaces, "local_gate_settings", lambda gemini=False, qwen=False: None)
    monkeypatch.setattr(workspaces.subprocess, "Popen", Agent)
    Agent.starts = []
    yield workspaces.provider()
    workspaces.shutdown()


@pytest.fixture
def fly(monkeypatch):
    """The fly provider against the stand-in for Fly's Machines API (fly_fixtures.py), as test_workspace_fly.py sets
    it up; for admins only unless a test says everyone."""
    from fly_fixtures import FakeFly
    from proofpane_open.ade import fly_provider
    app, token = "pp-workspaces-test", "fo1_not_a_real_token_" + "z" * 20
    fake = FakeFly(app, token)
    for name, value in {"OPEN_EDITION_ADE": "fly", "OPEN_EDITION_WORKSPACE_APP": app,
                        "OPEN_EDITION_WORKSPACE_IMAGE": f"registry.fly.io/{app}:v1",
                        "OPEN_EDITION_WORKSPACE_FLY_TOKEN": token, "OPEN_EDITION_WORKSPACE_OWN_NETWORK": "yes",
                        "OPEN_EDITION_KEYSTORE_KEY": KEY, "OPEN_EDITION_FLY_API": fake.url,
                        "OPEN_EDITION_WORKSPACE_REGION": "syd"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("OPEN_EDITION_WORKSPACE_FOR", raising=False)
    monkeypatch.setattr(fly_provider, "wait_healthy", lambda endpoint, wait=None: None)
    yield fake
    workspaces.shutdown()
    fake.close()


def made(fake):
    """What the stand-in for Fly holds: (volumes, machines not destroyed)."""
    return (list(fake.volumes.values()),
            [m for m in fake.machines.values() if m["state"] != "destroyed"])


# ---- the choice and its default ------------------------------------------------------------------------------------

def test_a_new_account_keeps_its_work_on_its_own_machine_until_it_chooses(client, fly):
    """The default for a new account is its own machine, chosen by nobody, under every provider; and the
    choice the account makes is the one it reads after."""
    h = signed_up(client)
    view = storage(client, h)
    assert (view["choice"], view["chosen"]) == ("machine", False)
    assert [c["choice"] for c in view["choices"]] == ["repository", "machine", "hosted"]
    assert offered(view)["machine"]["sentence"] == MACHINE
    assert view["refusal"] and "on its own machine" in view["refusal"] and HOSTED_LABEL in view["refusal"]
    view = choose(client, h, "repository")
    assert (view["choice"], view["chosen"]) == ("repository", True)
    assert storage(client, h)["choice"] == "repository" and "in its repository" in storage(client, h)["refusal"]
    view = choose(client, h, "hosted")
    assert (view["choice"], view["chosen"], view["refusal"]) == ("hosted", True, None)


def test_with_the_workspace_off_a_new_account_s_choice_is_its_own_machine(client):
    h = signed_up(client, MEMBER)
    view = storage(client, h)
    assert (view["choice"], view["chosen"]) == ("machine", False)
    assert offered(view)["hosted"]["offered"] is False


def test_an_account_with_a_workspace_reads_hosted_until_it_chooses_otherwise(client, fly):
    """Under the fly provider an account has a workspace when it has an ade_workspaces row (which only that provider
    writes): with no choice kept it reads hosted, so no account that has one loses it when this ships; one that chose
    keeps its choice, the row there or not."""
    h = signed_up(client)
    user = me(client, h)
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_workspaces (user_id, app, region, created_at, updated_at) VALUES (?,?,?,?,?)",
                   (user["id"], "pp-workspaces-test", "syd", now, now))
    view = storage(client, h)
    assert (view["choice"], view["chosen"], view["refusal"]) == ("hosted", False, None)
    assert choose(client, h, "machine")["choice"] == "machine"
    assert storage(client, h)["choice"] == "machine"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_workspaces WHERE user_id = ?", (user["id"],)).fetchone()[0] == 1


def test_the_desktop_app_s_account_still_starts_its_workspace_with_the_app(client, local):
    """The desktop app makes its one account with no password (desktop.ensure_account) and starts its workspace with
    the app (desktop.py, announce): under the local provider an account it gives a workspace has one, so with no
    choice kept it reads hosted and that start goes on as before."""
    user_id = desktop.ensure_account(ADMIN)
    h = {"Authorization": "Bearer " + auth.issue_token(user_id)}
    view = storage(client, h)
    assert (view["choice"], view["chosen"], view["refusal"]) == ("hosted", False, None)
    assert offered(view)["hosted"]["sentence"] == HOSTED_LOCAL
    user = auth.user(user_id)
    endpoint = workspaces.for_user(user).ensure(user)          # the line the desktop app runs as it starts
    assert endpoint is not None and endpoint.http == "http://127.0.0.1:9" and len(Agent.starts) == 1


# ---- what is offered -----------------------------------------------------------------------------------------------

def test_with_no_provider_the_hosted_choice_is_not_offered_and_says_why(client):
    view = storage(client, signed_up(client))
    choices = offered(view)
    assert choices["repository"]["offered"] and choices["machine"]["offered"]
    assert choices["hosted"]["offered"] is False
    assert choices["hosted"]["why"] == "The hosted workspace is off on this deployment."


def test_under_fly_the_hosted_choice_is_offered_to_admins_or_to_everyone_as_the_deployment_says(client, fly,
                                                                                                monkeypatch):
    admin, member = signed_up(client), signed_up(client, MEMBER)
    hosted = offered(storage(client, admin))["hosted"]
    assert (hosted["offered"], hosted["why"], hosted["sentence"]) == (True, None, HOSTED_FLY)
    hosted = offered(storage(client, member))["hosted"]
    assert (hosted["offered"], hosted["why"]) == (False, "On this deployment a workspace is for admins only, for now.")
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_FOR", "everyone")
    hosted = offered(storage(client, member))["hosted"]
    assert (hosted["offered"], hosted["why"]) == (True, None)


def test_the_local_provider_on_the_hosted_site_offers_no_hosted_choice(client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("FLY_APP_NAME", "pp-community")
    hosted = offered(storage(client, signed_up(client)))["hosted"]
    assert (hosted["offered"], hosted["why"]) == (False, "The local workspace never runs on the hosted site.")


def test_a_choice_not_offered_is_refused_and_nothing_is_kept(client, fly):
    member = signed_up(client, MEMBER)
    r = client.put(ST, headers=member, json={"choice": "hosted"})
    assert r.status_code == 409 and r.json()["detail"] == "On this deployment a workspace is for admins only, for now."
    assert storage(client, member)["chosen"] is False
    r = client.put(ST, headers=member, json={"choice": "elsewhere"})
    assert r.status_code == 400 and storage(client, member)["chosen"] is False
    assert client.get(ST).status_code == 401


# ---- each provider's start -----------------------------------------------------------------------------------------

def test_the_fly_provider_starts_no_machine_for_an_account_whose_choice_is_not_hosted(client, fly):
    h = signed_up(client)
    view = choose(client, h, "machine")
    user = me(client, h)
    refused = refused_start(workspaces.for_user(user), user)
    assert refused is not None and (refused.status, str(refused)) == (403, view["refusal"])
    assert made(fly) == ([], []) and [c for c in fly.log if c[0] != "GET"] == []
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM ade_workspaces WHERE user_id = ?", (user["id"],)).fetchone() is None
    choose(client, h, "hosted")
    assert refused_start(workspaces.for_user(user), user) is None
    volumes, machines = made(fly)
    assert len(volumes) == 1 and len(machines) == 1 and machines[0]["state"] == "started"


def test_a_new_account_that_has_not_chosen_gets_no_machine_started(client, fly):
    """The default at the start: an account with no choice kept and no workspace keeps its work on its own machine, the
    default, so neither the provider nor the page's Start starts anything for it until it chooses the hosted
    workspace; and the refusal does not say the account set what it never chose."""
    h = signed_up(client)
    view = storage(client, h)
    assert (view["choice"], view["chosen"]) == ("machine", False)
    assert "on its own machine" in view["refusal"] and "as set" not in view["refusal"]
    user = me(client, h)
    refused = refused_start(workspaces.for_user(user), user)
    assert refused is not None and (refused.status, str(refused)) == (403, view["refusal"])
    r = client.post("/api/ade/workspace/start", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == view["refusal"]
    assert made(fly) == ([], [])


def test_an_account_whose_workspace_came_before_any_choice_still_starts_it(client, fly):
    """The default's other half at the start: an account with a workspace and no choice kept, as one whose workspace
    came before any choice is, reads hosted, and the provider starts its machine again as before."""
    h = signed_up(client)
    choose(client, h, "hosted")
    user = me(client, h)
    workspaces.for_user(user).ensure(user)
    assert client.post("/api/ade/workspace/stop", headers=h).status_code == 200
    with store.connect() as db:                        # as before this shipped: a workspace, and no choice kept
        db.execute("DELETE FROM ade_storage_choice WHERE user_id = ?", (user["id"],))
    assert storage(client, h)["chosen"] is False
    assert refused_start(workspaces.for_user(user), user) is None
    (machine,) = made(fly)[1]
    assert machine["state"] == "started"


def test_the_start_and_the_stop_say_the_account_s_choice(client, fly):
    """The page keeps the workspace's state as a start or a stop answers it: each says the account's choice, as the
    status does, so a move asks for the hosted choice whichever answered last."""
    h = signed_up(client)
    choose(client, h, "hosted")
    r = client.post("/api/ade/workspace/start", headers=h)
    assert r.status_code == 200 and r.json().get("choice") == "hosted", r.text
    choose(client, h, "machine")
    r = client.post("/api/ade/workspace/stop", headers=h)
    assert r.status_code == 200 and r.json().get("choice") == "machine", r.text


def test_the_local_provider_starts_no_workspace_for_an_account_whose_choice_is_not_hosted(client, local, env):
    h = signed_up(client)
    view = choose(client, h, "repository")
    user = me(client, h)
    refused = refused_start(workspaces.for_user(user), user)
    assert refused is not None and (refused.status, str(refused)) == (403, view["refusal"])
    assert Agent.starts == [] and not os.path.exists(os.path.join(str(env), "workspaces", user["id"]))
    choose(client, h, "hosted")
    assert refused_start(workspaces.for_user(user), user) is None and len(Agent.starts) == 1


def test_a_start_from_the_page_is_refused_with_the_choice_s_sentence(client, fly):
    h = signed_up(client)
    view = choose(client, h, "machine")
    r = client.post("/api/ade/workspace/start", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == view["refusal"]
    assert made(fly) == ([], [])


def test_an_account_that_left_the_hosted_choice_still_reads_stops_and_reads_the_finishes_of_its_workspace(client,
                                                                                                         fly):
    """The stop and the finishes go through workspace_user, which this does not gate, and the
    status route says the account's choice; leaving the hosted choice stops a running machine at once
    (test_hosted_keep.py), and a stop asked for after that still answers."""
    h = signed_up(client)
    choose(client, h, "hosted")
    user = me(client, h)
    workspaces.for_user(user).ensure(user)
    choose(client, h, "machine")
    state = client.get("/api/ade/workspace", headers=h).json()
    assert state["choice"] == "machine" and state["running"] is False
    assert client.get("/api/ade/workspace/finishes", headers=h).status_code == 200
    r = client.post("/api/ade/workspace/stop", headers=h)
    assert r.status_code == 200 and r.json()["running"] is False
    (machine,) = made(fly)[1]
    assert machine["state"] == "stopped"
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 403
    assert made(fly)[1][0]["state"] == "stopped"


# ---- firings, runs, hand-offs and moves ----------------------------------------------------------------------------

@pytest.fixture
def agent_owner(client, local, monkeypatch, tmp_path):
    """An admin under the local provider, with one agent; a start an agent's run asks for is kept, not made, until a
    test puts the real one back."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    asked = []
    real = agents._deliver_soon
    monkeypatch.setattr(agents, "_deliver_soon", lambda user, start=False: (asked.append(start) if start else None)
                        or False)
    h = signed_up(client)
    r = client.post(A, headers=h, json={"name": "Triage", "brief": "hello", "repo": "demo"})
    assert r.status_code == 200, r.text
    return h, r.json()["id"], asked, real


def runs_of(agent_id):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_agent_runs WHERE agent_id = ? ORDER BY queued_at, run_id",
                                            (agent_id,))]


def test_a_schedule_s_firing_is_held_with_the_choice_s_sentence_and_its_next_time_set(client, agent_owner):
    h, agent_id, asked, _ = agent_owner
    view = choose(client, h, "machine")
    t = client.post(f"{A}/{agent_id}/triggers", headers=h, json={"kind": "schedule", "cron": "*/5 * * * *",
                                                                  "tz": "UTC"}).json()
    ((tid, run_id, why),) = triggers.tick(now=t["next_at"])
    assert (tid, run_id, why) == (t["id"], None, view["refusal"])
    got = client.get(f"{A}/{agent_id}", headers=h).json()["triggers"][0]
    assert got["last_error"] == view["refusal"] and got["next_at"] == t["next_at"] + 300
    assert runs_of(agent_id) == [] and asked == []
    choose(client, h, "hosted")
    ((_, run_id, why),) = triggers.tick(now=t["next_at"] + 300)
    assert run_id is not None and why is None and asked == [True]


def test_a_hook_s_delivery_is_answered_409_with_the_choice_s_sentence(client, agent_owner):
    h, agent_id, asked, _ = agent_owner
    view = choose(client, h, "repository")
    t = client.post(f"{A}/{agent_id}/triggers", headers=h, json={"kind": "hook", "hook_type": "bearer"}).json()
    r = client.post(f"/api/ade/hooks/{t['id']}", content=b"{}", headers={"Authorization": f"Bearer {t['secret']}"})
    assert r.status_code == 409 and r.json()["detail"] == view["refusal"]
    assert runs_of(agent_id) == [] and asked == []
    assert client.get(f"{A}/{agent_id}", headers=h).json()["triggers"][0]["last_error"] == view["refusal"]


def test_a_firing_for_an_account_that_has_not_chosen_and_has_no_workspace_is_held(client, fly, monkeypatch, tmp_path):
    """The default at a firing: under the fly provider an account with no choice kept and no workspace keeps its work on
    its own machine, so its schedule's firing is held with that sentence, and no machine is made."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    h = signed_up(client)
    view = storage(client, h)
    assert (view["choice"], view["chosen"]) == ("machine", False)
    r = client.post(A, headers=h, json={"name": "Triage", "brief": "hello", "repo": "demo"})
    assert r.status_code == 200, r.text
    agent_id = r.json()["id"]
    t = client.post(f"{A}/{agent_id}/triggers", headers=h, json={"kind": "schedule", "cron": "*/5 * * * *",
                                                                  "tz": "UTC"}).json()
    ((tid, run_id, why),) = triggers.tick(now=t["next_at"])
    assert (tid, run_id, why) == (t["id"], None, view["refusal"])
    assert runs_of(agent_id) == [] and made(fly) == ([], [])


def test_a_task_s_automatic_continuation_is_held_with_the_choice_s_sentence(client, agent_owner):
    """A task's automatic continuation (a next step, an auto-resume, a relay's move) is held as a schedule's firing is
    (tasks._firing_hold, in its own words), so no run of it is queued only to be blocked at the start."""
    h, agent_id, _, _ = agent_owner
    view = choose(client, h, "machine")
    user, now = me(client, h), int(time.time())
    with store.connect() as db:
        agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (agent_id,)).fetchone()
        assert tasks._firing_hold(db, user["id"], agent, now) == (view["refusal"], None)
    choose(client, h, "hosted")
    with store.connect() as db:
        assert tasks._firing_hold(db, user["id"], agent, now) is None


def test_a_run_by_hand_is_blocked_with_the_sentence_and_so_are_the_account_s_other_queued_runs(client, agent_owner,
                                                                                               monkeypatch):
    """A run asked for by hand starts the workspace (agents._deliver_soon): refused, every queued run of the account
    not yet sent is blocked, saying why, as it is for any start that fails."""
    h, agent_id, asked, real = agent_owner
    t = client.post(f"{A}/{agent_id}/triggers", headers=h, json={"kind": "schedule", "cron": "*/5 * * * *",
                                                                  "tz": "UTC"}).json()
    ((_, first, _),) = triggers.tick(now=t["next_at"])          # queued while the account was hosted, not sent
    assert first is not None and asked == [True]
    view = choose(client, h, "machine")
    monkeypatch.setattr(agents, "_deliver_soon", real)
    r = client.post(f"{A}/{agent_id}/runs", headers=h, json={"prompt": "the open issues"})
    assert r.status_code == 200, r.text
    deadline = time.time() + 15
    while time.time() < deadline and any(run["state"] == "queued" for run in runs_of(agent_id)):
        time.sleep(0.05)
    runs = runs_of(agent_id)
    reason = ("The workspace could not start: " + view["refusal"])[:300]
    assert [(run["state"], run["cause"], run["reason"]) for run in runs] == [("blocked", "start", reason)] * 2
    assert Agent.starts == []


# a teammate's hand-off and its way back: the teammate track's own world (test_task_offers.py), whose stand-in for
# the fly provider starts any workspace it is asked to; here its start is gated as each real provider's is
from test_task_offers import MATE, OWNER, T, accepted, setup_pressed, world, worked  # noqa: E402,F401  world: fixture


def gated(w, monkeypatch):
    """The world's provider's start, gated as each real provider's is (its ensure asks storage.may_start first, which
    the two tests above show of the fly and local providers); each start it is asked for is counted."""
    from proofpane_open.ade import storage as gate
    real, asked = w.provider.ensure, []

    def ensure(user):
        asked.append(user["id"])
        gate.may_start(user)
        return real(user)
    monkeypatch.setattr(w.provider, "ensure", ensure)
    return asked


def test_a_hand_off_whose_owner_keeps_work_elsewhere_ends_failed_in_the_choice_s_words(world, monkeypatch):
    """The hand-off's push starts the owner's workspace; refused by the owner's choice, the offer
    ends failed with that refusal's own words, not NO_START's."""
    view = choose(world.client, world.h(OWNER), "machine")
    gated(world, monkeypatch)
    task_id, _ = setup_pressed(world)
    made_offer = world.offer_to(task_id)
    world.settle()
    offer = world.offer(made_offer["offer"]["id"])
    assert offer["state"] == "failed" and offer["why"] == view["refusal"] and offer["why"] != task_offers.NO_START
    assert world.get(OWNER, task_id)["state"] == "suspended" and world.ws.remote == {}


CUSTODIAN_ELSEWHERE = ("The custodian keeps their work outside a workspace this deployment runs for them, so their "
                       "workspace did not start and the hand-off ended.")


def test_a_custody_hand_off_whose_custodian_keeps_work_elsewhere_ends_failed_saying_so_of_the_custodian(world,
                                                                                                       monkeypatch):
    """The custody's fetch starts the custodian's workspace: refused by the custodian's choice, the offer ends failed in
    words its owner reads of the custodian, not in the custodian's own refusal, which speaks of "this account"."""
    from test_task_offers import hand, setup_custody
    task_id, _, _ = setup_custody(world)
    found, hold = hand(world, task_id)
    choose(world.client, world.h(OWNER), "hosted")
    view = choose(world.client, world.h(MATE), "repository")
    gated(world, monkeypatch)
    world.running(OWNER)
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "failed" and offer["why"] == CUSTODIAN_ELSEWHERE and offer["why"] != view["refusal"]
    assert world.tasks.created == []


def test_the_way_back_waits_in_the_choice_s_words_tried_every_round_until_the_owner_chooses_hosted_again(world,
                                                                                                         monkeypatch):
    """An owner may leave the hosted choice while a task is with a teammate, and is told what waits; the way
    back then waits with the refusal's words, tried every round, and goes on once the owner chooses hosted again."""
    owner = world.h(OWNER)
    choose(world.client, owner, "hosted")
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    left = storage(world.client, owner)["left_behind"]["machine"]
    assert any("1 task of yours is with a teammate" in line and "tried every round" in line
               and "while it runs" in line for line in left)          # a running workspace takes it, not a stopped one
    view = choose(world.client, owner, "machine")
    asked = gated(world, monkeypatch)
    world.provider.running.discard(world.user(OWNER))
    assert world.client.post(f"{T}/tasks/{task_id}/take-back", headers=owner).status_code == 200
    task_offers.drive()
    assert world.offer(offer_id)["why"] == view["refusal"] and world.tasks.returns == []
    task_offers.drive()
    assert world.offer(offer_id)["why"] == view["refusal"] and world.tasks.returns == []
    assert asked.count(world.user(OWNER)) == 2                 # tried again in the next round
    choose(world.client, owner, "hosted")
    task_offers.drive()
    assert world.offer(offer_id)["state"] == "returned" and world.tasks.returns


from test_ade_link import paired  # noqa: E402
from test_ade_link_commands import device, owner, send  # noqa: E402,F401  fixtures
from test_ade_link_moves import _held_anything, all_moves, lists, move_payload, no_wait, workspace  # noqa: E402,F401


def test_a_signed_move_is_refused_before_it_is_held_where_the_choice_is_not_hosted(client, owner, device, workspace):
    """moves.check_free, which a signed move passes before it is held, refuses it with the choice's
    sentence; once the account chooses hosted, the same move is held."""
    view = choose(client, owner, "machine")
    mac = paired(client, owner)
    lists(mac, ("s1", False))
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s1"))
    assert r.status_code == 403 and r.json()["detail"] == view["refusal"], r.text
    assert _held_anything() == (0, 0) and all_moves() == []
    choose(client, owner, "hosted")
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s1"))
    assert r.status_code == 200 and "held" in r.json(), r.text


def test_a_move_whose_account_leaves_the_hosted_choice_in_its_hold_is_not_stored(client, owner, device, workspace):
    """A move signed while the account kept its work in the workspace is checked again as its hold ends (moves._store):
    the account having chosen otherwise since, it is not stored, and its hold keeps the choice's sentence."""
    choose(client, owner, "hosted")
    mac = paired(client, owner)
    lists(mac, ("s1", False))
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s1"))
    assert r.status_code == 200 and "held" in r.json(), r.text
    view = choose(client, owner, "machine")
    holds.settle()
    assert all_moves() == []
    with store.connect() as db:
        hold = db.execute("SELECT done_at, failed FROM ade_holds WHERE id = ?", (r.json()["held"]["id"],)).fetchone()
    assert hold["done_at"] is not None and hold["failed"] == view["refusal"]


def test_an_account_with_no_choice_and_no_workspace_row_has_its_move_refused(client, owner, device, workspace,
                                                                            monkeypatch):
    """The default at a move: under the fly provider, which writes a row for each workspace it makes, an account with
    neither a row nor a choice keeps its work on its own machine, so its signed move is refused before it is held."""
    monkeypatch.setattr(workspace, "name", "fly")
    view = storage(client, owner)
    assert (view["choice"], view["chosen"]) == ("machine", False)
    mac = paired(client, owner)
    lists(mac, ("s1", False))
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s1"))
    assert r.status_code == 403 and r.json()["detail"] == view["refusal"], r.text
    assert _held_anything() == (0, 0) and all_moves() == []


# ---- what a move leaves --------------------------------------------------------------------------------------------

def test_a_move_away_from_hosted_leaves_the_machine_the_volume_and_the_rows_and_says_so(client, fly):
    h = signed_up(client)
    choose(client, h, "hosted")
    user = me(client, h)
    workspaces.for_user(user).ensure(user)
    left = storage(client, h)["left_behind"]
    assert left["hosted"] == []
    # kept 30 days, then destroyed with its row (test_hosted_keep.py)
    assert any("volume" in line and "kept for 30 days" in line for line in left["machine"])
    assert any("counts toward the number of workspaces" in line for line in left["machine"])   # its row, kept
    assert left["machine"] == left["repository"]
    choose(client, h, "repository")
    volumes, machines = made(fly)
    assert len(volumes) == 1 and len(machines) == 1
    assert [c for c in fly.log if c[0] == "DELETE"] == []
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_workspaces WHERE user_id = ?", (user["id"],)).fetchone()[0] == 1


def test_a_move_away_from_a_repository_or_a_machine_says_what_stays_and_deletes_nothing(client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    h = signed_up(client)
    user = me(client, h)
    ledger.add(user, "github", "acme/demo", "main", ["tasks"], False)
    ledger.add(user, "github", "acme/site", "main", ["tasks"], False, token="github_pat_x")
    choose(client, h, "repository")
    left = storage(client, h)["left_behind"]["machine"]
    assert any(line.startswith("Your 2 connections to repositories stay") for line in left)
    choose(client, h, "machine")
    assert len(ledger.list_for(user["id"])) == 2
    assert storage(client, h)["left_behind"]["repository"] == []        # no session linked: nothing stays


def test_a_move_away_names_the_approvals_and_hook_tokens_that_stay_and_one_connection_as_one(client, monkeypatch):
    """The commands held for approval stay whatever the choice, since nothing deletes one, and the hook
    tokens until they are revoked; and a single connection is said as one."""
    from proofpane_open.ade import approvals
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    h = signed_up(client)
    user = me(client, h)
    ledger.add(user, "github", "acme/demo", "main", ["tasks"], False)
    choose(client, h, "repository")
    (line,) = [one for one in storage(client, h)["left_behind"]["machine"] if "repositor" in one]
    assert line.startswith("Your connection to a repository stays") and "reading it" in line and "them" not in line
    _, token = approvals.create_token(user["id"], "mac")
    approvals.request(approvals.hook_identity(token), "git push")
    assert any("1 command held for your approval stays" in one for one in storage(client, h)["left_behind"]["machine"])
    choose(client, h, "machine")
    left = storage(client, h)["left_behind"]["repository"]
    assert any("1 command held for your approval stays" in one for one in left)
    assert any("1 hook token stays until you revoke it" in one for one in left)


def test_leaving_the_hosted_choice_names_a_hand_off_waiting_on_its_hold(world):
    """A hand-off in its hold pushes from the owner's workspace as the hold ends: what leaving the hosted choice leaves
    says so, since one that finds the workspace stopped then ends failed."""
    choose(world.client, world.h(OWNER), "hosted")
    task_id, _ = setup_pressed(world)
    world.offer_to(task_id)
    left = storage(world.client, world.h(OWNER))["left_behind"]["machine"]
    assert any("1 hand-off of yours waits on its hold" in line for line in left), left


# ---- a repository connection that writes no records ----------------------------------------------------------------

NO_TOKEN = ("Board only: this connection has no token, so it writes no records. Records need a token for this "
            "repository alone with Contents: read and write, given as it is connected.")
CLONE = ("Board only: a clone on this deployment's machine writes no records. Records need the repository "
         "connected from GitHub with a token for it alone with Contents: read and write.")
NO_KEY = "Board only: this deployment keeps no key to seal a token with, so a connection here writes no records."
SHUT = ("This deployment cannot open this connection's token now: its key-store key is gone or has changed. It shows "
        "no board and writes no records: remove it and connect the repository again.")
OTHER_KEY = base64.b64encode(bytes(range(1, 33))).decode()


def test_a_connection_without_a_token_shows_a_board_and_writes_no_records_and_says_so(client, monkeypatch):
    from ade_fixtures import FakeGitHubRepo
    from proofpane_open.ade import sources
    monkeypatch.setattr(sources, "_default_opener", FakeGitHubRepo(files={"t/NEXT.md": "# next\n"}))
    monkeypatch.setattr(sources, "_SHARED_CACHE", sources._Cache())
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    h = signed_up(client)
    connect = lambda **token: client.post("/api/ade/repos", headers=h, json=dict(
        {"repo": "owner/repo", "branch": "main", "dirs": ["t"]}, **token)).json()
    plain, sealed = connect(), connect(token="github_pat_x")
    listed = {r["id"]: r for r in client.get("/api/ade/repos", headers=h).json()["repos"]}
    assert (listed[plain["id"]].get("records"), listed[plain["id"]].get("records_note")) == (False, NO_TOKEN)
    assert (listed[sealed["id"]].get("records"), listed[sealed["id"]].get("records_note")) == (True, None)
    board = client.get(f"/api/ade/repos/{plain['id']}/board", headers=h).json()
    assert board["writable"] is False and [c["name"] for c in board["channels"]] == ["t"]   # a board, no records
    assert client.get(f"/api/ade/repos/{sealed['id']}/board", headers=h).json()["writable"] is True
    sentence = offered(storage(client, h))["repository"]["sentence"]
    assert sentence.startswith(REPOSITORY) and "shows a board and writes no records" in sentence
    assert "Contents: read and write" in sentence
    # a token sealed with a key this deployment now keeps changed, or no longer keeps: no board and no records, said
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", OTHER_KEY)
    listed = {r["id"]: r for r in client.get("/api/ade/repos", headers=h).json()["repos"]}
    assert (listed[sealed["id"]].get("records"), listed[sealed["id"]].get("records_note")) == (False, SHUT)
    assert client.get(f"/api/ade/repos/{sealed['id']}/board", headers=h).status_code == 400
    monkeypatch.delenv("OPEN_EDITION_KEYSTORE_KEY")
    listed = {r["id"]: r for r in client.get("/api/ade/repos", headers=h).json()["repos"]}
    assert (listed[sealed["id"]].get("records"), listed[sealed["id"]].get("records_note")) == (False, SHUT)
    assert (listed[plain["id"]].get("records"), listed[plain["id"]].get("records_note")) == (False, NO_KEY)


def test_a_clone_on_this_machine_writes_no_records_and_says_so(client, tmp_path, monkeypatch):
    from ade_fixtures import BRANCH, make_repo
    path, _ = make_repo(tmp_path)
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", f"demo={path}")
    h = signed_up(client)
    r = client.post("/api/ade/repos", headers=h, json={"source": "local", "repo": "demo", "branch": BRANCH,
                                                         "dirs": ["tasks/demo"], "verify": False})
    assert r.status_code == 200, r.text
    (row,) = client.get("/api/ade/repos", headers=h).json()["repos"]
    assert (row.get("records"), row.get("records_note")) == (False, CLONE)
    assert client.get(f"/api/ade/repos/{row['id']}/board", headers=h).json()["writable"] is False


def test_a_deployment_with_no_key_store_key_offers_the_repository_choice_for_public_repositories_board_only(
        client, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_KEYSTORE_KEY", raising=False)
    h = signed_up(client)
    repository = offered(storage(client, h))["repository"]
    assert repository["offered"] is True
    assert "public repositories" in repository["sentence"] and "board only" in repository["sentence"]
    assert "writes no records" in repository["sentence"] and "the token you give it" not in repository["sentence"]
    r = client.post("/api/ade/repos", headers=h, json={"repo": "acme/private", "branch": "main", "dirs": ["x"],
                                                         "token": "github_pat_secret"})
    assert r.status_code == 400 and "cannot store a token" in r.json()["detail"]
    # a public repository connected there says why it writes no records: no token can be given here, so not that one
    from ade_fixtures import FakeGitHubRepo
    from proofpane_open.ade import sources
    monkeypatch.setattr(sources, "_default_opener", FakeGitHubRepo(files={"t/NEXT.md": "# next\n"}))
    monkeypatch.setattr(sources, "_SHARED_CACHE", sources._Cache())
    r = client.post("/api/ade/repos", headers=h, json={"repo": "owner/repo", "branch": "main", "dirs": ["t"]})
    assert r.status_code == 200, r.text
    (row,) = client.get("/api/ade/repos", headers=h).json()["repos"]
    assert (row.get("records"), row.get("records_note")) == (False, NO_KEY)


def test_the_connect_form_says_a_read_only_token_gives_a_board_and_records_need_read_and_write():
    """Where a token is given: the ADE page's form asks for no read-only token alone, since records need
    Contents: read and write, which a connection then may write (GitHub refusing a write says so, sources.py)."""
    with open(os.path.join(HERE, "..", "web", "index.html"), encoding="utf-8") as fh:
        page = fh.read()
    label = re.search(r'<label id="ade-token-label">(.*?)<input', page, re.S).group(1)
    assert "Contents: read and write" in label and "board" in label, label


LOAD_ADE = r"""
function node(tag) {
  const n = { tag, children: [], own: "", className: "", hidden: false, open: false, type: "",
              append(...xs) { for (const x of xs) this.children.push(typeof x === "string" ? { own: x } : x); },
              appendChild(x) { this.children.push(x); return x; }, addEventListener() {},
              insertRow() { return this.appendChild(node("tr")); } };
  Object.defineProperty(n, "textContent", {
    get() { return this.own + this.children.map((c) => c.tag ? c.textContent : c.own).join(""); },
    set(v) { this.own = String(v); this.children = []; } });
  return n;
}
global.document = { createElement: node };
const table = node("table");
table.tBodies = [node("tbody")];
const els = { "ade-repos": table, "ade-empty": node("p"), "ade-connect": node("details"),
              "ade-source-label": node("label"), "ade-token-label": node("label"), "ade-no-token": node("p"),
              "ade-board": node("div") };
function $(id) { return els[id]; }
function cards() {}
function fail(e) { throw e; }
function removeRepo() {}
function toggleChecks() {}
function openBoard() { return Promise.resolve(); }
function loadProofpane() { return Promise.resolve(); }
function applyPrefill() {}
const ade = { open: null, prefill: null };
const REPOS = JSON.parse(process.argv[2]);
async function api(method, path) { return { repos: REPOS, local: [], can_store_tokens: true }; }
loadAde().then(() => process.stdout.write(JSON.stringify(
  table.tBodies[0].children.map((tr) => tr.children.map((td) => td.textContent)))));
"""


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_page_says_on_a_connection_that_it_writes_no_records(client, tmp_path, monkeypatch):
    """On the connection: the ADE page's table of connections (web/app.js, loadAde) says it of each
    connection whose answer says it writes none, in the answer's own words."""
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    h = signed_up(client)
    user = me(client, h)
    ledger.add(user, "github", "acme/demo", "main", ["tasks"], False)
    ledger.add(user, "github", "acme/site", "main", ["tasks"], False, token="github_pat_x")
    repos = client.get("/api/ade/repos", headers=h).json()["repos"]
    with open(os.path.join(HERE, "..", "web", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    grab = lambda name: re.search(rf"(?:async )?function {name}\(.*?\n\}}\n", script, re.S).group(0)
    probe = tmp_path / "repos.js"
    probe.write_text("".join(grab(f) for f in ("el", "cell", "loadAde")) + LOAD_ADE, encoding="utf-8")
    done = subprocess.run([NODE, str(probe), json.dumps(repos)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    rows = [" ".join(cells) for cells in json.loads(done.stdout)]
    (plain,) = [row for row in rows if row.startswith("acme/demo")]
    (sealed,) = [row for row in rows if row.startswith("acme/site (token)")]
    assert NO_TOKEN in plain and NO_TOKEN not in sealed


# ---- the question to turn end-to-end encryption on -----------------------------------------------------------------

def test_a_member_choosing_its_own_machine_is_asked_once_to_turn_encryption_on(client):
    h = signed_up(client, MEMBER)
    assert storage(client, h)["e2e"]["ask"] is False               # its default, not chosen: nothing asked
    view = choose(client, h, "machine")
    assert view["e2e"] == {"on": False, "not_now": False, "ask": True}
    assert storage(client, h)["e2e"]["ask"] is True                # until it answers
    r = client.put(ST, headers=h, json={"e2e_not_now": True})
    assert r.status_code == 200, r.text
    assert r.json()["e2e"] == {"on": False, "not_now": True, "ask": False}
    choose(client, h, "repository")
    assert choose(client, h, "machine")["e2e"]["ask"] is False     # kept: not asked again
    assert storage(client, h)["e2e"]["not_now"] is True


def test_a_member_choosing_a_repository_is_not_asked(client):
    h = signed_up(client, MEMBER)
    assert choose(client, h, "repository")["e2e"]["ask"] is False


def test_an_admin_is_not_asked(client):
    assert choose(client, signed_up(client), "machine")["e2e"]["ask"] is False


def test_the_desktop_app_s_account_is_not_asked_even_where_it_is_no_admin(client):
    """The desktop app's own account has nothing to confirm with, no password and no passkey (desktop.ensure_account,
    passkeys.confirm_ways): it is not asked, whether or not its deployment names it an admin."""
    for email in (ADMIN, "solo@example.com"):
        user_id = desktop.ensure_account(email)
        h = {"Authorization": "Bearer " + auth.issue_token(user_id)}
        assert choose(client, h, "machine")["e2e"]["ask"] is False, email


def test_an_account_whose_encryption_is_on_is_not_asked(client):
    h = signed_up(client, MEMBER)
    user = me(client, h)
    with store.connect() as db:                    # its newest statement says encryption is on (e2e_keys.on)
        db.execute("INSERT INTO ade_e2e_statements (user_id, seq, id, purpose, payload, created_at) VALUES "
                   "(?,1,?,?,?,?)", (user["id"], "s1", "e2e-key", "{}", int(time.time())))
    assert choose(client, h, "machine")["e2e"] == {"on": True, "not_now": False, "ask": False}


def test_not_now_before_any_choice_is_refused(client):
    h = signed_up(client, MEMBER)
    r = client.put(ST, headers=h, json={"e2e_not_now": True})
    assert r.status_code == 409 and storage(client, h)["e2e"]["not_now"] is False
