import atexit
import errno
import hashlib
import os
import re
import signal
import stat
import subprocess
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))


def manual_or_skip():
    """The manual's path. A test that reads back what the manual states is skipped, saying why, where the tree does
    not hold the manual."""
    path = os.path.join(ROOT, "MANUAL.md")
    if not os.path.exists(path):
        pytest.skip("MANUAL.md is not in this tree, so what the manual states cannot be read back here")
    return path
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SECRET = "t" * 40
ADMIN = "admin@example.com"

# The coding agents no test may reach on the machine the tests run on: they are a person's own and signed in, perhaps
# through the login keychain whatever HOME a test gives them (Claude Code on a Mac). Before any test runs, each one found
# on the PATH is shadowed by a tripwire that notes the call and fails; a test that needs one puts a stand-in of its own
# first on its PATH, as the tests do. The run fails if any test reached one. Under pytest-xdist a worker uses the
# tripwires the controller made, which are first on the PATH it inherited and log where the controller looks once every
# worker is done: a worker's own failure never reaches the run's status, so a worker that kept its own would fail open.
REAL_AGENTS = ("claude", "codex", "gemini", "opencode", "grok", "qwen", "kimi", "mcode")
TRIPWIRES = {}
TRIPWIRES_ENV = "PP_TEST_TRIPWIRES"     # the folder the run made them in, for pytest-xdist's workers
TRIPWIRE = """#!/bin/sh
printf '%s\\n' "{name} $*" >> '{log}'
echo 'a test reached the real {name} on the PATH: give it a stand-in of its own' >&2
exit 97
"""


# Claude Code's records on the machine the tests run on: a file for each of its running processes in sessions/, and each
# session's transcript under projects/, in ~/.claude or in $CLAUDE_CONFIG_DIR. ade_link.py reads them in the home the
# password database gives, whatever $HOME says (_take_sessions_dir, on purpose: a program in a session could set $HOME),
# and in $CLAUDE_CONFIG_DIR and $HOME's .claude too (_claude_folders, _projects_root), so a test left to it reads the
# records of whoever runs the tests, and can bind the Claude Code session running them into a file of its own. So each
# test points ade_link.py's at a folder of its own (own_claude_records), where a test that needs a record writes it, and
# runs without the run's own values of the variables through which ade_link.py would find the session that runs the
# tests, or post into it (no_claude_code_session).
#
# The guard keeps the records where they are as the run starts: in the password database's home's .claude, in $HOME's,
# and in $CLAUDE_CONFIG_DIR. In this process's main interpreter, from any of its threads and until the process ends, it
# stops each of these on them before it happens, whatever the test did with the error: an open (open, os.open,
# sqlite3.connect, and a file action of os.posix_spawn or os.posix_spawnp that opens a file for the program it starts);
# a listing, a walk or a glob (os.listdir and os.scandir with no folder named list the working folder); a look-up
# (os.stat, os.lstat, os.access, os.readlink, os.statvfs, os.pathconf, os.getxattr, os.listxattr, and what is built on
# them, such as os.path.exists), and two things whose error tells as much: a start of a program there, by its path or by
# a bare name looked up in a PATH that names them (subprocess, the os.exec functions, os.posix_spawn, os.posix_spawnp,
# whose PATH is the one the C library holds, followed through os.putenv and os.unsetenv as HOME is), and a connection or
# a datagram to a Unix socket there (socket's connect, connect_ex, sendto and sendmsg); a change of the working folder
# into them, by this process or by a program as it starts (subprocess's cwd, whoever starts the program); each change of
# a file, a folder or a link there that Python audits (a Unix socket's bind among them), and os.mkfifo and os.mknod,
# which it does not; and a removal, rename or move of a folder above them, which would take them with it. It stops every
# change of a process's root by os.chroot, wherever it leads: from another root it could no longer tell what a path, the
# working folder or a descriptor reaches. A path is judged by its text, from the working folder or from a descriptor (on
# Linux and macOS), and then as the system walks it: through any symbolic link (Linux's /proc/self ones among them), and
# by each folder's device and inode as well as its name. For that walk the guard looks up each part of the path with the
# real os.lstat and os.readlink, up to the records' folders and never into them; as the run starts it looks up the
# folders themselves, and nothing in them. A part it cannot look up, and each after it until a .. leaves it, is kept as
# written (a removed folder, which Linux names as its path and " (deleted)", for one). It follows at most the 40 links
# Linux follows in one path, and takes a path through more as reaching nothing past them (macOS follows 32: there a path
# through 33 to 40 links to them is stopped, though the system would refuse it). A relative path that climbs with ..
# from a folder whose path cannot be told (a working folder that was removed, which holds nothing, for one) is stopped
# wherever it leads. Making a symbolic link to them touches nothing; a path through one does. An audited operation whose
# event does not say whether it follows its last link (os.utime, os.chmod, os.chown, os.chflags, os.link, os.getxattr,
# os.listxattr, os.setxattr, os.removexattr, shutil.copyfile, shutil.copymode and shutil.copystat given
# follow_symlinks=False) is taken as following it. Not judged: a look-up through an entry of their parent's listing
# (DirEntry.stat), which reaches the two folders and nothing in them; a call that goes around os's functions (the posix
# module's own stat, lstat, access, readlink, mkfifo, mknod, statvfs, pathconf and chroot, the file actions of its
# posix_spawn and posix_spawnp and the PATH the latter looks in, its open given a descriptor, a C module's own opening
# of a file, as readline's history functions do, or the C library's through ctypes), and what os.read or os.fstat does
# with a descriptor got so; a descriptor whose path cannot be told (on Linux where no /proc is mounted, for one), and a
# path with no .. from one; an audited operation not named here (a library ctypes loads from there, for one); code run
# in another interpreter of this process (one the private _interpreters module makes, for one), where its hook and
# wrappers are not; a path through one of the folders that leaves it again by .. (sessions/..), which, as a listing of
# their parent does, tells only whether the folder is there; a file SQLite opens itself, such as one an ATTACH names; a
# hard link to a record made before the run; and a folder made after the run starts, by another name than its own (a
# case on macOS, a mount).
#
# The guard also stops a test from starting a program whose environment holds no HOME, since such a program takes the
# password database's home as its ~ (os.path.expanduser falls back to it); own_home is a home of the test's own to give
# one. A program given no environment gets the one the C library holds, which the guard follows through each os.putenv
# and os.unsetenv of HOME (os.environ's own changes make them too; one through ctypes, the C library's own, it does not
# see). A start is a test's when the nearest frame outside the standard library, installed packages (site-packages,
# dist-packages), code with no file of its own (<frozen ...>, <string>) and the guard's own wrappers (each stands for
# its caller's) is in a file of the tests other than this one, and, while a test runs, when there is no such frame (a
# thread or an executor given subprocess.run to run). A process this one forks (os.spawnv, os.fork, pty.spawn,
# multiprocessing's fork) is judged until it calls exec, that exec too, as are those it forks.
#
# A stop fails the test that was running at its teardown (for a thread that outlives its test, the test running then). A
# stop no test can be failed for fails the run, as a reached agent does: one outside any test, as a module is collected
# or in a plugin's hook; one after a test's report is made; one in a forked process after its test's report; and one in
# a cleanup of the run's configuration or a handler registered with atexit after the guard's own. Each is said at the
# session's end, or, after that, as the process ends (_at_exit, which then ends it failing); one later than that handler
# is not said and does not fail the run, though one in a handler registered before the guard's is still stopped. A
# program a test starts is not seen (a multiprocessing process started by spawn or forkserver is one): one that runs
# ade_link.py reads the records whatever HOME it is given.
CLAUDE_RECORDS = ("sessions", "projects")
GUARD_ALSO = "PP_TEST_GUARD_ALSO"       # a home whose records are guarded too, for the guard's own tests: it only adds
SESSION_VARIABLES = ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR",
                     "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN")
