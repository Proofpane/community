"""The improve loop in the app: off until the owner turns it on for an agent
(`improve`), and bounded at every step.

- Scheduling: at most one review a day for the account, given to the agent with the highest-weighted open signal
  (runs since its last review: a thumbs down 1.0, a failed acceptance check 0.9, an assumed premise 0.9, a failed
  verdict of a trusted judge 0.7, a run failed on its own error or the step limit 0.6, a fragmented run or one with
  low retention 0.5, a stale question missed 0.5, an unbacked claim 0.3, a run costing more than COST_FACTOR times
  its agent's 7-day median 0.3), with a 7-day cooldown per agent, and none while a proposal is in the critic or the
  sandbox. An agent whose digest would still be longer than DIGEST_MAX after every cut digest() makes, which the
  workspace would refuse, is passed over: no review queued, no session counted, no cooldown started, the reason kept
  on its record (`review_skipped`) and measured again the next day; the day's review goes to the next by weight. The
  digest is measured again when the review's run is sent (passed_over_at_send): one grown past DIGEST_MAX since is not
  sent, and the agent is passed over so, its review's session and cooldown undone.
- The review run (the workspace's improve.py) reads a digest of the agent's last runs, its brief's newest
  DIGEST_VERSIONS versions, at most DIGEST_SKILLS of its skills, fewer of each where the digest would pass DIGEST_MAX,
  and what the loop already proposed for it (`tried`: its last DIGEST_TRIED decided proposals, with what became of each,
  a rollback naming the change it undoes) and proposes at most two changes, each to its brief, a skill, its effort, its
  compaction point or its model; or to the review prompt, which is the account's own versioned text, changed at most
  once in 30 days and only by the owner's tap.
- A change that adds a skill the agent does not hold is refused while it holds shapes.MAX_SKILLS, the most the
  workspace takes for an agent (FULL), in three places: as it is taken (one that holds a run's input word for word
  refused so first, kept out of `tried`); as its sandbox is planned, not evaluated before any run is queued or session
  counted, its treatment arm holding more skills than the workspace takes; and at the owner's tap, where the proposal
  is refused so, no longer eligible, with no time of decision, so that an equal change once there is room is not
  refused as tried before (a rollback refused so is not proposed again: watch makes one for a change). A change to a
  skill it holds, and one that takes a skill away, are taken as before.
- The pruner drops a change of more than MAX_EDITS edits, one whose text holds one of its evidence runs' prompts word
  for word kept out of `tried` (`withheld`), its reason the pruning's; a change equal to one of the agent's own
  proposals that was
  refused as written for one run, found not eligible or pruned by its sandbox, rejected by the owner or undone by a
  rollback is refused as tried before, unless the owner has approved an equal one since (a rolled-back one counts from
  the rollback that undid it) or a check before this one has refused or pruned it, and no critic is asked; the critic
  (a chat of one turn) refuses one that names a specific input, file or answer of the runs it was drawn from.
- The sandbox replays up to 8 signal runs and 8 comparison runs (recent runs that passed), all replayable, each
  twice from its own base and memory, with the current target (control) and with the change (treatment) in an order
  drawn for the proposal; the pairwise judge grades each pair's outputs in one call, blind to the arms. A run passes
  when the judge passes it and its acceptance checks, if any, passed. A pair the machine cut short says nothing and
  is dropped without asking the judge, whose call reads both runs (one the app failed at start before sending it was
  never made in the workspace); a sandbox left too few pairs so says how many said nothing. A change is eligible on
  the exact sign test (improve_stats.py); the pruner also drops one whose
  replays cost more than MAX_COST_RISE more a run than the control's.
- Approval is the owner's tap: a version is made (author the loop, with the review's model; approver the owner),
  and the change applied. A proposal not decided in 14 days expires.
- After a promotion the judge grades every run of the agent until WATCH are graded; a drop the exact Fisher test
  puts at p <= 0.05 against the previous version's last WATCH makes a rollback proposal, for the owner's tap.
- Budget: 40 loop sessions (reviews, critics and replays, and the business skills' reviews and critics, whose loop
  shares it: ade/skill_improve.py) and 20 USD est. a week for the account; the loop's sessions and their costs are
  kept, and are its part of the cost of improvement."""

import difflib
import json
import random
import secrets
import time

from . import improve_stats as stats
from . import shapes

DAY = 86400
WEEK = 7 * DAY
COOLDOWN = 7 * DAY
EXPIRES = 14 * DAY
META_EVERY = 30 * DAY
SIGNAL_DAYS = 30
BUDGET_SESSIONS = 40
BUDGET_USD = 20.0
MAX_EDITS = 3
MAX_COST_RISE = 0.20
MAX_SIGNAL = 8
MAX_COMPARISON = 8
DIGEST_RUNS = 20
DIGEST_TRIED = 20                               # the decided proposals a review's digest lists, newest first
TRIED_CUT = 600                                 # a listed value: _canonical's text, cut at so many characters
DIGEST_VERSIONS = 20                            # the brief's versions a review's digest lists: the newest, in order
DIGEST_SKILLS = shapes.MAX_SKILLS               # the agent's skills a digest shows, the first by name: as many as
#                                                 the workspace takes for an agent, the most a change may bring it to
#                                                 (FULL); one that holds more from before this rule
#                                                 shows its first DIGEST_SKILLS
DIGEST_MAX = 1024 * 1024                        # the workspace's MAX_DIGEST: check_review refuses a digest whose
#                                                 json.dumps is longer, so digest() leaves out versions, then skills,
#                                                 and maybe_review queues no review where that is not enough (SKIPPED),
#                                                 nor does passed_over_at_send send one that grew past it since
FULL = "it adds a skill to an agent holding {n}; an agent holds at most {most}"
SKIPPED = ("its digest would be {size:,} characters with every brief version and skill it may leave out left out; "
           "the workspace takes at most {most:,}")
WORD_FOR_WORD = "it names a run's own input word for word"     # the app's refusal; `tried` withholds the value it
#                                                 refused, a pruned one's that holds such an input (`withheld`), and any
#                                                 of the agent's proposals equal to either
COST_FACTOR = 1.5
RETENTION_LOW = 0.5
WEIGHTS = {"thumbs_down": 1.0, "acceptance": 0.9, "premise": 0.9, "judge_fail": 0.7, "failed": 0.6,
           "fragmented": 0.5, "stale_miss": 0.5, "claims": 0.3, "cost": 0.3}
LOOP_KINDS = ("review", "sandbox")
# what the week's sessions count, a judge's call counting in dollars: the agents' loop's three kinds and the
# business skills' loop's two (ade/skill_improve.py), one budget for both
COUNTED = ("review", "critic", "replay", "skill_review", "skill_critic")
OPEN = ("critic", "waiting", "sandbox")         # a proposal still on its way
# a proposal turned down: a change equal to one of the agent's own in one of these states, decided past its intake,
# is not tried again, unless the owner has approved an equal one since
NOT_AGAIN = ("refused", "pruned", "not_eligible", "rejected", "rolled_back")
SAID = NOT_AGAIN + ("approved",)                # the owner's word, or the loop's, on an equal change: the last decides
CAUSES_OF_ITS_OWN = ("error_result", "limit", "acceptance")
REVIEW_PROMPT = ("Review this agent's recent runs, and propose what would make them pass more often. A change is to its "
                 "brief, one of its skills (a SKILL.md whose frontmatter holds name, description and when_to_use "
                 "only), its effort, its compaction point or its model; never its permissions, tools, code, limits, "
                 "memory or knowledge. Draw on what its runs share, never on one run's input, file or answer: a "
                 "change written for one run is refused. Weigh the brief's versions and their numbers: a change "
                 "tried before and undone is not proposed again unless the owner has approved it since. The digest's "
                 "`tried` lists what was proposed before and what became of each: a change equal to one refused as "
                 "written for one run, found not eligible or pruned by its sandbox, rejected by the owner or undone by "
                 "a rollback is refused as it is taken, unless the owner has approved an equal one since (an undone "
                 "change counting from the rollback that undid it, whose entry names it), and one like such a change "
                 "is proposed only when the runs since give a reason it would fare otherwise.")


