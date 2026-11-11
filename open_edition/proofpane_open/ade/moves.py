"""Moving one of a linked machine's Claude Code sessions into the hosted workspace, where it continues: its
conversation and its repository's state go with it.

The owner signs a move as an instruction is signed (link_commands.py, purpose "move", for a session the link's History
list has, not withheld, of which no other move is held or under way: one move of a session at a time, by whichever of
the account's links). Every move is held first (ade/holds.py): it waits HOLD_SETTING seconds (HOLD_DEFAULT unless
set), a notice of it goes at once to the account's devices, and the owner can cancel it until then. Held, it is a row
of ade_link_held_moves and nothing else: no event and no seq, nothing its link reads. So the link's client, which reads
its feed from a cursor, meets the move only once it is stored, at a seq past any it has read. When the hold ends the
move is checked again (_store) and stored as an event to that link, and where it stands is kept on a row of
ade_link_moves, by the command's seq. The link's client takes it (POST /take, once), sends the session's transcript and
a git bundle of what the remotes lack, then says it is done, or why it is not; the app keeps the files on disk and hands
them to the owner's workspace, which starts the session from them, resuming its conversation. A move's states:

    held       signed and held: not stored, so its link has nothing of it (its seq is null)
    cancelled  the owner cancelled it while it was held: never stored
    asked      stored, not taken
    taken      taken by its link, whose client is sending it
    uploaded   its link sent it and said so: it waits for the workspace
    starting   the app is handing it to the workspace
    started    the workspace started it: workspace_session is that session's id
    failed     its link said why it could not (reason), the workspace refused it (in its own words), its link's History
               list has its session withheld since or no longer has it, the owner withdrew it, or the passkey that
               signed it was removed, or was no hardware key when high security was turned on, before its link took
               it, or its link was revoked before it was in the workspace; or, held, a check made as its hold ended
               did not hold, and it was never stored (its seq is null)
    expired    its life ended before its link took it; or its link did not finish sending it within KEPT of taking it,
               or the workspace did not take it within KEPT of its upload, and its files were deleted

A link's requests here are signed with its key, as all of its requests are (link.py), and only the link that took a
move sends anything of it, while it is taken: PUT /api/ade/link/moves/{seq}/transcript and .../bundle (raw bytes, at
most FILE_MAX each), POST .../done and POST .../failed. The files are kept under the data directory (moves/<seq>/),
never in the database, each streamed to a temporary file there and put in place whole once the signature over it
checks; they are deleted once the workspace has the session (or refused it), or KEPT after their upload. The workspace
gets them through workspace_client, with its secret: PUT /moves/{seq}/transcript, PUT /moves/{seq}/bundle when there is
one, then POST /sessions, the usual body with "program": "claude" and "moved". A workspace that is not running leaves
the move uploaded; the owner carries it on from the page (POST /api/ade/link/moves/{seq}/start), which starts the
workspace first when it is not running.
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from .. import auth, config, store
from . import holds, link, link_commands, workspace_client, workspaces

FILE_MAX = 32 * 1024 * 1024         # a transcript or a bundle, as a link sends it
UPLOAD_WAIT = 600                   # seconds a file's whole body may take to come, from its request's start
KEPT = 24 * 3600                    # files after their upload, and a take its link has not finished
FILES = ("transcript", "bundle")
UNDER_WAY = ("asked", "taken", "uploaded", "starting")
REASON_MAX = 300                    # a link's word on why it could not send a move: one line
REMOTE_MAX = 2000
BRANCH_MAX = 255
CWD_MAX = 4096
WORDS_MAX = 1000                    # what is kept of the workspace's words when it refuses a move
LISTED = 20                         # the moves the owner's list gives, newest first
CARRY_ON_WAIT = 8                   # seconds the owner's carry on waits for the hand-off before it answers
PUT_WAIT = 300                      # one of a move's files to the workspace
START_WAIT = 180                    # the workspace starting the session (workspace_routes.FINISH_WAIT)
HAND_OFF_WAIT = 900                 # a hand-off's calls to the workspace, in all
SWEEP_EVERY = 600
INCOMING_KEPT = 3600                # a temporary file an upload cut short left behind
DONE_FIELDS = frozenset({"session", "remote", "branch", "head", "changes", "cwd", "bundle"})
_SHA = re.compile(r"[0-9a-f]{40}")                                      # fullmatch, never match
_BODY_SHA = re.compile(r"[0-9a-f]{64}")             # a body's sha256 as X-Body-SHA256 gives it: fullmatch
_SCP = re.compile(r"([^@/:]+)@([^@/:]+):(.*)")          # git's own form of an ssh address, user@host:path: fullmatch
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")     # an address's scheme, where it starts with one: match
HOLD = "move"                       # the kind of hold every move waits on (ade/holds.py)
HOLD_SETTING = "OPEN_EDITION_ADE_MOVE_HOLD"
HOLD_DEFAULT = 180
HOLD_MARGIN = 300                   # seconds of its life a move must have past its hold, as the owner posts it
LIFE_LEFT = 60                      # and has left when its hold ends, for its link to take it in
HELD_SHOWN = 1800                   # how long Linked sessions shows a move whose hold ended without its being stored

WITHDRAWN = "The passkey that signed it was removed before its link took it."
HIGH_SECURITY = "High security was turned on: a passkey that is not a hardware key signed it."
OWNER_WITHDREW = "You withdrew it before its link took it."
REVOKED = "Its link was revoked before it was in the workspace."
UNLISTED = "Its link's History list does not have the session now."
BUSY = "A move of this session is held or under way already: cancel or withdraw it, or let it end, first."
BUSY_AT_END = "Another move of this session was under way when its hold ended."
NOT_TAKEN = "Its link did not take it before it expired."
NOT_FINISHED = "Its link took it and did not finish sending it within 24 hours."
NOT_STARTED = "The workspace did not take it within 24 hours of its upload, so its files were deleted."
WITHHELD = "Its link's History list has the session withheld now: its words stay on its machine."
GONE = "Its files are not here any more."
UPLOADING = "This file of the move is being sent already: wait for that upload to end."
UNSIGNED_FILE = "A move's file comes with its sha256 in X-Body-SHA256, which the link's signature covers."
NOT_THE_BODY = "The signature does not check: the body that came is not the one it covers."
USER_IN_REMOTE = "The remote's address names a user or a password; the ADE takes a repository's host and path alone."
QUERY_IN_REMOTE = "The remote's address holds a query or a fragment; the ADE takes a repository's host and path alone."
UNREAD_REMOTE = "The remote's address cannot be read as one."
NOT_RUNNING = "The workspace is not running: start it, and carry the move on."
NOT_ANSWERING = "The workspace did not answer: carry the move on again."
AGAIN = "Carry the move on again."
CUT_SHORT = "Handing it to the workspace was cut short (the app stopped): carry it on again."
TOO_LONG = "Handing it to the workspace took too long: carry it on again."
ERROR = "Handing it to the workspace failed on an error in the ADE (it is logged): carry it on again."
NO_SESSION = "The workspace's answer named no session."
NOT_IN_WORKSPACE = ("This session's repository, {name} ({folder}), is not in your workspace: add it there first (Add a "
                    "repository), then move the session.")

# the moves being handed to the workspace in this process now (the app runs one): a move left starting by a process
# that stopped is put back to uploaded by the sweep
_handing = set()
_handing_lock = threading.Lock()
# the files of moves being sent to this process now, by (seq, file): one upload of each at a time
_uploading = set()
_uploading_lock = threading.Lock()
# the names of each account's workspace repositories as this process last gave the page their list (GET
# /api/ade/workspace/repos), by account, case folded: what a move's repository is held to before it is held
# (check_repository); forgotten when the list changes through this process, so a list not known since holds nothing
_repos_seen = {}
_repos_seen_lock = threading.Lock()


def _now(now):
    return int(now if now is not None else time.time())


def state_of(row, now):
    """A move's state as its row keeps it, but an asked move past its life is expired at once, before the sweep writes
    it so."""
    if row["state"] == "asked" and row["expires_at"] <= now:
        return "expired"
    return row["state"]


# ---- the files ----------------------------------------------------------------------------------------------------

def _root():
    path = os.path.join(config.data_dir(), "moves")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def folder(seq):
    """Where a move's files are kept: moves/<seq>/ under the data directory."""
    return os.path.join(_root(), str(int(seq)))


