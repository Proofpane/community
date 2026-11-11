"""The ADE's HTTP surface, under /api/ade. Every route needs a signed-in account, and every connection is looked up
together with that account's id, so another account's connection is not found (404) rather than refused.

    GET    /api/ade/repos                            the account's connections; the clones an admin may connect
    POST   /api/ade/repos                            connect a repository, after reading its branch and directories
    PATCH  /api/ade/repos/{id}                       change its Verify settings (the checks CI must pass)
    DELETE /api/ade/repos/{id}                       disconnect, and forget its Verify results
    GET    /api/ade/repos/{id}/board                 the board, read now
    POST   /api/ade/repos/{id}/verify                Verify the next few commits that have no result
    GET    /api/ade/repos/{id}/verifications/{sha}   one commit's result, manifest and signature
    GET    /api/ade/proofpane                        the account's pairing with its Proofpane organisation (its
                                                     address, device id and name, when it was paired; never its
                                                     token) and its linked sessions' usage sent, waiting and dropped
    POST   /api/ade/proofpane                        {code, address?}: pair the account, the code exchanged at
                                                     Proofpane's /api/v1/devices/pair (ade/proofpane.py)
    DELETE /api/ade/proofpane                        unpair it here; nothing is revoked in Proofpane
    POST   /api/ade/repos/{id}/answer                answer a question: one signed commit
    POST   /api/ade/repos/{id}/window                open, extend or close a window: one signed commit
    POST   /api/ade/repos/{id}/stop                  write STOP.md: one signed commit
    POST   /api/ade/repos/{id}/resume                remove STOP.md: one commit
    GET    /api/ade/approvals                        the account's approval requests, open ones first
    POST   /api/ade/approvals/{id}                   approve once, or deny, an open request
    GET    /api/ade/hook-tokens                      the account's hook tokens
    POST   /api/ade/hook-tokens                      a new hook token for one session, shown once
    DELETE /api/ade/hook-tokens/{id}                 revoke one
    POST   /api/ade/hook/requests                    a hook: hold a command for approval (hook token only)
    GET    /api/ade/hook/requests/{id}               a hook: read its request; reading an approval uses it up
    GET    /api/ade/push                             the VAPID public key, and the account's devices
    POST   /api/ade/push/subscribe                   keep this browser's push subscription
    POST   /api/ade/push/unsubscribe                 forget it
    POST   /api/ade/push/test                        send a test notification to the account's devices
    GET    /api/ade/holds                            the account's approvals that wait, neither ended nor cancelled
    POST   /api/ade/holds/{id}/cancel                cancel one before it takes effect (ade/holds.py); a change to
                                                     end-to-end encryption takes {confirmation} from one of the
                                                     account's passkeys that the change does not remove, or
                                                     {recovery_proof} (ade/e2e_keys.py)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .. import config, signing
from ..deps import current_user
from . import (actions, approvals, board, channel, holds, ledger, link_usage, proofpane, push, sources, storage,
               verifier)

router = APIRouter(prefix="/api/ade")


class RepoIn(BaseModel):
    source: str = "github"
    repo: str
    branch: str
    dirs: List[str] = []
    verify: bool = False
    token: Optional[str] = None
    verify_config: Optional[dict] = None


class RepoPatch(BaseModel):
    verify_config: Optional[dict] = None


def _can_store_tokens():
    try:
        return ledger.keystore_key() is not None
    except ledger.LedgerError:
        return False


def _connection(user, repo_id):
    conn = ledger.get(user["id"], repo_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="No such connection.")
    return conn


@router.get("/repos")
def list_repos(user=Depends(current_user)):
    """Each connection says whether it may write the ADE's records (`records`), and where it does not, why and what
    would (`records_note`, storage.py): a board only, without a token, as a clone, or on a deployment that
    keeps no key-store key, and no board, with a token this deployment cannot open now."""
    repos = []
    for r in ledger.list_for(user["id"]):
        note = storage.records_note(user["id"], r)
        repos.append(dict(r, records=note is None, records_note=note))
    return {"repos": repos, "local": sorted(sources.local_repos()) if user["admin"] else [],
            "can_store_tokens": _can_store_tokens()}


@router.post("/repos")
def connect(body: RepoIn, user=Depends(current_user)):
    """Read the branch and each directory first, so a connection that cannot be read is never stored."""
    token = body.token.strip() if body.token and body.token.strip() else None
    ledger.clean_verify_config(body.verify_config)  # a setting Verify does not know is refused before any read
    if body.source == "local":
        if not user["admin"]:
            raise ledger.LedgerError("Only an admin can connect a clone on this machine.")
        path = sources.local_repos().get(body.repo)
        if path is None:
            raise ledger.LedgerError("This deployment names no such clone.")
        source = sources.LocalGit(body.repo, path)
    elif body.source == "github":
        if token and not _can_store_tokens():
            raise ledger.LedgerError("This deployment cannot store a token: connect a public repository without one.")
        source = sources.GitHub(body.repo, token=token)
    else:
        raise ledger.LedgerError("A repository is on GitHub, or a clone this deployment's admin named.")
    dirs = [sources.check_path(d.strip().strip("/")) for d in body.dirs]
    head = source.head(sources.check_branch(body.branch))
    for d in dirs:
        source.list_dir(head, d)
    return ledger.add(user, body.source, body.repo, body.branch, dirs, body.verify, token,
                      verify_config=body.verify_config)


@router.delete("/repos/{repo_id}")
def disconnect(repo_id: str, user=Depends(current_user)):
    if not ledger.remove(user["id"], repo_id):
        raise HTTPException(status_code=404, detail="No such connection.")
    return {"ok": True}


@router.patch("/repos/{repo_id}")
def update_repo(repo_id: str, body: RepoPatch, user=Depends(current_user)):
    """Change a connection's Verify settings (its expected checks); null clears them."""
    conn = ledger.set_verify_config(user["id"], repo_id, body.verify_config)
    if conn is None:
        raise HTTPException(status_code=404, detail="No such connection.")
    return conn


