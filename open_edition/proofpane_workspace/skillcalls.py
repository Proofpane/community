"""The workspace's own record of the skills its sessions called: the lines each
session's skills hook wrote to its own `skills.jsonl` (activity.py --skills), taken into one file of the workspace's,
outside every session's folder, and served in pages to the app (GET /skills?since=N, agent.py).

The session's file is the session's own word, which its programs can write; this record is kept apart, and the gate
refuses a tool call that spells the file's name (gate.SKILLS_RECORD, as it refuses git-places.json and
controller-record.json). That is a rule on a name: a glob, an operation on the folder (`rm -rf`, `mv`) or a program that
builds the name is not caught, and a program a session runs can write the record as every program of the workspace runs
as one user, so what it holds is for display, not a record that holds against a session.

Taking (`take`): each session's file is read from where the last take ended, by the file's identity and the offset it
was read to. `activity._append` cuts a file that passes its bound to its newest half by writing a new file in its
place (`_trim`), so a new identity means a cut, and since a new file can be given the identity an earlier one had once
that one is gone, a file shorter than the offset read to, or whose line ending at that offset is not the last one taken,
is read as cut too. A cut file is read from the line after the last one taken, found by that line (a digest of its
last TAIL_KEEP bytes: for a line the hook wrote that is all of it, and holds its id; a line out of shape has none, and
is found too, of whatever length), or from its start where that line is gone: the cut keeps the newest lines, so every
line left is newer. A last line not yet ended is left for the next take, and so is every line past the first
LINES_MOST of those a take reads (a file of a session's programs can hold any number of them: it is taken a little at a
time, from the line after the last one looked at). READ_MOST bytes from the offset with no newline in them are the start
of a line no hook wrote (its lines are a few hundred bytes): they are passed over and counted as one line out of shape,
and the take goes on after the first newline past them, or in the file's last READ_MOST bytes where it runs on farther
(the lines between are passed over too); where there is no newline there either, nothing is taken, nor counted, until
there is. The identity, the offset and that line's digest are kept in the record, so that a restart of the workspace
takes no line twice. Each line is held to its shape (each field to its pattern and size, the kinds and paths and
versions the hook writes, and none but its fields): one out of shape is dropped and counted (`dropped`).

The record keeps at most MAX_LINES lines and KEPT_DAYS days of them, the oldest dropped first, each with its session's
id, its repository and the controller's task id where it has one (a removed session's calls keep where they were made),
each numbered within the record, and a page of them is at most PAGE lines after a number, with whether more follow. The
record has an id of its own (`store`), made with it: one made anew (a new volume, or one put aside because it could not
be read) has another, so that the app starts its reading again from the start.
"""

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import threading
import time

from . import activity

FILE = "skill-calls.json"           # the record's name, in its own folder: the gate refuses a call that names it
MAX_LINES = 20000
KEPT_DAYS = 30
PAGE = 1000
MAX_RECORD = 32 * 1024 * 1024       # a record larger than any this writes is not read
READ_MOST = 1024 * 1024             # the bytes of a session's file taken at once
LINES_MOST = 4000                   # the lines of one session's file one take examines: the rest wait for the next take
LINE_MOST = 2048                    # a line of a session's file longer than this is out of shape
TAIL_LOOK = 8192                    # the bytes before an offset read to find the line that ends there
TAIL_KEEP = 4096                    # a line is known by its last this many bytes: a line out of shape can be longer
                                    # than TAIL_LOOK, and its end is what an offset's look holds
FIELDS = ("id", "at", "skill", "kind", "path", "failed", "version")
KINDS = ("core", "business", "other")
PATHS = ("claude", "typed")
_ID = re.compile(r"[0-9a-f]{16}")
_BUSINESS = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}|uncommitted|unknown")
_CORE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,31}\+(?:[0-9a-f]{64}|cut|unknown)")
_STORE = re.compile(r"[0-9a-f]{32}")
_SESSION = re.compile(r"[0-9a-f]{12}")
_TASK = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.:-]{0,79}")
_REPO = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.-]{0,99}")
SLACK = 86400                       # a line may be this far ahead of the workspace's clock


def _text(value):
    return value if isinstance(value, str) else None


