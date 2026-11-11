"""The controller across the app and the workspace: the app's own sync
(proofpane_open/ade/controller.py, through the real workspace_client) against a real workspace agent's two routes
(proofpane_workspace/controller.py), so that a field one side renames, or a shape one side changes, fails here. The
workspace runs the stand-in claude (chat_stand_in.py) for the controller's chat, which calls the controller's own tools
through the SDK as the real one does ("ctool"), and for the sessions the controller starts; nothing reaches a real
claude."""

import json
import os
import sys
import time
import types

import pytest

from proofpane_open import store
from proofpane_open.ade import controller, workspace_client
from proofpane_workspace import controller as workspace_controller
from test_controller import own
from workspace_fixtures import Agent, make_repo

STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")


@pytest.fixture(autouse=True)
def own_requirements(monkeypatch):
    """These tests are of the controller's own requirements: the app's sync sends no managers' route, as an
    app from before requirements' managers sends none, so that the workspace takes a plan with no share
    (a share is required once an app has sent that route, and a requirement planned with none is the controller's).
    A managed requirement's crossing is test_controller_managers_crossing.py's."""
    monkeypatch.setattr(controller, "_managers_round", lambda *a, **k: False)
REQUEST = "Make the README say hello.\n\nIts first line is hello."
REAL_CALL = workspace_client.call


@pytest.fixture
def agent(tmp_path, request):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = dict({"PATH": f"{bin_dir}:{os.environ['PATH']}"}, **getattr(request, "param", {}))
    a = Agent(str(tmp_path / "ws"), str(home), env)
    make_repo(str(tmp_path / "ws" / "repos" / "demo"), origin="https://github.com/acme/demo.git")
    a.home = home
    a.root = tmp_path / "ws"
    a.endpoint = types.SimpleNamespace(http=a.base, secret=a.secret, ssl=None)
    yield a
    a.close()


def owner():
    """An owner whose controller is on, on the workspace's default account, confirmed as theirs, with one request."""
    user = {"id": "u" + os.urandom(4).hex()}
    own(user["id"])
    controller.change_settings(user, {"account": "default"})
    controller.set_state(user, "on")
    controller.add_request(user, REQUEST)
    return user


def until(what, done, timeout=60):
    deadline = time.time() + timeout
    while True:
        got = what()
        if done(got):
            return got
        assert time.time() < deadline, got
        time.sleep(0.1)


def record(agent, since=0):
    r = agent.http.get(f"/controller/record?since={since}")
    assert r.status_code == 200, r.text
    return r.json()


def said(agent, sid, text):
    """`text` sent to the controller's chat, and the stand-in's answer to it once its turn ended: the text after it,
    up to its turn's result, so that a message the workspace sends the chat meanwhile (a session's reset passed, say)
    and its answer are never taken for it."""
    before = len(agent.http.get(f"/sessions/{sid}/chat").json()["events"])

    def post():
        r = agent.http.post(f"/sessions/{sid}/chat", json={"text": text})
        if r.status_code == 400 and "still working" in r.text:
            return None             # a turn on a message of the workspace's has not ended yet: sent once it has
        assert r.status_code == 200, r.text
        return r
    until(post, lambda r: r is not None)

    def answered(log):
        events = log["events"][before:]
        mine = next((i for i, e in enumerate(events) if e["kind"] == "user" and e["text"] == text), None)
        if mine is None:
            return None
        after = events[mine + 1:]
        end = next((i for i, e in enumerate(after) if e["kind"] == "result"), None)
        return None if end is None else [e["text"] for e in after[:end] if e["kind"] == "text"]
    return until(lambda: answered(agent.http.get(f"/sessions/{sid}/chat").json()), lambda got: got)[-1]


def runs(agent):
    with open(agent.home / "stand-in-runs.jsonl") as fh:
        return [json.loads(line) for line in fh]


