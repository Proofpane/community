"""The owner's controller, the workspace's side (proofpane_workspace/controller.py): the record the app reads a page at
a time and the owner's side the app
sends, the six tools and what each refuses, a start's command and its first
prompt in three marked parts, the pacing, the wait at an account's limit and the continue, and the settings and hooks a
controller's session is given. The workspace's own pieces, each start's command kept and nothing run (a stand-in
terminal), with a stand-in claude that answers only --version and --help; the chat and the app's sync against a real
workspace agent are in test_controller_crossing.py."""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import time

import pytest

from proofpane_open.ade import controller as app_controller, link, link_commands, tasks as app_tasks
from proofpane_workspace import activity, controller as ctl, settings as settings_made, statusline
from proofpane_workspace import task_shapes as shapes
from proofpane_workspace import workspace as wsmod
from proofpane_workspace.workspace import Workspace
from test_workspace_ask_effort import OFFERED, _no_handshake, _refreshed, _says_claude
from workspace_fixtures import make_repo

NOW = int(time.time())
REQUEST = "Make the README say hello.\n\nIts first line is hello.\n\nKeep the rest as it is."
SECRET_SHAPED = "ghp_" + "a" * 36           # a GitHub token's shape (task_runs.SECRET_SHAPES)


def uid():
    return secrets.token_hex(16)


class _Term:
    """terminals.Terminal's place in these tests: each start's command kept, its program running until stopped."""
    seen = []

    def __init__(self, argv, cwd, env, cols=100, rows=30, on_activity=None, save=None):
        _Term.seen.append(self)
        self.argv, self.cwd, self.env = list(argv), cwd, dict(env)
        self.running, self.exit_code, self.ended_at, self.backlog = True, None, 0, bytearray()
        self.started_at = time.time()

    async def stop(self):
        self.running = False


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace with two repositories, a stand-in claude that says its version (2.1.274) and its help, and a
    stand-in terminal; its own claude offers OFFERED."""
    bin_dir = tmp_path / "wsbin"
    bin_dir.mkdir()
    says = str(tmp_path / "says")
    (bin_dir / "claude").write_text(_says_claude(says))
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "wshome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(wsmod.terminals, "Terminal", _Term)
    monkeypatch.setattr(wsmod.claude_cli, "handshake", _no_handshake)
    _Term.seen = []
    w = Workspace(str(tmp_path / "ws"))
    for name in ("demo", "other"):
        make_repo(os.path.join(w.repos_dir, name))
    w.capabilities = lambda: dict(OFFERED)
    yield w
    _refreshed()


def request(text=REQUEST, n=1, at=NOW - 600, checks=None):
    return {"id": uid(), "n": n, "text": text, "digest": hashlib.sha256(text.encode("utf-8")).hexdigest(), "at": at,
            "checks": checks}


def mirror(**over):
    m = {"version": 1, "state": "on", "state_at": NOW - 900, "account": "default", "cap": 2,
         "models": ["opus", "sonnet"], "efforts": ["max", "high"], "chat": {"model": "opus", "effort": "xhigh"},
         "confirm_plans": False, "end_all": 0, "requests": [request()], "more_requests": 0, "decisions": [],
         "more_decisions": 0, "requirements": [], "questions": [], "packet_b64": None, "packet_digest": None,
         "packet_error": None}
    m.update(over)
    return m


def packet(**extra):
    data = json.dumps(dict({"schema": ctl.PACKET_SCHEMA, "instructions": {"by": "owner", "requests": [],
                                                                         "decisions": []}}, **extra)).encode()
    return base64.b64encode(data).decode(), shapes.tagged_digest(ctl.PACKET_TAG, data)


def take(w, m):
    return asyncio.run(w.controller.take(m))


def on(w, **over):
    """The owner's side taken, the controller on: its mirror."""
    m = mirror(**over)
    take(w, m)
    return m


def plan(w, m, **over):
    args = dict({"request": m["requests"][0]["id"], "title": "Hello README", "words": "Make the README say hello.",
                 "acceptance": "Its first line is hello.", "repos": ["demo"]}, **over)
    return w.controller.plan_requirement(args)["requirement"]


def start(w, req, **over):
    return asyncio.run(w.controller.start_task(dict({"requirement": req, "repo": "demo",
                                                      "task": "Edit README.md so its first line is hello."}, **over)))


def page(w, since=0):
    return w.controller.page(since)


def read_through(w):
    """The app's reads as its rounds make them: the record read to its end, then, at its next round, asked since what
    it took, which is what the workspace counts as taken."""
    return page(w, page(w)["seq"])


def actions(w, kind=None):
    return [a for a in page(w)["actions"] if kind is None or a["kind"] == kind]


def refused(fn, *args, **kwargs):
    with pytest.raises(ctl.ControllerError) as e:
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    return str(e.value)


# ---- the record and its pages ----------------------------------------------------------------------------------------

def test_a_fresh_record_answers_the_contract_s_empty_page(ws):
    # with a requirement's own manager's fields: no attempt, the
    # room the loop measured (no window before it has), and no managers' version held
    assert page(ws) == {"seq": 0, "more": False, "held": None, "session": None, "tasks": [], "actions": [],
                        "attempts": [], "room": {"window": None, "until": None, "capped_since": None},
                        "managers_held": None}


def test_each_action_comes_once_in_the_order_taken_and_a_page_holds_at_most_200(ws):
    with ws.controller._lock:
        made = [ws.controller._act("question", question=uid(), requirement=None, text=f"q{i}") for i in range(250)]
    first = page(ws)
    assert (len(first["actions"]), first["more"], first["seq"]) == (200, True, made[199]["seq"])
    assert [a["id"] for a in first["actions"]] == [a["id"] for a in made[:200]]
    assert all("seq" not in a for a in first["actions"])
    second = page(ws, first["seq"])
    assert [a["id"] for a in second["actions"]] == [a["id"] for a in made[200:]]
    assert (second["more"], second["seq"]) == (False, made[-1]["seq"])
    assert page(ws, second["seq"])["actions"] == []


def test_a_page_ends_before_its_bytes_pass_its_bound(ws, monkeypatch):
    monkeypatch.setattr(ctl, "PAGE_BYTES", 2000)
    with ws.controller._lock:
        made = [ws.controller._act("question", question=uid(), requirement=None, text="x" * 600) for _ in range(5)]
    got = page(ws)
    assert 1 <= len(got["actions"]) < 5 and got["more"] is True
    assert got["seq"] == made[len(got["actions"]) - 1]["seq"]
    assert len(json.dumps(got["actions"])) <= 2000 + 700


def _task(w, state, **over):
    """A task in the record, as start_task leaves one: its session made as a workspace session is."""
    with w.controller._lock:
        t = dict({"id": uid(), "session": secrets.token_hex(6), "requirement": uid(), "repo": "demo", "base": None,
                  "branch": None, "model": "opus", "effort": "max", "account": "default", "state": state,
                  "waits_until": None, "ended_at": NOW if state in ctl.TASK_ENDED else None, "started_at": NOW - 60,
                  "seq": w.controller._next()}, **over)
        w.controller.record["tasks"][t["id"]] = t
    os.makedirs(w._dir(t["session"]), exist_ok=True)
    w._write({"id": t["session"], "repo": "demo", "program": "claude", "task": "t", "base": None,
              "branch": f"pp/{t['session']}", "created_at": NOW - 60, "finished": None})
    return t


def test_open_sessions_come_on_every_page_and_ended_ones_once_after_their_change(ws):
    running = _task(ws, "waiting", waits_until=NOW + 3600)
    ended = _task(ws, "done")
    since = ended["seq"]
    later = _task(ws, "stopped")
    got = page(ws, since)
    assert [t["id"] for t in got["tasks"]] == [running["id"], later["id"]]
    assert got["tasks"][0] == {"id": running["id"], "state": "waiting", "waits_until": NOW + 3600, "ended_at": None,
                               "checks": None, "why": None, "checking": False, "needs_owner": False,
                               "changed": running["seq"], "stopped_by": None}
    assert ended["id"] in [t["id"] for t in page(ws, 0)["tasks"]]


def test_states_past_a_page_are_cut_and_the_page_s_seq_is_the_last_given(ws):
    opened = [_task(ws, "waiting", waits_until=NOW + 60) for _ in range(3)]
    ended = [_task(ws, "done") for _ in range(ctl.PAGE)]
    got = page(ws)
    listed = [t["id"] for t in got["tasks"]]
    assert listed[:3] == [t["id"] for t in opened] and len(listed) == ctl.PAGE
    assert got["more"] is True and got["seq"] == ended[ctl.PAGE - 4]["seq"]
    rest = page(ws, got["seq"])
    assert [t["id"] for t in rest["tasks"]] == [t["id"] for t in opened + ended[ctl.PAGE - 3:]]
    assert rest["more"] is False


def test_when_both_are_cut_the_page_s_seq_is_the_smaller(ws, monkeypatch):
    monkeypatch.setattr(ctl, "PAGE", 2)
    with ws.controller._lock:
        early = [ws.controller._act("question", question=uid(), requirement=None, text="q") for _ in range(3)]
    ended = [_task(ws, "done") for _ in range(3)]
    got = page(ws)
    assert got["more"] is True and got["seq"] == min(early[1]["seq"], ended[1]["seq"])


def test_a_page_asked_past_the_record_says_the_record_s_lower_change_number(ws):
    with ws.controller._lock:
        ws.controller._act("question", question=uid(), requirement=None, text="q")
    seq = ws.controller.record["seq"]
    got = page(ws, seq + 999)
    assert got["seq"] == seq and got["actions"] == []


def test_the_record_lasts_a_restart_of_the_workspace(ws):
    on(ws)
    with ws.controller._lock:
        ws.controller._act("question", question=uid(), requirement=None, text="q")
        ws.controller._save()
    again = ctl.Controller(ws)
    assert again.page(0)["actions"] == page(ws)["actions"]
    # the owner's side it held is let go: the controller acts on none until the app sends one again, which a page
    # saying none is held has it do at its next round
    assert again.record["mirror"] is None and again.page(0)["held"] is None


@pytest.mark.parametrize("bad", ['{"seq": "1"}', "[]", "not json", '{"seq": 1, "actions": [{"id": 1}]}'])
def test_a_record_not_the_workspace_s_is_replaced_by_a_fresh_one_and_said(ws, bad, capsys):
    os.makedirs(ws.controller.dir, exist_ok=True)
    with open(ws.controller.path, "w") as fh:
        fh.write(bad)
    again = ctl.Controller(ws)
    assert again.page(0) == {"seq": 0, "more": False, "held": None, "session": None, "tasks": [], "actions": [],
                             "attempts": [], "room": {"window": None, "until": None, "capped_since": None},
                             "managers_held": None}
    assert "a fresh one" in capsys.readouterr().err


def test_a_record_that_is_a_link_is_not_followed(ws, tmp_path):
    os.makedirs(ws.controller.dir, exist_ok=True)
    elsewhere = tmp_path / "elsewhere.json"
    record = ctl._fresh() | {"seq": 7}
    elsewhere.write_text(json.dumps({"record": record, "mac": ws.controller._mac(record)}))
    os.remove(ws.controller.path)
    os.symlink(elsewhere, ws.controller.path)
    assert ctl.Controller(ws).record["seq"] == 0


def test_a_record_another_program_changed_is_not_taken(ws, capsys):
    on(ws)
    with open(ws.controller.path) as fh:
        signed = json.load(fh)
    assert ctl.Controller(ws).record["seq"] == ws.controller.record["seq"]      # the workspace's own, signed
    planted = _task(ws, "working")["session"]                 # one of the owner's sessions, which End all would stop
    signed["record"]["tasks"]["f" * 32] = dict(ws.controller.record["tasks"].popitem()[1], session=planted)
    with open(ws.controller.path, "w") as fh:
        json.dump(signed, fh)
    again = ctl.Controller(ws)
    assert again.record["tasks"] == {} and again.record["seq"] == 0
    assert "not one the workspace wrote" in capsys.readouterr().err


@pytest.mark.parametrize("mac", ["absent", None, 0, ["x"]])
def test_a_record_with_no_signature_or_one_not_a_string_is_not_taken(ws, mac, capsys):
    # a session's programs cannot make the signature, but they can leave it out: a record without one, or with one that
    # is not a string, is not one the workspace wrote, never read as one written before records were signed. The record
    # is the workspace's own, sound and holding a session End all would stop, so the signature alone decides
    on(ws)
    _task(ws, "working")
    ws.controller._save()
    with open(ws.controller.path) as fh:
        signed = json.load(fh)
    assert set(ctl.Controller(ws).record["tasks"]) == set(ws.controller.record["tasks"])    # taken while signed
    if mac == "absent":
        signed.pop("mac")
    else:
        signed["mac"] = mac
    with open(ws.controller.path, "w") as fh:
        json.dump(signed, fh)
    again = ctl.Controller(ws)
    assert again.record["tasks"] == {} and again.record["seq"] == 0
    assert "not one the workspace wrote" in capsys.readouterr().err


@pytest.mark.parametrize("hole", ["plan", "task", "question", "action"])
def test_a_signed_record_missing_a_field_the_controller_reads_is_not_taken(ws, hole, capsys):
    m = on(ws)
    plan(ws, m)
    start(ws, next(iter(ws.controller.record["plans"])))
    ws.controller.ask_owner({"text": "Which?"})
    record = json.loads(json.dumps(ws.controller.record))
    item = {"plan": lambda r: next(iter(r["plans"].values())), "task": lambda r: next(iter(r["tasks"].values())),
            "question": lambda r: next(iter(r["questions"].values())),
            "action": lambda r: r["actions"][0]}[hole](record)
    item.pop({"plan": "request", "task": "requirement", "question": "seq", "action": "kind"}[hole])
    with open(ws.controller.path, "w") as fh:
        json.dump({"record": record, "mac": ws.controller._mac(record)}, fh)        # signed with the right key
    assert ctl.Controller(ws).record["seq"] == 0
    assert "not one the workspace wrote" in capsys.readouterr().err


def test_a_restart_ends_each_session_but_one_waiting_for_its_reset(ws):
    working = _task(ws, "working")
    waiting = _task(ws, "waiting", waits_until=NOW + 600)
    with ws.controller._lock:
        ws.controller._save()
    again = ctl.Controller(ws)
    assert again.record["tasks"][working["id"]]["state"] == "stopped"
    assert again.record["tasks"][waiting["id"]]["state"] == "waiting"
    # each one's model and effort kept for its later starts, the ended one's too, which the owner may start again
    for t in (working, waiting):
        assert ws._models[t["session"]] == "opus" and ws._efforts[t["session"]] == "max"


# ---- the owner's side (PUT /controller) ------------------------------------------------------------------------------

def _broken(**over):
    m = mirror()
    m.update(over)
    return m


