"""The Claude app connector's HTTP surface (oauth.py says what the authorization server is, ade/connector.py what the
MCP endpoint is). Every route answers 404 while OPEN_EDITION_CONNECTOR is not on, but GET /api/connector, which says so;
every answer under /oauth/, /mcp and /api/connector carries Cache-Control: no-store (main.py), the metadata's alone
may be kept five minutes.

    GET    /.well-known/oauth-protected-resource/mcp     RFC 9728's metadata for /mcp
    GET    /.well-known/oauth-protected-resource         the same, at the root form
    GET    /.well-known/oauth-authorization-server       RFC 8414's metadata
    POST   /oauth/register                               RFC 7591: a client registers itself -> 201
    GET    /oauth/authorize                              the request kept, the browser sent to the app's consent pane;
                                                         or back to the app with an error; or the ADE's error page
    POST   /oauth/token                                  form-encoded: a code exchanged, or a refresh token used
    POST   /mcp                                          the MCP endpoint, with an access token (ade/connector.py);
                                                         GET, DELETE and the rest 405

The page's, each with the sign-in:

    GET    /api/connector                                {on, account_on, url, redirects, client_hosts, hold_seconds}
    PUT    /api/connector/account                        {on, confirmation?}: the account's switch
    GET    /api/connector/requests/{id}                  what the consent pane shows of a request, and from where
    POST   /api/connector/requests/{id}/allow            {confirmation, propose, account} -> {redirect, hold, grant}
    POST   /api/connector/requests/{id}/refuse           -> {redirect}
    GET    /api/connector/grants                         the connected apps, newest first
    DELETE /api/connector/grants/{id}                    revoke one, with the sign-in alone
    GET    /api/connector/grants/{id}/events             its connector events, newest first (?before=ID)
    POST   /api/connector/grants/{id}/refuse-all         {words?}: refuse every proposal of that app that waits
    GET    /api/connector/proposals                      the proposals waiting for the owner, oldest first
    POST   /api/connector/proposals/{id}/refuse          {words?}: the owner refuses one
    POST   /api/connector/proposals/{id}/accept          a proposed stop, accepted with the sign-in alone

An instruction, an answer or a resume a connected app proposed is signed through the session channel's own routes,
its payload's id the proposal's (ade/link_commands.py binds them); a proposed answer to a held command is given on the
command, POST /api/ade/approvals/{id} with {proposal, confirmation} (ade/approvals.py).
"""

import html
import json
import secrets
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import oauth
from ..deps import current_user
from . import connector, holds, proposals

router = APIRouter()
METADATA_CACHE = {"Cache-Control": "max-age=300"}
NOT_FRAMED = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}


def _on():
    if not oauth.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _client(request: Request):
    """The address a request came from, as the server sees it (behind the hosted site's proxy, the client's)."""
    return request.client.host if request.client else ""


class AccountIn(BaseModel):
    on: bool
    confirmation: Optional[str] = None      # from /api/account/confirm, to turn it on


class AllowIn(BaseModel):
    confirmation: Optional[str] = None
    propose: bool = True
    account: Optional[str] = None           # the account the pane showed (its account_id): refused unless signed in


class WordsIn(BaseModel):
    words: Optional[str] = None             # the owner's, for the app to read back


@router.get("/.well-known/oauth-protected-resource/mcp")
@router.get("/.well-known/oauth-protected-resource")
def protected_resource():
    _on()
    return JSONResponse(oauth.protected_resource_metadata(), headers=METADATA_CACHE)


@router.get("/.well-known/oauth-authorization-server")
def authorization_server():
    _on()
    return JSONResponse(oauth.authorization_server_metadata(), headers=METADATA_CACHE)


@router.post("/oauth/register")
async def register(request: Request):
    _on()
    # RFC 7591 (3.1): JSON, said so. A page of another site can send a body of any words without asking first only as
    # text/plain, a form or multipart (fetch's no-cors), so this type makes its visitor's browser ask first, a CORS
    # preflight only the app's own origins pass (main.py), and no visitor of it registers an app here
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise oauth.OAuthError(400, "invalid_client_metadata", "A registration is sent as application/json.")
    try:
        data = json.loads(await request.body() or b"null")
    except (ValueError, RecursionError):
        raise oauth.OAuthError(400, "invalid_client_metadata", "A registration is JSON.") from None
    # its database work waits for the write lock, which another connection may hold for seconds: off the event loop,
    # as /mcp's is, so it stalls no other request meanwhile
    return JSONResponse(await run_in_threadpool(oauth.register, data, _client(request)), status_code=201)


def error_page(words):
    """The ADE's own page for an authorization it cannot send back to the app: text only, never framed."""
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1\"><title>Not connected</title>"
        "<link rel=\"stylesheet\" href=\"/style.css\"></head><body><main><h1>The app was not connected</h1>"
        f"<p>{html.escape(words)}</p><p>Nothing was sent to the app. Close this window, and connect again from the "
        "app.</p></main></body></html>", status_code=400, headers=NOT_FRAMED)


@router.get("/oauth/authorize")
def authorize(request: Request):
    _on()
    params, repeated = {}, False
    for key, value in request.query_params.multi_items():
        repeated = repeated or key in params
        params.setdefault(key, value)
    came = {"mode": request.headers.get("sec-fetch-mode"), "dest": request.headers.get("sec-fetch-dest"),
            "site": request.headers.get("sec-fetch-site"), "referer": request.headers.get("referer"),
            "purpose": request.headers.get("sec-purpose"), "legacy_purpose": request.headers.get("purpose")}
    # the request is bound to this browser by a cookie of its own: a random value, which carries no
    # sign-in, and of which the request keeps only the sha256; the pane warns when another browser shows it
    cookie = secrets.token_urlsafe(32)
    kind, value = oauth.authorize(params, _client(request), repeated=repeated, came=came,
                                  browser=oauth.browser_digest(cookie))
    if kind == "error":
        return error_page(value)
    response = RedirectResponse(value, status_code=302, headers=NOT_FRAMED)
    if kind == "page":
        response.set_cookie(oauth.browser_cookie(value.rsplit("#connect=", 1)[1]), cookie, max_age=oauth.REQUEST_TTL,
                            path="/", secure=True, httponly=True, samesite="lax")
    return response


