"""Tasks in the app (ade/tasks.py): its acceptance tests, against a fake workspace (Fake below), with fakes of other
modules' sides where a test needs them: the words push.WORDS lacks, and task_offers' custody and prune."""

import contextlib
import json
import re
import secrets
import sqlite3
import threading
import time

import pytest

from conftest import signed_up
from proofpane_open import auth, passkeys, store
from proofpane_open.ade import agents, holds, push, task_offers, tasks, triggers, workspace_client
from proofpane_open.ade import task_shapes as shapes

A = "/api/ade/agents"
T = A + "/tasks"
PASSWORD = "correct horse battery"
# the workspace's accounts in these tests: each 12 hex, as the workspace names an added account
ACC = dict(zip("ABCDEFGHIJ", ("a" * 12, "b" * 12, "c" * 12, "d" * 12, "e" * 12, "f" * 12, "1" * 12, "2" * 12,
                              "3" * 12, "4" * 12)))
HOUR = 3600


# ---- the tables ---------------------------------------------------------------------

TABLES = {
    "ade_agent_tasks": ("id", "user_id", "agent_id", "title", "goal", "goal_digest", "protected", "external", "repo",
                        "repo_slug", "repo_address", "checkpoint0", "state", "cause", "reason", "waits_until",
                        "state_version", "fence", "lease_run", "step", "backend", "asked", "home_grp", "bound_org",
                        "crossings_ended", "between_steps", "relay", "auto_resume", "cross_groups", "taken_out",
                        "resumed", "moves", "custody_digest", "custody_cross", "continued_from", "returned",
                        "created_at", "updated_at", "ended_at", "deleted_at"),
    "ade_agent_task_steps": ("task_id", "n", "user_id", "objective", "check_cmd", "state", "evidence",
                             "checkpoint_id", "attempts", "reopened"),
    "ade_agent_task_decisions": ("task_id", "n", "user_id", "text", "author", "run_id", "at"),
    "ade_agent_checkpoints": ("id", "user_id", "task_id", "step", "n", "kind", "run_id", "fence", "manifest", "digest",
                              "state", "why", "created_at"),
    "ade_agent_task_events": ("seq", "user_id", "task_id", "kind", "run_id", "fence", "at", "payload"),
    "ade_agent_effects": ("id", "user_id", "task_id", "run_id", "fence", "call_id", "tool", "kind", "target", "intent",
                          "state", "exit_status", "resolved", "resolved_by", "resolved_at", "note", "unseen",
                          "created_at", "updated_at"),
    "ade_agent_packets": ("run_id", "user_id", "task_id", "packet", "digest", "created_at"),
    "ade_account_owners": ("id", "user_id", "account", "name", "report", "auth_status", "org_id", "mismatch",
                           "reported_at", "state", "confirmed", "pending", "hold_id", "confirmed_at", "grp", "grp_kind",
                           "position", "in_relays", "reserve_lifted", "ready", "ready_at", "last_start_at",
                           "worked_at"),
    "ade_backend_windows": ("user_id", "backend", "rate_window", "status", "resets_at", "utilization",
                            "overage_status", "overage_resets_at", "overage_disabled_reason", "at", "seen_at",
                            "run_id", "lifted_at", "paid"),
    "ade_agent_task_offers": ("id", "user_id", "task_id", "teammate_user", "address", "kind", "state", "fence",
                              "packet", "packet_digest", "repo_address", "branch", "commit", "checks_adopted",
                              "their_task", "hold_id", "why", "created_at", "updated_at", "expires_at", "pushed",
                              "secret_findings", "kept_at", "reduce_requested_at", "press_dropped_at", "reread"),
    "ade_task_custody": ("id", "owner_user", "custodian_user", "address", "state", "confirmed_at", "why", "agents",
                         "ended_by", "ended_at", "reduced_at", "created_at", "updated_at", "reduce_requested_at"),
    "ade_task_account_record": ("seq", "user_id", "task_id", "agent_id", "repo_address", "branch", "title", "kind",
                                "run_id", "fence", "step", "via", "accounts", "at", "reduced_at", "statement",
                                "statement_at"),
    "ade_secret_lifts": ("user_id", "repo_address", "finding", "task_id", "offer_id", "kind", "shape", "at"),
}
RUN_COLUMNS = ("task_id", "task_step", "task_attempt", "fence", "backend", "packet_digest", "resume", "served_model",
               "chosen_by", "claude_version")


def test_every_task_table_is_made_with_its_columns(env):
    with store.connect() as db:
        for table, columns in TABLES.items():
            assert tuple(r[1] for r in db.execute(f"PRAGMA table_info({table})")) == columns, table
        runs = [r[1] for r in db.execute("PRAGMA table_info(ade_agent_runs)")]
        assert set(RUN_COLUMNS) <= set(runs)
        indexes = {r[1] for r in db.execute("SELECT * FROM sqlite_master WHERE type = 'index'")}
        assert {"ade_agent_runs_task", "ade_account_owners_account", "ade_agent_task_events_task",
                "ade_agent_effects_task", "ade_task_account_record_task", "ade_task_account_record_agent"} <= indexes
        # an account is the user's once: the uniqueness is an index, which a later release can replace
        unique = db.execute("SELECT sql FROM sqlite_master WHERE name = 'ade_account_owners_account'").fetchone()[0]
        assert unique.startswith("CREATE UNIQUE INDEX")


def test_an_older_store_gets_the_run_columns_and_their_index(env, tmp_path):
    path = tmp_path / "old.db"
    with store.connect(str(path)):
        pass
    db = sqlite3.connect(str(path))
    db.execute("DROP INDEX ade_agent_runs_task")
    for column in RUN_COLUMNS:
        db.execute(f"ALTER TABLE ade_agent_runs DROP COLUMN {column}")
    db.commit()
    db.close()
    with store.connect(str(path)) as again:
        runs = [r[1] for r in again.execute("PRAGMA table_info(ade_agent_runs)")]
        assert all(runs.count(c) == 1 for c in RUN_COLUMNS)
        assert again.execute("SELECT 1 FROM sqlite_master WHERE name = 'ade_agent_runs_task'").fetchone()


# ---- the fake workspace, and other modules' sides -----------------------------------------------------------------

# the push's words, faked where push.WORDS lacks them; push.WORDS wins once it has them
WORDS = {
    "task-continued": ("A task of yours continued", "Open Agents.", "/app/#agents"),
    "task-relayed": ("A task of yours continued on another of your accounts", "Open Agents.", "/app/#agents"),
    "task-crossed": ("A task of yours continued in another organisation", "Open Agents.", "/app/#agents"),
    "task-needs-you": ("A task of yours needs a decision", "Open Agents.", "/app/#agents"),
    "held-account": ("An account waits to join your tasks' relay", push.WAITS, "/app/"),
    "held-task-cross": ("A task waits to cross organisations again", push.WAITS, "/app/#agents"),
}


class Fake:
    """The workspace as the app's tests see it: what the app asks of it, each
    call kept; the attempts it was sent; the identity each account reports; and the pushes the app told."""

    def __init__(self):
        self.calls, self.sent, self.pushes = [], [], []
        self.identities = {}
        self.mismatches = {}            # the mismatch the workspace's own read names, by account (default: none)
        self.backends = []
        self.attempts = None
        self.worktree = {"head": "e" * 40, "tree": "f" * 40, "record_seq": 7}
        self.stop_cause = None          # the cause a stopped run's record carries
        self.on_stop = None             # a test's own answer to a stop: given the run id, its view, or it raises
        # the repositories as an older workspace lists them, before each had its address and slug
        self.repos = [{"name": "demo", "label": "workspace/demo", "branch": "main", "head": "a" * 40, "linked": None,
                       "origin": True, "github": True}]
        # the account new sessions start on, as the workspace's GET /accounts says it (Claude Code's `active`); None:
        # the workspace does not answer; an error: the workspace's refusal, raised
        self.starts_on = "default"
        # a test's own change to the app's records while the workspace is asked, by path, made once
        self.meanwhile = {}

    def call(self, endpoint, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        if path in self.meanwhile:
            self.meanwhile.pop(path)()
        if path == "/repos" and method == "GET":
            if self.repos is None:
                raise workspace_client.NotAnswering(0, workspace_client.NOT_ANSWERING)
            return {"repos": [dict(r) for r in self.repos]}
        if path == "/accounts?status=0" and method == "GET":
            if self.starts_on is None:
                raise workspace_client.NotAnswering(0, workspace_client.NOT_ANSWERING)
            if isinstance(self.starts_on, Exception):
                raise self.starts_on
            return {"claude": {"active": self.starts_on, "accounts": []}, "codex": {"active": "default", "accounts": []}}
        if path == "/agents/identity":
            report, status = self.identities[body["account"]]
            return {"report": report, "auth_status": status, "org_id": (status or {}).get("orgId"),
                    "mismatch": self.mismatches.get(body["account"])}
        if path == "/agents/backends":
            return {"backends": self.backends}
        if re.fullmatch(r"/agents/tasks/[0-9a-f]{32}/attempts", path):
            if self.attempts is not None:
                return {"attempts": self.attempts}
            return {"attempts": [{"run_id": b["run_id"], "fence": b["task"]["fence"], "state": "queued"}
                                 for b in self.sent if "task" in b]}
        if re.fullmatch(r"/agents/tasks/[0-9a-f]{32}/worktree", path):
            return dict(self.worktree)
        if method == "DELETE":
            return {"removed": True}
        if re.fullmatch(r"/agents/[0-9a-f]{12}/runs", path):
            self.sent.append(body)
            return {"run_id": body["run_id"], "agent_id": path.split("/")[2], "state": "queued"}
        stop = re.fullmatch(r"/agents/runs/([0-9a-f]{12})/stop", path)
        if stop:
            if self.on_stop is not None:
                return self.on_stop(stop[1])
            with store.connect() as db:
                agent_id = db.execute("SELECT agent_id FROM ade_agent_runs WHERE run_id = ?", (stop[1],)).fetchone()[0]
            now = int(time.time())
            return {"run_id": stop[1], "agent_id": agent_id, "state": "stopped", "cause": self.stop_cause,
                    "started_at": now, "ended_at": now}
        if path.startswith("/agents"):
            return {}
        raise AssertionError(f"the app asked the workspace {method} {path}")

    def kinds(self):
        """The kind of each notice told, by its fixed title (push.WORDS)."""
        return [next(k for k, w in push.WORDS.items() if w[0] == m.title) for m in self.pushes]


@pytest.fixture
def ws(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    for name in ("RELAY", "AUTO_RESUME", "TEAMMATE", "CROSS_GROUPS", "ACCOUNT_HOLD", "CROSS_HOLD"):
        monkeypatch.delenv("OPEN_EDITION_ADE_TASK_" + name, raising=False)
    monkeypatch.delenv(tasks.RECORD_DAYS_SETTING, raising=False)
    # the relay on, as a deployment that sets it: its default is off unless set, and these tests are of the relay at
    # work; the tests of the default delete the setting again
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "on")
    fake = Fake()
    monkeypatch.setattr(workspace_client, "call", fake.call)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace.test")
    monkeypatch.setattr(agents, "_deliver_soon", lambda user, start=False: False)
    monkeypatch.setattr(push, "WORDS", dict(WORDS, **push.WORDS))
    monkeypatch.setattr(push, "notice", lambda user_id, message, now=None, post=None: fake.pushes.append(message) or 1)
    # task_offers' side, faked: no custodian, and nothing of its own to prune
    monkeypatch.setattr(task_offers, "custody_candidate", lambda db, user_id, task_id, now: None)
    monkeypatch.setattr(task_offers, "forget", lambda db, now: None)
    return fake


@pytest.fixture
def owner(client, ws):
    h = signed_up(client)
    return h, auth.user(client.get("/api/auth/me", headers=h).json()["id"])


def own(user, account, plan="Claude Max", org=None, org_name=None, email=None, state="confirmed"):
    """One of the workspace's accounts, confirmed as the user's own (its row as a confirmation's hold leaves it)."""
    group = shapes.group_of(plan, org)
    report = {"email": email or account + "@example.com", "organization": org_name if org else None, "plan": plan,
              "token_source": None, "api_key_source": None, "api_provider": "firstParty"}
    with store.connect() as db:
        have = db.execute("SELECT id FROM ade_account_owners WHERE user_id = ? AND account = ?",
                          (user["id"], account)).fetchone()
        if have is not None:
            return have["id"]                           # once per account, as the store keeps it
        position = db.execute("SELECT COUNT(*) FROM ade_account_owners WHERE user_id = ?", (user["id"],)).fetchone()[0]
        owner_id = secrets.token_hex(16)
        db.execute("INSERT INTO ade_account_owners (id, user_id, account, name, report, confirmed, org_id, state, "
                   "confirmed_at, grp, grp_kind, position, in_relays, reserve_lifted, reported_at) VALUES "
                   "(?,?,?,?,?,?,?,?,1,?,?,?,1,0,1)",
                   (owner_id, user["id"], account, "acct " + account[:2], json.dumps(report), json.dumps(report), org,
                    state, group[0] if group else None, group[1] if group else None, position))
    return owner_id


def make_agent(client, h, account=None):
    body = {"name": "Worker", "brief": "Do the task.", "repo": "demo"}
    if account is not None:
        body["account"] = account
    r = client.post(A, headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def make_task(client, h, agent_id, steps=2, **over):
    body = dict({"agent_id": agent_id, "title": "Count lines", "goal": "Make the file two lines long.",
                 "steps": [{"objective": f"Append line {i}", "check": f"test $(wc -l < f) -ge {i}"}
                           for i in range(1, steps + 1)]}, **over)
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
    return client.post(f"{T}/{task_id}/{action}", headers=h, json=body)


def row(task_id):
    with store.connect() as db:
        return tasks._as_dict(db.execute("SELECT * FROM ade_agent_tasks WHERE id = ?", (task_id,)).fetchone())


def run(run_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone())


def runs_of(task_id):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_agent_runs WHERE task_id = ? ORDER BY fence",
                                            (task_id,)).fetchall()]


def events(task_id, kind=None):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_agent_task_events WHERE task_id = ? ORDER BY seq", (task_id,)).fetchall()
    return [dict(r, payload=json.loads(r["payload"])) for r in rows if kind is None or r["kind"] == kind]


def cp(n, step, kind="step", check=0, protected=(), note=None, left_out=()):
    """A checkpoint entry of a run's record."""
    return {"n": n, "step": step, "kind": kind, "state": "complete", "parent": None, "head": "%040x" % (n + 1),
            "tree": "%040x" % (n + 1000), "check": check, "check_tail": "", "protected": list(protected),
            "memory_digest": None, "record_seq": n, "made_at": int(time.time()),
            "note": {"by": "model", "text": note} if note is not None else None, "left_out": list(left_out)}


def view_of(run_id, state="working", cause=None, reason=None, limits=None, top=None, **fields):
    """A run's record as the workspace's view gives it."""
    r = run(run_id)
    now = int(time.time())
    view = {"run_id": run_id, "agent_id": r["agent_id"], "state": state, "cause": cause, "reason": reason,
            "started_at": now, "ended_at": now if state in agents.ENDED else None, "rate_limits": limits or {},
            "task": dict({"id": r["task_id"], "fence": r["fence"], "step": r["task_step"],
                          "attempt": r["task_attempt"], "backend": json.loads(r["backend"])}, **fields)}
    view.update(top or {})
    return view


def record(user, run_id, state="working", cause=None, reason=None, limits=None, top=None, **fields):
    """A run's record as the workspace's view gives it, copied by the app's own poll (agents._apply)."""
    view = view_of(run_id, state, cause, reason, limits, top, **fields)
    agents._apply(user["id"], [view], record_seq=False)
    return view


def finish(user, run_id, n, step, check=0, **fields):
    """The attempt completed its step: its step's checkpoint, and its end."""
    return record(user, run_id, "done", checkpoints=[cp(n, step, check=check)], **fields)


def refuse(user, run_id, resets=None, window="five_hour", **fields):
    """The attempt's account refused it at its first answer: its window rejected, the run blocked (rate_limit)."""
    limits = {window: {"status": "rejected", "resets_at": resets, "at": int(time.time())}}
    return record(user, run_id, "blocked", cause="rate_limit", limits=limits, **fields)


def allowed(window="five_hour"):
    return {window: {"status": "allowed", "resets_at": int(time.time()) + 5 * HOUR, "at": int(time.time())}}


def backend_of(run_id):
    return json.loads(run(run_id)["backend"])


def acct(run_id):
    return backend_of(run_id).get("id")


def confirmation(user, subject):
    return passkeys.confirm(user, password=PASSWORD, subject=subject)["confirmation"]


# ---- fences, the relay's order, task.continued and its push, Continue on..., Next on..., done -----------------------

def test_a_task_moves_on_its_steps_and_the_relay_moves_it_to_the_next_account(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    own(user, ACC["B"])
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id)
    assert task["backend"] == {"kind": "account", "id": ACC["A"]} and task["state"] == "ready"
    assert press(client, h, task["id"], "start").status_code == 200
    first = row(task["id"])["lease_run"]
    assert acct(first) == ACC["A"] and run(first)["fence"] == 1 and run(first)["chosen_by"] == "owner"
    record(user, first, "working", limits=allowed())
    finish(user, first, 1, 1)
    second = row(task["id"])["lease_run"]
    assert second != first and run(second)["fence"] == 2 and acct(second) == ACC["A"]
    assert run(second)["chosen_by"] == "next_step" and run(second)["task_step"] == 2
    refuse(user, second, resets=int(time.time()) + 2 * HOUR)
    third = row(task["id"])["lease_run"]
    assert run(third)["fence"] == 3 and acct(third) == ACC["B"] and run(third)["chosen_by"] == "relay"
    moved = events(task["id"], "task.continued")[-1]["payload"]
    assert moved == {"from": {"kind": "account", "id": ACC["A"]}, "to": {"kind": "account", "id": ACC["B"]},
                     "by": "relay"}
    assert ws.kinds() == ["task-relayed"]
    finish(user, third, 2, 2)
    done = task_of(client, h, task["id"])
    assert done["state"] == "done" and [s["state"] for s in done["steps"]] == ["done", "done"]
    assert events(task["id"], "task.done")[-1]["payload"] == {"by": "checkpoint"}
    assert [a["fence"] for a in done["attempts"]] == [1, 2, 3]


def test_pressed_after_a_step_continue_on_b_and_while_it_works_next_on_b(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    own(user, ACC["B"])
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, between_steps="wait")
    press(client, h, task["id"], "start")
    finish(user, row(task["id"])["lease_run"], 1, 1)
    waiting = task_of(client, h, task["id"])
    assert waiting["state"] == "suspended" and waiting["cause"] == "step_done" and waiting["step"] == 2
    r = press(client, h, task["id"], "continue-on", backend={"kind": "account", "id": ACC["B"]})
    assert r.status_code == 200 and r.json()["queued"] is True
    pressed = row(task["id"])
    assert pressed["backend"]["id"] == ACC["B"] and acct(pressed["lease_run"]) == ACC["B"]
    assert run(pressed["lease_run"])["chosen_by"] == "owner"
    assert events(task["id"], "task.continued")[-1]["payload"]["by"] == "owner"
    # Next on... while step 1 of another task works: its next attempt is on the account asked for
    other_agent = make_agent(client, h, ACC["A"])
    other = make_task(client, h, other_agent)
    press(client, h, other["id"], "start")
    assert press(client, h, other["id"], "next-on", backend={"kind": "account", "id": ACC["B"]}).status_code == 200
    finish(user, row(other["id"])["lease_run"], 1, 1)
    after = row(other["id"])
    assert acct(after["lease_run"]) == ACC["B"] and after["asked"] is None and after["backend"]["id"] == ACC["B"]


# ---- a key, pressed with a cap -------------------------------------------------------------------------------------

def test_a_key_needs_its_cap_serves_one_attempt_and_is_nowhere_in_the_app(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, between_steps="wait")
    press(client, h, task["id"], "start")
    finish(user, row(task["id"])["lease_run"], 1, 1)
    key = {"kind": "key", "provider": "anthropic"}
    refused = press(client, h, task["id"], "continue-on", backend=key)
    assert refused.status_code == 400 and "cap" in refused.json()["detail"]
    assert press(client, h, task["id"], "continue-on", backend=key, cap_usd=5000).status_code == 400
    assert press(client, h, task["id"], "continue-on", backend=key, cap_usd=5).status_code == 200
    on_key = row(task["id"])["lease_run"]
    assert backend_of(on_key) == key and json.loads(run(on_key)["trigger"])["cap_usd"] == 5
    assert row(task["id"])["backend"] == {"kind": "account", "id": ACC["A"]}       # a key is for one attempt
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send_now(user, "http://workspace.test")
    message = ws.sent[-1]["task"]
    assert message["backend"] == key and message["cap_usd"] == 5 and message["identity"] is None
    # what the workspace reports of the key's start (nothing the check below looks for once reached the app): the
    # initialize answer's account names the credential's source and no account (task_attempt.report_of), and the start
    # message reports apiKeyHelper (task_attempt._init)
    start = {"report": {"email": None, "organization": None, "plan": None, "token_source": None,
                        "api_key_source": "apiKeyHelper", "api_provider": "firstParty"},
             "api_key_source": "apiKeyHelper", "org_id": None, "failure": None}
    record(user, on_key, "working", start=start)
    finish(user, on_key, 2, 2, start=start)
    with store.connect() as db:
        entry = [e for e in tasks.account_records(db, user["id"])[0]["entries"] if e["run_id"] == on_key][0]
        dump = "\n".join(db.iterdump())
    assert entry["accounts"] == [{"ref": key, "vendor": "Anthropic", "label": "Anthropic API key"}]
    # no key, nor any credential, is anywhere in the app's store: a key's attempt names its provider alone
    assert "sk-ant" not in dump and "apiKeyHelper" not in dump


def test_after_the_key_attempt_the_next_is_on_the_task_s_own_account(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, steps=3, between_steps="wait")
    press(client, h, task["id"], "start")
    finish(user, row(task["id"])["lease_run"], 1, 1)
    press(client, h, task["id"], "continue-on", backend={"kind": "key", "provider": "anthropic"}, cap_usd=3)
    record(user, row(task["id"])["lease_run"], "working")
    finish(user, row(task["id"])["lease_run"], 2, 2)
    assert press(client, h, task["id"], "continue").status_code == 200
    assert backend_of(row(task["id"])["lease_run"]) == {"kind": "account", "id": ACC["A"]}


# ---- the packet ----------------------------------------------------------------------------------------------------

SECRET = "gh" + "p_" + "q" * 36          # shaped like a GitHub token, built so that no literal token is in the source


def packet_of(run_id):
    with store.connect() as db:
        kept = db.execute("SELECT packet, digest FROM ade_agent_packets WHERE run_id = ?", (run_id,)).fetchone()
    return bytes(kept["packet"]), kept["digest"]


def started(client, h, user, account="A", **task_over):
    own(user, ACC[account])
    agent_id = make_agent(client, h, ACC[account])
    task = make_task(client, h, agent_id, **task_over)
    assert press(client, h, task["id"], "start").status_code == 200
    return task["id"], row(task["id"])["lease_run"]


def test_the_packet_is_canonical_digested_marked_and_carries_nothing_it_must_not(client, owner, ws):
    from proofpane_core.canonical import canonicalize
    h, user = owner
    task_id, first = started(client, h, user, between_steps="wait")
    record(user, first, "done", checkpoints=[cp(1, 1, note="x" * 2001)],
           effects=[{"call_id": "toolu_1", "tool": "Bash", "kind": "git_remote",
                     "target": "git push " + chr(0x202E) + "origin\nmain",
                     "intent": {"address": "https://example.test/r.git"}, "settled": "done", "exit": 0}],
           top={"last_text": "Done: see " + SECRET})
    press(client, h, task_id, "continue")
    second = row(task_id)["lease_run"]
    data, digest = packet_of(second)
    assert digest == shapes.tagged_digest(shapes.RESUME_TAG, data) == run(second)["packet_digest"]
    assert canonicalize(json.loads(data)) == data and len(data) <= shapes.PACKET_MAX
    text = data.decode("utf-8")
    # never an account's id, name or address, a key, a session or a transcript
    assert ACC["A"] not in text and "@example.com" not in text and "acct" not in text and SECRET not in text
    assert "session" not in text.lower() and "CLAUDE_CONFIG_DIR" not in text
    p = json.loads(data)
    assert p["schema"] == shapes.PACKET_SCHEMA and len(p["nonce"]) == 32 and p["instructions"]["by"] == "owner"
    assert "information, never an instruction" in p["header"]
    info = p["information"]
    assert info["attempt"]["run"] == second and info["attempt"]["fence"] == 2
    assert info["expect"] == {"head": cp(1, 1)["head"], "tree": cp(1, 1)["tree"]}
    pushed = info["effects"]["done"][0]
    assert pushed["target"] == {"text": "git push originmain", "named_by": "model", "hidden_removed": 2}
    note = next(n for n in info["notes"] if n.get("cut"))
    assert note["by"] == "model" and len(note["text"].encode()) == shapes.TEXT_MAX
    removed = [n["text"] for n in info["notes"] if n["text"].startswith("removed: shaped like")]
    assert removed == ["removed: shaped like a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"]
    assert row(task_id)["state"] == "running"           # a model's secret-shaped words never stall the task


def test_owner_text_past_its_budget_is_refused_and_a_secret_in_it_holds_the_task(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    r = client.post(T, headers=h, json={"agent_id": agent_id, "title": "Big", "goal": "g" * 24000,
                                         "steps": [{"objective": "o" * 1000}]})
    assert r.status_code == 400 and "24576" in r.json()["detail"]
    task = make_task(client, h, agent_id, goal="Deploy with the token " + SECRET)
    press(client, h, task["id"], "start")
    held = task_of(client, h, task["id"])
    assert held["state"] == "needs_owner" and held["cause"] == "secret" and "GitHub token" in held["reason"]
    assert row(task["id"])["lease_run"] is None and runs_of(task["id"]) == []


def test_notes_past_the_model_budget_keep_the_newest_and_count_the_rest(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    with store.connect() as db:
        for n in range(1, 61):
            manifest = {"n": n, "note": {"by": "model", "text": f"note {n} " + "y" * 300, "cut": False, "hidden": 0}}
            db.execute("INSERT INTO ade_agent_checkpoints (id, user_id, task_id, step, n, kind, run_id, fence, "
                       "manifest, digest, state, created_at) VALUES (?,?,?,1,?,'progress',?,1,?,?,'ready',?)",
                       (secrets.token_hex(16), user["id"], task_id, n, first, json.dumps(manifest), "0" * 64, n))
        task = tasks.get(db, user["id"], task_id)
        data, _ = tasks.build_packet(db, task, "0" * 12, 2, 2, int(time.time()))
    model = json.loads(data)["information"]
    kept = [int(n["text"].split()[1]) for n in model["notes"]]
    assert kept == sorted(kept, reverse=True) and kept[0] == 60 and len(kept) + model["dropped_notes"] == 60
    assert model["dropped_notes"] > 0 and len(json.dumps(model["notes"])) <= shapes.PACKET_BUDGETS["model"]


def test_outward_effects_past_their_budget_put_the_task_to_the_owner_none_dropped(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user, between_steps="wait")
    effects = [{"call_id": f"toolu_{i}", "tool": "Bash", "kind": "git_remote", "target": f"git push origin b{i} " +
                "z" * 250, "intent": {}, "settled": "done", "exit": 0} for i in range(60)]
    finish(user, first, 1, 1, effects=effects)
    press(client, h, task_id, "continue")
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "packet" and len(held["effects"]) == 60
    assert press(client, h, task_id, "continue").status_code == 412


# ---- a refused window, the reset, and auto-resume -------------------------------------------------------------------

def test_suspended_until_the_reset_then_one_automatic_resume_and_no_second_for_it(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    reset = int(time.time()) + 2 * HOUR
    refuse(user, first, resets=reset)
    waiting = task_of(client, h, task_id)
    assert waiting["state"] == "suspended" and waiting["cause"] == "window" and waiting["waits_until"] == reset
    tasks.sweep(now=reset - 1)
    assert row(task_id)["lease_run"] is None
    tasks.sweep(now=reset)                              # at the reset itself, not a second later
    resumed = row(task_id)["lease_run"]
    assert resumed and acct(resumed) == ACC["A"] and run(resumed)["chosen_by"] == "auto_resume"
    assert events(task_id, "task.continued")[-1]["payload"]["by"] == "auto_resume" and ws.kinds() == ["task-continued"]
    refuse(user, resumed, resets=reset)                 # refused again at once, for the same reset
    again = task_of(client, h, task_id)
    assert again["state"] == "needs_owner" and again["cause"] == "window"
    tasks.sweep(now=reset + 2)
    assert len(runs_of(task_id)) == 2


@pytest.mark.parametrize("off", ["setting", "switch"])
def test_no_automatic_resume_with_auto_resume_off(client, owner, ws, monkeypatch, off):
    h, user = owner
    task_id, first = started(client, h, user)
    reset = int(time.time()) + HOUR
    if off == "setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", "off")
    else:
        assert press(client, h, task_id, "switches", auto_resume=False).status_code == 200
    refuse(user, first, resets=reset)
    tasks.sweep(now=reset + 1)
    assert row(task_id)["state"] == "suspended" and len(runs_of(task_id)) == 1


def test_a_reset_not_later_than_the_clock_puts_the_task_to_the_owner(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    refuse(user, first, resets=int(time.time()) - 5)
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "window"


# ---- a refusal with no reset; the seven-day window -------------------------------------------------------------------

def test_a_refusal_with_no_reset_holds_the_task_and_the_backend_until_an_allowed_event(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    refuse(user, first, resets=None)
    assert task_of(client, h, task_id)["cause"] == "window"
    now = int(time.time())
    with store.connect() as db:
        assert tasks.held_for_schedules(db, user["id"], ACC["A"], now).startswith("held: its account's window")
        assert tasks.held_for_schedules(db, user["id"], ACC["B"], now) is None
    tasks.sweep(now=now + 10 * HOUR)
    assert len(runs_of(task_id)) == 1                   # nothing continues it by itself
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"], {"five_hour": {"status": "allowed",
                                                                                   "resets_at": now + HOUR,
                                                                                   "at": now + 1}}, None, now + 1)
        assert tasks.held_for_schedules(db, user["id"], ACC["A"], now + 2) is None


def test_a_refused_seven_day_window_holds_the_backend_whatever_the_five_hour_says(owner):
    _, user = owner
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"], {
            "five_hour": {"status": "allowed", "resets_at": now + HOUR, "at": now},
            "seven_day": {"status": "rejected", "resets_at": now + 3 * 86400, "at": now}}, None, now)
        assert tasks.backend_state(db, user["id"], "account:" + ACC["A"], now) == (False, now + 3 * 86400)
        assert tasks.held_for_schedules(db, user["id"], ACC["A"], now).startswith("held until")


# ---- the relay off --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("off", ["setting", "switch", "default"])
def test_with_the_relay_off_a_refusal_waits_for_its_reset_and_nothing_goes_to_a_custodian(client, owner, ws,
                                                                                           monkeypatch, off):
    """The relay off by the deployment's setting, by the task's switch, or left unset, its default, where a task made
    meanwhile starts with its switch off: a refusal waits for its reset although B could take it (B has reported no
    window: an account the relay would try), nothing goes to a custodian, and Continue on... still moves the task to
    B."""
    h, user = owner
    asked = []
    monkeypatch.setattr(task_offers, "custody_candidate", lambda db, user_id, task_id, now: asked.append(task_id))
    own(user, ACC["B"])
    if off == "default":
        monkeypatch.delenv("OPEN_EDITION_ADE_TASK_RELAY")
    task_id, first = started(client, h, user)
    if off == "setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "0")
    elif off == "switch":
        assert press(client, h, task_id, "switches", relay=False).status_code == 200
    else:
        assert task_of(client, h, task_id)["relay"] is False and row(task_id)["relay"] == 0
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    waiting = task_of(client, h, task_id)
    assert waiting["state"] == "suspended" and waiting["waits_until"] == reset and asked == []
    assert len(runs_of(task_id)) == 1
    r = press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["B"]})
    assert r.status_code == 200 and acct(row(task_id)["lease_run"]) == ACC["B"]


@pytest.mark.parametrize("off", ["setting", "switch"])
def test_with_crossing_off_either_way_a_refusal_crosses_nowhere_and_continue_on_still_takes_another_groups_seat(
        client, owner, ws, monkeypatch, off):
    """With the crossing switch off either way, the deployment's setting turned off after the task was made (its own
    switch still on) or the task's switch turned off, every account of the task's group refused leaves the task waiting
    for the earliest reset among them, as in the tests of a refused window, though a seat of another group has room:
    nothing crosses. The group's earlier reset is the first refusal's, not the last, and a seat of a third organisation,
    out with a reset sooner than both, has no say in the wait. The relay's step 2 is still asked, since custody keeps
    its own tick whatever the task's switch; and Continue on... still takes the other group's seat, which the relay now
    leaves out, since the switches govern the relay's automatic crossings alone and a press may always cross. "Continue
    on... offers all" is checked here: the press, as the relay-off case shows it too, and the accounts listing the page
    builds its list from, which still lists both other organisations' seats, confirmed (GET /accounts; AgentTask.tsx's
    BackendList offers every other account confirmed there, and reads neither the relay set nor a switch; the page's own
    tests open it with the task's switch on only)."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    asked = []
    monkeypatch.setattr(task_offers, "custody_candidate", lambda db, user_id, task_id, now: asked.append(task_id))
    task_id = crossing_setup(client, h, user)                          # on B; the seat A and C have room
    assert row(task_id)["cross_groups"] == 1
    own(user, ACC["D"], plan="Claude Team", org="org-y", org_name="Y")
    closed(user, ACC["D"], int(time.time()) + HOUR // 2)
    if off == "setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off")
    else:
        assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    sooner, later = int(time.time()) + HOUR, int(time.time()) + 2 * HOUR
    refuse(user, row(task_id)["lease_run"], resets=sooner)
    assert acct(row(task_id)["lease_run"]) == ACC["C"] and asked == []   # within the group first, as with it on
    refuse(user, row(task_id)["lease_run"], resets=later)
    waiting = task_of(client, h, task_id)
    assert waiting["state"] == "suspended" and waiting["cause"] == "window" and waiting["waits_until"] == sooner
    assert events(task_id, "task.crossed") == [] and len(runs_of(task_id)) == 2 and asked == [task_id]
    eligible = {e["account"]["account"]: e["eligible"] for e in waiting["relay_set"]}
    assert eligible[ACC["A"]] is False and eligible[ACC["D"]] is False
    listed = {a["account"]: a["state"] for a in client.get(A + "/accounts", headers=h).json()["accounts"]}
    assert listed == {ACC[k]: "confirmed" for k in "ABCD"}, listed
    r = press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["A"]})
    assert r.status_code == 200 and acct(row(task_id)["lease_run"]) == ACC["A"]
    assert row(task_id)["bound_org"] == "org-x"


# ---- a machine stop, and where the continuation starts ---------------------------------------------------------------

@pytest.mark.parametrize("action", ["keep", "discard"])
def test_a_machine_stop_needs_the_owner_and_the_continuation_starts_from_the_resolved_head(client, owner, ws,
                                                                                            action):
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "working", checkpoints=[cp(1, 1, kind="progress")])
    record(user, first, "blocked", cause="machine", end_state={"head": "c" * 40, "tree": "d" * 40, "committed": True})
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "worktree"
    assert press(client, h, task_id, "continue").status_code == 412
    # keep: the worktree as it is, its files the new end state; discard: back to the last checkpoint the app took
    ws.worktree = {"head": "e" * 40 if action == "keep" else cp(1, 1)["head"], "tree": "f" * 40, "record_seq": 9}
    r = press(client, h, task_id, "resolve", what="worktree", outcome=action)
    assert r.status_code == 200, r.text
    asked = [c for c in ws.calls if c[1].endswith("/worktree")][-1]
    # the workspace resets a discard to the head it is given, so that head is the newest checkpoint the app took and
    # never the stopped attempt's own end state, which holds its work; a keep commits the
    # worktree on its own head, and is given the end state's
    assert asked[2] == {"fence": 1, "action": action, "head": "c" * 40 if action == "keep" else cp(1, 1)["head"]}
    assert press(client, h, task_id, "continue").status_code == 200
    expect = json.loads(packet_of(row(task_id)["lease_run"])[0])["information"]["expect"]
    assert expect == {"head": ws.worktree["head"], "tree": "f" * 40}


def test_a_discard_with_no_checkpoint_taken_is_refused_before_the_workspace_is_asked(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="machine", end_state={"head": "c" * 40, "tree": "d" * 40, "committed": True})
    assert task_of(client, h, task_id)["cause"] == "worktree"
    r = press(client, h, task_id, "resolve", what="worktree", outcome="discard")
    assert r.status_code == 412 and "no checkpoint to go back to" in r.json()["detail"], r.text
    assert not [c for c in ws.calls if c[1].endswith("/worktree")]
    r = press(client, h, task_id, "resolve", what="worktree", outcome="keep")
    assert r.status_code == 200, r.text
    assert [c for c in ws.calls if c[1].endswith("/worktree")][-1][2]["head"] == "c" * 40


@pytest.mark.parametrize("how", ["resume check", "machine stop", "its own checkpoint"])
def test_a_discard_goes_back_to_the_head_recorded_when_the_held_attempt_was_sent(client, owner, ws, how):
    """A discard resets the worktree to the head the task had recorded when the attempt that holds it was sent:
    an earlier attempt's end state past the newest checkpoint, as that attempt's expect named it, whether it was held
    at its resume check on the worktree or the machine stopped under it (what it
    left is dropped); a checkpoint it took itself is newer than that end state, whatever second each was copied in."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    record(user, first, "working", checkpoints=[cp(1, 1, kind="progress")])
    record(user, first, "stopped", end_state={"head": "c" * 40, "tree": "d" * 40, "committed": True})
    assert (row(task_id)["state"], row(task_id)["cause"]) == ("suspended", "stopped")
    assert press(client, h, task_id, "continue").status_code == 200
    second = row(task_id)["lease_run"]
    assert json.loads(packet_of(second)[0])["information"]["expect"] == {"head": "c" * 40, "tree": "d" * 40}
    if how == "resume check":
        record(user, second, "blocked", cause="resume", resume={"outcome": "blocked", "check": "worktree",
                                                                 "found": "a file the task did not write"})
    else:
        if how == "its own checkpoint":
            record(user, second, "working", checkpoints=[cp(2, 1, kind="progress")])
        record(user, second, "blocked", cause="machine", end_state={"head": "9" * 40, "tree": "8" * 40,
                                                                     "committed": False})
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", "worktree")
    r = press(client, h, task_id, "resolve", what="worktree", outcome="discard")
    assert r.status_code == 200, r.text
    asked = [c for c in ws.calls if c[1].endswith("/worktree")][-1][2]
    assert asked == {"fence": 2, "action": "discard", "head": cp(2, 1)["head"] if how == "its own checkpoint"
                     else "c" * 40}


def test_checkpoint_0_is_the_first_record_s_base_and_a_discard_before_any_checkpoint_goes_back_to_it(client, owner,
                                                                                                    ws):
    # the workspace's records carry the commit it cut the task's worktree from as the run's base (checkpoint 0): an
    # owner's task learns it from its first attempt's record, once, and a discard before any other checkpoint goes
    # back to it (the first two kill points, end to end, found it never learnt)
    h, user = owner
    task_id, first = started(client, h, user)
    zero = "a1" * 20
    record(user, first, "working", top={"base": zero})
    assert row(task_id)["checkpoint0"] == zero
    record(user, first, "working", top={"base": "b2" * 20})
    assert row(task_id)["checkpoint0"] == zero
    record(user, first, "blocked", cause="machine", top={"base": zero},
           end_state={"head": "c" * 40, "tree": "d" * 40, "committed": True})
    assert task_of(client, h, task_id)["cause"] == "worktree"
    ws.worktree = {"head": zero, "tree": "f" * 40, "record_seq": 9}
    r = press(client, h, task_id, "resolve", what="worktree", outcome="discard")
    assert r.status_code == 200, r.text
    assert [c for c in ws.calls if c[1].endswith("/worktree")][-1][2] == {"fence": 1, "action": "discard",
                                                                          "head": zero}
    assert press(client, h, task_id, "continue").status_code == 200
    assert json.loads(packet_of(row(task_id)["lease_run"])[0])["information"]["expect"]["head"] == zero


@pytest.mark.parametrize("steps", [2, 1])
def test_a_step_done_before_the_machine_stopped_is_counted_and_the_continuation_is_the_next(client, owner, ws,
                                                                                           steps):
    # the last kill point: the step's record completed (its check passed), then the machine stopped before the
    # attempt ended. The task needs the owner, and once the worktree is resolved Continue starts the next step from
    # that step's checkpoint; its last step done, the task waits all the same, and Continue runs that step again
    h, user = owner
    task_id, first = started(client, h, user, steps=steps)
    record(user, first, "working", checkpoints=[cp(1, 1)])
    record(user, first, "blocked", cause="machine")
    held = row(task_id)
    assert (held["state"], held["cause"], held["step"]) == ("needs_owner", "worktree", 2 if steps == 2 else 1)
    ws.worktree = {"head": cp(1, 1)["head"], "tree": cp(1, 1)["tree"], "record_seq": 9}
    assert press(client, h, task_id, "resolve", what="worktree", outcome="keep").status_code == 200
    assert press(client, h, task_id, "continue").status_code == 200
    packet = json.loads(packet_of(row(task_id)["lease_run"])[0])
    assert packet["next"]["step"] == (2 if steps == 2 else 1)
    assert [s["n"] for s in packet["information"]["steps_done"]] == [1]
    assert packet["information"]["expect"]["head"] == cp(1, 1)["head"]


# ---- an unknown effect holds until Resolve; a done one is in the next packet ----------------------------------------

def test_an_unknown_effect_holds_until_resolved_and_a_done_one_is_in_the_next_packet(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    push_call = {"call_id": "toolu_9", "tool": "Bash", "kind": "git_remote", "target": "git push origin pp/task-x",
                 "intent": {"before": "0" * 40}, "settled": None}
    record(user, first, "working", effects=[push_call])
    assert task_of(client, h, task_id)["effects"] == []         # in flight: taken when it settles, or at the end
    refuse(user, first, resets=int(time.time()) + HOUR, effects=[push_call])
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "effect"     # nothing relayed while it is unknown
    assert len(runs_of(task_id)) == 1 and events(task_id, "effect.unknown")
    assert press(client, h, task_id, "continue").status_code == 412
    effect = held["effects"][0]
    assert effect["state"] == "unknown" and effect["resolved"] is None
    r = press(client, h, task_id, "resolve", what="effect", effect_id=effect["id"], outcome="happened",
              note="I saw it on the remote")
    assert r.status_code == 200 and r.json()["task"]["effects"][0]["resolved"] == "page"
    assert events(task_id, "effect.resolved")[-1]["payload"] == {"effect": effect["id"], "outcome": "happened",
                                                                 "by": "page"}
    assert press(client, h, task_id, "continue").status_code == 200
    done = json.loads(packet_of(row(task_id)["lease_run"])[0])["information"]["effects"]["done"]
    assert [d["id"] for d in done] == [effect["id"]] and done[0]["target"]["named_by"] == "model"


def test_an_unknown_effect_settled_later_by_the_workspace_is_resolved_by_it(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    call = {"call_id": "toolu_5", "tool": "Bash", "kind": "git_remote", "target": "git push", "settled": "unknown"}
    record(user, first, "working", effects=[call])
    record(user, first, "working", effects=[dict(call, settled="done")])
    effect = task_of(client, h, task_id)["effects"][0]
    assert effect["state"] == "done" and effect["resolved"] == "workspace"


# ---- late records ----------------------------------------------------------------------------------------------------

def test_a_late_record_and_one_at_another_fence_are_refused_and_change_nothing(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send_now(user, "http://workspace.test")
    ws.attempts = []                                    # the workspace does not know the attempt it was sent
    tasks.check_attempts(user, "http://workspace.test")
    assert run(first)["state"] == "failed" and task_of(client, h, task_id)["state"] == "suspended"
    press(client, h, task_id, "continue")
    second = row(task_id)["lease_run"]
    before = task_of(client, h, task_id)
    record(user, first, "working", checkpoints=[cp(1, 1)])            # attempt 1, late
    assert run(first)["flag"] and run(first)["state"] == "failed"
    rejected = events(task_id, "checkpoint.rejected")
    assert [e["payload"]["run"] for e in rejected] == [first]
    record(user, second, "working", checkpoints=[cp(1, 1)], fence=99)    # a fence that is not the task's
    assert run(second)["flag"] and [e["payload"]["run"] for e in events(task_id, "checkpoint.rejected")] == [first,
                                                                                                                second]
    assert run(second)["state"] == "working"            # the attempt's own state is still copied, as any run's is
    after = task_of(client, h, task_id)
    assert after["checkpoints"] == [] and after["steps"] == before["steps"] and after["state"] == "running"


# ---- two presses at once, and an old version -------------------------------------------------------------------------

def test_two_continue_presses_at_once_queue_one_attempt_and_an_old_version_is_refused(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "failed", reason="its own error")
    version = task_of(client, h, task_id)["state_version"]
    outcomes = []

    def go():
        try:
            tasks.continue_(user, task_id, version)
            outcomes.append(200)
        except agents.AgentError as e:
            outcomes.append(e.status)
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == [200, 409] and len(runs_of(task_id)) == 2
    stale = client.post(f"{T}/{task_id}/stop", headers=h, json={})
    assert stale.status_code == 200
    old = client.post(f"{T}/{task_id}/end", headers=h, json={"state_version": version, "outcome": "stopped"})
    assert old.status_code == 409 and old.json()["detail"] == "The task changed; look again."


# ---- a schedule is held by its own backend's window alone ----------------------------------------------------------

def schedule(client, h, agent_id):
    r = client.post(f"{A}/{agent_id}/triggers", headers=h, json={"kind": "schedule", "cron": "* * * * *", "tz": "UTC"})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_schedule_waits_for_its_own_account_s_reset_and_another_account_s_fires(client, owner, ws):
    h, user = owner
    on_a, on_b = make_agent(client, h, ACC["A"]), make_agent(client, h, ACC["B"])
    first, second = schedule(client, h, on_a), schedule(client, h, on_b)
    now = first["next_at"]
    reset = now + 600                                   # within the schedule's grace, so its next firing is not missed
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"],
                           {"five_hour": {"status": "rejected", "resets_at": reset, "at": now - 60}}, None, now - 60)
    fired = {t: (run_id, why) for t, run_id, why in triggers.tick(now=now)}
    assert fired[first["id"]][0] is None and fired[first["id"]][1].startswith("held until the plan's window resets")
    assert fired[second["id"]][0] is not None
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET state = 'done'")
    fired = {t: (run_id, why) for t, run_id, why in triggers.tick(now=reset + 60)}
    assert fired[first["id"]][0] is not None


# ---- an agent with no task -------------------------------------------------------------------------------------------

def test_an_agent_s_own_run_is_copied_as_before_and_its_windows_kept_under_its_account(client, owner, ws):
    h, user = owner
    agent_id = make_agent(client, h, ACC["C"])
    run_id = client.post(f"{A}/{agent_id}/runs", headers=h, json={}).json()["run_id"]
    now = int(time.time())
    view = {"run_id": run_id, "agent_id": agent_id, "state": "done", "started_at": now, "ended_at": now,
            "rate_limits": {"seven_day": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}}}
    agents._apply(user["id"], [view], record_seq=False)
    assert run(run_id)["state"] == "done" and run(run_id)["task_id"] is None
    with store.connect() as db:
        kept = db.execute("SELECT * FROM ade_backend_windows WHERE user_id = ?", (user["id"],)).fetchall()
        assert [(r["backend"], r["rate_window"], r["status"]) for r in kept] == [
            ("account:" + ACC["C"], "seven_day", "allowed_warning")]
        assert db.execute("SELECT COUNT(*) FROM ade_agent_task_events").fetchone()[0] == 0


# ---- each cause of needs_owner, and its remedy -----------------------------------------------------------------------

def _resolve_then(client, h, task_id, what, outcome="lift"):
    r = press(client, h, task_id, "resolve", what=what, outcome=outcome)
    assert r.status_code == 200, r.text


CAUSES = {
    # cause: (how the attempt ends, what the owner does before Continue)
    "branch": (dict(state="blocked", cause="branch"), lambda c, h, t: _resolve_then(c, h, t, "worktree", "keep")),
    # discard goes back to the newest checkpoint the app took: the attempt took checkpoint 0 before the machine stopped
    "worktree": (dict(state="blocked", cause="machine", checkpoints=[cp(0, 1, kind="zero")]),
                 lambda c, h, t: _resolve_then(c, h, t, "worktree", "discard")),
    "watch": (dict(state="blocked", cause="watch"), lambda c, h, t: _resolve_then(c, h, t, "watch")),
    "resume": (dict(state="blocked", cause="resume", resume={"outcome": "blocked", "check": "fence", "found": "7"}),
               None),
    "settings": (dict(state="blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
                      start={"failure": "settings", "report": None}), None),
    "window": (dict(state="blocked", cause="rate_limit",
                    limits={"five_hour": {"status": "rejected", "resets_at": None, "at": 1}}), None),
    "ungated": (dict(state="blocked", cause="ungated",
                     effects=[{"call_id": "toolu_u", "tool": "Bash", "kind": "unclassified", "target": "curl x",
                               "unseen": True, "settled": "unknown"}]), "effect"),
}


@pytest.mark.parametrize("cause", sorted(CAUSES))
def test_each_cause_needs_the_owner_and_its_remedy_leads_back_to_running(client, owner, ws, cause):
    h, user = owner
    task_id, first = started(client, h, user)
    how, remedy = CAUSES[cause]
    how = dict(how)
    record(user, first, how.pop("state"), cause=how.pop("cause"), limits=how.pop("limits", None), **how)
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == cause and held["reason"]
    if cause == "ungated":
        # the call it ended at ran, and the batch rule makes others ones that may have run
        assert held["reason"] == ("An attempt ran a tool call the gate did not see, and may have run others beside "
                                  "it: say whether each unseen call happened (Resolve), then Continue.")
    if remedy == "effect":
        assert press(client, h, task_id, "continue").status_code == 412
        effect = held["effects"][0]
        assert effect["unseen"] is True
        assert press(client, h, task_id, "resolve", what="effect", effect_id=effect["id"],
                     outcome="not_happened").status_code == 200
    elif remedy is not None:
        if cause in ("branch", "worktree"):
            assert press(client, h, task_id, "continue").status_code == 412
        remedy(client, h, task_id)
    if cause == "watch":
        with store.connect() as db:
            db.execute("UPDATE ade_agents SET paused = 0")
    assert press(client, h, task_id, "continue").status_code == 200
    assert task_of(client, h, task_id)["state"] == "running" and len(runs_of(task_id)) == 2


def test_a_watched_place_goes_on_at_one_continue_with_no_resolve_first(client, owner, ws):
    """The page's "I have looked: go on" is Continue alone, no resolve before it (the window's "It is open: go on"
    is the window case above). While the watch's hold keeps the agent paused, Continue is refused in the app's words,
    which the page says beside the task; once the agent is resumed, the same one press queues the next attempt."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="watch")
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "watch"
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET paused = 1")
    r = press(client, h, task_id, "continue")
    assert r.status_code == 412 and r.json()["detail"] == "This agent is paused: resume it first."
    assert task_of(client, h, task_id)["state"] == "needs_owner" and len(runs_of(task_id)) == 1
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET paused = 0")
    assert press(client, h, task_id, "continue").status_code == 200
    assert task_of(client, h, task_id)["state"] == "running" and len(runs_of(task_id)) == 2
    with store.connect() as db:
        kinds = [r[0] for r in db.execute("SELECT kind FROM ade_agent_task_events WHERE task_id = ? ORDER BY seq",
                                          (task_id,)).fetchall()]
    assert "task.continued" in kinds and "task.resolved" not in kinds


def test_a_packet_that_cannot_be_built_is_ended_not_continued(client, owner, ws):
    h, user = owner
    task_id, _ = started(client, h, user)
    with store.connect() as db:
        tasks._needs_owner(db, row(task_id), "packet", "Attempts made more outward effects than a packet carries.",
                           int(time.time()))
        db.execute("UPDATE ade_agent_tasks SET lease_run = NULL")
    assert press(client, h, task_id, "continue").status_code == 412
    r = press(client, h, task_id, "end", outcome="stopped")
    assert r.status_code == 200 and r.json()["task"]["state"] == "stopped"


# ---- which start failures are the account's own ----------------------------------------------------------------------

def other_report(email):
    return {"email": email, "organization": None, "plan": "Claude Max", "token_source": None,
            "api_key_source": None, "api_provider": "firstParty"}


def test_an_identity_unlike_the_confirmed_one_is_the_account_refusing_and_the_relay_moves_on(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": other_report("someone@else.test"), "org_id": None})
    after = row(task_id)
    assert after["state"] == "running" and acct(after["lease_run"]) == ACC["B"]
    with store.connect() as db:
        assert tasks._owner(db, user["id"], ACC["A"])["state"] == "changed"
    left = {e["account"]["account"]: e["left_out"] for e in task_of(client, h, task_id)["relay_set"]}
    assert left[ACC["A"]] == "its identity changed: confirm it again"


@pytest.mark.parametrize("failure,cause", [("same_everywhere", "resume"), ("settings", "settings")])
def test_a_failure_every_account_would_have_needs_the_owner_and_moves_nothing(client, owner, ws, failure, cause):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": failure, "report": None})
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == cause and len(runs_of(task_id)) == 1
    assert events(task_id, "task.continued") == []


def test_an_account_the_owner_pressed_that_fails_its_start_needs_the_owner(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    own(user, ACC["C"])
    task_id, first = started(client, h, user, between_steps="wait")
    finish(user, first, 1, 1)
    press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["B"]})
    pressed = row(task_id)["lease_run"]
    record(user, pressed, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": other_report("b@else.test")})
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "resume" and len(runs_of(task_id)) == 2


# ---- the relay set, its order, why each account is out, the reserve -------------------------------------------------

def room(user, *accounts):
    now = int(time.time())
    with store.connect() as db:
        for account in accounts:
            tasks.take_windows(db, user["id"], "account:" + account, {
                "five_hour": {"status": "allowed", "resets_at": now + 5 * HOUR, "at": now}}, None, now)


def test_the_relay_set_in_connections_order_names_why_each_account_is_out(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    own(user, ACC["B"], plan="Claude Pro")
    own(user, ACC["C"])
    own(user, ACC["D"], plan="Claude Team", org="org-x", org_name="X")
    own(user, ACC["E"], plan="Claude Pro", state="reported")
    own(user, ACC["F"], state="changed")
    own(user, ACC["G"], plan="Claude Pro")
    own(user, ACC["H"])
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["H"], {
            "seven_day": {"status": "allowed_warning", "resets_at": now + 3 * 86400, "at": now}}, None, now)
    room(user, ACC["A"], ACC["C"])
    agent_id = make_agent(client, h, ACC["B"])
    task = make_task(client, h, agent_id)
    left = {e["account"]["account"]: e["left_out"] for e in task["relay_set"]}
    assert left == {ACC["A"]: None, ACC["B"]: None, ACC["C"]: None,
                    ACC["D"]: "crossing organisations is off for this task", ACC["E"]: "not confirmed as yours",
                    ACC["F"]: "its identity changed: confirm it again", ACC["G"]: None,
                    ACC["H"]: "its weekly reserve"}
    press(client, h, task["id"], "start")
    order = []
    for _ in range(3):
        refuse(user, row(task["id"])["lease_run"], resets=int(time.time()) + 3 * HOUR)
        order.append(acct(row(task["id"])["lease_run"]))
    assert order == [ACC["C"], ACC["A"], ACC["G"]]          # after B, not back to the first; room before a probe
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET reserve_lifted = 1 WHERE account = ?", (ACC["H"],))
    refuse(user, row(task["id"])["lease_run"], resets=int(time.time()) + 3 * HOUR)
    assert acct(row(task["id"])["lease_run"]) == ACC["H"]


def test_the_relay_set_is_numbered_from_1_in_the_relay_s_order(client, owner, ws):
    """The page shows each account's `order` as it is, so the relay set is numbered
    from 1, in the relay's order, which is not Connections' where an account of another group sits between two of the
    task's (D, a Team seat, second in Connections, is listed last)."""
    h, user = owner
    own(user, ACC["A"])
    own(user, ACC["D"], plan="Claude Team", org="org-x", org_name="X")
    own(user, ACC["B"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    listed = [(e["account"]["account"], e["order"]) for e in task["relay_set"]]
    assert listed == [(ACC["A"], 1), (ACC["B"], 2), (ACC["D"], 3)]


# ---- probes, every account refused, the earliest reset ---------------------------------------------------------------

def test_with_every_account_refused_the_task_waits_for_the_account_open_soonest(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    now = int(time.time())
    a_week, b_reset = now + 4 * 86400, now + 3 * HOUR
    record(user, first, "blocked", cause="rate_limit", limits={
        "five_hour": {"status": "rejected", "resets_at": now + HOUR, "at": now},
        "seven_day": {"status": "rejected", "resets_at": a_week, "at": now}})
    on_b = row(task_id)["lease_run"]
    assert acct(on_b) == ACC["B"]                          # B, a probe, is tried at once
    record(user, on_b, "blocked", cause="rate_limit", limits={
        "five_hour": {"status": "rejected", "resets_at": b_reset, "at": now},
        "seven_day": {"status": "rejected", "resets_at": b_reset, "at": now}})
    waiting = row(task_id)
    assert waiting["state"] == "suspended" and waiting["waits_until"] == b_reset      # A is open only next week
    tasks.sweep(now=b_reset + 1)
    assert acct(row(task_id)["lease_run"]) == ACC["B"] and len(runs_of(task_id)) == 3


@pytest.fixture
def clock(monkeypatch):
    """The app's clock, injected: what time.time() says, wherever the app reads it."""
    now = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


def test_an_account_refusing_again_for_the_same_reset_moves_the_relay_on(client, owner, ws, clock):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    reset = clock[0] + HOUR
    refuse(user, first, resets=reset)
    refuse(user, row(task_id)["lease_run"], resets=reset)              # B too, for the same time
    assert row(task_id)["waits_until"] == reset
    clock[0] = reset + 1
    tasks.sweep(now=reset + 1)
    resumed = row(task_id)["lease_run"]
    refuse(user, resumed, resets=reset)                     # the account it resumed on refuses again at once
    after = row(task_id)
    other = ACC["B"] if acct(resumed) == ACC["A"] else ACC["A"]
    assert after["state"] == "running" and acct(after["lease_run"]) == other
    refuse(user, after["lease_run"], resets=reset)
    assert row(task_id)["state"] == "needs_owner"


# ---- paid usage -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("reset", [True, False])
def test_an_attempt_ended_on_paid_usage_records_the_window_refused_and_the_relay_moves_on(client, owner, ws,
                                                                                           reset):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    now = int(time.time())
    resets = now + 2 * HOUR if reset else None
    record(user, first, "blocked", cause="rate_limit", reason="paid usage", paid_usage=True, limits={
        "five_hour": {"status": "allowed", "resets_at": resets, "at": now, "overage_status": "allowed",
                      "overage_resets_at": None, "overage_disabled_reason": None}})
    with store.connect() as db:
        kept = db.execute("SELECT * FROM ade_backend_windows WHERE backend = ? AND rate_window = 'five_hour'",
                          ("account:" + ACC["A"],)).fetchone()
        assert kept["status"] == "rejected" and kept["overage_status"] == "allowed"
        assert kept["resets_at"] == resets
        assert tasks.held_for_schedules(db, user["id"], ACC["A"], now + 1) is not None
    assert acct(row(task_id)["lease_run"]) == ACC["B"]


# ---- the relay's step 2, custody (task_offers' side faked) -----------------------------------------------------------

@pytest.fixture
def custodian(monkeypatch):
    """task_offers' custody, faked: a candidate always, and a hand-off that makes the task offering and places its
    hold."""
    monkeypatch.setitem(holds._KINDS, "task.offer", dict(holds._KINDS[tasks.HOLD_ACCOUNT], apply=lambda db, h, n: None,
                                                          cancelled=None, notice="held-other"))
    began = []

    def begin(db, user_id, task_id, candidate, now):
        tasks.set_state(db, user_id, task_id, "offering", now=now)
        began.append((task_id, candidate))
        return holds.place(db, user_id, "task.offer", secrets.token_hex(16), "a hand-off", now)
    monkeypatch.setattr(task_offers, "custody_candidate",
                        lambda db, user_id, task_id, now: {"custody_id": "c" * 32, "agent_id": "a" * 12,
                                                           "grp": "personal"})
    monkeypatch.setattr(task_offers, "begin_custody", begin)
    return began


def test_with_every_account_refused_the_relay_hands_to_the_custodian_counted_as_a_move(client, owner, ws,
                                                                                        custodian):
    h, user = owner
    task_id, first = started(client, h, user)
    refuse(user, first, resets=int(time.time()) + 5 * HOUR)
    after = row(task_id)
    assert after["state"] == "offering" and [b[0] for b in custodian] == [task_id] and len(after["moves"]) == 1
    assert "held-other" in ws.kinds()


def test_custody_is_not_asked_while_an_account_of_the_relay_set_has_room(client, owner, ws, custodian):
    h, user = owner
    own(user, ACC["B"])
    room(user, ACC["B"])
    task_id, first = started(client, h, user)
    refuse(user, first, resets=int(time.time()) + 5 * HOUR)
    assert custodian == [] and acct(row(task_id)["lease_run"]) == ACC["B"]


def test_a_hand_off_that_fails_to_begin_keeps_nothing_and_the_task_waits(client, owner, ws, monkeypatch):
    h, user = owner

    def broken(db, user_id, task_id, candidate, now):
        db.execute("UPDATE ade_agent_tasks SET title = 'changed by a hand-off that broke' WHERE id = ?", (task_id,))
        raise RuntimeError("its hold could not be placed")
    monkeypatch.setattr(task_offers, "custody_candidate", lambda db, user_id, task_id, now: {"agent_id": "a" * 12})
    monkeypatch.setattr(task_offers, "begin_custody", broken)
    task_id, first = started(client, h, user)
    reset = int(time.time()) + 5 * HOUR
    refuse(user, first, resets=reset)
    after = row(task_id)
    assert after["state"] == "suspended" and after["waits_until"] == reset and after["title"] == "Count lines"


# ---- the relay after a return ----------------------------------------------------------------------------------------

def test_a_return_is_taken_and_the_task_goes_on_from_the_returned_commit(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    finish(user, first, 1, 1)
    record(user, row(task_id)["lease_run"], "stopped")
    returned = "9" * 40
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "returning")
        tasks.take_return(db, user["id"], task_id, {"commit": returned, "tree": "8" * 40, "descends": True,
                                                    "protected_touched": [], "loaded_changed": [],
                                                    "steps": [{"n": 2, "state": "done", "check_passed": True}],
                                                    "effects": [{"call_id": "handover:o:1", "tool": "Bash",
                                                                 "kind": "local", "target": "make", "settled": "ran",
                                                                 "exit": 0}]})
    back = task_of(client, h, task_id)
    assert back["state"] == "suspended" and back["step"] == 3 and back["steps"][1]["evidence"] == "checked"
    assert press(client, h, task_id, "continue").status_code == 200
    # the returned commit and the tree the owner's workspace said, both, as the workspace's start requires
    assert json.loads(packet_of(row(task_id)["lease_run"])[0])["information"]["expect"] == {"head": returned,
                                                                                          "tree": "8" * 40}


@pytest.mark.parametrize("report", [{"descends": True}, {"descends": True, "tree": "not a tree"},
                                    {"descends": False, "tree": "8" * 40}])
def test_a_return_is_the_end_state_only_with_its_tree_and_once_the_worktree_was_moved_onto_it(client, owner, ws,
                                                                                             report):
    """From the end-to-end tests of custody's cases: after every way back, the owner's next attempt was refused
    by her own workspace, since the return was kept as the end state with no tree, and the workspace's start refuses an
    expect that is not {head, tree}. A way back whose workspace said no tree, or whose commit did not descend from the
    handed one (so the worktree was not moved onto it), keeps no end state: the next attempt expects the task's last
    checkpoint, which the worktree still holds, never a head and no tree that every start would refuse."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    finish(user, first, 1, 1)
    record(user, row(task_id)["lease_run"], "stopped")
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "returning")
        tasks.take_return(db, user["id"], task_id, dict({"commit": "9" * 40, "protected_touched": [],
                                                         "loaded_changed": [], "steps": [], "effects": []}, **report))
    assert press(client, h, task_id, "continue").status_code == 200
    assert json.loads(packet_of(row(task_id)["lease_run"])[0])["information"]["expect"] == {
        "head": cp(1, 1)["head"], "tree": cp(1, 1)["tree"]}


@pytest.mark.parametrize("report", [{"descends": False}, {"descends": True, "protected_touched": ["tests/x"]}])
def test_a_return_that_does_not_descend_or_touches_a_protected_path_needs_the_owner(client, owner, ws, report):
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "stopped")
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "returning")
        tasks.take_return(db, user["id"], task_id, dict({"commit": "8" * 40, "steps": [{"n": 1, "state": "done"}]},
                                                       **report))
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "returned"
    assert held["steps"][0]["state"] != "done"


# ---- two users' rows apart -------------------------------------------------------------------------------------------

def test_another_user_s_task_accounts_and_record_are_not_theirs_to_see_or_press(client, owner, ws, monkeypatch):
    h, user = owner
    task_id, _ = started(client, h, user)
    monkeypatch.setenv("OPEN_EDITION_ADMINS", "admin@example.com,bob@example.com")   # the local provider's users
    other = signed_up(client, "bob@example.com")
    assert client.get(f"{T}/{task_id}", headers=other).status_code == 404
    assert client.get(f"{T}/{task_id}/account-record", headers=other).status_code == 404
    assert client.get(f"{T}/{task_id}/journal", headers=other).status_code == 404
    assert press(client, other, task_id, "stop").status_code == 404
    assert client.post(f"{T}/{task_id}/end", headers=other, json={"state_version": 0, "outcome": "done"}
                       ).status_code == 404
    assert client.get(T, headers=other).json()["tasks"] == []
    assert client.get(A + "/accounts", headers=other).json()["accounts"] == []
    assert client.get(A + "/account-records", headers=other).json()["records"] == []
    assert task_of(client, h, task_id)["state"] == "running"


# ---- what an attempt's end holds the task for comes first ------------------------------------------------------------

@pytest.mark.parametrize("end,cause", [
    (dict(state="blocked", cause="watch"), "watch"),
    (dict(state="blocked", cause="branch"), "branch"),
    (dict(state="blocked", cause="rate_limit", effects=[{"call_id": "toolu_e", "tool": "Bash", "kind": "external",
                                                         "target": "make deploy", "settled": None}]), "effect"),
])
def test_what_the_end_holds_wins_over_the_relay_and_over_the_owner_s_choice(client, owner, ws, end, cause):
    h, user = owner
    own(user, ACC["B"])
    room(user, ACC["B"])
    task_id, first = started(client, h, user)
    press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["B"]})
    end = dict(end)
    record(user, first, end.pop("state"), cause=end.pop("cause"), **end)
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == cause and len(runs_of(task_id)) == 1


# ---- the owner's recorded choice wins over the relay's order ---------------------------------------------------------

def test_next_on_c_then_a_refuses_and_the_next_attempt_is_on_c(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    own(user, ACC["C"])
    room(user, ACC["B"], ACC["C"])
    task_id, first = started(client, h, user)
    press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["C"]})
    refuse(user, first, resets=int(time.time()) + HOUR)
    after = row(task_id)
    assert acct(after["lease_run"]) == ACC["C"] and after["asked"] is None


# ---- Stop and the switches need no version ---------------------------------------------------------------------------

def test_stop_relay_off_and_auto_resume_off_need_no_version(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    stale = task_of(client, h, task_id)["state_version"]
    refuse(user, first, resets=int(time.time()) + HOUR)               # the relay moves the task on meanwhile
    assert task_of(client, h, task_id)["state_version"] != stale
    assert client.post(f"{T}/{task_id}/switches", headers=h, json={"relay": False}).status_code == 200
    assert client.post(f"{T}/{task_id}/switches", headers=h, json={"auto_resume": False}).status_code == 200
    assert client.post(f"{T}/{task_id}/stop", headers=h, json={}).status_code == 200
    stopped = task_of(client, h, task_id)
    assert stopped["state"] == "suspended" and stopped["cause"] == "stopped"
    assert not stopped["relay"] and not stopped["auto_resume"] and len(runs_of(task_id)) == 2
    tasks.sweep(now=int(time.time()) + 2 * HOUR)
    assert len(runs_of(task_id)) == 2


def test_a_switch_is_turned_on_only_while_the_deployment_s_setting_for_it_is_on(client, owner, ws, monkeypatch):
    """The route's refusals: 403 a setting is off. Turning a switch off is always taken; turning it on while the
    deployment's own setting is off is refused, and a request that changes nothing of a switch is not."""
    h, user = owner
    task_id, _ = started(client, h, user)
    for name in ("relay", "auto_resume"):
        variable = "OPEN_EDITION_ADE_TASK_" + name.upper()
        assert press(client, h, task_id, "switches", **{name: False}).status_code == 200
        monkeypatch.setenv(variable, "off")
        refused = press(client, h, task_id, "switches", **{name: True})
        assert refused.status_code == 403 and variable in refused.json()["detail"]
        assert task_of(client, h, task_id)[name] is False
        # turned on again by the setting itself, not by deleting it: deleting it leaves the relay off, its default, and
        # Auto-resume on, its default
        monkeypatch.setenv(variable, "on")
        assert press(client, h, task_id, "switches", **{name: True}).status_code == 200
        assert task_of(client, h, task_id)[name] is True
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "off")
    taken = press(client, h, task_id, "switches", relay=True, auto_resume=False)   # relay already on: no change
    assert taken.status_code == 200 and taken.json()["task"]["relay"] is True
    assert taken.json()["task"]["auto_resume"] is False
    # the crossing setting unset, so off: refused before any confirmation is asked for
    assert task_of(client, h, task_id)["cross_groups"] is False
    r = press(client, h, task_id, "switches", cross_groups=True)
    assert r.status_code == 403 and "OPEN_EDITION_ADE_TASK_CROSS_GROUPS" in r.json()["detail"]
    assert events(task_id, "task.switched")[-1]["payload"] == {"switch": "auto_resume", "on": False, "hold": None}


@pytest.mark.parametrize("setting", ["on", "off"])
def test_turning_crossing_off_needs_no_version_while_the_relay_moves_the_task(client, owner, ws, monkeypatch,
                                                                               setting):
    """The crossing switch, off needing no version. Turning a task's Cross organisations off takes the sign-in alone and
    no version (the switches route's request has no version field), as Stop and turning Relay or Auto-resume off do:
    pressed while the relay moves the task on, after the version the page read has changed, it is not refused because
    the task moved; it is taken at once, with no hold, and queues nothing. So too with the deployment's setting turned
    off after the task was made, the task's own switch still on: the route's 403 for a setting that is off is for
    turning a switch on, and turning one off is always taken (tasks.switches); this case runs the crossing switch alone,
    with the setting on and with it turned off."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    read = task_of(client, h, task_id)
    assert read["cross_groups"] is True
    if setting == "off":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off")
    refuse(user, first, resets=int(time.time()) + HOUR)               # the relay moves the task on meanwhile
    moved = row(task_id)
    assert moved["state_version"] != read["state_version"] and acct(moved["lease_run"]) == ACC["B"]
    assert run(moved["lease_run"])["state"] == "queued"
    r = client.post(f"{T}/{task_id}/switches", headers=h, json={"cross_groups": False})
    assert r.status_code == 200 and r.json()["task"]["cross_groups"] is False and r.json()["hold"] is None
    assert events(task_id, "task.switched")[-1]["payload"] == {"switch": "cross_groups", "on": False, "hold": None}
    after = row(task_id)
    assert after["lease_run"] == moved["lease_run"] and len(runs_of(task_id)) == 2


# ---- an attempt that fails for its own reason ------------------------------------------------------------------------

def test_an_attempt_that_fails_for_its_own_reason_is_not_relayed(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    room(user, ACC["B"])
    task_id, first = started(client, h, user)
    record(user, first, "failed", reason="its turn budget was spent")
    after = task_of(client, h, task_id)
    assert after["state"] == "suspended" and after["cause"] == "failed" and len(runs_of(task_id)) == 1


# ---- nothing queued while offering, edits refused --------------------------------------------------------------------

def test_while_a_task_is_offered_nothing_is_queued_and_its_steps_cannot_change(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "offering", cause=None, waits_until=None)
    version = task_of(client, h, task_id)["state_version"]
    assert press(client, h, task_id, "continue").status_code == 412
    changed = client.put(f"{T}/{task_id}", headers=h, json={"state_version": version, "title": "Other"})
    assert changed.status_code == 412
    tasks.sweep(now=reset + 1)
    assert len(runs_of(task_id)) == 1 and row(task_id)["state"] == "offering"


def ask_offer(user, task_id):
    """An offer the owner asked for while the task's attempt works (task_offers' row, held)."""
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_task_offers (id, user_id, task_id, kind, state, fence, created_at, updated_at) "
                   "VALUES (?,?,?,'pressed','held',?,?,?)", (secrets.token_hex(16), user["id"], task_id,
                                                              row(task_id)["fence"], now, now))


@pytest.mark.parametrize("end, state, cause", [
    ("last step", "done", None),
    ("a step", "suspended", "stopped"),
    ("refused", "suspended", "stopped"),
    ("machine", "needs_owner", "worktree"),
])
def test_an_offer_asked_while_it_worked_is_told_once_the_end_has_decided_the_task(client, owner, ws, monkeypatch,
                                                                                   end, state, cause):
    h, user = owner
    own(user, ACC["B"])
    room(user, ACC["B"])
    told, heard = [], []

    def after_attempt_end(db, user_id, task_id, run_id, now, tells=None):
        got = db.execute("SELECT state, cause, lease_run FROM ade_agent_tasks WHERE id = ?", (task_id,)).fetchone()
        told.append((run_id, got["state"], got["cause"], got["lease_run"]))
        # the offer begun: its hold put among what the end leaves to tell, as task_offers puts it
        tells.append(("hold", {"id": "the-offers-hold"}))
        return True
    monkeypatch.setattr(task_offers, "after_attempt_end", after_attempt_end)
    monkeypatch.setattr(holds, "tell", lambda hold, now=None, post=None: heard.append(hold["id"]) or 1)
    task_id, first = started(client, h, user, steps=1 if end == "last step" else 2)
    ask_offer(user, task_id)
    if end in ("last step", "a step"):
        finish(user, first, 1, 1)
    elif end == "refused":
        refuse(user, first, resets=int(time.time()) + HOUR)
    else:
        record(user, first, "blocked", cause="machine")
    # the end decides the task first, G is told once with it, and nothing moves it on: no relay, no next step
    assert told == [(first, state, cause, None)]
    assert row(task_id)["state"] == state and row(task_id)["cause"] == cause and len(runs_of(task_id)) == 1
    # and the hold G placed is told once that end's transaction has committed, with what else it left to tell
    assert heard.count("the-offers-hold") == 1, heard


def test_an_offer_that_fails_at_the_end_keeps_nothing_of_it_and_the_task_stays_as_the_end_left_it(client, owner,
                                                                                                  ws, monkeypatch):
    h, user = owner
    heard = []

    def after_attempt_end(db, user_id, task_id, run_id, now, tells=None):
        db.execute("UPDATE ade_agent_task_offers SET state = 'offered' WHERE task_id = ?", (task_id,))
        tells.append(("hold", {"id": "the-offers-hold"}))
        raise RuntimeError("the teammate's track failed")
    monkeypatch.setattr(task_offers, "after_attempt_end", after_attempt_end)
    monkeypatch.setattr(holds, "tell", lambda hold, now=None, post=None: heard.append(hold["id"]) or 1)
    task_id, first = started(client, h, user)
    ask_offer(user, task_id)
    finish(user, first, 1, 1)
    with store.connect() as db:
        offer = db.execute("SELECT state FROM ade_agent_task_offers WHERE task_id = ?", (task_id,)).fetchone()
    assert offer["state"] == "held"
    assert row(task_id)["state"] == "suspended" and row(task_id)["cause"] == "stopped" and len(runs_of(task_id)) == 1
    # nor is the hold it put among what to tell before it failed told
    assert "the-offers-hold" not in heard


# ---- the task's pushes are content-free ------------------------------------------------------------------------------

def test_each_push_of_a_task_is_in_fixed_words_naming_nothing_of_the_task(client, owner, ws, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["B"], plan="Claude Team", org="org-x", org_name="X Corp")
    task_id, first = started(client, h, user, title="Secret plan for Q3")
    refuse(user, first, resets=int(time.time()) + HOUR)                # a crossing, onto X's seats
    reset = int(time.time()) + 2 * HOUR
    refuse(user, row(task_id)["lease_run"], resets=reset)
    tasks.sweep(now=reset + HOUR)
    told = ws.kinds()
    assert told and set(told) <= {"task-relayed", "task-crossed", "task-continued"}
    for m in ws.pushes:
        title, body, url = push.WORDS[next(k for k, w in push.WORDS.items() if w[0] == m.title)]
        assert (m.title, m.body, m.url) == (title, body, url)
        for word in ("Secret plan", ACC["A"], ACC["B"], "X Corp", "org-x", task_id, "@example.com"):
            assert word not in m.title + m.body + m.url + m.tag


# ---- the backend recorded when the task was made ---------------------------------------------------------------------

def test_a_task_runs_on_the_account_it_was_made_on_after_its_agent_s_account_changes(client, owner, ws):
    h, user = owner
    own(user, ACC["C"])
    own(user, ACC["D"])
    agent_id = make_agent(client, h, ACC["C"])
    task = make_task(client, h, agent_id)
    assert task["backend"] == {"kind": "account", "id": ACC["C"]}
    assert client.put(f"{A}/{agent_id}", headers=h, json={"account": ACC["D"]}).status_code == 200
    press(client, h, task["id"], "start")
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send_now(user, "http://workspace.test")
    assert ws.sent[-1]["task"]["backend"] == {"kind": "account", "id": ACC["C"]}


def test_an_agent_with_no_account_makes_its_task_on_the_default_named_so(client, owner, ws):
    h, user = owner
    own(user, "default")
    # new sessions start on the default account here (the fake's own answer): the task's account is that one
    task = make_task(client, h, make_agent(client, h))
    assert task["backend"] == {"kind": "account", "id": "default"}
    press(client, h, task["id"], "start")
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send_now(user, "http://workspace.test")
    message = ws.sent[-1]["task"]
    assert message["backend"] == {"kind": "account", "id": "default"} and message["identity"]["plan"] == "Claude Max"


def test_an_agent_with_no_account_makes_its_task_on_the_account_new_sessions_start_on(client, owner, ws):
    """A task made with no backend named, of an agent with no account of its own, begins on the account
    new sessions start on then, as the workspace says it, not on the default account; its attempt goes
    there by its id."""
    h, user = owner
    own(user, "default")
    own(user, ACC["C"])
    ws.starts_on = ACC["C"]
    task = make_task(client, h, make_agent(client, h))
    assert task["backend"] == {"kind": "account", "id": ACC["C"]}
    assert row(task["id"])["backend"] == {"kind": "account", "id": ACC["C"]}
    assert [c for c in ws.calls if c[1] == "/accounts?status=0"] == [("GET", "/accounts?status=0", None)]
    press(client, h, task["id"], "start")
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send_now(user, "http://workspace.test")
    assert ws.sent[-1]["task"]["backend"] == {"kind": "account", "id": ACC["C"]}


@pytest.mark.parametrize("how", ["not answering", "refused", "not running", "out of shape"])
def test_a_task_of_an_agent_with_no_account_is_refused_while_the_workspace_cannot_say_where_new_sessions_start(
        client, owner, ws, monkeypatch, how):
    """Where the workspace cannot say which account new sessions start on, a task of an agent with no account,
    with no backend named, is refused in words that say what to do, and none is made; never put on the default. The
    workspace's own refusal (an error status, not only no answer) is one such, never passed on as its status."""
    h, user = owner
    own(user, "default")
    agent_id = make_agent(client, h)
    if how == "not answering":
        ws.starts_on = None
    elif how == "refused":
        ws.starts_on = workspace_client.WorkspaceCallError(500, "The workspace could not read its accounts.")
    elif how == "not running":
        monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    else:
        ws.starts_on = "Default"                        # not an account's shape: nothing read from it
    r = client.post(T, headers=h, json={"agent_id": agent_id, "title": "Count lines", "goal": "Make it two lines.",
                                        "steps": [{"objective": "Append a line", "check": None}]})
    assert r.status_code == 412, r.text
    assert r.json()["detail"] == tasks.NO_STARTS_ON
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_tasks").fetchone()[0] == 0
    asked = [c for c in ws.calls if c[1] == "/accounts?status=0"]
    assert asked == ([] if how == "not running" else [("GET", "/accounts?status=0", None)])
    # named in the body, the account is the one named, the workspace not asked
    task = make_task(client, h, agent_id, backend={"kind": "account", "id": "default"})
    assert task["backend"] == {"kind": "account", "id": "default"}
    assert [c for c in ws.calls if c[1] == "/accounts?status=0"] == asked


def test_an_agent_with_an_account_of_its_own_makes_its_task_there_without_asking(client, owner, ws):
    """An agent's own account is its task's, whichever account new sessions start on, and the workspace is not
    asked which that is. Today an agent set to the default account is kept as one with none (agents.py), so such an
    agent's task begins where new sessions start, as its runs do; whether "default" should be a value of an agent's
    own is left open here."""
    h, user = owner
    own(user, ACC["C"])
    own(user, ACC["D"])
    ws.starts_on = ACC["D"]
    task = make_task(client, h, make_agent(client, h, ACC["C"]))
    assert task["backend"] == {"kind": "account", "id": ACC["C"]}
    assert not [c for c in ws.calls if c[1] == "/accounts?status=0"]


def set_account(agent_id, account):
    """The agent's own account changed in the app's records, as the owner's edit in another window would."""
    def change():
        with store.connect() as db:
            db.execute("UPDATE ade_agents SET account = ? WHERE id = ?", (account, agent_id))
    return change


def test_an_agent_given_an_account_while_the_workspace_is_asked_makes_its_task_on_that_account(client, owner, ws):
    """The app asks the workspace before its transaction, and decides inside it with the agent as it reads it
    then: an agent given an account of its own while the workspace was asked begins its task there, the workspace's
    answer unused."""
    h, user = owner
    own(user, ACC["C"])
    own(user, ACC["D"])
    ws.starts_on = ACC["D"]
    agent_id = make_agent(client, h)
    ws.meanwhile["/accounts?status=0"] = set_account(agent_id, ACC["C"])
    task = make_task(client, h, agent_id)
    assert [c for c in ws.calls if c[1] == "/accounts?status=0"] == [("GET", "/accounts?status=0", None)]
    assert task["backend"] == {"kind": "account", "id": ACC["C"]}
    assert row(task["id"])["backend"] == {"kind": "account", "id": ACC["C"]}


def test_an_agent_whose_account_is_taken_away_while_the_task_is_made_is_refused(client, owner, ws):
    """An agent read with an account of its own, the workspace not asked, whose account is taken away before the
    transaction, has no account there and no answer of the workspace's: the task is refused, never put on the default
    account, in words that say its account changed (the workspace was not asked, so not NO_STARTS_ON's), and Make
    again asks the workspace."""
    h, user = owner
    own(user, "default")
    own(user, ACC["C"])
    ws.starts_on = ACC["C"]
    agent_id = make_agent(client, h, ACC["C"])
    ws.meanwhile["/repos"] = set_account(agent_id, None)
    r = client.post(T, headers=h, json={"agent_id": agent_id, "title": "Count lines", "goal": "Make it two lines.",
                                        "steps": [{"objective": "Append a line", "check": None}]})
    assert r.status_code == 412, r.text
    assert r.json()["detail"] == tasks.ACCOUNT_CHANGED
    assert not [c for c in ws.calls if c[1] == "/accounts?status=0"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_tasks").fetchone()[0] == 0
    task = make_task(client, h, agent_id)
    assert task["backend"] == {"kind": "account", "id": ACC["C"]}
    assert [c for c in ws.calls if c[1] == "/accounts?status=0"] == [("GET", "/accounts?status=0", None)]


# ---- the weekly reserve ----------------------------------------------------------------------------------------------

def near_week(user, account, window="seven_day"):
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + account, {
            window: {"status": "allowed_warning", "resets_at": now + 3 * 86400, "at": now}}, None, now)


def test_the_reserve_keeps_an_unattended_step_off_its_account_and_a_press_runs_there(client, owner, ws):
    h, user = owner
    own(user, ACC["H"])
    own(user, ACC["B"])
    agent_id = make_agent(client, h, ACC["H"])
    task = make_task(client, h, agent_id, steps=3)
    press(client, h, task["id"], "start")
    first = row(task["id"])["lease_run"]
    sent = sent_task(user, ws)                  # the owner's own press, on an account whose reserve is not lifted
    assert (sent["chosen_by"], sent["reserve"]) == ("owner", False)
    near_week(user, ACC["H"])
    finish(user, first, 1, 1)
    second = row(task["id"])["lease_run"]
    sent = sent_task(user, ws)                  # the relay's, on B, whose reserve is not lifted either
    assert acct(second) == ACC["B"] and (sent["chosen_by"], sent["reserve"]) == ("relay", False)
    record(user, second, "stopped")
    r = press(client, h, task["id"], "continue-on", backend={"kind": "account", "id": ACC["H"]})
    assert r.status_code == 200 and acct(row(task["id"])["lease_run"]) == ACC["H"]


def test_an_unattended_attempt_ended_at_its_reserve_moves_the_relay_on(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    now = int(time.time())
    record(user, first, "blocked", cause="rate_limit", reason="weekly reserve", limits={
        "seven_day_opus": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}})
    assert acct(row(task_id)["lease_run"]) == ACC["B"]
    left = {e["account"]["account"]: e["left_out"] for e in task_of(client, h, task_id)["relay_set"]}
    assert left[ACC["A"]] == "its weekly reserve"


@pytest.mark.parametrize("relay", ["on", "off"])
def test_the_reserve_stands_until_its_window_resets_and_the_next_step_then_starts_on_its_account(client, owner, ws,
                                                                                                  monkeypatch,
                                                                                                  relay):
    """A weekly window last reported near its limit holds its account back from an unattended start until a later event
    for that window or its reset time. The reset half: H, the task's only account, near its seven-day limit, so its next
    step waits for that window's reset; one second before it, the relay set still leaves H out for its weekly reserve;
    at the reset itself, the time the task waits until, the sweep starts the next step on H. Both with the relay on and
    with it off."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    if relay == "off":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "0")
    now = int(time.time())
    reset = now + HOUR
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["H"], {
            "seven_day": {"status": "allowed_warning", "resets_at": reset, "at": now}}, None, now)
    finish(user, first, 1, 1)
    waiting = row(task_id)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", reset)
    assert len(runs_of(task_id)) == 1
    with store.connect() as db:
        left = {e["account"]: e["left_out"] for e in tasks.relay_set(db, user["id"], task_id, reset - 1)}
    assert left == {ACC["H"]: "its weekly reserve"}
    tasks.sweep(now=reset)
    resumed = row(task_id)
    assert resumed["state"] == "running" and acct(resumed["lease_run"]) == ACC["H"] and len(runs_of(task_id)) == 2


# ---- presses ---------------------------------------------------------------------------------------------------------

def test_presses_go_through_the_reserve_and_into_another_group_and_stop_drops_next_on(client, owner, ws):
    h, user = owner
    own(user, ACC["C"])
    own(user, ACC["H"])
    own(user, ACC["D"], plan="Claude Team", org="org-x", org_name="X")
    near_week(user, ACC["H"])
    task_id, first = started(client, h, user, steps=4, between_steps="wait")
    press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["C"]})
    press(client, h, task_id, "stop")
    record(user, first, "stopped")
    assert row(task_id)["asked"] is None
    assert press(client, h, task_id, "continue").status_code == 200
    on_a = row(task_id)["lease_run"]
    assert acct(on_a) == ACC["A"]
    finish(user, on_a, 1, 1)
    assert press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["H"]}).status_code == 200
    finish(user, row(task_id)["lease_run"], 2, 2)
    r = press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["D"]})
    assert r.status_code == 200 and acct(row(task_id)["lease_run"]) == ACC["D"]
    assert row(task_id)["bound_org"] == "org-x" and not row(task_id)["crossings_ended"]


# ---- two confirmations held at once, one cancelled -------------------------------------------------------------------

def identity(account, email, plan="Claude Max", org=None, org_name=None, word=None):
    report = {"email": email, "organization": org_name, "plan": plan, "token_source": None, "api_key_source": None,
              "api_provider": "firstParty"}
    status = {"loggedIn": True, "email": email, "orgId": org or "personal-" + account, "orgName": org_name,
              "subscriptionType": word or shapes.plan_word(plan), "authMethod": "claude.ai"}
    return report, status


def report(client, h, ws, account, **kw):
    ws.identities[account] = identity(account, kw.pop("email", account + "@example.com"), **kw)
    r = client.post(A + "/accounts/report", headers=h, json={"account": account})
    assert r.status_code == 200, r.text
    return r.json()["account"]


def shown(client, h, account_id):
    """What the page shows of an account and sends with its confirmation: its report and orgId as listed now."""
    listed = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == account_id)
    return {"report": listed["report"], "org_id": listed["org_id"]}


def test_two_confirmations_held_at_once_one_cancelled_never_joins_the_other_joins(client, owner, ws):
    h, user = owner
    first, second = report(client, h, ws, ACC["A"]), report(client, h, ws, ACC["B"])
    held = []
    for account in (first, second):
        subject = f"task-account-own:{account['id']}"
        r = client.post(f"{A}/accounts/{account['id']}/confirm", headers=h,
                        json={"confirmation": confirmation(user, subject), **shown(client, h, account["id"])})
        assert r.status_code == 200, r.text
        held.append(r.json()["hold"])
    listed = client.get(A + "/accounts", headers=h).json()["accounts"]
    assert [a["hold"]["id"] for a in listed] == [x["id"] for x in held]
    assert all(a["state"] == "held" for a in listed) and ws.kinds() == ["held-account", "held-account"]
    assert client.post(f"/api/ade/holds/{held[0]['id']}/cancel", headers=h, json={}).status_code == 200
    holds.settle(user["id"], held[1]["takes_effect_at"] + 1)
    states = {a["account"]: (a["state"], a["grp"]) for a in client.get(A + "/accounts", headers=h).json()["accounts"]}
    assert states == {ACC["A"]: ("reported", None), ACC["B"]: ("confirmed", "personal")}


def test_a_confirmation_needs_more_than_the_sign_in(client, owner, ws):
    h, user = owner
    account = report(client, h, ws, ACC["A"])
    r = client.post(f"{A}/accounts/{account['id']}/confirm", headers=h, json=shown(client, h, account["id"]))
    assert r.status_code == 403
    wrong = confirmation(user, f"task-account-own:{'0' * 32}")
    assert client.post(f"{A}/accounts/{account['id']}/confirm", headers=h,
                       json={"confirmation": wrong, **shown(client, h, account["id"])}).status_code == 403


@pytest.mark.parametrize("changed", ["email", "token_source", "org_id"])
def test_a_confirmation_binds_the_report_the_page_showed_and_one_read_again_since_is_refused(client, owner, ws,
                                                                                              changed):
    """A confirmation names the report and orgId the page
    showed. Where the account was read again since and any of it differs, the confirmation is refused, 409, before it
    is spent, and the account is as that read left it: no hold, nothing pending, no notice. The same confirmation,
    naming the report as read now, is taken, and what waits on the hold is that report. Each difference alone is one:
    the email; the token source alone, which the email, the organisation and the plan do not show; the orgId alone,
    the organisation's name the same."""
    h, user = owner
    first, status = identity(ACC["A"], "a@example.com", plan="Claude Team", org="org-x", org_name="X")
    ws.identities[ACC["A"]] = (first, status)
    looked = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]}).json()["account"]
    page = {"report": looked["report"], "org_id": looked["org_id"]}
    ws.identities[ACC["A"]] = {"email": (dict(first, email="b@example.com"), dict(status, email="b@example.com")),
                               "token_source": (dict(first, token_source="CLAUDE_CODE_OAUTH_TOKEN"), status),
                               "org_id": (first, dict(status, orgId="org-y"))}[changed]
    now = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]}).json()["account"]
    assert now["mismatch"] is None and (now["report"], now["org_id"]) != (page["report"], page["org_id"])
    word = confirmation(user, f"task-account-own:{looked['id']}")
    r = client.post(f"{A}/accounts/{looked['id']}/confirm", headers=h, json={"confirmation": word, **page})
    assert (r.status_code, r.json()["detail"]) == (409, tasks.SHOWN_CHANGED)

    def state():
        with store.connect() as db:
            return tuple(db.execute("SELECT state, hold_id, pending FROM ade_account_owners WHERE id = ?",
                                    (looked["id"],)).fetchone())
    assert state() == ("reported", None, None) and ws.kinds() == []
    r = client.post(f"{A}/accounts/{looked['id']}/confirm", headers=h,
                    json={"confirmation": word, "report": now["report"], "org_id": now["org_id"]})
    assert r.status_code == 200, r.text
    assert state()[0] == "held" and json.loads(state()[2]) == dict(now["report"], org_id=now["org_id"])
    assert ws.kinds() == ["held-account"]


def test_a_confirmation_that_names_no_report_is_refused_and_one_whose_page_showed_none_was_read_since(client, owner,
                                                                                                      ws):
    """A request that names no report, as a page from before the check sends, is refused, 400, saying to reload,
    an orgId alone naming none; one whose page showed no report, while the account has one now, is a report read again
    since, 409. Neither spends the confirmation."""
    h, user = owner
    account = report(client, h, ws, ACC["A"])
    word = confirmation(user, f"task-account-own:{account['id']}")
    for body, refusal in (({"confirmation": word}, (400, tasks.NOT_SHOWN)),
                          ({"confirmation": word, "org_id": account["org_id"]}, (400, tasks.NOT_SHOWN)),
                          ({"confirmation": word, "report": None, "org_id": account["org_id"]},
                           (409, tasks.SHOWN_CHANGED))):
        r = client.post(f"{A}/accounts/{account['id']}/confirm", headers=h, json=body)
        assert (r.status_code, r.json()["detail"]) == refusal, body
    r = client.post(f"{A}/accounts/{account['id']}/confirm", headers=h,
                    json={"confirmation": word, **shown(client, h, account["id"])})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("again", ["mismatch", "nothing"])
def test_a_report_read_again_into_a_refusal_of_its_own_is_refused_as_read_since(client, owner, ws, again):
    """The refusals that read the report come after the check of what the page showed, so that they speak of the report
    the owner saw. The page showed a report that could be confirmed; the account was read again since, its report and
    auth status now disagreeing, or its start now reporting nothing: the confirmation naming what the page showed is
    refused as a read since (409), not for the new read's own fault (412); a confirmation naming what is listed now
    meets that read's own refusal, nothing of the account changed. That a 409 leaves the confirmation unspent is shown
    by test_a_confirmation_binds_the_report_the_page_showed_and_one_read_again_since_is_refused, whose same confirmation
    is then taken; here the 412 comes before any spend, so it could not show it. (The third such refusal, a plan in no
    group, no read reaches: a plan the shared table does not know, or a seat with no orgId, is a mismatch first.)"""
    h, user = owner
    first, status = identity(ACC["A"], "a@example.com")
    ws.identities[ACC["A"]] = (first, status)
    looked = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]}).json()["account"]
    page = {"report": looked["report"], "org_id": looked["org_id"]}
    assert looked["mismatch"] is None and page["report"]["plan"] == "Claude Max"
    ws.identities[ACC["A"]] = {"mismatch": (first, dict(status, email="other@example.com")),
                               "nothing": (None, status)}[again]
    now = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]}).json()["account"]
    assert (now["mismatch"], now["report"] is None) == ({"mismatch": "email", "nothing": "report"}[again],
                                                         again == "nothing")
    word = confirmation(user, f"task-account-own:{looked['id']}")
    r = client.post(f"{A}/accounts/{looked['id']}/confirm", headers=h, json={"confirmation": word, **page})
    assert (r.status_code, r.json()["detail"]) == (409, tasks.SHOWN_CHANGED)
    r = client.post(f"{A}/accounts/{looked['id']}/confirm", headers=h,
                    json={"confirmation": word, **shown(client, h, looked["id"])})
    own_refusal = {"mismatch": "Its email differs between Claude Code's report and auth status: sign it in again, then "
                               "read it again.",
                   "nothing": "Read its identity first (report)."}[again]
    assert (r.status_code, r.json()["detail"]) == (412, own_refusal)
    with store.connect() as db:
        assert tuple(db.execute("SELECT state, hold_id, pending FROM ade_account_owners WHERE id = ?",
                                (looked["id"],)).fetchone()) == ("reported", None, None)


@pytest.mark.parametrize("state", ["held", "confirmed"])
def test_an_account_held_or_confirmed_already_is_refused_for_that_whatever_its_confirmation_names(client, owner, ws,
                                                                                                  state):
    """The two refusals of the account's state come before the check of what the page showed: a confirmation of an
    account that waits for its hold already, or is confirmed already, is refused for that (412), whether it names no
    report, as a page from before the check sends it, or a report that is not the account's."""
    h, user = owner
    account = report(client, h, ws, ACC["A"])
    r = client.post(f"{A}/accounts/{account['id']}/confirm", headers=h,
                    json={"confirmation": confirmation(user, f"task-account-own:{account['id']}"),
                          **shown(client, h, account["id"])})
    assert r.status_code == 200, r.text
    if state == "confirmed":
        holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    listed = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == account["id"])
    assert listed["state"] == state
    words = {"held": "It waits for its confirmation's hold already.", "confirmed": "It is confirmed as yours already."}
    word = confirmation(user, f"task-account-own:{account['id']}")
    for body in ({"confirmation": word}, {"confirmation": word, "report": None, "org_id": None}):
        r = client.post(f"{A}/accounts/{account['id']}/confirm", headers=h, json=body)
        assert (r.status_code, r.json()["detail"]) == (412, words[state]), body


# ---- the relay across groups ----------------------------------------------------------------------------------------

def crossing_setup(client, h, user):
    # in Connections' order the task's own B, then the seat A, then C: after B, A comes first by order alone, so only
    # the relay's preference for its own group puts C before it
    own(user, ACC["B"], plan="Claude Pro")
    own(user, ACC["A"], plan="Claude Team", org="org-x", org_name="X")
    own(user, ACC["C"])
    room(user, ACC["A"], ACC["C"])
    agent_id = make_agent(client, h, ACC["B"])
    task = make_task(client, h, agent_id)
    press(client, h, task["id"], "start")
    return task["id"]


def test_the_relay_stays_in_its_group_first_then_crosses_with_its_journal_push_and_lines(client, owner, ws,
                                                                                        monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id = crossing_setup(client, h, user)
    assert task_of(client, h, task_id)["cross_groups"] is True
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    assert acct(row(task_id)["lease_run"]) == ACC["C"]                  # within the group first
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    after = row(task_id)
    assert acct(after["lease_run"]) == ACC["A"] and after["bound_org"] == "org-x"
    crossed = events(task_id, "task.crossed")[-1]["payload"]
    assert crossed["from_grp"] == "personal" and crossed["to_grp"] == "org:org-x" and crossed["org"] == "org-x"
    assert crossed["from"] == "account:" + ACC["C"] and crossed["to"] == "account:" + ACC["A"]
    assert crossed["home"] is None and isinstance(crossed["notice_at"], int)
    assert "@" not in json.dumps(crossed)
    assert ws.kinds() == ["task-relayed", "task-crossed"]
    lines = task_of(client, h, task_id)["moves"]
    assert [(m["kind"], m["point"], m["cases"]) for m in lines] == [
        ("relay", 2, []), ("relay", 2, []), ("crossing", 1, ["crossing", "onto_seat"])]


def test_an_account_an_attempt_worked_on_since_its_last_start_is_known_to_have_room(client, owner, ws, clock):
    """tasks._known_room, which the relay's order reads within a group: an account is known to have room when none of
    its windows holds it and one of these: since its last start, an event said a window allowed or allowed_warning, or
    an attempt worked on it; a window's report of allowed_warning still stands; or a refused window's reset has passed.
    (A field older than the last start counts as unknown, but for a window reported allowed_warning or refused, which
    stands until a later event for it or its reset time; the code once bounded an allowed_warning window by the last
    start too, which the next three tests pin, the third from a refusal's side.) The attempt's clause alone: A's attempt
    reported working with no rate-limit event at all, and C never run. Then a later attempt starts on A a second after
    that work and reports nothing yet: the work is older than A's last start, and A is no longer known to have room. The
    end-to-end case of the relay across groups gives A its known room by an event, so it does not pin this clause."""
    h, user = owner
    task_id, first = started(client, h, user, account="A")
    own(user, ACC["C"])

    def known():
        with store.connect() as db:
            return {e["account"]: e["known_room"] for e in tasks.relay_set(db, user["id"], task_id, int(time.time()))}
    assert known() == {ACC["A"]: False, ACC["C"]: False}                # started, nothing heard from it yet
    record(user, first, "working")
    with store.connect() as db:
        windows = db.execute("SELECT COUNT(*) FROM ade_backend_windows WHERE user_id = ?",
                             (user["id"],)).fetchone()[0]
    assert windows == 0                                                 # no event said anything of either account
    assert known() == {ACC["A"]: True, ACC["C"]: False}
    finish(user, first, 1, 1)                                           # step 2 starts, on A again
    second = row(task_id)["lease_run"]
    assert second != first and acct(second) == ACC["A"]
    clock[0] += 1
    record(user, second, "waiting")                                     # its start, a second after the work
    with store.connect() as db:
        a = db.execute("SELECT worked_at, last_start_at FROM ade_account_owners WHERE user_id = ? AND account = ?",
                       (user["id"], ACC["A"])).fetchone()
    assert (a["worked_at"], a["last_start_at"]) == (clock[0] - 1, clock[0])
    assert known() == {ACC["A"]: False, ACC["C"]: False}


@pytest.mark.parametrize("status,resets_in,stands", [("allowed_warning", 5 * HOUR, True),
                                                      ("allowed_warning", None, True),
                                                      ("allowed", 5 * HOUR, False)])
def test_a_window_reported_allowed_warning_stands_as_known_room_after_a_later_start(client, owner, ws, clock,
                                                                                    status, resets_in, stands):
    """A field older than its account's last start counts as unknown, but for a window reported allowed_warning or
    refused, which stands until a later event for it or its reset time. A's five-hour window reported its status in step
    1's attempt; step 2 then started on A a second later and reported nothing, since the program sends an event only
    when a status changes. A report of allowed_warning still stands, so A is known to have room, not an account to try
    as a probe: until the window's reset, or while no later event comes when it gave none. A report of allowed, older
    than A's last start, is unknown."""
    h, user = owner
    task_id, first = started(client, h, user, account="A")
    now = clock[0]
    resets = None if resets_in is None else now + resets_in
    record(user, first, "working", limits={"five_hour": {"status": status, "resets_at": resets, "at": now}})
    finish(user, first, 1, 1)
    second = row(task_id)["lease_run"]
    assert second != first and acct(second) == ACC["A"]
    clock[0] += 1
    record(user, second, "waiting")                      # the program sends no event: no window's status changed
    with store.connect() as db:
        w = db.execute("SELECT status, seen_at FROM ade_backend_windows WHERE user_id = ? AND rate_window = ?",
                       (user["id"], "five_hour")).fetchone()
        a = db.execute("SELECT worked_at, last_start_at FROM ade_account_owners WHERE user_id = ? AND account = ?",
                       (user["id"], ACC["A"])).fetchone()
    assert (w["status"], w["seen_at"] < a["last_start_at"], a["worked_at"] < a["last_start_at"]) == (status, True, True)

    def entry(at):
        with store.connect() as db:
            return {e["account"]: e for e in tasks.relay_set(db, user["id"], task_id, at)}[ACC["A"]]
    assert entry(clock[0])["eligible"] is True
    assert entry(clock[0])["known_room"] is stands, entry(clock[0])
    later = now + 7 * 86400 if resets is None else resets    # at the window's reset, its report no longer stands
    assert entry(later)["known_room"] is (resets is None), entry(later)


@pytest.mark.parametrize("five_hour", [None, "allowed"])
def test_a_weekly_window_s_standing_allowed_warning_is_known_room_once_its_reserve_is_lifted(client, owner, ws,
                                                                                            clock, five_hour):
    """A weekly window's report of allowed_warning stands as a five-hour window's does. A's reserve lifted through the
    route the owner's "Use to the limit" uses (tasks.put_account), its seven-day window warned in step 1's attempt, and
    its five-hour window either never reported or reported allowed in that same attempt. After step 2 starts on A with
    no event, A is known to have room, and at the weekly window's reset it no longer is. `_known_room` takes any one
    window's evidence, where the relay orders first the accounts whose windows are all known to have room: "all" read
    over the windows ever reported would change the second case alone, its five-hour report older than A's last start;
    read over every window, both."""
    h, user = owner
    task_id, first = started(client, h, user, account="A")
    tasks.put_account(user, own(user, ACC["A"]), {"reserve_lifted": True})
    now = clock[0]
    resets = now + 3 * 86400
    limits = {"seven_day": {"status": "allowed_warning", "resets_at": resets, "at": now}}
    if five_hour:
        limits["five_hour"] = {"status": five_hour, "resets_at": now + 5 * HOUR, "at": now}
    record(user, first, "working", limits=limits)
    finish(user, first, 1, 1)
    second = row(task_id)["lease_run"]
    assert second != first and acct(second) == ACC["A"]
    clock[0] += 1
    record(user, second, "waiting")                      # the program sends no event: no window's status changed
    with store.connect() as db:
        w = db.execute("SELECT rate_window, status, seen_at FROM ade_backend_windows WHERE user_id = ?",
                       (user["id"],)).fetchall()
        a = db.execute("SELECT worked_at, last_start_at FROM ade_account_owners WHERE user_id = ? AND account = ?",
                       (user["id"], ACC["A"])).fetchone()
    assert sorted((r["rate_window"], r["status"]) for r in w) == sorted(
        [("seven_day", "allowed_warning")] + ([("five_hour", five_hour)] if five_hour else []))
    assert all(r["seen_at"] < a["last_start_at"] for r in w) and a["worked_at"] < a["last_start_at"]

    def entry(at):
        with store.connect() as db:
            return {e["account"]: e for e in tasks.relay_set(db, user["id"], task_id, at)}[ACC["A"]]
    assert (entry(clock[0])["eligible"], entry(clock[0])["known_room"]) == (True, True), entry(clock[0])
    assert entry(resets)["known_room"] is False, entry(resets)


def test_a_window_s_warning_stands_through_a_later_event_for_another_window(client, owner, ws, clock):
    """A window's report of allowed_warning ends at a later event for that window or at its reset, not at an event for
    another window. A's five-hour window warned in step 1's attempt, then a later event of the same attempt said its
    seven-day window allows it; step 2 then started on A with no event. The warning stands and A is known to have room,
    where a rule letting any later event end it would read A as an account to try as a probe; at the five-hour window's
    reset it is not known to have room."""
    h, user = owner
    task_id, first = started(client, h, user, account="A")
    now = clock[0]
    resets = now + 5 * HOUR
    record(user, first, "working", limits={"five_hour": {"status": "allowed_warning", "resets_at": resets, "at": now}})
    clock[0] += 1
    record(user, first, "working", limits={"seven_day": {"status": "allowed", "resets_at": now + 3 * 86400,
                                                         "at": clock[0]}})
    finish(user, first, 1, 1)
    second = row(task_id)["lease_run"]
    assert second != first and acct(second) == ACC["A"]
    clock[0] += 1
    record(user, second, "waiting")                      # the program sends no event: no window's status changed
    with store.connect() as db:
        w = {r["rate_window"]: r for r in db.execute(
            "SELECT rate_window, status, seen_at FROM ade_backend_windows WHERE user_id = ?", (user["id"],)).fetchall()}
        started_at = db.execute("SELECT last_start_at FROM ade_account_owners WHERE user_id = ? AND account = ?",
                                (user["id"], ACC["A"])).fetchone()["last_start_at"]
    assert (w["five_hour"]["status"], w["seven_day"]["status"]) == ("allowed_warning", "allowed")
    assert w["five_hour"]["seen_at"] < w["seven_day"]["seen_at"] < started_at

    def entry(at):
        with store.connect() as db:
            return {e["account"]: e for e in tasks.relay_set(db, user["id"], task_id, at)}[ACC["A"]]
    assert (entry(clock[0])["eligible"], entry(clock[0])["known_room"]) == (True, True), entry(clock[0])
    assert entry(resets)["known_room"] is False, entry(resets)


def test_a_refusal_with_no_reset_that_the_owner_lifted_is_not_known_room(client, owner, ws, clock):
    """Of the reports that stand whatever their age, allowed_warning stands as room; a refusal stands as a refusal. A's
    five-hour window refused with no reset, which holds A until the owner lifts it. His Continue lifts it and starts an
    attempt on A a second later, so the refusal is older than A's last start: A is held no longer and is eligible, but
    the refusal says nothing of A's room, and A is an account to try as a probe, not one known to have room."""
    h, user = owner
    task_id, first = started(client, h, user, account="A")
    refuse(user, first, resets=None)
    assert (row(task_id)["state"], row(task_id)["cause"]) == ("needs_owner", "window")
    assert press(client, h, task_id, "continue").status_code == 200
    second = row(task_id)["lease_run"]
    assert second != first and acct(second) == ACC["A"]
    clock[0] += 1
    record(user, second, "waiting")                      # its start: no event, so no window's status changed
    with store.connect() as db:
        w = db.execute("SELECT status, resets_at, seen_at, lifted_at FROM ade_backend_windows WHERE user_id = ? AND "
                       "rate_window = 'five_hour'", (user["id"],)).fetchone()
        started_at = db.execute("SELECT last_start_at FROM ade_account_owners WHERE user_id = ? AND account = ?",
                                (user["id"], ACC["A"])).fetchone()["last_start_at"]
        a = {e["account"]: e for e in tasks.relay_set(db, user["id"], task_id, clock[0])}[ACC["A"]]
        held = tasks.backend_state(db, user["id"], "account:" + ACC["A"], clock[0])
    assert (w["status"], w["resets_at"], w["lifted_at"] is not None) == ("rejected", None, True)
    assert w["seen_at"] < started_at
    assert held == (True, None)
    assert (a["eligible"], a["known_room"]) == (True, False), a


@pytest.mark.parametrize("off", ["unset", "setting", "switch"])
def test_with_either_crossing_switch_off_the_seat_is_left_out_and_the_task_waits(client, owner, ws, monkeypatch,
                                                                                  off):
    if off != "unset":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off" if off == "setting" else "on")
    h, user = owner
    task_id = crossing_setup(client, h, user)
    if off == "switch":
        assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    after = task_of(client, h, task_id)
    assert after["state"] == "suspended" and after["cause"] == "window" and events(task_id, "task.crossed") == []
    left = {e["account"]["account"]: e["left_out"] for e in after["relay_set"]}
    assert left[ACC["A"]] == "crossing organisations is off for this task"


def test_the_page_s_switches_say_each_setting_and_the_teammate_track_s_gate_in_effect(client, owner, ws,
                                                                                         monkeypatch):
    """TaskSettings: the deployment's switches as set, and the teammate track's as its gate has it in effect for the
    user (task_offers.enabled: the setting, the provider, the operator), off when that gate cannot be asked."""
    h, user = owner
    asked = []
    why = {"now": None}

    def enabled(who):
        asked.append(who["id"])
        if why["now"] == "raise":
            raise RuntimeError("the teammate track failed")
        return why["now"]
    monkeypatch.setattr(task_offers, "enabled", enabled)

    def settings():
        r = client.get(T, headers=h)
        assert r.status_code == 200, r.text
        return r.json()["settings"]
    assert settings() == {"relay": True, "auto_resume": True, "teammate": True, "cross_groups": False,
                          "local_provider": True}
    assert asked == [user["id"]]
    why["now"] = "This deployment does not name its operator, so offers and custody are off."
    assert settings()["teammate"] is False                      # set on, but off in effect
    why["now"] = "raise"
    assert settings()["teammate"] is False
    why["now"] = None
    for name in ("RELAY", "AUTO_RESUME", "TEAMMATE"):
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_" + name, "off")
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    asked.clear()
    assert settings() == {"relay": False, "auto_resume": False, "teammate": False, "cross_groups": True,
                          "local_provider": True}
    assert asked == []                                          # the setting off: nothing to ask


# ---- groups by orgId, the confirmation's checks, the plan table -----------------------------------------------------

def confirm(client, h, user, account_id):
    return client.post(f"{A}/accounts/{account_id}/confirm", headers=h,
                       json={"confirmation": confirmation(user, f"task-account-own:{account_id}"),
                             **shown(client, h, account_id)})


def joined(client, h, user, account_id):
    r = confirm(client, h, user, account_id)
    assert r.status_code == 200, r.text
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    return next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == account_id)


def test_two_seats_with_one_organisation_name_are_two_groups_and_one_email_is_two_accounts(client, owner, ws):
    h, user = owner
    one = report(client, h, ws, ACC["A"], email="me@corp.test", plan="Claude Team", org="org-1", org_name="Acme")
    two = report(client, h, ws, ACC["B"], email="me@corp.test", plan="Claude Team", org="org-2", org_name="Acme")
    mine = report(client, h, ws, ACC["C"], email="me@corp.test", plan="Claude Max")
    groups = [joined(client, h, user, a["id"])["grp"] for a in (one, two, mine)]
    assert groups[0] == "org:org-1" and groups[1] == "org:org-2" and groups[2] == "personal"


def test_a_renamed_organisation_is_an_identity_change_and_confirming_again_reads_its_org_id(client, owner, ws):
    h, user = owner
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme")
    joined(client, h, user, seat["id"])
    own(user, ACC["B"], plan="Claude Team", org="org-1", org_name="Acme")
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id)
    press(client, h, task["id"], "start")
    renamed = dict(identity(ACC["A"], ACC["A"] + "@example.com", plan="Claude Team", org="org-1",
                            org_name="Acme Ltd")[0])
    record(user, row(task["id"])["lease_run"], "blocked", cause="resume",
           resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": renamed, "org_id": "org-1"})
    assert acct(row(task["id"])["lease_run"]) == ACC["B"]               # that account refusing: the relay moves on
    states = {a["account"]: a["state"] for a in client.get(A + "/accounts", headers=h).json()["accounts"]}
    assert states[ACC["A"]] == "changed"
    again = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme Ltd")
    back = joined(client, h, user, again["id"])
    assert back["state"] == "confirmed" and back["org_id"] == "org-1" and back["grp"] == "org:org-1"


@pytest.mark.parametrize("field,status", [("email", {"email": "other@example.com"}),
                                          ("organization", {"orgName": "Other"}),
                                          ("plan", {"subscriptionType": "pro"}),
                                          ("orgId", {"orgId": None})])
def test_a_confirmation_whose_auth_status_disagrees_fails_naming_the_field(client, owner, ws, field, status):
    h, user = owner
    report_, auth_status = identity(ACC["A"], "a@example.com", plan="Claude Team", org="org-1", org_name="Acme")
    ws.identities[ACC["A"]] = (report_, dict(auth_status, **status))
    account = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]}).json()["account"]
    assert account["mismatch"] == field and account["org_id"] is None
    r = confirm(client, h, user, account["id"])
    assert r.status_code == 412 and field in r.json()["detail"]
    listed = client.get(A + "/accounts", headers=h).json()["accounts"][0]
    assert listed["state"] == "reported" and listed["org_id"] is None and listed["grp"] is None


@pytest.mark.parametrize("plan,word,group", [("Claude Team", "team", "org:org-1"), ("Claude Max", "max", "personal"),
                                              ("Claude Enterprise", "enterprise", "org:org-1")])
def test_the_plan_table_maps_the_report_s_display_string_to_auth_status_s_word(client, owner, ws, plan, word,
                                                                               group):
    h, user = owner
    account = report(client, h, ws, ACC["A"], plan=plan, org="org-1", org_name="Acme" if word != "max" else None)
    assert account["mismatch"] is None and account["org_id"] == "org-1"
    assert joined(client, h, user, account["id"])["grp"] == group


@pytest.mark.parametrize("plan,word", [("Claude Team", "max"), ("Claude Max", "team"), ("Claude Team", "Claude Team")])
def test_a_display_string_and_a_word_the_table_does_not_pair_are_a_mismatch(client, owner, ws, plan, word):
    h, user = owner
    account = report(client, h, ws, ACC["A"], plan=plan, org="org-1", org_name="Acme", word=word)
    assert account["mismatch"] == "plan" and account["org_id"] is None


@pytest.mark.parametrize("plan", ["Claude API", None])
def test_claude_api_or_no_plan_is_in_no_group(client, owner, ws, plan):
    h, user = owner
    report_, status = identity(ACC["A"], "a@example.com", plan="Claude Max")
    ws.identities[ACC["A"]] = (dict(report_, plan=plan), dict(status, subscriptionType="api"))
    account = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]}).json()["account"]
    r = confirm(client, h, user, account["id"])
    assert r.status_code == 412
    assert shapes.group_of(plan, "org-1") is None


# ---- the home group, and the way back to it -------------------------------------------------------------------------

def away_from_home(client, h, user, clock, steps=4):
    """A task started on a Team seat (S, org-x), its seat refused, crossed onto a personal plan (P)."""
    own(user, ACC["A"], plan="Claude Team", org="org-x", org_name="X")
    own(user, ACC["B"])
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, steps=steps)
    press(client, h, task["id"], "start")
    reset = clock[0] + HOUR
    refuse(user, row(task["id"])["lease_run"], resets=reset)
    assert acct(row(task["id"])["lease_run"]) == ACC["B"]
    return task["id"], reset


@pytest.mark.parametrize("evidence,minutes,home", [("reset", None, True), ("allowed", 14, True),
                                                   ("allowed", 16, False), ("worked", 14, True),
                                                   ("worked", 16, False)])
def test_at_a_step_s_end_the_task_goes_back_home_on_fresh_evidence(client, owner, ws, monkeypatch, clock,
                                                                  evidence, minutes, home):
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, reset = away_from_home(client, h, user, clock)
    if evidence == "reset":
        clock[0] = reset + 60
    else:
        clock[0] = reset - 30 * 60
        with store.connect() as db:
            if evidence == "allowed":
                tasks.take_windows(db, user["id"], "account:" + ACC["A"], {"five_hour": {
                    "status": "allowed", "resets_at": clock[0] + HOUR, "at": clock[0] - minutes * 60}}, None,
                    clock[0] - minutes * 60)
            else:
                # the seat allowed again long ago, and an attempt working on it `minutes` before the step's end
                db.execute("UPDATE ade_backend_windows SET status = 'allowed', resets_at = NULL, seen_at = ? WHERE "
                           "backend = ?", (clock[0] - 2 * HOUR, "account:" + ACC["A"]))
                db.execute("UPDATE ade_account_owners SET worked_at = ? WHERE account = ?",
                           (clock[0] - minutes * 60, ACC["A"]))
    finish(user, row(task_id)["lease_run"], 1, 1)
    after = row(task_id)
    if home:
        assert acct(after["lease_run"]) == ACC["A"] and after["moves"][-1] == clock[0]      # counted as a move
        assert events(task_id, "task.crossed")[-1]["payload"]["home"] == "room"
        line = task_of(client, h, task_id)["moves"][-1]
        assert line["kind"] == "home" and line["cases"] == ["back_home", "onto_seat"]
    else:
        assert acct(after["lease_run"]) == ACC["B"]


@pytest.mark.parametrize("at,seen,home", [(-2 * HOUR, -60, False), (-60, -2 * HOUR, True), (None, -60, False)])
def test_back_home_judges_room_fresh_by_the_event_s_own_time_never_by_when_the_app_copied_it(client, owner, ws,
                                                                                             monkeypatch, clock,
                                                                                             at, seen, home):
    """The home seat allowed again by an event two hours old that the app copied a minute ago (a record read late, the
    workspace unreachable meanwhile) is no fresh room, and the task stays on B; an event a minute old by its own time
    is, whenever the app copied it; one that gives no time is none. The home group's other tests copy each event at its
    own time, so they cannot tell which time decides."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, reset = away_from_home(client, h, user, clock)
    clock[0] = reset - 30 * 60
    with store.connect() as db:
        db.execute("UPDATE ade_backend_windows SET status = 'allowed', resets_at = NULL, at = ?, seen_at = ? WHERE "
                   "backend = ?", (None if at is None else clock[0] + at, clock[0] + seen, "account:" + ACC["A"]))
    finish(user, row(task_id)["lease_run"], 1, 1)
    assert acct(row(task_id)["lease_run"]) == (ACC["A"] if home else ACC["B"])
    assert len(events(task_id, "task.crossed")) == (2 if home else 1)


def test_with_its_switch_turned_off_away_from_home_its_next_attempt_is_on_a_seat_once_one_has_room(
        client, owner, ws, monkeypatch, clock):
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, reset = away_from_home(client, h, user, clock)
    own(user, ACC["C"])                                 # another personal account, with room: never taken now
    room(user, ACC["C"])
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    finish(user, row(task_id)["lease_run"], 1, 1)
    waiting = row(task_id)
    assert waiting["state"] == "suspended" and waiting["waits_until"] == reset    # the seat has no room yet
    clock[0] = reset + 1
    tasks.sweep(now=reset + 1)
    after = row(task_id)
    assert acct(after["lease_run"]) == ACC["A"] and events(task_id, "task.crossed")[-1]["payload"]["home"] == "off"


def test_turning_crossing_back_on_needs_a_confirmation_and_takes_effect_after_its_hold(client, owner, ws,
                                                                                     monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, _ = started(client, h, user)
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    assert press(client, h, task_id, "switches", cross_groups=True).status_code == 403
    assert task_of(client, h, task_id)["cross_groups"] is False
    r = press(client, h, task_id, "switches", cross_groups=True,
              confirmation=confirmation(user, f"task-cross:{task_id}"))
    assert r.status_code == 200 and r.json()["hold"]["kind"] == tasks.HOLD_CROSS
    assert r.json()["task"]["cross_groups"] is False and r.json()["task"]["holds"][0]["id"] == r.json()["hold"]["id"]
    assert "held-task-cross" in ws.kinds()
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    assert task_of(client, h, task_id)["cross_groups"] is True


def test_turning_crossing_on_again_while_its_hold_waits_is_refused_before_a_confirmation_is_spent(
        client, owner, ws, monkeypatch):
    """One turning on at a time, as an account's confirmation: pressed again
    while the first one's hold waits, it is refused (412) before its confirmation is spent, and no second hold is
    placed; that confirmation turns it on once the first hold is gone."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, _ = started(client, h, user)
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    first = press(client, h, task_id, "switches", cross_groups=True,
                  confirmation=confirmation(user, f"task-cross:{task_id}"))
    assert first.status_code == 200, first.text
    second = confirmation(user, f"task-cross:{task_id}")
    again = press(client, h, task_id, "switches", cross_groups=True, confirmation=second)
    assert (again.status_code, again.json()["detail"]) == (412, "Its turning on waits for its hold already.")
    assert [x["id"] for x in task_of(client, h, task_id)["holds"]] == [first.json()["hold"]["id"]]
    assert client.post(f"/api/ade/holds/{first.json()['hold']['id']}/cancel", headers=h, json={}).status_code == 200
    assert press(client, h, task_id, "switches", cross_groups=True, confirmation=second).status_code == 200


def test_a_task_id_is_32_hex(client, owner, ws):
    h, user = owner
    task_id, _ = started(client, h, user)
    assert shapes.TASK_ID.fullmatch(task_id)


# ---- one organisation per task, and Enterprise seats ----------------------------------------------------------------

@pytest.fixture
def orgs(client, owner, ws, monkeypatch):
    """P1 and P2 personal; S1 a seat of X, S2 a seat of Y (Team); E1 a seat of Z (Enterprise)."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    accounts = {"P1": ACC["A"], "P2": ACC["B"], "S1": ACC["C"], "S2": ACC["D"], "E1": ACC["E"]}
    own(user, accounts["P1"], plan="Claude Pro")
    own(user, accounts["P2"])
    own(user, accounts["S1"], plan="Claude Team", org="org-x", org_name="X")
    own(user, accounts["S2"], plan="Claude Team", org="org-y", org_name="Y")
    own(user, accounts["E1"], plan="Claude Enterprise", org="org-z", org_name="Z")
    return h, user, accounts


def refuse_all(user, *accounts):
    now = int(time.time())
    with store.connect() as db:
        for account in accounts:
            tasks.take_windows(db, user["id"], "account:" + account, {
                "five_hour": {"status": "rejected", "resets_at": now + HOUR, "at": now}}, None, now)


def on_task(client, h, account, steps=3, **over):
    task = make_task(client, h, make_agent(client, h, account), steps=steps, **over)
    press(client, h, task["id"], "start")
    return task["id"]


def test_a_personal_task_is_bound_to_the_first_organisation_it_crosses_into_and_no_other(client, orgs):
    h, user, acc = orgs
    room(user, acc["S1"])
    task_id = on_task(client, h, acc["P1"])
    refuse_all(user, acc["P2"])
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    assert acct(row(task_id)["lease_run"]) == acc["S1"] and row(task_id)["bound_org"] == "org-x"
    refuse_all(user, acc["P1"], acc["P2"])
    room(user, acc["S2"], acc["E1"])
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    after = task_of(client, h, task_id)
    assert after["state"] == "suspended" and after["cause"] == "window"
    left = {e["account"]["account"]: e["left_out"] for e in after["relay_set"]}
    assert left[acc["S2"]] == "another organisation's seats"
    assert left[acc["E1"]] == "an Enterprise organisation's seats"


def test_a_task_on_a_seat_never_reaches_another_organisation_s_seats(client, orgs):
    h, user, acc = orgs
    room(user, acc["S2"])
    task_id = on_task(client, h, acc["S1"])
    refuse_all(user, acc["P1"], acc["P2"])
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    assert row(task_id)["state"] == "suspended" and all(acct(r["run_id"]) != acc["S2"] for r in runs_of(task_id))


def test_no_relay_crosses_into_or_out_of_enterprise_seats(client, orgs):
    h, user, acc = orgs
    room(user, acc["P1"])
    on_e1 = on_task(client, h, acc["E1"])
    refuse(user, row(on_e1)["lease_run"], resets=int(time.time()) + HOUR)
    assert row(on_e1)["state"] == "suspended" and len(runs_of(on_e1)) == 1
    room(user, acc["E1"])
    personal = on_task(client, h, acc["P1"])
    refuse_all(user, acc["P2"], acc["S1"], acc["S2"])
    refuse(user, row(personal)["lease_run"], resets=int(time.time()) + HOUR)
    assert row(personal)["state"] == "suspended" and len(runs_of(personal)) == 1


def test_a_press_onto_another_organisation_s_seats_ends_the_task_s_crossings(client, orgs):
    h, user, acc = orgs
    room(user, acc["S1"])
    task_id = on_task(client, h, acc["P1"], between_steps="wait")
    refuse_all(user, acc["P2"])
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    assert row(task_id)["bound_org"] == "org-x"
    finish(user, row(task_id)["lease_run"], 1, 1)
    room(user, acc["S2"])
    assert press(client, h, task_id, "continue-on", backend={"kind": "account", "id": acc["S2"]}).status_code == 200
    assert row(task_id)["crossings_ended"] == 1 and row(task_id)["bound_org"] == "org-x"
    room(user, acc["S1"])
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    after = task_of(client, h, task_id)
    assert after["state"] == "suspended" and after["crossings_ended"] is True
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    assert {e["account"]["account"] for e in task_of(client, h, task_id)["relay_set"] if e["eligible"]} == {acc["S2"]}


def test_presses_onto_and_off_an_enterprise_seat_bind_the_task_and_end_its_crossings(client, orgs):
    h, user, acc = orgs
    onto = on_task(client, h, acc["P1"], between_steps="wait")
    finish(user, row(onto)["lease_run"], 1, 1)
    press(client, h, onto, "continue-on", backend={"kind": "account", "id": acc["E1"]})
    assert row(onto)["bound_org"] == "org-z" and row(onto)["crossings_ended"] == 1
    room(user, acc["S1"], acc["P2"])
    refuse(user, row(onto)["lease_run"], resets=int(time.time()) + HOUR)
    assert row(onto)["state"] == "suspended" and len(runs_of(onto)) == 2
    off = on_task(client, h, acc["E1"], between_steps="wait")
    finish(user, row(off)["lease_run"], 1, 1)
    press(client, h, off, "continue-on", backend={"kind": "account", "id": acc["P2"]})
    assert row(off)["crossings_ended"] == 1
    room(user, acc["E1"])
    refuse_all(user, acc["P1"])
    refuse(user, row(off)["lease_run"], resets=int(time.time()) + HOUR)
    assert row(off)["state"] == "suspended" and all(acct(r["run_id"]) != acc["E1"] or r["fence"] == 1
                                                    for r in runs_of(off))


def test_a_press_onto_a_team_seat_binds_a_task_bound_to_none_and_its_crossings_go_on(client, orgs, clock):
    h, user, acc = orgs
    task_id = on_task(client, h, acc["P1"], steps=4, between_steps="wait")
    finish(user, row(task_id)["lease_run"], 1, 1)
    press(client, h, task_id, "continue-on", backend={"kind": "account", "id": acc["S1"]})
    assert row(task_id)["bound_org"] == "org-x" and row(task_id)["crossings_ended"] == 0
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET between_steps = 'any'")
    room(user, acc["P1"])
    finish(user, row(task_id)["lease_run"], 2, 2)
    assert acct(row(task_id)["lease_run"]) == acc["P1"]                    # home, at the step's end
    refuse_all(user, acc["P2"])
    room(user, acc["S1"], acc["S2"])
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    assert acct(row(task_id)["lease_run"]) == acc["S1"]                    # onto S1 by itself, never S2


# ---- the bound of 10 moves an hour -----------------------------------------------------------------------------------

def _confirmed_again(client, h, user, ws, account, **identity_fields):
    """The account read again and confirmed as the owner's under its new identity, its confirmation's hold settled."""
    seat = report(client, h, ws, account, **identity_fields)
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    with store.connect() as db:
        return tasks._owner(db, user["id"], account)


def _allowed_now(user, clock, *accounts):
    with store.connect() as db:
        for account in accounts:
            tasks.take_windows(db, user["id"], "account:" + account, {"five_hour": {
                "status": "allowed", "resets_at": clock[0] + HOUR, "at": clock[0]}}, None, clock[0])


def _regroupings(task_id):
    """The task's task.account_regrouped lines' payloads, and its account record's regrouped entries as
    (accounts, step, run_id, fence, via)."""
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_task_account_record WHERE task_id = ? AND kind = 'regrouped' ORDER BY seq",
                          (task_id,)).fetchall()
    return ([e["payload"] for e in events(task_id, "task.account_regrouped")],
            [(json.loads(r["accounts"]), r["step"], r["run_id"], r["fence"], r["via"]) for r in rows])


# what task.account_regrouped's `why` opens with, and ends with, by the plan the account was confirmed as now, what
# the confirmation did to the task's binding, and what the task's relay does with the account (tasks._regrouped_why)
REGROUPED = ("Its account was confirmed again, now as {on}: from now the task's attempts on it run on that plan's usage, "
             "because of that confirmation, not a press or the relay.")
BOUND_CHANGED = " Its binding changed as a press from the old group onto the new one would."
BOUND_SAME = " Its binding needed no change."
LEFT_OFFERED = " The task was offered then, so its binding stays as it was."
# whether the task's relay takes the account now, and where it does not, why, and what the
# app does with the task by itself instead (tasks._regrouped_relay_words): an Enterprise seat that sends the task
# nowhere, an attempt queued before, the custodian named only where
# custody could take the task, no word where the relay could not be read, and "now"
RELAY_TAKES = " Its relay takes that account now."
LEFT_OUT = (" Its relay leaves that account out now ({why}), so by itself the app queues no attempt on it, though one "
            "queued before runs as it would. By itself the app moves the task ")
GOES_HOME = "only home"
MOVES_ON = "only onto another account its relay takes, in its relay's order"
CUSTODIAN = ", or to your custodian if you have one"
WAITS = ", and otherwise leaves it waiting."
NOWHERE = "onto none of your other accounts, and leaves it waiting."
STAYS = (" Its relay leaves that account out now ({why}), but the relay is off for the task, so by itself the app "
         "keeps it on that account.")
# in the "home" case custody is asked only after a refusal, and an account
# out for its weekly reserve alone takes an attempt again once that window resets
HOME_BUT_CUSTODIAN = ", or, after an attempt on that account is refused, to your custodian if you have one"
LEFT_OUT_RESERVE = (" Its relay leaves that account out now (its weekly reserve), so by itself the app queues no "
                    "attempt on it until its weekly window resets, though one queued before runs as it would. By "
                    "itself the app moves the task ")
UNREAD = " Whether its relay takes that account now could not be read."
CROSSING_OFF = "crossing organisations is off for this task"
ENTERPRISE_OUT = "an Enterprise organisation's seats"
RECEIVED_OUT = "a task received from a teammate stays in its group"


def _snap(account, email, plan, organization=None, org_id=None):
    return {"ref": {"kind": "account", "id": account}, "email": email, "plan": plan, "organization": organization,
            "org_id": org_id}


def test_an_account_confirmed_again_into_another_group_records_on_each_task_on_it_whose_usage_it_now_runs_on(
        client, owner, ws):
    # each task on an account confirmed
    # again into another group gets an entry in its account record and a line in its journal saying whose usage its
    # attempts on the account now run on and why. A task on another account, one that ended and one deleted get
    # neither, and a confirmation that leaves the account in its group writes nothing
    h, user = owner
    task_id, _ = started(client, h, user, between_steps="wait")          # on A, a personal plan
    ready = make_task(client, h, make_agent(client, h, ACC["A"]))["id"]   # on A, never started
    ended = make_task(client, h, make_agent(client, h, ACC["A"]))["id"]
    gone = make_task(client, h, make_agent(client, h, ACC["A"]))["id"]
    own(user, ACC["B"])
    other = make_task(client, h, make_agent(client, h, ACC["B"]))["id"]
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET state = 'done' WHERE id = ?", (ended,))
        db.execute("UPDATE ade_agent_tasks SET deleted_at = 1 WHERE id = ?", (gone,))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    before = _snap(ACC["A"], ACC["A"] + "@example.com", "Claude Max")
    team = _snap(ACC["A"], ACC["A"] + "@example.com", "Claude Team", "Tee", "org-t")
    for tid in (task_id, ready):
        # crossing is off (the deployment's default), so its relay set is the personal plans alone
        assert _regroupings(tid) == ([{
            "account": "account:" + ACC["A"], "from_grp": "personal", "from_kind": "personal", "to_grp": "org:org-t",
            "to_kind": "team", "bound": {"bound_org": "org-t"}, "left_out": None,
            "why": REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
            + LEFT_OUT.format(why=CROSSING_OFF) + GOES_HOME + WAITS}],
            [([before, team], row(tid)["step"], None, None, None)]), tid
    for tid in (ended, gone, other):
        assert _regroupings(tid) == ([], []), tid
    # the control: confirmed again in the group it is in, under another address, nothing more is written
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee",
                     email="other-" + ACC["A"] + "@example.com")
    assert [len(x) for x in _regroupings(task_id)] == [1, 1]
    # and back to a personal plan: off a Team seat the binding needs no change, and the entry starts from the address
    # confirmed last
    _confirmed_again(client, h, user, ws, ACC["A"])
    lines, entries = _regroupings(task_id)
    assert (lines[-1]["to_grp"], lines[-1]["bound"], lines[-1]["left_out"]) == ("personal", {}, None)
    assert lines[-1]["why"] == REGROUPED.format(on="a personal plan") + BOUND_SAME + RELAY_TAKES
    assert entries[-1][0] == [_snap(ACC["A"], "other-" + ACC["A"] + "@example.com", "Claude Team", "Tee", "org-t"),
                              _snap(ACC["A"], ACC["A"] + "@example.com", "Claude Max", None, "personal-" + ACC["A"])]
    assert row(task_id)["bound_org"] == "org-t"
    # the account as confirmed before is what was confirmed, its orgId among it: here the personal plan's, read with
    # it; only a confirmation that holds none (the first, made by own() as an older store keeps it) takes its group's
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    assert _regroupings(task_id)[1][-1][0][0] == _snap(ACC["A"], ACC["A"] + "@example.com", "Claude Max", None,
                                                       "personal-" + ACC["A"])


@pytest.mark.parametrize("cross", ["on", "off"])
def test_an_account_confirmed_again_onto_an_enterprise_seat_puts_its_task_there_as_a_press_would(
        client, owner, ws, monkeypatch, clock, cross):
    # the hold's end once moved the account's row to its new group and looked at no
    # task on it, so a personal task found itself on an Enterprise organisation's seat with its crossings and binding
    # as they were; at its step's end the relay carried it off that seat, and with crossing on, a refusal there took
    # it on into a Team organisation's seats and bound it there (no automatic move ever carries one organisation's work
    # into another)
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", cross)
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    enterprise = identity(ACC["A"], ACC["A"] + "@example.com", plan="Claude Enterprise", org="org-z",
                          org_name="Zed")[0]
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": enterprise, "org_id": "org-z"})
    assert (row(task_id)["state"], row(task_id)["cause"]) == ("needs_owner", "identity")
    moved = _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert (moved["state"], moved["grp"], moved["grp_kind"]) == ("confirmed", "org:org-z", "enterprise")
    t = row(task_id)
    assert (t["crossings_ended"], t["bound_org"], t["home_grp"]) == (1, "org-z", "personal")
    assert press(client, h, task_id, "continue").status_code == 200
    own(user, ACC["B"])
    own(user, ACC["C"], plan="Claude Team", org="org-t", org_name="Tee")
    _allowed_now(user, clock, ACC["B"], ACC["C"])
    finish(user, row(task_id)["lease_run"], 1, 1)
    t = row(task_id)
    assert acct(t["lease_run"]) == ACC["A"] and not events(task_id, "task.crossed")
    refuse(user, t["lease_run"], resets=clock[0] + HOUR)
    assert all(acct(r["run_id"]) == ACC["A"] for r in runs_of(task_id))
    assert not events(task_id, "task.crossed") and row(task_id)["bound_org"] == "org-z"


def test_an_account_confirmed_again_off_an_enterprise_seat_ends_its_task_s_crossings(client, owner, ws):
    h, user = owner
    own(user, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, between_steps="wait")
    assert press(client, h, task["id"], "start").status_code == 200
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (0, "org-z")
    _confirmed_again(client, h, user, ws, ACC["A"])                     # a personal plan now
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (1, "org-z")
    lines, entries = _regroupings(task["id"])
    assert [(x["from_grp"], x["from_kind"], x["to_grp"], x["to_kind"], x["bound"], x["why"]) for x in lines] == [
        ("org:org-z", "enterprise", "personal", "personal", {"crossings_ended": 1},
         REGROUPED.format(on="a personal plan") + BOUND_CHANGED + RELAY_TAKES)]
    assert [e[0][0]["plan"] for e in entries] == ["Claude Enterprise"]


def test_a_regrouping_journals_as_bound_only_what_it_changed_of_the_task_s_binding(client, owner, ws):
    # _onto_group gives the fields a press sets, read against none of the task's own, and
    # the line journaled them all as `bound` and said the binding changed. An Enterprise task confirmed onto a personal
    # plan has its crossings ended; confirmed then onto a Team seat of another organisation, a press would end them
    # again, but they had ended already: nothing of the binding changed, and the line says so
    h, user = owner
    own(user, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    task = make_task(client, h, make_agent(client, h, ACC["A"]), between_steps="wait")
    assert press(client, h, task["id"], "start").status_code == 200
    _confirmed_again(client, h, user, ws, ACC["A"])                     # a personal plan now
    version = row(task["id"])["state_version"]
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    lines, _ = _regroupings(task["id"])
    assert [(x["to_grp"], x["bound"]) for x in lines] == [("personal", {"crossings_ended": 1}), ("org:org-t", {})]
    assert lines[-1]["why"] == REGROUPED.format(on="a seat of a Team organisation") + BOUND_SAME + RELAY_TAKES
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (1, "org-z")
    # the fields _onto_group gives for _set are kept unchanged: the binding is set with
    # every field a press from the old group onto the new one sets, as before; only the line names fewer. So the row is
    # written, and its state_version moves by 1, _set's own as for every change, though no value changed. This pins a
    # consequence of that: a press the owner makes with the version he saw before is
    # refused, under a line that says the binding needed no change
    assert row(task["id"])["state_version"] == version + 1


def test_an_account_confirmed_again_onto_another_team_organisation_ends_its_bound_task_s_crossings(
        client, owner, ws):
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-1", org_name="One")
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, between_steps="wait")
    assert press(client, h, task["id"], "start").status_code == 200
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (0, "org-1")
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-2", org_name="Two")
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (1, "org-1")


def test_an_account_confirmed_again_onto_a_team_seat_binds_a_personal_task_and_its_crossings_go_on(
        client, owner, ws):
    h, user = owner
    task_id, _ = started(client, h, user, between_steps="wait")
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (0, None)
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (0, "org-t")
    # the control: confirmed again in the group it is in, nothing of the task changes
    version = row(task_id)["state_version"]
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee",
                     email="other-" + ACC["A"] + "@example.com")
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"], row(task_id)["state_version"]) == \
        (0, "org-t", version)


@pytest.mark.parametrize("before, after", [("Claude Team", "Claude Enterprise"), ("Claude Enterprise", "Claude Team")])
def test_an_account_confirmed_again_as_another_kind_of_seat_of_its_organisation_ends_its_task_s_crossings(
        client, owner, ws, before, after):
    # a group is its organisation's id and its kind comes from the plan, and the hold's end once compared ids alone, so
    # an account confirmed again onto or off an Enterprise seat of the same organisation
    # changed no task on it
    h, user = owner
    own(user, ACC["A"], plan=before, org="org-z", org_name="Zed")
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, between_steps="wait")
    assert press(client, h, task["id"], "start").status_code == 200
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (0, "org-z")
    moved = _confirmed_again(client, h, user, ws, ACC["A"], plan=after, org="org-z", org_name="Zed")
    assert (moved["grp"], moved["grp_kind"]) == ("org:org-z", "enterprise" if "Enterprise" in after else "team")
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (1, "org-z")
    on = "a seat of an Enterprise organisation" if "Enterprise" in after else "a seat of a Team organisation"
    lines, entries = _regroupings(task["id"])
    assert [(x["from_grp"], x["to_grp"], x["why"]) for x in lines] == [
        ("org:org-z", "org:org-z", REGROUPED.format(on=on + " (the same organisation's, another kind of seat)")
         + BOUND_CHANGED + RELAY_TAKES)]
    assert [[a["plan"] for a in e[0]] for e in entries] == [[before, after]]


def test_an_account_confirmed_again_as_an_enterprise_seat_of_an_organisation_read_as_one_leaves_its_task_s_binding(
        client, owner, ws):
    """At a confirmation: A, a Team seat of org-z, which also
    holds G, an Enterprise seat of the owner's, so that org-z reads as an Enterprise organisation; a task made on A.
    A confirmed again as an Enterprise seat of org-z: the binding is the one a press between the two groups as they are
    read gives (_account_moved's `read`), one Enterprise organisation's seats to the same one's, so none of it changes
    and the task's crossings are not ended, where by the seats' own kinds, a Team seat onto an Enterprise one, they
    would be; its line says its binding needed no change."""
    h, user = owner
    own(user, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    agent_id = make_agent(client, h, ACC["A"])
    task = make_task(client, h, agent_id, between_steps="wait")
    assert press(client, h, task["id"], "start").status_code == 200
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (0, "org-z")
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (0, "org-z")
    lines, _ = _regroupings(task["id"])
    assert [(x["from_grp"], x["to_grp"], x["bound"]) for x in lines] == [("org:org-z", "org:org-z", {})]
    assert BOUND_SAME in lines[0]["why"]


@pytest.mark.parametrize("plan", ["Claude Team", "Claude Enterprise"])
def test_the_relay_crosses_into_the_organisation_a_task_is_bound_to_only_while_it_holds_no_enterprise_seat(
        client, owner, ws, monkeypatch, plan):
    # the relay set once matched a group by its id, whatever its kind, so a personal task bound to a Team organisation
    # was carried by itself onto a seat of it that is now an Enterprise seat (relay_groups: no relay crosses into an
    # Enterprise organisation's seats). Then the organisation, with a Team seat of it too, G, read as a Team one, and
    # the relay crossed onto G; an organisation holding seats of both kinds now reads as an Enterprise one, so with A an
    # Enterprise seat the relay crosses onto neither seat and the task waits. The control: A a Team seat, first, the
    # relay crosses onto it
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["B"])
    own(user, ACC["A"], plan=plan, org="org-z", org_name="Zed")
    own(user, ACC["G"], plan="Claude Team", org="org-z", org_name="Zed")        # after A in Connections' order
    room(user, ACC["A"], ACC["G"])
    agent_id = make_agent(client, h, ACC["B"])
    task = make_task(client, h, agent_id)
    assert press(client, h, task["id"], "start").status_code == 200
    with store.connect() as db:                 # bound as a press onto a Team seat of org-z would have bound it
        db.execute("UPDATE ade_agent_tasks SET bound_org = 'org-z' WHERE id = ?", (task["id"],))
    refuse(user, row(task["id"])["lease_run"], resets=int(time.time()) + HOUR)
    if plan == "Claude Team":
        assert acct(row(task["id"])["lease_run"]) == ACC["A"] and events(task["id"], "task.crossed")
        return
    after = task_of(client, h, task["id"])
    assert after["state"] == "suspended" and not events(task["id"], "task.crossed")
    assert all(acct(r["run_id"]) == ACC["B"] for r in runs_of(task["id"]))
    left = {e["account"]["account"]: e["left_out"] for e in after["relay_set"]}
    assert left[ACC["A"]] == left[ACC["G"]] == "an Enterprise organisation's seats"


def test_a_task_on_a_team_seat_of_an_organisation_holding_an_enterprise_seat_has_that_seat_in_its_relay_set(
        client, owner, ws, monkeypatch):
    # a group once read as its organisation's id and its kind as the seat's plan put an
    # Enterprise seat of the Team organisation a task was made in out of its relay set, and the task crossed to a
    # personal plan; an organisation holding seats of both kinds reads as an Enterprise one, so the task's relay set, as
    # the page shows it, holds that seat as one of its
    # group's and leaves the personal plan out, as the relay set of an Enterprise organisation's task does; each
    # account says the kind it was confirmed as (grp_kind) and the kind it is read as (org_kind)
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    own(user, ACC["E"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["B"])
    room(user, ACC["E"], ACC["B"])
    task_id, _ = started(client, h, user)
    view = {e["account"]["account"]: e for e in task_of(client, h, task_id)["relay_set"]}
    assert {a: (e["account"]["grp_kind"], e["account"]["org_kind"], e["eligible"], e["left_out"])
            for a, e in view.items()} == {
        ACC["A"]: ("team", "enterprise", True, None), ACC["E"]: ("enterprise", "enterprise", True, None),
        ACC["B"]: ("personal", "personal", False,
                   "no relay crosses into or out of an Enterprise organisation's seats")}


def test_a_task_a_confirmation_put_on_an_enterprise_seat_relays_onto_a_team_seat_its_organisation_holds_besides(
        client, owner, ws, clock):
    # an account confirmed again as an Enterprise seat of the Team organisation it was a seat of ends its task's
    # crossings, so its relay set is that organisation's group; a Team seat of it stayed in the set, and the relay
    # carried the task off the Enterprise seat onto it, which a fix once stopped. The organisation, once it holds that
    # Team seat of his besides, reads as an Enterprise one, so the Team seat is one of its seats as the relay reads
    # them, and the relay moves the task onto it as within any group
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, steps=3)
    enterprise = identity(ACC["A"], ACC["A"] + "@example.com", plan="Claude Enterprise", org="org-z",
                          org_name="Zed")[0]
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": enterprise, "org_id": "org-z"})
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (1, "org-z")
    assert press(client, h, task_id, "continue").status_code == 200
    # a Team seat of org-z whose account id sorts before A's, so that neither order of the ids decides it
    own(user, ACC["G"], plan="Claude Team", org="org-z", org_name="Zed")
    _allowed_now(user, clock, ACC["G"])
    with store.connect() as db:
        entries = {e["account"]: e for e in tasks.relay_set(db, user["id"], task_id, clock[0])}
    assert (entries[ACC["G"]]["org_kind"], entries[ACC["G"]]["left_out"]) == ("enterprise", None)
    refuse(user, row(task_id)["lease_run"], resets=clock[0] + HOUR)
    after = row(task_id)
    assert acct(after["lease_run"]) == ACC["G"] and not events(task_id, "task.crossed")
    assert (after["crossings_ended"], after["bound_org"]) == (1, "org-z")


@pytest.mark.parametrize("state, changed", [("handed_over", True), ("returning", True), ("offering", False)])
def test_an_account_confirmed_again_binds_a_task_with_a_teammate_but_never_an_offered_one(
        client, owner, ws, state, changed):
    # the hold's end once added 1 to an offered task's state_version, its offer's fence, so the
    # teammate's acceptance was refused ("This offer no longer waits for you.") and the offer stayed offered, saying
    # nothing; a handed-over or returning task comes back onto this account, and nothing of its hand-off compares it
    h, user = owner
    task_id, _ = started(client, h, user, between_steps="wait")
    with store.connect() as db:                 # as task_offers' hand-off leaves it
        db.execute("UPDATE ade_agent_tasks SET state = ?, lease_run = NULL WHERE id = ?", (state, task_id))
    version = row(task_id)["state_version"]
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    t = row(task_id)
    if changed:
        assert (t["crossings_ended"], t["bound_org"], t["state"]) == (1, "org-z", state)
    else:
        assert (t["crossings_ended"], t["bound_org"], t["state_version"]) == (0, None, version)
    # each of them is told, the offered one too,
    # its line and entry changing nothing of its row
    lines, entries = _regroupings(task_id)
    on = REGROUPED.format(on="a seat of an Enterprise organisation")
    # the offered one keeps its binding, so its relay set stays the personal plans, and the seat is out of it
    assert [(x["bound"], x["left_out"], x["why"]) for x in lines] == (
        [({"crossings_ended": 1, "bound_org": "org-z"}, None, on + BOUND_CHANGED + RELAY_TAKES)] if changed
        else [({}, "offered", on + LEFT_OFFERED + LEFT_OUT.format(why=ENTERPRISE_OUT) + NOWHERE)])
    assert [[a["plan"] for a in e[0]] for e in entries] == [["Claude Max", "Claude Enterprise"]]


@pytest.mark.parametrize("custody_cross", [0, 1])
def test_an_account_confirmed_again_binds_a_task_received_from_a_teammate_whatever_its_tick(
        client, owner, ws, custody_cross):
    # a custody task whose custody_cross is 1 crosses as any task does, and left out of the binding, it kept the old
    # fault whole. A task received by a pressed offer is bound too, as a custody task is whatever its tick; before, it
    # was left out ("received"), its relay keeping to the group it was accepted in, and its account's new seat out of
    # that group
    h, user = owner
    task_id, _ = started(client, h, user, between_steps="wait")
    # as task_offers' acceptance makes it, and custody's crossing tick; its offer id names no offer's row, so its
    # unticked case
    # is a task received by a pressed offer, or a custody task whose offer's row the prune removed
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET continued_from = ?, custody_cross = ? WHERE id = ?",
                   (json.dumps({"task": "e" * 32, "offer": "f" * 32, "packet_digest": "0" * 64, "from_user": "x"}),
                    custody_cross, task_id))
    version = row(task_id)["state_version"]
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    t = row(task_id)
    # told either way, its record's entry the
    # receiving user's own account, as confirmed before and now
    lines, entries = _regroupings(task_id)
    on = REGROUPED.format(on="a seat of an Enterprise organisation")
    # either way its crossings end on the Enterprise seat, as a press onto one ends them, so its relay set is the
    # group it is on, and its relay takes the account
    assert [(x["bound"], x["left_out"], x["why"]) for x in lines] == [
        ({"crossings_ended": 1, "bound_org": "org-z"}, None, on + BOUND_CHANGED + RELAY_TAKES)]
    assert [[a["org_id"] for a in e[0]] for e in entries] == [[None, "org-z"]]
    assert (t["crossings_ended"], t["bound_org"], t["state_version"]) == (1, "org-z", version + 1)
    with store.connect() as db:
        assert tasks.relay_groups(db, t)[0] == ["org:org-z"]


# ---- what the regrouping's line says of the task's relay, and what it is written with --------------------------------

@pytest.mark.parametrize("home_has_room", [True, False])
def test_a_personal_task_s_account_confirmed_onto_a_team_seat_is_told_it_moves_only_home_and_it_goes_home_or_waits(
        client, owner, ws, home_has_room):
    # the line once said the task's attempts on its account run on the Team seat's usage, while
    # with crossing off (the deployment's default) its relay set is the personal plans alone, so at its step's end
    # _next takes it home, or it waits. The line now says so, and the step's end does what it says: home where an
    # account there takes it, else it waits (the second case waits)
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="any")        # on A, a personal plan
    own(user, ACC["B"])
    if home_has_room:
        room(user, ACC["B"])
    else:
        with store.connect() as db:
            db.execute("UPDATE ade_account_owners SET in_relays = 0 WHERE account = ?", (ACC["B"],))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    assert _regroupings(task_id)[0][-1]["why"] == (REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
                                                   + LEFT_OUT.format(why=CROSSING_OFF) + GOES_HOME + WAITS)
    with store.connect() as db:
        entry = next(e for e in tasks.relay_set(db, user["id"], task_id, int(time.time())) if e["account"] == ACC["A"])
    assert (entry["eligible"], entry["left_out"]) == (False, CROSSING_OFF)
    # the attempt queued before the confirmation, not yet started, runs as
    # it would
    assert run(first)["state"] == "queued"
    finish(user, first, 1, 1)
    if home_has_room:
        assert acct(row(task_id)["lease_run"]) == ACC["B"]                       # home, by itself
        assert [(e["payload"]["to_grp"], e["payload"]["home"]) for e in events(task_id, "task.crossed")] == [
            ("personal", "off")]
    else:
        assert (row(task_id)["state"], row(task_id)["cause"]) == ("needs_owner", "window")       # it waits for you
        assert [acct(r["run_id"]) for r in runs_of(task_id)] == [ACC["A"]]


def test_a_received_task_whose_account_moves_onto_an_enterprise_seat_is_bound_there_and_goes_on_on_it(
        client, owner, ws):
    # the line's second case: a task received from a teammate at a pressed offer's acceptance gets the binding a press
    # would, as a custody task does, so its account confirmed as an Enterprise seat binds it to that organisation and
    # ends its crossings, and its relay set is the group it is on, whose account takes it: at its step's end it goes on
    # on A, though B, at home, has room. Before, it was left out ("received"), its relay set the personal plans, none of
    # which the relay reaches from the Enterprise seat (_enterprise_move), and it waited, the line saying the app moves
    # it onto none of the owner's other accounts
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="any")
    with store.connect() as db:                 # as task_offers' acceptance makes it, custody's crossing not ticked
        db.execute("UPDATE ade_agent_tasks SET continued_from = ?, custody_cross = 0 WHERE id = ?",
                   (json.dumps({"task": "e" * 32, "offer": "f" * 32, "packet_digest": "0" * 64, "from_user": "x"}),
                    task_id))
    own(user, ACC["B"])
    room(user, ACC["B"])
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert _regroupings(task_id)[0][-1]["why"] == (REGROUPED.format(on="a seat of an Enterprise organisation")
                                                   + BOUND_CHANGED + RELAY_TAKES)
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (1, "org-z")
    finish(user, first, 1, 1)
    assert [acct(r["run_id"]) for r in runs_of(task_id)] == [ACC["A"], ACC["A"]]


@pytest.mark.parametrize("cross", ["off", "on"])
def test_a_task_s_account_confirmed_onto_an_enterprise_seat_its_relay_does_not_hold_is_told_it_goes_nowhere(
        client, owner, ws, monkeypatch, cross):
    # for a task of the owner's own: an offered task keeps its binding, so its
    # account confirmed onto an Enterprise seat leaves it on a seat its relay set does not hold, and from there the
    # relay reaches no account of the owner's, home or a Team seat, crossing on or off (_enterprise_move). The line
    # said it moves only home, or on in its relay's order; it now says nowhere. Once the offer ends, the task waits
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", cross)
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    record(user, first, "stopped")
    own(user, ACC["B"])
    own(user, ACC["C"], plan="Claude Team", org="org-t", org_name="Tee")
    room(user, ACC["B"], ACC["C"])
    with store.connect() as db:                 # an offer standing, as task_offers' hand-off leaves it
        db.execute("UPDATE ade_agent_tasks SET state = 'offering', lease_run = NULL WHERE id = ?", (task_id,))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert _regroupings(task_id)[0][-1]["why"] == (REGROUPED.format(on="a seat of an Enterprise organisation")
                                                   + LEFT_OFFERED + LEFT_OUT.format(why=ENTERPRISE_OUT) + NOWHERE)
    with store.connect() as db:
        entries = {e["account"]: e for e in tasks.relay_set(db, user["id"], task_id, int(time.time()))}
    assert [entries[ACC[k]]["eligible"] for k in "ABC"] == [False, False, False]
    with store.connect() as db:                 # the offer ended, the task back as it waited
        db.execute("UPDATE ade_agent_tasks SET state = 'suspended', cause = 'stopped' WHERE id = ?", (task_id,))
    assert press(client, h, task_id, "continue").status_code == 200
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    assert all(acct(r["run_id"]) == ACC["A"] for r in runs_of(task_id))
    assert row(task_id)["state"] == "suspended"


@pytest.mark.parametrize("home", ["none", "before the line", "after the line"])
def test_a_regrouping_s_relay_sentence_holds_at_a_reset_auto_resume_moving_the_task_only_home(
        client, owner, ws, clock, home):
    # the line says the app queues no attempt on an account its relay leaves
    # out, and a task that waits for that account's reset when it is confirmed onto a Team seat (crossing off) is
    # taken at the reset by auto-resume, not by _next or the relay. The line once said the
    # app moves the task onto none of the owner's other accounts where he had none at home when it was written, and
    # an account at home confirmed after it then took the task at the reset. The line now reads the groups the relay
    # may move the task into, not the accounts there when it is written: "only home", whatever the owner has at home.
    # At the reset auto-resume moves the task home onto B where B is his by then, confirmed before the line or after it,
    # and with none it waits for him, nothing queued on A
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    reset = clock[0] + HOUR
    refuse(user, first, resets=reset)
    assert (row(task_id)["state"], row(task_id)["waits_until"]) == ("suspended", reset)
    if home == "before the line":
        own(user, ACC["B"])
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    assert _regroupings(task_id)[0][-1]["why"] == (REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
                                                   + LEFT_OUT.format(why=CROSSING_OFF) + GOES_HOME + WAITS)
    if home == "after the line":
        own(user, ACC["B"])                     # a personal plan, confirmed while the task waits
    clock[0] = reset + 1
    tasks.sweep(now=reset + 1)
    if home == "none":
        assert [r["run_id"] for r in runs_of(task_id)] == [first]
        assert (row(task_id)["state"], row(task_id)["cause"]) == ("needs_owner", "window")
        return
    assert [acct(r["run_id"]) for r in runs_of(task_id)] == [ACC["A"], ACC["B"]]
    assert [e["payload"]["by"] for e in events(task_id, "task.continued")][-1] == "auto_resume"
    assert [(e["payload"]["to_grp"], e["payload"]["home"]) for e in events(task_id, "task.crossed")] == [
        ("personal", "off")]


@pytest.mark.parametrize("off", ["deployment", "task"])
def test_with_the_relay_off_a_regrouping_says_the_task_stays_on_its_account_and_it_does(
        client, owner, ws, monkeypatch, off):
    # with the relay off, the deployment's or the task's own, _next keeps the task on the account it is on whatever
    # its relay set says (_may_stay)
    if off == "deployment":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "off")
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="any")
    if off == "task":
        assert press(client, h, task_id, "switches", relay=False).status_code == 200
    own(user, ACC["B"])
    room(user, ACC["A"], ACC["B"])
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    assert _regroupings(task_id)[0][-1]["why"] == (REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
                                                   + STAYS.format(why=CROSSING_OFF))
    finish(user, first, 1, 1)
    assert [acct(r["run_id"]) for r in runs_of(task_id)] == [ACC["A"], ACC["A"]]    # its next step there


def test_a_regrouping_says_a_task_on_an_account_its_relay_leaves_out_moves_on_in_its_relay_s_order(
        client, owner, ws):
    # the line's other case: a task in no group but the one it is on (its crossings ended), on an account its relay
    # leaves out for another reason (taken out of relays): _next hands it to the relay, which moves it onto another
    # account it takes, in its order, or waits. Two accounts of that group have room, and the relay takes the first in
    # Connections' order after the one it leaves
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-1", org_name="One")
    own(user, ACC["C"], plan="Claude Team", org="org-2", org_name="Two")
    own(user, ACC["B"], plan="Claude Team", org="org-2", org_name="Two")
    room(user, ACC["B"], ACC["C"])
    task = make_task(client, h, make_agent(client, h, ACC["A"]), steps=3, between_steps="any")
    assert press(client, h, task["id"], "start").status_code == 200
    first = row(task["id"])["lease_run"]
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET in_relays = 0 WHERE account = ?", (ACC["A"],))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-2", org_name="Two")
    assert (row(task["id"])["crossings_ended"], row(task["id"])["bound_org"]) == (1, "org-1")
    assert _regroupings(task["id"])[0][-1]["why"] == (
        REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
        + LEFT_OUT.format(why="taken out of relays") + MOVES_ON + WAITS)
    finish(user, first, 1, 1)
    assert acct(row(task["id"])["lease_run"]) == ACC["C"]


def test_a_regrouping_says_a_task_away_from_home_with_crossing_on_moves_on_in_its_relay_s_order(
        client, owner, ws, monkeypatch):
    # and the case of _next's first branch with crossing on: a personal task bound to the Team organisation its account
    # was confirmed onto, its crossings going on, so its relay set is the personal plans and that organisation's seats;
    # its account taken out of relays, it goes home first, its relay's order, where an account there has room: B, at
    # home, rather than C, a seat of that organisation before B in Connections' order
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="any")        # on A, a personal plan
    own(user, ACC["C"], plan="Claude Team", org="org-t", org_name="Tee")
    own(user, ACC["B"])
    room(user, ACC["B"], ACC["C"])
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET in_relays = 0 WHERE account = ?", (ACC["A"],))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (0, "org-t")
    assert _regroupings(task_id)[0][-1]["why"] == (
        REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
        + LEFT_OUT.format(why="taken out of relays") + MOVES_ON + WAITS)
    finish(user, first, 1, 1)
    assert acct(row(task_id)["lease_run"]) == ACC["B"]


def test_a_regrouping_says_an_account_out_for_its_weekly_reserve_takes_no_attempt_until_its_window_resets(
        client, owner, ws, clock):
    # "queues no attempt on it" was once false for the one reason that lapses by
    # itself. An account out of the relay set for its weekly reserve alone (a weekly window near its limit) takes no
    # attempt the app queues by itself until that window resets; then the app's own next decision is to queue there:
    # at the attempt's end the relay finds no other account and the task waits for that reset, and at it auto-resume
    # queues on the account. A Team seat confirmed as an Enterprise seat of its organisation stays in the task's relay
    # set (its crossings end there), so only the reserve leaves it out
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, steps=3, between_steps="any")
    reset = clock[0] + HOUR
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"], {"seven_day": {
            "status": "allowed_warning", "resets_at": reset, "at": clock[0]}}, None, clock[0])
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert _regroupings(task_id)[0][-1]["why"] == (
        REGROUPED.format(on="a seat of an Enterprise organisation (the same organisation's, another kind of seat)")
        + BOUND_CHANGED + LEFT_OUT_RESERVE + MOVES_ON + WAITS)
    finish(user, first, 1, 1)                   # the attempt queued before the confirmation ran as it would
    assert (row(task_id)["state"], row(task_id)["cause"], row(task_id)["waits_until"]) == ("suspended", "window", reset)
    assert [r["run_id"] for r in runs_of(task_id)] == [first]
    clock[0] = reset + 1
    tasks.sweep(now=reset + 1)
    assert [acct(r["run_id"]) for r in runs_of(task_id)] == [ACC["A"], ACC["A"]]
    assert [e["payload"]["by"] for e in events(task_id, "task.continued")][-1] == "auto_resume"


@pytest.mark.parametrize("custody_cross", [1, 0])
def test_a_custody_task_whose_custodian_may_cross_moves_on_in_its_relay_s_order_and_one_that_may_not_waits_for_home(
        client, owner, ws, custody_cross):
    # a custody task whose custody_cross is 1 on an account its relay leaves out, where "home" against "relay" turns on
    # _crossing_on reading custody_cross. Received with its
    # crossing ticked, its relay set crosses as a crossing may (the personal plans and the Team organisation it is bound
    # to), so its account moved onto that organisation's seat stays in it, out only for being taken out of relays, and
    # the relay moves it on in its order; not ticked, its set is the group it was accepted in, the seat is out of it,
    # and the app moves it only home. Where both cases end on B, at home, the contrast is in the words alone, so with B
    # out on a refused window and C, a seat of that organisation, with room, the relay moves
    # the first onto C, and the second, which may go nowhere but home, waits for B's reset, running only on A. An
    # unticked custody task is told from a task received by a pressed offer by its
    # offer's row, so the task has an offer of kind custody behind it, as custody's take makes one; with none, its
    # second case would be a pressed offer's task
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="any")        # on A, a personal plan
    now = int(time.time())
    with store.connect() as db:                 # as custody's take makes it
        db.execute("INSERT INTO ade_agent_task_offers (id, user_id, task_id, kind, state, fence, created_at, "
                   "updated_at) VALUES (?,?,?,?,?,?,?,?)", ("f" * 32, "x" * 32, "e" * 32, "custody", "accepted", 0,
                                                            now, now))
        db.execute("UPDATE ade_agent_tasks SET continued_from = ?, custody_cross = ? WHERE id = ?",
                   (json.dumps({"task": "e" * 32, "offer": "f" * 32, "packet_digest": "0" * 64, "from_user": "x"}),
                    custody_cross, task_id))
        db.execute("UPDATE ade_account_owners SET in_relays = 0 WHERE account = ?", (ACC["A"],))
    own(user, ACC["B"])
    refuse_all(user, ACC["B"])
    own(user, ACC["C"], plan="Claude Team", org="org-t", org_name="Tee")
    room(user, ACC["C"])
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    why = _regroupings(task_id)[0][-1]["why"]
    if custody_cross:
        assert why == (REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
                       + LEFT_OUT.format(why="taken out of relays") + MOVES_ON + WAITS)
    else:
        # an unticked custody task gets the binding a press would too, its relay set still the group
        # it was accepted in
        assert why == (REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
                       + LEFT_OUT.format(why=RECEIVED_OUT) + GOES_HOME + WAITS)
        assert row(task_id)["bound_org"] == "org-t"
    finish(user, first, 1, 1)
    if custody_cross:
        assert acct(row(task_id)["lease_run"]) == ACC["C"]
    else:
        assert (row(task_id)["state"], row(task_id)["cause"]) == ("suspended", "window")
        assert [acct(r["run_id"]) for r in runs_of(task_id)] == [ACC["A"]]


@pytest.mark.parametrize("off", ["deployment", "task"])
def test_with_the_relay_off_a_regrouping_onto_an_account_its_relay_set_holds_says_its_relay_takes_it(
        client, owner, ws, monkeypatch, off):
    # where every relay-off case puts the account out of the set, a line that reads the relay's switch before the
    # account's entry passes. With the relay off and the account in the set (a Team seat
    # confirmed as an Enterprise seat of its organisation, whose crossings end there), the line says the relay takes it
    if off == "deployment":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "off")
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task = make_task(client, h, make_agent(client, h, ACC["A"]), between_steps="wait")
    assert press(client, h, task["id"], "start").status_code == 200
    if off == "task":
        assert press(client, h, task["id"], "switches", relay=False).status_code == 200
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert _regroupings(task["id"])[0][-1]["why"] == (
        REGROUPED.format(on="a seat of an Enterprise organisation (the same organisation's, another kind of seat)")
        + BOUND_CHANGED + RELAY_TAKES)


@pytest.mark.parametrize("case", ["named", "track off", "track unreadable", "custody's read refused", "no address",
                                  "not confirmed", "changed since", "received", "no account custody reads", "held",
                                  "changed"])
def test_a_regrouping_names_the_custodian_only_where_custody_could_take_the_task(
        client, owner, ws, monkeypatch, case):
    # the line once named the custodian for every task not received from a teammate,
    # while custody takes a task only with the teammate track on, its repository's address recorded and the task
    # confirmed for custody as it stands (task_offers.custody_candidate), and reads only the accounts of its relay set.
    # task_offers' side is faked here (ws): the track's switch is faked on but in "track off", the confirmation written
    # as its custody-confirm writes it. Each case but "named" and "changed" fails one condition, and the line names no
    # custodian. Custody is asked only after a refusal in the "home" case, and the line says so; a track that cannot be
    # asked names no custodian, as _custody hands to none then, and leaves the rest of
    # the line as read (it once said the relay could not be read), whatever it raises while the hold's transaction
    # stands, a SQLite error among them; and custody's refusal reads the accounts of the relay set as
    # task_offers._members reads them, so a held one at home is not read and a changed one is
    if case == "track unreadable":
        def unreadable(user):
            raise RuntimeError("the teammate's track cannot be asked")
        monkeypatch.setattr(task_offers, "enabled", unreadable)
    elif case != "track off":
        monkeypatch.setattr(task_offers, "enabled", lambda user: None)
    if case == "custody's read refused":
        def refused(db, owner_id, task, now):
            db.execute("SELECT no_such_column FROM ade_agent_tasks")       # refused; the transaction stands
        monkeypatch.setattr(task_offers, "_members", refused)
    if case != "no address":
        ws.repos[0].update(address=ADDRESS, slug="acme/demo")
    h, user = owner
    task_id, _ = started(client, h, user, between_steps="wait")                 # on A, a personal plan
    if case != "no account custody reads":
        own(user, ACC["B"], state=case if case in ("held", "changed") else "confirmed")
    with store.connect() as db:
        if case != "not confirmed":
            db.execute("UPDATE ade_agent_tasks SET custody_digest = ? WHERE id = ?",
                       (tasks.custody_digest(db, user["id"], task_id), task_id))
        if case == "changed since":             # an answer kept since, not confirmed for custody
            db.execute("INSERT INTO ade_agent_task_decisions (task_id, n, user_id, text, author, at) VALUES "
                       "(?,1,?,'Use the staging one.','owner',?)", (task_id, user["id"], int(time.time())))
        if case == "received":                  # as a pressed offer's acceptance makes it, no custody offer's row
            db.execute("UPDATE ade_agent_tasks SET continued_from = ?, custody_cross = 0 WHERE id = ?",
                       (json.dumps({"task": "e" * 32, "offer": "f" * 32, "packet_digest": "0" * 64, "from_user": "x"}),
                        task_id))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    on = REGROUPED.format(on="a seat of a Team organisation")
    named = BOUND_CHANGED + LEFT_OUT.format(why=CROSSING_OFF) + GOES_HOME + HOME_BUT_CUSTODIAN + WAITS
    # a received task names no custodian; it is bound as any task is, its relay
    # set still the group it was accepted in
    expected = {"named": named, "changed": named,
                "received": BOUND_CHANGED + LEFT_OUT.format(why=RECEIVED_OUT) + GOES_HOME + WAITS}
    assert _regroupings(task_id)[0][-1]["why"] == on + expected.get(
        case, BOUND_CHANGED + LEFT_OUT.format(why=CROSSING_OFF) + GOES_HOME + WAITS)


def test_a_regrouping_names_the_custodian_where_custody_reads_only_the_account_the_task_is_on(
        client, owner, ws, monkeypatch):
    # the account the task is on still in its relay set, out of it for another reason (taken out of relays), and no
    # other account of the owner's in it: custody's refusal reads that account, so the custodian may still take the
    # task. The line once said the app moves it onto none of his other accounts, though an Enterprise seat of that
    # organisation he confirms later is one the relay moves it onto, for the relay set holds
    # the organisation's group, the one the task is on. The line now names the relay's order, and an attempt refused
    # on A goes onto that seat
    monkeypatch.setattr(task_offers, "enabled", lambda user: None)
    ws.repos[0].update(address=ADDRESS, slug="acme/demo")
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    own(user, ACC["B"])                         # a personal plan, never reached from an Enterprise seat
    task = make_task(client, h, make_agent(client, h, ACC["A"]), between_steps="wait")
    assert press(client, h, task["id"], "start").status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET in_relays = 0 WHERE account = ?", (ACC["A"],))
        db.execute("UPDATE ade_agent_tasks SET custody_digest = ? WHERE id = ?",
                   (tasks.custody_digest(db, user["id"], task["id"]), task["id"]))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert _regroupings(task["id"])[0][-1]["why"] == (
        REGROUPED.format(on="a seat of an Enterprise organisation (the same organisation's, another kind of seat)")
        + BOUND_CHANGED + LEFT_OUT.format(why="taken out of relays") + MOVES_ON + CUSTODIAN + WAITS)
    own(user, ACC["E"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    room(user, ACC["E"])
    refuse(user, row(task["id"])["lease_run"], resets=int(time.time()) + HOUR)
    assert acct(row(task["id"])["lease_run"]) == ACC["E"]


@pytest.mark.parametrize("how", ["raises", "sqlite, its transaction standing", "no entry"])
def test_a_regrouping_whose_relay_cannot_be_read_says_so_and_the_confirmation_stands(
        client, owner, ws, monkeypatch, how):
    # the relay read runs inside the hold's end, so a raise there once cost
    # the owner his confirmation (holds._broke), and an account with no entry in the set read as one the relay takes.
    # Neither now: the line says the relay could not be read, and the binding and the account's new group stand. A
    # SQLite error that leaves the hold's transaction standing is caught as any other (one
    # that gives it up is the next test's), so a catch narrowed to RuntimeError fails here
    h, user = owner
    task_id, _ = started(client, h, user, between_steps="wait")                 # on A, a personal plan
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    real = tasks._relay_entries

    def unreadable(db, task, now, unattended=True):
        if how == "raises":
            raise RuntimeError("a row the relay cannot read")
        if how.startswith("sqlite"):
            db.execute("SELECT no_such_column FROM ade_agent_tasks")       # refused; the transaction stands
        return [e for e in real(db, task, now, unattended) if e["account"] != ACC["A"]]
    monkeypatch.setattr(tasks, "_relay_entries", unreadable)
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    monkeypatch.setattr(tasks, "_relay_entries", real)
    with store.connect() as db:
        moved = tasks._owner(db, user["id"], ACC["A"])
    assert (moved["state"], moved["grp"]) == ("confirmed", "org:org-t")
    assert (row(task_id)["bound_org"], row(task_id)["crossings_ended"]) == ("org-t", 0)
    assert _regroupings(task_id)[0][-1]["why"] == (REGROUPED.format(on="a seat of a Team organisation") + BOUND_CHANGED
                                                   + UNREAD)


@pytest.mark.parametrize("where", ["relay set", "custodian"])
def test_a_regrouping_whose_transaction_sqlite_gave_up_keeps_nothing_and_the_confirmation_fails(
        client, owner, ws, monkeypatch, where):
    # the relay read's catch once took sqlite3.Error too. Where SQLite rolls the
    # hold's transaction back whole (SQLITE_FULL, IOERR, BUSY, NOMEM), the line and the entry written next were kept
    # outside it, in a transaction of their own that the hold's end then committed with the hold marked applied, while
    # the account's confirmation and the binding were gone and the account was left held. Now the error is raised
    # there: the hold's end fails (holds._broke) and keeps nothing, as any raise in it does. The custodian's read has a
    # catch of its own, and gives such an error up the same way
    if where == "custodian":                    # the custodian's read reached: the track on, an address, confirmed
        monkeypatch.setattr(task_offers, "enabled", lambda user: None)
        ws.repos[0].update(address=ADDRESS, slug="acme/demo")
    h, user = owner
    task_id, _ = started(client, h, user, between_steps="wait")                 # on A, a personal plan
    if where == "custodian":
        with store.connect() as db:
            db.execute("UPDATE ade_agent_tasks SET custody_digest = ? WHERE id = ?",
                       (tasks.custody_digest(db, user["id"], task_id), task_id))
    version = row(task_id)["state_version"]
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    module, name = (tasks, "_relay_entries") if where == "relay set" else (task_offers, "_members")
    real = getattr(module, name)

    def full(db, *args, **kwargs):
        db.execute("ROLLBACK")                  # as SQLite does on a full disk, before its error reaches Python
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(module, name, full)
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    monkeypatch.setattr(module, name, real)
    t = row(task_id)
    assert (t["bound_org"], t["crossings_ended"], t["state_version"]) == (None, 0, version)
    assert _regroupings(task_id) == ([], [])
    with store.connect() as db:
        moved = tasks._owner(db, user["id"], ACC["A"])
        hold = db.execute("SELECT failed FROM ade_holds WHERE id = ?", (r.json()["hold"]["id"],)).fetchone()
    assert (moved["state"], moved["grp"], hold["failed"]) == ("changed", "personal", holds.BROKE)


def test_a_regrouping_s_line_and_entry_carry_the_task_s_step_the_confirmation_s_time_and_no_run(
        client, owner, ws):
    # the entry's step is the task's (here 2, not the 1 of every
    # earlier case); both writes are at the confirmation's own time, which here is far from the wall clock; and the
    # line, which belongs to no attempt, carries no run and no fence while the task has an attempt in flight
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="wait")
    finish(user, first, 1, 1)
    assert press(client, h, task_id, "continue").status_code == 200
    t = row(task_id)
    assert t["step"] == 2 and t["lease_run"] is not None and t["fence"] > 0
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    at = r.json()["hold"]["takes_effect_at"] + 5 * 24 * HOUR
    holds.settle(user["id"], at)
    line = events(task_id, "task.account_regrouped")[-1]
    assert (line["at"], line["run_id"], line["fence"]) == (at, None, None)
    with store.connect() as db:
        entry = db.execute("SELECT step, at FROM ade_task_account_record WHERE task_id = ? AND kind = 'regrouped'",
                           (task_id,)).fetchone()
    assert (entry["step"], entry["at"]) == (2, at)


@pytest.mark.parametrize("raises_in", ["journal", "account_record"])
def test_a_regrouping_s_binding_line_and_entry_are_written_together_or_not_at_all(
        client, owner, ws, monkeypatch, raises_in):
    # the line, the entry and the binding are written in the hold's end's transaction, so a
    # raise in the loop, at the second task's line or entry, keeps none of them for either task, and the account goes
    # back as it was before the confirmation (holds._broke)
    h, user = owner
    first, _ = started(client, h, user, between_steps="wait")                   # on A, a personal plan
    second = make_task(client, h, make_agent(client, h, ACC["A"]))["id"]
    versions = {tid: row(tid)["state_version"] for tid in (first, second)}
    real = getattr(tasks, raises_in)
    calls = []

    def breaking(db, user_id, task_id, kind, *args, **kwargs):
        if kind in ("task.account_regrouped", "regrouped"):
            calls.append(task_id)
            if len(calls) == 2:
                raise RuntimeError("the store would not take it")
        return real(db, user_id, task_id, kind, *args, **kwargs)
    monkeypatch.setattr(tasks, raises_in, breaking)
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    assert sorted(calls) == sorted([first, second])
    for tid in (first, second):
        t = row(tid)
        assert (t["bound_org"], t["crossings_ended"], t["state_version"]) == (None, 0, versions[tid]), tid
        assert _regroupings(tid) == ([], []), tid
    with store.connect() as db:
        moved = tasks._owner(db, user["id"], ACC["A"])
    assert (moved["state"], moved["grp"]) == ("changed", "personal")


def test_a_received_task_on_one_enterprise_organisation_s_seat_is_never_moved_onto_another_s(
        client, owner, ws, clock):
    # a received task's account confirmed again as an Enterprise seat of another organisation puts it on that one's
    # seat, and a rule reading kinds alone (an Enterprise seat onto an Enterprise seat is no move) would carry it by
    # itself onto an Enterprise seat of the organisation it was accepted in, out of one Enterprise organisation's seats
    # into another's. The confirmation binds it as any task, its crossings ending, so its relay set is the group it is
    # on, org-y's seats, which E is not in; the test below pins the relay's own rule on a task left out of the binding,
    # an offered one
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, _ = started(client, h, user, between_steps="wait")
    with store.connect() as db:                 # as task_offers' acceptance makes it, custody's crossing not ticked
        db.execute("UPDATE ade_agent_tasks SET continued_from = ?, custody_cross = 0 WHERE id = ?",
                   (json.dumps({"task": "e" * 32, "offer": "f" * 32, "packet_digest": "0" * 64, "from_user": "x"}),
                    task_id))
    assert row(task_id)["home_grp"] == "org:org-z"
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-y", org_name="Why")
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (1, "org-z")
    own(user, ACC["E"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    _allowed_now(user, clock, ACC["E"])
    refuse(user, row(task_id)["lease_run"], resets=clock[0] + HOUR)
    assert all(acct(r["run_id"]) == ACC["A"] for r in runs_of(task_id))
    assert not events(task_id, "task.crossed") and row(task_id)["state"] in ("suspended", "needs_owner")
    with store.connect() as db:
        entries = {e["account"]: e for e in tasks.relay_set(db, user["id"], task_id, clock[0])}
    assert entries[ACC["E"]]["left_out"] == "an Enterprise organisation's seats"


def test_an_offered_task_on_one_enterprise_organisation_s_seat_is_never_moved_onto_another_s(
        client, owner, ws, clock):
    # the test above's case for a task left out of the confirmation's binding, which keeps its crossings (the offered
    # task alone is left out): offered when its account, org-z's Team seat, was confirmed
    # again as an Enterprise seat of org-y, it is still homed on org-z's seats once the offer ends, so only the relay's
    # own rule keeps it off E, org-z's Enterprise seat; a rule reading kinds alone (an Enterprise seat onto an
    # Enterprise seat is no move) would carry it there, out of one Enterprise organisation's seats into another's
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, steps=3)
    record(user, first, "stopped")
    assert row(task_id)["home_grp"] == "org:org-z"
    with store.connect() as db:                 # an offer standing, as task_offers' hand-off leaves it
        db.execute("UPDATE ade_agent_tasks SET state = 'offering', lease_run = NULL WHERE id = ?", (task_id,))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-y", org_name="Why")
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (0, "org-z")
    with store.connect() as db:                 # the offer ended, the task back as it waited
        db.execute("UPDATE ade_agent_tasks SET state = 'suspended', cause = 'stopped' WHERE id = ?", (task_id,))
    assert press(client, h, task_id, "continue").status_code == 200
    own(user, ACC["E"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    _allowed_now(user, clock, ACC["E"])
    refuse(user, row(task_id)["lease_run"], resets=clock[0] + HOUR)
    assert all(acct(r["run_id"]) == ACC["A"] for r in runs_of(task_id))
    assert not events(task_id, "task.crossed") and row(task_id)["state"] in ("suspended", "needs_owner")
    with store.connect() as db:
        entries = {e["account"]: e for e in tasks.relay_set(db, user["id"], task_id, clock[0])}
    assert entries[ACC["E"]]["left_out"] == "an Enterprise organisation's seats"


def test_a_task_left_out_of_a_confirmation_s_binding_is_still_never_moved_off_an_enterprise_seat(
        client, owner, ws, monkeypatch, clock):
    # a task offered when its account's confirmation applied keeps its
    # binding (its crossings on, bound to none), so only the relay's own rule keeps it on the Enterprise seat it is on
    # now; a rule for the home group's tier alone, with the crossing tiers matching an Enterprise seat by kind, would
    # carry it into a Team organisation's seats and bound it there
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    record(user, first, "stopped")
    with store.connect() as db:                 # an offer standing, as task_offers' hand-off leaves it
        db.execute("UPDATE ade_agent_tasks SET state = 'offering', lease_run = NULL WHERE id = ?", (task_id,))
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (0, None)
    with store.connect() as db:                 # the offer ended, the task back as it waited
        db.execute("UPDATE ade_agent_tasks SET state = 'suspended', cause = 'stopped' WHERE id = ?", (task_id,))
    assert press(client, h, task_id, "continue").status_code == 200
    own(user, ACC["B"])
    own(user, ACC["C"], plan="Claude Team", org="org-t", org_name="Tee")
    _allowed_now(user, clock, ACC["B"], ACC["C"])
    refuse(user, row(task_id)["lease_run"], resets=clock[0] + HOUR)
    assert all(acct(r["run_id"]) == ACC["A"] for r in runs_of(task_id))
    assert not events(task_id, "task.crossed") and row(task_id)["bound_org"] is None


def test_a_relay_set_of_nine_keeps_the_bound_of_ten_moves_an_hour(client, owner, ws, clock):
    h, user = owner
    nine = ["default"] + [ACC[k] for k in "ABCDEFGH"]
    for account in nine:
        own(user, account)
    task_id = on_task(client, h, "default")
    reset = clock[0] + 600
    for _ in range(8):
        refuse(user, row(task_id)["lease_run"], resets=reset)
    refuse(user, row(task_id)["lease_run"], resets=reset)              # every account out: the task waits
    assert row(task_id)["state"] == "suspended" and len(row(task_id)["moves"]) == 8
    clock[0] = reset + 1
    tasks.sweep(now=reset + 1)                          # the account it is on resumes: no move
    for _ in range(2):
        refuse(user, row(task_id)["lease_run"], resets=reset + 1200)
    assert len(row(task_id)["moves"]) == 10
    refuse(user, row(task_id)["lease_run"], resets=reset + 1200)
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "moves"
    assert held["reason"].startswith("The task moved 10 times in an hour")
    assert press(client, h, task_id, "continue").status_code == 200 and row(task_id)["moves"] == []


# ---- an attempt ended ungated ----------------------------------------------------------------------------------------

def test_an_ungated_end_needs_the_owner_its_unseen_call_an_unknown_effect_resolved_before_continue(client, owner,
                                                                                                  ws):
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="ungated", effects=[
        {"call_id": "toolu_x", "tool": "Bash", "kind": "unclassified", "target": "rm -rf build", "intent": {},
         "settled": "unknown", "unseen": True, "end": "ok"}])
    held = task_of(client, h, task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "ungated"
    effect = held["effects"][0]
    assert effect["unseen"] is True and effect["state"] == "unknown" and effect["target"] == "rm -rf build"
    refused = press(client, h, task_id, "continue")
    assert refused.status_code == 412 and len(runs_of(task_id)) == 1
    assert press(client, h, task_id, "resolve", what="effect", effect_id=effect["id"],
                 outcome="not_happened").status_code == 200
    assert press(client, h, task_id, "continue").status_code == 200 and len(runs_of(task_id)) == 2


def test_an_attempt_ended_by_stop_leaves_the_task_suspended_never_ungated(client, owner, ws):
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "working")
    ws.stop_cause = "ungated"           # the control channel closed by Stop, which is the workspace's own close
    assert press(client, h, task_id, "stop").status_code == 200
    assert [c[1] for c in ws.calls if c[1].endswith("/stop")] == [f"/agents/runs/{first}/stop"]
    after = task_of(client, h, task_id)
    assert after["state"] == "suspended" and after["cause"] == "stopped"


# ---- the account record ---------------------------------------------------------------------------------------------

ENTRY_KEYS = {"seq", "kind", "run_id", "step", "fence", "via", "repo_address", "branch", "title", "at", "accounts",
              "reduced_at", "statement"}
DAY = 86400


def record_of(user, task_id):
    with store.connect() as db:
        found = tasks.account_records(db, user["id"], task_id=task_id)
    return found[0] if found else None


def crossed_and_deleted(client, h, user, monkeypatch):
    """A task with an attempt on A, a crossing onto a Team seat S and an attempt there, ended and deleted."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    own(user, ACC["A"])
    own(user, ACC["C"], plan="Claude Team", org="org-x", org_name="X")
    room(user, ACC["C"])
    task_id, first = started(client, h, user)
    record(user, first, "working")
    refuse(user, first, resets=int(time.time()) + HOUR)
    second = row(task_id)["lease_run"]
    record(user, second, "working")
    record(user, second, "failed", reason="its own error")
    before = record_of(user, task_id)
    press(client, h, task_id, "end", outcome="stopped")
    r = client.request("DELETE", f"{T}/{task_id}", headers=h,
                       json={"state_version": task_of(client, h, task_id)["state_version"]})
    assert r.status_code == 200, r.text
    return task_id, first, second, before


def test_one_entry_per_attempt_and_crossing_holding_accounts_by_reference_and_nothing_of_the_work(
        client, owner, ws, monkeypatch):
    h, user = owner
    task_id, first, second, before = crossed_and_deleted(client, h, user, monkeypatch)
    entries = before["entries"]
    assert [(e["kind"], e["run_id"], e["step"]) for e in entries] == [
        ("attempt", first, 1), ("crossing", second, 1), ("attempt", second, 1)]
    for e in entries:
        assert set(e) == ENTRY_KEYS and e["branch"] == "pp/task-" + task_id and e["title"] == "Count lines"
        assert isinstance(e["at"], int) and e["via"] is None
    a, s = (dict(x) for x in entries[1]["accounts"])
    assert a == {"ref": {"kind": "account", "id": ACC["A"]}, "email": ACC["A"] + "@example.com", "plan": "Claude Max",
                 "organization": None, "org_id": None}
    assert s["org_id"] == "org-x" and s["organization"] == "X" and s["plan"] == "Claude Team"
    after = record_of(user, task_id)
    assert after["entries"][:3] == entries                       # the deletion changed no entry
    deleted = after["entries"][3]
    assert deleted["kind"] == "deleted" and deleted["accounts"] == [] and deleted["run_id"] is None
    assert after["deleted_at"] == deleted["at"] and after["record_days"] == 90
    text = json.dumps(after)
    for work in ("Append line", "Make the file two lines long.", "packet", "note", "wc -l"):   # the goal as made
        assert work not in text


@pytest.mark.parametrize("setting,kept_at,gone_at", [(None, None, 90), ("forever", 400, None), ("400", 399, 400)])
def test_the_record_is_kept_as_the_setting_says_counted_from_its_deleted_entry(client, owner, ws, monkeypatch,
                                                                               setting, kept_at, gone_at):
    h, user = owner
    task_id, *_ = crossed_and_deleted(client, h, user, monkeypatch)
    if setting is not None:
        monkeypatch.setenv(tasks.RECORD_DAYS_SETTING, setting)
    deleted_at = record_of(user, task_id)["deleted_at"]
    with store.connect() as db:
        # the task's attempts a month before its deletion: the keeping is counted from the deleted entry alone
        db.execute("UPDATE ade_task_account_record SET at = at - ? WHERE task_id = ? AND kind != 'deleted'",
                   (30 * DAY, task_id))
        db.execute("DELETE FROM ade_account_owners")        # an account removed changes no entry
        tasks.forget(db, deleted_at + 89 * DAY)
    assert record_of(user, task_id)["title"] == "Count lines"
    if kept_at is not None:
        with store.connect() as db:
            tasks.forget(db, deleted_at + kept_at * DAY)
        kept = record_of(user, task_id)
        assert kept is not None and kept["title"] is None and kept["task_id"] == task_id
        assert kept["branch"] == "pp/task-" + task_id and all(e["title"] is None for e in kept["entries"])
        assert row_gone(task_id)
    if gone_at is not None:
        with store.connect() as db:
            tasks.forget(db, deleted_at + gone_at * DAY + 1)
        assert record_of(user, task_id) is None


def row_gone(task_id):
    with store.connect() as db:
        return db.execute("SELECT 1 FROM ade_agent_tasks WHERE id = ?", (task_id,)).fetchone() is None


def test_the_owner_s_delete_takes_a_confirmation_and_only_once_the_task_is_deleted(client, owner, ws,
                                                                                 monkeypatch):
    h, user = owner
    live_id, _ = started(client, h, user)
    assert client.request("DELETE", f"{T}/{live_id}/account-record", headers=h, json={}).status_code == 412
    assert client.request("DELETE", f"{T}/{'0' * 32}/account-record", headers=h, json={}).status_code == 404
    task_id, *_ = crossed_and_deleted(client, h, user, monkeypatch)
    refused = client.request("DELETE", f"{T}/{task_id}/account-record", headers=h, json={})
    assert refused.status_code == 403 and record_of(user, task_id) is not None
    r = client.request("DELETE", f"{T}/{task_id}/account-record", headers=h,
                       json={"confirmation": confirmation(user, f"task-account-record:{task_id}")})
    assert r.status_code == 200 and r.json() == {"deleted": 4} and record_of(user, task_id) is None


def test_a_teammate_s_entry_holds_their_user_and_plan_never_a_personal_plan_s_organisation(client, owner, ws):
    h, user = owner
    task_id, _ = started(client, h, user)
    teammate = "1" * 32
    with store.connect() as db:
        # a personal plan's organisation is the teammate's own: refused whichever member holds it
        for own_org in ({"organization": "Their own", "org_id": "o-1"}, {"organization": "Their own", "org_id": None},
                        {"organization": None, "org_id": "o-1"}):
            with pytest.raises(ValueError):
                tasks.account_record(db, user["id"], task_id, "handoff", [
                    dict({"teammate": teammate, "plan": "Claude Pro"}, **own_org)], step=1, via="offer")
        for bad in ({"teammate": teammate, "plan": "Claude Team", "organization": "X", "org_id": "o", "email": "t@x"},
                    {"ref": {"kind": "account", "id": ACC["A"]}, "email": None, "plan": None, "organization": None},
                    {"ref": {"kind": "key", "provider": "anthropic"}, "vendor": "Anthropic", "label": "k",
                     "key": "x"}):
            with pytest.raises(ValueError):
                tasks.account_record(db, user["id"], task_id, "handoff" if "teammate" in bad else "attempt", [bad],
                                     step=1, via="offer" if "teammate" in bad else None)
        tasks.account_record(db, user["id"], task_id, "handoff", [
            {"teammate": teammate, "plan": "Claude Pro", "organization": None, "org_id": None}], step=1, via="offer")
    entry = record_of(user, task_id)["entries"][-1]
    assert entry["kind"] == "handoff" and entry["via"] == "offer" and entry["accounts"] == [
        {"teammate": teammate, "plan": "Claude Pro", "organization": None, "org_id": None}]


def test_a_regrouping_s_entry_holds_one_of_the_owner_s_accounts_as_confirmed_before_and_now_and_no_run(
        client, owner, ws):
    # the entry tasks._account_moved writes; any other shape is refused, so
    # neither a key, a teammate, another account nor a run rides on it
    h, user = owner
    task_id, _ = started(client, h, user)
    was = _snap(ACC["A"], "a@example.com", "Claude Max")
    now_ = _snap(ACC["A"], "a@example.com", "Claude Team", "Tee", "org-t")
    key = {"ref": {"kind": "key", "provider": "anthropic"}, "vendor": "Anthropic", "label": "Anthropic API key"}
    mate = {"teammate": "1" * 32, "plan": "Claude Team", "organization": "X", "org_id": "o"}
    with store.connect() as db:
        for accounts, extra in (([now_], {}), ([was, now_, now_], {}), ([was, _snap(ACC["B"], "b@x", "Claude Max")], {}),
                                ([was, key], {}), ([key, key], {}), ([was, mate], {}),
                                ([was, now_], {"run_id": "b" * 12}), ([was, now_], {"fence": 1}),
                                ([was, now_], {"via": "offer"})):
            with pytest.raises(ValueError):
                tasks.account_record(db, user["id"], task_id, "regrouped", accounts, step=1, **extra)
        tasks.account_record(db, user["id"], task_id, "regrouped", [was, now_], step=1)
    entry = record_of(user, task_id)["entries"][-1]
    assert (entry["kind"], entry["accounts"], entry["run_id"], entry["fence"], entry["via"]) == \
        ("regrouped", [was, now_], None, None, None)


def test_the_prune_reduces_a_teammate_to_a_teammate_and_the_operator_lists_states_and_reduces(
        client, owner, ws, monkeypatch, capsys):
    from proofpane_open import __main__ as command
    h, user = owner
    monkeypatch.setenv("OPEN_EDITION_ADMINS", "admin@example.com,bob@example.com")
    signed_up(client, "bob@example.com")
    bob = auth.user(client.get("/api/auth/me", headers=signed_in(client, "bob@example.com")).json()["id"])
    # task_offers' rows: one offer whose owner typed the address of someone with no account here
    typed = {"carol@example.com": [{"table": "offer", "id": "9" * 32, "owner": user["id"]}]}
    monkeypatch.setattr(task_offers, "person_rows", lambda db, person: typed.get(person, []))
    task_id, _ = started(client, h, user)
    seat = {"teammate": bob["id"], "plan": "Claude Team", "organization": "X", "org_id": "org-x"}
    with store.connect() as db:
        seq = tasks.account_record(db, user["id"], task_id, "handoff", [seat], step=1, via="custody")
    assert command.main(["account-record", "list", "bob@example.com"]) == 0
    listed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(e["seq"], e["owner"], e["snapshot"]) for e in listed] == [(seq, user["id"], seat)]
    assert command.main(["account-record", "statement", str(seq), "I asked for this to be reduced."]) == 0
    assert json.loads(capsys.readouterr().out)["statement"]["text"] == "I asked for this to be reduced."

    def failing(db, person, now):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(task_offers, "reduce_person", failing)
    assert command.main(["account-record", "reduce", bob["id"]]) == 1
    assert "Nothing was changed" in capsys.readouterr().err
    kept = record_of(user, task_id)["entries"][-1]
    assert kept["accounts"] == [seat] and kept["reduced_at"] is None        # all of it or none
    monkeypatch.setattr(task_offers, "reduce_person", lambda db, person, now: {"offers": 0, "custody": 0})
    assert command.main(["account-record", "reduce", bob["id"]]) == 0
    assert json.loads(capsys.readouterr().out) == {"entries": 1, "offers": 0, "custody": 0}
    reduced = record_of(user, task_id)["entries"][-1]
    assert reduced["accounts"] == [dict(seat, teammate=None)] and reduced["title"] == "Count lines"
    assert reduced["reduced_at"] is not None
    assert reduced["statement"]["text"] == "I asked for this to be reduced."      # kept beside its entry
    # a person with no account here is named by no entry, but an owner's typed address may still name them in
    # task_offers' rows
    assert command.main(["account-record", "list", "carol@example.com"]) == 0
    out = capsys.readouterr()
    assert [json.loads(line) for line in out.out.splitlines()] == typed["carol@example.com"]
    assert "No account has that address or id" in out.err
    assert command.main(["account-record", "statement", "999999", "x"]) == 1
    # a person nothing names, no account and no row of task_offers': 1, with one line, and nothing reduced
    reduced_rows = []
    monkeypatch.setattr(task_offers, "reduce_person", lambda db, person, now: reduced_rows.append(person) or {})
    capsys.readouterr()
    for form in ("list", "reduce"):
        assert command.main(["account-record", form, "dave@example.com"]) == 1
        out = capsys.readouterr()
        assert out.out == "" and len(out.err.strip().splitlines()) == 1 and "No such person" in out.err
    assert reduced_rows == []


def test_a_user_id_no_account_has_is_a_person_only_where_a_row_names_it(client, owner, ws, monkeypatch, capsys):
    """An id is a person only where an account has it, or an entry or one of task_offers' rows names it. One that
    nothing names gets one line on standard error, and the command exits 1, reducing nothing; one an entry names, the id
    of a teammate whose account was removed since, is listed and reduced, with the line that no account has it."""
    from proofpane_open import __main__ as command
    h, user = owner
    monkeypatch.setattr(task_offers, "person_rows", lambda db, person: [])
    reduced_rows = []
    monkeypatch.setattr(task_offers, "reduce_person", lambda db, person, now: reduced_rows.append(person) or {})
    task_id, _ = started(client, h, user)
    gone, nobody = "7" * 32, "8" * 32
    for form in ("list", "reduce"):
        assert command.main(["account-record", form, nobody]) == 1
        out = capsys.readouterr()
        assert out.out == "" and out.err.strip().splitlines() == [
            "No such person: no account has that address or id, and no entry, offer or custody row names them."]
    assert reduced_rows == []
    seat = {"teammate": gone, "plan": "Claude Team", "organization": "X", "org_id": "org-x"}
    with store.connect() as db:
        seq = tasks.account_record(db, user["id"], task_id, "handoff", [seat], step=1, via="custody")
    assert command.main(["account-record", "list", gone]) == 0
    out = capsys.readouterr()
    assert [json.loads(line)["seq"] for line in out.out.splitlines()] == [seq]
    assert out.err.startswith("No account has that address or id")
    assert command.main(["account-record", "reduce", gone]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == 1 and reduced_rows == [gone]


def signed_in(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_the_journal_s_prune_drops_titles_and_reduces_teammates_whatever_the_setting(client, owner, ws,
                                                                                     monkeypatch):
    monkeypatch.setenv(tasks.RECORD_DAYS_SETTING, "forever")
    h, user = owner
    task_id, first = started(client, h, user)
    seat = {"teammate": "2" * 32, "plan": "Claude Team", "organization": "X", "org_id": "org-x"}
    with store.connect() as db:
        seq = tasks.account_record(db, user["id"], task_id, "handoff", [seat], step=1, via="offer")
        tasks.attach_statement(db, seq, "Their statement.", int(time.time()))
    record(user, first, "stopped")
    press(client, h, task_id, "end", outcome="stopped")
    client.request("DELETE", f"{T}/{task_id}", headers=h,
                   json={"state_version": task_of(client, h, task_id)["state_version"]})
    deleted_at = record_of(user, task_id)["deleted_at"]
    with store.connect() as db:
        tasks.forget(db, deleted_at + 90 * DAY)
    kept = record_of(user, task_id)
    handoff = next(e for e in kept["entries"] if e["kind"] == "handoff")
    assert handoff["accounts"] == [dict(seat, teammate=None)] and handoff["title"] is None
    assert handoff["statement"]["text"] == "Their statement."                     # kept by the prune


def test_no_path_but_the_delete_the_prune_and_the_operator_changes_or_removes_an_entry():
    """Every statement that changes or removes the account record's entries is in one of the allowed functions, and
    only account_record writes one."""
    import ast
    import pathlib
    allowed = {"UPDATE": {"_reduce", "attach_statement"}, "DELETE": {"delete_account_record", "forget"},
               "INSERT": {"account_record"}}
    found = {"UPDATE": set(), "DELETE": set(), "INSERT": set()}
    root = pathlib.Path(tasks.__file__).resolve().parent.parent
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                        "ade_task_account_record" in node.value:
                    for verb in found:
                        if re.search(rf"\b{verb}\b", node.value) and verb in node.value.split(
                                "ade_task_account_record")[0]:
                            found[verb].add(fn.name)
    assert found == allowed, found


# ---- a task's repository: its address and GitHub name, recorded when it is made ------------------------------------

ADDRESS = "https://github.com/acme/demo.git"


def sent_task(user, ws):
    """The attempt message the workspace was last sent, once the queue is delivered."""
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send_now(user, "http://workspace.test")
    return ws.sent[-1]["task"]


def test_a_task_records_its_repository_s_address_and_name_and_its_attempts_carry_them(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    ws.repos[0].update(address=ADDRESS, slug="acme/demo")
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    assert (row(task["id"])["repo_slug"], row(task["id"])["repo_address"]) == ("acme/demo", ADDRESS)   # when made
    assert [c for c in ws.calls if c[1] == "/repos"] == [("GET", "/repos", None)]
    assert press(client, h, task["id"], "start").status_code == 200
    message = sent_task(user, ws)
    assert (message["repo_slug"], message["repo_address"]) == ("acme/demo", ADDRESS)


@pytest.mark.parametrize("address", [
    "https://x-access-token:secret@github.com/acme/demo.git",      # user information: a token can hide there
    "https://acme@github.com/acme/demo.git",
    "https://github.com/acme/demo.git?token=secret",               # a query
    "https://github.com/acme/demo.git#main",                       # a fragment
    "http://github.com/acme/demo.git",                             # not https
    "git@github.com:acme/demo.git",                                # not a URL both workspaces can reach
    "https://github.com/acme/de mo.git",                           # a space
    "https://github.com/acme/demo.git\n",                          # a control character
    "https:///acme/demo.git",                                      # no host
    "https://github.com/" + "a" * 490,                             # past 500 characters
    7,
])
def test_a_repository_address_that_could_carry_a_secret_or_is_not_one_is_not_recorded(client, owner, ws, address):
    h, user = owner
    ws.repos[0].update(address=address, slug="acme/demo")
    task_id, _ = started(client, h, user)
    assert (row(task_id)["repo_slug"], row(task_id)["repo_address"]) == ("acme/demo", None)


@pytest.mark.parametrize("slug", ["acme", "-acme/demo", "acme/demo/more", "acme/de mo", "", None, ["acme/demo"]])
def test_a_slug_that_is_not_owner_and_name_is_not_recorded(client, owner, ws, slug):
    h, user = owner
    ws.repos[0].update(address=ADDRESS, slug=slug)
    task_id, _ = started(client, h, user)
    assert (row(task_id)["repo_slug"], row(task_id)["repo_address"]) == (None, ADDRESS)


@pytest.mark.parametrize("listed", [
    [{"name": "other", "address": ADDRESS, "slug": "acme/demo"}],                    # another repository's entry
    [{"name": "demo", "address": ADDRESS, "slug": "acme/demo", "missing": True}],    # its folder gone
    [],
])
def test_a_repository_the_workspace_does_not_list_for_the_agent_records_nothing(client, owner, ws, listed):
    h, user = owner
    ws.repos = listed
    task_id, _ = started(client, h, user)
    assert (row(task_id)["repo_slug"], row(task_id)["repo_address"]) == (None, None)


@pytest.mark.parametrize("how", ["not running", "not answering"])
def test_a_task_made_while_the_workspace_could_not_say_gets_its_address_at_its_start(client, owner, ws, monkeypatch,
                                                                                       how):
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    listed = [dict(ws.repos[0], address=ADDRESS, slug="acme/demo")]
    if how == "not running":
        monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    else:
        ws.repos = None
    task = make_task(client, h, agent_id)
    assert (row(task["id"])["repo_slug"], row(task["id"])["repo_address"]) == (None, None)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace.test")
    ws.repos = listed
    assert press(client, h, task["id"], "start").status_code == 200
    assert (row(task["id"])["repo_slug"], row(task["id"])["repo_address"]) == ("acme/demo", ADDRESS)
    message = sent_task(user, ws)
    assert (message["repo_slug"], message["repo_address"]) == ("acme/demo", ADDRESS)


def test_at_its_start_a_task_takes_the_name_and_the_address_from_one_reading(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    ws.repos[0].update(address="https://a:b@github.com/acme/demo.git", slug="acme/demo")
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    assert (row(task["id"])["repo_slug"], row(task["id"])["repo_address"]) == ("acme/demo", None)
    ws.repos[0].update(address="https://github.com/elsewhere/demo.git", slug="elsewhere/demo")
    assert press(client, h, task["id"], "start").status_code == 200
    assert (row(task["id"])["repo_slug"], row(task["id"])["repo_address"]) == \
        ("elsewhere/demo", "https://github.com/elsewhere/demo.git")


def test_a_task_with_its_address_is_not_asked_again_at_its_start_and_keeps_the_one_recorded(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    ws.repos[0].update(address=ADDRESS, slug="acme/demo")
    task = make_task(client, h, make_agent(client, h, ACC["A"]))
    asked = len([c for c in ws.calls if c[1] == "/repos"])
    ws.repos[0].update(address="https://github.com/elsewhere/demo.git", slug="elsewhere/demo")
    assert press(client, h, task["id"], "start").status_code == 200
    assert len([c for c in ws.calls if c[1] == "/repos"]) == asked
    assert (row(task["id"])["repo_slug"], row(task["id"])["repo_address"]) == ("acme/demo", ADDRESS)


def test_a_received_task_keeps_the_owner_s_address_and_drops_one_that_could_carry_a_secret(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    fields = {"title": "Count lines", "goal": "Make the file two lines long.",
              "steps": [{"objective": "Append a line", "check": None}], "repo_slug": "acme/demo"}
    came = {"task": "9" * 32, "offer": "8" * 32, "packet_digest": "7" * 64, "from_user": "someone"}
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        kept = tasks.create(db, user["id"], agent_id, dict(fields, repo_address=ADDRESS), continued_from=came)
        db.execute("UPDATE ade_agent_tasks SET state = 'done' WHERE id = ?", (kept,))
        dropped = tasks.create(db, user["id"], agent_id, dict(fields, repo_address="https://a:b@github.com/acme/demo"),
                               continued_from=came)
    assert (row(kept)["repo_slug"], row(kept)["repo_address"]) == ("acme/demo", ADDRESS)
    assert (row(dropped)["repo_slug"], row(dropped)["repo_address"]) == ("acme/demo", None)


# a received task's owner's side, as task_offers gives it from the offer's packet: the "ran" one is no
# outward effect a packet carries (build_packet's own outward lists carry done and not done alone)
HANDED = {"from": "ana@example.com", "done_steps": [{"n": 1, "evidence": "checked"}],
          "outward_effects": [{"tool": "Bash", "kind": "git_remote", "target": "git push origin main", "state": "done"},
                              {"tool": "Bash", "kind": "external", "target": "curl https://hooks.example/x",
                               "state": "not_done"},
                              {"tool": "Bash", "kind": "git_remote", "target": "git push origin pp/x", "state": "ran"}]}
OWNERS_SIDE = {"done_steps": [1], "outward_effects": [
    {"tool": "Bash", "kind": "git_remote", "target": {"text": "git push origin main", "named_by": "model"},
     "state": "done"},
    {"tool": "Bash", "kind": "external", "target": {"text": "curl https://hooks.example/x", "named_by": "model"},
     "state": "not_done"}]}


def received(user, agent_id, handed=HANDED, steps=3, goal="Make the file three lines long."):
    """A task as task_offers makes it at an acceptance: tasks.create with its continued_from, the owner's side
    `handed`."""
    fields = {"title": "Count lines", "goal": goal, "between_steps": "wait",
              "steps": [{"objective": f"Append line {n}", "check": None} for n in range(1, steps + 1)],
              "repo_slug": "acme/demo", "repo_address": ADDRESS}
    came = {"task": "9" * 32, "offer": "8" * 32, "packet_digest": "7" * 64, "from_user": "someone"}
    if handed is not None:
        came["handed"] = handed
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return tasks.create(db, user["id"], agent_id, fields, continued_from=came)


def test_a_received_task_starts_at_the_first_step_left_and_every_packet_carries_the_owner_s_side(
        client, owner, ws):
    """The end-to-end tests found a received task made with every step to do, so its first attempt did again the step
    the owner had done, and its packets were marked as its own owner's, with neither the owner's address nor what the
    owner had pushed. The instructions are marked as a teammate's with the owner's address, and the owner's done steps
    and outward effects given as information; the outward effects reach every later attempt."""
    h, user = owner
    own(user, ACC["A"])
    task_id = received(user, make_agent(client, h, ACC["A"]))
    assert row(task_id)["step"] == 2 and row(task_id)["continued_from"]["handed"] == HANDED
    with store.connect() as db:
        assert [(s["state"], s["evidence"]) for s in tasks._steps(db, task_id)] == [
            ("done", "checked"), ("todo", None), ("todo", None)]
    assert press(client, h, task_id, "start").status_code == 200
    first = row(task_id)["lease_run"]
    p = json.loads(packet_of(first)[0])
    assert (p["instructions"]["by"], p["instructions"]["from"]) == ("teammate", "ana@example.com")
    assert p["instructions"]["step"]["n"] == 2 and p["next"] == {"step": 2, "action": "start step 2"}
    assert p["information"]["steps_done"] == [{"n": 1, "checkpoint": None, "evidence": "checked"}]
    assert p["information"]["handed"] == OWNERS_SIDE
    finish(user, first, 1, 2)
    assert row(task_id)["state"] == "suspended"
    assert press(client, h, task_id, "continue").status_code == 200
    p = json.loads(packet_of(row(task_id)["lease_run"])[0])
    assert p["instructions"]["by"] == "teammate" and p["information"]["handed"] == OWNERS_SIDE
    assert [s["n"] for s in p["information"]["steps_done"]] == [1, 2] and p["next"]["step"] == 3


def test_a_received_task_stays_in_its_group_its_crossing_switch_off_and_not_to_be_turned_on(client, owner, ws,
                                                                                            monkeypatch):
    """A task received by a pressed offer stays in the group it was accepted in. Made while the deployment's crossing is
    on, its cross_groups and custody_cross are 0; turning its switch on is refused (412), a turning on held for it would
    not apply, and its relay set is its home group alone. The control, an ordinary task made under the same setting with
    the same accounts, has its switch on and a relay set that reaches the Team organisations' seats, so the checks can
    fail."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["A"])                                                         # a personal plan
    own(user, ACC["B"], plan="Claude Team", org="org-x", org_name="X")          # a seat it could cross into
    theirs = received(user, make_agent(client, h, ACC["A"]))
    assert (row(theirs)["cross_groups"], row(theirs)["custody_cross"]) == (0, 0)
    r = press(client, h, theirs, "switches", cross_groups=True)
    assert (r.status_code, r.json()["detail"]) == (412, "A task received from a teammate stays in its group.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert tasks._cross_hold_applied(db, {"subject": theirs}, int(time.time())) == \
            "A task received from a teammate stays in its group."
    with store.connect() as db:
        assert tasks.relay_groups(db, row(theirs)) == (
            [shapes.PERSONAL], "a task received from a teammate stays in its group")
    assert row(theirs)["cross_groups"] == 0 and events(theirs, "task.switched") == []
    mine = make_task(client, h, make_agent(client, h, ACC["A"]))["id"]
    assert (row(mine)["cross_groups"], row(mine)["custody_cross"]) == (1, 0)
    with store.connect() as db:
        assert tasks.relay_groups(db, row(mine))[0] == [shapes.PERSONAL, "org:*"]


def test_a_custody_task_s_first_attempt_is_the_custodian_s_relay_s(client, owner, ws):
    """A custody task, made `ready`, is started by the custodian's relay (tasks.relay_start), its
    first attempt queued as `custody` on its account, never as a press, so the reserve holds; a task
    of the owner's own stays `ready` for its owner's Start, and a received task already going is left as it is."""
    h, user = owner
    own(user, ACC["A"])
    theirs = received(user, make_agent(client, h, ACC["A"]))
    mine = make_task(client, h, make_agent(client, h, ACC["A"]))["id"]
    tells = []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert tasks.relay_start(db, user["id"], theirs, tells=tells)["state"] == "running"
        assert tasks.relay_start(db, user["id"], mine)["state"] == "ready"
        # called again while its attempt is queued: nothing more is queued, and the task is not suspended by a refusal
        again = tasks.relay_start(db, user["id"], theirs)
    assert again["state"] == "running" and runs_of(mine) == []
    (first,) = runs_of(theirs)
    assert (first["chosen_by"], first["task_step"], json.loads(first["backend"])) == (
        "custody", 2, {"kind": "account", "id": ACC["A"]})
    assert "pressed" not in json.loads(first["trigger"]) and ("deliver",) in [tuple(t[:1]) for t in tells]


def test_a_custody_task_whose_first_attempt_is_refused_stays_taken_and_waits_saying_why(client, owner, ws, monkeypatch):
    """A custody task's first attempt in a savepoint of its own (_guarded), as after any of a task's steps: a refusal of
    it undoes only that attempt and what it left to tell; what the take wrote before it in the same transaction stands,
    and the custody task waits, stopped, saying why. Nothing in the app refuses it today (task_offers' take checks its
    agent in the same transaction), so the refusal is injected."""
    h, user = owner
    own(user, ACC["A"])
    theirs = received(user, make_agent(client, h, ACC["A"]))

    def refused(*args, **kwargs):
        raise agents.AgentError(412, "This agent is retired.")
    monkeypatch.setattr(tasks, "_queue", refused)
    tells = []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE ade_agent_tasks SET title = 'Taken by custody' WHERE id = ?", (theirs,))
        row = tasks.relay_start(db, user["id"], theirs, tells=tells)
    assert (row["state"], row["cause"]) == ("suspended", "stopped"), row
    assert row["reason"] == "The task could not go on: This agent is retired."
    assert tells == [] and runs_of(theirs) == []
    with store.connect() as db:
        assert tasks.get(db, user["id"], theirs)["title"] == "Taken by custody"


def test_a_received_task_takes_the_id_chosen_for_its_worktree_and_no_other(client, owner, ws):
    """task_offers chooses a received task's id before its fetch makes the task's worktree under it in the teammate's
    workspace, so that its attempts find that worktree: create gives the task that id, and refuses one out of shape, one
    taken, and any id for a task that is not received (task_offers' errors, ValueError)."""
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    fields = {"title": "Count lines", "goal": "Make the file two lines long.",
              "steps": [{"objective": "Append a line", "check": None}]}
    came = {"task": "9" * 32, "offer": "8" * 32, "packet_digest": "7" * 64, "from_user": "someone"}
    chosen = "a" * 32
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert tasks.create(db, user["id"], agent_id, fields, continued_from=came, task_id=chosen) == chosen
        db.execute("UPDATE ade_agent_tasks SET state = 'done' WHERE id = ?", (chosen,))
    for given, received_ in ((chosen, True), ("A" * 32, True), ("a" * 31, True), (32, True), ("b" * 32, False)):
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError):
                tasks.create(db, user["id"], agent_id, fields, continued_from=came if received_ else None,
                             task_id=given)
    with store.connect() as db:
        assert [r[0] for r in db.execute("SELECT id FROM ade_agent_tasks WHERE agent_id = ?", (agent_id,))] == [chosen]


def test_a_task_s_recorded_head_is_where_its_worktree_was_left(client, owner, ws):
    """tasks.recorded_head, by which task_offers pushes a received task's work (what the teammate's side pushes):
    none before the task learns its checkpoint 0; then that commit; its newest checkpoint's head; the end an attempt
    left past it; and a later attempt's checkpoint over that end again."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)

    def head():
        with store.connect() as db:
            return tasks.recorded_head(db, tasks.get(db, user["id"], task_id))
    assert head() is None
    zero = "a1" * 20
    record(user, first, "working", top={"base": zero})
    assert head() == zero
    record(user, first, "working", checkpoints=[cp(1, 1, kind="progress")])
    assert head() == cp(1, 1)["head"]
    record(user, first, "stopped", end_state={"head": "c" * 40, "tree": "d" * 40, "committed": True})
    assert head() == "c" * 40
    assert press(client, h, task_id, "continue").status_code == 200
    record(user, row(task_id)["lease_run"], "working", checkpoints=[cp(2, 1, kind="progress")])
    assert head() == cp(2, 1)["head"]


def test_a_received_task_without_the_owner_s_side_is_still_marked_as_a_teammate_s(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    task_id = received(user, make_agent(client, h, ACC["A"]), handed=None)
    assert row(task_id)["step"] == 1 and "handed" not in row(task_id)["continued_from"]
    assert press(client, h, task_id, "start").status_code == 200
    p = json.loads(packet_of(row(task_id)["lease_run"])[0])
    assert p["instructions"]["by"] == "teammate" and "from" not in p["instructions"]
    assert "handed" not in p["information"]


STEPS_WRONG = "handed's done steps are {n, evidence}, each a step of the task, once"
EFFECTS_WRONG = "handed's outward effects are {tool, kind, target, state}, each an outward one"
ADDRESS_WRONG = "handed names the owner's address"


@pytest.mark.parametrize("change, words", [
    ({"done_steps": [{"n": 1, "evidence": "trusted"}]}, STEPS_WRONG),
    ({"done_steps": [{"n": 0, "evidence": "checked"}]}, STEPS_WRONG),
    ({"done_steps": [{"n": 4, "evidence": "checked"}]}, STEPS_WRONG),
    ({"done_steps": [{"n": True, "evidence": "checked"}]}, STEPS_WRONG),
    ({"done_steps": [{"n": 1, "evidence": "checked"}, {"n": 1, "evidence": "checked"}]}, STEPS_WRONG),
    ({"done_steps": "1"}, STEPS_WRONG),
    ({"done_steps": [{"n": n, "evidence": "checked"} for n in (1, 2, 3)]}, "a task is handed with a step left to do"),
    ({"outward_effects": [{"tool": "Bash", "kind": "local", "target": "make", "state": "done"}]}, EFFECTS_WRONG),
    ({"outward_effects": [{"tool": "Bash", "kind": "git_remote", "target": "git push", "state": "pushed"}]},
     EFFECTS_WRONG),
    ({"outward_effects": "none"}, EFFECTS_WRONG),
    ({"from": "ana@example.com\nBcc: x"}, ADDRESS_WRONG), ({"from": ""}, ADDRESS_WRONG),
    ({"from": None}, ADDRESS_WRONG),
    ({"extra": True}, "handed is {from, done_steps, outward_effects}")])
def test_a_received_task_s_owner_s_side_out_of_shape_is_refused_and_no_task_made(client, owner, ws, change, words):
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    with pytest.raises(ValueError) as refused:
        received(user, agent_id, handed=dict(HANDED, **change))
    assert str(refused.value) == words
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_tasks WHERE agent_id = ?", (agent_id,)).fetchone()[0] == 0


def test_a_received_task_s_instructions_are_measured_with_the_teammate_s_marks(client, owner, ws):
    """The instructions fit when the task is made as its packets hold them: a received task's with its teammate's
    marks, which a task that fits only without them would pass at its first attempt."""
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    objectives = [f"Append line {n}" for n in (1, 2, 3)]
    goal = "a" * (tasks.INSTRUCTIONS_MAX - tasks.instructions_size("Count lines", "", objectives, []))
    assert tasks.instructions_size("Count lines", goal, objectives, []) == tasks.INSTRUCTIONS_MAX
    marked = tasks.instructions_size("Count lines", goal, objectives, [], {"task": "9" * 32, "handed": HANDED})
    assert marked > tasks.INSTRUCTIONS_MAX
    with pytest.raises(agents.AgentError) as refused:
        received(user, agent_id, goal=goal)
    assert refused.value.status == 400
    task_id = received(user, agent_id, goal=goal[:tasks.INSTRUCTIONS_MAX - marked])
    assert row(task_id)["step"] == 2


# ---- continuations, auto-resume and reserves, identities, answers, records, Stop and changes, returns ----------------

def done_push(call_id="toolu_9"):
    return {"call_id": call_id, "tool": "Bash", "kind": "git_remote", "target": "git push origin pp/task",
            "intent": {"address": "https://example.test/r.git"}, "settled": "done", "exit": 0}


def set_moves(task_id, count, now=None):
    """The task's moves of the last hour, `count` of them, as if the product had made them."""
    now = int(time.time()) if now is None else now
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET moves = ? WHERE id = ?",
                   (json.dumps([now - 60 - i for i in range(count)]), task_id))


def window_reset(user, account, window):
    with store.connect() as db:
        return db.execute("SELECT resets_at FROM ade_backend_windows WHERE user_id = ? AND backend = ? AND "
                          "rate_window = ?", (user["id"], "account:" + account, window)).fetchone()["resets_at"]


def closed(user, account, resets):
    """The account's five-hour window refused, as an earlier attempt of another task would have left it."""
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + account,
                           {"five_hour": {"status": "rejected", "resets_at": resets, "at": now}}, None, now)


@pytest.mark.parametrize("end", ["done", "refused"])
def test_an_agent_retired_while_its_attempt_works_keeps_the_end_s_record_and_holds_the_task(client, owner, ws, end):
    """The end's record is taken whatever the continuation finds, and a retired agent
    holds the continuation, the task waiting with why; the owner's task list still answers."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    record(user, first, "working")
    agents.retire(user, row(task_id)["agent_id"])
    if end == "done":
        finish(user, first, 1, 1, effects=[done_push()])
    else:
        refuse(user, first, resets=int(time.time()) + HOUR, effects=[done_push()])
    held = row(task_id)
    assert held["state"] == "suspended" and held["cause"] == "stopped" and "retired" in held["reason"]
    assert held["lease_run"] is None and len(runs_of(task_id)) == 1
    with store.connect() as db:
        effects = [r["state"] for r in db.execute("SELECT state FROM ade_agent_effects WHERE task_id = ?", (task_id,))]
        step = db.execute("SELECT state FROM ade_agent_task_steps WHERE task_id = ? AND n = 1", (task_id,)).fetchone()
        points = db.execute("SELECT COUNT(*) FROM ade_agent_checkpoints WHERE task_id = ?", (task_id,)).fetchone()[0]
    assert effects == ["done"] and step["state"] == ("done" if end == "done" else "doing")
    assert points == (1 if end == "done" else 0)
    assert client.get(T, headers=h).status_code == 200 and client.get(f"{T}/{task_id}", headers=h).status_code == 200


@pytest.mark.parametrize("how", ["retired", "paused"])
def test_the_owner_s_recorded_choice_waits_with_its_agent_retired_or_paused_and_is_kept(client, owner, ws, how):
    """Next on... recorded while the attempt worked, its agent retired or paused before the end: the end's
    record is taken and the choice is kept for the owner's Continue, the task waiting with why."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    record(user, first, "working")
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["B"]}).status_code == 200
    if how == "retired":
        agents.retire(user, row(task_id)["agent_id"])
    else:
        with store.connect() as db:
            db.execute("UPDATE ade_agents SET paused = 1")
    finish(user, first, 1, 1)
    held = row(task_id)
    assert held["state"] == "suspended" and held["cause"] == "stopped" and held["lease_run"] is None
    assert held["reason"] == ("Its agent is retired: nothing continues this task." if how == "retired"
                              else "Its agent is paused: resume it, then Continue.")
    assert held["asked"] == {"kind": "account", "id": ACC["B"]} and len(runs_of(task_id)) == 1


def test_a_continuation_refused_at_an_end_undoes_only_the_continuation(client, owner, ws, monkeypatch):
    """Whatever refuses the continuation, what the end's record brought stays, and nothing of the continuation
    is told."""
    h, user = owner
    task_id, first = started(client, h, user)

    def refused(db, user_id, task, now, tells, chosen_by):
        tells.append(("continued", f"task-continued:{task['id']}:1"))
        raise agents.AgentError(409, "Refused for the test.")
    monkeypatch.setattr(tasks, "_next", refused)
    finish(user, first, 1, 1, effects=[done_push()])
    held = row(task_id)
    assert held["state"] == "suspended" and held["cause"] == "stopped" and held["lease_run"] is None
    assert held["reason"] == "The task could not go on: Refused for the test."
    with store.connect() as db:
        assert db.execute("SELECT state FROM ade_agent_task_steps WHERE task_id = ? AND n = 1",
                          (task_id,)).fetchone()["state"] == "done"
        assert db.execute("SELECT COUNT(*) FROM ade_agent_effects WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    assert ws.pushes == []


@pytest.mark.parametrize("where", ["next step", "relay"])
def test_an_automatic_continuation_waits_until_midnight_when_its_agent_s_day_s_runs_are_spent(client, owner, ws,
                                                                                               monkeypatch, where):
    """An automatic continuation is a firing, held while its agent's day's runs are spent."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    limits = agents.limits_of
    monkeypatch.setattr(agents, "limits_of", lambda agent: dict(limits(agent), max_runs_per_day=1))
    if where == "next step":
        finish(user, first, 1, 1)
    else:
        refuse(user, first, resets=int(time.time()) + HOUR)
    held = row(task_id)
    now = int(time.time())
    assert held["state"] == "suspended" and held["cause"] == "window" and held["waits_until"] == now - now % 86400 + \
        86400 and "runs for today are spent" in held["reason"] and len(runs_of(task_id)) == 1
    assert ws.kinds() == []


@pytest.mark.parametrize("where", ["next step", "relay"])
def test_an_automatic_continuation_of_a_paused_agent_waits_for_the_owner(client, owner, ws, where):
    """Held while its agent is paused, for the owner's Continue once it is resumed."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET paused = 1")
    if where == "next step":
        finish(user, first, 1, 1)
    else:
        refuse(user, first, resets=int(time.time()) + HOUR)
    held = row(task_id)
    assert held["state"] == "suspended" and held["cause"] == "stopped" and "paused" in held["reason"]
    assert len(runs_of(task_id)) == 1


def one_run_a_day(monkeypatch):
    limits = agents.limits_of
    monkeypatch.setattr(agents, "limits_of", lambda agent: dict(limits(agent), max_runs_per_day=1))


def test_a_spent_day_s_runs_never_put_off_the_owner_s_turn(client, owner, ws, monkeypatch):
    """When no account in the relay set has a known reset, the task needs the owner (UNKNOWN is not
    AVAILABLE). The relay decides first, and only an attempt it would queue now is held as a firing is: a spent day's
    runs never turns the owner's decision into a wait until midnight, after which nothing would decide it."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    one_run_a_day(monkeypatch)
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["B"],
                           {"five_hour": {"status": "rejected", "resets_at": None, "at": now}}, None, now)
    refuse(user, first)                                 # A refused too, with no reset known
    held = row(task_id)
    assert (held["state"], held["cause"], held["waits_until"]) == ("needs_owner", "window", None)
    assert held["reason"].startswith("No account this task may take has a known time its window resets")
    assert (f"task-needs:{task_id}:window", "task-needs-you") in tasks.alerts(user["id"], now)
    assert len(runs_of(task_id)) == 1


def test_a_spent_day_s_runs_never_hold_a_hand_off_to_the_custodian(client, owner, ws, monkeypatch, custodian):
    """A custody hand-off starts no run of the owner's agent, so his agent's day's runs
    spent do not hold it; only a move onto another of his accounts, which queues an attempt of that agent, is held as
    a firing is."""
    h, user = owner
    task_id, first = started(client, h, user)
    one_run_a_day(monkeypatch)
    refuse(user, first, resets=int(time.time()) + 5 * HOUR)
    after = row(task_id)
    assert after["state"] == "offering" and [b[0] for b in custodian] == [task_id] and len(after["moves"]) == 1
    assert after["waits_until"] is None and len(runs_of(task_id)) == 1


def test_a_spent_day_s_runs_never_put_off_a_next_step_s_wait_for_its_reset(client, owner, ws, monkeypatch):
    """After a step the next step decides first, so an account whose window was refused waits for its reset,
    which a spent day's runs of the agent never turns into a wait until midnight; only an attempt it would queue now
    is held as a firing is."""
    h, user = owner
    task_id, first = started(client, h, user)
    one_run_a_day(monkeypatch)
    now = int(time.time())
    midnight = (now // 86400 + 1) * 86400
    reset = now + 600 if now + 600 != midnight else now + 601
    finish(user, first, 1, 1, limits={"five_hour": {"status": "rejected", "resets_at": reset, "at": now}})
    held = row(task_id)
    assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", reset)
    assert "runs for today" not in held["reason"] and len(runs_of(task_id)) == 1


def test_a_continuation_held_for_a_day_s_runs_goes_on_after_midnight(client, owner, ws, monkeypatch):
    """With Auto-resume on, a next step held for its agent's day's runs waits until midnight UTC, and the
    round after midnight queues it."""
    h, user = owner
    task_id, first = started(client, h, user)
    one_run_a_day(monkeypatch)
    finish(user, first, 1, 1)
    midnight = row(task_id)["waits_until"]
    assert row(task_id)["cause"] == "window" and midnight > int(time.time())
    tasks.sweep(now=midnight + 1)
    after = row(task_id)
    assert after["state"] == "running" and run(after["lease_run"])["task_step"] == 2


@pytest.mark.parametrize("off", ["setting", "switch"])
def test_with_auto_resume_off_a_spent_day_s_runs_wait_for_the_owner_and_say_so(client, owner, ws, monkeypatch, off):
    """With Auto-resume off nothing takes a continuation at midnight, so a next step held for its agent's day's
    runs waits for the owner's Continue, saying so, never that it continues after midnight; and Continue, his own
    press and no firing, runs it."""
    h, user = owner
    task_id, first = started(client, h, user)
    if off == "setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", "0")
    else:
        assert press(client, h, task_id, "switches", auto_resume=False).status_code == 200
    one_run_a_day(monkeypatch)
    finish(user, first, 1, 1)
    held = row(task_id)
    assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "stopped", None)
    assert held["reason"] == ("Its agent's runs for today are spent (1 of 1), and Auto-resume is off: press Continue "
                              "when you choose.")
    assert press(client, h, task_id, "continue").status_code == 200 and row(task_id)["state"] == "running"


def test_an_automatic_resume_whose_packet_cannot_be_built_says_nothing_continued(client, owner, ws):
    """Auto-resume on the same account, whose packet cannot be built (a secret's
    shape put in the owner's goal meanwhile): what holds the task wins, and nothing says it continued, no journal line,
    no push, and the reset is not marked as resumed on."""
    h, user = owner
    task_id, first = started(client, h, user)
    now = int(time.time())
    refuse(user, first, resets=now + HOUR)
    assert (row(task_id)["state"], row(task_id)["waits_until"]) == ("suspended", now + HOUR)
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)["state_version"],
                                                       "goal": "Push with " + SECRET})
    assert r.status_code == 200, r.text
    continued = len(events(task_id, "task.continued"))
    tasks.sweep(now=now + HOUR + 1)
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", "secret") and len(runs_of(task_id)) == 1
    assert len(events(task_id, "task.continued")) == continued and ws.kinds() == [] and held["resumed"] == {}


def test_continue_on_whose_packet_cannot_be_built_is_not_used_up(client, owner, ws):
    """The owner's choice whose packet cannot be built is not used up,
    as a move's is not (_move): what holds the task wins, the backend stays, nothing says it continued, and once he
    edits his text his next Continue goes on the backend he chose."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user, between_steps="wait")
    finish(user, first, 1, 1)
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)["state_version"],
                                                       "goal": "Push with " + SECRET})
    assert r.status_code == 200, r.text
    continued = len(events(task_id, "task.continued"))
    b = {"kind": "account", "id": ACC["B"]}
    r = press(client, h, task_id, "continue-on", backend=b)
    assert r.status_code == 200 and r.json()["queued"] is False
    held = row(task_id)
    assert (held["state"], held["cause"], held["asked"]) == ("needs_owner", "secret", b)
    assert held["backend"] == {"kind": "account", "id": ACC["A"]} and len(runs_of(task_id)) == 1
    assert len(events(task_id, "task.continued")) == continued
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)["state_version"],
                                                       "goal": "Make the file two lines long."})
    assert r.status_code == 200, r.text
    assert press(client, h, task_id, "resolve", what="secret", outcome="lift").status_code == 200
    assert press(client, h, task_id, "continue").status_code == 200
    assert acct(row(task_id)["lease_run"]) == ACC["B"] and row(task_id)["asked"] is None


@pytest.mark.parametrize("state", ["done", "failed", "stopped"])
def test_a_watched_place_changed_holds_the_task_whatever_ended_its_attempt(client, owner, ws, state):
    """A watched place the attempt changed wins over whatever else its end says, not only at a
    refusal: an attempt that ends done, failed or stopped with a changed watched place holds the task for the owner,
    cause watch, and nothing goes on."""
    h, user = owner
    task_id, first = started(client, h, user)
    watch = {"digest": "a" * 64, "changed": ["~/.ssh/config"]}
    if state == "done":
        finish(user, first, 1, 1, watch=watch)
    else:
        record(user, first, state, watch=watch)
    held = row(task_id)
    assert (held["state"], held["cause"], held["lease_run"]) == ("needs_owner", "watch", None)
    assert len(runs_of(task_id)) == 1


@pytest.mark.parametrize("off", ["setting", "switch"])
def test_with_the_relay_off_the_reserve_holds_the_next_step_and_the_task_waits_for_its_reset(client, owner, ws,
                                                                                              monkeypatch, off):
    """No unattended start uses an account whose seven-day window is near its limit, the next step
    on the same account among them, whether the relay is on or off; the owner's own press still runs there."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    if off == "setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "0")
    else:
        assert press(client, h, task_id, "switches", relay=False).status_code == 200
    near_week(user, ACC["H"])
    now = int(time.time())
    with store.connect() as db:                         # a model's own weekly window too, resetting sooner
        tasks.take_windows(db, user["id"], "account:" + ACC["H"], {
            "seven_day_opus": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}}, None, now)
    finish(user, first, 1, 1)
    waiting = row(task_id)
    assert waiting["state"] == "suspended" and waiting["cause"] == "window" and len(runs_of(task_id)) == 1
    assert waiting["waits_until"] == window_reset(user, ACC["H"], "seven_day_opus")
    assert press(client, h, task_id, "continue").status_code == 200
    pressed = row(task_id)["lease_run"]
    sent = sent_task(user, ws)
    assert acct(pressed) == ACC["H"] and (sent["chosen_by"], sent["reserve"]) == ("owner", False)


def test_with_the_reserve_lifted_the_next_step_stays_on_its_account_with_the_relay_off(client, owner, ws,
                                                                                        monkeypatch):
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "0")
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET reserve_lifted = 1 WHERE account = ?", (ACC["H"],))
    near_week(user, ACC["H"])
    finish(user, first, 1, 1)
    assert acct(row(task_id)["lease_run"]) == ACC["H"]


def test_with_the_relay_on_two_warned_weekly_windows_hold_the_task_until_the_later_one_resets(client, owner, ws):
    """No unattended start uses an account any of whose
    seven-day windows last reported allowed_warning, a report that stands until a later event for that window or its
    reset time. H, the task's only account, warned on two weekly windows, with the relay on: its next step waits for the
    earlier reset (the relay's wait counts the account that refused); at that reset the other warning still stands, so
    auto-resume finds H held back still, and the task waits again, now for the later reset, where it needed the owner
    ("the task waits for the next reset known", tasks._auto_resume); at that reset the step starts on H. Its first wait
    is the earlier reset; a rule that an account is open only when all its windows are would have the task wait for the
    later reset at once, as backend_state does for refused windows."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    near_week(user, ACC["H"])
    now = int(time.time())
    with store.connect() as db:                         # a model's own weekly window too, resetting sooner
        tasks.take_windows(db, user["id"], "account:" + ACC["H"], {
            "seven_day_opus": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}}, None, now)
    finish(user, first, 1, 1)
    opus, week = window_reset(user, ACC["H"], "seven_day_opus"), window_reset(user, ACC["H"], "seven_day")
    waiting = row(task_id)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", opus)
    tasks.sweep(now=opus)
    after = row(task_id)
    assert len(runs_of(task_id)) == 1
    assert (after["state"], after["cause"], after["waits_until"]) == ("suspended", "window", week), dict(after)
    tasks.sweep(now=week)
    resumed = row(task_id)
    assert resumed["state"] == "running" and acct(resumed["lease_run"]) == ACC["H"] and len(runs_of(task_id)) == 2


def test_with_the_relay_off_auto_resume_waits_for_the_reserve_s_reset_instead(client, owner, ws, monkeypatch):
    """Auto-resume is an unattended start; its own account's window opened, its reserve holds it back."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "0")
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    near_week(user, ACC["H"])
    tasks.sweep(now=reset + 1)
    waiting = row(task_id)
    assert len(runs_of(task_id)) == 1 and waiting["state"] == "suspended"
    assert waiting["waits_until"] == window_reset(user, ACC["H"], "seven_day")


def test_with_the_relay_on_auto_resume_waits_for_the_reserve_s_reset_too(client, owner, ws):
    """H, the task's only
    account, refused; at the reset its window opened, but a seven-day window reported near its limit holds it back from
    the unattended start. The task waits for that window's reset, as tasks._auto_resume says ("its weekly
    reserve holding it back ... the task waits for the next reset known"), where it needed the owner; at that reset
    auto-resume starts the attempt on H, its window open and the reserve gone."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    assert row(task_id)["waits_until"] == reset
    near_week(user, ACC["H"])
    tasks.sweep(now=reset + 1)
    week = window_reset(user, ACC["H"], "seven_day")
    waiting = row(task_id)
    assert len(runs_of(task_id)) == 1
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", week), dict(waiting)
    tasks.sweep(now=week)
    resumed = row(task_id)
    assert resumed["state"] == "running" and len(runs_of(task_id)) == 2
    assert acct(resumed["lease_run"]) == ACC["H"] and run(resumed["lease_run"])["chosen_by"] == "auto_resume"


def test_with_the_relay_on_auto_resume_waits_for_its_own_account_s_refusal_while_its_reserve_holds_it_back(
        client, owner, ws):
    """The account the task is on counts in auto-resume's wait while its weekly reserve alone leaves it
    out of the relay set, whether or not a window still holds it. H, the task's only account, refused until a reset an
    hour on; before that reset another run on H hears its five-hour window refused again, resetting two hours after the
    first reset, and a seven-day window near its limit. At the first reset H is still refused, and the task waits for
    its new reset, where a wait counting H only once its window opened would put the task to the owner; at that reset
    H's window opens, the reserve still holding it, and the task waits for the reserve's reset, where auto-resume starts
    on H."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    assert row(task_id)["waits_until"] == reset
    later = reset + 2 * HOUR
    closed(user, ACC["H"], later)
    near_week(user, ACC["H"])
    tasks.sweep(now=reset + 1)
    waiting = row(task_id)
    assert len(runs_of(task_id)) == 1
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", later), \
        dict(waiting)
    tasks.sweep(now=later)
    week = window_reset(user, ACC["H"], "seven_day")
    held = row(task_id)
    assert len(runs_of(task_id)) == 1
    assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", week), dict(held)
    tasks.sweep(now=week)
    resumed = row(task_id)
    assert resumed["state"] == "running" and len(runs_of(task_id)) == 2
    assert acct(resumed["lease_run"]) == ACC["H"] and run(resumed["lease_run"])["chosen_by"] == "auto_resume"


@pytest.mark.parametrize("out", ["taken out of relays", "signed out", "its identity changed: confirm it again"])
def test_with_the_relay_on_auto_resume_waits_for_no_reserve_of_an_account_it_may_not_take(client, owner, ws, out):
    """The account the task is on counts in auto-resume's wait, the relay on, while its weekly reserve
    alone leaves it out of the task's relay set: that reserve lifts by itself once no warned window's report stands, and
    the task may take the account again then. Out for another reason as well when auto-resume reads
    the relay set (taken out of relays, signed out, or its identity changed), it
    would still be out after the reserve lifts: the task does not wait for it, and needs the owner at once, as it did
    before the fix."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    near_week(user, ACC["H"])
    owner_id = own(user, ACC["H"])
    if out == "taken out of relays":
        tasks.put_account(user, owner_id, {"in_relays": False})
    else:
        with store.connect() as db:
            db.execute("UPDATE ade_account_owners SET " + ("ready = 0" if out == "signed out" else "state = 'changed'")
                       + " WHERE id = ?", (owner_id,))
    with store.connect() as db:
        left = {e["account"]: e["left_out"] for e in tasks.relay_set(db, user["id"], task_id, reset + 1)}
    assert left == {ACC["H"]: out}
    tasks.sweep(now=reset + 1)
    held = row(task_id)
    assert len(runs_of(task_id)) == 1
    assert (held["state"], held["cause"], held["waits_until"]) == ("needs_owner", "window", None), dict(held)


def test_with_the_relay_on_auto_resume_waits_for_no_reserve_of_its_account_out_of_the_task_s_group(
        client, owner, ws, monkeypatch, clock):
    """The account the task is on counts in auto-resume's wait only while
    its weekly reserve alone keeps it out of the relay set, never while its group does. Started on the seat A, the
    task crossed onto the personal plan B; its crossing switch is then turned off away from home, which leaves B out
    for its group. The seat refuses again with no known reset, B is held back by its reserve and refuses
    until a reset an hour on, and the relay's own wait counts B, which refused. At that reset B's window opens, but B
    would still be out after its reserve lifts: the task needs the owner, as it did before the fix, where a wait that
    counted B by the other conditions alone (confirmed, in relays, signed in) would sleep until B's weekly reset."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, _ = away_from_home(client, h, user, clock)
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    closed(user, ACC["A"], None)                         # the seat refused again, its reset unknown
    near_week(user, ACC["B"])
    reset = clock[0] + HOUR
    refuse(user, row(task_id)["lease_run"], resets=reset)
    assert (row(task_id)["state"], row(task_id)["waits_until"]) == ("suspended", reset)
    with store.connect() as db:
        left = {e["account"]: e["left_out"] for e in tasks.relay_set(db, user["id"], task_id, reset + 1)}
    assert left == {ACC["A"]: None, ACC["B"]: "crossing organisations is off for this task"}
    tasks.sweep(now=reset + 1)
    held = row(task_id)
    assert len(runs_of(task_id)) == 2
    assert (held["state"], held["cause"], held["waits_until"]) == ("needs_owner", "window", None), dict(held)


def test_with_the_relay_on_a_reserve_whose_warning_gave_no_reset_puts_the_task_to_the_owner(client, owner, ws):
    """A lone warning holding back the task's own account gave no reset. It
    stands until a later event for that window, a time nothing knows, so the task needs the owner at once:
    UNKNOWN is not AVAILABLE. The wait's own line is not what decides it:
    the test passes before the fix too, _reserve_until giving no reset; it pins what the wait does with such a warning.
    Beside a dated warning on another weekly window, the task would wait for the dated reset first."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["H"], {
            "seven_day": {"status": "allowed_warning", "resets_at": None, "at": now}}, None, now)
        left = {e["account"]: e["left_out"] for e in tasks.relay_set(db, user["id"], task_id, reset + 1)}
    assert left == {ACC["H"]: "its weekly reserve"}
    tasks.sweep(now=reset + 1)
    held = row(task_id)
    assert len(runs_of(task_id)) == 1
    assert (held["state"], held["cause"], held["waits_until"]) == ("needs_owner", "window", None), dict(held)


def test_with_the_relay_on_auto_resume_waits_for_the_earliest_reserve_of_any_account_its_reserve_alone_holds_back(
        client, owner, ws):
    """Auto-resume's wait
    counts each account of the relay set that its weekly reserve alone holds back, the task's own and any other, at its
    warned window's reset, so that the task wakes at the earliest such reset and decides again there; before, it counted
    the task's own alone. H, another
    account, is held back by a reserve that lifts after a day; A, the task's own, by one that lifts after three: the
    task waits for H's, and at that reset auto-resume moves it onto H, whose reserve has lifted, A still held back."""
    h, user = owner
    own(user, ACC["H"])
    now = int(time.time())
    with store.connect() as db:                         # H held back before A refuses, so the relay does not take it
        tasks.take_windows(db, user["id"], "account:" + ACC["H"], {
            "seven_day_opus": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}}, None, now)
    task_id, first = started(client, h, user, account="A")
    reset = now + HOUR
    refuse(user, first, resets=reset)
    assert (row(task_id)["waits_until"], len(runs_of(task_id))) == (reset, 1)
    near_week(user, ACC["A"])                           # A's own reserve, three days off
    tasks.sweep(now=reset + 1)
    with store.connect() as db:
        left = {e["account"]: e["left_out"] for e in tasks.relay_set(db, user["id"], task_id, reset + 1)}
    assert left == {ACC["A"]: "its weekly reserve", ACC["H"]: "its weekly reserve"}
    waiting = row(task_id)
    h_reserve = window_reset(user, ACC["H"], "seven_day_opus")
    assert h_reserve < window_reset(user, ACC["A"], "seven_day")
    assert len(runs_of(task_id)) == 1
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", h_reserve), \
        dict(waiting)
    tasks.sweep(now=h_reserve)
    moved = row(task_id)
    assert moved["state"] == "running" and len(runs_of(task_id)) == 2
    assert acct(moved["lease_run"]) == ACC["H"] and run(moved["lease_run"])["chosen_by"] == "auto_resume"


@pytest.mark.parametrize("at", ["the relay's wait after a refusal", "auto-resume's wait"])
def test_with_the_relay_on_the_task_waits_for_another_account_s_reserve_where_it_lifts_first(client, owner, ws,
                                                                                            at):
    """The relay's own wait
    after a refusal, and auto-resume's wait, count each account of the relay set that its weekly reserve alone holds
    back, at its warned window's reset. B, another account, is held back by a reserve that lifts after a day. A, the
    task's own, refuses until two days on: the relay's wait counts B, so the task waits for B's reserve, where it waited
    for A's reset. Or A refuses until an hour on, and at that reset refuses again until two days on: auto-resume's wait
    counts B, so the task waits for B's reserve, where it waited for A's new reset. Either way, at B's reserve's reset
    auto-resume moves the task onto B, its reserve lifted: the wait changes when the task wakes, never what it may
    take."""
    h, user = owner
    own(user, ACC["B"])
    now = int(time.time())
    with store.connect() as db:                         # B held back by its reserve before A refuses
        tasks.take_windows(db, user["id"], "account:" + ACC["B"], {
            "seven_day": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}}, None, now)
    task_id, first = started(client, h, user, account="A")
    later = now + 2 * 86400
    if at == "the relay's wait after a refusal":
        refuse(user, first, resets=later)
    else:
        reset = now + HOUR
        refuse(user, first, resets=reset)
        assert row(task_id)["waits_until"] == reset
        closed(user, ACC["A"], later)                   # A refused again, its new reset two days on
        tasks.sweep(now=reset + 1)
    b_reserve = window_reset(user, ACC["B"], "seven_day")
    with store.connect() as db:
        left = {e["account"]: e["left_out"] for e in tasks.relay_set(db, user["id"], task_id, int(time.time()))}
    assert left == {ACC["A"]: None, ACC["B"]: "its weekly reserve"}
    waiting = row(task_id)
    assert len(runs_of(task_id)) == 1
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", b_reserve), \
        dict(waiting)
    tasks.sweep(now=b_reserve)
    moved = row(task_id)
    assert moved["state"] == "running" and len(runs_of(task_id)) == 2
    assert acct(moved["lease_run"]) == ACC["B"] and run(moved["lease_run"])["chosen_by"] == "auto_resume"


@pytest.mark.parametrize("out", ["taken out of relays", "taken out of this task"])
def test_with_the_relay_on_the_task_waits_for_no_reserve_of_an_account_out_for_another_reason_too(
        client, owner, ws, out):
    """An account counts in the wait only where its weekly reserve alone
    keeps it out of the relay set. B, held back by a reserve that lifts after a day, is out for another reason as well
    (taken out of relays, or taken out of this task), which the relay set names, its reserve being read last; it would
    still be out once its reserve lifts, so the task waits for A's reset, two days on, as before."""
    h, user = owner
    owner_id = own(user, ACC["B"])
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["B"], {
            "seven_day": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}}, None, now)
    task_id, first = started(client, h, user, account="A")
    if out == "taken out of relays":
        tasks.put_account(user, owner_id, {"in_relays": False})
    else:
        with store.connect() as db:
            db.execute("UPDATE ade_agent_tasks SET taken_out = ? WHERE id = ?", (json.dumps([owner_id]), task_id))
    later = now + 2 * 86400
    refuse(user, first, resets=later)
    with store.connect() as db:
        left = {e["account"]: e["left_out"] for e in tasks.relay_set(db, user["id"], task_id, int(time.time()))}
    assert left == {ACC["A"]: None, ACC["B"]: out}
    waiting = row(task_id)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", later), \
        dict(waiting)


@pytest.mark.parametrize("b_days", [2, 4])
def test_with_the_relay_on_auto_resume_waits_for_the_earliest_of_its_reserve_and_another_account_s_reset(
        client, owner, ws, b_days):
    """Auto-resume's wait counts the accounts the task may take as well as its own account held back by
    its reserve, and waits for the earliest of their resets (as the relay's wait has it for the accounts it may take). B
    is refused on its seven-day window; A, the task's own, opened at its five-hour reset, but its reserve lifts
    after three days. B's refusal resetting two days on: the task waits for it and moves onto B there, auto-resume's
    move, A still held back. Resetting four days on: the task waits for A's reserve,
    and at its reset resumes on A."""
    h, user = owner
    own(user, ACC["B"])
    now = int(time.time())
    with store.connect() as db:                         # B out of room before A refuses, so the relay waits
        tasks.take_windows(db, user["id"], "account:" + ACC["B"], {
            "seven_day": {"status": "rejected", "resets_at": now + b_days * 86400, "at": now}}, None, now)
    task_id, first = started(client, h, user, account="A")
    reset = now + HOUR
    refuse(user, first, resets=reset)
    assert (row(task_id)["waits_until"], len(runs_of(task_id))) == (reset, 1)
    near_week(user, ACC["A"])                           # A's own reserve, three days off
    tasks.sweep(now=reset + 1)
    b_reset, a_reserve = window_reset(user, ACC["B"], "seven_day"), window_reset(user, ACC["A"], "seven_day")
    first_up, account = (b_reset, ACC["B"]) if b_reset < a_reserve else (a_reserve, ACC["A"])
    assert (account == ACC["B"]) is (b_days == 2)
    waiting = row(task_id)
    assert len(runs_of(task_id)) == 1
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", first_up), \
        dict(waiting)
    tasks.sweep(now=first_up)
    moved = row(task_id)
    assert moved["state"] == "running" and len(runs_of(task_id)) == 2
    assert acct(moved["lease_run"]) == account and run(moved["lease_run"])["chosen_by"] == "auto_resume"


@pytest.mark.parametrize("off", ["setting", "switch"])
def test_with_the_relay_off_the_task_resumes_on_its_own_account_at_its_reset(client, owner, ws, monkeypatch, off):
    """With the relay off, only auto-resume on the same account."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    if off == "setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "0")
    else:
        assert press(client, h, task_id, "switches", relay=False).status_code == 200
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    tasks.sweep(now=reset + 1)
    resumed = row(task_id)["lease_run"]
    assert resumed and acct(resumed) == ACC["A"] and run(resumed)["chosen_by"] == "auto_resume"


@pytest.fixture
def away(client, owner, ws, monkeypatch):
    """A task made on a Team seat (A, organisation X) with crossing on, relayed onto a personal plan (B) when A
    refused; then A reported room again, on fresh evidence."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-x", org_name="X")
    own(user, ACC["B"])
    task_id, first = started(client, h, user, steps=3)
    refuse(user, first, resets=int(time.time()) + HOUR)
    on_b = row(task_id)["lease_run"]
    assert acct(on_b) == ACC["B"] and row(task_id)["home_grp"] == "org:org-x"
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"], allowed(), None, now)
    return h, user, task_id, on_b


def test_at_the_bound_back_home_needs_the_owner(client, away):
    """The way home is a move, and a move past the bound puts the task in needs_owner,
    cause moves: it never works on outside its home group,
    which Back home exists to end. Continue then clears the moves and goes on."""
    h, user, task_id, on_b = away
    set_moves(task_id, tasks.MOVES_BOUND)
    finish(user, on_b, 1, 1)
    held = row(task_id)
    assert (held["state"], held["cause"], held["lease_run"]) == ("needs_owner", "moves", None)
    assert len(runs_of(task_id)) == 2
    assert press(client, h, task_id, "continue").status_code == 200 and row(task_id)["moves"] == []


@pytest.mark.parametrize("moved", [tasks.MOVES_BOUND - 2, tasks.MOVES_BOUND - 1])
def test_below_the_bound_back_home_moves_the_task_home(client, away, moved):
    """Below the bound, up to one short of it, the way home is taken."""
    h, user, task_id, on_b = away
    set_moves(task_id, moved)
    finish(user, on_b, 1, 1)
    assert acct(row(task_id)["lease_run"]) == ACC["A"] and len(row(task_id)["moves"]) == moved + 1


def test_at_the_bound_a_switch_turned_off_away_from_home_needs_the_owner(client, away):
    """With Cross organisations off the task must go home, and at the bound it cannot move."""
    h, user, task_id, on_b = away
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    set_moves(task_id, tasks.MOVES_BOUND)
    finish(user, on_b, 1, 1)
    held = row(task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "moves" and held["lease_run"] is None


def test_with_the_relay_off_back_home_is_not_taken(client, away):
    """With the relay off nothing moves a task by itself, the way home among it."""
    h, user, task_id, on_b = away
    assert press(client, h, task_id, "switches", relay=False).status_code == 200
    finish(user, on_b, 1, 1)
    assert acct(row(task_id)["lease_run"]) == ACC["B"]


def test_an_automatic_resume_onto_another_account_at_the_bound_needs_the_owner(client, owner, ws):
    """Auto-resume's move counts against the bound (the relay's move)."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    now = int(time.time())
    closed(user, ACC["B"], now + HOUR)
    refuse(user, first, resets=now + 2 * HOUR)
    assert row(task_id)["waits_until"] == now + HOUR
    set_moves(task_id, tasks.MOVES_BOUND, now=now + HOUR)
    tasks.sweep(now=now + HOUR + 1)
    held = row(task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "moves" and len(runs_of(task_id)) == 1


def test_below_the_bound_an_automatic_resume_moves_onto_the_account_whose_reset_came(client, owner, ws):
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    now = int(time.time())
    closed(user, ACC["B"], now + HOUR)
    refuse(user, first, resets=now + 2 * HOUR)
    tasks.sweep(now=now + HOUR + 1)
    assert acct(row(task_id)["lease_run"]) == ACC["B"]


@pytest.mark.parametrize("relay", [True, False])
def test_at_its_reset_a_task_whose_account_was_refused_again_is_not_left_waiting(client, owner, ws, relay):
    """At a reset: when no account opened by then (its own refused again since, its reset
    unknown), the task is not left waiting on a reset that has passed: it waits for the next reset known, or with none
    needs the owner (UNKNOWN is not AVAILABLE); with the relay off, the same for its own account alone."""
    h, user = owner
    task_id, first = started(client, h, user)
    if not relay:
        assert press(client, h, task_id, "switches", relay=False).status_code == 200
    now = int(time.time())
    refuse(user, first, resets=now + HOUR)
    assert row(task_id)["waits_until"] == now + HOUR
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"],
                           {"five_hour": {"status": "rejected", "resets_at": None, "at": now + 60}}, None, now + 60)
    tasks.sweep(now=now + HOUR + 1)
    held = row(task_id)
    assert (held["state"], held["cause"], held["waits_until"]) == ("needs_owner", "window", None)
    assert len(runs_of(task_id)) == 1


def test_a_first_crossing_made_by_auto_resume_binds_the_organisation(client, owner, ws, monkeypatch):
    """The organisation is bound at the first crossing into a Team organisation's seats, whoever makes it."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["A"])
    own(user, ACC["C"], plan="Claude Team", org="org-x", org_name="X")
    own(user, ACC["D"], plan="Claude Team", org="org-y", org_name="Y")
    task_id, first = started(client, h, user)
    now = int(time.time())
    closed(user, ACC["C"], now + HOUR)
    closed(user, ACC["D"], now + 3 * HOUR)
    refuse(user, first, resets=now + 2 * HOUR)
    tasks.sweep(now=now + HOUR + 1)
    on_c = row(task_id)["lease_run"]
    assert acct(on_c) == ACC["C"] and run(on_c)["chosen_by"] == "auto_resume"
    assert row(task_id)["bound_org"] == "org-x"


@pytest.mark.parametrize("crossing", [False, True])
def test_a_move_whose_packet_cannot_be_built_moves_nothing_and_needs_the_owner(client, owner, ws, monkeypatch,
                                                                                crossing):
    """What holds the task wins and nothing is relayed: no backend change, move,
    task.continued, task.crossed or push."""
    if crossing:
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
        own(owner[1], ACC["C"], plan="Claude Team", org="org-x", org_name="X")
    else:
        own(owner[1], ACC["B"])
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "working")
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)["state_version"],
                                                       "goal": "Push with " + SECRET})
    assert r.status_code == 200, r.text
    refuse(user, first, resets=int(time.time()) + HOUR)
    held = row(task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "secret" and held["lease_run"] is None
    assert held["backend"] == {"kind": "account", "id": ACC["A"]} and held["moves"] == []
    assert held["bound_org"] is None and len(runs_of(task_id)) == 1
    assert events(task_id, "task.continued") == [] and events(task_id, "task.crossed") == [] and ws.pushes == []


def test_a_watched_place_changed_at_a_refusal_s_end_holds_the_task_and_is_not_relayed(client, owner, ws):
    """A watched place changed holds the task whatever else ended the attempt."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    refuse(user, first, resets=int(time.time()) + HOUR,
           watch={"digest": "d" * 64, "changed": [".claude/settings.json"]})
    held = row(task_id)
    assert held["state"] == "needs_owner" and held["cause"] == "watch" and len(runs_of(task_id)) == 1


@pytest.mark.parametrize("check,relayed", [("start", False), ("backend", True)])
def test_a_start_the_workspace_could_not_class_holds_the_task_and_check_3_moves_the_relay(client, owner, ws, check,
                                                                                         relayed):
    """When the cause is unclear the task is held for the owner; the backend check is the
    account's own refusal."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": check, "found": None},
           start={"failure": None, "report": None})
    after = row(task_id)
    if relayed:
        assert after["state"] == "running" and acct(after["lease_run"]) == ACC["B"]
    else:
        assert after["state"] == "needs_owner" and after["cause"] == "resume" and len(runs_of(task_id)) == 1


def test_an_org_id_read_again_during_the_hold_is_not_the_one_confirmed(client, owner, ws):
    """A folder signed in again, during the hold, to another organisation of the same name changes its orgId:
    the hold's end finds the identity changed."""
    h, user = owner
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme")
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    report(client, h, ws, ACC["A"], plan="Claude Team", org="org-2", org_name="Acme")
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    after = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert after["state"] == "reported" and after["grp"] is None


@pytest.mark.parametrize("when", ["read again", "during its new confirmation's hold", "after that hold is cancelled",
                                  "after that hold applied"])
def test_an_attempt_carries_the_confirmed_org_id_never_one_read_since(client, owner, ws, when):
    """The identity an attempt carries is the confirmed one, which the workspace compares
    each start with, its orgId among it: a folder signed in again to another organisation of the same name, read since,
    confirmed again and still on its hold, or that hold cancelled, never changes the orgId an attempt carries or its
    account record entry names, so a start in an organisation the owner did not confirm is refused there; only the new
    confirmation's hold applying does."""
    h, user = owner
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme")
    r = confirm(client, h, user, seat["id"])
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    task_id, first = started(client, h, user, between_steps="wait")
    finish(user, first, 1, 1)
    report(client, h, ws, ACC["A"], plan="Claude Team", org="org-2", org_name="Acme")
    if when != "read again":
        r = confirm(client, h, user, seat["id"])
        assert r.status_code == 200, r.text
        if when == "after that hold is cancelled":
            assert client.post(f"/api/ade/holds/{r.json()['hold']['id']}/cancel", headers=h,
                               json={}).status_code == 200
        elif when == "after that hold applied":
            holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    expected = "org-2" if when == "after that hold applied" else "org-1"
    assert press(client, h, task_id, "continue").status_code == 200
    second = row(task_id)["lease_run"]
    assert sent_task(user, ws)["identity"]["org_id"] == expected
    record(user, second, "working")
    with store.connect() as db:
        entry = [e for e in tasks.account_records(db, user["id"])[0]["entries"] if e["run_id"] == second][0]
    assert entry["accounts"][0]["org_id"] == expected


def test_a_start_refused_in_the_organisation_a_new_confirmation_waits_on_leaves_its_hold_to_decide(client, owner,
                                                                                                      ws):
    """An attempt sent during a new confirmation's hold carries the identity confirmed before, and the workspace,
    whose folder is now in the other organisation, refuses its start: an identity change, which takes the account out
    of no relay it is in (it is in none, waiting on its hold) and leaves it held, one confirmation at a time, its hold's
    end deciding; that end confirms what the owner confirmed."""
    h, user = owner
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme")
    holds.settle(user["id"], confirm(client, h, user, seat["id"]).json()["hold"]["takes_effect_at"] + 1)
    task_id, first = started(client, h, user, between_steps="wait")
    finish(user, first, 1, 1)
    report(client, h, ws, ACC["A"], plan="Claude Team", org="org-2", org_name="Acme")
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    assert press(client, h, task_id, "continue").status_code == 200
    second = row(task_id)["lease_run"]
    reported = identity(ACC["A"], ACC["A"] + "@example.com", plan="Claude Team", org="org-2", org_name="Acme")[0]
    record(user, second, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": reported, "org_id": "org-2"})
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", "identity")
    listed = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert listed["state"] == "held" and listed["hold"]["id"] == r.json()["hold"]["id"]
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    after = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert after["state"] == "confirmed" and after["grp"] == "org:org-2"


def test_an_org_id_read_again_during_the_hold_and_back_again_is_confirmed(client, owner, ws):
    h, user = owner
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme")
    r = confirm(client, h, user, seat["id"])
    report(client, h, ws, ACC["A"], plan="Claude Team", org="org-2", org_name="Acme")
    report(client, h, ws, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme")
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    after = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert after["state"] == "confirmed" and after["grp"] == "org:org-1"


def test_a_start_whose_org_id_alone_differs_is_an_identity_change(client, owner, ws):
    """An orgId other than the confirmed one is the account's own refusal, an identity change."""
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme")
    own(user, ACC["B"], plan="Claude Team", org="org-1", org_name="Acme")
    task_id, first = started(client, h, user)
    same = {"email": ACC["A"] + "@example.com", "organization": "Acme", "plan": "Claude Team", "token_source": None,
            "api_key_source": None, "api_provider": "firstParty"}
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": same, "org_id": "org-2"})
    with store.connect() as db:
        assert tasks._owner(db, user["id"], ACC["A"])["state"] == "changed"
    assert acct(row(task_id)["lease_run"]) == ACC["B"]


def check_3(user, run_id, found, org_id, reason):
    """Check 3's refusal as the workspace makes it (task_attempt._backend_check), before any claude starts: blocked by
    the backend check, the account's own failure, the orgId `auth status` gave (none for an account signed out), and no
    report, since no claude started to give one."""
    record(user, run_id, "blocked", cause="resume", reason=reason,
           resume={"outcome": "blocked", "check": "backend", "found": found, "acks": 0},
           start={"org_id": org_id, "failure": "account"})


def owner_row(user, account):
    with store.connect() as db:
        return dict(tasks._owner(db, user["id"], account))


@pytest.mark.parametrize("confirmed,read", [("org-1", "org-2"), ("org-1", None), (None, "org-z")])
def test_check_3_finding_another_org_id_is_an_identity_change_and_the_relay_does_not_take_it_again(
        client, owner, ws, confirmed, read):
    """Check 3 reads `auth status` before any claude starts and refuses an orgId other
    than the confirmed one, none and one counting as different, with no report. That is an identity change all the
    same: the account is in no relay until it is confirmed again, its last report kept for the page, so when B,
    which the relay moved to, refuses too, A is not taken again."""
    h, user = owner
    plan, name = ("Claude Team", "Acme") if confirmed else ("Claude Max", None)
    own(user, ACC["A"], plan=plan, org=confirmed, org_name=name)
    own(user, ACC["B"], plan=plan, org=confirmed, org_name=name)
    kept = owner_row(user, ACC["A"])["report"]
    task_id, first = started(client, h, user)
    check_3(user, first, {"org_id": read, "confirmed": confirmed}, read, "the account's orgId is not the one confirmed")
    a = owner_row(user, ACC["A"])
    assert (a["state"], a["report"]) == ("changed", kept)
    second = row(task_id)["lease_run"]
    assert acct(second) == ACC["B"]
    refuse(user, second, resets=int(time.time()) + HOUR)
    waiting = row(task_id)
    assert (waiting["state"], waiting["cause"], waiting["lease_run"]) == ("suspended", "window", None)
    left = {e["account"]["account"]: e["left_out"] for e in task_of(client, h, task_id)["relay_set"]}
    assert left[ACC["A"]] == "its identity changed: confirm it again"


@pytest.mark.parametrize("confirmed", ["org-1", None])
def test_check_3_finding_the_account_signed_out_marks_it_so_and_the_relay_does_not_take_it_again(
        client, owner, ws, confirmed):
    """An account check 3 finds signed out is the account's own refusal, and is marked
    signed out; the orgId it reads then is none, which says nothing of who the account is, so a seat stays
    confirmed. When B, which the relay moved to, refuses too, A is not taken again."""
    h, user = owner
    plan, name = ("Claude Team", "Acme") if confirmed else ("Claude Max", None)
    own(user, ACC["A"], plan=plan, org=confirmed, org_name=name)
    own(user, ACC["B"], plan=plan, org=confirmed, org_name=name)
    task_id, first = started(client, h, user)
    check_3(user, first, {"signed_in": False}, None, "the account is not signed in")
    a = owner_row(user, ACC["A"])
    assert (a["state"], a["ready"]) == ("confirmed", 0)
    second = row(task_id)["lease_run"]
    assert acct(second) == ACC["B"]
    refuse(user, second, resets=int(time.time()) + HOUR)
    waiting = row(task_id)
    assert (waiting["state"], waiting["cause"], waiting["lease_run"]) == ("suspended", "window", None)
    left = {e["account"]["account"]: e["left_out"] for e in task_of(client, h, task_id)["relay_set"]}
    assert left[ACC["A"]] == "signed out"


def test_check_3_refusing_in_the_organisation_a_new_confirmation_waits_on_leaves_its_hold_to_decide(client, owner,
                                                                                                      ws):
    """The refusal of test_51's start refused in that organisation, made at check 3 as the workspace
    makes it, with no report: the account stays held with the report its new confirmation waits on, so the hold's end
    confirms what the owner confirmed."""
    h, user = owner
    seat = report(client, h, ws, ACC["A"], plan="Claude Team", org="org-1", org_name="Acme")
    holds.settle(user["id"], confirm(client, h, user, seat["id"]).json()["hold"]["takes_effect_at"] + 1)
    task_id, first = started(client, h, user, between_steps="wait")
    finish(user, first, 1, 1)
    report(client, h, ws, ACC["A"], plan="Claude Team", org="org-2", org_name="Acme")
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    assert press(client, h, task_id, "continue").status_code == 200
    check_3(user, row(task_id)["lease_run"], {"org_id": "org-2", "confirmed": "org-1"}, "org-2",
            "the account's orgId is not the one confirmed")
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", "identity")
    listed = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert listed["state"] == "held" and listed["hold"]["id"] == r.json()["hold"]["id"]
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    after = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert after["state"] == "confirmed" and after["grp"] == "org:org-2"


@pytest.mark.parametrize("state", ["offering", "handed_over", "returning"])
def test_an_answer_is_refused_while_the_task_is_with_a_teammate(client, owner, ws, state):
    """While a task is offering, handed_over or returning, none of its instructions can change."""
    h, user = owner
    task_id, first = started(client, h, user)
    finish(user, first, 1, 1, top={"question": "Which database?"})
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, state)
    r = press(client, h, task_id, "answer", text="Use the staging database.")
    assert r.status_code == 412 and "teammate" in r.json()["detail"]
    with store.connect() as db:
        assert tasks._decisions(db, task_id) == []


def test_an_answer_shaped_like_a_secret_is_refused_and_one_without_is_kept(client, owner, ws):
    """An answer cannot be changed once kept, so one shaped like a secret is refused, naming the shape and
    never the value (the remedy, edit the text, cannot reach it)."""
    h, user = owner
    task_id, first = started(client, h, user)
    finish(user, first, 1, 1, top={"question": "Which token?"})
    r = press(client, h, task_id, "answer", text="Use " + SECRET)
    assert r.status_code == 400 and "GitHub token" in r.json()["detail"] and SECRET not in r.text
    assert press(client, h, task_id, "answer", text="Use the one in the vault.").status_code == 200
    with store.connect() as db:
        assert [d["text"] for d in tasks._decisions(db, task_id)] == ["Use the one in the vault."]


def test_a_received_task_whose_answer_is_shaped_like_a_secret_is_refused(client, owner, ws):
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    fields = {"title": "Count lines", "goal": "Make the file two lines long.",
              "steps": [{"objective": "Append a line", "check": None}],
              "decisions": [{"text": "Use " + SECRET, "author": "owner"}]}
    came = {"task": "9" * 32, "offer": "8" * 32, "packet_digest": "7" * 64, "from_user": "someone"}
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(agents.AgentError) as refused:
            tasks.create(db, user["id"], agent_id, fields, continued_from=came)
    assert refused.value.status == 400 and SECRET not in str(refused.value)


def test_an_answer_confirmed_for_custody_needs_its_confirmation(client, owner, ws):
    """Answer, and confirm for custody, takes its own confirmation; without it, nothing is
    kept."""
    h, user = owner
    task_id, first = started(client, h, user)
    finish(user, first, 1, 1, top={"question": "Which database?"})
    r = press(client, h, task_id, "answer", text="Use the staging one.", for_custody=True)
    assert r.status_code in (400, 401, 403, 412) and row(task_id)["custody_digest"] is None
    with store.connect() as db:
        assert tasks._decisions(db, task_id) == []
    r = press(client, h, task_id, "answer", text="Use the staging one.", for_custody=True,
              confirmation=confirmation(user, f"task-custody-confirm:{task_id}"))
    assert r.status_code == 200 and row(task_id)["custody_digest"]


def test_cross_organisations_turned_off_during_its_hold_cancels_the_hold(client, owner, ws, monkeypatch):
    """Turning the switch off needs no version and is taken at once; a pending turning-on never takes
    effect after it."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, _ = started(client, h, user)
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    r = press(client, h, task_id, "switches", cross_groups=True,
              confirmation=confirmation(user, f"task-cross:{task_id}"))
    hold = r.json()["hold"]
    assert r.status_code == 200 and hold is not None
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    holds.settle(user["id"], hold["takes_effect_at"] + 1)
    assert row(task_id)["cross_groups"] == 0
    with store.connect() as db:
        assert db.execute("SELECT cancelled_at FROM ade_holds WHERE id = ?", (hold["id"],)).fetchone()[0] is not None
    assert [e["payload"]["on"] for e in events(task_id, "task.switched")] == [False, True, False]


def test_an_attempt_s_last_words_and_question_shaped_like_a_secret_are_not_kept_on_its_run(client, owner, ws):
    """Each model-written text is checked as it arrives from a record, and a match is stored as "removed:
    shaped like <shape>", never the value; the run's row among what stores it."""
    h, user = owner
    task_id, first = started(client, h, user)
    finish(user, first, 1, 1, top={"last_text": "Done: see " + SECRET, "question": "Is " + SECRET + " right?"})
    kept = run(first)
    assert kept["last_text"].startswith("removed: shaped like ") and SECRET not in kept["last_text"]
    assert kept["question"].startswith("removed: shaped like ") and SECRET not in kept["question"]


@pytest.mark.parametrize("state", ["working", "waiting"])
def test_a_live_attempt_s_last_words_and_question_shaped_like_a_secret_are_not_kept(client, owner, ws, state):
    """The check runs on every copy of a record, a live attempt's among them, not only at its end: a working or
    waiting attempt's question or last words shaped like a secret are never on its row, nor so on its pane."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, state, top={"last_text": "Using " + SECRET, "question": "Is " + SECRET + " right?"})
    kept = run(first)
    assert kept["state"] == state
    assert kept["last_text"].startswith("removed: shaped like ")
    assert kept["question"].startswith("removed: shaped like ")


SPLIT = "gh" + chr(0x200B) + "p_" + "q" * 36      # a token's shape split by a character that does not show


@pytest.mark.parametrize("where", ["note", "last words", "question", "target", "unknown target", "tool", "cut note",
                                   "cut target"])
def test_a_secret_s_shape_in_any_form_the_store_keeps_is_removed_and_stalls_nothing(client, owner, ws, where):
    """A model's text is checked in each form the store keeps or shows it: as it arrived, made plain
    (removing a character that does not show joins a split shape back together), and cut to its bound in bytes (which
    can end one: a checkpoint's note at 2,000, a target at 300, neither cut before). A match is kept as "removed: shaped
    like <shape>", never the value, so the whole packet's check finds nothing a model wrote, and no model can stall the
    task with End as its only remedy (the next attempt's packet is built)."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="wait")
    checkpoint, effects, top = cp(1, 1), [], {}
    push = {"call_id": "toolu_s", "tool": "Bash", "kind": "git_remote", "intent": {}, "exit": 0,
            "target": "git push https://x:" + SPLIT + "@github.com/acme/demo", "settled": "done"}
    ticket = " PP-ABCDEFGHIJ"                       # no shape whole; cut after its eighth letter, a ticket's
    if where == "note":
        checkpoint = cp(1, 1, note="see " + SPLIT)
    elif where == "last words":
        top = {"last_text": "Pushed with " + SPLIT}
    elif where == "question":
        top = {"question": "Is " + SPLIT + " the right one?"}
    elif where == "target":
        effects = [push]
    elif where == "unknown target":
        effects = [dict(push, settled=None)]
    elif where == "tool":
        effects = [dict(push, tool="gh" + "p_" + "q" * 36, kind="local", target="ls", settled="ran")]
    elif where == "cut note":
        checkpoint = cp(1, 1, note="x" * (shapes.TEXT_MAX - len(ticket) + 2) + ticket)
    else:
        effects = [dict(push, kind="local", settled="ran",
                        target="echo " + "x" * (shapes.NAME_MAX - 5 - len(ticket) + 2) + ticket)]
    record(user, first, "done", checkpoints=[checkpoint], effects=effects, top=top)
    with store.connect() as db:
        dump = "\n".join(db.iterdump())
    assert "q" * 36 not in dump and "PP-ABCDEFGH" not in dump.upper()
    if where == "unknown target":
        effect = task_of(client, h, task_id)["effects"][0]
        assert effect["target"].startswith("removed: shaped like ")
        assert press(client, h, task_id, "resolve", what="effect", effect_id=effect["id"],
                     outcome="happened").status_code == 200
    r = press(client, h, task_id, "continue")
    assert r.status_code == 200, r.text
    after = row(task_id)
    assert after["state"] == "running" and after["lease_run"] != first
    assert "q" * 36 not in packet_of(after["lease_run"])[0].decode("utf-8")


def test_the_commands_that_ran_are_kept_newest_first_within_one_record(client, owner, ws):
    """The packet keeps the commands that ran newest first, within one copy of a record too, which
    lists them in the order they ran: they share their copy's time, so the order they were stored in decides."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="wait")
    ran = [{"call_id": f"toolu_{i}", "tool": "Bash", "kind": "local", "target": f"command {i}", "intent": {},
            "settled": "ran", "exit": 0} for i in range(12)]
    finish(user, first, 1, 1, effects=ran)
    assert press(client, h, task_id, "continue").status_code == 200
    packet = json.loads(packet_of(row(task_id)["lease_run"])[0])
    assert [e["program"]["text"] for e in packet["information"]["effects"]["ran"]] == [
        f"command {i}" for i in reversed(range(12))]


def test_an_ended_attempt_s_record_read_again_is_not_late_and_one_bringing_work_is(client, owner, ws):
    """A record read again brings nothing the store has not taken, and is not late; one bringing a
    checkpoint the store has not taken from that run is."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    finish(user, first, 1, 1)
    record(user, first, "done", checkpoints=[cp(1, 1)], top={"pruned": True})
    assert run(first)["flag"] is None and events(task_id, "checkpoint.rejected") == []
    record(user, first, "done", checkpoints=[cp(1, 1), cp(2, 2)])
    assert run(first)["flag"] == tasks.LATE and len(events(task_id, "checkpoint.rejected")) == 1


def test_the_deployment_s_crossing_setting_turned_off_after_a_task_was_made_stops_its_crossing(client, owner, ws,
                                                                                                monkeypatch):
    """Either switch off, the relay set is the home group alone, the deployment's among them."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["C"], plan="Claude Team", org="org-x", org_name="X")
    task_id, first = started(client, h, user)
    assert row(task_id)["cross_groups"] == 1
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off")
    refuse(user, first, resets=int(time.time()) + HOUR)
    assert row(task_id)["state"] == "suspended" and len(runs_of(task_id)) == 1


def test_a_home_group_s_probe_comes_before_a_crossing_with_room_and_the_crossing_after_it(client, owner, ws,
                                                                                         monkeypatch):
    """A crossing account is taken after every account of the task's group, the
    group's probes among them, with the notice."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["G"])
    own(user, ACC["D"], plan="Claude Team", org="org-x", org_name="X")
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["D"], allowed(), None, now)
    task_id, first = started(client, h, user)
    refuse(user, first, resets=now + HOUR)
    on_g = row(task_id)["lease_run"]
    assert acct(on_g) == ACC["G"] and events(task_id, "task.crossed") == []
    refuse(user, on_g, resets=now + HOUR)
    assert acct(row(task_id)["lease_run"]) == ACC["D"] and len(events(task_id, "task.crossed")) == 1
    assert ws.kinds()[-1] == "task-crossed"


@pytest.mark.parametrize("cross", ["off", "on"])
def test_an_enterprise_task_relays_among_its_own_organisation_s_seats(client, owner, ws, monkeypatch, cross):
    """An Enterprise organisation's seats are a group of their own, and a task relays among them,
    whether or not crossing is on, since none crosses into or out of them."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", cross)
    h, user = owner
    own(user, ACC["E"], plan="Claude Enterprise", org="org-z", org_name="Z")
    own(user, ACC["F"], plan="Claude Enterprise", org="org-z", org_name="Z")
    task_id, first = started(client, h, user, account="E")
    refuse(user, first, resets=int(time.time()) + HOUR)
    assert acct(row(task_id)["lease_run"]) == ACC["F"]


def test_a_press_onto_another_seat_of_the_bound_organisation_leaves_its_crossings_on(client, owner, ws,
                                                                                      monkeypatch):
    """A press ends a task's crossings only onto another organisation's seats."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["C"], plan="Claude Team", org="org-x", org_name="X")
    own(user, ACC["F"], plan="Claude Team", org="org-x", org_name="X")
    task_id, first = started(client, h, user, steps=4, between_steps="wait")
    finish(user, first, 1, 1)
    assert press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["C"]}).status_code == 200
    finish(user, row(task_id)["lease_run"], 2, 2)
    assert press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    finish(user, row(task_id)["lease_run"], 3, 3)
    # from the personal plans onto another seat of the organisation it is bound to
    assert press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["F"]}).status_code == 200
    after = row(task_id)
    assert acct(after["lease_run"]) == ACC["F"] and after["bound_org"] == "org-x" and after["crossings_ended"] == 0


def test_an_end_s_reason_beyond_the_first_plane_fits_the_journal(client, owner, ws):
    """A reason the workspace's programs could write, cut so that its event fits the journal; the end is
    taken and the task list answers."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "failed", reason="\U0001F600" * 300)
    assert row(task_id)["state"] == "suspended" and row(task_id)["lease_run"] is None
    reason = events(task_id, "attempt.ended")[-1]["payload"]["reason"]
    assert reason and set(reason) == {"\U0001F600"} and len(json.dumps(reason)) <= tasks.REASON_JOURNAL_BYTES
    assert client.get(T, headers=h).status_code == 200


WORST = "\U0001F600"        # beyond the first plane: four bytes of UTF-8, twelve of JSON


def test_a_record_s_texts_and_numbers_at_their_worst_are_taken_whole(client, owner, ws, capsys):
    """Every text and number a record brings that a journal event or a table keeps is bounded, so that the
    record is taken whole: an end's cause and reason of 2,000 characters beyond the first plane, a checkpoint's note as
    long, an effect's target that JSON writes three times as long as its 300 bytes, a tool's name of 100 characters,
    and numbers past SQLite's 64 bits, which are kept as none (a checkpoint whose own number is one is not taken)."""
    h, user = owner
    task_id, first = started(client, h, user)
    huge = 1 << 70
    checkpoint = dict(cp(1, 1), record_seq=huge, made_at=huge, note={"by": "model", "text": WORST * 2000})
    effect = {"call_id": "toolu_w", "tool": "B" * 100, "kind": "git_remote", "target": chr(0xA1) * 300,
              "intent": {}, "settled": None, "exit": huge}
    limits = {"seven_day": {"status": "allowed", "resets_at": huge, "at": huge, "utilization": 1e308}}
    record(user, first, "done", cause=WORST * 2000, reason=WORST * 2000, limits=limits,
           checkpoints=[checkpoint, dict(cp(2, 1), n=huge)], effects=[effect], top={"started_at": huge})
    assert "could not be taken" not in capsys.readouterr().err
    with store.connect() as db:
        taken = db.execute("SELECT n, manifest FROM ade_agent_checkpoints WHERE task_id = ?", (task_id,)).fetchall()
        kept = db.execute("SELECT * FROM ade_agent_effects WHERE task_id = ?", (task_id,)).fetchall()
    assert [c["n"] for c in taken] == [1]
    manifest = json.loads(taken[0]["manifest"])
    assert manifest["record_seq"] is None and manifest["made_at"] is None
    assert len(kept) == 1 and kept[0]["state"] == "unknown" and kept[0]["exit_status"] is None
    ended = events(task_id, "attempt.ended")[-1]["payload"]
    assert set(ended["cause"]) == {WORST} and len(json.dumps(ended["cause"])) <= tasks.CAUSE_JOURNAL_BYTES
    assert set(ended["reason"]) == {WORST} and len(json.dumps(ended["reason"])) <= tasks.REASON_JOURNAL_BYTES
    assert events(task_id, "effect.unknown")[-1]["payload"]["target"] == chr(0xA1) * 150
    assert run(first)["started_at"] is None
    held = row(task_id)
    assert (held["state"], held["cause"], held["lease_run"]) == ("needs_owner", "effect", None)
    assert client.get(T, headers=h).status_code == 200


def test_a_resume_check_s_found_beyond_the_first_plane_fits_the_journal(client, owner, ws, capsys):
    """What the workspace found at a failed resume check is cut so that its event fits the journal: the
    record is taken, the check it names is kept, and the task says which check failed (never "start")."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "packet",
                                                           "found": WORST * 300, "acks": 1 << 70})
    assert "could not be taken" not in capsys.readouterr().err
    kept = json.loads(run(first)["resume"])
    assert (kept["outcome"], kept["check"]) == ("blocked", "packet") and set(kept["found"]) == {WORST}
    blocked = events(task_id, "resume.blocked")[-1]["payload"]
    assert blocked["check"] == "packet" and len(json.dumps(blocked["found"])) <= tasks.REASON_JOURNAL_BYTES
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", "resume") and "(packet)" in held["reason"]


@pytest.mark.parametrize("found,kept", [
    ({"found": "b" * 64, "expected": "a" * 64}, '{"expected":"' + "a" * 64 + '","found":"' + "b" * 64 + '"}'),
    ({"signed_in": False}, '{"signed_in":false}'),
    ({"error": "cannot read refs/heads/x" + chr(0x202E) + "y"}, '{"error":"cannot read refs/heads/x\\u202ey"}')])
def test_what_a_resume_check_found_as_an_object_is_kept_on_the_run_its_event_and_the_page(client, owner, ws,
                                                                                           found, kept):
    """The workspace names what a resume check found with an object
    (task_attempt._blocked: ids, digests, counts, a sign-in, an error's words), which the app kept only when it was a
    text, so the run, its resume.blocked event and the page all said none. It is kept as compact JSON, its keys sorted,
    and a character past ASCII written as its escape, so that one which turns the reading direction or hides shows."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "watch", "found": found,
                                                           "acks": 1})
    assert json.loads(run(first)["resume"])["found"] == kept
    assert events(task_id, "resume.blocked")[-1]["payload"] == {"run": first, "fence": 1, "check": "watch",
                                                                "found": kept}
    assert task_of(client, h, task_id)["attempts"][0]["resume"] == {"outcome": "blocked", "check": "watch",
                                                                    "found": kept}


def test_what_a_resume_check_found_as_a_long_object_is_cut_to_fit_its_event(client, owner, ws, capsys):
    """An object past the bound is cut as a text is (at most 300 characters and 1,200 bytes as JSON writes it), each
    character past ASCII written as its escape."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "worktree",
                                                           "found": {"error": WORST * 300}, "acks": 1})
    assert "could not be taken" not in capsys.readouterr().err
    kept = json.loads(run(first)["resume"])["found"]
    assert kept.startswith('{"error":"\\ud83d\\ude00') and len(kept) == 300
    blocked = events(task_id, "resume.blocked")[-1]["payload"]
    assert blocked["found"] == kept and len(json.dumps(kept)) <= tasks.REASON_JOURNAL_BYTES


def test_a_block_after_a_pass_replaces_it_and_a_pass_after_a_block_does_not(client, owner, ws):
    """The workspace's check passes at the frozen claude's acknowledgement and can block afterwards (the
    unfreezing restart's own start, here, which reported another account than the frozen start's); a record copied in
    between carries the pass, which the app kept for good, so the block reached neither the run, the journal nor the
    page. A block replaces a pass; a pass, from a copy of the record older than the block, never replaces a block."""
    h, user = owner
    task_id, first = started(client, h, user)
    for _ in range(2):              # two copies of the record while it works: one pass, journaled once
        record(user, first, "working", resume={"outcome": "passed", "check": None, "found": None, "acks": 1})
    found = {"reported": "the account reported at the restart is not the frozen start's"}
    blocked = {"outcome": "blocked", "check": "settings", "found": found, "acks": 1}
    record(user, first, "working", resume=blocked, start={"failure": "settings", "org_id": None, "report": None})
    record(user, first, "working", resume={"outcome": "passed", "check": None, "found": None, "acks": 1})
    record(user, first, "blocked", cause="resume", reason="the start reported " + found["reported"], resume=blocked,
           start={"failure": "settings", "org_id": None, "report": None})
    kept = '{"reported":"the account reported at the restart is not the frozen start\'s"}'
    assert json.loads(run(first)["resume"])["outcome"] == "blocked"
    assert [e["kind"] for e in events(task_id) if e["kind"].startswith("resume.")] == ["resume.passed",
                                                                                        "resume.blocked"]
    assert events(task_id, "resume.blocked")[-1]["payload"]["found"] == kept
    assert task_of(client, h, task_id)["attempts"][0]["resume"] == {"outcome": "blocked", "check": "settings",
                                                                    "found": kept}
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", "settings")


def test_an_attempt_whose_record_cannot_be_taken_holds_its_task_for_the_owner(client, owner, ws, monkeypatch,
                                                                             capsys):
    """Whatever keeps the app from taking what an attempt's record brought (take_record raising, its part
    undone), the run's own copy is marked, and its end holds the task for the owner, told to look at the work first:
    the task neither moves on as if the record brought nothing, nor runs for ever on an attempt that ended."""
    h, user = owner
    own(user, ACC["B"])                                  # an account the relay could have moved it to
    task_id, first = started(client, h, user)
    fails = [1]
    take = tasks._take_effects

    def failing_once(*args, **kwargs):
        if fails:
            fails.pop()
            raise RuntimeError("for the test")
        return take(*args, **kwargs)
    monkeypatch.setattr(tasks, "_take_effects", failing_once)
    finish(user, first, 1, 1)
    assert "a task's record could not be taken" in capsys.readouterr().err
    assert run(first)["state"] == "done" and json.loads(run(first)["trigger"])["record_lost"]
    assert client.get(T, headers=h).status_code == 200
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("needs_owner", "resume", tasks.RECORD_LOST)
    assert held["lease_run"] is None and len(runs_of(task_id)) == 1
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_checkpoints WHERE task_id = ?", (task_id,)).fetchone()[0] == 0
    assert press(client, h, task_id, "continue").status_code == 200
    assert acct(row(task_id)["lease_run"]) == ACC["A"]


def test_a_task_whose_end_cannot_be_settled_holds_no_other_back(client, owner, ws, monkeypatch):
    """Settling each running task whose attempt ended in the store, one that fails leaves the others settled
    and the page answering. The one that fails, its end raising every time, waits for the owner with why, its
    lease freed, rather than running for ever on an attempt that ended; Continue then reaches it."""
    h, user = owner
    one, first = started(client, h, user)
    other = make_task(client, h, make_agent(client, h, ACC["A"]))
    assert press(client, h, other["id"], "start").status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET state = 'stopped', ended_at = ? WHERE task_id IN (?, ?)",
                   (int(time.time()), one, other["id"]))
    ended = tasks._ended

    def failing(db, user_id, task, run, t, now, tells):
        if task["id"] == one:
            raise ValueError("for the test")
        return ended(db, user_id, task, run, t, now, tells)
    monkeypatch.setattr(tasks, "_ended", failing)
    assert client.get(T, headers=h).status_code == 200
    held = row(one)
    assert (held["state"], held["cause"], held["lease_run"]) == ("needs_owner", "resume", None)
    assert held["reason"].startswith("The app could not take this attempt's end: look at its work")
    assert row(other["id"])["state"] == "suspended"
    assert press(client, h, one, "continue").status_code == 200 and row(one)["state"] == "running"


def refused_limits(resets):
    return {"five_hour": {"status": "rejected", "resets_at": resets, "at": int(time.time())}}


@pytest.mark.parametrize("how", ["refused as ended", "answered with its end"])
def test_stop_overtaken_by_an_end_at_the_workspace_still_stops_the_task(client, owner, ws, how):
    """Stop is the owner's word whatever ended the attempt at the workspace before the press reached
    it: kept on the attempt, its end stops the task, and the relay moves nothing."""
    h, user = owner
    own(user, ACC["B"])                                  # an account the relay would move the task to
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    resets = int(time.time()) + HOUR
    if how == "refused as ended":
        def ended(run_id):
            raise workspace_client.WorkspaceCallError(400, tasks.ENDED_ALREADY)
        ws.on_stop = ended
    else:
        ws.on_stop = lambda run_id: view_of(run_id, "blocked", cause="rate_limit", limits=refused_limits(resets))
    r = client.post(f"{T}/{task_id}/stop", headers=h, json={})
    assert r.status_code == 200, r.text
    if how == "refused as ended":
        refuse(user, first, resets=resets)               # the poll copies the end the workspace made
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("suspended", "stopped", "Stopped.")
    assert held["lease_run"] is None and len(runs_of(task_id)) == 1 and ws.kinds() == []


@pytest.mark.parametrize("how", ["not running", "not answering"])
def test_a_stop_that_does_not_reach_the_workspace_says_why_and_the_end_still_stops_the_task(client, owner, ws,
                                                                                            monkeypatch, how):
    """Stop answers what kept it from the workspace (the live attempt stopped as the agent's Stop does, which says so
    too), and its mark stays on the attempt, so the end the workspace reports later stops it. The press is kept, so it
    answers 200 with `not_stopped` saying why, never 409 (only a state_version that differs) nor any refusal, which
    would tell the owner his press failed."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    if how == "not running":
        monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    else:
        def silent(run_id):
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        ws.on_stop = silent
    r = client.post(f"{T}/{task_id}/stop", headers=h, json={})
    assert r.status_code == 200, r.text
    why = "The workspace is not running: start it first." if how == "not running" else workspace_client.NOT_ANSWERING
    assert r.json()["not_stopped"] == "The attempt could not be stopped: " + why + tasks.NOT_STOPPED
    assert row(task_id)["state"] == "running" and r.json()["task"]["state"] == "running"
    refuse(user, first, resets=int(time.time()) + HOUR)
    held = row(task_id)
    assert (held["state"], held["reason"]) == ("suspended", "Stopped.") and len(runs_of(task_id)) == 1


def test_continue_on_pressed_after_stop_while_the_attempt_still_works_is_the_owner_s_later_word(client, owner, ws):
    """Stop clears what the owner asked for, so a choice recorded after it (Continue on..., which stops the attempt
    too) is his later word: the end continues on the backend asked for."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    ws.on_stop = lambda run_id: view_of(run_id, "working")      # the stop asked for, the attempt not ended yet
    assert client.post(f"{T}/{task_id}/stop", headers=h, json={}).status_code == 200
    r = press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["B"]})
    assert r.status_code == 200, r.text
    record(user, first, "stopped")
    after = row(task_id)
    assert after["state"] == "running" and acct(after["lease_run"]) == ACC["B"] and len(runs_of(task_id)) == 2


def change_goal(client, h, task_id):
    return client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)["state_version"],
                                                          "goal": "A different goal entirely."})


@pytest.mark.parametrize("how", ["stopped there", "workspace not running"])
def test_a_change_stops_an_attempt_sent_and_not_started_and_its_end_waits_for_the_owner(client, owner, ws,
                                                                                         monkeypatch, how):
    """A change cancels an attempt queued and not started, its packet naming the old goal, steps,
    protected paths and external commands. That holds for one sent and waiting at the workspace: it is stopped there,
    the change is made whatever the stop meets, and its end waits for the owner as an attempt cancelled before it was
    sent does; Continue then carries the change. When the workspace cannot take the stop, the answer says so
    (`not_stopped`), and an attempt that then runs, with the old instructions, is said to have run, not to have been
    stopped, and moves nothing on by itself (the relay neither, its account refusing it)."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    assert (run(first)["state"], run(first)["sent"]) == ("queued", 1)
    if how == "workspace not running":
        monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    r = change_goal(client, h, task_id)
    assert r.status_code == 200, r.text
    if how == "workspace not running":
        assert r.json()["not_stopped"] == ("The attempt could not be stopped: The workspace is not running: start it "
                                           "first." + tasks.NOT_STOPPED)
        assert row(task_id)["state"] == "running" and not [c for c in ws.calls if c[1].endswith("/stop")]
        refuse(user, first, resets=int(time.time()) + HOUR)      # it ran there, when the workspace was back
    else:
        assert "not_stopped" not in r.json()
        assert [c[1] for c in ws.calls if c[1].endswith("/stop")] == [f"/agents/runs/{first}/stop"]
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("suspended", "stopped") and len(runs_of(task_id)) == 1
    assert held["reason"] == (tasks.CHANGED_RAN if how == "workspace not running" else
                              "The task changed after its attempt was sent, and the attempt was stopped: press "
                              "Continue.")
    assert press(client, h, task_id, "continue").status_code == 200
    packet = json.loads(packet_of(row(task_id)["lease_run"])[0])
    assert packet["instructions"]["task"]["goal"] == "A different goal entirely."


def test_a_change_while_the_attempt_works_leaves_it_going_with_the_packet_it_has(client, owner, ws):
    """A live attempt goes on with the packet it has."""
    h, user = owner
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    assert change_goal(client, h, task_id).status_code == 200
    assert not [c for c in ws.calls if c[1].endswith("/stop")]
    assert run(first)["state"] == "working" and row(task_id)["lease_run"] == first


def silent_stop(run_id):
    """A workspace that cannot take a stop: the run goes on there, its end to come."""
    raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)


def test_an_attempt_that_ran_its_last_step_with_the_old_instructions_does_not_end_the_task(client, owner, ws):
    """A change made while the workspace cannot take the stop is kept, and says that the attempt sent before it
    could not be stopped. When that attempt then runs, with the old protected paths, and completes the task's last
    step, its step counts, but the task is not done by itself: it waits for the owner to look at what was done under
    the old instructions, then End it, or Continue, whose attempt carries the new ones."""
    h, user = owner
    task_id, first = started(client, h, user, steps=1)
    sent_task(user, ws)
    ws.on_stop = silent_stop
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)["state_version"],
                                                       "protected": ["tests/**"]})
    assert r.status_code == 200, r.text
    assert r.json()["not_stopped"].startswith("The attempt could not be stopped: ")
    finish(user, first, 1, 1)                  # it ran when the workspace answered again, with the old instructions
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("suspended", "step_done", tasks.CHANGED_RAN)
    assert task_of(client, h, task_id)["steps"][0]["state"] == "done" and not events(task_id, "task.done")
    assert press(client, h, task_id, "continue").status_code == 200
    assert sent_task(user, ws)["protected"] == ["tests/**"]
    assert press(client, h, task_id, "end", outcome="done").status_code == 412      # an attempt works again


def test_next_on_pressed_after_stop_names_the_next_backend_and_the_stop_holds(client, owner, ws):
    """Next on... names the backend of the task's next attempt without stopping the live one, and
    Stop drops what was asked before it: pressed after Stop, Next on overrides nothing. The end stops the task as Stop
    asked, and the owner's next Continue goes on the backend Next on named."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    ws.on_stop = lambda run_id: view_of(run_id, "working")      # the stop asked for, the attempt not ended yet
    assert client.post(f"{T}/{task_id}/stop", headers=h, json={}).status_code == 200
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["B"]}).status_code == 200
    record(user, first, "stopped")
    held = row(task_id)
    assert (held["state"], held["reason"]) == ("suspended", "Stopped.") and len(runs_of(task_id)) == 1
    assert held["asked"] == {"kind": "account", "id": ACC["B"]}
    assert press(client, h, task_id, "continue").status_code == 200
    assert acct(row(task_id)["lease_run"]) == ACC["B"] and row(task_id)["asked"] is None


@pytest.mark.parametrize("order", ["change, then Continue on", "Continue on, then change"])
def test_continue_on_and_a_change_to_a_sent_attempt_the_later_word_decides(client, owner, ws, order):
    """Continue on B while A works is kept: at A's end the task goes on with B. Pressed after a change
    that asked A to stop before it started, it is the owner's later word, and B's attempt is queued, carrying the
    change; a change pressed after it is the later word instead, and the task waits for Continue, which then goes on
    B. (The workspace takes neither stop here, so that A's end comes after both presses.)"""
    h, user = owner
    own(user, ACC["B"])
    b = {"kind": "account", "id": ACC["B"]}
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    ws.on_stop = silent_stop
    if order == "change, then Continue on":
        assert change_goal(client, h, task_id).status_code == 200
        assert press(client, h, task_id, "continue-on", backend=b).status_code == 200
    else:
        assert press(client, h, task_id, "continue-on", backend=b).status_code == 200
        assert change_goal(client, h, task_id).status_code == 200
    record(user, first, "stopped")
    after = row(task_id)
    if order == "change, then Continue on":
        assert after["state"] == "running" and acct(after["lease_run"]) == ACC["B"] and len(runs_of(task_id)) == 2
    else:
        assert (after["state"], after["cause"], after["asked"]) == ("suspended", "stopped", b)
        assert after["reason"] == "The task changed after its attempt was sent, and the attempt was stopped: press " \
                                  "Continue."
        assert press(client, h, task_id, "continue").status_code == 200
        assert acct(row(task_id)["lease_run"]) == ACC["B"]
    packet = json.loads(packet_of(row(task_id)["lease_run"])[0])
    assert packet["instructions"]["task"]["goal"] == "A different goal entirely."


@pytest.mark.parametrize("steps", [1, 2])
def test_an_attempt_that_completes_its_step_after_stop_counts_it(client, owner, ws, steps):
    """An attempt the owner stopped that completes its step before the stop reaches it counts the step (the
    manual: "its step counted if the attempt did it"), the last one ending the task done; otherwise the task waits,
    Stopped, on its next step, so that Continue does not run the done step again."""
    h, user = owner
    task_id, first = started(client, h, user, steps=steps)
    sent_task(user, ws)
    record(user, first, "working")
    ws.on_stop = lambda run_id: view_of(run_id, "working")
    assert client.post(f"{T}/{task_id}/stop", headers=h, json={}).status_code == 200
    finish(user, first, 1, 1)
    after = row(task_id)
    if steps == 1:
        assert after["state"] == "done" and events(task_id, "task.done")[-1]["payload"] == {"by": "checkpoint"}
    else:
        assert (after["state"], after["reason"], after["step"]) == ("suspended", "Stopped.", 2)
        assert [s["state"] for s in task_of(client, h, task_id)["steps"]] == ["done", "todo"]


def test_continue_on_while_the_workspace_cannot_be_reached_is_kept_and_says_so(client, owner, ws, monkeypatch):
    """Continue on... while the attempt works is kept, not waited for: when the workspace cannot take
    its stop, it answers 200 with `not_stopped`, the backend asked for kept, and the attempt's end goes on with it."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    r = press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["B"]})
    assert r.status_code == 200, r.text
    assert r.json()["not_stopped"] == ("The attempt could not be stopped: The workspace is not running: start it "
                                       "first." + tasks.NOT_STOPPED)
    assert r.json()["queued"] is False and row(task_id)["asked"] == {"kind": "account", "id": ACC["B"], "go": True}
    record(user, first, "stopped")
    after = row(task_id)
    assert after["state"] == "running" and acct(after["lease_run"]) == ACC["B"] and after["asked"] is None


def returning(client, h, user, steps=3, **over):
    """A task coming back from a teammate (task_offers' side faked): its first step done by the owner's own attempt, the
    rest the teammate's."""
    task_id, first = started(client, h, user, steps=steps, **over)
    finish(user, first, 1, 1)
    if row(task_id)["lease_run"]:
        record(user, row(task_id)["lease_run"], "stopped")
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "returning")
    return task_id


def take_back(user, task_id, steps, way=None, tells=None):
    report = {"commit": "9" * 40, "tree": "8" * 40, "descends": True, "protected_touched": [], "loaded_changed": [],
              "effects": [], "steps": steps}
    if way is not None:
        report["way"] = way
    with store.connect() as db:
        return tasks.take_return(db, user["id"], task_id, report, **({"tells": tells} if tells is not None else {}))


CHECKED, SAID = {"n": 2, "state": "done", "check_passed": True}, {"n": 2, "state": "done"}


@pytest.mark.parametrize("between,step,done", [("checked", CHECKED, True), ("checked", SAID, False),
                                               ("any", SAID, True), ("wait", CHECKED, True), ("wait", SAID, False)])
def test_a_return_with_every_step_done_is_done_only_when_each_came_back_checked_or_between_steps_is_any(
        client, owner, ws, between, step, done):
    """The ways back, done once the way back has found every step checked, or the owner's
    Between steps goes on after any step; otherwise it waits for the owner (a step said done is the model's word on
    another person's side)."""
    h, user = owner
    task_id = returning(client, h, user, steps=2, between_steps=between)
    take_back(user, task_id, [step])
    after = row(task_id)
    with store.connect() as db:
        evidence = db.execute("SELECT evidence FROM ade_agent_task_steps WHERE task_id = ? AND n = 2",
                              (task_id,)).fetchone()["evidence"]
    assert evidence == ("checked" if step is CHECKED else "said_done")
    if done:
        assert after["state"] == "done" and events(task_id, "task.done")[-1]["payload"] == {"by": "return"}
    else:
        assert (after["state"], after["cause"]) == ("suspended", "step_done") and not events(task_id, "task.done")
        assert after["reason"] == ("It came back from your teammate with a step said done but not checked: look, "
                                   "then End it, or Continue.")


def came_back(*hows, first=2):
    """The steps a way back reports done, from step `first` on: each checked by its own check, or only said done."""
    return [dict({"n": n, "state": "done"}, **({"check_passed": True} if how == "checked" else {}))
            for n, how in enumerate(hows, first)]


@pytest.mark.parametrize("between,hows,done", [
    ("checked", ("checked", "said"), False), ("checked", ("said", "checked"), False),
    ("checked", ("checked", "checked"), True), ("wait", ("said", "checked"), False),
    ("any", ("said", "said"), True)])
def test_a_return_of_several_steps_is_done_only_when_every_one_came_back_checked(client, owner, ws, between, hows,
                                                                                done):
    """With more than one step back, the task is done only once every step that came back was checked, or the
    owner's Between steps is any: every one, not any one. A step said done among
    checked ones holds it for the owner, wherever it is among them."""
    h, user = owner
    task_id = returning(client, h, user, steps=3, between_steps=between)
    take_back(user, task_id, came_back(*hows))
    after = row(task_id)
    assert [s["evidence"] for s in task_of(client, h, task_id)["steps"]] == [
        "checked"] + ["checked" if how == "checked" else "said_done" for how in hows]
    if done:
        assert after["state"] == "done" and events(task_id, "task.done")[-1]["payload"] == {"by": "return"}
    else:
        assert (after["state"], after["cause"]) == ("suspended", "step_done") and not events(task_id, "task.done")
        assert after["reason"] == ("It came back from your teammate with a step said done but not checked: look, "
                                   "then End it, or Continue.")


@pytest.mark.parametrize("hows,goes_on", [(("checked", "said"), False), (("said", "checked"), False),
                                          (("checked", "checked"), True)])
def test_after_custody_s_return_of_several_steps_the_relay_goes_on_only_when_every_one_was_checked(
        client, owner, ws, hows, goes_on):
    """A return (custody) of more than one step, with Between steps `checked`: the owner's relay goes on only
    when every step that came back was checked; one said done among them leaves the task waiting for him."""
    h, user = owner
    task_id = returning(client, h, user, steps=4, between_steps="checked")
    before = len(runs_of(task_id))
    tells = []
    take_back(user, task_id, came_back(*hows), way="return", tells=tells)
    after = row(task_id)
    if goes_on:
        assert after["state"] == "running" and run(after["lease_run"])["task_step"] == 4
    else:
        assert (after["state"], after["cause"]) == ("suspended", "step_done") and len(runs_of(task_id)) == before
        assert tells == []


@pytest.mark.parametrize("between,steps,goes_on", [("checked", [CHECKED], True), ("checked", [SAID], False),
                                                   ("any", [SAID], True), ("wait", [CHECKED], False),
                                                   ("wait", [], True), ("checked", [], True)])
def test_after_custody_s_return_the_owner_s_relay_goes_on_as_between_steps_allows(client, owner, ws, between,
                                                                                 steps, goes_on):
    """A return (custody) leaves the task back and the owner's relay going on as Between
    steps allows, which it asks only where a step came back done; the next attempt starts from the returned commit."""
    h, user = owner
    task_id = returning(client, h, user, between_steps=between)
    before = len(runs_of(task_id))
    tells = []
    take_back(user, task_id, steps, way="return", tells=tells)
    after = row(task_id)
    if goes_on:
        lease = after["lease_run"]
        assert after["state"] == "running" and len(runs_of(task_id)) == before + 1 and ("deliver",) in tells
        assert (run(lease)["chosen_by"], run(lease)["task_step"]) == ("next_step", 3 if steps else 2)
        assert json.loads(packet_of(lease)[0])["information"]["expect"] == {"head": "9" * 40, "tree": "8" * 40}
    else:
        assert (after["state"], after["cause"]) == ("suspended", "step_done") and len(runs_of(task_id)) == before
        assert tells == []


def test_after_a_return_the_owner_s_recorded_choice_is_used_before_the_relay(client, owner, ws):
    """As after a step of the owner's own: Between steps first, then the owner's recorded choice, used
    once."""
    h, user = owner
    own(user, ACC["B"])
    task_id = returning(client, h, user)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET asked = ? WHERE id = ?",
                   (json.dumps({"kind": "account", "id": ACC["B"]}), task_id))
    take_back(user, task_id, [CHECKED], way="return", tells=[])
    after = row(task_id)
    assert after["state"] == "running" and after["asked"] is None
    assert (acct(after["lease_run"]), run(after["lease_run"])["chosen_by"]) == (ACC["B"], "owner")


def test_any_other_way_back_waits_for_the_owner(client, owner, ws):
    """Stopped, ended, its agent retired, the teammate removed, or Give back: back, suspended."""
    h, user = owner
    task_id = returning(client, h, user)
    before = len(runs_of(task_id))
    take_back(user, task_id, [CHECKED])
    after = row(task_id)
    assert (after["state"], after["cause"]) == ("suspended", "step_done") and len(runs_of(task_id)) == before
    assert after["reason"] == "It came back from your teammate: Continue to go on."


def test_a_return_is_counted_as_a_move(client, owner, ws):
    """The bound counts the hand-offs and returns with the relay's moves."""
    h, user = owner
    task_id = returning(client, h, user)
    moved = len(row(task_id)["moves"])
    take_back(user, task_id, [])
    assert len(row(task_id)["moves"]) == moved + 1


def test_a_return_whose_relay_is_refused_keeps_what_came_back_and_waits_with_why(client, owner, ws, monkeypatch):
    """Whatever refuses the relay after a return, the return's results stand (taken in task_offers' transaction), and
    the task
    waits for the owner with why; nothing of the refused continuation is told."""
    h, user = owner
    task_id = returning(client, h, user)

    def refused(db, user_id, task, now, tells, chosen_by):
        tells.append(("continued", f"task-continued:{task['id']}:1"))
        raise agents.AgentError(409, "Refused for the test.")
    monkeypatch.setattr(tasks, "_next", refused)
    tells = []
    take_back(user, task_id, [CHECKED], way="return", tells=tells)
    after = row(task_id)
    assert (after["state"], after["cause"]) == ("suspended", "stopped") and tells == []
    assert after["reason"] == "The task could not go on: Refused for the test."
    with store.connect() as db:
        assert db.execute("SELECT evidence FROM ade_agent_task_steps WHERE task_id = ? AND n = 2",
                          (task_id,)).fetchone()["evidence"] == "checked"


def test_a_custody_task_whose_step_end_sends_it_back_queues_no_next_step(client, owner, ws, monkeypatch):
    """A return and the custodian's next step in the same copy: only the return happens."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET continued_from = ? WHERE id = ?",
                   (json.dumps({"task": secrets.token_hex(16), "offer": secrets.token_hex(16)}), task_id))
    went = []
    monkeypatch.setattr(task_offers, "on_step_end", lambda db, user_id, task, run, now: went.append(run) or True)
    finish(user, first, 1, 1)
    assert went == [first] and len(runs_of(task_id)) == 1


def next_packet(client, h, user, task_id):
    """The packet of the task's next attempt, Continue pressed on a suspended task."""
    assert press(client, h, task_id, "continue").status_code == 200
    return json.loads(packet_of(row(task_id)["lease_run"])[0])


def test_the_packet_keeps_the_newest_model_text_first_and_drops_the_oldest(client, owner, ws):
    """Model-written text "the newest first, the oldest dropped and counted": an attempt's question
    and last words, written at its end, come before an older attempt's notes, and the oldest notes are dropped."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "working", checkpoints=[cp(n, 1, kind="progress", note=f"note {n:02} " + "o" * 480)
                                                for n in range(1, 21)])
    record(user, first, "stopped")
    next_packet(client, h, user, task_id)
    second = row(task_id)["lease_run"]
    record(user, second, "stopped", top={"question": "Q" * 1530, "last_text": "L" * 1530})
    info = next_packet(client, h, user, task_id)["information"]
    assert [(q["attempt"], q["text"]) for q in info["open_questions"]] == [(2, "Q" * 1530)]
    assert (info["notes"][0]["attempt"], info["notes"][0]["text"]) == (2, "L" * 1530)
    kept = [n["text"][:7] for n in info["notes"][1:]]
    assert kept and kept == [f"note {n:02}" for n in range(20, 20 - len(kept), -1)]       # newest first
    assert info["dropped_notes"] == 20 - len(kept) > 0


def test_an_answered_question_is_open_in_no_later_packet_and_its_attempt_s_last_words_stay(client, owner, ws):
    """A build_packet that lists every ended attempt's question as open, an answered one in every later packet too,
    beside its decision and on the model's budget, fails here. A question is open until a decision answers it (answer
    keeps the run it answers): step 1's attempt asks one and nobody answers it; step 2's asks another, which the owner
    answers. The next packet has the first one open alone, the answer among the instructions, and step 2's attempt's
    last words still among the notes, newest first (each the first attempt of its step)."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="wait")
    finish(user, first, 1, 1, top={"question": "Which file?", "last_text": "Step 1 is done."})
    info = next_packet(client, h, user, task_id)["information"]
    assert [(q["attempt"], q["text"]) for q in info["open_questions"]] == [(1, "Which file?")]
    finish(user, row(task_id)["lease_run"], 2, 2, top={"question": "Which database?", "last_text": "Step 2 is done."})
    assert press(client, h, task_id, "answer", text="Use the staging database.").status_code == 200
    packet = next_packet(client, h, user, task_id)
    info = packet["information"]
    assert [(q["attempt"], q["text"]) for q in info["open_questions"]] == [(1, "Which file?")]
    assert [(n["attempt"], n["text"]) for n in info["notes"]] == [(1, "Step 2 is done."), (1, "Step 1 is done.")]
    assert [d["text"] for d in packet["instructions"]["decisions"]] == ["Use the staging database."]
    assert "Which database?" not in json.dumps(packet) and info["dropped_notes"] == 0


def test_an_effect_s_tool_named_like_a_secret_is_kept_as_removed_and_never_reaches_a_packet(client, owner, ws):
    """Each model-written text is checked as it arrives, a tool's name among them."""
    h, user = owner
    task_id, first = started(client, h, user)
    tool = "mcp__x-" + "sk-" + "ant-api03-" + "a" * 20          # the characters of a tool's name, a key's shape
    record(user, first, "stopped", effects=[{"call_id": "toolu_7", "tool": tool, "kind": "git_remote", "target": "x",
                                             "intent": {}, "settled": "done", "exit": 0}])
    with store.connect() as db:
        kept = db.execute("SELECT tool FROM ade_agent_effects WHERE task_id = ?", (task_id,)).fetchone()["tool"]
    assert kept == "removed: shaped like an Anthropic key (sk-ant-)"
    info = next_packet(client, h, user, task_id)["information"]
    assert info["effects"]["done"][0]["tool"] == kept and "api03" not in json.dumps(info)


def test_a_secret_shape_in_a_packet_that_nothing_removed_holds_the_task(client, owner, ws):
    """The whole packet is checked again before it is stored: a shape nothing removed, here an
    effect's tool written to the store directly, is never sent; the task needs the owner, the shape named."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "stopped")
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_effects (id, user_id, task_id, run_id, fence, call_id, tool, kind, target, "
                   "intent, state, exit_status, unseen, created_at, updated_at) VALUES (?,?,?,?,1,'toolu_8',?,"
                   "'git_remote','x','{}','done',0,0,?,?)",
                   (secrets.token_hex(16), user["id"], task_id, first, "mcp__x-sk-" + "ant-x", now, now))
    press(client, h, task_id, "continue")
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", "packet") and held["lease_run"] is None
    assert "an Anthropic key (sk-ant-)" in held["reason"] and len(runs_of(task_id)) == 1


def test_left_out_paths_are_cut_to_fit_and_never_hold_the_packet(client, owner, ws):
    """Only the outward effects must fit; the files a checkpoint left out are cut to a count where
    the budget ends, and their count is carried."""
    h, user = owner
    task_id, first = started(client, h, user)
    paths = [f"dir{i:02}/" + "p" * 280 for i in range(50)]
    record(user, first, "stopped", checkpoints=[cp(1, 1, kind="progress", left_out=paths)])
    info = next_packet(client, h, user, task_id)["information"]
    kept = [p["text"] for p in info["left_out"]]
    assert kept and kept == paths[:len(kept)] and info["more_left_out"] == 50 - len(kept) > 0
    assert len(tasks._canon({k: info[k] for k in OBSERVED})) <= shapes.PACKET_BUDGETS["observed"]


OBSERVED = ("steps_done", "effects", "left_out", "more_left_out", "since_checkpoint", "memory")


def test_each_packet_budget_holds_to_the_byte(client, owner, ws, monkeypatch):
    """Each budget of the packet is a bound to the byte: a part exactly at it builds, one byte past it does
    not (the instructions at entry, then the fixed part, the observations, the model's text and the whole packet)."""
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    base = tasks.instructions_size("Big", "", ["o" * 1000], [])
    goal = "g" * (tasks.INSTRUCTIONS_MAX - base)
    assert tasks.instructions_size("Big", goal, ["o" * 1000], []) == tasks.INSTRUCTIONS_MAX
    over = client.post(T, headers=h, json={"agent_id": agent_id, "title": "Big", "goal": goal + "g",
                                            "steps": [{"objective": "o" * 1000}]})
    assert over.status_code == 400
    task_id, first = started(client, h, user)
    # twelve commands that ran: their count, when none fits, takes two digits, which the must-fit part holds
    record(user, first, "stopped", checkpoints=[cp(1, 1, kind="progress", note="a note", left_out=["x/y"])],
           effects=[done_push()] + [{"call_id": f"toolu_r{i}", "tool": "Bash", "kind": "local", "target": f"make {i}",
                                     "settled": "ran", "exit": 0} for i in range(12)],
           top={"question": "Which?", "last_text": "Stopped."})

    budgets = dict(shapes.PACKET_BUDGETS)

    def build(part=None, budget=None):
        """The next packet's information, `part`'s budget set to `budget` for this build alone."""
        if part is not None:
            monkeypatch.setitem(shapes.PACKET_BUDGETS, part, budget)
        try:
            with store.connect() as db:
                data = tasks.build_packet(db, tasks.get(db, user["id"], task_id), "0" * 12, 2, 2, int(time.time()))[0]
            return json.loads(data)["information"]
        finally:
            if part is not None:
                monkeypatch.setitem(shapes.PACKET_BUDGETS, part, budgets[part])
    with store.connect() as db:
        p = json.loads(tasks.build_packet(db, tasks.get(db, user["id"], task_id), "0" * 12, 2, 2, 0)[0])
    info = p["information"]
    assert info["effects"]["ran"] and info["left_out"] and info["notes"] and info["dropped_notes"] == 0
    effects = info["effects"]
    must_fit = dict({k: info[k] for k in OBSERVED}, left_out=[], more_left_out=len(info["left_out"]),
                    effects=dict(effects, ran=[], more_ran=len(effects["ran"])))
    sizes = {"instructions": len(tasks._canon(p["instructions"])),
             "fixed": len(tasks._canon({"schema": p["schema"], "nonce": p["nonce"], "header": p["header"],
                                        "attempt": info["attempt"], "checkpoint": info["checkpoint"],
                                        "expect": info["expect"], "next": p["next"]})),
             "observed": len(tasks._canon(must_fit))}
    for part, size in sizes.items():          # exactly at it, it builds; a byte under, it is held
        assert build(part, size)["dropped_notes"] == 0
        with pytest.raises(tasks.PacketHeld):
            build(part, size - 1)
    at_must_fit = build("observed", sizes["observed"])
    assert at_must_fit["effects"]["ran"] == [] and at_must_fit["effects"]["more_ran"] == 12
    assert at_must_fit["left_out"] == [] and at_must_fit["more_left_out"] == 1
    # the lists cut to the byte: the whole observations fit exactly, and a byte less drops the last list's item
    whole = len(tasks._canon({k: info[k] for k in OBSERVED}))
    assert build("observed", whole)["left_out"] == info["left_out"]
    cut = build("observed", whole - 1)
    assert cut["left_out"] == [] and cut["more_left_out"] == 1 and cut["effects"]["ran"] == effects["ran"]
    model = len(tasks._canon({k: info[k] for k in ("open_questions", "notes", "dropped_notes")}))
    assert build("model", model)["dropped_notes"] == 0
    dropped = build("model", model - 1)
    assert dropped["dropped_notes"] == 1 and dropped["notes"][-1]["text"] == "Stopped."    # the oldest, the note
    with store.connect() as db:
        size = len(tasks.build_packet(db, tasks.get(db, user["id"], task_id), "0" * 12, 2, 2, 0)[0])
    monkeypatch.setattr(shapes, "PACKET_MAX", size)
    build()
    monkeypatch.setattr(shapes, "PACKET_MAX", size - 1)
    with pytest.raises(tasks.PacketHeld):
        build()


def test_a_checkpoint_note_shaped_like_a_secret_is_kept_as_removed(client, owner, ws):
    """A checkpoint's note is checked as it arrives, as every model-written text is."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "stopped", checkpoints=[cp(1, 1, kind="progress", note="Pushed with " + SECRET)])
    with store.connect() as db:
        manifest = json.loads(db.execute("SELECT manifest FROM ade_agent_checkpoints WHERE task_id = ?",
                                         (task_id,)).fetchone()["manifest"])
    assert manifest["note"]["text"] == "removed: shaped like a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"
    assert SECRET not in json.dumps(next_packet(client, h, user, task_id))


def test_the_agent_s_allow_rules_and_permission_settings_are_never_in_a_packet(client, owner, ws):
    """Never carried: the agent's allow rules or permission settings, which the workspace
    applies from the agent."""
    h, user = owner
    task_id, first = started(client, h, user)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET allow = ?, chat_settings = ?",
                   (json.dumps(["Bash(npm run allow-rule-marker:*)"]), json.dumps({"permission_mode": "acceptEdits"})))
    record(user, first, "stopped")
    text = json.dumps(next_packet(client, h, user, task_id))
    assert "allow-rule-marker" not in text and "acceptEdits" not in text


def test_an_open_task_s_attempts_outlive_the_runs_prune_and_its_next_attempt_starts_from_their_end(client, owner,
                                                                                                  ws):
    """`expect` is the last attempt's end state when it ended past the last checkpoint: the prune of
    runs ended 90 days ago keeps an open task's attempts, and an ended task's go as any run's do."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "working", checkpoints=[cp(1, 1, kind="progress")])
    record(user, first, "stopped", end_state={"head": "c" * 40, "tree": "d" * 40, "committed": True})
    with store.connect() as db:
        agents.forget(db, int(time.time()) + 91 * DAY)
    assert [r["run_id"] for r in runs_of(task_id)] == [first]
    assert next_packet(client, h, user, task_id)["information"]["expect"] == {"head": "c" * 40, "tree": "d" * 40}
    assert client.post(f"{T}/{task_id}/stop", headers=h, json={}).status_code == 200
    assert press(client, h, task_id, "end", outcome="stopped").status_code == 200
    with store.connect() as db:
        agents.forget(db, int(time.time()) + 91 * DAY)
    assert runs_of(task_id) == []


@pytest.mark.parametrize("newer", [True, False])
def test_a_reset_from_an_older_release_holds_only_until_a_newer_event_of_its_backend(client, owner, ws, newer):
    """A window is exhausted while the newest event for that backend said rejected: a run from an older release counts
    as its agent's account unless an event recorded since is newer."""
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, cause, ended_at, updated_at, "
                   "resets_at) VALUES (?,?,?,'blocked','rate_limit',?,?,?)",
                   ("9" * 12, agent_id, user["id"], now - 600, now - 600, now + 2 * HOUR))
        at = now - 60 if newer else now - 1200
        tasks.take_windows(db, user["id"], "account:" + ACC["A"], {"five_hour": {
            "status": "allowed", "resets_at": now + 5 * HOUR, "at": at}}, None, at)
        held = tasks.held_for_schedules(db, user["id"], ACC["A"], now)
    assert held is None if newer else held.startswith("held until")


def test_a_task_holds_at_most_200_checkpoints(client, owner, ws):
    """At most 20 checkpoints an attempt and 200 a task."""
    h, user = owner
    task_id, first = started(client, h, user)
    with store.connect() as db:
        for n in range(1, 200):
            db.execute("INSERT INTO ade_agent_checkpoints (id, user_id, task_id, step, n, kind, run_id, fence, "
                       "manifest, digest, state, created_at) VALUES (?,?,?,1,?,'progress',?,1,'{}',?,'ready',?)",
                       (secrets.token_hex(16), user["id"], task_id, n, first, "0" * 64, n))
    record(user, first, "working", checkpoints=[cp(200, 1, kind="progress"), cp(201, 1, kind="progress")])
    with store.connect() as db:
        taken = [r["n"] for r in db.execute("SELECT n FROM ade_agent_checkpoints WHERE task_id = ? AND n >= 199 "
                                            "ORDER BY n", (task_id,))]
    assert taken == [199, 200]


def test_a_reset_not_later_than_the_clock_it_arrived_at_is_no_known_reset(owner):
    """A refusal whose reset is not later than the clock when it arrived holds until the owner lifts
    it or a later event says otherwise."""
    _, user = owner
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"], {"five_hour": {
            "status": "rejected", "resets_at": now, "at": now}}, None, now)
        assert tasks.backend_state(db, user["id"], "account:" + ACC["A"], now) == (False, None)


def test_refusals_that_are_not_a_version_answer_412(client, owner, ws, monkeypatch):
    """409 is a state_version that differs ("the task changed; look again") and 412 a state that
    forbids the press, so that the page never shows a refusal of state as a changed task."""
    h, user = owner
    task_id, first = started(client, h, user)
    agent_id = row(task_id)["agent_id"]
    r = client.post(T, headers=h, json={"agent_id": agent_id, "title": "Two", "goal": "g",
                                        "steps": [{"objective": "o"}]})
    assert (r.status_code, r.json()["detail"]) == (412, "This agent has an open task already: end it first.")
    record(user, first, "stopped")
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET paused = 1")
    r = press(client, h, task_id, "continue")
    assert (r.status_code, r.json()["detail"]) == (412, "This agent is paused: resume it first.")
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET paused = 0")
    agents.retire(user, agent_id)
    r = press(client, h, task_id, "continue")
    assert (r.status_code, r.json()["detail"]) == (412, "This agent is retired.")
    r = client.post(T, headers=h, json={"agent_id": agent_id, "title": "Two", "goal": "g",
                                        "steps": [{"objective": "o"}]})
    assert (r.status_code, r.json()["detail"]) == (412, "This agent is retired.")
    account = report(client, h, ws, ACC["B"])
    subject = f"task-account-own:{account['id']}"
    for detail in (None, "It waits for its confirmation's hold already."):
        r = client.post(f"{A}/accounts/{account['id']}/confirm", headers=h,
                        json={"confirmation": confirmation(user, subject), **shown(client, h, account["id"])})
        assert r.status_code == 200 if detail is None else (r.status_code, r.json()["detail"]) == (412, detail)
    with store.connect() as db:
        confirmed = db.execute("SELECT id FROM ade_account_owners WHERE account = ?", (ACC["A"],)).fetchone()["id"]
    r = client.post(f"{A}/accounts/{confirmed}/confirm", headers=h,
                    json={"confirmation": confirmation(user, f"task-account-own:{confirmed}"),
                          **shown(client, h, confirmed)})
    assert (r.status_code, r.json()["detail"]) == (412, "It is confirmed as yours already.")
    monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    r = client.post(A + "/accounts/report", headers=h, json={"account": ACC["C"]})
    assert (r.status_code, r.json()["detail"]) == (412, "Start the workspace first: an account's identity is read "
                                                        "there.")


def test_the_attempt_message_says_whether_the_owner_lifted_the_account_s_weekly_reserve(client, owner, ws):
    """The attempt message's `reserve`, as the workspace reads it (its `reserve_lifted`): whether the owner lifted the
    weekly reserve of the attempt's account, whatever started the attempt,
    which `chosen_by` says. It once said whether the reserve held, which the workspace read the other way round: the
    end-to-end tests found the two readings apart."""
    h, user = owner
    lifted = own(user, ACC["A"])
    own(user, ACC["B"])
    r = client.put(f"{A}/accounts/{lifted}", headers=h, json={"reserve_lifted": True})
    assert r.status_code == 200, r.text
    task_id, first = started(client, h, user)
    sent = sent_task(user, ws)                  # the owner's press, on A, whose reserve the owner lifted
    assert (sent["backend"]["id"], sent["chosen_by"], sent["reserve"]) == (ACC["A"], "owner", True)
    refuse(user, first, resets=int(time.time()) + HOUR)
    sent = sent_task(user, ws)                  # the relay's, on B, whose reserve stands
    assert (sent["backend"]["id"], sent["chosen_by"], sent["reserve"]) == (ACC["B"], "relay", False)


@pytest.mark.parametrize("action,body", [("continue-on", {"backend": {"kind": "account", "id": ACC["B"]}}),
                                         ("next-on", {"backend": {"kind": "account", "id": ACC["B"]}}),
                                         ("start", {}), ("resolve", {"what": "window", "outcome": "lift"}),
                                         ("answer", {"text": "An answer."}), ("change", {"title": "Other"})])
def test_every_control_that_takes_a_version_refuses_a_stale_one(client, owner, ws, action, body):
    """A press with a state_version that differs is refused with 409 and changes nothing."""
    h, user = owner
    own(user, ACC["B"])
    task_id, _ = started(client, h, user)
    current = task_of(client, h, task_id)["state_version"]
    stale = dict(body, state_version=current - 1)
    r = client.put(f"{T}/{task_id}", headers=h, json=stale) if action == "change" else \
        client.post(f"{T}/{task_id}/{action}", headers=h, json=stale)
    assert (r.status_code, r.json()["detail"]) == (409, "The task changed; look again.")
    assert task_of(client, h, task_id)["state_version"] == current


def test_a_step_said_done_waits_for_the_owner_unless_between_steps_goes_on_after_any(client, owner, ws):
    """A task does not go past a "said done" step by itself unless the owner chose that for it."""
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    r = client.post(T, headers=h, json={"agent_id": agent_id, "title": "No checks", "goal": "Two plain steps.",
                                        "steps": [{"objective": "First"}, {"objective": "Second"}]})
    assert r.status_code == 200, r.text
    task_id = r.json()["task"]["id"]
    assert press(client, h, task_id, "start").status_code == 200
    finish(user, row(task_id)["lease_run"], 1, 1)
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("suspended", "step_done") and len(runs_of(task_id)) == 1
    assert task_of(client, h, task_id)["steps"][0]["evidence"] == "said_done"


def test_a_back_home_crossing_has_its_entry_in_the_account_record(client, owner, ws, monkeypatch, clock):
    """A relay crossing from one group to another and back home: one entry per attempt and per
    crossing (a crossing entry beside task.crossed, Back home among them)."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, reset = away_from_home(client, h, user, clock)
    clock[0] = reset + 60
    finish(user, row(task_id)["lease_run"], 1, 1)
    first, second, third = [r["run_id"] for r in runs_of(task_id)]
    assert acct(third) == ACC["A"] and events(task_id, "task.crossed")[-1]["payload"]["home"] == "room"
    record(user, third, "working")                      # an attempt's own entry is written when it starts
    assert [(e["kind"], e["run_id"]) for e in record_of(user, task_id)["entries"]] == [
        ("attempt", first), ("crossing", second), ("attempt", second), ("crossing", third), ("attempt", third)]


@pytest.mark.parametrize("setting,days", [(None, 90), ("forever", None), ("400", 400)])
def test_a_deleted_task_s_record_is_read_through_its_route_and_tells_how_long_it_is_kept(client, owner, ws,
                                                                                        monkeypatch, setting, days):
    """The record's route still names a deleted task by its id, its repository's address and its
    branch, past the journal's prune where it is kept; and the owner is told how long, as the setting says."""
    h, user = owner
    ws.repos[0].update(address=ADDRESS, slug="acme/demo")
    if setting is not None:
        monkeypatch.setenv(tasks.RECORD_DAYS_SETTING, setting)
    task_id, *_ = crossed_and_deleted(client, h, user, monkeypatch)

    def read():
        r = client.get(f"{T}/{task_id}/account-record", headers=h)
        assert r.status_code == 200, r.text
        return r.json()["record"]
    found = read()
    assert (found["task_id"], found["repo_address"], found["branch"]) == (task_id, ADDRESS, "pp/task-" + task_id)
    assert found["deleted_at"] is not None and found["record_days"] == days and found["title"] == "Count lines"
    if days != 90:
        with store.connect() as db:
            tasks.forget(db, found["deleted_at"] + 100 * DAY)
        kept = read()
        assert (kept["repo_address"], kept["branch"], kept["title"]) == (ADDRESS, "pp/task-" + task_id, None)


def test_a_task_that_needs_the_owner_is_a_standing_push(client, owner, ws):
    """Tasks.alerts gives task-needs-you through agents.alerts while the task needs the owner."""
    h, user = owner
    task_id, first = started(client, h, user)
    assert not [i for i in agents.alerts(user["id"]) if i[1] == "task-needs-you"]
    record(user, first, "blocked", cause="machine")
    assert (f"task-needs:{task_id}:worktree", "task-needs-you") in agents.alerts(user["id"])


def test_a_step_done_or_in_progress_cannot_change_and_one_not_begun_can(client, owner, ws):
    """Steps that have not begun may be changed or added; one that is done or in progress may not,
    its objective nor its check."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    finish(user, first, 1, 1)                           # step 1 done; step 2's attempt queued
    steps = [{"objective": s["objective"], "check": s["check"]} for s in task_of(client, h, task_id)["steps"]]

    def change(n, **member):
        new = [dict(s, **member) if i == n - 1 else s for i, s in enumerate(steps)]
        return client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)[
            "state_version"], "steps": new})
    for n, member in ((1, {"objective": "Changed"}), (1, {"check": "true"}), (2, {"objective": "Changed"}),
                      (2, {"check": "true"})):
        r = change(n, **member)
        assert (r.status_code, r.json()["detail"]) == (412, "A step that has begun cannot change.")
    assert change(3, objective="Changed").status_code == 200
    assert [s["objective"] for s in task_of(client, h, task_id)["steps"]] == [s["objective"] for s in steps[:2]] + [
        "Changed"]


def test_a_change_cannot_end_the_steps_before_the_step_the_task_is_at(client, owner, ws):
    """Steps 1 and 2 done and step 3 not begun, a change that removes step 3 would
    leave the task at a step that is no longer there, its next attempt step 3 of 2 with no objective, never done by
    itself: it is refused, and the task stays as it was. Changing step 3 is not refused."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3, between_steps="wait")
    finish(user, first, 1, 1)
    assert press(client, h, task_id, "continue").status_code == 200
    finish(user, row(task_id)["lease_run"], 2, 2)
    before = task_of(client, h, task_id)
    assert (before["state"], before["step"], before["steps"][2]["attempts"]) == ("suspended", 3, 0)
    steps = [{"objective": s["objective"], "check": s["check"]} for s in before["steps"]]
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": before["state_version"], "steps": steps[:2]})
    assert (r.status_code, r.json()["detail"]) == (412, "The task is at step 3, which a change cannot remove: End the "
                                                        "task instead.")
    after = task_of(client, h, task_id)
    assert after["state_version"] == before["state_version"] and len(after["steps"]) == 3
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": after["state_version"],
                                                       "steps": steps[:2] + [{"objective": "Append line 3, twice"}]})
    assert r.status_code == 200, r.text
    assert [s["objective"] for s in task_of(client, h, task_id)["steps"]][2] == "Append line 3, twice"


def test_a_change_may_remove_the_steps_after_the_one_the_task_is_at_while_it_works(client, owner, ws):
    """The other side: what a change must keep is the step the task is at, not every step. Step 1 done and step 2's
    attempt working, a change removing step 3 is kept, and step 2's end then ends the task by itself."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    finish(user, first, 1, 1)
    second = row(task_id)["lease_run"]
    record(user, second, "working")
    before = task_of(client, h, task_id)
    assert (before["state"], before["step"], before["steps"][1]["state"]) == ("running", 2, "doing")
    steps = [{"objective": s["objective"], "check": s["check"]} for s in before["steps"]]
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": before["state_version"], "steps": steps[:2]})
    assert r.status_code == 200, r.text
    assert [s["objective"] for s in task_of(client, h, task_id)["steps"]] == [s["objective"] for s in steps[:2]]
    finish(user, second, 2, 2)
    assert task_of(client, h, task_id)["state"] == "done"


def test_after_a_return_every_step_up_to_the_last_begun_one_stays_as_it_is_by_its_number(client, owner, ws):
    """A return reopens a failing step as not begun while a later one stays done
    (take_return), so the begun steps need not come first. Steps 1 and 2 done here, step 3 reopened and step 4 done by
    the return: the steps sent back unchanged are taken; a list that drops step 3, so that step 4's words come third,
    or drops step 4, is refused and step 4 stays done; step 3 cannot change, since step 4 after it has begun; and a
    step 5 can be added."""
    h, user = owner
    task_id, first = started(client, h, user, steps=4, between_steps="wait")
    finish(user, first, 1, 1)
    assert press(client, h, task_id, "continue").status_code == 200
    finish(user, row(task_id)["lease_run"], 2, 2)
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "returning")
        tasks.take_return(db, user["id"], task_id, {"commit": "9" * 40, "tree": "8" * 40, "descends": True,
                                                    "protected_touched": [], "loaded_changed": [], "effects": [],
                                                    "steps": [{"n": 3, "state": "done", "check_passed": False},
                                                              {"n": 4, "state": "done", "check_passed": True}]})
    before = task_of(client, h, task_id)
    assert (before["state"], before["step"]) == ("suspended", 3)
    assert [(s["state"], s["attempts"]) for s in before["steps"]] == [("done", 1), ("done", 1), ("todo", 0),
                                                                       ("done", 0)]
    steps = [{"objective": s["objective"], "check": s["check"]} for s in before["steps"]]
    fifth = {"objective": "Append line 5", "check": None}

    def put(new):
        return client.put(f"{T}/{task_id}", headers=h, json={
            "state_version": task_of(client, h, task_id)["state_version"], "steps": new})
    assert put(steps).status_code == 200
    for new in (steps[:2] + steps[3:] + [fifth], steps[:3]):
        r = put(new)
        assert (r.status_code, r.json()["detail"]) == (412, "A step that has begun cannot change.")
    r = put(steps[:2] + [dict(steps[2], objective="Append line 3, again")] + steps[3:])
    assert (r.status_code, r.json()["detail"]) == (412, "Step 3 cannot change, since a step after it has begun.")
    assert put(steps + [fifth]).status_code == 200
    after = task_of(client, h, task_id)
    assert [(s["objective"], s["state"]) for s in after["steps"]] == [
        (s["objective"], state) for s, state in zip(steps + [fifth], ("done", "done", "todo", "done", "todo"))]
    assert after["steps"][3]["evidence"] == "checked" and after["step"] == 3


def needs_secret(client, h, user, ws):
    task_id, first = started(client, h, user)
    record(user, first, "stopped")
    r = client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)["state_version"],
                                                       "goal": "Deploy with the token " + SECRET})
    assert r.status_code == 200, r.text
    press(client, h, task_id, "continue")
    return task_id


def needs_identity(client, h, user, ws):
    task_id, first = started(client, h, user)
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": other_report("someone@else.test"), "org_id": None})
    return task_id


def needs_moves(client, h, user, ws):
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    set_moves(task_id, tasks.MOVES_BOUND)
    refuse(user, first, resets=int(time.time()) + HOUR)
    return task_id


def needs_effect(client, h, user, ws):
    task_id, first = started(client, h, user)
    record(user, first, "stopped", effects=[{"call_id": "toolu_e", "tool": "Bash", "kind": "git_remote",
                                             "target": "git push", "settled": "unknown"}])
    return task_id


def remedy_secret(client, h, user, task_id):
    assert press(client, h, task_id, "continue").status_code == 200        # pressed again, still held: edit first
    assert row(task_id)["cause"] == "secret" and row(task_id)["lease_run"] is None
    assert client.put(f"{T}/{task_id}", headers=h, json={"state_version": task_of(client, h, task_id)[
        "state_version"], "goal": "Deploy it."}).status_code == 200
    return press(client, h, task_id, "continue")


def remedy_identity(client, h, user, task_id):
    own(user, ACC["B"])
    return press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["B"]})


def remedy_effect(client, h, user, task_id):
    assert press(client, h, task_id, "continue").status_code == 412
    effect = task_of(client, h, task_id)["effects"][0]
    assert press(client, h, task_id, "resolve", what="effect", effect_id=effect["id"],
                 outcome="not_happened").status_code == 200
    return press(client, h, task_id, "continue")


MORE_CAUSES = {"secret": (needs_secret, remedy_secret), "identity": (needs_identity, remedy_identity),
               "moves": (needs_moves, lambda client, h, user, task_id: press(client, h, task_id, "continue")),
               "effect": (needs_effect, remedy_effect)}


@pytest.mark.parametrize("cause", sorted(MORE_CAUSES))
def test_the_other_causes_need_the_owner_and_their_remedies_lead_back_to_running(client, owner, ws, cause):
    """Each cause made in turn, its remedy leading back to
    running: a secret shape in the owner's text (edit it, then Continue), an identity changed with no other account
    (Continue on another), the bound of moves (Continue), an unknown effect (Resolve, then Continue)."""
    h, user = owner
    setup, remedy = MORE_CAUSES[cause]
    task_id = setup(client, h, user, ws)
    held = task_of(client, h, task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", cause) and held["reason"]
    r = remedy(client, h, user, task_id)
    assert r.status_code == 200, r.text
    after = row(task_id)
    assert after["state"] == "running" and after["lease_run"] and (cause != "moves" or after["moves"] == [])


# ---- windows, an account's hold, a custody line, the run's packet ----------------------------------------------------

OVERAGE = {"status": "allowed", "resets_at": None, "disabled_reason": None, "using": True}


@pytest.mark.parametrize("named, events, refused", [
    ("overage", ("overage",), {"five_hour": None}),
    ("seven_day", ("seven_day", "five_hour"), {"seven_day": "reset"}),
    ("seven_day_opus", ("overage",), {"seven_day_opus": None}),
    ("seven_day_sonnet", ("five_hour", "overage"), {"seven_day_sonnet": None}),
    (None, ("five_hour", "seven_day"), {"five_hour": "reset", "seven_day": "reset"}),
])
def test_a_paid_usage_end_refuses_the_plan_s_window_it_went_past_whatever_its_events(client, owner, ws, named,
                                                                                     events, refused):
    """The plan's window a run went past onto paid usage is recorded refused, with that window's
    reset when an event gives one, else exhausted with its reset unknown: the window the view's task.overage names
    when it is a plan's window, that one alone; else each plan's window whose event
    carries the overage fields; else the five-hour one, even with no event of its own, at the latest time of the run's
    events. Schedules on the account are held, and the relay moves on."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    now = int(time.time())
    reset = now + 2 * HOUR
    limits = {w: {"status": "allowed", "resets_at": None if w == "overage" else reset, "at": now - 5 - i,
                  "overage_status": "allowed", "overage_resets_at": None, "overage_disabled_reason": None}
              for i, w in enumerate(events)}
    overage = dict(OVERAGE, window=named) if named is not None else None
    record(user, first, "blocked", cause="rate_limit", reason="paid usage", paid_usage=True, overage=overage,
           limits=limits)
    with store.connect() as db:
        rows = {r["rate_window"]: dict(r) for r in db.execute(
            "SELECT * FROM ade_backend_windows WHERE backend = ?", ("account:" + ACC["A"],)).fetchall()}
        assert {w for w, r in rows.items() if r["status"] == "rejected"} == set(refused)
        for window, kind in refused.items():
            kept = rows[window]
            assert kept["resets_at"] == (reset if kind == "reset" else None)
            assert kept["overage_status"] == "allowed"
            assert kept["at"] == (limits[window]["at"] if window in limits else now - 5)
        assert tasks.held_for_schedules(db, user["id"], ACC["A"], now + 1) is not None
    assert acct(row(task_id)["lease_run"]) == ACC["B"]


def own_run(user, agent_id, account, limits):
    """An agent's own run on `account` (not a task's attempt) that ended reporting `limits`, as the app copies it."""
    now = int(time.time())
    with store.connect() as db:
        agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (agent_id,)).fetchone()
        run_id = agents.new_run(db, agent, {"kind": "manual", "prompt": None, "at": now}, now)
    agents._apply(user["id"], [{"run_id": run_id, "agent_id": agent_id, "state": "done", "cause": None,
                                "reason": None, "started_at": now, "ended_at": now, "task": None,
                                "rate_limits": limits, "backend": {"kind": "account", "id": account}}],
                  record_seq=False)


@pytest.mark.parametrize("reset_known", [True, False])
@pytest.mark.parametrize("allowed_first", [False, True])
def test_a_paid_usage_refusal_holds_until_its_reset_or_a_lift_whatever_allowed_events_say(client, owner, ws,
                                                                                        reset_known,
                                                                                        allowed_first):
    """The plan's window an attempt went past onto paid usage is refused until its reset, or with
    its reset unknown until the owner lifts it: an event may say allowed while paid usage carries the account on, so
    neither a later event that says allowed (an attended run's on the same account) nor one already kept that is newer
    than the attempt's own events opens it before then; and a later refusal still gives it its reset."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    attended = make_agent(client, h, ACC["A"])
    now = int(time.time())
    reset = now + 2 * HOUR
    allowed_later = {"five_hour": {"status": "allowed", "resets_at": reset, "at": now + 60,
                                   "overage_status": "allowed", "overage_resets_at": None,
                                   "overage_disabled_reason": None}}
    if allowed_first:
        own_run(user, attended, ACC["A"], allowed_later)
    limits = {"five_hour": {"status": "allowed", "resets_at": reset, "at": now - 5, "overage_status": "allowed"}} \
        if reset_known else {"seven_day": {"status": "allowed", "resets_at": now + 50 * HOUR, "at": now - 5}}
    record(user, first, "blocked", cause="rate_limit", reason="paid usage", paid_usage=True,
           overage=dict(OVERAGE, window="five_hour"), limits=limits)
    assert acct(row(task_id)["lease_run"]) == ACC["B"]
    own_run(user, attended, ACC["A"], allowed_later)
    a = "account:" + ACC["A"]
    with store.connect() as db:
        assert tasks.backend_state(db, user["id"], a, now + HOUR) == (False, reset if reset_known else None)
        why = tasks.held_for_schedules(db, user["id"], ACC["A"], now + HOUR)
        if reset_known:
            assert why == "held until the plan's window resets at " + time.strftime("%H:%M UTC", time.gmtime(reset))
            assert tasks.backend_state(db, user["id"], a, reset + 1) == (True, None)
            return
        assert why == ("held: its account went past a plan's window onto paid usage, with no time it resets; it "
                       "opens when a task's Continue lifts it")
        assert tasks.held_for_schedules(db, user["id"], ACC["B"], now + HOUR) is None
    # a later refusal gives it a reset, and it stays paid usage's until then
    own_run(user, attended, ACC["A"], {"five_hour": {"status": "rejected", "resets_at": reset, "at": now + 120}})
    own_run(user, attended, ACC["A"], {"five_hour": {"status": "allowed", "resets_at": reset, "at": now + 180}})
    with store.connect() as db:
        assert tasks.backend_state(db, user["id"], a, now + HOUR) == (False, reset)
        db.execute("UPDATE ade_backend_windows SET resets_at = NULL WHERE backend = ?", (a,))
        tasks.lift(db, user["id"], a, now + 1)
        assert tasks.backend_state(db, user["id"], a, now + HOUR) == (True, None)
    # lifted, it holds no more, and the next event that says allowed is kept as any other
    own_run(user, attended, ACC["A"], {"five_hour": {"status": "allowed", "resets_at": reset, "at": now + 240}})
    with store.connect() as db:
        kept = db.execute("SELECT status, paid FROM ade_backend_windows WHERE backend = ? AND rate_window = "
                          "'five_hour'", (a,)).fetchone()
    assert (kept["status"], kept["paid"]) == ("allowed", 0)


@pytest.mark.parametrize("agent_account, named, kept", [
    (None, {"kind": "account", "id": ACC["C"]}, {ACC["C"]}),
    (None, None, {"default"}),
    (None, {"kind": "key", "provider": "anthropic"}, {"default"}),
    (None, {"kind": "account", "id": "not an account"}, {"default"}),
    (ACC["A"], {"kind": "account", "id": ACC["A"]}, {ACC["A"]}),
    (ACC["A"], None, {ACC["A"]}),
])
def test_an_agent_s_own_run_keeps_its_windows_under_the_account_its_view_names(client, owner, ws, agent_account,
                                                                               named, kept):
    """An agent's own run's view names the account it ran on at the top level (`backend`): its windows are that
    account's, so a refusal there holds that account's tasks and
    schedules. Only that account's, never default's too (never because another backend is);
    an agent with no account ran on the account new sessions start on, which the run keeps, and its own schedules are
    held by that account's windows. A view that names no account, or a key, or nothing well formed, keeps them as
    before, under the agent's account or default."""
    h, user = owner
    agent_id = make_agent(client, h, agent_account)
    now = int(time.time())
    with store.connect() as db:
        agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (agent_id,)).fetchone()
        run_id = agents.new_run(db, agent, {"kind": "manual", "prompt": None, "at": now}, now)
    view = {"run_id": run_id, "agent_id": agent_id, "state": "blocked", "cause": "rate_limit", "reason": None,
            "started_at": now, "ended_at": now, "task": None,
            "rate_limits": {"five_hour": {"status": "rejected", "resets_at": now + 3 * HOUR, "at": now}}}
    if named is not None:
        view["backend"] = named
    agents._apply(user["id"], [view], record_seq=False)
    with store.connect() as db:
        rows = db.execute("SELECT backend, status FROM ade_backend_windows WHERE user_id = ?", (user["id"],)).fetchall()
        assert sorted(r["backend"] for r in rows) == sorted("account:" + k for k in kept)
        assert {r["status"] for r in rows} == {"rejected"}
        held = {k for k in (ACC["A"], ACC["C"], "default")
                if tasks.held_for_schedules(db, user["id"], k, now + 1) is not None}
        # the agent's own schedules, asked as the scheduler asks (triggers._held): held by where its runs go
        assert tasks.held_for_schedules(db, user["id"], agent_account, now + 1, agent_id=agent_id) is not None
    assert held == kept


def test_a_task_made_on_the_account_new_sessions_start_on_keeps_it(client, owner, ws):
    """An agent with no account while C is the account new sessions start on: the task's body
    names C, and the task's backend is C, shown and recorded when it was made, its
    attempt going to C by its id, whatever new sessions start on afterwards."""
    h, user = owner
    own(user, ACC["C"])
    own(user, ACC["D"])
    task = make_task(client, h, make_agent(client, h), backend={"kind": "account", "id": ACC["C"]})
    assert task["backend"] == {"kind": "account", "id": ACC["C"]}
    assert row(task["id"])["backend"] == {"kind": "account", "id": ACC["C"]}
    press(client, h, task["id"], "start")
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send_now(user, "http://workspace.test")
    assert ws.sent[-1]["task"]["backend"] == {"kind": "account", "id": ACC["C"]}


@pytest.mark.parametrize("before", ["reported", "changed"])
def test_a_confirmation_s_hold_that_breaks_leaves_the_account_to_be_confirmed_again(client, owner, ws, monkeypatch,
                                                                                    before):
    """A confirmation's hold whose applying raises ends failed (holds), and the account goes back as a
    cancelled hold leaves it, reported, or changed once confirmed before; never held with no hold. It can be
    confirmed again."""
    h, user = owner
    account_id = report(client, h, ws, ACC["A"])["id"] if before == "reported" else own(user, ACC["A"], state=before)

    def broken(db, hold, now):
        raise RuntimeError("applying the hold broke")
    monkeypatch.setitem(holds._KINDS, tasks.HOLD_ACCOUNT, dict(holds._KINDS[tasks.HOLD_ACCOUNT], apply=broken))
    subject = f"task-account-own:{account_id}"
    r = client.post(f"{A}/accounts/{account_id}/confirm", headers=h,
                    json={"confirmation": confirmation(user, subject), **shown(client, h, account_id)})
    assert r.status_code == 200, r.text
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    listed = {a["id"]: a for a in client.get(A + "/accounts", headers=h).json()["accounts"]}
    assert (listed[account_id]["state"], listed[account_id]["hold"]) == (before, None)
    with store.connect() as db:
        assert db.execute("SELECT hold_id FROM ade_account_owners WHERE id = ?", (account_id,)).fetchone()[0] is None
    again = client.post(f"{A}/accounts/{account_id}/confirm", headers=h,
                        json={"confirmation": confirmation(user, subject), **shown(client, h, account_id)})
    assert again.status_code == 200, again.text


@pytest.mark.parametrize("crossed, cases", [(True, ["custody_cross"]), (False, []), (None, [])])
def test_a_custody_line_names_its_crossing_where_its_event_says_it_crossed(client, owner, ws, crossed, cases):
    """A custody line's cases are custody_cross where the hand-off crossed a group, which
    its event says: task_offers' task.handed_over carries `crossed`."""
    h, user = owner
    task_id, _ = started(client, h, user)
    fence = row(task_id)["fence"]
    now = int(time.time())
    offer = secrets.token_hex(16)
    payload = dict({"offer": offer, "fence": fence, "why": None}, **({"crossed": crossed} if crossed is not None
                                                                    else {}))
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_task_offers (id, user_id, task_id, kind, state, fence, created_at, "
                   "updated_at) VALUES (?,?,?,'custody','accepted',?,?,?)", (offer, user["id"], task_id, fence, now,
                                                                             now))
        tasks.journal(db, user["id"], task_id, "task.handed_over", payload, now=now)
    lines = [m for m in task_of(client, h, task_id)["moves"] if m["kind"] == "custody"]
    assert [(m["point"], m["cases"]) for m in lines] == [(12, cases)]


def test_the_run_s_pane_reads_the_packet_its_attempt_was_sent(client, owner, ws, monkeypatch):
    """The run's own pane shows the packet as the run read it, each part marked by who wrote it:
    GET /tasks/{id}/packets/{run_id} gives the packet the app kept for that attempt, with its digest; a run of another
    task's, a run with no packet, and another user answer 404."""
    h, user = owner
    task_id, first = started(client, h, user)
    got = client.get(f"{T}/{task_id}/packets/{first}", headers=h)
    assert got.status_code == 200, got.text
    body = got.json()
    with store.connect() as db:
        kept = db.execute("SELECT packet, digest FROM ade_agent_packets WHERE run_id = ?", (first,)).fetchone()
    assert body["run_id"] == first and body["digest"] == kept["digest"] == run(first)["packet_digest"]
    assert tasks._canon(body["packet"]) == bytes(kept["packet"])
    assert body["packet"]["header"] == tasks.HEADER and "Make the file two lines long." in json.dumps(body["packet"])
    other_task, other_run = started(client, h, user, account="B")
    assert client.get(f"{T}/{task_id}/packets/{other_run}", headers=h).status_code == 404
    assert client.get(f"{T}/{other_task}/packets/{other_run}", headers=h).status_code == 200
    assert client.get(f"{T}/{task_id}/packets/{'0' * 12}", headers=h).status_code == 404
    record(user, other_run, "failed", reason="its own error")
    press(client, h, other_task, "end", outcome="stopped")
    assert client.request("DELETE", f"{T}/{other_task}", headers=h, json={
        "state_version": task_of(client, h, other_task)["state_version"]}).status_code == 200
    assert client.get(f"{T}/{other_task}/packets/{other_run}", headers=h).status_code == 404     # a deleted task's
    monkeypatch.setenv("OPEN_EDITION_ADMINS", "admin@example.com,bob@example.com")   # the local provider's users
    other = signed_up(client, "bob@example.com")
    assert client.get(f"{T}/{task_id}/packets/{first}", headers=other).status_code == 404


# ---- the relay's default: off unless the deployment sets it ------------------------------------

@pytest.mark.parametrize("raw, relays", [(None, False), ("1", True), ("true", True), ("yes", True), ("on", True),
                                        (" ON ", True), ("True", True), ("0", False), ("off", False), ("", False),
                                        ("2", False), ("enabled", False)])
def test_the_relay_setting_is_on_only_for_1_true_yes_or_on(monkeypatch, raw, relays):
    """OPEN_EDITION_ADE_TASK_RELAY unset is off; 1, true, yes or on, in any case and with spaces around, turns
    it on; any other value is off, as unset is."""
    if raw is None:
        monkeypatch.delenv("OPEN_EDITION_ADE_TASK_RELAY", raising=False)
    else:
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", raw)
    assert tasks.on("relay") is relays


def test_the_relay_is_off_unless_the_deployment_sets_it(client, owner, ws, monkeypatch, clock):
    """The relay is off unless the deployment sets OPEN_EDITION_ADE_TASK_RELAY: switching subscription accounts to keep
    a task going breaks the usage policy, so moving a task between accounts is the owner's choice. This replaces the old
    default, the relay on unless turned off. Unset: the settings say
    off, a new task's Relay switch starts off, turning it on is refused naming the setting, and a refusal waits for
    its reset although B, in the task's group, could take it (it has reported no window, so the relay would try it); at
    the reset the task goes on by itself on its own account (auto-resume). Set on later: the task made while it was
    off keeps its switch off, so its next refusal waits again and moves nothing, until its owner turns the switch on,
    which the setting now allows, and its next refusal moves it to B; and a task made while the setting is on starts
    with its switch on."""
    h, user = owner
    monkeypatch.delenv("OPEN_EDITION_ADE_TASK_RELAY")
    own(user, ACC["B"])
    r = client.get(T, headers=h)
    assert r.status_code == 200 and r.json()["settings"]["relay"] is False
    task_id, first = started(client, h, user)
    assert task_of(client, h, task_id)["relay"] is False and row(task_id)["relay"] == 0
    refused = press(client, h, task_id, "switches", relay=True)
    assert refused.status_code == 403 and "OPEN_EDITION_ADE_TASK_RELAY" in refused.json()["detail"]
    reset = clock[0] + HOUR
    refuse(user, first, resets=reset)
    waiting = task_of(client, h, task_id)
    assert waiting["state"] == "suspended" and waiting["waits_until"] == reset and len(runs_of(task_id)) == 1
    assert events(task_id, "task.continued") == []
    clock[0] = reset + 1
    tasks.sweep(now=reset + 1)
    resumed = row(task_id)["lease_run"]
    assert resumed != first and acct(resumed) == ACC["A"] and run(resumed)["chosen_by"] == "auto_resume"
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "on")
    assert client.get(T, headers=h).json()["settings"]["relay"] is True
    assert task_of(client, h, task_id)["relay"] is False              # the task's own switch, written when it was made
    later = clock[0] + HOUR
    refuse(user, resumed, resets=later)
    again = task_of(client, h, task_id)
    assert again["state"] == "suspended" and again["waits_until"] == later and len(runs_of(task_id)) == 2
    # its one continuation is the resume at the reset, on its own account: nothing moved it
    assert [e["payload"]["by"] for e in events(task_id, "task.continued")] == ["auto_resume"]
    assert press(client, h, task_id, "switches", relay=True).status_code == 200
    assert task_of(client, h, task_id)["relay"] is True
    clock[0] = later + 1
    tasks.sweep(now=later + 1)
    third = row(task_id)["lease_run"]
    assert third != resumed and acct(third) == ACC["A"] and run(third)["chosen_by"] == "auto_resume"
    refuse(user, third, resets=clock[0] + HOUR)
    moved = row(task_id)["lease_run"]
    assert moved != third and run(moved)["chosen_by"] == "relay" and acct(moved) == ACC["B"]
    second_id, _ = started(client, h, user, account="C")
    assert task_of(client, h, second_id)["relay"] is True and row(second_id)["relay"] == 1


def test_the_example_deployment_names_placeholders_and_reads_as_the_task_settings_defaults(monkeypatch):
    """fly.toml, an example of a Fly deployment: its app, region and address are placeholders, and its [env], taken as
    a deployment's environment, gives the app's own readers the task settings' defaults: the relay off (unset), the
    crossing between groups off, and a deleted task's account record kept KEEP_DAYS days."""
    import pathlib
    import tomllib
    fly = pathlib.Path(tasks.__file__).resolve().parent.parent.parent / "fly.toml"
    config = tomllib.loads(fly.read_text(encoding="utf-8"))
    env = config["env"]
    assert (config["app"], config["primary_region"]) == ("<app>", "<region>")
    assert (env["OPEN_EDITION_DOMAIN"], env["OPEN_EDITION_PUBLIC_URL"]) == ("<domain>", "https://<domain>")
    assert "OPEN_EDITION_ADE_TASK_RELAY" not in env
    for variable in ("OPEN_EDITION_ADE_TASK_RELAY", "OPEN_EDITION_ADE_TASK_CROSS_GROUPS", tasks.RECORD_DAYS_SETTING):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in env.items():
        if variable.startswith("OPEN_EDITION_ADE_"):
            monkeypatch.setenv(variable, value)
    assert tasks.on("relay") is False and tasks.on("cross_groups") is False
    assert tasks.record_days() == tasks.KEEP_DAYS


# ---- received tasks gone back, the Agents view's Stop, presses dropped, organisations of both kinds ------------------

def made_received(task_id):
    """The task made a received one, as task_offers' acceptance makes it (its continued_from names the owner's task and
    the
    offer), after its start: its relay never crosses groups, and no custody offer names it."""
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET continued_from = ? WHERE id = ?",
                   (json.dumps({"task": secrets.token_hex(16), "offer": secrets.token_hex(16)}), task_id))


def mark_returned(task_id):
    """The way back's mark on the teammate's task, as task_offers._start_way_back sets it on one whose attempt works:
    returned, its state_version moved on, its state left as it is (_to_hold_as_returned holds no such task)."""
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET returned = 1, state_version = state_version + 1, updated_at = ? WHERE "
                   "id = ?", (int(time.time()), task_id))


@pytest.mark.parametrize("end", ["its step done", "refused by its account", "a backend asked for"])
def test_a_received_task_gone_back_while_its_attempt_worked_queues_nothing_at_that_attempt_s_end(client, owner, ws,
                                                                                                   end):
    """Once the teammate's task is marked returned, no next step, no relay and no auto-resume is queued for it: the mark
    set while an attempt works, that attempt's end queues no next step (its step done),
    no relay (refused by its account, B in its group with room) and no backend the user asked for (Next on B), its
    step counted if it did it; the task waits for its user as the way back holds one that runs nothing."""
    h, user = owner
    own(user, ACC["B"])
    room(user, ACC["B"])
    task_id, first = started(client, h, user, steps=3)
    made_received(task_id)
    if end == "a backend asked for":
        assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["B"]}).status_code == 200
    mark_returned(task_id)
    if end == "its step done":
        finish(user, first, 1, 1)
    elif end == "refused by its account":
        refuse(user, first, resets=int(time.time()) + HOUR)
    else:
        record(user, first, "done")                     # ended without its step: Next on's backend would go on now
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("needs_owner", "returned", task_offers.RETURNED_REASON)
    assert held["lease_run"] is None and len(runs_of(task_id)) == 1 and held["returned"] == 1
    assert held["step"] == (2 if end == "its step done" else 1)
    with store.connect() as db:
        steps = [s["state"] for s in tasks._steps(db, task_id)]
    assert steps[0] == ("done" if end == "its step done" else "doing")
    assert events(task_id, "task.suspended")[-1]["payload"] == {"state": "needs_owner", "cause": "returned",
                                                                "until": None}


def test_a_received_task_gone_back_whose_attempt_changed_a_watched_place_is_held_for_that_first(client, owner, ws):
    """What an end holds the task for comes before the way back's mark, so that its user still
    learns of a watched place their own machine's attempt changed; nothing is queued either way."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user, steps=3)
    made_received(task_id)
    mark_returned(task_id)
    finish(user, first, 1, 1, watch={"digest": "a" * 64, "changed": ["~/.ssh/config"]})
    held = row(task_id)
    assert (held["state"], held["cause"], held["lease_run"]) == ("needs_owner", "watch", None)
    assert held["step"] == 2 and len(runs_of(task_id)) == 1


def test_a_received_task_gone_back_while_its_last_step_worked_is_done_when_that_step_is(client, owner, ws):
    """The last step done by the attempt that worked when the task was marked returned ends the task done, as
    an end after a last step does; nothing is queued, and the way back leaves a done task as it is."""
    h, user = owner
    task_id, first = started(client, h, user, steps=1)
    made_received(task_id)
    mark_returned(task_id)
    finish(user, first, 1, 1)
    done = row(task_id)
    assert (done["state"], done["lease_run"], len(runs_of(task_id))) == ("done", None, 1)


def test_a_received_task_marked_returned_while_it_waits_for_a_reset_is_never_resumed_at_it(client, owner, ws):
    """Auto-resume, which is queued for no returned task: a received task waiting for its
    account's reset, marked returned with nothing else (the way back's own hold, which follows the mark on a waiting
    task, is left out, so that the sweep's guard is what this reaches), is not resumed by the sweep at that reset; it
    waits for its user, returned. Control: the same task not marked is resumed."""
    h, user = owner
    task_id, first = started(client, h, user)
    made_received(task_id)
    reset = int(time.time()) + 2 * HOUR
    refuse(user, first, resets=reset)
    waiting = row(task_id)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", reset)
    mark_returned(task_id)
    tasks.sweep(now=reset)
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("needs_owner", "returned", task_offers.RETURNED_REASON)
    assert held["lease_run"] is None and held["waits_until"] is None and len(runs_of(task_id)) == 1
    tasks.sweep(now=reset + HOUR)
    assert len(runs_of(task_id)) == 1


def agents_stop(client, h, run_id):
    """The Agents view's Stop on a run (POST /api/ade/agents/runs/{run}/stop)."""
    return client.post(f"{A}/runs/{run_id}/stop", headers=h)


def marked_by_owner(run_id):
    return "stopped_by_owner" in json.loads(run(run_id)["trigger"] or "{}")


@pytest.mark.parametrize("pressed", ["next-on", "continue-on"])
def test_the_agents_view_s_stop_on_a_task_s_attempt_is_the_owner_s_stop(client, owner, ws, pressed):
    """Stop is the owner's word, the relay's never: the Agents view's Stop on a task's attempt
    drops the backend asked for and marks the attempt stopped by its owner, as the task's own Stop does, so that its
    end leaves the task waiting for him rather than going on to B: after Next on B, and after Continue on B, whose `go`
    (on now) Stop drops too, Continue on's own stop not having reached the workspace."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user, steps=3)
    sent_task(user, ws)
    record(user, first, "working")
    if pressed == "continue-on":
        def silent(run_id):
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        ws.on_stop = silent
    r = press(client, h, task_id, pressed, backend={"kind": "account", "id": ACC["B"]})
    assert r.status_code == 200, r.text
    assert row(task_id)["asked"]["id"] == ACC["B"] and row(task_id)["lease_run"] == first
    ws.on_stop = None
    r = agents_stop(client, h, first)
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == first and "not_stopped" not in r.json()
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("suspended", "stopped", "Stopped.")
    assert held["asked"] is None and held["lease_run"] is None and len(runs_of(task_id)) == 1
    assert marked_by_owner(first)


@pytest.mark.parametrize("how", ["refused as ended", "answered with its end", "ended in the app"])
def test_the_agents_view_s_stop_overtaken_by_a_refusal_at_the_workspace_still_stops_the_task(client, owner, ws,
                                                                                             monkeypatch, how):
    """The case Stop's own comment names, from the Agents view: the attempt ended at the workspace,
    refused by its account, before the Stop reached it; the mark kept on it holds the task for the owner, and the relay
    moves nothing (B in its group). The press is kept, so it answers as the task's own Stop does, 200, with the run and
    no `not_stopped` (never a refusal): where the workspace refuses the stop as
    ended, its end copied later; where it answers with that end; and where the app's own copy of the end came between
    the mark and the stop (agents.stop_run's own 409, "This run has ended already.")."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    resets = int(time.time()) + HOUR
    if how == "refused as ended":
        def ended(run_id):
            raise workspace_client.WorkspaceCallError(400, tasks.ENDED_ALREADY)
        ws.on_stop = ended
    elif how == "answered with its end":
        ws.on_stop = lambda run_id: view_of(run_id, "blocked", cause="rate_limit", limits=refused_limits(resets))
    else:
        mark = tasks.stop_attempt

        def then_copied(user_, run_id, now=None):
            marked = mark(user_, run_id, now)
            refuse(user, first, resets=resets)          # the poll copies the end before the route asks the workspace
            return marked
        monkeypatch.setattr(tasks, "stop_attempt", then_copied)
        # the workspace is never asked: agents.stop_run refuses from the app's own copy of the end (its 409), which
        # is what this case answers 200
        ws.on_stop = lambda run_id: pytest.fail("the workspace was asked to stop a run the app had seen end")
    r = agents_stop(client, h, first)
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == first and "not_stopped" not in r.json()
    if how == "refused as ended":
        refuse(user, first, resets=resets)               # the poll copies the end the workspace made
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("suspended", "stopped", "Stopped.")
    assert held["lease_run"] is None and len(runs_of(task_id)) == 1 and ws.kinds() == []


def test_the_agents_view_s_stop_on_an_attempt_that_no_longer_holds_its_task_s_lease_changes_nothing(client, owner, ws):
    """The attempt named must still hold its task's lease, read in the transaction that marks it: on an
    earlier attempt of a task that went on to its next (its step done), the Stop marks nothing and drops nothing, and is
    answered as before (409, it ended); the live attempt and the backend the owner asked for stand, and are used."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user, steps=3)
    finish(user, first, 1, 1)
    second = row(task_id)["lease_run"]
    assert second != first
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["B"]}).status_code == 200
    r = agents_stop(client, h, first)
    assert (r.status_code, r.json()["detail"]) == (409, tasks.ENDED_ALREADY)
    after = row(task_id)
    assert (after["state"], after["lease_run"], after["asked"]["id"]) == ("running", second, ACC["B"])
    assert not marked_by_owner(first) and not marked_by_owner(second)
    finish(user, second, 2, 2)
    assert acct(row(task_id)["lease_run"]) == ACC["B"] and len(runs_of(task_id)) == 3


def test_the_agents_view_s_stop_on_another_user_s_attempt_is_404_and_changes_nothing_of_theirs(client, owner, ws,
                                                                                              monkeypatch):
    """Only the caller's own task. The mark is made before the route's check of whose run it is (agents'
    _run_row), so it is scoped by itself: another user's run id is answered 404, as before, and that user's task, the
    backend they asked for and their attempt are as they were."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["B"]}).status_code == 200
    monkeypatch.setenv("OPEN_EDITION_ADMINS", "admin@example.com,bob@example.com")   # the local provider's users
    other = signed_up(client, "bob@example.com")
    r = agents_stop(client, other, first)
    assert (r.status_code, r.json()["detail"]) == (404, "No such run.")
    after = row(task_id)
    assert (after["state"], after["lease_run"], after["asked"]["id"]) == ("running", first, ACC["B"])
    assert not marked_by_owner(first) and run(first)["state"] == "working"


@pytest.mark.parametrize("how", ["not running", "not answering"])
def test_the_agents_view_s_stop_that_does_not_reach_the_workspace_is_kept_and_says_why(client, owner, ws, monkeypatch,
                                                                                       how):
    """Once the attempt is marked, the owner's Stop stands whatever the workspace does, so a stop it cannot take
    (not running, not answering) is answered as the task's own Stop answers one: 200, the run, and why in its words
    (`not_stopped`), never a failed stop; the end that comes back later stops the task."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    if how == "not running":
        monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    else:
        def silent(run_id):
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        ws.on_stop = silent
    r = agents_stop(client, h, first)
    assert r.status_code == 200, r.text
    why = "The workspace is not running: start it first." if how == "not running" else workspace_client.NOT_ANSWERING
    assert r.json()["not_stopped"] == "The attempt could not be stopped: " + why + tasks.NOT_STOPPED
    assert (r.json()["run_id"], r.json()["state"]) == (first, "working") and marked_by_owner(first)
    assert row(task_id)["state"] == "running" and row(task_id)["lease_run"] == first
    refuse(user, first, resets=int(time.time()) + HOUR)
    held = row(task_id)
    assert (held["state"], held["reason"]) == ("suspended", "Stopped.") and len(runs_of(task_id)) == 1


def test_the_agents_view_s_stop_on_an_agent_s_own_run_is_answered_as_before(client, owner, ws, monkeypatch):
    """Where the run is no task's attempt nothing is marked, and the route answers as it did: a stop the
    workspace cannot be asked for (it is not running) is refused, 409, not answered 200 as a kept press."""
    h, user = owner
    own(user, ACC["A"])
    agent_id = make_agent(client, h, ACC["A"])
    r = client.post(f"{A}/{agent_id}/runs", headers=h, json={})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    agents._apply(user["id"], [{"run_id": run_id, "agent_id": agent_id, "state": "working"}], record_seq=False)
    assert run(run_id)["sent"] == 1 and run(run_id)["task_id"] is None
    monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    r = agents_stop(client, h, run_id)
    assert (r.status_code, r.json()) == (409, {"detail": "The workspace is not running: start it first."})


def held_as_returned(user, task_id):
    """The way back's start on the teammate's side, as task_offers._start_way_back makes it for a received task that
    waits: marked returned, and held for its user (needs_owner, cause returned)."""
    mark_returned(task_id)
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "needs_owner", cause="returned", reason=task_offers.RETURNED_REASON)


@pytest.mark.parametrize("action", ["continue", "continue-on"])
def test_continue_and_continue_on_are_refused_on_a_received_task_that_went_back(client, owner, ws, action):
    """A task received
    from a teammate, held as the way back holds one that runs nothing (marked returned, needs_owner, cause returned), is
    not continued by its user's press: Continue and Continue on are refused, 412, in RETURNED_REASON's words, and
    nothing is queued or kept. End is its one way out, and ends it."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user, steps=3)
    made_received(task_id)
    record(user, first, "stopped")
    held_as_returned(user, task_id)
    body = {"backend": {"kind": "account", "id": ACC["B"]}} if action == "continue-on" else {}
    r = press(client, h, task_id, action, **body)
    assert (r.status_code, r.json()["detail"]) == (412, task_offers.RETURNED_REASON)
    after = row(task_id)
    assert (after["state"], after["cause"], after["lease_run"], after["asked"]) == ("needs_owner", "returned", None,
                                                                                  None)
    assert len(runs_of(task_id)) == 1
    r = press(client, h, task_id, "end", outcome="stopped")
    assert r.status_code == 200, r.text
    assert row(task_id)["state"] == "stopped" and len(runs_of(task_id)) == 1


@pytest.mark.parametrize("cause", ["watch", "effect"])
def test_a_received_task_that_went_back_held_for_another_cause_is_not_continued_either(client, owner, ws, cause):
    """The refusal is the mark's, whatever the task is held for, and comes first: a received task that went
    back while its attempt worked, held at that attempt's end (the end's own hold comes first) for a watched
    place it changed, whose remedy would go on at one Continue, or for an unknown effect, whose words would ask for a
    Resolve that leads nowhere, is refused in RETURNED_REASON's words."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    made_received(task_id)
    mark_returned(task_id)
    if cause == "watch":
        finish(user, first, 1, 1, watch={"digest": "a" * 64, "changed": ["~/.ssh/config"]})
    else:
        finish(user, first, 1, 1, effects=[{"call_id": "toolu_9", "tool": "Bash", "kind": "git_remote",
                                            "target": "git push origin pp/task-x", "intent": {"before": "0" * 40},
                                            "settled": None}])
    assert (row(task_id)["state"], row(task_id)["cause"]) == ("needs_owner", cause)
    r = press(client, h, task_id, "continue")
    assert (r.status_code, r.json()["detail"]) == (412, task_offers.RETURNED_REASON)
    assert row(task_id)["state"] == "needs_owner" and len(runs_of(task_id)) == 1



@pytest.mark.parametrize("pressed", ["next-on", "continue-on"])
def test_continue_on_and_next_on_are_refused_on_a_received_task_marked_returned_while_its_attempt_works(
        client, owner, ws, pressed):
    """A Continue on or Next on
    pressed on a received task the way back marked returned while its attempt still works is refused, 412 in
    RETURNED_REASON's words, as it is while the task waits (_refuse_returned), where before it was taken, Continue on
    stopping the attempt, and that attempt's end used neither (_hold_returned). Nothing of it is kept: no press, no
    version moved, no stop sent; the attempt's end then holds the task as returned. A backend the owner cannot press
    is refused first, as on a task that waits. The same press before the mark is taken: the case "a backend asked for"
    of test_a_received_task_gone_back_while_its_attempt_worked_queues_nothing_at_that_attempt_s_end."""
    h, user = owner
    own(user, ACC["B"])
    task_id, first = started(client, h, user, steps=3)
    made_received(task_id)
    mark_returned(task_id)
    version = row(task_id)["state_version"]
    # a backend the owner cannot press is refused first, as on a task that waits: C is no confirmed account of his
    r = press(client, h, task_id, pressed, backend={"kind": "account", "id": ACC["C"]})
    assert (r.status_code, r.json()["detail"]) == (412, "Confirm that account as yours in Connections first.")
    r = press(client, h, task_id, pressed, backend={"kind": "account", "id": ACC["B"]})
    assert (r.status_code, r.json()["detail"]) == (412, task_offers.RETURNED_REASON)
    kept = row(task_id)
    assert (kept["state"], kept["asked"], kept["state_version"]) == ("running", None, version)
    assert [c[1] for c in ws.calls if c[1].endswith("/stop")] == []
    finish(user, first, 1, 1)
    held = row(task_id)
    assert (held["state"], held["cause"], held["lease_run"]) == ("needs_owner", "returned", None)
    assert len(runs_of(task_id)) == 1


def test_a_task_s_view_says_whether_the_way_back_marked_it_returned(client, owner, ws):
    """The view carries
    `returned`, the mark the way back sets on a received task, so that the page offers no
    Continue for it in any state: false for a received task not marked, true once marked, whatever it waits for, and
    false for the owner's own task held because the work that came back is to be looked at (cause `returned` too,
    never marked)."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    made_received(task_id)
    assert (task_of(client, h, task_id)["received"], task_of(client, h, task_id)["returned"]) == (True, False)
    mark_returned(task_id)
    assert task_of(client, h, task_id)["returned"] is True
    finish(user, first, 1, 1, watch={"digest": "a" * 64, "changed": ["~/.ssh/config"]})
    assert (task_of(client, h, task_id)["cause"], task_of(client, h, task_id)["returned"]) == ("watch", True)
    own_task, own_first = started(client, h, user)
    record(user, own_first, "stopped")
    with store.connect() as db:
        tasks.set_state(db, user["id"], own_task, "returning")
        tasks.take_return(db, user["id"], own_task, {"commit": "8" * 40, "descends": False, "steps": []})
    assert (task_of(client, h, own_task)["cause"], task_of(client, h, own_task)["returned"]) == ("returned", False)


@pytest.mark.parametrize("cause, pushes", [
    ("returned", False), ("window", False), ("resume", False), ("identity", False), ("moves", False),
    ("settings", False), ("secret", False), ("packet", False), ("handoff", False), ("a cause added later", False),
    ("watch", True), ("effect", True), ("ungated", True), ("worktree", True), ("branch", True)])
def test_a_received_task_marked_returned_gives_the_push_only_where_its_hold_keeps_a_remedy_of_its_own(
        client, owner, ws, cause, pushes):
    """A received task the way back marked returned, held for its user for any cause, gives "A task of
    yours needs a decision" only where its hold keeps a remedy of its own (tasks.RETURNED_OWN_REMEDY: a watched place
    to look at, its agent to resume; an unknown effect or an unseen call to say whether it happened; a worktree or a
    branch to keep or drop), not where End is all it waits for, its Continue and Continue on being refused; nor for a
    cause that list does not name, as one added later would be until it is added there. Each cause made here by
    set_state on a marked task, an unknown effect or an unseen call with one effect left unresolved (resolved, the test
    after this). The control: the same task unmarked gives it for every
    cause."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "stopped")
    made_received(task_id)
    mark_returned(task_id)
    if cause in tasks.RETURNED_WHILE_UNKNOWN:
        an_effect(user, task_id, first, unseen=cause == "ungated")
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "needs_owner", cause=cause, reason="The app's one line.")
    alert = [(f"task-needs:{task_id}:{cause}", "task-needs-you")]
    assert [i for i in agents.alerts(user["id"]) if i[1] == "task-needs-you"] == (alert if pushes else [])
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET returned = 0 WHERE id = ?", (task_id,))
    assert [i for i in agents.alerts(user["id"]) if i[1] == "task-needs-you"] == alert


def test_an_owner_s_task_held_because_the_teammate_s_work_came_back_changed_still_goes_on_at_continue(client, owner,
                                                                                                     ws):
    """The refusal reads the received task's mark, never the cause's word: the owner's own task, held because
    the work that came back does not descend from the handed commit (cause returned too, take_return), goes on at
    Continue, its remedy ("look, then Continue or End")."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "stopped")
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "returning")
        tasks.take_return(db, user["id"], task_id, {"commit": "8" * 40, "descends": False, "steps": []})
    held = row(task_id)
    assert (held["state"], held["cause"], held["returned"]) == ("needs_owner", "returned", 0)
    r = press(client, h, task_id, "continue")
    assert r.status_code == 200, r.text
    assert row(task_id)["state"] == "running" and len(runs_of(task_id)) == 2


@pytest.mark.parametrize("held", ["received, held as returned", "received and marked, held for a watched place",
                                  "the owner's own, held as returned"])
def test_the_push_of_a_decision_leaves_out_a_received_task_held_as_returned(client, owner, ws, held):
    """A task received from a teammate that
    went back to them and waits for its user as returned (marked returned, needs_owner, cause returned) gives no
    task-needs-you through tasks.alerts. One marked returned and held for a watched place its attempt's end found after
    the mark still gives it, that hold keeping a remedy of its own (every cause, made by set_state:
    test_a_received_task_marked_returned_gives_the_push_only_where_its_hold_keeps_a_remedy_of_its_own); and
    so does the owner's own task held because the work that came back is to be looked at (cause returned, never
    marked), whose remedy is Continue. Each case's cause is asserted before its push."""
    h, user = owner
    task_id, first = started(client, h, user, steps=3)
    if held == "received, held as returned":
        made_received(task_id)
        record(user, first, "stopped")
        held_as_returned(user, task_id)
    elif held == "received and marked, held for a watched place":
        made_received(task_id)
        mark_returned(task_id)
        finish(user, first, 1, 1, watch={"digest": "a" * 64, "changed": ["~/.ssh/config"]})
    else:
        record(user, first, "stopped")
        with store.connect() as db:
            tasks.set_state(db, user["id"], task_id, "returning")
            tasks.take_return(db, user["id"], task_id, {"commit": "8" * 40, "descends": False, "steps": []})
    task = row(task_id)
    cause = "watch" if held == "received and marked, held for a watched place" else "returned"
    assert (task["state"], task["cause"], task["returned"]) == (
        "needs_owner", cause, 0 if held.startswith("the owner's") else 1)
    pushes = [i for i in agents.alerts(user["id"]) if i[1] == "task-needs-you"]
    assert pushes == ([] if held == "received, held as returned"
                      else [(f"task-needs:{task_id}:{cause}", "task-needs-you")])


@pytest.mark.parametrize("move", ["onto a Team seat", "onto another organisation's Enterprise seat"])
@pytest.mark.parametrize("pressed", ["next-on", "continue-on"])
def test_a_press_naming_an_account_confirmed_into_another_group_is_dropped_and_the_end_goes_nowhere_new(
        client, owner, ws, pressed, move):
    """A press (`asked`) naming an account that is confirmed into another group is dropped: a press naming A, made while
    the task works on B (Next on A; or Continue on A, whose own stop did not reach the workspace), then A confirmed
    again into another group: from a personal plan onto a Team organisation's seat, or from one organisation's Team
    seat onto another organisation's Enterprise seat, which a drop written by the new seat's kind, or by where A left
    from, would keep. The press, made against A's group as it was, is dropped, so B's end
    uses it nowhere: after Next on the next step goes on on B, after Continue on the task waits, stopped, and no
    attempt is ever queued on A. A press naming C stays."""
    h, user = owner
    if move == "onto a Team seat":
        own(user, ACC["A"])
    else:
        own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    own(user, ACC["C"])
    task_id, first = started(client, h, user, account="B", steps=3)
    other, _ = started(client, h, user, account="B")
    assert press(client, h, other, "next-on", backend={"kind": "account", "id": ACC["C"]}).status_code == 200
    sent_task(user, ws)
    record(user, first, "working")
    if pressed == "continue-on":
        def silent(run_id):
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        ws.on_stop = silent
    assert press(client, h, task_id, pressed, backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    assert row(task_id)["asked"]["id"] == ACC["A"]
    if move == "onto a Team seat":
        _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    else:
        _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-u", org_name="You")
    assert row(task_id)["asked"] is None and row(other)["asked"]["id"] == ACC["C"]
    if pressed == "next-on":
        finish(user, first, 1, 1)
        after = row(task_id)
        assert after["state"] == "running" and acct(after["lease_run"]) == ACC["B"] and len(runs_of(task_id)) == 2
    else:
        record(user, first, "stopped")                   # the stop Continue on asked for, reaching the workspace later
        after = row(task_id)
        assert (after["state"], after["cause"], after["lease_run"]) == ("suspended", "stopped", None)
        assert len(runs_of(task_id)) == 1
    assert all(acct(r["run_id"]) != ACC["A"] for r in runs_of(task_id))


@pytest.mark.parametrize("before, after", [("Claude Team", "Claude Enterprise"), ("Claude Enterprise", "Claude Team"),
                                           ("Claude Team", "Claude Team")])
def test_a_press_naming_an_account_confirmed_again_as_another_kind_of_seat_of_its_organisation_is_dropped(
        client, owner, ws, before, after):
    """A press naming an account confirmed again as another
    kind of seat of its organisation is dropped, as a move into another group drops it (_presses_dropped, from
    _account_hold_applied), where before it stood and B's end went on to A. A press naming A, a seat of org-z, made
    while the task works on B (Next on A); then A confirmed again as the other kind of seat of org-z, either way: the
    press is dropped, so B's end uses it nowhere and the next step goes on on B, no attempt queued on A. The control,
    "Claude Team" twice: A confirmed again as the same kind of seat of the same organisation, under another address,
    which moves it into no other group and onto no other kind of seat: the press stands and B's end goes on to A."""
    h, user = owner
    own(user, ACC["A"], plan=before, org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, account="B", steps=3)
    record(user, first, "working")
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    _confirmed_again(client, h, user, ws, ACC["A"], plan=after, org="org-z", org_name="Zed",
                     email="other-" + ACC["A"] + "@example.com")
    kept = before == after
    assert (row(task_id)["asked"] or {}).get("id") == (ACC["A"] if kept else None)
    finish(user, first, 1, 1)
    moved = row(task_id)
    assert moved["state"] == "running" and len(runs_of(task_id)) == 2
    assert acct(moved["lease_run"]) == (ACC["A"] if kept else ACC["B"])
    if not kept:
        assert all(acct(r["run_id"]) != ACC["A"] for r in runs_of(task_id))


@pytest.mark.parametrize("held", ["suspended", "needs_owner"])
def test_a_waiting_task_s_recorded_choice_naming_an_account_confirmed_into_another_group_is_dropped(client, owner, ws,
                                                                                                    held):
    """The same for a press kept for the owner's next Continue, not only a running task's: Next on A
    recorded while the attempt worked; then either its agent paused before the end, so the task waits with the choice
    kept (suspended), or the end changing a watched place, which holds the task for the owner first and keeps the
    choice (needs_owner, cause watch, its agent paused with it). Then A confirmed again
    into another group: the choice is dropped, and his Continue, the agent resumed, goes on on the task's own account,
    B, not on A."""
    h, user = owner
    own(user, ACC["A"])
    task_id, first = started(client, h, user, account="B", steps=3)
    record(user, first, "working")
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    if held == "suspended":
        with store.connect() as db:
            db.execute("UPDATE ade_agents SET paused = 1")
        finish(user, first, 1, 1)
    else:
        finish(user, first, 1, 1, watch={"digest": "a" * 64, "changed": ["~/.ssh/config"]})
    assert (row(task_id)["state"], row(task_id)["asked"]["id"]) == (held, ACC["A"])
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET paused = 0")
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    assert row(task_id)["asked"] is None
    assert press(client, h, task_id, "continue").status_code == 200
    after = row(task_id)
    assert after["state"] == "running" and acct(after["lease_run"]) == ACC["B"] and len(runs_of(task_id)) == 2


def offering_with_next_on_a(client, h, user, ws, monkeypatch):
    """A task offering with Next on A recorded: pressed while its attempt worked on B, then an offer asked for, which
    task_offers' own code began at that attempt's end (after_attempt_end, the teammate track taken as on, its round left
    to the
    test): neither the end's offer branch nor the offer's start clears the press. The task's id and its offer's row."""
    ws.repos[0].update(address=ADDRESS, slug="acme/demo")          # an offer needs the task's address (_offerable)
    monkeypatch.setattr(task_offers, "enabled", lambda user: None)  # the track on, as on a hosted deployment
    monkeypatch.setattr(task_offers, "_kick", lambda: None)
    monkeypatch.setattr(holds, "tell", lambda hold, now=None, post=None: 1)
    task_id, first = started(client, h, user, account="B", steps=3)
    record(user, first, "working")
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    ask_offer(user, task_id)
    finish(user, first, 1, 1)
    with store.connect() as db:
        offer = dict(db.execute("SELECT * FROM ade_agent_task_offers WHERE task_id = ?", (task_id,)).fetchone())
    task = row(task_id)
    assert (task["state"], task["state_version"], task["asked"]["id"]) == ("offering", offer["fence"], ACC["A"])
    return task_id, offer


def test_an_offered_task_keeps_its_press_while_the_offer_is_out_and_drops_it_as_the_offer_ends(
        client, owner, ws, monkeypatch):
    """An offered task is left out of the drop while its offer is out, as _account_moved leaves it
    out of the binding: its state_version is its offer's fence, which the teammate's acceptance compares
    (task_offers.accept refuses a task not offering, or one whose state_version is not its offer's fence: "This offer
    no longer waits for you."), so a drop then would refuse it. The offer begun by task_offers' own code, A confirmed
    again into another group: the task's press and its version stand, so the acceptance's condition
    still holds, and its offer is marked (press_dropped_at). Then the offer ends, withdrawn by the owner: the press is
    dropped as it ends (task_offers._drop_marked_press),
    where before it outlived the offer, so the task waits with no choice kept and his next Continue goes on on B.
    Control: another task of the owner's, running on B with Next on A, has its press dropped by the same confirmation
    at once."""
    h, user = owner
    own(user, ACC["A"])
    task_id, offer = offering_with_next_on_a(client, h, user, ws, monkeypatch)
    other, _ = started(client, h, user, account="B")
    assert press(client, h, other, "next-on", backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    offered = row(task_id)
    assert (offered["state"], offered["state_version"], offered["asked"]["id"]) == ("offering", offer["fence"],
                                                                                    ACC["A"])
    assert row(other)["asked"] is None
    with store.connect() as db:
        marked = db.execute("SELECT press_dropped_at FROM ade_agent_task_offers WHERE id = ?",
                            (offer["id"],)).fetchone()["press_dropped_at"]
    assert marked is not None
    task_offers.withdraw(user, offer["id"])
    back = row(task_id)
    assert (back["state"], back["asked"]) == ("suspended", None)
    assert press(client, h, task_id, "continue").status_code == 200
    assert acct(row(task_id)["lease_run"]) == ACC["B"]


@pytest.mark.parametrize("when", ["handed_over", "returning"])
def test_a_handed_over_task_s_press_naming_an_account_moved_into_another_group_is_dropped_and_it_still_comes_back(
        client, owner, ws, monkeypatch, when):
    """The drop takes a task handed over or on its
    way back, as _account_moved's binding does, since nothing of a hand-off compares the owner's task's state_version
    once accepted. The task offered above, its acceptance made here by two writes (the offer row accepted, the task
    handed_over), not by task_offers.accept, which needs the teammate's workspace; then A confirmed again into another
    group while the task is handed over, or after task_offers' Take back has started the way back: its press is dropped
    and its state_version moves, and task_offers' Take back and tasks' take_return still go on. So it shows that those
    two calls compare no version, on this state."""
    h, user = owner
    own(user, ACC["A"])
    task_id, offer = offering_with_next_on_a(client, h, user, ws, monkeypatch)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_task_offers SET state = 'accepted' WHERE id = ?", (offer["id"],))
        tasks.set_state(db, user["id"], task_id, "handed_over")
    if when == "returning":
        task_offers.take_back(user, task_id)
    version = row(task_id)["state_version"]
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Team", org="org-t", org_name="Tee")
    moved = row(task_id)
    assert (moved["state"], moved["asked"]) == (when, None) and moved["state_version"] > version
    if when == "handed_over":
        task_offers.take_back(user, task_id)
    assert row(task_id)["state"] == "returning"
    take_back(user, task_id, [])
    assert row(task_id)["state"] not in ("handed_over", "returning")


@pytest.mark.parametrize("state, cause", [("blocked", "rate_limit"), ("done", None)])
def test_an_attempt_s_end_on_a_standing_record_holds_the_task_as_one_that_could_not_be_taken(client, owner, ws, state,
                                                                                             cause):
    """A record rewritten by something other than the workspace, which had no copy left to put back, arrives
    `unverified`, and the app keeps only how the run stands from it (agents._apply): its end, whatever state and cause
    the rewritten file says (a refusal by its account, B in its group with room; or done, with no step), holds the
    task for the owner as a record the app could not take holds it (RECORD_LOST), and neither the relay nor anything
    else moves it on what it says. Nor are its windows taken (take_record): its account is
    not held refused until a reset on the rewritten file's word."""
    h, user = owner
    own(user, ACC["B"])
    room(user, ACC["B"])
    task_id, first = started(client, h, user)
    sent_task(user, ws)
    record(user, first, "working")
    limits = refused_limits(int(time.time()) + HOUR) if cause == "rate_limit" else None
    record(user, first, state, cause=cause, limits=limits, top={"unverified": True})
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("needs_owner", "resume", tasks.RECORD_LOST)
    assert held["lease_run"] is None and len(runs_of(task_id)) == 1
    assert json.loads(run(first)["trigger"])["record_lost"] and run(first)["rewritten"]
    with store.connect() as db:
        kept = db.execute("SELECT status FROM ade_backend_windows WHERE user_id = ? AND backend = ?",
                          (user["id"], "account:" + acct(first))).fetchall()
    assert [w["status"] for w in kept if w["status"] == "rejected"] == []


def test_continue_on_a_task_whose_custody_hand_off_ended_holding_it_for_its_owner_runs_it_again(client, owner, ws):
    """The `handoff` cause made through the app's own modules, a custody hand-off ended by task_offers' _back with
    what ended it holding the task for its owner (needs_owner=True: a moved branch, a secret shape), and its remedy
    pressed: Continue, 200, the task running, its second attempt queued."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "stopped")
    now = int(time.time())
    with store.connect() as db:                 # as task_offers' hand-off begins and then ends it
        tasks.set_state(db, user["id"], task_id, "offering", now=now)
        task_offers._back(db, {"hold_id": "h" * 32, "user_id": user["id"], "task_id": task_id, "kind": "custody"},
                          "The task's branch moved.", now, needs_owner=True)
    held = row(task_id)
    assert (held["state"], held["cause"], held["reason"]) == ("needs_owner", "handoff", "The task's branch moved.")
    r = press(client, h, task_id, "continue")
    assert r.status_code == 200, r.text
    after = row(task_id)
    assert after["state"] == "running" and after["lease_run"] != first and len(runs_of(task_id)) == 2
    assert run(after["lease_run"])["chosen_by"] == "owner"


@pytest.mark.parametrize("first", ["team", "enterprise"])
def test_a_team_seat_s_task_relays_onto_its_organisation_s_enterprise_seat_and_crosses_nowhere_whichever_sorts_first(
        client, owner, ws, monkeypatch, first):
    """An organisation holding seats of both kinds (one
    confirmed before it moved from Team to Enterprise, one after) reads as an Enterprise one (_kind_of_group), whichever
    seat's account id sorts first, where an earlier `_kind_of_group` read it as a Team one while any seat of it was. So
    a task on its Team seat, A, crosses nowhere by itself, where before it crossed to a personal plan as a Team
    organisation's task does; and it relays among the organisation's seats as within any group, the Enterprise seat
    among them, where before each seat's own kind kept a move
    onto that seat out (_enterprise_move). A refused: the task goes onto the Enterprise seat with no crossing, never
    onto B, the personal plan with room. Either order of the two seats' ids."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    seat = ACC["G"] if first == "enterprise" else ACC["E"]          # G's id, 1s, sorts before A's; E's after
    if first == "enterprise":
        own(user, seat, plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    if first == "team":
        own(user, seat, plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["B"])
    room(user, seat, ACC["B"])
    task_id, first_run = started(client, h, user)
    refuse(user, first_run, resets=int(time.time()) + HOUR)
    moved = row(task_id)["lease_run"]
    assert moved is not None, row(task_id)["reason"]
    assert acct(moved) == seat and not events(task_id, "task.crossed")
    assert all(acct(r["run_id"]) != ACC["B"] for r in runs_of(task_id))


def test_a_crossing_onto_a_team_seat_keeps_its_case_once_an_enterprise_seat_of_its_organisation_sorting_first_joins(
        client, owner, ws, monkeypatch):
    """`_kind_of_group`, its caller for the page: a crossing's line has its case "onto_seat" where
    the group crossed into held a Team seat of the owner's (tasks.moves), read when the line is shown. A crossing across
    groups onto A, a Team seat of org-x; then G, an Enterprise seat of org-x whose account id sorts first, confirmed
    as his. org-x now reads as an Enterprise one, into which
    no relay crosses; the line says what the crossing was onto when it was made, a Team seat, and keeps its case
    (_crossed_onto_seat), as it kept it before."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id = crossing_setup(client, h, user)
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    assert acct(row(task_id)["lease_run"]) == ACC["A"]
    own(user, ACC["G"], plan="Claude Enterprise", org="org-x", org_name="X")
    lines = task_of(client, h, task_id)["moves"]
    assert [(m["kind"], m["cases"]) for m in lines][-1] == ("crossing", ["crossing", "onto_seat"])


def test_a_task_on_a_personal_plan_crosses_into_no_organisation_holding_seats_of_both_kinds(client, owner, ws,
                                                                                          monkeypatch):
    """An organisation holding seats of both kinds reads as an
    Enterprise one, into whose seats no relay crosses, its Team seat among them. A crossing across groups, with G, an
    Enterprise seat of org-x, confirmed as the owner's before the task starts: refused on B, the task relays onto C
    within its group as before; refused on C, it waits, where before it crossed onto A, org-x's Team seat, as a Team
    organisation's; and A is out of its relay set as an Enterprise organisation's seat ("org:*" stands for every Team
    organisation's seats, read by _read_kind)."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["G"], plan="Claude Enterprise", org="org-x", org_name="X")
    task_id = crossing_setup(client, h, user)
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    assert acct(row(task_id)["lease_run"]) == ACC["C"]
    refuse(user, row(task_id)["lease_run"], resets=int(time.time()) + HOUR)
    after = task_of(client, h, task_id)
    assert (after["state"], after["cause"]) == ("suspended", "window") and not events(task_id, "task.crossed")
    assert all(acct(r["run_id"]) not in (ACC["A"], ACC["G"]) for r in runs_of(task_id))
    left = {e["account"]["account"]: e["left_out"] for e in after["relay_set"]}
    assert left[ACC["A"]] == left[ACC["G"]] == "an Enterprise organisation's seats"


def test_a_task_on_no_grouped_account_is_taken_as_on_its_home_organisation_read_as_its_seats_are(
        client, owner, ws, monkeypatch):
    """`_kind_of_group`, its third caller: _on_group, for
    a task whose backend is no grouped account, reads the task's home group with this kind. The state is made here by a
    raw write, the task's account's group cleared, which no path of the app was found to make (a group is written only
    when a confirmation applies, never cleared). Its home organisation holds two Team seats
    and an Enterprise one whose account id sorts first, so it reads as an Enterprise one, and each of its seats is read
    so too (_read_kind): the task, taken as on its home
    organisation, relays among them as within any group, onto G, first in Connections' order with room. Read with
    another kind than its seats are read with (Team, as it once was, its seats then too), the task would count as
    on a seat of another kind, and no relay moves it onto or off an Enterprise seat: it would wait."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")      # G's id, 1s, sorts before A's
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    own(user, ACC["E"], plan="Claude Team", org="org-z", org_name="Zed")
    room(user, ACC["G"], ACC["E"])
    task_id, first = started(client, h, user)
    assert acct(first) == ACC["A"]
    with store.connect() as db:                 # the task's account in no group now: _on_group reads its home group
        db.execute("UPDATE ade_account_owners SET grp = NULL, grp_kind = NULL WHERE user_id = ? AND account = ?",
                   (user["id"], ACC["A"]))
    refuse(user, first, resets=int(time.time()) + HOUR)
    moved = row(task_id)["lease_run"]
    assert moved is not None, row(task_id)["reason"]
    assert acct(moved) == ACC["G"] and not events(task_id, "task.crossed")


@pytest.mark.parametrize("mixed", [False, True])
def test_a_press_onto_a_team_seat_of_an_organisation_holding_an_enterprise_seat_too_ends_the_task_s_crossings(
        client, owner, ws, mixed):
    """A press still can: a task on a personal plan,
    pressed onto C, the Team seat of an organisation that also holds an Enterprise seat of his, G, so that it reads as
    an Enterprise one, is put there as a press onto an Enterprise organisation's seats puts it (_press_onto): its
    crossings end and it is bound to that organisation. The control, the organisation holding the Team seat alone:
    bound there, its crossings going on, as a press onto a Team organisation's seat binds a task bound to none."""
    h, user = owner
    own(user, ACC["C"], plan="Claude Team", org="org-z", org_name="Zed")
    if mixed:
        own(user, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, steps=3, between_steps="wait")
    finish(user, first, 1, 1)
    assert press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["C"]}).status_code == 200
    after = row(task_id)
    assert acct(after["lease_run"]) == ACC["C"]
    assert (after["crossings_ended"], after["bound_org"]) == (1 if mixed else 0, "org-z")


def an_effect(user, task_id, run_id, unseen=False, state="unknown", call_id="toolu_9"):
    """One of the task's effects, written as an attempt's record leaves it: unknown, or settled (`state`); an unseen
    call's (`unseen`) where the gate did not see it."""
    now = int(time.time())
    effect_id = secrets.token_hex(16)
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_effects (id, user_id, task_id, run_id, fence, call_id, tool, kind, target, "
                   "intent, state, exit_status, unseen, created_at, updated_at) VALUES (?,?,?,?,1,?,'Bash',"
                   "'external','./deploy.sh','{}',?,NULL,?,?,?)",
                   (effect_id, user["id"], task_id, run_id, call_id, state, int(unseen), now, now))
    return effect_id


@pytest.mark.parametrize("cause", ["effect", "ungated"])
def test_a_received_task_marked_returned_gives_no_push_once_its_last_unknown_effect_is_resolved(client, owner, ws,
                                                                                               cause):
    """A marked task held for an unknown effect or an unseen call keeps its remedy,
    and its push, only while one of its effects is unresolved (tasks.RETURNED_WHILE_UNKNOWN). Resolving the last leaves
    it waiting for the same cause, its Continue refused, End all it waits for: no push then. Held with one unknown, the
    push; that one resolved through the route, none, the task still waiting for that cause and its Continue refused.
    The control: unmarked, the same task resolved still gives it, its Continue being its way on. Beside the unknown one,
    an effect the attempt's record settled, which no press resolved (`resolved` NULL, `state` done): it is not unknown,
    so it keeps no push (a test of `resolved` alone would pass both cases)."""
    h, user = owner
    task_id, first = started(client, h, user)
    record(user, first, "stopped")
    made_received(task_id)
    mark_returned(task_id)
    an_effect(user, task_id, first, state="done", call_id="toolu_8")
    effect_id = an_effect(user, task_id, first, unseen=cause == "ungated")
    with store.connect() as db:
        tasks.set_state(db, user["id"], task_id, "needs_owner", cause=cause, reason="The app's one line.")
    alert = [(f"task-needs:{task_id}:{cause}", "task-needs-you")]
    assert [i for i in agents.alerts(user["id"]) if i[1] == "task-needs-you"] == alert
    r = press(client, h, task_id, "resolve", what="effect", effect_id=effect_id, outcome="happened")
    assert r.status_code == 200, r.text
    held = row(task_id)
    assert (held["state"], held["cause"]) == ("needs_owner", cause)
    assert [i for i in agents.alerts(user["id"]) if i[1] == "task-needs-you"] == []
    refused = press(client, h, task_id, "continue")
    assert refused.status_code == 412, refused.text
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET returned = 0 WHERE id = ?", (task_id,))
    assert [i for i in agents.alerts(user["id"]) if i[1] == "task-needs-you"] == alert


def _b_held_back_by_its_reserve(user, days=1):
    """B, another account of the owner's, held back by its weekly reserve until a warned window resets `days` on."""
    own(user, ACC["B"])
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["B"], {
            "seven_day": {"status": "allowed_warning", "resets_at": now + days * 86400, "at": now}}, None, now)


@pytest.mark.parametrize("auto_resume", ["on", "off by the deployment's setting", "off by the task's switch"])
def test_the_relay_s_wait_counts_an_account_its_reserve_alone_holds_back_only_while_auto_resume_is_on(
        client, owner, ws, monkeypatch, auto_resume):
    """The wait counts an account of the relay set that its weekly reserve alone
    holds back for the wake auto-resume makes at its warned window's reset. With auto-resume off, the deployment's
    setting or the task's switch, nothing wakes the task there, so the relay's wait after a refusal counts no reserve's
    reset (_wait_for_reset, for the account that refused too): A refuses
    with no reset known and B is held back by its reserve for a day, so the task waits, suspended, for nothing but a
    Continue, its line saying why (tasks.HELD_BY_A_RESERVE), never needing its owner, so that custody's return and an
    offer still reach it (before, it needed him, cause window, and gave the push), where
    with auto-resume on it waits for B's reserve, its line saying that the account it waits for is held back by its
    reserve, not out of room."""
    h, user = owner
    _b_held_back_by_its_reserve(user)
    task_id, first = started(client, h, user, account="A")
    if auto_resume == "off by the deployment's setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", "0")
    elif auto_resume == "off by the task's switch":
        assert press(client, h, task_id, "switches", auto_resume=False).status_code == 200
    refuse(user, first)                                 # no reset known for A
    held = row(task_id)
    if auto_resume == "on":
        b_reserve = window_reset(user, ACC["B"], "seven_day")
        assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", b_reserve), dict(held)
        assert held["reason"] == ("Every account this task may take is out of room or held back by its weekly "
                                  f"reserve; the earliest of their resets is at {tasks._utc(b_reserve)}.")
    else:
        assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", None), dict(held)
        assert held["reason"] == HELD_BY_A_RESERVE, held["reason"]
        assert tasks.alerts(user["id"], int(time.time())) == []
    assert len(runs_of(task_id)) == 1


# the line of a task that waits for a Continue where an account its reserve alone holds back is not waited for,
# Auto-resume off
HELD_BY_A_RESERVE = ("Every account this task may take is out of room or held back by its weekly reserve, and "
                     "Auto-resume is off: press Continue when you choose.")


@pytest.mark.parametrize("relay", ["on", "off"])
@pytest.mark.parametrize("how", ["at its next step", "at its attempt's end"])
@pytest.mark.parametrize("auto_resume", ["on", "off by the deployment's setting", "off by the task's switch"])
def test_a_task_its_own_account_s_reserve_alone_holds_back_waits_for_a_continue_while_auto_resume_is_off(
        client, owner, ws, monkeypatch, relay, how, auto_resume):
    """The account the task is on, its only one, held back by its weekly
    reserve alone, its next step stopped by the reserve or its attempt ended at it. With Auto-resume off, the
    deployment's setting or the task's switch, nothing starts the task at the reserve's reset, so the task waits,
    suspended, for nothing but a Continue, its line saying why; before, the wait counted the account that refused, and
    the task waited days for a reset at which nothing took it, the page saying "Continue then" at that date. Needing its
    owner here would keep custody's return and an offer from it; it waits as a firing held past midnight with
    auto-resume off does (_held_continuation). With Auto-resume on it waits
    for the reserve's reset, as before. With the relay on and with it off."""
    h, user = owner
    task_id, first = started(client, h, user, account="H")
    if relay == "off":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "0")
    if auto_resume == "off by the deployment's setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", "0")
    elif auto_resume == "off by the task's switch":
        assert press(client, h, task_id, "switches", auto_resume=False).status_code == 200
    now = int(time.time())
    if how == "at its next step":
        near_week(user, ACC["H"])
        finish(user, first, 1, 1)
        window = "seven_day"
    else:
        record(user, first, "blocked", cause="rate_limit", reason="weekly reserve", limits={
            "seven_day_opus": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}})
        window = "seven_day_opus"
    held = row(task_id)
    pushed = (f"task-needs:{task_id}:window", "task-needs-you") in tasks.alerts(user["id"], int(time.time()))
    if auto_resume == "on":
        reset = window_reset(user, ACC["H"], window)
        assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", reset), dict(held)
        assert not pushed
    else:
        assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", None), dict(held)
        assert held["reason"] == HELD_BY_A_RESERVE, held["reason"]
        assert not pushed
    assert len(runs_of(task_id)) == 1


@pytest.mark.parametrize("auto_resume", ["on", "off"])
def test_an_identity_change_beside_an_account_its_reserve_holds_back_is_named_first(client, owner, ws,
                                                                                    auto_resume):
    """The same rule beside a changed identity: A's start finds another identity,
    so A refuses and is out of the relay set until the owner confirms it again; B is held back by its weekly reserve.
    With Auto-resume on the task waits for B's reserve. With it off it needs the owner, and the line is the identity's,
    which names the one step only the owner can take, ahead of the reserve's (_wait_for_reset's order)."""
    h, user = owner
    _b_held_back_by_its_reserve(user)
    task_id, first = started(client, h, user, account="A")
    if auto_resume == "off":
        assert press(client, h, task_id, "switches", auto_resume=False).status_code == 200
    record(user, first, "blocked", cause="resume", resume={"outcome": "blocked", "check": "start", "found": None},
           start={"failure": "account", "report": other_report("someone@else.test"), "org_id": None})
    held = row(task_id)
    if auto_resume == "on":
        b_reserve = window_reset(user, ACC["B"], "seven_day")
        assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", b_reserve), dict(held)
    else:
        assert (held["state"], held["cause"], held["waits_until"]) == ("needs_owner", "identity", None), dict(held)
        assert held["reason"] == ("Its account's identity changed and no other account can take the task: confirm it "
                                  "again in Connections, then Continue; or Continue on."), held["reason"]
    assert len(runs_of(task_id)) == 1


def test_at_a_reset_shared_by_an_account_s_own_window_and_a_reserve_the_line_names_the_reserve_too(client, owner,
                                                                                                     ws):
    """Where the earliest reset the wait counts is
    both an account's own, refused, and a weekly reserve's, at the same second, the line says the accounts are out of
    room or held back by their weekly reserve, which is true of both, where it said they were all out of room, which B,
    held back by its reserve, is not."""
    h, user = owner
    _b_held_back_by_its_reserve(user)
    task_id, first = started(client, h, user, account="A")
    b_reserve = window_reset(user, ACC["B"], "seven_day")
    refuse(user, first, resets=b_reserve)
    held = row(task_id)
    assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", b_reserve), dict(held)
    assert held["reason"] == ("Every account this task may take is out of room or held back by its weekly reserve; the "
                              f"earliest of their resets is at {tasks._utc(b_reserve)}.")


def test_an_account_refused_while_its_weekly_warning_stands_gives_its_refused_reset_with_auto_resume_off(
        client, owner, ws):
    """A refuses on its five-hour window, a reset known, while its seven-day
    warning stands, and B is held back by its weekly reserve. The refusal, not the reserve, keeps A out, so with
    Auto-resume off the task waits for A's own reset, its line saying the accounts are out of room, and never for a
    Continue alone (_wait_for_reset reads the reserve only for an account that is open)."""
    h, user = owner
    _b_held_back_by_its_reserve(user)
    task_id, first = started(client, h, user, account="A")
    assert press(client, h, task_id, "switches", auto_resume=False).status_code == 200
    now = int(time.time())
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"], {
            "seven_day": {"status": "allowed_warning", "resets_at": now + 86400, "at": now}}, None, now)
    reset = now + HOUR
    refuse(user, first, resets=reset)
    held = row(task_id)
    assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", reset), dict(held)
    assert held["reason"] == ("Every account this task may take is out of room; the earliest resets at "
                              f"{tasks._utc(reset)}.")


def test_the_wait_s_line_says_out_of_room_where_its_earliest_reset_is_an_account_s_own(client, owner, ws):
    """The control: where the earliest reset the wait counts is an account's own,
    refused, the line says every account the task may take is out of room, as before, B's reserve lifting later."""
    h, user = owner
    _b_held_back_by_its_reserve(user, days=3)
    task_id, first = started(client, h, user, account="A")
    reset = int(time.time()) + HOUR
    refuse(user, first, resets=reset)
    held = row(task_id)
    assert (held["state"], held["cause"], held["waits_until"]) == ("suspended", "window", reset), dict(held)
    assert held["reason"] == ("Every account this task may take is out of room; the earliest resets at "
                              f"{tasks._utc(reset)}.")


@pytest.mark.parametrize("auto_resume", [True, False])
def test_back_home_s_wait_with_crossing_off_counts_a_home_seat_its_reserve_alone_holds_back_while_auto_resume_is_on(
        client, owner, ws, monkeypatch, clock, auto_resume):
    """The next step's wait for a task away
    from its home group with crossing off (Back home) counts each home account its weekly reserve alone holds
    back, at its warned window's reset, as the other waits do, and only while auto-resume is on (tasks._reserve_waits).
    The task homed on A, org-x's Team seat, crossed onto B, a personal plan; C, another seat of org-x, is held back by
    its reserve until half an hour on, before A's reset an hour on. Crossing turned off, the step's end: with
    auto-resume on the task waits for C's reserve, and at that reset auto-resume takes it home onto C; with it off, it
    waits for A's reset, as before."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, reset = away_from_home(client, h, user, clock)
    own(user, ACC["C"], plan="Claude Team", org="org-x", org_name="X")
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["C"], {
            "seven_day": {"status": "allowed_warning", "resets_at": clock[0] + 30 * 60, "at": clock[0]}}, None,
            clock[0])
    c_reserve = window_reset(user, ACC["C"], "seven_day")
    assert c_reserve < reset
    if not auto_resume:
        assert press(client, h, task_id, "switches", auto_resume=False).status_code == 200
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    finish(user, row(task_id)["lease_run"], 1, 1)
    waiting = row(task_id)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == (
        "suspended", "window", c_reserve if auto_resume else reset), dict(waiting)
    if auto_resume:
        clock[0] = c_reserve + 1
        tasks.sweep(now=c_reserve + 1)
        after = row(task_id)
        assert acct(after["lease_run"]) == ACC["C"] and run(after["lease_run"])["chosen_by"] == "auto_resume"
        assert events(task_id, "task.crossed")[-1]["payload"]["home"] == "off"


def test_back_home_s_wait_with_no_home_reset_known_waits_for_a_continue_while_auto_resume_is_off(
        client, owner, ws, monkeypatch, clock):
    """As the test above with auto-resume off, A's refusal giving no reset
    this time. Back home's wait passes C, the home seat its weekly reserve alone holds back, and gives it no reset, so
    with no home reset known the task waits, suspended, for a Continue (tasks.HELD_BY_A_RESERVE), where with C left
    out of the wait it would need its owner for no known reset at all."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    task_id, _ = away_from_home(client, h, user, clock)
    own(user, ACC["C"], plan="Claude Team", org="org-x", org_name="X")
    with store.connect() as db:
        tasks.take_windows(db, user["id"], "account:" + ACC["A"], {
            "five_hour": {"status": "rejected", "resets_at": None, "at": clock[0]}}, None, clock[0])
        tasks.take_windows(db, user["id"], "account:" + ACC["C"], {
            "seven_day": {"status": "allowed_warning", "resets_at": clock[0] + 30 * 60, "at": clock[0]}}, None,
            clock[0])
    assert press(client, h, task_id, "switches", auto_resume=False).status_code == 200
    assert press(client, h, task_id, "switches", cross_groups=False).status_code == 200
    finish(user, row(task_id)["lease_run"], 1, 1)
    waiting = row(task_id)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"], waiting["reason"]) == (
        "suspended", "window", None, HELD_BY_A_RESERVE), dict(waiting)


@pytest.mark.parametrize("how", ["as a Team seat", "into another organisation", "still mixed"])
def test_a_task_on_a_seat_of_an_organisation_that_stops_reading_as_an_enterprise_one_is_told_and_crosses_nowhere(
        client, owner, ws, monkeypatch, how):
    """org-z holds E, an
    Enterprise seat of the owner's, and A, a Team seat, so it reads as an Enterprise one; a task runs on A, crossing
    on, C a personal plan with room, and another on E. E confirmed again as a Team seat of org-z, or into another
    organisation, org-w: org-z holds no Enterprise seat now and reads as a Team one again, so the task on A, whose
    account was not confirmed again, gets a journal line and its crossings end, as a press onto or off an Enterprise
    organisation's seat ends them (tasks._organisation_reread), from the old organisation's reading as from the new.
    Refused on A, E closed, it then waits, and never crosses onto C, where before nothing told it and it crossed onto C
    by itself. The task on E gets its own account's line alone (_account_moved), not a second.
    The control: org-z also holds G, another Enterprise seat, so it still reads as an Enterprise one: no line on the
    task on A, its crossings as they were."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["E"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    if how == "still mixed":
        own(user, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    own(user, ACC["C"])
    room(user, ACC["C"])
    task_e, _ = started(client, h, user, account="E")
    task_id, first = started(client, h, user, account="A")
    org = "org-w" if how == "into another organisation" else "org-z"
    _confirmed_again(client, h, user, ws, ACC["E"], plan="Claude Team" if how != "into another organisation" else
                     "Claude Enterprise", org=org, org_name="Zed" if org == "org-z" else "Wye",
                     email="other-" + ACC["E"] + "@example.com")
    on_e = [e["payload"] for e in events(task_e, "task.account_regrouped")]
    assert len(on_e) == 1 and "read_from" not in on_e[0], on_e
    lines = [e["payload"] for e in events(task_id, "task.account_regrouped")]
    if how == "still mixed":
        assert lines == [] and row(task_id)["crossings_ended"] == 0
        return
    (line,) = lines
    assert (line["account"], line["read_from"], line["read_to"], line["by"]) == (
        "account:" + ACC["A"], "enterprise", "team", "account:" + ACC["E"])
    assert (line["from_grp"], line["to_grp"], line["from_kind"], line["to_kind"]) == ("org:org-z",) * 2 + ("team",) * 2
    assert line["bound"] == {"crossings_ended": 1} and line["left_out"] is None
    assert line["why"].startswith("Its account's organisation holds no Enterprise seat of yours now, so it does not "
                                  "read as an Enterprise one.")
    assert row(task_id)["crossings_ended"] == 1
    with store.connect() as db:
        left = {e["account"]: e["left_out"] for e in tasks.relay_set(db, user["id"], task_id, int(time.time()))}
    assert left[ACC["C"]] == "its crossings ended: it stays in the group it is on"
    closed(user, ACC["E"], int(time.time()) + 2 * HOUR)
    refuse(user, first, resets=int(time.time()) + HOUR)
    assert all(acct(r["run_id"]) != ACC["C"] for r in runs_of(task_id)) and not events(task_id, "task.crossed")


@pytest.mark.parametrize("state", ["running", "offering"])
def test_a_task_on_a_seat_of_an_organisation_that_comes_to_read_as_an_enterprise_one_is_told_and_bound(
        client, owner, ws, monkeypatch, state):
    """The other way: org-z holds A, a Team seat; a task runs on A. G confirmed for the first time as an Enterprise seat
    of
    org-z: org-z reads as an Enterprise one now, so the task on A gets a journal line, its crossings end and it is bound
    to org-z, as a press onto an Enterprise organisation's seat binds it. An offered task is left out, its offer's fence
    being its state_version: a line saying so, its binding as it was."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, account="A")
    before = row(task_id)
    if state == "offering":
        with store.connect() as db:
            tasks.set_state(db, user["id"], task_id, "offering")
    seat = report(client, h, ws, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    joined(client, h, user, seat["id"])
    (line,) = [e["payload"] for e in events(task_id, "task.account_regrouped")]
    assert (line["account"], line["read_from"], line["read_to"], line["by"]) == (
        "account:" + ACC["A"], "team", "enterprise", "account:" + ACC["G"])
    after = row(task_id)
    if state == "offering":
        assert line["left_out"] == "offered" and line["bound"] == {}
        assert (after["crossings_ended"], after["bound_org"]) == (before["crossings_ended"], before["bound_org"])
        return
    assert line["left_out"] is None and line["bound"] == {"crossings_ended": 1}
    assert (after["crossings_ended"], after["bound_org"]) == (1, "org-z")
    assert line["why"].startswith("Its account's organisation holds an Enterprise seat of yours now, so it reads as an "
                                  "Enterprise one: no relay crosses into or out of its seats.")


def test_a_task_bound_to_none_on_a_seat_of_an_organisation_that_comes_to_read_as_an_enterprise_one_is_bound_to_it(
        client, owner, ws):
    """A task bound to no organisation on A, a Team seat of org-z (a task from an older release kept bindings, which the
    migration leaves unbound), is bound to org-z once G, confirmed
    for the first time as an Enterprise seat of org-z, makes it read as an Enterprise one, its crossings ended, as a
    press onto an Enterprise organisation's seat binds a task bound to none. A test of tasks bound there from their
    making alone would pass a binding never written."""
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, account="A")
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET bound_org = NULL WHERE id = ?", (task_id,))
    seat = report(client, h, ws, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    joined(client, h, user, seat["id"])
    (line,) = [e["payload"] for e in events(task_id, "task.account_regrouped")]
    assert line["bound"] == {"crossings_ended": 1, "bound_org": "org-z"}, line
    assert (row(task_id)["crossings_ended"], row(task_id)["bound_org"]) == (1, "org-z")


def test_a_task_on_another_seat_whose_crossings_had_ended_is_told_of_its_organisation_read_otherwise(
        client, owner, ws):
    """Every task on the organisation's other seats is told, its binding
    changed or not. org-z holds E, an Enterprise seat, and A, a Team seat; a task on A whose crossings a press had
    already ended. E confirmed again as a Team seat: the task gets its line, its binding as it was (`bound` {}), and
    words saying it needed no change."""
    h, user = owner
    own(user, ACC["E"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, account="A")
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET crossings_ended = 1 WHERE id = ?", (task_id,))
    _confirmed_again(client, h, user, ws, ACC["E"], plan="Claude Team", org="org-z", org_name="Zed",
                     email="other-" + ACC["E"] + "@example.com")
    (line,) = [e["payload"] for e in events(task_id, "task.account_regrouped")]
    assert (line["bound"], line["left_out"], line["read_from"], line["read_to"]) == ({}, None, "enterprise", "team")
    assert "Its binding needed no change." in line["why"], line["why"]


@pytest.mark.parametrize("how", ["as a Team seat", "still mixed"])
def test_a_press_naming_a_seat_of_an_organisation_that_stops_reading_as_an_enterprise_one_is_dropped(
        client, owner, ws, how):
    """The way out drops a
    press too. org-z holds E, an Enterprise seat, and A, a Team seat, so it reads as an Enterprise one; a task on B with
    a press naming A, made over the texts A had then. E confirmed again as a Team seat: org-z reads as an Enterprise one
    no more, so the press is dropped and B's end goes on on B. The control: org-z holds G, another Enterprise seat, so
    it still reads as one, and the press stands, B's end going on to A."""
    h, user = owner
    own(user, ACC["E"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    if how == "still mixed":
        own(user, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, account="B", steps=3)
    record(user, first, "working")
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    _confirmed_again(client, h, user, ws, ACC["E"], plan="Claude Team", org="org-z", org_name="Zed",
                     email="other-" + ACC["E"] + "@example.com")
    kept = how == "still mixed"
    assert (row(task_id)["asked"] or {}).get("id") == (ACC["A"] if kept else None)
    finish(user, first, 1, 1)
    moved = row(task_id)
    assert moved["state"] == "running" and acct(moved["lease_run"]) == (ACC["A"] if kept else ACC["B"])


@pytest.mark.parametrize("where", ["org-z", "org-y"])
def test_a_press_naming_a_seat_of_an_organisation_that_comes_to_read_as_an_enterprise_one_is_dropped(
        client, owner, ws, where):
    """A press naming A, a Team seat of
    org-z, made over the texts A had as org-z was read then, is dropped once G, an Enterprise seat, is
    confirmed into org-z for the first time, as a press naming an account confirmed as another kind of seat is dropped;
    so B's end goes on on B. The control: G confirmed into another organisation, org-y, which changes nothing of how
    org-z is read: the press stands and B's end goes on to A."""
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, account="B", steps=3)
    record(user, first, "working")
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    seat = report(client, h, ws, ACC["G"], plan="Claude Enterprise", org=where, org_name="Zed" if where == "org-z"
                  else "Why")
    joined(client, h, user, seat["id"])
    kept = where == "org-y"
    assert (row(task_id)["asked"] or {}).get("id") == (ACC["A"] if kept else None)
    finish(user, first, 1, 1)
    moved = row(task_id)
    assert moved["state"] == "running" and acct(moved["lease_run"]) == (ACC["A"] if kept else ACC["B"])


@pytest.mark.parametrize("mixed", [False, True])
def test_a_press_off_a_team_seat_of_an_organisation_holding_an_enterprise_seat_too_ends_the_task_s_crossings(
        client, owner, ws, mixed):
    """A press off a seat of an organisation holding both kinds is
    judged by the kind the organisation is read as, an Enterprise one, so that a task on A, its Team seat, pressed onto
    C, a personal plan, has its crossings ended, as a press off an Enterprise organisation's seat ends them, the page
    saying so beside C. The control: org-z holds the Team seat alone, so the press is off a Team
    organisation's seat onto a personal plan, which ends nothing."""
    h, user = owner
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    if mixed:
        own(user, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["C"])
    task_id, first = started(client, h, user, account="A", steps=3, between_steps="wait")
    finish(user, first, 1, 1)
    assert press(client, h, task_id, "continue-on", backend={"kind": "account", "id": ACC["C"]}).status_code == 200
    after = row(task_id)
    assert acct(after["lease_run"]) == ACC["C"]
    assert after["crossings_ended"] == (1 if mixed else 0)


def test_a_press_naming_a_team_seat_of_a_mixed_organisation_confirmed_again_as_its_enterprise_seat_is_dropped(
        client, owner, ws):
    """A press is dropped naming an account confirmed again as
    another kind of seat of its organisation, by the seat's own kind, not by the kind the organisation is read as. org-z
    holds G, an Enterprise seat, and A, a Team seat, so both read as Enterprise ones; a press naming A, then A confirmed
    again as an Enterprise seat of org-z: the organisation reads as it did, but A's own kind changed, so the press is
    dropped and B's end goes on on B."""
    h, user = owner
    own(user, ACC["G"], plan="Claude Enterprise", org="org-z", org_name="Zed")
    own(user, ACC["A"], plan="Claude Team", org="org-z", org_name="Zed")
    task_id, first = started(client, h, user, account="B", steps=3)
    record(user, first, "working")
    assert press(client, h, task_id, "next-on", backend={"kind": "account", "id": ACC["A"]}).status_code == 200
    _confirmed_again(client, h, user, ws, ACC["A"], plan="Claude Enterprise", org="org-z", org_name="Zed",
                     email="other-" + ACC["A"] + "@example.com")
    assert row(task_id)["asked"] is None
    finish(user, first, 1, 1)
    assert acct(row(task_id)["lease_run"]) == ACC["B"]


# ---- the read by itself and a confirmation made elsewhere ------------------------------------------------------------

def _confirmed_elsewhere(user, seat, page, state, took):
    """What another client does while a read waits on the workspace: the owner's confirmation of the account, made on
    the phone or in another tab, waiting on its hold (`held`), and for `confirmed` the hold's end too, as with a hold of
    no seconds (`OPEN_EDITION_ADE_TASK_ACCOUNT_HOLD`, 0 to 86400 are taken)."""
    def confirm_there():
        word = confirmation(user, f"task-account-own:{seat['id']}")
        took["hold"] = tasks.confirm_account(user, seat["id"], word, page)["hold"]
        if state == "confirmed":
            holds.settle(user["id"], took["hold"]["takes_effect_at"] + 1)
    return confirm_there


def _no_report(kind):
    """What the workspace gives back when its frozen start gave nothing: no report (a start that gave no `account`), or
    the report of an `account` object with no fields, every field empty (which `_clean_report` makes none)."""
    first, status = identity(ACC["A"], ACC["A"] + "@example.com")
    status = dict(status, authMethod="console")         # an auth status unlike the one the first read stored
    return {"no report": (None, status), "an empty report": ({key: None for key in first}, status)}[kind]


def _read_while_confirmed_elsewhere(client, h, user, ws, state, answer, mismatch=None):
    """The setting: the account is read once (a good identity) and the page shows that report; then a second
    read waits on the workspace, which answers `answer` once another client has confirmed the account (`held`, waiting
    on its hold; or `confirmed`, the hold applied too). Returns the account's row as the first read made it, what the
    page showed, `took` (the hold, and the whole row as that client left it), and the second read's response.
    `mismatch` is the one the workspace's own read names for the second read, as it does for a Console sign-in."""
    seat = report(client, h, ws, ACC["A"])
    page = shown(client, h, seat["id"])
    took = {}
    confirm_elsewhere = _confirmed_elsewhere(user, seat, page, state, took)

    def confirm_there():
        confirm_elsewhere()
        with store.connect() as db:
            took["row"] = dict(tasks._owner(db, user["id"], ACC["A"]))         # the whole row, as that client left it
    ws.meanwhile["/agents/identity"] = confirm_there
    ws.identities[ACC["A"]] = answer
    ws.mismatches[ACC["A"]] = mismatch
    r = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert r.status_code == 200, r.text
    return seat, page, took, r


@pytest.mark.parametrize("kind", ["no report", "an empty report"])
@pytest.mark.parametrize("state", ["held", "confirmed"])
def test_a_read_with_no_report_leaves_an_account_confirmed_elsewhere_meanwhile_as_it_is(client, owner, ws, state,
                                                                                        kind):
    """The read, waiting on the workspace, finds the account confirmed on another device when it comes to
    write, waiting on its hold or past it, and its start gave no report (or the report of nothing a start with no
    account gives). Written, that sent a held account's confirmation back (the hold's end: "read again during the hold
    and differs") and a confirmed account to `changed`. Now it writes nothing, in any column of the row, says so
    (`kept`: the state it found), and the account joins as it was confirmed, its report as the other device showed
    it."""
    h, user = owner
    seat, page, took, r = _read_while_confirmed_elsewhere(client, h, user, ws, state, _no_report(kind))
    kept = r.json()["account"]
    assert (kept["state"], kept["report"], kept["org_id"], kept["mismatch"]) == (state, page["report"], page["org_id"],
                                                                                None)
    with store.connect() as db:
        assert dict(tasks._owner(db, user["id"], ACC["A"])) == took["row"]
    assert r.json()["kept"] == state
    holds.settle(user["id"], took["hold"]["takes_effect_at"] + 1)
    after = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert (after["state"], after["grp"], after["hold"]) == ("confirmed", "personal", None)
    assert (after["report"], after["org_id"], after["mismatch"]) == (page["report"], page["org_id"], None)


@pytest.mark.parametrize("who", ["the same identity", "another email", "the same email on another plan"])
@pytest.mark.parametrize("kind", ["no auth status", "signed out"])
@pytest.mark.parametrize("state", ["held", "confirmed"])
def test_a_read_with_a_report_and_no_auth_status_to_compare_it_with_is_judged_by_its_report(client, owner, ws,
                                                                                            state, kind, who):
    """The second part of the read, `auth status` after the start, gave
    nothing (none, or signed out) while the start gave a good report. That is no information, not a disagreement. Where
    the report is the identity the owner confirmed, the report alone is written and what a comparison sets (the
    mismatch, the orgId, the auth status) is left as it was, since none was made and nothing changed: a held account
    joins, a confirmed one stays. Where it is another email it is written as before, the old orgId and auth status
    cleared (it is no longer the account's) and the mismatch "auth status" where there was no `auth status` to compare,
    or "email" where it said signed out (the signed-out record is stored, and it has no email): a held account's hold
    refuses it, a confirmed one is `changed`, and neither can be confirmed again before it is read again with both
    parts. Keeping the whole read, as for no report, would leave a confirmation standing over a folder signed in again
    as someone else."""
    h, user = owner
    email = "other@example.com" if who == "another email" else ACC["A"] + "@example.com"
    first, _ = identity(ACC["A"], email, plan="Claude Pro" if who == "the same email on another plan" else "Claude Max")
    answer = (first, None) if kind == "no auth status" else (first, {"loggedIn": False})
    seat, page, took, r = _read_while_confirmed_elsewhere(client, h, user, ws, state, answer)
    assert "kept" not in r.json()
    with store.connect() as db:
        now = dict(tasks._owner(db, user["id"], ACC["A"]))
    assert json.loads(now["report"]) == first
    if who == "the same identity":
        for column in ("auth_status", "org_id", "mismatch"):
            assert now[column] == took["row"][column], column
    else:
        assert now["org_id"] is None
        if kind == "no auth status":                    # nothing to compare, and nothing to store
            assert (now["mismatch"], now["auth_status"]) == ("auth status", None)
        else:                                           # signed out: its record is stored, and it has no email
            assert now["mismatch"] == "email" and json.loads(now["auth_status"])["loggedIn"] is False
    holds.settle(user["id"], took["hold"]["takes_effect_at"] + 1)
    after = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    if who == "the same identity":
        assert (after["state"], after["grp"], after["mismatch"]) == ("confirmed", "personal", None)
    else:
        assert after["state"] == ("reported" if state == "held" else "changed")
        assert after["report"] == first and after["mismatch"]
        assert confirm(client, h, user, seat["id"]).status_code == 412


def test_a_read_with_a_report_and_no_auth_status_writes_a_confirmed_account_s_report_alone(client, owner, ws):
    """Where the report is the identity confirmed, it is the report that is written (here the one field the identity
    does not compare, the token source, shows it) and nothing a comparison sets: the auth status, the orgId and the
    mismatch are as the other client left them, and the account stays confirmed."""
    h, user = owner
    first, _ = identity(ACC["A"], ACC["A"] + "@example.com")
    first = dict(first, token_source="CLAUDE_CODE_OAUTH_TOKEN")
    seat, page, took, r = _read_while_confirmed_elsewhere(client, h, user, ws, "confirmed", (first, None))
    assert "kept" not in r.json()
    with store.connect() as db:
        now = dict(tasks._owner(db, user["id"], ACC["A"]))
    assert (now["state"], json.loads(now["report"])) == ("confirmed", first)
    for column in ("auth_status", "org_id", "mismatch"):
        assert now[column] == took["row"][column], column


@pytest.mark.parametrize("before", ["reported", "changed"])
def test_a_read_that_reports_nothing_is_still_written_over_an_account_neither_confirmed_nor_held(client, owner,
                                                                                                 ws, before):
    """The narrowness of the rule above: only a row that is confirmed or held when the read writes is kept as it is. An
    account not yet confirmed, or whose confirmation changed, takes the read's failure as before (the page shows the
    report gone and its mismatch, "report"), and the answer says nothing of keeping."""
    h, user = owner
    if before == "reported":
        report(client, h, ws, ACC["A"])
    else:
        own(user, ACC["A"], state="changed")
    ws.identities[ACC["A"]] = (None, {})
    r = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert r.status_code == 200, r.text
    assert "kept" not in r.json()
    written = r.json()["account"]
    assert (written["state"], written["report"], written["mismatch"]) == (before, None, "report")


@pytest.mark.parametrize("found, state, token_source", [("same", "confirmed", "CLAUDE_CODE_OAUTH_TOKEN"),
                                                         ("other", "changed", None)])
def test_a_read_that_found_an_identity_is_still_written_over_a_confirmed_account(client, owner, ws, found, state,
                                                                                 token_source):
    """The other side of the narrowness: a read that found who the account is stands, over a confirmed account too. The
    same identity with a field the identity does not compare changed (the token source) is written and the account
    stays confirmed; another email is an identity change, the account `changed` and in no relay until it is confirmed
    again. Neither answer says `kept`."""
    h, user = owner
    seat = report(client, h, ws, ACC["A"], email="a@example.com")
    joined(client, h, user, seat["id"])
    first, status = identity(ACC["A"], "a@example.com")
    ws.identities[ACC["A"]] = (dict(first, token_source=token_source), status) if found == "same" else \
        identity(ACC["A"], "other@example.com")
    r = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert r.status_code == 200, r.text
    assert "kept" not in r.json()
    written = r.json()["account"]
    assert written["state"] == state
    assert written["report"]["token_source"] == token_source
    assert written["report"]["email"] == ("a@example.com" if found == "same" else "other@example.com")


def test_a_read_whose_report_and_auth_status_disagree_is_still_written_over_a_held_account(client, owner, ws):
    """A read whose report and `auth status` are both there and disagree found an identity it could not reconcile, not
    nothing: it is written over a held account, and the hold's end refuses it, as it did. (A read of another orgId
    during the hold is a report and an `auth status` that agree, written over a held account all the same.)"""
    h, user = owner
    first, status = identity(ACC["A"], "a@example.com")
    ws.identities[ACC["A"]] = (first, status)
    seat = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]}).json()["account"]
    r = confirm(client, h, user, seat["id"])
    assert r.status_code == 200, r.text
    ws.identities[ACC["A"]] = (first, dict(status, email="other@example.com"))
    again = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert again.status_code == 200 and "kept" not in again.json()
    assert (again.json()["account"]["state"], again.json()["account"]["mismatch"]) == ("held", "email")
    holds.settle(user["id"], r.json()["hold"]["takes_effect_at"] + 1)
    after = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert (after["state"], after["mismatch"]) == ("reported", "email")


def test_the_workspace_s_refusal_of_a_read_that_cannot_have_its_turn_reaches_the_page_as_a_state_that_forbids_it(
        client, owner, ws, monkeypatch):
    """One account's identity is read by one start at a time, in the workspace
    (`/agents/identity`, test_identity_read_lock.py), and a read that cannot have its turn in time is refused there with
    that reason (409, as the workspace refuses what is going on). The app passes the reason on as the state that
    forbids it, 412, since 409 is a state_version that differs here, and asks for the whole of the read's deadline,
    120 seconds, which the workspace's wait stays inside."""
    h, user = owner
    seat = report(client, h, ws, ACC["A"])
    reason = "Another read of this account's identity is still running: try again when it ends."
    asked = {}

    def busy(endpoint, method, path, body=None, timeout=30):
        asked["timeout"] = timeout
        raise workspace_client.WorkspaceCallError(409, reason)
    monkeypatch.setattr(workspace_client, "call", busy)
    r = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert (r.status_code, r.json()["detail"]) == (412, reason)
    assert asked["timeout"] == tasks.READ_DEADLINE == 120
    now = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert (now["state"], now["report"], now["mismatch"]) == (seat["state"], seat["report"], seat["mismatch"])


@pytest.mark.parametrize("status, detail", [(400, "That account is not signed in."), (404, "No such account."),
                                            (412, "Start the workspace first: an account's identity is read there.")])
def test_the_workspace_s_other_refusals_of_a_read_reach_the_page_as_they_were(client, owner, ws, monkeypatch,
                                                                               status, detail):
    """Only the workspace's 409 is the app's 412: its other refusals, which carry their own reasons, are passed on with
    their own statuses."""
    h, user = owner

    def refuses(endpoint, method, path, body=None, timeout=30):
        raise workspace_client.WorkspaceCallError(status, detail)
    monkeypatch.setattr(workspace_client, "call", refuses)
    r = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert (r.status_code, r.json()["detail"]) == (status, detail)


def test_a_workspace_that_does_not_answer_a_read_is_still_a_502(client, owner, ws, monkeypatch):
    h, user = owner

    def silent(endpoint, method, path, body=None, timeout=30):
        raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
    monkeypatch.setattr(workspace_client, "call", silent)
    r = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert (r.status_code, r.json()["detail"]) == (502, workspace_client.NOT_ANSWERING)


# the answers the real workspace route gave for three folders whose cleaned report is none (the tests' stand-in claude
# through `POST /agents/identity`): a Console sign-in, a start that gave no account while
# `auth status` names another identity, and a report with the email and no plan; each with the mismatch the workspace's own
# read named ("plan", "report", "plan"), which the app does not look at either
CLEANED_NONE = {
    "a console sign-in": ({"email": None, "organization": None, "plan": None, "token_source": None,
                           "api_key_source": "/login managed key", "api_provider": "firstParty"},
                          {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max", "orgId": "org-key"},
                          "plan"),
    "no account, auth status of another identity": (None, {"loggedIn": True, "authMethod": "claude.ai",
                                                           "email": "other@example.com", "subscriptionType": "max",
                                                           "orgId": "org-other"}, "report"),
    "a report with no plan": ({"email": "aaaaaaaaaaaa@example.com", "organization": None, "plan": None,
                               "token_source": None, "api_key_source": None, "api_provider": "firstParty"},
                              {"loggedIn": True, "authMethod": "claude.ai", "email": "aaaaaaaaaaaa@example.com",
                               "subscriptionType": "max", "orgId": "personal-aaaaaaaaaaaa"}, "plan"),
}


@pytest.mark.parametrize("shape", list(CLEANED_NONE))
@pytest.mark.parametrize("state", ["held", "confirmed"])
def test_a_read_whose_cleaned_report_is_none_is_kept_whatever_its_auth_status_says(client, owner, ws, state, shape):
    """What is kept is a read whose cleaned report is
    none (`_clean_report`: no report, or a report with no email or no plan), and for such a read nothing else is looked
    at, a signed-in `auth status` that names another email and orgId included. Writing the whole read where a signed-in
    `auth status` names another identity than the one confirmed would turn the second shape into `changed` or
    `reported`, and these six cases change with the rule, and nothing else does."""
    h, user = owner
    report_, status, named = CLEANED_NONE[shape]
    seat, page, took, r = _read_while_confirmed_elsewhere(client, h, user, ws, state, (report_, status), mismatch=named)
    assert r.json()["kept"] == state
    with store.connect() as db:
        assert dict(tasks._owner(db, user["id"], ACC["A"])) == took["row"]
    holds.settle(user["id"], took["hold"]["takes_effect_at"] + 1)
    after = next(a for a in client.get(A + "/accounts", headers=h).json()["accounts"] if a["id"] == seat["id"])
    assert (after["state"], after["report"], after["org_id"], after["mismatch"]) == ("confirmed", page["report"],
                                                                                     page["org_id"], None)


def _account_row(user):
    with store.connect() as db:
        return dict(tasks._owner(db, user["id"], ACC["A"]))


def test_the_same_email_and_plan_in_another_organisation_is_another_identity_over_a_confirmed_account(client, owner,
                                                                                                      ws):
    """A confirmed account's identity is its email, organisation and plan, so a read with
    the same email and plan in another organisation, and no `auth status` to say otherwise, is not the identity
    confirmed: the account is `changed`. (Comparing the email and plan alone would leave it confirmed though nothing
    could compare its orgId.)"""
    h, user = owner
    other, _ = identity(ACC["A"], ACC["A"] + "@example.com", plan="Claude Max", org="org-x", org_name="Acme")
    _read_while_confirmed_elsewhere(client, h, user, ws, "confirmed", (other, None))
    assert _account_row(user)["state"] == "changed"


def test_a_held_account_is_judged_by_the_whole_report_its_hold_waits_on(client, owner, ws):
    """The hold's end compares the whole report with the one it waits on (`pending`), so a
    held account read with a field the identity does not compare changed (the token source) and no `auth status` is
    not the report the hold waits on: it is written whole, its mismatch saying nothing was compared, and the page says
    at once that it will not join. (Judging it by the three fields of a confirmed account would write the report
    alone and leave the hold's end to refuse it.)"""
    h, user = owner
    first, _ = identity(ACC["A"], ACC["A"] + "@example.com")
    _read_while_confirmed_elsewhere(client, h, user, ws, "held", (dict(first, token_source="CLAUDE_CODE_OAUTH_TOKEN"),
                                                                  None))
    assert _account_row(user)["mismatch"] == "auth status"


def test_the_report_written_alone_leaves_a_mismatch_an_earlier_read_set(client, owner, ws):
    """A read that compared nothing leaves what a comparison set as it was, the mismatch
    among it: a held account whose earlier read, with both parts, found its plan word different keeps that mismatch
    when it is read again as the same identity with no `auth status`."""
    h, user = owner
    first, status = identity(ACC["A"], ACC["A"] + "@example.com")
    ws.identities[ACC["A"]] = (first, status)
    seat = client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]}).json()["account"]
    assert confirm(client, h, user, seat["id"]).status_code == 200                  # held
    ws.identities[ACC["A"]] = (first, dict(status, subscriptionType="pro"))         # both parts, the plan word disagrees
    client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert _account_row(user)["mismatch"] == "plan"
    ws.identities[ACC["A"]] = (first, None)                                         # the same identity, nothing to compare
    client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert _account_row(user)["mismatch"] == "plan"                                 # nothing was compared this time


def test_a_changed_account_read_again_with_nothing_to_compare_is_not_confirmable(client, owner, ws):
    """The report alone is written only over a row that is confirmed or held. A `changed`
    row (its identity read with another orgId) read again as its old identity with no `auth status` is written whole,
    so that the other orgId does not stay beside the old report with a mismatch of none and the account confirmable
    into the other organisation's group: confirming it is still refused (412)."""
    h, user = owner
    seat = report(client, h, ws, ACC["A"])
    joined(client, h, user, seat["id"])
    first, status = identity(ACC["A"], ACC["A"] + "@example.com")
    ws.identities[ACC["A"]] = (first, dict(status, orgId="personal-OTHER"))
    client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    ws.identities[ACC["A"]] = (first, None)
    client.post(A + "/accounts/report", headers=h, json={"account": ACC["A"]})
    assert confirm(client, h, user, seat["id"]).status_code == 412


def test_the_read_checks_the_row_inside_the_write_lock(client, owner, ws, monkeypatch):
    """The state of the row is read inside `BEGIN IMMEDIATE`, which is what closes the window between the workspace's
    answer
    and the write. The seam is the connection: right after the SELECT of the account's row, the owner's confirmation
    is started in a thread. With the transaction first the confirmation waits for the write lock, the read writes
    over a row that is not yet confirmed, and the confirmation is then refused (the report it names is gone);
    with the row read first the confirmation completes in the gap and the read then blanks the report of an account
    the owner has just confirmed. The assertion: an accepted confirmation never has its report blanked by this read."""
    h, user = owner
    seat = report(client, h, ws, ACC["A"])
    page = shown(client, h, seat["id"])
    ws.identities[ACC["A"]] = (None, identity(ACC["A"], ACC["A"] + "@example.com")[1])      # a start that gave no report
    word = confirmation(user, f"task-account-own:{seat['id']}")
    real, seen = store.connect, {}

    class Watched:
        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def execute(self, sql, *args, **kwargs):
            cursor = self._connection.execute(sql, *args, **kwargs)
            if "FROM ade_account_owners WHERE user_id = ? AND account = ?" in sql and "fired" not in seen:
                seen["fired"] = True

                def confirm_there():
                    try:
                        seen["result"] = tasks.confirm_account(user, seat["id"], word, page)
                    except Exception as e:                  # the refusal is what the right order gives
                        seen["result"] = e
                seen["thread"] = threading.Thread(target=confirm_there)
                seen["thread"].start()
                seen["thread"].join(1.5)                    # the confirmation completes in this gap, or waits for the lock
            return cursor

    @contextlib.contextmanager
    def connect(path=None):
        with real(path) as db:
            yield Watched(db) if "fired" not in seen else db
    monkeypatch.setattr(store, "connect", connect)
    tasks.report_account(user, ACC["A"])
    seen["thread"].join(15)
    monkeypatch.setattr(store, "connect", real)
    assert not (isinstance(seen["result"], dict) and _account_row(user)["report"] is None)
