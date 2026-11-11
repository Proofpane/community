"""The channel reader over real channel directories, when some are named.

Real channel directories are a person's own, kept on a private branch, so none is copied here: set
`ADE_REAL_CHANNELS` to one or more channel directories, separated by the path separator (`:` on macOS and Linux), and
this test reads each. Unset, it is skipped.

    ADE_REAL_CHANNELS=~/work/channel-a:~/work/channel-b pytest -q tests

What it checks is what must hold for any real directory: nothing raises; every file the reader takes is placed; every
log line that looks like an entry is read as one; every window file is valid or says why not; each question is
listed once.
"""

import os
import re

import pytest

from proofpane_open.ade import channel

DIRS = [d for d in os.environ.get("ADE_REAL_CHANNELS", "").split(os.pathsep) if d.strip()]


@pytest.mark.skipif(not DIRS, reason="ADE_REAL_CHANNELS names no channel directory")
@pytest.mark.parametrize("path", DIRS)
def test_a_real_channel_directory_is_read(path):
    path = os.path.expanduser(path)
    files = {}
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isfile(full):
            with open(full, encoding="utf-8", errors="replace") as fh:
                files[name] = fh.read()
    ch = channel.read_channel(os.path.basename(path), files)
    assert {f["name"] for f in ch["files"]} | set(ch["other"]) == set(files)
    for log in ch["logs"]:
        looks = [line for line in files[log["file"]].split("\n") if re.match(r"^- \d{4}-\d{2}-\d{2}T\d{2}:\d{2}", line)]
        missed = [line[:80] for line in looks if channel.read_log_line(line) is None]
        assert not missed, f"{log['file']}: lines that look like entries but were not read: {missed}"
        assert len(log["entries"]) == len(looks)
    for window in ch["windows"]:
        assert window["problems"] or (channel.parse_time(window["start"]) and channel.parse_time(window["end"]))
    ids = [q["id"] for q in ch["questions"]]
    assert len(ids) == len(set(ids))
