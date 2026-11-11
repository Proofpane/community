"""The control window's map (sessionmap.py and the agent's GET /map and GET /sessions/{id}/map): every session
the page's list shows with its agents, and one session's calls and turns;
no message's text anywhere, and "missing", "version" or "no" where a source is not read, never a guess."""

import json
import os
import stat
import subprocess
import sys
import time

import pytest

from conftest import ROOT
from proofpane_workspace import activity, runtree, sessionmap, settings, subagentline, terminals, transcripts, usage
from workspace_fixtures import Agent, make_repo

SECRET = "the-words-of-a-message-must-never-reach-the-map"
PY, DIR = sys.executable, os.path.join(ROOT, "proofpane_workspace")


def _events(folder, *events):
    with open(os.path.join(folder, activity.AGENT_EVENTS), "a") as fh:
        for at, event, agent_id, kind in events:
            fh.write(json.dumps({"at": at, "event": event, "id": agent_id, "type": kind}) + "\n")


def _line(folder, rows, at=1):
    with open(os.path.join(folder, subagentline.FILE), "w") as fh:
        json.dump({"at": at, "agents": rows}, fh)


def _row(agent_id, **given):
    row = {"id": agent_id, "name": f"name {agent_id}", "type": "local_agent", "agent_type": "Explore",
           "status": "running", "started_at": 100, "model": "claude-opus-5-5", "effort": "high", "tokens": 10,
           "seen_at": 110, "ended_at": None}
    row.update(given)
    return row


def _by_id(folder):
    return {a["id"]: a for a in sessionmap.agents(str(folder))}


# level 4: the line's rows joined with the hook's starts and stops


def test_an_agent_the_line_shows_is_as_it_shows_it(tmp_path):
    _line(tmp_path, [_row("a1")])
    assert _by_id(tmp_path)["a1"] == {"id": "a1", "name": "name a1", "agent_type": "Explore", "status": "running",
                                      "started_at": 100, "ended_at": None, "tokens": 10,
                                      "model": "claude-opus-5-5", "effort": "high"}


def test_an_agent_only_the_hook_saw_runs_until_its_stop_and_then_ended_how_not_said(tmp_path):
    _events(tmp_path, (200, "start", "h1", "Plan"), (200, "start", "h2", None), (230, "stop", "h2", None))
    got = _by_id(tmp_path)
    assert got["h1"] == {"id": "h1", "name": None, "agent_type": "Plan", "status": "running", "started_at": 200,
                         "ended_at": None, "tokens": None, "model": None, "effort": None}
    assert got["h2"]["status"] is None and got["h2"]["ended_at"] == 230


def test_a_stop_no_start_came_for_is_one_of_claude_code_s_own_and_is_left_out(tmp_path):
    _events(tmp_path, (300, "stop", "prompt-suggestion", ""))
    assert _by_id(tmp_path) == {}


def test_a_stop_after_the_line_last_saw_it_running_ends_it(tmp_path):
    _line(tmp_path, [_row("a1", seen_at=110)])
    _events(tmp_path, (100, "start", "a1", "Explore"), (120, "stop", "a1", "Explore"))
    got = _by_id(tmp_path)["a1"]
    assert got["status"] is None and got["ended_at"] == 120


def test_the_line_s_end_is_kept_with_the_hook_s_time(tmp_path):
    _line(tmp_path, [_row("a1", status="completed", seen_at=130, ended_at=130)])
    _events(tmp_path, (100, "start", "a1", None), (125, "stop", "a1", None))
    got = _by_id(tmp_path)["a1"]
    assert got["status"] == "completed" and got["ended_at"] == 125
    _line(tmp_path, [_row("a2", status="failed", seen_at=130, ended_at=128)])
    assert _by_id(tmp_path)["a2"]["status"] == "failed" and _by_id(tmp_path)["a2"]["ended_at"] == 128


def test_a_start_after_the_line_saw_it_end_is_running_again(tmp_path):
    _line(tmp_path, [_row("a1", status="completed", seen_at=130, ended_at=130)])
    _events(tmp_path, (100, "start", "a1", None), (125, "stop", "a1", None), (140, "start", "a1", None))
    got = _by_id(tmp_path)["a1"]
    assert got["status"] == "running" and got["ended_at"] is None and got["started_at"] == 100


def test_the_hook_s_type_stands_in_where_the_line_has_none(tmp_path):
    _line(tmp_path, [_row("a1", agent_type=None)])
    _events(tmp_path, (100, "start", "a1", "code-reviewer"))
    assert _by_id(tmp_path)["a1"]["agent_type"] == "code-reviewer"


def test_a_session_s_agents_are_capped_running_first(tmp_path):
    rows = [_row(f"e{n}", status="completed", started_at=1000 + n, ended_at=2000) for n in range(80)]
    rows += [_row("r1", started_at=5), _row("r2", started_at=6)]
    _line(tmp_path, rows)
    got = sessionmap.agents(str(tmp_path))
    assert len(got) == sessionmap.MAX_AGENTS
    ids = [a["id"] for a in got]
    assert "r1" in ids and "r2" in ids and "e79" in ids and "e0" not in ids
    assert [a["started_at"] for a in got] == sorted(a["started_at"] for a in got)
    # the cap is said, never silent: how many it left out
    _given(tmp_path)
    row = sessionmap.session_row(_view(), str(tmp_path))
    assert row["agents"] == got and row["agents_more"] == 82 - sessionmap.MAX_AGENTS


def test_unreadable_lines_are_passed_over(tmp_path):
    with open(tmp_path / activity.AGENT_EVENTS, "w") as fh:
        fh.write("not json\n" + json.dumps({"at": "x", "event": "start", "id": "a"}) + "\n"
                 + json.dumps({"at": 5, "event": "begin", "id": "b"}) + "\n"
                 + json.dumps({"at": 5, "event": "start", "id": "../c"}) + "\n"
                 + json.dumps({"at": 5, "event": "start", "id": "ok"}) + "\n")
    assert list(_by_id(tmp_path)) == ["ok"]


# level 3: a session's row


def _view(**given):
    view = {"id": "0123456789ab", "program": "claude", "repo": "demo", "branch": "pp/0123456789ab", "task": "Fix it\nnow",
            "created_at": 1000, "running": True, "activity": {"state": "working", "at": 1500}, "account": None}
    view.update(given)
    return view


def _given(folder, version=(2, 1, 274)):
    with open(os.path.join(folder, sessionmap.MAP_SETTINGS), "w") as fh:
        json.dump(settings.with_map({}, PY, DIR, version), fh)


@pytest.mark.parametrize("view, state", [
    ({"program": "chat", "chat": {"state": "running"}}, "working"),
    ({"program": "chat", "chat": {"state": "needs_you"}}, "waiting"),
    ({"program": "chat", "chat": {"state": "idle", "asks": [{"id": 1}]}}, "waiting"),
    ({"program": "chat", "chat": {"state": "idle"}, "finished": {"at": 5}}, "done"),
    ({"program": "chat", "chat": {"state": "idle"}, "finished": {}}, "done"),
    ({"program": "chat", "chat": {"state": "error"}}, "failed"),
    ({"program": "chat", "chat": None}, "idle"),
    ({"running": True, "activity": {"state": "waiting"}}, "waiting"),
    ({"running": True, "activity": {"state": "idle"}}, "idle"),
    ({"running": True, "activity": None}, "running"),
    ({"running": True, "activity": {"state": "asleep"}}, "running"),
    ({"running": False, "finished": {"at": 1}, "exit_code": 1}, "done"),
    ({"running": False, "finished": {}, "exit_code": 1}, "done"),       # an empty object is true to JavaScript
    ({"running": False, "exit_code": 0}, "done"),
    ({"running": False, "exit_code": -15}, "stopped"),
    ({"running": False, "exit_code": 2}, "failed"),
    ({"running": False, "exit_code": None}, "stopped"),
])
def test_a_session_s_state_is_the_list_s(view, state):
    assert sessionmap.state_of(_view(**view)) == state


