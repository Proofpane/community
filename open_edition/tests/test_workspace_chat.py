"""The workspace's chat with Claude Code (chat.py): the real Claude Agent SDK
driving a stand-in `claude` that speaks its stream-JSON protocol (chat_stand_in.py), through a real agent."""

import json
import os
import sys
import threading
import time

import pytest

from proofpane_workspace import chat as chats
from workspace_fixtures import LEAK, SECRET, Agent, make_repo

pytestmark = pytest.mark.skipif(not chats.available(), reason="needs the Claude Agent SDK (requirements.txt)")
STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")


@pytest.fixture
def cagent(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"), origin="https://github.com/acme/demo.git")
    a.home = home
    yield a
    a.close()


def _chat(agent, task="hello"):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": task})
    assert r.status_code == 200, r.text
    return r.json()


def _until(agent, sid, done, timeout=30):
    deadline = time.time() + timeout
    while True:
        log = agent.http.get(f"/sessions/{sid}/chat").json()
        if done(log):
            return log
        assert time.time() < deadline, log
        time.sleep(0.05)


def _kinds(log, *drop):
    return [(e["kind"], e.get("text") or e.get("state") or e.get("summary") or e.get("decision") or e.get("subtype"))
            for e in log["events"] if e["kind"] not in drop]


def _runs(agent):
    with open(agent.home / "stand-in-runs.jsonl") as fh:
        return [json.loads(line) for line in fh]


def test_a_chat_s_task_is_its_first_message_and_the_log_keeps_what_claude_says(cagent, tmp_path):
    s = _chat(cagent)
    assert s["program"] == "chat" and s["chat"]["state"] in ("running", "idle")
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle")
    assert _kinds(log) == [("user", "hello"), ("status", "running"), ("session", None),
                           ("text", "Hello from the stand-in"), ("result", "success"), ("status", "idle")]
    assert [e["seq"] for e in log["events"]] == list(range(1, 7)) and log["seq"] == 6
    assert (log["events"][4]["cost"], log["cost"], log["last_text"]) == (0.0123, 0.0123, "Hello from the stand-in")
    view = cagent.http.get(f"/sessions/{s['id']}").json()
    assert view["running"] is True and view["chat"]["state"] == "idle"
    # the real SDK ran claude in the session's worktree, with Claude Code's own prompt and settings, and asking the
    # page for permission
    (run,) = _runs(cagent)
    assert os.path.realpath(run["cwd"]) == os.path.realpath(tmp_path / "ws" / "sessions" / s["id"] / "work")
    assert "--system-prompt" not in run["args"] and "--permission-prompt-tool" in run["args"]
    assert run["args"][run["args"].index("--disallowedTools") + 1] == "AskUserQuestion"   # no picker in a chat
    assert run["args"][run["args"].index("--permission-prompt-tool") + 1] == "stdio"
    assert "--setting-sources=user,project,local" in run["args"]


def test_a_chat_s_claude_gets_none_of_the_agent_s_names(cagent):
    # the SDK starts claude with its own process's whole environment; the wrapper builds claude's from nothing
    s = _chat(cagent)
    _until(cagent, s["id"], lambda log: log["state"] == "idle")
    (run,) = _runs(cagent)
    assert "OPEN_EDITION_TOKEN_SECRET" not in run["env"] and "PP_WORKSPACE_SECRET" not in run["env"]
    assert {"PATH", "HOME", "PP_SESSION_ID", "PP_SESSION_DIR", "CLAUDE_CODE_ENTRYPOINT"} <= set(run["env"])
    assert set(run["env"]) - {"PWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING"} <= (
        set(chats.WRAPPER_NAMES) | {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
                                    "LC_MESSAGES", "TZ", "TMPDIR"})
    mode = os.stat(os.path.join(cagent.root, "chat-claude")).st_mode & 0o777
    assert mode == 0o700
    wrapper = open(os.path.join(cagent.root, "chat-claude")).read()
    assert LEAK not in wrapper and SECRET not in wrapper


@pytest.mark.parametrize("decision, ran, summary", [("allow", True, "hi-2"), ("deny", False, "The owner said no"),
                                                    ("allow_session", True, "hi-2")])
def test_a_tool_call_waits_for_the_page_s_answer(cagent, decision, ran, summary):
    s = _chat(cagent, "run echo hi-$((1+1))")
    log = _until(cagent, s["id"], lambda log: log["state"] == "needs_you")
    (ask,) = [e for e in log["events"] if e["kind"] == "ask"]
    assert (ask["name"], ask["summary"], ask["for_session"]) == ("Bash", "echo hi-$((1+1))", True)
    assert log["pending"] == [ask["id"]]
    # the sessions' list carries what each waiting question is, for the page's needs-you queue
    (listed,) = [x for x in cagent.http.get("/sessions").json()["sessions"] if x["id"] == s["id"]]
    assert listed["chat"]["asks"] == [{"id": ask["id"], "name": "Bash", "summary": "echo hi-$((1+1))",
                                       "for_session": True, "time": ask["time"]}]
    r = cagent.http.post(f"/sessions/{s['id']}/chat/answer", json={"id": ask["id"], "decision": decision})
    assert r.status_code == 200, r.text
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle")
    end = [e for e in log["events"] if e["kind"] == "tool_end"][0]
    assert end["summary"].startswith(summary) and end["error"] is (not ran)
    assert ("answered", decision) in _kinds(log) and log["pending"] == [] and log["asks"] == []
    with open(cagent.home / "stand-in-answers.jsonl") as fh:
        (sent,) = [json.loads(line) for line in fh]
    if decision == "allow_session":       # the CLI's own suggestion, for this conversation, never the repository's
        assert sent["updatedPermissions"] == [{"type": "addRules", "destination": "session", "behavior": "allow",
                                               "rules": [{"toolName": "Bash", "ruleContent": "echo hi-$((1+1))"}]}]
    else:
        assert "updatedPermissions" not in sent


def test_only_a_waiting_question_takes_an_answer_and_only_one_of_three(cagent):
    s = _chat(cagent, "run true")
    log = _until(cagent, s["id"], lambda log: log["state"] == "needs_you")
    ask = log["pending"][0]
    for body, detail in [({"id": "nope", "decision": "allow"}, "no longer waiting"),
                         ({"id": ask, "decision": "yes please"}, "allow, allow_session or deny"),
                         ({"decision": "allow"}, "Name the question")]:
        r = cagent.http.post(f"/sessions/{s['id']}/chat/answer", json=body)
        assert r.status_code == 400 and detail in r.json()["detail"], r.text
    r = cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "and another thing"})
    assert r.status_code == 400 and "still working" in r.json()["detail"]
    cagent.http.post(f"/sessions/{s['id']}/chat/answer", json={"id": ask, "decision": "allow"})
    _until(cagent, s["id"], lambda log: log["state"] == "idle")
    r = cagent.http.post(f"/sessions/{s['id']}/chat/answer", json={"id": ask, "decision": "allow"})
    assert r.status_code == 400 and "no longer waiting" in r.json()["detail"]


