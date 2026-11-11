"""An agent's memory versions: each snapshot a run left when it changed the
agent's memory folder (the workspace's memory.py), copied into the app as the next version, with what changed from the
one before; and a rollback, which makes the folder a version's files again (the archive left as it is) and is itself
the next version, so nothing is lost by rolling back."""

import difflib
import json
import re
import time

from .. import store

MAX_FILES = 200
MAX_FILE = 64 * 1024
MAX_DIFF = 64 * 1024                     # of one version's changes, as the page shows them
KEEP_VERSIONS = 30                       # an agent's versions kept: its newest, each a whole copy of the folder
KEEP_DAYS = 90                           # and none older, but the newest, as the runs they came from
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class MemoryVersionError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def clean(given):
    """A snapshot from the workspace held to its shapes: ({path: text or None}, digest), or None."""
    if not isinstance(given, dict) or not (isinstance(given.get("digest"), str) and _DIGEST.match(given["digest"])):
        return None
    files = given.get("files") if isinstance(given.get("files"), dict) else {}
    out = {}
    for path, text in list(files.items())[:MAX_FILES]:
        if not isinstance(path, str) or not path or len(path) > 300 or path.startswith(("/", "..")) or \
                _CONTROL.search(path):
            continue
        out[path] = text if isinstance(text, str) and len(text.encode("utf-8")) <= MAX_FILE else None
    return out, given["digest"]


def _latest(db, agent_id):
    return db.execute("SELECT * FROM ade_agent_memory WHERE agent_id = ? ORDER BY version DESC LIMIT 1",
                      (agent_id,)).fetchone()


def add(db, user_id, agent_id, run_id, snapshot, kind="run", from_version=None, now=None):
    """The next version, unless the folder is as the latest version had it. Its number, or None."""
    got = clean(snapshot)
    if got is None:
        return None
    files, digest = got
    latest = _latest(db, agent_id)
    if latest is not None and latest["digest"] == digest:
        return None
    version = (latest["version"] if latest else 0) + 1
    db.execute("INSERT INTO ade_agent_memory (agent_id, user_id, version, run_id, kind, from_version, digest, files, "
               "made_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (agent_id, user_id, version, run_id, kind, from_version, digest, json.dumps(files),
                int(now if now is not None else time.time())))
    db.execute("DELETE FROM ade_agent_memory WHERE agent_id = ? AND version <= ?", (agent_id, version - KEEP_VERSIONS))
    return version


def forget(db, now):
    """Versions older than KEEP_DAYS go, but each agent's newest."""
    db.execute("DELETE FROM ade_agent_memory WHERE made_at < ? AND version < (SELECT MAX(version) FROM ade_agent_memory "
               "m WHERE m.agent_id = ade_agent_memory.agent_id)", (int(now) - KEEP_DAYS * 86400,))


def _row(db, user_id, agent_id, version):
    row = db.execute("SELECT * FROM ade_agent_memory WHERE agent_id = ? AND user_id = ? AND version = ?",
                     (agent_id, user_id, version)).fetchone()
    if row is None:
        raise MemoryVersionError(404, "No such memory version.")
    return row


def _summary(row):
    files = json.loads(row["files"])
    return {"version": row["version"], "run_id": row["run_id"], "kind": row["kind"], "from_version": row["from_version"],
            "made_at": row["made_at"], "files": len(files),
            "bytes": sum(len(t.encode("utf-8")) for t in files.values() if isinstance(t, str))}


def versions(user_id, agent_id):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_agent_memory WHERE agent_id = ? AND user_id = ? ORDER BY version DESC "
                          "LIMIT 100", (agent_id, user_id)).fetchall()
    return [_summary(r) for r in rows]


def changes(before, after):
    """{added, removed, changed: [{path, diff}]} from one version's files to the next's."""
    out = {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)), "changed": []}
    budget = MAX_DIFF
    for path in sorted(set(before) & set(after)):
        if before[path] == after[path]:
            continue
        if not isinstance(before[path], str) or not isinstance(after[path], str):
            out["changed"].append({"path": path, "diff": None})
            continue
        diff = "".join(difflib.unified_diff(before[path].splitlines(True), after[path].splitlines(True),
                                            f"a/{path}", f"b/{path}", n=2))
        out["changed"].append({"path": path, "diff": diff[:budget] if budget > 0 else None})
        budget -= len(diff)
    return out


def one(user_id, agent_id, version):
    """A version's files, and what changed from the version before it."""
    with store.connect() as db:
        row = _row(db, user_id, agent_id, version)
        before = db.execute("SELECT files FROM ade_agent_memory WHERE agent_id = ? AND version < ? ORDER BY version "
                            "DESC LIMIT 1", (agent_id, version)).fetchone()
    files = json.loads(row["files"])
    return dict(_summary(row), contents=files, changes=changes(json.loads(before["files"]) if before else {}, files))


def to_restore(user_id, agent_id, version):
    """A version's files for the workspace to write: refused when one of them could not be kept as text."""
    with store.connect() as db:
        files = json.loads(_row(db, user_id, agent_id, version)["files"])
    if any(t is None for t in files.values()):
        raise MemoryVersionError(409, "This version has a file that was not text, or too large to keep: it cannot "
                                      "be rolled back to.")
    return files
