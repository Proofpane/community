"""While the workspace is paired
with the owner's Proofpane organisation, the workspace agent itself posts one usage row per reply of its claude, codex
and chat sessions, its agents' runs and its tasks' attempts, read from their transcripts, and one row per one-turn chat
from the Agent SDK's result; Claude's as `mcp.subscription.usage`, Codex's as `codex.session.turn`, each with its
request id at the top and what the workspace knows of who and what made it (proofpane_workspace/usage_report.py).
Proofpane is a stand-in here (proofpane_stand_in.py), every program a stand-in too; no real claude, codex or
Proofpane, and nothing beyond this machine."""

import asyncio
import json
import os
import re
import sys
import time
import uuid

import pytest

from conftest import ROOT, manual_or_skip
from proofpane_stand_in import DEVICE, TOKEN, Proofpane, posted, proofpane  # noqa: F401  (proofpane is a fixture)
from proofpane_workspace import chat as chats, task_shapes
from workspace_fixtures import Agent, make_repo

USAGE_EVENTS = ("mcp.subscription.usage", "codex.session.turn")
# the reporter's waits, made short for the tests (a deployment's are seconds): the quiet before a session is read, and
# the wait before a refused post is tried again
QUICK = {"PP_WORKSPACE_USAGE_QUIET": "0.3", "PP_WORKSPACE_USAGE_RETRY": "0.4"}
USAGE = {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 40}
SAID = "the reply's own words, which never leave the workspace"
# a terminal program as these tests run it: claude's version where it is asked, then a line of output each time the
# test drops its session's nudge file in the home, so that the terminal has activity once the test has written its files
TERMINAL = '''import os, sys, time
if sys.argv[1:2] in (["-v"], ["--version"]):
    print("2.1.274 (Claude Code)")
    sys.exit(0)
flag = os.path.join(os.environ["HOME"], "nudge-" + os.environ.get("PP_SESSION_ID", ""))
while True:
    if os.path.exists(flag):
        os.unlink(flag)
        print("again", flush=True)
    time.sleep(0.05)
'''


