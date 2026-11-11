"""One `claude auth status` per account at a time: GET /claude and GET /accounts with
status both ask an account's Claude Code whether it is signed in, and each asking can take seconds; the pages ask
every few seconds, each look not waiting for the last. A read made while one of the same account is under way joins
it, and its answer is kept a few seconds and shared, so that many reads at once run the program once. A stand-in
`claude` here counts each `auth status` it answers, taking a second over it, as a slow answer would."""

import os
import sys
import threading

import pytest

from workspace_fixtures import Agent

STAND_IN = r'''#!{python}
import json, os, sys, time
if sys.argv[1:2] in (["-v"], ["--version"]):        # as the real one says it
    print("2.1.274 (Claude Code)")
    sys.exit(0)
if sys.argv[1:3] == ["auth", "status"]:
    with open(os.path.join(os.environ["HOME"], ".status-calls"), "a") as fh:
        fh.write("status\n")
    slow = os.path.join(os.environ["HOME"], ".status-seconds")      # a test's own, else a second
    time.sleep(float(open(slow).read()) if os.path.exists(slow) else 1.0)
    print(json.dumps({{"loggedIn": False, "authMethod": "none"}}))
    sys.exit(1)
sys.exit(2)
'''
# GET /accounts asks Codex too: a stand-in for it, signed out, so that no codex the machine has is ever run
CODEX = '#!/bin/sh\nif [ "$1 $2" = "login status" ]; then echo "Not logged in" >&2; exit 1; fi\nexit 2\n'


@pytest.fixture
def sagent(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(STAND_IN.format(python=sys.executable))
    claude.chmod(0o755)
    (bin_dir / "codex").write_text(CODEX)
    (bin_dir / "codex").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    yield a
    a.close()


def _calls(tmp_path):
    path = tmp_path / "home" / ".status-calls"
    return path.read_text().count("status\n") if path.exists() else 0


def _at_once(agent, paths):
    answers, errors = [], []

    def read(path):
        try:
            answers.append((path, agent.http.get(path, timeout=30).status_code))
        except Exception as e:  # noqa: BLE001  told below, with the rest
            errors.append(repr(e))
    threads = [threading.Thread(target=read, args=(p,)) for p in paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return answers, errors


def test_many_reads_of_one_account_at_once_run_its_status_once(sagent, tmp_path):
    before = _calls(tmp_path)
    answers, errors = _at_once(sagent, ["/claude"] * 6 + ["/accounts"] * 2)
    assert not errors and len(answers) == 8 and all(code == 200 for _, code in answers), (answers, errors)
    assert _calls(tmp_path) - before == 1
    # kept a few seconds: a read just after takes the same answer, the program not asked again
    assert sagent.http.get("/claude").json()["signed_in"] is False
    assert _calls(tmp_path) - before == 1


def test_a_sign_out_asks_the_account_again(sagent, tmp_path):
    assert sagent.http.get("/claude").json()["signed_in"] is False
    before = _calls(tmp_path)
    # the sign-out's own answer is read after it, never the one kept from before it
    sagent.http.post("/claude/sign-out")
    assert _calls(tmp_path) - before == 1


def test_a_slow_status_is_kept_a_few_seconds_after_it_comes(sagent, tmp_path):
    # kept from when its answer came, not from when it was asked: a status that takes longer than it is kept is still
    # shared by the reads just after it
    (tmp_path / "home" / ".status-seconds").write_text("3.5")
    before = _calls(tmp_path)
    assert sagent.http.get("/claude", timeout=30).json()["signed_in"] is False
    assert sagent.http.get("/claude", timeout=30).json()["signed_in"] is False
    assert _calls(tmp_path) - before == 1


def test_a_read_for_a_start_asks_anew_though_an_answer_is_kept(sagent, tmp_path):
    # a kept answer is for the pages' looks; a session's start, which the app checks with ?fresh=1, reads the account
    # anew (a sign-in made outside the workspace, in a terminal of its own, is seen at once), still one read at a time
    assert sagent.http.get("/claude").json()["signed_in"] is False
    before = _calls(tmp_path)
    assert sagent.http.get("/claude").json()["signed_in"] is False            # kept: not asked again
    assert _calls(tmp_path) - before == 0
    assert sagent.http.get("/claude", params={"fresh": "1"}).json()["signed_in"] is False
    assert _calls(tmp_path) - before == 1


def test_a_task_s_backends_read_each_account_anew_though_an_answer_is_kept(sagent, tmp_path):
    # the backends a task can run on (GET /agents/backends) give each account's readiness and organisation as auth
    # status gives them, which the app keeps for the owner's choice: read anew, as a session's start is, never the
    # answer kept for the pages' looks
    assert sagent.http.get("/claude").json()["signed_in"] is False
    before = _calls(tmp_path)
    assert sagent.http.get("/claude").json()["signed_in"] is False            # kept: not asked again
    assert _calls(tmp_path) - before == 0
    got = sagent.http.get("/agents/backends", timeout=30)
    assert got.status_code == 200 and got.json()["backends"][0]["ready"] is False, got.text
    assert _calls(tmp_path) - before == 1
