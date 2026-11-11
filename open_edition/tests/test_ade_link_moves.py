"""Moving one of a linked machine's Claude Code sessions into the hosted workspace, through the app (ade/moves.py): the
owner signs a move of a session the link's History list has, not withheld; once its hold ends it is stored, and the
link takes it once, sends the session's transcript and a git bundle, and says it is done (or why it is not); the app
keeps the files on disk and hands them to the workspace, which starts the session there. The machine's client and the
workspace agent are built elsewhere: here the link is the tests' own signer, and the workspace a stand-in that answers
as the design has its agent answer. A move waits on no hold here, so each is stored at the next settle;
test_ade_link_move_holds.py has the hold."""

import json
import os
import re
import threading
import time
import types
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import signed_up
from proofpane_open import store
from proofpane_open.ade import holds, link, link_commands, moves, workspace_client, workspaces
from proofpane_core.canonical import canonicalize
from test_ade_link import HOST, OTHER, Session, b64u, paired, revoke, ticket
from test_ade_link_commands import DROP, account, device, event_row, nonce, owner, send, take  # noqa: F401  fixtures
from test_passkeys import Authenticator, add, remove

SESSION = "s1"
TRANSCRIPT = b'{"type":"user","cwd":"/Users/me/work/demo","message":{"content":"fix the sign-in"}}\n' * 50
BUNDLE = bytes(range(256)) * 5000           # not text, and past the 1 MiB the app takes of a JSON body
HEAD, CHANGES = "a" * 40, "b" * 40
REMOTE = "git@github.com:acme/demo.git"
CWD = "/Users/me/work/demo"


# ---- a stand-in for the workspace agent -----------------------------------------------------------------------------

class StandIn:
    """The workspace agent as a move needs it, and only so far: it takes a move's files (PUT
    /moves/{seq}/transcript and /bundle) and starts its session (POST /sessions with "moved"), or refuses it in its
    own words; and it answers the two reads the app makes before it starts any session (its agents' asks switch,
    Claude Code's sign-in). Every request must carry the workspace's secret. What it was sent is kept, to be read.
    `broken` holds an error status to answer in place of a route, by (method, the start of its path)."""

    SECRET = "w" * 43

    def __init__(self):
        self.files, self.started, self.seen, self.asks = {}, [], [], []
        self.refuse, self.signed_in, self.silent_puts, self.broken = None, True, False, {}
        self.repos = [{"name": "demo"}]          # its repositories, as GET /repos lists them; a clone adds one
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, status, obj):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _secret(self):
                outer.seen.append((self.command, self.path))
                if self.headers.get("Authorization") != "Bearer " + outer.SECRET:
                    self._answer(401, {"detail": "The workspace's secret is needed."})
                    return False
                status = next((s for (m, p), s in outer.broken.items() if m == self.command
                               and self.path.startswith(p)), None)
                if status is not None:                  # an error of its own, in place of the route
                    self._answer(status, {"detail": "Something went wrong."})
                    return False
                return True

            def do_GET(self):
                if not self._secret():
                    return
                if self.path == "/asks":
                    return self._answer(200, {"on": False, "asks": []})
                if self.path.startswith("/claude?"):
                    return self._answer(200, {"installed": True, "signed_in": outer.signed_in, "account": "default"})
                if self.path == "/repos":
                    return self._answer(200, {"repos": outer.repos})
                self._answer(404, {"detail": "Not Found"})

            def do_PUT(self):
                data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if outer.silent_puts:                    # it took the bytes and went away without an answer
                    self.close_connection = True
                    return
                if not self._secret():
                    return
                m = re.fullmatch(r"/moves/([0-9]+)/(transcript|bundle)", self.path)
                if not m:
                    return self._answer(404, {"detail": "Not Found"})
                outer.files[(m.group(1), m.group(2))] = data
                self._answer(200, {"seq": m.group(1), m.group(2): len(data)})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if not self._secret():
                    return
                if self.path == "/asks/settings":
                    outer.asks.append(body)
                    return self._answer(200, {"on": body.get("on"), "asks": []})
                if self.path == "/repos":                # a clone, named after its address
                    name = body["url"].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
                    outer.repos.append({"name": name})
                    return self._answer(200, {"name": name, "label": f"workspace/{name}"})
                if self.path != "/sessions":
                    return self._answer(404, {"detail": "Not Found"})
                outer.started.append(body)
                if outer.refuse:
                    return self._answer(400, {"detail": outer.refuse})
                self._answer(200, {"id": "0123456789ab", "program": "claude", "account": "default", "repo": "demo"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = workspaces.Endpoint(f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}", self.SECRET)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Provider:
    """The account's workspace as workspaces.for_user gives it: running or not, started by ensure."""
    name = "local"

    def __init__(self, agent):
        self.agent, self.running, self.ensured = agent, True, 0

    def check_user(self, user):
        pass

    def status(self, user):
        return {"provider": self.name, "running": self.running}

    def endpoint(self, user):
        return self.agent.endpoint if self.running else None

    def touch(self, user):
        pass

    def ensure(self, user):
        self.ensured += 1
        self.running = True
        return self.agent.endpoint


def _in_place(fn, *args):
    fn(*args)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    """A move waits on no hold here: what follows its hold is tested here, the hold itself apart."""
    monkeypatch.setenv(moves.HOLD_SETTING, "0")


@pytest.fixture
def workspace(monkeypatch):
    """The account's workspace, running, its agent the stand-in; a hand-off runs in place, so a test reads its end."""
    provider = Provider(StandIn())
    monkeypatch.setattr(workspaces, "for_user", lambda user: provider)
    monkeypatch.setattr(moves, "_spawn", _in_place)
    monkeypatch.setattr(moves, "_repos_seen", {}, raising=False)       # no workspace's list read yet, in this process
    yield provider
    provider.agent.close()


# ---- helpers --------------------------------------------------------------------------------------------------------

def lists(s, *rows):
    """The link's History list as its machine sends it: each row (session id, withheld)."""
    sessions = [{"id": sid, "withheld": True, "project": "~/work/demo"} if withheld else
                {"id": sid, "title": f"Work on {sid}", "withheld": False, "project": "~/work/demo", "branch": "main"}
                for sid, withheld in rows]
    r = s.call("PUT", "/history", {"sessions": sessions, "conversations": False})
    assert r.status_code == 200 and r.json()["left_out"] == 0, r.text


def move_payload(client, owner, to, session=SESSION, **over):
    """A move's payload as the page composes it; `over` replaces a field, or drops it (DROP)."""
    now = int(time.time())
    given = over.pop("nonce", None)
    p = {"v": 2, "deployment": HOST, "purpose": "move", "account": account(client, owner),
         "nonce": given if given is not None else nonce(client, owner, "move"), "id": uuid.uuid4().hex, "to": to,
         "session": session, "created_at": now, "expires_at": now + 600}
    for key, value in dict(over, session=session).items():
        if value is DROP:
            p.pop(key, None)
        else:
            p[key] = value
    return p


def signed_move(client, owner, device, s, session=SESSION, **over):
    """A move signed, and stored as its hold (of no seconds here) ends: (its seq, its payload)."""
    p = move_payload(client, owner, s.link_id, session, **over)
    r = send(client, owner, device, p)
    assert r.status_code == 200 and "seq" not in r.json(), r.text
    holds.settle()
    with store.connect() as db:
        seq = db.execute("SELECT seq FROM ade_link_held_moves WHERE hold_id = ?", (r.json()["held"]["id"],)).fetchone()
    assert seq["seq"] is not None, "the move was not stored as its hold ended"
    return seq["seq"], p


def move_row(seq):
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_link_moves WHERE seq = ?", (seq,)).fetchone()
    return dict(row) if row else None


def all_moves():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_link_moves ORDER BY seq")]


def owners(client, owner, **query):
    r = client.get("/api/ade/link/moves", headers=owner, params=query)
    assert r.status_code == 200, r.text
    return r.json()["moves"]


def put(s, seq, what, data, digest=None, **kw):
    """A move's file sent as the link's client sends it: its bytes, with the sha256 its signature covers in
    X-Body-SHA256 (`digest` in its place, None for the right one, False for none at all)."""
    import hashlib
    given = hashlib.sha256(kw.get("sign_body", data)).hexdigest() if digest is None else digest
    headers = {"Content-Type": "application/octet-stream", **({"X-Body-SHA256": given} if given is not False else {})}
    return s.call("PUT", f"/moves/{seq}/{what}", raw_body=data, headers=headers, **kw)


def done_body(**over):
    return dict({"session": SESSION, "remote": REMOTE, "branch": "main", "head": HEAD, "changes": CHANGES, "cwd": CWD,
                 "bundle": True}, **over)


def say_done(s, seq, **over):
    return s.call("POST", f"/moves/{seq}/done", done_body(**over))


def taken_move(client, owner, device, name="mac", session=SESSION):
    """A move of `session` (one move of a session at a time: another for each move under way), signed and taken by its
    link: (the link, the move's seq)."""
    s = paired(client, owner, name=name)
    lists(s, (session, False))
    seq, _ = signed_move(client, owner, device, s, session)
    assert take(s, seq).status_code == 200
    return s, seq


def sent_whole(client, owner, device, bundle=True, name="mac", session=SESSION):
    """A move its link took, sent and said is done: (the link, the move's seq)."""
    s, seq = taken_move(client, owner, device, name, session)
    assert put(s, seq, "transcript", TRANSCRIPT).status_code == 200
    if bundle:
        assert put(s, seq, "bundle", BUNDLE).status_code == 200
    r = say_done(s, seq, bundle=bundle, session=session)
    assert r.status_code == 200 and r.json() == {"seq": seq, "state": "uploaded"}, r.text
    return s, seq


def kept(env, seq):
    """The files the app keeps of a move: {name: bytes}."""
    folder = env / "moves" / str(seq)
    return {p.name: p.read_bytes() for p in folder.iterdir()} if folder.exists() else {}


def incoming(env):
    folder = env / "moves" / "incoming"
    return sorted(os.listdir(folder)) if folder.exists() else []


# ---- the command ----------------------------------------------------------------------------------------------------

def test_a_signed_move_is_stored_as_an_event_to_its_link_as_its_hold_ends_and_its_row_is_asked(client, owner, device,
                                                                                               workspace):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    lists(mac, (SESSION, False))
    seq, p = signed_move(client, owner, device, mac)
    row = event_row(seq)
    assert row["payload"].encode("utf-8") == canonicalize(p)
    assert json.loads(row["signature"])["id"] == b64u(device.credential_id)
    assert (row["kind"], row["link_id"], row["to_id"], row["to_name"], row["needs_turn"], row["client_id"],
            row["text"], row["expires_at"]) == ("move", "owner", mac.link_id, "mac", 1, p["id"],
                                                "move session s1 into the workspace", p["expires_at"])
    feed = mac.call("GET", "/feed", query={"turn": 1}).json()["events"]
    assert [(e["seq"], e["kind"], e["state"]) for e in feed] == [(seq, "move", "queued")]
    assert cloud.call("GET", "/feed").json()["events"] == []
    got = mac.call("GET", f"/events/{seq}").json()
    assert got["payload"] == row["payload"] and got["signature"]["id"] == b64u(device.credential_id)
    assert cloud.call("GET", f"/events/{seq}").status_code == 404
    m = move_row(seq)
    assert (m["state"], m["user_id"], m["link_id"], m["session_id"], m["title"], m["expires_at"]) == \
        ("asked", account(client, owner), mac.link_id, SESSION, "Work on s1", p["expires_at"])
    assert [(v["seq"], v["state"], v["session"]) for v in owners(client, owner)] == [(seq, "asked", SESSION)]


@pytest.mark.parametrize("over, words", [
    ({"text": "go"}, "not expected text"),
    ({"session": DROP}, "missing session"),
    ({"session": "-s1"}, "Claude Code session's id"),
    ({"session": "s" * 101}, "Claude Code session's id"),
    ({"session": "s1/../s2"}, "Claude Code session's id"),
    ({"session": "s1\n"}, "Claude Code session's id"),
    ({"session": 7}, "Claude Code session's id"),
])
def test_a_move_s_payload_has_exactly_its_fields_and_a_session_s_id(client, owner, device, workspace, over, words):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, **over))
    assert r.status_code == 422 and words in r.json()["detail"], r.text
    assert all_moves() == []


