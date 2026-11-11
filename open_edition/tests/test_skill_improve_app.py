"""The improve loop's proposals for a business skill, the app's half (proofpane_open/ade/skill_improve.py), against a
stand-in for the workspace's routes whose objects are a real git repository's: the counts and their bar, the schedule
and its budget, a proposal kept only once a record the app read holds it, the app's checks of its texts and of the
objects it is sent, the critic, the publish, the look, the expiry, the watch and its proposal to go back, the switch,
the owner's taps, the app's word and its sender, and the routes. No real claude, codex, grok or qwen is started, and no
hosting service is reached."""

import base64
import json
import os
import subprocess
import time

import pytest

from conftest import signed_up
from proofpane_open import auth, store
from proofpane_open.ade import agents, controller, improve, shapes, skill_improve as si, workspace_client

PATH = ".claude/skills/release-notes/SKILL.md"
CURRENT = "---\nname: release-notes\ndescription: Write the release notes for a version.\n---\nWrite the notes.\n"
PROPOSED = ("---\nname: release-notes\ndescription: Write the release notes for a version, one line a change.\n---\n"
            "Write the notes.\n")
DAY = 86400
NO_ROUTE = workspace_client.WorkspaceCallError(502, "The workspace answered 404.")
BUILDS = [{"claude": "/bin/claude", "version": "2.1.274", "newer": False, "why": None}]


def git(*args, cwd):
    full = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
            and k not in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")}
    full.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    p = subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", *args], cwd=cwd,
                       env=full, capture_output=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def blob_id(text, fmt="sha1"):
    return shapes.object_id("blob", text.encode("utf-8"), fmt)


class Repo:
    """A real repository holding the skill, whose commits and objects the stand-in sends: `base` holding CURRENT,
    and a commit on it changing the skill to a proposal's text."""

    def __init__(self, path, fmt="sha1"):
        self.path, self.fmt = str(path), fmt
        os.makedirs(self.path)
        git("init", "-q", "-b", "main", f"--object-format={fmt}", cwd=self.path)
        self.write(PATH, CURRENT)
        self.write("README.md", "demo\n")
        self.base = self.commit("first")

    def write(self, name, text):
        full = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def commit(self, message):
        git("add", "-A", cwd=self.path)
        git("commit", "-q", "-m", message, cwd=self.path)
        return git("rev-parse", "HEAD", cwd=self.path).decode().strip()

    def propose(self, text=PROPOSED, base=None):
        base = base or self.base
        git("checkout", "-q", "-b", f"p{time.time_ns()}", base, cwd=self.path)
        self.write(PATH, text)
        head = self.commit("proposal")
        git("checkout", "-q", "main", cwd=self.path)
        return head

    def raw(self, kind, oid):
        return git("cat-file", kind, oid, cwd=self.path)

    def oid(self, spec):
        return git("rev-parse", spec, cwd=self.path).decode().strip()

    def objects(self, base, head):
        """What the workspace sends (skill_improve._sent_objects): both commits, each tree on the path at each, both
        blobs, each once."""
        out, seen = [], set()
        parts = PATH.split("/")
        for commit in (base, head):
            chain = [(commit, "commit"), (self.oid(f"{commit}^{{tree}}"), "tree")]
            chain += [(self.oid(f"{commit}:{'/'.join(parts[:i])}"), "tree") for i in range(1, len(parts))]
            chain.append((self.oid(f"{commit}:{PATH}"), "blob"))
            for oid, kind in chain:
                if oid not in seen:
                    seen.add(oid)
                    out.append({"kind": kind, "id": oid, "data": base64.b64encode(self.raw(kind, oid)).decode()})
        return out


class Workspace:
    """The workspace's routes as the app calls them, a call at a time, each kept: its record of
    proposals (a file the stand-in says is signed unless `holds` is false), a review that proposes `reply`, the objects
    of a real repository, the critic's `specific`, and the look's `seen`."""

    def __init__(self, repo):
        self.repo = repo
        self.calls, self.words = [], []
        self.generation, self.proposals, self.holds, self.word = 0, [], True, False
        self.reply = {"text": PROPOSED, "hypothesis": "Say what one line holds."}
        self.specific, self.seen, self.fail, self.heads, self.bases = False, {}, {}, {}, {}
        self.candidates = [{"folder": "release-notes", "path": PATH, "names": ["release-notes"],
                            "blob": blob_id(CURRENT, repo.fmt), "mode": "100644", "notes": [],
                            "current_version": repo.base, "before_version": None}]
        self.listed, self.builds, self.pr_state, self.record_route = ["demo"], BUILDS, None, True
        self.view_state, self.step_five, self.restored = "record", True, None
        self.later_builds = None            # the builds a look and an acceptance read, once set
        self.bars_at = None                 # where the look read whether a start is refused now
        self.critic_answer = None           # the critic's answer as the workspace gives it, where a test sets one

    def file(self):
        record = {"version": 1, "generation": self.generation, "switches": {}, "repos": {"demo": self.proposals}}
        return base64.b64encode(json.dumps({"record": record, "mac": "m" * 64}).encode()).decode()

    def _change(self):
        self.generation += 1

    def call(self, endpoint, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        route = path.split("?")[0]
        if route in self.fail:
            raise self.fail[route]
        if route == "/skill-improve/record":
            if not self.record_route:
                raise workspace_client.WorkspaceCallError(NO_ROUTE.status, str(NO_ROUTE))
            if method == "PUT":
                self.words.append(body)
                self.word = True
                return {"state": "record", "held": None}
            return {"file": self.file(), "holds": self.holds, "generation": self.generation, "word": self.word,
                    "state": self.view_state, "held": None, "seen": self.generation, "refused": []}
        if route == "/skill-improve/record/restore":
            self.restored = body
            self._change()
            return {"generation": self.generation}
        if route == "/repos":
            return {"repos": [{"name": n, "linked": "/own"} for n in self.listed]}
        if route == "/skill-improve/switch":
            return dict(body, url=None, branch="refs/heads/main", at=1, linked=True)
        if route == "/skill-improve/candidates":
            return {"repo": "demo", "ok": True, "base": {"head": self.repo.base}, "candidates": self.candidates,
                    "not": {}, "builds": self.builds}
        if route == "/skill-improve/reviews":
            return self.review(body)
        if route.endswith("/objects"):
            pid = route.split("/")[3]
            base = self.bases.get(pid, self.repo.base)
            head = self.repo.propose(body["text"], base=base)
            self.heads[pid] = head
            for p in self.proposals if self.step_five else ():     # the record gains its commit
                if p["id"] == pid and head not in p["commits"]:
                    p["commits"].append(head)
                    self._change()
            return {"object_format": self.repo.fmt, "base": base, "head": head, "path": PATH,
                    "objects": self.repo.objects(base, head)}
        if route.endswith("/critic"):
            if self.critic_answer is not None:
                return self.critic_answer
            return {"specific": self.specific, "why": "it names a run's file" if self.specific else "", "cost": 0.02,
                    "model": "a-model"}
        if route.endswith("/publish"):
            pid = route.split("/")[3]
            return {"ref": f"refs/proofpane/skill-proposals/{pid}", "head": self.heads.get(pid)}
        if route.endswith("/close"):
            return {"id": route.split("/")[3], "state": body["state"], "ref_removed": True}
        if route.endswith("/accept"):
            return dict({"merged": True, "head": self.heads.get(route.split("/")[3]), "hooks_ran": False},
                        **({"builds": self.later_builds} if self.later_builds is not None else {}))
        if route.endswith("/pull-request"):
            return {"url": "https://github.com/octo/demo/pull/1", "branch": "proofpane/skill-release-notes-x"}
        if route.endswith("/merge"):
            return {"head": "c" * 40, "version": "d" * 40, "version_read": True, "first_parent_was_head": True,
                    "changed_to_blob": True, "brought_besides": [], "merged": True}
        if route.endswith("/take"):
            return {"id": route.split("/")[3], "ended": "taken"}
        if route.endswith("/remake"):           # the same text on the branch's head, where another path moved
            pid = route.split("/")[3]
            self.repo.write("README.md", f"demo {time.time_ns()}\n")
            base = self.repo.commit("another path moved")
            head = self.repo.propose(self.reply["text"], base=base)
            self.heads[pid], self.bases[pid] = head, base
            for p in self.proposals if self.step_five else ():     # the record gains the new base and commit
                if p["id"] == pid:
                    p["bases"].append(base)
                    p["commits"].append(head)
                    self._change()
            return {"object_format": self.repo.fmt, "base": base, "head": head, "path": PATH,
                    "objects": self.repo.objects(base, head)}
        if route.endswith("/go-back"):
            return self.go_back(route.split("/")[3], body)
        if route == "/skill-improve/branches":
            return dict({"repo": "demo", "ok": True, "branch": "refs/heads/main", "head": self.repo.base,
                         "proposals": self.seen, "bars_now": None, "bars_at": self.bars_at},
                        **({"builds": self.later_builds} if self.later_builds is not None else {}))
        raise AssertionError(f"the app asked the workspace {method} {path}")

    def review(self, body):
        if self.reply is None:
            return {"cost": 0.05, "model": "a-model", "builds": self.builds, "proposal": None}
        text = self.reply["text"]
        pid = body["id"]
        self.proposals.append({"id": pid, "kind": "change", "path": PATH, "branch": "refs/heads/main",
                               "bases": [self.repo.base], "commits": [], "blob": blob_id(text, self.repo.fmt),
                               "fresh": True, "state": "critic", "ended": None})
        self._change()
        return {"cost": 0.05, "model": "a-model", "builds": self.builds, "proposal": {
            "id": pid, "repo": "demo", "folder": "release-notes", "path": PATH, "kind": "change",
            "branch": "refs/heads/main", "base": self.repo.base, "object_format": self.repo.fmt, "mode": "100644",
            "linked": True, "url": None, "current_version": self.repo.base, "before_version": None,
            "current_blob": blob_id(CURRENT, self.repo.fmt), "blob": blob_id(text, self.repo.fmt), "fresh": True,
            "current_text": CURRENT, "proposed_text": text, "hypothesis": self.reply["hypothesis"], "notes": [],
            "names": ["release-notes"]}}

    def go_back(self, found, body):
        """A proposal to go back, on the branch's head then: the found or merged proposal's commit."""
        pid = body["id"]
        base = self.bases[pid] = self.heads[found]
        self.proposals.append({"id": pid, "kind": "back", "path": PATH, "branch": "refs/heads/main",
                               "bases": [base], "commits": [], "blob": blob_id(body["text"]),
                               "fresh": False, "state": "critic", "ended": None, "for": found})
        self._change()
        return {"proposal": {"id": pid, "repo": "demo", "folder": "release-notes", "path": PATH, "kind": "back",
                             "for": found, "branch": "refs/heads/main", "base": base,
                             "object_format": self.repo.fmt, "mode": "100644",
                             "current_blob": blob_id(PROPOSED), "blob": blob_id(body["text"]), "fresh": False,
                             "current_text": PROPOSED, "proposed_text": body["text"], "notes": []}}

    def paths(self):
        return [(m, p.split("?")[0]) for m, p, _ in self.calls]


@pytest.fixture
def ws(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    w = Workspace(Repo(tmp_path / "demo"))
    monkeypatch.setattr(workspace_client, "call", w.call)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace.test")
    si._tried.clear()
    si._looked.clear()
    return w


@pytest.fixture
def owner(client, ws):
    h = signed_up(client)
    return h, auth.user(client.get("/api/auth/me", headers=h).json()["id"])


def calls_row(user_id, n, at, failed=False, version=None, path="claude", task=None, skill="release-notes",
              kind="business", repo="demo"):
    with store.connect() as db:
        db.execute("INSERT INTO ade_skill_calls (user_id, line_id, at, skill, kind, path, failed, version, session_id, "
                   "repo, task_id, taken_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (user_id, f"{n:016x}", at, skill, kind, path, None if path == "typed" else int(failed), version,
                    "0123456789ab", repo, task, at))


def task_row(user_id, task_id, state, checks=None):
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_controller_tasks (id, user_id, state, started_at, created_at, updated_at, checks) "
                   "VALUES (?,?,?,?,?,?,?)", (task_id, user_id, state, now, now, now,
                                              json.dumps(checks) if checks is not None else None))


