"""The FastAPI app: the API under /api, the static pages at /.

    uvicorn proofpane_open.main:app --host 127.0.0.1 --port 8000

Every tool needs a signed-in account; the public key and the health check do not. The ADE's routes are in
`ade/routes.py`, under /api/ade, and the hosted workspace's, off by default, in `ade/workspace_routes.py`. The Claude
app connector's (/mcp, /oauth/* and /.well-known/oauth-*), off by default, are in `ade/connector_routes.py`.
"""

import json
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import VERSION, auth, build, config, fido_mds, oauth, passkeys, tools
from .ade import actions as ade_actions
from .ade import agents as ade_agents
from .ade import agents_routes as ade_agents_routes
from .ade import approvals as ade_approvals
from .ade import connector_routes as ade_connector_routes
from .ade import progress_board as ade_progress_board
from .ade import controller_routes as ade_controller_routes
from .ade import skill_routes as ade_skill_routes
from .ade import skill_improve as ade_skill_improve
from .ade import skill_improve_routes as ade_skill_improve_routes
from .ade import proposals as ade_proposals
from .ade import facts as ade_facts
from .ade import holds as ade_holds
from .ade import memory_versions as ade_memory_versions
from .ade import suites as ade_suites
from .ade import triggers as ade_triggers
from .ade import custom_agents as ade_custom_agents
from .ade import e2e_keys as ade_e2e_keys
from .ade import e2e_routes as ade_e2e_routes
from .ade import push as ade_push
from .ade import ledger as ade_ledger
from .ade import link as ade_link
from .ade import link_routes as ade_link_routes
from .ade import link_usage as ade_link_usage
from .ade import moves as ade_moves
from .ade import risk_routes as ade_risk_routes
from .ade import routes as ade_routes
from .ade import signer_routes as ade_signer_routes
from .ade import sources as ade_sources
from .ade import storage_routes as ade_storage_routes
from .ade import summary as ade_summary
from .ade import task_offers as ade_task_offers
from .ade import workspace_client as ade_workspace_client
from .ade import workspace_routes as ade_workspace_routes
from .ade import workspaces as ade_workspaces
from .deps import admin_user, bearer, current_user

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")

config.token_secret()  # refuse to start without a signing secret
config.public_url()  # and without its own public address
passkeys.check_config()  # and with a public address whose host a passkey cannot be made for
ade_workspaces.check_config()  # and with a workspace provider that must not run here
oauth.check_config()  # and with the connector on at a public address that is not https

@asynccontextmanager
async def _lifespan(app):
    # the ADE's notifier (ade/push.py) runs in this process while it serves; tests, which never enter the lifespan,
    # start none
    ade_push.start()
    ade_moves.start()           # what time bounds of the moves into the workspace (ade/moves.py)
    ade_link_usage.start()      # the linked sessions' usage that waits to go to each account's Proofpane, and
                                # what was sent or dropped, forgotten after 30 days (ade/link_usage.py)
    ade_e2e_keys.start()        # a compaction after sealing in place that a reader or a stop kept from completing
    oauth.start()               # the Claude app connector's codes, tokens, events and counts, cleared past their use
    # the FIDO metadata, kept fresh once a blob is wanted; a new one judges again the passkeys waiting for one, and
    # makes device passkeys of the hardware keys whose model it reports revoked or compromised
    fido_mds.start(passkeys.blob_arrived, passkeys.awaiting_judgment)
    ade_workspaces.start()      # the fly provider's spending meter
    if ade_workspaces.provider() is not None:
        ade_agents.start_poller()       # agents' runs copied back from running machines (ade/agents.py)
        # the app's word on each workspace's record of skill proposals (ade/skill_improve.py)
        ade_skill_improve.start_word_sender()
        ade_triggers.start_scheduler()  # their schedules, fired by the app, which runs while machines stop
    yield
    ade_workspaces.shutdown()   # the local provider's workspaces are this process's children


app = FastAPI(title="Proofpane Community", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=_lifespan)


