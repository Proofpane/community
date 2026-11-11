"""The controller's records, the app's side (ade/controller.py, ade/controller_routes.py): the tables, the owner's
controls, the second check of each action the workspace's record brings (a start outside the controller's bounds
kept and flagged, judged by what was in effect when it was made), the sync against a stand-in for the workspace's two
routes, the packet's form, the board's rows and the alerts, in fixed words."""

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time

import pytest

from conftest import signed_up
from proofpane_open import auth, store
from proofpane_open.ade import agents, controller, controller_routes, push, tasks, workspace_client
from proofpane_open.ade import task_shapes as shapes
from proofpane_core.canonical import canonicalize

C = "/api/ade/controller"
REAL_SYNC_SOON = controller_routes._sync_soon
REAL_CALL = workspace_client.call
# an hour ago, to the minute: recent enough for what is kept a day or a week
T0 = int(time.time()) // 60 * 60 - 3600
SESSION = "0123456789ab"
OTHER = "b" * 12


# ---- the stand-in workspace, and helpers ----------------------------------------------------------------------------

# what workspace_client raises for a 404 with no refusal in it: a workspace agent's answer to a path it does not serve
NO_ROUTE = workspace_client.WorkspaceCallError(502, "The workspace answered 404.")


class Workspace:
    """The workspace as the app's sync sees it: GET /controller/record serves the pages a test gave, each once, then an
    empty page; PUT /controller keeps the mirror and holds its version; a session's stop is kept. Every call is kept.
    `routes` False answers both routes as a workspace without the controller's routes does (a 404 with no refusal in it,
    which workspace_client gives as 502 in its own words), `read_route` or `put_route` False one of them alone; `fail`
    and `fail_put` a refusal or a failure of that status, as workspace_client gives it. As the contract has it, the
    empty page lists the sessions open by the pages served (`open`: those a page started or gave as open, until one
    gives them as ended). PUT /controller/managers, which a sync sends after the mirror where the managers' version is
    not the one the workspace holds, is held and answered with no attempt, and left out of `paths`: the read's and the
    mirror's calls, which these tests are of (test_controller_managers.py is the managers'). So is the app's word on the
    record of skill proposals, read and sent before any other call of a sync (skill_improve.send_word), answered as a
    workspace with no record yet answers it; `words` keeps its calls."""

    def __init__(self):
        self.calls, self.pages, self.mirrors, self.stopped, self.managers = [], [], [], [], []
        self.held, self.routes, self.read_route, self.put_route = None, True, True, True
        self.fail, self.fail_put, self.open, self.words = None, None, {}, []

    def _served(self, page):
        given = {s.get("id") for s in page.get("tasks") or [] if isinstance(s, dict)}
        for a in page.get("actions") or []:
            if isinstance(a, dict) and a.get("kind") == "start" and a.get("task") not in given:
                self.open.setdefault(a.get("task"), {"id": a.get("task"), "state": "starting"})
        for s in page.get("tasks") or []:
            if isinstance(s, dict):
                if s.get("state") in controller.TASK_ENDED:
                    self.open.pop(s.get("id"), None)
                else:
                    self.open[s.get("id")] = s
        return page

    def call(self, endpoint, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        if path == "/skill-improve/record":
            self.words.append(method)
            if method == "GET":
                return {"file": None, "holds": False, "generation": None, "word": False, "state": "waiting",
                        "held": None, "seen": 0, "refused": []}
            return {"word": True}
        if path.startswith("/controller"):
            if not (self.routes and (self.put_route if method == "PUT" else self.read_route)):
                raise workspace_client.WorkspaceCallError(NO_ROUTE.status, str(NO_ROUTE))
            if path.startswith("/controller/record?since="):
                if self.fail:
                    raise workspace_client.WorkspaceCallError(self.fail, "The workspace answered so.")
                since = int(path.split("=", 1)[1])
                page = self.pages.pop(0) if self.pages else {"seq": since, "more": False, "actions": [],
                                                             "tasks": list(self.open.values())}
                return dict({"held": self.held}, **self._served(page))
            if path == "/controller/managers" and method == "PUT":
                self.managers.append(body)
                return {"held": body["version"], "attempt": None}
            if path == "/controller" and method == "PUT":
                if self.fail_put:
                    raise workspace_client.WorkspaceCallError(self.fail_put, "The workspace answered so.")
                self.mirrors.append(body)
                self.held = body["version"]
                return {"held": body["version"]}
        stop = re.fullmatch(r"/sessions/([0-9a-f]{12})/stop", path)
        if stop:
            self.stopped.append(stop[1])
            return {"id": stop[1], "state": "stopped"}
        if path.startswith("/agents"):
            return {}
        if path.startswith("/skills?since="):
            # the poll's read of the workspace's skill calls (ade/skill_counts.py): a record with none
            return {"store": "5" * 32, "seq": 0, "more": False, "first": None, "dropped": 0, "lines": []}
        raise AssertionError(f"the app asked the workspace {method} {path}")

    def paths(self):
        return [p for _, p, _ in self.calls if p not in ("/controller/managers", "/skill-improve/record")]


@pytest.fixture
def ws(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    fake = Workspace()
    monkeypatch.setattr(workspace_client, "call", fake.call)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace.test")
    synced = []
    monkeypatch.setattr(controller_routes, "_sync_soon", lambda user: synced.append(user["id"]))
    fake.synced = synced
    return fake


@pytest.fixture
def owner(client, ws):
    h = signed_up(client)
    return h, auth.user(client.get("/api/auth/me", headers=h).json()["id"])


def own(user_id, account="default", state="confirmed"):
    """One of the workspace's accounts, as Connections leaves it once the owner confirmed it as theirs."""
    with store.connect() as db:
        db.execute("INSERT INTO ade_account_owners (id, user_id, account, state, confirmed_at, position) VALUES "
                   "(?,?,?,?,1,0)", (secrets.token_hex(16), user_id, account, state))


def setup(user, now=T0, **settings):
    """A controller on account `default`, confirmed, turned on at `now`, with `settings` changed first."""
    own(user["id"])
    controller.change_settings(user, dict({"account": "default"}, **settings), now=now)
    return controller.set_state(user, "on", now=now)


def uid():
    return secrets.token_hex(16)


def record(*actions, tasks_=None, session=None, seq=1, more=False):
    """A page of the record. `tasks` only when `tasks_` is given: a page without it says nothing of the sessions'
    states, and so ends none (_unlisted); a page with it lists, by the contract, every session open in the
    workspace."""
    out = {"seq": seq, "more": more, "actions": list(actions)}
    if tasks_ is not None:
        out["tasks"] = list(tasks_)
    if session is not None:
        out["session"] = session
    return out


def plan(request_id, words, *, rid=None, aid=None, title="Say hello", acceptance=None, repos=("demo",), at=T0 + 10):
    return {"id": aid or uid(), "kind": "plan", "at": at, "requirement": rid or uid(), "request": request_id,
            "title": title, "words": words, "acceptance": acceptance, "repos": list(repos)}


def start(requirement_id, *, tid=None, aid=None, session=SESSION, repo="demo", branch="pp/abc", base="main",
          account="default", program="claude", model="opus", effort="max", at=T0 + 20):
    return {"id": aid or uid(), "kind": "start", "at": at, "task": tid or uid(), "requirement": requirement_id,
            "session": session, "repo": repo, "branch": branch, "base": base, "account": account, "program": program,
            "model": model, "effort": effort}


def act(kind, task_id, *, aid=None, at=T0 + 30):
    return {"id": aid or uid(), "kind": kind, "at": at, "task": task_id}


def question(text, *, qid=None, aid=None, requirement=None, at=T0 + 30):
    return {"id": aid or uid(), "kind": "question", "at": at, "question": qid or uid(), "requirement": requirement,
            "text": text}


def take(user, *actions, now=T0 + 100, **kw):
    return controller.take(user["id"], record(*actions, **kw), now=now)


def events(user_id, kind=None):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_controller_events WHERE user_id = ? ORDER BY seq", (user_id,)).fetchall()
    return [dict(r, payload=json.loads(r["payload"])) for r in rows if kind is None or r["kind"] == kind]


def _row_of(table, row_id, user_id):
    """The row of `table` with that id: the owner's, or the only one when no owner is named (an id is an owner's
    own)."""
    with store.connect() as db:
        rows = db.execute(f"SELECT * FROM {table} WHERE id = ?" + (" AND user_id = ?" if user_id else ""),
                          (row_id, user_id) if user_id else (row_id,)).fetchall()
    assert len(rows) <= 1, f"{len(rows)} rows of {table} have that id: name the owner"
    return dict(rows[0]) if rows else None


def task_row(task_id, user_id=None):
    return _row_of("ade_controller_tasks", task_id, user_id)


def requirement_row(rid, user_id=None):
    return _row_of("ade_controller_requirements", rid, user_id)


# three paragraphs: a plan's words, and its acceptance, are whole paragraphs of the request
REQUEST = "Make the README say hello.\n\nits first line is hello.\n\nThanks\tagain"


def planned(user, now=T0 + 5, **kw):
    """A request, and a requirement planned from it and taken: (request, requirement id)."""
    request = controller.add_request(user, REQUEST, now=now)
    rid = uid()
    assert take(user, plan(request["id"], "Make the README say hello.", rid=rid, **kw))["taken"] == 1
    return request, rid


# ---- the tables ------------------------------------------------------------------------------------------------------

TABLES = {
    "ade_controllers": ("user_id", "state", "state_at", "account", "cap", "models", "efforts", "chat_model",
                        "chat_effort", "confirm_plans", "session_id", "end_all", "version", "mirrored_version", "seq",
                        "read_at", "read_behind_at", "listed_at", "read_error", "read_error_at", "mirror_error",
                        "mirror_error_at", "mirror_error_version", "mirror_error_version_at", "refused", "refused_at",
                        "packet", "packet_digest", "packet_at", "packet_error",
                        "packet_error_at", "created_at", "updated_at", "managers_version", "managers_sent",
                        "managers_held", "managers_error", "managers_error_at", "room", "room_at",
                        # a linked session that holds the Manager's role
                        "holder_link", "holder_payload", "holder_since", "holder_until", "holder_settings"),
    "ade_controller_requests": ("id", "user_id", "n", "text", "digest", "go_at", "planned_at", "withdrawn_at",
                                "created_at", "checks",
                                "instruction_seq"),     # a goal's signed instruction to a linked Manager
    "ade_controller_requirements": ("id", "user_id", "request_id", "title", "title_cut", "title_hidden", "words",
                                    "acceptance", "repos", "state", "hold", "confirm", "go_at", "planned_at",
                                    "closed_at",
                                    "created_at", "updated_at", "checks", "protected"),
    "ade_controller_tasks": ("id", "user_id", "requirement_id", "session_id", "repo", "branch", "base", "account",
                             "program", "model", "effort", "state", "waits_until", "started_at", "stopped_at",
                             "ended_at", "unlisted", "continues", "flag", "flagged_at", "created_at", "updated_at",
                             "checks", "why", "checking", "text", "text_cut", "text_hidden", "attempt_id",
                             "needs_owner", "changed",
                             "reported_by"),        # a linked Manager's own report of its task
    "ade_controller_questions": ("id", "user_id", "requirement_id", "text", "cut", "hidden", "state", "asked_at",
                                 "answered_at", "decision_id", "created_at"),
    "ade_controller_decisions": ("id", "user_id", "n", "question_id", "text", "at", "requirement_id"),
    "ade_controller_actions": ("user_id", "id", "kind", "taken", "at"),
    "ade_controller_events": ("seq", "user_id", "kind", "requirement_id", "task_id", "at", "recorded_at", "payload"),
    # a requirement's own manager and its attempts
    "ade_controller_managers": ("user_id", "requirement_id", "share", "fence", "lease", "woken", "woken_at", "wakes",
                                "more_wakes", "waits", "waits_until", "waits_since", "waits_models", "failures",
                                "attempts", "reset_at", "told_at", "for_owner", "for_owner_at", "created_at",
                                "updated_at"),
    "ade_controller_attempts": ("user_id", "id", "requirement_id", "n", "fence", "state", "outcome", "cause", "refused",
                                "refused_count", "refused_models", "note", "note_cut", "note_hidden", "woken_by",
                                "packet", "packet_digest", "session_id", "waits_until", "late", "untaken",
                                "queued_at", "sent_at", "started_at", "ended_at", "updated_at"),
}


def test_the_controller_s_tables_are_made_with_their_columns(env):
    with store.connect() as db:
        for table, columns in TABLES.items():
            assert tuple(r[1] for r in db.execute(f"PRAGMA table_info({table})")) == columns, table


def test_a_store_made_before_the_controller_gets_its_tables(env, tmp_path):
    path = str(tmp_path / "old.db")
    with store.connect(path):
        pass
    db = sqlite3.connect(path)
    for table in TABLES:
        db.execute(f"DROP TABLE {table}")
    db.commit()
    db.close()
    with store.connect(path) as again:
        names = {r[0] for r in again.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(TABLES) <= names


# ---- the owner's controls ------------------------------------------------------------------------------------------

def test_before_it_is_set_up_the_view_says_so_and_the_settings_make_it_with_the_defaults(client, owner):
    h, user = owner
    assert client.get(C, headers=h).json() == {"controller": None, "requests": [], "requirements": [],
                                               "other_sessions": [], "questions": [], "decisions": [],
                                               "holder": None}
    r = client.put(C, headers=h, json={"cap": 3})
    assert r.status_code == 200, r.text
    c = r.json()["controller"]
    assert (c["state"], c["account"], c["cap"], c["models"], c["efforts"], c["chat_model"], c["chat_effort"],
            c["confirm_plans"]) == ("off", None, 3, ["opus"], ["max"], "opus", "xhigh", False)


@pytest.mark.parametrize("body,status,words", [
    ({"cap": 0}, 400, "The cap is a whole number from 1 to 10."),
    ({"cap": 11}, 400, "The cap is a whole number from 1 to 10."),
    ({"cap": 12}, 400, "The cap is a whole number from 1 to 10."),
    ({"cap": True}, 400, "The cap is a whole number from 1 to 10."),
    ({"models": []}, 400, "The tasks' models is a list of 1 to 8."),
    ({"models": ["opus"] * 9}, 400, "The tasks' models is a list of 1 to 8."),
    ({"models": ["op us"]}, 400, "The tasks' models holds one that is not one."),
    ({"efforts": ["huge"]}, 400, "The tasks' efforts holds one that is not one."),
    ({"models": ["opus", "sk-ant-x"]}, 400, "The tasks' models holds a name shaped like an Anthropic key (sk-ant-)."),
    ({"chat_model": "ghp_x"}, 400, "The Manager's model holds a name shaped like a GitHub token (ghp_, gho_, ghu_, "
                                   "ghs_, ghr_)."),
    ({"chat_effort": "huge"}, 400, "The Manager's effort is one of low, medium, high, xhigh, max, ultracode."),
    ({"chat_model": ""}, 400, "The Manager's model is a model's name or alias."),
    ({"confirm_plans": 1}, 400, "confirm_plans is true or false."),
    ({"account": "nobody"}, 400, "An account is default or the account's id, or null."),
    ({"account": "default"}, 409, "Confirm the account as your own in Connections first: the Manager runs on one "
                                  "of your own accounts only."),
    ({"relay": True}, 400, "Not a setting of the Manager: relay."),
])
def test_a_setting_that_is_not_one_is_refused_with_why(client, owner, body, status, words):
    h, user = owner
    r = client.put(C, headers=h, json=body)
    assert (r.status_code, r.json()["detail"]) == (status, words)


def test_the_settings_change_what_is_given_each_change_journaled_once(client, owner):
    h, user = owner
    own(user["id"], "a" * 12)
    r = client.put(C, headers=h, json={"account": "a" * 12, "cap": 1, "models": ["opus", "sonnet", "opus"],
                                       "efforts": ["high", "max"], "chat_model": "claude-opus-5-5",
                                       "chat_effort": "max", "confirm_plans": True})
    assert r.status_code == 200, r.text
    c = r.json()["controller"]
    assert (c["account"], c["account_confirmed"], c["cap"], c["models"], c["efforts"], c["chat_model"],
            c["chat_effort"], c["confirm_plans"]) == ("a" * 12, True, 1, ["opus", "sonnet"], ["high", "max"],
                                                      "claude-opus-5-5", "max", True)
    assert client.put(C, headers=h, json={"cap": 1}).status_code == 200          # the same again: no new line
    lines = events(user["id"], "controller.settings")
    assert len(lines) == 1 and lines[0]["payload"]["changed"]["models"] == ["opus", "sonnet"]


def test_on_needs_its_account_assigned_and_confirmed_and_stop_needs_it_on(client, owner):
    h, user = owner
    r = client.post(C + "/on", headers=h)
    assert (r.status_code, r.json()["detail"]) == (409, "Choose the account the Manager runs on first.")
    assert client.post(C + "/stop", headers=h).json()["detail"] == "The Manager is off: there is nothing to stop."
    own(user["id"])
    assert client.put(C, headers=h, json={"account": "default"}).status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET state = 'reported' WHERE user_id = ?", (user["id"],))
    r = client.post(C + "/on", headers=h)
    assert r.status_code == 409 and "no longer confirmed" in r.json()["detail"]
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET state = 'confirmed' WHERE user_id = ?", (user["id"],))
    assert client.post(C + "/on", headers=h).json()["controller"]["state"] == "on"
    assert client.post(C + "/on", headers=h).json()["controller"]["state"] == "on"            # again: no change
    assert client.post(C + "/stop", headers=h).json()["controller"]["state"] == "stopped"
    assert client.post(C + "/off", headers=h).json()["controller"]["state"] == "off"
    kinds = [(e["kind"], e["payload"].get("from"), e["payload"].get("to")) for e in events(user["id"])
             if e["kind"].startswith("controller.") and e["kind"] != "controller.settings"]
    assert kinds == [("controller.on", "off", "on"), ("controller.stopped", "on", "stopped"),
                     ("controller.off", "stopped", "off")]


def test_taking_its_account_away_turns_it_off(client, owner):
    h, user = owner
    setup(user)
    c = client.put(C, headers=h, json={"account": None}).json()["controller"]
    assert (c["state"], c["account"]) == ("off", None)
    assert events(user["id"])[-1]["kind"] == "controller.off" and events(user["id"])[-1]["payload"]["why"] == \
        "no account"


def test_each_change_is_sent_on_at_once_and_moves_the_version(client, owner, ws):
    h, user = owner
    version = 0
    for method, path, body in (("put", "", {"cap": 3}), ("post", "/requests", {"text": "Hello"}),
                               ("post", "/decisions", {"text": "Use main"})):
        r = getattr(client, method)(C + path, headers=h, json=body)
        assert r.status_code == 200, r.text
        now = client.get(C, headers=h).json()["controller"]["version"]
        assert now > version
        version = now
    assert ws.synced == [user["id"]] * 3


def test_the_go_and_a_page_that_changes_something_move_the_version_and_one_that_changes_nothing_does_not(env):
    user = {"id": "u1"}
    setup_plain(user)
    controller.change_settings(user, {"confirm_plans": True}, now=T0 + 1)
    request = controller.add_request(user, REQUEST, now=T0 + 5)

    def version():
        with store.connect() as db:
            return controller._row(db, "u1")["version"]
    v = version()
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid))
    assert version() == v + 1
    v = version()
    assert take(user) == {"taken": 0, "refused": 0, "flagged": 0, "states": 0} and version() == v
    controller.go(user, request["id"], now=T0 + 6)
    assert version() == v + 1
    with pytest.raises(agents.AgentError) as e:
        controller.go(user, request["id"], now=T0 + 7)          # no plan waits for it now
    assert e.value.status == 409 and version() == v + 1


def test_a_request_is_kept_word_for_word_numbered_with_its_digest_and_journaled_without_its_words(client, owner):
    h, user = owner
    words = "  Make the README say hello.\n\tKeep the rest. Grüße, café: zażółć — done  "
    r = client.post(C + "/requests", headers=h, json={"text": words})
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["n"], got["text"], got["digest"]) == (1, words, hashlib.sha256(words.encode("utf-8")).hexdigest())
    assert client.post(C + "/requests", headers=h, json={"text": "Second"}).json()["n"] == 2
    with store.connect() as db:
        assert db.execute("SELECT text FROM ade_controller_requests WHERE id = ?", (got["id"],)).fetchone()[0] == words
    line = events(user["id"], "request")[0]
    assert line["payload"] == {"request": got["id"], "n": 1, "chars": len(words)}
    assert "README" not in json.dumps(events(user["id"]))
    listed = client.get(C + "/requests", headers=h).json()["requests"]
    assert [x["n"] for x in listed] == [2, 1]
    assert [x["n"] for x in client.get(C + "/requests?before=2", headers=h).json()["requests"]] == [1]


@pytest.mark.parametrize("text,words", [
    ("", "A request is text of at most 4000 characters."),
    ("   ", "A request is text of at most 4000 characters."),
    ("x" * 4001, "A request is text of at most 4000 characters."),
    ("a\x00b", "A request is text of at most 4000 characters."),
    ("use the key sk-ant-api03-" + "A" * 40, None),
])
def test_a_request_that_is_not_one_or_holds_a_secret_s_shape_is_refused(client, owner, text, words):
    h, user = owner
    r = client.post(C + "/requests", headers=h, json={"text": text})
    assert r.status_code == 400
    if words:
        assert r.json()["detail"] == words
    else:
        assert "shaped like" in r.json()["detail"] and "A" * 40 not in r.json()["detail"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_controller_requests").fetchone()[0] == 0


def test_requests_not_yet_planned_fill_what_a_fresh_controller_is_given_then_wait(env):
    user = {"id": "u1"}
    long = "y" * controller.REQUEST_MAX
    made = 0
    with pytest.raises(agents.AgentError) as refused:
        for _ in range(20):
            controller.add_request(user, long, now=T0)
            made += 1
    assert refused.value.status == 409 and "not yet planned your earlier requests" in str(refused.value)
    assert 3 <= made < 20
    # every one not planned fits the packet's instructions
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, "u1", T0)[0])
    assert len(packet["instructions"]["requests"]) == made and packet["instructions"]["more_requests"] == 0
    # one planned, and the next is taken again
    with store.connect() as db:
        first = db.execute("SELECT id FROM ade_controller_requests WHERE n = 1").fetchone()[0]
    controller.take("u1", record(plan(first, long)), now=T0 + 100)
    controller.add_request(user, long, now=T0 + 200)


def test_a_request_too_large_as_the_packet_holds_it_is_refused(env):
    hidden = "\u200b" * controller.REQUEST_MAX           # each escaped as \u200b in the packet: six bytes
    with pytest.raises(agents.AgentError) as refused:
        controller.add_request({"id": "u1"}, hidden, now=T0)
    assert refused.value.status == 400 and "past 8192" in str(refused.value)


def test_a_decision_answers_its_question_once_and_is_journaled_without_its_words(client, owner):
    h, user = owner
    _, rid = planned(user)
    qid = uid()
    take(user, question("Which branch?", qid=qid, requirement=rid))
    r = client.post(f"{C}/questions/{qid}/answer", headers=h, json={"text": "Use main, always."})
    assert r.status_code == 200, r.text
    assert (r.json()["n"], r.json()["question"], r.json()["text"]) == (1, qid, "Use main, always.")
    q = client.get(C, headers=h).json()["questions"][0]
    assert (q["state"], q["decision"]) == ("answered", r.json()["id"])
    again = client.post(f"{C}/questions/{qid}/answer", headers=h, json={"text": "No"})
    assert (again.status_code, again.json()["detail"]) == (409, "The question is answered already.")
    assert client.post(f"{C}/questions/{uid()}/answer", headers=h, json={"text": "x"}).status_code == 404
    own_one = client.post(C + "/decisions", headers=h, json={"text": "Never touch infra/."}).json()
    assert (own_one["n"], own_one["question"]) == (2, None)
    line = events(user["id"], "decision")[0]
    assert line["payload"] == {"decision": r.json()["id"], "n": 1, "question": qid, "chars": 17}
    assert line["requirement_id"] == rid
    assert "main" not in json.dumps([e["payload"] for e in events(user["id"])])
    # the view keeps an answered question for a day, an open one until it is answered
    still = uid()
    take(user, question("Still open?", qid=still))
    later = controller.view(user, now=int(time.time()) + 86401)["questions"]
    assert [q["id"] for q in later] == [still]


def test_hold_release_and_close_a_requirement(client, owner):
    h, user = owner
    _, rid = planned(user)
    assert client.post(f"{C}/requirements/{rid}/hold", headers=h, json={"hold": True}).json()["hold"] is True
    assert client.post(f"{C}/requirements/{rid}/hold", headers=h, json={"hold": True}).status_code == 200
    assert client.post(f"{C}/requirements/{rid}/hold", headers=h, json={"hold": False}).json()["hold"] is False
    assert [e["kind"] for e in events(user["id"]) if e["requirement_id"] == rid][-2:] == ["hold", "release"]
    bad = client.post(f"{C}/requirements/{rid}/close", headers=h, json={"as": "finished"})
    assert (bad.status_code, bad.json()["detail"]) == (400, "A requirement is closed as done or as stopped.")
    closed = client.post(f"{C}/requirements/{rid}/close", headers=h, json={"as": "done"}).json()
    assert (closed["state"], closed["activity"]) == ("done", "done")
    again = client.post(f"{C}/requirements/{rid}/close", headers=h, json={"as": "stopped"})
    assert (again.status_code, again.json()["detail"]) == (409, "The requirement is closed already.")
    held = client.post(f"{C}/requirements/{rid}/hold", headers=h, json={"hold": True})
    assert (held.status_code, held.json()["detail"]) == (409, "The requirement is closed.")
    assert events(user["id"], "requirement.closed")[0]["payload"] == {"requirement": rid, "as": "done"}


def test_another_account_reaches_none_of_these_records(client, owner, monkeypatch):
    h, user = owner
    request, rid = planned(user)
    qid = uid()
    take(user, question("Which?", qid=qid))
    monkeypatch.setenv("OPEN_EDITION_ADMINS", "admin@example.com,admin2@example.com")
    other = signed_up(client, "admin2@example.com")
    for method, path, body in (("post", f"/requirements/{rid}/hold", {"hold": True}),
                               ("post", f"/requirements/{rid}/close", {"as": "done"}),
                               ("get", f"/requirements/{rid}", None),
                               ("post", f"/questions/{qid}/answer", {"text": "x"}),
                               ("post", f"/requests/{request['id']}/go", None)):
        kwargs = {"json": body} if body is not None else {}
        r = getattr(client, method)(C + path, headers=other, **kwargs)
        assert r.status_code == 404, (path, r.text)
    assert client.get(C, headers=other).json()["controller"] is None


def test_without_a_workspace_on_this_deployment_or_signed_in_there_is_no_controller(client, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_ADE", raising=False)
    h = signed_up(client)
    assert client.get(C, headers=h).status_code == 404
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    assert client.get(C).status_code == 401


# ---- taking the workspace's record: plans ---------------------------------------------------------------------------

def test_a_plan_whose_words_are_the_owner_s_word_for_word_is_taken(env):
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    rid = uid()
    got = take(user, plan(request["id"], "Make the README say hello.", rid=rid,
                          acceptance="its first line is hello.", title="README\u200b greeting", repos=["demo", "web",
                                                                                                        "demo"]))
    assert got == {"taken": 1, "refused": 0, "flagged": 0, "states": 0}
    r = requirement_row(rid)
    assert (r["words"], r["acceptance"], r["title"], r["title_hidden"], json.loads(r["repos"]), r["state"]) == (
        "Make the README say hello.", "its first line is hello.", "README greeting", 1, ["demo", "web"], "open")
    line = events("u1", "plan")[0]
    assert line["payload"]["n"] == 1 and line["payload"]["acceptance"] is True and "README" not in json.dumps(
        line["payload"])


def setup_plain(user, now=T0):
    own(user["id"])
    controller.change_settings(user, {"account": "default"}, now=now)
    controller.set_state(user, "on", now=now)


NOT_WHOLE = "not in the request it names, word for word, as whole paragraphs"


def look(at):
    """The alert the owner is told of a flag or a refusal by, keyed by the hour the newest came in (alerts)."""
    return f"controller-look:{at // controller.LOOK}", "controller-look"


@pytest.mark.parametrize("change,why", [
    (lambda a, other: a.update(words="Make the readme say hello."), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(words="Make the README  say hello."), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(words="   "), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(words=7), "its words are " + NOT_WHOLE),
    # in the request word for word, but not whole: the rest of its sentence would change what it says
    (lambda a, other: a.update(words="the README say hello."), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(words="Make the README"), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(words="say hello.\n\nits first line is hello."), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(words="Make the README say hello.\n\nits first line"), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(words="  Make the README say hello."), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(words="Make the README say hello.\n\n\n"), "its words are " + NOT_WHOLE),
    (lambda a, other: a.update(acceptance="its first line is Hello."), "its acceptance is " + NOT_WHOLE),
    (lambda a, other: a.update(acceptance="first line is hello."), "its acceptance is " + NOT_WHOLE),
    # a blank acceptance is refused as blank words are, never taken as none
    (lambda a, other: a.update(acceptance=""), "its acceptance is " + NOT_WHOLE),
    (lambda a, other: a.update(request=uid()), "it names no request of the owner's"),
    (lambda a, other: a.update(request=other), "it names no request of the owner's"),
    (lambda a, other: a.update(requirement="x"), "it names no requirement"),
    (lambda a, other: a.update(title="\u200b\u200b"), "it has no title"),
    (lambda a, other: a.update(repos=[]), "its repositories are not 1 to 10 names"),
    (lambda a, other: a.update(repos=["../etc"]), "its repositories are not 1 to 10 names"),
    (lambda a, other: a.update(repos=["r%d" % i for i in range(11)]), "its repositories are not 1 to 10 names"),
    (lambda a, other: a.update(repos=["demo", "ghp_tools"]), "a repository it names is shaped like a GitHub token "
                                                              "(ghp_, gho_, ghu_, ghs_, ghr_)"),
])
def test_a_plan_that_does_not_hold_is_refused_with_why_and_not_taken(env, change, why):
    user, them = {"id": "u1"}, {"id": "u2"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    theirs = controller.add_request(them, "Make the README say hello.", now=T0)
    a = plan(request["id"], "Make the README say hello.")
    change(a, theirs["id"])
    assert take(user, a)["refused"] == 1
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_controller_requirements").fetchone()[0] == 0
    line = events("u1", "plan.refused")[0]
    assert line["payload"]["why"] == why and line["payload"]["action"] == a["id"]
    assert look(T0 + 100) in controller.alerts("u1", T0 + 100)
    # told for a day after the app refused it, then no longer
    assert look(T0 + 100) in controller.alerts("u1", T0 + 100 + 86400)
    assert look(T0 + 100) not in controller.alerts("u1", T0 + 100 + 86401)


@pytest.mark.parametrize("request_text,words", [
    ("Make the README say hello.\n\nThen add a licence.", "Then add a licence."),
    ("Make the README say hello.\n\nThen add a licence.", "Make the README say hello."),
    ("Make the README say hello.\n\nThen add a licence.", "Make the README say hello.\n\nThen add a licence."),
    # a run of paragraphs, the blank lines between them more than one and holding spaces
    ("A.\n\nB, on\ntwo lines.\n \n\nC.", "B, on\ntwo lines.\n \n\nC."),
    # the whitespace around a paragraph is no part of where it starts and ends
    ("  First, indented.\n\n\tThen a tab.  \n", "Then a tab."),
    ("  First, indented.\n\n\tThen a tab.  \n", "\tThen a tab.  "),
    ("First.\r\n\r\nSecond.", "Second."),
    ("First.\n \t \nSecond.", "Second."),
    ("Make the README say hello\u3002\n\nAdd a licence\u3002", "Add a licence\u3002"),
    # found first inside a paragraph, then whole: whole somewhere is enough
    ("Do not Say hello.\n\nSay hello.", "Say hello."),
])
def test_a_plan_s_words_may_be_any_whole_paragraph_or_run_of_paragraphs_of_the_request(env, request_text, words):
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, request_text, now=T0)
    assert take(user, plan(request["id"], words, acceptance=words))["taken"] == 1


@pytest.mark.parametrize("request_text,words", [
    ("Do not delete the prod database.", "delete the prod database."),
    # five more, each inside one paragraph: a sentence, a clause or a line of it is not whole
    ("Do not\ndelete the backups.", "delete the backups."),
    ("Never, e.g. delete the prod database.", "delete the prod database."),
    ("Two things you must never do: push to main; delete the backups.", "push to main;"),
    ("Under no circumstances; delete the prod database.", "delete the prod database."),
    ("Never run these:\n- rm -rf /tmp/data\n- drop the database", "- rm -rf /tmp/data"),
    ("Never run these:\n- rm -rf /tmp/data\n- drop the database", "rm -rf /tmp/data"),
    # what an earlier rule let through: whole sentences, clauses and lines of one paragraph
    ("Make the README say hello. Then add a licence.", "Then add a licence."),
    ("First line\nSecond line", "First line"),
    ("Make the README say hello\u3002Add a licence\u3002", "Add a licence\u3002"),
])
def test_a_part_of_a_paragraph_is_not_the_owner_s_words(env, request_text, words):
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, request_text, now=T0)
    for a in (plan(request["id"], words), plan(request["id"], request_text, acceptance=words)):
        assert take(user, a)["refused"] == 1
    assert [e["payload"]["why"] for e in events("u1", "plan.refused")] == [
        "its words are " + NOT_WHOLE, "its acceptance is " + NOT_WHOLE]