def test_the_list_s_state_function_is_the_one_written_out():
    # the page's sessionState, whose branches state_of follows one for one: a change there shows here
    text = open(os.path.join(ROOT, "ui", "src", "store.ts"), encoding="utf-8").read()
    start = text.index("export function sessionState(")
    body = text[start:text.index("\n}\n", start)]
    for said in ('"working"', '"waiting"', '"done"', '"failed"', '"idle"', '"running"', '"stopped"', "exit_code < 0",
                 "r.finished", 'a === "working"', "asks?.length"):
        assert said in body, said
    assert body.count("return") == 9


@pytest.mark.parametrize("view, out", [
    ({"closed": True}, True), ({"closed": []}, True), ({"closed": {}}, True), ({"closed": 0}, False),
    ({"program": "claude-sign-in", "sign_in": True, "running": True}, True),
    ({"program": "github", "running": False}, True), ({"program": "codex-sign-in", "running": True}, False),
    ({"program": "github", "running": []}, False),                    # an empty list is true to JavaScript
    ({"program": "shell", "running": False}, False), ({}, False),
])
def test_a_session_the_list_leaves_out_the_map_leaves_out(view, out):
    assert sessionmap.left_out(_view(**view)) is out


def test_a_claude_session_s_row(tmp_path):
    (tmp_path / "status.json").write_text(json.dumps({"at": 5, "model": "Opus 5.5", "effort": "max",
                                                      "context_tokens": 724500, "context_used": 70}))
    _given(tmp_path)
    _line(tmp_path, [_row("a1")])
    row = sessionmap.session_row(_view(), str(tmp_path))
    assert row == {"id": "0123456789ab", "title": "Fix it", "program": "claude", "repo": "demo",
                   "branch": "pp/0123456789ab", "account": {"kind": "claude", "id": "default"}, "model": "Opus 5.5",
                   "effort": "max", "state": "working", "started_at": 1000, "updated_at": 1500,
                   "context_tokens": 724500, "agents_read": "yes", "agents": sessionmap.agents(str(tmp_path)),
                   "agents_more": 0}
    assert [a["id"] for a in row["agents"]] == ["a1"]


@pytest.mark.parametrize("version, read", [((2, 1, 274), "yes"), ((2, 1, 214), "yes"), ((2, 1, 213), "version")])
def test_agents_are_read_where_the_session_s_start_gave_the_line(tmp_path, version, read):
    _given(tmp_path, version)
    _line(tmp_path, [_row("a1")])
    row = sessionmap.session_row(_view(), str(tmp_path))
    assert row["agents_read"] == read and (row["agents"] != []) is (read == "yes")


def test_a_session_given_none_of_the_sources_says_missing_never_a_guess(tmp_path):
    _line(tmp_path, [_row("a1")])                       # a file there, but its start gave none: not read
    row = sessionmap.session_row(_view(), str(tmp_path))
    assert row["agents_read"] == "missing" and row["agents"] == []
    chat = sessionmap.session_row(_view(program="chat", chat={"state": "idle", "chosen": {"model": "opus",
                                                                                          "effort": "xhigh"}}),
                                  str(tmp_path))
    assert chat["agents_read"] == "missing" and chat["agents"] == [] and chat["model"] == "opus"
    assert chat["effort"] == "xhigh" and chat["context_tokens"] is None


@pytest.mark.parametrize("program", ["codex", "shell", "gemini", "custom", "opencode"])
def test_a_session_that_is_not_claude_code_s_says_no(tmp_path, program):
    _given(tmp_path)
    _line(tmp_path, [_row("a1")])
    row = sessionmap.session_row(_view(program=program), str(tmp_path))
    assert row["agents_read"] == "no" and row["agents"] == [] and row["model"] is None


@pytest.mark.parametrize("view, account", [
    ({}, {"kind": "claude", "id": "default"}), ({"account": "acct1"}, {"kind": "claude", "id": "acct1"}),
    ({"program": "chat"}, {"kind": "claude", "id": "default"}), ({"program": "codex", "account": "c2"},
                                                                  {"kind": "codex", "id": "c2"}),
    ({"model_provider": "deepseek"}, None), ({"program": "shell"}, None), ({"program": "gemini"}, None),
])
def test_a_session_s_account_is_its_own(tmp_path, view, account):
    assert sessionmap.session_row(_view(**view), str(tmp_path))["account"] == account


def test_a_row_s_missing_values_are_null_never_left_out(tmp_path):
    row = sessionmap.session_row({"id": "0123456789ab", "program": "shell"}, str(tmp_path))
    assert set(row) == {"id", "title", "program", "repo", "branch", "account", "model", "effort", "state",
                        "started_at", "updated_at", "context_tokens", "agents_read", "agents", "agents_more"}
    assert row["title"] == "Shell" and row["repo"] is None and row["model"] is None and row["updated_at"] is None
    assert row["agents"] == [] and row["agents_more"] == 0


# level 5 and the turns


def _calls(folder, *calls):
    with open(os.path.join(folder, activity.CALLS), "a") as fh:
        for c in calls:
            fh.write((c if isinstance(c, str) else json.dumps(c)) + "\n")


def test_calls_are_kept_under_the_agent_each_was_made_in(tmp_path):
    _given(tmp_path)
    _events(tmp_path, (100, "start", "a1", "Explore"))
    _calls(tmp_path, {"at": 101, "agent": None, "tool": "Read", "target": "/w/a.py", "failed": False},
           {"at": 102, "agent": "a1", "tool": "Bash", "target": "pytest", "failed": True},
           {"at": 103, "agent": "a9", "tool": "Grep", "target": "", "failed": False},
           "not json", {"at": 104, "agent": "../x", "tool": "Read"}, {"at": 104, "tool": "rm -rf /"})
    got = sessionmap.session_calls("0123456789ab", "claude", str(tmp_path), str(tmp_path / "w"), None, None, now=7)
    assert got["at"] == 7 and got["session"] == "0123456789ab" and got["calls_read"] == "yes"
    assert got["main"] == {"calls": [{"tool": "Read", "target": "/w/a.py", "failed": False, "at": 101}], "more": 0}
    assert got["agents"] == [{"id": "a1", "calls": [{"tool": "Bash", "target": "pytest", "failed": True, "at": 102}],
                              "more": 0},
                             {"id": "a9", "calls": [{"tool": "Grep", "target": None, "failed": False, "at": 103}],
                              "more": 0}]
    assert got["turns_read"] == "missing" and got["turns"] == [] and got["cut"] is False


@pytest.mark.parametrize("program, given, read", [("claude", None, "missing"), ("chat", None, "missing"),
                                                  ("codex", (2, 1, 274), "no"), ("claude", (2, 1, 68), "version")])
def test_calls_not_given_are_said_not_read(tmp_path, program, given, read):
    if given:
        _given(tmp_path, given)
    _calls(tmp_path, {"at": 101, "agent": None, "tool": "Read", "target": "/w/a.py", "failed": False})
    got = sessionmap.session_calls("0123456789ab", program, str(tmp_path), str(tmp_path), None, None)
    assert got["calls_read"] == read and got["main"] == {"calls": [], "more": 0} and got["agents"] == []
    assert got["turns_read"] == ("no" if program == "codex" else "missing")


def test_an_answer_past_64_kb_is_cut_its_newest_calls_kept_the_rest_counted(tmp_path):
    _given(tmp_path)
    _calls(tmp_path, *[{"at": 1000 + n, "agent": None, "tool": "Read", "target": f"/w/{n:05d}/" + "p" * 40,
                        "failed": False} for n in range(3000)])
    got = sessionmap.session_calls("0123456789ab", "claude", str(tmp_path), str(tmp_path), None, None)
    assert len(json.dumps(got)) <= sessionmap.MAX_ANSWER and got["cut"] is True
    kept = got["main"]["calls"]
    assert kept[-1]["at"] == 3999 and got["main"]["more"] == 3000 - len(kept) > 0
    assert [c["at"] for c in kept] == list(range(4000 - len(kept), 4000))


def _transcript(path, *rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}, "timestamp": "2026-10-17T10:00:00Z"}