@pytest.mark.parametrize("m,field", [
    (_broken(version="1"), "version"),
    (_broken(version=True), "version"),
    (_broken(state="paused"), "state"),
    (_broken(account="Default"), "account"),
    (_broken(account="0123456789AB"), "account"),
    (_broken(cap=0), "cap"),
    (_broken(cap=12), "cap"),
    (_broken(models=[]), "models"),
    (_broken(models=["opus", "a b"]), "models"),
    (_broken(efforts="max"), "efforts"),
    (_broken(chat={"model": "opus"}), "chat.effort"),
    (_broken(confirm_plans=1), "confirm_plans"),
    (_broken(end_all=-1), "end_all"),
    (_broken(requests=[dict(request(), digest="0" * 64)]), "requests[0].digest"),
    (_broken(requests=[dict(request(), id="X" * 32)]), "requests[0].id"),
    (_broken(requests=[dict(request(), checks=[])]), "requests[0].checks"),
    (_broken(requests=[dict(request(), checks={"commands": [], "protected": []})]), "requests[0].checks"),
    (_broken(requests=[dict(request(), checks={"commands": [{"id": 2, "repo": "demo", "name": "n", "command": "c"}],
                                               "protected": []})]), "requests[0].checks"),
    (_broken(requests=[dict(request(), checks={"commands": [{"id": 1, "repo": "../x", "name": "n", "command": "c"}],
                                               "protected": []})]), "requests[0].checks"),
    (_broken(requests=[dict(request(), checks={"commands": [{"id": 1, "repo": "demo", "name": "n", "command": "c"}],
                                               "protected": [{"repo": "other", "paths": ["a"]}]})]),
     "requests[0].checks"),
    (_broken(decisions=[{"id": uid(), "n": 1, "question": "q", "text": "t", "at": 1}]), "decisions[0].question"),
    (_broken(requirements=["x"]), "requirements[0]"),
    (_broken(requirements=[{"id": uid(), "state": "open", "hold": 0, "waits_for_go": False}]), "requirements[0].hold"),
    (_broken(questions=[{"id": uid(), "state": "closed", "decision": None}]), "questions[0].state"),
    (_broken(packet_b64="not base64!"), "packet_b64"),
    (_broken(packet_b64=packet()[0], packet_digest="sha256:" + "0" * 64), "packet_digest"),
    (_broken(packet_error=7), "packet_error"),
])
def test_a_mirror_out_of_its_form_is_refused_naming_the_field(ws, m, field):
    with pytest.raises(ctl.ControllerError) as e:
        take(ws, m)
    assert f"The mirror's {field} is not" in str(e.value) or f"The mirror has no {field}." in str(e.value)
    assert ws.controller.record["held"] is None


def _without(path):
    """A mirror with one field the contract names left out (a dotted path; [0] the first item of a list)."""
    m = mirror(decisions=[{"id": uid(), "n": 1, "question": None, "text": "Yes.", "at": NOW}],
               requirements=[{"id": uid(), "state": "open", "hold": False, "waits_for_go": False}],
               questions=[{"id": uid(), "state": "open", "decision": None}])
    where, *rest = path.split(".")
    if where.endswith("[0]"):
        del m[where[:-3]][0][rest[0]]
    elif rest:
        del m[where][rest[0]]
    else:
        del m[where]
    return m


@pytest.mark.parametrize("path", ["version", "state_at", "account", "chat.effort", "end_all", "requests[0].at",
                                  "requests[0].checks",
                                  "decisions[0].question", "requirements[0].waits_for_go", "questions[0].decision",
                                  "packet_b64", "packet_digest", "packet_error"])
def test_a_mirror_missing_a_field_the_contract_names_is_refused_even_one_that_may_be_null(ws, path):
    with pytest.raises(ctl.ControllerError) as e:
        take(ws, _without(path))
    assert str(e.value) == f"The mirror has no {path}."
    take(ws, mirror())                                  # the same mirror whole: taken


def test_a_packet_of_another_schema_or_too_large_is_refused(ws, monkeypatch):
    data = json.dumps({"schema": "ade.resume.v1"}).encode()
    m = mirror(packet_b64=base64.b64encode(data).decode(), packet_digest=shapes.tagged_digest(ctl.PACKET_TAG, data))
    assert "is not one of ade.controller.v1" in refused(take, ws, m)
    monkeypatch.setattr(shapes, "PACKET_MAX", 10)
    b64, digest = packet()
    assert "past 10 bytes" in refused(take, ws, mirror(packet_b64=b64, packet_digest=digest))


def test_a_mirror_is_held_and_answered_with_its_version_and_a_lower_one_is_taken_too(ws):
    b64, digest = packet()
    assert take(ws, mirror(version=5, packet_b64=b64, packet_digest=digest)) == {"held": 5}
    assert page(ws)["held"] == 5
    assert take(ws, mirror(version=2)) == {"held": 2}
    assert ws.controller.record["mirror"]["version"] == 2


def test_on_with_an_account_the_workspace_lacks_is_refused_412_and_what_it_held_stays(ws):
    take(ws, mirror(version=1, state="off", account=None))
    with pytest.raises(ctl.NotHere) as e:
        take(ws, mirror(version=2, account="0123456789ab"))
    assert "no Claude Code account" in str(e.value)
    assert ws.controller.record["held"] == 1
    assert take(ws, mirror(version=3, state="stopped", account="0123456789ab")) == {"held": 3}


def test_end_all_s_first_count_is_a_baseline_and_each_rise_stops_every_open_session(ws):
    m = on(ws, end_all=4)
    requirement = plan(ws, m)
    first = start(ws, requirement)
    assert ws.controller.record["tasks"][first["task"]]["state"] == "starting"
    take(ws, dict(m, version=2, end_all=4))
    assert _Term.seen[-1].running is True
    # a rise, even in a mirror refused for its account: End all needs none
    with pytest.raises(ctl.NotHere):
        take(ws, dict(m, version=3, end_all=5, account="0123456789ab"))
    assert _Term.seen[-1].running is False
    t = ws.controller.record["tasks"][first["task"]]
    assert t["state"] == "stopped" and t["ended_at"] is not None
    assert os.path.isdir(ws._work(first["session"]))        # its worktree kept for Finish
    assert ws.controller.record["end_all"] == 5


def test_end_all_s_count_falling_is_a_new_baseline_and_stops_nothing(ws):
    m = on(ws, end_all=4)
    first = start(ws, plan(ws, m))
    take(ws, dict(m, version=2, end_all=1))         # an app that counts again, from a new store
    assert ws.controller.record["tasks"][first["task"]]["state"] == "starting" and _Term.seen[-1].running
    take(ws, dict(m, version=3, end_all=2))
    assert ws.controller.record["tasks"][first["task"]]["state"] == "stopped"


def test_the_owner_s_new_words_are_kept_for_the_chat_once_and_never_a_request_already_planned(ws):
    m = on(ws)
    plan(ws, m)
    newer = request("Also make the licence say MIT.", n=2)
    decision = {"id": uid(), "n": 1, "question": None, "text": "Use main.", "at": NOW}
    take(ws, dict(m, version=2, requests=[newer, *m["requests"]], decisions=[decision]))
    take(ws, dict(m, version=3, requests=[newer, *m["requests"]], decisions=[decision]))
    assert [(p["kind"], p["id"], p["text"]) for p in ws.controller.pending] == [
        ("request", newer["id"], "Also make the licence say MIT."), ("decision", decision["id"], "Use main.")]


def test_a_fresh_chat_waits_for_a_packet_built_on_all_the_controller_did(ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, packet_b64=b64, packet_digest=digest)
    plan(ws, m)
    made = []

    async def fresh(given, covered):
        made.append(given["version"])
    monkeypatch.setattr(ws.controller, "_fresh_chat", fresh)
    monkeypatch.setattr(ws.controller, "cannot_run", lambda given: None)
    asyncio.run(ws.controller.keep_chat())
    assert made == []                   # the app has not read the plan: its packet would not hold it
    # the app's read reaches the record's end, and is told no side is held, so that it sends its side again
    got = page(ws)
    assert got["held"] is None
    take(ws, dict(m, version=2))
    asyncio.run(ws.controller.keep_chat())
    assert made == []                   # a page served is not one taken: its read may have failed after it
    assert page(ws, got["seq"])["held"] is None          # the next round's read, asked since what it took
    take(ws, dict(m, version=3))
    asyncio.run(ws.controller.keep_chat())
    assert made == [3]


class _Chat:
    """A chat as keep_chat sees one: its state, its settings, and its claude running (`client`) until it is stopped."""

    def __init__(self, sid, state="idle", chosen=None):
        self.session_id, self.state, self.client, self.restart = sid, state, object(), False
        self.chosen = dict(chosen or {"model": "opus", "effort": "xhigh"})

    async def stop(self):
        self.client, self.state = None, "stopped"


def _primed(w, m, account="default", state="idle", chosen=None):
    """The controller's chat as keep_chat finds it once it started: its session, primed, on `account`."""
    sid = secrets.token_hex(6)
    os.makedirs(os.path.join(w._dir(sid), "work"))
    w._write({"id": sid, "repo": None, "program": "chat", "task": ctl.CHAT_TITLE, "base": None, "branch": None,
              "created_at": NOW, "finished": None})
    with w.controller._lock:
        w.controller.record.update(session=sid, chats=[sid], chat={"account": account, "primed": True,
                                                                   "requests": [r["id"] for r in m["requests"]],
                                                                   "decisions": []})
        given = set(w.controller.record["chat"]["requests"])
        w.controller.pending = [p for p in w.controller.pending if p["id"] not in given]     # as _prime leaves it
    w.chats[sid] = _Chat(sid, state, chosen)
    return sid


def _watch_chat(w, monkeypatch):
    made, chosen, sent = [], [], []

    async def fresh(given, covered):
        made.append(given["account"])

    async def choose(sid, given):
        chosen.append(dict(given))

    async def send(sid, text):
        sent.append(text)
    monkeypatch.setattr(w.controller, "_fresh_chat", fresh)
    monkeypatch.setattr(w.controller, "cannot_run", lambda given: None)
    monkeypatch.setattr(w, "chat_choose", choose)
    monkeypatch.setattr(w, "chat_send", send)
    return made, chosen, sent


def test_a_change_of_account_starts_a_fresh_chat_even_mid_turn_and_a_compaction_waits_for_the_turn(ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, packet_b64=b64, packet_digest=digest)
    made, _, _ = _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m, state="running")
    ws.controller.compacted = True
    asyncio.run(ws.controller.keep_chat())
    assert made == []                   # a compaction: once the turn has ended
    ws.chats[sid].state = "idle"
    asyncio.run(ws.controller.keep_chat())
    assert made == ["default"]
    ws.controller.compacted = False
    ws.chats[sid].state = "running"
    with ws.controller._lock:
        ws.controller.record["mirror"]["account"] = "0123456789ab"
    asyncio.run(ws.controller.keep_chat())
    assert made == ["default", "0123456789ab"]      # never on two accounts: at once


def test_the_owner_s_new_model_or_effort_reaches_the_chat_and_nothing_is_sent_while_the_window_is_spent(
        ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, packet_b64=b64, packet_digest=digest)
    made, chosen, sent = _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m)
    take(ws, dict(m, version=2, chat={"model": "sonnet", "effort": "xhigh"}))
    asyncio.run(ws.controller.keep_chat())
    assert chosen == [{"model": "sonnet", "effort": "xhigh"}] and made == []     # a new model: the running claude's
    ws.chats[sid].chosen = {"model": "sonnet", "effort": "xhigh"}               # as the chat keeps it
    take(ws, dict(m, version=3, chat={"model": "sonnet", "effort": "high"}))
    asyncio.run(ws.controller.keep_chat())
    # a new effort reaches claude only as it starts again, which would read its old conversation back: a fresh chat
    assert made == ["default"] and len(chosen) == 1
    ws.controller.record["session"] = sid
    ws.chats[sid].chosen = {"model": "sonnet", "effort": "high"}
    take(ws, dict(m, version=4, chat={"model": "sonnet", "effort": "high"},
                  requests=[request("And a licence.", n=2), *m["requests"]]))
    ws.controller.rate_limited({"status": "rejected", "rate_limit_type": "five_hour",
                                "resets_at": int(time.time()) + 600})
    asyncio.run(ws.controller.keep_chat())
    assert sent == []
    ws.controller.rates.clear()
    asyncio.run(ws.controller.keep_chat())
    # the window the chat's own turn stopped at has reset: told to go on, with the request that waited
    assert len(sent) == 1 and sent[0].startswith(ctl.GO_ON) and ctl.NEW_FROM_OWNER in sent[0]
    assert "And a licence." in sent[0]


def test_a_window_spent_holds_its_packet_too(ws, monkeypatch):
    b64, digest = packet()
    on(ws, packet_b64=b64, packet_digest=digest)
    made, _, _ = _watch_chat(ws, monkeypatch)
    ws.controller.rate_limited({"status": "rejected", "rate_limit_type": "five_hour",
                                "resets_at": int(time.time()) + 600})
    asyncio.run(ws.controller.keep_chat())
    assert made == []                   # no fresh chat primed while the window is spent: its packet waits too
    ws.controller.rates.clear()
    asyncio.run(ws.controller.keep_chat())
    assert made == ["default"]


def test_off_or_stopped_stops_the_chat_and_sends_it_nothing(ws, monkeypatch):
    m = on(ws)
    _, _, sent = _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m)
    stopped = []

    async def stop():
        stopped.append(sid)
    ws.chats[sid].client = object()
    ws.chats[sid].stop = stop
    take(ws, dict(m, version=2, state="stopped", requests=[request("Later.", n=2), *m["requests"]]))
    asyncio.run(ws.controller.keep_chat())
    assert stopped == [sid] and sent == []


def test_a_mirror_the_controller_cannot_run_on_is_kept_for_its_restraints(ws, monkeypatch):
    m = on(ws)
    requirement = plan(ws, m)
    held = dict(m, version=2, chat={"model": "opus", "effort": "ultracode-plus"},
                requirements=[{"id": requirement, "state": "open", "hold": True, "waits_for_go": False}])
    with pytest.raises(ctl.NotHere) as e:
        take(ws, held)
    assert "model and effort" in str(e.value) and ws.controller.record["held"] == 1     # sent again by the app
    # its restraints in force (the hold), and the controller refusing to run until a side it can run on comes
    assert "cannot run as the owner's side sets it" in refused(start, ws, requirement)
    assert "cannot run as the owner's side sets it" in refused(ws.controller.ask_owner, {"text": "Hello?"})
    assert ws.controller.busy() is False
    assert ws.controller.read_tree()["requirements"][0]["hold"] is True      # its hold in force, though refused
    take(ws, dict(held, version=3, chat=m["chat"]))
    assert "holds that requirement" in refused(start, ws, requirement)


def test_the_count_of_end_all_is_kept_only_once_its_stops_are_done(ws, monkeypatch):
    m = on(ws, end_all=1)
    got = start(ws, plan(ws, m))

    async def crash():
        raise RuntimeError("the agent stopped here")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ws.controller, "end_all", crash)
        with pytest.raises(RuntimeError):
            take(ws, dict(m, version=2, end_all=2))
    assert ws.controller.record["end_all"] == 1          # the stops not done: the count not kept
    take(ws, dict(m, version=3, end_all=2))
    assert ws.controller.record["tasks"][got["task"]]["state"] == "stopped" and ws.controller.record["end_all"] == 2


def test_a_plan_the_app_read_and_does_not_list_is_started_from_no_more_and_told(ws):
    m = on(ws)
    requirement = plan(ws, m)
    read_through(ws)                                    # the app took the plan
    take(ws, dict(m, version=2))                         # and does not list it: refused
    assert "did not take its plan" in refused(start, ws, requirement)
    assert ws.controller.wakes["plan:" + requirement]["requirement"] == requirement
    take(ws, dict(m, version=3))
    assert list(ws.controller.wakes) == ["plan:" + requirement]        # told once


def test_plans_and_questions_the_app_has_not_read_are_counted_whatever_the_side_s_version(ws, monkeypatch):
    m = on(ws)
    for i in range(3):
        ws.controller.ask_owner({"text": f"Question {i}?"})
    take(ws, dict(m, version=2))                          # built before the app read them
    assert "3 questions are open already" in refused(ws.controller.ask_owner, {"text": "A fourth?"})
    monkeypatch.setattr(ctl, "REQUIREMENTS_OPEN_MOST", 1)
    plan(ws, m)
    take(ws, dict(m, version=3))
    assert "1 requirements are open already" in refused(plan, ws, m)


def test_a_plan_the_owner_s_side_lists_is_kept_past_four_weeks(ws):
    m = on(ws)
    kept, gone = plan(ws, m), plan(ws, m)
    for k in (kept, gone):
        ws.controller.record["plans"][k]["at"] = NOW - 40 * 86400
    take(ws, dict(m, version=2, requirements=[{"id": kept, "state": "open", "hold": False, "waits_for_go": False}]))
    ws.controller.refresh()
    assert kept in ws.controller.record["plans"] and gone not in ws.controller.record["plans"]


