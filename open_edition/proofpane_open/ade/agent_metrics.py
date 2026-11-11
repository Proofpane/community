"""Agents' numbers in the app. Each run's, as its workspace measured them at its end
(the workspace's metrics.py), held to their shapes, on the buckets both sides keep (shapes.py); added up by day and agent once the run has ended, with each
duration's counts on the same fixed buckets, so a week's or a month's percentile is read from the summed counts (a
day's own percentiles could not be combined); and given as CSV, under the OpenTelemetry GenAI names where one exists
(`gen_ai.invoke_agent.duration`, `gen_ai.execute_tool.duration`, `gen_ai.invoke_agent.tool_calls`)."""

import calendar
import csv
import io
import json
import re
import time

from . import shapes as measured

ENDED = ("done", "failed", "blocked", "stopped", "skipped")
TOGETHER = measured.TOGETHER
SUMS = ("runs", "done", "failed", "blocked", "stopped", "skipped", "wall_s", "active_s", "model_s", "tool_s",
        "waiting_s", "queue_s", "calls", "errors", "refused") + TOGETHER + (
        "cache_read", "cache_input", "requests", "cold_requests", "retrieval_calls", "surfaced", "cited", "cost_usd")
MAX_TOOLS = 100
MAX_TOOL_NAME = 80
TOOLS_SHOWN = 50
MAX_SOURCES = measured.MAX_SOURCES
MAX_DAYS = 366                  # a year of days
_NAME = re.compile(r"^[A-Za-z0-9_.()-][A-Za-z0-9_.() -]{0,79}$")


def _number(value, most=1e12):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < most else 0


def _counts(value):
    size = len(measured.BUCKETS) + 1
    if isinstance(value, list) and len(value) == size and all(isinstance(n, int) and not isinstance(n, bool)
                                                              and 0 <= n < 1e9 for n in value):
        return value
    return [0] * size


def clean(given):
    """A run's numbers from the workspace as JSON text, held to what each is, or None: the workspace's programs could
    write them."""
    if not isinstance(given, dict):
        return None
    t = given.get("time") if isinstance(given.get("time"), dict) else {}
    tools = {}
    for name, v in list((given.get("tools") if isinstance(given.get("tools"), dict) else {}).items())[:MAX_TOOLS]:
        if isinstance(name, str) and _NAME.match(name) and isinstance(v, dict):
            tools[name] = {"calls": int(_number(v.get("calls"), 1e7)), "errors": int(_number(v.get("errors"), 1e7)),
                           "refused": int(_number(v.get("refused"), 1e7)), "p50_ms": _number(v.get("p50_ms")) or None,
                           "p95_ms": _number(v.get("p95_ms")) or None, "hist": _counts(v.get("hist"))}
    servers = {}
    for name, v in list((given.get("servers") if isinstance(given.get("servers"), dict) else {}).items())[:MAX_TOOLS]:
        if isinstance(name, str) and _NAME.match(name) and isinstance(v, dict):
            servers[name] = {k: int(_number(v.get(k), 1e7)) for k in ("calls", "errors", "refused")}
    together = given.get("together") if isinstance(given.get("together"), dict) else {}
    cache = given.get("cache") if isinstance(given.get("cache"), dict) else {}
    retrieval = given.get("retrieval") if isinstance(given.get("retrieval"), dict) else {}
    sources = [s[:measured.MAX_SOURCE] for s in (retrieval.get("sources") or [])[:MAX_SOURCES]
               if isinstance(s, str) and s and not re.search(r"[\x00-\x1f\x7f]", s)] \
        if isinstance(retrieval.get("sources"), list) else []
    ratio = cache.get("ratio")
    return json.dumps({
        "time": {k: (_number(t.get(k)) if t.get(k) is not None else None)
                 for k in ("wall", "queue_wait", "active", "model", "tool", "waiting_owner")},
        "wall_hist": _counts(given.get("wall_hist")),
        "calls": int(_number(given.get("calls"), 1e7)), "errors": int(_number(given.get("errors"), 1e7)),
        "refused": int(_number(given.get("refused"), 1e7)), "tools": tools, "servers": servers,
        "together": {k: int(_number(together.get(k), 1e7)) for k in TOGETHER},
        "cache": {"read": int(_number(cache.get("read"))), "input": int(_number(cache.get("input"))),
                  "requests": int(_number(cache.get("requests"), 1e7)),
                  "cold_requests": int(_number(cache.get("cold_requests"), 1e7)),
                  "ratio": ratio if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and 0 <= ratio <= 1
                  else None},
        "retrieval": {"calls": int(_number(retrieval.get("calls"), 1e7)), "surfaced": len(sources),
                      "cited": min(int(_number(retrieval.get("cited"), 1e7)), len(sources)), "sources": sources},
        "answer": given["answer"][:2000] if isinstance(given.get("answer"), str) and
        not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", given["answer"]) else None,
        **_checks(given),
    })