def _reply(message_id, text, **usage_given):
    usage_ = {"input_tokens": 3, "output_tokens": 50, "cache_read_input_tokens": 1000,
              "cache_creation_input_tokens": 200}
    usage_.update(usage_given)
    return {"type": "assistant", "timestamp": "2026-10-17T10:00:01Z",
            "message": {"id": message_id, "role": "assistant", "usage": usage_,
                        "content": [{"type": "text", "text": text},
                                    {"type": "tool_use", "id": "toolu_" + message_id, "name": "Bash",
                                     "input": {"command": f"echo {SECRET}"}}]}}


def _result(tool_id, text):
    return {"type": "user", "timestamp": "2026-10-17T10:00:02Z",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


ROWS = [_user(f"first prompt {SECRET}"), _reply("m1", f"reply {SECRET}"), _reply("m1", f"reply {SECRET} again"),
        _result("toolu_m1", f"output {SECRET}"), _reply("m2", "done"),
        {"type": "user", "isCompactSummary": True, "message": {"content": f"summary {SECRET}"}},
        _user([{"type": "text", "text": f"second {SECRET}"}]), _reply("m3", "ok", input_tokens=7),
        _user([{"type": "image", "source": {}}])]


def test_turns_are_counted_as_runtree_counts_a_run_s(tmp_path):
    path = tmp_path / "t.jsonl"
    _transcript(str(path), *ROWS)
    walked = runtree._walk(str(path))[0]
    mine = sessionmap.walk(list(sessionmap._rows(path.read_bytes(), False)))
    assert [t["requests"] for t in mine] == [t["requests"] for t in walked] == [2, 1]
    assert [t["output_tokens"] for t in mine] == [t["tokens"]["output_tokens"] for t in walked]
    assert [t["input_tokens"] for t in mine] == [
        sum(t["tokens"][f] for f in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        for t in walked] == [2406, 1207]


def test_a_long_transcript_is_read_from_its_end_and_said_cut(tmp_path, monkeypatch):
    work, claude_dir = tmp_path / "w", tmp_path / "claude"
    work.mkdir()
    folder = claude_dir / "projects" / usage.slug(os.path.realpath(work))
    _transcript(str(folder / "conv-1.jsonl"), *(ROWS * 40))
    monkeypatch.setattr(sessionmap, "MAX_TRANSCRIPT", 4096)
    sessionmap._turns_kept.clear()
    got, cut = sessionmap.turns(str(work), str(claude_dir), "conv-1")
    assert cut is True and 0 < sum(len(t) for _, t in got) < 80
    monkeypatch.setattr(sessionmap, "MAX_TRANSCRIPT", 4 * 1024 * 1024)
    sessionmap._turns_kept.clear()
    got, cut = sessionmap.turns(str(work), str(claude_dir), "conv-1")
    assert cut is False and len(got[0][1]) == 80


def test_sub_agents_files_past_the_ones_read_are_said_cut(tmp_path, monkeypatch):
    # transcripts.files takes the first MAX_SUBAGENTS by name: those past it are left out, and `cut` says so; at the
    # cap, none left out, it does not
    work, claude_dir = tmp_path / "w", tmp_path / "claude"
    work.mkdir()
    folder = claude_dir / "projects" / usage.slug(os.path.realpath(work))
    _transcript(str(folder / "conv-3.jsonl"), *ROWS)
    for n in range(4):
        _transcript(str(folder / "conv-3" / "subagents" / f"agent-s{n}.jsonl"), _user("go"), _reply(f"s{n}", "ok"))
    monkeypatch.setattr(transcripts, "MAX_SUBAGENTS", 4)
    sessionmap._turns_kept.clear()
    got, cut = sessionmap.turns(str(work), str(claude_dir), "conv-3")
    assert cut is False and [a for a, _ in got] == [None, "s0", "s1", "s2", "s3"]
    monkeypatch.setattr(transcripts, "MAX_SUBAGENTS", 3)
    got, cut = sessionmap.turns(str(work), str(claude_dir), "conv-3")
    assert cut is True and [a for a, _ in got] == [None, "s0", "s1", "s2"]
    assert transcripts.subagents_left_out(str(work), str(claude_dir), "conv-3") == 1


def test_a_transcript_is_read_again_only_once_it_changed(tmp_path, monkeypatch):
    path = tmp_path / "t.jsonl"
    _transcript(str(path), *ROWS)
    sessionmap._turns_kept.clear()
    read = []
    real = sessionmap._tail
    monkeypatch.setattr(sessionmap, "_tail", lambda p, most: read.append(p) or real(p, most))
    first = sessionmap._turns_of(str(path))
    assert sessionmap._turns_of(str(path)) == first and len(read) == 1
    with open(path, "a") as fh:
        fh.write(json.dumps(_user("third")) + "\n")
    assert len(sessionmap._turns_of(str(path))[0]) == 3 and len(read) == 2


def test_a_fifo_or_a_link_is_never_read(tmp_path):
    os.mkfifo(tmp_path / activity.CALLS)                # never waited on
    os.symlink(tmp_path / "x", tmp_path / sessionmap.MAP_SETTINGS)
    (tmp_path / "x").write_text(json.dumps(settings.with_map({}, PY, DIR, (2, 1, 274))))
    # a read that waited would fail here, not hang
    got = within(5, sessionmap.session_calls, "0123456789ab", "claude", str(tmp_path), str(tmp_path), None, None)
    assert got["calls_read"] == "missing" and got["main"]["calls"] == []
    assert within(5, sessionmap._tail, str(tmp_path / activity.CALLS), 100) == (None, False)


# the routes, through a real agent, a stand-in claude in place of Claude Code


STAND_IN = r'''#!{python}
import json, os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1:2] in (["-v"], ["--version"]):
    print("2.1.274 (Claude Code)")
    sys.exit(0)
# a real Claude Code begins its first agent seconds after it starts, and the map takes a sighting in the second a run
# started as an earlier run's (Workspace.map_runs): this one reports its agents from the next second
time.sleep(1.05 - time.time() % 1)
folder = os.environ["PP_SESSION_DIR"]
made = json.load(open(sys.argv[sys.argv.index("--settings") + 1]))
inputs = json.load(open(os.path.join(here, "INPUTS")))
for line in (inputs["status"], ):
    subprocess.run(made["statusLine"]["command"] if "statusLine" in made else "true", shell=True, input=json.dumps(line),
                   text=True, capture_output=True, timeout=30)
subprocess.run(made["subagentStatusLine"]["command"], shell=True, input=json.dumps(inputs["agents"]), text=True,
               capture_output=True, timeout=30)
for event in inputs["events"]:
    for group in made["hooks"].get(event["hook_event_name"], []):
        for hook in group["hooks"]:
            subprocess.run([hook["command"], *hook["args"]], input=json.dumps(event), text=True, capture_output=True,
                           timeout=30)
open(os.path.join(folder, "done"), "w").write("1")
time.sleep(600)
'''


def _inputs():
    status = {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"}, "effort": {"level": "max"},
              "context_window": {"used_percentage": 72, "total_input_tokens": 724500, "total_output_tokens": 9},
              "session_id": SECRET, "transcript_path": f"/x/{SECRET}.jsonl", "cwd": f"/{SECRET}"}
    agents = {"session_id": SECRET, "transcript_path": f"/{SECRET}", "columns": 80, "tasks": [
        {"id": "agent-a1", "name": "reviewer", "type": "local_agent", "agentType": "Explore", "status": "running",
         "description": "Look for the parser", "label": SECRET, "startTime": 1760700000000, "model": "claude-opus-5-5",
         "effort": "high", "tokenCount": 200600, "tokenSamples": [1], "cwd": f"/{SECRET}"},
        {"id": "agent-b2", "type": "local_agent", "agentType": "Plan", "status": "completed",
         "description": "Plan the change " + "z" * 200, "label": SECRET, "startTime": 1760700001000, "tokenCount": 5,
         "cwd": "/w"}]}
    events = [
        {"hook_event_name": "SubagentStart", "agent_id": "agent-a1", "agent_type": "Explore", "session_id": SECRET},
        {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/w/src/a.py"},
         "tool_response": {"content": SECRET}},
        {"hook_event_name": "PostToolUse", "tool_name": "Bash", "agent_id": "agent-a1", "agent_type": "Explore",
         "tool_input": {"command": f"grep {SECRET} .", "description": SECRET}, "tool_response": {"stdout": SECRET}},
        {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "agent_id": "agent-a1",
         "tool_input": {"command": f"pytest -k {SECRET}"}, "error": SECRET},
        # commands whose words a shell splits otherwise than shlex: each named an argument
        *[{"hook_event_name": "PostToolUse", "tool_name": "Bash", "agent_id": "agent-b2", "tool_input": {"command": c}}
          for c in (f'AUTH=$(printf "admin:{SECRET}" | base64); curl -H "Authorization: Basic $AUTH" https://x',
                    f"TOKEN=$(echo '{SECRET}') && gh api user", f"KEY=abc;mysql -p{SECRET} -u root",
                    f"HOOK=$(curl https://hooks.example.test/services/T000/B000/{SECRET}) && echo ok",
                    f"PASS=$'pa\\' {SECRET} x' mysql -u root", f"A=${{B:- {SECRET} }} mysql -u root")],
        {"hook_event_name": "SubagentStop", "agent_id": "agent-b2", "agent_type": "Plan",
         "last_assistant_message": SECRET, "agent_transcript_path": f"/{SECRET}"},
        {"hook_event_name": "SubagentStop", "agent_id": "prompt-suggestion", "agent_type": "",
         "last_assistant_message": SECRET}]
    return {"status": status, "agents": agents, "events": events}


@pytest.fixture
def mapped(tmp_path):
    bin_dir, home = tmp_path / "bin", tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(STAND_IN.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    (bin_dir / "INPUTS").write_text(json.dumps(_inputs()))
    gate = tmp_path / "gate-settings.json"
    gate.write_text(json.dumps(settings.claude_settings(PY, DIR, managed=False)))
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                                                "PP_WORKSPACE_CLAUDE_SETTINGS": str(gate)})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    try:
        claude = a.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "Map the parser"}).json()
        folder = tmp_path / "ws" / "sessions" / claude["id"]
        deadline = time.time() + 30
        while not (folder / "done").exists():
            assert time.time() < deadline, "the stand-in did not run its hooks"
            time.sleep(0.05)
        # what Claude Code writes of the conversation, and the conversation its hooks named
        conversation = "c0ffee-1"
        work = os.path.realpath(folder / "work")
        _transcript(str(home / ".claude" / "projects" / usage.slug(work) / f"{conversation}.jsonl"), *ROWS)
        _transcript(str(home / ".claude" / "projects" / usage.slug(work) / conversation / "subagents"
                        / "agent-agent-a1.jsonl"), _user(f"sub prompt {SECRET}"), _reply("s1", f"sub reply {SECRET}"))
        activity.record("working", {"hook_event_name": "UserPromptSubmit", "session_id": conversation},
                        directory=str(folder))
        shell = a.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "A shell"}).json()
        closed = a.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Closed"}).json()
        assert a.http.post(f"/sessions/{closed['id']}/close").status_code == 200
        yield a, claude, shell, closed, folder
    finally:
        a.close()


