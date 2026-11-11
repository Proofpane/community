"""The hosted workspace's HTTP and terminal surface, under /api/ade/workspace. Off unless `OPEN_EDITION_ADE` names a
provider (`workspaces.py`). Every route needs a signed-in account the provider gives a workspace, and the workspace
is always the account's own, found from the account; a session id is only looked for inside it.

    GET    /api/ade/workspace                        whether this account has a workspace, whether it runs, whether it
                                                     was ever made (`made`: started once, so it keeps something), and
                                                     whether a start is under way or failed (while one is, it does
                                                     not run yet: its machine may be up before its agent answers)
    POST   /api/ade/workspace/start                  start it: waits a few seconds, then the start goes on while the
                                                     page watches the state (a new image takes a minute or two)
    POST   /api/ade/workspace/stop                   stop it (its programs are hung up; its files stay)
    GET    /api/ade/workspace/repos                  its repositories
    POST   /api/ade/workspace/repos                  clone one from an https address
    POST   /api/ade/workspace/repos/link             {path}: add one already on this machine, where it is (the
                                                     local provider's, on the person's own machine)
    DELETE /api/ade/workspace/repos/{name}           take one added from the machine out: its link, not its folder
    GET    /api/ade/workspace/sessions               its sessions
    POST   /api/ade/workspace/sessions               a new session: a worktree on pp/<id> and a program in a terminal
                                                     (claude, or a chat, once Claude is signed in there)
    GET    /api/ade/workspace/sessions/{id}
    DELETE /api/ade/workspace/sessions/{id}          remove its worktree; the branch stays
    POST   /api/ade/workspace/sessions/{id}/start    start its program again
    POST   /api/ade/workspace/sessions/{id}/stop     hang up its program
    POST   /api/ade/workspace/sessions/{id}/close    hang up its program and take it out of the page's list (nothing of
                                                     it is removed; no archive)
    POST   /api/ade/workspace/sessions/{id}/open     a closed session back in the list
    POST   /api/ade/workspace/sessions/{id}/title    {title}: name it; an empty name puts its task's first line back
    POST   /api/ade/workspace/sessions/{id}/group    {group}: join it to a group, to compare it with later sessions
    GET    /api/ade/workspace/model-providers        other providers' models for claude, and whether each has a key
    PUT    /api/ade/workspace/model-providers/{id}   {key}: the workspace keeps it; DELETE forgets it
    GET    /api/ade/workspace/linear                 whether a Linear key is kept there, and whose; never the key
    PUT    /api/ade/workspace/linear/key             {key}: kept once Linear takes it; DELETE forgets it
    GET    /api/ade/workspace/linear/issues?search=S&limit=N   the person's open issues, or a search of them
    GET    /api/ade/workspace/proofpane              the workspace's pairing with the owner's Proofpane organisation,
                                                     and what each claude start is given of its core; never the token
    PUT    /api/ade/workspace/proofpane/pair         {code, cloud_url?, holds?}: pair with a code Proofpane made, the
                                                     device's token kept there; DELETE unpairs
    PUT    /api/ade/workspace/proofpane/holds        {holds}: the commands a session's call waits on approval for
    GET    /api/ade/workspace/sessions/{id}/usage    the tokens its claude or codex wrote down, read only
    GET    /api/ade/workspace/map                    the control view's map: every session the workspace has, each
                                                     with its agents (names, times and counts, never a message's
                                                     words), as the workspace gives it
    GET    /api/ade/workspace/sessions/{id}/map      one session's tool calls and turns, for the map's opened session
    GET    /api/ade/workspace/github                 the GitHub account gh is signed in to there, and its repositories
    GET    /api/ade/workspace/claude                 whether Claude Code is signed in there, and as whom
    POST   /api/ade/workspace/claude/sign-out        sign Claude Code out there (its own sign-out)
    POST   /api/ade/workspace/sessions/{id}/sign-in-code  {code}: typed into its running Claude sign-in
    POST   /api/ade/workspace/sessions/{id}/finish   commit, then Verify the change and sign the manifest
    GET    /api/ade/workspace/sessions/{id}/update   how far behind the branch it started from it is
    POST   /api/ade/workspace/sessions/{id}/update   bring it up to date with that branch (its work committed, then
                                                     the branch merged in); stopped by conflicts, left for its agent
    POST   /api/ade/workspace/sessions/{id}/update/abort  give up a merge an update stopped on
    POST   /api/ade/workspace/sessions/{id}/land     a finished session's branch into the one it follows, a fast-forward,
                                                   in a repository added from the person's machine (workspace.land)
    GET    /api/ade/workspace/finishes               the account's finishes, newest first
    GET    /api/ade/workspace/finishes/{id}          one, with its manifest and signature
    WS     /api/ade/workspace/terminal               a session's terminal; the first message is
                                                     {"token", "session", "cols", "rows", "shell"?}: with "shell": N,
                                                     the session's shell N rather than its program
    POST   /api/ade/workspace/sessions/{id}/shell    {cols, rows, n?}: open a shell in its worktree, beside its
                                                     program (up to four; n opens that one again)
    POST   /api/ade/workspace/sessions/{id}/shell/{n}/stop   hang shell n up
    DELETE /api/ade/workspace/sessions/{id}/shell/{n}        hang it up and close it
    GET    /api/ade/workspace/sessions/{id}/chat?after=N   a chat's state and its events after N, a page at a time
    POST   /api/ade/workspace/sessions/{id}/chat     {text}: send a chat a message
    POST   /api/ade/workspace/sessions/{id}/chat/answer    {id, decision}: answer a question it asked
    POST   /api/ade/workspace/sessions/{id}/chat/interrupt stop what it is doing
    POST   /api/ade/workspace/sessions/{id}/chat/settings  {model?, effort?, permission_mode?}: change what is given
    GET    /api/ade/workspace/sessions/{id}/chat/mcp       its MCP servers: the repository's (approved or not), and what
                                                     claude says it has
    POST   /api/ade/workspace/sessions/{id}/chat/mcp       {action: approve | stop | add | remove, ...}: the owner's
                                                     choice about one (the agent's proofpane_workspace/mcp.py)
    POST   /api/ade/workspace/sessions/{id}/upload?name=N&to=chat  a file's bytes (at most 10 MB), into its
                                                     worktree; noted in its chat when attached to a message
    GET    /api/ade/workspace/capabilities           what the installed Claude Code offers a chat: its version, efforts,
                                                     permission modes (the pages build their choices from it)
    GET    /api/ade/workspace/claude/update          Claude Code kept current there: the mode, the versions, the last try
    POST   /api/ade/workspace/claude/update          {mode?: automatic | manual, now?: true}
    GET    /api/ade/workspace/asks                   whether its agents may ask for sessions, and the asks waiting
    POST   /api/ade/workspace/asks/settings          {on}: let them ask, or not (proofpane_workspace/asks.py)
    POST   /api/ade/workspace/asks/{id}/start        {cols?, rows?, effort?}: start the session an agent asked for,
                                                     a claude one at the effort the person chose; when the
                                                     workspace does not say it started with that effort, the answer's
                                                     effort_notice says so
    POST   /api/ade/workspace/asks/{id}/refuse       refuse it
    WS     /api/ade/workspace/chat                   every chat's events as they happen; the first message is {"token"}
    GET    /api/ade/workspace/sessions/{id}/diff     what its worktree changed since its base
    GET    /api/ade/workspace/sessions/{id}/files?path=P   a folder of its worktree; .../file?path=P one file, read only
    GET    /api/ade/workspace/sessions/{id}/paths    every file git sees in its worktree, to find one by name
    GET    /api/ade/workspace/sessions/{id}/search?q=Q&case=1&regex=1   the lines of its worktree that match
    GET    /api/ade/workspace/sessions/{id}/image?path=P   an image of its worktree, as its bytes, to show
    PUT    /api/ade/workspace/sessions/{id}/file     {path, text, base}: save one file from the editor, if it is still
                                                     the version the page opened (412 when it changed meanwhile)
    POST   /api/ade/workspace/sessions/{id}/discard  {path}: put one file back as it was where the session started
    GET    /api/ade/workspace/programs               which terminal agents it has: claude, codex, gemini, opencode,
                                                     grok, qwen, kimi, mcode
    POST   /api/ade/workspace/sessions/{id}/ship     push its branch and open a pull request
    GET    /api/ade/workspace/sessions/{id}/pr       its pull request as GitHub has it now: state, review, checks
    GET    /api/ade/workspace/sessions/{id}/pr/feedback  what its reviewers and failed checks said, for its agent
    POST   /api/ade/workspace/sessions/{id}/pr/merge {method: squash | merge | rebase, auto?}: merge it
    POST   /api/ade/workspace/sessions/{id}/pr/review {event: approve | request_changes | comment, body, url}: the
                                                     owner's own review of the pull request it started from
    GET    /api/ade/workspace/sessions/{id}/gate     what the workspace's gate refused it, and its agent's asks with
                                                     the person's answers as the app recorded them
    GET    /api/ade/workspace/sessions/{id}/ports    what its app listens on, for a preview (the desktop app's)
    GET    /api/ade/workspace/repos/{name}/issues?search=S   a repository's open issues on GitHub
    GET    /api/ade/workspace/repos/{name}/pulls?search=S    its open pull requests, to start a session from one
"""

import asyncio
import json
import re
import secrets
import sys
import threading
import time
import traceback
from typing import Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from websockets.exceptions import ConnectionClosed