def test_a_turn_can_be_stopped_and_the_chat_goes_on(cagent):
    s = _chat(cagent, "wait")
    _until(cagent, s["id"], lambda log: log["state"] == "running" and any(e["kind"] == "session" for e in log["events"]))
    assert cagent.http.post(f"/sessions/{s['id']}/chat/interrupt").status_code == 200
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle")
    assert ("result", "error_during_execution") in _kinds(log)
    r = cagent.http.post(f"/sessions/{s['id']}/chat/interrupt")
    assert r.status_code == 400 and "not working" in r.json()["detail"]
    assert cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "hello"}).status_code == 200
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle" and log["last_text"] == "Hello from the stand-in")


def test_a_chat_stopped_while_the_sdk_asks_its_claude_s_version_leaves_no_claude_running(tmp_path):
    """The SDK asks claude's version inside its connect, before it starts claude: a stop then has no claude to end,
    and one that disconnected the client in the middle of the connect left the claude the connect went on to start
    running. The stop is kept to the connect's end, which ends that claude."""
    from test_agents_watch import _gone
    bin_dir, home = tmp_path / "bin", tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\nCONFIG = {{'VERSION_DELAY': 3}}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"), origin="https://github.com/acme/demo.git")
        asked = home / "stand-in-version-asked"

        def asks():
            return len(asked.read_text().split()) if asked.exists() else 0
        s = _chat(a, "hello")                           # its first connect, as the session is made
        _until(a, s["id"], lambda log: log["state"] == "idle")
        assert a.http.post(f"/sessions/{s['id']}/stop").status_code == 200    # its next message starts claude again
        before = asks()
        sending = threading.Thread(target=lambda: a.http.post(f"/sessions/{s['id']}/chat", json={"text": "hello"}))
        sending.start()
        deadline = time.time() + 15
        while asks() == before:                         # the SDK's connect is asking its version
            assert time.time() < deadline
            time.sleep(0.02)
        assert a.http.post(f"/sessions/{s['id']}/stop").status_code == 200
        sending.join(30)
        time.sleep(3)                                   # past the claude the connect went on to start
        alive = home / "stand-in-alive"
        assert all(_gone(int(p), timeout=1) for p in (os.listdir(alive) if alive.exists() else [])), \
            "a claude started for a chat stopped while it connected lived on"
        assert a.http.get(f"/sessions/{s['id']}/chat").json()["state"] == "stopped"
    finally:
        a.close()


