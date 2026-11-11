"""An agent's quality in the app: the judge's verdicts, asked of the workspace for a
sample of the runs (SAMPLE, by the run's id, so the choice is the same wherever it is made), every run the owner
labelled, and every replay and the run it replays; held to their shapes as they come back with the run's record. Judge
trust is weighed per judge version, over the account's labelled runs; drift per agent, over its judged runs of the
latest judge version, and only for a trusted judge. A replay agrees with its run when both pass or both fail their
acceptance checks and their judge scores are within REPLAY_SCORE of each other."""

import hashlib
import json
import re

from . import quality_stats as stats

SAMPLE = 10                         # one run in SAMPLE is judged, besides the labelled ones and replays
JUDGES_A_SYNC = 3
REPLAY_SCORE = 0.1
DIMENSIONS = ("task_fit", "grounding", "completeness", "hygiene", "actionable")
RULES = ("tool_repetition", "no_progress", "context_budget", "spend", "time_budget")
_VERSION = re.compile(r"^[0-9a-f]{16}$")
_ID = re.compile(r"^[0-9a-f]{12}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sampled(run_id):
    return int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16) % SAMPLE == 0


def _text(value, most):
    return value[:most] if isinstance(value, str) and not _CONTROL.search(value) else None


def clean_judge(given):
    """A verdict from the workspace held to its shapes, as JSON text, or None."""
    if not isinstance(given, dict) or not isinstance(given.get("scores"), dict):
        return None
    scores = {}
    for d in DIMENSIONS:
        v = given["scores"].get(d)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1:
            return None
        scores[d] = round(float(v), 3)
    version = given.get("version")
    if not (isinstance(version, str) and _VERSION.match(version)):
        return None
    cost = given.get("cost_est_usd")
    return json.dumps({"scores": scores, "pass": all(v >= 0.6 for v in scores.values()), "score": min(scores.values()),
                       "version": version, "why": _text(given.get("why"), 300),
                       "cost_est_usd": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                       and 0 <= cost < 100 else None})


def clean_anomalies(given):
    if not isinstance(given, list):
        return None
    out = []
    for a in given[:50]:
        if isinstance(a, dict) and a.get("rule") in RULES and a.get("level") in ("warning", "critical") and \
                isinstance(a.get("at"), int) and not isinstance(a.get("at"), bool):
            out.append({"rule": a["rule"], "level": a["level"], "at": a["at"], "evidence": _text(a.get("evidence"), 300)})
    return json.dumps(out) if out else None


def clean_acceptance(given):
    if not isinstance(given, dict) or not isinstance(given.get("pass"), bool):
        return None
    failures = [f[:200] for f in (given.get("failures") or [])[:20] if isinstance(f, str) and not _CONTROL.search(f)]
    return json.dumps({"pass": given["pass"], "failures": failures, "retried": given.get("retried") is True})


def due(db, user_id, limit=JUDGES_A_SYNC):
    """Ended runs to ask the judge about, of agents whose judge_runs is on (the judge's calls cost the owner's plan,
    so it grades nothing until the owner switches an agent on): sampled ones, labelled ones, replays and the runs
    they replay."""
    # chosen in the query itself, the sample by its id too, so a labelled run is never behind runs that are not due;
    # a run that failed is due once the owner labels it. After a promotion every run is graded until the watch has its
    # runs (improve.py), whatever judge_runs says.
    db.create_function("pp_sampled", 1, lambda run_id: 1 if isinstance(run_id, str) and sampled(run_id) else 0,
                       deterministic=True)
    rows = db.execute("SELECT r.run_id FROM ade_agent_runs r JOIN ade_agents a ON a.id = r.agent_id WHERE r.user_id = ? "
                      "AND r.loop = 0 AND r.judge IS NULL AND r.judge_asked = 0 AND r.sent = 1 AND (r.state = 'done' "
                      "OR (r.state = 'failed' AND r.thumbs IS NOT NULL)) AND (EXISTS (SELECT 1 FROM "
                      "ade_improve_proposals p WHERE p.agent_id = r.agent_id AND p.kind = 'change' AND p.state = "
                      "'approved' AND p.watched_at IS NULL AND r.ended_at >= p.approved_at) OR (a.judge_runs = 1 AND "
                      "(r.thumbs IS NOT NULL OR r.replay_of IS NOT NULL OR pp_sampled(r.run_id) = 1 OR EXISTS (SELECT 1 "
                      "FROM ade_agent_runs x WHERE x.replay_of = r.run_id AND x.user_id = r.user_id)))) ORDER BY "
                      "r.ended_at DESC LIMIT ?", (user_id, limit)).fetchall()
    return [r["run_id"] for r in rows]