@router.get("/repos/{repo_id}/board")
def get_board(repo_id: str, user=Depends(current_user)):
    conn = _connection(user, repo_id)
    source = ledger.source_for(user["id"], repo_id)
    summaries = ledger.verification_summaries(repo_id) if conn["verify"] else None
    out = board.build_board(conn, source, datetime.now(timezone.utc), summaries, signing.public_key())
    out["connection"] = conn
    out["writable"] = storage.can_write_records(conn)        # where one may be made: GitHub refuses a read-only token
    return out


@router.post("/repos/{repo_id}/verify")
def verify_new(repo_id: str, user=Depends(current_user)):
    conn = _connection(user, repo_id)
    if not conn["verify"]:
        raise HTTPException(status_code=400, detail="Verify is not turned on for this connection.")
    return verifier.verify_new(repo_id, ledger.source_for(user["id"], repo_id), conn["branch"],
                               verify_config=conn["verify_config"])


@router.get("/repos/{repo_id}/verifications/{sha}")
def get_verification(repo_id: str, sha: str, user=Depends(current_user)):
    _connection(user, repo_id)
    record = ledger.verification(repo_id, sha)
    if record is None:
        raise HTTPException(status_code=404, detail="That commit has no Verify result.")
    return record


# ---- the account's pairing with Proofpane, for its linked sessions' usage (ade/proofpane.py, ade/link_usage.py) ----

class ProofpaneIn(BaseModel):
    # proofpane.py says what a code and an address are; the token Proofpane gives for them is kept sealed, never sent
    # back here
    code: str = Field(max_length=64)
    address: Optional[str] = Field(default=None, max_length=300)


def _proofpane_status(user):
    return dict(proofpane.status_of(user["id"]), usage=link_usage.summary(user["id"]))


