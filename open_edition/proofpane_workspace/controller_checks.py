"""A round of a controller's task's checks: the round's clone of the
session's branch, the commits judged in it, and each command in a detached checkout of it. The controller
(controller.py) queues the rounds, holds the record's lock and keeps what a round gives; this module runs git and the
commands, and keeps nothing.

A session's programs run as the workspace's user and can write the repository's folder: its objects, its settings, its
attributes, its grafts and its replace refs among it. So a round's clone is made over git's own transport (file://,
never a local clone's links or shared objects), where index-pack checks every object it takes against its id, with no
settings read but the clone's own (GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL=/dev/null, no template), and with no graft or
replace ref of the repository's read on its side (UPLOAD_PACK): an object a program rewrote is refused, not read, and
nothing else of the repository's comes with it or cuts what is sent. The commits are judged in the clone, and each
command runs in a detached worktree of it that git's own checkout writes just before the command runs, while the clone
is as it was made (clone_state), with git's own refusals of a path through a link it wrote, of a .git part and of the
names core.protectHFS refuses. Neither the clone nor a checkout is a worktree of the
owner's repository. A command's environment is program_env's with SSH_AUTH_SOCK taken out and its mark added, and
nothing else."""

import hashlib
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import time
import unicodedata

from . import processes
from . import task_shapes as shapes
from .workspace import GIT_TIMEOUT, GitHeld, WorkspaceError, _git_env, git, program_env

READ_AFTER_EXIT = 2               # seconds a command's output is read on after it exits
LOST = object()                   # an exit no longer this process's to read (exited)

# the workspace's own words: of a command not run, and of a task's end decided without a round. The app
# keeps the same list (proofpane_open/ade/controller.py, CHECK_WHYS), which a test compares with this one
NOT_AFTER_BASE = "the task's head does not come after its base"
PAST_BOUNDS = "the commit's checkout passes its bounds"
NO_CHECKOUT = "the workspace could not read the commit or make its checkout"
NO_START = "the check could not start"
STOPPED = "its task was stopped"
ENDED = "its task ended"
AGENT_STOPPED = "the workspace's agent stopped while it ran"
NO_BRANCH = "the task's branch could not be read"
NO_COMMIT = "no commit since the task's base"
NO_BASE = "the task was started before the workspace kept its base"
FINISHED = "the branch's head is a commit of the owner's Finish, which no check judges"
SPENT = "this task's checks have run as often as they may"
CLOSED = "its requirement was closed before its checks ran"
# two more: an end round whose branch was moved
# so that it no longer holds the head the program ended on, and rounds the workspace could not start, again and again
HEAD_GONE = "the task's branch no longer holds the commit its program ended on"
NOT_STARTED = "the workspace could not start this task's checks, round after round"
# and one more: a command after one that started, whose round's clone is no longer as it was made,
# which a command of the round could have changed (clone_state)
CHANGED = "the round's clone was changed after it was made"
# and one more: a command whose exit the workspace can no longer read, its process waited for by another
# than the round, which judges nothing (exited)
NO_EXIT = "the workspace could not read the check's exit"
NOT_RUN_WHYS = (NOT_AFTER_BASE, PAST_BOUNDS, NO_CHECKOUT, NO_START, STOPPED, ENDED, AGENT_STOPPED, CHANGED, NO_EXIT)
END_WHYS = (NO_BRANCH, NO_COMMIT, NO_BASE, FINISHED, SPENT, CLOSED, HEAD_GONE, NOT_STARTED)
WHYS = NOT_RUN_WHYS + END_WHYS
# a round holding one of these judges no head: the workspace's own failures, and a cut
JUDGES_NOTHING = (NO_CHECKOUT, NO_START, STOPPED, ENDED, AGENT_STOPPED, NO_EXIT)
OUTCOMES = ("passed", "failed", "timed out", "protected", "not run")
_GLOB = re.compile(r"[*?]")
_IN_CLONE = ("--no-replace-objects", "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null")
# no settings read but the clone's own, and no attributes file of the system's (git's own prefix may be the workspace's
# user's, a Homebrew git's on a Mac, which a session's program could write once for every later checkout)
_NOTHING_READ = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
                 "GIT_NO_REPLACE_OBJECTS": "1"}