def line_in_shape(row, now):
    """Whether `row` (a line of a session's skills.jsonl, parsed) is one the hook writes: exactly its fields, each of its
    pattern, and the combinations it makes (a typed call is a core or a business skill's and has no outcome; a call
    Claude chose has one; a version is of the form of its kind's, and an `other` skill has none)."""
    if not isinstance(row, dict) or set(row) != set(FIELDS):
        return False
    at = row["at"]
    if not (isinstance(row["id"], str) and _ID.fullmatch(row["id"]) and type(at) is int and 0 < at <= now + SLACK):
        return False
    skill, kind, path, failed, version = (row[k] for k in ("skill", "kind", "path", "failed", "version"))
    if not (isinstance(skill, str) and activity.NAME.fullmatch(skill) and kind in KINDS and path in PATHS):
        return False
    if (path == "typed") != (failed is None) or not (failed is None or type(failed) is bool):
        return False
    if path == "typed" and kind == "other":
        return False
    if kind == "other":
        return version is None
    pattern = _CORE if kind == "core" else _BUSINESS
    return isinstance(version, str) and pattern.fullmatch(version) is not None


def _digest(line):
    """What a line is known by: a digest of its last TAIL_KEEP bytes (the whole line, where it is shorter)."""
    return hashlib.sha256(line[-TAIL_KEEP:]).hexdigest()[:32]


def _lines(data, ended, start):
    """The lines of `data` up to its last newline (the one at index `ended`), one at a time, each as (the file's offset
    right after its newline, the line without it): a file of a million short lines is never a list of a million."""
    pos = 0
    while pos <= ended:
        end = data.find(b"\n", pos)
        yield start + end + 1, data[pos:end]
        pos = end + 1


def _fresh():
    return {"v": 1, "store": secrets.token_hex(16), "next": 1, "dropped": 0, "lines": [], "cursors": {}}


def _held_line(row):
    """Whether `row` is a line as the record holds it (its fields, the session's id, its repository and task)."""
    if not isinstance(row, dict) or set(row) != set(FIELDS) | {"n", "session", "repo", "task"}:
        return False
    if type(row["n"]) is not int or row["n"] < 1:
        return False
    if not (isinstance(row["session"], str) and _SESSION.fullmatch(row["session"])):
        return False
    if row["repo"] is not None and not (isinstance(row["repo"], str) and _REPO.fullmatch(row["repo"])):
        return False
    if row["task"] is not None and not (isinstance(row["task"], str) and _TASK.fullmatch(row["task"])):
        return False
    return line_in_shape({k: row[k] for k in FIELDS}, 2 ** 62)


def _sound(record):
    """Whether `record` is one this writes: its fields, a store id, lines in the order of their numbers each as held,
    numbers below `next`, and each session's cursor with its identity, its offset and its tail's digest."""
    if not isinstance(record, dict) or set(record) != {"v", "store", "next", "dropped", "lines", "cursors"}:
        return False
    if record["v"] != 1 or not (isinstance(record["store"], str) and _STORE.fullmatch(record["store"])):
        return False
    if type(record["next"]) is not int or record["next"] < 1 or type(record["dropped"]) is not int:
        return False
    if not isinstance(record["lines"], list) or not isinstance(record["cursors"], dict):
        return False
    last, seen = 0, set()
    for row in record["lines"]:
        if not _held_line(row) or row["n"] <= last or row["n"] >= record["next"] or row["id"] in seen:
            return False
        last = row["n"]
        seen.add(row["id"])
    for sid, c in record["cursors"].items():
        if not (isinstance(sid, str) and _SESSION.fullmatch(sid) and isinstance(c, dict)
                and set(c) == {"dev", "ino", "offset", "tail"}):
            return False
        if not (type(c["dev"]) is int and type(c["ino"]) is int and type(c["offset"]) is int and c["offset"] > 0
                and isinstance(c["tail"], str) and _STORE.fullmatch(c["tail"])):
            return False
    return True


def _read_from(fd, start, most):
    """`most` bytes at most of the open file `fd` from `start`."""
    chunks, left, at = [], most, start
    while left > 0:
        got = os.pread(fd, min(left, 1 << 20), at)
        if not got:
            break
        chunks.append(got)
        left -= len(got)
        at += len(got)
    return b"".join(chunks)


def _tail_is(fd, offset, tail):
    """Whether the line of the file `fd` that ends at `offset` (right after its newline) is the one whose digest is
    `tail`: the last line taken."""
    look = min(offset, TAIL_LOOK)
    blob = _read_from(fd, offset - look, look)
    if len(blob) != look or not blob.endswith(b"\n"):
        return False
    body = blob[:-1]
    # a line that starts before what was read is read by its end, which is all its digest holds (TAIL_KEEP < TAIL_LOOK)
    return _digest(body[body.rfind(b"\n") + 1:]) == tail