def _verdicts(db, user_id, agent_id=None):
    sql = ("SELECT run_id, agent_id, judge, thumbs, ended_at FROM ade_agent_runs WHERE user_id = ? AND judge IS NOT "
           "NULL" + (" AND agent_id = ?" if agent_id else "") + " ORDER BY ended_at, run_id")
    out = []
    for r in db.execute(sql, (user_id, agent_id) if agent_id else (user_id,)):
        try:
            out.append((r, json.loads(r["judge"])))
        except ValueError:
            continue
    return out


def trust(db, user_id):
    """{version: trust} over the account's labelled, judged runs, per judge version."""
    by_version = {}
    for row, verdict in _verdicts(db, user_id):
        if row["thumbs"] in ("up", "down"):
            by_version.setdefault(verdict["version"], []).append((verdict["pass"], row["thumbs"] == "up"))
    return {version: stats.trust(pairs) for version, pairs in by_version.items()}


def drift(db, user_id, agent_id, trusts=None):
    """The agent's drift over its judged runs of the latest judge version: its level only when that judge is
    trusted (a tentative or greyed judge drives nothing), its numbers either way."""
    verdicts = _verdicts(db, user_id, agent_id)
    if not verdicts:
        return {"version": None, "band": None, "level": None, "current": None, "baseline": None, "drop": None}
    latest = verdicts[-1][1]["version"]
    got = stats.drift([v["pass"] for _, v in verdicts if v["version"] == latest])
    band = (trusts if trusts is not None else trust(db, user_id)).get(latest, {}).get("band")
    return dict(got, version=latest, band=band, level=got["level"] if band == "trusted" else None)


def _passed(acceptance):
    """Whether a run passed its acceptance checks: one with none passed them."""
    try:
        got = json.loads(acceptance) if acceptance else None
    except ValueError:
        return True
    return got["pass"] if isinstance(got, dict) and isinstance(got.get("pass"), bool) else True


def agreement(db, user_id, agent_id):
    """Its replays: how many, and of those compared with the run they replay, how many agree. A replay that started on
    another brief than its run (the brief edited while it was queued) is not compared. A pair whose acceptance checks
    went apart (one passed, one failed) disagrees, judged or not; else it is compared when both were judged, and agrees
    when their scores are within REPLAY_SCORE (a run with no checks passed them)."""
    rows = db.execute("SELECT r.judge AS again, r.acceptance AS again_ok, o.judge AS was, o.acceptance AS was_ok, "
                      "r.brief_version AS again_brief, o.brief_version AS was_brief FROM ade_agent_runs r JOIN "
                      "ade_agent_runs o ON o.run_id = r.replay_of AND o.user_id = r.user_id WHERE r.user_id = ? AND "
                      "r.agent_id = ?", (user_id, agent_id)).fetchall()
    compared = agree = 0
    for r in rows:
        if r["again_brief"] != r["was_brief"]:
            continue
        if _passed(r["again_ok"]) != _passed(r["was_ok"]):
            compared += 1
            continue
        if not (r["again"] and r["was"]):
            continue
        again, was = json.loads(r["again"]), json.loads(r["was"])
        compared += 1
        agree += abs(again["score"] - was["score"]) <= REPLAY_SCORE + 1e-9
    return {"replays": len(rows), "compared": compared, "agreement": round(agree / compared, 4) if compared else None}


IMPROVING = ("eval", "reverify", "replay", "review", "sandbox")   # the kinds of run that measure or keep up an agent


