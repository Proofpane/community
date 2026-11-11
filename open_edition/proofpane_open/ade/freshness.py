"""An agent's freshness. From the workspace's daily sweep: the memory
files it archived, and its repository's knowledge files by tier (knowledge.py there); from the app's own ledger: the
told facts recorded more than TOLD_DAYS ago, and the facts that contradict each other. From these: `current_share`
(current knowledge files over the tiered ones), `stale_count` (current files past their check and old told facts) and
`contradictions`; the owner is told once when stale items pass STALE_SHARE of the agent's knowledge items (its knowledge
files and current facts). The sweep changes nothing but its archive move; re-verification is a run, weekly, off unless
the owner turns it on for the agent: it is given each flagged item with its source, and the source decides."""

import json
import re
import time

TOLD_DAYS = 30
STALE_SHARE = 0.2
REVERIFY_EVERY = 7 * 86400
MAX_LISTED = 50
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[0-9a-f]{12}$")


def _knowledge(given):
    """A sweep's knowledge part held to its shapes, or None."""
    if not isinstance(given, dict):
        return None
    number = lambda k: given.get(k) if isinstance(given.get(k), int) and not isinstance(given.get(k), bool) \
        and 0 <= given.get(k) < 10 ** 6 else 0  # noqa: E731
    stale = []
    for s in (given.get("stale") if isinstance(given.get("stale"), list) else [])[:200]:
        if isinstance(s, dict) and isinstance(s.get("path"), str) and len(s["path"]) <= 300:
            stale.append({"path": s["path"], "verify_by": s.get("verify_by") if isinstance(s.get("verify_by"), str)
                          and _DAY.match(s["verify_by"]) else None,
                          "source": s.get("source")[:300] if isinstance(s.get("source"), str) else None})
    return {**{k: number(k) for k in ("files", "current", "historical", "deprecated", "untiered")}, "stale": stale}


def take(db, user_id, sweep, now=None):
    """The workspace's last sweep, kept per agent of the account's; then the agents due a re-verify run, with its
    prompt: [(agent row, prompt)]."""
    now = int(now if now is not None else time.time())
    day = sweep.get("day") if isinstance(sweep, dict) else None
    result = sweep.get("result") if isinstance(sweep, dict) and isinstance(sweep.get("result"), dict) else {}
    if not (isinstance(day, str) and _DAY.match(day)):
        return []
    due = []
    for agent_id, entry in result.items():
        if not (isinstance(agent_id, str) and _ID.match(agent_id)) or not isinstance(entry, dict):
            continue
        agent = db.execute("SELECT * FROM ade_agents WHERE id = ? AND user_id = ? AND retired_at IS NULL",
                           (agent_id, user_id)).fetchone()
        if agent is None:
            continue
        archived = [p for p in (entry.get("archived") if isinstance(entry.get("archived"), list) else [])
                    if isinstance(p, str) and len(p) <= 300][:200]
        knowledge = _knowledge(entry.get("knowledge"))
        db.execute("INSERT INTO ade_agent_freshness (agent_id, user_id, day, knowledge, archived) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(agent_id) DO UPDATE SET day = excluded.day, knowledge = excluded.knowledge, "
                   "archived = excluded.archived", (agent_id, user_id, day, json.dumps(knowledge),
                                                    json.dumps(archived)))
        if agent["reverify"]:
            row = db.execute("SELECT reverified_at FROM ade_agent_freshness WHERE agent_id = ?", (agent_id,)).fetchone()
            items = reverify_items(knowledge, told_stale(db, agent_id, now))
            if items and (row["reverified_at"] is None or now - row["reverified_at"] >= REVERIFY_EVERY):
                due.append((agent, items))
                db.execute("UPDATE ade_agent_freshness SET reverified_at = ? WHERE agent_id = ?", (now, agent_id))
    return due


def told_stale(db, agent_id, now=None):
    now = int(now if now is not None else time.time())
    return db.execute("SELECT * FROM ade_agent_facts WHERE agent_id = ? AND expired_at IS NULL AND basis = 'told' AND "
                      "recorded_at < ? ORDER BY recorded_at", (agent_id, now - TOLD_DAYS * 86400)).fetchall()


def reverify_items(knowledge, told):
    """What a re-verify run is given, as data and never as its prompt (a told fact's value and a file's source were
    written by others): each flagged item with its source; None when nothing is flagged. The workspace keeps them in a
    file beside the run and names it in the run's first message as information."""
    items = []
    for s in (knowledge or {}).get("stale", [])[:MAX_LISTED]:
        items.append({"kind": "knowledge", "path": s["path"], "source": s["source"] or None,
                      "verify_by": s["verify_by"] or None})
    for f in told[:max(0, MAX_LISTED - len(items))]:
        items.append({"kind": "fact", "id": f["id"], "subject": f["subject"], "relation": f["relation"],
                      "object": f["object"], "source": f["source"] or None})
    return items or None


def summary(db, agent_id, now=None):
    """The agent's freshness now: the last sweep's part, and the ledger's as it is."""
    now = int(now if now is not None else time.time())
    row = db.execute("SELECT * FROM ade_agent_freshness WHERE agent_id = ?", (agent_id,)).fetchone()
    knowledge = json.loads(row["knowledge"]) if row and row["knowledge"] else None
    archived = json.loads(row["archived"]) if row and row["archived"] else []
    told = len(told_stale(db, agent_id, now))
    current_facts, contradictions = db.execute(
        "SELECT COUNT(*), SUM(contradicts IS NOT NULL) FROM ade_agent_facts WHERE agent_id = ? AND expired_at IS NULL",
        (agent_id,)).fetchone()
    files = knowledge["files"] if knowledge else 0
    tiered = (knowledge["current"] + knowledge["historical"] + knowledge["deprecated"]) if knowledge else 0
    stale = (len(knowledge["stale"]) if knowledge else 0) + told
    items = files + (current_facts or 0)
    return {"day": row["day"] if row else None, "knowledge": knowledge, "archived": archived,
            # current and not past its check (the scan's stale ones are current files past it, or with none)
            "current_share": round(max(0, knowledge["current"] - len(knowledge["stale"])) / tiered, 4)
            if knowledge and tiered else None,
            "stale_count": stale, "told_stale": told, "contradictions": contradictions or 0, "items": items,
            "stale_share": round(stale / items, 4) if items else None}


def alert(db, agent, now=None):
    """The notifier's key once stale items pass STALE_SHARE of the agent's knowledge items, else None: the key names the
    day they first did, so the owner is told once a time it happens (agents.alerts, in fixed words)."""
    got = summary(db, agent["id"], now)
    over = got["stale_share"] is not None and got["stale_share"] > STALE_SHARE
    row = db.execute("SELECT stale_since FROM ade_agent_freshness WHERE agent_id = ?", (agent["id"],)).fetchone()
    since = row["stale_since"] if row else None
    if over and since is None:
        since = time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))
        db.execute("INSERT INTO ade_agent_freshness (agent_id, user_id, day, stale_since) VALUES (?,?,?,?) ON "
                   "CONFLICT(agent_id) DO UPDATE SET stale_since = excluded.stale_since",
                   (agent["id"], agent["user_id"], since, since))
    elif not over and since is not None:
        db.execute("UPDATE ade_agent_freshness SET stale_since = NULL WHERE agent_id = ?", (agent["id"],))
        return None
    if not over:
        return None
    return f"agent-stale:{agent['id']}:{since}"
