"""The workspace's gate and each session's activity for Grok Build: enforced hooks in root's requirements file
(settings.grok_requirements), which the image writes; the gate read in the form Grok Build gives a call (camelCase
`toolName` and `toolInput`, its shell tool `run_terminal_command`; 1.0.46 and 1.0.62 give `tool_name` and `tool_input`
beside them, which the gate reads first) and answered in Claude Code's form, which it ranks above its own. With
PP_TEST_GROK naming a grok program, a real grok runs against a stand-in model (codex_stand_in.py, over the Responses
API) and the gate refuses its command."""

import json
import os
import re
import subprocess
import sys
import tomllib

import pytest

from proofpane_workspace import settings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMAGE_DIR = "/opt/proofpane/proofpane_workspace"


def _run_gate(payload, home):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "proofpane_workspace", "gate.py")], input=json.dumps(payload),
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": home}, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def _grok_call(tool, tool_input):
    # the camelCase fields Grok Build gives a PreToolUse hook, 1.0.46 (2026-10-02) and 1.0.62 (2026-10-18) alike; both
    # give `tool_name` and `tool_input` too, left out here so that the gate reads the camelCase ones
    return {"hookEventName": "pre_tool_use", "hook_event_name": "PreToolUse", "sessionId": "s", "cwd": "/w",
            "workspaceRoot": "/w", "permissionMode": "default", "toolName": tool, "toolInput": tool_input}