def switched_on(client, h):
    r = client.put("/api/ade/skill-improve/repos/demo/switch", headers=h, json={"on": True})
    assert r.status_code == 200, r.text
    return r.json()


def calls_with_failures(user_id, version, n=12, failed=3, start=1):
    now = int(time.time())
    for i in range(n):
        calls_row(user_id, start + i, now - 3600 + i, failed=i < failed, version=version)


def proposals(user_id):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_skill_proposals WHERE user_id = ? ORDER BY created_at, "
                                            "id", (user_id,))]


def sessions(user_id):
    with store.connect() as db:
        return [(r["kind"], r["proposal_id"]) for r in db.execute("SELECT kind, proposal_id FROM ade_improve_sessions "
                                                                  "WHERE user_id = ? ORDER BY at, id", (user_id,))]


# ---- the tables ---------------------------------------------------------------------------------------------------

def test_the_tables_are_made_with_these_columns(client):
    with store.connect() as db:
        columns = {t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
                   for t in ("ade_skill_proposals", "ade_skill_improve_repos", "ade_skill_records")}
    assert columns["ade_skill_proposals"] == [
        "id", "user_id", "repo", "folder", "path", "kind", "base", "head", "branch", "object_format", "mode",
        "current_version", "current_blob", "blob", "fresh", "current_text", "proposed_text", "hypothesis", "counts",
        "notes", "state", "reason", "pr_url", "found", "rolls_back", "heads", "ended", "ended_at", "created_at",
        "decided_at", "merged_at", "watched_at"]
    assert columns["ade_skill_improve_repos"] == ["user_id", "repo", "turned_on", "account", "model", "url", "branch",
                                                  "changed_at", "reviewed"]
    assert columns["ade_skill_records"] == ["user_id", "generation", "record", "read_at"]


# ---- the counts and their bar -------------------------------------------------------------------------------------

def test_the_counts_by_version_and_a_task_at_both_versions_counted_at_each_and_once_in_the_whole(client, owner):
    h, user = owner
    uid = user["id"]
    now = int(time.time())
    old, new = "a" * 40, "b" * 40
    task_row(uid, "t-both", "failed", {"round": 1, "passed": False})
    task_row(uid, "t-done", "done", {"round": 1, "passed": True})
    calls_row(uid, 1, now - 50, failed=True, version=old, task="t-both")
    calls_row(uid, 2, now - 40, failed=False, version=new, task="t-both")
    calls_row(uid, 3, now - 30, failed=False, version=new, task="t-done")
    calls_row(uid, 4, now - 20, path="typed", version=new)
    calls_row(uid, 5, now - 10, failed=True, version=new, task="t-gone")
    calls_row(uid, 6, now - 5, failed=True, version=new, skill="another")      # another folder's name
    with store.connect() as db:
        got = si.counts(db, uid, "demo", ["release-notes"], new, old)
        none = si.counts(db, uid, "demo", ["release-notes"], None, None)
    assert got["whole"] == {"calls": 5, "typed": 1, "failed": 2, "tasks_done": 1, "tasks_failed": 1,
                            "tasks_stopped": 0, "tasks_open": 0, "tasks_unknown": 1, "tasks_checks_failed": 1}
    assert got["current"] == {"calls": 4, "typed": 1, "failed": 1, "tasks_done": 1, "tasks_failed": 1,
                              "tasks_stopped": 0, "tasks_open": 0, "tasks_unknown": 1, "tasks_checks_failed": 1}
    assert got["before"] == {"calls": 1, "typed": 0, "failed": 1, "tasks_done": 0, "tasks_failed": 1,
                             "tasks_stopped": 0, "tasks_open": 0, "tasks_unknown": 0, "tasks_checks_failed": 1}
    assert none["current"] is None and none["before"] is None
    assert shapes.check_skill_counts(got) == got


def test_a_call_counted_other_31_to_89_days_before_bars_the_folder(client, owner, ws):
    h, user = owner
    uid = user["id"]
    now = int(time.time())
    with store.connect() as db:
        assert si.other_named(db, uid, "demo", ["release-notes"]) is False
    calls_row(uid, 1, now - 89 * DAY, kind="other", path="claude")
    with store.connect() as db:
        assert si.other_named(db, uid, "demo", ["release-notes"]) is True
        assert si.other_named(db, uid, "elsewhere", ["release-notes"]) is False
    # a folder that would be reviewed (its failures as published() makes them) is not, for that one call
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base, start=10)
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    assert ("POST", "/skill-improve/reviews") not in ws.paths()


