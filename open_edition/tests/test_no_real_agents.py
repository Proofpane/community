"""No test reaches a coding agent installed where the tests run (conftest.py): each one found on the PATH is shadowed by
a tripwire, which notes the call and fails, and a run in which any test reached one fails, under pytest-xdist too."""

import os
import shutil
import subprocess
import sys

import pytest

from conftest import REAL_AGENTS, TRIPWIRE, TRIPWIRES


def test_each_agent_installed_here_is_shadowed_by_its_tripwire():
    for name in TRIPWIRES["found"]:
        assert os.path.dirname(shutil.which(name)) == TRIPWIRES["folder"], name
    assert set(TRIPWIRES["found"]) <= set(REAL_AGENTS)


def test_a_tripwire_notes_the_call_and_fails(tmp_path):
    # one of its own, written as the suite writes them, so the run's own log stays empty
    log = tmp_path / "reached.log"
    wire = tmp_path / "claude"
    wire.write_text(TRIPWIRE.format(name="claude", log=log))
    wire.chmod(0o755)
    p = subprocess.run([str(wire), "-p", "do things"], capture_output=True, text=True)
    assert p.returncode == 97 and "reached the real claude" in p.stderr
    assert log.read_text() == "claude -p do things\n"


@pytest.mark.parametrize("workers", [None, "2"], ids=["one_process", "xdist"])
def test_a_run_that_reaches_one_fails(tmp_path, workers):
    if workers:
        pytest.importorskip("xdist")            # where it is installed: a worker's own failure never reaches the run
    test = tmp_path / "test_reach.py"
    test.write_text("import subprocess\n"
                    "def test_it():\n"
                    "    subprocess.run(['claude', '--version'])\n")
    conftest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conftest.py")
    (tmp_path / "conftest.py").write_text(open(conftest, encoding="utf-8").read())
    # a stand-in "real" claude for this inner run to find on its PATH, which the tripwire then shadows
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\necho real\n")
    (bin_dir / "claude").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTEST_ADDOPTS", None)             # the inner run is in one process or with workers as named here
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        *(["-n", workers] if workers else []), str(test)], cwd=tmp_path, env=env, capture_output=True,
                       text=True)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "Tests reached a real agent" in p.stderr and "claude --version" in p.stderr