def test_the_app_s_sync_and_the_workspace_s_routes_agree_on_every_field(agent):
    user = owner()
    # the first sync: the workspace's empty record read, and the owner's side sent and held
    assert controller.sync(user, agent.endpoint, call=REAL_CALL) is True
    with store.connect() as db:
        c = controller._row(db, user["id"])
        assert (c["read_error"], c["mirror_error"]) == (None, None)
        version = c["version"]
    assert record(agent)["held"] == version

    # the controller's chat starts from the packet, on its own: its first message the brief and the packet
    sid = until(lambda: record(agent)["session"], lambda s: s is not None)
    log = until(lambda: agent.http.get(f"/sessions/{sid}/chat").json(),
                lambda log: any(e["kind"] == "result" for e in log["events"]))
    first = next(e["text"] for e in log["events"] if e["kind"] == "user")
    assert first.startswith("The workspace: you are the owner's Manager")
    assert '"schema":"ade.controller.v1"' in first and "Make the README say hello." in first
    chat_run = next(r for r in runs(agent) if r["cwd"].endswith(os.path.join(sid, "work")))
    args = chat_run["args"]
    assert args[args.index("--tools") + 1] == "" and "--strict-mcp-config" in args
    assert "--setting-sources=" in args               # no settings read from files: no CLAUDE.md, no hooks of theirs
    assert args[args.index("--allowedTools") + 1].split(",") == [
        f"mcp__proofpane__{t}" for t in ("plan_requirement", "start_task", "read_tree", "stop_task", "continue_task",
                                        "ask_owner")]
    assert args[args.index("--model") + 1] == "opus" and args[args.index("--effort") + 1] == "xhigh"
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    with store.connect() as db:
        assert controller._row(db, user["id"])["session_id"] == sid

    # a plan through the chat's own tool, read by the app's sync and taken
    [req] = controller.view(user)["requests"]
    plan = {"request": req["id"], "title": "Hello README", "words": "Make the README say hello.",
            "acceptance": "Its first line is hello.", "repos": ["demo"]}
    answer = json.loads(said(agent, sid, "ctool plan_requirement|" + json.dumps(plan)))
    requirement = answer["requirement"]
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    [r] = controller.view(user)["requirements"]
    assert (r["id"], r["title"]["text"], r["words"], r["acceptance"], r["repos"]) == (
        requirement, "Hello README", "Make the README say hello.", "Its first line is hello.", ["demo"])

    # a start, within the owner's bounds, so not flagged; its session's state read back
    answer = json.loads(said(agent, sid, "ctool start_task|" + json.dumps(
        {"requirement": requirement, "repo": "demo", "task": "Edit README.md so its first line is hello."})))
    time.sleep(2.5)         # read seconds later, so that a time the app took from its own clock would differ
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    [t] = controller.view(user)["requirements"][0]["tasks"]
    assert (t["id"], t["session"], t["repo"], t["branch"], t["program"], t["model"], t["effort"], t["account"],
            t["flag"]) == (answer["task"], answer["session"], "demo", f"pp/{answer['session']}", "claude", "opus",
                           "max", "default", None)
    assert t["state"] in ("starting", "working", "running", "idle")
    terminal = until(lambda: next((r for r in runs(agent) if r["args"] and
                                   r["args"][-1].startswith("This session was started")), None),
                     lambda r: r is not None)          # written as the terminal's program starts, in its own time
    assert "--model=opus" in terminal["args"]

    # a question about the requirement, and a stop: each taken, the session ended as the workspace says; the times
    # and the question's requirement as the workspace's record gives them
    question = json.loads(said(agent, sid, "ctool ask_owner|" + json.dumps(
        {"text": "Which branch?", "requirement": requirement})))["question"]
    said(agent, sid, "ctool stop_task|" + json.dumps({"task": answer["task"]}))
    time.sleep(2.5)
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    view = controller.view(user)
    assert [(q["id"], q["requirement"]) for q in view["questions"]] == [(question, requirement)]
    with store.connect() as db:
        c = controller._row(db, user["id"])
        assert (c["read_error"], c["mirror_error"]) == (None, None)     # the side listing the question taken whole
    assert view["requirements"][0]["tasks"][0]["state"] == "stopped"
    acted = {a["kind"]: a for a in record(agent)["actions"]}
    assert view["requirements"][0]["tasks"][0]["started_at"] == acted["start"]["at"]
    assert view["questions"][0]["asked_at"] == acted["question"]["at"]
    refused = [e for e in controller.journal_of(user)["events"] if e["kind"].endswith(".refused")]
    assert refused == []

    # a refusal the workspace makes is said to the chat, and nothing reaches the app
    out = said(agent, sid, "ctool start_task|" + json.dumps({"requirement": requirement, "repo": "demo", "task": "x",
                                                            "program": "codex"}))
    assert out.startswith("refused: ") and "Claude Code alone" in out


