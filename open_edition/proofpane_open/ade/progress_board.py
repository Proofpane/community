"""The live progress board: the person's work as rows, a row a task, a requirement or a session, each in one of three
phases, history, now and next, for the page to read every few seconds while the board is open.

    GET /api/ade/board    {at, rows: [row]}

A row (the controller's rows are in the same shape):

    {kind, id, title, parent, phase, state, order, progress, account, session, created_at, started_at, ended_at,
     waits_until, events: [{at, kind, words}], more_events, grant}

Times are Unix seconds, and a missing value is null, never left out. The rows come newest change first within each
phase, the phases in the order history, now, next; a row's change is the newest of its own times and its events'.

What the board holds:
- each of the person's durable tasks (ade/tasks.py) not deleted: its title as the owner gave it, its state, its steps
  counted (`progress`, done of all), the account it is on (`account`, null on a model key), its times (`started_at`
  its first attempt's start; `ended_at` once it is in history; `waits_until` below), and the newest EVENTS_MAX
  events of its journal, oldest first, the rest counted (its grant among them, ade/task_grants.py: each version its
  owner signed with the head of its digest, each ask for more in it, each refusal); and its grant as it stands
  (`grant`, as task_grants.grant_view reads it: {version, digest, ask_current}, the version that stands, the first 12
  hex digits of its digest, and whether it pins the task's goal as it is now; each null where every version was
  withdrawn; the whole null for a task with no grant), which no number of newer events drops. `grant` is the board's
  own, beside the other fields: a source's row may leave it out, and is null there. A task's attempts run as an
  agent's runs, not in a workspace session, so a task's row names no session; its current attempt is in its newest
  events ("Step 2, attempt 1 started on Default");
- each of the workspace's sessions that no other row of the board names in its `session`, as the list of sessions shows
  it: its title, its state as the page's store.ts sessionState reads it from the session's record (so the board and the
  list agree; session_state says on what), the account it runs on, and its times; a closed session and a sign-in left
  out, as the list leaves them out. Its title is the session's own, as the list shows it: the person's name for it, else
  its task's first line, which for a session started from an agent's ask is the line that agent asked with. They are
  read from the workspace's own route, as the summary reads them (summary.py), and waited for at most SESSIONS_WAIT
  seconds: a read not done by then, or one that failed in its call to the workspace, stands in with the last one that
  did not fail if that ended in the last LATE_KEPT seconds, and with none otherwise; a slow read goes on in its thread,
  which the next board waits on rather than asking again. A workspace that does not run, or a deployment that gives the
  account none, gives no sessions, and its last read stands in no more;
- the rows other modules add through add_rows: the controller adds its requirements and the sessions it lists, under
  the name `controller` (controller.board_source), each session's row holding its workspace session.

The phases: history is what ended (a task done or stopped; a session done, failed or stopped). Next is what
waits to start or to go on without its owner, in its order: an attempt queued that has not started, which its agent
sends (once its pause ends, where it is paused until a time, and the workspace runs: agents._send_now sends to a running
workspace alone, and a pause's end starts none, so such an attempt waits after that time for whatever starts the
workspace) and the workspace starts in its turn; a task being offered to a teammate (its offer held, then waiting for
their answer); and a task waiting for a known time at which the app goes on with it, before that time or after it: both
Auto-resume switches on, the deployment's and the task's, and nothing holding it back (held_back: its agent retired or
paused with no end, the task marked returned, an effect of it unknown), as tasks.py's _auto_resume decides it. An
attempt queued waits on its owner instead, in now, where its agent is retired, paused with no end or not in the store,
and, sent already, where its agent is paused at all: the app's retire stops an attempt not sent at once, and the
workspace's one sent; the workspace stops a sent attempt rather than start it while its agent is paused, which the app
tells it (proofpane_workspace's task_runs.start); and the task then waits for its owner, whose Continue is refused while
its agent is paused and for good once it is retired, End being its way out then (tasks._continue_after_end, _queue). Now
is the rest, what runs, here or with a teammate, or waits on its owner: among them a task made and not started (it waits
for Start), a task handed over to a teammate or coming back from one (the teammate's work, under way; the owner may take
it back), and one waiting for a known time that nothing will go on with.

A task's `waits_until`, in next, is the time it goes on: for an attempt queued, not sent, whose agent is paused until a
time, that time; for a task waiting for a known time (an account's reset, or midnight UTC where its agent's runs for the
day were spent), that time, or a later one that holds it, its agent's pause's end or midnight UTC where its agent's runs
for today are spent (tasks.py's _firing_hold). In now it is the time from which the owner's Continue is what the task
waits for, where Auto-resume is off and nothing else holds it back: its known time, or its agent's pause's end where
that is later (Continue is refused before it). Where something holds it back, Continue is refused while that holds, for
good where its agent is retired or not in the store or the task is marked returned (End is its way out then), and the
board gives no time. The board numbers its next rows once, 1 first (`order`, null in the other phases), in the order
they go on as far as it can tell: the rows whose time is known first, soonest first (an attempt queued as its agent
sends them, in the order queued, from its queueing; a task waiting, at its `waits_until`); then the rows carrying an
`order` of their source's queue, by that order; then the rest, oldest first, one whose making is not known last.

An event's words are this module's own, made from the journal's kinds, numbers and states, and from the owner's own
names for accounts: never a payload's free text (a reason, a check's output, a target), nor anything a model wrote. An
event out of shape is left out, said on standard error. Where the journal does not say what holds a task, the board adds
a line of its own from the store's records (_held_lines), in time order among the journal's events and dated no later
than the board's own time: what the task's record says it waits for, at the time the record last changed, where the
journal's newest wait says otherwise, since the app moves a task without a journal line in two places (the way back
holding a received task, an offer's end putting the owner's task back); its agent retired, at its retirement, for a task
in its owner's hands; and its agent paused, at the agent's last change, for an attempt queued that waits on its owner.
They say what holds the task now, so they are gone once nothing does, and a task in history has none. A row has a
task's events from its journal alone, what happened: these lines widen it, and are named by kinds the journal does not
have (task.waits, agent.retired, agent.paused). The board writes nothing in the app's store: the scheduler's round
(tasks.sweep) settles what has ended, and a read never waits on that. Its read of the workspace's sessions is the list's
own read, and does what that read does (a teleported session is taken over and named after its branch).
"""

import concurrent.futures
import json
import re
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import store
from ..deps import current_user
from . import agents, task_offers, tasks, workspace_client, workspace_routes, workspaces

router = APIRouter(prefix="/api/ade/board")
NO_STORE = {"Cache-Control": "no-store"}

KINDS = ("task", "requirement", "session")
PHASES = ("history", "now", "next")
FIELDS = ("kind", "id", "title", "parent", "phase", "state", "order", "progress", "account", "session", "created_at",
          "started_at", "ended_at", "waits_until", "events", "more_events", "grant")