def test_get_map_lists_the_sessions_with_their_agents(mapped):
    a, claude, shell, closed, folder = mapped
    got = a.http.get("/map").json()
    assert isinstance(got["at"], int) and abs(got["at"] - time.time()) < 60
    rows = {r["id"]: r for r in got["sessions"]}
    assert set(rows) == {claude["id"], shell["id"]}                 # the closed one left out, as the list leaves it
    row = rows[claude["id"]]
    assert {k: row[k] for k in ("title", "program", "repo", "branch", "account", "model", "effort", "state",
                                "context_tokens", "agents_read")} == {
        "title": "Map the parser", "program": "claude", "repo": "demo", "branch": f"pp/{claude['id']}",
        "account": {"kind": "claude", "id": "default"}, "model": "Opus 5.5", "effort": "max", "state": "working",
        "context_tokens": 724500, "agents_read": "yes"}
    agents = {x["id"]: x for x in row["agents"]}
    assert set(agents) == {"agent-a1", "agent-b2"}                  # Claude Code's own agent's stop left out
    assert agents["agent-a1"] == {"id": "agent-a1", "name": "reviewer", "agent_type": "Explore", "status": "running",
                                  "started_at": 1760700000, "ended_at": None, "tokens": 200600,
                                  "model": "claude-opus-5-5", "effort": "high"}
    assert agents["agent-b2"]["name"] == ("Plan the change " + "z" * 200)[:80]
    assert agents["agent-b2"]["status"] == "completed" and isinstance(agents["agent-b2"]["ended_at"], int)
    assert rows[shell["id"]]["agents_read"] == "no" and rows[shell["id"]]["account"] is None


def test_get_session_map_gives_its_calls_and_turns(mapped):
    a, claude, shell, closed, folder = mapped
    got = a.http.get(f"/sessions/{claude['id']}/map").json()
    assert got["session"] == claude["id"] and got["calls_read"] == "yes" and got["turns_read"] == "yes"
    assert [(c["tool"], c["target"], c["failed"]) for c in got["main"]["calls"]] == [("Read", "/w/src/a.py", False)]
    by_agent = {x["id"]: x for x in got["agents"]}
    assert [(c["tool"], c["target"], c["failed"]) for c in by_agent["agent-a1"]["calls"]] == [
        ("Bash", "grep", False), ("Bash", "pytest", True)]
    assert got["turns"] == [{"agent": None, "requests": 2, "input_tokens": 2406, "output_tokens": 100},
                            {"agent": None, "requests": 1, "input_tokens": 1207, "output_tokens": 50},
                            {"agent": "agent-a1", "requests": 1, "input_tokens": 1203, "output_tokens": 50}]
    assert got["cut"] is False
    assert a.http.get(f"/sessions/{shell['id']}/map").json()["calls_read"] == "no"
    assert a.http.get("/sessions/0123456789ab/map").status_code == 404
    assert a.http.get("/sessions/not-an-id/map").status_code in (400, 404)


def test_no_message_s_text_reaches_either_route(mapped):
    a, claude, shell, closed, folder = mapped
    # it is in the transcript, the status line's input, a row's label, a call's arguments and response, a failure's
    # error and a sub-agent's last message: never in what the map gives
    home = folder.parent.parent.parent / "home"
    assert any(SECRET in path.read_text() for path in home.joinpath(".claude").rglob("*.jsonl"))
    assert SECRET in json.dumps(_inputs())
    for answer in (a.http.get("/map").text, a.http.get(f"/sessions/{claude['id']}/map").text):
        assert SECRET not in answer
        assert "last_assistant_message" not in answer and "label" not in answer
    # nor a part of it as a call's target: a command whose words a shell may split otherwise names nothing
    calls = {x["id"]: x for x in a.http.get(f"/sessions/{claude['id']}/map").json()["agents"]}["agent-b2"]["calls"]
    assert [(c["tool"], c["target"]) for c in calls] == [("Bash", None)] * 6


def test_an_answer_whose_first_cut_is_still_past_64_kb_is_cut_again(tmp_path):
    # 400 calls of 120-character targets are past 64 KB: the cut halves again until the answer fits
    _given(tmp_path)
    _calls(tmp_path, *[{"at": 1000 + n, "agent": None, "tool": "Read", "target": f"/w/{n:05d}" + "p" * 111,
                        "failed": False} for n in range(2000)])
    full = [c for c in sessionmap.calls(str(tmp_path))]
    assert len(full) == 2000 and len(json.dumps(full[-400:])) > sessionmap.MAX_ANSWER
    got = sessionmap.session_calls("0123456789ab", "claude", str(tmp_path), str(tmp_path), None, None)
    kept = got["main"]["calls"]
    assert len(json.dumps(got)) <= sessionmap.MAX_ANSWER and len(kept) < 400 and got["cut"] is True
    assert got["main"]["more"] == 2000 - len(kept) and kept[-1]["at"] == 2999