class _NoStoreLinks:
    """Every answer under the session channel's routes, the second screen's (ade/signer_routes.py: steps parked for a
    signer and the assertions over them), and the Claude app connector's (its codes and tokens must never be kept,
    RFC 6749 5.1), carries Cache-Control: no-store, whoever wrote it: its own answers, its refusals, the sign-in
    check's 401 and FastAPI's own 422 for a query or body it cannot read."""

    PREFIXES = ("/api/ade/link/", "/api/ade/signer/", "/api/ade/signers", "/api/ade/sign-requests", "/oauth/",
                "/api/connector")
    EXACT = ("/mcp",)

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path.startswith(self.PREFIXES) and path not in self.EXACT:
            return await self.inner(scope, receive, send)

        async def send_no_store(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = dict(message, headers=headers + [(b"cache-control", b"no-store")])
            await send(message)

        await self.inner(scope, receive, send_no_store)


MAX_NESTING = 64       # a JSON body's arrays and objects inside one another; the API's own nest a few deep
MAX_BODY = 1024 * 1024          # a request's body, read before any sign-in is checked: kept small
MAX_BODY_LARGE = 8 * 1024 * 1024    # on the routes that take a file's text (twice config.MAX_EVIDENCE, as JSON text)
# the routes a body that large is taken on: a change or a record to check, and a file saved from the editor
_LARGE_BODY = re.compile(r"^/api/(?:verify|check/(?:manifest|chain))$|^/api/ade/workspace/sessions/[^/]+/file$")
# a link's History list and a conversation, which grow by a third once sealed (end-to-end encryption, ade/e2e.py):
# Caddy lets these two through to 2 MiB (the Caddyfile's @link_history), and the app reads as much of them
MAX_BODY_HISTORY = 2 * 1024 * 1024
_HISTORY_BODY = re.compile(r"^/api/ade/link/history(?:/talk)?$")
# the Claude app connector's OAuth routes (oauth.py), which take no sign-in: a registration and a token request are a
# few hundred bytes, and Caddy lets through 64 KB (the Caddyfile's @connector), which the app holds to as well, so a
# deployment run without Caddy reads no more of them
_OAUTH_BODY = re.compile(r"^/oauth/")
MAX_BODY_INLINE = 256 * 1024    # a body larger than this is checked in a worker thread, off the event loop
# the routes whose body is a file's bytes, not JSON, each streaming the body itself to its own limit: a file sent to a
# session, and a move's transcript and bundle (ade/moves.py)
_RAW_BODY = re.compile(r"^/api/ade/workspace/sessions/[^/]+/upload$|^/api/ade/link/moves/[^/]+/(?:transcript|bundle)$")
_JSON_STRING = re.compile(rb'"(?:[^"\\]++|\\.)*+(?:"|\\?\Z)', re.S)     # to its end when it is never closed
_NOT_A_BRACKET = bytes(b for b in range(256) if b not in b"[]{}")


def nests_too_deep(body, limit=MAX_NESTING):
    """Whether a JSON body nests more than `limit` arrays or objects inside one another, read without parsing it: its
    strings taken out and the rest kept to its brackets, both in C, and then the brackets counted."""
    if body.count(b"[") + body.count(b"{") <= limit:
        return False
    depth = 0
    for b in _JSON_STRING.sub(b"", body).translate(None, _NOT_A_BRACKET):
        if b == 0x5B or b == 0x7B:
            depth += 1
            if depth > limit:
                return True
        else:
            depth -= 1
    return False


def holds_half_a_pair(body):
    """Whether a JSON body holds half of a UTF-16 surrogate pair (an escape such as \\ud83d without its other half, or
    the bytes of one), which Python's JSON reader takes as text but nothing that writes UTF-8 can write: the database,
    a file, an agent's input. Each string of the body is read on its own, so no list or object is ever built here; a
    string that is not one (never closed) is the route's to refuse."""
    if b"\\u" not in body and b"\xed" not in body:
        return False
    for m in _JSON_STRING.finditer(body):
        literal = m.group(0)
        if b"\\u" not in literal and b"\xed" not in literal:
            continue
        try:
            json.loads(literal).encode("utf-8")
        except UnicodeEncodeError:
            return True
        except ValueError:
            continue
    return False


def body_problem(body):
    """Why a request's body is refused before anything reads it, or None: not UTF-8 (JSON's own detection, which reads
    UTF-16 and UTF-32 too), nested past MAX_NESTING, or holding half of a character."""
    if not body:
        return None
    if json.detect_encoding(body) not in ("utf-8", "utf-8-sig"):
        return "The request is not UTF-8."
    if nests_too_deep(body):
        return "The request nests too deep."
    if holds_half_a_pair(body):
        return "The request holds half of a character (an unpaired surrogate), which is not text."
    return None


def _refuse(status, detail):
    return JSONResponse(status_code=status, content={"detail": detail})


class _NestingLimit:
    """Each request's body is read and checked before anything else reads it: larger than MAX_BODY is refused (413),
    or than MAX_BODY_LARGE on the routes that take a file's text (_LARGE_BODY), or than MAX_BODY_HISTORY on a link's
    PUT of its History list and of a conversation (_HISTORY_BODY), or than oauth.BODY_MAX on the connector's OAuth
    routes (_OAUTH_BODY); JSON in UTF-16 or UTF-32, nested deeper
    than MAX_NESTING or holding half of a character is refused (400, body_problem), the same on every Python (3.13's
    reader ran out of stack on a body thousands deep, which the RecursionError handler below answers, but 3.14's reads
    it whole, and what reads the result next may not). Every body is checked, whatever its type says, since FastAPI
    reads a body with no type as JSON and the session channel's routes read theirs as JSON whatever it says; but a
    file's bytes sent to a session, and a move's transcript and bundle, which their routes stream to their own
    limit."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _RAW_BODY.match(scope.get("path") or ""):
            return await self.inner(scope, receive, send)
        path = scope.get("path") or ""
        most = MAX_BODY_LARGE if _LARGE_BODY.match(path) else MAX_BODY_HISTORY if (
            scope.get("method") == "PUT" and _HISTORY_BODY.match(path)) else oauth.BODY_MAX if _OAUTH_BODY.match(path) \
            else MAX_BODY
        held, chunks, size = [], [], 0
        while True:
            message = await receive()
            held.append(message)
            if message["type"] != "http.request":
                break                   # the sender went away before its body came: nothing to check
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > most:
                return await _refuse(413, "The request is larger than the server takes.")(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        problem = await run_in_threadpool(body_problem, body) if len(body) > MAX_BODY_INLINE else body_problem(body)
        if problem:
            return await _refuse(400, problem)(scope, receive, send)

        async def replay():
            return held.pop(0) if held else await receive()

        await self.inner(scope, replay, send)


app.add_middleware(_NestingLimit)
app.add_middleware(_NoStoreLinks)
# the phone app's pages, which are its own, call the API across origins (config.app_origins); no cookies are used
app.add_middleware(CORSMiddleware, allow_origins=list(config.app_origins()), allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"], max_age=600)


@app.exception_handler(auth.TooManyAttempts)
def _too_many_attempts(request, exc):
    return JSONResponse(status_code=429, content={"detail": str(exc)}, headers={"Retry-After": str(exc.wait)})


@app.exception_handler(auth.AuthError)
def _auth_error(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(auth.SignedOut)
def _signed_out(request, exc):
    return JSONResponse(status_code=401, content={"detail": str(exc)})


@app.exception_handler(tools.ToolError)
def _tool_error(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ade_sources.SourceError)
@app.exception_handler(ade_ledger.LedgerError)
@app.exception_handler(ade_actions.ActionError)
@app.exception_handler(ade_approvals.ApprovalError)
@app.exception_handler(ade_push.PushError)
def _ade_error(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ade_link.LinkError)
def _link_error(request, exc):
    # the session channel's refusals: no-store like its answers, and what the design says each carries (the server's
    # time with a 401 for a skewed clock, the held version and document with a 409 for a stale checkpoint)
    return JSONResponse(status_code=exc.status, content=dict({"detail": str(exc)}, **exc.extra),
                        headers=ade_link_routes.NO_STORE)


@app.exception_handler(oauth.OAuthError)
def _oauth_error(request, exc):
    # the token and registration endpoints' refusals, as RFC 6749 (5.2) and RFC 7591 (3.2.2) write them
    return JSONResponse(status_code=exc.status, content={"error": exc.error, "error_description": exc.description})


@app.exception_handler(oauth.ConnectorError)
@app.exception_handler(ade_proposals.ProposalError)
def _connector_error(request, exc):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(RecursionError)
def _too_deep(request, exc):
    # a body nested thousands deep ("[[[[...") makes the JSON reader recurse out of the stack; that is the sender's
    # request refused, not a fault of the server's
    return JSONResponse(status_code=400, content={"detail": "The request nests too deep."})


@app.exception_handler(passkeys.PasskeyError)
def _passkey_error(request, exc):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(ade_agents.AgentError)
@app.exception_handler(ade_holds.HoldError)
@app.exception_handler(ade_suites.SuiteError)
@app.exception_handler(ade_facts.FactError)
@app.exception_handler(ade_memory_versions.MemoryVersionError)
def _agent_error(request, exc):
    # an agent's or a task's refusal may carry members beside its detail (the teammate track's "off"); the detail is
    # always its own
    return JSONResponse(status_code=exc.status, content=dict(getattr(exc, "extra", None) or {}, detail=str(exc)))


@app.exception_handler(ade_workspaces.WorkspaceAccess)
@app.exception_handler(ade_workspace_client.WorkspaceCallError)
def _workspace_error(request, exc):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


class Login(BaseModel):
    email: str
    password: str


class InvitationIn(BaseModel):
    token: str


class Register(BaseModel):
    token: str
    password: str


class DeviceStart(BaseModel):
    name: str


class DeviceCode(BaseModel):
    code: str
    # to approve: from /api/account/confirm, made for this code's request (`device` there); a hardware key's while
    # high security is on
    confirmation: Optional[str] = None


class DevicePoll(BaseModel):
    id: str
    poll: str


class Invite(BaseModel):
    email: str


class PasskeyIn(BaseModel):
    challenge_id: str
    credential: dict
    name: Optional[str] = None
    prf: Optional[bool] = None              # the browser's word that a new passkey gives the PRF extension


class PasskeyRenameIn(BaseModel):
    name: str                               # empty: named by what it is again (passkeys.rename)
    confirmation: Optional[str] = None      # the person's word, made for this passkey's rename (passkeys.confirm)


class PasskeyOptionsIn(BaseModel):
    confirmation: Optional[str] = None      # from /api/account/confirm, when the account has a way to make one
    recovery_proof: Optional[dict] = None   # while end-to-end encryption is on: the recovery code's proof instead


class ConfirmIn(BaseModel):
    # the account's password, or a passkey's answer to /api/account/confirm/options
    password: Optional[str] = None
    challenge_id: Optional[str] = None
    credential: Optional[dict] = None
    # the code of a device waiting to be signed in: the confirmation approves that device, and nothing else
    device: Optional[str] = None
    # or the change the confirmation is for (passkeys.CHANGE: "link-revoke:<link id>", say), and nothing else
    change: Optional[str] = None


class ConfirmationIn(BaseModel):
    confirmation: Optional[str] = None      # from /api/account/confirm, when the account has a way to make one


class SignInOptionsIn(BaseModel):
    # the ceremony the page asks for, passkeys.SIGN_IN, which the challenge it signs is derived for; a page from an
    # older release names none (passkeys.OLD_PAGE)
    purpose: Optional[str] = None


class ConfirmOptionsIn(BaseModel):
    hardware: bool = False                  # offer the account's hardware keys alone
    # the ceremony the page asks for (passkeys.CONFIRM_PURPOSES), which the challenge it signs is derived for; a page
    # from an older release names none (passkeys.OLD_PAGE)
    purpose: Optional[str] = None
    device: Optional[str] = None            # as ConfirmIn's: the confirmation these options give approves that device
    change: Optional[str] = None            # as ConfirmIn's: or is for that change


class HighSecurityIn(BaseModel):
    on: bool
    confirmation: Optional[str] = None      # a hardware key's, from /api/account/confirm


class VerifyIn(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    diff: str
    repo: Optional[str] = None
    base_sha: Optional[str] = None
    head_sha: Optional[str] = None


class FileIn(BaseModel):
    text: str


class SignatureIn(BaseModel):
    value: Any
    signature: Any


@app.get("/api/health")
def health(token=Depends(bearer)):
    # with the release this build was made from, as the image kept it when it was built (build.py), "unknown" where it
    # kept none: which build answers, for anyone who asks; and to a signed-in account the commit too, the one it was
    # built from. It needs no sign-in, and one that is not valid now is none
    kept = build.info()
    signed_in = token is not None and auth.token_user(token) is not None
    return {"ok": True, "version": VERSION, "release": kept["release"], **({"commit": kept["commit"]} if signed_in else {})}


@app.post("/api/auth/login")
def login(body: Login, request: Request):
    user_id = auth.login(body.email, body.password, _client(request))
    return {"token": auth.issue_token(user_id), "user": auth.user(user_id)}


@app.post("/api/auth/invitation")
def invitation(body: InvitationIn):
    # POST, with the token in the body: a token in a URL would be written to the server's access log
    email = auth.invitation_email(body.token)
    if email is None:
        raise HTTPException(status_code=404, detail="This invitation link is not valid: it may have been used "
                                                    "already, or have expired.")
    return {"email": email}


@app.post("/api/auth/register")
def register(body: Register):
    user_id, _ = auth.register(body.token, body.password)
    return {"token": auth.issue_token(user_id), "user": auth.user(user_id)}


# a device signed in by another, signed-in one (auth.py says how): the asking device's two routes need no sign-in, the
# approving account's need its own, and approving takes a confirmation made for that device as well

def _client(request: Request):
    """The address a request came from, as the server sees it (behind the hosted site's proxy, the client's)."""
    return request.client.host if request.client else ""


@app.post("/api/auth/device/start")
def device_start(body: DeviceStart, request: Request):
    return auth.start_device_signin(body.name, _client(request))


@app.post("/api/auth/device/poll")
def device_poll(body: DevicePoll):
    return auth.poll_device_signin(body.id, body.poll)


@app.post("/api/auth/device/lookup")
def device_lookup(body: DeviceCode, request: Request, user=Depends(current_user)):
    return auth.device_signin_view(body.code, _client(request))


@app.post("/api/auth/device/approve")
def device_approve(body: DeviceCode, user=Depends(current_user), token=Depends(bearer)):
    # the sign-in is checked again inside the approval: signed out everywhere meanwhile, it approves nothing
    done = auth.decide_device_signin(user["id"], body.code, True, confirmation=body.confirmation, approver=token)
    # in fixed words (ade/push.py words): never the device's name, the account's address or where it asked from
    ade_push.notice(user["id"], ade_push.words("device-signed-in"))
    return done


@app.post("/api/auth/device/refuse")
def device_refuse(body: DeviceCode, user=Depends(current_user)):
    return auth.decide_device_signin(user["id"], body.code, False)


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user


# passkeys (passkeys.py): signing in with one needs no account named; adding and removing one needs the sign-in and a
# confirmation, as signing out everywhere does

@app.post("/api/auth/passkey/options")
def passkey_signin_options(body: Optional[SignInOptionsIn] = None):
    return passkeys.signin_options(purpose=body.purpose if body else None)


@app.post("/api/auth/passkey")
def passkey_signin(body: PasskeyIn):
    user_id = passkeys.sign_in(body.challenge_id, body.credential)
    return {"token": auth.issue_token(user_id), "user": auth.user(user_id)}


@app.get("/api/account/confirm")
def confirm_ways(user=Depends(current_user)):
    return passkeys.confirm_ways(user["id"])


def _subject(device, change):
    """The one change a confirmation is asked for, or None (end-to-end encryption's touches, which their purpose
    binds): approving the device waiting with the code `device` (auth.approval_subject, 400 when none waits with it),
    or `change` (passkeys.change_subject, 422 for one not of its forms). Both at once are refused."""
    if device is not None and change is not None:
        raise HTTPException(status_code=422, detail="Name one change: a device's code, or a change.")
    if device is not None:
        return auth.approval_subject(device)
    return passkeys.change_subject(change) if change is not None else None


@app.post("/api/account/confirm/options")
def confirm_options(body: Optional[ConfirmOptionsIn] = None, user=Depends(current_user)):
    return passkeys.confirm_options(user, hardware=bool(body and body.hardware),
                                    purpose=body.purpose if body else None,
                                    subject=_subject(body.device, body.change) if body else None)


@app.post("/api/account/confirm")
def confirm(body: ConfirmIn, request: Request, user=Depends(current_user)):
    # the person's own word that a change is theirs, beside the sign-in, which may have been taken: one of the
    # account's passkeys, or its password
    if (body.password is None) == (body.challenge_id is None and body.credential is None):
        raise HTTPException(status_code=400, detail="Give the account's password, or a passkey's answer.")
    return passkeys.confirm(user, body.challenge_id, body.credential, body.password, client=_client(request),
                            subject=_subject(body.device, body.change))


@app.get("/api/account/passkeys")
def list_passkeys(user=Depends(current_user)):
    # each passkey with its tier, whether high security is on, and whether a valid copy of the FIDO metadata is here
    return {"passkeys": passkeys.for_user(user["id"]), "high_security": passkeys.high_security(user["id"]),
            "metadata": fido_mds.status()}


@app.put("/api/account/high-security")
def high_security(body: HighSecurityIn, user=Depends(current_user)):
    # on or off, each with a hardware key's confirmation; while on, only a hardware key confirms a risky change or
    # signs a command to a linked session
    done = passkeys.set_high_security(user["id"], body.on, body.confirmation)
    ade_push.notice(user["id"], ade_push.words("high-security-on" if body.on else "high-security-off"))
    return done


@app.post("/api/account/passkeys/options")
def passkey_add_options(body: Optional[PasskeyOptionsIn] = None, user=Depends(current_user)):
    return passkeys.registration_options(user, body.confirmation if body else None,
                                         recovery_proof=body.recovery_proof if body else None)


@app.post("/api/account/passkeys")
def passkey_add(body: PasskeyIn, user=Depends(current_user)):
    added = passkeys.register(user, body.challenge_id, body.credential, body.name, prf=body.prf)
    if "held" in added:
        # end-to-end encryption is on: the passkey waits on a hold, whose own notice went to the account's devices
        return added
    ade_push.notice(user["id"], ade_push.words("passkey-added"))
    return added


@app.patch("/api/account/passkeys/{passkey_id}")
def passkey_rename(passkey_id: str, body: PasskeyRenameIn, user=Depends(current_user)):
    # a name is the owner's word on which passkey is which, which removing and revoking go by: renaming one takes the
    # person's word made for it, and its tier and kind, the server's, stay (passkeys.rename); told to every device
    renamed = passkeys.rename(user["id"], passkey_id, body.name, body.confirmation)
    ade_push.notice(user["id"], ade_push.words("passkey-renamed"))
    return renamed


@app.delete("/api/account/passkeys/{passkey_id}")
def passkey_remove(passkey_id: str, body: Optional[ConfirmationIn] = None, user=Depends(current_user)):
    removed = passkeys.remove(user["id"], passkey_id, body.confirmation if body else None)
    # the page's answer says which apps it disconnected (oauth.lost_device_words); the notice, that some were, and
    # opens Connected apps
    ade_push.notice(user["id"], ade_push.words("passkey-removed-apps" if removed["disconnected"]
                                               else "passkey-removed"))
    return removed


@app.post("/api/account/sign-out-everywhere")
def sign_out_everywhere(body: Optional[ConfirmationIn] = None, user=Depends(current_user), token=Depends(bearer)):
    # every token the account holds stops working, and every link ticket not yet used with them; this device gets a new
    # token. It ends no app connection in effect: nothing says the device it ends allowed one, so it answers with those
    # allowed in the last days (oauth.recent_grants), which the page lists with Revoke; the notice says that connected
    # apps keep working, by no name, and opens Connected apps
    passkeys.use_confirmation(user["id"], body.confirmation if body else None, "sign-out-everywhere")
    # a device signed in by another's approval stays that device sign-in, which removing the passkey that approved it
    # ends (auth.devices_going)
    token = auth.sign_out_everywhere(user["id"], device=auth.token_device(token))
    recent = oauth.recent_grants(user["id"])
    apps = oauth.signed_out_words(recent)
    ade_push.notice(user["id"], ade_push.words("signed-out-apps" if recent and apps else "signed-out"))
    return {"token": token, "connected_apps": recent, "apps": apps}


@app.post("/api/admin/invitations")
def create_invitation(body: Invite, user=Depends(admin_user)):
    token = auth.create_invitation(body.email, user["email"])
    return {"email": auth.normalise_email(body.email), "link": f"{config.public_url()}/#invite={token}",
            "expires_in_hours": config.INVITE_TTL_SECONDS // 3600}


@app.get("/api/admin/invitations")
def invitations(user=Depends(admin_user)):
    return {"invitations": auth.list_invitations()}


@app.post("/api/verify")
def verify(body: VerifyIn, user=Depends(current_user)):
    return tools.verify(body.title, body.body, body.diff, body.repo, body.base_sha, body.head_sha)


@app.post("/api/check/manifest")
def check_manifest(body: FileIn, user=Depends(current_user)):
    return tools.check_manifest(body.text)


@app.post("/api/check/chain")
def check_chain(body: FileIn, user=Depends(current_user)):
    return tools.check_chain(body.text)


@app.post("/api/check/signature")
def check_signature(body: SignatureIn, user=Depends(current_user)):
    return tools.check_signature(body.value, body.signature)


@app.get("/api/public-key")
def public_key():
    return tools.public_key()


app.include_router(ade_routes.router)
app.include_router(ade_workspace_routes.router)
app.include_router(ade_storage_routes.router)
app.include_router(ade_custom_agents.router)
app.include_router(ade_link_routes.router)
app.include_router(ade_signer_routes.router)
app.include_router(ade_e2e_routes.router)
app.include_router(ade_agents_routes.router)
app.include_router(ade_task_offers.router)
app.include_router(ade_risk_routes.router)
app.include_router(ade_agents_routes.hooks)
app.include_router(ade_summary.router)
app.include_router(ade_progress_board.router)
app.include_router(ade_controller_routes.router)
app.include_router(ade_skill_routes.router)
app.include_router(ade_skill_improve_routes.router)
# the Claude app connector's routes (/mcp, /oauth/*, /.well-known/oauth-*), before the pages, which take every path
app.include_router(ade_connector_routes.router)


class Pages(StaticFiles):
    """The pages, revalidated on every load, so that after a deploy no browser keeps running an old app.js against
    the new API; an unchanged file costs a 304. The app's built files under /app/assets/ are the exception: Vite
    names each by a hash of what it holds, so a name never comes to hold anything else, and a browser keeps them
    without asking again; the page that names them, /app/index.html, is revalidated like the rest."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        built = path.replace("\\", "/").startswith("app/assets/") and response.status_code in (200, 304)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if built else "no-cache"
        return response


if os.path.isdir(WEB):
    app.mount("/", Pages(directory=WEB, html=True), name="web")
