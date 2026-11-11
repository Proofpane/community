"""Under the fly provider, a hosted workspace's claude terminals and chats hold
their gated commands for this deployment's approval queue, through open_edition/ade_hook.py, with a hook token the app
makes for the workspace and keeps from anyone's sight, and the app's own address as the ADE; an agent's run, a replay
and a task's attempt are not held.

The app's part: what a machine's configuration gives its sessions' hook, the token's life beside the workspace's row,
and the hook itself, as the machine runs it, against this deployment's queue. The agent's part: the settings a hosted
terminal and chat start with, an agent's run's and the controller's chat's without the hook, a start that cannot be
given it refused; and end to end, a hosted chat's gated command through the real hook to the app served on 127.0.0.1.
"""

import asyncio
import glob
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import ade_hook
from conftest import ADMIN, ROOT, signed_up
from proofpane_open import config, store
from proofpane_open.ade import approvals
from proofpane_open.ade import fly_provider as fly
from proofpane_open.ade import workspaces
from proofpane_open.ade.workspaces import WorkspaceAccess
from proofpane_workspace import chat as chats
from proofpane_workspace import gate
from proofpane_workspace import mcp as mcps
from proofpane_workspace import sessionmap
from proofpane_workspace import settings
from proofpane_workspace.workspace import Workspace
from test_ade_approvals import Clock, approve, bash, through
from test_controller_workspace import _Term, on as ctl_on, plan as ctl_plan, start as ctl_start
from test_controller_workspace import ws  # noqa: F401  (a fixture: the controller's workspace)
from test_workspace_chat import STAND_IN as CHAT_STAND_IN, _chat, _until
from test_workspace_fly import IMAGE, _files, _machine, fake  # noqa: F401  (fake: a fixture)
from test_workspace_map_settings import _first_start, _given, _start, stand_in, wait_for
from test_workspace_providers import KEY as MODEL_KEY
from workspace_fixtures import Agent, make_repo

TOKEN_FILE = "ade-hook-token"
URL_FILE = "ade-url"


def _owner(client, choice="hosted"):
    """A real account, signed in, and its user as the providers take it: an admin, at conftest's ADMIN address. Under
    the fake Fly (test_workspace_fly's `fake`), which makes that address's account itself for the provider's meter, that
    account, signed in with a token of its own (auth.issue_token); else one signed up through the API. Its storage is
    chosen as a person chooses it (PUT /api/ade/storage), hosted unless `choice` names another or is None (no choice
    made): the provider starts nothing for an account that has not chosen a hosted workspace."""
    from proofpane_open import auth
    with store.connect() as db:
        made = db.execute("SELECT id FROM users WHERE email = ?", (ADMIN,)).fetchone()
    headers = {"Authorization": "Bearer " + auth.issue_token(made["id"])} if made else signed_up(client, ADMIN)
    uid = client.get("/api/auth/me", headers=headers).json()["id"]
    if choice is not None:
        r = client.put("/api/ade/storage", headers=headers, json={"choice": choice})
        assert r.status_code == 200, r.text
    return headers, {"id": uid, "admin": True}


def _member(client, monkeypatch):
    """A member signed up through the API, with a password (conftest's `signed_up`), keeping its work in a hosted
    workspace by its own choice, on a deployment that offers one to everyone, as a removal's own tests sign one up:
    for what takes the account's password (a confirmation), which the fake Fly's admin account, made with none, cannot
    give. Returns (headers, user)."""
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_FOR", "everyone")
    headers = signed_up(client, "member@example.com")
    assert client.put("/api/ade/storage", headers=headers, json={"choice": "hosted"}).status_code == 200
    return headers, {"id": client.get("/api/auth/me", headers=headers).json()["id"], "admin": False}


def _row(user):
    with store.connect() as conn:
        r = conn.execute("SELECT * FROM ade_workspaces WHERE user_id = ?", (user["id"],)).fetchone()
    return dict(r) if r else None


def _live(user):
    return [t for t in approvals.list_tokens(user["id"]) if t["revoked_at"] is None]


def _stored_bytes():
    """The database's bytes, its write-ahead log's among them: what the app keeps on its volume."""
    data = b""
    for path in glob.glob(config.db_path() + "*"):
        with open(path, "rb") as fh:
            data += fh.read()
    return data


def _hook_env(files, tmp_path):
    """The hook's environment as the workspace starts it (`env -i`, then these two): the address, and the token's file
    as start.sh puts it in the machine."""
    token_file = tmp_path / "hook-token"
    token_file.write_text(files[TOKEN_FILE])
    return {"PROOFPANE_ADE_URL": files.get(URL_FILE, ""), "PROOFPANE_ADE_TOKEN_FILE": str(token_file)}


# ---- what a machine's configuration gives its sessions' hook ---------------------------------------------------------

def test_a_machine_is_given_the_app_s_address_and_a_hook_token_of_its_own(fake, client):
    _, user = _owner(client)
    workspaces.for_user(user).ensure(user)
    files = _files(_machine(fake))
    assert TOKEN_FILE in files and URL_FILE in files
    assert files[URL_FILE] == config.public_url() == "https://open.example.test"
    token = files[TOKEN_FILE]
    found = approvals.hook_identity(token)
    assert found is not None and found["user_id"] == user["id"] and found["name"] == fly.HOOK_TOKEN_NAME
    assert _row(user)["hook_token_id"] == found["id"]
    assert [t["id"] for t in _live(user)] == [found["id"]]          # one of the account's tokens, listed as any


def test_the_token_is_kept_nowhere_but_the_machine_s_boot_file(fake, client):
    _, user = _owner(client)
    workspaces.for_user(user).ensure(user)
    m = _machine(fake)
    files = _files(m)
    assert TOKEN_FILE in files
    token = files[TOKEN_FILE]
    assert token.encode() not in _stored_bytes()                     # only its hash and its id are kept here
    rest = {k: v for k, v in m["config"].items() if k != "files"}
    assert token not in json.dumps(rest) and token not in json.dumps(m.get("metadata") or {})
    assert [n for n, v in files.items() if v == token] == [TOKEN_FILE]


def test_the_next_configuration_takes_a_new_token_and_revokes_the_one_before(fake, client, monkeypatch):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    p.ensure(user)
    first = _files(_machine(fake)).get(TOKEN_FILE)
    assert first is not None
    p.stop(user)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IMAGE", IMAGE.replace(":v1", ":v2"))
    p.ensure(user)                                                    # stopped: a new image, a new configuration
    second = _files(_machine(fake)).get(TOKEN_FILE)
    assert second is not None and second != first
    assert approvals.hook_identity(first) is None and approvals.hook_identity(second) is not None
    assert [t["name"] for t in _live(user)] == [fly.HOOK_TOKEN_NAME]