# ---- the schedule -------------------------------------------------------------------------------------------------

def test_a_skill_with_ten_calls_and_no_failure_is_not_a_candidate(client, owner, ws):
    h, user = owner
    switched_on(client, h)
    calls_with_failures(user["id"], ws.repo.base, n=10, failed=0)
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    assert ("POST", "/skill-improve/reviews") not in ws.paths()


def test_too_few_calls_with_an_outcome_start_no_review(client, owner, ws):
    h, user = owner
    switched_on(client, h)
    calls_with_failures(user["id"], ws.repo.base, n=9, failed=9)
    now = int(time.time())
    for i in range(5):
        calls_row(user["id"], 100 + i, now - 60 + i, path="typed", version=ws.repo.base)   # typed: no outcome
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    assert ("POST", "/skill-improve/reviews") not in ws.paths()


def test_a_critic_whose_reply_the_workspace_could_not_read_refuses_and_is_counted(client, owner, ws):
    # the workspace's answer for a critic's reply it could not read
    # carries its turn's cost, which the app counts, and does not clear the proposal
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    ws.critic_answer = {"specific": None, "why": "The critic gave no answer.", "cost": 0.03, "model": "a-model"}
    pid = si.maybe_skill_review(user, "http://workspace.test")
    (p,) = proposals(uid)
    assert p["state"] == "refused" and p["reason"] == "the critic: The critic gave no answer."
    with store.connect() as db:
        costs = {r["kind"]: r["cost_est_usd"] for r in db.execute("SELECT kind, cost_est_usd FROM ade_improve_sessions "
                                                                  "WHERE user_id = ? AND proposal_id = ?", (uid, pid))}
    assert costs == {"skill_review": 0.05, "skill_critic": 0.03}


