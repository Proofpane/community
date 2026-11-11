"""Agents: a chat kept under a name, with a brief, and run again (the workspace's side: proofpane_workspace/agents.py).

The app keeps every agent and every run's record, so the page that watches a hundred of them reads one database and
never starts a machine. The workspace keeps what only the machine has: each agent's worktree and memory, and its live
runs.

- An agent is mirrored to its workspace at each change while the machine runs, and at the machine's next start
  otherwise, before any of its runs is sent.
- A run is made here first, queued, with its id. When the machine runs it is sent at once; otherwise the machine is
  started for it, as the console starts one, and it is sent once the workspace answers.
- While a machine runs, a poller copies its runs' records back every POLL_EVERY seconds (the workspace answers the
  runs changed since the last change number it gave), and a page's read does the same first when the last copy is
  more than SYNC_FRESH seconds old. Each agent's totals are worked out again when one of its runs changes.

Every query is the account's own (user_id).
"""

import base64
import hashlib
import json
import re
import secrets
import sys
import threading
import time

from .. import auth, store
from . import agent_metrics, facts, freshness, improve, memory_versions, quality, shapes, suites
from . import workspace_client as client
from . import workspaces

MAX_NAME = 80
MAX_GROUP = 40
MAX_BRIEF = 4000
MAX_PROMPT = 4000
MAX_RULES = 100
MAX_RULE = 200
MAX_NOTE = 500
UNATTENDED = ("dontAsk", "auto", "bypassPermissions")
MAX_PRECHECK = 2000
LIMITS = {"max_run_seconds": (3600, 60, 86400), "max_runs_per_day": (24, 1, 1000), "max_steps": (200, 1, 10000),
          "queue_max": (10, 1, 100), "storage_mb": (200, 1, 10000)}
POLL_EVERY = 30
SYNC_FRESH = 5
PAGES = 25                  # pages of runs a poll reads at most
PAGE_MOST = 200             # runs a page gives (the workspace's own PAGE)
RUNS_SHOWN = 5
RUNS_PAGE = 50
STATES = ("queued", "working", "waiting", "done", "failed", "blocked", "stopped", "skipped")
LIVE = ("working", "waiting")
ENDED = ("done", "failed", "blocked", "stopped", "skipped")
GROUP_DEFAULT = "Agents"
DISPATCH_WIDTH = 5          # runs one run (or a chat) may start: an agent's own max_dispatch_per_run, a chat's this
MAX_DEPTH = 2
_ID = re.compile(r"^[0-9a-f]{12}\Z")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
_RULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.*-]{0,99}(\(.{0,190}\))?\Z")
_MODEL = re.compile(r"^[a-z][a-z0-9.-]{0,63}(?:\[1m\])?\Z")
_WORD = re.compile(r"^[A-Za-z]{1,40}\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SCRATCH = re.compile(r"^agent-[0-9a-f]{12}-scratch$")
_lock = threading.Lock()                # one sync per account at a time
_syncing = set()
_sending = {}                           # account -> its lock: one sender at a time, so no run is sent twice
_mirroring = {}                         # account -> its lock: one mirror at a time (_mirror)


class AgentError(Exception):
    """A refusal, with its HTTP status and the members main.py adds to its answer beside the detail (the
    teammate track's `off`)."""

    def __init__(self, status, detail, **extra):
        super().__init__(detail)
        self.status = status
        self.extra = extra


def _text(value, most, what, required=False):
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > most or _CONTROL.search(value):
        raise AgentError(400, f"{what} is at most {most} characters, with no control characters.")
    return value.strip()


def check(given, partial=False):
    """An agent's fields as the page gives them, checked: only those given when `partial`."""
    if not isinstance(given, dict):
        raise AgentError(400, "An agent is an object.")
    out = {}
    if not partial or "name" in given:
        out["name"] = _text(given.get("name"), MAX_NAME, "A name", required=True)
    if not partial or "brief" in given:
        out["brief"] = _text(given.get("brief"), MAX_BRIEF, "A brief", required=True)
    if not partial or "group" in given:
        out["grp"] = _text(given.get("group"), MAX_GROUP, "A group")
    if not partial or "repo" in given:
        repo = given.get("repo")
        if repo in (None, ""):
            out["repo"] = None
        elif isinstance(repo, str) and _REPO.match(repo) and ".." not in repo and not repo.endswith(".git") \
                and not _SCRATCH.match(repo):
            out["repo"] = repo
        else:
            raise AgentError(400, "An agent's repository is one of the workspace's, by its name, or none.")
    if not partial or "base_ref" in given:
        base = given.get("base_ref")
        if base in (None, ""):
            out["base_ref"] = None
        elif isinstance(base, str) and _REF.match(base) and ".." not in base and not base.endswith((".lock", "/")):
            out["base_ref"] = base
        else:
            raise AgentError(400, "An agent starts from a branch, a tag or a commit of its repository.")
    if not partial or "keep_worktree" in given:
        out["keep_worktree"] = 0 if given.get("keep_worktree") is False else 1
    if not partial or "account" in given:
        account = given.get("account")
        if account in (None, "", "default"):
            out["account"] = None
        elif isinstance(account, str) and _ID.match(account):
            out["account"] = account
        else:
            raise AgentError(400, "An agent's account is one of the workspace's, by its id, or the default.")
    if not partial or "chat_settings" in given:
        settings = given.get("chat_settings") or {}
        if not isinstance(settings, dict) or set(settings) - {"model", "effort", "permission_mode"}:
            raise AgentError(400, "An agent's settings are its model, its effort and its permission mode.")
        kept = {}
        for key in ("model", "effort", "permission_mode"):
            value = settings.get(key)
            if value in (None, ""):
                continue
            if not isinstance(value, str) or not (_MODEL.match(value) if key == "model" else _WORD.match(value)):
                raise AgentError(400, f"An agent's {key.replace('_', ' ')} is a word, as Claude Code names it.")
            kept[key] = value
        out["chat_settings"] = json.dumps(kept, sort_keys=True)
    if not partial or "allow" in given:
        allow = given.get("allow") or []
        if not isinstance(allow, list) or len(allow) > MAX_RULES or not all(
                isinstance(r, str) and len(r) <= MAX_RULE and _RULE.match(r) and not _CONTROL.search(r)
                for r in allow):
            raise AgentError(400, f"Allow rules are at most {MAX_RULES}, each a tool's name with its pattern, such "
                                  "as Bash(npm test:*).")
        out["allow"] = json.dumps(sorted(set(allow)))
    if not partial or "limits" in given:
        limits = given.get("limits") or {}
        if not isinstance(limits, dict) or set(limits) - set(LIMITS):
            raise AgentError(400, "An agent's limits are " + ", ".join(LIMITS) + ".")
        kept = {}
        for key, (default, low, high) in LIMITS.items():
            value = limits.get(key, default)
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise AgentError(400, f"{key} is a whole number from {low} to {high}.")
            kept[key] = value
        out["limits"] = json.dumps(kept, sort_keys=True)
    if not partial or "mcp_servers" in given:
        # checked as the workspace checks them: one check, shapes.py, kept on both sides
        try:
            out["mcp_servers"] = shapes.check_mcp({"servers": given.get("mcp_servers") or []})["servers"]
        except shapes.ShapeError as e:
            raise AgentError(400, str(e)) from None
    if not partial or "connectors" in given:
        out["connectors"] = given.get("connectors") is True
    if not partial or "unattended_mode" in given:
        mode = given.get("unattended_mode") or "dontAsk"
        if mode not in UNATTENDED:
            raise AgentError(400, "A run nobody watches is dontAsk, auto or bypassPermissions.")
        out["unattended_mode"] = mode
    if not partial or "precheck" in given:
        out["precheck"] = _text(given.get("precheck"), MAX_PRECHECK, "A precheck")
    if not partial or "autocompact_window" in given:
        window = given.get("autocompact_window")
        if window is not None and (not isinstance(window, int) or isinstance(window, bool)
                                   or not 100_000 <= window <= 1_000_000):
            raise AgentError(400, "A run's compaction point is from 100,000 to 1,000,000 tokens.")
        out["autocompact_window"] = window
    if not partial or "max_compactions" in given:
        most = given.get("max_compactions", 2)
        if not isinstance(most, int) or isinstance(most, bool) or not 0 <= most <= 20:
            raise AgentError(400, "A run's most compactions are from 0 to 20.")
        out["max_compactions"] = most
    for key in ("may_dispatch", "accepts_dispatch"):
        if not partial or key in given:
            out[key] = 1 if given.get(key) is True else 0
    if not partial or "relations" in given:
        try:
            out["relations"] = json.dumps(facts.check_relations(given.get("relations")), sort_keys=True)
        except facts.FactError as e:
            raise AgentError(400, str(e)) from None
    for key in ("anomaly_pause", "judge_runs", "improve"):
        if not partial or key in given:
            out[key] = 1 if given.get(key) is True else 0
    if not partial or "acceptance" in given:
        try:
            spec = shapes.check_acceptance(given.get("acceptance"))
        except shapes.ShapeError as e:
            raise AgentError(400, str(e)) from None
        out["acceptance"] = json.dumps(spec) if spec else None
    if not partial or "reverify" in given:
        out["reverify"] = 1 if given.get("reverify") is True else 0
    if not partial or "decay_runs" in given:
        decay = given.get("decay_runs", 30)
        if not isinstance(decay, int) or isinstance(decay, bool) or not 0 <= decay <= 1000:
            raise AgentError(400, "A memory file is archived after from 1 to 1000 runs unused, or never (0).")
        out["decay_runs"] = decay
    if not partial or "max_dispatch_per_run" in given:
        width = given.get("max_dispatch_per_run", DISPATCH_WIDTH)
        if not isinstance(width, int) or isinstance(width, bool) or not 1 <= width <= 20:
            raise AgentError(400, "The runs one run may start are from 1 to 20.")
        out["max_dispatch_per_run"] = width
    for key, what in (("max_budget_usd", "A run's budget"), ("est_usd_per_day", "A day's estimated cost")):
        if not partial or key in given:
            value = given.get(key)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                      or not 0 < value <= 1000):
                raise AgentError(400, f"{what} is a number of US dollars, more than 0 and at most 1000.")
            out[key] = float(value) if value is not None else None
    return out


def _paused(row, now=None):
    """Whether the agent is paused now: by the owner, or until a time (its day's estimated cost passed its limit)."""
    now = time.time() if now is None else now
    return bool(row["paused"]) and not (row["paused_until"] and now >= row["paused_until"])


NO_MCP = {"connectors": False, "servers": [], "accepted": {}, "held": []}


def _mcp(row):
    got = _json(row["mcp"], {}) if row is not None else {}
    return dict(NO_MCP, **{k: got[k] for k in NO_MCP if k in got})


def _mcp_fields(fields, row=None):
    """The agent's MCP record after a change from the page: its servers and whether it uses the connectors as given;
    what the owner accepted of a server's tools, and its hold, kept for a server whose definition did not change."""
    if "mcp_servers" not in fields and "connectors" not in fields:
        return
    mcp = _mcp(row)
    if "mcp_servers" in fields:
        before = {s["name"]: s for s in mcp["servers"]}
        servers = fields.pop("mcp_servers")
        same = {s["name"] for s in servers if before.get(s["name"]) == s}
        mcp.update(servers=servers, accepted={k: v for k, v in mcp["accepted"].items() if k in same},
                   held=[n for n in mcp["held"] if n in same])
    if "connectors" in fields:
        mcp["connectors"] = fields.pop("connectors")
    fields["mcp"] = json.dumps(mcp, sort_keys=True)


# ---- the records ---------------------------------------------------------------------------------------------------

def _row(db, user_id, agent_id):
    if not isinstance(agent_id, str) or not _ID.match(agent_id):
        raise AgentError(404, "No such agent.")
    row = db.execute("SELECT * FROM ade_agents WHERE id = ? AND user_id = ?", (agent_id, user_id)).fetchone()
    if row is None:
        raise AgentError(404, "No such agent.")
    return row


def _run_row(db, user_id, run_id):
    if not isinstance(run_id, str) or not _ID.match(run_id):
        raise AgentError(404, "No such run.")
    row = db.execute("SELECT * FROM ade_agent_runs WHERE run_id = ? AND user_id = ?", (run_id, user_id)).fetchone()
    if row is None:
        raise AgentError(404, "No such run.")
    return row


def run_branch(user_id, run_id):
    """The branch a run of the account's own works on, as its agent is kept here (its kept worktree's, or the run's
    own); None when `run_id` is no run of theirs."""
    if not isinstance(run_id, str) or not _ID.match(run_id):
        return None
    with store.connect() as db:
        row = db.execute("SELECT r.agent_id, a.keep_worktree FROM ade_agent_runs r JOIN ade_agents a ON a.id = "
                         "r.agent_id AND a.user_id = r.user_id WHERE r.run_id = ? AND r.user_id = ?",
                         (run_id, user_id)).fetchone()
    if row is None:
        return None
    return f"pp/agent-{row['agent_id']}" if row["keep_worktree"] else f"pp/run-{run_id}"