class ImproveError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def _json(text, default=None):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _kind(row):
    return (_json(row["trigger"], {}) or {}).get("kind")


# ---- the agent's improvable parts: what each is now, and the account's review prompt --------------------------------

def skills(db, agent_id):
    return {r["name"]: r["content"] for r in db.execute("SELECT name, content FROM ade_agent_skills WHERE agent_id = ? "
                                                         "ORDER BY name", (agent_id,))}


def current(db, agent, target, name=None):
    """The target's value now: the brief, a skill's SKILL.md (None when it has none), the effort, the model, the
    compaction point, or the review prompt."""
    settings = _json(agent["chat_settings"], {}) or {}
    if target == "brief":
        return agent["brief"]
    if target == "skill":
        return skills(db, agent["id"]).get(name)
    if target in ("effort", "model"):
        return settings.get(target)
    if target == "autocompact_window":
        return agent["autocompact_window"]
    return review_prompt(db, agent["user_id"])["prompt"]


def review_prompt(db, user_id):
    """The account's review prompt: its latest version, or the first."""
    row = db.execute("SELECT version, prompt FROM ade_improve_prompts WHERE user_id = ? ORDER BY version DESC LIMIT 1",
                     (user_id,)).fetchone()
    return {"version": row["version"], "prompt": row["prompt"]} if row else {"version": 1, "prompt": REVIEW_PROMPT}


def settings_now(db, agent):
    """The control arm: the agent's brief, skills, effort, model and compaction point as they are."""
    s = _json(agent["chat_settings"], {}) or {}
    return {"brief": agent["brief"], "skills": skills(db, agent["id"]),
            "chat_settings": {k: s[k] for k in ("model", "effort") if s.get(k)},
            "autocompact_window": agent["autocompact_window"]}


def with_change(arm, target, name, value):
    """An arm with one change applied (the treatment)."""
    out = json.loads(json.dumps(arm))
    if target == "brief":
        out["brief"] = value
    elif target == "skill":
        out["skills"][name] = value
    elif target in ("effort", "model"):
        out["chat_settings"][target] = value
    elif target == "autocompact_window":
        out["autocompact_window"] = value
    return out


# ---- signals ---------------------------------------------------------------------------------------------------------

def _median_cost(db, agent_id, now):
    """The median cost of the agent's work runs done in the week: never a run that measures or keeps it up."""
    from .quality import IMPROVING
    costs = sorted(r["cost_est_usd"] for r in db.execute(
        "SELECT cost_est_usd, trigger FROM ade_agent_runs WHERE agent_id = ? AND state = 'done' AND ended_at >= ? AND "
        "cost_est_usd IS NOT NULL", (agent_id, now - WEEK)) if _kind(r) not in IMPROVING)
    if not costs:
        return None
    mid = len(costs) // 2
    return costs[mid] if len(costs) % 2 else (costs[mid - 1] + costs[mid]) / 2


def _trusted(db, user_id):
    from . import quality
    return {v for v, t in quality.trust(db, user_id).items() if t.get("band") == "trusted"}


def signals(db, row, trusted=None, median=None, stale=None):
    """The signals of an ended run, by name."""
    found = []
    if row["thumbs"] == "down":
        found.append("thumbs_down")
    acceptance = _json(row["acceptance"])
    if acceptance and acceptance.get("pass") is False:
        found.append("acceptance")
    metrics = _json(row["metrics"], {}) or {}
    if (metrics.get("premise") or {}).get("flagged"):
        found.append("premise")
    judge = _json(row["judge"])
    if judge and judge.get("pass") is False and judge.get("version") in (trusted or ()):
        found.append("judge_fail")
    if row["state"] == "failed" and (row["cause"] == "error_result" or
                                     (row["cause"] == "limit" and "step limit" in (row["reason"] or ""))):
        found.append("failed")
    context = _json(row["context"], {}) or {}
    if context.get("fragmented") or (isinstance(context.get("retention"), (int, float)) and
                                     context["retention"] < RETENTION_LOW):
        found.append("fragmented")
    if row["run_id"] in (stale or ()):
        found.append("stale_miss")
    if (metrics.get("claims") or {}).get("flagged"):
        found.append("claims")
    if row["state"] == "done" and median and (row["cost_est_usd"] or 0) > COST_FACTOR * median:
        found.append("cost")
    return found


def weight(found):
    return max((WEIGHTS[s] for s in found), default=0.0)


def _stale_misses(db, agent_id):
    return {r["run_id"] for r in db.execute(
        "SELECT i.run_id FROM ade_agent_eval_items i JOIN ade_agent_evals e ON e.id = i.eval_id WHERE e.agent_id = ? "
        "AND i.grp = 'stale' AND i.state = 'scored' AND i.hit_at_5 = 0 AND i.run_id IS NOT NULL", (agent_id,))}


def runs_with_signals(db, agent, since, now):
    """[(run row, its signals, its weight)] of the agent's own ended runs since `since`, newest first."""
    trusted, median, stale = _trusted(db, agent["user_id"]), _median_cost(db, agent["id"], now), \
        _stale_misses(db, agent["id"])
    out = []
    for row in db.execute("SELECT * FROM ade_agent_runs WHERE agent_id = ? AND ended_at >= ? AND state IN "
                          "('done', 'failed') ORDER BY ended_at DESC, run_id", (agent["id"], since)):
        if _kind(row) in LOOP_KINDS:
            continue
        found = signals(db, row, trusted, median, stale)
        out.append((row, found, weight(found)))
    return out


# ---- the budget and the sessions --------------------------------------------------------------------------------------

def budget(db, user_id, now):
    """The week's loop sessions and their est. cost, and whether either is spent."""
    row = db.execute(f"SELECT COUNT(CASE WHEN kind IN ({','.join('?' * len(COUNTED))}) THEN 1 END) AS n, "
                     "COALESCE(SUM(cost_est_usd), 0) AS usd FROM ade_improve_sessions WHERE user_id = ? AND at >= ?",
                     (*COUNTED, user_id, now - WEEK)).fetchone()
    return {"sessions": row["n"], "usd": round(row["usd"], 6), "max_sessions": BUDGET_SESSIONS, "max_usd": BUDGET_USD,
            "spent": row["n"] >= BUDGET_SESSIONS or row["usd"] >= BUDGET_USD}


def session(db, user_id, agent_id, kind, now, run_id=None, proposal_id=None, cost=0.0):
    db.execute("INSERT INTO ade_improve_sessions (id, user_id, agent_id, kind, run_id, proposal_id, cost_est_usd, at) "
               "VALUES (?,?,?,?,?,?,?,?)", (secrets.token_hex(6), user_id, agent_id, kind, run_id, proposal_id,
                                            float(cost or 0.0), now))


def run_ended(db, run_id, cost):
    """A loop run's est. cost, kept on its session at its end."""
    db.execute("UPDATE ade_improve_sessions SET cost_est_usd = ? WHERE run_id = ?", (float(cost or 0.0), run_id))


