"""The shell's summary, under /api/ade/summary: what the app's title bar, its list of sessions and the tab's title
show, in one answer, which the page reads every few seconds in place of the nine routes its shell used to poll (the
workspace's state, its repositories and sessions, the approval hook's requests, what connected apps proposed, the
sessions agents asked for, the linked sessions and their machines' lists, and the agents' tree).

    GET /api/ade/summary    {workspace, approvals, proposals, asks, links: {live, alerts}, agents, stamps}

`workspace` is what GET /api/ade/workspace answers; null, with `workspace_error` saying why, when its provider could not
say. The counts are what the title bar's badges count: the approval hook's requests waiting, with the asks for more in
a task's grant that wait for the owner's passkey (`approvals`, ade/task_grants.py); what
connected apps proposed that waits for the owner, an answer to a held command counted as that command (`proposals`,
null where the deployment has no connector); the new sessions the workspace's agents asked for (`asks`, null while the
workspace does not run); the live linked sessions and what needs the owner among them, each silent one, each open
question to the owner or to all, each request to be linked and each session a ticket paired that waits on its hold
(`links`); and the agents' runs waiting for the owner (`agents`, null where the account is given no workspace).
`stamps` holds a stamp for each list the page keeps (`sessions`, `repos`, `approvals`, `proposals`, `asks`, `links`,
`history`): a digest of the list as its own route serves it (the linked sessions' without the server's time, which is
in each answer), different each time the list changes, null when the list could not be read (or is not there to read,
as a stopped workspace's sessions). The page reads a list again only when its stamp is not the one it last read.

A stamp says whether a list changed and nothing of what it holds, and nothing else in the answer is a word any session,
machine or app sent, sealed or in the clear: a sealed text stays where its own route serves it, to a page that can open
it. Reading the summary settles the account's holds whose time has come, as reading the linked sessions does.

The workspace's three lists come from its agent, which may be slow or not answer at all (a machine half up, an agent
stopped): they are read at once, each in a thread of its own, while the account's own lists are read, and the summary
waits for them at most WORKSPACE_WAIT seconds. A list not read by then is stamped as its last read that did not fail,
if that finished in the last LATE_KEPT seconds (so an agent slower than the wait is still followed, a summary behind),
and null otherwise; its read goes on in its thread, and the next summary waits on it rather than asking the agent again
while it is under way. So an agent that does not answer holds no summary, and no badge of the account's own, for longer
than WORKSPACE_WAIT, nor gathers more than one read of each list.
"""

import concurrent.futures
import hashlib
import json
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import oauth, store
from ..deps import current_user
from . import approvals, link_asks, link_routes, proposals, task_grants, workspace_client, workspace_routes, workspaces

router = APIRouter(prefix="/api/ade/summary")
NO_STORE = {"Cache-Control": "no-store"}
STAMPED = ("sessions", "repos", "approvals", "proposals", "asks", "links", "history")
# a list of the workspace's that could not be read this time: its stamp is null, and the page keeps what it has
_UNREADABLE = (HTTPException, workspaces.WorkspaceAccess, workspace_client.WorkspaceCallError)
WORKSPACE_LISTS = ("sessions", "repos", "asks")
WORKSPACE_WAIT = 3.0        # seconds a summary waits for the workspace's lists, read at once
LATE_KEPT = 60.0            # seconds a list read after its own summary answered may stand in for a later one's
_reads_lock = threading.Lock()
_reads = {}                 # (account, list): the Future of the list's read under way, or of the last one
_last_read = {}             # (account, list): (when, digest) of the list's last read that did not fail