def _json(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def run_view(row, subtree=None):
    return {"run_id": row["run_id"], "agent_id": row["agent_id"], "state": row["state"], "reason": row["reason"],
            "cause": row["cause"], "trigger": _json(row["trigger"], {}), "queued_at": row["queued_at"],
            "started_at": row["started_at"], "ended_at": row["ended_at"], "base": row["base"], "head": row["head"],
            "branch": row["branch"], "brief_version": row["brief_version"], "question": row["question"],
            "steps": row["steps"], "requests": row["requests"], "cost_est_usd": row["cost_est_usd"],
            "tokens": _json(row["tokens"], {}) or None, "last_text": row["last_text"], "bytes": row["bytes"],
            "pruned": bool(row["pruned"]), "thumbs": row["thumbs"], "thumbs_note": row["thumbs_note"],
            "sent": bool(row["sent"]), "context": _json(row["context"], {}) or None,
            "parent_run_id": row["parent_run_id"], "depth": row["depth"], "dispatches": row["dispatches"],
            "waiting_on": row["waiting_on"], "subtree": subtree, "metrics": _json(row["metrics"], {}) or None,
            "judge": _json(row["judge"], {}) or None, "anomalies": _json(row["anomalies"], []) or None,
            "acceptance": _json(row["acceptance"], {}) or None, "replay_of": row["replay_of"], "flag": row["flag"],
            "rewritten": row["rewritten"], "judge_error": row["judge_error"]}


def _agent_state(row, runs):
    if row["retired_at"]:
        return "retired"
    if any(r["state"] == "waiting" for r in runs):
        return "waiting"
    if any(r["state"] == "working" for r in runs):
        return "working"
    if _paused(row):
        return "paused"
    return "idle"


def agent_view(row, totals=None, runs=(), offered=(), triggers=(), subtrees=None, numbers=None, fresh=None,
               skills=None):
    runs = list(runs)
    t = totals
    return {"id": row["id"], "name": row["name"], "group": row["grp"], "brief": row["brief"], "repo": row["repo"],
            "base_ref": row["base_ref"], "keep_worktree": bool(row["keep_worktree"]), "account": row["account"],
            "chat_settings": _json(row["chat_settings"], {}), "allow": _json(row["allow"], []),
            "limits": dict({k: v[0] for k, v in LIMITS.items()}, **_json(row["limits"], {})),
            "brief_version": row["brief_version"], "paused": _paused(row),
            "paused_reason": row["paused_reason"] if _paused(row) else None, "retired": bool(row["retired_at"]),
            "paused_until": row["paused_until"] if _paused(row) else None,
            "unattended_mode": row["unattended_mode"], "precheck": row["precheck"],
            "max_budget_usd": row["max_budget_usd"], "est_usd_per_day": row["est_usd_per_day"],
            "autocompact_window": row["autocompact_window"], "max_compactions": row["max_compactions"],
            "may_dispatch": bool(row["may_dispatch"]), "accepts_dispatch": bool(row["accepts_dispatch"]),
            "max_dispatch_per_run": row["max_dispatch_per_run"], "relations": _json(row["relations"], {}),
            "decay_runs": row["decay_runs"], "reverify": bool(row["reverify"]), "freshness": fresh,
            "anomaly_pause": bool(row["anomaly_pause"]), "acceptance": _json(row["acceptance"], {}) or None,
            "judge_runs": bool(row["judge_runs"]), "improve": bool(row["improve"]), "reviewed_at": row["reviewed_at"],
            "review_skipped": _json(row["review_skipped"], {}) or None,
            "skills": [{"name": n, "content": c} for n, c in sorted((skills or {}).items())],
            "from_session": row["from_session"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "state": _agent_state(row, runs), "mcp": _mcp(row), "tools": _json(row["mcp_state"], {}),
            "proposals": list(offered), "triggers": list(triggers), "numbers": numbers,
            "totals": {"runs": t["runs"] if t else 0, "done": t["done"] if t else 0, "failed": t["failed"] if t else 0,
                       "blocked": t["blocked"] if t else 0, "seconds": t["seconds"] if t else 0,
                       "tokens": t["tokens"] if t else 0, "cost_est_usd": round(t["cost_est_usd"], 6) if t else 0.0,
                       "last_run_at": t["last_run_at"] if t else None, "last_state": t["last_state"] if t else None,
                       "runs_today": t["runs_today"] if t and t["today"] == _today() else 0,
                       "est_usd_today": round(t["est_usd_today"], 6) if t and t["today"] == _today() else 0.0,
                       "success_rate": (round(t["done"] / (t["done"] + t["failed"]), 4)
                                        if t and (t["done"] + t["failed"]) else None)},
            "runs": [run_view(r, (subtrees or {}).get(r["run_id"])) for r in runs]}


def _today(now=None):
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))


def _mirror_body(row, user, median_usd=None, skills=None):
    """The agent as the workspace keeps it (proofpane_workspace/agents.check_agent), with its median run's est. cost
    over 7 days, which the live rules weigh a run's spend against."""
    settings = _json(row["chat_settings"], {})
    settings["allow"] = _json(row["allow"], [])
    email = user.get("email") or ""
    return {"id": row["id"], "name": row["name"], "group": row["grp"], "brief": row["brief"], "repo": row["repo"],
            "base_ref": row["base_ref"], "keep_worktree": bool(row["keep_worktree"]), "account": row["account"],
            "chat_settings": settings, "limits": _json(row["limits"], {}), "paused": _paused(row),
            "retired": bool(row["retired_at"]), "brief_version": row["brief_version"], "mcp": _mcp(row),
            "unattended_mode": row["unattended_mode"], "precheck": row["precheck"],
            "max_budget_usd": row["max_budget_usd"], "autocompact_window": row["autocompact_window"],
            "max_compactions": row["max_compactions"], "may_dispatch": bool(row["may_dispatch"]),
            "accepts_dispatch": bool(row["accepts_dispatch"]), "max_dispatch_per_run": row["max_dispatch_per_run"],
            "decay_runs": row["decay_runs"], "anomaly_pause": bool(row["anomaly_pause"]),
            "acceptance": _json(row["acceptance"], {}) or None, "median_usd_7d": median_usd, "skills": skills or {},
            "author": {"name": email.split("@")[0][:100] or "Proofpane agent", "email": email}}


def create(user, given, from_session=None):
    fields = check(given)
    _mcp_fields(fields)
    now = int(time.time())
    agent_id = secrets.token_hex(6)
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, grp, brief, repo, base_ref, keep_worktree, account, "
                   "chat_settings, allow, limits, brief_version, paused, from_session, created_at, updated_at, mcp, "
                   "unattended_mode, precheck, max_budget_usd, est_usd_per_day, autocompact_window, max_compactions, "
                   "may_dispatch, accepts_dispatch, max_dispatch_per_run, relations, decay_runs, reverify, anomaly_pause, "
                   "acceptance, judge_runs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (agent_id, user["id"], fields["name"], fields["grp"], fields["brief"], fields["repo"],
                    fields["base_ref"], fields["keep_worktree"], fields["account"], fields["chat_settings"],
                    fields["allow"], fields["limits"], from_session, now, now, fields["mcp"],
                    fields["unattended_mode"], fields["precheck"], fields["max_budget_usd"], fields["est_usd_per_day"],
                    fields["autocompact_window"], fields["max_compactions"], fields["may_dispatch"],
                    fields["accepts_dispatch"], fields["max_dispatch_per_run"], fields["relations"],
                    fields["decay_runs"], fields["reverify"], fields["anomaly_pause"], fields["acceptance"], fields["judge_runs"]))
        db.execute("UPDATE ade_agents SET improve = ? WHERE id = ?", (fields["improve"], agent_id))
        improve.version(db, agent_id, user["id"], "brief", fields["brief"], "owner", now)
    _deliver_soon(user)
    return one(user, agent_id)


def update(user, agent_id, given):
    fields = check(given, partial=True)
    if not fields:
        raise AgentError(400, "Say what to change.")
    with store.connect() as db:
        row = _row(db, user["id"], agent_id)
        if row["retired_at"]:
            raise AgentError(409, "This agent is retired.")
        _mcp_fields(fields, row)
        if "brief" in fields and fields["brief"] != row["brief"]:
            fields["brief_version"] = row["brief_version"] + 1
            improve.version(db, agent_id, user["id"], "brief", fields["brief"], "owner", int(time.time()))
        fields["updated_at"] = int(time.time())
        db.execute("UPDATE ade_agents SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE id = ? AND user_id = ?",
                   (*fields.values(), agent_id, user["id"]))
    _deliver_soon(user)
    return one(user, agent_id)


def set_paused(user, agent_id, paused, reason=None):
    with store.connect() as db:
        row = _row(db, user["id"], agent_id)
        if row["retired_at"]:
            raise AgentError(409, "This agent is retired.")
        db.execute("UPDATE ade_agents SET paused = ?, paused_reason = ?, paused_until = NULL, updated_at = ? WHERE id = ?",
                   (1 if paused else 0, (reason or "paused by you") if paused else None, int(time.time()), agent_id))
    _deliver_soon(user)
    return one(user, agent_id)


def retire(user, agent_id):
    """Retired: its triggers off, its queued runs stopped, its worktree removed at the workspace (now, or at the
    machine's next start); its records stay."""
    now = int(time.time())
    with store.connect() as db:
        row = _row(db, user["id"], agent_id)
        if row["retired_at"]:
            return one(user, agent_id)
        db.execute("UPDATE ade_agents SET retired_at = ?, updated_at = ? WHERE id = ?", (now, now, agent_id))
        db.execute("UPDATE ade_agent_runs SET state = 'stopped', reason = 'the agent was retired', ended_at = ?, "
                   "updated_at = ? WHERE agent_id = ? AND state = 'queued' AND sent = 0", (now, now, agent_id))
        # its schedules fire no more, and its hooks answer as though they were gone; each turned off is a version of
        # its trigger's, as any change of one is (triggers.record)
        turned = db.execute("SELECT * FROM ade_agent_triggers WHERE agent_id = ? AND enabled = 1",
                            (agent_id,)).fetchall()
        db.execute("UPDATE ade_agent_triggers SET enabled = 0, updated_at = ? WHERE agent_id = ?", (now, agent_id))
        from . import triggers
        for t in turned:
            triggers.record(db, dict(t, enabled=0), now)
    _deliver_soon(user)
    return one(user, agent_id)


def limits_of(row):
    return dict({k: v[0] for k, v in LIMITS.items()}, **_json(row["limits"], {}))


def queue_full(db, row):
    """Why a run of the agent `row` would be refused for its queue now (new_run), or None: also what the calendar says
    would hold a firing (triggers.calendar)."""
    queued = db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE agent_id = ? AND state = 'queued' AND loop = 0",
                        (row["id"],)).fetchone()[0]
    most = limits_of(row)["queue_max"]
    return f"This agent has {queued} runs queued already, its most." if queued >= most else None


def new_run(db, row, trigger, now, payload=None, loop=False):
    """A run of the agent `row`, queued in the store with the app's own id, to be sent when its machine runs: by
    hand, a schedule or a hook (whose delivery, `payload`, is kept apart until it is sent). Refused past its queue;
    the improve loop's runs (`loop`) are bounded by its own budget, never the agent's queue."""
    full = None if loop else queue_full(db, row)
    if full:
        raise AgentError(409, full)
    run_id = secrets.token_hex(6)
    db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, trigger, queued_at, brief_version, "
               "loop, updated_at) VALUES (?,?,?,'queued',?,?,?,?,?)",
               (run_id, row["id"], row["user_id"], json.dumps(trigger), now, row["brief_version"], 1 if loop else 0,
                now))
    if payload is not None:
        db.execute("INSERT INTO ade_agent_payloads (run_id, user_id, payload) VALUES (?,?,?)",
                   (run_id, row["user_id"], payload))
    return run_id


def run_now(user, agent_id, prompt=None):
    """Queue a run by hand: sent at once when the machine runs, else the machine is started for it."""
    prompt = _text(prompt, MAX_PROMPT, "A run's prompt")
    now = int(time.time())
    with store.connect() as db:
        row = _row(db, user["id"], agent_id)
        if row["retired_at"]:
            raise AgentError(409, "This agent is retired.")
        if _paused(row, now):
            raise AgentError(409, "This agent is paused: resume it first.")
        run_id = new_run(db, row, {"kind": "manual", "prompt": prompt, "at": now}, now)
    starting = _deliver_soon(user, start=True)
    with store.connect() as db:
        return dict(run_view(_run_row(db, user["id"], run_id)), starting=starting)


def stop_run(user, run_id):
    stopped = None
    with store.connect() as db:
        row = _run_row(db, user["id"], run_id)
        if row["state"] not in ("queued",) + LIVE:
            raise AgentError(409, "This run has ended already.")
        if not row["sent"]:
            # never sent: stopped here, unless _send sent it just now (it changes the row only while it is queued)
            now = int(time.time())
            if db.execute("UPDATE ade_agent_runs SET state = 'stopped', reason = 'stopped by you', ended_at = ?, "
                          "updated_at = ? WHERE run_id = ? AND state = 'queued' AND sent = 0",
                          (now, now, run_id)).rowcount:
                stopped = run_view(_run_row(db, user["id"], run_id))
            elif _run_row(db, user["id"], run_id)["state"] not in ("queued",) + LIVE:
                raise AgentError(409, "This run has ended already.")
    if stopped is not None:
        if row["task_id"]:
            from . import tasks
            tasks.settle(user["id"])            # a task's attempt stopped before it was sent: its end decides
        return stopped
    endpoint = _endpoint(user)
    if endpoint is None:
        raise AgentError(409, "The workspace is not running: start it first.")
    got = client.call(endpoint, "POST", f"/agents/runs/{run_id}/stop", {}, timeout=60)
    _apply(user["id"], [got], record_seq=False)
    with store.connect() as db:
        return run_view(_run_row(db, user["id"], run_id))


def thumbs(user, run_id, value, note=None):
    """The owner's label on a run: up, down, or none."""
    if value not in ("up", "down", None):
        raise AgentError(400, "A run's thumbs are up, down or none.")
    note = _text(note, MAX_NOTE, "A note")
    with store.connect() as db:
        _run_row(db, user["id"], run_id)
        db.execute("UPDATE ade_agent_runs SET thumbs = ?, thumbs_note = ? WHERE run_id = ?",
                   (value, note if value else None, run_id))
        return run_view(_run_row(db, user["id"], run_id))