def _line(value, most=200):
    return value[:most] if isinstance(value, str) and not re.search(r"[\x00-\x1f\x7f]", value) else None


def _names(value):
    return [n for n in (_line(v, 80) for v in value[:20]) if n] if isinstance(value, list) else []


def _checks(given):
    """The quality parts of a run's numbers, held to their shapes."""
    claims = given.get("claims") if isinstance(given.get("claims"), dict) else {}
    premise = given.get("premise") if isinstance(given.get("premise"), dict) else {}
    autonomy = given.get("autonomy") if isinstance(given.get("autonomy"), dict) else {}
    health = given.get("mcp_health") if isinstance(given.get("mcp_health"), dict) else {}
    dispatch = given.get("dispatch") if isinstance(given.get("dispatch"), dict) else {}
    return {
        "replayable": given.get("replayable") is True,
        "claims": {"flagged": claims.get("flagged") is True,
                   "standing": [{"word": _line(s.get("word"), 40), "text": _line(s.get("text"))}
                                for s in (claims.get("standing") or [])[:5] if isinstance(s, dict)]},
        "premise": {"flagged": premise.get("flagged") is True,
                    **{k: _names(premise.get(k)) for k in ("counterfactual", "governance", "request")}},
        "autonomy": {"unattended": autonomy.get("unattended") is True,
                     "interventions": int(_number(autonomy.get("interventions"), 1e6))} if autonomy else None,
        "mcp_health": {"needs_auth": _names(health.get("needs_auth")), "failed": _names(health.get("failed")),
                       "disabled": _names(health.get("disabled")),
                       "tool_changes": int(_number(health.get("tool_changes"), 1e6))} if health else None,
        "dispatch": {"fan_out": int(_number(dispatch.get("fan_out"), 1e4)),
                     "waited_s": round(_number(dispatch.get("waited_s"), 1e7), 3)},
    }


def _day(unix):
    return time.strftime("%Y-%m-%d", time.gmtime(unix))


def roll(db, user_id, agent_id, run_id, now=None):
    """An ended run added to its agent's day (its end's, in UTC): once, then marked rolled."""
    row = db.execute("SELECT state, ended_at, queued_at, cost_est_usd, metrics, rolled FROM ade_agent_runs WHERE "
                     "run_id = ? AND agent_id = ? AND user_id = ?", (run_id, agent_id, user_id)).fetchone()
    if row is None or row["rolled"] or row["state"] not in ENDED:
        return
    try:
        m = json.loads(row["metrics"]) if row["metrics"] else {}
    except ValueError:
        m = {}
    t = m.get("time") or {}
    together = m.get("together") or {}
    cache = m.get("cache") or {}
    retrieval = m.get("retrieval") or {}
    add = dict.fromkeys(SUMS, 0)
    add.update({"runs": 1, row["state"]: 1, "wall_s": t.get("wall") or 0, "active_s": t.get("active") or 0,
                "model_s": t.get("model") or 0, "tool_s": t.get("tool") or 0, "waiting_s": t.get("waiting_owner") or 0,
                "queue_s": t.get("queue_wait") or 0, "calls": m.get("calls") or 0, "errors": m.get("errors") or 0,
                "refused": m.get("refused") or 0, "cache_read": cache.get("read") or 0,
                "cache_input": cache.get("input") or 0, "requests": cache.get("requests") or 0,
                "cold_requests": cache.get("cold_requests") or 0, "retrieval_calls": retrieval.get("calls") or 0,
                "surfaced": retrieval.get("surfaced") or 0, "cited": retrieval.get("cited") or 0,
                "cost_usd": row["cost_est_usd"] or 0, **{k: together.get(k) or 0 for k in TOGETHER}})
    day = _day(row["ended_at"] or row["queued_at"] or int(now or time.time()))
    have = db.execute("SELECT wall_hist FROM ade_agent_metrics_daily WHERE agent_id = ? AND day = ?",
                      (agent_id, day)).fetchone()
    hist = measured.add_counts(json.loads(have["wall_hist"]) if have and have["wall_hist"] else None,
                               m.get("wall_hist"))
    db.execute(f"INSERT INTO ade_agent_metrics_daily (agent_id, user_id, day, {', '.join(SUMS)}, wall_hist) VALUES "
               f"(?, ?, ?, {', '.join('?' * len(SUMS))}, ?) ON CONFLICT(agent_id, day) DO UPDATE SET "
               + ", ".join(f"{k} = {k} + excluded.{k}" for k in SUMS) + ", wall_hist = excluded.wall_hist",
               (agent_id, user_id, day, *[add[k] for k in SUMS], json.dumps(hist)))
    for name, v in (m.get("tools") or {}).items():
        have = db.execute("SELECT hist FROM ade_agent_tool_daily WHERE agent_id = ? AND day = ? AND tool = ?",
                          (agent_id, day, name)).fetchone()
        summed = measured.add_counts(json.loads(have["hist"]) if have and have["hist"] else None, v.get("hist"))
        db.execute("INSERT INTO ade_agent_tool_daily (agent_id, user_id, day, tool, calls, errors, refused, hist) "
                   "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(agent_id, day, tool) DO UPDATE SET calls = calls + "
                   "excluded.calls, errors = errors + excluded.errors, refused = refused + excluded.refused, "
                   "hist = excluded.hist", (agent_id, user_id, day, name, v.get("calls") or 0, v.get("errors") or 0,
                                            v.get("refused") or 0, json.dumps(summed)))
    db.execute("UPDATE ade_agent_runs SET rolled = 1 WHERE run_id = ?", (run_id,))