TOUCHED = "touched Claude Code's records of whoever runs the tests"
NO_HOME = "started a program whose environment holds no HOME"
CHANGED_ROOT = "changed a process's root, from where the guard cannot tell what a path reaches"
FORKED = "in a process it forked"
STOPPED = "The guard of tests/conftest.py stopped this test:"
STOPPED_OUTSIDE = "Where no test could be failed for it, the guard of tests/conftest.py stopped:"
GUARD = {"folders": (), "inodes": frozenset(), "above": frozenset(), "runner": {}, "test": None, "hits": {},
         "log": None, "forks": None, "pid": None, "on": False, "hooked": False, "at_exit": False, "exitstatus": None,
         "c_home": True, "c_path": None}
_LIBRARY = os.path.dirname(os.__file__) + os.sep
_INSTALLED = (f"{os.sep}site-packages{os.sep}", f"{os.sep}dist-packages{os.sep}")
_SUPPORTS = (os.supports_dir_fd, os.supports_fd, os.supports_follow_symlinks, os.supports_effective_ids)
_MAX_LINKS = 40                         # the symbolic links Linux follows in one path (macOS follows 32)
# How an operation's path names what it acts on: what the path leads to, its last symbolic link followed (an open, a
# look-up, a change of what is there); the entry it names, its last link not followed (a removal, a creation, the
# entry a rename replaces); or that entry or a folder above the records, which a removal, rename or move takes with it
_IN, _ENTRY, _ABOVE = "leads into", "names", "names or is above"
# os's functions the guard wraps, with how their path names what they act on: Python audits none of them but os.open,
# whose event lacks the descriptor a relative path is from
_WRAPPED = {"stat": _IN, "lstat": _ENTRY, "access": _IN, "readlink": _ENTRY, "open": _IN, "mkfifo": _ENTRY,
            "mknod": _ENTRY, "statvfs": _IN, "pathconf": _IN}
