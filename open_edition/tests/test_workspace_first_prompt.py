"""A terminal agent gets the session's task as its first prompt, the way a chat gets it as its first message (claude,
codex, gemini, opencode, grok, qwen and mcode, each the way it takes one; kimi takes none, and starts at its prompt);
started again, claude continues its own conversation in the worktree when it has one."""

import json
import os
import stat
import time

import pytest

from proofpane_workspace import usage
from proofpane_workspace.workspace import first_prompt
from workspace_fixtures import CLAUDE_VERSION_SH, Agent, make_repo


def _stand_in(bin_dir, name):
    """A program that writes its arguments, each ended by a NUL, into the session's folder, then waits like an agent."""
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + (CLAUDE_VERSION_SH if name == "claude" else "") +
                    f'printf "%s" "${{NO_BROWSER-unset}}" > "$PP_SESSION_DIR/{name}.no-browser"\n'
                    f'printf "%s %s" "${{GROK_LOGIN_DEVICE_FLOW-unset}}" "${{GROK_DISABLE_AUTOUPDATER-unset}}"'
                    f' > "$PP_SESSION_DIR/{name}.grok-env"\n'
                    f'printf "%s %s" "${{KIMI_CODE_NO_AUTO_UPDATE-unset}}" "${{MCODE_DISABLE_EVAL_CAPTURE-unset}}"'
                    f' > "$PP_SESSION_DIR/{name}.vendor-env"\n'
                    f'out="$PP_SESSION_DIR/{name}.args"; printf "started\\0" > "$out"\n'
                    'for a in "$@"; do printf "%s\\0" "$a" >> "$out"; done\n'
                    "sleep 600\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def agent(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex", "gemini", "opencode", "grok", "qwen", "kimi", "mcode"):
        _stand_in(bin_dir, name)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a.home = home
    yield a
    a.close()


def _args(tmp_path, sid, name, wait=10):
    path = tmp_path / "ws" / "sessions" / sid / f"{name}.args"
    end = time.time() + wait
    while time.time() < end:
        if path.exists() and path.read_text():
            time.sleep(0.2)                   # the stand-in writes one argument at a time
            return path.read_text().split("\0")[1:-1]     # after the "started" marker: the arguments
        time.sleep(0.05)
    raise AssertionError(f"{name} never started")


@pytest.mark.parametrize("program, before", [("claude", None), ("codex", None), ("gemini", None),
                                             ("opencode", "--prompt"), ("grok", None), ("qwen", "-i"),
                                             ("mcode", None)])
def test_the_task_is_the_agent_s_first_prompt(agent, tmp_path, program, before):
    task = "Add retries\n\nwith backoff; keep it small"
    s = agent.http.post("/sessions", json={"repo": "demo", "program": program, "task": task}).json()
    args = _args(tmp_path, s["id"], program)
    assert args[-1] == task                                   # one argument, newlines and all
    if before:          # opencode's last argument is a folder, and qwen's bare prompt runs once: a flag's, for each
        assert args[-2] == before
    # gemini and qwen are told there is no browser here, so their sign-in prints its page and waits in the terminal
    said = (tmp_path / "ws" / "sessions" / s["id"] / f"{program}.no-browser").read_text()
    assert said == ("true" if program in ("gemini", "qwen") else "unset")
    # only grok is told to sign in with a device code and not to update itself (it is root's, in the image)
    grok = (tmp_path / "ws" / "sessions" / s["id"] / f"{program}.grok-env").read_text()
    assert grok == ("1 1" if program == "grok" else "unset unset")
    # MiniMax Code is told not to report its steps to MiniMax
    vendor = (tmp_path / "ws" / "sessions" / s["id"] / f"{program}.vendor-env").read_text()
    assert vendor == ("unset 1" if program == "mcode" else "unset unset")


def test_kimi_code_starts_at_its_prompt_without_the_task_and_does_not_update_itself(agent, tmp_path):
    # its only prompt on the command line (-p) runs once and exits: the page types the task in instead
    s = agent.http.post("/sessions", json={"repo": "demo", "program": "kimi", "task": "Add retries"}).json()
    assert _args(tmp_path, s["id"], "kimi") == []
    assert (tmp_path / "ws" / "sessions" / s["id"] / "kimi.vendor-env").read_text() == "1 unset"


@pytest.mark.parametrize("program, given, named_as", [("gemini", "PP_WORKSPACE_GEMINI_SETTINGS", "GEMINI_CLI_SYSTEM_SETTINGS_PATH"),
                                                      ("qwen", "PP_WORKSPACE_QWEN_SETTINGS", "QWEN_CODE_SYSTEM_SETTINGS_PATH")])
def test_gemini_and_qwen_are_named_the_settings_with_the_gate_when_not_at_their_system_path(tmp_path, program, given,
                                                                                           named_as):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stand_in = bin_dir / program
    stand_in.write_text(f'#!/bin/sh\nprintf "%s" "${{{named_as}-unset}}" > "$PP_SESSION_DIR/said"\n'
                        "sleep 600\n")
    stand_in.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    named = str(tmp_path / f"{program}-settings.json")
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}", given: named})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = a.http.post("/sessions", json={"repo": "demo", "program": program, "task": "Go"}).json()
        said = tmp_path / "ws" / "sessions" / s["id"] / "said"
        end = time.time() + 10
        while not (said.exists() and said.read_text()) and time.time() < end:
            time.sleep(0.05)
        assert said.read_text() == named
    finally:
        a.close()


