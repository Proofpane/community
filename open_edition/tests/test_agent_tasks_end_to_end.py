"""Tasks end to end: the app (ade/tasks.py, ade/task_offers.py) driving real workspace agents
(proofpane_workspace: task_attempt.py, task_runs.py, agents.py), each user's own, through the app's own client
(ade/workspace_client.py) as under the fly provider, where each person's workspace is a machine of their own. Every
claude is the tests' stand-in (chat_stand_in.py) and every gh the stand-in gh (fake_gh.py), as test_task_attempt.py runs
them; nothing else stands in, the provider's machines aside, which are workspace agents on this machine.

Each case that crosses a workspace's seam is here; a few that cross none are test_task_offers.py's unit tests on the
real app with a stand-in workspace instead."""

import contextlib
import json
import os
import secrets
import signal
import subprocess
import time

import pytest

from conftest import signed_up
from proofpane_open import auth, passkeys, store
from proofpane_open.ade import agents, holds, push, task_offers, tasks, workspaces
from proofpane_workspace import providers
from proofpane_workspace.task_attempt import INTENT_FAILED
from test_task_attempt import TaskWorkspace, _GitHub, _files_holding, account_file, claude_starts, commit_in, \
    signed_in_to
from workspace_fixtures import git

A = "/api/ade/agents"
T = A + "/tasks"
PASSWORD = "correct horse battery"
# what an attempt's claude does, as its agent's brief tells the stand-in (chat_stand_in.py): append a line to the
# task's file, then complete the step, whose check counts the file's lines
APPEND = "bash echo line >> lines.txt ;; complete step"


# ---- the provider: each user's workspace a real agent of their own --------------------------------------------------

class Provider:
    """The fly provider as the app sees it: each user's workspace a machine of their own, here a workspace agent on
    this machine (B's TaskWorkspace) started at the user's first `ensure`, in a folder of the user's own, with a secret
    of its own (as each machine is given one), so that a call carrying one user's secret to the other's workspace is
    refused there. Each endpoint carries its own workspace's secret, so a call the app
    sends through the other user's endpoint is accepted there."""

    name = "fly"

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.running = {}               # user id -> TaskWorkspace

    def check_user(self, user):
        pass

    def start(self, user_id, **kw):
        if user_id not in self.running:
            # the row the fly provider writes as it makes a workspace: an account with it keeps its work there until
            # it chooses otherwise (ade/storage.py)
            now = int(time.time())
            with store.connect() as db:
                db.execute("INSERT OR IGNORE INTO ade_workspaces (user_id, app, region, created_at, updated_at) VALUES "
                           "(?,?,?,?,?)", (user_id, "pp-workspaces-test", "syd", now, now))
            folder = self.tmp / ("user-" + user_id[:12])
            folder.mkdir(exist_ok=True)
            self.running[user_id] = TaskWorkspace(folder, secret=secrets.token_hex(20), **kw)
        return self.running[user_id]

    def endpoint(self, user):
        w = self.running.get(user["id"])
        if w is None:
            return None
        return workspaces.Endpoint(http=w.agent.base, ws="ws" + w.agent.base[len("http"):], secret=w.secret)

    def ensure(self, user):
        self.start(user["id"])
        return self.endpoint(user)

    def touch(self, user):
        pass

    def status(self, user):
        return {"provider": "fly", "running": user["id"] in self.running}

    def shutdown(self):
        for w in self.running.values():
            w.stop()
        self.running.clear()


@pytest.fixture
def provider(monkeypatch, tmp_path):
    p = Provider(tmp_path)
    monkeypatch.setattr(workspaces, "provider_name", lambda: "fly")
    monkeypatch.setattr(workspaces, "provider", lambda: p)
    # the test drives the app's sync itself (agents.sync), one round at a time, so no delivery runs beside it
    monkeypatch.setattr(agents, "_deliver_soon", lambda user, start=False: False)
    for name in ("RELAY", "AUTO_RESUME", "TEAMMATE", "CROSS_GROUPS", "OFFER_HOLD", "CROSSING_HOLD", "CROSS_HOLD"):
        monkeypatch.delenv("OPEN_EDITION_ADE_TASK_" + name, raising=False)
    # the relay on, as a deployment that sets it: its default is off unless set, and these tests run the relay end to
    # end
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "on")
    # an account confirmed as the owner's own joins the relay at once (its hold of 0 seconds), as the two-step task
    # below starts there
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_ACCOUNT_HOLD", "0")
    yield p
    p.shutdown()


@pytest.fixture
def told(monkeypatch):
    """Every notice the app sends, as its fixed words (push.words): what it would send to the owner's devices."""
    sent = []
    monkeypatch.setattr(push, "notice", lambda user_id, message, now=None, post=None: sent.append((user_id, message)) or 1)
    return sent


# ---- the app's side, through its routes ------------------------------------------------------------------------------

def person(client, provider, email):
    """A user signed up, and their workspace started: (headers, user, their TaskWorkspace)."""
    h = signed_up(client, email=email, password=PASSWORD)
    user = auth.user(client.get("/api/auth/me", headers=h).json()["id"])
    return h, user, provider.start(user["id"])


def add_account(w, name, email, plan="Claude Max", org_id=None, organization=None, **more):
    """A Claude Code account added in the workspace, with the identity its claude reports (the stand-in's account
    file in its folder): its id there."""
    r = w.http.post("/accounts", json={"kind": "claude", "name": name})
    assert r.status_code == 200, r.text
    account = r.json()["id"]
    folder = os.path.join(w.root, "agent-accounts", account, "claude")
    word = {"Claude Max": "max", "Claude Pro": "pro", "Claude Team": "team", "Claude Enterprise": "enterprise"}[plan]
    values = dict({"email": email, "plan": plan, "auth_plan": word, "org_id": org_id or "org-" + name.lower()}, **more)
    if organization is not None:
        values["organization"] = organization
    account_file(w, folder=folder, **values)
    w.__dict__.setdefault("identities", {})[account] = values
    return account


def account_folder(w, account):
    return os.path.join(w.root, "agent-accounts", account, "claude")


def confirm(client, h, user, account):
    """The owner reads the account's identity in their workspace and confirms it as their own with their password,
    naming the report and orgId the read gave, as the page sends what it showed; its hold of 0 seconds applies at the
    next read. The account's row id in the app."""
    r = client.post(A + "/accounts/report", headers=h, json={"account": account})
    assert r.status_code == 200, r.text
    read = r.json()["account"]
    owner_id = read["id"]
    confirmation = passkeys.confirm(user, password=PASSWORD, subject=f"task-account-own:{owner_id}")["confirmation"]
    r = client.post(f"{A}/accounts/{owner_id}/confirm", headers=h,
                    json={"confirmation": confirmation, "report": read["report"], "org_id": read["org_id"]})
    assert r.status_code == 200, r.text
    listed = {a["id"]: a for a in client.get(A + "/accounts", headers=h).json()["accounts"]}
    assert listed[owner_id]["state"] == "confirmed", listed[owner_id]
    return owner_id


