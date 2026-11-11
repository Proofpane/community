"""The workspace agent's API, which the app calls, and, for its /ask/ routes, a session's own program.

Every route but /health and the /ask/ ones needs `Authorization: Bearer <the workspace's secret>`, compared in
constant time; a terminal's WebSocket needs the same header and is refused before it is accepted without it. The /ask/
routes take a session's own token instead (asks.py), which the agent gives a terminal agent's program while the person
lets agents ask; it reads only that session's repository's sessions and asks for one more, and is never the secret,
nor the secret one of them. The agent listens on 127.0.0.1 only (see __main__), so what reaches it comes from this
machine: the app itself under the local provider, or the TLS proxy in front of it in a workspace machine, and a
session's programs.

    GET    /health                           no secret needed
    GET    /repos                            the workspace's repositories
    POST   /repos            {url}           clone one from an https address
    POST   /repos/link       {path}          add one already on this machine, where it is (the local provider's)
    DELETE /repos/{name}                     take one added from this machine out: its link, never its folder
    GET    /sessions                         its sessions
    POST   /sessions         {repo, program, task, base? or pull?, cols?, rows?, chat_settings?, account?}   pull:
                                             a pull request's number, to start from its head; account: the Claude Code
                                             or Codex account it runs on (an id, or "default"), else the one chosen;
                                             with "moved": {id, session, remote, branch, head, changes, cwd, bundle},
                                             a claude session moved from a linked machine, its repository found by its
                                             origin (moves.py); a move that started one already gets that one again
                                             (or a refusal), never a second; with "continues": {session, head}, a
                                             session continued in a new one (Claude Code, Codex or a chat), cut from
                                             that commit of the old session's branch, its worktree checked before its
                                             program starts
    PUT    /moves/{seq}/transcript           a moved session's transcript, its bytes (at most 32 MiB), kept until the
                                             session it came for starts or is refused
    PUT    /moves/{seq}/bundle               its git bundle, the same way, when there is one
    GET    /repos/{name}/pulls?search=S      a repository's open pull requests on GitHub, to start a session from one
    GET    /sessions/{id}
    POST   /sessions/{id}/title {title}      name it on the page; an empty name puts its task's first line back
    POST   /sessions/{id}/group {group}      join it to a group, to compare it with sessions started on its task
    GET    /model-providers                  other providers' models for claude, and whether each has a key (providers.py)
    PUT    /model-providers/{id} {key}       keep the person's own key for one; DELETE forgets it
    POST   /sessions/{id}/start {cols?, rows?, effort?}   start the program again, in the same worktree; effort: the
                                             one the app recorded for a claude session started from an ask, given
                                             when this agent keeps none of its own, and either given only
                                             while the installed claude offers it; one not given is said in the
                                             view's effort_notice. The claude read for it is the
                                             one this start runs, in the session's worktree, at this start
    POST   /sessions/{id}/stop
    POST   /sessions/{id}/close              stop its program and its shells, and mark it closed: out of the page's
                                             list, nothing of it removed; a start, or a chat's message, opens it again
    POST   /sessions/{id}/open               a closed session back in the page's list, nothing else changed
    POST   /sessions/{id}/finish {author_name, author_email}
    GET    /sessions/{id}/update             how far behind the branch it started from it is, as last known
    POST   /sessions/{id}/update {author_name, author_email}   bring it up to date: its work committed, then the
                                             branch merged in (fetched first for a clone); stopped by conflicts, left so
    POST   /sessions/{id}/update/abort       give up a merge an update stopped on
    POST   /sessions/{id}/pr/review {event, body, url}   the person's own review of the pull request it started from
    GET    /sessions/{id}/usage              the tokens its claude or codex wrote down (usage.py)
    GET    /map                              the control window's map: every session the page's list shows, each
                                             with its state, account, model, effort, tokens in context and its
                                             Claude Code's agents (sessionmap.py)
    GET    /sessions/{id}/map                one session's tool calls, under the agent each was made in, and its
                                             turns' requests and tokens, cut to 64 KB (sessionmap.py)
    GET    /skills?since=N                   the skills the sessions called (skillcalls.py): every session's
                                             new lines taken first, then the lines numbered after N, oldest first,
                                             at most 1000, with `more`, `seq` (where to ask from next), `first` (the
                                             oldest number held), `dropped` (lines out of shape) and the record's own
                                             id, `store`; one made anew has another, and the app reads from the start
    GET    /skill-improve/record             the record of skill proposals as its file holds it, whether its signature
                                             holds, whether the app's word came since this agent started, the starts
                                             the bar refused (skill_improve.py); PUT: the app's word, its copy's
                                             generation; POST
                                             /skill-improve/record/restore: written from the app's copy
    PUT    /skill-improve/switch {repo, on, account, model}   a repository's switch for the loop
    GET    /skill-improve/candidates?repo=R  the business skills at its branch's head and why each other is none
    GET    /skill-improve/branches?repo=R    its branch's head, and what each proposal's ref names and whether it
                                             reached the branch
    POST   /skill-improve/reviews {id, repo, folder, counts, current_version}   a review of one skill; then
                                             /reviews/{id}/objects {text} and /reviews/{id}/critic {text, hypothesis}
    POST   /skill-improve/proposals/{id}/{publish|close|accept|remake|take|pull-request|merge|go-back}
    GET    /sessions/{id}/observed?head=H    the commits on its branch from its base to H, oldest first, by id, and
                                             the names of the files they changed: what a session continued in a new
                                             one is given of the old one's work
    GET    /sessions/{id}/ports              what its app listens on, for a preview (ports.py)
    POST   /sessions/{id}/sign-in-code {code} typed into a running Claude sign-in, the one program that takes it
    GET    /github                           the GitHub account gh is signed in to, and its repositories
    GET    /sessions/{id}/chat?after=N       a chat's state and its events after N (chat.py)
    POST   /sessions/{id}/chat {text}        send a chat a message; its claude starts, or resumes, with it
    POST   /sessions/{id}/chat/answer {id, decision}   answer a question it asked: allow, allow_session or deny
    POST   /sessions/{id}/chat/interrupt     stop what it is doing now
    POST   /sessions/{id}/chat/settings {model, effort, permission_mode}   change what is given of a chat's settings
    GET    /sessions/{id}/chat/mcp           its MCP servers: the repository's (approved or not), what claude has
    POST   /sessions/{id}/chat/mcp {action: approve {name, fingerprint} | stop {name} | add {spec} | remove {name,
                                             scope}}   the owner's choice about one (mcp.py)
    POST   /sessions/{id}/upload?name=N&to=chat   a file's bytes, into the session's worktree under
                                             .proofpane-uploads/; noted in the chat when it is attached to one
    GET    /capabilities                     what the installed claude offers a chat: its version, efforts, modes
    GET    /claude/update                    Claude Code kept current: the mode, the versions, the last attempt
    POST   /claude/update {mode?, now?}      set the mode (automatic or manual); `now` starts an update
    WS     /chat                             every chat's events as they happen
    GET    /claude?account=A&fresh=1         whether Claude Code is signed in, and as whom, from its own status (an
                                             account's: an id, "default", or "active" for the one sessions start on),
                                             its answer kept a few seconds for the pages' looks; fresh=1 reads it
                                             anew (a session's start)
    POST   /claude/sign-out                  Claude Code's own sign-out; its credentials file is then made sure gone
    GET    /accounts?status=0                Claude Code's and Codex's accounts: each one's sign-in (not asked, with
                                             status=0), sessions and windows, and the one new sessions start on
    POST   /accounts         {kind, name}    a new account of claude or codex, to sign in to
    POST   /accounts/choose  {kind, account} the account new sessions of that kind start on ("default" its own)
    POST   /accounts/{id}/sign-out           an account's own sign-out ("default": the program's own)
    DELETE /accounts/{id}                    an account signed out and forgotten, once no session runs on it
    DELETE /sessions/{id}                    remove the worktree; the branch stays
    WS     /sessions/{id}/terminal           first {"type": "ready"}, or {"type": "error", "detail"} and a close;
                                             then binary frames both ways; text frames {"type": "resize", cols,
                                             rows} in, {"type": "exit", "code"} or {"type": "dropped"} out
    GET    /sessions/{id}/search?q=Q&case=1&regex=1   the lines of its worktree that match, with git grep
    GET    /sessions/{id}/image?path=P       an image of its worktree, as its bytes (only the kinds in IMAGE_TYPES)
    PUT    /sessions/{id}/file?path=P {text, base}   save one file from the page's editor, if it is still `base`
                                             (its sha256 when the page opened it): 412 when it changed meanwhile
    POST   /sessions/{id}/shell {cols?, rows?, n?}   open a shell in the session's worktree, beside its program (up to
                                             four; `n` opens that one again): the session, and the shell's number
    POST   /sessions/{id}/shell/{n}/stop     hang shell n up
    DELETE /sessions/{id}/shell/{n}          hang it up and let its place go
    WS     /sessions/{id}/shell/{n}          its terminal, as /terminal is the program's
    GET    /linear                           whether a Linear key is kept, and whose (linear.py); never the key
    PUT    /linear/key {key}                 keep the person's own Linear key, once Linear takes it; DELETE forgets it
    GET    /linear/issues?search=S&limit=N   the person's open Linear issues, or a search of them
    GET    /proofpane                        the pairing with the owner's Proofpane organisation and what each claude
                                             start is given of its core (core_plugin.py), and the usage rows posted and
                                             dropped there (usage_report.py); never the device's token
    PUT    /proofpane/pair {code, cloud_url?, holds?}   pair with a code Proofpane made; DELETE unpairs
    PUT    /proofpane/holds {holds}          the commands a session's call waits on the owner's approval for
    GET    /asks                             whether agents may ask for sessions, and the asks waiting (asks.py)
    POST   /asks/settings {on}               the person's switch, which the app keeps and gives again after a start
    POST   /asks/{id}/start {cols?, rows?, effort?}   start the session an agent asked for; effort: the person's
                                             choice, for a claude ask alone, none or "" being Claude Code's default
                                             and never the ask's; a model, a permission mode or may_dispatch true
                                             given with it is refused, an empty one being the default.
                                             The new session's view, with the effort it started with (null: the
                                             default); an effort the claude that starts does not offer, read in the
                                             new worktree, is refused as when the agent asked, the ask waiting again
                                             unless its session's asks were withdrawn, or the
                                             switch turned off, while it was being started
    POST   /asks/{id}/refuse                 refuse it, held as a Start holds it until the refusal is written, so the
                                             session that asked reads it as starting until then, never as gone
    GET    /agents/runs?since=N              the agents' runs changed since change number N, and the number now
                                             (agents.py: what the app's poll copies)
    PUT    /agents/{id}      {agent}         the app's agent, mirrored; GET gives it back with its runs
    POST   /agents/{id}/runs {trigger, run_id?, task?}   queue a run of it, with the app's id for it: {kind: manual,
                                             prompt?}; a task's attempt is {kind: task, id} with its message, task
    GET    /agents/backends                  the backends a task's attempt can run on (task_runs.py)
    POST   /agents/identity {account}        an account's identity, read by a frozen start that sends no message,
                                             one start at a time for an account (409 for a read that waits too long)
    GET    /agents/tasks/{task}/attempts     a task's attempts here: each run, its fence and its state
    POST   /agents/tasks/{task}/worktree {fence, action, head}   the owner's keep or discard of a task's worktree
    DELETE /agents/tasks/{task} {branch?}    a task's worktree gone, once no attempt of it is queued or running; with
                                             branch true, its branch too while still at its checkpoint 0 (an
                                             acceptance no task came of)
    POST   /agents/tasks/{task}/push {address, commit, since, call_id, branch_head}   a hand-off's push, by hash
    POST   /agents/tasks/{task}/fetch {address, commit, purpose, agent, base, protected}   an offer's view, an
                                             acceptance (the new task's worktree) or a return
    POST   /agents/tasks/{task}/checks {commit, steps}   the owner's checks of those steps on a returned commit
    POST   /agents/{id}/retire               no more runs; its live ones stop; its worktree goes
    GET|PUT /agents/{id}/mcp-secrets {secrets} its MCP servers' secret headers and environment: set or taken away;
                                             only which names have a value is given back
    POST   /agents/runs/{id}/stop            stop a run
    GET    /agents/runs/{id}/tree            its turns, tool calls and sub-agents (runtree.py): its copy, once ended
    POST   /agents/runs/{id}/message {text}  a message for a run: kept until its turn ends, or sent while it waits
    PUT    /agents/{id}/facts {facts, version}   the app's copy of the agent's current facts, for its runs' lookups
    GET    /agents/facts-versions            {agent id: the ledger version its copy holds, or null}: what to send again
    GET|PUT /agents/{id}/memory {files}      its memory folder: each file and the runs since it was used, and the
                                             archive; or made a version's files again (memory.py)
    POST   /agents/{id}/memory/restore {path}   an archived memory file back
    GET    /agents/runs/{id}/memory          the memory snapshot a run left, when it changed the folder
    POST   /agents/runs/{id}/judge           a judge's grades of an ended run (judge.py), kept on its record
    POST   /agents/runs/{id}/critic {change, inputs}   the critic's word on a review run's change (improve.py)
    POST   /agents/judge-pair {a, b}         the pairwise judge's grades of two sandbox runs of one input (improve.py)
    GET|POST /agents/sweep                   the last freshness sweep's day and what it found; or one now (it runs
                                             at the day's first start)

    With a session's own token, not the secret (the `proofpane` command, command.py):
    GET    /ask/sessions                     the sessions on its repository
    POST   /ask/sessions {program, task, base?, effort?}   ask the person for a new session there; effort: for a
                                             claude session alone, one of the efforts its claude offers;
                                             the same ask again while this one is being taken is refused
    GET    /ask/sessions/{id}                one of them, its state and what it waits for
    GET    /ask/sessions/{id}/diff           one of them's change
    GET    /ask/asks                         its own asks, and how each was answered: waiting, starting (the person
                                             starting it, or refusing it until the refusal is written), started,
                                             refused or gone
"""