def test_a_stopped_chat_resumes_its_conversation_with_the_next_message(cagent):
    s = _chat(cagent)
    first = _until(cagent, s["id"], lambda log: log["state"] == "idle")
    sdk = [e for e in first["events"] if e["kind"] == "session"][0]["sdk_session"]
    stopped = cagent.http.post(f"/sessions/{s['id']}/stop").json()
    assert stopped["running"] is False and stopped["chat"]["state"] == "stopped"
    assert cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "hello"}).status_code == 200
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle" and log["seq"] > first["seq"] + 2)
    assert log["last_text"] == f"Hello from the stand-in, resumed {sdk}"
    runs = _runs(cagent)
    assert len(runs) == 2 and not any(a.startswith("--resume") for a in runs[0]["args"])
    assert f"--resume={sdk}" in runs[1]["args"]


def test_a_closed_chat_given_a_message_is_back_in_the_list(cagent):
    """A close stops a chat as stop does; a chat's start again is its next message, so a message to a closed
    chat takes the mark away, as a start takes it from a terminal session; one the chat refuses leaves it closed."""
    s = _chat(cagent)
    _until(cagent, s["id"], lambda log: log["state"] == "idle")
    closed = cagent.http.post(f"/sessions/{s['id']}/close").json()
    assert closed["closed"] is True and closed["running"] is False and closed["chat"]["state"] == "stopped"
    assert cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "  "}).status_code == 400
    assert cagent.http.get(f"/sessions/{s['id']}").json().get("closed") is True
    assert cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "hello"}).status_code == 200
    assert not cagent.http.get(f"/sessions/{s['id']}").json().get("closed")
    _until(cagent, s["id"], lambda log: log["state"] == "idle")


def test_the_log_is_read_from_any_point(cagent):
    s = _chat(cagent)
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle")
    later = cagent.http.get(f"/sessions/{s['id']}/chat", params={"after": 3}).json()
    assert [e["seq"] for e in later["events"]] == [4, 5, 6] and later["seq"] == log["seq"]
    for bad in ("abc", "-1", "1e3"):
        r = cagent.http.get(f"/sessions/{s['id']}/chat", params={"after": bad})
        assert r.status_code == 400
    shell = cagent.http.post("/sessions", json={"program": "shell", "task": "t"}).json()
    r = cagent.http.get(f"/sessions/{shell['id']}/chat")
    assert r.status_code == 400 and "not a chat" in r.json()["detail"]


def test_one_socket_carries_every_chat_s_events_as_they_happen(cagent):
    from websockets.sync.client import connect
    ws = connect(f"ws://127.0.0.1:{cagent.port}/chat", additional_headers={"Authorization": "Bearer " + SECRET},
                 open_timeout=10)
    try:
        assert json.loads(ws.recv(timeout=10)) == {"type": "ready"}
        one, two = _chat(cagent), _chat(cagent, "hi there")
        seen = {one["id"]: [], two["id"]: []}
        written = {one["id"]: "", two["id"]: ""}
        deadline = time.time() + 30
        while not all(any(e["kind"] == "status" and e["state"] == "idle" for e in v) for v in seen.values()):
            msg = json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))
            if msg["type"] == "delta":              # what claude wrote as it wrote it (test_workspace_chat_stream)
                written[msg["session"]] += msg["delta"].get("text", "")
                continue
            assert msg["type"] == "event"
            seen[msg["session"]].append(msg["event"])
        for sid, events in seen.items():
            assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
            assert events == cagent.http.get(f"/sessions/{sid}/chat").json()["events"]
        assert seen[two["id"]][3]["text"] == "You said: hi there" == written[two["id"]]
    finally:
        ws.close()