def make_agent(client, h, account, brief=APPEND):
    r = client.post(A, headers=h, json={"name": "Worker", "brief": brief, "repo": "demo", "account": account,
                                        "allow": ["Bash", "Write", "Edit"]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def make_task(client, h, agent_id, steps=2, **over):
    """A task of `steps` steps, step i's check that the file has i lines; or of the steps given, as a list."""
    if isinstance(steps, int):
        steps = [{"objective": f"Append line {i}", "check": f'test "$(wc -l < lines.txt)" -eq {i}'}
                 for i in range(1, steps + 1)]
    body = dict({"agent_id": agent_id, "title": "Count lines", "goal": "Make the file two lines long.",
                 "steps": steps}, **over)
    r = client.post(T, headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()["task"]


def task_of(client, h, task_id):
    r = client.get(f"{T}/{task_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["task"]


def press(client, h, task_id, action, **body):
    if "state_version" not in body and action not in ("stop", "switches"):
        body["state_version"] = task_of(client, h, task_id)["state_version"]
    r = client.post(f"{T}/{task_id}/{action}", headers=h, json=body)
    assert r.status_code == 200, (action, r.text)
    return r.json()


def runs_of(task_id):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_agent_runs WHERE task_id = ? ORDER BY fence",
                                            (task_id,)).fetchall()]


def events(task_id, kind=None):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_agent_task_events WHERE task_id = ? ORDER BY seq", (task_id,)).fetchall()
    return [dict(r, payload=json.loads(r["payload"])) for r in rows if kind is None or r["kind"] == kind]


def settle(user, task_id, until, timeout=180, every=0.2):
    """The app's sync, round after round (send what waits, copy back what changed, decide), with the workspace's
    attempts running beside it, until `until(task)` holds for the task as the app keeps it; the task then."""
    deadline = time.time() + timeout
    while True:
        agents.sync(user)
        with store.connect() as db:
            row = dict(db.execute("SELECT * FROM ade_agent_tasks WHERE id = ?", (task_id,)).fetchone())
        if until(row):
            return row
        if time.time() > deadline:
            raise AssertionError(f"the task did not settle: {row['state']} {row['cause']} {row['reason']} "
                                 f"runs {[(r['fence'], r['state'], r['cause']) for r in runs_of(task_id)]}")
        time.sleep(every)


def step_head(task_id, step):
    """The commit of the step's checkpoint the app took (its last), as the workspace's manifest gives it."""
    with store.connect() as db:
        got = db.execute("SELECT manifest FROM ade_agent_checkpoints WHERE task_id = ? AND step = ? AND kind = 'step' "
                         "AND state = 'ready' ORDER BY n DESC", (task_id, step)).fetchone()
    assert got is not None, f"no checkpoint of step {step} was taken"
    return json.loads(got[0])["head"]


def lines_at(w, agent_id, task_id, commit):
    """The task's file as `commit` holds it, read in the task's worktree in the workspace."""
    work = os.path.join(w.root, "agents", agent_id, "tasks", task_id, "work")
    return git("show", f"{commit}:lines.txt", cwd=work).splitlines()


# ---- two steps, the relay's move between accounts, done -------------------------------------------------------------

def two_accounts(client, provider):
    """The owner, their workspace, and its accounts A and B, both on Claude Max, each confirmed as the owner's own:
    (headers, user, workspace, A's id, B's id), A first in Connections' order."""
    h, user, w = person(client, provider, "ana@example.com")
    a = add_account(w, "A", "a@example.com")
    b = add_account(w, "B", "b@example.com")
    confirm(client, h, user, a)
    confirm(client, h, user, b)
    return h, user, w, a, b


def attempt_folders(w):
    """Each attempt's claude start, in order, by the account folder it ran in (CLAUDE_CONFIG_DIR as it ran)."""
    return [s["effective"].get("CLAUDE_CONFIG_DIR") for s in claude_starts(w) if s["attempt"]]


def run_state(run_id):
    with store.connect() as db:
        return db.execute("SELECT state FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()[0]


def ended(t):
    return t["state"] in ("done", "stopped", "needs_owner") or (t["state"] == "suspended" and t["cause"] != "step_done")


def test_a_two_step_task_moves_from_a_refusing_account_to_the_next_and_ends_done(client, provider, told):
    h, user, w, a, b = two_accounts(client, provider)
    agent_id = make_agent(client, h, a)
    task = make_task(client, h, agent_id)
    press(client, h, task["id"], "start")
    # step 1 on A: one line appended, the step completed with its check
    row = settle(user, task["id"], lambda t: t["step"] == 2 or t["state"] in ("suspended", "needs_owner", "done"))
    assert row["step"] == 2, row
    # step 2 starts on A, which refuses it at its first answer; the relay takes it to B
    account_file(w, folder=account_folder(w, a), email="a@example.com", plan="Claude Max", auth_plan="max",
                 org_id="org-a", first_answer="ratelimit rejected five_hour")
    row = settle(user, task["id"], lambda t: t["state"] in ("done", "needs_owner", "stopped") or (
        t["state"] == "suspended" and t["cause"] != "step_done"))
    assert row["state"] == "done", row
    runs = runs_of(task["id"])
    assert [r["fence"] for r in runs] == [1, 2, 3]
    assert [json.loads(r["backend"])["id"] for r in runs] == [a, a, b]
    assert [r["chosen_by"] for r in runs][1:] == ["next_step", "relay"]
    # the move: a task.continued event marked as the relay's, and a content-free push
    moved = [e for e in events(task["id"], "task.continued") if e["payload"].get("by") == "relay"]
    assert len(moved) == 1, events(task["id"], "task.continued")
    titles = [m.title for _, m in told]
    assert "A task of yours continued on another of your accounts" in titles
    for _, m in told:
        assert agent_id not in m.title + m.body and task["id"] not in m.title + m.body
        assert "a@example.com" not in m.title + m.body and "b@example.com" not in m.title + m.body
    # each attempt's starts in its account's folder: A's three (step 1 frozen and restarted, step 2 frozen and
    # refused), then B's two; B's first a new session, and its only --resume its own unfreezing restart's
    starts = [s for s in claude_starts(w) if s["attempt"]]
    folders = [s["effective"].get("CLAUDE_CONFIG_DIR") for s in starts]
    assert folders == [account_folder(w, a)] * 3 + [account_folder(w, b)] * 2, folders
    b_frozen, b_restarted = starts[3:]
    # B's frozen start takes up no earlier conversation: neither --resume nor --continue (the SDK's
    # continue_conversation). The stand-in takes its session from --resume but does not act on --continue, so only the
    # arguments can show that one
    assert b_frozen["frozen"] and not any(x.startswith(("--resume", "--continue")) for x in b_frozen["args"])
    assert b_frozen["session"] not in [s["session"] for s in starts[:3]]
    (resumed,) = [x for x in b_restarted["args"] if x.startswith("--resume")]
    # the restart names the conversation B's own frozen start made, by the session each start records, not merely one
    # whose first file is in $HOME: A's refused step-2 start left one there too
    assert resumed.split("=", 1)[1] == b_frozen["session"], (resumed, [s["session"] for s in starts])
    # the file has exactly two lines, step 1's once, at the checkpoint the app took for step 2
    assert lines_at(w, agent_id, task["id"], step_head(task["id"], 2)) == ["line", "line"]
    assert lines_at(w, agent_id, task["id"], step_head(task["id"], 1)) == ["line"]


def test_waiting_between_steps_the_owner_s_continue_on_b_runs_step_2_on_b(client, provider, told):
    h, user, w, a, b = two_accounts(client, provider)
    agent_id = make_agent(client, h, a)
    task = make_task(client, h, agent_id, between_steps="wait")
    press(client, h, task["id"], "start")
    row = settle(user, task["id"], lambda t: t["state"] == "suspended" or ended(t))
    assert (row["state"], row["cause"], row["step"]) == ("suspended", "step_done", 2), row
    assert press(client, h, task["id"], "continue-on", backend={"kind": "account", "id": b})["queued"] is True
    row = settle(user, task["id"], ended)
    assert row["state"] == "done", row
    runs = runs_of(task["id"])
    # two runs when pressed: fence 1 on A, fence 2 on B, chosen by the owner
    assert [(r["fence"], json.loads(r["backend"])["id"]) for r in runs] == [(1, a), (2, b)]
    assert runs[1]["chosen_by"] == "owner"
    assert events(task["id"], "task.continued")[-1]["payload"]["by"] == "owner"
    assert attempt_folders(w) == [account_folder(w, a)] * 2 + [account_folder(w, b)] * 2
    assert lines_at(w, agent_id, task["id"], step_head(task["id"], 2)) == ["line", "line"]


def refusing(w, account, resets_in):
    """The account's claude refuses every attempt at its first answer, its identity unchanged: its five-hour window
    rejected, resetting `resets_in` seconds after the refusal (the stand-in's first_answer)."""
    account_file(w, folder=account_folder(w, account), **dict(
        w.identities[account], first_answer=f"ratelimit rejected five_hour resets={resets_in}"))


def test_next_on_b_pressed_while_step_1_works_runs_step_2_on_b(client, provider, told):
    h, user, w, a, b = two_accounts(client, provider)
    # step 1 works for a while after its line, so the owner presses Next on... B while it does
    agent_id = make_agent(client, h, a, brief="bash echo line >> lines.txt ;; sleep 4 ;; complete step")
    task = make_task(client, h, agent_id)
    press(client, h, task["id"], "start")
    first = settle(user, task["id"], lambda t: t["lease_run"] is not None and run_state(t["lease_run"]) == "working"
                   or ended(t))["lease_run"]
    press(client, h, task["id"], "next-on", backend={"kind": "account", "id": b})
    agents.sync(user)
    assert run_state(first) == "working"            # Next on... stops nothing: the live attempt goes on
    row = settle(user, task["id"], ended)
    assert row["state"] == "done", row
    runs = runs_of(task["id"])
    assert [(r["fence"], json.loads(r["backend"])["id"]) for r in runs] == [(1, a), (2, b)]
    assert runs[0]["state"] == "done" and row["asked"] is None
    assert attempt_folders(w) == [account_folder(w, a)] * 2 + [account_folder(w, b)] * 2
    assert lines_at(w, agent_id, task["id"], step_head(task["id"], 2)) == ["line", "line"]


# ---- the relay across groups, and only where both switches let it ---------------------------------------------------

@pytest.mark.parametrize("crossing", ["on", "off", "unset", "switch_off"])
def test_the_relay_crosses_into_a_team_organisation_only_with_both_switches_on(client, provider, told, monkeypatch,
                                                                             crossing):
    if crossing in ("on", "switch_off"):
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    elif crossing == "off":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off")
    h, user, w = person(client, provider, "ana@example.com")
    # Connections' order: A, a seat of Acme's Team organisation; C on Max; B on Pro, which the task runs on. After B,
    # Connections' order alone comes round to A first, so only the relay's preference for the task's own group takes C
    # before it (with B before C, both orders would give C)
    a = add_account(w, "A", "ana@acme.example", plan="Claude Team", org_id="org-acme", organization="Acme")
    c = add_account(w, "C", "ana.max@example.com")
    b = add_account(w, "B", "ana@example.com", plan="Claude Pro")
    for account in (a, c, b):
        confirm(client, h, user, account)
    assert [x["account"] for x in client.get(A + "/accounts", headers=h).json()["accounts"]] == [a, c, b]
    # and A is known to have room, from a task of its own that is done, whose attempt hears A's five-hour window allowed
    # at its first answer (since its last start an event said allowed), while C has never run: so after B, a
    # relay ordering by known room before the group would take A, and only the task's own group first takes C.
    # The event makes the room known whether or not a copy of the attempt ever
    # saw it working, which a short attempt can finish between two copies
    account_file(w, folder=account_folder(w, a), **dict(
        w.identities[a], first_answer=f"ratelimit allowed five_hour resets={5 * 3600}", restart={"first_answer": None}))
    earlier = make_task(client, h, make_agent(client, h, a), steps=1)
    press(client, h, earlier["id"], "start")
    assert settle(user, earlier["id"], ended)["state"] == "done"
    account_file(w, folder=account_folder(w, a), **w.identities[a])
    now = int(time.time())
    refusing(w, b, 5 * 3600)
    refusing(w, c, 4 * 3600)
    agent_id = make_agent(client, h, b)
    task = make_task(client, h, agent_id, steps=1)
    if crossing == "switch_off":
        r = client.post(f"{T}/{task['id']}/switches", headers=h, json={"cross_groups": False})
        assert r.status_code == 200, r.text
    if crossing == "on":
        with store.connect() as db:
            known = {e["account"]: e["known_room"]
                     for e in tasks.relay_set(db, user["id"], task["id"], int(time.time()))}
        assert known[a] is True and known[c] is False, known
    press(client, h, task["id"], "start")
    row = settle(user, task["id"], ended)
    runs = runs_of(task["id"])
    if crossing != "on":
        # A is left out: B, then C, each refused, and the task waits for the earliest reset, C's
        assert [json.loads(r["backend"])["id"] for r in runs] == [b, c]
        assert (row["state"], row["cause"]) == ("suspended", "window"), row
        assert now + 4 * 3600 <= row["waits_until"] <= time.time() + 4 * 3600, row["waits_until"] - now
        assert events(task["id"], "task.crossed") == []
        listed = {e["account"]["account"]: e for e in task_of(client, h, task["id"])["relay_set"]}
        assert listed[a]["eligible"] is False and listed[a]["left_out"], listed[a]
        assert listed[b]["eligible"] and listed[c]["eligible"]
        return
    # C first (the home group, personal), then A (a crossing into Acme's seats), where the step is done
    assert row["state"] == "done", row
    assert [json.loads(r["backend"])["id"] for r in runs] == [b, c, a]
    assert [r["chosen_by"] for r in runs][1:] == ["relay", "relay"]
    (crossed,) = events(task["id"], "task.crossed")
    # groups and references, never an email: from the task's own group onto Acme's seats, C's account to A's
    payload = crossed["payload"]
    assert [payload.get(k) for k in ("from_grp", "to_grp", "org")] == ["personal", "org:org-acme", "org-acme"], payload
    assert [payload.get(k) for k in ("from", "to")] == ["account:" + c, "account:" + a], payload
    assert "@" not in json.dumps(payload), crossed
    assert "A task of yours continued in another organisation" in [m.title for _, m in told]
    for _, m in told:
        assert "Acme" not in m.title + m.body and "@" not in m.title + m.body
    # the crossing's line on the task's page: risk point 1's organisation notice, with its time
    lines = [m for m in task_of(client, h, task["id"])["moves"] if m["kind"] == "crossing"]
    assert len(lines) == 1 and lines[0]["point"] == 1 and lines[0]["at"] >= now, lines


# ---- the weekly reserve ----------------------------------------------------------------------------------------------

def warned(w, account, window, resets_in=3 * 86400):
    """Every attempt on the account hears its `window` near its limit (allowed_warning) at its first answer, the
    frozen start's; the unfreezing restart then works as told (the stand-in's first_answer, and its restart object,
    which a process started with --resume lays over the account file)."""
    account_file(w, folder=account_folder(w, account), **dict(
        w.identities[account], first_answer=f"ratelimit allowed_warning {window} resets={resets_in}",
        restart={"first_answer": None}))


def relay_listing(client, h, task_id):
    return {e["account"]["account"]: e for e in task_of(client, h, task_id)["relay_set"]}


def test_the_weekly_reserve_holds_unattended_starts_and_the_owner_s_press_goes_through(client, provider, told):
    h, user, w = person(client, provider, "ana@example.com")
    hh = add_account(w, "H", "h@example.com")
    g = add_account(w, "G", "g@example.com")
    for account in (hh, g):
        confirm(client, h, user, account)
    warned(w, hh, "seven_day")
    warned(w, g, "seven_day_opus")
    agent_id = make_agent(client, h, hh)
    task = make_task(client, h, agent_id)
    # the owner's Start is a press: H's weekly warning, heard at its first answer, lets it through, and step 1 is done
    # on H; step 2 does not start on H by itself (held back for unattended starts): the relay moves on to G, whose
    # unattended attempt hears its seven_day_opus warning at its first answer and ends before its work; and with H
    # and G both held back, the task waits
    press(client, h, task["id"], "start")
    row = settle(user, task["id"], ended)
    runs = runs_of(task["id"])
    assert [(json.loads(r["backend"])["id"], r["state"]) for r in runs] == [(hh, "done"), (g, "blocked")], [
        (r["state"], r["chosen_by"], r["reason"]) for r in runs]
    assert runs[1]["chosen_by"] != "owner" and runs[1]["reason"].startswith("weekly reserve"), runs[1]
    assert row["state"] == "suspended" and row["step"] == 2, row
    listed = relay_listing(client, h, task["id"])
    assert listed[hh]["left_out"] == listed[g]["left_out"] == "its weekly reserve", listed
    # a terminal session started on H meanwhile, which tells the app nothing, leaves H held back, and nothing starts
    r = client.post("/api/ade/workspace/sessions", headers=h,
                    json={"repo": "demo", "program": "claude", "task": "Look around", "account": hh})
    assert r.status_code == 200, r.text
    agents.sync(user)
    assert relay_listing(client, h, task["id"])[hh]["left_out"] == "its weekly reserve"
    assert len(runs_of(task["id"])) == 2
    # the owner presses Continue on... H: the press goes through the reserve, and step 2 is done there. This attempt
    # hears nothing of H's weekly window, so after its start only the warning heard before it can hold H back: the
    # report stands until a later event for that window or its reset time, whatever else starts on the account
    # meanwhile. With every attempt on H warned again, a warning forgotten at a later start, or one the
    # press cleared, was heard afresh and passed unseen.
    # It hears an event for another weekly window, its seven-day Opus window allowed, which is a later event but not for
    # that window, so it too leaves the warning standing (a weekly one, so
    # that a rule letting the newest weekly event decide fails too)
    account_file(w, folder=account_folder(w, hh), **dict(
        w.identities[hh], first_answer=f"ratelimit allowed seven_day_opus resets={3 * 86400}",
        restart={"first_answer": None}))
    # the copy of H's warning and the press's start are both recorded in the app's whole seconds: the press waits for
    # a later second than the copy's, so that its start is recorded after the warning (in the same second, a rule that
    # forgets a warning heard before the account's last start would find nothing to forget)
    with store.connect() as db:
        heard = db.execute("SELECT seen_at FROM ade_backend_windows WHERE user_id = ? AND backend = ? AND "
                           "rate_window = 'seven_day'", (user["id"], "account:" + hh)).fetchone()["seen_at"]
    while int(time.time()) <= heard:
        time.sleep(0.05)
    press(client, h, task["id"], "continue-on", backend={"kind": "account", "id": hh})
    row = settle(user, task["id"], ended)
    assert row["state"] == "done", row
    runs = runs_of(task["id"])
    assert [(json.loads(r["backend"])["id"], r["chosen_by"]) for r in runs][2:] == [(hh, "owner")]
    # G's attempt did no work: the file has step 1's line and step 2's, and no more
    assert lines_at(w, agent_id, task["id"], step_head(task["id"], 2)) == ["line", "line"]
    # and H is still held back from unattended starts: neither the press nor the start it made lifted its reserve
    assert relay_listing(client, h, task["id"])[hh]["left_out"] == "its weekly reserve"
    # in the case the rule is for: H's last start, the press's, came after H's warning, and nothing since spoke of it
    with store.connect() as db:
        started_at = db.execute("SELECT last_start_at FROM ade_account_owners WHERE user_id = ? AND account = ?",
                                (user["id"], hh)).fetchone()[0]
        warning = db.execute("SELECT status, seen_at FROM ade_backend_windows WHERE user_id = ? AND backend = ? AND "
                             "rate_window = 'seven_day'", (user["id"], "account:" + hh)).fetchone()
        opus = db.execute("SELECT status, seen_at FROM ade_backend_windows WHERE user_id = ? AND backend = ? AND "
                          "rate_window = 'seven_day_opus'", (user["id"], "account:" + hh)).fetchone()
    assert warning is not None and warning["status"] == "allowed_warning", warning and dict(warning)
    assert warning["seen_at"] < started_at, (dict(warning), started_at)
    # the other weekly window's event was copied with the press's attempt, in its start's second or after it
    assert opus is not None and opus["status"] == "allowed" and opus["seen_at"] >= started_at, opus and dict(opus)
    # a task of H's own started there, a press, which the reserve does not hold back, and whose attempt hears nothing of
    # H's weekly windows, leaves H held back still: so the check after the next task is not passed by a Start that lifts
    # the reserve or clears the warning
    account_file(w, folder=account_folder(w, hh), **w.identities[hh])
    third = make_task(client, h, make_agent(client, h, hh), steps=1)
    press(client, h, third["id"], "start")
    assert settle(user, third["id"], ended)["state"] == "done"
    assert relay_listing(client, h, task["id"])[hh]["left_out"] == "its weekly reserve"
    # and a later event for that window lifts it: another task of H's, started there, whose attempt hears H's
    # seven-day window allowed at its first answer, leaves H held back no more
    account_file(w, folder=account_folder(w, hh), **dict(
        w.identities[hh], first_answer=f"ratelimit allowed seven_day resets={3 * 86400}",
        restart={"first_answer": None}))
    fourth = make_task(client, h, make_agent(client, h, hh), steps=1)
    press(client, h, fourth["id"], "start")
    assert settle(user, fourth["id"], ended)["state"] == "done"
    assert relay_listing(client, h, task["id"])[hh]["left_out"] is None
    # until the owner lifts G's reserve ("Use to the limit"): then an unattended attempt on G goes on through its
    # warning, and a task whose step 2 starts there by itself is done there
    owner_g = relay_listing(client, h, task["id"])[g]["account"]["id"]
    r = client.put(f"{A}/accounts/{owner_g}", headers=h, json={"reserve_lifted": True})
    assert r.status_code == 200, r.text
    on_g = make_agent(client, h, g)
    second = make_task(client, h, on_g)
    press(client, h, second["id"], "start")
    row = settle(user, second["id"], ended)
    runs = runs_of(second["id"])
    assert row["state"] == "done", (row, [(r["state"], r["chosen_by"], r["reason"]) for r in runs])
    assert [(json.loads(r["backend"])["id"], r["state"]) for r in runs] == [(g, "done"), (g, "done")]
    assert runs[1]["chosen_by"] != "owner"


# ---- the machine stopped at each kill point of step 1 ----------------------------------------------------------------

def held_command(marks):
    """A command that waits until the test lets it go (`release` among `marks`), at most a minute, so that none
    outlives the test."""
    return (f'i=0; while [ ! -e "{marks}/release" ] && [ "$i" -lt 600 ]; do sleep 0.1; i=$((i+1)); done')


def claude_pids(w):
    """The pids of this workspace's attempt's claude, its restart (the stand-in, started with --resume)."""
    out = subprocess.run(["ps", "-ww", "-eo", "pid=,args="], capture_output=True, text=True, check=True).stdout
    stand_in = str(w.bin / "claude")
    return [int(line.split(None, 1)[0]) for line in out.splitlines()
            if stand_in in line and "--resume" in line]


def stop_machine(w, how="soft"):
    """The machine stops, and the attempt's claude with it (the workspace leaves an attempt's claude to its end when it
    stops, which a machine's stop does not): "soft" as Fly stops a machine, the workspace told to stop first;
    "hard" as a power loss, the workspace killed with nothing of its own run."""
    if how == "hard":
        w.agent.proc.kill()
        w.agent.proc.wait(10)
        w.agent.http.close()
    else:
        w.stop()
    for pid in claude_pids(w):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_view(w, run_id):
    """The run as the workspace's record has it now, read there (not the app's copy)."""
    (got,) = [r for r in w.http.get("/agents/runs?since=0").json()["runs"] if r["run_id"] == run_id]
    return got


def task_row(task_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_agent_tasks WHERE id = ?", (task_id,)).fetchone())


def checkpoints_taken(task_id):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_agent_checkpoints WHERE task_id = ? AND state = 'ready' ORDER BY n",
                          (task_id,)).fetchall()
    return [dict(r, manifest=json.loads(r["manifest"] or "{}")) for r in rows]


KILL_POINTS = ("before_call", "after_write", "after_commit", "after_record")


@pytest.mark.parametrize("how", ["soft", "hard"])
@pytest.mark.parametrize("outcome", ["keep", "discard"])
@pytest.mark.parametrize("point", KILL_POINTS)
def test_a_machine_stop_at_each_kill_point_leaves_step_1_s_line_once(client, provider, told, point, outcome, how):
    h, user, w = person(client, provider, "ana@example.com")
    a = add_account(w, "A", "a@example.com")
    confirm(client, h, user, a)
    marks = os.path.join(str(w.tmp), "kill-marks")
    os.makedirs(marks)
    # step 1's check, at the last two kill points, writes the commit it checks (the checkpoint's own, made before its
    # check) and waits for the test before it says the file has its one line
    check1 = 'test "$(wc -l < lines.txt)" -eq 1'
    if point in ("after_commit", "after_record"):
        check1 = f'git rev-parse HEAD > "{marks}/commit" && {held_command(marks)} && {check1}'
    brief = {"before_call": "sleep 30 ;; bash echo line >> lines.txt ;; complete step",
             "after_write": "bash echo line >> lines.txt ;; sleep 30 ;; complete step"}.get(point, APPEND)
    agent_id = make_agent(client, h, a, brief=brief)
    task = make_task(client, h, agent_id, between_steps="wait", steps=[
        {"objective": "Append line 1", "check": check1},
        {"objective": "Append line 2", "check": 'test "$(wc -l < lines.txt)" -eq 2'}])
    branch = "pp/task-" + task["id"]
    work = task_work(w, agent_id, task["id"])
    lines = os.path.join(work, "lines.txt")
    press(client, h, task["id"], "start")
    (first,) = runs_of(task["id"])
    stopped_claude = None
    if point == "before_call":
        # the unfreezing restart's claude works through its first action, which is no call
        syncing_until(user, lambda: len(attempt_folders(w)) == 2, "the attempt's claude did not start working")
        time.sleep(1)
    elif point == "after_write":
        syncing_until(user, lambda: os.path.exists(lines) and open(lines).read() == "line\n", "no line was written")
    else:
        # the checkpoint's commit made, its check under way: the checkpoint pending in the record
        # the shell makes the mark before git writes the commit into it: wait for the whole line, not for the file
        syncing_until(user, lambda: commit_written(marks), "the step's check did not start")
        assert [c["state"] for c in run_view(w, first["run_id"])["task"]["checkpoints"]] == ["pending"]
        if point == "after_record":
            # the claude, waiting for complete_step's answer, is made to wait on (stopped), so that the attempt still
            # works when its checkpoint's record is completed: the machine then stops before the attempt's end, and
            # before the app has copied the record (nothing here syncs meanwhile)
            (stopped_claude,) = claude_pids(w)
            os.kill(stopped_claude, signal.SIGSTOP)
            open(os.path.join(marks, "release"), "w").close()
            deadline = time.time() + 30
            while [c["state"] for c in run_view(w, first["run_id"])["task"]["checkpoints"]] != ["complete"]:
                assert time.time() < deadline, "the checkpoint's record was not completed"
                time.sleep(0.05)
            assert run_view(w, first["run_id"])["state"] == "working"
            with store.connect() as db:
                assert db.execute("SELECT COUNT(*) FROM ade_agent_checkpoints WHERE task_id = ? AND kind = 'step'",
                                  (task["id"],)).fetchone()[0] == 0
    commit = open(os.path.join(marks, "commit")).read().strip() if point in ("after_commit", "after_record") else None
    stop_machine(w, how)
    assert stopped_claude is None or stopped_claude not in claude_pids(w)
    if point == "after_commit":
        # the stop after the checkpoint's branch moved, before its record was completed: the branch moved here, to the
        # checkpoint's own commit, as the workspace moves it once the check passes (that moment is too short to stop in)
        assert git("rev-parse", f"{commit}^", cwd=work) == git("rev-parse", branch, cwd=work)
        git("update-ref", "refs/heads/" + branch, commit, cwd=work)
    w.start()
    row = settle(user, task["id"], ended)
    open(os.path.join(marks, "release"), "w").close()
    # each time: the task needs its owner, the attempt blocked by the machine's stop, its agent held
    assert (row["state"], row["cause"]) == ("needs_owner", "worktree"), row
    (run,) = runs_of(task["id"])
    assert (run["state"], run["cause"]) == ("blocked", "machine"), run
    assert agent_paused(agent_id)
    # checkpoint 0, the commit the task's worktree was cut from, as the app learnt it from the attempt's record
    zero = git("rev-parse", "main", cwd=os.path.join(w.root, "repos", "demo"))
    assert task_row(task["id"])["checkpoint0"] == zero
    taken = [(c["kind"], c["manifest"]["head"]) for c in checkpoints_taken(task["id"])]
    if point == "after_commit":
        # completed at the next start from the branch's head, one commit of step 1; taken as progress, not as the
        # step: its check's outcome went with the machine, and the branch moves after a failing check too
        assert taken == [("progress", commit)]
    elif point == "after_record":
        # its record completed before the stop: step 1's checkpoint, its check passed, the step counted
        assert taken == [("step", commit)]
    else:
        assert taken == []
    if commit is not None:
        assert git("rev-list", "--count", f"{zero}..{commit}", cwd=work) == "1"
    newest = commit or zero
    # the owner resolves the worktree, resumes the agent, and continues
    press(client, h, task["id"], "resolve", what="worktree", outcome=outcome)
    kept = git("rev-parse", branch, cwd=work)
    assert client.post(f"{A}/{agent_id}/resume", headers=h).status_code == 200
    step = 2 if point == "after_record" else 1
    has_line = os.path.exists(lines) and open(lines).read() == "line\n"
    assert has_line == (commit is not None or (point == "after_write" and outcome == "keep")), (point, outcome)
    set_brief(client, h, agent_id, "complete step" if step == 1 and has_line else APPEND)
    press(client, h, task["id"], "continue")
    second = runs_of(task["id"])[1]
    packet = packet_of(second["run_id"])
    # where the continuation starts: after discard, the newest checkpoint the app took; after keep, the kept head the
    # workspace reported
    assert packet["next"]["step"] == step
    assert packet["information"]["expect"]["head"] == (newest if outcome == "discard" else kept)
    if outcome == "discard":
        assert kept == newest
    row = settle(user, task["id"], lambda t: ended(t) or (t["state"], t["cause"]) == ("suspended", "step_done"))
    if step == 1:
        assert (row["state"], row["cause"], row["step"]) == ("suspended", "step_done", 2), row
        set_brief(client, h, agent_id, APPEND)
        press(client, h, task["id"], "continue")
        row = settle(user, task["id"], ended)
    assert row["state"] == "done", row
    # step 1's line is in the file once, and step 2's after it
    assert lines_at(w, agent_id, task["id"], step_head(task["id"], 1)) == ["line"]
    assert lines_at(w, agent_id, task["id"], step_head(task["id"], 2)) == ["line", "line"]


# ---- a push the machine stops under --------------------------------------------------------------------------------

def set_brief(client, h, agent_id, brief):
    r = client.put(f"{A}/{agent_id}", headers=h, json={"brief": brief})
    assert r.status_code == 200, r.text


def task_work(w, agent_id, task_id):
    return os.path.join(w.root, "agents", agent_id, "tasks", task_id, "work")


def lines_of(folder, name="lines.txt"):
    """The lines of a file in a task's worktree as they are now, or [] while it cannot be read."""
    try:
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            return fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def hold_first_push(w, hook):
    """The bare remote's `hook` (pre-receive or post-receive) holds the first push to reach it until the test lets it
    go: a held pre-receive then refuses it, so its ref never moves; later pushes go through. The marks (`reached` when
    one is held, `release` to let it go) are kept outside the remote, which a case moves away; a held hook lets go by
    itself after a minute, so that none outlives the test."""
    marks = os.path.join(str(w.tmp), "push-marks")
    os.makedirs(marks, exist_ok=True)
    path = os.path.join(w.remote, "hooks", hook)
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n"
                 f'if mkdir "{marks}/reached" 2>/dev/null; then\n'
                 "  i=0\n"
                 f'  while [ ! -e "{marks}/release" ] && [ "$i" -lt 600 ]; do sleep 0.1; i=$((i+1)); done\n'
                 + ("  exit 1\n" if hook == "pre-receive" else "")
                 + "fi\nexit 0\n")
    os.chmod(path, 0o755)
    return marks


def syncing_until(user, done, what, timeout=60):
    """The app's sync, round after round, until `done()` holds."""
    deadline = time.time() + timeout
    while not done():
        agents.sync(user)
        assert time.time() < deadline, what
        time.sleep(0.1)


def agent_paused(agent_id):
    with store.connect() as db:
        return bool(db.execute("SELECT paused FROM ade_agents WHERE id = ?", (agent_id,)).fetchone()[0])


def effects_of(task_id):
    """The task's effects as the app keeps them, their intents read."""
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_agent_effects WHERE task_id = ? ORDER BY created_at, rowid",
                          (task_id,)).fetchall()
    return [dict(r, intent=json.loads(r["intent"] or "{}")) for r in rows]


def packet_of(run_id):
    with store.connect() as db:
        got = db.execute("SELECT packet FROM ade_agent_packets WHERE run_id = ?", (run_id,)).fetchone()
    assert got is not None, f"run {run_id} has no packet"
    return json.loads(got[0])


def remote_ref(w, ref, remote=None):
    return git("ls-remote", remote or w.remote, ref, cwd=str(w.tmp)).partition("\t")[0]


# the step's command, by case: the one spelling the workspace reads as a push to settle by its ref, and the two it
# cannot, which are outward calls of their own (external). In "on_remote" the task's branch is on the remote before
# the push, at the commit before the one the task is cut from: the push would move it
# on, so the intent's value before is that commit, and a push held and refused there leaves the ref at it, not done
PUSHES = {"done": "git push origin {branch}", "not_done": "git push origin {branch}",
          "on_remote": "git push origin {branch}",
          "moved": "git push origin {branch}", "unreadable": "git push origin {branch}",
          "cd_sub": "mkdir -p sub ;; bash cd sub && git push origin {branch}",
          "delete": "git push --delete origin {branch}"}


@pytest.mark.parametrize("how", ["soft", "hard"])
@pytest.mark.parametrize("case", list(PUSHES))
def test_a_push_the_machine_stops_under_is_settled_at_the_next_start(client, provider, told, case, how):
    h, user, w = person(client, provider, "ana@example.com")
    a = add_account(w, "A", "a@example.com")
    confirm(client, h, user, a)
    agent_id = make_agent(client, h, a)
    task = make_task(client, h, agent_id, steps=[{"objective": "Push the task's branch", "check": "true"}])
    branch, ref = "pp/task-" + task["id"], "refs/heads/pp/task-" + task["id"]
    set_brief(client, h, agent_id, "bash " + PUSHES[case].format(branch=branch))
    placed = ""                         # the ref's value on the remote before the push: none but in these two cases
    if case == "delete":
        # the ref is on the remote first: a delete of a ref the remote lacks fails before any hook runs
        git("push", "-q", w.remote, f"main:{ref}", cwd=os.path.join(w.root, "repos", "demo"))
        placed = commit_of_main(w)
    if case == "on_remote":
        # the owner's main moves on by two commits, the task cut from the second at its start, and the task's branch
        # is on the remote at the first, which no other ref there holds (the remote's main is the commit before both):
        # the step's push is then a fast-forward, which reaches the remote's hook
        demo = os.path.join(w.root, "repos", "demo")
        commit_in(demo, "later.txt", "a commit on main\n")
        commit_in(demo, "later.txt", "another commit on main\n")
        git("push", "-q", w.remote, f"main~1:{ref}", cwd=demo)
        placed = git("rev-parse", "main~1", cwd=demo)
        refs = {}
        for line in git("ls-remote", w.remote, cwd=str(w.tmp)).splitlines():
            value, _, name = line.partition("\t")
            refs[name] = value
        assert refs["refs/heads/main"] == git("rev-parse", "main~2", cwd=demo)
        assert [name for name, value in refs.items() if value == placed] == [ref], refs
    before = remote_ref(w, ref)
    assert before == placed
    # step 1's push reaches the remote, its intent written before it ran, and is held there: the machine stops under
    # it (after the remote took the ref, its post-receive held, where it is done; before, its pre-receive held, else)
    marks = hold_first_push(w, "post-receive" if case == "done" else "pre-receive")
    press(client, h, task["id"], "start")
    syncing_until(user, lambda: os.path.exists(os.path.join(marks, "reached")), "the push did not reach the remote")
    commit = git("rev-parse", branch, cwd=task_work(w, agent_id, task["id"]))
    stop_machine(w, how)
    moved = None
    if case == "moved":
        # another push moves the remote's ref meanwhile
        other = os.path.join(str(w.tmp), "other")
        git("clone", "-q", w.remote, other, cwd=str(w.tmp))
        moved = commit_in(other, "other.txt", "another push\n")
        git("push", "-q", "origin", f"HEAD:{ref}", cwd=other)
        assert remote_ref(w, ref) == moved
    if case == "unreadable":
        os.rename(w.remote, w.remote + ".away")
    w.start()
    row = settle(user, task["id"], ended)
    # the attempt ended by the machine's stop: the task waits for the owner, its worktree first, and its agent is held
    assert (row["state"], row["cause"]) == ("needs_owner", "worktree"), row
    (run,) = runs_of(task["id"])
    assert (run["state"], run["cause"]) == ("blocked", "machine"), run
    assert agent_paused(agent_id)
    (effect,) = effects_of(task["id"])
    want = {"done": "done", "not_done": "not_done", "on_remote": "not_done"}.get(case, "unknown")
    if case in ("cd_sub", "delete"):
        assert (effect["kind"], effect["target"], effect["state"], effect["intent"]) == (
            "external", "git push", "unknown", {}), effect
    else:
        # the intent's value before is the ref as the remote had it ("" where it had none): in "on_remote", a commit
        assert (effect["kind"], effect["state"]) == ("git_remote", want), effect
        assert effect["intent"] == {"address": w.remote, "ref": ref, "commit": commit, "before": before}, effect
    # the held push let go (a held pre-receive refuses it), and the remote where the case left it: moved by the push
    # or by another, else as it was before the push
    open(os.path.join(marks, "release"), "w").close()
    if case == "unreadable":
        os.rename(w.remote + ".away", w.remote)
    assert remote_ref(w, ref) == {"done": commit, "moved": moved}.get(case, placed)
    # the owner keeps the worktree; an unknown push holds the task until the owner says whether it happened, and
    # nothing continues it meanwhile, by itself or by Continue
    set_brief(client, h, agent_id, "complete step")
    press(client, h, task["id"], "resolve", what="worktree", outcome="keep")
    assert client.post(f"{A}/{agent_id}/resume", headers=h).status_code == 200
    if want == "unknown":
        for _ in range(3):
            agents.sync(user)
        assert len(runs_of(task["id"])) == 1
        r = client.post(f"{T}/{task['id']}/continue", headers=h,
                        json={"state_version": task_of(client, h, task["id"])["state_version"]})
        assert r.status_code == 412 and "An effect of this task is unknown" in r.text, r.text
        said = "happened" if case in ("unreadable", "delete") else "not_happened"
        press(client, h, task["id"], "resolve", what="effect", outcome=said, effect_id=effect["id"])
        want = "done" if said == "happened" else "not_done"
    press(client, h, task["id"], "continue")
    # the next attempt's packet carries the push as settled: done or not done
    runs = runs_of(task["id"])
    assert len(runs) == 2
    listed = packet_of(runs[1]["run_id"])["information"]["effects"]
    other_list = "not_done" if want == "done" else "done"
    assert [(e["id"], e["kind"]) for e in listed[want]] == [(effect["id"], effect["kind"])], listed
    assert listed[other_list] == [], listed
    row = settle(user, task["id"], ended)
    assert row["state"] == "done", row


def commit_of_main(w):
    return git("rev-parse", "main", cwd=os.path.join(w.root, "repos", "demo"))


def _writes_into(folder):
    """Whether a file can be made in `folder` now."""
    probe = os.path.join(folder, ".probe")
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    os.close(fd)
    os.unlink(probe)
    return True


def commit_written(marks):
    """Whether the step's check has written its commit into the mark: a whole line of hexadecimal digits."""
    try:
        with open(os.path.join(marks, "commit"), encoding="ascii") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return False
    return len(text) > 1 and text.endswith("\n") and all(c in "0123456789abcdef" for c in text[:-1])


@contextlib.contextmanager
def read_only(folder):
    """`folder` read-only: by its mode, and where its owner writes into it anyway (root does), by the immutable flag
    as well (chattr +i)."""
    mode = os.stat(folder).st_mode & 0o7777
    os.chmod(folder, 0o555)
    flagged = False
    try:
        if _writes_into(folder):
            flagged = subprocess.run(["chattr", "+i", folder], capture_output=True).returncode == 0
        assert not _writes_into(folder), "the folder could not be made read-only"
        yield
    finally:
        if flagged:
            subprocess.run(["chattr", "-i", folder], check=True, capture_output=True)
        os.chmod(folder, mode)


def read_only_works(tmp):
    """Whether this machine can make a folder read-only to this process (read_only)."""
    folder = os.path.join(str(tmp), "read-only-probe")
    os.makedirs(folder, exist_ok=True)
    try:
        with read_only(folder):
            return True
    except (AssertionError, OSError, subprocess.CalledProcessError):
        return False


def test_an_intent_that_cannot_be_written_refuses_its_push(client, provider, told, tmp_path):
    if not read_only_works(tmp_path):
        pytest.skip("a folder cannot be made read-only to this process here (root, and no chattr +i)")
    h, user, w = person(client, provider, "ana@example.com")
    a = add_account(w, "A", "a@example.com")
    confirm(client, h, user, a)
    agent_id = make_agent(client, h, a)
    task = make_task(client, h, agent_id, steps=1)
    branch = "pp/task-" + task["id"]
    push = f"git push origin {branch}"
    set_brief(client, h, agent_id, f"bash echo line >> lines.txt ;; sleep 3 ;; bash {push} ;; sleep 3 ;; complete step")
    press(client, h, task["id"], "start")
    lines = os.path.join(task_work(w, agent_id, task["id"]), "lines.txt")
    syncing_until(user, lambda: os.path.exists(lines) and open(lines).read() == "line\n", "no line was written")
    # while the claude sleeps after the line, its attempt's record's folder is made read-only; the push's intent
    # cannot be written there, so the push is refused, and the folder is writable again before the step's end
    (run,) = runs_of(task["id"])
    folder = os.path.join(w.root, "agents", agent_id, "runs", run["run_id"])
    assert os.path.isfile(os.path.join(folder, "session.json")), folder

    def the_push():
        path = w.home / "stand-in-calls.jsonl"
        calls = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
        return [c for c in calls if c["name"] == "Bash" and c["input"].get("command") == push]
    with read_only(folder):
        deadline = time.time() + 30
        while not the_push():
            assert time.time() < deadline, "the push was not tried"
            time.sleep(0.05)
    (call,) = the_push()
    assert (call["refused"], call["ran"]) == ("hook", False), call
    row = settle(user, task["id"], ended)
    assert row["state"] == "done", row
    # refused for its intent, in those words; it never ran, so the remote has no such ref, and no effect is kept
    said = [p for p, _, files in os.walk(os.path.join(account_folder(w, a), "projects")) for f in files
            if f.endswith(".jsonl") and INTENT_FAILED in open(os.path.join(p, f)).read()]
    assert said, "the claude was not told the intent could not be written"
    assert remote_ref(w, "refs/heads/" + branch) == ""
    assert effects_of(task["id"]) == []


# ---- a task handed to a teammate -----------------------------------------------------------------------------------
# GitHub is played on this machine (B's _GitHub: git's smart HTTP on 127.0.0.1:443 for git alone, each user's token
# reaching the repositories it names); each user's workspace signed in there with its own token (signed_in_to), its
# repository `demo` with its origin there, so that the listing gives the task an address.

TM = "/api/ade/teammate"
REPO = "acme/demo"
ADDRESS = "https://github.com/acme/demo.git"
OPERATOR = "Example Hosting Ltd, privacy@hosting.example"
TOKENS = {"ana": "gho_" + "1" * 36, "bo": "gho_" + "2" * 36}


@pytest.fixture
def github(tmp_path):
    g = _GitHub(tmp_path / "github", {TOKENS["ana"]: ("ana", (REPO,)), TOKENS["bo"]: ("bo", (REPO,))})
    yield g
    g.close()


@pytest.fixture
def teammates(monkeypatch, provider):
    """The teammate part on, its operator named, its rounds run by the tests themselves (task_offers.drive)."""
    monkeypatch.setenv("OPEN_EDITION_OPERATOR", OPERATOR)
    for name in ("TEAMMATE", "OFFER_HOLD", "CROSSING_HOLD"):
        monkeypatch.delenv("OPEN_EDITION_ADE_TASK_" + name, raising=False)
    monkeypatch.setattr(task_offers, "_kick", lambda: None)
    monkeypatch.setattr(task_offers, "_keep_running", lambda: None)
    task_offers._seen.clear()
    task_offers._shown.clear()
    return provider


def on_github(w, github, login):
    """The workspace's `demo` on the played GitHub: the first user's pushed there as the repository, the other's made
    the same history; its origin the repository's https address; the user signed in with their own token."""
    demo = os.path.join(w.root, "repos", "demo")
    bare = str(github.folder / "repos" / f"{REPO}.git")
    if not os.path.isdir(bare):
        github.repository(REPO, demo)
    else:
        git("fetch", "-q", bare, "main", cwd=demo)
        git("reset", "-q", "--hard", "FETCH_HEAD", cwd=demo)
    git("remote", "set-url", "origin", ADDRESS, cwd=demo)
    signed_in_to(w, github, login, TOKENS[login])
    return bare


def rounds(*users, after=200):
    """The holds of each user whose time has come, `after` seconds from now, applied; then a round of the teammate
    part's."""
    now = int(time.time()) + after
    for user in users:
        holds.settle(user["id"], now)
    task_offers.drive(now)
    return now


def received(client, h):
    return client.get(f"{TM}/offers", headers=h).json()["received"]


def offer(client, h, user, task_id, address, confirm_it=True):
    """An offer made and confirmed through the routes, as the page makes it: its draft, then its confirmation."""
    version = task_of(client, h, task_id)["state_version"]
    r = client.post(f"{TM}/offers", headers=h, json={"task_id": task_id, "state_version": version, "address": address})
    assert r.status_code == 200, r.text
    made = r.json()["offer"]
    if confirm_it:
        confirmation = passkeys.confirm(user, password=PASSWORD, subject=f"task-offer:{made['id']}")["confirmation"]
        r = client.post(f"{TM}/offers/{made['id']}/confirm", headers=h, json={"confirmation": confirmation})
        assert r.status_code == 200, r.text
    return made["id"]


def offer_row(offer_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_agent_task_offers WHERE id = ?", (offer_id,)).fetchone())


def git_calls(github, since=0):
    """The played GitHub's answered git calls since `since`: (push or read, the repository, the user), a read being
    a listing of refs or a fetch."""
    out = []
    for s in github.seen[since:]:
        if s["status"] == 200:
            repo = s["path"].split("/info/refs")[0].split("/git-")[0]
            out.append(("push" if s["path"].endswith("/git-receive-pack") else "read", repo, s["user"]))
    return out


def pushes(github, since=0):
    return [(repo, user) for kind, repo, user in git_calls(github, since) if kind == "push"]


def readers(github, since=0):
    return {user for kind, _, user in git_calls(github, since) if kind == "read"}


def keep_own(client, h, user, agent_id, key, memory, brief):
    """What a user keeps of their own, given through the app: a model key to their workspace (the page's settings),
    and a memory to their agent, written by a run of the agent's own (Claude Code's auto memory, as the stand-in writes
    it on "memory write") and copied to the app; the agent's brief then put back as `brief`."""
    r = client.put("/api/ade/workspace/model-providers/anthropic", headers=h, json={"key": key})
    assert r.status_code == 200, r.text
    set_brief(client, h, agent_id, f"memory write MEMORY.md={memory}")
    r = client.post(f"{A}/{agent_id}/runs", headers=h, json={})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    def over():
        with store.connect() as db:
            got = db.execute("SELECT state FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        return got is not None and got[0] in ("done", "failed", "blocked", "stopped", "skipped")
    syncing_until(user, over, "the agent's own run did not end")
    assert run_state(run_id) == "done"
    agents.sync(user)                                   # its memory copied, after its record
    set_brief(client, h, agent_id, brief)


def pair(client, provider, github, ana_brief=APPEND, ana_excludes=None, before_start=None,
         first_end=("suspended", "step_done"), kept=None, **fields):
    """Ana's task on Ana's account (of the task `fields` given, else two steps; its agent's brief `ana_brief`, and
    Ana's own git excludes `ana_excludes` where given), its step 1 done and the task waiting after it (Between steps:
    wait; or as `first_end` says, a state and a cause), its address recorded; Bo, a teammate, with an account of his
    own confirmed and an agent on the same repository: (ana, bo), each {h, user, w, account, agent}, and the task.
    `before_start`, where given, is called with Ana's workspace once the task is made and before it starts. `kept`,
    where given ({"ana": (key, memory), "bo": (key, memory)}), is what each keeps of their own (keep_own), given as
    soon as their agent is made; Bo is then made before Ana's task starts, so that each packet the app makes is made
    once both have theirs."""

    def teammate():
        hb, bo, wb = person(client, provider, "bo@example.com")
        on_github(wb, github, "bo")
        account_b = add_account(wb, "B", "bo@example.com")
        confirm(client, hb, bo, account_b)
        agent_b = make_agent(client, hb, account_b)
        if kept is not None:
            keep_own(client, hb, bo, agent_b, *kept["bo"], brief=APPEND)
        return {"h": hb, "user": bo, "w": wb, "account": account_b, "agent": agent_b}

    ha, ana, wa = person(client, provider, "ana@example.com")
    on_github(wa, github, "ana")
    if ana_excludes is not None:
        os.makedirs(os.path.join(str(wa.home), ".config", "git"), exist_ok=True)
        with open(os.path.join(str(wa.home), ".config", "git", "ignore"), "w") as fh:
            fh.write(ana_excludes)
    account_a = add_account(wa, "A", "ana@example.com")
    confirm(client, ha, ana, account_a)
    agent_a = make_agent(client, ha, account_a, brief=ana_brief)
    bo = None
    if kept is not None:
        keep_own(client, ha, ana, agent_a, *kept["ana"], brief=ana_brief)
        bo = teammate()
    task = make_task(client, ha, agent_a, **dict({"between_steps": "wait"}, **fields))
    assert task_row(task["id"])["repo_address"] == ADDRESS and task_row(task["id"])["repo_slug"] == REPO
    if before_start is not None:
        before_start(wa)
    press(client, ha, task["id"], "start")
    row = settle(ana, task["id"], lambda t: t["state"] in ("suspended", "needs_owner") or ended(t))
    assert (row["state"], row["cause"], row["step"]) == (*first_end, 2), row
    return {"h": ha, "user": ana, "w": wa, "account": account_a, "agent": agent_a}, bo or teammate(), task


def test_an_offer_is_held_pushed_by_its_recorded_commit_accepted_and_run_by_the_teammate(client, teammates, told,
                                                                                         github):
    ana, bo, task = pair(client, teammates, github)
    step1 = step_head(task["id"], 1)
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    assert task_row(task["id"])["state"] == "offering"
    # inside the hold nothing is pushed
    rounds(ana["user"], bo["user"], after=0)
    assert git_calls(github) == [] and offer_row(offer_id)["state"] == "held"
    # past it, Ana's workspace pushes the commit the app recorded, by its hash, with Ana's own sign-in, to the task's
    # branch at the address recorded when the task was made; and the round reads it in Bo's workspace, with Bo's
    rounds(ana["user"], bo["user"])
    assert pushes(github) == [("/" + REPO + ".git", "ana")] and readers(github) == {"ana", "bo"}
    assert {repo for _, repo, _ in git_calls(github)} == {"/" + REPO + ".git"}
    made = offer_row(offer_id)
    assert (made["state"], made["commit"], made["repo_address"]) == ("offered", step1, ADDRESS)
    assert git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=github.folder / "repos" / f"{REPO}.git"
               ) == step1
    assert "A teammate offered you a task" in [m.title for u, m in told if u == bo["user"]["id"]]
    # Bo's workspace reads what a next run would load; Bo sees the offer, and accepts it into his agent, adopting
    # step 2's check
    task_offers.drive()
    (seen,) = received(client, bo["h"])
    assert seen["id"] == offer_id and seen["commit"] == step1 and seen["from_email"] == "ana@example.com"
    since = len(github.seen)
    r = client.post(f"{TM}/offers/{offer_id}/accept", headers=bo["h"], json={"agent_id": bo["agent"],
                                                                            "adopt_checks": [2]})
    assert r.status_code == 200, r.text
    theirs = r.json()["task_id"]
    # fetched by its hash with Bo's own sign-in, and Bo's task starts from the offered commit
    assert pushes(github, since) == [] and readers(github, since) <= {"bo"}
    assert task_row(theirs)["checkpoint0"] == step1 and task_row(theirs)["user_id"] == bo["user"]["id"]
    assert task_row(task["id"])["state"] == "handed_over"
    # Bo's task keeps Ana's step 1 done and is at step 2
    assert task_row(theirs)["step"] == 2
    # Bo starts it: step 2 on Bo's account, in Bo's workspace, from Ana's step 1
    press(client, bo["h"], theirs, "start")
    row = settle(bo["user"], theirs, ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"],
                                    [(r["task_step"], r["state"], r["cause"], r["reason"]) for r in runs_of(theirs)])
    assert lines_at(bo["w"], bo["agent"], theirs, step_head(theirs, 2)) == ["line", "line"]
    (attempt,) = runs_of(theirs)
    assert json.loads(attempt["backend"]) == {"kind": "account", "id": bo["account"]} and attempt["task_step"] == 2
    packet = packet_of(attempt["run_id"])
    instructions = packet["instructions"]
    assert instructions["by"] == "teammate" and instructions["from"] == "ana@example.com", instructions
    assert packet["information"]["handed"]["done_steps"] == [1], packet["information"]
    # each ran on its own: Bo's attempt in Bo's account folder, none in Ana's workspace after the hand-off
    assert attempt_folders(bo["w"])[-1] == account_folder(bo["w"], bo["account"])
    # and in the one worktree the acceptance made, under Bo's task's own id, from the offered commit
    assert os.listdir(os.path.join(bo["w"].root, "agents", bo["agent"], "tasks")) == [theirs]
    assert git("rev-parse", f"{step_head(theirs, 2)}^", cwd=os.path.join(bo["w"].root, "repos", "demo")) == step1


def test_an_offer_whose_commit_changes_a_file_a_next_run_would_load_is_accepted_only_once_seen(
        client, teammates, told, github):
    """The acceptance's fetch once compared the offered commit with itself, so the files a next run would load that it
    changes were always none there, and an acceptance went through whether or not Bo had been shown them. Ana's step 1
    adds a hook in .claude/settings.json (as custody's hook test adds it, her watch lifted); Bo's acceptance before he
    has read his offers is refused, his next read shows the file, and the acceptance after it goes through."""
    ana, bo, task = pair(client, teammates, github, ana_brief=HOOK + " ;; " + APPEND,
                         first_end=("needs_owner", "watch"))
    press(client, ana["h"], task["id"], "resolve", what="watch", outcome="lift")
    assert client.post(f"{A}/{ana['agent']}/resume", headers=ana["h"]).status_code == 200
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "offered", offer_row(offer_id)["why"]
    task_offers.drive()
    r = client.post(f"{TM}/offers/{offer_id}/accept", headers=bo["h"], json={"agent_id": bo["agent"],
                                                                            "adopt_checks": []})
    assert r.status_code == 409 and r.json()["detail"] == task_offers.SHOWN_OTHERS, r.text
    assert tasks_of(bo["user"]) == []
    (seen,) = received(client, bo["h"])
    assert seen["loaded_changes"] == [".claude/settings.json"], seen
    r = client.post(f"{TM}/offers/{offer_id}/accept", headers=bo["h"], json={"agent_id": bo["agent"],
                                                                            "adopt_checks": []})
    assert r.status_code == 200, r.text
    assert task_row(r.json()["task_id"])["checkpoint0"] == step_head(task["id"], 1)


def offered_to_bo(client, teammates, github):
    """An offer, past its hold, pushed and waiting for Bo: (ana, bo, task, offer id)."""
    ana, bo, task = pair(client, teammates, github)
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "offered", offer_row(offer_id)["why"]
    return ana, bo, task, offer_id


def test_an_offer_without_its_confirmation_is_refused_and_nothing_is_pushed(client, teammates, told, github):
    """The offer's draft confirmed with no confirmation, and with one made for another offer: each refused,
    the task left as it waited, nothing held or pushed."""
    ana, bo, task = pair(client, teammates, github)
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com", confirm_it=False)
    other = passkeys.confirm(ana["user"], password=PASSWORD, subject="task-offer:" + "f" * 32)["confirmation"]
    for given in (None, other):
        r = client.post(f"{TM}/offers/{offer_id}/confirm", headers=ana["h"], json={"confirmation": given})
        assert r.status_code == 403, r.text
    assert offer_row(offer_id)["state"] == "draft" and offer_row(offer_id)["hold_id"] is None
    rounds(ana["user"], bo["user"])
    assert (task_row(task["id"])["state"], task_row(task["id"])["cause"]) == ("suspended", "step_done")
    assert git_calls(github) == []


@pytest.mark.parametrize("end", ["declined", "withdrawn", "expired"])
def test_an_offer_declined_withdrawn_or_expired_leaves_its_branch_and_puts_the_task_back(client, teammates, told,
                                                                                         github, end):
    """An offer Bo declines, Ana withdraws after its hold, or that nobody answers in 7 days (an injected
    clock): the branch stays on the remote, Ana's task is back as it waited before, and the offer is no longer Bo's,
    nor names him or the typed address."""
    ana, bo, task, offer_id = offered_to_bo(client, teammates, github)
    assert [o["id"] for o in received(client, bo["h"])] == [offer_id]
    if end == "declined":
        r = client.post(f"{TM}/offers/{offer_id}/decline", headers=bo["h"])
        assert r.status_code == 200, r.text
    elif end == "withdrawn":
        r = client.post(f"{TM}/offers/{offer_id}/withdraw", headers=ana["h"])
        assert r.status_code == 200, r.text
    else:
        # pushed at the round's clock (200 s ahead), so it waits OFFER_DAYS from then; a second before that it still
        # waits, and at that second the scheduler's round ends it
        until = offer_row(offer_id)["expires_at"]
        assert int(time.time()) + task_offers.OFFER_DAYS * 86400 < until <= \
            int(time.time()) + 200 + task_offers.OFFER_DAYS * 86400, until - int(time.time())
        for at in (until - 1, until):
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                task_offers.forget(db, at)
            if at < until:
                assert offer_row(offer_id)["state"] == "offered"
    gone = offer_row(offer_id)
    assert (gone["state"], gone["teammate_user"], gone["address"]) == (end, None, None)
    bare = github.folder / "repos" / f"{REPO}.git"
    assert git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=bare) == step_head(task["id"], 1)
    back = task_row(task["id"])
    assert (back["state"], back["cause"]) == ("suspended", "step_done"), (back["state"], back["cause"], back["reason"])
    assert received(client, bo["h"]) == [] and tasks_of(bo["user"]) == []


def test_an_acceptance_whose_sign_in_cannot_read_the_repository_is_refused_and_the_task_goes_back(
        client, teammates, told, tmp_path):
    """Bo's GitHub sign-in cannot read the task's repository (the played GitHub decides): his acceptance is
    refused, nothing is made on his side, and Ana's task is back as it waited."""
    github = _GitHub(tmp_path / "github", {TOKENS["ana"]: ("ana", (REPO,)), TOKENS["bo"]: ("bo", ())})
    try:
        ana, bo, task, offer_id = offered_to_bo(client, teammates, github)
        task_offers.drive()
        r = client.post(f"{TM}/offers/{offer_id}/accept", headers=bo["h"], json={"agent_id": bo["agent"],
                                                                                "adopt_checks": []})
        assert r.status_code == 412, r.text
        gone = offer_row(offer_id)
        assert (gone["state"], gone["why"]) == ("failed", task_offers.UNREADABLE) and tasks_of(bo["user"]) == []
        back = task_row(task["id"])
        assert (back["state"], back["cause"]) == ("suspended", "step_done"), (back["state"], back["reason"])
        # no worktree was made for a task of Bo's: his agent's tasks folder is absent or empty
        tasks_dir = os.path.join(bo["w"].root, "agents", bo["agent"], "tasks")
        assert not os.path.isdir(tasks_dir) or os.listdir(tasks_dir) == [], os.listdir(tasks_dir)
    finally:
        github.close()


def test_a_branch_moved_during_the_hold_holds_the_offer_for_its_owner_and_nothing_is_pushed(client, teammates,
                                                                                            told, github):
    """During the hold another commit lands on the task's branch in Ana's worktree; past the hold the push
    finds the branch moved, nothing is pushed, and the offer ends holding the task for Ana."""
    ana, bo, task = pair(client, teammates, github)
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    commit_in(task_work(ana["w"], ana["agent"], task["id"]), "other.txt", "moved\n", message="another run")
    rounds(ana["user"], bo["user"])
    assert pushes(github) == []
    ended_ = offer_row(offer_id)
    assert (ended_["state"], ended_["why"]) == ("failed", task_offers.PUSH_HELD)
    back = task_row(task["id"])
    assert (back["state"], back["cause"]) == ("needs_owner", "handoff"), (back["state"], back["reason"])
    bare = github.folder / "repos" / f"{REPO}.git"
    assert git("for-each-ref", f"refs/heads/pp/task-{task['id']}", cwd=bare) == ""


ELSEWHERE = "https://github.com/acme/elsewhere.git"


def origin_elsewhere(w):
    git("remote", "set-url", "origin", ELSEWHERE, cwd=os.path.join(w.root, "repos", "demo"))


@pytest.mark.parametrize("when", ["before_start", "between_attempts"])
def test_an_offer_goes_to_the_address_recorded_when_the_task_was_made_whatever_origin_says_now(client, teammates,
                                                                                               told, github, when):
    """The repository's origin changed after the task was made. Changed before the task's first attempt, it
    is the repository's config as the task's watch first sees it, and the push goes to the address the task recorded,
    never to origin, the offer carrying that address. Changed between its attempts, the repository's config is a
    watched place that changed, so the watch holds the push: nothing is pushed or read anywhere, and the
    offer ends holding the task for Ana."""
    ana, bo, task = pair(client, teammates, github, before_start=origin_elsewhere if when == "before_start" else None)
    if when == "between_attempts":
        origin_elsewhere(ana["w"])
    assert git("remote", "get-url", "origin", cwd=os.path.join(ana["w"].root, "repos", "demo")) == ELSEWHERE
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    rounds(ana["user"], bo["user"])
    made = offer_row(offer_id)
    assert not any("elsewhere" in s["path"] for s in github.seen), github.seen
    if when == "before_start":
        assert pushes(github) == [("/" + REPO + ".git", "ana")], (made["state"], made["why"])
        assert (made["state"], made["repo_address"]) == ("offered", ADDRESS), (made["state"], made["why"])
        assert task_row(task["id"])["repo_address"] == ADDRESS
        return
    assert (made["state"], made["why"]) == ("failed", task_offers.PUSH_HELD) and git_calls(github) == []
    back = task_row(task["id"])
    assert (back["state"], back["cause"]) == ("needs_owner", "handoff"), (back["state"], back["reason"])


def test_a_task_with_no_remote_cannot_be_offered(client, teammates, told, github):
    """A task made in a repository with no remote has no recorded address, and its offer is refused, saying
    why."""
    ha, ana, wa = person(client, teammates, "ana@example.com")
    on_github(wa, github, "ana")
    git("remote", "remove", "origin", cwd=os.path.join(wa.root, "repos", "demo"))
    account = add_account(wa, "A", "ana@example.com")
    confirm(client, ha, ana, account)
    task = make_task(client, ha, make_agent(client, ha, account), between_steps="wait")
    assert task_row(task["id"])["repo_address"] is None
    press(client, ha, task["id"], "start")
    assert settle(ana, task["id"], lambda t: t["state"] == "suspended" or ended(t))["cause"] == "step_done"
    person(client, teammates, "bo@example.com")
    version = task_of(client, ha, task["id"])["state_version"]
    r = client.post(f"{TM}/offers", headers=ha, json={"task_id": task["id"], "state_version": version,
                                                       "address": "bo@example.com"})
    assert r.status_code == 412 and r.json()["detail"] == task_offers.NO_ADDRESS, r.text
    assert git_calls(github) == []


def handed_to_bo(client, teammates, github, adopt=(2,), **fields):
    """The hand-off, up to Bo's acceptance: Ana's step 1 done (of the task `fields` given) and offered to Bo, who
    accepts it into his agent, adopting the owner's checks of the steps `adopt` names. (ana, bo, task, offer id, Bo's
    task id, step 1's head)."""
    ana, bo, task = pair(client, teammates, github, **fields)
    step1 = step_head(task["id"], 1)
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    rounds(ana["user"], bo["user"])
    task_offers.drive()
    r = client.post(f"{TM}/offers/{offer_id}/accept", headers=bo["h"], json={"agent_id": bo["agent"],
                                                                            "adopt_checks": list(adopt)})
    assert r.status_code == 200, r.text
    return ana, bo, task, offer_id, r.json()["task_id"], step1


def test_the_teammate_s_work_is_pushed_back_to_the_owner_s_branch_and_checked_by_the_owner(client, teammates, told,
                                                                                         github):
    """The teammate's task done, end to end: their workspace pushes their work, by its hash, with their
    own sign-in, to the owner's task's branch; the way back fetches it
    with the owner's sign-in, into the owner's task's branch, runs the owner's own check of the step done there in the
    owner's workspace (the owner never ran an attempt of it, so the app gives the check), and the owner's task is done,
    checked, the owner told."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    press(client, bo["h"], theirs, "start")
    row = settle(bo["user"], theirs, ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
    step2 = step_head(theirs, 2)
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    # Bo's work on the owner's branch, pushed with Bo's sign-in; then read with Ana's
    assert pushes(github, since) == [("/" + REPO + ".git", "bo")] and "ana" in readers(github, since)
    bare = github.folder / "repos" / f"{REPO}.git"
    assert git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=bare) == step2
    assert offer_row(offer_id)["pushed"] == step2 and offer_row(offer_id)["state"] == "returned"
    # Ana's task: its branch at Bo's step 2, step 2 done by Ana's own check, the task done
    owner = task_row(task["id"])
    assert owner["state"] == "done", (owner["state"], owner["cause"], owner["reason"])
    with store.connect() as db:
        steps = [tuple(s) for s in db.execute("SELECT n, state, evidence FROM ade_agent_task_steps WHERE task_id = ? "
                                              "ORDER BY n", (task["id"],)).fetchall()]
    assert steps == [(1, "done", "checked"), (2, "done", "checked")]
    work = task_work(ana["w"], ana["agent"], task["id"])
    assert git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=work) == step2
    assert lines_at(ana["w"], ana["agent"], task["id"], step2) == ["line", "line"]
    assert "A task of yours came back from your teammate" in [m.title for u, m in told if u == ana["user"]["id"]]


def came_back(owner, task_id, head, state="suspended"):
    """The owner's task back from the teammate, waiting for its owner at step 2 (in `state`), its branch at `head` in
    the owner's worktree: its row."""
    row = task_row(task_id)
    assert (row["state"], row["step"]) == (state, 2), (row["state"], row["cause"], row["reason"])
    with store.connect() as db:
        assert db.execute("SELECT state FROM ade_agent_task_steps WHERE task_id = ? AND n = 2",
                          (task_id,)).fetchone()[0] == "todo"
    work = task_work(owner["w"], owner["agent"], task_id)
    assert git("rev-parse", f"refs/heads/pp/task-{task_id}", cwd=work) == head
    return row


def owner_steps(task_id):
    with store.connect() as db:
        return [tuple(s) for s in db.execute("SELECT n, state, evidence FROM ade_agent_task_steps WHERE task_id = ? "
                                             "ORDER BY n", (task_id,)).fetchall()]


def test_a_returned_step_with_no_check_of_the_owner_s_comes_back_said_done_and_the_task_waits(client, teammates,
                                                                                               told, github):
    """A returned step with no check of the owner's: Bo completes the owner's step 2, which has none, and his
    task is done. It comes back "said done", the model's word on another person's side, and the owner's task waits
    after it (Between steps: wait), its branch at Bo's work, instead of ending done."""
    steps = [{"objective": "Append line 1", "check": 'test "$(wc -l < lines.txt)" -eq 1'},
             {"objective": "Append line 2"}]
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=(), steps=steps)
    press(client, bo["h"], theirs, "start")
    row = settle(bo["user"], theirs, ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
    step2 = step_head(theirs, 2)
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned" and offer_row(offer_id)["pushed"] == step2
    owner = task_row(task["id"])
    assert (owner["state"], owner["cause"]) == ("suspended", "step_done"), (owner["state"], owner["reason"])
    assert owner_steps(task["id"]) == [(1, "done", "checked"), (2, "done", "said_done")]
    work = task_work(ana["w"], ana["agent"], task["id"])
    assert git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=work) == step2


