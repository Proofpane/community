"""The live view, the server's side: a link's live view posts one note a round, mirror
"live", its items the turn's words and calls in order. Its notes are counted apart from the link's EVENTS_PER_HOUR,
against LIVE_PER_HOUR of their own, so the session's own events never wait on them; the link's timeline says when
that share is used, so the page can say so; a round's view says when a Stop
of its session has posted its turn's words again whole (posted_again), which the ADE alone decides; and a link keeps
its live view's notes up to LIVE_KEPT characters, deleting only rounds posted again, the oldest first, its other events
never."""

import json
import re
import time

import pytest

from proofpane_open import store
from proofpane_open.ade import link
from test_ade_e2e import send_sealed
from test_ade_link import paired, post
from test_ade_link_prompts import device, keys, owner, prompt  # noqa: F401  fixtures and helpers
from test_connector_tools import history, row


def _note(s, text, mirror, session, at, **more):
    """A mirrored note of `session`, its words said at `at` (milliseconds); its seq."""
    r = post(s, "note", text, mirror=mirror, session=session, said_at=at, **more)
    assert r.status_code == 200, r.text
    return r.json()["seq"]


def _live(n, pad=0):
    return json.dumps({"items": [["owner", "go"], ["session", f"live {n}" + "x" * pad], ["calls", ["Bash: ls"]]]})