def test_a_review_its_record_read_its_objects_checked_its_critic_and_its_publish(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    pid = si.maybe_skill_review(user, "http://workspace.test")
    (p,) = proposals(uid)
    assert p["id"] == pid and p["state"] == "open" and p["head"] == ws.heads[pid]
    assert (p["base"], p["current_blob"], p["blob"]) == (ws.repo.base, blob_id(CURRENT), blob_id(PROPOSED))
    # the review was given its counts and the current version; the record read before the objects were asked for, the
    # critic after them, and the publish last
    (review,) = [b for m, path, b in ws.calls if path == "/skill-improve/reviews"]
    assert review["folder"] == "release-notes" and review["current_version"] == ws.repo.base
    assert review["counts"]["whole"]["failed"] == 3 and shapes.check_skill_counts(review["counts"])
    order = [path for m, path, b in ws.calls if path.startswith("/skill-improve/") and m in ("GET", "POST")]
    review_at = order.index("/skill-improve/reviews")
    objects_at = order.index(f"/skill-improve/reviews/{pid}/objects")
    assert "/skill-improve/record" in order[review_at:objects_at]
    assert order.index(f"/skill-improve/reviews/{pid}/critic") > objects_at
    assert order[-2:] == [f"/skill-improve/proposals/{pid}/publish", "/skill-improve/record"]
    assert sorted(sessions(uid)) == [("skill_critic", pid), ("skill_review", pid)]      # at one second: not by order
    with store.connect() as db:
        copy = si._copy(db, uid)
        assert copy["generation"] == ws.generation
    # one a day, and the folder in its cooldown after it
    assert si.maybe_skill_review(user, "http://workspace.test", now=int(time.time()) + 10) is None


def test_the_budget_counts_both_kinds_and_a_spent_week_starts_no_review(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    now = int(time.time())
    with store.connect() as db:
        for _ in range(improve.BUDGET_SESSIONS):
            improve.session(db, uid, None, "skill_critic", now - 60)
        assert improve.budget(db, uid, now)["sessions"] == improve.BUDGET_SESSIONS
        assert improve.budget(db, uid, now)["spent"] is True
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    assert ("POST", "/skill-improve/reviews") not in ws.paths()


def test_no_review_while_a_business_proposal_waits_for_its_critic(client, owner, ws, monkeypatch):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    answering = [False]
    real = ws.call

    def critic_down(endpoint, method, path, body=None, timeout=30):
        if path.endswith("/critic") and not answering[0]:
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        return real(endpoint, method, path, body, timeout)

    monkeypatch.setattr(workspace_client, "call", critic_down)
    pid = si.maybe_skill_review(user, "http://workspace.test")
    assert proposals(uid)[0]["state"] == "critic"           # a critic that did not answer: asked again later
    si._tried.clear()
    with store.connect() as db:
        db.execute("DELETE FROM ade_improve_sessions")      # past the day's cap: the proposal waiting is what holds it
    reviews = len([1 for m, path, b in ws.calls if path == "/skill-improve/reviews"])
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    assert len([1 for m, path, b in ws.calls if path == "/skill-improve/reviews"]) == reviews
    answering[0] = True
    assert si.carry_on(user, "http://workspace.test", None, pid, int(time.time())) == "open"


def test_ties_go_to_the_folder_least_lately_reviewed(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    now = int(time.time())
    ws.candidates = [dict(ws.candidates[0], folder=f, path=f".claude/skills/{f}/SKILL.md", names=[f])
                     for f in ("aa", "bb")]
    for i, f in enumerate(("aa", "bb")):
        for j in range(12):
            calls_row(uid, 100 * i + j, now - 3600 + j, failed=j < 3, version=ws.repo.base, skill=f)
    with store.connect() as db:
        db.execute("INSERT INTO ade_skill_improve_repos (user_id, repo, turned_on, changed_at, reviewed) VALUES "
                   "(?,?,?,?,?) ON CONFLICT (user_id, repo) DO UPDATE SET reviewed = excluded.reviewed",
                   (uid, "demo", 1, now, json.dumps({"aa": now - 20 * DAY, "bb": now - 30 * DAY})))
    ws.reply = None
    si.maybe_skill_review(user, "http://workspace.test")
    (review,) = [b for m, path, b in ws.calls if path == "/skill-improve/reviews"]
    assert review["folder"] == "bb"


def test_a_claude_whose_version_cannot_be_read_and_a_repository_no_longer_listed_start_no_review(client, owner,
                                                                                                     ws):
    h, user = owner
    switched_on(client, h)
    calls_with_failures(user["id"], ws.repo.base)
    ws.builds = [dict(BUILDS[0], version=None, why="it did not answer")]
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    si._tried.clear()
    ws.builds, ws.listed = BUILDS, []
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    assert ("POST", "/skill-improve/reviews") not in ws.paths()


# ---- the app's checks of what it is sent --------------------------------------------------------------------------

def test_a_hypothesis_holding_a_construct_and_texts_that_do_not_hash_are_refused_by_the_app(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    ws.reply = {"text": PROPOSED, "hypothesis": "Name @alice in it."}
    si.maybe_skill_review(user, "http://workspace.test")
    (p,) = proposals(uid)
    assert p["state"] == "refused" and "a mention" in p["reason"]
    assert ("POST", f"/skill-improve/proposals/{p['id']}/close") in ws.paths()
    assert ("POST", f"/skill-improve/reviews/{p['id']}/objects") not in ws.paths()


def test_a_proposal_of_more_than_max_edits_is_pruned(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    body = "Write the notes.\n" + "".join(f"line {i}\n" for i in range(9))
    current = "---\nname: release-notes\ndescription: Write the release notes for a version.\n---\n" + body
    ws.candidates[0]["blob"] = blob_id(current)
    proposed = current.replace("line 1\n", "line one\n").replace("line 4\n", "line four\n").replace(
        "line 7\n", "line seven\n").replace("for a version.", "for a version, briefly.")
    real = ws.review

    def review(b):
        got = real(b)
        got["proposal"].update(current_text=current, current_blob=blob_id(current), proposed_text=proposed,
                               blob=blob_id(proposed))
        return got

    ws.review = review
    si.maybe_skill_review(user, "http://workspace.test")
    (p,) = proposals(uid)
    assert p["state"] == "pruned" and "more than 3 places" in p["reason"]


def test_a_proposal_the_record_the_app_read_does_not_hold_is_not_kept(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    ws.holds = False                    # a file a program wrote: never taken as the app's copy
    si.maybe_skill_review(user, "http://workspace.test")
    (p,) = proposals(uid)
    assert p["state"] == "refused" and p["reason"] == si.NOT_A_RECORD
    assert not any(path.endswith("/objects") for m, path, b in ws.calls)


def test_the_objects_are_checked_each_by_its_id_one_parent_one_path_and_the_same_mode(client, tmp_path):
    repo = Repo(tmp_path / "objs")
    head = repo.propose()
    row = {"object_format": "sha1", "path": PATH, "current_blob": blob_id(CURRENT), "blob": blob_id(PROPOSED),
           "current_text": CURRENT, "proposed_text": PROPOSED}
    sent = {"object_format": "sha1", "base": repo.base, "head": head, "path": PATH,
            "objects": repo.objects(repo.base, head)}
    assert si.check_objects(sent, row) == {"head": head, "base": repo.base, "mode": "100644"}

    def refused(sent_, match):
        with pytest.raises(si.SkillImproveError, match=match):
            si.check_objects(sent_, row)

    # an object whose bytes do not hash to its id
    spoiled = [dict(o, data=base64.b64encode(b"x" + base64.b64decode(o["data"])).decode()) if o["kind"] == "blob"
               else o for o in sent["objects"]]
    refused(dict(sent, objects=spoiled), "does not hash to its id")
    # a commit with a second parent
    git("checkout", "-q", "-b", "side", repo.base, cwd=repo.path)
    repo.write("x.txt", "x\n")
    other = repo.commit("another")
    git("checkout", "-q", "-b", "two", repo.base, cwd=repo.path)
    repo.write(PATH, PROPOSED)
    git("add", "-A", cwd=repo.path)
    tree = git("write-tree", cwd=repo.path).decode().strip()
    two = git("commit-tree", tree, "-p", repo.base, "-p", other, "-m", "two", cwd=repo.path).decode().strip()
    git("checkout", "-q", "main", cwd=repo.path)
    refused(dict(sent, head=two, objects=repo.objects(repo.base, two) + sent["objects"]), "exactly one parent")
    # another path changed beside the skill
    git("checkout", "-q", "-b", "wide", repo.base, cwd=repo.path)
    repo.write(PATH, PROPOSED)
    repo.write(".claude/skills/release-notes/run.sh", "echo\n")
    wide = repo.commit("wide")
    git("checkout", "-q", "main", cwd=repo.path)
    refused(dict(sent, head=wide, objects=repo.objects(repo.base, wide)), "differ elsewhere")
    # its mode changed
    git("checkout", "-q", "-b", "mode", repo.base, cwd=repo.path)
    repo.write(PATH, PROPOSED)
    os.chmod(os.path.join(repo.path, PATH), 0o755)
    moded = repo.commit("mode")
    git("checkout", "-q", "main", cwd=repo.path)
    refused(dict(sent, head=moded, objects=repo.objects(repo.base, moded)), "mode is not the same")
    # objects of another proposal than the one the app keeps
    with pytest.raises(si.SkillImproveError, match="blobs are not the proposal's"):
        si.check_objects(sent, dict(row, blob=blob_id("another\n")))


def test_the_objects_of_a_sha256_repository_are_checked_by_its_hash(client, tmp_path):
    repo = Repo(tmp_path / "objs256", fmt="sha256")
    head = repo.propose()
    row = {"object_format": "sha256", "path": PATH, "current_blob": blob_id(CURRENT, "sha256"),
           "blob": blob_id(PROPOSED, "sha256"), "current_text": CURRENT, "proposed_text": PROPOSED}
    sent = {"object_format": "sha256", "base": repo.base, "head": head, "path": PATH,
            "objects": repo.objects(repo.base, head)}
    assert si.check_objects(sent, row)["head"] == head
    with pytest.raises(si.SkillImproveError, match="object format"):
        si.check_objects(dict(sent, object_format="sha1"), row)


def test_a_proposal_the_critic_calls_specific_is_refused_and_never_published(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    ws.specific = True
    si.maybe_skill_review(user, "http://workspace.test")
    (p,) = proposals(uid)
    assert p["state"] == "refused" and p["reason"].startswith("the critic: it names a run's file")
    assert not any(path.endswith("/publish") for m, path, b in ws.calls)
    assert ("POST", f"/skill-improve/proposals/{p['id']}/close") in ws.paths()


def test_a_review_that_proposes_none_still_counts_its_session_and_its_cooldown(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    ws.reply = None
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    assert [k for k, _ in sessions(uid)] == ["skill_review"] and proposals(uid) == []
    with store.connect() as db:
        assert "release-notes" in json.loads(si._repo_row(db, uid, "demo")["reviewed"])


# ---- the look, the expiry and the switch --------------------------------------------------------------------------

def published(client, owner, ws):
    h, user = owner
    switched_on(client, h)
    calls_with_failures(user["id"], ws.repo.base)
    pid = si.maybe_skill_review(user, "http://workspace.test")
    assert proposals(user["id"])[0]["state"] == "open"
    return pid


def test_a_proposal_merged_by_a_session_is_found_not_the_owners_and_take_ends_it(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": "commit", "change_commit": ws.heads[pid],
                     "folder_unchanged": True, "ended": None, "pull_request": None}}
    ws.bars_at = {"branch": "refs/heads/side", "head": "e" * 40}
    before = int(time.time())
    got = client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()
    assert got["state"] == "found" and got["found"]["by"] == "commit" and got["ways"] == ["take", "go_back"]
    assert got["reason"] == "it reached the branch without the owner's tap" and got["ended"] is None
    # the branch and the commit the workspace read the bar at, which the
    # page names, not the proposal's branch
    assert got["branch"] == "refs/heads/main"
    # and when it was read
    at = got["bars_now"].pop("at")
    assert got["bars_now"] == {"refused": None, "head": "e" * 40, "branch": "refs/heads/side"}
    assert before <= at <= int(time.time())
    taken = client.post(f"/api/ade/skill-improve/proposals/{pid}/take", headers=h).json()
    assert taken["ended"] == "taken" and taken["found"]["version"] == ws.heads[pid]
    assert ("POST", f"/skill-improve/proposals/{pid}/take") in ws.paths()


def test_a_moved_ref_is_changed_and_a_folder_changed_on_the_branch_outdated(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    ws.seen = {pid: {"ref": "e" * 40, "ref_names_head": False, "reached": None, "change_commit": None,
                     "folder_unchanged": True, "ended": None, "pull_request": None}}
    assert client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()["state"] == "changed"
    with store.connect() as db:
        db.execute("UPDATE ade_skill_proposals SET state = 'open' WHERE id = ?", (pid,))
    ws.seen[pid].update(ref=ws.heads[pid], ref_names_head=True, folder_unchanged=False)
    got = client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()
    assert got["state"] == "outdated" and "a new review is the way" in got["reason"]
    r = client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h)
    assert r.status_code == 409 and not any(p.endswith("/accept") for _, p in ws.paths())


def test_a_pull_request_closed_on_github_without_a_merge_closes_the_proposal_as_rejected(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    with store.connect() as db:
        db.execute("UPDATE ade_skill_proposals SET notes = json_set(notes, '$.linked', json('false'), '$.url', "
                   "'git@github.com:octo/demo.git') WHERE id = ?", (pid,))
    opened = client.post(f"/api/ade/skill-improve/proposals/{pid}/pull-request", headers=h).json()
    assert opened["state"] == "pull_request" and opened["pr_url"] == "https://github.com/octo/demo/pull/1"
    ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": None, "change_commit": None,
                     "folder_unchanged": True, "ended": None, "pull_request": {"state": "closed", "merged": False}}}
    got = client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()
    assert got["state"] == "rejected" and "closed on GitHub" in got["reason"]


def test_the_builds_read_again_at_a_view_and_at_the_acceptance_are_kept_and_shown(client, owner, ws):
    # the builds read at each look and at the acceptance are kept beside
    # the review's, and the page is given the builds the checks were read on, not the ones the review found
    h, user = owner
    pid = published(client, owner, ws)
    ws.later_builds = [{"claude": "/bin/claude", "version": "2.1.311", "newer": True, "why": None}]
    ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": None, "change_commit": None,
                     "folder_unchanged": True, "ended": None, "pull_request": None}}
    viewed = client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()
    assert viewed["notes"]["builds_read"] == list(shapes.BUILDS_READ)
    reads = viewed["notes"]["reads"]
    assert [r["when"] for r in reads] == ["review", "view"]
    assert reads[0]["builds"] == BUILDS and reads[1]["builds"] == ws.later_builds and isinstance(reads[1]["at"], int)
    merged = client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h).json()
    assert [r["when"] for r in merged["notes"]["reads"]] == ["review", "view", "acceptance"]
    assert merged["notes"]["reads"][2]["builds"] == ws.later_builds


def test_merge_into_his_copy_and_on_github_record_the_owners_merge_and_the_version_after(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    got = client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h).json()
    assert (got["state"], got["ended"]) == ("merged", "merged") and got["found"] == {"version": ws.heads[pid]}
    # the commit the page shows sent, for the workspace to compare
    assert ("POST", f"/skill-improve/proposals/{pid}/accept", {"head": ws.heads[pid]}) in ws.calls
    assert client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h).status_code == 409


def test_a_merge_on_github_records_the_owners_merge_and_the_version_after(client, owner, ws):
    # the version after a merge on GitHub kept, which the watch reads
    h, user = owner
    pid = published(client, owner, ws)
    with store.connect() as db:
        db.execute("UPDATE ade_skill_proposals SET notes = json_set(notes, '$.linked', json('false'), '$.url', "
                   "'git@github.com:octo/demo.git') WHERE id = ?", (pid,))
    opened = client.post(f"/api/ade/skill-improve/proposals/{pid}/pull-request", headers=h).json()
    assert opened["state"] == "pull_request"
    got = client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h, json={"method": "squash"}).json()
    # the version the workspace read, the skill folder's last-changing
    # commit, never the head of origin's branch after the merge ("c" * 40 here)
    assert (got["state"], got["ended"]) == ("merged", "merged") and got["found"] == {"version": "d" * 40}
    assert ("POST", f"/skill-improve/proposals/{pid}/merge",
            {"url": "https://github.com/octo/demo/pull/1", "method": "squash", "head": ws.heads[pid]}) in ws.calls


def test_turning_the_switch_off_withdraws_what_is_on_its_way_and_starts_no_review(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    r = client.put("/api/ade/skill-improve/repos/demo/switch", headers=h, json={"on": False})
    assert r.status_code == 200 and r.json()["on"] is False
    (p,) = proposals(user["id"])
    assert p["state"] == "withdrawn"
    assert ("POST", f"/skill-improve/proposals/{pid}/close") in ws.paths()
    si._tried.clear()
    with store.connect() as db:
        db.execute("DELETE FROM ade_improve_sessions")
    assert si.maybe_skill_review(user, "http://workspace.test", now=int(time.time()) + 8 * DAY) is None


def test_a_proposal_on_its_way_expires_after_14_days(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    si.expire(user, "http://workspace.test", None, int(time.time()) + 13 * DAY)
    assert proposals(user["id"])[0]["state"] == "open"                 # 13 days: still on its way
    si.expire(user, "http://workspace.test", None, int(time.time()) + 15 * DAY)
    (p,) = proposals(user["id"])
    assert p["state"] == "expired" and p["reason"] == "not decided in 14 days"
    # one waiting for its critic, and one whose pull request is open, expire as one open for the owner does
    for state in ("critic", "pull_request"):
        with store.connect() as db:
            db.execute("UPDATE ade_skill_proposals SET state = ?, reason = NULL WHERE id = ?", (state, pid))
        si.expire(user, "http://workspace.test", None, int(time.time()) + 15 * DAY)
        assert proposals(user["id"])[0]["state"] == "expired", state


def test_reject_with_a_reason_removes_its_ref_and_its_bar_goes_on(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    got = client.post(f"/api/ade/skill-improve/proposals/{pid}/reject", headers=h, json={"reason": "too terse"}).json()
    assert (got["state"], got["reason"], got["ended"]) == ("rejected", "too terse", None)
    assert ("POST", f"/skill-improve/proposals/{pid}/close") in ws.paths()


# ---- the watch and its proposal to go back ------------------------------------------------------------------------

def test_a_drop_after_a_merge_makes_a_proposal_to_go_back_for_the_owners_tap_watched_once(client, owner, ws):
    h, user = owner
    uid = user["id"]
    pid = published(client, owner, ws)
    client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h)
    new = ws.heads[pid]
    now = int(time.time())
    for i in range(20):                                     # the replaced version: 20 passes before
        calls_row(uid, 500 + i, now - 7200 + i, failed=False, version=ws.repo.base)
    for i in range(20):                                     # the new one: 12 failures in its first 20
        calls_row(uid, 600 + i, now + i, failed=i < 12, version=new)
    si.step(user, "http://workspace.test", now=now + 100)
    rows = {p["id"]: p for p in proposals(uid)}
    assert rows[pid]["watched_at"] == now + 100
    (back,) = [p for p in rows.values() if p["kind"] == "back"]
    assert back["rolls_back"] == pid and back["proposed_text"] == CURRENT and back["state"] == "open"
    assert back["hypothesis"].startswith("Go back: its first 20 calls")
    assert not any(path.endswith(f"/reviews/{back['id']}/critic") for m, path, b in ws.calls)
    si.step(user, "http://workspace.test", now=now + 200)
    assert len([p for p in proposals(uid) if p["kind"] == "back"]) == 1


def test_the_owners_go_back_on_a_found_proposal(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": "content", "change_commit": "f" * 40,
                     "folder_unchanged": True, "ended": None, "pull_request": None}}
    client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h)
    got = client.post(f"/api/ade/skill-improve/proposals/{pid}/go-back", headers=h).json()
    assert got["kind"] == "back" and got["rolls_back"] == pid and got["state"] == "open"
    (body,) = [b for m, path, b in ws.calls if path.endswith("/go-back")]
    assert body["text"] == CURRENT


# ---- the record and the app's word --------------------------------------------------------------------------------

def test_the_app_takes_a_copy_only_where_its_signature_holds_and_above_its_generation(client, owner, ws):
    h, user = owner
    uid = user["id"]
    ws.generation = 3
    si.read_record(user, "http://workspace.test")
    with store.connect() as db:
        assert si._copy(db, uid)["generation"] == 3
    ws.generation, ws.holds = 5, False
    si.read_record(user, "http://workspace.test")
    with store.connect() as db:
        assert si._copy(db, uid)["generation"] == 3          # a file whose signature does not hold: never the copy
    ws.generation, ws.holds = 2, True
    si.read_record(user, "http://workspace.test")
    with store.connect() as db:
        assert si._copy(db, uid)["generation"] == 3          # a file below the copy's generation: never the copy


def test_the_word_is_the_copy_and_its_generation_and_never_raises(client, owner, ws, capsys):
    h, user = owner
    ws.generation = 4
    assert si.send_word(user, "http://workspace.test") is True
    assert ws.words[-1]["generation"] == 4 and ws.words[-1]["record"] == ws.file()
    ws.record_route = False                                  # an older image, with no bar to wait on
    assert si.send_word(user, "http://workspace.test") is False
    ws.record_route = True
    ws.fail["/skill-improve/record"] = workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
    assert si.send_word(user, "http://workspace.test") is False
    assert "was not sent" in capsys.readouterr().err


def test_the_word_comes_first_in_the_mirror_and_in_the_controllers_sync(client, owner, ws):
    h, user = owner
    ws.calls.clear()
    with pytest.raises(AssertionError, match="facts-versions"):
        agents._mirror(user, "http://workspace.test")          # the stand-in answers none of the mirror's own calls
    assert ws.paths()[:2] == [("GET", "/skill-improve/record"), ("PUT", "/skill-improve/record")]
    ws.calls.clear()
    seen = []
    controller.sync(user, "http://workspace.test", call=lambda e, m, p, b=None, timeout=30: (
        seen.append((m, p.split("?")[0])), ws.call(e, m, p, b, timeout))[1] if p.startswith("/skill-improve/") else
        (_ for _ in ()).throw(workspace_client.NotAnswering(502, "no")))
    assert seen[:2] == [("GET", "/skill-improve/record"), ("PUT", "/skill-improve/record")]


def test_the_sender_sends_the_word_only_where_the_workspace_has_not_had_it(client, owner, ws, monkeypatch):
    h, user = owner
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES "
                   "(?,?,?,?,?,?)", ("a1a2a3a4a5a6", user["id"], "Triage", "hello", 1, 1))
    ws.word = False
    for t in si.word_round():
        t.join(10)
    assert ws.paths() == [("GET", "/skill-improve/record"), ("PUT", "/skill-improve/record")]
    ws.calls.clear()
    for t in si.word_round():
        t.join(10)
    assert ws.paths() == [("GET", "/skill-improve/record")]          # had it: read, no word
    # an owner whose last send has not ended is passed over
    with si._send_lock:
        si._sending.add(user["id"])
    try:
        ws.calls.clear()
        assert si.word_round() == [] and ws.calls == []
    finally:
        with si._send_lock:
            si._sending.discard(user["id"])


