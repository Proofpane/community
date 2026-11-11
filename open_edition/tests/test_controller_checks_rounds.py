"""A round's clone, its judgement of the commits and its commands (proofpane_workspace/controller_checks.py),
against repositories the tests make: no command but shell builtins,
git and sleep runs, and nothing runs a real claude."""

import os
import subprocess
import sys
import time
import zlib

import pytest

from proofpane_workspace import controller_checks as cc
from proofpane_workspace import processes
from proofpane_workspace.workspace import git
from workspace_fixtures import make_repo

SID = "0123456789ab"


def _commit(path, files, message="work"):
    for name, text in files.items():
        full = os.path.join(path, name)
        os.makedirs(os.path.dirname(full) or path, exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)
    git("add", "-A", cwd=path)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message, cwd=path)
    return git("rev-parse", "HEAD", cwd=path)[1].strip()


@pytest.fixture
def repo(tmp_path):
    """A repository with a protected test file, and the session's branch pp/<SID> one commit past its base."""
    path = str(tmp_path / "repo")
    make_repo(path)
    base = _commit(path, {"tests/test_a.py": "def test_a():\n    assert True\n", "Makefile": "test:\n\ttrue\n"})
    git("branch", f"pp/{SID}", cwd=path)
    git("checkout", "-q", f"pp/{SID}", cwd=path)
    head = _commit(path, {"src/app.py": "print('hello')\n"})
    git("checkout", "-q", "main", cwd=path)
    return {"path": path, "base": base, "head": head, "round": str(tmp_path / "round")}


def _clone(repo):
    os.makedirs(repo["round"], exist_ok=True)
    return cc.make_clone(repo["path"], SID, repo["head"], repo["round"], 60)


# ---- the clone and the commits judged in it --------------------------------------------------------------------------

def test_a_round_s_clone_holds_the_branch_s_head_and_judges_a_change_outside_the_protected_paths(repo):
    clone = _clone(repo)
    assert git("rev-parse", "HEAD", cwd=clone)[1].strip() != "" and os.path.isdir(os.path.join(clone, ".git"))
    assert cc.judge(clone, repo["base"], repo["head"], ["tests/**", "Makefile"], 2 ** 31) == 0


def test_a_change_to_a_protected_path_is_counted(repo):
    git("checkout", "-q", f"pp/{SID}", cwd=repo["path"])
    repo["head"] = _commit(repo["path"], {"tests/test_a.py": "def test_a():\n    pass\n", "Makefile": "test:\n"})
    clone = _clone(repo)
    assert cc.judge(clone, repo["base"], repo["head"], ["tests/**", "Makefile"], 2 ** 31) == 2


def test_a_branch_moved_since_the_round_was_queued_lets_the_round_go(repo):
    with pytest.raises(cc.Moved):
        os.makedirs(repo["round"])
        cc.make_clone(repo["path"], SID, repo["base"], repo["round"], 60)


def test_a_head_that_does_not_come_after_the_base_runs_nothing(repo):
    git("checkout", "-q", "--orphan", "elsewhere", cwd=repo["path"])
    other = _commit(repo["path"], {"x": "x\n"})
    git("checkout", "-q", "-B", f"pp/{SID}", other, cwd=repo["path"])
    repo["head"] = other
    clone = _clone(repo)
    with pytest.raises(cc.Unrun) as e:
        cc.judge(clone, repo["base"], other, ["tests/**"], 2 ** 31)
    assert e.value.why == cc.NOT_AFTER_BASE


def test_a_head_s_files_past_the_bound_are_not_run(repo):
    clone = _clone(repo)
    with pytest.raises(cc.Unrun) as e:
        cc.judge(clone, repo["base"], repo["head"], [], 10)
    assert e.value.why == cc.PAST_BOUNDS


@pytest.mark.parametrize("changed,patterns,count", [
    (["TESTS/conftest.py"], ["tests/**"], 1),
    (["makefile"], ["Makefile"], 1),
    (["tests"], ["tests/**"], 1),
    (["Tests"], ["tests/"], 1),
    (["docs"], ["docs/conf.py"], 1),
    (["src/a.py"], ["tests/**"], 0),
    (["tests_extra/a.py"], ["tests/**"], 0),
    (["caf\u00e9/x"], ["cafe\u0301/**"], 1),
])
def test_a_name_that_folds_onto_a_protected_path_or_stands_where_its_folder_was_counts(changed, patterns, count):
    assert cc.protected_count(changed, patterns) == count


