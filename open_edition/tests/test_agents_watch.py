"""What an agent's run could leave to start later, outside its claude, its time limit and its gate (watch.py,
processes.py, agents.py): looked at when it starts and again at its end, before any git; what it started ended with
it. Through a real workspace agent with the stand-in claude, whose "run <command>" runs a command once allowed."""

import json
import os
import shlex
import sys
import time

import pytest

from test_agents_runs import AGENT, Workspace, _answer, put, run, until
from workspace_fixtures import git

# A process the stand-in's "[command]" leaves running is in a session of its own, as a Bash call's is (Claude Code
# starts each command detached), and its command's shell ends at once. On a Mac `ps -E` shows no environment of macOS's
# platform binaries, so one Apple's sleep runs there is reached by neither its mark nor any session the run owns, since
# no look saw its call running: this change does not end it (processes.py; a Mac runner that reaches a Bash call's
# process group through Claude Code's shell prefix is not built here). There these tests run with a sleep of a
# program that is not a platform binary, whose mark ps shows, which finds it as before the change (a control);
# elsewhere with sleep, as before.
APPLE_SLEEP_UNREACHED = ("on a Mac, Apple's own sleep left by a Bash call that ended before any look saw it running "
                         "(as the stand-in's [command] ends at once) shows ps no environment and is in no session the "
                         "run owns, so this change does not reach it: processes.py says so")
SLEEP_NOT_A_PLATFORM_BINARY = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(300)'"
ON_A_MAC_ALONE = pytest.mark.skipif(sys.platform != "darwin",
                                    reason="a Mac's own case: elsewhere the mark shows whatever the program")
SLEEPERS = [
    pytest.param("sleep 300", id="sleep",
                 marks=pytest.mark.skipif(sys.platform == "darwin", reason=APPLE_SLEEP_UNREACHED)),
    pytest.param(SLEEP_NOT_A_PLATFORM_BINARY, id="a-sleep-not-a-platform-binary", marks=ON_A_MAC_ALONE),
]
# The stand-in's "run <command>" keeps its command in the claude's own session, as Claude Code keeps its MCP servers; a
# real Bash call's runs in a session of its own (detached), as "[command]"'s does. So on a Mac what a real call leaves
# there with Apple's sleep, once the call has ended before any look saw it running, is reached by nothing, and a test of
# a call's case run through "run" would pass there for the stand-in's way alone: there it runs with a sleep of a
# program that is not a platform binary, and elsewhere with sleep, as before.
RUN_UNREACHED = ("on a Mac, a real Bash call's command runs in a session of its own (Claude Code starts it detached), "
                 "where Apple's own sleep it leaves, the call ended before any look saw it running, shows ps no "
                 "environment and is in no session the run owns, so this change does not reach it; the stand-in's "
                 "\"run\" keeps it in the claude's own session, where it would pass for that alone: processes.py "
                 "says so")
RUN_SLEEPERS = [
    pytest.param("sleep 300", id="sleep", marks=pytest.mark.skipif(sys.platform == "darwin", reason=RUN_UNREACHED)),
    pytest.param(SLEEP_NOT_A_PLATFORM_BINARY, id="a-sleep-not-a-platform-binary", marks=ON_A_MAC_ALONE),
]
# after a machine stop, besides: the agent that starts again keeps none of the run's sessions, so the mark alone finds
# what the run left, wherever it is
SLEEPERS_AFTER_A_STOP = [
    pytest.param("sleep 300", id="sleep", marks=pytest.mark.skipif(sys.platform == "darwin", reason=(
        APPLE_SLEEP_UNREACHED + "; and the agent started again after a machine stop keeps none of the run's "
        "sessions, so even one left in the run's own session is found by its mark alone"))),
    pytest.param(SLEEP_NOT_A_PLATFORM_BINARY, id="a-sleep-not-a-platform-binary", marks=ON_A_MAC_ALONE),
]


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    yield w
    w.stop()


def script(ws, name, text):
    """A script the run's command runs: what it does is written here, not in the brief's one paragraph."""
    path = ws.tmp / name
    path.write_text("#!/bin/sh\nset -e\n" + text)
    path.chmod(0o755)
    return str(path)