def test_end_all_and_stop_from_the_app_reach_the_workspace(agent):
    user = owner()
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    sid = until(lambda: record(agent)["session"], lambda s: s is not None)
    until(lambda: agent.http.get(f"/sessions/{sid}/chat").json(),
          lambda log: any(e["kind"] == "result" for e in log["events"]))
    [req] = controller.view(user)["requests"]
    requirement = json.loads(said(agent, sid, "ctool plan_requirement|" + json.dumps(
        {"request": req["id"], "title": "T", "words": "Make the README say hello.", "repos": ["demo"]})))["requirement"]
    task = json.loads(said(agent, sid, "ctool start_task|" + json.dumps(
        {"requirement": requirement, "repo": "demo", "task": "Do it."})))["task"]
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    controller.end_all(user)
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    got = until(lambda: record(agent), lambda r: any(t["id"] == task and t["state"] == "stopped" for t in r["tasks"]))
    assert got["held"] is not None
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    assert controller.view(user)["requirements"][0]["tasks"][0]["state"] == "stopped"
    # the owner's Stop: the controller's chat stops, and is never started again on its old conversation: a message
    # to it is refused, not sent to a claude that would read that conversation back
    controller.set_state(user, "stopped")
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    until(lambda: chat(agent, sid)["state"], lambda state: state == "stopped")
    r = agent.http.post(f"/sessions/{sid}/chat", json={"text": 'ctool ask_owner|{"text": "Still there?"}'})
    assert r.status_code == 400 and "afresh" in r.json()["detail"]


def chat(agent, sid):
    return agent.http.get(f"/sessions/{sid}/chat").json()


def ready(agent, user):
    """The first sync, and the controller's chat started from the packet, its first turn ended: its session."""
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    sid = until(lambda: record(agent)["session"], lambda s: s is not None)
    until(lambda: chat(agent, sid), lambda log: any(e["kind"] == "result" for e in log["events"]))
    return sid


def users_said(log):
    return [e["text"] for e in log["events"] if e["kind"] == "user"]


def test_the_controller_s_chat_is_never_started_again_on_its_old_conversation(agent):
    user = owner()
    sid = ready(agent, user)
    controller.set_state(user, "stopped")
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    until(lambda: chat(agent, sid)["state"], lambda state: state == "stopped")
    controller.set_state(user, "on")
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    # on again: a fresh chat from the packet, never the old one's claude started with its conversation (its
    # transcript, in the account's folder, is a file any session's programs can write)
    fresh = until(lambda: record(agent)["session"], lambda s: s not in (None, sid))
    log = until(lambda: chat(agent, fresh), lambda log: any(e["kind"] == "result" for e in log["events"]))
    assert next(e["text"] for e in log["events"] if e["kind"] == "user").startswith(
        "The workspace: you are the owner's Manager")
    starts = [r for r in runs(agent) if r["cwd"].endswith((os.path.join(sid, "work"), os.path.join(fresh, "work")))]
    assert len(starts) == 2 and not any(a.startswith("--resume") for r in starts for a in r["args"])


def test_a_chat_that_compacts_is_replaced_by_a_fresh_one_from_the_packet(agent):
    user = owner()
    sid = ready(agent, user)
    said(agent, sid, "compact")
    new = until(lambda: record(agent)["session"], lambda s: s != sid)
    log = until(lambda: chat(agent, new), lambda log: any(e["kind"] == "result" for e in log["events"]))
    assert users_said(log)[0].startswith("The workspace: you are the owner's Manager")
    [old] = [s for s in agent.http.get("/sessions").json()["sessions"] if s["id"] == sid]
    assert old.get("closed") is True                    # closed, not removed: the owner's to open again
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    with store.connect() as db:
        assert controller._row(db, user["id"])["session_id"] == new
    # the chat replaced is never started again on its conversation: a message to it is refused (its tools, should its
    # claude still run, refuse too: test_controller_workspace)
    r = agent.http.post(f"/sessions/{sid}/chat", json={"text": "ctool read_tree|{}"})
    assert r.status_code == 400 and "afresh" in r.json()["detail"]


