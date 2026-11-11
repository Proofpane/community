"""A session moved from this machine into the hosted workspace, the machine's side (ade_link.py move): a move the owner
signed is looked for, verified offline against the pinned keys as an instruction is, taken once, its session judged as
the History list and the mirror judge it, and its transcript and repository state sent, or the ADE told why not. The
ADE here is a stand-in that answers the routes as the design states them (the transcript, the bundle, done
and failed; and the feed, one event and the take as the app answers them), checking every request's signature with the
link's key; the repositories are real ones made in tmp_path, read by the git on this machine."""

import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import ade_link
from proofpane_open.ade import link_commands  # noqa: F401  (puts the open packages on sys.path first)
from proofpane_core.canonical import canonicalize
from test_ade_link_client import run
from test_passkeys import Authenticator, as_step

ACCOUNT = "acct-owner"
SCRIPT = os.path.abspath(ade_link.__file__)
DROP = object()                                 # a payload field left out


@pytest.fixture(autouse=True)
def quiet_machine(monkeypatch):
    # these tests may run inside a Claude Code session, whose inbox and folders they must not reach
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CONFIG_DIR", "ADE_OWNER_KEYS",
                 "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)


# ---- the stand-in ADE -----------------------------------------------------------------------------------------------

class MoveADE:
    """The ADE's side of a move as the design states it: the feed, one event and the take, then PUT
    /moves/SEQ/transcript and /bundle, POST /moves/SEQ/done and /failed, which it takes only from the link that took the
    move, while it is taken (refused otherwise with the move's state, as the app refuses it). Each request's signature
    is checked with the link's key; what it is sent is kept. `refuse[(method, route)]` holds answers given in place of
    the route's own, one per request (route with {seq}); `lost[(method, route)]`, how many of the route's next requests
    are taken with their answer lost on the way (the connection closed before it). A copy that asks again for a take it
    made is given it again once, as the app gives it (retry)."""

    def __init__(self):
        self.events, self.moves, self.got, self.requests, self.refuse, self.lost = [], {}, {}, [], {}, {}
        self.public, self.taker = None, None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _serve(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                raw_path, _, raw_query = self.path.partition("?")
                status, answer = outer.answer(self.command, raw_path, raw_query, body,
                                              self.headers.get("Authorization"))
                shape = (self.command, re.sub(r"/[0-9]+(?=/|$)", "/{seq}", raw_path[len("/api/ade/link"):]))
                if outer.lost.get(shape):
                    outer.lost[shape] -= 1
                    self.close_connection = True
                    return
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _serve

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.url = f"http://{self.host}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def signed(self, method, raw_path, raw_query, body, auth):
        m = re.fullmatch(r"ADE-Link key_id=([0-9a-f]{16}), ts=(\d+), sig=([A-Za-z0-9_-]+)", auth or "")
        if not m or self.public is None or m.group(1) != hashlib.sha256(self.public).hexdigest()[:16]:
            return False
        message = "\n".join((self.host, method, raw_path, raw_query, m.group(2),
                             hashlib.sha256(body).hexdigest())).encode()
        try:
            Ed25519PublicKey.from_public_bytes(self.public).verify(ade_link.unb64u(m.group(3)), message)
        except InvalidSignature:
            return False
        return True

    def answer(self, method, raw_path, raw_query, body, auth):
        route = raw_path[len("/api/ade/link"):]
        self.requests.append((method, route))
        if not self.signed(method, raw_path, raw_query, body, auth):
            return 401, {"detail": "The signature does not check."}
        shape = re.sub(r"/[0-9]+(?=/|$)", "/{seq}", route)
        if self.refuse.get((method, shape)):
            status, detail = self.refuse[(method, shape)].pop(0)
            return status, {"detail": detail}
        query = dict(urllib.parse.parse_qsl(raw_query))
        if (method, route) == ("GET", "/feed"):
            after = int(query.get("after") or 0)
            rows = [e for e in self.events if e["seq"] > after and (not query.get("turn") or e.get("needs_turn"))]
            limit = int(query.get("limit") or 200)
            head = max([e["seq"] for e in self.events], default=0)
            shown = [{k: v for k, v in e.items() if k not in ("payload", "signature", "client_id")}
                     for e in rows[:limit]]
            return 200, {"events": shown, "through": rows[limit - 1]["seq"] if len(rows) > limit else head,
                         "head": head}
        if method == "GET" and shape == "/events/{seq}":
            found = [e for e in self.events if e["seq"] == int(route.rsplit("/", 1)[1])]
            return (200, found[0]) if found else (404, {"detail": "No such event."})
        if (method, route) == ("POST", "/take"):
            data = json.loads(body)
            move = self.moves.get(data["seq"])
            if move is None:
                return 404, {"detail": "No command to this session has that seq."}
            if move["state"] == "taken" and move.get("instance") == data["instance"] and not move.get("retried"):
                move["retried"] = True
                return 200, {"seq": data["seq"], "taken_at": move["taken_at"], "retry": True}
            if move["state"] != "asked":
                return 409, {"detail": "Not taken.", "why": "taken"}
            if move["expires_at"] <= time.time():
                return 409, {"detail": "Not taken.", "why": "expired"}
            move.update(state="taken", instance=data["instance"], taken_at=int(time.time()))
            return 200, {"seq": data["seq"], "taken_at": move["taken_at"]}
        m = re.fullmatch(r"/moves/([0-9]+)/(transcript|bundle|done|failed)", route)
        if m:
            seq, part = int(m.group(1)), m.group(2)
            move = self.moves.get(seq)
            if move is None or move["state"] != "taken" or (method == "PUT") != (part in ("transcript", "bundle")):
                return 409, dict({"detail": "This move is not taken by this link, or is done or failed."},
                                 **({"state": move["state"]} if move else {}))
            got = self.got.setdefault(seq, {})
            if part in ("transcript", "bundle"):
                got[part] = body
            else:
                got[part] = json.loads(body)
                move["state"] = "uploaded" if part == "done" else "failed"
            return 200, {"seq": seq, "state": move["state"]}
        if (method, route) == ("PUT", "/history"):
            return 200, {"listed": 0, "noted": [], "talks": []}
        return 404, {"detail": "Not found."}

    def add(self, event, expires_at):
        self.events.append(event)
        self.moves[event["seq"]] = {"state": "asked", "expires_at": expires_at}

    def next_seq(self):
        return max([e["seq"] for e in self.events], default=0) + 1

    def asked(self, method, route):
        return [r for r in self.requests if r == (method, route)]


@pytest.fixture
def ade():
    a = MoveADE()
    yield a
    a.close()


# ---- the machine: a link, its pinned key, a home, a repository and a session ----------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    config = tmp_path / "gitconfig"                # git as the tests set it up, whatever this machine's own says
    config.write_text("[user]\n\tname = Ana\n\temail = ana@example.test\n[init]\n\tdefaultBranch = main\n"
                      "[advice]\n\tdetachedHead = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return h


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).stdout.strip()


def a_link(tmp_path, ade, device, where=None):
    """A link to the stand-in, its directory `where` (else in tmp_path), with the device's passkey pinned as the
    owner's."""
    d = where or tmp_path / "links" / "mac"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"url": ade.url, "name": "mac", "link_id": uuid.uuid4().hex}))
    key = ade_link.Link(str(d)).key(make=True)
    ade.public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    der = device.key.public_key().public_bytes(serialization.Encoding.DER,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    (d / "owner_keys.json").write_text(json.dumps({
        "account": ACCOUNT, "source": "pin", "pinned_at": int(time.time()),
        "keys": [{"credential_id": ade_link.b64u(device.credential_id), "alg": device.alg,
                  "public_key": ade_link.b64u(der), "name": "Ana's key",
                  "fingerprint": hashlib.sha256(der).hexdigest()[:32]}]}))
    return d


def a_move(ade, d, device, session, life=600, created=None, kind="move", sender="owner", **over):
    """A move signed by `device` as the page signs one, stored in the stand-in as the app stores one: its seq."""
    link = ade_link.Link(str(d))
    now = int(time.time()) if created is None else created
    p = {"v": 2, "deployment": link.host(), "purpose": "move", "account": ACCOUNT, "nonce": uuid.uuid4().hex,
         "id": uuid.uuid4().hex, "to": link.link_id, "session": session, "created_at": now, "expires_at": now + life}
    p.update(over)
    p = {k: v for k, v in p.items() if v is not DROP}
    raw = canonicalize(p)
    seq = ade.next_seq()
    ade.add({"seq": seq, "time": "2026-10-08T00:00:00Z", "kind": kind, "from": sender, "to": "mac", "ref": None,
             "text": "", "needs_turn": True, "closed_by": None, "from_id": sender, "to_id": link.link_id,
             "payload": raw.decode(), "client_id": p["id"], "passkey_id": "pk",
             "signature": device.get(as_step(raw))},
            expires_at=p["expires_at"])
    return seq


def a_repository(tmp_path, home, where="work/app"):
    """A clone of a remote in tmp_path, its first commit pushed: (its folder, the remote)."""
    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(remote))
    work = home / where
    work.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "-q", str(remote), str(work))
    (work / "README.md").write_text("hello\n")
    (work / "notes.txt").write_text("notes\n")
    (work / "gone.txt").write_text("to be deleted\n")
    (work / ".gitignore").write_text("*.log\nlocal.conf\n")
    (work / "local.conf").write_text("tracked though ignored\n")
    git("add", "README.md", "notes.txt", "gone.txt", ".gitignore", cwd=work)
    git("add", "-f", "local.conf", cwd=work)
    git("commit", "-qm", "first", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    git("fetch", "-q", "origin", cwd=work)
    return work, remote


def a_session(home, sid, cwd, extra=(), projects=None):
    """A Claude Code session that worked in `cwd`: its transcript under the projects folder (`projects`, else the
    home's), its path. Its entries are its own (two sessions with one first entry are read as copies of one)."""
    path = (projects or home / ".claude" / "projects") / "-work-app" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [{"type": "user", "uuid": f"u-{sid}", "origin": {"kind": "human"}, "turnOrigin": "human",
                "cwd": str(cwd), "sessionId": sid, "timestamp": "2026-10-08T10:00:00Z", "entrypoint": "cli",
                "message": {"role": "user", "content": [{"type": "text", "text": "make the build green"}]}},
               {"type": "assistant", "uuid": f"a-{sid}", "cwd": str(cwd), "sessionId": sid,
                "timestamp": "2026-10-08T10:01:00Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Green now."}]}}, *extra]
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