def test_returned_work_that_touches_a_protected_path_waits_for_the_owner(client, teammates, told, github):
    """A returned change that edits a protected path: the owner's task protects keep.txt, and Bo's work changes
    it. On Bo's side the step cannot complete over it (a progress checkpoint is left); his work comes back touching the
    protected path, no step of his is taken as done, and the owner's task needs its owner."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=(), protected=["keep.txt"])
    set_brief(client, bo["h"], bo["agent"], "bash echo x >> keep.txt ;; bash echo line >> lines.txt ;; complete step")
    press(client, bo["h"], theirs, "start")
    settle(bo["user"], theirs, ended)
    assert owner_steps(theirs)[1][1] != "done"
    rounds(ana["user"], bo["user"])
    pushed = offer_row(offer_id)["pushed"]
    assert pushed and pushed != step1
    r = client.post(f"{TM}/tasks/{task['id']}/take-back", headers=ana["h"])
    assert r.status_code == 200, r.text
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned"
    assert came_back(ana, task["id"], pushed, state="needs_owner")["cause"] == "returned"
    assert owner_steps(task["id"]) == [(1, "done", "checked"), (2, "todo", None)]
    assert lines_at(ana["w"], ana["agent"], task["id"], pushed) == ["line", "line"]


def test_work_that_leaves_the_handed_history_is_neither_saved_nor_pushed(client, teammates, told, github):
    """A returned commit that does not descend from the handed one, as it can arise end to end: the owner's
    task's branch is gone from the remote, so a push of such a commit would make it anew, and Bo's attempt moves his
    branch back past the handed commit before it saves. His own workspace refuses it first: no checkpoint is taken, the
    attempt ends held for him, and nothing is pushed; Take back brings back the handed commit alone, no step of his
    taken. The app's own refusal of such a commit (the fetch's 409) is test_task_offers.py's, with a workspace that
    answers it."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    bare = github.folder / "repos" / f"{REPO}.git"
    git("update-ref", "-d", f"refs/heads/pp/task-{task['id']}", cwd=bare)
    set_brief(client, bo["h"], bo["agent"], "bash git reset -q --hard HEAD~1 ;; bash echo other > other.txt ;; "
                                            "save other ;; complete step")
    press(client, bo["h"], theirs, "start")
    row = settle(bo["user"], theirs, ended)
    assert (row["state"], row["cause"]) == ("needs_owner", "worktree"), row
    (run,) = runs_of(theirs)
    assert run["state"] == "blocked" and "left the task's branch or its history" in run["reason"], run
    assert checkpoints_taken(theirs) == []
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["pushed"] is None and pushes(github, since) == []
    r = client.post(f"{TM}/tasks/{task['id']}/take-back", headers=ana["h"])
    assert r.status_code == 200, r.text
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned" and pushes(github, since) == []
    came_back(ana, task["id"], step1)
    assert owner_steps(task["id"]) == [(1, "done", "checked"), (2, "todo", None)]


