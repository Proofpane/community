"""A Claude Code session moved into the workspace from a linked machine (the owner's Mac): the files the app sends for
it, the move it describes, checked, and its transcript made this workspace's.

The app hands a move over in three requests, each with the workspace's secret (agent.py): the session's transcript, as
it was on the machine; a git bundle of what the repository's remotes lack, when they lack anything; and the session to
start, whose `moved` says (check):

    id        the move's seq in the app, a decimal string
    session   Claude Code's id for the session: its transcript's name, and the conversation its claude resumes
    remote    the URL of the repository's origin on the machine, in whatever form it is written there
    branch    the branch the machine was on, or None for none (a label only)
    head      the commit the machine was on
    changes   a commit on `head` whose tree is the work not committed (files staged, unstaged and new that git does
              not ignore), or None when there was none
    cwd       the repository's folder on the machine
    bundle    whether a bundle was sent

The files are kept under the workspace's root, `moves/<seq>/transcript` and `moves/<seq>/bundle`, each written whole
through a temporary file, until the session they came for is started or refused; a move's files that no session asked
for go a day after the last of them came (sweep). The session's programs run as the workspace's user and can change
that folder while the workspace works in it, so every part of the way to a move's file is opened without following a
link, and a file is read once, whole, through the descriptor opened on it (take), never opened again by its path.
Nothing here runs git or reads a repository: workspace.py does, in the repository whose origin is the same as `remote`
(remote_name), never fetching from `remote` itself.
"""

import contextlib
import json
import os
import re
import secrets
import shutil
import stat
import time
from urllib.parse import urlsplit

MAX_FILE = 32 * 1024 * 1024         # a transcript, and a bundle
KEEP = 24 * 3600                    # how long a move's files that no session asked for are kept
FILES = ("transcript", "bundle")
SEQ_MAX = 2 ** 63 - 1               # the largest seq the app stores
MAX_REMOTE = 2000
MAX_BRANCH = 255
MAX_CWD = 4096
BUNDLE_HEADER = 1024 * 1024         # what is read of a bundle for its header: thousands of prerequisites fit
_SEQ = re.compile(r"^[1-9][0-9]{0,18}$")
# a Claude Code session's id, as the move's command names it (its transcript is <id>.jsonl)
SESSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,99}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")          # SHA-1, or SHA-256
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_HOST = re.compile(r"^[A-Za-z0-9.-]+$")
_SCP = re.compile(r"^(?:[^@/:]+@)?([^@/:]+):(.+)$")    # [user@]host:path, git's short form for ssh: no / before the :
_SCHEMES = ("https", "http", "ssh", "git", "git+ssh", "ssh+git")
_FOLDER = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW    # a folder of moves: never a link followed to one


class MoveError(ValueError):
    """A move that cannot be taken, said as it is."""


def check_seq(seq):
    """A move's seq as the app names it: a decimal string (the number is taken too); None for anything else."""
    if isinstance(seq, int) and not isinstance(seq, bool):
        seq = str(seq)
    if not isinstance(seq, str) or not _SEQ.match(seq) or int(seq) > SEQ_MAX:
        return None
    return seq