def test_a_smudge_filter_in_the_repository_s_settings_neither_runs_nor_changes_a_protected_file(repo, tmp_path):
    marker = str(tmp_path / "ran")
    git("config", "filter.evil.smudge", f"sh -c 'touch {marker}; echo replaced'", cwd=repo["path"])
    with open(os.path.join(repo["path"], ".git", "info", "attributes"), "w") as fh:
        fh.write("Makefile filter=evil\n")
    clone = _clone(repo)
    folder = os.path.join(repo["round"], "check-1", "work")
    os.makedirs(os.path.dirname(folder))
    cc.make_checkout(clone, repo["head"], folder, 60)
    with open(os.path.join(folder, "Makefile")) as fh:
        assert fh.read() == "test:\n\ttrue\n"
    assert not os.path.exists(marker)
    # the checkout is a worktree of the round's clone, never of the owner's repository
    listed = git("worktree", "list", "--porcelain", cwd=repo["path"])[1]
    assert folder not in listed
    cc.remove_checkout(folder)
    assert not os.path.exists(os.path.dirname(folder))


def test_an_object_rewritten_in_the_repository_s_store_is_refused_by_the_round_s_clone(repo):
    blob = git("rev-parse", f"{repo['head']}:Makefile", cwd=repo["path"])[1].strip()
    loose = os.path.join(repo["path"], ".git", "objects", blob[:2], blob[2:])
    assert os.path.exists(loose)
    other = b"test:\n\tfalse\n"
    os.chmod(loose, 0o644)
    with open(loose, "wb") as fh:
        fh.write(zlib.compress(b"blob %d\x00" % len(other) + other))
    os.makedirs(repo["round"])
    with pytest.raises(cc.Unrun) as e:
        cc.make_clone(repo["path"], SID, repo["head"], repo["round"], 60)
    assert e.value.why == cc.NO_CHECKOUT


# ---- a command -------------------------------------------------------------------------------------------------------

def test_a_command_s_environment_is_the_named_variables_the_terminal_s_type_and_its_mark_and_nothing_else(monkeypatch):
    from proofpane_workspace import workspace as wsmod
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("CW_C2_NOT_NAMED", "x")                 # in the agent's environment, named by no list
    env = cc.check_env("ctl-check-x-1-1")
    want = {k for k in wsmod.PASS_ENV if k in os.environ and k != "SSH_AUTH_SOCK"} | {"TERM", "COLORTERM", processes.MARK}
    if sys.platform == "darwin":
        want.add("BASH_SILENCE_DEPRECATION_WARNING")
    assert set(env) == want


def test_no_git_of_the_round_s_reads_the_system_s_settings_or_attributes_or_the_user_s(tmp_path):
    env = cc._clone_env(str(tmp_path))
    assert (env["GIT_CONFIG_NOSYSTEM"], env["GIT_ATTR_NOSYSTEM"], env["GIT_CONFIG_GLOBAL"]) == ("1", "1", os.devnull)
    assert "core.attributesFile=/dev/null" in cc._IN_CLONE