def test_take_back_during_an_attempt_stops_it_and_brings_its_work_back(client, teammates, told, github):
    """Take back during an attempt: Bo's attempt, which saved its work and works on, is stopped as Stop
    stops it; its work is pushed with Bo's sign-in to the owner's task's branch and comes back to Ana's task, whose
    step 2 it did not complete, so Ana's task waits for her."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    set_brief(client, bo["h"], bo["agent"], "bash echo line >> lines.txt ;; save half ;; sleep 60")
    press(client, bo["h"], theirs, "start")
    settle(bo["user"], theirs, lambda t: bool(checkpoints_taken(theirs)))
    half = checkpoints_taken(theirs)[-1]["manifest"]["head"]
    since = len(github.seen)
    r = client.post(f"{TM}/tasks/{task['id']}/take-back", headers=ana["h"])
    assert r.status_code == 200 and r.json()["task"]["state"] == "returning", r.text
    rounds(ana["user"], bo["user"])                     # Bo's attempt stopped
    settle(bo["user"], theirs, lambda t: t["lease_run"] is None)
    assert [x["state"] for x in runs_of(theirs)] == ["stopped"]
    rounds(ana["user"], bo["user"])                     # its work pushed, then fetched back
    assert pushes(github, since) == [("/" + REPO + ".git", "bo")] and offer_row(offer_id)["state"] == "returned"
    bare = github.folder / "repos" / f"{REPO}.git"
    assert git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=bare) == half
    came_back(ana, task["id"], half)
    assert lines_at(ana["w"], ana["agent"], task["id"], half) == ["line", "line"]


def test_take_back_with_the_teammate_s_machine_stopped_brings_back_what_was_pushed(client, teammates, told,
                                                                                 github):
    """Take back with the teammate's machine stopped and work there after its last push: Bo's attempt
    saved its work and ended, and the round pushed it; a second attempt saved more and worked on, and Bo's machine
    stopped under it. Take back comes back at once with what was pushed, nothing stopped or pushed on Bo's side, and
    the second attempt's work, never pushed, does not come back."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    set_brief(client, bo["h"], bo["agent"], "bash echo line >> lines.txt ;; save half")
    press(client, bo["h"], theirs, "start")
    row = settle(bo["user"], theirs, ended)
    assert (row["state"], row["cause"]) == ("suspended", "no_step"), row
    half = checkpoints_taken(theirs)[-1]["manifest"]["head"]
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["pushed"] == half
    set_brief(client, bo["h"], bo["agent"], "bash echo more >> lines.txt ;; save more ;; sleep 60")
    press(client, bo["h"], theirs, "continue")
    settle(bo["user"], theirs, lambda t: len(checkpoints_taken(theirs)) == 2)
    more = checkpoints_taken(theirs)[-1]["manifest"]["head"]
    assert more != half
    rounds(ana["user"], bo["user"])                     # nothing is pushed while the attempt works
    assert offer_row(offer_id)["pushed"] == half
    stop_machine(bo["w"], "hard")
    teammates.running.pop(bo["user"]["id"])
    since = len(github.seen)
    r = client.post(f"{TM}/tasks/{task['id']}/take-back", headers=ana["h"])
    assert r.status_code == 200, r.text
    rounds(ana["user"], bo["user"])
    assert pushes(github, since) == [] and offer_row(offer_id)["state"] == "returned"
    came_back(ana, task["id"], half)
    assert lines_at(ana["w"], ana["agent"], task["id"], half) == ["line", "line"]