def test_each_action_is_taken_once_by_its_id_and_a_refused_one_is_not_journaled_again(env):
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    good, bad = plan(request["id"], "Make the README say hello."), plan(request["id"], "nope")
    page = record(good, bad)
    assert controller.take("u1", page, now=T0 + 100) == {"taken": 1, "refused": 1, "flagged": 0, "states": 0}
    assert controller.take("u1", page, now=T0 + 200) == {"taken": 0, "refused": 0, "flagged": 0, "states": 0}
    assert len(events("u1", "plan")) == 1 and len(events("u1", "plan.refused")) == 1
    # the same requirement planned again under another action is refused, not taken twice
    again = dict(good, id=uid())
    assert controller.take("u1", record(again), now=T0 + 300)["refused"] == 1
    assert events("u1", "plan.refused")[-1]["payload"]["why"] == "a requirement of that id is recorded already"


@pytest.mark.parametrize("page", [None, [], "x", {"actions": "x"}])
def test_a_page_that_is_not_one_takes_nothing(env, page):
    assert controller.take("u1", page, now=T0) == {"taken": 0, "refused": 0, "flagged": 0, "states": 0}
    assert events("u1") == []


def test_entries_of_a_page_that_are_not_actions_are_counted_and_told(env):
    """An entry with no id of an action's form (one character short, say) or a kind not the controller's is no action
    to take: it is counted as refused and the page told in one line, so no entry leaves no trace."""
    setup_plain({"id": "u1"})
    short = {"id": uid()[:31], "kind": "start", "task": uid(), "requirement": uid()}
    page = record(None, 1, "x", {"kind": "plan"}, {"id": uid(), "kind": "other"}, short)
    assert controller.take("u1", page, now=T0 + 100) == {"taken": 0, "refused": 6, "flagged": 0, "states": 0}
    lines = events("u1", "record.refused")
    assert [e["payload"] for e in lines] == [{"why": "a page of the workspace's record held 6 entries that are not "
                                                     "actions", "skipped": 6, "actions": 0, "states": 0}]
    with store.connect() as db:
        c = controller._row(db, "u1")
    assert (c["refused"], c["refused_at"]) == (6, T0 + 100)
    assert look(T0 + 100) in controller.alerts("u1", T0 + 100)


def test_past_the_refused_actions_the_app_journals_they_are_only_counted(env, monkeypatch):
    monkeypatch.setattr(controller, "REFUSED_A_DAY", 3)
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    assert take(user, *[plan(request["id"], "nope") for _ in range(5)])["refused"] == 5
    assert len(events("u1", "plan.refused")) == 3
    with store.connect() as db:
        assert db.execute("SELECT refused FROM ade_controllers").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM ade_controller_actions").fetchone()[0] == 3
    # a page past what the app reads is counted too, and past the cap not journaled
    monkeypatch.setattr(controller, "ACTIONS_A_PAGE", 2)
    assert take(user, *[plan(request["id"], "nope") for _ in range(4)])["refused"] == 4
    assert events("u1", "record.refused") == [] and len(events("u1", "plan.refused")) == 3
    with store.connect() as db:
        assert db.execute("SELECT refused FROM ade_controllers").fetchone()[0] == 5 + 2 + 2
    # and the owner is told of them all the same, by when the last came in
    take(user, plan(request["id"], "nope"), now=T0 + 7200)
    assert look(T0 + 7200) in controller.alerts("u1", T0 + 7200)
    # a day on, the journal takes refusals again
    take(user, plan(request["id"], "nope"), now=T0 + 100 + 86401)
    assert len(events("u1", "plan.refused")) == 4


def test_open_requirements_past_their_most_are_refused(env, monkeypatch):
    monkeypatch.setattr(controller, "REQUIREMENTS_OPEN_MOST", 2)
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    got = take(user, *[plan(request["id"], "Make the README say hello.") for _ in range(3)])
    assert (got["taken"], got["refused"]) == (2, 1)
    assert events("u1", "plan.refused")[0]["payload"]["why"] == \
        "the Manager has the most open requirements the app keeps (2)"


# ---- taking the workspace's record: starts and the bounds ----------------------------------------------------------

def test_a_start_within_the_bounds_is_taken_and_not_flagged(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    a = start(rid)
    assert take(user, a) == {"taken": 1, "refused": 0, "flagged": 0, "states": 0}
    t = task_row(a["task"])
    assert (t["flag"], t["state"], t["session_id"], t["started_at"], t["account"], t["model"], t["effort"]) == (
        None, "starting", SESSION, T0 + 20, "default", "opus", "max")
    line = events("u1", "start")[0]
    assert "flag" not in line["payload"] and line["task_id"] == a["task"] and line["requirement_id"] == rid
    assert controller.alerts("u1", T0 + 100) == []


BOUNDS = [
    ("off", {}, lambda user, rid: controller.set_state(user, "off", now=T0 + 15),
     "started while the Manager was off"),
    ("stopped", {}, lambda user, rid: controller.set_state(user, "stopped", now=T0 + 15),
     "started while the Manager was stopped"),
    ("account", {"account": OTHER}, None, "started on another account than the Manager's"),
    ("program", {"program": "codex"}, None, "started a program other than Claude Code"),
    ("model", {"model": "sonnet"}, None, "started with a model the owner did not allow"),
    ("effort", {"effort": "low"}, None, "started at an effort the owner did not allow"),
    ("requirement", {"requirement": None}, None, "started for a requirement the app does not have"),
    ("unknown", {"requirement": "c" * 32}, None, "started for a requirement the app does not have"),
    ("held", {}, lambda user, rid: controller.hold(user, rid, True, now=T0 + 15), "started for a requirement on hold"),
    ("closed", {}, lambda user, rid: controller.close(user, rid, "stopped", now=T0 + 15),
     "started for a closed requirement"),
    ("repo", {"repo": "other"}, None, "started in a repository its requirement may not touch"),
]


@pytest.mark.parametrize("name,fields,before,words", BOUNDS, ids=[b[0] for b in BOUNDS])
def test_a_start_outside_a_bound_is_kept_flagged_with_that_bound_and_told(env, name, fields, before, words):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    if before:
        before(user, rid)
    a = start(fields.get("requirement", rid) if "requirement" in fields else rid,
              **{k: v for k, v in fields.items() if k != "requirement"})
    assert take(user, a)["flagged"] == 1
    t = task_row(a["task"])
    assert t["flag"] == words
    assert events("u1", "start")[0]["payload"]["flag"] == words
    assert look(T0 + 100) in controller.alerts("u1", T0 + 100)
    # told for a day after the app found it, then no longer
    assert look(T0 + 100) not in controller.alerts("u1", T0 + 100 + 86401)


def test_flags_and_refusals_are_told_at_most_once_in_each_clock_hour_and_a_later_change_tells_nothing(env,
                                                                                                       monkeypatch):
    user = {"id": "u1"}
    setup(user, cap=10)
    _, rid = planned(user)
    hour = (T0 // 3600 + 1) * 3600              # an hour's start after the controller was set up
    sent = []
    monkeypatch.setattr(push, "subscriptions", lambda user_id: ["device"])
    monkeypatch.setattr(push, "send", lambda sub, m, now, post: sent.append(m) or 201)

    def tell(now):
        items = [(k, m) for k, m in push.items_for("u1", [], now) if k.startswith("controller-look")]
        push.notify("u1", items, now=now)
        return [k for k, _ in items]
    a = start(rid, account=OTHER, at=hour + 10)
    take(user, a, now=hour + 10)
    assert tell(hour + 20) == [look(hour + 10)[0]] and len(sent) == 1
    # more in the same hour, a flag again by a continue and a refusal: the same key, so not told again
    controller.set_state(user, "stopped", now=hour + 30)
    take(user, act("continue", a["task"], at=hour + 40), plan("x" * 32, "nope"), now=hour + 50)
    assert task_row(a["task"])["flagged_at"] == hour + 50
    assert tell(hour + 60) == [look(hour + 10)[0]] and len(sent) == 1
    # something new in the next hour: told once more
    take(user, start(rid, account=OTHER, session=None, at=hour + 3610), now=hour + 3610)
    assert tell(hour + 3620) == [look(hour + 3610)[0]] and len(sent) == 2
    # two that come either side of a clock hour's turn, twenty seconds apart, are told twice: the key is the clock hour
    #
    turn = hour + 3 * 3600
    take(user, start(rid, account=OTHER, session=None, at=turn - 10), now=turn - 10)
    assert tell(turn - 5) == [look(turn - 10)[0]] and len(sent) == 3
    take(user, start(rid, account=OTHER, session=None, at=turn + 10), now=turn + 10)
    assert tell(turn + 15) == [look(turn + 10)[0]] and len(sent) == 4
    # a day on, a session ends: a change of state, neither a flag nor a refusal, so nothing is told
    take(user, tasks_=[{"id": a["task"], "state": "done", "ended_at": turn + 90000}], now=turn + 90100)
    assert task_row(a["task"])["updated_at"] == turn + 90100
    assert tell(turn + 90100) == [] and len(sent) == 4


def test_every_bound_a_start_broke_is_named(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    a = start(rid, account=OTHER, program="codex", model="sonnet", effort="low", repo="other")
    take(user, a)
    assert task_row(a["task"])["flag"] == (
        "started on another account than the Manager's; started a program other than Claude Code; started with a "
        "model the owner did not allow; started at an effort the owner did not allow; started in a repository its "
        "requirement may not touch")


def test_a_start_is_judged_by_the_bounds_in_effect_when_it_was_made(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    before_stop = start(rid, at=T0 + 20)
    controller.set_state(user, "stopped", now=T0 + 40)
    while_stopped = start(rid, at=T0 + 50, session=OTHER)
    controller.set_state(user, "on", now=T0 + 60)          # on again: the start made while stopped stays flagged
    controller.change_settings(user, {"models": ["sonnet"], "cap": 5}, now=T0 + 70)
    take(user, before_stop, while_stopped, now=T0 + 100)
    assert task_row(before_stop["task"])["flag"] is None
    assert task_row(while_stopped["task"])["flag"] == "started while the Manager was stopped"


def test_a_hold_is_judged_at_the_start_s_time_and_a_closed_requirement_from_its_closing(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    controller.hold(user, rid, True, now=T0 + 15)
    held = start(rid, at=T0 + 20)
    controller.hold(user, rid, False, now=T0 + 25)          # released after: still started while held
    released = start(rid, at=T0 + 30, session=OTHER)
    take(user, held, released, now=T0 + 40)
    assert task_row(held["task"])["flag"] == "started for a requirement on hold"
    assert task_row(released["task"])["flag"] is None
    controller.close(user, rid, "done", now=T0 + 50)
    late = start(rid, at=T0 + 45, session="c" * 12)         # made before the close, taken after it
    take(user, late, now=T0 + 60)
    assert task_row(late["task"])["flag"] is None


def test_a_plan_made_while_plans_wait_for_the_owner_s_go_needs_the_go_before_its_start(env):
    user = {"id": "u1"}
    own(user["id"])
    controller.change_settings(user, {"account": "default", "confirm_plans": True, "cap": 5}, now=T0)
    controller.set_state(user, "on", now=T0)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 5), now=T0 + 6)
    assert requirement_row(rid)["confirm"] == 1
    early = start(rid, at=T0 + 10)
    assert controller.go(user, request["id"], now=T0 + 12) == {"request": request["id"], "n": 1, "go_at": T0 + 12,
                                                              "requirements": [rid]}
    after = start(rid, at=T0 + 14, session=OTHER)
    take(user, early, after, now=T0 + 20)
    assert task_row(early["task"])["flag"] == "started before the owner's go on its plan"
    assert task_row(after["task"])["flag"] is None
    with pytest.raises(agents.AgentError) as e:                  # nothing waits for a go now
        controller.go(user, request["id"], now=T0 + 21)
    assert e.value.status == 409 and str(e.value) == "No plan of this request waits for your go."
    # a plan made after the go is one the owner did not see: it waits for a go of its own
    later = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=later, at=T0 + 25), now=T0 + 26)
    unseen = start(later, at=T0 + 27, session="c" * 12)
    take(user, unseen, now=T0 + 28)
    assert task_row(unseen["task"])["flag"] == "started before the owner's go on its plan"
    assert controller.go(user, request["id"], now=T0 + 30)["requirements"] == [later]
    seen = start(later, at=T0 + 31, session="d" * 12)
    take(user, seen, now=T0 + 32)
    assert task_row(seen["task"])["flag"] is None
    assert (requirement_row(rid)["go_at"], requirement_row(later)["go_at"]) == (T0 + 12, T0 + 30)
    assert [(e["requirement_id"], e["at"]) for e in events("u1", "go")] == [(rid, T0 + 12), (later, T0 + 30)]
    # a plan made before the switch was turned on needs no go
    controller.change_settings(user, {"confirm_plans": False}, now=T0 + 40)
    rid2 = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid2, at=T0 + 45), now=T0 + 46)
    assert requirement_row(rid2)["confirm"] == 0


def test_a_go_in_the_same_second_as_a_start_covers_it_and_a_closed_plan_waits_for_none(env, ws):
    user = auth_user()
    own(user["id"])
    controller.change_settings(user, {"account": "default", "confirm_plans": True, "cap": 5}, now=T0)
    controller.set_state(user, "on", now=T0)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    rid, closed = uid(), uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 5),
         plan(request["id"], "Make the README say hello.", rid=closed, at=T0 + 5), now=T0 + 6)
    controller.close(user, closed, "stopped", now=T0 + 7)
    controller.go(user, request["id"], now=T0 + 12)
    s = start(rid, at=T0 + 12)
    take(user, s, now=T0 + 20)
    assert task_row(s["task"])["flag"] is None
    assert requirement_row(closed)["go_at"] is None             # a go covers the open plans only
    controller.sync(user, "http://workspace.test")
    assert {r["id"]: r["waits_for_go"] for r in ws.mirrors[-1]["requirements"]} == {rid: False, closed: False}


def test_a_go_given_before_any_plan_covers_none_made_after_it(env):
    user = {"id": "u1"}
    own(user["id"])
    controller.change_settings(user, {"account": "default", "confirm_plans": True, "cap": 5}, now=T0)
    controller.set_state(user, "on", now=T0)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    with pytest.raises(agents.AgentError) as e:
        controller.go(user, request["id"], now=T0 + 2)
    assert e.value.status == 409 and events("u1", "go") == []
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 5), now=T0 + 6)
    s = start(rid, at=T0 + 10)
    take(user, s, now=T0 + 11)
    assert task_row(s["task"])["flag"] == "started before the owner's go on its plan"
    assert requirement_row(rid)["go_at"] is None


def test_an_owner_s_change_in_the_same_second_as_a_start_is_in_effect_for_it(env):
    user = {"id": "u1"}
    own(user["id"])
    controller.change_settings(user, {"account": "default", "cap": 5}, now=T0)
    controller.set_state(user, "on", now=T0 + 10)
    request = controller.add_request(user, REQUEST, now=T0 + 10)
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 10), now=T0 + 11)
    at_on = start(rid, at=T0 + 10)
    controller.set_state(user, "stopped", now=T0 + 20)
    at_stop = start(rid, at=T0 + 20, session=OTHER)
    take(user, at_on, at_stop, now=T0 + 30)
    assert task_row(at_on["task"])["flag"] is None
    assert task_row(at_stop["task"])["flag"] == "started while the Manager was stopped"


def test_a_hold_or_a_close_in_the_same_second_as_a_start_is_in_effect_for_it(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    held, closed = uid(), uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=held, at=T0 + 2), plan(request["id"], "Make the README say hello.", rid=closed, at=T0 + 2),
         now=T0 + 3)
    controller.hold(user, held, True, now=T0 + 20)
    controller.close(user, closed, "stopped", now=T0 + 20)
    a, b = start(held, at=T0 + 20), start(closed, at=T0 + 20, session=OTHER)
    take(user, a, b, now=T0 + 30)
    assert task_row(a["task"])["flag"] == "started for a requirement on hold"
    assert task_row(b["task"])["flag"] == "started for a closed requirement"


def test_an_id_is_the_owner_s_own_and_one_another_owner_has_takes_and_touches_nothing_of_theirs(env):
    user, them = {"id": "u1"}, {"id": "u2"}
    setup_plain(user)
    setup_plain(them)
    _, their_rid = planned(them)
    theirs, their_q = start(their_rid), uid()
    take(them, theirs, question("Theirs?", qid=their_q))
    request = controller.add_request(user, REQUEST, now=T0 + 6)
    # the same ids in another owner's record are that owner's own: taken, and nothing of the first owner's is told of
    # them or changed by them
    got = take(user, plan(request["id"], "Make the README say hello.", rid=their_rid),
               start(their_rid, tid=theirs["task"], session=OTHER, at=T0 + 20), question("Mine?", qid=their_q),
               tasks_=[{"id": theirs["task"], "state": "done", "ended_at": T0 + 50}])
    assert got == {"taken": 3, "refused": 0, "flagged": 0, "states": 0}
    assert requirement_row(their_rid, "u1")["user_id"] == "u1" and requirement_row(their_rid, "u2")["user_id"] == "u2"
    assert task_row(theirs["task"], "u1")["session_id"] == OTHER and task_row(theirs["task"], "u2")["session_id"] == \
        SESSION
    # a state the first owner's record gives touches only that owner's task, and a hold only that owner's requirement
    take(user, tasks_=[{"id": theirs["task"], "state": "done", "ended_at": T0 + 60}], now=T0 + 110)
    assert task_row(theirs["task"], "u1")["state"] == "done" and task_row(theirs["task"], "u2")["state"] == "starting"
    controller.hold(user, their_rid, True, now=T0 + 120)
    assert requirement_row(their_rid, "u1")["hold"] == 1 and requirement_row(their_rid, "u2")["hold"] == 0
    take(user, act("stop", theirs["task"], at=T0 + 121), act("continue", theirs["task"], at=T0 + 122), now=T0 + 123)
    assert (task_row(theirs["task"], "u1")["stopped_at"], task_row(theirs["task"], "u1")["continues"]) == (
        T0 + 121, 1)
    assert (task_row(theirs["task"], "u2")["stopped_at"], task_row(theirs["task"], "u2")["continues"]) == (None, 0)
    controller.close(user, their_rid, "done", now=T0 + 130)
    assert requirement_row(their_rid, "u2")["state"] == "open"
    controller.decide(user, "No.", their_q, now=T0 + 140)
    with store.connect() as db:
        assert [r["state"] for r in db.execute("SELECT state FROM ade_controller_questions WHERE id = ? ORDER BY "
                                                "user_id", (their_q,))] == ["answered", "open"]
    assert [q["id"] for q in controller.view(them, now=T0 + 150)["questions"]] == [their_q]
    assert controller.alerts("u2", T0 + 150) == [("controller-question:" + their_q, "controller-question")]


def test_the_confirm_switch_is_the_one_in_effect_when_the_plan_was_made(env):
    user = {"id": "u1"}
    own(user["id"])
    controller.change_settings(user, {"account": "default", "confirm_plans": True}, now=T0)
    controller.set_state(user, "on", now=T0)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    controller.change_settings(user, {"confirm_plans": False}, now=T0 + 10)
    rid, rid_after = uid(), uid()
    # made at T0 + 5, while plans waited for the owner's go, and taken after the switch was turned off
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 5), plan(request["id"], "Make the README say hello.", rid=rid_after,
                                                                         at=T0 + 15), now=T0 + 20)
    assert (requirement_row(rid)["confirm"], requirement_row(rid_after)["confirm"]) == (1, 0)


def test_the_cap_counts_the_sessions_open_when_the_start_was_made(env):
    user = {"id": "u1"}
    own(user["id"])
    controller.change_settings(user, {"account": "default", "cap": 1}, now=T0)
    controller.set_state(user, "on", now=T0)
    _, rid = planned(user)
    first = start(rid, at=T0 + 20)
    second = start(rid, at=T0 + 30, session=OTHER)
    take(user, first, second, now=T0 + 40)
    assert task_row(first["task"])["flag"] is None
    assert task_row(second["task"])["flag"] == "one more than the 1 sessions the Manager may have open at once"
    # the first ended at T0 + 45, said in the same page as the next start: not counted against it
    third = start(rid, at=T0 + 50, session="c" * 12)
    take(user, third, tasks_=[{"id": first["task"], "state": "done", "ended_at": T0 + 45},
                              {"id": second["task"], "state": "stopped", "ended_at": T0 + 46}], now=T0 + 60)
    assert task_row(third["task"])["flag"] is None
    # the third, not ended, was open at the next start
    fourth = start(rid, at=T0 + 55, session="d" * 12)
    take(user, fourth, now=T0 + 70)
    assert "one more than the 1 sessions" in task_row(fourth["task"])["flag"]


def test_the_cap_counts_a_session_that_ended_after_the_start_and_not_one_started_after_it(env):
    user = {"id": "u1"}
    own(user["id"])
    controller.change_settings(user, {"account": "default", "cap": 1}, now=T0)
    controller.set_state(user, "on", now=T0)
    _, rid = planned(user)
    first = start(rid, at=T0 + 20)
    take(user, first, now=T0 + 30)
    # the first ended at T0 + 50, after the next start at T0 + 40, said in the same page: it was open at that start
    second = start(rid, at=T0 + 40, session=OTHER)
    take(user, second, tasks_=[{"id": first["task"], "state": "done", "ended_at": T0 + 50}], now=T0 + 60)
    assert task_row(first["task"])["ended_at"] == T0 + 50
    assert task_row(second["task"])["flag"] == "one more than the 1 sessions the Manager may have open at once"
    # a start the record lists before one made earlier: the page is taken in the order of its times, so the earlier
    # one is open at the later one's start, and the later one is past the cap
    later, earlier = start(rid, at=T0 + 200, session="c" * 12), start(rid, at=T0 + 100, session="d" * 12)
    take(user, tasks_=[{"id": second["task"], "state": "done", "ended_at": T0 + 90}], now=T0 + 95)
    take(user, later, earlier, now=T0 + 210)
    assert task_row(earlier["task"])["flag"] is None
    assert task_row(later["task"])["flag"] == "one more than the 1 sessions the Manager may have open at once"


def test_a_start_outside_the_bounds_is_kept_however_many_were_flagged_before(env):
    """No count of flagged sessions makes the app refuse the next one: a start outside the bounds is never refused for
    being outside them, so a record cannot first spend such a count, by continues flagging what it started, say, and
    then start one the app keeps no row of."""
    user = {"id": "u1"}
    setup(user, cap=10)
    _, rid = planned(user)
    first = [start(rid, session=None, at=T0 + 20) for _ in range(10)]
    take(user, *first, now=T0 + 25)
    # ten more, the first ten ended before: within the cap, CAP_MOST
    second = [start(rid, session=None, at=T0 + 40) for _ in range(10)]
    got = take(user, *second, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 30} for a in first],
               now=T0 + 45)
    assert (got["taken"], got["flagged"]) == (10, 0)
    controller.set_state(user, "stopped", now=T0 + 50)
    assert take(user, *[act("continue", a["task"], at=T0 + 60) for a in first + second],
                now=T0 + 65)["flagged"] == 20
    late = start(rid, account=OTHER, session=None, at=T0 + 70)
    assert take(user, late, now=T0 + 80)["flagged"] == 1
    assert task_row(late["task"])["flag"].startswith("started while the Manager was stopped")


def test_past_the_starts_a_day_the_app_takes_a_start_is_refused_within_the_bounds_or_not(env, monkeypatch):
    monkeypatch.setattr(controller, "STARTS_A_DAY", 3)
    user = {"id": "u1"}
    setup(user, cap=10)
    _, rid = planned(user)
    starts = [start(rid, session=None), start(rid, account=OTHER, session=None), start(rid, session=None)]
    over = [start(rid, session=None), start(rid, account=OTHER, session=None)]
    got = take(user, *starts, *over)
    assert (got["taken"], got["flagged"], got["refused"]) == (2, 1, 2)
    assert [task_row(a["task"]) is None for a in over] == [True, True]
    assert [e["payload"]["why"] for e in events("u1", "start.refused")] == ["past the 3 starts a day the app takes"] * 2
    # counted by when the app took them, not by the time a record gave: a day after the app took them, more are taken
    assert take(user, start(rid, session=None, at=T0 + 20), now=T0 + 100 + 86401)["taken"] == 1


def test_no_flood_of_other_actions_leaves_a_start_untaken(env, monkeypatch):
    """The record's other lines have a budget of their own (RECORD_LINES_A_DAY): once spent, a plan, a stop or a
    continue is refused, and a start, outside the bounds or within them, is still taken."""
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", 4)
    user = {"id": "u1"}
    setup(user, cap=10)
    request, rid = planned(user)                # the plan: one line
    a = start(rid, session=None)
    take(user, a)
    assert take(user, *[act("continue", a["task"]) for _ in range(5)])["refused"] == 2
    assert {e["payload"]["why"] for e in events("u1", "continue.refused")} == {"past the 4 lines a day the workspace's record may add to the journal"}
    assert take(user, plan(request["id"], "Make the README say hello."))["refused"] == 1
    late = start(rid, account=OTHER, session=None)
    assert take(user, late)["flagged"] == 1 and task_row(late["task"]) is not None


