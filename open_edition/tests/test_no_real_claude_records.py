"""No test, in the tests' own process or a process it forks, reads the Claude Code records of whoever runs the tests
(conftest.py): ade_link.py's are a folder of each test's own and the variables through which it would find the session
running the tests are gone; a touch of the real ones is stopped before it happens and fails the test that made it,
whatever the test did with the error; and so is a test that starts a program with no HOME, which would take the
password database's home as its ~. The words the guard prints are written out here rather than taken from conftest.py,
so that a run against a conftest.py without the guard fails on what the guard does (test_the_words_here_are_the_guard_s
checks that they are its words)."""

import ast
import inspect
import json
import os
import pathlib
import pwd
import re
import shutil
import subprocess
import sys

import pytest

import ade_link
import conftest

TOUCHED = "touched Claude Code's records of whoever runs the tests"
NO_HOME = "started a program whose environment holds no HOME"
CHANGED_ROOT = "changed a process's root, from where the guard cannot tell what a path reaches"
FORKED = "in a process it forked"
STOPPED = "The guard of tests/conftest.py stopped this test:"
STOPPED_OUTSIDE = "Where no test could be failed for it, the guard of tests/conftest.py stopped:"
GUARD_ALSO = "PP_TEST_GUARD_ALSO"
SESSION_VARIABLES = ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR",
                     "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN")
RECORD_TEXT = '{"pid": 1, "sessionId": "not-to-be-read"}'

