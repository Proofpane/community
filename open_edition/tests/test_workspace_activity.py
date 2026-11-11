"""What a session's claude says it is doing (activity.py, run by its hooks) and how the agent shows it: the session's
own word, since this run of its program, never blocking a turn."""

import json
import os
import subprocess
import sys
import time

import pytest

from conftest import ROOT
from proofpane_workspace import activity
from workspace_fixtures import Agent, make_repo

SCRIPT = os.path.join(ROOT, "proofpane_workspace", "activity.py")


def _run(state, stdin, env_dir, home):
    env = {"PATH": os.environ["PATH"], "HOME": home}
    if env_dir is not None:
        env["PP_SESSION_DIR"] = str(env_dir)
    return subprocess.run([sys.executable, SCRIPT, state], input=stdin, env=env, capture_output=True, text=True,
                          timeout=30)


def test_a_notification_is_written_as_waiting_with_its_message(tmp_path, own_home):
    p = _run("waiting", json.dumps({"hook_event_name": "Notification",
                                    "message": "Claude needs your permission\nto use Bash"}), tmp_path, own_home)
    assert p.returncode == 0 and p.stdout == "" and p.stderr == ""
    got = json.loads((tmp_path / "activity.json").read_text())
    assert got["state"] == "waiting" and got["event"] == "Notification"
    assert got["message"] == "Claude needs your permission to use Bash" and abs(got["at"] - time.time()) < 30


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", json.dumps({"message": 5})])
def test_any_input_is_taken_and_nothing_blocks(tmp_path, stdin, own_home):
    p = _run("idle", stdin, tmp_path, own_home)
    assert p.returncode == 0 and p.stdout == ""
    got = json.loads((tmp_path / "activity.json").read_text())
    assert got["state"] == "idle" and "message" not in got


def test_without_a_session_or_with_an_unknown_state_nothing_is_written(tmp_path, own_home):
    assert _run("idle", "{}", None, own_home).returncode == 0
    assert _run("sleeping", "{}", tmp_path, own_home).returncode == 0
    assert not (tmp_path / "activity.json").exists()
    assert activity.record("working", {}, directory=str(tmp_path / "missing")) is False


@pytest.mark.parametrize("event, names", [
    # Codex's PermissionRequest names the tool; Claude Code's and Gemini CLI's Notification the kind; Grok Build both,
    # in camelCase
    ({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "env | grep KEY"}},
     {"tool": "Bash"}),
    ({"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "m"},
     {"kind": "permission_prompt"}),
    ({"hook_event_name": "Notification", "notification_type": "ToolPermission", "message": "m"}, {"kind": "ToolPermission"}),
    ({"hook_event_name": "Notification", "notificationType": "permission_prompt", "toolName": "run_terminal_command"},
     {"kind": "permission_prompt", "tool": "run_terminal_command"}),
    ({"hook_event_name": "PostToolUse", "tool_name": "mcp__fs__read_file"}, {"tool": "mcp__fs__read_file"}),
    # what is not a name is not kept as one
    ({"hook_event_name": "Notification", "tool_name": "cat ~/.codex/auth.json", "notification_type": 5}, {}),
    ({"tool_name": "x" * 81, "notificationType": "-dash first"}, {}),
])
def test_the_tool_and_the_kind_are_kept_as_names_for_notifications(tmp_path, event, names):
    activity.record("waiting", event, directory=str(tmp_path))
    got = json.loads((tmp_path / "activity.json").read_text())
    assert {k: got[k] for k in ("tool", "kind") if k in got} == names


def test_the_session_view_passes_on_the_names_and_not_what_is_no_name(agent, tmp_path):
    s = _session(agent)
    directory = tmp_path / "ws" / "sessions" / s["id"]
    (directory / "activity.json").write_text(json.dumps({"state": "waiting", "at": int(time.time()), "message": "m",
                                                         "tool": "Bash", "kind": "permission prompt"}))
    got = agent.http.get(f"/sessions/{s['id']}").json()["activity"]
    assert got["tool"] == "Bash" and "kind" not in got and got["message"] == "m"


