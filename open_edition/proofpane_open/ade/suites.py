"""An agent's suites (its retrieval measured offline). An item is a question, the
sources that answer it (paths in the agent's repository, as relative paths, or the ids an MCP search tool gives), a
group and whether it is held out. A `rag` item is scored by its sources; a `stale` one (a question whose
naive answer is a superseded fact) by its answer: the run's reply names the current value and none of the superseded
ones, else it is a miss. Suites live in the app, never on the
workspace. An eval sends each question alone, as the prompt of a fresh run of the agent (a chat with its brief and
nothing of the item's sources), a few at a time within the agent's queue; when a run ends it is scored from the order
in which its tool results first showed each source (proofpane_workspace/metrics.py): `hit_at_5`, an expected source
among the first five, and the reciprocal rank of the first one, averaged over the eval as `mrr`. That order is the
order sources appeared in, not a retriever's ranking, and is named so; nDCG is not computed, as no grading of relevance
is defined here."""

import json
import os
import re
import secrets
import time

from .. import store

MAX_SUITES = 20
MAX_ITEMS = 50
MAX_QUESTION = 2000
MAX_SOURCES = 20
MAX_SOURCE = 300
MAX_NAME = 80
MAX_ANSWER = 500
MAX_SUPERSEDED = 10
GROUPS = ("rag", "stale")
_ID = re.compile(r"^[0-9a-f]{12}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SuiteError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _source(value):
    """A source as items and runs both name it: a relative path without a leading ./, or an id."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_SOURCE or _CONTROL.search(value):
        raise SuiteError(400, f"A source is a path in the repository or an id, at most {MAX_SOURCE} characters.")
    value = value.strip()
    return os.path.normpath(value) if "/" in value or "." in value else value


def check_item(given):
    if not isinstance(given, dict):
        raise SuiteError(400, "An item is an object.")
    question = given.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION:
        raise SuiteError(400, f"An item's question is some text, at most {MAX_QUESTION} characters.")
    group = given.get("group", "rag")
    if group not in GROUPS:
        raise SuiteError(400, "An item's group is rag or stale.")
    sources = given.get("sources") or ([] if group == "stale" else None)
    if not isinstance(sources, list) or not (0 if group == "stale" else 1) <= len(sources) <= MAX_SOURCES:
        raise SuiteError(400, f"An item names from 1 to {MAX_SOURCES} sources that answer it.")
    answer, superseded = None, []
    if group == "stale":
        answer = given.get("answer")
        superseded = given.get("superseded") or []
        if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_ANSWER or _CONTROL.search(answer) \
                or not isinstance(superseded, list) or len(superseded) > MAX_SUPERSEDED or not all(
                    isinstance(v, str) and v.strip() and len(v) <= MAX_ANSWER and not _CONTROL.search(v)
                    for v in superseded):
            raise SuiteError(400, "A stale item names its current answer, and the superseded values a run should "
                                  f"not give (at most {MAX_SUPERSEDED}).")
        answer, superseded = answer.strip(), sorted({v.strip() for v in superseded})
    return {"question": question.strip(), "sources": sorted({_source(s) for s in sources}), "grp": group,
            "held_out": 1 if given.get("held_out") is True else 0, "answer": answer, "superseded": superseded}


def _agent(db, user_id, agent_id):
    row = db.execute("SELECT * FROM ade_agents WHERE id = ? AND user_id = ?", (agent_id, user_id)).fetchone()
    if row is None:
        raise SuiteError(404, "No such agent.")
    return row


def _suite(db, user_id, suite_id):
    row = db.execute("SELECT * FROM ade_agent_suites WHERE id = ? AND user_id = ?", (suite_id, user_id)).fetchone()
    if row is None:
        raise SuiteError(404, "No such suite.")
    return row


def _name(name):
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME or _CONTROL.search(name):
        raise SuiteError(400, f"A suite's name is some text, at most {MAX_NAME} characters.")
    return name.strip()


def create(user, agent_id, name, items=()):
    """A suite of the agent's: its name and its items."""
    checked = [check_item(i) for i in items or ()]
    if len(checked) > MAX_ITEMS:
        raise SuiteError(400, f"A suite holds at most {MAX_ITEMS} items.")
    now = int(time.time())
    with store.connect() as db:
        _agent(db, user["id"], agent_id)
        if db.execute("SELECT COUNT(*) FROM ade_agent_suites WHERE agent_id = ?", (agent_id,)).fetchone()[0] >= \
                MAX_SUITES:
            raise SuiteError(409, f"An agent has at most {MAX_SUITES} suites.")
        suite_id = secrets.token_hex(6)
        db.execute("INSERT INTO ade_agent_suites (id, user_id, agent_id, name, created_at, updated_at) VALUES "
                   "(?,?,?,?,?,?)", (suite_id, user["id"], agent_id, _name(name), now, now))
        _put_items(db, user["id"], suite_id, checked, now)
    return one(user, suite_id)


def _put_items(db, user_id, suite_id, checked, now):
    db.execute("DELETE FROM ade_agent_suite_items WHERE suite_id = ?", (suite_id,))
    for item in checked:
        db.execute("INSERT INTO ade_agent_suite_items (id, suite_id, user_id, question, sources, grp, held_out, "
                   "answer, superseded, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (secrets.token_hex(6), suite_id, user_id, item["question"], json.dumps(item["sources"]),
                    item["grp"], item["held_out"], item["answer"], json.dumps(item["superseded"]), now))