def paused(ws):
    return ws.http.get(f"/agents/{AGENT}").json()["paused"]


def test_a_hook_a_run_plants_never_runs_and_no_git_runs_after_it(ws):
    common = os.path.join(ws.root, "repos", "demo", ".git")
    marker = ws.tmp / "hook-ran"
    plant = script(ws, "plant.sh", f"printf '#!/bin/sh\\ntouch {marker}\\n' > {common}/hooks/post-commit\n"
                                   f"chmod +x {common}/hooks/post-commit\necho made > made.txt\n")
    put(ws, f"run {plant}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    ended = until(ws, r, ("blocked",))
    assert ended["cause"] == "watch" and ended["reason"].startswith(
        "Git's settings changed while the run worked (the repository's .git/hooks/post-commit), so no git ran after it")
    assert ended["hold"] == ended["reason"]
    assert not marker.exists()
    work = os.path.join(ws.root, "agents", AGENT, "work")
    assert git("status", "--porcelain", cwd=work).strip() == "?? made.txt"      # left as it was: no git ran
    assert paused(ws) is True
    refused = ws.http.post(f"/agents/{AGENT}/runs", json={"trigger": {"kind": "manual"}})   # held till resumed
    assert refused.status_code == 400 and "paused" in refused.json()["detail"]


def test_the_workspace_s_own_git_for_a_run_runs_no_hook_the_repository_had(ws):
    common = os.path.join(ws.root, "repos", "demo", ".git")
    marker = ws.tmp / "hook-ran"
    hook = os.path.join(common, "hooks", "post-commit")
    with open(hook, "w") as fh:
        fh.write(f"#!/bin/sh\ntouch {marker}\n")
    os.chmod(hook, 0o755)
    put(ws, "run echo made > made.txt")
    r = run(ws)["run_id"]
    _answer(ws, r)
    done = until(ws, r, ("done",))
    assert done["head"] != done["base"] and done["hold"] is None                # its change committed, nothing held
    assert not marker.exists()                                                  # the repository's own hook did not run
    assert paused(ws) is False


def test_a_run_that_changes_where_a_program_starts_from_is_blocked_and_its_agent_held(ws):
    put(ws, f"run {script(ws, 'rc.sh', 'echo made > made.txt; echo alias ls=true >> ' + str(ws.home / '.bashrc'))}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    ended = until(ws, r, ("blocked",))
    assert ended["cause"] == "watch" and ended["reason"].startswith("Changed while the run worked: ~/.bashrc.")
    assert "Git's settings" not in ended["reason"]
    assert ended["head"] != ended["base"]                                        # git's places held: its change committed
    assert paused(ws) is True


def test_a_change_to_its_kept_worktree_s_claude_settings_holds_the_next_run(ws):
    put(ws, f"run {script(ws, 'claude.sh', 'mkdir -p .claude; echo {} > .claude/settings.json')}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    done = until(ws, r, ("done",))                                              # the run itself ended as it ended
    assert done["hold"].startswith("The run changed .claude/settings.json in its worktree")
    assert paused(ws) is True


def test_a_run_cannot_change_what_its_agent_lets_it_do(ws):
    record = os.path.join(ws.root, "agents", AGENT, "agent.json")
    tamper = script(ws, "tamper.sh", f"""python3 - <<'EOF'
import json
a = json.load(open({record!r}))
a["allow"] = ["Bash(*)"]
a["chat_settings"]["permission_mode"] = "bypassPermissions"
json.dump(a, open({record!r}, "w"))
EOF
""")
    put(ws, f"run {tamper}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    ended = until(ws, r, ("blocked",))
    assert f"Changed while the run worked: the workspace's agents/{AGENT}/agent.json." in ended["reason"]
    kept = ws.http.get(f"/agents/{AGENT}").json()
    assert kept["allow"] == [] and "permission_mode" not in kept["chat_settings"] and kept["paused"] is True


@pytest.mark.parametrize("sleeper", RUN_SLEEPERS)
def test_what_a_run_started_is_ended_with_it(ws, sleeper):
    pidfile = ws.tmp / "pid"
    put(ws, f"run {script(ws, 'bg.sh', f'({sleeper} >/dev/null 2>&1 & echo $! > {pidfile})')}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    until(ws, r, ("done",))
    assert _gone(int(pidfile.read_text())), "the run's process outlived it"


def test_what_a_run_s_claude_starts_in_its_own_session_is_ended_with_it_even_apple_s_sleep(ws):
    # what the claude itself starts stays in its session, as its MCP servers do (RUN_SLEEPERS' note: the stand-in's
    # "run" keeps its command there): on a Mac what it leaves there with Apple's sleep is ended by the session the run's
    # claude leads, as its wrapper says it is; elsewhere by its mark
    pidfile = ws.tmp / "pid"
    put(ws, f"run {script(ws, 'bg.sh', f'(sleep 300 >/dev/null 2>&1 & echo $! > {pidfile})')}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    until(ws, r, ("done",))
    assert _gone(int(pidfile.read_text())), "what the run's claude started in its own session outlived it"


def test_what_a_run_s_claude_started_again_leaves_in_its_own_session_is_ended_with_the_run_too(ws):
    # its claude stopped to wait for the owner and started again for their answer: on a Mac the second leads a session
    # of its own too, taken as the run's as it connects, so what it leaves there (RUN_SLEEPERS' note: the stand-in's
    # "run" keeps its command in the claude's own session) is ended with the run; elsewhere the mark finds it
    pidfile = ws.tmp / "pid"
    put(ws, "ask Which tag?")
    r = run(ws)["run_id"]
    until(ws, r, ("waiting",))
    bg = script(ws, "bg.sh", f"(sleep 300 >/dev/null 2>&1 & echo $! > {pidfile})")
    assert ws.http.post(f"/sessions/{r}/chat", json={"text": f"run {bg}"}).status_code == 200
    _answer(ws, r)
    until(ws, r, ("done",))
    assert _gone(int(pidfile.read_text())), "what its claude started again left outlived the run"


def _gone(pid, timeout=10):
    deadline = time.time() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        if os.path.exists(f"/proc/{pid}/stat") and open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] == "Z":
            return True
        if time.time() > deadline:
            return False
        time.sleep(0.1)


@ON_A_MAC_ALONE
def test_on_a_mac_a_bash_call_a_look_saw_running_is_the_run_s_and_so_is_the_apple_s_sleep_it_leaves(ws):
    # "[command]" runs as a Bash call does, in a session of its own, a child of the claude: while it runs, the agent's
    # look at the run's sessions, every 30 seconds, takes the call's session as the run's, so what it left there with
    # Apple's sleep is ended with the run's wait, after its claude has gone (a call no look saw running is not)
    call, left = ws.tmp / "call", ws.tmp / "left"
    put(ws, f"[sleep 300 >/dev/null 2>&1 & echo $! > {left}; echo $$ > {call}; exec sleep 300] later 40 ask Which tag?")
    r = run(ws)["run_id"]
    until(ws, r, ("waiting",), timeout=90)
    assert _gone(int(left.read_text())) and _gone(int(call.read_text())), "what the call left outlived the run's wait"


@pytest.mark.parametrize("sleeper", SLEEPERS)
def test_a_run_waiting_for_the_owner_holds_no_process_it_started(ws, sleeper):
    pidfile = ws.tmp / "pid"
    put(ws, f"[{sleeper} & echo $! > {pidfile}] ask Which tag?")
    r = run(ws)["run_id"]
    waiting = until(ws, r, ("waiting",))
    assert waiting["question"] == "Which tag?"
    deadline = time.time() + 10
    while not pidfile.exists() or not pidfile.read_text().strip():
        assert time.time() < deadline
        time.sleep(0.05)
    assert _gone(int(pidfile.read_text())), "the run's process outlived its wait"


def test_a_stop_while_its_claude_is_stopped_to_wait_for_the_owner_ends_the_run(ws):
    """The owner's stop landing while the run's claude is being stopped to wait for their answer (what it started
    being ended: here a process that ignores the polite signal, so two seconds) ends the run; the wait does not make
    it waiting after, nor was the stop dropped as the end of a run already ending."""
    from test_agents_runs import view
    stubborn = script(ws, "stubborn.sh", "trap '' TERM\nsleep 300\n")
    put(ws, f"[{stubborn} &] ask Which tag?")
    r = run(ws)["run_id"]
    deadline = time.time() + 30
    while not (view(ws, r).get("question") == "Which tag?" and view(ws, r)["state"] == "working"
               and ws.http.get(f"/sessions/{r}").json()["chat"]["state"] == "stopped"):
        assert time.time() < deadline, view(ws, r)
        time.sleep(0.02)
    assert ws.http.post(f"/agents/runs/{r}/stop").status_code == 200
    ended = until(ws, r, ("stopped", "waiting"))
    assert (ended["state"], ended["reason"]) == ("stopped", "stopped by you")
    time.sleep(3)                                                   # past the wait's own end
    assert view(ws, r)["state"] == "stopped"


def test_an_answer_sent_while_its_claude_is_stopped_to_wait_for_it_is_kept(ws):
    """The owner answers while the run's claude is being stopped to wait for them (what it started being ended: a
    process that ignores the polite signal, so two seconds): the answer waits for that stop on the run's send lock,
    nothing waits on anything forever, and the answer reaches claude and the run ends on it. (The few milliseconds in
    which an answer's claude went with the stop are held by the lock's own tests in test_agents_runs.py.)"""
    import threading
    from test_agents_runs import view
    stubborn = script(ws, "stubborn.sh", "trap '' TERM\nsleep 300\n")
    put(ws, f"[{stubborn} &] ask Which tag?")
    r = run(ws)["run_id"]
    deadline = time.time() + 30
    while not (view(ws, r).get("question") == "Which tag?" and view(ws, r)["state"] == "working"
               and ws.http.get(f"/sessions/{r}").json()["chat"]["state"] == "stopped"):
        assert time.time() < deadline, view(ws, r)
        time.sleep(0.02)
    answering = threading.Thread(target=lambda: ws.http.post(f"/agents/runs/{r}/message", json={"text": "v1.4.2"}))
    answering.start()
    answering.join(30)
    done = until(ws, r, ("done", "failed", "stopped", "blocked"), timeout=30)
    assert (done["state"], done.get("question")) == ("done", None), done
    said = [e.get("text") for e in ws.http.get(f"/sessions/{r}/chat").json()["events"] if e["kind"] == "user"]
    assert said[-1] == "v1.4.2"                         # the answer reached claude, and the run ended on it


@pytest.mark.parametrize("sleeper", SLEEPERS_AFTER_A_STOP)
def test_a_machine_stop_ends_what_its_live_run_started(tmp_path, sleeper):
    w = Workspace(tmp_path)
    pidfile = tmp_path / "pid"
    try:
        put(w, f"[{sleeper} & echo $! > {pidfile}] wait")
        r = run(w)["run_id"]
        until(w, r, ("working",))
        deadline = time.time() + 30                     # its claude's start, then its command: seconds, more on a full run
        while not pidfile.exists() or not pidfile.read_text().strip():
            assert time.time() < deadline
            time.sleep(0.05)
        pid = int(pidfile.read_text())
        w.stop()
        assert not _gone(pid, timeout=1)                    # the agent stopping is not what ends it
        w.start()
        until(w, r, ("blocked",))
        assert _gone(pid), "a run's process outlived the agent's start again"
    finally:
        w.stop()


def test_a_mirror_sent_before_the_app_knew_of_a_hold_does_not_lift_it(ws):
    put(ws, f"run {script(ws, 'rc.sh', 'echo x >> ' + str(ws.home / '.profile'))}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    until(ws, r, ("blocked",))
    put(ws, f"run {script(ws, 'rc.sh', 'true')}")                              # the app, not yet knowing
    assert paused(ws) is True
    put(ws, "hello", paused=True)
    assert paused(ws) is True
    put(ws, "hello")                                                            # the owner resumed it
    assert paused(ws) is False
    assert until(ws, run(ws)["run_id"], ("done",))["state"] == "done"


def test_the_record_given_to_the_app_names_the_hold(ws):
    put(ws, f"run {script(ws, 'rc.sh', 'echo x >> ' + str(ws.home / '.zshrc'))}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    until(ws, r, ("blocked",))
    (got,) = [v for v in ws.http.get("/agents/runs?since=0").json()["runs"] if v["run_id"] == r]
    assert got["hold"] and "~/.zshrc" in got["hold"] and json.dumps(got)