def test_a_started_again_machine_keeps_its_token_while_it_is_live(fake, client):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    p.ensure(user)
    first = _files(_machine(fake)).get(TOKEN_FILE)
    p.stop(user)
    p.ensure(user)
    assert first is not None and _files(_machine(fake)).get(TOKEN_FILE) == first
    assert approvals.hook_identity(first) is not None


def test_a_token_the_owner_revoked_is_replaced_at_the_next_start_from_stopped_never_under_running_sessions(fake,
                                                                                                         client):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    p.ensure(user)
    first = _files(_machine(fake)).get(TOKEN_FILE)
    assert first is not None
    assert approvals.revoke_token(user["id"], _row(user)["hook_token_id"])
    p.ensure(user)                                                    # running: its configuration left as it was
    assert _files(_machine(fake))[TOKEN_FILE] == first and _machine(fake)["state"] == "started"
    p.stop(user)
    p.ensure(user)                                                    # stopped: it takes a live one
    again = _files(_machine(fake))[TOKEN_FILE]
    assert again != first and approvals.hook_identity(again) is not None


def test_a_configuration_fly_refuses_leaves_no_new_token_live(fake, client, monkeypatch):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    p.ensure(user)
    first = _files(_machine(fake)).get(TOKEN_FILE)
    assert first is not None
    before = _row(user)["hook_token_id"]
    p.stop(user)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IMAGE", IMAGE.replace(":v1", ":v2"))
    real = fly.Machines.call

    def refused(self, method, path, body=None, query=None, timeout=30):
        if method == "POST" and path.startswith("/machines/") and path.count("/") == 2:
            raise fly.FlyError(422, "the update was refused")
        return real(self, method, path, body, query, timeout)

    monkeypatch.setattr(fly.Machines, "call", refused)
    with pytest.raises(WorkspaceAccess):
        p.ensure(user)
    assert [t["id"] for t in _live(user)] == [before] and _row(user)["hook_token_id"] == before
    assert approvals.hook_identity(first) is not None


def _update_answer_lost(monkeypatch, took, read_back=True):
    """Fly's update of the machine's configuration answered with no answer (Machines.call's 502), after Fly `took` it or
    before; and with `read_back` false, the machine's read after it failing too."""
    real = fly.Machines.call
    lost = []

    def call(self, method, path, body=None, query=None, timeout=30):
        if method == "POST" and path.startswith("/machines/") and path.count("/") == 2 and not lost:
            if took:
                real(self, method, path, body, query, timeout)
            lost.append(path)
            raise fly.FlyError(502, "Fly's machine service is not answering.")
        if lost and not read_back and method == "GET" and path.startswith("/machines/"):
            raise fly.FlyError(502, "Fly's machine service is not answering.")
        return real(self, method, path, body, query, timeout)

    monkeypatch.setattr(fly.Machines, "call", call)
    return lost, real


def _stopped_for_a_new_image(fake, p, user, monkeypatch):
    """A machine made and stopped, with a new image to take at its next start: its token, and the row's id of it."""
    p.ensure(user)
    first, before = _files(_machine(fake)).get(TOKEN_FILE), _row(user)["hook_token_id"]
    assert first is not None and before is not None
    p.stop(user)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IMAGE", IMAGE.replace(":v1", ":v2"))
    return first, before


def test_an_update_fly_took_but_whose_answer_was_lost_keeps_the_new_token(fake, client, monkeypatch):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    first, before = _stopped_for_a_new_image(fake, p, user, monkeypatch)
    lost, _ = _update_answer_lost(monkeypatch, took=True)
    with pytest.raises(WorkspaceAccess):
        p.ensure(user)
    assert lost
    given = _files(_machine(fake))[TOKEN_FILE]                       # the machine has the new one
    assert given != first and approvals.hook_identity(given) is not None
    assert approvals.hook_identity(first) is None and _row(user)["hook_token_id"] not in (None, before)
    assert [t["id"] for t in _live(user)] == [_row(user)["hook_token_id"]]


def test_an_update_whose_answer_was_lost_before_fly_took_it_keeps_the_one_before(fake, client, monkeypatch):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    first, before = _stopped_for_a_new_image(fake, p, user, monkeypatch)
    _update_answer_lost(monkeypatch, took=False)
    with pytest.raises(WorkspaceAccess):
        p.ensure(user)
    assert _files(_machine(fake))[TOKEN_FILE] == first and approvals.hook_identity(first) is not None
    assert [t["id"] for t in _live(user)] == [before] and _row(user)["hook_token_id"] == before


def test_an_update_whose_outcome_cannot_be_read_leaves_no_token_live_and_the_next_start_writes_anew(fake, client,
                                                                                                  monkeypatch):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    first, before = _stopped_for_a_new_image(fake, p, user, monkeypatch)
    _, real = _update_answer_lost(monkeypatch, took=True, read_back=False)
    with pytest.raises(WorkspaceAccess):
        p.ensure(user)
    # whichever token the machine has, none is live, and the row names none: its gated commands are refused
    assert _live(user) == [] and _row(user)["hook_token_id"] is None
    assert approvals.hook_identity(_files(_machine(fake))[TOKEN_FILE]) is None
    monkeypatch.setattr(fly.Machines, "call", real)                 # Fly answers again
    _machine(fake)["state"] = "stopped"
    p.ensure(user)                                                   # started from stopped: a configuration anew
    again = _files(_machine(fake))[TOKEN_FILE]
    assert approvals.hook_identity(again) is not None and _row(user)["hook_token_id"] is not None


def test_a_row_that_cannot_name_the_new_token_leaves_neither_live(fake, client, monkeypatch):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    first, before = _stopped_for_a_new_image(fake, p, user, monkeypatch)
    real = fly._save

    def save(user_id, **fields):
        if fields.get("hook_token_id"):
            raise RuntimeError("the store is not answering")
        return real(user_id, **fields)

    monkeypatch.setattr(fly, "_save", save)
    with pytest.raises(RuntimeError):
        p.ensure(user)
    assert _live(user) == [] and approvals.hook_identity(_files(_machine(fake))[TOKEN_FILE]) is None


def test_a_machine_made_again_revokes_the_token_of_the_one_gone(fake, client, monkeypatch):
    _, user = _owner(client)
    p = workspaces.for_user(user)
    p.ensure(user)
    first, before = _files(_machine(fake))[TOKEN_FILE], _row(user)["hook_token_id"]
    fake.destroy(_machine(fake)["id"])                              # gone: a new machine is made
    real = fly.Machines.call

    def refused(self, method, path, body=None, query=None, timeout=30):
        if method == "POST" and path == "/machines":
            raise fly.FlyError(422, "the machine was refused")
        return real(self, method, path, body, query, timeout)

    monkeypatch.setattr(fly.Machines, "call", refused)
    with pytest.raises(WorkspaceAccess):
        p.ensure(user)                                               # the new machine refused: none holds a token
    assert approvals.hook_identity(first) is None and _live(user) == [] and _row(user)["hook_token_id"] is None
    monkeypatch.setattr(fly.Machines, "call", real)
    p.ensure(user)
    again = _files(_machine(fake))[TOKEN_FILE]
    assert again != first and approvals.hook_identity(again) is not None
    assert [t["id"] for t in _live(user)] == [_row(user)["hook_token_id"]] != [before]


