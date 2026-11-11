"""An agent's facts: a bi-temporal ledger kept in the app. A fact is a
subject, a relation and an object; `valid_at` and `invalid_at` say when it was true in the world, `recorded_at` and
`expired_at` when the ledger learned it and when it retired it (on the workspace's clock, so a late recording is never
taken for a late truth: a fact retires from when the one that retires it was recorded, or a run's retraction was made;
an owner's retraction is on the app's); `superseded_by` names what retired it; `source` where it came from (the run, and the tool
result, payload or message it cites); and `basis`, which the workspace sets and never the model: `observed` when the
object appears verbatim in a tool result of the same run, else `told`.

The rules run as the poll brings facts, with no model call:
- keys (the subject and the relation) are normalised: lower case, trimmed, inner whitespace collapsed;
- a `valid_at` later than its `recorded_at` is refused;
- each relation is declared per agent as `functional` (one current value per subject) or `multi` (several at once); one
  not declared is `multi`;
- for a functional relation, of two current facts with different objects the one valid later retires the other (its
  `invalid_at`, `expired_at` and `superseded_by` set; nothing is deleted), except that a `told` fact never retires an
  `observed` one: then both stay, marked as contradicting each other, until a run or the owner decides; the same object
  again is a duplicate, retired by the one already there (or retiring it, when it was told and this one observed);
- a multi fact retires only by `retract_fact`, the run's or the owner's.

The workspace keeps a copy of an agent's current facts, sent by the app, which `lookup_fact` reads: signed there as a
run's record is, so a copy a run changed reads as none, and the app sends it again whenever the workspace says its copy
holds another version of the ledger (the agents module's _mirror_facts). This record takes facts only from runs'
records, held to their shapes, and never reads the copy back."""

import json
import re
import time

from .. import store

KINDS = ("functional", "multi")
MAX_KEY = 200
MAX_OBJECT = 500
MAX_SOURCE = 500
MAX_REASON = 500
MAX_FACTS_A_RUN = 50
MAX_COPY = 1000
MAX_RELATIONS = 100
CLOCK_ALLOWANCE = 3600          # how far ahead of the app's clock a fact's recorded_at may be (the workspace's clock)
EARLIEST = -(10 ** 11)          # a valid_at as far back as dates go (before 1970 is negative)
_ID = re.compile(r"^[0-9a-f]{12}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class FactError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def key(text):
    """A subject or a relation as the ledger compares it."""
    return " ".join(str(text).split()).lower()


def same(a, b):
    """Two objects the same value: compared without case and inner whitespace."""
    return key(a) == key(b)


def check_relations(given):
    """{relation: functional | multi}, the relation names normalised."""
    if given in (None, {}):
        return {}
    if not isinstance(given, dict) or len(given) > MAX_RELATIONS:
        raise FactError(400, f"An agent's relations are at most {MAX_RELATIONS}, each functional or multi.")
    out = {}
    for name, kind in given.items():
        name = key(name) if isinstance(name, str) else ""
        if not name or len(name) > MAX_KEY or _CONTROL.search(name) or kind not in KINDS:
            raise FactError(400, "A relation is a name, functional (one value at a time) or multi (several).")
        out[name] = kind
    return out


def _int(value, earliest=0):
    return value if isinstance(value, int) and not isinstance(value, bool) and earliest <= value < 10 ** 11 else None


def _text(value, most):
    return value[:most] if isinstance(value, str) and value.strip() and not _CONTROL.search(value) else None


def clean(given):
    """A run's facts and retractions from the workspace, held to their shapes: ({id: fact}, [retraction])."""
    facts = {}
    for f in (given.get("facts") if isinstance(given.get("facts"), list) else [])[:MAX_FACTS_A_RUN]:
        if not isinstance(f, dict) or not (isinstance(f.get("id"), str) and _ID.match(f["id"])):
            continue
        subject, relation, obj = (_text(f.get("subject"), MAX_KEY), _text(f.get("relation"), MAX_KEY),
                                  _text(f.get("object"), MAX_OBJECT))
        recorded = _int(f.get("recorded_at"))
        if not (subject and relation and obj and recorded):
            continue
        valid = _int(f.get("valid_at"), EARLIEST)               # a time before 1970 is negative, 0 is 1970
        facts[f["id"]] = {"id": f["id"], "subject": key(subject), "relation": key(relation), "object": obj.strip(),
                          "valid_at": recorded if valid is None else valid, "recorded_at": recorded,
                          "source": _text(f.get("source"), MAX_SOURCE),
                          "basis": "observed" if f.get("basis") == "observed" else "told"}
    retractions = []
    for r in (given.get("retractions") if isinstance(given.get("retractions"), list) else [])[:MAX_FACTS_A_RUN]:
        if isinstance(r, dict) and isinstance(r.get("id"), str) and _ID.match(r["id"]):
            retractions.append({"id": r["id"], "reason": _text(r.get("reason"), MAX_REASON) or "retracted",
                                "at": _int(r.get("at"))})
    return facts, retractions


def _relations(db, agent_id):
    row = db.execute("SELECT relations FROM ade_agents WHERE id = ?", (agent_id,)).fetchone()
    try:
        return json.loads(row["relations"]) if row and row["relations"] else {}
    except ValueError:
        return {}


def _retire(db, older, by, now):
    """`older` retired by `by`, valid later: from when `by` was recorded (or `older`, when later), on the workspace's
    clock as both are, so it never expires before it was recorded."""
    db.execute("UPDATE ade_agent_facts SET invalid_at = ?, expired_at = ?, superseded_by = ?, changed_at = ? "
               "WHERE id = ?", (by["valid_at"], max(by["recorded_at"], older["recorded_at"]), by["id"], now,
                                older["id"]))


def take(db, user_id, agent_id, run_id, given, now=None):
    """The facts and retractions a run recorded, applied to the ledger once each (by id): which ones were taken and
    which refused, with why."""
    now = int(now if now is not None else time.time())
    facts, retractions = clean(given)
    relations = _relations(db, agent_id)
    taken, refused = [], []
    for f in facts.values():
        if db.execute("SELECT 1 FROM ade_agent_facts WHERE id = ?", (f["id"],)).fetchone():
            continue                                    # read again: taken already
        if f["valid_at"] > f["recorded_at"]:
            refused.append({"id": f["id"], "why": "valid_at is after it was recorded"})
            continue
        if f["recorded_at"] > now + CLOCK_ALLOWANCE:
            refused.append({"id": f["id"], "why": "recorded after the app's time, past its allowance"})
            continue
        db.execute("INSERT INTO ade_agent_facts (id, user_id, agent_id, run_id, subject, relation, object, valid_at, "
                   "recorded_at, source, basis, changed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f["id"], user_id, agent_id, run_id, f["subject"], f["relation"], f["object"], f["valid_at"],
                    f["recorded_at"], f["source"], f["basis"], now))
        taken.append(f["id"])
        if relations.get(f["relation"]) != "functional":
            continue
        current = db.execute("SELECT * FROM ade_agent_facts WHERE agent_id = ? AND subject = ? AND relation = ? AND "
                             "expired_at IS NULL AND id != ? ORDER BY valid_at DESC",
                             (agent_id, f["subject"], f["relation"], f["id"])).fetchall()
        for old in current:
            if same(old["object"], f["object"]):
                # the same value again: one record of it stays current, an observation before a telling
                keep, drop = (f, old) if f["basis"] == "observed" and old["basis"] == "told" else (old, f)
                db.execute("UPDATE ade_agent_facts SET expired_at = ?, superseded_by = ?, changed_at = ? WHERE id = ?",
                           (max(f["recorded_at"], drop["recorded_at"]), keep["id"], now, drop["id"]))
                if drop is f:
                    break
                continue
            later, earlier = (f, old) if f["valid_at"] >= old["valid_at"] else (old, f)
            if later["basis"] == "told" and earlier["basis"] == "observed":
                # a telling never retires an observation: both stay, each naming the other
                db.execute("UPDATE ade_agent_facts SET contradicts = ?, changed_at = ? WHERE id = ?",
                           (earlier["id"], now, later["id"]))
                db.execute("UPDATE ade_agent_facts SET contradicts = ?, changed_at = ? WHERE id = ?",
                           (later["id"], now, earlier["id"]))
                continue
            _retire(db, dict(earlier), later, now)
            if earlier is f:
                break
    for r in retractions:
        db.execute("UPDATE ade_agent_facts SET expired_at = ?, retracted = ?, changed_at = ? WHERE id = ? AND "
                   "agent_id = ? AND expired_at IS NULL", (r["at"] or now, r["reason"], now, r["id"], agent_id))
    if taken or retractions:
        db.execute("UPDATE ade_agents SET facts_version = facts_version + 1 WHERE id = ?", (agent_id,))
    return {"taken": taken, "refused": refused}