@pytest.mark.parametrize("life, refused", [(59, "60 to 3600 seconds"), (60, "needs 300 seconds more"),
                                            (299, "needs 300 seconds more"), (300, None), (3600, None),
                                            (3601, "60 to 3600 seconds")])
def test_a_move_lives_one_minute_to_one_hour_and_past_its_hold(client, owner, device, workspace, monkeypatch, life,
                                                               refused):
    """Its hold here is of no seconds, so the 300 seconds it must live past its hold are all it needs."""
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    t0 = int(time.time())
    monkeypatch.setattr(link_commands, "_now", lambda now: int(now if now is not None else t0))
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, created_at=t0, expires_at=t0 + life))
    assert (r.status_code == 200) == (refused is None), r.text
    if refused:
        assert r.status_code == 422 and refused in r.json()["detail"]


def _held_anything():
    # a move's holds: pairing the Mac placed one of its own (link.pair waits), held for no time here
    with store.connect() as db:
        return (db.execute("SELECT COUNT(*) FROM ade_link_held_moves").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM ade_holds WHERE kind = ?", (moves.HOLD,)).fetchone()[0])


def test_a_move_whose_repository_the_workspace_lacks_is_refused_before_its_hold_and_names_it(client, owner, device,
                                                                                              workspace, monkeypatch):
    """A move is held to the page's read of the workspace's list: without that, "The repository is not in the
    workspace" came only after the hold and the Mac's upload, and named no repository."""
    monkeypatch.setenv(moves.HOLD_SETTING, "180")             # a hold of its own: nothing may wait on it
    mac = paired(client, owner)
    lists(mac, (SESSION, False))                              # its folder ~/work/demo
    workspace.agent.repos = [{"name": "site"}, {"name": "demo", "missing": True}]     # demo's folder is gone
    assert client.get("/api/ade/workspace/repos", headers=owner).status_code == 200
    r = send(client, owner, device, move_payload(client, owner, mac.link_id))
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == ("This session's repository, demo (~/work/demo), is not in your workspace: add it "
                                  "there first (Add a repository), then move the session.")
    assert _held_anything() == (0, 0)                          # no hold, no notice, nothing for the Mac to upload
    # cloned through the app, and the list read again: the move is held
    assert client.post("/api/ade/workspace/repos", headers=owner,
                       json={"url": "https://github.com/acme/Demo.git"}).status_code == 200
    assert client.get("/api/ade/workspace/repos", headers=owner).status_code == 200
    r = send(client, owner, device, move_payload(client, owner, mac.link_id))
    assert r.status_code == 200 and "held" in r.json(), r.text
    assert _held_anything() == (1, 1)


def test_a_move_is_held_to_no_list_of_the_workspace_s_the_app_does_not_know_now(client, owner, device, workspace):
    """Not read since the app started, or changed through it since it was read: the workspace finds the repository as
    the move starts, and says so then, as before."""
    mac = paired(client, owner)
    lists(mac, ("s1", False), ("s2", False), ("s3", False))
    workspace.agent.repos = [{"name": "site"}]
    assert send(client, owner, device, move_payload(client, owner, mac.link_id, "s1")).status_code == 200
    assert client.get("/api/ade/workspace/repos", headers=owner).status_code == 200
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s2"))
    assert r.status_code == 409 and "demo (~/work/demo)" in r.json()["detail"], r.text
    assert client.post("/api/ade/workspace/repos", headers=owner,
                       json={"url": "https://github.com/acme/other.git"}).status_code == 200
    assert send(client, owner, device, move_payload(client, owner, mac.link_id, "s2")).status_code == 200


def test_a_session_s_repository_is_named_as_the_page_puts_it_with_the_workspace_s():
    assert moves.repository_of("~/work/demo", None) == ("demo", "~/work/demo")
    assert moves.repository_of("~/work/demo/api", ["~/work/demo/", "~/work/lib"]) == ("demo", "~/work/demo")
    assert moves.repository_of("~", None) is None and moves.repository_of(None, []) is None


def test_a_move_is_refused_for_a_session_its_link_does_not_list_unwithheld(client, owner, device, workspace):
    mac = paired(client, owner)
    other = paired(client, owner, name="mini")
    lists(mac, (SESSION, False), ("kept", True))
    lists(other, ("elsewhere", False))
    refused = {}
    for session, words in (("kept", "has this one withheld"), ("nowhere", "does not have it"),
                           ("elsewhere", "does not have it")):
        refused[session] = move_payload(client, owner, mac.link_id, session)
        r = send(client, owner, device, refused[session])
        assert r.status_code == 409 and words in r.json()["detail"], (session, r.text)
    assert all_moves() == [] and event_rows_of_kind("move") == []
    # nothing was stored, its nonce included: once mac lists the session, the very payload refused is taken
    lists(mac, (SESSION, False), ("nowhere", False))
    assert send(client, owner, device, refused["elsewhere"]).status_code == 409     # still another link's only
    assert send(client, owner, device, refused["nowhere"]).status_code == 200


def test_a_move_needs_a_nonce_given_for_a_move(client, owner, device, workspace):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, nonce=nonce(client, owner, "instruction")))
    assert r.status_code == 400 and "for this purpose" in r.json()["detail"]
    assert client.post("/api/ade/link/nonce", headers=owner, json={"purpose": "move"}).status_code == 200


