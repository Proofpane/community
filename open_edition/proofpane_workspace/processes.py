"""A run's processes, found by the mark in their environment and ended with the run.

Everything a run's claude starts carries PP_AGENT_RUN_ID=<run> in its environment, whatever it does to leave the
claude's session or its process group: measured with Claude Code 2.1.274 in the workspace image, a Bash command's
`setsid nohup sleep &`, `(sleep &)` and `nohup sleep &` each outlived the claude that started them, each with the mark.
So the run's end (and its wait for the owner, when it holds no process) ends every process still carrying it. One that
drops the mark from its environment (`env -i`, `env -u PP_AGENT_RUN_ID`, an exec with an environment of its own) is
not found by it: the manual says so.

A Mac keeps no /proc, and the mark is read there from what `ps -E` prints, which shows no environment of macOS's
platform binaries (`/bin/sleep`, `/bin/sh`, `/bin/zsh`, `/bin/bash`, `/usr/bin/perl`), nor of a program whose runtime
rewrites what ps reads when it sets its title (node's `process.title`, `npm exec`: measured on node alone), while
another program shows it (Xcode's Python, which `/usr/bin/python3` runs, and Homebrew's): measured on macOS 15.7.9,
what a run leaves running with one of those carries the mark unseen.
So on a Mac a run's claude, which its wrapper says it is (chat.WRAPPER, reported), and its precheck (start) each lead a
session of their own, which the run owns. The claude's is taken only while the claude shows ps its mark: 2.1.274 sets
its title as it starts (`process.title="claude"`, read in its code) through Bun, whose setter leaves what ps reads, and
the claudes running on the Mac this was built on, 2.1.296, which set it the same way, show their environment; 2.1.274
itself was not run, and a claude whose runtime rewrote what ps reads would lose its session, with nothing to say so.
Each look at the run's sessions takes as the run's too every process still running that one of their processes started,
or that such a one started in turn, wherever it is now, the session such a process leads and what is in it (a Bash call
or a command hook while it runs, which Claude Code starts in a session of its own, detached, as read in 2.1.274's code).
A "child" that ps says is being traced, or that began before its parent, is not taken so: a debugger attached to a
process may be shown as its parent, from a recollection of XNU's ptrace source (none was read here, nor measured);
and a clock set back between two starts makes a real child look older than its parent, which is then not
taken either. The run's end ends every process in one of its sessions, whatever its program, one its own look takes
among them, `env -i`'s and a process group of its own too. A task's attempt's precheck starts as before (its attempt has
its own way, task_runs.py), and its end reads the mark through the same /bin/ps. Out of the agent's own session and
group, a signal to the agent's group (a terminal's interrupt or hangup) no longer reaches them there, and the agent's
terminal is not theirs, so a precheck or an MCP server that asks on /dev/tty (a password, a host's key) fails at once:
the agent ends them at the run's end and its wait, but not at its own stop, so a precheck running when an agent run from
a terminal is interrupted runs on, and what it left is found after by its mark alone (the local provider starts the
agent in a session of its own and signals it alone, so there nothing changes). What leaves the run's sessions unseen is
not reached so: a program's own setsid() once its parent has gone, and what a Bash call or a hook leaves when no look
saw it running, as is likely for one that runs less than half a minute; a process ps shows no environment of is ended
there by nothing here. (A Bash call still running when the run ends is ended by Claude Code itself as the run stops it:
2.1.274's shutdown ends each running command's process group, as read in its code, not run; it passes over one put in
the background. A Mac runner that reaches a Bash call's process group through Claude Code's shell prefix is not built
for a run.) The claude's MCP servers stay in its session and are reached, once it has connected (a stop while
it connects takes no session). ps is /bin/ps, in a fixed environment (UTC, the C locale), so that neither a `ps` on PATH
nor this agent's time zone or language changes what it says, and each call is waited for 20 seconds at most, so that a
look can take a minute or more while ps hangs. A session is taken only when ps answers: one a precheck or a claude leads
when ps does not answer at its start, or as the claude connects, is never the run's, and what is left in it is found by
its mark alone, with nothing to say so. A session is forgotten once two looks whose ps listed a second or more apart
have each seen it empty (a look's ps lists no process born after it), or once its leader runs from another start than
the one kept. The kept sessions are looked at so every 30 seconds while any is kept, and a second after a look first
sees one empty. So an id given again, which takes the pid counter coming round, is taken for the run's only if, between
the session's emptying and the look that forgets it, the counter comes round to it, and the process given it leads a
session of its own and has ended leaving another in it: a second or so after a run's end or its wait, whose own look
sees the claude's session empty at once, and up to some 31 seconds for a session that empties between looks, or longer
while ps does not answer. At the 78 ids a second measured on that Mac, the counter comes to a given
id within a second about once in 1,300 times, and within 31 seconds about once in 40. What such a session's processes
started, and the sessions they lead, are then the run's too, and stay so after it is forgotten. The sessions are kept in
this process's memory: once the agent has stopped and started again, the mark alone finds what a run left. Linux reads
each process's environment in /proc, where the mark shows whatever the program, and keeps no session.
"""

