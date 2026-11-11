"""The controller's routes, under /api/ade/controller (ade/controller.py). Every route needs a signed-in account the
provider gives a workspace, but as the last paragraph says, and reads or changes the app's store alone, so none of them
starts a machine; a change is sent on to the workspace by the next sync, at once where it runs. The workspace's side
serves GET /controller/record and PUT /controller, which the app's sync calls (controller.sync); it never calls these.

    GET    /api/ade/controller                        the controller as the page shows it: its settings and state, the
                                                      newest requests and decisions, the requirements (open, and closed
                                                      in the last week) with their sessions, the sessions started for no
                                                      requirement the app has, and the questions
    PUT    /api/ade/controller                        {account?, cap?, models?, efforts?, chat_model?, chat_effort?,
                                                      confirm_plans?}: the owner's settings; only those given change
    POST   /api/ade/controller/on | off | stop        the On switch, and Stop: it starts and continues nothing until on
    POST   /api/ade/controller/end-all                Stop, and the workspace told to stop every session its
                                                      controller started, worktrees kept
    GET    /api/ade/controller/requests?before=N&limit=M   the owner's requests, newest first
    POST   /api/ade/controller/requests               {text, checks?, protected?}: a request, kept word for word,
                                                      with its checks and protected paths
    POST   /api/ade/controller/requests/{id}/go       the owner's go on that request's plan (with confirm_plans on)
    POST   /api/ade/controller/requests/{id}/withdraw the owner withdraws a request no plan was taken from
    GET    /api/ade/controller/requirements/{id}      one requirement, with its sessions
    POST   /api/ade/controller/requirements/{id}/hold {hold}: held, or released
    POST   /api/ade/controller/requirements/{id}/close {as: done | stopped}
    POST   /api/ade/controller/requirements/{id}/resume  null or {text}: the owner's Resume of its own manager, with
                                                      words its manager alone is given
    GET    /api/ade/controller/requirements/{id}/attempts?before=N&limit=M   its manager's attempts, newest first
    GET    /api/ade/controller/requirements/{id}/attempts/{attempt}   one attempt, with its packet
    POST   /api/ade/controller/questions/{id}/answer  {text}: the owner's answer, a decision word for word
    POST   /api/ade/controller/decisions              {text}: a decision of the owner's that answers no question
    GET    /api/ade/controller/journal?before=S&limit=M   the log, newest first, each line in the app's own words
    GET    /api/ade/controller/packet                 the packet the workspace was last sent, or one built now
    GET    /api/ade/controller/board                  the board's rows for the requirements and their sessions
    GET    /api/ade/controller/holders                the owner's live links to choose the Manager from, the digest of
                                                      the settings a holder holds the role under, and the holder now
    POST   /api/ade/controller/holder                 {payload, credential}: a linked session made the Manager, signed
    DELETE /api/ade/controller/holder                 the role taken back from the linked session that holds it

A linked session may hold the Manager's role in the account's place (ade/manager_link.py). It needs no
workspace: where the deployment runs none, the holder routes need only the sign-in, and the others answer an account
with a live link or that has had a linked session as its holder (manager_user). Where the deployment keeps the
workspace from the account (the hosted site's admins alone), all but taking the role back refuse it, as before.
"""

import sys
import threading
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..deps import current_user
from . import agents, controller, manager_link, workspaces

router = APIRouter(prefix="/api/ade/controller")


def manager_user(user=Depends(current_user)):
    """An account the provider gives a workspace; or, where the deployment runs none (the refusal's 404), one with a
    live link the page may make the Manager, or that has had a linked session as its holder (a linked
    Manager needs no workspace; manager_link.answers). Where the deployment keeps the
    workspace from this account (403: on the hosted site, an admin's alone), its refusal stands, as before: a link is
    no way round it."""
    try:
        workspaces.for_user(user)
    except workspaces.WorkspaceAccess as e:
        if e.status != 404 or not manager_link.answers(user):
            raise
    return user


def holder_user(user=Depends(current_user)):
    """The account a linked session may be made the Manager for: the sign-in alone where the deployment runs no
    workspace or gives this account one; the refusal where it keeps the workspace from this account, as manager_user
    keeps it. Taking the role back needs the sign-in alone, always."""
    try:
        workspaces.for_user(user)
    except workspaces.WorkspaceAccess as e:
        if e.status != 404:
            raise
    return user


def _sync_soon(user):
    """Send a change on at once where the workspace runs, in a thread of its own (controller.sync runs one sync per
    account at a time); the poll's next round sends it otherwise."""
    endpoint = agents._endpoint(user)
    if endpoint is None:
        return

    def run():
        try:
            controller.sync(user, endpoint)
        except Exception as e:  # noqa: BLE001  the poll tries again
            print(f"controller: a sync failed: {type(e).__name__}: {e}", file=sys.stderr)
    threading.Thread(target=run, name="controller-sync", daemon=True).start()


class TextIn(BaseModel):
    text: str = Field(max_length=controller.REQUEST_MAX * 2)


