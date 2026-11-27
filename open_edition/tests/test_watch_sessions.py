"""A run's sessions on a Mac, and when each is forgotten (processes.py), with the Mac written out by the cases: what its
ps lists and says of a start, the session each process is in, and the clock a look reads before its ps starts and after
it answers. So they run where there is no Mac; tests/test_watch.py's Mac cases run the same code against a Mac's own.
The written-out ps and sessions are given to a case's own threads alone: any other thread of the process is answered
by the machine's."""

import os
import subprocess
import threading
import time
import types

import pytest

from proofpane_workspace import processes

RUN, ANOTHER_RUN = "5e55105e5510", "0ddba110ddba"       # runs' ids: no process carries either
SHELL, LEFT, OTHER = 5_000_001, 5_000_002, 5_000_003    # ids no process has: past the most Linux or a Mac gives one
THEN, LATER = "Tue Nov 24 08:00:00 2026", "Tue Nov 24 09:30:00 2026"     # two starts, each as ps gives one
HELD = "a look held"
GETSID = os.getsid


class Mac:
    """The Mac a look reads, as a case writes it: for the case's own threads, the one that made it and its held looks'.
    `real` is the module's own ps, which answers any other thread."""

    def __init__(self, real):
        self.real, self.mine = real, {threading.current_thread()}
        self.clock = 1000.0             # what a look reads before its ps starts and after it answers (time.monotonic)
        self.running = {}               # pid -> (its parent, its session, its start as ps gives it)
        self.unlisted = set()           # running, but born after a look's ps listed: the listing shows none of them
        self.zombies = set()            # ended and not yet waited for: listed, with a start, their session not given
        self.held = None                # (a ps call, reached, go on): a look in the thread named HELD waits after it
        self.unanswered = set()         # the ps calls ("list", "start") that do not answer the case's own threads
        self.before_it_lists = {}       # a ps call -> what happens, once, before a call of the case's own thread
                                        # takes its answer (a ps that lists late), and
        self.before_it_answers = {}     # once it has taken it and before it gives it (a ps that answers late)

    def start(self, pid, begun, session=None, parent=1):
        self.running[pid] = (parent, session or pid, begun)

    def end(self, pid):
        del self.running[pid]
        self.zombies.discard(pid)

    def ps(self, *args, text=True):
        """processes._ps: what runs, as a look's listing reads it ("list"), or when one process began ("start")."""
        if threading.current_thread() not in self.mine:
            return self.real(*args, text=text)
        own = threading.current_thread().name != HELD
        if args == ("-ax", "-o", "pid=,ppid=,stat=,lstart="):
            call = "list"
        elif args[:3] == ("-o", "lstart=", "-p") and len(args) == 4:
            call = "start"
        else:
            raise AssertionError(f"a ps call no case here expects: {args}")
        if call in self.unanswered:
            return None                 # as processes._ps gives a call that timed out or could not be started
        if own and call in self.before_it_lists:
            self.before_it_lists.pop(call)()
        if call == "list":
            out = "".join(f"{pid:>7} {parent:>7} {'Z' if pid in self.zombies else 'S':<4} {begun}\n"
                          for pid, (parent, _, begun) in sorted(self.running.items()) if pid not in self.unlisted)
            answer = subprocess.CompletedProcess(args, 0, out, "")
        else:
            running = self.running.get(int(args[3]))
            answer = subprocess.CompletedProcess(args, 0 if running else 1, f"{running[2]}\n" if running else "", "")
        if self.held and self.held[0] == call and not own:
            _, reached, go_on = self.held
            reached.set()
            go_on.wait(10)
        elif own and call in self.before_it_answers:
            self.before_it_answers.pop(call)()
        return answer

    def getsid(self, pid):
        if threading.current_thread() not in self.mine or pid in (0, os.getpid(), os.getppid()):
            return GETSID(pid)
        if pid in self.running and pid not in self.zombies:
            return self.running[pid][1]
        raise ProcessLookupError(pid)


class Os:
    """The os module as processes.py reads it, a process's session told by the case's Mac."""

    def __init__(self, getsid):
        self.getsid = getsid

    def __getattr__(self, name):
        return getattr(os, name)


class Lock:
    """The module's lock, with something done just before one taking of it by the case's own thread: what another
    thread did meanwhile. The takings counted are that thread's."""

    def __init__(self):
        self.lock, self.taken, self.before, self.mine = threading.Lock(), 0, {}, threading.current_thread()

    def __enter__(self):
        if threading.current_thread() is self.mine:
            self.taken += 1
            meanwhile = self.before.pop(self.taken, None)
            if meanwhile is not None:
                meanwhile()
        self.lock.acquire()

    def __exit__(self, *exc):
        self.lock.release()


class Watched(dict):
    """The times the module keeps, with each change made while its lock is free said."""

    def __init__(self, lock):
        super().__init__()
        self.lock, self.free = lock, []

    def _changing(self, how):
        if not self.lock.lock.locked():
            self.free.append(how)

    def __setitem__(self, key, value):
        self._changing("set")
        super().__setitem__(key, value)

    def __delitem__(self, key):
        self._changing("del")
        super().__delitem__(key)

    def pop(self, *args):
        self._changing("pop")
        return super().pop(*args)

    def setdefault(self, *args):
        self._changing("setdefault")
        return super().setdefault(*args)

    def update(self, *args, **more):
        self._changing("update")
        super().update(*args, **more)

    def clear(self):
        self._changing("clear")
        super().clear()