def _timeline(client, owner, s, query=""):
    r = client.get(f"/api/ade/link/timeline/{s.link_id}{query}", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_live_note_is_a_mirrored_note_to_the_owner(client, owner):
    mac = paired(client, owner)
    r = post(mac, "note", _live(0), mirror="live")
    assert r.status_code == 200, r.text
    (ev,) = [e for e in _timeline(client, owner, mac)["events"] if e.get("mirror") == "live"]
    assert ev["text"] == _live(0) and ev["to"] == "owner"
    for wrong in ({"kind": "question"}, {"to": "all"}, {"ref": 1}):
        body = dict({"kind": "note"}, **wrong)
        assert post(mac, body.pop("kind"), _live(1), mirror="live", **body).status_code == 422, wrong


def test_a_live_view_at_its_share_leaves_the_session_s_question_waiting_and_prompt_their_own(client, owner, keys,
                                                                                            monkeypatch):
    """A link's live notes are counted apart from its EVENTS_PER_HOUR, against LIVE_PER_HOUR of their own. Past that
    share a live note is refused (429), and the session's own events go on, counted on the link's hour alone: a
    question to the owner, a waiting and the prompt that reaches the owner's Approvals. And the other way: the link's
    hour used, its live view's share is still its own."""
    monkeypatch.setattr(link, "LIVE_PER_HOUR", 3, raising=False)
    mac = paired(client, owner)
    with store.connect() as db:
        before = db.execute("SELECT COUNT(*) FROM ade_link_events WHERE link_id = ?", (mac.link_id,)).fetchone()[0]
    monkeypatch.setattr(link, "EVENTS_PER_HOUR", before + 3)        # the link's own hour: these three and no more
    for n in range(4):
        r, _, _ = send_sealed(keys, mac, "note", _live(n), mirror="live")
        assert r.status_code == (200 if n < 3 else 429), (n, r.text)
    r, _, _ = send_sealed(keys, mac, "question", "Which cap, per user or per org?", to="owner")
    assert r.status_code == 200, r.text
    r, _, _ = send_sealed(keys, mac, "waiting", "for the tray")
    assert r.status_code == 200, r.text
    prompt(keys, mac, "Bash: git push origin main")
    r, _, _ = send_sealed(keys, mac, "note", "one more", mirror="session")
    assert r.status_code == 429, r.text                              # the link's own hour is used
    monkeypatch.setattr(link, "LIVE_PER_HOUR", 4, raising=False)
    r, _, _ = send_sealed(keys, mac, "note", _live(4), mirror="live")
    assert r.status_code == 200, r.text                              # and its live view's share is its own


def test_the_timeline_says_when_the_live_view_s_share_is_used(client, owner, monkeypatch):
    """The page says plainly when the live view's share is reached: a link's timeline, its own
    conversation's and a session's, says whether its live notes of the last hour are at LIVE_PER_HOUR."""
    monkeypatch.setattr(link, "LIVE_PER_HOUR", 2, raising=False)
    mac = paired(client, owner)
    for n in range(2):                                      # the link's other events are not its live view's
        assert post(mac, "note", f"said {n}", mirror="session").status_code == 200
    assert post(mac, "question", "Which cap?", to="owner").status_code == 200
    full = lambda q="": _timeline(client, owner, mac, q).get("live_full")   # noqa: E731
    assert (full(), full("?session=s1")) == (False, False)
    for n in range(2):
        assert post(mac, "note", _live(n), mirror="live").status_code == 200
    assert (full(), full("?session=s1")) == (True, True)
    assert post(mac, "note", _live(2), mirror="live").status_code == 429


def test_a_note_in_the_link_s_own_conversation_names_its_session_for_a_page_loaded_before(client, owner):
    """A note that named a session the link's list has not registered goes in the link's own conversation, and its view
    says which session (untagged_session): this release's page and connector read the ADE's posted_again instead, but
    a page loaded before it reads the session to tell one session's round from another's, so the view keeps it for one
    release."""
    mac = paired(client, owner)
    assert post(mac, "note", _live(0), mirror="live", session="s1").status_code == 200
    (ev,) = [e for e in _timeline(client, owner, mac)["events"] if e.get("mirror") == "live"]
    assert ev.get("untagged_session") == "s1" and "session" not in ev


def test_whole_since_is_a_time_on_a_stop_s_note_that_names_its_session(client, owner):
    """A Stop's last note says it posted its turn's words whole from a time on (whole_since, in milliseconds): only a
    mirrored note that is not a round's and names its session carries it, an integer time not in the ADE's future, as
    said_at is."""
    mac = paired(client, owner)
    now = int(time.time()) * 1000
    wrong = [("a round's", dict(mirror="live", session="s1", said_at=now, whole_since=now)),
             ("no mirror's", dict(whole_since=now)),
             ("no session's", dict(mirror="session", said_at=now, whole_since=now)),
             ("a string", dict(mirror="session", session="s1", whole_since=str(now))),
             ("true", dict(mirror="session", session="s1", whole_since=True)),
             ("a float", dict(mirror="session", session="s1", whole_since=now + 0.5)),
             ("zero", dict(mirror="session", session="s1", whole_since=0)),
             ("the ADE's future", dict(mirror="session", session="s1", whole_since=now + 310_000))]
    for what, body in wrong:
        r = post(mac, "note", "Done.", **body)
        assert r.status_code == 422, (what, r.text)
    seq = _note(mac, "Done.", "session", "s1", now, whole_since=now - 5000)
    with store.connect() as db:
        assert db.execute("SELECT whole_since FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()[0] == now - 5000
    # and up to 300 seconds past the ADE's time, as said_at (310 refused and 5 back taken alone left any bound from 0
    # to 309 seconds)
    _note(mac, "Again.", "session", "s1", now, whole_since=now + 290_000)


def test_the_view_says_a_round_was_posted_again_only_behind_its_session_s_whole_stop(client, owner):
    """A round's view says posted_again only where a later note of its session says
    its Stop posted the turn's words whole from a time at or before the round's first words (whole_since), the session
    registered by the link's list or only named. Not behind a Stop's note that does not say so (an older client's,
    which posts what its rounds left, or a Stop that cut its turn), nor a turn's behind a later turn's Stop that read
    from that turn's start, nor another session's; and a round posted before its session was registered goes with its
    Stop's notes posted after, though the two are in different views."""
    mac = paired(client, owner)
    t = (int(time.time()) - 3600) * 1000
    first = _note(mac, _live(0), "live", "s1", t)                 # s1 not registered yet: the link's own conversation
    history(mac, [row("s1", "Work", "~/code", registered=True)])
    _note(mac, "go", "owner", "s1", t)
    _note(mac, "Done.", "session", "s1", t + 1000, whole_since=t)   # its Stop, s1 registered now: the session's view
    behind = _note(mac, _live(1), "live", "s1", t + 5000)
    _note(mac, "More.", "session", "s1", t + 5000)                 # a Stop that does not say its turn went whole
    interrupted = _note(mac, _live(2), "live", "s1", t + 7000)    # a turn the owner interrupted, its Stop never run
    other = _note(mac, _live(3), "live", "s2", t + 9000)          # another session's, not registered
    _note(mac, "Later.", "session", "s1", t + 9500, whole_since=t + 8000)   # the next turn's Stop, from its start
    covered = _note(mac, _live(4), "live", "s1", t + 10000)
    _note(mac, "Last.", "session", "s1", t + 10500, whole_since=t + 10000)
    nxt = _note(mac, _live(5), "live", "s1", t + 12000)           # the next turn's, its Stop still to come
    again = lambda q="": {e["seq"] for e in _timeline(client, owner, mac, q)["events"]    # noqa: E731
                          if e.get("posted_again") is True}
    assert again() == {first}, "a round posted before its session was registered, with its Stop's notes after"
    assert again("?session=s1") == {covered}, "no Stop before a round says it posted that round"
    live = {e["seq"] for q in ("", "?session=s1") for e in _timeline(client, owner, mac, q)["events"]
            if e.get("mirror") == "live"}
    assert {behind, interrupted, other, nxt} <= live


def test_the_cap_deletes_only_rounds_a_stop_posted_again_whole_the_oldest_first(client, owner, monkeypatch):
    """The cap deletes a round (mirror live) only where the ADE says a Stop posted it again
    (posted_again), the oldest first and only until the link's rounds are within LIVE_KEPT; a round behind a Stop's note
    that does not say its turn went whole, an earlier turn's, another session's, a turn's with no Stop yet, a question
    and any Stop's note it never deletes, however far past LIVE_KEPT they go (it does not bound those). The two
    sessions' rounds interleave, so a cap blind to sessions deletes the other's."""
    size = len(_live(0, pad=100))
    mac = paired(client, owner)
    t = (int(time.time()) - 3600) * 1000

    def kept():
        return {e["seq"] for e in _timeline(client, owner, mac)["events"]}

    r = post(mac, "question", "Which cap?", to="owner")
    assert r.status_code == 200, r.text
    asked = r.json()["seq"]
    r1 = _note(mac, _live(1, pad=100), "live", "s1", t + 1000)           # s1's first turn
    stops = [_note(mac, "go", "owner", "s1", t + 1000)]                   # its Stop does not say it went whole
    r2 = _note(mac, _live(2, pad=100), "live", "s2", t + 3000)           # s2's, after s1's second turn began
    stops.append(_note(mac, "Other.", "session", "s2", t + 3000))        # nor does s2's
    r3 = _note(mac, _live(3, pad=100), "live", "s1", t + 3000)           # s1's second turn, begun at t + 2000
    r4 = _note(mac, _live(4, pad=100), "live", "s1", t + 4000)
    stops.append(_note(mac, "Done.", "session", "s1", t + 4000, whole_since=t + 2000))
    monkeypatch.setattr(link, "LIVE_KEPT", 4 * size, raising=False)
    r5 = _note(mac, _live(5, pad=100), "live", "s2", t + 5000)           # five rounds: one goes
    seqs = kept()
    assert r3 not in seqs and {r1, r2, r4, r5} <= seqs, "the oldest round posted again goes first, and only it"
    monkeypatch.setattr(link, "LIVE_KEPT", 2 * size, raising=False)
    r6 = _note(mac, _live(6, pad=100), "live", "s2", t + 6000)
    seqs = kept()
    assert r4 not in seqs, "a round posted again goes once the cap needs room"
    assert {r1, r2, r5, r6} <= seqs, "past LIVE_KEPT, what no Stop posted again whole stays"
    assert set(stops) <= seqs and asked in seqs


def test_a_link_s_feed_reads_are_bounded_and_none_is_an_event(client, owner, monkeypatch):
    """A watcher reading its feed every 3 seconds costs the link no event of its share, and the
    server bounds a link's reads at FEED_READS_PER_MINUTE a minute, refusing more (429), and takes them again once the
    minute has passed."""
    monkeypatch.setattr(link, "FEED_READS_PER_MINUTE", 5, raising=False)
    t = [0.0]
    monkeypatch.setattr(link, "_feed_clock", lambda: t[0], raising=False)
    mac = paired(client, owner)
    other = paired(client, owner, "pc")
    def count():
        with store.connect() as db:
            return db.execute("SELECT COUNT(*) FROM ade_link_events WHERE link_id = ?", (mac.link_id,)).fetchone()[0]

    before = count()
    assert [mac.call("GET", "/feed").status_code for _ in range(6)] == [200] * 5 + [429]
    assert other.call("GET", "/feed").status_code == 200            # the bound is each link's own
    t[0] = 61.0
    assert mac.call("GET", "/feed").status_code == 200
    assert count() == before                                         # none of the reads is an event


def test_the_feed_bound_leaves_room_for_a_move_s_look_beside_the_watcher(client, owner, monkeypatch):
    """A move's look reads up to 50 pages of the feed back to back (ade_link.py, _start_move), a watcher 20 a minute,
    and its session's `next` a few more: all go within the bound, which is 120 reads a
    minute and not one more (75 alone would pass any bound of 75 or more, or none)."""
    t = [0.0]
    monkeypatch.setattr(link, "_feed_clock", lambda: t[0], raising=False)
    mac = paired(client, owner)
    assert [mac.call("GET", "/feed").status_code for _ in range(121)] == [200] * 120 + [429]


@pytest.mark.parametrize("method,path,body,count", [("GET", "/feed", None, 4), ("GET", "/state", None, 5),
                                                    ("POST", "/heartbeat", {}, 1)], ids=["feed", "state", "heartbeat"])
def test_each_read_a_watcher_makes_goes_by_an_index(client, owner, monkeypatch, method, path, body, count):
    """A watcher reads its link's feed every 3 seconds and its state every other round, and beats every 5 minutes
    (ade_link.py, WATCH_EVERY, STATE_FRESH, WATCH_HEARTBEAT), so each read these make in the ADE's store goes by an
    index: none scans a table of the deployment's links or events, with an index or without (SCAN ... USING
    INDEX is a scan too, and /state is read as well). Every SELECT the route makes, counted, whatever its table, and a
    scan by any name (a scan under an alias, `SCAN e`, is one too, and every read is traced, not one alone)."""
    import sqlite3
    mac = paired(client, owner)
    seen, real = [], sqlite3.connect

    def traced(*a, **k):
        db = real(*a, **k)
        db.set_trace_callback(seen.append)
        return db

    monkeypatch.setattr(store.sqlite3, "connect", traced)
    r = mac.call(method, path, body) if body is not None else mac.call(method, path)
    assert r.status_code == 200, r.text
    monkeypatch.setattr(store.sqlite3, "connect", real)
    reads = [q for q in seen if q.lstrip().upper().startswith("SELECT")]
    plans = {}
    with store.connect() as db:
        for q in reads:
            plans[q] = [r[-1] for r in db.execute("EXPLAIN QUERY PLAN " + q, [None] * q.count("?"))]
    scans = {q: p for q, p in plans.items() if any(re.match(r"SCAN (?!CONSTANT ROW)", step) for step in p)}
    assert len(reads) == count and not scans, (len(reads), scans, plans)