def test_a_command_s_environment_has_its_mark_and_no_ssh_agent(monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    env = cc.check_env("ctl-check-x-1-1")
    assert env[processes.MARK] == "ctl-check-x-1-1" and "SSH_AUTH_SOCK" not in env
    assert "PP_AGENT_RUN" not in env and "PP_AGENT_ID" not in env


def _run(command, cwd, seconds=10):
    mark = "ctl-check-test-%d" % time.monotonic_ns()
    p = cc.start_command(command, cwd, mark)
    began = time.monotonic()
    code, tail = cc.read_output(p, seconds)
    took = time.monotonic() - began
    cc.end_command(p, mark)
    cc.reap(p)
    return code, tail, took


def test_a_command_that_passes_fails_or_runs_past_its_time(tmp_path):
    assert _run("exit 0", str(tmp_path))[0] == 0
    assert _run("echo no >&2; exit 3", str(tmp_path))[:2] == (3, b"no\n")
    code, _, took = _run("sleep 30", str(tmp_path), seconds=1)
    assert code is None and took < 10


def test_a_command_s_output_is_kept_to_its_last_bytes(tmp_path):
    code, tail, _ = _run("i=0; while [ $i -lt 2000 ]; do echo line$i; i=$((i+1)); done", str(tmp_path))
    assert code == 0 and len(tail) == 4096 and tail.endswith(b"line1999\n")


def test_a_command_that_leaves_a_program_holding_its_output_is_not_waited_for(tmp_path):
    code, _, took = _run("(sleep 30 &); exit 0", str(tmp_path))
    assert code == 0 and took < 10


def test_a_command_s_exit_and_the_signal_that_ended_it_are_read_after_its_output_closes(tmp_path):
    assert _run("exec >&- 2>&-; sleep 1; exit 4", str(tmp_path))[0] == 4
    assert _run("kill -9 $$", str(tmp_path))[0] == -9


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="os.waitid, which the image's and the Mac app's Pythons have")
def test_a_command_s_exit_is_read_without_reaping_it_so_that_its_id_stays_its_own_until_reaped(tmp_path):
    mark = "ctl-check-test-%d" % time.monotonic_ns()
    p = cc.start_command("exit 3", str(tmp_path), mark)
    code, _ = cc.read_output(p, 10)
    waitable = os.WEXITED | os.WNOHANG | os.WNOWAIT
    assert code == 3 and os.waitid(os.P_PID, p.pid, waitable) is not None
    cc.end_command(p, mark)                                   # its group killed, by an id still its own
    assert os.waitid(os.P_PID, p.pid, waitable) is not None
    cc.reap(p)
    with pytest.raises(ChildProcessError):
        os.waitid(os.P_PID, p.pid, waitable)


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="os.waitid, which the image's and the Mac app's Pythons have")
def test_a_command_another_wait_reaped_has_an_exit_not_known_which_is_not_run_and_judges_nothing(tmp_path):
    mark = "ctl-check-test-%d" % time.monotonic_ns()
    p = cc.start_command("exit 0", str(tmp_path), mark)
    os.waitpid(p.pid, 0)                                       # reaped by a wait not the round's
    assert cc.exited(p) is cc.LOST                             # never 0, as Popen.poll would take it
    assert cc.read_output(p, 10)[0] is cc.LOST
    result = cc.outcome(1, cc.LOST, b"ok")
    assert result == cc.not_run(1, cc.NO_EXIT) and not cc.judges([result])
    cc.end_command(p, mark)


def test_a_command_s_time_is_counted_from_when_it_began():
    mark = "ctl-check-test-%d" % time.monotonic_ns()
    p = cc.start_command("sleep 30", "/", mark)
    began = time.monotonic() - 5
    try:
        assert cc.read_output(p, 3, began=began)[0] is None and time.monotonic() - began < 8
    finally:
        cc.end_command(p, mark)
        cc.reap(p)


def test_outcomes_in_the_round_s_words():
    assert cc.outcome(1, 0, b"ok") == {"id": 1, "outcome": "passed", "tail": "ok"}
    assert cc.outcome(2, 3, b"\xff") == {"id": 2, "outcome": "failed", "exit": 3, "tail": "\ufffd"}
    assert cc.outcome(3, None, b"") == {"id": 3, "outcome": "timed out", "tail": ""}
    results = [cc.outcome(1, 0, b""), cc.not_run(2, cc.NO_START)]
    assert not cc.judges(results) and not cc.passed(results)
    assert cc.judges([cc.not_run(1, cc.NOT_AFTER_BASE)]) and cc.judges([cc.protected(1, 2)])


# ---- Finish's line, and the head read --------------------------------------------------------------------------------

def test_a_commit_of_finish_s_is_told_by_its_line_among_the_trailers(repo):
    git("checkout", "-q", f"pp/{SID}", cwd=repo["path"])
    for message, want in ((f"Title\n\nProofpane-Session: {SID}\n", True),
                          (f"Title\n\nProofpane-Session: {SID}\nChange-Id: I0123\n", True),
                          ("Title\n\nProofpane-Session: ffffffffffff\n", False),
                          (f"Proofpane-Session: {SID} in the title alone\n", False)):
        head = _commit(repo["path"], {"f%d" % time.monotonic_ns(): "x"}, message)
        assert cc.finish_commit(repo["path"], head, SID) is want, message