import asyncio
import hmac
import json
import os
import re
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from . import VERSION, agents as agentry, chat as chats, controller as control, core_plugin as core, linear, moves, \
    providers, skill_improve as skillimp, terminals
from .workspace import MAX_UPLOAD, Conflict, NotFound, WorkspaceError, check_id

MAX_BODY = 64 * 1024
MAX_RUN_BODY = 2 * 1024 * 1024      # a run a hook started carries its delivery (at most 1 MB) in base64
MAX_ASK_BODY = 16 * 1024            # an ask: a task of at most 4000 characters, from anything that holds a token
_ASK_ID = re.compile(r"^[0-9a-f]{12}$")
MAX_SAVE_BODY = 4 * 1024 * 1024     # a file saved from the page: its text, at most 512 KB, as JSON (escapes grow it)
# the app's word on the record of skill proposals and its restore: the record's file (at most skillimp.RECORD_MAX) in
# base64, with the commits and the ends the app recorded beside it; the record only grows, so a limit below its own would
# refuse the word, and every start with it, once the record passed it
MAX_RECORD_BODY = 3 * skillimp.RECORD_MAX


class _Secret:
    """Refuse every request but /health that does not carry the workspace's secret. A request that carries it and
    changes something, or opens a terminal, counts as activity (`on_activity`); reading a list does not. The /ask/
    routes are a session's program's (`proofpane`, asks.py): each carries that program's own token, which its route checks,
    never the workspace's secret, which no program is given."""

    def __init__(self, app, secret, on_activity=None, on_page=None):
        self.app = app
        self.expected = b"Bearer " + secret.encode("utf-8")
        self.on_activity = on_activity or (lambda: None)
        # activity of a page's (anything but the app's own mirroring of the agents): a machine started for a run alone
        # stops soon after its runs end only while there has been none (__main__'s --idle-exit-after-runs)
        self.on_page = on_page or (lambda: None)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/ask/"):
            return await self.app(scope, receive, send)
        if scope["type"] in ("http", "websocket") and scope["path"] != "/health":
            given = b""
            for name, value in scope.get("headers") or []:
                if name == b"authorization":
                    given = value
            if not hmac.compare_digest(given, self.expected):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"detail": "Not this workspace's secret."}, status_code=401)(scope, receive,
                                                                                                    send)
                return
            if scope["type"] == "websocket" or scope.get("method") not in ("GET", "HEAD"):
                # the app's mirror of the owner's controller is activity only where the workspace takes it (its route
                # says so): one refused (412, an account the workspace lacks) comes again every round and would keep
                # the machine up for nothing; the controller's own work keeps it up (Controller.busy)
                if not scope["path"].startswith("/controller"):
                    self.on_activity()
                # the app's own mirroring, of the agents and of the owner's controller, is no page's, nor is its word on
                # the record of skill proposals, sent first in each call of its own that can lead to a start
                # (proofpane_open/ade/skill_improve.py's send_word); the owner's restore from the app's copy, a page's
                # tap, still is
                if not (scope["path"].startswith(("/agents", "/controller"))
                        or scope["path"] == "/skill-improve/record"):
                    self.on_page()
        await self.app(scope, receive, send)