@pytest.fixture
def mac(monkeypatch):
    """processes.py given a Mac of the case's own: nothing kept before the case or after it is the other's, and no
    thread of the module's looks meanwhile (none is started, and one a Mac's own cases left running looks at nothing).
    """
    mac = Mac(processes._ps)
    monkeypatch.setattr(processes, "sys", types.SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(processes, "time", types.SimpleNamespace(monotonic=lambda: mac.clock, sleep=time.sleep))
    monkeypatch.setattr(processes, "os", Os(mac.getsid))
    monkeypatch.setattr(processes, "_ps", mac.ps)
    monkeypatch.setattr(processes, "_SESSIONS", {})
    monkeypatch.setattr(processes, "_EMPTY", {})
    monkeypatch.setattr(processes, "_SINCE", {}, raising=False)    # the table this file's later cases are of
    monkeypatch.setattr(processes, "_wake", threading.Event())
    monkeypatch.setattr(processes, "_pruner", [None])
    mac.prune = processes.prune         # the module's own, for the case that runs its thread
    monkeypatch.setattr(processes, "prune", lambda: None)
    return mac


def _kept(run_id=RUN):
    """The sessions kept for the run, each with its leader's start."""
    return dict(processes._SESSIONS.get(run_id, {}))


def _times(run_id=RUN):
    """The times kept for the run's sessions, by session."""
    return {sid: at for (run, sid), at in dict(processes._EMPTY).items() if run == run_id}


def _seen_empty_once(mac, beside=()):
    """RUN keeps the session a shell led, the shell over and nothing left in it, and one look has seen it so (what the
    look found in the run's other sessions is `beside`)."""
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.end(SHELL)
    assert processes.in_sessions(RUN) == list(beside) and SHELL in _kept() and SHELL in _times()


class HeldLook:
    """A look at RUN's sessions in a thread of its own, held in its ps call `after`, its copy of the kept sessions taken
    before; go_on() lets it go on to its end. Held in "list" (what runs), its listing is made as the call begins and
    the look reads the clock for its answer only once it is let go: a ps that answers late. Held in "start" (when a
    leader began), it has made both readings and has not yet said what it saw of that leader's session."""

    def __init__(self, mac, after):
        self.reached, self.let_go, self.failed = threading.Event(), threading.Event(), []
        mac.held = (after, self.reached, self.let_go)
        self.thread = threading.Thread(target=self.look, name=HELD, daemon=True)
        mac.mine.add(self.thread)
        self.thread.start()
        assert self.reached.wait(10)

    def look(self):
        try:
            processes.in_sessions(RUN)
        except BaseException as error:      # said by go_on(), in the case's own thread
            self.failed.append(error)

    def go_on(self):
        self.let_go.set()
        self.thread.join(10)
        assert not self.thread.is_alive() and self.failed == []


def _own(by, begun):
    return processes.own(RUN, SHELL) if by == "own" else processes.own_kept(RUN, SHELL, begun)


def _takes_its_two_looks(mac):
    """The session SHELL leads, just kept for RUN: its leader leaves one process in it and ends, and no one look that
    sees it empty forgets it, whatever a look saw of that id before; two a second or more apart do."""
    mac.start(LEFT, LATER, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after this look's ps listed
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # one look that sees it empty: kept
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [LEFT]             # so the next sees what was left: the run's to end
    mac.end(LEFT)
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # seen empty anew
    mac.clock += 0.75
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # and again within a second: kept
    mac.clock += 0.25
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}   # a second on: forgotten


# The order lane 2 forced on a Mac: a look takes its copy of the run's kept sessions, another look forgets one of
# them, and the first goes on from its copy. Its script is kept with release 66's records, not in this tree:
# docs/handoff/open-edition/release-66-runbook/checks/watch_race.py.

@pytest.mark.parametrize("beside", [False, True], ids=["the run's only session", "beside another of the run's"])
def test_a_look_that_took_its_copy_before_another_look_forgot_a_session_keeps_no_time_for_it(mac, beside):
    if beside:
        mac.start(OTHER, THEN)                              # another session of the run's, its leader running on
        assert processes.own(RUN, OTHER)
    found = [OTHER] if beside else []
    _seen_empty_once(mac, found)
    mac.clock += processes.EMPTY_AGAIN
    look = HeldLook(mac, "list")                            # its copy taken, its ps listed, and held there
    try:
        assert processes.in_sessions(RUN) == found          # another look, a second on: forgotten
        assert SHELL not in _kept() and _times() == {}
        processes._wake.clear()
    finally:
        look.go_on()
    assert _times() == {}                                   # no time kept for a session forgotten
    assert not processes._wake.is_set()                     # and no look asked for on its account
    assert _kept() == ({OTHER: THEN} if beside else {})


def test_a_look_told_a_leader_is_gone_keeps_no_time_for_a_session_forgotten_since_as_one_whose_id_was_given_again(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.end(SHELL)
    look = HeldLook(mac, "start")                           # told its leader is gone, and held there
    try:
        mac.start(SHELL, LATER)                             # its id given again, to a process that is not the run's
        assert processes.in_sessions(RUN) == [] and _kept() == {}       # another start: forgotten
    finally:
        look.go_on()
    assert _kept() == {} and _times() == {}


@pytest.mark.parametrize("taking", [1, 2, 3, 4])
def test_whenever_another_look_forgets_a_session_a_look_keeps_a_time_only_for_one_still_kept(mac, monkeypatch, taking):
    _seen_empty_once(mac)
    processes._wake.clear()
    mac.clock += processes.EMPTY_AGAIN
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    lock.before[taking] = lambda: processes.in_sessions(RUN)             # another look, whole, before this taking
    assert processes.in_sessions(RUN) == []
    assert (taking in lock.before) == (taking == 4)         # a look that forgets takes the lock three times, no more
    assert _kept() == {} and _times() == {}                 # forgotten by one of the two, and no time left
    assert not processes._wake.is_set()                     # and no look asked for on account of a session not kept


def test_of_a_session_not_kept_for_the_run_a_look_is_told_no_keeps_no_time_and_wakes_no_thread(mac):
    assert processes._empty_twice(RUN, SHELL, THEN, mac.clock, mac.clock) is False      # one never kept
    assert _times() == {} and not processes._wake.is_set()
    _seen_empty_once(mac)
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and _kept() == {}           # one kept, and forgotten by two looks
    processes._wake.clear()
    assert processes._empty_twice(RUN, SHELL, THEN, mac.clock, mac.clock) is False      # no longer kept
    assert _times() == {} and not processes._wake.is_set()
    mac.start(SHELL, THEN)
    assert processes.own(ANOTHER_RUN, SHELL)                # and one kept for another run: no more this one's for that
    assert processes._empty_twice(RUN, SHELL, THEN, mac.clock, mac.clock) is False
    assert _times() == {} and _times(ANOTHER_RUN) == {} and not processes._wake.is_set()


def test_a_look_s_time_is_kept_and_dropped_only_while_the_module_s_lock_is_held(mac, monkeypatch):
    lock = Lock()
    times, probe = Watched(lock), Watched(lock)
    probe[RUN] = 0                                          # the watch itself: a change with the lock free is said
    assert probe.free == ["set"]
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    monkeypatch.setattr(processes, "_EMPTY", times)
    _seen_empty_once(mac)                                   # kept by a look
    mac.start(SHELL, LATER)
    assert processes.own(RUN, SHELL)                        # the session kept under that id by own()
    mac.end(SHELL)
    assert processes.in_sessions(RUN) == [] and (RUN, SHELL) in times
    mac.start(SHELL, LATER)
    assert processes.own_kept(RUN, SHELL, LATER)            # and by own_kept()
    mac.start(LEFT, LATER, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}
    assert processes.in_sessions(RUN) == [] and (RUN, SHELL) in times
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [LEFT] and times == {}         # dropped by a look that sees a process
    mac.end(LEFT)
    assert processes.in_sessions(RUN) == [] and (RUN, SHELL) in times
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and _kept() == {} and times == {}   # and by the forgetting
    assert times.free == [] and lock.taken


def test_when_a_session_was_kept_or_seen_with_a_process_is_changed_only_while_the_module_s_lock_is_held(
        mac, monkeypatch):
    lock = Lock()
    since = Watched(lock)
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    monkeypatch.setattr(processes, "_SINCE", since)
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL) and since == {(RUN, SHELL): mac.clock}         # set by own()
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.clock += 0.5
    assert processes.in_sessions(RUN) == [SHELL, LEFT] and since == {(RUN, SHELL): mac.clock}   # moved on by a sighting
    mac.clock += 0.5
    assert processes.own_kept(RUN, SHELL, THEN) and since == {(RUN, SHELL): mac.clock}          # set by own_kept()
    mac.end(SHELL)
    mac.end(LEFT)
    assert processes.in_sessions(RUN) == [] and (RUN, SHELL) in since
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and _kept() == {} and since == {}       # dropped by a forgetting
    mac.start(SHELL, LATER)
    assert processes.own(RUN, SHELL)
    processes._SESSIONS[RUN][SHELL] = THEN                  # as if kept from another start than its leader's
    assert processes.in_sessions(RUN) == [] and _kept() == {} and since == {}       # and by that forgetting too
    assert since.free == [] and lock.taken


# A session owned again under an id a look's time was left for: forgotten only by two looks of its own.

@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_session_owned_again_under_the_id_of_one_forgotten_as_a_look_went_on_takes_its_two_looks(mac, by):
    _seen_empty_once(mac)
    mac.clock += processes.EMPTY_AGAIN
    look = HeldLook(mac, "list")
    try:
        assert processes.in_sessions(RUN) == [] and _kept() == {}
    finally:
        look.go_on()                                        # the look that took its copy before, gone on and over
    mac.clock += 60
    mac.start(SHELL, LATER)                                 # the id given again: a process of the run's leads under it
    assert _own(by, LATER) and _kept() == {SHELL: LATER}
    _takes_its_two_looks(mac)


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_session_owned_under_an_id_still_kept_with_a_look_s_time_takes_its_two_looks(mac, by):
    _seen_empty_once(mac)                                   # one look has seen the old one empty: its time is kept
    mac.start(SHELL, LATER)                                 # the id given again before a second look
    assert _own(by, LATER) and _kept() == {SHELL: LATER}
    mac.clock += processes.EMPTY_AGAIN                      # a second after that look
    _takes_its_two_looks(mac)


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_session_kept_again_from_the_same_start_takes_its_two_looks_from_then_too(mac, by):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.zombies = {SHELL}                                   # over and not yet waited for: ps still gives its start
    assert processes.in_sessions(RUN) == [] and SHELL in _times()
    assert _own(by, THEN)
    assert SHELL not in _times()
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # the first look since it was kept again
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_time_left_for_an_id_no_session_is_kept_under_is_dropped_as_one_is_kept_under_it(mac, by):
    processes._EMPTY[(RUN, SHELL)] = mac.clock - 60         # as the code left one before this change: nothing here does
    mac.start(SHELL, LATER)
    assert _own(by, LATER) and _times() == {}
    _takes_its_two_looks(mac)


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_look_that_goes_on_before_an_own_takes_the_lock_leaves_the_session_kept_and_no_time(mac, monkeypatch, by):
    _seen_empty_once(mac)                                   # the old session, one look's time kept for it
    mac.clock += processes.EMPTY_AGAIN
    look = HeldLook(mac, "start")                           # a look told the old leader is gone, and held there
    try:
        mac.start(SHELL, LATER)                             # the id given again: a process of the run's leads under it
        lock = Lock()
        monkeypatch.setattr(processes, "_sessions_lock", lock)
        lock.before[1] = look.go_on                         # the look goes on to its end, then the own takes the lock
        assert _own(by, LATER) and lock.before == {}
        assert _kept() == {SHELL: LATER} and _times() == {}
    finally:
        look.go_on()


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_an_own_keeps_the_session_and_drops_the_time_in_one_hold_of_the_lock(mac, monkeypatch, by):
    _seen_empty_once(mac)                                   # the old session, one look's time kept for it
    mac.start(SHELL, LATER)                                 # the id given again: a process of the run's leads under it
    lock, between = Lock(), []
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    lock.before[2] = lambda: between.append((_kept(), _times()))        # what a thread would find between two holds
    assert _own(by, LATER)
    assert lock.taken == 1 and between == []                # one hold: nothing for another thread to come between
    assert _kept() == {SHELL: LATER} and _times() == {}


def test_an_own_that_keeps_nothing_leaves_a_look_s_time_as_it_was(mac, monkeypatch):
    _seen_empty_once(mac)
    assert not processes.own(RUN, SHELL) and not processes.own_kept(RUN, SHELL, THEN)     # its leader gone
    mac.start(SHELL, LATER)
    assert not processes.own_kept(RUN, SHELL, THEN)         # running from another start than the one given
    monkeypatch.setattr(processes, "sys", types.SimpleNamespace(platform="linux"))
    assert not processes.own(RUN, SHELL) and not processes.own_kept(RUN, SHELL, LATER)    # nor elsewhere than a Mac
    monkeypatch.setattr(processes, "sys", types.SimpleNamespace(platform="darwin"))
    mac.end(SHELL)
    assert _kept() == {SHELL: THEN} and SHELL in _times()
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}