def test_a_start_that_names_no_task_is_refused(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    a = start(rid, tid="x")
    assert take(user, a)["refused"] == 1
    assert events("u1", "start.refused")[0]["payload"]["why"] == "it names no task"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_controller_tasks").fetchone()[0] == 0


@pytest.mark.parametrize("field,value,why", [
    ("session", "XYZ", "its session is not a session's id"),
    ("repo", "../x", "its repo is not one"),
    ("account", "nobody", "its account is not one"),
    ("model", "", "its model is not one"),
    ("effort", None, "its effort is not one"),
    ("program", None, "its program is not one"),
    ("branch", "-x", "its branch is not one"),
    ("base", "x y", "its base is not one"),
])
def test_a_start_with_a_field_that_is_not_one_is_kept_flagged_and_counted_against_the_cap(env, field, value, why):
    """A start with one field of another form was refused, kept nowhere and
    counted against no cap, so a session the workspace ran (its states listed every page) was missing from the board
    and a second start within the cap broke it unflagged, though fewer than 200 starts were taken that day. Kept, the
    field as none, and flagged; the next start counts it."""
    user = {"id": "u1"}
    setup(user, cap=1)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    a[field] = value
    second = start(rid, session=OTHER, at=T0 + 25)
    assert take(user, a, second) == {"taken": 0, "refused": 0, "flagged": 2, "states": 0}
    kept = task_row(a["task"])
    assert kept[{"session": "session_id"}.get(field, field)] is None and why in kept["flag"]
    assert task_row(second["task"])["flag"] == "one more than the 1 sessions the Manager may have open at once"
    assert events("u1", "start.refused") == []


# ---- stops, continues, questions and states -------------------------------------------------------------------------

def test_a_stop_or_a_continue_of_a_session_the_controller_did_not_start_is_refused(env):
    user = {"id": "u1"}
    setup_plain(user)
    for kind in ("stop", "continue"):
        a = act(kind, uid())
        assert take(user, a)["refused"] == 1
        assert events("u1", kind + ".refused")[0]["payload"]["why"] == "it names no session the Manager started"


def test_another_owner_s_session_or_requirement_named_in_a_record_is_refused(env):
    user, them = {"id": "u1"}, {"id": "u2"}
    setup_plain(user)
    setup_plain(them)
    _, their_rid = planned(them)
    theirs = start(their_rid)
    take(them, theirs)
    for kind in ("stop", "continue"):
        assert take(user, act(kind, theirs["task"]))["refused"] == 1
    assert take(user, question("Whose is it?", requirement=their_rid))["refused"] == 1
    assert task_row(theirs["task"])["continues"] == 0 and task_row(theirs["task"])["stopped_at"] is None
    # nor does a state another owner's record gives for their session change it
    assert take(user, tasks_=[{"id": theirs["task"], "state": "done", "ended_at": T0 + 50}])["states"] == 0
    assert (task_row(theirs["task"])["state"], task_row(theirs["task"])["ended_at"]) == ("starting", None)
    assert events("u1", "task.ended") == [] and events("u2", "task.ended") == []
    assert [e["payload"]["why"] for e in events("u1") if e["kind"].endswith(".refused")] == [
        "it names no session the Manager started", "it names no session the Manager started",
        "it names a requirement the app does not have"]


def test_a_stop_is_recorded_once_and_a_continue_counted_flagged_when_outside_its_bounds(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    a = start(rid)
    take(user, a, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": T0 + 500}])
    assert task_row(a["task"])["waits_until"] == T0 + 500
    early = act("continue", a["task"], at=T0 + 400)
    assert take(user, early, now=T0 + 450)["flagged"] == 1
    assert task_row(a["task"])["flag"] == "continued before its account's reset"
    on_time = act("continue", a["task"], at=T0 + 600)
    assert take(user, on_time, now=T0 + 650)["taken"] == 1
    controller.set_state(user, "stopped", now=T0 + 700)
    late = act("continue", a["task"], at=T0 + 710)
    take(user, late, now=T0 + 720)
    t = task_row(a["task"])
    assert t["continues"] == 3
    assert t["flag"] == "continued before its account's reset; continued while the Manager was stopped"
    stop = act("stop", a["task"], at=T0 + 730)
    take(user, stop, act("stop", a["task"], at=T0 + 740), now=T0 + 750)
    assert task_row(a["task"])["stopped_at"] == T0 + 730
    assert [e["payload"].get("flag") for e in events("u1", "continue")] == [
        "continued before its account's reset", None, "continued while the Manager was stopped"]
    # the second stop of a session stopped already adds no line, no action and no refusal
    assert len(events("u1", "stop")) == 1 and events("u1", "stop.refused") == []
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_controller_actions WHERE kind = 'stop'").fetchone()[0] == 1


def test_a_continue_at_its_reset_is_within_the_bound(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    a = start(rid)
    take(user, a, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": T0 + 500}])
    assert take(user, act("continue", a["task"], at=T0 + 500), now=T0 + 510)["taken"] == 1
    assert task_row(a["task"])["flag"] is None


def test_a_question_is_kept_as_a_model_s_words_three_open_at_most_and_journaled_without_them(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    ids = [uid() for _ in range(4)]
    texts = ["Which branch\u200b?", "Merge it? token sk-ant-api03-" + "B" * 40, "x" * 3000, "A fourth?"]
    got = take(user, *[question(t, qid=q, requirement=rid) for q, t in zip(ids, texts)])
    assert (got["taken"], got["refused"]) == (3, 1)
    with store.connect() as db:
        rows = {r["id"]: dict(r) for r in db.execute("SELECT * FROM ade_controller_questions")}
    assert (rows[ids[0]]["text"], rows[ids[0]]["hidden"]) == ("Which branch?", 1)
    assert rows[ids[1]]["text"].startswith("removed: shaped like") and "B" * 40 not in rows[ids[1]]["text"]
    assert len(rows[ids[2]]["text"].encode()) == shapes.TEXT_MAX and rows[ids[2]]["cut"] == 1
    assert events("u1", "question.refused")[0]["payload"]["why"] == "3 questions are open already"
    assert "branch" not in json.dumps([e["payload"] for e in events("u1")])
    assert [k for k, _ in controller.alerts("u1", T0 + 100) if k.startswith("controller-question:")] == [
        "controller-question:" + q for q in sorted(ids[:3], key=lambda q: (T0 + 30, q))]
    assert take(user, question("Whose?", requirement="d" * 32))["refused"] == 1
    assert take(user, question("\u200b"))["refused"] == 1


def test_a_session_s_state_is_kept_its_end_journaled_once_and_an_ended_one_does_not_come_back(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    a = start(rid)
    take(user, a)
    for state in ("working", "idle", "nonsense"):
        take(user, tasks_=[{"id": a["task"], "state": state}])
    assert task_row(a["task"])["state"] == "idle"
    take(user, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 300}], now=T0 + 310)
    take(user, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 300}], now=T0 + 320)
    take(user, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 330)
    t = task_row(a["task"])
    assert (t["state"], t["ended_at"]) == ("done", T0 + 300)
    # from one ended state to another: kept, its end journaled once all the same
    take(user, tasks_=[{"id": a["task"], "state": "failed", "ended_at": T0 + 400}], now=T0 + 410)
    t = task_row(a["task"])
    assert (t["state"], t["ended_at"]) == ("failed", T0 + 300)
    assert [e["payload"]["state"] for e in events("u1", "task.ended")] == ["done"]


def test_the_controller_s_chat_is_kept_as_the_workspace_names_it(env):
    user = {"id": "u1"}
    setup_plain(user)
    take(user, session=SESSION)
    take(user, session=SESSION)
    take(user, session="not a session")
    with store.connect() as db:
        assert db.execute("SELECT session_id FROM ade_controllers").fetchone()[0] == SESSION
    assert [e["payload"] for e in events("u1", "controller.chat")] == [{"session": SESSION}]


# ---- the sync ---------------------------------------------------------------------------------------------------------

def test_the_sync_reads_the_record_page_by_page_then_sends_the_mirror(env, ws):
    user = auth_user()
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    a = plan(request["id"], "Make the README say hello.")
    ws.pages = [dict(record(a), seq=5, more=True), dict(record(start(a["requirement"])), seq=9)]
    assert controller.sync(user, "http://workspace.test") is True
    assert ws.paths()[:2] == ["/controller/record?since=0", "/controller/record?since=5"]
    assert ws.paths()[2] == "/controller"
    with store.connect() as db:
        c = db.execute("SELECT * FROM ade_controllers").fetchone()
    assert (c["seq"], c["mirrored_version"]) == (9, ws.mirrors[0]["version"])
    assert ws.mirrors[0]["version"] == c["version"]
    # nothing new and the version held: no mirror sent again
    ws.calls.clear()
    assert controller.sync(user, "http://workspace.test") is False
    assert ws.paths() == ["/controller/record?since=9"]


def auth_user():
    return {"id": "u" + secrets.token_hex(4)}


def test_a_workspace_without_the_controller_s_routes_is_left_alone(env, ws, capsys):
    """A 404 to the read alone says nothing: the mirror is asked too, and only a 404 to both marks a workspace
    without the controller's routes, which is left alone, the store as it was."""
    user = auth_user()
    setup_plain(user)
    with store.connect() as db:
        before = dict(controller._row(db, user["id"]))
    ws.routes = False
    assert controller.sync(user, "http://workspace.test") is False
    assert ws.paths() == ["/controller/record?since=0", "/controller"]
    # quietly: the poll asks every 30 seconds, and such a workspace is no failure
    assert capsys.readouterr().err == ""
    with store.connect() as db:
        assert dict(controller._row(db, user["id"])) == before


@pytest.fixture
def agent_without_routes():
    """A workspace agent's app as the workspace runs one (Starlette, with no handler for a path it does not serve),
    without the controller's two routes, behind a real server on this host, for the real workspace_client."""
    import socket
    import threading
    import types
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(request):
        return JSONResponse({"ok": True})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(Starlette(routes=[Route("/health", health)]), host="127.0.0.1", port=port,
                                           log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.025)
    yield types.SimpleNamespace(http=f"http://127.0.0.1:{port}", secret="s", ssl=None)
    server.should_exit = True
    thread.join(5)


def test_a_real_workspace_agent_without_the_routes_is_left_alone(env, agent_without_routes, capsys):
    """A workspace agent without the controller's routes answers a path it does not serve
    with a plain 404, which workspace_client gives as 502 in its own words, "The workspace answered 404."; the app took
    only a 404 carrying a reason for the routes' absence, so such a workspace had both calls recorded as failures, two
    lines said on standard error each poll round, and the owner told. Through the real client and a real agent's
    routing: left alone, nothing recorded, said or told."""
    with pytest.raises(workspace_client.WorkspaceCallError) as e:
        REAL_CALL(agent_without_routes, "GET", "/controller/record?since=0", timeout=5)
    assert (e.value.status, str(e.value)) == (502, "The workspace answered 404.")
    user = auth_user()
    setup_plain(user)
    with store.connect() as db:
        before = dict(controller._row(db, user["id"]))
    assert controller.sync(user, agent_without_routes, call=REAL_CALL) is False
    assert capsys.readouterr().err == ""
    with store.connect() as db:
        assert dict(controller._row(db, user["id"])) == before
    assert controller.alerts(user["id"]) == []


def test_a_404_the_routes_give_with_their_reason_is_a_refusal_said_recorded_and_told(env, ws, capsys):
    """The other way: a 404 carrying a reason is one of the controller's routes refusing (the
    contract has neither answer 404), never the routes' absence, so a 404 with a reason to both is recorded, said and
    told, and the next sync tries again."""
    user = auth_user()
    setup_plain(user)
    ws.fail = ws.fail_put = 404
    assert controller.sync(user, "http://workspace.test") is False
    err = capsys.readouterr().err
    assert "reading the workspace's record failed: The workspace refused the read of its record (404)." in err
    assert "sending the mirror failed: The workspace refused the mirror (404)." in err
    c = controller.view(user)["controller"]
    assert (c["read_error"], c["mirror_error"]) == ("The workspace refused the read of its record (404).",
                                                    "The workspace refused the mirror (404).")
    assert c["read_error_at"] is not None and c["mirror_error_at"] is not None and c["mirrored_version"] is None
    assert any(k.startswith("controller-look") for k, _ in controller.alerts(user["id"]))


def test_a_refusal_in_the_words_of_a_missing_route_is_a_refusal(env, ws, monkeypatch):
    """A 404 carrying a reason is a refusal whatever its words, those of workspace_client for a missing route among
    them: only the client's own 502 is a route's absence."""
    user = auth_user()
    setup_plain(user)

    def call(endpoint, method, path, body=None, timeout=30):
        raise workspace_client.WorkspaceCallError(404, "The workspace answered 404.")
    monkeypatch.setattr(workspace_client, "call", call)
    assert controller.sync(user, "http://workspace.test") is False
    c = controller.view(user)["controller"]
    assert (c["read_error"], c["mirror_error"]) == ("The workspace refused the read of its record (404).",
                                                    "The workspace refused the mirror (404).")


def test_a_read_with_no_route_and_a_mirror_refused_are_both_said_and_recorded(env, ws, capsys):
    user = auth_user()
    setup_plain(user)
    ws.read_route, ws.fail_put = False, 400
    assert controller.sync(user, "http://workspace.test") is False
    err = capsys.readouterr().err
    assert "reading the workspace's record failed: The workspace answered 404." in err
    assert "sending the mirror failed: The workspace refused the mirror (400)." in err
    c = controller.view(user)["controller"]
    assert (c["read_error"], c["mirror_error"]) == ("The workspace answered 404.",
                                                    "The workspace refused the mirror (400).")


@pytest.mark.parametrize("missing", ["read", "mirror"])
def test_a_route_missing_alone_is_a_failure_said_recorded_and_told(env, ws, capsys, missing):
    """A 404 with no refusal in it to one route alone is no workspace without the routes: it is said, recorded and
    told as any other failure, and the other route's call goes through: a
    mirror answered so is not left alone, whatever the read did."""
    user = auth_user()
    setup_plain(user)
    controller.set_state(user, "stopped", now=T0 + 50)
    setattr(ws, {"read": "read_route", "mirror": "put_route"}[missing], False)
    assert controller.sync(user, "http://workspace.test") is (missing == "read")
    err = capsys.readouterr().err
    c = controller.view(user)["controller"]
    if missing == "read":
        assert "reading the workspace's record failed: The workspace answered 404." in err
        assert (c["read_error"], c["mirror_error"], c["mirrored_version"]) == (
            "The workspace answered 404.", None, c["version"])
        assert ws.mirrors[-1]["state"] == "stopped"
    else:
        assert "sending the mirror failed: The workspace answered 404." in err
        assert (c["read_error"], c["mirror_error"], c["mirrored_version"]) == (
            None, "The workspace answered 404.", None)
    assert any(k.startswith("controller-look") for k, _ in controller.alerts(user["id"]))


def test_a_record_that_cannot_be_read_still_lets_the_owner_s_controls_through(env, ws, capsys):
    """The owner's side needs nothing read: a read that fails (anything but a 404) is said, and the mirror is sent all
    the same, so a Stop reaches the workspace while its record route is broken."""
    user = auth_user()
    setup_plain(user)
    ws.fail = 500
    controller.set_state(user, "stopped", now=T0 + 50)
    assert controller.sync(user, "http://workspace.test") is True
    assert "reading the workspace's record failed" in capsys.readouterr().err
    assert ws.mirrors[-1]["state"] == "stopped"
    with store.connect() as db:
        c = controller._row(db, user["id"])
    assert c["mirrored_version"] == c["version"] and c["seq"] == 0 and c["read_at"] is None


def test_a_mirror_that_fails_is_sent_again_at_the_next_sync(env, ws, capsys):
    user = auth_user()
    setup_plain(user)
    ws.fail_put = 502
    assert controller.sync(user, "http://workspace.test") is False
    assert "sending the mirror failed" in capsys.readouterr().err
    with store.connect() as db:
        assert db.execute("SELECT mirrored_version FROM ade_controllers").fetchone()[0] is None
    ws.fail_put = None
    assert controller.sync(user, "http://workspace.test") is True


def test_a_workspace_whose_count_went_back_is_read_again_from_the_start(env, ws):
    user = auth_user()
    setup_plain(user)
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET seq = 40")
    request = controller.add_request(user, REQUEST, now=T0)
    ws.pages = [{"seq": 3, "more": False, "actions": [], "tasks": []},
                dict(record(plan(request["id"], "Make the README say hello.")), seq=3)]
    controller.sync(user, "http://workspace.test")
    assert ws.paths()[:2] == ["/controller/record?since=40", "/controller/record?since=0"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_controller_requirements").fetchone()[0] == 1


def test_one_sync_per_account_at_a_time(env, ws):
    user = auth_user()
    setup_plain(user)
    controller._syncing.add(user["id"])
    try:
        assert controller.sync(user, "http://workspace.test") is False
        assert ws.calls == [] and user["id"] in controller._again
    finally:
        controller._syncing.discard(user["id"])
        controller._again.discard(user["id"])


def test_a_sync_asked_for_while_one_runs_is_run_by_it_once_more_when_it_is_done(env, ws, monkeypatch):
    """A Stop pressed during a sync is sent right after it, not at the poll's next round."""
    user = auth_user()
    setup_plain(user)
    real, rounds = controller._sync, []

    def one_round(user_, endpoint, call):
        rounds.append(len(rounds))
        sent = real(user_, endpoint, call)
        if len(rounds) == 1:
            # the owner presses Stop once this round has sent its mirror: its sync is asked for, and returns at once
            controller.set_state(user, "stopped", now=T0 + 50)
            assert controller.sync(user, "http://workspace.test") is False
        return sent
    monkeypatch.setattr(controller, "_sync", one_round)
    assert controller.sync(user, "http://workspace.test") is True
    assert rounds == [0, 1] and [m["state"] for m in ws.mirrors] == ["on", "stopped"]
    assert user["id"] not in controller._syncing and user["id"] not in controller._again
    # a sync that fails lets the next one run
    monkeypatch.setattr(controller, "_sync", lambda *a: (_ for _ in ()).throw(RuntimeError("broken")))
    with pytest.raises(RuntimeError):
        controller.sync(user, "http://workspace.test")
    assert user["id"] not in controller._syncing


def test_the_mirror_carries_the_owner_s_words_and_controls_and_the_packet(env, ws):
    user = auth_user()
    setup_plain(user)
    request, rid = planned(user)
    unplanned = controller.add_request(user, "Second request, still unplanned.", now=T0 + 6)
    done = controller.add_request(user, "Third, planned and closed.", now=T0 + 7)
    rid3 = uid()
    take(user, plan(done["id"], "Third, planned and closed.", rid=rid3))
    controller.close(user, rid3, "done", now=T0 + 8)
    controller.hold(user, rid, True, now=T0 + 9)
    qid = uid()
    take(user, question("Which?", qid=qid, requirement=rid))
    decision = controller.decide(user, "Main.", qid, now=T0 + 40)
    # a requirement closed more than a week ago is neither in the mirror nor in the view
    old = controller.add_request(user, "Fourth, closed long ago.", now=T0 - 9 * 86400)
    rid4 = uid()
    take(user, plan(old["id"], "Fourth, closed long ago.", rid=rid4, at=T0 - 9 * 86400 + 5))
    controller.close(user, rid4, "done", now=int(time.time()) - controller.KEPT_CLOSED - 60)
    assert rid4 not in [r["id"] for r in controller.view(user)["requirements"]]
    controller.sync(user, "http://workspace.test")
    m = ws.mirrors[-1]
    # in the packet's order: those not yet planned, those still worked on, and those whose requirements closed in
    # the last week
    assert [r["n"] for r in m["requests"]] == [2, 1, 3] and m["more_requests"] == 0
    assert m["requests"][1]["text"] == REQUEST and m["requests"][1]["digest"] == request["digest"]
    assert m["packet_error"] is None
    assert [d["text"] for d in m["decisions"]] == ["Main."] and m["decisions"][0]["id"] == decision["id"]
    assert {r["id"]: (r["state"], r["hold"]) for r in m["requirements"]} == {rid: ("open", True), rid3: ("done", False)}
    assert m["questions"] == [{"id": qid, "state": "answered", "decision": decision["id"]}]
    assert (m["state"], m["account"], m["cap"], m["models"], m["efforts"], m["chat"], m["confirm_plans"],
            m["end_all"]) == ("on", "default", 2, ["opus"], ["max"], {"model": "opus", "effort": "xhigh"}, False, 0)
    data = base64.b64decode(m["packet_b64"])
    assert m["packet_digest"] == shapes.tagged_digest(controller.PACKET_TAG, data)
    assert len(json.dumps(m, ensure_ascii=False).encode()) <= controller.MIRROR_MAX
    assert unplanned["id"] in data.decode()
    sent = controller.packet_of(user)
    assert sent["sent"] is True and sent["digest"] == m["packet_digest"] and sent["packet"] == json.loads(data)


def test_the_mirror_s_lists_are_cut_where_their_bytes_end_and_counted(env, ws, monkeypatch):
    monkeypatch.setattr(controller, "MIRROR_REQUESTS_BYTES", 600)
    monkeypatch.setattr(controller, "MIRROR_DECISIONS_BYTES", 300)
    user = auth_user()
    setup_plain(user)
    for i in range(6):
        controller.add_request(user, f"Request number {i} " + "z" * 100, now=T0 + i)
        controller.decide(user, f"Decision {i} " + "w" * 60, now=T0 + i)
    with store.connect() as db:
        body, _, _ = controller.mirror_body(db, user["id"], T0 + 50)
    assert 0 < len(body["requests"]) < 6 and body["more_requests"] == 6 - len(body["requests"])
    assert body["requests"][0]["n"] == 6
    assert 0 < len(body["decisions"]) < 6 and body["more_decisions"] == 6 - len(body["decisions"])
    assert len(json.dumps({"requests": body["requests"], "more_requests": body["more_requests"]},
                          ensure_ascii=False).encode()) <= 600


def test_the_owners_the_poll_syncs(env):
    """Every owner with a controller, one that is off and whose workspace was told so
    among them, since only a read of the record finds a session its workspace starts while it is off."""
    with store.connect() as db:
        assert controller.users_to_poll(db) == []
    on, off, told = {"id": "u-on"}, {"id": "u-off"}, {"id": "u-told"}
    setup_plain(on)
    controller.change_settings(off, {"cap": 3}, now=T0)
    controller.change_settings(told, {"cap": 3}, now=T0)
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET mirrored_version = version WHERE user_id = 'u-told'")
        assert controller.users_to_poll(db) == ["u-off", "u-on", "u-told"]


def test_the_app_s_poll_syncs_the_controller_of_an_owner_with_no_agent(client, owner, ws, monkeypatch):
    h, user = owner
    setup_plain(user)
    monkeypatch.setattr(agents, "_slow_work", lambda user, endpoint: None)
    agents.poll_all()
    assert "/controller/record?since=0" in ws.paths() and "/controller" in ws.paths()


def test_a_controller_sync_that_raises_stops_nothing_of_the_agents_sync(client, owner, ws, monkeypatch, capsys):
    """The poll syncs an owner's controller, then their agents (poll_all): a controller's sync that raises is said and
    stops nothing of the agents' sync. It goes through the poll:
    agents.sync no longer reaches the controller's sync, so a test of it could not fail for what its name
    says."""
    h, user = owner
    setup_plain(user)
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   ("a1a2a3a4a5a6", user["id"], "Tidy", "Tidy up.", T0, T0))

    def broken(user, endpoint):
        raise RuntimeError("broken")
    monkeypatch.setattr(controller, "sync", broken)
    monkeypatch.setattr(agents, "_slow_work", lambda user, endpoint: None)
    agents.poll_all()
    assert any(p.startswith("/agents") for p in ws.paths())
    assert "agents: the controller's sync failed: RuntimeError: broken" in capsys.readouterr().err


# ---- End all ----------------------------------------------------------------------------------------------------------

def test_end_all_stops_the_controller_and_tells_the_workspace_and_stops_nothing_itself(client, owner, ws):
    """The workspace stops each session its controller started, by its own record, when the mirror's end_all count
    moves on; the app stops none by an id a record gave, which could name any of the owner's sessions, the owner's
    own among them."""
    h, user = owner
    setup_plain(user)
    _, rid = planned(user)
    running, ended = start(rid, session=SESSION), start(rid, session=OTHER, at=T0 + 21)
    take(user, running, ended, tasks_=[{"id": ended["task"], "state": "done", "ended_at": T0 + 22}])
    r = client.post(C + "/end-all", headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["controller"]["state"], r.json()["controller"]["end_all"]) == ("stopped", 1)
    assert set(r.json()) == {"controller", "requests", "requirements", "other_sessions", "questions", "decisions",
                             "holder"}
    assert ws.synced == [user["id"]] and ws.stopped == []
    assert [p for p in ws.paths() if "/sessions/" in p] == []
    line = events(user["id"], "controller.end_all")[0]["payload"]
    assert line == {"from": "on", "to": "stopped", "sessions": 1}
    with store.connect() as db:
        body, _, _ = controller.mirror_body(db, user["id"])
    assert (body["state"], body["end_all"]) == ("stopped", 1)
    # pressed again, while stopped: counted again, and the controller stays stopped
    again = client.post(C + "/end-all", headers=h).json()["controller"]
    assert (again["state"], again["end_all"]) == ("stopped", 2)


# ---- the packet -------------------------------------------------------------------------------------------------------

def test_the_packet_s_form_is_built_from_the_store_alone(env):
    user = {"id": "u1"}
    setup_plain(user)
    request, rid = planned(user)
    waiting = controller.add_request(user, "Second:\x1b[31m red\u202e", now=T0 + 6)
    ended, a = start(rid, at=T0 + 15, session="e" * 12), start(rid)
    qid = uid()
    take(user, ended, a, question("Which branch?", qid=qid, requirement=rid),
         tasks_=[{"id": ended["task"], "state": "done", "ended_at": T0 + 18}])
    controller.decide(user, "Use main.", now=T0 + 50)
    with store.connect() as db:
        data, digest = controller.build_packet(db, "u1", T0 + 60)
    assert digest == hashlib.sha256(b"proofpane-controller/1\x00" + data).hexdigest()
    packet = json.loads(data)
    assert canonicalize(packet) == data.decode() or canonicalize(packet) == data
    assert (packet["schema"], packet["header"]) == ("ade.controller.v1", controller.HEADER)
    assert re.fullmatch(r"[0-9a-f]{32}", packet["nonce"])
    ins = packet["instructions"]
    assert ins["by"] == "owner"
    assert [r["n"] for r in ins["requests"]] == [2, 1]            # not yet planned first, then the live ones
    assert ins["requests"][0]["text"] == "Second:\\x1b[31m red\\u202e"
    assert ins["requests"][1]["text"] == tasks.owner_text(REQUEST)
    assert [d["text"] for d in ins["decisions"]] == ["Use main."]
    info = packet["information"]
    req = info["requirements"][0]
    assert (req["id"], req["request"], req["activity"]) == (rid, 1, "waiting")
    assert req["title"] == {"text": "Say hello", "named_by": "model"}
    assert req["words"] == {"text": "Make the README say hello.", "by": "owner"}
    assert req["repos"] == [{"text": "demo", "named_by": "model"}]
    # open sessions first, then the ended ones
    assert [t["id"] for t in req["tasks"]] == [a["task"], ended["task"]] and req["more_tasks"] == 0
    assert req["tasks"][0]["repo"]["named_by"] == "model"
    # the names a start came with, the model and the effort among them, are a model's
    assert (req["tasks"][0]["model"], req["tasks"][0]["effort"]) == ({"text": "opus", "named_by": "model"},
                                                                      {"text": "max", "named_by": "model"})
    assert info["open_questions"] == [{"by": "model", "id": qid, "requirement": rid, "text": "Which branch?"}]
    # no account: the model is not told which account it runs on
    assert packet["controller"] == {"state": "on", "cap": 2, "models": ["opus"], "efforts": ["max"],
                                    "confirm_plans": False}
    assert "default" not in json.dumps(packet["controller"]) and '"account"' not in text_of(packet)
    # nothing of a session's words, a provider session id, a folder or a credential
    text = data.decode()
    for word in ("transcript", "provider_session", "CLAUDE_CONFIG_DIR", ".claude", "token"):
        assert word not in text
    assert waiting["id"] in text


def text_of(packet):
    return json.dumps(packet)


def test_the_packet_gives_the_requests_not_yet_planned_before_those_still_worked_on(env, monkeypatch):
    user = {"id": "u1"}
    setup_plain(user)
    older = controller.add_request(user, "First, not yet planned.", now=T0)
    newer = controller.add_request(user, "Second, planned and open.", now=T0 + 1)
    take(user, plan(newer["id"], "Second, planned and open."))
    with store.connect() as db:
        ins = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["instructions"]
    assert [r["n"] for r in ins["requests"]] == [1, 2]
    # with room for one of them, the one not yet planned is kept and the other counted
    with store.connect() as db:
        one = len(canonicalize({"by": "owner", "requests": [ins["requests"][0]], "more_requests": 1,
                                "decisions": [], "more_decisions": 0}))
    monkeypatch.setitem(controller.BUDGETS, "instructions", one)
    with store.connect() as db:
        ins = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["instructions"]
    assert ([r["id"] for r in ins["requests"]], ins["more_requests"]) == ([older["id"]], 1)


def test_a_list_past_its_budget_is_cut_and_counted(env, monkeypatch):
    monkeypatch.setitem(controller.BUDGETS, "observed", 1200)
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    rids = [uid() for _ in range(8)]
    take(user, *[plan(request["id"], "Make the README say hello.", rid=r) for r in rids])
    with store.connect() as db:
        info = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["information"]
    kept = len(info["requirements"])
    assert 0 < kept < 8 and info["more_requirements"] == 8 - kept
    assert len(canonicalize({k: info[k] for k in ("requirements", "more_requirements", "other_sessions",
                                                   "more_other_sessions")})) <= 1200


def test_the_packet_shows_the_sessions_started_for_no_requirement_it_has(env):
    user = {"id": "u1"}
    setup_plain(user)
    stray = start(None, session=OTHER)
    take(user, stray)
    with store.connect() as db:
        info = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["information"]
    assert [t["id"] for t in info["other_sessions"]] == [stray["task"]] and info["other_sessions"][0]["flagged"]


def test_a_requirement_s_sessions_and_the_stray_ones_past_their_count_are_cut_open_ones_first_and_counted(
        env, monkeypatch):
    monkeypatch.setattr(controller, "SHOWN_TASKS", 2)
    user = {"id": "u1"}
    setup(user, cap=10)
    _, rid = planned(user)
    starts = [start(rid, session=None, at=T0 + 20 + i) for i in range(3)]
    strays = [start(None, session=None, at=T0 + 30 + i) for i in range(3)]
    take(user, *starts, *strays, tasks_=[{"id": starts[0]["task"], "state": "done", "ended_at": T0 + 40}])
    with store.connect() as db:
        info = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["information"]
    req = info["requirements"][0]
    assert [t["id"] for t in req["tasks"]] == [starts[1]["task"], starts[2]["task"]] and req["more_tasks"] == 1
    assert [t["id"] for t in info["other_sessions"]] == [s["task"] for s in strays[:2]]
    assert info["more_other_sessions"] == 1


def test_the_packet_before_any_was_sent_is_built_now_and_marked_so(client, owner):
    h, user = owner
    assert client.get(C + "/packet", headers=h).status_code == 404
    setup_plain(user)
    got = client.get(C + "/packet", headers=h).json()
    assert got["sent"] is False and got["at"] is None and got["packet"]["schema"] == "ade.controller.v1"


# ---- the board's rows -------------------------------------------------------------------------------------------------

ROW_KEYS = {"kind", "id", "title", "parent", "phase", "state", "order", "progress", "account", "session", "created_at",
            "started_at", "ended_at", "waits_until", "events", "more_events"}


def test_the_board_s_rows_are_in_section_5_s_shape(client, owner):
    h, user = owner
    setup_plain(user)
    request = controller.add_request(user, "Line one of the owner's\nwords.\n\nLong " + "w" * 200, now=T0)
    rid_run, rid_held, rid_plan, rid_wait, rid_done, rid_idle = (uid() for _ in range(6))
    for rid in (rid_run, rid_held, rid_plan, rid_wait, rid_done, rid_idle):
        take(user, plan(request["id"], "Line one of the owner's\nwords.", rid=rid, title="The controller's title"))
    controller.change_settings(user, {"cap": 5}, now=T0 + 15)
    run, wait, idle = start(rid_run), start(rid_wait, session=OTHER), start(rid_idle, session="c" * 12)
    held_wait = start(rid_held, session="d" * 12)
    take(user, run, wait, idle, held_wait, tasks_=[{"id": run["task"], "state": "working"},
                                                   {"id": wait["task"], "state": "waiting", "waits_until": T0 + 900},
                                                   {"id": idle["task"], "state": "idle"},
                                                   {"id": held_wait["task"], "state": "waiting",
                                                    "waits_until": T0 + 800}])
    controller.hold(user, rid_held, True, now=T0 + 50)
    controller.close(user, rid_done, "done", now=T0 + 60)
    rows = controller.board_rows(user["id"], T0 + 100)
    assert all(set(r) == ROW_KEYS for r in rows), [set(r) ^ ROW_KEYS for r in rows]
    by_id = {r["id"]: r for r in rows}
    req = by_id[rid_run]
    assert (req["kind"], req["parent"], req["phase"], req["state"], req["progress"], req["account"],
            req["session"]) == ("requirement", None, "now", "running", {"done": 0, "of": 1},
                                {"kind": "claude", "id": "default"}, SESSION)
    assert req["title"] == "Line one of the owner's words."                   # the owner's words, one line
    assert "controller's title" not in json.dumps(rows)
    # a session's row is the controller's task's, and names the workspace session it ran in
    sess = by_id[run["task"]]
    assert (sess["kind"], sess["parent"], sess["phase"], sess["state"], sess["title"], sess["session"]) == (
        "session", rid_run, "now", "working", "demo on pp/abc", SESSION)
    assert (by_id[rid_held]["phase"], by_id[rid_held]["state"], by_id[rid_held]["waits_until"]) == ("next", "held",
                                                                                                    None)
    assert (by_id[rid_plan]["phase"], by_id[rid_plan]["state"], by_id[rid_plan]["progress"]) == ("next", "planned",
                                                                                                 None)
    assert (by_id[rid_wait]["phase"], by_id[rid_wait]["state"], by_id[rid_wait]["waits_until"]) == (
        "next", "waiting for a reset", T0 + 900)
    assert (by_id[wait["task"]]["phase"], by_id[wait["task"]]["waits_until"]) == ("next", T0 + 900)
    assert (by_id[rid_done]["phase"], by_id[rid_done]["ended_at"]) == ("history", T0 + 60)
    assert (by_id[rid_idle]["phase"], by_id[rid_idle]["state"]) == ("now", "idle")
    assert (by_id[idle["task"]]["phase"], by_id[idle["task"]]["state"]) == ("now", "idle")
    nexts = [r for r in rows if r["phase"] == "next"]
    assert sorted(r["order"] for r in nexts) == list(range(1, len(nexts) + 1))
    assert all(r["order"] is None for r in rows if r["phase"] != "next")
    assert [r["phase"] for r in rows] == sorted((r["phase"] for r in rows), key=["now", "next", "history"].index)
    assert [e["words"] for e in req["events"]] == ["The Manager planned it from request 1",
                                                   f"The Manager started session {SESSION}"]
    # the board's route gives the same rows
    got = client.get(C + "/board", headers=h).json()
    assert isinstance(got["at"], int) and [r["id"] for r in got["rows"]]


def test_a_row_s_events_are_its_newest_fifty_the_rest_counted(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    for i in range(50):                     # with its plan, 51 lines
        controller.hold(user, rid, i % 2 == 0, now=T0 + 20 + i)
    row = next(r for r in controller.board_rows("u1", T0 + 100) if r["id"] == rid)
    assert (len(row["events"]), row["more_events"]) == (50, 1)
    assert row["events"][-1]["words"] == "You released it"


# ---- the alerts and their words ---------------------------------------------------------------------------------------

def test_the_controller_s_alerts_reach_the_notifier_in_fixed_words(env):
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    qid = uid()
    take(user, question("Secret plan: wipe prod?", qid=qid), start(rid, account=OTHER))
    items = push.items_for("u1", [], T0 + 100)
    kinds = {key.split(":")[0]: notice for key, notice in items}
    assert set(kinds) >= {"controller-question", "controller-look"}
    for key, notice in items:
        if key.startswith("controller-"):
            text = json.dumps([notice.title, notice.body, notice.url])
            assert "wipe" not in text and "Secret" not in text
    assert push.WORDS["controller-question"] == ("The Manager asks you something",
                                                 "Open the control view to read it.", "/app/")
    assert push.WORDS["controller-look"] == ("The Manager needs a look", "Open the control view.", "/app/")


def test_the_journal_s_words_are_the_app_s_own(client, owner):
    h, user = owner
    setup_plain(user)
    _, rid = planned(user)
    take(user, start(rid, account=OTHER))
    got = client.get(C + "/journal?limit=3", headers=h).json()["events"]
    assert [e["kind"] for e in got] == ["start", "plan", "request"]
    assert got[0]["words"] == (f"The Manager started session {SESSION}, outside the Manager's bounds: started "
                               "on another account than the Manager's")
    older = client.get(f"{C}/journal?before={got[-1]['seq']}", headers=h).json()["events"]
    assert [e["kind"] for e in older][:1] == ["controller.on"]


def test_a_page_holding_more_than_the_app_reads_has_the_rest_counted_and_told(env, monkeypatch):
    monkeypatch.setattr(controller, "ACTIONS_A_PAGE", 3)
    user = {"id": "u1"}
    setup(user, cap=10)
    _, rid = planned(user)
    starts = [start(rid, session=None) for _ in range(4)]
    take(user, *starts[:3])
    take(user, starts[3])
    # five refused plans in one page: three read and refused, two not read
    bad = [{"id": uid(), "kind": "plan", "at": T0 + 40} for _ in range(5)]
    assert take(user, *bad, now=T0 + 200)["refused"] == 5
    assert len(events("u1", "plan.refused")) == 3
    cut = events("u1", "record.refused")
    assert [e["payload"] for e in cut] == [{"why": "a page of the workspace's record held 5 actions and 0 sessions' "
                                                   "states, past the 3 of each the app reads", "skipped": 0,
                                            "actions": 2, "states": 0}]
    assert look(T0 + 200) in controller.alerts("u1", T0 + 300)
    assert controller.words_of({"kind": "record.refused", "payload": json.dumps(cut[0]["payload"])}) == (
        "The app did not read all of a page of the workspace's record: a page of the workspace's record held 5 "
        "actions and 0 sessions' states, past the 3 of each the app reads")
    # four sessions' states in one page: three read, and no action refused: told in a line of its own, which is no
    # refusal, at most one a day (each such page once spent a refusal)
    with store.connect() as db:
        refused_at = controller._row(db, "u1")["refused_at"]
        version = controller._row(db, "u1")["version"]
    got = take(user, tasks_=[{"id": s["task"], "state": "working"} for s in starts], now=T0 + 400)
    with store.connect() as db:
        assert controller._row(db, "u1")["refused_at"] == refused_at == T0 + 200
    assert got["states"] == 3 and [task_row(s["task"])["state"] for s in starts].count("working") == 3
    assert len(events("u1", "record.refused")) == 1
    unread = events("u1", "record.states_unread")
    assert [e["payload"] for e in unread] == [{"states": 4, "unread": 1}]
    assert controller.words_of({"kind": "record.states_unread", "payload": json.dumps(unread[0]["payload"])}) == (
        "The app did not read every session's state a page of the workspace's record gave: it held 4, 1 past what "
        "the app reads")
    # the same page again, nothing new: no line, no refusal, and the version stays (the mirror holds none of it)
    with store.connect() as db:
        version = controller._row(db, "u1")["version"]
    assert take(user, tasks_=[{"id": s["task"], "state": "working"} for s in starts], now=T0 + 500)["states"] == 0
    assert len(events("u1", "record.states_unread")) == 1 and len(events("u1", "record.refused")) == 1
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] == version
    # more than a day later it is told again
    take(user, tasks_=[{"id": s["task"], "state": "working"} for s in starts], now=T0 + 401 + controller.TOLD)
    assert len(events("u1", "record.states_unread")) == 2
    # a session's state past what the app reads is no action: told, not counted as refused
    with store.connect() as db:
        assert controller._row(db, "u1")["refused"] == 3 + 2


def test_a_page_of_the_journal_or_of_the_requests_is_at_most_a_hundred(client, owner):
    h, user = owner
    setup_plain(user)
    for i in range(105):
        controller.add_request(user, f"Request {i}", now=T0 + i)
    assert len(client.get(C + "/journal?limit=1000", headers=h).json()["events"]) == 100
    assert len(client.get(C + "/requests?limit=1000", headers=h).json()["requests"]) == 100
    assert len(client.get(C + "/requests", headers=h).json()["requests"]) == 20
    assert len(client.get(C + "/journal?limit=0", headers=h).json()["events"]) == 1


def test_a_controller_action_s_line_is_dated_when_it_happened_and_kept_in_the_order_recorded(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    request = controller.add_request(user, REQUEST, now=T0 + 5)
    rid, qid = uid(), uid()
    a, late = start(rid, at=T0 + 20), start(rid, at=T0 + 100 + 1000)
    controller.take("u1", record(plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 10), a,
                                 act("stop", a["task"], at=T0 + 30), act("continue", a["task"], at=T0 + 40),
                                 question("Which?", qid=qid, requirement=rid, at=T0 + 50), late,
                                 {"id": uid(), "kind": "plan", "at": T0 + 60}), now=T0 + 100)
    got = [(e["kind"], e["at"]) for e in events("u1") if e["kind"] not in ("controller.settings", "controller.on")]
    assert got == [("request", T0 + 5), ("plan", T0 + 10), ("start", T0 + 20), ("stop", T0 + 30),
                   ("continue", T0 + 40), ("question", T0 + 50),
                   # a refusal is dated when the app refused it, and taken in the order of the time its action gives
                   ("plan.refused", T0 + 100),
                   # an action dated more than a minute past the app's clock is dated when the app took it
                   ("start", T0 + 100)]
    assert task_row(late["task"])["started_at"] == T0 + 100
    # the owner holds it, then the app takes a start the workspace made before the hold: the log keeps the order the
    # app recorded them, the board's events the order they happened
    controller.hold(user, rid, True, now=T0 + 200)
    b = start(rid, at=T0 + 150)
    take(user, b, now=T0 + 210)
    assert [e["kind"] for e in controller.journal_of(user, limit=2)["events"]] == ["start", "hold"]
    row = next(r for r in controller.board_rows("u1", T0 + 300) if r["id"] == rid)
    assert [(e["kind"], e["at"]) for e in row["events"]][-2:] == [("start", T0 + 150), ("hold", T0 + 200)]


def test_a_session_s_end_is_never_before_its_start(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid, at=T0 + 20), start(rid, at=T0 + 30)
    take(user, a, now=T0 + 100)
    # an end the workspace dates before the start: kept at the start, and its line too
    take(user, b, tasks_=[{"id": a["task"], "state": "failed", "ended_at": T0 + 5},
                          {"id": b["task"], "state": "done", "ended_at": T0 + 6}], now=T0 + 110)
    assert task_row(a["task"])["ended_at"] == T0 + 20 and task_row(b["task"])["ended_at"] == T0 + 30
    ended = {e["task_id"]: e["at"] for e in events("u1", "task.ended")}
    assert ended == {a["task"]: T0 + 20, b["task"]: T0 + 30}
    # an end the workspace dates after the start is kept as given
    c = start(rid, at=T0 + 40)
    take(user, c, now=T0 + 120)
    take(user, tasks_=[{"id": c["task"], "state": "stopped", "ended_at": T0 + 45}], now=T0 + 130)
    assert task_row(c["task"])["ended_at"] == T0 + 45
    assert [e["at"] for e in events("u1", "task.ended") if e["task_id"] == c["task"]] == [T0 + 45]


def test_each_kind_of_journal_line_has_the_app_s_own_words(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    request, rid = planned(user)
    a, b = start(rid), start(rid, session=None, account=OTHER)
    take(user, a, b, now=T0 + 110)
    take(user, act("stop", a["task"], at=T0 + 115), now=T0 + 120)
    take(user, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 121}, {"id": b["task"], "state": "working"}],
         now=T0 + 125)
    qid = uid()
    take(user, question("Which branch?", qid=qid, requirement=rid, at=T0 + 126), now=T0 + 130)
    controller.decide(user, "Use main.", qid, now=T0 + 131)
    controller.decide(user, "Keep it short.", now=T0 + 132)
    controller.change_settings(user, {"confirm_plans": True}, now=T0 + 132)
    take(user, plan(request["id"], "Make the README say hello.", at=T0 + 132), now=T0 + 132)
    controller.go(user, request["id"], now=T0 + 133)
    controller.hold(user, rid, True, now=T0 + 134)
    controller.hold(user, rid, False, now=T0 + 135)
    take(user, {"id": uid(), "kind": "plan", "at": T0 + 136}, now=T0 + 137)
    controller.take("u1", record(session="cdef01234567"), now=T0 + 138)
    controller.set_state(user, "stopped", now=T0 + 140)
    take(user, act("continue", b["task"], at=T0 + 141), now=T0 + 142)
    controller.close(user, rid, "done", now=T0 + 143)
    controller.end_all(user, now=T0 + 144)
    controller.set_state(user, "off", now=T0 + 145)
    take(user, tasks_=[], now=T0 + 146)
    take(user, tasks_=[{"id": b["task"], "state": "working"}], now=T0 + 147)
    got = [e["words"] for e in reversed(controller.journal_of(user)["events"])]
    assert got == [
        "You changed the Manager's settings: account, cap",
        "You turned the Manager on",
        "You sent request 1",
        "The Manager planned it from request 1",
        f"The Manager started session {SESSION}",
        "The Manager started a session, outside the Manager's bounds: started on another account than the "
        "Manager's",
        "The Manager stopped the session",
        "The session ended (done)",
        "The Manager asked you a question",
        "You answered a question of the Manager's",
        "You recorded decision 2",
        "You changed the Manager's settings: confirm_plans",
        "The Manager planned it from request 1",
        "You gave the go on the plan of request 1",
        "You held it",
        "You released it",
        "The app did not take a plan of the Manager's: it names no requirement",
        "The Manager's chat is session cdef01234567",
        "You stopped the Manager",
        "The Manager continued the session after its account's reset, outside the Manager's bounds: continued "
        "while the Manager was stopped",
        "You closed it as done",
        "You pressed End all: the Manager is stopped, its open sessions (1) to be stopped",
        "The Manager was turned off",
        "The session ended (failed): the workspace's record no longer listed it as open",
        "The session is open again (working): the workspace's record listed it as open after the app had ended it for "
        "no longer listing it",
    ]


def test_a_journal_line_is_one_of_its_kinds_and_holds_no_more_than_its_bytes(env):
    with store.connect() as db:
        with pytest.raises(ValueError):
            controller.journal(db, "u1", "anything", {})
        with pytest.raises(ValueError):
            controller.journal(db, "u1", "request", [])
        with pytest.raises(ValueError):
            controller.journal(db, "u1", "request", {"x": "y" * 2048})
        assert controller.journal(db, "u1", "request", {"x": "y" * 2000}) > 0


# ---- secret shapes, the packet and the mirror, times and budgets, and the sync ---------------------------------------

def test_a_start_naming_a_secret_shaped_model_is_kept_as_the_shape_s_name_and_the_mirror_still_goes(env, ws):
    """A name a record gives is checked for a secret's shape when it arrives, kept as the shape's name, and
    flagged, so it can neither reach the store, the journal or the packet as itself nor stop the mirror."""
    user = auth_user()
    setup_plain(user)
    _, rid = planned(user)
    a = start(rid, model="sk-ant-x", effort="max")
    assert take(user, a)["flagged"] == 1
    removed = "removed: shaped like an Anthropic key (sk-ant-)"
    t = task_row(a["task"])
    assert t["model"] == removed
    assert t["flag"] == ("started with a model the owner did not allow; named something shaped like a secret, its "
                         "model")
    assert events(user["id"], "start")[0]["payload"]["model"] == removed
    controller.set_state(user, "stopped", now=T0 + 200)
    assert controller.sync(user, "http://workspace.test") is True
    m = ws.mirrors[-1]
    assert (m["state"], m["packet_error"]) == ("stopped", None) and m["packet_b64"]
    for where in (json.dumps(controller.view(user)), json.dumps(events(user["id"])),
                  base64.b64decode(m["packet_b64"]).decode()):
        assert "sk-ant-x" not in where


def test_a_plan_s_part_of_a_request_cannot_carry_a_secret_s_shape_the_request_does_not(env, monkeypatch):
    """In a plan: a part of a request that starts inside a word ("sk-ant-thing", in "Xsk-ant-thing")
    is not whole, and a whole part found shaped like a secret, which no shape the app knows can be where the request
    is not, is refused all the same."""
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, "Rename the field Xsk-ant-thing to Y.", now=T0)
    assert take(user, plan(request["id"], "sk-ant-thing to Y."))["refused"] == 1
    assert events("u1", "plan.refused")[0]["payload"]["why"] == "its words are " + NOT_WHOLE
    hello = controller.add_request(user, "Say hello.\n\nThen stop.", now=T0 + 1)
    monkeypatch.setattr(tasks, "secret_shape", lambda text: "a test shape" if text == "Say hello." else None)
    assert take(user, plan(hello["id"], "Then stop.", acceptance="Say hello."))["refused"] == 1
    assert take(user, plan(hello["id"], "Say hello."))["refused"] == 1
    assert [e["payload"]["why"] for e in events("u1", "plan.refused")][1:] == [
        "its acceptance is shaped like a test shape", "its words are shaped like a test shape"]


def test_a_packet_that_cannot_be_built_leaves_the_mirror_going_with_why(env, ws, monkeypatch):
    """The owner's controls never wait on the packet. A mirror whose packet cannot be built is sent without
    it, saying why, and the page is told since when."""
    user = auth_user()
    setup_plain(user)
    real = controller.build_packet
    monkeypatch.setattr(controller, "build_packet", lambda db, user_id, now=None: (_ for _ in ()).throw(
        controller.PacketError("The packet does not fit.")))
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    assert controller.sync(user, "http://workspace.test") is True
    m = ws.mirrors[-1]
    assert (m["packet_b64"], m["packet_digest"], m["packet_error"], m["state"]) == (None, None,
                                                                                   "The packet does not fit.", "on")
    c = controller.view(user, now=T0 + 1000)["controller"]
    assert (c["packet_error"], c["packet_error_at"]) == ("The packet does not fit.", T0 + 1000)
    assert c["packet_at"] is None and c["mirrored_version"] == c["version"]
    with pytest.raises(agents.AgentError) as refused:
        controller.packet_of(user)
    assert refused.value.status == 409
    # the same error again, later: since when it began is kept
    clock[0] = T0 + 2000
    controller.set_state(user, "stopped", now=T0 + 300)
    controller.sync(user, "http://workspace.test")
    assert controller.view(user, now=T0 + 2000)["controller"]["packet_error_at"] == T0 + 1000
    # built again: sent, the error gone
    monkeypatch.setattr(controller, "build_packet", real)
    controller.set_state(user, "on", now=T0 + 400)
    controller.sync(user, "http://workspace.test")
    c = controller.view(user)["controller"]
    assert (c["packet_error"], c["packet_error_at"]) == (None, None) and c["packet_at"] is not None
    sent = controller.packet_of(user)
    assert sent["sent"] is True and (sent["error"], sent["error_at"]) == (None, None)
    # a packet sent before, and none built since: shown with why it is not the store's now
    monkeypatch.setattr(controller, "build_packet", lambda db, user_id, now=None: (_ for _ in ()).throw(
        controller.PacketError("The packet does not fit.")))
    controller.set_state(user, "stopped", now=T0 + 500)
    controller.sync(user, "http://workspace.test")
    sent = controller.packet_of(user)
    assert sent["sent"] is True and sent["error"] == "The packet does not fit." and sent["error_at"] is not None


def test_the_packet_s_own_check_refuses_a_secret_s_shape_nothing_should_have_let_in(env):
    user = {"id": "u1"}
    setup_plain(user)
    with store.connect() as db:
        db.execute("INSERT INTO ade_controller_questions (id, user_id, text, state, asked_at, created_at) VALUES "
                   "(?, 'u1', 'token ghp_abc', 'open', ?, ?)", (uid(), T0, T0))
        with pytest.raises(ValueError) as refused:
            controller.build_packet(db, "u1", T0 + 60)
    assert "shaped like a GitHub token" in str(refused.value) and "ghp_abc" not in str(refused.value)


def test_a_mirror_past_its_bytes_is_not_sent(env, ws, monkeypatch, capsys):
    monkeypatch.setattr(controller, "MIRROR_MAX", 200)
    user = auth_user()
    setup_plain(user)
    assert controller.sync(user, "http://workspace.test") is False
    assert "the mirror could not be built: The Manager's mirror does not fit." in capsys.readouterr().err
    assert ("PUT", "/controller") not in [(m, p) for m, p, _ in ws.calls]


def test_a_mirror_that_would_not_fit_with_the_packet_is_sent_without_it(env, ws, monkeypatch):
    user = auth_user()
    setup_plain(user)
    controller.set_state(user, "stopped", now=T0 + 50)
    with store.connect() as db:
        body, _, _ = controller.mirror_body(db, user["id"])
    without = len(json.dumps(dict(body, packet_b64=None, packet_digest=None,
                                  packet_error="The Manager's mirror would not fit with the packet."),
                             ensure_ascii=False).encode())
    monkeypatch.setattr(controller, "MIRROR_MAX", without)
    assert controller.sync(user, "http://workspace.test") is True
    m = ws.mirrors[-1]
    assert (m["state"], m["packet_b64"], m["packet_digest"], m["packet_error"]) == (
        "stopped", None, None, "The Manager's mirror would not fit with the packet.")


def test_the_mirror_s_parts_fit_its_bytes_at_their_most(env):
    """The bounds on each of the mirror's lists keep it within MIRROR_MAX, the packet at its largest included: the
    workspace's route is asked to take that much, and no record can make the mirror larger. Each part is measured as
    the mirror sends it, a requirement with whether it waits for the owner's go."""
    entry = {"id": "a" * 32, "state": "stopped", "hold": False, "waits_for_go": False}
    question_ = {"id": "a" * 32, "state": "answered", "decision": "b" * 32}
    settings = {"version": 1 << 52, "state": "stopped", "state_at": 1 << 52, "account": "a" * 12, "cap": 10,
                "models": ["m" * 100] * controller.ALLOWED_MOST, "efforts": ["ultracode"] * controller.ALLOWED_MOST,
                "chat": {"model": "m" * 100, "effort": "ultracode"}, "confirm_plans": False, "end_all": 1 << 52,
                "more_requests": 1 << 52, "more_decisions": 1 << 52,
                "packet_digest": "a" * 64, "packet_error": "x" * 100}
    most = (len(base64.b64encode(b"x" * controller.PACKET_MAX)) + controller.MIRROR_REQUESTS_BYTES
            + controller.MIRROR_DECISIONS_BYTES
            + controller.MIRROR_REQUIREMENTS_MOST * (len(json.dumps(entry)) + 2)
            + controller.MIRROR_QUESTIONS_MOST * (len(json.dumps(question_)) + 2)
            + len(json.dumps(settings)) + 200)
    assert most <= controller.MIRROR_MAX, most


def test_a_plan_dated_before_its_request_is_dated_at_the_request(env):
    """The confirm switch turned on before the request was made is in effect for its plan, whatever earlier
    time the record gives the plan."""
    user = {"id": "u1"}
    setup_plain(user)
    controller.change_settings(user, {"confirm_plans": True}, now=T0 + 10)
    request = controller.add_request(user, REQUEST, now=T0 + 50)
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 5), now=T0 + 100)
    r = requirement_row(rid)
    assert (r["planned_at"], r["confirm"]) == (T0 + 50, 1)
    assert events("u1", "plan")[0]["at"] == T0 + 50
    a = start(rid, at=T0 + 60)
    take(user, a, now=T0 + 110)
    assert task_row(a["task"])["flag"] == "started before the owner's go on its plan"