_SPAWNING = ("posix_spawn", "posix_spawnp")     # their file actions open files in the new process, unaudited
# os's functions the guard stops whatever they are given: after a change of root it could no longer tell what a path,
# the working folder or a descriptor reaches (os.chroot, which Python does not audit)
_REFUSED = {"chroot": CHANGED_ROOT}
_WRAPPER_CODES = set()                  # the code of the guard's own wrappers: a frame of one stands for its caller's
_IN_OS_OPEN = threading.local()         # os.open's wrapper judged this call: its event is not judged again
_IN_SPAWNP = threading.local()          # os.posix_spawnp's wrapper judged its program: its event's is not judged again
_LSTAT = getattr(os.lstat, "__wrapped__", os.lstat)             # the real ones, for the guard's own walk of a path
_READLINK = getattr(os.readlink, "__wrapped__", os.readlink)
# the audited operations that take a path: (where the path is, where a descriptor it is relative to is or None, how it
# names what the operation acts on)
_PATH_EVENTS = {
    "open": ((0, None, _IN),), "sqlite3.connect": ((0, None, _IN),), "os.listdir": ((0, None, _IN),),
    "os.scandir": ((0, None, _IN),), "os.walk": ((0, None, _IN),), "os.fwalk": ((0, 4, _IN),),
    "glob.glob/2": ((0, 3, _IN),), "os.chdir": ((0, None, _IN),), "os.mkdir": ((0, 2, _ENTRY),),
    "os.remove": ((0, 1, _ENTRY),), "os.rmdir": ((0, 1, _ENTRY),), "os.rename": ((0, 2, _ABOVE), (1, 3, _ENTRY)),
    "os.link": ((0, 2, _IN), (1, 3, _ENTRY)), "os.symlink": ((1, 2, _ENTRY),), "os.truncate": ((0, None, _IN),),
    "os.utime": ((0, 3, _IN),), "os.chmod": ((0, 2, _IN),), "os.chown": ((0, 3, _IN),),
    "os.chflags": ((0, None, _IN),), "os.listxattr": ((0, None, _IN),), "os.getxattr": ((0, None, _IN),),
    "os.setxattr": ((0, None, _IN),), "os.removexattr": ((0, None, _IN),),
    "shutil.copyfile": ((0, None, _IN), (1, None, _IN)), "shutil.copymode": ((0, None, _IN), (1, None, _IN)),
    "shutil.copystat": ((0, None, _IN), (1, None, _IN)), "shutil.copytree": ((0, None, _IN), (1, None, _ENTRY)),
    "shutil.move": ((0, None, _ABOVE), (1, None, _IN)), "shutil.rmtree": ((0, 1, _ABOVE),),
    "shutil.make_archive": ((0, None, _ENTRY), (2, None, _IN)),
    "shutil.unpack_archive": ((0, None, _IN), (1, None, _IN)),
    "tempfile.mkstemp": ((0, None, _ENTRY),), "tempfile.mkdtemp": ((0, None, _ENTRY),),
    "socket.bind": ((1, None, _ENTRY),),        # a Unix socket's file
    "socket.connect": ((1, None, _IN),), "socket.sendto": ((1, None, _IN),), "socket.sendmsg": ((1, None, _IN),),
}
_HERE_WHEN_NONE = {"os.listdir", "os.scandir", "os.listxattr"}      # with no folder named: the working folder
# the audited starts of a program: (where the environment it is given is, None for the process's own; what to name).
# On Linux and macOS os.spawnv and its like fork and then exec, so their start is the child's exec. Each start's program
# is judged too, where its event names one (all but os.system's), and subprocess.Popen's working folder
_START_EVENTS = {"subprocess.Popen": (3, 1), "os.exec": (2, 0), "os.posix_spawn": (2, 0), "os.system": (None, 0)}


def _password_home():
    """The home the password database gives this user, as ade_link.py's _take_sessions_dir reads it; else None."""
    try:
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError):
        return None
    return home if isinstance(home, str) and os.path.isabs(home) else None


def _said(value):
    try:
        return os.fsdecode(os.fspath(value))[:300]
    except TypeError:
        return repr(value)[:300]


def _is_fd(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _fd_path(fd):
    """The path an open descriptor names (Linux's /proc, macOS's F_GETPATH); else None."""
    try:
        if sys.platform == "darwin":
            import fcntl
            return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]) or None
        return _READLINK(f"/proc/self/fd/{fd}")
    except (OSError, ValueError, OverflowError, AttributeError, TypeError):
        return None


def _absolute(path, dir_fd=None):
    """The absolute path `path` names, as written: an open descriptor's own; a relative path from the folder the
    descriptor `dir_fd` names, else from the working folder; None where that cannot be told (a Unix socket's abstract
    name among them)."""
    if _is_fd(path):
        text = _fd_path(path)
    else:
        try:
            text = os.fsdecode(os.fspath(path))
        except (TypeError, ValueError):
            return None
        if not os.path.isabs(text):
            if _is_fd(dir_fd) and dir_fd >= 0:
                base = _fd_path(dir_fd)
            else:
                try:
                    base = os.getcwd()
                except OSError:
                    base = None
            text = os.path.join(base, text) if base else None
    if not text or not os.path.isabs(text) or "\0" in text:
        return None
    return text


def _named(where, above):
    """Whether the absolute path `where`, by its text alone, is one of the guarded folders or in one; with `above`, or
    a folder above one."""
    for folder in GUARD["folders"]:
        if where == folder or where.startswith(folder + os.sep):
            return True
        if above and folder.startswith(where.rstrip(os.sep) + os.sep):
            return True
    return False


def _walk(text, follow, stop=None):
    """The absolute path `text` as the system walks it: [(the name of each part of where it leads, (st_dev, st_ino) of
    it)], each symbolic link on the way followed, the last one only with `follow` or with a separator after it. Each
    part is looked up with the real os.lstat and each link read with the real os.readlink, Linux's /proc/self links
    among them; a part that cannot be looked up, and each after it until a .. leaves it, is kept as written, with None
    (a removed folder, which Linux names as its path and " (deleted)", for one). None, with nothing more looked up, as
    soon as `stop`, given the parts walked so far, says the next part would be in a folder not to look in."""
    follow = follow or text.endswith(os.sep) or text.endswith(os.sep + os.curdir)
    pending = [part for part in reversed(text.split(os.sep)) if part and part != os.curdir]
    walked, links, lost = [], 0, False
    while pending:
        part = pending.pop()
        if part == os.pardir:
            if walked:
                walked.pop()
            lost = any(key is None for _, key in walked)
            continue
        if stop is not None and stop(walked):
            return None
        here = os.sep + os.sep.join([name for name, _ in walked] + [part])
        found = None
        if not lost:
            try:
                found = _LSTAT(here)
            except (OSError, ValueError):
                lost = True
        if found is not None and stat.S_ISLNK(found.st_mode) and (pending or follow):
            if links >= _MAX_LINKS:     # where the system gives up (ELOOP): past it nothing is reached or looked at
                lost = True
            else:
                links += 1
                try:
                    target = os.fsdecode(_READLINK(here))
                except (OSError, ValueError):
                    target = ""
                if target:
                    if os.path.isabs(target):
                        walked = []
                    pending.extend(each for each in reversed(target.split(os.sep)) if each and each != os.curdir)
                    continue
                lost, found = True, None
        walked.append((part, None if found is None else (found.st_dev, found.st_ino)))
    return walked


