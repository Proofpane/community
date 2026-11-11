"""One account's identity is read by one start at a time: a lock on one account's identity read, its wait bounded by
the read's own deadline, a read that cannot have it in time refused with that reason.

The lock is the workspace's, in `identity` (proofpane_workspace/task_runs.py, behind `POST /agents/identity`), where the
read runs. Two reads of one account at once are a sign-out and a sign-in seen half-way by a press's own read and a later
page's, and a read slower than the app's deadline (120 seconds) overlapping the next page's, which goes on in the
workspace after the app has stopped waiting for it; the app cannot close that one. These tests drive `identity` itself
with the workspace's claude replaced (task_attempt's `read_identity` and `_auth_status`), so no claude starts."""

import inspect
import threading
import time
from types import SimpleNamespace

import pytest

from proofpane_open.ade import tasks
from proofpane_workspace import agents as agentry, task_attempt, task_runs

ACCOUNT, OTHER = "a" * 12, "b" * 12
REASON = "Another read of this account's identity is still running: try again when it ends."


class Reads:
    """`identity` as the workspace runs it, over reads that each wait until the test lets one go: how many were running at
    once, which accounts' folders they read, in the order they began."""

    def __init__(self, monkeypatch):
        self.mutex = threading.Lock()
        self.began, self.active, self.most = [], 0, 0
        self.go = threading.Semaphore(0)
        self.fail = []                      # an error each next read raises, if it is one, before it waits
        self.signed_in = [True]             # what `auth status` says before each read: a list, popped from the front
        self.hold_status = False            # each `auth status` waits until the test lets one go
        self.status_go = threading.Semaphore(0)
        self.status_began = []
        monkeypatch.setattr(task_runs.attempts, "_auth_status", self._auth_status)
        monkeypatch.setattr(task_runs.attempts, "read_identity", self._read)
        accounts = SimpleNamespace(get=lambda account: {"kind": "claude", "id": account},
                                   env=lambda got: {"CLAUDE_CONFIG_DIR": "/accounts/" + got["id"]})
        self.runs = task_runs.TaskRuns(SimpleNamespace(ws=SimpleNamespace(accounts=accounts)))
        monkeypatch.setattr(self.runs, "claude", lambda: "/usr/bin/claude-of-the-test")

    def _auth_status(self, claude, env, timeout):
        with self.mutex:
            self.status_began.append(env.get("CLAUDE_CONFIG_DIR", "default"))
            logged_in = self.signed_in.pop(0) if len(self.signed_in) > 1 else self.signed_in[0]
        if self.hold_status:
            assert self.status_go.acquire(timeout=10), "the test never let this `auth status` go"
        return {"loggedIn": logged_in}

    def _read(self, claude, env, *, timeout=60):
        with self.mutex:
            self.began.append(env.get("CLAUDE_CONFIG_DIR", "default"))
            self.active += 1
            self.most = max(self.most, self.active)
        try:
            if self.fail:
                raise self.fail.pop(0)
            assert self.go.acquire(timeout=10), "the test never let this read go"
        finally:
            with self.mutex:
                self.active -= 1
        return {"report": {"email": "a@example.com"}, "auth_status": {}, "org_id": "org", "mismatch": None}

    def let_go(self, how_many=1):
        for _ in range(how_many):
            self.go.release()

    def wait_began(self, how_many, within=5, of="began"):
        end = time.monotonic() + within
        while len(getattr(self, of)) < how_many and time.monotonic() < end:
            time.sleep(0.01)
        assert len(getattr(self, of)) >= how_many, f"{len(getattr(self, of))} of {of}, not {how_many}"

    def read_in_a_thread(self, account, answers):
        def run():
            try:
                answers.append(self.runs.identity(account))
            except Exception as e:                          # noqa: BLE001  what a test's thread ended with
                answers.append(e)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread


def test_two_reads_of_one_account_run_one_after_the_other(monkeypatch):
    reads = Reads(monkeypatch)
    answers = []
    first = reads.read_in_a_thread(ACCOUNT, answers)
    reads.wait_began(1)
    second = reads.read_in_a_thread(ACCOUNT, answers)
    time.sleep(0.4)                                         # long enough for a second read to begin, were it let
    assert len(reads.began) == 1
    reads.let_go()                                          # the first ends
    reads.wait_began(2)                                     # and the second has its turn only then
    reads.let_go()
    first.join(5)
    second.join(5)
    assert (reads.most, len(answers)) == (1, 2)
    assert all(isinstance(a, dict) and a["org_id"] == "org" for a in answers)


@pytest.mark.parametrize("second", ["default", None])
def test_the_default_account_is_one_account_however_it_is_named(monkeypatch, second):
    reads = Reads(monkeypatch)
    answers = []
    first = reads.read_in_a_thread("default", answers)
    reads.wait_began(1)
    waiting = reads.read_in_a_thread(second, answers)
    time.sleep(0.4)
    assert len(reads.began) == 1
    reads.let_go(2)
    first.join(5)
    waiting.join(5)
    assert (reads.most, len(answers)) == (1, 2)


