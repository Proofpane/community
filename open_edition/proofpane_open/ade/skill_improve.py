"""The improve loop for a repository's business skills, the app's half (the workspace's half is
proofpane_workspace/skill_improve.py). Off until the owner turns a repository's switch on, and bounded at every step:

- The counts: a folder's calls are the rows of its repository whose `skill` is one of the
  folder's names (the workspace maps names to folders at the branch's head), over the calls kept (skill_counts.KEPT_DAYS
  and CALLS_MOST): its calls, its typed calls, its failed calls, the tasks that called it by how each stands and those
  whose last round of checks did not pass; the same at the current version and at the version before. A folder any of
  whose names has a row of the repository counted `other` is never proposed for.
- The schedule: at most one business review a day for the account, apart from the agents' one, within the
  week's budget both loops share (improve.budget counts `skill_review` and `skill_critic`), none while a business
  proposal waits for its critic; the candidate with the highest score is reviewed, ties going to the one least lately
  reviewed: a folder of a repository switched on that passes every bar at the branch's head (the workspace's
  candidates), with at least MIN_CALLS calls with an outcome in the last SIGNAL_DAYS days, no proposal open, not
  reviewed in the last SKILL_COOLDOWN, and a score above zero (SKILL_WEIGHTS, over its calls since its last review).
- The proposal: the workspace's review records it and gives back its texts and ids, no object written. The app checks
  them again (shapes.check_business_proposal, its ids, its hypothesis, MAX_EDITS), keeps it only once a record it read
  from the workspace holds it, asks for its objects and checks each of them (their ids, the one parent, the one path,
  the same mode), has the critic read it, and only then has the workspace publish its ref. A proposal refused at any of
  these steps never reaches the shared repository, and its ref, where it has one, is removed.
- The owner's ways, each his tap and never the loop's: Merge into his copy (a repository added from his
  machine), Bring it up to date, Open a pull request and then Merge (GitHub), Reject, and for a found proposal Take it as
  mine or Go back.
- The look, at each view of a proposal and once a day: the branch's head read for each proposal not ended,
  which reached the branch by one of its commits or by its content (`merged` where the owner's tap did it, else
  `found`), whose ref no longer names its head (`changed`), whose skill's folder changed on the branch (`outdated`), or
  whose pull request was closed on GitHub without a merge (`rejected`).
- The watch: once a merged version has improve_stats.WATCH calls with an outcome, and the version it
  replaced as many, a drop at p <= improve_stats.P_MAX by the exact Fisher test makes a proposal to go back, for the
  owner's tap, never a revert by itself.
- The word: the app's copy of the workspace's record of proposals, taken only where the workspace says
  the file's signature holds and only at a generation above the copy's, sent back as the app's word first in each of its
  own calls that can lead to a start (agents._mirror, controller.sync) and by a sender of its own every WORD_EVERY
  seconds where the workspace has not had it since it started; the owner's Restore writes the record from it.

What is counted is the sessions' word, for display (skill_counts.py): forged or flooded lines can start a review, at
most one a day within the week's budget, or prompt a proposal to go back, which waits for the owner's tap; they can never
merge anything. Every function that takes `db` runs inside the caller's transaction; a call to the workspace is never
made while a transaction is open."""

import base64
import json
import re
import secrets
import sys
import threading
import time

from .. import store
from . import agents, improve, shapes, skill_counts
from . import improve_stats as stats

DAY = 86400
MIN_CALLS = 10                      # calls with an outcome in the window, for a review; and at each version, for a drop
SIGNAL_DAYS = 30                    # the window of the signals and the schedule
SKILL_COOLDOWN = 7 * DAY            # a folder reviewed once in this long
SKILL_WEIGHTS = {"failed": 1.0, "checks_failed": 0.9, "task_failed": 0.6, "task_stopped": 0.3, "drop": 2.0}
WORD_EVERY = 15                     # seconds between the sender's rounds
WORD_TIMEOUT = 5                    # seconds each of the sender's calls, and the word's, waits
EXPIRES = improve.EXPIRES           # critic, open and pull_request expire after it, as an agent's proposals do
TRY_EVERY = 3600                    # seconds between two tries at a review that did not run, for one account
REVIEW_TIMEOUT = 1800               # seconds a review's call may take: a probe, a clone and one turn
CRITIC_TIMEOUT = 900                # seconds the critic's call may take
CALL_TIMEOUT = 600                  # seconds any other call may take: a look or a merge can fetch origin's branch
LOOK_EVERY = DAY                    # a repository's proposals looked at once a day by the step, and at each view

KINDS = ("change", "back")
STATES = ("critic", "open", "refused", "pruned", "pull_request", "merged", "found", "outdated", "changed", "rejected",
          "expired", "withdrawn")
OPEN = ("critic", "open", "pull_request")       # on its way: expires, withdrawn when its switch is turned off
CLOSED = ("refused", "pruned", "rejected", "expired", "withdrawn")      # closed unmerged: its bar goes on
ENDS = ("merged", "taken", "back")
REJECTED = "rejected"
_ID = re.compile(r"[0-9a-f]{12}")
_REPO = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.-]{0,99}")
_OIDS = {"sha1": re.compile(r"[0-9a-f]{40}"), "sha256": re.compile(r"[0-9a-f]{64}")}
_MODES = ("100644", "100755")
# an origin on GitHub, as the workspace's own test of one (workspace._GITHUB) takes it: only such a clone's proposal
# goes to GitHub
_GITHUB = re.compile(r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")
_ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,40}")
_MODEL = re.compile(r"[A-Za-z0-9._:\[\]-]{1,100}")
NOT_A_RECORD = "the workspace's record of skill proposals did not show it"
RESTARTED = "the workspace started again before the proposal was published: a new review is the way"


class SkillImproveError(agents.AgentError):
    """A refusal of the loop's, answered by main.py as {"detail": ...} with its status, as an agent's is."""


def _json(text, default=None):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _now(now):
    return int(now if now is not None else time.time())


def _call(call):
    from . import workspace_client
    return call or workspace_client.call


def _said(e):
    """A workspace call's failure, as the page and the record say it: the workspace's own refusal, cut, or its status."""
    return str(e)[:300]


def _transient(e):
    """Whether a workspace call may get through later: no answer, or an answer that was not one of the workspace's
    refusals. Anything else, the app's own failure among them, is not: a proposal left waiting would hold every review
    of the account."""
    from . import workspace_client
    return isinstance(e, workspace_client.WorkspaceCallError) and (
        isinstance(e, workspace_client.NotAnswering) or e.status not in workspace_client.REFUSALS)


# ---- the counts ----------------------------------------------------------------------------------------------------

def _where(user_id, repo, names, version=None, since=None):
    sql = (f"c.user_id = ? AND c.kind = 'business' AND c.repo = ? AND c.skill IN ({','.join('?' * len(names))})")
    args = [user_id, repo, *names]
    if version is not None:
        sql += " AND c.version = ?"
        args.append(version)
    if since is not None:
        sql += " AND c.at >= ?"
        args.append(since)
    return sql, args


_TASKS = "LEFT JOIN ade_controller_tasks t ON t.user_id = c.user_id AND t.id = c.task_id"


def _checks_failed(checks):
    """Whether a task's last round of checks did not pass (ade_controller_tasks.checks, controller._task_item)."""
    rnd = _json(checks, {})
    return isinstance(rnd, dict) and rnd.get("passed") is False


def _part(db, user_id, repo, names, version=None):
    if not names:
        return dict.fromkeys(shapes.SKILL_COUNTS_FIELDS, 0)
    where, args = _where(user_id, repo, names, version)
    head = db.execute(f"SELECT COUNT(*) AS calls, COALESCE(SUM(c.path = 'typed'), 0) AS typed, "
                      f"COALESCE(SUM(c.failed = 1), 0) AS failed FROM ade_skill_calls c WHERE {where}", args).fetchone()
    ended = dict.fromkeys(skill_counts.ENDED, 0)
    checks_failed = 0
    for r in db.execute(f"SELECT DISTINCT c.task_id AS task, t.state AS state, t.checks AS checks FROM ade_skill_calls c "
                        f"{_TASKS} WHERE {where} AND c.task_id IS NOT NULL", args):
        ended[skill_counts._ended_key(r["state"])] += 1
        checks_failed += _checks_failed(r["checks"])
    return {"calls": head["calls"], "typed": head["typed"], "failed": head["failed"], "tasks_done": ended["done"],
            "tasks_failed": ended["failed"], "tasks_stopped": ended["stopped"], "tasks_open": ended["open"],
            "tasks_unknown": ended["unknown"], "tasks_checks_failed": checks_failed}


def counts(db, user_id, repo, names, current, before):
    """The counts a proposal carries, over the calls kept: {"whole", "current", "before"}, the two versions'
    None where there is no such version. A task that called the skill at both versions is counted at each, and once in
    the whole."""
    names = list(names)
    return {"whole": _part(db, user_id, repo, names),
            "current": _part(db, user_id, repo, names, current) if current else None,
            "before": _part(db, user_id, repo, names, before) if before else None}