def test_an_address_the_hook_would_refuse_is_not_given(fake, client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "https://open.example.test/app")
    _, user = _owner(client)
    workspaces.for_user(user).ensure(user)
    files = _files(_machine(fake))
    assert URL_FILE not in files and TOKEN_FILE in files


@pytest.mark.parametrize("url", ["https://open.example.test", "https://open.example.test:8443",
                                 "https://open.example.test/", "https://open.example.test/app",
                                 "http://open.example.test", "https://user@open.example.test", "https://"])
def test_the_app_gives_the_address_the_hook_takes(url):
    # fly_provider keeps its own copy of the hook's rule (ade_hook.URL, its https part), never importing the hook
    taken = bool(ade_hook.URL.fullmatch(url.rstrip("/"))) and url.startswith("https://")
    assert bool(fly._ADE_ADDRESS.fullmatch(url.rstrip("/"))) is taken


def test_the_owner_s_own_twenty_tokens_do_not_keep_the_workspace_from_its_own(fake, client):
    _, user = _owner(client)
    for i in range(approvals.MAX_TOKENS):
        approvals.create_token(user["id"], f"session {i}")
    workspaces.for_user(user).ensure(user)
    token = _files(_machine(fake)).get(TOKEN_FILE)
    assert token is not None and approvals.hook_identity(token) is not None
    with pytest.raises(approvals.ApprovalError):
        approvals.create_token(user["id"], "one more")                # the owner's own cap holds as it did


def test_the_workspace_s_token_does_not_count_among_the_owner_s_own_twenty(fake, client):
    _, user = _owner(client)
    workspaces.for_user(user).ensure(user)                          # the workspace's token, which its row names
    made = 0
    for i in range(approvals.MAX_TOKENS + 1):
        try:
            approvals.create_token(user["id"], f"session {i}")
        except approvals.ApprovalError:
            break
        made += 1
    assert made == approvals.MAX_TOKENS                              # the owner's twenty, and no more


def test_an_account_whose_choice_is_not_hosted_is_given_no_token(fake, client):
    _, user = _owner(client, choice="machine")
    with pytest.raises(WorkspaceAccess):                             # the provider's refusal, before anything is made
        workspaces.for_user(user).ensure(user)
    assert _live(user) == [] and approvals.list_tokens(user["id"]) == [] and _row(user) is None


# ---- the token's life beside the workspace's row -----------------------------------------------------------------

