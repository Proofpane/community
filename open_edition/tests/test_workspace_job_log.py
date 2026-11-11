"""A failed job's log, read through gh a line at a time (workspace.py, Workspace._gh_log): never held whole, stopped
at FEEDBACK_LOG_BYTES and FEEDBACK_LOG_SECONDS, each line's first 8 KB kept, and gh's refusals said in words. gh is a
stand-in that writes what each test gives it."""

import os
import sys
import time

import pytest

from proofpane_workspace import workspace as ws


@pytest.fixture
def gh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def make(body):
        (bin_dir / "gh").write_text(f"#!{sys.executable}\nimport os, sys, time\nout = sys.stdout.buffer\n" + body)
        (bin_dir / "gh").chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    make.dir = bin_dir
    return make


def read(tmp_path):
    return ws.Workspace._gh_log(None, ["api", "repos/acme/demo/actions/jobs/1/logs"], str(tmp_path))


def test_a_line_of_any_length_is_cut_and_the_error_after_it_still_found(gh, tmp_path):
    gh('out.write(b"x" * 300000 + b"\\n##[error]boom\\n")')
    text, why = read(tmp_path)
    assert why is None
    assert text == "x" * (ws.FEEDBACK_LINE - 1) + "…\nerror: boom"


def test_a_log_longer_than_the_workspace_reads_is_read_in_part_and_gh_stopped(gh, tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "FEEDBACK_LOG_BYTES", 200_000)
    done = tmp_path / "finished"
    gh(f'for i in range(2_000_000):\n    out.write(b"line %d\\n" % i)\nopen({str(done)!r}, "w").close()')
    began = time.monotonic()
    text, why = read(tmp_path)
    assert why is None and time.monotonic() - began < 20
    assert text.endswith("\n(The log is longer than the workspace reads; this is from its first part.)")
    assert text.startswith("line ") and not done.exists()          # gh did not go on to write the rest


def test_a_slow_log_is_read_for_the_time_allowed(gh, tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "FEEDBACK_LOG_SECONDS", 1)
    gh('out.write(b"##[group]Run make\\n##[error]bad\\n"); out.flush(); time.sleep(60)')
    began = time.monotonic()
    text, why = read(tmp_path)
    assert time.monotonic() - began < 15
    assert (text, why) == ("Run make\nerror: bad\n(GitHub sent the log too slowly to read it all; this is what came "
                           "in time.)", None)
    gh("time.sleep(60)")
    assert read(tmp_path) == (None, "GitHub did not send this job's log in time.")


@pytest.mark.parametrize("body, said", [
    ('print("gh: Not Found (HTTP 404)", file=sys.stderr); sys.exit(1)', "GitHub has no log for this job."),
    ('print("gh: Server Error (HTTP 410)", file=sys.stderr); sys.exit(1)', "GitHub no longer keeps this job's log."),
    ('print("first\\n\\x1b[31mgh: boom\\x1b[0m\\x07", file=sys.stderr); sys.exit(1)', "gh: boom"),
    ("sys.exit(3)", "gh stopped with status 3."),
    ("pass", "Its log is empty."),
    ('out.write(b"\\x1b[2J\\x1b]0;title\\x07\\n")', "Its log is empty."),
])
def test_what_gh_said_instead_of_a_log_is_given_in_words(gh, tmp_path, body, said):
    gh(body)
    assert read(tmp_path) == (None, said)


def test_with_no_gh_there_is_no_log_to_read(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ws.WorkspaceError, match="gh is not installed"):
        read(tmp_path)
