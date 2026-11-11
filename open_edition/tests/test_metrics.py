"""A run's numbers at its end (metrics.py), from a transcript in the layout Claude Code 2.1.274 writes (each reply on
two lines with one message id, tool results on user lines, each line with its time), its chat log's results and asks,
and its record."""

import json
import os
import random

from proofpane_workspace import metrics
from proofpane_workspace.usage import slug

SESSION = "6e1c7a52-0d55-4c41-9a39-3a3b0f6d2a11"


def at(seconds):
    return f"2026-10-03T04:00:{seconds:06.3f}Z" if seconds < 60 else \
        f"2026-10-03T04:{int(seconds // 60):02d}:{seconds % 60:06.3f}Z"


def reply(t, mid, content, usage):
    row = {"type": "assistant", "timestamp": at(t), "requestId": "req_" + mid,
           "message": {"id": mid, "usage": usage, "content": content}}
    return [row, row]                                       # a streamed reply is written on two lines


def results(t, *pairs):
    return {"type": "user", "timestamp": at(t), "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": text} for tid, error, text in pairs]}}


def use(tid, name, **given):
    return {"type": "tool_use", "id": tid, "name": name, "input": given}


def usage(read, fresh=100, created=0):
    return {"input_tokens": fresh, "cache_read_input_tokens": read, "cache_creation_input_tokens": created,
            "output_tokens": 10}


def write(tmp_path, rows, chat):
    work, claude = tmp_path / "work", tmp_path / "claude"
    work.mkdir(exist_ok=True)
    folder = claude / "projects" / slug(os.path.realpath(work))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{SESSION}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "chat.jsonl").write_text("\n".join(json.dumps(e) for e in chat) + "\n")
    return str(work), str(claude), str(tmp_path / "chat.jsonl")


def conversation(work):
    """Twelve calls: three Reads at once (one of them refused), a Grep and a Glob that list paths, a Bash that fails,
    six more one after another (one failing); the cache cold on the first request only."""
    rows = [{"type": "user", "timestamp": at(0), "message": {"content": "Label the issues."}}]
    rows += reply(1, "msg_1", [use("r1", "Read", file_path=f"{work}/src/a.py"), use("r2", "Read", file_path=f"{work}/src/b.py"),
                               use("r3", "Read", file_path=f"{work}/docs/c.md")], usage(0, created=5000))
    rows.append(results(4, ("r1", False, "a"), ("r2", False, "b"), ("r3", True, "denied by the gate")))  # 1 to 4
    rows += reply(5, "msg_2", [use("g1", "Grep", pattern="label")], usage(5000))
    rows.append(results(7, ("g1", False, f"{work}/src/rules.py:12:label = 1\n{work}/src/a.py:3:labels")))  # 5 to 7
    rows += reply(8, "msg_3", [use("g2", "Glob", pattern="**/*.md")], usage(5000))
    rows.append(results(9, ("g2", False, f"{work}/docs/labels.md\n{work}/README.md")))                     # 8 to 9
    rows += reply(10, "msg_4", [use("b1", "Bash", command="npm test")], usage(5000))
    rows.append(results(20, ("b1", True, "1 failing")))                                                    # 10 to 20
    for n, t in enumerate(range(21, 33, 2)):
        rows += reply(t, f"msg_e{n}", [use(f"e{n}", "mcp__tracker__search_issues" if n < 2 else "Edit")], usage(5000))
        rows.append(results(t + 1, (f"e{n}", n == 5, "ok")))                                              # 6 x 1 s
    rows += reply(40, "msg_last", [{"type": "text", "text": "Labelled them, per src/rules.py and labels.md."}],
                  usage(5000))
    return rows


CHAT = [{"kind": "user", "time": 1_790_000_000},
        {"kind": "ask", "time": 1_790_000_010, "id": "q1"}, {"kind": "answered", "time": 1_790_000_013, "id": "q1",
                                                               "decision": "deny"},
        {"kind": "result", "duration_ms": 30_000, "api_ms": 12_000, "denials": [{"id": "r3", "name": "Read"}]},
        {"kind": "result", "duration_ms": 9_500, "api_ms": 4_500, "denials": []}]