def test_the_token_is_revoked_with_the_workspace_s_row_and_the_hook_is_refused(fake, client, tmp_path):
    headers, user = _owner(client)
    workspaces.for_user(user).ensure(user)
    files = _files(_machine(fake))
    assert TOKEN_FILE in files
    env = _hook_env(files, tmp_path)
    env["PROOFPANE_ADE_WAIT"] = "0"
    call, clock = through(client), Clock()
    first = ade_hook.decide(bash("git push origin main"), env, call, clock.sleep, clock)
    assert first["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "no answer yet" in first["hookSpecificOutput"]["permissionDecisionReason"]     # asked: the token is live
    with store.connect() as conn:                                     # the workspace removed (the provider's destroy)
        conn.execute("DELETE FROM ade_workspaces WHERE user_id = ?", (user["id"],))
    assert approvals.hook_identity(files[TOKEN_FILE]) is None
    (gone,) = [t for t in approvals.list_tokens(user["id"]) if t["name"] == fly.HOOK_TOKEN_NAME]
    assert gone["revoked_at"] is not None
    again = ade_hook.decide(bash("git push origin main"), env, call, clock.sleep, clock)
    assert again["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "refused the hook token" in again["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_token_is_revoked_when_a_delete_at_once_removes_the_workspace(fake, client, monkeypatch):
    # the provider's removal (fly_provider.remove_workspace) deletes the workspace's row, and the trigger revokes the
    # token with it: here through the person's own delete at once, after leaving the hosted choice, with the
    # confirmation made for that act, which takes the account's password: a member signed up through the API, as
    # a removal's own tests sign one up, on a deployment that offers a hosted workspace to everyone
    headers, user = _member(client, monkeypatch)
    workspaces.for_user(user).ensure(user)
    token = _files(_machine(fake))[TOKEN_FILE]
    assert approvals.hook_identity(token) is not None
    assert client.put("/api/ade/storage", headers=headers, json={"choice": "machine"}).status_code == 200
    assert approvals.hook_identity(token) is not None and _row(user) is not None      # left: stopped, kept
    made = client.post("/api/account/confirm", headers=headers,
                       json={"password": "correct horse battery", "change": "workspace-delete"})
    assert made.status_code == 200, made.text
    r = client.request("DELETE", "/api/ade/storage/workspace", headers=headers,
                       json={"confirmation": made.json()["confirmation"]})
    assert r.status_code == 200, r.text
    assert _row(user) is None and approvals.hook_identity(token) is None
    (gone,) = [t for t in approvals.list_tokens(user["id"]) if t["name"] == fly.HOOK_TOKEN_NAME]
    assert gone["revoked_at"] is not None


def test_removing_another_account_s_row_revokes_nothing_of_this_one_s(fake, client):
    headers, user = _owner(client)
    workspaces.for_user(user).ensure(user)
    token = _files(_machine(fake)).get(TOKEN_FILE)
    assert token is not None
    with store.connect() as conn:
        conn.execute("INSERT INTO ade_workspaces (user_id, app, region, created_at, updated_at, hook_token_id) "
                     "VALUES ('someone-else', 'pp', 'syd', 0, 0, ?)", (_row(user)["hook_token_id"],))
        conn.execute("DELETE FROM ade_workspaces WHERE user_id = 'someone-else'")
    assert approvals.hook_identity(token) is not None


# ---- the hook, as the machine runs it, against this deployment's queue --------------------------------------------

def test_the_machine_s_token_asks_the_queue_and_an_approval_takes_a_confirmation_and_runs_once(fake, client,
                                                                                                tmp_path, monkeypatch):
    # an account with a password, whose approval takes a confirmation (one without, as the fake Fly's admin is made,
    # approves on its sign-in, as the desktop app's own account does)
    headers, user = _member(client, monkeypatch)
    workspaces.for_user(user).ensure(user)
    files = _files(_machine(fake))
    assert TOKEN_FILE in files
    env = _hook_env(files, tmp_path)
    env["PROOFPANE_ADE_WAIT"] = "0"
    call, clock = through(client), Clock()
    assert ade_hook.decide(bash("ls -la"), env, call, clock.sleep, clock) is None          # not gated: not held
    first = ade_hook.decide(bash("git push origin work"), env, call, clock.sleep, clock)
    assert first["hookSpecificOutput"]["permissionDecision"] == "deny"
    (req,) = client.get("/api/ade/approvals", headers=headers).json()["requests"]
    assert req["command"] == "git push origin work" and req["token_name"] == fly.HOOK_TOKEN_NAME
    # the sign-in alone approves nothing
    alone = client.post(f"/api/ade/approvals/{req['id']}", headers=headers, json={"approve": True})
    assert alone.status_code == 403
    assert approve(client, headers, req["id"], text="go ahead").status_code == 200
    second = ade_hook.decide(bash("git push origin work"), env, call, clock.sleep, clock)
    assert second["hookSpecificOutput"]["permissionDecision"] == "allow"
    third = ade_hook.decide(bash("git push origin work"), env, call, clock.sleep, clock)
    assert third["hookSpecificOutput"]["permissionDecision"] == "deny"                  # used: a new request


# ---- the settings a hosted session starts with -------------------------------------------------------------------

ADE_PY = os.path.join(ROOT, "ade_hook.py")      # open_edition/ade_hook.py, beside the agent's package, as in the image
START = os.path.join(ROOT, "proofpane_workspace", "ade_hook_start")    # the hook's own program, in the agent's package
FAKE_TOKEN = "adeh_" + "x" * 43                 # well formed, as the app's (approvals.TOKEN_PREFIX and 43 characters)
URL = "https://open.example.test"
needs_sdk = pytest.mark.skipif(not chats.available(), reason="needs the Claude Agent SDK (requirements.txt)")


def _folder(tmp_path, url=URL, token=FAKE_TOKEN):
    """The folder a workspace machine's start.sh makes: the address and the token, each in a file of its own."""
    folder = tmp_path / "ade"
    folder.mkdir()
    if url is not None:
        (folder / "ade-url").write_text(url + "\n")
    if token is not None:
        (folder / "ade-hook-token").write_text(token + "\n")
    return folder


def _assert_holds(made, folder, url=URL, python=None):
    """That settings `made` hold the approval hook as the app gives it, spelled out here, not read from the code:
    on Bash, ade_hook.py started by the agent's own program (ade_hook_start) with the interpreter, the address and the
    token's file; the start check beside it, a line naming that program; the managed settings' pins in the `env`; and
    disableAllHooks false."""
    python = python or sys.executable
    assert isinstance(made, dict) and made.get("disableAllHooks") is False
    groups = [g for g in (made.get("hooks") or {}).get("PreToolUse", []) if g.get("matcher") == "Bash"]
    assert len(groups) == 1
    run, check = groups[0]["hooks"]
    assert run == {"type": "command", "command": START, "timeout": 90,
                   "args": [python, ADE_PY, url, f"{folder}/ade-hook-token"]}
    assert "args" not in check and check["command"].startswith(f"s={shlex.quote(START)}; ") and check["timeout"] == 10
    env = made.get("env") or {}
    for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
                 "GCONV_PATH", "LOCPATH", "CLAUDE_CODE_SHELL_PREFIX"):
        assert env.get(name) == "", name
    assert env.get("CLAUDE_CODE_SIMPLE") == "0"


def _no_hook(made):
    return "ade_hook.py" not in json.dumps(made) and not any(
        g.get("matcher") == "Bash" for g in ((made or {}).get("hooks") or {}).get("PreToolUse", []))


def test_the_hook_starts_by_a_program_of_its_own_with_the_token_s_file_never_the_token(tmp_path):
    held = settings.ade_hook_settings(START, sys.executable, ADE_PY, str(tmp_path), URL)
    _assert_holds(held, tmp_path)
    assert set(held) == {"env", "hooks", "disableAllHooks"} and set(held["hooks"]) == {"PreToolUse"}
    run, _ = held["hooks"]["PreToolUse"][0]["hooks"]
    assert run["command"] not in ("/usr/bin/env", "/bin/sh", sys.executable)    # no program another hook shares
    assert set(held["env"]) == set(settings.HOOK_ENV) | set(settings.LOADER_ENV)   # the managed settings' pins alone
    assert settings.ADE_HOOK_TIMEOUT >= ade_hook.DEADLINE + 10      # the hook's deadline inside its block's time
    assert settings.ADE_HOOK_FOLDER == "/run/proofpane/ade" == gate.ADE_FOLDER
    assert settings.ADE_URL.pattern == ade_hook.URL.pattern          # the agent says held only of what the hook takes
    assert os.access(START, os.X_OK)                                 # the image copies the package as it is kept


@pytest.mark.parametrize("missing", ["start", "env", "hook", "python", "arguments"])
def test_a_hook_that_cannot_start_holds_every_bash_command(tmp_path, missing):
    start, hook, python = START, ADE_PY, sys.executable
    if missing == "start":
        start = str(tmp_path / "ade_hook_start")
    elif missing == "hook":
        hook = str(tmp_path / "ade_hook.py")
    elif missing == "python":
        python = str(tmp_path / "python3")
    run, check = settings.ade_hook_settings(start, python, hook, str(tmp_path), URL)["hooks"]["PreToolUse"][0]["hooks"]
    if missing == "start":
        argv = ["/bin/sh", "-c", check["command"]]           # the line beside it, as Claude Code runs a line
    elif missing == "env":
        # the start's own check of /usr/bin/env, on a copy that looks for it where it is not
        copy = tmp_path / "ade_hook_start"
        copy.write_text(open(START).read().replace("/usr/bin/env", str(tmp_path / "no-env")))
        copy.chmod(0o755)
        argv = [str(copy), *run["args"]]
    else:
        argv = [run["command"], *(run["args"][:2] if missing == "arguments" else run["args"])]
    p = subprocess.run(argv, input="{}", capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "every Bash command is held" in p.stderr, (p.returncode, p.stderr)


def test_the_start_check_lets_the_hook_answer_when_it_can_start(tmp_path):
    _, check = settings.ade_hook_settings(START, sys.executable, ADE_PY, str(tmp_path),
                                          URL)["hooks"]["PreToolUse"][0]["hooks"]
    p = subprocess.run(["/bin/sh", "-c", check["command"]], input="{}", capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and p.stderr == ""


def test_the_start_gives_the_hook_nothing_of_the_session_s_environment_but_its_two_names(tmp_path):
    seen = tmp_path / "seen.json"
    python = tmp_path / "python3"                    # an interpreter that writes down what it was given
    python.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                      f"json.dump({{'argv': sys.argv[1:], 'env': dict(os.environ), 'stdin': sys.stdin.read()}}, "
                      f"open({str(seen)!r}, 'w'))\n")
    python.chmod(0o755)
    held = settings.ade_hook_settings(START, str(python), ADE_PY, str(tmp_path), URL)
    run, _ = held["hooks"]["PreToolUse"][0]["hooks"]
    theirs = {"PROOFPANE_ADE_URL": "https://other.example.test", "PROOFPANE_ADE_TOKEN": "adeh_theirs",
              "PROOFPANE_ADE_CA_FILE": "/tmp/theirs.pem", "PROOFPANE_ADE_PROXY": "http://theirs.example.test:8080",
              "PROOFPANE_ADE_WAIT": "0", "HTTPS_PROXY": "http://theirs.example.test:8080",
              "SSL_CERT_FILE": "/tmp/theirs.pem", "OPENSSL_CONF": "/tmp/theirs.cnf", "RES_OPTIONS": "attempts:0"}
    p = subprocess.run([run["command"], *run["args"]], input='{"tool_name": "Bash"}', env=dict(os.environ, **theirs),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    got = json.loads(seen.read_text())
    assert got["argv"] == ["-I", "-X", "utf8", ADE_PY] and got["stdin"] == '{"tool_name": "Bash"}'
    # what the interpreter adds itself (the C locale's coercion, and macOS's text encoding) aside
    env = {k: v for k, v in got["env"].items() if k not in ("LC_CTYPE", "__CF_USER_TEXT_ENCODING")}
    assert env == {"PROOFPANE_ADE_URL": URL, "PROOFPANE_ADE_TOKEN_FILE": f"{tmp_path}/ade-hook-token"}


def test_with_held_adds_the_hook_after_what_is_there_and_refuses_what_it_cannot_add_to(tmp_path):
    held = settings.ade_hook_settings(START, sys.executable, ADE_PY, str(tmp_path), URL)
    gate = {"matcher": "*", "hooks": [{"type": "command", "command": "/usr/bin/python3"}]}
    given = {"hooks": {"PreToolUse": [gate], "Stop": []}, "statusLine": {"type": "command", "command": "x"},
             "env": {"CLAUDE_CODE_SIMPLE": "1", "LD_PRELOAD": "/tmp/theirs.so", "MINE": "kept"}}
    made = settings.with_held(given, held)
    assert made["hooks"]["PreToolUse"][0] == gate and made["statusLine"] == given["statusLine"]
    assert made["env"]["MINE"] == "kept"                             # its pins over what was given, the rest kept
    _assert_holds(made, tmp_path)
    assert settings.holds(made, held) and not settings.holds(given, held)
    assert not settings.holds(dict(made, disableAllHooks=True), held)
    assert not settings.holds(dict(made, env=dict(made["env"], CLAUDE_CODE_SIMPLE="1")), held)
    assert not settings.holds({k: v for k, v in made.items() if k != "env"}, held)
    for bad in (None, [], {"hooks": []}, {"hooks": {"PreToolUse": {}}}, {"env": []}, {"env": "x"}):
        assert settings.with_held(bad, held) is None


@pytest.mark.parametrize("value", [None, '{"model": "x"}', "file"])
def test_a_chat_s_settings_take_the_hook_given_as_none_as_text_and_as_a_file(tmp_path, value):
    held = settings.ade_hook_settings(START, sys.executable, ADE_PY, str(tmp_path), URL)
    if value == "file":
        value = str(tmp_path / "settings.json")
        (tmp_path / "settings.json").write_text('{"model": "x"}')
    _assert_holds(json.loads(mcps.with_held(value, held)), tmp_path)


@pytest.mark.parametrize("value", ["/no/such/settings.json", "a list in a file", '{"hooks": []}',
                                   '{"hooks": {"PreToolUse": 1}}', '{"env": "x"}'])
def test_a_chat_s_settings_the_hook_cannot_be_added_to_give_none(tmp_path, value):
    held = settings.ade_hook_settings(START, sys.executable, ADE_PY, str(tmp_path), URL)
    if value == "a list in a file":                 # read, and not settings: not an object
        (tmp_path / "settings.json").write_text("[1]")
        value = str(tmp_path / "settings.json")
    assert mcps.with_held(value, held) is None


def test_settings_too_large_to_be_one_argument_give_none(tmp_path):
    held = settings.ade_hook_settings(START, sys.executable, ADE_PY, str(tmp_path), URL)
    big = json.dumps({"x": "y" * mcps.MERGED_MOST})
    assert mcps.with_held(big, held) is None


# a claude terminal: through a real agent and the map's stand-in claude (test_workspace_map_settings)

def _hosted_terminal(tmp_path, folder, version="2.1.274"):
    bin_dir, home = tmp_path / "bin", tmp_path / "home"
    home.mkdir()
    stand_in(bin_dir, version)
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                                                "PP_WORKSPACE_ADE_HOOK": str(folder)})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    return a


@pytest.mark.parametrize("version", ["2.1.213", "2.1.274"])
def test_a_hosted_terminal_starts_with_the_hook_holding_the_workspace_s_token_and_the_app_s_address(tmp_path,
                                                                                                    version):
    folder = _folder(tmp_path)
    a = _hosted_terminal(tmp_path, folder, version)
    try:
        sid, session = _first_start(a, tmp_path)
        argv, path = _given(session)
        assert path is not None, argv
        made = json.loads(open(path).read())
        _assert_holds(made, folder)
        assert FAKE_TOKEN not in json.dumps(made) and FAKE_TOKEN not in json.dumps(argv)    # its file's path alone
    finally:
        a.close()


def test_a_terminal_under_the_local_provider_is_given_no_hook(tmp_path):
    a, _, _ = _start(tmp_path, "2.1.274", local=True)
    try:
        sid, session = _first_start(a, tmp_path)
        _, path = _given(session)
        assert _no_hook(json.loads(open(path).read()))
    finally:
        a.close()


def test_a_hosted_terminal_whose_settings_file_cannot_be_written_is_not_started(tmp_path):
    folder = _folder(tmp_path)
    a = _hosted_terminal(tmp_path, folder)
    try:
        sid, session = _first_start(a, tmp_path)
        assert a.http.post(f"/sessions/{sid}/stop").status_code == 200
        os.unlink(session / "done")
        # a folder where the session's settings file is written first: nothing can be written there
        (session / (sessionmap.MAP_SETTINGS + ".tmp")).mkdir()
        r = a.http.post(f"/sessions/{sid}/start", json={})
        assert r.status_code != 200 and "approval hook" in r.text, r.text
        time.sleep(1)
        assert not (session / "done").exists()                       # its claude was never started
        assert len((session / "starts.jsonl").read_text().splitlines()) == 1
    finally:
        a.close()


def test_a_hosted_terminal_with_no_address_or_token_in_its_folder_still_holds_its_gated_commands(tmp_path):
    folder = _folder(tmp_path, url=None, token=None)
    a = _hosted_terminal(tmp_path, folder)
    try:
        sid, session = _first_start(a, tmp_path)
        _, path = _given(session)
        made = json.loads(open(path).read())
        _assert_holds(made, folder, url="")
        run = [g for g in made["hooks"]["PreToolUse"] if g.get("matcher") == "Bash"][0]["hooks"][0]
        p = subprocess.run([run["command"], *run["args"]], input=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "git push"}, "session_id": "s", "cwd": str(tmp_path)}),
            capture_output=True, text=True, timeout=120)
        answer = json.loads(p.stdout)["hookSpecificOutput"]
        assert answer["permissionDecision"] == "deny" and "no ADE address" in answer["permissionDecisionReason"]
    finally:
        a.close()