def test_a_head_is_read_where_the_branch_is_and_none_where_it_is_not(repo):
    assert cc.read_head(repo["path"], SID) == repo["head"]
    git("branch", "-D", f"pp/{SID}", cwd=repo["path"])
    assert cc.read_head(repo["path"], SID) is None


def test_a_submodule_s_change_under_a_protected_path_is_found_though_gitmodules_ignores_it(repo, tmp_path):
    sub = str(tmp_path / "sub")
    make_repo(sub)
    first = git("rev-parse", "HEAD", cwd=sub)[1].strip()
    second = _commit(sub, {"x": "x\n"})
    path = repo["path"]
    git("checkout", "-q", f"pp/{SID}", cwd=path)
    with open(os.path.join(path, ".gitmodules"), "w") as fh:
        fh.write('[submodule "tests/vendor"]\n\tpath = tests/vendor\n\turl = ./sub\n\tignore = all\n')
    git("add", ".gitmodules", cwd=path)
    git("update-index", "--add", "--cacheinfo", f"160000,{first},tests/vendor", cwd=path)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "add the submodule", cwd=path)
    base = git("rev-parse", "HEAD", cwd=path)[1].strip()
    git("update-index", "--cacheinfo", f"160000,{second},tests/vendor", cwd=path)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "move the submodule", cwd=path)
    repo["head"] = git("rev-parse", "HEAD", cwd=path)[1].strip()
    clone = _clone(repo)
    assert cc.judge(clone, base, repo["head"], ["tests/**"], 2 ** 31) == 1


def test_a_graft_in_the_repository_is_not_read_by_the_round_s_judgement(repo):
    # a graft that gives the head no parent would make it come after no base, in the repository itself
    with open(os.path.join(repo["path"], ".git", "info", "grafts"), "w") as fh:
        fh.write(repo["head"] + "\n")
    clone = _clone(repo)
    assert cc.judge(clone, repo["base"], repo["head"], ["tests/**"], 2 ** 31) == 0