def other_named(db, user_id, repo, names):
    """Whether any of the folder's names has a row of the repository counted `other` among the calls kept."""
    if not names:
        return False
    return db.execute(f"SELECT 1 FROM ade_skill_calls WHERE user_id = ? AND repo = ? AND kind = 'other' AND skill IN "
                      f"({','.join('?' * len(names))}) LIMIT 1", (user_id, repo, *names)).fetchone() is not None


def _outcomes(db, user_id, repo, names, version):
    """The passes and the calls with an outcome at a version, among the calls kept."""
    where, args = _where(user_id, repo, names, version)
    r = db.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(c.failed = 0), 0) AS passed FROM ade_skill_calls c WHERE "
                   f"{where} AND c.failed IS NOT NULL", args).fetchone()
    return r["passed"], r["n"]


def score(db, user_id, repo, names, current, before, since, now):
    """A candidate's signals: (its score, its calls with an outcome in the window). The score sums
    SKILL_WEIGHTS over its calls since `since` (its last review), or the window where that is earlier: each failed
    call, each task that called it whose last round of checks did not pass, that ended failed, that ended stopped, a task
    counting once for each signal it gives; and, once, the current version failing more often than the one before at p
    <= P_MAX, with at least MIN_CALLS calls with an outcome at each. A typed call has no outcome."""
    names = list(names)
    window = now - SIGNAL_DAYS * DAY
    where, args = _where(user_id, repo, names, since=window)
    with_outcome = db.execute(f"SELECT COUNT(*) FROM ade_skill_calls c WHERE {where} AND c.failed IS NOT NULL",
                              args).fetchone()[0]
    where, args = _where(user_id, repo, names, since=max(since or 0, window))
    total = db.execute(f"SELECT COALESCE(SUM(c.failed = 1), 0) FROM ade_skill_calls c WHERE {where}",
                       args).fetchone()[0] * SKILL_WEIGHTS["failed"]
    for r in db.execute(f"SELECT DISTINCT c.task_id AS task, t.state AS state, t.checks AS checks FROM ade_skill_calls c "
                        f"{_TASKS} WHERE {where} AND c.task_id IS NOT NULL", args):
        if _checks_failed(r["checks"]):
            total += SKILL_WEIGHTS["checks_failed"]
        if r["state"] == "failed":
            total += SKILL_WEIGHTS["task_failed"]
        elif r["state"] == "stopped":
            total += SKILL_WEIGHTS["task_stopped"]
    if current and before:
        cur_pass, cur_n = _outcomes(db, user_id, repo, names, current)
        old_pass, old_n = _outcomes(db, user_id, repo, names, before)
        if cur_n >= MIN_CALLS and old_n >= MIN_CALLS and \
                stats.fisher_drop(cur_pass, cur_n, old_pass, old_n) <= stats.P_MAX:
            total += SKILL_WEIGHTS["drop"]
    return round(total, 6), with_outcome


# ---- the switch and the owners the round reaches --------------------------------------------------------------------

def _repo_row(db, user_id, repo):
    return db.execute("SELECT * FROM ade_skill_improve_repos WHERE user_id = ? AND repo = ?", (user_id, repo)).fetchone()


def users_to_poll(db):
    """The owners whose account's thread of the judge and the loop the round starts for the business skills' loop: one
    with a repository switched on, or with a proposal not closed (on its way, found and not taken, outdated, changed, or
    merged and not yet watched)."""
    return sorted({r[0] for r in db.execute(
        "SELECT user_id FROM ade_skill_improve_repos WHERE turned_on = 1 UNION SELECT user_id FROM ade_skill_proposals "
        "WHERE (ended IS NULL AND state IN ('critic', 'open', 'pull_request', 'found', 'outdated', 'changed')) OR "
        "(ended IN ('merged', 'taken') AND watched_at IS NULL)")})


def switch_view(row):
    if row is None:
        return None
    return {"repo": row["repo"], "on": bool(row["turned_on"]), "account": row["account"], "model": row["model"],
            "url": row["url"], "branch": row["branch"], "changed_at": row["changed_at"],
            "reviewed": _json(row["reviewed"], {})}


def set_switch(user, repo, on, account=None, model=None, endpoint=None, call=None, now=None):
    """The owner's switch on a repository, sent to the workspace first, which records, for a clone,
    origin's address and the branch it has checked out: off by default. Turned off, each of the repository's proposals
    on its way is withdrawn and its ref removed; the bar of every proposal recorded goes on."""
    now = _now(now)
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise SkillImproveError(400, "A switch names a repository.")
    if not isinstance(on, bool):
        raise SkillImproveError(400, "A switch is on or off.")
    if account is not None and (not isinstance(account, str) or not _ACCOUNT.fullmatch(account)):
        raise SkillImproveError(400, "A switch's account is an account's id, or none.")
    if model is not None and (not isinstance(model, str) or not _MODEL.fullmatch(model)):
        raise SkillImproveError(400, "A switch's model is a model's name, or none.")
    endpoint = endpoint or agents._endpoint(user)
    if endpoint is None:
        raise SkillImproveError(409, "The workspace is not running: start it, then turn the switch.")
    send_word(user, endpoint, call)
    try:
        got = _call(call)(endpoint, "PUT", "/skill-improve/switch", {"repo": repo, "on": on, "account": account,
                                                                     "model": model}, timeout=CALL_TIMEOUT)
    except Exception as e:  # noqa: BLE001  the workspace's words, said back
        raise SkillImproveError(409, f"The workspace did not turn the switch: {_said(e)}") from None
    with store.connect() as db:
        db.execute("INSERT INTO ade_skill_improve_repos (user_id, repo, turned_on, account, model, url, branch, "
                   "changed_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (user_id, repo) DO UPDATE SET turned_on = "
                   "excluded.turned_on, account = excluded.account, model = excluded.model, url = excluded.url, "
                   "branch = excluded.branch, changed_at = excluded.changed_at",
                   (user["id"], repo, int(on), account, model, got.get("url") if isinstance(got, dict) else None,
                    got.get("branch") if isinstance(got, dict) else None, now))
        withdrawn = [] if on else [r["id"] for r in db.execute(
            f"SELECT id FROM ade_skill_proposals WHERE user_id = ? AND repo = ? AND state IN "
            f"({','.join('?' * len(OPEN))})", (user["id"], repo, *OPEN))]
    for pid in withdrawn:
        _close(user, endpoint, call, pid, "withdrawn", "its repository's switch was turned off", now)
    read_record(user, endpoint, call)
    with store.connect() as db:
        return switch_view(_repo_row(db, user["id"], repo))


# ---- the record of proposals and the app's word ---------------------------------------------------------------------

def _copy(db, user_id):
    return db.execute("SELECT * FROM ade_skill_records WHERE user_id = ?", (user_id,)).fetchone()


def _file_record(b64):
    """The record a file of the workspace's holds ({"record", "mac"}), read as data alone; None where it is not one."""
    try:
        got = json.loads(base64.b64decode(b64, validate=True).decode("utf-8"))
    except (ValueError, UnicodeError, TypeError, RecursionError):
        return None
    record = got.get("record") if isinstance(got, dict) else None
    return record if isinstance(record, dict) and isinstance(record.get("repos"), dict) else None


def read_record(user, endpoint, call=None):
    """`GET /skill-improve/record`: the workspace's view of its record, the file taken as the app's copy only where the
    workspace says its signature holds and that it believes it (its state `record`, not `waiting` or `held`), and only
    at a generation above the copy's, so that a file a program wrote or put back never becomes the copy a restore signs.
    The view, or None where the workspace serves no such route; raises the call's
    failure otherwise."""
    from . import workspace_client
    try:
        got = _call(call)(endpoint, "GET", "/skill-improve/record", timeout=WORD_TIMEOUT)
    except workspace_client.WorkspaceCallError as e:
        if skill_counts._no_route(e):
            return None
        raise
    if not isinstance(got, dict):
        raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
    gen = got.get("generation")
    if got.get("holds") is True and got.get("state") == "record" and isinstance(got.get("file"), str) and \
            type(gen) is int and gen >= 0 and _file_record(got["file"]) is not None:
        now = int(time.time())
        with store.connect() as db:
            db.execute("INSERT INTO ade_skill_records (user_id, generation, record, read_at) VALUES (?,?,?,?) ON "
                       "CONFLICT (user_id) DO UPDATE SET generation = excluded.generation, record = excluded.record, "
                       "read_at = excluded.read_at WHERE excluded.generation > ade_skill_records.generation",
                       (user["id"], gen, got["file"], now))
    return got