def test_the_sender_reaches_an_owner_with_a_controller_and_no_agent(client, owner, ws, monkeypatch):
    # an owner whose workspace the controller polls, with no agent, has
    # the word too; else each controller start there waits WORD_WAIT and is refused as waiting for the app
    h, user = owner
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agents WHERE user_id = ?", (user["id"],)).fetchone()[0] == 0
    monkeypatch.setattr(controller, "users_to_poll", lambda db: [user["id"]])
    ws.word = False
    for t in si.word_round():
        t.join(10)
    assert ws.paths() == [("GET", "/skill-improve/record"), ("PUT", "/skill-improve/record")]


def test_one_owners_slow_workspace_does_not_delay_anothers_word(client, owner, ws, monkeypatch):
    """Each owner's word in a thread of its own, so that a workspace that does not answer for one owner
    holds no other's word back."""
    import threading
    h, user = owner
    other = auth.user(client.get("/api/auth/me", headers=signed_up(client, email="other@example.com")).json()["id"])
    with store.connect() as db:
        for n, u in enumerate((user, other)):
            db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES "
                       "(?,?,?,?,?,?)", (f"a1a2a3a4a5a{n}", u["id"], "Triage", "hello", 1, 1))
    monkeypatch.setattr(agents, "_endpoint", lambda u: "http://slow.test" if u["id"] == user["id"] else "http://quick.test")
    release, words = threading.Event(), []

    def call(endpoint, method, path, body=None, timeout=30):
        if endpoint == "http://slow.test":
            release.wait(20)                                # a workspace that does not answer, for this owner
        if method == "PUT":
            words.append(endpoint)
        return ws.call(endpoint, method, path, body, timeout)

    monkeypatch.setattr(workspace_client, "call", call)
    ws.word = False
    threads = si.word_round()
    try:
        assert len(threads) == 2
        deadline = time.time() + 10
        while "http://quick.test" not in words and time.time() < deadline:
            time.sleep(0.01)
        assert words == ["http://quick.test"]                # the other's word sent while the slow one still waits
        ws.word = False                                     # the stand-in is one workspace for both owners
    finally:
        release.set()
        for t in threads:
            t.join(10)
    assert sorted(words) == ["http://quick.test", "http://slow.test"]