def _incoming():
    path = os.path.join(_root(), "incoming")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def delete_files(seqs):
    """Delete the files kept of these moves."""
    for seq in seqs:
        shutil.rmtree(folder(seq), ignore_errors=True)


# ---- the owner's command (link_commands.py) and what changes it ----------------------------------------------------

def check_workspace(user_id):
    """A move goes into the account's hosted workspace: refused (409, with the workspace's own words) when the account
    has none on this deployment, before anything is stored."""
    user = auth.user(user_id)
    try:
        if user is None:
            raise workspaces.WorkspaceAccess(404, "No such account.")
        workspaces.for_user(user)
    except workspaces.WorkspaceAccess as e:
        raise link.LinkError(409, f"A move goes into your hosted workspace: {e}") from None


def check_life(expires_at, now):
    """A move waits on its hold before its link may take it, then must live HOLD_MARGIN seconds more: refused (422,
    with the numbers) when the life it has left as it is posted is shorter than those two together."""
    wait, left = holds.delay(HOLD), expires_at - now
    if left < wait + HOLD_MARGIN:
        raise link.LinkError(422, f"This move has {left} seconds of its life left. A move waits {wait} seconds before "
                                  f"its link may take it, then needs {HOLD_MARGIN} seconds more: {wait + HOLD_MARGIN} "
                                  "in all.")


def _busy(db, user_id, session, now, held=True):
    """Whether a move of the account's Claude Code session `session` is under way, by any link (stored and not ended:
    asked and in its life, taken, uploaded or starting), or, with `held`, waits on a hold that has not ended."""
    if db.execute("SELECT 1 FROM ade_link_moves WHERE user_id = ? AND session_id = ? AND (state IN ('taken', "
                  "'uploaded', 'starting') OR (state = 'asked' AND expires_at > ?)) LIMIT 1",
                  (user_id, session, now)).fetchone():
        return True
    return held and db.execute(
        "SELECT 1 FROM ade_link_held_moves hm JOIN ade_holds ho ON ho.id = hm.hold_id WHERE hm.user_id = ? AND "
        "hm.session_id = ? AND hm.seq IS NULL AND ho.done_at IS NULL AND ho.cancelled_at IS NULL LIMIT 1",
        (user_id, session)).fetchone() is not None


def check_free(db, user_id, session, now):
    """In post_command's transaction: one move of a session at a time, so a session never goes twice; refused (409)
    while one is held or under way. And, before anything is held, the account's work lives in the workspace this
    deployment runs, as it chose (storage.py; refused, 403, in the words a start is refused with), and the session's
    repository is the workspace's (check_repository)."""
    if _busy(db, user_id, session, now):
        raise link.LinkError(409, BUSY)
    from . import storage
    user = auth.user(user_id)
    refused = storage.refusal(user, db) if user is not None else None
    if refused is not None:
        raise link.LinkError(403, refused)
    check_repository(db, user_id, session)


def note_repos(user_id, answer):
    """The workspace's repositories as the page just read them through this process (workspace_routes.repos): their
    names kept for check_repository, but one whose folder is gone."""
    repos = answer.get("repos") if isinstance(answer, dict) else None
    if not isinstance(repos, list):
        return
    names = frozenset(r["name"].casefold() for r in repos
                      if isinstance(r, dict) and isinstance(r.get("name"), str) and not r.get("missing"))
    with _repos_seen_lock:
        _repos_seen[user_id] = names


def forget_repos(user_id):
    """The workspace's list changes through this process (a clone, a folder added or taken out): what was noted of it
    is not known any more, until the page reads it again."""
    with _repos_seen_lock:
        _repos_seen.pop(user_id, None)


def repository_of(project, repositories):
    """A machine session's repository as the page puts it with the workspace's (the Sidebar's machineRepo): the folder
    of the repository it worked in first, else its own folder, as (its name, the folder as its machine wrote it); None
    where it names none (no folder, or the home folder)."""
    folder = ((repositories or [None])[0] or project or "").rstrip("/")
    name = folder.rsplit("/", 1)[-1]
    return (name, folder) if name and name != "~" else None


def check_repository(db, user_id, session):
    """A move's session's repository, as its machine's History list names it (repository_of), is one of the
    workspace's by name, as the page last read the workspace's list: refused (409, naming it) otherwise, before the
    move is held, so it does not wait on its hold and its machine's upload only to fail there. Where that list is not
    known (not read since the app started, or changed since), or the session's row is sealed or names no repository,
    nothing is held to it here: the workspace finds the repository by its origin as the move starts, and says so."""
    with _repos_seen_lock:
        names = _repos_seen.get(user_id)
    if names is None:
        return
    row = db.execute("SELECT project, repositories, sealed FROM ade_link_history WHERE user_id = ? AND session_id = ? "
                     "AND withheld = 0 ORDER BY listed_at DESC LIMIT 1", (user_id, session)).fetchone()
    if row is None or row["sealed"]:
        return
    found = repository_of(row["project"], json.loads(row["repositories"]) if row["repositories"] else None)
    if found is not None and found[0].casefold() not in names:
        raise link.LinkError(409, NOT_IN_WORKSPACE.format(name=found[0], folder=found[1]))