RUN = {"queued_at": 1_790_000_000 - 30, "started_at": 1_790_000_000, "ended_at": 1_790_000_045, "waiting_s": 3,
       "dispatches": 2, "messages_sent": 1, "messages_received": 0, "questions": 1, "answered": 1, "owner_turns": 2,
       "trigger": {"kind": "dispatch"}}


def test_twelve_calls_three_at_once_are_counted_and_their_time_is_their_union(tmp_path):
    work = str(tmp_path / "work")
    w, c, chat = write(tmp_path, conversation(work), CHAT)
    got = metrics.read(w, c, SESSION, chat, RUN)
    assert (got["calls"], got["errors"], got["refused"]) == (12, 2, 1)
    assert got["tools"]["Read"]["calls"] == 3 and got["tools"]["Read"]["refused"] == 1 and got["tools"]["Read"]["errors"] == 0
    assert got["tools"]["Bash"]["errors"] == 1 and got["tools"]["Edit"]["errors"] == 1
    # the three reads at once count 1 to 4 once, not three times; then 5-7, 8-9, 10-20 and six of a second
    assert got["time"]["tool"] == 3 + 2 + 1 + 10 + 6
    assert got["tools"]["Read"]["p50_ms"] == 3000.0                     # the refused call's time is not a duration
    assert sum(got["tools"]["Read"]["hist"]) == 2 and got["tools"]["Bash"]["p95_ms"] == 10_000.0
    assert got["servers"] == {"tracker": {"calls": 2, "errors": 0, "refused": 0}}


def test_a_refusal_named_by_the_turn_s_result_is_refused_not_an_error_and_an_ask_s_time_is_not_tool_time(tmp_path):
    work = str(tmp_path / "work")
    rows = [{"type": "user", "timestamp": at(0), "message": {"content": "go"}}]
    rows += reply(1, "msg_1", [use("b1", "Bash", command="rm -rf build")], usage(0))
    rows.append(results(11, ("b1", True, "The owner said no.")))
    asks = [{"kind": "ask", "time": 2, "id": "a1"}, {"kind": "answered", "time": 9, "id": "a1", "decision": "deny"},
            {"kind": "result", "duration_ms": 11_000, "api_ms": 1_000, "denials": [{"id": "b1", "name": "Bash"}]}]
    # the chat log's times are unix seconds: put the transcript's in the same clock for the hole to fall inside
    for row in rows:
        row["timestamp"] = row["timestamp"].replace("2026-10-03T04:00:", "1970-01-01T00:00:")
    w, c, chat = write(tmp_path, rows, asks)
    got = metrics.read(w, c, SESSION, chat, {})
    assert (got["refused"], got["errors"]) == (1, 0)
    assert got["together"]["permission_asks"] == 1 and got["together"]["denied"] == 1 and got["together"]["granted"] == 0
    assert got["time"]["tool"] == 10 - 7                                  # 1 to 11, less the ask from 2 to 9


def test_the_cache_ratio_is_the_status_line_s_formula_each_request_once(tmp_path):
    work = str(tmp_path / "work")
    w, c, chat = write(tmp_path, conversation(work), CHAT)
    got = metrics.read(w, c, SESSION, chat, RUN)["cache"]
    requests = 11                                   # four, six and the last; each written on two lines
    read = 10 * 5000
    every = read + requests * 100 + 5000
    assert (got["requests"], got["read"], got["input"]) == (requests, read, every)
    assert got["ratio"] == round(read / every, 4) and got["cold_requests"] == 1


def test_the_sources_in_the_order_they_appeared_and_those_the_last_words_name(tmp_path):
    work = str(tmp_path / "work")
    w, c, chat = write(tmp_path, conversation(work), CHAT)
    got = metrics.read(w, c, SESSION, chat, RUN)["retrieval"]
    assert got["sources"] == ["src/a.py", "src/b.py", "src/rules.py", "docs/labels.md", "README.md"]   # c.md refused
    assert got["calls"] == 3 + 1 + 1 + 2 and got["surfaced"] == 5
    assert got["cited"] == 2                                                   # src/rules.py, and labels.md by name