def _a_folder(walked):
    """Whether the parts `walked` lead to one of the guarded folders: by their text, or by its device and inode."""
    return os.sep + os.sep.join(name for name, _ in walked) in GUARD["folders"] or \
        bool(walked) and walked[-1][1] in GUARD["inodes"]


def _climbs(path):
    """Whether `path` is a relative path with a .. among its parts: from a folder whose path cannot be told (a working
    folder that was removed, for one) it may lead anywhere, while one with no .. names something in that folder, which,
    removed, holds nothing."""
    if _is_fd(path):
        return False
    try:
        text = os.fsdecode(os.fspath(path))
    except (TypeError, ValueError):
        return False
    return not os.path.isabs(text) and "\0" not in text and os.pardir in text.split(os.sep)


def _reaches(path, dir_fd=None, how=_IN):
    """Whether `path` (from `dir_fd`) reaches the guarded folders as `how` says: by its text, with nothing looked up;
    else as the system walks it, through any symbolic link, up to them and never into them. A relative path from a
    folder that cannot be told reaches them if it climbs with a .., wherever it leads."""
    text = _absolute(path, dir_fd)
    if text is None:
        return _climbs(path)
    above = how == _ABOVE
    if _named(os.path.normpath(os.sep + text.lstrip(os.sep)), above):      # two leading slashes name the root here too
        return True
    walked = _walk(text, how == _IN, _a_folder)
    if walked is None or _a_folder(walked):
        return True
    return above and (bool(walked) and walked[-1][1] in GUARD["above"] or
                      _named(os.sep + os.sep.join(name for name, _ in walked), True))


def _databases(name):
    """The files sqlite3.connect may open for `name`: the name itself, and a file: URI's path."""
    found = [name]
    try:
        text = os.fsdecode(os.fspath(name))
    except (TypeError, ValueError):
        return found
    if text.startswith("file:"):
        from urllib.parse import unquote, urlsplit
        found.append(unquote(urlsplit(text).path))
    return found


def _no_follow(flags):
    """Whether an open's flags say not to follow its last symbolic link."""
    return _is_fd(flags) and bool(flags & getattr(os, "O_NOFOLLOW", 0))


def _no_home(env):
    """Whether a program given the environment `env` has no HOME. None: this process's own, as the C library holds it
    (GUARD["c_home"]), which os.putenv and os.unsetenv change underneath os.environ."""
    if env is None:
        return not GUARD["c_home"]
    for key in ("HOME", b"HOME"):
        try:
            if key in env:
                return False
        except TypeError:               # a mapping whose keys are the other kind: os.environ, os.environb
            pass
    return True


def _program_places(program, env=None, cwd=None, search=True):
    """Where a start looks up the program `program`: the path it names; or, with no folder in it and `search`, that
    name in each folder of the PATH of `env` (None: this process's own); each relative one from `cwd`, the new
    process's working folder, where one is given. [] where that cannot be told."""
    try:
        text = os.fsdecode(os.fspath(program))
        places = [text] if os.sep in text or not search else \
            [os.path.join(os.fsdecode(folder), text) for folder in os.get_exec_path(env)]
        if cwd is not None:
            base = os.fsdecode(os.fspath(cwd))
            places = [place if os.path.isabs(place) else os.path.join(base, place) for place in places]
    except (TypeError, ValueError):
        return []
    return places


def _library(name):
    return name.startswith("<") or name.startswith(_LIBRARY) or any(part in name for part in _INSTALLED)


def _a_test_starts_it():
    """Whether a program is started by a test: the nearest frame neither the standard library's nor an installed
    package's nor one of the guard's own wrappers (os.posix_spawn's, which it calls) is in a file of the tests, this one
    excepted; with no such frame, whether a test runs (a thread or an executor given subprocess.run itself). A program
    the code under test starts is its own affair."""
    frame = sys._getframe(2)
    while frame is not None and (_library(frame.f_code.co_filename) or frame.f_code in _WRAPPER_CODES):
        frame = frame.f_back
    if frame is None:
        return GUARD["test"] is not None
    name = os.path.abspath(frame.f_code.co_filename)
    return name.startswith(HERE + os.sep) and name != os.path.abspath(__file__)


