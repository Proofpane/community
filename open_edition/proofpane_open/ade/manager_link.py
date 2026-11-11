"""A running linked session made the Manager. Beside the account whose workspace's chat is the Manager
(ade/controller.py), the owner may give the role to one of the sessions linked to the ADE (ade/link.py), each with its
own key: no workspace, sign-in or clone is needed, and the panel says which session holds it.

The holder, signed. Choosing a link changes who acts for the owner, so the page signs it with a passkey: a stage-2a
payload of the purpose `manager` (ade/link_commands.py) whose `to` is the link's id, which names the settings it holds
the role under (the cap, the models, the efforts, wait for my go: controller.HOLD_SETTINGS) by their digest, and which
expires, a week at most after it is made; a new signature renews it. The server checks it as every signed payload is,
and that the link is one of the owner's live links, heard within two of its intervals, and that the settings are the
current ones; it keeps it on the Manager's row as its holder, with the payload's id. There is one holder at a time, an
account or a link: while a link holds the role, the account's chat in the workspace is told the Manager is off
(controller.mirror_body), and choosing an account, or changing the settings the link holds the role under, ends its
hold (controller.change_settings); so does revoking the link. Taking the role back needs only the sign-in, as a stop
does, since it only removes authority, and takes effect at once. However a hold ends, it turns the Manager off: the
account's chat acts again only once the owner turns the Manager on. A hold that expired is kept, holding nothing, and
keeps the account's chat off until the owner signs again, chooses an account or takes the role back, since a change of
holder is the owner's. A change of holder is a line of the Manager's journal, naming the link and
the payload's id.

A link whose hook does not run, a cloud session among them, cannot be governed as a local session is: it is offered all
the same, and the panel says, before the owner signs and for as long as it holds the role, that Proofpane holds none of
that session's own calls, only the acts it makes as the Manager through the app's checked routes. Which local link's
hook runs is not told apart today: a local link is taken as one whose hooks its pairing set up.

Holding the role gives no reach over other sessions: that stays a delegation of its own, signed apart."""

import json
import sqlite3
import time

from .. import store
from . import controller, link, link_commands

# where a linked Manager's tasks run in this build: its own machine alone; a start in the workspace
# is left for later
OWN_MACHINE = ("A linked Manager's tasks run on its own machine, in its folder, each its own report: the workspace "
               "starts none for it in this build.")

# what the panel says of a holder whose own calls Proofpane does not hold
NOT_GOVERNED = ("Proofpane holds none of this session's own calls: only the acts it makes as the Manager, through the "
                "app's checked routes.")


def _now(now):
    return int(time.time()) if now is None else int(now)


def governed(link_row):
    """Whether Proofpane holds the link's session's own calls through its hooks: a local link, whose pairing set up its
    hooks; never a cloud session, which runs no hook here."""
    return link_row["kind"] != "cloud"


def _link_item(row, now):
    """One of the owner's live links as the panel lists them to choose the Manager: its name and kind, when it was last
    heard, whether it is silent (not heard within two of its intervals: shown, not offered), whether it is offered,
    and the notice its holding the role carries, where its own calls are none of Proofpane's."""
    quiet = link.silent(row, now)
    item = {"id": row["id"], "name": row["name"], "kind": row["kind"], "last_heard_at": link.heard_at(row),
            "silent": quiet, "offered": not quiet}
    if not governed(row):
        item["notice"] = NOT_GOVERNED
    return item


def _live_links(db, user_id):
    return db.execute("SELECT * FROM ade_links WHERE user_id = ? AND revoked_at IS NULL ORDER BY created_at, name",
                      (user_id,)).fetchall()


def offer(user, now=None):
    """What the panel shows to give the Manager's role to a linked session: the owner's live links, each marked silent
    and not offered where it was not heard within two of its intervals; the digest of the settings a holder would hold
    the role under, which its payload names; the longest a hold may last; and the holder now, or None."""
    now = _now(now)
    with store.connect() as db:
        c = controller._ensure(db, user["id"], now)
        return {"links": [_link_item(r, now) for r in _live_links(db, user["id"])],
                "settings_digest": controller.settings_digest(c), "settings": controller.hold_settings(c),
                "life_most": link_commands.MANAGER_LIFE_MAX, "holder": holder_view(db, user["id"], c, now)}