@router.get("/proofpane")
def proofpane_status(user=Depends(current_user)):
    return _proofpane_status(user)


@router.post("/proofpane")
def proofpane_pair(body: ProofpaneIn, user=Depends(current_user)):
    proofpane.pair(user["id"], body.code, body.address)
    return _proofpane_status(user)


@router.delete("/proofpane")
def proofpane_unpair(user=Depends(current_user)):
    if not proofpane.unpair(user["id"]):
        raise HTTPException(status_code=404, detail="This account is not paired with Proofpane.")
    return _proofpane_status(user)


# ---- the owner's actions, each one signed commit ----------------------------------------------------------

_QID = re.compile(r"[A-Z]{1,3}-\d{1,4}|[A-Z]")
_WINDOW_FILE = re.compile(r"GO(?:-\d+)?\.md")
MAX_MINUTES = 24 * 60


class AnswerIn(BaseModel):
    dir: str
    id: str
    text: str


class WindowIn(BaseModel):
    dir: str
    text: str
    file: Optional[str] = None      # an existing window file to extend or close; none opens a new one
    name: Optional[str] = None      # a new window's name
    minutes: int = 30               # how long a new window lasts, or how much longer an existing one does
    start_in: int = 0               # minutes from now until a new window opens
    close: bool = False             # end an existing window now (before it opens: withdraw it)


class StopIn(BaseModel):
    dir: str
    text: str


def _writable(user, repo_id, directory):
    conn = _connection(user, repo_id)
    if conn["source"] != "github" or not conn["has_token"]:
        raise ledger.LedgerError("Writing needs a repository on GitHub connected with a token that has Contents: "
                                 "read and write.")
    if directory not in conn["dirs"]:
        raise ledger.LedgerError("That is not one of this connection's channel directories.")
    return conn, ledger.source_for(user["id"], repo_id)


def _read(source, head, path):
    try:
        return source.read_file(head, path)
    except sources.NotFound:
        return None


def _write(conn, source, user, subject, words, build):
    now = datetime.now(timezone.utc)
    key = signing.private_key()
    message = actions.commit_message(subject, words, user["email"], now, signing.public_key())
    sha = source.commit_files(conn["branch"], lambda head: build(head, key, now), message)
    return {"commit": sha}


def _signed(key, kind, source, conn, path, text, user, now):
    return {path: text, actions.sig_path(path): actions.sign_file(key, kind, source.label, conn["branch"], path, text,
                                                                  user["email"], now)}


@router.post("/repos/{repo_id}/answer")
def answer(repo_id: str, body: AnswerIn, user=Depends(current_user)):
    conn, source = _writable(user, repo_id, body.dir)
    if not _QID.fullmatch(body.id or ""):
        raise ledger.LedgerError("A question id is written like W-1 or Q-003.")
    words = actions.one_line(body.text, "answer")

    def build(head, key, now):
        names = source.list_dir(head, body.dir)
        name = next((n for n in names if channel.role_of(n) == "answers"), "owner-answers.md")
        path = sources.join(body.dir, name)
        text = actions.answers_text(_read(source, head, path), body.id, words)
        return _signed(key, "answers", source, conn, path, text, user, now)

    return _write(conn, source, user, f"Answer {body.id}", f"{body.id}: {words}", build)