# What the two looks did before this change, here as on a Mac.

def test_a_session_is_forgotten_by_two_looks_a_second_or_more_apart_that_see_it_empty_and_by_no_fewer(mac):
    assert processes.EMPTY_AGAIN == 1
    _seen_empty_once(mac)
    assert processes._wake.is_set()                         # the first to see it so: the thread that looks is woken
    processes._wake.clear()
    mac.clock += 0.75
    assert processes.in_sessions(RUN) == [] and SHELL in _kept() and not processes._wake.is_set()
    mac.clock += 0.25
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}
    assert processes.in_sessions(RUN) == [] and _times() == {}  # and a look after: nothing of it


def test_a_look_that_sees_a_process_in_a_session_has_its_two_looks_begin_again(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}
    assert processes.in_sessions(RUN) == [] and SHELL in _times()
    mac.unlisted = set()
    mac.clock += 2 * processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [LEFT] and SHELL not in _times()
    mac.unlisted = {LEFT}
    mac.clock += 2 * processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # seen empty anew: kept yet
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [LEFT]


# A look acts only on the session it copied: one kept under an id given again, while a look that copied the earlier
# session of that id is under way, is that look's neither to give a time nor to forget (the three orders the read of
# the change before this one found).

@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_look_told_the_old_leader_is_gone_keeps_no_time_for_a_session_kept_since_under_its_id(mac, by):
    _seen_empty_once(mac)                                   # the old session, one look's time kept for it
    mac.clock += processes.EMPTY_AGAIN
    look = HeldLook(mac, "start")                           # a look told the old leader is gone, and held there
    try:
        mac.start(SHELL, LATER)                             # the id given again: a process of the run's leads under it
        assert _own(by, LATER) and _kept() == {SHELL: LATER} and _times() == {}
    finally:
        look.go_on()                                        # the look goes on after the own
    assert _kept() == {SHELL: LATER} and _times() == {}     # the session it copied was another
    mac.clock += processes.EMPTY_AGAIN                      # a second after that look's listing
    _takes_its_two_looks(mac)


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_look_whose_copy_holds_the_old_start_does_not_forget_the_session_kept_since_under_its_id(mac, by):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.end(SHELL)
    look = HeldLook(mac, "list")                            # its copy taken, its ps listed, no leader's start asked yet
    try:
        mac.start(SHELL, LATER)                             # the id given again: a process of the run's leads under it
        assert _own(by, LATER)
    finally:
        look.go_on()                                        # it reads the new leader's start: another than its copy's
    assert _kept() == {SHELL: LATER}                        # the session it copied is gone; this one is not its own
    assert processes.in_sessions(RUN) == [SHELL] and _kept() == {SHELL: LATER}


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_an_own_between_a_look_s_yes_and_its_forgetting_keeps_its_session(mac, monkeypatch, by):
    _seen_empty_once(mac)
    processes._wake.clear()
    mac.clock += processes.EMPTY_AGAIN
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)

    def meanwhile():
        mac.start(SHELL, LATER)                             # the id given again: a process of the run's leads under it
        assert _own(by, LATER)
    lock.before[3] = meanwhile                              # before the look's third taking of the lock: its forgetting
    assert processes.in_sessions(RUN) == [] and lock.before == {}
    assert _kept() == {SHELL: LATER} and _times() == {}
    assert not processes._wake.is_set()                     # nor does it ask for a look: that session is not its copy's


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_look_that_listed_before_a_session_was_kept_again_from_the_same_start_keeps_no_time_for_it(mac, by):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.zombies = {SHELL}                                   # over and not yet waited for: ps still gives its start
    look = HeldLook(mac, "start")                           # a look that listed before the keeping below
    try:
        mac.clock += 0.5
        assert _own(by, THEN) and _times() == {}            # kept again, from now
    finally:
        look.go_on()
    assert _kept() == {SHELL: THEN} and _times() == {}      # what the look listed was before the keeping
    mac.clock += 0.5                                        # a second after that look's listing, half after the keeping
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # the first look since it was kept again
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}