def check(moved):
    """`moved` as the app gives it, checked: {id, session, remote, branch, head, changes, cwd, bundle} as the module
    says; MoveError saying what is wrong."""
    if not isinstance(moved, dict):
        raise MoveError("A moved session comes with its move: id, session, remote, branch, head, changes, cwd and "
                        "bundle.")
    seq = check_seq(moved.get("id"))
    if seq is None:
        raise MoveError("A move's id is its seq, a whole number.")
    session = moved.get("session")
    if not isinstance(session, str) or not SESSION.match(session):
        raise MoveError("A Claude Code session's id is 1 to 100 letters, digits, dashes and underscores, starting with "
                        "a letter or a digit.")
    remote = moved.get("remote")
    if not isinstance(remote, str) or not remote.strip() or len(remote) > MAX_REMOTE or _CONTROL.search(remote):
        raise MoveError("A moved session names its repository by the URL of its origin.")
    branch = moved.get("branch")
    if branch is not None and (not isinstance(branch, str) or not branch or len(branch) > MAX_BRANCH
                               or _CONTROL.search(branch)):
        raise MoveError("A moved session's branch is a branch's name, or null.")
    head, changes = moved.get("head"), moved.get("changes")
    if not isinstance(head, str) or not _COMMIT.match(head):
        raise MoveError("A moved session's head is a commit's id.")
    if changes is not None and (not isinstance(changes, str) or not _COMMIT.match(changes)):
        raise MoveError("A moved session's changes are a commit's id, or null.")
    cwd = moved.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/") or len(cwd) > MAX_CWD or _CONTROL.search(cwd) \
            or not cwd.rstrip("/"):
        raise MoveError("A moved session's folder is its repository's full path on the machine, from /.")
    if not isinstance(moved.get("bundle"), bool):
        raise MoveError("A move says whether it sent a bundle: true or false.")
    return {"id": seq, "session": session, "remote": remote, "branch": branch, "head": head, "changes": changes,
            "cwd": cwd, "bundle": moved["bundle"]}


def remote_name(url):
    """A repository's URL as one name, so that two written in different forms compare: its host, owner and repository
    (and any groups between them), in lower case, without .git, whatever its scheme, user, port or trailing slash, or
    written as its host and path alone (as the machine's client sends it); None for what names no repository on a host
    (a local path, a file:// URL)."""
    if not isinstance(url, str) or len(url) > MAX_REMOTE or _CONTROL.search(url):
        return None
    url = url.strip()
    if "://" in url:
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
        except ValueError:
            return None
        if parts.scheme.lower() not in _SCHEMES:
            return None
        path = parts.path
    else:
        m = _SCP.match(url)
        if m:
            host, path = m.group(1), m.group(2)
        else:
            # its host and path alone: the first name is taken for a host when it has a dot inside it, as a
            # domain's name does; one without, or starting with a dot (. and ..), is a local folder's
            host, _, path = url.partition("/")
            if "." not in host.strip(".") or host.startswith("."):
                return None
    host = host.lower().rstrip(".")
    names = [n for n in path.split("/") if n]
    if names and names[-1].lower().endswith(".git"):
        names[-1] = names[-1][:-4]
    if not host or not _HOST.match(host) or len(names) < 2 or any(n in ("", ".", "..") for n in names):
        return None
    return "/".join([host] + [n.lower() for n in names])


def _folder(root, seq=None, make=False):
    """A descriptor of the workspace's folder of moves, or of one move's folder in it (`seq`): each opened from the one
    above it without following a link, and made first with `make`; None when one is not there as a folder of its own
    (a link, a file). The workspace's own root is taken as it is given. The caller closes it."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in ("moves",) + ((seq,) if seq else ()):
            if make:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(name, 0o700, dir_fd=fd)
            below = os.open(name, _FOLDER, dir_fd=fd)
            os.close(fd)
            fd = below
        return fd
    except OSError:
        os.close(fd)
        return None


def write_whole(path, data):
    """Write `data` to `path`, the folders above it made: through a temporary file beside it, so the file is never
    there half written; only the workspace's user reads it (a conversation can hold anything)."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def store(root, seq, name, data, now=None):
    """Keep one of a move's files (FILES), whole, in its folder, through a temporary file beside it that only the
    workspace's user reads; first, the files of moves no session asked for within KEEP go. MoveError when the folder of
    moves, or the move's, is a link or a file: nothing is written through it."""
    sweep(root, now)
    fd = _folder(root, seq, make=True)
    if fd is None:
        raise MoveError("The workspace's folder for this move is a link or a file, not a folder of its own: nothing is "
                        "written through it.")
    try:
        tmp = f"{name}.{secrets.token_hex(4)}.tmp"
        out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, "wb") as fh:
                fh.write(data)
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=fd)
            raise
    finally:
        os.close(fd)