def test_the_image_writes_grok_s_requirements_as_root_from_the_settings_module():
    with open(os.path.join(ROOT, "workspace_image", "Dockerfile"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    assert ("/usr/bin/python3 -m proofpane_workspace.settings --grok --python /usr/bin/python3 \\\n"
            "        --dir /opt/proofpane/proofpane_workspace > /etc/grok/requirements.toml") in dockerfile
    assert re.search(r"chmod 0644 [^\n]*\\\n\s+/etc/grok/requirements\.toml", dockerfile)


def test_the_requirements_run_the_gate_before_every_tool_and_say_what_the_session_does():
    hooks = tomllib.loads(settings.grok_requirements("/usr/bin/python3", IMAGE_DIR))["hooks"]
    assert hooks["PreToolUse"] == [{"matcher": "*", "hooks": [{
        "type": "command", "command": f"/usr/bin/python3 -E -s -X utf8 {IMAGE_DIR}/gate.py",
        "timeout": settings.HOOK_TIMEOUT}]}]
    states = {event: (group.get("matcher"), group["hooks"][0]["command"].rsplit(" ", 1)[1])
              for event, (group,) in hooks.items() if event != "PreToolUse"}
    # a turn ends in one of three ways, each of which Grok fires instead of the others: completed, failed, cut short
    assert states == {"UserPromptSubmit": (None, "working"), "PostToolUse": ("*", "working"),
                      "Notification": ("permission_prompt|idle_prompt", "waiting"), "Stop": (None, "idle"),
                      "StopFailure": (None, "idle"), "StopCancelled": (None, "idle")}


def test_the_gate_refuses_grok_s_calls_in_the_answer_grok_takes(tmp_path):
    home = str(tmp_path)
    for payload in (_grok_call("run_terminal_command", {"command": "cat ~/.grok/auth.json", "description": "d"}),
                    _grok_call("read_file", {"target_file": f"{home}/.grok/auth.json"}),
                    _grok_call("run_terminal_command", {"command": "cp ~/.codex/auth.json /tmp/x", "description": "d"})):
        answer = _run_gate(payload, home)
        assert answer["hookSpecificOutput"]["permissionDecision"] == "deny", payload
        assert "sign-in" in answer["hookSpecificOutput"]["permissionDecisionReason"]
    pushed = _run_gate(_grok_call("run_terminal_command", {"command": "git push --force", "description": "d"}), home)
    assert "force push" in pushed["hookSpecificOutput"]["permissionDecisionReason"]
    assert _run_gate(_grok_call("run_terminal_command", {"command": "npm test", "description": "d"}), home) is None
    # a call that names neither form is not read, and is refused
    assert _run_gate({"hook_event_name": "PreToolUse", "toolName": "x"}, home)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_command_another_tool_runs_is_held_to_the_same_rules(tmp_path):
    # Grok Build's monitor runs a command and gives the model each line it prints; so do the rules hold for it
    home = str(tmp_path)
    token = _run_gate(_grok_call("monitor", {"command": "gh auth token", "description": "d"}), home)
    assert "sign-in" in token["hookSpecificOutput"]["permissionDecisionReason"]
    forced = _run_gate(_grok_call("monitor", {"command": "git push --force origin main", "description": "d"}), home)
    assert "force push" in forced["hookSpecificOutput"]["permissionDecisionReason"]
    assert _run_gate(_grok_call("monitor", {"command": "npm run dev", "description": "d"}), home) is None
    # a file that only mentions a command is not one
    assert _run_gate(_grok_call("write_file", {"path": "a.md", "content": "never git push --force"}), home) is None


def test_grok_s_mcp_tokens_are_a_sign_in_too(tmp_path):
    home = str(tmp_path)
    for payload in (_grok_call("read_file", {"target_file": "~/.grok/mcp_credentials.json"}),
                    _grok_call("run_terminal_command", {"command": "cat ~/.grok/mcp_credentials.json", "description": "d"})):
        assert "sign-in" in _run_gate(payload, home)["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_command_grok_gives_only_in_part_is_not_let_through(tmp_path):
    # Grok Build says when it gave a tool's input cut short (toolInputTruncated): what the gate cannot see of a
    # command could name anything, so such a command is refused (a call of any other tool: the test below)
    home = str(tmp_path)
    cut = dict(_grok_call("run_terminal_command", {"command": "npm test && cat ~/.gr", "description": "d"}),
               toolInputTruncated=True)
    assert "only part" in _run_gate(cut, home)["hookSpecificOutput"]["permissionDecisionReason"]
    whole = dict(_grok_call("run_terminal_command", {"command": "npm test", "description": "d"}), toolInputTruncated=False)
    assert _run_gate(whole, home) is None


def test_a_call_grok_gives_only_in_part_is_refused_whatever_the_tool(tmp_path):
    # Grok Build 1.0.62 gives a tool input past 128 KiB as the object with each string over a length of at least 4 KiB
    # cut, ending in a tag and " [truncated]", where 1.0.46 gave the whole input as one cut string, which the gate
    # cannot read (both measured 2026-10-18): the gate's rules on names read every string a call carries, a file's
    # text too, so what was cut off a string could name what they refuse (a script that reads the sign-in, written
    # whole, is refused; written past a cut, it went on and was run, with a real grok: measured 2026-10-19), and a
    # call cut short is refused whatever its tool, as it was in effect with 1.0.46
    home = str(tmp_path)
    cut = "x" * 5000 + " #1e918d7d654c50cb [truncated]"
    calls = (("spawn_subagent", {"prompt": cut, "description": "d"}),
             ("use_tool", {"tool_name": "notes__save", "tool_input": {"path": "n.txt", "content": cut}}),
             ("notes__save", {"path": "n.txt", "content": cut}),
             # Grok's own file tools, and the Cursor preset's that a session can switch on (agent_type = "cursor")
             ("write", {"file_path": "big.txt", "content": cut}),
             ("search_replace", {"file_path": "big.txt", "old_string": "a", "new_string": cut}),
             ("Write", {"path": "big.txt", "contents": cut}),
             ("StrReplace", {"path": "big.txt", "old_string": "a", "new_string": cut}),
             ("write_file", {"path": "big.txt", "content": cut}))
    for tool, given in calls:
        why = _run_gate(dict(_grok_call(tool, given), toolInputTruncated=True), home)
        assert why and "only part of this tool call" in why["hookSpecificOutput"]["permissionDecisionReason"], tool
        snake = _run_gate(dict(_grok_call(tool, given), tool_input_truncated=True), home)
        assert snake and "only part of this tool call" in snake["hookSpecificOutput"]["permissionDecisionReason"], tool
        assert _run_gate(dict(_grok_call(tool, given), toolInputTruncated=False), home) is None, tool
        assert _run_gate(_grok_call(tool, given), home) is None, tool
    # what decides is Grok's flag, not a string's ending: a call it marks whole goes on though a string of it ends as a
    # cut one does (above), and one it marks cut is refused though no string of it shows the mark
    unmarked = dict(_grok_call("write", {"file_path": "big.txt", "content": "x" * 100}), toolInputTruncated=True)
    assert "only part of this tool call" in _run_gate(unmarked, home)["hookSpecificOutput"]["permissionDecisionReason"]
    # the rules on names come first: a cut call whose shown part names the sign-in is refused for that
    signin = dict(_grok_call("write", {"file_path": "~/.grok/auth.json", "content": cut}), toolInputTruncated=True)
    assert "sign-in" in _run_gate(signin, home)["hookSpecificOutput"]["permissionDecisionReason"]
    whole = _grok_call("write", {"file_path": "s.sh", "content": "#" * 5000 + "\ncat ~/.grok/auth.json\n"})
    assert "sign-in" in _run_gate(whole, home)["hookSpecificOutput"]["permissionDecisionReason"]
    command = dict(_grok_call("monitor", {"command": "tail -f " + cut, "description": "d"}), toolInputTruncated=True)
    assert "only part of this command" in _run_gate(command, home)["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_refusal_is_recorded_with_grok_s_tool_name(tmp_path):
    session = tmp_path / "session"
    (session / "work").mkdir(parents=True)
    (session / "session.json").write_text("{}")
    payload = dict(_grok_call("run_terminal_command", {"command": "cat ~/.grok/auth.json", "description": "d"}),
                   cwd=str(session / "work"))
    subprocess.run([sys.executable, os.path.join(ROOT, "proofpane_workspace", "gate.py")], input=json.dumps(payload),
                   capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, timeout=30)
    line = json.loads((session / "gate.jsonl").read_text())
    assert line["tool"] == "run_terminal_command" and "sign-in" in line["reason"]


GROK = os.environ.get("PP_TEST_GROK")


@pytest.mark.skipif(not GROK, reason="PP_TEST_GROK names no grok program to run against a stand-in model")
def test_a_real_grok_is_refused_by_the_gate_and_says_what_it_does(tmp_path):
    """Grok Build's own run, its hooks from grok_requirements in a throwaway home's config (a layer it always runs);
    in the image they are root's enforced hooks (the Docker test)."""
    home, session = tmp_path / "home", tmp_path / "session"
    (home / ".grok").mkdir(parents=True)
    (session / "work").mkdir(parents=True)
    (session / "session.json").write_text("{}")
    log = tmp_path / "standin.log"
    standin = subprocess.Popen([sys.executable, os.path.join(HERE, "codex_stand_in.py")], stdout=subprocess.PIPE,
                               text=True, env={"PATH": os.environ["PATH"], "HOME": str(home), "STANDIN_LOG": str(log),
                                               "STANDIN_COMMAND": "cat ~/.grok/auth.json; echo RAN-$((40+2))"})
    try:
        port = int(standin.stdout.readline())
        config = (f'[models]\ndefault = "standin"\n\n[model.standin]\nmodel = "standin"\n'
                  f'base_url = "http://127.0.0.1:{port}/v1"\nname = "Stand-in"\nenv_key = "STANDIN_KEY"\n'
                  f'api_backend = "responses"\n\n')
        config += settings.grok_requirements(sys.executable, os.path.join(ROOT, "proofpane_workspace"))
        (home / ".grok" / "config.toml").write_text(config)
        run = subprocess.run([GROK, "-p", "Show me the sign-in file.", "--always-approve"], cwd=session / "work",
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                             env={"PATH": os.environ["PATH"], "HOME": str(home), "STANDIN_KEY": "x",
                                  "PP_SESSION_DIR": str(session), "GROK_DISABLE_AUTOUPDATER": "1",
                                  "GROK_TELEMETRY_ENABLED": "0"})
    finally:
        standin.kill()
        standin.wait()
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    refused = [json.loads(line) for line in (session / "gate.jsonl").read_text().splitlines()]
    assert refused and refused[0]["tool"] == "run_terminal_command" and "sign-in" in refused[0]["reason"]
    told = [json.loads(line)["output"] for line in log.read_text().splitlines() if json.loads(line)["output"]]
    assert told and "Refused by the workspace's gate" in json.dumps(told[-1])
    assert "RAN-42" not in log.read_text()          # the command never ran: gate.jsonl alone would not show that
    assert json.loads((session / "activity.json").read_text())["state"] == "idle"