# A look told it is the second takes the lock again to forget the session: what was kept anew, or seen with a process,
# between those two takings is not forgotten on sightings older than that.

@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_session_kept_again_between_a_look_s_yes_and_its_forgetting_is_kept(mac, monkeypatch, by):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.zombies = {SHELL}                                   # over and not yet waited for: ps still gives its start
    assert processes.in_sessions(RUN) == [] and SHELL in _times()       # one look has seen it empty
    mac.clock += processes.EMPTY_AGAIN
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)

    def meanwhile():
        mac.clock += 0.25                                   # a quarter second after the look's ps started
        assert _own(by, THEN)                               # kept again, from the same start
    lock.before[3] = meanwhile                              # before the look's third taking of the lock: its forgetting
    assert processes.in_sessions(RUN) == [] and lock.before == {}
    assert _kept() == {SHELL: THEN} and _times() == {}      # both of the look's sightings were before this keeping
    mac.clock += 0.75                                       # a second after that look's ps started
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # the first look since it was kept again
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}


def test_a_look_that_sees_a_process_between_another_s_yes_and_its_forgetting_keeps_the_session(mac, monkeypatch):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after the first look's ps listed
    assert processes.in_sessions(RUN) == [] and SHELL in _times()       # which sees the session empty
    mac.clock += processes.EMPTY_AGAIN
    mac.start(OTHER, LATER, session=SHELL, parent=LEFT)
    mac.end(LEFT)
    mac.unlisted = {OTHER}                                  # what that one started, born just after the second's listed
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    saw = []

    def meanwhile():
        mac.unlisted = set()
        mac.clock += 0.25                                   # a look whose ps lists a quarter second on sees it,
        saw.append(processes.in_sessions(RUN))              # and says so before the second look's forgetting
    lock.before[3] = meanwhile                              # before the second look's third taking of the lock
    assert processes.in_sessions(RUN) == [] and lock.before == {} and saw == [[OTHER]]
    assert _kept() == {SHELL: THEN} and _times() == {}      # a process was seen in it since that look's ps started
    assert processes.in_sessions(RUN) == [OTHER]            # so what runs in it is the run's to end still


