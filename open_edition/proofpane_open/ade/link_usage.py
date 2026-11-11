"""A linked session's usage, for the Proofpane organisation its account is paired with.

The link's Stop hook (open_edition/ade_link.py, the mirror's `mirror reply`) reads, from the session's own transcript,
each reply since the last it sent: its request id, the session's id, its model, its four token counts, when it began
and who began its turn, never a word of it. It sends them here, signed as each of the link's requests is
(`POST /api/ade/link/usage`, {records}), and here they are:

- taken from a live link of the account only (link.authenticate), and checked whole before anything is kept: at most
  PER_POST replies in one request, each with those fields and no other, each text of the form its field has and
  refused when it is shaped like a secret (link.check_secrets, which names the shape, never the value), each count a
  whole number from 0 to COUNT_MAX; and at most PER_HOUR replies kept an hour per link, counted from the table, as the
  link's other limits are.
- kept only while the account is paired (ade/proofpane.py). An account with no pairing keeps none and forwards nothing,
  and the answer says so ({paired: false}): its link then sends no usage for ten minutes, and asks again with an empty
  request before it sends any (ade_link.py's _send_usage), so an unpaired account's link sends the app none of it but
  for one request right after an unpairing, from a link that last heard the account was paired, kept none of here.
- kept once per account and request id (ade_link_usage), whichever of its links sends it and however often: a reply
  reaches Proofpane once from here.
- forwarded, each in a row of its own, while this deployment still names its address (proofpane.addresses(); a reply
  of a pairing whose address it no longer names is dropped, and said), to `<address>/api/v1/mcp/event` of the
  account's own pairing, with that pairing's device id and token, as the workspace's plugin posts its rows
  (core_plugin/hooks/proofpane_hook.py):
  {device_id, token, vendor "ade", agent "default", event_type "mcp.subscription.usage", payload: {request_id,
  session_id (Claude Code's own id for the session, which the History list and the mirrored notes name a linked
  machine's session by: the ADE makes none of its own for it), ade_kind "linked", ade_link_name, ade_started_by where
  the transcript says (person or schedule), ade_sign_in "unknown" (a link reads no status line), client "claude_code",
  count 1, records [the reply's request_id, session_id, model, its four counts and ts], totals (its four counts)}}.
  No claude_account: the workspace's account ids mean nothing on a linked machine.
- tried at once, in a thread of its own, so the link's answer never waits on Proofpane, and its events never wait on a
  forward; a try that fails is made again after RETRY_AFTER (1, 5 and 30 minutes, by the sweep that runs every minute
  while the app serves), and after TRIES tries the reply is dropped: its row kept as dropped, with why and when, which
  the page shows beside the pairing, and a line in the app's log. A reply that waits when its account is unpaired, or
  paired again, is dropped then: it goes only through the pairing it came under. Each try claims its reply first (its
  next try moved CLAIM seconds on), so two forwards never send it at once; a try cut off before its answer is kept
  (the app stopped mid-way) is made again once the claim lapses, so Proofpane can then be sent that reply twice, and
  keeps one record of it by its request id only where it does so itself.
- kept KEEP_DAYS after it was sent or dropped, so a reply a link sends again in that time is still known.

What leaves the app here: those rows, to the address of the account's own pairing alone, each with its device's id and
token. Never a word of a session, a key or a sign-in's secret: a row holds the counts, the ids, the model and the times.
"""

import re
import sys
import threading
import time

from .. import config, store
from . import link, proofpane

PER_POST = 50               # replies in one request: what ade_link.py sends at most
PER_HOUR = 3000             # replies kept an hour per link
COUNT_MAX = 10 ** 9
FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
KEYS = frozenset(("request_id", "session_id", "model", "ts", "started_by") + FIELDS)
STARTED_BY = ("person", "schedule", "hook", "controller")
TRIES = 4
RETRY_AFTER = (60, 300, 1800)   # seconds after the first, second and third try
CLAIM = 120                 # seconds a try under way holds its row: one that never ends is tried again after it
TIMEOUT = 10                # seconds a forward may take
BATCH = 100                 # rows a forward reads at a time
SWEEP_EVERY = 60
KEEP_DAYS = 30
UNPAIRED = "the account was unpaired, or paired again, before it went"
UNNAMED = "this deployment no longer names the pairing's address (OPEN_EDITION_PROOFPANE_ADDRESSES)"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@\[\]-]{0,99}")
_WHEN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{5,20}(?:Z|[+-][0-9]{2}:?[0-9]{2})")


def _now(now):
    return int(now if now is not None else time.time())