def _past_stretch(fd, size, start):
    """The bytes of the file `fd` (of `size` bytes) after the first newline past a stretch of READ_MOST bytes with none
    from `start`, read from there or, where that is farther, from the file's last READ_MOST bytes (the work of a take is
    bounded: the file may be any size), and the offset they begin at; (b"", start) where there is no newline in them."""
    begin = max(start + READ_MOST, size - READ_MOST)
    window = _read_from(fd, begin, READ_MOST)
    first = window.find(b"\n")
    if first < 0:
        return b"", start
    return window[first + 1:], begin + first + 1


class SkillCalls:
    """The record (the module's docstring), on the volume at `<folder>/skill-calls.json`, read and written under one
    lock, from the agent's event loop and from threads alike; nothing awaits while holding it."""

    def __init__(self, folder, now=time.time):
        self.dir = folder
        self.path = os.path.join(folder, FILE)
        self._now = now
        self._lock = threading.RLock()
        self._unsaved = None            # why the record was last not written, said once
        self._dirty = False             # something changed since the record was last written
        self.record = self._load()
        self._ids = {row["id"] for row in self.record["lines"]}

    # the record on the volume

    def _folder(self):
        """The record's folder, made where it is not, and never a link (a session's programs could put one there to have
        the record written elsewhere): an OSError otherwise."""
        try:
            st = os.lstat(self.dir)
        except FileNotFoundError:
            os.makedirs(self.dir, mode=0o700)
            st = os.lstat(self.dir)
        if not stat.S_ISDIR(st.st_mode):
            raise OSError(f"{self.dir} is not a folder of the workspace's own")
        return self.dir

    def _load(self):
        try:
            self._folder()
            st = os.lstat(self.path)
        except FileNotFoundError:
            return _fresh()
        except OSError as e:
            print(f"skills: the record could not be read ({e.strerror or e}): a fresh one", file=sys.stderr)
            return _fresh()
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_RECORD:
            return self._put_aside("is not a file the workspace wrote")
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as fh:
                got = json.loads(fh.read(MAX_RECORD + 1).decode("utf-8"))
        except (OSError, ValueError, RecursionError):
            got = None
        if not _sound(got):
            return self._put_aside("is not one the workspace wrote")
        return got

    def _put_aside(self, why):
        """A record that cannot be taken is put aside (a link renamed, never followed), not written over, so that one
        read that failed, or a later version's, is not lost; the one made in its place has an id of its own."""
        with contextlib.suppress(OSError):
            os.replace(self.path, self.path + ".unread")
        print(f"skills: the record {why}: a fresh one, the one there put aside as {FILE}.unread", file=sys.stderr)
        return _fresh()

    def _save(self):
        """Write the record whole, in place of the last (never through a link, never waiting on a FIFO). One the volume
        will not take (a full disk, a link put where its folder goes) is said on standard error, once until one is
        written again, and the record in memory goes on: a restart then finds the last one written, or none."""
        try:
            self._folder()
            tmp = self.path + ".tmp"
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.record, fh, separators=(",", ":"))
            os.replace(tmp, self.path)
            self._unsaved = None
        except OSError as e:
            why = str(e.strerror or e)
            if self._unsaved != why:
                print(f"skills: the record could not be written ({why})", file=sys.stderr)
            self._unsaved = why

    # taking a session's lines

    def take(self, session_id, session_dir, repo=None, task=None, flush=True):
        """Take the new lines of a session's skills.jsonl (the module's docstring): how many lines were added. `repo` is
        the workspace's name for its repository and `task` the controller's task id, each where it has one, kept with
        each line. The record is written when something changed, unless `flush` is false, for a caller that takes
        many sessions' and writes once (`flush()`). Never raises for a file a session's programs made what they like."""
        if not (isinstance(session_id, str) and _SESSION.fullmatch(session_id)):
            return 0
        repo = repo if isinstance(repo, str) and _REPO.fullmatch(repo) else None
        task = task if isinstance(task, str) and _TASK.fullmatch(task) else None
        try:
            fd = os.open(os.path.join(session_dir, activity.SKILLS),
                         os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return 0                    # none there, or a link: nothing to take
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return 0
            with self._lock:
                added = self._take(fd, st, session_id, repo, task)
                if flush:
                    self.flush()
                return added
        except OSError:
            return 0
        finally:
            os.close(fd)

    def _take(self, fd, st, session_id, repo, task):
        cursor = self.record["cursors"].get(session_id)
        cut = False
        if cursor is None:
            start = 0
        elif (cursor["dev"], cursor["ino"]) == (st.st_dev, st.st_ino) and st.st_size >= cursor["offset"] \
                and _tail_is(fd, cursor["offset"], cursor["tail"]):
            start = cursor["offset"]
        else:
            start, cut = 0, True        # a new identity, a shorter file, or another line at the offset: cut
        data = _read_from(fd, start, READ_MOST)
        ended = data.rfind(b"\n")
        passed = False
        if ended < 0 and len(data) >= READ_MOST:
            # READ_MOST bytes with no newline: the start of a line no hook wrote (its lines are a few hundred bytes),
            # which would hold this take at it for ever. It is passed over as one line out of shape, and the take goes
            # on after the first newline past it (or in the file's last READ_MOST bytes, where that is farther): where
            # there is none yet, nothing is taken, nor counted, until there is
            data, begun = _past_stretch(fd, st.st_size, start)
            ended = data.rfind(b"\n")
            if ended >= 0:
                start, passed = begun, True
        if ended < 0:
            return 0                    # no line has ended: a last line not yet ended is left for the next take
        # a cut file: the line after the last one taken, found by its digest among its first LINES_MOST lines; where it
        # is gone, from the start (every line left is newer than the last one taken)
        skip = 0
        if cut:
            for i, (_, piece) in zip(range(LINES_MOST), _lines(data, ended, start)):
                if _digest(piece) == cursor["tail"]:
                    skip = i + 1
        now = int(self._now())
        added = examined = 0
        if passed:
            self.record["dropped"] += 1
        for i, (at, piece) in enumerate(_lines(data, ended, start)):
            if i >= skip:
                if examined >= LINES_MOST:
                    break               # a file of a session's programs can hold any number of lines: the rest wait
                examined += 1
                if piece.strip() and self._add(piece, now, session_id, repo, task):
                    added += 1
            last = (at, piece)          # the offset right after the last line looked at, and that line
        self.record["cursors"][session_id] = {"dev": st.st_dev, "ino": st.st_ino, "offset": last[0],
                                              "tail": _digest(last[1])}
        self._keep(now)
        self._dirty = True
        return added

    def flush(self):
        """Write the record when something changed since it was last written."""
        with self._lock:
            if self._dirty:
                self._dirty = False
                self._save()

    def _add(self, piece, now, session_id, repo, task):
        """One line of a session's file, kept with the next number when it is in shape and not one held, else counted
        out of shape; whether it was kept."""
        try:
            row = json.loads(piece.decode("utf-8")) if 0 < len(piece) <= LINE_MOST else None
        except (ValueError, RecursionError):
            row = None
        if not line_in_shape(row, now):
            self.record["dropped"] += 1
            return False
        if row["id"] in self._ids:
            return False                # one taken before (a restart between a take and its record)
        n = self.record["next"]
        self.record["next"] = n + 1
        self.record["lines"].append(dict(n=n, **{k: row[k] for k in FIELDS}, session=session_id, repo=repo, task=task))
        self._ids.add(row["id"])
        return True

    def _keep(self, now):
        """The newest MAX_LINES lines of the last KEPT_DAYS days: the oldest dropped first."""
        floor = now - KEPT_DAYS * 86400
        lines = [row for row in self.record["lines"] if row["at"] >= floor][-MAX_LINES:]
        if len(lines) != len(self.record["lines"]):
            self.record["lines"] = lines
            self._ids = {row["id"] for row in lines}

    def forget(self, live):
        """The cursors of sessions that are not in `live` (a set of ids) let go; their lines stay, with where they were
        made."""
        with self._lock:
            gone = [sid for sid in self.record["cursors"] if sid not in live]
            for sid in gone:
                del self.record["cursors"][sid]
            if gone:
                self._dirty = True
                self.flush()

    # serving

    def page(self, since):
        """GET /skills?since=N: the lines numbered after `since`, at most PAGE of them, oldest first, with `more` (lines
        beyond the page), `seq` (the number of the page's last line, else the last number given: where to ask from next),
        `first` (the number of the oldest line held: lines before it are gone), `dropped` (the lines out of shape, in
        all) and the record's `store` id."""
        with self._lock:
            held = self.record["lines"]
            newer = [row for row in held if row["n"] > since]
            chunk = newer[:PAGE]
            return {"store": self.record["store"], "seq": chunk[-1]["n"] if chunk else self.record["next"] - 1,
                    "more": len(newer) > len(chunk), "first": held[0]["n"] if held else None,
                    "dropped": self.record["dropped"], "lines": [dict(row) for row in chunk]}
