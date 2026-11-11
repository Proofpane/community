"""A linked session's usage, for the Proofpane organisation its account is paired with: the
account's pairing in the app, its token kept sealed and never shown; each reply's usage sent by the link's Stop hook
after its notes, and forwarded by the app to its own account's pairing alone, once; nothing on for an account that is
not paired; a forward that fails tried a bounded number of times, then dropped and said, the link never waiting on
it. Through the app served on a local port, the link's own Stop hook (ade_link.py mirror reply) and Proofpane's
stand-in (tests/proofpane_stand_in.py), which keeps every request it is sent."""

import base64
import contextlib
import io
import json
import os
import sys
import time

import pytest

import ade_link
from conftest import signed_up
from proofpane_open import store
from proofpane_stand_in import (CODE, CODE_B, DEVICE, DEVICE_B, TOKEN, TOKEN_B, Proofpane, posted,  # noqa: F401
                                proofpane)
from test_ade_link_client import link_row, owner, pair, run, served  # noqa: F401  fixtures and helpers

KEY = base64.b64encode(bytes(range(32))).decode()
MODEL = "test-model-2026-11"
EVENT = "/api/v1/mcp/event"
NAME = "Proofpane Community at 127.0.0.1"           # the device's name: the served app's own host


@pytest.fixture(autouse=True)
def a_home_of_its_own(monkeypatch, own_home):
    # the Stop hook looks for a session's transcripts under $HOME/.claude/projects: the test's own home there
    monkeypatch.setenv("HOME", own_home)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture(autouse=True)
def keystore(monkeypatch):
    # the key the app seals a pairing's token with, as it seals a repository's
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)


@pytest.fixture(autouse=True)
def the_stand_in_named(monkeypatch, proofpane):
    # the deployment names the Proofpane its accounts may pair with: here the stand-in
    monkeypatch.setenv("OPEN_EDITION_PROOFPANE_ADDRESSES", proofpane)


def at(second):
    return f"2026-11-02T10:00:{second:02d}.000Z"


def _counts(i, o, r, c):
    return {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": r, "cache_creation_input_tokens": c}


def _typed(uid, text, second, session="s1", **extra):
    """A turn's first prompt, typed by a person, as Claude Code writes one."""
    return dict({"type": "user", "uuid": uid, "sessionId": session, "timestamp": at(second),
                 "origin": {"kind": "human"}, "turnOrigin": "human",
                 "message": {"role": "user", "content": [{"type": "text", "text": text}]}}, **extra)


def _reply(uid, request_id, block, usage, second, session="s1"):
    """One line of a model's reply, as Claude Code writes one: a reply of several blocks is several lines, one request
    id, each line with the counts so far."""
    return {"type": "assistant", "uuid": uid, "sessionId": session, "requestId": request_id, "timestamp": at(second),
            "message": {"id": "msg_" + request_id, "role": "assistant", "model": MODEL, "content": [block],
                        "usage": usage}}


def _words(text):
    return {"type": "text", "text": text}


TURN = [
    _typed("u1", "WORDS-OF-THE-OWNER how is the build?", 0),
    _reply("a1", "req_011A", _words("WORDS-OF-THE-SESSION Checking."), _counts(10, 3, 100, 20), 1),
    _reply("a2", "req_011A", {"type": "tool_use", "id": "t1", "name": "Bash",
                              "input": {"command": "pytest -q WORDS-OF-A-COMMAND"}}, _counts(10, 25, 100, 20), 2),
    {"type": "user", "uuid": "r1", "sessionId": "s1", "timestamp": at(3), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "WORDS-OF-A-RESULT 12 passed"}]}},
    _reply("a3", "req_011B", _words("WORDS-OF-THE-SESSION Green: 12 passed."), _counts(4, 9, 130, 0), 4),
]
LATER = [
    _typed("u2", "WORDS-OF-THE-OWNER and the deploy?", 5),
    _reply("a4", "req_011C", _words("WORDS-OF-THE-SESSION Done."), _counts(2, 5, 160, 8), 6),
]