def test_restore_from_the_apps_copy(client, owner, ws):
    h, user = owner
    ws.generation = 6
    si.read_record(user, "http://workspace.test")
    got = client.post("/api/ade/skill-improve/record/restore", headers=h)
    assert got.status_code == 200 and got.json() == {"generation": 7}
    (body,) = [b for m, path, b in ws.calls if path == "/skill-improve/record/restore"]
    assert json.loads(base64.b64decode(body["record"]))["record"]["generation"] == 6        # the app's copy, sent back


# ---- the owners the round reaches, the page's views and the routes ------------------------------------------------

def test_the_round_starts_the_loops_thread_for_an_owner_with_a_switch_and_no_agent(client, owner, ws, monkeypatch):
    h, user = owner
    switched_on(client, h)
    with store.connect() as db:
        assert user["id"] in si.users_to_poll(db)
    started = []
    monkeypatch.setattr(agents, "_slow_work", lambda u, e: started.append(u["id"]))
    monkeypatch.setattr(controller, "users_to_poll", lambda db: [])
    from proofpane_open.ade import skill_counts, task_offers
    monkeypatch.setattr(skill_counts, "read", lambda u: {"state": "read", "taken": 0})
    # the teammates' round is not this test's: its thread, which the round's forget wakes, would outlive the test and
    # open the next test's database, as test_task_offers keeps it out
    monkeypatch.setattr(task_offers, "_kick", lambda: None)
    agents.poll_all()
    assert started == [user["id"]]


def test_the_listing_and_the_whole_diff_and_another_owners_proposal_unseen(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    listing = client.get("/api/ade/skill-improve", headers=h).json()
    (p,) = listing["proposals"]
    assert p["id"] == pid and p["ways"] == ["merge", "remake", "reject"] and p["expires_at"] is not None
    assert "-description: Write the release notes for a version." in p["diff"]
    assert listing["budget"]["by_kind"]["skill_review"] == 1 and listing["budget"]["by_kind"]["skill_critic"] == 1
    assert listing["switches"][0]["on"] is True
    long_ = "---\nname: x\ndescription: y.\n---\n" + "".join(f"line {i}\n" for i in range(300))
    assert len(si.diff(long_, long_.replace("line 0\n", "zero\n").replace("line 299\n", "last\n"))) > 8
    assert len(si.diff("", "".join(f"l{i}\n" for i in range(400)))) == 401          # no cut at 200 lines
    # another owner's: none of his, at the service and at the routes (where he has no workspace, refused before it)
    someone = {"id": "f" * 32}
    with pytest.raises(si.SkillImproveError) as e:
        si.proposal(someone, pid, look_now=False)
    assert e.value.status == 404
    with pytest.raises(si.SkillImproveError) as e:
        si.merge(someone, pid)
    assert e.value.status == 404
    assert si.listing(someone)["proposals"] == []
    other = signed_up(client, email="other@example.com")
    assert client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=other).status_code in (403, 404)


# ---- the app's copy, restores, refused reviews, merges and the watch ----------------------------------------------

def test_a_file_the_workspace_does_not_believe_never_becomes_the_apps_copy(client, owner, ws):
    h, user = owner
    uid = user["id"]
    ws.generation = 3
    for state in ("waiting", "held"):
        ws.view_state = state
        si.read_record(user, "http://workspace.test")
        with store.connect() as db:
            assert si._copy(db, uid) is None, state
    ws.view_state = "record"
    si.read_record(user, "http://workspace.test")
    with store.connect() as db:
        assert si._copy(db, uid)["generation"] == 3