# the fields a source's row (add_rows) carries exactly: `grant`, a task row's of the board's own, it
# may leave out
SECTION_5 = FIELDS[:-1]
_GRANT_HEAD = re.compile(r"[0-9a-f]{12}")
EVENTS_MAX = 50             # events a row carries, the newest; the rest counted in more_events
TITLE_MOST = 200            # characters of a title the board carries
WORDS_MOST = 300            # characters of an event's words
NAME_MOST = 80              # characters of an account's name in an event's words
SESSIONS_WAIT = 2.0         # seconds a board waits for the workspace's sessions (the page asks every 3)
LATE_KEPT = 60.0            # seconds a read finished after its own board answered may stand in for a later one's
MOST_SECONDS = 253402300799 # 9999-12-31 23:59:59 UTC: the latest time both Python and a page's Date write

# each matched whole (fullmatch): a trailing line break is no part of a name
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_STATE = re.compile(r"[a-z][a-z_]{0,39}")
_EVENT_KIND = re.compile(r"[a-z][a-z_.]{0,59}")
_SESSION = re.compile(r"[0-9a-f]{12}")
_ACCOUNT_ID = re.compile(r"default|[0-9a-f]{12}")
ACCOUNT_KINDS = ("claude", "codex")

# the states in which a task is in its owner's hands, its Start, Continue or End theirs to press (_held_lines)
OWN_HANDS = ("ready", "running", "suspended", "needs_owner")
# a task's state in the history phase; next holds an offering one, a running one whose attempt has not started, and a
# suspended one the app goes on with at a known time (task_phase)
TASK_ENDED = ("done", "stopped")
# a session's state, as store.ts's sessionState gives it, in the history phase; every other one is now
SESSION_ENDED = ("done", "failed", "stopped")
SIGN_IN_PROGRAMS = ("github", "claude-sign-in", "codex-sign-in")   # store.ts's SIGN_IN_PROGRAMS

# the workspace's sessions not to be had: none runs, or the deployment gives the account none (its route's refusals,
# summary.py's list less the call that failed): no sessions on the board, and the last read stands in no more
_NOT_RUNNING = (HTTPException, workspaces.WorkspaceAccess)
_reads_lock = threading.Lock()
_reads = {}                 # account id: the Future of its sessions' read under way, or of the last one
_last_read = {}             # account id: (when, sessions) of its last read that did not fail

# the modules that add rows: name -> function (user, now) -> [row]
_SOURCES = {}


# ---- a row ----------------------------------------------------------------------------------------------------------

def _seconds(value, what):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MOST_SECONDS:
        raise ValueError(f"{what} is Unix seconds up to the year 9999, or null")
    return value


def _time(value):
    """A time a session's record carries, which its programs can write: whole seconds up to MOST_SECONDS, else
    none."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MOST_SECONDS else None


def _count(value, what):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{what} is a whole number")
    return value


def _cut(text, most):
    text = " ".join(str(text).split())
    return text if len(text) <= most else text[:most - 1].rstrip() + "\u2026"


def event(at, kind, words):
    """One event of a row: what happened at `at`, its kind, and the app's own words for it."""
    if not isinstance(kind, str) or not _EVENT_KIND.fullmatch(kind):
        raise ValueError("an event's kind is a lower-case name")
    if not isinstance(words, str):
        raise ValueError("an event's words are text")
    if _seconds(at, "an event's time") is None:
        raise ValueError("an event has a time")
    return {"at": at, "kind": kind, "words": _cut(words, WORDS_MOST)}


def row(kind, row_id, title, *, phase, state, parent=None, order=None, progress=None, account=None, session=None,
        created_at=None, started_at=None, ended_at=None, waits_until=None, events=(), more_events=0, grant=None):
    """One row in the board's shape, checked (ValueError for one out of shape): what a module that adds rows makes each
    of them with. `events`, oldest first, are cut to the newest EVENTS_MAX, the rest added to `more_events`; a title is
    cut to TITLE_MOST characters and an event's words to WORDS_MOST. `order` is a next row's place in its source's own
    queue, 1 first. `waits_until` is a next row's time to go on, and a now row's time from which its owner's press is
    what it waits for. The board numbers every next row once (arranged): the rows with a known time first, whatever
    their order, then the rows carrying an order, by it (one source's 2 after another's 1), then the rest; so a
    source's order holds among its own rows that have no known time. `grant` is a task row's grant as it stands
    (_grant), or null."""
    if kind not in KINDS:
        raise ValueError(f"a row's kind is one of {', '.join(KINDS)}")
    if phase not in PHASES:
        raise ValueError(f"a row's phase is one of {', '.join(PHASES)}")
    if not isinstance(row_id, str) or not _ID.fullmatch(row_id):
        raise ValueError("a row's id is a short name")
    if parent is not None and (not isinstance(parent, str) or not _ID.fullmatch(parent)):
        raise ValueError("a row's parent is another row's id, or null")
    if not isinstance(title, str):
        raise ValueError("a row's title is text")
    if not isinstance(state, str) or not _STATE.fullmatch(state):
        raise ValueError("a row's state is a lower-case name")
    if order is not None and (isinstance(order, bool) or not isinstance(order, int) or order < 1):
        raise ValueError("a row's order is 1 or more, or null")
    if progress is not None:
        if not isinstance(progress, dict) or set(progress) != {"done", "of"}:
            raise ValueError("a row's progress is {done, of}, or null")
        if _count(progress["done"], "done") > _count(progress["of"], "of"):
            raise ValueError("a row's progress has no more done than there are")
        progress = {"done": progress["done"], "of": progress["of"]}
    if account is not None:
        if not isinstance(account, dict) or set(account) != {"kind", "id"} or account["kind"] not in ACCOUNT_KINDS \
                or not isinstance(account["id"], str) or not _ACCOUNT_ID.fullmatch(account["id"]):
            raise ValueError("a row's account is {kind: claude or codex, id: default or an account's id}, or null")
        account = {"kind": account["kind"], "id": account["id"]}
    if session is not None and (not isinstance(session, str) or not _SESSION.fullmatch(session)):
        raise ValueError("a row's session is a workspace session's id, or null")
    given = []
    for e in events or ():
        if not isinstance(e, dict) or set(e) != {"at", "kind", "words"}:
            raise ValueError("an event is {at, kind, words}")
        given.append(event(e["at"], e["kind"], e["words"]))
    kept = given[-EVENTS_MAX:]
    grant = _grant(kind, grant)
    return {"kind": kind, "id": row_id, "title": _cut(title, TITLE_MOST), "parent": parent, "phase": phase,
            "state": state, "order": order if phase == "next" else None, "progress": progress, "account": account,
            "session": session, "created_at": _seconds(created_at, "created_at"),
            "started_at": _seconds(started_at, "started_at"), "ended_at": _seconds(ended_at, "ended_at"),
            "waits_until": _seconds(waits_until, "waits_until"), "events": kept,
            "more_events": _count(more_events, "more_events") + len(given) - len(kept), "grant": grant}