def answers(user):
    """Whether the Manager's routes answer this account where the deployment runs no workspace
    (controller_routes.manager_user): while it has a live link, which the page may make the
    Manager, and once it has had a linked session as its holder, so that what it did stays shown after its hold ends,
    as it is renewed, given to another or taken back."""
    with store.connect() as db:
        return db.execute("SELECT 1 FROM ade_links WHERE user_id = ? AND revoked_at IS NULL LIMIT 1",
                          (user["id"],)).fetchone() is not None or db.execute(
            "SELECT 1 FROM ade_controller_events INDEXED BY ade_controller_events_at WHERE user_id = ? AND "
            "kind = 'holder' LIMIT 1", (user["id"],)).fetchone() is not None


def holder_view(db, user_id, c, now):
    """The linked session kept as the Manager's holder, as the page shows it: the link, its name and kind, when it took
    the role and when its hold ends or ended, whether it is live (an ended hold holds nothing, and keeps the account's
    chat off until the owner acts: controller.holder_kept), the payload's id, whether it is silent now and when it was
    last heard, and the notice where its own calls are none of Proofpane's; None where none is kept."""
    if not controller.holder_kept(c):
        return None
    h = {"link": c["holder_link"], "payload": c["holder_payload"], "since": c["holder_since"],
         "until": c["holder_until"], "settings": c["holder_settings"], "live": controller.holder_of(c, now) is not None}
    row = db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ?", (h["link"], user_id)).fetchone()
    out = dict(h, name=row["name"] if row else None, kind=row["kind"] if row else None,
               last_heard_at=link.heard_at(row) if row else None,
               silent=bool(row is None or row["revoked_at"] is not None or link.silent(row, now)))
    if row is not None and not governed(row):
        out["notice"] = NOT_GOVERNED
    return out


def set_holder(user, data, now=None):
    """Give the Manager's role to the linked session the owner signed for ({payload, credential}, the purpose
    `manager`): checked as every signed payload is (link_commands.check_manager), then that its `to` is one of the
    owner's live links, heard within two of its intervals, and that its settings are the digest of the current ones;
    kept on the Manager's row as its holder, superseding any other, with its nonce used and the passkey's counter moved
    in the same transaction, and a journal line. Returns the holder as the page shows it."""
    now = _now(now)
    payload, _, _, row, auth = link_commands.check_manager(user["id"], data, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        c = controller._ensure(db, user["id"], now)
        target = db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                            (payload["to"], user["id"])).fetchone()
        if target is None:
            raise link.LinkError(404, "The payload's to is none of your live linked sessions.")
        if link.silent(target, now):
            raise link.LinkError(409, "That session has not been heard from within two of its intervals: a silent "
                                      "session is not given the Manager's role.")
        if payload["settings"] != controller.settings_digest(c):
            raise link.LinkError(409, "The settings changed since the page asked: sign the Manager's holder again, "
                                      "under the settings it holds the role under now.")
        link_commands.commit_statement(db, user["id"], payload, row, auth, now)
        # the link kept as the holder before, its hold live or ended: the same one renews its hold, and another's
        # place taken ends that one's open tasks, as any end of its hold does
        before = c["holder_link"] if controller.holder_kept(c) else None
        if before is not None and before != target["id"]:
            controller._end_link_tasks(db, user["id"], before, controller.HOLD_SUPERSEDED, now)
        db.execute("UPDATE ade_controllers SET holder_link = ?, holder_payload = ?, holder_since = ?, "
                   "holder_until = ?, holder_settings = ? WHERE user_id = ?",
                   (target["id"], payload["id"], now, payload["expires_at"], payload["settings"], user["id"]))
        controller._bump(db, user["id"], now)
        controller.journal(db, user["id"], "holder", {"from": before, "to": target["id"], "payload": payload["id"],
                                                     "until": payload["expires_at"]}, now=now)
        return holder_view(db, user["id"], controller._row(db, user["id"]), now)


def link_ended(db, user_id, link_id, now):
    """Inside the transaction that revokes a link (link.end_link): a hold it has on the Manager's role ends with it,
    and the Manager is turned off (controller.end_hold)."""
    c = controller._row(db, user_id)
    if controller.holder_kept(c) and c["holder_link"] == link_id:
        controller.end_hold(db, user_id, c, now, "its link was revoked")


