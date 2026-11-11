"""The workspace's gate and each session's activity for Codex: managed hooks in its requirements file
(settings.codex_requirements), which the image writes as root; the gate read in the form Codex gives a call (Codex
0.163.0: the shell tool is `Bash`, its command a string) and answered in Claude Code's form, which Codex takes; and
activity.py's note for a PermissionRequest. With PP_TEST_CODEX naming a codex program, a real codex runs against a
stand-in model (codex_stand_in.py) and the gate refuses its command."""

import json
import os
import re
import subprocess
import sys
import time
import tomllib

import pytest

from proofpane_workspace import activity, gate, settings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMAGE_DIR = "/opt/proofpane/proofpane_workspace"


def _run_gate(payload, home):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "proofpane_workspace", "gate.py")], input=json.dumps(payload),
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": home}, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def _codex_call(tool, tool_input, cwd="/w"):
    # the fields Codex 0.157.1 gives a PreToolUse hook, as it gave them to one (2026-10-02); 0.157.1 and 0.163.0 both
    # gave these, and tool_use_id and transcript_path besides (2026-10-18)
    return {"session_id": "s", "turn_id": "t", "cwd": cwd, "hook_event_name": "PreToolUse", "model": "gpt-5",
            "permission_mode": "default", "tool_name": tool, "tool_input": tool_input}