# A run of its own, against a home of its own that the guard keeps as well as the real ones (GUARD_ALSO): each test
# does one thing to Claude Code's records there, beside them or above them, or starts a program. DOOR is a symbolic
# link to the records made before the run; installed_helper and debian_helper are in folders named site-packages and
# dist-packages, as an installed package is; started_elsewhere stands for the code under test.
INNER = r'''
import concurrent.futures
import errno
import glob
import os
import pathlib
import posix
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile

import pytest

import conftest
import debian_helper
import installed_helper
import started_elsewhere

HOME = os.environ["THE_HOME"]
ELSEWHERE = os.environ["ELSEWHERE"]
DOOR = os.environ["DOOR"]
CLAUDE = os.path.join(HOME, ".claude")
SESSIONS = os.path.join(CLAUDE, "sessions")
RECORD = os.path.join(SESSIONS, "1.json")
PROJECTS = os.path.join(CLAUDE, "projects")
PROGRAM = [sys.executable, "-c", "pass"]
NO_HOME = {"PATH": os.environ["PATH"]}
A_HOME = {"PATH": os.environ["PATH"], "HOME": HOME}
ON_LINUX = os.path.isdir("/proc/self/fd")


def refused(do):
    try:
        return do()
    except PermissionError:
        return "refused"


def unguarded(do):
    """`do` with the guard off: a descriptor opened so stands for one opened before the run."""
    conftest.GUARD["on"] = False
    try:
        return do()
    finally:
        conftest.GUARD["on"] = True


def test_open():
    assert refused(lambda: open(RECORD).read()) == "refused"


def test_write():
    assert refused(lambda: open(os.path.join(SESSIONS, "2.json"), "w")) == "refused"


def test_os_open():
    assert refused(lambda: os.open(RECORD, os.O_RDONLY)) == "refused"


def test_os_open_from_a_descriptor_above():     # the descriptor's folder, not the working one, is what it is read from
    above = os.open(CLAUDE, os.O_RDONLY)
    try:
        assert refused(lambda: os.open(os.path.join("sessions", "1.json"), os.O_RDONLY, dir_fd=above)) == "refused"
    finally:
        os.close(above)


def test_os_open_from_a_descriptor_elsewhere(monkeypatch):     # judged from the descriptor, not the working folder
    monkeypatch.chdir(CLAUDE)
    elsewhere = os.open(ELSEWHERE, os.O_RDONLY)
    try:
        with pytest.raises(FileNotFoundError):
            os.open(os.path.join("sessions", "1.json"), os.O_RDONLY, dir_fd=elsewhere)
    finally:
        os.close(elsewhere)


def test_a_descriptor_of_them_opened_before():
    folder = unguarded(lambda: os.open(SESSIONS, os.O_RDONLY))
    try:
        assert refused(lambda: os.listdir(folder)) == "refused"
        assert refused(lambda: list(os.scandir(folder))) == "refused"
        assert refused(lambda: os.stat(folder)) == "refused"
        assert refused(lambda: os.statvfs(folder)) == "refused"
        assert refused(lambda: os.fchdir(folder)) == "refused"
    finally:
        os.close(folder)


def test_open_a_descriptor():
    record = unguarded(lambda: os.open(RECORD, os.O_RDONLY))
    try:
        assert refused(lambda: open(record, closefd=False).read()) == "refused"
    finally:
        os.close(record)


def test_sqlite():
    assert refused(lambda: sqlite3.connect(os.path.join(SESSIONS, "x.db"))) == "refused"
    assert refused(lambda: sqlite3.connect(f"file:{SESSIONS}/y.db?mode=rwc", uri=True)) == "refused"
    # a URI's path is read with its escapes undone: sess%69ons is sessions
    assert refused(lambda: sqlite3.connect(f"file:{CLAUDE}/sess%69ons/z.db?mode=rwc", uri=True)) == "refused"


def test_listdir():
    assert refused(lambda: os.listdir(SESSIONS)) == "refused"


def test_scandir():
    assert refused(lambda: list(os.scandir(PROJECTS))) == "refused"


def test_glob():
    assert refused(lambda: glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))) == "refused"


def test_glob_from_a_descriptor_above():        # glob keeps the error to itself, and lists what it may
    above = os.open(CLAUDE, os.O_RDONLY)
    try:
        assert glob.glob(os.path.join("*", "*.json"), dir_fd=above) == [os.path.join("sessions-beside", "1.json")]
    finally:
        os.close(above)


def test_walk():
    assert refused(lambda: list(os.walk(SESSIONS))) == "refused"


def test_fwalk_from_above():                    # fwalk keeps the error to itself, and walks what it may
    assert {top for top, _, _, _ in os.fwalk(CLAUDE)} == {CLAUDE, os.path.join(CLAUDE, "sessions-beside"),
                                                         os.path.join(CLAUDE, "a")}


def test_exists():                              # os.path.exists keeps the error to itself, and says False
    assert not os.path.exists(RECORD)


def test_lexists():
    assert not os.path.lexists(RECORD)


def test_isdir():
    assert not os.path.isdir(PROJECTS)


def test_access():
    assert refused(lambda: os.access(RECORD, os.R_OK)) == "refused"


def test_readlink():                            # refused before its own error (the record is no link)
    assert refused(lambda: os.readlink(RECORD)) == "refused"


def test_statvfs():
    assert refused(lambda: os.statvfs(SESSIONS)) == "refused"


def test_pathconf():
    assert refused(lambda: os.pathconf(RECORD, "PC_NAME_MAX")) == "refused"


def test_pathlib():                             # Path.exists lets a refusal through (3.13); os.path.exists says False
    assert refused(lambda: pathlib.Path(RECORD).exists()) in ("refused", False)


def test_relative(monkeypatch):
    monkeypatch.chdir(CLAUDE)
    assert refused(lambda: open(os.path.join("sessions", "1.json")).read()) == "refused"


def test_relative_change(monkeypatch):           # os.mkdir names no descriptor as -1
    monkeypatch.chdir(CLAUDE)
    assert refused(lambda: os.mkdir(os.path.join("sessions", "new"))) == "refused"


def test_not_normalised():
    roundabout = os.path.join(CLAUDE, "sessions-beside", "..", "sessions", "1.json")
    assert refused(lambda: open(roundabout).read()) == "refused"


def test_two_leading_slashes():                # the root, here as on Linux and macOS
    assert refused(lambda: open("/" + RECORD).read()) == "refused"


def test_a_path_through_a_link_made_before():
    assert refused(lambda: open(os.path.join(DOOR, "1.json")).read()) == "refused"
    assert refused(lambda: os.lstat(DOOR + os.sep)) == "refused"      # a separator after a link follows it


def test_a_link_left_by_dot_dot():              # the system reads beside/.. as .claude; its text says ELSEWHERE
    os.symlink(os.path.join(CLAUDE, "sessions-beside"), os.path.join(ELSEWHERE, "beside"))
    roundabout = os.path.join(ELSEWHERE, "beside", "..", "sessions")
    assert refused(lambda: open(os.path.join(roundabout, "1.json")).read()) == "refused"
    assert refused(lambda: os.listdir(roundabout)) == "refused"


def a_working_folder_removed(monkeypatch):
    folder = tempfile.mkdtemp(dir=HOME)
    monkeypatch.chdir(folder)
    os.rmdir(folder)                            # os.getcwd() can no longer say where the working folder is


def test_a_climb_from_a_working_folder_removed(monkeypatch):    # it may lead anywhere: taken as reaching them
    os.symlink(SESSIONS, os.path.join(HOME, "a-door"))
    a_working_folder_removed(monkeypatch)
    assert refused(lambda: os.stat(os.path.join("..", ".claude", "sessions", "1.json"))) == "refused"
    assert refused(lambda: os.stat(pathlib.Path("..", ".claude", "sessions", "1.json"))) == "refused"
    assert refused(lambda: os.stat(os.path.join("..", "a-door", "1.json"))) == "refused"
    assert refused(lambda: os.stat(os.path.join(os.curdir, "..", ".claude", "sessions"))) == "refused"
    assert refused(lambda: os.listdir("..")) == "refused"           # the home, which is not them: stopped all the same


def test_a_path_with_no_climb_from_a_working_folder_removed(monkeypatch):     # it can name nothing but that folder
    a_working_folder_removed(monkeypatch)
    with pytest.raises(FileNotFoundError):
        os.stat("x")


def test_a_climb_through_a_link_from_a_descriptor_of_a_folder_removed():
    os.symlink(SESSIONS, os.path.join(HOME, "b-door"))
    folder = tempfile.mkdtemp(dir=HOME)
    gone = os.open(folder, os.O_RDONLY)
    os.rmdir(folder)                            # Linux names it as its path and " (deleted)", where nothing is
    try:
        assert refused(lambda: os.stat(os.path.join("..", "b-door", "1.json"), dir_fd=gone)) == "refused"
    finally:
        os.close(gone)


@pytest.mark.skipif(not ON_LINUX, reason="/proc is Linux's")
def test_proc_self_fd():                        # a descriptor's own path, as /proc and /dev/fd name it
    above = os.open(CLAUDE, os.O_RDONLY)
    try:
        assert refused(lambda: open(f"/proc/self/fd/{above}/sessions/1.json").read()) == "refused"
        assert refused(lambda: os.listdir(f"/dev/fd/{above}/sessions")) == "refused"
    finally:
        os.close(above)


@pytest.mark.skipif(not ON_LINUX, reason="/proc is Linux's")
def test_proc_self_root():
    assert refused(lambda: open("/proc/self/root" + RECORD).read()) == "refused"


def test_chdir():
    here = os.getcwd()
    assert refused(lambda: os.chdir(SESSIONS)) == "refused" and os.getcwd() == here


def test_mkdir():
    assert refused(lambda: os.mkdir(os.path.join(SESSIONS, "new"))) == "refused"


def test_remove():
    assert refused(lambda: os.remove(RECORD)) == "refused"


def test_rmdir():
    assert refused(lambda: os.rmdir(os.path.join(PROJECTS, "p"))) == "refused"


def test_rename_out():
    assert refused(lambda: os.rename(RECORD, os.path.join(ELSEWHERE, "r.json"))) == "refused"


def test_rename_in():
    assert refused(lambda: os.rename(os.path.join(ELSEWHERE, "e.txt"), os.path.join(SESSIONS, "e.txt"))) == "refused"


def test_rename_above():
    assert refused(lambda: os.rename(CLAUDE, os.path.join(ELSEWHERE, "moved"))) == "refused"


def test_link_out():
    assert refused(lambda: os.link(RECORD, os.path.join(ELSEWHERE, "l.json"))) == "refused"


def test_link_in():
    assert refused(lambda: os.link(os.path.join(ELSEWHERE, "e.txt"), os.path.join(SESSIONS, "l.txt"))) == "refused"


def test_symlink_in():
    assert refused(lambda: os.symlink(ELSEWHERE, os.path.join(SESSIONS, "s"))) == "refused"


def test_symlink_to():                          # a link to them is made, and a path through it is judged
    os.symlink(RECORD, os.path.join(ELSEWHERE, "s"))
    assert refused(lambda: open(os.path.join(ELSEWHERE, "s")).read()) == "refused"


def test_symlink_to_a_folder_above():
    os.symlink(HOME, os.path.join(ELSEWHERE, "h"))
    assert refused(lambda: os.listdir(os.path.join(ELSEWHERE, "h", ".claude", "sessions"))) == "refused"


def test_symlink_from_the_link_s_folder():      # a relative target leads from the link's folder
    os.symlink(os.path.join("..", "sessions"), os.path.join(CLAUDE, "a", "s"))
    assert refused(lambda: open(os.path.join(CLAUDE, "a", "s", "1.json")).read()) == "refused"
    above = os.open(CLAUDE, os.O_RDONLY)
    try:
        os.symlink(os.path.join("..", "sessions"), os.path.join("a", "t"), dir_fd=above)
    finally:
        os.close(above)
    assert refused(lambda: open(os.path.join(CLAUDE, "a", "t", "1.json")).read()) == "refused"


def test_truncate():
    assert refused(lambda: os.truncate(RECORD, 0)) == "refused"


def test_utime():
    assert refused(lambda: os.utime(RECORD)) == "refused"


def test_chmod():
    assert refused(lambda: os.chmod(RECORD, 0o600)) == "refused"


def test_chown():
    assert refused(lambda: os.chown(RECORD, -1, -1)) == "refused"


def test_chflags():                             # macOS's: the event is raised here as macOS raises it
    assert refused(lambda: sys.audit("os.chflags", RECORD, 0)) == "refused"


def test_xattr():                               # macOS has none of these: their events are raised as Linux raises them
    for name, args in (("listxattr", (RECORD,)), ("getxattr", (RECORD, "user.k")),
                       ("setxattr", (RECORD, "user.k", b"v", 0)), ("removexattr", (RECORD, "user.k"))):
        do = getattr(os, name, None)
        assert refused(lambda: do(*args) if do else sys.audit(f"os.{name}", *args)) == "refused", name


def test_mkfifo():
    assert refused(lambda: os.mkfifo(os.path.join(SESSIONS, "f"))) == "refused"


def test_mknod():
    assert refused(lambda: os.mknod(os.path.join(SESSIONS, "n"))) == "refused"


def test_unix_socket(monkeypatch):              # bind makes the socket's file; a relative path keeps it short
    monkeypatch.chdir(CLAUDE)
    with socket.socket(socket.AF_UNIX) as unix:
        assert refused(lambda: unix.bind(os.path.join("sessions", "s.sock"))) == "refused"


# each audited operation the guard judges that takes a descriptor, and each function it wraps that takes one, with its
# path's place: (the operation, its path's place, what is done from a descriptor of the folder above the records)
FROM_ABOVE = [
    ("os.fwalk", 0, lambda fd: list(os.fwalk("sessions", dir_fd=fd))),
    ("glob.glob/2", 0, lambda fd: glob.glob(os.path.join("sessions", "*"), dir_fd=fd)),
    ("os.mkdir", 0, lambda fd: os.mkdir(os.path.join("sessions", "new"), dir_fd=fd)),
    ("os.remove", 0, lambda fd: os.remove(os.path.join("sessions", "1.json"), dir_fd=fd)),
    ("os.rmdir", 0, lambda fd: os.rmdir(os.path.join("projects", "p"), dir_fd=fd)),
    ("os.rename", 0, lambda fd: os.rename(os.path.join("sessions", "1.json"), os.path.join(ELSEWHERE, "r2.json"),
                                          src_dir_fd=fd)),
    ("os.rename", 1, lambda fd: os.rename(os.path.join(ELSEWHERE, "e.txt"), os.path.join("sessions", "e2.txt"),
                                          dst_dir_fd=fd)),
    ("os.link", 0, lambda fd: os.link(os.path.join("sessions", "1.json"), os.path.join(ELSEWHERE, "l2.json"),
                                      src_dir_fd=fd)),
    ("os.link", 1, lambda fd: os.link(os.path.join(ELSEWHERE, "e.txt"), os.path.join("sessions", "l2.txt"),
                                      dst_dir_fd=fd)),
    ("os.symlink", 1, lambda fd: os.symlink(ELSEWHERE, os.path.join("sessions", "s2"), dir_fd=fd)),
    ("os.utime", 0, lambda fd: os.utime(os.path.join("sessions", "1.json"), dir_fd=fd)),
    ("os.chmod", 0, lambda fd: os.chmod(os.path.join("sessions", "1.json"), 0o600, dir_fd=fd)),
    ("os.chown", 0, lambda fd: os.chown(os.path.join("sessions", "1.json"), -1, -1, dir_fd=fd)),
    ("shutil.rmtree", 0, lambda fd: shutil.rmtree("sessions", dir_fd=fd)),
    ("os.stat", 0, lambda fd: os.stat(os.path.join("sessions", "1.json"), dir_fd=fd)),
    ("os.lstat", 0, lambda fd: os.lstat(os.path.join("sessions", "1.json"), dir_fd=fd)),
    ("os.access", 0, lambda fd: os.access(os.path.join("sessions", "1.json"), os.R_OK, dir_fd=fd)),
    ("os.readlink", 0, lambda fd: os.readlink(os.path.join("sessions", "1.json"), dir_fd=fd)),
    ("os.open", 0, lambda fd: os.open(os.path.join("sessions", "1.json"), os.O_RDONLY, dir_fd=fd)),
    ("os.mkfifo", 0, lambda fd: os.mkfifo(os.path.join("sessions", "f2"), dir_fd=fd)),
    ("os.mknod", 0, lambda fd: os.mknod(os.path.join("sessions", "n2"), dir_fd=fd)),
]


def test_each_from_a_descriptor_above():
    above = os.open(CLAUDE, os.O_RDONLY)
    try:
        for name, at, do in FROM_ABOVE:
            assert refused(lambda: do(above)) == "refused", (name, at)
    finally:
        os.close(above)


def test_copyfile():
    assert refused(lambda: shutil.copyfile(RECORD, os.path.join(ELSEWHERE, "c.json"))) == "refused"


def test_copymode():
    assert refused(lambda: shutil.copymode(RECORD, os.path.join(ELSEWHERE, "e.txt"))) == "refused"


def test_copystat():
    assert refused(lambda: shutil.copystat(RECORD, os.path.join(ELSEWHERE, "e.txt"))) == "refused"


def test_copytree():
    assert refused(lambda: shutil.copytree(SESSIONS, os.path.join(ELSEWHERE, "copy"))) == "refused"


def test_copytree_from_above():                 # copytree gathers its errors into one of its own
    with pytest.raises(shutil.Error):
        shutil.copytree(CLAUDE, os.path.join(ELSEWHERE, "copy-above"))
    assert sorted(os.listdir(os.path.join(ELSEWHERE, "copy-above"))) == ["a", "sessions-beside", "settings.json"]


def test_move():
    assert refused(lambda: shutil.move(RECORD, ELSEWHERE)) == "refused"


def test_move_above():
    assert refused(lambda: shutil.move(CLAUDE, os.path.join(ELSEWHERE, "moved"))) == "refused"


def test_rmtree():
    assert refused(lambda: shutil.rmtree(SESSIONS)) == "refused"


def test_rmtree_above():
    assert refused(lambda: shutil.rmtree(CLAUDE)) == "refused"


def test_make_archive():
    assert refused(lambda: shutil.make_archive(os.path.join(ELSEWHERE, "a"), "zip", root_dir=SESSIONS)) == "refused"


def test_unpack_archive():
    made = shutil.make_archive(os.path.join(ELSEWHERE, "b"), "zip", root_dir=ELSEWHERE, base_dir="e.txt")
    assert refused(lambda: shutil.unpack_archive(made, SESSIONS)) == "refused"


def test_mkstemp():
    assert refused(lambda: tempfile.mkstemp(dir=SESSIONS)) == "refused"


def test_mkdtemp():
    assert refused(lambda: tempfile.mkdtemp(dir=SESSIONS)) == "refused"


@pytest.fixture
def looks_at_its_end():
    yield
    os.path.exists(RECORD)


def test_teardown(looks_at_its_end):
    pass


@pytest.fixture
def fails_at_its_end_too():
    yield
    os.path.exists(RECORD)
    raise RuntimeError("its own teardown error")


def test_teardown_failing_too(fails_at_its_end_too):
    pass


def in_a_child(do):
    """What refused(do) gives in a process forked for it: a change of root there leaves this one's as it is."""
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(read)
            os.write(write, repr(refused(do)).encode())
        finally:
            os._exit(0)
    os.close(write)
    os.waitpid(child, 0)
    with os.fdopen(read) as fh:
        return fh.read()


def test_chroot():      # wherever it leads: from another root, the working folder and a descriptor kept reach on
    for root in (SESSIONS, CLAUDE, DOOR, ELSEWHERE):
        assert in_a_child(lambda: os.chroot(root)) == "'refused'", root
    assert in_a_child(lambda: os.chroot(path=SESSIONS)) == "'refused'"         # its path given by name


def test_a_program_started_in_them():           # its working folder, which its new process changes to
    assert refused(lambda: subprocess.run(PROGRAM, cwd=SESSIONS, env=A_HOME)) == "refused"
    assert refused(lambda: subprocess.run(PROGRAM, cwd=DOOR, env=A_HOME)) == "refused"
    assert refused(lambda: subprocess.run(PROGRAM, cwd=pathlib.Path(SESSIONS), env=A_HOME)) == "refused"
    assert refused(lambda: started_elsewhere.run(PROGRAM, cwd=PROJECTS)) == "refused"     # whoever starts it


def test_a_spawn_s_file_action_opening_one():   # the C library opens it in the new process, which Python does not audit
    action = [(os.POSIX_SPAWN_OPEN, 0, RECORD, os.O_RDONLY, 0)]
    for spawning in (os.posix_spawn, os.posix_spawnp):
        assert refused(lambda: os.waitpid(spawning(sys.executable, PROGRAM, A_HOME, file_actions=action), 0)) == \
            "refused", spawning
    assert refused(lambda: os.posix_spawn(sys.executable, PROGRAM, A_HOME, file_actions=tuple(action))) == "refused"


def test_a_program_looked_up_in_them(monkeypatch):      # its error would say what is there, as a look-up's would
    monkeypatch.chdir(CLAUDE)                   # a bare name posix_spawnp does not look for in the working folder
    with pytest.raises(FileNotFoundError):
        os.posix_spawnp("sessions", ["sessions"], A_HOME)
    there = os.path.join(SESSIONS, "x")
    climbing = os.path.join(os.pardir, os.path.basename(HOME), ".claude", "sessions", "x")
    assert refused(lambda: subprocess.run([there], env=A_HOME)) == "refused"
    os.symlink(there, os.path.join(ELSEWHERE, "to-a-program"))
    assert refused(lambda: subprocess.run([os.path.join(ELSEWHERE, "to-a-program")], env=A_HOME)) == "refused"
    assert refused(lambda: subprocess.run([climbing], cwd=ELSEWHERE, env=A_HOME)) == "refused"     # from its cwd
    assert refused(lambda: subprocess.run(["x"], env={"PATH": SESSIONS, "HOME": HOME})) == "refused"
    in_its_path = {"PATH": os.path.join(".claude", "sessions"), "HOME": HOME}      # from its cwd, not from this one's
    assert refused(lambda: subprocess.run(["x"], cwd=HOME, env=in_its_path)) == "refused"
    assert refused(lambda: os.posix_spawn(there, ["x"], A_HOME)) == "refused"
    os.putenv("PATH", SESSIONS)                 # the C library's own, underneath os.environ, which posix_spawnp reads
    try:
        assert refused(lambda: os.posix_spawnp("x", ["x"], A_HOME)) == "refused"
        os.unsetenv("PATH")                     # none: the C library's default, which names no folder of theirs
        with pytest.raises(FileNotFoundError):
            os.posix_spawnp("x", ["x"], A_HOME)
    finally:
        os.putenv("PATH", os.environ["PATH"])
    monkeypatch.setenv("PATH", SESSIONS + os.pathsep + os.environ["PATH"])
    assert refused(lambda: os.posix_spawnp("x", ["x"], A_HOME)) == "refused"       # this process's PATH, not A_HOME's
    assert refused(lambda: subprocess.run(["x"])) == "refused"                     # a start given no environment
    assert in_a_child(lambda: os.execv(there, ["x"])) == "'refused'"


def test_a_unix_socket_reached(monkeypatch):    # connect's error, or a datagram's, would say what is there
    monkeypatch.chdir(CLAUDE)
    there = os.path.join("sessions", "s.sock")
    os.symlink(os.path.join(SESSIONS, "s.sock"), os.path.join("a", "to-a-socket"))
    with socket.socket(socket.AF_UNIX) as unix:
        assert refused(lambda: unix.connect(there)) == "refused"
        assert refused(lambda: unix.connect_ex(there)) == "refused"
        assert refused(lambda: unix.connect(os.path.join("a", "to-a-socket"))) == "refused"     # it follows a link
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as unix:
        assert refused(lambda: unix.sendto(b"x", there)) == "refused"
        assert refused(lambda: unix.sendmsg([b"x"], [], 0, there)) == "refused"
        assert refused(lambda: unix.sendto(b"x", os.path.join("a", "to-a-socket"))) == "refused"
        assert refused(lambda: unix.sendmsg([b"x"], [], 0, os.path.join("a", "to-a-socket"))) == "refused"


def test_a_program_started_elsewhere():         # its working folder and a file its start opens, beside them
    assert subprocess.run(PROGRAM, cwd=ELSEWHERE, env=A_HOME).returncode == 0
    action = [(os.POSIX_SPAWN_OPEN, 0, os.path.join(ELSEWHERE, "e.txt"), os.O_RDONLY, 0)]
    assert os.waitpid(os.posix_spawn(sys.executable, PROGRAM, A_HOME, file_actions=action), 0)[1] == 0
    out = os.path.join(ELSEWHERE, "read-on-3.txt")
    reads = [sys.executable, "-c", "import os, sys; open(sys.argv[1], 'w').write(os.read(3, 10).decode())", out]
    once = (each for each in [(os.POSIX_SPAWN_OPEN, 3, os.path.join(ELSEWHERE, "e.txt"), os.O_RDONLY, 0)])
    assert os.waitpid(os.posix_spawn(sys.executable, reads, A_HOME, file_actions=once), 0)[1] == 0
    with open(out) as fh:                       # a one-shot iterator of actions reaches the program as it was given
        assert fh.read() == "e"


def test_a_grandchild():                        # forked by a forked process: what it stops reaches the test too
    child = os.fork()
    if child == 0:
        try:
            grandchild = os.fork()
            if grandchild == 0:
                try:
                    os.path.exists(os.path.join(PROJECTS, "from-a-grandchild"))
                finally:
                    os._exit(0)
            os.waitpid(grandchild, 0)
        finally:
            os._exit(0)
    os.waitpid(child, 0)


def test_no_home():
    assert refused(lambda: subprocess.run(PROGRAM, env=NO_HOME)) == "refused"


def test_no_home_in_bytes():
    assert refused(lambda: subprocess.run(PROGRAM, env={b"PATH": os.environ["PATH"].encode()})) == "refused"


def test_the_run_s_own_without_home(monkeypatch):
    monkeypatch.delenv("HOME")
    monkeypatch.setenv("PP_ANOTHER", "x")       # another variable set is not HOME set
    assert refused(lambda: subprocess.run(PROGRAM)) == "refused"


def test_system_without_home(monkeypatch):
    monkeypatch.delenv("HOME")
    assert refused(lambda: os.system("true")) == "refused"


def test_home_unset_underneath_os_environ():    # the C library's environment, which a program given none inherits
    os.unsetenv("HOME")
    try:
        assert "HOME" in os.environ and refused(lambda: subprocess.run(PROGRAM)) == "refused"
    finally:
        os.putenv("HOME", os.environ["HOME"])


def test_spawn_without_home():
    assert refused(lambda: os.posix_spawn(sys.executable, PROGRAM, NO_HOME)) == "refused"
    assert refused(lambda: os.posix_spawnp(sys.executable, PROGRAM, NO_HOME)) == "refused"


def test_spawnv_without_home(monkeypatch):      # a fork and its exec: the child's exec is refused, and it exits 127
    monkeypatch.delenv("HOME")
    assert os.spawnv(os.P_WAIT, sys.executable, PROGRAM) == 127


def test_spawnve_without_home():                # the environment it is given, not the run's, which has a HOME
    assert os.spawnve(os.P_WAIT, sys.executable, PROGRAM, NO_HOME) == 127


def test_from_an_executor():                    # every frame on the thread's stack is a library's
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        started = pool.submit(subprocess.run, PROGRAM, env=NO_HOME)
    assert refused(started.result) == "refused"


def test_from_an_installed_package():
    assert refused(lambda: installed_helper.run(PROGRAM)) == "refused"


def test_from_a_debian_installed_package():     # Debian's Python installs packages in dist-packages
    assert refused(lambda: debian_helper.run(PROGRAM)) == "refused"


def test_from_exec_d_code():
    assert refused(lambda: exec("subprocess.run(PROGRAM, env=NO_HOME)")) == "refused"


def test_beside():              # Claude Code's other files there, and a folder whose name runs on, are not its records
    assert open(os.path.join(CLAUDE, "settings.json")).read() == "{}"
    assert open(os.path.join(CLAUDE, "sessions-beside", "1.json")).read() == "{}"
    assert open(os.path.join(SESSIONS, "..", "settings.json")).read() == "{}"      # out of them again, by ..
    os.symlink(os.path.join("..", "sessions-beside"), os.path.join(CLAUDE, "a", "beside"))
    assert sorted(os.listdir(os.open(CLAUDE, os.O_RDONLY))) == ["a", "projects", "sessions", "sessions-beside",
                                                               "settings.json"]


def test_a_link_to_them_left_alone():           # making one, looking at the link itself and removing it touch nothing
    link = os.path.join(ELSEWHERE, "to-the-record")
    os.symlink(RECORD, link)
    assert os.readlink(link) == RECORD and os.path.islink(link)
    assert os.stat(link, follow_symlinks=False).st_size == len(RECORD)
    for opening in (os.open, posix.open):       # told not to follow it, the system refuses
        with pytest.raises(OSError) as refused_to_follow:
            opening(link, os.O_RDONLY | os.O_NOFOLLOW)
        assert refused_to_follow.value.errno == errno.ELOOP, opening
    action = [(os.POSIX_SPAWN_OPEN, 0, link, os.O_RDONLY | os.O_NOFOLLOW, 0)]
    with pytest.raises(OSError) as refused_to_follow:      # and so does a spawn's file action
        os.posix_spawn(sys.executable, PROGRAM, A_HOME, file_actions=action)
    assert refused_to_follow.value.errno == errno.ELOOP
    os.remove(link)


def test_rename_of_a_name_that_runs_on():       # .cl is no folder above .claude
    os.mkdir(os.path.join(HOME, ".cl"))
    os.rename(os.path.join(HOME, ".cl"), os.path.join(ELSEWHERE, "cl"))
    assert os.path.isdir(os.path.join(ELSEWHERE, "cl"))


def test_a_home():
    assert subprocess.run(PROGRAM, env={"PATH": os.environ["PATH"], "HOME": HOME}).returncode == 0
    assert subprocess.run(PROGRAM, env={b"PATH": os.environ["PATH"].encode(), b"HOME": HOME.encode()}).returncode == 0
    assert subprocess.run(PROGRAM, env=os.environb).returncode == 0


def test_spawn_and_exec_with_a_home():
    assert os.waitpid(os.posix_spawn(sys.executable, PROGRAM, A_HOME), 0)[1] == 0
    assert os.spawnve(os.P_WAIT, sys.executable, PROGRAM, A_HOME) == 0


def test_system_with_a_home():                  # os.system's program is given the run's own environment
    assert os.system("true") == 0


def test_the_run_s_own_home():  # no environment given: the run's own, its HOME with it
    assert subprocess.run(PROGRAM).returncode == 0


def test_home_set_again_underneath_os_environ(monkeypatch):     # what the C library holds, not what os.environ says
    says_its_home = [sys.executable, "-c", "import os, sys; sys.exit(os.environ.get('HOME') != sys.argv[1])"]
    os.unsetenv("HOME")
    os.putenv("HOME", os.environ["HOME"])
    assert subprocess.run([*says_its_home, os.environ["HOME"]]).returncode == 0
    monkeypatch.delenv("HOME")
    os.putenv("HOME", HOME)
    assert "HOME" not in os.environ and subprocess.run([*says_its_home, HOME]).returncode == 0


def test_started_elsewhere():   # a program the code under test starts, outside the tests' files, is its own affair
    assert started_elsewhere.run(PROGRAM) == 0
'''