# ---- reading -------------------------------------------------------------------------------------------------------

def listing(user, sync=True):
    """The tree: groups, each agent with its totals and its last runs, and what waits for the owner. One read of the
    store; first a copy of the runs from the workspace when its machine runs and the last copy is stale."""
    if sync:
        _sync_if_stale(user)
    with store.connect() as db:
        agents = db.execute("SELECT * FROM ade_agents WHERE user_id = ? ORDER BY retired_at IS NOT NULL, name, id",
                            (user["id"],)).fetchall()
        totals = {r["agent_id"]: r for r in db.execute("SELECT * FROM ade_agent_totals WHERE user_id = ?",
                                                       (user["id"],))}
        recent = db.execute("SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY queued_at "
                            "DESC, run_id DESC) AS n FROM ade_agent_runs WHERE user_id = ? AND loop = 0) WHERE n <= ? "
                            "ORDER BY queued_at DESC", (user["id"], RUNS_SHOWN)).fetchall()
        live = db.execute("SELECT * FROM ade_agent_runs WHERE user_id = ? AND loop = 0 AND state IN ('working', "
                          "'waiting')", (user["id"],)).fetchall()
        kept = {}
        for r in db.execute("SELECT agent_id, name, content FROM ade_agent_skills WHERE user_id = ?", (user["id"],)):
            kept.setdefault(r["agent_id"], {})[r["name"]] = r["content"]
        synced = db.execute("SELECT polled_at FROM ade_agent_sync WHERE user_id = ?", (user["id"],)).fetchone()
    offered, fires = {}, {}
    for p in proposals(user["id"]):
        offered.setdefault(p["agent_id"], []).append(p)
    from . import triggers
    with store.connect() as db:
        for t in db.execute("SELECT * FROM ade_agent_triggers WHERE user_id = ? ORDER BY created_at", (user["id"],)):
            fires.setdefault(t["agent_id"], []).append(triggers.view(t, skipped=triggers._skipped(db, t["id"])))
    by_agent = {}
    for r in recent:
        by_agent.setdefault(r["agent_id"], []).append(r)
    for r in live:          # a live run older than the five shown still decides its agent's state
        if all(x["run_id"] != r["run_id"] for x in by_agent.get(r["agent_id"], [])):
            by_agent.setdefault(r["agent_id"], []).append(r)
    with store.connect() as db:
        subtrees = _subtrees(db, user["id"], [r["run_id"] for runs in by_agent.values() for r in runs
                                              if r["dispatches"]])
        numbers = agent_metrics.summaries(db, user["id"])
        fresh = {row["id"]: freshness.summary(db, row["id"]) for row in agents if not row["retired_at"]}
    groups = {}
    for row in agents:
        view = agent_view(row, totals.get(row["id"]), by_agent.get(row["id"], ()), offered.get(row["id"], ()),
                          fires.get(row["id"], ()), subtrees, numbers.get(row["id"]), fresh.get(row["id"]),
                          kept.get(row["id"]))
        groups.setdefault("Retired" if view["retired"] else (row["grp"] or GROUP_DEFAULT), []).append(view)
    waiting = [run_view(r) for r in live if r["state"] == "waiting"]
    return {"groups": [{"name": name, "agents": items} for name, items in groups.items()],
            "waiting": waiting, "live": len(live), "polled_at": synced["polled_at"] if synced else None}


def one(user, agent_id, before=None):
    """An agent with its runs, newest first, a page at a time (`before`: a queued_at)."""
    with store.connect() as db:
        row = _row(db, user["id"], agent_id)
        totals = db.execute("SELECT * FROM ade_agent_totals WHERE agent_id = ?", (agent_id,)).fetchone()
        if isinstance(before, int):
            runs = db.execute("SELECT * FROM ade_agent_runs WHERE agent_id = ? AND loop = 0 AND queued_at < ? ORDER BY "
                              "queued_at DESC, run_id DESC LIMIT ?", (agent_id, before, RUNS_PAGE)).fetchall()
        else:
            runs = db.execute("SELECT * FROM ade_agent_runs WHERE agent_id = ? AND loop = 0 ORDER BY queued_at DESC, "
                              "run_id DESC LIMIT ?", (agent_id, RUNS_PAGE)).fetchall()
        kept = improve.skills(db, agent_id)
    from . import triggers
    with store.connect() as db:
        fires = triggers.of_agent(db, user["id"], agent_id)
        subtrees = _subtrees(db, user["id"], [r["run_id"] for r in runs if r["dispatches"]])
        numbers = agent_metrics.summaries(db, user["id"]).get(agent_id)
        fresh = freshness.summary(db, agent_id)
    return agent_view(row, totals, runs, proposals(user["id"], agent_id), fires, subtrees, numbers, fresh, kept)


def numbers(user, agent_id, days=7):
    """An agent's numbers over its last `days` days (agent_metrics.window)."""
    if days not in (7, 30):
        raise AgentError(400, "Numbers are for the last 7 or 30 days.")
    with store.connect() as db:
        _row(db, user["id"], agent_id)
        return agent_metrics.window(db, user["id"], agent_id, days)


def judge_trust(user):
    """The judge's trust per judge version, over the account's labelled runs."""
    with store.connect() as db:
        return quality.trust(db, user["id"])


def numbers_csv(user, days=30):
    if days not in (7, 30, 90):
        raise AgentError(400, "A CSV is of the last 7, 30 or 90 days.")
    with store.connect() as db:
        return agent_metrics.csv_text(db, user["id"], days)


# ---- the workspace -------------------------------------------------------------------------------------------------

def _endpoint(user):
    """The running workspace's address, or None: never starts it."""
    from .workspace_routes import _starting
    try:
        p = workspaces.for_user(user)
    except workspaces.WorkspaceAccess:
        return None
    if _starting(user["id"]):
        return None
    return p.endpoint(user)


def _deliver_soon(user, start=False):
    """Mirror and send what is waiting, in a thread of its own: at once when the machine runs, after starting it
    when `start` (a run asked for by hand). Whether a start was begun."""
    try:
        p = workspaces.for_user(user)
    except workspaces.WorkspaceAccess:
        return False
    running = p.endpoint(user) is not None
    if not running and not start:
        return False
    threading.Thread(target=_deliver, args=(user, start and not running), name="agents-deliver", daemon=True).start()
    return start and not running


def _deliver(user, start):
    from .workspace_routes import _run_start, _starts, _starts_lock
    try:
        if start:
            p = workspaces.for_user(user)
            with _starts_lock:
                job = _starts.get(user["id"])
                if job is None or job["done"]:
                    job = {"done": False, "error": None}
                    job["thread"] = threading.Thread(target=_run_start, args=(p, user, job), name="workspace-start",
                                                     daemon=True)
                    _starts[user["id"]] = job
                    job["thread"].start()
            job["thread"].join()
            if job["error"] is not None:
                _fail_unsent(user, f"The workspace could not start: {job['error']}")
                return
        sync(user, background=True)
    except Exception as e:  # noqa: BLE001  the next poll tries again
        print(f"agents: delivering for {user['id']} failed: {type(e).__name__}: {e}", file=sys.stderr)


def _fail_unsent(user, reason):
    from . import tasks
    now = int(time.time())
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET state = 'blocked', cause = 'start', reason = ?, ended_at = ?, "
                   "updated_at = ? WHERE user_id = ? AND state = 'queued' AND sent = 0",
                   (reason[:300], now, now, user["id"]))
    tasks.settle(user["id"], now)       # a task's attempt among them: its end decides, and frees the lease


def _sync_if_stale(user):
    with store.connect() as db:
        synced = db.execute("SELECT polled_at FROM ade_agent_sync WHERE user_id = ?", (user["id"],)).fetchone()
    if synced and synced["polled_at"] and time.time() - synced["polled_at"] < SYNC_FRESH:
        return
    try:
        sync(user)
    except Exception as e:  # noqa: BLE001  the page shows what the store has
        print(f"agents: a page's sync for {user['id']} failed: {type(e).__name__}: {e}", file=sys.stderr)


def sync(user, background=False):
    """With the machine up: mirror the agents changed since their last mirror, send the runs waiting to be sent, and
    copy back the runs changed since the last change number; in the background (the poller's, a delivery's), ask the
    judge about the runs due one too, which takes a chat each. Nothing when the machine is down. One at a time per
    account."""
    endpoint = _endpoint(user)
    if endpoint is None:
        return False
    with _lock:
        if user["id"] in _syncing:
            return False
        _syncing.add(user["id"])
    try:
        from . import tasks
        _mirror(user, endpoint)
        _send(user, endpoint)
        _poll(user, endpoint)
        _take_sweep(user, endpoint)
        # a task's attempt sent and not known to the workspace ends failed, freeing its task's lease (tasks.py)
        tasks.check_attempts(user, endpoint)
        _send(user, endpoint)           # what the poll and the sweep queued (an eval's next questions), sent now
        if background:
            _slow_work(user, endpoint)  # the judge's and the loop's chats, in a thread of the account's own
        return True
    finally:
        with _lock:
            _syncing.discard(user["id"])


