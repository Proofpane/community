"""Where the account's work lives (ade/storage.py), under /api/ade/storage, for the signed-in account only.

    GET  /api/ade/storage    the choice, whether the account made it, the words a start is refused with now, the three
                             choices with their sentences (offered or not, and why not), what a move to each leaves,
                             and the question to turn end-to-end encryption on
    PUT  /api/ade/storage    {choice}: keep a choice (400 where it is no choice, 409 where it is not offered here); a
                             move away from the hosted choice stops the account's workspace first (502 where that
                             fails, the choice kept as it was);
                             {e2e_not_now: true}: the account's "not now" to that question, kept (409 before a choice)
    DELETE /api/ade/storage/workspace   {confirmation}: delete the account's workspace left behind at once, with a
                             confirmation made for that (passkeys.CHANGE's workspace-delete): 409 while its work
                             lives in it, where its record names another Fly app, or where no choice is kept; 404
                             where it has none, or under the local provider, whose workspace is a folder; 403 without
                             the confirmation, which none of the others uses up
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import current_user
from . import storage

router = APIRouter(prefix="/api/ade/storage")


class StorageIn(BaseModel):
    choice: Optional[str] = None
    e2e_not_now: bool = False


class DeleteIn(BaseModel):
    confirmation: Optional[str] = None


@router.get("")
def get_storage(user=Depends(current_user)):
    return storage.view(user)


@router.put("")
def put_storage(body: StorageIn, user=Depends(current_user)):
    if body.choice is not None and body.e2e_not_now:
        raise HTTPException(status_code=400, detail="Keep a choice, or say not now: one at a time.")
    if body.choice is not None:
        return storage.choose(user, body.choice)
    if body.e2e_not_now:
        return storage.e2e_not_now(user)
    raise HTTPException(status_code=400, detail=storage.NO_CHOICE)


@router.delete("/workspace")
def delete_workspace(body: Optional[DeleteIn] = None, user=Depends(current_user)):
    return storage.delete_workspace(user, body.confirmation if body else None)
