"""The budget check for an attempt of a requirement's manager (budget_check.py):
the word of Proofpane's budget check that decides an attempt's start is one asked for
that attempt, and lately. The loop's round (controller.keep_attempts) asks it, with the account's status lines, before
the start lock, for the attempt it finds starting then, and waits for the lock behind whatever holds it: a session's
start, a clone, a removal, the mirror's take, the app's take of an attempt (take_managers), among others.
A round that found nothing starting once asked nothing, so an attempt the app's take marked starting while the round
waited had its chat made at once with no check at all, and its window tested on no status lines. So a word asked for no
attempt, for another, or longer ago than the controller takes one for (controller.ATTEMPT_WORD_SECONDS, six of the
check's waits) is asked again under the lock, the status lines read again with it, before the attempt's chat is made;
one asked for the attempt that starts, and fresh, is not asked twice. Proofpane is the stand-in (proofpane_stand_in.py);
the Agent SDK is the in-process stand-in of test_workspace_budget_check.py, and an attempt's chat's sends are kept, so
no claude starts; the start lock is a stand-in that counts who waits on it, so each test orders the waits it needs."""

import asyncio
import time

import pytest

from proofpane_stand_in import DEVICE, TOKEN, Proofpane, budget_answer, proofpane  # noqa: F401  (a fixture)
from proofpane_workspace import budget_check, controller as ctl
from test_controller_managers_workspace import _closes, _sends, an_attempt, listed, managed, taken
from test_controller_workspace import NOW, ws  # noqa: F401  (ws is a fixture)
from test_workspace_budget_check import REFUSED, checks, pair, sdk  # noqa: F401  (sdk is a fixture)

GUARD = 15                          # seconds a test waits on the controller at most: past them it fails, never hangs
FRESH = budget_check.WAIT * 6       # seconds a word is taken for at an attempt's start: ATTEMPT_WORD_SECONDS
BODY = {"device_id": DEVICE, "token": TOKEN}        # one budget check, as Proofpane is asked it: the pairing's alone
SPENT = ("subscription",)                           # a spent scope: the product refuses an attempt's kind of spend


class _StartLock(asyncio.Lock):
    """The controller's start lock, which the agent hands it (agent.make_app: every session's start, clone and removal
    holds it), counting the takes begun, so that a test knows who waits behind the take it holds itself."""

    def __init__(self):
        super().__init__()
        self.begun = 0

    async def acquire(self):
        self.begun += 1
        return await super().acquire()


class _Clock:
    """The controller module's `time` in a test: the real module's, but for monotonic, which runs `ahead` seconds fast
    once set, so that a word asked before the lock is older than the bound by the time the lock is had."""

    def __init__(self):
        self.ahead = 0.0

    def monotonic(self):
        return time.monotonic() + self.ahead

    def __getattr__(self, name):
        return getattr(time, name)


async def _queued(lock, tasks):
    """Until each of `tasks` waits on the start lock, behind the test's own take: one that ended first, which none may
    while the lock is held, fails the test with its own error or with this one's words."""
    deadline = time.monotonic() + GUARD
    while lock.begun < len(tasks) + 1:
        for task in tasks:
            if task.done():
                task.result()
                raise AssertionError("a step ended before it waited on the start lock")
        assert time.monotonic() < deadline, f"{lock.begun - 1} of {len(tasks)} steps wait on the start lock"
        await asyncio.sleep(0.01)


def behind_the_lock(w, *steps, between=None):
    """The start lock taken and held, as a session's start holds it; each of `steps` (each a function giving a
    coroutine) begun in turn, each waiting on the lock behind the one before; `between` run while the lock is still
    held; then the lock let go and the steps waited for, GUARD seconds at most in all: their answers, in order."""
    async def main():
        lock = w.controller.lock = _StartLock()
        await lock.acquire()
        tasks = []
        try:
            for step in steps:
                tasks.append(asyncio.create_task(step()))
                await _queued(lock, tasks)
            if between is not None:
                between()
        finally:
            lock.release()
        return await asyncio.wait_for(asyncio.gather(*tasks), GUARD)
    try:
        return asyncio.run(main())
    finally:
        w.controller.lock = None


def take_attempt(w, a, version=1):
    """The app's take of the attempt `a` (PUT /controller/managers, as take_managers takes it): a function giving it."""
    return lambda: w.controller.take_managers({"version": version, "managers": [], "attempt": a})


def outcome(attempt):
    return attempt["state"], attempt.get("outcome"), attempt.get("cause"), attempt.get("note")


def started(w, attempt, sent):
    """Whether the attempt's chat was made (its session a chat's) and given its first message, and the attempt runs."""
    return attempt["state"] == "running" and [s[0] for s in sent] == [attempt["session"]] and \
        w._read(attempt["session"])["program"] == "chat"