def stamp(value):
    """A list's stamp: sixteen hex digits of the sha256 of its JSON, its keys sorted. It is another whenever the list
    changes, and says nothing of what the list holds."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


def _served(response):
    """What a route that answers with a JSONResponse of its own (the session channel's do) answered, as data."""
    return json.loads(response.body)


def _runs(ws):
    return bool(ws and ws.get("enabled") and ws.get("allowed") is not False and ws.get("running")
                and not ws.get("starting"))


def _digest(key, got):
    """What the summary keeps of one of the workspace's lists: its stamp, and for the asks how many there are."""
    asks = got.get("asks") if key == "asks" and isinstance(got, dict) else None
    return stamp(got), (len(asks) if isinstance(asks, list) else None)


def _read_list(user, key, future):
    """One of the workspace's lists, read as its own route reads it, in a thread of its own: its digest, or what it
    raised, in `future`. A fault that is not the workspace's is also said in the log, for a summary that no longer
    waits on it."""
    try:
        digest = _digest(key, getattr(workspace_routes, key)(user))
    except _UNREADABLE as e:
        future.set_exception(e)
        return
    except Exception as e:  # noqa: BLE001  ours, not the workspace's: the summary waiting on it answers 500
        print(f"summary: the workspace's {key} could not be read: {type(e).__name__}", file=sys.stderr)
        future.set_exception(e)
        return
    with _reads_lock:
        _last_read[_slot(user, key)] = (time.monotonic(), digest)
    future.set_result(digest)


def _slot(user, key):
    """Whose read of which list: the account's own, so no summary waits on, or stands in with, another account's."""
    return user["id"], key


def _workspace_reads(user):
    """The workspace's three lists, each read at once in a thread of its own: {list: Future of its digest}. A list
    whose read for an earlier summary is still under way is not asked for again: this summary waits on that read."""
    futures = {}
    with _reads_lock:
        for key in WORKSPACE_LISTS:
            slot = _slot(user, key)
            future = _reads.get(slot)
            if future is None or future.done():
                future = _reads[slot] = concurrent.futures.Future()
                threading.Thread(target=_read_list, args=(user, key, future), name=f"summary-{key}",
                                 daemon=True).start()
            futures[key] = future
    return futures


def _waited(user, key, future, deadline):
    """A list's digest once its read is done, waited for until the deadline. Not done by then: the digest of a read of
    it that finished in the last LATE_KEPT seconds, else None, as a list that could not be read is (its stamp null)."""
    try:
        return future.result(timeout=max(0.0, deadline - time.monotonic()))
    except concurrent.futures.TimeoutError:
        with _reads_lock:
            last = _last_read.get(_slot(user, key))
        return last[1] if last is not None and time.monotonic() - last[0] <= LATE_KEPT else None
    except _UNREADABLE:
        return None


def _agents_waiting(user_id):
    """The agents' runs waiting for the owner: those agents.listing names in `waiting` (its own runs, not an
    improve loop's, whose state is waiting)."""
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE user_id = ? AND loop = 0 AND state = 'waiting'",
                          (user_id,)).fetchone()[0]


def summary(user):
    """The account's summary, as GET /api/ade/summary answers it."""
    out = {"workspace": None, "approvals": 0, "proposals": None, "asks": None, "links": {"live": 0, "alerts": 0},
           "agents": None, "stamps": dict.fromkeys(STAMPED)}
    stamps = out["stamps"]
    try:
        out["workspace"] = workspace_routes.status(user)
    except workspaces.WorkspaceAccess as e:
        out["workspace_error"] = str(e)
    ws = out["workspace"]
    # the workspace's own lists, read from it while it runs (each as its route reads it), all at once and while the
    # account's own are read below; none while it does not run
    deadline = time.monotonic() + WORKSPACE_WAIT
    reading = _workspace_reads(user) if _runs(ws) else {}
    requests = approvals.list_for_owner(user["id"])
    # the asks for more in a task's grant wait for the owner too, in the same list (ade/task_grants.py): counted
    # with the held commands, and in their stamp, which is as it was while none waits
    widenings = task_grants.waiting(user["id"])
    out["approvals"] = sum(1 for r in requests if r["state"] == "pending") + len(widenings)
    stamps["approvals"] = stamp({"requests": requests, "grant_widenings": widenings} if widenings else requests)
    if oauth.enabled():
        waiting = proposals.waiting(user["id"])
        out["proposals"] = sum(1 for p in waiting if p["state"] == "proposed" and p["kind"] != "approval")
        stamps["proposals"] = stamp(waiting)
    # the linked sessions as the page reads them, a hold whose time has come settled first; a request waiting for the
    # owner's answer counts, and so does a session a ticket paired while its hold runs
    channel = _served(link_routes.sessions(user))
    questions = [q for q in channel["questions"] if q.get("to") in ("owner", "all")]
    out["links"] = {"live": len(channel["links"]),
                    "alerts": sum(1 for x in channel["links"] if x.get("silent")) + len(questions)
                    + len(channel.get("waitings") or ())
                    + sum(1 for a in channel.get("asks") or () if a.get("state") == "pending"
                          or (a.get("state") == "held" and a.get("via") == link_asks.TICKET))}
    # the server's time is in every answer and is no change of the list
    stamps["links"] = stamp({k: v for k, v in channel.items() if k != "server_time"})
    stamps["history"] = stamp(_served(link_routes.history(user)))
    if ws and ws.get("enabled") and ws.get("allowed") is not False:
        out["agents"] = _agents_waiting(user["id"])
    for key, future in reading.items():
        digest = _waited(user, key, future, deadline)
        if digest is None:
            continue
        stamps[key], count = digest
        if key == "asks" and count is not None:
            out["asks"] = count
    return out


@router.get("")
def read(user=Depends(current_user)):
    return JSONResponse(summary(user), headers=NO_STORE)