def _since(days, now=None):
    now = int(now if now is not None else time.time())
    return _day(now - (max(1, min(days, MAX_DAYS)) - 1) * 86400)


def _pct(counts, q):
    got = measured.percentile(counts, q)
    return got["value"] if got else None


def window(db, user_id, agent_id, days=7, now=None):
    """An agent's numbers over its last `days` days (its runs' ends, in UTC), from the days added up."""
    since = _since(days, now)
    rows = db.execute("SELECT * FROM ade_agent_metrics_daily WHERE agent_id = ? AND user_id = ? AND day >= ?",
                      (agent_id, user_id, since)).fetchall()
    sums = {k: sum(r[k] for r in rows) for k in SUMS}
    hist = [0] * (len(measured.BUCKETS) + 1)
    for r in rows:
        hist = measured.add_counts(hist, json.loads(r["wall_hist"]) if r["wall_hist"] else None)
    tools = {}
    for r in db.execute("SELECT * FROM ade_agent_tool_daily WHERE agent_id = ? AND user_id = ? AND day >= ?",
                        (agent_id, user_id, since)):
        t = tools.setdefault(r["tool"], {"name": r["tool"], "calls": 0, "errors": 0, "refused": 0,
                                         "hist": [0] * (len(measured.BUCKETS) + 1)})
        t["calls"] += r["calls"]
        t["errors"] += r["errors"]
        t["refused"] += r["refused"]
        t["hist"] = measured.add_counts(t["hist"], json.loads(r["hist"]) if r["hist"] else None)
    ranked = sorted(tools.values(), key=lambda t: (-t["calls"], t["name"]))[:TOOLS_SHOWN]
    for t in ranked:
        t["p50_ms"], t["p95_ms"] = _pct(t["hist"], 0.5), _pct(t["hist"], 0.95)
        t.pop("hist")
    since_unix = calendar.timegm(time.strptime(since, "%Y-%m-%d"))
    worked_with = db.execute(
        "SELECT p.agent_id AS other FROM ade_agent_runs c JOIN ade_agent_runs p ON p.run_id = c.parent_run_id "
        "WHERE c.agent_id = ? AND c.user_id = ? AND c.queued_at >= ? "
        "UNION SELECT c.agent_id FROM ade_agent_runs c JOIN ade_agent_runs p ON p.run_id = c.parent_run_id "
        "WHERE p.agent_id = ? AND p.user_id = ? AND c.queued_at >= ?",
        (agent_id, user_id, since_unix, agent_id, user_id, since_unix)).fetchall()
    done, failed = sums["done"], sums["failed"]
    from . import quality
    return {
        "quality": quality.window(db, user_id, agent_id, since_unix),
        "days": days, "since": since,
        "runs": {k: sums[k] for k in ("runs", "done", "failed", "blocked", "stopped", "skipped")},
        "success_rate": round(done / (done + failed), 4) if done + failed else None,
        "cost_usd": round(sums["cost_usd"], 6),
        "cost_per_success": round(sums["cost_usd"] / done, 6) if done else None,
        "time": {"wall_s": round(sums["wall_s"], 3), "active_s": round(sums["active_s"], 3),
                 "model_s": round(sums["model_s"], 3), "tool_s": round(sums["tool_s"], 3),
                 "waiting_owner_s": round(sums["waiting_s"], 3), "queue_wait_s": round(sums["queue_s"], 3),
                 "wall_p50_s": _pct(hist, 0.5), "wall_p95_s": _pct(hist, 0.95)},
        "calls": sums["calls"], "errors": sums["errors"], "refused": sums["refused"], "tools": ranked,
        "together": dict({k: sums[k] for k in TOGETHER},
                         collaborators=len({r["other"] for r in worked_with if r["other"] != agent_id})),
        "cache": {"ratio": round(sums["cache_read"] / sums["cache_input"], 4) if sums["cache_input"] else None,
                  "requests": sums["requests"], "cold_requests": sums["cold_requests"]},
        "retrieval": {"calls": sums["retrieval_calls"], "surfaced": sums["surfaced"], "cited": sums["cited"],
                      "read_then_cited": round(sums["cited"] / sums["surfaced"], 4) if sums["surfaced"] else None},
    }