def test_a_hosted_terminal_started_again_keeps_the_hook(tmp_path):
    folder = _folder(tmp_path)
    a = _hosted_terminal(tmp_path, folder)
    try:
        sid, session = _first_start(a, tmp_path)
        assert a.http.post(f"/sessions/{sid}/stop").status_code == 200
        os.unlink(session / "done")
        r = a.http.post(f"/sessions/{sid}/start", json={})              # Start again: launch's own command
        assert r.status_code == 200, r.text
        wait_for(session / "done")
        assert len((session / "starts.jsonl").read_text().splitlines()) == 2
        _, path = _given(session)
        _assert_holds(json.loads(open(path).read()), folder)
    finally:
        a.close()


def test_a_hosted_terminal_on_another_provider_s_model_holds_in_the_settings_file_it_runs_with(tmp_path):
    folder = _folder(tmp_path)
    a = _hosted_terminal(tmp_path, folder)
    try:
        assert a.http.put("/model-providers/kimi", json={"key": MODEL_KEY}).status_code == 200
        r = a.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "Map it",
                                           "model_provider": "kimi"})
        assert r.status_code == 200, r.text
        session = tmp_path / "ws" / "sessions" / r.json()["id"]
        wait_for(session / "done")
        argv, path = _given(session)
        assert path == str(session / "model-settings.json")
        made = json.loads(open(path).read())
        _assert_holds(made, folder)
        assert "apiKeyHelper" in made and MODEL_KEY not in json.dumps(made)
    finally:
        a.close()