LINUX_ONLY = {"test_proc_self_fd", "test_proc_self_root"}
STOPPED_TESTS = {
    "test_open", "test_write", "test_os_open", "test_os_open_from_a_descriptor_above",
    "test_a_descriptor_of_them_opened_before", "test_open_a_descriptor", "test_sqlite", "test_listdir",
    "test_scandir", "test_glob", "test_glob_from_a_descriptor_above", "test_walk", "test_fwalk_from_above",
    "test_exists", "test_lexists", "test_isdir", "test_access", "test_readlink", "test_statvfs", "test_pathconf",
    "test_pathlib", "test_relative", "test_relative_change", "test_not_normalised", "test_two_leading_slashes",
    "test_a_path_through_a_link_made_before", "test_a_link_left_by_dot_dot",
    "test_a_climb_from_a_working_folder_removed", "test_a_climb_through_a_link_from_a_descriptor_of_a_folder_removed",
    *LINUX_ONLY, "test_chdir", "test_mkdir",
    "test_remove", "test_rmdir", "test_rename_out", "test_rename_in", "test_rename_above", "test_link_out",
    "test_link_in", "test_symlink_in", "test_symlink_to", "test_symlink_to_a_folder_above",
    "test_symlink_from_the_link_s_folder", "test_truncate", "test_utime", "test_chmod", "test_chown", "test_chflags",
    "test_xattr", "test_mkfifo", "test_mknod", "test_unix_socket", "test_each_from_a_descriptor_above",
    "test_copyfile", "test_copymode", "test_copystat", "test_copytree", "test_copytree_from_above", "test_move",
    "test_move_above", "test_rmtree", "test_rmtree_above", "test_make_archive", "test_unpack_archive", "test_mkstemp",
    "test_mkdtemp", "test_chroot", "test_a_program_started_in_them", "test_a_spawn_s_file_action_opening_one",
    "test_a_program_looked_up_in_them", "test_a_unix_socket_reached",
    "test_teardown", "test_teardown_failing_too", "test_a_grandchild", "test_no_home",
    "test_no_home_in_bytes", "test_the_run_s_own_without_home", "test_system_without_home",
    "test_home_unset_underneath_os_environ", "test_spawn_without_home",
    "test_spawnv_without_home", "test_spawnve_without_home", "test_from_an_executor", "test_from_an_installed_package",
    "test_from_a_debian_installed_package", "test_from_exec_d_code"}