# ---- the review --------------------------------------------------------------------------------------------------------

def maybe_review(db, user, now, new_run):
    """The day's review for the account, queued for the agent with the highest-weighted open signal, when one is due:
    its run id, or None. An agent whose digest would still be longer than DIGEST_MAX after every cut digest() makes is
    passed over, the workspace's check_review refusing such a digest: no run, no session, no cooldown; its record
    keeps when and why (`review_skipped`, the last such measurement), and it is not measured again until the next day
    (the day the account's one review is counted by), when it may fit. The review goes to the next agent by weight,
    and the one it goes to has the reason cleared. The digest is measured here, when the review would be queued, and
    again when the run is sent (passed_over_at_send), where one grown past DIGEST_MAX in between is not sent and the
    agent is passed over as here, its review's session and cooldown undone: the run's trigger keeps the agent's
    reviewed_at from before this review (`reviewed_before`) for that."""
    user_id = user["id"]
    if budget(db, user_id, now)["spent"]:
        return None
    today = now - now % DAY
    if db.execute("SELECT 1 FROM ade_improve_sessions WHERE user_id = ? AND kind = 'review' AND at >= ?",
                  (user_id, today)).fetchone():
        return None                                 # one review a day for the account
    if db.execute("SELECT 1 FROM ade_improve_proposals WHERE user_id = ? AND state IN ('critic', 'waiting', 'sandbox')",
                  (user_id,)).fetchone():
        return None                                 # one proposal on its way at a time
    due = []
    for agent in db.execute("SELECT * FROM ade_agents WHERE user_id = ? AND improve = 1 AND retired_at IS NULL AND "
                            "paused = 0 ORDER BY reviewed_at, id", (user_id,)).fetchall():
        if agent["reviewed_at"] and now - agent["reviewed_at"] < COOLDOWN:
            continue
        since = max(agent["reviewed_at"] or 0, now - WEEK)
        score = round(sum(w for _, _, w in runs_with_signals(db, agent, since, now)), 6)
        if score > 0:
            due.append((agent, score))
    for agent, _ in sorted(due, key=lambda d: -d[1]):  # by weight; equal weights in the order read, as before
        skipped = _json(agent["review_skipped"], {})
        if isinstance(skipped, dict) and isinstance(skipped.get("at"), int) and skipped["at"] >= today:
            continue                                # passed over today already: measured again tomorrow
        size = len(json.dumps(digest(db, agent)))   # as check_review measures it
        if size > DIGEST_MAX:
            db.execute("UPDATE ade_agents SET review_skipped = ? WHERE id = ?",
                       (json.dumps({"at": now, "reason": SKIPPED.format(size=size, most=DIGEST_MAX)}), agent["id"]))
            continue
        run_id = new_run(db, agent, {"kind": "review", "at": now, "reviewed_before": agent["reviewed_at"]}, now,
                         loop=True)
        session(db, user_id, agent["id"], "review", now, run_id=run_id)
        db.execute("UPDATE ade_agents SET reviewed_at = ?, review_skipped = NULL WHERE id = ?", (now, agent["id"]))
        return run_id
    return None


def _canonical(value):
    """A value as json.dumps gives it with its keys sorted at every level and its characters outside ASCII escaped:
    two values that differ only in the order of their keys give the same text. Not RFC 8785's canonical JSON."""
    return json.dumps(value, sort_keys=True)


def tried(db, agent):
    """What the loop already proposed for the agent and what became of it: its own decided proposals (any state but
    OPEN's), newest first by when they were decided and then by when they were made, at most DIGEST_TRIED; one with no
    decided_at (refused or pruned as it was taken, refused for the agent's skills at the owner's tap, or a rollback
    waiting for the owner's tap) takes its place by when it was made. Each with its kind and, for a rollback, the id
    of the change it undoes (`rolls_back`): a rollback is made,
    and decided, after the change it undoes was decided, so a rolled-back change listed comes with its rollback, whose
    hypothesis and numbers say why; but a rollback can be listed once that change is past DIGEST_TRIED, and the value it
    undid is then in no entry (it is the rollback's value before, never shown). Its proposed value as _canonical gives
    it, cut at TRIED_CUT characters of that text (a character outside ASCII takes six of them, twelve outside the BMP,
    and the cut can split an escape), but none (null) for a value the app refused because a run's own input is in it
    word for word (WORD_FOR_WORD), for one pruned as it was taken for more than MAX_EDITS edits whose text holds one of
    its evidence runs' prompts (`withheld`, marked as it was taken, its reason staying the pruning's; one kept before
    that mark existed, `withheld` reading nothing, is checked here, while its runs are there), and for any of the
    agent's proposals equal to such a value (a later review's twin, refused as tried before, naming it): the run's input
    stays out of `tried`, though a hypothesis is listed as written. And of its result the numbers it holds; never its
    evidence, its value before or its sandbox."""
    out = []
    withheld = set()
    for r in db.execute("SELECT state, proposed, evidence, withheld FROM ade_improve_proposals WHERE user_id = ? AND "
                        "agent_id = ? AND ((state = 'refused' AND reason = ?) OR withheld = 1 OR (state = 'pruned' AND "
                        "withheld IS NULL))", (agent["user_id"], agent["id"], WORD_FOR_WORD)):
        value = _json(r["proposed"])
        if r["state"] == "pruned" and r["withheld"] is None and \
                _verbatim(db, agent["id"], value, (_json(r["evidence"], {}) or {}).get("runs")) is None:
            continue                                # kept before `withheld` was, and holding no run's prompt
        withheld.add(_canonical(value))
    for p in db.execute("SELECT * FROM ade_improve_proposals WHERE user_id = ? AND agent_id = ? AND state NOT IN "
                        f"({','.join('?' * len(OPEN))}) ORDER BY COALESCE(decided_at, created_at) DESC, "
                        "created_at DESC, id LIMIT ?", (agent["user_id"], agent["id"], *OPEN, DIGEST_TRIED)):
        value = _canonical(_json(p["proposed"]))
        hidden = value in withheld
        if len(value) > TRIED_CUT:
            cut = len(value) - TRIED_CUT
            value = f"{value[:TRIED_CUT]} [{cut} character{'' if cut == 1 else 's'} cut]"
        result = _json(p["result"], {})
        numbers = {k: v for k, v in result.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} \
            if isinstance(result, dict) else {}
        if hidden:
            value = None                        # the run's input stays out of `tried`; a reason says why
        out.append({"id": p["id"], "kind": p["kind"], "rolls_back": p["rolls_back"], "target": p["target"],
                    "name": p["name"], "hypothesis": p["hypothesis"], "state": p["state"], "reason": p["reason"],
                    "edits": p["edits"], "result": numbers, "decided_at": p["decided_at"], "proposed": value})
    return out