def test_a_terminal_the_owner_s_controller_starts_holds_beside_its_limit_hooks(ws, tmp_path):  # noqa: F811
    folder = _folder(tmp_path)
    ws.ade_hook = str(folder)
    got = ctl_start(ws, ctl_plan(ws, ctl_on(ws)))
    argv = _Term.seen[-1].argv
    assert ws.own_settings(got["session"], argv)
    made = json.loads(open(argv[argv.index("--settings") + 1]).read())
    _assert_holds(made, folder)
    limits = settings.controller_settings(sys.executable, os.path.join(ROOT, "proofpane_workspace"), (2, 1, 274))
    assert limits.get("hooks")
    for event, groups in limits["hooks"].items():
        assert all(g in made["hooks"][event] for g in groups), event


def _record_rewritten(session, **fields):
    """The session's record as its own programs can write it (workspace._read)."""
    path = session / "session.json"
    record = json.loads(path.read_text())
    record.update(fields)
    path.write_text(json.dumps(record))


@pytest.mark.parametrize("named", ["claude", "a link to it"])
def test_a_session_made_a_program_of_the_owner_s_own_that_is_claude_is_not_started_hosted(tmp_path, named):
    folder = _folder(tmp_path)
    a = _hosted_terminal(tmp_path, folder)
    try:
        sid, session = _first_start(a, tmp_path)
        assert a.http.post(f"/sessions/{sid}/stop").status_code == 200
        os.unlink(session / "done")
        first = "claude"
        if named == "a link to it":                     # the workspace's claude by another name
            (tmp_path / "elsewhere").mkdir()
            (tmp_path / "elsewhere" / "cc").symlink_to(tmp_path / "bin" / "claude")
            first = str(tmp_path / "elsewhere" / "cc")
        _record_rewritten(session, program="custom", custom={"argv": [first, "--continue"]})
        r = a.http.post(f"/sessions/{sid}/start", json={})
        assert r.status_code != 200 and "a program of your own that is Claude Code is not started" in r.text, r.text
        time.sleep(1)
        assert not (session / "done").exists()                       # its claude was never started
        assert len((session / "starts.jsonl").read_text().splitlines()) == 1
    finally:
        a.close()


def test_a_program_of_the_owner_s_own_that_is_not_claude_still_starts_hosted(tmp_path):
    folder = _folder(tmp_path)
    a = _hosted_terminal(tmp_path, folder)
    try:
        sid, session = _first_start(a, tmp_path)
        assert a.http.post(f"/sessions/{sid}/stop").status_code == 200
        _record_rewritten(session, program="custom", custom={"argv": ["/bin/sh", "-c", f"touch {session}/ran"]})
        r = a.http.post(f"/sessions/{sid}/start", json={})
        assert r.status_code == 200, r.text
        wait_for(session / "ran")
    finally:
        a.close()


def test_under_the_local_provider_a_program_of_the_owner_s_own_that_is_claude_is_left_as_it_is(tmp_path):
    a, _, _ = _start(tmp_path, "2.1.274", local=True)
    try:
        sid, session = _first_start(a, tmp_path)
        assert a.http.post(f"/sessions/{sid}/stop").status_code == 200
        os.unlink(session / "done")
        _record_rewritten(session, program="custom", custom={"argv": ["claude", "--continue"]})
        r = a.http.post(f"/sessions/{sid}/start", json={})
        assert r.status_code == 200, r.text
        wait_for(session / "done")
    finally:
        a.close()


@pytest.mark.parametrize("url, token, held", [
    (URL, FAKE_TOKEN, "held"), ("http://127.0.0.1:8000", FAKE_TOKEN, "held"), (None, FAKE_TOKEN, "refused"),
    ("https://open.example.test/app", FAKE_TOKEN, "refused"), ("http://open.example.test", FAKE_TOKEN, "refused"),
    (URL, None, "refused")])
def test_the_agent_says_whether_its_sessions_are_held(tmp_path, monkeypatch, url, token, held):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    w = Workspace(str(tmp_path / "ws"))
    assert w.programs()["held"] == "none"                            # given no folder: the local provider's
    w.ade_hook = str(_folder(tmp_path, url=url, token=token))
    assert w.held() == held and w.programs()["held"] == held
    monkeypatch.setattr(settings, "ADE_HOOK_START", "no-such-start")    # its start not there: refused, never held
    assert w.held() == "refused"


def test_the_page_is_told_through_the_agent_s_programs(tmp_path):
    a = _hosted_terminal(tmp_path, _folder(tmp_path))
    try:
        assert a.http.get("/programs").json()["held"] == "held"
    finally:
        a.close()


# a chat: its start in this process (workspace._chat_start), and its connect with a stand-in SDK