@pytest.mark.parametrize("way", ["stopped", "retired", "removed"])
def test_the_teammate_s_side_ending_brings_back_what_it_pushed(client, teammates, told, github, way):
    """The teammate's task stopped, its agent retired, its user removed by an admin (each: back,
    `suspended`, from the way back): the round takes the way back by itself, and the owner's task comes back with the
    work the teammate's side pushed, waiting for its owner at the step the teammate did not complete. Stopped is the
    teammate's End: their Stop alone leaves their task waiting, theirs to go on with, and takes nothing back."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    set_brief(client, bo["h"], bo["agent"],
              "bash echo line >> lines.txt ;; save half" + (" ;; sleep 60" if way == "stopped" else ""))
    press(client, bo["h"], theirs, "start")
    if way == "stopped":
        settle(bo["user"], theirs, lambda t: bool(checkpoints_taken(theirs)))
        press(client, bo["h"], theirs, "stop")
        row = settle(bo["user"], theirs, lambda t: t["lease_run"] is None)
        assert (row["state"], row["cause"]) == ("suspended", "stopped"), row
    else:
        row = settle(bo["user"], theirs, ended)
        assert (row["state"], row["cause"]) == ("suspended", "no_step"), row
    half = checkpoints_taken(theirs)[-1]["manifest"]["head"]
    # the round pushes Bo's work, with Bo's sign-in, and takes nothing back yet
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    assert pushes(github, since) == [("/" + REPO + ".git", "bo")]
    assert offer_row(offer_id)["pushed"] == half and task_row(task["id"])["state"] == "handed_over"
    if way == "stopped":
        press(client, bo["h"], theirs, "end", outcome="stopped")
        assert task_row(theirs)["state"] == "stopped"
    if way == "retired":
        assert client.post(f"{A}/{bo['agent']}/retire", headers=bo["h"]).status_code == 200
    if way == "removed":
        with store.connect() as db:
            db.execute("DELETE FROM users WHERE id = ?", (bo["user"]["id"],))
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned" and pushes(github, since) == []
    came_back(ana, task["id"], half)
    assert lines_at(ana["w"], ana["agent"], task["id"], half) == ["line", "line"]
    assert "A task of yours came back from your teammate" in [m.title for u, m in told if u == ana["user"]["id"]]


def test_a_returned_step_whose_owner_s_check_fails_is_reopened(client, teammates, told, github):
    """Bo completes step 2 (it has no check on his side, none adopted) with a third line; the owner's own
    check of step 2, two lines, run in the owner's workspace on the returned commit, fails: the step is reopened, and
    the owner's task waits for the owner, its branch at Bo's work."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    set_brief(client, bo["h"], bo["agent"],
              "bash echo line >> lines.txt ;; bash echo more >> lines.txt ;; complete step")
    press(client, bo["h"], theirs, "start")
    row = settle(bo["user"], theirs, ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
    step2 = step_head(theirs, 2)
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned" and offer_row(offer_id)["pushed"] == step2
    came_back(ana, task["id"], step2)
    assert lines_at(ana["w"], ana["agent"], task["id"], step2) == ["line", "line", "more"]


def test_take_back_with_an_unknown_effect_brings_it_and_the_owner_s_task_does_not_go_on(client, teammates, told,
                                                                                      github):
    """Take back with an unknown effect: Take back stops Bo's attempt while a command it cannot place runs,
    so its effect is unknown; the way back brings it to the owner's task, which does not go on until the owner says
    whether it happened."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    marks = os.path.join(str(bo["w"].tmp), "held-marks")
    os.makedirs(marks, exist_ok=True)
    set_brief(client, bo["h"], bo["agent"], f'bash echo line >> lines.txt ;; save half ;; bash touch "{marks}/started"; '
              + held_command(marks))
    press(client, bo["h"], theirs, "start")
    syncing_until(bo["user"], lambda: os.path.exists(os.path.join(marks, "started")), "the command did not start")
    settle(bo["user"], theirs, lambda t: bool(checkpoints_taken(theirs)))
    half = checkpoints_taken(theirs)[-1]["manifest"]["head"]
    r = client.post(f"{TM}/tasks/{task['id']}/take-back", headers=ana["h"])
    assert r.status_code == 200, r.text
    try:
        rounds(ana["user"], bo["user"])                 # Bo's attempt stopped, its command in flight
        settle(bo["user"], theirs, lambda t: t["lease_run"] is None)
    finally:
        open(os.path.join(marks, "release"), "w").close()
    assert [e["state"] for e in effects_of(theirs)] == ["unknown"], effects_of(theirs)
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned"
    assert came_back(ana, task["id"], half, state="needs_owner")["cause"] == "effect"
    assert [e["state"] for e in effects_of(task["id"])] == ["unknown"], effects_of(task["id"])
    r = client.post(f"{T}/{task['id']}/continue", headers=ana["h"],
                    json={"state_version": task_of(client, ana["h"], task["id"])["state_version"]})
    assert r.status_code == 412 and "An effect of this task is unknown" in r.text, r.text


@contextlib.contextmanager
def answering_within(seconds):
    """Every call to a workspace waits at most `seconds` for its answer, as one to a workspace that does not answer
    would wait its whole time out."""
    from proofpane_open.ade import workspace_client
    real = workspace_client.call
    with pytest.MonkeyPatch.context() as m:
        m.setattr(workspace_client, "call", lambda endpoint, method, path, body=None, timeout=30:
                  real(endpoint, method, path, body, timeout=min(timeout, seconds)))
        yield


def test_take_back_while_the_teammate_s_workspace_runs_and_does_not_answer_waits_and_says_so(client, teammates,
                                                                                           told, github):
    """Take back while the teammate's workspace runs and does not answer: the app never acts on silence,
    so Take back waits, the offer saying why, until the workspace answers again; then it goes on."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    set_brief(client, bo["h"], bo["agent"], "bash echo line >> lines.txt ;; save half ;; sleep 60")
    press(client, bo["h"], theirs, "start")
    settle(bo["user"], theirs, lambda t: bool(checkpoints_taken(theirs)))
    half = checkpoints_taken(theirs)[-1]["manifest"]["head"]
    r = client.post(f"{TM}/tasks/{task['id']}/take-back", headers=ana["h"])
    assert r.status_code == 200, r.text
    agent_pid = bo["w"].agent.proc.pid
    os.kill(agent_pid, signal.SIGSTOP)
    try:
        with answering_within(2):
            rounds(ana["user"], bo["user"])
            rounds(ana["user"], bo["user"])
        assert task_row(task["id"])["state"] == "returning"
        assert offer_row(offer_id)["state"] == "accepted" and offer_row(offer_id)["why"] == task_offers.WAITING
    finally:
        os.kill(agent_pid, signal.SIGCONT)
    rounds(ana["user"], bo["user"])                     # stopped now
    settle(bo["user"], theirs, lambda t: t["lease_run"] is None)
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned"
    came_back(ana, task["id"], half)


# ---- hostile hand-offs -----------------------------------------------------------------------------------------------

def check_ran(log):
    """The homes the hostile check ran in, as it wrote them."""
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("adopt", [False, True])
def test_the_owner_s_check_runs_on_the_teammate_s_side_only_when_adopted(client, teammates, told, github, tmp_path,
                                                                        adopt):
    """The owner's check reads a credential file and sends it out (here it says where it ran): the offer shows
    it word for word; the teammate's workspace never runs it unless the teammate adopted it; and the owner's own
    workspace runs it on the returned commit."""
    log = tmp_path / "check-ran.log"
    hostile = f'cat "$HOME/.ssh/id_ed25519" > /dev/null 2>&1; echo "$HOME" >> "{log}"; test "$(wc -l < lines.txt)" -eq 2'
    steps = [{"objective": "Append line 1", "check": 'test "$(wc -l < lines.txt)" -eq 1'},
             {"objective": "Append line 2", "check": hostile}]
    ana, bo, task = pair(client, teammates, github, steps=steps)
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    rounds(ana["user"], bo["user"])
    task_offers.drive()
    (seen,) = received(client, bo["h"])
    assert seen["steps"][1]["check"] == hostile
    r = client.post(f"{TM}/offers/{offer_id}/accept", headers=bo["h"],
                    json={"agent_id": bo["agent"], "adopt_checks": [2] if adopt else []})
    assert r.status_code == 200, r.text
    theirs = r.json()["task_id"]
    press(client, bo["h"], theirs, "start")
    row = settle(bo["user"], theirs, ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
    assert (str(bo["w"].home) in check_ran(log)) is adopt, check_ran(log)
    assert str(ana["w"].home) not in check_ran(log)
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned"
    assert str(ana["w"].home) in check_ran(log) and task_row(task["id"])["state"] == "done"


AWS = "AKIA" + "Q" * 16


def test_a_secret_shape_in_the_owner_s_work_holds_the_offer_and_nothing_is_pushed(client, teammates, told, github):
    """The pushed changes hold a secret-shaped string, on the owner's side: the push's scan finds it, nothing
    is pushed, the offer ends naming the shape and its file (never the value), and the owner's task waits for them."""
    ana, bo, task = pair(client, teammates, github,
                         ana_brief=f"bash echo {AWS} > notes.txt ;; bash echo line >> lines.txt ;; complete step")
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    made = offer_row(offer_id)
    assert made["state"] == "failed" and pushes(github, since) == [], made
    assert "an AWS key (AKIA) in notes.txt" in made["why"] and AWS not in made["why"], made["why"]
    assert task_row(task["id"])["state"] == "needs_owner"
    assert received(client, bo["h"]) == []


def test_a_secret_shape_in_the_teammate_s_work_is_not_pushed_and_the_way_back_brings_what_was(client, teammates,
                                                                                              told, github):
    """On the teammate's side: Bo's first attempt saves clean work, which the round pushes; his second saves
    a secret-shaped string. Its push is refused by his workspace's scan, the offer naming the shape and its file, and
    Take back brings back the clean work alone."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(client, teammates, github, adopt=())
    set_brief(client, bo["h"], bo["agent"], "bash echo line >> lines.txt ;; save half")
    press(client, bo["h"], theirs, "start")
    settle(bo["user"], theirs, ended)
    half = checkpoints_taken(theirs)[-1]["manifest"]["head"]
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["pushed"] == half
    set_brief(client, bo["h"], bo["agent"], f"bash echo {AWS} > notes.txt ;; save more")
    press(client, bo["h"], theirs, "continue")
    settle(bo["user"], theirs, lambda t: len(checkpoints_taken(theirs)) == 2 and t["lease_run"] is None)
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    noted = offer_row(offer_id)
    assert noted["pushed"] == half and pushes(github, since) == [], noted
    assert "an AWS key (AKIA) in notes.txt" in noted["why"] and AWS not in noted["why"], noted["why"]
    r = client.post(f"{TM}/tasks/{task['id']}/take-back", headers=ana["h"])
    assert r.status_code == 200, r.text
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "returned" and pushes(github, since) == []
    came_back(ana, task["id"], half)


def test_a_dot_env_the_person_s_own_excludes_name_is_in_no_commit_and_no_push(client, teammates, told, github):
    """A `.env` ignored by the person's own excludes is in the worktree: it is in none of the owner's
    checkpoints, nor in the commit pushed for the offer, and so nowhere on the teammate's side."""
    ana, bo, task = pair(client, teammates, github, ana_excludes=".env\n",
                         ana_brief="bash echo TOKEN=local > .env ;; bash echo line >> lines.txt ;; complete step")
    assert os.path.exists(os.path.join(task_work(ana["w"], ana["agent"], task["id"]), ".env"))
    step1 = step_head(task["id"], 1)
    offer_id = offer(client, ana["h"], ana["user"], task["id"], "bo@example.com")
    rounds(ana["user"], bo["user"])
    assert offer_row(offer_id)["state"] == "offered"
    bare = github.folder / "repos" / f"{REPO}.git"
    assert git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=bare) == step1
    assert ".env" not in git("ls-tree", "-r", "--name-only", step1, cwd=bare).splitlines()
    task_offers.drive()
    r = client.post(f"{TM}/offers/{offer_id}/accept", headers=bo["h"], json={"agent_id": bo["agent"],
                                                                            "adopt_checks": []})
    assert r.status_code == 200, r.text
    theirs = r.json()["task_id"]
    assert not os.path.exists(os.path.join(task_work(bo["w"], bo["agent"], theirs), ".env"))


# ---- offering --------------------------------------------------------------------------------------------------------

def test_while_a_task_is_offered_nothing_is_queued_and_its_owner_s_presses_are_refused(client, teammates, told,
                                                                                      github):
    """Ana's step 2 is refused on her one account, its window resetting in an hour, and the task waits for that
    reset; she offers it. While it is offered, the reset comes (an injected clock) and auto-resume, which would take
    it, queues nothing; Continue is refused; an edit is refused. Cancelled inside the hold, nothing is pushed and the
    task is back waiting for that reset as before, which the next sweep then takes."""
    ha, ana, wa = person(client, teammates, "ana@example.com")
    on_github(wa, github, "ana")
    a = add_account(wa, "A", "ana@example.com")
    confirm(client, ha, ana, a)
    agent_a = make_agent(client, ha, a)
    task = make_task(client, ha, agent_a, between_steps="wait")
    press(client, ha, task["id"], "start")
    row = settle(ana, task["id"], lambda t: t["state"] == "suspended" or ended(t))
    assert (row["state"], row["cause"], row["step"]) == ("suspended", "step_done", 2), row
    refusing(wa, a, 3600)
    press(client, ha, task["id"], "continue")
    row = settle(ana, task["id"], lambda t: (t["state"], t["cause"]) == ("suspended", "window") or ended(t))
    assert (row["state"], row["cause"]) == ("suspended", "window") and row["waits_until"], row
    reset = row["waits_until"]
    person(client, teammates, "bo@example.com")
    offer_id = offer(client, ha, ana, task["id"], "bo@example.com")
    assert task_row(task["id"])["state"] == "offering"
    attempts = len(runs_of(task["id"]))
    tasks.sweep(reset + 60)
    agents.sync(ana)
    assert len(runs_of(task["id"])) == attempts and task_row(task["id"])["state"] == "offering"
    version = task_of(client, ha, task["id"])["state_version"]
    r = client.post(f"{T}/{task['id']}/continue", headers=ha, json={"state_version": version})
    assert r.status_code == 412, r.text
    r = client.put(f"{T}/{task['id']}", headers=ha, json={"state_version": version, "title": "Count more lines"})
    assert r.status_code == 412 and "offered to one" in r.text, r.text
    # Cancel, inside the hold
    r = client.post(f"/api/ade/holds/{offer_row(offer_id)['hold_id']}/cancel", headers=ha)
    assert r.status_code == 200, r.text
    since = len(github.seen)
    rounds(ana)
    assert pushes(github, since) == [] and offer_row(offer_id)["state"] == "withdrawn"
    row = task_row(task["id"])
    assert (row["state"], row["cause"], row["waits_until"]) == ("suspended", "window", reset), row
    # the account has room again: the sweep at the reset takes the task, and step 2 is done
    account_file(wa, folder=account_folder(wa, a), **wa.identities[a])
    tasks.sweep(reset + 60)
    row = settle(ana, task["id"], ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])


