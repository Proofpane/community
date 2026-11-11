"""A controller's task's rounds of checks and its states: when a round is
queued and when it may start, what each stop, the owner's side, a restart and the agent's own stop do to one, what a
round kept decides, and the loop's step that ends a passed task's program. Each task is a real session of a repository
the test makes, its program a stand-in terminal (test_controller_workspace's _Term), its hooks' word written as they
write it; every command a check runs is a shell builtin, sleep, or one of git, ls, wc, mkdir and chmod, and nothing runs a
real claude."""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from proofpane_workspace import controller as ctl, controller_checks as cc, processes, task_shapes as shapes
from proofpane_workspace import workspace as wsmod
from proofpane_workspace.workspace import git
from test_controller_workspace import _activity, _Term, on, packet, page, request, start, take, ws  # noqa: F401


def side(w, m, rid=None, req="open", hold=False, **over):
    """The owner's side sent again, its next version, listing the requirement `rid` as given."""
    m = dict(m, version=w.controller.record["mirror"]["version"] + 1,
             requirements=[{"id": rid, "state": req, "hold": hold, "waits_for_go": False}] if rid else [], **over)
    take(w, m)
    return m


def checked(w, commands, protected=(), listed=True, **over):
    """A started task whose plan chose `commands` in demo, each a check of its request, with `protected` there; the
    owner's side listing its requirement open unless not `listed`. (side, requirement, start's answer, task)."""
    checks = {"commands": [{"id": n, "repo": "demo", "name": f"check {n}", "command": c}
                           for n, c in enumerate(commands, 1)],
              "protected": [{"repo": "demo", "paths": list(protected)}] if protected else []}
    m = on(w, requests=[request(checks=checks)], **over)
    rid = w.controller.plan_requirement({
        "request": m["requests"][0]["id"], "title": "Hello README", "words": "Make the README say hello.",
        "acceptance": "Its first line is hello.", "repos": ["demo"],
        "checks": list(range(1, len(commands) + 1))})["requirement"]
    if listed:
        m = side(w, m, rid)
    got = start(w, rid)
    return m, rid, got, w.controller.record["tasks"][got["task"]]


def commit(w, got, files=None, message="Say hello"):
    """A commit on the session's branch, as its program makes one in its worktree: its head."""
    work = w._work(got["session"])
    for name, text in (files or {"README.md": "hello\n"}).items():
        path = os.path.join(work, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
    git("add", "-A", cwd=work)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message, cwd=work)
    return git("rev-parse", "HEAD", cwd=work)[1].strip()


def rest(w, got):
    _activity(w, got["session"], "idle")


def look(w):
    """The background loop's look at the tasks: their heads read."""
    w.controller.refresh(heads=True)


def ends(w):
    """The task's program ends by itself (the stand-in terminal's)."""
    _Term.seen[-1].running = False


def outcomes(t):
    return [r["outcome"] for r in t["checks"]["results"]]


def running(w, t, seconds=20):
    """The rounds run in a thread of their own, as the checks' thread runs them, until a command of the task runs."""
    th = threading.Thread(target=w.controller.run_rounds, daemon=True)
    th.start()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with w.controller._lock:
            checking = (t.get("running") or {}).get("checking")
            if checking and checking.get("pid"):
                return th
        time.sleep(0.05)
    raise AssertionError("no command of the task started")


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


STOPS = {
    "End all": lambda w, m, got: take(w, dict(m, version=w.controller.record["mirror"]["version"] + 1,
                                              end_all=m["end_all"] + 1)),
    "stop_task": lambda w, m, got: asyncio.run(w.controller.stop_task({"task": got["task"]})),
    "the owner's Stop": lambda w, m, got: asyncio.run(w.stop(got["session"])),
    "Close": lambda w, m, got: asyncio.run(w.close(got["session"])),
    "Remove": lambda w, m, got: asyncio.run(w.remove(got["session"])),
}


# ---- a round's outcome and what it decides ---------------------------------------------------------------------------

def test_a_round_that_passes_on_an_idle_session_s_commit_ends_its_program_and_the_task_is_done(ws):
    m, rid, got, t = checked(ws, ["exit 0", "true"])
    head = commit(ws, got)
    rest(ws, got)
    look(ws)
    assert (t["queued"]["kind"], t["queued"]["head"]) == ("rest", head)
    assert page(ws)["tasks"][0]["checking"] is True
    ws.controller.run_rounds()
    rnd = t["checks"]
    assert (rnd["round"], rnd["head"], rnd["passed"], outcomes(t), t["runs"]) == (1, head, True, ["passed"] * 2, 2)
    assert t["passed"] == head and t["state"] == "idle" and _Term.seen[-1].running
    asyncio.run(ws.controller._end_passed())
    assert (t["state"], t.get("why"), _Term.seen[-1].running) == ("done", None, False)
    assert os.path.isdir(ws._work(got["session"]))            # its worktree kept for Finish
    assert os.listdir(os.path.join(ws.root, ctl.CHECKS_FOLDER)) == []
    assert page(ws)["tasks"][0]["checking"] is False


def test_a_round_that_fails_leaves_the_task_as_it_is_and_an_end_at_rest_on_that_head_fails_it(ws):
    m, rid, got, t = checked(ws, ["echo said; exit 3"])
    head = commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["checks"]["results"] == [{"id": 1, "outcome": "failed", "exit": 3, "tail": "said\n"}]
    assert t["state"] == "idle" and "passed" not in t and t["judged"] == {head: False}
    look(ws)
    assert "queued" not in t                                   # a head judged is not judged again
    ends(ws)
    look(ws)
    assert (t["state"], t.get("why"), "queued" in t) == ("failed", None, False)