LEFT_ALONE = {"test_beside", "test_os_open_from_a_descriptor_elsewhere", "test_a_link_to_them_left_alone",
              "test_a_path_with_no_climb_from_a_working_folder_removed", "test_rename_of_a_name_that_runs_on",
              "test_a_program_started_elsewhere", "test_a_home", "test_spawn_and_exec_with_a_home",
              "test_system_with_a_home", "test_the_run_s_own_home", "test_home_set_again_underneath_os_environ",
              "test_started_elsewhere"}
# each audited operation conftest.py judges, and each function of os it wraps, is done in an inner test above
DONE_IN = {"open": "test_open", "sqlite3.connect": "test_sqlite", "os.listdir": "test_listdir",
           "os.scandir": "test_scandir", "os.walk": "test_walk", "os.fwalk": "test_fwalk_from_above",
           "glob.glob/2": "test_glob", "os.chdir": "test_chdir", "os.mkdir": "test_mkdir", "os.remove": "test_remove",
           "os.rmdir": "test_rmdir", "os.rename": "test_rename_in", "os.link": "test_link_in",
           "os.symlink": "test_symlink_in", "os.truncate": "test_truncate", "os.utime": "test_utime",
           "os.chmod": "test_chmod", "os.chown": "test_chown", "os.chflags": "test_chflags",
           "os.listxattr": "test_xattr", "os.getxattr": "test_xattr", "os.setxattr": "test_xattr",
           "os.removexattr": "test_xattr", "shutil.copyfile": "test_copyfile", "shutil.copymode": "test_copymode",
           "shutil.copystat": "test_copystat", "shutil.copytree": "test_copytree", "shutil.move": "test_move_above",
           "shutil.rmtree": "test_rmtree_above", "shutil.make_archive": "test_make_archive",
           "shutil.unpack_archive": "test_unpack_archive", "tempfile.mkstemp": "test_mkstemp",
           "tempfile.mkdtemp": "test_mkdtemp", "socket.bind": "test_unix_socket",
           "socket.connect": "test_a_unix_socket_reached", "socket.sendto": "test_a_unix_socket_reached",
           "socket.sendmsg": "test_a_unix_socket_reached", "os.stat": "test_exists",
           "os.lstat": "test_lexists", "os.access": "test_access", "os.readlink": "test_readlink",
           "os.open": "test_os_open_from_a_descriptor_above", "os.mkfifo": "test_mkfifo", "os.mknod": "test_mknod",
           "os.statvfs": "test_statvfs", "os.pathconf": "test_pathconf", "os.chroot": "test_chroot",
           "os.posix_spawn": "test_a_spawn_s_file_action_opening_one",
           "os.posix_spawnp": "test_a_spawn_s_file_action_opening_one"}