def _word_body(user_id):
    """The app's word: its copy and the copy's generation, with each commit it checked for a proposal and each end it
    recorded, which the workspace's record must hold beside the copy, so that the copy itself put back by a session's
    program after a later write is held."""
    with store.connect() as db:
        copy = _copy(db, user_id)
    return {"generation": copy["generation"] if copy else 0, "record": copy["record"] if copy else None,
            "commits": checked_commits(user_id), "ends": recorded_ends(user_id)}


def send_word(user, endpoint, call=None):
    """The app's word, first in each call of the app's own that can lead to a start: the record read
    (read_record), then the copy and its generation sent (PUT /skill-improve/record). Never raises into the call it is
    first in: a workspace that answers 404 with no refusal in it, an older image with no bar to wait on, is read as
    such; any other failure is said on standard error, and the start it leads to waits for the sender's word. Whether
    the word was taken."""
    try:
        if read_record(user, endpoint, call) is None:
            return False
        _call(call)(endpoint, "PUT", "/skill-improve/record", _word_body(user["id"]), timeout=WORD_TIMEOUT)
        return True
    except Exception as e:  # noqa: BLE001  the sender's word comes, or the start is refused after WORD_WAIT
        print(f"skill improve: the word for {user['id']} was not sent: {type(e).__name__}: {e}", file=sys.stderr)
        return False


_sending = set()
_send_lock = threading.Lock()


def _send_one(user):
    try:
        endpoint = agents._endpoint(user)
        if endpoint is None:
            return
        got = read_record(user, endpoint)
        if got is not None and got.get("word") is not True:
            _call(None)(endpoint, "PUT", "/skill-improve/record", _word_body(user["id"]), timeout=WORD_TIMEOUT)
    except Exception as e:  # noqa: BLE001  the next round tries again
        print(f"skill improve: the sender's word for {user['id']} failed: {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        with _send_lock:
            _sending.discard(user["id"])


def word_round():
    """One round of the sender: for each owner with an agent or a controller whose workspace runs, in a thread of that
    owner's own, so that no owner's workspace delays another's word, the record read and, where the workspace says it
    has not had the word since it started, the word; an owner whose last send has not ended is passed over."""
    from .. import auth
    from . import controller
    with store.connect() as db:
        owners = [r[0] for r in db.execute("SELECT DISTINCT user_id FROM ade_agents WHERE retired_at IS NULL")]
        owners += [u for u in controller.users_to_poll(db) if u not in owners]
    started = []
    for user_id in owners:
        with _send_lock:
            if user_id in _sending:
                continue
            _sending.add(user_id)
        try:
            user = auth.user(user_id)
        except Exception as e:  # noqa: BLE001  this owner at the next round, the others now
            print(f"skill improve: the sender could not read {user_id}: {type(e).__name__}: {e}", file=sys.stderr)
            user = None
        if user is None:
            with _send_lock:
                _sending.discard(user_id)
            continue
        t = threading.Thread(target=_send_one, args=(user,), name=f"skill-word-{user_id}", daemon=True)
        t.start()
        started.append(t)
    return started


def start_word_sender(every=WORD_EVERY):
    """The sender, beside the poller: a word round every `every` seconds, so that a workspace whose
    routes answer has the word within WORD_EVERY and two WORD_TIMEOUTs of its start."""
    def loop():
        while True:
            time.sleep(every)
            try:
                word_round()
            except Exception as e:  # noqa: BLE001  the sender never takes the app down
                print(f"skill improve: a word round failed: {type(e).__name__}: {e}", file=sys.stderr)
    thread = threading.Thread(target=loop, name="skill-word-sender", daemon=True)
    thread.start()
    return thread


def checked_commits(user_id):
    """Each commit the app checked for a proposal (check_objects), by proposal id, its heads before a remake first: what
    a restore adds to the copy's proposals, so that a copy read before step 5 does not unbar a commit."""
    out = {}
    with store.connect() as db:
        for r in db.execute("SELECT id, head, heads, notes FROM ade_skill_proposals WHERE user_id = ? AND head IS NOT "
                            "NULL", (user_id,)):
            heads = [h.get("head") for h in _json(r["heads"], []) if isinstance(h, dict)]
            remade = (_json(r["notes"], {}) or {}).get("remade")
            later = [remade.get("head")] if isinstance(remade, dict) else []
            out[r["id"]] = [c for c in dict.fromkeys(heads + [r["head"]] + later) if isinstance(c, str)][-100:]
    return out


def recorded_ends(user_id):
    """Each end the app recorded for a proposal ({"how", "at"}, by proposal id): the owner's merge or take, and an end
    the workspace gave at a look; what a restore gives the copy's proposals the copy has no end for, so that a copy read
    before the end does not undo it."""
    out = {}
    with store.connect() as db:
        for r in db.execute("SELECT id, ended, ended_at FROM ade_skill_proposals WHERE user_id = ? AND ended IS NOT "
                            "NULL", (user_id,)):
            if r["ended"] in ENDS and type(r["ended_at"]) is int and r["ended_at"] >= 0:
                out[r["id"]] = {"how": r["ended"], "at": r["ended_at"]}
    return out


def restore(user, endpoint=None, call=None):
    """The owner's tap, "Restore it from the app's copy" (POST /skill-improve/record/restore): the record written from
    the copy, under the workspace's own key, its generation one more than the highest seen, each proposal given the
    commits the app checked for it (checked_commits) and the end the app recorded for it (recorded_ends); then read
    back."""
    endpoint = endpoint or agents._endpoint(user)
    if endpoint is None:
        raise SkillImproveError(409, "The workspace is not running.")
    body = _word_body(user["id"])
    try:
        got = _call(call)(endpoint, "POST", "/skill-improve/record/restore",
                          {"record": body["record"], "commits": checked_commits(user["id"]),
                           "ends": recorded_ends(user["id"])}, timeout=CALL_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        raise SkillImproveError(409, f"The record was not restored: {_said(e)}") from None
    read_record(user, endpoint, call)
    return got


def _record_holds(user_id, pid, blob, path, base, commit=None):
    """Whether the app's copy of the record holds the proposal, by its id, blob, path and base, and, with `commit`,
    that commit among its commits."""
    with store.connect() as db:
        copy = _copy(db, user_id)
    record = _file_record(copy["record"]) if copy else None
    if record is None:
        return False
    for ps in record["repos"].values():
        for p in ps if isinstance(ps, list) else ():
            if isinstance(p, dict) and p.get("id") == pid:
                return p.get("blob") == blob and p.get("path") == path and base in (p.get("bases") or []) and \
                    (commit is None or commit in (p.get("commits") or []))
    return False


# ---- the objects the workspace sends --------------------------------------------------------------------------------

def _commit_fields(data):
    """A commit's tree and parents, read from its headers; None where it is not one."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return None
    head, sep, _ = text.partition("\n\n")
    if not sep:
        return None
    tree, parents = None, []
    for line in head.split("\n"):
        if line.startswith("tree ") and tree is None and not parents:
            tree = line[5:]
        elif line.startswith("parent "):
            parents.append(line[7:])
    return (tree, parents) if tree else None


def _tree_entries(data, fmt):
    """A tree's entries, [(mode, name, id)], read from its bytes; None where they are not a tree's."""
    size = 20 if fmt == "sha1" else 32
    out, at = [], 0
    while at < len(data):
        sp, nul = data.find(b" ", at), data.find(b"\0", at)
        if sp < 0 or nul < 0 or sp > nul or nul + 1 + size > len(data):
            return None
        try:
            mode, name = data[at:sp].decode("ascii"), data[sp + 1:nul].decode("utf-8")
        except UnicodeError:
            return None
        out.append((mode, name, data[nul + 1:nul + 1 + size].hex()))
        at = nul + 1 + size
    return out


def check_objects(sent, row, expect=None):
    """The app's checks of the objects the workspace sent for a proposal: each hashed
    again and refused where its id differs; the proposal's commit with exactly one parent, the base; each tree entry on
    the path a tree but the last, a blob whose mode is the same at both commits (100644 or 100755); the trees at the two
    commits differing only on that path; the two blobs the proposal's, their texts passing check_business_proposal. The
    app has no git: it cannot check that the base is on the branch, nor anything outside these objects. With
    `expect`, the base must be that one, the proposal's own. Gives {"head", "base",
    "mode"}; raises SkillImproveError naming what failed."""
    def no(why):
        raise SkillImproveError(409, f"The proposal's objects do not check: {why}.")

    if not isinstance(sent, dict):
        no("the workspace sent none")
    fmt, base, head, path = sent.get("object_format"), sent.get("base"), sent.get("head"), sent.get("path")
    oid = _OIDS.get(fmt)
    if oid is None or fmt != row["object_format"]:
        no("their object format is not the proposal's")
    if not (isinstance(base, str) and oid.fullmatch(base) and isinstance(head, str) and oid.fullmatch(head)):
        no("the base or the commit is not an object's id")
    if expect is not None and base != expect:
        no("their base is not the proposal's")
    if path != row["path"]:
        no("their path is not the proposal's")
    objects = {}
    for o in sent.get("objects") if isinstance(sent.get("objects"), list) else no("the workspace sent none"):
        if not isinstance(o, dict) or o.get("kind") not in shapes.OBJECT_KINDS or not isinstance(o.get("id"), str):
            no("an object is not a blob, a tree or a commit with its id")
        try:
            data = base64.b64decode(o.get("data") or "", validate=True)
        except (ValueError, TypeError):
            no(f"{o['id']}'s bytes are not base64")
        if shapes.object_id(o["kind"], data, fmt) != o["id"]:
            no(f"{o['id']} does not hash to its id")
        objects[o["id"]] = (o["kind"], data)

    def get(oid_, kind):
        got = objects.get(oid_)
        if got is None or got[0] != kind:
            no(f"{oid_} is not among them as a {kind}")
        return got[1]

    head_fields, base_fields = _commit_fields(get(head, "commit")), _commit_fields(get(base, "commit"))
    if head_fields is None or base_fields is None:
        no("a commit cannot be read")
    if head_fields[1] != [base]:
        no("the proposal's commit has not exactly one parent, the base")
    parts = path.split("/")
    levels = []
    for tree in (base_fields[0], head_fields[0]):
        entries, chain = None, []
        for i, part in enumerate(parts):
            entries = _tree_entries(get(tree, "tree"), fmt)
            if entries is None:
                no(f"a tree on {path} cannot be read")
            found = [e for e in entries if e[1] == part]
            if len(found) != 1:
                no(f"{'/'.join(parts[:i + 1])} is not there once")
            chain.append((entries, found[0]))
            if i < len(parts) - 1:
                if found[0][0] != "40000":
                    no(f"{'/'.join(parts[:i + 1])} is not a folder")
                tree = found[0][2]
        levels.append(chain)
    (b_chain, h_chain) = levels
    b_last, h_last = b_chain[-1][1], h_chain[-1][1]
    if b_last[0] not in _MODES or b_last[0] != h_last[0]:
        no("the skill's mode is not the same at both commits")
    if b_last[2] != row["current_blob"] or h_last[2] != row["blob"]:
        no("the blobs are not the proposal's")
    for (b_entries, b_entry), (h_entries, h_entry) in zip(b_chain, h_chain):
        rest_b = sorted(e for e in b_entries if e[1] != b_entry[1])
        rest_h = sorted(e for e in h_entries if e[1] != h_entry[1])
        if rest_b != rest_h or b_entry[2] == h_entry[2]:
            no("the two commits' trees differ elsewhere than on the path")
    try:
        current = get(b_last[2], "blob").decode("utf-8")
        proposed = get(h_last[2], "blob").decode("utf-8")
    except UnicodeError:
        no("a SKILL.md is not UTF-8 text")
    if current != row["current_text"] or proposed != row["proposed_text"]:
        no("the blobs' texts are not the proposal's")
    try:
        shapes.check_business_proposal(current, proposed)
    except shapes.ShapeError as e:
        no(str(e).rstrip("."))
    return {"head": head, "base": base, "mode": b_last[0]}


# ---- the proposal's way to the owner --------------------------------------------------------------------------------

def _row(db, user_id, pid):
    return db.execute("SELECT * FROM ade_skill_proposals WHERE user_id = ? AND id = ?", (user_id, pid)).fetchone()


def _set(user_id, pid, now, was=None, **fields):
    """A proposal's columns, each change of state a timestamp (decided_at), set only while its state is still `was`."""
    if "state" in fields:
        fields.setdefault("decided_at", now)
    sets = ", ".join(f"{k} = ?" for k in fields)
    sql = f"UPDATE ade_skill_proposals SET {sets} WHERE user_id = ? AND id = ?"
    args = [*fields.values(), user_id, pid]
    if was is not None:
        sql += f" AND state IN ({','.join('?' * len(was))})"
        args += list(was)
    with store.connect() as db:
        return db.execute(sql, args).rowcount


def _note(user_id, pid, key, value):
    """One key of a proposal's notes set, in the one statement, so that a write of another key meanwhile is kept."""
    with store.connect() as db:
        db.execute("UPDATE ade_skill_proposals SET notes = json_set(COALESCE(notes, '{}'), ?, json(?)) WHERE user_id = ? "
                   "AND id = ?", (f"$.{key}", json.dumps(value), user_id, pid))


def _close(user, endpoint, call, pid, state, reason, now, was=None):
    """A proposal closed (refused, pruned, rejected, expired, withdrawn): its state and why, and the workspace asked to
    remove its ref where the ref still names its head, and the loop's folder for it; its bar goes on."""
    if not _set(user["id"], pid, now, was=was, state=state, reason=(reason or "")[:300] or None):
        return False
    if endpoint is not None:
        try:
            _call(call)(endpoint, "POST", f"/skill-improve/proposals/{pid}/close", {"state": state},
                        timeout=CALL_TIMEOUT)
        except Exception as e:  # noqa: BLE001  the ref stays where it was; the bar is the record's
            print(f"skill improve: closing {pid} in the workspace failed: {_said(e)}", file=sys.stderr)
    return True


def _session(user_id, kind, pid, cost, now):
    with store.connect() as db:
        improve.session(db, user_id, None, kind, now, proposal_id=pid, cost=cost if isinstance(cost, (int, float))
                        and not isinstance(cost, bool) else 0.0)


def _checked_texts(p, kind):
    """The texts and ids a review gave back, checked again by the app (shapes.check_business_proposal among them): None
    where they hold, else why."""
    try:
        fmt = p["object_format"]
        if fmt not in shapes.OBJECT_FORMATS or not _OIDS[fmt].fullmatch(p.get("base") or ""):
            return "its base is not a commit's id"
        if p.get("kind") != kind or not isinstance(p.get("folder"), str) or \
                not shapes.SKILL_NAME.fullmatch(p["folder"]) or p.get("path") != f".claude/skills/{p['folder']}/SKILL.md":
            return "its folder and path are not a skill's"
        if p.get("mode") not in _MODES:
            return "its mode is not a file's"
        current, proposed = p.get("current_text"), p.get("proposed_text")
        if not isinstance(current, str) or not isinstance(proposed, str):
            return "its texts are missing"
        if shapes.object_id("blob", current.encode("utf-8"), fmt) != p.get("current_blob") or \
                shapes.object_id("blob", proposed.encode("utf-8"), fmt) != p.get("blob"):
            return "its texts do not hash to its blobs"
        shapes.check_business_proposal(current, proposed)
        if kind == "change":
            shapes.check_skill_hypothesis(p.get("hypothesis"))
    except shapes.ShapeError as e:
        return str(e)
    except (KeyError, TypeError, AttributeError):
        return "it is not in the form a review gives"
    return None


def _insert(user_id, p, kind, counts_, notes, state, reason, now, rolls_back=None, current_version=None):
    with store.connect() as db:
        db.execute("INSERT INTO ade_skill_proposals (id, user_id, repo, folder, path, kind, base, branch, object_format, "
                   "mode, current_version, current_blob, blob, fresh, current_text, proposed_text, hypothesis, counts, "
                   "notes, state, reason, rolls_back, heads, created_at, decided_at) VALUES "
                   "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (p["id"], user_id, p.get("repo"), p.get("folder"), p.get("path"), kind, p.get("base"),
                    p.get("branch"), p.get("object_format"), p.get("mode"), current_version, p.get("current_blob"),
                    p.get("blob"), int(bool(p.get("fresh"))), p.get("current_text"), p.get("proposed_text"),
                    p.get("hypothesis"), json.dumps(counts_), json.dumps(notes), state, reason, rolls_back, "[]", now,
                    now if state != "critic" else None))