def _text(value, pattern, what, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise link.LinkError(422, f"A reply's {what} is not in the form the link sends.")
    return link.check_secrets(value, what)


def _record(r):
    """One reply as the link sends it, checked; LinkError(422) naming what is wrong."""
    if not isinstance(r, dict) or set(r) - KEYS or not {"request_id", "session_id"} <= set(r):
        raise link.LinkError(422, "A reply is {request_id, session_id, model, ts, started_by?, " + ", ".join(FIELDS) +
                             "}, and nothing else.")
    out = {"request_id": _text(r["request_id"], _ID, "request id"),
           "session_id": _text(r["session_id"], link._SESSION_ID, "session id"),
           "model": _text(r.get("model"), _MODEL, "model", required=False),
           "ts": _text(r.get("ts"), _WHEN, "time", required=False)}
    by = r.get("started_by")
    if by is not None and by not in STARTED_BY:
        raise link.LinkError(422, "A reply's turn was started by " + ", ".join(STARTED_BY) + ", or it does not say.")
    out["started_by"] = by
    for f in FIELDS:
        v = r.get(f, 0)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= COUNT_MAX:
            raise link.LinkError(422, f"A reply's {f} is a whole number from 0 to {COUNT_MAX}.")
        out[f] = v
    return out


def take(me, data, now=None):
    """Keep the replies a link sent, once per account and request id, while its account is paired; start their
    forward. Returns {paired, taken}: whether the account is paired (nothing is kept when it is not), and how many of
    them were new here."""
    now = _now(now)
    if not isinstance(data, dict) or set(data) != {"records"} or not isinstance(data["records"], list):
        raise link.LinkError(422, "Usage is {records: [...]}.")
    if len(data["records"]) > PER_POST:
        raise link.LinkError(422, f"At most {PER_POST} replies in one request.")
    records = [_record(r) for r in data["records"]]
    taken = 0
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        pairing = db.execute("SELECT id FROM ade_proofpane_pairings WHERE user_id = ?", (me["user_id"],)).fetchone()
        if pairing is None:
            return {"paired": False, "taken": 0}
        if records and db.execute("SELECT COUNT(*) FROM ade_link_usage WHERE link_id = ? AND received_at > ?",
                                  (me["id"], now - 3600)).fetchone()[0] + len(records) > PER_HOUR:
            raise link.LinkError(429, f"A link sends at most {PER_HOUR} replies' usage an hour.")
        for r in records:
            taken += db.execute(
                "INSERT OR IGNORE INTO ade_link_usage (user_id, request_id, pairing_id, link_id, link_name, "
                "session_id, model, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, "
                "ts, started_by, received_at, state, tries, next_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'waiting',0,?)",
                (me["user_id"], r["request_id"], pairing["id"], me["id"], me["name"], r["session_id"], r["model"],
                 r["input_tokens"], r["output_tokens"], r["cache_read_input_tokens"], r["cache_creation_input_tokens"],
                 r["ts"], r["started_by"], now, now)).rowcount
    if taken:
        kick()
    return {"paired": True, "taken": taken}


def drop_waiting(db, user_id, reason, now):
    """In `db`'s transaction: every reply of the account still waiting dropped, with `reason`."""
    db.execute("UPDATE ade_link_usage SET state = 'dropped', done_at = ?, reason = ? WHERE user_id = ? AND "
               "state = 'waiting'", (now, reason, user_id))


def event(row, device):
    """The row Proofpane is sent for one reply (the module's docstring says what it holds)."""
    record = {"request_id": row["request_id"], "session_id": row["session_id"], "model": row["model"]}
    record.update({f: row[f] for f in FIELDS})
    record["ts"] = row["ts"]
    payload = {"request_id": row["request_id"], "session_id": row["session_id"], "ade_kind": "linked",
               "ade_link_name": row["link_name"], "ade_sign_in": "unknown", "client": "claude_code", "count": 1,
               "records": [record], "totals": {f: row[f] for f in FIELDS}}
    if row["started_by"]:
        payload["ade_started_by"] = row["started_by"]
    return {"device_id": device["device_id"], "token": device["token"], "vendor": "ade", "agent": "default",
            "event_type": "mcp.subscription.usage", "payload": payload}


def _settle(path, row, now, state, tries, reason):
    with store.connect(path) as db:
        if state == "waiting":
            db.execute("UPDATE ade_link_usage SET tries = ?, next_at = ?, reason = ? WHERE user_id = ? AND "
                       "request_id = ? AND state = 'waiting'",
                       (tries, now + RETRY_AFTER[tries - 1], reason, row["user_id"], row["request_id"]))
        else:
            db.execute("UPDATE ade_link_usage SET state = ?, tries = ?, done_at = ?, reason = ? WHERE user_id = ? AND "
                       "request_id = ? AND state = 'waiting'",
                       (state, tries, now, reason, row["user_id"], row["request_id"]))
    if state == "dropped" and reason != UNPAIRED:
        # the app's log says each drop but those of an unpairing, the account's own act, which the page says
        print("ade usage: a linked session's reply was dropped" + (f" after {tries} tries" if tries else "") +
              f": {reason}", file=sys.stderr)


def _try(path, row, now):
    """One forward of one claimed row; returns whether Proofpane took it."""
    try:
        device = proofpane.device_for(row["user_id"], path=path)
    except proofpane.PairingError as e:
        _settle(path, row, now, "dropped", row["tries"], str(e))
        return False
    if device is None or device["pairing_id"] != row["pairing_id"]:
        _settle(path, row, now, "dropped", row["tries"], UNPAIRED)
        return False
    if device["cloud_url"] not in proofpane.addresses():
        _settle(path, row, now, "dropped", row["tries"], UNNAMED)
        return False
    ok, why = proofpane.send_event(device["cloud_url"], event(row, device), TIMEOUT)
    tries = row["tries"] + 1
    if ok:
        _settle(path, row, now, "sent", tries, None)
    elif tries >= TRIES:
        _settle(path, row, now, "dropped", tries, why)
    else:
        _settle(path, row, now, "waiting", tries, why)
    return ok


def forward_due(now=None, path=None, limit=BATCH):
    """Forward each reply whose time has come, each claimed first (its next try moved CLAIM seconds on, where no other
    forward moved it since), so two forwards never send one reply twice. Returns how many Proofpane took."""
    now = _now(now)
    sent = 0
    with store.connect(path) as db:
        rows = db.execute("SELECT * FROM ade_link_usage WHERE state = 'waiting' AND next_at <= ? ORDER BY next_at, "
                          "received_at, rowid LIMIT ?", (now, limit)).fetchall()
    for row in rows:
        with store.connect(path) as db:
            claimed = db.execute("UPDATE ade_link_usage SET next_at = ? WHERE user_id = ? AND request_id = ? AND "
                                 "state = 'waiting' AND next_at = ?",
                                 (now + CLAIM, row["user_id"], row["request_id"], row["next_at"])).rowcount
        if claimed:
            sent += _try(path, row, now)
    return sent


_lock = threading.Lock()
_due = []                   # the databases with replies to try, in the order they were named
_running = [False]


def kick(path=None):
    """Forward what is due now, in a thread of its own, so no request waits on Proofpane: one thread at a time, which
    takes every database named meanwhile before it ends (each by its own path, as a request named it)."""
    path = path or config.db_path()
    with _lock:
        if path not in _due:
            _due.append(path)
        if _running[0]:
            return None
        _running[0] = True

    def run():
        while True:
            with _lock:
                if not _due:
                    _running[0] = False
                    return
                where = _due.pop(0)
            try:
                forward_due(path=where)
            except Exception as e:  # noqa: BLE001  a forward never takes the app down
                print(f"ade usage: a forward failed: {type(e).__name__}", file=sys.stderr)
    worker = threading.Thread(target=run, name="ade-usage", daemon=True)
    worker.start()
    return worker


def sweep(now=None, path=None):
    """Forward what is due, and forget what was sent or dropped more than KEEP_DAYS ago."""
    now = _now(now)
    forward_due(now, path)
    with store.connect(path) as db:
        db.execute("DELETE FROM ade_link_usage WHERE state IN ('sent', 'dropped') AND done_at < ?",
                   (now - KEEP_DAYS * 86400,))


def start(every=SWEEP_EVERY):
    """Run sweep in a thread of this process, at once and then every `every` seconds, while it serves (the app's
    lifespan; the tests, which never enter it, call sweep or forward_due themselves)."""
    def loop():
        while True:
            try:
                sweep()
            except Exception as e:  # noqa: BLE001  the sweep never takes the app down
                print(f"ade usage: a sweep failed: {type(e).__name__}", file=sys.stderr)
            time.sleep(every)
    worker = threading.Thread(target=loop, name="ade-usage-sweep", daemon=True)
    worker.start()
    return worker


def summary(user_id):
    """What the page shows beside the pairing: how many of the account's replies went, wait and were dropped, and the
    newest drop's time and why."""
    with store.connect() as db:
        counts = {r["state"]: r["n"] for r in db.execute(
            "SELECT state, COUNT(*) AS n FROM ade_link_usage WHERE user_id = ? GROUP BY state", (user_id,))}
        last = db.execute("SELECT done_at, reason FROM ade_link_usage WHERE user_id = ? AND state = 'dropped' ORDER BY "
                          "done_at DESC LIMIT 1", (user_id,)).fetchone()
    return {"sent": counts.get("sent", 0), "waiting": counts.get("waiting", 0), "dropped": counts.get("dropped", 0),
            "last_dropped": {"at": last["done_at"], "reason": last["reason"]} if last else None}