import calendar
import os
import signal
import stat
import subprocess
import sys
import threading
import time

MARK = "PP_AGENT_RUN_ID"
PRUNE_EVERY = 30                    # seconds between two looks at the kept sessions, while any is kept
EMPTY_AGAIN = 1                     # seconds at least between the two looks that see a session empty to forget it
PS_ENV = {"PATH": "/bin:/usr/bin", "TZ": "UTC0", "LC_ALL": "C"}

_SESSIONS = {}                      # on a Mac: run id -> {session id: its leader's start, as ps gives it}
_EMPTY = {}                         # on a Mac: (run id, session id) -> when a look first saw it empty (monotonic)
_sessions_lock = threading.Lock()
_pruner = []                        # the thread that looks at the kept sessions, once one is kept
_wake = threading.Event()           # set by a look that first sees a session empty: that thread looks a second on
UNKNOWN = object()                  # ps did not answer: nothing is taken on its word


def _ps(*args, text=True):
    """/bin/ps run with `args`, by its absolute path and in a fixed environment (UTC, the C locale), so that neither a
    `ps` on PATH nor this agent's time zone or language changes what it says; None when it could not be run."""
    try:
        return subprocess.run(["/bin/ps", *args], capture_output=True, text=text, timeout=20, env=PS_ENV)
    except (OSError, subprocess.SubprocessError):
        return None


def marked(run_id, proc="/proc", among=None):
    """The ids of the processes carrying the run's mark, but this one and its parent (of `among` only, when given)."""
    entry = f"{MARK}={run_id}".encode()
    mine = {os.getpid(), os.getppid()}
    found = []
    if os.path.isdir(os.path.join(proc, "self")):
        for name in (os.listdir(proc) if among is None else [str(p) for p in among]):
            if not name.isdigit() or int(name) in mine:
                continue
            try:
                with open(os.path.join(proc, name, "environ"), "rb") as fh:
                    env = fh.read()
            except OSError:
                continue
            if entry in env.split(b"\0"):
                found.append(int(name))
        return found
    if sys.platform == "darwin":
        # macOS keeps no /proc: ps shows the environment of the user's own processes after their command line
        p = _ps("-axwwE", "-o", "pid=,command=", text=False)
        if p is None:
            return found
        for line in p.stdout.splitlines():
            pid, _, rest = line.strip().partition(b" ")
            if pid.isdigit() and int(pid) not in mine and entry in rest.split(b" ") \
                    and (among is None or int(pid) in among):
                found.append(int(pid))
    return found


def _started(pid):
    """When a process started, as ps gives it (to the second); None for one that is gone; UNKNOWN when ps did not
    answer."""
    p = _ps("-o", "lstart=", "-p", str(pid))
    if p is None or p.returncode not in (0, 1):
        return UNKNOWN
    return p.stdout.strip() or None


def _not_ours(sid):
    """A session the run may never own: none at all, launchd's, this agent's own or its parent's."""
    try:
        return sid <= 1 or sid in (os.getsid(0), os.getsid(os.getppid()))
    except OSError:
        return True


def own(run_id, sid):
    """On a Mac, the session `sid`, which a process of the run leads (its precheck's shell, its claude, one a process
    of the run's started), ended with the run whatever is in it. Its leader's start is kept with it, so that a leader
    started at another time is another's. Whether it was taken: never elsewhere, nor this agent's own session or its
    parent's, nor one whose leader is gone already, nor while ps does not answer."""
    if sys.platform != "darwin" or _not_ours(sid):
        return False
    start = _started(sid)
    if start is None or start is UNKNOWN:
        return False
    with _sessions_lock:
        _SESSIONS.setdefault(run_id, {})[sid] = start
        if not _pruner:
            _pruner.append(threading.Thread(target=_prune_always, name="processes-prune", daemon=True))
            _pruner[0].start()
    return True