ELSEWHERE_STARTS = '''
import os
import subprocess


def run(argv, cwd=None):
    return subprocess.run(argv, env={"PATH": os.environ["PATH"]}, cwd=cwd).returncode
'''


def inner_run(tmp_path, files, workers=None, more=(), env=None, modules=None):
    """pytest run in a process of its own on `files` ({name: text}) in a folder `suite`, this file's conftest.py copied
    beside them, from a working folder outside it; with a home of its own (THE_HOME) whose records the guard keeps too,
    a folder ELSEWHERE beside it, a symbolic link DOOR made to the records before the run, the code under test and
    `modules` ({name: text}) in `lib`, and installed packages in folders named site-packages (installed_helper) and
    dist-packages (debian_helper); `env` added to the run's environment: (the run, the home)."""
    suite = tmp_path / "suite"
    suite.mkdir()
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "conftest.py"), suite / "conftest.py")
    for name, text in files.items():
        (suite / name).write_text(text)
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "started_elsewhere.py").write_text(ELSEWHERE_STARTS)
    for name, text in (modules or {}).items():
        (lib / f"{name}.py").write_text(text)
    installed = tmp_path / "python" / "site-packages"
    installed.mkdir(parents=True)
    (installed / "installed_helper.py").write_text(ELSEWHERE_STARTS)
    debian = tmp_path / "python" / "dist-packages"
    debian.mkdir()
    (debian / "debian_helper.py").write_text(ELSEWHERE_STARTS)
    home = tmp_path / "the-home"
    for folder in ("sessions", "projects/p", "sessions-beside", "a"):
        (home / ".claude" / folder).mkdir(parents=True)
    (home / ".claude" / "sessions" / "1.json").write_text(RECORD_TEXT)
    (home / ".claude" / "projects" / "p" / "s.jsonl").write_text("{}\n")
    (home / ".claude" / "sessions-beside" / "1.json").write_text("{}")
    (home / ".claude" / "settings.json").write_text("{}")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "e.txt").write_text("e")
    door = tmp_path / "door"
    os.symlink(home / ".claude" / "sessions", door)
    run_env = dict(os.environ, THE_HOME=str(home), ELSEWHERE=str(elsewhere), DOOR=str(door),
                   PYTHONPATH=os.pathsep.join([str(lib), str(installed), str(debian)]), PYTHONDONTWRITEBYTECODE="1",
                   **{GUARD_ALSO: str(home)}, **(env or {}))
    run_env.pop("PYTEST_ADDOPTS", None)
    # rooted at the suite's folder, as a run of the tests is at theirs: pytest looks at each entry of each folder from
    # its root down to the files it runs, and here DOOR's would be one, which the guard would stop it from following
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "--rootdir", "suite",
                        "--confcutdir", "suite", *(["-n", workers] if workers else []), *more,
                        *[os.path.join("suite", n) for n in files]],
                       cwd=tmp_path, env=run_env, capture_output=True, text=True, timeout=300)
    return p, home


def outcomes(out):
    """{test name: its outcomes in the run's summary}: PASSED, FAILED or ERROR (a test that passed can error at its
    teardown)."""
    seen = {}
    for word, name in re.findall(r"^(PASSED|FAILED|ERROR) \S*::(\w+)", out, re.M):
        seen.setdefault(name, set()).add(word)
    return seen


def untouched(tmp_path, home):
    """Whether the run left the home's records, Claude Code's other files there and the folder beside them as they
    were made, and made nothing of the records elsewhere."""
    claude = home / ".claude"
    elsewhere = tmp_path / "elsewhere"
    return (sorted(os.listdir(claude / "sessions")) == ["1.json"]
            and (claude / "sessions" / "1.json").read_text() == RECORD_TEXT
            and sorted(os.listdir(claude / "projects" / "p")) == ["s.jsonl"]
            and (claude / "settings.json").read_text() == "{}"
            and os.path.isdir(claude / "projects" / "p")
            and not set(os.listdir(elsewhere)) & {"r.json", "r2.json", "l.json", "l2.json", "moved", "c.json", "copy"}
            and (elsewhere / "e.txt").read_text() == "e")


def test_ade_link_s_records_are_the_test_s_own(tmp_path):
    folder = ade_link._take_sessions_dir()
    assert folder == os.path.join(str(tmp_path), "claude-home", ".claude", "sessions")
    assert os.path.dirname(folder) in ade_link._claude_folders()


class TestAFixtureOfAFileStillPointsThemElsewhere:
    @pytest.fixture(autouse=True)
    def elsewhere(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ade_link, "_take_sessions_dir", lambda: str(tmp_path / "elsewhere"))

    def test_it(self, tmp_path):
        assert ade_link._take_sessions_dir() == str(tmp_path / "elsewhere")


@pytest.fixture(scope="class")
def a_class_s_own_records(tmp_path_factory):
    folder = str(tmp_path_factory.mktemp("a-class-s-own"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ade_link, "_take_sessions_dir", lambda: folder)
        yield folder


class TestAFixtureOfAClassStillPointsThemElsewhere:       # set up before own_claude_records, which keeps it
    def test_it(self, a_class_s_own_records):
        assert ade_link._take_sessions_dir() == a_class_s_own_records


def _take_sessions_dir():                       # named as ade_link.py's own, and this file's
    return "a-folder-of-this-file-s-own"


@pytest.fixture(scope="class")
def a_function_of_the_same_name():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ade_link, "_take_sessions_dir", _take_sessions_dir)
        yield


class TestAFunctionOfTheSameNameIsStillElsewhere:         # told apart from ade_link.py's by its module, not its name
    def test_it(self, a_function_of_the_same_name):
        assert ade_link._take_sessions_dir() == "a-folder-of-this-file-s-own"


def test_a_record_a_test_writes_in_its_own_folder_is_the_one_the_take_binds(tmp_path):
    folder = ade_link._take_sessions_dir()
    assert folder.startswith(str(tmp_path) + os.sep)            # never the password database's home's, before anything
    assert os.environ.get("CLAUDE_CODE_SESSION_ID") is None     # the run's own is gone (no_claude_code_session)
    os.makedirs(folder)
    parent = os.getppid()
    with open(os.path.join(folder, f"{parent}.json"), "w") as fh:
        json.dump({"pid": parent, "sessionId": "the-test-s-own", "procStart": ade_link._process_start(parent)}, fh)
    assert ade_link._taking_session() == "the-test-s-own"


def test_the_words_here_are_the_guard_s():
    assert (TOUCHED, NO_HOME, CHANGED_ROOT, FORKED, STOPPED, STOPPED_OUTSIDE, GUARD_ALSO, SESSION_VARIABLES) == \
        (conftest.TOUCHED, conftest.NO_HOME, conftest.CHANGED_ROOT, conftest.FORKED, conftest.STOPPED,
         conftest.STOPPED_OUTSIDE, conftest.GUARD_ALSO, conftest.SESSION_VARIABLES)


def test_the_guard_keeps_the_records_where_they_are_as_the_run_starts(tmp_path, monkeypatch):
    home = pwd.getpwuid(os.getuid()).pw_dir
    real = [os.path.join(home, ".claude", name) for name in ("sessions", "projects")]
    assert conftest.GUARD["on"] and set(real) <= set(conftest.GUARD["folders"])
    # with no variable: the password database's home's
    assert set(conftest.guarded_folders({})) == {os.path.normpath(f) for f in real} | \
        {os.path.join(os.path.realpath(os.path.join(home, ".claude")), name) for name in ("sessions", "projects")}
    # each variable adds its place, and takes none away: $HOME's .claude, $CLAUDE_CONFIG_DIR itself, and GUARD_ALSO's
    given = {"HOME": str(tmp_path / "h"), "CLAUDE_CONFIG_DIR": str(tmp_path / "c"), GUARD_ALSO: str(tmp_path / "g")}
    folders = set(conftest.guarded_folders(given))
    assert set(real) <= folders
    for place in (tmp_path / "h" / ".claude", tmp_path / "c", tmp_path / "g" / ".claude"):
        assert {str(place / "sessions"), str(place / "projects")} <= folders, place
    assert all(os.path.basename(f) in ("sessions", "projects") for f in folders)


def test_a_home_reached_through_a_link_is_guarded_as_written_and_as_it_is(tmp_path, monkeypatch):
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "linked")
    monkeypatch.setattr(conftest, "_password_home", lambda: str(tmp_path / "linked"))
    folders = set(conftest.guarded_folders({}))
    for base in ("linked", "real"):
        assert {str(tmp_path / base / ".claude" / "sessions"), str(tmp_path / base / ".claude" / "projects")} <= folders


@pytest.mark.parametrize("event", ["os.listdir", "os.scandir", "os.listxattr"])
def test_a_listing_with_no_folder_named_is_of_the_working_folder(tmp_path, monkeypatch, event):
    folder = tmp_path / ".claude" / "sessions"
    folder.mkdir(parents=True)
    monkeypatch.chdir(folder)                                   # before it is one of the guarded folders
    monkeypatch.setitem(conftest.GUARD, "hits", {})             # what is stopped here is not this test's to fail for
    monkeypatch.setitem(conftest.GUARD, "folders", (str(tmp_path / "elsewhere"),))
    conftest._audit(event, (None,))                             # the working folder is not one of them
    monkeypatch.setitem(conftest.GUARD, "folders", (str(folder),))
    with pytest.raises(PermissionError):
        conftest._audit(event, (None,))


def test_a_wrapped_function_says_it_supports_what_the_real_one_does():
    # shutil asks once, as it is imported, whether os.stat and os.open take dir_fd: a module imported after the guard
    # is on gets the answer the real functions give
    for name in (*conftest._WRAPPED, *conftest._SPAWNING, *conftest._REFUSED):
        wrapper = getattr(os, name)
        real = wrapper.__wrapped__
        for supports in (os.supports_dir_fd, os.supports_fd, os.supports_follow_symlinks, os.supports_effective_ids):
            assert (wrapper in supports) == (real in supports), (name, supports)


