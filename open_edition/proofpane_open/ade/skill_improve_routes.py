"""The routes of the improve loop for a repository's business skills, under /api/ade/skill-improve
(ade/skill_improve.py). Every route needs a signed-in account the provider gives a workspace, and answers the signed-in
owner's own proposals alone; each tap is the owner's, and none is made by the loop. The listing reads the app's store
alone; the others reach the workspace only while it runs, and never start it.

    GET   /api/ade/skill-improve                              the loop's list: every business proposal, every state,
                                                              newest first; the week's budget by kind; the switches;
                                                              and the app's copy of the record of proposals
    GET   /api/ade/skill-improve/record                       the workspace's record as it stands: its state, whether
                                                              it is held and why, the starts the bar refused
    POST  /api/ade/skill-improve/record/restore               the owner's tap: the record written from the app's copy
    PUT   /api/ade/skill-improve/repos/{repo}/switch          {on, account?, model?}: the repository's switch
    GET   /api/ade/skill-improve/repos/{repo}/candidates      each folder at the branch's head that may be proposed for,
                                                              and why each other may not
    GET   /api/ade/skill-improve/proposals/{id}               one proposal, its repository looked at first
    POST  /api/ade/skill-improve/proposals/{id}/merge         {method?}: into their copy, or on GitHub once a pull
                                                              request is open
    POST  /api/ade/skill-improve/proposals/{id}/pull-request  its branch pushed to origin and its pull request opened
    POST  /api/ade/skill-improve/proposals/{id}/remake        brought up to date, the same proposal on a new commit
    POST  /api/ade/skill-improve/proposals/{id}/reject        {reason?}: rejected, its ref removed, its bar going on
    POST  /api/ade/skill-improve/proposals/{id}/take          a found proposal taken as theirs
    POST  /api/ade/skill-improve/proposals/{id}/go-back       for a found proposal, a proposal to go back
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from . import skill_improve
from .workspace_routes import workspace_user

router = APIRouter(prefix="/api/ade/skill-improve")


class SwitchIn(BaseModel):
    on: bool
    account: Optional[str] = None
    model: Optional[str] = None


class MergeIn(BaseModel):
    method: str = "squash"


class RejectIn(BaseModel):
    reason: Optional[str] = None


@router.get("")
def listing(user=Depends(workspace_user)):
    return skill_improve.listing(user)


@router.get("/record")
def record(user=Depends(workspace_user)):
    return skill_improve.record_view(user)


@router.post("/record/restore")
def restore(user=Depends(workspace_user)):
    return skill_improve.restore(user)


@router.put("/repos/{repo}/switch")
def switch(repo: str, body: SwitchIn, user=Depends(workspace_user)):
    return skill_improve.set_switch(user, repo, body.on, body.account, body.model)


@router.get("/repos/{repo}/candidates")
def candidates(repo: str, user=Depends(workspace_user)):
    return skill_improve.candidates(user, repo)


@router.get("/proposals/{proposal_id}")
def proposal(proposal_id: str, user=Depends(workspace_user)):
    return skill_improve.proposal(user, proposal_id)


@router.post("/proposals/{proposal_id}/merge")
def merge(proposal_id: str, body: Optional[MergeIn] = None, user=Depends(workspace_user)):
    return skill_improve.merge(user, proposal_id, (body or MergeIn()).method)


@router.post("/proposals/{proposal_id}/pull-request")
def pull_request(proposal_id: str, user=Depends(workspace_user)):
    return skill_improve.pull_request(user, proposal_id)


@router.post("/proposals/{proposal_id}/remake")
def remake(proposal_id: str, user=Depends(workspace_user)):
    return skill_improve.remake(user, proposal_id)


@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: str, body: Optional[RejectIn] = None, user=Depends(workspace_user)):
    return skill_improve.reject(user, proposal_id, (body or RejectIn()).reason)


@router.post("/proposals/{proposal_id}/take")
def take(proposal_id: str, user=Depends(workspace_user)):
    return skill_improve.take(user, proposal_id)


@router.post("/proposals/{proposal_id}/go-back")
def go_back(proposal_id: str, user=Depends(workspace_user)):
    return skill_improve.owners_go_back(user, proposal_id)