def _grant(kind, grant):
    """A row's `grant`, checked: null, or, on a task's row, {version, digest, ask_current}, each null together (no
    version stands), or a version from 1, the first 12 hex digits of its digest, and whether it pins the goal as it
    stands."""
    if grant is None:
        return None
    if kind != "task" or not isinstance(grant, dict) or set(grant) != {"version", "digest", "ask_current"}:
        raise ValueError("a row's grant is a task's {version, digest, ask_current}, or null")
    version, head, current = grant["version"], grant["digest"], grant["ask_current"]
    if (version, head, current) == (None, None, None):
        return {"version": None, "digest": None, "ask_current": None}
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 2 ** 53 - 1 \
            or not isinstance(head, str) or not _GRANT_HEAD.fullmatch(head) or not isinstance(current, bool):
        raise ValueError("a row's grant names a version from 1, its digest's first 12 hex digits and whether it pins "
                         "the goal as it stands, or none of the three")
    return {"version": version, "digest": head, "ask_current": current}


def _checked(given):
    """A row a source gave, as row() makes it: ValueError for one without the board's fields, exactly, beside the
    board's own `grant` or without it, or out of shape."""
    if not isinstance(given, dict) or set(given) - {"grant"} != set(SECTION_5):
        raise ValueError("a row has the board's fields, exactly")
    return row(given["kind"], given["id"], given["title"], phase=given["phase"], state=given["state"],
               parent=given["parent"], order=given["order"], progress=given["progress"], account=given["account"],
               session=given["session"], created_at=given["created_at"], started_at=given["started_at"],
               ended_at=given["ended_at"], waits_until=given["waits_until"], events=given["events"],
               more_events=given["more_events"], grant=given.get("grant"))


def add_rows(name, source):
    """Add a module's rows to every board: `source(user, now)` returns a list of rows, each made with row(), of `user`'s
    own work alone (the board shows to each person what the source gives for that person). Named, so that adding it
    again replaces it. A source that raises, or a row of its that is out of shape, is said on standard
    error and left out: the board answers with the rest. A source's row naming a workspace session in `session` holds
    it, and the board lists that session as no row of its own."""
    if not isinstance(name, str) or not name or not callable(source):
        raise TypeError("add_rows(name, source): a name, and a function (user, now) -> rows")
    _SOURCES[name] = source


# ---- tasks ----------------------------------------------------------------------------------------------------------

_PROVIDERS = {"anthropic": "Anthropic", "kimi": "Kimi", "minimax": "MiniMax", "deepseek": "DeepSeek"}
_CHOSEN_BY = {"owner": "at your press", "relay": "by the relay", "auto_resume": "by Auto-resume",
              "next_step": "for its next step", "custody": "by custody", "return": "on its way back"}
_RUN_END = {"done": "finished", "failed": "failed", "blocked": "was held", "stopped": "was stopped",
            "skipped": "was skipped"}
_NEEDS = {"effect": "a command's effect is not known", "worktree": "its worktree is not as recorded",
          "branch": "its branch moved", "resume": "its attempt could not go on", "settings": "a setting named another "
          "credential", "watch": "a watched place changed", "window": "no account it may take has a known reset",
          "secret": "its text holds something shaped like a secret", "identity": "its account's identity changed",
          "moves": "it moved too often in an hour", "handoff": "a hand-off", "packet": "its packet is past its bound",
          "ungated": "a call ran outside the gate", "returned": "your teammate's work came back, to look at first"}
# `returned` on a task received from a teammate (one marked returned) means the other way (tasks.py's _hold_returned),
# and Continue is refused for it whatever it waits for, so End is its way out (tasks.py's _may_continue)
RETURNED_RECEIVED = "it went back to the teammate who handed it to you: End it"
# task.returned, journaled as the way back starts (task_offers._start_way_back): custody's own return, one of the
# owner's own accounts having room again (its `why` task_offers.CUSTODY_RETURN, compared, never shown); else the
# teammate's Give back, or the end of their side (their task ended, their agent retired, their account gone), which
# its payload does not tell apart
CUSTODY_BACK = "Custody started it back: one of your own accounts has room again"
ON_ITS_WAY_BACK = "On its way back from your teammate"
# `step_done` is also a task's back from a teammate whether or not a step came back done (tasks.take_return); and
# `stopped`, of its eleven uses, follows a stop in four (a Stop pressed; an attempt that ended stopped; one stopped, or
# cancelled before it was sent, for a change to its task) and in seven none (an attempt that ran on its old
# instructions, a continuation refused, an offer asked for at an attempt's end, a firing held for an agent paused or
# retired or a day's runs spent with Auto-resume off: tasks._continue_after_end, _guarded, _go_asked,
# _held_continuation; an offer's end on a task that had never waited: task_offers._suspended_as). Their reasons, free
# text, tell these apart, and the board reads none, so its words name neither a step done nor a stop
_SUSPENDED = {"step_done": "It waits for you before it goes on",
              "no_step": "Its attempt ended without finishing its step", "failed": "Its attempt failed",
              "stopped": "It waits for you"}
# a task suspended for its window: with a time, an account's reset or, its agent's runs for the day spent, midnight UTC
# (tasks.py's _wait_for_reset and _held_continuation, whose journal entries are the same); with none, its accounts out
# of room or held back by a weekly reserve, and Auto-resume off (HELD_BY_A_RESERVE)
WINDOW_UNTIL = ("Suspended until a known time: an account's reset, or midnight UTC once its agent's runs for the day "
                "are spent")
WINDOW_HELD = "Held until you press Continue: its accounts are out of room or held back by a weekly reserve"
# the board's own line for a record waiting for its window at a time no later than its change (_held_lines)
WINDOW_DUE = "Suspended for its window, its time to go on already come"
# the task's switches (tasks.py's switches; custody's crossing, task_offers.custody_cross), and their holds: turning
# on Cross organisations or custody's crossing is held first, and the switch reads off until its hold ends
_SWITCHES = {"relay": "Relay", "auto_resume": "Auto-resume", "cross_groups": "Cross organisations",
             "custody_cross": "Custody's crossing"}
_RESOLVED = {("worktree", "keep"): "You kept its worktree as it is",
             ("worktree", "discard"): "You dropped its worktree's change",
             # written once its last unknown effect is settled, with that one's outcome alone (tasks.resolve)
             ("effect", "happened"): "Every unknown effect of it settled",
             ("effect", "not_happened"): "Every unknown effect of it settled",
             ("window", "lift"): "You lifted its window", ("watch", "lift"): "You lifted its watch",
             ("secret", "lift"): "You lifted its secret finding"}
_DONE_BY = {"checkpoint": "Done: its last step's checkpoint", "owner": "Ended as done, by you",
            "return": "Done: it came back done"}
# task.bound: (bound to an organisation, its crossings ended) -> its words; the page says of a task whose crossings
# ended that "it crosses nowhere by itself" (AgentTask.tsx)
_BOUND = {(True, True): "Bound to its organisation, and its crossings ended: it crosses nowhere by itself",
          (True, False): "Bound to its organisation",
          (False, True): "Its crossings ended: it crosses nowhere by itself",
          (False, False): "Its binding changed"}