def summaries(db, user_id, days=7, now=None):
    """For the list, each agent's: its runs' p50 and p95 time, its calls, errors and refusals, its cache ratio."""
    out = {}
    for r in db.execute("SELECT * FROM ade_agent_metrics_daily WHERE user_id = ? AND day >= ?",
                        (user_id, _since(days, now))):
        s = out.setdefault(r["agent_id"], {"hist": [0] * (len(measured.BUCKETS) + 1), "calls": 0, "errors": 0,
                                           "refused": 0, "read": 0, "input": 0})
        s["hist"] = measured.add_counts(s["hist"], json.loads(r["wall_hist"]) if r["wall_hist"] else None)
        for k, field in (("calls", "calls"), ("errors", "errors"), ("refused", "refused"), ("read", "cache_read"),
                         ("input", "cache_input")):
            s[k] += r[field]
    return {agent_id: {"days": days, "wall_p50_s": _pct(s["hist"], 0.5), "wall_p95_s": _pct(s["hist"], 0.95),
                       "calls": s["calls"], "errors": s["errors"], "refused": s["refused"],
                       "cache_ratio": round(s["read"] / s["input"], 4) if s["input"] else None}
            for agent_id, s in out.items()}


COLUMNS = (("day", None), ("agent_id", None), ("agent", None), ("runs", "runs"), ("done", "done"),
           ("failed", "failed"), ("blocked", "blocked"), ("stopped", "stopped"), ("skipped", "skipped"),
           ("gen_ai.invoke_agent.duration.p50_s", None), ("gen_ai.invoke_agent.duration.p95_s", None),
           ("wall_s", "wall_s"), ("active_s", "active_s"), ("model_s", "model_s"), ("tool_s", "tool_s"),
           ("waiting_owner_s", "waiting_s"), ("queue_wait_s", "queue_s"), ("gen_ai.invoke_agent.tool_calls", "calls"),
           ("tool_errors", "errors"), ("tool_refused", "refused")) + tuple((k, k) for k in TOGETHER) + (
           ("cache_read_tokens", "cache_read"), ("cache_input_tokens", "cache_input"), ("cache_hit_ratio", None),
           ("requests", "requests"), ("cold_requests", "cold_requests"), ("retrieval_calls", "retrieval_calls"),
           ("sources_surfaced", "surfaced"), ("read_then_cited", "cited"), ("est_cost_usd", "cost_usd"))


def _cell(value):
    """A cell a spreadsheet will not read as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def csv_text(db, user_id, days=30, now=None):
    """The account's agents by day over the last `days` days, one row each, as CSV."""
    names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM ade_agents WHERE user_id = ?", (user_id,))}
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow([c for c, _ in COLUMNS])
    for r in db.execute("SELECT * FROM ade_agent_metrics_daily WHERE user_id = ? AND day >= ? ORDER BY day, agent_id",
                        (user_id, _since(days, now))):
        hist = json.loads(r["wall_hist"]) if r["wall_hist"] else None
        row = []
        for column, field in COLUMNS:
            if column == "day":
                row.append(r["day"])
            elif column == "agent_id":
                row.append(r["agent_id"])
            elif column == "agent":
                row.append(_cell(names.get(r["agent_id"], "")))
            elif column.endswith("p50_s"):
                row.append(_pct(measured.add_counts(hist, None), 0.5))
            elif column.endswith("p95_s"):
                row.append(_pct(measured.add_counts(hist, None), 0.95))
            elif column == "cache_hit_ratio":
                row.append(round(r["cache_read"] / r["cache_input"], 4) if r["cache_input"] else None)
            else:
                value = r[field]
                row.append(round(value, 6) if isinstance(value, float) else value)
        w.writerow(row)
    return out.getvalue()


def forget(db, now):
    """Days older than MAX_DAYS go."""
    cutoff = _day(int(now) - MAX_DAYS * 86400)
    db.execute("DELETE FROM ade_agent_metrics_daily WHERE day < ?", (cutoff,))
    db.execute("DELETE FROM ade_agent_tool_daily WHERE day < ?", (cutoff,))