def digest(db, agent):
    """What a review reads (the app's part; the workspace adds each run's final message and tree): its last runs with
    their verdicts and numbers, its brief's newest DIGEST_VERSIONS versions with theirs (oldest first), what the loop
    already proposed for it and what became of each (`tried`), and what it is now, with at most DIGEST_SKILLS of its
    skills, the first by name: the most the workspace takes for an agent, and the most a change may bring it to (FULL),
    though one that holds more from before this rule shows its first. Fewer of each where the digest would be longer
    than DIGEST_MAX as the workspace's check_review measures it (json.dumps, which writes a character outside ASCII as
    six characters and one outside the BMP as twelve): the oldest versions are left out first, then the last skills by
    name, one at a time until it fits; `left_out` says how many of the brief's versions and which skills it does not
    hold. Where the rest alone is longer, nothing more is left out, and maybe_review queues no review for the agent
    (SKIPPED), which the workspace would refuse."""
    now = int(time.time())
    trusted, median, stale = _trusted(db, agent["user_id"]), _median_cost(db, agent["id"], now), \
        _stale_misses(db, agent["id"])
    runs, by_version = [], {}
    for row in db.execute("SELECT * FROM ade_agent_runs WHERE agent_id = ? AND state IN ('done', 'failed') ORDER BY "
                          "ended_at DESC LIMIT ?", (agent["id"], DIGEST_RUNS * 2)):
        if _kind(row) in LOOP_KINDS or len(runs) >= DIGEST_RUNS:
            continue
        metrics = _json(row["metrics"], {}) or {}
        runs.append({"run_id": row["run_id"], "state": row["state"], "cause": row["cause"], "reason": row["reason"],
                     "trigger": _kind(row), "brief_version": row["brief_version"], "thumbs": row["thumbs"],
                     "note": row["thumbs_note"], "judge": _json(row["judge"]), "acceptance": _json(row["acceptance"]),
                     "anomalies": _json(row["anomalies"]), "cost_est_usd": row["cost_est_usd"],
                     "context": {k: (_json(row["context"], {}) or {}).get(k) for k in ("fragmented", "retention")},
                     "numbers": {k: metrics.get(k) for k in ("time", "calls", "errors", "claims", "premise",
                                                             "autonomy")},
                     "signals": signals(db, row, trusted, median, stale)})
    # the agent's own runs: the loop's (a review, a sandbox's arms on a proposed brief) are no run of a brief version
    for row in db.execute("SELECT brief_version, state, judge FROM ade_agent_runs WHERE agent_id = ? AND loop = 0 AND "
                          "state IN ('done', 'failed')", (agent["id"],)):
        v = by_version.setdefault(row["brief_version"], {"runs": 0, "done": 0, "judged": 0, "passed": 0})
        v["runs"] += 1
        v["done"] += row["state"] == "done"
        verdict = _json(row["judge"])
        if verdict:
            v["judged"] += 1
            v["passed"] += verdict.get("pass") is True
    versions = []
    for row in reversed(db.execute("SELECT version, value, author, created_at FROM ade_agent_versions WHERE agent_id = ? "
                                   "AND target = 'brief' ORDER BY version DESC LIMIT ?",
                                   (agent["id"], DIGEST_VERSIONS)).fetchall()):
        versions.append({"version": row["version"], "brief": _json(row["value"]), "author": row["author"],
                         "at": row["created_at"], "numbers": by_version.get(row["version"])})
    every = db.execute("SELECT COUNT(*) FROM ade_agent_versions WHERE agent_id = ? AND target = 'brief'",
                       (agent["id"],)).fetchone()[0]
    now_ = settings_now(db, agent)
    shown = dict(list(now_["skills"].items())[:DIGEST_SKILLS])
    out = {"runs": runs, "brief_versions": versions, "tried": tried(db, agent),
           "current": {"brief": now_["brief"], "skills": shown,
                       "effort": now_["chat_settings"].get("effort"),
                       "model": now_["chat_settings"].get("model"),
                       "autocompact_window": now_["autocompact_window"]}}
    while True:                                 # to what the workspace takes: oldest versions first, then last skills
        out["left_out"] = {"brief_versions": every - len(versions),
                           "skills": [name for name in now_["skills"] if name not in shown]}
        if len(json.dumps(out)) <= DIGEST_MAX or not (versions or shown):
            return out
        if versions:
            versions.pop(0)
        else:
            shown.pop(next(reversed(shown)))


def review_body(db, user_id, agent):
    return {"prompt": review_prompt(db, user_id)["prompt"], "digest": digest(db, agent)}


def passed_over_at_send(db, agent, run_row, body, now):
    """A queued review's digest measured again as its run is sent (agents._send_now), as check_review measures it:
    one grown past DIGEST_MAX since maybe_review queued the review is not sent. The run, if still queued and unsent,
    ends failed at start, its reason SKIPPED, and the agent is passed over as maybe_review passes it over (its
    `review_skipped`, this measurement), the review's session and cooldown undone: the session is taken away, so the
    day's review may go to the next agent by weight, and its reviewed_at is put back to what it was before this review,
    kept in the run's trigger, while it is still this review's; a review queued before this change keeps no such value,
    and its agent's reviewed_at stays as the review set it. A run no longer queued and unsent (the owner's stop) is
    left as it is, its session and cooldown with it. True when it is not sent."""
    size = len(json.dumps(body["digest"]))
    if size <= DIGEST_MAX:
        return False
    said = SKIPPED.format(size=size, most=DIGEST_MAX)
    if db.execute("UPDATE ade_agent_runs SET state = 'failed', cause = 'start', reason = ?, ended_at = ?, updated_at = "
                  "? WHERE run_id = ? AND state = 'queued' AND sent = 0",
                  (said, now, now, run_row["run_id"])).rowcount != 1:
        return True                                 # stopped meanwhile: not sent, and the stop stands as it left it
    trigger = _json(run_row["trigger"], {}) or {}
    db.execute("DELETE FROM ade_improve_sessions WHERE run_id = ? AND kind = 'review'", (run_row["run_id"],))
    db.execute("UPDATE ade_agents SET review_skipped = ? WHERE id = ?",
               (json.dumps({"at": now, "reason": said}), agent["id"]))
    if "reviewed_before" in trigger:                # one queued before this change does not say what it was
        db.execute("UPDATE ade_agents SET reviewed_at = ? WHERE id = ? AND reviewed_at = ?",
                   (trigger["reviewed_before"], agent["id"], trigger.get("at")))
    return True


# ---- proposals: taken, pruned, criticised -----------------------------------------------------------------------------

def take(db, user_id, now):
    """The changes of the account's ended reviews, each once: a proposal each, refused, pruned or on to the critic."""
    taken = []
    for row in db.execute("SELECT run_id, agent_id, changes FROM ade_agent_runs WHERE user_id = ? AND state IN "
                          "('done', 'failed') AND changes IS NOT NULL AND changes_taken = 0", (user_id,)).fetchall():
        agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (row["agent_id"],)).fetchone()
        for change in (_json(row["changes"], []) or [])[:2]:
            taken.append(_propose(db, user_id, agent, row["run_id"], change, now))
        db.execute("UPDATE ade_agent_runs SET changes_taken = 1 WHERE run_id = ?", (row["run_id"],))
    return taken


