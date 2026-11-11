"""The hosted workspace's usage: a session's tokens, read only from the transcripts its `claude`
wrote, each reply counted once, and nothing counted from another directory or read with a sign-in; a `codex`
session's, and the account's windows, from the conversation files Codex writes (with PP_TEST_CODEX naming a codex
program, from a real one's, against a stand-in model that answers with a subscriber's rate-limit headers)."""

import json
import os
import subprocess
import sys
import time

import pytest

from proofpane_workspace import usage
from test_workspace_agent import _session, agent  # noqa: F401  (agent is a fixture)
from workspace_fixtures import Agent, make_repo

HERE = os.path.dirname(os.path.abspath(__file__))


def line(cwd, mid=None, kind="assistant", **tokens):
    message = {"usage": dict(tokens)}
    if mid is not None:
        message["id"] = mid
    return json.dumps({"type": kind, "cwd": cwd, "message": message}) + "\n"


def write_transcript(home, worktree, name, text):
    folder = os.path.join(home, ".claude", "projects", usage.slug(os.path.realpath(worktree)))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def test_the_slug_is_the_path_with_every_other_character_a_dash():
    assert usage.slug("/Users/a.b/work_1") == "-Users-a-b-work-1"


def test_each_reply_is_counted_once_and_only_from_this_worktree(tmp_path):
    home, work = str(tmp_path / "home"), str(tmp_path / "ws" / "sessions" / "0123456789ab" / "work")
    os.makedirs(work)
    elsewhere = str(tmp_path / "elsewhere")
    write_transcript(home, work, "a.jsonl",
                     line(work, "m1", input_tokens=10, output_tokens=5, cache_read_input_tokens=100)
                     + line(work, "m1", input_tokens=10, output_tokens=7)                  # the same reply, again
                     + line(work, "m2", input_tokens=3, output_tokens=2, cache_creation_input_tokens=40)
                     + line(elsewhere, "m3", input_tokens=1000)                            # another directory
                     + line(work, "u1", kind="user", input_tokens=1000)                    # not a reply
                     + "not json\n" + "[1, 2]\n"
                     + line(work, "m4", input_tokens=-5, output_tokens=True))              # nothing sensible
    write_transcript(home, work, "b.jsonl", line(work, None, input_tokens=1) + line(work, None, input_tokens=2))
    got = usage.claude_usage(work, os.path.join(home, ".claude"))
    assert got == {"input_tokens": 16, "output_tokens": 7, "cache_creation_input_tokens": 40,
                   "cache_read_input_tokens": 100, "messages": 5, "transcripts": 2}


def test_no_transcript_is_no_usage(tmp_path):
    got = usage.claude_usage(str(tmp_path / "work"), str(tmp_path / "home" / ".claude"))
    assert got["messages"] == 0 and got["transcripts"] == 0 and got["input_tokens"] == 0


def test_the_bounds_hold(tmp_path, monkeypatch):
    home, work = str(tmp_path / "home"), str(tmp_path / "work")
    os.makedirs(work)
    for n in range(3):
        write_transcript(home, work, f"{n}.jsonl", line(work, f"m{n}", input_tokens=1))
    monkeypatch.setattr(usage, "MAX_FILES", 2)
    assert usage.claude_usage(work, os.path.join(home, ".claude"))["messages"] == 2
    monkeypatch.setattr(usage, "MAX_FILES", 200)
    monkeypatch.setattr(usage, "MAX_BYTES", 1)
    assert usage.claude_usage(work, os.path.join(home, ".claude"))["messages"] == 0


def test_a_session_of_another_program_has_no_usage_here(agent):  # noqa: F811
    s = _session(agent, program="shell")
    got = agent.http.get(f"/sessions/{s['id']}/usage").json()
    assert got["available"] is False and got["program"] == "shell" and "claude" in got["reason"]
    assert agent.http.get("/sessions/0123456789ab/usage").status_code == 404