def test_a_start_dated_before_its_requirement_was_planned_is_dated_then_and_flagged(env):
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 30))
    a = start(rid, at=T0 + 20)
    take(user, a)
    t = task_row(a["task"])
    assert (t["started_at"], t["flag"]) == (T0 + 30, "dated before its requirement was planned")


def test_a_time_a_record_gives_is_never_before_the_last_whole_read_began(env, ws, monkeypatch):
    """An action a read first finds was not in the record when the last read that reached its end began,
    so it is dated no earlier than that; a Stop pressed before that cannot be stepped around by dating a start back."""
    user = auth_user()
    setup(user, cap=5)
    _, rid = planned(user)
    controller.set_state(user, "stopped", now=T0 + 100)
    before = int(time.time())
    controller.sync(user, "http://workspace.test")                 # a whole read: one page, nothing more
    with store.connect() as db:
        read_at = controller._row(db, user["id"])["read_at"]
    assert before <= read_at <= int(time.time())
    a = start(rid, at=T0 + 19)
    ws.pages = [dict(record(a), seq=3)]
    controller.sync(user, "http://workspace.test")
    t = task_row(a["task"])
    assert t["started_at"] == read_at and t["flag"] == "started while the Manager was stopped"
    # a read that stops before the record's end moves nothing
    monkeypatch.setattr(controller, "PAGES", 1)
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET read_at = 7")
    ws.pages = [dict(record(), seq=4, more=True)]
    controller.sync(user, "http://workspace.test")
    with store.connect() as db:
        assert controller._row(db, user["id"])["read_at"] == 7


def test_the_day_s_budgets_count_by_when_the_app_recorded_not_by_the_time_a_record_gave(env, monkeypatch):
    """A record that dates its actions days back still spends today's budgets."""
    monkeypatch.setattr(controller, "STARTS_A_DAY", 2)
    user = {"id": "u1"}
    long_ago = T0 - 3 * 86400
    setup(user, now=long_ago, cap=10)
    request = controller.add_request(user, REQUEST, now=long_ago + 1)
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=long_ago + 2), now=long_ago + 3)
    starts = [start(rid, session=None, at=long_ago + 10) for _ in range(3)]
    assert take(user, *starts[:2], now=T0 + 100)["taken"] == 2
    line = events("u1", "start")[0]
    assert (line["at"], line["recorded_at"]) == (long_ago + 10, T0 + 100)
    # a later page: the two lines dated days back were recorded today, so the day's starts are spent
    got = take(user, starts[2], now=T0 + 110)
    assert (got["taken"], got["refused"]) == (0, 1)
    assert events("u1", "start.refused")[0]["payload"]["why"] == "past the 2 starts a day the app takes"


def test_a_session_s_end_past_the_day_s_lines_is_kept_and_not_journaled(env, monkeypatch):
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", 1)
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)                      # the plan: the day's one line
    a = start(rid)
    take(user, a)
    take(user, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 50}])
    assert (task_row(a["task"])["state"], task_row(a["task"])["ended_at"]) == ("done", T0 + 50)
    assert events("u1", "task.ended") == []


def test_a_change_of_the_controller_s_chat_past_the_day_s_lines_is_kept_and_not_journaled(env, monkeypatch):
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", 1)
    user = {"id": "u1"}
    setup_plain(user)
    take(user, session=SESSION)
    take(user, session=OTHER)
    with store.connect() as db:
        assert controller._row(db, "u1")["session_id"] == OTHER
    assert [e["payload"] for e in events("u1", "controller.chat")] == [{"session": SESSION}]


def worst_requirement(user, request, *, repos=10):
    rid = uid()
    take(user, plan(request["id"], request["text"], rid=rid, acceptance=request["text"], title='"' * 300,
                    repos=["r" * 99 + str(i) for i in range(repos)]))
    return rid


def test_one_requirement_is_never_more_than_its_bytes_in_the_packet(env):
    """Each requirement is cut to REQUIREMENT_ITEM_MAX bytes, its words and acceptance last, so that one
    large requirement never leaves the others out."""
    user = {"id": "u1"}
    setup(user, cap=10)
    rids = []
    # each two bytes a character as the packet holds it, the backslash four (doubled, then escaped)
    for text in ('"' * controller.REQUEST_MAX, chr(0xe9) * controller.REQUEST_MAX, "\\" * 2000):
        request = controller.add_request(user, text, now=T0)
        request["text"] = text
        rids.append(worst_requirement(user, request))
    with store.connect() as db:
        info = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["information"]
    kept = info["requirements"]
    assert len(kept) >= 2 and info["more_requirements"] == 3 - len(kept)
    for item in kept:
        assert len(canonicalize(item)) <= controller.REQUIREMENT_ITEM_MAX
        assert item["words"]["cut"] is True and len(item["repos"]) == 10
    assert len(canonicalize({k: info[k] for k in ("requirements", "more_requirements", "other_sessions",
                                                   "more_other_sessions")})) <= controller.BUDGETS["observed"]


def test_the_sessions_started_for_no_requirement_fit_the_observed_budget(env):
    user = {"id": "u1"}
    setup(user, cap=10)
    strays = [start(None, session=None, repo="r" * 100, branch="b" * 200, at=T0 + 20) for _ in range(25)]
    take(user, *strays)
    with store.connect() as db:
        info = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["information"]
    kept = len(info["other_sessions"])
    assert 0 < kept < 25 and info["more_other_sessions"] == 25 - kept
    assert len(canonicalize({k: info[k] for k in ("requirements", "more_requirements", "other_sessions",
                                                   "more_other_sessions")})) <= controller.BUDGETS["observed"]


def test_a_plan_waits_for_the_go_in_the_packet_only_until_the_go(env):
    user = {"id": "u1"}
    setup(user, confirm_plans=True)
    request, rid = planned(user)

    def item():
        with store.connect() as db:
            info = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["information"]
        return next(r for r in info["requirements"] if r["id"] == rid)
    assert item()["waits_for_go"] is True
    controller.go(user, request["id"], now=T0 + 30)
    assert "waits_for_go" not in item()


def test_a_session_naming_another_owner_s_requirement_is_one_of_this_owner_s_other_sessions(env):
    """The view judges a session by the owner's own requirements alone."""
    user, them = {"id": "u1"}, {"id": "u2"}
    setup_plain(user)
    setup_plain(them)
    _, their_rid = planned(them)
    a = start(their_rid)
    take(user, a)
    view = controller.view(user)
    assert [t["id"] for t in view["other_sessions"]] == [a["task"]]
    assert task_row(a["task"])["flag"] == "started for a requirement the app does not have"


def test_another_account_reads_none_of_these_records_through_the_page_s_routes(client, owner, monkeypatch):
    """The journal, the requests, the board and the packet, read as another account with a controller of
    its own, hold none of the owner's."""
    h, user = owner
    setup_plain(user)
    planned(user)
    take(user, question("Which?"))
    monkeypatch.setenv("OPEN_EDITION_ADMINS", "admin@example.com,admin2@example.com")
    other = signed_up(client, "admin2@example.com")
    assert client.put(C, headers=other, json={"cap": 3}).status_code == 200
    assert client.get(C + "/requests", headers=other).json() == {"requests": []}
    assert [e["kind"] for e in client.get(C + "/journal", headers=other).json()["events"]] == ["controller.settings"]
    assert client.get(C + "/board", headers=other).json()["rows"] == []
    packet = client.get(C + "/packet", headers=other).json()["packet"]
    assert packet["instructions"]["requests"] == [] and packet["information"]["open_questions"] == []
    other_id = client.get("/api/auth/me", headers=other).json()["id"]
    assert controller.alerts(other_id, T0 + 100) == []
    assert [k for k, _ in controller.alerts(user["id"], T0 + 100) if k.startswith("controller-question:")]


def test_a_stopped_controller_is_polled_even_once_the_workspace_was_told(env):
    """Stop leaves the controller able to answer, so its sync goes on."""
    user = {"id": "u-stopped"}
    setup_plain(user)
    controller.set_state(user, "stopped", now=T0 + 10)
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET mirrored_version = version")
        assert controller.users_to_poll(db) == ["u-stopped"]


def test_a_session_that_ended_in_the_start_s_own_second_is_not_counted_against_the_cap(env):
    user = {"id": "u1"}
    setup(user, cap=1)
    _, rid = planned(user)
    first = start(rid, at=T0 + 20)
    take(user, first, now=T0 + 30)
    second = start(rid, at=T0 + 50, session=OTHER)
    take(user, second, tasks_=[{"id": first["task"], "state": "done", "ended_at": T0 + 50}], now=T0 + 60)
    assert task_row(second["task"])["flag"] is None


def test_a_change_is_sent_on_to_a_running_workspace_in_a_thread_of_its_own(env, monkeypatch, capsys):
    """The routes' _sync_soon, which every other test stands in for."""
    user = {"id": "u1"}
    made, calls = [], []

    class Thread:
        def __init__(self, target, name, daemon):
            made.append((name, daemon))
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(controller_routes.threading, "Thread", Thread)
    monkeypatch.setattr(controller, "sync", lambda user, endpoint: calls.append((user["id"], endpoint)))
    monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    REAL_SYNC_SOON(user)
    assert (made, calls) == ([], [])                       # the machine down: nothing started, the poll sends it
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace.test")
    REAL_SYNC_SOON(user)
    assert (made, calls) == ([("controller-sync", True)], [("u1", "http://workspace.test")])

    def broken(user, endpoint):
        raise RuntimeError("broken")
    monkeypatch.setattr(controller, "sync", broken)
    REAL_SYNC_SOON(user)
    assert "controller: a sync failed: RuntimeError: broken" in capsys.readouterr().err


def test_a_request_holding_a_character_that_is_not_one_is_refused(env):
    with pytest.raises(agents.AgentError) as refused:
        controller.add_request({"id": "u1"}, "a\ud800b", now=T0)
    assert (refused.value.status, str(refused.value)) == (400, "A request holds a character that is not one.")


