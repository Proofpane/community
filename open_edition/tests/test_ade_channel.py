"""The ADE's channel reader, on files shaped like the ones the owner's two sessions write.

The fixtures are made up and in English. The Chinese the owner's sessions also write is built from the reader's own
code-point constants, so this file stays in English as the repository requires. `test_ade_real_channel.py` runs the
same reader over a real channel directory when one is named.
"""

import pytest

from proofpane_open.ade import channel
from proofpane_open.ade.channel import (DOT, FW_COLON, FW_LPAREN, FW_RPAREN, ZH_HEARTBEAT, ZH_NOT_UNREAD,
                                        ZH_PROCESSED, ZH_UNREAD, ZH_WITHDRAWN)

S = f" {DOT} "

LOG = f"""---
status: current
---

# Example task: the log of the session on the owner's machine

*One line per step and per heartbeat: UTC time, short commit, what happened, what is next.*

- 2026-09-26T09:34Z{S}3c43394{S}heartbeat{S}step 1 done; waiting for the next handoff{S}handoff has an unread update: NEXT.md
- 2026-09-26T09:35Z{S}acbc317{S}read HANDOFF.md (6ad5b34, cloud 09:26Z: a new task) and NEXT.md @ 1a2b3c4 from the top
- 2026-09-26T09:49Z{S}a49b5cb{S}step 1{S}created the venv; pip install exited 0
- 2026-09-26T09:52Z{S}f02c9ae{S}step 3{S}WAITING FOR OWNER (W-1, W-2, see questions-for-owner.md): the check was held
- 2026-09-26T10:06Z{S}30bb9da{S}heartbeat{S}WAITING: W-2 still waits; heartbeat every 20 minutes{S}no unread update
- 2026-09-26T10:30Z{S}deploy{S}DONE: deployed (machine 123)
- not a log line: the header's example has no time
- 2026-09-26T10:31Z{S}a note with no commit and no label
"""

WINDOW = """## GO: example deploy
window: 2026-09-27T04:05Z to 2026-09-27T05:05Z
key file: /somewhere/.env

Written by the cloud session at 2026-09-27T04:05Z. The record, not instructions.

- The owner to the cloud session: "deploy".
"""

QUESTIONS = f"""# Example task: questions for the owner

Answer in the local session, or by your own commit to `owner-answers.md`, one line each.

## Waiting

WAITING FOR OWNER (W-1): the step 3 check was sent for approval and timed out, so it did not run.
Recommendation: approve it when you are at the machine.

WAITING FOR OWNER (W-2): the run needs a key in the file named in NEXT.md.

WAITING FOR OWNER (A, step 6, 2026-09-26T20:45Z): the paid part can start. Say "here" when you are at the machine.

## Update, 2026-09-26T10:06Z

- **W-1: withdrawn, nothing for you to do.** The revised page takes the key file out of step 3.

## Translation

WAITING FOR OWNER{FW_LPAREN}W-2{FW_RPAREN}{FW_COLON}the same question again, in the owner's language.
"""

ANSWERS = """# Example task: the owner's answers

The owner writes here, one line each (for example `W-1: here, retry`).

---
W-2: done, the key is in the file
A (the step 6 question): here
A: here, again after the page changed
"""


def test_roles_come_from_file_names():
    assert [channel.role_of(n) for n in ("GO.md", "GO-2.md", "RELEASE.md", "NEXT.md", "HANDOFF-TO-LOCAL-SESSION.md",
                                         "ana-answers.md", "questions-for-ana.md", "step5-questions.md",
                                         "local-run-log.md", "log.md", "README.md", "STOP", "STOP.md",
                                         "GO.md.sig.json", "run.sh", "GO.txt", "go.md", "notes.md")] == [
        "window", "window", "release", "instructions", "instructions", "answers", "questions", "questions", "log",
        "log", "readme", "stop", "stop", "signature", None, None, None, None]


def test_a_log_is_read_line_by_line():
    entries = channel.read_log("local-run-log.md", LOG)["entries"]
    assert [(e["time"], e["commit"], e["kind"], e["label"]) for e in entries] == [
        ("2026-09-26T09:34Z", "3c43394", "heartbeat", None),
        ("2026-09-26T09:35Z", "acbc317", "read", None),
        ("2026-09-26T09:49Z", "a49b5cb", "step", "step 1"),
        ("2026-09-26T09:52Z", "f02c9ae", "waiting", "step 3"),
        ("2026-09-26T10:06Z", "30bb9da", "heartbeat", None),
        ("2026-09-26T10:30Z", None, "done", "deploy"),
        ("2026-09-26T10:31Z", None, "step", None),
    ]
    first, read, step, waiting, second, done, note = entries
    assert first["unread"] is True and first["every_minutes"] is None
    assert first["state"] == "step 1 done; waiting for the next handoff"
    assert second["unread"] is False and second["every_minutes"] == 20
    assert read["reads"] == [{"path": "HANDOFF.md", "commit": "6ad5b34"}, {"path": "NEXT.md", "commit": "1a2b3c4"}]
    assert step["text"] == "created the venv; pip install exited 0"
    assert waiting["waiting_for"] == "OWNER" and waiting["ids"] == ["W-1", "W-2"]
    assert done["text"] == "DONE: deployed (machine 123)"
    assert note["text"] == "a note with no commit and no label"
    assert [e["line"] for e in entries] == [9, 10, 11, 12, 13, 14, 16]