def test_a_look_whose_ps_answers_after_a_sighting_said_between_its_yes_and_its_forgetting_keeps_the_session(
        mac, monkeypatch):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after the first look's ps listed
    assert processes.in_sessions(RUN) == [] and SHELL in _times()
    mac.clock += processes.EMPTY_AGAIN
    mac.start(OTHER, LATER, session=SHELL, parent=LEFT)
    mac.end(LEFT)
    mac.unlisted = {OTHER}                                  # what that one started, born just after the second's listed
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    looks = []

    def while_its_ps_runs():
        mac.unlisted = set()
        mac.clock += 0.25
        looks.append(HeldLook(mac, "start"))                # a look that lists a quarter second on, and sees it
        mac.clock += 0.25                                   # the second look's ps answers a quarter second after that
    mac.before_it_answers["list"] = while_its_ps_runs
    lock.before[3] = lambda: looks[0].go_on()               # the sighting said before the second look's forgetting
    assert processes.in_sessions(RUN) == [] and lock.before == {} and mac.before_it_answers == {}
    assert _kept() == {SHELL: THEN} and _times() == {}      # seen with a process since the second look's ps started
    assert processes.in_sessions(RUN) == [OTHER]


def test_a_sighting_listed_before_the_second_look_began_and_said_before_its_forgetting_keeps_the_session(
        mac, monkeypatch):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after the first look's ps listed
    assert processes.in_sessions(RUN) == [] and SHELL in _times()       # which sees the session empty
    processes._wake.clear()
    mac.unlisted = set()
    mac.clock += 0.5
    early = HeldLook(mac, "start")                          # a look half a second on sees the process, and is held
    mac.clock += 0.5
    mac.start(OTHER, LATER, session=SHELL, parent=LEFT)
    mac.end(LEFT)
    mac.unlisted = {OTHER}                                  # what that one started, born just after the next listing
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    lock.before[3] = early.go_on                            # it says what it saw before the second look's forgetting
    assert processes.in_sessions(RUN) == [] and lock.before == {}
    assert _kept() == {SHELL: THEN} and _times() == {}      # the first look's sighting was dropped: not forgotten
    assert processes._wake.is_set()                         # and, refused with no time kept, it wakes the thread
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [OTHER]            # and what runs in it is the run's to end still


@pytest.mark.parametrize("by", ["own", "own_kept"])
def test_a_look_that_saw_a_process_in_the_session_it_copied_leaves_the_time_of_another_kept_since(mac, by):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    look = HeldLook(mac, "start")                           # a look that saw a process in the old session, held
    try:
        mac.end(LEFT)                                       # the old session over,
        mac.start(SHELL, LATER)                             # its id given again: a process of the run's leads under it
        assert _own(by, LATER)
        mac.end(SHELL)
        mac.clock += 0.25
        assert processes.in_sessions(RUN) == [] and _times() == {SHELL: mac.clock}  # the new one, seen empty once
    finally:
        look.go_on()                                        # the look says what it saw, of the session it copied
    assert _kept() == {SHELL: LATER} and _times() == {SHELL: mac.clock}     # the new one's time is not its to drop
    mac.clock += processes.EMPTY_AGAIN
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}   # a second on: forgotten


def test_nothing_is_left_of_a_session_forgotten_whatever_a_look_that_saw_a_process_in_it_says_after(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL) and set(processes._SINCE) == {(RUN, SHELL)}
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    look = HeldLook(mac, "start")                           # a look that saw a process in it, held
    try:
        mac.end(LEFT)
        assert processes.in_sessions(RUN) == [] and SHELL in _times()
        mac.clock += processes.EMPTY_AGAIN
        assert processes.in_sessions(RUN) == [] and _kept() == {}       # two looks a second apart saw it empty
        assert processes._SINCE == {} and processes._EMPTY == {}
    finally:
        look.go_on()                                        # and the look that saw a process comes to it only now
    assert _kept() == {} and processes._SINCE == {} and processes._EMPTY == {}  # it changes nothing
    mac.start(SHELL, LATER)                                 # so too one forgotten for its leader's other start,
    assert processes.own(RUN, SHELL)
    processes._SESSIONS[RUN][SHELL] = THEN
    assert processes.in_sessions(RUN) == [] and _kept() == {} and processes._SINCE == {}
    assert processes.own(RUN, SHELL)                        # and one forgotten by its id alone
    processes._forget(RUN, SHELL)
    assert _kept() == {} and processes._SINCE == {} and processes._EMPTY == {}