def test_own_home_is_a_folder_of_the_test_s_own_beside_its_tmp_path(own_home, tmp_path):
    assert os.path.isdir(own_home) and os.listdir(own_home) == []
    assert not own_home.startswith(str(tmp_path) + os.sep)
    assert os.path.dirname(own_home) == os.path.dirname(str(tmp_path))
    assert re.fullmatch(r"home\d+", os.path.basename(own_home))


def _from_above_in(source):
    """(operation, its path's place) of each entry of FROM_ABOVE in `source`."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["FROM_ABOVE"]:
            return {(each.elts[0].value, each.elts[1].value) for each in node.value.elts}
    return set()


def test_every_event_and_wrapper_is_done_in_an_inner_test():
    assert set(DONE_IN) == set(conftest._PATH_EVENTS) | {f"os.{name}" for name in (*conftest._WRAPPED,
                                                                                   *conftest._SPAWNING,
                                                                                   *conftest._REFUSED)}
    names = set(re.findall(r"^def (test_\w+)", INNER, re.M))
    assert set(DONE_IN.values()) <= names and names == STOPPED_TESTS | LEFT_ALONE
    # and each that takes a descriptor is done from one too (test_each_from_a_descriptor_above)
    taking_one = {(event, at) for event, spec in conftest._PATH_EVENTS.items() for at, fd_at, _ in spec
                  if fd_at is not None}
    taking_one |= {(f"os.{name}", 0) for name in conftest._WRAPPED
                   if "dir_fd" in inspect.signature(getattr(os, name)).parameters}
    assert _from_above_in(INNER) == taking_one


@pytest.mark.parametrize("workers", [None, "2"], ids=["one_process", "xdist"])
def test_a_touch_or_a_program_with_no_home_fails_the_test_that_made_it(tmp_path, workers):
    if workers:
        pytest.importorskip("xdist")
    p, home = inner_run(tmp_path, {"test_inner.py": INNER}, workers)
    out = p.stdout + p.stderr
    seen = outcomes(p.stdout)
    stopped = STOPPED_TESTS if os.path.isdir("/proc/self/fd") else STOPPED_TESTS - LINUX_ONLY
    left_alone = LEFT_ALONE
    assert set(seen) == stopped | left_alone, out
    for name in stopped:
        assert seen[name] == {"PASSED", "ERROR"}, (name, out)      # the stop itself, then the test failed for it
    for name in left_alone:
        assert seen[name] == {"PASSED"}, (name, out)
    assert p.returncode == 1, out
    record = os.path.join(str(home), ".claude", "sessions", "1.json")
    assert STOPPED in out and f"{TOUCHED}: open {record}" in out and f"{TOUCHED}: os.stat {record}" in out
    assert f"{NO_HOME}: subprocess.Popen" in out and f"{NO_HOME}: os.exec {sys.executable} ({FORKED})" in out
    grandchild = os.path.join(str(home), ".claude", "projects", "from-a-grandchild")
    assert f"{TOUCHED}: os.stat {grandchild} ({FORKED})" in out
    assert f"{CHANGED_ROOT}: os.chroot {tmp_path / 'elsewhere'} ({FORKED})" in out
    assert f"{TOUCHED}: subprocess.Popen {os.path.join(os.path.dirname(record), 'x')}" in out
    assert "not-to-be-read" not in out and untouched(tmp_path, home), out
    assert "RuntimeError: its own teardown error" in out                # kept beside the guard's report
    assert STOPPED_OUTSIDE not in out


STARTED_WITH_NO_HOME = '''
import subprocess
import sys


def test_its_own_environment():
    try:
        subprocess.run([sys.executable, "-c", "pass"])
    except PermissionError:
        pass
'''

# a plugin loaded before the guard starts, which takes HOME away as a run started with none would lack it (the guard
# here stops a test from starting the run itself with none)
TAKES_HOME = '''
import os

os.environ.pop("HOME", None)
'''


@pytest.mark.parametrize("when", ["before_the_guard_starts", "as_the_module_is_collected"])
def test_a_run_with_no_home_stops_a_test_s_start_in_its_own_environment(tmp_path, when):
    if when == "before_the_guard_starts":
        p, _ = inner_run(tmp_path, {"test_started.py": STARTED_WITH_NO_HOME}, more=["-p", "takes_home"],
                         modules={"takes_home": TAKES_HOME})
    else:                                       # underneath os.environ, as the module is collected
        p, _ = inner_run(tmp_path, {"test_started.py": 'import os\nos.unsetenv("HOME")\n' + STARTED_WITH_NO_HOME})
    out = p.stdout + p.stderr
    assert outcomes(p.stdout) == {"test_its_own_environment": {"PASSED", "ERROR"}} and p.returncode == 1, out
    assert f"{NO_HOME}: subprocess.Popen {[sys.executable, '-c', 'pass']!r}" in out, out


# a plugin loaded before the guard starts, which puts the records' folder first in PATH, as a run started so would have
# it; a test then starts by a bare name the program posix_spawnp looks for in the PATH the C library holds
PUTS_THEIR_PATH = '''
import os

os.environ["PATH"] = os.path.join(os.environ["THE_HOME"], ".claude", "sessions") + os.pathsep + os.environ["PATH"]
'''

SPAWNED_BY_A_BARE_NAME = '''
import os


def test_a_bare_name():
    try:
        os.posix_spawnp("x", ["x"], {"PATH": os.environ["PATH"], "HOME": os.environ["THE_HOME"]})
    except PermissionError:
        pass
'''


def test_a_path_naming_them_as_the_guard_starts_is_the_one_a_bare_name_is_judged_in(tmp_path):
    p, home = inner_run(tmp_path, {"test_bare.py": SPAWNED_BY_A_BARE_NAME}, more=["-p", "puts_their_path"],
                        modules={"puts_their_path": PUTS_THEIR_PATH})
    out = p.stdout + p.stderr
    assert outcomes(p.stdout) == {"test_a_bare_name": {"PASSED", "ERROR"}} and p.returncode == 1, out
    assert f"{TOUCHED}: os.posix_spawnp {os.path.join(str(home), '.claude', 'sessions', 'x')}" in out, out


THE_TEST_S_OWN = '''
import os
import re

import pytest

SEEN = []


def test_its_own_home(own_home):
    with open(os.path.join(own_home, "left"), "w") as fh:
        fh.write("x")
    SEEN.append(own_home)


def test_another_s_own_home(own_home):
    assert SEEN and own_home != SEEN[0] and os.listdir(own_home) == []
    assert re.fullmatch(r"home\\d+", os.path.basename(own_home))


def test_no_session_variable_of_the_run():
    assert not set(os.environ) & {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR",
                                  "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN"}


@pytest.fixture(scope="module")
def a_project_of_its_own():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CLAUDE_PROJECT_DIR", "its-own")
        yield


def test_a_fixture_s_value_is_kept(a_project_of_its_own):
    assert os.environ["CLAUDE_PROJECT_DIR"] == "its-own"
'''


def test_each_test_has_its_own_home_and_none_of_the_session_s_variables(tmp_path):
    session = {"CLAUDE_CONFIG_DIR": str(tmp_path / "config"), "CLAUDE_CODE_SESSION_ID": "the-run-s-own",
               "CLAUDE_PROJECT_DIR": str(tmp_path), "CLAUDE_CODE_MESSAGING_SOCKET": str(tmp_path / "inbox"),
               "CLAUDE_CODE_MESSAGING_TOKEN": "the-run-s-own-token"}
    p, _ = inner_run(tmp_path, {"test_own.py": THE_TEST_S_OWN}, env=session)
    assert outcomes(p.stdout) == {name: {"PASSED"} for name in re.findall(r"^def (test_\w+)", THE_TEST_S_OWN, re.M)}, \
        p.stdout + p.stderr
    assert p.returncode == 0, p.stdout + p.stderr


COLLECTED = '''
import atexit
import os
import time

ELSEWHERE = os.environ["ELSEWHERE"]
SESSIONS = os.path.join(os.environ["THE_HOME"], ".claude", "sessions")

STOPPED_IN_TIME = not os.path.exists(os.path.join(SESSIONS, "1.json"))


def test_it():
    assert STOPPED_IN_TIME


def test_a_cleanup_of_the_run(request):
    request.config.add_cleanup(lambda: os.path.exists(os.path.join(SESSIONS, "in-a-cleanup")))


def test_at_exit():
    atexit.register(os.path.exists, os.path.join(SESSIONS, "at-exit"))


def test_a_child_after_its_test_s_report():     # it looks once late_touch says the test's report is made
    if os.fork() == 0:
        try:
            for _ in range(600):
                if os.path.exists(os.path.join(ELSEWHERE, "reported")):
                    break
                time.sleep(0.05)
            os.path.exists(os.path.join(SESSIONS, "from-a-child-after-the-report"))
        finally:
            open(os.path.join(ELSEWHERE, "child-done"), "w").close()
            os._exit(0)
'''

LATE = '''
import concurrent.futures
import os
import subprocess
import sys
import time

ELSEWHERE = os.environ["ELSEWHERE"]
CLAUDE = os.path.join(os.environ["THE_HOME"], ".claude")


def pytest_runtest_logreport(report):
    if report.when == "teardown" and report.nodeid.endswith("::test_a_child_after_its_test_s_report"):
        open(os.path.join(ELSEWHERE, "reported"), "w").close()


def pytest_runtest_logfinish(nodeid):
    if nodeid.endswith("::test_it"):
        os.path.exists(os.path.join(CLAUDE, "sessions", "in-logfinish"))


def pytest_sessionfinish(session):
    for _ in range(600):                        # the child that looks after its test's report has looked
        if not os.path.exists(os.path.join(ELSEWHERE, "reported")) or \\
                os.path.exists(os.path.join(ELSEWHERE, "child-done")):
            break
        time.sleep(0.05)
    os.path.exists(os.path.join(CLAUDE, "projects"))
    given = {"PATH": os.environ["PATH"], "HOME": ELSEWHERE}
    opening = [(os.POSIX_SPAWN_OPEN, 0, os.path.join(CLAUDE, "sessions", "1.json"), os.O_RDONLY, 0)]
    try:                                        # a program looked up in them
        subprocess.run(["x"], env={"PATH": os.path.join(CLAUDE, "sessions"), "HOME": ELSEWHERE})
    except OSError:
        pass
    child = os.fork()                           # a change of root, in a process forked for it
    if child == 0:
        try:
            os.chroot(os.path.join(CLAUDE, "sessions"))
        except OSError:
            pass
        finally:
            os._exit(0)
    os.waitpid(child, 0)
    for start in (lambda: subprocess.run([sys.executable, "-c", "pass"], cwd=os.path.join(CLAUDE, "sessions"),
                                         env=given),
                  lambda: os.posix_spawn(sys.executable, [sys.executable, "-c", "pass"], given,
                                         file_actions=opening)):
        try:
            start()
        except PermissionError:
            pass
    # a program started from a thread where no test runs is no test's start: left alone
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(subprocess.run, [sys.executable, "-c", "pass"], env={"PATH": os.environ["PATH"]}).result()


def pytest_terminal_summary(terminalreporter):
    os.path.exists(os.path.join(CLAUDE, "sessions", "in-the-summary"))


def pytest_unconfigure(config):
    os.path.exists(os.path.join(CLAUDE, "sessions", "in-unconfigure"))
'''


@pytest.mark.parametrize("workers", [None, "2"], ids=["one_process", "xdist"])
def test_a_touch_no_test_can_be_failed_for_fails_the_run(tmp_path, workers):
    """A touch as a module is collected, which is stopped in time as any other; and those after a test's report, by a
    plugin at the run's end, in a cleanup of the run's configuration, at the process's exit and in a process a test
    forked: each said, and the run fails."""
    if workers:
        pytest.importorskip("xdist")
    p, home = inner_run(tmp_path, {"test_collected.py": COLLECTED}, workers, more=["-p", "late_touch"],
                        modules={"late_touch": LATE})
    out = p.stdout + p.stderr
    assert outcomes(p.stdout) == {name: {"PASSED"} for name in re.findall(r"^def (test_\w+)", COLLECTED, re.M)}, out
    assert p.returncode == 1, out
    claude = os.path.join(str(home), ".claude")
    said = p.stderr.split(STOPPED_OUTSIDE, 1)[-1]
    assert STOPPED_OUTSIDE in p.stderr, out
    late = ["sessions/1.json", "projects", "sessions/in-logfinish", "sessions/in-the-summary", "sessions/in-a-cleanup",
            "sessions/in-unconfigure", "sessions/at-exit", "sessions/from-a-child-after-the-report"]
    lines = [f"{TOUCHED}: os.stat {os.path.join(claude, *name.split('/'))}" +
             (f" ({FORKED})" if name.endswith("from-a-child-after-the-report") else "") for name in late]
    lines += [f"{TOUCHED}: subprocess.Popen {os.path.join(claude, 'sessions')}",
              f"{TOUCHED}: subprocess.Popen {os.path.join(claude, 'sessions', 'x')}",
              f"{TOUCHED}: os.posix_spawn {os.path.join(claude, 'sessions', '1.json')}",
              f"{CHANGED_ROOT}: os.chroot {os.path.join(claude, 'sessions')} ({FORKED})"]
    said_lines = said.splitlines()
    for line in lines:
        assert line in said_lines, (line, out)
        if not workers:                         # said once (each worker collects the file, and has hooks of its own)
            assert said_lines.count(line) == 1, (line, out)
    assert NO_HOME not in out, out


QUIET = '''
def test_it():
    pass
'''

LATER = '''
import os


def pytest_terminal_summary(terminalreporter):
    os.path.exists(os.path.join(os.environ["THE_HOME"], ".claude", "sessions", "in-the-summary"))
'''


def test_a_touch_after_the_run_s_report_fails_the_run_as_the_process_ends(tmp_path):
    p, home = inner_run(tmp_path, {"test_quiet.py": QUIET}, more=["-p", "later_touch"],
                        modules={"later_touch": LATER})
    out = p.stdout + p.stderr
    assert outcomes(p.stdout) == {"test_it": {"PASSED"}} and "1 passed" in p.stdout, out
    assert p.returncode == 1, out              # pytest found nothing wrong: the guard ends the process failing
    said = p.stderr.split(STOPPED_OUTSIDE, 1)[-1]
    assert f"{TOUCHED}: os.stat {os.path.join(str(home), '.claude', 'sessions', 'in-the-summary')}" in said, out


def test_the_guard_looks_at_nothing_in_the_folders(tmp_path, monkeypatch):
    folder = tmp_path / "the-records"
    (folder / "inside").mkdir(parents=True)
    os.symlink(folder, tmp_path / "door")
    looked = []
    real = conftest._LSTAT
    monkeypatch.setattr(conftest, "_LSTAT", lambda path: looked.append(path) or real(path))
    monkeypatch.setitem(conftest.GUARD, "folders", (str(folder),))
    assert conftest._reaches(str(tmp_path / "door" / "inside" / "x.json"))        # walked up to them, not into them
    assert looked and not [path for path in looked if path.startswith(str(folder) + os.sep)], looked
    before = len(looked)
    named = folder / "inside" / "x.json"
    assert conftest._reaches(str(named)) and len(looked) == before        # its text names them: nothing looked up


def test_a_climb_from_a_descriptor_whose_folder_cannot_be_told_is_taken_as_reaching_them(tmp_path, monkeypatch):
    monkeypatch.setitem(conftest.GUARD, "folders", (str(tmp_path / "the-records"),))
    monkeypatch.setattr(conftest, "_fd_path", lambda fd: None)      # as where no /proc is mounted
    assert conftest._reaches(os.path.join(os.pardir, "x"), dir_fd=3)
    assert conftest._reaches(pathlib.Path(os.pardir, "x"), dir_fd=3)
    assert not conftest._reaches("x", dir_fd=3)                     # with no climb it names nothing but that folder


def test_a_folder_is_known_by_its_device_and_inode_as_well_as_its_name(tmp_path, monkeypatch):
    # another name for the folder that is no symbolic link, as on a disk that ignores case or through a mount
    named, other, beside, up = tmp_path / "named", tmp_path / "other", tmp_path / "beside", tmp_path / "up"
    for each in (named, other, beside, up):
        each.mkdir()
    key = (os.stat(other).st_dev, os.stat(other).st_ino)
    above = (os.stat(up).st_dev, os.stat(up).st_ino)
    monkeypatch.setitem(conftest.GUARD, "folders", (str(named),))
    monkeypatch.setitem(conftest.GUARD, "inodes", frozenset({key}))
    monkeypatch.setitem(conftest.GUARD, "above", frozenset({above}))     # a folder above it, as no name says
    assert conftest._reaches(str(other)) and conftest._reaches(str(other / "1.json"))
    assert not conftest._reaches(str(beside / "1.json"))
    assert conftest._reaches(str(up), how=conftest._ABOVE) and not conftest._reaches(str(up))
    assert conftest._reaches(str(tmp_path), how=conftest._ABOVE)    # above by its name, its inode not taken
    assert not conftest._reaches(str(beside), how=conftest._ABOVE)


KEYS = '''
import json
import os

import conftest


def test_the_keys_the_guard_took():
    with open(os.path.join(os.environ["ELSEWHERE"], "keys.json"), "w") as fh:
        json.dump({"inodes": sorted(conftest.GUARD["inodes"]), "above": sorted(conftest.GUARD["above"])}, fh)
'''


def test_the_guard_takes_each_folder_s_device_and_inode_and_those_above_it_as_the_run_starts(tmp_path):
    p, home = inner_run(tmp_path, {"test_keys.py": KEYS})
    assert outcomes(p.stdout) == {"test_the_keys_the_guard_took": {"PASSED"}} and p.returncode == 0, \
        p.stdout + p.stderr
    keys = json.loads((tmp_path / "elsewhere" / "keys.json").read_text())
    inodes, above = {tuple(key) for key in keys["inodes"]}, {tuple(key) for key in keys["above"]}

    def key(path):
        found = os.stat(path)
        return found.st_dev, found.st_ino

    for name in conftest.CLAUDE_RECORDS:
        folder = os.path.realpath(home / ".claude" / name)
        assert key(folder) in inodes, folder
        parent = os.path.dirname(folder)
        while parent != os.path.dirname(parent):            # each folder above it but the root, which its name says
            assert key(parent) in above, parent
            parent = os.path.dirname(parent)


def test_a_folder_that_is_a_link_is_known_by_what_it_leads_to(tmp_path):
    real = tmp_path / "real-sessions"
    real.mkdir()
    linked = tmp_path / "linked-home"
    (linked / ".claude").mkdir(parents=True)
    os.symlink(real, linked / ".claude" / "sessions")
    p, _ = inner_run(tmp_path, {"test_keys.py": KEYS}, env={"HOME": str(linked)})
    assert outcomes(p.stdout) == {"test_the_keys_the_guard_took": {"PASSED"}} and p.returncode == 0, \
        p.stdout + p.stderr
    keys = json.loads((tmp_path / "elsewhere" / "keys.json").read_text())
    found = os.stat(real)
    assert [found.st_dev, found.st_ino] in keys["inodes"]          # the folder it leads to, not the link's own entry


def test_a_separator_or_a_dot_after_a_link_s_name_follows_it(tmp_path, monkeypatch):
    folder = tmp_path / "the-records"
    folder.mkdir()
    os.symlink(folder, tmp_path / "door")
    door = str(tmp_path / "door")
    monkeypatch.setitem(conftest.GUARD, "folders", (str(folder),))
    assert not conftest._reaches(door, how=conftest._ENTRY)                 # the link itself
    for after in (os.sep, os.sep + os.curdir):                              # what it leads to, as the system takes it
        assert conftest._reaches(door + after, how=conftest._ENTRY), after


def test_the_guard_follows_as_many_links_as_the_system_does(tmp_path, monkeypatch):
    folder = tmp_path / "the-records"
    folder.mkdir()
    previous, followed = "the-records", 0
    for n in range(1, 42):
        os.symlink(previous, tmp_path / f"link{n}")
        previous = f"link{n}"
    for n in range(1, 42):
        try:
            os.stat(tmp_path / f"link{n}")
        except OSError:                         # more links than the system follows in one path (ELOOP)
            break
        followed = n
    if sys.platform.startswith("linux"):
        assert followed == conftest._MAX_LINKS == 40
    looked = []
    real = conftest._LSTAT
    monkeypatch.setattr(conftest, "_LSTAT", lambda path: looked.append(path) or real(path))
    monkeypatch.setitem(conftest.GUARD, "folders", (str(folder),))       # after the system's look, which it would stop
    for n in range(1, followed + 1):
        assert conftest._reaches(str(tmp_path / f"link{n}")), n
    for n in range(conftest._MAX_LINKS + 1, 42):    # no more: past them the system reaches nothing, nor does the guard
        assert not conftest._reaches(str(tmp_path / f"link{n}")), n
        assert not conftest._reaches(str(tmp_path / f"link{n}" / "1.json")), n
    # nothing looked up below them, or through a link it did not follow (link1/1.json would be inside them)
    below = [path for path in looked if path.startswith(str(tmp_path) + os.sep) and
             os.sep in path[len(str(tmp_path)) + 1:]]
    assert not below, below


# A static look at every program the tests start, beside the guard's own look at those that run: some tests start
# theirs only where a tool is installed (caddy, a real codex), and are skipped elsewhere.
STARTERS = {("subprocess", name) for name in ("run", "Popen", "call", "check_call", "check_output")} | \
    {("os", name) for name in ("posix_spawn", "posix_spawnp", "execve", "execvpe", "spawnve", "spawnvpe")}
ENV_AT = {"posix_spawn": 2, "posix_spawnp": 2, "execve": 2, "execvpe": 2, "spawnve": 3, "spawnvpe": 3}


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join([node.id, *reversed(parts)]) if isinstance(node, ast.Name) else None


def _from_environ(node):
    """os.environ, os.environ.copy(), dict(os.environ, ...) or {**os.environ}."""
    name = _dotted(node)
    if name in ("os.environ", "environ"):
        return True
    if isinstance(node, ast.Call):
        called = _dotted(node.func)
        if called in ("os.environ.copy", "environ.copy"):
            return True
        if called == "dict" and node.args:
            return _from_environ(node.args[0])
    if isinstance(node, ast.Dict):
        return any(key is None and _from_environ(value) for key, value in zip(node.keys, node.values))
    return False


def _assigned(name, body):
    """What `name` is given in `body` (a function, with what it holds; else the module's own statements): its values,
    and whether HOME is set in it."""
    values, home = [], False
    nodes = ast.walk(body) if not isinstance(body, ast.Module) else iter(body.body)
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    values.append(node.value)
                elif isinstance(target, ast.Subscript) and _dotted(target.value) == name and \
                        isinstance(target.slice, ast.Constant) and target.slice.value in ("HOME", b"HOME"):
                    home = True
        elif isinstance(node, ast.Call) and _dotted(node.func) == f"{name}.update":
            home = home or any(kw.arg == "HOME" for kw in node.keywords)
    return values, home


def _gives_home(node, scopes, depth=0):
    """True: the environment holds HOME or is built from os.environ; False: it is written out without either; None:
    it is not written out here (a parameter, a call), for the run-time guard to judge. `scopes`: the functions around
    the start, innermost first, then the module."""
    if node is None or (isinstance(node, ast.Constant) and node.value is None) or _from_environ(node):
        return True
    if depth > 4:
        return None
    if isinstance(node, ast.Dict):
        if any(isinstance(k, ast.Constant) and k.value in ("HOME", b"HOME") for k in node.keys):
            return True
        spread = [_gives_home(v, scopes, depth + 1) for k, v in zip(node.keys, node.values) if k is None]
        return True if True in spread else (None if None in spread else False)
    if isinstance(node, ast.Call) and _dotted(node.func) == "dict":
        if any(kw.arg == "HOME" for kw in node.keywords):
            return True
        return _gives_home(node.args[0], scopes, depth + 1) if node.args else False
    if isinstance(node, ast.IfExp):
        both = [_gives_home(n, scopes, depth + 1) for n in (node.body, node.orelse)]
        return False if False in both else (None if None in both else True)
    if isinstance(node, ast.Name):
        for at, body in enumerate(scopes):
            values, home = _assigned(node.id, body)
            if home:
                return True
            if values:
                said = [_gives_home(v, scopes[at:], depth + 1) for v in values]
                return False if False in said else (None if None in said else True)
            if not isinstance(body, ast.Module) and node.id in {a.arg for a in ast.walk(body.args)
                                                                if isinstance(a, ast.arg)}:
                return None                         # a parameter: its callers say
    return None


def no_home_starts(source):
    """(line, what) of each program `source` starts in an environment it writes out with no HOME and not built from
    os.environ."""
    tree = ast.parse(source)
    imported = {a.asname or a.name: (n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    aliases = {a.asname: a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names if a.asname}
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        module, _, called = (name or "").rpartition(".")
        module = aliases.get(module, module)
        if not module and called in imported:
            module, called = imported[called]
        if (module, called) not in STARTERS:
            continue
        env = next((kw.value for kw in node.keywords if kw.arg == "env"), None)
        if called in ENV_AT and len(node.args) > ENV_AT[called]:
            env = node.args[ENV_AT[called]]
        around = sorted((s for s in scopes if s.lineno <= node.lineno <= s.end_lineno), key=lambda s: -s.lineno)
        if _gives_home(env, [*around, tree]) is False:
            found.append((node.lineno, f"{module}.{called}"))
    return found


def no_home_starts_in(folder):
    """{file: its starts with no HOME} for each Python file in `folder` that has one."""
    found = {}
    for name in sorted(os.listdir(folder)):
        if name.endswith(".py"):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                starts = no_home_starts(fh.read())
            if starts:
                found[name] = starts
    return found


def test_a_program_a_test_starts_from_an_environment_it_writes_out_is_given_a_home(tmp_path):
    control = '''
import os, subprocess
from subprocess import run as start
def f(home, given):
    subprocess.run(["x"])                                           # its own
    subprocess.run(["x"], env={"PATH": "/bin"})                     # none
    subprocess.run(["x"], env={"PATH": "/bin", "HOME": home})
    subprocess.run(["x"], env=dict(os.environ, A="1"))
    subprocess.run(["x"], env={k: v for k, v in os.environ.items() if k != "A"})
    env = {"PATH": "/bin"}
    env["A"] = "1"
    subprocess.Popen(["x"], env=env)                                # none
    start(["x"], env=dict(env, A="2"))                              # none
    other = {"PATH": "/bin"}
    other["HOME"] = home
    subprocess.run(["x"], env=other)
    subprocess.run(["x"], env=given)                                # a parameter: not written out here
    subprocess.run(["x"], env=given if given is not None else {"PATH": "/bin"})    # none
    os.posix_spawn("/bin/x", ["x"], {"PATH": "/bin"})               # none
    os.posix_spawn("/bin/x", ["x"], {b"HOME": b"/h"})
    os.posix_spawnp("x", ["x"], {"PATH": "/bin"})                   # none
    os.execve("/bin/x", ["x"], {"PATH": "/bin"})                    # none
    os.execve("/bin/x", ["x"], {"PATH": "/bin", "HOME": home})
    os.execvpe("x", ["x"], {"PATH": "/bin"})                        # none
    os.spawnve(os.P_WAIT, "/bin/x", ["x"], {"PATH": "/bin"})        # none
    os.spawnve(os.P_WAIT, "/bin/x", ["x"], {"HOME": home})
    os.spawnvpe(os.P_WAIT, "x", ["x"], {"PATH": "/bin"})            # none
    import subprocess as sp
    sp.run(["x"], env={"PATH": "/bin"})                             # none
    sp.run(["x"], env={"PATH": "/bin", "HOME": home})
'''
    flagged = [n for n, text in enumerate(control.splitlines(), 1) if text.endswith("# none")]
    assert [line for line, _ in no_home_starts(control)] == flagged
    (tmp_path / "test_control.py").write_text(control)
    (tmp_path / "a_helper.py").write_text(control)        # a file of the tests' not named test_*, as fixtures are
    (tmp_path / "test_given_a_home.py").write_text("import subprocess\nsubprocess.run(['x'], env={'HOME': '/h'})\n")
    assert {name: [line for line, _ in starts] for name, starts in no_home_starts_in(tmp_path).items()} == \
        {"a_helper.py": flagged, "test_control.py": flagged}
    assert no_home_starts_in(os.path.dirname(os.path.abspath(__file__))) == {}