def _propose(db, user_id, agent, review_run, change, now):
    target, name, value = change["target"], change.get("name"), change["value"]
    meta = target == "review_prompt"
    was = current(db, agent, target, name)
    state, reason, edits = ("eligible" if meta else "critic"), None, stats.edits(was, value)
    full = None if meta else _full(db, agent["id"], target, name, value)
    withheld = None                             # marked for a pruned change alone: 1 when it holds a run's input
    if meta and db.execute("SELECT 1 FROM ade_improve_proposals WHERE user_id = ? AND kind = 'meta' AND created_at >= ? "
                           "AND reason IS NOT 'one change to the review prompt a month'",
                           (user_id, now - META_EVERY)).fetchone():
        state, reason = "refused", "one change to the review prompt a month"
    elif edits == 0:
        state, reason = "refused", "it changes nothing"
    elif edits > MAX_EDITS:                     # pruned, its reason kept; one that holds a run's input word for word
        #                                         is kept out of `tried` too (withheld), as one refused for that is
        state, reason = "pruned", f"it bundles {edits} edits; a proposal makes at most {MAX_EDITS}"
        withheld = int(_verbatim(db, agent["id"], value, (change.get("evidence") or {}).get("runs")) is not None)
    elif full is not None:                      # a skill past what the workspace takes for an agent; one that holds
        #                                         a run's input word for word is refused so first, kept out of `tried`
        verbatim = _verbatim(db, agent["id"], value, (change.get("evidence") or {}).get("runs"))
        state, reason = "refused", WORD_FOR_WORD if verbatim else FULL.format(n=full, most=shapes.MAX_SKILLS)
    if state == "critic":                       # on its way to the critic: refused here when tried and turned down
        before = _tried_before(db, user_id, agent["id"], target, name, value)
        if before is not None:
            state, reason = "refused", f"tried before: proposal {before['id']}, {before['state']}"
    pid = secrets.token_hex(6)
    # no decided_at, whatever the state: _tried_before does not count a decision made here, and tells it by that: one
    # refused as changing nothing or pruned as bundling too many edits was weighed only against the value then
    # current, one refused for the agent's skills (FULL) against what the agent held then, and one refused as tried
    # before names the earlier decision, which counts itself
    db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, review_run_id, kind, target, name, current, "
               "proposed, hypothesis, evidence, edits, state, reason, created_at, withheld) VALUES "
               "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (pid, user_id, None if meta else agent["id"], review_run, "meta" if meta else "change", target, name,
                json.dumps(was), json.dumps(value), change.get("hypothesis"), json.dumps(change.get("evidence") or {}),
                edits, state, reason, now, withheld))
    return pid


def _full(db, agent_id, target, name, value):
    """How many skills the agent holds, when the change would add one it does not hold while it holds
    shapes.MAX_SKILLS or more, the most the workspace takes for an agent; else None. A change to a skill it holds, and
    one that takes a skill away (a rollback's, its value None), adds none."""
    if target != "skill" or value is None:
        return None
    held = skills(db, agent_id)
    return len(held) if name not in held and len(held) >= shapes.MAX_SKILLS else None


def _tried_before(db, user_id, agent_id, target, name, value):
    """The agent's own proposal of the same change (its target, its name and its value as _canonical gives it) that was
    turned down past its intake (NOT_AGAIN): refused as written for one run (by the critic, or by the app's
    word-for-word check before it), found not eligible or pruned by its sandbox, rejected by the owner (a rollback
    among them) or rolled back; None when there is none, or when the owner has approved an equal one since. Of the
    agent's equal proposals turned down or approved past their intake, the one decided last decides (then the one made
    last, then the lower id): approved, and nothing is refused; turned down, and it is the one returned. A rolled-back
    proposal keeps the decided_at of its approval, which the rollback that undid it does not change, so it counts from
    when that rollback was approved (from its own decided_at where no approved rollback names it). An approved rollback
    is an approval of the value it restores. Another agent's proposal, and one expired, not evaluated, eligible or still
    on its way, is no reason either way; nor is a decision with no decided_at, one made as a change was taken or a
    refusal of the count at the owner's tap: one refused as changing nothing or pruned as bundling too many edits was
    weighed against the value then current, never on its own, and its twin reaches this check only once that value has
    changed; one refused for the agent's skills (FULL), as it was taken or at the owner's tap, was weighed against what
    the agent held, and its twin reaches this check only once the agent has room; one refused as tried before names the
    decision it repeats, which is counted itself."""
    want = _canonical(value)
    words = []
    for p in db.execute("SELECT id, state, proposed, decided_at, created_at FROM ade_improve_proposals WHERE user_id = ? "
                        f"AND agent_id = ? AND target = ? AND name IS ? AND state IN ({','.join('?' * len(SAID))}) AND "
                        "decided_at IS NOT NULL", (user_id, agent_id, target, name, *SAID)):
        try:
            if _canonical(json.loads(p["proposed"])) != want:
                continue
        except (TypeError, ValueError):     # no value kept, or not JSON: nothing to compare with
            continue
        when = p["decided_at"]
        if p["state"] == "rolled_back":
            undone = db.execute("SELECT MAX(decided_at) FROM ade_improve_proposals WHERE user_id = ? AND kind = "
                                "'rollback' AND rolls_back = ? AND state = 'approved'", (user_id, p["id"])).fetchone()[0]
            when = undone if undone is not None else when
        words.append((-when, -p["created_at"], p["id"], p))
    if not words:
        return None
    last = min(words)[3]
    return None if last["state"] == "approved" else last


def _verbatim(db, agent_id, value, runs):
    """The first of the agent's runs' own prompts (of at most 20 runs; each prompt of 12 characters or more once
    stripped) that the value, a text, holds word for word; else None. The app's check before the critic; where FULL
    refuses a change as it is taken, so that a run's input never reaches `tried` through a refusal of the count; where
    a change is pruned for its edits, marking it `withheld`; and in `tried`, for one pruned before that mark existed."""
    runs = list(runs or [])[:20]
    prompts = [(_json(r["trigger"], {}) or {}).get("prompt") for r in db.execute(
        f"SELECT trigger FROM ade_agent_runs WHERE agent_id = ? AND run_id IN ({','.join('?' * len(runs))})",
        (agent_id, *runs))] if runs else []
    text = value if isinstance(value, str) else ""
    return next((q for q in prompts if isinstance(q, str) and len(q.strip()) >= 12 and q.strip() in text), None)


def criticise(db_connect, user, endpoint, call, now):
    """Each proposal waiting for the critic: refused when a run's own input is in it word for word, or when the
    critic says it names a specific input, file or answer; else on to the sandbox."""
    with db_connect() as db:
        rows = db.execute("SELECT * FROM ade_improve_proposals WHERE user_id = ? AND state = 'critic' ORDER BY "
                          "created_at", (user["id"],)).fetchall()
    for p in rows:
        if budget_spent(db_connect, user["id"], now):
            return
        change = {"target": p["target"], "name": p["name"], "value": _json(p["proposed"]),
                  "hypothesis": p["hypothesis"], "evidence": _json(p["evidence"], {})}
        runs = [r for r in (change["evidence"].get("runs") or [])][:20]
        with db_connect() as db:
            verbatim = _verbatim(db, p["agent_id"], change["value"], runs)
            runs = [r["run_id"] for r in db.execute(
                f"SELECT run_id FROM ade_agent_runs WHERE agent_id = ? AND run_id IN ({','.join('?' * len(runs))})",
                (p["agent_id"], *runs))] if runs else []
        if verbatim:
            _decide(db_connect, p["id"], "critic", "refused", WORD_FOR_WORD, now)
            continue
        try:
            got = call(endpoint, "POST", f"/agents/runs/{p['review_run_id']}/critic",
                       {"change": change, "inputs": runs}, timeout=300)
        except Exception as e:  # noqa: BLE001  the critic is asked again at the next sync
            print(f"agents: the critic of {p['id']} failed: {e}", file=__import__("sys").stderr)
            continue
        with db_connect() as db:
            session(db, user["id"], p["agent_id"], "critic", now, proposal_id=p["id"],
                    cost=got.get("cost_est_usd") if isinstance(got, dict) else 0)
        if isinstance(got, dict) and got.get("specific") is True:
            _decide(db_connect, p["id"], "critic", "refused", "the critic: " + str(
                got.get("why") or "it names a specific run's input, file or answer")[:200], now)
        else:
            _decide(db_connect, p["id"], "critic", "waiting", None, now)