# a task's grant (ade/task_grants.py): who asked for a version the owner signed, and who asked for more in it; a
# version's digest is said by its first twelve hex digits, read only where it is 64 of them
_GRANT_DIGEST = re.compile(r"[0-9a-f]{64}")
_GRANT_ASKER = {"agent": ", as its agent asked", "link": ", as a linked session asked"}
_GRANT_ASKED = {"agent": "Its agent", "link": "A linked session"}
# why versions of its grant were withdrawn (task_grants.passkeys_going), of one version and of several
_GRANT_WITHDRAWN = {"removed": ("the passkey that signed it was removed", "the passkey that signed them was removed"),
                    "high_security": ("high security was turned on, and no hardware key signed it",
                                      "high security was turned on, and no hardware key signed them")}
# the board's own lines, from the store's records, where the journal does not say what holds a task: a queued attempt
# whose agent is retired, or paused (_task_rows)
HELD_RETIRED = "Its agent was retired: this attempt does not run, and End is the task's way out"
RETIRED = "Its agent was retired: End is the task's way out"
# a resume, like a pause's end, starts no workspace (agents.set_paused, poll_all): an attempt not sent goes once
# its agent is resumed and the workspace runs
HELD_PAUSED = "Its agent is paused: this attempt is not sent while the pause lasts"
HELD_PAUSED_SENT = ("Its agent is paused: the workspace stops this attempt rather than start it while the pause lasts, "
                    "and the task then waits for you")
# the words of the journal's kinds that need no more than their kind (each of JOURNAL_KINDS has words here or below)
_PLAIN = {"checkpoint.rejected": "A checkpoint was refused: its attempt no longer held the task",
          "effect.unknown": "A command's effect is not known: say whether it happened",
          "task.stopped": "Ended, by you", "task.offered": "Offered to a teammate",
          "task.offer_cancelled": "Its offer was cancelled", "task.offer_declined": "Your teammate declined it",
          "task.offer_expired": "Its offer expired", "task.offer_failed": "Its offer failed",
          "task.taken_back": "You took it back",
          "task.secret_lifted": "A secret finding was lifted for its push",
          "task.secret_lift_used": "A push went out under a lifted finding", "custody.asked": "Custody asked",
          "custody.accepted": "Custody accepted", "custody.suspended": "Custody suspended",
          "custody.ended": "Custody ended"}


def _json(text):
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _backend(value):
    """A backend as a dict (BackendRef), from its JSON, its text ("account:<id>", "key:<provider>") or itself; None."""
    if isinstance(value, str) and not value.startswith("{"):
        m = re.fullmatch(r"(account|key):(.+)", value)
        if m is None:
            return None
        return {"kind": "account", "id": m[2]} if m[1] == "account" else {"kind": "key", "provider": m[2]}
    value = _json(value)
    return value if value.get("kind") in ("account", "key") else None


def _named(value, names):
    """A backend as the board names it: the owner's name for the account, Default for the program's own sign-in, the
    id of one the owner never named; or one of the person's model keys."""
    ref = _backend(value)
    if ref is None:
        return "an account"
    if ref["kind"] == "key":
        return f"your {_PROVIDERS.get(ref.get('provider'), 'model')} key"
    account = ref.get("id")
    if not isinstance(account, str) or not _ACCOUNT_ID.fullmatch(account):
        return "an account"
    name = names.get(account)
    if isinstance(name, str) and name.strip():
        return _cut(name, NAME_MOST)
    return "Default" if account == "default" else account