def keep(user, endpoint, call, got, now, kind="change", counts_=None, current_version=None, rolls_back=None,
         builds=None, inherit=None):
    """A proposal the workspace gave back (a review's, or a proposal to go back, which takes its repository's kind and
    the folder's names from `inherit`, the notes of the proposal it goes back from): checked again, kept, and carried
    on (carry_on) as far as it can go. Its id, or None where nothing was kept."""
    p = got.get("proposal") if isinstance(got, dict) else None
    if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not _ID.fullmatch(p["id"]):
        return None
    inherit = inherit or {}
    notes = {"keys": [k for k in (p.get("notes") or []) if k in shapes.BUSINESS_NOTE_KEYS],
             "builds": builds or [], "newer": any(isinstance(b, dict) and b.get("newer") for b in builds or []),
             "linked": bool(p.get("linked", inherit.get("linked"))), "url": p.get("url", inherit.get("url")),
             "before_version": p.get("before_version"),
             "names": [n for n in p.get("names") or inherit.get("names") or [] if isinstance(n, str)]}
    why = _checked_texts(p, kind)
    state, reason = ("refused", f"the app refused it: {why}") if why else ("critic", None)
    if not why and kind == "change" and stats.edits(p["current_text"], p["proposed_text"]) > improve.MAX_EDITS:
        state, reason = "pruned", f"it changes more than {improve.MAX_EDITS} places"
    _insert(user["id"], p, kind, counts_, notes, state, reason, now, rolls_back=rolls_back,
            current_version=current_version or p.get("current_version"))
    if state != "critic":
        _close(user, endpoint, call, p["id"], state, reason, now)
        return p["id"]
    carry_on(user, endpoint, call, p["id"], now)
    return p["id"]