@router.post("/repos/{repo_id}/window")
def window(repo_id: str, body: WindowIn, user=Depends(current_user)):
    conn, source = _writable(user, repo_id, body.dir)
    words = actions.one_line(body.text, "note")
    if not 1 <= body.minutes <= MAX_MINUTES or not 0 <= body.start_in <= MAX_MINUTES:
        raise ledger.LedgerError(f"A window lasts 1 to {MAX_MINUTES} minutes and opens within a day.")
    if body.file is not None and not _WINDOW_FILE.fullmatch(body.file):
        raise ledger.LedgerError("A window file is GO.md or GO-<n>.md.")
    name = actions.one_line(body.name, "window's name", actions.MAX_NAME) if body.file is None else None

    def build(head, key, now):
        names = source.list_dir(head, body.dir)
        if body.file is None:
            taken = set(names)
            file = next(f for f in ["GO.md"] + [f"GO-{n}.md" for n in range(2, 1000)] if f not in taken)
            start = now + timedelta(minutes=body.start_in)
            text = actions.window_text(name, start, start + timedelta(minutes=body.minutes), user["email"], now, words)
        else:
            path = sources.join(body.dir, body.file)
            current = _read(source, head, path) if body.file in names else None
            if current is None:
                raise ledger.LedgerError(f"{body.file} does not exist in {body.dir}.")
            w = channel.read_window(body.file, current)
            if w["problems"]:
                raise ledger.LedgerError(f"{body.file} is not a valid window, so it is not changed.")
            start, end = channel.parse_time(w["start"]), channel.parse_time(w["end"])
            if now >= end:
                raise ledger.LedgerError(f"The window in {body.file} has ended: open a new one.")
            if body.close:
                # times are to the minute: the end becomes this minute's start, or the window's own start when it
                # has not opened yet or opened this minute, which leaves it empty. Either way nothing more may be sent.
                new_end, what = max(start, now.replace(second=0, microsecond=0)), "Closed"
            else:
                new_end, what = end + timedelta(minutes=body.minutes), "Extended"
            text = actions.window_changed(current, new_end, user["email"], now, words, what)
            file = body.file
        return _signed(key, "window", source, conn, sources.join(body.dir, file), text, user, now)

    what = "Close" if body.close else ("Extend" if body.file else "Open")
    return _write(conn, source, user, f"{what} a window in {body.dir}", words, build)


@router.post("/repos/{repo_id}/stop")
def stop(repo_id: str, body: StopIn, user=Depends(current_user)):
    conn, source = _writable(user, repo_id, body.dir)
    words = actions.one_line(body.text, "reason")

    def build(head, key, now):
        path = sources.join(body.dir, "STOP.md")
        if "STOP.md" in source.list_dir(head, body.dir):
            raise ledger.LedgerError(f"{body.dir} already has a STOP.md.")
        return _signed(key, "stop", source, conn, path, actions.stop_text(user["email"], now, words), user, now)

    return _write(conn, source, user, f"STOP the sessions in {body.dir}", words, build)


@router.post("/repos/{repo_id}/resume")
def resume(repo_id: str, body: StopIn, user=Depends(current_user)):
    conn, source = _writable(user, repo_id, body.dir)
    words = actions.one_line(body.text, "note")

    def build(head, key, now):
        names = source.list_dir(head, body.dir)
        if "STOP.md" not in names:
            raise ledger.LedgerError(f"{body.dir} has no STOP.md.")
        path = sources.join(body.dir, "STOP.md")
        out = {path: None}
        if "STOP.md.sig.json" in names:
            out[actions.sig_path(path)] = None
        return out

    return _write(conn, source, user, f"Remove STOP in {body.dir}", words, build)


# ---- the approval queue ---------------------------------------------------------------------------------

class DecisionIn(BaseModel):
    approve: bool
    text: Optional[str] = None
    proposal: Optional[str] = None          # a connected app's proposal on this request, answered with it
    confirmation: Optional[str] = None      # from /api/account/confirm, made for this request: approving takes it


class TokenIn(BaseModel):
    name: str


class HookRequestIn(BaseModel):
    command: str
    tool: str = "Bash"
    session: Optional[str] = None
    cwd: Optional[str] = None
    until: Optional[str] = None     # the end of the window the command was sent in, as a window line writes it


def hook_caller(authorization: Optional[str] = Header(default=None)):
    """A hook's identity from its hook token. An account's own token is not a hook token, and a hook token is not an
    account's: each is refused on the other's routes."""
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    identity = approvals.hook_identity(token) if token else None
    if identity is None:
        raise HTTPException(status_code=401, detail="A live hook token is needed.")
    return identity