def own_kept(run_id, sid, start):
    """On a Mac, the session `sid` owned for the run, as own() owns one, where its leader runs from `start`, the start
    kept when it began (a controller's check, which a restart may have left running): own() takes no session whose
    leader is gone, and this takes none whose leader began at
    another time, whose id was given again. Whether it was taken."""
    if sys.platform != "darwin" or not isinstance(start, str) or _not_ours(sid):
        return False
    if _started(sid) != start:
        return False
    with _sessions_lock:
        _SESSIONS.setdefault(run_id, {})[sid] = start
        if not _pruner:
            _pruner.append(threading.Thread(target=_prune_always, name="processes-prune", daemon=True))
            _pruner[0].start()
    return True


def reported(run_id, path):
    """On a Mac, the session the run's claude leads, as the wrapper wrote which process it is to `path` before it
    started it (chat.WRAPPER): taken as the run's when that process carries the run's mark and leads its session. Its
    id, or None. The run's own programs can write in its folder, so the file is read as one the run may have put
    there: never through a link, never one that is not a plain file (a FIFO would block the read), and no more of it
    than a process's id."""
    if sys.platform != "darwin":
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        text = os.read(fd, 32).decode("ascii", "replace").strip()
    except OSError:
        return None
    finally:
        os.close(fd)
    if not text.isdigit():
        return None
    pid = int(text)
    try:
        leads = os.getsid(pid) == pid
    except (OSError, OverflowError):
        return None
    return pid if leads and pid in marked(run_id, among=[pid]) and own(run_id, pid) else None


def start(argv, run_id, session=True, **popen):
    """A program the run runs outside its claude (its precheck), started as subprocess.Popen starts it: on a Mac, with
    `session`, in a session of its own, which the run owns, so that what it leaves is ended with the run whatever its
    program; else as Popen starts it. When ps does not answer, it runs on in a session the run does not own (own()
    says why); when keeping its session raises, it is ended with its group before the error goes on."""
    if sys.platform != "darwin" or not session:
        return subprocess.Popen(argv, **popen)
    p = subprocess.Popen(argv, start_new_session=True, **popen)
    try:
        own(run_id, p.pid)
    except BaseException:
        # its session not kept, nothing but the mark would end what it starts: it is ended now with its group, its
        # pipes closed and it waited for, before the error goes on
        with p:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        raise
    return p


def _forget(run_id, sid):
    with _sessions_lock:
        _EMPTY.pop((run_id, sid), None)
        kept = _SESSIONS.get(run_id)
        if kept is not None:
            kept.pop(sid, None)
            if not kept:
                del _SESSIONS[run_id]


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _utc(begun):
    """A start as ps gives it in UTC and the C locale (`Sun Oct 18 12:10:20 2026`), in seconds; None when it is not
    one. Read without the locale's names, whatever this agent's own locale."""
    words = (begun or "").split()
    try:
        _, month, day, clock, year = words
        hour, minute, second = (int(w) for w in clock.split(":"))
        return calendar.timegm((int(year), MONTHS.index(month) + 1, int(day), hour, minute, second))
    except ValueError:
        return None


def _not_before(child, parent):
    """Whether a process that began at `child` began no earlier than one that began at `parent` (each as ps gives
    it); not when either cannot be read."""
    a, b = _utc(child), _utc(parent)
    return a is not None and b is not None and a >= b


def _empty_twice(run_id, sid, listed):
    """Whether a look whose ps listed EMPTY_AGAIN seconds or more before this one's (`listed`) saw the session empty
    too, and none since saw a process in it: a look's ps lists no process born after it, so one look alone never
    forgets a session. A look that is the first to see it so wakes the thread that looks, to look again a second on,
    so that an emptied session is not kept for the 30 seconds to its next look."""
    with _sessions_lock:
        first = _EMPTY.get((run_id, sid))
        if first is None:
            _EMPTY[(run_id, sid)] = listed
    if first is None:
        _wake.set()
        return False
    return listed - first >= EMPTY_AGAIN