def carry_on(user, endpoint, call, pid, now):
    """A proposal in `critic` taken as far as it can go now: the app's copy of the record read until it holds the
    proposal; its objects asked for and checked; its critic asked (a proposal to go back has none, its text being a
    version the branch held); and its ref published, the proposal then `open`. A call that did not get through leaves it
    where it is, for the next step; a refusal refuses the proposal. Its state."""
    uid = user["id"]
    with store.connect() as db:
        row = _row(db, uid, pid)
    if row is None or row["state"] != "critic":
        return row["state"] if row else None
    notes = _json(row["notes"], {})

    def refuse(why):
        _close(user, endpoint, call, pid, "refused", why, now, was=("critic",))
        return "refused"

    try:
        if not notes.get("record_read"):
            read_record(user, endpoint, call)
            if not _record_holds(uid, pid, row["blob"], row["path"], row["base"]):
                return refuse(NOT_A_RECORD)
            notes["record_read"] = True
            send_word(user, endpoint, call)
            _set(uid, pid, now, notes=json.dumps(notes))
        if not row["head"]:
            sent = _call(call)(endpoint, "POST", f"/skill-improve/reviews/{pid}/objects",
                               {"text": row["proposed_text"]}, timeout=CALL_TIMEOUT)
            checked = check_objects(sent, row, expect=row["base"])
            _set(uid, pid, now, head=checked["head"])
        if row["kind"] == "change" and "critic" not in notes:
            with store.connect() as db:
                spent = improve.budget(db, uid, now)["spent"]
            if spent:
                # no critic while the week's budget is spent, as no review starts then: the proposal waits, and expires
                # as one in critic does (before, a critic whose answer did not come back was
                # asked again at every step, whatever the budget said)
                print(f"skill improve: {pid} waits for its critic: the week's budget is spent", file=sys.stderr)
                return "critic"
            got = _call(call)(endpoint, "POST", f"/skill-improve/reviews/{pid}/critic",
                              {"text": row["proposed_text"], "hypothesis": row["hypothesis"]}, timeout=CRITIC_TIMEOUT)
            got = got if isinstance(got, dict) else {}
            _session(uid, "skill_critic", pid, got.get("cost"), now)
            notes["critic"] = {"specific": got.get("specific") is not False,
                               "why": str(got.get("why") or "")[:300] or None}
            _set(uid, pid, now, notes=json.dumps(notes))
        if row["kind"] == "change" and notes["critic"]["specific"]:
            return refuse("the critic: " + (notes["critic"]["why"] or "it names a specific input, file or answer"))
        with store.connect() as db:
            row = _row(db, uid, pid)
        # the copy read again first: a commit reaches the shared repository only once the app's copy holds it, so
        # that a restore keeps it barred whatever happens to the workspace's file after
        read_record(user, endpoint, call)
        if not _record_holds(uid, pid, row["blob"], row["path"], row["base"], commit=row["head"]):
            return refuse(NOT_A_RECORD + " with its commit")
        published = _call(call)(endpoint, "POST", f"/skill-improve/proposals/{pid}/publish", {}, timeout=CALL_TIMEOUT)
        if not isinstance(published, dict) or published.get("head") != row["head"]:
            return refuse("its published ref does not name the commit the app checked")
        _set(uid, pid, now, was=("critic",), state="open", reason=None)
        read_record(user, endpoint, call)
        return "open"
    except SkillImproveError as e:
        return refuse(str(e))
    except Exception as e:  # noqa: BLE001  a workspace's failure, said in its own words or the app's
        if _transient(e):
            print(f"skill improve: {pid} waits for the next step: {_said(e)}", file=sys.stderr)
            return "critic"
        from . import workspace_client
        if not isinstance(e, workspace_client.WorkspaceCallError):
            return refuse(f"the app failed carrying it on ({type(e).__name__})")
        return refuse(RESTARTED if "No review of this proposal" in str(e) else f"the workspace refused it: {_said(e)}")


# ---- the schedule ---------------------------------------------------------------------------------------------------

_tried = {}


def _reviewed_today(db, user_id, now):
    return db.execute("SELECT 1 FROM ade_improve_sessions WHERE user_id = ? AND kind = 'skill_review' AND at >= ?",
                      (user_id, now - now % DAY)).fetchone() is not None


def maybe_skill_review(user, endpoint, call=None, now=None):
    """The day's business review for the account, when one is due: the candidate with the highest score
    among the repositories switched on, reviewed by the workspace, its proposal kept (keep). Its proposal's id, or
    None."""
    now = _now(now)
    uid = user["id"]
    with store.connect() as db:
        if improve.budget(db, uid, now)["spent"] or _reviewed_today(db, uid, now):
            return None
        if db.execute("SELECT 1 FROM ade_skill_proposals WHERE user_id = ? AND state = 'critic'", (uid,)).fetchone():
            return None
        repos = db.execute("SELECT * FROM ade_skill_improve_repos WHERE user_id = ? AND turned_on = 1 ORDER BY repo",
                           (uid,)).fetchall()
    if not repos or now - _tried.get(uid, 0) < TRY_EVERY:
        return None
    _tried[uid] = now
    try:
        got = _call(call)(endpoint, "GET", "/repos", timeout=CALL_TIMEOUT)
        listed = {r.get("name") for r in (got.get("repos") if isinstance(got, dict) else None) or []
                  if isinstance(r, dict)}
    except Exception as e:  # noqa: BLE001  a workspace that cannot be reached starts no review
        print(f"skill improve: no review for {uid}: the workspace's repositories were not read: {_said(e)}",
              file=sys.stderr)
        return None
    best = None
    for repo_row in repos:
        repo = repo_row["repo"]
        if repo not in listed:
            continue
        try:
            got = _call(call)(endpoint, "GET", f"/skill-improve/candidates?repo={repo}", timeout=CALL_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            print(f"skill improve: {repo}'s candidates were not read: {_said(e)}", file=sys.stderr)
            continue
        if not isinstance(got, dict) or not got.get("ok"):
            continue
        builds = got.get("builds") or []
        if any(not isinstance(b, dict) or b.get("version") is None for b in builds):
            continue                        # a claude whose version cannot be read: no review
        reviewed = _json(repo_row["reviewed"], {})
        with store.connect() as db:
            for cand in got.get("candidates") or []:
                folder = cand.get("folder") if isinstance(cand, dict) else None
                names = [n for n in cand.get("names") or [] if isinstance(n, str)] if folder else []
                if not folder or not names or other_named(db, uid, repo, names):
                    continue
                if db.execute(f"SELECT 1 FROM ade_skill_proposals WHERE user_id = ? AND repo = ? AND folder = ? AND "
                              f"state IN ({','.join('?' * len(OPEN))})", (uid, repo, folder, *OPEN)).fetchone():
                    continue
                last = reviewed.get(folder) or 0
                if now - last < SKILL_COOLDOWN:
                    continue
                current, before = cand.get("current_version"), cand.get("before_version")
                value, with_outcome = score(db, uid, repo, names, current, before, last or None, now)
                if with_outcome < MIN_CALLS or value <= 0:
                    continue
                key = (value, -last)
                if best is None or key > best[0]:
                    best = (key, repo, folder, names, current, before, builds,
                            counts(db, uid, repo, names, current, before))
    if best is None:
        return None
    _, repo, folder, names, current, before, builds, counts_ = best
    pid = secrets.token_hex(6)
    try:
        got = _call(call)(endpoint, "POST", "/skill-improve/reviews", {"id": pid, "repo": repo, "folder": folder,
                                                                      "counts": counts_, "current_version": current},
                          timeout=REVIEW_TIMEOUT)
    except Exception as e:  # noqa: BLE001  the workspace may have run the turn and recorded its proposal
        # counted as the day's review and the folder's, so that it is not run again within the day uncounted; kept
        # refused under its id, and closed in the workspace where its record holds it, so that a later repeat of its
        # text names a proposal the app knows
        # a refusal of the workspace's said as one, in its words (a branch longer than a look reads among them), never
        # as an answer that did not come back
        from . import workspace_client
        refused = isinstance(e, workspace_client.WorkspaceCallError) and e.status in workspace_client.REFUSALS
        said = "the workspace refused its review" if refused else "its review's answer did not come back"
        print(f"skill improve: the review of {repo}/{folder}: {said}: {_said(e)}", file=sys.stderr)
        got = {"refused": f"{said}: {_said(e)}"[:300], "lost": True}
    got = got if isinstance(got, dict) else {}
    _session(uid, "skill_review", pid, got.get("cost"), now)
    with store.connect() as db:
        row = _repo_row(db, uid, repo)
        reviewed = _json(row["reviewed"], {}) if row else {}
        reviewed[folder] = now
        db.execute("UPDATE ade_skill_improve_repos SET reviewed = ? WHERE user_id = ? AND repo = ?",
                   (json.dumps(reviewed), uid, repo))
    if got.get("proposal") is None:
        if got.get("refused"):
            with store.connect() as db:
                db.execute("INSERT INTO ade_skill_proposals (id, user_id, repo, folder, path, kind, counts, notes, "
                           "state, reason, created_at, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (pid, uid, repo, folder, f".claude/skills/{folder}/SKILL.md", "change", json.dumps(counts_),
                            json.dumps({"builds": builds}), "refused", str(got["refused"])[:300], now, now))
            if got.get("lost"):
                try:
                    _call(call)(endpoint, "POST", f"/skill-improve/proposals/{pid}/close", {"state": "refused"},
                                timeout=CALL_TIMEOUT)
                except Exception as e:  # noqa: BLE001  a record that never held it, or a workspace still away
                    print(f"skill improve: closing {pid} in the workspace failed: {_said(e)}", file=sys.stderr)
        return None
    return keep(user, endpoint, call, got, now, counts_=counts_, current_version=current, builds=builds)