def row(request_id, counts, second, link="mac", session="s1", device=DEVICE, token=TOKEN):
    """The row Proofpane is sent for one reply."""
    record = dict({"request_id": request_id, "session_id": session, "model": MODEL, "ts": at(second)}, **counts)
    return {"device_id": device, "token": token, "vendor": "ade", "agent": "default",
            "event_type": "mcp.subscription.usage",
            "payload": {"request_id": request_id, "session_id": session, "ade_kind": "linked", "ade_link_name": link,
                        "ade_started_by": "person", "ade_sign_in": "unknown", "client": "claude_code", "count": 1,
                        "records": [record], "totals": counts}}


ROW_A = row("req_011A", _counts(10, 25, 100, 20), 1)          # its counts: the largest its two lines give
ROW_B = row("req_011B", _counts(4, 9, 130, 0), 4)
ROW_C = row("req_011C", _counts(2, 5, 160, 8), 6)


def _transcript(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _stop(monkeypatch, capsys, directory, path, session="s1", cwd=None):
    payload = {"transcript_path": path, "session_id": session}
    if cwd:
        payload["cwd"] = cwd
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return run(capsys, "--dir", directory, "mirror", "reply")


def _wait(check, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        got = check()
        if got:
            return got
        time.sleep(0.05)
    return check()


def _rows():
    """The usage rows Proofpane was sent, in order."""
    return [b for b in posted(EVENT) if (b or {}).get("event_type") == "mcp.subscription.usage"]


def _rid(r):
    return r["payload"]["request_id"]


def _pair_proofpane(client, headers, address, code=CODE):
    return client.post("/api/ade/proofpane", headers=headers, json={"address": address, "code": code})


def _usage(client, headers):
    """The account's linked sessions' usage as the page reads it: sent, waiting, dropped and the last drop."""
    return client.get("/api/ade/proofpane", headers=headers).json()["usage"]


def _settled(client, headers):
    """The page's counts once nothing of the account's usage waits any more (each forward has ended)."""
    _wait(lambda: _usage(client, headers)["waiting"] == 0)
    return _usage(client, headers)


def _spy(monkeypatch):
    """The bodies of the link's requests to /usage, in order."""
    sent = []
    real = ade_link.Link.request

    def spy(self, method, path, *args, **kwargs):
        if path == "/usage":
            sent.append(args[0] if args else kwargs.get("body"))
        return real(self, method, path, *args, **kwargs)
    monkeypatch.setattr(ade_link.Link, "request", spy)
    return sent


def _usage_row():
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_link_usage").fetchone())


# ---- once, and only while paired -------------------------------------------------------------------------------------

def test_a_linked_session_s_reply_reaches_proofpane_once(client, owner, served, proofpane, capsys, tmp_path,
                                                          monkeypatch):
    """Read twice, sent twice by the link, and sent by a second link of the account: each reply is one row at
    Proofpane, its usage and never its words."""
    sent = _spy(monkeypatch)
    _pair_proofpane(client, owner, proofpane)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN)
    for _ in range(2):                  # the turn's Stop, and another one reading the same transcript
        assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(_rows()) >= 2), posted(EVENT)
    assert sorted(_rows(), key=_rid) == [ROW_A, ROW_B]
    assert [len(b["records"]) for b in sent] == [0, 2]      # asked first, then the turn's two replies, once
    assert "WORDS-OF" not in json.dumps(posted(EVENT))       # counts, ids, the model and times: never a word
    # sent twice: the link's record of what it sent lost, it sends the turn's replies again
    os.unlink(os.path.join(mac, "usage.json"))
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    # and a second link of the account reads the same session
    other = pair(client, owner, served, capsys, str(tmp_path / "other"), "other")
    assert _stop(monkeypatch, capsys, other, path) == (0, "", "")
    assert [len(b["records"]) for b in sent] == [0, 2, 2, 0, 2]
    assert _settled(client, owner) == {"sent": 2, "waiting": 0, "dropped": 0, "last_dropped": None}
    assert sorted(_rows(), key=_rid) == [ROW_A, ROW_B]       # still each once at Proofpane


def test_an_unpaired_account_s_link_sends_nothing_on(client, owner, served, proofpane, capsys, tmp_path,
                                                     monkeypatch):
    """No pairing: Proofpane is sent nothing, and the link sends the ADE none of its sessions' usage, asking first."""
    sent = _spy(monkeypatch)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN)
    for _ in range(2):
        assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert not _wait(lambda: Proofpane.asked, 1)                # a forward in a thread of its own, given time
    assert Proofpane.asked == []
    assert [b for b in sent if b.get("records")] == []