def iso(t):
    """A time as Claude Code and Codex write a line's: UTC, with milliseconds."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".250Z"


def pair(home, cloud, paired_at):
    """The workspace's pairing as core_plugin.pair writes it, made at `paired_at`."""
    folder = os.path.join(str(home), ".proofpane", "proofpane-core")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "config.json"), "w") as fh:
        json.dump({"cloud_url": cloud, "device_id": DEVICE, "token": TOKEN, "name": "ws",
                   "paired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(paired_at)), "holds": []}, fh)


def usage_rows():
    return [b for b in posted("/api/v1/mcp/event") if b.get("event_type") in USAGE_EVENTS]


def wait_rows(n, timeout=30):
    """The usage rows Proofpane was sent, once there are at least `n`."""
    deadline = time.time() + timeout
    while True:
        got = usage_rows()
        if len(got) >= n:
            return got
        assert time.time() < deadline, f"{len(got)} usage rows reached Proofpane, not {n}: {got}"
        time.sleep(0.05)


def project(home, work, claude=".claude"):
    """Where Claude Code keeps the transcripts of a claude that ran in `work`."""
    return os.path.join(str(home), claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(work)))


def reply(cwd, mid, rid, at, conversation="conv-1", usage=USAGE, model="claude-test-1", text=SAID, copies=2):
    """One reply as Claude Code writes it: on `copies` lines (it streams), each with the reply's message id and request
    id and a line id (`uuid`) of its own."""
    return [{"type": "assistant", "uuid": str(uuid.uuid4()), "cwd": cwd, "sessionId": conversation, "requestId": rid,
             "timestamp": iso(at),
             "message": {"id": mid, "role": "assistant", "model": model, "usage": usage,
                         "content": [{"type": "text", "text": text},
                                     {"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                      "input": {"command": "echo sk-ant-" + "x" * 30}}]}}
            for _ in range(copies)]


def reads_move_access_times(folder):
    """Whether a read of a file just written moves its access time here (relatime or strictatime, not noatime): what
    tells, below, that nothing read a transcript."""
    probe = os.path.join(str(folder), "probe-atime")
    with open(probe, "w") as fh:
        fh.write("x\n")
    before = os.stat(probe).st_atime_ns
    with open(probe) as fh:
        fh.read()
    return os.stat(probe).st_atime_ns != before


def write_lines(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))


class Ws:
    """A workspace agent whose terminal programs are the TERMINAL stand-in, with a repository `demo`."""

    def __init__(self, tmp_path, programs=("claude",), env=None):
        self.bin = tmp_path / "bin"
        self.bin.mkdir(exist_ok=True)
        for program in programs:
            (self.bin / program).write_text(f"#!{sys.executable}\n" + TERMINAL)
            (self.bin / program).chmod(0o755)
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self.root = str(tmp_path / "ws")
        make_repo(os.path.join(self.root, "repos", "demo"))
        self.env = dict({"PATH": f"{self.bin}:{os.environ['PATH']}"}, **QUICK)
        self.env.update(env or {})
        self.start()

    def start(self):
        info = os.path.join(self.root, "agent.json")
        if os.path.exists(info):
            os.unlink(info)
        self.agent = Agent(self.root, str(self.home), self.env)
        self.http = self.agent.http

    def stop(self):
        self.agent.close()

    def session(self, program="claude", task="Look at the open issues"):
        r = self.http.post("/sessions", json={"repo": "demo", "program": program, "task": task})
        assert r.status_code == 200, r.text
        return r.json()

    def work(self, sid):
        return os.path.join(self.root, "sessions", sid, "work")

    def nudge(self, sid):
        (self.home / f"nudge-{sid}").write_text("")


# ---- a claude terminal's transcript ---------------------------------------------------------------------------------

def test_a_claude_terminal_s_replies_post_one_row_each_and_nothing_before_the_pairing_or_the_session(tmp_path,
                                                                                                    proofpane):
    now = time.time()
    w = Ws(tmp_path)
    try:
        pair(w.home, proofpane, now - 3600)
        s = w.session()
        sid, born, work = s["id"], s["created_at"], w.work(s["id"])
        # the status line's windows, as a subscriber's replies carry them (statusline.py)
        with open(os.path.join(w.root, "sessions", sid, "status.json"), "w") as fh:
            json.dump({"at": int(now), "five_hour": {"used_percentage": 12, "resets_at": int(now) + 3600}}, fh)
        cwd = os.path.realpath(work)
        folder = project(w.home, work)
        write_lines(os.path.join(folder, "conv-1.jsonl"),
                    reply(cwd, "msg_old_0", "req_old_0", now - 7200)             # before the pairing
                    + reply(cwd, "msg_old_1", "req_old_1", now - 1800)           # paired, but before the session
                    + reply(cwd, "msg_1", "req_1", born + 5)
                    + [{"type": "user", "cwd": cwd, "timestamp": iso(born + 5),
                        "message": {"role": "user", "content": "go on"}}]
                    + reply(cwd, "msg_2", "req_2", born + 6, usage=dict(USAGE, output_tokens=8))
                    + reply(cwd, "msg_3", "req_3", born + 7, model="claude-test-2")
                    + reply(cwd, "msg_syn", "req_syn", born + 8, model="<synthetic>", usage={
                        "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0})
                    + reply(str(tmp_path / "elsewhere"), "msg_else", "req_else", born + 9))
        # a sub-agent's replies, in its own file beside the conversation's (transcripts.py)
        write_lines(os.path.join(folder, "conv-1", "subagents", "agent-a1.jsonl"),
                    reply(cwd, "msg_sub", "req_sub", born + 10))
        w.nudge(sid)
        rows = wait_rows(4)
        time.sleep(1.5)
        rows = usage_rows()
        assert sorted(r["payload"]["request_id"] for r in rows) == ["req_1", "req_2", "req_3", "req_sub"], rows
        for r in rows:
            assert r["event_type"] == "mcp.subscription.usage"
            assert (r["device_id"], r["token"], r["vendor"], r["agent"]) == (DEVICE, TOKEN, "ade", "default")
            p = r["payload"]
            assert (p["session_id"], p["ade_kind"], p["ade_started_by"], p["client"], p["claude_account"],
                    p["ade_sign_in"]) == (sid, "terminal", "person", "claude_code", "default", "subscription"), p
            assert not {"ade_agent_id", "ade_task_id", "ade_run_id", "ade_trigger_id", "turn_id"} & set(p), p
            (record,) = p["records"]
            assert p["count"] == 1 and record["request_id"] == p["request_id"] and record["session_id"] == sid
            assert {k: record[k] for k in USAGE} == {k: p["totals"][k] for k in USAGE}
        by = {r["payload"]["request_id"]: r["payload"]["records"][0] for r in rows}
        assert by["req_2"] == {"request_id": "req_2", "session_id": sid, "model": "claude-test-1", "input_tokens": 11,
                               "output_tokens": 8, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 40,
                               "ts": iso(born + 6)}
        assert by["req_3"]["model"] == "claude-test-2" and by["req_sub"]["ts"] == iso(born + 10)
        # the counts, the ids, the model and the times: never a word of the reply, nor what it ran
        sent = json.dumps([a for a in Proofpane.asked])
        assert "reply's own words" not in sent and "sk-ant-" not in sent and "echo" not in sent
    finally:
        w.stop()


def test_a_reply_made_after_the_session_began_but_before_the_pairing_is_not_posted(tmp_path, proofpane):
    w = Ws(tmp_path)
    try:
        s = w.session()
        sid, born, work = s["id"], s["created_at"], w.work(s["id"])
        time.sleep(2.2)
        paired = time.time()
        pair(w.home, proofpane, paired)               # paired two seconds after the session began
        cwd = os.path.realpath(work)
        write_lines(os.path.join(project(w.home, work), "conv-1.jsonl"),
                    reply(cwd, "msg_unpaired", "req_unpaired", born + 1) + reply(cwd, "msg_paired", "req_paired",
                                                                                  paired + 1))
        w.nudge(sid)
        wait_rows(1)
        time.sleep(1.5)
        assert [r["payload"]["request_id"] for r in usage_rows()] == ["req_paired"]
    finally:
        w.stop()


def test_read_again_and_after_the_agent_starts_again_no_reply_is_posted_twice(tmp_path, proofpane):
    now = time.time()
    w = Ws(tmp_path)
    try:
        pair(w.home, proofpane, now - 3600)
        s = w.session()
        sid, born, work = s["id"], s["created_at"], w.work(s["id"])
        cwd = os.path.realpath(work)
        path = os.path.join(project(w.home, work), "conv-1.jsonl")
        write_lines(path, reply(cwd, "msg_1", "req_1", born + 5) + reply(cwd, "msg_2", "req_2", born + 6))
        w.nudge(sid)
        wait_rows(2)
        # read again: the session's activity, and the same lines once more (a reply streamed on another line)
        write_lines(path, reply(cwd, "msg_1", "req_1", born + 5, copies=1))
        w.nudge(sid)
        time.sleep(1.5)
        assert sorted(r["payload"]["request_id"] for r in usage_rows()) == ["req_1", "req_2"]
        # the agent started again, with one reply more meanwhile: that one alone is posted
        w.stop()
        write_lines(path, reply(cwd, "msg_3", "req_3", born + 7))
        w.start()
        wait_rows(3)
        time.sleep(1.5)
        assert sorted(r["payload"]["request_id"] for r in usage_rows()) == ["req_1", "req_2", "req_3"]
    finally:
        w.stop()


# ---- a refused post -------------------------------------------------------------------------------------------------

def test_a_refused_post_is_tried_three_times_then_dropped_and_the_drop_is_recorded(tmp_path, proofpane):
    # the design lists this among the usage report's controls
    now = time.time()
    Proofpane.revoked = True                 # every row refused: 401, as Proofpane refuses a revoked device
    w = Ws(tmp_path)
    try:
        pair(w.home, proofpane, now - 3600)
        s = w.session()
        sid, born, work = s["id"], s["created_at"], w.work(s["id"])
        cwd = os.path.realpath(work)
        path = os.path.join(project(w.home, work), "conv-1.jsonl")
        write_lines(path, reply(cwd, "msg_refused", "req_refused", born + 5))
        w.nudge(sid)

        def tries(rid):
            return [r for r in usage_rows() if r["payload"]["request_id"] == rid]
        deadline = time.time() + 20
        while len(tries("req_refused")) < 3:
            assert time.time() < deadline, usage_rows()
            time.sleep(0.05)
        time.sleep(2.5)                       # several of the reporter's waits: no fourth try, ever
        w.nudge(sid)
        time.sleep(1.5)
        assert len(tries("req_refused")) == 3
        got = w.http.get("/proofpane").json()
        assert TOKEN not in json.dumps(got)
        kept = got.get("usage") or {}
        assert kept.get("dropped") == 1 and kept.get("posted") == 0, got
        (drop,) = kept["drops"]
        assert (drop["request_id"], drop["session_id"], drop["tries"], drop["event_type"]) == \
            ("req_refused", sid, 3, "mcp.subscription.usage"), drop
        assert "401" in drop["why"]
        # Proofpane takes rows again: a new reply is posted, and the dropped one is not tried again
        Proofpane.revoked = False
        write_lines(path, reply(cwd, "msg_next", "req_next", born + 6))
        w.nudge(sid)
        deadline = time.time() + 20
        while not tries("req_next"):
            assert time.time() < deadline, usage_rows()
            time.sleep(0.05)
        time.sleep(1.5)
        assert len(tries("req_refused")) == 3 and len(tries("req_next")) == 1
        assert w.http.get("/proofpane").json()["usage"]["posted"] == 1
    finally:
        w.stop()


def test_a_refused_reply_s_tries_are_counted_across_the_agent_s_restart(tmp_path, proofpane):
    now = time.time()
    Proofpane.revoked = True
    w = Ws(tmp_path, env={"PP_WORKSPACE_USAGE_RETRY": "10"})      # a try, then the next ten seconds on
    try:
        pair(w.home, proofpane, now - 3600)
        s = w.session()
        work = w.work(s["id"])
        write_lines(os.path.join(project(w.home, work), "conv-1.jsonl"),
                    reply(os.path.realpath(work), "msg_refused", "req_refused", s["created_at"] + 5))
        w.nudge(s["id"])
        deadline = time.time() + 30
        while len(usage_rows()) < 2:
            assert time.time() < deadline, usage_rows()
            time.sleep(0.05)
        w.stop()                              # two tries made; the agent starts again before the third
        w.start()
        deadline = time.time() + 8
        while (w.http.get("/proofpane").json().get("usage") or {}).get("dropped") != 1:
            assert time.time() < deadline, (usage_rows(), w.http.get("/proofpane").json())
            time.sleep(0.1)
        time.sleep(2)
        assert len(usage_rows()) == 3         # the third, at the start: three tries in all, then dropped
    finally:
        w.stop()


# ---- controls: another program, and a workspace not paired -----------------------------------------------------------

def test_control_a_session_of_another_program_posts_nothing(tmp_path, proofpane):
    now = time.time()
    w = Ws(tmp_path, programs=("kimi", "opencode"))
    try:
        pair(w.home, proofpane, now - 3600)
        for program in ("kimi", "opencode"):
            s = w.session(program)
            work = w.work(s["id"])
            # what a claude or a codex would leave for a session's worktree: not this program's, and not read for it
            write_lines(os.path.join(project(w.home, work), "conv-1.jsonl"),
                        reply(os.path.realpath(work), "msg_" + program, "req_" + program, s["created_at"] + 5))
            write_lines(os.path.join(str(w.home), ".codex", "sessions", *time.strftime("%Y/%m/%d", time.gmtime(now))
                                     .split("/"), f"rollout-x-{program}.jsonl"),
                        [{"timestamp": iso(now), "type": "session_meta",
                          "payload": {"id": "conv-" + program, "cwd": os.path.realpath(work)}},
                         {"timestamp": iso(s["created_at"] + 5), "type": "token_usage_record",
                          "payload": {"turn_id": "u", "response_id": "resp_" + program,
                                      "usage": {"input_tokens": 9, "output_tokens": 3}}}])
            w.nudge(s["id"])
        time.sleep(3)
        assert usage_rows() == []
    finally:
        w.stop()


def test_control_unpaired_nothing_is_read_posted_or_kept(tmp_path, proofpane):
    w = Ws(tmp_path)
    try:
        s = w.session()
        work = w.work(s["id"])
        path = os.path.join(project(w.home, work), "conv-1.jsonl")
        write_lines(path, reply(os.path.realpath(work), "msg_1", "req_1", s["created_at"] + 5))
        written = os.stat(path).st_atime_ns
        w.nudge(s["id"])
        time.sleep(3)
        assert Proofpane.asked == []
        assert not os.path.exists(os.path.join(str(w.home), ".proofpane"))
        if reads_move_access_times(tmp_path):
            assert os.stat(path).st_atime_ns == written          # the transcript was not read
    finally:
        w.stop()


def test_control_unpaired_after_the_agent_started_paired_nothing_is_read_or_posted(tmp_path, proofpane):
    now = time.time()
    home = tmp_path / "home"
    home.mkdir()
    pair(home, proofpane, now - 3600)
    w = Ws(tmp_path)
    try:
        time.sleep(1)
        assert w.http.delete("/proofpane/pair").json()["paired"] is False
        s = w.session()
        work = w.work(s["id"])
        path = os.path.join(project(w.home, work), "conv-1.jsonl")
        write_lines(path, reply(os.path.realpath(work), "msg_1", "req_1", s["created_at"] + 5))
        written = os.stat(path).st_atime_ns
        w.nudge(s["id"])
        time.sleep(3)
        assert usage_rows() == []
        if reads_move_access_times(tmp_path):
            assert os.stat(path).st_atime_ns == written
    finally:
        w.stop()


# ---- a codex terminal ------------------------------------------------------------------------------------------------

def test_a_codex_session_s_calls_are_posted_as_codex_session_turn(tmp_path, proofpane):
    now = time.time()
    w = Ws(tmp_path, programs=("codex",))
    try:
        pair(w.home, proofpane, now - 3600)
        s = w.session("codex")
        sid, born, work = s["id"], s["created_at"], w.work(s["id"])
        limits = {"primary": {"used_percent": 20.0, "window_minutes": 300, "resets_at": int(now) + 3600},
                  "secondary": {"used_percent": 3.0, "window_minutes": 10080, "resets_at": int(now) + 86400}}
        calls = (("resp_1", {"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 30,
                             "reasoning_output_tokens": 5, "total_tokens": 150}),
                 ("resp_2", {"input_tokens": 200, "cached_input_tokens": 150, "output_tokens": 9,
                             "reasoning_output_tokens": 0, "total_tokens": 209}))
        lines = [{"timestamp": iso(born), "type": "session_meta",
                  "payload": {"id": "conv-codex", "cwd": os.path.realpath(work), "cli_version": "0.163.0"}},
                 {"timestamp": iso(born + 1), "type": "turn_context", "payload": {"model": "gpt-5-codex"}},
                 {"timestamp": iso(born + 1), "type": "event_msg",
                  "payload": {"type": "user_message", "message": SAID}}]
        for n, (rid, used) in enumerate(calls):
            lines.append({"timestamp": iso(born + 2 + n), "type": "token_usage_record",
                          "payload": {"thread_id": "conv-codex", "turn_id": "turn-1", "response_id": rid,
                                      "usage": used}})
            lines.append({"timestamp": iso(born + 2 + n), "type": "event_msg",
                          "payload": {"type": "token_count", "info": {"total_token_usage": used,
                                                                       "last_token_usage": used},
                                      "rate_limits": limits}})
        day = time.strftime("%Y/%m/%d", time.gmtime(born))
        write_lines(os.path.join(str(w.home), ".codex", "sessions", *day.split("/"),
                                 f"rollout-{time.strftime('%Y-%m-%dT%H-%M-%S', time.gmtime(born))}-x.jsonl"), lines)
        w.nudge(sid)
        wait_rows(2)
        time.sleep(1.5)
        rows = usage_rows()
        assert [r["event_type"] for r in rows] == ["codex.session.turn"] * 2
        by = {r["payload"]["request_id"]: r["payload"] for r in rows}
        assert set(by) == {"resp_1", "resp_2"}
        assert by["resp_1"] == {"request_id": "resp_1", "session_id": sid, "ade_kind": "terminal",
                                "ade_started_by": "person", "ade_sign_in": "subscription", "client": "codex",
                                "model": "gpt-5-codex", "input_tokens": 120, "output_tokens": 30,
                                "cache_read_input_tokens": 20, "reasoning_tokens": 5, "ts": iso(born + 2)}
        assert (by["resp_2"]["input_tokens"], by["resp_2"]["cache_read_input_tokens"]) == (200, 150)
        assert SAID not in json.dumps(Proofpane.asked)
    finally:
        w.stop()


# ---- an agent's run, and a task's attempt: chats the plugin is not given -------------------------------------------

CHAT_STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
AGENT = "a1a2a3a4a5a6"
SCHEDULE = "5c5c5c5c5c5c"


def transcript_replies(folder):
    """{message id: request id} of every reply in the transcripts under `folder`, sub-agents' among them."""
    found = {}
    for top, _, names in os.walk(folder):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            for line in open(os.path.join(top, name)):
                row = json.loads(line)
                if row.get("type") == "assistant" and isinstance((row.get("message") or {}).get("usage"), dict):
                    found[row["message"]["id"]] = row["requestId"]
    return found


def test_an_agent_s_scheduled_run_s_replies_are_posted_while_the_run_has_no_plugin(tmp_path, proofpane):
    if not chats.available():
        pytest.skip("needs the Claude Agent SDK (requirements-dev.txt installs it)")
    now = time.time()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(CHAT_STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    pair(home, proofpane, now - 3600)
    root = str(tmp_path / "ws")
    make_repo(os.path.join(root, "repos", "demo"))
    a = Agent(root, str(home), dict({"PATH": f"{bin_dir}:{os.environ['PATH']}"}, **QUICK))
    try:
        r = a.http.put(f"/agents/{AGENT}", json={"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo",
                                                 "author": {"name": "Ana", "email": "ana@example.org"}})
        assert r.status_code == 200, r.text
        r = a.http.post(f"/agents/{AGENT}/runs", json={"trigger": {"kind": "schedule", "id": SCHEDULE,
                                                                    "prompt": "look at the open issues"}})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        deadline = time.time() + 60
        while True:
            (view,) = [v for v in a.http.get("/agents/runs?since=0").json()["runs"] if v["run_id"] == run_id]
            if view["state"] in ("done", "failed", "blocked", "stopped", "skipped"):
                break
            assert time.time() < deadline, view
            time.sleep(0.1)
        assert view["state"] == "done", view
        expected = transcript_replies(project(home, os.path.join(root, "agents", AGENT, "work")))
        assert expected
        rows = wait_rows(len(expected))
        time.sleep(1.5)
        rows = usage_rows()
        assert sorted(r["payload"]["request_id"] for r in rows) == sorted(expected.values())
        for r in rows:
            p = r["payload"]
            assert r["event_type"] == "mcp.subscription.usage"
            assert (p["session_id"], p["ade_kind"], p["ade_started_by"], p["ade_agent_id"], p["ade_run_id"],
                    p["ade_trigger_id"], p["client"], p["claude_account"]) == \
                (run_id, "agent_run", "schedule", AGENT, run_id, SCHEDULE, "claude_code", "default"), p
            assert "ade_task_id" not in p
        # and the run's claude was given nothing of Proofpane's core: its usage came from the workspace agent
        starts = [json.loads(line) for line in open(home / "stand-in-runs.jsonl")]
        assert starts and all("--plugin-dir" not in s["args"] for s in starts)
    finally:
        a.close()


def test_an_owner_s_chat_s_replies_are_posted_from_its_own_conversations(tmp_path, proofpane):
    if not chats.available():
        pytest.skip("needs the Claude Agent SDK (requirements-dev.txt installs it)")
    now = time.time()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(CHAT_STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    pair(home, proofpane, now - 3600)
    root = str(tmp_path / "ws")
    make_repo(os.path.join(root, "repos", "demo"))
    a = Agent(root, str(home), dict({"PATH": f"{bin_dir}:{os.environ['PATH']}"}, **QUICK))
    try:
        # the stand-in's plan's rate-limit event, then its words: a subscriber's chat
        r = a.http.post("/sessions", json={"repo": "demo", "program": "chat",
                                           "task": "ratelimit allowed_warning five_hour"})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        deadline = time.time() + 30
        while a.http.get(f"/sessions/{sid}/chat").json()["state"] != "idle":
            assert time.time() < deadline
            time.sleep(0.05)
        expected = transcript_replies(project(home, os.path.join(root, "sessions", sid, "work")))
        assert expected
        wait_rows(len(expected))
        time.sleep(1.5)
        rows = usage_rows()
        assert sorted(r["payload"]["request_id"] for r in rows) == sorted(expected.values())
        for r in rows:
            p = r["payload"]
            assert (p["session_id"], p["ade_kind"], p["ade_started_by"], p["client"], p["claude_account"],
                    p["ade_sign_in"]) == (sid, "chat", "person", "claude_code", "default", "subscription"), p
    finally:
        a.close()


def test_a_task_attempt_s_rows_carry_its_task_s_id_and_its_run_s_id_apart(tmp_path, proofpane):
    from test_task_attempt import AGENT_ID, TASK, TaskWorkspace, attempt, task_work
    now = time.time()
    home = tmp_path / "home"
    home.mkdir()
    pair(home, proofpane, now - 3600)
    tws = TaskWorkspace(tmp_path, env=QUICK)
    try:
        done = attempt(tws, "append line.txt=one ;; complete step one", check='test "$(cat line.txt)" = one')
        assert done["state"] == "done", done
        run_id = done["run_id"]
        expected = transcript_replies(project(home, task_work(tws)))
        assert expected
        rows = wait_rows(len(expected))
        time.sleep(1.5)
        rows = usage_rows()
        assert sorted(r["payload"]["request_id"] for r in rows) == sorted(expected.values())
        assert task_shapes.TASK_ID.fullmatch(TASK) and task_shapes.RUN_ID.fullmatch(run_id)
        for r in rows:
            p = r["payload"]
            assert (p["ade_kind"], p["ade_task_id"], p["ade_run_id"], p["ade_agent_id"], p["session_id"]) == \
                ("task_attempt", TASK, run_id, AGENT_ID, run_id), p
            # the task's own id, never a run's; the attempt's run id as its run's alone
            assert "ade_trigger_id" not in p and "ade_started_by" not in p
            assert [k for k, v in p.items() if k.startswith("ade_") and v == run_id] == ["ade_run_id"]
            assert [k for k, v in p.items() if v == TASK] == ["ade_task_id"]
    finally:
        tws.stop()


# ---- a one-turn chat: the business skills' review, which leaves no transcript ----------------------------------------

def test_a_skill_review_s_one_turn_is_posted_from_its_result_with_no_transcript(tmp_path, monkeypatch, proofpane):
    if not chats.available():
        pytest.skip("needs the Claude Agent SDK (requirements-dev.txt installs it)")
    import claude_agent_sdk
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    from proofpane_workspace.workspace import Workspace
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    pair(home, proofpane, time.time() - 3600)
    turn_usage = {"input_tokens": 1200, "output_tokens": 210, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}
    session = "5e55" + uuid.uuid4().hex[4:]

    async def fake_query(prompt, options):
        assert options.extra_args == {"no-session-persistence": None}
        yield AssistantMessage(content=[TextBlock(text='{"none": true}')], model="claude-review-1",
                               message_id="msg_review_1", usage=dict(turn_usage), session_id=session)
        yield ResultMessage(subtype="success", duration_ms=5, duration_api_ms=4, is_error=False, num_turns=1,
                            session_id=session, total_cost_usd=0.0123, usage=dict(turn_usage))

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(chats, "write_wrapper", lambda *a, **k: "/bin/true")
    ws = Workspace(str(tmp_path / "ws"))
    folder = os.path.join(ws.root, "skill-improve", "b" * 12, "review")
    try:
        said, cost, used = asyncio.run(ws.skill_improve._turn(folder, None, None, "review this skill"))
        assert (said, cost, used) == ('{"none": true}', 0.0123, "claude-review-1")
        (row,) = wait_rows(1, timeout=15)
        time.sleep(1)
    finally:
        reporter = getattr(ws, "usage_report", None)
        if reporter is not None:
            reporter.stop()
    assert len(usage_rows()) == 1
    p = row["payload"]
    assert row["event_type"] == "mcp.subscription.usage"
    assert (p["request_id"], p["session_id"], p["ade_kind"], p["client"], p["claude_account"], p["count"]) == \
        ("msg_review_1", session, "one_turn", "claude_code", "default", 1), p
    # beside the counts, the Agent SDK's own estimate of the turn
    assert p.get("ade_cost_est_usd") == 0.0123, p
    (record,) = p["records"]
    assert record == dict(turn_usage, request_id="msg_review_1", session_id=session, model="claude-review-1",
                          ts=record["ts"])
    assert not os.path.exists(project(home, folder))          # no transcript: the result was what was read
    assert "review this skill" not in json.dumps(Proofpane.asked) and '"none"' not in json.dumps(row)


# ---- the manual's numbers are the reporter's -------------------------------------------------------------------------

def test_the_manual_states_the_reporter_s_own_numbers():
    manual_or_skip()
    with open(os.path.join(ROOT, "MANUAL.md"), encoding="utf-8") as fh:
        readme = " ".join(fh.read().split())
    assert "proofpane_workspace/usage_report.py" in readme
    start = readme.index("Once paired, the workspace agent itself also reports the usage")
    part = readme[start:readme.index("Unpaired, nothing is read for it and nothing is posted.", start)]
    import importlib
    report = importlib.import_module("proofpane_workspace.usage_report")
    times = {1: "once", 2: "twice", 3: "three times", 4: "four times", 5: "five times"}
    assert f"no answer in {report.TIMEOUT:g} seconds" in part
    assert f"{report.RETRY_AFTER:g} seconds on at the soonest" in part
    assert f"{times[report.TRIES]} in all" in part