def test_an_offer_pressed_on_a_working_task_begins_once_its_stopped_attempt_s_end_is_copied(client, teammates, told,
                                                                                           github):
    """An offer on a running task: the confirmation asks for the offer and stops the attempt as Stop does; the
    offer begins only once the attempt's end is copied and leaves the task waiting and holding nothing: its hold
    placed then, and past it the commit the app recorded pushed, which is the stopped attempt's end: Stop committed
    its change past step 1's checkpoint."""
    ha, ana, wa = person(client, teammates, "ana@example.com")
    on_github(wa, github, "ana")
    a = add_account(wa, "A", "ana@example.com")
    confirm(client, ha, ana, a)
    agent_a = make_agent(client, ha, a)
    task = make_task(client, ha, agent_a, between_steps="wait")
    press(client, ha, task["id"], "start")
    settle(ana, task["id"], lambda t: t["state"] == "suspended" or ended(t))
    step1 = step_head(task["id"], 1)
    set_brief(client, ha, agent_a, "bash echo line >> lines.txt ;; sleep 60 ;; complete step")
    press(client, ha, task["id"], "continue")
    second = settle(ana, task["id"], lambda t: t["lease_run"] is not None and run_state(t["lease_run"]) == "working"
                    or ended(t))["lease_run"]
    # "working" is the attempt's state before its claude has run anything: the offer's stop waits for the attempt's
    # first command to have written its line, so that the stop has a change to commit past step 1's checkpoint (on a
    # Mac the stop came first, and the end it committed was step 1's)
    work = task_work(wa, agent_a, task["id"])
    syncing_until(ana, lambda: lines_of(work) == ["line", "line"], "the second attempt's first command did not run")
    person(client, teammates, "bo@example.com")
    offer_id = offer(client, ha, ana, task["id"], "bo@example.com")
    # the confirmation stopped the attempt; once its end is copied (the stop's own answer carries it) the offer begins
    row = settle(ana, task["id"], lambda t: t["state"] == "offering" or (t["lease_run"] is None and t["state"] !=
                                                                         "running"))
    assert row["state"] == "offering" and run_state(second) == "stopped", row
    assert offer_row(offer_id)["state"] == "held" and offer_row(offer_id)["hold_id"] is not None
    kinds = [e["kind"] for e in events(task["id"])]
    assert kinds[-3:] == ["attempt.ended", "task.suspended", "task.offered"], kinds
    # its hold told as that end was copied, with what the end left to tell, though no round has run
    assert [m.title for u, m in told if u == ana["id"]].count("An offer of a task waits") == 1
    # past the hold, the commit the app recorded: the stopped attempt's end, its change committed by the stop, past
    # step 1's checkpoint (the task's last checkpoint or end state), which the push checks the
    # branch at and pushes, by its hash
    end = git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=work)
    assert end != step1 and git("rev-parse", f"{end}~1", cwd=work) == step1
    rounds(ana)
    assert offer_row(offer_id)["state"] == "offered" and offer_row(offer_id)["commit"] == end, offer_row(offer_id)
    assert [m.title for u, m in told if u == ana["id"]].count("An offer of a task waits") == 1   # not told again
    bare = github.folder / "repos" / f"{REPO}.git"
    assert git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=bare) == end
    assert git("show", f"{end}:lines.txt", cwd=work).splitlines() == ["line", "line"]


# ---- two users, two workspaces, one app ------------------------------------------------------------------------------

ANA_WORDS = "Ana's own words, said in her run"
BO_WORDS = "Bo's own words, said in his run"
# what each keeps of their own (keep_own): a model key, and a memory of their agent's
KEPT = {"ana": ("sk-ant-test-ana-" + "a" * 30, "Ana-only memory: the staging host is blue"),
        "bo": ("sk-ant-test-bo-" + "b" * 30, "Bo-only memory: the build cache is red")}