def test_a_move_is_refused_where_the_account_has_no_hosted_workspace(client, owner, device, monkeypatch):
    def off(user):
        raise workspaces.WorkspaceAccess(404, "The hosted workspace is off on this deployment.")
    monkeypatch.setattr(workspaces, "for_user", off)
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    r = send(client, owner, device, move_payload(client, owner, mac.link_id))
    assert r.status_code == 409 and "The hosted workspace is off on this deployment." in r.json()["detail"], r.text
    assert all_moves() == [] and event_rows_of_kind("move") == []


def event_rows_of_kind(kind):
    with store.connect() as db:
        return db.execute("SELECT seq FROM ade_link_events WHERE kind = ?", (kind,)).fetchall()


def test_its_link_takes_a_move_once_and_no_other_link_takes_it(client, owner, device, workspace):
    mac = paired(client, owner)
    mini = paired(client, owner, name="mini")
    lists(mac, (SESSION, False))
    seq, p = signed_move(client, owner, device, mac)
    assert take(mini, seq).status_code == 404
    first = take(mac, seq)
    assert first.status_code == 200 and move_row(seq)["state"] == "taken"
    assert move_row(seq)["taken_at"] == first.json()["taken_at"]
    with store.connect() as db:
        took = db.execute("SELECT text FROM ade_link_events WHERE kind = 'take' AND ref = ?", (seq,)).fetchall()
    assert [t["text"] for t in took] == [link_commands.text_digest(canonicalize(p).decode("utf-8"))]
    r = take(mac, seq, "i2")                                             # a second copy of the link
    assert r.status_code == 409 and r.json()["why"] == "taken"


def test_a_move_not_taken_in_its_life_is_expired_and_not_taken(client, owner, device, workspace):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    seq, _ = signed_move(client, owner, device, mac)
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET expires_at = ? WHERE seq = ?", (int(time.time()) - 1, seq))
        db.execute("UPDATE ade_link_moves SET expires_at = ? WHERE seq = ?", (int(time.time()) - 1, seq))
    r = take(mac, seq)
    assert r.status_code == 409 and r.json()["why"] == "expired"
    (v,) = owners(client, owner)
    assert (v["state"], v["reason"]) == ("expired", moves.NOT_TAKEN)       # said at once, before the sweep writes it
    moves.sweep()
    assert move_row(seq)["state"] == "expired"


def test_the_copy_that_took_a_move_is_given_it_again_once_only_until_it_sent_it(client, owner, device, workspace):
    s, seq = taken_move(client, owner, device)
    again = take(s, seq)
    assert again.status_code == 200 and again.json().get("retry") is True
    s2, seq2 = taken_move(client, owner, device, name="mini", session="s2")
    assert put(s2, seq2, "transcript", TRANSCRIPT).status_code == 200
    assert say_done(s2, seq2, bundle=False, session="s2").status_code == 200
    r = take(s2, seq2)
    assert r.status_code == 409 and "sent the move" in r.json()["detail"]