# an agent's end, the answer's bound, and what a session's programs can write where the map reads


def test_an_agent_of_a_program_that_ended_is_not_shown_running(tmp_path):
    # its program's end ends its agents, with no SubagentStop and no row from the line again
    _line(tmp_path, [_row("a1", seen_at=110), _row("a2", seen_at=200)])
    _events(tmp_path, (300, "start", "h1", "Plan"))
    stopped = {a["id"]: a for a in sessionmap.agents(str(tmp_path), live_since=None, stopped_at=250)}
    assert stopped["a1"]["status"] is None and stopped["a1"]["ended_at"] == 250
    assert stopped["a2"]["status"] is None and stopped["a2"]["ended_at"] == 250
    assert stopped["h1"]["status"] is None and stopped["h1"]["ended_at"] is None      # seen after the end it is given
    unknown = sessionmap.agents(str(tmp_path), live_since=None, stopped_at=None)
    assert all(a["status"] is None and a["ended_at"] is None for a in unknown)


def test_an_agent_seen_only_before_this_run_of_its_program_is_not_shown_running(tmp_path):
    _line(tmp_path, [_row("old", seen_at=110), _row("new", seen_at=220)])
    got = {a["id"]: a for a in sessionmap.agents(str(tmp_path), live_since=200)}
    assert got["old"]["status"] is None and got["old"]["ended_at"] is None
    assert got["new"]["status"] == "running"


def test_a_stop_in_the_second_the_line_saw_it_running_ends_it(tmp_path):
    _line(tmp_path, [_row("a1", seen_at=120)])
    _events(tmp_path, (100, "start", "a1", None), (120, "stop", "a1", None))
    got = _by_id(tmp_path)["a1"]
    assert got["status"] is None and got["ended_at"] == 120


def test_a_run_the_line_has_not_seen_is_the_hook_s_whatever_the_line_last_said(tmp_path):
    _line(tmp_path, [_row("a1", status="completed", seen_at=130, ended_at=130)])
    _events(tmp_path, (100, "start", "a1", None), (125, "stop", "a1", None), (140, "start", "a1", None),
            (150, "stop", "a1", None))
    got = _by_id(tmp_path)["a1"]
    assert got["status"] is None and got["ended_at"] == 150             # the run since: ended, how not said


def test_the_answer_is_bounded_however_many_agents_and_whatever_their_text(tmp_path):
    _given(tmp_path)
    long_id = "a" * 100
    _events(tmp_path, *[(100 + n, "start", f"{long_id}{n:03d}", "Explore") for n in range(sessionmap.MAX_CALL_AGENTS)])
    _calls(tmp_path, *[{"at": 1000 + n, "agent": f"{long_id}{n:03d}", "tool": "Read",
                        "target": "/w/" + "\u00e9\u4e2d" * 50, "failed": False}
                       for n in range(sessionmap.MAX_CALL_AGENTS)])
    got = sessionmap.session_calls("0123456789ab", "claude", str(tmp_path), str(tmp_path), None, None)
    assert sessionmap.size(got) <= sessionmap.MAX_ANSWER and got["cut"] is True
    assert len(got["agents"]) < sessionmap.MAX_CALL_AGENTS


def test_calls_of_agents_past_the_last_one_listed_are_said_cut(tmp_path):
    _given(tmp_path)
    _calls(tmp_path, *[{"at": 1000 + n, "agent": f"x{n}", "tool": "Read", "target": "/w/a", "failed": False}
                       for n in range(sessionmap.MAX_CALL_AGENTS + 5)])
    got = sessionmap.session_calls("0123456789ab", "claude", str(tmp_path), str(tmp_path), None, None)
    assert len(got["agents"]) == sessionmap.MAX_CALL_AGENTS and got["cut"] is True


def test_a_call_that_names_nothing_has_a_null_target(tmp_path):
    _given(tmp_path)
    _calls(tmp_path, {"at": 5, "agent": None, "tool": "Grep", "target": "", "failed": False})
    got = sessionmap.session_calls("0123456789ab", "claude", str(tmp_path), str(tmp_path), None, None)
    assert got["main"]["calls"] == [{"tool": "Grep", "target": None, "failed": False, "at": 5}]


@pytest.mark.parametrize("view", [{"task": ["a list"]}, {"title": 5}, {"program": ["claude"]}, {"repo": {"x": 1}},
                                  {"activity": "working"}])
def test_a_record_its_programs_wrote_out_of_shape_still_gives_a_row(tmp_path, view):
    row = sessionmap.session_row(_view(**view), str(tmp_path))
    assert isinstance(row["title"], (str, type(None))) and isinstance(row["repo"], (str, type(None)))
    assert row["agents_read"] in ("yes", "version", "missing", "no")


def test_a_record_out_of_shape_leaves_the_list_and_the_map_to_the_other_sessions(mapped):
    # a session's programs can write its record: one holding [], or a created_at of "soon",
    # failed the list and the map for every session
    a, claude, shell, closed, folder = mapped
    broken = folder.parent / "0123456789ab"
    broken.mkdir()
    (broken / "session.json").write_text("[]")
    record_path = folder.parent / closed["id"] / "session.json"
    record = json.loads(record_path.read_text())
    record_path.write_text(json.dumps(dict(record, created_at="soon")))
    # a record that cannot be read: a link to itself, one its programs took the reading of away, one nested too deep
    # (each failed both for every session)
    looped, denied, deep = (folder.parent / n for n in ("0123456789ae", "0123456789af", "0123456789b0"))
    for made in (looped, denied, deep):
        made.mkdir()
    os.symlink(looped / "session.json", looped / "session.json")
    (denied / "session.json").write_text(json.dumps(dict(record, id=denied.name)))
    (denied / "session.json").chmod(0)
    (deep / "session.json").write_text("[" * 100_000 + "]" * 100_000)
    # and one that names another session's id: listed as its own folder's, and never written as the other
    planted = folder.parent / "0123456789b1"
    planted.mkdir()
    shell_record = json.loads((folder.parent / shell["id"] / "session.json").read_text())
    (planted / "session.json").write_text(json.dumps(dict(shell_record, id=claude["id"], title="Planted")))
    readable = {denied.name} if os.geteuid() == 0 else set()        # root reads a record whatever its mode
    listed = a.http.get("/sessions")
    assert listed.status_code == 200
    ids = [x["id"] for x in listed.json()["sessions"]]
    assert set(ids) == {claude["id"], shell["id"], closed["id"], planted.name} | readable
    assert ids[-1] == closed["id"] and len(ids) == len(set(ids))   # after the others, and no id twice
    mapped_ids = [r["id"] for r in a.http.get("/map").json()["sessions"]]
    # the record root reads whatever its mode copies the closed session's, which the map leaves out as closed
    assert sorted(mapped_ids) == sorted({claude["id"], shell["id"], planted.name})
    assert a.http.post(f"/sessions/{planted.name}/title", json={"title": "Renamed"}).status_code == 200
    mine = json.loads((folder / "session.json").read_text())        # the session whose id was planted, untouched
    assert mine.get("title") != "Renamed" and mine["task"] == "Map the parser"
    assert json.loads((planted / "session.json").read_text())["title"] == "Renamed"
    (denied / "session.json").chmod(0o600)


def within(seconds, fn, *args):
    """fn(*args) in a thread of its own, its answer, or a failure once `seconds` pass with none (a read that waits on a
    FIFO would otherwise hold the whole run, never fail it): the thread is left behind, a daemon, and ends with the
    run."""
    import threading
    got = []
    thread = threading.Thread(target=lambda: got.append(fn(*args)), daemon=True)
    thread.start()
    thread.join(seconds)
    assert got, f"{getattr(fn, '__name__', fn)} did not answer within {seconds} s"
    return got[0]