def send_goal(user, data, now=None):
    """A goal the owner sent while a linked session holds the Manager's role ({payload, credential}):
    a stage-2a instruction to the holder, signed with one of the account's passkeys, so that
    the server cannot make a goal up. Checked as every command is (link_commands.check_instruction), then that a link
    holds the role now and the payload names it (409 otherwise, a goal waiting for none); kept, in one transaction, as
    the holder's instruction (link_commands.store_signed: its session takes it and checks the signature again itself,
    within the owner's commands an hour and what a session holds untaken) and as the owner's request, word for word,
    naming it (controller.add_request_in). A goal carries its words alone: checks and protected paths are no part of
    what the owner signs, so the server could have made them up, and are refused with it (422). The request as the
    page shows it, with its instruction's seq."""
    now = _now(now)
    if not isinstance(data, dict) or set(data) - {"payload", "credential"}:
        raise link.LinkError(422, "A goal sent to a linked Manager carries its signed words alone: no checks and no "
                                  "protected paths, which are no part of what you sign.")
    payload, raw, credential, row, auth = link_commands.check_instruction(user["id"], data, now)
    text = controller._owner_words(payload["text"], controller.REQUEST_MAX, "A request")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        c = controller._ensure(db, user["id"], now)
        h = controller.holder_of(c, now)
        if h is None:
            raise link.LinkError(409, "No linked session holds the Manager's role now: sign one as its holder "
                                      "again, or choose an account, before sending it a goal.")
        if payload["to"] != h["link"]:
            raise link.LinkError(409, "A goal is signed to the linked session that holds the Manager's role, and "
                                      "this one names another.")
        target = db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                            (h["link"], user["id"])).fetchone()
        if target is None:
            raise link.LinkError(409, "The linked session that holds the Manager's role is revoked.")
        link_commands.check_room(db, user["id"], target, now)
        link_commands.commit_statement(db, user["id"], payload, row, auth, now)
        try:
            seq = link_commands.store_signed(db, user["id"], "instruction", payload, raw.decode("utf-8"),
                                             json.dumps(credential, separators=(",", ":")), row["id"], target, now)
        except sqlite3.IntegrityError:
            raise link.LinkError(409, "A command with this id is stored already.") from None
        return controller.add_request_in(db, user["id"], text, None, now, instruction=seq)


def packet_for(link_row, now=None):
    """What the linked session that holds the Manager's role reads (GET /api/ade/link/manager):
    the packet the workspace's chat would be given (controller.build_packet: the requests and decisions word for word
    as the owner's instructions, the requirements, sessions and questions as information, the settings and the
    state), but that its requests are those the owner signed to this link, each with its signed instruction's seq, for
    the session to check again itself against the owner keys it pinned; the others, which the server could have made
    up, are counted (unsigned_requests) and given as no instruction. A goal whose instruction was withdrawn is
    withdrawn with it, and one past its life untaken is counted (expired_requests), not given: the session can take
    neither. The owner's decisions are made with the sign-in alone, so they are counted
    (unsigned_decisions) and given as none: the holder asks the owner in the session channel, whose answers they sign.
    With the holder's own hold, End all's count and, where it was pressed while this hold lasted, when, and a fresh
    read of who holds the role: 403 for a link that does not hold it now; 503 with the app's
    words where the packet cannot be built."""
    now = _now(now)
    user_id = link_row["user_id"]
    with store.connect() as db:
        c = controller._row(db, user_id)
        h = controller.holder_of(c, now)
        if h is None or h["link"] != link_row["id"]:
            raise link.LinkError(403, "This session does not hold the Manager's role.")
        try:
            data, digest = controller.build_packet(db, user_id, now)
        except controller.PacketError as e:
            raise link.LinkError(503, f"The Manager's packet cannot be built now: {e}") from None
        signed, expired = {}, set()
        for r in db.execute("SELECT r.id, r.instruction_seq, e.taken_at, e.withdrawn_at, e.expires_at FROM "
                            "ade_controller_requests r JOIN ade_link_events e ON e.seq = r.instruction_seq AND "
                            "e.user_id = r.user_id WHERE r.user_id = ? AND e.to_id = ?",
                            (user_id, link_row["id"])).fetchall():
            if r["withdrawn_at"] is not None:
                continue
            if r["taken_at"] is None and r["expires_at"] <= now:
                expired.add(r["id"])
            else:
                signed[r["id"]] = r["instruction_seq"]
        pressed = db.execute("SELECT MAX(at) FROM ade_controller_events INDEXED BY ade_controller_events_at WHERE "
                             "user_id = ? AND kind = 'controller.end_all' AND at >= ?",
                             (user_id, h["since"])).fetchone()[0]
    packet = json.loads(data)
    given = packet["instructions"]["requests"]
    packet["instructions"]["requests"] = [dict(r, instruction=signed[r["id"]]) for r in given if r["id"] in signed]
    packet["expired_requests"] = sum(1 for r in given if r["id"] in expired)
    packet["unsigned_requests"] = sum(1 for r in given if r["id"] not in signed and r["id"] not in expired)
    decisions = packet["instructions"]["decisions"]
    packet["unsigned_decisions"] = len(decisions) + (packet["instructions"].get("more_decisions") or 0)
    packet["instructions"]["decisions"], packet["instructions"]["more_decisions"] = [], 0
    packet["holder"] = {"until": h["until"], "settings": h["settings"], "payload": h["payload"]}
    packet["end_all"] = c["end_all"]
    packet["end_all_at"] = pressed
    packet["digest"] = digest
    packet["where"] = OWN_MACHINE
    return packet