def test_removing_the_passkey_that_signed_a_move_withdraws_it_until_it_is_taken(client, owner, device, workspace):
    other = Authenticator(alg=-8)
    assert add(client, owner, other, name="the other", confirm_with=device).status_code == 200
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    asked, _ = signed_move(client, owner, device, mac)
    took, _ = signed_move(client, owner, device, mac, "s2")
    assert take(mac, took).status_code == 200
    lost = next(k["id"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
                if k["name"] != "the other")
    r = remove(client, owner, lost)
    assert r.status_code == 200 and r.json()["withdrawn"] == 1, r.text
    assert (move_row(asked)["state"], move_row(asked)["reason"]) == ("failed", moves.WITHDRAWN)
    assert take(mac, asked).status_code == 409
    assert move_row(took)["state"] == "taken"


def test_the_owner_withdraws_a_move_its_link_has_not_taken(client, owner, device, workspace):
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    seq, _ = signed_move(client, owner, device, mac)
    r = client.post(f"/api/ade/link/commands/{seq}/withdraw", headers=owner)
    assert r.status_code == 200, r.text
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("failed", moves.OWNER_WITHDREW)
    assert owners(client, owner)[0]["reason"] == moves.OWNER_WITHDREW
    t = take(mac, seq)
    assert t.status_code == 409 and t.json()["why"] == "withdrawn"
    took, _ = signed_move(client, owner, device, mac, "s2")
    assert take(mac, took).status_code == 200
    r = client.post(f"/api/ade/link/commands/{took}/withdraw", headers=owner)
    assert r.status_code == 409 and r.json()["state"] == "taken"
    assert move_row(took)["state"] == "taken"
    stranger = signed_up(client, OTHER)
    assert client.post(f"/api/ade/link/commands/{took}/withdraw", headers=stranger).status_code == 404


def test_moves_count_in_their_link_s_queue_and_one_not_taken_in_time_is_told(client, owner, device, workspace):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    uid = account(client, owner)
    seq, _ = signed_move(client, owner, device, mac)
    at = event_row(seq)["received_at"]
    keys = lambda t: [k for k, _ in link.alerts(uid, t)]  # noqa: E731
    assert f"link-untaken:{seq}" not in keys(at + 299) and f"link-untaken:{seq}" in keys(at + 300)
    now = int(time.time())
    with store.connect() as db:
        for i in range(link_commands.QUEUED_PER_LINK - 1):
            db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, needs_turn, "
                       "received_at, to_id, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (uid, "owner", f"m{i}", "move", "mac", "t", 1, now - 7200, mac.link_id, now + 3600))
    r = send(client, owner, device, {**_instruction(client, owner, mac.link_id)})
    assert r.status_code == 429 and "at most 50 commands" in r.json()["detail"]


def _instruction(client, owner, to):
    now = int(time.time())
    return {"v": 2, "deployment": HOST, "purpose": "instruction", "account": account(client, owner),
            "nonce": nonce(client, owner, "instruction"), "id": uuid.uuid4().hex, "to": to, "text": "run the tests",
            "created_at": now, "expires_at": now + 3600}


# ---- the upload -----------------------------------------------------------------------------------------------------

def test_the_link_that_took_a_move_sends_its_files_and_they_are_kept_on_disk_whole(client, owner, device, workspace,
                                                                                    env):
    s, seq = taken_move(client, owner, device)
    r = put(s, seq, "transcript", TRANSCRIPT)
    assert r.status_code == 200 and r.json() == {"seq": seq, "transcript": len(TRANSCRIPT)}, r.text
    assert r.headers["cache-control"] == "no-store"
    r = put(s, seq, "bundle", BUNDLE)
    assert r.status_code == 200 and r.json() == {"seq": seq, "bundle": len(BUNDLE)}, r.text
    assert kept(env, seq) == {"transcript": TRANSCRIPT, "bundle": BUNDLE}
    row = move_row(seq)
    assert (row["transcript_size"], row["bundle_size"], row["state"]) == (len(TRANSCRIPT), len(BUNDLE), "taken")
    assert b"fix the sign-in" not in (env / "open_edition.db").read_bytes()      # on disk, never in the database
    assert put(s, seq, "transcript", b'{"again":1}\n').status_code == 200      # sent again, it is replaced whole
    assert kept(env, seq)["transcript"] == b'{"again":1}\n' and move_row(seq)["transcript_size"] == 12
    assert incoming(env) == []                                                  # no temporary file left behind
    assert (os.stat(env / "moves" / str(seq)).st_mode & 0o077) == 0           # the folder is the app's alone


def test_only_the_link_that_took_a_move_sends_anything_of_it_and_only_while_it_is_taken(client, owner, device,
                                                                                         workspace, env):
    mac = paired(client, owner)
    mini = paired(client, owner, name="mini")
    lists(mac, (SESSION, False))
    seq, _ = signed_move(client, owner, device, mac)
    r = put(mac, seq, "transcript", TRANSCRIPT)                           # not taken yet
    assert r.status_code == 409 and r.json()["state"] == "asked", r.text
    assert take(mac, seq).status_code == 200
    for r in (put(mini, seq, "transcript", TRANSCRIPT), say_done(mini, seq),
              mini.call("POST", f"/moves/{seq}/failed", {"reason": "no"}), put(mac, 999999, "transcript", b"x")):
        assert r.status_code == 404, r.text
    assert kept(env, seq) == {} and move_row(seq)["state"] == "taken"
    assert put(mac, seq, "transcript", TRANSCRIPT).status_code == 200
    assert say_done(mac, seq, bundle=False).status_code == 200
    for r in (put(mac, seq, "transcript", TRANSCRIPT), say_done(mac, seq, bundle=False),
              mac.call("POST", f"/moves/{seq}/failed", {"reason": "no"})):
        assert r.status_code == 409 and r.json()["state"] in ("uploaded", "started"), r.text
    for seq_ in (0, 2 ** 63):
        assert put(mac, seq_, "transcript", b"x").status_code == 404


def test_an_upload_not_signed_by_a_live_link_s_key_over_its_bytes_keeps_nothing(client, owner, device, workspace, env):
    s, seq = taken_move(client, owner, device)
    r = client.put(f"/api/ade/link/moves/{seq}/transcript", content=TRANSCRIPT)
    assert r.status_code == 401 and r.headers["cache-control"] == "no-store"
    r = put(s, seq, "transcript", TRANSCRIPT, sign_body=b"other bytes")
    assert r.status_code == 401 and "does not check" in r.json()["detail"]
    r = put(s, seq, "transcript", TRANSCRIPT, key=Session(client).key)          # a key no live link has
    assert r.status_code == 401 and "pair again" in r.json()["detail"]
    r = put(s, seq, "transcript", TRANSCRIPT, ts=int(time.time()) - 400)
    assert r.status_code == 401 and "5 minutes" in r.json()["detail"]
    assert kept(env, seq) == {} and incoming(env) == [] and move_row(seq)["transcript_size"] is None


def test_a_file_over_32_mib_is_refused_and_nothing_is_kept(client, owner, device, workspace, env, monkeypatch):
    assert moves.FILE_MAX == 32 * 1024 * 1024
    monkeypatch.setattr(moves, "FILE_MAX", 1000)
    s, seq = taken_move(client, owner, device)
    r = put(s, seq, "bundle", b"x" * 1001)
    assert r.status_code == 413 and "at most" in r.json()["detail"], r.text
    assert put(s, seq, "bundle", b"x" * 1000).status_code == 200
    assert kept(env, seq) == {"bundle": b"x" * 1000} and incoming(env) == []


def test_a_file_whose_body_runs_past_32_mib_whatever_its_length_says_is_refused(client, owner, device, workspace, env,
                                                                                 monkeypatch):
    monkeypatch.setattr(moves, "FILE_MAX", 1000)
    s, seq = taken_move(client, owner, device)

    def chunks():
        for _ in range(4):
            yield b"y" * 400                                                 # no length given: sent in chunks

    headers = _signed_headers(s, "PUT", f"/moves/{seq}/bundle", b"y" * 1600)
    r = client.put(f"/api/ade/link/moves/{seq}/bundle", content=chunks(), headers=headers)
    assert r.status_code == 413, r.text
    assert kept(env, seq) == {} and incoming(env) == []


def _signed_headers(s, method, path, body, ts=None, key=None):
    import hashlib
    from test_ade_link import raw
    key = key or s.key
    ts = int(time.time()) if ts is None else ts
    message = "\n".join((HOST, method, "/api/ade/link" + path, "", str(ts), hashlib.sha256(body).hexdigest())).encode()
    kid = hashlib.sha256(raw(key)).hexdigest()[:16]
    return {"Authorization": f"ADE-Link key_id={kid}, ts={ts}, sig={b64u(key.sign(message))}",
            "Content-Type": "application/octet-stream", "X-Body-SHA256": hashlib.sha256(body).hexdigest()}


class Unread:
    """A request as the upload route reads it, whose body says when it is read."""
    method = "PUT"

    def __init__(self, headers, path):
        self.headers, self.read = headers, False
        self.scope = {"raw_path": path.encode("latin-1"), "query_string": b""}

    async def stream(self):
        self.read = True
        yield b"x"


def test_an_upload_is_refused_before_its_body_is_read_for_its_key_its_time_or_its_length(client, owner, device,
                                                                                          workspace, monkeypatch, env):
    import asyncio
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    monkeypatch.setattr(moves, "FILE_MAX", 1000)
    s, seq = taken_move(client, owner, device)

    def attempt(length="1", **kw):
        path = f"/moves/{seq}/bundle"
        signed = _signed_headers(s, "PUT", path, b"other bytes", **kw)
        request = Unread({"authorization": signed["Authorization"], "content-length": length,
                          "x-body-sha256": signed["X-Body-SHA256"]}, "/api/ade/link" + path)
        with pytest.raises(link.LinkError) as e:
            asyncio.run(moves.receive(request, seq, "bundle"))
        return e.value.status, request.read

    assert attempt(ts=int(time.time()) - 400) == (401, False)
    assert attempt(key=Ed25519PrivateKey.generate()) == (401, False)
    assert attempt(length="1001") == (413, False)
    assert attempt(length="1") == (401, True)             # read, then found not to be the body its signature covers
    assert kept(env, seq) == {} and incoming(env) == []


def test_a_file_sent_slowly_is_held_to_the_time_its_request_began(client, owner, device, workspace, monkeypatch, env):
    s, seq = taken_move(client, owner, device)
    began = int(time.time()) - 350                       # signed then; the body took six minutes to come
    r = put(s, seq, "transcript", TRANSCRIPT, ts=began)
    assert r.status_code == 401                          # a request this old is refused, by the server's clock now

    class Then:
        @staticmethod
        def time():
            return began
    monkeypatch.setattr(moves, "time", Then)             # the server's clock read as the request began
    r = put(s, seq, "transcript", TRANSCRIPT, ts=began)
    assert r.status_code == 200, r.text
    assert kept(env, seq)["transcript"] == TRANSCRIPT


def test_an_upload_is_refused_before_its_body_is_read_unless_its_link_took_the_move_and_sends_this_file_alone(
        client, owner, device, workspace, env):
    import asyncio
    mac = paired(client, owner)
    mini = paired(client, owner, name="mini")
    lists(mac, (SESSION, False))
    seq, _ = signed_move(client, owner, device, mac)

    def attempt(s):
        path = f"/moves/{seq}/bundle"
        signed = _signed_headers(s, "PUT", path, b"x")
        request = Unread({"authorization": signed["Authorization"], "content-length": "1",
                          "x-body-sha256": signed["X-Body-SHA256"]}, "/api/ade/link" + path)
        with pytest.raises(link.LinkError) as e:
            asyncio.run(moves.receive(request, seq, "bundle"))
        return e.value.status, str(e.value), request.read

    assert attempt(mac)[0::2] == (409, False)                    # asked: not taken yet
    assert take(mac, seq).status_code == 200
    assert attempt(mini)[0::2] == (404, False)                   # another link's move
    with moves._uploading_lock:
        moves._uploading.add((seq, "bundle"))                    # an upload of this file under way
    try:
        assert attempt(mac) == (409, moves.UPLOADING, False)
        assert put(mac, seq, "transcript", TRANSCRIPT).status_code == 200      # the other file is sent the while
    finally:
        with moves._uploading_lock:
            moves._uploading.discard((seq, "bundle"))
    assert put(mac, seq, "bundle", BUNDLE).status_code == 200
    assert kept(env, seq) == {"transcript": TRANSCRIPT, "bundle": BUNDLE} and incoming(env) == []


def test_an_upload_lets_the_next_one_go_whatever_became_of_it(client, owner, device, workspace, monkeypatch):
    monkeypatch.setattr(moves, "FILE_MAX", 1000)
    s, seq = taken_move(client, owner, device)
    assert put(s, seq, "bundle", b"x" * 1001).status_code == 413
    assert put(s, seq, "bundle", b"x" * 10, sign_body=b"other").status_code == 401
    assert moves._uploading == set()
    assert put(s, seq, "bundle", b"x" * 10).status_code == 200


def test_an_upload_is_authenticated_whole_before_anything_of_it_is_read_taken_or_said(client, owner, device,
                                                                                       workspace, env):
    import asyncio
    import hashlib
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_ade_link import raw
    s, seq = taken_move(client, owner, device)
    kid = hashlib.sha256(raw(s.key)).hexdigest()[:16]           # the first half of the fingerprint the page shows

    def attempt(at, headers):
        request = Unread(headers, f"/api/ade/link/moves/{at}/bundle")
        with pytest.raises(link.LinkError) as e:
            asyncio.run(moves.receive(request, at, "bundle"))
        return e.value.status, str(e.value), request.read

    def stranger(at, body=b"x"):
        """What one who knows the link's key id and not its key can send: its own signature under that key id."""
        signed = _signed_headers(s, "PUT", f"/moves/{at}/bundle", body, key=Ed25519PrivateKey.generate())
        return {"authorization": re.sub(r"key_id=[0-9a-f]{16}", f"key_id={kid}", signed["Authorization"]),
                "x-body-sha256": signed["X-Body-SHA256"]}

    # the same refusal for the link's move and for a seq that is no move: nothing said of either, no place taken
    refused = (401, f"The signature does not check (this ADE signs for {HOST}).", False)
    assert attempt(seq, stranger(seq)) == attempt(999999, stranger(999999)) == refused
    # nor of a length past the most, or an upload of it under way: those are told to its link alone
    assert attempt(seq, dict(stranger(seq), **{"content-length": str(moves.FILE_MAX + 1)})) == refused
    with moves._uploading_lock:
        moves._uploading.add((seq, "bundle"))
    try:
        assert attempt(seq, stranger(seq)) == refused
    finally:
        with moves._uploading_lock:
            moves._uploading.discard((seq, "bundle"))
    signed = _signed_headers(s, "PUT", f"/moves/{seq}/bundle", b"x")
    for given in ({}, {"x-body-sha256": "abc"}, {"x-body-sha256": "A" * 64}, {"x-body-sha256": "0" * 65}):
        assert attempt(seq, {"authorization": signed["Authorization"], **given}) == (401, moves.UNSIGNED_FILE, False)
    assert attempt(seq, {"authorization": signed["Authorization"], "x-body-sha256": "0" * 64})[0::2] == (401, False)
    assert moves._uploading == set() and move_row(seq)["state"] == "taken"
    # the link's own signature over one digest, and another body: read, found not to be the one signed, not kept
    r = put(s, seq, "bundle", b"x" * 10, sign_body=b"other")
    assert r.status_code == 401 and r.json()["detail"] == moves.NOT_THE_BODY
    assert kept(env, seq) == {} and incoming(env) == []
    assert put(s, seq, "bundle", b"x" * 10).status_code == 200


class Trickle(Unread):
    """A request whose body comes a byte, then nothing for long after."""

    async def stream(self):
        import asyncio
        self.read = True
        yield b"x"
        await asyncio.sleep(60)
        yield b"y"


class Drip(Unread):
    """A request whose body comes a byte every half second, ten in all: each soon after the last, all slowly."""
    BODY = b"x" * 10

    async def stream(self):
        import asyncio
        self.read = True
        for byte in self.BODY:
            await asyncio.sleep(0.5)
            yield bytes([byte])


@pytest.mark.parametrize("sender", [Trickle, Drip])
def test_an_upload_that_does_not_come_whole_within_its_time_is_refused_and_nothing_is_kept(client, owner, device,
                                                                                         workspace, env, monkeypatch,
                                                                                         sender):
    import asyncio
    assert moves.UPLOAD_WAIT == 600                              # ten minutes for 32 MiB
    monkeypatch.setattr(moves, "UPLOAD_WAIT", 2)
    s, seq = taken_move(client, owner, device)
    path = f"/moves/{seq}/bundle"
    signed = _signed_headers(s, "PUT", path, b"xy" if sender is Trickle else Drip.BODY)
    request = sender({"authorization": signed["Authorization"], "x-body-sha256": signed["X-Body-SHA256"]},
                     "/api/ade/link" + path)
    started = time.monotonic()
    with pytest.raises(link.LinkError) as e:
        asyncio.run(moves.receive(request, seq, "bundle"))
    assert e.value.status == 408 and request.read and time.monotonic() - started < 4.5     # the drip takes five
    assert kept(env, seq) == {} and incoming(env) == [] and moves._uploading == set()
    assert move_row(seq)["state"] == "taken"


@pytest.mark.parametrize("over, words", [
    ({"head": "A" * 40}, "40 lowercase hex"),
    ({"head": "a" * 39}, "40 lowercase hex"),
    ({"head": None}, "40 lowercase hex"),
    ({"changes": "c" * 41}, "or null"),
    ({"bundle": "yes"}, "true or false"),
    ({"session": "-x"}, "names its session"),
    ({"remote": "https://me:pa55word@github.com/acme/demo.git"}, "names a user or a password"),
    ({"remote": "https://github.com/acme/demo.git?token=ghp_abcdefghijklmnop"}, "GitHub token"),
    ({"remote": ""}, "remote is text"),
    ({"branch": "main\nmore"}, "not shown as itself"),
    ({"branch": "x" * 256}, "at most 255"),
    ({"cwd": "/Users/me/" + chr(0x202E) + "work"}, "not shown as itself"),
    ({"cwd": 5}, "folder is text"),
    ({"extra": 1}, "done is {session"),
])
def test_a_move_s_done_is_held_to_its_fields(client, owner, device, workspace, over, words):
    s, seq = taken_move(client, owner, device)
    assert put(s, seq, "transcript", TRANSCRIPT).status_code == 200
    r = say_done(s, seq, **over)
    assert r.status_code == 422 and words in r.json()["detail"], r.text
    assert move_row(seq)["state"] == "taken"


# a remote holding a GitHub token is refused as holding a secret, before its address is read for a user: the token is
# the worse of the two, and the refusal says so
TOKEN_IN_REMOTE = ("The remote holds something shaped like a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_); the ADE keeps "
                   "no secret.")


@pytest.mark.parametrize("remote, words", [
    ("https://ghs_abc123@github.com/acme/demo.git", TOKEN_IN_REMOTE),           # a token as the user name
    ("ghs_abc123@github.com:acme/demo.git", TOKEN_IN_REMOTE),
    ("ghs_abc123@github.com/acme/demo", TOKEN_IN_REMOTE),
    ("https://me@github.com/acme/demo.git", moves.USER_IN_REMOTE),     # a user alone, its name no secret's shape
    ("https://me:pa55word@github.com/acme/demo.git", moves.USER_IN_REMOTE),
    ("ssh://git@github.com/acme/demo.git", moves.USER_IN_REMOTE),       # an address with a scheme comes bare
    ("me@github.com:acme/demo.git", moves.USER_IN_REMOTE),              # git's own form, a user other than git
    ("git@x@github.com:acme/demo.git", moves.USER_IN_REMOTE),
    ("me@github.com/acme/demo", moves.USER_IN_REMOTE),                  # no scheme
    ("me@github.com:acme/a://b.git", moves.USER_IN_REMOTE),             # a :// later on is no scheme
    ("me@github.com:a://b.git", moves.USER_IN_REMOTE),
    ("me@github.com/acme/a://b", moves.USER_IN_REMOTE),
    ("https://github.com/acme/demo.git?ref=main", moves.QUERY_IN_REMOTE),
    ("https://github.com/acme/demo.git#readme", moves.QUERY_IN_REMOTE),
    ("github.com/acme/demo?x=1", moves.QUERY_IN_REMOTE),
    ("https://[::1/acme/demo", moves.UNREAD_REMOTE),
])
def test_a_remote_holding_a_token_naming_a_user_or_holding_a_query_is_refused(client, owner, device, workspace, remote,
                                                                              words):
    s, seq = taken_move(client, owner, device)
    assert put(s, seq, "transcript", TRANSCRIPT).status_code == 200
    r = say_done(s, seq, remote=remote, bundle=False)
    assert r.status_code == 422 and r.json()["detail"] == words, r.text
    assert move_row(seq)["state"] == "taken"


@pytest.mark.parametrize("remote, kept_as", [
    ("git@github.com:acme/demo.git", "git@github.com:acme/demo.git"),
    ("github.com:acme/demo.git", "github.com:acme/demo.git"),           # what the link sends for git's own form
    ("https://github.com/acme/demo.git", "https://github.com/acme/demo.git"),
    ("ssh://github.com:22/acme/demo.git", "ssh://github.com:22/acme/demo.git"),
    ("file:///srv/git/demo.git", "file:///srv/git/demo.git"),
    ("/home/a@b/demo#1.git", "/home/a@b/demo#1.git"),                   # a path on the machine, as it is
    ("  https://github.com/acme/demo.git ", "https://github.com/acme/demo.git"),
])
def test_a_remote_as_its_host_and_path_alone_is_kept(client, owner, device, workspace, remote, kept_as):
    workspace.running = False
    s, seq = taken_move(client, owner, device)
    assert put(s, seq, "transcript", TRANSCRIPT).status_code == 200
    r = say_done(s, seq, remote=remote, bundle=False)
    assert r.status_code == 200, r.text
    assert json.loads(move_row(seq)["done"])["remote"] == kept_as


def test_every_address_the_link_s_client_sends_is_taken_as_it_is():
    import ade_link
    for given in ("https://x-access-token:s3cret@github.com/o/r.git", "https://only-a-token@github.com/o/r",
                  "HTTP://u:p@host:8080/o/r", "ssh://git:pw@github.com:22/o/r.git", "ssh://git@github.com/o/r.git",
                  "git@github.com:o/r.git", "github.com:o/r.git", "file:///srv/git/r.git", "/srv/git/r.git",
                  "/home/a@b/r.git", "https://u:p@ss@host/o/r?private_token=x#top"):
        sent = ade_link._remote_shown(given)
        assert moves._remote(sent) == sent, given


def test_a_move_is_done_once_its_transcript_and_the_bundle_it_names_are_in(client, owner, device, workspace, env):
    s, seq = taken_move(client, owner, device)
    r = say_done(s, seq, bundle=False)
    assert r.status_code == 409 and "transcript has not been sent" in r.json()["detail"]
    assert put(s, seq, "transcript", TRANSCRIPT).status_code == 200
    r = say_done(s, seq, bundle=True)
    assert r.status_code == 409 and "bundle has not been sent" in r.json()["detail"]
    r = say_done(s, seq, session="s2", bundle=False)
    assert r.status_code == 422 and "not another" in r.json()["detail"]
    assert move_row(seq)["state"] == "taken"
    workspace.running = False                                                # so it waits, uploaded, to be read
    assert put(s, seq, "bundle", BUNDLE).status_code == 200
    r = say_done(s, seq, bundle=False, changes=None, branch=None)         # a bundle sent, then said to be none
    assert r.status_code == 200 and r.json() == {"seq": seq, "state": "uploaded"}
    row = move_row(seq)
    assert row["state"] == "uploaded" and row["uploaded_at"] is not None and row["bundle_size"] is None
    assert json.loads(row["done"]) == {"remote": REMOTE, "branch": None, "head": HEAD, "changes": None, "cwd": CWD,
                                       "bundle": False}
    assert kept(env, seq) == {"transcript": TRANSCRIPT}


def test_a_link_says_why_it_could_not_send_a_move_and_what_it_sent_goes(client, owner, device, workspace, env):
    s, seq = taken_move(client, owner, device)
    assert put(s, seq, "transcript", TRANSCRIPT).status_code == 200
    for reason, words in (("two\nlines", "not shown as itself"), ("x" * 301, "at most 300"), ("", "reason is text"),
                          ("key sk-ant-abc", "Anthropic key")):
        r = s.call("POST", f"/moves/{seq}/failed", {"reason": reason})
        assert r.status_code == 422 and words in r.json()["detail"], r.text
    assert s.call("POST", f"/moves/{seq}/failed", {"why": "x"}).status_code == 422
    r = s.call("POST", f"/moves/{seq}/failed", {"reason": "The session is still running: end it first."})
    assert r.status_code == 200 and r.json() == {"seq": seq, "state": "failed"}
    row = move_row(seq)
    assert (row["state"], row["reason"]) == ("failed", "The session is still running: end it first.")
    assert kept(env, seq) == {}
    assert owners(client, owner)[0]["reason"] == "The session is still running: end it first."


def test_a_session_its_link_lists_withheld_since_is_not_sent_and_its_move_fails(client, owner, device, workspace, env):
    s, seq = taken_move(client, owner, device)
    assert put(s, seq, "transcript", TRANSCRIPT).status_code == 200
    lists(s, (SESSION, True))                             # it touched an excepted project since
    r = put(s, seq, "bundle", BUNDLE)
    assert r.status_code == 409 and r.json()["state"] == "failed" and "withheld" in r.json()["detail"], r.text
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("failed", moves.WITHHELD)
    assert kept(env, seq) == {} and incoming(env) == []
    s2, seq2 = taken_move(client, owner, device, name="mini")
    assert put(s2, seq2, "transcript", TRANSCRIPT).status_code == 200
    lists(s2, (SESSION, True))
    r = say_done(s2, seq2, bundle=False)
    assert r.status_code == 409 and move_row(seq2)["state"] == "failed" and kept(env, seq2) == {}


def test_a_session_its_link_no_longer_lists_is_not_sent_and_its_move_fails(client, owner, device, workspace, env):
    s, seq = taken_move(client, owner, device)
    assert put(s, seq, "transcript", TRANSCRIPT).status_code == 200
    newest = [{"id": f"o{i}", "title": f"Other {i}", "withheld": False, "project": "~/work/other"} for i in range(100)]
    r = s.call("PUT", "/history", {"sessions": newest, "conversations": False, "withheld": [SESSION]})
    assert r.status_code == 200, r.text                          # withheld since, and past the newest 100 it lists
    r = put(s, seq, "bundle", BUNDLE)
    assert r.status_code == 409 and r.json()["detail"] == moves.UNLISTED and r.json()["state"] == "failed", r.text
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("failed", moves.UNLISTED)
    assert kept(env, seq) == {} and incoming(env) == []
    s2, seq2 = taken_move(client, owner, device, name="mini")
    assert put(s2, seq2, "transcript", TRANSCRIPT).status_code == 200
    assert s2.call("PUT", "/history", {"sessions": [], "conversations": False}).status_code == 200    # list withdrawn
    r = say_done(s2, seq2, bundle=False)
    assert r.status_code == 409 and r.json()["detail"] == moves.UNLISTED, r.text
    assert (move_row(seq2)["state"], move_row(seq2)["reason"]) == ("failed", moves.UNLISTED) and kept(env, seq2) == {}


def test_a_session_its_link_no_longer_lists_since_its_upload_is_not_handed_over(client, owner, device, workspace,
                                                                               env):
    workspace.running = False
    s, seq = sent_whole(client, owner, device)
    assert s.call("PUT", "/history", {"sessions": [], "conversations": False}).status_code == 200
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert r.status_code == 200 and (r.json()["state"], r.json()["reason"]) == ("failed", moves.UNLISTED), r.text
    assert workspace.agent.files == {} and kept(env, seq) == {}


def test_the_files_and_the_moves_time_bounds_are_kept_by_the_sweep(client, owner, device, workspace, env):
    workspace.running = False
    old, new = sent_whole(client, owner, device), sent_whole(client, owner, device, name="mini", session="s2")
    stalled_link, stalled = taken_move(client, owner, device, name="pi", session="s3")
    assert put(stalled_link, stalled, "transcript", TRANSCRIPT).status_code == 200
    asked_link = paired(client, owner, name="air")
    lists(asked_link, ("s4", False))
    asked, _ = signed_move(client, owner, device, asked_link, "s4")
    now = int(time.time())
    with store.connect() as db:
        db.execute("UPDATE ade_link_moves SET uploaded_at = ? WHERE seq = ?", (now - moves.KEPT, old[1]))
        db.execute("UPDATE ade_link_moves SET taken_at = ? WHERE seq = ?", (now - moves.KEPT, stalled))
        db.execute("UPDATE ade_link_moves SET expires_at = ? WHERE seq = ?", (now, asked))
    (env / "moves" / "incoming" / "transcript-left").write_bytes(b"cut short")
    os.utime(env / "moves" / "incoming" / "transcript-left", (now - 3601, now - 3601))
    (env / "moves" / "incoming" / "bundle-now").write_bytes(b"still coming")
    moves.sweep(now)
    states = {m["seq"]: (m["state"], m["reason"]) for m in all_moves()}
    assert states[old[1]] == ("expired", moves.NOT_STARTED) and states[stalled] == ("expired", moves.NOT_FINISHED)
    assert states[asked] == ("expired", moves.NOT_TAKEN) and states[new[1]][0] == "uploaded"
    assert kept(env, old[1]) == {} and kept(env, stalled) == {}
    assert kept(env, new[1]) == {"transcript": TRANSCRIPT, "bundle": BUNDLE}
    assert incoming(env) == ["bundle-now"]


def test_a_move_left_starting_by_a_process_that_stopped_is_uploaded_again(client, owner, device, workspace, env):
    workspace.running = False
    _, seq = sent_whole(client, owner, device)
    with store.connect() as db:
        db.execute("UPDATE ade_link_moves SET state = 'starting' WHERE seq = ?", (seq,))
    with moves._handing_lock:
        moves._handing.add(seq)                           # this process is handing it over: left as it is
    moves.sweep()
    assert move_row(seq)["state"] == "starting"
    with moves._handing_lock:
        moves._handing.discard(seq)
    moves.sweep()
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("uploaded", moves.CUT_SHORT)
    assert kept(env, seq) == {"transcript": TRANSCRIPT, "bundle": BUNDLE}


def test_the_files_of_a_move_that_ended_are_deleted_and_a_folder_no_move_names_after_an_hour(client, owner, device,
                                                                                             workspace, env):
    workspace.running = False
    _, seq = sent_whole(client, owner, device)
    with store.connect() as db:
        db.execute("UPDATE ade_link_moves SET state = 'failed' WHERE seq = ?", (seq,))
    orphan, fresh = env / "moves" / "999990", env / "moves" / "999991"
    for folder in (orphan, fresh):
        folder.mkdir()
        (folder / "transcript").write_bytes(b"x")
    os.utime(orphan, (time.time() - 3601, time.time() - 3601))
    moves.sweep()
    assert kept(env, seq) == {} and not orphan.exists() and fresh.exists()


# ---- what the app takes as a body ------------------------------------------------------------------------------------

def test_only_a_move_s_two_file_routes_take_a_body_that_is_not_json_or_over_1_mib(client, owner, device, workspace):
    from proofpane_open import main
    s, seq = taken_move(client, owner, device)
    big = b"\xff" * (main.MAX_BODY + 10)
    assert put(s, seq, "bundle", big).status_code == 200
    for path in (f"/moves/{seq}/done", "/events", f"/moves/{seq}/transcript/x"):
        r = s.call("POST", path, raw_body=big)
        assert r.status_code == 413, (path, r.text)


# ---- handing it to the workspace -------------------------------------------------------------------------------------

def test_an_uploaded_move_is_handed_to_the_running_workspace_and_started_there(client, owner, device, workspace, env):
    s, seq = sent_whole(client, owner, device)
    agent = workspace.agent
    assert agent.files == {(str(seq), "transcript"): TRANSCRIPT, (str(seq), "bundle"): BUNDLE}
    (body,) = agent.started
    assert body["program"] == "claude" and body["repo"] is None and body["account"] == "default"
    assert body["moved"] == {"id": str(seq), "session": SESSION, "remote": REMOTE, "branch": "main", "head": HEAD,
                             "changes": CHANGES, "cwd": CWD, "bundle": True}
    assert body["task"].startswith("Work on s1\n") and "from mac" in body["task"]
    assert {k for k in body if k not in ("moved",)} >= {"task", "base", "pull", "teleport", "cols", "rows"}
    order = [(m, p) for m, p in agent.seen if p.startswith(("/moves", "/sessions"))]
    assert order == [("PUT", f"/moves/{seq}/transcript"), ("PUT", f"/moves/{seq}/bundle"), ("POST", "/sessions")]
    row = move_row(seq)
    assert (row["state"], row["workspace_session"], row["reason"]) == ("started", "0123456789ab", None)
    assert kept(env, seq) == {}                                              # the workspace has them now
    (v,) = owners(client, owner, link_id=s.link_id, session=SESSION)
    assert (v["state"], v["workspace_session"], v["branch"], v["head"], v["changes"]) == \
        ("started", "0123456789ab", "main", HEAD, CHANGES)
    with store.connect() as db:                           # the account it started on, as the app records a session's
        assert db.execute("SELECT account FROM ade_session_accounts WHERE session = ?",
                          ("0123456789ab",)).fetchone()["account"] == "default"


def test_the_workspace_s_asks_switch_is_the_person_s_choice_before_the_moved_session_starts(client, owner, device,
                                                                                            workspace):
    """As before any session starts there (workspace_routes._asks_as_chosen): a token goes to a program only as the
    person chose, whatever the workspace's own switch says."""
    with store.connect() as db:
        db.execute("UPDATE users SET asks_on = 1")
    sent_whole(client, owner, device)
    assert workspace.agent.asks == [{"on": True}]
    order = [p for _, p in workspace.agent.seen if p in ("/asks/settings", "/sessions")]
    assert order == ["/asks/settings", "/sessions"]


def test_a_move_with_no_bundle_sends_none(client, owner, device, workspace):
    _, seq = sent_whole(client, owner, device, bundle=False)
    agent = workspace.agent
    assert agent.files == {(str(seq), "transcript"): TRANSCRIPT}
    assert agent.started[0]["moved"]["bundle"] is False and move_row(seq)["state"] == "started"


def test_a_move_the_workspace_refuses_fails_in_its_words_and_its_files_go(client, owner, device, workspace, env):
    workspace.agent.refuse = "The repository is not in the workspace: clone it there first."
    _, seq = sent_whole(client, owner, device)
    row = move_row(seq)
    assert (row["state"], row["reason"]) == ("failed", "The repository is not in the workspace: clone it there first.")
    assert row["workspace_session"] is None and kept(env, seq) == {}


@pytest.mark.parametrize("broken, name", [(("PUT", "/moves/"), "mac"), (("POST", "/sessions"), "mini")])
def test_a_workspace_that_answers_with_an_error_of_its_own_leaves_the_move_uploaded_and_its_files(client, owner, device,
                                                                                                  workspace, env,
                                                                                                  broken, name):
    workspace.agent.broken = {broken: 503}
    _, seq = sent_whole(client, owner, device, name=name)
    row = move_row(seq)
    assert (row["state"], row["reason"]) == ("uploaded", "The workspace answered 503. Carry the move on again.")
    assert row["workspace_session"] is None and kept(env, seq) == {"transcript": TRANSCRIPT, "bundle": BUNDLE}
    workspace.agent.broken = {}
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert r.json()["state"] == "started" and kept(env, seq) == {}


def test_a_move_waits_uploaded_while_the_workspace_is_not_running_and_the_owner_starts_it(client, owner, device,
                                                                                          workspace):
    workspace.running = False
    s, seq = sent_whole(client, owner, device)
    assert workspace.agent.started == []
    (v,) = owners(client, owner, link_id=s.link_id, session=SESSION)
    assert (v["state"], v["reason"]) == ("uploaded", moves.NOT_RUNNING)
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert r.status_code == 200 and r.json()["state"] == "started", r.text
    assert workspace.ensured == 1 and r.json()["workspace_session"] == "0123456789ab"
    assert r.headers["cache-control"] == "no-store"
    again = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert again.status_code == 409 and again.json()["state"] == "started"


def test_a_move_waits_uploaded_until_claude_is_signed_in_there(client, owner, device, workspace):
    workspace.agent.signed_in = False
    _, seq = sent_whole(client, owner, device)
    from proofpane_open.ade import workspace_routes
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("uploaded", workspace_routes.CLAUDE_FIRST)
    assert workspace.agent.files == {} and workspace.agent.started == []
    workspace.agent.signed_in = True
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert r.json()["state"] == "started" and workspace.ensured == 0          # it ran: nothing to start


def test_a_workspace_that_does_not_answer_leaves_the_move_uploaded(client, owner, device, workspace):
    port = workspace.agent.server.server_address[1]
    workspace.agent.close()
    workspace.agent.endpoint = workspaces.Endpoint(f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}",
                                                   StandIn.SECRET)
    _, seq = sent_whole(client, owner, device)
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("uploaded", moves.NOT_ANSWERING)


