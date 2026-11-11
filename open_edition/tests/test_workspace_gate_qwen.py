"""The workspace's gate and each session's activity for Qwen Code: its system settings (settings.qwen_settings), which
the image writes as root and which override a user's and a repository's settings; hooks kept on there over a
`disableAllHooks` anywhere else; the gate read in the form Qwen Code 0.24.7 gives a call, which is Claude Code's
(`tool_name`, `tool_input`, its shell tool `run_shell_command`), and answered in Claude Code's form. With PP_TEST_QWEN
naming a qwen program, a real qwen runs against a stand-in model (codex_stand_in.py, over the Responses API) with the
user's and the repository's settings both turning hooks off, and the gate still refuses its command."""

import json
import os
import re
import subprocess
import sys

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


def _qwen_call(tool, tool_input, cwd="/w"):
    # the fields Qwen Code gives a PreToolUse hook (its hooks reference): Claude Code's
    return {"session_id": "s", "transcript_path": "/t", "cwd": cwd, "hook_event_name": "PreToolUse",
            "permission_mode": "default", "tool_name": tool, "tool_input": tool_input}


def test_the_image_writes_qwen_s_system_settings_as_root_from_the_settings_module():
    with open(os.path.join(ROOT, "workspace_image", "Dockerfile"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    assert ("/usr/bin/python3 -m proofpane_workspace.settings --qwen --python /usr/bin/python3 \\\n"
            "        --dir /opt/proofpane/proofpane_workspace > /etc/qwen-code/settings.json") in dockerfile
    assert re.search(r"chmod 0644 [^\n]*\\\n\s+[^\n]*/etc/qwen-code/settings\.json", dockerfile)


def test_the_settings_keep_hooks_on_and_run_the_gate_before_every_tool():
    got = settings.qwen_settings("/usr/bin/python3", IMAGE_DIR)
    # "disableAllHooks" from any other settings file would turn the system's hooks off too: these say false, over it
    assert got["disableAllHooks"] is False
    assert got["general"] == {"enableAutoUpdate": False}
    hooks = got["hooks"]
    assert hooks["PreToolUse"] == [{"matcher": "*", "hooks": [{
        "name": "proofpane-gate", "type": "command", "command": f"/usr/bin/python3 -E -s -X utf8 {IMAGE_DIR}/gate.py",
        "timeout": settings.HOOK_TIMEOUT}]}]
    states = {event: (group.get("matcher"), group["hooks"][0]["command"].rsplit(" ", 1)[1])
              for event, (group,) in hooks.items() if event != "PreToolUse"}
    assert states == {"UserPromptSubmit": (None, "working"), "PostToolUse": ("*", "working"),
                      "Notification": ("permission_prompt|idle_prompt", "waiting"), "Stop": (None, "idle"),
                      "StopFailure": (None, "idle")}
    for group in (g for groups in hooks.values() for g in groups):
        for h in group["hooks"]:
            assert h["timeout"] < 1000           # Qwen reads a command hook's timeout of 1000 or more as milliseconds
            assert "--gemini" not in h["command"]     # Qwen takes Claude Code's answer, and adds what a hook prints
    out = subprocess.run([sys.executable, "-m", "proofpane_workspace.settings", "--qwen", "--python", "/usr/bin/python3",
                          "--dir", IMAGE_DIR], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert json.loads(out.stdout) == got


def test_the_gate_refuses_qwen_s_calls_in_the_answer_qwen_takes(tmp_path):
    home = str(tmp_path)
    for payload in (_qwen_call("run_shell_command", {"command": "cat ~/.qwen/oauth_creds.json"}),
                    _qwen_call("read_file", {"absolute_path": f"{home}/.qwen/oauth_creds.json"}),
                    _qwen_call("read_file", {"absolute_path": f"{home}/.qwen/mcp-oauth-tokens.json"}),
                    _qwen_call("run_shell_command", {"command": "base64 ~/.claude/.credentials.json"})):
        answer = _run_gate(payload, home)
        assert answer["hookSpecificOutput"]["permissionDecision"] == "deny", payload
        assert "sign-in" in answer["hookSpecificOutput"]["permissionDecisionReason"]
    # a link to the sign-in by another name is still the sign-in
    os.makedirs(os.path.join(home, ".qwen"))
    open(os.path.join(home, ".qwen", "oauth_creds.json"), "w").close()
    os.symlink(os.path.join(home, ".qwen", "oauth_creds.json"), os.path.join(home, "notes.txt"))
    assert _run_gate(_qwen_call("read_file", {"absolute_path": "~/notes.txt"}), home) is not None
    pushed = _run_gate(_qwen_call("run_shell_command", {"command": "git push -f origin main"}), home)
    assert "force push" in pushed["hookSpecificOutput"]["permissionDecisionReason"]
    assert _run_gate(_qwen_call("run_shell_command", {"command": "npm test"}), home) is None


def test_a_command_that_starts_qwen_without_the_gate_is_refused(tmp_path):
    # its safe and bare modes turn every hook off; its system settings, moved, leave the gate's behind
    home = str(tmp_path)
    for command in ("QWEN_CODE_SAFE_MODE=1 qwen -p hi", "qwen --safe-mode -p hi", "qwen -p hi --bare",
                    "QWEN_CODE_SIMPLE=1 qwen", "QWEN_CODE_SYSTEM_SETTINGS_PATH=/tmp/none qwen -p hi",
                    "unset QWEN_CODE_SAFE_MODE; qwen -p hi"):
        answer = _run_gate(_qwen_call("run_shell_command", {"command": command}), home)
        assert "gate is not switched off" in answer["hookSpecificOutput"]["permissionDecisionReason"], command
    # a flag that is only words in a prompt is not one
    assert _run_gate(_qwen_call("run_shell_command", {"command": "qwen -p 'add a --bare flag'"}), home) is None


def test_a_qwen_session_starts_with_its_safe_and_bare_modes_off(tmp_path):
    import time
    from workspace_fixtures import Agent, make_repo
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "qwen").write_text('#!/bin/sh\nprintf "%s %s" "${QWEN_CODE_SAFE_MODE-unset}" "${QWEN_CODE_SIMPLE-unset}"'
                                  ' > "$PP_SESSION_DIR/modes"\nsleep 600\n')
    (bin_dir / "qwen").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = a.http.post("/sessions", json={"repo": "demo", "program": "qwen", "task": "Go"}).json()
        said = tmp_path / "ws" / "sessions" / s["id"] / "modes"
        end = time.time() + 10
        while not (said.exists() and said.read_text()) and time.time() < end:
            time.sleep(0.05)
        assert said.read_text() == "0 0"      # set, so a repository's .env cannot set them
    finally:
        a.close()


QWEN = os.environ.get("PP_TEST_QWEN")


@pytest.mark.skipif(not QWEN, reason="PP_TEST_QWEN names no qwen program to run against a stand-in model")
def test_a_real_qwen_is_refused_by_the_gate_though_its_user_and_repository_turn_hooks_off(tmp_path):
    """Qwen Code's own run, its system settings from qwen_settings (named with QWEN_CODE_SYSTEM_SETTINGS_PATH, as the
    local provider does; in the image they are root's, at Qwen's own path). The user's settings and the repository's
    .qwen/settings.json both say `disableAllHooks: true`, and the repository adds a hook of its own that leaves a mark:
    its settings were read, and the gate ran all the same."""
    home, session = tmp_path / "home", tmp_path / "session"
    work = session / "work"
    (home / ".qwen").mkdir(parents=True)
    (work / ".qwen").mkdir(parents=True)
    (session / "session.json").write_text("{}")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    mark = tmp_path / "repository-hook-ran"
    (work / ".qwen" / "settings.json").write_text(json.dumps({"disableAllHooks": True, "hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": f"touch {mark}", "timeout": 10}]}]}}))
    # and its .env files ask for safe and bare mode, which the workspace's launch keeps off
    for env_file in (work / ".env", work / ".qwen" / ".env"):
        env_file.write_text("QWEN_CODE_SAFE_MODE=1\nQWEN_CODE_SIMPLE=1\n")
    system = tmp_path / "system-settings.json"
    system.write_text(json.dumps(settings.qwen_settings(sys.executable, os.path.join(ROOT, "proofpane_workspace"))))
    log = tmp_path / "standin.log"
    standin = subprocess.Popen([sys.executable, os.path.join(HERE, "codex_stand_in.py")], stdout=subprocess.PIPE,
                               text=True, env={"PATH": os.environ["PATH"], "HOME": str(home), "STANDIN_LOG": str(log),
                                               "STANDIN_COMMAND": "cat ~/.qwen/oauth_creds.json; echo RAN-$((40+2))"})
    try:
        port = int(standin.stdout.readline())
        (home / ".qwen" / "settings.json").write_text(json.dumps({
            "modelProviders": {"openai": [{"id": "standin", "wireApi": "responses", "envKey": "STANDIN_KEY",
                                           "baseUrl": f"http://127.0.0.1:{port}/v1"}]},
            "security": {"auth": {"selectedType": "openai"}}, "model": {"name": "standin"},
            "privacy": {"usageStatisticsEnabled": False}, "disableAllHooks": True}))
        run = subprocess.run([QWEN, "-p", "Show me the sign-in file.", "--approval-mode", "yolo"], cwd=work,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
                             env={"PATH": os.environ["PATH"], "HOME": str(home), "STANDIN_KEY": "x",
                                  "PP_SESSION_DIR": str(session), "QWEN_CODE_SYSTEM_SETTINGS_PATH": str(system),
                                  "QWEN_CODE_SUPPRESS_YOLO_WARNING": "1", "QWEN_CODE_SAFE_MODE": "0",
                                  "QWEN_CODE_SIMPLE": "0"})
    finally:
        standin.kill()
        standin.wait()
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    assert mark.exists()                                       # the repository's settings were read
    refused = [json.loads(line) for line in (session / "gate.jsonl").read_text().splitlines()]
    assert refused and refused[0]["tool"] == "run_shell_command" and "sign-in" in refused[0]["reason"]
    seen = log.read_text()
    assert "RAN-42" not in seen                                # the command never ran
    told = [json.loads(line)["output"] for line in seen.splitlines() if json.loads(line)["output"]]
    assert told and "Refused by the workspace's gate" in json.dumps(told[-1])
    assert json.loads((session / "activity.json").read_text())["state"] == "idle"