def window(db, user_id, agent_id, since):
    """The agent's quality numbers over its runs that ended since `since` (unix): the judge's, acceptance's,
    the live rules', its words' checks, its autonomy, its servers' health, its dispatch shape, its replays, and what
    measuring it cost."""
    every = db.execute("SELECT * FROM ade_agent_runs WHERE agent_id = ? AND user_id = ? AND ended_at >= ?",
                       (agent_id, user_id, since)).fetchall()
    rows = [r for r in every if not r["loop"]]          # the agent's own runs; the improve loop's are its cost alone
    judged = [json.loads(r["judge"]) for r in rows if r["judge"]]
    checked = [json.loads(r["acceptance"]) for r in rows if r["acceptance"]]
    anomalies = [a for r in rows if r["anomalies"] for a in json.loads(r["anomalies"])]
    measured = [(r, json.loads(r["metrics"]) if r["metrics"] else {}) for r in rows]
    kinds = {r["run_id"]: (json.loads(r["trigger"]) if r["trigger"] else {}).get("kind") for r in rows}
    judging = sum(j.get("cost_est_usd") or 0 for j in judged)
    # the improve loop's calls that are no run of the agent's: its critic and its pairwise judge
    calls = db.execute("SELECT COALESCE(SUM(cost_est_usd), 0) FROM ade_improve_sessions WHERE agent_id = ? AND user_id = ? "
                       "AND kind IN ('critic', 'judge') AND at >= ?", (agent_id, user_id, since)).fetchone()[0]
    loop_runs = sum(r["cost_est_usd"] or 0 for r in every if r["loop"])
    total_cost = sum(r["cost_est_usd"] or 0 for r in rows) + judging + loop_runs + calls   # all of them its cost
    improving = sum(r["cost_est_usd"] or 0 for r in rows if kinds[r["run_id"]] in IMPROVING) + judging + loop_runs + \
        calls
    coordinating = [(r, m) for r, m in measured if (m.get("dispatch") or {}).get("fan_out")]
    depths = {}
    for r, _ in coordinating:
        deepest = db.execute("WITH RECURSIVE sub(run_id, depth) AS (SELECT run_id, depth FROM ade_agent_runs WHERE "
                             "parent_run_id = ? UNION SELECT c.run_id, c.depth FROM ade_agent_runs c JOIN sub ON "
                             "c.parent_run_id = sub.run_id) SELECT MAX(depth) FROM sub", (r["run_id"],)).fetchone()[0]
        depths[r["run_id"]] = (deepest or r["depth"]) - r["depth"]
    trusts = trust(db, user_id)
    d = drift(db, user_id, agent_id, trusts)
    autonomy = [m.get("autonomy") or {} for _, m in measured if m.get("autonomy")]
    health = [m.get("mcp_health") or {} for _, m in measured if m.get("mcp_health")]
    return {
        "judged": len(judged), "judge_pass_rate": round(sum(j["pass"] for j in judged) / len(judged), 4) if judged
        else None, "judge_version": d["version"], "judge_band": d["band"],
        "drift": {k: d[k] for k in ("level", "current", "baseline", "drop")},
        "acceptance": {"checked": len(checked), "passed": sum(c["pass"] for c in checked),
                       "pass_rate": round(sum(c["pass"] for c in checked) / len(checked), 4) if checked else None},
        "anomalies": {"warning": sum(a["level"] == "warning" for a in anomalies),
                      "critical": sum(a["level"] == "critical" for a in anomalies),
                      "by_rule": {rule: sum(a["rule"] == rule for a in anomalies) for rule in RULES
                                  if any(a["rule"] == rule for a in anomalies)}},
        "claims_flagged": sum(bool((m.get("claims") or {}).get("flagged")) for _, m in measured),
        "premise_flagged": sum(bool((m.get("premise") or {}).get("flagged")) for _, m in measured),
        "autonomy": {"unattended_share": round(sum(a.get("unattended") is True for a in autonomy) / len(autonomy), 4)
                     if autonomy else None,
                     "interventions_per_run": round(sum(a.get("interventions") or 0 for a in autonomy) /
                                                    len(autonomy), 4) if autonomy else None},
        "mcp_health": {k: sum(1 for h in health if h.get(k)) for k in ("needs_auth", "failed", "disabled")} |
                      {"tool_changes": sum(h.get("tool_changes") or 0 for h in health)},
        "dispatch": {"coordinating_runs": len(coordinating),
                     "most_fan_out": max([(m.get("dispatch") or {}).get("fan_out", 0) for _, m in coordinating],
                                         default=0),
                     "deepest": max(depths.values(), default=0),
                     "waited_s": round(sum((m.get("dispatch") or {}).get("waited_s") or 0 for _, m in coordinating), 3)},
        "replays": agreement(db, user_id, agent_id),
        "cost_of_improvement": {"usd": round(improving, 6),
                                "share": round(improving / total_cost, 4) if total_cost else None},
    }