def listed(d, rule=None):
    """history.json written, and the History list's reading made whole by a run of it."""
    (d / "history.json").write_text(json.dumps(rule or {"projects": "all"}))
    ade_link._history_run(ade_link.Link(str(d)))


def as_left(work):
    """What a move must leave as it was: every file of the repository, its git directory's (index, refs, objects,
    logs, config) and its working tree's, by name and content, read without running git. A file's time is not
    compared: git may touch an object it reads, as any git command does."""
    files = {}
    for top, _, names in os.walk(work):
        for n in names:
            with open(os.path.join(top, n), "rb") as fh:
                files[os.path.relpath(os.path.join(top, n), work)] = hashlib.sha256(fh.read()).hexdigest()
    return files


def with_work_not_committed(work):
    """A commit not pushed, then staged, unstaged, untracked, ignored and deleted files, and a file tracked though
    ignored, changed."""
    (work / "src").mkdir()
    (work / "src" / "app.py").write_text("print('committed, not pushed')\n")
    git("add", "src/app.py", cwd=work)
    git("commit", "-qm", "not pushed", cwd=work)
    (work / "README.md").write_text("hello\nstaged line\n")
    git("add", "README.md", cwd=work)
    (work / "notes.txt").write_text("notes\nunstaged line\n")
    (work / "new.txt").write_text("untracked, not ignored\n")
    (work / "build.log").write_text("ignored\n")
    (work / "gone.txt").unlink()
    (work / "local.conf").write_text("tracked though ignored, changed\n")


# ---- what goes ------------------------------------------------------------------------------------------------------