def test_the_controller_s_sync_runs_when_the_agents_own_poll_fails(client, owner, ws, monkeypatch):
    """The owner's Stop never waits on the agents' sync, nor fails with it. The
    poll syncs the controller first, then the agents."""
    h, user = owner
    setup_plain(user)
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   ("a1a2a3a4a5a6", user["id"], "Tidy", "Tidy up.", T0, T0))

    def broken(user, endpoint):
        raise RuntimeError("the agents' poll broke")
    monkeypatch.setattr(agents, "_poll", broken)
    monkeypatch.setattr(agents, "_slow_work", lambda user, endpoint: None)
    agents.poll_all()
    assert ws.paths()[:2] == ["/controller/record?since=0", "/controller"]
    assert any(p.startswith("/agents") for p in ws.paths()[2:])


def test_a_page_s_read_never_syncs_the_controller(client, owner, ws, monkeypatch):
    """The Agents page and a suite sync the agents inside their read when it is
    stale, and that read never reads the controller's record nor sends its mirror, which could hold it up."""
    h, user = owner
    setup_plain(user)
    agents.sync(user)
    agents.listing(user)
    assert not any(p.startswith("/controller") for p in ws.paths())


def test_the_poll_gives_an_owner_with_a_controller_and_no_agent_the_controller_s_sync_alone(client, owner, ws,
                                                                                            monkeypatch):
    """None of the agents' work, nor the judge's, for an owner who has no agent. The controller's sync is
    first, and the read of the workspace's skill calls follows it."""
    h, user = owner
    setup_plain(user)
    slow = []
    monkeypatch.setattr(agents, "_slow_work", lambda user, endpoint: slow.append(user["id"]))
    agents.poll_all()
    assert ws.paths() == ["/controller/record?since=0", "/controller", "/skills?since=0"] and slow == []


def test_a_start_on_an_account_no_longer_confirmed_is_flagged(env):
    """Connections keeps no history, so this bound is judged when the app takes the start."""
    user = {"id": "u1"}
    setup_plain(user)
    _, rid = planned(user)
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET state = 'reported' WHERE user_id = 'u1'")
    a = start(rid)
    take(user, a)
    assert task_row(a["task"])["flag"] == "started on an account no longer confirmed as the owner's own"


def test_a_request_whose_requirements_all_closed_is_given_for_a_week(env):
    """A part of such a request may be still unplanned, so a fresh controller is given it, and the workspace may still
    check a plan against it, for a week after its last requirement closed."""
    user = {"id": "u1"}
    setup_plain(user)
    request, rid = planned(user)
    controller.close(user, rid, "done", now=T0 + 20)
    with store.connect() as db:
        week = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["instructions"]["requests"]
        body, _, _ = controller.mirror_body(db, "u1", T0 + 60)
        later = json.loads(controller.build_packet(db, "u1", T0 + 20 + controller.KEPT_CLOSED + 1)[0])
    assert [r["id"] for r in week] == [request["id"]] and [r["id"] for r in body["requests"]] == [request["id"]]
    assert later["instructions"]["requests"] == []


# ---- what a record holds or lacks, sessions no longer listed, and the reads ------------------------------------------

def test_a_record_s_text_that_is_no_text_never_stops_the_owner_s_controls(env, ws):
    """JSON's "\\ud800" gives a lone surrogate, which UTF-8 cannot hold. A
    question's words and a plan's title holding one are taken with it removed and counted, the record's change number
    moves on, and the owner's Stop reaches the workspace in the same sync."""
    user = auth_user()
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    rid = uid()
    title, words = json.loads('"Say\\udc00 hello"'), json.loads('"Which\\ud800 branch?"')
    ws.pages = [record(plan(request["id"], "Make the README say hello.", rid=rid, title=title),
                       question(words, requirement=rid), seq=5)]
    controller.set_state(user, "stopped", now=T0 + 50)
    assert controller.sync(user, "http://workspace.test") is True
    assert ws.mirrors[-1]["state"] == "stopped"
    with store.connect() as db:
        c = controller._row(db, user["id"])
        q = db.execute("SELECT text, hidden FROM ade_controller_questions WHERE user_id = ?", (user["id"],)).fetchone()
    assert c["seq"] == 5 and c["mirrored_version"] == c["version"]
    assert (q["text"], q["hidden"]) == ("Which branch?", 1)
    r = requirement_row(rid)
    assert (r["title"], r["title_hidden"]) == ("Say hello", 1)


def test_a_reply_nested_past_python_s_limit_never_stops_the_owner_s_controls(env):
    """Through the real client and a real HTTP server: a record nested past Python's
    recursion limit makes the client raise RecursionError, which is none of the workspace's own errors. The read is
    recorded as failed, with why, and the mirror goes all the same."""
    import http.server
    import threading
    import types
    got = []

    class Workspace(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.answer(b"[" * 200000)

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/controller":                  # the managers' route goes after it
                got.append(body)
            self.answer(json.dumps({"held": body["version"], "attempt": None}).encode())
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Workspace)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        endpoint = types.SimpleNamespace(http=f"http://127.0.0.1:{server.server_port}", secret="s", ssl=None)
        user = auth_user()
        setup_plain(user)
        controller.set_state(user, "stopped", now=T0 + 10)
        assert controller.sync(user, endpoint, call=REAL_CALL) is True
        assert got[-1]["state"] == "stopped"
        c = controller.view(user, now=T0 + 20)["controller"]
        assert c["read_error"] == "The app failed reading the workspace's record (RecursionError)."
        assert c["mirrored_version"] == c["version"]
    finally:
        server.shutdown()
        server.server_close()


def test_an_action_the_app_fails_to_take_is_refused_by_the_failure_s_name_and_the_page_goes_on(env, ws, monkeypatch):
    """Whatever an action's checks raise, the action is undone and refused, named by the
    failure's type and never by its message, which could carry what the record wrote; the rest of the page is taken,
    and the mirror goes."""
    user = auth_user()
    setup_plain(user)
    real = controller._take_question

    def breaks(db, user_id, page, action_id, action, at, now):
        if action["text"] == "Break?":
            db.execute("INSERT INTO ade_controller_questions (id, user_id, text, state, asked_at, created_at) VALUES "
                       "(?,?,'half-written','open',0,0)", (action["question"], user_id))
            raise RuntimeError("what the record wrote: Break?")
        return real(db, user_id, page, action_id, action, at, now)
    monkeypatch.setattr(controller, "_take_question", breaks)
    bad, good = question("Break?", at=T0 + 20), question("Fine?", at=T0 + 30)
    ws.pages = [record(bad, good, seq=3)]
    assert controller.sync(user, "http://workspace.test") is True and ws.mirrors
    assert [e["payload"]["why"] for e in events(user["id"], "question.refused")] == [
        "the app failed to take it (RuntimeError)"]
    assert "Break?" not in json.dumps(events(user["id"]))
    with store.connect() as db:
        assert [r["text"] for r in db.execute("SELECT text FROM ade_controller_questions WHERE user_id = ?",
                                              (user["id"],))] == ["Fine?"]
        assert controller._row(db, user["id"])["seq"] == 3


def test_a_state_the_app_fails_to_take_is_left_for_the_next_page_and_the_others_are_taken(env, monkeypatch):
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid), start(rid, session=OTHER)
    take(user, a, b)
    real = controller._take_state

    def breaks(db, user_id, task_id, s, page, now):
        if task_id == a["task"]:
            raise RuntimeError("no")
        return real(db, user_id, task_id, s, page, now)
    monkeypatch.setattr(controller, "_take_state", breaks)
    got = take(user, tasks_=[{"id": a["task"], "state": "working"}, {"id": b["task"], "state": "working"}],
               now=T0 + 110)
    assert got["states"] == 1
    assert (task_row(a["task"])["state"], task_row(b["task"])["state"]) == ("starting", "working")
    # an open session's state comes again with the next page, which takes it
    monkeypatch.setattr(controller, "_take_state", real)
    assert take(user, tasks_=[{"id": a["task"], "state": "working"}, {"id": b["task"], "state": "working"}],
                now=T0 + 120)["states"] == 1
    assert task_row(a["task"])["state"] == "working"


def test_a_session_a_whole_page_no_longer_lists_is_ended_in_the_store_from_when_it_was_last_listed(env):
    """A session whose end the app never read (the workspace's volume new, an end
    it failed to take or that fell between two pages, a session the workspace no longer has) stayed open in the store
    for good: it held the cap, flagging every later start, and stayed on the board and in the packet as working. A
    page whose states the app read in full lists every session the controller started that is open in the workspace
    (the contract), so one the store has open that it does not list is ended as failed, dated when the app last took
    such a page (here, one that listed it), and journaled; a start made after that is not counted against it."""
    user = {"id": "u1"}
    setup(user, cap=2)
    _, rid = planned(user)
    a, b = start(rid, at=T0 + 20), start(rid, session=OTHER, at=T0 + 21)
    both = [{"id": a["task"], "state": "working"}, {"id": b["task"], "state": "working"}]
    take(user, a, b, tasks_=both, now=T0 + 100)
    take(user, tasks_=both, now=T0 + 200)
    with store.connect() as db:
        version = controller._row(db, "u1")["version"]
    # the workspace's volume is new: its record lists only the session it started since, within the cap
    c = start(rid, session="cccccccccccc", at=T0 + 300)
    got = take(user, c, tasks_=[{"id": c["task"], "state": "working"}], now=T0 + 400)
    assert (got["taken"], got["flagged"], got["states"]) == (1, 0, 2)
    for x in (a, b):
        t = task_row(x["task"])
        assert (t["state"], t["ended_at"], t["waits_until"]) == ("failed", T0 + 200, None)
    assert task_row(c["task"])["flag"] is None
    ended = events("u1", "task.ended")
    assert [(e["task_id"], e["at"], e["payload"]["unlisted"], e["payload"]["state"]) for e in ended] == [
        (a["task"], T0 + 200, True, "failed"), (b["task"], T0 + 200, True, "failed")]
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] > version
    view = controller.view(user, now=T0 + 500)
    assert view["controller"]["open_sessions"] == 1
    rows = {r["id"]: r for r in controller.board_rows("u1", T0 + 500)}
    assert (rows[a["task"]]["phase"], rows[c["task"]]["phase"]) == ("history", "now")
    # a start dated before the old sessions were last listed counts them: they were open then
    d = start(rid, session="dddddddddddd", at=T0 + 150)
    take(user, d, tasks_=[{"id": c["task"], "state": "working"}, {"id": d["task"], "state": "working"}],
         now=T0 + 600)
    assert task_row(d["task"])["flag"] == "one more than the 2 sessions the Manager may have open at once"


def test_a_session_ended_for_no_longer_being_listed_moves_the_version_on_a_page_that_holds_nothing_else(env):
    """The mirror and the packet give a session the app has open as open, so ending it for no longer being listed moves
    the version even on a page that changes nothing else: a workspace that holds the mirror is sent the end (a
    page that also took a start would move the version anyway)."""
    user = {"id": "u1"}
    setup(user, cap=5)
    a = start(None, session=None, at=T0 + 10)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 20)
    with store.connect() as db:
        version = controller._row(db, "u1")["version"]
    assert take(user, tasks_=[], now=T0 + 30)["states"] == 1
    assert task_row(a["task"])["state"] == "failed"
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] == version + 1


def test_an_end_the_app_failed_to_take_is_read_from_the_next_page_that_no_longer_lists_the_session(env, monkeypatch):
    """A session's end the app failed to take never came again, since the contract
    lists a session that ended only on the pages after its change; it is ended at the next page that does not list it.
    Its end is the app's: as failed, dated when a page last listed it open."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 100)
    real = controller._take_state

    def breaks(db, user_id, task_id, s, page, now):
        raise RuntimeError("no")
    monkeypatch.setattr(controller, "_take_state", breaks)
    take(user, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 150}], now=T0 + 200)
    assert task_row(a["task"])["state"] == "working"
    monkeypatch.setattr(controller, "_take_state", real)
    take(user, tasks_=[], now=T0 + 300)
    t = task_row(a["task"])
    assert (t["state"], t["ended_at"]) == ("failed", T0 + 200)


def test_a_start_its_own_page_does_not_list_is_kept_starting_until_a_later_page_does_not_either(env):
    """The page's states are taken, and the sessions it does not list ended, before its actions: a start the same page
    holds is the workspace's after its states were read, so it is kept, starting, and a later page that does not list
    it ends it."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[], now=T0 + 100)
    assert (task_row(a["task"])["state"], task_row(a["task"])["ended_at"]) == ("starting", None)
    take(user, tasks_=[], now=T0 + 200)
    assert (task_row(a["task"])["state"], task_row(a["task"])["ended_at"]) == ("failed", T0 + 100)


def test_a_session_no_longer_listed_is_ended_no_earlier_than_its_start(env):
    """The app dates the end it gives a session no longer listed when it last took a page whose states it read in full,
    or at the session's start if that was later: a start taken from a page that said nothing of the states, after the
    last such page, is not ended before it began."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    take(user, tasks_=[], now=T0 + 100)     # states read in full: the last such page, until T0 + 300
    a = start(rid, at=T0 + 150)
    take(user, a, now=T0 + 200)             # says nothing of the states
    assert task_row(a["task"])["state"] == "starting"
    take(user, tasks_=[], now=T0 + 300)
    assert (task_row(a["task"])["state"], task_row(a["task"])["ended_at"]) == ("failed", T0 + 150)


def test_a_session_ended_for_no_longer_being_listed_past_the_day_s_lines_is_ended_and_not_journaled(env,
                                                                                                   monkeypatch):
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", 1)
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)                  # the plan: the day's one line
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 100)
    take(user, tasks_=[], now=T0 + 200)
    assert task_row(a["task"])["state"] == "failed" and events("u1", "task.ended") == []


def test_a_page_that_says_nothing_of_the_states_or_more_than_the_app_reads_ends_no_session(env, monkeypatch):
    """Only a page whose sessions' states the app read in full says which are open: one without the states, or one
    past what the app reads (the rest unread may list the session), ends none, and moves no time of the last
    listing."""
    monkeypatch.setattr(controller, "ACTIONS_A_PAGE", 2)
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 100)
    controller.take("u1", {"seq": 1, "more": False, "actions": []}, now=T0 + 200)
    controller.take("u1", {"seq": 1, "more": False, "actions": [], "tasks": {"not": "a list"}}, now=T0 + 210)
    take(user, tasks_=[{"id": uid(), "state": "working"} for _ in range(3)], now=T0 + 300)
    assert task_row(a["task"])["state"] == "working"
    with store.connect() as db:
        assert controller._row(db, "u1")["listed_at"] == T0 + 100
    take(user, tasks_=[], now=T0 + 400)
    assert (task_row(a["task"])["state"], task_row(a["task"])["ended_at"]) == ("failed", T0 + 100)


def test_a_packet_that_fails_in_any_way_leaves_the_mirror_going_named_by_the_failure(env, ws, monkeypatch):
    user = auth_user()
    setup_plain(user)
    monkeypatch.setattr(controller, "build_packet", lambda db, user_id, now=None: (_ for _ in ()).throw(
        RuntimeError("words the store held")))
    assert controller.sync(user, "http://workspace.test") is True
    assert ws.mirrors[-1]["packet_error"] == "The packet could not be built: the app failed (RuntimeError)."


def test_a_mirror_whose_answer_cannot_be_read_is_sent_again(env, ws, monkeypatch):
    user = auth_user()
    setup_plain(user)
    real = ws.call

    def unreadable(endpoint, method, path, body=None, timeout=30):
        out = real(endpoint, method, path, body, timeout)
        if method == "PUT":
            raise RecursionError("maximum recursion depth exceeded")
        return out
    monkeypatch.setattr(workspace_client, "call", unreadable)
    assert controller.sync(user, "http://workspace.test") is False
    with store.connect() as db:
        assert controller._row(db, user["id"])["mirrored_version"] is None
    monkeypatch.setattr(workspace_client, "call", real)
    assert controller.sync(user, "http://workspace.test") is True and len(ws.mirrors) == 2


def test_what_the_workspace_says_it_holds_never_keeps_the_app_from_sending_what_it_has_not_sent(env, ws):
    """After the owner's Stop the app's version is one past what it sent; a
    record that says it holds that version has the app send it all the same. Sent, and said to be held, nothing more
    goes."""
    user = auth_user()
    setup_plain(user)
    assert controller.sync(user, "http://workspace.test") is True
    v = ws.mirrors[-1]["version"]
    controller.set_state(user, "stopped", now=T0 + 10)
    ws.held = v + 1
    assert controller.sync(user, "http://workspace.test") is True
    assert (ws.mirrors[-1]["version"], ws.mirrors[-1]["state"]) == (v + 1, "stopped")
    n = len(ws.mirrors)
    assert controller.sync(user, "http://workspace.test") is False and len(ws.mirrors) == n
    c = controller.view(user, now=T0 + 20)["controller"]
    assert c["mirrored_version"] == c["version"] == v + 1


@pytest.mark.parametrize("kind,most", [("question", shapes.TEXT_MAX), ("plan", controller.TITLE_MAX)])
def test_a_model_s_text_is_read_to_four_times_what_is_kept_and_no_further(env, monkeypatch, kind, most):
    """The secret-shape check costs more than linear time on some texts, so it is
    never given more of a record's text than could be kept: four times the bytes kept, in characters."""
    seen = []
    real = tasks.model_shape
    monkeypatch.setattr(tasks, "model_shape", lambda value, most: seen.append(len(value)) or real(value, most))
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    long = "Which branch? " * 300000
    a = question(long) if kind == "question" else plan(request["id"], "Make the README say hello.", title=long)
    assert take(user, a)["taken"] == 1
    assert seen and max(seen) <= 4 * most
    with store.connect() as db:
        row = db.execute("SELECT text, cut FROM ade_controller_questions" if kind == "question" else
                         "SELECT title AS text, title_cut AS cut FROM ade_controller_requirements").fetchone()
    assert row["cut"] == 1 and row["text"].startswith("Which branch? ")


def test_the_dearer_checks_run_only_for_an_action_that_will_be_taken(env, monkeypatch):
    """A question past the three open, or a plan past the open requirements, is refused before its text or
    its words are read at all, so a record cannot make the app check text it will never keep."""
    calls, parts = [], []
    real_line, real_part = controller._model_line, controller._owners_part
    monkeypatch.setattr(controller, "_model_line", lambda value, most: calls.append(most) or real_line(value, most))
    monkeypatch.setattr(controller, "_owners_part", lambda value, text: parts.append(1) or real_part(value, text))
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    assert take(user, *[question(f"Q{i}?") for i in range(3)])["taken"] == 3
    calls.clear()
    assert take(user, *[question("x" * 5000) for _ in range(20)], now=T0 + 200)["refused"] == 20
    assert calls == []
    monkeypatch.setattr(controller, "REQUIREMENTS_OPEN_MOST", 1)
    assert take(user, plan(request["id"], "Make the README say hello."), now=T0 + 300)["taken"] == 1
    calls.clear()
    parts.clear()
    got = take(user, *[plan(request["id"], "Make the README say hello.") for _ in range(20)], now=T0 + 400)
    assert got["refused"] == 20 and parts == [] and calls == []


def test_a_continue_is_judged_by_the_reset_its_session_waited_for_before_the_page(env):
    """A continue made before the reset, read with the session's state after it
    (working, no reset), is flagged; one made after the reset its session waited for, read where the session
    already waits for the next one, is not."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": T0 + 500}], now=T0 + 25)
    take(user, act("continue", a["task"], at=T0 + 300), tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 310)
    assert task_row(a["task"])["flag"] == "continued before its account's reset"
    b = start(rid, at=T0 + 600, session=OTHER)
    take(user, b, tasks_=[{"id": b["task"], "state": "waiting", "waits_until": T0 + 700}], now=T0 + 610)
    take(user, act("continue", b["task"], at=T0 + 800),
         tasks_=[{"id": b["task"], "state": "waiting", "waits_until": T0 + 5000}], now=T0 + 810)
    assert task_row(b["task"])["flag"] is None
    # a session the app never saw waiting, started and continued in one page: no reset to judge the continue by
    c = start(rid, at=T0 + 900, session="c" * 12)
    take(user, c, act("continue", c["task"], at=T0 + 950),
         tasks_=[{"id": c["task"], "state": "waiting", "waits_until": T0 + 9000}], now=T0 + 960)
    assert task_row(c["task"])["flag"] is None


def test_a_page_is_taken_in_the_order_of_its_times_whatever_order_it_lists(env):
    """With a cap of one, a start listed first but made later is past the cap,
    and a plan listed after its start but made before it is the start's requirement."""
    user = {"id": "u1"}
    own(user["id"])
    controller.change_settings(user, {"account": "default", "cap": 1}, now=T0)
    controller.set_state(user, "on", now=T0)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    rid = uid()
    later, earlier = start(rid, at=T0 + 30, session=OTHER), start(rid, at=T0 + 20)
    take(user, later, earlier, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 10), now=T0 + 40)
    assert task_row(earlier["task"])["flag"] is None
    assert task_row(later["task"])["flag"] == "one more than the 1 sessions the Manager may have open at once"


def test_a_read_that_never_reaches_the_record_s_end_is_told_and_moves_no_floor(env, ws, monkeypatch):
    """A record that always says there is more keeps the floor on its times
    where the last read that reached its end set it, which is all the app can know; such a read is recorded as
    behind, shown, and told to the owner for a day. A read that reaches the end sets the floor to when it began, and
    is shown later than the last read left behind."""
    user = auth_user()
    setup_plain(user)
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    ws.pages = [record(seq=i + 1, more=True) for i in range(controller.PAGES)]
    assert controller.sync(user, "http://workspace.test") is True
    c = controller.view(user, now=T0 + 1000)["controller"]
    assert (c["read_at"], c["read_behind_at"]) == (None, T0 + 1000)
    assert look(T0 + 1000) in controller.alerts(user["id"], T0 + 1000)
    clock[0] = T0 + 2000
    assert controller.sync(user, "http://workspace.test") is False
    c = controller.view(user, now=T0 + 2000)["controller"]
    assert (c["read_at"], c["read_behind_at"]) == (T0 + 2000, T0 + 1000)
    assert look(T0 + 1000) in controller.alerts(user["id"], T0 + 2000)       # told for a day after
    assert look(T0 + 1000) not in controller.alerts(user["id"], T0 + 1000 + 86401)


def test_a_read_that_fails_is_shown_with_why_and_since_when_until_one_succeeds(env, ws, monkeypatch):
    user = auth_user()
    setup_plain(user)
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    ws.fail = 502
    assert controller.sync(user, "http://workspace.test") is True           # the mirror goes all the same
    clock[0] = T0 + 1500
    controller.set_state(user, "stopped", now=T0 + 1500)
    assert controller.sync(user, "http://workspace.test") is True
    c = controller.view(user, now=T0 + 1500)["controller"]
    assert (c["read_error"], c["read_error_at"]) == ("The workspace answered 502.", T0 + 1000)
    ws.fail = None
    clock[0] = T0 + 2000
    controller.sync(user, "http://workspace.test")
    c = controller.view(user, now=T0 + 2000)["controller"]
    assert (c["read_error"], c["read_error_at"], c["read_at"]) == (None, None, T0 + 2000)


def test_the_settings_replay_reads_through_the_index_by_owner_kind_and_time(env):
    """The replay a start's bounds are judged by reads the few lines of the owner's
    changes through an index, never the owner's whole journal."""
    with store.connect() as db:
        seen = []
        db.set_trace_callback(seen.append)
        controller._settings_at(db, "u1", T0)
        db.set_trace_callback(None)
        sql = next(x for x in seen if "controller.settings" in x)
        plan_ = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql)]
    assert any("ade_controller_events_at" in p for p in plan_), plan_


def test_the_mirror_tells_the_workspace_which_plans_wait_for_the_owner_s_go(env, ws):
    """A plan made while plans waited for the owner's go waits for it
    whatever the switch is later, and the mirror says so, until a go covers it."""
    user = auth_user()
    setup_plain(user)
    controller.change_settings(user, {"confirm_plans": True}, now=T0 + 1)
    request = controller.add_request(user, REQUEST, now=T0 + 2)
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 5), now=T0 + 6)
    controller.change_settings(user, {"confirm_plans": False}, now=T0 + 7)
    controller.sync(user, "http://workspace.test")
    assert ws.mirrors[-1]["requirements"] == [{"id": rid, "state": "open", "hold": False, "waits_for_go": True}]
    controller.go(user, request["id"], now=T0 + 8)
    controller.sync(user, "http://workspace.test")
    assert ws.mirrors[-1]["requirements"][0]["waits_for_go"] is False
    view = controller.requirement_view(user, rid, now=T0 + 9)
    assert (view["waits_for_go"], view["go_at"]) == (False, T0 + 8)


def test_a_state_given_before_its_start_is_taken_with_the_page_that_holds_the_start(env):
    """The routes' contract: each page's `tasks` is
    the state, as the workspace has it then, of each session the controller started that is open, and of each whose
    state changed after the change number the page was asked from. A session's start is a change of its own, so a
    state that comes one page before its start, which the app cannot take yet, comes again with the start: the start
    is after the first page's change number, and every change of the session after it."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    ended = {"id": a["task"], "state": "done", "ended_at": T0 + 40}
    assert take(user, tasks_=[ended], now=T0 + 50)["states"] == 0
    take(user, a, tasks_=[ended], now=T0 + 60)
    assert (task_row(a["task"])["state"], task_row(a["task"])["ended_at"]) == ("done", T0 + 40)


def test_the_view_the_board_and_the_packet_list_a_requirement_s_newest_sessions_and_count_the_rest(env):
    """What the page reads grows with what is open, never with every session
    ever started: twenty of a requirement's sessions, open ones first, the rest counted; twenty of those started for no
    requirement the app has."""
    user = {"id": "u1"}
    setup(user, cap=10)
    _, rid = planned(user)
    starts = [start(rid, session=None, at=T0 + 20 + i) for i in range(25)]
    strays = [start(None, session=None, at=T0 + 20 + i) for i in range(25)]
    take(user, *starts, *strays, tasks_=[{"id": s["task"], "state": "done", "ended_at": T0 + 60 + i}
                                         for i, s in enumerate(starts[:22] + strays)], now=T0 + 100)
    view = controller.view(user, now=T0 + 200)
    req = view["requirements"][0]
    assert len(req["tasks"]) == 20 and req["more_tasks"] == 5
    assert [t["id"] for t in req["tasks"][:3]] == [s["task"] for s in starts[22:]]          # the open ones first
    assert req["tasks"][3]["id"] == starts[21]["task"]                                      # then the newest ended
    rows = controller.board_rows("u1", T0 + 200)
    assert sum(1 for r in rows if r["kind"] == "session" and r["parent"] == rid) == 20
    assert sum(1 for r in rows if r["kind"] == "session" and r["parent"] is None) == 20
    with store.connect() as db:
        info = json.loads(controller.build_packet(db, "u1", T0 + 200)[0])["information"]
    assert len(info["requirements"][0]["tasks"]) + info["requirements"][0]["more_tasks"] == 25


def test_two_starts_naming_one_session_are_two_board_rows_of_their_own_ids(env):
    """A session row's id is the controller's task's, which never repeats; the
    workspace session it ran in is its `session`."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid), start(rid)
    take(user, a, b)
    rows = [r for r in controller.board_rows("u1", T0 + 200) if r["kind"] == "session"]
    assert sorted(r["id"] for r in rows) == sorted([a["task"], b["task"]])
    assert [r["session"] for r in rows] == [SESSION, SESSION]


def test_a_fresh_controller_is_given_each_question_its_decisions_answer_with_its_decision(env):
    """A decision that answers a question is given beside
    the question's own words, however old, so a fresh controller never has the answer alone; and a question answered
    is given only with the decision that answered it."""
    user = {"id": "u1"}
    setup_plain(user)
    q1, q2 = uid(), uid()
    take(user, question("Which branch?", qid=q1), question("Squash?", qid=q2))
    d = controller.decide(user, "Use main.", q1, now=T0 + 200)
    with store.connect() as db:
        info = json.loads(controller.build_packet(db, "u1", T0 + 300)[0])["information"]
        later = json.loads(controller.build_packet(db, "u1", T0 + 200 + controller.KEPT_CLOSED + 1)[0])
    assert [q["id"] for q in info["open_questions"]] == [q2]
    answered = [{"by": "model", "id": q1, "requirement": None, "text": "Which branch?", "decision": d["id"]}]
    assert info["answered_questions"] == answered and info["more_answered"] == 0
    # a week and more later the decision is still given, and its question with it
    assert [(x["id"], x["question"], x["text"]) for x in later["instructions"]["decisions"]] == [
        (d["id"], q1, "Use main.")]
    assert later["information"]["answered_questions"] == answered


# ---- the board, the view, the mirror and the packet at their edges ---------------------------------------------------

ROUTES = [
    ("put", "", lambda ids: {"cap": 3}),
    ("post", "/on", lambda ids: None),
    ("post", "/off", lambda ids: None),
    ("post", "/stop", lambda ids: None),
    ("post", "/end-all", lambda ids: None),
    ("post", "/requests", lambda ids: {"text": "Say hello."}),
    ("post", "/requests/{request}/go", lambda ids: None),
    ("post", "/requests/{spare}/withdraw", lambda ids: None),
    ("post", "/requirements/{requirement}/hold", lambda ids: {"hold": True}),
    ("post", "/requirements/{requirement}/close", lambda ids: {"as": "done"}),
    ("post", "/questions/{question}/answer", lambda ids: {"text": "Use main."}),
    ("post", "/decisions", lambda ids: {"text": "Keep it short."}),
]


@pytest.mark.parametrize("method,path,body", ROUTES, ids=[m + (p or "/") for m, p, _ in ROUTES])
def test_every_route_that_changes_something_sends_it_on_at_once(client, owner, ws, method, path, body):
    """Each of the owner's changes starts a sync, so it reaches the workspace at once where it runs."""
    h, user = owner
    own(user["id"])
    controller.change_settings(user, {"account": "default", "confirm_plans": True}, now=T0)
    if path != "/on":
        controller.set_state(user, "on", now=T0)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    rid, qid = uid(), uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid), question("Which?", qid=qid))
    spare = controller.add_request(user, "Tidy the CHANGELOG.", now=T0 + 2)
    ids = {"request": request["id"], "requirement": rid, "question": qid, "spare": spare["id"]}
    ws.synced.clear()
    r = getattr(client, method)(C + path.format(**ids), headers=h, json=body(ids))
    assert r.status_code == 200, r.text
    assert ws.synced == [user["id"]]


def test_the_board_puts_the_newest_change_first_within_now_and_history_and_numbers_next_oldest_first(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    ids = [uid() for _ in range(6)]
    take(user, *[plan(request["id"], "Make the README say hello.", rid=r, at=T0 + 2 + i) for i, r in enumerate(ids)],
         now=T0 + 10)
    first, second, queued_old, queued_new, closed_old, closed_new = ids
    a, b = start(first, at=T0 + 20), start(second, at=T0 + 30, session=OTHER)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 40)
    take(user, b, tasks_=[{"id": a["task"], "state": "working"}, {"id": b["task"], "state": "working"}], now=T0 + 50)
    controller.close(user, closed_old, "done", now=T0 + 60)
    controller.close(user, closed_new, "done", now=T0 + 70)
    order = [r["id"] for r in controller.board_rows("u1", T0 + 100)]
    assert order.index(second) < order.index(first)                    # in Now, the newest change first
    assert order.index(closed_new) < order.index(closed_old)           # in History too
    rows = {r["id"]: r for r in controller.board_rows("u1", T0 + 100)}
    assert (rows[queued_old]["order"], rows[queued_new]["order"]) == (1, 2)    # Next: the oldest first