def test_read_json_never_waits_on_a_fifo_nor_follows_a_link(tmp_path):
    os.mkfifo(tmp_path / "activity.json")
    assert within(5, sessionmap.read_json, str(tmp_path / "activity.json"), 1024) is None
    (tmp_path / "x.json").write_text("{}")
    os.symlink(tmp_path / "x.json", tmp_path / "linked.json")
    assert sessionmap.read_json(str(tmp_path / "linked.json"), 1024) is None
    (tmp_path / "big.json").write_text(json.dumps({"a": "b" * 2000}))
    assert sessionmap.read_json(str(tmp_path / "big.json"), 1024) is None
    assert sessionmap.read_json(str(tmp_path / "x.json"), 1024) == {}


def test_a_session_whose_program_stopped_shows_no_agent_running_and_its_fifos_hang_nothing(mapped):
    a, claude, shell, closed, folder = mapped
    assert a.http.post(f"/sessions/{claude['id']}/stop").status_code == 200
    row = {r["id"]: r for r in a.http.get("/map").json()["sessions"]}[claude["id"]]
    assert row["state"] in ("stopped", "failed", "done") and row["agents"]
    assert all(x["status"] != "running" for x in row["agents"]), row["agents"]
    # the agent that was running ended with its program: its end is the program's, where that is known
    ended = a.http.get(f"/sessions/{claude['id']}").json()["ended_at"]
    a1 = {x["id"]: x for x in row["agents"]}["agent-a1"]
    assert isinstance(ended, (int, float)) and a1["ended_at"] == int(ended) and a1["status"] is None
    # a session's own program can put a FIFO where its files go: the map is still answered, at once
    for name in ("status.json", "activity.json", "subagents.json", activity.CALLS, sessionmap.MAP_SETTINGS):
        path = folder / name
        if path.exists():
            path.unlink()
        os.mkfifo(path)
    shell_activity = folder.parent / shell["id"] / "activity.json"     # a running session's, which the list reads too
    if shell_activity.exists():
        shell_activity.unlink()
    os.mkfifo(shell_activity)
    started = time.monotonic()
    assert a.http.get("/map", timeout=20).status_code == 200
    assert a.http.get(f"/sessions/{claude['id']}/map", timeout=20).status_code == 200
    assert time.monotonic() - started < 15


def test_a_run_s_start_is_given_to_the_map_with_its_fraction(tmp_path, monkeypatch):
    # what the route test shows end to end, held here whatever the machine's load (the route
    # test skips when no start lands in the second it was asked in)
    from proofpane_workspace.workspace import Workspace as Space

    class Running:
        running, started, started_at = True, 1000.5, 1000

    class Ended:
        running, started, started_at = False, 900.25, 900
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    ws.terminals = {"0123456789ab": Running(), "ba9876543210": Ended()}
    views = [{"id": "0123456789ab", "ended_at": None}, {"id": "ba9876543210", "ended_at": 950.7}]
    assert ws.map_runs(views) == {"0123456789ab": (1000.5, None), "ba9876543210": (None, 950)}
    # an agent last seen in the second the run started is the earlier run's; one seen in the next, this run's
    _line(tmp_path, [_row("old", seen_at=1000), _row("new", seen_at=1001)])
    got = {a["id"]: a["status"] for a in sessionmap.agents(str(tmp_path), live_since=1000.5)}
    assert got == {"old": None, "new": "running"}