# ---- codex ------------------------------------------------------------------------------------------------------

def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def write_rollout(codex_home, cwd, began, counts, name=None, calls=(), meta=None):
    """A conversation file as Codex 0.163.0 writes one: its meta first, then each call's own tokens (a
    token_usage_record for each of `calls`), and a token count for each (epoch, total, limits) in `counts`, between
    other lines. Returns its path."""
    day = time.strftime("%Y/%m/%d", time.gmtime(began))
    folder = os.path.join(codex_home, "sessions", *day.split("/"))
    os.makedirs(folder, exist_ok=True)
    name = name or f"rollout-{time.strftime('%Y-%m-%dT%H-%M-%S', time.gmtime(began))}-{began:x}.jsonl"
    lines = [{"timestamp": _iso(began), "type": "session_meta",
              "payload": dict({"id": "x", "cwd": cwd, "cli_version": "0.163.0"}, **(meta or {}))},
             {"timestamp": _iso(began), "type": "event_msg", "payload": {"type": "task_started"}}]
    for used in calls:
        lines.append({"timestamp": _iso(began), "type": "token_usage_record",
                      "payload": {"thread_id": "t", "turn_id": "u", "response_id": "r", "usage": used}})
    for at, total, limits in counts:
        lines.append({"timestamp": _iso(at), "type": "response_item", "payload": {"type": "message"}})
        lines.append({"timestamp": _iso(at), "type": "event_msg",
                      "payload": {"type": "token_count", "info": {"total_token_usage": total, "last_token_usage": total},
                                  "rate_limits": limits}})
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(x) + "\n" for x in lines))
    return path


def _limits(five, week, resets=1_790_000_000):
    return {"limit_id": "codex", "primary": {"used_percent": five, "window_minutes": 300, "resets_at": resets},
            "secondary": {"used_percent": week, "window_minutes": 10080, "resets_at": resets + 86400}, "plan_type": None}


def test_codex_s_tokens_are_each_conversation_s_last_total_and_the_windows_its_newest(tmp_path):
    home, work = str(tmp_path / "codex"), str(tmp_path / "work")
    os.makedirs(work)
    t = 1_790_000_000
    tokens = lambda i, o: {"input_tokens": i, "cached_input_tokens": 2, "output_tokens": o,  # noqa: E731
                           "reasoning_output_tokens": 1, "total_tokens": i + o}
    # two conversations in this worktree: each one's running total counts once, as its last count says
    write_rollout(home, work, t, [(t + 1, tokens(10, 5), _limits(10, 1)), (t + 2, tokens(30, 9), _limits(12, 2))])
    write_rollout(home, os.path.join(tmp_path, "work", ".", ""), t + 100, [(t + 101, tokens(100, 50), None)])
    # another directory's, its windows newer: not this session's
    write_rollout(home, str(tmp_path / "other"), t + 200, [(t + 201, tokens(999, 999), _limits(99, 99))])
    got = usage.codex_usage(work, home)
    assert got["conversations"] == 2
    assert (got["input_tokens"], got["output_tokens"], got["cached_input_tokens"]) == (130, 59, 4)
    assert got["status"] == {"at": t + 2, "five_hour": {"used_percentage": 12.0, "resets_at": 1_790_000_000},
                             "seven_day": {"used_percentage": 2.0, "resets_at": 1_790_086_400}}


def test_codex_without_windows_or_files_says_none_and_odd_lines_are_skipped(tmp_path):
    home, work = str(tmp_path / "codex"), str(tmp_path / "work")
    os.makedirs(work)
    assert usage.codex_usage(work, home) == dict(dict.fromkeys(usage.CODEX_FIELDS, 0), conversations=0, status=None)
    t = 1_790_000_000
    # an API key's answers carry no windows; a window of another length is not one the page names; junk is skipped
    write_rollout(home, work, t, [(t + 1, {"input_tokens": 7, "output_tokens": True}, None),
                                  (t + 2, {"input_tokens": 8}, {"primary": {"used_percent": 50, "window_minutes": 60}})])
    folder = os.path.join(home, "sessions", "2026", "01", "01")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "rollout-2026-01-01T00-00-00-bad.jsonl"), "w") as fh:
        fh.write("not json\n[1]\n")
    got = usage.codex_usage(work, home)
    assert got["conversations"] == 1 and got["input_tokens"] == 8 and got["output_tokens"] == 0
    assert got["status"] is None


