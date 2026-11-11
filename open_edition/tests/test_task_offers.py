"""The teammate's tests (ade/task_offers.py), through
the app's routes and the module's functions. Where another module's side is still a stub it runs against a fake of it,
each standing for its module as that module's stub and contract say: the tasks module (FakeTasks, over the tables
ade/tasks.py made), the risk texts, confirmation purposes and notice words, the agents' refusal with extras, the fly
provider (each user a workspace of their own), and the workspace routes (FakeWorkspaces: push, fetch and checks, over
a remote of commits). Each fake is put in place only where the real side is still a stub, so the same tests run against
the real code once it is in place."""

import inspect
import hashlib
import json
import re
import secrets
import time

import pytest
from fastapi.responses import JSONResponse

from conftest import signed_up
from proofpane_open import auth, passkeys, store
from proofpane_open.ade import agents, holds, push, risk_texts, task_offers, tasks, workspace_client, workspaces
from proofpane_open.ade import task_shapes as shapes

T = "/api/ade/teammate"
PASSWORD = "correct horse battery"
OPERATOR = "Example Hosting Ltd, privacy@hosting.example"
ADDRESS = "https://github.com/acme/demo.git"
OWNER, MATE, OTHER, THIRD = "owner@example.com", "mate@example.com", "other@example.com", "third@example.com"
C0, C1, C2, C3 = "0" * 39 + "1", "0" * 39 + "2", "0" * 39 + "3", "0" * 39 + "4"
RETURNED_TREE = "7" * 40                # the tree the fake workspace says a return left the worktree at
HOUR = 3600
REAL_KICK = task_offers._kick           # before the fixture replaces it: the round's own thread, for its own test


# ---- fakes of the other modules' sides, each put in place only over a stub -----------------------------------------

def _stub(fn):
    """Whether `fn` is still a stub: a body that only raises NotImplementedError."""
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    return source.rstrip().endswith("raise NotImplementedError")


def _loads(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if value is not None else default


class FakeTasks:
    """ade/tasks.py as its stub's docstrings and its contract say, over the tables it
    made: what task_offers calls, where the real module is still a stub. It keeps what it was given, for the tests to
    read."""

    JSON = {"protected": [], "external": [], "backend": {}, "asked": None, "taken_out": [], "resumed": {}, "moves": [],
            "continued_from": None}

    def __init__(self):
        self.created, self.returns, self.states, self.started = [], [], [], []

    def spy(self, name, real):
        """The real `name`, once in place, called through, with what it was given kept as this fake keeps it, so that
        the tests read the same records whichever runs."""
        signature = inspect.signature(real)

        def call(*args, **kwargs):
            out = real(*args, **kwargs)
            given = signature.bind(*args, **kwargs).arguments
            if name == "create":
                self.created.append({"user_id": given["user_id"], "agent_id": given["agent_id"],
                                     "fields": given["fields"], "continued_from": given.get("continued_from"),
                                     "task_id": out})
            elif name == "take_return":
                self.returns.append((given["task_id"], given["report"]))
            elif name == "set_state":
                self.states.append((given["task_id"], given["state"], given.get("cause")))
            elif name == "start":
                self.started.append((given["user"]["id"], given["task_id"], given["state_version"]))
            return out
        return call

    def on(self, name):
        variable, default = tasks.SETTINGS[name]
        import os
        value = os.environ.get(variable, "").strip().lower()
        return True if value in tasks.SETTING_ON else False if value in tasks.SETTING_OFF else default

    def journal(self, db, user_id, task_id, kind, payload, *, run_id=None, fence=None, now=None):
        assert kind in tasks.JOURNAL_KINDS, kind
        text = json.dumps(payload, separators=(",", ":"))
        if len(text.encode()) > tasks.JOURNAL_PAYLOAD_MAX:
            raise ValueError("payload too long")
        return db.execute("INSERT INTO ade_agent_task_events (user_id, task_id, kind, run_id, fence, at, payload) "
                          "VALUES (?,?,?,?,?,?,?)", (user_id, task_id, kind, run_id, fence, now or int(time.time()),
                                                     text)).lastrowid

    def get(self, db, user_id, task_id):
        row = db.execute("SELECT * FROM ade_agent_tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
        if row is None:
            return None
        out = dict(row)
        for column, default in self.JSON.items():
            out[column] = _loads(out[column], default)
        return out

    def view(self, db, user_id, task_id, now=None):
        task = self.get(db, user_id, task_id)
        steps = [{"n": r["n"], "objective": r["objective"], "check": r["check_cmd"], "state": r["state"],
                  "evidence": r["evidence"], "checkpoint_id": r["checkpoint_id"], "attempts": r["attempts"],
                  "reopened": bool(r["reopened"])}
                 for r in db.execute("SELECT * FROM ade_agent_task_steps WHERE task_id = ? ORDER BY n", (task_id,))]
        decisions = [{"n": r["n"], "text": r["text"], "author": r["author"], "at": r["at"]}
                     for r in db.execute("SELECT * FROM ade_agent_task_decisions WHERE task_id = ? ORDER BY n",
                                         (task_id,))]
        checkpoints = [{"id": r["id"], "n": r["n"], "step": r["step"], "kind": r["kind"],
                        "head": _loads(r["manifest"], {}).get("head"), "digest": r["digest"],
                        "created_at": r["created_at"], "left_out": []}
                       for r in db.execute("SELECT * FROM ade_agent_checkpoints WHERE task_id = ? AND state = 'ready' "
                                           "ORDER BY n", (task_id,))]
        effects = [{"id": r["id"], "run_id": r["run_id"], "call_id": r["call_id"], "tool": r["tool"],
                    "kind": r["kind"], "target": r["target"], "state": r["state"], "exit_status": r["exit_status"],
                    "resolved": r["resolved"], "note": r["note"], "unseen": bool(r["unseen"]), "at": r["created_at"]}
                   for r in db.execute("SELECT * FROM ade_agent_effects WHERE task_id = ? ORDER BY created_at",
                                       (task_id,))]
        return dict(task, steps=steps, decisions=decisions, checkpoints=checkpoints, effects=effects)

    def create(self, db, user_id, agent_id, fields, *, continued_from=None, now=None):
        now = now or int(time.time())
        if db.execute("SELECT 1 FROM ade_agent_tasks WHERE user_id = ? AND agent_id = ? AND deleted_at IS NULL AND "
                      "state NOT IN ('done', 'stopped')", (user_id, agent_id)).fetchone():
            raise agents.AgentError(409, "That agent has an open task.")
        backend = fields["backend"]
        row = db.execute("SELECT grp FROM ade_account_owners WHERE user_id = ? AND account = ?",
                         (user_id, backend.get("id"))).fetchone()
        task_id = secrets.token_hex(16)
        db.execute("INSERT INTO ade_agent_tasks (id, user_id, agent_id, title, goal, goal_digest, protected, external, "
                   "repo_slug, repo_address, checkpoint0, state, backend, home_grp, between_steps, cross_groups, "
                   "custody_cross, continued_from, created_at, updated_at, repo) VALUES "
                   "(?,?,?,?,?,?,?,?,?,?,?,'ready',?,?,?,?,?,?,?,?,?)",
                   (task_id, user_id, agent_id, fields["title"], fields["goal"], "0" * 64,
                    json.dumps(fields.get("protected", [])), json.dumps(fields.get("external", [])),
                    fields.get("repo_slug"), fields.get("repo_address"), fields.get("checkpoint0"), json.dumps(backend),
                    row["grp"] if row else "personal", fields.get("between_steps", "checked"),
                    0 if continued_from else int(self.on("cross_groups")), fields.get("custody_cross", 0),
                    json.dumps(continued_from) if continued_from else None, now, now,
                    db.execute("SELECT repo FROM ade_agents WHERE id = ?", (agent_id,)).fetchone()["repo"]))
        for n, step in enumerate(fields["steps"], 1):
            db.execute("INSERT INTO ade_agent_task_steps (task_id, n, user_id, objective, check_cmd) VALUES "
                       "(?,?,?,?,?)", (task_id, n, user_id, step["objective"], step.get("check")))
        for n, decision in enumerate(fields.get("decisions", []), 1):
            db.execute("INSERT INTO ade_agent_task_decisions (task_id, n, user_id, text, author, at) VALUES "
                       "(?,?,?,?,?,?)", (task_id, n, user_id, decision["text"], decision["author"], now))
        self.created.append({"user_id": user_id, "agent_id": agent_id, "fields": fields,
                             "continued_from": continued_from, "task_id": task_id})
        return task_id

    def set_state(self, db, user_id, task_id, state, *, cause=None, reason=None, waits_until=None,
                  expect_version=None, now=None):
        assert state in tasks.STATES, state
        task = self.get(db, user_id, task_id)
        if expect_version is not None and task["state_version"] != expect_version:
            raise agents.AgentError(409, "the task changed; look again")
        db.execute("UPDATE ade_agent_tasks SET state = ?, cause = ?, reason = ?, waits_until = ?, state_version = "
                   "state_version + 1, updated_at = ? WHERE id = ? AND user_id = ?",
                   (state, cause, reason, waits_until, now or int(time.time()), task_id, user_id))
        self.states.append((task_id, state, cause))
        return self.get(db, user_id, task_id)

    def relay_groups(self, db, task):
        """The groups of the task's relay set, as tasks.relay_groups gives them, and why another is out."""
        home = task["home_grp"]
        if task["crossings_ended"]:         # the group it is on: its backend's
            on = db.execute("SELECT grp FROM ade_account_owners WHERE user_id = ? AND account = ?",
                            (task["user_id"], task["backend"].get("id"))).fetchone()
            return [on["grp"] if on is not None and on["grp"] else home], "its crossings ended"
        crossing = bool(task["custody_cross"]) if task["continued_from"] else \
            bool(task["cross_groups"]) and self.on("cross_groups")
        if not crossing:
            return [home], "crossing organisations is off for this task"
        kind = db.execute("SELECT grp_kind FROM ade_account_owners WHERE user_id = ? AND grp = ? LIMIT 1",
                          (task["user_id"], home)).fetchone()
        if home != shapes.PERSONAL and (kind is None or kind["grp_kind"] == "enterprise"):
            return [home], "no relay crosses into or out of an Enterprise organisation's seats"
        if home == shapes.PERSONAL:
            return [home, "org:" + task["bound_org"] if task["bound_org"] else "org:*"], "another organisation's seats"
        return [home, shapes.PERSONAL], "another organisation's seats"

    def relay_set(self, db, user_id, task_id, now):
        """As ade/tasks.py's: every account the owner reported, each with why it is left out of the task's relay set,
        if it is (another group, never confirmed, its identity changed, taken out), its windows, and whether it has
        room."""
        task = self.get(db, user_id, task_id)
        groups, why_out = self.relay_groups(db, task)
        out = []
        for row in db.execute("SELECT * FROM ade_account_owners WHERE user_id = ? ORDER BY position, account",
                              (user_id,)):
            windows = [{"window": w["rate_window"], "status": w["status"], "resets_at": w["resets_at"],
                        "at": w["seen_at"]}
                       for w in db.execute("SELECT * FROM ade_backend_windows WHERE user_id = ? AND backend = ?",
                                           (user_id, "account:" + row["account"]))]
            if row["state"] != "confirmed":
                left_out = "its identity changed: confirm it again" if row["state"] == "changed" else \
                    "not confirmed as yours"
            elif not any(g == row["grp"] or (g == "org:*" and row["grp_kind"] == "team") for g in groups):
                left_out = why_out
            elif not row["in_relays"]:
                left_out = "taken out of relays"
            else:
                left_out = None
            held = any(w["status"] == "rejected" and (w["resets_at"] is None or w["resets_at"] > now) for w in windows)
            out.append({"owner_id": row["id"], "account": row["account"], "grp": row["grp"],
                        "grp_kind": row["grp_kind"], "eligible": left_out is None, "left_out": left_out,
                        "windows": windows, "known_room": left_out is None and not held
                        and any(w["status"] == "allowed" for w in windows)})
        return out

    def custody_digest(self, db, user_id, task_id):
        """As ade/tasks.py's: the tagged digest of what custody's confirmation covers, the title among it."""
        from proofpane_core.canonical import canonicalize
        task, view = self.get(db, user_id, task_id), self.view(db, user_id, task_id)
        agent = db.execute("SELECT brief_version FROM ade_agents WHERE id = ?", (task["agent_id"],)).fetchone()
        covered = {"title": task["title"], "goal": task["goal"],
                   "steps": [{"n": s["n"], "objective": s["objective"], "check": s["check"]} for s in view["steps"]],
                   "decisions": [{"n": d["n"], "text": d["text"], "author": d["author"]} for d in view["decisions"]],
                   "external": task["external"], "protected": task["protected"],
                   "brief_version": agent["brief_version"] if agent is not None else None}
        return shapes.tagged_digest(b"proofpane-custody/1", canonicalize(covered))

    def unknown_effects(self, db, user_id, task_id):
        return db.execute("SELECT COUNT(*) FROM ade_agent_effects WHERE task_id = ? AND user_id = ? AND state = "
                          "'unknown'", (task_id, user_id)).fetchone()[0]

    def start(self, user, task_id, state_version, now=None):
        """As ade/tasks.py's POST /tasks/{id}/start, in a transaction of its own: step 1's first attempt queued, against
        the task's state_version, only on a task that has not started."""
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self.get(db, user["id"], task_id)
            if task is None:
                raise agents.AgentError(404, "No such task.")
            if task["state_version"] != state_version:
                raise agents.AgentError(409, "the task changed; look again")
            if task["state"] != "ready":
                raise agents.AgentError(412, "Only a task that has not started can be started.")
            db.execute("UPDATE ade_agent_tasks SET state = 'running', lease_run = 'eeeeeeeeeeee', state_version = "
                       "state_version + 1, updated_at = ? WHERE id = ?", (now or int(time.time()), task_id))
        self.started.append((user["id"], task_id, state_version))
        return {"task": None}

    def take_return(self, db, user_id, task_id, report, now=None):
        self.returns.append((task_id, report))
        for n, effect in enumerate(report["effects"]):
            db.execute("INSERT INTO ade_agent_effects (id, user_id, task_id, run_id, call_id, tool, kind, target, "
                       "state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (secrets.token_hex(16), user_id, task_id, "return", f"return:{n}:{effect['call_id']}",
                        effect["tool"], effect["kind"], effect["target"], effect["settled"] or "unknown", now, now))
        held = not report["descends"] or report["protected_touched"] or report["loaded_changed"]
        for step in report["steps"]:
            if step["state"] != "done":
                continue
            if held or step["check_passed"] is False:
                db.execute("UPDATE ade_agent_task_steps SET state = 'todo', reopened = 1 WHERE task_id = ? AND n = ?",
                           (task_id, step["n"]))
            else:
                db.execute("UPDATE ade_agent_task_steps SET state = 'done', evidence = ? WHERE task_id = ? AND n = ?",
                           ("checked" if step["check_passed"] else "said_done", task_id, step["n"]))
        if held:
            return self.set_state(db, user_id, task_id, "needs_owner", cause="returned", now=now)
        return self.set_state(db, user_id, task_id, "suspended", cause="step_done", now=now)

    def account_record(self, db, user_id, task_id, kind, accounts, *, step, run_id=None, fence=None, via=None,
                       now=None):
        assert kind in tasks.ACCOUNT_RECORD_KINDS and (via in tasks.ACCOUNT_RECORD_VIA) == (kind == "handoff")
        for snap in accounts:
            if "teammate" in snap:
                if set(snap) != {"teammate", "plan", "organization", "org_id"}:
                    raise ValueError("a teammate's snapshot holds its four members alone")
                if snap["plan"] in shapes.PERSONAL_PLANS and (snap["organization"] or snap["org_id"]):
                    raise ValueError("a personal plan's organisation is never written")
        task = self.get(db, user_id, task_id)
        came = task["continued_from"]
        return db.execute("INSERT INTO ade_task_account_record (user_id, task_id, agent_id, repo_address, branch, "
                          "title, kind, run_id, fence, step, via, accounts, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (user_id, task_id, task["agent_id"], task["repo_address"],
                           f"pp/task-{came['task'] if came else task_id}", task["title"], kind, run_id, fence, step,
                           via, json.dumps(accounts), now or int(time.time()))).lastrowid


# risk point 13's text as risk_texts gives it, its placeholders as written
POINT_13 = {
    "point": 13, "title": "Accepting a task from a teammate",
    "design": [{"case": None, "text": "The task runs on your own agent and account."},
               {"case": "custody", "text": "Tasks your teammate confirms for custody while it stands will run here."},
               {"case": "other_group", "text": "Point 1's organisation notice."},
               {"case": "adopted_check", "text": "This command runs in your workspace, as your user."},
               {"case": "offer", "text": "If you accept, {owner}'s record of this task keeps your user. {operator} "
                                         "holds it."},
               {"case": "custody", "text": "If you accept custody, then for every task handed to you while it stands, "
                                           "{owner}'s record of that task keeps your user. {operator} holds it."}],
    "quotations": [{"id": "sharing", "case": None, "source": "Consumer Terms of Service", "section": "2",
                    "address": "https://www.anthropic.com/legal/consumer-terms", "source_date": "Effective October 8, "
                    "2025", "read_on": "2026-10-10", "words": "You may not share your Account login information.",
                    "unread": None},
                   {"id": "responsibility", "case": "other_group", "source": "Commercial Terms of Service",
                    "section": "D.5", "address": "https://www.anthropic.com/legal/commercial-terms",
                    "source_date": "Effective June 17, 2025", "read_on": "2026-10-10",
                    "words": "Customer is responsible for all activity under its account.", "unread": None}],
    "unread": False}

# the words of the teammate's notices, as push.py holds them
WORDS = {
    "task-offered": ("A teammate offered you a task", "Open Agents.", "/app/#agents"),
    "task-handed": ("A task was handed to you", "Open Agents.", "/app/#agents"),
    "task-returned": ("A task of yours came back from your teammate", "Open Agents.", "/app/#agents"),
    "custody-asked": ("A teammate asks you to be their custodian", "Open Account.", "/app/#account"),
    "custody-suspended": ("Your custody was suspended", "Open Account.", "/app/#account"),
    "held-task-crossing": ("A task's custody crossing waits", push.WAITS, "/app/#agents"),
    "held-task-offer": ("An offer of a task waits", push.WAITS, "/app/#agents"),
    "held-task-custody": ("A task of yours goes to your custodian", "Goes to them at {at} unless you cancel it.",
                          "/app/#agents"),
}
PURPOSES = ("task-account-own:{i}|task-cross:{i}|task-custody-cross:{i}|task-offer:{i}|task-custody:{i}"
            "|task-custody-accept:{i}|task-custody-confirm:{i}|task-account-record:{i}|task-secret-lift:{i}:{i}")


class FakeProvider:
    """The fly provider as task_offers sees it: each user a machine of their own, running or not."""
    name = "fly"

    def __init__(self):
        self.running, self.started = set(), []
        self.refusing = set()           # users whose workspace the provider will not start (WorkspaceAccess)

    def check_user(self, user):
        pass

    def endpoint(self, user):
        if user["id"] not in self.running:
            return None
        return workspaces.Endpoint(http=f"http://{user['id']}.ws.test", ws=f"ws://{user['id']}.ws.test",
                                   secret=user["id"])

    def ensure(self, user):
        if user["id"] in self.refusing:
            raise workspaces.WorkspaceAccess(503, "The hosted workspace could not start.")
        self.running.add(user["id"])
        self.started.append(user["id"])
        return self.endpoint(user)

    def touch(self, user):
        pass

    def stop(self, user):
        self.running.discard(user["id"])
        return {"provider": "fly", "running": False}

    def status(self, user):
        return {"provider": "fly", "running": user["id"] in self.running}


class FakeWorkspaces:
    """The workspace routes that task_offers calls, each user's own by its endpoint's secret,
    over one remote of commits: push (the scan for secret shapes, the branch's head checked against the commit the app
    recorded, that commit pushed by its hash to the branch at the address it names), fetch (view, accept, return: the
    commit brought or not, the files a next run would load it changes, the protected paths it touches, whether it
    descends) and checks (each step's exit). Every call is kept with its body."""

    def __init__(self):
        self.calls = []
        self.remote = {}                # (address, branch) -> commit
        self.commits = set()            # what the remote holds
        self.heads = {}                 # task id -> its branch's head in the owner's workspace, where moved
        self.secret = None
        self.unreadable, self.silent = set(), set()
        self.loaded = {}                # commit -> the files a next run would load it changes
        # commit -> those an acceptance lists, where it compares otherwise than the view (the receiving agent's start)
        self.loaded_accept = {}
        self.touched = []
        self.descends = True
        self.exits = {}                 # step n -> its check's exit
        self.refusing = None            # (status, words) a push answers with, as a workspace refuses it in its words
        self.refusing_fetch = None      # (status, words) a fetch answers with
        self.during = None              # what changes while a fetch runs, outside the app's transactions
        self.mirrored = []              # (user id, agent id) of each agent the app mirrored, in order
        self.fetched = {}               # (user id, task id) -> the commit an acceptance's fetch made a worktree at
        self.removed = []               # (user id, task id) of each worktree the app removed
        self.steps = {}                 # commit -> the steps its commits complete since the handed one (a return)
        self.lands = True               # a push reaches the remote; else it is refused there (not a fast-forward)
        # the findings each user's workspace's scan finds in what a push would carry (task_runs._scan_all), by user id;
        # a push holds for those its body does not lift, and names those it lifted that the range met
        self.findings = {}

    def of(self, path, user_id=None):
        return [(u, body) for u, p, body in self.calls if p.endswith(path) and (user_id is None or u == user_id)]

    def call(self, endpoint, method, path, body=None, timeout=30):
        user_id = endpoint.secret
        self.calls.append((user_id, path, body))
        if user_id in self.silent:
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        # the app's word on the workspace's record of skill proposals, first in each mirror (skill_improve.send_word):
        # a workspace with no record yet, which takes the word
        if path == "/skill-improve/record":
            if method == "GET":
                return {"file": None, "holds": False, "generation": None, "word": False, "state": "waiting",
                        "held": None, "seen": 0, "refused": []}
            return {"word": True}
        # an agent mirrored (agents._mirror), and the facts' versions it reads after
        mirror = re.fullmatch(r"/agents/([0-9a-f]{12})", path)
        if mirror and method == "PUT":
            self.mirrored.append((user_id, mirror[1]))
            return dict(body)
        if path == "/agents/facts-versions" and method == "GET":
            return {}
        gone = re.fullmatch(r"/agents/tasks/([0-9a-f]{32})", path)
        if gone and method == "DELETE":
            self.removed.append((user_id, gone[1]))
            self.fetched.pop((user_id, gone[1]), None)
            return {"removed": True}
        found = re.fullmatch(r"/agents/tasks/([0-9a-f]{32})/(push|fetch|checks)", path)
        assert found and method == "POST", path
        task_id, route = found.groups()
        if route == "push":
            if self.refusing is not None:
                raise workspace_client.WorkspaceCallError(*self.refusing)
            if self.during is not None:
                self.during(dict(body, purpose="push"))
            if self.heads.get(task_id, body["branch_head"]) != body["branch_head"]:
                raise workspace_client.WorkspaceCallError(409, "The task's branch moved since its last checkpoint.")
            if self.secret is not None:
                return {"pushed": False, "ref": None, "before": None, "after": None, "secret_shape": self.secret}
            lifted = set(body.get("lifted") or [])
            found = self.findings.get(user_id, [])
            held = [f for f in found if not (isinstance(f, dict) and f.get("finding") in lifted)]
            if held:
                return {"pushed": False, "ref": None, "before": None, "after": None,
                        "secret_shape": {"shape": held[0].get("shape"), "file": held[0].get("file")},
                        "secret_findings": held, "lifted_passed": []}
            passed = [f["finding"] for f in found if f["finding"] in lifted]
            # to the task's own branch, or to the one `ref` names (a received task's work to the owner's)
            branch = body["ref"][len("refs/heads/"):] if body.get("ref") else f"pp/task-{task_id}"
            if not self.lands:
                before = self.remote.get((body["address"], branch))
                return {"pushed": False, "ref": "refs/heads/" + branch, "before": before, "after": before,
                        "secret_shape": None}
            self.remote[(body["address"], branch)] = body["commit"]
            self.commits.add(body["commit"])
            return {"pushed": True, "ref": "refs/heads/" + branch, "before": None, "after": body["commit"],
                    "secret_shape": None, "secret_findings": [], "lifted_passed": passed}
        if route == "fetch":
            if user_id in self.unreadable:
                raise workspace_client.WorkspaceCallError(412, "This workspace's GitHub sign-in cannot read it.")
            if self.refusing_fetch is not None:
                raise workspace_client.WorkspaceCallError(*self.refusing_fetch)
            if body["purpose"] == "return" and not self.descends:
                raise workspace_client.WorkspaceCallError(409, "The commit does not descend from the handed one.")
            if self.during is not None:
                self.during(body)
            brought = body["commit"] if body["commit"] in self.commits or body["purpose"] == "view" else None
            if body["purpose"] == "accept":
                # the workspace makes the task's worktree under the id the call names, refusing one it has
                if (user_id, task_id) in self.fetched:
                    raise workspace_client.WorkspaceCallError(409, "This task is here already.")
                if brought is not None:
                    self.fetched[(user_id, task_id)] = brought
            listed = self.loaded_accept if body["purpose"] == "accept" and body["commit"] in self.loaded_accept \
                else self.loaded
            return {"commit": brought, "descends": self.descends, "loaded_files": listed.get(body["commit"], []),
                    "protected_touched": list(self.touched),
                    "steps": self.steps.get(body["commit"], []) if body["purpose"] == "return" else [],
                    "tree": RETURNED_TREE if body["purpose"] == "return" else None}
        return {"results": [{"n": n, "exit": self.exits.get(n, 0), "tail": ""} for n in body["steps"]]}


class World:
    """The users, their workspaces and what the fakes keep, with helpers that write the tables as the other modules'
    code would have written them."""

    def __init__(self, client, monkeypatch, fake_tasks, workspaces_, provider):
        self.client, self.mp = client, monkeypatch
        self.tasks, self.ws, self.provider = fake_tasks, workspaces_, provider
        self.told, self.stops = [], []
        self.headers, self.ids = {}, {}

    # -- users ---------------------------------------------------------------------------------------------------
    def user(self, email):
        if email not in self.headers:
            self.headers[email] = signed_up(self.client, email, PASSWORD)
            self.ids[email] = self.client.get("/api/auth/me", headers=self.headers[email]).json()["id"]
            # each user a machine of their own (FakeProvider), so the row the fly provider writes as it makes one: an
            # account with it keeps its work there until it chooses otherwise (ade/storage.py)
            now = int(time.time())
            with store.connect() as db:
                db.execute("INSERT OR IGNORE INTO ade_workspaces (user_id, app, region, created_at, updated_at) VALUES "
                           "(?,?,?,?,?)", (self.ids[email], "pp-workspaces-test", "syd", now, now))
        return self.ids[email]

    def u(self, email):
        return auth.user(self.user(email))

    def h(self, email):
        self.user(email)
        return self.headers[email]

    def confirmation(self, email, change):
        r = self.client.post("/api/account/confirm", headers=self.h(email),
                             json={"password": PASSWORD, "change": change})
        assert r.status_code == 200, r.text
        return r.json()["confirmation"]

    # -- what the other modules write ----------------------------------------------------------------------------
    def agent(self, email, repo="demo", account="aaaaaaaaaaaa", name="Builder", brief="Build it."):
        agent_id = secrets.token_hex(6)
        now = int(time.time())
        with store.connect() as db:
            db.execute("INSERT INTO ade_agents (id, user_id, name, brief, repo, account, created_at, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?)", (agent_id, self.user(email), name, brief, repo, account, now, now))
        return agent_id

    def account(self, email, account, plan="Claude Max", org=None, org_id=None, in_relays=True, position=0):
        grp, kind = shapes.group_of(plan, org_id)
        report = {"email": email, "organization": org, "plan": plan, "token_source": None, "api_key_source": None,
                  "api_provider": "firstParty"}
        with store.connect() as db:
            db.execute("INSERT INTO ade_account_owners (id, user_id, account, report, org_id, state, confirmed, grp, "
                       "grp_kind, position, in_relays) VALUES (?,?,?,?,?,'confirmed',?,?,?,?,?)",
                       (secrets.token_hex(16), self.user(email), account, json.dumps(report), org_id,
                        json.dumps(report), grp, kind, position, int(in_relays)))

    def window(self, email, account, status, resets_at=None, seen_at=None, window="five_hour"):
        with store.connect() as db:
            db.execute("INSERT OR REPLACE INTO ade_backend_windows (user_id, backend, rate_window, status, resets_at, "
                       "seen_at) VALUES (?,?,?,?,?,?)", (self.user(email), "account:" + account, window, status,
                                                         resets_at, seen_at or int(time.time())))

    def task(self, email, agent_id, state="suspended", account="aaaaaaaaaaaa", home="personal", address=ADDRESS,
             steps=(("Write the parser", "pytest -q tests/test_parser.py"), ("Write the docs", None)),
             decisions=("Use the standard library only.",), head=C1, protected=("tests/**",), external=(),
             **columns):
        task_id = secrets.token_hex(16)
        now = int(time.time())
        values = dict(id=task_id, user_id=self.user(email), agent_id=agent_id, title="Parse the config files",
                      goal="Parse every config file the service reads.", goal_digest="0" * 64,
                      protected=json.dumps(list(protected)), external=json.dumps(list(external)), repo="demo",
                      repo_slug="acme/demo", repo_address=address, checkpoint0=C0, state=state,
                      backend=json.dumps({"kind": "account", "id": account}), home_grp=home, cross_groups=0,
                      created_at=now, updated_at=now)
        values.update(columns)
        with store.connect() as db:
            db.execute(f"INSERT INTO ade_agent_tasks ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                       tuple(values.values()))
            for n, (objective, check) in enumerate(steps, 1):
                db.execute("INSERT INTO ade_agent_task_steps (task_id, n, user_id, objective, check_cmd, state) VALUES "
                           "(?,?,?,?,?,?)", (task_id, n, self.user(email), objective, check, "done" if n == 1 else
                                             "todo"))
            for n, text in enumerate(decisions, 1):
                db.execute("INSERT INTO ade_agent_task_decisions (task_id, n, user_id, text, author, at) VALUES "
                           "(?,?,?,?,'owner',?)", (task_id, n, self.user(email), text, now))
        if head:
            self.checkpoint(email, task_id, head, n=1)
        return task_id

    def checkpoint(self, email, task_id, head, n):
        with store.connect() as db:
            db.execute("INSERT INTO ade_agent_checkpoints (id, user_id, task_id, step, n, kind, manifest, digest, "
                       "state, created_at) VALUES (?,?,?,?,?,'step',?,?,'ready',?)",
                       (secrets.token_hex(16), self.user(email), task_id, 1, n, json.dumps({"head": head}), "0" * 64,
                        int(time.time())))

    def effect(self, email, task_id, state="unknown", kind="git_remote", target="origin pp/task-x"):
        with store.connect() as db:
            db.execute("INSERT INTO ade_agent_effects (id, user_id, task_id, run_id, call_id, tool, kind, target, "
                       "state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (secrets.token_hex(16), self.user(email), task_id, "aaaaaaaaaaaa", secrets.token_hex(4), "Bash",
                        kind, target, state, int(time.time()), int(time.time())))

    def row(self, table, key, value):
        with store.connect() as db:
            row = db.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone()
        return dict(row) if row is not None else None

    def get(self, email, task_id):
        with store.connect() as db:
            return tasks.get(db, self.user(email), task_id)

    def offer(self, offer_id):
        return self.row("ade_agent_task_offers", "id", offer_id)

    def custody(self, custody_id):
        return self.row("ade_task_custody", "id", custody_id)

    def journal(self, task_id=None):
        with store.connect() as db:
            rows = db.execute("SELECT * FROM ade_agent_task_events ORDER BY seq").fetchall()
        return [dict(r, payload=json.loads(r["payload"])) for r in rows if task_id is None or r["task_id"] == task_id]

    def record(self, task_id):
        with store.connect() as db:
            rows = db.execute("SELECT * FROM ade_task_account_record WHERE task_id = ? ORDER BY seq",
                              (task_id,)).fetchall()
        return [dict(r, accounts=json.loads(r["accounts"])) for r in rows]

    def notices(self, email):
        return [m.title for u, m in self.told if u == self.user(email)]

    # -- the app's side ------------------------------------------------------------------------------------------
    def offer_to(self, task_id, owner=OWNER, address=MATE, confirm=True):
        """An offer made and confirmed through the routes, as the page makes it: its draft, then its confirmation."""
        version = self.get(owner, task_id)["state_version"]
        r = self.client.post(f"{T}/offers", headers=self.h(owner),
                             json={"task_id": task_id, "state_version": version, "address": address})
        assert r.status_code == 200, r.text
        draft = r.json()["offer"]
        if not confirm:
            return draft
        r = self.client.post(f"{T}/offers/{draft['id']}/confirm", headers=self.h(owner),
                             json={"confirmation": self.confirmation(owner, f"task-offer:{draft['id']}")})
        assert r.status_code == 200, r.text
        return r.json()

    def settle(self, email=OWNER, after=200):
        """The holds whose time has come, `after` seconds from now, applied; then a round of the module's."""
        now = int(time.time()) + after
        holds.settle(self.user(email), now)
        task_offers.drive(now)
        return now

    def forget(self, now):
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task_offers.forget(db, now)

    def running(self, *emails):
        for email in emails:
            self.provider.running.add(self.user(email))


@pytest.fixture
def world(client, monkeypatch):
    """The teammate track turned on under the fly provider, the operator named, with the other modules' sides in place:
    their real code where it is not a stub, else a fake of it."""
    monkeypatch.setenv("OPEN_EDITION_ADE", "fly")
    monkeypatch.setenv("OPEN_EDITION_OPERATOR", OPERATOR)
    for variable in ("OPEN_EDITION_ADE_TASK_TEAMMATE", "OPEN_EDITION_ADE_TASK_RELAY",
                     "OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "OPEN_EDITION_ADE_TASK_OFFER_HOLD",
                     "OPEN_EDITION_ADE_TASK_CROSSING_HOLD"):
        monkeypatch.delenv(variable, raising=False)
    # the relay on, as a deployment that sets it: custody's hand-off is the relay's, and the relay is off
    # unless set; the test of custody under the default
    # deletes the setting again
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "on")
    provider = FakeProvider()
    monkeypatch.setattr(workspaces, "provider", lambda: provider)
    fake = FakeTasks()
    for name in ("on", "journal", "get", "view", "create", "set_state", "relay_set", "unknown_effects",
                 "take_return", "account_record"):
        if _stub(getattr(tasks, name)):
            monkeypatch.setattr(tasks, name, getattr(fake, name))
    # three of ade/tasks.py's that its stub has not: the custody digest its "Answer, and confirm for custody" keeps, the
    # groups of a task's relay set, and Start, which a custody task's first attempt is queued by
    for name in ("custody_digest", "relay_groups", "start"):
        if not hasattr(tasks, name) or _stub(getattr(tasks, name)):
            monkeypatch.setattr(tasks, name, getattr(fake, name), raising=False)
    # where the real one is in place, what it is given is kept as the fake keeps it
    for name in ("create", "take_return", "set_state", "start"):
        real = getattr(tasks, name)
        if getattr(real, "__self__", None) is not fake:
            monkeypatch.setattr(tasks, name, fake.spy(name, real))
    if _stub(risk_texts.operator):
        import os
        monkeypatch.setattr(risk_texts, "operator",
                            lambda: " ".join(os.environ.get("OPEN_EDITION_OPERATOR", "").split()) or None)
    if _stub(risk_texts.text):
        def text(point):
            if point != 13:
                return None
            operator = risk_texts.operator()
            out = json.loads(json.dumps(POINT_13))
            for part in out["design"]:
                part["text"] = part["text"].replace("{operator}", operator) if operator else part["text"]
            return dict(out, digest="d" * 64)
        monkeypatch.setattr(risk_texts, "text", text)
    if not passkeys.CHANGE.fullmatch("task-offer:" + "0" * 32):
        monkeypatch.setattr(passkeys, "CHANGE", re.compile(passkeys.CHANGE.pattern + "|" + PURPOSES.replace(
            "{i}", passkeys._ID)))
    for kind, words in WORDS.items():
        if kind not in push.WORDS:
            monkeypatch.setitem(push.WORDS, kind, words)
    try:
        agents.AgentError(409, "x", off="teammate")
    except TypeError:
        # the real agents.py widens AgentError to take extras, and main.py's handler to answer them
        def init(self, status, detail, **extra):
            Exception.__init__(self, detail)
            self.status, self.extra = status, extra
        monkeypatch.setattr(agents.AgentError, "__init__", init)
        from proofpane_open import main
        monkeypatch.setitem(main.app.exception_handlers, agents.AgentError, lambda request, exc: JSONResponse(
            status_code=exc.status, content={"detail": str(exc), **getattr(exc, "extra", {})}))
        monkeypatch.setattr(main.app, "middleware_stack", None)
    ws = FakeWorkspaces()
    monkeypatch.setattr(workspace_client, "call", ws.call)
    w = World(client, monkeypatch, fake, ws, provider)
    monkeypatch.setattr(push, "notice", lambda user_id, message, now=None, post=None: w.told.append(
        (user_id, message)) or 1)

    def stop(user, run_id):
        w.stops.append((user["id"], run_id))
        if user["id"] in ws.silent:
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        return {"run_id": run_id, "state": "stopped"}
    monkeypatch.setattr(agents, "stop_run", stop)
    monkeypatch.setattr(agents, "_deliver_soon", lambda user, start=False: provider.ensure(user) and False)
    monkeypatch.setattr(task_offers, "_kick", lambda: None)     # the tests run each round themselves (drive)
    monkeypatch.setattr(task_offers, "_keep_running", lambda: None)
    task_offers._seen.clear()
    task_offers._shown.clear()
    return w


def setup_pressed(w, owner_state="suspended", mate_plan="Claude Team", mate_org="Yarra", mate_org_id="org-y", **task):
    """The owner's task on their personal plan, with `task`'s columns, and the teammate's agent on the same repository,
    its account the teammate's own: on a seat of organisation Y unless said otherwise."""
    w.account(OWNER, "aaaaaaaaaaaa", "Claude Max")
    owner_agent = w.agent(OWNER)
    task_id = w.task(OWNER, owner_agent, state=owner_state, lease_run="bbbbbbbbbbbb" if owner_state == "running"
                     else None, **task)
    w.account(MATE, "cccccccccccc", mate_plan, mate_org, mate_org_id)
    mate_agent = w.agent(MATE, account="cccccccccccc", name="Helper")
    return task_id, mate_agent


def offered(w, task_id, **kw):
    """An offer of `task_id` past its hold, pushed and waiting for the teammate, and read by their workspace."""
    made = w.offer_to(task_id, **kw)
    w.running(OWNER, MATE)
    w.settle()
    task_offers.drive()
    return made


# ---- the track's gate -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("off, words", [
    ("setting", task_offers.OFF_SETTING), ("purposes", task_offers.OFF_PURPOSES), ("local", task_offers.OFF_LOCAL),
    ("operator", task_offers.OFF_OPERATOR)])
def test_while_the_track_is_off_only_the_reads_and_what_ends_a_hand_off_answer(world, monkeypatch, off, words):
    """The teammate setting, confirmations that carry no purpose (as older releases made them), the local provider,
    and no operator named (an offer, a custody request or an acceptance taken while OPEN_EDITION_OPERATOR is unset):
    each turns offers and custody off. Of the track's 18 routes, the 10 that start or advance a hand-off (a lift of what
    held a push's scan among them, which lets the next push go out) refuse 409 with why and
    `off`, though given all they would otherwise take, a confirmation made for each while the track was on among it,
    and what was made while it was on (an offer waiting, one in its hold, a custody request, custody, a task with the
    custodian) stays as it was; the 2 reads answer with `off`, why, and point 13's text on nothing; and the 6 that only
    end or undo one answer and do it. Custody hands nothing."""
    task_id, mate_agent, custody_id = setup_custody(world)
    theirs = handed(world, task_id)["their_task"]
    accept_custody(world, custody_id, [mate_agent, world.agent(MATE, account="cccccccccccc", name="Spare")])
    offer_id = offered(world, world.task(OWNER, world.agent(OWNER)))["offer"]["id"]
    in_hold = world.task(OWNER, world.agent(OWNER))
    held = world.offer_to(in_hold)["offer"]["id"]
    taker = world.agent(MATE, account="cccccccccccc", name="Taker")
    draft = world.offer_to(world.task(OWNER, world.agent(OWNER)), confirm=False)["id"]
    asked = ask_custody(world, owner=OTHER)
    pending = world.client.post(f"{T}/custody", headers=world.h(THIRD), json={"address": MATE}).json()["custody"]["id"]
    other_task = world.task(OWNER, world.agent(OWNER))
    confirm_for_custody(world, other_task)
    assert candidate(world, other_task) is not None
    other_version = world.get(OWNER, other_task)["state_version"]
    version = world.get(MATE, theirs)["state_version"]
    made = {"offer": world.confirmation(OWNER, f"task-offer:{draft}"),
            "custody": world.confirmation(THIRD, f"task-custody:{pending}"),
            "accept": world.confirmation(MATE, f"task-custody-accept:{asked}"),
            "confirm": world.confirmation(OWNER, f"task-custody-confirm:{other_task}"),
            "cross": world.confirmation(OWNER, f"task-custody-cross:{other_task}"),
            "lift": world.confirmation(OWNER, task_offers.lift_subject(other_task, ["0" * 32]))}
    assert task_offers.enabled(world.u(OWNER)) is None
    if off == "setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_TEAMMATE", "off")
    elif off == "purposes":
        monkeypatch.setattr(passkeys, "CHANGE", re.compile(r"passkey-add|sign-out-everywhere"))
    elif off == "local":
        monkeypatch.setattr(workspaces, "provider_name", lambda: "local")
    else:
        monkeypatch.delenv("OPEN_EDITION_OPERATOR")
    assert task_offers.enabled(world.u(OWNER)) == words
    refused = [(OWNER, "POST", "/offers", {"task_id": other_task, "state_version": other_version, "address": MATE}),
               (OWNER, "POST", f"/offers/{draft}/confirm", {"confirmation": made["offer"]}),
               (MATE, "POST", f"/offers/{offer_id}/accept", {"agent_id": taker, "adopt_checks": []}),
               (THIRD, "POST", "/custody", {"address": MATE}),
               (THIRD, "POST", f"/custody/{pending}/confirm", {"confirmation": made["custody"]}),
               (MATE, "POST", f"/custody/{asked}/accept", {"agents": [taker], "confirmation": made["accept"]}),
               (OWNER, "POST", f"/tasks/{other_task}/custody-confirm",
                {"state_version": other_version, "confirmation": made["confirm"]}),
               (OWNER, "POST", f"/tasks/{other_task}/custody-cross",
                {"state_version": other_version, "confirmation": made["cross"]}),
               (MATE, "POST", f"/tasks/{theirs}/keep", {"state_version": version}),
               (OWNER, "POST", f"/tasks/{other_task}/secret-lift", {"findings": ["0" * 32],
                                                                    "confirmation": made["lift"]})]
    reads = [(MATE, "GET", "/offers", None), (MATE, "GET", "/custody", None)]
    # in this order none stands in another's way: Give back before Take back, which then finds the task on its way
    answered = [(MATE, "POST", f"/offers/{offer_id}/decline", {}),
                (OWNER, "POST", f"/offers/{held}/withdraw", {}),
                (MATE, "POST", f"/tasks/{theirs}/give-back", {"state_version": version}),
                (OWNER, "POST", f"/tasks/{task_id}/take-back", {}),
                (MATE, "POST", f"/custody/{asked}/decline", {}),
                (OWNER, "POST", f"/custody/{custody_id}/end", {})]
    routes = {(m, re.sub(r"\{\w+\}", "{}", r.path)) for r in task_offers.router.routes for m in r.methods}
    assert {(m, re.sub(r"[0-9a-f]{32}", "{}", T + p)) for _, m, p, _ in refused + reads + answered} == routes \
        and len(routes) == 18 and (len(refused), len(reads), len(answered)) == (10, 2, 6)
    for who, method, path, body in refused:
        r = world.client.request(method, T + path, headers=world.h(who), json=body)
        assert r.status_code == 409 and r.json() == {"detail": words, "off": "teammate"}, (path, r.text)
    assert world.offer(offer_id)["state"] == "offered" and world.offer(draft)["state"] == "draft"
    assert world.offer(held)["state"] == "held" and world.get(OWNER, in_hold)["state"] == "offering"
    assert [world.custody(c)["state"] for c in (custody_id, asked, pending)] == ["accepted", "asked", "asked"]
    assert world.custody(pending)["confirmed_at"] is None and world.custody(custody_id)["custodian_user"]
    assert world.get(OWNER, task_id)["state"] == "handed_over" and world.get(MATE, theirs)["state_version"] == version
    assert world.get(OWNER, other_task)["state_version"] == other_version and len(world.tasks.created) == 1
    assert candidate(world, other_task) is None
    got = [world.client.request(method, T + path, headers=world.h(who)) for who, method, path, _ in reads]
    assert [r.status_code for r in got] == [200, 200] and [r.json()["off"] for r in got] == [words, words]
    offers_, custody_ = got[0].json(), got[1].json()
    assert [(o["id"], o["terms"]) for o in offers_["received"]] == [(offer_id, None)]
    assert [(c["id"], c["terms"]) for c in custody_["as_custodian"]] == [(custody_id, None), (asked, None)]
    assert [t["id"] for t in custody_["tasks"]] == [theirs]
    for who, method, path, body in answered:
        r = world.client.request(method, T + path, headers=world.h(who), json=body)
        assert r.status_code == 200, (path, r.text)
    assert world.offer(offer_id)["state"] == "declined" and world.offer(held)["state"] == "withdrawn"
    assert world.get(OWNER, in_hold)["state"] == "suspended" and world.get(OWNER, task_id)["state"] == "returning"
    assert [world.custody(c)["state"] for c in (custody_id, asked)] == ["ended", "declined"]
    assert candidate(world, other_task) is None


def test_the_track_is_on_under_the_fly_provider_with_purposes_and_an_operator(world):
    assert task_offers.enabled(world.u(OWNER)) is None
    r = world.client.get(f"{T}/offers", headers=world.h(OWNER))
    assert r.status_code == 200 and r.json() == {"made": [], "received": [], "secret_holds": []}


# ---- an offer the owner presses -------------------------------------------------------------------------------------

def _named(text, *who):
    """Whether `text` names any of `who` (a user id or an email)."""
    return any(w and w.lower() in text.lower() for w in who)


def test_an_offer_pushes_the_end_state_its_last_attempt_left_past_its_last_checkpoint(world):
    """The commit the app recorded is the task's last checkpoint or end state. An attempt stopped
    after the last checkpoint (the workspace's stop commits its change) left the task at an end state past it, its
    branch there: the offer pushes that commit, the branch checked at it, not the checkpoint behind it, which would
    read as a branch something else moved."""
    task_id, _ = setup_pressed(world)
    end = "0" * 39 + "6"
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, sent, queued_at, updated_at, task_id, "
                   "fence, resume) VALUES (?,?,?,'stopped',1,?,?,?,1,?)",
                   ("dddddddddddd", world.get(OWNER, task_id)["agent_id"], world.user(OWNER), now, now, task_id,
                    json.dumps({"end_state": {"head": end, "tree": None, "at": now + 1}})))
    world.ws.heads[task_id] = end
    made = world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    (pushed,) = world.ws.of("/push")
    assert (pushed[1]["commit"], pushed[1]["branch_head"]) == (end, end)
    offer = world.offer(made["offer"]["id"])
    assert offer["state"] == "offered" and offer["commit"] == end


def test_an_offer_is_confirmed_held_pushed_by_its_recorded_commit_and_accepted(world):
    """Without its confirmation the offer is refused, and with one made for another change; with its
    own the task is offering under a task.offer hold whose summary names ids alone; past the hold the owner's
    workspace pushes the commit the app recorded, by its hash, to the address recorded when the task was made, the
    branch's head checked against it; the packet is kept; the teammate, told content-free, sees the owner's checks
    word for word and point 13's text, and accepts into their agent: their task starts from the offered commit, its
    steps without the owner's checks, its decisions the teammate's; the owner's task is handed over; the owner's
    account record holds the teammate by user id with their account's plan and seat organisation, never their
    email."""
    task_id, mate_agent = setup_pressed(world)
    world.checkpoint(OWNER, task_id, C2, n=2)          # the newest checkpoint: the commit the app recorded
    world.ws.heads[task_id] = C2
    world.effect(OWNER, task_id, "done", kind="git_remote", target="origin main")      # an outward effect, done
    world.effect(OWNER, task_id, "done", kind="local_write", target="src/parser.py")   # not outward
    draft = world.offer_to(task_id, confirm=False)
    assert draft["state"] == "draft" and draft["to_email"] == MATE and draft["hold"] is None
    offer_id = draft["id"]
    r = world.client.post(f"{T}/offers/{offer_id}/confirm", headers=world.h(OWNER), json={})
    assert r.status_code == 403, r.text
    other = world.confirmation(OWNER, "task-offer:" + "f" * 32)
    r = world.client.post(f"{T}/offers/{offer_id}/confirm", headers=world.h(OWNER), json={"confirmation": other})
    assert r.status_code == 403 and world.get(OWNER, task_id)["state"] == "suspended"
    before = int(time.time())
    r = world.client.post(f"{T}/offers/{offer_id}/confirm", headers=world.h(OWNER),
                          json={"confirmation": world.confirmation(OWNER, f"task-offer:{offer_id}")})
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == "task.offer" and hold["takes_effect_at"] >= before + 180
    assert world.get(OWNER, task_id)["state"] == "offering"
    summary = world.row("ade_holds", "id", hold["id"])["summary"]
    assert offer_id in summary and task_id in summary and not _named(summary, MATE, world.user(MATE))
    assert [(e["kind"], e["payload"]["offer"], e["payload"]["why"]) for e in world.journal(task_id)] == [
        ("task.offered", offer_id, None)]
    assert world.notices(OWNER) == ["An offer of a task waits"] and world.ws.of("/push") == []

    world.running(OWNER, MATE)
    world.settle()
    assert world.ws.of("/push") == [(world.user(OWNER), {"address": ADDRESS, "commit": C2, "since": C0,
                                                          "call_id": f"handover:{offer_id}", "branch_head": C2,
                                                          "lifted": [],
                                                          "findings_key": task_offers.findings_key(world.user(OWNER))})]
    offer = world.offer(offer_id)
    assert offer["state"] == "offered" and offer["commit"] == C2 and offer["repo_address"] == ADDRESS
    assert offer["branch"] == f"pp/task-{task_id}" and world.ws.remote == {(ADDRESS, f"pp/task-{task_id}"): C2}
    assert shapes.DIGEST.fullmatch(offer["packet_digest"]) and offer["expires_at"] > before + 7 * 86400 - 10
    packet = json.loads(offer["packet"])
    assert packet["instructions"]["by"] == "teammate" and packet["instructions"]["from"] == OWNER
    assert packet["instructions"]["steps"] == [{"n": 1, "objective": "Write the parser"},
                                               {"n": 2, "objective": "Write the docs"}]
    assert packet["information"]["done_steps"] == [1] and "pytest" not in offer["packet"].decode()
    assert packet["information"]["outward_effects"] == [{"tool": "Bash", "kind": "git_remote", "target": "origin main",
                                                         "state": "done"}]
    assert world.notices(MATE) == ["A teammate offered you a task"]

    task_offers.drive()                                 # the teammate's workspace reads what it would load
    got = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()
    assert got["made"] == [] and [o["id"] for o in got["received"]] == [offer_id]
    seen = got["received"][0]
    assert seen["from_email"] == OWNER and seen["title"] == "Parse the config files" and seen["commit"] == C2
    assert seen["steps"] == [{"n": 1, "objective": "Write the parser", "check": "pytest -q tests/test_parser.py"},
                             {"n": 2, "objective": "Write the docs", "check": None}]
    # the task is on the owner's personal plan, and the teammate's agent on Y's seat: another group
    assert seen["loaded_changes"] == [] and seen["why"] is None and seen["other_group_agents"] == [mate_agent]
    words = " ".join(p["text"] for p in seen["terms"]["design"])
    assert f"If you accept, {OWNER}'s record" in words and OPERATOR in words and "{owner}" not in words
    assert "custody" not in words and seen["terms"]["point"] == 13
    # every part of no case and every one of an offer's cases, and the quotations of no case and of an offer's cases,
    # in the order point 13's text has them: the stand-in's above where risk_texts is a stub, its own words otherwise
    point = risk_texts.text(13)
    cases = (None, "other_group", "adopted_check", "offer")
    assert [p["case"] for p in seen["terms"]["design"]] == [p["case"] for p in point["design"] if p["case"] in cases]
    assert [q["id"] for q in seen["terms"]["quotations"]] == [q["id"] for q in point["quotations"]
                                                              if q["case"] in cases]
    assert {p["case"] for p in point["design"]} - set(cases) == {"custody"}     # what the offer's cases leave out

    r = world.client.post(f"{T}/offers/{offer_id}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    theirs = r.json()["task_id"]
    made = world.tasks.created[-1]
    assert made["task_id"] == theirs and made["user_id"] == world.user(MATE) and made["agent_id"] == mate_agent
    assert made["continued_from"] == {"task": task_id, "offer": offer_id, "packet_digest": offer["packet_digest"],
                                      "from_user": world.user(OWNER),
                                      # the owner's side as the offer's packet gave it; this
                                      # world's done step has no evidence, so it comes as the lesser one
                                      "handed": {"from": OWNER, "done_steps": [{"n": 1, "evidence": "said_done"}],
                                                 "outward_effects": packet["information"]["outward_effects"]}}
    assert made["fields"]["checkpoint0"] == C2 and [s["check"] for s in made["fields"]["steps"]] == [None, None]
    assert [d["author"] for d in made["fields"]["decisions"]] == ["teammate"]
    assert made["fields"]["backend"] == {"kind": "account", "id": "cccccccccccc"}
    assert "custody_cross" not in made["fields"]
    assert world.ws.of("/fetch", world.user(MATE))[-1][1] == {
        "address": ADDRESS, "commit": C2, "purpose": "accept", "agent": mate_agent, "base": None,
        "protected": ["tests/**"]}
    assert world.get(OWNER, task_id)["state"] == "handed_over"
    assert world.offer(offer_id)["state"] == "accepted" and world.offer(offer_id)["their_task"] == theirs
    record = world.record(task_id)
    assert [(e["kind"], e["via"], e["step"]) for e in record] == [("handoff", "offer", 1)]
    assert record[0]["accounts"] == [{"teammate": world.user(MATE), "plan": "Claude Team", "organization": "Yarra",
                                      "org_id": "org-y"}]
    assert not _named(json.dumps(record), MATE)
    assert [e["kind"] for e in world.journal(task_id)] == ["task.offered", "task.handed_over"]


def test_an_offer_cancelled_in_its_hold_pushes_nothing_and_its_task_is_back(world):
    """Cancelled inside the hold, nothing is pushed or offered, the task is as it was, and the offer keeps
    nothing of the teammate."""
    task_id, _ = setup_pressed(world)
    made = world.offer_to(task_id)
    holds.cancel(world.user(OWNER), made["hold"]["id"])
    offer = world.offer(made["offer"]["id"])
    assert offer["state"] == "withdrawn" and offer["teammate_user"] is None and offer["address"] is None
    assert offer["why"] == task_offers.CANCELLED
    task = world.get(OWNER, task_id)
    assert task["state"] == "suspended"
    world.running(OWNER, MATE)
    world.settle()
    assert world.ws.of("/push") == [] and world.notices(MATE) == []
    assert [e["kind"] for e in world.journal(task_id)] == ["task.offered", "task.offer_cancelled"]
    # Withdraw does the same through the route while the hold runs
    second = world.offer_to(task_id)
    r = world.client.post(f"{T}/offers/{second['offer']['id']}/withdraw", headers=world.h(OWNER), json={})
    assert r.status_code == 200 and r.json()["offer"]["state"] == "withdrawn" and r.json()["offer"]["to_email"] is None
    assert world.row("ade_holds", "id", second["hold"]["id"])["cancelled_at"] is not None
    assert world.get(OWNER, task_id)["state"] == "suspended"


def test_an_unknown_effect_or_no_recorded_address_refuses_the_offer_with_why(world):
    """An unknown effect, or a repository with no recorded address (a scratch repository), refused with
    why; so is a task that neither waits nor works."""
    task_id, _ = setup_pressed(world)
    world.effect(OWNER, task_id, "unknown")
    r = world.client.post(f"{T}/offers", headers=world.h(OWNER),
                          json={"task_id": task_id, "state_version": 0, "address": MATE})
    assert r.status_code == 412 and r.json()["detail"] == task_offers.UNKNOWN
    scratch = world.task(OWNER, world.agent(OWNER), address=None)
    r = world.client.post(f"{T}/offers", headers=world.h(OWNER),
                          json={"task_id": scratch, "state_version": 0, "address": MATE})
    assert r.status_code == 412 and r.json()["detail"] == task_offers.NO_ADDRESS
    held = world.task(OWNER, world.agent(OWNER), state="needs_owner")
    r = world.client.post(f"{T}/offers", headers=world.h(OWNER),
                          json={"task_id": held, "state_version": 0, "address": MATE})
    assert r.status_code == 412 and r.json()["detail"] == task_offers.NOT_WAITING
    r = world.client.post(f"{T}/offers", headers=world.h(OWNER),
                          json={"task_id": held, "state_version": 0, "address": OWNER})
    assert r.status_code == 412
    assert world.row("ade_agent_task_offers", "task_id", task_id) is None


def test_an_unreadable_repository_refuses_the_acceptance_and_brings_the_owners_task_back(world):
    """The teammate's sign-in cannot read the repository (the stand-in workspace refuses the fetch): the
    acceptance is refused, the offer ends failed, the owner's task is back, and nothing of the teammate is kept."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)
    world.ws.unreadable.add(world.user(MATE))
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    # the teammate is told their own workspace's words; the owner's offer, task and journal keep the app's own alone
    assert r.status_code == 412 and r.json()["detail"].endswith("This workspace's GitHub sign-in cannot read it.")
    offer = world.offer(made["offer"]["id"])
    assert offer["state"] == "failed" and offer["teammate_user"] is None and offer["address"] is None
    assert offer["why"] == task_offers.UNREADABLE and world.get(OWNER, task_id)["reason"] == task_offers.UNREADABLE
    assert world.get(OWNER, task_id)["state"] == "suspended" and world.tasks.created == []
    assert world.journal(task_id)[-1]["kind"] == "task.offer_failed"
    assert world.journal(task_id)[-1]["payload"]["why"] == task_offers.UNREADABLE


def test_an_acceptance_mirrors_the_receiving_agent_into_the_teammates_workspace_before_its_fetch(world):
    """An agent the teammate made while their workspace did not run (or before
    the app's next sync) is not in that workspace, whose fetch would then find no such agent, and the offer fail as
    unreadable. The acceptance mirrors the teammate's changed agents first, as a sync would."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    mate = world.user(MATE)
    calls = [(p, (b or {}).get("purpose")) for u, p, b in world.ws.calls if u == mate]
    assert (mate, mate_agent) in world.ws.mirrored
    assert calls.index((f"/agents/{mate_agent}", None)) < calls.index(
        (f"/agents/tasks/{r.json()['task_id']}/fetch", "accept"))


def test_an_acceptance_makes_the_worktree_under_the_received_tasks_own_id(world):
    """An acceptance's fetch that named the owner's task would have the teammate's
    workspace make the worktree at the offered commit under the owner's task's id, while the received task has an id of
    its own: its attempts would find no worktree of theirs and cut one from the repository's default branch, without
    the owner's work. The fetch names the received task's own id, which tasks.create gives it."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    theirs = r.json()["task_id"]
    assert theirs != task_id and world.tasks.created[-1]["task_id"] == theirs
    assert world.ws.fetched == {(world.user(MATE), theirs): world.offer(made["offer"]["id"])["commit"]}
    assert world.ws.removed == []


@pytest.mark.parametrize("status", [404, 409])
def test_the_teammates_own_workspace_refusing_for_now_refuses_the_acceptance_and_keeps_the_offer(world, status):
    """The teammate's workspace refuses the fetch for a state of its own (its agent not there yet, 404; an agent of
    theirs on that repository paused, held or working, or the task there already, 409): the acceptance is refused with
    their workspace's words, the offer stays theirs to accept, and the owner's task stays offered; once that state
    passes, they accept."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)
    offer_id = made["offer"]["id"]
    world.ws.refusing_fetch = (status, "An agent on that repository is paused or held: resume it, then try again.")
    r = world.client.post(f"{T}/offers/{offer_id}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 409 and r.json()["detail"].endswith("resume it, then try again."), r.text
    assert world.offer(offer_id)["state"] == "offered" and world.offer(offer_id)["why"] is None
    assert world.get(OWNER, task_id)["state"] == "offering" and world.tasks.created == []
    world.ws.refusing_fetch = None
    r = world.client.post(f"{T}/offers/{offer_id}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    assert world.offer(offer_id)["state"] == "accepted" and world.get(OWNER, task_id)["state"] == "handed_over"


def test_the_received_task_keeps_the_owners_side_and_starts_at_the_first_step_left(world):
    """A received task made with every step to do would have the teammate's first
    attempt do again the step the owner had done, and its packets name neither the owner nor what the owner had
    pushed. The acceptance gives tasks.create the owner's side as the offer's packet gave it: the
    owner's address, their outward effects, and their done steps, each with the evidence the owner's task has for it;
    the received task keeps the owner's done steps done, with that evidence, and starts at the first step left."""
    task_id, mate_agent = setup_pressed(world)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_task_steps SET evidence = 'checked' WHERE task_id = ? AND n = 1", (task_id,))
    world.effect(OWNER, task_id, state="done", kind="git_remote", target="origin main")
    made = offered(world, task_id)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    theirs = r.json()["task_id"]
    given = world.tasks.created[-1]["continued_from"]["handed"]
    assert given == {"from": OWNER, "done_steps": [{"n": 1, "evidence": "checked"}],
                     "outward_effects": [{"tool": "Bash", "kind": "git_remote", "target": "origin main",
                                          "state": "done"}]}
    task = world.get(MATE, theirs)
    assert task["step"] == 2 and task["continued_from"]["handed"] == given
    with store.connect() as db:
        steps = [(s["state"], s["evidence"]) for s in tasks.view(db, world.user(MATE), theirs)["steps"]]
    assert steps == [("done", "checked"), ("todo", None)]


@pytest.mark.parametrize("status", [500, 409])
def test_a_workspaces_own_words_never_reach_an_offers_why(world, status):
    """`why` is the app's own words, never an address or a user: a push the owner's
    workspace refuses in words of its own, which may name an address, ends the offer with the app's words alone, in the
    offer, the task's reason and the journal; a 409, the watch or a moved branch, holds the task for the owner."""
    task_id, _ = setup_pressed(world)
    world.ws.refusing = (status, "fatal: could not read from https://mate:token@github.com/acme/demo.git")
    made = world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    offer = world.offer(made["offer"]["id"])
    words = task_offers.PUSH_HELD if status == 409 else task_offers.PUSH_FAILED
    assert offer["state"] == "failed" and offer["why"] == words
    task = world.get(OWNER, task_id)
    assert task["reason"] == words and task["state"] == ("needs_owner" if status == 409 else "suspended")
    assert "github.com" not in json.dumps(world.journal()) and world.journal(task_id)[-1]["payload"]["why"] == words


@pytest.mark.parametrize("end", ["declined", "withdrawn", "expired"])
def test_an_offer_declined_withdrawn_or_expired_brings_the_task_back_and_names_the_teammate_no_more(world, end):
    """Declined, withdrawn after its hold, or not accepted in 7 days, the pushed branch stays on the
    remote, the owner's task is back, suspended, the offer is gone from the teammate's panel, and neither its row nor
    any journal entry names the teammate or the address the owner typed."""
    task_id, _ = setup_pressed(world)
    made = offered(world, task_id)
    offer_id = made["offer"]["id"]
    if end == "declined":
        r = world.client.post(f"{T}/offers/{offer_id}/decline", headers=world.h(MATE), json={})
        assert r.status_code == 200, r.text
    elif end == "withdrawn":
        r = world.client.post(f"{T}/offers/{offer_id}/withdraw", headers=world.h(OWNER), json={})
        assert r.status_code == 200, r.text
    else:
        world.forget(int(time.time()) + 7 * 86400 + 300)
    offer = world.offer(offer_id)
    assert offer["state"] == end and offer["teammate_user"] is None and offer["address"] is None
    assert world.get(OWNER, task_id)["state"] == "suspended"
    assert world.ws.remote == {(ADDRESS, f"pp/task-{task_id}"): C1}
    assert world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"] == []
    kinds = {"declined": "task.offer_declined", "withdrawn": "task.offer_cancelled", "expired": "task.offer_expired"}
    assert [e["kind"] for e in world.journal(task_id)] == ["task.offered", kinds[end]]
    assert not _named(json.dumps(world.journal()), MATE, world.user(MATE))
    r = world.client.post(f"{T}/offers/{offer_id}/accept", headers=world.h(MATE),
                          json={"agent_id": world.agent(MATE, account="cccccccccccc"), "adopt_checks": []})
    assert r.status_code == 404


@pytest.mark.parametrize("cause, until", [("window", 2 * HOUR), ("step_done", None)])
def test_an_offer_ended_without_acceptance_leaves_the_task_waiting_as_it_waited(world, cause, until):
    """The task is back as it was, suspended: a task that
    waited for a window's reset when it was offered waits for that reset again once the offer is declined, so it
    resumes then as it would have; one that waited after a step waits so again. How it waited is the journal's newest
    task.suspended entry, not an older one."""
    task_id, _ = setup_pressed(world)
    now = int(time.time())
    reset = now + until if until else None
    older = ("step_done", None) if cause == "window" else ("window", now + 3 * HOUR)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET cause = ?, waits_until = ? WHERE id = ?", (cause, reset, task_id))
        for was, was_until in (older, (cause, reset)):      # an older wait, then the one the task waits in now
            tasks.journal(db, world.user(OWNER), task_id, "task.suspended",
                          {"state": "suspended", "cause": was, "until": was_until}, now=now)
    made = offered(world, task_id)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/decline", headers=world.h(MATE), json={})
    assert r.status_code == 200, r.text
    task = world.get(OWNER, task_id)
    assert (task["state"], task["cause"], task["waits_until"], task["reason"]) == (
        "suspended", cause, reset, task_offers.DECLINED)


def test_an_offer_is_accepted_only_into_an_agent_on_the_tasks_repository_with_no_open_task(world):
    """An offer is accepted into one of their agents whose repository is the task's and which has no open task: an
    agent on another repository, or one with an open task, is refused with why, nothing is made, and the offer still
    waits."""
    task_id, _ = setup_pressed(world)
    made = offered(world, task_id)
    elsewhere = world.agent(MATE, repo="other", account="cccccccccccc", name="Elsewhere")
    busy = world.agent(MATE, account="cccccccccccc", name="Busy")
    world.task(MATE, busy, account="cccccccccccc")
    for agent_id, words in ((elsewhere, "That agent's repository is not this task's."),
                            (busy, "That agent has a task that has not ended.")):
        r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                              json={"agent_id": agent_id, "adopt_checks": []})
        assert r.status_code == 412 and r.json()["detail"] == words
    assert world.tasks.created == [] and world.offer(made["offer"]["id"])["state"] == "offered"
    # refused before the teammate's workspace was asked to fetch anything for that agent
    assert [b for _, b in world.ws.of("/fetch", world.user(MATE)) if b["purpose"] == "accept"] == []


@pytest.mark.parametrize("change", ["account", "retired", "track_off"])
def test_an_acceptance_reads_the_receiving_agent_and_the_track_again_in_its_transaction(world, change):
    """The acceptance's transaction: the agent's account no longer confirmed, the agent retired,
    or the track turned off, while the teammate's workspace fetched, outside any transaction, refuses the acceptance
    with why, rather than breaking, and makes nothing."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)

    def meanwhile(body):
        if body["purpose"] != "accept":
            return
        if change == "track_off":
            world.mp.setenv("OPEN_EDITION_ADE_TASK_TEAMMATE", "off")
            return
        with store.connect() as db:
            if change == "account":
                db.execute("UPDATE ade_account_owners SET state = 'changed' WHERE user_id = ?", (world.user(MATE),))
            else:
                db.execute("UPDATE ade_agents SET retired_at = 1 WHERE id = ?", (mate_agent,))
    world.ws.during = meanwhile
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    status, words = {"account": (412, "not one you confirmed"), "retired": (404, "No such agent."),
                     "track_off": (409, task_offers.OFF_SETTING)}[change]
    assert r.status_code == status and words in r.json()["detail"]
    assert world.tasks.created == [] and world.get(OWNER, task_id)["state"] == "offering"
    # the worktree its fetch made, of which no task came, is removed from the teammate's workspace
    (fetched,) = [p.split("/")[3] for u, p, b in world.ws.calls if p.endswith("/fetch") and b["purpose"] == "accept"]
    assert world.ws.removed == [(world.user(MATE), fetched)] and world.ws.fetched == {}


def test_an_agent_that_names_no_account_receives_nothing(world):
    """An agent that names no account runs on whichever account its user's new sessions start on (ade/agents.py keeps
    "default" and none alike as none), which the app cannot name: it is refused, saying so, at an
    offer's acceptance and at custody's, even where its user confirmed `default`, and nothing is made on it."""
    task_id, _ = setup_pressed(world)
    world.account(MATE, "default", "Claude Max", position=1)
    plain = world.agent(MATE, account=None, name="Plain")
    made = offered(world, task_id)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": plain, "adopt_checks": []})
    assert r.status_code == 412 and r.json()["detail"] == task_offers.NO_ACCOUNT
    assert world.tasks.created == [] and world.offer(made["offer"]["id"])["state"] == "offered"
    custody_id = ask_custody(world, owner=OTHER)
    r = world.client.post(f"{T}/custody/{custody_id}/accept", headers=world.h(MATE),
                          json={"agents": [plain],
                                "confirmation": world.confirmation(MATE, f"task-custody-accept:{custody_id}")})
    assert r.status_code == 412 and r.json()["detail"].startswith("Plain names no account, so it runs on")
    assert world.custody(custody_id)["state"] == "asked"


@pytest.mark.parametrize("how", ["silent", "not_started"])
def test_a_hand_off_whose_owners_workspace_fails_it_ends_at_once(world, how):
    """If a condition no longer holds, or the push or the fetch fails, the hand-off ends failed: the
    owner's workspace not answering the push, or one the provider will not start, ends the offer failed at once, in
    words true of which, nothing pushed or told, and the task back."""
    task_id, _ = setup_pressed(world)
    made = world.offer_to(task_id)
    if how == "silent":
        world.ws.silent.add(world.user(OWNER))
        world.running(OWNER)
    else:
        world.provider.refusing.add(world.user(OWNER))
    world.settle()
    offer = world.offer(made["offer"]["id"])
    assert offer["state"] == "failed" and offer["teammate_user"] is None
    assert offer["why"] == (task_offers.SILENT if how == "silent" else task_offers.NO_START)
    assert world.get(OWNER, task_id)["state"] == "suspended" and world.ws.remote == {} and world.notices(MATE) == []


@pytest.mark.parametrize("change", ["track_off", "task_changed"])
def test_an_offer_is_checked_again_when_the_round_comes_to_push_it(world, change):
    """At the hold's end every condition is checked again, and the round that pushes may come later than
    that (it waits on one workspace call at a time), so it checks them once more: the track turned off, or the task
    changed, between the hold's end and the round, ends the offer failed with why, nothing pushed and nobody told."""
    task_id, _ = setup_pressed(world)
    made = world.offer_to(task_id)
    world.running(OWNER, MATE)
    now = int(time.time()) + 200
    holds.settle(world.user(OWNER), now)                # the hold ends and its check passes; no round yet
    assert world.offer(made["offer"]["id"])["state"] == "held"
    if change == "track_off":
        world.mp.setenv("OPEN_EDITION_ADE_TASK_TEAMMATE", "off")
    else:
        with store.connect() as db:
            db.execute("UPDATE ade_agent_tasks SET state_version = state_version + 1 WHERE id = ?", (task_id,))
    task_offers.drive(now)
    offer = world.offer(made["offer"]["id"])
    words = task_offers.OFF_SETTING if change == "track_off" else task_offers.NOT_AS_OFFERED
    assert offer["state"] == "failed" and offer["why"] == words
    assert world.ws.of("/push") == [] and world.notices(MATE) == []


def test_an_offer_whose_track_turned_off_while_it_was_pushed_is_not_offered(world):
    """The push takes time (up to PUSH_WAIT), so the transaction that records it checks every condition once more: the
    track turned off while the owner's workspace pushed ends the offer failed, saying so, and the teammate is not told,
    the pushed branch left on the remote as for any offer that failed."""
    task_id, _ = setup_pressed(world)
    made = world.offer_to(task_id)

    def meanwhile(body):
        if body["purpose"] == "push":
            world.mp.setenv("OPEN_EDITION_ADE_TASK_TEAMMATE", "off")
    world.ws.during = meanwhile
    world.running(OWNER, MATE)
    world.settle()
    offer = world.offer(made["offer"]["id"])
    assert offer["state"] == "failed" and offer["why"] == task_offers.OFF_SETTING and offer["teammate_user"] is None
    assert world.notices(MATE) == [] and world.ws.remote == {(ADDRESS, f"pp/task-{task_id}"): C1}


def test_the_tracks_reads_apply_the_users_holds_whose_time_has_come(world):
    """As the tasks' reads do: GET /offers applies the user's holds whose time has come before it answers, so an offer
    is not shown held, with Cancel, past its time; GET /custody does the same, custody's crossing among them."""
    task_id, _ = setup_pressed(world)
    made = world.offer_to(task_id)
    later = int(time.time()) + 200
    got = task_offers.offers(world.u(OWNER), now=later)
    assert world.row("ade_holds", "id", made["hold"]["id"])["done_at"] == later and got["made"][0]["hold"] is None
    other = world.task(OWNER, world.agent(OWNER))
    r = world.client.post(f"{T}/tasks/{other}/custody-cross", headers=world.h(OWNER),
                          json={"state_version": world.get(OWNER, other)["state_version"],
                                "confirmation": world.confirmation(OWNER, f"task-custody-cross:{other}")})
    assert r.status_code == 200 and world.get(OWNER, other)["custody_cross"] == 0
    task_offers.custody(world.u(OWNER), now=r.json()["hold"]["takes_effect_at"])
    assert world.get(OWNER, other)["custody_cross"] == 1


def test_an_offer_to_an_address_that_is_no_ones_answers_the_same_and_reaches_nobody(world):
    """The immediate answer is the one a teammate's address gets; past its hold nothing is pushed and nothing
    reaches anyone; it waits, then expires, and the task is back."""
    task_id, _ = setup_pressed(world)
    other_task = world.task(OWNER, world.agent(OWNER))
    someone = world.offer_to(task_id, confirm=False)
    nobody = world.offer_to(other_task, address="nobody@example.com", confirm=False)
    same = ("id", "task_id", "branch", "to_email", "created_at", "expires_at")     # each the task's or the typed
    assert {k: v for k, v in someone.items() if k not in same} == {k: v for k, v in nobody.items() if k not in same}
    assert nobody["to_email"] == "nobody@example.com"
    r = world.client.post(f"{T}/offers/{nobody['id']}/confirm", headers=world.h(OWNER),
                          json={"confirmation": world.confirmation(OWNER, f"task-offer:{nobody['id']}")})
    assert r.status_code == 200
    world.running(OWNER)
    world.settle()
    offer = world.offer(nobody["id"])
    assert offer["state"] == "offered" and offer["commit"] is None and world.ws.of("/push") == []
    assert [u for u, _ in world.told if u != world.user(OWNER)] == []
    world.forget(int(time.time()) + 7 * 86400 + 300)
    assert world.offer(nobody["id"])["state"] == "expired" and world.get(OWNER, other_task)["state"] == "suspended"


def test_an_offer_on_a_working_task_begins_only_from_an_end_that_leaves_it_waiting(world):
    """The press on a working task asks for the offer and stops the attempt as Stop does; the offer begins only
    in the transaction that copies the attempt's end, when that end leaves the task waiting and holding nothing;
    otherwise it is refused, with why, and the request is gone. ade/tasks.py may call after_attempt_end at any end:
    with no offer asked for, nothing changes."""
    task_id, _ = setup_pressed(world, owner_state="running")
    made = world.offer_to(task_id)
    offer_id = made["offer"]["id"]
    assert made["hold"] is None and world.offer(offer_id)["state"] == "held"
    assert world.offer(offer_id)["hold_id"] is None
    assert world.get(OWNER, task_id)["state"] == "running"
    assert world.stops == [(world.user(OWNER), "bbbbbbbbbbbb")]
    now = int(time.time())
    owner = world.user(OWNER)
    tells = []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        tasks.set_state(db, owner, task_id, "suspended", cause="stopped", now=now)
        assert task_offers.after_attempt_end(db, owner, task_id, "bbbbbbbbbbbb", now, tells=tells) is True
    assert world.get(OWNER, task_id)["state"] == "offering"
    hold_id = world.offer(offer_id)["hold_id"]
    assert hold_id and world.journal(task_id)[-1]["kind"] == "task.offered"
    # the hold placed in the tasks module's transaction goes among what that end leaves to tell, which it tells once it
    # has committed, as it tells a custody hand-off's; the round then tells it no more
    assert [(kind, hold["id"]) for kind, hold in tells] == [("hold", hold_id)] and world.notices(OWNER) == []
    tasks.deliver(owner, tells)
    task_offers.drive(now + task_offers.TOLD_GRACE + 1)
    task_offers.drive(now + task_offers.TOLD_GRACE + 2)
    assert world.notices(OWNER) == ["An offer of a task waits"]

    second = world.task(OWNER, world.agent(OWNER), state="running", lease_run="dddddddddddd")
    asked = world.offer_to(second)["offer"]["id"]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        tasks.set_state(db, owner, second, "needs_owner", cause="effect", now=now)
        assert task_offers.after_attempt_end(db, owner, second, "dddddddddddd", now) is False
        assert task_offers.after_attempt_end(db, owner, second, "dddddddddddd", now) is False   # none asked for now
    offer = world.offer(asked)
    assert offer["state"] == "failed" and offer["why"] == task_offers.NOT_ASKED and offer["teammate_user"] is None
    assert world.get(OWNER, second)["state"] == "needs_owner"
    # an end that finished the task: the offer did not begin, and says so in words true of it
    third = world.task(OWNER, world.agent(OWNER), state="running", lease_run="cccccccccccc")
    finished = world.offer_to(third)["offer"]["id"]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        tasks.set_state(db, owner, third, "done", now=now)
        assert task_offers.after_attempt_end(db, owner, third, "cccccccccccc", now) is False
    assert world.offer(finished)["why"] == task_offers.NOT_ASKED_ENDED and world.get(OWNER, third)["state"] == "done"


def test_an_offers_hold_whose_telling_never_ran_is_told_by_the_round_once_its_grace_has_passed(world):
    """Where the tasks module's telling of the hold after_attempt_end placed never ran (its tells dropped: the
    process stopped between the end's commit and the telling, say), the round tells it, once; and only TOLD_GRACE
    seconds after it was placed, since inside that time a telling may be under way and the notice would go twice."""
    task_id, _ = setup_pressed(world, owner_state="running")
    world.offer_to(task_id)
    now = int(time.time())
    owner = world.user(OWNER)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        tasks.set_state(db, owner, task_id, "suspended", cause="stopped", now=now)
        assert task_offers.after_attempt_end(db, owner, task_id, "bbbbbbbbbbbb", now, tells=[]) is True
    task_offers.drive(now + 4)
    task_offers.drive(now + task_offers.TOLD_GRACE - 1)
    assert world.notices(OWNER) == []
    task_offers.drive(now + task_offers.TOLD_GRACE + 1)
    task_offers.drive(now + task_offers.TOLD_GRACE + 2)
    assert world.notices(OWNER) == ["An offer of a task waits"]


@pytest.mark.parametrize("end, words", [("needs_owner", "NOT_ASKED"), ("done", "NOT_ASKED_ENDED"),
                                         ("stopped", "NOT_ASKED_ENDED"), ("suspended", "NOT_BEGUN")])
def test_an_offer_asked_for_whose_attempts_end_began_nothing_is_gone(world, end, words):
    """The working attempt ended and the transaction that copied it began no offer (the end held the task for
    its owner, or ended it, or after_attempt_end was not asked): the request is gone, ended failed by the next round
    with why in words true of how the end left the task, the teammate and the typed address cleared; while the attempt
    still works, it stays."""
    task_id, _ = setup_pressed(world, owner_state="running")
    offer_id = world.offer_to(task_id)["offer"]["id"]
    task_offers.drive()
    assert world.offer(offer_id)["state"] == "held"
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET state = ?, lease_run = NULL WHERE id = ?", (end, task_id))
    task_offers.drive()
    offer = world.offer(offer_id)
    assert offer["state"] == "failed" and offer["why"] == getattr(task_offers, words)
    assert offer["teammate_user"] is None and offer["address"] is None
    assert world.get(OWNER, task_id)["state"] == end
    assert world.journal(task_id)[-1]["kind"] == "task.offer_failed"


def test_a_hold_whose_task_changed_or_whose_track_turned_off_ends_the_offer_failed(world, monkeypatch):
    """At the hold's end the app checks again: a task changed during the hold (a change adds to its
    state_version), or the teammate setting turned off, ends the offer failed, with why, nothing pushed, the task
    back."""
    task_id, _ = setup_pressed(world)
    made = world.offer_to(task_id)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET state_version = state_version + 1 WHERE id = ?", (task_id,))
    world.running(OWNER, MATE)
    world.settle()
    offer = world.offer(made["offer"]["id"])
    assert offer["state"] == "failed" and offer["why"] == task_offers.NOT_AS_OFFERED and offer["teammate_user"] is None
    assert world.row("ade_holds", "id", made["hold"]["id"])["failed"] == task_offers.NOT_AS_OFFERED
    assert world.get(OWNER, task_id)["state"] == "suspended" and world.ws.of("/push") == []
    second = world.task(OWNER, world.agent(OWNER))
    made = world.offer_to(second)
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_TEAMMATE", "off")
    world.settle()
    assert world.offer(made["offer"]["id"])["why"] == task_offers.OFF_SETTING and world.ws.of("/push") == []
    assert world.get(OWNER, second)["state"] == "suspended"


def test_a_moved_branch_or_a_secret_shape_holds_the_offer_for_the_owner_and_nothing_is_pushed(world):
    """A branch something else moved since the recorded checkpoint, and a secret shape the scan finds,
    each hold the offer for the owner (needs_owner, cause handoff), naming the shape and the file, never the value;
    nothing reaches the remote or the teammate."""
    task_id, _ = setup_pressed(world)
    world.ws.heads[task_id] = C3
    made = world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    assert world.ws.of("/push")[0][1]["branch_head"] == C1 and world.ws.remote == {}
    offer = world.offer(made["offer"]["id"])
    task = world.get(OWNER, task_id)
    assert offer["state"] == "failed" and "moved" in offer["why"] and task["state"] == "needs_owner"
    assert task["cause"] == "handoff" and task["reason"] == offer["why"]
    second = world.task(OWNER, world.agent(OWNER))
    world.ws.secret = {"shape": "an AWS access key", "file": "config/prod.yml"}
    made = world.offer_to(second)
    world.settle()
    offer = world.offer(made["offer"]["id"])
    assert "an AWS access key in config/prod.yml" in offer["why"] and world.ws.remote == {}
    assert world.get(OWNER, second)["state"] == "needs_owner" and world.notices(MATE) == []


def test_a_packet_past_its_bound_ends_the_offer_before_anything_is_pushed(world):
    """The packet's bound (task_shapes.PACKET_MAX): a hand-off whose packet would pass it
    ends, held for the owner, before the owner's workspace pushes anything; and a draft withdrawn before its
    confirmation is answered withdrawn, nothing kept of it."""
    task_id, _ = setup_pressed(world)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET goal = ? WHERE id = ?", ("x" * (shapes.PACKET_MAX + 1), task_id))
    made = world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    offer = world.offer(made["offer"]["id"])
    assert offer["state"] == "failed" and offer["why"] == task_offers.PACKET_TOO_BIG
    assert world.ws.calls == [] and world.get(OWNER, task_id)["state"] == "needs_owner"
    draft = world.offer_to(world.task(OWNER, world.agent(OWNER)), confirm=False)
    r = world.client.post(f"{T}/offers/{draft['id']}/withdraw", headers=world.h(OWNER), json={})
    assert r.status_code == 200 and r.json()["offer"]["state"] == "withdrawn" and r.json()["offer"]["to_email"] is None
    assert world.offer(draft["id"]) is None


def test_the_owners_check_reaches_the_teammate_only_where_they_adopt_it(world):
    """The teammate's workspace never runs the owner's check unless the teammate adopted it, shown word for
    word: the received task's steps carry none but the adopted one; a step with no check has none to adopt."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)
    offer_id = made["offer"]["id"]
    r = world.client.post(f"{T}/offers/{offer_id}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": [2]})
    assert r.status_code == 400 and world.tasks.created == []
    r = world.client.post(f"{T}/offers/{offer_id}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": [1]})
    assert r.status_code == 200, r.text
    assert [s["check"] for s in world.tasks.created[-1]["fields"]["steps"]] == ["pytest -q tests/test_parser.py", None]
    assert json.loads(world.offer(offer_id)["checks_adopted"]) == [1]


def test_the_teammate_sees_the_packets_decisions_protected_paths_and_information_before_accepting(world):
    """The teammate sees the packet as the
    owner's words and information: a received offer carries what the packet kept with it holds, read from that
    packet, which its digest covers: the owner's decisions, protected paths and external commands, and as information
    the steps done and the outward effects, an effect that is not outward left out as the packet leaves it. The
    owner's view of the same offer carries none, nor the teammate's task's state; an offer with no packet kept
    carries none."""
    world.account(OWNER, "aaaaaaaaaaaa", "Claude Max")
    task_id = world.task(OWNER, world.agent(OWNER), decisions=("Use the standard library only.", "Keep the old API."),
                         protected=("tests/**", "migrations/**"), external=("make deploy",))
    world.account(MATE, "cccccccccccc", "Claude Team", "Yarra", "org-y")
    world.agent(MATE, account="cccccccccccc", name="Helper")
    world.effect(OWNER, task_id, "done", kind="git_remote", target="origin main")
    world.effect(OWNER, task_id, "done", kind="local_write", target="src/parser.py")
    made = world.offer_to(task_id)
    with store.connect() as db:
        held = task_offers._offer_view(db, task_offers._offer_row(db, made["offer"]["id"]), received=True)
    assert held["packet"] is None                       # in its hold: no packet kept yet
    world.running(OWNER, MATE)
    world.settle()
    task_offers.drive()
    # changed after the packet was kept: what the teammate sees is the packet's, not the task's now
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET external = ? WHERE id = ?", (json.dumps(["rm -rf build"]), task_id))
    (seen,) = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"]
    assert seen["packet"] == {"decisions": [{"n": 1, "text": "Use the standard library only."},
                                            {"n": 2, "text": "Keep the old API."}],
                              "protected": ["tests/**", "migrations/**"], "external": ["make deploy"],
                              "done_steps": [1],
                              "outward_effects": [{"tool": "Bash", "kind": "git_remote", "target": "origin main",
                                                   "state": "done"}]}
    assert (seen["their_state"], seen["their_step"]) == (None, None)
    (mine,) = world.client.get(f"{T}/offers", headers=world.h(OWNER)).json()["made"]
    assert (mine["packet"], mine["their_state"], mine["their_step"]) == (None, None, None)


@pytest.mark.parametrize("theirs_now, word, step", [
    ("running", "running", 2), ("ready", "waiting", 2), ("suspended", "waiting", 2), ("needs_owner", "waiting", 2),
    ("done", "done", None), ("stopped", None, None), ("deleted", None, None), ("returned", None, None)])
def test_the_owner_sees_the_teammates_task_running_waiting_or_done_and_its_step(world, theirs_now, word, step):
    """The owner sees its step, and running,
    waiting or done: the owner's accepted offer carries the teammate's task's state in three words, waiting standing
    for ready, suspended and needs_owner alike, and its step while it is not done; nothing for a task of theirs
    stopped or deleted, whose way back is on its way, a deleted one that was done among them,
    nor once the offer is no longer accepted (returned). Nothing else of their task: beyond the members
    the view had, it gains these two and the packet's own alone."""
    task_id, offer_id, theirs = accepted(world)
    assert world.get(MATE, theirs)["step"] == 2         # step 1 the owner's, done before the hand-off
    now = int(time.time())
    with store.connect() as db:
        if theirs_now == "deleted":
            db.execute("UPDATE ade_agent_tasks SET deleted_at = ?, state = 'done' WHERE id = ?", (now, theirs))
        elif theirs_now == "returned":
            db.execute("UPDATE ade_agent_task_offers SET state = 'returned' WHERE id = ?", (offer_id,))
            db.execute("UPDATE ade_agent_tasks SET state = 'running' WHERE id = ?", (theirs,))
        else:
            db.execute("UPDATE ade_agent_tasks SET state = ? WHERE id = ?", (theirs_now, theirs))
    (mine,) = world.client.get(f"{T}/offers", headers=world.h(OWNER)).json()["made"]
    assert (mine["their_state"], mine["their_step"]) == (word, step)
    with store.connect() as db:
        received = task_offers._offer_view(db, task_offers._offer_row(db, offer_id), received=True)
    assert (received["their_state"], received["their_step"]) == (None, None)
    assert set(mine) == {"id", "kind", "state", "task_id", "title", "goal", "steps", "from_email", "to_email",
                         "repo_slug", "branch", "commit", "packet_digest", "loaded_changes", "hold", "why",
                         "created_at", "expires_at", "terms", "other_group_agents", "packet", "their_state",
                         "their_step"}


def test_the_files_a_next_run_would_load_are_shown_before_an_offer_can_be_accepted(world):
    """The offered commit adds a hook in .claude/settings.json: the offer lists it, as the teammate's own
    workspace read it against the repository's default branch, and cannot be accepted before it has been shown."""
    task_id, mate_agent = setup_pressed(world)
    world.ws.loaded[C1] = [".claude/settings.json"]
    made = world.offer_to(task_id)
    world.running(OWNER)
    world.settle()                                      # the teammate's workspace is not running: nothing read
    seen = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"][0]
    assert seen["loaded_changes"] == [] and seen["why"] == task_offers.NOT_READ
    world.running(MATE)
    for _ in range(2):                                  # refused again: what was shown is what GET /offers gave
        r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                              json={"agent_id": mate_agent, "adopt_checks": []})
        assert r.status_code == 409 and r.json()["detail"] == task_offers.SHOWN_OTHERS and world.tasks.created == []
    seen = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"][0]
    assert seen["loaded_changes"] == [".claude/settings.json"] and seen["why"] is None
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    # each refused acceptance's fetch made a worktree that no task names, and it was removed, so that the next could be
    # taken; the accepted one is the received task's own
    mate = world.user(MATE)
    ids = [p.split("/")[3] for u, p, b in world.ws.calls
           if u == mate and p.endswith("/fetch") and b["purpose"] == "accept"]
    assert len(set(ids)) == 3 and ids[-1] == r.json()["task_id"]
    assert world.ws.removed == [(mate, i) for i in ids[:2]] and world.ws.fetched == {(mate, ids[-1]): C1}


def test_an_acceptance_whose_list_is_not_the_view_s_is_refused_once_and_then_taken(world):
    """The teammate's workspace compares the app's view, which names no
    agent, with the remote's default branch, and an acceptance with the receiving agent's own start, so the two lists
    differ where that start differs from the default branch in a file a next run
    would load. The first acceptance is refused and its list shown; a round of the module, which reads the offers'
    views again, and a GET /offers do not put the view's list back; the next acceptance is taken."""
    task_id, mate_agent = setup_pressed(world)
    world.ws.loaded[C1] = [".claude/settings.json"]
    world.ws.loaded_accept[C1] = [".claude/settings.json", "CLAUDE.md"]
    made = offered(world, task_id)
    seen = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"][0]
    assert seen["loaded_changes"] == [".claude/settings.json"] and seen["why"] is None

    def press():
        return world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                                 json={"agent_id": mate_agent, "adopt_checks": []})
    r = press()
    assert r.status_code == 409 and r.json()["detail"] == task_offers.SHOWN_OTHERS and world.tasks.created == []
    world.settle()
    task_offers.drive()
    seen = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"][0]
    assert seen["loaded_changes"] == [".claude/settings.json", "CLAUDE.md"] and seen["why"] is None
    r = press()
    assert r.status_code == 200, r.text
    # the view was read once, naming no agent: the round after the refusal passed over the offer whose list it set
    views = [b for _, b in world.ws.of("/fetch", world.user(MATE)) if b["purpose"] == "view"]
    assert len(views) == 1 and views[0]["agent"] is None, views


def test_a_received_task_can_be_neither_offered_on_nor_confirmed_for_custody(world):
    """The teammate of a pressed offer can only send it back where it came from."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    theirs = r.json()["task_id"]
    version = world.get(MATE, theirs)["state_version"]
    r = world.client.post(f"{T}/offers", headers=world.h(MATE),
                          json={"task_id": theirs, "state_version": version, "address": THIRD})
    assert r.status_code == 412 and r.json()["detail"] == task_offers.RECEIVED
    for route in ("custody-confirm", "custody-cross"):
        change = f"task-{route}:{theirs}"
        r = world.client.post(f"{T}/tasks/{theirs}/{route}", headers=world.h(MATE),
                              json={"state_version": version, "confirmation": world.confirmation(MATE, change)})
        assert r.status_code == 412 and r.json()["detail"] == task_offers.RECEIVED, route


def test_an_acceptance_into_a_personal_plan_writes_its_plan_and_no_organisation(world):
    """An offer accepted into an agent on the teammate's personal plan: the owner's record holds their plan
    and no organisation, from when it is written."""
    task_id, mate_agent = setup_pressed(world, mate_plan="Claude Pro", mate_org="Mate's own", mate_org_id="org-m")
    made = offered(world, task_id)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    assert world.record(task_id)[0]["accounts"] == [{"teammate": world.user(MATE), "plan": "Claude Pro",
                                                     "organization": None, "org_id": None}]


def test_an_acceptance_into_another_group_is_named_for_point_1s_clauses(world):
    """The received offer names the teammate's agents whose account is in another group than the task's, for
    which the acceptance opens point 1's clauses (case other_group); one in the task's group is not named."""
    task_id, seat_agent = setup_pressed(world)          # the task on a personal plan; the agent on Y's seat
    world.account(MATE, "eeeeeeeeeeee", "Claude Max")
    personal_agent = world.agent(MATE, account="eeeeeeeeeeee", name="Personal")
    offered(world, task_id)
    seen = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"][0]
    assert seen["other_group_agents"] == [seat_agent] and personal_agent not in seen["other_group_agents"]


# ---- custody --------------------------------------------------------------------------------------------------------

def confirmed_again(w, email, account, plan, org, org_id):
    """The user's account confirmed again under a new identity, its row as confirm_account leaves it, its hold settled
    (tasks._account_hold_applied): onto a seat of `org_id`, or onto the personal plans where it is None."""
    report = {"email": email, "organization": org, "plan": plan, "token_source": None, "api_key_source": None,
              "api_provider": "firstParty"}
    now = int(time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        owner_id = db.execute("SELECT id FROM ade_account_owners WHERE user_id = ? AND account = ?",
                              (w.user(email), account)).fetchone()["id"]
        hold = holds.place(db, w.user(email), tasks.HOLD_ACCOUNT, owner_id, "an account confirmed again", now)
        db.execute("UPDATE ade_account_owners SET report = ?, org_id = ?, state = 'held', hold_id = ?, pending = ?, "
                   "confirmed_at = ? WHERE id = ?", (json.dumps(report), org_id, hold["id"],
                                                     json.dumps(dict(report, org_id=org_id)), now, owner_id))
    holds.settle(w.user(email), hold["takes_effect_at"] + 1)
    owner = w.row("ade_account_owners", "id", owner_id)
    assert (owner["state"], owner["grp"]) == ("confirmed", "org:" + org_id if org_id else "personal")


def test_an_offer_waiting_when_the_owners_account_is_confirmed_into_another_group_is_still_accepted(world):
    """A confirmation's hold that put the offered task on the account's new group with a
    change that added 1 to its state_version, the offer's fence, would have the teammate's acceptance refused, "This
    offer no longer waits for you.", and the offer stay offered, saying nothing why, until it expired."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)
    before = world.row("ade_agent_tasks", "id", task_id)
    assert before["state"] == "offering" and world.offer(made["offer"]["id"])["fence"] == before["state_version"]
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Enterprise", "Zed", "org-z")
    after = world.row("ade_agent_tasks", "id", task_id)
    assert (after["state"], after["state_version"], after["crossings_ended"], after["bound_org"]) == (
        "offering", before["state_version"], 0, None)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    assert world.get(OWNER, task_id)["state"] == "handed_over"
    # the offered task is told whose
    # usage its attempts on the account run on, and why its binding stayed; and the teammate's received task, on their
    # own account, is told when theirs is confirmed into another group, the owner's task not
    regrouped = lambda tid: [e["payload"] for e in world.journal(tid) if e["kind"] == "task.account_regrouped"]
    assert [(x["to_grp"], x["left_out"], x["bound"]) for x in regrouped(task_id)] == [("org:org-z", "offered", {})]
    assert [e["kind"] for e in world.record(task_id) if e["kind"] == "regrouped"] == ["regrouped"]
    theirs = world.offer(made["offer"]["id"])["their_task"]
    assert regrouped(theirs) == []
    confirmed_again(world, MATE, "cccccccccccc", "Claude Enterprise", "Wye", "org-w")
    # the received task gets the binding a press from Y's
    # Team seats onto W's Enterprise seat gives, as a task of the teammate's own would, so its crossings end and it
    # stays in the group it is on, W's seats, whose account its relay takes. Left out
    # ("received"), its relay set would be Y's seats, and from the Enterprise seat the relay would reach none of them,
    # so it would wait
    assert [(x["account"], x["from_grp"], x["to_grp"], x["left_out"], x["bound"]) for x in regrouped(theirs)] == [
        ("account:cccccccccccc", "org:org-y", "org:org-w", None, {"crossings_ended": 1})]
    assert regrouped(theirs)[0]["why"].endswith(
        " Its binding changed as a press from the old group onto the new one would. Its relay takes that account now.")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-y", 1)
    with store.connect() as db:
        assert tasks.relay_groups(db, tasks.get(db, world.user(MATE), theirs))[0] == ["org:org-w"]
    entry = [e for e in world.record(theirs) if e["kind"] == "regrouped"]
    assert [(e["user_id"], [a["org_id"] for a in e["accounts"]]) for e in entry] == [(world.user(MATE), ["org-y", "org-w"])]
    assert len(regrouped(task_id)) == 1


def test_an_account_confirmed_into_another_group_changes_only_its_owners_tasks(world):
    """Account ids are each workspace's own, so two users can each have an account of the
    same id; the hold's end changes the tasks of the account's owner alone."""
    world.account(OWNER, "aaaaaaaaaaaa", "Claude Max")
    world.account(MATE, "aaaaaaaaaaaa", "Claude Max")
    mine = world.task(OWNER, world.agent(OWNER))
    theirs = world.task(MATE, world.agent(MATE))
    version = world.row("ade_agent_tasks", "id", theirs)["state_version"]
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Enterprise", "Zed", "org-z")
    t = world.row("ade_agent_tasks", "id", mine)
    assert (t["crossings_ended"], t["bound_org"]) == (1, "org-z")
    t = world.row("ade_agent_tasks", "id", theirs)
    assert (t["crossings_ended"], t["bound_org"], t["state_version"]) == (0, None, version)
    # and its journal line and record entry on the owner's tasks alone
    lines = [e for e in world.journal() if e["kind"] == "task.account_regrouped"]
    assert [(e["user_id"], e["task_id"]) for e in lines] == [(world.user(OWNER), mine)]
    assert [e["kind"] for e in world.record(mine)] == ["regrouped"] and world.record(theirs) == []



@pytest.mark.parametrize("moved", [True, False])
@pytest.mark.parametrize("end", ["declined", "withdrawn", "expired", "accepted"])
def test_an_offered_task_s_press_is_dropped_as_its_offer_ends_where_its_account_was_confirmed_meanwhile(
        world, end, moved):
    """An offered task's press is dropped when the offer
    ends, accepted or not, where its account was confirmed into another group while the offer was out, not kept to
    outlive the offer. The owner's task waits with a press naming dddddddddddd (Next on it, kept for his
    next Continue), and is offered; that account confirmed again onto a Team seat while the offer is out, the task is
    left as it was, its press and its version, the offer's fence (tasks._presses_dropped marks the offer instead).
    Declined, withdrawn, expired or accepted, the offer ends, and the press goes with it (_drop_marked_press). The
    control: no confirmation meanwhile, and the press stays, however the offer ended."""
    asked = {"kind": "account", "id": "dddddddddddd"}
    task_id, mate_agent = setup_pressed(world, asked=json.dumps(asked))
    world.account(OWNER, "dddddddddddd", "Claude Pro", position=1)
    made = offered(world, task_id)
    offer_id = made["offer"]["id"]
    version = world.get(OWNER, task_id)["state_version"]
    if moved:
        confirmed_again(world, OWNER, "dddddddddddd", "Claude Team", "Tee", "org-t")
    during = world.get(OWNER, task_id)
    assert (during["state"], during["asked"], during["state_version"]) == ("offering", asked, version)
    assert (world.offer(offer_id)["press_dropped_at"] is not None) is moved
    if end == "expired":
        world.forget(int(time.time()) + 7 * 86400 + 300)
    else:
        route = {"declined": "decline", "withdrawn": "withdraw", "accepted": "accept"}[end]
        r = world.client.post(f"{T}/offers/{offer_id}/{route}", headers=world.h(OWNER if end == "withdrawn" else MATE),
                              json={"agent_id": mate_agent, "adopt_checks": []} if end == "accepted" else {})
        assert r.status_code == 200, r.text
    assert world.offer(offer_id)["state"] == end
    after = world.get(OWNER, task_id)
    assert (after["state"], after["asked"]) == ("handed_over" if end == "accepted" else "suspended",
                                                None if moved else asked)



@pytest.mark.parametrize("way", ["out", "in", "out and back"])
@pytest.mark.parametrize("end", ["declined", "withdrawn", "expired", "accepted"])
def test_an_offered_task_is_bound_as_its_offer_ends_where_its_organisation_was_read_otherwise_meanwhile(world, end,
                                                                                                       way):
    """A confirmation that changes how an organisation is read leaves an
    offered task on another of its seats as it is, its version the offer's fence, and marks its offer (`reread`); as
    the offer ends, untaken or accepted, the task is bound as a press from the reading then onto the reading now binds
    it (its crossings end), with a journal line saying which end and why, so that no relay takes it onto a personal plan
    by itself once the organisation no longer reads as an Enterprise one. The owner's task on eeeeeeeeeeee, a Team seat
    of org-z, homed and bound there. The way out: org-z holds ffffffffffff, an Enterprise seat, too, and it is confirmed
    again as a Team seat. The way in: ffffffffffff, a personal plan, confirmed again as an Enterprise seat of org-z. Out
    and back: the way out, then ffffffffffff confirmed again as an Enterprise seat, so that org-z reads at the end as it
    did, and the task is left as it was. The teammate's agent is on a personal plan of theirs, so that an acceptance's
    own binding, which follows the teammate's task (_bind_as_custody), gives the owner's task nothing: what binds it
    when the offer is accepted is the reread's (were an accepted offer's lost, the
    owner's task would come back with its crossings going on)."""
    task_id, mate_agent = setup_pressed(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                        account="eeeeeeeeeeee", home="org:org-z", bound_org="org-z")
    world.account(OWNER, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=1)
    if way == "in":
        world.account(OWNER, "ffffffffffff", "Claude Pro", position=2)
    else:
        world.account(OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z", position=2)
    made = offered(world, task_id)
    offer_id = made["offer"]["id"]
    version = world.get(OWNER, task_id)["state_version"]
    if way == "in":
        confirmed_again(world, OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z")
    else:
        confirmed_again(world, OWNER, "ffffffffffff", "Claude Team", "Zed", "org-z")
        if way == "out and back":
            confirmed_again(world, OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z")
    during = world.get(OWNER, task_id)
    assert (during["state"], during["state_version"], during["crossings_ended"]) == ("offering", version, 0)
    mark = json.loads(world.offer(offer_id)["reread"])
    assert mark == [{"grp": "org:org-z", "read_from": "team" if way == "in" else "enterprise",
                     "by": "account:ffffffffffff"}], mark
    if end == "expired":
        world.forget(int(time.time()) + 7 * 86400 + 300)
    else:
        route = {"declined": "decline", "withdrawn": "withdraw", "accepted": "accept"}[end]
        r = world.client.post(f"{T}/offers/{offer_id}/{route}", headers=world.h(OWNER if end == "withdrawn" else MATE),
                              json={"agent_id": mate_agent, "adopt_checks": []} if end == "accepted" else {})
        assert r.status_code == 200, r.text
    assert world.offer(offer_id)["state"] == end
    after = world.get(OWNER, task_id)
    assert after["state"] == ("handed_over" if end == "accepted" else "suspended"), dict(after)
    at_end = [e for e in world.journal() if e["kind"] == "task.account_regrouped" and e["task_id"] == task_id
              and e["payload"].get("at_offer_end")]
    if way == "out and back":
        assert after["crossings_ended"] == 0 and at_end == [], (dict(after), at_end)
        return
    assert (after["crossings_ended"], after["bound_org"]) == (1, "org-z"), dict(after)
    assert len(at_end) == 1, at_end
    line = at_end[0]["payload"]
    assert line["at_offer_end"] == ("accepted" if end == "accepted" else "untaken")
    assert line["bound"] == {"crossings_ended": 1}
    assert (line["read_from"], line["read_to"], line["by"], line["left_out"]) == (
        "team" if way == "in" else "enterprise", "enterprise" if way == "in" else "team", "account:ffffffffffff",
        None)
    assert ("its offer has been accepted" if end == "accepted" else "its offer has ended untaken") in line["why"], \
        line["why"]
    if end == "accepted":
        # the task the teammate works on keeps to the group it was accepted in: nothing of the reread reaches it
        theirs = world.get(MATE, world.offer(offer_id)["their_task"])
        assert (theirs["crossings_ended"], theirs["bound_org"]) == (0, None), dict(theirs)


@pytest.mark.parametrize("end", ["withdrawn", "failed, held for the owner"])
def test_an_offer_still_in_its_hold_is_marked_by_a_reread_and_its_task_bound_as_it_ends(world, monkeypatch, end):
    """The offer that stands is marked whether it is offered or still in its
    hold (`held`). The offer's hold made longer than the confirmation's, so that the way out takes effect while the
    offer is held: the owner's task on eeeeeeeeeeee, a Team seat of org-z, whose Enterprise seat ffffffffffff is then
    confirmed again as a Team seat. The owner withdraws the offer while it is held, and the task is bound as the offer
    ends untaken, its crossings ended. An end that leaves the task needing its owner
    binds it all the same (task_offers._back, before the task needs him): here the hold's end finds a secret shape in
    the push, so the offer ends failed and the task needs its owner, cause handoff."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_OFFER_HOLD", "80000")
    task_id, mate_agent = setup_pressed(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                        account="eeeeeeeeeeee", home="org:org-z", bound_org="org-z")
    world.account(OWNER, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=1)
    world.account(OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z", position=2)
    offer_id = world.offer_to(task_id)["offer"]["id"]
    confirmed_again(world, OWNER, "ffffffffffff", "Claude Team", "Zed", "org-z")
    offer = world.offer(offer_id)
    assert offer["state"] == "held" and [m["read_from"] for m in json.loads(offer["reread"])] == ["enterprise"], offer
    assert world.get(OWNER, task_id)["crossings_ended"] == 0
    if end == "withdrawn":
        r = world.client.post(f"{T}/offers/{offer_id}/withdraw", headers=world.h(OWNER), json={})
        assert r.status_code == 200, r.text
    else:
        world.ws.secret = {"shape": "an AWS access key", "file": "config/prod.yml"}
        world.running(OWNER, MATE)
        world.settle(after=80000 + 200)
        assert world.offer(offer_id)["state"] == "failed", world.offer(offer_id)
        held = world.get(OWNER, task_id)
        assert (held["state"], held["cause"]) == ("needs_owner", "handoff"), dict(held)
    after = world.get(OWNER, task_id)
    assert (after["crossings_ended"], after["bound_org"]) == (1, "org-z"), dict(after)
    (line,) = [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"
               and e["payload"].get("at_offer_end")]
    assert (line["at_offer_end"], line["bound"]) == ("untaken", {"crossings_ended": 1}), line


def test_an_offered_task_whose_own_account_left_the_organisation_gets_no_binding_of_its_reread(world):
    """The reread's binding is given at the offer's end only where
    the task's account is still one of that organisation's seats (tasks.apply_held_reread). Here the way out marks the
    offer (ffffffffffff, org-z's Enterprise seat, confirmed again as a Team seat), and then eeeeeeeeeeee, the seat the
    task is on, is itself confirmed into org-y while the offer is out: the rule for that confirmation
    (tasks._account_moved) leaves an offered task with its binding, then and once its offer ends, and the reread's,
    for an organisation the task's account is no longer a seat of, is not given either. The control, the same without
    eeeeeeeeeeee's move, is the offer's-end test's way out, declined, which binds the task."""
    task_id, mate_agent = setup_pressed(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                        account="eeeeeeeeeeee", home="org:org-z", bound_org="org-z")
    world.account(OWNER, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=1)
    world.account(OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z", position=2)
    offer_id = offered(world, task_id)["offer"]["id"]
    confirmed_again(world, OWNER, "ffffffffffff", "Claude Team", "Zed", "org-z")
    confirmed_again(world, OWNER, "eeeeeeeeeeee", "Claude Team", "Yarra", "org-y")
    assert [m["grp"] for m in json.loads(world.offer(offer_id)["reread"])] == ["org:org-z"]
    r = world.client.post(f"{T}/offers/{offer_id}/decline", headers=world.h(MATE), json={})
    assert r.status_code == 200, r.text
    after = world.get(OWNER, task_id)
    assert (after["crossings_ended"], after["bound_org"]) == (0, "org-z"), dict(after)
    assert [e for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"
            and e["payload"].get("at_offer_end")] == []


@pytest.mark.parametrize("back", [False, True])
def test_an_offer_s_marks_keep_each_organisation_s_reread_and_its_end_binds_by_the_one_its_task_is_on(world, back):
    """An offer's mark that kept the first organisation's reread whatever
    organisation a later one was of would give a task whose account was confirmed into another organisation while the
    offer was out, that organisation then read otherwise, nothing as the offer ended, though its line said its
    binding would change then. The marks are one for each organisation, the first reading of each, and the offer's end
    binds the task by the mark of the organisation its account is a seat of then (tasks.apply_held_reread). The owner's
    task on eeeeeeeeeeee, a Team seat of org-z, offered: ffffffffffff, org-z's Enterprise seat, confirmed again as a
    Team one (org-z's mark); eeeeeeeeeeee confirmed into org-y; dddddddddddd, org-y's Enterprise seat, confirmed again
    as a Team one (org-y's mark, org-y having read as an Enterprise organisation while it held both kinds). Declined,
    the task is bound by org-y's mark. `back`: eeeeeeeeeeee confirmed again into org-z before the end, so org-z's mark,
    kept beside org-y's, binds it, by org-z's reading; a single mark replaced by the later organisation's would give it
    nothing. The offered task's line says when its binding changes, its account a seat of the organisation
    then."""
    task_id, mate_agent = setup_pressed(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                        account="eeeeeeeeeeee", home="org:org-z", bound_org="org-z")
    world.account(OWNER, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=1)
    world.account(OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z", position=2)
    world.account(OWNER, "dddddddddddd", "Claude Enterprise", "Yarra", "org-y", position=3)
    offer_id = offered(world, task_id)["offer"]["id"]
    confirmed_again(world, OWNER, "ffffffffffff", "Claude Team", "Zed", "org-z")
    confirmed_again(world, OWNER, "eeeeeeeeeeee", "Claude Team", "Yarra", "org-y")
    confirmed_again(world, OWNER, "dddddddddddd", "Claude Team", "Yarra", "org-y")
    marks = json.loads(world.offer(offer_id)["reread"])
    assert marks == [{"grp": "org:org-z", "read_from": "enterprise", "by": "account:ffffffffffff"},
                     {"grp": "org:org-y", "read_from": "enterprise", "by": "account:dddddddddddd"}], marks
    told = [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"
            and e["payload"].get("left_out") == "offered" and e["payload"].get("read_from")]
    assert [(t["from_grp"], t["by"]) for t in told] == [("org:org-z", "account:ffffffffffff"),
                                                        ("org:org-y", "account:dddddddddddd")], told
    for t in told:
        assert ("The task was offered then, so its binding stays as it was while the offer is out, and changes as the "
                "offer ends, taken or not, where its account is a seat of this organisation then and the organisation "
                "still reads otherwise.") in t["why"], t["why"]
    if back:
        confirmed_again(world, OWNER, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z")
    r = world.client.post(f"{T}/offers/{offer_id}/decline", headers=world.h(MATE), json={})
    assert r.status_code == 200, r.text
    after = world.get(OWNER, task_id)
    assert (after["crossings_ended"], after["bound_org"]) == (1, "org-z"), dict(after)
    (line,) = [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"
               and e["payload"].get("at_offer_end")]
    assert (line["from_grp"], line["read_from"], line["read_to"], line["by"], line["bound"]) == (
        ("org:org-z", "enterprise", "team", "account:ffffffffffff", {"crossings_ended": 1}) if back else
        ("org:org-y", "enterprise", "team", "account:dddddddddddd", {"crossings_ended": 1})), line


def test_an_offered_task_whose_crossings_had_ended_gets_no_line_as_its_offer_ends(world):
    """The binding at the offer's end changes only what it changes, and
    writes its line only where it changed something (tasks.apply_held_reread). The way out, on a task whose crossings
    had already ended: the offer is marked, and as it ends declined the task's binding stays as it was, with no line
    saying that it changed."""
    task_id, mate_agent = setup_pressed(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                        account="eeeeeeeeeeee", home="org:org-z", bound_org="org-z",
                                        crossings_ended=1)
    world.account(OWNER, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=1)
    world.account(OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z", position=2)
    offer_id = offered(world, task_id)["offer"]["id"]
    confirmed_again(world, OWNER, "ffffffffffff", "Claude Team", "Zed", "org-z")
    assert [m["grp"] for m in json.loads(world.offer(offer_id)["reread"])] == ["org:org-z"]
    r = world.client.post(f"{T}/offers/{offer_id}/decline", headers=world.h(MATE), json={})
    assert r.status_code == 200, r.text
    after = world.get(OWNER, task_id)
    assert (after["state"], after["crossings_ended"], after["bound_org"]) == ("suspended", 1, "org-z"), dict(after)
    assert [e for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"
            and e["payload"].get("at_offer_end")] == []


def test_a_task_its_own_account_s_reserve_alone_holds_back_waits_with_auto_resume_off_and_may_be_offered(world):
    """The owner's task on his one account, held back by its weekly reserve
    alone, Auto-resume off, at an attempt's end (tasks._next, as the app runs it there): it waits, suspended, for
    nothing but a Continue (tasks.HELD_BY_A_RESERVE), so it may be offered, as a task that waits may
    (task_offers._offerable); made to need its owner, its offer would be refused with 412, "Only a task that
    waits, or one working now, can be offered."."""
    world.mp.setenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", "0")
    task_id, mate_agent = setup_pressed(world, owner_state="running")
    world.window(OWNER, "aaaaaaaaaaaa", "allowed_warning", resets_at=int(time.time()) + 86400, window="seven_day")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE ade_agent_tasks SET lease_run = NULL WHERE id = ?", (task_id,))
        task = tasks._as_dict(db.execute("SELECT * FROM ade_agent_tasks WHERE id = ?", (task_id,)).fetchone())
        tasks._next(db, world.user(OWNER), task, int(time.time()), [], "next_step")
    held = world.get(OWNER, task_id)
    assert (held["state"], held["cause"], held["waits_until"], held["reason"]) == (
        "suspended", "window", None, tasks.HELD_BY_A_RESERVE), dict(held)
    assert tasks.alerts(world.user(OWNER), int(time.time())) == []
    made = offered(world, task_id)
    assert world.offer(made["offer"]["id"])["state"] == "offered"


def test_a_confirmation_marks_only_the_offer_that_stands_of_a_task_offered_before(world):
    """`press_dropped_at` is set on the offer that stands, held or
    offered, never on an offer of the same task that ended before: the owner's task, its press naming dddddddddddd,
    offered and declined, then offered again; the account confirmed again onto a Team seat while the second offer is
    out marks the second alone."""
    asked = {"kind": "account", "id": "dddddddddddd"}
    task_id, mate_agent = setup_pressed(world, asked=json.dumps(asked))
    world.account(OWNER, "dddddddddddd", "Claude Pro", position=1)
    first = offered(world, task_id)["offer"]["id"]
    r = world.client.post(f"{T}/offers/{first}/decline", headers=world.h(MATE), json={})
    assert r.status_code == 200, r.text
    assert world.get(OWNER, task_id)["asked"] == asked
    second = offered(world, task_id)["offer"]["id"]
    confirmed_again(world, OWNER, "dddddddddddd", "Claude Team", "Tee", "org-t")
    assert (world.offer(first)["state"], world.offer(first)["press_dropped_at"]) == ("declined", None)
    assert world.offer(second)["press_dropped_at"] is not None


def test_an_offer_s_end_drops_the_press_by_its_own_mark_never_an_earlier_offer_s(world):
    """_drop_marked_press finds the ending offer by its hold, never
    by any marked offer of the task, since an ended offer keeps its mark. The first offer, marked by a confirmation,
    declined: the press goes with it. A press made again naming the same account, the task offered again and declined
    with no confirmation meanwhile: the new press stands, the first offer's mark not being this offer's."""
    asked = {"kind": "account", "id": "dddddddddddd"}
    task_id, mate_agent = setup_pressed(world, asked=json.dumps(asked))
    world.account(OWNER, "dddddddddddd", "Claude Pro", position=1)
    first = offered(world, task_id)["offer"]["id"]
    confirmed_again(world, OWNER, "dddddddddddd", "Claude Team", "Tee", "org-t")
    r = world.client.post(f"{T}/offers/{first}/decline", headers=world.h(MATE), json={})
    assert r.status_code == 200, r.text
    assert world.get(OWNER, task_id)["asked"] is None and world.offer(first)["press_dropped_at"] is not None
    with store.connect() as db:                 # the press made again, as a Next on... kept for his next Continue
        db.execute("UPDATE ade_agent_tasks SET asked = ? WHERE id = ?", (json.dumps(asked), task_id))
    second = offered(world, task_id)["offer"]["id"]
    r = world.client.post(f"{T}/offers/{second}/decline", headers=world.h(MATE), json={})
    assert r.status_code == 200, r.text
    assert world.offer(second)["press_dropped_at"] is None
    assert world.get(OWNER, task_id)["asked"] == asked

@pytest.mark.parametrize("moved", [True, False])
def test_a_custody_hand_off_taken_drops_the_press_a_confirmation_made_stale_while_it_was_out(world, monkeypatch,
                                                                                          moved):
    """Custody's hand-off drops a press as a pressed offer's end does: the
    owner's task, its press naming bbbbbbbbbbbb, handed by custody and pushed; bbbbbbbbbbbb confirmed again onto a Team
    seat before the custodian's workspace takes it, the task keeps its press, and loses it as the custodian takes the
    task (_take's _drop_marked_press). The hand-off pushed in one round and taken in a later one, as the test of a
    hand-off checked again before its fetch makes it. The control: no confirmation meanwhile, and the press stays."""
    asked = {"kind": "account", "id": "bbbbbbbbbbbb"}
    task_id, mate_agent, _ = setup_custody(world, asked=json.dumps(asked))
    found, hold = hand(world, task_id)
    world.running(OWNER, MATE)
    take = task_offers._take
    monkeypatch.setattr(task_offers, "_take", lambda offer_id, now: None)
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "offered"
    if moved:
        confirmed_again(world, OWNER, "bbbbbbbbbbbb", "Claude Team", "Tee", "org-t")
    assert (world.offer(offer["id"])["press_dropped_at"] is not None) is moved
    assert (world.get(OWNER, task_id)["state"], world.get(OWNER, task_id)["asked"]) == ("offering", asked)
    monkeypatch.setattr(task_offers, "_take", take)
    task_offers.drive()
    assert world.offer(offer["id"])["state"] == "accepted"
    after = world.get(OWNER, task_id)
    assert (after["state"], after["asked"]) == ("handed_over", None if moved else asked)


def ask_custody(w, owner=OWNER, address=MATE):
    """A custody request asked and confirmed through the routes, as the page asks it."""
    r = w.client.post(f"{T}/custody", headers=w.h(owner), json={"address": address})
    assert r.status_code == 200, r.text
    custody_id = r.json()["custody"]["id"]
    r = w.client.post(f"{T}/custody/{custody_id}/confirm", headers=w.h(owner),
                      json={"confirmation": w.confirmation(owner, f"task-custody:{custody_id}")})
    assert r.status_code == 200, r.text
    return custody_id


def accept_custody(w, custody_id, agents_, who=MATE):
    confirmation = w.confirmation(who, f"task-custody-accept:{custody_id}")
    r = w.client.post(f"{T}/custody/{custody_id}/accept", headers=w.h(who),
                      json={"agents": agents_, "confirmation": confirmation})
    assert r.status_code == 200, r.text
    return r.json()["custody"]


def confirm_for_custody(w, task_id, owner=OWNER):
    version = w.get(owner, task_id)["state_version"]
    r = w.client.post(f"{T}/tasks/{task_id}/custody-confirm", headers=w.h(owner),
                      json={"state_version": version,
                            "confirmation": w.confirmation(owner, f"task-custody-confirm:{task_id}")})
    assert r.status_code == 200, r.text
    return r.json()["task"]


def setup_custody(w, mate_plan="Claude Max", mate_org=None, mate_org_id=None, refused_for=HOUR, **task):
    """The owner's task on their personal plan, both of its relay set's accounts refused until `refused_for` from
    now, confirmed for custody; the teammate their custodian, with one receiving agent on the same repository."""
    now = int(time.time())
    w.account(OWNER, "aaaaaaaaaaaa", "Claude Max", position=0)
    w.account(OWNER, "bbbbbbbbbbbb", "Claude Pro", position=1)
    for account in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
        w.window(OWNER, account, "rejected", resets_at=now + refused_for)
    task_id = w.task(OWNER, w.agent(OWNER), **task)
    w.account(MATE, "cccccccccccc", mate_plan, mate_org, mate_org_id)
    mate_agent = w.agent(MATE, account="cccccccccccc", name="Helper")
    custody_id = ask_custody(w)
    accept_custody(w, custody_id, [mate_agent])
    confirm_for_custody(w, task_id)
    return task_id, mate_agent, custody_id


def candidate(w, task_id, owner=OWNER):
    with store.connect() as db:
        return task_offers.custody_candidate(db, w.user(owner), task_id, int(time.time()))


def hand(w, task_id, owner=OWNER):
    """The relay's step 2 as ade/tasks.py takes it: the candidate, and the hand-off begun in its transaction, its hold
    told once that has committed."""
    now = int(time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        found = task_offers.custody_candidate(db, w.user(owner), task_id, now)
        assert found is not None
        hold = task_offers.begin_custody(db, w.user(owner), task_id, found, now)
    holds.tell(hold)
    return found, hold


def handed(w, task_id):
    """A custody hand-off begun, past its hold, and taken by the custodian's receiving agent."""
    found, hold = hand(w, task_id)
    w.running(OWNER, MATE)
    w.settle()
    offer = w.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "accepted", offer
    return offer


@pytest.mark.parametrize("case", ["named", "track off", "no account custody reads"])
def test_a_regrouping_names_the_custodian_only_where_custody_could_take_the_task(world, monkeypatch, case):
    """The line ade/tasks.py writes on a task whose account is confirmed into another
    group (task.account_regrouped) does not name the owner's custodian for every task not received from a teammate.
    With custody real here, the line names the custodian where custody_candidate finds one for the task, and not where
    the track is off for the owner, nor where no account of the task's relay set is left for custody's refusal to read.

    This task is away from home with crossing off, so at an attempt's end it goes home
    or waits (tasks._next), handed to no one, and only the relay, which an attempt its account refuses reaches
    (tasks._relay), asks custody. The line says so, and the two are run here as the app runs them: the end hands the
    task to no one in every case, and the refusal hands it to the custodian only where the line names one. With the
    other account gone the line still says only home, for the relay set is the personal plans, whatever
    the owner has there now."""
    task_id, mate_agent, _ = setup_custody(world)
    if case == "no account custody reads":     # the owner's other account gone: his one account is the moved one
        with store.connect() as db:
            db.execute("DELETE FROM ade_account_owners WHERE user_id = ? AND account = 'bbbbbbbbbbbb'",
                       (world.user(OWNER),))
    if case == "track off":
        monkeypatch.setattr(workspaces, "provider_name", lambda: "local")
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Tee", "org-t")
    why = [e["payload"]["why"] for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"]
    found = candidate(world, task_id)
    tail = {"named": " By itself the app moves the task only home, or, after an attempt on that account is refused, to"
                     " your custodian if you have one, and otherwise leaves it waiting.",
            "track off": " By itself the app moves the task only home, and otherwise leaves it waiting.",
            "no account custody reads": " By itself the app moves the task only home, and otherwise leaves it"
                                        " waiting."}
    assert len(why) == 1 and why[0].endswith(tail[case]), why
    assert (found or {}).get("agent_id") == (mate_agent if case == "named" else None)
    with store.connect() as db:
        refused = db.execute("SELECT id FROM ade_account_owners WHERE user_id = ? AND account = 'aaaaaaaaaaaa'",
                             (world.user(OWNER),)).fetchone()["id"]

    def decide(how):
        """The app's own decision for the task where the confirmation left it, at an attempt's end or at a refusal of
        an attempt on its account: the task's state after it, read in its transaction, which is then rolled back so
        that each decision starts from the same place."""
        now = int(time.time())
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = tasks._as_dict(db.execute("SELECT * FROM ade_agent_tasks WHERE id = ?", (task_id,)).fetchone())
            if how == "end":
                tasks._next(db, world.user(OWNER), task, now, [], "next_step")
            else:
                tasks._relay(db, world.user(OWNER), task, refused, now, [])
            state = db.execute("SELECT state FROM ade_agent_tasks WHERE id = ?", (task_id,)).fetchone()["state"]
            db.execute("ROLLBACK")
        return state
    # waiting at the end, for B's reset or, with no account at home, for the owner
    assert decide("end") == ("needs_owner" if case == "no account custody reads" else "suspended")
    assert decide("refusal") == ("offering" if case == "named" else "suspended")


def custody_on_seats(w, owner_plan, mate_plan, on="org-z", **task):
    """A Team or Enterprise organisation's task, of org-z, on the owner's seat of `on`, refused for an hour, confirmed
    for custody; the teammate its custodian, their receiving agent on a seat of `on` of `mate_plan`."""
    now = int(time.time())
    w.account(OWNER, "aaaaaaaaaaaa", owner_plan, "Zed", on, position=0)
    w.window(OWNER, "aaaaaaaaaaaa", "rejected", resets_at=now + HOUR)
    task_id = w.task(OWNER, w.agent(OWNER), home="org:org-z", **task)
    w.account(MATE, "cccccccccccc", mate_plan, "Zed", on)
    mate_agent = w.agent(MATE, account="cccccccccccc", name="Helper")
    custody_id = ask_custody(w)
    accept_custody(w, custody_id, [mate_agent])
    confirm_for_custody(w, task_id)
    return task_id, mate_agent


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("on_plan, other_plan, ended", [("Claude Team", "Claude Enterprise", 0),
                                                        ("Claude Enterprise", "Claude Team", 1)])
def test_custody_counts_a_seat_of_either_kind_of_the_organisation_a_task_is_on_once_it_holds_both(
        world, on_plan, other_plan, ended, changed):
    """Were custody's members to take the relay set's accounts by group id while the relay leaves out an Enterprise
    seat of the Team organisation a task is on, that seat's room would keep custody from
    ever handing the task, while nothing would run it there; and the other way round too, a Team seat of the
    organisation of the Enterprise seat a task is on. An
    organisation holding seats of both kinds reads as an Enterprise one, so such a seat is one of the seats of the
    group the task is on as the relay reads them, onto which the relay moves the task as within any group: the
    relay takes it (left out for no reason, or, its identity changed since, for that alone), and custody counts it
    among its members (_members), so its room keeps custody from handing the task, as any member's room keeps it, the
    relay having somewhere to go. Each case's control: custody hands the task before that seat is confirmed. A seat of
    the task's relay groups that the relay leaves out for another reason: the next test."""
    task_id, mate_agent = custody_on_seats(world, on_plan, on_plan, crossings_ended=ended,
                                           bound_org="org-z" if ended else None)
    assert candidate(world, task_id)["agent_id"] == mate_agent           # the control
    world.account(OWNER, "dddddddddddd", other_plan, "Zed", "org-z", position=1)
    world.window(OWNER, "dddddddddddd", "allowed", resets_at=int(time.time()) + HOUR)
    left_out = None
    if changed:
        with store.connect() as db:
            report = json.loads(db.execute("SELECT report FROM ade_account_owners WHERE user_id = ? AND account = ?",
                                           (world.user(OWNER), "dddddddddddd")).fetchone()["report"])
            db.execute("UPDATE ade_account_owners SET state = 'changed', report = ? WHERE user_id = ? AND account = ?",
                       (json.dumps(dict(report, plan=on_plan)), world.user(OWNER), "dddddddddddd"))
        left_out = "its identity changed: confirm it again"
    now = int(time.time())
    task = world.get(OWNER, task_id)
    with store.connect() as db:
        entries = {e["account"]: e for e in tasks.relay_set(db, world.user(OWNER), task_id, now)}
        members = [e["account"] for e in task_offers._members(db, world.user(OWNER), task, now)]
    assert (entries["dddddddddddd"]["org_kind"], entries["dddddddddddd"]["left_out"]) == ("enterprise", left_out)
    assert "dddddddddddd" in members
    assert candidate(world, task_id) is None


@pytest.mark.parametrize("mate_plan", ["Claude Team", "Claude Enterprise"])
def test_custody_hands_a_task_of_an_organisation_holding_seats_of_both_kinds_as_its_custodian_reads_their_seat(
        world, mate_plan):
    """An organisation holding seats of both kinds reads as
    an Enterprise one, each user's organisations read from the accounts they confirmed (tasks._kind_of_group, read for
    custody's receiving account by tasks.read_kind, from the custodian's). The owner's task on aa, a Team seat of
    org-z, refused for an hour; then dd, his Enterprise seat of org-z, refused too, so that org-z reads to him as an
    Enterprise organisation. Custody hands the task onto the custodian's Enterprise seat of org-z, a move within an
    Enterprise organisation's seats as the relay makes one, not onto a
    Team seat of theirs; and not onto their Team seat of org-z, which they read as a Team organisation's, so that the
    move would be onto a seat read as another kind than the one the task is on (tasks.enterprise_move)."""
    task_id, mate_agent = custody_on_seats(world, "Claude Team", mate_plan)
    before = candidate(world, task_id)
    assert (before is not None and before["agent_id"] == mate_agent) is (mate_plan == "Claude Team"), before
    world.account(OWNER, "dddddddddddd", "Claude Enterprise", "Zed", "org-z", position=1)
    world.window(OWNER, "dddddddddddd", "rejected", resets_at=int(time.time()) + HOUR)
    found = candidate(world, task_id)
    assert (found is not None and found["agent_id"] == mate_agent) is (mate_plan == "Claude Enterprise"), found


@pytest.mark.parametrize("mixed", [False, True])
def test_custody_crosses_onto_no_seat_of_an_organisation_its_custodian_holds_seats_of_both_kinds_of(world, mixed):
    """At custody's crossing, an organisation holding seats of both kinds reads as an Enterprise one: a personal task,
    custody's crossing ticked, its receiving agent on the custodian's Team seat of org-z: custody crosses onto it (the
    control), but not once the custodian also holds an Enterprise seat of org-z, which then reads to them as an
    Enterprise organisation (the receiving account's org_kind), since no crossing goes into an Enterprise
    organisation's seats (_custody_groups)."""
    task_id, mate_agent, _ = setup_custody(world, mate_plan="Claude Team", mate_org="Zed", mate_org_id="org-z",
                                           custody_cross=1)
    if mixed:
        world.account(MATE, "eeeeeeeeeeee", "Claude Enterprise", "Zed", "org-z", position=1)
    found = candidate(world, task_id)
    assert (found is not None and found["agent_id"] == mate_agent) is not mixed, found


@pytest.mark.parametrize("left_out, hands", [
    (None, False), ("taken out of relays", False), ("taken out of this task", False), ("signed out", False),
    ("its weekly reserve", False), ("its identity changed: confirm it again", False),
    ("it waits for its confirmation's hold", True), ("not confirmed as yours", True),
    ("crossing organisations is off for this task", True)])
def test_custody_reads_a_seat_the_relay_leaves_out_for_another_reason_and_no_held_unconfirmed_or_other_seat(
        world, left_out, hands):
    """Beside the owner's seat the task is
    on, refused for an hour, a Team seat of the same organisation with room. Left in the relay (the control), or left
    out by it, a confirmed seat of the task's group, as taken out of relays, taken out of this task, signed out or held
    by its weekly reserve, or as an identity changed since, it is still one of custody's members (_members), not out on
    a refused window (_all_refused), and custody waits: every reason the relay gives such a seat but an Enterprise move,
    which the test before takes. A held seat, an unconfirmed one and one of another organisation are not read, and
    custody hands the task over."""
    task_id, mate_agent = custody_on_seats(world, "Claude Team", "Claude Team")
    assert candidate(world, task_id)["agent_id"] == mate_agent           # nothing else: it hands
    org = "org-y" if left_out == "crossing organisations is off for this task" else "org-z"
    world.account(OWNER, "dddddddddddd", "Claude Team", "Zed", org, position=1,
                  in_relays=left_out != "taken out of relays")
    now = int(time.time())
    if left_out == "its weekly reserve":
        world.window(OWNER, "dddddddddddd", "allowed_warning", resets_at=now + 24 * HOUR, window="seven_day")
    else:
        world.window(OWNER, "dddddddddddd", "allowed", resets_at=now + HOUR)
    state = {"its identity changed: confirm it again": "changed", "it waits for its confirmation's hold": "held",
             "not confirmed as yours": "reported"}.get(left_out)
    with store.connect() as db:
        if state:
            db.execute("UPDATE ade_account_owners SET state = ? WHERE user_id = ? AND account = ?",
                       (state, world.user(OWNER), "dddddddddddd"))
        if left_out == "signed out":
            db.execute("UPDATE ade_account_owners SET ready = 0 WHERE user_id = ? AND account = ?",
                       (world.user(OWNER), "dddddddddddd"))
        if left_out == "taken out of this task":
            seat = db.execute("SELECT id FROM ade_account_owners WHERE user_id = ? AND account = ?",
                              (world.user(OWNER), "dddddddddddd")).fetchone()["id"]
            db.execute("UPDATE ade_agent_tasks SET taken_out = ? WHERE id = ?", (json.dumps([seat]), task_id))
    with store.connect() as db:
        entries = {e["account"]: e for e in tasks.relay_set(db, world.user(OWNER), task_id, int(time.time()))}
    assert entries["dddddddddddd"]["left_out"] == left_out
    found = candidate(world, task_id)
    assert (found is not None and found["agent_id"] == mate_agent) == hands, found


@pytest.mark.parametrize("owner_plan, mate_plan, ended, hands", [
    ("Claude Team", "Claude Team", 0, True), ("Claude Team", "Claude Enterprise", 0, False),
    ("Claude Enterprise", "Claude Enterprise", 1, True), ("Claude Enterprise", "Claude Team", 1, False)])
def test_custody_hands_no_task_onto_or_off_an_enterprise_seat_of_the_organisation_it_is_on(
        world, owner_plan, mate_plan, ended, hands):
    """Custody is the relay's step 2; matching a group by its id alone, it would send a
    Team organisation's task to a teammate's Enterprise seat of it, and one whose crossings ended on an Enterprise
    seat to a teammate's Team seat of the same organisation."""
    task_id, mate_agent = custody_on_seats(world, owner_plan, mate_plan, crossings_ended=ended,
                                           bound_org="org-z" if ended else None)
    found = candidate(world, task_id)
    assert (found is not None and found["agent_id"] == mate_agent) == hands, found


def test_custody_reads_the_group_a_task_is_on_not_its_home(world):
    """The group a task is on decides, not its home group: a Team organisation's task pressed onto an Enterprise seat
    of another organisation, its crossings ended there, goes by custody to a teammate's Enterprise seat of that one (a
    version reading the home group's kind would refuse it)."""
    task_id, mate_agent = custody_on_seats(world, "Claude Enterprise", "Claude Enterprise", on="org-y",
                                           crossings_ended=1, bound_org="org-z")
    assert candidate(world, task_id)["agent_id"] == mate_agent


def test_custody_takes_a_confirmation_at_each_of_its_three_places(world):
    """Naming a custodian, accepting custody and confirming a task for custody each take their own
    confirmation; a request is the custodian's to see only once confirmed, and they are told, content-free."""
    world.user(MATE)
    r = world.client.post(f"{T}/custody", headers=world.h(OWNER), json={"address": MATE})
    custody_id = r.json()["custody"]["id"]
    assert r.json()["custody"]["state"] == "asked" and r.json()["custody"]["confirmed_at"] is None
    assert world.client.get(f"{T}/custody", headers=world.h(MATE)).json()["as_custodian"] == []
    r = world.client.post(f"{T}/custody/{custody_id}/confirm", headers=world.h(OWNER), json={})
    assert r.status_code == 403 and world.notices(MATE) == []
    r = world.client.post(f"{T}/custody/{custody_id}/confirm", headers=world.h(OWNER),
                          json={"confirmation": world.confirmation(OWNER, f"task-custody:{custody_id}")})
    assert r.status_code == 200 and world.notices(MATE) == ["A teammate asks you to be their custodian"]
    world.account(MATE, "cccccccccccc", "Claude Max")
    mate_agent = world.agent(MATE, account="cccccccccccc")
    r = world.client.post(f"{T}/custody/{custody_id}/accept", headers=world.h(MATE), json={"agents": [mate_agent]})
    assert r.status_code == 403 and world.custody(custody_id)["state"] == "asked"
    custody = accept_custody(world, custody_id, [mate_agent])
    assert custody["state"] == "accepted" and [a["agent_id"] for a in custody["agents"]] == [mate_agent]
    assert custody["agents"][0]["grp"] == "personal"
    task_id = world.task(OWNER, world.agent(OWNER))
    r = world.client.post(f"{T}/tasks/{task_id}/custody-confirm", headers=world.h(OWNER), json={"state_version": 0})
    assert r.status_code == 403 and world.get(OWNER, task_id)["custody_digest"] is None
    assert confirm_for_custody(world, task_id)["custody_confirmed"] is True
    assert [e["kind"] for e in world.journal()] == ["custody.asked", "custody.accepted"]


def test_custody_hands_a_confirmed_task_after_its_hold_and_tells_the_custodian(world):
    """Every account of the owner's relay set refused, a task confirmed for custody goes, after a
    hold of 180 seconds whose notice says when, to the custodian's agent, whose task starts from the pushed commit with
    no check of the owner's, its packet holding no model-written text; the custodian is told; the owner's record holds
    the custodian by user id with their plan."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found = candidate(world, task_id)
    assert found == {"custody_id": custody_id, "agent_id": mate_agent, "grp": "personal"}
    found, hold = hand(world, task_id)
    assert world.get(OWNER, task_id)["state"] == "offering"
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["kind"] == "custody" and offer["state"] == "held"
    summary = world.row("ade_holds", "id", hold["id"])["summary"]
    assert offer["id"] in summary and not _named(summary, MATE, world.user(MATE))
    told = [m for u, m in world.told if u == world.user(OWNER)][-1]
    assert (told.title, told.body) == ("A task of yours goes to your custodian",
                                       f"Goes to them at {holds.utc(hold['takes_effect_at'])} unless you cancel it.")
    world.running(OWNER, MATE)
    sent = []                                           # the workspaces sent their queued attempts at once
    world.mp.setattr(agents, "_deliver_soon",
                     lambda user, start=False: sent.append((user["id"], start)) or (world.provider.ensure(user) and False))
    world.settle()
    offer = world.offer(offer["id"])
    assert offer["state"] == "accepted" and world.get(OWNER, task_id)["state"] == "handed_over"
    made = world.tasks.created[-1]
    # the custodian's worktree is made under the received task's own id, which its attempts name; their receiving
    # agent mirrored into their workspace before its fetch, as an acceptance mirrors it (without it, an agent made while
    # that workspace did not run would not be there, and the fetch would find no such agent)
    # (the app's word, read and sent, first in the mirror)
    assert [p for _, p, _ in world.ws.calls] == [f"/agents/tasks/{task_id}/fetch", f"/agents/tasks/{task_id}/push",
                                                "/skill-improve/record", "/skill-improve/record",
                                                f"/agents/{mate_agent}", "/agents/facts-versions",
                                                f"/agents/tasks/{made['task_id']}/fetch"]
    assert world.ws.mirrored == [(world.user(MATE), mate_agent)]
    assert made["task_id"] != task_id and world.ws.fetched == {(world.user(MATE), made["task_id"]): offer["commit"]}
    assert world.ws.of("/fetch", world.user(OWNER))[0][1]["purpose"] == "view"
    assert world.ws.of("/fetch", world.user(MATE))[0][1]["agent"] == mate_agent
    assert made["agent_id"] == mate_agent and made["continued_from"]["offer"] == offer["id"]
    assert made["fields"]["custody_cross"] == 0 and made["fields"]["between_steps"] == "any"
    assert [s["check"] for s in made["fields"]["steps"]] == [None, None]
    # the packet whole: the owner's instructions and what is information, nothing a model wrote and no check
    assert json.loads(offer["packet"]) == {
        "schema": "ade.handoff.v1", "offer": offer["id"], "kind": "custody", "task": task_id,
        "instructions": {"by": "teammate", "from": OWNER, "goal": "Parse every config file the service reads.",
                         "steps": [{"n": 1, "objective": "Write the parser"}, {"n": 2, "objective": "Write the docs"}],
                         "decisions": [{"n": 1, "text": "Use the standard library only."}],
                         "protected": ["tests/**"], "external": []},
        "information": {"done_steps": [1], "outward_effects": []},
        "repository": {"address": ADDRESS, "branch": f"pp/task-{task_id}", "commit": C1}}
    # its first attempt is the custodian's relay's (chosen_by "custody"), queued as the hand-off is
    # taken; the owner's Start, a press, would run past the custodian's reserve, and nothing presses it
    assert world.tasks.started == [] and world.get(MATE, made["task_id"])["state"] == "running"
    with store.connect() as db:
        (run,) = db.execute("SELECT * FROM ade_agent_runs WHERE task_id = ?", (made["task_id"],)).fetchall()
    assert (run["chosen_by"], run["task_step"], json.loads(run["backend"])) == (
        "custody", 2, {"kind": "account", "id": "cccccccccccc"})
    assert "pressed" not in json.loads(run["trigger"])
    # and sent to the custodian's workspace at once, once the take has committed, as Run now sends one
    assert (world.user(MATE), True) in sent
    assert world.notices(MATE)[-1] == "A task was handed to you"
    assert [(e["kind"], e["via"]) for e in world.record(task_id)] == [("handoff", "custody")]
    assert world.record(task_id)[0]["accounts"] == [{"teammate": world.user(MATE), "plan": "Claude Max",
                                                     "organization": None, "org_id": None}]
    held = world.client.get(f"{T}/custody", headers=world.h(MATE)).json()["tasks"]
    assert [t["id"] for t in held] == [made["task_id"]] and held[0]["continued_from"]["task"] == task_id


CONDITIONS = ["decision", "check", "external", "protected", "brief", "taken_out", "reserve", "reset_soon",
              "unknown_reset", "unknown", "room", "no_address", "relay_switch", "relay_setting", "relay_default",
              "needs_owner", "received", "other_repo", "seat", "suspended", "ended", "unconfirmed", "track_off"]


@pytest.mark.parametrize("change", CONDITIONS)
def test_custody_hands_nothing_unless_every_condition_holds(world, monkeypatch, change):
    """Custody hands the task only while every condition holds: nothing after a
    decision, a check, an external command, a protected path or the agent's brief changed since the confirmation; with
    an account taken out of the relay, or held back by its reserve, rather than refused (its window allowed, near its
    limit), the earliest reset 10 minutes away, an unknown effect, an
    account with room, no address, the relay off by its switch or by the deployment, set off or left unset (its default,
    which the fixture's setting on stands in front of), a task that waits for its owner,
    one that came by custody, a receiving agent on another repository, one on a seat for a personal task with no
    crossing ticked, custody suspended or ended, or a task never confirmed; a refused window whose reset is not known
    does not count toward it; and nothing while the track is off."""
    task_id, mate_agent, custody_id = setup_custody(world, refused_for=600 if change == "reset_soon" else HOUR)
    if change == "reset_soon":
        assert candidate(world, task_id) is None
        return
    assert candidate(world, task_id) is not None
    with store.connect() as db:
        if change == "decision":
            db.execute("INSERT INTO ade_agent_task_decisions (task_id, n, user_id, text, author, at) VALUES "
                       "(?,2,?,'Use the C parser.','owner',0)", (task_id, world.user(OWNER)))
        elif change == "check":
            db.execute("UPDATE ade_agent_task_steps SET check_cmd = 'true' WHERE task_id = ? AND n = 1", (task_id,))
        elif change in ("external", "protected"):
            db.execute(f"UPDATE ade_agent_tasks SET {change} = '[\"x\"]' WHERE id = ?", (task_id,))
        elif change == "brief":
            db.execute("UPDATE ade_agents SET brief_version = brief_version + 1 WHERE user_id = ?",
                       (world.user(OWNER),))
        elif change == "taken_out":
            db.execute("DELETE FROM ade_backend_windows WHERE backend = 'account:bbbbbbbbbbbb'")
            db.execute("UPDATE ade_account_owners SET in_relays = 0 WHERE account = 'bbbbbbbbbbbb'")
        elif change == "reserve":
            db.execute("UPDATE ade_backend_windows SET status = 'allowed_warning' WHERE backend = "
                       "'account:bbbbbbbbbbbb'")
        elif change == "unknown_reset":
            db.execute("UPDATE ade_backend_windows SET resets_at = NULL WHERE backend = 'account:bbbbbbbbbbbb'")
        elif change == "unknown":
            pass
        elif change == "room":
            db.execute("UPDATE ade_backend_windows SET status = 'allowed', resets_at = NULL WHERE backend = "
                       "'account:bbbbbbbbbbbb'")
        elif change == "no_address":
            db.execute("UPDATE ade_agent_tasks SET repo_address = NULL WHERE id = ?", (task_id,))
        elif change == "relay_switch":
            db.execute("UPDATE ade_agent_tasks SET relay = 0 WHERE id = ?", (task_id,))
        elif change == "needs_owner":
            db.execute("UPDATE ade_agent_tasks SET state = 'needs_owner', cause = 'resume' WHERE id = ?", (task_id,))
        elif change == "received":
            db.execute("UPDATE ade_agent_tasks SET continued_from = ? WHERE id = ?",
                       (json.dumps({"task": "e" * 32, "offer": "f" * 32, "packet_digest": "0" * 64,
                                    "from_user": "someone"}), task_id))
        elif change == "other_repo":
            db.execute("UPDATE ade_agents SET repo = 'other' WHERE id = ?", (mate_agent,))
        elif change == "seat":
            db.execute("UPDATE ade_account_owners SET grp = 'org:org-y', grp_kind = 'team', org_id = 'org-y' WHERE "
                       "user_id = ? AND account = 'cccccccccccc'", (world.user(MATE),))
            db.execute("UPDATE ade_task_custody SET agents = ? WHERE id = ?", (json.dumps([{
                "agent_id": mate_agent, "digest": task_offers._agent_digest(db.execute(
                    "SELECT * FROM ade_agents WHERE id = ?", (mate_agent,)).fetchone()), "grp": "org:org-y"}]),
                custody_id))
        elif change == "suspended":
            db.execute("UPDATE ade_task_custody SET state = 'suspended' WHERE id = ?", (custody_id,))
        elif change == "ended":
            db.execute("UPDATE ade_task_custody SET state = 'ended' WHERE id = ?", (custody_id,))
        elif change == "unconfirmed":
            db.execute("UPDATE ade_agent_tasks SET custody_digest = NULL WHERE id = ?", (task_id,))
    if change == "unknown":
        world.effect(OWNER, task_id, "unknown")
    if change == "relay_setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "off")
    if change == "relay_default":
        monkeypatch.delenv("OPEN_EDITION_ADE_TASK_RELAY")
    if change == "track_off":
        monkeypatch.delenv("OPEN_EDITION_OPERATOR")
    assert candidate(world, task_id) is None, change


@pytest.mark.parametrize("away, hands", [(30 * 60 - 10, False), (30 * 60 + 60, True)])
def test_custody_waits_while_the_earliest_reset_is_30_minutes_away_or_less(world, away, hands):
    """Custody hands nothing while the earliest reset of the relay set is 30 minutes away or
    less, and does once it is further."""
    task_id, mate_agent, custody_id = setup_custody(world, refused_for=away)
    assert (candidate(world, task_id) is not None) == hands


def test_a_task_on_an_account_whose_identity_changed_is_handed_to_nobody(world):
    """An account of the relay set out for any other reason than a refused window does not count, and custody takes no
    task whose crossings a press ended into another group: a task whose crossings a press ended, on
    S2 (a seat of Y) and bound to X, goes to the receiving agent on Y's seat; once S2's identity changed since its
    confirmation, to nobody, never to the agent on X's seat: S2 is out for another reason than a refused window, and
    the task is on Y's group still, as a received offer's other group shows it."""
    now = int(time.time())
    world.account(OWNER, "aaaaaaaaaaaa", "Claude Team", "Xanadu", "org-x")
    world.account(OWNER, "bbbbbbbbbbbb", "Claude Team", "Yarra", "org-y")
    world.window(OWNER, "bbbbbbbbbbbb", "rejected", resets_at=now + HOUR)
    task_id = world.task(OWNER, world.agent(OWNER), account="bbbbbbbbbbbb", home="org:org-x", bound_org="org-x",
                         custody_cross=1, crossings_ended=1)
    world.account(MATE, "cccccccccccc", "Claude Team", "Xanadu", "org-x")
    world.account(MATE, "dddddddddddd", "Claude Team", "Yarra", "org-y")
    on_x = world.agent(MATE, account="cccccccccccc", name="On X")
    on_y = world.agent(MATE, account="dddddddddddd", name="On Y")
    accept_custody(world, ask_custody(world), [on_x, on_y])
    confirm_for_custody(world, task_id)
    assert candidate(world, task_id)["agent_id"] == on_y
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET state = 'changed' WHERE user_id = ? AND account = 'bbbbbbbbbbbb'",
                   (world.user(OWNER),))
    assert candidate(world, task_id) is None
    offered(world, task_id)
    seen = world.client.get(f"{T}/offers", headers=world.h(MATE)).json()["received"][0]
    assert seen["other_group_agents"] == [on_x]


def test_a_receiving_agent_with_an_open_task_is_passed_over_for_the_next(world):
    """The first receiving agent with an open task does not take it; the second, without one, does."""
    task_id, first, custody_id = setup_custody(world)
    second = world.agent(MATE, account="cccccccccccc", name="Second")
    world.task(MATE, first, home="personal", account="cccccccccccc")     # the first agent's own open task
    accept_custody(world, custody_id, [first, second])
    assert candidate(world, task_id)["agent_id"] == second


def test_a_task_confirmed_for_custody_by_an_answer_is_one_custody_hands(world):
    """The tasks' "Answer, and confirm for custody" keeps the digest tasks.custody_digest makes; custody reads the same
    one, so a task confirmed that way is handed as one confirmed through this track's route is, and a change after it
    (its title, here) leaves it unconfirmed."""
    task_id, mate_agent, custody_id = setup_custody(world)
    owner = world.user(OWNER)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET custody_digest = NULL WHERE id = ?", (task_id,))
    assert candidate(world, task_id) is None
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET custody_digest = ? WHERE id = ?",
                   (tasks.custody_digest(db, owner, task_id), task_id))
    assert candidate(world, task_id) is not None
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET title = 'Parse every config file' WHERE id = ?", (task_id,))
    assert candidate(world, task_id) is None


def test_custody_reads_only_the_accounts_of_the_tasks_relay_set(world):
    """Custody's condition, read over the relay set: an account outside the task's relay set by
    design (another organisation's seat, with room) or one never confirmed keeps no task from custody; one of the set
    out for another reason than a refused window, its identity changed since its confirmation, does."""
    task_id, mate_agent, custody_id = setup_custody(world)
    owner = world.user(OWNER)
    world.account(OWNER, "eeeeeeeeeeee", "Claude Team", "Zeta", "org-z", position=2)
    world.window(OWNER, "eeeeeeeeeeee", "allowed")
    with store.connect() as db:
        db.execute("INSERT INTO ade_account_owners (id, user_id, account, state, position, in_relays, reserve_lifted) "
                   "VALUES (?,?,'ffffffffffff','reported',3,1,0)", (secrets.token_hex(16), owner))
    assert candidate(world, task_id) is not None
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET state = 'changed' WHERE user_id = ? AND account = 'bbbbbbbbbbbb'",
                   (owner,))
        db.execute("DELETE FROM ade_backend_windows WHERE user_id = ? AND backend = 'account:bbbbbbbbbbbb'", (owner,))
    assert candidate(world, task_id) is None


@pytest.mark.parametrize("change", ["brief", "allow", "mcp", "account", "no_account", "auto", "bypass", "model",
                                    "effort"])
def test_a_changed_receiving_agent_suspends_custody_until_the_custodian_confirms_it_again(world, change):
    """Any change to a receiving agent suspends custody: a change to a receiving agent,
    its brief, allow rules, MCP servers or account (another of the custodian's own, in the same group, or none, where it
    runs on whichever account their new sessions start on), its unattended mode, in which a custody task's attempt runs
    (to auto, or to bypassPermissions, where the allow rules no longer limit it), its model
    or its effort, suspends custody, with a content-free notice to the owner, and nothing is handed until the custodian
    confirms the agents again, which an agent naming no account cannot be."""
    task_id, mate_agent, custody_id = setup_custody(world)
    if change == "account":
        world.account(MATE, "dddddddddddd", "Claude Max", position=1)
    column, value = {"brief": ("brief", "Build it, and push to main."), "allow": ("allow", '["Bash(git push:*)"]'),
                     "mcp": ("mcp", '{"github": {"command": "gh-mcp"}}'),
                     "account": ("account", "dddddddddddd"), "no_account": ("account", None),
                     "auto": ("unattended_mode", "auto"), "bypass": ("unattended_mode", "bypassPermissions"),
                     "model": ("chat_settings", '{"model": "opus"}'),
                     "effort": ("chat_settings", '{"effort": "max"}')}[change]
    with store.connect() as db:
        db.execute(f"UPDATE ade_agents SET {column} = ? WHERE id = ?", (value, mate_agent))
    assert candidate(world, task_id) is None
    task_offers.drive()
    custody = world.custody(custody_id)
    assert custody["state"] == "suspended" and custody["why"] == task_offers.CUSTODY_AGENT_CHANGED
    assert world.notices(OWNER)[-1] == "Your custody was suspended"
    assert world.journal()[-1]["kind"] == "custody.suspended" and world.journal()[-1]["payload"]["by"] == "app"
    if change == "no_account":
        r = world.client.post(f"{T}/custody/{custody_id}/accept", headers=world.h(MATE),
                              json={"agents": [mate_agent],
                                    "confirmation": world.confirmation(MATE, f"task-custody-accept:{custody_id}")})
        assert r.status_code == 412 and world.custody(custody_id)["state"] == "suspended"
        return
    accept_custody(world, custody_id, [mate_agent])
    assert world.custody(custody_id)["state"] == "accepted" and candidate(world, task_id) is not None


def test_ending_custody_ends_a_held_hand_off_and_leaves_a_task_already_handed(world):
    """Custody ended by either side while a hand-off is held ends the hand-off failed and the relay goes on
    (the task waits for its earliest reset); a task already with the custodian stays until a way back returns it."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found, hold = hand(world, task_id)
    r = world.client.post(f"{T}/custody/{custody_id}/end", headers=world.h(MATE), json={})
    assert r.status_code == 200 and r.json()["custody"]["state"] == "ended"
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "failed" and offer["why"] == task_offers.CUSTODY_ENDED_WHY
    task = world.get(OWNER, task_id)
    assert task["state"] == "suspended" and task["cause"] == "window" and task["waits_until"] > int(time.time())
    assert world.custody(custody_id)["custodian_user"] == world.user(MATE)      # an accepted custody keeps them
    assert world.journal()[-2]["payload"] == {"custody": custody_id, "by": "custodian",
                                              "why": task_offers.CUSTODY_ENDED_BY["custodian"]}
    second, agent2, custody2 = setup_custody_again(world)
    offer = handed(world, second)
    r = world.client.post(f"{T}/custody/{custody2}/end", headers=world.h(OWNER), json={})
    assert r.status_code == 200 and world.get(OWNER, second)["state"] == "handed_over"
    assert world.offer(offer["id"])["state"] == "accepted"


def earliest_reset(w, email=OWNER):
    with store.connect() as db:
        return min(r["resets_at"] for r in db.execute("SELECT resets_at FROM ade_backend_windows WHERE user_id = ? AND "
                                                      "status = 'rejected'", (w.user(email),)))


def test_a_custody_hand_off_cancelled_in_its_hold_hands_nothing(world):
    """Cancelling keeps the task, for custody too: the owner's Cancel of a custody hand-off's hold ends it
    withdrawn, the custodian named no more on it; nothing is pushed or handed; the task waits for the earliest reset
    of its relay set, from which the relay decides again."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found, hold = hand(world, task_id)
    holds.cancel(world.user(OWNER), hold["id"])
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "withdrawn" and offer["why"] == task_offers.CANCELLED and offer["teammate_user"] is None
    task = world.get(OWNER, task_id)
    assert (task["state"], task["cause"], task["waits_until"]) == ("suspended", "window", earliest_reset(world))
    world.running(OWNER, MATE)
    world.settle()
    assert world.ws.calls == [] and world.tasks.created == []
    assert world.notices(MATE) == ["A teammate asks you to be their custodian"]


def owner_groups(w, task_id):
    """The owner's task's relay groups (tasks.relay_groups)."""
    with store.connect() as db:
        return tasks.relay_groups(db, tasks.get(db, w.user(OWNER), task_id))[0]


def owner_left_out(w, task_id):
    """Why each of the owner's accounts is out of the task's relay set, or None where it is in."""
    with store.connect() as db:
        return {e["account"]: e["left_out"] for e in tasks.relay_set(db, w.user(OWNER), task_id, int(time.time()))}


def bound_lines(w, task_id):
    """A task's task.bound lines: an owner's task's, a way back's end binding it (task_offers._carry_back); a custody
    task's, a binding its owner's task took while the custodian had it reaching it (task_offers.bind_custody_task)."""
    return [e["payload"] for e in w.journal(task_id) if e["kind"] == "task.bound"]


def taken_back(w, task_id, offer_id, theirs, meanwhile=None):
    """The owner's Take back, through its route, then the way back to its end: `meanwhile` run once the way back has
    started, the custodian's attempt stopped by the round and its end copied, and the work come back."""
    r = w.client.post(f"{T}/tasks/{task_id}/take-back", headers=w.h(OWNER))
    assert r.status_code == 200, r.text
    if meanwhile is not None:
        meanwhile()
    task_offers.drive()
    ended(theirs)
    task_offers.drive()
    assert w.offer(offer_id)["state"] == "returned"


# whatever ended them: a press, a confirmation, a way back (not "its crossings ended with a press: ...")
CROSSINGS_ENDED_OUT = "its crossings ended: it stays in the group it is on"


@pytest.mark.parametrize("bound_before, mate, bound_after", [
    (None, ("Claude Team", "Zed", "org-z"), "org-z"),
    ("org-z", ("Claude Team", "Zed", "org-z"), "org-z"),
    (None, ("Claude Max", None, None), None)])
def test_custody_binds_a_personal_task_it_hands_onto_a_team_organisations_seat_to_that_organisation(
        world, monkeypatch, bound_before, mate, bound_after):
    """A personal task bound to no organisation that custody,
    its crossing ticked, hands onto a Team organisation's seat is bound to that organisation at the hand-off, as the
    relay's own crossing into one binds it, and task.handed_over names what it bound (`bound`) and that it crossed a
    group (`crossed`). So after the way back its relay set holds that organisation's seats beside the personal plans,
    and an account on another organisation's seats is out, where before the hand-off every Team organisation's seats
    were in ("org:*"). A task bound already keeps its binding, and so does one handed onto the personal plans, which
    binds nothing: `bound` is then empty. Its way back changes nothing more, the custody task bound as the hand-off
    left it: no task.bound line, and the way back's own line names no binding."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    plan, org, org_id = mate
    task_id, mate_agent, _ = setup_custody(world, mate_plan=plan, mate_org=org, mate_org_id=org_id, custody_cross=1,
                                           cross_groups=1, bound_org=bound_before)
    assert owner_groups(world, task_id) == ["personal", f"org:{bound_before}" if bound_before else "org:*"]
    offer = handed(world, task_id)
    assert world.get(OWNER, task_id)["bound_org"] == bound_after
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    assert line["payload"]["bound"] == ({"bound_org": bound_after} if bound_after and not bound_before else {})
    assert line["payload"]["crossed"] is (plan == "Claude Team")
    taken_back(world, task_id, offer["id"], offer["their_task"])
    back = world.get(OWNER, task_id)
    assert back["state"] not in ("handed_over", "returning") and back["bound_org"] == bound_after
    assert back["crossings_ended"] == 0
    assert owner_groups(world, task_id) == ["personal", f"org:{bound_after}" if bound_after else "org:*"]
    world.account(OWNER, "dddddddddddd", "Claude Team", "Yarra", "org-y", position=2)
    left = owner_left_out(world, task_id)
    assert (left["dddddddddddd"] == "another organisation's seats") == bool(bound_after), left
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.taken_back"]
    assert "bound" not in line["payload"] and bound_lines(world, task_id) == []


@pytest.mark.parametrize("how", ["cancelled", "failed", "relay_off"])
def test_a_custody_hand_off_onto_a_team_seat_that_does_not_happen_binds_nothing(world, how):
    """The binding is the hand-off's, in the transaction that hands the task over, after that transaction's last
    check: a hand-off cancelled in its hold, failing on the custodian's side before that transaction (their workspace
    refusing the fetch), or failing its last check, which ends the offer failed in the same transaction (the task's
    Relay switch turned off while their workspace fetched), leaves the task bound
    to no organisation."""
    task_id, mate_agent, _ = setup_custody(world, mate_plan="Claude Team", mate_org="Zed", mate_org_id="org-z",
                                           custody_cross=1)
    found, hold = hand(world, task_id)
    assert found["grp"] == "org:org-z"
    if how == "cancelled":
        holds.cancel(world.user(OWNER), hold["id"])
    else:
        def meanwhile(body):
            if body["purpose"] != "accept":
                return
            if how == "relay_off":
                with store.connect() as db:
                    db.execute("UPDATE ade_agent_tasks SET relay = 0 WHERE id = ?", (task_id,))
            else:
                raise workspace_client.WorkspaceCallError(409, "A run of an agent on that repository is working.")
        world.ws.during = meanwhile
    world.running(OWNER, MATE)
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == ("withdrawn" if how == "cancelled" else "failed")
    if how == "relay_off":
        assert offer["why"] == "The task's relay is off."
    assert world.get(OWNER, task_id)["bound_org"] is None
    assert [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"] == []


@pytest.mark.parametrize("when", ["its first attempt", "a later attempt"])
def test_custody_binds_a_personal_task_where_the_custodians_relay_takes_its_work_onto_a_team_organisations_seat(
        world, monkeypatch, when):
    """The receiving agent on the custodian's personal plan,
    custody's crossing ticked, and the custodian's relay, which the tick lets cross, takes the custody task onto
    their seat of a Team organisation, W, binding it there (tasks._move). At its first attempt, in the take's own
    transaction, the receiving account having no room then: the owner's task is bound to W at the hand-off, and
    task.handed_over names it, and that the hand-off crossed a group. At a later
    attempt, the receiving account refusing it at its first answer: the hand-off bound nothing and crossed nothing,
    and the owner's task is bound to W as its way back ends, task.bound naming it, the way back's start naming none.
    Either way, after the way back its relay set holds W's seats beside the personal plans, an account of the owner's
    on another organisation's seats, Y's, out, where before every Team organisation's seats were in ("org:*")."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    assert owner_groups(world, task_id) == ["personal", "org:*"]
    if when == "its first attempt":
        world.window(MATE, "cccccccccccc", "rejected", resets_at=now + HOUR)
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    if when == "its first attempt":
        assert runs_on(theirs) == ["dddddddddddd"] and world.get(MATE, theirs)["bound_org"] == "org-w"
        assert world.get(OWNER, task_id)["bound_org"] == "org-w"
        with store.connect() as db:
            assert tasks.moves(db, tasks.get(db, world.user(OWNER), task_id))[-1]["cases"] == ["custody_cross"]
        assert line["payload"]["bound"] == {"bound_org": "org-w"} and line["payload"]["crossed"] is True
    else:
        assert runs_on(theirs) == ["cccccccccccc"] and world.get(MATE, theirs)["bound_org"] is None
        assert world.get(OWNER, task_id)["bound_org"] is None and line["payload"]["bound"] == {}
        assert line["payload"]["crossed"] is False
        refused_there(world, MATE, theirs, resets=now + HOUR)
        assert runs_on(theirs) == ["cccccccccccc", "dddddddddddd"] and world.get(MATE, theirs)["bound_org"] == "org-w"
        assert world.get(OWNER, task_id)["bound_org"] is None
    taken_back(world, task_id, offer["id"], theirs)
    assert world.get(OWNER, task_id)["bound_org"] == "org-w"
    assert owner_groups(world, task_id) == ["personal", "org:org-w"]
    world.account(OWNER, "eeeeeeeeeeee", "Claude Team", "Yarra", "org-y", position=2)
    left = owner_left_out(world, task_id)
    assert left["eeeeeeeeeeee"] == "another organisation's seats", left
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.taken_back"]
    assert "bound" not in line["payload"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == ([] if when == "its first attempt"
                                                                  else [{"bound_org": "org-w"}])


def test_a_custody_hand_off_onto_a_seat_whose_first_attempt_comes_onto_the_personal_plans_still_crossed(
        world, monkeypatch):
    """task.handed_over's `crossed`:
    the hand-off crossed a group where the received task's home group, or the group its first attempt is on once
    decided in the take, differs from the owner's. The receiving agent is on the custodian's seat of Z, which refuses
    the first attempt, and the custodian's relay, custody's crossing ticked, takes it onto their personal plan, the
    owner's group: the hand-off still crossed (into Z's seats, where the task was made, and bound), and the owner's
    task is bound to Z."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, mate_plan="Claude Team", mate_org="Zed", mate_org_id="org-z",
                                           custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.window(MATE, "cccccccccccc", "rejected", resets_at=now + HOUR)
    world.account(MATE, "eeeeeeeeeeee", "Claude Max", position=1)
    offer = handed(world, task_id)
    theirs = world.get(MATE, offer["their_task"])
    assert (theirs["home_grp"], theirs["bound_org"]) == ("org:org-z", "org-z")
    assert runs_on(offer["their_task"]) == ["eeeeeeeeeeee"]
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    assert (line["payload"]["crossed"], line["payload"]["bound"]) == (True, {"bound_org": "org-z"})
    assert world.get(OWNER, task_id)["bound_org"] == "org-z"


@pytest.mark.parametrize("how", ["the relay after the round's stop missed", "the custodian's press",
                                 "pressed back onto the personal plans"])
def test_custody_binds_a_personal_task_as_the_custody_task_is_bound_when_its_way_back_ends(world, monkeypatch, how):
    """The owner's task is bound as the custody task's
    way back ends, before the work is applied, to what the custody task's binding says then. The custody task went
    onto the custodian's seat of W before the Take back, and the custodian pressed it back onto their personal plan:
    still bound to W, which its binding says, though its group no longer does, so the owner's task, unbound when the way
    back started, is bound to W as it ends, task.bound naming it. Once the way back has started the custody task moves
    no more: its attempt's end, copied as refused before the round's stop reached it, holds it as
    returned, its relay not taking it onto W; and the custodian's press onto W is refused while it waits, held
    as returned. In those two cases the owner's task ends its way back as unbound as it began it, and no line names a
    binding."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    assert runs_on(theirs) == ["cccccccccccc"] and world.get(OWNER, task_id)["bound_org"] is None

    def press_onto(account, status=200):
        r = world.client.post(f"/api/ade/agents/tasks/{theirs}/continue-on", headers=world.h(MATE),
                              json={"state_version": world.get(MATE, theirs)["state_version"],
                                    "backend": {"kind": "account", "id": account}})
        assert r.status_code == status, r.text
        return r

    def meanwhile():
        assert world.get(OWNER, task_id)["state"] == "returning"
        if how == "the relay after the round's stop missed":
            refused_there(world, MATE, theirs, resets=now + HOUR)
        elif how == "the custodian's press":
            assert (world.get(MATE, theirs)["state"], world.get(MATE, theirs)["cause"]) == ("needs_owner", "returned")
            assert press_onto("dddddddddddd", status=412).json()["detail"] == task_offers.RETURNED_REASON
        if how != "pressed back onto the personal plans":
            # the way back started: the custody task moves no more, by its relay or by its custodian's press
            assert runs_on(theirs)[-1] == "cccccccccccc" and world.get(MATE, theirs)["bound_org"] is None
            assert world.get(OWNER, task_id)["bound_org"] is None
        else:
            assert world.get(MATE, theirs)["bound_org"] == "org-w" and runs_on(theirs)[-1] == "cccccccccccc"
    if how == "the custodian's press":
        ended(theirs)                       # its first attempt over: the custody task waits on the custodian's side
    elif how == "pressed back onto the personal plans":
        refused_there(world, MATE, theirs, resets=now + HOUR)
        assert runs_on(theirs) == ["cccccccccccc", "dddddddddddd"]
        ended(theirs)
        world.window(MATE, "cccccccccccc", "allowed", resets_at=now + 5 * HOUR)
        press_onto("cccccccccccc")
    taken_back(world, task_id, offer["id"], theirs, meanwhile)
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.taken_back"]
    assert "bound" not in line["payload"]
    if how != "pressed back onto the personal plans":
        assert world.get(OWNER, task_id)["bound_org"] is None and bound_lines(world, task_id) == []
        return
    assert world.get(OWNER, task_id)["bound_org"] == "org-w"
    assert owner_groups(world, task_id) == ["personal", "org:org-w"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"bound_org": "org-w"}]


def test_custodys_own_return_binds_the_owners_task_before_its_relay_goes_on(world, monkeypatch):
    """Custody's own return, the way back after which
    the owner's relay goes on at once (tasks.take_return, way "return"), binds the owner's task first. The custodian's
    relay took the custody task onto their seat of W at a later attempt, and W's seat refused it too, so it waits on
    the custodian's side; an account of the owner's gets room, and custody brings the task back by itself. The owner's
    task is bound to W, task.bound naming it, before its relay queues its next attempt, on the personal plans."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert runs_on(theirs) == ["cccccccccccc", "dddddddddddd"] and world.get(MATE, theirs)["bound_org"] == "org-w"
    refused_there(world, MATE, theirs, resets=now + 2 * HOUR)
    assert world.get(MATE, theirs)["state"] == "suspended" and world.get(OWNER, task_id)["bound_org"] is None
    world.window(OWNER, "aaaaaaaaaaaa", "allowed")
    task_offers.drive()
    task_offers.drive()
    assert world.offer(offer["id"])["state"] == "returned"
    assert world.get(OWNER, task_id)["bound_org"] == "org-w" and runs_on(task_id)[-1] == "aaaaaaaaaaaa"
    assert owner_groups(world, task_id) == ["personal", "org:org-w"]
    (back,) = [e for e in world.journal(task_id) if e["kind"] == "task.returned"]
    assert back["payload"]["why"] == task_offers.CUSTODY_RETURN
    events = world.journal(task_id)
    (bound,) = [e for e in events if e["kind"] == "task.bound"]
    assert bound["payload"]["bound"] == {"bound_org": "org-w"}
    queued = [e for e in events if e["kind"] == "attempt.queued" and e["seq"] > back["seq"]]
    assert queued and bound["seq"] < queued[0]["seq"]


def test_an_unticked_custody_task_the_custodian_presses_onto_a_seat_binds_the_owners_task(world, monkeypatch):
    """Custody's tick lets the custodian's relay cross; the custodian's
    own press crosses whatever the tick, as an owner's press does. A custody task made unticked on the custodian's
    personal plan, pressed onto their seat of Team organisation Y, is bound to Y, and so is the owner's task as the way
    back ends."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, cross_groups=1)
    world.account(MATE, "dddddddddddd", "Claude Team", "Yarra", "org-y", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    assert world.get(MATE, theirs)["custody_cross"] == 0
    ended(theirs)
    r = world.client.post(f"/api/ade/agents/tasks/{theirs}/continue-on", headers=world.h(MATE),
                          json={"state_version": world.get(MATE, theirs)["state_version"],
                                "backend": {"kind": "account", "id": "dddddddddddd"}})
    assert r.status_code == 200, r.text
    assert world.get(MATE, theirs)["bound_org"] == "org-y"
    taken_back(world, task_id, offer["id"], theirs)
    assert world.get(OWNER, task_id)["bound_org"] == "org-y"
    assert owner_groups(world, task_id) == ["personal", "org:org-y"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"bound_org": "org-y"}]


def test_custody_ends_the_owners_crossings_where_the_custody_tasks_work_spans_two_organisations(world, monkeypatch):
    """Where the custody task's work spans two
    organisations, the owner's task's crossings end, as a press of a task bound to one organisation onto another's seats
    ends them, so that no automatic move carries either's work into the other. The custody task is bound to W in the
    take, and the custodian presses it onto their seat of a second organisation, Y, which ends its crossings: the
    owner's task, bound to W at the hand-off, has its crossings ended as the way back ends. Its relay set is the group
    it is on, the personal plans: an account of the owner's on W's or Z's seats is out. (An owner's task
    bound to Z before the hand-off makes a custody task bound to Z, whose relay never takes W's seats; the test of a
    confirmation while the way back runs, below, reaches that branch.)"""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.window(MATE, "cccccccccccc", "rejected", resets_at=now + HOUR)
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    assert runs_on(theirs) == ["dddddddddddd"] and world.get(MATE, theirs)["bound_org"] == "org-w"
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    mine = world.get(OWNER, task_id)
    assert (mine["bound_org"], mine["crossings_ended"]) == ("org-w", 0)
    assert line["payload"]["bound"] == {"bound_org": "org-w"}
    world.account(MATE, "eeeeeeeeeeee", "Claude Team", "Yarra", "org-y", position=2)
    ended(theirs)
    r = world.client.post(f"/api/ade/agents/tasks/{theirs}/continue-on", headers=world.h(MATE),
                          json={"state_version": world.get(MATE, theirs)["state_version"],
                                "backend": {"kind": "account", "id": "eeeeeeeeeeee"}})
    assert r.status_code == 200, r.text
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-w", 1)
    taken_back(world, task_id, offer["id"], theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-w", 1)
    assert owner_groups(world, task_id) == ["personal"]
    world.account(OWNER, "ffffffffffff", "Claude Team", "Wye", "org-w", position=2)
    world.account(OWNER, "999999999999", "Claude Team", "Zed", "org-z", position=3)
    left = owner_left_out(world, task_id)
    assert left["ffffffffffff"] == left["999999999999"] == CROSSINGS_ENDED_OUT, left
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"crossings_ended": 1}]


def test_an_owners_task_bound_while_with_the_custodian_binds_its_custody_task_whose_relay_then_keeps_to_it(
        world, monkeypatch):
    """A binding the owner's task takes
    while it is with the custodian, its account confirmed again into an organisation, reaches the custody task too, so
    a ticked custody task's relay cannot take the work onto another organisation's seat. Nothing binds at the
    hand-off, the custody task made on the custodian's personal plan and the owner's task bound to no organisation; the
    owner's own account is then confirmed again as a seat of Team organisation Z while the task is with the custodian
    (tasks._account_moved binds a task handed over too), which binds the custody task to Z too, task.bound on it naming
    that (task_offers.bind_custody_task). Refused there, the custody task's relay, custody's crossing ticked, takes the
    custodian's seat of Z, not their seat of W, which comes first in their order; taking W's, it would have the owner's
    task's crossings end as the way back ended. The way back
    changes nothing of the owner's task's binding, and its relay set is the personal plans and Z's seats."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    world.account(MATE, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=2)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    assert runs_on(theirs) == ["cccccccccccc"] and world.get(MATE, theirs)["bound_org"] is None
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    assert line["payload"]["bound"] == {}
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Zed", "org-z")
    assert (world.get(OWNER, task_id)["bound_org"], world.get(OWNER, task_id)["state"]) == ("org-z", "handed_over")
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["crossings_ended"]) == ("org-z", 0)
    assert bound_lines(world, theirs) == [{"offer": offer["id"], "fence": offer["fence"],
                                           "bound": {"bound_org": "org-z"}}]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert runs_on(theirs) == ["cccccccccccc", "eeeeeeeeeeee"]
    taken_back(world, task_id, offer["id"], theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-z", 0) and bound_lines(world, task_id) == []
    assert owner_groups(world, task_id) == ["personal", "org:org-z"]


def test_an_organisation_read_otherwise_reaches_the_custody_task_of_a_task_on_another_of_its_seats(world, monkeypatch):
    """A binding the reread gives a task handed over by custody reaches its
    custody task, as _account_moved's does (task_offers.bind_custody_task). The owner's own account confirmed again as
    a Team seat of org-z while the task is with the custodian binds both tasks to org-z, their crossings going on;
    then ffffffffffff, a personal plan of the owner's, confirmed for the first time as an Enterprise seat of org-z:
    org-z reads as an Enterprise one, so the owner's task's crossings end, and its custody task's with them, task.bound
    naming that."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Zed", "org-z")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-z", 0)
    world.account(OWNER, "ffffffffffff", "Claude Pro", position=2)
    confirmed_again(world, OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z")
    mine = world.get(OWNER, task_id)
    assert (mine["state"], mine["crossings_ended"]) == ("handed_over", 1)
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["crossings_ended"]) == ("org-z", 1)
    assert bound_lines(world, theirs)[-1] == {"offer": offer["id"], "fence": offer["fence"],
                                              "bound": {"crossings_ended": 1}}


def test_a_custody_hand_off_the_owners_reading_changed_under_ends_failed_and_its_task_is_bound(world):
    """A reread at custody's take: the owner's task on aaaaaaaaaaaa, a Team seat of
    org-z, handed to the custodian's receiving agent on their Team seat of org-z. While the hand-off is out,
    ffffffffffff, a personal plan of the owner's, is confirmed again as an Enterprise seat of org-z, so that org-z reads
    to him as an Enterprise organisation and to the custodian still as a Team one: the take's check reads the receiving
    seat as another kind than the one the task is on (tasks.enterprise_move), so the hand-off ends failed, and the task,
    bound to no organisation until then, is bound as the offer ends untaken (tasks.apply_held_reread) as a press onto
    an Enterprise organisation's seat binds it: to org-z, its crossings ended."""
    task_id, mate_agent = custody_on_seats(world, "Claude Team", "Claude Team")
    world.account(OWNER, "ffffffffffff", "Claude Pro", position=1)
    found, hold = hand(world, task_id)
    assert found["agent_id"] == mate_agent
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    confirmed_again(world, OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z")
    assert [m["read_from"] for m in json.loads(world.offer(offer["id"])["reread"])] == ["team"]
    world.running(OWNER, MATE)
    world.settle()
    assert world.offer(offer["id"])["state"] == "failed", world.offer(offer["id"])
    mine = world.get(OWNER, task_id)
    assert (mine["crossings_ended"], mine["bound_org"]) == (1, "org-z"), dict(mine)
    (line,) = [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"
               and e["payload"].get("at_offer_end")]
    assert (line["at_offer_end"], line["bound"]) == ("untaken", {"crossings_ended": 1, "bound_org": "org-z"}), line


def test_a_custody_hand_off_s_untaken_end_waits_by_the_binding_its_reread_gave(world, monkeypatch):
    """As a hand-off ends untaken, the binding a reread held back is applied first (tasks.apply_held_reread), and the
    task then waits by its relay set as that binding leaves it: were _back to pass _wait_again the task as it read it
    before the binding, a custody hand-off's wait would count the room of an account outside the organisation the
    binding now keeps the task in. The owner's task on aaaaaaaaaaaa, a Team seat of org-z,
    its crossing switch on, refused for an hour; org-z also holds ffffffffffff, his Enterprise seat, refused for two,
    so org-z reads to him as an Enterprise organisation and his personal plan bbbbbbbbbbbb, with room, is outside the
    task's relay set; the custodian's receiving agent on their Enterprise seat of org-z. While the hand-off is out,
    ffffffffffff is confirmed again as a Team seat (the way out): org-z reads to him as a Team organisation, to the
    custodian still as an Enterprise one, so the take fails, and the task's crossings end as the hand-off ends
    untaken. It waits for aaaaaaaaaaaa's reset, the earliest of org-z's seats; the task as read before the binding, its
    crossings going on, would count bbbbbbbbbbbb, in its relay set then, and wait for nothing."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    now = int(time.time())
    task_id, mate_agent = custody_on_seats(world, "Claude Team", "Claude Enterprise", cross_groups=1)
    world.account(OWNER, "ffffffffffff", "Claude Enterprise", "Zed", "org-z", position=1)
    world.window(OWNER, "ffffffffffff", "rejected", resets_at=now + 2 * HOUR)
    world.account(OWNER, "bbbbbbbbbbbb", "Claude Pro", position=2)
    world.window(OWNER, "bbbbbbbbbbbb", "allowed")
    found, hold = hand(world, task_id)
    assert found["agent_id"] == mate_agent
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    confirmed_again(world, OWNER, "ffffffffffff", "Claude Team", "Zed", "org-z")
    assert [m["read_from"] for m in json.loads(world.offer(offer["id"])["reread"])] == ["enterprise"]
    world.running(OWNER, MATE)
    world.settle()
    assert world.offer(offer["id"])["state"] == "failed", world.offer(offer["id"])
    mine = world.get(OWNER, task_id)
    assert (mine["state"], mine["crossings_ended"]) == ("suspended", 1), dict(mine)
    with store.connect() as db:
        aaaa_reset = db.execute("SELECT resets_at FROM ade_backend_windows WHERE user_id = ? AND backend = ? AND "
                                "rate_window = 'five_hour'", (world.user(OWNER), "account:aaaaaaaaaaaa")).fetchone()[0]
    assert mine["waits_until"] == aaaa_reset, dict(mine)


def test_a_custody_hand_off_taken_after_its_organisation_was_read_otherwise_binds_its_custody_task(world, monkeypatch):
    """Where custody's take goes on, the binding a reread held back while
    the hand-off was out is applied as the custodian's side takes it, and reaches the custody task before its first
    attempt (task_offers.bind_custody_task), so that custody's crossing, ticked, takes the work nowhere. The owner's
    task on aaaaaaaaaaaa, a Team seat of org-z, custody's crossing ticked; the custodian's receiving agent on their Team
    seat of org-z. The take's own check reads both users' organisations as they read now, so a hand-off reaches the
    take with a reread behind it only where the owner's and the custodian's readings changed alike (the test above
    shows the other case ending failed): the mark is written here as tasks._organisation_reread writes it, org-z read
    as an Enterprise organisation when the owner's reread came, and as a Team one by both now."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent = custody_on_seats(world, "Claude Team", "Claude Team", custody_cross=1, cross_groups=1)
    found, hold = hand(world, task_id)
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    with store.connect() as db:
        db.execute("UPDATE ade_agent_task_offers SET reread = ? WHERE id = ?",
                   (json.dumps([{"grp": "org:org-z", "read_from": "enterprise", "by": "account:ffffffffffff"}]),
                    offer["id"]))
    world.running(OWNER, MATE)
    world.settle()
    offer = world.offer(offer["id"])
    assert offer["state"] == "accepted", offer
    mine = world.get(OWNER, task_id)
    assert (mine["state"], mine["crossings_ended"]) == ("handed_over", 1), dict(mine)
    (line,) = [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"
               and e["payload"].get("at_offer_end")]
    assert (line["at_offer_end"], line["bound"]) == ("accepted", {"crossings_ended": 1}), line
    assert "its offer has been accepted" in line["why"], line["why"]
    theirs = world.get(MATE, offer["their_task"])
    assert theirs["crossings_ended"] == 1, dict(theirs)
    assert bound_lines(world, offer["their_task"])[-1] == {"offer": offer["id"], "fence": offer["fence"],
                                                           "bound": {"crossings_ended": 1}}


def test_an_owners_task_bound_while_its_custody_task_is_on_another_organisations_seat_ends_both_tasks_crossings(
        world, monkeypatch):
    """A binding the owner's task takes while with the custodian, where the two bindings differ: the custodian's relay
    took the custody task onto
    their seat of Team organisation W at a later attempt, which binds it to W; the owner's account is then confirmed
    again as a seat of Team organisation Z while the task is with the custodian, binding the owner's task to Z. The
    custody task keeps W and its crossings end, as a press of a task bound to W onto Z's seats ends them, task.bound on
    it naming that; and as the way back ends the owner's task's crossings end too, its relay set the group it is
    on, Z's seats."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert runs_on(theirs) == ["cccccccccccc", "dddddddddddd"] and world.get(MATE, theirs)["bound_org"] == "org-w"
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Zed", "org-z")
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["crossings_ended"]) == ("org-w", 1)
    assert [p["bound"] for p in bound_lines(world, theirs)] == [{"crossings_ended": 1}]
    taken_back(world, task_id, offer["id"], theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-z", 1)
    assert owner_groups(world, task_id) == ["org:org-z"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"crossings_ended": 1}]


def test_an_owners_task_bound_while_its_way_back_runs_leaves_its_custody_task_and_its_crossings_end_as_it_ends(
        world, monkeypatch):
    """A binding the owner's task takes while with the custodian, not during a way back: once it starts the custody
    task moves no more, and its end gives the owner's task the custody task's binding. The custodian's relay took the
    custody task onto their seat of Team organisation W; the owner presses Take back; while the way back runs, the
    owner's task returning, the owner's account is confirmed again as a seat of Team organisation Z, binding the owner's
    task to Z. The custody task is left as it was, no task.bound line on it; as the way back ends the owner's task keeps
    Z, not written over with W, and its crossings end, by the branch of task_offers._bind_as_custody for a task bound to
    another organisation than the other task's. Its relay set is then the group it is
    on, Z's seats, and an account of the owner's on the personal plans or on W's seats is out."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert world.get(MATE, theirs)["bound_org"] == "org-w"
    seen = {}

    def meanwhile():
        seen["state"] = world.get(OWNER, task_id)["state"]
        confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Zed", "org-z")
        seen["mine"] = world.get(OWNER, task_id)["bound_org"]
        seen["theirs"] = world.get(MATE, theirs)

    taken_back(world, task_id, offer["id"], theirs, meanwhile=meanwhile)
    assert (seen["state"], seen["mine"]) == ("returning", "org-z")
    assert (seen["theirs"]["bound_org"], seen["theirs"]["crossings_ended"]) == ("org-w", 0)
    assert bound_lines(world, theirs) == []
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-z", 1)
    assert owner_groups(world, task_id) == ["org:org-z"]
    world.account(OWNER, "ffffffffffff", "Claude Team", "Wye", "org-w", position=2)
    left = owner_left_out(world, task_id)
    assert left["bbbbbbbbbbbb"] == left["ffffffffffff"] == CROSSINGS_ENDED_OUT, left
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"crossings_ended": 1}]


def test_an_owners_task_whose_crossings_end_while_with_the_custodian_ends_its_custody_tasks_crossings_too(
        world, monkeypatch):
    """A binding the owner's task takes while with the custodian, for the crossings: the owner's personal task bound to
    Team organisation Z, custody's crossing ticked, makes a custody task bound to Z on the custodian's personal plan;
    the owner's account is then confirmed again as a seat of Team organisation W while the task is with the custodian,
    which ends the owner's task's crossings, as a press of a task bound to Z onto W's seats ends them. The custody
    task's crossings end too, task.bound on it naming that, so that refused, its relay keeps it in the group it is on
    rather than taking the custodian's seat of Z. The way back then changes nothing more of the owner's task's
    binding."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1, bound_org="org-z")
    now = int(time.time())
    world.account(MATE, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["custody_cross"], made["crossings_ended"]) == ("org-z", 1, 0)
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Wye", "org-w")
    mine = world.get(OWNER, task_id)
    assert (mine["bound_org"], mine["crossings_ended"]) == ("org-z", 1)
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["crossings_ended"]) == ("org-z", 1)
    assert [p["bound"] for p in bound_lines(world, theirs)] == [{"crossings_ended": 1}]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert runs_on(theirs) == ["cccccccccccc"]
    taken_back(world, task_id, offer["id"], theirs)
    assert bound_lines(world, task_id) == []


def test_an_owners_task_confirmed_onto_an_enterprise_seat_while_with_the_custodian_binds_its_custody_task_there(
        world, monkeypatch):
    """A binding the owner's task takes while with the custodian, onto an Enterprise seat (this test fails if the
    custody task's binding is skipped where the confirmation puts the owner's account on an Enterprise seat). A ticked
    custody task made bound to no organisation on the custodian's personal plan; the owner's account confirmed again as
    an Enterprise seat of E binds the owner's task to E and ends its crossings, as a press onto an Enterprise seat does.
    The custody task is bound to E and its crossings end too, task.bound on it naming both, so that refused, its relay
    keeps it in the group it is on rather than taking the custodian's seat of Team organisation W. The way back then
    changes nothing more."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    assert world.get(MATE, theirs)["bound_org"] is None
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Enterprise", "Eee", "org-e")
    mine = world.get(OWNER, task_id)
    assert (mine["bound_org"], mine["crossings_ended"]) == ("org-e", 1)
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["crossings_ended"]) == ("org-e", 1)
    assert [p["bound"] for p in bound_lines(world, theirs)] == [{"bound_org": "org-e", "crossings_ended": 1}]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert runs_on(theirs) == ["cccccccccccc"]
    taken_back(world, task_id, offer["id"], theirs)
    assert bound_lines(world, task_id) == []


def test_an_unticked_custody_task_takes_its_owners_binding_from_a_confirmation_too(world, monkeypatch):
    """A binding the owner's task takes while with the custodian, custody's tick or not (this test fails if the custody
    task's binding is given to a ticked custody task alone). A custody task made unticked and bound to no organisation
    on the custodian's personal plan; the owner's account confirmed again as a seat of Team organisation Z binds the
    owner's task to Z, and the custody task to Z too, task.bound on it naming that, as an account of the custodian's
    confirmed again would bind it, its tick or not."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, cross_groups=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    assert (world.get(MATE, theirs)["custody_cross"], world.get(MATE, theirs)["bound_org"]) == (0, None)
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Zed", "org-z")
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["crossings_ended"]) == ("org-z", 0)
    assert [p["bound"] for p in bound_lines(world, theirs)] == [{"bound_org": "org-z"}]


def test_a_custody_task_bound_already_as_its_owners_task_becomes_is_left_as_it_was(world, monkeypatch):
    """A binding the owner's task takes while with the custodian, where the custody task needs no change (this test
    fails if task.bound is written with an empty `bound`, where it is written only where a field changed). The
    custodian's relay took a ticked custody task onto their seat of Team organisation Z, which binds it to Z, while the
    owner's task stayed bound to no organisation; the owner's account confirmed again as a seat of Z then binds the
    owner's task to Z, and the custody task, bound to Z already, is left as it was: no task.bound line on it, and its
    state_version unmoved."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert world.get(MATE, theirs)["bound_org"] == "org-z" and world.get(OWNER, task_id)["bound_org"] is None
    version = world.get(MATE, theirs)["state_version"]
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Zed", "org-z")
    assert world.get(OWNER, task_id)["bound_org"] == "org-z"
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["crossings_ended"], made["state_version"]) == ("org-z", 0, version)
    assert bound_lines(world, theirs) == []


@pytest.mark.parametrize("plan", ["Claude Team", "Claude Enterprise"])
def test_a_confirmation_that_changes_nothing_of_the_owners_binding_leaves_the_custody_task_as_it_was(world, plan):
    """A binding the owner's task takes while with the custodian, only where the confirmation changed the binding of the
    owner's task: the owner's task bound to Team organisation Z, its crossings ended, waiting on the personal plans,
    makes a custody task bound to Z on the custodian's personal plan, its crossing not ticked and its crossings not
    ended; the owner's account confirmed again as a seat of Z changes nothing of the owner's task's binding, its line's
    `bound` empty, and the custody task is left as it was, no task.bound line on it. As a Team seat of Z the press's
    binding fields are none (tasks._onto_group); as an Enterprise seat of Z they are `crossings_ended` 1, which the
    owner's task has already (this test fails if the custody task's binding is given wherever those fields are not
    empty)."""
    task_id, mate_agent, _ = setup_custody(world, bound_org="org-z", crossings_ended=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", plan, "Zed", "org-z")
    (line,) = [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.account_regrouped"]
    assert line["bound"] == {}
    made = world.get(MATE, theirs)
    assert (made["bound_org"], made["crossings_ended"]) == ("org-z", 0) and bound_lines(world, theirs) == []


def test_custody_binds_a_personal_task_where_the_custodians_own_press_puts_its_work_on_an_enterprise_seat(
        world, monkeypatch):
    """No automatic move ever carries one organisation's work into another: custody
    hands a personal task onto no Enterprise organisation's seat (_custody_groups), nor does a relay move one onto it
    (tasks._enterprise_move), but the custodian's own press can put the custody task there, which binds it and ends
    its crossings (tasks._onto_group). The owner's task, bound to no organisation, is then bound to that organisation
    as its way back ends, and its crossings end too, task.bound naming both: so
    after the way back its relay set is the group it is on, the personal plans, and an account of the owner's on that
    organisation's Team seats, or on another organisation's, Y's, is out."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Enterprise", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    assert runs_on(theirs) == ["cccccccccccc"]
    refused_there(world, MATE, theirs, resets=now + HOUR)        # no relay moves it onto W's seat: it waits
    assert world.get(MATE, theirs)["state"] == "suspended" and runs_on(theirs) == ["cccccccccccc"]
    r = world.client.post(f"/api/ade/agents/tasks/{theirs}/continue-on", headers=world.h(MATE),
                          json={"state_version": world.get(MATE, theirs)["state_version"],
                                "backend": {"kind": "account", "id": "dddddddddddd"}})
    assert r.status_code == 200, r.text
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-w", 1)
    assert world.get(OWNER, task_id)["bound_org"] is None
    taken_back(world, task_id, offer["id"], theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-w", 1)
    assert owner_groups(world, task_id) == ["personal"]
    world.account(OWNER, "eeeeeeeeeeee", "Claude Team", "Yarra", "org-y", position=2)
    world.account(OWNER, "ffffffffffff", "Claude Team", "Wye", "org-w", position=3)
    left = owner_left_out(world, task_id)
    assert left["eeeeeeeeeeee"] == left["ffffffffffff"] == CROSSINGS_ENDED_OUT, left
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"bound_org": "org-w", "crossings_ended": 1}]


def test_custody_binds_a_personal_task_where_its_custody_task_was_though_the_custodian_deleted_it(world, monkeypatch):
    """The way back reads the custody task's binding from its row whatever became of it. The custodian's relay
    took the work onto their seat of Team organisation W at a later attempt; the custodian stopped the custody task
    and deleted it before the round came to it, so the round finds it gone and brings the task back, and the owner's
    task is bound to W as its way back ends, task.bound naming it."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1)
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert world.get(MATE, theirs)["bound_org"] == "org-w" and world.get(OWNER, task_id)["bound_org"] is None
    ended(theirs, state="stopped")
    r = world.client.request("DELETE", f"/api/ade/agents/tasks/{theirs}", headers=world.h(MATE),
                             json={"state_version": world.get(MATE, theirs)["state_version"]})
    assert r.status_code == 200, r.text
    assert world.row("ade_agent_tasks", "id", theirs)["deleted_at"] is not None
    task_offers.drive()
    task_offers.drive()
    assert world.offer(offer["id"])["state"] == "returned"
    assert world.get(OWNER, task_id)["bound_org"] == "org-w"
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"bound_org": "org-w"}]


def test_a_custody_task_takes_the_owners_binding_so_its_relay_crosses_into_that_organisation_alone(world, monkeypatch):
    """A custody task made on the custodian's personal plan takes the owner's task's binding: the owner's
    personal task bound to Team organisation Z, custody's crossing ticked, handed to the receiving agent on the
    custodian's personal plan, makes a custody task bound to Z, so that its relay's groups are the personal plans and
    Z's seats. The receiving account refusing it, the custodian's relay takes it onto their seat of Z, not onto their
    seat of W, which comes first in their order (no automatic move ever carries one organisation's work
    into another), as its relay would were the custody task made bound to no organisation. The way
    back then changes nothing of the owner's task's binding."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1, bound_org="org-z")
    now = int(time.time())
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    world.account(MATE, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=2)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    made = world.get(MATE, theirs)
    assert (made["home_grp"], made["bound_org"], made["custody_cross"], made["crossings_ended"]) == (
        "personal", "org-z", 1, 0)
    with store.connect() as db:
        assert tasks.relay_groups(db, tasks.get(db, world.user(MATE), theirs))[0] == ["personal", "org:org-z"]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert runs_on(theirs) == ["cccccccccccc", "eeeeeeeeeeee"]
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-z", 0)
    taken_back(world, task_id, offer["id"], theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-z", 0) and bound_lines(world, task_id) == []


def test_an_unticked_custody_task_takes_the_owners_binding_and_a_seat_of_another_organisation_ends_its_crossings(
        world, monkeypatch):
    """A custody task takes the owner's binding, and a confirmation of the custodian's account binds it as one of their
    own: the owner's personal task bound to Team organisation Z, custody's crossing not
    ticked, handed to the custodian's personal plan, makes a custody task bound to Z, whose relay still never crosses.
    The custodian then confirms that account again as a seat of Team organisation W: the custody task gets the binding a
    press from the personal plans onto W's seats gives, as a task of the custodian's own would, its tick or not, so its
    crossings end, W's work now beside Z's binding (tasks._account_moved; its line names no "received" left out). As its
    way back ends, the owner's task's crossings end too (task_offers._bind_as_custody), so its relay carries the
    work into no organisation. Were the custody task made unbound and left out, the owner's task would come back
    bound to Z with its crossings going on."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, cross_groups=1, bound_org="org-z")
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    made = world.get(MATE, theirs)
    assert (made["home_grp"], made["bound_org"], made["custody_cross"]) == ("personal", "org-z", 0)
    ended(theirs)
    confirmed_again(world, MATE, "cccccccccccc", "Claude Team", "Wye", "org-w")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-z", 1)
    (line,) = [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.account_regrouped"]
    assert (line["left_out"], line["bound"]) == (None, {"crossings_ended": 1})
    taken_back(world, task_id, offer["id"], theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-z", 1)
    assert owner_groups(world, task_id) == ["personal"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"crossings_ended": 1}]


def test_an_unticked_custody_task_on_an_account_confirmed_as_a_seat_is_bound_there_and_so_is_the_owners_task(
        world, monkeypatch):
    """A confirmation of the custodian's account binds a custody task as one of their own: the owner's personal task
    bound to
    no organisation, custody's crossing not ticked, handed to the custodian's personal plan; the custodian then
    confirms that account again as a seat of Team organisation Y. The custody task, which runs on that seat now, is
    bound to Y by that confirmation as a task of the custodian's own would be, its relay still never crossing; and as
    its way back ends the owner's task is bound to Y too, so its relay crosses into Y's seats alone. Were the
    custody task left out ("received"), it would stay unbound, and so would the owner's task, its relay set every Team
    organisation's seats."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, cross_groups=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["custody_cross"]) == (None, 0)
    ended(theirs)
    confirmed_again(world, MATE, "cccccccccccc", "Claude Team", "Yarra", "org-y")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-y", 0)
    (line,) = [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.account_regrouped"]
    assert (line["left_out"], line["bound"]) == (None, {"bound_org": "org-y"})
    taken_back(world, task_id, offer["id"], theirs)
    assert world.get(OWNER, task_id)["bound_org"] == "org-y"
    assert owner_groups(world, task_id) == ["personal", "org:org-y"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"bound_org": "org-y"}]


def test_a_custody_task_made_on_an_organisations_seat_is_bound_to_that_organisation_whatever_the_owners_binding(
        world, monkeypatch):
    """A custody task made on the custodian's personal plans binds to the owner's task's
    organisation; one made on an organisation's seat is bound to that organisation, as any task made on a seat is
    (tasks.create). Custody puts a task bound to Z on another organisation's seat only where its crossings ended with it
    on that organisation's seats (an owner's press onto them, or an account of the owner's confirmed again as one of
    them, ends them), so that custody hands it into the group it is on alone (_custody_groups): the owner's task bound
    to Z, on their seat of W, its crossings ended, handed to the custodian's seat of W, makes a custody task bound to
    W, its crossing not ticked."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    world.account(OWNER, "ffffffffffff", "Claude Team", "Wye", "org-w", position=2)
    world.window(OWNER, "ffffffffffff", "rejected", resets_at=int(time.time()) + HOUR)
    task_id, mate_agent, _ = setup_custody(world, mate_plan="Claude Team", mate_org="Wye", mate_org_id="org-w",
                                           account="ffffffffffff", bound_org="org-z", crossings_ended=1)
    offer = handed(world, task_id)
    made = world.get(MATE, offer["their_task"])
    assert (made["home_grp"], made["bound_org"], made["custody_cross"]) == ("org:org-w", "org-w", 0)


def test_a_custody_task_takes_the_owners_binding_where_the_owners_crossings_ended_on_the_personal_plans(world):
    """A custody task made on the personal plans takes the owner's binding where the owner's task's crossings ended: a
    press from a Team organisation's
    seats onto the personal plans keeps a task's binding and its crossings as they were (tasks._onto_group), so an
    owner's task bound to Team organisation Z can wait on the personal plans with its crossings ended. Custody hands it
    into the group it is on alone, the custodian's personal plan, its crossing not ticked (_take), and the custody task
    made there takes the owner's task's binding all the same, as every custody task made on the personal plans does."""
    task_id, mate_agent, _ = setup_custody(world, bound_org="org-z", crossings_ended=1)
    offer = handed(world, task_id)
    made = world.get(MATE, offer["their_task"])
    assert (made["home_grp"], made["bound_org"], made["custody_cross"]) == ("personal", "org-z", 0)


def test_a_custody_task_from_a_task_homed_on_an_organisations_seats_takes_its_binding_on_the_personal_plans(
        world, monkeypatch):
    """A custody task made on the personal plans takes the owner's binding, for an owner's task homed on Team
    organisation Z's seats (this test fails if the binding is passed only for a task homed on, or now on, the personal
    plans). Made there, the task is bound to Z; custody's crossing ticked, it is handed to the custodian's personal
    plan, a crossing between one Team organisation's seats and the personal plans (_custody_groups), and the custody
    task made there takes Z: its relay's groups are the personal plans and Z's seats, and the receiving account refusing
    it, the relay takes the custodian's seat of Z, not their seat of W, which comes first in their order."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    now = int(time.time())
    world.account(OWNER, "ffffffffffff", "Claude Team", "Zed", "org-z", position=2)
    world.window(OWNER, "ffffffffffff", "rejected", resets_at=now + HOUR)
    task_id, mate_agent, _ = setup_custody(world, custody_cross=1, cross_groups=1, home="org:org-z",
                                           bound_org="org-z", account="ffffffffffff")
    world.account(MATE, "dddddddddddd", "Claude Team", "Wye", "org-w", position=1)
    world.account(MATE, "eeeeeeeeeeee", "Claude Team", "Zed", "org-z", position=2)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    made = world.get(MATE, theirs)
    assert (made["home_grp"], made["bound_org"], made["custody_cross"]) == ("personal", "org-z", 1)
    with store.connect() as db:
        assert tasks.relay_groups(db, tasks.get(db, world.user(MATE), theirs))[0] == ["personal", "org:org-z"]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert runs_on(theirs) == ["cccccccccccc", "eeeeeeeeeeee"]


def test_an_unticked_custody_tasks_binding_still_changes_by_a_confirmation_while_its_way_back_runs(world, monkeypatch):
    """The manual: once the way back starts the custody task moves no more, "but an account of the custodian's confirmed
    again can still change its binding" (this test fails with no binding once the custody task is marked returned). An
    unticked custody task, made bound to Z on the custodian's personal plan; the owner's Take back; while the way back
    runs, its custody task marked returned and the owner's task returning, the custodian's account is confirmed again as
    a seat of W: the custody task's crossings end, as a press of a task bound to Z onto W's seats ends them, and as the
    way back ends the owner's task's end too."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, cross_groups=1, bound_org="org-z")
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    ended(theirs)
    seen = {}

    def meanwhile():
        seen["before"] = (world.get(MATE, theirs)["returned"], world.get(OWNER, task_id)["state"])
        confirmed_again(world, MATE, "cccccccccccc", "Claude Team", "Wye", "org-w")
        seen["theirs"] = world.get(MATE, theirs)

    taken_back(world, task_id, offer["id"], theirs, meanwhile=meanwhile)
    assert seen["before"] == (1, "returning")
    assert (seen["theirs"]["bound_org"], seen["theirs"]["crossings_ended"]) == ("org-z", 1)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-z", 1)
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"crossings_ended": 1}]


def test_an_unticked_custody_task_is_bound_by_a_confirmation_once_its_way_back_has_ended(world, monkeypatch):
    """Nothing tells a custody task from a pressed offer's at a
    confirmation, every received task getting the binding, so this pins only that a custody task whose way back has
    ended, its offer returned, still gets it. Its account confirmed again as a seat of Y, the unticked custody task is
    bound to Y, its line's `left_out` null."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, cross_groups=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    ended(theirs)
    taken_back(world, task_id, offer["id"], theirs)
    assert world.offer(offer["id"])["state"] == "returned"
    confirmed_again(world, MATE, "cccccccccccc", "Claude Team", "Yarra", "org-y")
    (line,) = [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.account_regrouped"]
    assert (line["left_out"], line["bound"]) == (None, {"bound_org": "org-y"})


def test_an_unticked_custody_task_confirmed_onto_an_enterprise_seat_is_bound_there_and_so_is_the_owners_task(
        world, monkeypatch):
    """A confirmation of the custodian's account binds a custody task as one of their own, onto an Enterprise seat (this
    test fails if the binding is given on a Team seat alone). The owner's task bound to no organisation, custody's
    crossing not ticked, handed to the custodian's personal plan; that account confirmed again as an Enterprise seat of
    E: as a press from the personal plans onto an Enterprise seat would, the custody task is bound to E and its
    crossings end, its line naming no "received" left out; as the way back ends the owner's task is bound to E, its
    crossings ended too."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, _ = setup_custody(world, cross_groups=1)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    ended(theirs)
    confirmed_again(world, MATE, "cccccccccccc", "Claude Enterprise", "Eee", "org-e")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-e", 1)
    (line,) = [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.account_regrouped"]
    assert line["left_out"] is None
    taken_back(world, task_id, offer["id"], theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-e", 1)


def test_an_unticked_custody_task_on_a_team_seat_confirmed_as_that_organisations_enterprise_seat_ends_its_crossings(
        world, monkeypatch):
    """A confirmation of the custodian's account binds a custody task as one of their own, for an account confirmed
    again as another kind of seat of the same organisation (this test fails if the binding is given on a move into
    another group alone). The owner's task homed on Z's Team seats, custody's crossing not ticked, handed to the
    custodian's seat of Z, makes a custody task bound to Z; that seat confirmed again as an Enterprise seat of Z ends
    its crossings, as a press onto an Enterprise seat ends them, and the owner's task's end as the way back ends."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    now = int(time.time())
    world.account(OWNER, "ffffffffffff", "Claude Team", "Zed", "org-z", position=2)
    world.window(OWNER, "ffffffffffff", "rejected", resets_at=now + HOUR)
    task_id, mate_agent, _ = setup_custody(world, mate_plan="Claude Team", mate_org="Zed", mate_org_id="org-z",
                                           cross_groups=1, home="org:org-z", bound_org="org-z",
                                           account="ffffffffffff")
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    made = world.get(MATE, theirs)
    assert (made["home_grp"], made["bound_org"], made["custody_cross"]) == ("org:org-z", "org-z", 0)
    ended(theirs)
    confirmed_again(world, MATE, "cccccccccccc", "Claude Enterprise", "Zed", "org-z")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-z", 1)
    taken_back(world, task_id, offer["id"], theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-z", 1)


def test_a_task_received_by_a_pressed_offer_takes_no_binding_of_the_owners_task(world):
    """The owner's binding given where a custody task is made is custody's alone: a task received
    by a pressed offer never crosses, and the account it runs on is the teammate's own choice at their acceptance. The
    owner's personal task bound to Team organisation Z, offered by a press and accepted into the teammate's agent on
    their personal plan, makes a received task bound to no organisation."""
    world.account(OWNER, "aaaaaaaaaaaa", "Claude Max")
    task_id = world.task(OWNER, world.agent(OWNER), bound_org="org-z")
    world.account(MATE, "cccccccccccc", "Claude Max")
    mate_agent = world.agent(MATE, account="cccccccccccc", name="Helper")
    made = offered(world, task_id)
    r = world.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=world.h(MATE),
                          json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    theirs = world.get(MATE, r.json()["task_id"])
    assert (theirs["home_grp"], theirs["bound_org"]) == ("personal", None)


def test_a_pressed_offer_binds_the_owners_task_at_its_acceptance_as_custodys_hand_off_does(world, monkeypatch):
    """A pressed offer accepted into
    a teammate's agent on an organisation's seat binds the owner's task as custody's hand-off does (no
    automatic move ever carries one organisation's work into another). The owner's personal task, bound to no
    organisation, offered by a press and accepted into the teammate's agent on a seat of Team organisation Y, its
    received task bound to Y, is bound to Y at the acceptance, its task.handed_over's `bound` naming that, with no
    `crossed`, which is custody's; the way back, the owner's Take back, changes nothing more, no task.bound line, and
    the owner's relay set is the personal plans and Y's seats, not every Team organisation's seats, as it would be
    were the owner's task to stay bound to no organisation."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world, cross_groups=1)
    assert world.get(MATE, theirs)["bound_org"] == "org-y"
    mine = world.get(OWNER, task_id)
    assert (mine["bound_org"], mine["crossings_ended"]) == ("org-y", 0)
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    assert line["payload"] == {"offer": offer_id, "fence": world.offer(offer_id)["fence"], "why": None,
                               "bound": {"bound_org": "org-y"}}
    taken_back(world, task_id, offer_id, theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-y", 0) and bound_lines(world, task_id) == []
    assert owner_groups(world, task_id) == ["personal", "org:org-y"]


def test_a_pressed_offers_way_back_binds_the_owners_task_as_its_received_task_is_bound_as_it_ends(world, monkeypatch):
    """A pressed offer binds the owner's task again as the offer's way back ends. Accepted into the
    teammate's agent on their personal plan, the received task is bound to no organisation, so the acceptance binds
    nothing (`bound` {}); the teammate then presses Continue on their seat of Team organisation Y, which binds the
    received task to Y (tasks._onto_group), and as the way back ends the owner's task is bound to Y too, task.bound
    naming that (task_offers._carry_back, which binds for a pressed offer's way back as for custody's)."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                         cross_groups=1)
    assert world.get(MATE, theirs)["bound_org"] is None and world.get(OWNER, task_id)["bound_org"] is None
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    assert line["payload"]["bound"] == {}
    world.account(MATE, "dddddddddddd", "Claude Team", "Yarra", "org-y", position=1)
    ended(theirs)
    r = world.client.post(f"/api/ade/agents/tasks/{theirs}/continue-on", headers=world.h(MATE),
                          json={"state_version": world.get(MATE, theirs)["state_version"],
                                "backend": {"kind": "account", "id": "dddddddddddd"}})
    assert r.status_code == 200, r.text
    assert world.get(MATE, theirs)["bound_org"] == "org-y" and world.get(OWNER, task_id)["bound_org"] is None
    taken_back(world, task_id, offer_id, theirs)
    assert world.get(OWNER, task_id)["bound_org"] == "org-y"
    assert owner_groups(world, task_id) == ["personal", "org:org-y"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"bound_org": "org-y"}]


def test_a_pressed_offer_accepted_onto_another_organisations_seat_ends_the_owners_crossings(world, monkeypatch):
    """A pressed offer's binding at its acceptance, where the two bindings differ: the owner's personal task bound to
    Team organisation Z, accepted into the teammate's agent on a seat of Team organisation W, its received task bound to
    W, keeps Z and its crossings end at the acceptance (`bound` {"crossings_ended": 1}), as custody's hand-off ends
    them, so that no automatic move carries either organisation's work into the other. The way back changes nothing
    more, and the owner's relay set is the group the task is on, the personal plans."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world, mate_org="Wye", mate_org_id="org-w", cross_groups=1,
                                         bound_org="org-z")
    assert world.get(MATE, theirs)["bound_org"] == "org-w"
    mine = world.get(OWNER, task_id)
    assert (mine["bound_org"], mine["crossings_ended"]) == ("org-z", 1)
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    assert line["payload"]["bound"] == {"crossings_ended": 1}
    taken_back(world, task_id, offer_id, theirs)
    assert bound_lines(world, task_id) == [] and owner_groups(world, task_id) == ["personal"]


def test_a_pressed_offer_accepted_onto_an_enterprise_seat_binds_the_owners_task_to_that_organisation(
        world, monkeypatch):
    """A pressed offer's binding at its acceptance, onto an Enterprise seat (this test fails if the acceptance binds the
    owner's task only where the seat is a Team one). The owner's personal task, bound to no organisation, accepted into
    the teammate's agent on an Enterprise seat of E, its received task made bound to E with its crossings not ended, is
    bound to E at the acceptance, its task.handed_over's `bound` {"bound_org": "org-e"}; the way back changes nothing
    more."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world, mate_plan="Claude Enterprise", mate_org="Eee", mate_org_id="org-e",
                                         cross_groups=1)
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-e", 0)
    mine = world.get(OWNER, task_id)
    assert (mine["bound_org"], mine["crossings_ended"]) == ("org-e", 0)
    (line,) = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"]
    assert line["payload"]["bound"] == {"bound_org": "org-e"}
    taken_back(world, task_id, offer_id, theirs)
    assert bound_lines(world, task_id) == [] and world.get(OWNER, task_id)["bound_org"] == "org-e"


def test_a_task_handed_over_by_a_pressed_offer_binds_nothing_of_its_received_task_at_a_confirmation(world):
    """A binding the owner's task takes while it is handed over reaches a custody task alone: a pressed offer's received
    task's relay keeps to the group it was accepted in, and task_offers.bind_custody_task finds custody's accepted offer
    alone. The owner's task handed over by a pressed offer accepted onto the teammate's personal plan; the owner's
    account then confirmed again as a seat of Team organisation Z, which binds the owner's task to Z: its received task
    is left as it was, bound to no organisation, no task.bound line on it."""
    task_id, offer_id, theirs = accepted(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None)
    confirmed_again(world, OWNER, "aaaaaaaaaaaa", "Claude Team", "Zed", "org-z")
    assert (world.get(OWNER, task_id)["bound_org"], world.get(OWNER, task_id)["state"]) == ("org-z", "handed_over")
    received = world.get(MATE, theirs)
    assert (received["bound_org"], received["crossings_ended"]) == (None, 0) and bound_lines(world, theirs) == []


def test_a_task_received_by_a_pressed_offer_is_bound_by_a_confirmation_of_its_account_and_so_is_the_owners_task(
        world, monkeypatch):
    """A task
    received by a pressed offer is bound by a confirmation of its account into an organisation, as a custody task
    is whatever its tick. The owner's personal task, bound to no organisation, offered by a press and accepted into
    the teammate's agent on their personal plan, its received task bound to none; the teammate then confirms that
    account again as a seat of Team organisation Y. The received task, which runs on that seat now, is bound to Y, its
    line's `left_out` null and its `bound` naming that, its relay set still the group it was accepted in, the personal
    plans; and as its way back ends the owner's task is bound to Y too, task.bound naming that, so its relay crosses
    into Y's seats alone. Were the received task left out ("received"), it would stay unbound, and so would the
    owner's task, its relay set every Team organisation's seats."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                         cross_groups=1)
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["custody_cross"]) == (None, 0)
    ended(theirs)
    confirmed_again(world, MATE, "cccccccccccc", "Claude Team", "Yarra", "org-y")
    received = world.get(MATE, theirs)
    assert (received["bound_org"], received["crossings_ended"]) == ("org-y", 0)
    (line,) = [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.account_regrouped"]
    assert (line["left_out"], line["bound"]) == (None, {"bound_org": "org-y"})
    with store.connect() as db:
        assert tasks.relay_groups(db, tasks.get(db, world.user(MATE), theirs))[0] == ["personal"]
    assert world.get(OWNER, task_id)["bound_org"] is None
    taken_back(world, task_id, offer_id, theirs)
    assert world.get(OWNER, task_id)["bound_org"] == "org-y"
    assert owner_groups(world, task_id) == ["personal", "org:org-y"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"bound_org": "org-y"}]


def test_a_task_received_by_a_pressed_offer_confirmed_onto_another_organisations_seat_ends_the_owners_crossings(
        world, monkeypatch):
    """A received task bound by a confirmation, where the two bindings differ: the owner's personal task bound to Team
    organisation Z, accepted into the teammate's agent on their personal plan, its received task bound to none (a
    pressed offer gives it none of the owner's task's binding); the teammate's account then confirmed again as a seat
    of Team organisation W binds the received task to W, and as the way back ends the owner's task keeps Z and its
    crossings end, as custody's way back ends them, so that its relay carries neither organisation's work into the
    other: its relay set is the group it is on, the personal plans. Were the received task to stay unbound, the
    owner's task would come back bound to Z with its crossings going on, its relay free to carry the work done on W's
    seat onto Z's."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                         cross_groups=1, bound_org="org-z")
    assert world.get(MATE, theirs)["bound_org"] is None
    assert (world.get(OWNER, task_id)["bound_org"], world.get(OWNER, task_id)["crossings_ended"]) == ("org-z", 0)
    ended(theirs)
    confirmed_again(world, MATE, "cccccccccccc", "Claude Team", "Wye", "org-w")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-w", 0)
    taken_back(world, task_id, offer_id, theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-z", 1)
    assert owner_groups(world, task_id) == ["personal"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"crossings_ended": 1}]


def test_a_task_received_by_a_pressed_offer_confirmed_onto_an_enterprise_seat_ends_its_and_the_owners_crossings(
        world, monkeypatch):
    """A received task bound by a confirmation, onto an Enterprise seat: accepted into the teammate's agent on their
    seat of Team organisation Y, the received task is bound to Y, and the owner's personal task with it at the
    acceptance; the teammate's account then confirmed again as an Enterprise seat of W ends the received task's
    crossings, as a press onto an Enterprise seat would, and its relay keeps it in the group it is on, W's seats; as the
    way back ends the owner's task's crossings end too, its relay set the group it is on. Were the received task to keep
    its binding, its relay set would be Y's seats, none of which the relay reaches from the Enterprise seat, and the
    owner's task would come back bound to Y with its crossings going on, its relay free to carry the work done on W's
    seat onto Y's."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world, cross_groups=1)
    assert world.get(MATE, theirs)["bound_org"] == "org-y" and world.get(OWNER, task_id)["bound_org"] == "org-y"
    ended(theirs)
    confirmed_again(world, MATE, "cccccccccccc", "Claude Enterprise", "Wye", "org-w")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-y", 1)
    with store.connect() as db:
        assert tasks.relay_groups(db, tasks.get(db, world.user(MATE), theirs))[0] == ["org:org-w"]
    taken_back(world, task_id, offer_id, theirs)
    back = world.get(OWNER, task_id)
    assert (back["bound_org"], back["crossings_ended"]) == ("org-y", 1)
    assert owner_groups(world, task_id) == ["personal"]
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"crossings_ended": 1}]


def test_a_task_received_by_a_pressed_offer_confirmed_off_an_enterprise_seat_onto_a_personal_plan_ends_its_crossings(
        world):
    """A received task is bound as a custody task is, by every confirmation, at a confirmation onto the personal plans
    too. Accepted into the teammate's agent on an Enterprise seat of E, the
    received task is bound to E; the teammate's account then confirmed again as a personal plan ends its crossings, as
    a press off an Enterprise seat ends them, so its relay keeps it on the personal plans, the group it is on, and takes
    that account now. Left out, it would keep its binding, its relay set E's seats, onto none of which the relay moves a
    task from a personal plan (_enterprise_move), and its line would say the app moves it onto none of the teammate's
    other accounts."""
    task_id, offer_id, theirs = accepted(world, mate_plan="Claude Enterprise", mate_org="Eee", mate_org_id="org-e")
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-e", 0)
    ended(theirs)
    confirmed_again(world, MATE, "cccccccccccc", "Claude Max", None, None)
    assert (world.get(MATE, theirs)["bound_org"], world.get(MATE, theirs)["crossings_ended"]) == ("org-e", 1)
    (line,) = [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.account_regrouped"]
    assert (line["left_out"], line["bound"]) == (None, {"crossings_ended": 1})
    assert line["why"].endswith(" Its binding changed as a press from the old group onto the new one would. Its relay"
                                " takes that account now.")
    with store.connect() as db:
        assert tasks.relay_groups(db, tasks.get(db, world.user(MATE), theirs))[0] == ["personal"]


def test_a_task_received_by_a_pressed_offer_is_still_bound_by_a_confirmation_while_its_way_back_runs(world,
                                                                                                    monkeypatch):
    """A received task bound by a confirmation while the way back runs, as custody's test above has it for an unticked
    custody task: a version leaving out a received task once it is marked returned, since it moves no more then, would
    let the confirmation's binding miss the owner's task. Accepted into the teammate's agent on their personal plan, its
    received task bound to none; the owner's Take back; while the way back runs, the received task marked returned and
    the owner's task returning, the teammate's account is confirmed again as a seat of Team organisation Y: the received
    task is bound to Y, and as the way back ends so is the owner's task, task.bound naming that."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world, mate_plan="Claude Max", mate_org=None, mate_org_id=None,
                                         cross_groups=1)
    ended(theirs)
    seen = {}

    def meanwhile():
        seen["before"] = (world.get(MATE, theirs)["returned"], world.get(OWNER, task_id)["state"])
        confirmed_again(world, MATE, "cccccccccccc", "Claude Team", "Yarra", "org-y")
        seen["theirs"] = world.get(MATE, theirs)

    taken_back(world, task_id, offer_id, theirs, meanwhile=meanwhile)
    assert seen["before"] == (1, "returning")
    assert (seen["theirs"]["bound_org"], seen["theirs"]["crossings_ended"]) == ("org-y", 0)
    assert world.get(OWNER, task_id)["bound_org"] == "org-y"
    assert [p["bound"] for p in bound_lines(world, task_id)] == [{"bound_org": "org-y"}]


def test_a_custody_hand_off_ends_when_an_account_has_room_at_its_holds_end_and_the_relay_goes_on_at_once(world):
    """A failed custody hold goes on to the relay's step 1, not its step 3:
    an account of the relay set given room during the hold (a fresh allowed window): at the hold's end every condition
    of custody is checked again and the hand-off ends failed, nothing reaching the custodian; and the task waits for
    nothing, so the relay decides again from its first step at once (the tasks' sweep resumes a task whose wait has
    passed), rather than at the earliest reset of the accounts that are still refused."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found, hold = hand(world, task_id)
    world.window(OWNER, "bbbbbbbbbbbb", "allowed")
    world.running(OWNER, MATE)
    now = world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "failed" and world.ws.calls == [] and world.tasks.created == []
    task = world.get(OWNER, task_id)
    assert (task["state"], task["cause"], task["waits_until"]) == ("suspended", "window", now)


@pytest.mark.parametrize("how", ["silent", "not_started", "unreadable", "no_agent", "busy", "track_off", "relay_off",
                                 "broken"])
def test_a_custody_hand_off_that_fails_on_the_custodians_side_ends_at_once_saying_why(world, monkeypatch, how):
    """Once pushed, the custodian's side takes the hand-off at once, every condition checked again, since
    its fetch takes time. Their workspace not answering, one the provider will not start, a fetch its sign-in cannot
    make, their workspace refusing it now (404: no such agent there; 409: an agent of theirs on that repository
    paused, held or working), the track or the task's Relay switch turned off while it fetched, or a break in the
    taking itself, ends the hand-off failed at once, in the app's words true of which, nothing made and nothing told,
    and the relay decides again."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found, hold = hand(world, task_id)
    world.running(OWNER)
    if how == "not_started":
        world.provider.refusing.add(world.user(MATE))
    else:
        world.running(MATE)
    if how == "silent":
        world.ws.silent.add(world.user(MATE))
    elif how == "unreadable":
        world.ws.unreadable.add(world.user(MATE))
    elif how == "broken":
        def broken(*args, **kwargs):
            raise RuntimeError("the store broke")
        monkeypatch.setattr(tasks, "create", broken)

    def meanwhile(body):
        if body["purpose"] != "accept":
            return
        if how == "track_off":
            world.mp.setenv("OPEN_EDITION_ADE_TASK_TEAMMATE", "off")
        elif how == "relay_off":
            with store.connect() as db:
                db.execute("UPDATE ade_agent_tasks SET relay = 0 WHERE id = ?", (task_id,))
        elif how == "no_agent":
            raise workspace_client.WorkspaceCallError(404, "No such agent.")
        elif how == "busy":
            raise workspace_client.WorkspaceCallError(409, "A run of an agent on that repository is working.")
    world.ws.during = meanwhile
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    words = {"silent": task_offers.SILENT, "not_started": task_offers.NO_START, "unreadable": task_offers.UNREADABLE,
             "no_agent": task_offers.TAKE_FAILED, "busy": task_offers.TAKE_FAILED,
             "track_off": task_offers.OFF_SETTING, "relay_off": "The task's relay is off.",
             "broken": task_offers.TAKE_FAILED}[how]
    assert offer["state"] == "failed" and offer["why"] == words and world.tasks.created == []
    assert world.get(OWNER, task_id)["state"] == "suspended"
    assert world.notices(MATE) == ["A teammate asks you to be their custodian"]
    # a fetch that made the custodian's worktree, of which no task came, has it removed; one refused made none
    mate = world.user(MATE)
    ids = [p.split("/")[3] for u, p, b in world.ws.calls if u == mate and p.endswith("/fetch")]
    assert world.ws.fetched == {}
    assert world.ws.removed == ([(mate, ids[0])] if how in ("track_off", "relay_off", "broken") else [])


def test_a_custody_hand_off_is_checked_again_when_the_round_comes_to_push_it(world):
    """The round that pushes a custody hand-off may come later than its hold's end, so every condition is
    checked once more: the task's Relay switch turned off in between ends it failed with why, nothing compared or
    pushed."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found, hold = hand(world, task_id)
    world.running(OWNER, MATE)
    now = int(time.time()) + 200
    holds.settle(world.user(OWNER), now)                # the hold ends and its check passes; no round yet
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET relay = 0 WHERE id = ?", (task_id,))
    task_offers.drive(now)
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "failed" and offer["why"] == "The task's relay is off." and world.ws.calls == []


def test_a_custody_hand_off_pushed_but_not_yet_taken_is_checked_again_before_the_custodian_fetches(world, monkeypatch):
    """A custody hand-off pushed in one round and taken in a later one (the round broken between the two, say) is
    checked again before the custodian's workspace is asked to fetch it: the task's Relay switch turned off in between
    ends it failed with why, and the custodian's workspace is asked nothing."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found, hold = hand(world, task_id)
    world.running(OWNER, MATE)
    take = task_offers._take
    monkeypatch.setattr(task_offers, "_take", lambda offer_id, now: None)
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "offered"
    monkeypatch.setattr(task_offers, "_take", take)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET relay = 0 WHERE id = ?", (task_id,))
    task_offers.drive()
    offer = world.offer(offer["id"])
    assert offer["state"] == "failed" and offer["why"] == "The task's relay is off."
    assert world.ws.of("/fetch", world.user(MATE)) == []


def test_a_custody_hand_off_whose_account_changed_during_its_hold_lets_the_relay_decide_at_once(world):
    """An account of the relay set whose identity changed during the hold is out for another reason than
    a refused window: the hand-off ends at the hold's end, and the task waits for nothing (its wait now), its old
    refused window not counted, so the relay decides again from its first step."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found, hold = hand(world, task_id)
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET state = 'changed' WHERE user_id = ? AND account = 'bbbbbbbbbbbb'",
                   (world.user(OWNER),))
    world.running(OWNER, MATE)
    now = world.settle()
    assert world.row("ade_agent_task_offers", "hold_id", hold["id"])["state"] == "failed"
    task = world.get(OWNER, task_id)
    assert (task["state"], task["cause"], task["waits_until"]) == ("suspended", "window", now)


def test_an_offer_asked_for_gives_nothing_back_when_custody_began_at_that_attempts_end(world):
    """The relay goes ahead of an offer asked for, at an attempt's end: where that end is a refusal, the
    relay's step 2 can begin a custody hand-off; the offer asked for then does not begin, and gives nothing back, since
    it never made the task offering: the task stays offering under the custody hand-off's hold."""
    task_id, mate_agent, custody_id = setup_custody(world, state="running", lease_run="bbbbbbbbbbbb")
    world.user(OTHER)
    asked = world.offer_to(task_id, address=OTHER)["offer"]["id"]
    assert world.offer(asked)["hold_id"] is None
    owner, now = world.user(OWNER), int(time.time())
    with store.connect() as db:                         # the attempt's end, as ade/tasks.py copies it: the relay first
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE ade_agent_tasks SET lease_run = NULL WHERE id = ?", (task_id,))
        found = task_offers.custody_candidate(db, owner, task_id, now)
        hold = task_offers.begin_custody(db, owner, task_id, found, now)
        assert task_offers.after_attempt_end(db, owner, task_id, "bbbbbbbbbbbb", now) is False
    assert world.offer(asked)["state"] == "failed" and world.offer(asked)["why"] == task_offers.NOT_BEGUN
    assert world.get(OWNER, task_id)["state"] == "offering"
    assert world.row("ade_agent_task_offers", "hold_id", hold["id"])["state"] == "held"


def test_the_round_tells_no_custody_hold_its_relay_tells(world):
    """A custody hand-off's hold is told by the relay once its transaction has committed (tasks.deliver); the round
    tells only the hold of an offer begun at an attempt's end, which nobody else tells. So a custody hold is not told
    twice, however late its relay's transaction commits."""
    task_id, mate_agent, custody_id = setup_custody(world)
    now = int(time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        found = task_offers.custody_candidate(db, world.user(OWNER), task_id, now)
        task_offers.begin_custody(db, world.user(OWNER), task_id, found, now)     # not told yet by its caller
    told = len(world.told)
    task_offers.drive(now + task_offers.TOLD_GRACE + 60)
    assert len(world.told) == told


def setup_custody_again(w):
    """A second custody, after the first ended: a new task, request and acceptance."""
    task_id = w.task(OWNER, w.agent(OWNER))
    agent_id = w.agent(MATE, account="cccccccccccc", name="Again")
    custody_id = ask_custody(w)
    accept_custody(w, custody_id, [agent_id])
    confirm_for_custody(w, task_id)
    return task_id, agent_id, custody_id


def test_a_custody_hand_off_that_changes_a_file_a_next_run_would_load_is_never_taken(world):
    """The recorded commit adds a hook in .claude/settings.json: nobody looks at a custody hand-off
    before it runs, so it ends, nothing pushed, and the relay decides again."""
    task_id, mate_agent, custody_id = setup_custody(world)
    world.ws.loaded[C1] = [".claude/settings.json"]
    found, hold = hand(world, task_id)
    world.running(OWNER, MATE)
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "failed" and offer["why"] == task_offers.LOADED_CHANGED
    assert world.ws.of("/push") == [] and world.tasks.created == [] and offer["teammate_user"] is None
    task = world.get(OWNER, task_id)        # every account still refused: the relay decides again at the first reset
    assert (task["state"], task["cause"], task["waits_until"]) == ("suspended", "window", earliest_reset(world))


@pytest.mark.parametrize("listed", [[".claude/settings.json"], ["CLAUDE.md"], []],
                         ids=["settings.json", "CLAUDE.md", "none"])
def test_a_custody_hand_off_whose_custodians_acceptance_lists_a_loaded_file_ends_before_any_task_is_made(world,
                                                                                                       listed):
    """Custody's check of the
    files a next run would load stays on the default branch, in the owner's view before the push, and the custodian's
    acceptance list, read against the receiving agent's own start, also ends the hand-off when it is not empty. The
    owner's view lists nothing (against the default branch), so the commit is pushed; the custodian's fetch, an
    acceptance, lists `.claude/settings.json` (against the receiving agent's start, which differs): the hand-off ends
    failed in custody's words, no custody task is made, the worktree the fetch made is removed, and the relay decides
    again at the first reset. Unread for custody, that list would let the task be taken. With the
    list empty (the control), the custody task is made. A list naming a file outside `.claude/`, CLAUDE.md, ends it too:
    the list ends it whatever it names."""
    task_id, mate_agent, custody_id = setup_custody(world)
    if listed:
        world.ws.loaded_accept[C1] = list(listed)
    found, hold = hand(world, task_id)
    world.running(OWNER, MATE)
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert [b["commit"] for u, b in world.ws.of("/push", world.user(OWNER))] == [C1]
    mate = world.user(MATE)
    fetched = [p.split("/")[3] for u, p, b in world.ws.calls if u == mate and p.endswith("/fetch")]
    assert len(fetched) == 1
    if not listed:
        assert offer["state"] == "accepted" and len(world.tasks.created) == 1 and world.ws.removed == []
        return
    assert offer["state"] == "failed" and offer["why"] == task_offers.LOADED_CHANGED
    assert world.tasks.created == [] and world.ws.fetched == {} and world.ws.removed == [(mate, fetched[0])]
    task = world.get(OWNER, task_id)        # every account still refused: the relay decides again at the first reset
    assert (task["state"], task["cause"], task["waits_until"]) == ("suspended", "window", earliest_reset(world))


def test_custodys_crossing_tick_lets_a_seat_take_a_personal_task_whatever_the_tasks_switch(world, monkeypatch):
    """Custody's crossing does not follow the task's switch: a receiving agent on a Team seat for a
    personal task takes it only with custody's own crossing ticked, after its confirmation and hold, whatever the
    task's "Cross organisations" switch and the deployment's; the custody task is then made with custody_cross 1."""
    task_id, mate_agent, custody_id = setup_custody(world, mate_plan="Claude Team", mate_org="Yarra",
                                                    mate_org_id="org-y")
    assert candidate(world, task_id) is None
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET cross_groups = 1 WHERE id = ?", (task_id,))
    assert candidate(world, task_id) is None
    version = world.get(OWNER, task_id)["state_version"]
    r = world.client.post(f"{T}/tasks/{task_id}/custody-cross", headers=world.h(OWNER), json={"state_version": version})
    assert r.status_code == 403
    r = world.client.post(f"{T}/tasks/{task_id}/custody-cross", headers=world.h(OWNER),
                          json={"state_version": version,
                                "confirmation": world.confirmation(OWNER, f"task-custody-cross:{task_id}")})
    assert r.status_code == 200 and r.json()["hold"]["kind"] == "task.crossing"
    assert world.notices(OWNER)[-1] == "A task's custody crossing waits" and candidate(world, task_id) is None
    world.settle()
    assert world.get(OWNER, task_id)["custody_cross"] == 1
    confirm_for_custody(world, task_id)
    assert candidate(world, task_id)["grp"] == "org:org-y"
    handed(world, task_id)
    assert world.tasks.created[-1]["fields"]["custody_cross"] == 1


def runs_on(task_id):
    """The accounts the task's attempts were queued on, in order (tasks._queue)."""
    with store.connect() as db:
        return [json.loads(r["backend"])["id"] for r in db.execute(
            "SELECT backend FROM ade_agent_runs WHERE task_id = ? ORDER BY fence", (task_id,)).fetchall()]


def refused_there(w, email, task_id, resets):
    """The task's attempt refused at its first answer, as ade/tasks.py copies the run's record from the workspace's view
    (agents._apply, the app's own poll): its account's five-hour window rejected until `resets`, the run blocked
    (rate_limit). The tasks' relay then decides where the task goes."""
    with store.connect() as db:
        run = db.execute("SELECT r.* FROM ade_agent_tasks t JOIN ade_agent_runs r ON r.run_id = t.lease_run WHERE "
                         "t.id = ?", (task_id,)).fetchone()
    now = int(time.time())
    view = {"run_id": run["run_id"], "agent_id": run["agent_id"], "state": "blocked", "cause": "rate_limit",
            "reason": None, "started_at": now, "ended_at": now,
            "rate_limits": {"five_hour": {"status": "rejected", "resets_at": resets, "at": now}},
            "task": {"id": task_id, "fence": run["fence"], "step": run["task_step"], "attempt": run["task_attempt"],
                     "backend": json.loads(run["backend"])}}
    agents._apply(w.user(email), [view], record_seq=False)


def test_a_task_whose_crossings_a_press_ended_goes_by_custody_only_to_the_group_it_is_on(world):
    """A task started on S1 (a seat of X, so bound to X), custody's crossing
    ticked, then pressed onto S2 (a seat of Y), which ends its crossings; the custodian's receiving agents one on their
    personal plan (first) and one on a seat of Y: with every S2 seat refused and its reset more than 30 minutes away,
    custody hands it only to the agent on Y's seat, the custody task made with custody_cross 0 so the custodian's
    relay keeps it in Y's group; the tick is refused (412) after the press."""
    now = int(time.time())
    world.account(OWNER, "aaaaaaaaaaaa", "Claude Team", "Xanadu", "org-x")
    world.account(OWNER, "bbbbbbbbbbbb", "Claude Team", "Yarra", "org-y")
    world.window(OWNER, "bbbbbbbbbbbb", "rejected", resets_at=now + HOUR)
    task_id = world.task(OWNER, world.agent(OWNER), account="bbbbbbbbbbbb", home="org:org-x", bound_org="org-x",
                         custody_cross=1, crossings_ended=1)
    world.account(MATE, "cccccccccccc", "Claude Max")
    world.account(MATE, "dddddddddddd", "Claude Team", "Yarra", "org-y")
    personal = world.agent(MATE, account="cccccccccccc", name="Personal")
    seat = world.agent(MATE, account="dddddddddddd", name="Seat")
    accept_custody(world, ask_custody(world), [personal, seat])
    confirm_for_custody(world, task_id)
    assert candidate(world, task_id) == {"custody_id": world.row("ade_task_custody", "owner_user", world.user(OWNER))[
        "id"], "agent_id": seat, "grp": "org:org-y"}
    version = world.get(OWNER, task_id)["state_version"]
    r = world.client.post(f"{T}/tasks/{task_id}/custody-cross", headers=world.h(OWNER),
                          json={"state_version": version,
                                "confirmation": world.confirmation(OWNER, f"task-custody-cross:{task_id}")})
    assert r.status_code == 412 and r.json()["detail"] == task_offers.CROSSINGS_ENDED
    handed(world, task_id)
    made = world.tasks.created[-1]
    assert made["agent_id"] == seat and made["fields"]["custody_cross"] == 0
    assert made["continued_from"]["task"] == task_id
    # the owner's side goes with a custody hand-off as with an offer
    assert made["continued_from"]["handed"] == {"from": OWNER, "done_steps": [{"n": 1, "evidence": "said_done"}],
                                                "outward_effects": []}
    assert world.get(MATE, made["task_id"])["step"] == 2
    assert world.record(task_id)[0]["accounts"][0]["organization"] == "Yarra"
    # it stays in the group it is on, Y's, though its home is X's: the hand-off crossed none
    assert [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"][-1]["payload"]["crossed"] is False
    # and the custodian's relay keeps it there, its tick notwithstanding: with the deployment's crossing setting on,
    # which a received task's
    # relay never follows, Y's seat refusing it while the custodian's personal plan has room, the task waits for the
    # seat's reset and is not moved onto the personal plan
    theirs = made["task_id"]
    world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    world.window(MATE, "cccccccccccc", "allowed", resets_at=now + 5 * HOUR)
    assert runs_on(theirs) == ["dddddddddddd"]
    refused_there(world, MATE, theirs, resets=now + 2 * HOUR)
    waiting = world.get(MATE, theirs)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", now + 2 * HOUR)
    assert runs_on(theirs) == ["dddddddddddd"] and [e for e in world.journal(theirs) if e["kind"] == "task.crossed"] \
        == []
    with store.connect() as db:
        left_out = {e["account"]: e["left_out"] for e in tasks.relay_set(db, world.user(MATE), theirs, now)}
    assert left_out == {"cccccccccccc": "a task received from a teammate stays in its group", "dddddddddddd": None}


def test_a_custody_task_ticked_to_cross_crosses_by_the_custodians_relay_until_a_press_ends_its_crossings(world):
    """The receiving side of custody's crossing tick: the
    owner's personal task, custody's crossing ticked, handed by custody to the custodian's receiving agent on a seat
    of Y, is made with custody_cross 1, and the custodian's relay lets it cross whatever either
    switch, here with both off: its own, as every received task's is, and the deployment's crossing setting. The seat
    refusing it while the custodian's personal plan has room, it moves onto the personal plan, with task.crossed. Then
    the custodian's press onto a seat of a second organisation, Z, while it is bound to Y, ends its crossings: Z's seat
    refusing it while the personal plan has room again, the custodian's relay keeps it in Z's group, custody_cross 1
    notwithstanding, and it waits for the seat's reset."""
    now = int(time.time())
    world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off")
    task_id, mate_agent, custody_id = setup_custody(world, mate_plan="Claude Team", mate_org="Yarra",
                                                    mate_org_id="org-y")
    r = world.client.post(f"{T}/tasks/{task_id}/custody-cross", headers=world.h(OWNER),
                          json={"state_version": world.get(OWNER, task_id)["state_version"],
                                "confirmation": world.confirmation(OWNER, f"task-custody-cross:{task_id}")})
    assert r.status_code == 200
    world.settle()
    confirm_for_custody(world, task_id)
    handed(world, task_id)
    theirs = world.tasks.created[-1]["task_id"]
    received = world.get(MATE, theirs)
    assert (received["custody_cross"], received["cross_groups"], received["home_grp"]) == (1, 0, "org:org-y")
    world.account(MATE, "eeeeeeeeeeee", "Claude Max", position=1)
    world.window(MATE, "eeeeeeeeeeee", "allowed", resets_at=now + 5 * HOUR)
    assert runs_on(theirs) == ["cccccccccccc"]
    refused_there(world, MATE, theirs, resets=now + HOUR)
    assert runs_on(theirs) == ["cccccccccccc", "eeeeeeeeeeee"]
    crossed = [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.crossed"]
    assert [(c["from_grp"], c["to_grp"]) for c in crossed] == [("org:org-y", "personal")]
    # the personal plan refuses it too: it waits for Y's seat's reset, the earliest
    refused_there(world, MATE, theirs, resets=now + 3 * HOUR)
    assert world.get(MATE, theirs)["waits_until"] == now + HOUR
    world.account(MATE, "ffffffffffff", "Claude Team", "Zed", "org-z", position=2)
    r = world.client.post(f"/api/ade/agents/tasks/{theirs}/continue-on", headers=world.h(MATE),
                          json={"state_version": world.get(MATE, theirs)["state_version"],
                                "backend": {"kind": "account", "id": "ffffffffffff"}})
    assert r.status_code == 200, r.text
    pressed = world.get(MATE, theirs)
    assert (pressed["crossings_ended"], pressed["bound_org"], pressed["custody_cross"]) == (1, "org-y", 1)
    assert runs_on(theirs) == ["cccccccccccc", "eeeeeeeeeeee", "ffffffffffff"]
    world.window(MATE, "eeeeeeeeeeee", "allowed", resets_at=now + 5 * HOUR)
    refused_there(world, MATE, theirs, resets=now + 2 * HOUR)
    waiting = world.get(MATE, theirs)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", now + 2 * HOUR)
    assert runs_on(theirs) == ["cccccccccccc", "eeeeeeeeeeee", "ffffffffffff"]


@pytest.mark.parametrize("setting", [None, "on"])
def test_a_task_received_by_an_offer_starts_with_its_relay_switch_as_the_deployment_s_setting(world, setting):
    """A task received by an offer's acceptance is made as any task is, its Relay switch starting as the
    deployment's OPEN_EDITION_ADE_TASK_RELAY, which is off unless set (the fixture sets it on; unset here, its
    default): unlike its crossing switch, which is off whatever the setting, it follows the setting."""
    if setting is None:
        world.mp.delenv("OPEN_EDITION_ADE_TASK_RELAY")
    task_id, offer_id, theirs = accepted(world)
    assert world.get(MATE, theirs)["relay"] == (0 if setting is None else 1)


def test_a_task_received_by_an_offer_does_not_cross_and_its_switch_cannot_be_turned_on(world):
    """With the deployment's crossing setting on, a task
    received by an offer's acceptance is made with its "Cross organisations" switch off, and turning it on is refused
    (412), its confirmation given; and its relay stays in the group it was accepted in, shown on one refusal: Y's seat
    refusing it while the teammate's personal plan has room, it waits for the seat's reset."""
    now = int(time.time())
    world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, offer_id, theirs = accepted(world)
    received = world.get(MATE, theirs)
    assert (received["cross_groups"], received["custody_cross"], received["home_grp"]) == (0, 0, "org:org-y")
    r = world.client.post(f"/api/ade/agents/tasks/{theirs}/switches", headers=world.h(MATE),
                          json={"cross_groups": True, "confirmation": world.confirmation(MATE, f"task-cross:{theirs}")})
    assert r.status_code == 412 and r.json()["detail"] == "A task received from a teammate stays in its group."
    assert world.get(MATE, theirs)["cross_groups"] == 0
    world.account(MATE, "eeeeeeeeeeee", "Claude Max", position=1)
    world.window(MATE, "eeeeeeeeeeee", "allowed", resets_at=now + 5 * HOUR)
    r = world.client.post(f"/api/ade/agents/tasks/{theirs}/start", headers=world.h(MATE),
                          json={"state_version": world.get(MATE, theirs)["state_version"]})
    assert r.status_code == 200, r.text
    assert runs_on(theirs) == ["cccccccccccc"]
    refused_there(world, MATE, theirs, resets=now + 2 * HOUR)
    waiting = world.get(MATE, theirs)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", now + 2 * HOUR)
    assert runs_on(theirs) == ["cccccccccccc"] and [e for e in world.journal(theirs) if e["kind"] == "task.crossed"] \
        == []


@pytest.mark.parametrize("switch", ["off", "on", "setting off"])
def test_an_owners_task_with_custodys_crossing_ticked_crosses_by_its_own_relay_only_as_its_switches_say(world, switch):
    """custody_cross: on the owner's task the column is custody's own crossing tick, which lets custody hand the task
    between the personal plans and one Team organisation's seats (task_offers._custody_groups) and the custodian's relay
    cross with it; the owner's own relay crosses only while the task's switch and the deployment's setting are both on
    (tasks._crossing_on), tick or no tick. The owner's personal task, the tick ticked, refused on its plan while the
    owner's seat of another organisation has room: with its own switch off and the setting on, it does not cross there
    and waits for its plan's reset, the seat left out of its relay set (this test fails if _crossing_on reads
    custody_cross on any task); with its switch on and the setting on, it crosses onto the seat (this test fails if
    _crossing_on refuses every ticked task of the owner's); with its switch on and the setting turned off after the task
    was made, it waits as with its switch off (this test fails if the tick stands in for the setting)."""
    now = int(time.time())
    world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    world.account(OWNER, "aaaaaaaaaaaa", "Claude Max", position=0)
    world.account(OWNER, "dddddddddddd", "Claude Team", "Xanadu", "org-x", position=1)
    world.window(OWNER, "dddddddddddd", "allowed", resets_at=now + 5 * HOUR)
    task_id = world.task(OWNER, world.agent(OWNER), state="ready", step=2, custody_cross=1,     # step 1 done already
                         cross_groups=0 if switch == "off" else 1)
    mine = world.get(OWNER, task_id)
    assert (mine["custody_cross"], mine["cross_groups"], mine["continued_from"]) == (1, int(switch != "off"), None)
    if switch == "setting off":
        world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off")
    r = world.client.post(f"/api/ade/agents/tasks/{task_id}/start", headers=world.h(OWNER),
                          json={"state_version": world.get(OWNER, task_id)["state_version"]})
    assert r.status_code == 200, r.text
    assert runs_on(task_id) == ["aaaaaaaaaaaa"]
    refused_there(world, OWNER, task_id, resets=now + 2 * HOUR)
    if switch == "on":
        crossed = [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.crossed"]
        assert runs_on(task_id) == ["aaaaaaaaaaaa", "dddddddddddd"]
        assert [(c["from_grp"], c["to_grp"]) for c in crossed] == [("personal", "org:org-x")]
        return
    waiting = world.get(OWNER, task_id)
    assert (waiting["state"], waiting["cause"], waiting["waits_until"]) == ("suspended", "window", now + 2 * HOUR)
    assert runs_on(task_id) == ["aaaaaaaaaaaa"] and [e for e in world.journal(task_id) if e["kind"] == "task.crossed"] \
        == []
    with store.connect() as db:
        left_out = {e["account"]: e["left_out"] for e in tasks.relay_set(db, world.user(OWNER), task_id, now)}
    assert left_out == {"aaaaaaaaaaaa": None, "dddddddddddd": "crossing organisations is off for this task"}


@pytest.mark.parametrize("via", ["offer", "custody"])
def test_after_the_journals_prune_the_owners_record_says_a_teammate_with_the_plan_and_the_seats_organisation(world,
                                                                                                             via):
    """The hand-off entry an acceptance writes in the
    owner's account record, an offer's or custody's, holds the teammate by their user id with the plan, and the
    organisation only for a seat (the offer is accepted onto a seat of Y, custody's task onto the teammate's personal
    plan); once the task is back, ended and deleted, the tasks' journal prune, KEEP_DAYS after its deletion and not a
    second before, reduces the teammate to "a teammate" (their snapshot's teammate null), the plan and any organisation
    kept and the title dropped. The record is kept for good here, as a deployment may keep it: under the default it goes
    with the prune."""
    world.mp.setenv("OPEN_EDITION_ADE_ACCOUNT_RECORD_DAYS", "forever")
    if via == "offer":
        task_id, offer_id, theirs = accepted(world)
        worked(world, theirs)
        r = world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
        assert r.status_code == 200, r.text
        task_offers.drive()
        ended(theirs)
        snapshot = {"teammate": world.user(MATE), "plan": "Claude Team", "organization": "Yarra", "org_id": "org-y"}
    else:
        task_id, mate_agent, custody_id = setup_custody(world)
        theirs = handed(world, task_id)["their_task"]
        ended(theirs, state="stopped")                  # stopped on their side, it comes back waiting for the owner
        world.window(OWNER, "aaaaaaaaaaaa", "allowed")
        world.ws.commits.add(C1)
        snapshot = {"teammate": world.user(MATE), "plan": "Claude Max", "organization": None, "org_id": None}
    task_offers.drive()
    assert world.get(OWNER, task_id)["state"] not in tasks.WITH_TEAMMATE
    title = world.get(OWNER, task_id)["title"]
    r = world.client.post(f"/api/ade/agents/tasks/{task_id}/end", headers=world.h(OWNER),
                          json={"state_version": world.get(OWNER, task_id)["state_version"], "outcome": "stopped"})
    assert r.status_code == 200, r.text
    r = world.client.request("DELETE", f"/api/ade/agents/tasks/{task_id}", headers=world.h(OWNER),
                             json={"state_version": world.get(OWNER, task_id)["state_version"]})
    assert r.status_code == 200, r.text
    deleted = next(e for e in world.record(task_id) if e["kind"] == "deleted")["at"]
    for at, kept in ((deleted + tasks.KEEP_DAYS * tasks.DAY - 1, True), (deleted + tasks.KEEP_DAYS * tasks.DAY, False)):
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            tasks.forget(db, at)
        (handoff,) = [e for e in world.record(task_id) if e["kind"] == "handoff"]
        assert handoff["via"] == via and handoff["accounts"] == [snapshot if kept else dict(snapshot, teammate=None)]
        assert title and handoff["title"] == (title if kept else None)


def test_custodys_crossing_on_a_task_whose_home_is_an_enterprise_seat_is_taken_and_crosses_nothing(world):
    """Custody never takes a task into or out of an Enterprise organisation's seats. Its crossing's tick
    is taken there (it is refused only where a press ended the task's crossings) and changes
    nothing: every Enterprise seat of the owner's refused, the custodian's agent on their personal plan is not
    chosen."""
    now = int(time.time())
    world.account(OWNER, "aaaaaaaaaaaa", "Claude Enterprise", "Bigco", "org-b")
    world.window(OWNER, "aaaaaaaaaaaa", "rejected", resets_at=now + HOUR)
    task_id = world.task(OWNER, world.agent(OWNER), home="org:org-b")
    world.account(MATE, "cccccccccccc", "Claude Max")
    accept_custody(world, ask_custody(world), [world.agent(MATE, account="cccccccccccc", name="Personal")])
    r = world.client.post(f"{T}/tasks/{task_id}/custody-cross", headers=world.h(OWNER),
                          json={"state_version": world.get(OWNER, task_id)["state_version"],
                                "confirmation": world.confirmation(OWNER, f"task-custody-cross:{task_id}")})
    assert r.status_code == 200 and r.json()["hold"]["kind"] == "task.crossing"
    world.settle()
    assert world.get(OWNER, task_id)["custody_cross"] == 1
    confirm_for_custody(world, task_id)
    assert candidate(world, task_id) is None


def test_custody_requests_declined_left_unanswered_or_ended_before_an_answer_name_nobody_after(world):
    """A custody request to U declined, one to V left unanswered for 7 days, and one to U that the owner ended
    before U answered: none names U nor V, nor the address the owner typed, and the custody entries name only the
    request and a role."""
    for email in (OTHER, THIRD):
        world.user(email)
    declined = ask_custody(world, address=OTHER)
    r = world.client.post(f"{T}/custody/{declined}/decline", headers=world.h(OTHER), json={})
    assert r.status_code == 200 and r.json()["custody"]["state"] == "declined"
    row = world.custody(declined)
    assert row["custodian_user"] is None and row["address"] is None and row["ended_by"] == "custodian"
    unanswered = ask_custody(world, address=THIRD)
    world.forget(int(time.time()) + 7 * 86400 + 60)
    row = world.custody(unanswered)
    assert row["state"] == "expired" and row["custodian_user"] is None and row["address"] is None
    assert row["ended_by"] == "app"
    withdrawn = ask_custody(world, address=OTHER)
    r = world.client.post(f"{T}/custody/{withdrawn}/end", headers=world.h(OWNER), json={})
    assert r.status_code == 200 and r.json()["custody"]["state"] == "ended"
    row = world.custody(withdrawn)
    assert row["custodian_user"] is None and row["address"] is None and row["ended_by"] == "owner"
    entries = world.journal()
    assert [e["kind"] for e in entries] == ["custody.asked", "custody.ended"] * 3
    assert not _named(json.dumps(entries), OTHER, THIRD, world.user(OTHER), world.user(THIRD))
    assert world.client.get(f"{T}/custody", headers=world.h(OTHER)).json()["as_custodian"] == []


def test_a_custody_request_to_an_address_that_is_no_ones_answers_the_same(world):
    """An address that is no one's gets the same immediate answer, for custody too:
    the request and its confirmation answer as one to a teammate's address does, naming the address as typed; nobody is
    told; unanswered, it expires."""
    world.user(MATE)
    answers = {}
    for owner, address in ((OWNER, MATE), (OTHER, "nobody@example.com")):
        asked = world.client.post(f"{T}/custody", headers=world.h(owner), json={"address": address}).json()["custody"]
        confirmed = world.client.post(f"{T}/custody/{asked['id']}/confirm", headers=world.h(owner), json={
            "confirmation": world.confirmation(owner, f"task-custody:{asked['id']}")}).json()["custody"]
        assert asked["custodian_email"] == confirmed["custodian_email"] == address
        answers[owner] = [{k: v for k, v in a.items() if k not in ("id", "owner_email", "custodian_email",
                                                                      "created_at", "updated_at", "confirmed_at")}
                          for a in (asked, confirmed)]
    assert answers[OWNER] == answers[OTHER]
    assert [u for u, _ in world.told] == [world.user(MATE)]
    world.forget(int(time.time()) + 7 * 86400 + 60)
    assert world.row("ade_task_custody", "owner_user", world.user(OTHER))["state"] == "expired"


def test_an_ended_custody_names_its_custodian_no_more_90_days_after_it_ends(world):
    """An accepted custody that ended keeps its custodian until 90 days after it ended, then names them no
    more; its custody entries never named them."""
    task_id, mate_agent, custody_id = setup_custody(world)
    r = world.client.post(f"{T}/custody/{custody_id}/end", headers=world.h(OWNER), json={})
    ended = world.custody(custody_id)["ended_at"]
    world.forget(ended + 89 * 86400)
    assert world.custody(custody_id)["custodian_user"] == world.user(MATE)
    world.forget(ended + 90 * 86400 + 1)
    row = world.custody(custody_id)
    assert row["custodian_user"] is None and row["address"] is None and row["agents"] == "[]"
    assert row["reduced_at"] == ended + 90 * 86400 + 1 and row["state"] == "ended"
    assert not _named(json.dumps(world.journal()), MATE, world.user(MATE), mate_agent)


def test_a_custody_request_through_the_api_carries_point_13_worded_for_custody(world):
    """The request as the custodian reads it carries point 13's text worded for custody, the owner filled,
    with point 1's clauses (every custody acceptance opens them); accepted through its route, without the page, it
    goes through."""
    world.user(MATE)
    custody_id = ask_custody(world)
    request = world.client.get(f"{T}/custody", headers=world.h(MATE)).json()["as_custodian"][0]
    cases = [p["case"] for p in request["terms"]["design"]]
    words = " ".join(p["text"] for p in request["terms"]["design"])
    assert "offer" not in cases and "adopted_check" not in cases and "other_group" in cases
    assert cases.count("custody") == 2 and f"{OWNER}'s record of that task" in words and OPERATOR in words
    assert request["owner_email"] == OWNER
    world.account(MATE, "cccccccccccc", "Claude Max")
    answer = accept_custody(world, custody_id, [world.agent(MATE, account="cccccccccccc")])
    assert answer["state"] == "accepted" and answer["terms"] == request["terms"]


@pytest.mark.parametrize("change", ["relay_switch", "teammate_setting", "branch_moved", "custody_ended"])
def test_a_change_during_a_custody_hold_ends_the_hand_off_at_the_holds_end(world, monkeypatch, change):
    """During a custody hand-off's hold, the task's Relay switch turned off, the teammate setting turned off,
    custody ended, or the task's branch moved by another run: the hand-off ends at the hold's end (or at once, for
    custody's end), nothing reaches the custodian, and the relay decides again."""
    task_id, mate_agent, custody_id = setup_custody(world)
    found, hold = hand(world, task_id)
    if change == "relay_switch":
        with store.connect() as db:
            db.execute("UPDATE ade_agent_tasks SET relay = 0 WHERE id = ?", (task_id,))
    elif change == "teammate_setting":
        monkeypatch.setenv("OPEN_EDITION_ADE_TASK_TEAMMATE", "no")
    elif change == "branch_moved":
        world.ws.heads[task_id] = C3
    else:
        world.client.post(f"{T}/custody/{custody_id}/end", headers=world.h(OWNER), json={})
    world.running(OWNER, MATE)
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "failed" and offer["teammate_user"] is None and world.tasks.created == []
    assert world.ws.remote == {} and world.notices(MATE) == ["A teammate asks you to be their custodian"]
    task = world.get(OWNER, task_id)
    if change == "branch_moved":
        assert (task["state"], task["cause"]) == ("needs_owner", "handoff")
    else:                                   # every account still refused: the relay decides again at the first reset
        assert (task["state"], task["cause"], task["waits_until"]) == ("suspended", "window", earliest_reset(world))


def test_drafts_requests_never_confirmed_and_the_offers_of_deleted_tasks_are_forgotten(world):
    """The scheduler's round (forget): a draft, and a custody request, not confirmed within 300 seconds are deleted
    (a draft past them cannot be confirmed either); the offers of a task deleted 90 days ago are deleted with it."""
    task_id, _ = setup_pressed(world)
    draft = world.offer_to(task_id, confirm=False)["id"]
    r = world.client.post(f"{T}/custody", headers=world.h(OWNER), json={"address": MATE})
    request = r.json()["custody"]["id"]
    now = int(time.time())
    with store.connect() as db:                         # both made at `now`, whatever second the clock turned at
        db.execute("UPDATE ade_agent_task_offers SET created_at = ? WHERE id = ?", (now, draft))
        db.execute("UPDATE ade_task_custody SET created_at = ? WHERE id = ?", (now, request))
    world.forget(now + 299)
    assert world.offer(draft) is not None and world.custody(request) is not None
    with store.connect() as db:
        db.execute("UPDATE ade_agent_task_offers SET created_at = created_at - 301 WHERE id = ?", (draft,))
    r = world.client.post(f"{T}/offers/{draft}/confirm", headers=world.h(OWNER),
                          json={"confirmation": world.confirmation(OWNER, f"task-offer:{draft}")})
    assert r.status_code == 409 and r.json()["detail"] == task_offers.DRAFT_GONE
    world.forget(now + 301)
    assert world.offer(draft) is None and world.custody(request) is None
    second = world.task(OWNER, world.agent(OWNER))
    made = offered(world, second)
    world.client.post(f"{T}/offers/{made['offer']['id']}/withdraw", headers=world.h(OWNER), json={})
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET deleted_at = ? WHERE id = ?", (now - 90 * 86400 + 60, second))
    world.forget(now)
    assert world.offer(made["offer"]["id"]) is not None
    world.forget(now + 61)
    assert world.offer(made["offer"]["id"]) is None


# ---- every way back -------------------------------------------------------------------------------------------------

def accepted(w, **kw):
    """An offer of the owner's task accepted by the teammate: the task, the offer and the teammate's task."""
    task_id, mate_agent = setup_pressed(w, **kw)
    made = offered(w, task_id)
    r = w.client.post(f"{T}/offers/{made['offer']['id']}/accept", headers=w.h(MATE),
                      json={"agent_id": mate_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    return task_id, made["offer"]["id"], r.json()["task_id"]


def worked(w, theirs, running=True):
    """The teammate's side worked on the task: step 2 done there, said done (step 1 the owner's, done before the
    hand-off); a checkpoint at C3, whose commits complete step 2, which their workspace pushes to the owner's task's
    branch once no attempt works (_push_theirs); an attempt still working when `running`."""
    with store.connect() as db:
        db.execute("UPDATE ade_agent_task_steps SET state = 'done', evidence = 'said_done' WHERE task_id = ? AND n = 2",
                   (theirs,))
        if running:
            db.execute("UPDATE ade_agent_tasks SET state = 'running', lease_run = 'ffffffffffff' WHERE id = ?",
                       (theirs,))
    w.checkpoint(MATE, theirs, C3, n=1)
    w.ws.steps[C3] = [2]


def ended(theirs, state="suspended"):
    """The teammate's attempt's end, as ade/tasks.py copies it."""
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET state = ?, lease_run = NULL WHERE id = ?", (state, theirs))


def test_take_back_stops_the_teammates_attempt_then_brings_their_last_pushed_work_back(world):
    """Take back, with no version, makes the owner's task returning and marks the teammate's
    returned; their live attempt is stopped as Stop stops it and its end waited for; then the owner's workspace
    fetches the last commit they pushed, from the handed one, and runs the owner's checks of the steps done there (a
    step with no check of the owner's comes back said done); the effects and step states are copied
    (tasks.take_return); the offer ends returned, and the owner is told."""
    task_id, offer_id, theirs = accepted(world)
    owner, mate = world.user(OWNER), world.user(MATE)
    worked(world, theirs)
    r = world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    assert r.status_code == 200 and r.json()["task"]["state"] == "returning"
    assert world.get(MATE, theirs)["returned"] == 1 and world.journal(task_id)[-1]["kind"] == "task.taken_back"
    task_offers.drive()
    assert world.stops == [(mate, "ffffffffffff")] and world.ws.of("/fetch", owner)[1:] == []
    assert world.ws.of("/push", mate) == []             # nothing pushed while their attempt works
    ended(theirs)
    task_offers.drive()
    assert world.get(MATE, theirs)["state"] == "needs_owner" and world.get(MATE, theirs)["cause"] == "returned"
    # their work pushed first, by its hash, with their own sign-in, to the owner's task's branch
    (pushed,) = world.ws.of("/push", mate)
    assert pushed[1] == {"address": ADDRESS, "commit": C3, "since": C1, "branch_head": C3,
                         "call_id": f"handover:{offer_id}:{world.get(MATE, theirs)['fence']}",
                         "ref": f"refs/heads/pp/task-{task_id}", "lifted": [],
                         "findings_key": task_offers.findings_key(mate)}
    assert world.ws.remote[(ADDRESS, f"pp/task-{task_id}")] == C3 and world.offer(offer_id)["pushed"] == C3
    assert world.ws.of("/fetch", owner)[-1][1] == {
        "address": ADDRESS, "commit": C3, "purpose": "return", "agent": world.get(OWNER, task_id)["agent_id"],
        "base": C1, "protected": ["tests/**"]}
    # step 2 came back done; it has no check of the owner's, so none ran; step 1 was the owner's own
    assert world.ws.of("/checks", owner) == []
    returned_task, report = world.tasks.returns[-1]
    assert returned_task == task_id and report["descends"] is True and report["commit"] == C3
    # with the tree the owner's workspace says it left the worktree at, which ade/tasks.py keeps as the end state
    assert report["tree"] == RETURNED_TREE
    assert report["steps"] == [{"n": 2, "state": "done", "evidence": "said_done", "check_passed": None}]
    offer = world.offer(offer_id)
    assert offer["state"] == "returned" and offer["address"] is None and offer["teammate_user"] == mate
    assert world.notices(OWNER)[-1] == "A task of yours came back from your teammate"
    # where the task goes from there is tasks.take_return's to say (both steps back done: done, by its own code)
    assert world.get(OWNER, task_id)["state"] not in ("handed_over", "returning")


def test_take_back_with_the_teammates_workspace_stopped_comes_back_at_once(world):
    """Their machine stopped, no attempt runs there: nothing to stop, and the last commit pushed comes back
    at once."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    task_offers.drive()                                 # their work pushed while their workspace ran
    with store.connect() as db:                         # an attempt still recorded as working when it stopped
        db.execute("UPDATE ade_agent_tasks SET state = 'running', lease_run = 'ffffffffffff' WHERE id = ?", (theirs,))
    world.provider.running.discard(world.user(MATE))
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    assert world.stops == [] and world.offer(offer_id)["state"] == "returned"
    assert world.tasks.returns[-1][1]["commit"] == C3 and len(world.ws.of("/push", world.user(MATE))) == 1


C4 = "0" * 39 + "5"


@pytest.mark.parametrize("when", ["never", "before_more"])
def test_work_the_teammates_side_never_pushed_does_not_come_back(world, when):
    """Take back with the teammate's machine stopped: the task comes back with the last commit pushed,
    and a step done after it, never pushed, is not copied back as done: nothing pushed at all (the handed commit comes
    back, no step with it), or work after the last push (C3 comes back, not C4)."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    handed_commit = world.offer(offer_id)["commit"]
    if when == "before_more":
        task_offers.drive()
        assert world.offer(offer_id)["pushed"] == C3
        world.checkpoint(MATE, theirs, C4, n=2)         # more work after the push, which no round pushes before the stop
        world.ws.steps[C4] = [2]
    world.provider.running.discard(world.user(MATE))
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    report = world.tasks.returns[-1][1]
    if when == "never":
        assert report["commit"] == handed_commit and report["steps"] == [] and world.ws.of("/push", world.user(MATE)) == []
    else:
        assert report["commit"] == C3 and [s["n"] for s in report["steps"]] == [2]
    assert world.offer(offer_id)["state"] == "returned"


def test_the_round_pushes_a_received_tasks_work_between_its_attempts_and_once(world):
    """What the teammate's side pushes: nothing while an attempt of the received task works; its work once
    the attempt has ended, by its hash, to the owner's task's branch; and nothing again while nothing new was done."""
    task_id, offer_id, theirs = accepted(world)
    mate = world.user(MATE)
    worked(world, theirs)
    task_offers.drive()
    assert world.ws.of("/push", mate) == [] and world.offer(offer_id)["pushed"] is None
    ended(theirs)
    task_offers.drive()
    task_offers.drive()
    assert [b["commit"] for _, b in world.ws.of("/push", mate)] == [C3] and world.offer(offer_id)["pushed"] == C3
    assert world.ws.remote[(ADDRESS, f"pp/task-{task_id}")] == C3 and world.get(OWNER, task_id)["state"] == \
        "handed_over"


def test_the_way_back_fetches_the_work_its_own_push_just_pushed(world):
    """The way back pushes their work first (_push_theirs) and fetches the commit that push leaves on the offer: here
    no round pushed it before (their attempt's end came after the round's push step), so the way back's own push is
    the one that brings it, and the commit fetched is that one, not the handed one."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    assert world.offer(offer_id)["pushed"] is None
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers._ways_back(int(time.time()))
    assert world.offer(offer_id)["pushed"] == C3 and world.offer(offer_id)["state"] == "returned"
    assert world.tasks.returns[-1][1]["commit"] == C3


def test_a_push_of_the_teammates_work_the_remote_does_not_take_is_not_kept_as_pushed(world):
    """Their workspace pushed, and the remote's branch did not move (`pushed` false, as for a push that is not a
    fast-forward): the offer keeps no such commit, a later round tries again, and a way back meanwhile brings what was
    pushed before, here the handed commit with no step."""
    task_id, offer_id, theirs = accepted(world)
    mate = world.user(MATE)
    worked(world, theirs, running=False)
    handed_commit = world.offer(offer_id)["commit"]
    world.ws.lands = False
    task_offers.drive()
    assert [b["commit"] for _, b in world.ws.of("/push", mate)] == [C3] and world.offer(offer_id)["pushed"] is None
    task_offers.drive()
    assert len(world.ws.of("/push", mate)) == 2
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    report = world.tasks.returns[-1][1]
    assert report["commit"] == handed_commit and report["steps"] == [] and world.offer(offer_id)["state"] == "returned"


def test_a_push_of_the_teammates_work_that_does_not_answer_holds_the_way_back(world):
    """The app never acts on silence: their workspace runs but does not answer the push of their work,
    so Take back waits, saying so, rather than bring back less than they pushed; once it answers, the task comes
    back with it."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.ws.silent.add(world.user(MATE))
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    assert world.offer(offer_id)["why"] == task_offers.WAITING and world.tasks.returns == []
    world.ws.silent.clear()
    task_offers.drive()
    assert world.offer(offer_id)["state"] == "returned" and world.tasks.returns[-1][1]["commit"] == C3


def test_a_secret_shape_in_the_teammates_work_is_not_pushed_and_the_offer_says_so(world):
    """Their workspace's scan finds a secret shape in what the push would carry: nothing is pushed, the offer names
    the shape and the file in the app's words, never the value, and what comes back is the work pushed before it."""
    task_id, offer_id, theirs = accepted(world)
    handed_commit = world.offer(offer_id)["commit"]
    worked(world, theirs, running=False)
    world.ws.secret = {"shape": "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)", "file": "config.txt"}
    task_offers.drive()
    offer = world.offer(offer_id)
    assert offer["pushed"] is None and world.ws.remote[(ADDRESS, f"pp/task-{task_id}")] == handed_commit
    assert offer["why"] == task_offers.THEIR_SECRET.format(
        what="a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_) in config.txt")
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    assert world.tasks.returns[-1][1]["commit"] == handed_commit and world.tasks.returns[-1][1]["steps"] == []


# ---- a push held by its secret scan, and its lift -------------------------------------------------------------------

TOKEN_SHAPE = "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"


def finding(n, kind="line", shape=TOKEN_SHAPE, file="tests/fixture.py", commit=C2, column=9, line=4, place=2):
    """A finding as a workspace's scan names it (task_runs._scan_all): its id the n-th of 32 hex."""
    return {"kind": kind, "shape": shape, "file": None if kind == "commit" else file, "line": line, "place": place,
            "column": column, "commit": commit, "finding": f"{n:032x}"}


def holds_of(w, email):
    r = w.client.get(f"{T}/offers", headers=w.h(email))
    assert r.status_code == 200, r.text
    return r.json()["secret_holds"]


def lift(w, email, task_id, findings, confirmation=None, change=None):
    """POST /tasks/{task_id}/secret-lift, with a confirmation made for the lift of those findings on the task unless
    one is given (task_offers.lift_subject)."""
    word = confirmation if confirmation is not None else \
        w.confirmation(email, change or task_offers.lift_subject(task_id, findings))
    return w.client.post(f"{T}/tasks/{task_id}/secret-lift", headers=w.h(email),
                         json={"findings": findings, "confirmation": word})


def lifts(email_of, w):
    with store.connect() as db:
        rows = db.execute("SELECT user_id, repo_address, finding, kind, shape FROM ade_secret_lifts ORDER BY rowid")
        return [(email_of[r["user_id"]], r["repo_address"], r["finding"], r["kind"], r["shape"]) for r in rows]


def test_a_push_held_for_findings_keeps_them_and_its_owner_lifts_them_with_a_confirmation_for_the_task(world):
    """A hold that no change of the task can clear is lifted by the person whose push it is, finding
    by finding, with a confirmation made for the lift of those findings on the task (task_offers.lift_subject); once
    every finding the hold names is lifted the task waits again as before the offer, and its next push is given the
    lifts, goes out, and is journaled."""
    task_id, _ = setup_pressed(world)
    first, second = finding(1), finding(2, kind="commit")
    world.ws.findings[world.user(OWNER)] = [first, second]
    made = world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    offer = world.offer(made["offer"]["id"])
    task = world.get(OWNER, task_id)
    assert offer["state"] == "failed" and world.ws.remote == {}
    assert offer["why"] == task_offers.SECRET.format(what=f"{TOKEN_SHAPE} in tests/fixture.py, and 1 more")
    assert task["state"] == "needs_owner" and task["cause"] == "handoff" and task["reason"] == offer["why"]
    assert json.loads(offer["secret_findings"]) == [first, second]
    assert holds_of(world, OWNER) == [{"task_id": task_id, "offer": offer["id"], "side": "owner",
                                       "findings": [dict(first, lifted=False), dict(second, lifted=False)]}]
    assert holds_of(world, MATE) == []
    # refused, nothing lifted and nothing spent: a confirmation made for another change, none, a finding not held
    r = lift(world, OWNER, task_id, [first["finding"]], change=f"task-custody-confirm:{task_id}")
    assert r.status_code == 403, r.text
    assert lift(world, OWNER, task_id, [first["finding"]], confirmation="").status_code == 403
    word = world.confirmation(OWNER, task_offers.lift_subject(task_id, [first["finding"]]))
    r = lift(world, OWNER, task_id, [first["finding"], "f" * 32], confirmation=word)
    assert r.status_code == 409 and r.json()["detail"] == task_offers.NOT_HELD
    for bad in ([], ["F" * 32], [first["finding"] + "0"]):
        assert lift(world, OWNER, task_id, bad, confirmation=word).status_code == 400, bad
    assert lift(world, OWNER, task_id, [f"{n:032x}" for n in range(21)], confirmation=word).status_code == 422
    # one made for the first finding lifts no other, and is left unspent
    r = lift(world, OWNER, task_id, [second["finding"]], confirmation=word)
    assert r.status_code == 403, r.text
    assert lifts({}, world) == []
    # the confirmation the refusals left unspent lifts the first alone: the task still waits for its owner
    r = lift(world, OWNER, task_id, [first["finding"], first["finding"]], confirmation=word)
    assert r.status_code == 200, r.text
    assert [f["lifted"] for f in r.json()["hold"]["findings"]] == [True, False]
    assert world.get(OWNER, task_id)["state"] == "needs_owner"
    assert lift(world, OWNER, task_id, [first["finding"]], confirmation=word).status_code == 403      # spent
    r = lift(world, OWNER, task_id, [second["finding"]])
    assert r.status_code == 200, r.text
    task = world.get(OWNER, task_id)
    assert task["state"] == "suspended" and task["reason"] == task_offers.LIFTED_BACK
    assert r.json()["task"]["state"] == "suspended"
    assert [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.secret_lifted"] == [
        {"offer": offer["id"], "findings": [first["finding"]], "back": False},
        {"offer": offer["id"], "findings": [second["finding"]], "back": True}]
    owner = {world.user(OWNER): OWNER}
    assert lifts(owner, world) == [(OWNER, ADDRESS, first["finding"], "line", TOKEN_SHAPE),
                                   (OWNER, ADDRESS, second["finding"], "commit", TOKEN_SHAPE)]
    # handed again: the push is given the owner's lifts, newest first, and goes out, the journal saying which it met
    again = world.offer_to(task_id)
    world.settle()
    assert world.ws.of("/push")[-1][1]["lifted"] == [second["finding"], first["finding"]]
    # with the app's key for the owner, the same in whichever workspace scans
    assert world.ws.of("/push")[-1][1]["findings_key"] == task_offers.findings_key(world.user(OWNER))
    assert task_offers.findings_key(world.user(OWNER)) != task_offers.findings_key(world.user(MATE))
    assert world.offer(again["offer"]["id"])["state"] == "offered" and holds_of(world, OWNER) == []
    assert [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.secret_lift_used"] == [
        {"offer": again["offer"]["id"], "findings": [first["finding"], second["finding"]], "by": "owner"}]
    # a finding of a hold that is no longer the task's newest offer's is lifted no more
    r = lift(world, OWNER, task_id, [first["finding"]])
    assert r.status_code == 409 and r.json()["detail"] == task_offers.NOT_HELD


def test_a_lift_leaves_a_task_held_for_its_owner_by_anything_else_as_it_is(world):
    """The task waits again only where what holds it for its owner is the hold lifted: one held since for another cause,
    or for another hand-off's reason, stays held, its lift still recorded."""
    for n, cause, reason in ((20, "watch", "A watched place changed: look at it."),
                             (21, "handoff", "The task's branch moved.")):
        # a finding each, since a lift covers its finding in every later push to the address
        task_id, _ = setup_pressed(world) if cause == "watch" else (world.task(OWNER, world.agent(OWNER)), None)
        world.ws.findings[world.user(OWNER)] = [finding(n)]
        world.offer_to(task_id)
        world.running(OWNER, MATE)
        world.settle()
        assert world.get(OWNER, task_id)["state"] == "needs_owner"
        with store.connect() as db:
            db.execute("UPDATE ade_agent_tasks SET cause = ?, reason = ? WHERE id = ?", (cause, reason, task_id))
        r = lift(world, OWNER, task_id, [finding(n)["finding"]])
        assert r.status_code == 200, r.text
        task = world.get(OWNER, task_id)
        assert (task["state"], task["cause"], task["reason"]) == ("needs_owner", cause, reason)
        assert [e["payload"]["back"] for e in world.journal(task_id) if e["kind"] == "task.secret_lifted"] == [False]

def test_a_custody_hand_off_held_for_findings_waits_again_for_its_accounts_once_they_are_lifted(world):
    task_id, _, _ = setup_custody(world)
    world.ws.findings[world.user(OWNER)] = [finding(4)]
    found, hold = hand(world, task_id)
    world.running(OWNER, MATE)
    world.settle()
    offer = world.row("ade_agent_task_offers", "hold_id", hold["id"])
    assert offer["state"] == "failed" and world.get(OWNER, task_id)["state"] == "needs_owner"
    assert lift(world, OWNER, task_id, [finding(4)["finding"]]).status_code == 200
    task = world.get(OWNER, task_id)
    resets = world.row("ade_backend_windows", "backend", "account:aaaaaaaaaaaa")["resets_at"]
    assert task["state"] == "suspended" and task["cause"] == "window" and task["waits_until"] == resets


def test_the_teammate_alone_lifts_a_hold_of_their_side_s_push_and_their_work_then_goes_out(world):
    """A hold of the teammate's side's push is theirs to lift, on the task they received, with their own confirmation:
    the owner, who is told why their work is not coming, cannot lift it, since it goes out with the teammate's sign-in.
    Once lifted, their side's next push is given their lifts alone and goes out, and the note goes."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    mine = finding(3, kind="name", file="notes/\u2026.txt")
    world.ws.findings[world.user(MATE)] = [mine]
    task_offers.drive()
    offer = world.offer(offer_id)
    assert offer["pushed"] is None and offer["state"] == "accepted"
    assert offer["why"] == task_offers.THEIR_SECRET.format(what=f"{TOKEN_SHAPE} in a file's name, notes/\u2026.txt")
    assert holds_of(world, OWNER) == []
    assert holds_of(world, MATE) == [{"task_id": theirs, "offer": offer_id, "side": "teammate",
                                      "findings": [dict(mine, lifted=False)]}]
    # the owner can lift it neither on their own task, whose newest offer is the teammate's, nor on the teammate's
    r = lift(world, OWNER, task_id, [mine["finding"]])
    assert r.status_code == 409 and r.json()["detail"] == task_offers.NOT_HELD
    assert lift(world, OWNER, theirs, [mine["finding"]]).status_code == 404
    version = world.get(MATE, theirs)["state_version"]
    with store.connect() as db:                 # a lift of the owner's for the same address, never given to theirs
        db.execute("INSERT INTO ade_secret_lifts (user_id, repo_address, finding, task_id, offer_id, kind, shape, at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (world.user(OWNER), ADDRESS, "e" * 32, task_id, offer_id, "line",
                                                TOKEN_SHAPE, int(time.time()) + 50))
    r = lift(world, MATE, theirs, [mine["finding"]])
    assert r.status_code == 200, r.text
    assert r.json()["hold"]["findings"] == [dict(mine, lifted=True)] and r.json()["hold"]["side"] == "teammate"
    assert world.get(MATE, theirs)["state_version"] == version          # their task does not move: nothing held it
    task_offers.drive()
    assert world.ws.of("/push", world.user(MATE))[-1][1]["lifted"] == [mine["finding"]]
    assert world.ws.of("/push", world.user(MATE))[-1][1]["findings_key"] == task_offers.findings_key(world.user(MATE))
    offer = world.offer(offer_id)
    assert offer["pushed"] == C3 and offer["why"] is None and offer["secret_findings"] is None
    assert holds_of(world, MATE) == []
    used = {"offer": offer_id, "findings": [mine["finding"]], "by": "teammate"}
    assert [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.secret_lift_used"] == [used]
    assert [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.secret_lift_used"] == [used]
    assert lifts({world.user(MATE): MATE, world.user(OWNER): OWNER}, world) == [
        (OWNER, ADDRESS, "e" * 32, "line", TOKEN_SHAPE), (MATE, ADDRESS, mine["finding"], "name", TOKEN_SHAPE)]


def test_a_lift_is_its_lifters_for_the_address_and_given_with_each_of_their_later_pushes_there(world):
    """A lift covers its finding in the lifter's later pushes to the same address, whichever task: a made-up token in a
    file of the default branch holds every task cut before it, and one lift answers them all. A push to another
    address is given none, nor is another user's push; LIFTS_PASSED are given, the task's own first, then the newest
    (otherwise a range of more findings than the window would never go out)."""
    task_id, _ = setup_pressed(world)
    world.ws.findings[world.user(OWNER)] = [finding(5)]
    world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    assert lift(world, OWNER, task_id, [finding(5)["finding"]]).status_code == 200
    second = world.task(OWNER, world.agent(OWNER))
    world.offer_to(second)
    world.settle()
    assert world.ws.of("/push")[-1][1]["lifted"] == [finding(5)["finding"]]
    elsewhere = world.task(OWNER, world.agent(OWNER), address="https://github.com/acme/other.git")
    world.offer_to(elsewhere)
    world.settle()
    assert world.ws.of("/push")[-1][1]["lifted"] == []
    now = int(time.time())
    with store.connect() as db:
        for n in range(task_offers.LIFTS_PASSED + 5):
            db.execute("INSERT INTO ade_secret_lifts (user_id, repo_address, finding, task_id, offer_id, kind, shape, "
                       "at) VALUES (?,?,?,?,?,?,?,?)", (world.user(OWNER), ADDRESS, f"{1000 + n:032x}", task_id, "o",
                                                        "line", TOKEN_SHAPE, now + 10 + n))
        given = task_offers._lifts_for(db, world.user(OWNER), ADDRESS)
        assert task_offers._lifts_for(db, world.user(MATE), ADDRESS) == []
    assert len(given) == task_offers.LIFTS_PASSED == 1000
    assert given[0] == f"{1000 + task_offers.LIFTS_PASSED + 4:032x}" and finding(5)["finding"] not in given
    # a task's own lift, the oldest of all, is given first with its push, the window then the newest
    fourth = world.task(OWNER, world.agent(OWNER))
    with store.connect() as db:
        db.execute("INSERT INTO ade_secret_lifts (user_id, repo_address, finding, task_id, offer_id, kind, shape, at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (world.user(OWNER), ADDRESS, "d" * 32, fourth, "o", "line", TOKEN_SHAPE,
                                                now - 1000))
    world.offer_to(fourth)
    world.settle()
    pushed = world.ws.of("/push")[-1][1]["lifted"]
    assert len(pushed) == task_offers.LIFTS_PASSED and pushed[0] == "d" * 32
    assert pushed[1] == f"{1000 + task_offers.LIFTS_PASSED + 4:032x}"


def test_findings_in_another_form_are_left_out_and_what_the_scan_could_not_read_is_never_lifted(world):
    """The app keeps a workspace's findings only in the form the scan gives them (an id of 32 hex, a kind and a shape
    it knows, a commit's id, a file's name with what does not print taken out); a hold from a workspace that names no
    finding, or of what its scan could not read, has nothing to lift."""
    task_id, _ = setup_pressed(world)
    good = finding(9, commit="not a commit")
    world.ws.findings[world.user(OWNER)] = [
        good, dict(finding(6), finding="A" * 32), dict(finding(7), shape="a made-up shape"),
        dict(finding(8), kind="tag"), dict(finding(10), file="a\x1bb\nc.txt", column=True),
        dict(finding(11), finding=None), dict(finding(12), line=True), dict(finding(13), line=0),
        dict(finding(14), line="7"), dict(finding(15), line=task_offers.COLUMN_MOST + 1),
        dict(finding(16), line=task_offers.COLUMN_MOST), dict(finding(17), place=True), dict(finding(18), place=0),
        dict(finding(19), place="2"), dict(finding(20), place=task_offers.COLUMN_MOST + 1),
        dict(finding(21), place=task_offers.COLUMN_MOST), {k: v for k, v in finding(22).items() if k != "place"}]
    made = world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    offer = world.offer(made["offer"]["id"])
    assert json.loads(offer["secret_findings"]) == [
        dict(good, commit=None), dict(finding(10), file="abc.txt", column=None)] + [
        dict(finding(n), line=None) for n in (12, 13, 14, 15)] + [finding(16, line=task_offers.COLUMN_MOST)] + [
        dict(finding(n), place=None) for n in (17, 18, 19, 20)] + [finding(21, place=task_offers.COLUMN_MOST),
                                                                   finding(22, place=None)]
    assert offer["why"] == task_offers.SECRET.format(what=f"{TOKEN_SHAPE} in tests/fixture.py, and 12 more")
    # a workspace from before findings, and what a scan could not read: the hold names it, and nothing is lifted
    for shape, words in (({"shape": TOKEN_SHAPE, "file": "config.txt"}, task_offers.SECRET),
                         ({"shape": "a change too large to scan", "file": None}, task_offers.SCAN_UNREAD)):
        task = world.task(OWNER, world.agent(OWNER))
        world.ws.secret = shape
        made = world.offer_to(task)
        world.settle()
        offer = world.offer(made["offer"]["id"])
        assert offer["why"] == words.format(what=" in ".join(v for v in shape.values() if v))
        assert offer["secret_findings"] is None
        assert lift(world, OWNER, task, [finding(12)["finding"]]).status_code == 409
    assert [h["task_id"] for h in holds_of(world, OWNER)] == [task_id]


def test_a_file_the_app_cleans_into_a_secret_shape_is_said_as_an_ellipsis():
    """Dropping what does not print, or the cut at 300 characters, could
    make a secret shape of a file's name the workspace said with none; the app looks again, and says it whole as an
    ellipsis. A shape's own name is no file, and keeps its words."""
    hidden = chr(0x2026)

    def kept(file):
        return task_offers._findings({"secret_findings": [dict(finding(1), file=file)]})[0]["file"]
    cut = "a" * 288 + "/PP-ABCDEFGHJ/x.txt"
    for file in (cut, "gh" + chr(0x200B) + "p_" + "R" * 36, "gh" + chr(0xA0) + "p_" + "R" * 36):
        assert tasks.secret_shape(file) is None                     # the control: none as the workspace said it
        assert kept(file) == hidden, file
    assert task_offers._clean(cut) == "a" * 288 + "/PP-ABCDEFGH"     # the control: the cut's own words
    assert kept("notes/plan.txt") == "notes/plan.txt"
    words = task_offers._held_words({"secret_shape": {"shape": TOKEN_SHAPE, "file": cut}}, [], task_offers.SECRET,
                                    task_offers.SCAN_UNREAD)
    assert words == task_offers.SECRET.format(what=f"{TOKEN_SHAPE} in {hidden}")


def test_a_hold_s_words_say_its_first_finding_s_commit_whole():
    """Two commits whose ids share their first 12 characters (two such take a
    minute of one core's work to make) read alike where a commit is cut there; the app's words, like the page's,
    say it whole."""
    one, two = "0205bc31ba55ae9be4eea86eae365a70293c459e", "0205bc31ba55aa8d7c1fefb126546d720616a7d8"
    assert one[:12] == two[:12] and one != two                       # the control: alike cut at 12
    said = [task_offers._held_words({}, task_offers._findings({"secret_findings": [finding(1, kind="commit",
                                                                                           commit=c)]}),
                                    task_offers.SECRET, task_offers.SCAN_UNREAD) for c in (one, two)]
    assert said == [task_offers.SECRET.format(what=f"{TOKEN_SHAPE} in commit {c}") for c in (one, two)]


def test_a_push_a_lift_let_through_is_journaled_whatever_became_of_its_offer_meanwhile(world):
    """The use of a lift is journaled whether or not the offer still stands once the push has gone out, since the push
    has gone out either way."""
    task_id, _ = setup_pressed(world)
    world.ws.findings[world.user(OWNER)] = [finding(30)]
    world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    assert lift(world, OWNER, task_id, [finding(30)["finding"]]).status_code == 200
    again = world.offer_to(task_id)

    def meanwhile(body):
        if body["purpose"] == "push":
            with store.connect() as db:
                db.execute("UPDATE ade_agent_task_offers SET state = 'withdrawn' WHERE id = ?", (again["offer"]["id"],))
    world.ws.during = meanwhile
    world.settle()
    world.ws.during = None
    assert world.ws.remote and world.offer(again["offer"]["id"])["state"] == "withdrawn"     # it went out
    assert [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.secret_lift_used"] == [
        {"offer": again["offer"]["id"], "findings": [finding(30)["finding"]], "by": "owner"}]


def test_a_teammate_push_a_lift_let_through_is_journaled_though_its_offer_stopped_being_accepted(world):
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.ws.findings[world.user(MATE)] = [finding(31)]
    task_offers.drive()
    assert lift(world, MATE, theirs, [finding(31)["finding"]]).status_code == 200

    def meanwhile(body):
        if body["purpose"] == "push":
            with store.connect() as db:
                db.execute("UPDATE ade_agent_task_offers SET state = 'returned' WHERE id = ?", (offer_id,))
    world.ws.during = meanwhile
    task_offers.drive()
    world.ws.during = None
    used = {"offer": offer_id, "findings": [finding(31)["finding"]], "by": "teammate"}
    assert [e["payload"] for e in world.journal(theirs) if e["kind"] == "task.secret_lift_used"] == [used]
    assert [e["payload"] for e in world.journal(task_id) if e["kind"] == "task.secret_lift_used"] == [used]


def test_a_teammate_push_is_given_the_lifts_made_on_the_task_they_received_first(world):
    """The teammate's side's own-first order, tested on their side (tested on the owner's side alone, looking their
    lifts up under the owner's task would pass): past LIFTS_PASSED lifts of theirs
    for the address, those made on the task they received go out with its push first, not those under the owner's."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    now = int(time.time())
    with store.connect() as db:
        rows = [(world.user(MATE), ADDRESS, f"{1000 + n:032x}", "f" * 32, now + 10 + n)
                for n in range(task_offers.LIFTS_PASSED + 5)]
        rows += [(world.user(MATE), ADDRESS, "d" * 32, theirs, now - 1000),       # theirs, the oldest of all
                 (world.user(MATE), ADDRESS, "c" * 32, task_id, now - 2000)]      # under the owner's task's id
        db.executemany("INSERT INTO ade_secret_lifts (user_id, repo_address, finding, task_id, offer_id, kind, shape, "
                       "at) VALUES (?,?,?,?,'o','line',?,?)", [(u, a, f, t, TOKEN_SHAPE, at) for u, a, f, t, at in rows])
    task_offers.drive()
    pushed = world.ws.of("/push", world.user(MATE))[-1][1]["lifted"]
    assert len(pushed) == task_offers.LIFTS_PASSED and pushed[0] == "d" * 32 and "c" * 32 not in pushed
    assert pushed[1] == f"{1000 + task_offers.LIFTS_PASSED + 4:032x}"
    assert world.offer(offer_id)["pushed"] == C3


def test_a_waiting_note_written_over_a_teammate_push_s_hold_is_cleared_with_it_and_written_again(world):
    """Where the teammate's side's push goes out and clears a hold's
    note, the `CASE` clears whatever note stands, the way back's waiting note written over the hold's among them; the
    way back writes it again in the same round, so the owner reads it as before once the round ends."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.ws.findings[world.user(MATE)] = [finding(41)]
    world.provider.running.discard(world.user(OWNER))
    world.provider.refusing.add(world.user(OWNER))
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    offer = world.offer(offer_id)
    assert offer["why"] == task_offers.WAITING_OWN and json.loads(offer["secret_findings"]) == [finding(41)]
    assert lift(world, MATE, theirs, [finding(41)["finding"]]).status_code == 200
    world.ws.findings[world.user(MATE)] = []
    seen = []

    def meanwhile(body):
        if body.get("purpose") == "push":
            seen.append(world.offer(offer_id)["why"])
    world.ws.during = meanwhile
    # the push alone, read before the way back runs: the note goes with the hold (a CASE that left a
    # waiting note in place would pass, since the round's end reads the same either way)
    task_offers._teammate_pushes(task_offers._now(None))
    world.ws.during = None
    offer = world.offer(offer_id)
    assert offer["pushed"] == C3 and offer["secret_findings"] is None and offer["why"] is None
    assert seen and seen[0] == task_offers.WAITING_OWN       # the note stood over the hold's when the push went
    task_offers.drive()
    offer = world.offer(offer_id)
    assert offer["why"] == task_offers.WAITING_OWN and world.tasks.returns == []    # the way back wrote it again


def test_a_teammate_push_s_unread_hold_is_said_until_a_push_goes_out_and_has_nothing_to_lift(world):
    """What the scan could not read is said with no findings and nothing to lift, and the note goes once a later push
    goes out."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.ws.secret = {"shape": "a change too large to scan", "file": None}
    task_offers.drive()
    offer = world.offer(offer_id)
    assert offer["why"] == task_offers.THEIR_UNREAD.format(what="a change too large to scan")
    assert json.loads(offer["secret_findings"]) == [] and holds_of(world, MATE) == []
    assert lift(world, MATE, theirs, [finding(32)["finding"]]).status_code == 409
    world.ws.secret = None
    task_offers.drive()
    offer = world.offer(offer_id)
    assert offer["pushed"] == C3 and offer["why"] is None and offer["secret_findings"] is None


def test_a_hold_of_twenty_findings_is_lifted_with_one_confirmation(world):
    """FINDINGS_MOST at its edge: a hold names twenty, and a lift of all twenty is taken."""
    task_id, _ = setup_pressed(world)
    twenty = [finding(100 + n) for n in range(task_offers.FINDINGS_MOST)]
    world.ws.findings[world.user(OWNER)] = twenty
    world.offer_to(task_id)
    world.running(OWNER, MATE)
    world.settle()
    assert [f["finding"] for f in holds_of(world, OWNER)[0]["findings"]] == [f["finding"] for f in twenty]
    r = lift(world, OWNER, task_id, [f["finding"] for f in twenty])
    assert r.status_code == 200, r.text
    assert world.get(OWNER, task_id)["reason"] == task_offers.LIFTED_BACK


def test_a_lift_s_change_is_the_task_and_a_digest_of_the_findings_it_lifts_as_the_page_makes_it():
    """task_offers.lift_subject, written from its words (the page's liftDigest says the same): the ids sorted, once each,
    joined by commas, SHA-256, its first 32 hex."""
    a, b, task = "a" * 32, "b" * 32, "0" * 32
    digest = hashlib.sha256(f"{a},{b}".encode()).hexdigest()[:32]
    assert task_offers.lift_subject(task, [b, a, b]) == f"task-secret-lift:{task}:{digest}"
    assert task_offers.lift_subject(task, [a]) != task_offers.lift_subject(task, [a, b])
    assert passkeys.CHANGE.fullmatch(task_offers.lift_subject(task, [a]))


def test_a_push_s_answer_counts_as_passed_only_the_lifts_it_was_given():
    a, b = "a" * 32, "b" * 32
    assert task_offers._passed({"lifted_passed": [a, b, a, 3, None]}, [a]) == [a]
    assert task_offers._passed({"lifted_passed": a}, [a]) == [] and task_offers._passed({}, [a]) == []

def test_take_back_waits_while_the_teammates_workspace_does_not_answer_and_says_so(world):
    """Their machine runs but does not answer: Take back waits, as the app never acts on silence, and the
    page says so; once it answers, the task comes back."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs)
    world.ws.silent.add(world.user(MATE))
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    assert world.offer(offer_id)["why"] == task_offers.WAITING and world.tasks.returns == []
    assert world.get(OWNER, task_id)["state"] == "returning"
    world.ws.silent.clear()
    task_offers.drive()
    ended(theirs)
    task_offers.drive()
    assert world.offer(offer_id)["state"] == "returned" and world.offer(offer_id)["why"] is None


def test_take_back_waits_while_the_owners_own_workspace_cannot_start_and_says_so(world):
    """The way back's first step, with the app acting on no silence: the owner's own workspace, which fetches the
    work, will not start (the provider refuses it): the way back waits, the page saying so; once it starts, the task
    comes back."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.provider.running.discard(world.user(OWNER))
    world.provider.refusing.add(world.user(OWNER))
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    assert world.offer(offer_id)["why"] == task_offers.WAITING_OWN and world.tasks.returns == []
    world.provider.refusing.clear()
    task_offers.drive()
    assert world.offer(offer_id)["state"] == "returned" and world.tasks.returns[-1][1]["commit"] == C3


def test_take_back_brings_an_unknown_effect_with_it(world):
    """Take back does not wait on the teammate's unknown effect: it comes back with the task, unknown, so the
    owner's task does not go on."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.effect(MATE, theirs, "unknown", target="origin main")
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    effects = world.tasks.returns[-1][1]["effects"]
    assert [(e["kind"], e["target"], e["settled"]) for e in effects] == [("git_remote", "origin main", "unknown")]
    with store.connect() as db:
        assert tasks.unknown_effects(db, world.user(OWNER), task_id) == 1


@pytest.mark.parametrize("returned", ["failed_check", "not_descending", "protected_and_loaded"])
def test_a_return_reports_what_the_owners_workspace_found(world, returned):
    """A returned step whose owner's check fails is reported failed; a returned commit that does
    not descend from the handed one is reported so; one that touches a protected path or changes a file a next run
    would load is reported with them; the owner's task waits for the owner in each case (tasks.take_return)."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    if returned == "failed_check":
        # the owner's step 2, which came back done, has a check of the owner's, and it fails on the returned commit
        with store.connect() as db:
            db.execute("UPDATE ade_agent_task_steps SET check_cmd = 'pytest -q tests/test_docs.py' WHERE task_id = ? "
                       "AND n = 2", (task_id,))
        world.ws.exits[2] = 1
    elif returned == "not_descending":
        world.ws.descends = False
    else:
        world.ws.touched = ["tests/test_parser.py"]
        world.ws.loaded[C3] = [".claude/settings.json"]
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    report = world.tasks.returns[-1][1]
    if returned == "failed_check":
        assert report["steps"] == [{"n": 2, "state": "done", "evidence": "said_done", "check_passed": False}]
        # the owner's check given with the request, since the owner never ran an attempt of step 2
        assert report["descends"] is True and world.ws.of("/checks") == [
            (world.user(OWNER), {"commit": C3, "steps": [2], "checks": {"2": "pytest -q tests/test_docs.py"}})]
    elif returned == "not_descending":
        assert report["descends"] is False and world.ws.of("/checks") == []
        assert world.get(OWNER, task_id)["state"] == "needs_owner"
    else:
        assert report["protected_touched"] == ["tests/test_parser.py"]
        assert report["loaded_changed"] == [".claude/settings.json"]
        assert world.get(OWNER, task_id)["state"] == "needs_owner"


@pytest.mark.parametrize("how", ["done", "stopped", "retired", "removed"])
def test_the_received_task_ending_starts_its_way_back(world, how):
    """The teammate's task done, stopped, its agent retired, or its user removed: each maps to the way back,
    which none needs the teammate to cooperate in, and each brings back the last commit the teammate pushed (a
    teammate an admin removed comes back by the way back, whose first step fetches it)."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    task_offers.drive()                                 # the round pushes their work while their workspace runs
    assert world.offer(offer_id)["pushed"] == C3 and world.ws.remote[(ADDRESS, f"pp/task-{task_id}")] == C3
    with store.connect() as db:
        if how in ("done", "stopped"):
            db.execute("UPDATE ade_agent_tasks SET state = ? WHERE id = ?", (how, theirs))
        elif how == "retired":
            db.execute("UPDATE ade_agents SET retired_at = 1 WHERE user_id = ?", (world.user(MATE),))
        else:
            db.execute("DELETE FROM users WHERE id = ?", (world.user(MATE),))
    task_offers.drive()
    # the way back's own entry; what follows it (task.done, here, with both steps back done) is tasks.take_return's
    assert "task.returned" in [e["kind"] for e in world.journal(task_id)]
    assert world.offer(offer_id)["state"] == "returned"
    report = world.tasks.returns[-1][1]
    assert report["commit"] == C3 and [(s["n"], s["state"]) for s in report["steps"]] == [(2, "done")]


def test_a_custody_task_comes_back_by_itself_on_fresh_room_and_not_otherwise(world, monkeypatch):
    """At the end of a custody task's step on the custodian's side, it goes back when one of the
    owner's accounts in its relay set has room on fresh evidence; not on stale room (an allowed window two hours old),
    not while it has an unknown effect, not while the custodian keeps it; and it comes back with the relay turned off
    while it was with the custodian."""
    task_id, mate_agent, custody_id = setup_custody(world)
    theirs = handed(world, task_id)["their_task"]
    mate = world.user(MATE)

    def step_end():
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return task_offers.on_step_end(db, mate, theirs, "ffffffffffff", int(time.time()))
    assert step_end() is False
    world.window(OWNER, "aaaaaaaaaaaa", "allowed", seen_at=int(time.time()) - 2 * HOUR)
    assert step_end() is False
    world.window(OWNER, "aaaaaaaaaaaa", "allowed")
    world.effect(MATE, theirs, "unknown")
    assert step_end() is False
    with store.connect() as db:
        db.execute("UPDATE ade_agent_effects SET state = 'done' WHERE task_id = ?", (theirs,))
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_RELAY", "off")
    assert step_end() is True
    assert world.get(OWNER, task_id)["state"] == "returning" and world.get(MATE, theirs)["returned"] == 1
    assert world.journal(task_id)[-1]["kind"] == "task.returned"
    assert step_end() is False                          # a second return of the same hand-off does nothing


@pytest.mark.parametrize("crossing", ["kept on", "setting off", "switch off"])
def test_a_custody_task_comes_back_by_itself_with_crossing_turned_off_on_room_in_its_own_group(world, crossing):
    """A custody task's way back with the crossing switch: the owner's personal task, crossing on, handed to the
    custodian; then a seat of another organisation of
    the owner's has fresh room. With crossing kept on, that room brings the task back at the end of a custody task's
    step. With crossing turned off while the task is away, the deployment's setting or the task's own switch, it does
    not, since custody's way back reads the task's relay set, which the switches decide (task_offers._has_room asks
    both custody's members and the tasks' eligibility, either of which leaves the seat out); and the task still comes
    back by itself, on fresh room in its own group."""
    world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, custody_id = setup_custody(world, cross_groups=1)
    theirs = handed(world, task_id)["their_task"]
    world.account(OWNER, "dddddddddddd", "Claude Team", "Xanadu", "org-x", position=2)
    world.window(OWNER, "dddddddddddd", "allowed")
    mate = world.user(MATE)

    def step_end():
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return task_offers.on_step_end(db, mate, theirs, "ffffffffffff", int(time.time()))
    if crossing == "setting off":
        world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off")
    elif crossing == "switch off":
        r = world.client.post(f"/api/ade/agents/tasks/{task_id}/switches", headers=world.h(OWNER),
                              json={"cross_groups": False})
        assert r.status_code == 200 and world.get(OWNER, task_id)["cross_groups"] == 0, r.text
    if crossing != "kept on":
        assert step_end() is False
        world.window(OWNER, "aaaaaaaaaaaa", "allowed")
    assert step_end() is True
    assert world.get(OWNER, task_id)["state"] == "returning" and world.get(MATE, theirs)["returned"] == 1
    assert world.journal(task_id)[-1]["kind"] == "task.returned"


@pytest.mark.parametrize("switch", ["on", "off"])
def test_custodys_condition_is_read_over_the_relay_set_the_tasks_crossing_switch_decides(world, switch):
    """Custody's condition with the crossing switch: it is read over the task's relay set, and an account outside it is
    not read: every account of the owner's own group refused, and the owner's seat of another organisation with room,
    the deployment's setting on. With the task's switch on, the seat is in the relay set, so its room keeps custody's
    condition from naming a candidate until the seat is refused too; with the switch off, the seat is outside the set
    and has no say, and the condition names the custodian's agent at once (a version of task_offers._members reading the
    groups as though the switch were on passes every other test of this file). It runs the condition
    (task_offers.custody_candidate) alone, not the hand-off."""
    now = int(time.time())
    world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    task_id, mate_agent, custody_id = setup_custody(world, cross_groups=1 if switch == "on" else 0)
    world.account(OWNER, "dddddddddddd", "Claude Team", "Xanadu", "org-x", position=2)
    world.window(OWNER, "dddddddddddd", "allowed")
    if switch == "on":
        assert candidate(world, task_id) is None
        world.window(OWNER, "dddddddddddd", "rejected", resets_at=now + HOUR)
    found = candidate(world, task_id)
    assert found is not None and found["agent_id"] == mate_agent, found


@pytest.mark.parametrize("case", ["on", "off", "switch off"])
def test_custodys_condition_on_a_task_with_its_crossing_ticked_is_read_over_the_relay_set_its_switches_decide(world,
                                                                                                             case):
    """The same on a task with custody's crossing ticked: the tick is custody's, for its
    own hand-off, and has no say in the owner's relay set, which the task's switch and the deployment's setting
    decide. Every account of the owner's own group refused and the owner's seat of another organisation with room.
    With the task's switch on and the deployment's setting on, the seat is in the relay set and keeps custody's
    condition from naming a candidate until it is refused too (task_offers._members leaving a ticked task's other
    groups out names one at once). With the switch on and the setting turned off after the task was made, the seat is
    outside the set, and the condition names the custodian's agent at once (a _crossing_on letting the tick stand in
    for the setting does not). With the switch off and the setting on, the same (task_offers._members reading a ticked
    task's groups as though its switch were on does not)."""
    now = int(time.time())
    world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "on")
    switch = 0 if case == "switch off" else 1
    task_id, mate_agent, custody_id = setup_custody(world, cross_groups=switch, custody_cross=1)
    assert (world.get(OWNER, task_id)["custody_cross"], world.get(OWNER, task_id)["cross_groups"]) == (1, switch)
    world.account(OWNER, "dddddddddddd", "Claude Team", "Xanadu", "org-x", position=2)
    world.window(OWNER, "dddddddddddd", "allowed")
    if case == "on":
        assert candidate(world, task_id) is None
        world.window(OWNER, "dddddddddddd", "rejected", resets_at=now + HOUR)
    elif case == "off":
        world.mp.setenv("OPEN_EDITION_ADE_TASK_CROSS_GROUPS", "off")
    found = candidate(world, task_id)
    assert found is not None and found["agent_id"] == mate_agent, found


@pytest.mark.parametrize("waiting", ["after_an_attempt", "never_started", "held_by_the_custodians_reserve"])
def test_a_custody_task_waiting_for_the_custodians_reset_comes_back_when_the_owner_has_room(world, waiting):
    """While a custody task waits on the custodian's side, after an attempt of it or before any (every
    account of the custodian's out when it was taken, so the custodian's relay left it waiting for a reset), one of
    the owner's accounts having room on fresh evidence brings it back by itself, at the next round. The
    custodian's one account held back by its weekly reserve alone, Auto-resume off, so
    that the custodian's relay counts no reset and the custody task waits, suspended, for nothing but a Continue
    (tasks.HELD_BY_A_RESERVE), the custodian not pushed; it comes back all the same. Made to need the
    custodian, cause window, it would never be brought back by custody's own return, which takes back only a custody
    task that waits or has not started (task_offers._ways_back_due)."""
    task_id, mate_agent, custody_id = setup_custody(world)
    if waiting == "never_started":
        world.window(MATE, "cccccccccccc", "rejected", resets_at=int(time.time()) + HOUR)
    if waiting == "held_by_the_custodians_reserve":
        world.mp.setenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", "0")
        world.window(MATE, "cccccccccccc", "allowed_warning", resets_at=int(time.time()) + 86400, window="seven_day")
    theirs = handed(world, task_id)["their_task"]
    if waiting == "after_an_attempt":
        ended(theirs)
    else:
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE task_id = ?", (theirs,)).fetchone()[0] == 0
    if waiting == "held_by_the_custodians_reserve":
        held = world.get(MATE, theirs)
        assert (held["state"], held["cause"], held["waits_until"], held["reason"]) == (
            "suspended", "window", None, tasks.HELD_BY_A_RESERVE), dict(held)
        assert tasks.alerts(world.user(MATE), int(time.time())) == []
    assert world.get(MATE, theirs)["state"] == "suspended"
    task_offers.drive()
    assert world.get(OWNER, task_id)["state"] == "handed_over"
    world.window(OWNER, "aaaaaaaaaaaa", "allowed")
    world.ws.commits.add(C1)
    task_offers.drive()
    assert world.journal(task_id)[-2]["kind"] == "task.returned" or world.journal(task_id)[-1]["kind"] == \
        "task.returned"
    assert world.offer(world.row("ade_agent_task_offers", "their_task", theirs)["id"])["state"] == "returned"


@pytest.mark.parametrize("lease", [False, True])
def test_a_custody_task_sent_back_at_its_step_end_is_held_returned_never_left_running(world, lease):
    """Custody's own return begins in the tasks' copy of the custody task's step end, where the
    task is still `running` with its lease already let go, and ade/tasks.py moves it no further; a way back that held
    for their user only a task not `running` would leave that one `running` for good, its page saying so. A task running
    with no lease runs nothing, and is held returned like one waiting; one whose attempt still holds the lease is left
    to end (the positive control)."""
    task_id, mate_agent, custody_id = setup_custody(world)
    theirs = handed(world, task_id)["their_task"]
    world.window(OWNER, "aaaaaaaaaaaa", "allowed")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not lease:
            # as tasks._ended leaves the task before its _after_step asks on_step_end: the lease let go, still running
            db.execute("UPDATE ade_agent_tasks SET lease_run = NULL WHERE id = ?", (theirs,))
        assert task_offers.on_step_end(db, world.user(MATE), theirs, "ffffffffffff", int(time.time())) is True
    held = world.get(MATE, theirs)
    assert held["returned"] == 1
    if lease:
        assert held["state"] == "running" and held["lease_run"] is not None
    else:
        assert (held["state"], held["cause"], held["reason"]) == ("needs_owner", "returned",
                                                                  task_offers.RETURNED_REASON)


@pytest.mark.parametrize("how", ["room", "step_end", "give_back", "stopped"])
def test_custodys_own_return_goes_on_and_a_give_back_waits_for_the_owner(world, monkeypatch, how):
    """Custody's own return, begun because
    one of the owner's accounts has room (by the round, or at the end of the custody task's step), passes take_return
    the way "return", and the owner's relay goes on as Between steps allows, its next attempt queued on the account
    with room; a Give back, the custodian's, and a custody task stopped on their side pass none, and the task waits for
    the owner."""
    task_id, mate_agent, custody_id = setup_custody(world)
    theirs = handed(world, task_id)["their_task"]
    ended(theirs, state="stopped" if how == "stopped" else "suspended")
    world.window(OWNER, "aaaaaaaaaaaa", "allowed")
    world.ws.commits.add(C1)
    if how == "give_back":
        version = world.get(MATE, theirs)["state_version"]
        r = world.client.post(f"{T}/tasks/{theirs}/give-back", headers=world.h(MATE), json={"state_version": version})
        assert r.status_code == 200, r.text
    elif how == "step_end":
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            assert task_offers.on_step_end(db, world.user(MATE), theirs, "ffffffffffff", int(time.time())) is True
    sent = []                                           # the attempts the owner's workspace is told of at once
    monkeypatch.setattr(agents, "_deliver_soon", lambda user, start=False: sent.append(user["id"]))
    task_offers.drive()
    began = [e for e in world.journal(task_id) if e["kind"] == "task.returned"][-1]
    report = world.tasks.returns[-1][1]
    back = world.get(OWNER, task_id)
    if how in ("room", "step_end"):
        assert began["payload"]["why"] == task_offers.CUSTODY_RETURN and report["way"] == "return"
        assert back["state"] == "running" and back["lease_run"] is not None and world.user(OWNER) in sent
    else:
        assert began["payload"]["why"] is None and "way" not in report
        assert (back["state"], back["reason"]) == ("suspended", "It came back from your teammate: Continue to go on.")
        assert world.user(OWNER) not in sent


@pytest.mark.parametrize("seat", [False, True])
def test_a_custody_hand_off_says_whether_it_crossed_a_group(world, seat):
    """A custody hand-off's task.handed_over carries `crossed`, true
    where the received task's group differs from that of the account the owner's task was on, so that the tasks' custody
    line names the case custody_cross; false on the same group, where the line names none."""
    if seat:
        task_id, mate_agent, custody_id = setup_custody(world, mate_plan="Claude Team", mate_org="Yarra",
                                                        mate_org_id="org-y")
        version = world.get(OWNER, task_id)["state_version"]
        r = world.client.post(f"{T}/tasks/{task_id}/custody-cross", headers=world.h(OWNER),
                              json={"state_version": version,
                                    "confirmation": world.confirmation(OWNER, f"task-custody-cross:{task_id}")})
        assert r.status_code == 200, r.text
        world.settle()
        confirm_for_custody(world, task_id)
    else:
        task_id, mate_agent, custody_id = setup_custody(world)
    handed(world, task_id)
    event = [e for e in world.journal(task_id) if e["kind"] == "task.handed_over"][-1]
    assert event["payload"]["crossed"] is seat
    with store.connect() as db:
        lines = [m for m in tasks.moves(db, tasks.get(db, world.user(OWNER), task_id)) if m["kind"] == "custody"]
    assert [m["cases"] for m in lines] == [["custody_cross"] if seat else []]


def test_a_custody_task_comes_back_once_a_reset_has_passed_and_not_past_an_unknown_effect(world):
    """With an injected clock past its reset: while a custody task waits on the custodian's side,
    the round brings it back once a refused window of the owner's relay set has reached its reset, which is fresh
    evidence of room (no allowed window seen since); not while the custody task has an unknown effect."""
    task_id, mate_agent, custody_id = setup_custody(world)
    offer = handed(world, task_id)
    theirs = offer["their_task"]
    ended(theirs)
    task_offers.drive()
    assert world.get(OWNER, task_id)["state"] == "handed_over"         # every reset an hour away
    world.effect(MATE, theirs, "unknown")
    past = earliest_reset(world) + 60
    task_offers.drive(past)
    assert world.get(OWNER, task_id)["state"] == "handed_over"
    with store.connect() as db:
        db.execute("UPDATE ade_agent_effects SET state = 'done' WHERE task_id = ?", (theirs,))
    task_offers.drive(past)
    assert world.offer(offer["id"])["state"] == "returned"
    assert "task.returned" in [e["kind"] for e in world.journal(task_id)]


def test_take_back_wins_over_keep_either_way_and_needs_no_version(world):
    """The custodian's Keep stops the return by itself, at a step's end and while the custody task waits on
    the custodian's side; the owner's Take back, with no version, wins over it; a Keep after a Take back is refused."""
    task_id, mate_agent, custody_id = setup_custody(world)
    theirs = handed(world, task_id)["their_task"]
    version = world.get(MATE, theirs)["state_version"]
    r = world.client.post(f"{T}/tasks/{theirs}/keep", headers=world.h(MATE), json={"state_version": version})
    assert r.status_code == 200 and world.row("ade_agent_task_offers", "their_task", theirs)["why"] == task_offers.KEPT
    world.window(OWNER, "aaaaaaaaaaaa", "allowed")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert task_offers.on_step_end(db, world.user(MATE), theirs, "ffffffffffff", int(time.time())) is False
    ended(theirs)
    task_offers.drive()
    assert world.get(OWNER, task_id)["state"] == "handed_over"
    r = world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    assert r.status_code == 200 and r.json()["task"]["state"] == "returning"
    version = world.get(MATE, theirs)["state_version"]
    r = world.client.post(f"{T}/tasks/{theirs}/keep", headers=world.h(MATE), json={"state_version": version})
    assert r.status_code == 409


def test_keep_and_give_back_each_act_against_the_custody_tasks_version(world):
    """Keep and Give back each act against the custody task's own state_version, so two cannot both act."""
    task_id, mate_agent, custody_id = setup_custody(world)
    theirs = handed(world, task_id)["their_task"]
    version = world.get(MATE, theirs)["state_version"]
    r = world.client.post(f"{T}/tasks/{theirs}/keep", headers=world.h(MATE), json={"state_version": version - 1})
    assert r.status_code == 409
    r = world.client.post(f"{T}/tasks/{theirs}/give-back", headers=world.h(MATE), json={"state_version": version})
    assert r.status_code == 200 and world.get(OWNER, task_id)["state"] == "returning"
    r = world.client.post(f"{T}/tasks/{theirs}/give-back", headers=world.h(MATE), json={"state_version": version})
    assert r.status_code == 409
    r = world.client.post(f"{T}/tasks/{theirs}/keep", headers=world.h(MATE), json={"state_version": version + 1})
    assert r.status_code == 409


@pytest.mark.parametrize("order", ["keep_then_note", "note_then_keep"])
def test_a_note_on_the_offer_never_undoes_a_keep_nor_a_keep_the_note(world, order):
    """Keep is a column of its own (kept_at), not the offer's free-text `why`, which the round writes over with a note
    when the teammate's work holds a secret shape (_push_theirs): kept in `why`, a kept custody task would go back
    anyway once the owner had room, at the end of its step (on_step_end) and while it waited on the custodian's side
    (_ways_back_due), and a Keep pressed after the note would write over it, so the owner would never be told of the
    secret shape. In either order the task is kept, by the round and at its step's end, the note stays the offer's
    `why`, and Take back still wins over the Keep."""
    task_id, mate_agent, custody_id = setup_custody(world)
    offer_id = handed(world, task_id)["id"]
    theirs = world.offer(offer_id)["their_task"]
    worked(world, theirs, running=False)
    ended(theirs)                                       # no attempt works, so the round pushes their work
    world.ws.secret = {"shape": "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)", "file": "config.txt"}
    note = task_offers.THEIR_SECRET.format(what="a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_) in config.txt")

    def keep():
        version = world.get(MATE, theirs)["state_version"]
        r = world.client.post(f"{T}/tasks/{theirs}/keep", headers=world.h(MATE), json={"state_version": version})
        assert r.status_code == 200, r.text
    if order == "keep_then_note":
        keep()
        assert world.offer(offer_id)["why"] == task_offers.KEPT        # the page's words, where nothing else is said
        task_offers.drive()                             # the note, while every account of the owner's is refused
    else:
        task_offers.drive()
        assert world.offer(offer_id)["why"] == note
        keep()
    assert world.offer(offer_id)["why"] == note
    world.window(OWNER, "aaaaaaaaaaaa", "allowed")      # the owner's room, on fresh evidence
    task_offers.drive()                                 # the custody task waits on the custodian's side
    assert world.get(OWNER, task_id)["state"] == "handed_over" and world.offer(offer_id)["why"] == note
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert task_offers.on_step_end(db, world.user(MATE), theirs, "ffffffffffff", int(time.time())) is False
    assert world.get(OWNER, task_id)["state"] == "handed_over"
    r = world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    assert r.status_code == 200 and r.json()["task"]["state"] == "returning"


@pytest.mark.parametrize("order", ["keep_then_hold", "hold_then_keep"])
def test_a_kept_task_s_hold_lifted_and_its_push_gone_out_says_the_keep_again(world, order):
    """A Keep is kept in the offer's kept_at, and a secret-shape note may write over the Keep's words in `why`; that
    note is cleared once the teammate's side's push goes out. The clear gives `why` back the Keep's words where
    the task is kept, in either order; the Keep itself is never touched. A task not kept has none back
    (test_the_teammate_alone_lifts_a_hold_of_their_side_s_push_and_their_work_then_goes_out)."""
    task_id, _, _ = setup_custody(world)
    offer_id = handed(world, task_id)["id"]
    theirs = world.offer(offer_id)["their_task"]
    worked(world, theirs, running=False)
    ended(theirs)                                       # no attempt works, so the round pushes their work
    mine = finding(7)
    world.ws.findings[world.user(MATE)] = [mine]
    note = task_offers.THEIR_SECRET.format(what=f"{TOKEN_SHAPE} in tests/fixture.py")

    def keep():
        version = world.get(MATE, theirs)["state_version"]
        r = world.client.post(f"{T}/tasks/{theirs}/keep", headers=world.h(MATE), json={"state_version": version})
        assert r.status_code == 200, r.text
    if order == "keep_then_hold":
        keep()
        task_offers.drive()
    else:
        task_offers.drive()
        keep()
    offer = world.offer(offer_id)
    assert offer["why"] == note and offer["kept_at"] is not None and offer["pushed"] is None
    assert lift(world, MATE, theirs, [mine["finding"]]).status_code == 200
    task_offers.drive()
    offer = world.offer(offer_id)
    assert offer["pushed"] == C3 and offer["secret_findings"] is None
    assert offer["why"] == task_offers.KEPT and offer["kept_at"] is not None
    assert world.get(OWNER, task_id)["state"] == "handed_over"          # still kept


def test_a_stale_return_is_refused_once_the_hand_off_came_back(world):
    """Once the way back has run, a second return of the same hand-off, or a Take back, changes nothing."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    assert world.offer(offer_id)["state"] == "returned"
    states = list(world.tasks.states)
    r = world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    assert r.status_code == 412
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert task_offers.on_step_end(db, world.user(MATE), theirs, "ffffffffffff", int(time.time())) is False
    task_offers.drive()
    assert world.tasks.states == states and len(world.tasks.returns) == 1


# ---- what is kept of a teammate, and who sees what ------------------------------------------------------------------

def test_the_operators_command_lists_and_reduces_a_person_in_the_offer_and_custody_rows(world):
    """The operator's command, through these two functions: every
    offer and custody row that names the teammate is listed, by email or by user id, with whether it still stands;
    reducing them clears the ended ones (a returned offer, an ended custody), what a join would name them by among
    it, and leaves those that stand; a reduced row is listed no more."""
    task_id, returned_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    standing = world.offer_to(world.task(OWNER, world.agent(OWNER)))["offer"]["id"]
    world.account(MATE, "eeeeeeeeeeee", "Claude Max")
    receiver = world.agent(MATE, account="eeeeeeeeeeee", name="Receiver")
    accept_custody(world, ask_custody(world, owner=OTHER), [receiver])
    gone = ask_custody(world, owner=THIRD)
    accept_custody(world, gone, [receiver])
    world.client.post(f"{T}/custody/{gone}/end", headers=world.h(THIRD), json={})
    mate = world.user(MATE)
    with store.connect() as db:
        listed = task_offers.person_rows(db, MATE)
        assert task_offers.person_rows(db, mate) == listed
        assert task_offers.person_rows(db, "nobody@example.com") == []
    assert sorted((r["table"], r["state"], r["standing"]) for r in listed) == [
        ("custody", "accepted", True), ("custody", "ended", False), ("offer", "held", True),
        ("offer", "returned", False)]
    assert {r["id"] for r in listed if r["table"] == "offer"} == {returned_id, standing}
    now = int(time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert task_offers.reduce_person(db, MATE, now) == {"offers": 1, "custody": 1}
    offer = world.offer(returned_id)
    assert offer["teammate_user"] is None and offer["address"] is None and offer["their_task"] is None
    assert offer["state"] == "returned"
    custody = world.custody(gone)
    assert custody["custodian_user"] is None and custody["agents"] == "[]" and custody["reduced_at"] == now
    with store.connect() as db:
        assert sorted((r["table"], r["standing"]) for r in task_offers.person_rows(db, MATE)) == [
            ("custody", True), ("offer", True)]
    assert world.offer(standing)["teammate_user"] == mate


def test_the_operators_command_finds_by_their_user_id_a_teammate_whose_account_was_removed(world):
    """Once an admin removed the teammate's account, a returned offer and an ended custody still name them by their
    user id; a command that took a user id only where an account had it would list and reduce neither. The tasks' own
    command takes such an id for the account record (tasks._person), and both are listed by it, and reduced."""
    task_id, returned_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    world.account(MATE, "eeeeeeeeeeee", "Claude Max")
    receiver = world.agent(MATE, account="eeeeeeeeeeee", name="Receiver")
    gone = ask_custody(world, owner=THIRD)
    accept_custody(world, gone, [receiver])
    world.client.post(f"{T}/custody/{gone}/end", headers=world.h(THIRD), json={})
    mate = world.user(MATE)
    with store.connect() as db:
        db.execute("DELETE FROM users WHERE id = ?", (mate,))
    assert (world.offer(returned_id)["teammate_user"], world.custody(gone)["custodian_user"]) == (mate, mate)
    with store.connect() as db:
        listed = task_offers.person_rows(db, mate)
    assert sorted((r["table"], r["id"], r["state"], r["standing"]) for r in listed) == sorted([
        ("custody", gone, "ended", False), ("offer", returned_id, "returned", False)])
    now = int(time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert task_offers.reduce_person(db, mate, now) == {"offers": 1, "custody": 1}
    offer, custody = world.offer(returned_id), world.custody(gone)
    assert (offer["teammate_user"], offer["their_task"]) == (None, None)
    assert (custody["custodian_user"], custody["address"], custody["reduced_at"]) == (None, None, now)
    with store.connect() as db:
        assert task_offers.person_rows(db, mate) == []


def test_the_operators_command_finds_an_address_typed_before_its_person_had_an_account(world):
    """What the command still finds: a person named only by the address an owner typed for them, before they had an
    account here, is found by that address; and once they have one, by their user id too, since an id an account has
    still brings that account's email (a version that took every 32-hex id as one no account has would find nothing
    then, and one that took a person as the tasks' command does would find nothing by an address no account has)."""
    asked = ask_custody(world, address=OTHER)           # no account has OTHER's address yet: the request names it alone
    assert world.custody(asked)["custodian_user"] is None
    with store.connect() as db:
        assert [r["id"] for r in task_offers.person_rows(db, OTHER)] == [asked]
    other = world.user(OTHER)                           # they have an account since
    with store.connect() as db:
        assert [r["id"] for r in task_offers.person_rows(db, other)] == [asked]


def test_a_row_the_operator_reduced_while_it_stood_is_reduced_once_it_ends(world):
    """A row of an offer or a custody still standing is listed as
    standing, and reduced once it ends: the command leaves a row that still stands, since its hand-off needs the
    person, and, keeping nothing of having been asked, would leave the row naming them until the operator ran it again.
    It marks such a row, which the scheduler's round leaves while it stands (the way back still finds the teammate) and
    reduces once it has ended, a custody row with its reduced_at; a row made after the command ran is not its to
    reduce."""
    task_id, offer_id, theirs = accepted(world)         # the task with the teammate: the offer stands
    world.account(MATE, "eeeeeeeeeeee", "Claude Max")
    receiver = world.agent(MATE, account="eeeeeeeeeeee", name="Receiver")
    custody_id = ask_custody(world, owner=THIRD)
    accept_custody(world, custody_id, [receiver])       # custody accepted: it stands until either side ends it
    mate = world.user(MATE)
    now = int(time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert task_offers.reduce_person(db, MATE, now) == {"offers": 0, "custody": 0}
    world.forget(now + 1)                               # while they stand, the round leaves them
    assert (world.offer(offer_id)["teammate_user"], world.custody(custody_id)["custodian_user"]) == (mate, mate)
    with store.connect() as db:
        assert sorted((r["table"], r["standing"]) for r in task_offers.person_rows(db, mate)) == [
            ("custody", True), ("offer", True)]
    worked(world, theirs, running=False)
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()                                 # the way back, which needs the teammate, brings it home
    assert world.offer(offer_id)["state"] == "returned" and world.tasks.returns[-1][1]["commit"] == C3
    world.client.post(f"{T}/custody/{custody_id}/end", headers=world.h(THIRD), json={})
    second = world.task(OWNER, world.agent(OWNER))       # a later offer to them, made after the command ran, and back
    later_agent = world.agent(MATE, account="cccccccccccc", name="Later")
    later = offered(world, second)["offer"]["id"]
    r = world.client.post(f"{T}/offers/{later}/accept", headers=world.h(MATE),
                          json={"agent_id": later_agent, "adopt_checks": []})
    assert r.status_code == 200, r.text
    world.client.post(f"{T}/tasks/{second}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    assert world.offer(later)["state"] == "returned"
    world.forget(now + 2)
    offer, custody = world.offer(offer_id), world.custody(custody_id)
    assert (offer["state"], offer["teammate_user"], offer["address"], offer["their_task"]) == (
        "returned", None, None, None)
    assert (custody["state"], custody["custodian_user"], custody["address"], custody["agents"],
            custody["reduced_at"]) == ("ended", None, None, "[]", now + 2)
    assert world.offer(later)["teammate_user"] == mate  # not the command's: made after it ran
    with store.connect() as db:
        assert [r["id"] for r in task_offers.person_rows(db, mate)] == [later]


def test_no_journal_entry_hold_or_notice_names_a_teammate_or_the_work(world):
    """After an offer accepted and taken back, one declined, custody asked, accepted, used and ended,
    and a request declined: no journal entry and no hold's summary names a teammate, by user id, email or agent; and
    every notice of the track is content-free, its words push.WORDS' own, naming no task, step, backend or
    teammate."""
    task_id, offer_id, theirs = accepted(world)
    worked(world, theirs, running=False)
    world.client.post(f"{T}/tasks/{task_id}/take-back", headers=world.h(OWNER))
    task_offers.drive()
    declined = offered(world, world.task(OWNER, world.agent(OWNER)))["offer"]["id"]
    world.client.post(f"{T}/offers/{declined}/decline", headers=world.h(MATE), json={})
    with store.connect() as db:
        db.execute("UPDATE ade_backend_windows SET resets_at = ? WHERE user_id = ?", (int(time.time()) + HOUR,
                                                                                     world.user(OWNER)))
    world.account(OWNER, "bbbbbbbbbbbb", "Claude Pro", position=1)
    for account in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
        world.window(OWNER, account, "rejected", resets_at=int(time.time()) + HOUR)
    world.account(MATE, "eeeeeeeeeeee", "Claude Max")
    receiver = world.agent(MATE, account="eeeeeeeeeeee", name="Receiver")
    custody_id = ask_custody(world)
    accept_custody(world, custody_id, [receiver])
    third = world.task(OWNER, world.agent(OWNER))
    confirm_for_custody(world, third)
    handed(world, third)
    world.client.post(f"{T}/custody/{custody_id}/end", headers=world.h(OWNER), json={})
    world.user(OTHER)
    refused = ask_custody(world, address=OTHER)
    world.client.post(f"{T}/custody/{refused}/decline", headers=world.h(OTHER), json={})
    with store.connect() as db:
        summaries = [r["summary"] for r in db.execute("SELECT summary FROM ade_holds").fetchall()]
        mate_agents = [r["id"] for r in db.execute("SELECT id FROM ade_agents WHERE user_id = ?", (world.user(MATE),))]
    who = (MATE, world.user(MATE), OTHER, world.user(OTHER), *mate_agents)
    assert summaries and not any(_named(s, *who) for s in summaries)
    # the owner's journal: a teammate's own journal, of the task they received, is theirs and names their agent
    payloads = [e["payload"] for e in world.journal() if e["user_id"] == world.user(OWNER)]
    assert {"task.offered", "task.handed_over", "task.taken_back", "task.offer_declined", "custody.asked",
            "custody.accepted", "custody.ended"} <= {e["kind"] for e in world.journal()}
    assert not _named(json.dumps(payloads), *who)
    titles = {words[0] for words in push.WORDS.values()}
    told = [m for _, m in world.told]
    assert told and all(m.title in titles for m in told)
    work = ("Parse the config", "config file", "Write the parser", "Use the standard", OWNER, MATE, "acme/demo",
            "aaaaaaaaaaaa", "cccccccccccc")
    assert not any(_named(m.title + " " + m.body, *work) for m in told)


def test_each_users_offers_custody_and_tasks_are_their_own(world):
    """Another user's offer, custody request or task answers 404 (or, for a task they do not hold,
    412) on every route of the track, and changes nothing."""
    task_id, mate_agent = setup_pressed(world)
    made = offered(world, task_id)
    offer_id = made["offer"]["id"]
    world.account(OTHER, "ffffffffffff", "Claude Max")
    stranger_agent = world.agent(OTHER, account="ffffffffffff")
    custody_id = ask_custody(world)
    stranger = world.h(OTHER)
    for path, body in ((f"/offers/{offer_id}/confirm", {"confirmation": "x"}), (f"/offers/{offer_id}/withdraw", {}),
                       (f"/offers/{offer_id}/accept", {"agent_id": stranger_agent, "adopt_checks": []}),
                       (f"/offers/{offer_id}/decline", {}), (f"/tasks/{task_id}/take-back", {}),
                       (f"/custody/{custody_id}/confirm", {"confirmation": "x"}),
                       (f"/custody/{custody_id}/accept", {"agents": [stranger_agent], "confirmation": "x"}),
                       (f"/custody/{custody_id}/decline", {}), (f"/custody/{custody_id}/end", {}),
                       (f"/tasks/{task_id}/custody-confirm", {"state_version": 0, "confirmation": "x"}),
                       (f"/tasks/{task_id}/custody-cross", {"state_version": 0, "confirmation": "x"}),
                       (f"/tasks/{task_id}/keep", {"state_version": 0}),
                       (f"/tasks/{task_id}/give-back", {"state_version": 0}),
                       (f"/tasks/{task_id}/secret-lift", {"findings": ["0" * 32], "confirmation": "x"})):
        r = world.client.post(T + path, headers=stranger, json=body)
        assert r.status_code == 404, (path, r.status_code, r.text)
    assert world.client.get(f"{T}/offers", headers=stranger).json() == {"made": [], "received": [],
                                                                         "secret_holds": []}
    assert world.client.get(f"{T}/custody", headers=stranger).json() == {"as_owner": None, "as_custodian": [],
                                                                         "tasks": []}
    assert world.offer(offer_id)["state"] == "offered" and world.custody(custody_id)["state"] == "asked"


# ---- the round's own thread -----------------------------------------------------------------------------------------

def test_the_rounds_thread_runs_only_while_something_stands(world, monkeypatch):
    """What waits on a workspace runs in a thread of the module's own: woken with nothing standing, it runs one round
    and ends, so a process with no offer or custody keeps no thread for them; with an offer standing it stays; once
    nothing stands it ends, and a wake starts it again."""
    monkeypatch.setattr(task_offers, "DRIVE_EVERY", 0.05)
    try:
        REAL_KICK()
        first = task_offers._driver[0]
        first.join(timeout=20)
        assert not first.is_alive() and task_offers._driver == []
        task_id, _ = setup_pressed(world)
        made = world.offer_to(task_id)
        REAL_KICK()
        second = task_offers._driver[0]
        time.sleep(0.5)
        assert second.is_alive() and second is not first
        world.client.post(f"{T}/offers/{made['offer']['id']}/withdraw", headers=world.h(OWNER), json={})
        second.join(timeout=20)
        assert not second.is_alive() and task_offers._driver == []
    finally:
        monkeypatch.setattr(task_offers, "_standing", lambda: False)
        task_offers._wake.set()
        for thread in list(task_offers._driver):
            thread.join(timeout=20)