def test_the_image_writes_codex_s_requirements_as_root_from_the_settings_module():
    with open(os.path.join(ROOT, "workspace_image", "Dockerfile"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    assert ("/usr/bin/python3 -m proofpane_workspace.settings --codex --python /usr/bin/python3 \\\n"
            "        --dir /opt/proofpane/proofpane_workspace > /etc/codex/requirements.toml") in dockerfile
    assert re.search(r"chmod 0644 [^\n]*/etc/codex/requirements\.toml", dockerfile)


def test_the_requirements_pin_hooks_on_so_no_one_s_config_turns_the_gate_off():
    # Codex's hooks page: "[features] hooks = false" in a config.toml turns hooks off, managed ones too, unless the
    # requirements "pin [features].hooks = true ... alongside [hooks]"; a repository's own .codex/config.toml was
    # enough, in a project no one trusted (Codex 0.163.0, by `codex exec`). The Docker test runs the real codex with
    # every such switch.
    required = tomllib.loads(settings.codex_requirements("/usr/bin/python3", IMAGE_DIR))
    assert required["features"] == {"hooks": True}
    assert set(required) == {"hooks", "features"}


def test_the_requirements_run_the_gate_before_every_tool_and_say_what_the_session_does():
    hooks = tomllib.loads(settings.codex_requirements("/usr/bin/python3", IMAGE_DIR))["hooks"]
    assert set(hooks) == {"PreToolUse", "UserPromptSubmit", "PostToolUse", "PermissionRequest", "Stop"}
    gate_hook = hooks["PreToolUse"]
    assert gate_hook == [{"matcher": "*", "hooks": [{"type": "command",
                                                     "command": f"/usr/bin/python3 -E -s -X utf8 {IMAGE_DIR}/gate.py",
                                                     "timeout": settings.HOOK_TIMEOUT}]}]
    states = {event: hooks[event][0]["hooks"][0]["command"].rsplit(" ", 1)[1]
              for event in ("UserPromptSubmit", "PostToolUse", "PermissionRequest", "Stop")}
    assert states == {"UserPromptSubmit": "working", "PostToolUse": "working", "PermissionRequest": "waiting",
                      "Stop": "idle"}
    for event in hooks:
        for h in hooks[event][0]["hooks"]:
            assert "--gemini" not in h["command"]          # Codex reads Claude Code's answer, and no output from activity
    odd = tomllib.loads(settings.codex_requirements("/opt/my python/bin/python3", "/opt/a 'dir'"))["hooks"]
    assert odd["PreToolUse"][0]["hooks"][0]["command"] == (
        "'/opt/my python/bin/python3' -E -s -X utf8 '/opt/a '\"'\"'dir'\"'\"'/gate.py'")


def test_the_gate_refuses_codex_s_calls_in_the_answer_codex_takes(tmp_path):
    home = str(tmp_path)
    for payload in (_codex_call("Bash", {"command": "cat ~/.codex/auth.json"}),
                    _codex_call("Bash", {"command": ["bash", "-lc", "cat ~/.claude/.credentials.json"]}),
                    _codex_call("write_stdin", {"session_id": 1, "chars": "cat ~/.codex/auth.json\n"}),
                    _codex_call("mcp__fs__read_file", {"path": f"{home}/.config/gh/hosts.yml"})):
        answer = _run_gate(payload, home)
        assert answer["hookSpecificOutput"]["permissionDecision"] == "deny", payload
        assert "sign-in" in answer["hookSpecificOutput"]["permissionDecisionReason"]
    # what Codex types into a command that runs (write_stdin) is held to the command rules too
    typed = _run_gate(_codex_call("write_stdin", {"session_id": 1, "chars": "gh auth token\n"}), home)
    assert "sign-in" in typed["hookSpecificOutput"]["permissionDecisionReason"]
    pushed = _run_gate(_codex_call("Bash", {"command": "git push --force origin main"}), home)
    assert "force push" in pushed["hookSpecificOutput"]["permissionDecisionReason"]
    assert _run_gate(_codex_call("Bash", {"command": "git status --short"}), home) is None
    assert _run_gate(_codex_call("Bash", {"command": ["git", "push", "origin", "main"]}), home) is None


def test_a_codex_permission_request_is_waiting_with_what_it_asks(tmp_path, own_home):
    folder = tmp_path / "session"
    folder.mkdir()
    ask = {"session_id": "s", "hook_event_name": "PermissionRequest", "tool_name": "Bash",
           "tool_input": {"command": "npm  install\n left-pad", "description": "needs the network"}}
    assert activity.record("waiting", ask, str(folder))
    got = json.loads((folder / "activity.json").read_text())
    assert got["state"] == "waiting" and got["message"] == "Wants to run: npm install left-pad"
    activity.record("waiting", {"hook_event_name": "PermissionRequest", "tool_name": "mcp__x__y",
                                "tool_input": {"description": "to read the wiki"}}, str(folder))
    assert json.loads((folder / "activity.json").read_text())["message"] == "Wants to use mcp__x__y: to read the wiki"
    # nothing on stdout: Codex adds a hook's output to the model's context
    p = subprocess.run([sys.executable, os.path.join(ROOT, "proofpane_workspace", "activity.py"), "waiting"],
                       input=json.dumps(ask), capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "HOME": own_home, "PP_SESSION_DIR": str(folder)}, timeout=30)
    assert p.returncode == 0 and p.stdout == ""


CODEX = os.environ.get("PP_TEST_CODEX")


@pytest.mark.skipif(not CODEX, reason="PP_TEST_CODEX names no codex program to run against a stand-in model")
def test_a_real_codex_is_refused_by_the_gate_and_says_what_it_does(tmp_path):
    """Codex's own run, its hooks from codex_requirements in a throwaway home: here they are the user's, so they run
    with --dangerously-bypass-hook-trust; in the image they are managed, and trusted by policy (the Docker test)."""
    home, work, session = tmp_path / "home", tmp_path / "session" / "work", tmp_path / "session"
    home.mkdir()
    work.mkdir(parents=True)
    (session / "session.json").write_text("{}")
    log = tmp_path / "standin.log"
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "STANDIN_LOG": str(log),
           "STANDIN_COMMAND": "cat ~/.codex/auth.json"}
    standin = subprocess.Popen([sys.executable, os.path.join(HERE, "codex_stand_in.py")], stdout=subprocess.PIPE,
                               text=True, env=env)
    try:
        port = int(standin.stdout.readline())
        config = (f'model = "gpt-5"\nmodel_provider = "standin"\napproval_policy = "never"\n'
                  f'sandbox_mode = "danger-full-access"\n\n[model_providers.standin]\nname = "standin"\n'
                  f'base_url = "http://127.0.0.1:{port}/v1"\nwire_api = "responses"\nenv_key = "STANDIN_KEY"\n\n')
        config += settings.codex_requirements(sys.executable, os.path.join(ROOT, "proofpane_workspace"))
        (home / "config.toml").write_text(config)
        run = subprocess.run([CODEX, "exec", "--dangerously-bypass-hook-trust", "--skip-git-repo-check", "go"],
                             cwd=work, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                             env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home),
                                  "STANDIN_KEY": "x", "PP_SESSION_DIR": str(session)})
    finally:
        standin.kill()
        standin.wait()
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    refused = [json.loads(line) for line in (session / "gate.jsonl").read_text().splitlines()]
    assert refused and refused[0]["tool"] == "Bash" and "sign-in" in refused[0]["reason"]
    seen = [json.loads(line) for line in log.read_text().splitlines()]
    assert "Refused by the workspace's gate" in seen[-1]["output"]["output"]       # the model was told why
    state = json.loads((session / "activity.json").read_text())
    assert state["state"] == "idle" and state["at"] <= time.time()