ACT_KINDS = ("plan", "start", "state", "stop", "continue")
# a linked Manager's question to the owner: an answer the Manager's own questions take is
# made with the sign-in alone, so the holder asks in the session channel, where the owner's answer comes signed
ASK_IN_CHANNEL = ("A linked Manager asks the owner in the session channel: ade_link.py post question \"...\" --to "
                  "owner, whose answer they sign.")


def act(link_row, data, now=None):
    """One act of the linked session that holds the Manager's role (POST
    /api/ade/link/manager/acts), checked by the app before the link does it, as the workspace's tools check theirs,
    and refused with why where a check fails: the link acts only once the app took it, so a bound broken is refused,
    never kept and flagged as a workspace's start already made is. Each act once, by its id; 403 for a link that does
    not hold the role now. (status, body): 200 {id, taken}, or 409 {detail, why}, the refusal journaled as the
    Manager's (`by`, the link) where the journal keeps refusals.
    - plan: a requirement from a goal the owner signed to this link, its words and acceptance whole paragraphs of it,
      as controller._take_plan checks a plan; never with a share, which would make a sub-goal's own manager in the
      workspace: holding the role gives no reach over other sessions;
    - start: a task on the link's own machine, in its folder, within the bounds (_start_bounds); kept
      as the link's own report (`reported_by`), never the workspace's observation;
    - state: the link's report of one of its own tasks' state;
    - stop, continue: of one of its own tasks.
    A question is the session channel's (ASK_IN_CHANNEL). An act refused, sent again by its id, is refused again, with
    its why."""
    now = _now(now)
    user_id = link_row["user_id"]
    if isinstance(data, dict) and data.get("kind") == "question":
        raise link.LinkError(422, ASK_IN_CHANNEL)
    if not isinstance(data, dict) or data.get("kind") not in ACT_KINDS:
        raise link.LinkError(422, "An act is one of " + ", ".join(ACT_KINDS) + ", by its kind.")
    act_id = controller._uid_of(data.get("id"))
    if act_id is None:
        raise link.LinkError(422, "An act's id is 32 lowercase hex characters, new for each act.")
    token = controller.ACTING_LINK.set(link_row["id"])
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            c = controller._row(db, user_id)
            h = controller.holder_of(c, now)
            if h is None or h["link"] != link_row["id"]:
                raise link.LinkError(403, "This session does not hold the Manager's role.")
            seen = db.execute("SELECT kind, taken FROM ade_controller_actions WHERE user_id = ? AND id = ?",
                              (user_id, act_id)).fetchone()
            if seen is not None and seen["taken"]:
                return 200, {"id": act_id, "taken": True, "again": True}
            if seen is not None:
                why = _why_of(db, user_id, seen["kind"], act_id)
            else:
                why = _act(db, user_id, link_row, c, data, act_id, now)
                if why is None:
                    controller._bump(db, user_id, now)
    finally:
        controller.ACTING_LINK.reset(token)
    if why is not None:
        return 409, {"detail": f"The app did not take it: {why}.", "why": why}
    return 200, {"id": act_id, "taken": True}


def _refused(db, user_id, page, act_id, kind, why, now, **ids):
    """An act refused, journaled as the workspace's refusals are (controller._refuse): its why."""
    controller._refuse(db, user_id, page, act_id, kind, why, now, ids=ids or None)
    return why