def test_a_new_request_and_a_change_in_a_task_are_told_to_the_chat_while_it_is_idle(agent):
    user = owner()
    sid = ready(agent, user)
    controller.add_request(user, "Also make the licence say MIT.")
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    log = until(lambda: chat(agent, sid), lambda log: any(
        t.startswith("The workspace: new from the owner") for t in users_said(log)))
    told = next(t for t in users_said(log) if t.startswith("The workspace: new from the owner"))
    sent = json.loads(told.split("\n", 1)[1])
    assert [r["text"] for r in sent["requests"]] == ["Also make the licence say MIT."] and sent["by"] == "owner"
    # a task the owner stops in its terminal: the chat is told what changed
    req = next(r for r in controller.view(user)["requests"] if r["text"] == REQUEST)
    requirement = json.loads(said(agent, sid, "ctool plan_requirement|" + json.dumps(
        {"request": req["id"], "title": "T", "words": "Make the README say hello.", "repos": ["demo"]})))["requirement"]
    task = json.loads(said(agent, sid, "ctool start_task|" + json.dumps(
        {"requirement": requirement, "repo": "demo", "task": "Do it."})))
    assert agent.http.post(f"/sessions/{task['session']}/stop").status_code == 200
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    log = until(lambda: chat(agent, sid), lambda log: any(
        t.startswith("The workspace: what changed") for t in users_said(log)))
    changed = json.loads(next(t for t in users_said(log) if t.startswith("The workspace: what changed"))
                         .split("\n", 1)[1])
    assert [(c["id"], c["state"]) for c in changed] == [(task["task"], "stopped")]


def test_a_window_the_chat_s_events_say_is_spent_holds_every_start(agent):
    user = owner()
    sid = ready(agent, user)
    [req] = controller.view(user)["requests"]
    requirement = json.loads(said(agent, sid, "ctool plan_requirement|" + json.dumps(
        {"request": req["id"], "title": "T", "words": "Make the README say hello.", "repos": ["demo"]})))["requirement"]
    said(agent, sid, "ratelimit rejected five_hour resets=600")
    out = said(agent, sid, "ctool start_task|" + json.dumps({"requirement": requirement, "repo": "demo",
                                                            "task": "Do it."}))
    assert out.startswith("refused: ") and "five_hour window is spent" in out