def test_a_workspace_that_takes_a_file_and_gives_no_answer_leaves_the_move_uploaded_not_failed(client, owner, device,
                                                                                                workspace, env):
    workspace.agent.silent_puts = True
    _, seq = sent_whole(client, owner, device)
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("uploaded", moves.NOT_ANSWERING)
    assert workspace.agent.started == [] and kept(env, seq) == {"transcript": TRANSCRIPT, "bundle": BUNDLE}
    workspace.agent.silent_puts = False                    # it answers again: carried on, it starts
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert r.json()["state"] == "started" and kept(env, seq) == {}


class SweepEach:
    """moves._handing_lock, with the sweep's round run each time the lock is taken, but by the sweep itself: wherever a
    hand-off takes it, a sweep comes then. One that came between a hand-off marking its move starting and naming it as
    this process's would put the move back to uploaded, and the session the workspace then started would be recorded
    nowhere."""

    def __init__(self):
        self.lock, self.sweeping, self.swept = threading.Lock(), False, 0

    def __enter__(self):
        if not self.sweeping:
            self.sweeping = True
            try:
                moves.sweep()
            finally:
                self.sweeping = False
            self.swept += 1
        self.lock.acquire()

    def __exit__(self, *exc):
        self.lock.release()


@pytest.mark.parametrize("when", ["each time it takes its lock", "once it marks the move starting"])
def test_a_sweep_during_a_hand_off_leaves_the_move_to_it(client, owner, device, workspace, monkeypatch, when):
    workspace.running = False
    _, seq = sent_whole(client, owner, device)
    workspace.running = True
    lock = SweepEach()
    if when == "each time it takes its lock":
        monkeypatch.setattr(moves, "_handing_lock", lock)
    else:
        send = moves._send

        def swept_first(*args):
            assert move_row(seq)["state"] == "starting"
            moves.sweep()
            return send(*args)

        monkeypatch.setattr(moves, "_send", swept_first)
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert (r.json()["state"], r.json()["workspace_session"], r.json()["reason"]) == ("started", "0123456789ab", None)
    assert moves._handing == set()
    assert lock.swept >= (2 if when == "each time it takes its lock" else 0)     # as it named the move and let it go