from .. import auth, config, store
from ..deps import current_user
from . import agents, finishes, workspaces
from . import workspace_client as client

router = APIRouter(prefix="/api/ade/workspace")

FIRST_MESSAGE_WAIT = 10
MAX_HELLO = 8192            # a socket's first message: a sign-in and a session's id
SIGNIN_CHECK_EVERY = 10     # an open terminal's sign-in is checked again this often: signing out everywhere ends it
CLONE_WAIT = 660
FINISH_WAIT = 180
CHAT_SEND_WAIT = 90
SEARCH_WAIT = 30            # a search of a worktree's text, which the agent stops after 15 seconds
IMAGE_LIMIT = 10 * 1024 * 1024      # an image the page shows: the agent's own limit
IMAGE_KINDS = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp", "image/x-icon",
               "image/svg+xml", "application/pdf"}       # and a PDF, for the browser's own viewer
START_IN_REQUEST = 8        # a Start request waits this long; a phone drops a request held open for a minute or two
# how long a session's start again is waited for (start_session): the workspace's agent reads the
# claude it starts before it starts it, off its event loop, and each read is bounded by its claude_cli.TIMEOUT, 30
# seconds: `claude --version` for the gate's floor, then, for a session started from an ask at an effort, `--version`
# and `--help` for what that claude offers. Three reads at their bound are 90 seconds; with 30 more for the start
# itself, a start whose reads stay within their bound, and which then starts within 30 seconds, is not answered here
# as one that failed, and the words it says of an effort it did not give (`effort_notice`) reach the page. One that
# takes longer, waiting for the agent's lock while another session is being made, say, still is, though it may start.
# A phone may drop a request held open that long (START_IN_REQUEST's comment): the start still happens, but its words
# do not reach that page. A first Start from an ask is waited for ASK_START_WAIT, below. The app
# imports nothing of the workspace's, so the bound is written here
START_WAIT = 3 * 30 + 30
# how long a first Start from an agent's ask is waited for (ask_start): FINISH_WAIT, for the
# session's making, and the two reads the workspace's agent makes of the claude that starts to check the effort the
# person chose, its `--version` and its `--help` in the new worktree, each bounded by its claude_cli.TIMEOUT, 30
# seconds. Those reads hold the agent's lock, with the session's making; a Start waiting for that lock behind another
# is not counted here, so it can still be answered as one that failed though it starts
ASK_START_WAIT = FINISH_WAIT + 2 * 30
# an effort's name as the workspace's agent takes one (its workspace._EFFORT): a letter, then at most 31 letters or
# digits, never a flag's shape; what the app records of the effort a session started with has it, else none, and is
# the person's choice too
_EFFORT = re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}")
# a session's id, and nothing after it: `$` would take one with a newline at its end (a path's %0A), which then reached
# the call to the workspace, a 500 where the path goes on after the id and the id without its newline where it ends
# there
_SESSION = re.compile(r"^[0-9a-f]{12}\Z")
# a pull request's address, as the workspace gives it (workspace.PR_URL): on GitHub, or a GitHub Enterprise host
_PULL_URL = re.compile(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/pull/([0-9]+)")

# the starts under way, one per account at most, run in a thread of their own so none hangs on the request that
# asked for it: user id -> {"thread", "done", "error"}
_starts = {}
_starts_lock = threading.Lock()


def _run_start(p, user, job):
    try:
        p.ensure(user)
        p.touch(user)
    except workspaces.WorkspaceAccess as e:
        job["error"] = e
    except Exception as e:  # noqa: BLE001  a start failing in the background is told on the page, not lost
        print(f"workspace: a start failed: {type(e).__name__}: {e}", file=sys.stderr)
        job["error"] = workspaces.WorkspaceAccess(502, "The workspace could not start.")
    finally:
        job["done"] = True


def _start_state(user_id, running):
    """What the page is told about a start: under way, or failed (until the next start, or until it runs)."""
    with _starts_lock:
        job = _starts.get(user_id)
    if job is None:
        return {}
    if not job["done"]:
        return {"starting": True}
    return {"start_error": str(job["error"])} if job["error"] is not None and not running else {}


STARTING = "The workspace is starting: a moment, then try again."
DIFF_WAIT = 120     # git add --all on a temporary index, then two diffs, in a large worktree
GITHUB_WAIT = 60    # gh asking GitHub something, up to the agent's GITHUB_TIMEOUT
PAIR_WAIT = 40      # the agent exchanging a pairing code with Proofpane, up to its core_plugin.TIMEOUT
FEEDBACK_WAIT = 100  # its reviews and checks, then the failed jobs' logs, each up to GITHUB_TIMEOUT
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
SHIP_WAIT = 400     # a push and gh's pull request, each up to the agent's SHIP_TIMEOUT


def _starting(user_id):
    """Whether a start is under way for the account. Until it ends, its machine can be up while its agent does not
    answer yet: a Start's watch can read the sessions two seconds after the machine started, the agent listening a
    second later, and the page then says "The workspace answered 502". So until the start has seen the
    agent answer, the workspace is not said to run and nothing is sent to it."""
    with _starts_lock:
        job = _starts.get(user_id)
    return job is not None and not job["done"]


NEEDS_CLAUDE = ("claude", "chat")
CLAUDE_FIRST = "Sign in to Claude first, with Sign in to Claude at the top of the Workspace section."
STATUS_UNREAD = "Claude Code's sign-in could not be read just now: try again in a moment."
ACCOUNT_FIRST = ("The Claude account this session starts on is not signed in: sign it in under Accounts in Connections, "
                 "or start the session on another account.")
_ACCOUNT = re.compile(r"^(?:default|[0-9a-f]{12})\Z")


def _claude_ready(endpoint, account=None):
    """A claude session or a chat starts only once Claude Code is signed in in the workspace, on the account it starts
    on (`account`: an id or "default"; none, the one new sessions start on). Otherwise Claude Code asks for its sign-in
    in the session's own terminal, where a phone can neither copy its link nor paste its code, as a person who started
    a claude session before signing in met on a phone. The page's own sign-in shows the link and takes the code.
    Gives the account it checked, which the session is then started on: one chosen in between is not. Read anew
    (fresh=1), never the answer the agent keeps a few seconds for the pages' looks: a sign-in made
    just now, in a terminal of the workspace's own, starts the session; an agent from before that ignores it."""
    c = client.call(endpoint, "GET", f"/claude?account={quote(account or 'active', safe='')}&fresh=1", timeout=90)
    if not c.get("installed"):
        raise HTTPException(status_code=409, detail="claude is not installed in this workspace's image.")
    checked = c.get("account") or "default"
    if "signed_in" in c and c["signed_in"] is None:
        # Claude Code did not say in time: not a sign-out, and not a sign-in either
        raise HTTPException(status_code=503, detail=STATUS_UNREAD)
    if c.get("signed_in") is not True:
        raise HTTPException(status_code=409, detail=CLAUDE_FIRST if checked == "default" else ACCOUNT_FIRST)
    return checked if _ACCOUNT.match(str(checked)) else "default"


ACCOUNT_KINDS = ("claude", "chat", "codex")       # the programs that run on an account (proofpane_workspace/accounts.py)


def _account_recorded(user_id, session, account):
    """Record the account a session was made on, as the workspace said it made it: the app's own record, which the
    session's programs cannot write, to check it against when the session is started again."""
    if not _SESSION.match(str(session)) or not _ACCOUNT.match(str(account)):
        return
    try:
        with store.connect() as db:
            db.execute("INSERT OR REPLACE INTO ade_session_accounts (user_id, session, account, at) VALUES (?, ?, ?, ?)",
                       (user_id, session, account, int(time.time())))
    except Exception as e:  # noqa: BLE001  the session was made; only its record is missing
        print(f"workspace: session {session}'s account was not recorded: {type(e).__name__}: {e}", file=sys.stderr)


def _task_recorded(user_id, session, task):
    """Keep a session's task as the owner gave it (New session, or Continue in a new session): the app's own copy, which
    the session's programs cannot write (they can write the session's record), for the task a later Continue in a new
    session starts from."""
    if not _SESSION.match(str(session)) or not isinstance(task, str) or not task.strip():
        return
    try:
        with store.connect() as db:
            db.execute("INSERT OR REPLACE INTO ade_session_tasks (user_id, session, task, at) VALUES (?, ?, ?, ?)",
                       (user_id, session, task.strip(), int(time.time())))
    except Exception as e:  # noqa: BLE001  the session was made; only the app's copy of its task is missing
        print(f"workspace: session {session}'s task was not kept: {type(e).__name__}: {e}", file=sys.stderr)


def _task_of(user_id, session):
    """The task the owner gave a session, as the app kept it, or None for one made before the app kept them, or by an
    agent's ask."""
    with store.connect() as db:
        row = db.execute("SELECT task FROM ade_session_tasks WHERE user_id = ? AND session = ?",
                         (user_id, session)).fetchone()
    return row["task"] if row else None


def _account_of(user_id, session):
    """The account the app recorded for a session, or None for one made before it kept them."""
    with store.connect() as db:
        row = db.execute("SELECT account FROM ade_session_accounts WHERE user_id = ? AND session = ?",
                         (user_id, session)).fetchone()
    return row["account"] if row else None


def _page_state(p, user):
    st = p.status(user)
    more = _start_state(user["id"], st.get("running"))
    if more.get("starting"):
        st = dict(st, running=False)
    return dict(st, enabled=True, allowed=True, **more)


class RepoIn(BaseModel):
    url: str


class LinkIn(BaseModel):
    path: str = Field(max_length=4096)


class ReviewIn(BaseModel):
    event: str = Field(pattern="^(approve|request_changes|comment)$")
    body: str = Field(default="", max_length=15000)     # workspace.MAX_REVIEW
    url: str = Field(max_length=300)


class SessionIn(BaseModel):
    repo: Optional[str] = None      # none for a shell or GitHub's sign-in, which run in the workspace's root
    program: str
    task: str
    base: Optional[str] = None
    pull: Optional[int] = None      # a pull request's number on GitHub: the session starts from its head
    # a cloud session of the person's Claude account to continue here (Claude Code's --teleport): its id or its
    # claude.ai/code link; "" for Claude Code's own picker; the agent checks it
    teleport: Optional[str] = Field(default=None, max_length=300)
    cols: int = 100
    rows: int = 30
    chat_settings: Optional[dict] = None    # a chat's model, effort and permission mode: the agent checks them
    group: Optional[str] = None             # sessions started together on one task, to compare: the agent checks it
    model_provider: Optional[str] = None    # a claude session on another provider's model: the agent checks it
    custom: Optional[str] = None            # an agent of the person's own, by its id (custom_agents.py)
    # the Claude Code or Codex account it runs on: an id, or "default"; none, the one new sessions start on
    account: Optional[str] = Field(default=None, pattern=r"^(?:default|[0-9a-f]{12})$")


class AccountIn(BaseModel):
    kind: str = Field(pattern="^(?:claude|codex)$")
    name: str = Field(max_length=40)                 # the agent says what a name is


class ChooseIn(BaseModel):
    kind: str = Field(pattern="^(?:claude|codex)$")
    account: str = Field(pattern=r"^(?:default|[0-9a-f]{12})$")


class AccountSignOutIn(BaseModel):
    kind: Optional[str] = Field(default=None, pattern="^(?:claude|codex)$")    # the default account's program


class KeyIn(BaseModel):
    key: str = Field(max_length=400)        # the agent says what a key is; it is kept there, never sent back


class PairIn(BaseModel):
    # the agent says what a code, an address and a hold are (core_plugin.py); the device's token it gets for them is
    # kept there, never sent here
    code: str = Field(max_length=64)
    cloud_url: Optional[str] = Field(default=None, max_length=300)
    holds: Optional[list[str]] = Field(default=None, max_length=20)


class HoldsIn(BaseModel):
    holds: list[str] = Field(max_length=20)


class ChatSettingsIn(BaseModel):
    model: Optional[str] = None     # the agent checks each; one given empty goes back to Claude Code's own
    effort: Optional[str] = None
    permission_mode: Optional[str] = None
    may_dispatch: Optional[bool] = None     # whether it may start agents' runs (proofpane_workspace/agents.py)


class SizeIn(BaseModel):
    cols: int = 100
    rows: int = 30


class AskStartIn(SizeIn):
    # the person's choice at Start for an agent's ask: an effort, for a Claude Code ask alone; none given and
    # "" are Claude Code's own default, never the ask's (a page from before this sends {}). The agent refuses one that
    # does not have a level's name's shape, and one that has it that the claude that starts does not offer, read in the
    # new worktree, the ask then waiting again. A model, a permission mode or leave to start agents'
    # runs given is passed on for the agent to refuse, as it refuses any setting but an effort on a session started from
    # an ask (may_dispatch false and an empty value being the default it takes), rather than dropped here, as SizeIn
    # drops any other field
    effort: Optional[str] = Field(default=None, max_length=32)
    model: Optional[str] = Field(default=None, max_length=200)
    permission_mode: Optional[str] = Field(default=None, max_length=32)
    may_dispatch: Optional[bool] = None


class SaveIn(BaseModel):
    path: str = Field(max_length=4096)
    text: str = Field(max_length=1024 * 1024)       # the agent's own limit, 512 KB of UTF-8, is the one it says
    base: str = Field(max_length=64)


class ShellIn(SizeIn):
    n: Optional[int] = None         # a shell to open again in its place; none, the first free one


class AsksIn(BaseModel):
    on: bool


class TitleIn(BaseModel):
    title: str = Field(default="", max_length=400)    # the agent's own limit is shorter, and it says so


class GroupIn(BaseModel):
    group: str = Field(max_length=64)                   # the agent says what a group is


class CodeIn(BaseModel):
    code: str       # the agent checks its shape; the app keeps it nowhere


class ShipIn(BaseModel):
    title: Optional[str] = None     # the agent checks each; none given: the task's first line and the rest of it
    body: Optional[str] = None
    base: Optional[str] = None      # the branch the pull request goes into; none: the repository's default
    draft: bool = False


class ChatIn(BaseModel):
    text: str       # the agent checks its size; the app keeps it nowhere


class AnswerIn(BaseModel):
    id: str
    decision: str


class McpSpecIn(BaseModel):
    name: str
    scope: str                      # repository or workspace
    command: Optional[str] = None   # a command line, or
    url: Optional[str] = None       # an address
    transport: Optional[str] = None  # http (the default) or sse, for an address
    lines: Optional[str] = None     # its environment (NAME=value) or headers (Name: value), one per line


class McpIn(BaseModel):
    action: str                     # approve, stop, add or remove; the agent checks each field
    name: Optional[str] = None
    fingerprint: Optional[str] = None
    scope: Optional[str] = None
    spec: Optional[McpSpecIn] = None


def workspace_user(user=Depends(current_user)):
    workspaces.for_user(user)
    return user


def _session_id(session_id):
    if not _SESSION.match(session_id):
        raise HTTPException(status_code=404, detail="No such session.")
    return session_id


def _shell(n):
    if not 1 <= n <= client.MAX_SHELLS:
        raise HTTPException(status_code=404, detail="No such shell.")
    return n


def _running(user):
    p = workspaces.for_user(user)
    if _starting(user["id"]):
        raise HTTPException(status_code=409, detail=STARTING)
    endpoint = p.endpoint(user)
    if endpoint is None:
        raise HTTPException(status_code=409, detail="The workspace is not running: start it first.")
    p.touch(user)
    return endpoint


@router.get("")
def status(user=Depends(current_user)):
    """The workspace's state for the page, with the account's choice of where its work lives (storage.py) wherever
    the deployment has a workspace: a start is refused for an account whose choice is not `hosted`."""
    from . import storage
    p = workspaces.provider()
    if p is None:
        return {"enabled": False}
    try:
        p.check_user(user)
    except workspaces.WorkspaceAccess as e:
        return {"enabled": True, "allowed": False, "detail": str(e), "choice": storage.choice_for(user)}
    return dict(_page_state(p, user), choice=storage.choice_for(user))


def start_job(p, user):
    """The account's start under way, or a new one: one start at a time per account, so a second press, or a move
    carried on into the workspace (moves.py), joins the first. {"thread", "done", "error"}."""
    with _starts_lock:
        job = _starts.get(user["id"])
        if job is None or job["done"]:
            job = {"done": False, "error": None}
            job["thread"] = threading.Thread(target=_run_start, args=(p, user, job), name="workspace-start",
                                             daemon=True)
            _starts[user["id"]] = job
            job["thread"].start()
    return job


@router.post("/start")
def start(user=Depends(workspace_user)):
    p = workspaces.for_user(user)
    job = start_job(p, user)
    job["thread"].join(START_IN_REQUEST)
    if job["done"] and job["error"] is not None:
        raise job["error"]
    from . import storage
    return dict(_page_state(p, user), choice=storage.choice_for(user))     # the page keeps it as the status's


@router.post("/stop")
def stop(user=Depends(workspace_user)):
    from . import storage
    return dict(workspaces.for_user(user).stop(user), enabled=True, allowed=True, choice=storage.choice_for(user))


@router.get("/repos")
def repos(user=Depends(workspace_user)):
    from . import moves
    answer = client.call(_running(user), "GET", "/repos")
    moves.note_repos(user["id"], answer)            # what a move's repository is held to before it is held
    return answer


def _changing_repos(user, method, path, body=None, timeout=30):
    """A call that changes the workspace's repositories: what a move is held to (moves.note_repos) is forgotten before
    and after it, so a list the page read while it ran is not taken for the one after it."""
    from . import moves
    moves.forget_repos(user["id"])
    try:
        return client.call(_running(user), method, path, body, timeout=timeout)
    finally:
        moves.forget_repos(user["id"])


@router.post("/repos")
def clone(body: RepoIn, user=Depends(workspace_user)):
    return _changing_repos(user, "POST", "/repos", {"url": body.url}, timeout=CLONE_WAIT)


@router.post("/repos/link")
def link(body: LinkIn, user=Depends(workspace_user)):
    """Add a repository already on the workspace's machine, where it is: the local provider's only (the agent says
    no to it elsewhere), whose workspace is on the person's own machine and whose account is its admin."""
    return _changing_repos(user, "POST", "/repos/link", {"path": body.path})


@router.delete("/repos/{name}")
def unlink(name: str, user=Depends(workspace_user)):
    """Take a repository added from the machine out of the workspace: its link, never its folder."""
    if not _REPO.match(name) or ".." in name:
        raise HTTPException(status_code=404, detail="No such repository.")
    return _changing_repos(user, "DELETE", f"/repos/{name}")


@router.get("/sessions")
def sessions(user=Depends(workspace_user)):
    got = client.call(_running(user), "GET", "/sessions")
    if isinstance(got, dict) and isinstance(got.get("sessions"), list):
        _asked_by_as_answered(user["id"], got["sessions"])
        _pulls_as_recorded(user["id"], got["sessions"])
    return got


@router.post("/sessions")
def new_session(body: SessionIn, user=Depends(workspace_user)):
    endpoint = _running(user)
    _asks_as_chosen(endpoint, user)         # a token goes to a program only as the person chose
    # without a repository the agent refuses it and says why; on another provider's model, Claude's sign-in is not used
    checked = None
    if body.program in NEEDS_CLAUDE and body.repo and not body.model_provider:
        checked = _claude_ready(endpoint, body.account)
    sent = body.model_dump()
    if checked:
        sent["account"] = checked           # the account just checked, never one chosen since
    if body.program == "custom":
        # its command from the app's own record of the person's agents, never from anything the page sends
        from . import custom_agents
        own = custom_agents.get(user["id"], body.custom)
        if own is None:
            raise HTTPException(status_code=404, detail="No such agent of yours.")
        sent["custom"] = {"name": own["name"], "command": own["command"], "task": own["task"]}
    else:
        sent.pop("custom", None)
    made = client.call(endpoint, "POST", "/sessions", sent, timeout=FINISH_WAIT)
    if body.program in ACCOUNT_KINDS and isinstance(made, dict):
        _account_recorded(user["id"], made.get("id"), made.get("account") or "default")
    if isinstance(made, dict) and made.get("repo"):
        _task_recorded(user["id"], made.get("id"), body.task)
    if (body.chat_settings or {}).get("may_dispatch") is True and isinstance(made, dict) \
            and isinstance(made.get("id"), str) and _SESSION.match(made["id"]):
        agents.grant_chat(user["id"], made["id"])      # a chat made able to start agents' runs (chat_settings below)
    if body.pull is not None and isinstance(made, dict):
        url = made.get("pull_url")
        if isinstance(url, str) and url.endswith(f"/pull/{body.pull}"):
            _pull_recorded(user["id"], made.get("id"), "started", url)
    if isinstance(made, dict):
        _pulls_as_recorded(user["id"], [made])
    return made


@router.get("/sessions/{session_id}")
def session(session_id: str, user=Depends(workspace_user)):
    got = client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}")
    _asked_by_as_answered(user["id"], [got])
    _pulls_as_recorded(user["id"], [got])
    return got