def test_a_chat_works_in_a_repository_and_counts_among_the_programs_running(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                                                "PP_WORKSPACE_MAX_SESSIONS": "1"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        r = a.http.post("/sessions", json={"program": "chat", "task": "hello"})
        assert r.status_code == 400 and "works in a repository" in r.json()["detail"]
        s = _chat(a)
        _until(a, s["id"], lambda log: log["state"] == "idle")
        r = a.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "t"})
        assert r.status_code == 400 and "At most 1 programs" in r.json()["detail"]
        assert a.http.delete(f"/sessions/{s['id']}").json()["removed"] == s["id"]
        assert a.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "t"}).status_code == 200
    finally:
        a.close()


def test_stopping_a_turn_that_waits_on_a_question_answers_it_no(cagent):
    s = _chat(cagent, "run true")
    log = _until(cagent, s["id"], lambda log: log["state"] == "needs_you")
    assert cagent.http.post(f"/sessions/{s['id']}/chat/interrupt").status_code == 200
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle")
    assert log["pending"] == [] and ("answered", "stop") in _kinds(log)
    assert ("result", "error_during_execution") in _kinds(log) and "tool_end" not in [e["kind"] for e in log["events"]]
    with open(cagent.home / "stand-in-answers.jsonl") as fh:
        (sent,) = [json.loads(line) for line in fh]
    assert sent == {"behavior": "deny", "message": "Stopped from the ADE's chat.", "interrupt": True}
    # the no reached claude before the interrupt (under load it had come after)
    with open(cagent.home / "stand-in-order.jsonl") as fh:
        assert [json.loads(line) for line in fh] == ["answer", "interrupt"]


def test_the_log_is_given_a_page_at_a_time(tmp_path):
    path = tmp_path / "chat.jsonl"
    with open(path, "w") as fh:
        for seq in range(1, 11):
            fh.write(json.dumps({"seq": seq, "time": 0, "kind": "text", "text": "x" * 100}) + "\n")
        fh.write("not json\n")
    events, more = chats.read_events(str(path), 0, budget=450)       # each line is 150 bytes
    assert [e["seq"] for e in events] == [1, 2, 3] and more is True
    events, more = chats.read_events(str(path), 3, limit=4)
    assert [e["seq"] for e in events] == [4, 5, 6, 7] and more is True
    events, more = chats.read_events(str(path), 7)
    assert [e["seq"] for e in events] == [8, 9, 10] and more is False
    assert chats.read_events(str(tmp_path / "none.jsonl")) == ([], False)
    # one event larger than the budget is still given, alone, so a page is never stuck
    events, more = chats.read_events(str(path), 0, budget=10)
    assert [e["seq"] for e in events] == [1] and more is True


def test_the_app_passes_on_a_chat_event_and_nothing_else():
    from proofpane_open.ade import workspace_client as client
    ok = {"type": "event", "session": "a" * 12, "event": {"seq": 1, "kind": "text", "text": "é"}, "extra": 1}
    assert json.loads(client.chat_message(json.dumps(ok))) == {"type": "event", "session": "a" * 12,
                                                              "event": ok["event"]}
    assert client.chat_message(json.dumps({"type": "dropped", "why": "x"})) == '{"type": "dropped"}'
    for bad in ("not json", "[]", json.dumps({"type": "exit"}), json.dumps(dict(ok, session="../x")),
                json.dumps(dict(ok, event="text")), json.dumps(dict(ok, type="events"))):
        assert client.chat_message(bad) is None, bad


# a chat's model, effort and permission mode, as the Claude app has them: chosen when it is made, changed while it
# runs, kept with the session

def _controls(agent):
    path = agent.home / "stand-in-controls.jsonl"
    return [json.loads(line) for line in open(path)] if path.exists() else []


def _arg(run, flag):
    args = run["args"]
    for i, a in enumerate(args):
        if a == flag:
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def _results(agent, sid, n):
    return _until(agent, sid, lambda log: sum(e["kind"] == "result" for e in log["events"]) >= n)