# ---- the look, the expiry and the watch -----------------------------------------------------------------------------

_looked = {}


def look(user, endpoint, call, repo, now=None):
    """The branch's head read by the workspace for each of the repository's proposals not ended (GET
    /skill-improve/branches), and each row moved as it says: reached by one of its commits or by its content, `merged`
    where the owner's tap did it, else `found`, with what showed it; its ref no longer naming its head, `changed`; its
    skill's folder changed on the branch, `outdated`; its pull request closed on GitHub without a merge, `rejected`.
    The workspace's answer, or None where it gave none."""
    now = _now(now)
    uid = user["id"]
    try:
        got = _call(call)(endpoint, "GET", f"/skill-improve/branches?repo={repo}", timeout=CALL_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        print(f"skill improve: the look at {repo} failed: {_said(e)}", file=sys.stderr)
        return None
    _looked[(uid, repo)] = now
    if not isinstance(got, dict) or not got.get("ok") or not isinstance(got.get("proposals"), dict):
        return got if isinstance(got, dict) else None
    with store.connect() as db:
        rows = {r["id"]: r for r in db.execute("SELECT * FROM ade_skill_proposals WHERE user_id = ? AND repo = ? AND "
                                               "ended IS NULL", (uid, repo))}
    builds = got.get("builds")
    for pid, seen in got["proposals"].items():
        row = rows.get(pid)
        if row is None or not isinstance(seen, dict):
            continue
        if isinstance(builds, list):            # read again at each view
            _note(uid, pid, "view_builds", {"at": now, "builds": builds})
        ended = seen.get("ended")
        if isinstance(ended, dict) and ended.get("how") in ("merged", "taken", "back"):
            # the workspace ended its bar at the owner's tap (a merge, a take, or a merged proposal to go back), the
            # app's call of it lost: recorded so, never as found
            fields = {"ended": ended["how"], "ended_at": ended.get("at") if isinstance(ended.get("at"), int) else now}
            if ended["how"] == "merged":
                fields.update(state="merged", merged_at=fields["ended_at"], reason=None,
                              found=json.dumps({"version": seen.get("change_commit") or seen.get("head")
                                                or got.get("head")}))
            _set(uid, pid, now, was=(row["state"],), **fields)
            continue
        if seen.get("reached") in ("commit", "content"):
            if row["state"] not in ("merged", "found"):
                # the head of the proposal's own branch, where the workspace read it
                found = {"by": seen["reached"], "commit": seen.get("change_commit"),
                         "head": seen.get("head") or got.get("head"), "at": now}
                _set(uid, pid, now, was=(row["state"],), state="found", found=json.dumps(found),
                     reason="it reached the branch without the owner's tap")
            continue
        if row["state"] == "pull_request" and isinstance(seen.get("pull_request"), dict) and \
                seen["pull_request"].get("state") == "closed" and not seen["pull_request"].get("merged"):
            # closed as any rejected proposal is: its ref removed in the shared repository, the record's state set
            _close(user, endpoint, call, pid, REJECTED, "its pull request was closed on GitHub without a merge; its "
                   "branch is left on origin", now, was=("pull_request",))
            continue
        if row["state"] == "open" and "ref" in seen and seen.get("ref") != row["head"]:
            # its ref against the commit the app checked and published (a remake whose publish did not go through
            # leaves both on the commit before); a pull request's merge reads GitHub's view, not this ref
            _set(uid, pid, now, was=("open",), state="changed",
                 reason="its ref no longer names its commit: a program moved or removed it")
        elif row["state"] in ("open", "pull_request") and seen.get("folder_unchanged") is False:
            _set(uid, pid, now, was=("open", "pull_request"), state="outdated",
                 reason="a file of its skill's folder changed on the branch since its review: a new review is the way")
    return got


def expire(user, endpoint, call, now):
    """Each proposal on its way past EXPIRES (14 days), as an agent's expires: `expired`, its ref removed. A pull
    request left open on GitHub is left there."""
    with store.connect() as db:
        due = [r["id"] for r in db.execute(
            f"SELECT id FROM ade_skill_proposals WHERE user_id = ? AND state IN ({','.join('?' * len(OPEN))}) AND "
            "created_at < ?", (user["id"], *OPEN, now - EXPIRES))]
    for pid in due:
        _close(user, endpoint, call, pid, "expired", "not decided in 14 days", now, was=OPEN)


def watch_skills(db, user_id, now):
    """Each merged or taken version not yet watched: once it has WATCH calls with an outcome, and the
    version it replaced as many among the calls kept, the passes of its first WATCH against the replaced version's last
    WATCH, by the exact Fisher test; watched once. The proposals a drop at p <= P_MAX calls for a proposal to go back
    for, with their p and counts."""
    out = []
    for p in db.execute("SELECT * FROM ade_skill_proposals WHERE user_id = ? AND kind = 'change' AND ended IN "
                        "('merged', 'taken') AND watched_at IS NULL AND repo IN (SELECT repo FROM "
                        "ade_skill_improve_repos WHERE user_id = ? AND turned_on = 1)", (user_id, user_id)).fetchall():
        after_version = (_json(p["found"], {}) or {}).get("version") or p["head"]
        if not after_version or not p["current_version"]:
            continue
        names = (_json(p["notes"], {}) or {}).get("names") or [p["folder"]]
        where, args = _where(user_id, p["repo"], names, after_version)
        after = [r[0] == 0 for r in db.execute(f"SELECT c.failed FROM ade_skill_calls c WHERE {where} AND c.failed IS "
                                               "NOT NULL ORDER BY c.at, c.line_id LIMIT ?", args + [stats.WATCH])]
        if len(after) < stats.WATCH:
            continue
        where, args = _where(user_id, p["repo"], names, p["current_version"])
        before = [r[0] == 0 for r in db.execute(f"SELECT c.failed FROM ade_skill_calls c WHERE {where} AND c.failed "
                                                "IS NOT NULL ORDER BY c.at DESC, c.line_id LIMIT ?",
                                                args + [stats.WATCH])]
        db.execute("UPDATE ade_skill_proposals SET watched_at = ? WHERE id = ?", (now, p["id"]))
        if len(before) < stats.WATCH:
            continue
        p_value = stats.fisher_drop(sum(after), len(after), sum(before), len(before))
        if p_value <= stats.P_MAX:
            out.append({"id": p["id"], "p": round(p_value, 6), "after": sum(after), "before": sum(before)})
    return out


def go_back(user, endpoint, call, row, now, why=None):
    """A proposal to go back for `row` (a found one, at the owner's tap; or a merged or taken one the watch found
    failing more often): made by the workspace on the branch's head, its text the version `row` replaced, then kept as
    any proposal is, with no critic. Its id, or None."""
    pid = secrets.token_hex(6)
    got = _call(call)(endpoint, "POST", f"/skill-improve/proposals/{row['id']}/go-back",
                      {"id": pid, "text": row["current_text"]}, timeout=CALL_TIMEOUT)
    notes = _json(row["notes"], {}) or {}
    kept = keep(user, endpoint, call, got, now, kind="back", rolls_back=row["id"], builds=notes.get("builds"),
                inherit=notes, current_version=(_json(row["found"], {}) or {}).get("version") or row["head"])
    if kept and why:
        with store.connect() as db:
            db.execute("UPDATE ade_skill_proposals SET hypothesis = ? WHERE id = ?", (why[:300], kept))
    return kept


def step(user, endpoint, call=None, now=None):
    """The business skills' loop's step for the account, in its thread of the judge and the loop (agents._slow_work,
    after _improve): proposals past 14 days expired; each repository's proposals looked at once a day; the proposals on
    their way carried on; merged versions watched, a drop making a proposal to go back; and the day's review."""
    now = _now(now)
    uid = user["id"]
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM ade_skill_improve_repos WHERE user_id = ? AND turned_on = 1 UNION SELECT 1 FROM "
                          "ade_skill_proposals WHERE user_id = ? LIMIT 1", (uid, uid)).fetchone():
            return
        repos = [r[0] for r in db.execute("SELECT DISTINCT repo FROM ade_skill_proposals WHERE user_id = ? AND ended "
                                          "IS NULL", (uid,))]
    send_word(user, endpoint, call)
    expire(user, endpoint, call, now)
    for repo in repos:
        if now - _looked.get((uid, repo), 0) >= LOOK_EVERY:
            look(user, endpoint, call, repo, now)
    with store.connect() as db:
        waiting = [r[0] for r in db.execute("SELECT id FROM ade_skill_proposals WHERE user_id = ? AND state = 'critic' "
                                            "ORDER BY created_at", (uid,))]
    for pid in waiting:
        carry_on(user, endpoint, call, pid, now)
    with store.connect() as db:
        drops = watch_skills(db, uid, now)
    for drop in drops:
        with store.connect() as db:
            row = _row(db, uid, drop["id"])
        try:
            go_back(user, endpoint, call, row, now, why=f"Go back: its first {stats.WATCH} calls with an outcome "
                    f"passed {drop['after']} times, the {stats.WATCH} before it {drop['before']} (p = {drop['p']}).")
        except Exception as e:  # noqa: BLE001  watched once; the page shows the version's counts
            print(f"skill improve: no proposal to go back for {drop['id']}: {_said(e)}", file=sys.stderr)
    maybe_skill_review(user, endpoint, call, now)