@router.delete("/sessions/{session_id}")
def remove_session(session_id: str, user=Depends(workspace_user)):
    sid = _session_id(session_id)
    got = client.call(_running(user), "DELETE", f"/sessions/{sid}", timeout=FINISH_WAIT)
    try:
        with store.connect() as db:
            db.execute("DELETE FROM ade_session_pulls WHERE user_id = ? AND session = ?", (user["id"], sid))
            db.execute("DELETE FROM ade_session_accounts WHERE user_id = ? AND session = ?", (user["id"], sid))
            db.execute("DELETE FROM ade_session_tasks WHERE user_id = ? AND session = ?", (user["id"], sid))
    except Exception as e:  # noqa: BLE001  the session is gone; a record of it left over names nothing
        print(f"workspace: session {sid}'s pull requests were not forgotten: {type(e).__name__}: {e}", file=sys.stderr)
    return got


@router.post("/sessions/{session_id}/start")
def start_session(session_id: str, body: SizeIn = SizeIn(), user=Depends(workspace_user)):
    endpoint = _running(user)
    sid = _session_id(session_id)
    _asks_as_chosen(endpoint, user)
    found = client.call(endpoint, "GET", f"/sessions/{sid}")
    if found.get("program") in ACCOUNT_KINDS and not found.get("model_provider"):
        mine = _account_of(user["id"], sid)
        theirs = found.get("account") or "default"
        if mine is not None and theirs != mine:
            raise HTTPException(status_code=409, detail=(
                "This session's account in the workspace is not the one it was started on: it is not started "
                "again. Start a new session."))
        if found.get("program") in NEEDS_CLAUDE:
            _claude_ready(endpoint, mine or theirs)            # the account it runs on, again
    sent = body.model_dump()
    effort = _started_effort(user["id"], sid)
    if effort:
        # a session started from an agent's ask at an effort: the app's own copy, which the session's
        # programs cannot write, for the workspace to give at this start when it keeps none of its own (it started
        # since); never one read from the session's folder. The workspace gives either copy only while its claude
        # offers it, and its answer, passed back as it is, says in `effort_notice` when it gave none it would have;
        # this record is left as it is. That claude is the one the start runs, read at this start in the session's
        # worktree
        sent["effort"] = effort
    # waited for as long as the workspace's reads of its claude can take and 30 seconds more (START_WAIT), so a start
    # within that is not said to have failed, and its effort_notice reaches the page
    return client.call(endpoint, "POST", f"/sessions/{sid}/start", sent, timeout=START_WAIT)


