"""Handing a task to a teammate:
an offer the owner presses, standing custody, every way back, Take back, Keep and Give back, with their holds
("task.offer", and "task.crossing" for custody's own crossing tick), confirmations and content-free notices. Offered
only under the fly provider, while OPEN_EDITION_ADE_TASK_TEAMMATE is on (tasks.on("teammate")), while each confirmation
this track takes is made for one named change (passkeys.CHANGE), and while OPEN_EDITION_OPERATOR names the operator
(risk_texts.operator()). Its routes are `router`, under /api/ade/teammate, which main.py includes.

Who calls what across modules:
- ade/tasks.py calls custody_candidate and begin_custody (the relay's step 2), after_attempt_end (an offer asked
  for while an attempt ran), on_step_end (a custody task's way back on fresh evidence) and enabled; the scheduler
  calls forget each round; proofpane_open/__main__.py calls person_rows and reduce_person for the operator's
  command, `account-record list` and `reduce`;
- this module calls tasks.get, view, create, set_state, relay_set, unknown_effects, take_return, journal and
  account_record (the owner's account record gains a "handoff" entry, with the task's step, in the transaction
  that hands the task over, the teammate named by their user id with the plan of the account their agent takes it on,
  and its organisation only where that is a Team or Enterprise organisation's seat, from that account's row as it
  stands when they accept, never their email), and the owner's and the teammate's workspaces' push, fetch and checks
  routes through workspace_client; and two more of tasks.py's: relay_groups (the
  groups of a task's relay set) and custody_digest (what custody's confirmation covers, which tasks.py's "Answer, and
  confirm for custody" keeps too);
- this module calls risk_texts.operator() in enabled, and risk_texts.text(13) for the `terms` each received offer
  and each custody request as the custodian sees it carries, {owner} filled with the owner as the view names them.

A task received by a pressed offer, like a custody task, is made by tasks.create with its continued_from, so it stays in
the group it was accepted in: the relay never crosses with it, and its switch is off and not to be turned on; but a
custody task is made with custody_cross 1 in `fields` where the owner's task has custody's crossing tick and its
crossings_ended is 0, and the relay then lets it cross as it lets a crossing, whatever its switch and the deployment's
crossing setting, the column fixed for the task's life. Custody hands a task whose crossings ended (tasks.get's
crossings_ended), whatever ended them, only to a
receiving agent in the group it is on, its tick or not, the received task made with custody_cross 0, so the custodian's
relay keeps it there; the custody-cross route refuses the tick (412) on such a task, and the page does not offer it.
Risk points 12 and 13 are the page's: the teammate track's components open them through RiskTerms over
the owner's offer and custody controls and over a teammate's acceptance (point 13's cases: "offer" or "custody",
"adopted_check", and "other_group" for an offer's agent in other_group_agents and for every custody acceptance);
nothing here knows of a pop-up, so an acceptance made by a call to the API goes through, having been given point 13's
text as `terms`. Until an offer or a custody request is accepted, the teammate and the typed address are in
its row alone: an end without acceptance (an offer declined, withdrawn, expired or failed; a custody request declined,
expired, or ended while "asked") sets both to NULL, and no journal entry or hold names the teammate, the offer's and the
request's entries carrying their ids and the holds' summaries ids alone. An accepted custody that has ended has its
custodian and typed address set to NULL CUSTODY_KEEP_DAYS after its ended_at (forget).

Where the track is off (the setting, the confirmations' purposes, the local provider, OPEN_EDITION_OPERATOR unset),
every route that starts or advances a hand-off answers 409 {"detail": ..., "off": "teammate"}, the detail saying which:
agents.AgentError(409, words, off=OFF), the extra AgentError takes. Those are making, confirming and accepting an offer,
asking for custody, confirming and accepting it, confirming a task for custody, custody's crossing, and Keep; so an
acceptance made while it is off is refused too, an offer made while it was on among them, and custody hands nothing
(custody_candidate returns None). The routes that only end or undo one answer as they do while it is on: Withdraw,
Decline, Take back, Give back, and custody's End and Decline, so that what was begun while it was on always has a way
out; and the two reads answer with `off`, why, and point 13's text on nothing, since nothing can be accepted then
(_while_off). The round has no switch of its own: while the track is off it still ends what is due (an asked offer whose
attempt ended, an offer past its time) and carries out a way back, which brings a task home; but a hand-off asks for the
track again before its push and before the custodian's side takes it, and ends failed while it is off. A function
that takes `db` runs inside the caller's transaction and commits nothing itself.

What waits on a workspace runs outside any transaction, in a round of its own (drive): an offer whose hold has ended is
checked again, scanned and pushed by the owner's workspace, its packet built and kept, and the teammate told; a custody
hand-off whose hold has ended is checked again, compared with the repository's default branch, pushed, and taken by the
custodian's first receiving agent that may take it, at once, by standing consent, its task made and its first attempt
decided in the same transaction by the custodian's relay (tasks.relay_start, `chosen_by` "custody"),
after the receiving agent is mirrored into the custodian's workspace; a hand-off whose received task ended, or whose
owner's accounts have room again for a custody task, starts its way back; and a way back is carried out: the
teammate's live attempt stopped as Stop stops it, the last commit they pushed fetched and judged by the owner's
workspace, the owner's own checks run on it, and the results applied to the owner's task (tasks.take_return). A
hand-off whose push or fetch fails, a workspace that cannot start or does not answer among it, ends failed at once;
a way back waits on a workspace that does not answer, the app acting on no silence, the page saying so.
The round runs in a thread of this process, woken by the routes that change something, the holds' ends and the
scheduler's forget, and otherwise every DRIVE_EVERY seconds, one round at a time: a round waiting on a workspace call
delays the next. Each of its steps reads the store again before it acts, so a round that finds nothing to do changes
nothing."""

import hashlib
import hmac
import json
import re
import secrets
import sys
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import auth, config, passkeys, paths, store  # noqa: F401  (paths puts the open packages on sys.path)
from . import agents, holds, link, push, risk_texts, tasks, workspace_client, workspaces
from . import task_shapes as shapes
from .workspace_routes import workspace_user
from proofpane_core.canonical import canonicalize  # noqa: E402  (after paths, which puts it on sys.path)

router = APIRouter(prefix="/api/ade/teammate")

HOLD_OFFER = "task.offer"               # a pressed offer's hold, and a custody hand-off's
HOLD_CROSSING = "task.crossing"         # custody's crossing tick on a task (the relay's crossings take none)
HOLD_SETTINGS = {HOLD_OFFER: "OPEN_EDITION_ADE_TASK_OFFER_HOLD", HOLD_CROSSING: "OPEN_EDITION_ADE_TASK_CROSSING_HOLD"}
OFFER_KINDS = ("pressed", "custody")
# "draft": made, so its confirmation can name it (task-offer:<id>), never shown to the teammate; then held, offered...
OFFER_STATES = ("draft", "held", "offered", "accepted", "declined", "withdrawn", "expired", "failed", "returned")
# "asked" is written before the owner's confirmation names it (task-custody:<id>); the custodian sees it only once
# confirmed_at is set; "declined" (the custodian's) and "expired" (not answered in OFFER_DAYS after the confirmation)
# end a request as "ended" does while it is "asked", the custodian and the typed address cleared
CUSTODY_STATES = ("asked", "accepted", "suspended", "ended", "declined", "expired")
OFFER_DAYS = 7                          # an offer, or a confirmed custody request, not answered in this long expires
DRAFT_SECONDS = 300                     # a draft not confirmed in this long is deleted (a confirmation's own life)
CUSTODY_MARGIN = 1800                   # custody hands off only while the earliest reset is more than this away
CUSTODY_KEEP_DAYS = 90                  # an accepted custody's custodian is kept this long after it ends
OFF = "teammate"                        # the refusal's "off" member

# the purposes of this track's confirmations (passkeys.CHANGE): the track is on only where
# each is a change a confirmation is made for, since a confirmation made for anything could otherwise name a custodian,
# accept custody or confirm a task
PURPOSES = ("task-offer", "task-custody", "task-custody-accept", "task-custody-confirm", "task-custody-cross",
            "task-secret-lift")
OFFER_STANDING = ("draft", "held", "offered", "accepted")            # an offer not yet ended
CUSTODY_STANDING = ("asked", "accepted", "suspended")
# the packet an offer keeps: the owner's words and what the owner's workspace observed, never a
# word a model wrote; its digest, which the received task's continued_from names, under a tag of its own
PACKET_SCHEMA = "ade.handoff.v1"
HANDOFF_TAG = b"proofpane-handoff/1"
DRIVE_EVERY = 10                        # seconds between the rounds of drive, at most
PUSH_WAIT = 400                         # a scan and a push, up to the workspace's own time out for a push
FETCH_WAIT = 400                        # a fetch, a clone first when the workspace has none
CHECKS_WAIT = 3600                      # the owner's checks on a returned commit, each up to its own time out
FORGET_WAIT = 30                        # a worktree no task came of, removed (_forget_worktree)
TOLD_GRACE = 60                         # an offer's hold this old, never told (its teller never ran), told by the round
MADE_LISTED = 50                        # the owner's offers GET /offers gives, newest first
SUSPENDED_CAUSES = ("step_done", "no_step", "failed", "stopped", "window")   # tasks' own, a task's way back to them

# why the track is off, in the page's words and the routes' (enabled)
OFF_SETTING = "This deployment has turned offers and custody off."
OFF_PURPOSES = ("This deployment's confirmations are not each made for one named change, so offers and custody are "
                "off.")
OFF_LOCAL = ("Offers and custody need each person's workspace to be a machine of their own, as the fly provider gives "
             "it; this deployment runs every workspace on one machine, so they are off.")
OFF_OPERATOR = "This deployment does not name its operator, so offers and custody are off."
# refusals, and why an offer, a custody request or a hand-off ended (an offer's `why`, the journal's `why`, a task's
# reason): the app's own words, never an address, a user or a model's words
NO_ADDRESS = "This task's repository has no recorded address, so its work cannot be handed to a teammate."
RECEIVED = "A task received from a teammate can only go back to where it came from."
UNKNOWN = "An effect of this task is unknown: resolve it first."
NOT_WAITING = "Only a task that waits, or one working now, can be offered."
HANDING = "This task is being handed to a teammate, or is with one: withdraw the offer, or take it back, first."
OWN_ADDRESS = "That is your own address."
BAD_ADDRESS = "An address is someone's email address here."
CHANGED = "The task changed; look again."
DRAFT_GONE = "The draft has expired: make it again."
WITHDRAWN = "The owner withdrew the offer."
CANCELLED = "The owner cancelled the hand-off in its hold."
DECLINED = "The teammate declined the offer."
EXPIRED = f"Nobody accepted the offer within {OFFER_DAYS} days."
NOT_ASKED = "The attempt's end left the task holding something for the owner, so the offer did not begin."
NOT_ASKED_ENDED = "The task ended, so the offer did not begin."
NOT_BEGUN = "The attempt ended without the offer beginning: offer the task again."
NOT_AS_OFFERED = "The task was no longer as it was offered when the hold ended."
NO_START = "The workspace could not start, so the hand-off ended."
PUSH_FAILED = "The workspace could not push the task's work, so the hand-off ended."
TAKE_FAILED = "The custodian's side could not take the task, so the hand-off ended."
PUSH_HELD = ("The task's branch moved since its last checkpoint, or the workspace's watch held the push: look at the "
             "task, then hand it again.")
SILENT = "The workspace did not answer, so the hand-off ended."
SECRET = ("What the push would carry holds something shaped like a secret ({what}), so nothing was pushed. If it is "
          "one, take it out of every commit since the task began, since a later commit that deletes it still carries "
          "it; if it is not, lift it. Then hand the task again.")
# what the scan could not read, which holds the push and is never lifted (task_runs._scan_all's unread)
SCAN_UNREAD = "The push's scan could not read what it would carry ({what}), so nothing was pushed."
UNREAD_SHAPES = ("a change the scan could not read", "a change too large to scan", "a file the scan could not read")
UNREADABLE = ("The receiving workspace could not fetch the task's work: its GitHub sign-in may not read the "
              "repository.")
NOT_BROUGHT = "The receiving workspace did not bring the commit the task was handed at."
NO_ACCOUNT = ("That agent names no account, so it runs on whichever account your new sessions start on: choose its "
              "account first, one you confirmed as your own.")
LOADED_CHANGED = ("The task's work changes files a next run would load, which custody never takes by standing "
                  "consent: hand it by an offer, so your teammate can look.")
PACKET_TOO_BIG = (f"The hand-off's packet would pass {shapes.PACKET_MAX} bytes: shorten the task's goal, steps or "
                  "decisions, then hand it again.")
CUSTODY_ENDED_WHY = "Custody ended before the hand-off."
NO_RECEIVER = "No receiving agent of the custodian could take the task when the hand-off's hold ended."
CUSTODY_ENDED_BY = {"owner": "The owner ended custody.", "custodian": "The custodian ended custody."}
CUSTODY_DECLINED = "The teammate declined to be the custodian."
CUSTODY_EXPIRED = f"The teammate did not answer the custody request within {OFFER_DAYS} days."
CUSTODY_AGENT_CHANGED = "A receiving agent changed: the custodian confirms the agents again."
CROSSINGS_ENDED = ("A press ended this task's crossings, so custody keeps it in the group it is on, and its crossing "
                   "is not offered.")
RETURNED_REASON = "This task went back to the teammate who handed it to you."
WAITING = "The teammate's workspace does not answer: the task comes back once it does."
THEIR_SECRET = ("What the teammate's work would carry holds something shaped like a secret ({what}), so it was not "
                "pushed: what comes back is the work pushed before it, unless your teammate lifts it.")
THEIR_UNREAD = ("The scan of the teammate's push could not read what it would carry ({what}), so it was not pushed: "
                "what comes back is the work pushed before it.")
# a lift (lift_secret)
BAD_FINDINGS = "A lift names the findings it lifts by their ids."
NOT_HELD = "This task is not held for those findings now: look again."
LIFTED_BACK = "What held this task's hand-off is lifted: hand it again."
FINDINGS_MOST = 20                      # findings a hold keeps, and a lift names, at most (task_runs.FINDINGS_MAX)
LIFTS_PASSED = 1000                     # a person's lifts for an address passed with a push (task_runs.LIFTED_MAX)
FINDING_KINDS = ("line", "name", "commit")
COLUMN_MOST = 1 << 30                   # a finding's column, its line or its place, at most
_FINDING = re.compile(r"[0-9a-f]{32}")
_SHAPES = frozenset(name for name, _ in link.SECRET_SHAPES)
WAITING_OWN = "The workspace could not take the returned work yet: it is tried again."
CUSTODIAN_ELSEWHERE = ("The custodian keeps their work outside a workspace this deployment runs for them, so their "
                       "workspace did not start and the hand-off ended.")
NOT_READ = ("The files this offer changes that a next run would load are read once your workspace runs: open the "
            "offer again in a moment.")
SHOWN_OTHERS = "The files this offer changes that a next run would load are not those shown: look at it again."
STARTING = "Your workspace is starting: a moment, then try again."
KEPT = "The custodian keeps this task until it ends."
# the why of custody's own return in its task.returned entry ("a return (custody)"), which its
# way back reads, to pass tasks.take_return the way "return": the owner's relay then goes on as Between steps allows
CUSTODY_RETURN = "One of your own accounts has room again, so custody brought the task back."
_ADDRESS = re.compile(r"[^@\s\x00-\x1f\x7f]{1,64}@[^@\s\x00-\x1f\x7f]{1,255}")

_round_lock = threading.Lock()          # one round at a time in this process
_driver_lock = threading.Lock()
_wake = threading.Event()
_driver = []                            # the round's thread, once started
_seen = {}                              # (offer id, commit): the loaded files the teammate's workspace read for it
_shown = {}                             # (offer id, commit): the loaded files GET /offers gave the teammate


# ---- small helpers -------------------------------------------------------------------------------------------------

def _now(now):
    return int(now if now is not None else time.time())


def _loads(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _id(value, what):
    """An offer's, a custody row's or a task's id from a path: 32 hex, else 404, as an id that is no one's."""
    if not isinstance(value, str) or not shapes.TASK_ID.fullmatch(value):
        raise agents.AgentError(404, f"No such {what}.")
    return value


def _address(value):
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value.strip()):
        raise agents.AgentError(400, BAD_ADDRESS)
    return value.strip()


def _email(db, user_id):
    row = db.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None
    return row["email"] if row is not None else None


def _matched(db, address):
    """The user whose email `address` is, matched once, when the request is made, or None."""
    row = db.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE LIMIT 1", (address,)).fetchone()
    return row["id"] if row is not None else None


def _branch(task_id):
    """The branch a task's work is pushed to: the owner's task's id names it, on both sides."""
    return f"pp/task-{task_id}"


def _tell(user_id, make):
    """A content-free notice to the account's devices, after the change's transaction has committed (push.notice):
    `make` makes it (push.words, its kind named where it is called), and nothing of it ever fails the change."""
    try:
        push.notice(user_id, make())
    except Exception as e:  # noqa: BLE001  the change is made; telling of it never fails it
        print(f"task_offers: a notice could not be told: {type(e).__name__}", file=sys.stderr)


def _after(calls):
    for call in calls:
        try:
            call()
        except Exception as e:  # noqa: BLE001  what is left for after a commit never undoes it
            print(f"task_offers: what was left for after a change failed: {type(e).__name__}", file=sys.stderr)


# ---- whether the track is on ---------------------------------------------------------------------------------------

def enabled(user) -> Optional[str]:
    """None when offers and custody are offered to `user`, else why not, in the page's words: the deployment's setting
    off, a confirmation not made for one named change (passkeys.CHANGE without this track's purposes), the local
    provider (each user's workspace is not their own), or OPEN_EDITION_OPERATOR unset (risk_texts.operator() None: "This
    deployment does not name its operator, so offers and custody are off."). Every
    route of this module that starts or advances a hand-off refuses while it says why, an acceptance among them, so that
    no notice naming no holder is shown; those that only end or undo one answer, and the two reads say why."""
    if not tasks.on("teammate"):
        return OFF_SETTING
    if not all(passkeys.CHANGE.fullmatch(f"{purpose}:{'0' * 32}" if purpose != "task-secret-lift" else
                                         lift_subject("0" * 32, [])) for purpose in PURPOSES):
        return OFF_PURPOSES
    if workspaces.provider_name() != "fly":
        return OFF_LOCAL
    if risk_texts.operator() is None:
        return OFF_OPERATOR
    return None


def _on(user):
    """The refusal of a route that starts or advances a hand-off, while the track is off."""
    why = enabled(user)
    if why is not None:
        raise agents.AgentError(409, why, off=OFF)


def _while_off(out, off, key):
    """A read's answer (GET /offers, GET /custody) while the track is off, `off` being why: as while it is on, so that
    the page can show what ends or undoes a hand-off that stands, with `off` and with point 13's text (`terms`) on none
    of `out[key]`, since nothing can be accepted then. Unchanged while it is on."""
    if off is None:
        return out
    return dict(out, **{key: [dict(view, terms=None) for view in out[key]]}, off=off)


# ---- reading tasks, agents and accounts ----------------------------------------------------------------------------

def _task(db, user_id, task_id):
    task = tasks.get(db, user_id, task_id)
    if task is None or task.get("deleted_at"):
        raise agents.AgentError(404, "No such task.")
    return task


