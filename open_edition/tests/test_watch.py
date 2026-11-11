"""The look at what a run could leave to start later (watch.py), the ending of what it started (processes.py), and the
git the workspace runs for it (workspace.RUN_GIT)."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from proofpane_workspace import processes, watch, workspace


def test_a_place_changed_made_or_removed_is_a_change(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("{}")
    folder = tmp_path / "hooks"
    folder.mkdir()
    gone = tmp_path / "gone"
    gone.write_text("x")
    places = [str(f), str(folder), str(gone), str(tmp_path / "none")]
    before = watch.look(places)
    assert watch.changes(before, watch.look(places)) == []
    f.write_text("{}")                                   # the same bytes again: its change time moves on
    (folder / "post-commit").write_text("#!/bin/sh\n")
    gone.unlink()
    (tmp_path / "none").write_text("made")
    assert watch.changes(before, watch.look(places)) == sorted(
        [str(f), str(folder), str(folder / "post-commit"), str(gone), str(tmp_path / "none")])


def test_a_mode_alone_or_a_link_s_target_is_a_change(tmp_path):
    hook = tmp_path / "hook"
    hook.write_text("#!/bin/sh\n")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "one")
    before = watch.look([str(hook), str(link)])
    hook.chmod(0o755)
    link.unlink()
    link.symlink_to(tmp_path / "two")
    assert watch.changes(before, watch.look([str(hook), str(link)])) == [str(hook), str(link)]


def test_the_workspace_s_own_write_is_no_change_but_one_after_it_is(tmp_path):
    f = tmp_path / "agent.json"
    f.write_text("{}")
    before = watch.look([str(f)])
    f.write_text('{"paused": true}')
    watch.wrote(str(f))
    assert watch.changes(before, watch.look([str(f)])) == []
    f.write_text('{"allow": ["Bash(*)"]}')
    assert watch.changes(before, watch.look([str(f)])) == [str(f)]


def test_a_folder_is_read_to_its_most_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "MAX_ENTRIES", 3)
    folder = tmp_path / "plugins"
    folder.mkdir()
    for i in range(5):
        (folder / f"p{i}").write_text("x")
    got = watch.look([str(folder)])
    assert sum(1 for k in got if k.startswith(str(folder) + os.sep)) == 4         # three, and the mark of more
    assert got[str(folder) + os.sep + "..."] == ("more than", watch.MAX_ENTRIES)


def test_a_record_is_read_by_the_part_that_matters(tmp_path):
    f = tmp_path / ".claude.json"
    f.write_text(json.dumps({"numStartups": 1, "mcpServers": {}, "projects": {"/w": {"mcpServers": {}, "x": 1}}}))
    keys = (("mcpServers",), ("projects", "*", "mcpServers"))
    records = [("its MCP servers", lambda: watch.json_part(str(f), keys))]
    before = watch.look([], records)
    f.write_text(json.dumps({"numStartups": 9, "mcpServers": {}, "projects": {"/w": {"mcpServers": {}, "x": 2}}}))
    assert watch.changes(before, watch.look([], records)) == []                   # what the program rewrites itself
    f.write_text(json.dumps({"mcpServers": {"x": {"command": "sh"}}, "projects": {"/w": {"mcpServers": {}}}}))
    assert watch.changes(before, watch.look([], records)) == ["record:its MCP servers"]
    assert watch.json_part(str(tmp_path / "none"), keys) is None


def test_places_are_named_as_the_owner_reads_them(tmp_path):
    home, root, repo = "/h", "/h/ws", "/h/ws/repos/demo/.git"
    names = watch.named([f"{repo}/hooks", f"{repo}/hooks/post-commit", "/h/.bashrc", "/h/ws/agents/a/agent.json",
                         "record:the MCP servers in ~/.claude.json", "/h/.claude/plugins" + os.sep + "..."],
                        home, root, repo)
    assert names == ["the repository's .git/hooks/post-commit", "~/.bashrc", "the workspace's agents/a/agent.json",
                     "the MCP servers in ~/.claude.json", "~/.claude/plugins (more entries than are read)"]


def test_a_process_carrying_a_run_s_mark_is_ended_and_no_other(tmp_path):
    run_id = "abcabcabcabc"
    script = "import time; time.sleep(60)"
    marked = subprocess.Popen([sys.executable, "-c", script], env=dict(os.environ, **{processes.MARK: run_id}))
    other = subprocess.Popen([sys.executable, "-c", script], env=dict(os.environ, **{processes.MARK: "b" * 12}))
    try:
        deadline = time.time() + 10
        while marked.pid not in processes.marked(run_id):
            assert time.time() < deadline
            time.sleep(0.05)
        assert other.pid not in processes.marked(run_id)
        assert processes.end(run_id, wait=5) == 1
        assert marked.wait(timeout=10) is not None
        assert other.poll() is None
    finally:
        for p in (marked, other):
            p.kill()
            p.wait()


def test_on_linux_the_mark_is_read_from_each_process_s_environment(tmp_path):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    for pid, env in (("101", b"A=1\0PP_AGENT_RUN_ID=abcabcabcabc\0"), ("102", b"PP_AGENT_RUN_ID=abcabcabcabcd\0"),
                     ("103", b"X=PP_AGENT_RUN_ID=abcabcabcabc\0"), ("104", b"PP_AGENT_RUN_ID=bbbbbbbbbbbb\0"),
                     (str(os.getpid()), b"PP_AGENT_RUN_ID=abcabcabcabc\0")):
        (proc / pid).mkdir()
        (proc / pid / "environ").write_bytes(env)
    (proc / "105").mkdir()                                  # gone, or not readable: passed over
    assert processes.marked("abcabcabcabc", proc=str(proc)) == [101]


def test_git_in_an_agent_s_worktree_runs_no_hook(tmp_path):
    guarded = tmp_path / "agents"
    (guarded / "a" / "work").mkdir(parents=True)
    workspace.guard(str(guarded))
    run_git = workspace._config_pairs(workspace.RUN_GIT)
    # RUN_GIT is the pairs _git_env adds, in order, all of them
    assert run_git == list(workspace._RUN_PAIRS)
    for own in (False, True):           # in an agent's worktree as RUN_GIT says, the owner's own action there too
        inside = workspace._config_pairs(workspace._git_env(str(guarded / "a" / "work"), own=own))
        assert inside[:len(run_git)] == run_git and inside[0] == ("core.hooksPath", "/dev/null")
    # elsewhere every read of the workspace's as RUN_GIT says too (gitplaces.py); the owner's own action alone runs as
    # his own git would
    assert workspace._config_pairs(workspace._git_env(str(tmp_path)))[:len(run_git)] == run_git
    assert workspace._config_pairs(workspace._git_env(str(tmp_path), own=True)) == []


def test_the_kill_after_the_wait_reads_the_mark_again_first(monkeypatch):
    run_id = "cdcdcdcdcdcd"
    # it says it is ready once it ignores SIGTERM: on Linux its mark is in /proc from its exec, before the handler is
    # set, and a SIGTERM before it would end it
    stubborn = subprocess.Popen([sys.executable, "-c", "import signal, sys, time; signal.signal(signal.SIGTERM, "
                                                       "signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"],
                                env=dict(os.environ, **{processes.MARK: run_id}), stdout=subprocess.PIPE)
    try:
        assert stubborn.stdout.readline() == b"ready\n"
        deadline = time.time() + 10
        while stubborn.pid not in processes.marked(run_id):
            assert time.time() < deadline
            time.sleep(0.05)
        real, asked = processes.marked, []

        def gone_by_then(rid, proc="/proc", among=None):
            asked.append(among)
            return [] if among is not None else real(rid, proc)    # as a reused id's would read: no mark
        monkeypatch.setattr(processes, "marked", gone_by_then)
        processes.end(run_id, wait=0.5)
        assert stubborn.poll() is None and asked[-1] == [stubborn.pid]
        monkeypatch.setattr(processes, "marked", real)
        processes.end(run_id, wait=0.5)
        assert stubborn.wait(timeout=10) is not None
    finally:
        stubborn.kill()
        stubborn.wait()


# On a Mac (processes.py): ps -E shows no environment of
# macOS's platform binaries, so what a run leaves with one is ended by the sessions the run owns; on Linux /proc shows
# the mark whatever the program, and nothing of a run's program's session changes.
on_a_mac = pytest.mark.skipif(sys.platform != "darwin", reason="a Mac's own: /proc shows the mark elsewhere")
SLEEP_NOT_A_PLATFORM_BINARY = [sys.executable, "-c", "import time; time.sleep(300)"]


def _run_id():
    # a run's id of the test's own: another run of these tests at once on the same Mac ends nothing of this one's
    import secrets
    return secrets.token_hex(6)


def _pid_in(path, timeout=10):
    deadline = time.time() + timeout
    while not (os.path.exists(path) and open(path).read().strip()):
        assert time.time() < deadline, path
        time.sleep(0.05)
    return int(open(path).read())


def _running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _until_gone(pid, timeout=10):
    deadline = time.time() + timeout
    while _running(pid) and time.time() < deadline:
        time.sleep(0.05)
    return not _running(pid)


def _kill(*pids):
    for p in pids:
        if p:
            try:
                os.kill(p, 9)
            except OSError:
                pass


@on_a_mac
def test_on_a_mac_what_a_run_leaves_in_its_own_session_is_ended_whatever_its_program(tmp_path):
    run_id = _run_id()
    files = [tmp_path / n for n in ("subshell", "nohup", "env-i", "env-i-not-a-platform-binary", "own-group")]
    not_a_platform_binary = " ".join(f"'{w}'" for w in SLEEP_NOT_A_PLATFORM_BINARY)
    shell = processes.start(["/bin/sh", "-c", f"(sleep 300 >/dev/null 2>&1 & echo $! > {files[0]}); "
                             f"nohup sleep 300 >/dev/null 2>&1 & echo $! > {files[1]}; "
                             f"env -i /bin/sleep 300 >/dev/null 2>&1 & echo $! > {files[2]}; "
                             # a program that is not a platform binary whose mark is gone: the session alone finds it
                             f"env -i {not_a_platform_binary} >/dev/null 2>&1 & echo $! > {files[3]}; "
                             # a process group of its own, in the same session
                             f"perl -e 'setpgrp(0, 0); exec @ARGV' /bin/sleep 300 >/dev/null 2>&1 "
                             f"& echo $! > {files[4]}"],
                            run_id, env=dict(os.environ, **{processes.MARK: run_id}))
    shell.wait(timeout=10)                              # the shell is gone: what it started left orphaned, five ways
    elsewhere = subprocess.Popen(["/bin/sleep", "300"])  # this agent's own session's: never the run's
    pids = [_pid_in(f) for f in files]
    try:
        deadline = time.time() + 10
        while os.getpgid(pids[4]) != pids[4]:           # its own group made
            assert time.time() < deadline
            time.sleep(0.05)
        assert not any(p in processes.marked(run_id) for p in pids)       # ps -E shows none of them the mark
        assert sorted(processes.in_sessions(run_id)) == sorted(pids)
        assert processes.end(run_id, wait=5) == 5
        assert all(_until_gone(p) for p in pids)
        assert elsewhere.poll() is None
    finally:
        elsewhere.kill()
        elsewhere.wait()
        _kill(*pids)


@on_a_mac
def test_on_a_mac_what_leaves_the_run_s_session_is_reached_by_its_mark_alone(tmp_path, monkeypatch):
    run_id = _run_id()
    # no look but the end's: a look while the shell runs would walk from it to what it started (as it should)
    monkeypatch.setattr(processes, "prune", lambda: None)
    left, stayed, child = tmp_path / "left", tmp_path / "stayed", tmp_path / "child"
    # Apple's sleep by a program's own setsid(), and a program that is not a platform binary leaving the session with
    # Apple's sleep left in its new one: what the manual says escapes on a Mac, but for that program, which carries the
    # mark ps shows
    leaves = (f"import os, subprocess; os.setsid(); subprocess.Popen(['/bin/sh', '-c', "
              f"'sleep 300 >/dev/null 2>&1 & echo \\$! > {child}']); import time; time.sleep(300)")
    shell = processes.start(["/bin/sh", "-c", "perl -MPOSIX -e 'setsid(); exec @ARGV' /bin/sleep 300 "
                             f"& echo $! > {left}; "
                             f"sleep 300 >/dev/null 2>&1 & echo $! > {stayed}; "
                             f"'{sys.executable}' -c \"{leaves}\" >/dev/null 2>&1 &"],
                            run_id, env=dict(os.environ, **{processes.MARK: run_id}))
    shell.wait(timeout=10)
    gone, kept, little = _pid_in(left), _pid_in(stayed), _pid_in(child)
    try:
        deadline = time.time() + 10
        while os.getsid(gone) != gone:                  # its own setsid() done
            assert time.time() < deadline
            time.sleep(0.05)
        leader = os.getsid(little)
        assert leader in processes.marked(run_id)         # the leaver, not a platform binary: its mark shows
        processes.end(run_id, wait=5)
        assert _until_gone(kept) and _until_gone(leader)
        assert _running(gone) and _running(little)        # Apple's sleep outside the run's session: not reached
    finally:
        _kill(gone, kept, little)


@on_a_mac
def test_on_a_mac_no_run_owns_the_agent_s_own_session_or_one_whose_id_was_given_again(monkeypatch):
    run_id = _run_id()
    assert not processes.own(run_id, os.getsid(0)) and not processes.own(run_id, 1) and not processes.own(run_id, 0)
    # nor its parent's: as the local agent is started, in a session of its own, so that its parent's is another; here
    # the parent leads that session and still runs, so that only the test of the parent's session refuses it
    agent = ("import os\nfrom proofpane_workspace import processes\n"
             f"print(processes.own({run_id!r}, os.getsid(os.getppid())), os.getsid(0) != os.getsid(os.getppid()))")
    child = subprocess.run([sys.executable, "-c", "import subprocess, sys\n"
                            f"subprocess.run([sys.executable, '-c', {agent!r}], start_new_session=True)"],
                           start_new_session=True, capture_output=True, text=True, timeout=30,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert child.stdout.split() == ["False", "True"], child.stderr
    gone = subprocess.Popen(["/bin/sleep", "0"], start_new_session=True)
    gone.wait()
    assert not processes.own(run_id, gone.pid)                          # nor one whose leader has gone already
    leader = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)
    try:
        assert processes.own(run_id, leader.pid) and processes.in_sessions(run_id) == [leader.pid]
        # the id kept with another start than its leader's now: another process given that id later, not the run's,
        # and forgotten
        processes._SESSIONS[run_id] = {leader.pid: "Mon Jan  1 00:00:00 2001"}
        assert processes.in_sessions(run_id) == [] and processes.end(run_id, wait=1) == 0
        assert leader.poll() is None and run_id not in processes._SESSIONS
        assert processes.own(run_id, leader.pid) and processes.end(run_id, wait=5) == 1
        assert leader.wait(timeout=10) is not None
    finally:
        leader.kill()
        leader.wait()


@on_a_mac
def test_on_a_mac_a_session_two_looks_a_second_apart_see_empty_is_forgotten_never_to_be_the_run_s_again(tmp_path):
    assert processes.EMPTY_AGAIN == 1                                   # the manual's "a second or more"
    run_id = _run_id()
    shell = processes.start(["/bin/sh", "-c", "true"], run_id)
    shell.wait(timeout=10)
    sid = shell.pid
    assert sid in processes._SESSIONS[run_id]
    assert processes.in_sessions(run_id) == [] and processes.in_sessions(run_id) == []
    assert sid in processes._SESSIONS[run_id]                           # nothing in it, but seen so within a second
    time.sleep(processes.EMPTY_AGAIN)
    assert processes.in_sessions(run_id) == []                          # and a second on: forgotten
    assert run_id not in processes._SESSIONS and (run_id, sid) not in processes._EMPTY
    # every run's are looked at so, every PRUNE_EVERY seconds while any is kept, by a thread of their own
    assert processes._pruner and processes._pruner[0].is_alive()
    other_id = _run_id()
    other = processes.start(["/bin/sh", "-c", "true"], other_id)
    other.wait(timeout=10)
    processes.prune()
    assert other_id in processes._SESSIONS
    time.sleep(processes.EMPTY_AGAIN)
    processes.prune()
    assert other_id not in processes._SESSIONS


@on_a_mac
def test_on_a_mac_a_process_born_after_a_look_s_ps_listed_its_session_keeps_it_the_run_s(tmp_path, monkeypatch,
                                                                                           own_home):
    run_id = _run_id()
    pidfile = tmp_path / "pid"
    # a precheck's last line, `(sleep &)`: its shell gone, and its sleep born just after a look's ps listed the
    # session's processes, so that the look sees none in it
    shell = processes.start(["/bin/sh", "-c", f"(sleep 300 >/dev/null 2>&1 & echo $! > {pidfile})"], run_id)
    shell.wait(timeout=10)
    left = _pid_in(pidfile)
    real = subprocess.run
    homed = _homed(real, own_home)                                      # what the wrapper below passes on

    def listed_before_it(argv, *a, **k):
        p = homed(argv, *a, **k)
        if argv[1:3] == ["-ax", "-o"]:
            p.stdout = "".join(line + "\n" for line in p.stdout.splitlines() if line.split()[:1] != [str(left)])
        return p
    try:
        monkeypatch.setattr(processes.subprocess, "run", listed_before_it)
        assert processes.in_sessions(run_id) == []
        monkeypatch.setattr(processes.subprocess, "run", real)
        time.sleep(processes.EMPTY_AGAIN)
        assert processes.in_sessions(run_id) == [left]                  # the next look sees it: the session kept
        monkeypatch.setattr(processes.subprocess, "run", listed_before_it)
        assert processes.in_sessions(run_id) == [] and run_id in processes._SESSIONS   # seen empty anew: kept yet
        monkeypatch.setattr(processes.subprocess, "run", real)
        assert processes.end(run_id, wait=5) == 1 and _until_gone(left)
    finally:
        monkeypatch.setattr(processes.subprocess, "run", real)
        _kill(left)


@on_a_mac
def test_on_a_mac_each_of_a_run_s_sessions_is_its_own_whatever_becomes_of_another(tmp_path):
    run_id = _run_id()
    done = processes.start(["/bin/sh", "-c", "true"], run_id)           # its precheck's, say, over
    done.wait(timeout=10)
    left = processes.start(["/bin/sleep", "300"], run_id)               # and its claude's, say, still running
    try:
        assert list(processes._SESSIONS[run_id]) == [done.pid, left.pid]
        processes.in_sessions(run_id)
        time.sleep(processes.EMPTY_AGAIN)
        assert processes.in_sessions(run_id) == [left.pid]              # the first forgotten, the second looked in
        assert list(processes._SESSIONS[run_id]) == [left.pid]
        assert processes.end(run_id, wait=5) == 1 and left.wait(timeout=10) is not None
    finally:
        left.kill()
        left.wait()


@on_a_mac
def test_on_a_mac_the_kept_sessions_are_looked_at_every_30_seconds_by_a_thread_that_goes_on(monkeypatch):
    # the bound the manual gives an id given again rests on the number and on a loop that looks again and again,
    # past a look that failed
    assert processes.PRUNE_EVERY == 30
    mine, looks, real = [], [], processes.prune

    def prune():
        if threading.current_thread() is not mine[0]:
            return real()                                   # the agent's own thread, if it runs, looks as it does
        looks.append(time.monotonic())
        if len(looks) == 2:
            raise ValueError("a look that failed")
        if len(looks) == 4:
            raise SystemExit                                # the end of this test's own thread
    monkeypatch.setattr(processes, "prune", prune)
    monkeypatch.setattr(processes, "PRUNE_EVERY", 0.05)
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)    # Python's own: SystemExit ends it quietly
    mine.append(threading.Thread(target=processes._prune_always, daemon=True))
    mine[0].start()
    mine[0].join(10)
    assert not mine[0].is_alive() and len(looks) == 4
    assert all(b - a >= 0.05 for a, b in zip(looks, looks[1:]))
    # the agent's own: one thread, however many sessions are kept
    run_id = _run_id()
    kept = [processes.start(["/bin/sleep", "300"], run_id) for _ in range(2)]
    try:
        assert len(processes._SESSIONS[run_id]) == 2
        assert sum(t.name == "processes-prune" and t.is_alive() for t in threading.enumerate()) == 1
    finally:
        for p in kept:
            p.kill()
            p.wait()


@on_a_mac
def test_on_a_mac_nothing_is_taken_on_the_word_of_a_ps_that_does_not_answer(monkeypatch, own_home):
    run_id = _run_id()
    leader = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)
    real = subprocess.run
    homed = _homed(real, own_home)                                       # what the wrappers below pass on
    try:
        assert processes.own(run_id, leader.pid)

        def no_ps(argv, *a, **k):
            if argv[0] == "/bin/ps":
                raise subprocess.TimeoutExpired(argv, 20)
            return homed(argv, *a, **k)
        monkeypatch.setattr(processes.subprocess, "run", no_ps)
        assert processes.in_sessions(run_id) == [] and run_id in processes._SESSIONS    # nothing taken, nothing lost
        assert not processes.own(run_id, leader.pid)                     # nor kept anew

        def no_start(argv, *a, **k):                                     # the list answers, a start does not
            if argv[:2] == ["/bin/ps", "-o"]:
                raise subprocess.TimeoutExpired(argv, 20)
            return homed(argv, *a, **k)
        monkeypatch.setattr(processes.subprocess, "run", no_start)
        assert processes.in_sessions(run_id) == [] and run_id in processes._SESSIONS
        monkeypatch.setattr(processes.subprocess, "run", real)
        assert processes.in_sessions(run_id) == [leader.pid]
    finally:
        monkeypatch.setattr(processes.subprocess, "run", real)
        processes._SESSIONS.pop(run_id, None)
        leader.kill()
        leader.wait()


@on_a_mac
def test_on_a_mac_the_session_the_wrapper_says_the_run_s_claude_leads_is_the_run_s(tmp_path):
    run_id = _run_id()
    told, pidfile = tmp_path / "claude-process", tmp_path / "pid"
    # as the wrapper makes a run's claude: a program that is not a platform binary leading a session of its own, with
    # the run's mark, its id written where the agent reads it; what it leaves with Apple's sleep is in its session, its
    # mark unseen
    leader = subprocess.Popen([sys.executable, "-c", "import os, subprocess, sys; "
                               f"open({str(told)!r}, 'w').write(str(os.getpid())); "
                               "subprocess.run(['/bin/sh', '-c', "
                               f"'(sleep 300 >/dev/null 2>&1 & echo $! > {pidfile})']); print('ready', flush=True); "
                               "sys.stdin.read()"], env=dict(os.environ, **{processes.MARK: run_id}),
                              start_new_session=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    unmarked = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)       # leads one, without the mark
    follower = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                env=dict(os.environ, **{processes.MARK: run_id}))     # carries it, leads none
    left = None
    try:
        assert leader.stdout.readline() == b"ready\n"
        left = _pid_in(pidfile)
        for wrong in (unmarked.pid, follower.pid, "not a process"):
            (tmp_path / "wrong").write_text(str(wrong))
            assert processes.reported(run_id, str(tmp_path / "wrong")) is None
        assert processes.reported(run_id, str(tmp_path / "none")) is None
        assert processes.reported(run_id, str(told)) == leader.pid
        leader.stdin.close()
        leader.wait(timeout=10)                                 # it has gone: what it left is still the run's
        assert processes.end(run_id, wait=5) == 2               # the follower by its mark, its sleep by the session
        assert _until_gone(left) and follower.wait(timeout=10) is not None
        assert unmarked.poll() is None
    finally:
        for p in (leader, follower, unmarked):
            p.kill()
            p.wait()
        _kill(left)


def test_the_wrapper_has_a_run_s_claude_lead_a_session_of_its_own_and_say_which_on_the_agent_s_word_alone(tmp_path):
    from proofpane_workspace import chat
    claude = tmp_path / "claude"
    claude.write_text(f"#!{sys.executable}\nimport os\n"
                      "print(os.getsid(0) == os.getpid(), os.getpid(), 'PP_AGENT_SESSION' in os.environ)\n")
    claude.chmod(0o755)
    wrapper = tmp_path / "wrapper"
    chat.write_wrapper(str(wrapper), [], claude=str(claude))
    told = tmp_path / "claude-process"
    for asked in ({"PP_AGENT_SESSION": str(told)}, {}, {"PP_AGENT_SESSION": ""}):
        if told.exists():
            told.unlink()
        out = subprocess.run([str(wrapper)], env=dict(os.environ, **asked), capture_output=True, text=True,
                             timeout=30).stdout.split()
        leads = bool(asked.get("PP_AGENT_SESSION"))
        assert out[0] == str(leads) and out[2] == "False", (asked, out)   # and the name is not passed on to claude
        assert (told.read_text() == out[1]) if leads else not told.exists()   # the claude's own id, as it runs
    # a link put where it writes: the file made new there, nothing written where the link points
    target = tmp_path / "elsewhere"
    target.write_text("kept")
    told.unlink(missing_ok=True)
    told.symlink_to(target)
    out = subprocess.run([str(wrapper)], env=dict(os.environ, PP_AGENT_SESSION=str(told)), capture_output=True,
                         text=True, timeout=30).stdout.split()
    assert not told.is_symlink() and told.read_text() == out[1] and target.read_text() == "kept"
    assert told.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform == "darwin", reason="elsewhere than a Mac's: there the run owns its sessions")
def test_elsewhere_a_run_s_program_starts_in_the_agent_s_own_session_and_owns_none(tmp_path):
    run_id = _run_id()
    p = processes.start(["sleep", "60"], run_id)
    try:
        assert os.getsid(p.pid) == os.getsid(0)
        (tmp_path / "told").write_text(str(p.pid))
        assert not processes.own(run_id, p.pid) and processes.reported(run_id, str(tmp_path / "told")) is None
        assert processes.in_sessions(run_id) == []
    finally:
        p.kill()
        p.wait()


@on_a_mac
def test_on_a_mac_a_program_started_for_a_run_without_its_session_is_started_as_before(tmp_path):
    run_id = _run_id()
    p = processes.start(["/bin/sleep", "60"], run_id, session=False)     # a task's attempt's precheck
    try:
        assert os.getsid(p.pid) == os.getsid(0) and run_id not in processes._SESSIONS
    finally:
        p.kill()
        p.wait()


@on_a_mac
def test_on_a_mac_what_ignores_the_polite_signal_in_a_run_s_session_is_made_to_stop(tmp_path):
    run_id = _run_id()
    pidfile = tmp_path / "pid"
    shell = processes.start(["/bin/sh", "-c",
                             f"(trap '' TERM; exec /bin/sleep 300) >/dev/null 2>&1 & echo $! > {pidfile}"],
                            run_id)
    shell.wait(timeout=10)
    stubborn = _pid_in(pidfile)
    try:
        time.sleep(0.3)                                     # its trap set before exec: SIGTERM ignored across it
        assert processes.end(run_id, wait=0.5) == 1
        assert _until_gone(stubborn)                        # the kill after the wait, read again in the session
    finally:
        _kill(stubborn)


@on_a_mac
def test_on_a_mac_a_program_started_for_a_run_is_ended_with_its_group_when_keeping_its_session_raises(tmp_path,
                                                                                                     monkeypatch):
    run_id = _run_id()
    pidfile = tmp_path / "pid"
    started = []

    def cannot_keep(rid, sid):
        started.append(_pid_in(pidfile))                    # what it started, there by now
        raise RuntimeError("cannot keep it")
    monkeypatch.setattr(processes, "own", cannot_keep)
    try:
        with pytest.raises(RuntimeError):
            processes.start(["/bin/sh", "-c", f"sleep 300 >/dev/null 2>&1 & echo $! > {pidfile}; wait"], run_id,
                            stdout=subprocess.PIPE)
        assert started and _until_gone(started[0])         # its group ended, and the shell waited for
        assert run_id not in processes._SESSIONS
    finally:
        _kill(*started)


@on_a_mac
def test_on_a_mac_ps_is_the_system_s_whatever_this_agent_s_path_time_zone_or_language(tmp_path, monkeypatch):
    import calendar
    leader = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)
    try:
        said = processes._started(leader.pid)
        # in UTC, whatever this machine's zone: what was said of a process started just now is now's UTC time
        assert abs(calendar.timegm(time.strptime(" ".join(said.split()), "%a %b %d %H:%M:%S %Y")) - time.time()) < 60
        fake = tmp_path / "ps"
        fake.write_text("#!/bin/sh\necho 'Mon Jan  1 00:00:00 2001'\n")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
        monkeypatch.setenv("TZ", "America/New_York")
        monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
        assert processes._started(leader.pid) == said
        # and so the mark and the sessions are read, the walk from them among them
        run_id, child = _run_id(), tmp_path / "child"
        claude = processes.start([sys.executable, "-c", "import subprocess, time\n"
                                  "p = subprocess.Popen(['/bin/sleep', '300'], start_new_session=True)\n"
                                  f"open({str(child)!r}, 'w').write(str(p.pid))\ntime.sleep(300)"],
                                 run_id, env=dict(os.environ, **{processes.MARK: run_id}))
        call = None
        try:
            call = _pid_in(child)
            assert claude.pid in processes.marked(run_id)
            assert processes.in_sessions(run_id) == sorted([claude.pid, call])
            import locale
            was = locale.setlocale(locale.LC_TIME)
            try:
                locale.setlocale(locale.LC_TIME, "fr_FR.UTF-8")        # this agent's own names, not ps's
            except locale.Error:
                pytest.skip("no fr_FR.UTF-8 locale on this machine")
            try:
                processes._SESSIONS[run_id].pop(call)                    # taken again by this look's walk
                assert processes.in_sessions(run_id) == sorted([claude.pid, call])
                assert call in processes._SESSIONS[run_id]
            finally:
                locale.setlocale(locale.LC_TIME, was)
        finally:
            claude.kill()
            claude.wait()
            _kill(call)
    finally:
        leader.kill()
        leader.wait()


@on_a_mac
def test_on_a_mac_what_a_run_s_process_starts_in_a_session_of_its_own_is_the_run_s_while_it_runs(tmp_path):
    run_id = _run_id()
    call, left = tmp_path / "call", tmp_path / "left"
    # as Claude Code runs a Bash call: in a session of its own, a child of the claude, which leads the run's; what the
    # call leaves in its session with Apple's sleep is there once the call's shell has gone too
    claude = processes.start([sys.executable, "-c", "import subprocess, time\n"
                              "subprocess.Popen(['/bin/sh', '-c', "
                              f"'sleep 300 >/dev/null 2>&1 & echo $! > {left}; echo $$ > {call}; exec sleep 300'], "
                              "start_new_session=True)\ntime.sleep(300)"], run_id)
    shell = gone = None
    try:
        shell, gone = _pid_in(call), _pid_in(left)
        parent = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(shell)], capture_output=True, text=True).stdout
        assert os.getsid(shell) == shell and int(parent) == claude.pid    # its own session, the claude its parent
        assert not set(processes.marked(run_id)) & {shell, gone}         # ps -E shows neither the mark
        assert processes.in_sessions(run_id) == sorted([claude.pid, shell, gone])   # a look while the call runs
        assert shell in processes._SESSIONS[run_id]                       # its session the run's from then on
        os.kill(shell, 9)                                                 # the call over, its sleep left behind
        assert processes.end(run_id, wait=5) == 2                         # the claude, and the sleep the call left
        assert _until_gone(gone) and claude.wait(timeout=10) is not None
    finally:
        claude.kill()
        claude.wait()
        _kill(shell, gone)



@on_a_mac
def test_on_a_mac_a_program_started_for_a_run_runs_on_in_a_session_the_run_does_not_own_when_ps_does_not_answer(
        monkeypatch):
    # as the manual says: then its session is never the run's, and what it leaves is found by its mark alone
    run_id = _run_id()
    monkeypatch.setattr(processes, "_started", lambda pid: processes.UNKNOWN)
    p = processes.start(["/bin/sleep", "300"], run_id)
    try:
        time.sleep(0.3)
        assert p.poll() is None and os.getsid(p.pid) == p.pid and run_id not in processes._SESSIONS
    finally:
        p.kill()
        p.wait()


@on_a_mac
def test_on_a_mac_the_wrapper_s_word_is_read_as_one_the_run_may_have_put_there(tmp_path):
    run_id = _run_id()
    told, real = tmp_path / "claude-process", tmp_path / "real"
    leader = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE,
                              env=dict(os.environ, **{processes.MARK: run_id}), start_new_session=True)
    try:
        real.write_text(str(leader.pid))
        told.symlink_to(real)                                   # a link, even to a file that would do: not read
        assert processes.reported(run_id, str(told)) is None
        told.unlink()
        os.mkfifo(told)                                         # a FIFO no one writes: the read is not waited on
        reading = threading.Thread(target=processes.reported, args=(run_id, str(told)), daemon=True)
        reading.start()
        reading.join(5)
        assert not reading.is_alive()
        told.unlink()
        told.mkdir()                                            # not a plain file
        assert processes.reported(run_id, str(told)) is None
        told.rmdir()
        for wrong in ("9" * 20, "9" * 11, "9" * 10, "-" + str(leader.pid), str(leader.pid) + "x"):
            told.write_text(wrong)                              # nothing that is not a process's id, and no error
            assert processes.reported(run_id, str(told)) is None, wrong
        told.write_text(str(leader.pid) + " " * 40 + "x")       # past the first 32 bytes nothing is read
        assert processes.reported(run_id, str(told)) == leader.pid
    finally:
        leader.kill()
        leader.wait()


@on_a_mac
def test_on_a_mac_a_look_walks_from_the_run_s_sessions_never_from_a_word_read_as_its_mark(tmp_path):
    run_id = _run_id()
    child = tmp_path / "child"
    # ps -E prints a command line and an environment alike, so a process whose command line holds the run's mark is
    # found by it; what that process started is not walked to from it
    kept = processes.start(["/bin/sleep", "300"], run_id)               # a session of the run's: a look walks
    worded = subprocess.Popen(["/bin/sh", "-c", f"sleep 300 >/dev/null 2>&1 & echo $! > {child}; wait", "sh",
                               f"{processes.MARK}={run_id}"], start_new_session=True)
    left = None
    try:
        left = _pid_in(child)
        assert worded.pid in processes.marked(run_id) and left not in processes.in_sessions(run_id)
        processes.end(run_id, wait=5)
        assert _running(left)
    finally:
        _kill(left)
        for p in (kept, worded):
            p.kill()
            p.wait()


def _homed(real, home):
    """`real`, subprocess.run, as a test's own wrapper of the look's ps passes the look's call on, given `home`, a home
    of the test's own (own_home). The look starts /bin/ps with PS_ENV, which holds no HOME; called from the look, that
    start is the look's, but called from a wrapper in this file it is the test's, and the guard of tests/conftest.py
    stops a test's start of a program whose environment holds no HOME (it would take the password database's home as
    its ~). So the wrapper gives the call the test's own home, and the look's PS_ENV and the guard stay as they are.
    For the `-ax` listing and the `-o lstart=` start the look makes in these cases, ps reads nothing from a home, so
    its answer is the same with a home as without one: against a run with no home the rows differ no more than two
    runs with no home differ, a change of TZ the one control that does differ. A home reaches only ps's own line under
    its `-E` listing, which no case here reads."""
    def run(argv, *a, **k):
        env = k.get("env")
        if env is not None and "HOME" not in env:
            k = dict(k, env=dict(env, HOME=home))
        return real(argv, *a, **k)
    return run


def _parent_shown(monkeypatch, parents, traced=(), *, home):
    """The look's ps made to show each pid of `parents` as the child of the one given for it, as ps may show a
    debugger as the parent of the process it is attached to, and each of `traced` as being traced; `home`, the test's
    own, given to the call it passes on (_homed)."""
    real = _homed(subprocess.run, home)

    def shown(argv, *a, **k):
        p = real(argv, *a, **k)
        if argv[1:4] == ["-ax", "-o", "pid=,ppid=,stat=,lstart="]:
            lines = []
            for line in p.stdout.splitlines():
                words = line.split()
                if words and words[0].isdigit() and int(words[0]) in parents:
                    words[1] = str(parents[int(words[0])])
                if words and words[0].isdigit() and int(words[0]) in traced:
                    words[2] = words[2][:1] + "X" + words[2][1:]       # within the column, as in "SXs"
                lines.append(" ".join(words) + "\n")
            p.stdout = "".join(lines)
        return p
    monkeypatch.setattr(processes.subprocess, "run", shown)


@on_a_mac
def test_on_a_mac_a_look_takes_no_child_that_began_before_its_parent(monkeypatch, own_home):
    # a start as ps gives it, to the second: the same second is no earlier, and what cannot be read is no start
    then, later = "Sun Oct 18 12:10:20 2026", "Sun Oct 18 12:10:21 2026"
    assert processes._not_before(then, then) and processes._not_before(later, then)
    assert not processes._not_before(then, later) and not processes._not_before(None, then)
    assert not processes._not_before("dim. 18 oct. 12:10:20 2026", then)
    run_id = _run_id()
    older = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)      # an app outside the run, say
    time.sleep(1.1)                                         # ps gives a start to the second
    member = processes.start(["/bin/sleep", "300"], run_id)
    try:
        _parent_shown(monkeypatch, {older.pid: member.pid}, home=own_home)
        assert processes.in_sessions(run_id) == [member.pid] and older.pid not in processes._SESSIONS[run_id]
    finally:
        for p in (older, member):
            p.kill()
            p.wait()


@on_a_mac
def test_on_a_mac_a_look_takes_the_session_of_a_child_only_when_the_child_leads_it(tmp_path, monkeypatch, own_home):
    run_id = _run_id()
    child = tmp_path / "child"
    member = processes.start(["/bin/sleep", "300"], run_id)
    time.sleep(1.1)
    # a process in another's session, which it does not lead, shown as the member's child: that session not taken
    other = subprocess.Popen(["/bin/sh", "-c", f"sleep 300 >/dev/null 2>&1 & echo $! > {child}; wait"],
                             start_new_session=True)
    inside = None
    try:
        inside = _pid_in(child)
        _parent_shown(monkeypatch, {inside: member.pid}, home=own_home)
        processes.in_sessions(run_id)
        assert set(processes._SESSIONS[run_id]) == {member.pid}
    finally:
        _kill(inside)
        for p in (member, other):
            p.kill()
            p.wait()



@on_a_mac
def test_on_a_mac_a_look_takes_no_child_being_traced_whenever_it_began(monkeypatch, own_home):
    run_id = _run_id()
    member = processes.start(["/bin/sleep", "300"], run_id)
    time.sleep(1.1)
    younger = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)     # begun after its "parent"
    try:
        _parent_shown(monkeypatch, {younger.pid: member.pid}, traced={younger.pid}, home=own_home)
        assert processes.in_sessions(run_id) == [member.pid] and younger.pid not in processes._SESSIONS[run_id]
    finally:
        for p in (younger, member):
            p.kill()
            p.wait()


@on_a_mac
def test_on_a_mac_a_look_holds_each_child_to_its_own_parent_s_start(monkeypatch, own_home):
    run_id = _run_id()
    member = processes.start(["/bin/sleep", "300"], run_id)
    time.sleep(1.1)
    outsider = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)    # after the run began
    time.sleep(1.1)
    debugger = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)    # the run's, and after it
    try:
        # the debugger shown as the member's child, the outsider as the debugger's: older than its own parent
        _parent_shown(monkeypatch, {debugger.pid: member.pid, outsider.pid: debugger.pid}, home=own_home)
        assert processes.in_sessions(run_id) == sorted([member.pid, debugger.pid])
        assert outsider.pid not in processes._SESSIONS[run_id]
    finally:
        for p in (outsider, debugger, member):
            p.kill()
            p.wait()


@on_a_mac
def test_on_a_mac_a_look_walks_from_every_process_in_the_run_s_sessions_not_its_leaders_alone(tmp_path):
    run_id = _run_id()
    child = tmp_path / "child"
    # the precheck's shell leads its session; what it ran in the background, in the same session, starts a process of
    # a session of its own
    walker = (f"import subprocess, time; p = subprocess.Popen(['/bin/sleep', '300'], start_new_session=True); "
              f"open({str(child)!r}, 'w').write(str(p.pid)); time.sleep(300)")
    shell = processes.start(["/bin/sh", "-c", f"'{sys.executable}' -c \"{walker}\" >/dev/null 2>&1 & wait"], run_id)
    away = None
    try:
        away = _pid_in(child)
        assert away in processes.in_sessions(run_id) and away in processes._SESSIONS[run_id]
    finally:
        shell.kill()
        shell.wait()
        processes.end(run_id, wait=1)
        _kill(away)


@on_a_mac
def test_on_a_mac_a_run_s_end_ends_what_is_in_a_session_its_own_look_takes(tmp_path, monkeypatch):
    run_id = _run_id()
    monkeypatch.setattr(processes, "prune", lambda: None)              # no look but the end's own
    left = tmp_path / "left"
    # the run's process starts one leading a session of its own, which leaves Apple's sleep there and runs on; no
    # look has been since, so the end's own look is the first to take that session
    leader = (f"import subprocess, time; subprocess.run(['/bin/sh', '-c', "
              f"'(/bin/sleep 300 >/dev/null 2>&1 & echo $! > {left})']); time.sleep(300)")
    precheck = processes.start([sys.executable, "-c", "import subprocess, sys, time\n"
                                f"subprocess.Popen([sys.executable, '-c', {leader!r}], start_new_session=True)\n"
                                "time.sleep(300)"], run_id)
    gone = None
    try:
        gone = _pid_in(left)
        assert os.getsid(gone) != os.getsid(precheck.pid)                  # in the session of its own, not the run's
        assert processes.end(run_id, wait=1) == 3                           # the precheck, the leader, its sleep
        assert _until_gone(gone)
    finally:
        precheck.kill()
        precheck.wait()
        processes.end(run_id, wait=1)
        _kill(gone)


@on_a_mac
def test_on_a_mac_a_look_walks_to_what_a_process_it_took_started_in_turn(tmp_path):
    run_id = _run_id()
    deeper = tmp_path / "deeper"
    # the run's process starts one leading a session of its own, which starts one leading another: the second is
    # in neither the run's sessions nor the first's, and is found only by walking on from the first
    inner = (f"import subprocess, time; p = subprocess.Popen(['/bin/sleep', '300'], start_new_session=True); "
             f"open({str(deeper)!r}, 'w').write(str(p.pid)); time.sleep(300)")
    claude = processes.start([sys.executable, "-c", "import subprocess, sys, time\n"
                              f"subprocess.Popen([sys.executable, '-c', {inner!r}], start_new_session=True)\n"
                              "time.sleep(300)"], run_id)
    last = None
    try:
        last = _pid_in(deeper)
        assert os.getsid(last) == last
        assert last in processes.in_sessions(run_id) and last in processes._SESSIONS[run_id]
    finally:
        claude.kill()
        claude.wait()
        processes.end(run_id, wait=1)
        _kill(last)



@on_a_mac
def test_on_a_mac_a_look_that_first_sees_a_session_empty_has_the_next_look_come_a_second_on(monkeypatch):
    mine, looks = [], []

    def prune():
        if threading.current_thread() is not mine[0]:
            return None                                     # the agent's own thread, if one runs: no look meanwhile
        looks.append(time.monotonic())
        raise SystemExit                                    # the end of this test's own thread
    monkeypatch.setattr(processes, "prune", prune)
    monkeypatch.setattr(processes, "_wake", threading.Event())     # set by nothing before this test's own look
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    mine.append(threading.Thread(target=processes._prune_always, daemon=True))
    mine[0].start()
    time.sleep(0.2)                                         # waiting, its next look 30 seconds off
    run_id = _run_id()
    shell = processes.start(["/bin/sh", "-c", "true"], run_id)
    shell.wait(timeout=10)
    seen = time.monotonic()
    assert processes.in_sessions(run_id) == []              # the first look to see it empty
    mine[0].join(10)
    assert looks and processes.EMPTY_AGAIN <= looks[0] - seen < 5


@on_a_mac
def test_on_a_mac_two_looks_are_held_a_second_apart_by_when_their_ps_listed(tmp_path, monkeypatch, own_home):
    run_id = _run_id()
    pidfile = tmp_path / "pid"
    monkeypatch.setattr(processes, "prune", lambda: None)              # no look but these
    slow = processes.start(["/bin/sleep", "300"], run_id)               # kept first, so its start is read first
    shell = processes.start(["/bin/sh", "-c", f"(sleep 300 >/dev/null 2>&1 & echo $! > {pidfile})"], run_id)
    shell.wait(timeout=10)
    left = _pid_in(pidfile)
    real_run, real_started = subprocess.run, processes._started
    homed = _homed(real_run, own_home)                                  # what the wrapper below passes on

    def listed_before_it(argv, *a, **k):
        p = homed(argv, *a, **k)
        if argv[1:3] == ["-ax", "-o"]:
            p.stdout = "".join(line + "\n" for line in p.stdout.splitlines() if line.split()[:1] != [str(left)])
        return p

    def slow_start(pid):
        if pid == slow.pid:
            time.sleep(processes.EMPTY_AGAIN + 0.2)
        return real_started(pid)
    try:
        monkeypatch.setattr(processes.subprocess, "run", listed_before_it)
        processes.in_sessions(run_id)                       # the first to see the shell's session empty
        monkeypatch.setattr(processes, "_started", slow_start)
        processes.in_sessions(run_id)                       # listed at once after, but reaching it a second on
        assert shell.pid in processes._SESSIONS[run_id]     # two listings within a second: kept
    finally:
        monkeypatch.setattr(processes.subprocess, "run", real_run)
        monkeypatch.setattr(processes, "_started", real_started)
        processes.end(run_id, wait=1)
        _kill(left)
        slow.kill()
        slow.wait()


@on_a_mac
def test_on_a_mac_a_look_takes_no_child_leading_no_session_that_is_traced_or_older_than_its_parent(tmp_path,
                                                                                                monkeypatch,
                                                                                                own_home):
    run_id = _run_id()
    older_file, younger_file = tmp_path / "older", tmp_path / "younger"
    # processes inside sessions of others', which they do not lead, shown as the member's children
    first = subprocess.Popen(["/bin/sh", "-c", f"sleep 300 >/dev/null 2>&1 & echo $! > {older_file}; wait"],
                             start_new_session=True)
    time.sleep(1.1)
    member = processes.start(["/bin/sleep", "300"], run_id)
    time.sleep(1.1)
    second = subprocess.Popen(["/bin/sh", "-c", f"sleep 300 >/dev/null 2>&1 & echo $! > {younger_file}; wait"],
                              start_new_session=True)
    older = younger = None
    try:
        older, younger = _pid_in(older_file), _pid_in(younger_file)
        _parent_shown(monkeypatch, {older: member.pid, younger: member.pid}, traced={younger}, home=own_home)
        assert processes.in_sessions(run_id) == [member.pid]
    finally:
        _kill(older, younger)
        for p in (first, second, member):
            p.kill()
            p.wait()


@on_a_mac
def test_on_a_mac_a_look_walks_on_from_what_is_in_a_session_it_takes(tmp_path, monkeypatch):
    run_id = _run_id()
    deeper = tmp_path / "deeper"
    monkeypatch.setattr(processes, "prune", lambda: None)              # this look alone
    # the claude's call leads a session of its own; what the call runs in the background, in that session, starts a
    # process leading another: found in the same look as the call's session is taken
    grand = (f"import subprocess, time; p = subprocess.Popen(['/bin/sleep', '300'], start_new_session=True); "
             f"open({str(deeper)!r}, 'w').write(str(p.pid)); time.sleep(300)")
    call = f"'{sys.executable}' -c \"{grand}\" & wait"
    claude = processes.start([sys.executable, "-c", "import subprocess, time\n"
                              f"subprocess.Popen(['/bin/sh', '-c', {call!r}], start_new_session=True)\n"
                              "time.sleep(300)"], run_id)
    last = None
    try:
        last = _pid_in(deeper)
        assert last in processes.in_sessions(run_id)
    finally:
        claude.kill()
        claude.wait()
        processes.end(run_id, wait=1)
        _kill(last)


@on_a_mac
def test_on_a_mac_a_look_walks_on_from_no_child_it_refuses(tmp_path, monkeypatch, own_home):
    run_id = _run_id()
    kids = tmp_path / "kids"

    def outsider():
        # a process outside the run, with a child of its own
        return subprocess.Popen(["/bin/sh", "-c", f"sleep 300 >/dev/null 2>&1 & echo $! >> {kids}; wait"],
                                start_new_session=True)
    older = outsider()
    time.sleep(1.1)
    member = processes.start(["/bin/sleep", "300"], run_id)
    time.sleep(1.1)
    traced = outsider()
    left = []
    try:
        deadline = time.time() + 10
        while not (kids.exists() and len(kids.read_text().split()) == 2):
            assert time.time() < deadline
            time.sleep(0.05)
        left = [int(w) for w in kids.read_text().split()]
        # both shown as the member's children, one traced, one begun before it: neither taken, nor what it started
        _parent_shown(monkeypatch, {older.pid: member.pid, traced.pid: member.pid}, traced={traced.pid}, home=own_home)
        assert processes.in_sessions(run_id) == [member.pid]
    finally:
        _kill(*left)
        for p in (older, traced, member):
            p.kill()
            p.wait()