def test_a_terminal_s_start_is_taken_before_its_program_starts(tmp_path, monkeypatch):
    import asyncio
    stamp = tmp_path / "child"
    called = []                                         # when the program is started: its start is taken before
    real = terminals.subprocess.Popen

    def popen(argv, *args, **kwargs):
        if terminals._SHIM in argv:                     # the terminal's own, never another thread's program
            called.append(time.time())
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(terminals.subprocess, "Popen", popen)

    async def run():
        before = time.time()
        program = f"import time; open({str(stamp)!r}, 'w').write(repr(time.time()))"
        term = terminals.Terminal([sys.executable, "-c", program], str(tmp_path),
                                  dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        deadline = time.monotonic() + 20
        while term.running:
            assert time.monotonic() < deadline, "the program did not end"
            await asyncio.sleep(0.05)
        return before, term.started, term.started_at
    before, started, started_at = asyncio.run(run())
    # a time with its fraction, taken at the start (a whole second would be at or before `before`, the second it
    # was asked in, and would not tell an earlier run's sighting in that second from this run's)
    assert isinstance(started, float) and before <= started <= time.time() and started_at == int(started)
    assert len(called) == 1 and started <= called[0]
    assert started <= float(stamp.read_text())          # nothing the program does is stamped before its run started


def test_an_agent_left_running_in_an_earlier_run_is_not_running_in_the_next(mapped):
    # start, stop, start again: an agent the line last saw running before this run, even in
    # the very second this run started (its sightings are whole seconds), is not this run's; one seen since is
    a, claude, shell, closed, folder = mapped
    sid = claude["id"]
    for attempt in range(5):
        assert a.http.post(f"/sessions/{sid}/stop").status_code == 200
        (folder / "done").unlink(missing_ok=True)
        time.sleep(1.02 - time.time() % 1)                  # just past a second's start, so the start lands in it
        before = time.time()
        assert a.http.post(f"/sessions/{sid}/start").status_code == 200
        if int(time.time()) == int(before):
            break
    else:
        pytest.skip("no start landed in the second it was asked in")
    second = int(before)
    deadline = time.time() + 30
    while not (folder / "done").exists():                   # the stand-in has written its own rows by then
        assert time.time() < deadline, "the stand-in did not run its hooks"
        time.sleep(0.05)
    _line(folder, [_row("agent-old", seen_at=second), _row("agent-new", seen_at=second + 1)], at=second + 1)
    row = {r["id"]: r for r in a.http.get("/map").json()["sessions"]}[sid]
    got = {x["id"]: x for x in row["agents"]}
    assert got["agent-old"]["status"] is None and got["agent-old"]["ended_at"] is None
    assert got["agent-new"]["status"] == "running"


def test_a_fifo_where_a_record_or_a_saved_screen_goes_holds_up_neither_the_list_nor_the_map(mapped):
    # both are read on the agent's event loop, for the list and the map, every few seconds; a session's programs can
    # write its folder: a FIFO there is never waited on
    a, claude, shell, closed, folder = mapped
    fifo_record, screened = folder.parent / "0123456789ab", folder.parent / "0123456789ac"
    fifo_record.mkdir()
    os.mkfifo(fifo_record / "session.json")
    folder_record = folder.parent / "0123456789ad"                      # a folder where the record goes: no file
    (folder_record / "session.json").mkdir(parents=True)
    screened.mkdir()
    record = json.loads((folder.parent / shell["id"] / "session.json").read_text())
    (screened / "session.json").write_text(json.dumps(dict(record, id=screened.name)))
    os.mkfifo(screened / "screen")
    started = time.monotonic()
    listed = a.http.get("/sessions", timeout=20)
    assert listed.status_code == 200
    ids = {x["id"] for x in listed.json()["sessions"]}
    assert screened.name in ids and not {fifo_record.name, folder_record.name} & ids   # a record that is no file
    assert a.http.get("/map", timeout=20).status_code == 200
    assert time.monotonic() - started < 15
    assert within(5, terminals.SavedScreen.load, str(screened / "screen")) is None
    assert within(5, terminals.SavedScreen.meta, str(screened / "screen")) is None


def test_a_folder_where_a_file_is_read_leaves_no_descriptor_open(tmp_path):
    # a session's programs can put a folder where its saved screen goes; wrapping a folder's descriptor raises before
    # anything closes it, and the list and the map read the screen every few seconds:
    # each read looks at the file before it wraps it. The workspace's own settings file is read the same way
    from proofpane_workspace import workspace as ws_module
    folder = tmp_path / "screen"
    folder.mkdir()
    before = len(os.listdir("/dev/fd"))
    for _ in range(50):
        assert terminals.SavedScreen.load(str(folder)) is None and terminals.SavedScreen.meta(str(folder)) is None
        assert ws_module._workspace_settings(str(folder)) is None
    assert len(os.listdir("/dev/fd")) - before < 10


def test_what_a_session_s_programs_wrote_is_cut_again_as_either_route_reads_it_back(mapped):
    # the line's file and the hook's calls are the session's own programs' to write: a name and a target of 300
    # characters are cut as they are read back, to 80 and 120, the only cut on each
    a, claude, shell, closed, folder = mapped
    _line(folder, [_row("agent-long", name="n" * 300, seen_at=int(time.time()))])
    with open(folder / activity.CALLS, "a") as fh:
        fh.write(json.dumps({"at": 5, "agent": None, "tool": "Read", "target": "/w/" + "t" * 300,
                             "failed": False}) + "\n")
    row = {r["id"]: r for r in a.http.get("/map").json()["sessions"]}[claude["id"]]
    assert {x["id"]: x for x in row["agents"]}["agent-long"]["name"] == "n" * subagentline.NAME
    calls = a.http.get(f"/sessions/{claude['id']}/map").json()["main"]["calls"]
    assert [c["target"] for c in calls if (c["target"] or "").startswith("/w/t")] == [
        ("/w/" + "t" * 300)[:activity.TARGET]]
    assert (subagentline.NAME, activity.TARGET) == (80, 120)


def test_an_agent_the_hook_saw_start_and_stop_in_one_second_has_ended(tmp_path):
    _events(tmp_path, (100, "start", "h1", None), (100, "stop", "h1", None))
    got = _by_id(tmp_path)["h1"]
    assert got["status"] is None and got["ended_at"] == 100


def _turned(main, sub):
    """An answer with `main` turns of the session's own and `sub` of each of two sub-agents, for fit."""
    turn = {"requests": 1, "input_tokens": 1000, "output_tokens": 10}
    groups = [(None, [dict(turn, input_tokens=n) for n in range(main)])]
    groups += [(a, [dict(turn, input_tokens=n) for n in range(sub)]) for a in ("s1", "s2")]
    answer = {"at": 1, "session": "0123456789ab", "calls_read": "yes", "main": {"calls": [], "more": 0},
              "agents": [{"id": "s1", "calls": [], "more": 0}, {"id": "s2", "calls": [], "more": 0}],
              "turns_read": "yes", "turns": [], "cut": False}
    return answer, groups


def test_turns_past_the_bound_are_cut_to_each_holder_s_newest():
    got = sessionmap.fit(*_turned(300, 300), most=4000)
    assert sessionmap.size(got) <= 4000 and got["cut"] is True
    for agent in (None, "s1", "s2"):
        kept = [t["input_tokens"] for t in got["turns"] if t["agent"] == agent]
        assert kept and kept == list(range(300 - len(kept), 300)), (agent, kept)       # its newest, in order
    small = sessionmap.fit(*_turned(3, 3), most=100000)
    assert [t["input_tokens"] for t in small["turns"]] == [0, 1, 2, 0, 1, 2, 0, 1, 2] and small["cut"] is False


def test_with_one_turn_a_holder_still_past_the_bound_the_sub_agents_go_first_and_the_session_s_turn_last():
    answer, groups = _turned(300, 300)
    one = sessionmap.fit(answer, groups, most=10 ** 6)
    floor = dict(one, agents=[], turns=[{"requests": 1, "input_tokens": 299, "output_tokens": 10, "agent": None}],
                 cut=True)
    got = sessionmap.fit(answer, groups, most=sessionmap.size(floor))
    assert got["turns"] == floor["turns"] and got["agents"] == [] and got["cut"] is True
    # room for the agents but not their turns: the turns go, the agents stay
    roomy = sessionmap.fit(answer, groups, most=sessionmap.size(dict(floor, agents=one["agents"])))
    assert roomy["turns"] == floor["turns"] and roomy["agents"] == one["agents"]
    bare = sessionmap.fit(answer, groups, most=sessionmap.size(dict(floor, turns=[])))
    assert bare["turns"] == [] and bare["agents"] == []                 # past even that: the session's own last


@pytest.mark.parametrize("row", [
    {"type": "assistant", "message": {"id": ["a", "list"], "usage": {"input_tokens": 1}}},
    {"type": "user", "message": {"content": 5}},
    {"type": "user", "message": {"content": [{"type": "text", "text": 5}]}},
    {"type": "assistant", "message": {"id": {"x": 1}, "usage": {"output_tokens": "many"}}},
])
def test_a_transcript_line_out_of_shape_never_fails_the_route(tmp_path, row):
    work, claude_dir = tmp_path / "w", tmp_path / "claude"
    work.mkdir()
    folder = claude_dir / "projects" / usage.slug(os.path.realpath(work))
    _transcript(str(folder / "conv-2.jsonl"), _user("hello"), row, _reply("m1", "done"))
    sessionmap._turns_kept.clear()
    got, _ = sessionmap.turns(str(work), str(claude_dir), "conv-2")
    assert got is not None and got[0][0] is None


def test_a_record_that_is_not_an_object_is_one_that_cannot_be_read(tmp_path, monkeypatch, capsys):
    # left out as a record that is not JSON is, and said nowhere; only a field no view can be made of is said, once
    # (a view of such a record fails too, and was once said as a field out of shape)
    from proofpane_workspace.workspace import Workspace as Space
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    for sid, text in (("0123456789ab", "[]"), ("0123456789ac", '"a record"'), ("0123456789ad", "5")):
        os.makedirs(os.path.join(ws.sessions_dir, sid))
        with open(os.path.join(ws.sessions_dir, sid, "session.json"), "w") as fh:
            fh.write(text)
    capsys.readouterr()
    assert ws.sessions() == [] and capsys.readouterr().err == ""
    with pytest.raises(ValueError):
        ws._read("0123456789ab")


def _space(tmp_path, monkeypatch):
    """A workspace in this process, with no session."""
    from proofpane_workspace.workspace import Workspace as Space
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    return Space(str(root))


def _record(ws, sid, record=None, text=None):
    folder = os.path.join(ws.sessions_dir, sid)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "session.json"), "w") as fh:
        fh.write(text if text is not None else json.dumps(dict(
            {"id": sid, "repo": None, "program": "shell", "task": "A shell", "base": None, "created_at": 1000},
            **(record or {}))))
    return folder


def test_a_fifo_where_a_record_s_temporary_file_goes_holds_up_neither_the_list_nor_a_start(tmp_path, monkeypatch):
    # the list writes a teleported session's record as it names it after its branch, on the agent's event loop, and a
    # provider start writes its settings file; a session's programs can put a FIFO where either's temporary file goes
    # (the list once waited on it, and the whole agent with it)
    from proofpane_workspace import providers
    from proofpane_workspace.workspace import TELEPORT_TASK
    ws = _space(tmp_path, monkeypatch)
    folder = _record(ws, "0123456789ab", {"task": TELEPORT_TASK, "adopted": True,
                                           "teleported_from": "claude/fix-the-parser"})
    os.mkfifo(os.path.join(folder, "session.json.tmp"))
    assert [v.get("title") for v in within(5, ws.sessions)] == ["claude/fix-the-parser"]     # named and written
    monkeypatch.setattr(ws, "_provider_key", lambda provider: None)
    os.mkfifo(os.path.join(folder, "model-settings.json.tmp"))
    argv = within(5, ws._on_provider, "0123456789ab", sorted(providers.CATALOG)[0], ["/x/claude"])
    assert argv[1:3] == ["--settings", os.path.join(folder, "model-settings.json")]