def budget_spent(db_connect, user_id, now):
    with db_connect() as db:
        return budget(db, user_id, now)["spent"]


def _decide(db_connect, pid, was, state, reason, now, **more):
    """A proposal's state from one of the loop's own steps, set only while it is still as the step found it (`was`):
    the owner's word, or its expiry, given while the step's call took its time stands."""
    with db_connect() as db:
        sets = ", ".join(f"{k} = ?" for k in more)
        db.execute("UPDATE ade_improve_proposals SET state = ?, reason = ?, decided_at = ?" + (", " + sets if sets else "")
                   + " WHERE id = ? AND state = ?", (state, reason, now, *more.values(), pid, was))


# ---- the sandbox ---------------------------------------------------------------------------------------------------------

def _replayable(row):
    return (_json(row["metrics"], {}) or {}).get("replayable") is True and row["base"] is not None


def plan_sandbox(db, user, now, new_run):
    """The next waiting proposal's sandbox, when none is in one: its inputs fixed (signal runs first, then comparison
    runs), each queued twice in an order drawn for the proposal. Its id, or None. One that would add a skill the agent
    does not hold, while it holds shapes.MAX_SKILLS (FULL), is not evaluated, before any run is queued or session
    counted: its treatment arm would hold more skills than the workspace's check_sandbox takes."""
    user_id = user["id"]
    if db.execute("SELECT 1 FROM ade_improve_proposals WHERE user_id = ? AND state = 'sandbox'", (user_id,)).fetchone():
        return None
    p = db.execute("SELECT * FROM ade_improve_proposals WHERE user_id = ? AND state = 'waiting' ORDER BY created_at "
                   "LIMIT 1", (user_id,)).fetchone()
    if p is None:
        return None
    agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (p["agent_id"],)).fetchone()
    full = _full(db, agent["id"], p["target"], p["name"], _json(p["proposed"]))
    if full is not None:                            # its treatment arm would hold more skills than the workspace takes
        db.execute("UPDATE ade_improve_proposals SET state = 'not_evaluated', reason = ?, decided_at = ? WHERE id = ? "
                   "AND state = 'waiting'", (FULL.format(n=full, most=shapes.MAX_SKILLS), now, p["id"]))
        return None
    rows = runs_with_signals(db, agent, now - SIGNAL_DAYS * DAY, now)
    evidence = set((_json(p["evidence"], {}) or {}).get("runs") or [])
    signal = sorted([(r, w) for r, _, w in rows if w > 0 and _replayable(r)],
                    key=lambda x: (x[0]["run_id"] not in evidence, -x[1], -(x[0]["ended_at"] or 0)))[:MAX_SIGNAL]
    comparison = [r for r, found, w in rows if w == 0 and r["state"] == "done" and _replayable(r)][:MAX_COMPARISON]
    why = stats.too_few(len(signal), len(comparison))
    if why:
        db.execute("UPDATE ade_improve_proposals SET state = 'not_evaluated', reason = ?, decided_at = ? WHERE id = ? "
                   "AND state = 'waiting'", (why, now, p["id"]))
        return None
    left = budget(db, user_id, now)
    needed = 2 * (len(signal) + len(comparison))
    if left["spent"] or left["sessions"] + needed > BUDGET_SESSIONS:
        return None                                 # waits for the week's budget
    # claimed first, under the store's write lock: one the owner rejected since it was read gets no runs
    if db.execute("UPDATE ade_improve_proposals SET state = 'sandbox' WHERE id = ? AND state = 'waiting'",
                  (p["id"],)).rowcount != 1:
        return None
    rng = random.Random(p["id"])
    inputs = []
    for group, chosen in (("signal", [r for r, _ in signal]), ("comparison", comparison)):
        for row in chosen:
            first = rng.choice(("control", "treatment"))
            made = {}
            for arm in (first, "treatment" if first == "control" else "control"):
                made[arm] = new_run(db, agent, {"kind": "sandbox", "of": row["run_id"], "arm": arm,
                                                "proposal": p["id"], "at": now}, now, loop=True)
                session(db, user_id, agent["id"], "replay", now, run_id=made[arm], proposal_id=p["id"])
            inputs.append({"run_id": row["run_id"], "group": group, "control": made["control"],
                           "treatment": made["treatment"], "first": first})
    db.execute("UPDATE ade_improve_proposals SET sandbox = ? WHERE id = ?", (json.dumps({"inputs": inputs}), p["id"]))
    return p["id"]


def memory_before(db, agent_id, at):
    """The agent's memory as its last version made at or before `at` had it, its text files ({} for none): what a
    sandbox run and a replay start with."""
    memory = db.execute("SELECT files FROM ade_agent_memory WHERE agent_id = ? AND made_at <= ? ORDER BY version DESC "
                        "LIMIT 1", (agent_id, at)).fetchone()
    return {k: v for k, v in (_json(memory["files"], {}) if memory else {}).items() if isinstance(v, str)}


def sandbox_body(db, run_row):
    """What a sandbox run carries to the workspace: its input's base and the memory it started with, and its arm."""
    trigger = _json(run_row["trigger"], {}) or {}
    p = db.execute("SELECT * FROM ade_improve_proposals WHERE id = ?", (trigger.get("proposal"),)).fetchone()
    of = db.execute("SELECT * FROM ade_agent_runs WHERE run_id = ?", (trigger.get("of"),)).fetchone()
    agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (run_row["agent_id"],)).fetchone()
    if p is None or of is None or agent is None:
        return None
    files = memory_before(db, agent["id"], of["started_at"] or of["queued_at"] or 0)
    arm = settings_now(db, agent)
    if trigger.get("arm") == "treatment":
        arm = with_change(arm, p["target"], p["name"], _json(p["proposed"]))
    return dict(arm, base=of["base"], memory=files)


def _cut_short(row):
    """A sandbox run the machine, not the agent, ended in failure (one that failed at start among them, which the app
    may have failed before sending it): its pair says nothing of either arm, whatever the judge would say of the other
    run."""
    return row["state"] == "failed" and row["cause"] not in CAUSES_OF_ITS_OWN


def _outcome(row, acceptance_spec, verdict):
    """A sandbox run's pass or fail, or None when it says nothing of its arm (the machine, not the agent, ended it)."""
    if row is None or row["state"] not in ("done", "failed"):
        return None
    if _cut_short(row):
        return None
    if row["state"] == "failed":
        return False
    accepted = _json(row["acceptance"])
    if acceptance_spec and (not accepted or accepted.get("pass") is not True):
        return False
    return bool(verdict and verdict.get("pass"))