def test_a_chat_starts_claude_with_the_model_effort_and_mode_chosen_and_keeps_them(cagent):
    chosen = {"model": "opus", "effort": "high", "permission_mode": "acceptEdits"}
    r = cagent.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": "hello",
                                            "chat_settings": chosen})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    _results(cagent, sid, 1)
    run = _runs(cagent)[-1]
    assert (_arg(run, "--model"), _arg(run, "--effort"), _arg(run, "--permission-mode")) == \
        ("opus", "high", "acceptEdits")
    got = cagent.http.get(f"/sessions/{sid}").json()
    assert got["chat_settings"] == chosen and got["chat"]["chosen"] == chosen
    # none chosen: Claude Code's own model and effort, and the mode that asks
    s = _chat(cagent)
    _results(cagent, s["id"], 1)
    run = _runs(cagent)[-1]
    assert (_arg(run, "--model"), _arg(run, "--effort"), _arg(run, "--permission-mode")) == (None, None, "default")
    assert cagent.http.get(f"/sessions/{s['id']}").json()["chat"]["chosen"] == {}


def test_the_model_and_the_mode_change_at_once_and_the_effort_with_the_next_message(cagent):
    sid = _chat(cagent)["id"]
    _results(cagent, sid, 1)
    starts = len(_runs(cagent))
    path = f"/sessions/{sid}/chat/settings"
    r = cagent.http.post(path, json={"model": "sonnet", "permission_mode": "plan"})
    assert r.status_code == 200 and r.json()["chosen"] == {"model": "sonnet", "permission_mode": "plan"}, r.text
    assert [(c["subtype"], c.get("model", c.get("mode"))) for c in _controls(cagent)] == \
        [("set_model", "sonnet"), ("set_permission_mode", "plan")]
    assert len(_runs(cagent)) == starts                     # claude kept running: nothing started again
    assert cagent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"}).status_code == 200
    _results(cagent, sid, 2)
    assert len(_runs(cagent)) == starts                     # nor with the next message
    r = cagent.http.post(path, json={"effort": "low"})
    assert r.json()["chosen"] == {"model": "sonnet", "permission_mode": "plan", "effort": "low"}
    assert len(_runs(cagent)) == starts and len(_controls(cagent)) == 2       # the effort waits for a message
    assert cagent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"}).status_code == 200
    _results(cagent, sid, 3)
    run = _runs(cagent)[-1]
    assert len(_runs(cagent)) == starts + 1 and _arg(run, "--resume")          # claude again, with the conversation
    assert (_arg(run, "--model"), _arg(run, "--effort"), _arg(run, "--permission-mode")) == ("sonnet", "low", "plan")
    # an empty value goes back to Claude Code's own, and the rest stay; all of it is on the session's record
    r = cagent.http.post(path, json={"model": ""})
    assert r.json()["chosen"] == {"permission_mode": "plan", "effort": "low"}
    assert _controls(cagent)[-1]["subtype"] == "set_model" and _controls(cagent)[-1].get("model") is None
    assert cagent.http.get(f"/sessions/{sid}").json()["chat_settings"] == {"permission_mode": "plan", "effort": "low"}
    log = cagent.http.get(f"/sessions/{sid}/chat").json()
    assert [e.get("model") for e in log["events"] if e["kind"] == "settings"] == ["sonnet", "sonnet", None]


def test_a_chat_s_settings_are_the_owner_s_whatever_its_record_was_changed_to(cagent, tmp_path):
    sid = _chat(cagent)["id"]
    _results(cagent, sid, 1)
    path = f"/sessions/{sid}/chat/settings"
    assert cagent.http.post(path, json={"permission_mode": "plan"}).status_code == 200
    record = tmp_path / "ws" / "sessions" / sid / "session.json"
    written = json.loads(record.read_text())
    written["chat_settings"] = {"permission_mode": "bypassPermissions"}         # a program of some session's
    record.write_text(json.dumps(written))
    r = cagent.http.post(path, json={"effort": "low"})
    assert r.json()["chosen"] == {"permission_mode": "plan", "effort": "low"}
    assert json.loads(record.read_text())["chat_settings"] == {"permission_mode": "plan", "effort": "low"}
    written = json.loads(record.read_text())
    written["chat_settings"] = {"permission_mode": "bypassPermissions"}
    record.write_text(json.dumps(written))
    assert cagent.http.post(f"/sessions/{sid}/title", json={"title": "Renamed"}).status_code == 200   # any write
    assert json.loads(record.read_text())["chat_settings"] == {"permission_mode": "plan", "effort": "low"}
    # each one back to Claude Code's own: none left, and none written
    r = cagent.http.post(path, json={"permission_mode": "", "effort": ""})
    assert r.status_code == 200 and r.json()["chosen"] == {}, r.text
    assert "chat_settings" not in json.loads(record.read_text())