def test_two_names_that_fold_to_one_and_are_both_unchanged_since_the_base_count_as_nothing(repo):
    blob = git("hash-object", "-w", "--stdin", cwd=repo["path"], input=b"x\n")[1].strip()
    git("checkout", "-q", f"pp/{SID}", cwd=repo["path"])
    # both in the tree as git holds it, whatever the volume can hold in a worktree
    for name in ("Notes", "notes"):
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},{name}", cwd=repo["path"])
    base = git("rev-parse", git("write-tree", cwd=repo["path"])[1].strip(), cwd=repo["path"])[1].strip()
    base = git("commit-tree", base, "-p", repo["head"], "-m", "both", cwd=repo["path"],
               env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"})[1].strip()
    git("update-index", "--add", "--cacheinfo", f"100644,{blob},other", cwd=repo["path"])
    tree = git("write-tree", cwd=repo["path"])[1].strip()
    head = git("commit-tree", tree, "-p", base, "-m", "other", cwd=repo["path"],
               env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"})[1].strip()
    git("update-ref", f"refs/heads/pp/{SID}", head, cwd=repo["path"])
    repo["head"] = head
    clone = _clone(repo)
    assert cc.judge(clone, base, head, ["notes"], 2 ** 31) == 0


def test_a_git_in_the_clone_or_the_clone_itself_past_its_time_is_the_workspace_s_failure_and_judges_nothing(repo):
    clone = _clone(repo)
    with pytest.raises(cc.Unrun) as e:
        cc._in_clone(clone, "log", timeout=1e-6)
    assert e.value.why == cc.NO_CHECKOUT
    # a clone's time is the workspace's and the repository's as much as the commit's: another session's program can
    # stall the repository's side
    os.makedirs(repo["round"] + "-2")
    with pytest.raises(cc.Unrun) as e:
        cc.make_clone(repo["path"], SID, repo["head"], repo["round"] + "-2", 1e-6)
    assert e.value.why == cc.NO_CHECKOUT and cc.NO_CHECKOUT in cc.JUDGES_NOTHING


def test_an_end_round_s_clone_takes_a_head_below_the_branch_s_tip_and_none_the_branch_no_longer_holds(repo):
    git("checkout", "-q", f"pp/{SID}", cwd=repo["path"])
    later = _commit(repo["path"], {"later.txt": "later\n"})
    os.makedirs(repo["round"])
    clone = cc.make_clone(repo["path"], SID, repo["head"], repo["round"], 60, end=True)
    assert cc.judge(clone, repo["base"], repo["head"], [], 2 ** 31) == 0
    with pytest.raises(cc.Moved):
        os.makedirs(repo["round"] + "-rest")
        cc.make_clone(repo["path"], SID, repo["head"], repo["round"] + "-rest", 60)
    git("reset", "-q", "--hard", repo["base"], cwd=repo["path"])
    _commit(repo["path"], {"other.txt": "other\n"})
    os.makedirs(repo["round"] + "-gone")
    with pytest.raises(cc.Gone):
        cc.make_clone(repo["path"], SID, repo["head"], repo["round"] + "-gone", 60, end=True)
    assert later


def test_a_finish_commit_that_cannot_be_read_raises_rather_than_answering_no(repo):
    with pytest.raises(cc.WorkspaceError):
        cc.finish_commit(repo["path"], "0" * 40, SID)


def test_a_shallow_file_written_in_the_repository_does_not_cut_the_round_s_clone(repo):
    # one line a session's program writes into the repository's .git/shallow would make a clone shallow at that commit,
    # lacking the task's base, and the head judged as not coming after it
    with open(os.path.join(repo["path"], ".git", "shallow"), "w") as fh:
        fh.write(repo["head"] + "\n")
    clone = _clone(repo)
    assert not os.path.exists(os.path.join(clone, ".git", "shallow"))
    assert cc.judge(clone, repo["base"], repo["head"], ["tests/**"], 2 ** 31) == 0


# ---- the round's clone as it was made --------------------------------------------------------------------------------

def test_the_clone_s_state_is_unchanged_by_a_checkout_and_by_a_command_s_own_git_in_it(repo):
    clone = _clone(repo)
    made = cc.clone_state(clone)
    assert made is not None and made == cc.clone_state(clone)
    work = os.path.join(repo["round"], "check-1", "work")
    cc.make_checkout(clone, repo["head"], work, 60)
    git("status", "--short", cwd=work)
    assert cc.clone_state(clone) == made


@pytest.mark.parametrize("change", ["its settings", "its attributes", "an object", "a ref", "a link", "a new folder"])
def test_a_change_to_the_clone_s_own_files_changes_its_state(repo, change):
    clone = _clone(repo)
    made = cc.clone_state(clone)
    top = os.path.join(clone, ".git")
    if change == "its settings":
        with open(os.path.join(top, "config"), "a") as fh:
            fh.write('[filter "x"]\n\tsmudge = cat\n')
    elif change == "its attributes":
        os.makedirs(os.path.join(top, "info"), exist_ok=True)
        with open(os.path.join(top, "info", "attributes"), "w") as fh:
            fh.write("* filter=x\n")
    elif change == "an object":
        pack = next(os.path.join(top, "objects", "pack", n) for n in os.listdir(os.path.join(top, "objects", "pack"))
                    if n.endswith(".pack"))
        os.chmod(pack, 0o644)
        with open(pack, "r+b") as fh:                          # one byte rewritten in place, its size kept
            fh.seek(20)
            byte = fh.read(1)
            fh.seek(20)
            fh.write(bytes([byte[0] ^ 1]))
    elif change == "a ref":
        with open(os.path.join(top, "refs", "heads", "other"), "w") as fh:
            fh.write(repo["base"] + "\n")
    elif change == "a link":
        os.symlink(os.path.join(repo["path"], ".git", "objects"), os.path.join(top, "objects", "elsewhere"))
    else:
        os.makedirs(os.path.join(top, "hooks", "more"))
    assert cc.clone_state(clone) != made


def test_a_file_of_the_clone_s_is_compared_by_its_content_its_times_alone_moving_being_no_change(repo):
    clone = _clone(repo)
    made = cc.clone_state(clone)
    pack = next(os.path.join(clone, ".git", "objects", "pack", n)
                for n in os.listdir(os.path.join(clone, ".git", "objects", "pack")) if n.endswith(".pack"))
    time.sleep(0.05)                                           # past the volume's clock's step
    os.utime(pack)                                             # as git freshens a pack: its times move, not its bytes
    assert cc.clone_state(clone, made) != made and not cc.changed(made, cc.clone_state(clone, made))
    config = os.path.join(clone, ".git", "config")
    st = os.stat(config)
    with open(config, "r+b") as fh:                            # rewritten in place, its size and its time kept
        text = fh.read()
        fh.seek(0)
        fh.write(text.replace(b"bare = false", b"bare = fals_"))
    os.utime(config, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(config).st_size == st.st_size and cc.changed(made, cc.clone_state(clone, made))


def test_a_clone_whose_folder_is_a_link_has_no_state(repo, tmp_path):
    clone = _clone(repo)
    elsewhere = str(tmp_path / "elsewhere")
    os.rename(clone, elsewhere)
    os.symlink(elsewhere, clone)
    assert cc.clone_state(clone) is None


# ---- what a check's own git writes in the clone, and an exit read without os.waitid ----------------------------------

def test_what_a_check_s_own_git_writes_in_the_clone_is_no_change_a_later_checkout_reads_but_a_new_pack_is(repo):
    clone = _clone(repo)
    made = cc.clone_state(clone)
    top = os.path.join(clone, ".git")
    with open(os.path.join(top, "refs", "heads", "other"), "w") as fh:          # a ref, and its log
        fh.write(repo["base"] + "\n")
    os.makedirs(os.path.join(top, "logs", "refs", "heads"), exist_ok=True)
    with open(os.path.join(top, "logs", "refs", "heads", "other"), "w") as fh:
        fh.write("x\n")
    with open(os.path.join(top, "ORIG_HEAD"), "w") as fh:
        fh.write(repo["head"] + "\n")
    os.makedirs(os.path.join(top, "objects", "ab"))                              # a loose object new since made
    with open(os.path.join(top, "objects", "ab", "c" * 38), "wb") as fh:
        fh.write(b"x")
    assert not cc.changed(made, cc.clone_state(clone))
    with open(os.path.join(top, "objects", "pack", "pack-new.pack"), "wb") as fh:  # a pack new since made
        fh.write(b"x")
    assert cc.changed(made, cc.clone_state(clone)) and cc.changed(None, made) and cc.changed(made, None)


def test_a_loose_object_the_clone_held_when_it_was_made_is_compared(repo):
    clone = _clone(repo)
    os.makedirs(os.path.join(clone, ".git", "objects", "cd"))
    held = os.path.join(clone, ".git", "objects", "cd", "e" * 38)
    with open(held, "wb") as fh:
        fh.write(b"held")
    made = cc.clone_state(clone)
    with open(held, "wb") as fh:
        fh.write(b"else")
    assert cc.changed(made, cc.clone_state(clone))


def test_a_git_that_cannot_run_in_the_clone_is_the_workspace_s_failure_not_a_commit_it_lacks(repo):
    clone = _clone(repo)
    assert cc._holds(clone, repo["head"]) and not cc._holds(clone, "0" * 40)
    os.remove(os.path.join(clone, ".git", "HEAD"))                 # no repository there any more: git exits 128
    with pytest.raises(cc.Unrun) as e:
        cc.judge(clone, repo["base"], repo["head"], [], 2 ** 31)
    assert e.value.why == cc.NO_CHECKOUT


def test_without_os_waitid_an_exit_is_read_and_one_another_wait_took_is_lost_all_the_same(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "waitid", raising=False)               # a Mac's Python before 3.13
    mark = "ctl-check-test-%d" % time.monotonic_ns()
    p = cc.start_command("exit 3", str(tmp_path), mark)
    assert cc.read_output(p, 10)[0] == 3
    cc.end_command(p, mark)
    cc.reap(p)
    q = cc.start_command("exit 0", str(tmp_path), mark)
    os.waitpid(q.pid, 0)                                           # reaped by a wait not the round's
    assert cc.exited(q) is cc.LOST
    cc.end_command(q, mark)


def test_without_os_waitid_an_exit_once_read_is_given_again_on_each_later_call(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "waitid", raising=False)               # a Mac's Python before 3.13
    mark = "ctl-check-test-%d" % time.monotonic_ns()
    p = cc.start_command("exit 3", str(tmp_path), mark)
    assert cc.read_output(p, 10)[0] == 3
    assert (cc.exited(p), cc.exited(p)) == (3, 3)
    cc.end_command(p, mark)
    cc.reap(p)
