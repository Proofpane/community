"""The session channel's HTTP surface, under /api/ade/link (ade/link.py says what the channel is). The owner's routes
take the page's sign-in and nothing else; a session's routes take its signature and nothing else, so neither can use
the other's. Every answer is JSON and carries Cache-Control: no-store, refusals included (main.py adds it to
everything under this prefix). Another account's link is not found (404).

    POST   /api/ade/link/tickets                    owner: {link_id?, confirmation?} -> {ticket, expires_at, ticket_id,
                                                    url, keys, high_security, e2e, pair_hold}, once
    POST   /api/ade/link/pair                       signed by the new key: {ticket, name, kind, public_key, session_ref,
                                                    waits: true} -> where the pairing stands, as GET /ask answers it:
                                                    approved, the link made (or moved to the new key) when its hold
                                                    ends (POST /api/ade/holds/{id}/cancel stops it until then); a body
                                                    without waits (a client from an older release): 409, the ticket
                                                    left unused
    GET    /api/ade/link/sessions                   owner: live links (each with its key's fingerprint and whether it
                                                    may propose), presence, checkpoints, open questions, the open
                                                    prompts but those Close hid, and the open proposals (prompts,
                                                    proposals), the seqs of the open prompts Close hid, oldest first
                                                    (hidden_waiting), the requests to link and the
                                                    pairings that wait (asks), the seconds a pairing or an approval
                                                    waits now (connect_hold), the seconds a move waits (move_hold), the
                                                    moves that wait on their holds or whose holds just ended without
                                                    them (held_moves), the ticket pairings that ended in the last ten
                                                    minutes, linked or cancelled, by their tickets' ids
                                                    (ended_pairings), and the server's time (server_time); each link
                                                    with what its client last reported of its session (reported,
                                                    reported_at)
    GET    /api/ade/link/timeline/{link_id}         owner: one link's events, newest first (?before=SEQ&limit=N), but
                                                    the mirrored notes naming a session; ?session=ID: those alone
    POST   /api/ade/link/questions/{seq}/close      owner: the fixed closing note to the asker; for an open proposal or
                                                    item to look at, to the session that posted it, deciding nothing.
                                                    An open prompt the owner may answer is hidden instead, with no
                                                    note: out of the open list, still open and answerable until its
                                                    deadline -> {seq, hidden_at}, the same again while it is open
    GET    /api/ade/link/prompts/closed             owner: {prompts, closed_more, server_time}: every prompt Close hid
                                                    that is still open, and the newest 50 closed in the last 24 hours
                                                    (closed_more: how many more closed then are left out), newest
                                                    first, each with hidden_at and how it ended (ended), never an
                                                    answer's words
    DELETE /api/ade/link/links/{link_id}            owner: revoke, {confirmation?}
    PUT    /api/ade/link/links/{link_id}/reads-others  owner: {allowed}: let it read the others' checkpoints, or not
    PUT    /api/ade/link/links/{link_id}/proposes   owner: {allowed}: let it propose messages for the other sessions,
                                                    or not -> {proposes}
    POST   /api/ade/link/heartbeat                  link: {} or {reported}: presence, and what its client read of
                                                    its own session (link.heartbeat)
    POST   /api/ade/link/events                     link: {client_id, kind, to?, ref?, text, mirror?, session?,
                                                    said_at?} -> {seq, session, e2e}
    PUT    /api/ade/link/checkpoint                 link: {expected_version, document} -> {version}
    POST   /api/ade/link/usage                      link: {records}, each a reply's usage (ade/link_usage.py) ->
                                                    {paired, taken}: kept and forwarded to the account's Proofpane
                                                    only while the account is paired
    GET    /api/ade/link/history                    owner: each live link's listed sessions, newest first
    GET    /api/ade/link/history/{link_id}/{id}     owner: one listed session's conversation, where one is kept
    PUT    /api/ade/link/history                    link: {sessions, conversations, withheld?, machine?, pins?,
                                                    e2e_seal?} -> {listed, left_out, talks, noted, e2e}
    PUT    /api/ade/link/history/talk               link: {session, messages, earlier, key?, key_id?} -> {kept,
                                                    messages, e2e}
    POST   /api/ade/link/ack                        link: {seq}
    GET    /api/ade/link/feed                       link: ?after=SEQ&limit=N&turn=1 -> {events, through, head}
    GET    /api/ade/link/resume                     link: {document, checkpoint_version, head}
    GET    /api/ade/link/others                     link: the other sessions' presence and whole checkpoints, when the
                                                    owner lets it (403 otherwise)
    POST   /api/ade/link/tasks/{id}/grant-widenings link: {client_id, add, lease_end?, rungs?} -> {widening}: an ask for
                                                    more in one of the account's tasks' grants, which waits for the
                                                    owner's passkey and changes nothing until then (ade/task_grants.py)

The commands (ade/link_commands.py says what they are):

    POST   /api/ade/link/nonce                      owner: {purpose} -> {nonce, expires_at, rp_id, deployment, account,
                                                    server_time, credentials}
    POST   /api/ade/link/commands                   owner: {payload, credential}, an instruction or an answer -> {seq,
                                                    expires_at}; a move -> {held, expires_at}, the move stored only
                                                    when its hold ends (POST /api/ade/holds/{id}/cancel stops it)
    POST   /api/ade/link/commands/{seq}/withdraw    owner: a command or a move no session has taken (the move is failed,
                                                    saying so)
    POST   /api/ade/link/links/{link_id}/stop       owner: hold the session until a signed resume
    POST   /api/ade/link/links/{link_id}/resume     owner: {payload, credential}, a resume signed after the stop
    GET    /api/ade/link/owner-keys                 link: the account's passkeys, to pin
    GET    /api/ade/link/events/{seq}               link: one event for it, with a command's payload and signature; a
                                                    prompt's with its deadline_at, look, closed_reason, closed_at,
                                                    closed_by and open, which the prompt hook polls
    POST   /api/ade/link/take                       link: {seq, instance} -> {taken_at}; 409 with why (an answer to a
                                                    prompt: why prompt)
    POST   /api/ade/link/prompts/{seq}/claim        link that posted the prompt: {instance} -> {seq, payload,
                                                    signature, passkey_id, taken_at}, the owner's answer to it, claimed
                                                    as take claims a command; 409 with why
    GET    /api/ade/link/state                      link: {link_id, name, stopped, stopped_at, resume, head,
                                                    server_time, e2e}

End-to-end encryption of what linked sessions send (ade/e2e.py, ade/e2e_keys.py): where a text may be sealed, the
answers to POST /events, PUT /history and PUT /history/talk carry e2e, {seq, key_id} of the account's newest statement
of its keys (key_id null once encryption is off), or null; while encryption is on, a text in the clear on those paths
is refused 409 {e2e: "required", seq, key_id}.

    GET    /api/ade/link/e2e?after=SEQ&limit=N      link: {statements, more, e2e}: each statement after seq SEQ (0
                                                    by default), by seq, at most N (500 at most and by default), with
                                                    its canonical payload and the passkey's assertion (credential);
                                                    more: true while others follow, read with after the last seq
    POST   /api/ade/link/e2e                        link: {statements: [{payload, credential}]} past the server's
                                                    newest (after a restore) -> {held, hold, e2e}, held as a change of
                                                    the owner's is

A move of one of a machine's sessions into the hosted workspace (ade/moves.py says what it is), signed and taken as a
command is:

    PUT    /api/ade/link/moves/{seq}/transcript     link that took it: the transcript's bytes, at most 32 MiB
    PUT    /api/ade/link/moves/{seq}/bundle         link that took it: a git bundle's bytes, at most 32 MiB
    POST   /api/ade/link/moves/{seq}/done           link that took it: {session, remote, branch, head, changes, cwd,
                                                    bundle} -> {seq, state}
    POST   /api/ade/link/moves/{seq}/failed         link that took it: {reason} -> {seq, state}
    GET    /api/ade/link/moves                      owner: ?link_id=ID&session=ID -> {moves}, newest first, those held
                                                    and not stored among them (seq null)
    POST   /api/ade/link/moves/{seq}/start          owner: start an uploaded move in the workspace, starting the
                                                    workspace first when it is not running -> the move

Asking to be linked (ade/link_asks.py says what it is):

    POST   /api/ade/link/ask                        signed by the asking key: {address, name, kind, public_key,
                                                    session_ref} -> where the request stands
    GET    /api/ade/link/ask                        signed by the asking key, or a pairing's: where its request stands
    GET    /api/ade/link/address                    owner: {address, created_at, url}, the address made on first read
    POST   /api/ade/link/address                    owner: a new address; the requests waiting on the old one are
                                                    refused
    POST   /api/ade/link/asks/{id}/approve          owner: {confirmation?, replace?} -> {hold, told}; the link is made
                                                    when the hold ends (POST /api/ade/holds/{id}/cancel stops it until
                                                    then); replace: the id of the live cloud link of the request's
                                                    name, revoked then, for a cloud session's request
    POST   /api/ade/link/asks/{id}/refuse           owner
"""

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool

from .. import config, passkeys
from ..deps import current_user
from . import e2e_keys, holds, link, link_asks, link_commands, link_usage, moves, task_grants

router = APIRouter(prefix="/api/ade/link")
NO_STORE = {"Cache-Control": "no-store"}


def answer(content, status=200):
    return JSONResponse(content, status_code=status, headers=NO_STORE)


async def _signed(request: Request):
    """What a signature covers, read before any handler: the header, the method, the raw path and query, the body."""
    raw_path = request.scope.get("raw_path")
    return {"header": request.headers.get("authorization"), "method": request.method,
            "raw_path": raw_path.decode("latin-1") if raw_path else request.url.path,
            "raw_query": request.scope.get("query_string", b"").decode("latin-1"), "body": await request.body()}


def _link(signed=Depends(_signed)):
    found = link.authenticate(signed["header"], signed["method"], signed["raw_path"], signed["raw_query"],
                              signed["body"])
    found["body"] = signed["body"]
    return found


def _no_constant(name):
    raise ValueError(f"{name} is not a number JSON has")


def _json(body):
    """The request's JSON; NaN and the infinities, which Python's reader takes and a view could then not write back
    (a 500 at every read after), are refused with the rest of what is not JSON."""
    try:
        return json.loads(body or b"{}", parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise link.LinkError(422, "The request is JSON.") from None


class TicketIn(BaseModel):
    link_id: Optional[str] = None
    confirmation: Optional[str] = None      # from /api/account/confirm, when the account has a way to make one


class ConfirmationIn(BaseModel):
    confirmation: Optional[str] = None


class ApproveIn(BaseModel):
    confirmation: Optional[str] = None
    replace: Optional[str] = None           # the live cloud link the approval replaces (link_asks.approve)


class NonceIn(BaseModel):
    purpose: str


class ReadsOthersIn(BaseModel):
    allowed: bool


class ProposesIn(BaseModel):
    allowed: StrictBool        # true or false as JSON has them: no word turns it on


@router.post("/tickets")
def new_ticket(body: TicketIn, user=Depends(current_user)):
    ticket, expires_at, ticket_id = link.create_ticket(user["id"], body.link_id, body.confirmation)
    # the address the session pairs with is the one its signatures are checked against, never the page's own; the
    # fingerprints of the account's passkeys go in the pair command, which pins them as the session pairs: its
    # hardware keys alone while high security is on
    keys = [k["fingerprint"] for k in passkeys.owner_keys(user["id"])]
    # and the newest statement of the account's encryption keys ({seq, key_id}, or null), which the pair command
    # carries as it carries the keys (--e2e), so a new link takes nothing older (ade/e2e_keys.py)
    holds.settle(user["id"])
    # the ticket's id, which the pairing it makes is listed under (its request's ticket_id), and how long a pairing
    # waits now (link_asks.pair_hold): the page says both
    return answer({"ticket": ticket, "expires_at": expires_at, "ticket_id": ticket_id, "url": config.public_url(),
                   "keys": keys, "high_security": passkeys.high_security(user["id"]), "e2e": e2e_keys.told(user["id"]),
                   "pair_hold": link_asks.pair_hold(user["id"])})


@router.post("/pair")
def pair(request: Request, signed=Depends(_signed)):
    return answer(link.pair(signed["header"], signed["method"], signed["raw_path"], signed["raw_query"],
                            signed["body"], client=_client(request)))


@router.get("/sessions")
def sessions(user=Depends(current_user)):
    # a held approval whose time has come is a link by now, shown as one, and a held move a stored one; the requests to
    # link, beside the links, the moves that wait, the seconds a pairing and a move wait now, how the ticket pairings
    # that ended lately ended (the ticket's box on the page says it by them), and the server's time, by which the page
    # counts down to the ends of the holds it shows
    holds.settle(user["id"])
    return answer(dict(link.sessions(user["id"]), asks=link_asks.for_owner(user["id"]),
                       connect_hold=link_asks.pair_hold(user["id"]), move_hold=holds.delay(moves.HOLD),
                       held_moves=moves.held_for_owner(user["id"]),
                       ended_pairings=link_asks.ended_pairings(user["id"]), server_time=int(time.time())))


@router.get("/history")
def history(user=Depends(current_user)):
    return answer(link.history(user["id"]))


@router.put("/history")
def put_history(me=Depends(_link)):
    # a change to the account's encryption whose hold has ended takes effect first, so what a link sends meets it
    holds.settle(me["user_id"])
    return answer(link.put_history(me, _json(me["body"])))


@router.put("/history/talk")
def put_talk(me=Depends(_link)):
    holds.settle(me["user_id"])
    return answer(link.put_talk(me, _json(me["body"])))


@router.get("/history/{link_id}/{session_id}")
def talk(link_id: str, session_id: str, user=Depends(current_user)):
    return answer(link.talk(user["id"], link_id, session_id))


@router.get("/timeline/{link_id}")
def timeline(link_id: str, before: Optional[int] = None, limit: int = link.TIMELINE_LIMIT,
             session: Optional[str] = None, user=Depends(current_user)):
    return answer(link.timeline(user["id"], link_id, before, limit, session))


@router.post("/questions/{seq}/close")
def close(seq: int, user=Depends(current_user)):
    return answer(link.close_question(user["id"], seq))


@router.get("/prompts/closed")
def closed_prompts(user=Depends(current_user)):
    return answer(link.closed_prompts(user["id"]))


@router.delete("/links/{link_id}")
def revoke(link_id: str, body: Optional[ConfirmationIn] = None, user=Depends(current_user)):
    return answer(link.revoke(user["id"], link_id, body.confirmation if body else None))


@router.put("/links/{link_id}/reads-others")
def reads_others(link_id: str, body: ReadsOthersIn, user=Depends(current_user)):
    return answer(link.set_reads_others(user["id"], link_id, body.allowed))


@router.put("/links/{link_id}/proposes")
def proposes(link_id: str, body: ProposesIn, user=Depends(current_user)):
    return answer(link.set_proposes(user["id"], link_id, body.allowed))


@router.post("/heartbeat")
def heartbeat(me=Depends(_link)):
    return answer(link.heartbeat(me, _json(me["body"])))


@router.post("/events")
def events(me=Depends(_link)):
    holds.settle(me["user_id"])
    return answer(link.post_event(me, _json(me["body"])))


@router.put("/checkpoint")
def checkpoint(me=Depends(_link)):
    return answer(link.put_checkpoint(me, _json(me["body"])))


@router.post("/usage")
def usage(me=Depends(_link)):
    # the replies' usage the link's Stop hook read: kept once per account and request id while the account is paired
    # with Proofpane, and forwarded in a thread of its own, so this answer never waits on Proofpane
    return answer(link_usage.take(me, _json(me["body"])))


@router.post("/ack")
def ack(me=Depends(_link)):
    return answer(link.ack(me, _json(me["body"])))


@router.get("/feed")
def feed(after: Optional[int] = None, limit: int = link.FEED_LIMIT, turn: int = 0, me=Depends(_link)):
    return answer(link.feed(me, after, limit, bool(turn)))


@router.get("/resume")
def resume(me=Depends(_link)):
    return answer(link.resume(me))


@router.get("/others")
def others(me=Depends(_link)):
    return answer(link.others(me))


@router.post("/tasks/{task_id}/grant-widenings")
def ask_grant_widening(task_id: str, me=Depends(_link)):
    # a session asks, and only asks: its ask waits for the owner's passkey (task_grants.py)
    return answer(task_grants.ask_from_link(me, task_id, _json(me["body"])))


# ---- asking to be linked ------------------------------------------------------------------------------------------

def _client(request: Request):
    """The address a request came from, as the server sees it (behind the hosted site's proxy, the client's), as
    main._client gives it."""
    return request.client.host if request.client else ""


@router.post("/ask")
def ask(request: Request, signed=Depends(_signed)):
    return answer(link_asks.ask(signed["header"], signed["method"], signed["raw_path"], signed["raw_query"],
                                signed["body"], client=_client(request)))


@router.get("/ask")
def ask_poll(signed=Depends(_signed)):
    return answer(link_asks.poll(signed["header"], signed["method"], signed["raw_path"], signed["raw_query"],
                                 signed["body"]))


@router.get("/address")
def ask_address(user=Depends(current_user)):
    return answer(dict(link_asks.address(user["id"]), url=config.public_url()))


@router.post("/address")
def new_ask_address(user=Depends(current_user)):
    return answer(dict(link_asks.replace_address(user["id"]), url=config.public_url()))


@router.post("/asks/{ask_id}/approve")
def approve_ask(ask_id: str, body: Optional[ApproveIn] = None, user=Depends(current_user)):
    return answer(link_asks.approve(user["id"], ask_id, body.confirmation if body else None,
                                    body.replace if body else None))


@router.post("/asks/{ask_id}/refuse")
def refuse_ask(ask_id: str, user=Depends(current_user)):
    return answer(link_asks.refuse(user["id"], ask_id))


# ---- the commands -----------------------------------------------------------------------------------------------

@router.post("/nonce")
def nonce(body: NonceIn, user=Depends(current_user)):
    return answer(link_commands.nonce(user["id"], body.purpose))


@router.post("/commands")
async def commands(request: Request, user=Depends(current_user)):
    return answer(link_commands.post_command(user["id"], _json(await request.body())))


@router.post("/commands/{seq}/withdraw")
def withdraw(seq: int, user=Depends(current_user)):
    return answer(link_commands.withdraw(user["id"], seq))


@router.post("/links/{link_id}/stop")
def stop(link_id: str, user=Depends(current_user)):
    return answer(link_commands.stop(user["id"], link_id))


@router.post("/links/{link_id}/resume")
async def resume_link(link_id: str, request: Request, user=Depends(current_user)):
    return answer(link_commands.resume(user["id"], link_id, _json(await request.body())))


@router.get("/owner-keys")
def owner_keys(me=Depends(_link)):
    return answer(link_commands.owner_keys(me))


@router.get("/manager")
def manager_packet(me=Depends(_link)):
    from . import manager_link
    return answer(manager_link.packet_for(me))


@router.post("/manager/acts")
def manager_act(me=Depends(_link)):
    from . import manager_link
    status, body = manager_link.act(me, _json(me["body"]))
    return answer(body, status)


@router.get("/events/{seq}")
def event(seq: int, me=Depends(_link)):
    return answer(link_commands.event(me, seq))


@router.post("/take")
def take(me=Depends(_link)):
    return answer(link_commands.take(me, _json(me["body"])))


@router.post("/prompts/{seq}/claim")
def claim_prompt(seq: int, me=Depends(_link)):
    return answer(link_commands.claim_prompt(me, seq, _json(me["body"])))


@router.get("/state")
def state(me=Depends(_link)):
    holds.settle(me["user_id"])
    return answer(link_commands.state(me))


# ---- end-to-end encryption: the owner's statements of the account's keys --------------------------------------------

@router.get("/e2e")
def e2e_statements(me=Depends(_link), after: int = 0, limit: int = e2e_keys.FOR_LINK_PAGE,
                   e2e_seal: str | None = None):
    return answer(e2e_keys.for_link(me, after=after, limit=limit, seals=e2e_seal))


@router.post("/e2e")
def e2e_upload(me=Depends(_link)):
    holds.settle(me["user_id"])
    return answer(e2e_keys.upload(me, _json(me["body"])))


# ---- a move into the workspace ------------------------------------------------------------------------------------
# the files' routes check the link's signature themselves: they stream the body to a file, so it is never held whole

@router.put("/moves/{seq}/transcript")
async def move_transcript(seq: int, request: Request):
    return answer(await moves.receive(request, seq, "transcript"))


@router.put("/moves/{seq}/bundle")
async def move_bundle(seq: int, request: Request):
    return answer(await moves.receive(request, seq, "bundle"))


@router.post("/moves/{seq}/done")
def move_done(seq: int, me=Depends(_link)):
    return answer(moves.done(me, seq, _json(me["body"])))


@router.post("/moves/{seq}/failed")
def move_failed(seq: int, me=Depends(_link)):
    return answer(moves.failed(me, seq, _json(me["body"])))


@router.get("/moves")
def move_list(link_id: Optional[str] = None, session: Optional[str] = None, user=Depends(current_user)):
    return answer(moves.for_owner(user["id"], link_id, session))


@router.post("/moves/{seq}/start")
def move_start(seq: int, user=Depends(current_user)):
    return answer(moves.carry_on(user, seq))