def test_a_move_is_verified_taken_and_its_session_sent_with_its_repository_s_state(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, remote = a_repository(tmp_path, home)
    with_work_not_committed(work)
    transcript = a_session(home, "s-moved", work / "src")
    listed(d)
    head = git("rev-parse", "HEAD", cwd=work)
    before = as_left(work)
    seq = a_move(ade, d, device, "s-moved")
    code, out, err = run(capsys, "--dir", d, "move")
    assert code == 0, (out, err)
    # the History run's own requests left out: its list, and its read of the owner's encryption statements
    assert [r for r in ade.requests if r[1] not in ("/history", "/e2e")] == [
        ("GET", "/feed"), ("GET", f"/events/{seq}"), ("POST", "/take"), ("PUT", f"/moves/{seq}/transcript"),
        ("PUT", f"/moves/{seq}/bundle"), ("POST", f"/moves/{seq}/done")]
    got = ade.got[seq]
    assert got["transcript"] == transcript.read_bytes()                 # the transcript as it is on the machine
    done = got["done"]
    assert set(done) == {"session", "remote", "branch", "head", "changes", "cwd", "bundle"}
    assert (done["session"], done["remote"], done["branch"], done["head"], done["cwd"], done["bundle"]) == (
        "s-moved", str(remote), "main", head, str(work), True)            # the top, though it worked in src/
    assert re.fullmatch(r"[0-9a-f]{40}", done["changes"]) and done["changes"] != head
    assert out.startswith(f"ADE: move [{seq}] of session s-moved: sent to the ADE: its transcript "
                          f"({len(transcript.read_bytes())} bytes) and its repository's state (main at {head[:12]}, "
                          f"the work not committed as {done['changes'][:12]}; a bundle of {len(got['bundle'])} bytes)")
    assert as_left(work) == before          # the index, refs, objects and working tree as they were
    # the bundle needs the remote's commit and holds what the remote lacks, named by one ref: HEAD, at the tip
    header = got["bundle"].split(b"\n\n", 1)[0].decode().splitlines()
    assert header[0] == "# v2 git bundle"
    assert [x[1:41] for x in header if x.startswith("-")] == [git("rev-parse", "origin/main", cwd=work)]
    assert [x for x in header[1:] if not x.startswith("-")] == [f"{done['changes']} HEAD"]
    # a clone of the remote, given the bundle, holds the commit not pushed and the work not committed, as they were
    ws = tmp_path / "ws"
    git("clone", "-q", str(remote), str(ws))
    (tmp_path / "move.bundle").write_bytes(got["bundle"])
    git("bundle", "verify", str(tmp_path / "move.bundle"), cwd=ws)
    git("fetch", "-q", str(tmp_path / "move.bundle"), "HEAD", cwd=ws)
    assert git("rev-parse", "FETCH_HEAD", cwd=ws) == done["changes"]
    assert git("rev-parse", done["changes"] + "^", cwd=ws) == head and git("cat-file", "-t", head, cwd=ws) == "commit"
    names = set(git("ls-tree", "-r", "--name-only", done["changes"], cwd=ws).splitlines())
    assert names == {".gitignore", "README.md", "notes.txt", "new.txt", "local.conf", "src/app.py"}
    for name in names:
        assert git("show", f"{done['changes']}:{name}", cwd=ws) == (work / name).read_text().rstrip("\n")
    assert ade_link._moves(ade_link.Link(str(d)))[str(seq)]["state"] == "done"
    assert run(capsys, "--dir", d, "move")[:2] == (0, "ADE: no move for this link is waiting\n")    # done once


def test_a_session_whose_work_is_all_on_the_remote_sends_no_bundle_and_no_changes(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, remote = a_repository(tmp_path, home)
    a_session(home, "s-clean", work)
    listed(d)
    head = git("rev-parse", "HEAD", cwd=work)
    git("checkout", "-q", "--detach", cwd=work)                         # no branch: a detached HEAD
    seq = a_move(ade, d, device, "s-clean")
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 0 and not ade.asked("PUT", f"/moves/{seq}/bundle")
    assert ade.got[seq]["done"] == {"session": "s-clean", "remote": str(remote), "branch": None, "head": head,
                                    "changes": None, "cwd": str(work), "bundle": False}
    assert "(no branch at " in out
    assert "nothing not committed; no bundle: its origin holds every commit it needs)" in out


def test_a_commit_only_another_remote_has_goes_in_the_bundle_since_the_workspace_fetches_from_origin(tmp_path, ade,
                                                                                                    home, capsys):
    """The machine's clone has a second remote, the repository origin is a fork of, ahead of origin, and the session's
    HEAD is that remote's newer commit, with nothing not committed: what origin lacks goes in the bundle, that commit
    with it, built on origin's own; a clone of origin alone, as the workspace's is, takes it."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, remote = a_repository(tmp_path, home)
    upstream, ahead = tmp_path / "upstream.git", tmp_path / "ahead"
    git("clone", "-q", "--bare", str(remote), str(upstream))
    git("clone", "-q", str(upstream), str(ahead))
    (ahead / "later.txt").write_text("on upstream, not on origin\n")
    git("add", "later.txt", cwd=ahead)
    git("commit", "-qm", "later", cwd=ahead)
    git("push", "-q", "origin", "HEAD:main", cwd=ahead)
    git("remote", "add", "upstream", str(upstream), cwd=work)
    git("fetch", "-q", "upstream", cwd=work)
    git("checkout", "-q", "--detach", "upstream/main", cwd=work)
    later = git("rev-parse", "HEAD", cwd=work)
    a_session(home, "s-up", work)
    listed(d)
    seq = a_move(ade, d, device, "s-up")
    code, out, err = run(capsys, "--dir", d, "move")
    assert code == 0, (out, err)
    done, bundle = ade.got[seq]["done"], ade.got[seq].get("bundle")
    assert (done["head"], done["changes"], done["bundle"]) == (later, None, True) and bundle
    header = bundle.split(b"\n\n", 1)[0].decode().splitlines()
    assert [x[1:41] for x in header if x.startswith("-")] == [git("rev-parse", "origin/main", cwd=work)]
    ws = tmp_path / "ws"
    git("clone", "-q", str(remote), str(ws))
    (tmp_path / "move.bundle").write_bytes(bundle)
    git("fetch", "-q", str(tmp_path / "move.bundle"), "HEAD", cwd=ws)
    assert git("rev-parse", "FETCH_HEAD", cwd=ws) == later


def test_the_origin_address_goes_as_host_and_path_and_the_top_as_the_transcript_writes_it(tmp_path, ade, home,
                                                                                         capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home, where="real/app")
    os.symlink(home / "real", home / "work")                            # the session's folder reached through a link
    git("remote", "set-url", "origin", "https://ghp_notarealtoken0000@github.com/owner/app.git", cwd=work)
    (work / "docs").mkdir()
    a_session(home, "s-linked", home / "work" / "app" / "docs")
    listed(d)
    seq = a_move(ade, d, device, "s-linked")
    code, out, err = run(capsys, "--dir", d, "move")
    assert code == 0, (out, err)
    done = ade.got[seq]["done"]
    assert done["remote"] == "https://github.com/owner/app.git"
    assert done["cwd"] == str(home / "work" / "app")                    # not the real folder git gives
    git("remote", "set-url", "origin", "git@github.com:owner/app.git", cwd=work)
    again = a_move(ade, d, device, "s-linked")
    code, out, err = run(capsys, "--dir", d, "move")
    assert code == 0, (out, err, ade.requests[-8:])
    assert ade.got[again]["done"]["remote"] == "github.com:owner/app.git"


def test_an_origin_address_goes_as_its_host_and_path_alone_naming_the_same_repository():
    shown = ade_link._remote_shown
    cases = {
        "https://x-access-token:s3cret@github.com/o/r.git": "https://github.com/o/r.git",
        "https://only-a-token@github.com/o/r": "https://github.com/o/r",               # a token as a user name
        "HTTP://u:p@host:8080/o/r": "http://host:8080/o/r",
        "ssh://git:pw@github.com:22/o/r.git": "ssh://github.com:22/o/r.git",
        "ssh://git@github.com/o/r.git": "ssh://github.com/o/r.git",
        "git@github.com:o/r.git": "github.com:o/r.git",                                # git's own ssh form
        "github.com:o/r.git": "github.com:o/r.git",
        "  https://github.com/o/r.git\n": "https://github.com/o/r.git",
        "file:///srv/git/r.git": "file:///srv/git/r.git",
        "/srv/git/r.git": "/srv/git/r.git",
        "/home/a@b/r.git": "/home/a@b/r.git",                                          # a path, though it holds @
    }
    for given, sent in cases.items():
        assert shown(given) == sent, given
        assert ade_link._identity(sent) == ade_link._identity(given), given            # what the workspace compares
    # an @ inside the password, and a query, which can carry a token too: neither goes
    assert shown("https://u:p@ss@host/o/r?private_token=x#top") == "https://host/o/r"


def test_the_top_is_written_as_the_session_s_folder_writes_it(tmp_path):
    real = tmp_path / "real" / "app"
    (real / "a" / "b").mkdir(parents=True)
    os.symlink(tmp_path / "real", tmp_path / "via")
    top = os.path.realpath(real)
    assert ade_link._top_as_written(str(tmp_path / "via" / "app" / "a" / "b"), top) == str(tmp_path / "via" / "app")
    assert ade_link._top_as_written(str(tmp_path / "via" / "app"), top) == str(tmp_path / "via" / "app")
    assert ade_link._top_as_written(str(real / "a"), top) == str(real)
    assert ade_link._top_as_written(str(tmp_path / "elsewhere"), top) == top            # not inside it: git's own


# ---- what does not go -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("case, words", [
    ("no rule", "this machine's link has no history.json, so it lists no session"),
    ("excepted", "history.json does not list it"),
    ("outside", "history.json does not list it"),
    ("withheld", "it is withheld: it, or a session whose entries it holds, touched a project history.json excepts"),
    ("not read", "the History list has not read it whole yet"),
    ("running", "it is running on this machine: end it there, then move it again"),
    ("no transcript", "no transcript of it is on this machine"),
])
def test_a_session_that_may_not_go_is_failed_with_the_reason_and_nothing_else_is_sent(tmp_path, ade, home, capsys,
                                                                                    case, words):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    (home / "acme-plans").mkdir()
    (home / "elsewhere").mkdir()
    extra = []
    if case == "withheld":
        extra = [{"type": "assistant", "uuid": "t1", "cwd": str(work), "sessionId": "s-1",
                  "timestamp": "2026-10-08T10:02:00Z",
                  "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read",
                                           "input": {"file_path": str(home / "acme-plans" / "plan.md")}}]}}]
    a_session(home, "s-1" if case != "no transcript" else "s-other", work, extra)
    rule = {"excepted": {"except": ["~/work"]}, "outside": {"projects": ["~/elsewhere"]}}.get(case, {})
    listed(d, {"projects": "all", "except": ["~/acme-plans"], **rule})
    if case == "no rule":
        (d / "history.json").unlink()
    elif case == "not read":
        (d / "history_cache.json").unlink()
    elif case == "running":
        (home / ".claude" / "sessions").mkdir()
        (home / ".claude" / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(),
                                                                                       "sessionId": "s-1"}))
    seq = a_move(ade, d, device, "s-1")
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 2, out
    assert ade.asked("POST", "/take") and ade.asked("POST", f"/moves/{seq}/failed")
    assert not [r for r in ade.requests if r[1] in (f"/moves/{seq}/transcript", f"/moves/{seq}/bundle",
                                                    f"/moves/{seq}/done")]
    reason = ade.got[seq]["failed"]["reason"]
    assert words in reason and len(reason) <= 300 and "\n" not in reason
    assert out == f"ADE: move [{seq}] of session s-1: not sent: {reason}; the ADE was told\n"
    assert ade_link._moves(ade_link.Link(str(d)))[str(seq)]["state"] == "failed"


def test_a_session_written_to_while_it_is_judged_is_failed(tmp_path, ade, home, capsys, monkeypatch):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    transcript = a_session(home, "s-busy", work)
    listed(d)
    judged = ade_link._judged

    def resumed_meanwhile(link, path, sid):
        first = judged(link, path, sid)
        with open(transcript, "a") as fh:
            fh.write(json.dumps({"type": "user", "uuid": "u9", "cwd": str(work), "message": {"content": "more"}})
                     + "\n")
        return first
    monkeypatch.setattr(ade_link, "_judged", resumed_meanwhile)
    seq = a_move(ade, d, device, "s-busy")
    assert run(capsys, "--dir", d, "move")[0] == 2
    assert "it was written to while it was being moved" in ade.got[seq]["failed"]["reason"]
    assert not ade.asked("PUT", f"/moves/{seq}/transcript")


@pytest.mark.parametrize("limit, words", [(100, "its transcript is larger than"), (3000, "as a bundle")])
def test_a_transcript_or_a_bundle_past_the_limit_is_failed(tmp_path, ade, home, capsys, monkeypatch, limit, words):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    (work / "data.bin").write_bytes(os.urandom(8000))                  # what the remotes lack, past 3,000 bytes
    git("add", "data.bin", cwd=work)
    git("commit", "-qm", "data", cwd=work)
    a_session(home, "s-big", work)
    listed(d)
    monkeypatch.setattr(ade_link, "MOVE_MAX", limit)
    seq = a_move(ade, d, device, "s-big")
    assert run(capsys, "--dir", d, "move")[0] == 2
    assert words in ade.got[seq]["failed"]["reason"]
    assert not ade.asked("PUT", f"/moves/{seq}/transcript")


def test_the_ade_s_refusal_of_an_upload_is_told_as_the_move_s_failure(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    ade.refuse[("PUT", "/moves/{seq}/transcript")] = [(413, "A transcript is at most 32 MiB.")]
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 2
    assert ade.got[seq]["failed"]["reason"] == "the ADE refused its transcript (413): A transcript is at most 32 MiB."
    assert not ade.asked("POST", f"/moves/{seq}/done")


def test_a_move_the_ade_did_not_answer_is_carried_on_at_the_next_look_without_a_second_take(tmp_path, ade, home,
                                                                                           capsys, monkeypatch):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    with_work_not_committed(work)
    a_session(home, "s-1", work)
    listed(d)
    waits = []
    monkeypatch.setattr(ade_link, "sleep", waits.append)
    seq = a_move(ade, d, device, "s-1")
    ade.refuse[("PUT", "/moves/{seq}/transcript")] = [(503, "busy")] * 4
    code, out, err = run(capsys, "--dir", d, "move")
    assert code == 3 and waits == [10, 20, 30] and "carried on at the next look" in err
    assert ade.moves[seq]["state"] == "taken" and not ade.asked("POST", f"/moves/{seq}/failed")
    assert ade_link._moves(ade_link.Link(str(d)))[str(seq)]["state"] == "taken"
    ade.requests.clear()
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 0 and "sent to the ADE" in out
    assert not ade.asked("POST", "/take") and ade.got[seq]["done"]["bundle"] is True


def test_a_move_taken_by_a_run_cut_short_before_it_noted_the_take_is_carried_on(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    link = ade_link.Link(str(d))
    assert ade_link._find_moves(link) == [seq]
    # take's records hold the take; the run ended before moves.json said so
    assert ade_link._claim(link, seq, ade_link.MOVE_KINDS, lambda text, err=False: None)[0] == 0
    assert ade_link._moves(link)[str(seq)]["state"] == "found" and ade.moves[seq]["state"] == "taken"
    ade.requests.clear()
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 0 and "sent to the ADE" in out
    assert not ade.asked("POST", "/take") and ade.got[seq]["done"]["session"] == "s-1"


def test_a_done_the_ade_took_whose_answer_was_lost_is_told_as_sent_not_failed(tmp_path, ade, home, capsys,
                                                                             monkeypatch):
    """The ADE took the done and its answer was lost on the way: asked again, it refuses, saying the move is uploaded,
    which only this link's done makes it, and the move is told and kept as sent. So at the next look, after a run that
    gave up asking: what that one sends again is refused the same way. A move the ADE ended otherwise meanwhile is not
    taken for sent."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    sent = "sent to the ADE, which has it (uploaded) from this link's earlier done\n"
    seq = a_move(ade, d, device, "s-1")
    ade.lost[("POST", "/moves/{seq}/done")] = 1
    assert run(capsys, "--dir", d, "move")[:2] == (0, f"ADE: move [{seq}] of session s-1: {sent}")
    assert ade.moves[seq]["state"] == "uploaded" and len(ade.asked("POST", f"/moves/{seq}/done")) == 2
    assert ade_link._moves(ade_link.Link(str(d)))[str(seq)]["state"] == "done"
    again = a_move(ade, d, device, "s-1")
    ade.lost[("POST", "/moves/{seq}/done")] = 4                      # every answer of four tries lost: it gives up
    assert run(capsys, "--dir", d, "move")[0] == 3 and ade.moves[again]["state"] == "uploaded"
    assert run(capsys, "--dir", d, "move")[:2] == (0, f"ADE: move [{again}] of session s-1: {sent}")
    assert len(ade.asked("POST", "/take")) == 2                       # carried on at the next look, not taken again
    assert ade_link._moves(ade_link.Link(str(d)))[str(again)]["state"] == "done"
    ended = a_move(ade, d, device, "s-1")
    judged = ade_link._judged

    def expired_meanwhile(link, path, sid):                           # as the app's sweep ends one a day after its take
        ade.moves[ended]["state"] = "expired"
        return judged(link, path, sid)
    monkeypatch.setattr(ade_link, "_judged", expired_meanwhile)
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 2 and f"move [{ended}] of session s-1: not sent: " in out and "did not take that (409" in out
    assert ade_link._moves(ade_link.Link(str(d)))[str(ended)]["state"] == "failed"


# ---- verified, or not taken ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("case, why", [
    ("unpinned", "unpinned"), ("another link", "to"), ("text added", "fields"), ("session ..", "fields"),
    ("session -x", "fields"), ("session long", "fields"), ("an hour and a second", "life"), ("expired", "expired"),
    ("an instruction", "purpose"), ("another account", "account"), ("another deployment", "deployment"),
])
def test_a_move_that_does_not_verify_is_not_taken(tmp_path, ade, home, capsys, case, why):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    signer = Authenticator(rp="127.0.0.1", origin=ade.url, alg=-8) if case == "unpinned" else device
    over = {"another link": {"to": uuid.uuid4().hex}, "text added": {"text": "go"}, "session ..": {"session": ".."},
            "session -x": {"session": "-x"}, "session long": {"session": "s" * 101},
            "an hour and a second": {"life": 3601}, "expired": {"created": int(time.time()) - 7200},
            "an instruction": {"purpose": "instruction", "session": DROP, "text": "go"},
            "another account": {"account": "acct-someone-else"},
            "another deployment": {"deployment": "ade.example.test"}}.get(case, {})
    seq = a_move(ade, d, signer, over.pop("session", "s-1"), **over)
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 7 and out == f"ADE: not taken: [{seq}] {ade_link.NotVerified(why)}\n"
    assert not ade.asked("POST", "/take") and ade.moves[seq]["state"] == "asked"
    assert ade_link._moves(ade_link.Link(str(d)))[str(seq)]["state"] == "gone"
    assert run(capsys, "--dir", d, "move")[1] == "ADE: no move for this link is waiting\n"     # not tried again