def _note(path, line):
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _take(path):
    """The lines noted in the file `path`, the file taken away so that each is said once; [] where there is none."""
    taken = f"{path}.{os.getpid()}.taken"
    try:
        os.rename(path, taken)
    except OSError:
        return []
    try:
        with open(taken, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []
    finally:
        try:
            os.remove(taken)
        except OSError:
            pass


def _fork_file(test):
    """The file a process the tests' process forked notes what it stopped in, for the test that was running."""
    if not GUARD["forks"]:
        return None
    return os.path.join(GUARD["forks"], hashlib.sha256(test.encode("utf-8", "replace")).hexdigest()[:32] + ".log")


def _from_forks(test):
    """What processes forked while `test` ran noted, taken from their file."""
    path = _fork_file(test)
    return [] if path is None else _take(path)


def _unsaid():
    """What the guard stopped and no report has said, taken away so that each is said once: against a test whose report
    was never made; outside any test, and after a test's report, in the run's log, where a pytest-xdist worker notes its
    own too; and in processes forked, in their files."""
    lines = [f"{hit} (in {test})" for test, hits in GUARD["hits"].items() for hit in hits]
    GUARD["hits"].clear()
    if GUARD["log"]:
        lines += _take(GUARD["log"])
    if GUARD["forks"] and os.path.isdir(GUARD["forks"]):
        for name in sorted(os.listdir(GUARD["forks"])):
            if name.endswith(".log"):
                lines += _take(os.path.join(GUARD["forks"], name))
    return [line for line in lines if line.strip()]


def _say_unsaid():
    """Print what the guard stopped and no report has said, under STOPPED_OUTSIDE (its first 50 lines, each of a path
    _said cut short); whether there was any."""
    lines = _unsaid()
    if lines:
        said = lines[:50] + ([f"... and {len(lines) - 50} more"] if len(lines) > 50 else [])
        print("\n" + "\n".join([STOPPED_OUTSIDE, *said]), file=sys.stderr)
    return bool(lines)


def _stop(kind, what):
    """Note what was stopped against the test running now (a process this one forked notes it in a file the test's
    report reads; outside any test, in the run's log), and stop it."""
    line = f"{kind}: {what}"
    test = GUARD["test"]
    if os.getpid() != GUARD["pid"]:
        _note(_fork_file(test) if test is not None else GUARD["log"], f"{line} ({FORKED})")
    elif test is not None:
        GUARD["hits"].setdefault(test, []).append(line)
    else:
        _note(GUARD["log"], line)
    raise PermissionError(errno.EACCES, f"a test {kind} (tests/conftest.py: own_claude_records, own_home)")


def _audit(event, args):
    if not GUARD["on"]:
        return
    if event in ("os.putenv", "os.unsetenv"):           # os.environ's changes are made through them too
        key = os.fsencode(args[0]) if args and isinstance(args[0], (bytes, str)) else None
        if key == b"HOME":
            GUARD["c_home"] = event == "os.putenv"
        elif key == b"PATH":                # where posix_spawnp looks for a bare name
            value = args[1] if event == "os.putenv" and len(args) > 1 else None
            GUARD["c_path"] = os.fsdecode(value) if isinstance(value, (bytes, str)) else None
        return
    spec = _PATH_EVENTS.get(event)
    if spec is not None:
        if event == "open" and len(args) > 1 and args[1] is None and getattr(_IN_OS_OPEN, "on", False):
            return
        for at, fd_at, how in spec:
            path = args[at] if at < len(args) else None
            dir_fd = args[fd_at] if fd_at is not None and fd_at < len(args) else None
            if path is None and event in _HERE_WHEN_NONE:
                path = os.curdir
            if path is None:
                continue
            if event == "open" and len(args) > 2 and _no_follow(args[2]):
                how = _ENTRY
            if event == "glob.glob/2" and args[2] is not None:
                try:
                    path = os.path.join(os.fspath(args[2]), os.fspath(path))
                except TypeError:
                    pass
            for each in (_databases(path) if event == "sqlite3.connect" else (path,)):
                if _reaches(each, dir_fd, how):
                    _stop(TOUCHED, f"{event} {_said(path)}")
        return
    start = _START_EVENTS.get(event)
    if start is not None:
        if event == "subprocess.Popen" and len(args) > 2 and args[2] is not None and _reaches(args[2]):
            _stop(TOUCHED, f"{event} {_said(args[2])}")        # its working folder, which its new process changes to
        spawnp = event == "os.posix_spawn" and getattr(_IN_SPAWNP, "on", False)     # its program judged already
        if event != "os.system" and args and not spawnp:        # its program, which the new process looks up
            popen = event == "subprocess.Popen"
            places = _program_places(args[0], args[3] if popen and len(args) > 3 else None,
                                     args[2] if popen and len(args) > 2 else None, search=popen)
            for place in places:
                if _reaches(place):
                    _stop(TOUCHED, f"{event} {_said(place)}")
        env_at, name_at = start
        env = args[env_at] if env_at is not None and env_at < len(args) else None
        if _no_home(env) and _a_test_starts_it():
            _stop(NO_HOME, f"{event} {_said(args[name_at])}")


def _judging(real, name):
    """os.<name>, stopping a call on the guarded folders before it happens, its path judged as _WRAPPED says (its last
    symbolic link not followed where the call says so). For os.open its audit event is then not judged again: the
    event does not carry the descriptor a relative path is from."""
    def judged(path, *args, **kwargs):
        if GUARD["on"]:
            how = _WRAPPED[name]
            flags = kwargs.get("flags", args[0] if args else None) if name == "open" else None
            if kwargs.get("follow_symlinks") is False or _no_follow(flags):
                how = _ENTRY
            if _reaches(path, kwargs.get("dir_fd"), how):
                _stop(TOUCHED, f"os.{name} {_said(path)}")
        if name != "open":
            return real(path, *args, **kwargs)
        _IN_OS_OPEN.on = True
        try:
            return real(path, *args, **kwargs)
        finally:
            _IN_OS_OPEN.on = False
    judged.__wrapped__ = real
    _WRAPPER_CODES.add(judged.__code__)
    return judged


def _spawning(real, name):
    """os.<name>, stopping a start whose file actions open a path in the guarded folders: the C library opens it in the
    new process, and Python does not audit that; and for posix_spawnp, one whose program is looked up in them."""
    def judged(*args, **kwargs):
        if GUARD["on"]:
            actions = []
            if kwargs.get("file_actions"):
                try:                    # read once, and given on as read: they may be a one-shot iterator
                    kwargs["file_actions"] = actions = list(kwargs["file_actions"])
                except TypeError:
                    actions = []
            for action in actions:
                if isinstance(action, (tuple, list)) and len(action) > 3 and \
                        action[0] == getattr(os, "POSIX_SPAWN_OPEN", None) and \
                        _reaches(action[2], None, _ENTRY if _no_follow(action[3]) else _IN):
                    _stop(TOUCHED, f"os.{name} {_said(action[2])}")
            if name == "posix_spawnp" and args:     # a bare name, which the C library looks up in the PATH it holds
                path = GUARD["c_path"]
                for place in _program_places(args[0], {} if path is None else {"PATH": path}):
                    if _reaches(place):
                        _stop(TOUCHED, f"os.{name} {_said(place)}")
        if name != "posix_spawnp":
            return real(*args, **kwargs)
        _IN_SPAWNP.on = True                # its event names a bare name it does not look up in the working folder
        try:
            return real(*args, **kwargs)
        finally:
            _IN_SPAWNP.on = False
    judged.__wrapped__ = real
    _WRAPPER_CODES.add(judged.__code__)
    return judged


def _refusing(real, name):
    """os.<name>, stopped whatever it is given (_REFUSED says why)."""
    def judged(*args, **kwargs):
        if GUARD["on"]:
            _stop(_REFUSED[name], f"os.{name} {_said(args[0] if args else kwargs.get('path'))}")
        return real(*args, **kwargs)
    judged.__wrapped__ = real
    _WRAPPER_CODES.add(judged.__code__)
    return judged


def guarded_folders(environ=None):
    """Claude Code's records where they are as the run starts (`environ`, else this process's): in the password
    database's home's .claude, in $HOME's and in $CLAUDE_CONFIG_DIR (and in GUARD_ALSO's home's, for the guard's own
    tests), each as written and through its real path. A variable only adds a place: the password database's home's is
    kept whatever they say."""
    environ = os.environ if environ is None else environ
    bases = [os.path.join(os.path.abspath(home), ".claude") for home in
             (_password_home(), environ.get("HOME"), environ.get(GUARD_ALSO)) if home]
    if environ.get("CLAUDE_CONFIG_DIR"):
        bases.append(os.path.abspath(environ["CLAUDE_CONFIG_DIR"]))
    folders = set()
    for base in bases:
        for each in {base, os.path.realpath(base)}:
            folders.update(os.path.normpath(os.path.join(each, name)) for name in CLAUDE_RECORDS)
    return tuple(sorted(folders))


def _guard_on(folder):
    """The guard, in this process: its folders, with the device and inode of each that exists and of each folder above
    it, as the run starts (its only look at them: each folder's own entry, nothing in it); the run's own values of the
    session's variables; a log in `folder` (the tripwires' own) of what it stops outside any test, and a folder there
    for what forked processes stop; its audit hook, added once; and its wrappers of os's functions."""
    folders = guarded_folders()
    inodes, above = set(), set()
    for each in folders:
        walked = _walk(each, True)
        if walked and walked[-1][1] is not None:
            inodes.add(walked[-1][1])
            above.update(key for _, key in walked[:-1] if key is not None)
    GUARD.update(folders=folders, inodes=frozenset(inodes), above=frozenset(above), c_home="HOME" in os.environ,
                 c_path=os.environ.get("PATH"),
                 runner={name: os.environ.get(name) for name in SESSION_VARIABLES},
                 log=os.path.join(folder, "guard-stopped.log"), forks=os.path.join(folder, "guard-forks"),
                 pid=os.getpid(), on=True)
    os.makedirs(GUARD["forks"], exist_ok=True)
    if not GUARD["hooked"]:
        sys.addaudithook(_audit)
        GUARD["hooked"] = True
    for name in (*_WRAPPED, *_SPAWNING, *_REFUSED):
        real = getattr(os, name, None)
        if real is not None and not hasattr(real, "__wrapped__"):
            wrapper = _judging(real, name) if name in _WRAPPED else \
                _spawning(real, name) if name in _SPAWNING else _refusing(real, name)
            setattr(os, name, wrapper)
            # a module that asks whether os.stat takes dir_fd (shutil does, once, as it is imported) gets the answer the
            # real one gives
            for supports in _SUPPORTS:
                if real in supports:
                    supports.add(wrapper)


def _at_exit():
    """As the process that ran the session ends (a pytest-xdist worker notes in the run's log instead): what the guard
    stopped after the run's report (a plugin's last hooks, a cleanup of the run's configuration, a handler registered
    with atexit after the guard's own), printed; and the process ends failing, as the run would have."""
    try:
        said = _say_unsaid()
    except Exception:                   # the guard's own trouble never changes how the process ends
        return
    if not said:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    status = GUARD["exitstatus"]
    os._exit(1 if status in (None, pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED) else int(status))


def pytest_configure(config):
    import shutil
    import tempfile
    if hasattr(config, "workerinput") and os.environ.get(TRIPWIRES_ENV):
        folder = os.environ[TRIPWIRES_ENV]
        TRIPWIRES.update(folder=folder, log=os.path.join(folder, "reached.log"),
                         found=[name for name in REAL_AGENTS if os.path.exists(os.path.join(folder, name))])
        _guard_on(folder)
        return
    found = [name for name in REAL_AGENTS if shutil.which(name)]
    folder = tempfile.mkdtemp(prefix="pp-tripwires-")
    log = os.path.join(folder, "reached.log")
    for name in found:
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(TRIPWIRE.format(name=name, log=log))
        os.chmod(path, 0o755)
    if found:
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
    os.environ[TRIPWIRES_ENV] = folder
    TRIPWIRES.update(folder=folder, log=log, found=found)
    _guard_on(folder)
    if not GUARD["at_exit"]:            # the guard stays on until the process ends, and says then what it stopped late
        atexit.register(_at_exit)
        GUARD["at_exit"] = True


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    GUARD["test"] = item.nodeid          # what the guard stops while the test's fixtures and body run is the test's
    try:
        return (yield)
    finally:
        GUARD["test"] = None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """A test the guard stopped fails at its teardown, the last of its phases, whatever it did with the error. Once
    that report is made, what the guard stops is no test's: it is said where no test can be failed for it."""
    report = yield
    if call.when == "teardown":
        hits = GUARD["hits"].pop(item.nodeid, []) + _from_forks(item.nodeid)
        GUARD["test"] = None
        if hits:
            said = "\n  ".join([STOPPED, *hits[:20]] + ([f"... and {len(hits) - 20} more"] if len(hits) > 20 else []))
            # a teardown that failed of itself keeps its own error, the guard's after it
            report.longrepr = f"{report.longrepr}\n\n{said}" if report.failed else said
            report.outcome = "failed"
    return report


# What a test starts is the test's to end. A process a test starts names this run's base temporary folder on its command
# line (a workspace agent's --root, a stand-in in a test's bin), so one still alive once every fixture is torn down was
# left behind, whatever has adopted it since. At the session's end each one is named, killed, and the run fails. A
# process of another run names another folder, and this run's own ancestors (a shell that gave --basetemp) are spared.
# Under pytest-xdist the controller looks, once every worker is done: each worker's folder is inside its own. A run
# killed outright never gets here: the workspace agents then stop by themselves (PP_WORKSPACE_PARENT).
LEFT_BEHIND = "Tests left processes behind:"


def left_behind(root, table, me):
    """(pid, command) of each process in `table`, a list of (pid, ppid, command), whose command line names the folder
    `root` or something in it, but `me` and the processes it runs under."""
    parents = {pid: ppid for pid, ppid, _ in table}
    spared, pid = set(), me
    while pid and pid not in spared:
        spared.add(pid)
        pid = parents.get(pid)
    names = re.compile(re.escape(root) + r"(?=[/\s\"']|$)")      # the folder itself, not one whose name runs on
    return [(pid, command) for pid, _, command in table if pid not in spared and names.search(command)]


def _processes():
    """(pid, ppid, command) of every process on the machine, from one ps."""
    out = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], capture_output=True, timeout=30,
                         check=True).stdout
    table = []
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table.append((int(parts[0]), int(parts[1]), parts[2]))
    return table