def score_sandbox(db_connect, user, endpoint, call, now):
    """The account's proposal in the sandbox, once all its runs ended: each pair judged in one call (A the arm drawn
    first), but for a pair holding a run the machine cut short, which says nothing and is dropped without asking the
    judge; the sign test and the comparison regressions, and the cost of a run of each arm. Its state after."""
    with db_connect() as db:
        p = db.execute("SELECT * FROM ade_improve_proposals WHERE user_id = ? AND state = 'sandbox'",
                       (user["id"],)).fetchone()
        if p is None:
            return None
        plan = _json(p["sandbox"], {}) or {}
        ids = [i[arm] for i in plan.get("inputs") or [] for arm in ("control", "treatment")]
        rows = {r["run_id"]: r for r in db.execute(
            f"SELECT * FROM ade_agent_runs WHERE run_id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        agent = db.execute("SELECT * FROM ade_agents WHERE id = ?", (p["agent_id"],)).fetchone()
    if any(rows.get(r) is None or rows[r]["state"] in ("queued", "working", "waiting") for r in ids):
        return "sandbox"
    spec = _json(agent["acceptance"]) if agent is not None else None
    pairs = {"signal": [], "comparison": []}
    costs = {"control": [], "treatment": []}
    graded = []
    for item in plan.get("inputs") or []:
        control, treatment = rows[item["control"]], rows[item["treatment"]]
        if control["state"] not in ("done", "failed") or treatment["state"] not in ("done", "failed"):
            continue
        if _cut_short(control) or _cut_short(treatment):
            # the pair says nothing (_outcome), so the judge is not asked: its call reads both runs, and one the app
            # failed at start before sending it was never made in the workspace, so asked, it would fail at every round
            # and the proposal would wait in its sandbox for good, and the account's reviews and sandboxes behind it; a
            # run done, not judged, says nothing either, and one that failed on its own account says so
            graded.append({"run_id": item["run_id"], "group": item["group"],
                           "control": None if control["state"] == "done" else _outcome(control, spec, None),
                           "treatment": None if treatment["state"] == "done" else _outcome(treatment, spec, None)})
            continue
        a, b = (item["control"], item["treatment"]) if item["first"] == "control" else \
            (item["treatment"], item["control"])
        verdicts = {}
        if control["state"] == "done" or treatment["state"] == "done":
            try:
                got = call(endpoint, "POST", "/agents/judge-pair", {"a": a, "b": b}, timeout=300)
            except Exception as e:  # noqa: BLE001  asked again at the next sync
                print(f"agents: judging a pair of {p['id']} failed: {e}", file=__import__("sys").stderr)
                return "sandbox"
            with db_connect() as db:
                session(db, user["id"], p["agent_id"], "judge", now, proposal_id=p["id"],
                        cost=got.get("cost_est_usd") if isinstance(got, dict) else 0)
            verdicts = {a: got.get("A"), b: got.get("B")} if isinstance(got, dict) else {}
        oc = _outcome(control, spec, verdicts.get(item["control"]))
        ot = _outcome(treatment, spec, verdicts.get(item["treatment"]))
        graded.append({"run_id": item["run_id"], "group": item["group"], "control": oc, "treatment": ot})
        if oc is None or ot is None:
            continue                                   # the machine cut one short: the pair says nothing
        pairs[item["group"]].append((oc, ot))
        for arm, row in (("control", control), ("treatment", treatment)):
            costs[arm].append(row["cost_est_usd"] or 0.0)
    result = stats.eligibility(pairs["signal"], pairs["comparison"])
    mean = {arm: (sum(v) / len(v) if v else None) for arm, v in costs.items()}
    inputs = len(plan.get("inputs") or [])
    dropped = inputs - len(pairs["signal"]) - len(pairs["comparison"])     # pairs that said nothing
    result.update(pairs=graded, cost_control=mean["control"], cost_treatment=mean["treatment"], dropped=dropped)
    state, reason = ("eligible" if result["eligible"] else "not_eligible" if result["evaluated"] else "not_evaluated",
                     result["why"])
    if state == "not_evaluated" and dropped:        # too few pairs because the machine ended runs, not too few runs
        reason = f"{reason}: {dropped} of its {inputs} pairs said nothing, the machine having ended a run of each"
    if result["eligible"] and mean["control"] and mean["treatment"] is not None and \
            mean["treatment"] > (1 + MAX_COST_RISE) * mean["control"] + 1e-12:
        rise = mean["treatment"] / mean["control"] - 1
        state, reason = "pruned", f"its replays cost {rise:.0%} more a run than the control's; at most " \
                                  f"{MAX_COST_RISE:.0%}"
    _decide(db_connect, p["id"], "sandbox", state, reason, now, result=json.dumps(result))
    return state


# ---- the owner's word, expiry, and the watch after a promotion ---------------------------------------------------------------

def expire(db, user_id, now):
    db.execute("UPDATE ade_improve_proposals SET state = 'expired', reason = 'not decided in 14 days', decided_at = ? "
               "WHERE user_id = ? AND state IN ('critic', 'waiting', 'eligible') AND created_at < ?",
               (now, user_id, now - EXPIRES))


def version(db, agent_id, user_id, target, value, author, now, author_model=None, approver=None, proposal_id=None):
    """A version of one of the agent's improvable parts, numbered per part."""
    last = db.execute("SELECT MAX(version) FROM ade_agent_versions WHERE agent_id = ? AND target = ?",
                      (agent_id, target)).fetchone()[0] or 0
    db.execute("INSERT INTO ade_agent_versions (id, agent_id, user_id, target, version, value, author, author_model, "
               "approver, proposal_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (secrets.token_hex(6), agent_id, user_id, target, last + 1, json.dumps(value), author, author_model,
                approver, proposal_id, now))
    return last + 1


def apply(db, agent, target, name, value, now):
    """The change made to the agent itself; its record's change time moved, so the workspace is told."""
    if target == "brief":
        db.execute("UPDATE ade_agents SET brief = ?, brief_version = brief_version + 1, updated_at = ? WHERE id = ?",
                   (value, now, agent["id"]))
    elif target == "skill":
        if value is None:
            db.execute("DELETE FROM ade_agent_skills WHERE agent_id = ? AND name = ?", (agent["id"], name))
        else:
            db.execute("INSERT INTO ade_agent_skills (agent_id, user_id, name, content, updated_at) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(agent_id, name) DO UPDATE SET content = excluded.content, "
                       "updated_at = excluded.updated_at", (agent["id"], agent["user_id"], name, value, now))
        db.execute("UPDATE ade_agents SET updated_at = ? WHERE id = ?", (now, agent["id"]))
    elif target in ("effort", "model"):
        settings = _json(agent["chat_settings"], {}) or {}
        if value is None:
            settings.pop(target, None)
        else:
            settings[target] = value
        db.execute("UPDATE ade_agents SET chat_settings = ?, updated_at = ? WHERE id = ?",
                   (json.dumps(settings), now, agent["id"]))
    elif target == "autocompact_window":
        db.execute("UPDATE ade_agents SET autocompact_window = ?, updated_at = ? WHERE id = ?", (value, now, agent["id"]))


def approve(db, user, proposal_id, now):
    """The owner's tap on an eligible proposal: the change applied and a version made of it. Claimed first, under
    the store's write lock: a second tap at once, or a reject, finds it decided and changes nothing; a failure after
    the claim leaves it as it was (the store's transaction is not kept). One that would add a skill the agent does not
    hold, while it holds shapes.MAX_SKILLS (FULL), what it holds at the tap deciding, is refused so instead: no longer
    eligible, its reason FULL and never approved, with no time of decision, as FULL's refusal as a change is taken has
    none, so that _tried_before does not count it against an equal change once the agent has room; its view is
    returned, and the caller (agents.decide_improvement) says the refusal as a 409 once the store has kept it."""
    p = _one(db, user["id"], proposal_id)
    if db.execute("UPDATE ade_improve_proposals SET state = 'approved', decided_at = ?, approved_at = ? WHERE id = ? "
                  "AND state = 'eligible'", (now, now, p["id"])).rowcount != 1:
        raise ImproveError(409, "Only a proposal that passed its sandbox, or a rollback, is approved.")
    value = _json(p["proposed"])
    if p["kind"] == "meta":
        last = review_prompt(db, user["id"])["version"]
        db.execute("INSERT INTO ade_improve_prompts (user_id, version, prompt, proposal_id, created_at) VALUES "
                   "(?,?,?,?,?)", (user["id"], last + 1, value, p["id"], now))
    else:
        agent = db.execute("SELECT * FROM ade_agents WHERE id = ? AND user_id = ?", (p["agent_id"], user["id"])).fetchone()
        if agent is None or agent["retired_at"]:
            raise ImproveError(409, "Its agent is retired.")
        full = _full(db, agent["id"], p["target"], p["name"], value)
        if full is not None:                        # what it holds now decides: refused, kept, and said by the caller
            db.execute("UPDATE ade_improve_proposals SET state = 'refused', reason = ?, decided_at = NULL, approved_at "
                       "= NULL WHERE id = ? AND state = 'approved'", (FULL.format(n=full, most=shapes.MAX_SKILLS),
                                                                       p["id"]))
            return view(db.execute("SELECT * FROM ade_improve_proposals WHERE id = ?", (p["id"],)).fetchone())
        model = None
        if p["review_run_id"]:
            review = db.execute("SELECT r.run_id, a.chat_settings FROM ade_agent_runs r JOIN ade_agents a ON "
                                "a.id = r.agent_id WHERE r.run_id = ?", (p["review_run_id"],)).fetchone()
            model = (_json(review["chat_settings"], {}) or {}).get("model") if review else None
        apply(db, agent, p["target"], p["name"], value, now)
        target = f"skill:{p['name']}" if p["target"] == "skill" else p["target"]
        version(db, agent["id"], user["id"], target, value, "loop", now, author_model=model, approver="owner",
                proposal_id=p["id"])
        if p["kind"] == "rollback" and p["rolls_back"]:
            db.execute("UPDATE ade_improve_proposals SET state = 'rolled_back' WHERE id = ?", (p["rolls_back"],))
    return view(db.execute("SELECT * FROM ade_improve_proposals WHERE id = ?", (p["id"],)).fetchone())


def reject(db, user, proposal_id, reason, now):
    p = _one(db, user["id"], proposal_id)
    why = reason.strip()[:300] if isinstance(reason, str) and reason.strip() else "rejected by the owner"
    if db.execute("UPDATE ade_improve_proposals SET state = 'rejected', reason = ?, decided_at = ? WHERE id = ? AND "
                  "state IN ('eligible', 'critic', 'waiting')", (why, now, p["id"])).rowcount != 1:
        raise ImproveError(409, "This proposal is decided already.")
    return view(db.execute("SELECT * FROM ade_improve_proposals WHERE id = ?", (p["id"],)).fetchone())


def _one(db, user_id, proposal_id):
    p = db.execute("SELECT * FROM ade_improve_proposals WHERE id = ? AND user_id = ?", (proposal_id, user_id)).fetchone()
    if p is None:
        raise ImproveError(404, "No such proposal.")
    return p


def watching(db, agent_id):
    """The agent's promoted change still watched (every run judged until WATCH are), or None."""
    return db.execute("SELECT * FROM ade_improve_proposals WHERE agent_id = ? AND kind = 'change' AND state = 'approved' "
                      "AND watched_at IS NULL ORDER BY approved_at DESC LIMIT 1", (agent_id,)).fetchone()


def watch(db, user_id, now):
    """Each promoted change with WATCH graded runs since: its passes against the previous version's last WATCH, by the
    exact Fisher test; a drop at p <= P_MAX makes a rollback proposal for the owner's tap. The rollback proposals."""
    made = []
    for p in db.execute("SELECT * FROM ade_improve_proposals WHERE user_id = ? AND kind = 'change' AND state = "
                        "'approved' AND watched_at IS NULL", (user_id,)).fetchall():
        def passes(sql, args):
            out = []
            for r in db.execute(sql, args):
                verdict, kind = _json(r["judge"]), _kind(r)
                if verdict and kind not in LOOP_KINDS:
                    out.append(verdict.get("pass") is True)
            return out
        # after: runs that started once it was approved; one under way then started on the agent as it was, and is
        # in neither
        after = passes("SELECT judge, trigger FROM ade_agent_runs WHERE agent_id = ? AND judge IS NOT NULL AND "
                       "started_at >= ? ORDER BY ended_at, run_id", (p["agent_id"], p["approved_at"]))[:stats.WATCH]
        if len(after) < stats.WATCH:
            continue
        before = passes("SELECT judge, trigger FROM ade_agent_runs WHERE agent_id = ? AND judge IS NOT NULL AND "
                        "ended_at < ? ORDER BY ended_at DESC, run_id", (p["agent_id"], p["approved_at"]))[:stats.WATCH]
        db.execute("UPDATE ade_improve_proposals SET watched_at = ? WHERE id = ?", (now, p["id"]))
        if len(before) < stats.WATCH:
            continue
        p_value = stats.fisher_drop(sum(after), len(after), sum(before), len(before))
        if p_value > stats.P_MAX:
            continue
        rid = secrets.token_hex(6)
        db.execute("INSERT INTO ade_improve_proposals (id, user_id, agent_id, review_run_id, kind, target, name, "
                   "current, proposed, hypothesis, evidence, edits, state, reason, result, created_at, rolls_back) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (rid, user_id, p["agent_id"], None, "rollback", p["target"], p["name"], p["proposed"], p["current"],
                    f"Undo it: its first {stats.WATCH} graded runs passed {sum(after)} times, the {stats.WATCH} before "
                    f"it {sum(before)}.", json.dumps({}), 1, "eligible", None,
                    json.dumps({"after": sum(after), "before": sum(before), "p": round(p_value, 6)}), now, p["id"]))
        made.append(rid)
    return made