@router.post("/oauth/token")
async def token(request: Request):
    _on()
    # RFC 6749 (4.1.3): a token request is form-encoded, each parameter once; read here, so nothing else is needed
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind != "application/x-www-form-urlencoded":
        raise oauth.OAuthError(400, "invalid_request", "A token request is application/x-www-form-urlencoded.")
    try:
        pairs = urllib.parse.parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True,
                                       max_num_fields=50)
    except (UnicodeDecodeError, ValueError):
        raise oauth.OAuthError(400, "invalid_request", "The token request could not be read.") from None
    form = {}
    for key, value in pairs:
        if key in form:
            raise oauth.OAuthError(400, "invalid_request", "A parameter was given more than once.")
        form[key] = value
    return JSONResponse(await run_in_threadpool(oauth.token, form, _client(request)), headers={"Pragma": "no-cache"})


@router.post("/mcp")
async def mcp(request: Request):
    _on()
    origin = request.headers.get("origin")
    if origin is not None and origin not in connector.ORIGINS:
        # the MCP spec's rule against DNS rebinding: no page has a reason to call it, but the Claude app's
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "This endpoint does "
                             "not take a request from that page's origin."}}, status_code=403)
    try:
        grant = await run_in_threadpool(oauth.authenticate, request.headers.get("authorization"), _client(request))
    except oauth.Unauthorized as e:
        return JSONResponse({"error": "invalid_token" if e.given else "invalid_request",
                             "error_description": "An access token for this ADE is needed: connect the app."},
                            status_code=401, headers={"WWW-Authenticate": connector.challenge(e.given)})
    body = await request.body()
    if len(body) > connector.BODY_MAX:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "A request is at most "
                             f"{connector.BODY_MAX // 1024} KB."}}, status_code=413)
    status, answer, headers = await run_in_threadpool(connector.handle, request.headers, body, grant,
                                                      _client(request))
    if answer is None:
        return Response(status_code=status, headers=headers)
    return JSONResponse(answer, status_code=status, headers=headers)


@router.api_route("/mcp", methods=["GET", "DELETE", "PUT", "PATCH"])
def mcp_other():
    _on()
    # no stream to open and no session to end, in either era (2026-07-28 asks for 405; earlier ones allow it)
    return Response(status_code=405, headers={"Allow": "POST"})


@router.get("/api/connector")
def setup(user=Depends(current_user)):
    if not oauth.enabled():
        return {"on": False}
    return {"on": True, "account_on": oauth.account(user["id"])["on"], "url": oauth.resource(),
            "redirects": list(oauth.deployment_redirects()), "client_hosts": list(oauth.client_hosts()),
            "hold_seconds": holds.delay(oauth.HOLD)}


@router.put("/api/connector/account")
def set_account(body: AccountIn, user=Depends(current_user)):
    _on()
    return oauth.set_account(user["id"], body.on, body.confirmation)


@router.get("/api/connector/requests/{request_id}")
def request_view(request_id: str, request: Request, user=Depends(current_user)):
    _on()
    return oauth.request_view(user, request_id, viewer=_client(request),
                              browser=request.cookies.get(oauth.browser_cookie(request_id)))


@router.post("/api/connector/requests/{request_id}/allow")
def allow(request_id: str, request: Request, body: Optional[AllowIn] = None, user=Depends(current_user)):
    _on()
    body = body or AllowIn()
    return oauth.allow(user, request_id, body.confirmation, body.propose, client=_client(request),
                       account=body.account)


@router.post("/api/connector/requests/{request_id}/refuse")
def refuse(request_id: str, user=Depends(current_user)):
    _on()
    return oauth.refuse(user, request_id)


@router.get("/api/connector/grants")
def grants(user=Depends(current_user)):
    _on()
    return oauth.grants_for(user["id"])


@router.delete("/api/connector/grants/{grant_id}")
def revoke(grant_id: str, user=Depends(current_user)):
    _on()
    return oauth.revoke(user["id"], grant_id)


@router.get("/api/connector/grants/{grant_id}/events")
def grant_events(grant_id: str, before: Optional[int] = None, user=Depends(current_user)):
    _on()
    return oauth.events_for(user["id"], grant_id, before)


@router.post("/api/connector/grants/{grant_id}/refuse-all")
def refuse_all(grant_id: str, body: Optional[WordsIn] = None, user=Depends(current_user)):
    _on()
    return proposals.refuse_all(user["id"], grant_id, body.words if body else None)


@router.get("/api/connector/proposals")
def waiting(user=Depends(current_user)):
    _on()
    return {"proposals": proposals.waiting(user["id"])}


@router.post("/api/connector/proposals/{proposal_id}/refuse")
def refuse_proposal(proposal_id: str, body: Optional[WordsIn] = None, user=Depends(current_user)):
    _on()
    return proposals.refuse(user["id"], proposal_id, body.words if body else None)


@router.post("/api/connector/proposals/{proposal_id}/accept")
def accept_stop(proposal_id: str, user=Depends(current_user)):
    _on()
    return proposals.accept_stop(user["id"], proposal_id)