def test_a_requirement_running_a_session_is_running_whatever_another_waits_for(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid), start(rid, session=OTHER)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "working"},
                             {"id": b["task"], "state": "waiting", "waits_until": T0 + 900}])
    assert controller.view(user, now=T0 + 200)["requirements"][0]["activity"] == "running"


def test_a_session_whose_reset_is_past_is_now_on_the_board(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid)
    take(user, a, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": T0 + 150}])
    rows = {r["id"]: r for r in controller.board_rows("u1", T0 + 200)}
    assert (rows[a["task"]]["phase"], rows[a["task"]]["waits_until"]) == ("now", None)
    assert (rows[rid]["phase"], rows[rid]["state"], rows[rid]["waits_until"]) == ("now", "idle", None)


def test_the_cap_counts_a_session_started_in_the_same_second(env):
    user = {"id": "u1"}
    setup(user, cap=1)
    _, rid = planned(user)
    a, b = start(rid, at=T0 + 20), start(rid, at=T0 + 20, session=OTHER)
    take(user, a, b)
    assert task_row(a["task"])["flag"] is None
    assert task_row(b["task"])["flag"] == "one more than the 1 sessions the Manager may have open at once"


def test_an_action_is_dated_no_earlier_than_the_controller_was_made(env):
    """The floor is never before the controller's row was made, whatever the record says."""
    user = {"id": "u1"}
    setup(user, now=T0 + 100, cap=5)
    request = controller.add_request(user, REQUEST, now=T0 + 100)
    rid = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 100), now=T0 + 110)
    a = start(rid, at=T0 - 5000)
    take(user, a, now=T0 + 120)
    assert task_row(a["task"])["started_at"] == T0 + 100


def test_a_stop_or_a_continue_is_dated_no_earlier_than_its_session_s_start(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid, at=T0 + 50), start(rid, at=T0 + 50, session=OTHER)
    take(user, a, b)
    take(user, act("stop", a["task"], at=T0 + 10), act("continue", b["task"], at=T0 + 10), now=T0 + 120)
    assert task_row(a["task"])["stopped_at"] == T0 + 50
    assert [e["at"] for e in events("u1") if e["kind"] in ("stop", "continue")] == [T0 + 50, T0 + 50]


def test_the_mirror_keeps_the_requirements_changed_most_lately_past_its_most(env, monkeypatch):
    monkeypatch.setattr(controller, "MIRROR_REQUIREMENTS_MOST", 2)
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    ids = [uid() for _ in range(3)]
    take(user, *[plan(request["id"], "Make the README say hello.", rid=r) for r in ids], now=T0 + 10)
    controller.hold(user, ids[2], True, now=T0 + 20)
    controller.hold(user, ids[0], True, now=T0 + 30)
    with store.connect() as db:
        body, _, _ = controller.mirror_body(db, "u1", T0 + 40)
    assert [r["id"] for r in body["requirements"]] == [ids[0], ids[2]]


def test_the_packet_lists_the_newest_ended_session_first(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid, at=T0 + 20), start(rid, at=T0 + 21, session=OTHER)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 60},
                             {"id": b["task"], "state": "done", "ended_at": T0 + 50}])
    with store.connect() as db:
        req = json.loads(controller.build_packet(db, "u1", T0 + 200)[0])["information"]["requirements"][0]
    assert [t["id"] for t in req["tasks"]] == [a["task"], b["task"]]


def test_the_view_says_whether_the_account_is_still_confirmed_in_connections(env):
    user = {"id": "u1"}
    setup_plain(user)
    assert controller.view(user, now=T0 + 10)["controller"]["account_confirmed"] is True
    with store.connect() as db:
        db.execute("UPDATE ade_account_owners SET state = 'reported' WHERE user_id = 'u1'")
    assert controller.view(user, now=T0 + 10)["controller"]["account_confirmed"] is False


def test_a_requirement_s_progress_counts_the_sessions_done_and_no_other_ended_one(env):
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    ss = [start(rid, session=None) for _ in range(3)]
    take(user, *ss, tasks_=[{"id": s["task"], "state": state, "ended_at": T0 + 50}
                            for s, state in zip(ss, ("done", "failed", "stopped"))])
    row = next(r for r in controller.board_rows("u1", T0 + 200) if r["id"] == rid)
    assert row["progress"] == {"done": 1, "of": 3}


def test_an_ended_session_keeps_no_reset(env):
    """An ended session keeps no reset."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid)
    take(user, a, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": T0 + 900}])
    take(user, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 150, "waits_until": T0 + 900}],
         now=T0 + 160)
    assert task_row(a["task"])["waits_until"] is None


def test_the_mirror_keeps_a_question_answered_in_the_last_week(env):
    """The mirror keeps a question answered in the last week."""
    user = {"id": "u1"}
    setup_plain(user)
    qid = uid()
    take(user, question("Which?", qid=qid))
    controller.decide(user, "Main.", qid, now=T0 + 200)
    with store.connect() as db:
        body, _, _ = controller.mirror_body(db, "u1", T0 + 200 + 2 * 86400)
        later, _, _ = controller.mirror_body(db, "u1", T0 + 200 + controller.KEPT_CLOSED + 1)
    assert [q["id"] for q in body["questions"]] == [qid] and later["questions"] == []


def test_the_view_lists_at_most_twenty_sessions_started_for_no_requirement(env):
    """The view lists at most twenty sessions started for no requirement."""
    user = {"id": "u1"}
    setup(user, cap=10)
    take(user, *[start(None, session=None) for _ in range(21)])
    assert len(controller.view(user, now=T0 + 200)["other_sessions"]) == 20


def test_a_page_asked_for_with_no_room_still_gives_one(env):
    """A page asked for with no room still gives one."""
    user = {"id": "u1"}
    setup_plain(user)
    controller.add_request(user, "One.", now=T0 + 1)
    controller.add_request(user, "Two.", now=T0 + 2)
    assert len(controller.requests_of(user, limit=0)["requests"]) == 1
    assert len(controller.journal_of(user, limit=0)["events"]) == 1


def test_taking_the_account_from_a_controller_that_is_off_writes_no_line_of_state(env):
    """Taking the account from a controller that is off writes no line of state."""
    user = {"id": "u1"}
    own("u1")
    controller.change_settings(user, {"account": "default"}, now=T0)
    controller.change_settings(user, {"account": None}, now=T0 + 10)
    assert [e["kind"] for e in events("u1")] == ["controller.settings", "controller.settings"]


def test_the_floor_is_when_the_read_that_reached_the_end_began(env, ws, monkeypatch):
    """An action read later was not in the record when that read began; when it ended is too late a floor."""
    user = auth_user()
    setup_plain(user)
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    real = ws.call

    def slow(endpoint, method, path, body=None, timeout=30):
        clock[0] += 30
        return real(endpoint, method, path, body, timeout)
    monkeypatch.setattr(workspace_client, "call", slow)
    controller.sync(user, "http://workspace.test")
    # the read begins after the app's word, read and sent first: two
    # calls of 30 seconds each here
    assert ws.words == ["GET", "PUT"]
    assert controller.view(user, now=T0 + 2000)["controller"]["read_at"] == T0 + 1060


def test_the_view_shows_a_question_answered_in_the_last_day(env):
    """The view shows a question answered in the last day."""
    user = {"id": "u1"}
    setup_plain(user)
    qid = uid()
    take(user, question("Which?", qid=qid))
    controller.decide(user, "Main.", qid, now=T0 + 200)
    assert [q["id"] for q in controller.view(user, now=T0 + 200 + 7200)["questions"]] == [qid]
    assert controller.view(user, now=T0 + 200 + 86401)["questions"] == []


def test_the_view_counts_only_open_sessions_as_open(env):
    """The view counts only open sessions as open."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid), start(rid, session=OTHER)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 50}])
    assert controller.view(user, now=T0 + 200)["controller"]["open_sessions"] == 1


def test_a_model_s_text_keeps_what_its_hidden_characters_leave_and_is_marked_cut_where_it_was(env):
    """A model's text's bound, both ways: a text whose hidden characters make it longer than what is kept, but whose
    plain words fit, is kept whole within the bound; and one that runs past the bound is marked cut however little of
    what was read is kept."""
    user = {"id": "u1"}
    setup_plain(user)
    hidden = chr(0x200B)
    qa, qb = uid(), uid()
    take(user, question(("a" + hidden) * 1500, qid=qa), question("x" + hidden * 7999 + "y" * 100, qid=qb))
    with store.connect() as db:
        rows = {r["id"]: r for r in db.execute("SELECT id, text, cut, hidden FROM ade_controller_questions")}
    assert (rows[qa]["text"], rows[qa]["cut"], rows[qa]["hidden"]) == ("a" * 1500, 0, 1500)
    assert (rows[qb]["text"], rows[qb]["cut"]) == ("x", 1)


def test_the_board_shows_the_newest_closed_requirements_of_the_week_and_strays_of_the_week(env):
    """The other bounds of what the page reads: the LISTED closed most lately in the last week, and the sessions started
    for no requirement that ended in it."""
    user = {"id": "u1"}
    setup(user, cap=10)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    ids = [uid() for _ in range(controller.LISTED + 1)]
    take(user, *[plan(request["id"], "Make the README say hello.", rid=r) for r in ids], now=T0 + 10)
    for i, r in enumerate(ids):
        controller.close(user, r, "done", now=T0 + 20 + i)
    old, recent = start(None, session=None, at=T0 + 20), start(None, session=None, at=T0 + 21)
    take(user, old, recent, tasks_=[{"id": old["task"], "state": "done", "ended_at": T0 + 30},
                                    {"id": recent["task"], "state": "done", "ended_at": T0 + 9 * 86400}],
         now=T0 + 9 * 86400 + 10)
    rows = controller.board_rows("u1", T0 + 9 * 86400 + 20)
    assert [r["id"] for r in rows if r["kind"] == "session"] == [recent["task"]]
    rows = controller.board_rows("u1", T0 + 100)
    assert [r["id"] for r in rows if r["kind"] == "requirement"] == ids[::-1][:controller.LISTED]


# ---- what the page reads cost: the reads bounded --------------------------------------------------------------------

def work(fn, *args, **kw):
    """The thousands of SQLite virtual-machine steps a call costs, every connection it opens counted: what it reads,
    whatever plan SQLite chose for it."""
    steps = [0]
    real = sqlite3.connect

    def connect(*a, **k):
        conn = real(*a, **k)

        def tick():
            steps[0] += 1
            return 0
        conn.set_progress_handler(tick, 1000)
        return conn
    store.sqlite3.connect = connect
    try:
        fn(*args, **kw)
    finally:
        store.sqlite3.connect = real
    return steps[0]


def old_history(user_id, n, now):
    """`n` sessions that ended more than a week before `now`: half started for no requirement, half for a requirement
    closed then, written straight to the store, as months of a controller's work would leave them."""
    long_ago = now - controller.KEPT_CLOSED - 86400
    rid = uid()
    with store.connect() as db:
        db.execute("INSERT INTO ade_controller_requirements (id, user_id, request_id, title, words, repos, state, "
                   "planned_at, closed_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (rid, user_id, uid(), "Old", "Old.", '["demo"]', "done", long_ago - 10, long_ago, long_ago,
                    long_ago))
        db.executemany(
            "INSERT INTO ade_controller_tasks (id, user_id, requirement_id, session_id, repo, account, program, model, "
            "effort, state, started_at, ended_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(uid(), user_id, rid if i % 2 else None, None, "demo", "default", "claude", "opus", "max", "done",
              long_ago - 5, long_ago, long_ago, long_ago) for i in range(n)])


def a_controller_at_work(user):
    """A controller with an open requirement, a session open for it, one for no requirement, and a question."""
    setup(user, cap=10)
    _, rid = planned(user)
    a, stray = start(rid), start(None, session=OTHER)
    take(user, a, stray, question("Which?"), tasks_=[{"id": a["task"], "state": "working"}])
    return rid


@pytest.mark.parametrize("stats", [False, True], ids=["as kept", "analyzed"])
@pytest.mark.parametrize("what", ["view", "board", "packet", "alerts", "a start"])
def test_the_page_reads_and_a_start_read_nothing_of_sessions_ended_long_ago(env, what, stats):
    """What the view, the board, the packet, the alerts and the cap's count at a start read does not grow with
    sessions that ended more than a week ago, with no requirement or of a requirement closed then: the same work
    with thousands of them as with none, near enough (the indexes on (owner, state), (owner, end) and (owner, flag
    time)), whether SQLite has statistics of the tables (ANALYZE, which the app never runs) or not, since what plan
    it chooses depends on them. Before, each read every task the owner ever had."""
    now = T0 + 200
    costs = []
    for n in (0, 4000):
        user = auth_user()
        rid = a_controller_at_work(user)
        old_history(user["id"], n, now)
        if stats:
            with store.connect() as db:
                db.execute("ANALYZE")
        calls = {"view": lambda: controller.view(user, now=now),
                 "board": lambda: controller.board_rows(user["id"], now),
                 "packet": lambda: controller.packet_of(user),
                 "alerts": lambda: controller.alerts(user["id"], now),
                 "a start": lambda: take(user, start(rid, session="cccccccccccc", at=T0 + 150), now=now)}
        costs.append(work(calls[what]))
    # measured: 0 more with this change; before it, 20 (the alerts) to 183 (the packet) thousand more
    assert costs[1] - costs[0] < 5, costs


def test_a_session_started_for_no_requirement_that_ended_more_than_a_week_ago_is_shown_nowhere(env):
    """The view, the board and the packet show the sessions started for no requirement that are open or ended in the
    last week, as the board already did, and count only those: one that ended before is in none of them."""
    user = auth_user()
    setup(user, cap=10)
    old, recent, live = (start(None, session=None, at=T0 + 20 + i) for i in range(3))
    take(user, old, recent, live, tasks_=[{"id": old["task"], "state": "done", "ended_at": T0 + 30},
                                          {"id": recent["task"], "state": "done", "ended_at": T0 + 9 * 86400}],
         now=T0 + 9 * 86400 + 10)
    now = T0 + 9 * 86400 + 20
    shown = {live["task"], recent["task"]}
    assert {t["id"] for t in controller.view(user, now=now)["other_sessions"]} == shown
    assert {r["id"] for r in controller.board_rows(user["id"], now) if r["kind"] == "session"} == shown
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, user["id"], now)[0])
    assert {t["id"] for t in packet["information"]["other_sessions"]} == shown
    assert packet["information"]["more_other_sessions"] == 0


def test_the_view_lists_the_sessions_started_for_no_requirement_as_the_board_and_the_packet_do(env):
    """Open ones first, oldest started first, then the ended ones, newest ended first: the order a requirement's
    sessions are listed in, in all three."""
    user = auth_user()
    setup(user, cap=10)
    first, second, ended_last, ended_before = (start(None, session=None, at=T0 + 20 + i) for i in range(4))
    take(user, first, second, ended_last, ended_before,
         tasks_=[{"id": ended_last["task"], "state": "done", "ended_at": T0 + 100},
                 {"id": ended_before["task"], "state": "stopped", "ended_at": T0 + 90}], now=T0 + 200)
    order = [first["task"], second["task"], ended_last["task"], ended_before["task"]]
    assert [t["id"] for t in controller.view(user, now=T0 + 300)["other_sessions"]] == order
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, user["id"], T0 + 300)[0])
    assert [t["id"] for t in packet["information"]["other_sessions"]] == order


def test_a_requirement_s_board_row_names_its_newest_open_session_else_the_one_that_ended_last(env):
    """Read without reading all of its sessions: by state, then by end."""
    user = auth_user()
    setup(user, cap=10)
    _, rid = planned(user)
    early, late = start(rid, session="aaaaaaaaaaaa", at=T0 + 20), start(rid, session=OTHER, at=T0 + 21)
    take(user, early, late, tasks_=[{"id": early["task"], "state": "done", "ended_at": T0 + 90},
                                    {"id": late["task"], "state": "done", "ended_at": T0 + 60}], now=T0 + 100)
    row = next(r for r in controller.board_rows(user["id"], T0 + 200) if r["kind"] == "requirement")
    assert row["session"] == "aaaaaaaaaaaa"
    newest = start(rid, session="dddddddddddd", at=T0 + 150)
    take(user, newest, tasks_=[{"id": newest["task"], "state": "working"}], now=T0 + 160)
    row = next(r for r in controller.board_rows(user["id"], T0 + 200) if r["kind"] == "requirement")
    assert row["session"] == "dddddddddddd"


def test_the_sql_sets_of_open_and_ended_states_are_the_task_states(env):
    assert controller._ENDED_SQL == "(" + ", ".join(f"'{s}'" for s in controller.TASK_ENDED) + ")"
    assert controller._OPEN_SQL == "(" + ", ".join(
        f"'{s}'" for s in controller.TASK_STATES if s not in controller.TASK_ENDED) + ")"


def test_a_plan_spends_one_of_the_day_s_lines_however_many_checks_it_passed(env, monkeypatch):
    """The plan's look at the day's lines spends none; only the plan taken does."""
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", 2)
    got = take(user, plan(request["id"], "Make the README say hello."), question("Which?"))
    assert (got["taken"], got["refused"]) == (2, 0)


def test_past_the_day_s_lines_a_question_is_refused(env, monkeypatch):
    """A question is the record's line, so the day's lines bound it as they bound a plan."""
    user = {"id": "u1"}
    setup_plain(user)
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", 0)
    qid = uid()
    assert take(user, question("Which?", qid=qid))["refused"] == 1
    assert [e["payload"]["why"] for e in events("u1", "question.refused")] == [controller._past_lines()]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_controller_questions").fetchone()[0] == 0


def test_a_session_s_state_reaches_only_the_owner_s_own_task_of_that_id(env):
    """Another owner's task of the same id keeps its state when this owner's record gives one."""
    for user_id in ("u1", "u2"):
        setup({"id": user_id}, cap=10)
    tid = uid()
    take({"id": "u1"}, start(None, tid=tid))
    take({"id": "u2"}, start(None, tid=tid), tasks_=[{"id": tid, "state": "working"}])
    take({"id": "u1"}, tasks_=[{"id": tid, "state": "done", "ended_at": T0 + 50}])
    assert (task_row(tid, "u1")["state"], task_row(tid, "u2")["state"]) == ("done", "working")


def plans(fn, *args, **kw):
    """Each statement a call runs on the controller's sessions or journal, with SQLite's plan for it."""
    seen = []
    real = sqlite3.connect

    def connect(*a, **k):
        conn = real(*a, **k)
        conn.set_trace_callback(lambda sql: seen.append(sql) if re.match(r"\s*SELECT", sql) and re.search(
            r"ade_controller_(tasks|events)", sql) else None)
        return conn
    store.sqlite3.connect = connect
    try:
        fn(*args, **kw)
    finally:
        store.sqlite3.connect = real
    with store.connect() as db:
        return [(sql, [tuple(r)[-1] for r in db.execute("EXPLAIN QUERY PLAN " + sql)]) for sql in dict.fromkeys(seen)]


def test_a_shown_requirement_s_listings_are_read_from_the_newest_back(env):
    """Its ended sessions from the newest end back, and each row's lines from the newest back, through their indexes:
    no statement that lists them sorts all of them first (SQLite's plan names no whole sort)."""
    user = auth_user()
    setup(user, cap=10)
    _, rid = planned(user)
    a = start(rid)
    take(user, a, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 50}])
    listings = [(sql, plan) for fn, args in ((controller.board_rows, (user["id"], T0 + 200)),
                                            (controller.requirement_view, (user, rid, T0 + 200)))
                for sql, plan in plans(fn, *args) if "ORDER BY t.ended_at DESC" in sql or "ORDER BY at DESC" in sql]
    assert len(listings) >= 3, listings
    assert all("USE TEMP B-TREE FOR ORDER BY" not in plan for _, plan in listings), listings


@pytest.mark.parametrize("stats", [False, True], ids=["as kept", "analyzed"])
def test_a_start_reads_nothing_of_its_requirement_s_long_journal(env, stats):
    """Whether the requirement was held at the start's time is read from the owner's holds and releases, not from
    every line of its journal, with or without SQLite's statistics."""
    now = T0 + 200
    costs = []
    for n in (0, 4000):
        user = auth_user()
        rid = a_controller_at_work(user)
        with store.connect() as db:
            db.executemany("INSERT INTO ade_controller_events (user_id, kind, requirement_id, task_id, at, recorded_at, "
                           "payload) VALUES (?,?,?,?,?,?,?)",
                           [(user["id"], "task.ended", rid, uid(), T0 + 100, T0 - 3 * 86400, "{}") for _ in range(n)])
            if stats:
                db.execute("ANALYZE")
        costs.append(work(take, user, start(rid, session="cccccccccccc", at=T0 + 150), now=now))
    assert costs[1] - costs[0] < 5, costs


# ---- versions, the packet's and the mirror's order and bytes, failures told, and the week's bounds -------------------

def test_a_start_made_while_off_is_read_by_the_poll_and_flagged(client, owner, ws, monkeypatch):
    """Once Off reached the workspace, the poll synced only owners whose
    controller was not off, so a session the workspace started while off was never read. Every owner with a controller
    is read now, and such a start is kept, flagged and told."""
    h, user = owner
    setup_plain(user)
    monkeypatch.setattr(agents, "_slow_work", lambda user, endpoint: None)
    agents.poll_all()
    controller.set_state(user, "off")
    controller.sync(user, "http://workspace.test")
    assert ws.mirrors[-1]["state"] == "off"
    s = start(None, at=int(time.time()))
    ws.pages.append({"seq": 9, "more": False, "actions": [s], "tasks": []})
    agents.poll_all()
    t = task_row(s["task"], user["id"])
    assert t is not None and "started while the Manager was off" in t["flag"]
    assert any(k.startswith("controller-look") for k, _ in controller.alerts(user["id"]))


def test_a_404_to_the_read_alone_still_sends_the_owner_s_controls_and_is_said(env, ws, capsys):
    """Any 404 to the read kept every mirror unsent, and nothing said so, so a
    route that answers 404 for a record it does not have (a new volume, a file removed) kept Stop from the workspace.
    Only a 404 to the mirror too marks a workspace without the routes now; a 404 to the read alone is a read that
    failed, said and told, and the mirror goes."""
    user = auth_user()
    setup_plain(user)
    assert controller.sync(user, "http://workspace.test") is True
    ws.fail = 404
    controller.set_state(user, "stopped", now=T0 + 50)
    assert controller.sync(user, "http://workspace.test") is True
    assert ws.mirrors[-1]["state"] == "stopped"
    said = "The workspace refused the read of its record (404)."
    assert f"reading the workspace's record failed: {said}" in capsys.readouterr().err
    c = controller.view(user, now=T0 + 60)["controller"]
    assert (c["read_error"], c["mirrored_version"]) == (said, c["version"]) and c["read_error_at"] is not None
    assert any(k.startswith("controller-look") for k, _ in controller.alerts(user["id"]))


def test_a_failed_call_is_said_with_the_status_the_workspace_answered(env):
    """The words "The workspace answered 502." were said for any status but a refusal, since
    workspace_client gives every other status as 502; its own words carry the status, and a refusal's words, which a
    program in the workspace may have chosen, are never said."""
    wc = workspace_client
    assert controller._failure(wc.WorkspaceCallError(502, "The workspace answered 503."), "it") == (
        "The workspace answered 503.")
    assert controller._failure(wc.NotAnswering(502, wc.NOT_ANSWERING), "it") == (
        "The workspace did not answer, or the app could not read its answer.")
    assert controller._failure(wc.WorkspaceCallError(409, "words a program chose"), "the read") == (
        "The workspace refused the read (409).")


def test_states_past_what_a_page_reads_move_no_version_and_spend_no_refusal(env, monkeypatch):
    """A page giving more sessions' states than the app reads moved the
    version, so the mirror went again at every sync, and journaled a refusal each time, so that after a day's
    refusals a real one lost its line. The contract now asks for the open sessions and those changed since the page's
    change number only; a page past the most moves nothing and is told once a day, apart from the refusals."""
    monkeypatch.setattr(controller, "ACTIONS_A_PAGE", 3)
    user = {"id": "u1"}
    setup(user, cap=10)
    started = [start(None, session=None, at=T0 + 10) for _ in range(4)]
    take(user, *started[:3], now=T0 + 20)
    take(user, started[3], now=T0 + 21)
    states = [{"id": s["task"], "state": "done", "ended_at": T0 + 30} for s in started]
    take(user, tasks_=states, now=T0 + 40)
    with store.connect() as db:
        version = controller._row(db, "u1")["version"]
    for i in range(controller.REFUSED_A_DAY):
        take(user, tasks_=states, now=T0 + 100 + i)
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] == version
    bad = {"id": uid(), "kind": "plan", "at": T0 + 400}
    assert take(user, bad, now=T0 + 400)["refused"] == 1
    assert [e["payload"]["why"] for e in events("u1", "plan.refused")] == ["it names no requirement"]
    assert len(events("u1", "record.states_unread")) == 1


def test_a_change_of_the_controller_s_chat_moves_no_version(env):
    """The controller's chat session is in neither the mirror nor the packet, so a page
    naming a new one has it kept and journaled and moves no version, as a refusal moves none."""
    user = {"id": "u1"}
    setup_plain(user)
    with store.connect() as db:
        version = controller._row(db, "u1")["version"]
    take(user, session=SESSION)
    take(user, session=OTHER)
    with store.connect() as db:
        row = controller._row(db, "u1")
        assert (row["session_id"], row["version"]) == (OTHER, version)
    assert [e["payload"] for e in events("u1", "controller.chat")] == [{"session": SESSION}, {"session": OTHER}]


def test_a_page_whose_actions_are_all_refused_moves_no_version(env):
    """A refusal is in neither the mirror nor the packet, so a page whose every action the
    app refuses moves no version and has nothing sent again; a page with one action taken moves it."""
    user = {"id": "u1"}
    setup(user, cap=10)
    with store.connect() as db:
        version = controller._row(db, "u1")["version"]
    bad = [{"id": uid(), "kind": "plan", "at": T0 + 40}, act("stop", uid(), at=T0 + 40)]
    assert take(user, *bad, now=T0 + 40)["refused"] == 2
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] == version
    got = take(user, start(None, session=None, at=T0 + 50), now=T0 + 50)
    assert (got["refused"], got["taken"] + got["flagged"]) == (0, 1)
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] == version + 1


def test_a_stop_moves_no_version_and_a_continue_does(env):
    """A stop moved the version though a session's stop is in neither the mirror nor
    the packet, so the manual's "only when something the mirror or the packet holds changes" was not so; a continue,
    whose count and flag the packet holds, moves it."""
    user = {"id": "u1"}
    setup(user, cap=10)
    a = start(None, session=None, at=T0 + 10)
    take(user, a, now=T0 + 20)

    def packet():                           # its nonce aside, which each packet draws anew
        with store.connect() as db:
            return dict(json.loads(controller.build_packet(db, "u1", T0 + 100)[0]), nonce=None)
    with store.connect() as db:
        version = controller._row(db, "u1")["version"]
    before = packet()
    assert take(user, act("stop", a["task"], at=T0 + 30), now=T0 + 40)["taken"] == 1
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] == version
    assert packet() == before and task_row(a["task"])["stopped_at"] == T0 + 30
    assert take(user, act("continue", a["task"], at=T0 + 50), now=T0 + 60)["taken"] == 1
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] == version + 1


def test_the_cap_s_most_is_what_the_workspace_s_sessions_leave_beside_the_controller_s_chat_and_an_attempt(env):
    """The cap could be set to the workspace's MAX_SESSIONS, all 12 of its programs,
    though the controller's own chat is one of them, where it runs from one to what MAX_SESSIONS leaves. With a
    requirement's own manager, a running attempt holds no place in the cap, so the cap leaves one place beside it
    for the attempt's chat too: 10, and 11, the
    most before, refused."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "proofpane_workspace" / "workspace.py").read_text("utf-8")
    most = int(re.search(r"^MAX_SESSIONS = (\d+)$", src, re.M)[1])
    assert controller.CAP_MOST == most - 2 == 10
    user = {"id": "u1"}
    setup(user)
    assert controller.change_settings(user, {"cap": most - 2}, now=T0 + 10)["controller"]["cap"] == most - 2
    for cap in (most - 1, most):
        with pytest.raises(agents.AgentError) as e:
            controller.change_settings(user, {"cap": cap}, now=T0 + 20)
        assert (e.value.status, str(e.value)) == (400, "The cap is a whole number from 1 to 10.")


def test_a_cap_of_11_kept_from_before_is_read_as_10_wherever_the_app_reads_it(env):
    """A cap above 10 kept from before the build is read as 10 wherever either
    side reads it: the page's settings, the mirror, the packet, and the bounds a start is judged by."""
    user = {"id": "u1"}
    setup(user, cap=10)
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET cap = 11 WHERE user_id = ?", (user["id"],))
        # the owner's change of the cap to 11 as the journal kept it before, which _settings_at replays
        controller.journal(db, user["id"], "controller.settings", {"changed": {"cap": 11}}, now=T0 + 5)
        assert controller._settings_at(db, user["id"], T0 + 10)["cap"] == 10
        body, _, _ = controller.mirror_body(db, user["id"], now=T0 + 10)
        data, _ = controller.build_packet(db, user["id"], now=T0 + 10)
    assert body["cap"] == 10
    assert json.loads(data)["controller"]["cap"] == 10
    assert controller.view(user, now=T0 + 10)["controller"]["cap"] == 10


def test_the_mirror_gives_every_request_the_packet_gives_first_in_its_order(env):
    """The mirror listed the requests newest first and the packet the ones not
    yet planned first, so a request the packet told a fresh controller to plan could be cut from the mirror, which the
    workspace checks a plan against. The mirror now gives every request the packet gives, in its order, first."""
    user = {"id": "u1"}
    setup(user, cap=10)
    first = controller.add_request(user, "Do task one.", now=T0 + 1)
    big = chr(0x6587) * 2600
    for i in range(5):
        r = controller.add_request(user, big + str(i), now=T0 + 2 + i)
        assert take(user, plan(r["id"], big + str(i), at=T0 + 10 + i), now=T0 + 20 + i)["taken"] == 1
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, "u1", T0 + 100)[0])
        body, _, _ = controller.mirror_body(db, "u1", T0 + 100)
    given = [r["id"] for r in p["instructions"]["requests"]]
    assert given[0] == first["id"] and p["instructions"]["more_requests"] >= 1
    assert [r["id"] for r in body["requests"]][:len(given)] == given
    assert body["more_requests"] == 6 - len(body["requests"])


