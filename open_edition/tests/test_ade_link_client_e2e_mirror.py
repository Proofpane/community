"""End-to-end encryption, the link client's mirror (ade_link.py mirror reply, the Stop hook): each note to the owner
sealed, bound to its kind, whose words it reports and its client_id, its plaintext naming its session and when its
words were said; a turn held, its place kept, while encryption may be on and the link has no key to seal to, and posted
sealed once it has; and the ADE's 409 midway moving the place back to what went, so no turn is lost. Against the
stand-in of the server's side (e2e_link_stand_in)."""

import io
import json
import os
import sys

import pytest

import ade_link
from e2e_link_stand_in import (ACCOUNT, ENVELOPE, LINK_ID, LinkStandIn, NotOpened, a_link, key_id, open_envelope,
                               raw_public, slot)
from test_ade_link_client import run


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path):
    monkeypatch.delenv("ADE_OWNER_KEYS", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ade_link, "_history_soon", lambda link, every=None: None)     # no History run in a process


@pytest.fixture
def ade():
    s = LinkStandIn()
    yield s
    s.close()


def typed(uid, text, at):
    return {"type": "user", "uuid": uid, "origin": {"kind": "human"}, "turnOrigin": "human", "timestamp": at,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def said(uid, text, at):
    return {"type": "assistant", "uuid": uid, "timestamp": at,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def transcript(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def stop(monkeypatch, capsys, d, path, session="s1"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(path), "session_id": session})))
    return run(capsys, "--dir", d, "mirror", "reply")


def note_slot(e):
    return slot(ACCOUNT, LINK_ID, "event", "note", e["mirror"], None, e["client_id"])


def place(d, session="s1"):
    return json.loads(open(os.path.join(d, "mirror.json")).read()).get(session)


def log(d):
    path = os.path.join(d, "mirror.log")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


TURN = [typed("u1", "how is the build?", "2026-10-03T10:00:00Z"), said("a1", "Checking.", "2026-10-03T10:00:05Z"),
        said("a2", "Green: 12 passed.", "2026-10-03T10:00:09Z")]