# ---- the owner's taps -----------------------------------------------------------------------------------------------

def _endpoint_of(user):
    endpoint = agents._endpoint(user)
    if endpoint is None:
        raise SkillImproveError(409, "The workspace is not running: start it, then try again.")
    return endpoint


def _owned(user_id, pid):
    if not isinstance(pid, str) or not _ID.fullmatch(pid):
        raise SkillImproveError(404, "No such proposal.")
    with store.connect() as db:
        row = _row(db, user_id, pid)
    if row is None:
        raise SkillImproveError(404, "No such proposal.")
    return row


def _way(user, pid, action, body=None, call=None, timeout=CALL_TIMEOUT):
    endpoint = _endpoint_of(user)
    send_word(user, endpoint, call)
    try:
        return endpoint, _call(call)(endpoint, "POST", f"/skill-improve/proposals/{pid}/{action}", body or {},
                                     timeout=timeout)
    except Exception as e:  # noqa: BLE001  the workspace's words, said back
        raise SkillImproveError(409, _said(e)) from None


def merge(user, pid, method="squash", call=None, now=None):
    """The owner's Merge: into a branch of his own copy (the workspace's accept), or on GitHub once a pull request is
    open (the workspace's merge, which reads GitHub's own view first). Each only for a proposal `open` or
    `pull_request`: into his copy, its ref naming its head; on GitHub, the pull request's head being the proposal's
    commit, as GitHub's view says (the workspace reads no ref there). Either way the commit the page shows (the row's
    head) is sent, and the workspace refuses to merge another. Recorded as merged by the owner, the version after it
    kept for the watch: on GitHub, the version the workspace read from origin's branch after the merge, the skill
    folder's last-changing commit, which a session's call names (before, the branch's head was kept)."""
    now = _now(now)
    row = _owned(user["id"], pid)
    if row["state"] not in ("open", "pull_request") or row["ended"] is not None:
        raise SkillImproveError(409, f"A proposal that is {row['state']} is not merged.")
    if row["state"] == "pull_request":
        if method not in ("squash", "merge", "rebase"):
            raise SkillImproveError(400, "A merge is a squash, a merge or a rebase.")
        endpoint, got = _way(user, pid, "merge", {"url": row["pr_url"], "method": method, "head": row["head"]}, call)
        version = got.get("version") if isinstance(got, dict) else None
    else:
        endpoint, got = _way(user, pid, "accept", {"head": row["head"]}, call=call)
        if not isinstance(got, dict) or got.get("merged") is not True:
            why = (got or {}).get("why") if isinstance(got, dict) else None
            raise SkillImproveError(409, f"It was not merged: {why or 'the check after the move failed'}; "
                                         f"{_undo_words(got)}")
        version = got.get("head")
    with store.connect() as db:
        notes = _json(_row(db, user["id"], pid)["notes"], {})
    notes["merge"] = got if isinstance(got, dict) else None
    if isinstance(got, dict) and isinstance(got.get("builds"), list):     # read again at the acceptance
        notes["accept_builds"] = {"at": now, "builds": got["builds"]}
    _set(user["id"], pid, now, state="merged", ended="merged", ended_at=now, merged_at=now, reason=None,
         found=json.dumps({"version": version}), notes=json.dumps(notes))
    read_record(user, endpoint, call)
    return proposal(user, pid, look_now=False)


def _undo_words(got):
    if isinstance(got, dict) and got.get("undone") is False:
        return f"the undo could not be made, and it left: {json.dumps(got.get('left'))}"
    return "it was undone"


def pull_request(user, pid, call=None, now=None):
    """The owner's first tap on GitHub: the proposal's branch pushed to origin and its pull request opened."""
    now = _now(now)
    row = _owned(user["id"], pid)
    if row["state"] != "open":
        raise SkillImproveError(409, f"A proposal that is {row['state']} has no pull request opened.")
    endpoint, got = _way(user, pid, "pull-request", {"counts": _json(row["counts"])}, call)
    url = got.get("url") if isinstance(got, dict) else None
    if not isinstance(url, str) or not url.startswith("https://github.com/"):
        raise SkillImproveError(409, "The workspace gave no pull request's address.")
    _set(user["id"], pid, now, was=("open",), state="pull_request", pr_url=url[:300])
    read_record(user, endpoint, call)
    return proposal(user, pid, look_now=False)


