"""The status line every `claude` session runs keeps the numbers Claude Code passes it
(model, context, and, for a subscriber, the 5-hour and 7-day windows) in the session's status.json, and the agent
reports them with the session's usage. Nothing else is read."""

import json
import os
import stat
import subprocess
import sys

import pytest

from conftest import ROOT
from proofpane_workspace import statusline
from test_workspace_agent import agent  # noqa: F401  (a fixture)
from workspace_fixtures import CLAUDE_VERSION_SH, Agent, make_repo

SCRIPT = os.path.join(ROOT, "proofpane_workspace", "statusline.py")

# the example on https://code.claude.com/docs/en/statusline (read 2026-09-28), cut to the fields that matter here
DOCUMENTED = {
    "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
    "context_window": {"used_percentage": 8, "remaining_percentage": 92, "context_window_size": 200000},
    "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
                    "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600},
                    "spend_limit": {"used_percentage": 62.8, "resets_at": 1740787200}},
    "cost": {"total_cost_usd": 0.01234},
    "session_id": "abc123...",
    "transcript_path": "/path/to/transcript.jsonl",
}


def test_what_is_kept_of_the_documented_input():
    assert statusline.clean(DOCUMENTED, at=1700000000) == {
        "at": 1700000000, "model": "Opus", "context_used": 8,
        "five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
        "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600},
        "spend_limit": {"used_percentage": 62.8, "resets_at": 1740787200}}
    assert statusline.line(statusline.clean(DOCUMENTED, at=1)) == "Opus | context 8% | 5h 24% | 7d 41% | spend 63%"


def test_a_session_that_is_not_a_subscriber_s_has_no_windows():
    data = {k: v for k, v in DOCUMENTED.items() if k != "rate_limits"}
    got = statusline.clean(data, at=1)
    assert set(got) == {"at", "model", "context_used"}


@pytest.mark.parametrize("bad", [True, "23", -1, 1001, None, [23], {"x": 1}])
def test_nothing_that_is_not_a_sensible_number_is_kept(bad):
    data = {"model": {"display_name": bad}, "context_window": {"used_percentage": bad},
            "rate_limits": {"five_hour": {"used_percentage": bad, "resets_at": 1}, "seven_day": bad}}
    got = statusline.clean(data, at=1)
    assert got == {"at": 1} or (bad == "23" and got == {"at": 1, "model": "23"})


def test_the_script_keeps_the_session_s_status_and_prints_a_line(tmp_path, own_home):
    out = subprocess.run([sys.executable, SCRIPT], input=json.dumps(DOCUMENTED), capture_output=True, text=True,
                         env={"PATH": os.environ["PATH"], "HOME": own_home, "PP_SESSION_DIR": str(tmp_path)},
                         timeout=30)
    assert out.returncode == 0 and out.stdout == "Opus | context 8% | 5h 24% | 7d 41% | spend 63%"
    kept = json.loads((tmp_path / "status.json").read_text())
    assert kept["five_hour"] == {"used_percentage": 23.5, "resets_at": 1738425600}
    assert statusline.read(str(tmp_path)) == kept


@pytest.mark.parametrize("given", ["not json", "[1, 2]", ""])
def test_input_it_cannot_use_still_gives_a_line_and_no_error(tmp_path, given, own_home):
    out = subprocess.run([sys.executable, SCRIPT], input=given, capture_output=True, text=True,
                         env={"PATH": os.environ["PATH"], "HOME": own_home, "PP_SESSION_DIR": str(tmp_path)},
                         timeout=30)
    assert out.returncode == 0 and out.stdout == ""
    assert set(json.loads((tmp_path / "status.json").read_text())) == {"at"}


def test_outside_a_session_it_only_prints(tmp_path, own_home):
    out = subprocess.run([sys.executable, SCRIPT], input=json.dumps(DOCUMENTED), capture_output=True, text=True,
                         cwd=str(tmp_path), env={"PATH": os.environ["PATH"], "HOME": own_home}, timeout=30)
    assert out.returncode == 0 and out.stdout.startswith("Opus") and os.listdir(tmp_path) == []


def test_a_status_file_is_cleaned_again_when_read(tmp_path):
    (tmp_path / "status.json").write_text(json.dumps({"at": 5, "model": "M", "five_hour": {"used_percentage": "9"},
                                                      "seven_day": {"used_percentage": 3, "resets_at": -4}}))
    assert statusline.read(str(tmp_path)) == {"at": 5, "model": "M",
                                              "seven_day": {"used_percentage": 3, "resets_at": None}}
    (tmp_path / "status.json").write_text("x" * (statusline.MAX_FILE + 1))
    assert statusline.read(str(tmp_path)) is None
    (tmp_path / "status.json").write_text("{not json")
    assert statusline.read(str(tmp_path)) is None
    assert statusline.read(str(tmp_path / "missing")) is None