@pytest.mark.parametrize("given, why", [
    ({"model": "--dangerously-skip-permissions"}, "Name a model"),
    ({"model": "opus sonnet"}, "Name a model"),
    ({"model": "Opus"}, "Name a model"),
    ({"effort": "extreme"}, "An effort is one of low, medium, high, xhigh, max, ultracode."),
    ({"permission_mode": "dontAsk"}, "A permission mode is one of default, acceptEdits, auto, bypassPermissions, plan."),
    ({"permission_mode": "manual"}, "A permission mode is one of"),
    ({"colour": "red"}, "A chat's settings are its model, its effort, its permission mode and whether it may start"),
    ({"may_dispatch": "yes"}, "Whether a chat may start agents' runs is true or false."),
    ("opus", "A chat's settings are its model, its effort, its permission mode and whether it may start")])
def test_only_claude_code_s_own_settings_are_taken_and_only_by_a_chat(cagent, given, why):
    r = cagent.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": "t", "chat_settings": given})
    assert r.status_code == 400 and why in r.json()["detail"], r.text
    assert cagent.http.get("/sessions").json()["sessions"] == []            # nothing was made
    r = cagent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "t",
                                            "chat_settings": {"model": "opus"}})
    assert r.status_code == 400 and r.json()["detail"].startswith("Only a chat"), r.text
    for model in ("fable", "opus[1m]", "claude-opus-4-5"):       # an alias, or a model's full name
        assert cagent.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": "t",
                                                   "chat_settings": {"model": model}}).status_code == 200


# a file from the page into a session's worktree, as the Claude app takes one

def test_a_file_goes_into_the_worktree_kept_out_of_its_commits_and_the_chat_is_told(cagent, tmp_path):
    import subprocess
    sid = _chat(cagent)["id"]
    _results(cagent, sid, 1)
    work = tmp_path / "ws" / "sessions" / sid / "work"
    up = lambda name, data: cagent.http.post(f"/sessions/{sid}/upload", params={"name": name, "to": "chat"},
                                             content=data)
    r = up("photo.png", b"\x89PNG fake")
    assert r.status_code == 200 and r.json() == {"path": ".proofpane-uploads/photo.png", "name": "photo.png",
                                                 "size": 9}, r.text
    assert (work / ".proofpane-uploads" / "photo.png").read_bytes() == b"\x89PNG fake"
    assert up("photo.png", b"again").json()["path"] == ".proofpane-uploads/photo-2.png"      # nothing is overwritten
    assert up("../../etc/passwd", b"x").json()["path"] == ".proofpane-uploads/passwd"         # its name only
    odd = up("r\u00e9sum\u00e9.pdf", b"x").json()["path"]                                          # not a plain name
    assert odd.startswith(".proofpane-uploads/upload-") and odd.endswith(".pdf"), odd
    assert up(".hidden", b"x").json()["path"].startswith(".proofpane-uploads/upload-")
    # git does not see them: the folder ignores itself, and a commit leaves them out; the repository's own exclude,
    # which a repository added from the person's machine shares with their checkout, is not written to
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=work, capture_output=True,
                            text=True).stdout
    assert status == "", status
    assert (work / ".proofpane-uploads" / ".gitignore").read_text() == "*\n"
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=work, capture_output=True,
                            text=True).stdout.strip()
    exclude = work / common / "info" / "exclude"
    assert not exclude.exists() or "proofpane" not in exclude.read_text()
    for bad, why in ((b"", "The file is empty."), (b"x" * (10 * 1024 * 1024 + 1), "A file is at most 10 MB.")):
        r = up("big.bin", bad)
        assert r.status_code == 400 and r.json()["detail"] == why, r.text[:200]
    log = cagent.http.get(f"/sessions/{sid}/chat").json()
    ups = [(e["name"], e["path"], e["size"]) for e in log["events"] if e["kind"] == "upload"]
    assert ups[:2] == [("photo.png", ".proofpane-uploads/photo.png", 9), ("photo-2.png", ".proofpane-uploads/photo-2.png", 5)]
    assert len(ups) == 5
    # a file dropped on the session's terminal or shell, not attached to a message, is not noted in the chat
    r = cagent.http.post(f"/sessions/{sid}/upload", params={"name": "dropped.txt"}, content=b"x")
    assert r.status_code == 200 and r.json()["path"] == ".proofpane-uploads/dropped.txt"
    log = cagent.http.get(f"/sessions/{sid}/chat").json()
    assert len([e for e in log["events"] if e["kind"] == "upload"]) == 5
    # a session without a repository has no worktree to put it in
    shell = cagent.http.post("/sessions", json={"program": "shell", "task": "t"}).json()
    r = cagent.http.post(f"/sessions/{shell['id']}/upload", params={"name": "a.txt"}, content=b"x")
    assert r.status_code == 400 and r.json()["detail"].startswith("A session without a repository"), r.text