def _agent(db, user_id, agent_id):
    return db.execute("SELECT * FROM ade_agents WHERE id = ? AND user_id = ?", (agent_id, user_id)).fetchone()


def _open_task(db, user_id, agent_id):
    """Whether the agent has a task that has not ended: a receiving agent takes a task only when it has none."""
    return db.execute("SELECT 1 FROM ade_agent_tasks WHERE user_id = ? AND agent_id = ? AND deleted_at IS NULL AND "
                      "state NOT IN ('done', 'stopped') LIMIT 1", (user_id, agent_id)).fetchone() is not None


def _account(db, user_id, account):
    """An account of the user's workspace as they confirmed it their own: its group,
    the group's kind, its plan, its organisation and orgId; None when it is not one they confirmed, or is in no
    group."""
    row = db.execute("SELECT * FROM ade_account_owners WHERE user_id = ? AND account = ? AND state = 'confirmed'",
                     (user_id, account)).fetchone() if account else None
    if row is None or not row["grp"]:
        return None
    confirmed = _loads(row["confirmed"], {})
    return {"account": account, "grp": row["grp"], "grp_kind": row["grp_kind"],
            "org_kind": tasks.read_kind(db, user_id, row["grp"], row["grp_kind"]), "plan": confirmed.get("plan"),
            "organization": confirmed.get("organization"), "org_id": row["org_id"]}


def _group_kind(db, user_id, grp):
    """The kind of one of the user's groups (personal, team, enterprise), from the accounts they confirmed; None when
    none of theirs is in it. An organisation they hold seats of both kinds of reads as an Enterprise one, as
    tasks._kind_of_group reads it."""
    if grp == shapes.PERSONAL:
        return shapes.PERSONAL
    row = db.execute("SELECT grp_kind FROM ade_account_owners WHERE user_id = ? AND grp = ? AND state = 'confirmed' "
                     "ORDER BY grp_kind = 'enterprise' DESC, grp_kind LIMIT 1", (user_id, grp)).fetchone()
    return row["grp_kind"] if row is not None else None


def _on_group(db, user_id, task):
    """The group the task is on now: that of the account its backend names, whatever that account's state (an account
    whose identity changed since its confirmation keeps the group it was confirmed in, as tasks' own reading does); its
    home group for a key, or for an account in no group."""
    backend = task.get("backend") or {}
    row = db.execute("SELECT grp FROM ade_account_owners WHERE user_id = ? AND account = ?",
                     (user_id, backend.get("id"))).fetchone() if backend.get("kind") == "account" else None
    return row["grp"] if row is not None and row["grp"] else task["home_grp"]


def _agent_digest(row):
    """What custody's acceptance confirmed of a receiving agent: its brief, allow rules, MCP servers and account,
    and the unattended mode, model and effort a custody task's attempt runs with on it (an attempt the
    owner of the task did not choose runs in its unattended mode, which may be bypassPermissions), so that any change to
    them suspends custody until the custodian confirms it again."""
    settings = _loads(row["chat_settings"], {})
    return hashlib.sha256(canonicalize({"brief": row["brief"], "allow": _loads(row["allow"], []),
                                        "mcp": _loads(row["mcp"], {}), "account": row["account"],
                                        "unattended_mode": row["unattended_mode"], "model": settings.get("model"),
                                        "effort": settings.get("effort")})).hexdigest()


def _confirmed_for_custody(db, user_id, task):
    """Whether the task is confirmed for custody as it stands: the digest its confirmation kept is that
    of its whole instructions, checks, external commands, protected paths and its agent's brief's version now, as
    tasks.custody_digest (which tasks.py's "Answer, and confirm for custody" keeps too) makes it, so that a change to
    any of them, the brief's among them, leaves it unconfirmed."""
    return bool(task.get("custody_digest")) and task["custody_digest"] == tasks.custody_digest(db, user_id, task["id"])


def _snapshot(user_id, account):
    """The teammate in the owner's account record: their user id, and the plan of the account their
    agent took the task on with its organisation only for a Team or Enterprise seat; never their email."""
    personal = account["plan"] in shapes.PERSONAL_PLANS
    return {"teammate": user_id, "plan": account["plan"],
            "organization": None if personal else account["organization"],
            "org_id": None if personal else account["org_id"]}


def _opens_at(entry):
    """When an account of a relay set opens again, as its refused windows give it: the latest of their resets, or None
    where it has no refused window or one whose reset is not known."""
    resets = [w.get("resets_at") for w in entry.get("windows", []) if w.get("status") == "rejected"]
    if not resets or any(not isinstance(t, int) for t in resets):
        return None
    return max(resets)


def _members(db, owner_id, task, now):
    """The accounts of the task's relay set, among tasks.relay_set's entries, which name every account the owner
    reported: those in the task's relay groups (tasks.relay_groups) that the owner confirmed, an account whose
    identity changed since among them. An account of another group, or one never confirmed, is outside the set by
    design and is not read (read as written, any one of them would keep every custody hand-off from
    happening); nor is a seat of one of those groups that the relay leaves out as a move onto or off an Enterprise
    organisation's seat (tasks.enterprise_move), whose room nothing will use."""
    groups = tasks.relay_groups(db, task)[0]
    states = {r["id"]: r["state"] for r in db.execute("SELECT id, state FROM ade_account_owners WHERE user_id = ?",
                                                     (owner_id,)).fetchall()}
    return [dict(e, state=states[e.get("owner_id")]) for e in tasks.relay_set(db, owner_id, task["id"], now)
            if states.get(e.get("owner_id")) in ("confirmed", "changed")
            and any(g == e.get("grp") or (g == "org:*" and e.get("org_kind") == "team") for g in groups)
            and not tasks.enterprise_move(db, task, e.get("grp"), e.get("org_kind"))]


def _all_refused(entries, now):
    """Whether every account of a task's relay set is out on a refused window whose reset is known and still ahead
    (read over the relay set): an account out for any other reason (taken out, held back by its
    reserve, its identity changed since its confirmation, whatever its windows say), or with room, does not count, so
    neither the sign-in alone nor a program that damages accounts' folders can force a hand-off; and if so, the
    earliest time one of them opens again."""
    opens = [_opens_at(e) if e.get("state") == "confirmed" else None for e in entries]
    if not opens or any(t is None or t <= now for t in opens):
        return False, None
    return True, min(opens)


def _fresh_room(entry, now):
    """Whether an account has room on fresh evidence, as its windows say: none of them refuses
    it now, and an allowed window was seen within task_shapes.FRESH_SECONDS, or a refused window's reset has passed. An
    allowed window older than that is no evidence of room now."""
    windows = entry.get("windows") or []
    if any(w.get("status") == "rejected" and (w.get("resets_at") is None or w["resets_at"] > now) for w in windows):
        return False
    return any((w.get("status") in ("allowed", "allowed_warning") and (w.get("at") or 0) >= now - shapes.FRESH_SECONDS)
               or (w.get("status") == "rejected" and w["resets_at"] <= now) for w in windows)


def _has_room(db, owner_id, task_id, now):
    """Whether one of the owner's accounts in the task's relay set, eligible, has room on fresh evidence."""
    task = tasks.get(db, owner_id, task_id)
    return task is not None and any(e.get("eligible") and _fresh_room(e, now)
                                    for e in _members(db, owner_id, task, now))


# ---- what the page sees --------------------------------------------------------------------------------------------

def _hold_ref(db, hold_id):
    """A hold as the page lists it with Cancel (OfferHold), while it has neither ended nor been cancelled."""
    hold = holds.get(db, hold_id) if hold_id else None
    if hold is None or hold["done_at"] is not None or hold["cancelled_at"] is not None:
        return None
    return {"id": hold["id"], "kind": hold["kind"], "takes_effect_at": hold["takes_effect_at"]}


def _terms(kind, owner):
    """Risk point 13's text as the routes return it: risk_texts.text(13),
    worded for an offer or for custody (each keeps the parts of no case, its own, and point 1's clauses, case
    "other_group"; an offer also its adopted check's), "{owner}" filled with the owner as the view names them, the
    operator and the record's keeping as risk_texts filled them; its digest the whole text's, naming its words'
    version."""
    text = risk_texts.text(13)
    if text is None:
        return None
    keep = {None, "offer", "adopted_check", "other_group"} if kind == "pressed" else {None, "custody", "other_group"}
    named = owner or "your teammate"
    return dict(text, design=[dict(p, text=p["text"].replace("{owner}", named)) for p in text["design"]
                              if p["case"] in keep],
                quotations=[q for q in text["quotations"] if q["case"] in keep])


def _packet_view(row):
    """What the teammate sees of the packet kept with an offer they received (the packet as the owner's words and
    information): the owner's decisions,
    protected paths and external commands, and, as information, the steps done and the outward effects, each member
    held to its shape. Read from the packet, which the offer's digest covers, not from the task; an acceptance makes
    the received task from the owner's task itself, refused where that task is no longer offering at the offer's fence
    (accept). None where no packet is kept."""
    packet = _loads(row["packet"], {})
    if not packet:
        return None
    instructions = packet.get("instructions") if isinstance(packet.get("instructions"), dict) else {}
    information = packet.get("information") if isinstance(packet.get("information"), dict) else {}

    def listed(value):
        return value if isinstance(value, list) else []
    return {"decisions": [{"n": d["n"], "text": d["text"]} for d in listed(instructions.get("decisions"))
                          if isinstance(d, dict) and type(d.get("n")) is int and isinstance(d.get("text"), str)],
            "protected": [p for p in listed(instructions.get("protected")) if isinstance(p, str)],
            "external": [x for x in listed(instructions.get("external")) if isinstance(x, str)],
            "done_steps": [n for n in listed(information.get("done_steps")) if type(n) is int],
            "outward_effects": [{k: e[k] if isinstance(e.get(k), str) else None
                                 for k in ("tool", "kind", "target", "state")}
                                for e in listed(information.get("outward_effects")) if isinstance(e, dict)]}


# the teammate's task's state as the owner sees it while it is with them, in three words (its step; running, waiting
# or done): waiting to start, for a
# reset, between steps or for the teammate alike
THEIR_STATE = {"running": "running", "ready": "waiting", "suspended": "waiting", "needs_owner": "waiting",
               "done": "done"}


def _their_state(db, row):
    """The word of THEIR_STATE for the teammate's task of an offer accepted, and its step while it is not done; (None,
    None) for an offer not accepted, and where their task is stopped, deleted or gone, whose way back is on its way.
    Nothing else of their task: never its reason, its cause or its runs' words."""
    if row["state"] != "accepted" or not row["their_task"] or not row["teammate_user"]:
        return None, None
    theirs = tasks.get(db, row["teammate_user"], row["their_task"])        # None once deleted
    word = THEIR_STATE.get(theirs["state"]) if theirs is not None else None
    if word is None:
        return None, None
    return word, (theirs["step"] if word != "done" else None)


def _offer_view(db, row, received=False, other_group=()):
    """OfferView (taskOffersApi.ts): the offer, its task's title, goal and steps with each owner's check word for word;
    for one received, point 13's text, the files a next run would load that it changes as the teammate's own
    workspace read them, their agents whose account is in another group than the task's, and the packet's decisions,
    protected paths, external commands and information (_packet_view); and for the owner's own, the teammate's task's
    state in three words and its step while it is with them (_their_state)."""
    task = tasks.get(db, row["user_id"], row["task_id"]) or {}
    steps = tasks.view(db, row["user_id"], row["task_id"])["steps"] if task else []
    loaded = _seen.get((row["id"], row["commit"])) if received else None
    owner = _email(db, row["user_id"])
    why = NOT_READ if received and loaded is None else row["why"]
    their_state, their_step = (None, None) if received else _their_state(db, row)
    return {"id": row["id"], "kind": row["kind"], "state": row["state"], "task_id": row["task_id"],
            "title": task.get("title", ""), "goal": task.get("goal", ""),
            "steps": [{"n": s["n"], "objective": s["objective"], "check": s["check"]} for s in steps],
            "from_email": owner, "to_email": row["address"], "repo_slug": task.get("repo_slug"),
            "branch": _branch(row["task_id"]), "commit": row["commit"], "packet_digest": row["packet_digest"],
            "loaded_changes": list(loaded or []), "hold": _hold_ref(db, row["hold_id"]), "why": why,
            "created_at": row["created_at"], "expires_at": row["expires_at"] or row["created_at"] + DRAFT_SECONDS,
            "terms": _terms(row["kind"], owner) if received else None, "other_group_agents": list(other_group),
            "packet": _packet_view(row) if received else None, "their_state": their_state, "their_step": their_step}


