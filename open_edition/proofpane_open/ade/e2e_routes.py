"""The owner's routes for end-to-end encryption of what linked sessions send, under /api/ade/e2e (ade/e2e_keys.py says
what each body holds). Each takes the page's sign-in; each change takes a passkey's word (a statement's signature, or
a passkey's confirmation) or the recovery code's proof as well, and is held, turning encryption on among them
(ade/holds.py: POST /api/ade/holds/{id}/cancel stops it until its time, with {confirmation} from a passkey that
e2e_keys.cancellers names, or {recovery_proof} for purpose cancel from the recovery code in force; the sign-in alone does
not). Every answer carries Cache-Control: no-store.

    GET  /api/ade/e2e                     what the page unlocks the keys with and changes them from
    POST /api/ade/e2e/turn-on             {statement, content_key, mk_id?, credential?, recovery?, grants?, roster?,
                                          chain?, content_keys?, seal?, replace?} -> {hold, told}; a classical
                                          account turned on again with a new master key (chain) is upgraded to
                                          post-quantum keys in the same change with {upgrade: true, kem,
                                          credentials, recovery, kept, recovery_proof?, new_recovery_proof?}
    POST /api/ade/e2e/rotate              {statement, content_key, recovery_proof?} -> {hold, told}
    POST /api/ade/e2e/revoke              {statement, content_key, mk_id, chain, grants, roster, content_keys, remove,
                                          high_security, recovery?, recovery_proof?} -> {hold, told}; the upgrade to
                                          post-quantum keys adds {upgrade: true, kem, credentials, recovery, kept,
                                          new_recovery_proof?}
    POST /api/ade/e2e/credentials         {credential, mk_id, grant, roster, confirmation | recovery_proof} ->
                                          {hold, told}
    POST /api/ade/e2e/off                 {statement, recovery_proof?} -> {hold, told}
    POST /api/ade/e2e/anchors             {anchors, mk_id, confirmation | recovery_proof} -> {hold, told}
    POST /api/ade/e2e/recovery-proof      {purpose: rotate, revoke, credentials, off, anchors, passkey, cancel,
                                          turn-on or reseal; public_key?; digest?} -> {challenge_id, server_key,
                                          nonce, purpose, expires_at}, or for a post-quantum key {challenge_id,
                                          server_ct, nonce, purpose, expires_at, kem}
    GET  /api/ade/e2e/reseal              ?after=NEXT -> {items: [{where, value, slot?}], next}: what a post-quantum
                                          account stores sealed to a classical key, for the page to seal again
    POST /api/ade/e2e/reseal              {items: [{where, old_sha256, new}], recovery_proof, kem} -> {swapped}

Every write to a post-quantum account's keys carries kem: "x-wing" (ade/e2e_keys.py _era).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..deps import current_user
from . import e2e_keys
from .link_routes import _json

router = APIRouter(prefix="/api/ade/e2e")
NO_STORE = {"Cache-Control": "no-store"}


def answer(content):
    return JSONResponse(content, headers=NO_STORE)


async def _body(request: Request):
    """The request's JSON, read before the handler, which runs in a worker thread: a change whose hold has ended takes
    effect as the next request settles it, and turning encryption on then seals in place, which takes a while (the
    database's compaction after it runs in a thread of its own)."""
    return _json(await request.body())


class ProofIn(BaseModel):
    purpose: str
    public_key: Optional[str] = None        # the upgrade's new recovery key, for the proof that the page holds it
    digest: Optional[str] = None            # a re-seal's batch, the one change its proof is for


@router.get("")
def keys(user=Depends(current_user)):
    return answer(e2e_keys.account_view(user["id"]))


@router.post("/turn-on")
def turn_on(user=Depends(current_user), body=Depends(_body)):
    return answer(e2e_keys.turn_on(user["id"], body))


@router.post("/rotate")
def rotate(user=Depends(current_user), body=Depends(_body)):
    return answer(e2e_keys.rotate(user["id"], body))


@router.post("/revoke")
def revoke(user=Depends(current_user), body=Depends(_body)):
    return answer(e2e_keys.revoke(user["id"], body))


@router.post("/credentials")
def enrol(user=Depends(current_user), body=Depends(_body)):
    return answer(e2e_keys.enrol(user["id"], body))


@router.post("/off")
def off(user=Depends(current_user), body=Depends(_body)):
    return answer(e2e_keys.off(user["id"], body))


@router.post("/anchors")
def anchors(user=Depends(current_user), body=Depends(_body)):
    return answer(e2e_keys.anchors(user["id"], body))


@router.post("/recovery-proof")
def recovery_proof(body: ProofIn, user=Depends(current_user)):
    return answer(e2e_keys.proof_challenge(user["id"], body.purpose, public_key=body.public_key, digest=body.digest))


@router.get("/reseal")
def reseal_page(after: Optional[str] = None, user=Depends(current_user)):
    return answer(e2e_keys.reseal_page(user["id"], after))


@router.post("/reseal")
def reseal(user=Depends(current_user), body=Depends(_body)):
    return answer(e2e_keys.reseal(user["id"], body))