def _tokens(i, o):
    return {"input_tokens": i, "cached_input_tokens": 0, "output_tokens": o, "reasoning_output_tokens": 0,
            "total_tokens": i + o}


def test_a_forked_conversation_s_calls_are_counted_once(tmp_path):
    # Codex 0.163.0: a fork's file starts its running total from its parent's; its calls are its own
    home, work = str(tmp_path / "codex"), str(tmp_path / "work")
    os.makedirs(work)
    t = 1_790_000_000
    write_rollout(home, work, t, [(t + 1, _tokens(10, 5), None), (t + 2, _tokens(20, 10), None)],
                  calls=[_tokens(10, 5), _tokens(10, 5)])
    write_rollout(home, work, t + 60, [(t + 61, _tokens(30, 15), _limits(5, 1))], calls=[_tokens(10, 5)],
                  meta={"forked_from_id": "x", "history_base": {"thread_id": "x", "end_byte_offset": 1}})
    got = usage.codex_usage(work, home)
    assert got["conversations"] == 2 and (got["input_tokens"], got["output_tokens"]) == (30, 15)
    assert got["status"]["five_hour"]["used_percentage"] == 5.0


def test_other_directories_files_do_not_crowd_out_this_session_s(tmp_path):
    home, work = str(tmp_path / "codex"), str(tmp_path / "work")
    os.makedirs(work)
    t = 1_790_000_000
    write_rollout(home, work, t, [], calls=[_tokens(100, 1)])
    for i in range(usage.MAX_FILES + 50):        # newer, and another directory's
        write_rollout(home, str(tmp_path / "other"), t + 10 + i, [], calls=[_tokens(1, 1)])
    big = write_rollout(home, str(tmp_path / "other"), t + 5000, [], calls=[_tokens(1, 1)])
    with open(big, "a", encoding="utf-8") as fh:  # and one larger than the whole budget
        fh.write("x" * 4096 + "\n")
    old = usage.MAX_BYTES
    try:
        usage.MAX_BYTES = 4096
        got = usage.codex_usage(work, home)
    finally:
        usage.MAX_BYTES = old
    assert got["conversations"] == 1 and got["input_tokens"] == 100


def test_a_file_that_is_not_a_regular_one_or_cannot_be_read_is_passed_over(tmp_path):
    import threading
    home, work = str(tmp_path / "codex"), str(tmp_path / "work")
    os.makedirs(work)
    t = 1_790_000_000
    write_rollout(home, work, t, [], calls=[_tokens(7, 1)])
    folder = os.path.dirname(write_rollout(home, work, t + 1, [], calls=[_tokens(1, 1)], name="rollout-z-real.jsonl"))
    os.mkfifo(os.path.join(folder, "rollout-z-fifo.jsonl"))                 # would block a reader
    os.symlink("/dev/zero", os.path.join(folder, "rollout-z-zero.jsonl"))    # would never end
    os.symlink(os.path.join(folder, "rollout-z-real.jsonl"), os.path.join(folder, "rollout-z-link.jsonl"))
    with open(os.path.join(folder, "rollout-z-nul.jsonl"), "w") as fh:     # a path no file can have
        fh.write(json.dumps({"type": "session_meta", "payload": {"cwd": work + "\0x"}}) + "\n")
    deep = write_rollout(home, work, t + 2, [], calls=[_tokens(2, 1)], name="rollout-z-deep.jsonl")
    with open(deep, "a") as fh:                                              # a line too deep to parse
        fh.write('{"type": "token_usage_record", "payload": ' + "[" * 100000 + "]" * 100000 + "}\n")
    out = {}
    worker = threading.Thread(target=lambda: out.update(got=usage.codex_usage(work, home)), daemon=True)
    worker.start()
    worker.join(20)
    assert not worker.is_alive(), "the reader was blocked"
    got = out["got"]
    assert got["conversations"] == 3 and got["input_tokens"] == 10             # 7, 1 and 2: the link counted once