def sync_controller(user, endpoint=None):
    """The controller's sync (controller.sync), with the machine up, its failure said on standard error: what the poll
    does, before the agents' own sync, for each owner whose controller it syncs (controller.users_to_poll); never a
    page's read, which a record read and a mirror sent would hold up. Whether the mirror was sent."""
    from . import controller
    endpoint = endpoint or _endpoint(user)
    if endpoint is None:
        return False
    try:
        return controller.sync(user, endpoint)
    except Exception as e:  # noqa: BLE001
        print(f"agents: the controller's sync failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def _mirror(user, endpoint):
    """Send the workspace the agents changed since their last mirror, one mirror per account at a time: the acceptance
    of a teammate's task mirrors outside a sync (task_offers.accept), and two at once could each send a copy of an
    agent, the older reaching the workspace last while the app counted the newer as there. First of all, the app's
    word on the workspace's record of skill proposals (skill_improve.send_word), which every start the workspace makes
    unattended waits for after its own start, and which never raises into this call."""
    from . import skill_improve
    skill_improve.send_word(user, endpoint)
    with _lock:
        held = _mirroring.setdefault(user["id"], threading.Lock())
    with held:
        _mirror_now(user, endpoint)


def _mirror_now(user, endpoint):
    with store.connect() as db:
        # changed since (a change's time is always past the mirror before it: the store's ade_agents_changed), or not
        # sent today: its median run's cost moves with the days
        today = int(time.time()) - int(time.time()) % 86400
        rows = db.execute("SELECT * FROM ade_agents WHERE user_id = ? AND (mirrored_at IS NULL OR mirrored_at < "
                          "updated_at OR mirrored_at < ? OR hold_unmirrored > 0)", (user["id"], today)).fetchall()
    for row in rows:
        with store.connect() as db:
            median = _median_cost(db, row["id"])
            kept = improve.skills(db, row["id"])
        body = _mirror_body(row, user, median, kept)
        try:
            if row["hold_unmirrored"] and not body["paused"]:
                # a hold copied here that the owner resumed before any mirror said paused: the workspace keeps a held
                # agent paused until a mirror says paused and then one says not, so a resume sent alone would read
                # there as a mirror sent before the app knew of the hold, and every resume after it too
                # (proofpane_workspace/agents.put): paused first, then the agent as it is
                client.call(endpoint, "PUT", f"/agents/{row['id']}", dict(body, paused=True), timeout=60)
            client.call(endpoint, "PUT", f"/agents/{row['id']}", body, timeout=60)
            if row["retired_at"]:
                client.call(endpoint, "POST", f"/agents/{row['id']}/retire", {}, timeout=120)
        except client.WorkspaceCallError as e:
            print(f"agents: mirroring {row['id']} failed: {e}", file=sys.stderr)
            continue
        with store.connect() as db:
            db.execute("UPDATE ade_agents SET mirrored_at = ? WHERE id = ? AND updated_at = ?",
                       (max(int(time.time()), row["updated_at"]), row["id"], row["updated_at"]))
            if row["hold_unmirrored"]:
                # told; a hold copied while this was sent counts on, for the next mirror
                db.execute("UPDATE ade_agents SET hold_unmirrored = 0 WHERE id = ? AND hold_unmirrored = ?",
                           (row["id"], row["hold_unmirrored"]))
    _mirror_facts(user, endpoint)       # by the version each copy holds there: a new volume's none are sent


def _median_cost(db, agent_id, now=None):
    """The median est. cost of the agent's work runs that ended in the last 7 days, or None: never its eval, replay or
    re-verify runs (quality.IMPROVING), which measure or keep it up and cost what they cost."""
    now = int(now if now is not None else time.time())
    costs = sorted(r["cost_est_usd"] for r in db.execute(
        "SELECT cost_est_usd, trigger FROM ade_agent_runs WHERE agent_id = ? AND loop = 0 AND ended_at >= ? AND "
        "cost_est_usd IS NOT NULL", (agent_id, now - 7 * 86400)) if _json(r["trigger"], {}).get("kind")
        not in quality.IMPROVING)
    if not costs:
        return None
    mid = len(costs) // 2
    return round(costs[mid] if len(costs) % 2 else (costs[mid - 1] + costs[mid]) / 2, 6) or None


def _mirror_facts(user, endpoint):
    """Each agent's current facts (facts.copy_for), sent when the workspace's copy holds another version of the ledger:
    changed since, never sent to this machine, or found changed there (the workspace then says it holds none): what
    its runs' lookup_fact reads. The facts themselves are never read back: they come in only from runs' records."""
    try:
        held = client.call(endpoint, "GET", "/agents/facts-versions", timeout=30)
    except client.WorkspaceCallError:
        return
    held = held if isinstance(held, dict) else {}
    with store.connect() as db:
        rows = db.execute("SELECT id, facts_version FROM ade_agents WHERE user_id = ? AND retired_at IS NULL",
                          (user["id"],)).fetchall()
        copies = {r["id"]: (r["facts_version"], facts.copy_for(db, r["id"])) for r in rows
                  if r["id"] in held and held[r["id"]] != r["facts_version"]}
    for agent_id, (version, copy) in copies.items():
        try:
            client.call(endpoint, "PUT", f"/agents/{agent_id}/facts", {"facts": copy, "version": version}, timeout=60)
        except client.WorkspaceCallError as e:
            print(f"agents: sending {agent_id}'s facts failed: {e}", file=sys.stderr)
            continue


def facts_of(user, agent_id):
    """An agent's facts: the current ones, those that contradict each other, and the retired ones."""
    with store.connect() as db:
        _row(db, user["id"], agent_id)
        every = facts.of_agent(db, user["id"], agent_id)
    current = [f for f in every if f["expired_at"] is None]
    return {"current": current, "contradictions": [f for f in current if f["contradicts"]],
            "retired": [f for f in every if f["expired_at"] is not None][:200]}


def _send(user, endpoint):
    """Send the account's queued runs, one sender at a time (a sync's and the slow thread's take turns)."""
    with _lock:
        held = _sending.setdefault(user["id"], threading.Lock())
    with held:
        _send_now(user, endpoint)


def _send_now(user, endpoint):
    with store.connect() as db:
        rows = db.execute("SELECT r.*, a.mirrored_at, a.updated_at AS agent_updated, a.paused AS agent_paused, "
                          "a.paused_until AS agent_paused_until FROM ade_agent_runs r JOIN ade_agents a ON a.id = "
                          "r.agent_id WHERE r.user_id = ? AND r.state = 'queued' AND r.sent = 0 ORDER BY r.queued_at",
                          (user["id"],)).fetchall()
    for row in rows:
        if row["mirrored_at"] is None or row["mirrored_at"] < row["agent_updated"]:
            continue                    # its agent as it is now is not there yet
        if row["agent_paused"] and (row["agent_paused_until"] is None or row["agent_paused_until"] > time.time()):
            continue                    # it waits for its agent's resume, queued, never failed for it
        trigger = _json(row["trigger"], {})
        sent = {"kind": trigger.get("kind", "manual"), "prompt": trigger.get("prompt"), "id": trigger.get("id")}
        task_part = None
        if row["task_id"]:
            # a task's attempt carries its task's fields, and is sent only while it
            # holds its task's lease at the task's fence: a superseded one is never sent
            from . import tasks
            with store.connect() as db:
                task_part = tasks.attempt_message(db, user["id"], row)
                if task_part is None:
                    now = int(time.time())
                    db.execute("UPDATE ade_agent_runs SET state = 'failed', cause = 'start', reason = ?, ended_at = ?, "
                               "updated_at = ? WHERE run_id = ? AND state = 'queued' AND sent = 0",
                               ("superseded: its task's lease is another attempt's", now, now, row["run_id"]))
            if task_part is None:
                continue
            sent = {"kind": "task", "prompt": None, "id": row["task_id"]}
        if sent["kind"] in ("replay", "sandbox"):
            sent["of"] = trigger.get("of")
        if sent["kind"] == "reverify":
            sent["reverify"] = trigger.get("reverify")
        if sent["kind"] == "replay":
            # the memory the run it replays started with: its agent's last version before it
            with store.connect() as db:
                of = db.execute("SELECT started_at, queued_at FROM ade_agent_runs WHERE run_id = ? AND user_id = ?",
                                (trigger.get("of"), user["id"])).fetchone()
                sent["replay"] = {"memory": improve.memory_before(
                    db, row["agent_id"], (of["started_at"] or of["queued_at"] or 0) if of else 0)}
        if sent["kind"] in ("review", "sandbox"):
            # what the improve loop's run carries, made now: the digest and the review prompt, or the arm and the
            # input's base and memory
            with store.connect() as db:
                agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (row["agent_id"],)).fetchone()
                if sent["kind"] == "review":
                    sent["review"] = improve.review_body(db, user["id"], agent)
                    if improve.passed_over_at_send(db, agent, row, sent["review"], int(time.time())):
                        continue        # its digest grew past what the workspace takes since the review was queued
                else:
                    sent.update(arm=trigger.get("arm"), proposal=trigger.get("proposal"),
                                sandbox=improve.sandbox_body(db, row))
                    if sent["sandbox"] is None:
                        now = int(time.time())
                        db.execute("UPDATE ade_agent_runs SET state = 'failed', cause = 'start', reason = ?, "
                                   "ended_at = ?, updated_at = ? WHERE run_id = ?",
                                   ("its proposal or its input run is gone", now, now, row["run_id"]))
                        continue
        if sent["kind"] == "hook":
            with store.connect() as db:
                kept = db.execute("SELECT payload FROM ade_agent_payloads WHERE run_id = ?", (row["run_id"],)).fetchone()
            sent["payload_b64"] = base64.b64encode(bytes(kept["payload"]) if kept else b"").decode()
        body = {"run_id": row["run_id"], "trigger": sent}
        if task_part is not None:
            body["task"] = task_part
        try:
            got = client.call(endpoint, "POST", f"/agents/{row['agent_id']}/runs", body, timeout=60)
        except client.WorkspaceCallError as e:
            if e.status in (400, 404):
                now = int(time.time())
                with store.connect() as db:
                    db.execute("UPDATE ade_agent_runs SET state = 'failed', cause = 'start', reason = ?, ended_at = ?, "
                               "updated_at = ? WHERE run_id = ?", (str(e)[:300], now, now, row["run_id"]))
                if task_part is not None:
                    from . import tasks
                    tasks.settle(user["id"], now)       # the attempt never started: its task's lease is freed
            continue
        with store.connect() as db:
            # sent, as long as the owner has not stopped it meanwhile: stop_run's local stop, and it alone, leaves a
            # run stopped and unsent (a copy of the workspace's record sets sent too), and each changes the row only
            # as the other left it, so one of them wins and the other knows
            still = db.execute("UPDATE ade_agent_runs SET sent = 1 WHERE run_id = ? AND NOT (state = 'stopped' AND "
                               "sent = 0)", (row["run_id"],)).rowcount
            if not still:
                db.execute("UPDATE ade_agent_runs SET sent = 1 WHERE run_id = ?", (row["run_id"],))     # it is there
            db.execute("DELETE FROM ade_agent_payloads WHERE run_id = ?", (row["run_id"],))
        if not still:
            # the owner stopped it while it was being sent: stopped there too, or the workspace would start it and its
            # record would bring it back as running (a stop that cannot be asked now leaves it as the workspace has
            # it, for the owner to stop again)
            try:
                got = client.call(endpoint, "POST", f"/agents/runs/{row['run_id']}/stop", {}, timeout=60)
            except client.WorkspaceCallError:
                continue
        _apply(user["id"], [got], record_seq=False)


def _poll(user, endpoint):
    """Copy back the runs changed since the last change number, a page at a time (at most PAGES pages a poll: the
    next poll goes on from where this one stopped)."""
    with store.connect() as db:
        synced = db.execute("SELECT seq FROM ade_agent_sync WHERE user_id = ?", (user["id"],)).fetchone()
    since = synced["seq"] if synced else 0
    for _ in range(PAGES):
        got = client.call(endpoint, "GET", f"/agents/runs?since={since}", timeout=60)
        if not isinstance(got, dict):
            return
        seq = got.get("seq") if isinstance(got.get("seq"), int) and not isinstance(got.get("seq"), bool) else since
        if seq < since:
            # the workspace counts from less than we had: its volume is new. Read it all again
            since = 0
            continue
        runs = got.get("runs") if isinstance(got.get("runs"), list) else []
        _apply(user["id"], runs[:PAGE_MOST], record_seq=False)
        _copy_trees(user, endpoint)
        _copy_memory(user, endpoint)
        since = seq
        with store.connect() as db:
            db.execute("INSERT INTO ade_agent_sync (user_id, seq, polled_at) VALUES (?, ?, ?) ON CONFLICT(user_id) "
                       "DO UPDATE SET seq = excluded.seq, polled_at = excluded.polled_at",
                       (user["id"], since, int(time.time())))
        if got.get("more") is not True:
            return


TREES_A_POLL = 10


def _copy_trees(user, endpoint):
    """The kept trees of ended runs not yet copied (a few a poll): the app keeps them past the run's folder."""
    with store.connect() as db:
        rows = db.execute("SELECT run_id FROM ade_agent_runs r WHERE user_id = ? AND state IN ('done', 'failed', "
                          "'blocked', 'stopped') AND started_at IS NOT NULL AND pruned = 0 AND NOT EXISTS (SELECT 1 FROM "
                          "ade_agent_trees t WHERE t.run_id = r.run_id) ORDER BY ended_at DESC LIMIT ?",
                          (user["id"], TREES_A_POLL)).fetchall()
    for row in rows:
        try:
            got = client.call(endpoint, "GET", f"/agents/runs/{row['run_id']}/tree", timeout=30)
        except client.WorkspaceCallError:
            continue
        tree = clean_tree(got.get("tree")) if isinstance(got, dict) and got.get("copy") is True else None
        text = json.dumps(tree) if isinstance(tree, dict) else None
        if text is None or len(text) > 64 * 1024:
            continue
        with store.connect() as db:
            db.execute("INSERT OR IGNORE INTO ade_agent_trees (run_id, user_id, tree, copied_at) VALUES (?,?,?,?)",
                       (row["run_id"], user["id"], text, int(time.time())))


_slow = {}                      # account id -> its thread for the judge's and the improve loop's chats (_slow_work)
JUDGE_TIMEOUT = 120             # a judge's one turn, with no tool


def _slow_work(user, endpoint):
    """The judge's and the improve loop's chats for the account, each a call that can take minutes: in a thread of the
    account's own, one round at a time, never inside the poller's round nor the account's sync, so one account's
    judge never holds another's sync."""
    with _lock:
        running = _slow.get(user["id"])
        if running is not None and running.is_alive():
            return running

        def work():
            from . import skill_improve
            try:
                _judge_due(user, endpoint)
                _improve(user, endpoint)
                _send(user, endpoint)       # the loop's runs it queued (a review, a sandbox), sent now
            except Exception as e:  # noqa: BLE001  the next round tries again
                print(f"agents: the judge or the improve loop of {user['id']} failed: {type(e).__name__}: {e}",
                      file=sys.stderr)
            try:
                skill_improve.step(user, endpoint)     # the business skills' loop, after the agents'
            except Exception as e:  # noqa: BLE001  the next round tries again
                print(f"agents: the business skills' loop of {user['id']} failed: {type(e).__name__}: {e}",
                      file=sys.stderr)
        running = _slow[user["id"]] = threading.Thread(target=work, name=f"agents-slow-{user['id']}", daemon=True)
        running.start()
        return running


def settled(user_id, timeout=None):
    """The account's judge and improve loop, their round over (a test waits on it)."""
    running = _slow.get(user_id)
    if running is not None:
        running.join(timeout)


def _judge_due(user, endpoint):
    """The judge asked about a few runs due its verdict (quality.due), each once: its verdict kept on the run, or why
    it gave none."""
    with store.connect() as db:
        due = quality.due(db, user["id"])
        for run_id in due:
            db.execute("UPDATE ade_agent_runs SET judge_asked = 1 WHERE run_id = ?", (run_id,))
    for run_id in due:
        try:
            got = client.call(endpoint, "POST", f"/agents/runs/{run_id}/judge", {}, timeout=JUDGE_TIMEOUT)
        except client.WorkspaceCallError as e:
            print(f"agents: judging {run_id} failed: {e}", file=sys.stderr)
            with store.connect() as db:
                db.execute("UPDATE ade_agent_runs SET judge_error = ? WHERE run_id = ?", (str(e)[:300], run_id))
            continue
        verdict = quality.clean_judge(got)
        with store.connect() as db:
            if verdict:
                db.execute("UPDATE ade_agent_runs SET judge = ?, judge_error = NULL WHERE run_id = ?", (verdict, run_id))
            else:
                db.execute("UPDATE ade_agent_runs SET judge_error = ? WHERE run_id = ?",
                           ("its reply was not a verdict", run_id))


def _improve(user, endpoint):
    """The improve loop's step for the account (improve.py), in the background sync alone: proposals past 14 days
    expired, the ended reviews' changes taken, promotions watched, the critic asked, the sandbox scored or planned,
    and the day's review queued."""
    now = int(time.time())
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM ade_agents WHERE user_id = ? AND improve = 1 UNION SELECT 1 FROM "
                          "ade_improve_proposals WHERE user_id = ? AND state IN ('critic', 'waiting', 'sandbox', "
                          "'approved') LIMIT 1", (user["id"], user["id"])).fetchone():
            return
        improve.expire(db, user["id"], now)
        improve.take(db, user["id"], now)
        improve.watch(db, user["id"], now)
    improve.criticise(store.connect, user, endpoint, client.call, now)
    improve.score_sandbox(store.connect, user, endpoint, client.call, now)
    with store.connect() as db:
        improve.plan_sandbox(db, user, now, new_run)
        improve.maybe_review(db, user, now, new_run)


def improvements(user):
    """The account's improve loop: its proposals, newest first, its week's budget and its review prompt."""
    with store.connect() as db:
        return improve.listing(db, user["id"], int(time.time()))