# the choices follow the installed claude: read from its --help and
# --version (claude_cli.py), ultracode by its version rule

def test_the_agent_says_what_the_installed_claude_offers_and_ultracode_reaches_it(cagent):
    got = cagent.http.get("/capabilities").json()
    assert (got["claude_version"], got["floor"]) == ("2.1.274", False)
    assert got["efforts"] == ["low", "medium", "high", "xhigh", "max", "ultracode"]
    assert got["permission_modes"] == ["default", "acceptEdits", "auto", "bypassPermissions", "plan"]
    r = cagent.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": "hello",
                                            "chat_settings": {"effort": "ultracode"}})
    assert r.status_code == 200, r.text
    _results(cagent, r.json()["id"], 1)
    assert _arg(_runs(cagent)[-1], "--effort") == "ultracode"      # the SDK passes it on as it is


def _costs(log):
    return [(e["cost"], e.get("basis")) for e in log["events"] if e["kind"] == "result"]


def test_a_turn_s_cost_is_its_own_and_the_chat_s_is_what_they_add_up_to(cagent):
    # claude's total_cost_usd is a running total within one of its processes, and starts over at each start (seen with
    # the image's claude 2.1.274 on 2026-09-29): each result in the log carries its turn's own cost, and the chat's is
    # their sum, across a start again too
    s = _chat(cagent)
    _until(cagent, s["id"], lambda log: log["state"] == "idle")
    cagent.http.post(f"/sessions/{s['id']}/stop")        # a new claude for the next turn, whose total starts over
    cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "hello"})
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle" and len(_costs(log)) == 2)
    assert _costs(log) == [(0.0123, "turn"), (0.0123, "turn")] and log["cost"] == 0.0246
    cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "again"})     # the same claude: its total goes on
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle" and len(_costs(log)) == 3)
    assert _costs(log)[2] == (0.0123, "turn") and log["cost"] == 0.0369
    assert len(_runs(cagent)) == 2
    # read again from its log, as after the agent starts again
    again = chats.Chat(s["id"], os.path.join(cagent.root, "sessions", s["id"]), cagent.root, {}, "/bin/true")
    assert round(again.cost, 6) == 0.0369


def test_a_log_from_before_counts_claude_s_running_totals_once(tmp_path):
    # a chat's log written before a result named its basis: each result's cost is claude's running total, which drops
    # when claude starts again; a result that says it is a turn's own is taken as it is
    folder = tmp_path / "s"
    folder.mkdir()
    old = [0.01, 0.02, 0.03, 0.005, 0.012]
    with open(folder / "chat.jsonl", "w") as fh:
        for i, cost in enumerate(old, 1):
            fh.write(json.dumps({"seq": i, "time": i, "kind": "result", "cost": cost}) + "\n")
        fh.write(json.dumps({"seq": 6, "time": 6, "kind": "result", "cost": 0.004, "basis": "turn"}) + "\n")
        fh.write(json.dumps({"seq": 7, "time": 7, "kind": "result", "cost": None, "basis": "turn"}) + "\n")
    c = chats.Chat("s", str(folder), str(tmp_path), {}, "/bin/true")
    assert round(c.cost, 6) == round(0.01 + 0.01 + 0.01 + 0.005 + 0.007 + 0.004, 6) and c.seq == 7
    assert c.seen == [(None, cost) for cost in old]           # kept for a claude that brings back a total