def test_a_commit_is_published_only_once_the_apps_copy_holds_it(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    ws.step_five = False                    # the record shown to the app never gains the commit
    pid = si.maybe_skill_review(user, "http://workspace.test")
    (p,) = proposals(uid)
    assert p["id"] == pid and p["state"] == "refused" and p["reason"] == si.NOT_A_RECORD + " with its commit"
    assert ("POST", f"/skill-improve/proposals/{pid}/publish") not in ws.paths()
    assert ("POST", f"/skill-improve/proposals/{pid}/close") in ws.paths()


def test_a_restore_gives_the_workspace_each_commit_the_app_checked(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    pid = si.maybe_skill_review(user, "http://workspace.test")
    copy = si._word_body(uid)["record"]
    r = client.post("/api/ade/skill-improve/record/restore", headers=h)
    assert r.status_code == 200, r.text
    assert ws.restored == {"record": copy, "commits": {pid: [ws.heads[pid]]}, "ends": {}}


def test_objects_on_another_base_than_the_proposals_are_refused(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    # the workspace's objects on another commit than the proposal's base, holding the same SKILL.md, every other
    # check of the objects holding
    ws.repo.write("README.md", "demo, moved on\n")
    other = ws.repo.commit("moved on")
    real = ws.review

    def review(body):
        got = real(body)
        ws.bases[body["id"]] = other
        return got

    ws.review = review
    pid = si.maybe_skill_review(user, "http://workspace.test")
    (p,) = proposals(uid)
    assert p["state"] == "refused" and "their base is not the proposal's" in p["reason"]
    assert ("POST", f"/skill-improve/proposals/{pid}/publish") not in ws.paths()


def test_a_review_whose_answer_does_not_come_back_is_counted_and_kept_refused(client, owner, ws):
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    ws.fail["/skill-improve/reviews"] = workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
    now = int(time.time())
    assert si.maybe_skill_review(user, "http://workspace.test", now=now) is None
    (p,) = proposals(uid)
    assert p["state"] == "refused" and p["reason"].startswith("its review's answer did not come back")
    assert sessions(uid) == [("skill_review", p["id"])]
    assert ("POST", f"/skill-improve/proposals/{p['id']}/close") in ws.paths()
    del ws.fail["/skill-improve/reviews"]
    si._tried.clear()
    assert si.maybe_skill_review(user, "http://workspace.test", now=now + 60) is None      # the day's review is spent
    assert [path for m, path in ws.paths()].count("/skill-improve/reviews") == 1


def test_a_review_the_workspace_refuses_is_kept_refused_in_its_words(client, owner, ws):
    # a branch longer than a look reads gets no review, and the page says
    # why among what became of the others, a refusal said as one
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    why = "No review starts: the branch's history is longer than a look reads."
    ws.fail["/skill-improve/reviews"] = workspace_client.WorkspaceCallError(400, why)
    assert si.maybe_skill_review(user, "http://workspace.test") is None
    (p,) = proposals(uid)
    assert (p["state"], p["reason"]) == ("refused", f"the workspace refused its review: {why}")
    assert sessions(uid) == [("skill_review", p["id"])]


def test_a_merge_the_workspace_made_whose_answer_was_lost_is_the_owners_not_found(client, owner, ws):
    h, user = owner
    pid = published(client, owner, ws)
    ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": "commit", "change_commit": ws.heads[pid],
                     "folder_unchanged": True, "ended": {"how": "merged", "at": 5}, "pull_request": None}}
    got = client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()
    assert (got["state"], got["ended"], got["found"]) == ("merged", "merged", {"version": ws.heads[pid]})
    assert "take" not in got["ways"]


def test_a_look_does_not_undo_a_merge_made_while_it_ran(client, owner, ws, monkeypatch):
    h, user = owner
    pid = published(client, owner, ws)
    ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": "commit", "change_commit": ws.heads[pid],
                     "folder_unchanged": True, "ended": None, "pull_request": None}}
    real = si._set

    def racing(user_id, pid_, now, was=None, **fields):
        if fields.get("state") == "found":       # the owner's merge lands between the look's read and its write
            real(user_id, pid_, now, state="merged", ended="merged", ended_at=now, merged_at=now,
                 found=json.dumps({"version": "c" * 40}))
        return real(user_id, pid_, now, was=was, **fields)

    monkeypatch.setattr(si, "_set", racing)
    si.look(user, "http://workspace.test", None, "demo")
    (p,) = proposals(user["id"])
    assert (p["state"], json.loads(p["found"])) == ("merged", {"version": "c" * 40})


def test_the_watch_waits_while_the_repositorys_switch_is_off(client, owner, ws):
    h, user = owner
    uid = user["id"]
    pid = published(client, owner, ws)
    client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h)
    new = ws.heads[pid]
    now = int(time.time())
    for i in range(20):
        calls_row(uid, 500 + i, now - 7200 + i, failed=False, version=ws.repo.base)
    for i in range(20):
        calls_row(uid, 600 + i, now + i, failed=i < 12, version=new)
    assert client.put("/api/ade/skill-improve/repos/demo/switch", headers=h, json={"on": False}).status_code == 200
    si.step(user, "http://workspace.test", now=now + 100)
    rows = {p["id"]: p for p in proposals(uid)}
    assert rows[pid]["watched_at"] is None and not [p for p in rows.values() if p["kind"] == "back"]
    switched_on(client, h)
    si.step(user, "http://workspace.test", now=now + 200)
    rows = {p["id"]: p for p in proposals(uid)}
    assert rows[pid]["watched_at"] == now + 200 and len([p for p in rows.values() if p["kind"] == "back"]) == 1


def test_a_pull_request_is_offered_only_for_an_origin_on_github():
    row = {"state": "open", "ended": None, "notes": json.dumps({"linked": False,
                                                               "url": "https://example.org/github.com/demo.git"})}
    assert si._ways(row) == ["reject"]
    row["notes"] = json.dumps({"linked": False, "url": "git@github.com:octo/demo.git"})
    assert si._ways(row) == ["pull_request", "reject"]


# ---- restores, remakes, pull requests on GitHub and the critic's budget -------------------------------------------

def test_a_restore_gives_the_workspace_each_end_the_app_recorded(client, owner, ws):
    # the owner's merge, sent with the restore, so that a copy read before it does not undo it
    h, user = owner
    pid = published(client, owner, ws)
    r = client.post("/api/ade/skill-improve/record/restore", headers=h)
    assert r.status_code == 200, r.text
    assert ws.restored["ends"] == {}                                # on its way: no end
    r = client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h)
    assert r.status_code == 200, r.text
    with store.connect() as db:
        row = db.execute("SELECT ended, ended_at FROM ade_skill_proposals WHERE id = ?", (pid,)).fetchone()
    assert row["ended"] == "merged"
    r = client.post("/api/ade/skill-improve/record/restore", headers=h)
    assert r.status_code == 200, r.text
    assert ws.restored["ends"] == {pid: {"how": "merged", "at": row["ended_at"]}}
    assert ws.restored["commits"][pid] == [ws.heads[pid]]


@pytest.mark.parametrize("how", ["take", "look"])
def test_a_restore_gives_the_workspace_a_take_and_an_end_the_workspace_gave_at_a_look(client, owner, ws, how):
    # every end the app recorded, not the owner's merges alone: his take, and an end the workspace gave at a
    # look, its tap's answer lost
    h, user = owner
    pid = published(client, owner, ws)
    if how == "take":
        ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": "commit", "change_commit": ws.heads[pid],
                         "folder_unchanged": True, "ended": None, "pull_request": None}}
        assert client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()["state"] == "found"
        assert client.post(f"/api/ade/skill-improve/proposals/{pid}/take", headers=h).json()["ended"] == "taken"
        want = "taken"
    else:
        ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": None, "change_commit": None,
                         "folder_unchanged": True, "ended": {"how": "back", "at": 1_790_000_000}, "pull_request": None}}
        client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h)
        want = "back"
    row = _row(pid)
    assert row["ended"] == want
    r = client.post("/api/ade/skill-improve/record/restore", headers=h)
    assert r.status_code == 200, r.text
    assert ws.restored["ends"] == {pid: {"how": want, "at": row["ended_at"]}}


def _row(pid):
    with store.connect() as db:
        return db.execute("SELECT * FROM ade_skill_proposals WHERE id = ?", (pid,)).fetchone()


def test_a_remake_moves_the_proposal_to_its_new_commit_once_published(client, owner, ws):
    # the row moves to the new commit once it is published, the commit before kept among its heads
    h, user = owner
    pid = published(client, owner, ws)
    before = _row(pid)["head"]
    r = client.post(f"/api/ade/skill-improve/proposals/{pid}/remake", headers=h)
    assert r.status_code == 200, r.text
    row = _row(pid)
    assert row["head"] == ws.heads[pid] != before and row["state"] == "open"
    assert [x["head"] for x in json.loads(row["heads"])] == [before]
    assert si.checked_commits(user["id"])[pid] == [before, ws.heads[pid]]


@pytest.mark.parametrize("how", ["copy_lacks_it", "publish_fails"])
def test_a_remake_not_published_leaves_the_row_and_its_ref_on_the_commit_before(client, owner, ws, how):
    # where the app's copy does not hold the new commit, or the publish does not go through, no publish is
    # taken as made: the row keeps the commit its ref names, is outdated with why, the new commit given back by a
    # restore; and a look does not call it changed
    h, user = owner
    uid = user["id"]
    pid = published(client, owner, ws)
    before = _row(pid)["head"]
    if how == "copy_lacks_it":
        ws.step_five = False
    else:
        ws.fail[f"/skill-improve/proposals/{pid}/publish"] = workspace_client.NotAnswering(502, "it did not answer")
    publishes = sum(1 for _, p in ws.paths() if p.endswith("/publish"))
    r = client.post(f"/api/ade/skill-improve/proposals/{pid}/remake", headers=h)
    assert r.status_code == 409 and "brought up to date, but not published" in r.json()["detail"]
    row = _row(pid)
    assert (row["head"], row["state"]) == (before, "outdated") and "bring it up to date again" in row["reason"]
    assert json.loads(row["heads"] or "[]") == []
    assert ws.heads[pid] in si.checked_commits(uid)[pid]
    if how == "copy_lacks_it":
        assert sum(1 for _, p in ws.paths() if p.endswith("/publish")) == publishes      # no publish asked for
    with store.connect() as db:
        db.execute("UPDATE ade_skill_proposals SET state = 'open' WHERE id = ?", (pid,))
    ws.seen = {pid: {"ref": before, "ref_names_head": False, "reached": None, "change_commit": None,
                     "folder_unchanged": True, "ended": None, "pull_request": None}}
    assert client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()["state"] == "open"