def unmade(w, attempt, sent, sdk):
    """Whether nothing of the attempt's chat was made: no session, no message, no claude connected."""
    return sent == [] and attempt.get("session") is None and w.controller.record["attempt_sessions"] == [] and \
        not [c for c in sdk if c.connected]


# ---- the attempt that starts is not the one the word was asked for ---------------------------------------------------

def test_an_attempt_the_app_took_while_the_round_waited_for_the_lock_is_checked_and_a_spent_scope_ends_it_unmade(
        ws, tmp_path, proofpane, monkeypatch, sdk):
    """The race as it happens: something holds the start lock, the app's take of an attempt waits on it, and a round of
    the loop, finding nothing starting, asks nothing and waits behind the take. The take marks the attempt taken and
    starting; the round, given the lock, asks the check for it there, and the product's refusal ends it, `failed`
    (`start`) in the product's words, before its chat is made."""
    pair(tmp_path / "wshome", proofpane)
    Proofpane.budget = budget_answer(refused=SPENT)
    _, requirement = managed(ws)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    a = an_attempt(requirement)

    def nothing_asked():
        assert checks() == []           # the round found nothing starting: it asked nothing before the lock
    answer, _ = behind_the_lock(ws, take_attempt(ws, a), ws.controller.keep_attempts, between=nothing_asked)
    assert answer["attempt"] == {"id": a["id"], "taken": True, "cause": None, "why": None}, answer
    attempt = ws.controller.record["attempts"][a["id"]]
    assert outcome(attempt) == ("ended", "failed", "start", REFUSED), attempt
    assert checks() == [BODY]           # asked once, under the lock, for this attempt
    assert unmade(ws, attempt, sent, sdk), (outcome(attempt), attempt.get("session"), sent)


def test_a_word_asked_for_an_attempt_the_owner_s_side_ended_is_asked_again_for_the_attempt_taken_after_it(
        ws, tmp_path, proofpane, monkeypatch, sdk):
    """The round asks before the lock for the attempt starting then, and finds room. Behind the lock the owner's side
    holds the requirement, which ends that attempt, then lets the hold go, and the app's take takes the next. The word
    was not the next attempt's: the check is asked again for it under the lock, and its answer decides."""
    pair(tmp_path / "wshome", proofpane)
    m, requirement = managed(ws)
    first = taken(ws, requirement)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    after = an_attempt(requirement, fence=2, n=2)
    held = dict(listed(m, requirement, hold=True), version=m["version"] + 1)
    let_go = dict(listed(m, requirement), version=m["version"] + 2)

    def spent_since():
        assert checks() == [BODY]       # asked before the lock, for the first attempt, which had room then
        Proofpane.budget = budget_answer(refused=SPENT)
    *_, answer, _ = behind_the_lock(ws, lambda: ws.controller.take(held), lambda: ws.controller.take(let_go),
                                    take_attempt(ws, after, version=2), ws.controller.keep_attempts,
                                    between=spent_since)
    assert outcome(first)[:3] == ("ended", "stopped", "held"), first
    assert answer["attempt"]["taken"] is True, answer
    attempt = ws.controller.record["attempts"][after["id"]]
    assert outcome(attempt) == ("ended", "failed", "start", REFUSED), attempt
    assert checks() == [BODY, BODY]
    assert unmade(ws, attempt, sent, sdk), (outcome(attempt), attempt.get("session"), sent)


def test_an_attempt_the_app_took_while_the_round_waited_is_tested_on_the_status_lines_read_under_the_lock(
        ws, monkeypatch, sdk):
    """The status lines are read again with the check: an attempt the app's take marked starting while the round
    waited, which read no status lines before the lock, is refused `window` where the account's newest status line
    shows its window spent by the time the lock is had, waiting until its reset, as the start's window test has it."""
    status = {}
    monkeypatch.setattr(ws.controller, "_newest", lambda: status.get("now"))
    _, requirement = managed(ws)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    a = an_attempt(requirement)

    def window_spent():
        status["now"] = {"five_hour": {"resets_at": NOW + 3600, "used_percentage": 100}}
    behind_the_lock(ws, take_attempt(ws, a), ws.controller.keep_attempts, between=window_spent)
    attempt = ws.controller.record["attempts"][a["id"]]
    assert outcome(attempt)[:3] == ("ended", "refused", "window"), attempt
    assert attempt["waits_until"] == NOW + 3600, attempt
    assert unmade(ws, attempt, sent, sdk), (outcome(attempt), attempt.get("session"), sent)


# ---- the word that is the attempt's, but old -------------------------------------------------------------------------