def test_a_hand_off_leaves_a_move_this_process_hands_over_already_to_that_one(client, owner, device, workspace):
    workspace.running = False
    _, seq = sent_whole(client, owner, device)
    workspace.running = True
    with moves._handing_lock:
        moves._handing.add(seq)                                  # another thread of this process hands it over
    try:
        assert moves.hand_off(account(client, owner), seq) == "starting"
        assert seq in moves._handing                             # that one's mark is its own still
        assert move_row(seq)["state"] == "uploaded" and workspace.agent.started == []
    finally:
        with moves._handing_lock:
            moves._handing.discard(seq)


def test_a_hand_off_s_calls_to_the_workspace_are_held_to_its_time_in_all(client, owner, device, workspace, env,
                                                                         monkeypatch):
    assert moves.HAND_OFF_WAIT == 900
    workspace.running = False
    _, seq = sent_whole(client, owner, device)
    workspace.running = True
    asked, put_file, call, clock = [], workspace_client.put_file, workspace_client.call, [time.time()]

    def timed_put(endpoint, path, file_path, timeout=120):
        asked.append(timeout)
        clock[0] += 60                                   # each file takes a minute of the hand-off's time
        return put_file(endpoint, path, file_path, timeout=timeout)

    def timed_call(endpoint, method, path, body=None, timeout=30):
        if path == "/sessions":
            asked.append(timeout)
        return call(endpoint, method, path, body, timeout=timeout)

    monkeypatch.setattr(workspace_client, "put_file", timed_put)
    monkeypatch.setattr(workspace_client, "call", timed_call)
    monkeypatch.setattr(moves, "time", types.SimpleNamespace(time=lambda: clock[0], sleep=time.sleep))
    monkeypatch.setattr(moves, "HAND_OFF_WAIT", 150)
    assert client.post(f"/api/ade/link/moves/{seq}/start", headers=owner).json()["state"] == "started"
    assert asked == [150, 90, 30]                        # each call what was left of the 150 seconds, not more
    workspace.running = False
    _, seq2 = sent_whole(client, owner, device, name="mini")
    workspace.running = True
    asked.clear()
    monkeypatch.setattr(moves, "HAND_OFF_WAIT", 100)     # its files take its time: none is left for the session
    r = client.post(f"/api/ade/link/moves/{seq2}/start", headers=owner)
    assert (r.json()["state"], r.json()["reason"]) == ("uploaded", moves.TOO_LONG) and asked == [100, 40]
    assert len(workspace.agent.started) == 1 and kept(env, seq2) != {}                  # the first one's alone
    monkeypatch.setattr(moves, "HAND_OFF_WAIT", 0)       # no time left before its files
    asked.clear()
    r = client.post(f"/api/ade/link/moves/{seq2}/start", headers=owner)
    assert (r.json()["state"], r.json()["reason"]) == ("uploaded", moves.TOO_LONG) and asked == []