def view(row):
    return {k: row[k] for k in ("id", "subject", "relation", "object", "valid_at", "invalid_at", "recorded_at",
                                "expired_at", "superseded_by", "source", "basis", "run_id", "contradicts",
                                "retracted")}


def of_agent(db, user_id, agent_id, current_only=False):
    rows = db.execute("SELECT * FROM ade_agent_facts WHERE agent_id = ? AND user_id = ?"
                      + (" AND expired_at IS NULL" if current_only else "") +
                      " ORDER BY subject, relation, valid_at DESC, recorded_at DESC", (agent_id, user_id)).fetchall()
    return [view(r) for r in rows]


def copy_for(db, agent_id):
    """The current facts the workspace keeps for its runs' lookup_fact: at most MAX_COPY, the newest first."""
    rows = db.execute("SELECT * FROM ade_agent_facts WHERE agent_id = ? AND expired_at IS NULL ORDER BY recorded_at "
                      "DESC LIMIT ?", (agent_id, MAX_COPY)).fetchall()
    return [{k: r[k] for k in ("id", "subject", "relation", "object", "valid_at", "recorded_at", "basis", "source",
                               "contradicts")} for r in rows]


def retract(user, agent_id, fact_id, reason):
    """The owner retires a fact."""
    reason = _text(reason, MAX_REASON) or "retracted by the owner"
    now = int(time.time())
    with store.connect() as db:
        got = db.execute("UPDATE ade_agent_facts SET expired_at = ?, retracted = ?, changed_at = ? WHERE id = ? AND "
                         "agent_id = ? AND user_id = ? AND expired_at IS NULL",
                         (now, reason, now, fact_id, agent_id, user["id"]))
        if got.rowcount == 0:
            raise FactError(404, "No such current fact of this agent's.")
        db.execute("UPDATE ade_agents SET facts_version = facts_version + 1 WHERE id = ?", (agent_id,))
    return {"retracted": fact_id}