def _remove_in(folder, name):
    """A file, a link or a folder in the open folder `folder` gone, whichever it is: a link is never followed."""
    try:
        if stat.S_ISDIR(os.lstat(name, dir_fd=folder).st_mode):
            shutil.rmtree(name, ignore_errors=True, dir_fd=folder)
        else:
            os.unlink(name, dir_fd=folder)
    except OSError:
        pass


def sweep(root, now=None):
    """The files of moves that no session asked for, once KEEP seconds have passed since the last of them came."""
    now = time.time() if now is None else now
    fd = _folder(root)
    if fd is None:
        return
    try:
        for name in os.listdir(fd):
            try:
                if now - os.lstat(name, dir_fd=fd).st_mtime > KEEP:
                    _remove_in(fd, name)
            except OSError:
                continue
    finally:
        os.close(fd)


def discard(root, seq):
    """A move's files gone, once the session they came for is started or refused."""
    fd = _folder(root) if seq else None
    if fd is None:
        return
    try:
        _remove_in(fd, seq)
    finally:
        os.close(fd)


def take(root, seq, name):
    """A move's file as it came, or None unless it came as a regular file of at most MAX_FILE bytes in the move's own
    folder: every part of the way opened without following a link, the file opened without waiting (the session's
    programs run as the workspace's user, and could put a link, a folder or a pipe in a file's place, or a link in a
    folder's), and read whole through that one descriptor, so nothing of it is opened again by its path."""
    fd = _folder(root, seq)
    if fd is None:
        return None
    try:
        got = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError:
        return None
    finally:
        os.close(fd)
    # looked at before it is wrapped, which a folder is refused by
    if not stat.S_ISREG(os.fstat(got).st_mode):
        os.close(got)
        return None
    with os.fdopen(got, "rb") as fh:
        data = fh.read(MAX_FILE + 1)
    return data if len(data) <= MAX_FILE else None


def prerequisites(bundle):
    """The commits a bundle (its bytes) needs and does not hold, from its header (gitformat-bundle: a line
    "-<id> <subject>" for each, before the empty line that ends it); [] for none, and for bytes that are no bundle."""
    head = bundle[:BUNDLE_HEADER]
    end = head.find(b"\n\n")
    lines = head[:end].split(b"\n") if end >= 0 else []
    if not lines or lines[0] not in (b"# v2 git bundle", b"# v3 git bundle"):
        return []
    found = []
    for line in lines[1:]:
        if line.startswith(b"-"):
            commit = line[1:].split(b" ", 1)[0].decode("ascii", "replace")
            if _COMMIT.match(commit):
                found.append(commit)
    return found


def rewrite(data, machine, work):
    """The transcript `data` with each entry's `cwd` that is the machine's folder `machine`, or a folder inside it,
    made the worktree's folder `work`, or the same folder inside it. Every other line, and every line that is not a
    JSON object with such a cwd, stays byte for byte as it was."""
    machine = machine.rstrip("/")
    return b"\n".join(_rewrite_line(line, machine, work) for line in data.split(b"\n"))


def _rewrite_line(line, machine, work):
    if b'"cwd"' not in line:
        return line
    try:
        entry = json.loads(line)
    except (ValueError, RecursionError):
        return line
    cwd = entry.get("cwd") if isinstance(entry, dict) else None
    if not isinstance(cwd, str) or not (cwd == machine or cwd.startswith(machine + "/")):
        return line
    entry["cwd"] = work + cwd[len(machine):]
    # as Claude Code writes an entry: compact, its text as it is
    text = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        # half of a character (an unpaired surrogate) in it, which the line had as an escape: kept as one
        return json.dumps(entry, separators=(",", ":")).encode("ascii")