def test_what_a_session_said_while_its_account_was_unpaired_never_goes(client, owner, served, proofpane, capsys,
                                                                     tmp_path, monkeypatch):
    """Told the account is not paired, the link leaves those replies on the machine; ten minutes on it asks again, and
    once paired only what came after goes."""
    sent = _spy(monkeypatch)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    _pair_proofpane(client, owner, proofpane)
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: time.time() + 601)
    _transcript(path, TURN + LATER)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: _rows()), posted(EVENT)
    assert _settled(client, owner)["sent"] == 1
    assert _rows() == [ROW_C]
    assert [len(b["records"]) for b in sent] == [0, 0, 1]


# ---- the pairing --------------------------------------------------------------------------------------------------

def _answer_text(call):
    """What an answer of the app says, as text: its body, or a refusal's detail."""
    try:
        return json.dumps(call())
    except ade_link.Refused as e:
        return json.dumps([e.status, str(e), e.body])


def _files_holding(text, *folders):
    """The files under `folders` that hold `text`, by their names."""
    found = []
    for top in folders:
        for folder, _, names in os.walk(top):
            for name in names:
                with open(os.path.join(folder, name), "rb") as fh:
                    if text.encode() in fh.read():
                        found.append(os.path.join(folder, name))
    return found


def test_the_pairing_s_token_is_never_shown(client, owner, served, proofpane, capsys, tmp_path, monkeypatch):
    """A control: the account paired and a linked session's usage sent through the pairing, no answer of the app's to
    the page or to the link holds the token, nor any file of the app's or the link's, nor what either printed."""
    answers = [_pair_proofpane(client, owner, proofpane).text]
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    _wait(lambda: len(_rows()) >= 2 and _usage(client, owner).get("waiting") == 0, 5)     # its forward, where made
    for route in ("/api/ade/proofpane", "/api/ade/repos", "/api/ade/link/sessions", "/api/ade/link/history",
                  "/api/auth/me"):
        answers.append(client.get(route, headers=owner).text)
    answers.append(_answer_text(lambda: ade_link.Link(mac).request("POST", "/usage", {"records": []})))
    assert [a for a in answers if TOKEN in a] == []
    assert _files_holding(TOKEN, os.environ["OPEN_EDITION_DATA"]) == []     # the database, its log, all beside them
    out, err = capsys.readouterr()
    assert TOKEN not in out + err


def test_the_pairing_s_token_is_kept_sealed_as_a_repository_s_token_is(client, owner, served, proofpane, capsys,
                                                                       tmp_path, monkeypatch):
    """The code exchanged at Proofpane's route, the device named for the deployment; the device's id and token kept
    sealed together, the box opening in its own row alone and under the key-store key alone; the page told the
    pairing's address, the device's name and id and when it was paired, never the token or the box."""
    answer = _pair_proofpane(client, owner, proofpane)
    assert posted("/api/v1/devices/pair") == [{"code": CODE, "name": NAME, "platform": "proofpane-community"}]
    status = client.get("/api/ade/proofpane", headers=owner)
    assert answer.status_code == 200 and answer.json() == status.json()
    assert status.json() == {"can_pair": True, "pairing": {"address": proofpane, "device_id": DEVICE, "name": NAME,
                                                           "paired_at": "2026-10-18T01:02:03.456789Z"},
                             "addresses": [proofpane],
                             "usage": {"sent": 0, "waiting": 0, "dropped": 0, "last_dropped": None}}
    with store.connect() as db:
        kept = dict(db.execute("SELECT * FROM ade_proofpane_pairings").fetchone())
    assert kept["device_id"] == DEVICE and kept["box"].startswith("v1.")
    assert [a for a in (answer.text, status.text) if kept["box"] in a or kept["box"][3:20] in a] == []
    from proofpane_open.ade import proofpane as pairing
    assert pairing.unseal(kept["box"], kept["user_id"], kept["id"]) == (DEVICE, TOKEN)
    for user_id, pairing_id in ((kept["user_id"], "another"), ("another", kept["id"])):
        with pytest.raises(pairing.PairingError):
            pairing.unseal(kept["box"], user_id, pairing_id)
    # and its usage goes with the token it opens to
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(_rows()) >= 2) and all((r["device_id"], r["token"]) == (DEVICE, TOKEN) for r in _rows())
    assert _settled(client, owner)["sent"] == 2
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", base64.b64encode(bytes(32)).decode())
    with pytest.raises(pairing.PairingError):
        pairing.unseal(kept["box"], kept["user_id"], kept["id"])