def test_the_times_and_the_work_with_others_come_from_the_record_and_the_chat_log(tmp_path):
    work = str(tmp_path / "work")
    w, c, chat = write(tmp_path, conversation(work), CHAT)
    got = metrics.read(w, c, SESSION, chat, RUN)
    assert got["time"] | {"tool": None} == {"wall": 45, "queue_wait": 30, "active": 39.5, "model": 16.5, "tool": None,
                                            "waiting_owner": 3}
    assert got["together"] == {"dispatches_sent": 2, "dispatches_received": 1, "messages_sent": 1,
                               "messages_received": 0, "questions": 1, "answered": 1, "permission_asks": 1,
                               "granted": 0, "denied": 1, "owner_turns": 2}
    assert got["wall_hist"] == metrics.bucket_counts([45]) and sum(got["wall_hist"]) == 1
    # a permission ask, a question with its answer (one), and the owner's other turn
    assert got["autonomy"] == {"unattended": False, "interventions": 3}
    session = [dict(e, decision="allow_session") if e.get("kind") == "answered" else e for e in CHAT]
    (tmp_path / "again").mkdir()
    w2, c2, chat2 = write(tmp_path / "again", conversation(work), session)
    assert metrics.read(w2, c2, SESSION, chat2, RUN)["together"]["granted"] == 1        # for the session: granted
    nothing = metrics.read(w, c, None, str(tmp_path / "none.jsonl"), {})
    assert (nothing["calls"], nothing["time"]["wall"], sum(nothing["wall_hist"])) == (0, None, 0)
    assert nothing["autonomy"] == {"unattended": True, "interventions": 0}


def test_a_percentile_from_days_added_together_falls_in_the_bucket_of_the_pooled_values():
    rng = random.Random(7)
    days = [[rng.expovariate(1 / 400) for _ in range(rng.randint(20, 60))] for _ in range(3)]
    summed = [0] * (len(metrics.BUCKETS) + 1)
    for values in days:
        summed = metrics.add_counts(summed, metrics.bucket_counts(values))
    pooled = metrics.exact([v for values in days for v in values], 0.95)
    p95 = metrics.percentile(summed, 0.95)
    assert p95["low"] < pooled <= p95["high"] and p95["low"] <= p95["value"] <= p95["high"]
    assert metrics.percentile([0] * len(summed), 0.5) is None
    assert metrics.bucket_counts([0, 5, 5.1, 10_000, 10_001]) == \
        [1, 1, 1] + [0] * 11 + [1, 1]                                         # bounds belong to the bucket below
    assert metrics.percentile(metrics.bucket_counts([20_000]), 0.5) == {"low": 10_000, "high": None, "value": 10_000}
    assert metrics.add_counts(None, [1] * 16) == [1] * 16 and metrics.add_counts([1] * 3, [2] * 16) == [2] * 16
    # within its bucket the value is found by a straight line: the 5th of 10 values in (100, 250] at 175
    assert metrics.percentile(metrics.bucket_counts([150] * 10), 0.5) == {"low": 100, "high": 250, "value": 175.0}
    # one run's own quantile is the nearest rank: the 2nd of 4 at the median, the 19th of 20 at p95
    assert (metrics.exact([4, 1, 3, 2], 0.5), metrics.exact(range(1, 21), 0.95), metrics.exact([], 0.5)) == (2, 19, None)


def test_an_eval_s_run_keeps_its_reply_and_no_other_run_does(tmp_path):
    work = str(tmp_path / "work")
    w, c, chat = write(tmp_path, conversation(work), CHAT)
    assert metrics.read(w, c, SESSION, chat, {"trigger": {"kind": "eval"}})["answer"] == \
        "Labelled them, per src/rules.py and labels.md."
    assert metrics.read(w, c, SESSION, chat, {"trigger": {"kind": "manual"}})["answer"] is None