def test_two_starts_at_once_are_paced_one_after_the_other(ws, monkeypatch):
    now = int(time.time())
    m = on(ws, cap=3)
    requirement = plan(ws, m)
    monkeypatch.setattr(ws.controller, "_newest", lambda: {"at": now, "five_hour": {"used_percentage": 10,
                                                                                    "resets_at": now + 3600}})

    async def both():
        return await asyncio.gather(*(ws.controller.start_task({"requirement": requirement, "repo": "demo",
                                                                "task": f"Task {i}."}) for i in range(2)),
                                    return_exceptions=True)
    results = asyncio.run(both())
    assert sorted(type(r).__name__ for r in results) == ["ControllerError", "dict"]
    assert "speed is not measured yet" in str(next(r for r in results if isinstance(r, Exception)))


def test_the_page_s_cut_is_the_states_where_they_are_cut_below_the_actions(ws, monkeypatch):
    monkeypatch.setattr(ctl, "PAGE", 2)
    ended = [_task(ws, "done") for _ in range(3)]
    with ws.controller._lock:
        later = [ws.controller._act("question", question=uid(), requirement=None, text="q") for _ in range(3)]
    got = page(ws)
    assert got["more"] is True and got["seq"] == ended[1]["seq"] < later[0]["seq"]


def test_a_rate_limit_event_with_no_reset_holds_five_hours_and_no_longer(ws):
    c = ws.controller
    c.rate_limited({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": None})
    assert c.pace(NOW)[0] is False
    c.rates["five_hour"]["at"] = NOW - ctl.UNTIMED - 1
    assert c.pace(NOW)[0] is True


def test_the_chat_is_kept_though_the_round_s_states_fail_and_one_removed_is_made_again(ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, packet_b64=b64, packet_digest=digest)
    made, _, _ = _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m)
    shutil.rmtree(ws._dir(sid))                           # its session removed, as the owner's Remove leaves none
    del ws.chats[sid]
    asyncio.run(ws.controller.keep_chat())
    assert made == ["default"]                           # a fresh one

    def broken(now=None):
        raise RuntimeError("a state that cannot be read")
    monkeypatch.setattr(ws.controller, "tick", broken)
    kept = []

    async def keep():
        kept.append(True)
    monkeypatch.setattr(ws.controller, "keep_chat", keep)

    async def one_round():
        ws.controller.start()
        await asyncio.sleep(0.3)
        await ws.controller.shut()
    asyncio.run(one_round())
    assert kept


def test_a_chat_whose_record_is_removed_while_its_claude_runs_is_replaced_by_a_fresh_one(ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, packet_b64=b64, packet_digest=digest)
    made, _, sent = _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m)
    # its record removed (its sessions' programs can, as they can write it) while its claude runs: the owner's Remove,
    # which closes the chat too, is the test above
    os.remove(os.path.join(ws._dir(sid), "session.json"))
    assert ws.chats[sid].client is not None
    asyncio.run(ws.controller.keep_chat())
    assert made == ["default"] and sent == []


# ---- plan_requirement ------------------------------------------------------------------------------------------------

def test_a_plan_is_recorded_once_with_the_contract_s_fields(ws):
    m = on(ws)
    requirement = plan(ws, m)
    [a] = actions(ws, "plan")
    assert set(a) == {"id", "kind", "at", "requirement", "request", "title", "words", "acceptance", "repos", "checks"}
    assert (a["requirement"], a["request"], a["title"], a["words"], a["acceptance"], a["repos"], a["checks"]) == (
        requirement, m["requests"][0]["id"], "Hello README", "Make the README say hello.", "Its first line is hello.",
        ["demo"], [])
    assert ctl._UID.fullmatch(a["id"]) and ctl._UID.fullmatch(requirement)


def test_a_title_is_kept_as_one_line_with_nothing_hidden(ws):
    m = on(ws)
    plan(ws, m, title="Hello\n  README\u200b\ttoday")
    assert actions(ws, "plan")[0]["title"] == "Hello README today"


@pytest.mark.parametrize("over,why", [
    ({"words": "the README say hello."}, "not whole paragraphs"),
    ({"words": "Make the README"}, "not whole paragraphs"),
    ({"words": "  Make the README say hello."}, "not whole paragraphs"),
    ({"words": "Make the README say hello.\n\nIts first line"}, "not whole paragraphs"),
    ({"words": "Make the README  say hello."}, "not whole paragraphs"),
    # whole paragraphs, but with whitespace around them that is not the request's own
    ({"words": "Make the README say hello.  "}, "not whole paragraphs"),
    ({"words": "\tMake the README say hello."}, "not whole paragraphs"),
    ({"words": "   "}, "words is text"),
    ({"acceptance": ""}, "acceptance is text"),
    ({"acceptance": "first line is hello."}, "acceptance is not whole paragraphs"),
    ({"request": uid()}, "not a request of the owner's"),
    ({"title": "\u200b\u200b"}, "The title is blank"),
    ({"title": "x" * 301}, "at most 300 bytes"),
    ({"title": "token " + SECRET_SHAPED}, "shaped like"),
    ({"repos": []}, "repos is"),
    ({"repos": ["demo"] * 11}, "repos is"),
    ({"repos": ["missing"]}, "no repository missing"),
    ({"repos": ["../demo"]}, "named as the workspace names it"),
])
def test_a_plan_the_app_would_refuse_is_refused_here_first(ws, over, why):
    m = on(ws)
    assert why in refused(plan, ws, m, **over)
    assert actions(ws, "plan") == []


def test_a_plan_is_refused_while_the_controller_is_not_on(ws):
    m = on(ws, state="stopped")
    assert "not on" in refused(plan, ws, m)


def test_whole_paragraphs_are_judged_as_the_app_judges_them():
    texts = [REQUEST, "  One.\n\n\n  Two two.  \n\nThree\nlines here\n", "Solo", "a\n \nb\n\n\nc", "\n\nx\n\ny\n\n"]
    for text in texts:
        cuts = sorted({0, len(text), *[i for i, ch in enumerate(text) if ch in "\n "], *range(0, len(text), 3)})
        for i in cuts:
            for j in cuts:
                if j > i:
                    for part in (text[i:j], "\t" + text[i:j].strip(), text[i:j].strip() + "  ", " " + text[i:j]):
                        assert ctl.whole_in(part, text) == app_controller._whole_in(part, text), (text, part)


def test_the_characters_that_do_not_show_themselves_are_the_app_s():
    assert ctl.SELECTORS == link.SELECTORS and ctl.PRESENTATION == link.PRESENTATION and ctl.BLANK == link.BLANK
    assert ctl.DRAWN_OVER == link_commands.DRAWN_OVER
    sample = "a\\b\u200bc\nd\u202ee\U000e0041\ufe0f\ufe0fx\u20dd"
    assert ctl.owner_text(sample) == app_tasks.owner_text(sample)


# ---- start_task ------------------------------------------------------------------------------------------------------

def test_a_start_is_a_claude_terminal_with_its_model_effort_and_first_prompt_in_three_marked_parts(ws):
    m = on(ws)
    requirement = plan(ws, m)
    got = start(ws, requirement, model="sonnet", effort="high", base="main")
    argv = _Term.seen[-1].argv
    assert argv[argv.index("--effort") + 1] == "high" and "--model=sonnet" in argv
    prompt = argv[-1]
    one, two, three = prompt.split("\n\nPart ")[1:]
    assert one.startswith("1, the owner's request") and REQUEST in one
    assert one.endswith("The paragraphs of it this task's requirement was planned from, word for word:\n"
                        "Make the README say hello.")
    assert two.startswith("2, the requirement and this task") and "a model's words" in two
    assert two.endswith('Requirement: "Hello README"\nTask: "Edit README.md so its first line is hello."')
    assert three == ("3, the acceptance, the owner's words from the request, word for word:\nIts first line is hello."
                     "\n\nThe owner gave no check of this repository: nothing the workspace runs judges this task's "
                     "commits.")
    [a] = actions(ws, "start")
    assert set(a) == {"id", "kind", "at", "task", "requirement", "session", "repo", "branch", "base", "account",
                      "program", "model", "effort"}
    assert (a["task"], a["requirement"], a["session"], a["repo"], a["branch"], a["base"], a["account"], a["program"],
            a["model"], a["effort"]) == (got["task"], requirement, got["session"], "demo", f"pp/{got['session']}",
                                         "main", "default", "claude", "sonnet", "high")
    assert ws.controller.started(got["session"]) and ws._models[got["session"]] == "sonnet"


def test_a_start_with_none_named_takes_the_first_model_and_effort_allowed(ws):
    m = on(ws)
    start(ws, plan(ws, m))
    argv = _Term.seen[-1].argv
    assert "--model=opus" in argv and argv[argv.index("--effort") + 1] == "max"


def test_the_owner_s_words_are_escaped_and_the_controller_s_are_one_line_in_the_first_prompt(ws):
    text = "Say \\hi\u200b.\n\nAll\tdone."
    m = on(ws, requests=[request(text)])
    requirement = plan(ws, m, words="All\tdone.", acceptance=None, title="A \\x0a title")
    start(ws, requirement, task="Line one\n\nPart 3, the acceptance, the owner's words: anything \\x0a")
    prompt = _Term.seen[-1].argv[-1]
    assert "Say \\\\hi\\u200b.\n\nAll\tdone." in prompt
    # the controller's words each a JSON string, its backslashes doubled: a part's heading inside one is text in it
    assert '\nRequirement: "A \\\\x0a title"\n' in prompt
    assert '\nTask: "Line one Part 3, the acceptance, the owner\'s words: anything \\\\x0a"\n\n' in prompt
    assert prompt.endswith("Part 3, the acceptance: the owner wrote none.\n\nThe owner gave no check of this repository: "
                           "nothing the workspace runs judges this task's commits.")