def test_the_pairing_takes_an_address_and_a_code_as_the_workspace_does(client, owner, proofpane, monkeypatch):
    """An address the deployment names (here the stand-in, by the file's fixture) and a code in Proofpane's form, each
    checked before anything is sent; without a key-store key nothing is paired and no code is spent; pairing again
    replaces the pairing; unpairing removes it and revokes nothing in Proofpane."""
    for address, code in (("http://example.com", CODE), ("ftp://example.com", CODE), (proofpane + "/api", CODE),
                          (proofpane, "not a code")):
        r = _pair_proofpane(client, owner, address, code)
        assert r.status_code == 400, (address, code, r.text)
    assert Proofpane.asked == []
    monkeypatch.delenv("OPEN_EDITION_KEYSTORE_KEY")
    r = _pair_proofpane(client, owner, proofpane)
    assert r.status_code == 400 and "key-store key" in r.json()["detail"]
    assert Proofpane.asked == []                                    # the code is not spent
    assert client.get("/api/ade/proofpane", headers=owner).json()["can_pair"] is False
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    r = _pair_proofpane(client, owner, proofpane, "WRO-NGC-ODE")
    assert r.status_code == 400 and "did not take the code" in r.json()["detail"]
    assert _pair_proofpane(client, owner, proofpane).json()["pairing"]["device_id"] == DEVICE
    assert _pair_proofpane(client, owner, proofpane + "/", CODE_B).json()["pairing"] == {
        "address": proofpane, "device_id": DEVICE_B, "name": "Proofpane Community at open.example.test",
        "paired_at": "2026-10-18T01:02:03.456789Z"}
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_proofpane_pairings").fetchone()[0] == 1     # one per account
    asked = len(Proofpane.asked)
    gone = client.delete("/api/ade/proofpane", headers=owner)
    assert gone.status_code == 200 and gone.json()["pairing"] is None
    assert len(Proofpane.asked) == asked                            # nothing asked of Proofpane: nothing revoked there
    assert client.delete("/api/ade/proofpane", headers=owner).status_code == 404


# ---- to the account's own pairing alone ---------------------------------------------------------------------------

def test_one_account_s_usage_never_reaches_another_account_s_pairing(client, owner, served, proofpane, capsys,
                                                                    tmp_path, monkeypatch):
    other = signed_up(client, "other@example.com")
    _pair_proofpane(client, owner, proofpane, CODE)
    _pair_proofpane(client, other, proofpane, CODE_B)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    box = pair(client, other, served, capsys, str(tmp_path / "box"), "box")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(_rows()) >= 2), posted(EVENT)
    assert [(r["device_id"], r["token"]) for r in _rows()] == [(DEVICE, TOKEN)] * 2
    theirs = str(tmp_path / "theirs.jsonl")
    _transcript(theirs, [_typed("v1", "WORDS-OF-THE-OTHER hello", 7, session="s2"),
                         _reply("b1", "req_011D", _words("WORDS-OF-THE-SESSION Hi."), _counts(1, 2, 3, 4), 8,
                                session="s2")])
    assert _stop(monkeypatch, capsys, box, theirs, session="s2") == (0, "", "")
    assert _wait(lambda: len(_rows()) >= 3), posted(EVENT)
    assert _settled(client, owner)["sent"] == 2 and _settled(client, other)["sent"] == 1
    assert sorted((r["device_id"], r["token"], _rid(r), r["payload"]["ade_link_name"]) for r in _rows()) == [
        (DEVICE, TOKEN, "req_011A", "mac"), (DEVICE, TOKEN, "req_011B", "mac"), (DEVICE_B, TOKEN_B, "req_011D", "box")]