def seen_by(client, h, task_id):
    """All the app shows a user of a task: the task, its journal, and their offers, sent and received."""
    task = client.get(f"{T}/{task_id}", headers=h).json()
    journal = client.get(f"{T}/{task_id}/journal", headers=h).json()
    offers = client.get(f"{TM}/offers", headers=h).json()
    return json.dumps([task, journal, offers])


def test_two_users_keep_their_own_accounts_sign_ins_workspaces_and_words(client, teammates, told, github):
    """Two users, two workspaces, one app. Each attempt runs in its own user's workspace, on its own user's
    account; each push and fetch is made with its own user's sign-in; nothing of Ana's accounts, key, memory or her
    runs' words reaches Bo's workspace or packet, nor of Bo's hers; each sees the other's task state, never its runs'
    words. Each workspace has a secret of its own (Provider), so a call carrying one user's secret to the other's
    workspace is refused there."""
    ana, bo, task, offer_id, theirs, step1 = handed_to_bo(
        client, teammates, github, adopt=(), ana_brief=f"bash echo line >> lines.txt ;; {ANA_WORDS} ;; complete step",
        kept=KEPT)
    set_brief(client, bo["h"], bo["agent"], f"bash echo line >> lines.txt ;; {BO_WORDS} ;; complete step")
    press(client, bo["h"], theirs, "start")
    assert settle(bo["user"], theirs, ended)["state"] == "done"
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    assert task_row(task["id"])["state"] == "done"
    # each its own sign-in: Bo's work pushed with his (his push reading the remote's ref too), then read with hers
    users = [user for _, _, user in git_calls(github, since)]
    assert pushes(github, since) == [("/" + REPO + ".git", "bo")]
    assert users == ["bo"] * users.count("bo") + ["ana"] * users.count("ana") and "ana" in users, users
    # each attempt in its own workspace, in the folder of its own user's account, none of the other's there
    assert set(attempt_folders(ana["w"])) == {account_folder(ana["w"], ana["account"])}
    assert set(attempt_folders(bo["w"])) == {account_folder(bo["w"], bo["account"])}
    assert os.listdir(os.path.join(bo["w"].root, "agent-accounts")) == [bo["account"]]
    assert os.listdir(os.path.join(ana["w"].root, "agent-accounts")) == [ana["account"]]
    # Bo's packet holds Ana's task (her goal, steps and address, as a teammate's), never her account or her words
    (run,) = runs_of(theirs)
    packet = json.dumps(packet_of(run["run_id"]))
    assert ana["account"] not in packet and ANA_WORDS not in packet and "ana@example.com" in packet
    # what each sees of the other's: the task's state, never the words of the other's runs, which the app keeps for
    # each run's own user (the positive control: there, the words are)
    assert BO_WORDS in json.dumps(client.get(f"{A}/{bo['agent']}", headers=bo["h"]).json())
    assert ANA_WORDS in json.dumps(client.get(f"{A}/{ana['agent']}", headers=ana["h"]).json())
    assert BO_WORDS not in seen_by(client, ana["h"], task["id"])
    assert ANA_WORDS not in seen_by(client, bo["h"], theirs)
    for r in (client.get(f"{T}/{task['id']}", headers=bo["h"]), client.get(f"{T}/{theirs}", headers=ana["h"])):
        assert r.status_code == 404, r.text
    # each user's key and memory, given through the app before any packet was made (pair), as their own: the key in
    # their workspace's key file, the memory in their agent's memory folder and in the app's first version of it; and
    # neither in any file of the other's folder (root, home and remote; the same search finds each in its owner's), in
    # any packet the app made, of either user, nor read through the app by the other.
    # A call carrying one user's secret to the other's workspace would be refused there (Provider); and the app stores
    # no packet that holds a key's text, by its rule for secret shapes (sk-ant-), so the packet
    # check is the memory's
    with store.connect() as db:
        rows = db.execute("SELECT user_id, packet, created_at FROM ade_agent_packets").fetchall()
        kept_at = db.execute("SELECT MAX(made_at) FROM ade_agent_memory WHERE agent_id IN (?, ?) AND version = 1",
                             (ana["agent"], bo["agent"])).fetchone()[0]
    packets = [(user_id, json.dumps(json.loads(stored))) for user_id, stored, _ in rows]
    # packets of both users, each made once both had kept theirs (to the second the store keeps times in); and the
    # search's positive control: each user's packets hold the task's goal, which the app puts in a packet's
    # instructions
    assert {u for u, _ in packets} == {ana["user"]["id"], bo["user"]["id"]}, rows
    assert kept_at is not None and all(made >= kept_at for _, _, made in rows), (kept_at, rows)
    goal = task_row(task["id"])["goal"]
    for x in (ana, bo):
        assert any(goal in p for u, p in packets if u == x["user"]["id"]), (x["user"]["id"], goal)
    for name, x, other in (("ana", ana, bo), ("bo", bo, ana)):
        key, memory = KEPT[name]
        assert providers.keys(str(x["w"].home))["anthropic"] == key
        with open(os.path.join(x["w"].root, "agents", x["agent"], "memory", "MEMORY.md"), encoding="utf-8") as fh:
            assert fh.read() == memory
        assert memory in json.dumps(client.get(f"{A}/{x['agent']}/memory/1", headers=x["h"]).json())
        for needle in (key, memory):
            assert _files_holding(str(x["w"].tmp), needle), (name, needle)
            assert _files_holding(str(other["w"].tmp), needle) == [], (name, needle)
            assert all(needle not in p for _, p in packets), (name, needle)
        for path in ("memory", "memory/1"):
            r = client.get(f"{A}/{x['agent']}/{path}", headers=other["h"])
            assert r.status_code == 404, (path, r.text)


# ---- custody --------------------------------------------------------------------------------------------------------
#
# Ana names Bo her custodian, with her confirmation, and Bo accepts with his receiving agents, with his; Ana confirms a
# task for custody, with a third; then every account of her task's relay set refuses it, its reset more than custody's
# margin away, and the relay's step 2 hands the task to Bo's agent by standing consent, after its hold.

MODEL_NOTE = "a note the model wrote at the end of step 1"


def custodian(client, ana, bo, receiving=None):
    """Ana names Bo her custodian and confirms the request; Bo accepts it with his receiving agents (`receiving`, else
    his one agent) and his own confirmation. Custody's id."""
    r = client.post(f"{TM}/custody", headers=ana["h"], json={"address": "bo@example.com"})
    assert r.status_code == 200, r.text
    custody_id = r.json()["custody"]["id"]
    confirmation = passkeys.confirm(ana["user"], password=PASSWORD, subject=f"task-custody:{custody_id}")
    r = client.post(f"{TM}/custody/{custody_id}/confirm", headers=ana["h"],
                    json={"confirmation": confirmation["confirmation"]})
    assert r.status_code == 200, r.text
    confirmation = passkeys.confirm(bo["user"], password=PASSWORD, subject=f"task-custody-accept:{custody_id}")
    r = client.post(f"{TM}/custody/{custody_id}/accept", headers=bo["h"],
                    json={"agents": receiving or [bo["agent"]], "confirmation": confirmation["confirmation"]})
    assert r.status_code == 200, r.text
    return custody_id


def confirmed_for_custody(client, ana, task_id):
    """Ana confirms the task for custody as it stands, with her confirmation made for it."""
    version = task_of(client, ana["h"], task_id)["state_version"]
    confirmation = passkeys.confirm(ana["user"], password=PASSWORD, subject=f"task-custody-confirm:{task_id}")
    r = client.post(f"{TM}/tasks/{task_id}/custody-confirm", headers=ana["h"],
                    json={"state_version": version, "confirmation": confirmation["confirmation"]})
    assert r.status_code == 200, r.text


def custody_rows(task_id):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_agent_task_offers WHERE task_id = ? AND kind = "
                                            "'custody' ORDER BY created_at", (task_id,)).fetchall()]