def test_a_look_that_listed_a_process_between_two_empty_looks_and_comes_after_their_forgetting_changes_nothing(
        mac):
    # the order the module's docstring names as left: held here as it stands, not as it should be
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after the first look's ps listed
    assert processes.in_sessions(RUN) == [] and SHELL in _times()       # which sees the session empty
    mac.unlisted = set()
    mac.clock += 0.5
    late = HeldLook(mac, "start")                           # a look half a second on lists the process, and is held
    try:
        mac.start(OTHER, LATER, session=SHELL, parent=LEFT)
        mac.end(LEFT)
        mac.unlisted = {OTHER}                              # what that one started, born just after the next listing
        mac.clock += 0.5
        assert processes.in_sessions(RUN) == [] and _kept() == {}       # a second after the first: empty, forgotten
    finally:
        late.go_on()                                        # only now does the look between them come to the session
    assert _kept() == {} and processes._SINCE == {} and processes._EMPTY == {}
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == []                 # what runs in it is no longer found by its session


def test_a_look_that_lists_a_process_says_so_though_ps_does_not_tell_it_the_leader_s_start(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after the first look's ps listed
    assert processes.in_sessions(RUN) == [] and SHELL in _times()       # which sees the session empty
    mac.unlisted = set()
    mac.clock += 0.5
    mac.unanswered = {"start"}                              # ps lists, and does not answer for a leader's start
    assert processes.in_sessions(RUN) == []                 # a look lists the process: nothing is taken,
    assert _kept() == {SHELL: THEN} and _times() == {}      # and what it saw is said: the first look's time is dropped
    mac.unanswered = set()
    mac.start(OTHER, LATER, session=SHELL, parent=LEFT)
    mac.end(LEFT)
    mac.unlisted = {OTHER}                                  # what that one started, born just after the next listing
    mac.clock += 0.5
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # a second after the first: not forgotten
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [OTHER]            # so what runs in it is the run's to end still


def test_a_look_not_told_the_leader_s_start_that_listed_nothing_in_the_session_says_nothing(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.end(SHELL)
    assert processes.in_sessions(RUN) == [] and _times() == {SHELL: mac.clock}      # a first look sees it empty
    first, kept_at = mac.clock, dict(processes._SINCE)
    mac.clock += 0.5
    mac.unanswered = {"start"}                              # ps lists, and does not answer for a leader's start
    assert processes.in_sessions(RUN) == []                 # nothing listed in the session: nothing said of it
    assert _times() == {SHELL: first} and processes._SINCE == kept_at
    mac.unanswered = set()
    mac.clock += 0.5
    assert processes.in_sessions(RUN) == [] and _kept() == {}           # so a look a second after the first forgets


def test_what_a_look_not_told_the_leader_s_start_says_is_kept_by_when_its_ps_answered(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unanswered = {"start"}                              # ps lists, and does not answer for a leader's start

    def answers_late():
        mac.clock += 0.25                                   # the listing answers a quarter second after it started
    mac.before_it_answers["list"] = answers_late
    assert processes.in_sessions(RUN) == [] and mac.before_it_answers == {}
    assert processes._SINCE == {(RUN, SHELL): mac.clock}    # the sighting is kept by the answer, not by the start


def test_a_look_that_cannot_read_this_agent_s_own_session_takes_nothing_and_says_what_it_listed(mac, monkeypatch):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after the first look's ps listed
    assert processes.in_sessions(RUN) == [] and SHELL in _times()       # which sees the session empty
    mac.unlisted = set()
    mac.clock += 0.5

    def unread(pid):
        if pid == 0:
            raise ProcessLookupError(pid)                   # this agent's own session is not read,
        return mac.getsid(pid)
    monkeypatch.setattr(processes, "os", Os(unread))
    assert processes.in_sessions(RUN) == []                 # so no session is told from it: nothing is taken,
    assert _kept() == {SHELL: THEN} and _times() == {}      # and what the look listed is said all the same
    assert processes._SINCE == {(RUN, SHELL): mac.clock}
    monkeypatch.setattr(processes, "os", Os(mac.getsid))
    mac.start(OTHER, LATER, session=SHELL, parent=LEFT)
    mac.end(LEFT)
    mac.unlisted = {OTHER}                                  # what that one started, born just after the next listing
    mac.clock += 0.5
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # a second after the first: not forgotten
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [OTHER]            # so what runs in it is the run's to end still


# Two looks forget a session only as the manual says: each saw it empty, their listings a second or more apart, and
# no look that has said so by then listed a process in it after the first of the two began its ps.

def test_a_look_that_listed_before_another_saw_a_process_keeps_no_time_after_it(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after the first look's ps listed
    look = HeldLook(mac, "start")                           # that look, which saw the session empty, held
    try:
        mac.unlisted = set()
        mac.clock += 0.5
        assert processes.in_sessions(RUN) == [LEFT]         # a look that listed later sees the process
    finally:
        look.go_on()                                        # and the earlier look goes on after it
    assert _times() == {}                                   # its sighting is older than the process seen
    mac.unlisted = {LEFT}
    mac.clock += 0.5                                        # a second after the earlier look's listing
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # the first empty look since a process was seen
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [LEFT]


def test_two_looks_whose_ps_listed_at_one_moment_do_not_forget_a_session_a_second_between_their_starts(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after both listings
    look = HeldLook(mac, "list")                            # a look whose ps, started now, answers only a second on
    try:
        mac.clock += processes.EMPTY_AGAIN
    finally:
        look.go_on()                                        # it answers: the first look to see the session empty
    assert processes.in_sessions(RUN) == [] and SHELL in _kept()        # a look whose ps lists at that moment: kept
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [LEFT]             # so the next look sees what was born: the run's to end


def test_a_second_look_s_time_is_when_its_ps_started_however_late_it_answers(mac):
    _seen_empty_once(mac)
    mac.clock += 0.5
    look = HeldLook(mac, "list")                            # a look whose ps starts half a second on, and answers late
    try:
        mac.clock += processes.EMPTY_AGAIN
    finally:
        look.go_on()
    assert SHELL in _kept()                                 # its listing may be half a second after the first: kept
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}   # one started a second on: forgotten


# What is kept with a session is the latest answer among the looks that have said they saw a process in it, whichever
# said so last, and when it was kept, read as it is kept; a look is held to when its own ps started.

def test_a_look_that_saw_a_process_earlier_and_says_so_later_does_not_bring_back_the_looks_begun_between(
        mac, monkeypatch):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    early = HeldLook(mac, "start")                          # a look that saw a process, held before it says so
    mac.clock += 0.25
    mac.start(OTHER, LATER, session=SHELL, parent=LEFT)
    mac.end(LEFT)
    mac.unlisted = {OTHER}                                  # born just after the next look's ps listed
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    saw = []

    def meanwhile():
        mac.unlisted = set()
        mac.clock += 0.25
        saw.append(processes.in_sessions(RUN))              # a look that lists later sees a process and says so,
        early.go_on()                                       # and then the earlier one says what it saw
    lock.before[2] = meanwhile                              # before the look between them asks, having seen it empty
    assert processes.in_sessions(RUN) == [] and lock.before == {} and saw == [[OTHER]]
    assert _kept() == {SHELL: THEN} and _times() == {}      # its listing is older than the later sighting: no time


@pytest.mark.parametrize("since", ["a keeping", "a sighting"])
def test_a_look_whose_ps_started_before_and_answered_after_keeps_no_time(mac, since):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    if since == "a keeping":
        mac.zombies = {SHELL}                               # over and not yet waited for: ps still gives its start
    else:
        mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
        mac.end(SHELL)
        mac.unlisted = {LEFT}                               # born just after the look's ps listed
    look = HeldLook(mac, "list")                            # a look whose ps has started, and answers only later
    try:
        mac.clock += 0.5
        if since == "a keeping":
            assert processes.own_kept(RUN, SHELL, THEN)     # kept again meanwhile,
        else:
            mac.unlisted = set()
            assert processes.in_sessions(RUN) == [LEFT]     # or seen with a process by a look that listed meanwhile
        mac.clock += 0.25
    finally:
        look.go_on()                                        # its ps answers after that: what it listed may be older
    assert _kept() == {SHELL: THEN} and _times() == {}


def test_a_look_whose_ps_started_before_a_seeing_look_s_answered_keeps_no_time_after_it(mac):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    looks = []

    def before_its_ps_lists():
        mac.unlisted = {LEFT}                               # born just after the look below listed
        mac.clock += 0.25
        looks.append(HeldLook(mac, "start"))                # a look begun a quarter second on, which sees it empty
        mac.unlisted = set()
        mac.clock += 0.25
    mac.before_it_lists["list"] = before_its_ps_lists       # the ps of a look begun before that one lists only now,
    try:
        assert processes.in_sessions(RUN) == [LEFT] and mac.before_it_lists == {}   # sees the process and says so
    finally:
        looks[0].go_on()                                    # then the look begun before that answer asks
    assert _kept() == {SHELL: THEN} and _times() == {}      # its listing is older than the sighting: no time


def test_a_look_told_it_is_the_second_is_held_to_when_its_ps_started_as_it_forgets_too(mac, monkeypatch):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
    mac.end(SHELL)
    mac.unlisted = {LEFT}                                   # born just after each of the first two looks' ps listed
    assert processes.in_sessions(RUN) == [] and _times() == {SHELL: mac.clock}  # the first sees the session empty
    mac.clock += processes.EMPTY_AGAIN
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    looks = []

    def while_its_ps_runs():                                # the second look's ps has listed, and answers late:
        mac.unlisted = set()
        mac.clock += 0.25
        looks.append(HeldLook(mac, "start"))                # meanwhile a look sees the process, and is held;
        mac.start(OTHER, LATER, session=SHELL, parent=LEFT)
        mac.end(LEFT)
        mac.unlisted = {OTHER}                              # what that one started is born just after the listing
        mac.clock += 0.25
        looks.append(HeldLook(mac, "start"))                # of a look that sees the session empty, held too;
        mac.clock += processes.EMPTY_AGAIN                  # and the second look's ps answers a second after that
    mac.before_it_answers["list"] = while_its_ps_runs

    def before_its_forgetting():                            # the second look is told yes, on the first look's time;
        looks[0].go_on()                                    # then the sighting is said, which drops that time,
        looks[1].go_on()                                    # and the later empty look's time is kept in its place
    lock.before[3] = before_its_forgetting
    later = mac.clock + 0.5                                 # when that later look's ps answered
    assert processes.in_sessions(RUN) == [] and lock.before == {} and mac.before_it_answers == {}
    assert _kept() == {SHELL: THEN} and _times() == {SHELL: later}      # what the second listed is older than both
    mac.unlisted = set()
    assert processes.in_sessions(RUN) == [OTHER]


def test_a_look_begun_while_an_own_waited_for_the_lock_keeps_no_time_for_the_session_it_keeps(mac, monkeypatch):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    mac.zombies = {SHELL}                                   # over and not yet waited for: ps still gives its start
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)
    looks = []

    def meanwhile():
        looks.append(HeldLook(mac, "start"))                # a look begins, and sees the session empty,
        mac.clock += 0.25                                   # a quarter second before the own below has the lock
    lock.before[1] = meanwhile                              # the own has asked the leader's start, and waits
    try:
        assert processes.own_kept(RUN, SHELL, THEN) and lock.before == {}
    finally:
        looks[0].go_on()
    assert _kept() == {SHELL: THEN} and _times() == {}      # it began before the keeping: no time


# A look told no for having begun too early saw the session empty all the same: it wakes the thread that looks, so
# that a look that can count comes a second on, and the session is not kept for the 30 seconds to that thread's next.

@pytest.mark.parametrize("since", ["a keeping", "a sighting"])
def test_a_look_begun_too_early_to_count_wakes_the_thread_that_looks(mac, since):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    if since == "a keeping":
        mac.zombies = {SHELL}                               # over and not yet waited for: ps still gives its start
        look = HeldLook(mac, "list")                        # a look whose ps has started, and answers only later
        try:
            mac.clock += 0.5
            assert processes.own_kept(RUN, SHELL, THEN)     # kept again meanwhile
            mac.clock += 0.25
            assert not processes._wake.is_set()
        finally:
            look.go_on()                                    # it saw the session empty, too early to count
    else:
        mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
        mac.end(SHELL)

        def before_its_ps_lists():
            mac.clock += 0.25
            assert processes.in_sessions(RUN) == [LEFT]     # another look sees the process and says so,
            mac.end(LEFT)                                   # and then the process is over
            mac.clock += 0.25
            assert not processes._wake.is_set()
        mac.before_it_lists["list"] = before_its_ps_lists   # the ps of a look begun before that lists only now:
        assert processes.in_sessions(RUN) == []             # it sees the session empty, too early to count
    assert _kept() == {SHELL: THEN} and _times() == {}
    assert processes._wake.is_set()                         # so the thread that looks is woken,
    processes._wake.clear()
    mac.zombies = set()
    mac.running.pop(SHELL, None)
    mac.clock += processes.EMPTY_AGAIN                      # and its look a second on is the first that counts,
    assert processes.in_sessions(RUN) == [] and SHELL in _times() and processes._wake.is_set()
    mac.clock += processes.EMPTY_AGAIN                      # and the one a second after that forgets the session
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}


@pytest.mark.parametrize("since", ["a keeping", "a sighting"])
def test_a_look_told_it_is_the_second_and_refused_as_it_forgets_wakes_the_thread_that_looks(mac, monkeypatch, since):
    mac.start(SHELL, THEN)
    assert processes.own(RUN, SHELL)
    if since == "a keeping":
        mac.zombies = {SHELL}                               # over and not yet waited for: ps still gives its start
    else:
        mac.start(LEFT, THEN, session=SHELL, parent=SHELL)
        mac.end(SHELL)
        mac.unlisted = {LEFT}                               # born just after each of the two looks' ps listed
    assert processes.in_sessions(RUN) == [] and SHELL in _times()       # the first sees the session empty
    processes._wake.clear()
    mac.clock += processes.EMPTY_AGAIN
    lock = Lock()
    monkeypatch.setattr(processes, "_sessions_lock", lock)

    def meanwhile():                                        # the second look has been told yes; before it forgets,
        mac.clock += 0.25
        if since == "a keeping":
            assert processes.own_kept(RUN, SHELL, THEN)     # the session is kept again,
        else:
            mac.unlisted = set()
            assert processes.in_sessions(RUN) == [LEFT]     # or a look lists the process and says so,
            mac.end(LEFT)                                   # and the process is over
        assert not processes._wake.is_set()
    lock.before[3] = meanwhile
    assert processes.in_sessions(RUN) == [] and lock.before == {}
    assert _kept() == {SHELL: THEN} and _times() == {}      # refused: the session is kept, and no time is
    assert processes._wake.is_set()                         # so the thread that looks is woken,
    processes._wake.clear()
    mac.zombies = set()
    mac.running.pop(SHELL, None)
    mac.clock += processes.EMPTY_AGAIN                      # and its look a second on is the first that counts,
    assert processes.in_sessions(RUN) == [] and SHELL in _times() and processes._wake.is_set()
    mac.clock += processes.EMPTY_AGAIN                      # and the one a second after that forgets the session
    assert processes.in_sessions(RUN) == [] and _kept() == {} and _times() == {}


def test_a_look_wakes_no_thread_for_a_session_kept_since_under_its_id_from_another_start(mac):
    _seen_empty_once(mac)                                   # the old session, seen empty once
    processes._wake.clear()
    mac.clock += processes.EMPTY_AGAIN
    look = HeldLook(mac, "start")                           # a look told the old leader is gone, and held there
    try:
        mac.start(SHELL, LATER)                             # the id given again: a process of the run's leads under it
        assert processes.own(RUN, SHELL)
    finally:
        look.go_on()
    assert _kept() == {SHELL: LATER} and _times() == {} and not processes._wake.is_set()    # not the one it copied


# The module's own thread, which the other cases turn off: here it runs, on the machine's clock and its real second.

def test_the_module_s_own_thread_looks_a_second_after_a_first_empty_look_and_its_look_is_the_second(mac, monkeypatch):
    monkeypatch.setattr(processes, "time", time)            # the machine's clock, and its sleep
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    looked = []

    def prune():
        if threading.current_thread() is not thread:
            return None                                     # a thread a Mac's own cases left running looks at nothing
        try:
            mac.prune()                                     # the module's own look at every run's sessions,
            looked.append(time.monotonic())
        finally:
            raise SystemExit                                # once: the end of this case's thread
    thread = threading.Thread(target=processes._prune_always, daemon=True)     # named before any thread can ask
    monkeypatch.setattr(processes, "prune", prune)
    mac.mine.add(thread)
    thread.start()
    try:
        mac.start(SHELL, THEN)
        assert processes.own(RUN, SHELL)
        mac.end(SHELL)
        before = time.monotonic()
        assert processes.in_sessions(RUN) == []             # the first look to see it empty wakes the thread
        thread.join(10)
        assert not thread.is_alive() and _kept() == {} and _times() == {}   # the thread's look was the second
        assert processes.EMPTY_AGAIN <= looked[0] - before < 5
    finally:
        if thread.is_alive():                               # the case failed before that look: its thread is woken,
            processes._wake.set()                           # and ends at its next look, which its prune above makes
            thread.join(5)                                  # its last