def test_a_reply_that_waits_goes_only_through_the_pairing_it_came_under(client, owner, served, proofpane, capsys,
                                                                       tmp_path, monkeypatch):
    """Paired again, or unpaired, before it went: a reply waiting to go is dropped, and said, never sent through the
    pairing made since."""
    Proofpane.garbled = (EVENT,)                                    # Proofpane answers no row at first
    _pair_proofpane(client, owner, proofpane, CODE)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN[:3])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(posted(EVENT)) >= 1), Proofpane.asked
    from proofpane_open.ade import link_usage
    assert _wait(lambda: _usage_row()["tries"] == 1)
    Proofpane.garbled = ()
    _pair_proofpane(client, owner, proofpane, CODE_B)               # paired again, as another device
    assert _usage(client, owner) == {"sent": 0, "waiting": 0, "dropped": 1, "last_dropped": {
        "at": _usage_row()["done_at"], "reason": link_usage.UNPAIRED}}     # dropped as the pairing changed
    assert link_usage.forward_due(time.time() + 10 ** 6) == 0
    assert len(posted(EVENT)) == 1
    # a reply a forward read before the pairing changed (one still waiting under the old pairing) goes nowhere either
    with store.connect() as db:
        db.execute("INSERT INTO ade_link_usage (user_id, request_id, pairing_id, link_id, link_name, session_id, "
                   "model, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, ts, "
                   "started_by, received_at, state, tries, next_at) SELECT user_id, 'req_011S', pairing_id, link_id, "
                   "link_name, "
                   "session_id, model, 1, 1, 1, 1, ts, started_by, received_at, 'waiting', 0, 0 FROM ade_link_usage "
                   "WHERE request_id = 'req_011A'")
    assert link_usage.forward_due(time.time()) == 0
    assert len(posted(EVENT)) == 1 and _usage(client, owner)["dropped"] == 2
    # what comes after goes through the pairing made since
    _transcript(path, TURN + LATER)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(_rows()) >= 3), posted(EVENT)
    assert _settled(client, owner)["sent"] == 2
    # the first, refused, was the old device's try; the two since went to the new one
    assert [(r["device_id"], _rid(r)) for r in _rows()] == [(DEVICE, "req_011A"), (DEVICE_B, "req_011B"),
                                                            (DEVICE_B, "req_011C")]
    # and unpaired, a reply waiting is dropped, and nothing more goes
    Proofpane.garbled = (EVENT,)
    _transcript(path, TURN + LATER + [_typed("u3", "WORDS-OF-THE-OWNER and now?", 9),
                                      _reply("a5", "req_011E", _words("WORDS-OF-THE-SESSION Now."),
                                             _counts(1, 1, 1, 1), 10)])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(posted(EVENT)) >= 4)
    gone = client.delete("/api/ade/proofpane", headers=owner)
    assert gone.status_code == 200 and gone.json()["pairing"] is None
    assert gone.json()["usage"]["waiting"] == 0 and gone.json()["usage"]["dropped"] == 3    # dropped as it unpaired
    Proofpane.garbled = ()
    assert link_usage.forward_due(time.time() + 10 ** 6) == 0
    assert len(posted(EVENT)) == 4
    assert _settled(client, owner)["dropped"] == 3


def test_a_fork_s_copies_and_synthetic_replies_are_not_the_session_s_usage(client, owner, served, proofpane, capsys,
                                                                          tmp_path, monkeypatch):
    """Read from the window's start (the link's record names an entry no longer there), the entries another session
    wrote (a fork's copies of its source's) and a reply Claude Code writes itself (no call made) are left out."""
    _pair_proofpane(client, owner, proofpane)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    copied = [_typed("c1", "WORDS-OF-THE-OWNER an older prompt", 0, session="s0"),
              _reply("c2", "req_011Z", _words("WORDS-OF-THE-SESSION Older."), _counts(9, 9, 9, 9), 0, session="s0")]
    synthetic = {"type": "assistant", "uuid": "y1", "sessionId": "s1", "timestamp": at(3),
                 "message": {"id": "msg_synthetic_1", "role": "assistant", "model": "<synthetic>",
                             "content": [_words("No response requested.")], "usage": _counts(0, 0, 0, 0)}}
    _transcript(path, copied + TURN[:3] + [synthetic])
    with open(os.path.join(mac, "usage.json"), "w") as fh:
        json.dump({"s1": {"at": "an-entry-no-longer-there", "sent": []}}, fh)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: _rows()), posted(EVENT)
    assert _settled(client, owner)["sent"] == 1
    assert _rows() == [ROW_A]