def test_a_conversation_is_read_on_from_where_the_last_call_stopped(tmp_path):
    home, work = str(tmp_path / "codex"), str(tmp_path / "work")
    os.makedirs(work)
    t = 1_790_000_000
    path = write_rollout(home, work, t, [], calls=[_tokens(10, 1)])
    assert usage.codex_usage(work, home)["input_tokens"] == 10
    with open(path, "a", encoding="utf-8") as fh:                          # the conversation goes on
        fh.write(json.dumps({"type": "token_usage_record", "payload": {"usage": _tokens(5, 1)}}) + "\n")
        fh.write('{"type": "token_usage_record", "payload": {"usage": {"input_tokens": 99')   # half written
    assert usage.codex_usage(work, home)["input_tokens"] == 15
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("}}}\n")
    assert usage.codex_usage(work, home)["input_tokens"] == 114
    os.unlink(path)                                                          # replaced under its name
    write_rollout(home, work, t, [], calls=[_tokens(3, 1)], name=os.path.basename(path))
    assert usage.codex_usage(work, home)["input_tokens"] == 3


def test_only_the_days_from_the_session_s_on_are_looked_at(tmp_path):
    home, work = str(tmp_path / "codex"), str(tmp_path / "work")
    os.makedirs(work)
    t = 1_790_000_000
    write_rollout(home, work, t - 3 * 86400, [], calls=[_tokens(50, 1)])    # days before the session began
    write_rollout(home, work, t + 60, [], calls=[_tokens(5, 1)])
    assert usage.codex_usage(work, home, since=t)["input_tokens"] == 5
    assert usage.codex_usage(work, home)["input_tokens"] == 55


def test_claude_s_transcripts_that_are_not_regular_files_are_passed_over(tmp_path):
    home, work = str(tmp_path / "home"), str(tmp_path / "work")
    write_transcript(home, work, "a.jsonl", line(os.path.realpath(work), "m1", input_tokens=4))
    folder = os.path.join(home, ".claude", "projects", usage.slug(os.path.realpath(work)))
    os.mkfifo(os.path.join(folder, "b.jsonl"))
    assert usage.claude_usage(work, os.path.join(home, ".claude"))["input_tokens"] == 4