def test_the_chinese_words_of_a_heartbeat_are_known():
    line = f"- 2026-09-27T14:12Z{S}c801a09{S}{ZH_HEARTBEAT}{S}WAITING{FW_COLON}a window{S}handoff {ZH_UNREAD}"
    entry = channel.read_log_line(line)
    assert entry["kind"] == "heartbeat" and entry["commit"] == "c801a09" and entry["unread"] is True
    assert channel.read_log_line(line.replace(ZH_UNREAD, ZH_NOT_UNREAD))["unread"] is False
    every = chr(0x6BCF) + " 20 " + chr(0x5206) + chr(0x949F)
    assert channel.read_log_line(line + f"{S}{ZH_HEARTBEAT}{every}")["every_minutes"] == 20


def test_a_read_line_names_each_file_once():
    line = (f"- 2026-09-26T04:52Z{S}75f92d5{S}read answers.md @ 75f92d5 (it holds Q-003); answers.md again, "
            f"and NEXT.md{FW_LPAREN}30bb9da, from the top{FW_RPAREN}")
    assert channel.read_log_line(line)["reads"] == [{"path": "answers.md", "commit": "75f92d5"},
                                                    {"path": "NEXT.md", "commit": "30bb9da"}]


@pytest.mark.parametrize("line", ["", "- no time here", "- 2026-09-26T09:34 · no Z", "* 2026-09-26T09:34Z · a star",
                                  "  - 2026-09-26T09:34Z · indented", "- 2026-09-26T09:34Z no dot"])
def test_what_is_not_a_log_line_is_skipped(line):
    assert channel.read_log_line(line) is None


def test_a_hex_word_alone_is_text_not_a_commit():
    entry = channel.read_log_line("- 2026-09-26T09:34Z · deadbeef")
    assert entry["commit"] is None and entry["text"] == "deadbeef"


def test_a_window_file():
    w = channel.read_window("GO.md", WINDOW)
    assert w == {"file": "GO.md", "name": "example deploy", "start": "2026-09-27T04:05Z",
                 "end": "2026-09-27T05:05Z", "key_file": "/somewhere/.env", "problems": []}
    withdrawn = channel.read_window("GO-2.md", WINDOW.replace("05:05Z", "04:05Z"))
    assert withdrawn["problems"] == [] and withdrawn["start"] == withdrawn["end"]


@pytest.mark.parametrize("change,problem", [
    (lambda t: t + "\n## GO: another\n", "2 '## GO:' headings"),
    (lambda t: t.replace("## GO: example deploy\n", ""), "0 '## GO:' headings"),
    (lambda t: t.replace("window: 2026-09-27T04:05Z to 2026-09-27T05:05Z\n", ""), "0 window lines"),
    (lambda t: t + "window: 2026-09-28T04:05Z to 2026-09-28T05:05Z\n", "2 window lines"),
    (lambda t: t.replace("window: 2026-09-27T04:05Z to", "window: 2026-09-27 04:05 to"), "is not 'window:"),
    (lambda t: t.replace("to 2026-09-27T05:05Z", "to 2026-09-27T03:05Z"), "ends before it starts"),
    (lambda t: t.replace("2026-09-27T05:05Z", "2026-02-30T05:05Z"), "does not exist"),
    (lambda t: t.replace("## GO: example", "##GO: example"), "0 '## GO:' headings"),
])
def test_a_window_file_that_is_not_exact_is_invalid(change, problem):
    w = channel.read_window("GO.md", change(WINDOW))
    assert any(problem in p for p in w["problems"]), w["problems"]


def test_a_release_file_has_exactly_one_release_line():
    assert channel.read_release("RELEASE.md", "# Release\n\n## RELEASE: the run\n\nnotes\n") == {
        "file": "RELEASE.md", "name": "the run", "problems": []}
    assert channel.read_release("RELEASE.md", "## Round 2\n")["problems"]
    assert channel.read_release("RELEASE.md", "## RELEASE: a\n## RELEASE: b\n")["name"] is None


def test_questions_are_read_once_each():
    qs = channel.read_questions("questions-for-owner.md", QUESTIONS)
    assert [(q["id"], q["for"], q["asked_at"], q["withdrawn"], q["processed"]) for q in qs] == [
        ("W-1", "OWNER", None, True, False),
        ("W-2", "OWNER", None, False, False),
        ("A", "OWNER", "2026-09-26T20:45Z", False, False),
    ]
    assert qs[0]["summary"] == ("the step 3 check was sent for approval and timed out, so it did not run. "
                                "Recommendation: approve it when you are at the machine.")
    assert qs[1]["line"] == 10 and qs[1]["section"] == "Waiting"