def processes_naming(folder):
    """(pid, command) of each live process whose command line names `folder` or something in it, but this process
    and the processes it runs under: one ps."""
    return left_behind(folder, _processes(), os.getpid())


def _fail(session):
    if session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _end_what_tests_left(session):
    """The guard: each live process whose command line names the run's base temporary folder is named on a line
    beginning LEFT_BEHIND, killed, and the run fails. It cannot tell who started one, so it also ends a process the
    person runs that names the folder, such as a `tail -f` of a file in it, and fails the run for it too."""
    basetemp = getattr(getattr(session.config, "_tmp_path_factory", None), "_basetemp", None)
    if basetemp is None:                                # no test made a temporary folder
        return
    try:
        found = processes_naming(str(basetemp))
    except (OSError, subprocess.SubprocessError) as e:
        print(f"\nCould not look for processes the tests left behind: ps failed ({e})", file=sys.stderr)
        _fail(session)
        return
    if not found:
        return
    print("\n" + LEFT_BEHIND + "".join(f"\n  {pid} {command[:1000]}" for pid, command in found), file=sys.stderr)
    for pid, _ in found:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _fail(session)


@pytest.hookimpl(trylast=True)          # after pytest's own, which tears down whatever fixtures are still up
def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):          # a pytest-xdist worker: the controller looks, once all are done
        for test, hits in GUARD["hits"].items():        # what no report said goes where it looks
            for hit in hits:
                _note(GUARD["log"], f"{hit} (in {test})")
        GUARD["hits"].clear()
        return
    log = TRIPWIRES.get("log")
    if log and os.path.exists(log):
        with open(log, encoding="utf-8") as fh:
            reached = fh.read().strip()
        print(f"\nTests reached a real agent on the PATH, each a person's own and signed in:\n{reached[:2000]}",
              file=sys.stderr)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    # what the guard stopped where no test could be failed for it: outside any test, after a test's report, and in a
    # process a test forked that noted it after the test's report was made. What it stops after this is said as the
    # process ends (_at_exit)
    if _say_unsaid():
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    _end_what_tests_left(session)
    GUARD["exitstatus"] = session.exitstatus


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    # wrong passwords counted (auth.py) are kept in the process: each test's own. Cleared only once the app is loaded,
    # so a test run outside the app (test_no_real_agents copies this file) needs nothing of it
    auth = sys.modules.get("proofpane_open.auth")
    if auth is not None:
        auth._failures.clear()
    monkeypatch.setenv("OPEN_EDITION_DATA", str(tmp_path))
    monkeypatch.setenv("OPEN_EDITION_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("OPEN_EDITION_ADMINS", ADMIN)
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "https://open.example.test")
    # no test reaches the FIDO Metadata Service: empty, nothing is fetched (a test that wants a blob makes its own,
    # tests/fido_fixtures.py)
    monkeypatch.setenv("OPEN_EDITION_MDS_URL", "")
    # no test starts a cloud link's keeper, or has ade_link.py read the process of the session that runs the tests (its
    # start environment is where a proxy is taken again from): a test of the keeper names a process of its own
    # (tests/test_cloud_heard.py), and a client a test runs as a program of its own inherits this
    monkeypatch.setenv("ADE_LINK_KEEP", "0")
    return tmp_path


