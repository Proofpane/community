"""No test leaves a process behind (conftest.py): one that names the run's base temporary folder on its command line
and is still alive once every fixture is torn down is named, killed, and fails the run, under pytest-xdist too;
another run's processes and this run's own ancestors are spared."""

import os
import signal
import subprocess
import sys
import time

import pytest

from conftest import LEFT_BEHIND, left_behind

LEAVES = """import os, shutil, subprocess


def test_it_leaves_a_sleep_behind(tmp_path):
    p = subprocess.Popen([str(tmp_path / "sleep"), "300"], executable=shutil.which("sleep"), start_new_session=True)
    with open(os.environ["PP_LEFT_PID"], "w") as fh:
        fh.write(str(p.pid))
"""


def _gone(pid, timeout):
    """Whether process `pid` is gone within `timeout` seconds; one that ended and is not yet reaped counts."""
    deadline = time.time() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
        except OSError:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


def test_only_what_names_the_folder_is_named_and_the_run_s_own_line_is_spared():
    table = [
        (1, 0, "/sbin/launchd"),
        (10, 1, "/bin/sh -c python -m pytest --basetemp=/x/bt/ tests"),          # the shell the run was started from
        (11, 10, "python -m pytest --basetemp=/x/bt/ tests"),                    # the run itself
        (20, 1, "python -m proofpane_workspace --root /x/bt/test_a0/ws"),        # left behind, init's now
        (21, 20, '/x/bt/test_a0/bin/claude --settings {"dir": "/x/bt"}'),
        (22, 1, 'tool "/x/bt"'),
        (23, 1, "tool /x/bt"),
        (30, 1, "sleep 300 /x/bt2/a"),                                           # another folder, its name runs on
        (31, 1, "python -m pytest --basetemp=/x/bt-other"),                      # another run
        (32, 1, "python -m proofpane_workspace --root /y/x/bt.old/ws"),
    ]
    assert [pid for pid, _ in left_behind("/x/bt", table, 11)] == [20, 21, 22, 23]


@pytest.mark.parametrize("workers", [None, "2"], ids=["one_process", "xdist"])
def test_a_run_that_leaves_a_process_behind_fails_and_the_process_is_ended(tmp_path, workers):
    if workers:
        pytest.importorskip("xdist")            # where it is installed: a worker's own failure never reaches the run
    (tmp_path / "test_leaves.py").write_text(LEAVES)
    conftest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conftest.py")
    (tmp_path / "conftest.py").write_text(open(conftest, encoding="utf-8").read())
    pidfile = tmp_path / "left.pid"
    env = dict(os.environ, PP_LEFT_PID=str(pidfile), PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTEST_ADDOPTS", None)             # the inner run is in one process or with workers as named here
    # under this run's own folder: were the inner run's guard to miss it, this run's would not
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        *(["-n", workers] if workers else []), f"--basetemp={tmp_path / 'run'}", "test_leaves.py"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert pidfile.exists(), p.stdout + p.stderr
    pid = int(pidfile.read_text())
    try:
        assert p.returncode == 1, p.stdout + p.stderr
        lines = p.stderr.splitlines()
        (at,) = [i for i, line in enumerate(lines) if line.startswith(LEFT_BEHIND)]
        assert lines[at + 1].split()[0] == str(pid) and "test_it_leaves_a_sleep_behind0/sleep 300" in lines[at + 1]
        assert _gone(pid, 10), "the process left behind was not ended"
    finally:
        if not _gone(pid, 0):
            os.kill(pid, signal.SIGKILL)