def in_sessions(run_id, among=None):
    """On a Mac, the ids of the run's processes its sessions show, but this one and its parent (of `among` only, when
    given): every process in one of its sessions, and every one still running that one of those started, or that such
    a one started in turn, wherever it is now; a session one of those leads is the run's from then on. A session two
    looks a second or more apart have seen empty is forgotten, never to be the run's again, and so is one whose leader
    runs with another start than the one kept: its id was given again. While ps does not answer, nothing is taken."""
    with _sessions_lock:
        kept = dict(_SESSIONS.get(run_id, {}))
    if sys.platform != "darwin" or not kept:
        return []
    listed = time.monotonic()           # when this look's ps lists, the time two looks are held apart by
    p = _ps("-ax", "-o", "pid=,ppid=,stat=,lstart=")
    if p is None or p.returncode != 0:
        return []
    mine = {os.getpid(), os.getppid()}
    session, children, begun, traced, in_session = {}, {}, {}, set(), {}
    for line in p.stdout.splitlines():
        words = line.split()
        if len(words) < 3 or not words[0].isdigit() or not words[1].isdigit() or int(words[0]) in mine:
            continue
        try:
            session[int(words[0])] = os.getsid(int(words[0]))
        except OSError:                 # gone since, or a zombie, whose session macOS no longer gives
            continue
        children.setdefault(int(words[1]), []).append(int(words[0]))
        begun[int(words[0])] = " ".join(words[3:])
        if "X" in words[2]:             # ps's "being traced or debugged"
            traced.add(int(words[0]))
        in_session.setdefault(session[int(words[0])], []).append(int(words[0]))
    members = {sid: in_session.get(sid, []) for sid in kept}
    found = set()
    for sid, start in kept.items():
        now = _started(sid)
        if now is UNKNOWN or _not_ours(sid):
            continue
        if now is not None and now != start:
            _forget(run_id, sid)        # its leader runs from another start: its id was given again
        elif members[sid]:
            with _sessions_lock:
                _EMPTY.pop((run_id, sid), None)
            found.update(members[sid])
        elif _empty_twice(run_id, sid, listed):
            _forget(run_id, sid)
    # what those started and still runs, wherever it went (a Bash call's, a hook's, in a session of its own), and what
    # is in a session such a one leads; never a "child" being traced, or that began before its parent, as one a
    # debugger has attached to may be shown as the debugger's child
    todo = list(found)
    while todo:
        parent = todo.pop()
        for pid in children.get(parent, ()):
            if pid in found or pid in traced or not _not_before(begun.get(pid), begun.get(parent)):
                continue
            found.add(pid)
            todo.append(pid)
            if session[pid] == pid and pid not in kept:
                own(run_id, pid)
                for member in in_session.get(pid, ()):     # in the session it leads: the run's, ended by this look too
                    if member not in found:
                        found.add(member)
                        todo.append(member)
    return sorted(pid for pid in found if among is None or pid in among)


def prune():
    """Look at every run's kept sessions, forgetting those seen empty or whose id was given again (in_sessions)."""
    with _sessions_lock:
        runs = list(_SESSIONS)
    for run_id in runs:
        in_sessions(run_id)


def _prune_always():
    while True:
        if _wake.wait(PRUNE_EVERY):     # a look first saw a session empty: look again a second on
            _wake.clear()
            time.sleep(EMPTY_AGAIN)
        try:
            prune()
        except Exception:  # noqa: BLE001  the next look tries again
            pass


def _found(run_id, among=None):
    """The run's processes: those carrying its mark, and on a Mac those in its own sessions."""
    pids = marked(run_id, among=among)
    if sys.platform != "darwin":
        return pids
    return sorted(set(pids) | set(in_sessions(run_id, among=among)))


def end(run_id, wait=2.0):
    """End the run's processes: asked to stop, then, after `wait` seconds, made to. How many there were."""
    pids = _found(run_id)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + wait
    left = list(pids)
    while left and time.monotonic() < deadline:
        time.sleep(0.05)
        left = [p for p in left if _alive(p)]
    # read again before the kill: one that ended in the wait may have its id taken by another process since
    for pid in _found(run_id, among=left) if left else []:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return len(pids)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                # a process that ended but its parent has not reaped yet is a zombie: gone
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True
