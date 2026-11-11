"""The ADE's board, judged at a time the test chooses, over a source held in memory."""

from datetime import datetime, timezone

import pytest

from proofpane_open.ade import board, commits

HEAD = "f" * 40
CLOUD = "Claude-Session: https://claude.ai/code/session_01BOARD"
AGENT = "Co-Authored-By: Claude <noreply@anthropic.com>"


def at(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def c(n, when, message, who=("Owner", "owner@example.org"), parents=1):
    sha = (f"{n:x}" * 40)[:40]  # distinct in its first seven characters, as short ids are compared
    return commits.make_commit(sha, [f"{n + 1000:040x}"] * parents, who[0], who[1], when, message)


CLOUD_WHO = ("Claude", "noreply@anthropic.com")


class Memory:
    label = "owner/repo"

    def __init__(self, files, history=None, recent=None):
        self.files, self.history, self.recent = files, history or {}, recent or []

    def head(self, branch):
        return HEAD

    def list_dir(self, sha, directory):
        prefix = directory + "/"
        return sorted(p[len(prefix):] for p in self.files if p.startswith(prefix) and "/" not in p[len(prefix):])

    def read_file(self, sha, path):
        return self.files[path]

    def file_commits(self, sha, path, limit):
        return self.history.get(path, [])[:limit]

    def branch_commits(self, sha, limit):
        return self.recent[:limit]


def log_line(time, *fields):
    return f"- {time} · " + " · ".join(fields) + "\n"


def demo(extra_files=None, extra_history=None, log=None):
    handoff_commit = c(1, "2026-09-27T09:55:00Z", f"Handoff\n\n{CLOUD}", CLOUD_WHO)
    log = log if log is not None else (
        log_line("2026-09-27T09:58Z", f"read HANDOFF-TO-LOCAL-SESSION.md @ {handoff_commit['short']}")
        + log_line("2026-09-27T10:00Z", "heartbeat", "waiting; heartbeat every 20 minutes", "no unread update")
        + log_line("2026-09-27T10:05Z", "step 1", "WAITING FOR OWNER (Q-001): may it deploy?")
        + log_line("2026-09-27T10:20Z", "heartbeat", "still waiting", "no unread update"))
    files = {
        "t/HANDOFF-TO-LOCAL-SESSION.md": "# Handoff\n\n## Added at 09:55 UTC: step 1\n",
        "t/GO.md": "## GO: deploy\nwindow: 2026-09-27T10:00Z to 2026-09-27T11:00Z\n",
        "t/local-run-log.md": log,
        "t/questions-for-owner.md": "WAITING FOR OWNER (Q-001): may it deploy?\n",
        "t/run.sh": "echo\n",
    }
    files.update(extra_files or {})
    history = {
        "t/HANDOFF-TO-LOCAL-SESSION.md": [handoff_commit],
        "t/GO.md": [c(2, "2026-09-27T09:59:00Z", f"Window\n\n{CLOUD}", CLOUD_WHO)],
        "t/local-run-log.md": [c(3, "2026-09-27T10:20:30Z", f"Log\n\n{AGENT}")],
        "t/questions-for-owner.md": [c(3, "2026-09-27T10:20:30Z", f"Log\n\n{AGENT}")],
    }
    history.update(extra_history or {})
    return Memory(files, history)


def build(source, now, dirs=("t",), verify=False, verifications=None):
    return board.build_board({"branch": "work", "dirs": list(dirs), "verify": verify}, source, at(now), verifications)


def test_a_quiet_moment_on_the_board():
    b = build(demo(), "2026-09-27T10:30Z")
    ch = b["channels"][0]
    assert b["head"] == HEAD and ch["other"] == ["run.sh"]
    (w,) = ch["windows"]
    assert (w["status"], w["minutes_left"], w["writer_problem"]) == ("open", 30, None)
    assert w["writers"] == [{"kind": "cloud", "name": "cloud session 1BOARD"}]
    (log,) = ch["logs"]
    assert log["live"] is True and log["interval_minutes"] == 20 and log["silent_minutes"] == 9
    assert log["waiting"]["ids"] == ["Q-001"] and log["writer"] == "agent session as Owner"
    assert "entries" not in log and log["recent"][0]["time"] == "2026-09-27T10:20Z"
    (ins,) = ch["instructions"]
    assert ins["unread"] is False and ins["read_at"] == "2026-09-27T09:58Z"
    assert [a["level"] for a in b["alerts"]] == ["info", "warn", "info"]
    assert [s["kind"] for s in b["sessions"]] == ["agent", "cloud"]
    assert b["sessions"][0]["commits"] == 1  # one commit that touched two files is counted once


@pytest.mark.parametrize("now,status", [("2026-09-27T09:00Z", "upcoming"), ("2026-09-27T10:00Z", "open"),
                                        ("2026-09-27T10:59Z", "open"), ("2026-09-27T11:00Z", "ended")])
def test_a_window_opens_and_ends_on_time(now, status):
    assert build(demo(), now)["channels"][0]["windows"][0]["status"] == status


def test_a_withdrawn_or_invalid_window_is_never_open():
    withdrawn = demo({"t/GO.md": "## GO: deploy\nwindow: 2026-09-27T10:00Z to 2026-09-27T10:00Z\n"})
    assert build(withdrawn, "2026-09-27T10:00Z")["channels"][0]["windows"][0]["status"] == "withdrawn"
    invalid = demo({"t/GO.md": "## GO: deploy\n## GO: again\nwindow: 2026-09-27T10:00Z to 2026-09-27T11:00Z\n"})
    b = build(invalid, "2026-09-27T10:30Z")
    assert b["channels"][0]["windows"][0]["status"] == "invalid"
    assert any(a["level"] == "warn" and "not a valid window" in a["text"] for a in b["alerts"])


def test_a_window_written_by_anyone_but_the_cloud_session_is_flagged():
    history = {"t/GO.md": [c(9, "2026-09-27T10:01:00Z", f"Widen my own window\n\n{AGENT}"),
                           c(2, "2026-09-27T09:59:00Z", f"Window\n\n{CLOUD}", CLOUD_WHO)]}
    b = build(demo(extra_history=history), "2026-09-27T10:30Z")
    w = b["channels"][0]["windows"][0]
    assert w["writer_problem"].startswith("also written by agent session as Owner")
    assert any(a["level"] == "warn" and "GO.md: also written by" in a["text"] for a in b["alerts"])


def test_silence_past_two_intervals_raises_an_alert():
    quiet = build(demo(), "2026-09-27T11:00Z")          # 39 minutes since the last sign of life: under 2 x 20
    assert not [a for a in quiet["alerts"] if a["level"] == "alert"]
    silent = build(demo(), "2026-09-27T11:01Z")         # 40 minutes and 30 seconds: over
    alerts = [a for a in silent["alerts"] if a["level"] == "alert"]
    assert len(alerts) == 1 and "No new line in local-run-log.md for 40 minutes" in alerts[0]["text"]
    # the same silence read a minute later has other words and the same name, so a page does not notify it twice
    later = [a for a in build(demo(), "2026-09-27T11:02Z")["alerts"] if a["level"] == "alert"]
    assert (later[0]["kind"], later[0]["file"], later[0]["channel"]) == (
        alerts[0]["kind"], alerts[0]["file"], alerts[0]["channel"]) == ("silence", "local-run-log.md", "t")
    assert later[0]["text"] != alerts[0]["text"]


def test_a_finished_or_superseded_log_is_shown_not_alarmed_on():
    done = (log_line("2026-09-27T09:58Z", "read HANDOFF-TO-LOCAL-SESSION.md")
            + log_line("2026-09-27T10:00Z", "heartbeat", "going", "no unread update")
            + log_line("2026-09-27T10:10Z", "deploy", "DONE: deployed"))
    b = build(demo(log=done), "2026-09-27T20:00Z")
    assert b["channels"][0]["logs"][0]["finished"] is True
    assert not [a for a in b["alerts"] if "No new line" in a["text"]]
    old = {"u/local-run-log.md": log_line("2026-09-26T09:00Z", "heartbeat", "old", "no unread update")}
    b = build(demo(extra_files=old, extra_history={"u/local-run-log.md": []}), "2026-09-27T10:30Z", dirs=("t", "u"))
    stale = b["channels"][1]["logs"][0]
    assert stale["live"] is False and stale["silent_minutes"] > 1000
    assert not [a for a in b["alerts"] if a["level"] == "alert"]


def test_an_update_no_log_says_was_read_raises_an_alert():
    newer = c(5, "2026-09-27T10:10:00Z", f"Handoff: step 2\n\n{CLOUD}", CLOUD_WHO)
    history = {"t/HANDOFF-TO-LOCAL-SESSION.md": [newer]}
    b = build(demo(extra_history=history), "2026-09-27T10:25Z")
    ins = b["channels"][0]["instructions"][0]
    assert ins["unread"] is True and ins["unread_minutes"] == 15
    assert not [a for a in b["alerts"] if a["level"] == "alert"]   # 15 minutes: not yet past the 20-minute interval
    b = build(demo(extra_history=history), "2026-09-27T10:31Z")
    (alert,) = [a["text"] for a in b["alerts"] if a["level"] == "alert"]
    assert alert.startswith("HANDOFF-TO-LOCAL-SESSION.md changed 21 minutes ago (5555555, cloud session 1BOARD)")


def test_a_log_in_a_subdirectory_reads_the_instructions_above_it():
    files = {"t/NEXT.md": "# Next\n", "other/NEXT.md": "# Other\n",
             "t/v/local-run-log.md": log_line("2026-09-27T10:00Z", "read NEXT.md from the top")}
    history = {"t/NEXT.md": [c(7, "2026-09-27T09:00:00Z", f"Next\n\n{CLOUD}", CLOUD_WHO)],
               "other/NEXT.md": [c(8, "2026-09-27T09:00:00Z", f"Other\n\n{CLOUD}", CLOUD_WHO)],
               "t/v/local-run-log.md": [c(9, "2026-09-27T10:00:00Z", f"Log\n\n{AGENT}")]}
    b = build(Memory(files, history), "2026-09-27T12:00Z", dirs=("t", "t/v", "other"))
    by_dir = {ch["name"]: ch for ch in b["channels"]}
    assert by_dir["t"]["instructions"][0]["unread"] is False
    assert by_dir["other"]["instructions"][0]["unread"] is True   # an unrelated NEXT.md is not the one read


def test_questions_stop_and_problems_become_alerts():
    files = {"t/STOP.md": "Stop.\n", "t/owner-answers.md": "---\nQ-001: yes\n", "t/RELEASE.md": "no line\n"}
    b = build(demo(extra_files=files), "2026-09-27T10:30Z")
    texts = [(a["level"], a["text"]) for a in b["alerts"]]
    assert ("alert", "STOP.md is present: the sessions in this channel are to stop.") in texts
    assert ("warn", "RELEASE.md: it has 0 '## RELEASE:' lines; a release file has one") in texts
    assert not any("question(s) wait" in t for _, t in texts)   # Q-001 is answered


def test_a_verified_branch_lists_its_commits_with_their_results():
    recent = [c(20, "2026-09-27T11:00:00Z", "Merge", parents=2), c(21, "2026-09-27T10:50:00Z", f"Code\n\n{AGENT}")]
    source = demo()
    source.recent = recent
    results = {recent[1]["sha"]: {"status": "verified", "verify": "pass"}}
    b = build(source, "2026-09-27T11:05Z", verify=True, verifications=results)
    assert [(x["short"], x["merge"], x["verification"]) for x in b["commits"]] == [
        (recent[0]["short"], True, None), (recent[1]["short"], False, {"status": "verified", "verify": "pass"})]
    assert b["commits"][1]["session"]["kind"] == "agent"
    assert build(source, "2026-09-27T11:05Z")["commits"] == []   # verify off: no commits read