def held_since(db, user_id, since):
    """How many of the account's moves signed after `since` are not stored, whatever became of them: held, cancelled or
    failed. Each counts in the owner's commands an hour (link_commands.post_command), as a command withdrawn still
    does, so cancelling gives nothing back; a move stored counts as its event."""
    return db.execute("SELECT COUNT(*) FROM ade_link_held_moves WHERE user_id = ? AND seq IS NULL AND created_at > ?",
                      (user_id, since)).fetchone()[0]


def held_open(db, user_id, link_id, now):
    """How many of the account's moves to `link_id` wait on a hold that has not ended, with life left past `now`. Each
    is stored when its hold ends, so its link's commands not taken count it already (link_commands.post_command); one
    cancelled, or failed, frees its place."""
    return db.execute(
        "SELECT COUNT(*) FROM ade_link_held_moves hm JOIN ade_holds ho ON ho.id = hm.hold_id WHERE hm.user_id = ? AND "
        "hm.seq IS NULL AND ho.done_at IS NULL AND ho.cancelled_at IS NULL AND hm.link_id = ? AND hm.expires_at > ?",
        (user_id, link_id, now)).fetchone()[0]


def hold(db, user_id, target, title, payload, payload_text, signature, passkey_id, now):
    """In post_command's transaction, once every check of a move has held and its nonce is used: its hold
    (holds.place), which takes effect delay(HOLD) seconds after `now`, and its row of ade_link_held_moves, which keeps
    what makes its event when the hold ends (_store). `target` is its link's row, `title` the session's title as that
    link's list gives it. 409 for the id of a command stored already or of a move held already. The hold."""
    if db.execute("SELECT 1 FROM ade_link_events WHERE user_id = ? AND link_id = ? AND client_id = ?",
                  (user_id, link.OWNER, payload["id"])).fetchone():
        raise link.LinkError(409, "A command with this id is stored already.")
    named = (title or "").strip() or "(untitled)"
    placed = holds.place(db, user_id, HOLD, payload["id"],
                         f"Moving “{named}” from {target['name']} into the workspace was approved", now)
    try:
        db.execute("INSERT INTO ade_link_held_moves (hold_id, user_id, link_id, session_id, title, client_id, payload, "
                   "signature, passkey_id, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (placed["id"], user_id, target["id"], payload["session"], title, payload["id"], payload_text,
                    signature, passkey_id, payload["expires_at"], now))
    except sqlite3.IntegrityError:
        raise link.LinkError(409, "A move with this id is held already.") from None
    return placed