def _why_of(db, user_id, kind, act_id):
    """Why the app refused the act `act_id` of `kind`, as its refusal's journal line says: the line naming that act,
    found by its id (32 hex characters, so the pattern holds no wildcard), read through the index on (owner, kind).
    "refused" where none was kept (past the day's refusals, controller._refuse)."""
    row = db.execute("SELECT payload FROM ade_controller_events INDEXED BY ade_controller_events_at WHERE user_id = ? "
                     "AND kind = ? AND payload LIKE ? ORDER BY seq DESC LIMIT 1",
                     (user_id, kind + ".refused", f'%"action":"{act_id}"%')).fetchone()
    said = json.loads(row["payload"]) if row else {}
    return said.get("why") if said.get("action") == act_id and said.get("why") else "refused"


def _start_bounds(db, user_id, link_row, c, start, now):
    """The bounds a linked Manager's start is held to before it is made (controller.bounds_broken's, but for the
    account and the program, which are the link's own on its machine): the Manager on, a model and an effort the owner
    allows, a requirement the app has, open, not held and not waiting for the owner's go, planned from a goal signed to
    this link and kept by no sub-goal's own manager (holding the role gives no reach over
    another's work), a repository it may touch, and under the cap. The words of each it breaks."""
    words = []
    held = controller.hold_settings(c)
    if c["state"] != "on":
        words.append(f"the Manager is {c['state']}")
    if start.get("model") not in held["models"]:
        words.append("a model the owner did not allow")
    if start.get("effort") not in held["efforts"]:
        words.append("an effort the owner did not allow")
    requirement_id = controller._uid_of(start.get("requirement"))
    r = db.execute("SELECT * FROM ade_controller_requirements WHERE id = ? AND user_id = ?",
                   (requirement_id, user_id)).fetchone() if requirement_id else None
    if r is None:
        words.append("a requirement the app does not have")
    else:
        if r["state"] != "open":
            words.append("a closed requirement")
        if r["hold"]:
            words.append("a requirement on hold")
        if controller._waits_for_go(r):
            words.append("before the owner's go on its plan")
        if start.get("repo") not in controller._json(r["repos"], []):
            words.append("a repository its requirement may not touch")
        if controller._manager(db, user_id, r["id"]) is not None:
            words.append("a requirement its own manager keeps")
        if _signed_goal(db, user_id, r["request_id"], link_row["id"]) is None:
            words.append("a requirement not planned from a goal signed to it")
    if controller._open_at(db, user_id, now, start.get("task") or "") >= held["cap"]:
        words.append(f"one more than the {held['cap']} sessions the Manager may have open at once")
    return words


def _signed_goal(db, user_id, request_id, link_id):
    """The instruction a request was signed to the link `link_id` as (its seq, its take and its withdrawal), or None
    where it was not."""
    return db.execute("SELECT e.seq, e.taken_by, e.withdrawn_at FROM ade_controller_requests r JOIN ade_link_events e "
                      "ON e.seq = r.instruction_seq AND e.user_id = r.user_id WHERE r.id = ? AND r.user_id = ? AND "
                      "e.to_id = ?", (request_id, user_id, link_id)).fetchone() if request_id else None