def remake(user, pid, call=None, now=None):
    """The owner's Bring it up to date (his copy, where only other paths moved): the same proposal on a new commit, its
    new objects checked as any proposal's and its blob, path, mode and texts its own, the base and head it replaces kept
    in `heads`, then published; no new review or critic."""
    now = _now(now)
    row = _owned(user["id"], pid)
    if row["state"] not in ("open", "outdated") or row["ended"] is not None:
        raise SkillImproveError(409, f"A proposal that is {row['state']} is not brought up to date.")
    endpoint, sent = _way(user, pid, "remake", call=call)
    new = dict(row)
    new["base"] = sent.get("base") if isinstance(sent, dict) else None
    checked = check_objects(sent, new)
    # the new commit kept first, so that a restore gives it back whatever happens next (checked_commits); the row moves
    # to it only once it is published, so that a publish that does not go through leaves the row and its ref on the
    # commit before, for the owner to bring it up to date again
    _note(user["id"], pid, "remade", {"base": checked["base"], "head": checked["head"], "at": now})

    def unpublished(why):
        # outdated, so that the page offers bringing it up to date again rather than a merge its ref would refuse
        _set(user["id"], pid, now, was=("open", "outdated"), state="outdated",
             reason=f"it was brought up to date, but not published: {why}; bring it up to date again"[:300])
        return SkillImproveError(409, f"It was brought up to date, but not published: {why}. Bring it up to date "
                                      "again.")

    read_record(user, endpoint, call)
    if not _record_holds(user["id"], pid, row["blob"], row["path"], checked["base"], commit=checked["head"]):
        raise unpublished("the app's copy of the record does not hold its new commit")
    try:
        published = _call(call)(endpoint, "POST", f"/skill-improve/proposals/{pid}/publish", {}, timeout=CALL_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        raise unpublished(_said(e)) from None
    if not isinstance(published, dict) or published.get("head") != checked["head"]:
        raise unpublished("its published ref does not name the commit the app checked")
    heads = _json(row["heads"], []) + [{"base": row["base"], "head": row["head"], "at": now}]
    _set(user["id"], pid, now, base=checked["base"], head=checked["head"], heads=json.dumps(heads), state="open",
         reason=None)
    read_record(user, endpoint, call)
    return proposal(user, pid, look_now=False)


def reject(user, pid, reason=None, call=None, now=None):
    """The owner's Reject, with a reason: its ref removed; its bar goes on."""
    now = _now(now)
    row = _owned(user["id"], pid)
    if row["state"] not in ("open", "pull_request", "outdated", "changed") or row["ended"] is not None:
        raise SkillImproveError(409, f"A proposal that is {row['state']} is not rejected.")
    _close(user, _endpoint_of(user), call, pid, "rejected", (reason or "rejected by the owner").strip()[:300], now)
    return proposal(user, pid, look_now=False)


def take(user, pid, call=None, now=None):
    """The owner's Take it as mine, for a found proposal: its bar ends, and the version is watched."""
    now = _now(now)
    row = _owned(user["id"], pid)
    if row["state"] != "found" or row["ended"] is not None:
        raise SkillImproveError(409, "Only a found proposal is taken as his.")
    endpoint, got = _way(user, pid, "take", call=call)
    found = _json(row["found"], {}) or {}
    found["version"] = found.get("commit")
    _set(user["id"], pid, now, ended="taken", ended_at=now, found=json.dumps(found))
    read_record(user, endpoint, call)
    return proposal(user, pid, look_now=False)


def owners_go_back(user, pid, call=None, now=None):
    """The owner's Go back, for a found proposal: a proposal to go back made at once."""
    now = _now(now)
    row = _owned(user["id"], pid)
    if row["state"] != "found" or row["ended"] is not None:
        raise SkillImproveError(409, "Only a found proposal is gone back from at the owner's tap.")
    endpoint = _endpoint_of(user)
    send_word(user, endpoint, call)
    try:
        made = go_back(user, endpoint, call, row, now)
    except Exception as e:  # noqa: BLE001
        raise SkillImproveError(409, _said(e)) from None
    if made is None:
        raise SkillImproveError(409, "No proposal to go back was made.")
    return proposal(user, made, look_now=False)


# ---- the page's views -----------------------------------------------------------------------------------------------

def diff(was, now):
    """The whole diff of two texts whose blobs the app checked, as improve.diff makes its lines, without its cut."""
    import difflib
    lines = list(difflib.unified_diff((was or "").splitlines(), (now or "").splitlines(), n=2, lineterm=""))[2:]
    return [line if line.startswith(("+", "-", "@")) else " " + line[1:] for line in lines]


def _ways(row):
    """The taps the page offers for a proposal, as its state and its repository's kind allow."""
    notes = _json(row["notes"], {}) or {}
    linked, github = notes.get("linked"), bool(_GITHUB.match((notes.get("url") or "").strip()))
    out = []
    if row["ended"] is None:
        if row["state"] == "open" and linked:
            out += ["merge", "remake"]
        elif row["state"] == "open" and github:
            out.append("pull_request")
        elif row["state"] == "pull_request":
            out.append("merge")
        elif row["state"] == "outdated" and linked:
            out.append("remake")
        if row["state"] in ("open", "pull_request", "outdated", "changed"):
            out.append("reject")
        if row["state"] == "found":
            out += ["take", "go_back"]
    return out


def _reads(row, notes):
    """Each read of the builds the page names: at the review, at the newest view, at the acceptance."""
    out = [{"when": "review", "at": row["created_at"], "builds": notes.get("builds") or []}]
    for when, key in (("view", "view_builds"), ("acceptance", "accept_builds")):
        got = notes.get(key)
        if isinstance(got, dict) and isinstance(got.get("builds"), list):
            out.append({"when": when, "at": got.get("at"), "builds": got["builds"]})
    return out


def proposal_view(row, bars_now=None, now=None):
    now = _now(now)
    notes = _json(row["notes"], {}) or {}
    return {"id": row["id"], "repo": row["repo"], "folder": row["folder"], "path": row["path"], "kind": row["kind"],
            "branch": row["branch"], "base": row["base"], "head": row["head"], "heads": _json(row["heads"], []),
            "object_format": row["object_format"], "mode": row["mode"], "current_version": row["current_version"],
            "before_version": notes.get("before_version"), "fresh": bool(row["fresh"]) if row["fresh"] is not None
            else None, "hypothesis": row["hypothesis"], "counts": _json(row["counts"]), "state": row["state"],
            "reason": row["reason"], "pr_url": row["pr_url"], "found": _json(row["found"]),
            "rolls_back": row["rolls_back"], "ended": row["ended"], "ended_at": row["ended_at"],
            "created_at": row["created_at"], "decided_at": row["decided_at"], "merged_at": row["merged_at"],
            "watched_at": row["watched_at"],
            "expires_at": row["created_at"] + EXPIRES if row["state"] in OPEN else None,
            "notes": {"keys": notes.get("keys") or [], "builds": notes.get("builds") or [],
                      "newer": bool(notes.get("newer")), "linked": bool(notes.get("linked")),
                      "url": notes.get("url"), "critic": notes.get("critic"), "merge": notes.get("merge"),
                      "builds_read": list(shapes.BUILDS_READ), "reads": _reads(row, notes)},
            "diff": diff(row["current_text"], row["proposed_text"]) if row["current_text"] is not None else [],
            "ways": _ways(row), "bars_now": bars_now}


def budget_by_kind(db, user_id, now):
    """The week's loop sessions by kind, both loops', for the page: the shared budget's use."""
    used = {k: 0 for k in improve.COUNTED}
    for r in db.execute("SELECT kind, COUNT(*) AS n FROM ade_improve_sessions WHERE user_id = ? AND at >= ? GROUP BY "
                        "kind", (user_id, now - improve.WEEK)):
        if r["kind"] in used:
            used[r["kind"]] = r["n"]
    return dict(improve.budget(db, user_id, now), by_kind=used)


# the states of a proposal not ended that the page offers the owner a tap for (_ways), each listed whatever its age
DECIDE = ("open", "pull_request", "outdated", "changed", "found")
LIST_OTHERS = 200       # proposals listed at most besides those, newest first


def listing(user, now=None):
    """The loop's list, newest first: every business proposal for the owner to decide (DECIDE), whatever its age, so
    that a found proposal that bars keeps its taps (before, the newest 200 alone were listed),
    and the newest LIST_OTHERS of the others, every state; the week's budget by kind; the switches; and the app's copy
    of the record, its generation and when it was read."""
    now = _now(now)
    with store.connect() as db:
        marks = ",".join("?" * len(DECIDE))
        rows = db.execute(f"SELECT * FROM ade_skill_proposals WHERE user_id = ? AND ended IS NULL AND state IN ({marks}) "
                          "UNION SELECT * FROM (SELECT * FROM ade_skill_proposals WHERE user_id = ? AND NOT (ended IS "
                          f"NULL AND state IN ({marks})) ORDER BY created_at DESC, id LIMIT ?) ORDER BY created_at DESC, "
                          "id", (user["id"], *DECIDE, user["id"], *DECIDE, LIST_OTHERS)).fetchall()
        switches = [switch_view(r) for r in db.execute("SELECT * FROM ade_skill_improve_repos WHERE user_id = ? ORDER "
                                                      "BY repo", (user["id"],))]
        copy = _copy(db, user["id"])
        budget = budget_by_kind(db, user["id"], now)
    return {"proposals": [proposal_view(r, now=now) for r in rows], "budget": budget, "switches": switches,
            "copy": {"generation": copy["generation"], "read_at": copy["read_at"]} if copy else None}


def proposal(user, pid, look_now=True, call=None, now=None):
    """One proposal, as the page shows it, its repository's proposals looked at first (the look at each view), and
    whether a start from what the shared repository has checked out is refused now, with the branch and the commit that
    is and when it was read, which the page says, since it views a repository once while it shows the list."""
    now = _now(now)
    row = _owned(user["id"], pid)
    bars_now = None
    if look_now:
        endpoint = agents._endpoint(user)
        if endpoint is not None:
            got = look(user, endpoint, call, row["repo"], now)
            if isinstance(got, dict) and got.get("ok"):
                at = got.get("bars_at") if isinstance(got.get("bars_at"), dict) else {}
                bars_now = {"refused": got.get("bars_now"), "head": at.get("head"), "branch": at.get("branch"),
                            "at": now}
        row = _owned(user["id"], pid)
    return proposal_view(row, bars_now=bars_now, now=now)


def candidates(user, repo, call=None):
    """The workspace's candidates of a repository switched on, with why each other folder is not one (the Skills
    view's row of each business skill)."""
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise SkillImproveError(404, "No such repository.")
    endpoint = _endpoint_of(user)
    send_word(user, endpoint, call)
    try:
        return _call(call)(endpoint, "GET", f"/skill-improve/candidates?repo={repo}", timeout=CALL_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        raise SkillImproveError(409, _said(e)) from None


def record_view(user, call=None):
    """The workspace's record as it stands for the page: its state, whether it is held and why, and the starts the bar
    refused; with the app's copy's generation. Read only while the workspace runs."""
    endpoint = agents._endpoint(user)
    got = None
    if endpoint is not None:
        try:
            got = read_record(user, endpoint, call)
        except Exception as e:  # noqa: BLE001  the page says the workspace did not answer
            got = {"error": _said(e)}
    with store.connect() as db:
        copy = _copy(db, user["id"])
    view = {k: got.get(k) for k in ("state", "held", "word", "generation", "refused", "error")} if got else None
    return {"running": endpoint is not None, "record": view,
            "copy": {"generation": copy["generation"], "read_at": copy["read_at"]} if copy else None}