def test_a_restarted_workspace_ends_its_sessions_and_starts_a_fresh_chat(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    first = Agent(str(tmp_path / "ws"), str(home), env)
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"), origin="https://github.com/acme/demo.git")
        first.endpoint = types.SimpleNamespace(http=first.base, secret=first.secret, ssl=None)
        user = owner()
        sid = ready(first, user)
        [req] = controller.view(user)["requests"]
        requirement = json.loads(said(first, sid, "ctool plan_requirement|" + json.dumps(
            {"request": req["id"], "title": "T", "words": "Make the README say hello.",
             "repos": ["demo"]})))["requirement"]
        task = json.loads(said(first, sid, "ctool start_task|" + json.dumps(
            {"requirement": requirement, "repo": "demo", "task": "Do it."})))
        controller.sync(user, first.endpoint, call=REAL_CALL)
    finally:
        first.close()
    os.remove(tmp_path / "ws" / "agent.json")         # the first agent's address, which the second writes anew
    second = Agent(str(tmp_path / "ws"), str(home), env)
    try:
        second.endpoint = types.SimpleNamespace(http=second.base, secret=second.secret, ssl=None)
        controller.sync(user, second.endpoint, call=REAL_CALL)
        assert controller.view(user)["requirements"][0]["tasks"][0]["state"] == "stopped"
        new = until(lambda: record(second)["session"], lambda s: s not in (None, sid))
        log = until(lambda: chat(second, new), lambda log: any(e["kind"] == "result" for e in log["events"]))
        assert users_said(log)[0].startswith("The workspace: you are the owner's Manager")
        assert task["task"] in json.dumps(record(second)["tasks"])
    finally:
        second.close()


@pytest.mark.parametrize("agent", [{"PP_CONTROLLER_PAGE": "2"}], indirect=True)
def test_pages_waits_ends_bases_and_answers_cross_as_both_sides_read_them(agent):
    user = owner()
    sid = ready(agent, user)
    [req] = controller.view(user)["requests"]
    words = {"request": req["id"], "words": "Make the README say hello.", "repos": ["demo"]}
    made = [json.loads(said(agent, sid, "ctool plan_requirement|" + json.dumps(dict(words, title=f"T{i}"))))
            ["requirement"] for i in range(3)]
    # three plans on pages of two: one sync reads the pages through, by each page's `more` and `seq`
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    assert sorted(r["id"] for r in controller.view(user)["requirements"]) == sorted(made)
    since = 0
    while record(agent, since)["more"]:         # the record's end, read a page at a time as the app reads it
        since = record(agent, since)["seq"]
    with store.connect() as db:
        assert controller._row(db, user["id"])["seq"] == record(agent, since)["seq"] > 2
    # a quiet round: the record read, the side held, nothing sent again
    assert controller.sync(user, agent.endpoint, call=REAL_CALL) is False

    # a start from a base it names, which stops at its account's limit and waits, marked with the reset
    task = json.loads(said(agent, sid, "ctool start_task|" + json.dumps(
        {"requirement": made[0], "repo": "demo", "task": "Do it.", "base": "main"})))
    folder = agent.root / "sessions" / task["session"]
    reset = int(time.time()) + 3
    (folder / "status.json").write_text(json.dumps({"at": int(time.time()), "five_hour": {
        "used_percentage": 100, "resets_at": reset}}))
    with open(folder / "limits.jsonl", "a") as fh:
        fh.write(json.dumps({"at": int(time.time()) + 1, "kind": "rate_limit"}) + "\n")
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    [t] = [t for r in controller.view(user)["requirements"] for t in r["tasks"]]
    assert (t["base"], t["state"], t["waits_until"]) == ("main", "waiting", reset)
    # continued once its reset passed: the app counts it, unflagged, against the task the workspace named
    # a continue comes a minute after the reset at the soonest (the workspace's CONTINUE_AFTER)
    until(lambda: time.time(), lambda now: now > reset + workspace_controller.CONTINUE_AFTER + 1, timeout=120)
    went = json.loads(said(agent, sid, "ctool continue_task|" + json.dumps({"task": task["task"]})))
    assert went["state"] == "working"
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    [t] = [t for r in controller.view(user)["requirements"] for t in r["tasks"]]
    # started again, its hooks not yet heard from: starting, or working once they are
    assert t["state"] in ("starting", "working") and (t["continues"], t["flag"]) == (1, None)
    # stopped: its end, as the workspace dates it, read by the app a few seconds later, so that an end the app dated
    # by its own clock (the field not read) would differ
    said(agent, sid, "ctool stop_task|" + json.dumps({"task": task["task"]}))
    time.sleep(2.5)
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    [t] = [t for r in controller.view(user)["requirements"] for t in r["tasks"]]
    ended = next(x for x in record(agent)["tasks"] if x["id"] == task["task"])
    assert (t["state"], t["ended_at"], t["waits_until"]) == ("stopped", ended["ended_at"], None)

    # three questions, one answered by the owner: the answer reaches the chat with its question, and a fourth
    # question may be asked once the side says the first is answered
    asked = [json.loads(said(agent, sid, 'ctool ask_owner|{"text": "Q%d?"}' % i))["question"] for i in range(3)]
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    assert said(agent, sid, 'ctool ask_owner|{"text": "Q3?"}').startswith("refused: 3 questions are open")
    controller.decide(user, "Use main.", asked[0])
    controller.sync(user, agent.endpoint, call=REAL_CALL)
    log = until(lambda: chat(agent, sid), lambda log: any(
        t.startswith("The workspace: new from the owner") and "Use main." in t for t in users_said(log)))
    told = next(t for t in users_said(log) if "Use main." in t)
    [decision] = json.loads(told.split("\n", 1)[1])["decisions"]
    assert decision["question"] == asked[0]
    assert "question" in json.loads(said(agent, sid, 'ctool ask_owner|{"text": "Q3?"}'))