def test_a_claude_session_reports_what_its_status_line_kept(tmp_path):
    """A stand-in `claude` that does what Claude Code does after a reply: runs the status line with the session's
    data on its standard input. The status reaches the program through the environment the agent gives it (only
    PP_SESSION_DIR and the listed names), and the agent reports it with the usage."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text("#!/bin/sh\n" + CLAUDE_VERSION_SH +
                    f"printf '%s' '{json.dumps(DOCUMENTED)}' | {sys.executable} {SCRIPT} > /dev/null\n"
                    "echo reported\nexec sleep 600\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = a.http.post("/sessions", json=dict(repo="demo", program="claude", task="t")).json()
        path = tmp_path / "ws" / "sessions" / s["id"] / "status.json"
        for _ in range(200):
            if path.exists():
                break
            subprocess.run(["sleep", "0.05"])
        u = a.http.get(f"/sessions/{s['id']}/usage").json()
        assert u["available"] is True and u["status"]["model"] == "Opus"
        assert u["status"]["seven_day"] == {"used_percentage": 41.2, "resets_at": 1738857600}
    finally:
        a.close()


def test_a_claude_session_with_no_status_yet_reports_none(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text("#!/bin/sh\n" + CLAUDE_VERSION_SH + "exec sleep 600\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = a.http.post("/sessions", json=dict(repo="demo", program="claude", task="t")).json()
        u = a.http.get(f"/sessions/{s['id']}/usage").json()
        assert u["available"] is True and u["status"] is None and u["messages"] == 0
    finally:
        a.close()


def test_the_tokens_in_context_and_the_effort_are_kept_for_the_map_and_not_shown_on_the_line():
    # the control window's map: the tokens in the context window at the last reply, cache counted in, and the
    # session's live effort (statusline page, read 2026-10-17)
    data = dict(DOCUMENTED, effort={"level": "xhigh"},
                context_window=dict(DOCUMENTED["context_window"], total_input_tokens=724500, total_output_tokens=300))
    got = statusline.clean(data, at=1)
    assert got["context_tokens"] == 724500 and got["effort"] == "xhigh"
    assert statusline.line(got) == statusline.line(statusline.clean(DOCUMENTED, at=1))
    assert statusline.clean(got, at=2) == dict(got, at=2)           # as status.json is read back


@pytest.mark.parametrize("bad", [True, "724500", -1, 2 ** 41, 1.5, None, [1]])
def test_tokens_that_are_not_a_sensible_whole_number_are_not_kept(bad):
    data = dict(DOCUMENTED, context_window=dict(DOCUMENTED["context_window"], total_input_tokens=bad))
    assert "context_tokens" not in statusline.clean(data, at=1)


@pytest.mark.parametrize("bad", ["HIGH", "x" * 17, "hi gh", 5, {"level": 5}, {"level": "max; rm"}, None])
def test_an_effort_that_is_not_a_level_s_name_is_not_kept(bad):
    assert "effort" not in statusline.clean(dict(DOCUMENTED, effort=bad), at=1)
    assert "effort" not in statusline.clean(dict(DOCUMENTED, effort={"level": bad}), at=1)


def test_a_claude_in_one_of_the_session_s_shells_does_not_speak_for_the_session(tmp_path):
    # it runs on no account of the session's, and the map would show its model as the session's
    out = subprocess.run([sys.executable, SCRIPT], input=json.dumps(DOCUMENTED), capture_output=True, text=True,
                         env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PP_SESSION_DIR": str(tmp_path),
                              "PP_SHELL": "1"}, timeout=30)
    assert out.returncode == 0 and out.stdout.startswith("Opus") and not (tmp_path / "status.json").exists()


def test_a_status_file_that_is_a_fifo_or_a_link_is_never_read(tmp_path):
    from test_workspace_sessionmap import within
    os.mkfifo(tmp_path / "status.json")
    assert within(5, statusline.read, str(tmp_path)) is None              # a read that waited would fail, not hang
    (tmp_path / "status.json").unlink()
    (tmp_path / "real.json").write_text(json.dumps({"at": 5, "model": "M"}))
    os.symlink(tmp_path / "real.json", tmp_path / "status.json")
    assert statusline.read(str(tmp_path)) is None