def _steps(n):
    return f"{n} step" if n == 1 else f"{n} steps"


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def task_words(kind, payload, run, names, returned=False):
    """An event of a task's journal in the board's words: `payload` its payload, `run` the run its event names (its
    step, its attempt within the step, and its backend) or None, `names` the owner's names for accounts, `returned`
    whether the task is one received from a teammate that went back to them. Fixed words with numbers, states and
    names in them; a kind with none of its own reads as its kind."""
    p = payload if isinstance(payload, dict) else {}
    # an attempt is counted within its step (tasks.py's _queue), so it is named with its step
    n, of_step = (_int(run.get("task_attempt")), _int(run.get("task_step"))) if run else (None, None)
    attempt = f"Step {of_step}, attempt {n}" if n and of_step else f"Attempt {n}" if n else "An attempt"
    if kind == "task.created":
        return f"Made, with {_steps(_int(p.get('steps')) or 0)}"
    if kind == "task.steps_changed":
        return f"Its steps changed: {_steps(_int(p.get('steps')) or 0)} now"
    if kind == "attempt.queued":
        by = _CHOSEN_BY.get(p.get("chosen_by"))
        return f"{attempt} queued on {_named(p.get('backend'), names)}" + (f", {by}" if by else "")
    if kind == "attempt.started":
        return f"{attempt} started on {_named(run.get('backend') if run else None, names)}"
    if kind == "attempt.ended":
        return f"{attempt} {_RUN_END.get(p.get('state'), 'ended')}"
    if kind == "resume.passed":
        return f"{attempt}: its resume check passed"
    if kind == "resume.blocked":
        return f"{attempt}: its resume check held it"
    if kind == "checkpoint.ready":
        step = _int(p.get("step"))
        if p.get("kind") == "zero":
            return "Its starting point recorded"
        if p.get("kind") == "step":
            return f"Step {step}: done, its checkpoint saved" if step else "A step done, its checkpoint saved"
        return f"Step {step}: progress saved" if step else "Progress saved"
    if kind == "effect.resolved":
        outcome = "it happened" if p.get("outcome") == "happened" else "it did not happen"
        by = " (by you)" if p.get("by") == "page" else " (by the workspace)" if p.get("by") == "workspace" else ""
        return f"An unknown effect resolved: {outcome}{by}"
    if kind == "task.suspended":
        cause = p.get("cause")
        if p.get("state") == "needs_owner":
            if cause == "returned" and returned:
                return "Waits for you: " + RETURNED_RECEIVED
            return "Waits for you: " + _NEEDS.get(cause, "a decision")
        if cause == "window":
            return WINDOW_UNTIL if _int(p.get("until")) else WINDOW_HELD
        return _SUSPENDED.get(cause, "Waits for Continue")
    if kind == "task.continued":
        by, to = p.get("by"), _named(p.get("to"), names)
        moved = _backend(p.get("from")) not in (None, _backend(p.get("to")))
        frm = _named(p.get("from"), names)
        if by == "relay":
            return f"The relay moved it from {frm} to {to}"
        if by == "auto_resume":
            return f"Auto-resume moved it from {frm} to {to}" if moved else f"Auto-resume continued it on {to}"
        if by == "next_step":
            return f"On to its next step, on {to}"
        if by == "custody":
            return f"Custody continued it on {to}"
        if by == "return":
            return f"Back from your teammate, on {to}"
        press = ", at your press" if by == "owner" else ""
        return f"Moved from {frm} to {to}{press}" if moved else f"Continued on {to}{press}"
    if kind == "task.crossed":
        # into its home group (Back home), into an organisation's seats, or into personal plans (tasks.py's _move)
        to = p.get("to_grp") if isinstance(p.get("to_grp"), str) else ""
        return "Back to its home group's accounts" if p.get("home") else \
            "It crossed into an organisation's seats" if to.startswith("org:") else \
            "It crossed into personal plans" if to == "personal" else "It crossed into another group's accounts"
    if kind == "task.account_regrouped":
        # its account confirmed into another group; its seat read as another kind in the same group; or its
        # organisation read again, the group and the kind as they were (tasks.py's confirmations and rereads)
        if p.get("from_grp") != p.get("to_grp"):
            return "Its account was confirmed into another group"
        if p.get("from_kind") != p.get("to_kind"):
            return "Its account's seat was read as another kind"
        return "Its organisation was read again"
    if kind == "task.resolved":
        return _RESOLVED.get((p.get("what"), p.get("outcome")), "You resolved what it waited for")
    if kind == "task.switched":
        name = _SWITCHES.get(p.get("switch"), "A switch")
        if p.get("on") is True and isinstance(p.get("hold"), str):
            # its hold may end with it on, or be cancelled, or be refused as it applies, none of which is journaled
            return f"{name} asked on: its turning on is held"
        return f"{name} turned {'on' if p.get('on') is True else 'off'}"
    if kind == "task.done":
        return _DONE_BY.get(p.get("by"), "Done")
    if kind == "task.handed_over":
        return "Handed over to your teammate"
    if kind == "task.returned":
        return CUSTODY_BACK if p.get("why") == task_offers.CUSTODY_RETURN else ON_ITS_WAY_BACK
    if kind == "task.bound":
        # the binding fields a custody hand-off or a way back changed (task_offers._bind_as_custody): bound to an
        # organisation, its crossings ended, or both
        bound = p.get("bound") if isinstance(p.get("bound"), dict) else {}
        org, ended = bool(bound.get("bound_org")), bound.get("crossings_ended") == 1
        return _BOUND[(org, ended)]
    if kind == "task.grant_signed":
        # a version of its grant (ade/task_grants.py): its number, the head of its digest, and who asked for it
        version, kept = _int(p.get("version")), p.get("digest")
        said = (f": version {version}" + (f", {kept[:12]}" if isinstance(kept, str) and _GRANT_DIGEST.fullmatch(kept)
                                          else "")) if version else ""
        return "Its grant signed with your passkey" + said + _GRANT_ASKER.get(p.get("by"), "")
    if kind == "task.grant_asked":
        who = _GRANT_ASKED.get(p.get("by"))
        return f"{who} asked for more in its grant: it waits for your passkey" if who else \
            "More in its grant was asked for: it waits for your passkey"
    if kind == "task.grant_refused":
        return "An ask for more in its grant was refused"
    if kind == "task.grant_ask_ended":
        # an ask that ended as the linked session that made it was revoked
        return "An ask for more in its grant ended" + (": the linked session that asked was revoked"
                                                        if p.get("why") == "revoked" else "")
    if kind == "task.grant_withdrawn":
        # versions withdrawn as the passkey that signed them went: the newest of them the
        # event names, and how many went
        given = p.get("versions") if isinstance(p.get("versions"), list) else []
        versions = [v for v in given if _int(v)]
        count = _int(p.get("count")) or len(versions)
        why = _GRANT_WITHDRAWN.get(p.get("why"))
        if count == 1 and len(versions) == 1:
            said = f"Its grant's version {versions[0]} was withdrawn"
        elif versions and count == len(versions):
            said = f"Its grant's versions {', '.join(map(str, versions[:-1]))} and {versions[-1]} were withdrawn"
        elif count > 1:
            said = f"{count} versions of its grant were withdrawn"
        else:
            said = "A version of its grant was withdrawn"
        return said + (f": {why[0] if count == 1 else why[1]}" if why else "")
    return _PLAIN.get(kind, kind)


def task_phase(task, queued=False, by_itself=True):
    """A task's phase (the module's docstring): `task` its row; `queued` whether its attempt is queued and not started;
    `by_itself` whether the app goes on with it at the known time it waits for (goes_on_by_itself)."""
    state = task["state"]
    if state in TASK_ENDED:
        return "history"
    if state == "offering" or (state == "running" and queued):
        return "next"
    if state == "suspended" and task["cause"] == "window" and task["waits_until"] is not None and by_itself:
        return "next"
    return "now"


def held_back(task, held, unknown):
    """What holds a task back from going on, by itself and at its owner's Continue alike, while it holds: its agent
    `held` (retired or not in the store, for good, End being the task's way out then: tasks.py's _queue; or paused
    with no time the pause ends, until its owner resumes it: _firing_hold and _paused_agent), the task marked returned
    (for good: _refuse_returned), or one of its effects `unknown`, until it is settled (_may_continue)."""
    return bool(held or task["returned"] or unknown)


def goes_on_by_itself(task, auto_resume, held, unknown):
    """Whether the app goes on with a task at the known time it waits for (as tasks.py's _auto_resume decides it; the
    agents panel says the same of each, but of an agent paused with no end that the task goes on once it is resumed):
    both Auto-resume switches on (`auto_resume` the deployment's), and nothing holding it back (held_back). A pause
    that ends at a time and the agent's runs for the day spent only make it later (_task_rows says when). This month's
    machine hours at their share hold it until the month turns or its owner continues it; they are not read here (a
    call to the workspace's provider), so such a task can stand in next."""
    return bool(auto_resume and task["auto_resume"]) and not held_back(task, held, unknown)


def _held_lines(task, said_wait, names, agent, held_attempt, sent, now):
    """The board's own lines saying what holds a task, each (at, kind, words), where its journal does not say it; none
    dated later than the board's own time, `now` (an agent's change time can run a second or more ahead of the clock,
    kept moving on by the store's trigger ade_agents_changed). They say what holds the task now: once nothing does, they
    are gone, and a task in history has none.
    - A task that waits, suspended or for its owner, whose journal's newest task.suspended event says otherwise
      (`said_wait`, its words, read from the whole journal, not only the events shown), or which has none: what its
      record says it waits for, in that event's words, at the time its record last changed (the app keeps no time of the
      change itself, so a later change that is not its wait, a switch turned say, moves it). The way back holds a
      received task for its user (task_offers._start_way_back, and the way back's end), and an offer's end puts the
      owner's task back (task_offers._back), with tasks.set_state, which writes no journal line. A record waiting for
      its window at a time no later than that change, which only a custody hand-off's end writes
      (task_offers._wait_again: an account open, or none with a reset known), says that its time has come, not that it
      waits for a reset.
    - A task in its owner's hands (made, running, suspended or waiting for them) whose agent is retired: that, at its
      retirement, End being its way out (tasks._queue refuses Start and Continue for good).
    - An attempt queued that waits on its owner for its agent's pause (`held_attempt`; `sent` whether it was sent):
      that, at the agent's last change.
    None for an agent the store does not have (`agent` None): there is no record of it to read."""
    lines = []
    if task["state"] in ("suspended", "needs_owner"):
        words = task_words("task.suspended", {"state": task["state"], "cause": task["cause"],
                                              "until": task["waits_until"]}, None, names,
                           returned=bool(task["returned"]))
        if said_wait != _cut(words, WORDS_MOST):
            until, changed = _int(task["waits_until"]), _int(task["updated_at"])
            if task["state"] == "suspended" and task["cause"] == "window" and until is not None and \
                    changed is not None and until <= changed:
                words = WINDOW_DUE
            lines.append((task["updated_at"], "task.waits", words))
    if agent is not None and task["state"] in OWN_HANDS and agent["retired_at"] is not None:
        lines.append((agent["retired_at"], "agent.retired", HELD_RETIRED if held_attempt else RETIRED))
    elif agent is not None and held_attempt:
        lines.append((agent["updated_at"], "agent.paused", HELD_PAUSED_SENT if sent else HELD_PAUSED))
    # a time out of shape is left as it is, for event() to refuse
    return [(min(at, now) if _int(at) is not None else at, kind, words) for at, kind, words in lines]