def test_a_move_from_anyone_but_the_owner_is_no_move(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    a_move(ade, d, device, "s-1", sender="cloud")                      # another session's event of kind move
    a_move(ade, d, device, "s-1", kind="note")                         # the owner's, of another kind
    assert run(capsys, "--dir", d, "move")[:2] == (0, "ADE: no move for this link is waiting\n")
    assert ade.requests == [("GET", "/feed")]


def test_a_move_signed_for_an_hour_is_taken():
    keys = {"account": ACCOUNT}
    link = type("L", (), {"host": lambda self: "ade.example.test", "link_id": "a" * 32})()
    p = {"v": 2, "deployment": "ade.example.test", "purpose": "move", "account": ACCOUNT, "nonce": "n",
         "id": "b" * 32, "to": "a" * 32, "session": "S-1_x", "created_at": 1000, "expires_at": 4600}
    assert ade_link._payload(link, keys, canonicalize(p).decode(), ade_link.MOVE_KINDS, 1000)[0] == p
    for session in ("", "a b", "s/1", "s" * 101, 7):
        with pytest.raises(ade_link.NotVerified) as e:
            ade_link._payload(link, keys, canonicalize(dict(p, session=session)).decode(), ade_link.MOVE_KINDS, 1000)
        assert e.value.why == "fields", session


def test_a_move_found_expired_is_not_done(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    # ended a minute ago: within the grace this machine's clock is given, past the ADE's, which refuses the take
    seq = a_move(ade, d, device, "s-1", created=int(time.time()) - 660, life=600)
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 2 and out == f"ADE: not taken: [{seq}] expired\n"
    assert not [r for r in ade.requests if r[1].startswith("/moves/")]
    assert ade_link._moves(ade_link.Link(str(d)))[str(seq)]["state"] == "gone"
    # one found long ago, which no move's life could reach now, is let go without asking the ADE for it
    late = a_move(ade, d, device, "s-1")
    (d / "moves.json").write_text(json.dumps({str(late): {"state": "found", "at": int(time.time()) - 7200}}))
    (d / "moves_seq").write_text(str(late))
    ade.requests.clear()
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 2 and out == f"ADE: move [{late}]: not taken: its life has ended\n"
    assert ade.requests == [("GET", "/feed")]


def test_a_move_whose_take_the_ade_answered_after_its_life_is_told_failed_not_sent(tmp_path, ade, home, capsys,
                                                                                    monkeypatch):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    claim = ade_link._claim

    def answered_late(link, seq, kinds, say):                           # the take's answer came two hours on
        got = claim(link, seq, kinds, say)
        return got[:5] + (got[5] + 7200,) if got[0] == 0 else got
    monkeypatch.setattr(ade_link, "_claim", answered_late)
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 2 and ade.got[seq]["failed"]["reason"] == "its life ended before the ADE answered its take"
    assert not ade.asked("PUT", f"/moves/{seq}/transcript")


def test_the_transcript_sent_is_the_copy_written_last_never_a_link(tmp_path, home):
    projects = home / ".claude" / "projects"
    for folder, at in (("-a", 1_000), ("-b", 2_000)):
        (projects / folder).mkdir()
        (projects / folder / "s-1.jsonl").write_text(folder)
        os.utime(projects / folder / "s-1.jsonl", (at, at))
    (projects / "-c").mkdir()
    os.symlink(projects / "-a" / "s-1.jsonl", projects / "-c" / "s-1.jsonl")      # newer, but a link
    os.utime(projects / "-c" / "s-1.jsonl", (3_000, 3_000), follow_symlinks=False)
    assert ade_link._transcript_of("s-1") == str(projects / "-b" / "s-1.jsonl")
    with pytest.raises(ade_link.MoveFailed, match="no transcript of it is on this machine"):
        ade_link._transcript_of("s-2")


def test_a_look_reads_the_feed_on_past_one_page(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    for seq in range(1, ade_link.FEED_LIMIT + 6):                       # more than one page of other events first
        ade.events.append({"seq": seq, "time": "2026-10-08T00:00:00Z", "kind": "note", "from": "cloud", "to": "mac",
                           "ref": None, "text": "n", "needs_turn": False, "closed_by": None})
    seq = a_move(ade, d, device, "s-other")                             # no transcript of it here
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 2 and ade.asked("POST", "/take") and len(ade.asked("GET", "/feed")) == 2
    assert ade.got[seq]["failed"]["reason"] == "no transcript of it is on this machine"
    assert (d / "moves_seq").read_text() == str(seq)


# ---- take, show and next leave a move to move ------------------------------------------------------------------------

def test_take_and_show_refuse_a_move_and_next_names_it_without_its_words(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    seq = a_move(ade, d, device, "s-1")
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 7 and out == (f"ADE: not taken: [{seq}] it is a move of a session to the hosted workspace: "
                                 "ade_link.py move does it, not take\n")
    code, out, _ = run(capsys, "--dir", d, "show", seq)
    assert code == 7 and f"NOT VERIFIED: [{seq}] it is a move of a session" in out
    assert not ade.asked("POST", "/take")
    event = dict(ade.events[0], text="pretend words")
    assert ade_link.line(event).endswith(
        "owner>mac: (a move of a session to the hosted workspace: this machine's link does it, ade_link.py move)")


# ---- when a move runs ------------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_the_watcher_starts_a_move_run_on_a_move_s_event_and_wakes_no_one(tmp_path, ade, home, capsys,
                                                                         monkeypatch):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    seq = a_move(ade, d, device, "s-1")
    clock = Clock()
    monkeypatch.setattr(ade_link, "clock", clock)
    monkeypatch.setattr(ade_link, "sleep", clock.sleep)
    started, posted = [], []
    monkeypatch.setattr(ade_link, "_start_move", lambda link: started.append(link.dir))
    monkeypatch.setattr(ade_link, "post_to_inbox", posted.append)
    code, out, _ = run(capsys, "--dir", d, "watch", "--max", 90)
    assert code == 0 and out == "" and posted == []                     # nothing for the session's turn
    assert started == [str(d)]                                          # once, though the watcher polled three times
    assert (d / "announced_seq").read_text() == str(seq) and not ade.asked("POST", "/take")


def test_a_history_run_looks_for_a_move_and_starts_a_run_only_when_one_is_to_do(tmp_path, ade, home, capsys,
                                                                                monkeypatch):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    (d / "history.json").write_text(json.dumps({"projects": "all"}))
    started = []
    monkeypatch.setattr(ade_link, "_start_move", lambda link: started.append(link.dir))
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert started == [] and ade.asked("GET", "/feed")                  # looked: nothing to do
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "history", "--quiet")[0] == 0
    assert started == [str(d)] and not ade.asked("POST", "/take")       # the move is the run's, in its own process
    assert ade_link._moves(ade_link.Link(str(d)))[str(seq)]["state"] == "found"
    held = ade_link._try_lock(ade_link.Link(str(d)), "move.lock")       # a move run under way
    try:
        assert run(capsys, "--dir", d, "history", "--quiet")[0] == 0
    finally:
        os.close(held)
    assert started == [str(d)] * 2                                      # one more, to wait for it and look after it


def test_move_by_hand_waits_for_a_run_under_way_and_says_what_it_did(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    link = ade_link.Link(str(d))
    held = ade_link._try_lock(link, "move.lock")                        # the run under way: this test, below
    done = []
    waiting = threading.Thread(target=lambda: done.append(ade_link.main(["--dir", str(d), "move"])))
    waiting.start()
    seen, deadline = "", time.time() + 20
    while "this one waits for it" not in seen:
        assert time.time() < deadline and waiting.is_alive()
        time.sleep(0.05)
        seen += capsys.readouterr().out
    for todo in ade_link._find_moves(link):                             # the run under way does the move
        assert ade_link._do_move(link, todo, lambda text, err=False: None) == 0
    os.close(held)
    waiting.join(30)
    lines = (seen + capsys.readouterr().out).splitlines()
    assert done == [0] and lines[0] == ("ADE: a move run from this link's directory is under way: this one waits "
                                        "for it")
    assert len(lines) == 2 and lines[1].startswith(f"ADE: move [{seq}] of session s-1: sent to the ADE: ")
    assert len(ade.asked("POST", "/take")) == 1 and len(ade.asked("POST", f"/moves/{seq}/done")) == 1


def test_a_quiet_move_run_in_a_process_of_its_own_prints_nothing_and_notes_what_it_did(tmp_path, ade, home):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    with_work_not_committed(work)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    quiet = subprocess.run([sys.executable, SCRIPT, "--dir", str(d), "move", "--quiet"], capture_output=True,
                           timeout=120, cwd=str(tmp_path))
    assert (quiet.returncode, quiet.stdout, quiet.stderr) == (0, b"", b"")
    assert ade.got[seq]["done"]["session"] == "s-1" and ade.got[seq]["bundle"]
    log = (d / "move.log").read_text()
    assert re.search(rf"^[0-9]+ ADE: move \[{seq}\] of session s-1: sent to the ADE: ", log, re.M)


def test_a_move_run_starts_in_a_process_of_its_own(tmp_path, monkeypatch):
    got = {}

    def popen(argv, **kw):
        got.update(argv=argv, **kw)
    monkeypatch.setattr(subprocess, "Popen", popen)
    d = tmp_path / "mac"
    d.mkdir()
    ade_link._start_move(type("L", (), {"dir": str(d)})())
    assert got["argv"] == [sys.executable, SCRIPT, "--dir", str(d), "move", "--quiet"]
    assert got["start_new_session"] is True and got["stdin"] == got["stdout"] == got["stderr"] == subprocess.DEVNULL

    def broken(argv, **kw):
        raise OSError("no such interpreter")
    monkeypatch.setattr(subprocess, "Popen", broken)
    ade_link._start_move(type("L", (), {"dir": str(d)})())               # never raises: noted
    assert "start: OSError: no such interpreter" in (d / "move.log").read_text()


def test_a_failure_s_reason_is_one_line_of_at_most_300_characters_with_nothing_shaped_like_a_secret():
    assert ade_link._reason("git said:\n  fatal: no\tsuch\u200b thing\r\n") == "git said: fatal: no such thing"
    long = ade_link._reason("x" * 400)
    assert len(long) == 300 and long.endswith("x ...")
    assert ade_link._reason("remote https://ghp_abc@github.com said no") == (
        "[left out: the reason held something shaped like a secret, which the ADE does not keep]")


# ---- the move: the repository held to the rule whole, its configuration running nothing ----------------------------

def refused_only(ade, seq, words):
    """The move was told failed, for a reason holding `words`, and nothing of it was sent."""
    reason = ade.got[seq]["failed"]["reason"]
    assert words in reason, reason
    assert not [r for r in ade.requests if r[1] in (f"/moves/{seq}/transcript", f"/moves/{seq}/bundle",
                                                    f"/moves/{seq}/done")]


@pytest.mark.parametrize("case, words", [
    ("an excepted folder inside it", "its repository holds a folder history.json excepts"),
    ("its top outside the projects named", "its repository's top is not a folder history.json lists"),
])
def test_the_whole_repository_is_held_to_history_json(tmp_path, ade, home, capsys, case, words):
    """What a move sends of a repository is everything it holds, not only what the session touched: a session in a
    listed folder of a larger repository does not carry the rest of it."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home, where="mono")
    (work / "app").mkdir()
    (work / "acme-secret").mkdir()
    (work / "acme-secret" / "plan.md").write_text("the plan\n")         # not committed: git add -A would take it
    a_session(home, "s-1", work / "app")
    listed(d, {"projects": "all", "except": ["~/mono/acme-secret"]} if case == "an excepted folder inside it"
           else {"projects": ["~/mono/app"]})
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 2
    refused_only(ade, seq, words)


@pytest.mark.parametrize("case", ["a home folder kept in git", "Claude Code's folder", "the link's own directory",
                                  "another link's directory"])
def test_a_repository_holding_claude_code_s_folder_or_a_link_s_is_refused(tmp_path, ade, home, capsys, monkeypatch,
                                                                         case):
    """A home folder kept in git (dotfiles, files not ignored) holds every transcript and the links' keys: a session
    worked in a folder of it, not a repository of its own, so its top is the home folder."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(remote))
    projects, where = None, None
    if case == "a home folder kept in git":                             # every transcript and the link's key in it
        top, where = home, home / ".proofpane-ade" / "mac"
    elif case == "Claude Code's folder":                                # kept in a project: its transcripts alone
        top = home / "work" / "app"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(top / ".claude-here"))
        projects = top / ".claude-here" / "projects"
    else:
        config = tmp_path / "claude-config"                             # Claude Code's own folder, outside it
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
        projects = config / "projects"
        if case == "the link's own directory":
            top, where = home / "work" / "app", home / "work" / "app" / ".ade-link"
        else:
            top = home
            (home / ".proofpane-ade" / "other").mkdir(parents=True)
            (home / ".proofpane-ade" / "other" / "key").write_text("another link's key\n")
    d = a_link(tmp_path, ade, device, where=where)
    top.mkdir(parents=True, exist_ok=True)
    git("init", "-q", str(top))
    git("remote", "add", "origin", str(remote), cwd=top)
    (top / "dotfile").write_text("set -o vi\n")
    git("add", "dotfile", cwd=top)
    git("commit", "-qm", "dotfiles", cwd=top)
    git("push", "-q", "origin", "HEAD:main", cwd=top)
    git("fetch", "-q", "origin", cwd=top)
    (home / "work" / "app").mkdir(parents=True, exist_ok=True)
    a_session(home, "s-1", home / "work" / "app", projects=projects)
    a_session(home, "s-another", home / "elsewhere", projects=projects)   # another session's transcript
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 2
    refused_only(ade, seq, "its repository holds Claude Code's own folder or this machine's link")


def test_a_session_whose_first_folder_is_gone_is_refused_no_folder_above_taken_in_its_place(tmp_path, ade, home,
                                                                                          capsys):
    """A folder above the one a session worked in first may be in another repository (a home folder kept in git)."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work / "since-removed")
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 2
    refused_only(ade, seq, "the folder it worked in first is not on this machine any more")


def test_a_repository_whose_work_tree_is_elsewhere_than_the_session_s_folder_is_refused(tmp_path, ade, home,
                                                                                         capsys):
    """core.worktree has git name another folder as the top of the repository the session's folder is in."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    (home / "other").mkdir()
    git("config", "core.worktree", str(home / "other"), cwd=work)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 2
    refused_only(ade, seq, "the folder it worked in first is not inside its repository")


@pytest.mark.parametrize("case", ["touched an excepted project", "withheld", "told it holds"])
def test_a_repository_a_held_session_worked_in_is_refused(tmp_path, ade, home, capsys, case):
    """A session this link withholds may have left what it read in the repository's files."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    (home / "acme-plans").mkdir()
    read = [{"type": "assistant", "uuid": "t-other", "cwd": str(work), "sessionId": "s-other",
             "timestamp": "2026-10-08T10:02:00Z",
             "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read",
                                      "input": {"file_path": str(home / "acme-plans" / "plan.md")}}]}}]
    a_session(home, "s-1", work)
    a_session(home, "s-other", work, read if case == "touched an excepted project" else ())
    listed(d, {"projects": "all", "except": ["~/acme-plans"]})
    if case == "touched an excepted project":                           # the kept reading alone holds it
        (d / "history_withheld.json").unlink()
    elif case == "withheld":
        (d / "history_withheld.json").write_text(json.dumps(["s-other"]))
    elif case == "told it holds":
        (d / "mirror_told.json").write_text(json.dumps({"s-other": int(time.time())}))
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 2
    refused_only(ade, seq, "a session this link withholds, or told it holds, worked in its repository")


def test_the_repository_s_own_configuration_runs_no_program_and_writes_nothing_in_it(tmp_path, ade, home, capsys):
    """A filesystem monitor, a filter's clean and process programs and a hook, each set in the repository's own
    configuration, write a marker if they run; a split index would write its shared part into the git directory."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, remote = a_repository(tmp_path, home)
    git("config", "core.splitIndex", "true", cwd=work)
    git("update-index", "--split-index", cwd=work)
    marker, script, hooks = tmp_path / "MARKER", tmp_path / "mark.sh", tmp_path / "hooks"
    script.write_text(f"#!/bin/sh\necho \"$0 $*\" >> '{marker}'\n")
    script.chmod(0o755)
    hooks.mkdir()
    (hooks / "post-index-change").write_text(f"#!/bin/sh\necho post-index-change >> '{marker}'\n")
    (hooks / "post-index-change").chmod(0o755)
    for key, value in (("core.fsmonitor", str(script)), ("filter.evil.clean", f"{script} clean"),
                       ("filter.evil.process", f"{script} process"), ("filter.evil.required", "true"),
                       ("core.hooksPath", str(hooks))):
        git("config", key, value, cwd=work)
    (work / ".gitattributes").write_text("* filter=evil\n")             # not committed: the move adds it
    (work / "notes.txt").write_text("notes\nchanged, as it is\n")
    for n in range(3):                                                   # enough new files for a new shared index
        (work / f"new-{n}.txt").write_text(f"new {n}\n")
    a_session(home, "s-1", work)
    listed(d)
    before = as_left(work)
    seq = a_move(ade, d, device, "s-1")
    code, out, err = run(capsys, "--dir", d, "move")
    assert not marker.exists(), marker.read_text()
    assert code == 0, (out, err)
    assert as_left(work) == before                                       # no shared index written into .git
    ws = tmp_path / "ws"                                                 # the work as it is, no filter between
    git("clone", "-q", str(remote), str(ws))
    (tmp_path / "move.bundle").write_bytes(ade.got[seq]["bundle"])
    git("fetch", "-q", str(tmp_path / "move.bundle"), "HEAD", cwd=ws)
    changes = ade.got[seq]["done"]["changes"]
    assert git("show", f"{changes}:notes.txt", cwd=ws) == "notes\nchanged, as it is"


def test_the_bundle_leaves_out_only_what_origin_has_not_another_remote(tmp_path, ade, home, capsys):
    """A fork's work on its upstream's commits: origin, whose address goes, lacks them, so the bundle carries them."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, remote = a_repository(tmp_path, home)
    base = git("rev-parse", "HEAD", cwd=work)
    upstream, up = tmp_path / "upstream.git", tmp_path / "up-work"
    git("init", "-q", "--bare", str(upstream))
    git("clone", "-q", str(remote), str(up))
    (up / "upstream.txt").write_text("from upstream\n")
    git("add", "upstream.txt", cwd=up)
    git("commit", "-qm", "upstream's", cwd=up)
    theirs = git("rev-parse", "HEAD", cwd=up)
    git("push", "-q", str(upstream), "HEAD:main", cwd=up)
    git("remote", "add", "upstream", str(upstream), cwd=work)
    git("fetch", "-q", "upstream", cwd=work)
    git("checkout", "-q", "-b", "feature", "upstream/main", cwd=work)
    (work / "mine.txt").write_text("mine\n")
    git("add", "mine.txt", cwd=work)
    git("commit", "-qm", "mine", cwd=work)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 0
    bundle = ade.got[seq]["bundle"]
    header = bundle.split(b"\n\n", 1)[0].decode().splitlines()
    assert [x[1:41] for x in header if x.startswith("-")] == [base]     # only what origin has is left out
    ws = tmp_path / "ws"                                                 # a clone of origin alone is enough
    git("clone", "-q", str(remote), str(ws))
    (tmp_path / "move.bundle").write_bytes(bundle)
    git("bundle", "verify", str(tmp_path / "move.bundle"), cwd=ws)
    git("fetch", "-q", str(tmp_path / "move.bundle"), "HEAD", cwd=ws)
    assert git("cat-file", "-t", theirs, cwd=ws) == "commit"


def test_work_not_committed_past_the_limit_is_refused_before_anything_is_added(tmp_path, ade, home, capsys,
                                                                              monkeypatch):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    (work / "big.dat").write_bytes(os.urandom(5000))
    a_session(home, "s-1", work)
    listed(d)
    monkeypatch.setattr(ade_link, "MOVE_MAX", 4000)
    ran, real = [], ade_link._git
    monkeypatch.setattr(ade_link, "_git", lambda args, *a, **k: ran.append(list(args)) or real(args, *a, **k))
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 2
    refused_only(ade, seq, "the work not committed is more than")
    assert ran and not [args for args in ran if "add" in args]          # refused before git add read a file


def test_an_untracked_cache_in_the_index_is_not_trusted_to_say_what_git_add_will_read(tmp_path, ade, home, capsys,
                                                                                    monkeypatch):
    """The repository's own configuration keeps an untracked cache in its index, lists every untracked file and does
    not trust a file's change time. A folder's untracked file is replaced by a larger one of a name as long, and the
    folder's time set back as it was: the cache, trusted, lists the file gone and not the one in its place, which git
    add reads all the same. The move counts what git add will read, so the larger file past the limit is refused."""
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    for key, value in (("core.untrackedCache", "true"), ("status.showUntrackedFiles", "all"),
                       ("core.trustctime", "false")):
        git("config", key, value, cwd=work)
    drafts = work / "drafts"
    drafts.mkdir()
    (drafts / "small").write_text("small\n")
    past = os.stat(drafts).st_mtime_ns - 100 * 10**9                    # older than the index, so never racy
    for folder in (work, drafts):
        os.utime(folder, ns=(past, past))
    for _ in range(2):                                                   # the cache made, its folders read whole
        subprocess.run(["git", "status", "--porcelain"], cwd=work, check=True, capture_output=True)
    assert b"UNTR" in (work / ".git" / "index").read_bytes()
    size = os.stat(drafts).st_size
    (drafts / "small").unlink()
    (drafts / "large").write_bytes(os.urandom(5000))
    os.utime(drafts, ns=(past, past))
    assert os.stat(drafts).st_size == size
    a_session(home, "s-1", work)
    listed(d)
    # as git reads it by the repository's own configuration: the cache is trusted, and wrong
    assert git("status", "--porcelain", cwd=work).splitlines() == ["?? drafts/small"]
    monkeypatch.setattr(ade_link, "MOVE_MAX", 4000)
    ran, real = [], ade_link._git
    monkeypatch.setattr(ade_link, "_git", lambda args, *a, **k: ran.append(list(args)) or real(args, *a, **k))
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 2
    refused_only(ade, seq, "the work not committed is more than")
    assert ran and not [args for args in ran if "add" in args]


def test_git_s_output_is_read_with_a_cap_and_git_stopped_past_it(tmp_path, home, monkeypatch):
    repo = tmp_path / "r"
    git("init", "-q", str(repo))
    data = os.urandom(300_000)
    (repo / "big").write_bytes(data)
    git("add", "big", cwd=repo)
    git("commit", "-qm", "big", cwd=repo)
    args = ["-C", str(repo), "cat-file", "blob", "HEAD:big"]
    assert ade_link._git_capped(args, "reading", len(data)) == data
    assert ade_link._git_capped(args, "reading", 1000) is None
    with pytest.raises(ade_link.MoveFailed, match="^reading: fatal"):
        ade_link._git_capped(["-C", str(repo), "cat-file", "blob", "HEAD:none"], "reading", 1000)
    # a git whose output never ends is stopped at the cap, long before its time is up
    endless = tmp_path / "endless"
    endless.mkdir()
    (endless / "git").write_text("#!/bin/sh\nexec yes moving\n")
    (endless / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{endless}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(ade_link, "GIT_TIMEOUT", 20)
    began = time.monotonic()
    assert ade_link._git_capped(["bundle", "create", "-"], "reading", 1 << 20) is None
    assert time.monotonic() - began < 10


def test_a_move_run_deletes_the_scratch_folders_a_run_cut_short_left(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    (d / "move-k2j3h4" / "store.git").mkdir(parents=True)
    (d / "move-k2j3h4" / "index").write_text("left by a run cut short")
    kept = tmp_path / "kept"
    kept.mkdir()
    (kept / "file").write_text("not the link's")
    os.symlink(kept, d / "move-elsewhere")                              # a link: never followed
    assert run(capsys, "--dir", d, "move")[:2] == (0, "ADE: no move for this link is waiting\n")
    assert not (d / "move-k2j3h4").exists()
    assert os.path.islink(d / "move-elsewhere") and (kept / "file").read_text() == "not the link's"


@pytest.mark.parametrize("case, words", [
    ("files in Git LFS", "keeps files in Git LFS, whose contents a move does not carry"),
    ("a submodule", "has submodules, which a move does not carry"),
    ("a repository held inside", "holds another repository that is not a submodule"),
    ("a shallow clone", "is a shallow clone"),
    ("a merge under way", "a merge, rebase, cherry-pick or revert is under way"),
    ("a rebase under way", "a merge, rebase, cherry-pick or revert is under way"),
    ("files left unmerged", "has files left unmerged"),
])
def test_what_a_move_cannot_carry_is_refused_with_its_reason(tmp_path, ade, home, capsys, case, words):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, remote = a_repository(tmp_path, home)
    head = git("rev-parse", "HEAD", cwd=work)
    if case == "files in Git LFS":
        (work / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        (work / "model.bin").write_bytes(b"weights")
    elif case == "a submodule":
        git("update-index", "--add", "--cacheinfo", f"160000,{head},vendored", cwd=work)
    elif case == "a repository held inside":
        git("init", "-q", str(work / "inner"))
        (work / "inner" / "x").write_text("x\n")
    elif case == "a shallow clone":
        (work / "second.txt").write_text("2\n")
        git("add", "second.txt", cwd=work)
        git("commit", "-qm", "second", cwd=work)
        git("push", "-q", "origin", "HEAD:main", cwd=work)
        work = home / "shallow"
        git("clone", "-q", "--depth", "1", f"file://{remote}", str(work))
    elif case == "a merge under way":
        (work / ".git" / "MERGE_HEAD").write_text(head + "\n")
    elif case == "a rebase under way":
        (work / ".git" / "rebase-merge").mkdir()
    else:
        blob = git("hash-object", "-w", "notes.txt", cwd=work)
        subprocess.run(["git", "update-index", "--index-info"], cwd=work, check=True, text=True,
                       input="".join(f"100644 {blob} {stage}\tclash.txt\n" for stage in (1, 2, 3)))
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 2
    refused_only(ade, seq, words)


def test_replace_refs_are_not_followed_so_what_is_sent_is_what_the_workspace_reads(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    (work / "second.txt").write_text("2\n")
    git("add", "second.txt", cwd=work)
    git("commit", "-qm", "second", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    git("fetch", "-q", "origin", cwd=work)
    git("replace", "HEAD", "HEAD~1", cwd=work)                           # HEAD read as its parent, but here
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    assert run(capsys, "--dir", d, "move")[0] == 0
    done = ade.got[seq]["done"]
    assert (done["changes"], done["bundle"]) == (None, False)            # the tree is HEAD's own, all on origin


# ---- a move this directory took, and one whose take's answer was lost ----------------------------------------------

def test_a_move_taken_is_carried_on_past_its_life_for_the_day_the_ade_keeps_a_take(tmp_path, ade, home, capsys,
                                                                                  monkeypatch):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    seq = a_move(ade, d, device, "s-1", life=600)
    ade.refuse[("PUT", "/moves/{seq}/transcript")] = [(503, "busy")] * 4
    assert run(capsys, "--dir", d, "move")[0] == 3                      # taken; the upload had no answer
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 7200)            # the machine slept past the move's life
    code, out, _ = run(capsys, "--dir", d, "move")
    assert code == 0 and "sent to the ADE" in out and ade.moves[seq]["state"] == "uploaded"


def test_a_move_taken_more_than_a_day_ago_is_told_failed(tmp_path, ade, home, capsys, monkeypatch):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    seq = a_move(ade, d, device, "s-1", life=600)
    ade.refuse[("PUT", "/moves/{seq}/transcript")] = [(503, "busy")] * 4
    assert run(capsys, "--dir", d, "move")[0] == 3
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 86400 + 60)
    tried = len(ade.asked("PUT", f"/moves/{seq}/transcript"))
    assert run(capsys, "--dir", d, "move")[0] == 2
    assert ade.got[seq]["failed"]["reason"] == "it was taken more than a day ago, and the ADE keeps a take no longer"
    assert len(ade.asked("PUT", f"/moves/{seq}/transcript")) == tried and not ade.asked("POST", f"/moves/{seq}/done")


# seconds after the take was asked for: within the look's window of a found move, and past it
@pytest.mark.parametrize("later", [3600, 5000])
def test_a_take_whose_answer_was_lost_is_told_failed_once_its_life_is_over(tmp_path, ade, home, capsys, monkeypatch,
                                                                          later):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1", life=600)
    ade.lost[("POST", "/take")] = 1
    assert run(capsys, "--dir", d, "move")[0] == 3
    assert ade.moves[seq]["state"] == "taken"                           # the ADE made the take; its answer was lost
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + later)           # its life is over
    assert run(capsys, "--dir", d, "move")[0] == 2
    assert ade.moves[seq]["state"] == "failed"                          # so the page does not show it taken a day
    refused_only(ade, seq, "its life ended after its take was asked for, before the ADE's answer came")
    assert run(capsys, "--dir", d, "move")[1] == "ADE: no move for this link is waiting\n"


def test_a_take_whose_answer_was_lost_is_asked_again_while_the_move_lives(tmp_path, ade, home, capsys):
    device = Authenticator(rp="127.0.0.1", origin=ade.url)
    d = a_link(tmp_path, ade, device)
    work, _ = a_repository(tmp_path, home)
    a_session(home, "s-1", work)
    listed(d)
    seq = a_move(ade, d, device, "s-1")
    ade.lost[("POST", "/take")] = 1
    assert run(capsys, "--dir", d, "move")[0] == 3
    code, out, _ = run(capsys, "--dir", d, "move")                      # the ADE gives the take again (retry)
    assert code == 0 and "sent to the ADE" in out and len(ade.asked("POST", "/take")) == 2
    assert ade.moves[seq]["state"] == "uploaded"