# the upload-pack the clone runs on the repository's side reads none of the files a session's program could write there
# to change the history it sends: no graft (info/grafts), no replace ref, no shallow file (a line in .git/shallow would
# make the clone shallow, lacking the task's base), no commit-graph and no reachability bitmap. git takes the variables
# out of what it gives a process it starts on another repository (its local_repo_env), so they are set in the command
# itself, which git runs through the shell; an empty GIT_SHALLOW_FILE is git's own way of saying no shallow file
UPLOAD_PACK = ("GIT_GRAFT_FILE=/dev/null GIT_NO_REPLACE_OBJECTS=1 GIT_SHALLOW_FILE= git "
               "-c advice.graftFileDeprecated=false -c core.commitGraph=false -c pack.useBitmaps=false upload-pack")


class Unrun(Exception):
    """A round that cannot run on its commit: every command "not run" with `why`."""

    def __init__(self, why):
        super().__init__(why)
        self.why = why


class Moved(Exception):
    """The session's branch no longer holds the round's head: the round is let go."""


class Gone(Exception):
    """An end round's branch no longer holds the head the task's program ended on (a branch moved elsewhere, or
    rewritten): its end is decided without a round."""


class Held(Exception):
    """The repository's places are held (a GitHeld, until the owner resumes an agent): the round starts nothing, spends
    nothing and is tried again."""


def check_env(mark):
    """A command's environment: program_env's (PASS_ENV's names, the terminal's type, on macOS bash's notice
    silenced), SSH_AUTH_SOCK taken out, and its mark, and nothing else."""
    env = program_env({processes.MARK: mark})
    env.pop("SSH_AUTH_SOCK", None)
    return env


def _clone_env(folder):
    """The environment of a git in a round's clone: _git_env's with no folder (its record of git's places is the
    owner's repositories', and a clone is none of them), git looking no higher than the clone, SSH_AUTH_SOCK taken out,
    and no settings read but the clone's own."""
    env = _git_env(None)
    env.pop("SSH_AUTH_SOCK", None)
    env.update(_NOTHING_READ, GIT_CEILING_DIRECTORIES=os.path.dirname(os.path.realpath(folder)))
    return env


def _in_clone(clone, *args, ok=(0,), timeout=GIT_TIMEOUT):
    """A git in the round's clone: (exit code, output). Unrun(NO_CHECKOUT) where it fails with a code not in `ok`, or
    runs past `timeout`, never git's own message, which can name a committed file: only the clone's own time and
    the head's files' bytes judge the head, never the workspace's slowness."""
    try:
        p = subprocess.run(["git", *_IN_CLONE, *args], cwd=clone, env=_clone_env(clone), capture_output=True,
                           timeout=timeout, input=b"")
    except (subprocess.TimeoutExpired, OSError):
        raise Unrun(NO_CHECKOUT) from None
    if p.returncode not in ok:
        raise Unrun(NO_CHECKOUT)
    return p.returncode, p.stdout.decode("utf-8", "surrogateescape")