def _task_grant(db, task, now):
    """A task's row's `grant`: its grant as task_grants.grant_view reads it, the version that stands, the head of its
    digest and whether it pins the goal as it stands (each None where every version was withdrawn); None for a task
    with no grant."""
    from . import task_grants
    g = task_grants.grant_view(db, task, now)
    if g is None:
        return None
    if g["version"] is None:
        return {"version": None, "digest": None, "ask_current": None}
    return {"version": g["version"], "digest": g["digest"][:12], "ask_current": g["ask_current"]}


def _task_rows(db, user_id, now, when):
    """The person's tasks as rows. `when` gets, for each one in next whose time to go on the board can tell, that time
    (arranged's queue): an attempt queued, its queueing, or its agent's pause's end where the attempt waits for that;
    a task waiting for a known time, that time or a later one that holds it (its agent's pause's end, midnight UTC
    where its agent's runs for today are spent), which is also its `waits_until`."""
    found = db.execute("SELECT * FROM ade_agent_tasks WHERE user_id = ? AND deleted_at IS NULL",
                       (user_id,)).fetchall()
    if not found:
        return []
    steps = {r["task_id"]: (r["done"] or 0, r["total"]) for r in db.execute(
        "SELECT task_id, SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END) AS done, COUNT(*) AS total FROM "
        "ade_agent_task_steps WHERE user_id = ? GROUP BY task_id", (user_id,))}
    runs = {r["run_id"]: dict(r) for r in db.execute(
        "SELECT run_id, task_id, task_step, task_attempt, backend, state, sent, queued_at, started_at FROM "
        "ade_agent_runs WHERE user_id = ? AND task_id IS NOT NULL", (user_id,))}
    first = {}
    for r in runs.values():
        if _int(r["started_at"]) is not None:
            first[r["task_id"]] = min(first.get(r["task_id"], r["started_at"]), r["started_at"])
    events, counts = {}, {}
    # each task's newest EVENTS_MAX events, oldest first, and how many it has: one read for every task
    for e in db.execute(
            "SELECT seq, task_id, kind, run_id, at, payload, total FROM (SELECT seq, task_id, kind, run_id, at, "
            "payload, ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY seq DESC) AS newest, COUNT(*) OVER (PARTITION "
            "BY task_id) AS total FROM ade_agent_task_events WHERE user_id = ? AND task_id IS NOT NULL) WHERE newest "
            "<= ? ORDER BY task_id, seq", (user_id, EVENTS_MAX)):
        events.setdefault(e["task_id"], []).append(e)
        counts[e["task_id"]] = e["total"]
    # each task's newest wait in its whole journal, shown or not, which _held_lines compares its record with
    newest_wait = {r["task_id"]: r["payload"] for r in db.execute(
        "SELECT task_id, payload FROM (SELECT task_id, payload, ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY seq "
        "DESC) AS newest FROM ade_agent_task_events WHERE user_id = ? AND task_id IS NOT NULL AND kind = "
        "'task.suspended') WHERE newest = 1", (user_id,))}
    names = {r["account"]: r["name"] for r in db.execute("SELECT account, name FROM ade_account_owners WHERE "
                                                         "user_id = ?", (user_id,))}
    # each agent as tasks.py's _firing_hold reads it: retired, or paused with no time the pause ends, it holds its
    # tasks back (held_back), and a missing one too; paused until a time ahead, or its runs for today spent, it only
    # makes them later (agents._paused, agents.limits_of; agents._send_now sends no queued run of a paused agent)
    day = now - now % tasks.DAY
    today = {r["agent_id"]: r["n"] for r in db.execute(
        "SELECT agent_id, COUNT(*) AS n FROM ade_agent_runs WHERE user_id = ? AND loop = 0 AND queued_at >= ? GROUP BY "
        "agent_id", (user_id, day))}
    agents_found, held, pause_ends, day_spent = {}, set(), {}, {}
    for a in db.execute("SELECT id, retired_at, paused, paused_until, limits, updated_at FROM ade_agents WHERE "
                        "user_id = ?", (user_id,)):
        agents_found[a["id"]] = a
        if a["retired_at"] is not None or (a["paused"] and not a["paused_until"]):
            held.add(a["id"])
            continue
        if a["paused"] and _int(a["paused_until"]) is not None and a["paused_until"] > now:
            pause_ends[a["id"]] = a["paused_until"]
        if today.get(a["id"], 0) >= agents.limits_of(a)["max_runs_per_day"]:
            day_spent[a["id"]] = day + tasks.DAY
    unknown = {r["task_id"] for r in db.execute("SELECT DISTINCT task_id FROM ade_agent_effects WHERE user_id = ? AND "
                                                "state = 'unknown'", (user_id,))}
    auto_resume = tasks.on("auto_resume")
    out = []
    for t in found:
        lease = runs.get(t["lease_run"]) if t["lease_run"] else None
        agent = t["agent_id"]
        agent_held = agent in held or agent not in agents_found
        pause_end = pause_ends.get(agent)
        is_unknown = t["id"] in unknown
        # an attempt queued and not started, which goes on without its owner: one not sent its agent sends, at once or
        # once its pause ends (agents._send_now); one sent the workspace starts in its turn, unless its agent is paused
        # then, which the app tells it (agents._mirror_now), or retired, after which its retire stops it at once: the
        # task then waits for its owner (proofpane_workspace's task_runs.start, tasks._continue_after_end). Either waits
        # on its owner where its agent is retired, paused with no end or not in the store, and a sent one where its
        # agent is paused at all
        waiting = t["state"] == "running" and lease is not None and lease["state"] == "queued"
        unsent = waiting and not lease["sent"]
        queued = waiting and not agent_held and (unsent or pause_end is None)
        phase = task_phase(t, queued, goes_on_by_itself(t, auto_resume, agent_held, is_unknown))
        waits = t["waits_until"] if t["state"] == "suspended" and t["cause"] == "window" else None
        goes = None
        if phase == "next" and queued:
            # it goes as its agent sends it, in the order queued, or once its agent's pause ends
            waits = pause_end if unsent else None
            goes = waits if waits is not None else _int(lease["queued_at"])
        elif phase == "next" and waits is not None:
            goes = waits = max(waits, pause_end or 0, day_spent.get(agent, 0))
        elif waits is not None and not held_back(t, agent_held, is_unknown):
            # in now, the time from which the owner's Continue is what it waits for: Auto-resume off, the one thing
            # between it and going on by itself; not before its agent's pause ends, which refuses Continue
            # (_paused_agent). Where something else holds it (held_back), Continue is refused while that holds, for
            # some of them for good, and the board gives no time
            waits = max(waits, pause_end or 0)
        else:
            waits = None
        if goes is not None:
            when[("task", t["id"])] = goes
        done, of = steps.get(t["id"], (0, 0))
        backend = _backend(t["backend"])
        said = []
        for e in events.get(t["id"], ()):
            payload = _json(e["payload"])
            # the run an event is about: its own column, else its payload's (attempt.queued names it there too)
            key = e["run_id"] or (payload.get("run") if isinstance(payload.get("run"), str) else None)
            try:
                said.append(event(e["at"], e["kind"], task_words(e["kind"], payload, runs.get(key) if key else None,
                                                                 names, returned=bool(t["returned"]))))
            except ValueError as err:    # an event out of shape: said, and left out of the row
                print(f"board: an event of task {t['id']} could not be shown: {err}", file=sys.stderr)
        newest = newest_wait.get(t["id"])
        said_wait = None if newest is None else _cut(task_words("task.suspended", _json(newest), None, names,
                                                                returned=bool(t["returned"])), WORDS_MOST)
        for line in _held_lines(t, said_wait, names, agents_found.get(agent), waiting and not queued,
                                bool(lease and lease["sent"]), now):
            try:
                held_line = event(*line)
                i = len(said)                   # in time order, after the journal's events of its second
                while i and said[i - 1]["at"] > held_line["at"]:
                    i -= 1
                said.insert(i, held_line)
            except ValueError as err:
                print(f"board: what holds task {t['id']} could not be shown: {err}", file=sys.stderr)
        try:
            out.append(row("task", t["id"], t["title"], phase=phase, state=t["state"],
                           progress={"done": min(done, of), "of": of} if of else None,
                           account={"kind": "claude", "id": backend["id"]} if backend and backend["kind"] == "account"
                           and isinstance(backend.get("id"), str) and _ACCOUNT_ID.fullmatch(backend["id"]) else None,
                           created_at=t["created_at"], started_at=first.get(t["id"]),
                           ended_at=t["ended_at"] if phase == "history" else None,
                           waits_until=waits,
                           events=said, more_events=max(0, counts.get(t["id"], 0) - len(events.get(t["id"], ()))),
                           grant=_task_grant(db, t, now)))
        except ValueError as e:      # a row the store holds out of shape: said, and the board answers with the rest
            print(f"board: task {t['id']} could not be shown: {e}", file=sys.stderr)
    return out