class RequestIn(BaseModel):
    """A request with its checks and protected paths: a key it does not
    name is refused (422), never dropped; the checks and the protected paths are any JSON here, held to their form by
    add_request, which answers each wrong one 400 in the app's own words. While a linked session holds the Manager's
    role, a goal is {payload, credential} instead, the owner's signed instruction to it (manager_link.send_goal)."""
    model_config = ConfigDict(extra="forbid")
    text: Optional[str] = Field(default=None, max_length=controller.REQUEST_MAX * 2)
    checks: Any = None
    protected: Any = None
    payload: Optional[dict[str, Any]] = None
    credential: Optional[dict[str, Any]] = None


class HoldIn(BaseModel):
    hold: bool


class ResumeIn(BaseModel):
    """The owner's words with a Resume: a key it does not name is refused (422);
    resume holds the words to a decision's form."""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=controller.DECISION_MAX * 2)


class CloseIn(BaseModel):
    as_: str = Field(alias="as", max_length=20)


@router.get("")
def controller_view(user=Depends(manager_user)):
    return controller.view(user)


@router.put("")
def controller_settings(body: dict[str, Any], user=Depends(manager_user)):
    out = controller.change_settings(user, body)
    _sync_soon(user)
    return out


@router.post("/on")
def controller_on(user=Depends(manager_user)):
    out = controller.set_state(user, "on")
    _sync_soon(user)
    return out


@router.post("/off")
def controller_off(user=Depends(manager_user)):
    out = controller.set_state(user, "off")
    _sync_soon(user)
    return out


@router.post("/stop")
def controller_stop(user=Depends(manager_user)):
    out = controller.set_state(user, "stopped")
    _sync_soon(user)
    return out


@router.post("/end-all")
def controller_end_all(user=Depends(manager_user)):
    out = controller.end_all(user)
    _sync_soon(user)
    return out


@router.get("/requests")
def controller_requests(before: Optional[int] = None, limit: int = controller.LISTED, user=Depends(manager_user)):
    return controller.requests_of(user, before, limit)


@router.post("/requests")
def controller_request(body: RequestIn, user=Depends(manager_user)):
    if body.payload is not None or body.credential is not None:
        out = manager_link.send_goal(user, body.model_dump(exclude_none=True))
    elif body.text is None:
        raise controller._error(422, "A request has its words, text.")
    else:
        out = controller.add_request(user, body.text, checks=body.checks, protected=body.protected)
    _sync_soon(user)
    return out


@router.post("/requests/{request_id}/go")
def controller_go(request_id: str, user=Depends(manager_user)):
    out = controller.go(user, request_id)
    _sync_soon(user)
    return out


@router.post("/requests/{request_id}/withdraw")
def controller_withdraw(request_id: str, user=Depends(manager_user)):
    out = controller.withdraw(user, request_id)
    _sync_soon(user)
    return out


@router.get("/requirements/{requirement_id}")
def controller_requirement(requirement_id: str, user=Depends(manager_user)):
    return controller.requirement_view(user, requirement_id)


@router.post("/requirements/{requirement_id}/hold")
def controller_hold(requirement_id: str, body: HoldIn, user=Depends(manager_user)):
    out = controller.hold(user, requirement_id, body.hold)
    _sync_soon(user)
    return out


@router.post("/requirements/{requirement_id}/close")
def controller_close(requirement_id: str, body: CloseIn, user=Depends(manager_user)):
    out = controller.close(user, requirement_id, body.as_)
    _sync_soon(user)
    return out


@router.post("/requirements/{requirement_id}/resume")
def controller_resume(requirement_id: str, body: Optional[ResumeIn] = None, user=Depends(manager_user)):
    out = controller.resume(user, requirement_id, body.text if body is not None else None)
    _sync_soon(user)
    return out


@router.get("/requirements/{requirement_id}/attempts")
def controller_attempts(requirement_id: str, before: Optional[int] = None, limit: int = controller.LISTED,
                        user=Depends(manager_user)):
    return controller.attempts_of(user, requirement_id, before, limit)


@router.get("/requirements/{requirement_id}/attempts/{attempt_id}")
def controller_attempt(requirement_id: str, attempt_id: str, user=Depends(manager_user)):
    return controller.attempt_of(user, requirement_id, attempt_id)


@router.post("/questions/{question_id}/answer")
def controller_answer(question_id: str, body: TextIn, user=Depends(manager_user)):
    out = controller.decide(user, body.text, question_id)
    _sync_soon(user)
    return out


@router.post("/decisions")
def controller_decision(body: TextIn, user=Depends(manager_user)):
    out = controller.decide(user, body.text)
    _sync_soon(user)
    return out


@router.get("/journal")
def controller_journal(before: Optional[int] = None, limit: int = controller.JOURNAL_PAGE,
                       user=Depends(manager_user)):
    return controller.journal_of(user, before, limit)


@router.get("/packet")
def controller_packet(user=Depends(manager_user)):
    return controller.packet_of(user)


@router.get("/board")
def controller_board(user=Depends(manager_user)):
    now = int(time.time())
    return {"at": now, "rows": controller.board_rows(user["id"], now)}


@router.get("/holders")
def controller_holders(user=Depends(holder_user)):
    return manager_link.offer(user)


@router.post("/holder")
def controller_holder(body: dict[str, Any], user=Depends(holder_user)):
    out = manager_link.set_holder(user, body)
    _sync_soon(user)
    return out


@router.delete("/holder")
def controller_holder_taken_back(user=Depends(current_user)):
    out = manager_link.drop_holder(user)
    _sync_soon(user)
    return out