def decide_improvement(user, proposal_id, approved, reason=None):
    """The owner's tap on a proposal: approved (the change made, and a version of it), or rejected with why. An approval
    the agent's skills refuse (improve.approve) leaves the proposal refused, kept by the store, and is then said as a
    409 whose detail is its reason as a sentence."""
    now = int(time.time())
    try:
        with store.connect() as db:
            got = improve.approve(db, user, proposal_id, now) if approved else \
                improve.reject(db, user, proposal_id, reason, now)
    except improve.ImproveError as e:
        raise AgentError(e.status, str(e)) from None
    if approved and got["state"] == "refused":
        said = got["reason"]
        raise AgentError(409, said[0].upper() + said[1:] + ".")
    if approved:
        _deliver_soon(user)                 # the agent as it is now, mirrored
    return got


def remove_skill(user, agent_id, name):
    """One of the agent's skills taken away by the owner: a version of it with none."""
    now = int(time.time())
    with store.connect() as db:
        row = _row(db, user["id"], agent_id)
        if name not in improve.skills(db, agent_id):
            raise AgentError(404, "No such skill.")
        improve.apply(db, row, "skill", name, None, now)
        improve.version(db, agent_id, user["id"], f"skill:{name}", None, "owner", now)
    _deliver_soon(user)
    return one(user, agent_id)


def replay(user, run_id):
    """A replayable run of the owner's started again as it was, to measure it (the workspace's replay rules): queued
    as the agent's next run."""
    now = int(time.time())
    with store.connect() as db:
        row = _run_row(db, user["id"], run_id)
        if row["state"] not in ENDED:
            raise AgentError(409, "A run is replayed once it has ended.")
        if (_json(row["metrics"], {}) or {}).get("replayable") is not True:
            raise AgentError(409, "This run is not replayable: it reached outside its worktree, or used no tool.")
        agent = _row(db, user["id"], row["agent_id"])
        if agent["retired_at"]:
            raise AgentError(409, "This agent is retired.")
        if row["brief_version"] is not None and row["brief_version"] != agent["brief_version"]:
            raise AgentError(409, f"Its brief changed since this run (version {row['brief_version']}, now "
                                  f"{agent['brief_version']}): a replay would measure the new brief, not this run.")
        new = new_run(db, agent, {"kind": "replay", "of": run_id, "at": now}, now)
        db.execute("UPDATE ade_agent_runs SET replay_of = ? WHERE run_id = ?", (run_id, new))
    starting = _deliver_soon(user, start=True)
    with store.connect() as db:
        return dict(run_view(_run_row(db, user["id"], new)), starting=starting)


def _take_sweep(user, endpoint):
    """The workspace's last freshness sweep, once a day it ran: kept per agent, and a re-verify run queued for each
    agent that has it on and has flagged items, a week since its last."""
    with store.connect() as db:
        synced = db.execute("SELECT swept_day FROM ade_agent_sync WHERE user_id = ?", (user["id"],)).fetchone()
    try:
        got = client.call(endpoint, "GET", "/agents/sweep", timeout=30)
    except client.WorkspaceCallError:
        return
    day = got.get("day") if isinstance(got, dict) else None
    if not isinstance(day, str) or (synced and synced["swept_day"] == day):
        return
    now = int(time.time())
    with store.connect() as db:
        for agent, items in freshness.take(db, user["id"], got, now):
            try:
                new_run(db, agent, {"kind": "reverify", "reverify": {"items": items}, "at": now}, now)
            except AgentError:
                # its queue is full: tried again at the next day's sweep, not a week on
                db.execute("UPDATE ade_agent_freshness SET reverified_at = NULL WHERE agent_id = ?", (agent["id"],))
        db.execute("INSERT INTO ade_agent_sync (user_id, swept_day) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                   "swept_day = excluded.swept_day", (user["id"], day))


def _copy_memory(user, endpoint):
    """The memory snapshots ended runs left, not yet copied (a few a poll), each the agent's next version when the
    folder is not as its latest version had it."""
    with store.connect() as db:
        rows = db.execute("SELECT run_id, agent_id FROM ade_agent_runs WHERE user_id = ? AND memory_digest IS NOT NULL "
                          "AND memory_copied = 0 AND state IN ('done', 'failed', 'blocked', 'stopped') ORDER BY "
                          "ended_at LIMIT ?", (user["id"], TREES_A_POLL)).fetchall()
    for row in rows:
        try:
            got = client.call(endpoint, "GET", f"/agents/runs/{row['run_id']}/memory", timeout=30)
        except client.WorkspaceCallError as e:
            if e.status != 400:
                continue
            got = None                  # its folder was removed: nothing to copy, and nothing to ask again
        with store.connect() as db:
            if got is not None:
                memory_versions.add(db, user["id"], row["agent_id"], row["run_id"], got)
            db.execute("UPDATE ade_agent_runs SET memory_copied = 1 WHERE run_id = ?", (row["run_id"],))


def memory_of(user, agent_id):
    """An agent's memory versions, newest first; and its folder as the workspace has it now, when its machine runs."""
    with store.connect() as db:
        _row(db, user["id"], agent_id)
    live = None
    endpoint = _endpoint(user)
    if endpoint is not None:
        try:
            live = client.call(endpoint, "GET", f"/agents/{agent_id}/memory", timeout=30)
        except client.WorkspaceCallError:
            live = None
    return {"versions": memory_versions.versions(user["id"], agent_id), "now": live}


def memory_version(user, agent_id, version):
    with store.connect() as db:
        _row(db, user["id"], agent_id)
    return memory_versions.one(user["id"], agent_id, version)


def memory_rollback(user, agent_id, version):
    """The agent's memory made a version's files again, at the running workspace: itself the next version."""
    with store.connect() as db:
        _row(db, user["id"], agent_id)
    files = memory_versions.to_restore(user["id"], agent_id, version)
    endpoint = _endpoint(user)
    if endpoint is None:
        raise AgentError(409, "The workspace is not running: start it to roll the memory back.")
    try:
        client.call(endpoint, "PUT", f"/agents/{agent_id}/memory", {"files": files}, timeout=60)
    except client.WorkspaceCallError as e:
        raise AgentError(e.status if e.status in (400, 404, 409) else 502, str(e)) from None
    snapshot = {"files": files, "digest": shapes.memory_digest({p: t.encode("utf-8") for p, t in files.items()})}
    with store.connect() as db:
        made = memory_versions.add(db, user["id"], agent_id, None, snapshot, kind="rollback", from_version=version)
    return {"version": made, "versions": memory_versions.versions(user["id"], agent_id)}


def memory_unarchive(user, agent_id, path):
    endpoint = _endpoint(user)
    if endpoint is None:
        raise AgentError(409, "The workspace is not running: start it to bring a memory file back.")
    with store.connect() as db:
        _row(db, user["id"], agent_id)
    try:
        return client.call(endpoint, "POST", f"/agents/{agent_id}/memory/restore", {"path": path}, timeout=30)
    except client.WorkspaceCallError as e:
        raise AgentError(e.status if e.status in (400, 404, 409) else 502, str(e)) from None


def _str(value, most):
    return value[:most] if isinstance(value, str) else ""


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10 ** 12 else 0


def _tokens(given):
    return {k[:40]: _count(v) for k, v in list(given.items())[:10] if isinstance(k, str)} if isinstance(given, dict) else {}


def _tree_tool(given, depth=0):
    tool = {"id": _str(given.get("id"), 80), "name": _str(given.get("name"), 100), "target": _str(given.get("target"), 120),
            "ms": _count(given.get("ms")) if given.get("ms") is not None else None, "error": given.get("error") is True}
    agent = given.get("agent")
    if isinstance(agent, dict) and depth < 4:
        tool["agent"] = {"type": _str(agent.get("type"), 40), "description": _str(agent.get("description"), 120),
                         "depth": _count(agent.get("depth")) if agent.get("depth") is not None else None,
                         "requests": _count(agent.get("requests")), "tokens": _tokens(agent.get("tokens")),
                         "tools": [_tree_tool(t, depth + 1) for t in (agent.get("tools") or [])[:200]
                                   if isinstance(t, dict)] if isinstance(agent.get("tools"), list) else [],
                         "more": _count(agent.get("more"))}
    return tool


def clean_tree(given):
    """A run's tree held to its shapes (runtree.py's): what a workspace's programs could have written, as the page
    reads it; None when it is no tree."""
    if not isinstance(given, dict) or not isinstance(given.get("turns"), list):
        return None
    turns = []
    for t in given["turns"][:500]:
        if not isinstance(t, dict):
            continue
        at = t.get("at")
        turns.append({"at": at if isinstance(at, (int, float)) and not isinstance(at, bool) else None,
                      "text": _str(t.get("text"), 120), "requests": _count(t.get("requests")),
                      "tokens": _tokens(t.get("tokens")),
                      "tools": [_tree_tool(x) for x in (t.get("tools") or [])[:500] if isinstance(x, dict)]
                      if isinstance(t.get("tools"), list) else [], "more": _count(t.get("more"))})
    return {"turns": turns, "requests": _count(given.get("requests")), "tokens": _tokens(given.get("tokens")),
            "more": _count(given.get("more"))}


def tree(user, run_id):
    """A run's tree: the app's copy, else read from the running workspace; either held to its shapes."""
    with store.connect() as db:
        _run_row(db, user["id"], run_id)
        kept = db.execute("SELECT tree FROM ade_agent_trees WHERE run_id = ?", (run_id,)).fetchone()
    if kept is not None:
        return {"tree": clean_tree(_json(kept["tree"], {})) or {"turns": []}, "copy": True}
    endpoint = _endpoint(user)
    if endpoint is None:
        raise AgentError(409, "This run's tree is kept at its end, and its machine is stopped: start it to read it.")
    try:
        got = client.call(endpoint, "GET", f"/agents/runs/{run_id}/tree", timeout=30)
    except client.WorkspaceCallError as e:
        raise AgentError(e.status if e.status in (400, 404) else 502, str(e)) from None
    return {"tree": clean_tree((got or {}).get("tree") if isinstance(got, dict) else None) or {"turns": []},
            "copy": isinstance(got, dict) and got.get("copy") is True}


# what the app takes of a run whose record the workspace found rewritten with no copy of its own to put back (its
# view's `unverified`): how the run stands, and nothing it says of the run's work
STANDING = ("state", "reason", "cause", "ended_at")


def _clean(view):
    """A run's record from the workspace, held to what each field is: the workspace's programs could write it. Its
    judge's verdict and what it replays are the app's own (_judge_due, replay), never taken from here."""
    out = {}
    state = view.get("state")
    out["state"] = state if state in STATES else "failed"
    for key in ("reason", "cause", "question", "last_text", "branch"):
        value = view.get(key)
        out[key] = value[:2000] if isinstance(value, str) else None
    for key in ("started_at", "ended_at", "steps", "requests", "bytes"):
        value = view.get(key)
        # within SQLite's 64 bits: storing a larger one would raise, and the whole copy with it
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 1 << 63 else None
    for key in ("base", "head"):
        value = view.get(key)
        out[key] = value if isinstance(value, str) and re.match(r"^[0-9a-f]{40}([0-9a-f]{24})?$", value) else None
    cost = view.get("cost_est_usd")
    out["cost_est_usd"] = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) \
        and 0 <= cost < 1e6 else None
    tokens = view.get("tokens")
    out["tokens"] = json.dumps({k: v for k, v in tokens.items() if isinstance(k, str) and len(k) <= 40
                                and isinstance(v, int) and not isinstance(v, bool) and v >= 0})[:2000] \
        if isinstance(tokens, dict) else None
    out["pruned"] = 1 if view.get("pruned") is True else 0
    out["steps"] = out["steps"] or 0
    limits = view.get("rate_limits") if isinstance(view.get("rate_limits"), dict) else {}
    resets = [w.get("resets_at") for w in limits.values() if isinstance(w, dict) and w.get("status") == "rejected"
              and isinstance(w.get("resets_at"), int) and not isinstance(w.get("resets_at"), bool)
              and 0 <= w["resets_at"] < 1 << 63]
    out["resets_at"] = max(resets) if resets and out["cause"] == "rate_limit" else None
    out["context"] = _context(view.get("context"))
    dispatches = view.get("dispatches")
    out["dispatches"] = dispatches if isinstance(dispatches, int) and not isinstance(dispatches, bool) \
        and 0 <= dispatches <= 100 else 0
    waiting = view.get("waiting_on")
    out["waiting_on"] = waiting if isinstance(waiting, str) and _ID.match(waiting) else None
    out["metrics"] = agent_metrics.clean(view.get("metrics"))
    digest = view.get("memory_digest")
    out["memory_digest"] = digest if isinstance(digest, str) and re.match(r"^[0-9a-f]{64}$", digest) else None
    out["anomalies"] = quality.clean_anomalies(view.get("anomalies"))
    out["acceptance"] = quality.clean_acceptance(view.get("acceptance"))
    changes = view.get("changes") if isinstance(view.get("changes"), list) else []
    kept = [c for c in (improve.check_change_shape(c) for c in changes[:2]) if c]
    out["changes"] = json.dumps(kept) if kept else None
    return out


def _context(given):
    """A run's context as the workspace measured it (runtree.py), held to its shapes; None for none."""
    if not isinstance(given, dict):
        return None
    number = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e12 else None
    log = given.get("compaction_log") if isinstance(given.get("compaction_log"), list) else []
    lost = given.get("lost") if isinstance(given.get("lost"), list) else []
    return json.dumps({
        **{k: number(given.get(k)) for k in ("model_window", "compaction_window", "peak_tokens", "peak_pct",
                                             "last_tokens", "compactions", "retention")},
        "fragmented": given.get("fragmented") is True,
        "compaction_log": [{"at": number(c.get("at")), "trigger": str(c.get("trigger") or "")[:20],
                            "pre_tokens": number(c.get("pre_tokens")), "post_tokens": number(c.get("post_tokens"))}
                           for c in log[:20] if isinstance(c, dict)],
        "lost": [str(n)[:200] for n in lost[:50]]})