MAX_NESTING = 64            # a body's arrays and objects inside one another; the agent's own requests nest a few deep
_JSON_STRING = re.compile(r'"(?:[^"\\]++|\\.)*+(?:"|\\?\Z)', re.S)


def _nests_too_deep(text):
    """Whether JSON text nests more than MAX_NESTING arrays or objects, read from its brackets outside its strings
    without parsing it (a reader may run out of stack on one thousands deep, or read it whole)."""
    if text.count("[") + text.count("{") <= MAX_NESTING:
        return False
    depth = 0
    for c in _JSON_STRING.sub("", text):
        if c in "[{":
            depth += 1
            if depth > MAX_NESTING:
                return True
        elif c in "]}":
            depth -= 1
    return False


async def _body(request, limit=MAX_BODY):
    """A request's JSON object, refused when it is larger than `limit`, not UTF-8, nested past MAX_NESTING, or holds
    half of a character (an escape like \\ud83d without its other half: nothing that writes UTF-8 can write it)."""
    held = bytearray()
    async for chunk in request.stream():        # read as it comes, never past the limit
        held += chunk
        if len(held) > limit:
            raise WorkspaceError("The request is too large.")
    raw = bytes(held)
    try:
        text = raw.decode("utf-8") if raw else "{}"
    except UnicodeDecodeError:
        raise WorkspaceError("The request is not UTF-8.") from None
    if _nests_too_deep(text):
        raise WorkspaceError("The request nests too deep.")
    try:
        body = json.loads(text)
    except (ValueError, RecursionError):
        raise WorkspaceError("The request is not JSON.") from None
    if not isinstance(body, dict):
        raise WorkspaceError("The request is a JSON object.")
    if "\\u" in text:
        try:
            json.dumps(body, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            raise WorkspaceError("The request holds half of a character (an unpaired surrogate), which is not text.") \
                from None
    return body


def _size(body):
    return terminals.check_size(body.get("cols", 100), body.get("rows", 30))


def make_app(workspace, secret):
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise ValueError("The workspace's secret must be at least 32 bytes.")
    lock = asyncio.Lock()
    # the owner's controller starts its sessions under the same lock as every start here (controller.py), so that
    # MAX_SESSIONS is counted one start at a time; a stand-in for the workspace in a test may have no controller
    controller = getattr(workspace, "controller", None)
    if controller is not None:
        controller.lock = lock

    async def health(request):
        return JSONResponse({"ok": True, "version": VERSION})

    async def repos(request):
        if request.method == "POST":
            body = await _body(request)
            async with lock:
                return JSONResponse(await asyncio.to_thread(workspace.clone, body.get("url")))
        return JSONResponse({"repos": await asyncio.to_thread(workspace.repos)})

    async def repo_link(request):
        body = await _body(request)
        async with lock:
            return JSONResponse(await asyncio.to_thread(workspace.link, body.get("path")))

    async def repo_unlink(request):
        async with lock:
            return JSONResponse(await asyncio.to_thread(workspace.unlink, request.path_params["name"]))

    async def sessions(request):
        if request.method == "POST":
            body = await _body(request)
            cols, rows = _size(body)
            async with lock:
                # the app's second try at a move gets the session the first made, its program not started again
                already = await asyncio.to_thread(workspace.moved_already, body.get("moved"))
                if already:
                    return JSONResponse(workspace.session(already))
                # one Claude sign-in per account at a time: one still running on the account this
                # one starts on is stopped first, under the lock, so that two presses at once start one after the other
                await workspace.end_sign_ins(body.get("program"), body.get("account"))
                record, argv = await asyncio.to_thread(workspace.prepare, body.get("repo"), body.get("program"),
                                                       body.get("task"), body.get("base"), body.get("chat_settings"),
                                                       body.get("group"), body.get("model_provider"), body.get("pull"),
                                                       None, body.get("custom"), body.get("account"),
                                                       teleport=body.get("teleport"), moved=body.get("moved"),
                                                       continues=body.get("continues"))
                view = workspace.launch(record["id"], argv, cols, rows)
            if record["program"] == "chat":
                # a chat's task is its first message; when claude cannot start, the chat's log says why
                try:
                    await workspace.chat_send(record["id"], record["task"])
                except (chats.ChatError, WorkspaceError):
                    pass
                view = workspace.session(record["id"])
            return JSONResponse(view)
        return JSONResponse({"sessions": workspace.sessions()})

    async def session(request):
        sid = check_id(request.path_params["id"])
        if request.method == "DELETE":
            async with lock:
                return JSONResponse(await workspace.remove(sid))
        return JSONResponse(workspace.session(sid, checks=True))

    async def start(request):
        sid = check_id(request.path_params["id"])
        body = await _body(request)
        cols, rows = _size(body)
        # a claude terminal started again: its claude's version read here, off the event loop and before the lock, so
        # a slow read holds up no other request, and its start refused on a claude below the gate's floor
        # (Workspace.claude_floor_for); launch asks nothing, and starts the claude checked
        refused, claude = await asyncio.to_thread(workspace.claude_floor_for, sid)
        if refused:
            raise WorkspaceError(refused)
        # the effort of a session started from an ask: this agent's copy, else the one the app recorded and
        # sent, given only while the claude checked above offers it, read now in the session's worktree with its
        # environment, chosen here off the event loop and before the lock as the floor is
        # (Workspace.start_effort); one not given is said in the answer
        effort, notice = await asyncio.to_thread(workspace.start_effort, sid, body.get("effort"), claude)
        async with lock:
            # a Claude sign-in started again is one sign-in of its account at a time too
            await workspace.end_sign_ins_beside(sid)
            view = workspace.launch(sid, cols=cols, rows=rows, claude=claude, effort=effort)
        return JSONResponse(dict(view, effort_notice=notice) if notice else view)

    async def stop(request):
        sid = check_id(request.path_params["id"])
        return JSONResponse(await workspace.stop(sid))

    async def session_map_all(request):
        # the control window's map (sessionmap.py): the sessions' views made here, on the event loop, as /sessions makes
        # them (a chat's object is the loop's to make, chat.py), and their files read off it, as the page asks every few
        # seconds
        views = workspace.sessions()
        runs = workspace.map_runs(views)
        return JSONResponse(await asyncio.to_thread(workspace.map, views, runs))

    async def session_map(request):
        sid = check_id(request.path_params["id"])
        return JSONResponse(await asyncio.to_thread(workspace.session_map, sid))

    async def skills_page(request):
        # the skills the sessions called (skillcalls.py): every session's new lines taken, then the lines after a
        # number, at most a page; a workspace from before it answers 404 here, which the app takes for no lines
        since = request.query_params.get("since", "0")
        if not re.fullmatch(r"[0-9]{1,16}", since):
            raise WorkspaceError("Ask for the skill calls since a number.")
        return JSONResponse(await asyncio.to_thread(workspace.skills_page, int(since)))

    async def close_session(request):
        # stopped and out of the page's list, nothing removed
        sid = check_id(request.path_params["id"])
        return JSONResponse(await workspace.close(sid))

    async def open_session(request):
        sid = check_id(request.path_params["id"])
        return JSONResponse(await asyncio.to_thread(workspace.reopen, sid))

    async def title(request):
        sid = check_id(request.path_params["id"])
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.rename, sid, body.get("title")))

    async def model_providers(request):
        return JSONResponse(await asyncio.to_thread(providers.listing, os.path.expanduser("~")))

    async def model_provider(request):
        name, home = request.path_params["id"], os.path.expanduser("~")
        if request.method == "DELETE":
            return JSONResponse(await asyncio.to_thread(providers.remove, home, name))
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(providers.save, home, name, body.get("key")))

    async def group(request):
        sid = check_id(request.path_params["id"])
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.join_group, sid, body.get("group")))

    async def github(request):
        return JSONResponse(await asyncio.to_thread(workspace.github))

    async def claude(request):
        # an account's, with ?account= (an id, "default", or "active": the one new claude sessions start on)
        account = request.query_params.get("account")
        if account == "active":
            account = workspace.accounts.for_session("claude")
        # ?fresh=1: read anew, never a kept answer (a session's start, which the app checks so)
        got = await asyncio.to_thread(workspace.claude, account, request.query_params.get("fresh") == "1")
        return JSONResponse(dict(got, account=account or "default"))

    async def claude_sign_out(request):
        return JSONResponse(await asyncio.to_thread(workspace.claude_sign_out))

    # accounts (accounts.py): more than one sign-in to Claude Code or Codex, each in a folder of its own

    async def accounts_list(request):
        if request.method == "POST":
            body = await _body(request)
            async with lock:
                return JSONResponse(await asyncio.to_thread(workspace.account_add, body.get("kind"), body.get("name")))
        return JSONResponse(await asyncio.to_thread(workspace.accounts_view, request.query_params.get("status") != "0"))

    async def account_choose(request):
        body = await _body(request)
        async with lock:
            return JSONResponse(await asyncio.to_thread(workspace.account_choose, body.get("kind"), body.get("account")))

    async def account_sign_out(request):
        account = request.path_params["id"]
        found = workspace.accounts.get(account) if account != "default" else None
        kind = found["kind"] if found else (await _body(request)).get("kind")
        if account != "default" and found is None:
            raise NotFound("No such account.")
        if kind == "codex":
            return JSONResponse(await asyncio.to_thread(workspace.codex_sign_out, account))
        if kind == "claude":
            return JSONResponse(await asyncio.to_thread(workspace.claude_sign_out, account))
        raise WorkspaceError("Name the program whose own sign-in to end: claude or codex.")

    async def account_remove(request):
        account = request.path_params["id"]
        async with lock:
            # once no session runs on it, its own sign-ins (finished, or still waiting for a code) go first, so nothing
            # of it stays behind
            if workspace.accounts.get(account) is not None and not await asyncio.to_thread(workspace.account_users,
                                                                                            account):
                for sid in await asyncio.to_thread(workspace.account_sign_ins, account):
                    await workspace.remove(sid)
            return JSONResponse(await asyncio.to_thread(workspace.account_remove, account))

    async def chat_log(request):
        sid = check_id(request.path_params["id"])
        if request.method == "POST":
            body = await _body(request)
            return JSONResponse(await workspace.chat_send(sid, body.get("text")))
        after = request.query_params.get("after", "0")
        if not after.isdigit() or len(after) > 12:
            raise WorkspaceError("after is a whole number.")
        return JSONResponse(workspace.chat_log(sid, int(after)))

    async def chat_answer(request):
        sid = check_id(request.path_params["id"])
        body = await _body(request)
        return JSONResponse(workspace.chat_answer(sid, body.get("id"), body.get("decision")))

    async def chat_interrupt(request):
        return JSONResponse(await workspace.chat_interrupt(check_id(request.path_params["id"])))

    async def capabilities(request):
        return JSONResponse(await asyncio.to_thread(workspace.capabilities))

    async def claude_update(request):
        if request.method == "POST":
            body = await _body(request)
            if body.get("mode") is not None:
                await asyncio.to_thread(workspace.update_mode, body.get("mode"))
            if body.get("now") is True:
                if not workspace.claude_updates:
                    raise WorkspaceError("Claude Code is kept current in a workspace machine only.")
                workspace.updating = True       # said at once; the update itself runs on
                asyncio.get_running_loop().run_in_executor(None, workspace.update_claude)
        return JSONResponse(await asyncio.to_thread(workspace.update_info))

    async def upload(request):
        sid = check_id(request.path_params["id"])
        raw = bytearray()
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_UPLOAD:
                raise WorkspaceError(f"A file is at most {MAX_UPLOAD // (1024 * 1024)} MB.")
        return JSONResponse(await asyncio.to_thread(workspace.upload, sid, request.query_params.get("name", ""),
                                                    bytes(raw), request.query_params.get("to") == "chat"))

    async def move_file(request):
        # a file the app sends for a moved session (moves.py), read as it comes and never past its limit
        seq, name = request.path_params["seq"], request.path_params["file"]
        if moves.check_seq(seq) is None or name not in moves.FILES:
            raise NotFound("No such file of a move.")
        raw = bytearray()
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > moves.MAX_FILE:
                raise WorkspaceError(f"A move's {name} is at most {moves.MAX_FILE // (1024 * 1024)} MiB.")
        return JSONResponse(await asyncio.to_thread(workspace.move_file, seq, name, bytes(raw)))

    async def chat_settings(request):
        sid = check_id(request.path_params["id"])
        return JSONResponse(await workspace.chat_choose(sid, await _body(request)))

    async def chat_mcp(request):
        sid = check_id(request.path_params["id"])
        if request.method == "GET":
            return JSONResponse(await workspace.mcp_view(sid))
        body = await _body(request)
        action = body.get("action")
        if action == "approve":
            return JSONResponse(await workspace.mcp_approve(sid, body.get("name"), body.get("fingerprint")))
        if action == "stop":
            return JSONResponse(await workspace.mcp_revoke(sid, body.get("name")))
        if action == "add":
            return JSONResponse(await workspace.mcp_add(sid, body.get("spec")))
        if action == "remove":
            return JSONResponse(await workspace.mcp_remove(sid, body.get("name"), body.get("scope")))
        raise WorkspaceError("The action is approve, stop, add or remove.")

    async def chat_live(ws):
        """Every chat's events as they happen, one socket for them all: {"type": "event", "session", "event"}, and
        what claude is writing as it writes it, {"type": "delta", "session", "delta"}; a page that falls too far
        behind is told {"type": "dropped"} and closed, and reads the logs again."""
        await ws.accept()
        q = asyncio.Queue()
        workspace.chat_listeners.add(q)

        async def out():
            while True:
                item = await q.get()
                if item is None:
                    await ws.send_text(json.dumps({"type": "dropped"}))
                    return
                if len(item) == 3:          # what claude is writing as it writes it (Chat._stream), never logged
                    sid, d, _ = item
                    await ws.send_text(json.dumps({"type": "delta", "session": sid, "delta": d}, ensure_ascii=False))
                    continue
                sid, ev = item
                await ws.send_text(json.dumps({"type": "event", "session": sid, "event": ev}, ensure_ascii=False))

        async def inp():
            while True:
                if (await ws.receive())["type"] == "websocket.disconnect":
                    return

        try:
            await ws.send_text(json.dumps({"type": "ready"}))
            tasks = [asyncio.ensure_future(out()), asyncio.ensure_future(inp())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            workspace.chat_listeners.discard(q)
        try:
            await ws.close()
        except RuntimeError:
            pass

    async def sign_in_code(request):
        sid = check_id(request.path_params["id"])
        body = await _body(request)
        return JSONResponse(await workspace.send_sign_in_code(sid, body.get("code")))

    async def session_usage(request):
        sid = check_id(request.path_params["id"])
        return JSONResponse(await asyncio.to_thread(workspace.usage, sid))

    def _who(body, what):
        """The person a commit is made for (a finish, an update), as the app names them."""
        name, email = body.get("author_name"), body.get("author_email")
        if (not isinstance(name, str) or not isinstance(email, str) or not name.strip() or "@" not in email
                or len(name) > 200 or len(email) > 200 or any(c in name + email for c in "\n\r<>\0")):
            raise WorkspaceError(f"{what} names who asked for it.")
        return name.strip(), email.strip()

    async def finish(request):
        sid = check_id(request.path_params["id"])
        name, email = _who(await _body(request), "A finish")
        async with lock:
            return JSONResponse(await asyncio.to_thread(workspace.finish, sid, name, email))

    async def update(request):
        sid = check_id(request.path_params["id"])
        if request.method == "GET":
            return JSONResponse(await asyncio.to_thread(workspace.update_status, sid))
        name, email = _who(await _body(request), "An update")
        # the fetch first, without the lock: it can take minutes, and moves only a ref of origin's
        note = await asyncio.to_thread(workspace.update_fetch, sid)
        async with lock:
            return JSONResponse(await asyncio.to_thread(workspace.update, sid, name, email, note))

    async def pr_review(request):
        sid = check_id(request.path_params["id"])
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.pr_review, sid, body.get("event"), body.get("body", ""),
                                                    body.get("url")))

    async def update_abort(request):
        sid = check_id(request.path_params["id"])
        async with lock:
            return JSONResponse(await asyncio.to_thread(workspace.update_abort, sid))

    async def land(request):
        # a finished session's branch into the one it follows, in a repository of the person's own (workspace.land)
        sid = check_id(request.path_params["id"])
        async with lock:
            return JSONResponse(await asyncio.to_thread(workspace.land, sid))

    async def terminal(ws):
        try:
            term = workspace.terminal(check_id(ws.path_params["id"]))
        except NotFound:
            term = None
        await _attach(ws, term, "The session's program is not running.")

    async def shell_terminal(ws):
        try:
            term = workspace.shell(check_id(ws.path_params["id"]), ws.path_params["n"])
        except (NotFound, WorkspaceError):
            term = None
        await _attach(ws, term, "The session has no such shell open: open one first.")

    async def shell_start(request):
        sid = check_id(request.path_params["id"])
        body = await _body(request)
        cols, rows = _size(body)
        async with lock:
            view, n = workspace.shell_start(sid, cols, rows, body.get("n"))
        return JSONResponse(dict(view, opened=n))

    async def shell_stop(request):
        return JSONResponse(await workspace.shell_stop(check_id(request.path_params["id"]), request.path_params["n"]))

    async def shell_close(request):
        return JSONResponse(await workspace.shell_close(check_id(request.path_params["id"]), request.path_params["n"]))

    async def _attach(ws, term, missing):
        await ws.accept()
        if term is None:
            await ws.send_text(json.dumps({"type": "error", "detail": missing}))
            await ws.close()
            return
        backlog, queue = term.attach()
        try:
            await ws.send_text(json.dumps({"type": "ready"}))
            if backlog:
                await ws.send_bytes(backlog)
            await _pump(ws, term, queue)
        finally:
            term.detach(queue)

    @asynccontextmanager
    async def lifespan(app):
        await workspace.agents.startup()        # runs that were queued when the machine stopped start now
        # every session's usage read once, so that a reply a restart left unposted is posted now (usage_report.py)
        workspace.usage_report.catch_up()
        if controller is not None:
            controller.start()                  # the controller's loop: its tasks' states, its pacing, its chat
        yield
        if controller is not None:
            await controller.shut()
        # stopping the agent hangs up every program it started, as closing their terminal windows would, and ends
        # every chat's claude (its conversation stays in its log and resumes with the next message)
        shells = [t for byn in workspace.shells.values() for t in byn.values()]
        await asyncio.gather(*(t.stop() for t in [*workspace.terminals.values(), *shells] if t.running),
                             *(c.stop() for c in list(workspace.chats.values()) if c.client is not None),
                             return_exceptions=True)

    # agents (agents.py): the app mirrors each agent here, queues its runs, and reads them back

    # the improve loop's proposals for the repositories' business skills (skill_improve.py)

    def _skills():
        found = getattr(workspace, "skill_improve", None)
        if found is None:
            raise NotFound("This workspace keeps no record of skill proposals.")
        return found

    async def skill_record(request):
        skills = _skills()
        if request.method == "PUT":
            body = await _body(request, MAX_RECORD_BODY)
            # each start waiting for the word goes on (take_word wakes them, from whichever thread)
            return JSONResponse(await asyncio.to_thread(skills.proposals.take_word, body.get("generation"),
                                                        body.get("record"), body.get("commits"), body.get("ends")))
        return JSONResponse(await asyncio.to_thread(skills.proposals.view))

    async def skill_record_restore(request):
        body = await _body(request, MAX_RECORD_BODY)
        return JSONResponse(await asyncio.to_thread(_skills().proposals.restore, body.get("record"),
                                                    body.get("commits"), body.get("ends")))

    async def skill_switch(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(_skills().set_switch, body))

    async def skill_candidates(request):
        return JSONResponse(await asyncio.to_thread(_skills().candidates, request.query_params.get("repo", "")))

    async def skill_branches(request):
        return JSONResponse(await asyncio.to_thread(_skills().look, request.query_params.get("repo", "")))

    async def skill_review(request):
        body = await _body(request)
        return JSONResponse(await _skills().review(body))

    async def skill_review_objects(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(_skills().objects, request.path_params["id"], body))

    async def skill_review_critic(request):
        body = await _body(request)
        return JSONResponse(await _skills().critic(request.path_params["id"], body))

    async def skill_proposal(request):
        pid, action = request.path_params["id"], request.path_params["action"]
        body = await _body(request)
        skills = _skills()
        ways = {"accept": lambda: skills.accept(pid, body), "publish": lambda: skills.publish(pid),
                "close": lambda: skills.close(pid, body.get("state")), "take": lambda: skills.take(pid),
                "remake": lambda: skills.remake(pid), "pull-request": lambda: skills.pull_request(pid, body),
                "merge": lambda: skills.merge(pid, body), "go-back": lambda: skills.go_back(pid, body)}
        if action not in ways:
            raise NotFound("No such way for a proposal.")
        return JSONResponse(await asyncio.to_thread(ways[action]))

    async def agents_runs(request):
        try:
            since = int(request.query_params.get("since", "0"))
        except ValueError:
            raise WorkspaceError("Ask for the runs changed since a change number.") from None
        return JSONResponse(workspace.agents.since(since))

    async def agent_one(request):
        agent_id = request.path_params["id"]
        if request.method == "PUT":
            body = await _body(request)
            if body.get("id") != agent_id:
                raise WorkspaceError("The agent's id is the address's.")
            return JSONResponse(await asyncio.to_thread(workspace.agents.put, body))
        agent = await asyncio.to_thread(workspace.agents.agent, agent_id)
        runs = await asyncio.to_thread(workspace.agents.runs, agent_id)
        return JSONResponse(dict(agent, runs=[workspace.agents.view(r) for r in runs]))

    async def agent_memory(request):
        agent_id = request.path_params["id"]
        if request.method == "PUT":
            body = await _body(request, MAX_RUN_BODY)
            return JSONResponse(await asyncio.to_thread(workspace.agents.memory_put, agent_id, body.get("files")))
        return JSONResponse(await asyncio.to_thread(workspace.agents.memory_listing, agent_id))

    async def agent_memory_restore(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.agents.memory_restore, request.path_params["id"],
                                                    body.get("path")))

    async def run_memory(request):
        return JSONResponse(await asyncio.to_thread(workspace.agents.memory_of_run, check_id(request.path_params["id"])))

    async def run_judge(request):
        return JSONResponse(await workspace.agents.judge(check_id(request.path_params["id"])))

    async def run_critic(request):
        body = await _body(request)
        return JSONResponse(await workspace.agents.critic(check_id(request.path_params["id"]), body.get("change"),
                                                          body.get("inputs")))

    async def judge_pair(request):
        body = await _body(request)
        return JSONResponse(await workspace.agents.judge_pair(check_id(body.get("a")), check_id(body.get("b"))))

    async def agents_sweep(request):
        if request.method == "GET":
            return JSONResponse(await asyncio.to_thread(workspace.agents.last_sweep))
        return JSONResponse(await asyncio.to_thread(workspace.agents.sweep))

    async def agents_facts_versions(request):
        return JSONResponse(await asyncio.to_thread(workspace.agents.facts_versions))

    async def agent_facts(request):
        body = await _body(request, MAX_RUN_BODY)
        return JSONResponse(await asyncio.to_thread(workspace.agents.put_facts, request.path_params["id"],
                                                    body.get("facts"), body.get("version")))

    async def agent_queue(request):
        body = await _body(request, MAX_RUN_BODY)
        view = await asyncio.to_thread(workspace.agents.queue, request.path_params["id"], body.get("trigger"),
                                       body.get("run_id"), None, body.get("task"))
        workspace.agents.pump()
        return JSONResponse(view)

    # a task's attempts (task_runs.py), each in a thread of its own: git,
    # an account's frozen start and a step's check take seconds

    async def agents_backends(request):
        return JSONResponse(await asyncio.to_thread(workspace.agents.task_runs.backends))

    async def agents_identity(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.agents.task_runs.identity, body.get("account")))

    async def task_attempts(request):
        return JSONResponse(await asyncio.to_thread(workspace.agents.task_runs.attempts_view,
                                                    request.path_params["task"]))

    async def task_worktree(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.agents.task_runs.worktree, request.path_params["task"],
                                                    body))

    async def task_delete(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.agents.task_runs.delete, request.path_params["task"],
                                                    body.get("branch") is True))

    async def task_push(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.agents.task_runs.push, request.path_params["task"], body))

    async def task_fetch(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.agents.task_runs.fetch, request.path_params["task"],
                                                    body))

    async def task_checks(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.agents.task_runs.checks, request.path_params["task"],
                                                    body))

    async def agent_retire(request):
        return JSONResponse(await workspace.agents.retire(request.path_params["id"]))

    async def agent_secrets(request):
        # the values arrive from the page through the app, which keeps none, and are never given back: only which
        # names have one
        agent_id = request.path_params["id"]
        if request.method == "PUT":
            body = await _body(request)
            return JSONResponse(await asyncio.to_thread(workspace.agents.put_secrets, agent_id, body.get("secrets")))
        await asyncio.to_thread(workspace.agents.agent, agent_id)
        return JSONResponse(await asyncio.to_thread(workspace.agents.secrets_set, agent_id))

    async def run_tree(request):
        return JSONResponse(await asyncio.to_thread(workspace.agents.tree, check_id(request.path_params["id"])))

    async def run_stop(request):
        return JSONResponse(await workspace.agents.stop(check_id(request.path_params["id"])))

    async def run_message(request):
        body = await _body(request)
        return JSONResponse(await workspace.chat_send(check_id(request.path_params["id"]), body.get("text")))

    async def observed(request):
        # read only: git's log and diff between two commits, for a session continued in a new one
        return JSONResponse(await asyncio.to_thread(workspace.observed, check_id(request.path_params["id"]),
                                                    request.query_params.get("head")))

    async def changes(request):
        # read only: a temporary index takes the worktree's state, so no lock against the session's own git
        return JSONResponse(await asyncio.to_thread(workspace.changes, check_id(request.path_params["id"])))

    async def files(request):
        path = request.query_params.get("path", "")
        return JSONResponse(await asyncio.to_thread(workspace.files, check_id(request.path_params["id"]), path))

    async def file(request):
        path = request.query_params.get("path", "")
        sid = check_id(request.path_params["id"])
        if request.method == "PUT":
            body = await _body(request, MAX_SAVE_BODY)
            return JSONResponse(await asyncio.to_thread(workspace.save, sid, path, body.get("text"), body.get("base")))
        return JSONResponse(await asyncio.to_thread(workspace.file, sid, path))

    async def image(request):
        data, kind = await asyncio.to_thread(workspace.image, check_id(request.path_params["id"]),
                                             request.query_params.get("path", ""))
        return Response(data, media_type=kind, headers={"X-Content-Type-Options": "nosniff"})

    async def session_ports(request):
        return JSONResponse(await asyncio.to_thread(workspace.ports, check_id(request.path_params["id"])))

    async def gate_log(request):
        return JSONResponse(await asyncio.to_thread(workspace.gate_log, check_id(request.path_params["id"])))

    async def ship(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.ship, check_id(request.path_params["id"]),
                                                    body.get("title"), body.get("body"), body.get("base"),
                                                    bool(body.get("draft"))))

    async def issues(request):
        q = request.query_params
        return JSONResponse(await asyncio.to_thread(workspace.issues, request.path_params["name"],
                                                    q.get("search") or None, q.get("limit") or 50))

    async def pulls(request):
        q = request.query_params
        return JSONResponse(await asyncio.to_thread(workspace.pulls, request.path_params["name"],
                                                    q.get("search") or None, q.get("limit") or 50))

    async def discard(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.discard, check_id(request.path_params["id"]),
                                                    body.get("path", "")))

    async def programs(request):
        # ?also=NAME (up to 20): the programs of the person's own agents, whether each is found
        also = [n for n in request.query_params.getlist("also") if isinstance(n, str)][:20]
        return JSONResponse(await asyncio.to_thread(workspace.programs, also))

    async def paths(request):
        return JSONResponse(await asyncio.to_thread(workspace.paths, check_id(request.path_params["id"])))

    async def search(request):
        q = request.query_params
        return JSONResponse(await asyncio.to_thread(workspace.search, check_id(request.path_params["id"]),
                                                    q.get("q", ""), q.get("case") == "1", q.get("regex") == "1"))

    async def pull_request(request):
        return JSONResponse(await asyncio.to_thread(workspace.pull_request, check_id(request.path_params["id"]),
                                                    request.query_params.get("url")))

    async def pr_feedback(request):
        return JSONResponse(await asyncio.to_thread(workspace.pr_feedback, check_id(request.path_params["id"]),
                                                    request.query_params.get("url")))

    async def merge(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.merge, check_id(request.path_params["id"]),
                                                    body.get("method", "squash"), body.get("auto") is True,
                                                    body.get("url")))

    # Linear, for a session's task (linear.py): the person's key, kept here and never given back, and their issues

    async def linear_status(request):
        return JSONResponse(await asyncio.to_thread(linear.status, os.path.expanduser("~")))

    async def linear_key(request):
        home = os.path.expanduser("~")
        if request.method == "DELETE":
            return JSONResponse(await asyncio.to_thread(linear.remove, home))
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(linear.save, home, body.get("key")))

    async def linear_issues(request):
        q = request.query_params
        try:
            limit = int(q.get("limit") or linear.MAX_ISSUES)
        except ValueError:
            raise WorkspaceError("The limit is a number.") from None
        return JSONResponse(await asyncio.to_thread(linear.issues, os.path.expanduser("~"), q.get("search"), limit))

    # Proofpane's core (core_plugin.py): the pairing, kept here and never given back, and what each claude start is
    # given

    async def proofpane_status(request):
        return JSONResponse(await asyncio.to_thread(workspace.core_status))

    async def proofpane_pair(request):
        home = os.path.expanduser("~")
        if request.method == "DELETE":
            await asyncio.to_thread(core.unpair, home)
        else:
            body = await _body(request)
            await asyncio.to_thread(core.pair, home, body.get("code"), body.get("cloud_url"), body.get("holds"))
        return JSONResponse(await asyncio.to_thread(workspace.core_status))

    async def proofpane_holds(request):
        body = await _body(request)
        await asyncio.to_thread(core.set_holds, os.path.expanduser("~"), body.get("holds"))
        return JSONResponse(await asyncio.to_thread(workspace.core_status))

    # a session's program asking (asks.py, command.py): its own token names it; reads see its own repository's sessions only

    def asker_of(request):
        given = request.headers.get("authorization", "")
        return workspace.asker(given[len("Bearer "):] if given.startswith("Bearer ") else "")

    ask_diffs = asyncio.Semaphore(2)
    unasked = JSONResponse({"detail": "Not a running session's token, or the person has not let this workspace's "
                                      "agents ask for sessions."}, status_code=401)

    async def ask_sessions(request):
        bound = asker_of(request)
        if bound is None:
            return unasked
        if request.method == "POST":
            body = await _body(request, MAX_ASK_BODY)
            # while this ask is being taken, the same ask from the same session is refused, saying that this one is
            # still being taken (Workspace.taking_ask): the read below can outlast `proofpane ask`'s
            # wait, and until the ask is taken `proofpane asks` cannot show it
            with workspace.taking_ask(bound, body.get("program"), body.get("task"), body.get("base"),
                                      body.get("effort")):
                # what the installed claude offers, for an ask that names an effort: read first, off the
                # event loop, as the capabilities route reads it, for the one ask that makes a first read waits for it;
                # any other such ask meanwhile is refused at once (Workspace.ask_offered)
                offered = await asyncio.to_thread(workspace.ask_offered, body.get("program"), body.get("effort"))
                # read again after every wait: the switch may have gone off while the body came, or the session been
                # removed (its token revoked, its asks withdrawn) while claude was read; then the ask is taken with
                # nothing awaited between, so no removal comes after this read and before the ask
                bound = asker_of(request)
                if bound is None:
                    return unasked
                return JSONResponse(workspace.ask(bound, body.get("program"), body.get("task"), body.get("base"),
                                                  body.get("effort"), offered))
        return JSONResponse(await asyncio.to_thread(workspace.ask_sessions, bound))

    async def ask_session(request):
        bound = asker_of(request)
        if bound is None:
            return unasked
        return JSONResponse(await asyncio.to_thread(workspace.ask_session, bound, request.path_params["id"]))

    async def ask_asks(request):
        bound = asker_of(request)
        if bound is None:
            return unasked
        return JSONResponse(await asyncio.to_thread(workspace.asks_of, bound))

    async def ask_diff(request):
        bound = asker_of(request)
        if bound is None:
            return unasked
        async with ask_diffs:                   # a diff is git work on a worktree: a few at a time, whoever asks
            return JSONResponse(await asyncio.to_thread(workspace.ask_diff, bound, request.path_params["id"]))

    # the person's side of asks: whether they are on, the asks waiting, and each one started or refused

    async def asks_view(request):
        return JSONResponse(await asyncio.to_thread(workspace.asks_view))

    async def asks_settings(request):
        body = await _body(request)
        return JSONResponse(await asyncio.to_thread(workspace.set_asks_on, body.get("on")))

    def ask_id(request):
        got = request.path_params["ask"]
        if not isinstance(got, str) or not _ASK_ID.match(got):
            raise NotFound("No such ask.")
        return got

    async def ask_start(request):
        body = await _body(request)
        cols, rows = _size(body)
        # the person's choice at Start: an effort, for a claude ask alone, checked by prepare inside the try
        # below that puts the ask back: refused when it does not have a level's name's shape, and, once the new
        # worktree is made, refused when the claude that starts there does not offer it, or while what that claude
        # offers is claude_cli's floor, as the ask's own effort is refused (asks.check_effort).
        # None given and "" are Claude Code's default, never the ask's: a page from before this sends {}, its person
        # having seen no effort. Any other setting given is refused there, as on any session the person starts. The
        # taken ask is never written: what goes back is the agent's own ask.
        chosen = {k: body[k] for k in ("effort", "model", "permission_mode", "may_dispatch") if k in body}
        async with lock:
            # held as being started until it is answered below or put back (asks.Asks.take): the
            # asking session reads it as starting meanwhile, not as gone, since a Start that fails puts it back
            ask = workspace.asks.take(ask_id(request), starting=True)
            if ask is None:
                raise NotFound("That ask has been answered, or is gone.")
            record = None
            try:
                record, argv = await asyncio.to_thread(workspace.prepare, ask["repo"], ask["program"], ask["task"],
                                                       ask["base"], chosen, None, None, None, ask["from"])
                view = workspace.launch(record["id"], argv, cols, rows)
            except Exception:
                # the session half made goes, and the ask back, to be answered again once what stopped it is right
                if record is not None:
                    try:
                        await workspace.remove(record["id"])
                    except Exception:  # noqa: BLE001  the ask goes back whatever the clean-up found
                        pass
                workspace.asks.put_back(ask)
                raise
        # the effort it started with, said beside its view: what the app records of the answer, the person's
        # choice, since a Start whose claude does not offer it is refused above
        effort = workspace.effort_of(view["id"])
        workspace.ask_answered(ask, "started", view["id"], effort)
        return JSONResponse(dict(view, effort=effort))

    async def ask_refuse(request):
        # held as a Start holds it until the refusal is written below (asks.Asks.take, Workspace.ask_answered): the
        # asking session reads it as starting until then, and then as refused, never as gone between the two (taken
        # with no hold, it would read as gone, and `proofpane ask --wait` would end there)
        ask = workspace.asks.take(ask_id(request), starting=True)
        if ask is None:
            raise NotFound("That ask has been answered, or is gone.")
        workspace.ask_answered(ask, "refused")
        return JSONResponse(workspace.asks_view())

    # the controller (controller.py): the app reads its record and sends the owner's side; neither route answers
    # 404, which the app takes, from both, as a workspace from before them

    async def controller_record(request):
        since = request.query_params.get("since", "0")
        if not re.fullmatch(r"[0-9]{1,16}", since):
            raise control.ControllerError("Ask for the record since a change number.")
        return JSONResponse(await asyncio.to_thread(workspace.controller.page, int(since)))

    async def controller_mirror(request):
        # the controller takes the start lock itself, once its account's check (which can read claude) is made
        body = await _body(request, limit=control.MIRROR_MAX)
        held = await workspace.controller.take(body)
        # a side taken that the workspace did not hold already is activity: the owner changed something. One refused
        # is not, nor the same side sent again while a page says none is held (a fresh chat or End all waiting)
        if workspace.controller.new_side:
            workspace.touch()
        return JSONResponse(held)

    async def controller_managers(request):
        # a requirement's own manager: every manager's state and the
        # attempt to run, read to MANAGERS_MAX; the controller takes the start lock itself, once its side's check (which
        # can read claude) is made
        body = await _body(request, limit=control.MANAGERS_MAX)
        return JSONResponse(await workspace.controller.take_managers(body))

    app = Starlette(lifespan=lifespan, routes=[
        Route("/health", health),
        Route("/controller/record", controller_record),
        Route("/controller", controller_mirror, methods=["PUT"]),
        Route("/controller/managers", controller_managers, methods=["PUT"]),
        Route("/repos", repos, methods=["GET", "POST"]),
        Route("/repos/link", repo_link, methods=["POST"]),
        Route("/repos/{name}", repo_unlink, methods=["DELETE"]),
        Route("/repos/{name}/issues", issues),
        Route("/repos/{name}/pulls", pulls),
        Route("/sessions", sessions, methods=["GET", "POST"]),
        Route("/sessions/{id}", session, methods=["GET", "DELETE"]),
        Route("/sessions/{id}/start", start, methods=["POST"]),
        Route("/sessions/{id}/stop", stop, methods=["POST"]),
        Route("/sessions/{id}/close", close_session, methods=["POST"]),
        Route("/sessions/{id}/open", open_session, methods=["POST"]),
        Route("/sessions/{id}/title", title, methods=["POST"]),
        Route("/sessions/{id}/group", group, methods=["POST"]),
        Route("/model-providers", model_providers),
        Route("/model-providers/{id}", model_provider, methods=["PUT", "DELETE"]),
        Route("/sessions/{id}/finish", finish, methods=["POST"]),
        Route("/sessions/{id}/pr/review", pr_review, methods=["POST"]),
        Route("/sessions/{id}/update", update, methods=["GET", "POST"]),
        Route("/sessions/{id}/land", land, methods=["POST"]),
        Route("/sessions/{id}/update/abort", update_abort, methods=["POST"]),
        Route("/sessions/{id}/usage", session_usage),
        Route("/map", session_map_all),
        Route("/skills", skills_page),
        Route("/sessions/{id}/map", session_map),
        Route("/sessions/{id}/diff", changes),
        Route("/sessions/{id}/observed", observed),
        Route("/sessions/{id}/files", files),
        Route("/sessions/{id}/file", file, methods=["GET", "PUT"]),
        Route("/sessions/{id}/image", image),
        Route("/sessions/{id}/ship", ship, methods=["POST"]),
        Route("/sessions/{id}/pr", pull_request),
        Route("/sessions/{id}/pr/feedback", pr_feedback),
        Route("/sessions/{id}/paths", paths),
        Route("/sessions/{id}/search", search),
        Route("/sessions/{id}/discard", discard, methods=["POST"]),
        Route("/sessions/{id}/pr/merge", merge, methods=["POST"]),
        Route("/sessions/{id}/gate", gate_log),
        Route("/sessions/{id}/ports", session_ports),
        Route("/sessions/{id}/shell", shell_start, methods=["POST"]),
        Route("/sessions/{id}/shell/{n:int}/stop", shell_stop, methods=["POST"]),
        Route("/sessions/{id}/shell/{n:int}", shell_close, methods=["DELETE"]),
        Route("/github", github),
        Route("/claude", claude),
        Route("/claude/sign-out", claude_sign_out, methods=["POST"]),
        Route("/accounts", accounts_list, methods=["GET", "POST"]),
        Route("/accounts/choose", account_choose, methods=["POST"]),
        Route("/accounts/{id}/sign-out", account_sign_out, methods=["POST"]),
        Route("/accounts/{id}", account_remove, methods=["DELETE"]),
        Route("/sessions/{id}/sign-in-code", sign_in_code, methods=["POST"]),
        Route("/sessions/{id}/chat", chat_log, methods=["GET", "POST"]),
        Route("/sessions/{id}/chat/answer", chat_answer, methods=["POST"]),
        Route("/sessions/{id}/chat/interrupt", chat_interrupt, methods=["POST"]),
        Route("/sessions/{id}/chat/settings", chat_settings, methods=["POST"]),
        Route("/sessions/{id}/chat/mcp", chat_mcp, methods=["GET", "POST"]),
        Route("/sessions/{id}/upload", upload, methods=["POST"]),
        Route("/moves/{seq}/{file}", move_file, methods=["PUT"]),
        Route("/capabilities", capabilities),
        Route("/programs", programs),
        Route("/claude/update", claude_update, methods=["GET", "POST"]),
        Route("/linear", linear_status),
        Route("/linear/key", linear_key, methods=["PUT", "DELETE"]),
        Route("/linear/issues", linear_issues),
        Route("/proofpane", proofpane_status),
        Route("/proofpane/pair", proofpane_pair, methods=["PUT", "DELETE"]),
        Route("/proofpane/holds", proofpane_holds, methods=["PUT"]),
        Route("/ask/sessions", ask_sessions, methods=["GET", "POST"]),
        Route("/ask/asks", ask_asks),
        Route("/ask/sessions/{id}", ask_session),
        Route("/ask/sessions/{id}/diff", ask_diff),
        Route("/asks", asks_view),
        Route("/asks/settings", asks_settings, methods=["POST"]),
        Route("/asks/{ask}/start", ask_start, methods=["POST"]),
        Route("/asks/{ask}/refuse", ask_refuse, methods=["POST"]),
        Route("/skill-improve/record", skill_record, methods=["GET", "PUT"]),
        Route("/skill-improve/record/restore", skill_record_restore, methods=["POST"]),
        Route("/skill-improve/switch", skill_switch, methods=["PUT"]),
        Route("/skill-improve/candidates", skill_candidates),
        Route("/skill-improve/branches", skill_branches),
        Route("/skill-improve/reviews", skill_review, methods=["POST"]),
        Route("/skill-improve/reviews/{id}/objects", skill_review_objects, methods=["POST"]),
        Route("/skill-improve/reviews/{id}/critic", skill_review_critic, methods=["POST"]),
        Route("/skill-improve/proposals/{id}/{action}", skill_proposal, methods=["POST"]),
        Route("/agents/runs", agents_runs),
        Route("/agents/runs/{id}/stop", run_stop, methods=["POST"]),
        Route("/agents/runs/{id}/tree", run_tree),
        Route("/agents/runs/{id}/message", run_message, methods=["POST"]),
        Route("/agents/{id}/facts", agent_facts, methods=["PUT"]),
        Route("/agents/{id}/memory", agent_memory, methods=["GET", "PUT"]),
        Route("/agents/{id}/memory/restore", agent_memory_restore, methods=["POST"]),
        Route("/agents/runs/{id}/memory", run_memory, methods=["GET"]),
        Route("/agents/runs/{id}/judge", run_judge, methods=["POST"]),
        Route("/agents/runs/{id}/critic", run_critic, methods=["POST"]),
        Route("/agents/judge-pair", judge_pair, methods=["POST"]),
        Route("/agents/sweep", agents_sweep, methods=["GET", "POST"]),
        Route("/agents/facts-versions", agents_facts_versions),
        # before /agents/{id}, which would take each word as an agent's id
        Route("/agents/backends", agents_backends),
        Route("/agents/identity", agents_identity, methods=["POST"]),
        Route("/agents/tasks/{task}/attempts", task_attempts),
        Route("/agents/tasks/{task}/worktree", task_worktree, methods=["POST"]),
        Route("/agents/tasks/{task}/push", task_push, methods=["POST"]),
        Route("/agents/tasks/{task}/fetch", task_fetch, methods=["POST"]),
        Route("/agents/tasks/{task}/checks", task_checks, methods=["POST"]),
        Route("/agents/tasks/{task}", task_delete, methods=["DELETE"]),
        Route("/agents/{id}", agent_one, methods=["GET", "PUT"]),
        Route("/agents/{id}/runs", agent_queue, methods=["POST"]),
        Route("/agents/{id}/retire", agent_retire, methods=["POST"]),
        Route("/agents/{id}/mcp-secrets", agent_secrets, methods=["GET", "PUT"]),
        WebSocketRoute("/chat", chat_live),
        WebSocketRoute("/sessions/{id}/terminal", terminal),
        WebSocketRoute("/sessions/{id}/shell/{n:int}", shell_terminal),
    ], exception_handlers={
        NotFound: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=404),
        Conflict: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=412),
        skillimp.WindowHeld: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=503),
        WorkspaceError: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=400),
        providers.ProviderError: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=400),
        linear.LinearError: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=400),
        core.CoreError: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=400),
        terminals.TerminalError: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=400),
        chats.ChatError: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=400),
        agentry.AgentNotFound: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=404),
        agentry.TaskConflict: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=409),
        agentry.AgentError: lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=400),
    })
    app.state.workspace = workspace
    return _Secret(app, secret, on_activity=workspace.touch, on_page=workspace.page)


async def _pump(ws, term, queue):
    """Output to the viewer and input to the program, until either side ends."""

    async def out():
        while True:
            item = await queue.get()
            chunks = []
            while isinstance(item, bytes):
                chunks.append(item)
                if queue.empty():
                    item = None
                    break
                item = queue.get_nowait()
            if chunks:
                await ws.send_bytes(b"".join(chunks))
            if item is terminals.EXITED:
                await ws.send_text(json.dumps({"type": "exit", "code": term.exit_code}))
                return
            if item is terminals.DROPPED:
                await ws.send_text(json.dumps({"type": "dropped"}))
                return

    async def into():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await term.write(msg["bytes"])
            elif msg.get("text") is not None:
                try:
                    ctl = json.loads(msg["text"]) if len(msg["text"]) <= 4096 else None     # a resize is a few bytes
                except (ValueError, RecursionError):
                    return
                if not isinstance(ctl, dict) or ctl.get("type") != "resize":
                    return
                term.resize(ctl.get("cols"), ctl.get("rows"))

    tasks = [asyncio.ensure_future(out()), asyncio.ensure_future(into())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, terminals.TerminalError)):
                raise exc
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    try:
        await ws.close()
    except RuntimeError:
        pass