# ---- sessions -------------------------------------------------------------------------------------------------------

def _read_sessions(user, future):
    """The workspace's sessions, read as their own route reads them, in a thread of their own: the list, or what it
    raised, in `future`."""
    try:
        got = workspace_routes.sessions(user)
    except _NOT_RUNNING as e:
        with _reads_lock:
            _last_read.pop(user["id"], None)     # a workspace that stopped meanwhile has no sessions to stand in
        future.set_exception(e)
        return
    except workspace_client.WorkspaceCallError as e:    # a call that failed: the last read stands in, as for a slow one
        future.set_exception(e)
        return
    except Exception as e:  # noqa: BLE001  ours, not the workspace's: said here, the board answers without them
        print(f"board: the workspace's sessions could not be read: {type(e).__name__}", file=sys.stderr)
        future.set_exception(e)
        return
    sessions = got.get("sessions") if isinstance(got, dict) else None
    if not isinstance(sessions, list):
        future.set_exception(ValueError("the workspace's sessions are not a list"))
        return
    with _reads_lock:
        _last_read[user["id"]] = (time.monotonic(), sessions)
    future.set_result(sessions)


def _sessions(user):
    """The workspace's sessions, as the module's docstring says: [] when none can be read now."""
    with _reads_lock:
        future = _reads.get(user["id"])
        if future is None or future.done():
            future = _reads[user["id"]] = concurrent.futures.Future()
            threading.Thread(target=_read_sessions, args=(user, future), name="board-sessions", daemon=True).start()
    try:
        return future.result(timeout=SESSIONS_WAIT)
    except (concurrent.futures.TimeoutError, workspace_client.WorkspaceCallError):
        with _reads_lock:
            last = _last_read.get(user["id"])
        return last[1] if last is not None and time.monotonic() - last[0] <= LATE_KEPT else []
    except Exception:  # noqa: BLE001  not read (the workspace stopped, or none): no sessions
        return []


def _truthy(value):
    """A value as JavaScript reads it in a condition, as store.ts tests a record's fields: false for null, false, 0,
    NaN and the empty string, true for anything else, an empty object or list among them (where Python reads those as
    false)."""
    if value is None or value is False or (isinstance(value, str) and not value):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == value and value != 0          # NaN and zero are false
    return True


def _length(value):
    """JavaScript's `x?.length ?? 0` for a list, text or an object: a list's or a text's length, an object's own
    `length` where that is a number, else 0."""
    if isinstance(value, (list, str)):
        return len(value)
    if isinstance(value, dict):
        n = value.get("length")
        return n if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
    return 0


def session_state(r):
    """A session's state from its record, as the page's store.ts sessionState reads it: the same cases in the same
    order, a field's truth read as JavaScript reads it (_truthy), so the board and the list of sessions agree on every
    record of the shapes the workspace writes, and where a session's program wrote an empty object, list or text for
    one of them (tests/fixtures/session-states.json holds both sides to such cases). Where a program wrote another kind
    of value (an exit code as text, say), JavaScript's coercions are not followed: the board may read it otherwise."""
    if r.get("program") == "chat":
        chat = r.get("chat") if isinstance(r.get("chat"), dict) else {}
        s = chat.get("state")
        if s == "running":
            return "working"
        if s == "needs_you" or _length(chat.get("asks")) > 0:
            return "waiting"
        if _truthy(r.get("finished")):
            return "done"
        if s == "error":
            return "failed"
        return "idle"
    if _truthy(r.get("running")):
        activity = r.get("activity") if isinstance(r.get("activity"), dict) else {}
        a = activity.get("state")
        return a if a in ("working", "waiting", "idle") else "running"
    if _truthy(r.get("finished")):
        return "done"
    code = r.get("exit_code")
    if code is not None:
        number = isinstance(code, (int, float)) and not isinstance(code, bool)
        return "done" if number and code == 0 else "stopped" if number and code < 0 else "failed"
    return "stopped"


def _is_sign_in(r):
    """store.ts's isSignIn: a sign-in the workspace started, or one that has ended."""
    return r.get("program") in SIGN_IN_PROGRAMS and (r.get("sign_in") is True or not _truthy(r.get("running")))