def _chat_for(ws, tmp_path):
    return chats.Chat("0123456789ab", str(tmp_path / "s"), str(tmp_path), {}, ws._chat_wrapper)


@pytest.fixture
def hosted_ws(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    w = Workspace(str(tmp_path / "ws"))
    w.ade_hook = str(_folder(tmp_path))                 # as PP_WORKSPACE_ADE_HOOK gives it
    w._chat_wrapper = str(tmp_path / "chat-claude")
    monkeypatch.setattr(w, "_claude", lambda: "/no/such/claude")
    monkeypatch.setattr(w, "claude_floor_refusal", lambda claude, cwd, env: None)
    return w


def test_an_owner_s_chat_in_a_hosted_workspace_is_given_the_hook(hosted_ws, tmp_path):
    c = _chat_for(hosted_ws, tmp_path)
    assert hosted_ws._chat_start(c) is None
    _assert_holds(getattr(c, "held", None), tmp_path / "ade")


def test_an_agent_s_run_and_the_controller_s_chat_are_not_given_it(hosted_ws, tmp_path, monkeypatch):
    run = _chat_for(hosted_ws, tmp_path)
    run.is_run = True                                 # a replay is a run too (agents.REPLAY_KINDS)
    assert hosted_ws._chat_start(run) is None and not getattr(run, "held", None)
    owner = _chat_for(hosted_ws, tmp_path)
    assert hosted_ws._chat_start(owner) is None and getattr(owner, "held", None)      # the same workspace's own
    monkeypatch.setattr(hosted_ws.controller, "is_chat", lambda sid: True)
    monkeypatch.setattr(hosted_ws.controller, "start_refusal", lambda sid: None)
    controller = _chat_for(hosted_ws, tmp_path)
    assert hosted_ws._chat_start(controller) is None and not getattr(controller, "held", None)


def test_a_workspace_given_no_folder_gives_its_chats_no_hook(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    w = Workspace(str(tmp_path / "ws"))                # the local provider's
    w._chat_wrapper = str(tmp_path / "chat-claude")
    monkeypatch.setattr(w, "_claude", lambda: "/no/such/claude")
    monkeypatch.setattr(w, "claude_floor_refusal", lambda claude, cwd, env: None)
    c = _chat_for(w, tmp_path)
    assert w._chat_start(c) is None and not getattr(c, "held", None)


# the held settings spelled out, so the connect's tests do not lean on the code they test
def _held_literal(folder):
    return {"env": {"CLAUDE_CODE_SIMPLE": "0", "LD_PRELOAD": ""},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": START, "timeout": 90,
         "args": [sys.executable, ADE_PY, URL, f"{folder}/ade-hook-token"]},
        {"type": "command", "command": "exit 0", "timeout": 10},
    ]}]}, "disableAllHooks": False}


def _connect(tmp_path, value, held):
    """A chat's connect with a stand-in for the SDK, which notes the settings the client is made to connect with:
    (what it noted, the ChatError's words)."""
    pytest.importorskip("claude_agent_sdk")
    seen = {}

    class Options:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Client:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            seen["settings"] = self.options.settings
            raise RuntimeError("stop here")

    fake = type(sys)("claude_agent_sdk")
    fake.ClaudeAgentOptions, fake.ClaudeSDKClient = Options, Client
    saved = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = fake
    try:
        c = chats.Chat("0123456789ab", str(tmp_path), str(tmp_path), {}, "/no/claude", settings=value,
                       before_start=lambda: None)
        c.held = held
        with pytest.raises(chats.ChatError) as e:
            asyncio.run(c._connect())
    finally:
        if saved is not None:
            sys.modules["claude_agent_sdk"] = saved
        else:
            del sys.modules["claude_agent_sdk"]
    return seen, str(e.value)


def test_a_hosted_chat_connects_with_the_hook_in_its_settings(tmp_path):
    held = _held_literal(tmp_path)
    seen, _ = _connect(tmp_path, None, held)
    assert seen.get("settings") is not None
    made = json.loads(seen["settings"])
    assert made["disableAllHooks"] is False and held["hooks"]["PreToolUse"][0] in made["hooks"]["PreToolUse"]


def test_a_hosted_chat_whose_settings_cannot_take_the_hook_is_not_started(tmp_path):
    seen, why = _connect(tmp_path, str(tmp_path / "no-such-settings.json"), _held_literal(tmp_path))
    assert "settings" not in seen                                      # the SDK never connected
    assert "approval hook" in why and "not started" in why


def test_a_chat_with_no_hook_connects_with_its_settings_as_they_were(tmp_path):
    seen, _ = _connect(tmp_path, "/some/settings.json", {})
    assert seen["settings"] == "/some/settings.json"


# ---- end to end: a hosted chat, the real hook, the app served on 127.0.0.1 -----------------------------------------

@pytest.fixture
def served(client):
    """The app on a local port: its address, which the hook takes (ade_hook.URL allows http://127.0.0.1:<port>)."""
    import uvicorn
    from proofpane_open.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True
    thread.join(10)


WORKSPACE_TOKEN = "Hosted workspace"         # the token's name in these tests (fly_provider.HOOK_TOKEN_NAME's)


def _hosted_chat_agent(tmp_path, url, token):
    folder = _folder(tmp_path, url=url, token=token)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(f"#!{sys.executable}\n" + open(CHAT_STAND_IN).read())
    claude.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (home / "stand-in-command-hooks").write_text("")            # the stand-in runs its settings' command hooks
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                                                "PP_WORKSPACE_ADE_HOOK": str(folder)})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"), origin="https://github.com/acme/demo.git")
    a.home = home
    return a


def _calls(agent, name="Bash"):
    path = agent.home / "stand-in-calls.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [r for r in rows if r["name"] == name]


def _waiting(client, headers, seen=(), timeout=40):
    """The requests waiting in the queue but those in `seen`, once there is one."""
    deadline = time.time() + timeout
    while True:
        waiting = [r for r in client.get("/api/ade/approvals", headers=headers).json()["requests"]
                   if r["state"] == "pending" and r["id"] not in seen]
        if waiting:
            return waiting
        assert time.time() < deadline, "no request reached the queue"
        time.sleep(0.2)