def test_a_check_past_its_seconds_is_timed_out(ws, monkeypatch):
    monkeypatch.setattr(shapes, "CHECK_SECONDS", 1)
    m, rid, got, t = checked(ws, ["sleep 30"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    began = time.monotonic()
    ws.controller.run_rounds()
    assert time.monotonic() - began < 15
    assert t["checks"]["results"] == [{"id": 1, "outcome": "timed out", "tail": ""}]


def test_a_change_to_a_protected_path_fails_every_check_and_runs_none(ws, tmp_path):
    ran = tmp_path / "ran"
    m, rid, got, t = checked(ws, [f": > {ran}", "exit 0"], protected=["tests/**"])
    commit(ws, got, {"tests/test_x.py": "def test_x():\n    pass\n"})
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["checks"]["results"] == [{"id": 1, "outcome": "protected", "protected": 1},
                                      {"id": 2, "outcome": "protected", "protected": 1}]
    assert t["checks"]["passed"] is False and not ran.exists() and t.get("runs", 0) == 0


def test_the_protected_paths_are_judged_from_the_base_the_workspace_kept_not_the_session_s_record(ws):
    m, rid, got, t = checked(ws, ["exit 0"], protected=["README.md"])
    first = commit(ws, got, {"README.md": "changed\n"})
    # the session's programs can write its record: a base moved past the protected change hides nothing
    ws._update(got["session"], lambda record: record.__setitem__("base", first))
    commit(ws, got, {"src.py": "x = 1\n"})
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["checks"]["results"] == [{"id": 1, "outcome": "protected", "protected": 1}]


def test_a_round_written_into_the_session_s_own_record_changes_nothing(ws):
    m, rid, got, t = checked(ws, ["exit 1"])
    head = commit(ws, got)
    ws._update(got["session"], lambda record: record.__setitem__("checks", {"round": 9, "head": head, "passed": True}))
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    ends(ws)
    look(ws)
    assert t["state"] == "failed" and t["checks"]["round"] == 1


def test_each_round_is_told_to_the_chat_under_its_own_key_with_its_outcomes_alone(ws):
    m, rid, got, t = checked(ws, ["echo OUTPUT-OF-THE-CHECK; exit 1"])
    for n in (1, 2):
        commit(ws, got, {"README.md": f"hello {n}\n"})
        rest(ws, got)
        look(ws)
        ws.controller.run_rounds()
    told = {k: w for k, w in ws.controller.wakes.items() if k.startswith("checks:")}
    assert sorted(told) == [f"checks:{t['id']}:1", f"checks:{t['id']}:2"]
    assert [w["checks"]["results"] for w in told.values()] == [[{"id": 1, "outcome": "failed", "exit": 1}]] * 2
    assert t["checks"]["results"][0]["tail"] == "OUTPUT-OF-THE-CHECK\n"
    # its output reaches no model and no other session: not the record's page, read_tree, nor the chat's messages
    for given in (page(ws), ws.controller.read_tree(), ws.controller.wakes, ws.controller._next_message(None)[0]):
        assert "OUTPUT-OF-THE-CHECK" not in json.dumps(given)


# ---- a task's end at rest decided without a round --------------------------------------------------------------------

@pytest.mark.parametrize("why", [cc.NO_BRANCH, cc.NO_COMMIT, cc.NO_BASE, cc.SPENT])
def test_an_end_at_rest_with_no_round_of_its_head_fails_with_the_workspace_s_words(ws, why):
    m, rid, got, t = checked(ws, ["exit 0"])
    if why != cc.NO_COMMIT:
        commit(ws, got)
    if why == cc.NO_BRANCH:
        # a session's program can delete its branch
        git("checkout", "-q", "--detach", cwd=ws._work(got["session"]))
        git("branch", "-D", f"pp/{got['session']}", cwd=os.path.join(ws.repos_dir, "demo"))
    with ws.controller._lock:
        if why == cc.NO_BASE:
            del t["base_commit"]                           # a task started before tasks kept one has none
        if why == cc.SPENT:
            t["runs"] = ctl.CHECK_RUNS
    rest(ws, got)
    ends(ws)
    look(ws)
    assert (t["state"], t["why"]) == ("failed", why)
    assert [s["why"] for s in page(ws)["tasks"]] == [why]


def test_a_commit_of_the_owner_s_finish_is_judged_by_no_round_and_an_end_on_it_fails(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    with open(os.path.join(ws._work(got["session"]), "README.md"), "w") as fh:
        fh.write("hello\n")
    ws.finish(got["session"], "Owner", "owner@example.com")
    rest(ws, got)
    look(ws)
    assert "queued" not in t
    ends(ws)
    look(ws)
    assert (t["state"], t["why"]) == ("failed", cc.FINISHED)


def test_a_head_read_that_cannot_run_decides_nothing(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    ends(ws)

    def held(path, sid):
        raise wsmod.GitHeld("git's places changed")
    monkeypatch.setattr(cc, "read_head", held)
    look(ws)
    assert (t["state"], "ended_head" in t, "queued" in t) == ("idle", False, False)
    monkeypatch.undo()
    look(ws)
    assert t["queued"]["kind"] == "end"
    ws.controller.run_rounds()
    assert t["state"] == "done"


def test_a_task_whose_program_ends_at_rest_while_its_state_said_working_is_taken_as_idle(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    _activity(ws, got["session"], "working")
    look(ws)
    assert t["state"] == "working"
    commit(ws, got)
    rest(ws, got)
    ends(ws)
    ws.controller.refresh()                     # a page's read: no head is read, and the task waits for the loop
    assert (t["state"], "ended_head" in t) == ("idle", False)
    look(ws)
    ws.controller.run_rounds()
    assert t["state"] == "done"


def test_a_passed_task_whose_program_then_ends_not_at_rest_fails(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["passed"]
    _activity(ws, got["session"], "working")
    ends(ws)
    look(ws)
    assert (t["state"], "passed" in t) == ("failed", False)


def test_an_end_at_rest_on_a_head_a_round_passed_on_is_done_without_another_round(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    ends(ws)
    look(ws)
    assert (t["state"], t["rounds"]) == ("done", 1)


# ---- the owner's side ------------------------------------------------------------------------------------------------

def test_a_requirement_the_side_does_not_list_yet_has_its_rounds_wait_and_its_end_undecided(ws):
    m, rid, got, t = checked(ws, ["exit 0"], listed=False)
    head = commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["queued"]["head"] == head and t.get("checks") is None
    ends(ws)
    look(ws)
    assert (t["state"], t["ended_head"], t["queued"]["kind"]) == ("idle", head, "end")
    side(ws, m, rid)
    ws.controller.run_rounds()
    assert t["state"] == "done"


@pytest.mark.parametrize("change", ["off", "stopped", "hold"])
def test_a_round_cut_by_the_controller_s_off_or_stop_or_a_hold_waits_and_runs_again_from_its_first_command(
        ws, change, tmp_path):
    flag = tmp_path / "flag"
    m, rid, got, t = checked(ws, [f"test -f {flag} || sleep 30", "exit 0"])
    head = commit(ws, got)
    rest(ws, got)
    look(ws)
    th = running(ws, t)
    pid = t["running"]["checking"]["pid"]
    if change == "hold":
        side(ws, m, rid, hold=True)
    else:
        side(ws, m, rid, state=change)
    th.join(20)
    assert not th.is_alive() and gone(pid)
    assert (t.get("checks"), t["queued"]["head"], t["runs"], "running" in t) == (None, head, 1, False)
    ws.controller.run_rounds()
    assert t.get("checks") is None and t["queued"]
    flag.write_text("")
    side(ws, m, rid)
    ws.controller.run_rounds()
    assert (t["checks"]["round"], outcomes(t), t["runs"]) == (1, ["passed", "passed"], 3)


def test_a_round_never_starts_once_its_requirement_is_closed_and_an_end_waiting_on_one_fails(ws, tmp_path):
    ran = tmp_path / "ran"
    m, rid, got, t = checked(ws, [f": > {ran}"])
    commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")
    look(ws)
    assert t["queued"]
    side(ws, m, rid, req="closed")
    look(ws)
    ws.controller.run_rounds()
    assert "queued" not in t and not ran.exists()
    ends(ws)
    look(ws)
    assert (t["state"], t["why"]) == ("failed", cc.CLOSED)


# ---- every stop ends its task, whatever its round is doing -----------------------------------------------------------

@pytest.mark.parametrize("how", list(STOPS))
def test_a_round_queued_never_starts_once_a_stop_ends_its_task(ws, how, tmp_path):
    ran = tmp_path / "ran"
    m, rid, got, t = checked(ws, [f": > {ran}"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    assert t["queued"]
    STOPS[how](ws, m, got)
    look(ws)
    ws.controller.run_rounds()
    assert not ran.exists() and "queued" not in t and t.get("runs", 0) == 0 and t.get("checks") is None
    assert t["state"] == ("failed" if how == "Remove" else "stopped")


@pytest.mark.parametrize("how", list(STOPS))
def test_a_check_running_when_a_stop_ends_its_task_is_ended_with_its_group_and_its_round_kept(ws, how):
    m, rid, got, t = checked(ws, ["sleep 30", "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    th = running(ws, t)
    pid = t["running"]["checking"]["pid"]
    began = time.monotonic()
    STOPS[how](ws, m, got)
    th.join(20)
    assert not th.is_alive() and time.monotonic() - began < 15 and gone(pid)
    assert t["checks"]["results"] == [{"id": 1, "outcome": "not run", "why": cc.STOPPED},
                                      {"id": 2, "outcome": "not run", "why": cc.STOPPED}]
    look(ws)
    assert t["state"] == ("failed" if how == "Remove" else "stopped") and "running" not in t and t["runs"] == 1


def test_a_stop_before_a_command_s_start_starts_nothing_after_it(ws, monkeypatch, tmp_path):
    ran = tmp_path / "ran"
    m, rid, got, t = checked(ws, ["exit 0", f": > {ran}"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    removed, real = [], cc.remove_checkout

    def remove(folder):
        real(folder)
        if not removed:                                        # the first check's outcome kept, the second not started
            removed.append(folder)
            asyncio.run(ws.stop(got["session"]))
    monkeypatch.setattr(cc, "remove_checkout", remove)
    ws.controller.run_rounds()
    assert not ran.exists() and t["runs"] == 1
    assert t["checks"]["results"] == [{"id": 1, "outcome": "passed", "tail": ""},
                                      {"id": 2, "outcome": "not run", "why": cc.STOPPED}]
    look(ws)
    assert t["state"] == "stopped"


def test_a_stop_between_a_round_s_last_command_and_its_keeping_decides_before_the_round(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    real, seen = ws.controller._round_end, []

    def end(task_id, r, final, fate, path, sid):
        # every command has run and the round is not kept yet: the owner's Stop comes now
        seen.append([x["outcome"] for x in r["results"]])
        asyncio.run(ws.stop(got["session"]))
        return real(task_id, r, final, fate, path, sid)
    monkeypatch.setattr(ws.controller, "_round_end", end)
    ws.controller.run_rounds()
    assert seen == [["passed"]]
    # kept by the stop's cut as it stood, once, its results all in; never marked passed, and the stop decides
    assert (t["checks"]["results"], t["checks"]["passed"], "passed" in t) == (
        [{"id": 1, "outcome": "passed", "tail": ""}], True, False)
    assert (t["checks"]["round"], t["rounds"]) == (1, 1)
    look(ws)
    assert t["state"] == "stopped" and "running" not in t


def test_a_stop_between_a_pass_and_the_loop_s_step_ends_the_task_stopped(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["passed"]
    asyncio.run(ws.stop(got["session"]))
    asyncio.run(ws.controller._end_passed())
    look(ws)
    assert (t["state"], "passed" in t) == ("stopped", False)


def test_a_passed_task_s_program_is_not_ended_while_the_controller_is_stopped(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    m = side(ws, m, rid, state="stopped")
    look(ws)
    asyncio.run(ws.controller._end_passed())
    assert t["state"] == "idle" and _Term.seen[-1].running
    side(ws, m, rid, state="on")
    look(ws)
    asyncio.run(ws.controller._end_passed())
    assert t["state"] == "done"


# ---- a restart and the agent's own stop ------------------------------------------------------------------------------

def test_a_restart_passes_over_a_task_waiting_on_its_end_round_and_its_round_runs_once_a_side_comes(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    head = commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")
    ends(ws)
    look(ws)
    assert (t["state"], t["ended_head"], t["queued"]["kind"]) == ("idle", head, "end")
    again = ctl.Controller(ws)
    kept = again.record["tasks"][t["id"]]
    assert (kept["state"], "queued" in kept) == ("idle", False)
    again.tick()
    assert kept["queued"]["kind"] == "end"                     # no side held yet: it waits
    again.run_rounds()
    assert kept.get("checks") is None and kept["state"] == "idle"
    asyncio.run(again.take(dict(m, version=m["version"] + 1, state="on")))
    again.run_rounds()
    assert (kept["state"], kept["checks"]["passed"]) == ("done", True)


def test_a_check_a_restart_left_running_is_ended_by_its_start_and_mark_and_its_round_kept(ws):
    m, rid, got, t = checked(ws, ["sleep 30", "exit 0"])
    head = commit(ws, got)
    folder = tempfile.mkdtemp(dir=ws.controller._checks_dir())
    mark = f"ctl-check-{t['id'][:12]}-1-1"
    p = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], env=cc.check_env(mark), start_new_session=True)
    with ws.controller._lock:
        t["running"] = {"kind": "rest", "head": head, "folder": folder, "results": [], "ended": None,
                        "checking": {"mark": mark, "number": 1, "pid": p.pid, "start": processes._started(p.pid)}}
        ws.controller._save()
    again = ctl.Controller(ws)
    again.tick()
    assert p.wait(10) is not None
    kept = again.record["tasks"][t["id"]]
    assert kept["checks"]["results"] == [{"id": 1, "outcome": "not run", "why": cc.AGENT_STOPPED},
                                         {"id": 2, "outcome": "not run", "why": cc.AGENT_STOPPED}]
    assert "running" not in kept and not os.path.exists(folder) and kept["state"] == "stopped"


def test_a_folder_a_crash_left_in_the_checks_folder_is_removed_at_the_loop_s_first_round(ws):
    left = os.path.join(ws.controller._checks_dir(), "round-left")
    os.makedirs(os.path.join(left, "clone"))
    ws.controller.tick()
    assert os.listdir(ws.controller._checks_dir()) == []


def test_the_agent_s_own_stop_ends_a_running_check_and_keeps_its_round(ws):
    m, rid, got, t = checked(ws, ["sleep 30"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.start_checks()                               # the checks' own thread, as the agent starts it
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not (t.get("running") or {}).get("checking", {}) \
            or not ((t.get("running") or {}).get("checking") or {}).get("pid"):
        time.sleep(0.05)
    pid, folder = t["running"]["checking"]["pid"], t["running"]["folder"]
    thread = ws.controller._checks_thread
    asyncio.run(ws.controller.shut())
    assert not thread.is_alive() and gone(pid) and not os.path.exists(folder)
    assert t["checks"]["results"] == [{"id": 1, "outcome": "not run", "why": cc.AGENT_STOPPED}]
    assert "running" not in t


# ---- counted and let go ----------------------------------------------------------------------------------------------

def test_a_round_whose_clone_meets_a_git_held_is_neither_kept_nor_counted_and_is_tried_again(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    real, calls = cc._git_env, []

    def env(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise wsmod.GitHeld("git's places changed while an agent's run worked")
        return real(*args, **kwargs)
    monkeypatch.setattr(cc, "_git_env", env)
    look(ws)
    ws.controller.run_rounds()
    assert (t.get("checks"), t.get("runs", 0), t.get("rounds", 0), "queued" in t) == (None, 0, 0, False)
    assert t.get("unstarted", 0) == 0                          # a GitHeld counts for nothing
    look(ws)
    ws.controller.run_rounds()
    assert (t["checks"]["round"], t["checks"]["passed"]) == (1, True)


def test_a_round_is_let_go_each_time_its_head_moves_and_the_task_s_runs_are_spent_at_their_bound(ws, monkeypatch):
    monkeypatch.setattr(ctl, "CHECK_RUNS", 2)
    m, rid, got, t = checked(ws, ["sleep 2", "exit 0"])
    for n in (1, 2):
        commit(ws, got, {"README.md": f"hello {n}\n"})
        rest(ws, got)
        look(ws)
        th = running(ws, t)
        commit(ws, got, {"README.md": f"moved {n}\n"})     # the session commits again while the round runs
        th.join(20)
        assert t.get("checks") is None and t["runs"] == n and "running" not in t
    look(ws)
    assert "queued" not in t
    ends(ws)
    look(ws)
    assert (t["state"], t["why"]) == ("failed", cc.SPENT)


def test_the_tails_the_record_keeps_are_bounded_as_its_json_holds_them(ws, monkeypatch):
    monkeypatch.setattr(ctl, "TAILS_KEPT", 20)        # one tail of ten characters takes 12 bytes as JSON
    m, rid, got, t = checked(ws, ["printf 0123456789", "printf abcdefghij"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert [(r.get("tail"), r.get("tail_dropped")) for r in t["checks"]["results"]] == [
        ("0123456789", None), (None, True)]


def test_the_oldest_rounds_tails_are_let_go_first(ws, monkeypatch):
    monkeypatch.setattr(ctl, "TAILS_KEPT", 30)

    def task(seq, *tails):
        return {"checked_seq": seq, "checks": {"results": [{"id": n, "outcome": "passed", "tail": x}
                                                           for n, x in enumerate(tails, 1)]}}
    older, newer = task(1, "0123456789", "0123456789"), task(2, "abcdefghij", "abcdefghij")
    with ws.controller._lock:
        ws.controller.record["tasks"] = {"older": older, "newer": newer}
        ws.controller._bound_tails()
    assert [r.get("tail") for r in newer["checks"]["results"]] == ["abcdefghij"] * 2
    assert [r.get("tail_dropped") for r in older["checks"]["results"]] == [True, True]


# ---- what else changes with the states -------------------------------------------------------------------------------

def test_the_controller_is_busy_while_a_round_runs_or_may_start(ws):
    m, rid, got, t = checked(ws, ["sleep 30"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    _Term.seen[-1].running = True
    assert ws.controller.busy()
    m = side(ws, m, rid, state="stopped")
    assert not ws.controller.busy()                            # queued, and may not start
    side(ws, m, rid, state="on")
    th = running(ws, t)
    m = side(ws, m, rid, state="on")
    assert ws.controller.busy()
    asyncio.run(ws.controller.stop_task({"task": got["task"]}))
    th.join(20)
    assert not ws.controller.busy()


def test_read_tree_gives_the_checks_escaped_the_protected_paths_and_each_task_s_last_round(ws):
    m, rid, got, t = checked(ws, ["exit 1"], protected=["docs/**"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    tree = ws.controller.read_tree()
    item = next(r for r in tree["requirements"] if r["id"] == rid)
    assert item["checks"] == [{"id": 1, "repo": "demo", "name": {"text": "check 1", "by": "owner"}}]
    assert item["protected"] == [{"repo": "demo", "paths": [{"text": "docs/**", "by": "owner"}]}]
    task = item["tasks"][0]
    assert (task["checks"]["results"], task["checking"], task["rounds_left"], task["why"]) == (
        [{"id": 1, "outcome": "failed", "exit": 1}], False, ctl.CHECK_ROUNDS - 1, None)


def read_through(w):
    """The app's reads as its rounds make them (test_controller_workspace's read_through)."""
    return page(w, page(w)["seq"])


def test_a_fresh_chat_waits_until_the_app_has_read_past_the_newest_round(ws, monkeypatch):
    b64, digest = packet()
    m, rid, got, t = checked(ws, ["exit 1"], packet_b64=b64, packet_digest=digest)
    read_through(ws)
    m = side(ws, m, rid)
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    made = []

    async def fresh(given, covered):
        made.append(given["version"])
    monkeypatch.setattr(ws.controller, "_fresh_chat", fresh)
    monkeypatch.setattr(ws.controller, "cannot_run", lambda given: None)
    asyncio.run(ws.controller.keep_chat())
    assert made == []                   # the app has not read the round: the packet it sends would not hold it
    read_through(ws)
    m = side(ws, m, rid)
    asyncio.run(ws.controller.keep_chat())
    assert made == [m["version"]]


def test_a_fresh_chat_is_not_made_where_the_controller_is_turned_off_during_its_awaits(ws, monkeypatch):
    b64, digest = packet()
    m, rid, got, t = checked(ws, ["exit 1"], packet_b64=b64, packet_digest=digest)
    read_through(ws)
    m = side(ws, m, rid)
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    read_through(ws)                    # the app reads past the round and sends a side: the packet would hold it
    m = side(ws, m, rid)
    made = []

    async def fresh(given, covered):
        made.append(given["version"])

    def cannot_run(given):
        # while keep_chat awaits this, the owner's side turns the controller off, kept under the controller's lock
        c = ws.controller
        with c._lock:
            c.record["mirror"] = dict(c.record["mirror"], state="off", version=c.record["mirror"]["version"] + 1)
        return None
    monkeypatch.setattr(ws.controller, "_fresh_chat", fresh)
    monkeypatch.setattr(ws.controller, "cannot_run", cannot_run)
    asyncio.run(ws.controller.keep_chat())
    assert made == []


def test_a_fresh_chat_is_not_made_where_a_side_sent_during_its_awaits_holds_no_packet(ws, monkeypatch):
    b64, digest = packet()
    m, rid, got, t = checked(ws, ["exit 1"], packet_b64=b64, packet_digest=digest)
    read_through(ws)
    m = side(ws, m, rid)
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    read_through(ws)                    # the app reads past the round and sends a side: the packet would hold it
    m = side(ws, m, rid)
    made = []

    async def fresh(given, covered):
        made.append(given["version"])

    def cannot_run(given):
        # while keep_chat awaits this, a side comes with no packet, kept under the controller's lock
        c = ws.controller
        with c._lock:
            c.record["mirror"] = dict(c.record["mirror"], packet_b64=None, packet_digest=None,
                                      version=c.record["mirror"]["version"] + 1)
        return None
    monkeypatch.setattr(ws.controller, "_fresh_chat", fresh)
    monkeypatch.setattr(ws.controller, "cannot_run", cannot_run)
    asyncio.run(ws.controller.keep_chat())
    assert made == []


# ---- the session's own read ------------------------------------------------------------------------------------------

def agent_call(w, method, path, **kwargs):
    """A route of the workspace's agent, called in-process as the app calls it."""
    import httpx
    from proofpane_workspace import agent
    from workspace_fixtures import SECRET

    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(w, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            return await c.request(method, path, **kwargs)
    answer = asyncio.run(call())
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_the_session_s_own_read_gives_each_check_s_output_marked_as_a_program_s_and_no_other_answer_does(ws):
    m, rid, got, t = checked(ws, ["echo OUTPUT-OF-THE-CHECK; exit 1", "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    sid = got["session"]
    view = agent_call(ws, "GET", f"/sessions/{sid}")
    assert view["checks"]["results"] == [
        {"id": 1, "outcome": "failed", "exit": 1, "name": "check 1",
         "output": {"text": "OUTPUT-OF-THE-CHECK\n", "by": "program"}},
        {"id": 2, "outcome": "passed", "name": "check 2", "output": {"text": "", "by": "program"}}]
    # the list, and the answer of a route that changes a session, carry none of it
    listed = agent_call(ws, "GET", "/sessions")
    renamed = agent_call(ws, "POST", f"/sessions/{sid}/title", json={"title": "Renamed"})
    assert all("checks" not in one for one in listed["sessions"]) and "checks" not in renamed
    assert "OUTPUT-OF-THE-CHECK" not in json.dumps(listed) + json.dumps(renamed)
    with ws.controller._lock:
        t["checks"]["results"][0].pop("tail")
        t["checks"]["results"][0]["tail_dropped"] = True
    dropped = agent_call(ws, "GET", f"/sessions/{sid}")["checks"]["results"][0]
    assert (dropped["output"], dropped["tail_dropped"]) == (None, True)


def test_a_session_the_controller_did_not_start_has_no_checks_in_its_own_read(ws):
    made = ws.prepare("demo", "claude", "Say hello.")[0]
    assert agent_call(ws, "GET", f"/sessions/{made['id']}")["checks"] is None


def test_a_limit_line_written_after_an_end_at_rest_does_not_move_the_task_to_waiting(ws):
    from test_controller_workspace import _limit
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")                     # its end round waits
    ends(ws)
    look(ws)
    assert t["ended_head"] and t["state"] == "idle"
    _limit(ws, got["session"], "rate_limit")                   # a program the session left writes one
    look(ws)
    assert t["state"] == "idle" and t["queued"]["kind"] == "end"
    side(ws, m, rid, state="on")
    ws.controller.run_rounds()
    assert t["state"] == "done"


def test_no_rest_round_is_queued_for_a_session_at_rest_on_its_base(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    rest(ws, got)
    look(ws)
    assert "queued" not in t and t.get("checks") is None


# ---- restarts, the agent's stop and the heads a round reads ----------------------------------------------------------

def test_the_loop_s_first_round_ends_what_a_restart_left_before_it_observes_any_task_or_starts_the_thread(
        ws, monkeypatch):
    again = ctl.Controller(ws)
    order = []
    real_reap, real_refresh = again._reap, again.refresh
    monkeypatch.setattr(again, "_reap", lambda: (order.append("reap"), real_reap())[1])
    monkeypatch.setattr(again, "refresh", lambda *a, **k: (order.append("refresh"), real_refresh(*a, **k))[1])
    monkeypatch.setattr(again, "start_checks", lambda: order.append("thread"))

    async def started():
        again.start()                                          # the background loop, as the agent's lifespan starts it
        assert "thread" not in order                           # not with the loop: after the first round's reaping
        again.loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await again.loop
    asyncio.run(started())
    again.tick()
    again.tick()
    assert order[:3] == ["reap", "thread", "refresh"] and order.count("reap") == 1


def test_an_end_round_judges_the_head_its_program_ended_on_after_the_owner_s_finish(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    head = commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")                     # its end round waits
    ends(ws)
    look(ws)
    assert t["ended_head"] == head
    with open(os.path.join(ws._work(got["session"]), "notes.txt"), "w") as fh:
        fh.write("left uncommitted\n")
    ws.finish(got["session"], "Owner", "owner@example.com")    # commits on the branch, after the end
    assert git("rev-parse", "HEAD", cwd=ws._work(got["session"]))[1].strip() != head
    side(ws, m, rid, state="on")
    ws.controller.run_rounds()
    assert (t["state"], t["checks"]["head"], t["checks"]["passed"]) == ("done", head, True)


@pytest.mark.parametrize("how", ["rewritten", "deleted"])
def test_an_end_round_whose_branch_no_longer_holds_its_head_decides_the_task_failed(ws, how):
    m, rid, got, t = checked(ws, ["exit 0"])
    head = commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")
    ends(ws)
    look(ws)
    work = ws._work(got["session"])
    if how == "rewritten":
        git("reset", "-q", "--hard", t["base_commit"], cwd=work)
        commit(ws, got, {"other.txt": "another way\n"})
    else:
        git("checkout", "-q", "--detach", cwd=work)
        git("branch", "-D", f"pp/{got['session']}", cwd=os.path.join(ws.repos_dir, "demo"))
    side(ws, m, rid, state="on")
    ws.controller.run_rounds()
    assert (t["state"], t["why"], t.get("checks")) == (
        "failed", cc.HEAD_GONE if how == "rewritten" else cc.NO_BRANCH, None)


def test_a_filter_a_check_writes_into_its_clone_never_runs_in_a_later_check_s_checkout(ws, tmp_path):
    ran, evil = tmp_path / "ran", tmp_path / "evil.sh"
    # the session's commit names a filter no setting defines, which a checkout passes over; the first check then
    # defines it in the clone its checkout is a worktree of, as a Makefile no protected path covers could
    first = (f"printf ': > {ran}\\n' > {evil}; read -r line < .git; common=${{line#gitdir: }}; "
             f"common=${{common%/worktrees/*}}; printf '[filter \"x\"]\\n\\tsmudge = sh {evil}\\n' >> \"$common/config\"")
    m, rid, got, t = checked(ws, [first, "exit 0"])
    commit(ws, got, {".gitattributes": "README.md filter=x\n", "README.md": "hello\n"})
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert evil.exists()                                       # the first check did write its filter
    # the second check's checkout is not made in the clone the first changed, and the check is not run
    assert outcomes(t) == ["passed", "not run"] and t["checks"]["results"][1]["why"] == cc.CHANGED
    assert not ran.exists()


@pytest.mark.parametrize("running", [1, "x", True, [1]])
def test_a_record_whose_round_under_way_is_not_an_object_is_put_aside_and_the_agent_starts(ws, running, capsys):
    m, rid, got, t = checked(ws, ["exit 0"])
    with ws.controller._lock:
        record = json.loads(json.dumps(ws.controller.record))
    record["tasks"][t["id"]]["running"] = running
    assert ctl._sound(record) is False
    with open(ws.controller.path, "w") as fh:
        json.dump({"record": record, "mac": "0" * 64}, fh)
    again = ctl.Controller(ws)
    assert again.record["seq"] == 0 and "not one the workspace wrote" in capsys.readouterr().err


def test_a_cut_after_a_restart_kills_no_process_by_an_id_it_kept_and_the_reaping_ends_it(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["sleep 30"])
    head = commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")
    ends(ws)
    look(ws)                                                   # ended at rest: the restart passes it over
    folder = tempfile.mkdtemp(dir=ws.controller._checks_dir())
    mark = f"ctl-check-{t['id'][:12]}-1-1"
    p = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], env=cc.check_env(mark), start_new_session=True)
    with ws.controller._lock:
        del t["queued"]
        t["running"] = {"kind": "end", "head": head, "folder": folder, "results": [], "ended": None,
                        "checking": {"mark": mark, "number": 1, "pid": p.pid, "start": processes._started(p.pid)}}
        ws.controller._save()
    again = ctl.Controller(ws)
    killed, real = [], cc.kill_group
    monkeypatch.setattr(cc, "kill_group", lambda pid: (killed.append(pid), real(pid))[1])
    asyncio.run(again.take(dict(m, version=m["version"] + 1, state="off")))     # a cut before the reaping
    assert killed == [] and p.poll() is None
    again.tick()
    assert p.wait(10) is not None and not os.path.exists(folder)


def test_the_agent_s_stop_coming_between_a_round_s_head_read_and_its_start_starts_nothing(ws, monkeypatch, tmp_path):
    ran = tmp_path / "ran"
    m, rid, got, t = checked(ws, [f": > {ran}"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    real = ws.controller._branch_head

    def read(path, sid):
        ws.controller._checks_stop.set()                       # shut sets it, then takes the lock
        return real(path, sid)
    monkeypatch.setattr(ws.controller, "_branch_head", read)
    ws.controller._run_round(t["id"])
    assert not ran.exists() and "running" not in t and t["queued"] and t.get("runs", 0) == 0


def test_a_failure_reading_the_head_at_a_round_s_end_leaves_no_round_under_way(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    real, calls = cc.read_head, []

    def read(path, sid):
        calls.append(1)
        if len(calls) == 3:                                    # the read at the round's end
            raise OSError(11, "fork refused")
        return real(path, sid)
    monkeypatch.setattr(cc, "read_head", read)
    ws.controller.run_rounds()
    assert len(calls) == 3 and "running" not in t and t["checks"]["passed"] is True and "passed" not in t
    assert os.listdir(ws.controller._checks_dir()) == []


def test_a_round_whose_checkouts_all_fail_is_neither_kept_nor_numbered_and_an_end_round_s_task_fails_past_the_bound(
        ws, monkeypatch):
    monkeypatch.setattr(ctl, "UNSTARTED_WAIT", 0)              # the bound, not the wait (below)
    def refused(*args, **kwargs):
        raise cc.Unrun(cc.NO_CHECKOUT)
    monkeypatch.setattr(cc, "make_checkout", refused)
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert (t.get("checks"), t.get("rounds", 0), t["unstarted"]) == (None, 0, 1)
    ends(ws)
    for _ in range(ctl.UNSTARTED_MOST - 1):
        look(ws)
        ws.controller.run_rounds()
    assert (t["state"], t["why"], t.get("checks")) == ("failed", cc.NOT_STARTED, None)


def test_a_rest_round_is_queued_no_more_past_the_rounds_the_workspace_could_not_start(ws, monkeypatch):
    monkeypatch.setattr(ctl, "UNSTARTED_WAIT", 0)              # the bound, not the wait (below)
    def refused(*args, **kwargs):
        raise cc.Unrun(cc.NO_CHECKOUT)
    monkeypatch.setattr(cc, "make_clone", refused)
    m, rid, got, t = checked(ws, ["exit 0"])
    for n in range(ctl.UNSTARTED_MOST):
        commit(ws, got, {"README.md": f"hello {n}\n"})
        rest(ws, got)
        look(ws)
        ws.controller.run_rounds()
    assert t["unstarted"] == ctl.UNSTARTED_MOST
    commit(ws, got, {"README.md": "once more\n"})
    look(ws)
    assert "queued" not in t


def test_a_link_put_where_the_checks_folder_goes_is_put_aside_and_a_folder_made(ws, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(str(elsewhere), os.path.join(ws.root, ctl.CHECKS_FOLDER))
    folder = ws.controller._checks_dir()
    assert os.path.isdir(folder) and not os.path.islink(folder)
    assert [n for n in os.listdir(ws.root) if n.startswith(ctl.CHECKS_FOLDER + ".unread-")]


def test_a_round_let_go_for_a_moved_head_keeps_the_round_queued_for_the_new_one(ws):
    m, rid, got, t = checked(ws, ["sleep 2", "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    th = running(ws, t)
    moved = commit(ws, got, {"README.md": "moved\n"})
    look(ws)                                                   # queued for the new head while the old round runs
    th.join(20)
    assert t["queued"]["head"] == moved and t.get("checks") is None


def test_a_head_whose_commit_cannot_be_read_is_not_taken_and_decides_nothing(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    ends(ws)

    def unread(path, head, sid):
        raise wsmod.GitHeld("git's places changed")
    monkeypatch.setattr(cc, "finish_commit", unread)
    look(ws)
    assert (t["state"], "ended_head" in t, "queued" in t) == ("idle", False, False)


def test_an_older_round_s_small_tail_is_let_go_with_a_newer_one_s_that_did_not_fit(ws, monkeypatch):
    monkeypatch.setattr(ctl, "TAILS_KEPT", 30)

    def task(seq, *tails):
        return {"checked_seq": seq, "checks": {"results": [{"id": n, "outcome": "passed", "tail": x}
                                                           for n, x in enumerate(tails, 1)]}}
    older, newer = task(1, "ab"), task(2, "x" * 30)
    with ws.controller._lock:
        ws.controller.record["tasks"] = {"older": older, "newer": newer}
        ws.controller._bound_tails()
    assert [r.get("tail_dropped") for r in newer["checks"]["results"] + older["checks"]["results"]] == [True, True]


def test_a_round_queued_when_the_workspace_restarts_never_starts_and_its_task_is_stopped(ws, tmp_path):
    ran = tmp_path / "ran"
    m, rid, got, t = checked(ws, [f": > {ran}"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    assert t["queued"]
    with ws.controller._lock:
        ws.controller._save()
    again = ctl.Controller(ws)
    kept = again.record["tasks"][t["id"]]
    assert (kept["state"], "queued" in kept) == ("stopped", False)
    asyncio.run(again.take(dict(m, version=m["version"] + 1)))
    again.tick()
    again.run_rounds()
    assert not ran.exists() and kept.get("checks") is None and kept.get("runs", 0) == 0


def test_a_head_that_moves_between_a_check_s_checkout_and_its_start_keeps_the_round_queued_for_it(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    real, moved = cc.make_checkout, []

    def checkout(*args, **kwargs):
        real(*args, **kwargs)
        if not moved:
            # the session commits again after the first check's checkout, before its start reads the head
            moved.append(commit(ws, got, {"README.md": "moved\n"}))
            look(ws)
    monkeypatch.setattr(cc, "make_checkout", checkout)
    ws.controller.run_rounds()
    assert t.get("checks") is None and t.get("runs", 0) == 0
    assert (t["queued"]["kind"], t["queued"]["head"]) == ("rest", moved[0])



def test_the_agent_s_stop_while_a_round_of_the_loop_reaps_starts_no_thread_and_observes_nothing(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    c = ws.controller
    reaping, release = threading.Event(), threading.Event()
    real_reap = c._reap

    def slow_reap():
        reaping.set()
        release.wait(20)                                       # ps answering slowly, while the agent stops
        real_reap()
    monkeypatch.setattr(c, "_reap", slow_reap)
    c._loop = asyncio.new_event_loop()                         # the loop as the agent's lifespan starts it
    tick = threading.Thread(target=c.tick, daemon=True)
    tick.start()
    assert reaping.wait(20)
    asyncio.run(c.shut())
    ends(ws)                                                   # the lifespan then stops the terminals
    release.set()
    tick.join(20)
    c._loop.close()
    assert not tick.is_alive() and c._checks_thread is None
    # observed by nothing: before the fix, the reaping's tick went on to look at the task, whose program the agent's
    # stop had ended, and took it as an end at rest, keeping its head and queueing its end round
    assert (t["state"], "ended_head" in t, "queued" in t) == ("starting", False, False)


@pytest.mark.parametrize("why", ["another start", "no start"])
def test_a_process_whose_start_is_not_the_one_kept_survives_the_reaping_after_a_restart(ws, why):
    m, rid, got, t = checked(ws, ["sleep 30"])
    head = commit(ws, got)
    folder = tempfile.mkdtemp(dir=ws.controller._checks_dir())
    # a stranger's process, now holding the id the record kept for a check: no mark, and another start or none
    stranger = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)
    try:
        with ws.controller._lock:
            t["running"] = {"kind": "rest", "head": head, "folder": folder, "results": [], "ended": None,
                            "checking": {"mark": f"ctl-check-{t['id'][:12]}-1-1", "number": 1, "pid": stranger.pid,
                                         "start": "Thu Jan  1 00:00:00 1970" if why == "another start" else None}}
            ws.controller._save()
        again = ctl.Controller(ws)
        again.tick()
        assert stranger.poll() is None and "running" not in again.record["tasks"][t["id"]]
    finally:
        stranger.kill()
        stranger.wait(10)


@pytest.mark.skipif(sys.platform != "darwin", reason="a session is owned on a Mac alone")
def test_a_session_is_owned_again_only_where_its_leader_runs_from_the_start_kept():
    p = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)
    run = "ctl-check-own-kept-test"
    try:
        begun = processes._started(p.pid)
        assert processes.own_kept(run, p.pid, "Thu Jan  1 00:00:00 1970") is False
        assert processes.own_kept(run, p.pid, None) is False
        assert processes.own_kept(run, p.pid, begun) is True
        assert processes._SESSIONS[run] == {p.pid: begun}
    finally:
        processes._forget(run, p.pid)
        p.kill()
        p.wait(10)


def test_an_end_s_head_read_while_its_program_ran_waits_for_a_read_made_after_the_end(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    real = cc.read_head

    def read(path, sid):
        head = real(path, sid)
        ends(ws)                                               # the program ends between the read and the look
        return head
    monkeypatch.setattr(cc, "read_head", read)
    look(ws)
    assert (t["state"], "ended_head" in t, "queued" in t) == ("idle", False, False)
    monkeypatch.undo()
    look(ws)
    assert t["ended_head"] and t["queued"]["kind"] == "end"


def test_read_tree_gives_no_rounds_left_past_the_rounds_the_workspace_could_not_start(ws, monkeypatch):
    monkeypatch.setattr(ctl, "UNSTARTED_WAIT", 0)              # the bound, not the wait (below)
    def refused(*args, **kwargs):
        raise cc.Unrun(cc.NO_CHECKOUT)
    monkeypatch.setattr(cc, "make_clone", refused)
    m, rid, got, t = checked(ws, ["exit 0"])
    for n in range(ctl.UNSTARTED_MOST):
        commit(ws, got, {"README.md": f"hello {n}\n"})
        rest(ws, got)
        look(ws)
        ws.controller.run_rounds()
    item = next(r for r in ws.controller.read_tree()["requirements"] if r["id"] == rid)
    assert item["tasks"][0]["rounds_left"] == 0


def test_the_agent_s_stop_arriving_while_heads_are_read_has_nothing_observed_after_it(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    c = ws.controller
    real = c._branch_head

    def read(path, sid):
        head = real(path, sid)
        with c._lock:
            c._shutting = True                                 # the agent's stop begins while the loop reads heads
        ends(ws)                                               # and its lifespan then stops the terminals
        return head
    monkeypatch.setattr(c, "_branch_head", read)
    c.refresh(heads=True)
    assert (t["state"], "ended_head" in t, "queued" in t) == ("starting", False, False)


# ---- an end at rest saved at once, a round's clone and its checks' processes -----------------------------------------

@pytest.mark.parametrize("how", ["the owner's Stop", "Close"])
def test_a_stop_of_a_task_waiting_on_its_end_round_is_saved_at_once_and_a_restart_runs_no_round(ws, how, tmp_path):
    ran = tmp_path / "ran"
    m, rid, got, t = checked(ws, [f": > {ran}"])
    head = commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")                     # the controller stopped: the end round waits
    ends(ws)
    look(ws)
    assert (t["state"], t["ended_head"], t["queued"]["kind"]) == ("idle", head, "end")
    STOPS[how](ws, m, got)
    again = ctl.Controller(ws)                                # the agent restarts before any read or look saves
    kept = again.record["tasks"][t["id"]]
    again.tick()
    asyncio.run(again.take(dict(m, version=m["version"] + 1, state="on")))
    again.tick()
    again.run_rounds()
    assert not ran.exists() and kept.get("checks") is None and kept["state"] == "stopped"


def test_an_end_at_rest_seen_while_the_task_was_idle_already_is_saved_and_a_restart_passes_over_it(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    head = commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")
    look(ws)                                                   # at rest while its program runs: idle, and saved
    assert t["state"] == "idle" and "ended_head" not in t
    ends(ws)
    look(ws)                                                   # its end at rest: nothing but its kept head changes
    assert (t["state"], t["ended_head"]) == ("idle", head)
    again = ctl.Controller(ws)
    kept = again.record["tasks"][t["id"]]
    assert (kept["state"], kept.get("ended_head")) == ("idle", head)
    asyncio.run(again.take(dict(m, version=m["version"] + 1, state="on")))
    again.tick()
    again.run_rounds()
    assert (kept["state"], kept["checks"]["passed"]) == ("done", True)


def test_an_end_at_rest_let_go_as_its_program_starts_again_is_saved_and_a_restart_stops_the_task(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")
    ends(ws)
    look(ws)
    assert t["state"] == "idle" and t.get("ended_head")
    _Term.seen[-1].running = True                              # the owner starts its program again, still at rest
    look(ws)
    assert t["state"] == "idle" and "ended_head" not in t
    _Term.seen[-1].running = False                             # and it ends with the agent
    again = ctl.Controller(ws)
    kept = again.record["tasks"][t["id"]]
    assert kept["state"] == "stopped" and "ended_head" not in kept


@pytest.mark.parametrize("change", ["its settings", "its attributes", "its objects", "its hooks"])
def test_a_check_that_changes_the_round_s_clone_leaves_every_later_check_not_run(ws, change, tmp_path):
    common = 'read -r line < .git; common=${line#gitdir: }; common=${common%/worktrees/*}; '
    first = common + {
        "its settings": "printf '[core]\\n\\tx = 1\\n' >> \"$common/config\"",
        "its attributes": "mkdir -p \"$common/info\" && printf '* -text\\n' > \"$common/info/attributes\"",
        "its objects": "for f in \"$common\"/objects/pack/*.pack; do chmod u+w \"$f\"; printf x >> \"$f\"; done",
        "its hooks": "mkdir -p \"$common/hooks\" && printf '#!/bin/sh\\n' > \"$common/hooks/post-checkout\"",
    }[change]
    m, rid, got, t = checked(ws, [first, "exit 0", "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["checks"]["results"] == [{"id": 1, "outcome": "passed", "tail": ""},
                                      {"id": 2, "outcome": "not run", "why": cc.CHANGED},
                                      {"id": 3, "outcome": "not run", "why": cc.CHANGED}]
    assert t["runs"] == 1 and t["judged"]


def test_a_check_s_own_git_in_its_checkout_changes_nothing_a_later_check_needs(ws):
    m, rid, got, t = checked(ws, ["git status --short >/dev/null && git log -1 --format=%H >/dev/null", "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert outcomes(t) == ["passed", "passed"]


def test_the_round_s_clone_changed_before_its_first_check_is_the_workspace_s_own_failure(ws, monkeypatch):
    real = cc.judge

    def judge(clone, *args):
        with open(os.path.join(clone, ".git", "config"), "a") as fh:     # a program writes the clone meanwhile
            fh.write("[core]\n\tx = 1\n")
        return real(clone, *args)
    monkeypatch.setattr(cc, "judge", judge)
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert (t.get("checks"), t.get("runs", 0), t["unstarted"]) == (None, 0, 1)


def test_each_check_s_checkout_is_made_just_before_it_runs_one_at_a_time(ws):
    alone = "test $(ls -d ../../check-* | wc -l) -eq 1"
    m, rid, got, t = checked(ws, [alone, alone])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert outcomes(t) == ["passed", "passed"]


@pytest.mark.parametrize("when", ["its clone is made", "its commits are judged"])
def test_a_round_stopped_while_its_clone_is_made_or_its_commits_judged_makes_nothing_more(ws, monkeypatch, when):
    m, rid, got, t = checked(ws, ["exit 0", "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    real_clone, real_judge, made = cc.make_clone, cc.judge, []

    def clone(*args, **kwargs):
        got_clone = real_clone(*args, **kwargs)
        if when == "its clone is made":
            asyncio.run(ws.stop(got["session"]))             # the owner's Stop comes while the clone is made
        return got_clone

    def judge(*args):
        made.append("judged")
        count = real_judge(*args)
        asyncio.run(ws.stop(got["session"]))                 # or while the commits are judged
        return count
    monkeypatch.setattr(cc, "make_clone", clone)
    monkeypatch.setattr(cc, "judge", judge)
    monkeypatch.setattr(cc, "make_checkout", lambda *args: made.append("checkout"))
    ws.controller.run_rounds()
    assert made == ([] if when == "its clone is made" else ["judged"]) and t.get("runs", 0) == 0
    assert t["checks"]["results"] == [{"id": 1, "outcome": "not run", "why": cc.STOPPED},
                                      {"id": 2, "outcome": "not run", "why": cc.STOPPED}]
    look(ws)
    assert t["state"] == "stopped" and "running" not in t


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="os.waitid, which the image's and the Mac app's Pythons have")
def test_a_command_s_process_is_reaped_only_once_the_record_no_longer_names_its_id(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 3"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    c, seen = ws.controller, {}
    real = c._save

    def save():
        pid = ((t.get("running") or {}).get("checking") or {}).get("pid")
        if pid:
            seen["pid"] = pid
        elif "pid" in seen and "unreaped" not in seen:
            # the record lets the command's id go: its process, which has exited, is not reaped yet
            try:
                seen["unreaped"] = os.waitid(os.P_PID, seen["pid"], os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            except ChildProcessError:
                seen["unreaped"] = False
        real()
    monkeypatch.setattr(c, "_save", save)
    c.run_rounds()
    assert seen["unreaped"] is True and outcomes(t) == ["failed"] and t["checks"]["results"][0]["exit"] == 3
    with pytest.raises(ChildProcessError):                     # and reaped after
        os.waitid(os.P_PID, seen["pid"], os.WEXITED | os.WNOHANG | os.WNOWAIT)


def test_a_check_running_when_its_task_s_program_ends_not_at_rest_is_ended_and_its_round_kept(ws):
    m, rid, got, t = checked(ws, ["sleep 30", "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    th = running(ws, t)
    pid = t["running"]["checking"]["pid"]
    _activity(ws, got["session"], "working")
    ends(ws)
    began = time.monotonic()
    look(ws)
    th.join(20)
    assert not th.is_alive() and time.monotonic() - began < 15 and gone(pid)
    assert t["checks"]["results"] == [{"id": 1, "outcome": "not run", "why": cc.ENDED},
                                      {"id": 2, "outcome": "not run", "why": cc.ENDED}]
    assert t["state"] == "failed" and "running" not in t


def test_a_check_running_when_its_session_s_record_is_unreadable_with_its_program_gone_is_ended(ws):
    m, rid, got, t = checked(ws, ["sleep 30", "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    th = running(ws, t)
    pid = t["running"]["checking"]["pid"]
    ends(ws)
    with open(os.path.join(ws._dir(got["session"]), "session.json"), "w") as fh:
        fh.write("not a record")                               # its programs made it unreadable
    began = time.monotonic()
    look(ws)
    th.join(20)
    assert not th.is_alive() and time.monotonic() - began < 15 and gone(pid)
    assert t["checks"]["results"] == [{"id": 1, "outcome": "not run", "why": cc.ENDED},
                                      {"id": 2, "outcome": "not run", "why": cc.ENDED}]
    assert t["state"] == "failed" and "running" not in t


@pytest.mark.parametrize("by", ["its group", "its session"])
def test_a_check_a_restart_left_running_that_carries_no_mark_is_ended_by_the_start_kept(ws, monkeypatch, by):
    if by == "its session" and sys.platform != "darwin":
        pytest.skip("a session is owned on a Mac alone (processes.own_kept)")
    m, rid, got, t = checked(ws, ["sleep 30"])
    head = commit(ws, got)
    folder = tempfile.mkdtemp(dir=ws.controller._checks_dir())
    mark = f"ctl-check-{t['id'][:12]}-1-1"
    env = {k: v for k, v in cc.check_env(mark).items() if k != processes.MARK}
    p = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], env=env, start_new_session=True)
    begun = processes._started(p.pid)
    assert isinstance(begun, str)
    with ws.controller._lock:
        t["running"] = {"kind": "rest", "head": head, "folder": folder, "results": [], "ended": None,
                        "checking": {"mark": mark, "number": 1, "pid": p.pid, "start": begun}}
        ws.controller._save()
    if by == "its group":
        monkeypatch.setattr(processes, "own_kept", lambda *args: False)
    else:
        monkeypatch.setattr(cc, "kill_group", lambda pid: None)
    again = ctl.Controller(ws)
    again.tick()
    try:
        assert p.wait(10) is not None
    finally:
        with contextlib.suppress(OSError):
            os.killpg(p.pid, 9)


def test_a_round_kept_while_a_fresh_chat_is_made_is_told_to_it_after_its_packet(ws, monkeypatch):
    b64, digest = packet()
    m, rid, got, t = checked(ws, ["exit 1"], packet_b64=b64, packet_digest=digest)
    rest(ws, got)
    ws.controller.refresh()                                    # idle: the chat is to be told, and the packet holds it
    read_through(ws)
    m = side(ws, m, rid)
    before, sent = set(ws.controller.wakes), []
    assert t["id"] in before

    def make(given, sid):
        with ws.controller._lock:                              # the checks' thread keeps a round meanwhile
            ws.controller._keep_round(t, "a" * 40, [cc.outcome(1, 1, b"")])

    async def send(sid, text):
        sent.append(text)
    monkeypatch.setattr(ws.controller, "_make_chat", make)
    monkeypatch.setattr(ws.controller, "cannot_run", lambda given: None)
    monkeypatch.setattr(ws, "chat_send", send)
    asyncio.run(ws.controller.keep_chat())
    assert len(sent) == 1                                      # the brief and the packet, which held what came before
    assert set(ws.controller.wakes) == {f"checks:{t['id']}:1"}


def test_a_checks_key_a_session_writes_into_its_own_record_is_in_no_answer(ws):
    m, rid, got, t = checked(ws, ["exit 1"])
    sid = got["session"]
    fake = {"round": 9, "head": "a" * 40, "passed": True,
            "results": [{"id": 1, "outcome": "passed", "output": {"text": "FAKE-OUTPUT", "by": "program"}}]}
    ws._update(sid, lambda record: record.__setitem__("checks", fake))
    listed = agent_call(ws, "GET", "/sessions")
    renamed = agent_call(ws, "POST", f"/sessions/{sid}/title", json={"title": "Renamed"})
    own = agent_call(ws, "GET", f"/sessions/{sid}")
    assert all("checks" not in one for one in listed["sessions"]) and "checks" not in renamed
    assert own["checks"] is None                               # no round of the controller's yet
    assert "FAKE-OUTPUT" not in json.dumps(listed) + json.dumps(renamed) + json.dumps(own)


# ---- a command's exit and time, a failed checkout and the checks' thread ---------------------------------------------

def test_a_command_whose_exit_cannot_be_read_is_not_run_and_its_round_judges_nothing(ws, monkeypatch):
    real = cc.exited
    monkeypatch.setattr(cc, "exited", lambda p: cc.LOST if real(p) is not None else None)    # another wait took it
    m, rid, got, t = checked(ws, ["exit 0"])
    head = commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["checks"]["results"] == [{"id": 1, "outcome": "not run", "why": cc.NO_EXIT}]
    assert head not in (t.get("judged") or {}) and "passed" not in t


def test_a_checkout_that_fails_is_removed_before_the_next_is_made(ws, monkeypatch):
    real, made = cc.make_checkout, []

    def checkout(clone, head, folder, timeout):
        made.append(folder)
        if len(made) == 1:                                     # git leaves part of a checkout, and fails
            os.makedirs(folder)
            with open(os.path.join(folder, "part"), "w") as fh:
                fh.write("x")
            raise cc.Unrun(cc.NO_CHECKOUT)
        return real(clone, head, folder, timeout)
    monkeypatch.setattr(cc, "make_checkout", checkout)
    m, rid, got, t = checked(ws, ["exit 0", "test $(ls -d ../../check-* | wc -l) -eq 1"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["checks"]["results"][0] == {"id": 1, "outcome": "not run", "why": cc.NO_CHECKOUT}
    assert outcomes(t) == ["not run", "passed"]


def test_a_command_s_time_is_counted_from_its_start_not_from_ps_s_answers(ws, monkeypatch):
    real_start, real_read, real_started, seen = cc.start_command, cc.read_output, processes._started, {}

    def start(*args):
        seen["before"] = time.monotonic()
        return real_start(*args)

    def started(pid):
        if "before" in seen:                                   # ps asked about the command once it started (the
            seen.setdefault("ps", time.monotonic())            # task's own start asks ps too, before any round)
        return real_started(pid)

    def read(p, seconds=None, began=None):
        seen["began"] = began
        return real_read(p, seconds, began)
    monkeypatch.setattr(cc, "start_command", start)
    monkeypatch.setattr(processes, "_started", started)
    monkeypatch.setattr(cc, "read_output", read)
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert outcomes(t) == ["passed"]
    assert seen["began"] is not None and seen["before"] <= seen["began"] <= seen["ps"]


def test_a_round_after_one_the_workspace_could_not_start_waits_before_it_starts(ws, monkeypatch):
    def refused(*args, **kwargs):
        raise cc.Unrun(cc.NO_CHECKOUT)
    real = cc.make_clone
    monkeypatch.setattr(cc, "make_clone", refused)
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert (t["unstarted"], t.get("checks")) == (1, None)
    monkeypatch.setattr(cc, "make_clone", real)                # the failure passes
    look(ws)
    ws.controller.run_rounds()
    assert (t["unstarted"], t["queued"]["kind"], t.get("checks")) == (1, "rest", None)    # it waits
    now = ctl._now()
    monkeypatch.setattr(ctl, "_now", lambda: now + ctl.UNSTARTED_WAIT)
    ws.controller.run_rounds()
    assert (outcomes(t), "unstarted" in t, "unstarted_at" in t) == (["passed"], False, False)


def test_an_end_round_whose_branch_is_gone_decides_nothing_once_the_agent_s_stop_has_begun(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    m = side(ws, m, rid, state="stopped")
    ends(ws)
    look(ws)
    assert t["queued"]["kind"] == "end"
    git("checkout", "-q", "--detach", cwd=ws._work(got["session"]))
    git("branch", "-D", f"pp/{got['session']}", cwd=os.path.join(ws.repos_dir, "demo"))
    c = ws.controller
    with c._lock:
        c._shutting = True                                     # the agent's stop has begun; the thread's stop is not set
    c._run_round(t["id"])
    assert t["state"] == "idle" and "why" not in t


def test_the_checks_thread_is_started_under_the_record_s_lock_so_that_a_stop_meanwhile_starts_none(ws):
    c = ws.controller
    with c._lock:
        th = threading.Thread(target=c.start_checks)
        th.start()
        th.join(0.5)
        assert th.is_alive() and c._checks_thread is None      # it waits for the lock
        c._shutting = True                                     # the agent's stop sets its mark meanwhile
    th.join(5)
    assert not th.is_alive() and c._checks_thread is None


def test_a_tail_is_measured_as_the_record_s_json_escapes_it(ws, monkeypatch):
    monkeypatch.setattr(ctl, "TAILS_KEPT", 40)                 # five e-acutes take 32 bytes as JSON, escaped; 7 raw
    t = {"checked_seq": 1, "checks": {"results": [{"id": n, "outcome": "passed", "tail": "\u00e9" * 5}
                                                  for n in (1, 2)]}}
    with ws.controller._lock:
        ws.controller.record["tasks"] = {"one": t}
        ws.controller._bound_tails()
    assert [(r.get("tail"), r.get("tail_dropped")) for r in t["checks"]["results"]] == [
        ("\u00e9" * 5, None), (None, True)]


# ---- the head a finish keeps, and a passed task's end ----------------------------------------------------------------

def test_a_fresh_chat_is_primed_from_the_side_its_wait_tested_where_a_side_comes_during_its_awaits(ws, monkeypatch):
    b64, digest = packet()
    m, rid, got, t = checked(ws, ["exit 1"], packet_b64=b64, packet_digest=digest)
    read_through(ws)
    side(ws, m, rid)
    newer_b64, newer_digest = packet(note="the side sent while the chat waited")
    sent = []

    def cannot_run(given):
        # while keep_chat awaits this: a round is kept, and the app reads past it and sends a newer side, which _take
        # keeps with its read position in one hold
        c = ws.controller
        with c._lock:
            c._keep_round(t, "a" * 40, [cc.outcome(1, 1, b"")])
            c.record["mirror"] = dict(c.record["mirror"], packet_b64=newer_b64, packet_digest=newer_digest,
                                      version=c.record["mirror"]["version"] + 1)
            c.record["mirror_acked"] = c.record["seq"]
        return None

    async def send(sid, text):
        sent.append(text)
    monkeypatch.setattr(ws.controller, "cannot_run", cannot_run)
    monkeypatch.setattr(ws.controller, "_make_chat", lambda given, sid: None)
    monkeypatch.setattr(ws, "chat_send", send)
    asyncio.run(ws.controller.keep_chat())
    assert len(sent) == 1 and "the side sent while the chat waited" in sent[0]


def test_a_finish_between_its_program_s_end_and_the_loop_s_look_keeps_the_head_its_program_ended_on(ws):
    m, rid, got, t = checked(ws, ["test ! -f notes.txt"])      # a check the owner's Finish commit would fail
    head = commit(ws, got)
    rest(ws, got)
    ends(ws)                                                   # its program ends at rest; the loop has not looked
    with open(os.path.join(ws._work(got["session"]), "notes.txt"), "w") as fh:
        fh.write("left uncommitted\n")
    ws.finish(got["session"], "Owner", "owner@example.com")
    assert git("rev-parse", "HEAD", cwd=ws._work(got["session"]))[1].strip() != head
    assert (t["state"], t["ended_head"]) == ("idle", head)
    look(ws)
    ws.controller.run_rounds()
    assert (t["state"], t["checks"]["head"], t["checks"]["passed"], t.get("why")) == ("done", head, True, None)


def test_read_tree_builds_each_task_s_round_under_the_record_s_lock(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    real, held = ctl._round_shown, []

    def shown(rnd):
        held.append(ws.controller._lock._is_owned())          # a round kept in another thread lets go of tails under it
        return real(rnd)
    monkeypatch.setattr(ctl, "_round_shown", shown)
    item = next(r for r in ws.controller.read_tree()["requirements"] if r["id"] == rid)
    assert held and all(held) and item["tasks"][0]["checks"]["passed"] is True


def test_a_check_s_own_git_writing_a_commit_and_a_stash_changes_nothing_a_later_check_needs(ws):
    who = "-c user.name=t -c user.email=t@t"
    first = (f"printf one > a.txt && git add -A && git {who} commit -q -m drift && printf two > b.txt && "
             f"git add b.txt && git {who} stash -q && test -n \"$(git stash list)\"")
    m, rid, got, t = checked(ws, [first, "exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert outcomes(t) == ["passed", "passed"]


def test_a_passed_task_whose_program_commits_as_it_stops_is_left_to_its_end_at_rest(ws):
    m, rid, got, t = checked(ws, ["test ! -f late.txt"])
    head = commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["passed"] == head
    term = _Term.seen[-1]
    real_stop = term.stop

    async def stop():                                          # its program commits as it stops
        commit(ws, got, {"late.txt": "late\n"})
        await real_stop()
    term.stop = stop
    asyncio.run(ws.controller._end_passed())
    assert t["state"] == "idle" and "passed" not in t
    look(ws)
    ws.controller.run_rounds()
    assert (t["state"], t["checks"]["passed"]) == ("failed", False)


def test_a_passed_task_whose_head_cannot_be_read_after_its_stop_is_not_done(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    head = commit(ws, got)
    rest(ws, got)
    look(ws)
    ws.controller.run_rounds()
    assert t["passed"] == head
    term = _Term.seen[-1]
    real_stop = term.stop

    def held(path, sid):
        raise wsmod.GitHeld("git's places changed")

    async def stop():                                          # once its program is gone, git cannot read the head
        await real_stop()
        monkeypatch.setattr(cc, "read_head", held)
    term.stop = stop
    asyncio.run(ws.controller._end_passed())
    assert (t["state"], "passed" in t) == ("idle", False)


def test_an_update_between_its_program_s_end_and_the_loop_s_look_keeps_the_head_its_program_ended_on(ws):
    m, rid, got, t = checked(ws, ["test ! -f upstream.txt"])  # a check the merged branch would fail
    head = commit(ws, got)
    rest(ws, got)
    ends(ws)
    repo = os.path.join(ws.repos_dir, "demo")                 # what the session follows moves on
    with open(os.path.join(repo, "upstream.txt"), "w") as fh:
        fh.write("upstream\n")
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "upstream", cwd=repo)
    ws.update(got["session"], "Owner", "owner@example.com")
    assert git("rev-parse", "HEAD", cwd=ws._work(got["session"]))[1].strip() != head
    assert (t["state"], t["ended_head"]) == ("idle", head)
    look(ws)
    ws.controller.run_rounds()
    assert (t["state"], t["checks"]["head"], t["checks"]["passed"]) == ("done", head, True)


def test_a_finish_while_its_program_runs_keeps_no_head(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)                                                   # its program runs, at rest: it can still commit
    with open(os.path.join(ws._work(got["session"]), "notes.txt"), "w") as fh:
        fh.write("left uncommitted\n")
    ws.finish(got["session"], "Owner", "owner@example.com")
    assert "ended_head" not in t and _Term.seen[-1].running


def test_a_finish_whose_program_ends_while_its_head_is_read_keeps_no_head(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)                                                   # its program runs, at rest: it can still commit
    assert _Term.seen[-1].running and "ended_head" not in t
    real, steps = cc.read_head, []

    def read(path, sid):
        head = real(path, sid)
        steps.append("read")
        commit(ws, got, {"late.txt": "late\n"})                # the program commits after the read,
        steps.append("commit")
        ends(ws)                                               # and then ends
        steps.append("end")
        return head
    monkeypatch.setattr(cc, "read_head", read)
    ws.finish(got["session"], "Owner", "owner@example.com")
    assert "ended_head" not in t, steps


def test_a_head_a_finish_keeps_is_saved_at_once_and_a_restart_reads_it(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    head = commit(ws, got)
    rest(ws, got)
    ends(ws)                                                   # its program ends at rest; the loop has not looked
    ws.finish(got["session"], "Owner", "owner@example.com")
    again = ctl.Controller(ws)                                 # made again before any look, page, side or start saves
    assert again.record["tasks"][t["id"]].get("ended_head") == head


@pytest.mark.parametrize("word", ["working", "waiting"])
def test_a_finish_after_its_program_ended_not_at_rest_keeps_no_head(ws, word):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    _activity(ws, got["session"], word)                        # its hooks last said it was working, or waiting on a
    ends(ws)                                                   # question to its owner mid-turn (no kind)
    ws.finish(got["session"], "Owner", "owner@example.com")
    assert "ended_head" not in t


def test_a_finish_whose_branch_head_is_a_commit_of_the_owner_s_finish_keeps_no_head(ws):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    look(ws)                                                   # its program runs, at rest
    with open(os.path.join(ws._work(got["session"]), "notes.txt"), "w") as fh:
        fh.write("left uncommitted\n")
    ws.finish(got["session"], "Owner", "owner@example.com")    # keeps no head, and commits the file
    repo = os.path.join(ws.repos_dir, "demo")
    assert cc.finish_commit(repo, cc.read_head(repo, got["session"]), got["session"]) and "ended_head" not in t
    ends(ws)                                                   # its program ends at rest; the loop has not looked
    ws.finish(got["session"], "Owner", "owner@example.com")
    assert "ended_head" not in t


def test_a_finish_whose_head_s_commit_cannot_be_read_keeps_no_head(ws, monkeypatch):
    m, rid, got, t = checked(ws, ["exit 0"])
    commit(ws, got)
    rest(ws, got)
    ends(ws)                                                   # its program ends at rest; the loop has not looked

    def unread(path, head, sid):
        raise wsmod.GitHeld("git's places changed")
    monkeypatch.setattr(cc, "finish_commit", unread)
    ws.finish(got["session"], "Owner", "owner@example.com")
    assert "ended_head" not in t
