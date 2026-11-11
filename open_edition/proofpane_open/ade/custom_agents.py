"""The person's own agents: any command-line agent the workspace has, beside the ones it knows by name, started in a
session's terminal in its worktree. Kept by the app, on the account, never in the workspace, whose programs run as its
agent's user and could change a file there: what a session runs is what the person wrote here.

    GET    /api/ade/workspace/custom-agents          the account's own agents
    POST   /api/ade/workspace/custom-agents          {name, command, task}: add one
    PUT    /api/ade/workspace/custom-agents/{id}     {name, command, task}: change it
    DELETE /api/ade/workspace/custom-agents/{id}     forget it (sessions started from it keep their command)

`command` is the program and its arguments as a shell would split them (shlex), never run by a shell: no pipes, no
variables, no globs. `task` is how the session's task reaches it: `argument`, as its last argument (as Claude Code and
Codex take it), or `typed`, at its prompt by the page's "Type the task" (as Kimi Code needs). The workspace's gate does
not see inside one, as with a shell: what it changed is checked when the session finishes.
"""

import secrets
import shlex
import time
import unicodedata
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import store
from .workspace_routes import workspace_user

MAX_NAME = 40
MAX_COMMAND = 500
MAX_ARGS = 32
MAX_AGENTS = 20
TASK_MODES = ("argument", "typed")

router = APIRouter(prefix="/api/ade/workspace/custom-agents")


class AgentIn(BaseModel):
    name: str = Field(max_length=200)
    command: str = Field(max_length=2000)
    task: str = "argument"


def _unseen(text):
    """Whether `text` holds what a person would not see as text (control, format, private-use or unassigned)."""
    return any(unicodedata.category(c) in ("Cc", "Cf", "Co", "Cn", "Cs", "Zl", "Zp") for c in text)


def checked(name, command, task):
    """{name, command, task} as kept, or HTTPException 400 saying what is wrong."""
    name = " ".join(name.split()) if isinstance(name, str) else ""
    if not name or len(name) > MAX_NAME or _unseen(name):
        raise HTTPException(status_code=400, detail=f"An agent's name is 1 to {MAX_NAME} characters of plain text.")
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND or _unseen(command):
        raise HTTPException(status_code=400, detail=f"Its command is one line of 1 to {MAX_COMMAND} characters.")
    try:
        argv = shlex.split(command)
    except ValueError:
        raise HTTPException(status_code=400, detail="Its command has a quote that is not closed.") from None
    if not argv or len(argv) > MAX_ARGS:
        raise HTTPException(status_code=400, detail=f"Its command is a program and at most {MAX_ARGS - 1} arguments.")
    if argv[0].startswith("-"):
        raise HTTPException(status_code=400, detail="Its command starts with the program, not an option.")
    if task not in TASK_MODES:
        raise HTTPException(status_code=400, detail="Its task goes in as its last argument, or is typed at its prompt.")
    return {"name": name, "command": command.strip(), "task": task}


def _row(r):
    return {"id": r["id"], "name": r["name"], "command": r["command"], "task": r["task"]}


def list_for(user_id):
    with store.connect() as db:
        return [_row(r) for r in db.execute(
            "SELECT id, name, command, task FROM ade_custom_agents WHERE user_id = ? ORDER BY created_at, id",
            (user_id,))]


def get(user_id, agent_id):
    """One of the account's own agents, or None."""
    if not isinstance(agent_id, str):
        return None
    with store.connect() as db:
        r = db.execute("SELECT id, name, command, task FROM ade_custom_agents WHERE user_id = ? AND id = ?",
                       (user_id, agent_id)).fetchone()
    return _row(r) if r else None


def _availability(user, agents):
    """Each agent's program, whether the workspace finds it there (`available`), when the workspace runs; None
    otherwise, or when it cannot say."""
    from . import workspace_client as client
    from .workspace_routes import _running
    names = {}
    for a in agents:
        try:
            names[a["id"]] = shlex.split(a["command"])[0]
        except (ValueError, IndexError):
            names[a["id"]] = None
    try:
        endpoint = _running(user)
        query = urlencode([("also", n) for n in sorted({n for n in names.values() if n})])
        got = client.call(endpoint, "GET", "/programs?" + query)
        # a workspace agent from before `also` answers without it: not known, so not refused
        found = got["also"] if isinstance(got, dict) and isinstance(got.get("also"), dict) else None
    except Exception:  # noqa: BLE001  not running: not known, so not refused
        found = None
    for a in agents:
        a["available"] = None if found is None else bool(names[a["id"]] and found.get(names[a["id"]]))
    return agents


@router.get("")
def agents(user=Depends(workspace_user)):
    """The account's own agents, each with whether the workspace finds its program (`available`, None when the
    workspace does not run)."""
    return {"agents": _availability(user, list_for(user["id"]))}


@router.post("")
def add(body: AgentIn, user=Depends(workspace_user)):
    spec = checked(body.name, body.command, body.task)
    with store.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM ade_custom_agents WHERE user_id = ?", (user["id"],)).fetchone()[0]
        if count >= MAX_AGENTS:
            raise HTTPException(status_code=400, detail=f"At most {MAX_AGENTS} agents of your own: forget one first.")
        agent_id = secrets.token_hex(6)
        db.execute("INSERT INTO ade_custom_agents (user_id, id, name, command, task, created_at) VALUES (?,?,?,?,?,?)",
                   (user["id"], agent_id, spec["name"], spec["command"], spec["task"], int(time.time())))
    return dict(spec, id=agent_id)


@router.put("/{agent_id}")
def change(agent_id: str, body: AgentIn, user=Depends(workspace_user)):
    spec = checked(body.name, body.command, body.task)
    with store.connect() as db:
        done = db.execute("UPDATE ade_custom_agents SET name = ?, command = ?, task = ? WHERE user_id = ? AND id = ?",
                          (spec["name"], spec["command"], spec["task"], user["id"], agent_id)).rowcount
    if not done:
        raise HTTPException(status_code=404, detail="No such agent of yours.")
    return dict(spec, id=agent_id)


@router.delete("/{agent_id}")
def forget(agent_id: str, user=Depends(workspace_user)):
    with store.connect() as db:
        done = db.execute("DELETE FROM ade_custom_agents WHERE user_id = ? AND id = ?", (user["id"], agent_id)).rowcount
    if not done:
        raise HTTPException(status_code=404, detail="No such agent of yours.")
    return {"forgotten": agent_id}