def program_title(program):
    """A session's title from its program alone, as the list shows a session with no name of its own and no task:
    a shell's "Shell", another program's its name. The controller titles its rows of a session with no repository and
    no branch by it (controller._session_row)."""
    return "Shell" if program == "shell" else program


def _session_title(r):
    """Its title as the list shows it (store.ts's toSession): its own name, else its task's first line, else its
    program's (program_title)."""
    task = r.get("task") if isinstance(r.get("task"), str) else ""
    title = r.get("title") if isinstance(r.get("title"), str) and _truthy(r.get("title")) else \
        task.split("\n")[0].strip()
    program = r.get("program") if isinstance(r.get("program"), str) else ""
    return title or program_title(program)


def _session_account(r):
    """The account a session runs on, as store.ts's accountNameOf reads it: its own where it names one; else none for a
    session on another provider's model (it runs on that provider's key) or one whose program runs on no account."""
    program, account = r.get("program"), r.get("account")
    if _truthy(account):
        kind = "codex" if program == "codex" else "claude"
    else:
        if _truthy(r.get("model_provider")):
            return None
        kind = "codex" if program == "codex" else "claude" if program in ("claude", "chat") else None
        account = "default"
    if kind is None or not isinstance(account, str) or not _ACCOUNT_ID.fullmatch(account):
        return None
    return {"kind": kind, "id": account}


def _at(value):
    return _time(value.get("at")) if isinstance(value, dict) else None


def _session_events(r, ended_at):
    """What happened to a session, in the board's words, from the times its record carries (a time it cannot be,
    past the year 9999 or not whole seconds, left out: a session's programs can write its record)."""
    out = []
    for at, kind, words in ((_time(r.get("created_at")), "session.started", "Started"),
                            (_at(r.get("finished")), "session.finished", "Finished"),
                            (_at(r.get("shipped")), "session.shipped", "Shipped as a pull request"),
                            (_at(r.get("landed")), "session.landed", "Landed into the branch it follows")):
        if at is not None:
            out.append((at, kind, words))
    if ended_at is not None and not _truthy(r.get("running")) and r.get("program") != "chat":
        code = r.get("exit_code")
        number = isinstance(code, (int, float)) and not isinstance(code, bool)
        # as session_state reads the code: below 0 a signal, 0 or none the end, anything else a failure
        words = "Its program was stopped" if number and code < 0 else \
            "Its program ended" if code is None or (number and code == 0) else "Its program failed"
        out.append((ended_at, "session.ended", words))
    return [event(at, kind, words) for at, kind, words in sorted(out, key=lambda e: e[0])]


def _session_rows(user, held, own_ended=frozenset()):
    """The workspace's sessions as rows of their own: each one the list shows but those it leaves out, and those a row
    holds (`held`, the sessions the board's other rows name). A session in `own_ended`, whose rows of kind session the
    sources gave have all ended, is held only while the list shows it ended too: one the workspace runs again after
    them is its own row as well."""
    out = []
    for r in _sessions(user):
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not _SESSION.fullmatch(r["id"]):
            continue
        if _truthy(r.get("closed")) or _is_sign_in(r):
            continue
        state = session_state(r)
        if r["id"] in held and (r["id"] not in own_ended or state in SESSION_ENDED):
            continue
        phase = "history" if state in SESSION_ENDED else "now"
        created = _time(r.get("created_at"))
        ended = (_time(r.get("ended_at")) or _at(r.get("finished"))) if phase == "history" else None
        try:
            out.append(row("session", r["id"], _session_title(r), phase=phase, state=state,
                           account=_session_account(r), session=r["id"], created_at=created, started_at=created,
                           ended_at=ended, events=_session_events(r, ended)))
        except ValueError as e:      # a record out of shape: said, and the board answers with the rest
            print(f"board: session {r['id']} could not be shown: {e}", file=sys.stderr)
    return out


# ---- the board ------------------------------------------------------------------------------------------------------

def _changed(r):
    """A row's newest change: the newest of its own times and its events'."""
    times = [t for t in (r["created_at"], r["started_at"], r["ended_at"]) if t is not None]
    return max(times + [e["at"] for e in r["events"]], default=0)


def _queue_key(r, when):
    """Where a next row stands in the board's one queue: the rows whose time to go on is known first, soonest first (a
    task's as _task_rows tells it in `when`, else the row's `waits_until`); then the rows carrying an order of their
    source's queue, by that order; then the rest, the one waiting longest first, since it was made, one whose making is
    not known last."""
    t = when.get((r["kind"], r["id"]), r["waits_until"])
    made = r["created_at"]
    return (t is None, t if t is not None else 0, r["order"] is None, r["order"] if r["order"] is not None else 0,
            made is None, made if made is not None else 0, KINDS.index(r["kind"]), r["id"])


def arranged(rows, when=None):
    """The rows in the board's order (the module's docstring), each next row numbered; `when`, {(kind, id): time}, the
    time a next row goes on where the board can tell it and its `waits_until` does not say it (an attempt queued: its
    queueing)."""
    when = when or {}
    for i, r in enumerate(sorted((r for r in rows if r["phase"] == "next"), key=lambda r: _queue_key(r, when)), 1):
        r["order"] = i
    return sorted(rows, key=lambda r: (PHASES.index(r["phase"]), -_changed(r), KINDS.index(r["kind"]), r["id"]))


def board(user, now=None):
    """The person's board, as GET /api/ade/board answers it. A workspace session a row names is no row of its own
    (_session_rows), but for one whose rows of kind session from the sources have all ended (the controller's: its
    store keeps a task ended once it ended) while the list shows it open: the workspace runs it again, so it is its
    own row as well."""
    now = int(time.time()) if now is None else now
    when = {}
    with store.connect() as db:
        rows = _task_rows(db, user["id"], now, when)
    seen = {(r["kind"], r["id"]) for r in rows}
    for name, source in list(_SOURCES.items()):
        try:
            given = list(source(user, now) or ())
        except Exception as e:  # noqa: BLE001  one module's rows failing leaves the others
            print(f"board: the rows of {name} could not be read: {type(e).__name__}", file=sys.stderr)
            continue
        for g in given:
            try:
                r = _checked(g)
            except (ValueError, TypeError) as e:
                print(f"board: a row of {name} is out of shape: {e}", file=sys.stderr)
                continue
            if (r["kind"], r["id"]) in seen:
                print(f"board: a row of {name} is on the board already: {r['kind']} {r['id']}", file=sys.stderr)
                continue
            seen.add((r["kind"], r["id"]))
            rows.append(r)
    held = {r["session"] for r in rows if r["session"]}
    own_open = {r["session"] for r in rows if r["session"] and r["kind"] == "session" and r["phase"] != "history"}
    own = {r["session"] for r in rows if r["session"] and r["kind"] == "session"}
    rows.extend(_session_rows(user, held, own - own_open))
    return {"at": now, "rows": arranged(rows, when)}


@router.get("")
def read(user=Depends(current_user)):
    return JSONResponse(board(user), headers=NO_STORE)