@router.get("/approvals")
def list_approvals(user=Depends(current_user)):
    return {"requests": approvals.list_for_owner(user["id"])}


@router.post("/approvals/{request_id}")
def decide(request_id: str, body: DecisionIn, user=Depends(current_user)):
    view = approvals.decide(user["id"], request_id, body.approve, body.text, proposal=body.proposal,
                            confirmation=body.confirmation)
    if view is None:
        raise HTTPException(status_code=404, detail="No such request.")
    return view


@router.get("/hook-tokens")
def list_hook_tokens(user=Depends(current_user)):
    return {"tokens": approvals.list_tokens(user["id"])}


@router.post("/hook-tokens")
def create_hook_token(body: TokenIn, user=Depends(current_user)):
    token_id, token = approvals.create_token(user["id"], body.name)
    return {"id": token_id, "name": body.name.strip(), "token": token, "url": config.public_url()}


@router.delete("/hook-tokens/{token_id}")
def revoke_hook_token(token_id: str, user=Depends(current_user)):
    if not approvals.revoke_token(user["id"], token_id):
        raise HTTPException(status_code=404, detail="No such live hook token.")
    return {"ok": True}


@router.post("/hook/requests")
def hook_request(body: HookRequestIn, identity=Depends(hook_caller)):
    until = None
    if body.until is not None:
        end = channel.parse_time(body.until)
        if end is None:
            raise approvals.ApprovalError("`until` is a UTC time written like 2026-09-27T05:05Z.")
        until = int(end.timestamp())
    return approvals.request(identity, body.command, body.tool, body.session, body.cwd, until)


@router.get("/hook/requests/{request_id}")
def hook_read(request_id: str, identity=Depends(hook_caller)):
    view = approvals.read_for_hook(identity, request_id)
    if view is None:
        raise HTTPException(status_code=404, detail="No such request for this hook.")
    return view


# ---- notifications when no page is open ------------------------------------------------------------------

class PushIn(BaseModel):
    endpoint: str
    keys: dict = {}


class EndpointIn(BaseModel):
    endpoint: str


@router.get("/push")
def push_info(user=Depends(current_user)):
    return {"key": push.vapid_public_b64(), "devices": push.list_for(user["id"])}


@router.post("/push/subscribe")
def push_subscribe(body: PushIn, user=Depends(current_user)):
    push.subscribe(user["id"], body.endpoint, str(body.keys.get("p256dh", "")), str(body.keys.get("auth", "")))
    return {"ok": True, "devices": push.list_for(user["id"])}


@router.post("/push/unsubscribe")
def push_unsubscribe(body: EndpointIn, user=Depends(current_user)):
    return {"ok": push.unsubscribe(user["id"], body.endpoint)}


@router.post("/push/test")
def push_test(user=Depends(current_user)):
    devices = push.subscriptions(user["id"])
    sent = sum(1 for d in devices if push.send(d, push.words("test", key="test")) in (200, 201, 202))
    return {"sent": sent, "devices": len(devices)}


# ---- approvals that wait (holds.py): the owner's alone, with the page's sign-in ----------------------------------

@router.get("/holds")
def list_holds(user=Depends(current_user)):
    return {"holds": holds.running(user["id"])}


class CancelIn(BaseModel):
    # what a kind with a guard takes (holds.register): for a change to end-to-end encryption, a confirmation made with
    # one of the account's passkeys that the change does not remove, or the recovery code's proof (ade/e2e_keys.py)
    confirmation: Optional[str] = None
    recovery_proof: Optional[dict] = None


@router.post("/holds/{hold_id}/cancel")
def cancel_hold(hold_id: str, body: Optional[CancelIn] = None, user=Depends(current_user)):
    given = {"confirmation": body.confirmation, "recovery_proof": body.recovery_proof} if body else {}
    return holds.cancel(user["id"], hold_id, given=given)