def make_clone(repo_path, session_id, head, round_dir, timeout, end=False):
    """A clone of the session's branch, in `round_dir`/clone, by the workspace's own git (_git_env's
    environment, the repository's places checked first), over git's own transport, SSH_AUTH_SOCK taken out, no settings
    read but the clone's own and no template. Its folder. A rest round's branch must hold `head` at its tip, else Moved;
    an end round's (`end`) must hold it at its tip or below it, the head its program ended on, which is what is judged
    and checked out, else Gone. Held where the repository's places are held; Unrun(NO_CHECKOUT) where git fails or runs
    past `timeout`, never with git's message: a clone's time is the workspace's and the repository's as much as the
    commit's (a program another session runs can stall the repository's side), so it judges nothing."""
    dest = os.path.join(round_dir, "clone")
    try:
        env = _git_env(repo_path, network=True)
    except GitHeld:
        raise Held() from None
    except WorkspaceError:
        raise Unrun(NO_CHECKOUT) from None
    env.pop("SSH_AUTH_SOCK", None)
    env.update(_NOTHING_READ)
    try:
        p = subprocess.run(["git", "clone", "--quiet", "--no-checkout", "--no-tags", "--single-branch", "--branch",
                            f"pp/{session_id}", "--template=", "--upload-pack", UPLOAD_PACK,
                            "file://" + os.path.realpath(repo_path), dest],
                           cwd=repo_path, env=env, capture_output=True, timeout=timeout, input=b"")
    except (subprocess.TimeoutExpired, OSError):
        raise Unrun(NO_CHECKOUT) from None
    if p.returncode != 0:
        raise Unrun(NO_CHECKOUT)
    code, got = _in_clone(dest, "rev-parse", "--verify", "--quiet", f"refs/heads/pp/{session_id}^{{commit}}",
                          ok=(0, 1))
    tip = got.strip() if code == 0 else None
    if tip == head:
        return dest
    if not end:
        raise Moved()
    # the owner's Finish or Update, or a program left running, can commit on the branch after its program ended at rest:
    # the end is judged on the head it ended on, wherever the branch went on from it
    if tip is None or not _holds(dest, head) or \
            _in_clone(dest, "merge-base", "--is-ancestor", head, tip, ok=(0, 1))[0] != 0:
        raise Gone()
    return dest


def _holds(clone, rev):
    """Whether the round's clone holds the commit `rev`: rev-parse exits 1 for one it does not hold, and git's own
    failure (128: no repository there, say) is Unrun(NO_CHECKOUT), which decides nothing, never taken for a commit the
    clone lacks."""
    return _in_clone(clone, "rev-parse", "--verify", "--quiet", rev + "^{commit}", ok=(0, 1))[0] == 0


def fold(name):
    """A name as a Mac's volume takes it by default: its case folded, and Unicode's forms of it made one."""
    return unicodedata.normalize("NFC", name).casefold()


def protected_count(changed, patterns):
    """How many of the names changed since the base a protected path covers: a task's attempt's matcher
    (task_attempt.protected_paths) on each name and each protected path folded, so that TESTS/conftest.py is under a
    protected tests/** and a new makefile is a protected Makefile; and a name that is, folded, a protected path's fixed
    leading folder or a folder above it (a link, or a file, put where tests was)."""
    from .task_attempt import protected_paths
    folded = [fold(p) for p in patterns]
    names = [fold(n) for n in changed]
    covered = set(protected_paths(names, folded))
    folders = set()
    for p in folded:
        fixed = []
        for part in p.rstrip("/").split("/"):
            if _GLOB.search(part):
                break
            fixed.append(part)
        folders.update("/".join(fixed[:i]) for i in range(1, len(fixed) + 1))
    return sum(1 for n in names if n in covered or n in folders)


def judge(clone, base, head, patterns, most_bytes):
    """The commits judged in the round's clone: the count of protected paths changed since
    the base, or Unrun(NOT_AFTER_BASE) where the head does not come after the base (or the clone does not hold the
    base, which is then no ancestor of it), Unrun(PAST_BOUNDS) where the head's files pass `most_bytes`, and
    Unrun(NO_CHECKOUT) where a git fails."""
    if not _holds(clone, base):
        raise Unrun(NOT_AFTER_BASE)
    if _in_clone(clone, "merge-base", "--is-ancestor", base, head, ok=(0, 1))[0] != 0:
        raise Unrun(NOT_AFTER_BASE)
    _, out = _in_clone(clone, "diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", base, head)
    count = protected_count([n for n in out.split("\0") if n], patterns)
    if count:
        return count
    _, listed = _in_clone(clone, "ls-tree", "-r", "-l", "-z", head)
    total = 0
    for entry in listed.split("\0"):
        meta = entry.split("\t", 1)[0].split()
        if len(meta) == 4 and meta[1] == "blob" and meta[3].isdigit():
            total += int(meta[3])
    if total > most_bytes:
        raise Unrun(PAST_BOUNDS)
    return 0