def _late_hold(db, run_id, view):
    """A hold the workspace found after the run's end (its record's `late_hold`, proofpane_workspace/agents.py's
    _report_late), taken once: its words when its change number is past the one the store last took for the run (the
    run's `seq` column, which holds this and nothing else), which is then set to it; else None. A record the workspace
    found rewritten by something else (its view's `unverified`) carries none, and none is taken from it. A run's later
    record (its folder pruned, say) carries the same change number again, and is not taken twice; a later hold is a
    later write's, and is."""
    given = view.get("late_hold")
    if view.get("unverified") is True or not isinstance(given, dict):
        return None
    seq, words = given.get("seq"), given.get("words")
    if not (isinstance(seq, int) and not isinstance(seq, bool) and 0 < seq < 1 << 63) or \
            not (isinstance(words, str) and words.strip()):
        return None
    if not db.execute("UPDATE ade_agent_runs SET seq = ? WHERE run_id = ? AND (seq IS NULL OR seq < ?)",
                      (seq, run_id, seq)).rowcount:
        return None
    return words.strip()[:2000]


def _joined(first, later, most=2000):
    """A run's end's hold and a hold found after it (_late_hold), as one reason of at most `most` characters: both whole
    where they fit; else one no longer than half the room whole and the other cut to the rest, or, where both are
    longer, each cut to about half; each cut marked so, and neither text cut away by the other (the
    late words come second, and an end's hold of 2,000 characters would have left none of them).
    That holds for the two texts, not for each place they name: the late words name every place held for the agent,
    and one of them, the place just found among them, can fall past a cut."""
    if len(first) + 1 + len(later) <= most:
        return f"{first} {later}"
    room = most - 1

    def cut(text, n):
        return text if len(text) <= n else text[:max(0, n - 3)] + "..."
    if len(first) <= room // 2:
        return f"{first} {cut(later, room - len(first))}"
    if len(later) <= room // 2:
        return f"{cut(first, room - len(later))} {later}"
    first = cut(first, room // 2)
    return f"{first} {cut(later, room - len(first))}"


def _pause_held(db, user_id, agent_id, words, now):
    """The agent paused with why (a run's hold), until the owner resumes it, whatever pause till a time it had (which
    would lift it). Counted as not yet mirrored paused: the workspace lifts its hold only on a mirror saying paused and
    then one saying not (_mirror)."""
    db.execute("UPDATE ade_agents SET paused = 1, paused_reason = ?, paused_until = NULL, "
               "hold_unmirrored = hold_unmirrored + 1, updated_at = ? WHERE id = ? AND user_id = ? AND "
               "retired_at IS NULL", (words[:2000], now, agent_id, user_id))


def _apply(user_id, views, record_seq=True):
    """Copy runs' records from the workspace into the store: runs the app made, of the account's own agents, each to
    the row it has, and runs another run or a chat started there, taken in within their bounds (_adopt); any other run
    the workspace names is not taken, so nothing a workspace's programs write grows the store. Then each changed
    agent's totals again."""
    from . import tasks
    changed = set()
    now = int(time.time())
    tells = []                          # what a task's move left to tell, once this transaction has committed
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")   # a task's lease and fence are compared and set in this transaction
        pending = [v for v in views if isinstance(v, dict) and isinstance(v.get("run_id"), str)
                   and _ID.match(v["run_id"])]
        while pending:
            later = []
            for view in pending:
                run_id, agent_id = view["run_id"], view.get("agent_id")
                have = db.execute("SELECT agent_id, user_id, state FROM ade_agent_runs WHERE run_id = ?",
                                  (run_id,)).fetchone()
                if have is None:
                    taken = _adopt(db, user_id, view, now)
                    if taken is None:
                        later.append(view)          # its parent may be later in this read
                        continue
                    if not taken:
                        continue
                    have = db.execute("SELECT agent_id, user_id, state FROM ade_agent_runs WHERE run_id = ?",
                                      (run_id,)).fetchone()
                if have["agent_id"] != agent_id or have["user_id"] != user_id:
                    continue
                fields = _clean(view)
                standing = view.get("unverified") is True
                if standing:
                    fields = {k: fields[k] for k in STANDING}
                    fields["rewritten"] = ("Its record was rewritten by something other than the workspace, which had "
                                           "no copy left to put back: only how the run stands is kept.")
                elif isinstance(view.get("rewritten"), int) and not isinstance(view.get("rewritten"), bool) \
                        and view["rewritten"] > 0:
                    n = min(view["rewritten"], 1000)
                    fields["rewritten"] = (f"Its record was rewritten by something other than the workspace "
                                           f"({n} {'time' if n == 1 else 'times'}), and put back as the workspace "
                                           "wrote it.")
                version = view.get("brief_version")
                if not standing and isinstance(version, int) and not isinstance(version, bool):
                    # the brief it started with (an edit after it was queued is the one it ran), within what the store
                    # knows: never below the version it was queued with, never past the agent's own
                    bounds = db.execute("SELECT r.brief_version AS queued, a.brief_version AS now FROM ade_agent_runs r "
                                        "JOIN ade_agents a ON a.id = r.agent_id WHERE r.run_id = ?", (run_id,)).fetchone()
                    if bounds and bounds["queued"] is not None and bounds["queued"] <= version <= bounds["now"]:
                        fields["brief_version"] = version
                held = isinstance(view.get("hold"), str) and bool(view["hold"].strip())
                if have["state"] in ENDED and not (have["state"] == "stopped" and fields.get("state") == "blocked"
                                                   and (fields.get("cause") == "watch" or held)):
                    # ended in the store: after its end a run's record says only that its folder was pruned and that
                    # it was rewritten; the one way an ended run moves on is stopped while it started, then blocked by
                    # its precheck's watch, or by anything else that holds its agent (a task's attempt's start finding
                    # its watched places changed): the workspace holds the agent
                    # until the app pauses it and the owner resumes it. So a record written back over a later one (its
                    # own earlier one, after the workspace restarted with no copy to put back) changes nothing the
                    # store has of it
                    late = {"rewritten": fields["rewritten"]} if "rewritten" in fields else {}
                    if fields.get("pruned") == 1:
                        late.update(pruned=1, bytes=fields.get("bytes"))
                    told = view.get("task") if isinstance(view.get("task"), dict) else {}
                    if not standing and tasks.brings_new_work(db, user_id, run_id, told):
                        # a task's attempt that ended in the store, its record bringing work the store has not taken
                        # from it: refused, late (the same record read again brings nothing new, and is not)
                        tasks.late_record(db, user_id, run_id, now)
                        changed.add(agent_id)
                    if late:
                        db.execute("UPDATE ade_agent_runs SET " + ", ".join(f"{k} = ?" for k in late) + ", "
                                   "updated_at = ? WHERE run_id = ?", (*late.values(), now, run_id))
                        changed.add(agent_id)
                    found = _late_hold(db, run_id, view)
                    if found:
                        # a hold the workspace found after the run's end, the
                        # record's `late_hold`: the agent it holds there is paused here, once for each such hold
                        _pause_held(db, user_id, agent_id, found, now)
                        changed.add(agent_id)
                    continue
                hold = view.get("hold")
                ending = have["state"] not in ENDED and fields["state"] in ENDED
                # a run stopped while it started (the store has it stopped) is blocked once its precheck's watch is read
                blocked = have["state"] in ENDED and have["state"] != "blocked" and fields["state"] == "blocked"
                # the run ended holding its agent (the workspace saw something it left to start later): paused, with
                # why, until the owner resumes it; once, at its end, so reading it again never pauses it again. And a
                # hold found after its end (_late_hold), where the store missed its end or the run had not yet started:
                # its words after the end's own, unless the end's say them already, neither cut away by the other
                said = hold.strip() if isinstance(hold, str) and hold.strip() and (ending or blocked) else ""
                found = _late_hold(db, run_id, view)
                if found and found not in said:
                    said = _joined(said, found) if said else found
                if said:
                    _pause_held(db, user_id, agent_id, said, now)
                db.execute("UPDATE ade_agent_runs SET " + ", ".join(f"{k} = ?" for k in fields) + ", sent = 1, "
                           "updated_at = ? WHERE run_id = ?", (*fields.values(), now, run_id))
                if db.execute("SELECT task_id FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()[0]:
                    # a task's attempt: its last words and question kept as a model's text is, apart
                    # from the rest, which no failure there undoes; then what its record brings to its task, under
                    # the lease (tasks.py); a failure there keeps the run's own copy, marked so that the task's next
                    # settle, deciding from the run's end, holds it for the owner
                    tasks.scrub_said(db, run_id)
                    db.execute("SAVEPOINT task_record")
                    told = len(tells)
                    try:
                        tasks.take_record(db, user_id, run_id, have["state"], view, now, tells, standing=standing)
                    except Exception as e:  # noqa: BLE001  the run's history is copied all the same
                        db.execute("ROLLBACK TO task_record")
                        del tells[told:]
                        print(f"agents: a task's record could not be taken: {type(e).__name__}: {e}", file=sys.stderr)
                        tasks.mark_record_lost(db, run_id, now)
                    db.execute("RELEASE task_record")
                elif not standing:
                    # an agent's own run runs on its agent's account: its windows kept per backend,
                    # under the account the view's top-level `backend` names when it names one, and only there,
                    # never because another backend is; else under its agent's account,
                    # or default. An agent with no account runs on the account new sessions start on, which the run
                    # keeps as its backend, so that the agent's schedules are held by that account's windows
                    # (tasks.held_for_schedules), not by default's
                    owner = db.execute("SELECT account FROM ade_agents WHERE id = ?", (agent_id,)).fetchone()
                    account = owner["account"] if owner else None
                    try:
                        ran = tasks.check_backend(view["backend"]) if isinstance(view.get("backend"), dict) else None
                    except AgentError:
                        ran = None
                    named = ran["id"] if ran and ran["kind"] == "account" else None
                    if named and not account:
                        db.execute("UPDATE ade_agent_runs SET backend = ? WHERE run_id = ? AND task_id IS NULL",
                                   (json.dumps(ran), run_id))
                    tasks.take_windows(db, user_id, "account:" + (named or account or "default"),
                                       view.get("rate_limits"), run_id, now)
                if not standing and any(view.get(k) for k in ("mcp", "mcp_seen", "mcp_changed", "mcp_unavailable",
                                                              "proposals")):
                    _tools(db, user_id, agent_id, run_id, view, now)
                if not standing and (view.get("facts") or view.get("retractions")):
                    facts.take(db, user_id, agent_id, run_id, view, now)        # each once, by its id
                loop = db.execute("SELECT loop FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()["loop"]
                if fields["state"] in ENDED and loop:
                    improve.run_ended(db, run_id, fields["cost_est_usd"])        # the loop's, its session's cost
                elif fields["state"] in ENDED:
                    agent_metrics.roll(db, user_id, agent_id, run_id, now)      # once, at its end
                    suites.score(db, run_id, now)                                # an eval's run: scored, once
                changed.add(agent_id)
            if len(later) == len(pending):
                break
            pending = later
        for agent_id in changed:
            _totals(db, user_id, agent_id)
            _day_limit(db, agent_id, now)
    tasks.deliver(user_id, tells)
    return changed


FLAGGED_MAX = 20                # runs kept as flagged under one parent: past it, a workspace's programs grow nothing


def _adopt(db, user_id, view, now):
    """A run another run (or a chat) started in the workspace, taken into the store: True when taken, False when
    refused, None when its parent is not in the store (yet: it may come later in the same read). Bounded by what the
    store knows, never by what the workspace says: the agent is the account's; the parent is a run of the account's
    (not a hook's, not of the same agent), the run one deeper, at most MAX_DEPTH, and at most the parent agent's
    max_dispatch_per_run under it; or a chat its owner let start runs (depth 1, at most DISPATCH_WIDTH under it). A
    run under a known run of the account that broke one of those bounds is kept, flagged with which, and told to the
    owner (at most FLAGGED_MAX under one parent): the workspace started it, so it is shown and counted, never
    hidden."""
    run_id, agent_id = view["run_id"], view.get("agent_id")
    trigger = view.get("trigger") if isinstance(view.get("trigger"), dict) else {}
    parent = trigger.get("parent_run_id")
    if trigger.get("kind") != "dispatch" or not (isinstance(parent, str) and _ID.match(parent)) or parent == run_id:
        return False
    if db.execute("SELECT 1 FROM ade_agents WHERE id = ? AND user_id = ?", (agent_id, user_id)).fetchone() is None:
        return False
    above = db.execute("SELECT r.agent_id, r.depth, r.trigger, r.state, r.ended_at, r.parent_run_id, "
                       "a.max_dispatch_per_run, a.may_dispatch FROM ade_agent_runs r JOIN ade_agents a ON a.id = "
                       "r.agent_id WHERE r.run_id = ? AND r.user_id = ?", (parent, user_id)).fetchone()
    flag = None
    if above is not None:
        depth, width, sender = above["depth"] + 1, above["max_dispatch_per_run"], above["agent_id"]
        queued_at = view.get("queued_at") if isinstance(view.get("queued_at"), int) else now
        ancestors = {above["agent_id"]}
        up = above["parent_run_id"]
        while up and len(ancestors) <= MAX_DEPTH:
            row = db.execute("SELECT agent_id, parent_run_id FROM ade_agent_runs WHERE run_id = ?", (up,)).fetchone()
            if row is None:
                break
            ancestors.add(row["agent_id"])
            up = row["parent_run_id"]
        if _json(above["trigger"], {}).get("kind") == "hook":
            flag = "started by a run a hook started, which may start none"
        elif above["agent_id"] == agent_id:
            flag = "started by a run of its own agent"
        elif depth > MAX_DEPTH:
            flag = f"started {depth} deep, past {MAX_DEPTH}"
        elif agent_id in ancestors:
            flag = "started for an agent whose run started its caller, which it would wait behind"
        elif not above["may_dispatch"]:
            flag = "started by a run whose agent may not start runs"
        elif above["state"] in ENDED and above["ended_at"] and queued_at > above["ended_at"] + 60:
            flag = "started under a run that had ended"
    elif db.execute("SELECT 1 FROM ade_dispatch_chats WHERE user_id = ? AND session_id = ?",
                    (user_id, parent)).fetchone() is not None:
        depth, width, sender = 1, DISPATCH_WIDTH, None
    else:
        return None
    under = db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE user_id = ? AND parent_run_id = ? AND flag IS NULL",
                       (user_id, parent)).fetchone()[0]
    if flag is None and depth > MAX_DEPTH:
        flag = f"started {depth} deep, past {MAX_DEPTH}"
    if flag is None and under >= width:
        flag = f"one more than the {width} runs its parent may start"
    if flag is not None:
        if above is None or db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE user_id = ? AND parent_run_id = ? AND "
                                       "flag IS NOT NULL", (user_id, parent)).fetchone()[0] >= FLAGGED_MAX:
            return False
    queued = view.get("queued_at")
    queued = queued if isinstance(queued, int) and not isinstance(queued, bool) and 0 < queued <= now + 60 else now
    version = view.get("brief_version")
    db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, sent, trigger, queued_at, brief_version, "
               "parent_run_id, depth, flag, updated_at) VALUES (?,?,?,'queued',1,?,?,?,?,?,?,?)",
               (run_id, agent_id, user_id, json.dumps({"kind": "dispatch", "parent_run_id": parent, "depth": depth,
                                                       "from_agent": sender, "at": queued}), queued,
                version if isinstance(version, int) and not isinstance(version, bool) else None, parent, depth, flag,
                now))
    return True