MAX_DIFF = 200


def diff(was, now):
    """A text change as the card shows it: its lines marked " ", "-" or "+", with two lines around each change; a
    value that is not text, as itself before and after."""
    if not isinstance(was, str) and not isinstance(now, str):
        return [f"- {json.dumps(was)}", f"+ {json.dumps(now)}"]
    lines = list(difflib.unified_diff((was or "").splitlines(), (now or "").splitlines(), n=2, lineterm=""))[2:]
    return [line if line.startswith(("+", "-", "@")) else " " + line[1:] for line in lines][:MAX_DIFF]


def view(p):
    return {"id": p["id"], "agent_id": p["agent_id"], "review_run_id": p["review_run_id"], "kind": p["kind"],
            "target": p["target"], "name": p["name"], "current": _json(p["current"]), "proposed": _json(p["proposed"]),
            "hypothesis": p["hypothesis"], "evidence": _json(p["evidence"], {}), "edits": p["edits"],
            "state": p["state"], "reason": p["reason"], "result": _json(p["result"]), "created_at": p["created_at"],
            "decided_at": p["decided_at"], "approved_at": p["approved_at"], "rolls_back": p["rolls_back"],
            "expires_at": p["created_at"] + EXPIRES if p["state"] in ("critic", "waiting", "eligible") else None,
            "diff": diff(_json(p["current"]), _json(p["proposed"]))}


def listing(db, user_id, now):
    rows = db.execute("SELECT * FROM ade_improve_proposals WHERE user_id = ? ORDER BY created_at DESC LIMIT 200",
                      (user_id,)).fetchall()
    return {"proposals": [view(p) for p in rows], "budget": budget(db, user_id, now),
            "review_prompt": review_prompt(db, user_id)}


def check_change_shape(change):
    """A change as the app keeps it from a review run's record (the workspace's check, run again here)."""
    try:
        return shapes.check_change(change)
    except shapes.ShapeError:
        return None