def clone_state(clone, known=None):
    """What the round's clone holds outside its worktrees' own folders (.git/worktrees, which git writes for each
    checkout, and a command's own git for its own): each entry under its .git, never followed through a link, by its
    kind, and a file's or a link's mode, size, inode and times, a link's target, and a file's content's digest, taken
    again only where its size, inode or times moved since `known` (a state taken before), so that a pack is read once
    and again only once it moves. A checkout is made only while this has not `changed` since the clone was made: a
    command, or any program, that wrote the clone's settings, attributes or the objects it
    held would otherwise have the workspace's own git run a filter of its writing, or check out objects it rewrote, for
    a later command. None where it cannot be read."""
    top = os.path.join(clone, ".git")
    try:
        if not (stat.S_ISDIR(os.lstat(clone).st_mode) and stat.S_ISDIR(os.lstat(top).st_mode)):
            return None
        state = {"": (os.lstat(clone).st_ino, os.lstat(top).st_ino)}
        folders = [""]
        while folders:
            rel = folders.pop()
            with os.scandir(os.path.join(top, rel) if rel else top) as entries:
                for e in entries:
                    path = f"{rel}/{e.name}" if rel else e.name
                    st = e.stat(follow_symlinks=False)
                    if stat.S_ISDIR(st.st_mode):
                        if path != "worktrees":         # git's own, for each checkout: not compared
                            state[path] = ("dir",)
                            folders.append(path)
                    elif stat.S_ISLNK(st.st_mode):
                        state[path] = ("link", os.readlink(e.path), st.st_mode, st.st_size, st.st_ino,
                                       st.st_mtime_ns, st.st_ctime_ns)
                    elif stat.S_ISREG(st.st_mode):
                        ident = (st.st_mode, st.st_size, st.st_ino, st.st_mtime_ns, st.st_ctime_ns)
                        before = (known or {}).get(path)
                        digest = before[6] if before and before[0] == "file" and before[1:6] == ident \
                            else _digest(e.path, st)
                        state[path] = ("file", *ident, digest)
                    else:
                        state[path] = ("other", stat.S_IFMT(st.st_mode))
    except OSError:
        return None
    return state


# what a check's own git may write in the round's clone, which no git of the workspace's reads after the clone is made:
# refs and their logs (a checkout is of a commit's id, never a ref's name) and the files named for a HEAD; and loose
# objects the clone did not hold when it was made, since git reads an object from a pack before it looks for a loose
# one, and every object a checkout needs came in the clone's packs
_FREE = re.compile(r"(?:refs|logs)(?:/.*)?|packed-refs|[A-Z_]*HEAD")
_LOOSE = re.compile(r"objects/[0-9a-f]{2}(?:/[0-9a-f]+)?")


def changed(made, now):
    """Whether the round's clone is no longer as it was made (clone_state, `made` then and `now`), in what a later
    checkout reads: its settings, attributes, hooks, packs and the loose objects it held then, each file by its kind,
    mode, size and content, a link by its target, a folder by its being there. A check's own git writing its refs, its
    stash or new loose objects (`git add`, `git commit`, `git stash` in its checkout) changes nothing a later check
    needs, nor does git's freshening of a pack that holds an object it writes, which moves the pack's times alone.
    True where either cannot be read."""
    if made is None or now is None:
        return True
    then = {k: _read_of(v) for k, v in made.items() if not _FREE.fullmatch(k)}
    later = {k: _read_of(v) for k, v in now.items() if not _FREE.fullmatch(k)}
    for key in set(then) | set(later):
        if key not in then and _LOOSE.fullmatch(key):
            continue
        if then.get(key) != later.get(key):
            return True
    return False