def update(user, suite_id, name=None, items=None):
    """A suite renamed, or its items all given again (each one checked)."""
    checked = None if items is None else [check_item(i) for i in items]
    if checked is not None and len(checked) > MAX_ITEMS:
        raise SuiteError(400, f"A suite holds at most {MAX_ITEMS} items.")
    now = int(time.time())
    with store.connect() as db:
        _suite(db, user["id"], suite_id)
        if name is not None:
            db.execute("UPDATE ade_agent_suites SET name = ?, updated_at = ? WHERE id = ?", (_name(name), now, suite_id))
        if checked is not None:
            _put_items(db, user["id"], suite_id, checked, now)
            db.execute("UPDATE ade_agent_suites SET updated_at = ? WHERE id = ?", (now, suite_id))
    return one(user, suite_id)


def delete(user, suite_id):
    with store.connect() as db:
        _suite(db, user["id"], suite_id)
        evals = [r["id"] for r in db.execute("SELECT id FROM ade_agent_evals WHERE suite_id = ?", (suite_id,))]
        for eval_id in evals:
            db.execute("DELETE FROM ade_agent_eval_items WHERE eval_id = ?", (eval_id,))
        db.execute("DELETE FROM ade_agent_evals WHERE suite_id = ?", (suite_id,))
        db.execute("DELETE FROM ade_agent_suite_items WHERE suite_id = ?", (suite_id,))
        db.execute("DELETE FROM ade_agent_suites WHERE id = ?", (suite_id,))
    return {"deleted": suite_id}


def _item_view(row):
    return {"id": row["id"], "question": row["question"], "sources": json.loads(row["sources"]), "group": row["grp"],
            "held_out": bool(row["held_out"]), "answer": row["answer"],
            "superseded": json.loads(row["superseded"]) if row["superseded"] else []}