def _subtrees(db, user_id, run_ids):
    """For each of `run_ids` that started runs: the runs under it, theirs too, with their estimated cost and seconds,
    how many are open (queued or live) and how many failed. One recursive read a few hundred runs at a time; a tree is
    at most MAX_DEPTH deep."""
    out = {}
    ids = sorted(set(run_ids))
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        rows = db.execute(
            "WITH RECURSIVE sub(root, run_id) AS ("
            f"SELECT parent_run_id, run_id FROM ade_agent_runs WHERE user_id = ? AND parent_run_id IN "
            f"({','.join('?' * len(part))}) "
            "UNION SELECT sub.root, r.run_id FROM ade_agent_runs r JOIN sub ON r.parent_run_id = sub.run_id "
            "WHERE r.user_id = ?) "
            "SELECT sub.root AS root, COUNT(*) AS runs, COALESCE(SUM(r.cost_est_usd), 0) AS cost, "
            "COALESCE(SUM(CASE WHEN r.ended_at >= r.started_at THEN r.ended_at - r.started_at END), 0) AS seconds, "
            "SUM(r.state IN ('queued', 'working', 'waiting')) AS open, SUM(r.state = 'failed') AS failed "
            "FROM sub JOIN ade_agent_runs r ON r.run_id = sub.run_id GROUP BY sub.root",
            (user_id, *part, user_id)).fetchall()
        for r in rows:
            out[r["root"]] = {"runs": r["runs"], "cost_est_usd": round(r["cost"], 6), "seconds": r["seconds"],
                              "open": r["open"], "failed": r["failed"]}
    return out


def grant_chat(user_id, session_id):
    """The owner let a chat start agents' runs: kept, so the runs it starts are taken into the store (_adopt)."""
    with store.connect() as db:
        db.execute("INSERT OR IGNORE INTO ade_dispatch_chats (user_id, session_id, granted_at) VALUES (?,?,?)",
                   (user_id, session_id, int(time.time())))


def revoke_chat(user_id, session_id):
    """The owner turned a chat's grant off: the runs it starts from now are not taken into the store."""
    with store.connect() as db:
        db.execute("DELETE FROM ade_dispatch_chats WHERE user_id = ? AND session_id = ?", (user_id, session_id))


def _day_limit(db, agent_id, now):
    """Past its day's estimated cost (est_usd_per_day), the agent is paused until the next day in UTC or the owner
    resumes it: a soft pause on an estimate, which never stops a run midway (a run's own budget does that)."""
    row = db.execute("SELECT a.paused, a.est_usd_per_day, t.est_usd_today, t.today FROM ade_agents a JOIN "
                     "ade_agent_totals t ON t.agent_id = a.id WHERE a.id = ?", (agent_id,)).fetchone()
    if row is None or not row["est_usd_per_day"] or row["paused"] or row["today"] != _today(now) \
            or row["est_usd_today"] < row["est_usd_per_day"]:
        return
    db.execute("UPDATE ade_agents SET paused = 1, paused_until = ?, paused_reason = ?, updated_at = ? WHERE id = ?",
               (now - now % 86400 + 86400, f"Today's estimated cost passed its limit of {usd(row['est_usd_per_day'])}: "
                "it runs again tomorrow (UTC), or when you resume it.", now, agent_id))


def usd(value):
    return f"${value:.2f}" if value >= 1 else f"${value:.4f}"


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _tools(db, user_id, agent_id, run_id, view, now):
    """What a run reports of its agent's servers: each one's tools the first time a run sees them are taken as the
    owner's (the server is the one the owner added); one a run found changed since is held, out of every run until
    the owner accepts it; the last report kept for the Tools panel; a server the run proposed is kept, to show and
    push once. Held to its shapes: the workspace's programs could write it."""
    row = db.execute("SELECT mcp, mcp_state FROM ade_agents WHERE id = ? AND user_id = ?", (agent_id, user_id)).fetchone()
    if row is None:
        return
    mcp = _mcp(row)
    names = {s["name"] for s in mcp["servers"]}
    seen = view.get("mcp_seen") if isinstance(view.get("mcp_seen"), dict) else {}
    seen = {k: v for k, v in seen.items() if k in names and isinstance(v, str) and _DIGEST.match(v)}
    changed = [n for n in view.get("mcp_changed") or [] if isinstance(n, str) and n in names] \
        if isinstance(view.get("mcp_changed"), list) else []
    accepted = dict(mcp["accepted"])
    for name, digest in seen.items():
        accepted.setdefault(name, digest)
    held = sorted(set(mcp["held"]) | set(changed))
    if accepted != mcp["accepted"] or held != mcp["held"]:
        db.execute("UPDATE ade_agents SET mcp = ?, updated_at = ? WHERE id = ?",
                   (json.dumps(dict(mcp, accepted=accepted, held=held), sort_keys=True), now, agent_id))
    state = _json(row["mcp_state"], {})
    status = view.get("mcp") if isinstance(view.get("mcp"), list) else None
    if status is not None:
        state["status"] = [{"name": str(x.get("name"))[:64], "scope": str(x.get("scope") or "")[:20],
                            "status": str(x.get("status") or "")[:20], "error": str(x.get("error") or "")[:300] or None,
                            "tools": x.get("tools") if isinstance(x.get("tools"), int) else None}
                           for x in status[:40] if isinstance(x, dict)]
    unavailable = view.get("mcp_unavailable")
    if isinstance(unavailable, dict):
        state["unavailable"] = {str(k)[:64]: str(v)[:300] for k, v in list(unavailable.items())[:40]}
    state.update(seen=dict(state.get("seen") or {}, **seen), run_id=run_id, at=now)
    db.execute("UPDATE ade_agents SET mcp_state = ? WHERE id = ?", (json.dumps(state, sort_keys=True), agent_id))
    for p in (view.get("proposals") if isinstance(view.get("proposals"), list) else [])[:5]:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not 0 < len(p["name"]) <= 200:
            continue
        entry = _entry(p)
        why = p.get("why")[:2000] if isinstance(p.get("why"), str) else None
        db.execute("INSERT OR IGNORE INTO ade_agent_proposals (id, user_id, agent_id, run_id, name, entry, why, state, "
                   "created_at) VALUES (?,?,?,?,?,?,?,'open',?)",
                   (secrets.token_hex(6), user_id, agent_id, run_id, p["name"], json.dumps(entry)[:20000], why, now))


def _way(given):
    """A registry way of running a server, held to its shapes: strings as strings, named fields as such."""
    def fields(items):
        return [{"name": _str(f.get("name"), 128), "secret": f.get("secret") is True, "required": f.get("required") is True,
                 "description": _str(f.get("description"), 300)} for f in (items or [])[:40] if isinstance(f, dict)] \
            if isinstance(items, list) else []
    return {"kind": _str(given.get("kind"), 20), "registry": _str(given.get("registry"), 40),
            "identifier": _str(given.get("identifier"), 200), "version": _str(given.get("version"), 64),
            "transport": _str(given.get("transport"), 20), "url": _str(given.get("url"), 2000),
            "env": fields(given.get("env")), "headers": fields(given.get("headers"))}


def _entry(given):
    """A proposed server's registry entry as a run gave it, held to its shapes: the page renders each as text."""
    repository = given.get("repository")
    return {"name": _str(given.get("name"), 200), "description": _str(given.get("description"), 2000),
            "version": _str(given.get("version"), 64),
            "repository": repository[:400] if isinstance(repository, str) else None,
            "ways": [_way(w) for w in (given.get("ways") or [])[:10] if isinstance(w, dict)]
            if isinstance(given.get("ways"), list) else []}


def accept_tools(user, agent_id, name):
    """The owner looked at a server's tools as the last run found them, and accepts them: its runs use it again."""
    with store.connect() as db:
        row = _row(db, user["id"], agent_id)
        mcp = _mcp(row)
        seen = (_json(row["mcp_state"], {}).get("seen") or {}).get(name)
        if name not in {s["name"] for s in mcp["servers"]} or not isinstance(seen, str):
            raise AgentError(404, "No run has listed that server's tools yet.")
        mcp["accepted"][name] = seen
        mcp["held"] = [n for n in mcp["held"] if n != name]
        db.execute("UPDATE ade_agents SET mcp = ?, updated_at = ? WHERE id = ?",
                   (json.dumps(mcp, sort_keys=True), int(time.time()), agent_id))
    _deliver_soon(user)
    return one(user, agent_id)


def proposals(user_id, agent_id=None):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_agent_proposals WHERE user_id = ? AND state = 'open'" +
                          (" AND agent_id = ?" if agent_id else "") + " ORDER BY created_at",
                          (user_id, agent_id) if agent_id else (user_id,)).fetchall()
    return [{"id": r["id"], "agent_id": r["agent_id"], "run_id": r["run_id"], "name": r["name"], "why": r["why"],
             "entry": _entry(_json(r["entry"], {}) or {}), "created_at": r["created_at"]} for r in rows]


def settle_proposal(user, proposal_id, state):
    """A proposal the owner added (from its entry, on the agent's tools) or dismissed: no longer shown or pushed."""
    if state not in ("added", "dismissed"):
        raise AgentError(400, "A proposal is added or dismissed.")
    with store.connect() as db:
        done = db.execute("UPDATE ade_agent_proposals SET state = ? WHERE id = ? AND user_id = ? AND state = 'open'",
                          (state, proposal_id, user["id"])).rowcount
    if not done:
        raise AgentError(404, "No such proposal open.")
    return {"id": proposal_id, "state": state}


def secrets_of(user, agent_id, given=None):
    """Which of an agent's servers' secrets have a value, at the workspace, the only place they are kept; with `given`,
    first set or take some away there. The machine must run: the app keeps no secret, not even on its way."""
    with store.connect() as db:
        _row(db, user["id"], agent_id)
    endpoint = _endpoint(user)
    if endpoint is None:
        raise AgentError(409, "Start the workspace first: an agent's secrets are kept only there.")
    try:
        _mirror(user, endpoint)
        if given is None:
            return client.call(endpoint, "GET", f"/agents/{agent_id}/mcp-secrets", timeout=30)
        return client.call(endpoint, "PUT", f"/agents/{agent_id}/mcp-secrets", {"secrets": given}, timeout=30)
    except client.WorkspaceCallError as e:
        raise AgentError(e.status if e.status in (400, 404, 409) else 502, str(e)) from None


def search(query, cursor=None):
    """The official MCP registry's servers matching `query` (unvetted: anyone can publish there)."""
    from . import registry
    try:
        return registry.search(query, cursor)
    except registry.RegistryError as e:
        raise AgentError(502 if "registry" in str(e).lower() else 400, str(e)) from None