@pytest.mark.parametrize("first, second", [((), SPENT), (SPENT, ())], ids=["room then spent", "spent then room"])
def test_a_word_older_than_the_controller_takes_one_for_is_asked_again_and_the_second_answer_decides(
        ws, tmp_path, proofpane, monkeypatch, sdk, first, second):
    """The round asks for the attempt starting, then has the lock only once longer than a word is taken for has passed:
    the check is asked again under the lock, and its second answer, not its first, decides the start."""
    pair(tmp_path / "wshome", proofpane)
    Proofpane.budget = budget_answer(refused=first)
    _, requirement = managed(ws)
    attempt = taken(ws, requirement)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    clock = _Clock()
    monkeypatch.setattr(ctl, "time", clock)

    def past_the_bound():
        assert checks() == [BODY]       # asked once before the lock, for this attempt
        Proofpane.budget = budget_answer(refused=second)
        clock.ahead = FRESH + 1
    behind_the_lock(ws, ws.controller.keep_attempts, between=past_the_bound)
    if second:
        assert outcome(attempt) == ("ended", "failed", "start", REFUSED), attempt
        assert unmade(ws, attempt, sent, sdk), (outcome(attempt), attempt.get("session"), sent)
    else:
        assert started(ws, attempt, sent), (outcome(attempt), sent)
    assert checks() == [BODY, BODY]


def test_a_word_s_age_counts_from_before_its_ask_so_a_slow_ask_and_the_lock_s_wait_past_the_bound_ask_it_again(
        ws, tmp_path, proofpane, monkeypatch, sdk):
    """A word decides a start only within ATTEMPT_WORD_SECONDS of being asked, counted from before the ask, so that an
    ask that took the check's whole wait (budget_check.WAIT) counts. Here the ask takes that wait and the lock's wait
    two seconds less than the bound: past the bound counted from before the ask, within it counted from after. The
    check is asked again under the lock, and its second answer, a spent scope, decides."""
    pair(tmp_path / "wshome", proofpane)
    _, requirement = managed(ws)
    attempt = taken(ws, requirement)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    clock = _Clock()
    monkeypatch.setattr(ctl, "time", clock)
    ask = ws.budget

    def slow(kind):
        word = ask(kind)
        clock.ahead += budget_check.WAIT        # the ask took the check's whole wait
        return word
    monkeypatch.setattr(ws, "budget", slow)

    def the_lock_s_wait():
        assert checks() == [BODY]               # asked once before the lock, for this attempt, with room
        Proofpane.budget = budget_answer(refused=SPENT)
        clock.ahead += FRESH - 2
    behind_the_lock(ws, ws.controller.keep_attempts, between=the_lock_s_wait)
    assert outcome(attempt) == ("ended", "failed", "start", REFUSED), attempt
    assert unmade(ws, attempt, sent, sdk), (outcome(attempt), attempt.get("session"), sent)
    assert checks() == [BODY, BODY]


# ---- controls --------------------------------------------------------------------------------------------------------

def test_control_with_room_an_attempt_the_app_took_while_the_round_waited_for_the_lock_starts(
        ws, tmp_path, proofpane, monkeypatch, sdk):
    pair(tmp_path / "wshome", proofpane)
    _, requirement = managed(ws)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    a = an_attempt(requirement)
    behind_the_lock(ws, take_attempt(ws, a), ws.controller.keep_attempts)
    attempt = ws.controller.record["attempts"][a["id"]]
    assert started(ws, attempt, sent), (outcome(attempt), sent)


def test_control_a_workspace_with_no_budget_check_starts_an_attempt_taken_while_the_round_waited_asking_nothing(
        ws, tmp_path, proofpane, monkeypatch, sdk):
    """A stand-in for the workspace may have no budget check (keep_attempts' `ask`): its attempt starts as it did, with
    no word, and Proofpane is asked nothing."""
    pair(tmp_path / "wshome", proofpane)
    Proofpane.budget = budget_answer(refused=SPENT)        # were it asked, refused
    monkeypatch.setattr(ws, "budget", None)
    _, requirement = managed(ws)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    a = an_attempt(requirement)
    behind_the_lock(ws, take_attempt(ws, a), ws.controller.keep_attempts)
    attempt = ws.controller.record["attempts"][a["id"]]
    assert started(ws, attempt, sent), (outcome(attempt), sent)
    assert checks() == []


@pytest.mark.parametrize("ahead", [0, FRESH - 2], ids=["at once", "just inside the bound"])
def test_control_an_attempt_starting_before_the_round_is_asked_once_its_word_fresh_when_the_lock_is_had(
        ws, tmp_path, proofpane, monkeypatch, sdk, ahead):
    pair(tmp_path / "wshome", proofpane)
    _, requirement = managed(ws)
    attempt = taken(ws, requirement)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    clock = _Clock()
    monkeypatch.setattr(ctl, "time", clock)

    def later():
        clock.ahead = ahead
    behind_the_lock(ws, ws.controller.keep_attempts, between=later)
    assert started(ws, attempt, sent), (outcome(attempt), sent)
    assert checks() == [BODY]