def test_a_move_whose_files_are_gone_fails_when_it_is_carried_on(client, owner, device, workspace, env):
    workspace.running = False
    _, seq = sent_whole(client, owner, device)
    os.unlink(env / "moves" / str(seq) / "bundle")
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert r.status_code == 200 and (r.json()["state"], r.json()["reason"]) == ("failed", moves.GONE), r.text
    assert workspace.agent.files == {} and kept(env, seq) == {}


def test_a_session_withheld_since_its_upload_is_not_handed_over(client, owner, device, workspace, env):
    workspace.running = False
    s, seq = sent_whole(client, owner, device)
    lists(s, (SESSION, True))
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert r.status_code == 200 and (r.json()["state"], r.json()["reason"]) == ("failed", moves.WITHHELD)
    assert workspace.agent.files == {} and kept(env, seq) == {}


def test_the_owner_starts_only_an_uploaded_move_of_their_own(client, owner, device, workspace):
    s, seq = taken_move(client, owner, device)
    r = client.post(f"/api/ade/link/moves/{seq}/start", headers=owner)
    assert r.status_code == 409 and r.json()["state"] == "taken"
    stranger = signed_up(client, OTHER)
    assert client.post(f"/api/ade/link/moves/{seq}/start", headers=stranger).status_code == 404
    assert client.get("/api/ade/link/moves", headers=stranger).json() == {"moves": []}
    assert client.post(f"/api/ade/link/moves/{seq}/start").status_code == 401
    assert client.get("/api/ade/link/moves").status_code == 401
    assert s.call("GET", "/moves").status_code == 401                         # a link's signature is not a sign-in
    assert client.post("/api/ade/link/moves/0/start", headers=owner).status_code == 404