def hold_of(hold_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_holds WHERE id = ?", (hold_id,)).fetchone())


def refused_on_continue(client, ana, task_id, resets=3600):
    """Ana's account refuses every attempt, its five-hour window resetting `resets` seconds after the refusal; she
    presses Continue, and step 2's attempt is refused at its first answer: the task as the app keeps it once that end
    is copied and the relay has decided."""
    refusing(ana["w"], ana["account"], resets)
    press(client, ana["h"], task_id, "continue")
    return settle(ana["user"], task_id, lambda t: t["state"] == "offering" or ended(t))


def test_custody_takes_a_confirmed_task_every_account_of_which_refuses_after_its_hold(client, teammates, told,
                                                                                     github):
    """The hand-off is held 180 seconds with a notice to Ana; inside the hold nothing is pushed; past it Ana's
    workspace pushes the commit the app recorded, with her sign-in, and Bo's agent takes the task by standing consent,
    its first attempt the custodian's relay's on Bo's account, never a press; its packet holds no word a model wrote,
    and Bo is told. Bo's task done, the way back brings his work to Ana's branch, where her own check of step 2
    passes."""
    ana, bo, task = pair(client, teammates, github, ana_brief=f"bash echo line >> lines.txt ;; {ANA_WORDS} ;; "
                                                             f"complete {MODEL_NOTE}")
    custodian(client, ana, bo)
    confirmed_for_custody(client, ana, task["id"])
    step1 = step_head(task["id"], 1)
    row = refused_on_continue(client, ana, task["id"])
    assert row["state"] == "offering", (row["state"], row["cause"], row["reason"])
    (handoff,) = custody_rows(task["id"])
    assert (handoff["state"], handoff["teammate_user"]) == ("held", bo["user"]["id"])
    hold = hold_of(handoff["hold_id"])
    assert hold["takes_effect_at"] - hold["approved_at"] == 180, hold
    assert "A task of yours goes to your custodian" in [m.title for u, m in told if u == ana["user"]["id"]]
    # inside the hold nothing is pushed, nothing is fetched, and Bo has no task
    rounds(ana["user"], bo["user"], after=0)
    assert git_calls(github) == [] and offer_row(handoff["id"])["state"] == "held"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_tasks WHERE user_id = ?",
                          (bo["user"]["id"],)).fetchone()[0] == 0
    # past it: pushed with Ana's sign-in, fetched with Bo's, and taken
    rounds(ana["user"], bo["user"])
    taken = offer_row(handoff["id"])
    assert taken["state"] == "accepted", (taken["state"], taken["why"])
    assert pushes(github) == [("/" + REPO + ".git", "ana")] and readers(github) == {"ana", "bo"}
    bare = github.folder / "repos" / f"{REPO}.git"
    assert taken["commit"] == step1 and git("rev-parse", f"refs/heads/pp/task-{task['id']}", cwd=bare) == step1
    assert task_row(task["id"])["state"] == "handed_over"
    theirs = taken["their_task"]
    assert task_row(theirs)["user_id"] == bo["user"]["id"] and task_row(theirs)["checkpoint0"] == step1
    # its first attempt, the custodian's relay's, on Bo's account, at step 2: no press of Bo's or Ana's started it
    (run,) = runs_of(theirs)
    assert (run["chosen_by"], run["task_step"], json.loads(run["backend"])) == (
        "custody", 2, {"kind": "account", "id": bo["account"]})
    assert "pressed" not in json.loads(run["trigger"])
    assert "A task was handed to you" in [m.title for u, m in told if u == bo["user"]["id"]]
    # its packet, and the hand-off's: Ana's task as a teammate's, her step 1 done, and no word of her runs' model,
    # said or noted; the positive control, her own next attempt's packet (step 2's, refused), which carried both
    packet = packet_of(run["run_id"])
    assert packet["instructions"]["by"] == "teammate" and packet["instructions"]["from"] == "ana@example.com"
    assert packet["information"]["handed"]["done_steps"] == [1], packet["information"]
    handed = json.loads(taken["packet"])
    assert handed["kind"] == "custody" and handed["information"]["done_steps"] == [1]
    hers = json.dumps(packet_of(runs_of(task["id"])[1]["run_id"]))
    for words in (ANA_WORDS, MODEL_NOTE):
        assert words in hers and words not in json.dumps(packet) and words not in json.dumps(handed), words
    # Bo's relay runs it on his account, in his workspace, and it is done there
    row = settle(bo["user"], theirs, ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
    assert set(attempt_folders(bo["w"])) == {account_folder(bo["w"], bo["account"])}
    # the way back: his work pushed to her branch with his sign-in, read with hers, her own check of step 2 passing
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    assert pushes(github, since) == [("/" + REPO + ".git", "bo")] and "ana" in readers(github, since)
    assert offer_row(handoff["id"])["state"] == "returned"
    owner = task_row(task["id"])
    assert owner["state"] == "done", (owner["state"], owner["cause"], owner["reason"])
    assert owner_steps(task["id"]) == [(1, "done", "checked"), (2, "done", "checked")]
    assert lines_at(ana["w"], ana["agent"], task["id"], step_head(theirs, 2)) == ["line", "line"]
    # Ana's own attempts: step 1, and step 2's, refused at its first answer; none of hers after the hand-off
    assert [(r["task_step"], r["state"], r["cause"]) for r in runs_of(task["id"])] == [
        (1, "done", None), (2, "blocked", "rate_limit")]
    for user_id, m in told:
        for word in (task["id"], theirs, ana["agent"], bo["agent"], "ana@example.com", "bo@example.com"):
            assert word not in m.title + m.body, (m.title, m.body)


def tasks_of(user):
    with store.connect() as db:
        return [r[0] for r in db.execute("SELECT id FROM ade_agent_tasks WHERE user_id = ?", (user["id"],)).fetchall()]


def waits_for_the_reset(task_id, resets=3600):
    """The task waits for its account's reset, as the relay's third step leaves a task that nothing could take:
    `resets` seconds after the refusal, which came within the last ten minutes. Its row."""
    row = task_row(task_id)
    assert (row["state"], row["cause"]) == ("suspended", "window"), (row["state"], row["cause"], row["reason"])
    assert time.time() + resets - 600 < row["waits_until"] <= time.time() + resets, row["waits_until"] - time.time()
    return row


def test_cancelling_the_custody_hand_off_inside_its_hold_keeps_the_task(client, teammates, told, github):
    """Ana cancels the hand-off's hold. Nothing is pushed or fetched, Bo is given no task and told nothing, and
    the task waits for her account's reset."""
    ana, bo, task = pair(client, teammates, github)
    custodian(client, ana, bo)
    confirmed_for_custody(client, ana, task["id"])
    row = refused_on_continue(client, ana, task["id"])
    assert row["state"] == "offering", (row["state"], row["cause"], row["reason"])
    (handoff,) = custody_rows(task["id"])
    r = client.post(f"/api/ade/holds/{handoff['hold_id']}/cancel", headers=ana["h"])
    assert r.status_code == 200, r.text
    rounds(ana["user"], bo["user"])
    assert git_calls(github) == [] and offer_row(handoff["id"])["state"] == "withdrawn"
    waits_for_the_reset(task["id"])
    assert tasks_of(bo["user"]) == []
    assert "A task was handed to you" not in [m.title for u, m in told if u == bo["user"]["id"]]


@pytest.mark.parametrize("case", ["control", "unconfirmed", "task_changed", "brief_changed"])
def test_custody_hands_only_a_task_confirmed_as_it_stands(client, teammates, told, github, case):
    """A task never confirmed for custody, one whose title changed after its confirmation, and one whose
    agent's brief changed after it: the relay's step 2 hands none of them, nothing is pushed, and each waits for its
    account's reset. The control, confirmed and unchanged, goes to the custodian."""
    ana, bo, task = pair(client, teammates, github)
    custodian(client, ana, bo)
    if case != "unconfirmed":
        confirmed_for_custody(client, ana, task["id"])
    if case == "task_changed":
        version = task_of(client, ana["h"], task["id"])["state_version"]
        r = client.put(f"{T}/{task['id']}", headers=ana["h"], json={"state_version": version,
                                                                    "title": "Count more lines"})
        assert r.status_code == 200, r.text
    elif case == "brief_changed":
        set_brief(client, ana["h"], ana["agent"], APPEND.replace("complete step", "complete the step"))
    row = refused_on_continue(client, ana, task["id"])
    if case == "control":
        assert row["state"] == "offering" and len(custody_rows(task["id"])) == 1, (row["state"], row["cause"])
        return
    waits_for_the_reset(task["id"])
    assert custody_rows(task["id"]) == [] and git_calls(github) == [] and tasks_of(bo["user"]) == []


# the gate refuses an agent's run any command naming a .claude folder (gate.py, AGENT_SETTINGS); a name put together
# while the command runs is not seen, as its docstring says, so a run can still add a hook this way, and custody's own
# look at the recorded commit is what keeps it from being taken
HOOK = "bash d=.cla && mkdir -p ${d}ude && echo '{\"hooks\": {}}' > ${d}ude/settings.json"


@pytest.mark.parametrize("hook", [False, True])
def test_custody_never_takes_a_task_whose_recorded_commit_adds_a_hook(client, teammates, told, github, hook):
    """Ana's step 1 adds a hook in .claude/settings.json, a file a next run would load, by the one way a run
    passes the gate (above); the task's watch then holds the task for her at that attempt's end, and she looks, lifts
    the watch, keeping the hook, and resumes the agent. Past the hand-off's hold, her own workspace's look at the
    recorded commit lists the file before anything is pushed (the seam: the workspace's view of the task's commits
    against the repository's default branch, read by the app's custody), so custody never takes it by standing
    consent, its custodian never having looked: the hand-off ends failed, saying why, nothing is pushed, Bo reads
    nothing and is given no task, and the relay decides again, the task waiting for her account's reset. The control,
    the same steps without the hook, goes to Bo."""
    ana, bo, task = pair(client, teammates, github, ana_brief=(HOOK + " ;; " if hook else "") + APPEND,
                         first_end=("needs_owner", "watch") if hook else ("suspended", "step_done"))
    files = git("ls-tree", "-r", "--name-only", step_head(task["id"], 1),
                cwd=task_work(ana["w"], ana["agent"], task["id"])).split("\n")
    assert (".claude/settings.json" in files) == hook and "lines.txt" in files, files
    if hook:
        # the workspace held the agent at that end, and said so on the run's record, so the app shows it paused,
        # naming the file (found by this case: the hold was left off the record, the app showed the agent never
        # paused, and her resume lifted nothing in the workspace, every Continue refused there)
        with store.connect() as db:
            agent = dict(db.execute("SELECT paused, paused_reason, hold_unmirrored FROM ade_agents WHERE id = ?",
                                    (ana["agent"],)).fetchone())
        assert (agent["paused"], agent["hold_unmirrored"]) == (1, 1), agent
        assert ".claude/settings.json" in agent["paused_reason"], agent
        press(client, ana["h"], task["id"], "resolve", what="watch", outcome="lift")
        assert client.post(f"{A}/{ana['agent']}/resume", headers=ana["h"]).status_code == 200
        assert task_row(task["id"])["state"] == "suspended"
    custodian(client, ana, bo)
    confirmed_for_custody(client, ana, task["id"])
    row = refused_on_continue(client, ana, task["id"])
    assert row["state"] == "offering", (row["state"], row["cause"], row["reason"])
    (handoff,) = custody_rows(task["id"])
    rounds(ana["user"], bo["user"])
    ended_ = offer_row(handoff["id"])
    if not hook:
        assert ended_["state"] == "accepted", (ended_["state"], ended_["why"])
        return
    assert (ended_["state"], ended_["why"], ended_["teammate_user"]) == ("failed", task_offers.LOADED_CHANGED, None)
    assert pushes(github) == [] and "bo" not in readers(github) and tasks_of(bo["user"]) == []
    waits_for_the_reset(task["id"])


@pytest.mark.parametrize("ticked", [False, True])
def test_custody_takes_a_personal_task_to_a_seat_only_with_its_own_crossing_ticked(client, teammates, told, github,
                                                                                  monkeypatch, ticked):
    """Bo's receiving agent is on a seat of a Team organisation and Ana's task on her
    personal plan, with the deployment's crossing on and the task's own "Cross organisations" switch on. Custody's own
    crossing does not follow that switch: unticked, custody hands nothing and the task waits for its account's reset;
    ticked (its own confirmation, then its hold of 180 seconds), the seat's agent takes it, and the hand-over says it
    crossed."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    ana, bo, task = pair(client, teammates, github)
    assert task_row(task["id"])["cross_groups"] == 1
    seat = add_account(bo["w"], "S", "bo@yarra.example", plan="Claude Team", org_id="org-yarra", organization="Yarra")
    confirm(client, bo["h"], bo["user"], seat)
    on_seat = make_agent(client, bo["h"], seat)
    custodian(client, ana, bo, receiving=[on_seat])
    if ticked:
        version = task_of(client, ana["h"], task["id"])["state_version"]
        confirmation = passkeys.confirm(ana["user"], password=PASSWORD, subject=f"task-custody-cross:{task['id']}")
        r = client.post(f"{TM}/tasks/{task['id']}/custody-cross", headers=ana["h"],
                        json={"state_version": version, "confirmation": confirmation["confirmation"]})
        assert r.status_code == 200, r.text
        hold = hold_of(r.json()["hold"]["id"])
        assert hold["takes_effect_at"] - hold["approved_at"] == 180 and task_row(task["id"])["custody_cross"] == 0
        holds.settle(ana["user"]["id"], hold["takes_effect_at"] + 1)
        assert task_row(task["id"])["custody_cross"] == 1
    confirmed_for_custody(client, ana, task["id"])
    row = refused_on_continue(client, ana, task["id"])
    if not ticked:
        waits_for_the_reset(task["id"])
        assert custody_rows(task["id"]) == [] and git_calls(github) == [] and tasks_of(bo["user"]) == []
        return
    assert row["state"] == "offering", (row["state"], row["cause"], row["reason"])
    (handoff,) = custody_rows(task["id"])
    rounds(ana["user"], bo["user"])
    taken = offer_row(handoff["id"])
    assert taken["state"] == "accepted", (taken["state"], taken["why"])
    (handed,) = events(task["id"], "task.handed_over")
    assert handed["payload"]["crossed"] is True, handed["payload"]
    (run,) = runs_of(taken["their_task"])
    assert (run["chosen_by"], json.loads(run["backend"])) == ("custody", {"kind": "account", "id": seat})


# ---- custody's conditions --------------------------------------------------------------------------------------------

def custody_row(custody_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_task_custody WHERE id = ?", (custody_id,)).fetchone())


@pytest.mark.parametrize("case", ["reset_soon", "room", "agent_changed", "second_agent", "ended_held",
                                  "ended_with_the_custodian"])
def test_custody_hands_a_task_only_while_its_conditions_hold(client, teammates, told, github, case):
    """Custody's conditions, the cases a real relay and workspaces bear on (each of the others is test_task_offers.py's
    or test_agent_tasks.py's, against a fake): the earliest reset ten minutes away hands nothing, and the task waits for
    it; an account of Ana's with room hands nothing, the relay moving to it instead; a receiving agent changed suspends
    custody, Ana told, and nothing is handed; a receiving agent with an open task is passed over for the next; custody
    ended by Bo while a hand-off is held ends it failed and the task waits for its reset; ended by Ana while the task is
    with Bo, the task stays there until its way back brings it home."""
    ana, bo, task = pair(client, teammates, github)
    receiving = None
    if case == "second_agent":
        second = make_agent(client, bo["h"], bo["account"])
        make_task(client, bo["h"], bo["agent"])                     # Bo's first agent has a task open
        receiving = [bo["agent"], second]
    custody_id = custodian(client, ana, bo, receiving=receiving)
    if case == "room":
        b = add_account(ana["w"], "B", "ana.b@example.com")
        confirm(client, ana["h"], ana["user"], b)
    elif case == "agent_changed":
        set_brief(client, bo["h"], bo["agent"], APPEND.replace("complete step", "complete the step"))
        task_offers.drive()
        assert custody_row(custody_id)["state"] == "suspended"
        assert "Your custody was suspended" in [m.title for u, m in told if u == ana["user"]["id"]]
    confirmed_for_custody(client, ana, task["id"])
    row = refused_on_continue(client, ana, task["id"], resets=600 if case == "reset_soon" else 3600)
    if case in ("reset_soon", "room", "agent_changed"):
        if case == "room":
            assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
            assert [json.loads(r["backend"])["id"] for r in runs_of(task["id"])] == [ana["account"], ana["account"],
                                                                                      b]
        else:
            waits_for_the_reset(task["id"], resets=600 if case == "reset_soon" else 3600)
        assert custody_rows(task["id"]) == [] and git_calls(github) == [] and tasks_of(bo["user"]) == []
        return
    assert row["state"] == "offering", (row["state"], row["cause"], row["reason"])
    (handoff,) = custody_rows(task["id"])
    if case == "ended_held":
        r = client.post(f"{TM}/custody/{custody_id}/end", headers=bo["h"])
        assert r.status_code == 200, r.text
        ended_ = offer_row(handoff["id"])
        assert (ended_["state"], ended_["why"]) == ("failed", task_offers.CUSTODY_ENDED_WHY)
        waits_for_the_reset(task["id"])
        rounds(ana["user"], bo["user"])
        assert git_calls(github) == [] and tasks_of(bo["user"]) == []
        return
    rounds(ana["user"], bo["user"])
    taken = offer_row(handoff["id"])
    assert taken["state"] == "accepted", (taken["state"], taken["why"])
    theirs = taken["their_task"]
    if case == "second_agent":
        assert task_row(theirs)["agent_id"] == second and runs_of(theirs)[0]["agent_id"] == second
        return
    r = client.post(f"{TM}/custody/{custody_id}/end", headers=ana["h"])
    assert r.status_code == 200, r.text
    assert custody_row(custody_id)["state"] == "ended" and task_row(task["id"])["state"] == "handed_over"
    assert settle(bo["user"], theirs, ended)["state"] == "done"
    rounds(ana["user"], bo["user"])
    assert task_row(task["id"])["state"] == "done" and offer_row(handoff["id"])["state"] == "returned"


# ---- custody's cases: its own return on the owner's room, Keep, Give back --------------------------------------------

# three steps whose checks hold at any later commit too: the owner's checks of the steps that come back run on the
# returned commit, past the step each checks when more than one step came back
AT_LEAST = [{"objective": f"Append line {i}", "check": f'test "$(wc -l < lines.txt)" -ge {i}'} for i in (1, 2, 3)]


def with_bo_by_custody(client, teammates, github, steps=AT_LEAST, between="checked"):
    """Custody's hand-off of a task of `steps` steps whose Between steps is `between` (set by the task's own edit
    after its first step, before it is confirmed for custody, since an edit clears that confirmation): taken by Bo's
    agent, its first attempt queued on Bo's account and not yet sent. (ana, bo, task, the hand-off's row, Bo's task
    id)."""
    ana, bo, task = pair(client, teammates, github, steps=steps)
    custodian(client, ana, bo)
    version = task_of(client, ana["h"], task["id"])["state_version"]
    r = client.put(f"{T}/{task['id']}", headers=ana["h"], json={"state_version": version, "between_steps": between})
    assert r.status_code == 200, r.text
    confirmed_for_custody(client, ana, task["id"])
    row = refused_on_continue(client, ana, task["id"])
    assert row["state"] == "offering", (row["state"], row["cause"], row["reason"])
    (handoff,) = custody_rows(task["id"])
    rounds(ana["user"], bo["user"])
    taken = offer_row(handoff["id"])
    assert taken["state"] == "accepted", (taken["state"], taken["why"])
    return ana, bo, task, taken, taken["their_task"]


def answering_allowed(ana):
    """Ana's account answers every attempt with its five-hour window allowed, at the frozen start's first answer; the
    unfreezing restart then works as told (as in the weekly reserve's test). An attempt there is fresh evidence of its
    room."""
    account_file(ana["w"], folder=account_folder(ana["w"], ana["account"]), **dict(
        ana["w"].identities[ana["account"]], first_answer="ratelimit allowed five_hour resets=18000",
        restart={"first_answer": None}))


def room_again(client, ana):
    """Ana's account has room again on fresh evidence: an attempt of another task of hers, on another agent, hears it
    allowed."""
    answering_allowed(ana)
    other = make_agent(client, ana["h"], ana["account"])
    side = make_task(client, ana["h"], other, steps=1)
    press(client, ana["h"], side["id"], "start")
    row = settle(ana["user"], side["id"], ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"],
                                    [(r["state"], r["cause"], r["reason"]) for r in runs_of(side["id"])])


def back_home(ana, bo, task_id, most=5):
    """Rounds, each after Bo's side is synced (so that a stop's end is copied), until Ana's task is no longer coming
    back: its row."""
    for _ in range(most):
        agents.sync(bo["user"])
        rounds(ana["user"], bo["user"])
        row = task_row(task_id)
        if row["state"] != "returning":
            return row
    raise AssertionError(f"the task did not come back: {task_row(task_id)['state']}")


def test_custody_brings_the_task_back_at_a_step_s_end_once_the_owner_has_room_and_her_relay_goes_on(
        client, teammates, told, github):
    """Custody's own return, and the race of a step's end with the owner's room, end to end: Bo's custody task completes
    step 2 while Ana's account has room again, so the copy of that end begins the way back and queues no next step on
    Bo's side; the way back brings his work to her branch, where her check of step 2 passes, and her relay goes on
    (Between steps: checked) with step 3 on her account, from the returned commit, and the task is done. Bo's task is
    left returned, waiting for its owner."""
    ana, bo, task, taken, theirs = with_bo_by_custody(client, teammates, github)
    room_again(client, ana)
    settle(bo["user"], theirs, lambda t: task_row(task["id"])["state"] == "returning" or ended(t))
    assert task_row(task["id"])["state"] == "returning"
    assert [(r["task_step"], r["state"]) for r in runs_of(theirs)] == [(2, "done")]
    since = len(github.seen)
    rounds(ana["user"], bo["user"])
    assert pushes(github, since) == [("/" + REPO + ".git", "bo")] and "ana" in readers(github, since)
    # custody's own return: its way back says so, and the owner's relay goes on at once, as that return's results land
    (began,) = events(task["id"], "task.returned")
    assert began["payload"]["why"] == task_offers.CUSTODY_RETURN
    after = task_row(task["id"])
    assert after["state"] == "running", (after["state"], after["cause"], after["reason"])
    row = settle(ana["user"], task["id"], ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
    assert owner_steps(task["id"]) == [(1, "done", "checked"), (2, "done", "checked"), (3, "done", "checked")]
    third = runs_of(task["id"])[-1]
    assert (third["task_step"], third["chosen_by"], json.loads(third["backend"])) == (
        3, "next_step", {"kind": "account", "id": ana["account"]})
    assert lines_at(ana["w"], ana["agent"], task["id"], step_head(task["id"], 3)) == ["line"] * 3
    # on Bo's side only the return: no next step queued, his task returned and waiting for its owner
    assert [r["task_step"] for r in runs_of(theirs)] == [2]
    mine = task_row(theirs)
    assert (mine["state"], mine["cause"], mine["returned"], mine["lease_run"]) == (
        "needs_owner", "returned", 1, None), (mine["state"], mine["cause"], mine["reason"])


def test_a_custody_task_the_custodian_keeps_goes_on_there_until_it_ends_then_comes_back(client, teammates, told,
                                                                                         github):
    """Keep: Bo keeps the custody task; Ana has room again, yet the end of his step 2 brings nothing back and
    his relay goes on with step 3; done there, it comes back by its way back, Ana's own checks of steps 2 and 3 passing
    on her side."""
    ana, bo, task, taken, theirs = with_bo_by_custody(client, teammates, github)
    version = task_of(client, bo["h"], theirs)["state_version"]
    r = client.post(f"{TM}/tasks/{theirs}/keep", headers=bo["h"], json={"state_version": version})
    assert r.status_code == 200, r.text
    room_again(client, ana)
    row = settle(bo["user"], theirs, ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
    assert [r["task_step"] for r in runs_of(theirs)] == [2, 3] and task_row(task["id"])["state"] == "handed_over"
    rounds(ana["user"], bo["user"])
    back = task_row(task["id"])
    assert back["state"] == "done", (back["state"], back["cause"], back["reason"], owner_steps(task["id"]))
    assert events(task["id"], "task.returned")[-1]["payload"]["why"] is None     # not custody's own return
    assert owner_steps(task["id"]) == [(1, "done", "checked"), (2, "done", "checked"), (3, "done", "checked")]


def test_a_custody_task_given_back_comes_back_at_once_and_waits_for_its_owner(client, teammates, told, github):
    """Give back: Bo gives the custody task back before its first attempt is sent; its attempt is stopped, the
    task comes back by its way back with nothing done there, and, a return the custodian chose rather than custody's
    own, it waits for Ana; her Continue then runs steps 2 and 3 on her account."""
    ana, bo, task, taken, theirs = with_bo_by_custody(client, teammates, github)
    version = task_of(client, bo["h"], theirs)["state_version"]
    r = client.post(f"{TM}/tasks/{theirs}/give-back", headers=bo["h"], json={"state_version": version})
    assert r.status_code == 200, r.text
    assert task_row(task["id"])["state"] == "returning"
    row = back_home(ana, bo, task["id"])
    assert (row["state"], row["cause"], row["reason"]) == (
        "suspended", "step_done", "It came back from your teammate: Continue to go on."), row
    assert len(runs_of(task["id"])) == 2 and owner_steps(task["id"])[1:] == [(2, "doing", None), (3, "todo", None)]
    mine = task_row(theirs)
    assert (mine["state"], mine["cause"], mine["returned"]) == ("needs_owner", "returned", 1), mine
    # its one attempt, queued and never sent, stopped by the way back as Stop stops it
    assert [(r["task_step"], r["state"], r["cause"]) for r in runs_of(theirs)] == [(2, "stopped", None)], \
        [(r["task_step"], r["state"], r["cause"], r["reason"]) for r in runs_of(theirs)]
    answering_allowed(ana)
    press(client, ana["h"], task["id"], "continue")
    row = settle(ana["user"], task["id"], ended)
    assert row["state"] == "done", (row["state"], row["cause"], row["reason"])
    assert [r["task_step"] for r in runs_of(task["id"])][2:] == [2, 3]