def _custody_view(db, row, as_custodian=False):
    """CustodyView (taskOffersApi.ts): the request or the custody, its receiving agents in order with their groups,
    and, as the custodian sees a request, point 13's text worded for custody."""
    named = []
    for entry in _loads(row["agents"], []):
        agent = _agent(db, row["custodian_user"], entry.get("agent_id")) if row["custodian_user"] else None
        named.append({"agent_id": entry.get("agent_id"), "name": agent["name"] if agent is not None else "",
                      "grp": entry.get("grp")})
    owner = _email(db, row["owner_user"])
    return {"id": row["id"], "state": row["state"], "owner_email": owner, "custodian_email": row["address"],
            "confirmed_at": row["confirmed_at"], "agents": named, "why": row["why"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "terms": _terms("custody", owner) if as_custodian else None}


def _current_offer(db, task_id):
    """The task's newest offer that is not a draft, or None."""
    return db.execute("SELECT * FROM ade_agent_task_offers WHERE task_id = ? AND state != 'draft' ORDER BY created_at "
                      "DESC, rowid DESC LIMIT 1", (task_id,)).fetchone()


def _handoff_task(db, user_id, task_id):
    """HandOffTask (taskOffersApi.ts): the few fields of a task the teammate track's page parts read. custody_confirmed:
    confirmed for custody as the task stands now."""
    task = tasks.get(db, user_id, task_id)
    offer = _current_offer(db, task_id)
    came = task.get("continued_from")
    return {"id": task["id"], "agent_id": task["agent_id"], "state": task["state"],
            "state_version": task["state_version"], "title": task["title"], "repo_address": task["repo_address"],
            "continued_from": {"task": came["task"], "offer": came["offer"]} if came else None,
            "custody_confirmed": _confirmed_for_custody(db, user_id, task),
            "custody_cross": bool(task["custody_cross"]), "crossings_ended": bool(task["crossings_ended"]),
            "offer": {"id": offer["id"], "state": offer["state"]} if offer is not None else None}


# ---- an offer's end with no acceptance -----------------------------------------------------------------------------

_END_KINDS = {"withdrawn": "task.offer_cancelled", "declined": "task.offer_declined", "expired": "task.offer_expired",
              "failed": "task.offer_failed"}


def _offer_row(db, offer_id, **where):
    sql = "SELECT * FROM ade_agent_task_offers WHERE id = ?" + "".join(f" AND {k} = ?" for k in where)
    return db.execute(sql, (offer_id, *where.values())).fetchone()


def _suspended_as(db, user_id, task_id):
    """How the task waited before it was offered: the cause and the reset of its newest task.suspended entry (tasks.py's
    journal), or ("stopped", None) where there is none."""
    row = db.execute("SELECT payload FROM ade_agent_task_events WHERE user_id = ? AND task_id = ? AND kind = "
                     "'task.suspended' ORDER BY seq DESC LIMIT 1", (user_id, task_id)).fetchone()
    payload = _loads(row["payload"], {}) if row is not None else {}
    cause = payload.get("cause") if payload.get("cause") in SUSPENDED_CAUSES else "stopped"
    until = payload.get("until") if isinstance(payload.get("until"), int) else None
    return cause, until


def _drop_marked_press(db, row, now):
    """The owner's task's press dropped as its offer ends, accepted or not, where a confirmation moved the account it
    names into another group, or onto another kind of seat, or changed how that account's organisation is read
    (tasks._organisation_reread), while the offer was out: tasks._presses_dropped marks the offer then
    (press_dropped_at) rather than change the task, whose state_version is the offer's fence. The offer is found by its
    hold, which every offer that made its task `offering` has and _back reads first, as the hold's own ends find it."""
    mark = db.execute("SELECT press_dropped_at FROM ade_agent_task_offers WHERE hold_id = ?",
                      (row["hold_id"],)).fetchone()
    if mark is not None and mark["press_dropped_at"] is not None:
        tasks.drop_press(db, row["task_id"], now)


def _back(db, row, why, now, needs_owner=False):
    """The owner's task, offering, back as it was: for a pressed offer, waiting as it waited before it was
    offered, a window's reset among it, so the task resumes at that reset as it would have; for a custody hand-off, the
    relay deciding again from its first step, since an account may have room by then: waiting for nothing where an
    account of its relay set is open now, else for the earliest reset of its accounts; or waiting for the owner, cause
    handoff, where what ended the offer holds it for them (a moved branch, a secret shape). An offer that never began
    (asked for on a working task, with no hold yet) never made the task `offering`, so it gives nothing back: the
    task may be `offering` for another hand-off, a custody one the relay began at that attempt's end."""
    if row["hold_id"] is None:
        return
    task = tasks.get(db, row["user_id"], row["task_id"])
    if task is None or task["state"] != "offering":
        return
    _drop_marked_press(db, row, now)
    # the binding an organisation read otherwise while the offer was out held back, applied as the offer ends untaken,
    # before the task waits again, so that its relay decides on it: the task read again where it changed it
    if tasks.apply_held_reread(db, row, now):
        task = tasks.get(db, row["user_id"], row["task_id"])
    if needs_owner:
        tasks.set_state(db, row["user_id"], row["task_id"], "needs_owner", cause="handoff", reason=why, now=now)
    else:
        _wait_again(db, row, task, why, now)


def _wait_again(db, row, task, why, now):
    """The owner's task waits again after a hand-off of it that ended (_back): for a pressed offer, as it waited before
    it was offered, a window's reset among it; for a custody hand-off, for nothing where an account of its relay set is
    open now, else for the earliest reset of its accounts."""
    if row["kind"] == "custody":
        opens = [_opens_at(e) if e.get("state") == "confirmed" else None for e in _members(db, row["user_id"], task,
                                                                                          now)]
        later = [t for t in opens if t is not None and t > now]
        open_now = any(t is None or t <= now for t in opens)
        until = now if open_now or not later else min(later)
        tasks.set_state(db, row["user_id"], row["task_id"], "suspended", cause="window", reason=why,
                        waits_until=until, now=now)
    else:
        cause, until = _suspended_as(db, row["user_id"], row["task_id"])
        tasks.set_state(db, row["user_id"], row["task_id"], "suspended", cause=cause, reason=why,
                        waits_until=until if cause == "window" else None, now=now)


def _end_offer(db, row, state, why, now, needs_owner=False, applying=False):
    """End an offer that was never accepted, as `state` (declined, withdrawn, expired or failed): its teammate and the
    typed address set to NULL with its state and why, so nothing of a teammate who declines is kept in the owner's
    store, as risk point 13 tells them; a hold still running ended with it (not the one being applied, `applying`);
    the owner's task back; and the journal told by the offer's id alone. A draft is deleted, since nothing knew of
    it."""
    if row["state"] == "draft":
        db.execute("DELETE FROM ade_agent_task_offers WHERE id = ?", (row["id"],))
        return
    db.execute("UPDATE ade_agent_task_offers SET state = ?, why = ?, teammate_user = NULL, address = NULL, "
               "updated_at = ? WHERE id = ?", (state, why, now, row["id"]))
    if row["hold_id"] and not applying:
        holds.end(db, [row["hold_id"]], why, now)
    _back(db, row, why, now, needs_owner)
    tasks.journal(db, row["user_id"], row["task_id"], _END_KINDS[state],
                  {"offer": row["id"], "fence": row["fence"], "why": why}, now=now)


def _fail(offer_id, why, now, needs_owner=False, findings=None):
    """In a transaction of its own, an offer that stands and has not been accepted ends failed, with why, and with the
    findings its push's scan was held for, if any (_findings), which the owner may lift (lift_secret)."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _offer_row(db, offer_id)
        if row is not None and row["state"] in ("held", "offered"):
            if findings:
                db.execute("UPDATE ade_agent_task_offers SET secret_findings = ? WHERE id = ?",
                           (json.dumps(findings, separators=(",", ":")), offer_id))
            _end_offer(db, row, "failed", why, now, needs_owner)


# ---- a pressed offer ------------------------------------------------------------------------------------------------

def _offerable(db, user_id, task, running_too=True, besides=""):
    """(status, why) the task cannot be offered now, or None: a task that waits, or a working one (the
    offer then begins at its attempt's end), with no unknown effect and a recorded address, received from no one, and
    with no offer standing but `besides`, the one being begun."""
    if task.get("continued_from"):
        return 412, RECEIVED
    if task["state"] in ("offering", "handed_over", "returning"):
        return 412, HANDING
    if task["state"] != "suspended" and not (running_too and task["state"] == "running"):
        return 412, NOT_WAITING
    if not task.get("repo_address"):
        return 412, NO_ADDRESS
    if tasks.unknown_effects(db, user_id, task["id"]):
        return 412, UNKNOWN
    if db.execute("SELECT 1 FROM ade_agent_task_offers WHERE task_id = ? AND id != ? AND state IN ('held', 'offered', "
                  "'accepted') LIMIT 1", (task["id"], besides)).fetchone():
        return 412, HANDING
    return None


def make_offer(user, task_id, state_version, address, now=None):
    """POST /offers: a draft of an offer of the user's task to the teammate at `address`, the user matched to it once,
    now. The answer is the same whether or not the address is someone's: the draft
    names the address as typed, never whom it matched. Its fence is the state_version the page read, which the
    confirmation is checked against."""
    _on(user)
    now = _now(now)
    task_id = _id(task_id, "task")
    address = _address(address)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task = _task(db, user["id"], task_id)
        why = _offerable(db, user["id"], task)
        if why is not None:
            raise agents.AgentError(*why)
        if task["state_version"] != state_version:
            raise agents.AgentError(409, CHANGED)
        if address.lower() == (_email(db, user["id"]) or "").lower():
            raise agents.AgentError(400, OWN_ADDRESS)
        db.execute("DELETE FROM ade_agent_task_offers WHERE task_id = ? AND state = 'draft'", (task_id,))
        offer_id = secrets.token_hex(16)
        db.execute("INSERT INTO ade_agent_task_offers (id, user_id, task_id, teammate_user, address, kind, state, "
                   "fence, created_at, updated_at, expires_at) VALUES (?,?,?,?,?,'pressed','draft',?,?,?,?)",
                   (offer_id, user["id"], task_id, _matched(db, address), address, state_version, now, now,
                    now + DRAFT_SECONDS))
        return {"offer": _offer_view(db, _offer_row(db, offer_id))}


def _begin(db, owner_id, task_id, offer_id, version, now):
    """The offer begins: against the task's state_version, the task becomes offering, in
    which nothing is queued for it, and the hand-off's fence is the version it became offering at; a task.offer hold is
    placed, its summary ids alone; the journal is told by the offer's id. Returns the hold."""
    row = tasks.set_state(db, owner_id, task_id, "offering", expect_version=version, now=now)
    offer = _offer_row(db, offer_id)
    what = "A custody hand-off" if offer["kind"] == "custody" else "An offer"
    hold = holds.place(db, owner_id, HOLD_OFFER, offer_id, f"{what} ({offer_id}) of task {task_id}", now)
    db.execute("UPDATE ade_agent_task_offers SET state = 'held', hold_id = ?, fence = ?, updated_at = ? WHERE id = ?",
               (hold["id"], row["state_version"], now, offer_id))
    tasks.journal(db, owner_id, task_id, "task.offered",
                  {"offer": offer_id, "fence": row["state_version"], "why": None}, now=now)
    return hold


def confirm_offer(user, offer_id, confirmation, now=None):
    """POST /offers/{id}/confirm: the owner's confirmation, made for task-offer:<id>, begins the offer: a task that
    waits becomes offering under its hold; on a working one the offer is asked for, its attempt stopped as Stop stops
    it, and it begins only in the transaction that copies that attempt's end (after_attempt_end). Refused where the
    draft expired, the task changed since the draft, or it can no longer be offered."""
    _on(user)
    now = _now(now)
    offer_id = _id(offer_id, "offer")
    after = []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _offer_row(db, offer_id, user_id=user["id"])
        if row is None:
            raise agents.AgentError(404, "No such offer.")
        if row["state"] != "draft":
            raise agents.AgentError(409, "This offer was confirmed already.")
        if now - row["created_at"] > DRAFT_SECONDS:
            raise agents.AgentError(409, DRAFT_GONE)
        passkeys._use_confirmation(db, user["id"], confirmation, now, subject=f"task-offer:{offer_id}")
        task = _task(db, user["id"], row["task_id"])
        why = _offerable(db, user["id"], task)
        if why is not None:
            raise agents.AgentError(*why)
        if task["state_version"] != row["fence"]:
            raise agents.AgentError(409, CHANGED)
        hold = None
        if task["state"] == "running":
            # asked for: held with no hold yet, until the attempt's end is copied
            db.execute("UPDATE ade_agent_task_offers SET state = 'held', updated_at = ? WHERE id = ?", (now, offer_id))
            if task.get("lease_run"):
                after.append(lambda run=task["lease_run"]: _stop(user, run))
        else:
            hold = _begin(db, user["id"], row["task_id"], offer_id, row["fence"], now)
            after.append(lambda: holds.tell(hold))
        out = {"offer": _offer_view(db, _offer_row(db, offer_id)),
               "hold": _hold_ref(db, hold["id"]) if hold is not None else None}
    _after(after)
    _kick()
    return out


def _stop(user, run_id):
    """A live attempt stopped as Stop stops it, its change committed (the workspace's own stop): whether it is stopped,
    or was not running; False when its workspace does not answer, which leaves it for later."""
    try:
        agents.stop_run(user, run_id)
        return True
    except agents.AgentError:
        return True                     # it has ended already, or its workspace is stopped: nothing runs
    except workspace_client.WorkspaceCallError:
        return False


def after_attempt_end(db, user_id, task_id, run, now, tells=None) -> bool:
    """Called by tasks.py in the transaction that copies an attempt's end, when an offer was asked for while it ran:
    True when the offer began (the end left the task suspended and holding nothing first), False when it was refused,
    with why, and the request is gone. tasks.py may call it at every end of a task's attempt: where no offer was asked
    for, it is False and changes nothing. The hold it places is put in `tells`, as ("hold", hold), which tasks.py tells
    with tasks.deliver once this transaction has committed, as it tells a custody hand-off's hold; one never told (no
    `tells` given, or its telling never ran) is told by the round TOLD_GRACE seconds after it was placed
    (_tell_untold)."""
    row = db.execute("SELECT * FROM ade_agent_task_offers WHERE task_id = ? AND user_id = ? AND kind = 'pressed' AND "
                     "state = 'held' AND hold_id IS NULL ORDER BY created_at DESC LIMIT 1",
                     (task_id, user_id)).fetchone()
    if row is None:
        return False
    task = tasks.get(db, user_id, task_id)
    why = None if task is not None and task["state"] == "suspended" else _not_begun(task)
    if why is None:
        why = enabled({"id": user_id})
    if why is None:
        refused = _offerable(db, user_id, task, running_too=False, besides=row["id"])
        why = refused[1] if refused is not None else None
    if why is not None:
        _end_offer(db, row, "failed", why, now)
        return False
    hold = _begin(db, user_id, task_id, row["id"], task["state_version"], now)
    if tells is not None:
        tells.append(("hold", hold))
    _kick()
    return True


def _not_begun(task):
    """Why an offer asked for on a working task did not begin, in words true of how the attempt's end left the task:
    holding it for its owner, ended (done or stopped), or otherwise."""
    state = (task or {}).get("state")
    return NOT_ASKED if state == "needs_owner" else NOT_ASKED_ENDED if state in ("done", "stopped") else NOT_BEGUN


def withdraw(user, offer_id, now=None):
    """POST /offers/{id}/withdraw: the owner's, until the offer is accepted: a draft, one asked for on a working task,
    one in its hold (the hold cancelled), or one offered. The task is back, suspended; a pushed branch stays on the
    remote. It only ends a hand-off, so it answers while the track is off too."""
    now = _now(now)
    offer_id = _id(offer_id, "offer")
    with store.connect() as db:
        row = _offer_row(db, offer_id, user_id=user["id"])
    if row is None:
        raise agents.AgentError(404, "No such offer.")
    if row["state"] == "held" and row["hold_id"]:
        try:
            holds.cancel(user["id"], row["hold_id"], now)   # its kind's cancelled() ends the offer
        except holds.HoldError:
            pass                                            # its time came: it is ended below, while it stands
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _offer_row(db, offer_id, user_id=user["id"])
        if row is not None and row["state"] == "accepted":
            raise agents.AgentError(409, "This offer was accepted: take the task back instead.")
        draft = _offer_view(db, row) if row is not None and row["state"] == "draft" else None
        if row is not None and row["state"] in ("draft", "held", "offered"):
            _end_offer(db, row, "withdrawn", WITHDRAWN, now)
        row = _offer_row(db, offer_id)
        if row is None:                                     # a draft, deleted: nothing knew of it
            return {"offer": dict(draft, state="withdrawn", to_email=None) if draft is not None else None}
        return {"offer": _offer_view(db, row)}


def _received_row(db, user, offer_id, now):
    row = _offer_row(db, offer_id, teammate_user=user["id"], kind="pressed")
    if row is None:
        raise agents.AgentError(404, "No such offer.")
    if row["state"] != "offered" or (row["expires_at"] or 0) <= now:
        raise agents.AgentError(409, "This offer no longer waits for you.")
    return row


def accept(user, offer_id, agent_id, adopt_checks, now=None):
    """POST /offers/{id}/accept: the teammate accepts an offer into one of their agents whose repository is the task's
    and which has no open task, its account one they confirmed as their own, adopting the owner's checks of the steps
    they name, each shown to them word for word. Their workspace fetches the offered commit
    by its hash with their own sign-in; one that cannot read the repository, or does not bring the commit, refuses the
    acceptance and the owner's task comes back. The files a next run would load that the commit changes must be those
    the teammate's GET /offers gave them (what they accepted), else the acceptance is refused until
    they look again. Then, in one transaction, their task is made with its continued_from, the owner's task is handed
    over, the owner's account record gains the hand-off with the teammate's snapshot, and the owner's task is bound as
    their task is bound, as custody's hand-off binds it (_bind_as_custody)."""
    _on(user)
    now = _now(now)
    offer_id = _id(offer_id, "offer")
    with store.connect() as db:
        row = _received_row(db, user, offer_id, now)
        task = tasks.get(db, row["user_id"], row["task_id"])
        if task is None or task["state"] != "offering":
            raise agents.AgentError(409, "This offer no longer waits for you.")
        steps = tasks.view(db, row["user_id"], row["task_id"])["steps"]
        adopted = sorted(set(adopt_checks))
        if any(not any(s["n"] == n and s["check"] for s in steps) for n in adopted):
            raise agents.AgentError(400, "Only a step with the owner's check has a check to adopt.")
        _receiving(db, user, agent_id, task)
    endpoint = agents._endpoint(user)
    if endpoint is None:
        agents._deliver_soon(user, start=True)
        raise agents.AgentError(409, STARTING)
    # the receiving agent in the teammate's workspace before its fetch, as a sync puts it there: one made while that
    # workspace did not run was never mirrored, and the fetch would find no such agent
    agents._mirror(user, endpoint)
    # the received task's own id, chosen now: the fetch makes the task's worktree under it, at the offered commit, and
    # tasks.create gives the task the same id, so that its attempts find that worktree
    theirs = secrets.token_hex(16)
    try:
        got = workspace_client.call(endpoint, "POST", f"/agents/tasks/{theirs}/fetch",
                                    {"address": row["repo_address"], "commit": row["commit"], "purpose": "accept",
                                     "agent": agent_id, "base": None, "protected": list(task["protected"])},
                                    timeout=FETCH_WAIT)
    except workspace_client.NotAnswering:
        raise
    except workspace_client.WorkspaceCallError as e:
        if e.status in (404, 409):
            # the teammate's own workspace refuses for now (its agent not there yet; an agent of theirs on that
            # repository paused, held or working): the acceptance is refused, the offer still theirs to accept
            raise agents.AgentError(409, f"Your workspace cannot take the task's work now: {e}") from None
        # the owner's why is the app's own words; the teammate is told their own workspace's
        _fail(offer_id, UNREADABLE, now)
        raise agents.AgentError(412, f"Your workspace could not fetch the task's work: {e}") from None
    made = False
    try:
        if got.get("commit") != row["commit"]:
            _fail(offer_id, NOT_BROUGHT, now)
            raise agents.AgentError(412, NOT_BROUGHT)
        loaded = sorted(got.get("loaded_files") or [])
        shown = _shown.get((offer_id, row["commit"]))
        if (shown is None and loaded) or (shown is not None and sorted(shown) != loaded):
            _seen[(offer_id, row["commit"])] = loaded           # the next GET /offers shows them
            raise agents.AgentError(409, SHOWN_OTHERS)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            _on(user)
            row = _received_row(db, user, offer_id, now)
            task = tasks.get(db, row["user_id"], row["task_id"])
            if task is None or task["state"] != "offering" or task["state_version"] != row["fence"]:
                raise agents.AgentError(409, "This offer no longer waits for you.")
            agent, account = _receiving(db, user, agent_id, task)     # read again: either may have changed meanwhile
            view = tasks.view(db, row["user_id"], row["task_id"])
            fields = {"title": task["title"], "goal": task["goal"],
                      # never a step's check, but one the teammate adopted as their own
                      "steps": [{"objective": s["objective"], "check": s["check"] if s["n"] in adopted else None}
                                for s in view["steps"]],
                      "decisions": [{"text": d["text"], "author": "teammate"} for d in view["decisions"]],
                      "protected": list(task["protected"]), "external": list(task["external"]),
                      "repo_slug": task["repo_slug"], "repo_address": task["repo_address"],
                      "checkpoint0": row["commit"], "backend": {"kind": "account", "id": agent["account"]},
                      "between_steps": "checked"}
            theirs = tasks.create(db, user["id"], agent_id, fields, now=now, task_id=theirs,
                                  continued_from={"task": row["task_id"], "offer": offer_id,
                                                  "packet_digest": row["packet_digest"], "from_user": row["user_id"],
                                                  "handed": _handed(row, view)})
            tasks.set_state(db, row["user_id"], row["task_id"], "handed_over", now=now)
            _drop_marked_press(db, row, now)
            # the binding an organisation read otherwise while the offer was out held back, applied as the offer ends
            # accepted, as the marked press is dropped; the task made here keeps to the group it was accepted in, so
            # nothing of it reaches that task
            tasks.apply_held_reread(db, row, now, end="accepted")
            db.execute("UPDATE ade_agent_task_offers SET state = 'accepted', their_task = ?, checks_adopted = ?, "
                       "updated_at = ? WHERE id = ?", (theirs, json.dumps(adopted), now, offer_id))
            tasks.account_record(db, row["user_id"], row["task_id"], "handoff", [_snapshot(user["id"], account)],
                                 step=task["step"], via="offer", now=now)
            # the owner's task bound as the task made here is bound, the organisation of the seat its account is on,
            # as custody's hand-off binds it (_bind_as_custody); `bound` in task.handed_over names
            # what this changed
            bound = _bind_as_custody(db, row["user_id"], row["task_id"], user["id"], theirs, now)
            tasks.journal(db, row["user_id"], row["task_id"], "task.handed_over",
                          {"offer": offer_id, "fence": row["fence"], "why": None, "bound": bound}, now=now)
        made = True
    finally:
        if not made:
            _forget_worktree(endpoint, theirs)
    _seen.pop((offer_id, row["commit"]), None)
    _shown.pop((offer_id, row["commit"]), None)
    return {"task_id": theirs}


def _forget_worktree(endpoint, task_id):
    """The worktree an acceptance's fetch made under `task_id`, removed from the teammate's workspace when no task came
    of it (refused after the fetch, or its transaction undone), with the branch the fetch made for it, which the
    workspace deletes while nothing has moved it (the owner's own deletion of a task keeps
    its branch), so that neither takes room nor stands in the way: best effort, a worktree no task names being one
    nothing runs in."""
    try:
        workspace_client.call(endpoint, "DELETE", f"/agents/tasks/{task_id}", {"branch": True}, timeout=FORGET_WAIT)
    except Exception as e:  # noqa: BLE001  the acceptance's own answer is what the teammate is told
        print(f"task_offers: a worktree no task names was left: {type(e).__name__}", file=sys.stderr)


def _handed(row, view):
    """What the received task keeps of the owner's side (tasks.create): the owner's address and
    outward effects as the offer's packet gave them, and the steps it names done, each with the evidence the owner's
    task has for it (said_done where it has none)."""
    packet = _loads(row["packet"], {})
    instructions = packet.get("instructions") if isinstance(packet.get("instructions"), dict) else {}
    information = packet.get("information") if isinstance(packet.get("information"), dict) else {}
    evidence = {s["n"]: s["evidence"] for s in view["steps"]}
    return {"from": instructions.get("from"),
            "done_steps": [{"n": n, "evidence": evidence.get(n) or "said_done"}
                           for n in information.get("done_steps") or []],
            "outward_effects": list(information.get("outward_effects") or [])}


def _receiving(db, user, agent_id, task):
    """The teammate's agent an offer is accepted into, and its account as they confirmed it: one of theirs, not
    retired, on the task's repository, with no open task, naming an account they confirmed as their own; else the
    refusal, with why. An agent that names none runs on whichever account their new sessions start on, which the app
    cannot name, so the task could not be made on an account they confirmed (ade/agents.py keeps "default" and none
    alike as none)."""
    agent = _agent(db, user["id"], agent_id)
    if agent is None or agent["retired_at"]:
        raise agents.AgentError(404, "No such agent.")
    if agent["repo"] != task["repo"]:
        raise agents.AgentError(412, "That agent's repository is not this task's.")
    if _open_task(db, user["id"], agent_id):
        raise agents.AgentError(412, "That agent has a task that has not ended.")
    if not agent["account"]:
        raise agents.AgentError(412, NO_ACCOUNT)
    account = _account(db, user["id"], agent["account"])
    if account is None:
        raise agents.AgentError(412, "That agent's account is not one you confirmed as your own: confirm it in "
                                     "Connections first.")
    return agent, account


def decline(user, offer_id, now=None):
    """POST /offers/{id}/decline: the teammate's; the offer ends declined, its teammate and the typed address cleared,
    and the owner's task is back, suspended. It answers while the track is off too."""
    now = _now(now)
    offer_id = _id(offer_id, "offer")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _received_row(db, user, offer_id, now)
        _end_offer(db, row, "declined", DECLINED, now)
        out = {"offer": _offer_view(db, _offer_row(db, offer_id))}
    _seen.pop((offer_id, row["commit"]), None)
    _shown.pop((offer_id, row["commit"]), None)
    return out


# ---- custody --------------------------------------------------------------------------------------------------------

def _custody_row(db, custody_id, **where):
    sql = "SELECT * FROM ade_task_custody WHERE id = ?" + "".join(f" AND {k} = ?" for k in where)
    return db.execute(sql, (custody_id, *where.values())).fetchone()


def _standing_custody(db, owner_id):
    """The owner's custody that hands tasks: accepted, not suspended; or None."""
    return db.execute("SELECT * FROM ade_task_custody WHERE owner_user = ? AND state = 'accepted' AND "
                      "custodian_user IS NOT NULL ORDER BY created_at DESC LIMIT 1", (owner_id,)).fetchone()


def ask_custody(user, address, now=None):
    """POST /custody: a request that the teammate at `address` be the user's custodian, written first as asked so that
    the owner's confirmation names it (task-custody:<id>); the custodian sees it only once confirmed. The same answer
    whether or not the address is someone's. One custody, or request, at a time."""
    _on(user)
    now = _now(now)
    address = _address(address)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if address.lower() == (_email(db, user["id"]) or "").lower():
            raise agents.AgentError(400, OWN_ADDRESS)
        if db.execute("SELECT 1 FROM ade_task_custody WHERE owner_user = ? AND confirmed_at IS NOT NULL AND state IN "
                      "('asked', 'accepted', 'suspended') LIMIT 1", (user["id"],)).fetchone():
            raise agents.AgentError(409, "You have a custodian, or a request to one, already: end it first.")
        db.execute("DELETE FROM ade_task_custody WHERE owner_user = ? AND confirmed_at IS NULL", (user["id"],))
        custody_id = secrets.token_hex(16)
        db.execute("INSERT INTO ade_task_custody (id, owner_user, custodian_user, address, state, created_at, "
                   "updated_at) VALUES (?,?,?,?,'asked',?,?)",
                   (custody_id, user["id"], _matched(db, address), address, now, now))
        return {"custody": _custody_view(db, _custody_row(db, custody_id))}


def confirm_custody(user, custody_id, confirmation, now=None):
    """POST /custody/{id}/confirm: the owner's confirmation, made for task-custody:<id>, sends the request: the
    custodian, if the address is someone's, is told content-free and sees it."""
    _on(user)
    now = _now(now)
    custody_id = _id(custody_id, "custody request")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _custody_row(db, custody_id, owner_user=user["id"])
        if row is None:
            raise agents.AgentError(404, "No such custody request.")
        if row["confirmed_at"] is not None or row["state"] != "asked":
            raise agents.AgentError(409, "This custody request was confirmed already.")
        if now - row["created_at"] > DRAFT_SECONDS:
            raise agents.AgentError(409, DRAFT_GONE)
        passkeys._use_confirmation(db, user["id"], confirmation, now, subject=f"task-custody:{custody_id}")
        db.execute("UPDATE ade_task_custody SET confirmed_at = ?, updated_at = ? WHERE id = ?", (now, now, custody_id))
        tasks.journal(db, user["id"], None, "custody.asked", {"custody": custody_id, "by": "owner", "why": None},
                      now=now)
        out = {"custody": _custody_view(db, _custody_row(db, custody_id))}
    if row["custodian_user"]:
        _tell(row["custodian_user"], lambda: push.words("custody-asked", key="custody:" + custody_id))
    return out


def _as_custodian(db, user, custody_id, states):
    row = _custody_row(db, custody_id, custodian_user=user["id"])
    if row is None or row["confirmed_at"] is None:
        raise agents.AgentError(404, "No such custody request.")
    if row["state"] not in states:
        raise agents.AgentError(409, f"This custody is {row['state']}.")
    return row


def accept_custody(user, custody_id, agent_ids, confirmation, now=None):
    """POST /custody/{id}/accept: the custodian's confirmation, made for task-custody-accept:<id>, accepts custody with
    their receiving agents in order, each with its account's group, as each stands now; given again, it changes them,
    and confirms them again after a suspension."""
    _on(user)
    now = _now(now)
    custody_id = _id(custody_id, "custody request")
    if not agent_ids or len(set(agent_ids)) != len(agent_ids):
        raise agents.AgentError(400, "Choose the agents that receive custody tasks, each once.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _as_custodian(db, user, custody_id, CUSTODY_STANDING)
        entries = []
        for agent_id in agent_ids:
            agent = _agent(db, user["id"], agent_id)
            if agent is None or agent["retired_at"]:
                raise agents.AgentError(404, "No such agent.")
            if not agent["account"]:
                raise agents.AgentError(412, NO_ACCOUNT.replace("That agent", agent["name"], 1))
            account = _account(db, user["id"], agent["account"])
            if account is None:
                raise agents.AgentError(412, f"{agent['name']}'s account is not one you confirmed as your own: "
                                             "confirm it in Connections first.")
            entries.append({"agent_id": agent_id, "digest": _agent_digest(agent), "grp": account["grp"]})
        passkeys._use_confirmation(db, user["id"], confirmation, now, subject=f"task-custody-accept:{custody_id}")
        db.execute("UPDATE ade_task_custody SET state = 'accepted', agents = ?, why = NULL, updated_at = ? WHERE "
                   "id = ?", (json.dumps(entries), now, custody_id))
        tasks.journal(db, row["owner_user"], None, "custody.accepted",
                      {"custody": custody_id, "by": "custodian", "why": None}, now=now)
        return {"custody": _custody_view(db, _custody_row(db, custody_id), as_custodian=True)}


def _end_custody(db, row, state, by, why, now):
    """Custody, or a request, ended as `state` (ended, declined or expired) by a role (`by`: owner, custodian or app):
    a request never accepted has its custodian, the typed address and its agents set to NULL with it, as an offer's
    end without acceptance; the journal names the request and the role, never the custodian; and a hand-off held under
    it ends failed, so the relay goes on (a task already with the custodian stays until a way back returns it)."""
    unaccepted = row["state"] == "asked"
    clear = ", custodian_user = NULL, address = NULL, agents = '[]'" if unaccepted else ""
    db.execute(f"UPDATE ade_task_custody SET state = ?, ended_by = ?, ended_at = ?, why = ?, updated_at = ?{clear} "
               "WHERE id = ?", (state, by, now, why, now, row["id"]))
    if row["confirmed_at"] is not None:
        tasks.journal(db, row["owner_user"], None, "custody.ended", {"custody": row["id"], "by": by, "why": why},
                      now=now)
    for offer in db.execute("SELECT * FROM ade_agent_task_offers WHERE user_id = ? AND kind = 'custody' AND state IN "
                            "('held', 'offered')", (row["owner_user"],)).fetchall():
        _end_offer(db, offer, "failed", CUSTODY_ENDED_WHY, now)


def decline_custody(user, custody_id, now=None):
    """POST /custody/{id}/decline: the custodian declines a request still asked; its custodian and the typed address
    are cleared. It answers while the track is off too."""
    now = _now(now)
    custody_id = _id(custody_id, "custody request")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _as_custodian(db, user, custody_id, ("asked",))
        _end_custody(db, row, "declined", "custodian", CUSTODY_DECLINED, now)
        return {"custody": _custody_view(db, _custody_row(db, custody_id))}


def end_custody(user, custody_id, now=None):
    """POST /custody/{id}/end: either side ends custody, or the request, at any time with the sign-in alone,
    the track off among it: it only stops hand-offs."""
    now = _now(now)
    custody_id = _id(custody_id, "custody request")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _custody_row(db, custody_id)
        if row is not None and row["owner_user"] == user["id"]:
            by = "owner"
        elif row is not None and row["custodian_user"] == user["id"] and row["confirmed_at"] is not None:
            by = "custodian"
        else:
            raise agents.AgentError(404, "No such custody request.")
        if row["state"] not in CUSTODY_STANDING:
            raise agents.AgentError(409, f"This custody is {row['state']}.")
        _end_custody(db, row, "ended", by, CUSTODY_ENDED_BY[by], now)
        return {"custody": _custody_view(db, _custody_row(db, custody_id), as_custodian=by == "custodian")}


def confirm_for_custody(user, task_id, state_version, confirmation, now=None):
    """POST /tasks/{task_id}/custody-confirm: the owner's confirmation, made for task-custody-confirm:<task id>, covers
    the task's whole instructions, its checks, external commands and protected paths and its agent's brief's version,
    as they stand (custody_digest); any change to them is a task no longer confirmed."""
    _on(user)
    now = _now(now)
    task_id = _id(task_id, "task")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task = _task(db, user["id"], task_id)
        if task.get("continued_from"):
            raise agents.AgentError(412, RECEIVED)
        if task["state"] in ("offering", "handed_over", "returning"):
            raise agents.AgentError(412, HANDING)
        if task["state_version"] != state_version:
            raise agents.AgentError(409, CHANGED)
        passkeys._use_confirmation(db, user["id"], confirmation, now, subject=f"task-custody-confirm:{task_id}")
        db.execute("UPDATE ade_agent_tasks SET custody_digest = ?, state_version = state_version + 1, updated_at = ? "
                   "WHERE id = ? AND user_id = ?", (tasks.custody_digest(db, user["id"], task_id), now, task_id,
                                                    user["id"]))
        return {"task": _handoff_task(db, user["id"], task_id)}


def custody_cross(user, task_id, state_version, confirmation, now=None):
    """POST /tasks/{task_id}/custody-cross: custody's own crossing ticked on a task, with the owner's
    confirmation made for task-custody-cross:<task id> and a task.crossing hold of 180 seconds, after which custody may
    hand the task to a receiving agent across groups as the relay lets a crossing. Refused (412) on a task whose
    crossings a press ended, on which the page offers no tick, and on a received task. On a task
    whose home is an Enterprise organisation's seats the tick is taken and changes nothing, since no crossing goes into
    or out of those (_custody_groups), as the page's line says."""
    _on(user)
    now = _now(now)
    task_id = _id(task_id, "task")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task = _task(db, user["id"], task_id)
        if task.get("continued_from"):
            raise agents.AgentError(412, RECEIVED)
        if task["crossings_ended"]:
            raise agents.AgentError(412, CROSSINGS_ENDED)
        if task["state_version"] != state_version:
            raise agents.AgentError(409, CHANGED)
        passkeys._use_confirmation(db, user["id"], confirmation, now, subject=f"task-custody-cross:{task_id}")
        pending = db.execute("SELECT id FROM ade_holds WHERE user_id = ? AND kind = ? AND subject = ? AND done_at IS "
                             "NULL AND cancelled_at IS NULL", (user["id"], HOLD_CROSSING, task_id)).fetchone()
        hold = None
        if not task["custody_cross"] and pending is None:
            hold = holds.place(db, user["id"], HOLD_CROSSING, task_id, f"Custody's crossing on task {task_id}", now)
            tasks.journal(db, user["id"], task_id, "task.switched",
                          {"switch": "custody_cross", "on": True, "hold": hold["id"]}, now=now)
        out = {"task": _handoff_task(db, user["id"], task_id),
               "hold": _hold_ref(db, hold["id"] if hold is not None else pending["id"] if pending else None)}
    if hold is not None:
        holds.tell(hold)
    return out


def _crossing_hold_ended(db, hold, now):
    """Custody's crossing's hold has ended: the tick is set, unless a press ended the task's crossings meanwhile."""
    task = tasks.get(db, hold["user_id"], hold["subject"])
    if task is None:
        return "The task is gone."
    if task["crossings_ended"]:
        return CROSSINGS_ENDED
    db.execute("UPDATE ade_agent_tasks SET custody_cross = 1, state_version = state_version + 1, updated_at = ? WHERE "
               "id = ? AND user_id = ?", (now, task["id"], hold["user_id"]))
    return None


def _crossing_notice(db, row, at_once):
    return "held-other-now" if at_once else "held-task-crossing"


def _custody_groups(db, owner_id, task, grp, grp_kind):
    """Whether custody may hand the task to a receiving agent whose account is in `grp`, of kind `grp_kind`: a task
    whose crossings ended, whatever ended them, only in the group it is on, its tick or not; otherwise its home group,
    and, with custody's crossing ticked, a crossing as the relay lets one: between the personal plans and one Team
    organisation's seats (the one the task is bound to, if it is), never into or out of an Enterprise organisation's,
    nor onto or off an Enterprise seat of the organisation it is on (tasks.enterprise_move: a group is its
    organisation's id, its kind as it is read, an organisation the owner holds seats of both kinds of an Enterprise one;
    before, the seat's plan). The task's own "Cross organisations" switch does not touch it."""
    if tasks.enterprise_move(db, task, grp, grp_kind):
        return False
    if task["crossings_ended"]:
        return grp == _on_group(db, owner_id, task)
    home = task["home_grp"]
    if grp == home:
        return True
    if not task["custody_cross"]:
        return False
    home_kind = _group_kind(db, owner_id, home)
    if home_kind not in (shapes.PERSONAL, "team") or grp_kind not in (shapes.PERSONAL, "team"):
        return False
    if home == shapes.PERSONAL:
        bound = task.get("bound_org")
        return grp_kind == "team" and (not bound or grp in (bound, "org:" + bound))
    return grp == shapes.PERSONAL


def _agents_changed(db, custody):
    """Whether a receiving agent changed since the custodian confirmed it: its brief, allow rules, MCP servers or
    account, its unattended mode, model or effort (_agent_digest), or the group of its account."""
    for entry in _loads(custody["agents"], []):
        agent = _agent(db, custody["custodian_user"], entry.get("agent_id"))
        if agent is None:
            continue
        account = _account(db, custody["custodian_user"], agent["account"])
        if _agent_digest(agent) != entry.get("digest") or account is None or account["grp"] != entry.get("grp"):
            return True
    return False


def _receiver(db, owner_id, task, custody):
    """The custodian's first receiving agent, in their order, that may take the task now: not retired, unchanged since
    confirmed, on the task's repository, with no open task, naming an account in a group custody may hand it to; or
    None. None while any receiving agent changed, since that suspends custody."""
    if custody is None or custody["custodian_user"] is None or _agents_changed(db, custody):
        return None
    custodian = custody["custodian_user"]
    for entry in _loads(custody["agents"], []):
        agent = _agent(db, custodian, entry.get("agent_id"))
        if agent is None or agent["retired_at"] or agent["repo"] != task["repo"]:
            continue
        account = _account(db, custodian, agent["account"])
        if account is None or _open_task(db, custodian, agent["id"]):
            continue
        if _custody_groups(db, owner_id, task, account["grp"], account["org_kind"]):
            return {"custody_id": custody["id"], "agent_id": agent["id"], "grp": account["grp"]}
    return None


def _custody_why_not(db, owner_id, task, now, offering=False):
    """Why custody may not hand the task now, or None: the task's Relay switch and the deployment's on; a recorded
    address; received from no one; no unknown effect; confirmed for custody as it stands; and every account of its relay
    set out on a refused window whose reset is known, the earliest more than CUSTODY_MARGIN away."""
    if task["state"] not in (("offering",) if offering else ("suspended", "running")):
        return NOT_AS_OFFERED
    if not task["relay"] or not tasks.on("relay"):
        return "The task's relay is off."
    if not task.get("repo_address"):
        return NO_ADDRESS
    if task.get("continued_from"):
        return RECEIVED
    if tasks.unknown_effects(db, owner_id, task["id"]):
        return UNKNOWN
    if not _confirmed_for_custody(db, owner_id, task):
        return "The task is not confirmed for custody as it stands."
    refused, earliest = _all_refused(_members(db, owner_id, task, now), now)
    if not refused or earliest - now <= CUSTODY_MARGIN:
        return "An account of the task's relay set may have room before long."
    return None


def custody_candidate(db, user_id, task_id, now) -> Optional[dict]:
    """The relay's step 2: the custodian's receiving agent that may take the task now, or None; None
    always while enabled() says why not. Every condition of custody holds: custody accepted and not suspended, the task
    confirmed for custody as it stands, an address, not itself received, no unknown effect, every account of its relay
    set (tasks.relay_set) out on a refused window with a known reset more than CUSTODY_MARGIN away, and a
    receiving agent on its repository with no open task, in its home group unless custody's crossing is ticked; for a
    task whose crossings ended, whatever ended them, in the group it is on, tick or not. Returns {custody_id,
    agent_id, grp}."""
    if enabled({"id": user_id}) is not None:
        return None
    task = tasks.get(db, user_id, task_id)
    if task is None or task.get("deleted_at") or _custody_why_not(db, user_id, task, now) is not None:
        return None
    return _receiver(db, user_id, task, _standing_custody(db, user_id))


def begin_custody(db, user_id, task_id, candidate, now) -> dict:
    """Begin a custody hand-off to `candidate`: the task `offering` (tasks.set_state), an offer row of kind "custody"
    and its HOLD_OFFER hold placed, its summary ids alone. Returns the hold (holds.place's view); the caller tells it
    once committed. When the hand-off completes, the custodian's task is made by tasks.create with its continued_from
    and, in `fields`, custody_cross: 1 only where this task has custody's crossing tick and its crossings_ended is 0,
    else 0. The hold's end checks every condition again (drive does the rest)."""
    task = tasks.get(db, user_id, task_id)
    custody = _custody_row(db, candidate["custody_id"], owner_user=user_id)
    offer_id = secrets.token_hex(16)
    db.execute("INSERT INTO ade_agent_task_offers (id, user_id, task_id, teammate_user, address, kind, state, fence, "
               "created_at, updated_at) VALUES (?,?,?,?,?,'custody','held',?,?,?)",
               (offer_id, user_id, task_id, custody["custodian_user"], custody["address"], task["state_version"], now,
                now))
    hold = _begin(db, user_id, task_id, offer_id, task["state_version"], now)
    _kick()
    return hold


# ---- the hold of an offer, or of a custody hand-off ----------------------------------------------------------------

def _offer_hold_ended(db, hold, now):
    """An offer's hold, or a custody hand-off's, has ended (holds.settle, in its transaction): checked again: the task
    as it was offered (offering, at the hand-off's fence), no unknown effect, the track still on; for custody, every
    condition of custody again, the task's Relay switch among them, and a receiving agent that may take it. One that
    fails ends failed with why, the task back; one that holds is left for the round, which pushes it outside this
    transaction."""
    row = db.execute("SELECT * FROM ade_agent_task_offers WHERE hold_id = ?", (hold["id"],)).fetchone()
    if row is None or row["state"] != "held":
        return "The offer is no longer held."
    why = _why_not_now(db, row, now)
    if why is not None:
        _end_offer(db, row, "failed", why, now, applying=True)
        return why
    hold["after"].append(_kick)
    return None


def _why_not_now(db, row, now):
    """Why a hand-off whose hold has ended may not go on now, or None: the task as it was offered (offering, at the
    hand-off's fence), no unknown effect, the track on; for custody, every condition of custody, the task's Relay switch
    among them, and a receiving agent that may take it. Asked at the hold's end, and again before the push and before
    the custodian's side takes the task, since the round that does either may run later than the hold's end."""
    task = tasks.get(db, row["user_id"], row["task_id"])
    if task is None or task["state"] != "offering" or task["state_version"] != row["fence"]:
        return NOT_AS_OFFERED
    if tasks.unknown_effects(db, row["user_id"], row["task_id"]):
        return UNKNOWN
    why = enabled({"id": row["user_id"]})
    if why is None and row["kind"] == "custody":
        why = _custody_why_not(db, row["user_id"], task, now, offering=True)
        if why is None and _receiver(db, row["user_id"], task, _standing_custody(db, row["user_id"])) is None:
            why = NO_RECEIVER
    return why


def _offer_hold_cancelled(db, hold, now):
    """The owner cancelled the hold: the offer, or the custody hand-off, ends withdrawn, and the task is back as it
    was."""
    row = db.execute("SELECT * FROM ade_agent_task_offers WHERE hold_id = ?", (hold["id"],)).fetchone()
    if row is not None and row["state"] == "held":
        _end_offer(db, row, "withdrawn", CANCELLED, now, applying=True)


def _offer_hold_broke(db, hold, now):
    """The hold's apply broke (holds.settle): the offer ends failed, as a refusal ends it."""
    row = db.execute("SELECT * FROM ade_agent_task_offers WHERE hold_id = ?", (hold["id"],)).fetchone()
    if row is not None and row["state"] == "held":
        _end_offer(db, row, "failed", holds.BROKE, now, applying=True)


def _offer_notice(db, row, at_once):
    if at_once:
        return "held-other-now"
    offer = db.execute("SELECT kind FROM ade_agent_task_offers WHERE id = ?", (row["subject"],)).fetchone()
    return "held-task-custody" if offer is not None and offer["kind"] == "custody" else "held-task-offer"


# ---- the way back ---------------------------------------------------------------------------------------------------

def _hand_row(db, task_id, user_id):
    """The accepted offer a task was handed by, the owner's: the hand-off a way back ends."""
    return db.execute("SELECT * FROM ade_agent_task_offers WHERE task_id = ? AND user_id = ? AND state = 'accepted' "
                      "ORDER BY created_at DESC LIMIT 1", (task_id, user_id)).fetchone()


def _start_way_back(db, row, now, taken_back=False, why=None):
    """The way back starts, in the caller's transaction: the owner's task becomes returning, in which
    nothing is queued for it until the owner's workspace has reported; the teammate's task is marked returned, after
    which nothing queues a next step, relay or auto-resume for it, and its user's Continue and Continue on are refused
    while it waits (tasks._hold_returned, _auto_resume, _may_continue), as a Continue on or Next on pressed while the
    attempt the mark found still works is refused (tasks._refuse_returned; before, it was taken, and that attempt's end
    used neither), and one working now is stopped by the round as Stop stops it; the journal is told by the offer's id,
    with CUSTODY_RETURN as its why for custody's own return, which the way back reads (_carry_back). A way back binds
    the owner's task as it ends, not here (_carry_back; a pressed offer's too): the task it was handed to moves no more
    after this, but a custody task's binding can still change, by an account of the custodian's confirmed again
    (tasks._account_moved), and its work comes back as the way back ends."""
    tasks.set_state(db, row["user_id"], row["task_id"], "returning", now=now)
    theirs = tasks.get(db, row["teammate_user"], row["their_task"]) if row["teammate_user"] and row["their_task"] \
        else None
    if theirs is not None and not theirs.get("deleted_at"):
        db.execute("UPDATE ade_agent_tasks SET returned = 1, state_version = state_version + 1, updated_at = ? WHERE "
                   "id = ? AND user_id = ?", (now, theirs["id"], row["teammate_user"]))
        if _to_hold_as_returned(theirs):
            tasks.set_state(db, row["teammate_user"], theirs["id"], "needs_owner", cause="returned",
                            reason=RETURNED_REASON, now=now)
    tasks.journal(db, row["user_id"], row["task_id"], "task.taken_back" if taken_back else "task.returned",
                  {"offer": row["id"], "fence": row["fence"], "why": why}, now=now)


def _bind_as_custody(db, user_id, task_id, from_user, from_task, now):
    """The binding fields of the task (user_id, task_id) as the task (from_user, from_task) carries them, as a press
    moves them (tasks._onto_group): a task bound to no organisation is bound to the one the other task is bound to; one
    bound to another organisation than the other task's keeps its binding, and its crossings end; and where the other
    task's crossings ended, as tasks._onto_group ends them for a press or an account confirmed again, this task's end
    too. No automatic move ever carries one organisation's work into another.

    It binds the owner's task as the task it was handed to is bound: custody's as the take ends (_take), and a pressed
    offer's at its acceptance (accept); and either as its way back ends, before its work is applied (_carry_back), so it
    reads whatever moved that task until then, read with its row whatever became of it, a deleted task's among them.
    With the two tasks swapped it binds a custody task as its owner's task is bound, where an account of the owner's
    confirmed again changed his task's binding while the custodian has it (bind_custody_task).

    A custody task is bound where it is made: on an organisation's seat, or on the personal plans with the owner's
    task's binding where it has one (tasks.create, which _take passes it). It is bound after that
    where the custodian's relay, which custody's tick lets cross, moves it onto a Team organisation's seats; where the
    custodian's press moves it; where an account of theirs it is on is confirmed again into an organisation, its tick
    or not (tasks._account_moved); or where its owner's task is bound so (bind_custody_task). A task received by a
    pressed offer is bound where it is made to the organisation of the seat its account is on, never to the owner's
    task's; after that where its user's press moves it, or where an account of theirs it is on is confirmed again into
    an organisation (tasks._account_moved; before, such a confirmation left it
    out), never by its relay, which keeps to the group it was accepted in, or, once its crossings end, to the group it
    is on. Returns the fields this changed, {} where it changed none."""
    org, ended = None, False
    if from_user and from_task:
        source = db.execute("SELECT bound_org, crossings_ended FROM ade_agent_tasks WHERE id = ? AND user_id = ?",
                            (from_task, from_user)).fetchone()
        if source is not None:
            org, ended = source["bound_org"], bool(source["crossings_ended"])
    task = tasks.get(db, user_id, task_id)
    if task is None:
        return {}
    fields = {}
    if org and not task["bound_org"]:
        fields["bound_org"] = org
    if not task["crossings_ended"] and (ended or (org and task["bound_org"] and task["bound_org"] != org)):
        fields["crossings_ended"] = 1
    if fields:
        assigns = "".join(f"{name} = ?, " for name in fields)
        db.execute(f"UPDATE ade_agent_tasks SET {assigns}state_version = state_version + 1, updated_at = ? "
                   "WHERE id = ? AND user_id = ?", (*fields.values(), now, task_id, user_id))
    return fields


def bind_custody_task(db, owner_id, task_id, now):
    """A binding the owner's task takes while it is with the custodian, its account confirmed again into an
    organisation, reaches the custody task too, so a ticked custody task's relay cannot take the work onto another
    organisation's seat. Called by tasks._account_moved and tasks._organisation_reread in the hold's end's transaction,
    once it has set the binding fields of the owner's task that the confirmation changed, and only where it changed any,
    and by custody's take (_take) for the binding a reread held back while the hand-off was out
    (tasks.apply_held_reread): where that task is handed over (`handed_over`) by custody, the custody task its accepted
    offer names takes the owner's task's binding as it then stands, each field whether or not this confirmation changed
    it (a `crossings_ended` of 1 from before among them), by the rule _bind_as_custody applies the other way (the two
    tasks swapped), and task.bound on it names what that changed, where it changed anything. Nothing for a task handed
    over by a pressed offer, whose received task's relay keeps to the group it was accepted in; nor for one returning,
    whose way back's end gives the owner's task the custody task's binding (_carry_back), the custody task moving no
    more then. Returns the fields this changed, {} where it changed none."""
    task = tasks.get(db, owner_id, task_id)
    row = _hand_row(db, task_id, owner_id) if task is not None and task["state"] == "handed_over" else None
    if row is None or row["kind"] != "custody" or not row["teammate_user"] or not row["their_task"]:
        return {}
    bound = _bind_as_custody(db, row["teammate_user"], row["their_task"], owner_id, task_id, now)
    if bound:
        tasks.journal(db, row["teammate_user"], row["their_task"], "task.bound",
                      {"offer": row["id"], "fence": row["fence"], "bound": bound}, now=now)
    return bound


def _to_hold_as_returned(task):
    """Whether a received task that went back is now held for its user, returned: one not ended, not already waiting
    for them, and not running an attempt. A task `running` with no lease runs nothing: it is the custody task whose
    step's end, being copied, began custody's own return (on_step_end), which tasks.py then moves no further; left so,
    it would read as running for good."""
    if task["state"] in ("done", "stopped", "needs_owner"):
        return False
    return not (task["state"] == "running" and task.get("lease_run"))


def take_back(user, task_id, now=None):
    """POST /tasks/{task_id}/take-back: the owner takes a handed task back at any time, the track off among it, with no
    version, as Stop: in its own transaction, so it overrides a Keep and a custody task that moves fast cannot refuse
    it. The teammate's live attempt is stopped as Stop stops it, then the way back runs (drive)."""
    now = _now(now)
    task_id = _id(task_id, "task")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task = _task(db, user["id"], task_id)
        row = _hand_row(db, task_id, user["id"])
        if row is None or task["state"] not in ("handed_over", "returning"):
            raise agents.AgentError(412, "This task is not with a teammate.")
        if task["state"] == "handed_over":
            _start_way_back(db, row, now, taken_back=True)
        out = {"task": _handoff_task(db, user["id"], task_id)}
    _kick()
    return out


def _held_custody(db, user, task_id):
    """A custody task the user holds as custodian, with the accepted hand-off it came by."""
    task = _task(db, user["id"], task_id)
    came = task.get("continued_from") or {}
    row = _offer_row(db, came.get("offer") or "", teammate_user=user["id"], kind="custody", state="accepted",
                     their_task=task_id)
    if row is None:
        raise agents.AgentError(412, "This is not a custody task you hold.")
    owner_task = tasks.get(db, row["user_id"], row["task_id"])
    if owner_task is None or owner_task["state"] != "handed_over":
        raise agents.AgentError(409, "This task is on its way back already.")
    return task, row


def _bump(db, user_id, task_id, state_version, now):
    """Against the task's state_version, a change of it (every change adds 1): so Keep
    and Give back cannot both act."""
    if not db.execute("UPDATE ade_agent_tasks SET state_version = state_version + 1, updated_at = ? WHERE id = ? AND "
                      "user_id = ? AND state_version = ?", (now, task_id, user_id, state_version)).rowcount:
        raise agents.AgentError(409, CHANGED)


def keep(user, task_id, state_version, now=None):
    """POST /tasks/{task_id}/keep: the custodian keeps a custody task until it ends, then it goes back; against the
    custody task's own state_version. Take back overrides it. It keeps a hand-off going, so it is refused while the
    track is off, as what starts or advances one is. The Keep is the offer's kept_at, which custody's own return reads
    (on_step_end, _ways_back_due), so that a note the round writes in the offer's `why` later (a secret shape in the
    teammate's work, _push_theirs) never undoes it; the `why` says KEPT for the page only where it notes nothing else,
    so a Keep never writes over such a note either."""
    _on(user)
    now = _now(now)
    task_id = _id(task_id, "task")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _, row = _held_custody(db, user, task_id)
        _bump(db, user["id"], task_id, state_version, now)
        db.execute("UPDATE ade_agent_task_offers SET kept_at = COALESCE(kept_at, ?), why = COALESCE(why, ?), "
                   "updated_at = ? WHERE id = ?", (now, KEPT, now, row["id"]))
        return {"task": _handoff_task(db, user["id"], task_id)}


def give_back(user, task_id, state_version, now=None):
    """POST /tasks/{task_id}/give-back: the custodian gives a custody task back now, by the way back; against the
    custody task's own state_version. It answers while the track is off too."""
    now = _now(now)
    task_id = _id(task_id, "task")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _, row = _held_custody(db, user, task_id)
        _bump(db, user["id"], task_id, state_version, now)
        _start_way_back(db, row, now)
        out = {"task": _handoff_task(db, user["id"], task_id)}
    _kick()
    return out


def on_step_end(db, user_id, task_id, run, now) -> bool:
    """Called by tasks.py at the end of a custody task's step on the custodian's side: True when it went back to its
    owner, since one of the owner's accounts in its relay set has room on fresh evidence (task_shapes.FRESH_SECONDS). A
    return by itself waits while the custody task has an unknown effect, and while the custodian keeps it (Keep: the
    offer's kept_at)."""
    task = tasks.get(db, user_id, task_id)
    came = (task or {}).get("continued_from") or {}
    row = _offer_row(db, came.get("offer") or "", teammate_user=user_id, kind="custody", state="accepted",
                     their_task=task_id)
    if row is None or row["kept_at"] is not None or tasks.unknown_effects(db, user_id, task_id):
        return False
    owner_task = tasks.get(db, row["user_id"], row["task_id"])
    if owner_task is None or owner_task["state"] != "handed_over" or not _has_room(db, row["user_id"], row["task_id"],
                                                                                    now):
        return False
    _start_way_back(db, row, now, why=CUSTODY_RETURN)
    _kick()
    return True


# ---- what runs outside a request: the round ------------------------------------------------------------------------

def _kick():
    """Wake the round now, its thread started where none runs. Set under the lock the thread ends under, so a wake is
    never lost to a thread that is ending."""
    with _driver_lock:
        _wake.set()
        _start_locked()


def _keep_running():
    """The round's thread started where none runs (after a restart, say), without waking it: the routes that only read
    leave the round to its own time, so a page that reads every few seconds does not make a round of each read."""
    with _driver_lock:
        _start_locked()


def _start_locked():
    if not _driver or not _driver[0].is_alive():
        thread = threading.Thread(target=_loop, name="ade-teammate", daemon=True)
        _driver[:] = [thread]
        thread.start()


def _loop():
    """The round's thread: a round when woken, or every DRIVE_EVERY seconds; it ends after a round that leaves nothing
    standing, and the next wake starts it again, so a process with no offer or custody runs no thread for them."""
    while True:
        _wake.wait(DRIVE_EVERY)
        _wake.clear()
        try:
            standing = drive()
        except Exception as e:  # noqa: BLE001  the round never takes the app down
            print(f"task_offers: a round failed: {type(e).__name__}", file=sys.stderr)
            standing = True
        with _driver_lock:
            if not standing and not _wake.is_set():
                _driver.clear()
                return


def _standing():
    """Whether anything is left for a later round: an offer held, offered or accepted, or a custody accepted (whose
    receiving agents are watched for changes)."""
    with store.connect() as db:
        return db.execute("SELECT 1 FROM ade_agent_task_offers WHERE state IN ('held', 'offered', 'accepted') "
                          "UNION ALL SELECT 1 FROM ade_task_custody WHERE state = 'accepted' LIMIT 1").fetchone() \
            is not None


def drive(now=None):
    """One round of what runs outside a request's transaction (the module's docstring): holds placed in another's
    transaction told, custody suspended where a receiving agent changed, hand-offs pushed and custody's taken, ways
    back started and carried out, and the files a next run would load read for the offers that wait on a teammate. A
    round already running in this process is not run twice. Whether anything is left standing for a later round."""
    if not _round_lock.acquire(blocking=False):
        return True
    try:
        now = _now(now)
        for step in (_asked_gone, _tell_untold, _suspend_changed, _hand_offs, _teammate_pushes, _ways_back_due,
                     _ways_back, _read_offers):
            try:
                step(now)
            except Exception as e:  # noqa: BLE001  one step failing never stops the others
                print(f"task_offers: {step.__name__} failed: {type(e).__name__}: {e}", file=sys.stderr)
        return _standing()
    finally:
        _round_lock.release()


def _endpoint(user, start):
    """The user's running workspace, started first when `start` and it is not running; None when it does not run."""
    endpoint = agents._endpoint(user)
    if endpoint is None and start:
        provider = workspaces.for_user(user)
        endpoint = provider.ensure(user) or agents._endpoint(user)
        provider.touch(user)
    return endpoint


def _no_start(e, words=NO_START, custodian=False):
    """What a refused start says on an offer, which its owner reads: where the account's work lives elsewhere, as it
    chose (storage.NotHosted), the refusal's own words for the owner's own
    workspace, and CUSTODIAN_ELSEWHERE for a custodian's, whose refusal speaks of "this account", which the owner would
    read as their own; else `words`, the app's own, since a workspace's or a provider's may name more than the offer
    should."""
    from . import storage
    if isinstance(e, storage.NotHosted):
        return CUSTODIAN_ELSEWHERE if custodian else str(e)
    return words


def _asked_gone(now):
    """An offer asked for on a working task whose attempt has ended without its beginning (the transaction that copied
    the end did not begin it: the end held the task for its owner or finished it, or after_attempt_end was not asked),
    so the request is gone: it ends failed, with why as the end left the task, the teammate and the typed
    address cleared."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT o.*, t.state AS task_state FROM ade_agent_task_offers o JOIN ade_agent_tasks t "
                              "ON t.id = o.task_id AND t.user_id = o.user_id WHERE o.kind = 'pressed' AND o.state = "
                              "'held' AND o.hold_id IS NULL AND t.state != 'running'").fetchall():
            _end_offer(db, row, "failed", _not_begun({"state": row["task_state"]}), now)


def _tell_untold(now):
    """The notice of an offer's hold begun in the transaction that copies an attempt's end (after_attempt_end), where
    nobody told it: tasks.py tells it with what that end leaves to tell, once its transaction has committed (deliver),
    so this round tells one only TOLD_GRACE seconds after it was placed, once that telling has had its time and never
    ran (the process stopped between the commit and it, say). A notice is sent from a thread of its own and the hold's
    `told` is written right after that thread starts, so a telling under way has marked the hold well before then; one
    whose mark could not be written (holds.tell keeps the approval and goes on) is told again here. The relay tells a
    custody hand-off's hold itself the same way, and the routes tell the holds they place: this round tells none of
    those, since a hold told twice sends its notice twice."""
    with store.connect() as db:
        ids = [r["id"] for r in db.execute(
            "SELECT h.id FROM ade_holds h JOIN ade_agent_task_offers o ON o.hold_id = h.id WHERE h.kind = ? AND "
            "o.kind = 'pressed' AND h.told IS NULL AND h.done_at IS NULL AND h.cancelled_at IS NULL AND "
            "h.approved_at <= ?", (HOLD_OFFER, now - TOLD_GRACE)).fetchall()]
    for hold_id in ids:
        holds.tell({"id": hold_id})


def _suspend_changed(now):
    """Custody whose receiving agent changed is suspended until the custodian confirms the agents again, and the owner
    is told, content-free."""
    suspended = []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT * FROM ade_task_custody WHERE state = 'accepted'").fetchall():
            if _agents_changed(db, row):
                db.execute("UPDATE ade_task_custody SET state = 'suspended', why = ?, updated_at = ? WHERE id = ?",
                           (CUSTODY_AGENT_CHANGED, now, row["id"]))
                tasks.journal(db, row["owner_user"], None, "custody.suspended",
                              {"custody": row["id"], "by": "app", "why": CUSTODY_AGENT_CHANGED}, now=now)
                suspended.append(row)
    for row in suspended:
        _tell(row["owner_user"], lambda key="custody:" + row["id"]: push.words("custody-suspended", key=key))


def _packet(db, row, task, commit):
    """The offer's packet: the owner's goal, steps and decisions as instructions, by a teammate
    with the owner's address, and the done steps and outward effects as information; never a step's check, nor a
    word a model wrote (a custody hand-off's runs before anyone reads it). Its canonical bytes and their digest."""
    view = tasks.view(db, row["user_id"], row["task_id"])
    body = {"schema": PACKET_SCHEMA, "offer": row["id"], "kind": row["kind"], "task": row["task_id"],
            "instructions": {"by": "teammate", "from": _email(db, row["user_id"]), "goal": task["goal"],
                             "steps": [{"n": s["n"], "objective": s["objective"]} for s in view["steps"]],
                             "decisions": [{"n": d["n"], "text": d["text"]} for d in view["decisions"]],
                             "protected": list(task["protected"]), "external": list(task["external"])},
            "information": {"done_steps": [s["n"] for s in view["steps"] if s["state"] == "done"],
                            "outward_effects": [{"tool": e["tool"], "kind": e["kind"], "target": e["target"],
                                                 "state": e["state"]}
                                                for e in view["effects"] if e["kind"] in shapes.OUTWARD]},
            "repository": {"address": task["repo_address"], "branch": _branch(row["task_id"]), "commit": commit}}
    raw = canonicalize(body)
    if len(raw) > shapes.PACKET_MAX:
        raise ValueError(f"The hand-off's packet passes {shapes.PACKET_MAX} bytes.")
    return raw, shapes.tagged_digest(HANDOFF_TAG, raw)


def _hand_offs(now):
    """Each offer, or custody hand-off, whose hold ended and that still stands, handed on."""
    with store.connect() as db:
        ids = [r["id"] for r in db.execute(
            "SELECT o.id FROM ade_agent_task_offers o JOIN ade_holds h ON h.id = o.hold_id WHERE o.state = 'held' AND "
            "h.done_at IS NOT NULL AND h.failed IS NULL AND h.cancelled_at IS NULL").fetchall()]
        ids += [r["id"] for r in db.execute(
            "SELECT id FROM ade_agent_task_offers WHERE kind = 'custody' AND state = 'offered'").fetchall()]
    for offer_id in ids:
        try:
            _hand(offer_id, now)
        except Exception as e:  # noqa: BLE001  one hand-off failing never keeps the others
            print(f"task_offers: a hand-off failed: {type(e).__name__}: {e}", file=sys.stderr)
            with store.connect() as db:
                row = _offer_row(db, offer_id)
            # pushed already, it broke on the custodian's side; else before or at its push
            _fail(offer_id, TAKE_FAILED if row is not None and row["state"] == "offered" else PUSH_FAILED, now)


def _hand(offer_id, now):
    """An offer whose hold ended, checked again first, since the round may run later than the
    hold's end (_why_not_now): the owner's workspace scans what the push would carry since the task's first commit for
    secret shapes and pushes the recorded commit, by its hash, to the task's branch at the recorded address, a branch
    something else moved, or a secret shape, holding it for the owner; the packet is built and kept, every condition
    checked once more as it is; the teammate is told. An address that is no one's has nothing pushed and nothing told:
    the offer waits, offered, and expires. A custody hand-off is first compared with the repository's default branch,
    since nobody looks at it before it runs, and is then taken at once by standing consent (_take). A workspace that
    cannot start, does not answer, or refuses ends the hand-off failed, with why, the task back (if a
    condition no longer holds, or the push or the fetch fails, the hand-off ends failed)."""
    why = None
    with store.connect() as db:
        row = _offer_row(db, offer_id)
        if row is None or row["state"] not in ("held", "offered"):
            return
        if row["state"] == "held":
            why = _why_not_now(db, row, now)
            if why is None:
                task = tasks.get(db, row["user_id"], row["task_id"])
                # the commit the app recorded as the task's last checkpoint or end state: an
                # attempt stopped after the checkpoint, its change committed by the stop, left the branch at its end
                commit = tasks.recorded_head(db, task)
                pushed = row["kind"] == "custody" or row["teammate_user"] is not None
                # the owner's lifts for the address (lift_secret), this task's first, which hold nothing in the scan
                lifted = _lifts_for(db, row["user_id"], task["repo_address"], row["task_id"])
                try:
                    raw, digest = _packet(db, row, task, commit if pushed else None)
                except ValueError:
                    raw = None
    if row["state"] == "offered":
        _take(offer_id, now)
        return
    if why is not None:
        _fail(offer_id, why, now)
        return
    if raw is None:                     # found before anything is pushed
        _fail(offer_id, PACKET_TOO_BIG, now, needs_owner=True)
        return
    owner = auth.user(row["user_id"])
    if pushed:
        try:
            endpoint = _endpoint(owner, start=True) if owner is not None else None
            if endpoint is None:
                raise workspaces.WorkspaceAccess(502, NO_START)
            if row["kind"] == "custody":
                seen = workspace_client.call(endpoint, "POST", f"/agents/tasks/{row['task_id']}/fetch",
                                             {"address": task["repo_address"], "commit": commit, "purpose": "view",
                                              "agent": task["agent_id"], "base": None,
                                              "protected": list(task["protected"])}, timeout=FETCH_WAIT)
                if seen.get("loaded_files"):
                    _fail(offer_id, LOADED_CHANGED, now)
                    return
            done = workspace_client.call(endpoint, "POST", f"/agents/tasks/{row['task_id']}/push",
                                         {"address": task["repo_address"], "commit": commit,
                                          "since": task.get("checkpoint0"), "call_id": f"handover:{offer_id}",
                                          "branch_head": commit, "lifted": lifted,
                                          "findings_key": findings_key(row["user_id"])}, timeout=PUSH_WAIT)
        except workspaces.WorkspaceAccess as e:
            _fail(offer_id, _no_start(e), now)
            return
        except workspace_client.NotAnswering:
            _fail(offer_id, SILENT, now)
            return
        except workspace_client.WorkspaceCallError as e:
            # 409: the watch, or the branch moved by something else, holds it for the owner; anything else ends it.
            # Either way in the app's own words: a workspace's may name an address
            _fail(offer_id, PUSH_HELD if e.status == 409 else PUSH_FAILED, now, needs_owner=e.status == 409)
            return
        if done.get("secret_shape"):
            findings = _findings(done)
            _fail(offer_id, _held_words(done, findings, SECRET, SCAN_UNREAD), now, needs_owner=True,
                  findings=findings)
            return
        if not done.get("pushed"):
            _fail(offer_id, PUSH_FAILED, now)
            return
    used = _passed(done, lifted) if pushed else []
    owner_id, task_id = row["user_id"], row["task_id"]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if used:
            # a push a lift let through, told whatever became of its offer while it ran: the push went out
            tasks.journal(db, owner_id, task_id, "task.secret_lift_used",
                          {"offer": offer_id, "findings": used, "by": "owner"}, now=now)
        row = _offer_row(db, offer_id, state="held")
        if row is None:
            return
        why = _why_not_now(db, row, now)        # the push took time: what it was pushed for may have changed
        if why is not None:
            _end_offer(db, row, "failed", why, now)
            return
        task = tasks.get(db, row["user_id"], row["task_id"])
        db.execute("UPDATE ade_agent_task_offers SET state = 'offered', packet = ?, packet_digest = ?, "
                   "repo_address = ?, branch = ?, \"commit\" = ?, expires_at = ?, updated_at = ? WHERE id = ?",
                   (raw, digest, task["repo_address"], _branch(row["task_id"]), commit if pushed else None,
                    now + OFFER_DAYS * 86400, now, offer_id))
    if row["kind"] == "custody":
        _take(offer_id, now)
    elif row["teammate_user"] is not None:
        _tell(row["teammate_user"], lambda: push.words("task-offered", key="offer:" + offer_id))


def _take(offer_id, now):
    """A custody hand-off pushed is taken by standing consent, checked again first (_why_not_now): the
    custodian's first receiving agent that may take it fetches the commit with the custodian's own sign-in, and in one
    transaction, every condition checked once more, its task is made, the owner's task handed over and the owner's
    account record given the hand-off. The custody task is made `ready`, and in the same transaction its first attempt
    is the custodian's relay's (tasks.relay_start: queued as `custody`, its `chosen_by`,
    never the owner's Start, which would be a press past the custodian's reserve), told once that has committed. A
    workspace that cannot start, does not answer, or refuses ends the hand-off failed, with why, and the relay decides
    again; so does a fetch whose acceptance lists a file a next run would load that the commit changes against the
    receiving agent's own start, the owner's view having compared the commit with the
    default branch alone before the push."""
    with store.connect() as db:
        row = _offer_row(db, offer_id, kind="custody", state="offered")
        if row is None:
            return
        why = _why_not_now(db, row, now)
        task = tasks.get(db, row["user_id"], row["task_id"])
        target = _receiver(db, row["user_id"], task, _standing_custody(db, row["user_id"])) if why is None else None
    if why is not None or target is None:
        _fail(offer_id, why or NO_RECEIVER, now)
        return
    custodian = auth.user(row["teammate_user"]) if row["teammate_user"] else None
    # the received task's own id, its worktree made under it by the fetch (as an offer's acceptance, accept)
    theirs = secrets.token_hex(16)
    try:
        endpoint = _endpoint(custodian, start=True) if custodian is not None else None
        if endpoint is None:
            raise workspaces.WorkspaceAccess(502, NO_START)
        # the receiving agent in the custodian's workspace before its fetch, as an acceptance puts it there (accept):
        # one made or changed while that workspace did not run, which custody meets whenever it starts the workspace
        # itself (above), was never mirrored, and the fetch would find no such agent
        agents._mirror(custodian, endpoint)
        got = workspace_client.call(endpoint, "POST", f"/agents/tasks/{theirs}/fetch",
                                    {"address": row["repo_address"], "commit": row["commit"], "purpose": "accept",
                                     "agent": target["agent_id"], "base": None, "protected": list(task["protected"])},
                                    timeout=FETCH_WAIT)
    except workspaces.WorkspaceAccess as e:
        _fail(offer_id, _no_start(e, custodian=True), now)
        return
    except workspace_client.NotAnswering:
        _fail(offer_id, SILENT, now)
        return
    except workspace_client.WorkspaceCallError as e:
        # 404 or 409: the custodian's own workspace refuses it now (no such agent there, or an agent of theirs on that
        # repository paused, held or working), their side not taking it, never a sign-in that cannot read
        _fail(offer_id, TAKE_FAILED if e.status in (404, 409) else UNREADABLE, now)
        return
    made, started = False, []
    try:
        if got.get("commit") != row["commit"]:
            _fail(offer_id, NOT_BROUGHT, now)
            return
        if got.get("loaded_files"):
            # the custodian's acceptance list: the files a next run would load that the commit changes against the
            # receiving agent's own start, as any acceptance lists them. Nobody looks at a custody hand-off before it
            # runs, so a list that is not empty ends it, as the owner's view against the default branch does before the
            # push; the worktree the fetch made goes (finally)
            _fail(offer_id, LOADED_CHANGED, now)
            return
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = _offer_row(db, offer_id, kind="custody", state="offered")
            if row is None:
                return
            # the fetch took time: every condition again, and the agent the commit was fetched for still the first
            why = _why_not_now(db, row, now)
            task = tasks.get(db, row["user_id"], row["task_id"])
            again = _receiver(db, row["user_id"], task, _standing_custody(db, row["user_id"])) if why is None \
                else None
            if why is None and (again is None or again["agent_id"] != target["agent_id"]):
                why = NO_RECEIVER
            if why is not None:
                _end_offer(db, row, "failed", why, now)
                return
            agent = _agent(db, row["teammate_user"], target["agent_id"])
            account = _account(db, row["teammate_user"], agent["account"])
            view = tasks.view(db, row["user_id"], row["task_id"])
            fields = {"title": task["title"], "goal": task["goal"],
                      # no check of the owner's runs on the custodian's side, so it goes on after each step there
                      "steps": [{"objective": s["objective"], "check": None} for s in view["steps"]],
                      "decisions": [{"text": d["text"], "author": "teammate"} for d in view["decisions"]],
                      "protected": list(task["protected"]), "external": list(task["external"]),
                      "repo_slug": task["repo_slug"], "repo_address": task["repo_address"],
                      "checkpoint0": row["commit"], "backend": {"kind": "account", "id": agent["account"]},
                      "between_steps": "any",
                      "custody_cross": 1 if task["custody_cross"] and not task["crossings_ended"] else 0,
                      # the owner's task's binding, which the custody task takes where its account's group gives
                      # none, so that its relay, where custody's crossing is ticked, crosses into that organisation's
                      # seats alone (tasks.create); custody alone, a task received by a pressed
                      # offer keeping to the group it was accepted in
                      "bound_org": task["bound_org"]}
            theirs = tasks.create(db, row["teammate_user"], agent["id"], fields, now=now, task_id=theirs,
                                  continued_from={"task": row["task_id"], "offer": offer_id,
                                                  "packet_digest": row["packet_digest"], "from_user": row["user_id"],
                                                  "handed": _handed(row, view)})
            owner_grp = _on_group(db, row["user_id"], task)
            tasks.set_state(db, row["user_id"], row["task_id"], "handed_over", now=now)
            _drop_marked_press(db, row, now)
            db.execute("UPDATE ade_agent_task_offers SET state = 'accepted', their_task = ?, updated_at = ? WHERE "
                       "id = ?", (theirs, now, offer_id))
            # the binding an organisation read otherwise while the hand-off was out held back, applied as it is taken,
            # and given to the custody task before its first attempt, as a binding the owner's task takes while it is
            # handed over is (bind_custody_task). The take's check above read the receiver under the organisation as it
            # reads now, so where the reading changed a hand-off it lets go on stays in the group the task is on, as
            # custody keeps a task whose crossings ended (_custody_groups)
            if tasks.apply_held_reread(db, row, now, end="accepted"):
                bind_custody_task(db, row["user_id"], row["task_id"], now)
            tasks.account_record(db, row["user_id"], row["task_id"], "handoff",
                                 [_snapshot(row["teammate_user"], account)], step=task["step"], via="custody",
                                 now=now)
            # its first attempt, the custodian's relay's, as after any of its steps
            tasks.relay_start(db, row["teammate_user"], theirs, now=now, tells=started)
            # whether the hand-off crossed a group: the received task's own group, or the group its first attempt put it
            # on in this transaction, against the group of the account the owner's task was on, which tasks.py's custody
            # line names
            received = tasks.get(db, row["teammate_user"], theirs)
            crossed = received is not None and (received["home_grp"] != owner_grp
                                                or _on_group(db, row["teammate_user"], received) != owner_grp)
            # the owner's task bound as its custody task is bound once that attempt is decided (_bind_as_custody);
            # `bound` in task.handed_over names what this changed
            bound = _bind_as_custody(db, row["user_id"], row["task_id"], row["teammate_user"], theirs, now)
            tasks.journal(db, row["user_id"], row["task_id"], "task.handed_over",
                          {"offer": offer_id, "fence": row["fence"], "why": None, "crossed": crossed, "bound": bound},
                          now=now)
        made = True
    finally:
        if not made:
            _forget_worktree(endpoint, theirs)
    tasks.deliver(row["teammate_user"], started)
    _tell(row["teammate_user"], lambda: push.words("task-handed", key="offer:" + offer_id))


def _ways_back_due(now):
    """Each hand-off whose way back is due: the received task done, stopped or gone, its agent retired or its user
    removed; or a custody task waiting on the custodian's side, for a reset of their accounts or
    not yet started, while one of the owner's accounts has room on fresh evidence, with no unknown effect and not
    kept (the offer's kept_at)."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT * FROM ade_agent_task_offers WHERE state = 'accepted'").fetchall():
            owner_task = tasks.get(db, row["user_id"], row["task_id"])
            if owner_task is None or owner_task["state"] != "handed_over":
                continue
            mate = row["teammate_user"]
            theirs = tasks.get(db, mate, row["their_task"]) if mate and row["their_task"] else None
            agent = _agent(db, mate, theirs["agent_id"]) if theirs is not None else None
            ended = theirs is None or bool(theirs.get("deleted_at")) or theirs["state"] in ("done", "stopped") or \
                agent is None or bool(agent["retired_at"]) or _email(db, mate) is None
            room = (not ended and row["kind"] == "custody" and theirs["state"] in ("suspended", "ready")
                    and row["kept_at"] is None
                    and not tasks.unknown_effects(db, mate, theirs["id"])
                    and _has_room(db, row["user_id"], row["task_id"], now))
            if ended or room:
                _start_way_back(db, row, now, why=None if ended else CUSTODY_RETURN)


def _push_theirs(row, now):
    """A received task's work pushed from the teammate's workspace to the owner's task's branch (what the
    teammate's side pushes): its head as the app has recorded it (tasks.recorded_head), by its hash, with the
    teammate's own sign-in, after their workspace's secret-shape scan, while no attempt of it is queued or working; the
    last commit pushed kept on the offer (`pushed`), which the way back fetches. Returns "pushed"; "nothing" (nothing
    new, or no task to push); "stopped" (their workspace not running, or their user gone); "silent" (it runs and does
    not answer); or "refused" (a secret shape, named on the offer in the app's words, or a state of the workspace's
    own, as its watch). A push is the same however often it is made, so one cut off is made again by a later round."""
    with store.connect() as db:
        theirs = tasks.get(db, row["teammate_user"], row["their_task"]) if row["teammate_user"] and \
            row["their_task"] else None
        if theirs is None or theirs["lease_run"]:
            return "nothing"
        head = tasks.recorded_head(db, theirs)
        # the teammate's own lifts for the address (lift_secret), never the owner's: the push is theirs
        lifted = _lifts_for(db, row["teammate_user"], theirs["repo_address"], theirs["id"])
    since = row["pushed"] or row["commit"]
    if not head or head == since:
        return "nothing"
    mate = auth.user(row["teammate_user"])
    endpoint = agents._endpoint(mate) if mate is not None else None
    if endpoint is None:
        return "stopped"
    try:
        done = workspace_client.call(endpoint, "POST", f"/agents/tasks/{theirs['id']}/push",
                                     {"address": theirs["repo_address"], "commit": head, "since": since,
                                      "call_id": f"handover:{row['id']}:{theirs['fence']}", "branch_head": head,
                                      "ref": "refs/heads/" + _branch(row["task_id"]), "lifted": lifted,
                                      "findings_key": findings_key(row["teammate_user"])}, timeout=PUSH_WAIT)
    except workspace_client.NotAnswering:
        return "silent"
    except workspace_client.WorkspaceCallError:
        return "refused"
    if done.get("secret_shape"):
        findings = _findings(done)
        _note(row["id"], _held_words(done, findings, THEIR_SECRET, THEIR_UNREAD), now, findings=findings)
        return "refused"
    if not done.get("pushed"):
        return "refused"
    used = _passed(done, lifted)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # a hold of the push's said no more: its note and its findings go, the push having gone out (an unread
        # hold's too, whose findings are [], _note); where the custodian kept the task, its `why` says so again, the
        # words the note wrote over (the Keep itself is kept_at, which nothing here touches). Whatever note stands
        # goes with the findings, a waiting note written over the hold's among them, which the way back, running after
        # this in each round, writes again
        db.execute("UPDATE ade_agent_task_offers SET pushed = ?, why = CASE WHEN secret_findings IS NULL THEN why WHEN "
                   "kept_at IS NOT NULL THEN ? ELSE NULL END, secret_findings = NULL, updated_at = ? WHERE id = ? AND "
                   "state = 'accepted'", (head, KEPT, now, row["id"]))
        if used:
            # told whatever became of the offer while the push ran
            for user_id, task_id in ((row["teammate_user"], row["their_task"]), (row["user_id"], row["task_id"])):
                tasks.journal(db, user_id, task_id, "task.secret_lift_used",
                              {"offer": row["id"], "findings": used, "by": "teammate"}, now=now)
    return "pushed"


def _teammate_pushes(now):
    """Each received task's work pushed to the owner's task's branch as it goes (_push_theirs), between its attempts,
    so that the branch holds the teammate's work for the way back, whatever ends their side."""
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_agent_task_offers WHERE state = 'accepted' AND their_task IS NOT "
                          "NULL").fetchall()
    for row in rows:
        try:
            _push_theirs(row, now)
        except Exception as e:  # noqa: BLE001  one failing never keeps the others; the next round tries it again
            print(f"task_offers: a teammate's push failed: {type(e).__name__}: {e}", file=sys.stderr)


def _note(offer_id, why, now, findings=None):
    """An accepted offer's why, said to the owner; with `findings`, a hold of the teammate's side's push (_push_theirs),
    which their teammate may lift (lift_secret), [] for what its scan could not read, which no lift reaches but the
    next push that goes out clears; kept as they were otherwise."""
    with store.connect() as db:
        if findings is None:
            db.execute("UPDATE ade_agent_task_offers SET why = ?, updated_at = ? WHERE id = ? AND state = 'accepted'",
                       (why, now, offer_id))
        else:
            db.execute("UPDATE ade_agent_task_offers SET why = ?, secret_findings = ?, updated_at = ? WHERE id = ? AND "
                       "state = 'accepted'", (why, json.dumps(findings, separators=(",", ":")), now, offer_id))


# ---- a hand-off's push held by its secret scan, and its lift ---------------------------------------------------------

def _clean(text, most=300):
    """A file's name as a workspace gave it, as the app says it: its characters that print, at most `most`."""
    return "".join(c for c in text if c.isprintable())[:most] or None


def _clean_file(text):
    """A finding's file as the app says it (_clean), and an ellipsis whole where that holds a secret shape, which
    dropping a character or the cut can make where the workspace's file held none ("gh", a zero-width space, "p_" and a
    token's characters; "PP-" and nine letters cut to eight)."""
    text = _clean(text)
    return chr(0x2026) if text and tasks.secret_shape(text) else text


def _findings(done):
    """The findings a push's answer names (task_runs._scan_all's secret_findings), as the app keeps them: each with its
    id, its kind, its shape (one of the secret shapes the scan knows), its file as the scan named it (a part shaped like
    a secret hidden), its line (a line's number in the file or in the commit object), its place (its path's place among
    those its commit changes, which tells apart two files that read the same once a part is hidden) and the column its
    shape begins at (each from 1, None from a workspace that gives none), and its commit; one of another form is left
    out, and at most FINDINGS_MOST are kept. [] from a workspace that names none (one from before them, or what the scan
    could not read)."""
    got = done.get("secret_findings")
    out = []
    for f in got if isinstance(got, list) else []:
        if not (isinstance(f, dict) and isinstance(f.get("finding"), str) and _FINDING.fullmatch(f["finding"])
                and f.get("kind") in FINDING_KINDS and f.get("shape") in _SHAPES):
            continue
        file, commit, column, line, place = f.get("file"), f.get("commit"), f.get("column"), f.get("line"), \
            f.get("place")
        out.append({"finding": f["finding"], "kind": f["kind"], "shape": f["shape"],
                    "file": _clean_file(file) if isinstance(file, str) else None,
                    "line": line if type(line) is int and 0 < line <= COLUMN_MOST else None,
                    "place": place if type(place) is int and 0 < place <= COLUMN_MOST else None,
                    "column": column if type(column) is int and 0 < column <= COLUMN_MOST else None,
                    "commit": commit if isinstance(commit, str) and shapes.COMMIT.fullmatch(commit) else None})
        if len(out) == FINDINGS_MOST:
            break
    return out


def _held_words(done, findings, words, unread_words):
    """Why a push was held, in the app's words: its first finding, by its shape and where it is (a commit's id whole, as
    the page says it), and how many more; what the scan could not read in
    `unread_words`; and from a workspace that names no finding, its secret_shape as before."""
    if findings:
        f = findings[0]
        where = {"line": f"in {f['file']}" if f["file"] else "in a file",
                 "name": f"in a file's name, {f['file']}" if f["file"] else "in a file's name",
                 "commit": f"in commit {f['commit']}" if f["commit"] else "in a commit"}[f["kind"]]
        more = f", and {len(findings) - 1} more" if len(findings) > 1 else ""
        return words.format(what=f"{f['shape']} {where}{more}")
    shape = done.get("secret_shape")
    if isinstance(shape, dict):
        what = " in ".join((_clean_file if k == "file" else _clean)(str(shape.get(k))) or ""
                           for k in ("shape", "file") if shape.get(k))
        return (unread_words if shape.get("shape") in UNREAD_SHAPES else words).format(what=what)
    return words.format(what=_clean(str(shape)) or "")


def _lifts_for(db, user_id, address, task_id=None):
    """The ids of the findings `user_id` lifted for the repository at `address`, at most LIFTS_PASSED: those lifted on
    `task_id` first, so that a range holding more findings than the window (a default branch's corpus of made-up
    tokens, say) still goes out once its own task's are lifted, then
    the newest; what a push of theirs to it is given as lifted, never another user's."""
    if not address:
        return []
    return [r["finding"] for r in db.execute(
        "SELECT finding FROM ade_secret_lifts WHERE user_id = ? AND repo_address = ? ORDER BY task_id = ? DESC, at "
        "DESC, rowid DESC LIMIT ?", (user_id, address, task_id or "", LIFTS_PASSED)).fetchall()]


def findings_key(user_id):
    """The key a push's scan names `user_id`'s findings by (task_runs.finding_id), as 64 hex, given with each push of
    theirs: made from the app's own signing secret for that person, so the same in whichever workspace scans and
    however often its secret is made again (the fly provider makes a new one when it replaces a machine). The app's
    database, which keeps the ids, cannot test a guessed text against one;
    the app, which holds the secret, can, and so can a workspace given this key with a push, which holds
    the text anyway."""
    return hmac.new(config.token_secret(), b"proofpane secret findings\0" + user_id.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def lift_subject(task_id, findings):
    """The change a lift's confirmation is made for: the task, and a digest of the ids it lifts (sorted, once each,
    joined by commas; SHA-256, its first 32 hex), so that one made for some findings is spent on no others.
    The page makes the same."""
    digest = hashlib.sha256(",".join(sorted(set(findings))).encode("ascii")).hexdigest()[:32]
    return f"task-secret-lift:{task_id}:{digest}"


def _passed(done, lifted):
    """The lifted findings a push that went out met, as its answer names them: those it was given alone."""
    given = set(lifted)
    got = done.get("lifted_passed")
    return [f for f in dict.fromkeys(got if isinstance(got, list) else []) if isinstance(f, str) and f in given]


def _held_findings(row):
    """The findings an offer's push, or its teammate's side's, was held for, as _findings kept them."""
    return [f for f in _loads(row["secret_findings"], []) if isinstance(f, dict) and isinstance(f.get("finding"), str)
            and _FINDING.fullmatch(f["finding"])] if row is not None else []


def _held_offer(db, user_id, task):
    """The offer whose push's hold `user_id` may lift on `task`, or None: on the owner's task, its newest offer, where
    it ended failed for findings in the owner's own push (_fail); on a task they received, the accepted offer it came
    from, where their side's push was held (_note). Never the other side's: the owner's push and the teammate's each go
    out with its own person's sign-in, and an accepted offer's findings, the newest offer of the owner's task while it
    is with the teammate, are the teammate's."""
    came = task.get("continued_from")
    if came:
        row = db.execute("SELECT * FROM ade_agent_task_offers WHERE id = ? AND teammate_user = ? AND their_task = ? AND "
                         "state = 'accepted'", (came.get("offer"), user_id, task["id"])).fetchone()
    else:
        row = _current_offer(db, task["id"])
        row = row if row is not None and row["user_id"] == user_id and row["state"] == "failed" else None
    return row if _held_findings(row) else None


def _hold_view(db, user_id, row, side, task_id, address):
    """SecretHold (taskOffersApi.ts): a task's push held for findings, each with whether its person lifted it, as the
    task's next push is given it (_lifts_for)."""
    lifted = set(_lifts_for(db, user_id, address, task_id))
    return {"task_id": task_id, "offer": row["id"], "side": side,
            "findings": [dict(f, lifted=f["finding"] in lifted) for f in _held_findings(row)]}


def _secret_holds(db, user_id):
    """The holds of `user_id`'s pushes they may lift (GET /offers' secret_holds): on each task of theirs whose newest
    offer's push was held, and on each task they received whose side's push was."""
    out = []
    for row in db.execute("SELECT * FROM ade_agent_task_offers WHERE user_id = ? AND state = 'failed' AND "
                          "secret_findings IS NOT NULL ORDER BY created_at, rowid", (user_id,)).fetchall():
        task = tasks.get(db, user_id, row["task_id"])
        current = _current_offer(db, row["task_id"])
        if task is not None and not task.get("deleted_at") and current is not None and current["id"] == row["id"] \
                and _held_findings(row):
            out.append(_hold_view(db, user_id, row, "owner", row["task_id"], task["repo_address"]))
    for row in db.execute("SELECT * FROM ade_agent_task_offers WHERE teammate_user = ? AND state = 'accepted' AND "
                          "their_task IS NOT NULL AND secret_findings IS NOT NULL ORDER BY created_at, rowid",
                          (user_id,)).fetchall():
        theirs = tasks.get(db, user_id, row["their_task"])
        if theirs is not None and not theirs.get("deleted_at") and _held_findings(row):     # an unread hold's are []
            out.append(_hold_view(db, user_id, row, "teammate", row["their_task"], theirs["repo_address"]))
    return out


def lift_secret(user, task_id, findings, confirmation, now=None):
    """POST /tasks/{task_id}/secret-lift: the person whose push a secret scan held says the findings named, by their
    ids, are no secrets, with a confirmation made for them (lift_subject: task-secret-lift:<task id>:<digest of the
    ids>). On their own task, the owner lifts what its newest offer's push was held for; on a task they received,
    a teammate lifts what their side's push was held for, which the owner cannot: each push goes out with its own
    person's sign-in, and a lift by the owner would carry the teammate's work out on the owner's word. Each finding is
    lifted for the task's repository address, its lifter's alone (_lifts_for), and the journal is told on the task. On
    the owner's task, held for the owner by that hold, once every finding of it is lifted the task waits again as
    before the offer (_wait_again), so that it can be handed again. Refused (409) where the task is not held for each
    finding named, the confirmation unspent."""
    _on(user)
    now = _now(now)
    task_id = _id(task_id, "task")
    if not (isinstance(findings, list) and 0 < len(findings) <= FINDINGS_MOST
            and all(isinstance(f, str) and _FINDING.fullmatch(f) for f in findings)):
        raise agents.AgentError(400, BAD_FINDINGS)
    named = list(dict.fromkeys(findings))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task = _task(db, user["id"], task_id)
        row = _held_offer(db, user["id"], task)
        held = {f["finding"]: f for f in _held_findings(row)}
        if row is None or not task.get("repo_address") or any(f not in held for f in named):
            raise agents.AgentError(409, NOT_HELD)
        passkeys._use_confirmation(db, user["id"], confirmation, now, subject=lift_subject(task_id, named))
        for f in named:
            db.execute("INSERT INTO ade_secret_lifts (user_id, repo_address, finding, task_id, offer_id, kind, shape, at) "
                       "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (user_id, repo_address, finding) DO UPDATE SET task_id = "
                       "excluded.task_id, offer_id = excluded.offer_id, kind = excluded.kind, shape = excluded.shape, "
                       "at = excluded.at", (user["id"], task["repo_address"], f, task_id, row["id"], held[f]["kind"],
                                            str(held[f].get("shape"))[:200], now))
        lifted = set(_lifts_for(db, user["id"], task["repo_address"], task_id))
        back = (not task.get("continued_from") and row["state"] == "failed" and task["state"] == "needs_owner"
                and task["cause"] == "handoff" and task["reason"] == row["why"] and set(held) <= lifted)
        if back:
            _wait_again(db, row, task, LIFTED_BACK, now)
        tasks.journal(db, user["id"], task_id, "task.secret_lifted",
                      {"offer": row["id"], "findings": named, "back": back}, now=now)
        side = "teammate" if task.get("continued_from") else "owner"
        return {"hold": _hold_view(db, user["id"], row, side, task_id, task["repo_address"]),
                "task": _handoff_task(db, user["id"], task_id)}


def _effect(entry):
    """An effect of the teammate's task as the owner's store takes it: its tool, class,
    target and state, an unknown one as unknown."""
    return {"call_id": entry["call_id"], "tool": entry["tool"], "kind": entry["kind"], "target": entry["target"],
            "intent": {}, "end": None, "exit": entry.get("exit_status"), "settled": entry["state"],
            "unseen": bool(entry.get("unseen")), "at": entry.get("at")}


def _ways_back(now):
    with store.connect() as db:
        ids = [r["id"] for r in db.execute(
            "SELECT o.id FROM ade_agent_task_offers o JOIN ade_agent_tasks t ON t.id = o.task_id AND t.user_id = "
            "o.user_id WHERE o.state = 'accepted' AND t.state = 'returning'").fetchall()]
    for offer_id in ids:
        try:
            _carry_back(offer_id, now)
        except Exception as e:  # noqa: BLE001  one way back failing never keeps the others; the next round tries it
            print(f"task_offers: a way back failed: {type(e).__name__}: {e}", file=sys.stderr)


def _carry_back(offer_id, now):
    """A way back carried out, in five steps: a live attempt on the teammate's side stopped first, as
    Stop stops it, and waited for (a workspace that does not answer is waited for, and the page says so; a stopped one
    runs nothing, so the task comes back at once); then their work pushed, where their workspace runs (_push_theirs),
    and the owner's workspace fetches the last commit the teammate pushed, which must descend from the handed one, and
    reports the protected paths it touches, the files a next run would load it changes and the steps its commits
    complete; the owner's own checks run on it for each step done on the teammate's side that came back; and the
    results, with the teammate's task's effects and those steps, are applied to the owner's task (tasks.take_return).
    The offer then ends returned, and the owner is told."""
    with store.connect() as db:
        row = _offer_row(db, offer_id, state="accepted")
        if row is None:
            return
        task = tasks.get(db, row["user_id"], row["task_id"])
        if task is None or task["state"] != "returning":
            return
        # their task is read even where an admin removed their user: its rows, its checkpoints among them, stay
        mate = auth.user(row["teammate_user"]) if row["teammate_user"] else None
        theirs = tasks.get(db, row["teammate_user"], row["their_task"]) if row["teammate_user"] and \
            row["their_task"] else None
    if theirs is not None and theirs["state"] == "running" and theirs.get("lease_run"):
        if mate is not None and agents._endpoint(mate) is not None:
            if not _stop(mate, theirs["lease_run"]):
                _note(offer_id, WAITING, now)
            return                      # its end is copied first; a later round goes on
        # its workspace is stopped, or its user gone: no attempt runs there, so the task comes back with the last
        # commit pushed
    owner = auth.user(row["user_id"])
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        theirs = tasks.get(db, row["teammate_user"], row["their_task"]) if theirs is not None else None
        if theirs is not None and _to_hold_as_returned(theirs):
            tasks.set_state(db, row["teammate_user"], theirs["id"], "needs_owner", cause="returned",
                            reason=RETURNED_REASON, now=now)
        their_view = tasks.view(db, row["teammate_user"], theirs["id"]) if theirs is not None else None
        owner_view = tasks.view(db, row["user_id"], row["task_id"])
    # the teammate's work pushed first, where their workspace runs (_push_theirs): what comes back is the last commit
    # pushed, and with their machine stopped, or their user gone, that is what was pushed before
    if theirs is not None and _push_theirs(row, now) == "silent":
        _note(offer_id, WAITING, now)
        return
    with store.connect() as db:
        row = _offer_row(db, offer_id, state="accepted")
    if row is None:
        return
    commit = row["pushed"] or row["commit"]
    try:
        endpoint = _endpoint(owner, start=True) if owner is not None else None
    except workspaces.WorkspaceAccess as e:
        _note(offer_id, _no_start(e, WAITING_OWN), now)        # tried again next round, as any start refused
        return
    if endpoint is None:
        _note(offer_id, WAITING_OWN, now)
        return
    try:
        got = workspace_client.call(endpoint, "POST", f"/agents/tasks/{row['task_id']}/fetch",
                                    {"address": task["repo_address"], "commit": commit, "purpose": "return",
                                     "agent": task["agent_id"], "base": row["commit"],
                                     "protected": list(task["protected"])}, timeout=FETCH_WAIT)
        descends = bool(got.get("descends"))
    except workspace_client.WorkspaceCallError as e:
        if e.status != 409:
            _note(offer_id, WAITING_OWN, now)
            return
        got, descends = {"commit": commit}, False          # it does not descend from the handed commit
    # a step done on the teammate's side comes back only where its checkpoint is among the commits fetched (the steps
    # the fetch read from them): work of theirs never pushed does not come back as done
    came = set(got.get("steps") or []) if descends else set()
    their_steps = [s for s in (their_view or {}).get("steps", []) if s["state"] == "done" and s["n"] in came]
    owner_checks = {s["n"]: s["check"] for s in owner_view["steps"] if s["check"]}
    to_check = [s["n"] for s in their_steps if s["n"] in owner_checks]
    passed = {}
    if descends and to_check:
        try:
            # the owner's checks given with the request: the owner's workspace knows only those its own attempts ran
            results = workspace_client.call(endpoint, "POST", f"/agents/tasks/{row['task_id']}/checks",
                                            {"commit": got.get("commit", commit), "steps": to_check,
                                             "checks": {str(n): owner_checks[n] for n in to_check}},
                                            timeout=CHECKS_WAIT)
        except workspace_client.WorkspaceCallError:
            _note(offer_id, WAITING_OWN, now)
            return
        passed = {r["n"]: r.get("exit") == 0 for r in results.get("results", [])}
    # the tree the owner's workspace left the worktree at, which tasks.py keeps with the commit as the task's end state
    report = {"commit": got.get("commit", commit), "tree": got.get("tree"), "descends": descends,
              "protected_touched": list(got.get("protected_touched") or []),
              "loaded_changed": list(got.get("loaded_files") or []),
              "effects": [_effect(e) for e in (their_view or {}).get("effects", [])],
              "steps": [{"n": s["n"], "state": s["state"], "evidence": s.get("evidence"),
                         "check_passed": passed.get(s["n"]) if s["n"] in to_check else None} for s in their_steps]}
    tells = []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _offer_row(db, offer_id, state="accepted")
        task = tasks.get(db, row["user_id"], row["task_id"]) if row is not None else None
        if task is None or task["state"] != "returning":
            return
        # custody's own return, its way back begun on the owner's room (CUSTODY_RETURN), goes on as the owner's Between
        # steps allows; every other way back waits for the owner
        began = db.execute("SELECT payload FROM ade_agent_task_events WHERE user_id = ? AND task_id = ? AND kind IN "
                           "('task.returned', 'task.taken_back') ORDER BY seq DESC LIMIT 1",
                           (row["user_id"], row["task_id"])).fetchone()
        began = _loads(began["payload"], {}) if began is not None else {}
        if row["kind"] == "custody" and began.get("offer") == offer_id and began.get("why") == CUSTODY_RETURN:
            report["way"] = "return"
        # every way back, custody's or a pressed offer's, binds the owner's task as the task it was
        # handed to is bound now, whatever moved that task or changed its binding since the hand-off (_bind_as_custody),
        # before its work is applied and the owner's relay may go on with it; task.bound names what that changed, where
        # it changed anything
        bound = _bind_as_custody(db, row["user_id"], row["task_id"], row["teammate_user"], row["their_task"], now)
        if bound:
            tasks.journal(db, row["user_id"], row["task_id"], "task.bound",
                          {"offer": offer_id, "fence": row["fence"], "bound": bound}, now=now)
        tasks.take_return(db, row["user_id"], row["task_id"], report, now=now, tells=tells)
        db.execute("UPDATE ade_agent_task_offers SET state = 'returned', address = NULL, why = NULL, updated_at = ? "
                   "WHERE id = ?", (now, offer_id))
    tasks.deliver(row["user_id"], tells)        # what the relay left to tell, once the return has committed
    _tell(row["user_id"], lambda: push.words("task-returned", key="offer:" + offer_id))


def _read_offers(now):
    """The files a next run would load that each waiting offer's commit changes, against the repository's default
    branch as the teammate's own workspace has it, read while that workspace runs, so that the
    offer shows them before it can be accepted."""
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_agent_task_offers WHERE kind = 'pressed' AND state = 'offered' AND "
                          "teammate_user IS NOT NULL AND \"commit\" IS NOT NULL").fetchall()
        waiting = {(r["id"], r["commit"]) for r in rows}
    for known in (_seen, _shown):
        for key in [k for k in list(known) if k not in waiting]:     # a copy: a request may change it meanwhile
            known.pop(key, None)
    for row in rows:
        if (row["id"], row["commit"]) in _seen:
            continue
        mate = auth.user(row["teammate_user"])
        endpoint = agents._endpoint(mate) if mate is not None else None
        if endpoint is None:
            continue
        with store.connect() as db:
            task = tasks.get(db, row["user_id"], row["task_id"])
        try:
            got = workspace_client.call(endpoint, "POST", f"/agents/tasks/{row['task_id']}/fetch",
                                        {"address": row["repo_address"], "commit": row["commit"], "purpose": "view",
                                         "agent": None, "base": None, "protected": list(task["protected"])},
                                        timeout=FETCH_WAIT)
        except workspace_client.WorkspaceCallError:
            continue
        _seen[(row["id"], row["commit"])] = sorted(got.get("loaded_files") or [])


# ---- the scheduler's round, and the operator's command -------------------------------------------------------------

def forget(db, now) -> None:
    """The scheduler's round: end as "expired" an offer past its expires_at and a confirmed custody request not answered
    in OFFER_DAYS after its confirmed_at, each with its teammate or custodian and the typed address set to NULL; reduce,
    as reduce_person reduces an ended row, each row the operator's command asked to reduce while it still stood
    (reduce_requested_at) once it has ended; delete drafts past DRAFT_SECONDS, unconfirmed custody requests among them;
    set an ended custody's custodian and address to NULL CUSTODY_KEEP_DAYS after its ended_at, with its reduced_at; and
    delete offers of deleted tasks, custody's hand-offs among them, after tasks.KEEP_DAYS. Wakes the round."""
    for row in db.execute("SELECT * FROM ade_agent_task_offers WHERE state = 'offered' AND expires_at <= ?",
                          (now,)).fetchall():
        _end_offer(db, row, "expired", EXPIRED, now)
    for row in db.execute("SELECT * FROM ade_task_custody WHERE state = 'asked' AND confirmed_at IS NOT NULL AND "
                          "confirmed_at <= ?", (now - OFFER_DAYS * 86400,)).fetchall():
        _end_custody(db, row, "expired", "app", CUSTODY_EXPIRED, now)
    # what still stands (OFFER_STANDING, CUSTODY_STANDING) is left: its hand-off needs the person
    db.execute("UPDATE ade_agent_task_offers SET teammate_user = NULL, address = NULL, their_task = NULL WHERE "
               "reduce_requested_at IS NOT NULL AND state NOT IN ('draft', 'held', 'offered', 'accepted') AND "
               "(teammate_user IS NOT NULL OR address IS NOT NULL OR their_task IS NOT NULL)")
    db.execute("UPDATE ade_task_custody SET custodian_user = NULL, address = NULL, agents = '[]', reduced_at = ? WHERE "
               "reduce_requested_at IS NOT NULL AND state NOT IN ('asked', 'accepted', 'suspended') AND reduced_at IS "
               "NULL AND (custodian_user IS NOT NULL OR address IS NOT NULL)", (now,))
    db.execute("DELETE FROM ade_agent_task_offers WHERE state = 'draft' AND created_at <= ?", (now - DRAFT_SECONDS,))
    db.execute("DELETE FROM ade_task_custody WHERE state = 'asked' AND confirmed_at IS NULL AND created_at <= ?",
               (now - DRAFT_SECONDS,))
    db.execute("UPDATE ade_task_custody SET custodian_user = NULL, address = NULL, agents = '[]', reduced_at = ? WHERE "
               "state = 'ended' AND reduced_at IS NULL AND ended_at <= ? AND (custodian_user IS NOT NULL OR "
               "address IS NOT NULL)", (now, now - CUSTODY_KEEP_DAYS * 86400))
    db.execute("DELETE FROM ade_agent_task_offers WHERE task_id IN (SELECT id FROM ade_agent_tasks WHERE deleted_at IS "
               "NOT NULL AND deleted_at <= ?) OR task_id NOT IN (SELECT id FROM ade_agent_tasks)",
               (now - tasks.KEEP_DAYS * 86400,))
    _kick()


def _person(db, person):
    """(user id, email) that the operator's command names: a user id of the deployment, with its email; a user id no
    account has (32 hex), as the id of a teammate whose account was removed since, which a row may still name as
    tasks.py's command takes it (tasks._person); or an email, a user's or not."""
    if not isinstance(person, str) or not person.strip():
        return None, None
    person = person.strip()
    row = db.execute("SELECT id, email FROM users WHERE id = ?", (person,)).fetchone()
    if row is None and shapes.TASK_ID.fullmatch(person):
        return person, None
    if row is None and "@" in person:
        row = db.execute("SELECT id, email FROM users WHERE email = ? COLLATE NOCASE LIMIT 1", (person,)).fetchone()
        if row is None:
            return None, person
    return (row["id"], row["email"]) if row is not None else (None, None)


def _naming(db, person):
    """The offer rows and custody rows that name `person`, as teammate or custodian or by the address the owner
    typed."""
    user_id, email = _person(db, person)
    if user_id is None and email is None:
        return [], []
    offers = db.execute("SELECT * FROM ade_agent_task_offers WHERE (teammate_user IS NOT NULL AND teammate_user = ?) "
                        "OR (address IS NOT NULL AND address = ? COLLATE NOCASE) ORDER BY created_at, rowid",
                        (user_id, email)).fetchall()
    custody = db.execute("SELECT * FROM ade_task_custody WHERE (custodian_user IS NOT NULL AND custodian_user = ?) OR "
                         "(address IS NOT NULL AND address = ? COLLATE NOCASE) ORDER BY created_at, rowid",
                         (user_id, email)).fetchall()
    return offers, custody


def person_rows(db, person) -> list:
    """The operator's command, `account-record list`: each offer row and each custody
    row that names `person` (an email, or their user id in the deployment, that of an account removed since among it),
    as its teammate or custodian or by the address the owner typed, one dict each: {"table": "offer" or "custody", "id",
    "owner" (the owner's user id), "task_id" (an offer's; None for custody), "kind" (an offer's; None for custody),
    "state", "standing" (an offer not yet ended, or a custody request or custody not yet ended), "created_at",
    "updated_at", "ended_at" (custody's; None for an offer)}. A row whose person was cleared or reduced names them no
    more and is not listed."""
    offers, custody = _naming(db, person)
    return ([{"table": "offer", "id": r["id"], "owner": r["user_id"], "task_id": r["task_id"], "kind": r["kind"],
              "state": r["state"], "standing": r["state"] in OFFER_STANDING, "created_at": r["created_at"],
              "updated_at": r["updated_at"], "ended_at": None} for r in offers]
            + [{"table": "custody", "id": r["id"], "owner": r["owner_user"], "task_id": None, "kind": None,
                "state": r["state"], "standing": r["state"] in CUSTODY_STANDING, "created_at": r["created_at"],
                "updated_at": r["updated_at"], "ended_at": r["ended_at"]} for r in custody])


def reduce_person(db, person, now) -> dict:
    """The operator's command, `account-record reduce`: in every offer row and custody row that names `person` and has
    ended (an offer returned or ended without acceptance; a custody ended, declined or expired), set the person and the
    typed address to NULL (a custody row's reduced_at to `now`), the row's kind, state and times kept; and what would
    name them again by a join, the received task an offer names and the receiving agents a custody row lists. A row
    that still stands is left as it is, since its hand-off needs the person, and person_rows still lists it; but it is
    marked (reduce_requested_at), and the scheduler's round reduces it so once it has ended (forget: a row of an offer
    or a custody still standing is listed as standing, and reduced once it ends). Returns {"offers": n, "custody": n},
    the rows reduced now."""
    offers, custody = _naming(db, person)
    reduced = {"offers": 0, "custody": 0}
    for row in offers:
        if row["state"] not in OFFER_STANDING:
            db.execute("UPDATE ade_agent_task_offers SET teammate_user = NULL, address = NULL, their_task = NULL WHERE "
                       "id = ?", (row["id"],))
            reduced["offers"] += 1
        else:
            db.execute("UPDATE ade_agent_task_offers SET reduce_requested_at = COALESCE(reduce_requested_at, ?) WHERE "
                       "id = ?", (now, row["id"]))
    for row in custody:
        if row["state"] not in CUSTODY_STANDING:
            db.execute("UPDATE ade_task_custody SET custodian_user = NULL, address = NULL, agents = '[]', "
                       "reduced_at = ? WHERE id = ?", (now, row["id"]))
            reduced["custody"] += 1
        else:
            db.execute("UPDATE ade_task_custody SET reduce_requested_at = COALESCE(reduce_requested_at, ?) WHERE "
                       "id = ?", (now, row["id"]))
    return reduced


# ---- the routes -----------------------------------------------------------------------------------------------------

def offers(user, now=None):
    """GET /offers: the owner's offers, newest first, and those waiting for this user, each with point 13's text and
    the files a next run would load that it changes, once read; files given here are what an acceptance checks as
    shown; and the holds of this user's pushes a secret scan found something in, which they may lift (secret_holds,
    _secret_holds). The user's holds whose time has come are applied first, as tasks.py's reads apply them."""
    off = enabled(user)                 # it answers while the track is off too, saying why (_while_off)
    now = _now(now)
    holds.settle(user["id"], now)
    with store.connect() as db:
        made = [_offer_view(db, r) for r in db.execute(
            "SELECT * FROM ade_agent_task_offers WHERE user_id = ? AND state != 'draft' ORDER BY created_at DESC, "
            "rowid DESC LIMIT ?", (user["id"], MADE_LISTED)).fetchall()]
        mine = db.execute("SELECT * FROM ade_agents WHERE user_id = ? AND retired_at IS NULL ORDER BY created_at, id",
                          (user["id"],)).fetchall()
        received = []
        for row in db.execute("SELECT * FROM ade_agent_task_offers WHERE teammate_user = ? AND kind = 'pressed' AND "
                              "state = 'offered' AND expires_at > ? ORDER BY created_at, rowid",
                              (user["id"], now)).fetchall():
            task = tasks.get(db, row["user_id"], row["task_id"])
            on = _on_group(db, row["user_id"], task) if task is not None else None
            other = [a["id"] for a in mine
                     if (account := _account(db, user["id"], a["account"])) is not None and account["grp"] != on]
            view = _offer_view(db, row, received=True, other_group=other)
            received.append(view)
            if view["why"] != NOT_READ:             # the files as given to them: what an acceptance must find
                _shown[(row["id"], row["commit"])] = list(view["loaded_changes"])
        holds_now = _secret_holds(db, user["id"])
    _keep_running()
    return _while_off({"made": made, "received": received, "secret_holds": holds_now}, off, "received")


def custody(user, now=None):
    """GET /custody: the owner's custody, the requests and custody this user holds as custodian (each with point 13's
    text worded for custody), and the custody tasks they hold, each with Keep and Give back. The user's holds whose time
    has come are applied first, as tasks.py's reads apply them. It answers while the track is off too, saying why, its
    requests carrying no point 13's text then (_while_off)."""
    off = enabled(user)
    holds.settle(user["id"], _now(now))
    with store.connect() as db:
        mine = db.execute("SELECT * FROM ade_task_custody WHERE owner_user = ? AND confirmed_at IS NOT NULL ORDER BY "
                          "created_at DESC, rowid DESC LIMIT 1", (user["id"],)).fetchone()
        theirs = db.execute("SELECT * FROM ade_task_custody WHERE custodian_user = ? AND confirmed_at IS NOT NULL AND "
                            "state IN ('asked', 'accepted', 'suspended') ORDER BY created_at, rowid",
                            (user["id"],)).fetchall()
        held = []
        for row in db.execute("SELECT * FROM ade_agent_task_offers WHERE teammate_user = ? AND kind = 'custody' AND "
                              "state = 'accepted' AND their_task IS NOT NULL ORDER BY created_at, rowid",
                              (user["id"],)).fetchall():
            task = tasks.get(db, user["id"], row["their_task"])
            if task is not None and not task.get("deleted_at") and task["state"] not in ("done", "stopped"):
                held.append(_handoff_task(db, user["id"], row["their_task"]))
        out = {"as_owner": _custody_view(db, mine) if mine is not None else None,
               "as_custodian": [_custody_view(db, r, as_custodian=True) for r in theirs], "tasks": held}
    _keep_running()
    return _while_off(out, off, "as_custodian")


class OfferIn(BaseModel):
    task_id: str = Field(max_length=64)
    state_version: int
    address: str = Field(max_length=400)


class ConfirmIn(BaseModel):
    confirmation: Optional[str] = Field(default=None, max_length=4096)


class AcceptIn(BaseModel):
    agent_id: str = Field(pattern=r"^[0-9a-f]{12}$")
    adopt_checks: list[int] = Field(default_factory=list, max_length=shapes.STEPS_MAX)


class AddressIn(BaseModel):
    address: str = Field(max_length=400)


class CustodyAcceptIn(BaseModel):
    agents: list[str] = Field(max_length=20)
    confirmation: Optional[str] = Field(default=None, max_length=4096)


class VersionIn(BaseModel):
    state_version: int


class VersionConfirmIn(BaseModel):
    state_version: int
    confirmation: Optional[str] = Field(default=None, max_length=4096)


class LiftIn(BaseModel):
    findings: list[str] = Field(max_length=FINDINGS_MOST)
    confirmation: Optional[str] = Field(default=None, max_length=4096)


@router.get("/offers")
def route_offers(user=Depends(workspace_user)):
    return offers(user)


@router.post("/offers")
def route_make_offer(body: OfferIn, user=Depends(workspace_user)):
    return make_offer(user, body.task_id, body.state_version, body.address)


@router.post("/offers/{offer_id}/confirm")
def route_confirm_offer(offer_id: str, body: ConfirmIn, user=Depends(workspace_user)):
    return confirm_offer(user, offer_id, body.confirmation)


@router.post("/offers/{offer_id}/withdraw")
def route_withdraw(offer_id: str, user=Depends(workspace_user)):
    return withdraw(user, offer_id)


@router.post("/offers/{offer_id}/accept")
def route_accept(offer_id: str, body: AcceptIn, user=Depends(workspace_user)):
    return accept(user, offer_id, body.agent_id, body.adopt_checks)


@router.post("/offers/{offer_id}/decline")
def route_decline(offer_id: str, user=Depends(workspace_user)):
    return decline(user, offer_id)


@router.post("/tasks/{task_id}/take-back")
def route_take_back(task_id: str, user=Depends(workspace_user)):
    return take_back(user, task_id)


@router.get("/custody")
def route_custody(user=Depends(workspace_user)):
    return custody(user)


@router.post("/custody")
def route_ask_custody(body: AddressIn, user=Depends(workspace_user)):
    return ask_custody(user, body.address)


@router.post("/custody/{custody_id}/confirm")
def route_confirm_custody(custody_id: str, body: ConfirmIn, user=Depends(workspace_user)):
    return confirm_custody(user, custody_id, body.confirmation)


@router.post("/custody/{custody_id}/accept")
def route_accept_custody(custody_id: str, body: CustodyAcceptIn, user=Depends(workspace_user)):
    return accept_custody(user, custody_id, body.agents, body.confirmation)


@router.post("/custody/{custody_id}/decline")
def route_decline_custody(custody_id: str, user=Depends(workspace_user)):
    return decline_custody(user, custody_id)


@router.post("/custody/{custody_id}/end")
def route_end_custody(custody_id: str, user=Depends(workspace_user)):
    return end_custody(user, custody_id)


@router.post("/tasks/{task_id}/custody-confirm")
def route_confirm_for_custody(task_id: str, body: VersionConfirmIn, user=Depends(workspace_user)):
    return confirm_for_custody(user, task_id, body.state_version, body.confirmation)


@router.post("/tasks/{task_id}/custody-cross")
def route_custody_cross(task_id: str, body: VersionConfirmIn, user=Depends(workspace_user)):
    return custody_cross(user, task_id, body.state_version, body.confirmation)


@router.post("/tasks/{task_id}/secret-lift")
def route_lift_secret(task_id: str, body: LiftIn, user=Depends(workspace_user)):
    return lift_secret(user, task_id, body.findings, body.confirmation)


@router.post("/tasks/{task_id}/keep")
def route_keep(task_id: str, body: VersionIn, user=Depends(workspace_user)):
    return keep(user, task_id, body.state_version)


@router.post("/tasks/{task_id}/give-back")
def route_give_back(task_id: str, body: VersionIn, user=Depends(workspace_user)):
    return give_back(user, task_id, body.state_version)


holds.register(HOLD_OFFER, _offer_hold_ended, _offer_hold_cancelled, setting=HOLD_SETTINGS[HOLD_OFFER],
               default=holds.DEFAULT_DELAY, unsigned=True, released=_offer_hold_broke, notice=_offer_notice)
holds.register(HOLD_CROSSING, _crossing_hold_ended, setting=HOLD_SETTINGS[HOLD_CROSSING], default=holds.DEFAULT_DELAY,
               unsigned=True, notice=_crossing_notice)