@needs_sdk
def test_a_hosted_chat_s_gated_command_waits_for_the_queue_and_runs_once_after_its_approval(client, served,
                                                                                            tmp_path):
    headers, user = _owner(client, choice=None)
    _, token = approvals.create_token(user["id"], WORKSPACE_TOKEN)     # one of the account's, as the app makes it
    a = _hosted_chat_agent(tmp_path, served, token)
    try:
        s = _chat(a, "run echo git push")
        (req,) = _waiting(client, headers)
        assert (req["command"], req["tool"], req["token_name"]) == ("echo git push", "Bash", WORKSPACE_TOKEN)
        # the sign-in alone approves nothing; a confirmation made for this request does
        alone = client.post(f"/api/ade/approvals/{req['id']}", headers=headers, json={"approve": True})
        assert alone.status_code == 403
        assert approve(client, headers, req["id"]).status_code == 200
        log = _until(a, s["id"], lambda log: log["state"] == "idle", timeout=90)
        assert not [e for e in log["events"] if e["kind"] == "ask"]           # the chat's own ask never came
        (ran,) = _calls(a)
        assert ran["ran"] and not ran["denied"] and ran["input"]["command"] == "echo git push"
        # the same command again: that approval was used, so a new request, refused when the owner denies it
        assert a.http.post(f"/sessions/{s['id']}/chat", json={"text": "run echo git push"}).status_code == 200
        (again,) = _waiting(client, headers, seen={req["id"]})
        denied = client.post(f"/api/ade/approvals/{again['id']}", headers=headers,
                             json={"approve": False, "text": "not now"})
        assert denied.status_code == 200
        deadline = time.time() + 90
        while len(_calls(a)) < 2:
            assert time.time() < deadline
            time.sleep(0.2)
        second = _calls(a)[1]
        assert second["denied"] and not second["ran"] and second["refused"] == "hook"
        assert [c["ran"] for c in _calls(a)] == [True, False]                 # it ran once
    finally:
        a.close()


@needs_sdk
def test_a_hosted_chat_s_ungated_command_is_not_held_and_keeps_the_chat_s_own_answer(client, served, tmp_path):
    headers, user = _owner(client, choice=None)
    _, token = approvals.create_token(user["id"], WORKSPACE_TOKEN)
    a = _hosted_chat_agent(tmp_path, served, token)
    try:
        s = _chat(a, "run echo hello")
        log = _until(a, s["id"], lambda log: log["state"] == "needs_you", timeout=60)
        (ask,) = [e for e in log["events"] if e["kind"] == "ask"]
        assert ask["summary"] == "echo hello"
        assert client.get("/api/ade/approvals", headers=headers).json()["requests"] == []     # nothing in the queue
        r = a.http.post(f"/sessions/{s['id']}/chat/answer", json={"id": ask["id"], "decision": "allow"})
        assert r.status_code == 200
        _until(a, s["id"], lambda log: log["state"] == "idle", timeout=60)
        (call,) = _calls(a)
        assert call["ran"] and len(call["command_hooks"]) == 2          # the hook and its start check ran, silent
    finally:
        a.close()


@needs_sdk
@pytest.mark.parametrize("where", ["the repository's", "the user's"])
def test_a_hosted_chat_s_hold_runs_though_a_repository_or_its_user_turns_hooks_off(client, served, tmp_path, where):
    # Claude Code "reads the value left after settings precedence applies", where a --settings value outranks the
    # user's and a project's (its hooks documentation, and settings.py): the hold's false keeps a true of theirs from
    # turning its hooks off
    headers, user = _owner(client, choice=None)
    _, token = approvals.create_token(user["id"], WORKSPACE_TOKEN)
    a = _hosted_chat_agent(tmp_path, served, token)
    try:
        off = json.dumps({"disableAllHooks": True})
        if where == "the repository's":
            repo = tmp_path / "ws" / "repos" / "demo"
            (repo / ".claude").mkdir()
            (repo / ".claude" / "settings.json").write_text(off)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m",
                            "hooks off"], cwd=repo, check=True)
        else:
            (a.home / ".claude").mkdir(exist_ok=True)
            (a.home / ".claude" / "settings.json").write_text(off)
        s = _chat(a, "run echo hello")
        log = _until(a, s["id"], lambda log: log["state"] == "needs_you", timeout=60)
        (ask,) = [e for e in log["events"] if e["kind"] == "ask"]
        r = a.http.post(f"/sessions/{s['id']}/chat/answer", json={"id": ask["id"], "decision": "allow"})
        assert r.status_code == 200
        _until(a, s["id"], lambda log: log["state"] == "idle", timeout=60)
        (run,) = [json.loads(line) for line in (a.home / "stand-in-runs.jsonl").read_text().splitlines()][-1:]
        if where == "the user's":                       # the user's settings it read were the ones written here
            assert run["effective"].get("CLAUDE_CONFIG_DIR") in (None, str(a.home / ".claude")), run["effective"]
        else:
            assert (Path(run["cwd"]) / ".claude" / "settings.json").read_text() == off
        (call,) = _calls(a)
        assert call["ran"] and len(call["command_hooks"]) == 2          # the hook and its start check ran
    finally:
        a.close()


# ---- the workspace's gate keeps the token's file from a session, on a workspace machine alone ------------------------

NAMING = [{"command": "cat /run/proofpane/ade/ade-hook-token"}, {"command": "ls /run/proofpane/ade"},
          {"command": "cat /run/proofpane/ade/*"}, {"file_path": "/run/proofpane/ade/ade-hook-token"},
          {"command": "cp /etc/proofpane/boot/ade-hook-token /tmp/t"}]


def _call(tool_input):
    return {"tool_name": "Bash" if "command" in tool_input else "Read", "tool_input": tool_input}


@pytest.mark.parametrize("tool_input", NAMING)
def test_the_gate_keeps_the_hook_token_from_a_session_on_a_workspace_machine(tmp_path, tool_input):
    assert gate.refusal(_call(tool_input), str(tmp_path), env={}, hosted=True) == gate.ADE_TOKEN


@pytest.mark.parametrize("tool_input", NAMING + [{"file_path": "settings.py", "content": 'FILE = "ade-hook-token"'}])
def test_under_the_local_provider_the_gate_refuses_nothing_more_for_it(tmp_path, tool_input):
    call = {"tool_name": "Write", "tool_input": tool_input} if "content" in tool_input else _call(tool_input)
    assert gate.refusal(call, str(tmp_path), env={}, hosted=False) is None


def test_the_gate_reads_from_the_machine_whether_it_is_a_workspace_machine(tmp_path, monkeypatch):
    call = _call({"command": "cat /run/proofpane/ade/ade-hook-token"})
    monkeypatch.setattr(gate, "ADE_FOLDER", str(tmp_path / "ade"))
    assert gate.refusal(call, str(tmp_path), env={}) is None             # no such folder: not one
    (tmp_path / "ade").mkdir()
    assert gate.refusal(call, str(tmp_path), env={}) == gate.ADE_TOKEN    # start.sh's folder there: one
    assert gate.ADE_TOKEN != gate.SIGN_IN and "approval hook" in gate.ADE_TOKEN


@pytest.mark.parametrize("command", ["ls /run/proofpane", "cat /run/proofpane/adequate.txt", "echo ade hook"])
def test_the_gate_lets_what_only_looks_like_it_go_on(tmp_path, command):
    assert gate.refusal({"tool_name": "Bash", "tool_input": {"command": command}}, str(tmp_path), env={},
                        hosted=True) is None
