"""The Skills view's routes, under /api/ade/skills (ade/skill_counts.py). Every route needs a signed-in account the
provider gives a workspace, and answers the signed-in owner's own counts alone. The two reads of the app's store start
nothing; the read of the workspace's record reaches it only while it runs, and never starts it.

    GET   /api/ade/skills?days=7|30|90                 the owner's skill calls over the last days (30 by default): by
                                                       UTC day, and by skill (core, business by repository, other), each
                                                       with where its calls were made, its versions and how the tasks
                                                       that called it ended; and how the app's reads stand
    GET   /api/ade/skills/detail?kind=&skill=&repo=&days=   one skill: its calls by UTC day, its versions, the repositories,
                                                       sessions, tasks and requirements they were made in, and how its
                                                       tasks ended; `repo` (empty: no repository) for one repository's
                                                       calls of a business skill
    POST  /api/ade/skills/read                         read the workspace's record of skill calls now, as the poll's
                                                       round does: {"state", "taken"}

The workspace's side is `GET /skills?since=N` (proofpane_workspace/agent.py), which the app's read calls; it never calls
these. What these answer is the sessions' word, for display (ade/skill_counts.py).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from . import skill_counts
from .workspace_routes import workspace_user

router = APIRouter(prefix="/api/ade/skills")


@router.get("")
def skills_view(days: int = Query(30), user=Depends(workspace_user)):
    return skill_counts.view(user, days)


@router.get("/detail")
def skill_detail(kind: str = Query(max_length=20), skill: str = Query(max_length=100),
                 repo: Optional[str] = Query(None, max_length=120), days: int = Query(30),
                 user=Depends(workspace_user)):
    return skill_counts.detail(user, kind, skill, repo=repo or None, by_repo=repo is not None, days=days)


@router.post("/read")
def skills_read(user=Depends(workspace_user)):
    return skill_counts.read(user)
