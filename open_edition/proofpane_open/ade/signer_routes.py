"""The second screen's HTTP surface (ade/signers.py says what a signer is and what the relay carries). The page's routes
take the sign-in and nothing else; a signer's routes take its own signature (ADE-Signer) and nothing else; a link's key
works on none of them, and a signer's key on no route but its own. Every answer is JSON and carries Cache-Control:
no-store, refusals included (main.py adds it to everything under these prefixes).

    POST   /api/ade/signers/tickets            page: {confirmation?} -> {ticket, expires_at, url, account, command}, once
    GET    /api/ade/signers                    page: {signers: [{id, name, created_at, last_seen_at}]}
    DELETE /api/ade/signers/{id}               page: revoke -> {revoked_at}
    POST   /api/ade/sign-requests              page: {payload, title?} -> {id, code, expires_at}
    GET    /api/ade/sign-requests              page: {requests: [{id, purpose, to, created_at, expires_at, parked_from,
                                               state}]}, every one not ended, waiting or signed; never a code
    GET    /api/ade/sign-requests/{id}         page: {id, state, expires_at}, and once signed {payload, credential,
                                               signer}
    DELETE /api/ade/sign-requests/{id}         page: cancel it, with any assertion it holds -> {cancelled}
    DELETE /api/ade/sign-requests              page: cancel every one -> {cancelled}

    POST   /api/ade/signer/pair                signed by the new key: {ticket, name, public_key} -> {signer_id, account,
                                               url}
    GET    /api/ade/signer/owner-keys          signer: {account, passkeys, credentials, high_security}
    GET    /api/ade/signer/requests            signer: {requests: [{id, purpose, payload, code, created_at, expires_at,
                                               parked_from, allow, context}]}, every 2 seconds at most (429)
    POST   /api/ade/signer/requests/{id}       signer: {credential} -> {state}
    GET    /api/ade/signer/sessions            signer: {links: [{id, name, kind, stopped_at}]}
    POST   /api/ade/signer/nonce               signer: {purpose}: instruction, resume or move -> as POST
                                               /api/ade/link/nonce
    POST   /api/ade/signer/commands            signer: {payload, credential}: an instruction or a move -> as POST
                                               /api/ade/link/commands
    POST   /api/ade/signer/links/{id}/resume   signer: {payload, credential} -> {resumed_at}
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..deps import current_user
from . import signers
from .link_routes import _json, _signed, answer

router = APIRouter()


class TicketIn(BaseModel):
    # from /api/account/confirm, made for "signer-ticket" (passkeys.CHANGE), when the account has a way to make one
    confirmation: Optional[str] = None


def _signer(signed=Depends(_signed)):
    found = signers.authenticate(signed["header"], signed["method"], signed["raw_path"], signed["raw_query"],
                                 signed["body"])
    found["body"] = signed["body"]
    return found


def _client(request: Request):
    """The address a request came from, as the server sees it (behind the hosted site's proxy, the client's)."""
    return request.client.host if request.client else ""


# ---- the page's ----------------------------------------------------------------------------------------------------

@router.post("/api/ade/signers/tickets")
def new_ticket(body: Optional[TicketIn] = None, user=Depends(current_user)):
    return answer(signers.create_ticket(user["id"], body.confirmation if body else None))


@router.get("/api/ade/signers")
def signer_list(user=Depends(current_user)):
    return answer(signers.for_page(user["id"]))


@router.delete("/api/ade/signers/{signer_id}")
def revoke(signer_id: str, user=Depends(current_user)):
    return answer(signers.revoke(user["id"], signer_id))


@router.post("/api/ade/sign-requests")
async def park(request: Request, user=Depends(current_user)):
    return answer(signers.park(user["id"], _json(await request.body()), client=_client(request)))


@router.get("/api/ade/sign-requests")
def waiting(user=Depends(current_user)):
    return answer(signers.requests_for_page(user["id"]))


@router.get("/api/ade/sign-requests/{request_id}")
def collect(request_id: str, user=Depends(current_user)):
    return answer(signers.collect(user["id"], request_id))


@router.delete("/api/ade/sign-requests/{request_id}")
def cancel(request_id: str, user=Depends(current_user)):
    return answer(signers.cancel(user["id"], request_id))


@router.delete("/api/ade/sign-requests")
def cancel_all(user=Depends(current_user)):
    return answer(signers.cancel_all(user["id"]))


# ---- a signer's ----------------------------------------------------------------------------------------------------

@router.post("/api/ade/signer/pair")
def pair(signed=Depends(_signed)):
    return answer(signers.pair(signed["header"], signed["method"], signed["raw_path"], signed["raw_query"],
                               signed["body"]))


@router.get("/api/ade/signer/owner-keys")
def owner_keys(me=Depends(_signer)):
    return answer(signers.owner_keys_for_signer(me))


@router.get("/api/ade/signer/requests")
def requests(me=Depends(_signer)):
    return answer(signers.requests_for_signer(me))


@router.post("/api/ade/signer/requests/{request_id}")
def submit(request_id: str, me=Depends(_signer)):
    return answer(signers.submit(me, request_id, _json(me["body"])))


@router.get("/api/ade/signer/sessions")
def sessions(me=Depends(_signer)):
    return answer(signers.sessions_for_signer(me))


@router.post("/api/ade/signer/nonce")
def nonce(me=Depends(_signer)):
    return answer(signers.nonce_for_signer(me, _json(me["body"])))


@router.post("/api/ade/signer/commands")
def commands(me=Depends(_signer)):
    return answer(signers.post_for_signer(me, _json(me["body"])))


@router.post("/api/ade/signer/links/{link_id}/resume")
def resume(link_id: str, me=Depends(_signer)):
    return answer(signers.resume_for_signer(me, link_id, _json(me["body"])))