def test_reads_of_two_accounts_run_at_once(monkeypatch):
    reads = Reads(monkeypatch)
    answers = []
    threads = [reads.read_in_a_thread(a, answers) for a in (ACCOUNT, OTHER, "default")]
    reads.wait_began(3)
    assert reads.most == 3
    reads.let_go(3)
    for thread in threads:
        thread.join(5)
    assert len(answers) == 3


def test_a_read_that_cannot_have_its_turn_in_time_is_refused_with_that_reason(monkeypatch):
    monkeypatch.setattr(task_runs, "IDENTITY_WAIT", 0.3)
    reads = Reads(monkeypatch)
    answers = []
    holder = reads.read_in_a_thread(ACCOUNT, answers)
    reads.wait_began(1)
    began_waiting = time.monotonic()
    with pytest.raises(agentry.TaskConflict) as refused:
        reads.runs.identity(ACCOUNT)
    waited = time.monotonic() - began_waiting
    assert str(refused.value) == REASON
    assert 0.25 <= waited < 3
    assert len(reads.began) == 1                            # it never read
    reads.let_go()
    holder.join(5)
    assert len(answers) == 1 and isinstance(answers[0], dict)
    # the refused read gave up nobody's turn: the holder's was its own, and is free now
    reads.let_go()
    assert reads.runs.identity(ACCOUNT)["org_id"] == "org"
    assert reads.most == 1


@pytest.mark.parametrize("ends", ["a read that raises", "an account not signed in"])
def test_a_read_that_fails_gives_its_turn_up(monkeypatch, ends):
    monkeypatch.setattr(task_runs, "IDENTITY_WAIT", 0.3)
    reads = Reads(monkeypatch)
    if ends == "a read that raises":
        reads.fail.append(RuntimeError("the start broke"))
        with pytest.raises(RuntimeError):
            reads.runs.identity(ACCOUNT)
    else:
        reads.signed_in[:] = [False, True]
        with pytest.raises(agentry.AgentError, match="not signed in"):
            reads.runs.identity(ACCOUNT)
    reads.let_go()
    assert reads.runs.identity(ACCOUNT)["org_id"] == "org"  # no wait for a turn the failed read still held


def test_no_such_account_takes_no_turn_and_leaves_no_lock_behind(monkeypatch):
    monkeypatch.setattr(task_runs, "IDENTITY_WAIT", 0.3)
    reads = Reads(monkeypatch)
    reads.runs.ws.accounts.get = lambda account: None
    for account in (ACCOUNT, "", ["a"], {"a": 1}, 7):
        with pytest.raises(agentry.AgentNotFound):
            reads.runs.identity(account)
    assert reads.began == [] and reads.status_began == []
    assert reads.runs._identity_locks == {}                 # a lock for each account there is, and no other


def test_a_read_waiting_for_its_turn_holds_up_no_other_account(monkeypatch):
    reads = Reads(monkeypatch)
    answers = []
    holder = reads.read_in_a_thread(ACCOUNT, answers)
    reads.wait_began(1)
    waiter = reads.read_in_a_thread(ACCOUNT, answers)       # waits for the holder, as long as the holder takes
    time.sleep(0.3)
    other = reads.read_in_a_thread(OTHER, answers)
    reads.wait_began(2, within=3)                           # the other account's read begins at once, whatever waits
    assert reads.began[1] == "/accounts/" + OTHER
    reads.let_go(3)
    for thread in (holder, waiter, other):
        thread.join(5)
    assert len(answers) == 3 and all(isinstance(a, dict) for a in answers)


def test_the_turn_covers_the_check_that_the_account_is_signed_in_as_well_as_the_read(monkeypatch):
    reads = Reads(monkeypatch)
    reads.hold_status = True
    answers = []
    first = reads.read_in_a_thread(ACCOUNT, answers)
    reads.wait_began(1, of="status_began")                  # the first is in its `auth status`, before any read
    second = reads.read_in_a_thread(ACCOUNT, answers)
    time.sleep(0.4)
    assert len(reads.status_began) == 1                     # the second has not asked `auth status` either
    reads.status_go.release(2)
    reads.let_go(2)
    first.join(5)
    second.join(5)
    assert (reads.most, len(answers)) == (1, 2)


def test_the_wait_is_the_read_s_own_deadline_and_well_inside_the_app_s():
    """The wait is the 60 seconds `read_identity` gives its frozen start, so that a read that has not ended by then is
    slow or hung, and the refusal reaches the app, which stops waiting at its own deadline, with the time a read that
    waited the whole wait needs left to it (a check of the ordering alone would pass a wait of 119)."""
    own = inspect.signature(task_attempt.read_identity).parameters["timeout"].default
    assert task_runs.IDENTITY_WAIT == own == 60
    assert task_runs.IDENTITY_WAIT + 30 <= tasks.READ_DEADLINE