def test_a_subagent_s_turn_does_not_speak_for_the_session_s_but_its_wait_does(tmp_path):
    # Grok Build marks a subagent's events: its stop is not the session's, nor is its tool finishing after the
    # session's turn ended a new turn
    activity.record("idle", {"hook_event_name": "Stop"}, directory=str(tmp_path))
    late = {"hook_event_name": "PostToolUse", "toolName": "run_terminal_command", "subagentType": "explore"}
    assert activity.record("working", late, directory=str(tmp_path)) is False
    assert activity.record("idle", {"hook_event_name": "Stop", "subagentType": "explore"}, directory=str(tmp_path)) is False
    assert json.loads((tmp_path / "activity.json").read_text())["state"] == "idle"
    ask = {"hook_event_name": "Notification", "notificationType": "permission_prompt", "subagentType": "explore"}
    assert activity.record("waiting", ask, directory=str(tmp_path)) is True
    assert json.loads((tmp_path / "activity.json").read_text())["state"] == "waiting"


def test_a_long_message_is_cut(tmp_path):
    activity.record("waiting", {"message": "x" * 1000}, directory=str(tmp_path))
    assert len(json.loads((tmp_path / "activity.json").read_text())["message"]) == activity.MESSAGE


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    yield a
    a.close()


def _session(agent):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Activity"})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_session_view_carries_what_its_program_said_since_it_started(agent, tmp_path):
    s = _session(agent)
    assert s["activity"] is None                     # a shell says nothing
    directory = tmp_path / "ws" / "sessions" / s["id"]
    activity.record("waiting", {"hook_event_name": "Notification", "message": "needs you"}, directory=str(directory))
    view = agent.http.get(f"/sessions/{s['id']}").json()
    assert view["activity"]["state"] == "waiting" and view["activity"]["message"] == "needs you"
    listed = {x["id"]: x for x in agent.http.get("/sessions").json()["sessions"]}
    assert listed[s["id"]]["activity"]["state"] == "waiting"


def test_what_was_said_before_this_run_started_is_not_shown(agent, tmp_path):
    s = _session(agent)
    directory = tmp_path / "ws" / "sessions" / s["id"]
    (directory / "activity.json").write_text(json.dumps({"state": "working", "at": int(time.time()) - 3600}))
    assert agent.http.get(f"/sessions/{s['id']}").json()["activity"] is None
    (directory / "activity.json").write_text("{not json")
    assert agent.http.get(f"/sessions/{s['id']}").json()["activity"] is None


def test_the_gate_s_refusals_are_listed_and_garbage_is_skipped(agent, tmp_path):
    s = _session(agent)
    folder = tmp_path / "ws" / "sessions" / s["id"]
    assert agent.http.get(f"/sessions/{s['id']}/gate").json() == {"session": s["id"], "refusals": [], "asks": []}
    (folder / "gate.jsonl").write_text(
        json.dumps({"at": 100, "tool": "Bash", "reason": "Refused by the workspace's gate: a force push"}) + "\n"
        + "not json\n" + json.dumps({"at": "x"}) + "\n"
        + json.dumps({"at": 200, "tool": 5, "reason": "r" * 1000}) + "\n")
    got = agent.http.get(f"/sessions/{s['id']}/gate").json()["refusals"]
    assert got[0] == {"at": 100, "tool": "Bash", "reason": "Refused by the workspace's gate: a force push"}
    assert got[1]["tool"] is None and len(got[1]["reason"]) == 400 and len(got) == 2


def test_a_program_in_one_of_the_session_s_shells_does_not_speak_for_the_session(tmp_path, own_home):
    import subprocess
    import sys
    folder = tmp_path / "session"
    folder.mkdir()
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proofpane_workspace", "activity.py")
    event = json.dumps({"hook_event_name": "Notification", "message": "Claude needs your permission", "session_id": "abc-1"})
    for shell, written in (("2", False), ("", True)):
        env = {"PATH": os.environ["PATH"], "HOME": own_home, "PP_SESSION_DIR": str(folder),
               **({"PP_SHELL": shell} if shell else {})}
        p = subprocess.run([sys.executable, script, "waiting"], input=event, capture_output=True, text=True, env=env,
                           timeout=30)
        assert p.returncode == 0 and p.stdout == ""
        assert (folder / "activity.json").exists() is written
    got = json.loads((folder / "activity.json").read_text())
    assert got["state"] == "waiting" and got["program_session"] == "abc-1"


def test_only_a_plain_id_is_kept_as_the_program_s_conversation(tmp_path):
    for given, kept in (("4f1c2b-77", "4f1c2b-77"), ("../../x", None), ("", None), (7, None)):
        activity.record("idle", {"session_id": given}, str(tmp_path))
        assert json.loads((tmp_path / "activity.json").read_text()).get("program_session") == kept