def _totals(db, user_id, agent_id):
    """An agent's totals, from its runs: what its row in the list shows."""
    now = int(time.time())
    today, start_of_day = _today(now), now - now % 86400          # the day in UTC, as _today names it
    row = db.execute(
        "SELECT COUNT(*) AS runs, SUM(state = 'done') AS done, "
        # the agent's own failures: not a start, an account or a claude that failed it (the machine's doing)
        "SUM(state = 'failed' AND COALESCE(cause, '') NOT IN ('start', 'account', 'claude')) AS failed, "
        "SUM(state = 'blocked') AS blocked, "
        "COALESCE(SUM(CASE WHEN ended_at IS NOT NULL AND started_at IS NOT NULL THEN ended_at - started_at END), 0) "
        "AS seconds, COALESCE(SUM(cost_est_usd), 0) AS cost, MAX(COALESCE(started_at, queued_at)) AS last_run_at, "
        "SUM(CASE WHEN queued_at >= ? THEN 1 ELSE 0 END) AS runs_today, "
        "COALESCE(SUM(CASE WHEN queued_at >= ? THEN cost_est_usd END), 0) AS cost_today "
        "FROM ade_agent_runs WHERE agent_id = ? AND loop = 0", (start_of_day, start_of_day, agent_id)).fetchone()
    tokens = 0
    for r in db.execute("SELECT tokens FROM ade_agent_runs WHERE agent_id = ? AND loop = 0 AND tokens IS NOT NULL",
                        (agent_id,)):
        tokens += sum(v for v in _json(r["tokens"], {}).values() if isinstance(v, int))
    last = db.execute("SELECT state FROM ade_agent_runs WHERE agent_id = ? AND loop = 0 ORDER BY queued_at DESC, "
                      "run_id DESC LIMIT 1", (agent_id,)).fetchone()
    db.execute("INSERT INTO ade_agent_totals (agent_id, user_id, runs, done, failed, blocked, seconds, tokens, "
               "cost_est_usd, last_run_at, last_state, today, runs_today, est_usd_today) VALUES "
               "(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET runs = excluded.runs, "
               "done = excluded.done, failed = excluded.failed, blocked = excluded.blocked, "
               "seconds = excluded.seconds, tokens = excluded.tokens, cost_est_usd = excluded.cost_est_usd, "
               "last_run_at = excluded.last_run_at, last_state = excluded.last_state, today = excluded.today, "
               "runs_today = excluded.runs_today, est_usd_today = excluded.est_usd_today",
               (agent_id, user_id, row["runs"], row["done"] or 0, row["failed"] or 0, row["blocked"] or 0,
                row["seconds"], tokens, row["cost"], row["last_run_at"], last["state"] if last else None, today,
                row["runs_today"] or 0, row["cost_today"]))


# ---- what the owner is told (push.py's notifier) -------------------------------------------------------------------

FAILED_TOLD = 24 * 3600         # a failed run is told for a day after it ended; the notifier sends each once
CONTEXT_TOLD = 0.8              # a live run past this share of its compaction point is told once


def alerts(user_id, now=None):
    """(key, the kind of its notice: push.words) for each run waiting for the owner (its question, or a permission
    ask), each run that failed in the last day, and each thing of an agent's that needs a look: a live run near its
    context's end, a server its tools left out, a server it proposes, knowledge going stale, judged runs that drop, a
    run's anomaly, a run started outside its bounds, a run's record rewritten, and a change the improve loop has ready.
    Told in fixed words, never an agent's name, a question, a reason or anything a run wrote: the page shows them. A key
    stays the same while its alert lasts; a new question is a new key."""
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        rows = db.execute("SELECT r.run_id, r.state, r.question FROM ade_agent_runs r JOIN ade_agents a ON a.id = "
                          "r.agent_id WHERE r.user_id = ? AND r.loop = 0 AND (r.state = 'waiting' OR (r.state = "
                          "'failed' AND r.ended_at >= ?))", (user_id, now - FAILED_TOLD)).fetchall()
        full = db.execute("SELECT r.run_id, r.context FROM ade_agent_runs r JOIN ade_agents a ON a.id = r.agent_id "
                          "WHERE r.user_id = ? AND r.state IN ('working', 'waiting') AND r.context IS NOT NULL",
                          (user_id,)).fetchall()
    out = []
    for r in rows:
        if r["state"] == "waiting":
            if r["question"]:
                key = "agent-question:" + r["run_id"] + ":" + hashlib.sha256(r["question"].encode()).hexdigest()[:12]
                out.append((key, "agent-waits"))
            else:
                out.append(("agent-waiting:" + r["run_id"], "agent-waits"))
        else:
            out.append(("agent-failed:" + r["run_id"], "agent-failed"))
    for r in full:
        context = _json(r["context"], {})
        if isinstance(context.get("peak_pct"), (int, float)) and context["peak_pct"] >= CONTEXT_TOLD:
            out.append(("agent-context:" + r["run_id"], "agent-look"))
    with store.connect() as db:
        held = db.execute("SELECT id, mcp, mcp_state FROM ade_agents WHERE user_id = ? AND retired_at IS NULL AND "
                          "mcp LIKE '%\"held\": [\"%'", (user_id,)).fetchall()
        open_ = db.execute("SELECT p.id FROM ade_agent_proposals p JOIN ade_agents a ON a.id = p.agent_id WHERE "
                           "p.user_id = ? AND p.state = 'open'", (user_id,)).fetchall()
    for a in held:
        seen = _json(a["mcp_state"], {}).get("seen") or {}
        out += [(f"agent-tools:{a['id']}:{name}:{str(seen.get(name))[:12]}", "agent-look") for name in _mcp(a)["held"]]
    out += [(f"agent-proposal:{p['id']}", "agent-look") for p in open_]
    with store.connect() as db:
        for a in db.execute("SELECT * FROM ade_agents WHERE user_id = ? AND retired_at IS NULL", (user_id,)).fetchall():
            key = freshness.alert(db, a, now)
            if key:
                out.append((key, "agent-look"))
        trusts = quality.trust(db, user_id)
        for a in db.execute("SELECT id FROM ade_agents WHERE user_id = ? AND retired_at IS NULL", (user_id,)).fetchall():
            d = quality.drift(db, user_id, a["id"], trusts)
            if d["level"]:
                out.append((f"agent-drift:{a['id']}:{d['version']}:{d['level']}", "agent-look"))
        for r in db.execute("SELECT r.run_id, r.anomalies FROM ade_agent_runs r JOIN ade_agents a ON a.id = r.agent_id "
                            "WHERE r.user_id = ? AND r.loop = 0 AND r.anomalies IS NOT NULL AND r.updated_at >= ?",
                            (user_id, now - FAILED_TOLD)).fetchall():
            out += [(f"agent-anomaly:{r['run_id']}:{anomaly['rule']}:{anomaly['level']}", "agent-look")
                    for anomaly in _json(r["anomalies"], [])]
        for r in db.execute("SELECT r.run_id FROM ade_agent_runs r JOIN ade_agents a ON a.id = r.agent_id WHERE "
                            "r.user_id = ? AND r.flag IS NOT NULL AND r.updated_at >= ?",
                            (user_id, now - FAILED_TOLD)).fetchall():
            out.append((f"agent-flagged:{r['run_id']}", "agent-look"))
        for r in db.execute("SELECT r.run_id FROM ade_agent_runs r JOIN ade_agents a ON a.id = r.agent_id WHERE "
                            "r.user_id = ? AND r.rewritten IS NOT NULL AND r.updated_at >= ?",
                            (user_id, now - FAILED_TOLD)).fetchall():
            out.append((f"agent-rewritten:{r['run_id']}", "agent-look"))
        # the improve loop's proposals ready for the owner's tap: a change that passed its sandbox, and a rollback
        for p in db.execute("SELECT id FROM ade_improve_proposals WHERE user_id = ? AND state = 'eligible'",
                            (user_id,)).fetchall():
            out.append((f"agent-improve:{p['id']}", "agent-look"))
    from . import tasks
    out += tasks.alerts(user_id, now)   # a task that needs a decision (tasks.py)
    return out


# ---- the poller ----------------------------------------------------------------------------------------------------

def poll_all(now=None):
    """One round: a pause till a time that has come is lifted (mirrored at the next sync), every account with a
    controller to sync (controller.users_to_poll) has the controller's sync, first, so that the owner's Stop never
    waits on the agents' sync nor fails with it, every account the round reaches has the read of its workspace's skill
    calls (skill_counts.read, which reaches those the app read for before too, once an hour each), and every account
    with an agent whose machine runs is synced. An owner the business skills' loop names (skill_improve.users_to_poll)
    whom no sync reaches, having no agent, has the account's thread of the judge and the loop started all the same, so
    that his controller's tasks' skills are reviewed whether or not he has an agent."""
    from . import controller, skill_counts, skill_improve
    now = int(time.time() if now is None else now)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET paused = 0, paused_reason = NULL, paused_until = NULL, updated_at = ? WHERE "
                   "paused_until IS NOT NULL AND paused_until <= ?", (now, now))
        forget(db, now)
        users = [r[0] for r in db.execute("SELECT DISTINCT user_id FROM ade_agents WHERE retired_at IS NULL")]
        controlled = controller.users_to_poll(db)
        counted = skill_counts.users_to_poll(db, now)
        improving = skill_improve.users_to_poll(db)
    reached = controlled + [u for u in users if u not in controlled]
    reached += [u for u in counted if u not in reached]
    for user_id in reached + [u for u in improving if u not in reached]:
        user = auth.user(user_id)
        if user is None:
            continue
        try:
            if user_id in controlled:
                sync_controller(user)
            skill_counts.read(user)
            if user_id in users:
                sync(user, background=True)
            elif user_id in improving:
                endpoint = _endpoint(user)
                if endpoint is not None:
                    _slow_work(user, endpoint)
        except Exception as e:  # noqa: BLE001  one account failing never stops the others
            print(f"agents: the poll of {user_id} failed: {type(e).__name__}: {e}", file=sys.stderr)


KEPT_DAYS = 90


def forget(db, now):
    """Runs that ended more than KEPT_DAYS ago go, with their trees; their agents' totals are counted again. A task's
    attempts stay while the task has not ended: their end state is where its next attempt starts, and its packet carries
    their questions, last words and attempt numbers. A deleted task's journal and record are pruned as
    tasks.forget says, the teammate track's offers as its own forget does, and the skill calls the app counted past
    their own KEPT_DAYS (skill_counts.forget), each in a savepoint of its own, so one failing keeps nothing of it and
    stops nothing else."""
    from . import skill_counts, task_offers, tasks
    cutoff = now - KEPT_DAYS * 86400
    agent_metrics.forget(db, now)
    memory_versions.forget(db, now)
    for name, prune in (("tasks", tasks.forget), ("task_offers", task_offers.forget),
                        ("skill_counts", skill_counts.forget)):
        db.execute("SAVEPOINT prune")
        try:
            prune(db, now)
        except Exception as e:  # noqa: BLE001  the next round tries again
            db.execute("ROLLBACK TO prune")
            print(f"agents: {name}' prune failed: {type(e).__name__}", file=sys.stderr)
        db.execute("RELEASE prune")
    old = db.execute("SELECT run_id, agent_id, user_id FROM ade_agent_runs WHERE ended_at IS NOT NULL AND ended_at < ? "
                     "AND (task_id IS NULL OR task_id NOT IN (SELECT id FROM ade_agent_tasks WHERE state NOT IN "
                     "('done', 'stopped')))", (cutoff,)).fetchall()
    if not old:
        return
    for row in old:
        db.execute("DELETE FROM ade_agent_trees WHERE run_id = ?", (row["run_id"],))
        db.execute("DELETE FROM ade_agent_payloads WHERE run_id = ?", (row["run_id"],))
        db.execute("DELETE FROM ade_agent_runs WHERE run_id = ?", (row["run_id"],))
    for agent_id, user_id in {(r["agent_id"], r["user_id"]) for r in old}:
        _totals(db, user_id, agent_id)


def start_poller(every=POLL_EVERY):
    def loop():
        while True:
            time.sleep(every)
            try:
                poll_all()
            except Exception as e:  # noqa: BLE001  the poller never takes the app down
                print(f"agents: a poll round failed: {type(e).__name__}: {e}", file=sys.stderr)
    thread = threading.Thread(target=loop, name="agents-poller", daemon=True)
    thread.start()
    return thread


# ---- keeping a chat as an agent ------------------------------------------------------------------------------------

def draft(user, session_id):
    """What Keep as an agent carries from a chat: its repository, its branch (so the agent starts from what the chat
    wrote), its settings, its task as the brief, and the tool calls the owner allowed in it, as candidate allow rules.
    Read from the running workspace, where the chat is."""
    if not isinstance(session_id, str) or not _ID.match(session_id):
        raise AgentError(404, "No such session.")
    endpoint = _endpoint(user)
    if endpoint is None:
        raise AgentError(409, "The workspace is not running: start it first.")
    session = client.call(endpoint, "GET", f"/sessions/{session_id}", timeout=60)
    if session.get("program") != "chat":
        raise AgentError(400, "Only a chat is kept as an agent.")
    allowed, after = {}, 0
    answers = {}
    for _ in range(20):
        log = client.call(endpoint, "GET", f"/sessions/{session_id}/chat?after={after}", timeout=60)
        for ev in log.get("events") or []:
            if ev.get("kind") == "ask" and isinstance(ev.get("id"), str):
                allowed[ev["id"]] = (ev.get("name"), ev.get("summary"))
            elif ev.get("kind") == "answered" and ev.get("decision") in ("allow", "allow_session"):
                answers[ev.get("id")] = True
            if isinstance(ev.get("seq"), int):
                after = max(after, ev["seq"])
        if not log.get("more"):
            break
    rules = []
    for ask_id, (name, summary) in allowed.items():
        if ask_id not in answers or not isinstance(name, str):
            continue
        rule = name if not summary or name not in ("Bash",) else f"{name}({summary})"
        if summary and summary.endswith(" ..."):
            continue                    # cut short in the log: not the command as it ran
        if _RULE.match(rule) and len(rule) <= MAX_RULE and rule not in rules:
            rules.append(rule)
    task = session.get("task") if isinstance(session.get("task"), str) else ""
    title = session.get("title") if isinstance(session.get("title"), str) else ""
    settings = session.get("chat_settings") if isinstance(session.get("chat_settings"), dict) else {}
    return {"name": (title or task.splitlines()[0] if task else "An agent")[:MAX_NAME], "brief": task[:MAX_BRIEF],
            "repo": session.get("repo"), "base_ref": session.get("branch"), "from_session": session_id,
            "chat_settings": {k: settings[k] for k in ("model", "effort", "permission_mode") if settings.get(k)},
            "candidates": rules[:MAX_RULES]}