def record(db, seq, user_id, link_id, session, title, now, expires_at):
    """A move just stored (as its hold ends, _store): its row, asked. `title` is the session's title as its link's
    list gave it, the workspace session's name."""
    db.execute("INSERT INTO ade_link_moves (seq, user_id, link_id, session_id, title, state, created_at, expires_at, "
               "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (seq, user_id, link_id, session, title, "asked", now, expires_at, now))


def taken(db, seq, now):
    """In a take's transaction (link_commands.take): the move is taken."""
    db.execute("UPDATE ade_link_moves SET state = 'taken', taken_at = ?, updated_at = ? WHERE seq = ? AND "
               "state = 'asked'", (now, now, seq))


def sent(db, seq):
    """Whether a move is past its link's part: sent, said not to be, or ended otherwise (link_commands.take gives a
    take whose answer was lost again only before)."""
    row = db.execute("SELECT state FROM ade_link_moves WHERE seq = ?", (seq,)).fetchone()
    return row is not None and row["state"] not in ("asked", "taken")


def _end_held(db, user_id, column, value, reason, now):
    """Each of the account's moves whose `column` (passkey_id or link_id, never another) is `value` and that waits on
    its hold: the hold ends now, failed, `reason` its why, so the move is never stored."""
    if column not in ("passkey_id", "link_id"):     # it names a column: only these two are ever one
        raise ValueError(f"a held move is not ended by its {column!r}")
    ids = [r["hold_id"] for r in db.execute(
        f"SELECT hm.hold_id FROM ade_link_held_moves hm JOIN ade_holds ho ON ho.id = hm.hold_id WHERE hm.user_id = ? "
        f"AND hm.{column} = ? AND hm.seq IS NULL AND ho.done_at IS NULL AND ho.cancelled_at IS NULL",
        (user_id, value)).fetchall()]
    holds.end(db, ids, reason, now)


def withdrawn_signed(db, user_id, now, which, args, reason=WITHDRAWN):
    """In a change's own transaction (passkeys._withdraw_signed: a passkey's removal, high security turned on), after
    the commands the account's passkeys matching `which` (SQL after "passkey_id", with `args`) signed that no session
    took are withdrawn: the moves among them are failed, `reason` saying why (WITHDRAWN for a passkey removed,
    HIGH_SECURITY for high security turned on); and each move such a passkey signed that waits on its hold ends with
    the same words, never stored. Returns how many held moves it ended."""
    db.execute("UPDATE ade_link_moves SET state = 'failed', reason = ?, updated_at = ? WHERE user_id = ? AND "
               "state = 'asked' AND seq IN (SELECT seq FROM ade_link_events WHERE user_id = ? AND passkey_id "
               f"{which} AND kind = 'move' AND withdrawn_at = ?)", (reason, now, user_id, user_id, *args, now))
    ids = [r["hold_id"] for r in db.execute(
        "SELECT hm.hold_id FROM ade_link_held_moves hm JOIN ade_holds ho ON ho.id = hm.hold_id WHERE hm.user_id = ? "
        f"AND hm.passkey_id {which} AND hm.seq IS NULL AND ho.done_at IS NULL AND ho.cancelled_at IS NULL",
        (user_id, *args)).fetchall()]
    holds.end(db, ids, reason, now)
    return len(ids)


def withdrawn(db, user_id, passkey_id, now):
    """The same, for one passkey removed."""
    return withdrawn_signed(db, user_id, now, "= ?", (passkey_id,))


def owner_withdrew(db, seq, now):
    """In the owner's withdrawal of a move its link has not taken (link_commands.withdraw): the move is failed, saying
    so."""
    db.execute("UPDATE ade_link_moves SET state = 'failed', reason = ?, updated_at = ? WHERE seq = ? AND "
               "state = 'asked'", (OWNER_WITHDREW, now, seq))


def end_for_link(db, user_id, link_id, now):
    """In a link's revoke (link.revoke): its moves not yet in the workspace are failed, saying why (one being handed
    over finishes), and each of its moves that waits on its hold ends with the same words, never stored; the seqs
    whose files go."""
    rows = db.execute("SELECT seq FROM ade_link_moves WHERE user_id = ? AND link_id = ? AND state IN "
                      "('asked', 'taken', 'uploaded')", (user_id, link_id)).fetchall()
    db.execute("UPDATE ade_link_moves SET state = 'failed', reason = ?, updated_at = ? WHERE user_id = ? AND "
               "link_id = ? AND state IN ('asked', 'taken', 'uploaded')", (REVOKED, now, user_id, link_id))
    _end_held(db, user_id, "link_id", link_id, REVOKED, now)
    return [r["seq"] for r in rows]


# ---- what the link that took a move sends ------------------------------------------------------------------------

def _sending(db, link_row, seq, now):
    """The move `seq` when this link took it and has not finished it: its row; LinkError otherwise (404 for no move of
    this link's, 409 with its state for one it does not send now)."""
    if not 0 < seq <= link.SEQ_MAX:
        raise link.LinkError(404, "No move to this link has that seq.")
    row = db.execute("SELECT m.*, e.taken_by FROM ade_link_moves m JOIN ade_link_events e ON e.seq = m.seq WHERE "
                     "m.seq = ? AND m.user_id = ?", (seq, link_row["user_id"])).fetchone()
    if row is None or row["link_id"] != link_row["id"]:
        raise link.LinkError(404, "No move to this link has that seq.")
    state = state_of(row, now)
    if state != "taken" or row["taken_by"] != link_row["id"]:
        raise link.LinkError(409, f"A move's files and its end come from its link while it is taken; this one is "
                                  f"{state}.", state=state)
    return row


def _unlisted(db, row):
    """Why the move's session may not go now, by its link's History list as the server keeps it (its rows decide, as for
    a mirrored note): WITHHELD when the list has it withheld; UNLISTED when the list does not have it at all, withheld
    since and past the newest the link lists, or the list withdrawn (its machine's history.json gone); None when the
    list has it and has not withheld it."""
    got = db.execute("SELECT withheld FROM ade_link_history WHERE link_id = ? AND session_id = ?",
                     (row["link_id"], row["session_id"])).fetchone()
    if got is None:
        return UNLISTED
    return WITHHELD if got["withheld"] else None


def _fail(db, seq, reason, now):
    db.execute("UPDATE ade_link_moves SET state = 'failed', reason = ?, updated_at = ? WHERE seq = ?",
               (reason, now, seq))


async def receive(request, seq, what):
    """PUT .../transcript or .../bundle from the link that took the move: the body streamed to a temporary file under
    the data directory, at most FILE_MAX bytes (413 past them), and put in the move's place whole, over one sent before;
    refused, nothing is kept. The request is authenticated whole before a byte of its body is read: it gives the body's
    sha256 in X-Body-SHA256, and the signature every link request carries, which covers that digest, must check with a
    live link's key at the server's time as the request began. Anything less is refused as any unsigned request is
    (401), before anything about the move is looked at or said. Then the link must have taken the move and not finished
    it (404, or 409 with the move's state), a length it gives must be within FILE_MAX, and no other upload of this file
    of the move may be under way (409). The body must come whole within UPLOAD_WAIT seconds of the request's start (408
    past them), and be the one signed: its sha256 the one given (401 otherwise). {seq, <what>: its size in bytes}."""
    if what not in FILES:                   # it names a column (_keep): only the two files are ever one
        raise ValueError(f"a move has no file {what!r}")
    began = int(time.time())
    if not 0 < seq <= link.SEQ_MAX:
        raise link.LinkError(404, "No move to this link has that seq.")
    raw = request.scope.get("raw_path")
    raw_path = raw.decode("latin-1") if raw else request.url.path
    raw_query = request.scope.get("query_string", b"").decode("latin-1")
    claimed = request.headers.get("x-body-sha256", "")
    if not _BODY_SHA.fullmatch(claimed):
        raise link.LinkError(401, UNSIGNED_FILE)
    me = await run_in_threadpool(link.authenticate, request.headers.get("authorization"), request.method, raw_path,
                                 raw_query, b"", began, claimed)
    stated = request.headers.get("content-length", "")
    if stated.isdigit() and int(stated) > FILE_MAX:
        raise link.LinkError(413, f"A move's {what} is at most {FILE_MAX // (1024 * 1024)} MiB.")
    await run_in_threadpool(_may_send, me, seq, began)
    key = (seq, what)
    with _uploading_lock:
        if key in _uploading:
            raise link.LinkError(409, UPLOADING)
        _uploading.add(key)
    try:
        return await _take_in(request, me, seq, what, claimed, began)
    finally:
        with _uploading_lock:
            _uploading.discard(key)


def _may_send(link_row, seq, now):
    """Before a file's body is read, its request authenticated: the move is this link's, taken by it and not finished
    (_sending). _keep checks the move again as it keeps the file."""
    with store.connect() as db:
        _sending(db, link_row, seq, now)


async def _take_in(request, me, seq, what, claimed, began):
    """The body of receive's request, from the link `me`, streamed to a temporary file and kept (_keep) once its sha256
    is found to be the one its signature covers, `claimed`."""
    fd, tmp = tempfile.mkstemp(prefix=f"{what}-", dir=_incoming())
    try:
        digest, size = hashlib.sha256(), 0
        chunks = request.stream().__aiter__()
        with os.fdopen(fd, "wb") as fh:
            while True:
                try:
                    chunk = await asyncio.wait_for(chunks.__anext__(), max(began + UPLOAD_WAIT - time.time(), 0))
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise link.LinkError(408, f"A move's {what} must come whole within {UPLOAD_WAIT // 60} minutes "
                                              "of its request's start.") from None
                size += len(chunk)
                if size > FILE_MAX:
                    raise link.LinkError(413, f"A move's {what} is at most {FILE_MAX // (1024 * 1024)} MiB.")
                digest.update(chunk)
                fh.write(chunk)
            fh.flush()
            await run_in_threadpool(os.fsync, fh.fileno())
        if digest.hexdigest() != claimed:
            raise link.LinkError(401, NOT_THE_BODY)
        return await run_in_threadpool(_keep, me, seq, what, tmp, size)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass                            # put in place


def _keep(link_row, seq, what, tmp, size, now=None):
    """A file received whole, put in its move's place, its size recorded, in one transaction with what allows it: the
    move is this link's, taken by it and not finished, and its link's list has its session now, not withheld (else the
    move is failed, saying why, and what it sent deleted)."""
    now = _now(now)
    refusal = None
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _sending(db, link_row, seq, now)
        why = _unlisted(db, row)
        if why is not None:
            _fail(db, seq, why, now)
            refusal = link.LinkError(409, why, state="failed")
        else:
            place = folder(seq)
            os.makedirs(place, mode=0o700, exist_ok=True)
            os.replace(tmp, os.path.join(place, what))
            db.execute(f"UPDATE ade_link_moves SET {what}_size = ?, {what}_at = ?, updated_at = ? WHERE seq = ?",
                       (size, now, now, seq))
    if refusal is not None:
        delete_files([seq])
        raise refusal
    return {"seq": seq, what: size}


def _line(value, what, most):
    """One line of text a link sends, held to what everything it writes is: at most `most` characters, every one shown
    as itself, nothing shaped like a secret."""
    return link._shown(link._text(value, what, most), what)


def _remote(value):
    """The origin's address as a link sends it (ade_link.py, _remote_shown), stripped and held to what the workspace
    matches a repository by, its host and path alone: refused (422) when it names a user or a password (in an address
    that starts with a scheme, anything before an @ in its host; in git's own form user@host:path, a user other than
    git, whatever its path holds, a :// among it too; in any other form but a path, an @) or holds a query or a
    fragment. A path on the machine is taken as it is: it may hold an @, ? or #."""
    remote = _line(value, "remote", REMOTE_MAX).strip()
    if remote.startswith("/"):
        return remote
    if _SCHEME.match(remote):
        try:
            parts = urllib.parse.urlsplit(remote)
        except ValueError:                  # a host urlsplit cannot read (an IPv6 address left open, say)
            raise link.LinkError(422, UNREAD_REMOTE) from None
        user, extra = "@" in parts.netloc, bool(parts.query or parts.fragment)
    else:
        scp = _SCP.fullmatch(remote)
        user = scp.group(1) != "git" if scp is not None else "@" in remote
        extra = "?" in remote or "#" in remote
    if user:
        raise link.LinkError(422, USER_IN_REMOTE)
    if extra:
        raise link.LinkError(422, QUERY_IN_REMOTE)
    return remote


def _done_fields(data):
    if not isinstance(data, dict) or set(data) != DONE_FIELDS:
        raise link.LinkError(422, "A move's done is {session, remote, branch, head, changes, cwd, bundle}.")
    if not isinstance(data["session"], str) or not link._SESSION_ID.fullmatch(data["session"]):
        raise link.LinkError(422, "A move's done names its session by its id.")
    remote = _remote(data["remote"])
    branch = None if data["branch"] is None else _line(data["branch"], "branch", BRANCH_MAX)
    if not isinstance(data["head"], str) or not _SHA.fullmatch(data["head"]):
        raise link.LinkError(422, "A move's head is a commit's id: 40 lowercase hex characters.")
    if data["changes"] is not None and (not isinstance(data["changes"], str) or not _SHA.fullmatch(data["changes"])):
        raise link.LinkError(422, "A move's changes is a commit's id (40 lowercase hex characters), or null.")
    cwd = _line(data["cwd"], "folder", CWD_MAX)
    if not isinstance(data["bundle"], bool):
        raise link.LinkError(422, "A move's bundle is true or false.")
    return {"session": data["session"], "remote": remote, "branch": branch, "head": data["head"],
            "changes": data["changes"], "cwd": cwd, "bundle": data["bundle"]}


def done(link_row, seq, data, now=None):
    """POST .../done from the link that took the move: {session, remote, branch, head, changes, cwd, bundle}, each held
    to its form, the session the one moved. The transcript must be in, and the bundle where it says there is one (one
    sent when it says there is none is deleted), and its link's list must have its session now, not withheld (else the
    move is failed, saying why, and what was sent deleted). The move is then uploaded, and handed to the workspace when
    that runs (in a thread of its own). {seq, state}."""
    now = _now(now)
    fields = _done_fields(data)
    refusal = None
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _sending(db, link_row, seq, now)
        if fields["session"] != row["session_id"]:
            raise link.LinkError(422, "A move's done names the session it moves, not another.")
        why = _unlisted(db, row)
        if why is not None:
            _fail(db, seq, why, now)
            refusal = link.LinkError(409, why, state="failed")
        else:
            have = {w: row[f"{w}_size"] is not None and os.path.isfile(os.path.join(folder(seq), w)) for w in FILES}
            if not have["transcript"]:
                raise link.LinkError(409, "The transcript has not been sent: PUT it first.")
            if fields["bundle"] and not have["bundle"]:
                raise link.LinkError(409, "The bundle has not been sent: PUT it first, or say there is none.")
            kept = {k: v for k, v in fields.items() if k != "session"}
            db.execute("UPDATE ade_link_moves SET state = 'uploaded', done = ?, uploaded_at = ?, reason = NULL, "
                       "updated_at = ?, bundle_size = CASE WHEN ? THEN bundle_size END, bundle_at = CASE WHEN ? THEN "
                       "bundle_at END WHERE seq = ?",
                       (json.dumps(kept), now, now, int(fields["bundle"]), int(fields["bundle"]), seq))
    if refusal is not None:
        delete_files([seq])
        raise refusal
    if not fields["bundle"]:
        try:
            os.unlink(os.path.join(folder(seq), "bundle"))
        except FileNotFoundError:
            pass
    _spawn(hand_off, link_row["user_id"], seq)
    return {"seq": seq, "state": "uploaded"}


def failed(link_row, seq, data, now=None):
    """POST .../failed from the link that took the move: {reason}, why it could not send it, one line of at most
    REASON_MAX characters. The move is failed and what was sent of it deleted. {seq, state}."""
    now = _now(now)
    if not isinstance(data, dict) or set(data) != {"reason"}:
        raise link.LinkError(422, "A move's failure is {reason}.")
    reason = _line(data["reason"], "reason", REASON_MAX)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _sending(db, link_row, seq, now)
        _fail(db, seq, reason, now)
    delete_files([seq])
    return {"seq": seq, "state": "failed"}


# ---- the owner's side ---------------------------------------------------------------------------------------------

def view(row, now):
    """A move as the page shows it: where it stands and why, and what its link said of its repository."""
    state = state_of(row, now)
    kept = json.loads(row["done"]) if row["done"] else {}
    reason = row["reason"] if row["reason"] or state != "expired" else NOT_TAKEN
    return {"seq": row["seq"], "link_id": row["link_id"], "session": row["session_id"], "state": state,
            "created_at": row["created_at"], "expires_at": row["expires_at"], "taken_at": row["taken_at"],
            "uploaded_at": row["uploaded_at"], "updated_at": row["updated_at"],
            "workspace_session": row["workspace_session"], "reason": reason, "branch": kept.get("branch"),
            "head": kept.get("head"), "changes": kept.get("changes"), "bundle": kept.get("bundle")}


def held_view(row):
    """A move not stored, from its held row and its hold, as the page shows it: held, waiting on its hold; cancelled,
    as the owner did while it was held; or failed as its hold ended, with why. Its seq is null: nothing of it was
    stored, so its link has nothing of it."""
    state = "cancelled" if row["cancelled_at"] is not None else "failed" if row["done_at"] is not None else "held"
    return {"seq": None, "hold_id": row["hold_id"], "link_id": row["link_id"], "session": row["session_id"],
            "state": state, "takes_effect_at": row["takes_effect_at"], "told": row["told"],
            "created_at": row["created_at"], "expires_at": row["expires_at"], "reason": row["failed"]}


def _stored_rows(db, where, args):
    """for_owner's stored moves, `where` naming the moves' table mv. A move stored from a held row was signed when that
    row was made; among moves signed in one second, the held row made later (or, before holds, the higher seq) is the
    newer."""
    return db.execute(
        "SELECT mv.*, COALESCE(hm.created_at, mv.created_at) AS signed_at, COALESCE(hm.rowid, 0) AS nth FROM "
        "ade_link_moves mv LEFT JOIN ade_link_held_moves hm ON hm.seq = mv.seq AND hm.user_id = mv.user_id WHERE "
        f"{' AND '.join(where)} ORDER BY signed_at DESC, nth DESC, mv.seq DESC LIMIT ?", (*args, LISTED)).fetchall()


def _held_rows(db, where, args):
    """for_owner's moves not stored, `where` naming the held moves' table hm."""
    return db.execute(
        "SELECT hm.*, hm.created_at AS signed_at, hm.rowid AS nth, ho.takes_effect_at, ho.told, ho.cancelled_at, "
        "ho.done_at, ho.failed FROM ade_link_held_moves hm JOIN ade_holds ho ON ho.id = hm.hold_id WHERE "
        f"{' AND '.join(where)} AND hm.seq IS NULL ORDER BY signed_at DESC, nth DESC LIMIT ?",
        (*args, LISTED)).fetchall()


def for_owner(user_id, link_id=None, session=None, now=None):
    """The account's moves, at most LISTED, newest first by when the owner signed them: those to one link, of one
    session, as asked. The account's holds whose time has come are settled first, so a move whose hold has ended is
    stored by then, or failed. A move not stored is given from its held row (held_view); a stored one from its own
    row, which stands for its held row. The two are read as one, so a hold that ends as they are read leaves its move
    in one of them. {moves}."""
    now = _now(now)
    holds.settle(user_id, now)
    stored_where, held_where, args = ["mv.user_id = ?"], ["hm.user_id = ?"], [user_id]
    for column, value in (("link_id", link_id), ("session_id", session)):
        if value is not None:
            stored_where.append(f"mv.{column} = ?")
            held_where.append(f"hm.{column} = ?")
            args.append(value)
    with store.connect() as db:
        db.execute("BEGIN")                 # one read transaction: what it sees stays as it was until both are read
        stored = _stored_rows(db, stored_where, args)
        held = _held_rows(db, held_where, args)
    found = [((r["signed_at"], r["nth"], r["seq"]), view(r, now)) for r in stored] + \
        [((r["signed_at"], r["nth"], 0), held_view(r)) for r in held]
    found.sort(key=lambda f: f[0], reverse=True)
    return {"moves": [v for _, v in found[:LISTED]]}


def _one(user_id, seq, now=None):
    now = _now(now)
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_link_moves WHERE seq = ? AND user_id = ?", (seq, user_id)).fetchone()
    if row is None:
        raise link.LinkError(404, "No such move.")
    return view(row, now)


def carry_on(user, seq, now=None):
    """The owner's word to start an uploaded move in the workspace (POST /api/ade/link/moves/{seq}/start): the
    workspace is started first when it is not running (as the page's Start starts it, joining a start under way),
    then the move is handed to it, in a thread of its own; this waits up to CARRY_ON_WAIT seconds for that, then gives
    the move as it stands (the page reads it again until it is started or failed). 404 for a move not the account's,
    409 for one that is not uploaded; one being handed over already is given as it stands."""
    now = _now(now)
    if not 0 < seq <= link.SEQ_MAX:
        raise link.LinkError(404, "No such move.")
    state = _one(user["id"], seq, now)["state"]
    if state not in ("uploaded", "starting"):
        raise link.LinkError(409, f"Only an uploaded move is started in the workspace; this one is {state}.",
                             state=state)
    if state == "uploaded":
        workspaces.for_user(user)       # off here, or not this account's: its own refusal
        worker = _spawn(_carry_on, user, seq)
        if worker is not None:
            worker.join(CARRY_ON_WAIT)
    return _one(user["id"], seq)


def _carry_on(user, seq):
    from . import workspace_routes
    p = workspaces.for_user(user)
    if workspace_routes._starting(user["id"]) or p.endpoint(user) is None:
        job = workspace_routes.start_job(p, user)
        job["thread"].join()
        if job["error"] is not None:
            _note(user["id"], seq, str(job["error"]))
            return
    hand_off(user["id"], seq)


def _note(user_id, seq, reason, now=None):
    """Why an uploaded move was not handed over just now, kept on it for the page; it stays uploaded."""
    now = _now(now)
    with store.connect() as db:
        db.execute("UPDATE ade_link_moves SET reason = ?, updated_at = ? WHERE seq = ? AND user_id = ? AND "
                   "state = 'uploaded'", (reason, now, seq, user_id))
    return "uploaded"


# ---- handing it to the workspace ----------------------------------------------------------------------------------

def hand_off(user_id, seq, now=None):
    """Hand an uploaded move to the account's workspace, when it runs: its transcript (PUT /moves/{seq}/transcript),
    its bundle when there is one (PUT /moves/{seq}/bundle), then its session (POST /sessions, the usual body with
    "program": "claude" and "moved"), each with the workspace's secret. Started, with the session's id from the answer,
    or failed, with the workspace's refusal in its own words; its files go either way. A workspace that is not running
    or does not answer, that answers with an error of its own (a 5xx, anything but the agent's own refusals), or whose
    Claude Code is not signed in, leaves it uploaded, saying why, and so does a hand-off whose calls to the workspace
    take HAND_OFF_WAIT seconds in all. The move is named as this process's to hand over before its state says starting,
    so a sweep in between leaves it be; one this process hands over already is left to that. Its state after."""
    from . import workspace_routes
    now = _now(now)
    user = auth.user(user_id)
    if user is None:
        return None
    try:
        endpoint = workspace_routes._running(user)
    except workspaces.WorkspaceAccess as e:
        return _note(user_id, seq, str(e))
    except HTTPException:
        return _note(user_id, seq, NOT_RUNNING)
    with _handing_lock:
        if seq in _handing:
            return "starting"
        _handing.add(seq)
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT m.*, l.name AS link_name FROM ade_link_moves m LEFT JOIN ade_links l ON "
                             "l.id = m.link_id WHERE m.seq = ? AND m.user_id = ?", (seq, user_id)).fetchone()
            if row is None or row["state"] != "uploaded":
                return state_of(row, now) if row is not None else None
            kept = json.loads(row["done"])
            end = _unlisted(db, row)
            if end is None and not all(os.path.isfile(os.path.join(folder(seq), w)) for w in ("transcript",) + (
                    ("bundle",) if kept["bundle"] else ())):
                end = GONE
            if end is not None:
                _fail(db, seq, end, now)
            else:
                db.execute("UPDATE ade_link_moves SET state = 'starting', reason = NULL, updated_at = ? WHERE "
                           "seq = ?", (now, seq))
        if end is not None:
            delete_files([seq])
            return "failed"
        try:
            return _send(user, endpoint, row, kept)
        except Exception:  # noqa: BLE001  the move is put back for the owner to carry on; the log says what went wrong
            traceback.print_exc()
            return _back(seq, ERROR)
    finally:
        with _handing_lock:
            _handing.discard(seq)