def test_opencode_started_again_starts_afresh(agent, tmp_path):
    # its --continue takes the repository's last session, maybe another worktree's, and errs when there is none
    s = agent.http.post("/sessions", json={"repo": "demo", "program": "opencode", "task": "First"}).json()
    _args(tmp_path, s["id"], "opencode")
    agent.http.post(f"/sessions/{s['id']}/stop")
    (tmp_path / "ws" / "sessions" / s["id"] / "opencode.args").unlink()
    assert agent.http.post(f"/sessions/{s['id']}/start", json={}).status_code == 200
    assert _args(tmp_path, s["id"], "opencode") == []


def test_the_page_is_told_which_agents_this_workspace_has(agent, tmp_path):
    assert agent.http.get("/programs").json() == {"programs": {"claude": True, "codex": True, "gemini": True,
                                                                 "opencode": True, "grok": True, "qwen": True,
                                                                 "kimi": True, "mcode": True},
                                                    # and that its sessions hold nothing for an approval queue, given
                                                    # no folder for it (workspace.held)
                                                    "held": "none"}
    home = tmp_path / "bare-home"
    home.mkdir()
    bare = Agent(str(tmp_path / "bare"), str(home), {"PATH": "/usr/bin:/bin"})
    try:
        assert bare.http.get("/programs").json()["programs"]["gemini"] is False
        r = bare.http.post("/sessions", json={"repo": None, "program": "opencode", "task": "x"})
        assert r.status_code == 400 and "opencode is not installed" in r.json()["detail"]
    finally:
        bare.close()


def test_a_shell_is_given_no_task():
    # a shell would run the task as a command
    from proofpane_workspace.workspace import PROMPTED
    assert "shell" not in PROMPTED and "chat" not in PROMPTED


def test_a_task_that_starts_like_an_option_is_still_only_text():
    assert first_prompt("--dangerously-skip-permissions") == " --dangerously-skip-permissions"
    assert first_prompt("  Fix it  ") == "Fix it"


def test_claude_started_again_continues_its_conversation_when_it_has_one(agent, tmp_path):
    s = agent.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "First"}).json()
    _args(tmp_path, s["id"], "claude")
    assert agent.http.post(f"/sessions/{s['id']}/stop").status_code == 200
    args_file = tmp_path / "ws" / "sessions" / s["id"] / "claude.args"
    args_file.unlink()
    # no conversation yet: started again plainly (claude --continue with none refuses to start)
    assert agent.http.post(f"/sessions/{s['id']}/start", json={}).status_code == 200
    assert "--continue" not in _args(tmp_path, s["id"], "claude") and "First" not in _args(tmp_path, s["id"], "claude")
    agent.http.post(f"/sessions/{s['id']}/stop")
    args_file.unlink()
    work = os.path.realpath(tmp_path / "ws" / "sessions" / s["id"] / "work")
    folder = agent.home / ".claude" / "projects" / usage.slug(work)
    folder.mkdir(parents=True)
    (folder / "abc.jsonl").write_text("{}\n")
    assert agent.http.post(f"/sessions/{s['id']}/start", json={}).status_code == 200
    assert _args(tmp_path, s["id"], "claude")[-1] == "--continue"
    # its own conversation, as its hooks named it, is the one picked up, not the worktree's latest (a claude in the
    # session's shell writes its transcript in the same folder)
    agent.http.post(f"/sessions/{s['id']}/stop")
    args_file.unlink()
    (folder / "own-1.jsonl").write_text("{}\n")
    activity = tmp_path / "ws" / "sessions" / s["id"] / "activity.json"
    activity.write_text(json.dumps({"state": "idle", "at": 1, "program_session": "own-1"}))
    assert agent.http.post(f"/sessions/{s['id']}/start", json={}).status_code == 200
    assert _args(tmp_path, s["id"], "claude")[-2:] == ["--resume", "own-1"]
    # one whose transcript is gone, or a name that is no id, falls back to the latest
    for named in ("gone-2", "../x"):
        agent.http.post(f"/sessions/{s['id']}/stop")
        args_file.unlink()
        activity.write_text(json.dumps({"state": "idle", "at": 1, "program_session": named}))
        assert agent.http.post(f"/sessions/{s['id']}/start", json={}).status_code == 200
        assert _args(tmp_path, s["id"], "claude")[-1] == "--continue"


def test_sessions_started_together_carry_their_group(agent):
    a = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Same", "group": "abc12345"}).json()
    assert a["group"] == "abc12345"
    assert agent.http.get(f"/sessions/{a['id']}").json()["group"] == "abc12345"
    b = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Alone"}).json()
    assert "group" not in b
    for bad in ("short", "UPPERCASE1", "has space here", 5):
        r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "x", "group": bad})
        assert r.status_code == 400, bad