def test_the_mirror_holds_each_request_the_packet_gives_though_ones_before_it_are_left_out(env):
    """The packet may give a request of one list after leaving out some of the list
    before it; the mirror gives the packet's requests first, so it holds that one too."""
    user = {"id": "u1"}
    setup(user, cap=10)
    closed = controller.add_request(user, "Tidy up.", now=T0 + 1)
    rid = uid()
    assert take(user, plan(closed["id"], "Tidy up.", rid=rid), now=T0 + 2)["taken"] == 1
    controller.close(user, rid, "done", now=T0 + 3)
    big = chr(0x6587) * 2600
    for i in range(8):
        r = controller.add_request(user, big + str(i), now=T0 + 10 + i)
        assert take(user, plan(r["id"], big + str(i), at=T0 + 20 + i), now=T0 + 30 + i)["taken"] == 1
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, "u1", T0 + 100)[0])
        body, _, _ = controller.mirror_body(db, "u1", T0 + 100)
    given = [r["id"] for r in p["instructions"]["requests"]]
    assert closed["id"] in given and p["instructions"]["more_requests"] >= 1
    mirrored = [r["id"] for r in body["requests"]]
    assert mirrored[:len(given)] == given and body["more_requests"] >= 1


def test_the_mirror_holds_every_request_the_packet_gives_at_their_smallest(env):
    """MIRROR_REQUESTS_BYTES, worked out (its comment): a request costs the mirror 85 bytes more than the packet, apart
    from its words, and its words at most 6/5 of what the packet holds, so the most requests the mirror must hold are
    the packet's instructions filled with requests of one character. Stored as the owner's past what add_request
    lets wait, so that the packet's budget alone bounds them, every one the packet gives is in the mirror, first, in
    the packet's order."""
    user = {"id": "u1"}
    setup(user, cap=10)
    at = 1_800_000_000
    with store.connect() as db:
        for n in range(1, 321):
            db.execute("INSERT INTO ade_controller_requests (id, user_id, n, text, digest, created_at) VALUES "
                       "(?,?,?,?,?,?)", (uid(), "u1", n, "x", controller._digest("x"), at + n))
        p = json.loads(controller.build_packet(db, "u1", at + 1000)[0])
        body, _, _ = controller.mirror_body(db, "u1", at + 1000)
    given = [r["id"] for r in p["instructions"]["requests"]]
    assert (len(given), p["instructions"]["more_requests"]) == (319, 1)
    assert [r["id"] for r in body["requests"]][:len(given)] == given


def test_a_withdrawal_reaches_the_workspace_at_the_next_sync(env, ws):
    user = auth_user()
    setup_plain(user)
    r = controller.add_request(user, "Never mind.", now=T0 + 1)
    controller.sync(user, "http://workspace.test")
    assert r["id"] in [x["id"] for x in ws.mirrors[-1]["requests"]]
    controller.withdraw(user, r["id"], now=T0 + 2)
    assert controller.sync(user, "http://workspace.test") is True
    assert r["id"] not in [x["id"] for x in ws.mirrors[-1]["requests"]]


def test_the_owner_withdraws_a_request_no_plan_was_taken_from(client, owner, ws):
    """Requests the controller never planned filled what a fresh controller is
    given, and nothing withdrew one, so the owner could send no more. One not yet planned can be withdrawn now: it is
    no longer given, and a plan from it is refused; one planned from cannot be."""
    h, user = owner
    setup_plain(user)
    big = chr(0x6587) * 2700
    ids = []
    while True:
        r = client.post(C + "/requests", headers=h, json={"text": big + str(len(ids))})
        if r.status_code != 200:
            break
        ids.append(r.json()["id"])
    assert r.status_code == 409 and len(ids) == 2 and "withdraw" in r.json()["detail"]
    w = client.post(f"{C}/requests/{ids[0]}/withdraw", headers=h)
    assert w.status_code == 200 and w.json()["withdrawn_at"] is not None
    assert client.post(f"{C}/requests/{ids[0]}/withdraw", headers=h).status_code == 409
    assert client.post(f"{C}/requests/{uid()}/withdraw", headers=h).status_code == 404
    assert client.post(C + "/requests", headers=h, json={"text": "x"}).status_code == 200
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, user["id"])[0])
        body, _, _ = controller.mirror_body(db, user["id"])
    assert ids[0] not in [r["id"] for r in p["instructions"]["requests"] + body["requests"]]
    assert take(user, plan(ids[0], big + "0"))["refused"] == 1
    assert events(user["id"], "plan.refused")[-1]["payload"]["why"] == "it names a request the owner withdrew"
    assert take(user, plan(ids[1], big + "1"))["taken"] == 1
    assert client.post(f"{C}/requests/{ids[1]}/withdraw", headers=h).status_code == 409    # planned from
    assert [e["payload"] for e in events(user["id"], "request.withdrawn")] == [{"request": ids[0], "n": 1}]
    assert controller.words_of({"kind": "request.withdrawn", "payload": json.dumps({"n": 1})}) == (
        "You withdrew request 1")
    listed = {r["id"]: r for r in client.get(C + "/requests", headers=h).json()["requests"]}
    assert listed[ids[0]]["withdrawn_at"] is not None and listed[ids[1]]["withdrawn_at"] is None


def test_the_owner_s_decisions_come_before_the_requests_already_planned(env):
    """The decisions were filled after every request, so requests already
    planned crowded out the owner's rulings first. The decisions come right after the requests not yet planned now,
    and what is left out is the oldest request already planned; the question a given decision answers is given too."""
    user = {"id": "u1"}
    setup(user, cap=10)
    qid = uid()
    assert take(user, question("Which branch?", qid=qid, at=T0 + 30), now=T0 + 40)["taken"] == 1
    d = controller.decide(user, "Never push to main. " + chr(0x6587) * 600, qid, now=T0 + 50)
    big = chr(0x6587) * 2600
    for i in range(4):
        r = controller.add_request(user, big + str(i), now=T0 + 60 + i)
        assert take(user, plan(r["id"], big + str(i), at=T0 + 70 + i), now=T0 + 80 + i)["taken"] == 1
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, "u1", T0 + 200)[0])
    assert [x["id"] for x in p["instructions"]["decisions"]] == [d["id"]] and p["instructions"]["more_decisions"] == 0
    assert p["instructions"]["more_requests"] >= 1
    assert [a["id"] for a in p["information"]["answered_questions"]] == [qid]


def test_a_question_is_given_only_with_the_decision_that_answered_it(env):
    """A question answered is given only beside the decision that answered it; the
    oldest decisions are the ones left out, and their questions with them."""
    user = {"id": "u1"}
    setup(user)
    decisions = []
    for i in range(5):
        qid = uid()
        take(user, question(f"Question {i}?", qid=qid, at=T0 + 30 + i), now=T0 + 40 + i)
        decisions.append(controller.decide(user, f"Answer {i}: " + chr(0x6587) * 1900, qid, now=T0 + 50 + i))
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, "u1", T0 + 100)[0])
    given = [d["id"] for d in p["instructions"]["decisions"]]
    assert 0 < len(given) < 5 and p["instructions"]["more_decisions"] == 5 - len(given)
    assert given == [d["id"] for d in decisions[::-1][:len(given)]]
    answered = [q["decision"] for q in p["information"]["answered_questions"]]
    assert answered == given and p["information"]["more_answered"] == 0


def test_a_decision_is_sized_as_the_packet_holds_it(env):
    """A line feed is one byte as written and five as the packet holds it."""
    with pytest.raises(agents.AgentError) as refused:
        controller.decide({"id": "u1"}, "x" + "\n" * 1999, now=T0)
    assert refused.value.status == 400 and "past 6144" in str(refused.value)


def test_a_decision_of_exactly_its_bytes_as_the_packet_holds_it_is_taken(env):
    def item(text):
        return {"id": "0" * 32, "n": 1, "at": T0, "question": None, "text": tasks.owner_text(text)}
    rest = controller.DECISION_PACKET_MAX - len(controller._canon(item("")))
    text = chr(0x1F600) * (rest // 4) + "y" * (rest % 4)
    assert len(controller._canon(item(text))) == controller.DECISION_PACKET_MAX
    assert controller.decide({"id": "u1"}, text, now=T0)["n"] == 1
    with pytest.raises(agents.AgentError) as refused:
        controller.decide({"id": "u2"}, text + "y", now=T0)
    assert refused.value.status == 400


def test_the_requests_not_yet_planned_leave_the_owner_s_decisions_their_share(env):
    """Three requests of 6,000 bytes fit the instructions' 24,576 bytes but not the
    17,408 the requests not yet planned may fill, so that the newest decision, at its most, always fits beside them."""
    user = {"id": "u1"}
    text = chr(0x6587) * 2000
    controller.add_request(user, text + "1", now=T0)
    controller.add_request(user, text + "2", now=T0)
    with pytest.raises(agents.AgentError) as refused:
        controller.add_request(user, text + "3", now=T0)
    assert refused.value.status == 409
    d = controller.decide(user, chr(0x6587) * 2000, now=T0 + 1)
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, "u1", T0 + 2)[0])
    assert len(p["instructions"]["requests"]) == 2 and [x["id"] for x in p["instructions"]["decisions"]] == [d["id"]]


def test_a_decision_is_refused_past_what_the_packet_keeps_for_one(env):
    user = {"id": "u1"}
    setup(user)
    with pytest.raises(agents.AgentError) as refused:
        controller.decide(user, chr(0x1F600) * 2000, now=T0 + 5)
    assert refused.value.status == 400 and "past 6144" in str(refused.value)
    assert controller.decide(user, chr(0x6587) * 2000, now=T0 + 6)["n"] == 1


def test_the_owner_s_words_shaped_like_a_secret_once_escaped_are_refused(client, owner):
    """The owner's words were checked as written, and given escaped, where a line
    feed is written \\x0a: a private key's first line over two lines was let in, and the packet could not be built."""
    h, user = owner
    text = "Rotate the deploy key.\n\nThe old file starts -----BEGIN OPENSSH\nand its PRIVATE KEY must go."
    for path in ("/requests", "/decisions"):
        r = client.post(C + path, headers=h, json={"text": text})
        assert r.status_code == 400 and "private key" in r.json()["detail"], r.text


def test_the_owner_s_words_shaped_like_a_secret_as_written_are_refused_though_escaped_they_are_not(env):
    """After a line feed, a shape that starts at a word's start is one as written; escaped, the line feed's \\x0a
    ends in a letter, and the shape's start is inside a word: both forms are checked."""
    with pytest.raises(agents.AgentError) as refused:
        controller.add_request({"id": "u1"}, "The key:\nAKIA" + "A" * 16, now=T0)
    assert refused.value.status == 400 and "AWS" in str(refused.value)


def test_a_cut_of_the_owner_s_words_never_ends_in_a_secret_s_shape(env):
    """A requirement past its bytes has its words cut, and the cut could end them in
    the shape of a ticket (PP- and eight letters, where a word ends), which the whole did not hold: the packet could
    then not be built. The cut is made shorter instead."""
    user = {"id": "u1"}
    setup(user, cap=10)
    words = "a" * (controller.WORDS_SHOWN - 12) + " PP-ABCDEFGHJK " + "b" * 2900
    request = controller.add_request(user, words, now=T0 + 1)
    assert take(user, plan(request["id"], words))["taken"] == 1
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, "u1", T0 + 100)[0])
    cut = p["information"]["requirements"][0]["words"]
    assert cut["cut"] is True and tasks.secret_shape(cut["text"]) is None
    assert cut["text"] == "a" * (controller.WORDS_SHOWN - 12) + " PP-ABCDEFG"


def test_a_mirror_sent_without_the_packet_is_told(env, ws, monkeypatch):
    """A packet that could not be built was once said only in the view."""
    user = auth_user()
    setup_plain(user)
    monkeypatch.setattr(controller, "build_packet", lambda db, user_id, now=None: (_ for _ in ()).throw(
        controller.PacketError("The packet does not fit.")))
    assert controller.sync(user, "http://workspace.test") is True
    assert ws.mirrors[-1]["packet_error"] == "The packet does not fit."
    assert any(k.startswith("controller-look") for k, _ in controller.alerts(user["id"]))


def test_a_read_that_failed_is_told(env, ws):
    """A read that failed was once said only in the view."""
    user = auth_user()
    setup_plain(user)
    ws.fail = 500
    controller.sync(user, "http://workspace.test")
    assert controller.view(user)["controller"]["read_error"] == "The workspace answered 500."
    assert any(k.startswith("controller-look") for k, _ in controller.alerts(user["id"]))


def test_a_mirror_the_workspace_does_not_take_is_said_and_told_until_one_goes_through(env, ws, monkeypatch):
    """A mirror the workspace refused or failed was said only on standard
    error, so the owner's Stop could miss the workspace round after round with nothing told. It is recorded with why
    in the app's own words, since when it has been so, shown and told, until a mirror goes through."""
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    user = auth_user()
    setup_plain(user)
    ws.fail_put = 500
    assert controller.sync(user, "http://workspace.test") is False
    c = controller.view(user)["controller"]
    assert (c["mirror_error"], c["mirror_error_at"], c["read_error"]) == ("The workspace answered 500.", T0 + 1000,
                                                                          None)
    assert look(T0 + 1000) in controller.alerts(user["id"], T0 + 1000)
    clock[0] = T0 + 2000
    ws.fail_put = 409
    controller.sync(user, "http://workspace.test")
    clock[0] = T0 + 3000
    controller.sync(user, "http://workspace.test")
    c = controller.view(user)["controller"]
    assert (c["mirror_error"], c["mirror_error_at"]) == ("The workspace refused the mirror (409).", T0 + 2000)
    ws.fail_put = None
    clock[0] = T0 + 4000
    assert controller.sync(user, "http://workspace.test") is True
    c = controller.view(user)["controller"]
    assert (c["mirror_error"], c["mirror_error_at"]) == (None, None)
    assert not any(k.startswith("controller-look") for k, _ in controller.alerts(user["id"], T0 + 4000))


def test_a_failure_that_lasts_is_told_each_day_and_a_change_that_does_not_reach_the_workspace_at_once(env, ws,
                                                                                                    monkeypatch):
    """A failure kept with since when was told for one day from when it began,
    whatever came after: a mirror refused the same way for days told nothing past its first day, and a Stop pressed
    while it was refused was told by nothing. Each is told again each day it lasts, and a mirror failing for a version
    no failed mirror carried before, a Stop's, is told as new; nothing once a mirror goes through."""
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    user = auth_user()
    setup_plain(user)
    ws.fail_put = 400
    assert controller.sync(user, "http://workspace.test") is False
    assert controller.alerts(user["id"], T0 + 1000) == [look(T0 + 1000)]
    clock[0] = T0 + 1000 + 3600             # the same failure, the same version, an hour on: nothing new
    controller.sync(user, "http://workspace.test")
    assert controller.alerts(user["id"], clock[0]) == [look(T0 + 1000)]
    clock[0] = T0 + 1000 + 2 * 3600         # the owner presses Stop: it fails too, and is told as new
    controller.set_state(user, "stopped", now=clock[0])
    controller.sync(user, "http://workspace.test")
    assert controller.alerts(user["id"], clock[0]) == [look(clock[0])]
    stop = clock[0]
    for day in (1, 2, 3):                   # nothing changes, and the failure lasts: told again each day
        clock[0] = stop + day * 86400 + 600
        assert controller.sync(user, "http://workspace.test") is False
        c = controller.view(user, now=clock[0])["controller"]
        assert c["mirror_error_at"] == T0 + 1000 and c["mirrored_version"] != c["version"]
        assert controller.alerts(user["id"], clock[0]) == [look(T0 + 1000 + day * 86400)]
    ws.fail_put = None
    assert controller.sync(user, "http://workspace.test") is True
    assert controller.alerts(user["id"], clock[0]) == []


@pytest.mark.parametrize("what", ["read", "packet"])
def test_a_read_or_a_packet_that_keeps_failing_is_told_each_day(env, ws, monkeypatch, what):
    """For a read and a packet too: a read failing, or a mirror going without the
    packet, the same way for days was told on its first day only."""
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    user = auth_user()
    setup_plain(user)
    if what == "read":
        ws.fail = 500
    else:
        def no_packet(db, user_id, now=None):
            raise controller.PacketError("The packet does not fit.")
        monkeypatch.setattr(controller, "build_packet", no_packet)
    controller.sync(user, "http://workspace.test")
    key = f"{what}_error_at"
    assert controller.view(user)["controller"][key] == T0 + 1000
    assert controller.alerts(user["id"], T0 + 1000) == [look(T0 + 1000)]
    for day in (1, 2):
        clock[0] = T0 + 1000 + day * 86400 + 60
        controller.sync(user, "http://workspace.test")
        assert controller.view(user)["controller"][key] == T0 + 1000
        assert controller.alerts(user["id"], clock[0]) == [look(T0 + 1000 + day * 86400)]


def test_a_mirror_unanswered_unreadable_or_not_built_is_said_in_the_app_s_own_words(env, ws, monkeypatch):
    user = auth_user()
    setup_plain(user)
    real = ws.call

    def put_fails(failure):
        def call(endpoint, method, path, body=None, timeout=30):
            if method == "PUT":
                raise failure
            return real(endpoint, method, path, body, timeout)
        return call
    # what workspace_client lets out: NotAnswering for no answer or one that is not JSON, and a RecursionError for
    # JSON nested past what Python reads (a ValueError, which it never lets out, once stood in for both)
    for failure, words in ((workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING),
                            "The workspace did not answer, or the app could not read its answer."),
                           (RecursionError("maximum recursion depth exceeded"),
                            "The workspace's answer to the mirror could not be read.")):
        monkeypatch.setattr(workspace_client, "call", put_fails(failure))
        assert controller.sync(user, "http://workspace.test") is False
        assert controller.view(user)["controller"]["mirror_error"] == words
    monkeypatch.setattr(workspace_client, "call", real)

    def broken(db, user_id, now=None):
        raise RuntimeError("the store's own words")
    monkeypatch.setattr(controller, "mirror_body", broken)
    assert controller.sync(user, "http://workspace.test") is False
    assert controller.view(user)["controller"]["mirror_error"] == "The app failed building the mirror (RuntimeError)."


def test_a_mirror_the_app_cannot_build_for_a_change_the_owner_makes_is_told_at_once(env, ws, monkeypatch):
    """A mirror the app fails to build carries the version it was built for, as one the workspace refuses does: the
    owner's Stop, built no better than the change before it, is told at once, not when the failure's day comes round."""
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    user = auth_user()
    setup_plain(user)

    def broken(db, user_id, now=None):
        raise RuntimeError("the store's own words")
    monkeypatch.setattr(controller, "mirror_body", broken)
    assert controller.sync(user, "http://workspace.test") is False
    assert controller.alerts(user["id"], clock[0]) == [look(T0 + 1000)]
    clock[0] = T0 + 1000 + 2 * 3600
    controller.set_state(user, "stopped", now=clock[0])
    assert controller.sync(user, "http://workspace.test") is False
    c = controller.view(user, now=clock[0])["controller"]
    assert c["mirror_error_at"] == T0 + 1000 and c["mirrored_version"] != c["version"]
    assert controller.alerts(user["id"], clock[0]) == [look(clock[0])]


@pytest.mark.parametrize("answer,words", [
    (b"<html>taken</html>", "The workspace did not answer, or the app could not read its answer."),
    (b"[" * 100000 + b"]" * 100000, "The workspace's answer to the mirror could not be read."),
])
def test_an_answer_to_the_mirror_the_real_client_cannot_read_is_said_as_it_is(env, answer, words):
    """Through the real client an answer that is not JSON comes as no answer,
    so the app said "The workspace did not answer." of a workspace that answered; the words now say either, and JSON
    nested past what Python reads, which the client lets out as it is, is said as an answer that could not be read."""
    import http.server
    import threading
    import types

    class Agent(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def answer(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.answer(json.dumps({"seq": 0, "more": False, "held": None, "actions": [], "tasks": []}).encode())

        def do_PUT(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.answer(answer)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Agent)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        user = auth_user()
        setup_plain(user)
        endpoint = types.SimpleNamespace(http=f"http://127.0.0.1:{server.server_port}", secret="s", ssl=None)
        assert controller.sync(user, endpoint, call=REAL_CALL) is False
        c = controller.view(user)["controller"]
        assert (c["mirror_error"], c["read_error"], c["mirrored_version"]) == (words, None, None)
    finally:
        server.shutdown()
        server.server_close()


def test_a_read_that_fails_after_a_page_said_more_is_behind_and_told(env, ws, monkeypatch):
    """A read whose next page failed after one said "more" left the floor where
    the last whole read set it, and was not counted as behind, so the owner was not told it stayed there."""
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    user = {"id": "u" + uid()[:8]}
    setup(user, cap=5)
    _, rid = planned(user)
    controller.sync(user, "http://workspace.test")
    clock[0] = T0 + 2000
    controller.set_state(user, "stopped", now=T0 + 2000)
    pages = [dict(record(start(rid, at=T0 + 1500)), seq=3, more=True)]
    real = ws.call

    def call(endpoint, method, path, body=None, timeout=30):
        if path.startswith("/controller/record?since=3"):
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        if path.startswith("/controller/record") and pages:
            return dict({"held": None}, **pages.pop(0))
        return real(endpoint, method, path, body, timeout)
    monkeypatch.setattr(workspace_client, "call", call)
    clock[0] = T0 + 3000
    controller.sync(user, "http://workspace.test")
    with store.connect() as db:
        c = controller._row(db, user["id"])
    assert (c["read_at"], c["read_behind_at"], c["read_error"]) == (
        T0 + 1000, T0 + 3000, "The workspace did not answer, or the app could not read its answer.")
    assert look(T0 + 3000) in controller.alerts(user["id"], T0 + 3000)


def test_a_read_begins_no_page_after_its_time_and_is_then_behind(env, ws, monkeypatch):
    """A sync could make ten reads and a send, a minute each, in the poll's round,
    holding up every other owner's sync. No page is begun once a minute has gone since the read began; a read cut so
    is behind, as one cut by the pages' count is."""
    clock = [T0 + 1000]
    monkeypatch.setattr(controller.time, "time", lambda: clock[0])
    user = {"id": "u" + uid()[:8]}
    setup(user)
    real = ws.call
    asked = []

    def call(endpoint, method, path, body=None, timeout=30):
        if path.startswith("/controller/record"):
            asked.append(path)
            clock[0] += 31                  # each page takes 31 seconds to come
            return {"seq": len(asked), "more": True, "held": None, "actions": [], "tasks": []}
        return real(endpoint, method, path, body, timeout)
    monkeypatch.setattr(workspace_client, "call", call)
    controller.sync(user, "http://workspace.test")
    assert len(asked) == 2                  # the second begun 31 seconds in, none at 62
    with store.connect() as db:
        c = controller._row(db, user["id"])
    assert (c["read_behind_at"], c["read_at"], c["seq"]) == (clock[0], None, 2)


def test_what_closed_or_ended_two_days_ago_is_shown_wherever_a_week_bounds_it(env):
    """Each place that keeps a week would pass the other tests keeping a day."""
    user = {"id": "u1"}
    setup(user, cap=10)
    request, rid = planned(user)
    stray = start(None, session=OTHER, at=T0 + 20)
    take(user, stray, tasks_=[{"id": stray["task"], "state": "done", "ended_at": T0 + 30}], now=T0 + 40)
    controller.close(user, rid, "done", now=T0 + 50)
    now = T0 + 50 + 2 * 86400
    view = controller.view(user, now=now)
    assert [r["id"] for r in view["requirements"]] == [rid]
    assert [t["id"] for t in view["other_sessions"]] == [stray["task"]]
    rows = controller.board_rows("u1", now)
    assert rid in [r["id"] for r in rows if r["kind"] == "requirement"]
    assert stray["task"] in [r["id"] for r in rows if r["kind"] == "session"]
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, "u1", now)[0])
        body, _, _ = controller.mirror_body(db, "u1", now)
    assert [r["id"] for r in p["information"]["requirements"]] == [rid]
    assert [t["id"] for t in p["information"]["other_sessions"]] == [stray["task"]]
    assert [r["id"] for r in p["instructions"]["requests"]] == [request["id"]]
    assert [r["id"] for r in body["requirements"]] == [rid]
    assert [r["id"] for r in body["requests"]] == [request["id"]]


def test_what_closed_or_ended_a_week_ago_to_the_second_is_shown_wherever_a_week_bounds_it_and_after_it_nowhere(env):
    """Each place a week bounds keeps the week's first second, so the view, the board, the
    packet and the mirror agree on what the week holds; a second later, none holds it."""
    user = {"id": "u1"}
    setup(user, cap=10)
    request, rid = planned(user)
    stray = start(None, session=OTHER, at=T0 + 20)
    take(user, stray, tasks_=[{"id": stray["task"], "state": "done", "ended_at": T0 + 50}], now=T0 + 60)
    controller.close(user, rid, "done", now=T0 + 50)
    for now, kept in ((T0 + 50 + controller.KEPT_CLOSED, True), (T0 + 51 + controller.KEPT_CLOSED, False)):
        view = controller.view(user, now=now)
        rows = controller.board_rows("u1", now)
        with store.connect() as db:
            p = json.loads(controller.build_packet(db, "u1", now)[0])
            body, _, _ = controller.mirror_body(db, "u1", now)
        held = [rid in [r["id"] for r in view["requirements"]],
                stray["task"] in [t["id"] for t in view["other_sessions"]],
                rid in [r["id"] for r in rows if r["kind"] == "requirement"],
                stray["task"] in [r["id"] for r in rows if r["kind"] == "session"],
                rid in [r["id"] for r in p["information"]["requirements"]],
                stray["task"] in [t["id"] for t in p["information"]["other_sessions"]],
                request["id"] in [r["id"] for r in p["instructions"]["requests"]],
                rid in [r["id"] for r in body["requirements"]],
                request["id"] in [r["id"] for r in body["requests"]]]
        assert held == [kept] * 9, (now - T0, held)


def test_a_request_with_a_requirement_open_and_one_closed_is_given_once(env):
    """A request planned into two requirements, one closed in the last week and one still
    open, is among the requests with a requirement open, and the packet and the mirror give it once."""
    user = {"id": "u1"}
    setup(user, cap=10)
    r = controller.add_request(user, "Do one thing.\n\nDo another.", now=T0 + 1)
    a, b = uid(), uid()
    got = take(user, plan(r["id"], "Do one thing.", rid=a, at=T0 + 10), plan(r["id"], "Do another.", rid=b, at=T0 + 11),
               now=T0 + 20)
    assert got["taken"] == 2
    controller.close(user, a, "done", now=T0 + 30)
    with store.connect() as db:
        p = json.loads(controller.build_packet(db, "u1", T0 + 100)[0])
        body, _, _ = controller.mirror_body(db, "u1", T0 + 100)
    assert [x["id"] for x in p["instructions"]["requests"]] == [r["id"]]
    assert p["instructions"]["more_requests"] == 0
    assert [x["id"] for x in body["requests"]] == [r["id"]]


def test_a_continue_made_while_stopped_is_flagged_though_read_after_on(env):
    """A continue judged by the controller's state when the app reads it."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, now=T0 + 25)
    controller.set_state(user, "stopped", now=T0 + 30)
    controller.set_state(user, "on", now=T0 + 50)
    assert take(user, act("continue", a["task"], at=T0 + 40), now=T0 + 60)["flagged"] == 1
    assert "continued while the Manager was stopped" in task_row(a["task"])["flag"]


def test_a_time_up_to_a_minute_past_the_app_s_clock_is_kept_and_one_past_that_is_now(env):
    user = {"id": "u1"}
    setup(user)
    take(user, question("A?", at=T0 + 160), question("B?", at=T0 + 161), now=T0 + 100)
    with store.connect() as db:
        asked = {r["text"]: r["asked_at"] for r in db.execute("SELECT text, asked_at FROM ade_controller_questions")}
    assert asked == {"A?": T0 + 160, "B?": T0 + 100}


def test_a_plan_whose_title_is_only_spaces_is_refused(env):
    user = {"id": "u1"}
    setup(user)
    request = controller.add_request(user, REQUEST, now=T0 + 5)
    assert take(user, plan(request["id"], "Make the README say hello.", title="   "))["refused"] == 1
    assert events("u1", "plan.refused")[-1]["payload"]["why"] == "it has no title"


def test_a_question_about_a_requirement_is_among_its_row_s_events(env):
    user = {"id": "u1"}
    setup(user)
    _, rid = planned(user)
    take(user, question("Which branch?", requirement=rid))
    row = next(r for r in controller.board_rows("u1", T0 + 200) if r["id"] == rid)
    assert "The Manager asked you a question" in [e["words"] for e in row["events"]]


def test_a_held_requirement_with_an_open_question_is_held(env):
    user = {"id": "u1"}
    setup(user)
    _, rid = planned(user)
    take(user, question("Which branch?", requirement=rid))
    controller.hold(user, rid, True, now=T0 + 200)
    assert controller.requirement_view(user, rid, now=T0 + 300)["activity"] == "held"


def test_a_start_on_another_account_past_the_cap_is_flagged_for_both(env):
    """The cap counts the controller's sessions on any account."""
    user = {"id": "u1"}
    setup(user, cap=1)
    _, rid = planned(user)
    a, b = start(rid, at=T0 + 20), start(rid, at=T0 + 30, session=OTHER, account="cdef01234567")
    take(user, a, b, now=T0 + 40)
    flag = task_row(b["task"])["flag"]
    assert "another account than the Manager's" in flag and "one more than the 1 sessions" in flag


def test_the_owner_may_allow_eight_models_and_no_more(env):
    user = {"id": "u1"}
    eight = [f"model-{i}" for i in range(controller.ALLOWED_MOST)]
    assert controller.change_settings(user, {"models": eight}, now=T0)["controller"]["models"] == eight
    with pytest.raises(agents.AgentError) as refused:
        controller.change_settings(user, {"models": eight + ["model-8"]}, now=T0)
    assert refused.value.status == 400


def test_a_request_of_exactly_its_bytes_as_the_packet_holds_it_is_taken(env):
    """8,192 bytes as the packet holds it is taken, one more is not."""
    text = chr(0x6587) * 2706 + "x"
    item = {"id": "0" * 32, "n": 1, "at": T0, "text": text}
    assert len(canonicalize(item).encode() if isinstance(canonicalize(item), str) else canonicalize(item)) == 8192
    assert controller.add_request({"id": "u1"}, text, now=T0)["n"] == 1
    with pytest.raises(agents.AgentError) as refused:
        controller.add_request({"id": "u2"}, text + "y", now=T0)
    assert refused.value.status == 400 and "past 8192" in str(refused.value)


def test_a_page_whose_more_is_not_true_ends_the_read(env, ws):
    """Only `more: true` reads on."""
    user = auth_user()
    setup_plain(user)
    ws.pages.append({"seq": 1, "more": 1, "actions": [], "tasks": []})
    controller.sync(user, "http://workspace.test")
    assert [p for p in ws.paths() if p.startswith("/controller/record")] == ["/controller/record?since=0"]
    with store.connect() as db:
        assert controller._row(db, user["id"])["read_at"] is not None


def test_a_session_whose_reset_is_now_is_in_now(env):
    """A reset that has come is waited for no more."""
    user = {"id": "u1"}
    setup(user)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": T0 + 300}], now=T0 + 30)
    row = next(r for r in controller.board_rows("u1", T0 + 300) if r["id"] == a["task"])
    assert (row["phase"], row["waits_until"]) == ("now", None)
    row = next(r for r in controller.board_rows("u1", T0 + 299) if r["id"] == a["task"])
    assert (row["phase"], row["waits_until"]) == ("next", T0 + 300)