class _Late(Exception):
    """A hand-off's time in all ran out before its next call to the workspace."""


def _within(deadline, most):
    """A call's time out: `most` seconds, or what is left before `deadline` when that is less; _Late when none is."""
    left = deadline - time.time()
    if left <= 0:
        raise _Late()
    return min(most, left)


def _send(user, endpoint, row, kept):
    from . import workspace_routes
    seq = row["seq"]
    place = folder(seq)
    deadline = time.time() + HAND_OFF_WAIT          # what its calls to the workspace may take in all
    try:
        workspace_routes._asks_as_chosen(endpoint, user)     # a token goes to a program only as the person chose
        account = workspace_routes._claude_ready(endpoint)   # and its claude starts signed in, on the account checked
    except HTTPException as e:
        return _back(seq, str(e.detail))
    except workspace_client.NotAnswering:
        return _back(seq, NOT_ANSWERING)
    except workspace_client.WorkspaceCallError as e:
        return _back(seq, str(e)[:WORDS_MAX])
    body = workspace_routes.SessionIn(program="claude", task=_task(row), account=account).model_dump()
    body.pop("custom", None)
    body["moved"] = {"id": str(seq), "session": row["session_id"], "remote": kept["remote"], "branch": kept["branch"],
                     "head": kept["head"], "changes": kept["changes"], "cwd": kept["cwd"], "bundle": kept["bundle"]}
    try:
        workspace_client.put_file(endpoint, f"/moves/{seq}/transcript", os.path.join(place, "transcript"),
                                  timeout=_within(deadline, PUT_WAIT))
        if kept["bundle"]:
            workspace_client.put_file(endpoint, f"/moves/{seq}/bundle", os.path.join(place, "bundle"),
                                      timeout=_within(deadline, PUT_WAIT))
        made = workspace_client.call(endpoint, "POST", "/sessions", body, timeout=_within(deadline, START_WAIT))
    except _Late:
        return _back(seq, TOO_LONG)
    except workspace_client.NotAnswering:
        return _back(seq, NOT_ANSWERING)
    except workspace_client.WorkspaceCallError as e:
        if e.status in workspace_client.REFUSALS:       # the agent's own refusal, in its words: it would refuse again
            return _end(seq, "failed", str(e)[:WORDS_MAX])
        return _back(seq, f"{str(e)[:WORDS_MAX]} {AGAIN}")      # an error of the workspace's: it may get through later
    sid = made.get("id") if isinstance(made, dict) else None
    if not isinstance(sid, str) or not workspace_routes._SESSION.match(sid):
        return _end(seq, "failed", NO_SESSION)
    workspace_routes._account_recorded(user["id"], sid, made.get("account") or "default")
    return _end(seq, "started", None, sid)