def _act(db, user_id, link_row, c, data, act_id, now):
    """act, inside its transaction: None where it was taken, else why it was refused."""
    kind = data["kind"]
    page = controller._Page(db, user_id, c, now)
    if kind == "plan":
        if data.get("share") is not None:
            return _refused(db, user_id, page, act_id, "plan", "a linked Manager plans no share, which would make a "
                            "sub-goal's own manager in the workspace", now)
        signed = _signed_goal(db, user_id, controller._uid_of(data.get("request")), link_row["id"])
        if signed is None:
            return _refused(db, user_id, page, act_id, "plan", "it names no request the owner signed to it", now)
        # a goal is the session's to act on once it took it, and so checked its signature
        if signed["withdrawn_at"] is not None:
            return _refused(db, user_id, page, act_id, "plan", "the owner withdrew that goal", now)
        if signed["taken_by"] != link_row["id"]:
            return _refused(db, user_id, page, act_id, "plan", "a goal this session has not taken: take it first, "
                            f"with ade_link.py take {signed['seq']}", now)
        taken = controller._take_plan(db, user_id, page, act_id, data, now, now)
        return None if taken == "taken" else _why_of(db, user_id, "plan", act_id)
    if kind == "start":
        task_id = controller._uid_of(data.get("task"))
        if task_id is None:
            return _refused(db, user_id, page, act_id, "start", "it names no task", now)
        if db.execute("SELECT 1 FROM ade_controller_tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone():
            return _refused(db, user_id, page, act_id, "start", "a task of that id is recorded already", now,
                            task=task_id)
        named = {}
        for key, pattern, required in (("repo", controller._REPO, True), ("branch", controller._REF, False),
                                       ("base", controller._REF, False), ("model", controller._MODEL, True),
                                       ("effort", controller._MODEL, True)):
            value = data.get(key)
            if value is None and not required:
                named[key] = None
            elif isinstance(value, str) and pattern.fullmatch(value) and controller.tasks.secret_shape(value) is None:
                named[key] = value
            else:
                return _refused(db, user_id, page, act_id, "start", f"its {key} is not one", now, task=task_id)
        words = _start_bounds(db, user_id, link_row, c, dict(named, task=task_id, requirement=data.get("requirement")),
                              now)
        if words:
            return _refused(db, user_id, page, act_id, "start", "; ".join(words), now, task=task_id)
        if not page.start():
            return _refused(db, user_id, page, act_id, "start", controller._past_starts(), now, task=task_id)
        requirement_id = controller._uid_of(data.get("requirement"))
        db.execute("INSERT INTO ade_controller_tasks (id, user_id, requirement_id, session_id, repo, branch, base, "
                   "account, program, model, effort, state, started_at, created_at, updated_at, reported_by) VALUES "
                   "(?,?,?,NULL,?,?,?,NULL,?,?,?,'starting',?,?,?,?)",
                   (task_id, user_id, requirement_id, named["repo"], named["branch"], named["base"],
                    controller.PROGRAM, named["model"], named["effort"], now, now, now, link_row["id"]))
        controller._mark(db, user_id, act_id, "start", True, now)
        controller.journal(db, user_id, "start", {"action": act_id, "task": task_id, "requirement": requirement_id,
                                                  "model": named["model"], "effort": named["effort"]},
                           requirement_id=requirement_id, task_id=task_id, now=now)
        return None
    # a state, a stop or a continue: of one of its own tasks
    task_id = controller._uid_of(data.get("task"))
    row = db.execute("SELECT * FROM ade_controller_tasks WHERE id = ? AND user_id = ? AND reported_by = ?",
                     (task_id, user_id, link_row["id"])).fetchone() if task_id else None
    if row is None:
        why = "it names no task of its own"
        return _refused(db, user_id, page, act_id, kind, why, now) if kind != "state" else why
    if kind == "state":
        state = data.get("state")
        if state not in controller.TASK_STATES:
            return "its state is not one of " + ", ".join(controller.TASK_STATES)
        if row["state"] in controller.TASK_ENDED:
            return "its task has ended"
        ended = now if state in controller.TASK_ENDED else None
        db.execute("UPDATE ade_controller_tasks SET state = ?, ended_at = ?, updated_at = ? WHERE id = ? AND "
                   "user_id = ?", (state, ended, now, row["id"], user_id))
        controller._mark(db, user_id, act_id, "state", True, now)
        if ended is not None:
            controller.journal(db, user_id, "task.ended", {"task": row["id"], "state": state},
                               requirement_id=row["requirement_id"], task_id=row["id"], now=now)
        return None
    if kind == "continue" and c["state"] != "on":
        return _refused(db, user_id, page, act_id, kind, f"the Manager is {c['state']}", now, task=row["id"])
    if kind == "stop":
        db.execute("UPDATE ade_controller_tasks SET stopped_at = COALESCE(stopped_at, ?), updated_at = ? WHERE id = ? "
                   "AND user_id = ?", (now, now, row["id"], user_id))
    else:
        db.execute("UPDATE ade_controller_tasks SET continues = continues + 1, updated_at = ? WHERE id = ? AND "
                   "user_id = ?", (now, row["id"], user_id))
    controller._mark(db, user_id, act_id, kind, True, now)
    controller.journal(db, user_id, kind, {"action": act_id, "task": row["id"]},
                       requirement_id=row["requirement_id"], task_id=row["id"], now=now)
    return None


def drop_holder(user, now=None):
    """Take the Manager's role back from the linked session that holds it: the sign-in alone, as a stop, since it only
    removes authority; at once, with a journal line. {"holder": None}; a no-op where no link holds it."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        c = controller._ensure(db, user["id"], now)
        controller.end_hold(db, user["id"], c, now, "the owner took the role back")
    return {"holder": None}