def test_each_note_goes_sealed_bound_to_whose_words_it_reports_naming_its_session(ade, capsys, tmp_path,
                                                                                   monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    with open(os.path.join(d, "history_put.json"), "w") as fh:
        json.dump({"at": 1, "registered": ["s1"], "noted": []}, fh)          # the last list registered the session
    _, key = ade.owner.state()
    ade.require = True
    path = tmp_path / "t.jsonl"
    transcript(path, TURN)
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    owner, session = ade.events
    assert (owner["mirror"], session["mirror"], owner["session"], owner["to"]) == ("owner", "session", "s1", "owner")
    assert ENVELOPE.fullmatch(owner["text"]) and ENVELOPE.fullmatch(session["text"])
    assert open_envelope(owner["text"], note_slot(owner), key) == {
        "text": "how is the build?", "session": "s1", "said_at": owner["said_at"]}
    assert owner["said_at"] == 1791021600000
    assert open_envelope(session["text"], note_slot(session), key) == {
        "text": "Checking.\n\nGreen: 12 passed.", "session": "s1", "said_at": 1791021605000}
    with pytest.raises(NotOpened):                      # the slot names whose words: the owner's are not the session's
        open_envelope(owner["text"], slot(ACCOUNT, LINK_ID, "event", "note", "session", None, owner["client_id"]),
                      key)
    assert place(d) == "a2"


def test_a_turn_is_held_its_place_kept_while_the_link_has_no_key_and_goes_sealed_once_it_has(ade, capsys, tmp_path,
                                                                                              monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state(device=ade.owner.other)              # encryption on, by a passkey this link has not pinned
    path = tmp_path / "t.jsonl"
    transcript(path, TURN[:2])
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    transcript(path, TURN + [typed("u2", "and now?", "2026-10-03T10:01:00Z")])
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert ade.posted() == [] and place(d) == ade_link.MIRROR_FROM_START
    assert log(d).count("held: encryption") == 1 and "has not pinned" in log(d)
    _, key = ade.owner.state()                            # a pinned passkey signs a later statement: it chains back
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    texts = [open_envelope(e["text"], note_slot(e), key)["text"] for e in ade.events]
    assert texts == ["how is the build?", "Checking.\n\nGreen: 12 passed.", "and now?"]
    assert "no longer held: encryption" in log(d)


def test_the_ade_s_409_midway_has_the_rest_sealed_once_the_link_holds_the_key(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0                          # read: none stated
    real, key = ade.route, []

    def turned_on_after_the_first(method, path, body):
        answer = real(method, path, body)
        if path.endswith("/events") and not key:
            key.append(ade.owner.state()[1])
            ade.require = True
        return answer
    monkeypatch.setattr(ade, "route", turned_on_after_the_first)
    path = tmp_path / "t.jsonl"
    transcript(path, TURN)
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    first, second = ade.events
    assert first["text"] == "how is the build?"
    assert open_envelope(second["text"], note_slot(second), key[0])["text"] == "Checking.\n\nGreen: 12 passed."
    assert place(d) == "a2"


def test_the_ade_s_409_midway_with_no_key_moves_the_place_back_to_what_went_and_loses_no_turn(ade, capsys, tmp_path,
                                                                                            monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    real, turned = ade.route, []

    def turned_on_after_the_first(method, path, body):
        answer = real(method, path, body)
        if path.endswith("/events") and not turned:
            turned.append(ade.owner.state(device=ade.owner.other))       # by a passkey this link has not pinned
            ade.require = True
        return answer
    monkeypatch.setattr(ade, "route", turned_on_after_the_first)
    path = tmp_path / "t.jsonl"
    transcript(path, TURN)
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert [e["text"] for e in ade.events] == ["how is the build?"]
    assert place(d) == "u1" and "held: encryption" in log(d)                # back to the end of what went
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert len(ade.events) == 2 and place(d) == "a2"
    assert open_envelope(ade.events[1]["text"], note_slot(ade.events[1]), key)["text"] == "Checking.\n\nGreen: 12 " \
                                                                                         "passed."


def test_the_place_goes_back_to_the_last_entry_of_the_last_note_that_went_whole(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    real, posts = ade.route, []

    def turned_on_after_the_second(method, path, body):
        answer = real(method, path, body)
        if path.endswith("/events"):
            posts.append(1)
            if len(posts) == 2:
                ade.owner.state(device=ade.owner.other)
                ade.require = True
        return answer
    monkeypatch.setattr(ade, "route", turned_on_after_the_second)
    path = tmp_path / "t.jsonl"
    transcript(path, TURN + [typed("u2", "and now?", "2026-10-03T10:01:00Z"),
                             said("a3", "Done.", "2026-10-03T10:01:05Z")])
    with open(os.path.join(d, "mirror.json"), "w") as fh:
        json.dump({"s1": ade_link.MIRROR_FROM_START}, fh)                   # both turns go in this Stop
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert [e["text"] for e in ade.events] == ["how is the build?", "Checking.\n\nGreen: 12 passed."]
    assert place(d) == "a2"                                 # the session's note held two entries: after its last
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert [open_envelope(e["text"], note_slot(e), key)["text"] for e in ade.events[2:]] == ["and now?", "Done."]


def test_a_note_refused_again_after_the_statements_are_read_holds_the_rest_its_place_moved_back(ade, capsys,
                                                                                               tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    ade.require = True
    ade.retired.add(key_id(raw_public(key)))                # the ADE refuses the key it holds as its newest
    path = tmp_path / "t.jsonl"
    transcript(path, TURN)
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert ade.events == [] and place(d) == ade_link.MIRROR_FROM_START
    assert f"the ADE refused the note sealed to key {key_id(raw_public(key))} too" in log(d)
    ade.retired.clear()
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert [open_envelope(e["text"], note_slot(e), key)["text"] for e in ade.events] == [
        "how is the build?", "Checking.\n\nGreen: 12 passed."]


def test_a_later_stop_s_place_is_never_moved_back(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    with open(os.path.join(d, "mirror.json"), "w") as fh:
        json.dump({"s1": "a9"}, fh)
    ade_link._mirror_unplace(ade_link.Link(d), "s1", "a2", "u1")           # this Stop's place was a2: another moved it
    assert place(d) == "a9"
    ade_link._mirror_unplace(ade_link.Link(d), "s1", "a9", "u1")
    assert place(d) == "u1"


def test_a_note_too_long_to_seal_goes_as_a_note_saying_so(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    path = tmp_path / "t.jsonl"
    transcript(path, [typed("u1", "go", "2026-10-03T10:00:00Z"), said("a1", "\x01" * 3990, "2026-10-03T10:00:05Z")])
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert [open_envelope(e["text"], note_slot(e), key)["text"] for e in ade.events] == [
        "go", "[left out: it is longer than an envelope holds]"]


def test_a_stop_s_read_of_the_statements_is_bounded(ade, capsys, tmp_path, monkeypatch):
    import threading
    import time as _time
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0                          # holds the key already
    data = json.loads(open(os.path.join(d, "e2e.json")).read())
    data["fetched_at"] = 0                                                 # stale: a Stop reads again
    with open(os.path.join(d, "e2e.json"), "w") as fh:
        json.dump(data, fh)
    gate, real = threading.Event(), ade_link.Link.request

    def stuck_read(self, method, path, *a, **kw):
        if path == "/e2e":
            gate.wait(30)
        return real(self, method, path, *a, **kw)
    monkeypatch.setattr(ade_link.Link, "request", stuck_read)
    monkeypatch.setattr(ade_link, "E2E_STOP_READ", 0.5)
    path = tmp_path / "t.jsonl"
    transcript(path, TURN[:2])
    started = _time.monotonic()
    try:
        assert stop(monkeypatch, capsys, d, path) == (0, "", "")
        assert _time.monotonic() - started < 5
    finally:
        gate.set()
    assert [open_envelope(e["text"], note_slot(e), key)["text"] for e in ade.events] == ["how is the build?",
                                                                                         "Checking."]