def _task(row):
    """The workspace session's task, which names it there: the session's title, and where it came from."""
    title = (row["title"] or "").strip() or "(untitled)"
    where = row["link_name"] or "a linked machine"
    return f"{title}\n\nMoved here from {where}, where it was the Claude Code session {row['session_id']}."


def _back(seq, reason, now=None):
    """A hand-off that did not get through: the move is uploaded again, saying why, for the owner to carry it on."""
    now = _now(now)
    with store.connect() as db:
        db.execute("UPDATE ade_link_moves SET state = 'uploaded', reason = ?, updated_at = ? WHERE seq = ? AND "
                   "state = 'starting'", (reason, now, seq))
    return "uploaded"


def _end(seq, state, reason, session=None, now=None):
    """A hand-off's end, started (with the workspace's session) or failed (with the workspace's words); its files go."""
    now = _now(now)
    with store.connect() as db:
        ended = db.execute("UPDATE ade_link_moves SET state = ?, reason = ?, workspace_session = ?, updated_at = ? "
                           "WHERE seq = ? AND state = 'starting'", (state, reason, session, now, seq)).rowcount
    if ended:
        delete_files([seq])
    return state


def _spawn(fn, *args):
    """Run `fn` in a thread of its own, its failure logged (a hand-off can take minutes: two files of up to 32 MiB,
    then the session's start); the thread, for a caller that waits a while for it."""
    def run():
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001  logged; the move says where it stands
            print(f"moves: {fn.__name__} failed: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc()
    worker = threading.Thread(target=run, name="ade-move", daemon=True)
    worker.start()
    return worker


# ---- what time bounds ------------------------------------------------------------------------------------------------

def sweep(now=None):
    """What KEPT bounds, written on the rows and the files deleted: an asked move past its life is expired; one taken
    and not finished KEPT after its take, or uploaded and not taken by the workspace KEPT after its upload, is expired
    and its files go; one left starting by a process that stopped is uploaded again, to be carried on; the files of a
    move that is not under way, and what an upload cut short left in moves/incoming, are deleted."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE ade_link_moves SET state = 'expired', reason = ?, updated_at = ? WHERE state = 'asked' AND "
                   "expires_at <= ?", (NOT_TAKEN, now, now))
        for state, since, reason in (("taken", "taken_at", NOT_FINISHED), ("uploaded", "uploaded_at", NOT_STARTED)):
            db.execute(f"UPDATE ade_link_moves SET state = 'expired', reason = ?, updated_at = ? WHERE state = ? AND "
                       f"{since} <= ?", (reason, now, state, now - KEPT))
        with _handing_lock:
            handing = set(_handing)
        for r in db.execute("SELECT seq FROM ade_link_moves WHERE state = 'starting'").fetchall():
            if r["seq"] not in handing:
                db.execute("UPDATE ade_link_moves SET state = 'uploaded', reason = ?, updated_at = ? WHERE seq = ? AND "
                           "state = 'starting'", (CUT_SHORT, now, r["seq"]))
        # an ended move never moves again, so its files can go whenever they are found; a folder no row names is
        # left an hour, as an upload's temporary file is
        known, ended = set(), set()
        for r in db.execute("SELECT seq, state FROM ade_link_moves").fetchall():
            known.add(r["seq"])
            if r["state"] not in UNDER_WAY:
                ended.add(r["seq"])
    root = _root()
    for name in os.listdir(root):
        if not name.isdigit():
            continue
        try:
            if int(name) in ended or (int(name) not in known
                                      and os.path.getmtime(os.path.join(root, name)) <= now - INCOMING_KEPT):
                delete_files([int(name)])
        except FileNotFoundError:
            pass
    incoming = _incoming()
    for name in os.listdir(incoming):
        path = os.path.join(incoming, name)
        try:
            if os.path.getmtime(path) <= now - INCOMING_KEPT:
                os.unlink(path)
        except FileNotFoundError:
            pass


def start(every=SWEEP_EVERY):
    """Run sweep in a thread of this process, at once and then every `every` seconds, while it serves (the app's
    lifespan; the tests, which never enter it, call sweep themselves)."""
    def loop():
        while True:
            try:
                sweep()
            except Exception as e:  # noqa: BLE001  the sweep never takes the app down
                print(f"moves: a sweep failed: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(every)
    worker = threading.Thread(target=loop, name="ade-moves", daemon=True)
    worker.start()
    return worker


# ---- the hold a move waits on --------------------------------------------------------------------------------------

def _store(db, hold, now):
    """A move's hold has ended (holds.settle, in its transaction): the move is stored as its event to its link, as
    post_command stores a command, received now (link_commands.store_signed), with its row here, asked (record), and
    the event's seq kept on its held row. Checked again first: its link is live, that link's History list has the
    session unwithheld, no other move of the session is under way, the passkey that signed it is still the account's,
    LIFE_LEFT seconds of its life are left, its link's commands not taken leave room, the account still has a hosted
    workspace on this deployment (check_workspace), and its work still lives there, as it chose (storage.py: a choice
    changed in the hold refuses it, as check_free would have). One that fails a check is not stored: the hold keeps
    why, the workspace's refusal or the choice's in its own words."""
    row = db.execute("SELECT * FROM ade_link_held_moves WHERE hold_id = ? AND user_id = ?",
                     (hold["id"], hold["user_id"])).fetchone()
    if row is None or row["seq"] is not None:
        return "The move is no longer held."
    user_id = row["user_id"]
    target = db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                        (row["link_id"], user_id)).fetchone()
    if target is None:
        return REVOKED
    why = _unlisted(db, row)
    if why is not None:
        return why
    if _busy(db, user_id, row["session_id"], now, held=False):
        return BUSY_AT_END
    if db.execute("SELECT 1 FROM passkeys WHERE id = ? AND user_id = ?", (row["passkey_id"], user_id)).fetchone() \
            is None:
        return WITHDRAWN
    if row["expires_at"] - now < LIFE_LEFT:
        return (f"Under {LIFE_LEFT} seconds of its life were left when its hold ended: it ends at "
                f"{holds.utc(row['expires_at'])}.")
    if link_commands.queued(db, user_id, target["id"], now) >= link_commands.QUEUED_PER_LINK:
        return f"Its link holds {link_commands.QUEUED_PER_LINK} commands it has not taken, as many as it may."
    try:
        check_workspace(user_id)            # it reads through a connection of its own: this transaction's lock lets it
    except link.LinkError as e:
        return str(e)
    from . import storage
    user = auth.user(user_id)
    refused = storage.refusal(user, db) if user is not None else None
    if refused is not None:
        return refused
    try:
        seq = link_commands.store_signed(db, user_id, link_commands.MOVE, json.loads(row["payload"]), row["payload"],
                                         row["signature"], row["passkey_id"], target, now)
    except sqlite3.IntegrityError:
        return "A command with this id is stored already."
    record(db, seq, user_id, target["id"], row["session_id"], row["title"], now, row["expires_at"])
    db.execute("UPDATE ade_link_held_moves SET seq = ? WHERE hold_id = ?", (seq, row["hold_id"]))
    return None


def held_for_owner(user_id, now=None):
    """For Linked sessions: the account's moves that wait on their holds, and those whose holds ended in the last
    HELD_SHOWN seconds without their being stored (failed, with why), oldest first, each as held_view gives it with the
    session's title and its link's name. A cancelled one is not among them: the owner cancelled it."""
    now = _now(now)
    with store.connect() as db:
        rows = db.execute(
            "SELECT hm.*, ho.takes_effect_at, ho.told, ho.cancelled_at, ho.done_at, ho.failed, l.name AS link_name "
            "FROM ade_link_held_moves hm JOIN ade_holds ho ON ho.id = hm.hold_id LEFT JOIN ade_links l ON "
            "l.id = hm.link_id WHERE hm.user_id = ? AND hm.seq IS NULL AND ho.cancelled_at IS NULL AND "
            "(ho.done_at IS NULL OR ho.done_at > ?) ORDER BY hm.created_at, hm.rowid",
            (user_id, now - HELD_SHOWN)).fetchall()
    return [dict(held_view(r), title=r["title"], link=r["link_name"]) for r in rows]


holds.register(HOLD, _store, setting=HOLD_SETTING, default=HOLD_DEFAULT, notice="held-move")