# ---- a forward that fails, and what it never holds up -------------------------------------------------------------

def test_a_forward_that_fails_is_tried_four_times_then_dropped_and_said(client, owner, served, proofpane, capsys,
                                                                       tmp_path, monkeypatch):
    """Tried at once, then after 1, 5 and 30 minutes; after the fourth it is dropped, kept as dropped with why and when,
    which the page shows and the app's log says, and never tried again. The link's own notes went meanwhile."""
    Proofpane.garbled = (EVENT,)                                    # every row is answered with no HTTP at all
    _pair_proofpane(client, owner, proofpane)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN[:3])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(posted(EVENT)) >= 1), Proofpane.asked     # tried at once
    events = client.get(f"/api/ade/link/timeline/{link_row('mac')['id']}", headers=owner).json()["events"]
    assert {e["text"] for e in events} >= {"WORDS-OF-THE-OWNER how is the build?", "WORDS-OF-THE-SESSION Checking."}
    from proofpane_open.ade import link_usage
    assert _wait(lambda: _usage_row()["tries"] == 1)
    now = _usage_row()["next_at"]
    assert link_usage.forward_due(now - 1) == 0 and len(posted(EVENT)) == 1      # not before its minute
    assert link_usage.forward_due(now) == 0 and len(posted(EVENT)) == 2
    assert link_usage.forward_due(now + 299) == 0 and len(posted(EVENT)) == 2    # then not before five more
    assert link_usage.forward_due(now + 300) == 0 and len(posted(EVENT)) == 3
    assert link_usage.forward_due(now + 300 + 1799) == 0 and len(posted(EVENT)) == 3
    assert link_usage.forward_due(now + 300 + 1800) == 0 and len(posted(EVENT)) == 4
    dropped = _usage_row()
    assert dropped["state"] == "dropped" and dropped["tries"] == 4 and "BadStatusLine" in (dropped["reason"] or "")
    assert link_usage.forward_due(now + 10 ** 7) == 0 and len(posted(EVENT)) == 4    # never tried again
    assert _usage(client, owner) == {"sent": 0, "waiting": 0, "dropped": 1, "last_dropped": {
        "at": dropped["done_at"], "reason": dropped["reason"]}}
    assert "dropped after 4 tries" in capsys.readouterr().err


def test_the_link_never_waits_on_proofpane(client, owner, served, proofpane, capsys, tmp_path, monkeypatch):
    """A Proofpane slow to answer holds no Stop: the app takes the usage and forwards it in a thread of its own."""
    Proofpane.slow = (EVENT,)
    _pair_proofpane(client, owner, proofpane)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN[:3])
    monkeypatch.setattr(Proofpane, "SLOW", 6)
    began = time.monotonic()
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    took = time.monotonic() - began
    assert _wait(lambda: _rows(), 15) == [ROW_A], posted(EVENT)
    assert took < 4, took                   # the link's own request waits at most 5 seconds; Proofpane takes 6
    assert _wait(lambda: _usage(client, owner)["sent"] == 1, 15)      # and the forward went, once it answered


def test_two_forwards_at_once_send_a_reply_once(client, owner, served, proofpane, capsys, tmp_path, monkeypatch):
    """Each forward claims a reply before it sends it, so one that runs while another sends it sends nothing."""
    Proofpane.garbled = (EVENT,)
    _pair_proofpane(client, owner, proofpane)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN[:3])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(posted(EVENT)) >= 1), Proofpane.asked
    from proofpane_open.ade import link_usage
    from proofpane_open.ade import proofpane as pairing
    assert _wait(lambda: _usage_row()["tries"] == 1)
    Proofpane.garbled = ()
    now, during, entered = _usage_row()["next_at"], [], []
    real = pairing.send_event

    def another_forward_meanwhile(*args):
        if not entered:                         # once, marked first: the forward it starts sends through here too
            entered.append(1)
            during.append(link_usage.forward_due(now))
        return real(*args)
    monkeypatch.setattr(pairing, "send_event", another_forward_meanwhile)
    assert link_usage.forward_due(now) == 1 and during == [0]
    assert len(posted(EVENT)) == 2 and _usage(client, owner)["sent"] == 1