@pytest.fixture(autouse=True)
def classical_whole_turn_on(env, monkeypatch):
    # The server refuses a first turn-on, or a start again, that does not say kem (e2e_keys._whole_era): every account
    # whose keys it makes anew is post-quantum. Accounts with classical keys still exist, made by older releases, and
    # every classical path keeps its tests, which make such an account the way those releases did: a turn-on without
    # kem. So each test stands in for an account made before the refusal, with e2e_keys.CLASSICAL_WHOLE_TURN_ON on; a
    # test of the refusal turns it off again (tests/test_ade_e2e_turn_on_kem.py). Nothing in the server sets it. A run
    # outside the app (test_no_real_agents copies this file) has no such module, and needs nothing of it.
    try:
        from proofpane_open.ade import e2e_keys
    except ModuleNotFoundError as e:
        if e.name != "proofpane_open":
            raise
        return
    monkeypatch.setattr(e2e_keys, "CLASSICAL_WHOLE_TURN_ON", True)


@pytest.fixture(autouse=True)
def own_claude_records(tmp_path, monkeypatch):
    """ade_link.py's records of Claude Code's running processes (_take_sessions_dir, which _claude_folders reads too):
    a folder of the test's own, where a test that needs a record writes it. Where a fixture of the test's file, of any
    scope, already points them elsewhere, that is kept; a test that points them elsewhere itself does so after this.
    A run outside the app (test_no_real_agents copies this file) has no ade_link.py, and needs nothing of it."""
    try:
        import ade_link
    except ModuleNotFoundError as e:
        if e.name != "ade_link":
            raise
        return None
    take = ade_link._take_sessions_dir
    whose = (getattr(take, "__module__", None), getattr(take, "__name__", None))
    if whose != (ade_link.__name__, "_take_sessions_dir"):          # pointed elsewhere already by a fixture
        return None
    folder = os.path.join(str(tmp_path), "claude-home", ".claude", "sessions")
    monkeypatch.setattr(ade_link, "_take_sessions_dir", lambda: folder)
    return folder