def _read_of(entry):
    """What git reads of an entry of the clone_state: a file's kind, mode, size and content; a link's target."""
    if entry[0] == "file":
        return entry[0], entry[1], entry[2], entry[6]
    if entry[0] == "link":
        return entry[0], entry[1]
    return entry


def _digest(path, st):
    """A file's content's digest, read never through a link nor from what is not the file lstat gave."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if os.fstat(fd).st_ino != st.st_ino:
            raise OSError(f"{path} changed while it was read")
        h = hashlib.sha256()
        while chunk := os.read(fd, 65536):
            h.update(chunk)
        return h.hexdigest()
    finally:
        os.close(fd)


def make_checkout(clone, head, folder, timeout):
    """A command's checkout: a detached worktree of the round's clone at `head`, in `folder`, by
    git's own checkout, made just before the command runs, and only while the clone is as it was made (clone_state).
    Unrun as _in_clone gives it."""
    _in_clone(clone, "worktree", "add", "--quiet", "--detach", folder, head, timeout=timeout)


def remove_checkout(folder):
    """A command's checkout removed, whatever happened: its folder, with no git run in a clone a check could have
    written (the clone, and its record of the worktree, go with the round's folder)."""
    shutil.rmtree(os.path.dirname(folder), ignore_errors=True)


def start_command(command, cwd, mark):
    """A command started in its checkout, in a session of its own: /bin/sh -c, its output and standard error on
    one pipe, nothing on its input. The caller holds the record's lock."""
    return subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, env=check_env(mark), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)


def read_output(p, seconds=None, began=None):
    """(exit, tail): the command's exit code, None where it ran past `seconds` (CHECK_SECONDS by default) from when it
    `began` (the time.monotonic() of its start; now where not given), or LOST where its exit can no longer be read, and
    the last CHECK_TAIL bytes of its output, read in this thread with a deadline, so that its memory is bounded, and on
    for READ_AFTER_EXIT seconds at most once it exits, so that a program it left holding the pipe does not hold the
    thread. Its process group is not killed here (end_command)."""
    seconds = shapes.CHECK_SECONDS if seconds is None else seconds
    fd = p.stdout.fileno()
    os.set_blocking(fd, False)
    tail = bytearray()
    deadline = (time.monotonic() if began is None else began) + seconds
    # a selector, not select(): select refuses a descriptor at or past FD_SETSIZE, which a busy agent can reach
    with selectors.DefaultSelector() as sel:
        sel.register(fd, selectors.EVENT_READ)
        code = _read_into(p, fd, sel, tail, deadline)
    return code, bytes(tail)


def exited(p):
    """The command's exit code once it has exited (minus the signal that ended it), else None, read without reaping it
    (os.waitid's WNOWAIT): until reap takes it, its process id, which is its group's, is given to no other process, so
    that a kill by the id the controller's record still names reaches nothing but what is left of the command's own
    group. LOST where the process is no longer this one's to wait for (another wait took it), whose exit
    is not known: never 0, as Popen.poll would take it, so that a check whose exit is not read never passes. Where
    os.waitid is missing (a Mac's Python before 3.13; the workspace's image runs Debian's 3.13 and the Mac app ships
    3.13, both with it), os.waitpid, which reaps at once and so reopens what this guards against, but takes an exit
    another wait took as LOST all the same."""
    if not hasattr(os, "waitid"):
        if p.returncode is not None:
            return p.returncode
        try:
            pid, status = os.waitpid(p.pid, os.WNOHANG)
        except ChildProcessError:
            return LOST
        if pid == 0:
            return None
        p.returncode = os.waitstatus_to_exitcode(status)
        return p.returncode
    try:
        got = os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return LOST
    if got is None:
        return None
    return got.si_status if got.si_code == os.CLD_EXITED else -got.si_status