def test_two_forwards_that_read_a_reply_together_send_it_once(client, owner, served, proofpane, capsys, tmp_path,
                                                                monkeypatch):
    """A forward sends a reply only where its own claim took: one that read the reply as due while another forward read
    it too, and comes to claim it after the other sent it, sends nothing (the sweep and the thread a request starts, or
    two of the app's processes, each read what is due on their own)."""
    Proofpane.garbled = (EVENT,)
    _pair_proofpane(client, owner, proofpane)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN[:3])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: len(posted(EVENT)) >= 1), Proofpane.asked
    from proofpane_open.ade import link_usage
    assert _wait(lambda: _usage_row()["tries"] == 1)
    Proofpane.garbled = ()
    now, during, entered = _usage_row()["next_at"], [], []
    real = link_usage.store

    class _Db:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if sql.startswith("UPDATE") and not entered:    # this forward's first write, once it has read the reply:
                entered.append(1)                           # the other forward, which read it too, runs whole first
                during.append(link_usage.forward_due(now))
            return self.db.execute(sql, *args)

    class _Store:
        def __getattr__(self, name):
            return getattr(real, name)

        @contextlib.contextmanager
        def connect(self, *args, **kwargs):
            with real.connect(*args, **kwargs) as db:
                yield _Db(db)
    monkeypatch.setattr(link_usage, "store", _Store())
    assert link_usage.forward_due(now) == 0 and during == [1]
    assert len(posted(EVENT)) == 2 and _usage(client, owner)["sent"] == 1


# ---- what the link sends, and what the app takes ------------------------------------------------------------------

def test_a_session_history_json_does_not_list_sends_no_usage(client, owner, served, proofpane, capsys, tmp_path,
                                                             monkeypatch):
    """As the mirror posts no turn of it: a session of an excepted project, or outside the projects history.json names,
    keeps its usage on the machine; one it lists sends it."""
    _pair_proofpane(client, owner, proofpane)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home = tmp_path / "home"
    for folder in ("work", "grove", "elsewhere"):
        (home / folder).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": ["~/work"], "except": ["~/grove"]}, fh)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)     # the History run a Stop starts
    for sid, folder, request_id in (("s-grove", "grove", "req_011G"), ("s-else", "elsewhere", "req_011E"),
                                    ("s-work", "work", "req_011W")):
        cwd = str(home / folder)
        path = home / ".claude" / "projects" / "-work" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        _transcript(str(path), [_typed("u1", "WORDS-OF-THE-OWNER a plan", 0, session=sid, cwd=cwd),
                                _reply("a1", request_id, _words("WORDS-OF-THE-SESSION Noted."), _counts(1, 2, 3, 4), 1,
                                       session=sid)])
        assert _stop(monkeypatch, capsys, mac, str(path), session=sid, cwd=cwd) == (0, "", "")
    assert _wait(lambda: _rows()), posted(EVENT)
    assert _settled(client, owner)["sent"] == 1
    assert [(_rid(r), r["payload"]["session_id"]) for r in _rows()] == [("req_011W", "s-work")]