@pytest.mark.parametrize("presses", [(True, False, True, False), (True, False, True)], ids=["released", "held"])
def test_holds_and_releases_in_one_second_end_as_the_last_one_pressed(env, presses):
    """The owner's last press in a second, by the order recorded, whichever it was (a
    lookup by time alone, which ties within a second, would pass the case that ends released)."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    for on in presses:
        controller.hold(user, rid, on, now=T0 + 200)
    a = start(rid, at=T0 + 200)
    take(user, a, now=T0 + 210)
    assert ("on hold" in (task_row(a["task"])["flag"] or "")) == presses[-1]


@pytest.mark.parametrize("part", ["instructions", "observed", "model"])
def test_no_part_of_the_packet_ends_past_its_budget(env, monkeypatch, part):
    """A later fill of a part could lengthen a count an earlier fill measured
    as 0, and end the part a byte past its budget. Every count now starts at its largest."""
    user = {"id": "u1"}
    setup(user, cap=10)
    planned(user)
    if part == "observed":
        take(user, *[start(None, session=None, repo="r" * 100, branch="b" * 200, at=T0 + 20) for _ in range(10)])
    elif part == "model":
        for i in range(12):
            asked = question("Which way? " + "q" * 200, at=T0 + 20 + i)
            take(user, asked, now=T0 + 20 + i)
            controller.decide(user, f"This way {i}.", asked["question"], now=T0 + 20 + i)
        for i in range(3):
            take(user, question("Still open? " + "o" * 1500, at=T0 + 40 + i), now=T0 + 40 + i)
    else:
        for i in range(3):
            controller.add_request(user, f"Request {i} " + "z" * 300, now=T0 + 6 + i)
        for i in range(10):
            controller.decide(user, f"Decision {i} " + "w" * 300, now=T0 + 20 + i)
    keys = {"observed": ("requirements", "more_requirements", "other_sessions", "more_other_sessions"),
            "instructions": ("by", "requests", "more_requests", "decisions", "more_decisions"),
            "model": ("open_questions", "dropped_questions", "answered_questions", "more_answered")}[part]
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])
    held = packet["instructions"] if part == "instructions" else packet["information"]
    if part == "instructions":
        first = {"by": "owner", "requests": held["requests"][:4], "more_requests": 0, "decisions": [],
                 "more_decisions": 0}
    elif part == "model":
        assert len(held["open_questions"]) == 3 and len(held["answered_questions"]) == 12
        first = {"open_questions": held["open_questions"], "dropped_questions": 0, "answered_questions": [],
                 "more_answered": 0}
    else:
        first = {"requirements": held["requirements"], "more_requirements": 0, "other_sessions": [],
                 "more_other_sessions": 0}
    exact = len(controller._canon(first))       # what the first fill measured, the later list's count at 0
    monkeypatch.setitem(controller.BUDGETS, part, exact)
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])
    held = packet["instructions"] if part == "instructions" else packet["information"]
    assert len(controller._canon({k: held[k] for k in keys})) <= exact


def test_the_instructions_never_end_past_their_budget_when_the_requests_fill_it_to_the_byte(env, monkeypatch):
    """The decisions' count starts at its largest, the number of decisions made, before the requests not yet planned
    are measured: started at 0, the requests could fill the instructions to the byte and the decisions' fill, giving
    the count its two digits back, end them a byte past their budget; every
    other test passes a count started at 0."""
    user = {"id": "u1"}
    setup(user, cap=5)
    for i in range(3):
        controller.add_request(user, f"Request {i} " + "z" * 300, now=T0 + 6 + i)
    for i in range(10):
        controller.decide(user, f"Decision {i} " + "w" * 300, now=T0 + 20 + i)
    keys = ("by", "requests", "more_requests", "decisions", "more_decisions")
    with store.connect() as db:
        requests = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["instructions"]["requests"]
    assert len(requests) == 3
    exact = len(controller._canon({"by": "owner", "requests": requests, "more_requests": 0, "decisions": [],
                                   "more_decisions": 0}))
    monkeypatch.setitem(controller.BUDGETS, "instructions", exact)
    with store.connect() as db:
        held = json.loads(controller.build_packet(db, "u1", T0 + 60)[0])["instructions"]
    assert len(controller._canon({k: held[k] for k in keys})) <= exact
    assert len(held["requests"]) == 2


def old_requests(user_id, n, now):
    """`n` requests, each with one requirement closed more than a week before `now`: months of an owner's work."""
    long_ago = now - controller.KEPT_CLOSED - 86400
    with store.connect() as db:
        base = db.execute("SELECT COALESCE(MAX(n), 0) FROM ade_controller_requests WHERE user_id = ?",
                          (user_id,)).fetchone()[0]
        for i in range(n):
            qid, rid = uid(), uid()
            db.execute("INSERT INTO ade_controller_requests (id, user_id, n, text, digest, planned_at, created_at) "
                       "VALUES (?,?,?,?,?,?,?)", (qid, user_id, base + i + 1, "Old.", "0" * 64, long_ago - 10,
                                                  long_ago - 20))
            db.execute("INSERT INTO ade_controller_requirements (id, user_id, request_id, title, words, repos, state, "
                       "planned_at, closed_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (rid, user_id, qid, "Old", "Old.", '["demo"]', "done", long_ago - 10, long_ago, long_ago,
                        long_ago))


@pytest.mark.parametrize("stats", [False, True], ids=["as kept", "analyzed"])
@pytest.mark.parametrize("what", ["view", "board", "packet", "mirror", "requests"])
def test_the_page_reads_read_nothing_of_requests_whose_requirements_closed_long_ago(env, what, stats):
    """What the view, the board, the packet and the mirror read grew with every
    request and requirement the owner ever had (80,000 to 504,000 more SQLite steps with 4,000 closed long ago). The
    requests are read through an index of those not yet planned, from the open requirements and from those closed in
    the last week; the requirements open by state and closed from the newest close back: the same work with thousands
    of them as with none, near enough, with or without SQLite's statistics."""
    now = T0 + 200
    costs = []
    for n in (0, 4000):
        user = auth_user()
        a_controller_at_work(user)
        old_requests(user["id"], n, now)
        if stats:
            with store.connect() as db:
                db.execute("ANALYZE")

        def mirror():
            with store.connect() as db:
                controller.mirror_body(db, user["id"], now)
        calls = {"view": lambda: controller.view(user, now=now),
                 "board": lambda: controller.board_rows(user["id"], now),
                 "packet": lambda: controller.packet_of(user),
                 "mirror": mirror,
                 "requests": lambda: controller.requests_of(user)}
        costs.append(work(calls[what]))
    assert costs[1] - costs[0] < 5, costs


# ---- the most open, a day's telling, the account taken away, and the questions' order -------------------------------

def test_closed_requirements_do_not_count_against_the_most_open(env, monkeypatch):
    """A count of every requirement, closed ones among them, against REQUIREMENTS_OPEN_MOST would refuse every
    plan once that many had ever been planned."""
    monkeypatch.setattr(controller, "REQUIREMENTS_OPEN_MOST", 2)
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0)
    first = [plan(request["id"], "Make the README say hello.") for _ in range(2)]
    assert take(user, *first)["taken"] == 2
    assert take(user, plan(request["id"], "Make the README say hello."))["refused"] == 1
    assert events("u1", "plan.refused")[-1]["payload"]["why"] == (
        "the Manager has the most open requirements the app keeps (2)")
    for p in first:
        controller.close(user, p["requirement"], "done", now=T0 + 200)
    assert take(user, plan(request["id"], "Make the README say hello."), now=T0 + 300)["taken"] == 1


def test_states_past_what_a_page_reads_are_told_once_a_day_not_once_an_hour(env, monkeypatch):
    """The line for states past what a page reads is told at most once a day; once an hour would pass every test
    that looked only within an hour and after a day."""
    monkeypatch.setattr(controller, "ACTIONS_A_PAGE", 1)
    user = {"id": "u1"}
    setup(user, cap=5)
    states = [{"id": uid(), "state": "working"} for _ in range(2)]
    for now, lines in ((T0 + 100, 1), (T0 + 100 + 2 * 3600, 1), (T0 + 100 + 23 * 3600, 1),
                       (T0 + 101 + controller.TOLD, 2)):
        take(user, tasks_=states, now=now)
        assert len(events("u1", "record.states_unread")) == lines, now


@pytest.mark.parametrize("before", ["on", "stopped"])
def test_taking_the_account_away_turns_the_controller_off_whether_on_or_stopped(env, before):
    """A stopped controller left stopped when its account is taken away runs on no account; it is off."""
    user = {"id": "u1"}
    setup(user)
    if before == "stopped":
        controller.set_state(user, "stopped", now=T0 + 10)
    c = controller.change_settings(user, {"account": None}, now=T0 + 20)["controller"]
    assert (c["state"], c["account"]) == ("off", None)
    assert events("u1", "controller.off")[-1]["payload"]["why"] == "no account"


def test_end_all_while_off_leaves_the_controller_off(env):
    """End all is Stop for a controller that is on or stopped; one that is off stays off, its count moving on."""
    user = {"id": "u1"}
    setup(user)
    controller.set_state(user, "off", now=T0 + 10)
    c = controller.end_all(user, now=T0 + 20)["controller"]
    assert (c["state"], c["end_all"]) == ("off", 1)
    assert events("u1", "controller.end_all")[-1]["payload"]["to"] == "off"


def test_questions_are_listed_newest_asked_first_in_the_view_and_the_mirror(env, monkeypatch):
    """The view and the mirror list the questions newest asked first, the mirror up to
    MIRROR_QUESTIONS_MOST (50): oldest first, or a cut at 5, passed every test."""
    user = {"id": "u1"}
    setup_plain(user)
    asked = []
    for i in range(6):
        q = question(f"Question {i}?", at=T0 + 30 + i)
        take(user, q, now=T0 + 40 + i)
        asked.append(q["question"])
        if i < 3:
            controller.decide(user, "Yes.", q["question"], now=T0 + 50 + i)
    newest_first = asked[::-1]
    assert [q["id"] for q in controller.view(user, now=T0 + 100)["questions"]] == newest_first
    with store.connect() as db:
        assert [q["id"] for q in controller.mirror_body(db, "u1", now=T0 + 100)[0]["questions"]] == newest_first
        monkeypatch.setattr(controller, "MIRROR_QUESTIONS_MOST", 4)
        assert [q["id"] for q in controller.mirror_body(db, "u1", now=T0 + 100)[0]["questions"]] == newest_first[:4]


@pytest.mark.parametrize("text", ["   ", "\t \n "])
def test_a_question_of_only_spaces_is_refused(env, text):
    """A question whose text is only whitespace has no words, and is refused."""
    user = {"id": "u1"}
    setup_plain(user)
    q = question(text)
    assert take(user, q)["refused"] == 1
    assert events("u1", "question.refused")[0]["payload"]["why"] == "it has no words"
    assert controller.view(user)["questions"] == []


def test_a_read_that_fails_after_a_page_said_the_mirror_is_held_still_sends_it(env, ws, monkeypatch):
    """What a page of a read that then failed says the workspace holds keeps nothing from being sent (a read that
    failed sends the mirror too); kept, it would leave a mirror unsent that the workspace may not hold."""
    user = auth_user()
    setup_plain(user)
    assert controller.sync(user, "http://workspace.test") is True
    real, reads = ws.call, []

    def call(endpoint, method, path, body=None, timeout=30):
        if path.startswith("/controller/record"):
            reads.append(path)
            if len(reads) == 1:
                return {"seq": 1, "more": True, "held": ws.held, "actions": []}
            raise workspace_client.WorkspaceCallError(502, "The workspace answered 500.")
        return real(endpoint, method, path, body, timeout)
    monkeypatch.setattr(workspace_client, "call", call)
    ws.calls.clear()
    assert controller.sync(user, "http://workspace.test") is True
    assert len(reads) == 2 and ws.paths() == ["/controller"]


# ---- closed requirements with open sessions, sessions open again, and what the view and the packet give -------------

def test_an_open_session_of_a_requirement_closed_more_than_a_week_ago_is_shown_with_it(env):
    """Closing a requirement does not stop its sessions (close), and an open
    session holds a place under the cap whenever its requirement closed (_open_at), but the view, the packet and the
    mirror showed only the requirements open or closed in the last week: a week after its close the session was shown
    nowhere, though the start it pushed past the cap was flagged, and a fresh controller was not told it still ran. A
    requirement closed with a session still open is shown, with that session, until the session ends; then the week
    bounds it again."""
    user = {"id": "u1"}
    setup(user, cap=1)
    request, rid = planned(user)
    a = start(rid, at=T0 + 20)
    listed = [{"id": a["task"], "state": "idle"}]
    take(user, a, tasks_=listed, now=T0 + 100)
    controller.close(user, rid, "done", now=T0 + 200)
    later = T0 + 200 + 8 * 86400
    rid2 = uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=rid2, at=later - 60), tasks_=listed,
         now=later - 50)
    b = start(rid2, session=OTHER, at=later - 30)
    take(user, b, tasks_=listed + [{"id": b["task"], "state": "working"}], now=later)
    assert task_row(a["task"])["state"] == "idle"
    assert task_row(b["task"])["flag"] == "one more than the 1 sessions the Manager may have open at once"
    view = controller.view(user, now=later)
    assert view["controller"]["open_sessions"] == 2
    assert [(r["id"], r["state"], [t["id"] for t in r["tasks"]]) for r in view["requirements"]] == [
        (rid2, "open", [b["task"]]), (rid, "done", [a["task"]])]
    rows = {r["id"]: r for r in controller.board_rows("u1", later)}
    assert (rows[rid]["kind"], rows[rid]["phase"]) == ("requirement", "history")
    assert (rows[a["task"]]["parent"], rows[a["task"]]["phase"]) == (rid, "now")
    with store.connect() as db:
        data, _ = controller.build_packet(db, "u1", later)
        body, _, _ = controller.mirror_body(db, "u1", later)
    assert [(r["id"], [t["id"] for t in r["tasks"]]) for r in json.loads(data)["information"]["requirements"]] == [
        (rid2, [b["task"]]), (rid, [a["task"]])]
    assert [(r["id"], r["state"]) for r in body["requirements"]] == [(rid2, "open"), (rid, "done")]
    # once its session ends, the requirement closed more than a week ago is shown nowhere again
    take(user, tasks_=[{"id": a["task"], "state": "done", "ended_at": later + 10},
                       {"id": b["task"], "state": "working"}], now=later + 20)
    view = controller.view(user, now=later + 30)
    assert [r["id"] for r in view["requirements"]] == [rid2]
    assert rid not in {r["id"] for r in controller.board_rows("u1", later + 30)}
    with store.connect() as db:
        data, _ = controller.build_packet(db, "u1", later + 30)
        body, _, _ = controller.mirror_body(db, "u1", later + 30)
    assert [r["id"] for r in json.loads(data)["information"]["requirements"]] == [rid2]
    assert [r["id"] for r in body["requirements"]] == [rid2]


def test_an_open_session_of_a_requirement_closed_before_the_newest_twenty_of_the_week_is_on_the_board(env):
    """Within the week too, the board showed the LISTED requirements closed most
    lately, so the open session of one closed before them was on no row of the board. Its requirement is shown, and
    the session under it."""
    user = {"id": "u1"}
    setup(user, cap=10)
    request = controller.add_request(user, REQUEST, now=T0 + 5)
    rids = [uid() for _ in range(controller.LISTED + 1)]
    take(user, *[plan(request["id"], "Make the README say hello.", rid=r, at=T0 + 10 + i) for i, r in enumerate(rids)],
         now=T0 + 50)
    a, b = start(rids[0], at=T0 + 60), start(rids[-1], session=OTHER, at=T0 + 61)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "idle"}, {"id": b["task"], "state": "idle"}], now=T0 + 70)
    for i, r in enumerate(rids):
        controller.close(user, r, "done", now=T0 + 100 + i)
    rows = controller.board_rows("u1", T0 + 200)
    # each once: the one closed last among the newest twenty, with its session open, as well
    assert [r["id"] for r in rows if r["kind"] == "requirement"] == rids[::-1]
    assert sorted((r["id"], r["parent"], r["phase"]) for r in rows if r["kind"] == "session") == sorted(
        [(a["task"], rids[0], "now"), (b["task"], rids[-1], "now")])


def test_the_packet_gives_the_closed_requirements_with_a_session_still_open_before_the_other_closed_ones(env):
    """The packet gives the open requirements, then the closed ones with a session still open,
    then the other closed ones, each newest first: a fresh controller is told of a session it started that still runs
    before what is left out when the budget cuts. Before, the closed ones came newest closed first, so one closed long
    ago with a session still open was the first left out."""
    user = {"id": "u1"}
    setup(user, cap=5)
    request = controller.add_request(user, REQUEST, now=T0 + 5)
    live, new, old, still = uid(), uid(), uid(), uid()
    take(user, *[plan(request["id"], "Make the README say hello.", rid=r, at=T0 + 10 + i)
                 for i, r in enumerate((live, new, old, still))], now=T0 + 20)
    a = start(live, at=T0 + 30)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 40)
    controller.close(user, live, "done", now=T0 + 50)
    controller.close(user, old, "done", now=T0 + 60)        # planned after `new`, closed before it
    controller.close(user, new, "done", now=T0 + 70)
    with store.connect() as db:
        data, _ = controller.build_packet(db, "u1", T0 + 100)
    # the other closed ones newest closed first, whenever planned, not in the order planned
    assert [r["id"] for r in json.loads(data)["information"]["requirements"]] == [still, live, new, old]


def test_a_session_the_app_ended_for_no_longer_being_listed_is_open_again_when_a_later_page_lists_it_open(env):
    """An end the app inferred from one page that did not list a session
    (_unlisted) was as final as the workspace's own: a later page listing the session working left it failed, out of
    the cap's count while it ran, in History, and given to a fresh controller as failed. The app's inference gives
    way to the workspace's word: the session is open again, its inferred end gone, journaled once, and the version
    moves on a page that holds nothing else; a start made while it runs counts it against the cap."""
    user = {"id": "u1"}
    setup(user, cap=1)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 100)
    take(user, tasks_=[], now=T0 + 200)
    assert (task_row(a["task"])["state"], task_row(a["task"])["unlisted"]) == ("failed", 1)
    with store.connect() as db:
        version = controller._row(db, "u1")["version"]
    got = take(user, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": T0 + 900}], now=T0 + 300)
    assert got["states"] == 1
    t = task_row(a["task"])
    assert (t["state"], t["waits_until"], t["ended_at"], t["unlisted"]) == ("waiting", T0 + 900, None, 0)
    with store.connect() as db:
        assert controller._row(db, "u1")["version"] == version + 1
    assert [(e["task_id"], e["at"], e["payload"]) for e in events("u1", "task.reopened")] == [
        (a["task"], T0 + 300, {"task": a["task"], "session": SESSION, "state": "waiting"})]
    b = start(rid, session=OTHER, at=T0 + 350)
    take(user, b, tasks_=[{"id": a["task"], "state": "working"}, {"id": b["task"], "state": "working"}], now=T0 + 400)
    assert task_row(b["task"])["flag"] == "one more than the 1 sessions the Manager may have open at once"
    assert controller.view(user, now=T0 + 500)["controller"]["open_sessions"] == 2
    assert {r["id"]: r for r in controller.board_rows("u1", T0 + 500)}[a["task"]]["phase"] == "now"
    with store.connect() as db:
        data, _ = controller.build_packet(db, "u1", T0 + 500)
    given = {t["id"]: t["state"] for r in json.loads(data)["information"]["requirements"] for t in r["tasks"]}
    assert given[a["task"]] == "working"
    # left out again, it is ended again from when a page last listed it, and may come back again
    take(user, tasks_=[{"id": b["task"], "state": "working"}], now=T0 + 600)
    t = task_row(a["task"])
    assert (t["state"], t["ended_at"], t["unlisted"]) == ("failed", T0 + 400, 1)
    assert [e["payload"].get("unlisted") for e in events("u1", "task.ended")] == [True, True]


@pytest.mark.parametrize("state,ended_at", [("done", T0 + 150), ("failed", T0 + 100)],
                         ids=["another end", "the end the app gave"])
def test_a_session_the_app_ended_for_no_longer_being_listed_takes_the_end_the_workspace_gives_it_later(env, state,
                                                                                                     ended_at):
    """The workspace's own end of a session the app had ended for no longer listing it is
    taken, its state and its time, and journaled as its end, where before its state alone was taken, under the app's
    time, and nothing said; so too when it is the end the app gave. After it, as after any end of the workspace's, the
    session does not come back."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 100)
    take(user, tasks_=[], now=T0 + 200)
    assert take(user, tasks_=[{"id": a["task"], "state": state, "ended_at": ended_at}], now=T0 + 300)["states"] == 1
    t = task_row(a["task"])
    assert (t["state"], t["ended_at"], t["unlisted"]) == (state, ended_at, 0)
    assert [(e["at"], e["payload"].get("unlisted"), e["payload"]["state"]) for e in events("u1", "task.ended")] == [
        (T0 + 100, True, "failed"), (ended_at, None, state)]
    take(user, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 400)
    take(user, tasks_=[{"id": a["task"], "state": "stopped", "ended_at": T0 + 450}], now=T0 + 500)
    t = task_row(a["task"])
    assert (t["state"], t["ended_at"]) == ("stopped", ended_at)
    assert events("u1", "task.reopened") == [] and len(events("u1", "task.ended")) == 2


def test_a_session_open_again_past_the_day_s_lines_is_opened_and_not_journaled(env, monkeypatch):
    """Past the lines the record may add today, the end the app infers and the return are each taken, not journaled:
    the end is marked as the app's whether or not it was journaled."""
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", 1)
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)                  # the plan: the day's one line
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 100)
    take(user, tasks_=[], now=T0 + 200)
    assert task_row(a["task"])["state"] == "failed" and events("u1", "task.ended") == []
    take(user, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 300)
    assert task_row(a["task"])["state"] == "working" and events("u1", "task.reopened") == []


def test_a_session_s_return_spends_one_of_the_day_s_lines(env, monkeypatch):
    """task.reopened is a line the record adds (RECORD_KINDS), counted against the day's lines as an end is."""
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", 3)
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)                  # the plan: the first line
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 100)
    take(user, tasks_=[], now=T0 + 200)     # the end the app infers: the second
    take(user, tasks_=[{"id": a["task"], "state": "working"}], now=T0 + 300)       # its return: the third
    take(user, tasks_=[], now=T0 + 400)     # ended again: past them
    assert task_row(a["task"])["state"] == "failed"
    assert (len(events("u1", "task.ended")), len(events("u1", "task.reopened"))) == (1, 1)


def test_a_hold_a_release_and_a_close_each_reach_a_workspace_that_holds_the_mirror(env, ws):
    """A hold and a close that moved no version would pass any test that did not read the
    version after them, and a workspace holding the current version is sent nothing (_sync). Each moves it, and the
    next sync sends the requirement as the owner left it."""
    user = auth_user()
    setup_plain(user)
    _, rid = planned(user)
    assert controller.sync(user, "http://workspace.test") is True
    for change, want in ((lambda: controller.hold(user, rid, True), (True, "open")),
                         (lambda: controller.hold(user, rid, False), (False, "open")),
                         (lambda: controller.close(user, rid, "done"), (False, "done"))):
        ws.mirrors.clear()
        change()
        assert controller.sync(user, "http://workspace.test") is True
        assert len(ws.mirrors) == 1
        assert [(r["hold"], r["state"]) for r in ws.mirrors[0]["requirements"] if r["id"] == rid] == [want]


def test_a_page_of_exactly_as_many_states_as_the_app_reads_ends_a_session_it_does_not_list(env, monkeypatch):
    """A page whose sessions' states are exactly as many as
    the app reads holds them all, so it says which are open."""
    monkeypatch.setattr(controller, "ACTIONS_A_PAGE", 2)
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid, at=T0 + 20), start(rid, session=OTHER, at=T0 + 21)
    c = start(rid, session="cccccccccccc", at=T0 + 22)
    take(user, a, b, now=T0 + 100)
    take(user, c, now=T0 + 110)
    take(user, tasks_=[{"id": b["task"], "state": "working"}, {"id": c["task"], "state": "working"}], now=T0 + 200)
    assert [task_row(x["task"])["state"] for x in (a, b, c)] == ["failed", "working", "working"]


def test_a_session_waiting_for_a_reset_that_a_page_no_longer_lists_keeps_no_reset(env):
    """An ended session waits for no reset, in the store, the view and the packet, the end the app infers too (the
    other tests of that end give only sessions with none)."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": T0 + 900}], now=T0 + 100)
    assert task_row(a["task"])["waits_until"] == T0 + 900
    take(user, tasks_=[], now=T0 + 200)
    assert (task_row(a["task"])["state"], task_row(a["task"])["waits_until"]) == ("failed", None)
    assert controller.view(user, now=T0 + 300)["requirements"][0]["tasks"][0]["waits_until"] is None
    with store.connect() as db:
        data, _ = controller.build_packet(db, "u1", T0 + 300)
    assert json.loads(data)["information"]["requirements"][0]["tasks"][0]["waits_until"] is None


def test_a_session_no_page_listed_before_is_ended_from_its_start(env):
    """With no page before whose states the app read in full, the app last knew the session open at its start, so
    its end is dated from then, not now."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a = start(rid, at=T0 + 20)
    take(user, a, now=T0 + 100)             # says nothing of the states: none such page before
    take(user, tasks_=[], now=T0 + 200)
    assert (task_row(a["task"])["state"], task_row(a["task"])["ended_at"]) == ("failed", T0 + 20)


def test_a_request_keeps_the_time_of_its_last_go(env):
    """The routes record gives a request's
    go_at as the last go on it."""
    user = {"id": "u1"}
    setup(user, cap=5, confirm_plans=True)
    request, rid = planned(user)
    assert controller.requests_of(user)["requests"][0]["go_at"] is None
    controller.go(user, request["id"], now=T0 + 50)
    take(user, plan(request["id"], "Make the README say hello.", at=T0 + 60), now=T0 + 70)
    controller.go(user, request["id"], now=T0 + 80)
    assert [r["go_at"] for r in controller.requests_of(user)["requests"]] == [T0 + 80]
    assert [r["go_at"] for r in controller.view(user, now=T0 + 90)["requests"]] == [T0 + 80]


def test_the_view_gives_the_newest_twenty_decisions_newest_first(env):
    user = {"id": "u1"}
    setup_plain(user)
    for i in range(controller.LISTED + 1):
        controller.decide(user, f"Decision {i}.", now=T0 + 1 + i)
    assert [d["n"] for d in controller.view(user, now=T0 + 100)["decisions"]] == list(
        range(controller.LISTED + 1, 1, -1))


def test_a_request_lists_the_requirements_planned_from_it_oldest_first(env):
    """The routes record gives a request's requirements as
    planned from it, oldest first. The ids are chosen so that their own order is the other one."""
    user = {"id": "u1"}
    setup_plain(user)
    request = controller.add_request(user, REQUEST, now=T0 + 5)
    first, second = "f" * 32, "1" * 32
    take(user, plan(request["id"], "Make the README say hello.", rid=first, at=T0 + 10), now=T0 + 30)
    take(user, plan(request["id"], "Make the README say hello.", rid=second, at=T0 + 20), now=T0 + 40)
    assert controller.requests_of(user)["requests"][0]["requirements"] == [first, second]
    assert controller.view(user, now=T0 + 50)["requests"][0]["requirements"] == [first, second]


def owner_texts(value, path=""):
    """Where a packet's part holds a text marked as the owner's: the paths, `[]` for any item of a list."""
    if isinstance(value, dict):
        out = [path] if value.get("by") == "owner" and "text" in value else []
        return out + [p for k, v in value.items() for p in owner_texts(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for v in value for p in owner_texts(v, path + "[]")]
    return []


def test_the_packet_s_header_names_the_owner_s_words_its_information_holds(env):
    """The header said everything under information is never an instruction, while a
    requirement's words and acceptance there are the owner's words, marked as the owner's, which belong
    among the instructions. The header names them as the owner's, and they are the only texts of the owner's there."""
    user = {"id": "u1"}
    setup(user, cap=5)
    request = controller.add_request(user, REQUEST, now=T0 + 5)
    take(user, plan(request["id"], "Make the README say hello.", acceptance="its first line is hello."), now=T0 + 20)
    with store.connect() as db:
        data, _ = controller.build_packet(db, "u1", T0 + 100)
    packet = json.loads(data)
    assert "but for a requirement's words and acceptance, marked as the owner's" in packet["header"]
    assert sorted(set(owner_texts(packet["information"]))) == [".requirements[].acceptance", ".requirements[].words"]


def test_a_session_naming_another_owner_s_requirement_shows_nothing_of_theirs(env):
    """A requirement with a session still open is read by its id among the owner's own: a
    session of this owner's that names another owner's requirement, closed or open, shows nothing of it in the view,
    the board, the packet or the mirror."""
    user, them = {"id": "u1"}, {"id": "u2"}
    setup_plain(user)
    setup_plain(them)
    _, closed = planned(them)
    _, still = planned(them)
    controller.close(them, closed, "done", now=T0 + 40)
    a, b = start(closed, at=T0 + 50), start(still, session=OTHER, at=T0 + 51)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "working"}, {"id": b["task"], "state": "working"}],
         now=T0 + 100)
    later = T0 + 9 * 86400
    view = controller.view(user, now=later)
    assert view["requirements"] == [] and {t["id"] for t in view["other_sessions"]} == {a["task"], b["task"]}
    assert [r["id"] for r in controller.board_rows("u1", later) if r["kind"] == "requirement"] == []
    with store.connect() as db:
        data, _ = controller.build_packet(db, "u1", later)
        body, _, _ = controller.mirror_body(db, "u1", later)
    assert json.loads(data)["information"]["requirements"] == [] and body["requirements"] == []


def test_a_session_for_no_requirement_beside_one_of_a_requirement_closed_long_ago_shows_both(env):
    """The sessions with no requirement are read apart from those with one: read among them, the ids of the requirements
    with a session still open would hold none beside an id, which cannot be put in order, and the view, the board, the
    packet and the mirror would all fail. Each shows the closed requirement with its session, and the session started
    for no requirement among the other sessions."""
    user = {"id": "u1"}
    setup(user, cap=5)
    _, rid = planned(user)
    a, stray = start(rid, at=T0 + 20), start(None, session=OTHER, at=T0 + 21)
    listed = [{"id": a["task"], "state": "idle"}, {"id": stray["task"], "state": "idle"}]
    take(user, a, stray, tasks_=listed, now=T0 + 100)
    controller.close(user, rid, "done", now=T0 + 200)
    later = T0 + 9 * 86400
    take(user, tasks_=listed, now=later - 10)
    view = controller.view(user, now=later)
    assert [r["id"] for r in view["requirements"]] == [rid]
    assert [t["id"] for t in view["other_sessions"]] == [stray["task"]]
    rows = {r["id"]: r for r in controller.board_rows("u1", later)}
    assert (rows[a["task"]]["parent"], rows[stray["task"]]["parent"]) == (rid, None)
    with store.connect() as db:
        data, _ = controller.build_packet(db, "u1", later)
        body, _, _ = controller.mirror_body(db, "u1", later)
    info = json.loads(data)["information"]
    assert [r["id"] for r in info["requirements"]] == [rid]
    assert [t["id"] for t in info["other_sessions"]] == [stray["task"]]
    assert [r["id"] for r in body["requirements"]] == [rid]