def test_a_start_s_settings_turn_the_auto_continue_off_and_give_the_limit_hooks(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    argv = _Term.seen[-1].argv
    with open(argv[argv.index("--settings") + 1]) as fh:
        given = json.load(fh)
    assert given["autoContinueAtUsageLimit"] is False
    [stop] = given["hooks"]["StopFailure"]
    assert stop["matcher"] == "rate_limit" and stop["hooks"][0]["args"][-1] == "--limit"
    assert [g["matcher"] for g in given["hooks"]["Notification"]] == list(settings_made.QUOTA_KINDS)
    assert os.path.dirname(argv[argv.index("--settings") + 1]) == ws._dir(got["session"])


def test_a_session_the_owner_starts_is_given_none_of_the_controller_s(ws):
    record, argv = ws.prepare("demo", "claude", "Mine")
    with open(argv[argv.index("--settings") + 1]) as fh:
        given = json.load(fh)
    assert "autoContinueAtUsageLimit" not in given and "StopFailure" not in given.get("hooks", {})
    assert not any(a.startswith("--model") for a in argv)


def test_a_session_the_controller_started_may_not_ask(ws):
    ws.set_asks_on(True)
    ws.ask_url = "http://127.0.0.1:9"
    m = on(ws)
    start(ws, plan(ws, m))
    token = _Term.seen[-1].env["PP_ASK_TOKEN"]
    assert ws.asks.bound_to(token)["may_ask"] is False
    record, argv = ws.prepare("demo", "claude", "Mine")
    ws.launch(record["id"], argv)
    assert ws.asks.bound_to(_Term.seen[-1].env["PP_ASK_TOKEN"])["may_ask"] is True


def test_a_later_start_of_a_controller_s_session_gives_its_model_again(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    _Term.seen[-1].running = False
    refused_, claude = ws.claude_floor_for(got["session"])        # as the start route reads it, first
    ws.launch(got["session"], claude=claude, effort="max")
    argv = _Term.seen[-1].argv
    assert refused_ is None and "--model=opus" in argv and argv.count("--model=opus") == 1


@pytest.mark.parametrize("change,why", [
    (lambda w, m, r: {"program": "codex"}, "Claude Code alone"),
    (lambda w, m, r: {"requirement": uid()}, "no plan of that requirement"),
    (lambda w, m, r: {"repo": "other"}, "not one the requirement may touch"),
    (lambda w, m, r: {"repo": "missing"}, "no repository missing"),
    (lambda w, m, r: {"model": "haiku"}, "not one the owner allows"),
    (lambda w, m, r: {"effort": "low"}, "not one the owner allows"),
    (lambda w, m, r: {"model": "--dangerously-skip-permissions"}, "not a model's name"),
    (lambda w, m, r: {"task": "  \n "}, "The task is blank"),
    (lambda w, m, r: {"task": "x" * 4001}, "at most 4000 characters"),
    (lambda w, m, r: {"task": "use " + SECRET_SHAPED}, "shaped like"),
    (lambda w, m, r: {"base": "main..evil"}, "a branch, a tag or a commit"),
    (lambda w, m, r: {"base": "nothere"}, "no commit nothere"),
])
def test_a_start_outside_the_owner_s_bounds_is_refused_and_nothing_starts(ws, change, why):
    m = on(ws)
    requirement = plan(ws, m)
    assert why in refused(start, ws, requirement, **change(ws, m, requirement))
    assert actions(ws, "start") == [] and _Term.seen == [] and ws.controller.record["tasks"] == {}


@pytest.mark.parametrize("state,why", [
    ({"hold": True}, "holds that requirement"), ({"waits_for_go": True}, "waits for the owner's go"),
    ({"state": "done"}, "is closed"),
])
def test_a_start_for_a_requirement_held_waiting_or_closed_is_refused(ws, state, why):
    m = on(ws)
    requirement = plan(ws, m)
    take(ws, dict(m, version=2, requirements=[dict({"id": requirement, "state": "open", "hold": False,
                                                    "waits_for_go": False}, **state)]))
    assert why in refused(start, ws, requirement)


def test_a_plan_not_yet_confirmed_waits_for_the_go_where_plans_wait_for_it(ws):
    m = on(ws, confirm_plans=True)
    assert "waits for the owner's go" in refused(start, ws, plan(ws, m))


def test_a_start_past_the_cap_or_while_off_is_refused(ws):
    m = on(ws, cap=1)
    requirement = plan(ws, m)
    start(ws, requirement)
    assert "its cap" in refused(start, ws, requirement)
    take(ws, dict(m, version=2, state="stopped"))
    assert "not on" in refused(start, ws, requirement)


def test_a_start_the_pacing_refuses_is_refused_in_its_words(ws):
    m = on(ws)
    requirement = plan(ws, m)
    ws.controller.rate_limited({"status": "rejected", "rate_limit_type": "five_hour",
                                "resets_at": int(time.time()) + 600})
    assert "five_hour window is spent" in refused(start, ws, requirement)


def test_a_start_past_the_day_s_starts_is_refused(ws, monkeypatch):
    monkeypatch.setattr(ctl, "STARTS_A_DAY", 1)
    m = on(ws, cap=3)
    requirement = plan(ws, m)
    start(ws, requirement)
    assert "as much as the app takes in a day" in refused(start, ws, requirement)


# ---- states, stop, the wait and the continue -------------------------------------------------------------------------

def _limit(w, sid, kind, at=None):
    with open(os.path.join(w._dir(sid), ctl.LIMITS), "a") as fh:
        fh.write(json.dumps({"at": at or int(time.time()), "kind": kind}) + "\n")


def _status(w, sid, used, resets_at, seven=None):
    data = {"at": int(time.time()), "five_hour": {"used_percentage": used, "resets_at": resets_at}}
    if seven:
        data["seven_day"] = {"used_percentage": seven[0], "resets_at": seven[1]}
    with open(os.path.join(w._dir(sid), "status.json"), "w") as fh:
        json.dump(data, fh)


def _activity(w, sid, state, at=None):
    with open(os.path.join(w._dir(sid), "activity.json"), "w") as fh:
        json.dump({"state": state, "at": at or int(time.time()) + 1}, fh)


def test_a_session_s_state_follows_its_hooks_and_its_program(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    t = ws.controller.record["tasks"][got["task"]]
    ws.controller.refresh()
    assert t["state"] == "starting"
    for said, state in (("working", "working"), ("waiting", "idle"), ("idle", "idle")):
        _activity(ws, got["session"], said)
        ws.controller.refresh()
        assert t["state"] == state
    _Term.seen[-1].running = False
    ws.controller.refresh()
    assert (t["state"], t["ended_at"] is not None) == ("done", True)


def test_a_program_that_ends_while_working_has_failed_and_one_the_owner_stops_is_stopped(ws):
    m = on(ws, cap=3)
    requirement = plan(ws, m)
    one, two = start(ws, requirement), start(ws, requirement)
    _activity(ws, one["session"], "working")
    [t1, t2] = _Term.seen[-2:]
    t1.running = False
    ws.controller.refresh()
    assert ws.controller.record["tasks"][one["task"]]["state"] == "failed"
    asyncio.run(ws.stop(two["session"]))
    ws.controller.refresh()
    assert ws.controller.record["tasks"][two["task"]]["state"] == "stopped"


def test_stop_task_ends_a_session_the_controller_started_and_keeps_its_worktree(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    assert asyncio.run(ws.controller.stop_task({"task": got["task"]}))["state"] == "stopped"
    assert _Term.seen[-1].running is False and os.path.isdir(ws._work(got["session"]))
    assert [a["task"] for a in actions(ws, "stop")] == [got["task"]]
    assert "ended already" in refused(ws.controller.stop_task, {"task": got["task"]})
    assert "not a task the Manager started" in refused(ws.controller.stop_task, {"task": uid()})


def _waiting(w, m, reset):
    got = start(w, plan(w, m))
    _status(w, got["session"], 100, reset)
    _limit(w, got["session"], "rate_limit", at=int(time.time()) + 1)
    w.controller.refresh()
    return got, w.controller.record["tasks"][got["task"]]


def _two_waiting(w, m, reset):
    """Two sessions waiting at the account's limit, the first stopped there a second before the other: both started
    before any line said the window was spent (none starts into a window a line says is spent)."""
    first = start(w, plan(w, m))
    _limit(w, first["session"], "rate_limit", at=int(time.time()) + 1)
    w.controller.refresh()                          # waiting, its reset not known yet: none of the sessions runs
    second = start(w, plan(w, m))
    time.sleep(1.1)
    _limit(w, second["session"], "rate_limit", at=int(time.time()) + 1)
    for got in (first, second):
        _status(w, got["session"], 100, reset)
    w.controller.refresh()
    tasks = w.controller.record["tasks"]
    return (first, tasks[first["task"]]), (second, tasks[second["task"]])


def _passed(w, *waiting):
    """The window the waiting sessions stopped at has reset: their marks past, and their lines read before it."""
    past = int(time.time()) - ctl.CONTINUE_AFTER - 1
    for got, t in waiting:
        t["waits_until"] = past
        _status(w, got["session"], 100, past)


def test_a_session_that_stops_at_its_account_s_limit_waits_marked_with_its_reset(ws):
    reset = int(time.time()) + 3600
    m = on(ws)
    got, t = _waiting(ws, m, reset)
    assert (t["state"], t["waits_until"]) == ("waiting", reset)
    assert page(ws)["tasks"][0] == {"id": got["task"], "state": "waiting", "waits_until": reset, "ended_at": None,
                                    "checks": None, "why": None, "checking": False, "needs_owner": False,
                                    "changed": t["state_seq"], "stopped_by": None}
    assert "has not reset yet" in refused(ws.controller.continue_task, {"task": got["task"]})


def test_after_the_reset_continue_task_sends_the_one_fixed_line_with_its_model_and_effort(ws):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    out = asyncio.run(ws.controller.continue_task({"task": got["task"]}))
    argv = _Term.seen[-1].argv
    assert argv[-1] == ctl.FIXED_LINE and out["sent"] == ctl.FIXED_LINE
    assert "--model=opus" in argv and argv[argv.index("--effort") + 1] == "max"
    assert _Term.seen[-2].running is False                     # the program at the limit, stopped first
    assert t["state"] == "working" and [a["task"] for a in actions(ws, "continue")] == [got["task"]]


def test_the_oldest_waiting_session_is_continued_first(ws):
    m = on(ws, cap=3)
    (first, t1), (second, t2) = _two_waiting(ws, m, NOW + 3600)
    _passed(ws, (first, t1), (second, t2))
    assert f"oldest first: task {first['task']}" in refused(ws.controller.continue_task, {"task": second["task"]})
    asyncio.run(ws.controller.continue_task({"task": first["task"]}))


def test_a_session_claude_code_went_on_with_is_never_told_to_go_on_again(ws):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _limit(ws, got["session"], "quota_auto_resume_fired", at=int(time.time()) + 2)
    ws.controller.refresh()
    assert t["state"] == "working"
    assert "went on with it by itself" in refused(ws.controller.continue_task, {"task": got["task"]})


def test_a_wait_claude_code_ended_without_going_on_still_waits_for_the_controller(ws):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    for kind in ("quota_auto_resume_disabled", "quota_auto_resume_stale"):
        _limit(ws, got["session"], kind, at=int(time.time()) + 2)
    ws.controller.refresh()
    assert t["state"] == "waiting"


def test_a_session_waits_for_its_owner_when_no_reset_is_known(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    _limit(ws, got["session"], "rate_limit", at=int(time.time()) + 1)
    ws.controller.refresh()
    t = ws.controller.record["tasks"][got["task"]]
    assert (t["state"], t["waits_until"]) == ("waiting", None)
    assert "waits for the owner" in refused(ws.controller.continue_task, {"task": got["task"]})


def test_continue_is_refused_for_a_session_not_waiting_or_not_the_controller_s(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    assert "not waiting" in refused(ws.controller.continue_task, {"task": got["task"]})
    assert "not a task the Manager started" in refused(ws.controller.continue_task, {"task": uid()})


def test_the_owner_s_stop_of_a_waiting_session_ends_it(ws):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    asyncio.run(ws.stop(got["session"]))
    assert t["state"] == "stopped"


def _floor_then(w, monkeypatch, during):
    """The floor's read of a continue, with `during` done while it runs, as a stop or a mirror landing then."""
    real = w.claude_floor_for

    def floor(sid):
        during()
        return real(sid)
    monkeypatch.setattr(w, "claude_floor_for", floor)


@pytest.mark.parametrize("during", ["stop", "end_all", "end_all_under_way", "stopped", "hold"])
def test_a_continue_that_something_stopped_while_it_read_its_claude_starts_nothing(ws, monkeypatch, during):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    seen = len(_Term.seen)
    where = {}

    def on_the_loop(coro):
        return asyncio.run_coroutine_threadsafe(coro, where["loop"]).result(10)

    def land():
        if during == "stop":
            ws.controller.stopping(got["session"])
        elif during == "end_all":
            on_the_loop(ws.controller.end_all())
        elif during == "end_all_under_way":
            # End all marks every open session first and stops them one at a time: this one marked, not yet stopped
            with ws.controller._lock:
                t["stopping"] = True
                t.pop("continuing", None)
        elif during == "stopped":
            on_the_loop(ws.controller.take(dict(m, version=2, state="stopped")))
        else:
            on_the_loop(ws.controller.take(dict(m, version=2, requirements=[
                {"id": t["requirement"], "state": "open", "hold": True, "waits_for_go": False}])))
    _floor_then(ws, monkeypatch, land)

    async def go():
        where["loop"] = asyncio.get_running_loop()
        with pytest.raises(ctl.ControllerError):
            await ws.controller.continue_task({"task": got["task"]})
    asyncio.run(go())
    assert len(_Term.seen) == seen and actions(ws, "continue") == [] and not t.get("continuing")
    assert t["state"] in ("waiting", "stopped")


def test_two_continues_of_one_task_at_once_start_it_once(ws, monkeypatch):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    _floor_then(ws, monkeypatch, lambda: time.sleep(0.3))
    seen = len(_Term.seen)

    async def both():
        return await asyncio.gather(*(ws.controller.continue_task({"task": got["task"]}) for _ in range(2)),
                                    return_exceptions=True)
    results = asyncio.run(both())
    assert sorted(type(r).__name__ for r in results) == ["ControllerError", "dict"]
    assert "being continued already" in str(next(r for r in results if isinstance(r, Exception)))
    assert len(_Term.seen) == seen + 1 and len(actions(ws, "continue")) == 1


def test_the_oldest_held_waiting_session_does_not_hold_back_the_others(ws):
    m = on(ws, cap=3)
    (first, t1), (second, t2) = _two_waiting(ws, m, NOW + 3600)
    _passed(ws, (first, t1), (second, t2))
    take(ws, dict(m, version=2, requirements=[
        {"id": t1["requirement"], "state": "open", "hold": True, "waits_for_go": False},
        {"id": t2["requirement"], "state": "open", "hold": False, "waits_for_go": False}]))
    assert "holds that requirement" in refused(ws.controller.continue_task, {"task": first["task"]})
    asyncio.run(ws.controller.continue_task({"task": second["task"]}))


def test_a_stop_is_taken_past_the_day_s_lines_and_a_session_started_again_stays_ended(ws, monkeypatch):
    m = on(ws, cap=1)
    got = start(ws, plan(ws, m))
    monkeypatch.setattr(ctl, "LINES_A_DAY", 0)
    asyncio.run(ws.controller.stop_task({"task": got["task"]}))
    t = ws.controller.record["tasks"][got["task"]]
    assert t["state"] == "stopped"
    seq = page(ws)["seq"]
    time.sleep(1.1)
    _, claude = ws.claude_floor_for(got["session"])
    ws.launch(got["session"], claude=claude, effort="max")      # the owner's own start of it, as the route makes it
    owners = _Term.seen[-1]
    _activity(ws, got["session"], "working")
    ws.controller.refresh()
    # ended as the app keeps it: on no page again, not counted against the cap, not stopped by End all; still the
    # controller's session for its model and its asking, but given none of its limit hooks or setting (nothing
    # watches it now: Claude Code's own continuing is as any session's)
    assert t["state"] == "stopped" and page(ws, seq)["tasks"] == []
    assert "--model=opus" in owners.argv and ws.controller.started(got["session"])
    given = open(owners.argv[owners.argv.index("--settings") + 1]).read()
    assert "autoContinueAtUsageLimit" not in given and "--limit" not in given
    take(ws, dict(m, version=2, requirements=[{"id": t["requirement"], "state": "open", "hold": False,
                                               "waits_for_go": False}]))          # the app took its plan
    another = start(ws, t["requirement"], task="Another.")       # at cap 1: the owner's run is not counted
    assert asyncio.run(ws.controller.end_all()) is True
    assert owners.running and ws.controller.record["tasks"][another["task"]]["state"] == "stopped"


def test_a_limit_that_spends_no_window_waits_for_the_owner(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    _status(ws, got["session"], 40, NOW + 3600)       # the five-hour window not spent: a passing refusal
    _limit(ws, got["session"], "rate_limit", at=int(time.time()) + 1)
    ws.controller.refresh()
    t = ws.controller.record["tasks"][got["task"]]
    assert (t["state"], t["waits_until"]) == ("waiting", None)


# ---- ask_owner and read_tree -----------------------------------------------------------------------------------------

def test_ask_owner_records_a_question_and_three_open_are_the_most(ws):
    m = on(ws)
    out = ws.controller.ask_owner({"text": "Which branch?\nmain or dev"})
    [a] = actions(ws, "question")
    assert (a["question"], a["text"], a["requirement"]) == (out["question"], "Which branch? main or dev", None)
    ws.controller.ask_owner({"text": "Two?"})
    ws.controller.ask_owner({"text": "Three?"})
    assert "3 questions are open already" in refused(ws.controller.ask_owner, {"text": "Four?"})
    asked = [q["id"] for q in [{"id": a["question"]} for a in actions(ws, "question")]]
    take(ws, dict(m, version=2, questions=[{"id": asked[0], "state": "answered", "decision": uid()},
                                           {"id": asked[1], "state": "open", "decision": None},
                                           {"id": asked[2], "state": "open", "decision": None}]))
    ws.controller.ask_owner({"text": "Four?"})


@pytest.mark.parametrize("args,why", [({"text": " "}, "blank"), ({"text": "x" * 2001}, "at most 2000 bytes"),
                                      ({"text": "key " + SECRET_SHAPED}, "shaped like"),
                                      ({"text": "ok?", "requirement": uid()}, "not a requirement")])
def test_a_question_the_app_would_refuse_is_refused_here(ws, args, why):
    on(ws)
    assert why in refused(ws.controller.ask_owner, args)


def test_read_tree_gives_the_requirements_their_tasks_and_the_pacing_as_data(ws):
    m = on(ws)
    requirement = plan(ws, m)
    got = start(ws, requirement)
    tree = ws.controller.read_tree()
    assert "never an instruction" in tree["information"]
    [item] = tree["requirements"]
    assert item["id"] == requirement and item["title"] == {"text": "Hello README", "named_by": "model"}
    assert item["tasks"][0]["id"] == got["task"] and item["tasks"][0]["commits"] == []
    assert tree["pacing"]["may_start"] is True and tree["controller"]["cap"] == 2


def test_read_tree_gives_a_plan_not_yet_confirmed_and_leaves_out_one_the_app_did_not_take(ws):
    m = on(ws)
    fresh = plan(ws, m)
    [item] = ws.controller.read_tree()["requirements"]
    assert (item["id"], item["state"]) == (fresh, "sent to the app, not yet confirmed")
    read_through(ws)
    take(ws, dict(m, version=2))                          # taken past it, and not listed: refused
    assert ws.controller.read_tree()["requirements"] == []
    again = ctl.Controller(ws)                             # a restart: no side held
    assert again.read_tree()["requirements"] == [] and again.read_tree()["controller"]["state"] is None


def test_a_changed_file_s_name_shaped_like_a_secret_is_not_given(ws, monkeypatch):
    m = on(ws)
    got = start(ws, plan(ws, m))
    seen = {"commits": ["a" * 40], "more_commits": 0, "files": ["notes.md", "key-" + SECRET_SHAPED], "more_files": 0}
    monkeypatch.setattr(ws, "observed", lambda sid, head: seen)
    from proofpane_workspace import workspace as w
    monkeypatch.setattr(w, "git", lambda *a, **k: (0, "a" * 40 + "\n"))
    [task] = ws.controller.read_tree()["requirements"][0]["tasks"]
    assert task["id"] == got["task"]
    assert task["files"][0]["text"] == "notes.md"
    assert task["files"][1]["text"].startswith("removed: shaped like a GitHub token")


def test_a_start_runs_on_the_side_in_force_when_it_starts(ws, monkeypatch):
    other = ws.account_add("claude", "Second")["id"]
    m = on(ws)
    requirement = plan(ws, m)
    real = ws.controller.pace
    moved = []

    def pace(now=None, model=None):
        if not moved:                                     # the owner moves the controller while the start is checked
            moved.append(True)
            ws.controller.record["mirror"] = dict(ws.controller.record["mirror"], account=other, version=2,
                                                  models=["sonnet"], efforts=["high"])
        return real(now, model)
    monkeypatch.setattr(ws.controller, "pace", pace)
    got = start(ws, requirement)
    t = ws.controller.record["tasks"][got["task"]]
    [a] = actions(ws, "start")
    assert (t["account"], a["account"], t["model"], t["effort"]) == (other, other, "sonnet", "high")
    assert ws.accounts.of(got["session"]) == other


def test_an_account_the_controller_moved_from_holds_nothing_and_its_chat_closes_at_once(ws, monkeypatch):
    other = ws.account_add("claude", "Second")["id"]
    m = on(ws)
    made, _, _ = _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m)
    closed = []

    async def close(session_id):
        closed.append(session_id)
    monkeypatch.setattr(ws, "close", close)
    ws.controller.rate_limited({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": NOW + 3600})
    assert ws.controller.pace(NOW)[0] is False
    take(ws, dict(m, version=2, account=other))            # no packet: no fresh chat yet
    assert ws.controller.pace(NOW)[0] is True               # the old account's spent window holds nothing here
    asyncio.run(ws.controller.keep_chat())
    # closed, and no longer the controller's chat, so its tools refuse (the crossing test calls one of a replaced chat)
    assert closed == [sid] and ws.controller.record["session"] is None and made == []


def test_a_task_on_an_account_the_controller_moved_from_is_not_continued(ws):
    other = ws.account_add("claude", "Second")["id"]
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    take(ws, dict(m, version=2, account=other))
    assert "no longer runs on" in refused(ws.controller.continue_task, {"task": got["task"]})


def test_a_session_whose_record_its_programs_spoiled_stays_open_and_end_all_stops_it(ws):
    m = on(ws, end_all=0)
    got = start(ws, plan(ws, m))
    with open(os.path.join(ws._dir(got["session"]), "session.json"), "w") as fh:
        fh.write("[]")
    ws.controller.refresh()
    t = ws.controller.record["tasks"][got["task"]]
    assert t["state"] == "starting" and ws.controller.busy() is True
    take(ws, dict(m, version=2, end_all=1))
    assert _Term.seen[-1].running is False and t["state"] == "stopped" and ws.controller.record["end_all"] == 1


def test_end_all_marks_stopped_only_what_stopped_and_keeps_its_count_until_it_all_did(ws, monkeypatch):
    m = on(ws, end_all=0)
    got = start(ws, plan(ws, m))

    async def stuck(self):
        pass                                               # a program that does not stop
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_Term, "stop", stuck)
        take(ws, dict(m, version=2, end_all=1))
    t = ws.controller.record["tasks"][got["task"]]
    assert t["state"] != "stopped" and ws.controller.record["end_all"] == 0
    take(ws, dict(m, version=3, end_all=1))                 # the same count: tried again
    assert t["state"] == "stopped" and ws.controller.record["end_all"] == 1


def test_the_owner_s_words_go_in_pieces_and_what_changed_goes_first(ws, monkeypatch):
    m = on(ws, state="off")
    decisions = [{"id": uid(), "n": i + 1, "question": None, "text": "x" * 1900 + str(i), "at": NOW}
                 for i in range(40)]
    take(ws, dict(m, version=2, state="off", decisions=decisions))
    _, _, sent = _watch_chat(ws, monkeypatch)
    take(ws, dict(m, version=3, state="on", decisions=decisions))
    _primed(ws, m)
    ws.controller.wakes["f" * 32] = {"id": "f" * 32, "state": "idle", "waits_until": None, "ended_at": None}
    while True:
        before = len(sent)
        asyncio.run(ws.controller.keep_chat())
        if len(sent) == before:
            break
    assert len(sent) > 1 and all(len(text.encode("utf-8")) <= ctl.MESSAGE_MAX for text in sent)
    assert sent[0].startswith(ctl.CHANGED)
    given = [d["id"] for text in sent for d in json.loads(text.rsplit(ctl.NEW_FROM_OWNER + "\n", 1)[1])["decisions"]]
    assert sorted(given) == sorted(d["id"] for d in decisions) and ws.controller.pending == []


def test_the_chat_is_told_to_go_on_once_its_own_window_reset(ws, monkeypatch):
    now = int(time.time())
    m = on(ws)
    _, _, sent = _watch_chat(ws, monkeypatch)
    _primed(ws, m)
    ws.controller.rate_limited({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": now + 600})
    asyncio.run(ws.controller.keep_chat())
    assert sent == []
    ws.controller.rates["five_hour"]["resets_at"] = now - 1
    asyncio.run(ws.controller.keep_chat())
    assert sent == [ctl.GO_ON]
    asyncio.run(ws.controller.keep_chat())
    assert sent == [ctl.GO_ON]                              # once


def test_the_reset_is_the_furthest_spent_window_of_both_lines(ws, monkeypatch):
    m = on(ws)
    got = start(ws, plan(ws, m))
    _status(ws, got["session"], 97, NOW + 3600)            # its own line, from before the limit
    asked = []
    monkeypatch.setattr(ws.controller, "_account_status", lambda account: asked.append(account) or {
        "at": NOW, "five_hour": {"used_percentage": 100, "resets_at": NOW + 1800}})
    t = ws.controller.record["tasks"][got["task"]]
    assert ws.controller._reset_of(t, NOW) == NOW + 1800 and asked == ["default"]


def test_the_machine_is_kept_up_by_what_runs_or_waits_for_a_known_reset_and_nothing_else(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    t = ws.controller.record["tasks"][got["task"]]
    for state, waits, busy in (("idle", None, False), ("waiting", None, False),
                               ("waiting", int(time.time()) + 60, True),
                               ("working", None, True)):
        t["state"], t["waits_until"] = state, waits
        assert ws.controller.busy() is busy, (state, waits)


def test_a_question_about_a_requirement_the_app_did_not_take_is_refused(ws):
    m = on(ws)
    requirement = plan(ws, m)
    read_through(ws)
    take(ws, dict(m, version=2))
    assert "does not list that requirement" in refused(ws.controller.ask_owner, {"text": "Ok?",
                                                                               "requirement": requirement})


def test_a_window_reset_and_not_read_since_is_not_measured(ws, monkeypatch):
    c = _paced(ws, 1, 40, 0.0, -60)                        # the newest line read the window before its reset
    ok, why, _ = c.pace(NOW)
    assert ok is False and "reset and has not been read since" in why


def test_a_continue_whose_effort_is_no_longer_offered_or_that_went_on_meanwhile_is_refused(ws, monkeypatch):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ws, "start_effort", lambda sid, effort=None, claude=None: (
            None, "Claude Code no longer offers effort max: the session started at its default."))
        assert "no longer offers effort max: it would start at its default" in refused(
            ws.controller.continue_task, {"task": got["task"]})
    _floor_then(ws, monkeypatch, lambda: _limit(ws, got["session"], "quota_auto_resume_fired",
                                                 at=int(time.time()) + 2))
    assert "went on" in refused(ws.controller.continue_task, {"task": got["task"]})
    assert actions(ws, "continue") == []


def test_a_task_being_started_when_the_workspace_stopped_is_ended(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    ws.controller.record["tasks"][got["task"]]["launching"] = True
    with ws.controller._lock:
        ws.controller._save()
    again = ctl.Controller(ws)
    t = again.record["tasks"][got["task"]]
    assert t["state"] == "stopped" and "launching" not in t


def test_a_record_that_cannot_be_taken_is_put_aside(ws):
    with open(ws.controller.path, "w") as fh:
        fh.write("{}")
    ctl.Controller(ws)
    assert open(ws.controller.path + ".unread").read() == "{}"


def test_what_the_app_had_read_never_goes_back(ws):
    m = on(ws)
    ws.controller.record["mirror_acked"] = 10
    ws.controller.served = 0
    take(ws, dict(m, version=2))
    assert ws.controller.record["mirror_acked"] == 10


def test_a_start_takes_a_model_named_as_the_owner_s_side_names_it(ws):
    m = on(ws, models=["us.anthropic.Claude-Opus:0"])
    start(ws, plan(ws, m))
    assert "--model=us.anthropic.Claude-Opus:0" in _Term.seen[-1].argv


# ---- the pacing ------------------------------------------------------------------------------------------------------

def _paced(w, n, used, speed, resets_in, minutes=15, window="five_hour"):
    """n of the controller's sessions running, the window `used` with `speed` per minute over `minutes` samples."""
    for _ in range(n):
        _task(w, "working")
    c = w.controller
    reset = NOW + resets_in
    status = {"at": NOW, window: {"used_percentage": used, "resets_at": reset}}
    c._newest = lambda: status
    c.samples.clear()
    for i in range(minutes):
        c.samples.append({"at": NOW - 60 * (minutes - 1 - i), "ran": n,
                          window: {"used_percentage": used - speed * (minutes - 1 - i), "resets_at": reset}})
    return c


def test_with_none_running_the_first_starts_whatever_the_speed(ws):
    c = _paced(ws, 0, 99, 5.0, 3600)
    assert c.pace(NOW)[0] is True


@pytest.mark.parametrize("n,used,speed,resets_in,allowed", [
    # (100 - used) / (s * (n + 1) / n) against the minutes to the reset
    (1, 40, 1.0, 30 * 60, True),         # 60 / 2 = 30 minutes, 30 to the reset: just allowed
    (1, 40, 1.0, 31 * 60, False),        # 30 < 31
    (2, 70, 0.5, 40 * 60, True),         # 30 / 0.75 = 40
    (2, 70, 0.5, 41 * 60, False),
    (1, 99, 0.0, 300 * 60, True),        # no measured burn
])
def test_one_more_starts_only_when_the_window_lasts_to_its_reset(ws, n, used, speed, resets_in, allowed):
    c = _paced(ws, n, used, speed, resets_in)
    assert c.pace(NOW)[0] is allowed


def test_the_seven_day_window_is_read_the_same_way(ws):
    c = _paced(ws, 1, 40, 1.0, 31 * 60, window="seven_day")
    ok, why, _ = c.pace(NOW)
    assert ok is False and "seven-day window" in why


def test_samples_of_an_earlier_window_are_not_counted(ws):
    c = _paced(ws, 1, 40, 1.0, 31 * 60)
    assert c.pace(NOW)[0] is False
    # the same rise, but all of it in the window before the present one, which reset since: the present window's two
    # samples saw no change
    samples = list(c.samples)
    for s in samples[:-2]:
        s["five_hour"] = dict(s["five_hour"], resets_at=NOW - 3600)
    for s in samples[-2:]:
        s["five_hour"] = dict(s["five_hour"], used_percentage=40)
    assert c.pace(NOW)[0] is True


def test_the_second_session_waits_for_a_speed_the_first_gave(ws):
    c = _paced(ws, 1, 40, 1.0, 300 * 60, minutes=1)
    ok, why, _ = c.pace(NOW)
    assert ok is False and "speed is not measured yet" in why
    c.samples.append({"at": NOW + 60, "ran": 1, "five_hour": {"used_percentage": 40, "resets_at": NOW + 300 * 60}})
    assert c.pace(NOW)[0] is True


def test_a_sample_is_taken_once_a_minute_however_often_the_loop_goes_round(ws, monkeypatch):
    c = ws.controller
    status = {"at": NOW, "five_hour": {"used_percentage": 10, "resets_at": NOW + 3600}}
    monkeypatch.setattr(c, "_newest", lambda: status)
    for i in range(5):
        c.sample(NOW + i)
    c.sample(NOW + 60)
    assert [s["at"] for s in c.samples] == [NOW, NOW + 60]


@pytest.mark.parametrize("event,allowed", [
    ({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": NOW + 600}, False),
    ({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": NOW - 600}, True),
    ({"status": "allowed_warning", "rate_limit_type": "seven_day", "resets_at": NOW + 600}, False),
    ({"status": "allowed_warning", "rate_limit_type": "seven_day_opus", "resets_at": None}, False),
    ({"status": "allowed_warning", "rate_limit_type": "five_hour", "resets_at": NOW + 600}, True),
    ({"status": "allowed", "rate_limit_type": "seven_day", "resets_at": NOW + 600}, True),
])
def test_a_refused_window_and_the_weekly_reserve_stop_new_starts(ws, event, allowed):
    c = ws.controller
    c.rate_limited(event)
    assert c.pace(NOW)[0] is allowed


# ---- the controller's settings and limit hooks -----------------------------------------------------------------------

@pytest.mark.parametrize("version,events,auto", [
    ((2, 1, 77), set(), False), ((2, 1, 78), {"StopFailure"}, False), ((2, 1, 233), {"StopFailure"}, False),
    ((2, 1, 234), {"StopFailure", "Notification"}, True), (None, set(), False),
])
def test_a_controller_s_session_is_given_what_its_claude_takes(version, events, auto):
    got = settings_made.controller_settings("/py", "/here", version)
    assert set(got.get("hooks", {})) == events
    assert ("autoContinueAtUsageLimit" in got) is auto and got.get("autoContinueAtUsageLimit", False) is False
    for groups in got.get("hooks", {}).values():
        for g in groups:
            assert g["hooks"][0]["args"][-1] == "--limit" and g["hooks"][0]["async"] is True


def test_with_map_adds_the_controller_s_after_what_was_given_and_only_for_the_controller():
    given = {"hooks": {"Notification": [{"hooks": [{"type": "command", "command": "mine"}]}]}, "model": "x"}
    mine = settings_made.with_map(given, "/py", "/here", (2, 1, 274), controller=True)
    assert mine["hooks"]["Notification"][0] == given["hooks"]["Notification"][0]
    assert [g.get("matcher") for g in mine["hooks"]["Notification"][1:]] == list(settings_made.QUOTA_KINDS)
    assert mine["autoContinueAtUsageLimit"] is False and mine["model"] == "x"
    assert settings_made.with_map(given, "/py", "/here", (2, 1, 274)) == settings_made.with_map(
        given, "/py", "/here", (2, 1, 274), controller=False)
    assert "autoContinueAtUsageLimit" not in settings_made.with_map(given, "/py", "/here", (2, 1, 274))


@pytest.mark.parametrize("event,kind", [
    ({"hook_event_name": "StopFailure", "error": "rate_limit", "last_assistant_message": "API Error"}, "rate_limit"),
    ({"hook_event_name": "StopFailure", "error": "server_error"}, None),
    ({"hook_event_name": "Notification", "notification_type": "quota_auto_resume_fired"}, "quota_auto_resume_fired"),
    ({"hook_event_name": "Notification", "notification_type": "quota_auto_resume_stale"}, "quota_auto_resume_stale"),
    ({"hook_event_name": "Notification", "notification_type": "idle_prompt"}, None),
    ({"hook_event_name": "Stop", "error": "rate_limit"}, None),
])
def test_the_limit_hook_keeps_only_a_limit_s_kind_and_when(tmp_path, event, kind):
    assert activity.limit_record(event, str(tmp_path)) is (kind is not None)
    path = tmp_path / ctl.LIMITS
    lines = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    assert [line["kind"] for line in lines] == ([kind] if kind else [])
    assert all(set(line) == {"at", "kind"} for line in lines)


def test_the_limit_hook_writes_nothing_for_a_program_in_a_session_s_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("PP_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("PP_SHELL", "1")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(
        {"hook_event_name": "StopFailure", "error": "rate_limit"})))
    assert activity.main(["--limit"]) == 0
    assert not (tmp_path / ctl.LIMITS).exists()


def test_the_gate_refuses_a_session_s_call_that_names_the_controller_s_record(tmp_path):
    from proofpane_workspace import gate
    home = str(tmp_path)
    for name, given in (("Read", {"file_path": "/home/ws/workspace/controller/controller-record.json"}),
                        ("Bash", {"command": "cat controller/controller-record.json"}),
                        ("Bash", {"command": "cat controller/controller-rec''ord.json"}),
                        ("Write", {"file_path": "controller-record.json", "content": "{}"})):
        assert gate.refusal({"tool_name": name, "tool_input": given}, home, {}) == gate.CONTROLLER_RECORD, given
    assert gate.refusal({"tool_name": "Bash", "tool_input": {"command": "cat controller-notes.json"}}, home, {}) is None


def test_a_record_s_folder_that_is_a_link_is_neither_read_nor_written_through(ws, tmp_path, capsys):
    on(ws)
    elsewhere = tmp_path / "elsewhere"
    os.rename(ws.controller.dir, elsewhere)
    os.symlink(elsewhere, ws.controller.dir)
    again = ctl.Controller(ws)                          # the workspace starts all the same
    assert again.record["seq"] == 0                       # nothing read through the link
    err = capsys.readouterr().err
    assert "could not be read" in err and "could not be written" in err
    before = os.listdir(elsewhere)
    again._save()
    assert os.listdir(elsewhere) == before                # nothing written through it either
    assert capsys.readouterr().err == ""                  # said once


def test_the_machine_stays_up_while_the_controller_is_busy():
    from proofpane_workspace.__main__ import exit_when_idle

    class Server:
        should_exit = False

    class Agents:
        ended_at = time.monotonic() - 999

        def live_runs(self):
            return 0

        def queued(self):
            return 0

    class Controller:
        def busy(self):
            return True

    class Space:
        last_activity = time.monotonic() - 999
        page_seen = False
        agents = Agents()
        controller = Controller()

        def touch(self):
            self.last_activity = time.monotonic()

    server, space = Server(), Space()

    async def watch_briefly():
        task = asyncio.ensure_future(exit_when_idle(server, space, idle=5, check=0.01, after_runs=1))
        await asyncio.sleep(0.2)
        assert not task.done()
        task.cancel()
    asyncio.run(watch_briefly())
    assert server.should_exit is False
    Controller.busy = lambda self: False
    asyncio.run(asyncio.wait_for(exit_when_idle(server, Space(), idle=5, check=0.01, after_runs=1), 5))
    assert server.should_exit is True


def test_the_controller_is_busy_while_on_with_a_session_starting_and_not_otherwise(ws):
    m = on(ws)
    assert ws.controller.busy() is False
    got = start(ws, plan(ws, m))
    assert ws.controller.busy() is True
    take(ws, dict(m, version=2, state="off", account=None))
    assert ws.controller.busy() is False
    take(ws, dict(m, version=3))
    asyncio.run(ws.controller.stop_task({"task": got["task"]}))
    assert ws.controller.busy() is False


def test_the_app_s_mirror_of_the_controller_is_no_page_s_activity_nor_the_middleware_s():
    from proofpane_workspace.agent import _Secret
    seen = {"activity": 0, "page": 0}

    async def app(scope, receive, send):
        pass
    gate_app = _Secret(app, "s" * 40, on_activity=lambda: seen.__setitem__("activity", seen["activity"] + 1),
                       on_page=lambda: seen.__setitem__("page", seen["page"] + 1))
    for path in ("/controller", "/sessions"):
        scope = {"type": "http", "path": path, "method": "PUT",
                 "headers": [(b"authorization", b"Bearer " + b"s" * 40)]}
        asyncio.run(gate_app(scope, None, None))
    assert seen == {"activity": 1, "page": 1}          # the /sessions one: the mirror's route says its own


# ---- the chat's options (Workspace.chat) -----------------------------------------------------------------------------

def test_a_controller_chat_is_given_its_six_tools_and_no_built_in_one(ws):
    class Chat:
        session_id, env = "0123456789ab", {"PP_SESSION_ID": "0123456789ab"}
    c = Chat()
    with ws.controller._lock:
        ws.controller.record["session"] = c.session_id
    ws.controller.configure(c)
    assert c.base_tools == [] and c.setting_sources == [] and c.send_most == ctl.MESSAGE_MAX
    # marked for the gate, through the wrapper that passes only the names it keeps
    from proofpane_workspace import chat as chats
    assert c.env == {"PP_SESSION_ID": "0123456789ab", "PP_CONTROLLER_CHAT": "1"}
    assert "PP_CONTROLLER_CHAT" in chats.WRAPPER_NAMES and Chat.env == {"PP_SESSION_ID": "0123456789ab"}
    assert c.mcp_plan() == {"off": [], "strict": True, "servers": []}
    own = c.own_tools({})
    assert own["allowed_tools"] == [f"mcp__proofpane__{t}" for t in ctl.TOOLS]
    assert list(own["mcp_servers"]) == ["proofpane"]


def test_a_chat_no_longer_the_controller_s_has_its_tools_refused_and_the_controller_s_answered(ws, capsys):
    on(ws)
    c = ws.controller
    with c._lock:
        c.record["session"] = "0123456789ab"
    out = asyncio.run(c._tool_call("ba9876543210", c.ask_owner, {"text": "Hi?"}, True))
    assert out["is_error"] and "no longer the Manager's" in out["content"][0]["text"] and c.record["questions"] == {}
    out = asyncio.run(c._tool_call("0123456789ab", c.ask_owner, {"text": "Hi?"}, True))
    assert "is_error" not in out and [json.loads(out["content"][0]["text"])["question"]] == list(c.record["questions"])

    def broken(args):
        raise KeyError("x")
    out = asyncio.run(c._tool_call("0123456789ab", broken, {}, True))
    assert out["is_error"] and out["content"][0]["text"] == "The workspace failed (KeyError)."
    assert "a tool failed: KeyError" in capsys.readouterr().err


# ---- the pacing asked again; what a session's own files can steer; paid usage ----------------------------------------

def test_a_start_the_pacing_refused_is_told_to_the_chat_once_the_pacing_allows_one(ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, cap=3, packet_b64=b64, packet_digest=digest)
    requirement = plan(ws, m)
    c = ws.controller
    allows, asked = [], []

    def pace(now=None, model=None):
        asked.append(model)
        return (True, None, {}) if allows else (False, "The account's speed is not measured yet.", {})
    monkeypatch.setattr(c, "pace", pace)
    monkeypatch.setattr(c, "refresh", lambda now=None, heads=False: None)
    _, _, sent = _watch_chat(ws, monkeypatch)
    _primed(ws, m)
    c.tick()
    assert asked == []                                  # nothing refused yet: the pacing is not asked
    assert "not measured yet" in refused(c.start_task, {"requirement": requirement, "repo": "demo", "task": "Two.",
                                                        "model": "sonnet"})
    c.tick()
    asyncio.run(c.keep_chat())
    assert sent == [] and asked[-1] == "sonnet"         # refused still: nothing said; asked for the start's model
    allows.append(True)
    c.tick()
    asyncio.run(c.keep_chat())
    asyncio.run(c.keep_chat())
    assert sent == [ctl.PACED]                          # told once, and asked no more
    calls = len(asked)
    c.tick()
    assert len(asked) == calls


def test_a_continue_the_pacing_refused_is_asked_again_too(ws, monkeypatch):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    monkeypatch.setattr(ws.controller, "pace", lambda now=None, model=None: (False, "Spent.", {}))
    assert refused(ws.controller.continue_task, {"task": got["task"]}) == "Spent."
    assert ws.controller.paced is True and ws.controller.paced_model == "opus"


def _line(w, sid, at, **windows):
    with open(os.path.join(w._dir(sid), "status.json"), "w") as fh:
        json.dump(dict({"at": at}, **windows), fh)


def test_a_status_line_dated_ahead_of_the_clock_or_out_of_its_range_is_not_the_account_s(ws):
    on(ws)
    now = int(time.time())
    genuine = {"used_percentage": 40, "resets_at": now + 3600}
    _line(ws, _task(ws, "working")["session"], now, five_hour=genuine)
    # what a session's programs could write: dated far ahead (and the window "reset", which held every start),
    # used past 100, a reset further ahead than the window lasts
    _line(ws, _task(ws, "working")["session"], 2 ** 40, five_hour={"used_percentage": 0, "resets_at": now - 60})
    _line(ws, _task(ws, "working")["session"], now + 1, five_hour={"used_percentage": 1000, "resets_at": now + 60})
    _line(ws, _task(ws, "working")["session"], now + 2, five_hour={"used_percentage": 50,
                                                                   "resets_at": now + 5 * 3600 + 120})
    _line(ws, _task(ws, "working")["session"], now + 3, seven_day={"used_percentage": 50,
                                                                   "resets_at": now + 7 * 86400 + 120})
    assert ws.controller._newest()["five_hour"] == genuine
    late = {"used_percentage": 41, "resets_at": now + 3600}
    _line(ws, _task(ws, "working")["session"], now + 4, five_hour=late, seven_day={"used_percentage": 3,
                                                                                    "resets_at": now + 86400})
    assert ws.controller._newest()["five_hour"] == late          # one within its bounds, newer: the account's


def test_a_session_s_own_line_out_of_its_range_sets_no_reset_and_keeps_nothing_up(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    now = int(time.time())
    for at, used, resets in ((2 ** 40, 100, now + 3600), (now, 100, now + 30 * 86400), (now, 1000, now + 3600)):
        _line(ws, got["session"], at, five_hour={"used_percentage": used, "resets_at": resets})
        t = ws.controller.record["tasks"][got["task"]]
        assert ws.controller._reset_of(t, now) is None, (at, used, resets)
    _limit(ws, got["session"], "rate_limit", at=int(time.time()) + 1)
    ws.controller.refresh()
    t = ws.controller.record["tasks"][got["task"]]
    assert (t["state"], t["waits_until"]) == ("waiting", None) and ws.controller.busy() is False


def test_a_limit_or_an_activity_line_dated_ahead_of_the_clock_is_not_taken(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    t = ws.controller.record["tasks"][got["task"]]
    _limit(ws, got["session"], "rate_limit", at=2 ** 40)
    _activity(ws, got["session"], "idle", at=2 ** 40)
    ws.controller.refresh()
    assert t["state"] == "starting"                     # neither a limit nor its hooks' word taken
    _limit(ws, got["session"], "rate_limit", at=int(time.time()) + 1)
    ws.controller.refresh()
    assert t["state"] == "waiting"                      # a line in time after it is: none hides it


def test_a_mirror_the_workspace_takes_is_activity_and_one_it_refuses_is_not(ws):
    import httpx
    from proofpane_workspace import agent
    from workspace_fixtures import SECRET

    async def put(body):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(ws, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            return await c.put("/controller", json=body)
    ws.last_activity = 0
    assert asyncio.run(put(mirror(account="0123456789ab"))).status_code == 412     # an account it lacks
    assert asyncio.run(put(mirror(version="2"))).status_code == 400
    assert ws.last_activity == 0
    answer = asyncio.run(put(mirror(version=2)))
    assert (answer.status_code, answer.json()) == (200, {"held": 2}) and ws.last_activity > 0
    ws.last_activity = 0
    assert asyncio.run(put(mirror(version=2))).status_code == 200      # the same side again: held already
    assert ws.last_activity == 0


def test_the_owner_s_side_is_read_up_to_its_bytes_and_refused_past_them(ws):
    # the app sends at most MIRROR_MAX bytes of the mirror (controller.MIRROR_MAX on both sides); the workspace reads
    # that much and refuses one byte past it, before it reads a field
    import httpx
    from proofpane_workspace import agent
    from workspace_fixtures import SECRET

    async def put(raw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(ws, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            return await c.put("/controller", content=raw, headers={"Content-Type": "application/json"})
    assert ctl.MIRROR_MAX == app_controller.MIRROR_MAX
    raw = json.dumps(mirror(version=2)).encode()
    at = raw + b" " * (ctl.MIRROR_MAX - len(raw))     # the same side, spaced out to the most the app sends
    answer = asyncio.run(put(at))
    assert (answer.status_code, answer.json()) == (200, {"held": 2})
    past = asyncio.run(put(at + b" "))
    assert (past.status_code, past.json()) == (400, {"detail": "The request is too large."})


def _spoil(w, sid):
    with open(os.path.join(w._dir(sid), "session.json"), "w") as fh:
        fh.write("[]")                                  # a record a session's program made unreadable


def test_a_chat_whose_record_a_session_spoiled_is_stopped_before_a_fresh_one_and_not_replaced_each_round(
        ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, packet_b64=b64, packet_digest=digest)
    c = ws.controller
    monkeypatch.setattr(c, "cannot_run", lambda given: None)
    made = []

    def make(given, sid):
        os.makedirs(os.path.join(ws._dir(sid), "work"))
        ws._write({"id": sid, "repo": None, "program": "chat", "task": ctl.CHAT_TITLE, "base": None, "branch": None,
                   "created_at": NOW, "finished": None})
        ws.chats[sid] = _Chat(sid)
        made.append(sid)

    async def prime(given):
        with c._lock:
            c.record["chat"]["primed"] = True
    monkeypatch.setattr(c, "_make_chat", make)
    monkeypatch.setattr(c, "_prime", prime)
    sid = _primed(ws, m, state="running")
    old = ws.chats[sid]
    old.client = object()                               # its program live, in a turn
    _spoil(ws, sid)
    asyncio.run(c.keep_chat())
    assert made == [] and old.client is not None        # once its turn has ended
    old.state = "idle"
    asyncio.run(c.keep_chat())
    assert len(made) == 1 and old.client is None and c.record["session"] == made[0]
    _spoil(ws, made[0])                                 # and again, at once: not replaced each round
    asyncio.run(c.keep_chat())
    assert len(made) == 1
    c.spoiled_at -= ctl.RETRY + 1
    asyncio.run(c.keep_chat())
    assert len(made) == 2 and c.record["session"] == made[1]


def test_a_change_of_account_stops_the_old_chat_though_its_record_is_spoiled(ws, monkeypatch):
    m = on(ws)
    _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m)
    ws.chats[sid].client = object()
    _spoil(ws, sid)
    with ws.controller._lock:
        ws.controller.record["mirror"]["account"] = "0123456789ab"
    asyncio.run(ws.controller.keep_chat())
    assert ws.chats[sid].client is None and ws.controller.record["session"] is None


def test_paid_usage_holds_new_starts_until_the_window_s_reset_whatever_allowed_event_comes_after(ws):
    on(ws)
    c = ws.controller
    now = int(time.time())
    c.rate_limited({"status": "allowed", "rate_limit_type": "five_hour", "resets_at": now + 3600,
                    "is_using_overage": True})
    ok, why, numbers = c.pace(now)
    assert ok is False and "paid usage" in why and numbers["rate_limits"]["five_hour"]["paid"] is True
    assert c.cut_off                                    # nothing more sent to the chat before the reset
    c.rate_limited({"status": "allowed", "rate_limit_type": "five_hour", "resets_at": now + 3600})
    assert c.pace(now)[0] is False                      # an allowed event after it lifts nothing
    assert c.pace(now + 3601)[0] is True                # its reset does
    c.rate_limited({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": now + 600})
    assert c.pace(now)[0] is False and "paid usage" not in c.pace(now)[1]    # a refusal changes it


def test_paid_usage_past_no_window_of_the_plan_s_holds_the_five_hour_one_five_hours(ws):
    on(ws)
    c = ws.controller
    now = int(time.time())
    c.rate_limited({"status": "allowed", "rate_limit_type": "overage", "resets_at": now + 30 * 86400,
                    "is_using_overage": True})
    ok, why, _ = c.pace(now)
    assert ok is False and "five_hour" in why
    assert c.pace(now + ctl.UNTIMED + 120)[0] is True


@pytest.mark.parametrize("text", ["\u200b\ufe0f", "\n\ufe0f", "a\u200b\ufe0f", "\ufe0f\ufe0f", "x\n\ufe0fy",
                                  "\u200b\ufe0f\ufe0f", "\u3000\ufe0e", "a\u0336b\u200b\u0336", "\u2028\ufe0f a"])
def test_a_model_s_line_is_one_the_app_keeps_as_it_is(text):
    line, _ = ctl.model_line(text)
    assert app_tasks.model_text(line)[0] == line and line == " ".join(line.split())


def test_a_title_or_a_question_the_app_would_make_blank_is_refused_here(ws):
    m = on(ws)
    assert "The title is blank" in refused(plan, ws, m, title="\u200b\ufe0f")
    assert "A question is blank" in refused(ws.controller.ask_owner, {"text": "\n\ufe0f"})
    assert actions(ws) == []


def test_the_controller_s_own_tools_may_carry_what_a_session_s_call_may_not_name(tmp_path):
    from proofpane_workspace import gate
    home = str(tmp_path)
    words = ("Fix how controller-record.json is kept; leave git-places.json and ~/.claude/.credentials.json "
             "alone.")
    assert gate.CONTROLLER_TOOLS == {f"mcp__proofpane__{t}" for t in ctl.TOOLS}
    chat = {"PP_CONTROLLER_CHAT": "1"}                  # the controller's chat, as configure marks it
    for tool in sorted(gate.CONTROLLER_TOOLS):
        given = {"tool_name": tool, "tool_input": {"words": words, "text": words}}
        assert gate.refusal(given, home, chat) is None
        assert gate.refusal(given, home, {}) is not None, tool      # the same names in another session: held
    for tool in ("mcp__proofpane__propose_change", "mcp__other__plan_requirement", "Read"):
        assert gate.refusal({"tool_name": tool, "tool_input": {"words": words}}, home, chat) is not None, tool


def test_a_record_too_large_or_no_regular_file_is_put_aside_not_written_over(ws, monkeypatch, capsys):
    import stat
    with open(ws.controller.path, "w") as fh:
        fh.write("x" * 100)
    monkeypatch.setattr(ctl, "RECORD_MAX", 10)
    ctl.Controller(ws)
    assert open(ws.controller.path + ".unread").read() == "x" * 100
    monkeypatch.setattr(ctl, "RECORD_MAX", 64 * 1024 * 1024)
    os.remove(ws.controller.path)
    os.mkfifo(ws.controller.path)
    ctl.Controller(ws)
    assert stat.S_ISFIFO(os.lstat(ws.controller.path + ".unread").st_mode)
    assert stat.S_ISREG(os.lstat(ws.controller.path).st_mode)
    assert capsys.readouterr().err.count("put aside as controller-record.json.unread") == 2


def test_a_start_cut_short_between_its_launch_and_its_record_is_recorded_at_the_restart(ws, monkeypatch):
    m = on(ws)
    requirement = plan(ws, m)

    class Crash(BaseException):
        """The agent stopping there: neither the start's action nor its undoing written."""

    def launch(sid, argv=None, **kwargs):
        raise Crash()
    monkeypatch.setattr(ws, "launch", launch)
    with pytest.raises(Crash):
        start(ws, requirement)
    assert actions(ws, "start") == []
    again = ctl.Controller(ws)
    [t] = again.record["tasks"].values()
    [a] = [x for x in again.page(0)["actions"] if x["kind"] == "start"]
    assert (a["task"], a["session"], a["requirement"], a["at"], a["repo"], a["branch"], a["account"], a["program"],
            a["model"], a["effort"]) == (t["id"], t["session"], requirement, t["started_at"], "demo",
                                         f"pp/{t['session']}", "default", "claude", "opus", "max")
    assert t["state"] == "stopped" and "start" not in t and "launching" not in t
    assert again.page(0)["tasks"] == [{"id": t["id"], "state": "stopped", "waits_until": None,
                                       "ended_at": t["ended_at"], "checks": None, "why": None, "checking": False,
                                       "needs_owner": False, "changed": t["state_seq"], "stopped_by": None}]
    # the start before its end in the record's order: a page cut between them gives the app the start first
    [kept] = [x for x in again.record["actions"] if x["kind"] == "start"]
    assert kept["seq"] < t["seq"]


def test_a_removed_session_s_model_is_forgotten(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    assert ws._models[got["session"]] == "opus"
    asyncio.run(ws.remove(got["session"]))
    assert got["session"] not in ws._models


def test_end_all_s_stops_not_all_done_say_no_side_is_held_until_they_are(ws, monkeypatch):
    m = on(ws, end_all=0)
    start(ws, plan(ws, m))

    async def stuck(self):
        pass
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_Term, "stop", stuck)
        assert take(ws, dict(m, version=2, end_all=1)) == {"held": 2}
    assert page(ws)["held"] is None                     # the app sends it again, and the rise is seen again
    take(ws, dict(m, version=2, end_all=1))
    assert page(ws)["held"] == 2 and ws.controller.record["end_all"] == 1


def test_a_window_a_line_says_is_spent_holds_the_first_start_too(ws):
    c = ws.controller
    now = int(time.time())
    c._newest = lambda: {"at": now, "five_hour": {"used_percentage": 100, "resets_at": now + 600}}
    ok, why, _ = c.pace(now)
    assert ok is False and "five-hour window is spent" in why
    assert c.pace(now + 601)[0] is True                 # its reset passed: the first starts


def test_a_model_s_own_weekly_window_holds_that_model_s_starts_alone(ws):
    on(ws)
    c = ws.controller
    now = int(time.time())
    c.rate_limited({"status": "rejected", "rate_limit_type": "seven_day_opus", "resets_at": now + 600})
    assert [c.pace(now, model)[0] for model in ("opus", "claude-opus-4-1", "sonnet", None)] == [
        False, False, True, False]
    c.rate_limited({"status": "allowed_warning", "rate_limit_type": "seven_day_sonnet", "resets_at": now + 600})
    assert c.pace(now, "sonnet")[0] is False


def test_a_task_on_an_account_the_controller_moved_from_keeps_nothing_up_and_reads_its_own_account(ws):
    now = int(time.time())
    other = ws.account_add("claude", "Second")["id"]
    m = on(ws)
    got, t = _waiting(ws, m, now + 3600)
    assert ws.controller.busy() is True
    take(ws, dict(m, version=2, account=other))
    assert ws.controller.busy() is False                # its continue would be refused: nothing to wait up for
    ws.controller.rate_limited({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": now + 99})
    assert ws.controller._reset_of(t, now) == now + 3600     # the new account's refusal is not this task's


def test_an_ended_task_is_kept_while_its_session_is_in_the_workspace(ws):
    on(ws)
    t = _task(ws, "done", ended_at=NOW - ctl.KEPT_ENDED - 60)
    ws.controller.acked = ws.controller.record["seq"]
    ws.controller.refresh()
    assert t["id"] in ws.controller.record["tasks"]     # its later start: still the controller's session's
    shutil.rmtree(ws._dir(t["session"]))
    ws.controller.refresh()
    assert t["id"] not in ws.controller.record["tasks"]


def test_part_one_says_how_it_is_escaped_where_the_request_is_gone(ws):
    m = on(ws)
    requirement = plan(ws, m)
    prompt = ws.controller._first_prompt(dict(m, requests=[]), requirement, "Do it.", "demo")
    first = prompt.split("\n\nPart 2")[0]
    assert "as above" not in first and "a backslash is doubled" in first
    assert first.endswith("Make the README say hello.")


def test_the_controller_s_chat_is_given_the_core_plugin_never_the_daemon_s_companion(ws, monkeypatch, tmp_path):
    from proofpane_workspace import chat as chats, core_plugin as core
    monkeypatch.setattr(ws, "core_plugins", lambda folder: [core.PLUGIN, core.DAEMON_PLUGIN])
    monkeypatch.setattr(ws, "_claude", lambda: "/no/such/claude")
    monkeypatch.setattr(ws, "claude_floor_refusal", lambda claude, cwd, env: None)
    ws._chat_wrapper = str(tmp_path / "chat-claude")
    with ws.controller._lock:
        ws.controller.record["session"] = "0123456789ab"
    for sid, want in (("0123456789ab", [core.PLUGIN]), ("ba9876543210", [core.PLUGIN, core.DAEMON_PLUGIN])):
        c = chats.Chat(sid, str(tmp_path / "s"), str(tmp_path), {}, ws._chat_wrapper)
        assert ws._chat_start(c) is None and c.plugins == want, sid


# ---- the chat made as the controller's at once; idle and waiting on the owner; the owner's changes told; the sessions'
# undated lines; a fresh record's numbers ------------------------------------------------------------------------------

def test_a_list_read_while_the_controller_s_chat_is_made_makes_it_as_the_controller_s(ws, monkeypatch):
    b64, digest = packet()
    on(ws, packet_b64=b64, packet_digest=digest)
    c = ws.controller
    monkeypatch.setattr(c, "cannot_run", lambda given: None)
    real, seen = c._make_chat, []

    def make(given, sid):
        real(given, sid)
        ws.sessions()                   # the page's list, read while the chat is made, makes its Chat
        seen.append(ws.chats.get(sid))

    async def prime(given):
        pass
    monkeypatch.setattr(c, "_make_chat", make)
    monkeypatch.setattr(c, "_prime", prime)
    asyncio.run(c.keep_chat())
    [made] = seen
    assert made is not None and made is ws.chats[c.record["session"]]
    assert (made.base_tools, made.setting_sources, made.send_most) == ([], [], ctl.MESSAGE_MAX)
    assert made.env["PP_CONTROLLER_CHAT"] == "1" and made.own_tools({})["allowed_tools"] == [
        f"mcp__proofpane__{t}" for t in ctl.TOOLS]


def test_a_session_waiting_on_its_owner_or_at_the_idle_prompt_is_idle_and_counts_for_nothing(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    t = ws.controller.record["tasks"][got["task"]]
    folder = ws._dir(got["session"])
    activity.record("idle", {"hook_event_name": "Stop"}, folder)
    activity.record("waiting", {"hook_event_name": "Notification", "notification_type": "idle_prompt",
                                "message": "Claude is waiting for your input"}, folder)
    ws.controller.refresh()
    assert (t["state"], ws.controller.busy(), ws.controller._running()) == ("idle", False, 0)
    activity.record("waiting", {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                                "message": "Claude needs your permission to use Bash"}, folder)
    ws.controller.refresh()
    assert (t["state"], ws.controller.busy()) == ("idle", False)
    activity.record("working", {"hook_event_name": "UserPromptSubmit"}, folder)
    ws.controller.refresh()
    assert (t["state"], ws.controller.busy()) == ("working", True)


@pytest.mark.parametrize("said,ends", [("idle_prompt", "done"), ("permission_prompt", "failed")])
def test_a_program_that_ends_after_the_idle_prompt_is_done_and_mid_question_failed(ws, said, ends):
    m = on(ws)
    got = start(ws, plan(ws, m))
    t = ws.controller.record["tasks"][got["task"]]
    activity.record("waiting", {"hook_event_name": "Notification", "notification_type": said}, ws._dir(got["session"]))
    _Term.seen[-1].running = False
    ws.controller.refresh()
    assert t["state"] == ends


def test_a_status_line_that_gives_no_time_of_its_own_is_not_the_account_s(ws):
    on(ws)
    now = int(time.time())
    genuine = {"used_percentage": 40, "resets_at": now + 3600}
    _line(ws, _task(ws, "working")["session"], now - 30, five_hour=genuine)
    undated = _task(ws, "working")
    with open(os.path.join(ws._dir(undated["session"]), "status.json"), "w") as fh:
        json.dump({"five_hour": {"used_percentage": 100, "resets_at": now + 600}}, fh)
    assert ws.controller._newest()["five_hour"] == genuine
    assert ws.controller._reset_of(undated, now) is None            # its own line, undated, is not taken either
    assert statusline.read(ws._dir(undated["session"]))["five_hour"]["used_percentage"] == 100   # the map's still
    assert statusline.read(ws._dir(undated["session"]), dated=True) is None


def test_the_owner_s_go_a_hold_and_a_close_are_told_to_the_chat(ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, confirm_plans=True, packet_b64=b64, packet_digest=digest)
    requirement = plan(ws, m)
    _, _, sent = _watch_chat(ws, monkeypatch)
    _primed(ws, m)
    listed = {"id": requirement, "state": "open", "hold": False, "waits_for_go": True}
    version = [1]

    def side(**change):
        version[0] += 1
        take(ws, dict(m, version=version[0], requirements=[dict(listed, **change)]))
        before = len(sent)
        asyncio.run(ws.controller.keep_chat())
        return sent[before:]
    assert side() == []                                 # listed as its plan said: waiting for the go
    for change in ({"waits_for_go": False}, {"waits_for_go": False, "hold": True},
                   {"waits_for_go": False, "hold": False}, {"waits_for_go": False, "state": "done"}):
        [told] = side(**change)
        assert told.startswith(ctl.CHANGED)
        [w] = json.loads(told.split("\n", 1)[1])
        want = dict(listed, **change)
        assert w == {"requirement": requirement, "state": want["state"], "hold": want["hold"],
                     "waits_for_go": want["waits_for_go"], "changed_by": "owner"}, change
    assert side(waits_for_go=False, state="done") == []      # nothing changed: nothing said


def test_a_go_given_before_the_app_took_the_plan_is_told_too(ws, monkeypatch):
    b64, digest = packet()
    m = on(ws, confirm_plans=True, packet_b64=b64, packet_digest=digest)
    requirement = plan(ws, m)
    _, _, sent = _watch_chat(ws, monkeypatch)
    _primed(ws, m)
    take(ws, dict(m, version=2, requirements=[{"id": requirement, "state": "open", "hold": False,
                                               "waits_for_go": False}]))
    asyncio.run(ws.controller.keep_chat())
    assert len(sent) == 1 and requirement in sent[0] and '"waits_for_go": false' in sent[0]


def test_a_restart_lets_go_of_no_plan_before_the_owner_s_side_comes(ws):
    m = on(ws)
    requirement = plan(ws, m)
    ws.controller.record["plans"][requirement]["at"] = NOW - 5 * ctl.KEPT_ENDED      # five weeks old, no task
    with ws.controller._lock:
        ws.controller._save()
    again = ctl.Controller(ws)
    again.refresh()
    assert requirement in again.record["plans"]          # no side held yet: what it lists is not known
    asyncio.run(again.take(dict(m, version=2, requirements=[
        {"id": requirement, "state": "open", "hold": False, "waits_for_go": False}])))
    again.refresh()
    assert requirement in again.record["plans"]          # listed: kept
    asyncio.run(again.take(dict(m, version=3)))
    again.refresh()
    assert requirement not in again.record["plans"]      # a side that does not list it: let go


def test_a_waiting_session_keeps_the_machine_up_a_while_past_its_reset_and_only_while_it_may_go_on(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    t = ws.controller.record["tasks"][got["task"]]
    now = int(time.time())
    for waits, busy in ((now + 60, True), (now - ctl.CONTINUE_GRACE + 60, True),
                        (now - ctl.CONTINUE_GRACE - 60, False)):
        t["state"], t["waits_until"] = "waiting", waits
        assert ws.controller.busy() is busy, waits
    t["waits_until"] = now + 60
    take(ws, dict(m, version=2, requirements=[{"id": t["requirement"], "state": "open", "hold": True,
                                               "waits_for_go": False}]))
    assert ws.controller.busy() is False                 # held: nothing would continue it


def test_a_plan_not_yet_listed_waits_for_the_go_by_the_switch_it_was_planned_under(ws):
    m = on(ws, confirm_plans=True)
    waits = plan(ws, m)
    take(ws, dict(m, version=2, confirm_plans=False))    # off before the app read the plan made while it was on
    assert "waits for the owner's go" in refused(start, ws, waits)
    m3 = dict(m, version=3, confirm_plans=False)
    take(ws, m3)
    free = plan(ws, m3)
    take(ws, dict(m3, version=4, confirm_plans=True))   # on after a plan made while it was off
    assert start(ws, free)["task"] in ws.controller.record["tasks"]


def test_a_continue_asks_the_pacing_again_under_the_start_lock(ws, monkeypatch):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    calls = []

    def pace(now=None, model=None):
        calls.append(model)
        return (True, None, {}) if len(calls) == 1 else (False, "Another started meanwhile.", {})
    monkeypatch.setattr(ws.controller, "pace", pace)
    seen = len(_Term.seen)
    assert refused(ws.controller.continue_task, {"task": got["task"]}) == "Another started meanwhile."
    assert calls == ["opus", "opus"] and len(_Term.seen) == seen and _Term.seen[-1].running
    assert actions(ws, "continue") == [] and t["state"] == "waiting" and not t.get("continuing")


def test_a_change_or_a_refusal_that_comes_while_a_message_is_sent_is_kept_for_the_next(ws, monkeypatch):
    m = on(ws)
    _, _, sent = _watch_chat(ws, monkeypatch)
    _primed(ws, m)
    c = ws.controller
    t = _task(ws, "working")
    monkeypatch.setattr(c, "pace", lambda now=None, model=None: (True, None, {}))
    # a refusal whose window has reset already (it holds nothing), and a start the pacing refused, which now allows
    spent = {"status": "rejected", "rate_limit_type": "five_hour", "resets_at": int(time.time()) - 1}

    def again():
        c.rate_limited(spent)
        with c._lock:
            c.paced = True
        c.recheck_pace()

    async def send(sid, text):
        sent.append(text)
        if len(sent) == 1:
            with c._lock:
                c._set(t, "done", int(time.time()))         # it ended while the message went
            again()                                          # and the chat met the limit, and the pacing allowed, again
    monkeypatch.setattr(ws, "chat_send", send)
    with c._lock:
        c._set(t, "idle", int(time.time()))
    again()
    asyncio.run(c.keep_chat())
    assert ctl.GO_ON in sent[0] and ctl.PACED in sent[0] and '"state": "idle"' in sent[0]
    assert c.wakes[t["id"]]["state"] == "done" and c.cut_off and c.pace_ready
    asyncio.run(c.keep_chat())
    assert ctl.GO_ON in sent[1] and ctl.PACED in sent[1] and '"state": "done"' in sent[1]
    assert c.wakes == {} and not c.cut_off and not c.pace_ready


def test_a_fresh_record_numbers_its_changes_above_an_earlier_one(ws):
    m = on(ws)
    plan(ws, m)
    before = page(ws)["seq"]
    assert before >= int(time.time() * 1000) - 600_000  # a first change numbered by the clock's milliseconds
    shutil.rmtree(ws.controller.dir)                     # its folder removed, which names no file the gate guards
    again = ctl.Controller(ws)
    assert again.page(0)["seq"] == 0                     # empty: 0, as the contract has it
    with again._lock:
        again._act("question", question=uid(), requirement=None, text="q")
    assert again.page(0)["seq"] > before                 # above every change of the record before it


def test_a_start_cancelled_while_its_session_is_made_leaves_no_session(ws, monkeypatch):
    import threading
    m = on(ws)
    requirement = plan(ws, m)
    real, entered, release, made = ws.prepare, threading.Event(), threading.Event(), []

    def prepare(*args, **kwargs):
        entered.set()
        release.wait(30)
        out = real(*args, **kwargs)
        made.append(out[0]["id"])
        return out
    monkeypatch.setattr(ws, "prepare", prepare)

    async def go():
        call = asyncio.ensure_future(ws.controller.start_task({"requirement": requirement, "repo": "demo",
                                                               "task": "Do it."}))
        await asyncio.to_thread(entered.wait, 30)
        call.cancel()                                     # the chat closed at once: Off, or a change of account
        with pytest.raises(asyncio.CancelledError):
            await call
        release.set()
        for _ in range(300):
            if made and not os.path.exists(ws._dir(made[0])):
                return
            await asyncio.sleep(0.05)
    asyncio.run(go())
    assert made and not os.path.exists(ws._dir(made[0]))
    assert ws.controller.record["tasks"] == {} and actions(ws, "start") == []


def test_a_model_s_own_window_sets_no_reset_on_another_model_s_session(ws):
    m = on(ws)
    got = start(ws, plan(ws, m), model="sonnet")
    t = ws.controller.record["tasks"][got["task"]]
    now = int(time.time())
    ws.controller.rate_limited({"status": "rejected", "rate_limit_type": "seven_day_opus", "resets_at": now + 600})
    assert ws.controller._reset_of(t, now) is None
    t["model"] = "opus"
    assert ws.controller._reset_of(t, now) == now + 600


def test_a_request_withdrawn_before_it_was_given_is_given_no_more(ws):
    m = on(ws, state="off")
    second = request("Also add a licence.", n=2)
    pending = lambda: {p["id"] for p in ws.controller.pending}          # noqa: E731
    take(ws, dict(m, version=2, requests=m["requests"] + [second]))
    assert second["id"] in pending()
    take(ws, dict(m, version=3))                         # the whole list no longer holds it: withdrawn
    assert second["id"] not in pending()
    take(ws, dict(m, version=4, requests=m["requests"] + [second]))
    take(ws, dict(m, version=5, more_requests=1))        # a list cut short: whether it was withdrawn is not known
    assert second["id"] in pending()


def test_a_repository_named_twice_is_kept_once_as_the_app_keeps_it(ws):
    m = on(ws)
    requirement = plan(ws, m, repos=["demo", "other", "demo"])
    [a] = actions(ws, "plan")
    assert a["repos"] == ["demo", "other"] == ws.controller.record["plans"][requirement]["repos"]


# ---- never on its old conversation; a page taken, not served; a plan told, the owner's message, a continue, the
# settings file and an old question ------------------------------------------------------------------------------------

def test_a_controller_chat_starts_its_claude_once_and_never_resumes(ws):
    class Chat:
        session_id, env = "0123456789ab", {}
    c = Chat()
    with ws.controller._lock:
        ws.controller.record.update(session=c.session_id, chats=["ba9876543210", c.session_id],
                                    chat={"account": "default", "primed": False})
    ws.controller.configure(c)
    assert c.resumes is False                            # chat.py: no --resume at any start of its claude
    assert ws.controller.start_refusal(c.session_id) is None         # its first start, for its brief and packet
    ws.controller.record["chat"]["primed"] = True
    for sid in (c.session_id, "ba9876543210"):               # once primed, and an earlier chat of the controller's
        assert "afresh" in ws.controller.start_refusal(sid)
    from proofpane_workspace import chat as chats
    assert chats.Chat("ba9876543211", "/tmp", "/tmp", {}, "/tmp/w").resumes is True    # every other chat resumes


@pytest.mark.parametrize("resumes", [True, False])
def test_a_chat_that_does_not_resume_starts_its_claude_on_no_earlier_conversation(tmp_path, monkeypatch, resumes):
    import claude_agent_sdk
    from proofpane_workspace import chat as chats
    seen = {}

    class Built(Exception):
        """The options built: nothing more is started."""

    def options(**given):
        seen.update(given)
        raise Built()
    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", options)
    c = chats.Chat("0123456789ab", str(tmp_path / "s"), str(tmp_path), {}, str(tmp_path / "w"))
    c.mcp_plan = lambda: {"off": [], "strict": True, "servers": []}
    c.sdk_session, c.resumes = "an-earlier-conversation", resumes
    with pytest.raises(Built):
        asyncio.run(c._connect())
    assert seen["resume"] == ("an-earlier-conversation" if resumes else None)


def test_a_controller_chat_s_claude_is_not_started_again_by_the_workspace(ws, monkeypatch, tmp_path):
    from proofpane_workspace import chat as chats
    monkeypatch.setattr(ws, "_claude", lambda: "/no/such/claude")
    monkeypatch.setattr(ws, "claude_floor_refusal", lambda claude, cwd, env: None)
    ws._chat_wrapper = str(tmp_path / "chat-claude")
    with ws.controller._lock:
        ws.controller.record.update(session="0123456789ab", chat={"account": "default", "primed": True})
    c = chats.Chat("0123456789ab", str(tmp_path / "s"), str(tmp_path), {}, ws._chat_wrapper)
    assert "afresh" in ws._chat_start(c)
    other = chats.Chat("ba9876543210", str(tmp_path / "s"), str(tmp_path), {}, ws._chat_wrapper)
    assert ws._chat_start(other) is None                 # an owner's own chat starts as ever


@pytest.mark.parametrize("why", ["stopped", "crashed", "restart"])
def test_a_chat_whose_claude_stopped_or_would_start_again_is_replaced_by_a_fresh_one(ws, monkeypatch, why):
    b64, digest = packet()
    m = on(ws, packet_b64=b64, packet_digest=digest)
    made, _, sent = _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m)
    c = ws.chats[sid]
    if why == "restart":
        c.restart = True
    else:
        c.client = None                                  # the owner's Off or Stop, or a crash of its claude
    ws.controller.pending.append({"kind": "decision", "id": uid(), "n": 1, "at": NOW, "question": None, "text": "x"})
    asyncio.run(ws.controller.keep_chat())
    assert made == ["default"] and sent == []           # never a message to a claude that would start again
    del ws.chats[sid]
    made.clear()
    _primed(ws, m)                                       # its Chat gone from the workspace (closed), its record there
    del ws.chats[ws.controller.record["session"]]
    asyncio.run(ws.controller.keep_chat())
    assert made == ["default"]


def test_a_page_served_is_not_taken_for_read_until_the_app_asks_past_it(ws):
    m = on(ws)
    requirement = plan(ws, m)
    got = page(ws)                                       # served to its end, but the app's read may fail after it
    take(ws, dict(m, version=2))
    assert ws.controller.record["mirror_acked"] == 0 and "plan:" + requirement not in ws.controller.wakes
    assert start(ws, requirement)["task"]                # not known refused: a start as for a plan not yet read
    page(ws, got["seq"])                                 # its next round asks since what it took
    assert "does not yet say whether the app took" in refused(start, ws, requirement, task="Again.")
    take(ws, dict(m, version=3))                         # its side after that read, not listing it: refused
    assert ws.controller.record["mirror_acked"] == got["seq"] and "plan:" + requirement in ws.controller.wakes
    assert "did not take its plan" in refused(start, ws, requirement, task="Again.")


def test_a_plan_told_not_taken_and_listed_after_all_is_told_so(ws):
    m = on(ws)
    requirement = plan(ws, m)
    read_through(ws)
    take(ws, dict(m, version=2))
    ws.controller.wakes.clear()
    take(ws, dict(m, version=3, requirements=[{"id": requirement, "state": "open", "hold": False,
                                               "waits_for_go": False}]))
    w = ws.controller.wakes["plan:" + requirement]
    assert w["state"] == "listed by the owner's side after all" and "told" not in ws.controller.record["plans"][
        requirement]


def test_a_message_the_owner_sends_meanwhile_holds_the_controller_s_for_a_round_not_five_minutes(ws, monkeypatch):
    from proofpane_workspace import chat as chats
    m = on(ws)
    _watch_chat(ws, monkeypatch)
    sid = _primed(ws, m)
    ws.controller.pending.append({"kind": "decision", "id": uid(), "n": 1, "at": NOW, "question": None, "text": "x"})

    async def send(sid_, text):
        ws.chats[sid].state = "running"                  # the owner's own message took the chat first
        raise chats.ChatError("Claude is still working on the last message: wait for it, or stop it.")
    monkeypatch.setattr(ws, "chat_send", send)
    asyncio.run(ws.controller.keep_chat())
    assert ws.controller.failed_at is None and ws.controller.pending
    # the owner's message coming while the round reads the chat's settings: looked at again just before the send
    ws.chats[sid].state = "idle"
    tries, real = [], ws.chat_settings

    def settings_read(given):
        ws.chats[sid].state = "running"
        return real(given)

    async def counted(sid_, text):
        tries.append(text)
    monkeypatch.setattr(ws, "chat_settings", settings_read)
    monkeypatch.setattr(ws, "chat_send", counted)
    asyncio.run(ws.controller.keep_chat())
    assert tries == [] and ws.controller.failed_at is None


def test_a_session_waiting_on_its_owner_is_told_and_read_as_needing_the_owner(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    t = ws.controller.record["tasks"][got["task"]]
    activity.record("waiting", {"hook_event_name": "Notification", "notification_type": "permission_prompt"},
                    ws._dir(got["session"]))
    ws.controller.refresh()
    assert ws.controller.wakes[t["id"]] == {"id": t["id"], "state": "idle", "waits_until": None, "ended_at": None,
                                             "checks": None, "why": None, "checking": False, "needs_owner": True}
    assert page(ws)["tasks"][0] == {"id": t["id"], "state": "idle", "waits_until": None, "ended_at": None,
                                    "checks": None, "why": None, "checking": False, "needs_owner": True,
                                    "changed": t["state_seq"], "stopped_by": None}
    [item] = ws.controller.read_tree()["requirements"]
    assert item["tasks"][0]["needs_owner"] is True
    ws.controller.wakes.clear()
    activity.record("working", {"hook_event_name": "UserPromptSubmit"}, ws._dir(got["session"]))
    ws.controller.refresh()
    activity.record("waiting", {"hook_event_name": "Notification", "notification_type": "idle_prompt"},
                    ws._dir(got["session"]))
    ws.controller.refresh()
    assert "needs_owner" not in ws.controller.wakes[t["id"]]         # at rest, not waiting on its owner
    assert ws.controller.read_tree()["requirements"][0]["tasks"][0]["needs_owner"] is False


def test_a_continue_comes_a_minute_after_the_reset_at_the_soonest(ws):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    t["waits_until"] = int(time.time()) - 5
    assert "reset moments ago" in refused(ws.controller.continue_task, {"task": got["task"]})
    t["waits_until"] = int(time.time()) - ctl.CONTINUE_AFTER - 1
    assert asyncio.run(ws.controller.continue_task({"task": got["task"]}))["state"] == "working"


def test_a_continue_at_the_workspace_s_most_programs_replaces_its_own(ws, monkeypatch):
    m = on(ws)
    got, t = _waiting(ws, m, NOW + 3600)
    _passed(ws, (got, t))
    running = ws.running_programs()
    monkeypatch.setattr(ws, "max_sessions", running)     # its own program, still at the limit, makes the most
    assert asyncio.run(ws.controller.continue_task({"task": got["task"]}))["state"] == "working"
    t["state"], t["waits_until"] = "waiting", int(time.time()) - ctl.CONTINUE_AFTER - 1
    _Term.seen[-1].running = False                       # its program gone: a continue would add one
    monkeypatch.setattr(ws, "max_sessions", ws.running_programs())
    # refused by the controller, before anything is stopped or started (the workspace's launch would refuse it too)
    assert refused(ws.controller.continue_task, {"task": got["task"]}) == (
        f"At most {ws.max_sessions} programs run at once in the workspace.")


def test_a_controller_start_without_its_settings_file_is_refused_and_leaves_no_session(ws, monkeypatch):
    m = on(ws)
    requirement = plan(ws, m)
    before = set(os.listdir(ws.sessions_dir))
    monkeypatch.setattr(ws, "_with_map", lambda sid, argv, cwd, controller=False: argv)   # no file written
    assert "limit hooks" in refused(start, ws, requirement)
    assert set(os.listdir(ws.sessions_dir)) == before and ws.controller.record["tasks"] == {} and _Term.seen == []


def test_a_watched_session_is_never_started_again_without_its_settings_file(ws):
    m = on(ws)
    got = start(ws, plan(ws, m))
    _Term.seen[-1].running = False
    with pytest.raises(wsmod.WorkspaceError) as e:
        ws.launch(got["session"], effort="max")          # no version read for this start: no file could be made
    assert "limit hooks" in str(e.value)
    t = ws.controller.record["tasks"][got["task"]]
    with ws.controller._lock:
        ws.controller._set(t, "stopped", int(time.time()))
    ws.launch(got["session"], effort="max")              # ended: the owner's own run, as any session's


def test_an_old_open_question_a_full_list_leaves_out_still_counts(ws):
    m = on(ws)
    old = ws.controller.ask_owner({"text": "First?"})["question"]
    read_through(ws)
    answered = [{"id": uid(), "state": "answered", "decision": uid()} for _ in range(ctl.QUESTIONS_MOST)]
    take(ws, dict(m, version=2, questions=[{"id": old, "state": "open", "decision": None}]))
    for i in range(2):
        ws.controller.ask_owner({"text": f"Q{i}?"})
    read_through(ws)
    take(ws, dict(m, version=3, questions=answered))     # its list full, newest first: the old one cut, still open
    assert "3 questions are open" in refused(ws.controller.ask_owner, {"text": "Fourth?"})
    take(ws, dict(m, version=4, questions=answered[:10]))    # not full: one it does not list was refused or closed
    assert "question" in ws.controller.ask_owner({"text": "Fourth?"})
    # one seen answered, then cut from a full list, is not counted: the others still open are
    take(ws, dict(m, version=5, questions=[{"id": old, "state": "answered", "decision": uid()}]))
    read_through(ws)
    take(ws, dict(m, version=6, questions=answered))
    opened = {k for k, q in ws.controller.record["questions"].items() if q.get("state") != "answered"}
    assert old not in opened and len(opened) == 3
    assert "3 questions are open" in refused(ws.controller.ask_owner, {"text": "Fifth?"})