def test_the_ade_takes_only_usage_of_the_form_the_link_sends(client, owner, served, proofpane, capsys, tmp_path,
                                                             monkeypatch):
    """Each reply's fields and nothing else, each text of its field's form and nothing shaped like a secret (named by
    its shape), each count a whole number; at most 50 a request and a number an hour per link. A request refused keeps
    nothing."""
    _pair_proofpane(client, owner, proofpane)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    link = ade_link.Link(mac)
    good = dict({"request_id": "req_011A", "session_id": "s1", "model": MODEL, "ts": at(1)}, **_counts(10, 25, 100, 20))

    def refused(body):
        try:
            link.request("POST", "/usage", body)
        except ade_link.Refused as e:
            return e.status, str(e)
        return None
    for body, why in (({"records": [dict(good, request_id="sk-ant-api03-" + "x" * 20)]}, "an Anthropic key"),
                      ({"records": [dict(good, model="ghp_" + "y" * 36)]}, "a GitHub token"),
                      ({"records": [dict(good, session_id="AKIA" + "Z" * 16)]}, "an AWS key"),
                      ({"records": [dict(good, text="WORDS-OF-THE-SESSION")]}, "nothing else"),
                      ({"records": [dict(good, input_tokens=-1)]}, "whole number"),
                      ({"records": [dict(good, output_tokens=True)]}, "whole number"),
                      ({"records": [dict(good, output_tokens=2.5)]}, "whole number"),
                      ({"records": [dict(good, session_id="../s1")]}, "session id"),
                      ({"records": [dict(good, ts="yesterday")]}, "time"),
                      ({"records": [dict(good, started_by="the owner")]}, "started by"),
                      ({"records": [good] * 51}, "At most 50"),
                      ({"records": good}, "{records"),
                      ({"usage": [good]}, "{records")):
        got = refused(body)
        assert got is not None and got[0] == 422 and why in got[1], (body, got)
    assert _usage(client, owner) == {"sent": 0, "waiting": 0, "dropped": 0, "last_dropped": None}
    assert link.request("POST", "/usage", {"records": [good]}) == {"paired": True, "taken": 1}
    assert link.request("POST", "/usage", {"records": [good]}) == {"paired": True, "taken": 0}
    from proofpane_open.ade import link_usage
    monkeypatch.setattr(link_usage, "PER_HOUR", 2)
    assert link.request("POST", "/usage", {"records": [dict(good, request_id="req_011B")]})["taken"] == 1
    got = refused({"records": [dict(good, request_id="req_011C")]})
    assert got is not None and got[0] == 429, got
    assert _settled(client, owner)["sent"] == 2


# ---- where the app sends anything: only to a Proofpane its deployment names ---------------------------------------

def test_the_app_pairs_and_forwards_only_with_an_address_its_deployment_names(client, owner, served, proofpane,
                                                                              capsys, tmp_path, monkeypatch):
    """This server makes the request that spends the code, and later posts each reply's usage, to the address paired
    with: a member's typed address would make it send a request wherever that says, this machine and its private
    network among them. So OPEN_EDITION_PROOFPANE_ADDRESSES names the addresses (unset, api.proofpane.com alone;
    empty, none), the page is told them, any other is refused before anything is sent, and a pairing whose address the
    deployment no longer names forwards nothing."""
    monkeypatch.setenv("OPEN_EDITION_PROOFPANE_ADDRESSES", "https://api.proofpane.com")
    r = _pair_proofpane(client, owner, proofpane)       # http to this machine, a form the workspace's pairing takes
    assert r.status_code == 400, r.text
    assert Proofpane.asked == []                        # refused before anything was sent
    assert "https://api.proofpane.com" in r.json()["detail"]
    assert client.get("/api/ade/proofpane", headers=owner).json().get("addresses") == ["https://api.proofpane.com"]
    monkeypatch.setenv("OPEN_EDITION_PROOFPANE_ADDRESSES", "")
    assert client.get("/api/ade/proofpane", headers=owner).json().get("addresses") == []
    for address in (None, proofpane):
        r = client.post("/api/ade/proofpane", headers=owner, json=dict({"code": CODE}, **(
            {"address": address} if address else {})))
        assert r.status_code == 400, (address, r.text)
    assert Proofpane.asked == []
    monkeypatch.setenv("OPEN_EDITION_PROOFPANE_ADDRESSES", " https://api.proofpane.com/ , " + proofpane + "/,")
    assert client.get("/api/ade/proofpane", headers=owner).json().get("addresses") == [
        "https://api.proofpane.com", proofpane]
    paired = _pair_proofpane(client, owner, proofpane + "/")
    assert paired.status_code == 200 and paired.json()["pairing"]["address"] == proofpane
    # the deployment stops naming it: what the account's links send is dropped, and nothing goes there
    monkeypatch.setenv("OPEN_EDITION_PROOFPANE_ADDRESSES", "https://api.proofpane.com")
    asked = len(Proofpane.asked)
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, TURN[:3])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _wait(lambda: _usage(client, owner)["dropped"] == 1), _usage(client, owner)
    assert "no longer" in _usage(client, owner)["last_dropped"]["reason"]
    assert len(Proofpane.asked) == asked and posted(EVENT) == []