@pytest.mark.parametrize("config", [{"VERSION": "2.1.277", "RESTORES": True},
                                    {"VERSION": "2.1.277", "RESTORES": True, "EXTRA": 7}, {"EXTRA": 7},
                                    {"EXTRA_RESUMED": 500}, {"VERSION": "2.1.277", "EXTRA": 7}])
def test_a_claude_that_brings_back_its_session_s_spend_is_counted_once(cagent, tmp_path, config):
    # 2.1.277, which a workspace's hot update installs, brings a session's running total back when it resumes one it
    # left normally (2.1.274 starts over; both seen on 2026-09-29): a turn's own cost
    # is still what it added. With tokens counted apart from a turn's (a sub-agent's), with and without that, and on
    # 2.1.274 a resumed first turn whose sub-agent spent more than the whole chat before; and a
    # build that brings totals back but had nothing saved (an exit that was not normal), with a sub-agent's tokens
    (tmp_path / "bin" / "claude").write_text(f"#!{sys.executable}\nCONFIG = {config!r}\n" + open(STAND_IN).read())
    s = _chat(cagent)
    _until(cagent, s["id"], lambda log: log["state"] == "idle")
    cagent.http.post(f"/sessions/{s['id']}/stop")        # a normal exit: the stand-in saves the session's totals
    cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "hello"})
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle" and len(_costs(log)) == 2)
    cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "again"})
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle" and len(_costs(log)) == 3)
    assert _costs(log) == [(0.0123, "turn")] * 3 and log["cost"] == 0.0369
    totals = [e["total"] for e in log["events"] if e["kind"] == "result"]
    assert totals == ([0.0123, 0.0246, 0.0369] if config.get("RESTORES") else [0.0123, 0.0123, 0.0246])


def test_what_a_claude_brought_back_is_read_from_its_first_result(tmp_path):
    c = chats.Chat("s", str(tmp_path), str(tmp_path), {}, "/bin/true")
    c.claude_version = (2, 1, 277)
    assert c._base(0.01, 15, 15) == 0.0 and c._base(0.01, None, 15) == 0.0 and c._base(0.01, 15, None) == 0.0
    assert c._base(0.03, 15, 40) == 0.0                                  # nothing logged to have brought back
    c.seen = [(15, 0.01), (30, 0.02), (45, 0.03)]
    assert c._base(0.04, 15, 45) == 0.02                                 # exactly the session at its second result
    assert c._base(0.04, 15, 65) == 0.03                                 # more than the last: the last, and the rest apart
    assert c._base(0.04, 15, 50) == 0.0                                  # fewer than the last and no match: fresh
    assert c._base(0.02, 15, 65) == 0.0                                  # the last is more than the total: fresh
    c.seen = [(None, 0.05)]                                              # a log from before, with no tokens
    assert c._base(0.06, 15, 40) == 0.05 and c._base(0.04, 15, 40) == 0.0
    assert c._base(0.06, 15, 15) == 0.0                                  # all its tokens this turn's: fresh
    # a build seen not to bring anything back, and one whose version is not known yet
    c.seen = [(15, 0.01), (30, 0.02), (45, 0.03)]
    c.claude_version = (2, 1, 274)
    assert c._base(0.04, 15, 45) == 0.0 and c._base(0.04, 15, 65) == 0.0
    c.claude_version = None
    assert c._base(0.04, 15, 45) == 0.02


def test_a_chat_on_another_account_runs_its_claude_with_that_account_s_folder(cagent):
    # accounts.py: the chat's claude gets the account's CLAUDE_CONFIG_DIR through the chat's wrapper, which passes
    # nothing it does not name; a chat on the default account gets none
    account = cagent.http.post("/accounts", json={"kind": "claude", "name": "Work"}).json()
    s = cagent.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": "hello",
                                            "account": account["id"]}).json()
    assert s["account"] == account["id"]
    _until(cagent, s["id"], lambda log: log["state"] == "idle")
    assert "CLAUDE_CONFIG_DIR" in _runs(cagent)[-1]["env"]
    plain = _chat(cagent)
    _until(cagent, plain["id"], lambda log: log["state"] == "idle")
    assert "CLAUDE_CONFIG_DIR" not in _runs(cagent)[-1]["env"]