def _read_into(p, fd, sel, tail, deadline):
    """read_output's loop: the exit code, or None past the deadline; the command is not reaped here (exited)."""
    exited_at = code = None
    while True:
        now = time.monotonic()
        if exited_at is None:
            code = exited(p)
            if code is not None:
                exited_at = now
        if exited_at is not None and now - exited_at >= READ_AFTER_EXIT:
            break
        if exited_at is None and now >= deadline:
            return None
        wait = 0.25 if exited_at is not None else min(0.25, max(0.0, deadline - now))
        try:
            ready = sel.select(wait)
        except OSError:
            time.sleep(wait)
            ready = []
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            continue
        except OSError:
            chunk = b""
        if not chunk:
            # its output closed: what is left is to wait for its exit, within its time
            while code is None:
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.05)
                code = exited(p)
            break
        tail += chunk
        if len(tail) > shapes.CHECK_TAIL:
            del tail[:-shapes.CHECK_TAIL]
    return code


def kill_group(pid):
    """A command's process group killed, waiting on nothing."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except (OSError, TypeError):
        pass


def end_command(p, mark):
    """After a command, whatever happened: its process group killed, what carries its mark ended (on a Mac, what its
    owned session holds too), its pipe closed; not reaped, so that its id is still its own (exited), which the
    controller lets go of first (reap). Outside the record's lock: processes.end can wait on /bin/ps."""
    kill_group(p.pid)
    try:
        processes.end(mark, 0.5)
    except Exception:  # noqa: BLE001  what is left is found by its mark at the next end or restart
        pass
    try:
        p.stdout.close()
    except OSError:
        pass


def reap(p):
    """A command's process reaped, once the controller's record no longer names its id: waited for five seconds at
    most, its group killed already (end_command)."""
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def outcome(command_id, code, tail):
    """A command's result as the round keeps it: passed, failed with its exit code (minus the signal that
    ended it), or timed out; its output's last bytes decoded as UTF-8 with replacement. One whose exit could not be
    read (LOST) is "not run" with the workspace's words, which judge nothing."""
    if code is LOST:
        return not_run(command_id, NO_EXIT)
    text = tail.decode("utf-8", "replace")
    if code is None:
        return {"id": command_id, "outcome": "timed out", "tail": text}
    if code == 0:
        return {"id": command_id, "outcome": "passed", "tail": text}
    return {"id": command_id, "outcome": "failed", "exit": code, "tail": text}


def not_run(command_id, why):
    return {"id": command_id, "outcome": "not run", "why": why}


def protected(command_id, count):
    return {"id": command_id, "outcome": "protected", "protected": count}


def judges(results):
    """Whether a round with these results judged its head: it ran to its end on the commit."""
    return bool(results) and not any(r["outcome"] == "not run" and r["why"] in JUDGES_NOTHING for r in results)


def passed(results):
    return bool(results) and all(r["outcome"] == "passed" for r in results)


def finish_commit(repo_path, head, session_id):
    """Whether `head` is a commit of the owner's Finish for this session: the line Finish writes,
    "Proofpane-Session: <the session's id>", among the trailers of its message, its last paragraph. Read by the
    workspace's own git in the repository's folder, the line compared alone and given to nothing. A WorkspaceError
    where the commit cannot be read: the head is then not taken, so that no end is decided on a commit not known to be
    or not to be Finish's."""
    _, body = git("--no-replace-objects", "cat-file", "commit", head, cwd=repo_path)
    message = body.split("\n\n", 1)[1] if "\n\n" in body else ""
    paragraphs = [p for p in message.strip("\n").split("\n\n") if p.strip()]
    last = paragraphs[-1].split("\n") if paragraphs else []
    return f"Proofpane-Session: {session_id}" in (line.strip() for line in last)


def read_head(repo_path, session_id):
    """The head of the session's branch, read in the repository's folder: the commit, or None where the
    branch does not exist (rev-parse exiting 1). A WorkspaceError where git could not run (a GitHeld, a time-out),
    which decides nothing."""
    code, out = git("--no-replace-objects", "rev-parse", "--verify", "--quiet",
                    f"refs/heads/pp/{session_id}^{{commit}}", cwd=repo_path, ok=(0, 1))
    return out.strip() if code == 0 else None