@pytest.fixture(autouse=True)
def no_claude_code_session(monkeypatch):
    """No test sees the variables through which ade_link.py would find the Claude Code session that runs the tests,
    or post into it, with the values the run had: its folder of records (CLAUDE_CONFIG_DIR), its id
    (CLAUDE_CODE_SESSION_ID), its project (CLAUDE_PROJECT_DIR), and its inbox's socket and token
    (CLAUDE_CODE_MESSAGING_SOCKET, CLAUDE_CODE_MESSAGING_TOKEN). A value a fixture of the test's file set, of any scope,
    is kept."""
    for name in SESSION_VARIABLES:
        value = os.environ.get(name)
        if value is not None and value == GUARD["runner"].get(name):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def the_apps_word(request, monkeypatch):
    """The app's word on the workspace's record of skill proposals (proofpane_workspace/skill_improve.py):
    after each of its starts, every start the workspace makes unattended
    waits for it, at most a minute, and is refused where it has not come. A test with no app would wait that out at each
    start, so a workspace made in a test's process has the word at once, where the app's sender brings it within 25
    seconds of the workspace's start; a module whose tests are of that wait says so (WAITS_FOR_THE_APP),
    and keeps it."""
    if getattr(request.module, "WAITS_FOR_THE_APP", False):
        return
    try:
        from proofpane_workspace import skill_improve
    except ImportError:
        return
    real = skill_improve.SkillProposals.__init__

    def given(self, *args, **kwargs):
        real(self, *args, **kwargs)
        self.has_word = True

    monkeypatch.setattr(skill_improve.SkillProposals, "__init__", given)


@pytest.fixture
def own_home(tmp_path_factory):
    """A home of the test's own: for a program it starts with an environment of its own (one with no HOME takes the
    password database's home as its ~), or as its own HOME. Made beside the test's tmp_path, not in it, since some
    tests list that."""
    return str(tmp_path_factory.mktemp("home"))


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from proofpane_open.main import app
    return TestClient(app)


def signed_up(client, email=ADMIN, password="correct horse battery"):
    """Register `email` through a fresh invitation; returns the Authorization header."""
    from proofpane_open import auth
    token = auth.create_invitation(email, "test")
    r = client.post("/api/auth/register", json={"token": token, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def f():
-    return 1
+    return 2
diff --git a/tests/test_app.py b/tests/test_app.py
new file mode 100644
--- /dev/null
+++ b/tests/test_app.py
@@ -0,0 +1,2 @@
+def test_f():
+    assert f() == 2
"""