def test_the_owner_s_list_is_a_session_s_moves_newest_first(client, owner, device, workspace):
    workspace.running = False
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    first, _ = signed_move(client, owner, device, mac)
    assert client.post(f"/api/ade/link/commands/{first}/withdraw", headers=owner).status_code == 200   # ended
    second, _ = signed_move(client, owner, device, mac)
    other, _ = signed_move(client, owner, device, mac, "s2")
    assert [v["seq"] for v in owners(client, owner, link_id=mac.link_id, session=SESSION)] == [second, first]
    assert [v["seq"] for v in owners(client, owner)] == [other, second, first]


def test_revoking_a_link_fails_its_moves_not_yet_in_the_workspace_and_deletes_their_files(client, owner, device,
                                                                                         workspace, env):
    workspace.running = False
    s, seq = sent_whole(client, owner, device)
    assert revoke(client, owner, s.link_id).status_code == 200
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("failed", moves.REVOKED)
    assert kept(env, seq) == {}


def test_a_file_reaches_the_workspace_streamed_from_disk_with_its_secret(tmp_path):
    agent = StandIn()
    try:
        path = tmp_path / "bundle"
        path.write_bytes(BUNDLE)
        assert workspace_client.put_file(agent.endpoint, "/moves/7/bundle", str(path)) == {"seq": "7",
                                                                                            "bundle": len(BUNDLE)}
        assert agent.files == {("7", "bundle"): BUNDLE}
        wrong = workspaces.Endpoint(agent.endpoint.http, agent.endpoint.ws, "x" * 43)
        with pytest.raises(workspace_client.WorkspaceCallError) as e:
            workspace_client.put_file(wrong, "/moves/7/bundle", str(path))
        assert not isinstance(e.value, workspace_client.NotAnswering)
    finally:
        agent.close()
    with pytest.raises(workspace_client.NotAnswering):
        workspace_client.put_file(agent.endpoint, "/moves/7/bundle", str(path), timeout=5)