def test_a_codex_session_s_usage_is_served(tmp_path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\nexec /bin/sh\n")      # a stand-in codex: a shell
    os.chmod(bin_dir / "codex", 0o755)
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = _session(a, program="codex")
        work = os.path.realpath(tmp_path / "ws" / "sessions" / s["id"] / "work")
        t = int(time.time())
        write_rollout(str(home / ".codex"), work, t, [(t, {"input_tokens": 3, "output_tokens": 4}, _limits(42.5, 7))])
        got = a.http.get(f"/sessions/{s['id']}/usage").json()
        assert got["available"] is True and got["program"] == "codex" and got["conversations"] == 1
        assert (got["input_tokens"], got["output_tokens"]) == (3, 4)
        assert got["status"]["five_hour"]["used_percentage"] == 42.5 and got["status"]["seven_day"]["used_percentage"] == 7
    finally:
        a.close()


CODEX = os.environ.get("PP_TEST_CODEX")


@pytest.mark.skipif(not CODEX, reason="PP_TEST_CODEX names no codex program to run against a stand-in model")
def test_a_real_codex_s_own_files_give_its_tokens_and_the_account_s_windows(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    resets = int(time.time()) + 3600
    headers = {"x-codex-primary-used-percent": "42.5", "x-codex-primary-window-minutes": "300",
               "x-codex-primary-reset-at": str(resets), "x-codex-secondary-used-percent": "7",
               "x-codex-secondary-window-minutes": "10080", "x-codex-secondary-reset-at": str(resets + 86400)}
    standin = subprocess.Popen([sys.executable, os.path.join(HERE, "codex_stand_in.py")], stdout=subprocess.PIPE, text=True,
                               env={"PATH": os.environ["PATH"], "HOME": str(home),
                                    "STANDIN_LOG": str(tmp_path / "standin.log"), "STANDIN_COMMAND": "echo hi",
                                    "STANDIN_HEADERS": json.dumps(headers)})
    try:
        port = int(standin.stdout.readline())
        (home / "config.toml").write_text(
            f'model = "gpt-5"\nmodel_provider = "standin"\napproval_policy = "never"\nsandbox_mode = "danger-full-access"\n\n'
            f'[model_providers.standin]\nname = "standin"\nbase_url = "http://127.0.0.1:{port}/v1"\n'
            f'wire_api = "responses"\nenv_key = "STANDIN_KEY"\n')
        run = subprocess.run([CODEX, "exec", "--skip-git-repo-check", "go"], cwd=work, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=120,
                             env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home), "STANDIN_KEY": "x"})
    finally:
        standin.kill()
        standin.wait()
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    got = usage.codex_usage(str(work), str(home))
    # the stand-in answers twice (a command, then "done"), 10 in and 5 out each
    assert got["conversations"] == 1 and got["input_tokens"] == 20 and got["output_tokens"] == 10
    assert got["status"]["five_hour"] == {"used_percentage": 42.5, "resets_at": resets}
    assert got["status"]["seven_day"] == {"used_percentage": 7.0, "resets_at": resets + 86400}
    assert abs(got["status"]["at"] - time.time()) < 300


@pytest.mark.skipif(not CODEX, reason="PP_TEST_CODEX names no codex program to run against a stand-in model")
def test_a_real_codex_fork_is_counted_by_its_own_calls(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    log = tmp_path / "standin.log"
    standin = subprocess.Popen([sys.executable, os.path.join(HERE, "codex_stand_in.py")], stdout=subprocess.PIPE, text=True,
                               env={"PATH": os.environ["PATH"], "HOME": str(home), "STANDIN_LOG": str(log),
                                    "STANDIN_COMMAND": "echo hi"})
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home), "STANDIN_KEY": "x"}
    try:
        port = int(standin.stdout.readline())
        (home / "config.toml").write_text(
            f'model = "gpt-5"\nmodel_provider = "standin"\napproval_policy = "never"\nsandbox_mode = "danger-full-access"\n\n'
            f'[model_providers.standin]\nname = "standin"\nbase_url = "http://127.0.0.1:{port}/v1"\n'
            f'wire_api = "responses"\nenv_key = "STANDIN_KEY"\n')
        first = subprocess.run([CODEX, "exec", "--skip-git-repo-check", "go"], cwd=work, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=120, env=env)
        assert first.returncode == 0, first.stderr[-2000:]
        (parent,) = [n for _, _, names in os.walk(home / "sessions") for n in names]
        parent_id = parent[: -len(".jsonl")].split("-", 6)[-1]
        fork = subprocess.run([CODEX, "exec", "--skip-git-repo-check", "fork", parent_id, "again"], cwd=work,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, env=env)
        assert fork.returncode == 0, fork.stderr[-2000:]
    finally:
        standin.kill()
        standin.wait()
    calls = len(log.read_text().splitlines())        # one line for each request the stand-in answered
    got = usage.codex_usage(str(work), str(home))
    assert got["conversations"] == 2 and calls >= 3
    assert got["input_tokens"] == 10 * calls and got["output_tokens"] == 5 * calls