def test_questions_under_headings_and_a_processed_section():
    text = f"""# Questions

## Waiting

### Q-005 {DOT} 2026-09-27T01:00Z {DOT} WAITING FOR ANA: which window?

## {ZH_PROCESSED}

### Q-004 {DOT} 2026-09-26T04:16Z {DOT} WAITING FOR ANA{FW_COLON}what did "authorise" mean? {DOT} {ZH_PROCESSED} 2026-09-26T04:53Z
### Q-003 {DOT} 2026-09-26T02:38Z {DOT} confirm step 7

## Processed

### Q-002 {DOT} 2026-09-26T00:15Z {DOT} what next {DOT} processed 2026-09-26T00:48Z
"""
    qs = {q["id"]: q for q in channel.read_questions("questions.md", text)}
    assert qs["Q-005"]["processed"] is False and qs["Q-005"]["summary"] == "which window?"
    assert qs["Q-004"]["processed"] is True and qs["Q-004"]["processed_at"] == "2026-09-26T04:53Z"
    assert qs["Q-004"]["summary"] == 'what did "authorise" mean?' and qs["Q-004"]["for"] == "ANA"
    assert qs["Q-003"]["processed"] is True and qs["Q-003"]["processed_at"] is None
    assert qs["Q-002"]["processed_at"] == "2026-09-26T00:48Z"


def test_a_chinese_withdrawal_is_known():
    text = f"WAITING FOR OWNER (W-3): something\n\n- **W-3{FW_COLON}{ZH_WITHDRAWN}** nothing to do\n"
    assert channel.read_questions("questions.md", text)[0]["withdrawn"] is True


def test_answers_skip_the_header_and_the_last_answer_counts():
    answers = channel.read_answers("owner-answers.md", ANSWERS)
    assert [(a["id"], a["text"]) for a in answers] == [
        ("W-2", "done, the key is in the file"), ("A", "here"), ("A", "here, again after the page changed")]
    assert answers[0]["line"] == 6
    no_rule = channel.read_answers("a.md", f"Q-001{FW_COLON}yes\nQ-002 (the second): no\nnot an answer\n")
    assert [(a["id"], a["text"]) for a in no_rule] == [("Q-001", "yes"), ("Q-002", "no")]


def test_a_channel_directory():
    files = {"local-run-log.md": LOG, "GO.md": WINDOW, "questions-for-owner.md": QUESTIONS,
             "owner-answers.md": ANSWERS, "README.md": "# Example task\n\nWhat this task is.\nSecond line.\n\nMore.\n",
             "NEXT.md": "# Next\n\n## Step 1\n\n## Step 2\n", "run.sh": "#!/bin/sh\n", "RELEASE.md": "## RELEASE: x\n",
             "GO-2.md": "## GO: a\n## GO: b\n", "GO.md.sig.json": "{}", "STOP.md": "Stop now.\n"}
    ch = channel.read_channel("strategy/example", files)
    assert ch["other"] == ["run.sh"]
    assert ch["readme"] == {"title": "Example task", "summary": "What this task is. Second line."}
    assert ch["instructions"][0]["last_section"] == "Step 2"
    assert len(ch["logs"]) == 1 and len(ch["logs"][0]["entries"]) == 7
    assert [w["file"] for w in ch["windows"]] == ["GO-2.md", "GO.md"]
    assert ch["problems"] == ["GO-2.md: it has 2 '## GO:' headings; a window file has exactly one",
                              "GO-2.md: it has 0 window lines; a window file has exactly one"]
    assert ch["signatures"] == ["GO.md.sig.json"] and ch["stop"] == {"file": "STOP.md", "text": "Stop now."}
    q = {q["id"]: q for q in ch["questions"]}
    assert q["W-1"]["open"] is False and q["W-1"]["answer"] is None          # withdrawn
    assert q["W-2"]["open"] is False and q["W-2"]["answer"]["text"] == "done, the key is in the file"
    assert q["A"]["answer"]["text"] == "here, again after the page changed"


def test_a_file_too_large_or_not_text_is_named_not_read():
    big = "x" * (channel.MAX_FILE + 1)
    ch = channel.read_channel("d", {"local-run-log.md": big, "GO.md": b"bytes"})
    assert ch["logs"] == [] and ch["windows"] == []
    assert ch["problems"] == ["GO.md is not text", "local-run-log.md is larger than 2 MB and was not read"]


def test_times():
    assert channel.parse_time("2026-09-27T04:05Z").isoformat() == "2026-09-27T04:05:00+00:00"
    assert channel.parse_time("2026-09-27T04:05:09Z").second == 9
    for bad in ("2026-09-27T04:05", "2026-02-30T00:00Z", "2026-09-27 04:05Z", None, 5):
        assert channel.parse_time(bad) is None