@router.post("/sessions/{session_id}/stop")
def stop_session(session_id: str, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/stop")


@router.post("/sessions/{session_id}/close")
def close_session(session_id: str, user=Depends(workspace_user)):
    """Its program stopped and the session out of the page's list, nothing of it removed."""
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/close")


@router.post("/sessions/{session_id}/open")
def open_session(session_id: str, user=Depends(workspace_user)):
    """A closed session back in the page's list."""
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/open")


@router.post("/sessions/{session_id}/title")
def session_title(session_id: str, body: TitleIn, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/title", body.model_dump())


@router.get("/model-providers")
def model_providers(user=Depends(workspace_user)):
    """Other providers' models for claude sessions, and whether the workspace keeps a key for each (never the key)."""
    return client.call(_running(user), "GET", "/model-providers")


@router.put("/model-providers/{provider}")
def keep_model_key(provider: str, body: KeyIn, user=Depends(workspace_user)):
    """The person's own key for one, given to their workspace to keep: the app neither stores nor logs it."""
    return client.call(_running(user), "PUT", f"/model-providers/{_provider_name(provider)}", body.model_dump())


# Linear, for a session's task (the agent's linear.py): the person's own key, kept in the workspace

@router.get("/linear")
def linear_status(user=Depends(workspace_user)):
    return client.call(_running(user), "GET", "/linear", timeout=GITHUB_WAIT)


@router.put("/linear/key")
def linear_key(body: KeyIn, user=Depends(workspace_user)):
    return client.call(_running(user), "PUT", "/linear/key", body.model_dump(), timeout=GITHUB_WAIT)


@router.delete("/linear/key")
def linear_forget(user=Depends(workspace_user)):
    return client.call(_running(user), "DELETE", "/linear/key")


@router.get("/linear/issues")
def linear_issues(search: Optional[str] = None, limit: int = 50, user=Depends(workspace_user)):
    query = f"?limit={max(1, min(limit, 50))}" + (f"&search={quote(search, safe='')}" if search else "")
    return client.call(_running(user), "GET", f"/linear/issues{query}", timeout=GITHUB_WAIT)


# Proofpane's core (the agent's core_plugin.py): the workspace paired with the owner's Proofpane organisation, its
# device's token kept there; the plugin it gives each claude start

@router.get("/proofpane")
def proofpane_status(user=Depends(workspace_user)):
    return client.call(_running(user), "GET", "/proofpane")


@router.put("/proofpane/pair")
def proofpane_pair(body: PairIn, user=Depends(workspace_user)):
    return client.call(_running(user), "PUT", "/proofpane/pair", body.model_dump(), timeout=PAIR_WAIT)


@router.delete("/proofpane/pair")
def proofpane_unpair(user=Depends(workspace_user)):
    return client.call(_running(user), "DELETE", "/proofpane/pair")


@router.put("/proofpane/holds")
def proofpane_holds(body: HoldsIn, user=Depends(workspace_user)):
    return client.call(_running(user), "PUT", "/proofpane/holds", body.model_dump())


@router.delete("/model-providers/{provider}")
def forget_model_key(provider: str, user=Depends(workspace_user)):
    return client.call(_running(user), "DELETE", f"/model-providers/{_provider_name(provider)}")


def _provider_name(provider):
    if not re.fullmatch(r"[a-z0-9]{2,20}", provider):
        raise HTTPException(status_code=404, detail="No such model provider.")
    return provider


@router.post("/sessions/{session_id}/group")
def session_group(session_id: str, body: GroupIn, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/group", body.model_dump())


@router.post("/sessions/{session_id}/shell")
def session_shell(session_id: str, body: ShellIn = ShellIn(), user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/shell",
                       body.model_dump(exclude_none=True))


@router.post("/sessions/{session_id}/shell/{n}/stop")
def session_shell_stop(session_id: str, n: int, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/shell/{_shell(n)}/stop")


@router.delete("/sessions/{session_id}/shell/{n}")
def session_shell_close(session_id: str, n: int, user=Depends(workspace_user)):
    return client.call(_running(user), "DELETE", f"/sessions/{_session_id(session_id)}/shell/{_shell(n)}")


@router.get("/github")
def github(user=Depends(workspace_user)):
    return client.call(_running(user), "GET", "/github", timeout=90)


@router.get("/capabilities")
def capabilities(user=Depends(workspace_user)):
    return client.call(_running(user), "GET", "/capabilities", timeout=90)


class UpdateIn(BaseModel):
    mode: Optional[str] = None      # the agent checks it
    now: bool = False


@router.get("/claude/update")
def claude_update(user=Depends(workspace_user)):
    return client.call(_running(user), "GET", "/claude/update", timeout=90)


@router.post("/claude/update")
def claude_update_set(body: UpdateIn, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", "/claude/update", body.model_dump(exclude_unset=True), timeout=90)


# agents that ask for sessions (the agent's asks.py): the person turns it on, and starts or refuses each ask

_ASK = re.compile(r"^[0-9a-f]{12}\Z")


def _ask_id(ask_id):
    if not _ASK.match(ask_id or ""):
        raise HTTPException(status_code=404, detail="No such ask.")
    return ask_id


def _asks_choice(user_id):
    """The person's choice: whether their workspace's agents may ask for sessions. Kept here, not in the workspace,
    whose programs run as its agent's user and could change a file there."""
    with store.connect() as db:
        row = db.execute("SELECT asks_on FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row["asks_on"])


def _asks_as_chosen(endpoint, user):
    """The workspace's asks, its switch put back to the person's choice when it is not (an agent started since,
    which begins with asks off, or one whose switch was changed): before every session starts, and on each look."""
    view = client.call(endpoint, "GET", "/asks")
    chosen = _asks_choice(user["id"])
    if isinstance(view, dict) and view.get("on") is not chosen:
        view = client.call(endpoint, "POST", "/asks/settings", {"on": chosen})
    return view


def _answered(user_id, ask, answer, session=None, effort=None):
    """Record the person's answer to an ask, as they give it: `started` (with the session it started, and the effort it
    started with: none for Claude Code's default) or `refused`. After the workspace has done it, so a record
    that cannot be written is said in the log, not as a failed answer."""
    try:
        with store.connect() as db:
            db.execute("INSERT OR REPLACE INTO ade_ask_answers "
                       "(user_id, ask_id, from_session, program, answer, session, effort, at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (user_id, str(ask.get("id")), str(ask.get("from") or ""), str(ask.get("program") or ""), answer,
                        session, effort, int(time.time())))
    except Exception as e:  # noqa: BLE001  the answer was given; only its record is missing
        print(f"workspace asks: the answer to {ask.get('id')} was not recorded: {type(e).__name__}: {e}",
              file=sys.stderr)


def _started_effort(user_id, session):
    """The effort the app recorded a session started from an agent's ask with, or None: Claude Code's
    default, a session started before the app kept it, or one the person started themselves."""
    with store.connect() as db:
        row = db.execute("SELECT effort FROM ade_ask_answers WHERE user_id = ? AND session = ? AND answer = 'started'",
                         (user_id, session)).fetchone()
    return row["effort"] if row else None


def _answers(user_id):
    """The person's answers to their agents' asks, as the app recorded them: the app's own record, not the
    workspace's, whose programs run as its agent's user and could write in a session's folder."""
    with store.connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT ask_id, from_session, program, answer, session, at FROM ade_ask_answers WHERE user_id = ? "
            "ORDER BY at, rowid", (user_id,))]


def _asked_by_as_answered(user_id, views):
    """Each session's `asked_by` as the app recorded it when the person started the session from an ask, and none
    when it did not: the workspace reads it from the session's folder, where the session's programs could write one."""
    started = {a["session"]: a["from_session"] for a in _answers(user_id) if a["answer"] == "started" and a["session"]}
    for view in views:
        if isinstance(view, dict) and isinstance(view.get("id"), str):
            view["asked_by"] = started.get(view["id"])
    return views


def _pull_recorded(user_id, session, kind, url):
    """Record a session's pull request as the app saw it given: `started` (the one the person started the session
    from) or `shipped` (the one the person shipped it as). The workspace keeps one in the session's folder too, where
    the session's programs could write another; what the page is shown and what gh acts on is this one."""
    if not isinstance(url, str) or not _PULL_URL.fullmatch(url) or not _SESSION.match(str(session)):
        print(f"workspace: session {session}'s pull request was not recorded: not one", file=sys.stderr)
        return
    try:
        with store.connect() as db:
            db.execute("INSERT OR REPLACE INTO ade_session_pulls (user_id, session, kind, url, at) VALUES (?, ?, ?, ?, ?)",
                       (user_id, session, kind, url, int(time.time())))
    except Exception as e:  # noqa: BLE001  it was made; only its record is missing, which the page then says
        print(f"workspace: session {session}'s pull request was not recorded: {type(e).__name__}: {e}", file=sys.stderr)


def _pulls(user_id):
    """{session: {kind: url}}, the person's sessions' pull requests as the app recorded them."""
    out = {}
    with store.connect() as db:
        for r in db.execute("SELECT session, kind, url FROM ade_session_pulls WHERE user_id = ?", (user_id,)):
            out.setdefault(r["session"], {})[r["kind"]] = r["url"]
    return out


def _the_pull(user_id, session_id, shipped=False):
    """The pull request the app recorded for a session: the one it shipped as, else (unless `shipped`) the one it
    started from; or a refusal saying there is none."""
    mine = _pulls(user_id).get(session_id, {})
    url = mine.get("shipped") or (None if shipped else mine.get("started"))
    if url is None:
        raise HTTPException(status_code=400, detail=(
            "The app has no record of shipping this session: ship it here, and its pull request is recorded." if shipped
            else "The app has no record of this session's pull request: ship it here, or start a session from the pull "
                 "request, and it is recorded."))
    return url


def _pulls_as_recorded(user_id, views):
    """Each session's pull requests as the app recorded them, over the workspace's: `shipped.url` the one it shipped
    as (and `shipped` none when the app recorded none), `pull` and `pull_url` the one it started from. The workspace
    reads them from the session's folder, where the session's programs could write others."""
    recorded = _pulls(user_id)
    for view in views:
        if not isinstance(view, dict) or not isinstance(view.get("id"), str):
            continue
        mine = recorded.get(view["id"], {})
        started = _PULL_URL.fullmatch(mine.get("started") or "")
        view["pull"], view["pull_url"] = (int(started.group(1)), started.group(0)) if started else (None, None)
        if isinstance(view.get("shipped"), dict):
            view["shipped"] = dict(view["shipped"], url=mine["shipped"]) if mine.get("shipped") else None
    return views


@router.get("/asks")
def asks(user=Depends(workspace_user)):
    return _asks_as_chosen(_running(user), user)


@router.post("/asks/settings")
def asks_settings(body: AsksIn, user=Depends(workspace_user)):
    endpoint = _running(user)
    with store.connect() as db:
        db.execute("UPDATE users SET asks_on = ? WHERE id = ?", (1 if body.on else 0, user["id"]))
    return client.call(endpoint, "POST", "/asks/settings", body.model_dump())


@router.post("/asks/{ask_id}/start")
def ask_start(ask_id: str, body: AskStartIn = AskStartIn(), user=Depends(workspace_user)):
    endpoint = _running(user)
    aid = _ask_id(ask_id)
    ask = next((a for a in _asks_as_chosen(endpoint, user).get("asks", []) if a.get("id") == aid), None)
    if ask is None:
        raise HTTPException(status_code=404, detail="That ask has been answered, or is gone.")
    if ask.get("program") in NEEDS_CLAUDE:
        _claude_ready(endpoint)             # as a session the person starts themselves (new_session)
    # the person's choice passed on as given, the settings not given left out; the workspace refuses what it may not
    # take, and the ask then waits as it was
    view = client.call(endpoint, "POST", f"/asks/{aid}/start", body.model_dump(exclude_none=True),
                       timeout=ASK_START_WAIT)
    # the effort it started with, as the workspace said it started it (an agent from an older release says none, having
    # given none), as its account is recorded below: the app's own copy for its later starts, kept only when it is the
    # person's choice and has a level's name's shape, else none (were any value of that shape kept, a workspace that
    # said another effort than the one chosen would have it given at every later start the app's copy decides), so the
    # app never sends a later start an effort the person did not choose, or one the workspace
    # would refuse
    said = view.get("effort") if isinstance(view, dict) else None
    effort = said if isinstance(said, str) and _EFFORT.fullmatch(said) and said == body.effort else None
    _answered(user["id"], ask, "started", view.get("id") if isinstance(view, dict) else None, effort)
    if isinstance(view, dict) and view.get("program") in ACCOUNT_KINDS:
        _account_recorded(user["id"], view.get("id"), view.get("account") or "default")
    if body.effort and isinstance(view, dict) and said != body.effort:
        # the person chose an effort and the workspace does not say the session started with it:
        # they are told, the record above holding none. When it says the very value chosen, nothing
        # is said, whether or not the record keeps it: a value without a level's name's shape, which only a workspace
        # that took it would say, is not kept, and the words would be "started at X, not at X". The workspace says no
        # words of its own of an effort at a first Start: one the claude that starts does not offer is refused there
        view = dict(view, effort_notice=_effort_not_taken(body.effort, view))
    return view


def _effort_not_taken(chosen, view):
    """What the person is told when the workspace does not say a session started from an ask started at the effort they
    chose: an agent from an older release, which takes no effort at Start, says none at all."""
    started = view.get("effort") if isinstance(view.get("effort"), str) and view.get("effort") else None
    at = f"{started} effort" if started else "Claude Code's default effort"
    said = f"The session started at {at}, not at {chosen}, the effort you chose."
    if "effort" not in view:
        said += " This workspace's agent is older than the app and takes no effort at Start."
    return said


@router.post("/asks/{ask_id}/refuse")
def ask_refuse(ask_id: str, user=Depends(workspace_user)):
    endpoint = _running(user)
    aid = _ask_id(ask_id)
    ask = next((a for a in client.call(endpoint, "GET", "/asks").get("asks", []) if a.get("id") == aid), None)
    view = client.call(endpoint, "POST", f"/asks/{aid}/refuse")
    if ask is not None:
        _answered(user["id"], ask, "refused")
    return view


@router.get("/claude")
def claude(account: str = "active", user=Depends(workspace_user)):
    """Claude Code's sign-in on an account: by default the one new sessions start on, which is what the page asks."""
    if account != "active" and not _ACCOUNT.match(account):
        raise HTTPException(status_code=404, detail="No such account.")
    return client.call(_running(user), "GET", f"/claude?account={account}", timeout=90)


@router.get("/sessions/{session_id}/chat")
def chat_log(session_id: str, after: int = 0, user=Depends(workspace_user)):
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/chat?after={max(0, after)}")


@router.post("/sessions/{session_id}/chat")
def chat_send(session_id: str, body: ChatIn, user=Depends(workspace_user)):
    # the first message of a chat starts its claude, which can take a few seconds
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/chat", {"text": body.text},
                       timeout=CHAT_SEND_WAIT)


@router.post("/sessions/{session_id}/chat/answer")
def chat_answer(session_id: str, body: AnswerIn, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/chat/answer",
                       {"id": body.id, "decision": body.decision})


@router.get("/sessions/{session_id}/chat/mcp")
def chat_mcp(session_id: str, user=Depends(workspace_user)):
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/chat/mcp", timeout=60)


@router.post("/sessions/{session_id}/chat/mcp")
def chat_mcp_choose(session_id: str, body: McpIn, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/chat/mcp",
                       body.model_dump(exclude_none=True), timeout=90)


@router.post("/sessions/{session_id}/chat/settings")
def chat_settings(session_id: str, body: ChatSettingsIn, user=Depends(workspace_user)):
    endpoint = _running(user)
    if body.may_dispatch:
        # kept here first: the runs the chat starts are taken into the agents' records only for a chat its owner let
        agents.grant_chat(user["id"], _session_id(session_id))
    elif body.may_dispatch is False:
        agents.revoke_chat(user["id"], _session_id(session_id))
    return client.call(endpoint, "POST", f"/sessions/{_session_id(session_id)}/chat/settings",
                       body.model_dump(exclude_unset=True))


MAX_UPLOAD = 10 * 1024 * 1024       # the agent's own cap too (workspace.MAX_UPLOAD)


@router.post("/sessions/{session_id}/upload")
async def upload(session_id: str, request: Request, name: str = "", to: str = "", user=Depends(workspace_user)):
    sid = _session_id(session_id)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail=f"A file is at most {MAX_UPLOAD // (1024 * 1024)} MB.")
    endpoint = await run_in_threadpool(_running, user)
    return await run_in_threadpool(client.upload, endpoint, f"/sessions/{sid}/upload", name, bytes(data),
                                   to_chat=to == "chat")


@router.post("/sessions/{session_id}/chat/interrupt")
def chat_interrupt(session_id: str, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/chat/interrupt", {})


@router.post("/claude/sign-out")
def claude_sign_out(user=Depends(workspace_user)):
    return client.call(_running(user), "POST", "/claude/sign-out", {}, timeout=90)


# accounts: more than one sign-in to Claude Code or Codex in the workspace, each in a folder of its own there
# (proofpane_workspace/accounts.py); the person's own, as their workspace is

@router.get("/accounts")
def accounts(status: bool = True, user=Depends(workspace_user)):
    return client.call(_running(user), "GET", "/accounts" + ("" if status else "?status=0"), timeout=120)


@router.post("/accounts")
def account_add(body: AccountIn, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", "/accounts", body.model_dump())


@router.post("/accounts/choose")
def account_choose(body: ChooseIn, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", "/accounts/choose", body.model_dump())


@router.post("/accounts/{account_id}/sign-out")
def account_sign_out(account_id: str, body: AccountSignOutIn = AccountSignOutIn(), user=Depends(workspace_user)):
    if not _ACCOUNT.match(account_id):
        raise HTTPException(status_code=404, detail="No such account.")
    return client.call(_running(user), "POST", f"/accounts/{account_id}/sign-out", body.model_dump(), timeout=90)


@router.delete("/accounts/{account_id}")
def account_remove(account_id: str, user=Depends(workspace_user)):
    if not _ACCOUNT.match(account_id):
        raise HTTPException(status_code=404, detail="No such account.")
    return client.call(_running(user), "DELETE", f"/accounts/{account_id}", timeout=120)      # the default: none


@router.post("/sessions/{session_id}/sign-in-code")
def sign_in_code(session_id: str, body: CodeIn, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/sign-in-code", {"code": body.code})


@router.get("/sessions/{session_id}/usage")
def session_usage(session_id: str, user=Depends(workspace_user)):
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/usage")


# what the map's two routes say of a workspace whose agent has neither, one from before them: it answers a route it does
# not have in its framework's own plain words, which the client cannot show (workspace_client._refused's "The workspace
# answered 404.")
MAP_NOT_YET = "This workspace does not draw the map yet: it comes with a later version of the workspace."


def _map_call(user, path):
    try:
        return client.call(_running(user), "GET", path)
    except client.WorkspaceCallError as e:
        if e.answered == 404:
            raise client.WorkspaceCallError(404, MAP_NOT_YET) from None
        raise


@router.get("/map")
def workspace_map(user=Depends(workspace_user)):
    """The control view's map: a call through to the account's own workspace, whose agent reads it from its
    sessions' own files. The page asks for it every 3 seconds while it shows it."""
    return _map_call(user, "/map")


@router.get("/sessions/{session_id}/map")
def session_map(session_id: str, user=Depends(workspace_user)):
    """One session's calls and turns in the map, while the owner has it open there: looked for in the account's own
    workspace alone, so another person's session is not found (404)."""
    return _map_call(user, f"/sessions/{_session_id(session_id)}/map")


@router.get("/sessions/{session_id}/diff")
def session_changes(session_id: str, user=Depends(workspace_user)):
    """A session's change against where it started, committed or not, for the page to review (workspace.changes)."""
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/diff", timeout=DIFF_WAIT)


@router.get("/sessions/{session_id}/files")
def session_files(session_id: str, path: str = "", user=Depends(workspace_user)):
    """A folder of a session's worktree (workspace.files)."""
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/files?path={quote(path)}")


@router.get("/sessions/{session_id}/search")
def session_search(session_id: str, q: str = "", case: bool = False, regex: bool = False, user=Depends(workspace_user)):
    """The lines of a session's worktree that match (workspace.search)."""
    query = f"?q={quote(q, safe='')}" + ("&case=1" if case else "") + ("&regex=1" if regex else "")
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/search{query}", timeout=SEARCH_WAIT)


@router.get("/sessions/{session_id}/file")
def session_file(session_id: str, path: str = "", user=Depends(workspace_user)):
    """One file of a session's worktree, its text when it is small and not binary (workspace.file)."""
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/file?path={quote(path)}")


@router.get("/sessions/{session_id}/image")
def session_image(session_id: str, path: str = "", user=Depends(workspace_user)):
    """An image of a session's worktree, or a PDF (workspace.image), sent so that nothing in it can run: an SVG's
    scripts stay text, the browser takes the type as given, and the page shows an image as an image only and a PDF in
    the browser's own viewer, typed as a PDF by the page itself."""
    data, kind = client.fetch_bytes(_running(user), f"/sessions/{_session_id(session_id)}/image?path={quote(path)}",
                                    IMAGE_LIMIT)
    if kind not in IMAGE_KINDS:
        raise HTTPException(status_code=502, detail="The workspace sent something other than an image or a PDF.")
    return Response(content=data, media_type=kind, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})


@router.put("/sessions/{session_id}/file")
def session_file_save(session_id: str, body: SaveIn, user=Depends(workspace_user)):
    """Save one file of a session's worktree from the page's editor (workspace.save)."""
    return client.call(_running(user), "PUT", f"/sessions/{_session_id(session_id)}/file?path={quote(body.path)}",
                       {"text": body.text, "base": body.base})


@router.get("/sessions/{session_id}/gate")
def session_gate(session_id: str, user=Depends(workspace_user)):
    """What the workspace's gate refused in a session, as the session's folder records it (workspace.gate_log), and
    what its agent asked for, with the person's answers as the app recorded them: an answer written in the session's
    folder, which its programs could write, is not taken."""
    sid = _session_id(session_id)
    got = client.call(_running(user), "GET", f"/sessions/{sid}/gate")
    if isinstance(got, dict):
        asked = [e for e in got.get("asks") or [] if isinstance(e, dict) and e.get("event") == "asked"]
        answers = [{"at": a["at"], "event": a["answer"], "ask": a["ask_id"], "program": a["program"],
                    "session": a["session"]} for a in _answers(user["id"]) if a["from_session"] == sid]
        got["asks"] = sorted(asked + answers, key=lambda e: e.get("at") if isinstance(e.get("at"), int) else 0)
    return got


@router.get("/sessions/{session_id}/ports")
def session_ports(session_id: str, user=Depends(workspace_user)):
    """The ports a session's app listens on, from a process working in its worktree (proofpane_workspace/ports.py):
    the desktop app offers them for its preview."""
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/ports")


@router.post("/sessions/{session_id}/ship")
def session_ship(session_id: str, body: ShipIn = ShipIn(), user=Depends(workspace_user)):
    """Push a finished session's branch and open its pull request (workspace.ship), with the workspace's own GitHub
    sign-in; the owner asks for it here, so it is the owner's act, not the session's. The pull request it gives back
    is the session's from then on, in the app's own record."""
    sid = _session_id(session_id)
    got = client.call(_running(user), "POST", f"/sessions/{sid}/ship", body.model_dump(), timeout=SHIP_WAIT)
    if isinstance(got, dict) and got.get("session") == sid:
        _pull_recorded(user["id"], sid, "shipped", got.get("url"))
    return got


class MergeIn(BaseModel):
    method: str = "squash"          # or merge, or rebase: the agent checks
    auto: bool = False              # once what the repository requires has passed


class DiscardIn(BaseModel):
    path: str


@router.post("/sessions/{session_id}/discard")
def session_discard(session_id: str, body: DiscardIn, user=Depends(workspace_user)):
    """Put one file of a session's worktree back as it was where the session started: the owner's choice on the page."""
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/discard", {"path": body.path},
                       timeout=DIFF_WAIT)


@router.get("/programs")
def programs(user=Depends(workspace_user)):
    return client.call(_running(user), "GET", "/programs")


@router.get("/sessions/{session_id}/paths")
def session_paths(session_id: str, user=Depends(workspace_user)):
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/paths", timeout=DIFF_WAIT)


@router.get("/sessions/{session_id}/pr")
def session_pr(session_id: str, user=Depends(workspace_user)):
    sid = _session_id(session_id)
    url = _the_pull(user["id"], sid)
    return client.call(_running(user), "GET", f"/sessions/{sid}/pr?url={quote(url, safe='')}", timeout=GITHUB_WAIT)


@router.get("/sessions/{session_id}/pr/feedback")
def session_pr_feedback(session_id: str, user=Depends(workspace_user)):
    """What a shipped session's reviewers and failed checks said, for the owner to choose what its agent gets."""
    sid = _session_id(session_id)
    url = _the_pull(user["id"], sid)
    return client.call(_running(user), "GET", f"/sessions/{sid}/pr/feedback?url={quote(url, safe='')}",
                       timeout=FEEDBACK_WAIT)


@router.post("/sessions/{session_id}/pr/merge")
def session_merge(session_id: str, body: MergeIn = MergeIn(), user=Depends(workspace_user)):
    """Merge a shipped session's pull request with the workspace's own GitHub sign-in: the owner's act, as shipping is.
    The one the app recorded as it shipped the session, and no other."""
    sid = _session_id(session_id)
    url = _the_pull(user["id"], sid, shipped=True)
    return client.call(_running(user), "POST", f"/sessions/{sid}/pr/merge", dict(body.model_dump(), url=url),
                       timeout=SHIP_WAIT)


@router.get("/repos/{name}/issues")
def repo_issues(name: str, search: Optional[str] = None, limit: int = 50, user=Depends(workspace_user)):
    if not _REPO.match(name) or ".." in name:
        raise HTTPException(status_code=404, detail="No such repository.")
    query = f"?limit={max(1, min(limit, 100))}" + (f"&search={quote(search, safe='')}" if search else "")
    return client.call(_running(user), "GET", f"/repos/{name}/issues{query}", timeout=GITHUB_WAIT)


@router.get("/repos/{name}/pulls")
def repo_pulls(name: str, search: Optional[str] = None, limit: int = 50, user=Depends(workspace_user)):
    if not _REPO.match(name) or ".." in name:
        raise HTTPException(status_code=404, detail="No such repository.")
    query = f"?limit={max(1, min(limit, 100))}" + (f"&search={quote(search, safe='')}" if search else "")
    return client.call(_running(user), "GET", f"/repos/{name}/pulls{query}", timeout=GITHUB_WAIT)


def _finished(user, endpoint, sid):
    """A session's Finish: its worktree committed to its branch by the workspace, the report verified and kept. The
    Finish route's, and Continue in a new session's first step."""
    report = client.call(endpoint, "POST", f"/sessions/{sid}/finish",
                         {"author_name": user["email"].split("@")[0], "author_email": user["email"]},
                         timeout=FINISH_WAIT)
    try:
        if not isinstance(report, dict) or report.get("session") != sid:
            raise finishes.BadReport("The workspace reported another session.")
        record = finishes.verify_report(report, agents.run_branch(user["id"], sid))
    except finishes.BadReport as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    return dict(finishes.save(user["id"], report, record), committed=bool(report.get("committed")),
                files=report.get("files") if isinstance(report.get("files"), int) else None)


@router.post("/sessions/{session_id}/finish")
def finish(session_id: str, user=Depends(workspace_user)):
    return _finished(user, _running(user), _session_id(session_id))


# ---- Continue in a new session -------------------------------------------------------

HANDOVER_PROGRAMS = ("claude", "codex", "chat")
NOT_HANDED = "Only a Claude Code, Codex or chat session with a repository is continued in a new one."
_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


class ContinueIn(BaseModel):
    program: str = Field(pattern="^(claude|codex|chat)$")
    # the Claude Code or Codex account the new session starts on: an id, or "default"; none, the one new sessions of
    # its program start on
    account: Optional[str] = Field(default=None, pattern=r"^(?:default|[0-9a-f]{12})$")
    task: str = Field(max_length=4000)          # the workspace's MAX_TASK
    cols: int = 100
    rows: int = 30


def _handovers(user_id, sid):
    """The app's rows of Continue in a new session that name a session: the one it continues, and those that continue
    it."""
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_session_handovers WHERE user_id = ? AND (from_session = ? OR "
                          "to_session = ?) ORDER BY at, id", (user_id, sid, sid)).fetchall()
    out = [dict(r) for r in rows]
    for r in out:
        r.pop("user_id", None)
    came = next((r for r in out if r["to_session"] == sid), None)
    return {"continued_from": came, "continued_in": [r for r in out if r["from_session"] == sid]}


@router.get("/sessions/{session_id}/handover")
def handover_view(session_id: str, user=Depends(workspace_user)):
    """What Continue in a new session starts from: the task as the owner gave the session, which the app kept (None
    for a session made before it kept them, or by an agent's ask), and the app's rows of the handovers that name it."""
    sid = _session_id(session_id)
    return dict(task=_task_of(user["id"], sid), **_handovers(user["id"], sid))


@router.post("/sessions/{session_id}/continue")
def continue_session(session_id: str, body: ContinueIn, user=Depends(workspace_user)):
    """Continue in a new session, by the owner's press alone. The session's worktree is
    committed to its branch as Finish does (verified and kept as a Finish is), then a new session is cut from that
    commit on the program and the account the owner chose, Claude Code's or Codex's, its first prompt the task as the
    owner gave it here and what the workspace observed of the old session's work: its commits by id and the names of
    the files they changed, marked as data (handover.first_prompt). Never a model's words, a transcript, a provider's
    session id, an account's folder or a credential. The workspace checks the new session's worktree is that commit
    before its program starts, the one check it has: a terminal program or a chat has no tool to say it read what it
    was given, as a task's attempt has (resume_ack).
    The old session is left as a Finish leaves it. Nothing starts a session this way but this route."""
    from . import handover, tasks
    endpoint = _running(user)
    sid = _session_id(session_id)
    task = body.task.strip()
    if not task:
        raise HTTPException(status_code=400, detail="Say what the new session is for.")
    shape = tasks.secret_shape(task)
    if shape is not None:
        raise HTTPException(status_code=400, detail=f"The task holds something shaped like {shape}: write it without "
                                                    "the secret, which goes into the new session's first prompt.")
    try:
        handover.check_room(task)               # before the Finish, so a task too long leaves nothing done
    except handover.TooLong as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    old = client.call(endpoint, "GET", f"/sessions/{sid}")
    if old.get("program") not in HANDOVER_PROGRAMS or not old.get("repo") or old.get("run"):
        raise HTTPException(status_code=412, detail=NOT_HANDED)
    _asks_as_chosen(endpoint, user)
    checked = _claude_ready(endpoint, body.account) if body.program in NEEDS_CLAUDE else None
    finished = _finished(user, endpoint, sid)
    head = finished.get("head")
    if not isinstance(head, str) or not _OBJECT_ID.match(head):
        raise HTTPException(status_code=502, detail="The workspace's Finish named no commit.")
    seen = client.call(endpoint, "GET", f"/sessions/{sid}/observed?head={head}")
    if not isinstance(seen, dict) or seen.get("head") != head or not isinstance(seen.get("commits"), list) \
            or not isinstance(seen.get("files"), list):
        raise HTTPException(status_code=502, detail="The workspace's report of the session's work is not in the "
                                                    "expected form.")
    try:
        prompt = handover.first_prompt(task, seen)
    except handover.TooLong as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    sent = {"repo": old["repo"], "program": body.program, "task": prompt, "base": head, "cols": body.cols,
            "rows": body.rows, "account": checked or body.account, "continues": {"session": sid, "head": head}}
    made = client.call(endpoint, "POST", "/sessions", sent, timeout=FINISH_WAIT)
    new = made.get("id") if isinstance(made, dict) else None
    if not isinstance(new, str) or not _SESSION.match(new):
        raise HTTPException(status_code=502, detail="The workspace did not name the new session.")
    account = made.get("account") or "default"
    _account_recorded(user["id"], new, account)
    _task_recorded(user["id"], new, task)
    row = {"id": secrets.token_hex(16), "from_session": sid, "to_session": new, "head": head,
           "program": body.program, "account": account, "finish_id": finished.get("id"),
           "commits": len(seen["commits"]) + int(seen.get("more_commits") or 0),
           "files": len(seen["files"]) + int(seen.get("more_files") or 0), "at": int(time.time())}
    try:
        with store.connect() as db:
            db.execute("INSERT INTO ade_session_handovers (id, user_id, from_session, to_session, head, program, "
                       "account, finish_id, commits, files, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (row["id"], user["id"], sid, new, head, row["program"], account, row["finish_id"],
                        row["commits"], row["files"], row["at"]))
    except Exception as e:  # noqa: BLE001  the new session was made; only the app's row of it is missing
        print(f"workspace: session {sid}'s handover to {new} was not kept: {type(e).__name__}: {e}", file=sys.stderr)
    return {"finish": finished, "session": made, "handover": row}


@router.post("/sessions/{session_id}/pr/review")
def pr_review(session_id: str, body: ReviewIn, user=Depends(workspace_user)):
    """The owner's own review of the pull request a session started from, on GitHub (workspace.pr_review): approve
    it, ask for changes, or comment, in their words, on the pull request the page showed them, which is the one the
    app recorded for the session."""
    sid = _session_id(session_id)
    if body.url != _the_pull(user["id"], sid):
        raise HTTPException(status_code=400, detail="This session's pull request is not the one shown: read it again, "
                                                    "then review.")
    return client.call(_running(user), "POST", f"/sessions/{sid}/pr/review", body.model_dump(), timeout=SHIP_WAIT)


@router.get("/sessions/{session_id}/update")
def update_status(session_id: str, user=Depends(workspace_user)):
    """How far behind the branch it started from a session is, as last known there (workspace.update_status)."""
    return client.call(_running(user), "GET", f"/sessions/{_session_id(session_id)}/update")


@router.post("/sessions/{session_id}/update")
def update(session_id: str, user=Depends(workspace_user)):
    """Bring a session up to date with the branch it started from (workspace.update): asked for by the owner, so a
    commit it makes is theirs, named as a finish names them."""
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/update",
                       {"author_name": user["email"].split("@")[0], "author_email": user["email"]}, timeout=SHIP_WAIT)


@router.post("/sessions/{session_id}/update/abort")
def update_abort(session_id: str, user=Depends(workspace_user)):
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/update/abort")


@router.post("/sessions/{session_id}/land")
def land(session_id: str, user=Depends(workspace_user)):
    """A finished session's branch into the one it follows, as a fast-forward, in a repository added from the
    person's own machine (workspace.land); a repository cloned into the workspace is shipped instead."""
    return client.call(_running(user), "POST", f"/sessions/{_session_id(session_id)}/land", timeout=SHIP_WAIT)


@router.get("/finishes")
def finish_list(session: Optional[str] = None, user=Depends(workspace_user)):
    if session is not None:
        _session_id(session)
    return {"finishes": finishes.list_for(user["id"], session)}


@router.get("/finishes/{finish_id}")
def finish_one(finish_id: str, user=Depends(workspace_user)):
    found = finishes.get(user["id"], finish_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such finish.")
    return found


def _same_origin(ws):
    """A browser's WebSocket comes with an Origin; it must be this site, or the phone app's own pages
    (config.app_origins). (A client that sends none still has to sign in with its first message, which a page on
    another site cannot read.)"""
    origin = ws.headers.get("origin")
    if origin is None:
        return True
    if origin in config.app_origins():
        return True
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    return parts.scheme in ("https", "http") and parts.netloc == ws.headers.get("host")


def _hello_user(token):
    return auth.token_user(token) if isinstance(token, str) and token else None


async def _hello(ws):
    """A socket's first message, its sign-in: a JSON object of at most MAX_HELLO characters, read within
    FIRST_MESSAGE_WAIT seconds; None for anything else. A longer one is not parsed (thousands of brackets in a few
    kilobytes would run a reader out of stack)."""
    try:
        text = await asyncio.wait_for(ws.receive_text(), FIRST_MESSAGE_WAIT)
        hello = json.loads(text) if len(text) <= MAX_HELLO else None
    except (asyncio.TimeoutError, ValueError, KeyError, RuntimeError, RecursionError, WebSocketDisconnect):
        return None
    return hello if isinstance(hello, dict) else None


async def _refuse(ws, detail, code=1008):
    try:
        await ws.send_text(json.dumps({"type": "error", "detail": detail}))
        await ws.close(code=code)
    except (RuntimeError, WebSocketDisconnect):
        pass


@router.websocket("/terminal")
async def terminal(ws: WebSocket):
    if not _same_origin(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    hello = await _hello(ws)
    if hello is None:
        return await _refuse(ws, "Sign in first.")
    user = await run_in_threadpool(_hello_user, hello.get("token"))
    if user is None:
        return await _refuse(ws, "Sign in first.")
    sid = hello.get("session")
    if not isinstance(sid, str) or not _SESSION.match(sid):
        return await _refuse(ws, "No such session.")
    try:
        p = workspaces.for_user(user)
        if _starting(user["id"]):
            return await _refuse(ws, STARTING)
        # a provider may ask its host where the workspace is (fly_provider), so not on the event loop
        endpoint = await run_in_threadpool(p.endpoint, user)
        if endpoint is None:
            return await _refuse(ws, "The workspace is not running: start it first.")
        n = hello.get("shell")
        agent = await client.open_terminal(endpoint, sid, shell=n if isinstance(n, int) and not isinstance(n, bool)
                                           else None)
    except (workspaces.WorkspaceAccess, client.WorkspaceCallError) as e:
        print(f"workspace terminal: {sid} not opened: {e}", file=sys.stderr)
        return await _refuse(ws, str(e))
    except Exception as e:  # noqa: BLE001  the page is told something is wrong; the log says what
        print(f"workspace terminal: {sid} not opened: {type(e).__name__}", file=sys.stderr)
        traceback.print_exc()
        return await _refuse(ws, "The terminal could not open: an error in the ADE (it is logged).", code=1011)
    size = client.resize_message({"type": "resize", "cols": hello.get("cols"), "rows": hello.get("rows")})
    token = hello.get("token")

    async def still_signed_in():
        # the same check as at hello, so a token that signing out everywhere ended closes a terminal already open,
        # the lost device's included
        again = await run_in_threadpool(_hello_user, token)
        return again is not None and again["id"] == user["id"]

    why = "not started"
    try:
        if size:
            try:
                await agent.send(size)
            except ConnectionClosed:
                # a program that ended before this terminal opened: the agent has sent its last output and exit
                # status and closed; the pump below still hands them over
                pass
        await ws.send_text(json.dumps({"type": "ready"}))
        why = await client.pump(ws, agent, on_activity=lambda: p.touch(user), signed_in=still_signed_in,
                                every=SIGNIN_CHECK_EVERY)
        if why == "signed-out":
            await _refuse(ws, "Signed out everywhere: this terminal is closed. Sign in again to open it.")
    except Exception as e:  # noqa: BLE001  the page is told the terminal closed on an error; the log says which
        why = f"an error: {type(e).__name__}"
        traceback.print_exc()
        await _refuse(ws, "The terminal closed on an error in the ADE (it is logged).", code=1011)
    finally:
        print(f"workspace terminal: {sid} closed: {why}", file=sys.stderr)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001  the browser has gone already (uvicorn raises ClientDisconnected then)
            pass


@router.websocket("/chat")
async def chat_live(ws: WebSocket):
    """Every chat's events as they happen, relayed from the account's workspace, with the terminal's rules: same
    origin, the sign-in in the first message, checked again every SIGNIN_CHECK_EVERY seconds. The page speaks through
    the chat routes above; it only listens here."""
    if not _same_origin(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    hello = await _hello(ws)
    if hello is None:
        return await _refuse(ws, "Sign in first.")
    user = await run_in_threadpool(_hello_user, hello.get("token"))
    if user is None:
        return await _refuse(ws, "Sign in first.")
    try:
        p = workspaces.for_user(user)
        if _starting(user["id"]):
            return await _refuse(ws, STARTING)
        endpoint = await run_in_threadpool(p.endpoint, user)
        if endpoint is None:
            return await _refuse(ws, "The workspace is not running: start it first.")
        agent = await client.open_chat(endpoint)
    except (workspaces.WorkspaceAccess, client.WorkspaceCallError) as e:
        print(f"workspace chat: not opened: {e}", file=sys.stderr)
        return await _refuse(ws, str(e))
    except Exception as e:  # noqa: BLE001  the page is told something is wrong; the log says what
        print(f"workspace chat: not opened: {type(e).__name__}", file=sys.stderr)
        traceback.print_exc()
        return await _refuse(ws, "The chat could not open: an error in the ADE (it is logged).", code=1011)
    token = hello.get("token")

    async def still_signed_in():
        again = await run_in_threadpool(_hello_user, token)
        return again is not None and again["id"] == user["id"]

    why = "not started"
    try:
        await ws.send_text(json.dumps({"type": "ready"}))
        why = await client.pump(ws, agent, signed_in=still_signed_in, every=SIGNIN_CHECK_EVERY,
                                text=client.chat_message)
        if why == "signed-out":
            await _refuse(ws, "Signed out everywhere: this page's chat is closed. Sign in again to open it.")
    except Exception as e:  # noqa: BLE001
        why = f"an error: {type(e).__name__}"
        traceback.print_exc()
        await _refuse(ws, "The chat closed on an error in the ADE (it is logged).", code=1011)
    finally:
        print(f"workspace chat: closed: {why}", file=sys.stderr)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001  the browser has gone already (uvicorn raises ClientDisconnected then)
            pass