def test_a_pull_request_whose_ref_moved_stays_for_its_merge_on_github(client, owner, ws):
    # a merge on GitHub reads GitHub's view, not the shared repository's ref
    h, user = owner
    pid = published(client, owner, ws)
    with store.connect() as db:
        db.execute("UPDATE ade_skill_proposals SET notes = json_set(notes, '$.linked', json('false'), '$.url', "
                   "'git@github.com:octo/demo.git') WHERE id = ?", (pid,))
    client.post(f"/api/ade/skill-improve/proposals/{pid}/pull-request", headers=h)
    ws.seen = {pid: {"ref": "e" * 40, "ref_names_head": False, "reached": None, "change_commit": None,
                     "folder_unchanged": True, "ended": None, "pull_request": {"state": "open", "merged": False}}}
    got = client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()
    assert got["state"] == "pull_request" and "merge" in got["ways"]


def test_a_pull_request_closed_on_github_is_closed_in_the_workspace_too(client, owner, ws):
    # its ref removed and the record's state set, as any rejected proposal's
    h, user = owner
    pid = published(client, owner, ws)
    with store.connect() as db:
        db.execute("UPDATE ade_skill_proposals SET notes = json_set(notes, '$.linked', json('false'), '$.url', "
                   "'git@github.com:octo/demo.git') WHERE id = ?", (pid,))
    client.post(f"/api/ade/skill-improve/proposals/{pid}/pull-request", headers=h)
    ws.seen = {pid: {"ref": ws.heads[pid], "ref_names_head": True, "reached": None, "change_commit": None,
                     "folder_unchanged": True, "ended": None, "pull_request": {"state": "closed", "merged": False}}}
    assert client.get(f"/api/ade/skill-improve/proposals/{pid}", headers=h).json()["state"] == "rejected"
    closes = [b for m, p, b in ws.calls if p == f"/skill-improve/proposals/{pid}/close"]
    assert closes == [{"state": "rejected"}]


def test_no_critic_is_asked_while_the_weeks_budget_is_spent(client, owner, ws, monkeypatch):
    # a critic whose answer does not come back is asked again only while the week's
    # budget allows; with the budget spent the proposal waits in critic, asked nothing
    h, user = owner
    uid = user["id"]
    switched_on(client, h)
    calls_with_failures(uid, ws.repo.base)
    asked, real = [], ws.call

    def critic_down(endpoint, method, path, body=None, timeout=30):
        if path.endswith("/critic"):
            asked.append(path)
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        return real(endpoint, method, path, body, timeout)

    monkeypatch.setattr(workspace_client, "call", critic_down)
    pid = si.maybe_skill_review(user, "http://workspace.test")
    assert proposals(uid)[0]["state"] == "critic" and len(asked) == 1
    now = int(time.time())
    assert si.carry_on(user, "http://workspace.test", None, pid, now) == "critic" and len(asked) == 2
    with store.connect() as db:
        for _ in range(improve.BUDGET_SESSIONS):
            improve.session(db, uid, None, "review", now)
    for i in range(20):
        assert si.carry_on(user, "http://workspace.test", None, pid, now + 1 + i) == "critic"
    assert len(asked) == 2


@pytest.mark.parametrize("where", ["README.md", ".claude/settings.json", ".claude/skills/other/SKILL.md"])
def test_a_commit_that_also_changes_a_path_above_the_skills_folder_is_refused(client, tmp_path, where):
    # the two commits' trees are compared at each level of the path, the root's,
    # .claude's and .claude/skills', not at the skill's folder alone
    repo = Repo(tmp_path / "objs")
    row = {"object_format": "sha1", "path": PATH, "current_blob": blob_id(CURRENT), "blob": blob_id(PROPOSED),
           "current_text": CURRENT, "proposed_text": PROPOSED}
    git("checkout", "-q", "-b", "above", repo.base, cwd=repo.path)
    repo.write(PATH, PROPOSED)
    repo.write(where, "another file\n")
    above = repo.commit("above")
    git("checkout", "-q", "main", cwd=repo.path)
    sent = {"object_format": "sha1", "base": repo.base, "head": above, "path": PATH,
            "objects": repo.objects(repo.base, above)}
    with pytest.raises(si.SkillImproveError, match="differ elsewhere"):
        si.check_objects(sent, row)


# ---- the app's word, an owner's user record and the proposals' list and view --------------------------------------

def test_the_word_carries_the_commits_the_app_checked_and_the_ends_it_recorded(client, owner, ws):
    # the workspace's record must hold them beside the app's copy, so that the copy itself put back by a
    # session's program after a later write is held
    h, user = owner
    pid = published(client, owner, ws)
    assert si.send_word(user, "http://workspace.test") is True
    assert ws.words[-1]["commits"] == {pid: [ws.heads[pid]]} and ws.words[-1]["ends"] == {}
    assert client.post(f"/api/ade/skill-improve/proposals/{pid}/merge", headers=h).status_code == 200
    assert si.send_word(user, "http://workspace.test") is True
    with store.connect() as db:
        at = si._row(db, user["id"], pid)["ended_at"]
    assert ws.words[-1]["ends"] == {pid: {"how": "merged", "at": at}}
    assert ws.words[-1]["commits"] == {pid: [ws.heads[pid]]}


def test_an_owner_whose_user_record_cannot_be_read_is_tried_again_and_holds_no_other_back(client, owner, ws,
                                                                                               monkeypatch, capsys):
    # where the read of an owner's user record fails, his mark is taken off, so that the next round tries
    # him again, and the owners after him in that round are sent the word
    h, user = owner
    other = auth.user(client.get("/api/auth/me", headers=signed_up(client, email="other@example.com")).json()["id"])
    with store.connect() as db:
        for n, u in enumerate((user, other)):
            db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES "
                       "(?,?,?,?,?,?)", (f"a1a2a3a4a5a{n}", u["id"], "Triage", "hello", 1, 1))
    real, failed = auth.user, []

    def user_(user_id):
        if user_id == user["id"] and not failed:
            failed.append(user_id)
            raise RuntimeError("database is locked")
        return real(user_id)

    monkeypatch.setattr(auth, "user", user_)
    ws.word = False
    for t in si.word_round():
        t.join(10)
    assert failed == [user["id"]] and user["id"] not in si._sending
    assert "could not read" in capsys.readouterr().err
    assert ws.paths() == [("GET", "/skill-improve/record"), ("PUT", "/skill-improve/record")]     # the other's word
    ws.calls.clear()
    ws.word = False
    for t in si.word_round():
        t.join(10)
    assert ws.paths().count(("PUT", "/skill-improve/record")) == 2                              # both, this time


def test_the_list_keeps_every_proposal_for_the_owner_to_decide_whatever_its_age(client, owner, ws):
    # a found proposal, which bars until he takes it or goes back from it, is listed with its taps however
    # many proposals came after it; the others are the newest LIST_OTHERS
    h, user = owner
    pid = published(client, owner, ws)
    with store.connect() as db:
        db.execute("UPDATE ade_skill_proposals SET state = 'found', created_at = 1 WHERE id = ?", (pid,))
        row = dict(si._row(db, user["id"], pid))
        for n in range(si.LIST_OTHERS + 3):
            db.execute(f"INSERT INTO ade_skill_proposals ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       [f"{n:012x}" if k == "id" else "refused" if k == "state" else 10 + n if k == "created_at" else v
                        for k, v in row.items()])
    got = client.get("/api/ade/skill-improve", headers=h).json()["proposals"]
    assert len(got) == si.LIST_OTHERS + 1
    assert got[-1]["id"] == pid and got[-1]["ways"] == ["take", "go_back"]
    assert [p["id"] for p in got[:2]] == [f"{si.LIST_OTHERS + 2:012x}", f"{si.LIST_OTHERS + 1:012x}"]


def test_a_proposals_view_says_when_it_read_whether_a_start_is_refused(client, owner, ws):
    # the page views a repository once while it shows the list, so the view says when it read the bar
    h, user = owner
    pid = published(client, owner, ws)
    now = int(time.time())
    got = si.proposal(user, pid, call=ws.call, now=now)
    assert got["bars_now"]["at"] == now