def _eval_view(db, row):
    items = db.execute("SELECT * FROM ade_agent_eval_items WHERE eval_id = ? ORDER BY item_id", (row["id"],)).fetchall()
    scored = [i for i in items if i["state"] == "scored"]
    ranked = [i for i in scored if i["grp"] == "rag"]
    stale = [i for i in scored if i["grp"] == "stale"]
    groups = {}
    for i in scored:
        g = groups.setdefault(i["grp"], {"items": 0, "hits": 0, "rr": 0.0})
        g["items"] += 1
        g["hits"] += i["hit_at_5"]
        g["rr"] += i["rr"] or 0.0
    return {"id": row["id"], "suite_id": row["suite_id"], "agent_id": row["agent_id"], "started_at": row["started_at"],
            "brief_version": row["brief_version"], "held_out": bool(row["held_out"]),
            "items": len(items), "scored": len(scored),
            "open": sum(1 for i in items if i["state"] in ("pending", "queued")),
            "failed": sum(1 for i in items if i["state"] == "failed"),
            "hit_at_5": round(sum(i["hit_at_5"] for i in ranked) / len(ranked), 4) if ranked else None,
            "mrr": round(sum(i["rr"] for i in ranked) / len(ranked), 4) if ranked else None,
            "stale_accuracy": round(sum(i["hit_at_5"] for i in stale) / len(stale), 4) if stale else None,
            "by_group": {g: {"items": v["items"], "hit_at_5": round(v["hits"] / v["items"], 4),
                             "mrr": round(v["rr"] / v["items"], 4) if g == "rag" else None}
                         for g, v in groups.items()},
            "results": [{"item_id": i["item_id"], "run_id": i["run_id"], "state": i["state"],
                         "hit_at_5": None if i["hit_at_5"] is None else bool(i["hit_at_5"]), "rr": i["rr"],
                         "seen": json.loads(i["seen"]) if i["seen"] else []} for i in items]}


def one(user, suite_id):
    with store.connect() as db:
        row = _suite(db, user["id"], suite_id)
        items = db.execute("SELECT * FROM ade_agent_suite_items WHERE suite_id = ? ORDER BY created_at, id",
                           (suite_id,)).fetchall()
        evals = db.execute("SELECT * FROM ade_agent_evals WHERE suite_id = ? ORDER BY started_at DESC LIMIT 10",
                           (suite_id,)).fetchall()
        return {"id": row["id"], "agent_id": row["agent_id"], "name": row["name"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "items": [_item_view(i) for i in items],
                "evals": [_eval_view(db, e) for e in evals]}


def of_agent(user, agent_id, sync=True):
    """The agent's suites with their last evals; first, as the agents' list does, the runs' records copied from the
    workspace when the last copy is stale, so an eval's scores come in as its runs end."""
    if sync:
        from . import agents
        agents._sync_if_stale(user)
    with store.connect() as db:
        _agent(db, user["id"], agent_id)
        ids = [r["id"] for r in db.execute("SELECT id FROM ade_agent_suites WHERE agent_id = ? ORDER BY created_at",
                                           (agent_id,))]
    return [one(user, i) for i in ids]


def start(user, suite_id, held_out=False):
    """An eval: each item (the held-out ones when `held_out`, the others otherwise) a run of the agent with its
    question alone as the prompt, queued a few at a time within the agent's queue."""
    from . import agents
    now = int(time.time())
    with store.connect() as db:
        suite = _suite(db, user["id"], suite_id)
        agent = _agent(db, user["id"], suite["agent_id"])
        if agent["retired_at"]:
            raise SuiteError(409, "This agent is retired.")
        if agents._paused(agent, now):
            raise SuiteError(409, "This agent is paused: resume it first.")
        items = db.execute("SELECT * FROM ade_agent_suite_items WHERE suite_id = ? AND held_out = ?",
                           (suite_id, 1 if held_out else 0)).fetchall()
        if not items:
            raise SuiteError(409, "No item to ask: " + ("none is held out." if held_out else "each is held out."))
        eval_id = secrets.token_hex(6)
        db.execute("INSERT INTO ade_agent_evals (id, suite_id, user_id, agent_id, started_at, brief_version, held_out) "
                   "VALUES (?,?,?,?,?,?,?)", (eval_id, suite_id, user["id"], agent["id"], now, agent["brief_version"],
                                              1 if held_out else 0))
        for item in items:
            db.execute("INSERT INTO ade_agent_eval_items (eval_id, item_id, grp, state) VALUES (?,?,?,'pending')",
                       (eval_id, item["id"], item["grp"]))
        fill(db, eval_id, now)
    agents._deliver_soon(user, start=True)
    return one(user, suite_id)


def fill(db, eval_id, now=None):
    """Queue the eval's next items while its agent's queue has room: each a run whose prompt is the question alone."""
    from . import agents
    now = int(now if now is not None else time.time())
    ev = db.execute("SELECT * FROM ade_agent_evals WHERE id = ?", (eval_id,)).fetchone()
    if ev is None:
        return
    agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (ev["agent_id"],)).fetchone()
    if agent is None or agent["retired_at"]:
        return
    for pending in db.execute("SELECT e.item_id, i.question FROM ade_agent_eval_items e JOIN ade_agent_suite_items i "
                              "ON i.id = e.item_id WHERE e.eval_id = ? AND e.state = 'pending' ORDER BY e.item_id",
                              (eval_id,)).fetchall():
        try:
            run_id = agents.new_run(db, agent, {"kind": "eval", "prompt": pending["question"], "at": now,
                                                "eval_id": eval_id, "item_id": pending["item_id"]}, now)
        except agents.AgentError:
            return                      # its queue is full: the next run's end queues more
        db.execute("UPDATE ade_agent_eval_items SET state = 'queued', run_id = ? WHERE eval_id = ? AND item_id = ?",
                   (run_id, eval_id, pending["item_id"]))