def test_a_record_holding_a_number_json_has_no_name_for_or_past_its_bound_cannot_be_read(tmp_path, monkeypatch):
    # Python's json reads NaN, Infinity and 1e400 (as infinity), which no answer can render: the list's would fail for
    # every session; and a record is read whole, on the event loop, so at most a bound
    from proofpane_workspace import workspace as wsm
    ws = _space(tmp_path, monkeypatch)
    body = '{"id": "%s", "repo": null, "program": "shell", "task": "t", "base": null, "created_at": 1000, "x": %s}'
    for sid, value in (("0123456789aa", "NaN"), ("0123456789ab", "Infinity"), ("0123456789ac", "-Infinity"),
                       ("0123456789ad", "1e400")):
        _record(ws, sid, text=body % (sid, value))
    _record(ws, "0123456789ae", {"padding": "p" * wsm.MAX_RECORD})
    # past the bound, whatever its first part holds: a record that parses, then a megabyte of spaces (a read cut at
    # the bound would parse it)
    _record(ws, "0123456789b0", text=json.dumps({"id": "0123456789b0", "repo": None, "program": "shell", "task": "t",
                                                  "base": None, "created_at": 1000}) + " " * wsm.MAX_RECORD)
    _record(ws, "0123456789af", {"x": 1.5, "n": 10 ** 40})
    views = ws.sessions()
    assert [v["id"] for v in views] == ["0123456789af"]
    json.dumps(views, allow_nan=False)                  # as the route renders the list
    for sid in ("0123456789aa", "0123456789ad", "0123456789ae", "0123456789b0"):
        with pytest.raises(ValueError):
            ws._read(sid)


def test_a_saved_screen_whose_first_line_is_nested_too_deep_is_no_screen(tmp_path, monkeypatch):
    # Python 3.11 and older raise RecursionError on a line nested deeply enough, well within the 4 KB read: the list
    # and the map read the screen of every session not run since the agent started
    import types
    path = tmp_path / "screen"
    path.write_bytes(b'{"started_at": 1}\nout')

    def too_deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")
    monkeypatch.setattr(terminals, "json", types.SimpleNamespace(loads=too_deep, dumps=json.dumps))
    assert terminals.SavedScreen.meta(str(path)) is None and terminals.SavedScreen.load(str(path)) is None


def test_a_session_left_out_of_the_list_for_a_field_no_view_can_be_made_of_is_said_once(tmp_path, monkeypatch, capsys):
    ws = _space(tmp_path, monkeypatch)
    folder = _record(ws, "0123456789ab")
    with open(os.path.join(folder, "session.json"), "w") as fh:
        json.dump({"id": "0123456789ab", "repo": None, "task": "no program", "created_at": 1000}, fh)
    capsys.readouterr()
    assert ws.sessions() == [] and ws.sessions() == []
    assert capsys.readouterr().err.count("sessions: session '0123456789ab' left out: KeyError") == 1


def test_a_fifo_where_a_chat_s_record_goes_holds_up_no_check_of_its_settings(tmp_path, monkeypatch):
    # the check of the chats whose records name other settings than the owner chose reads each record as the list does
    ws = _space(tmp_path, monkeypatch)
    folder = os.path.join(ws.sessions_dir, "0123456789ab")
    os.makedirs(folder)
    os.mkfifo(os.path.join(folder, "session.json"))
    ws._chosen["0123456789ab"] = {"model": "opus"}
    assert within(5, ws.chosen_changed) == []


def test_a_provider_start_whose_settings_hold_a_number_json_has_no_name_for_is_refused(tmp_path, monkeypatch):
    # its own settings file would carry Infinity, which a strict reader refuses, the gate and the key helper with it:
    # refused, and said why
    from proofpane_workspace import providers
    from proofpane_workspace.workspace import WorkspaceError
    ws = _space(tmp_path, monkeypatch)
    _record(ws, "0123456789ab")
    monkeypatch.setattr(ws, "_provider_key", lambda provider: None)
    gate = tmp_path / "gate-settings.json"
    gate.write_text('{"hooks": {}, "cleanupPeriodDays": 1e400}')
    with pytest.raises(WorkspaceError, match="no name for"):
        ws._on_provider("0123456789ab", sorted(providers.CATALOG)[0], ["/x/claude", "--settings", str(gate)])
    assert not os.path.exists(os.path.join(ws.sessions_dir, "0123456789ab", "model-settings.json"))


def test_a_record_a_screen_and_the_workspace_s_settings_are_never_opened_as_the_agent_s_terminal(tmp_path, monkeypatch):
    # each follows a link, which a session's programs could point at a terminal: Linux makes the first one a session
    # leader with none opens its own, so each is opened with O_NOCTTY
    if not getattr(os, "O_NOCTTY", 0):
        pytest.skip("no O_NOCTTY on this system")
    from proofpane_workspace import workspace as wsm
    ws = _space(tmp_path, monkeypatch)
    folder = _record(ws, "0123456789ab")
    (tmp_path / "screen").write_bytes(b'{"started_at": 1}\nout')
    (tmp_path / "settings.json").write_text("{}")
    seen, real = [], os.open

    def opened(path, flags, *args, **kwargs):
        seen.append((os.path.basename(os.fspath(path)), flags))
        return real(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", opened)
    terminals.SavedScreen.meta(str(tmp_path / "screen"))
    wsm._workspace_settings(str(tmp_path / "settings.json"))
    ws._read("0123456789ab")
    monkeypatch.undo()
    named = {name: flags for name, flags in seen if name in ("screen", "settings.json", "session.json")}
    assert set(named) == {"screen", "settings.json", "session.json"}
    assert all(flags & os.O_NOCTTY for flags in named.values()), named


def test_one_session_s_row_that_cannot_be_made_leaves_the_map_whole(tmp_path, monkeypatch, capsys):
    from proofpane_workspace.workspace import Workspace as Space
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    real = sessionmap.session_row

    def row(view, *args):
        if view["id"] == "0123456789ab":
            raise RuntimeError("a bug")
        return real(view, *args)
    monkeypatch.setattr(sessionmap, "session_row", row)
    views = [_view(), _view(id="ba9876543210", program="shell")]
    got = ws.map(views, {v["id"]: (None, None) for v in views})
    assert [r["id"] for r in got["sessions"]] == ["ba9876543210"]
    said = capsys.readouterr().err                              # said where it is read, its kind alone
    assert "0123456789ab" in said and "RuntimeError" in said and "a bug" not in said
    # and said once: the page asks every few seconds, and the line would fill the agent's log
    assert [r["id"] for r in ws.map(views, {v["id"]: (None, None) for v in views})["sessions"]] == ["ba9876543210"]
    assert capsys.readouterr().err == ""


def test_the_provider_s_settings_are_never_read_from_a_fifo(tmp_path, monkeypatch):
    # the file named may be the session's own map file, which its programs can replace: refused at once, never waited on
    from proofpane_workspace import providers
    from proofpane_workspace.workspace import Workspace as Space, WorkspaceError
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    monkeypatch.setattr(ws, "_provider_key", lambda provider: None)
    folder = tmp_path / "s"
    folder.mkdir()
    ws._dir = lambda sid: str(folder)
    os.mkfifo(folder / "map-settings.json")
    name = sorted(providers.CATALOG)[0]

    def start():
        try:
            ws._on_provider("a" * 12, name, ["/x/claude", "--settings", str(folder / "map-settings.json")])
        except WorkspaceError as e:
            return str(e)
    assert "could not be read" in within(5, start)


def test_the_provider_step_reads_the_session_s_own_file_as_its_own_and_the_workspace_s_as_before(tmp_path, monkeypatch):
    # the session's own map file, which its programs can replace, is never followed as a link; the workspace's own file
    # (the local provider's) is, as it always was; neither is waited on as a FIFO
    from proofpane_workspace import providers
    from proofpane_workspace.workspace import Workspace as Space, WorkspaceError
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    monkeypatch.setattr(ws, "_provider_key", lambda provider: None)
    folder = tmp_path / "s"
    folder.mkdir()
    ws._dir = lambda sid: str(folder)
    name = sorted(providers.CATALOG)[0]
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"hooks": {}}))

    def start(path):
        try:
            return ws._on_provider("a" * 12, name, ["/x/claude", "--settings", str(path)])
        except WorkspaceError as e:
            return str(e)
    os.symlink(elsewhere, folder / "map-settings.json")
    assert "could not be read" in within(5, start, folder / "map-settings.json")      # the session's own: no link
    linked = tmp_path / "workspace-settings.json"
    os.symlink(elsewhere, linked)
    assert within(5, start, linked)[1:3] == ["--settings", str(folder / "model-settings.json")]   # as before
    os.mkfifo(tmp_path / "fifo.json")
    assert "could not be read" in within(5, start, tmp_path / "fifo.json")              # never waited on