def score(db, run_id, now=None):
    """An eval run that ended, scored from the sources its tool results showed, in their order; then its eval's next
    items queued. A run that did not end done is counted failed, not scored."""
    row = db.execute("SELECT e.eval_id, e.item_id, r.state, r.metrics, i.sources, i.grp, i.answer, i.superseded FROM "
                     "ade_agent_eval_items e JOIN "
                     "ade_agent_runs r ON r.run_id = e.run_id JOIN ade_agent_suite_items i ON i.id = e.item_id "
                     "WHERE e.run_id = ? AND e.state = 'queued'", (run_id,)).fetchone()
    if row is None:
        return
    if row["state"] != "done":
        db.execute("UPDATE ade_agent_eval_items SET state = 'failed' WHERE eval_id = ? AND item_id = ?",
                   (row["eval_id"], row["item_id"]))
    elif row["grp"] == "stale":
        try:
            reply = json.loads(row["metrics"] or "{}").get("answer") or ""
        except ValueError:
            reply = ""
        said = " ".join(reply.split()).lower()
        right = " ".join(row["answer"].split()).lower() in said
        old = [v for v in json.loads(row["superseded"] or "[]") if " ".join(v.split()).lower() in said]
        db.execute("UPDATE ade_agent_eval_items SET state = 'scored', hit_at_5 = ?, rr = NULL, seen = ? WHERE "
                   "eval_id = ? AND item_id = ?", (1 if right and not old else 0, json.dumps(old[:10]),
                                                   row["eval_id"], row["item_id"]))
    else:
        try:
            seen = (json.loads(row["metrics"] or "{}").get("retrieval") or {}).get("sources") or []
        except ValueError:
            seen = []
        expected = set(json.loads(row["sources"]))
        rank = next((n for n, s in enumerate(seen, 1) if s in expected), None)
        db.execute("UPDATE ade_agent_eval_items SET state = 'scored', hit_at_5 = ?, rr = ?, seen = ? WHERE "
                   "eval_id = ? AND item_id = ?", (1 if rank is not None and rank <= 5 else 0,
                                                   round(1 / rank, 6) if rank else 0.0, json.dumps(seen[:10]),
                                                   row["eval_id"], row["item_id"]))
    fill(db, row["eval_id"], now)
