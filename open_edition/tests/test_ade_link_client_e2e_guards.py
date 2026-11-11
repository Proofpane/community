"""End-to-end encryption, the link client (open_edition/ade_link.py): what it holds to. A fork at the
statement the link holds holds; the server's word is never lowered by a race or the wall clock; a lock that cannot be
taken holds; a link with no pinned keys still reads what the server says; what the link took is bound to its account
and its server; a statement that appears later than it could have been made and held is not acted on; the remote
command's guard holds `e2e --reset`; pair checks --e2e and --keys before the ticket is spent; the mirror loses no turn
when the statements' read after a 409 runs out of time; and the sensitive judgement of posts, checkpoints and moves
fails closed and stays. Against the stand-in of the server's side (e2e_link_stand_in)."""

import io
import json
import os
import sys
import threading
import time

import pytest

import ade_link
from e2e_link_stand_in import (ACCOUNT, LINK_ID, LinkStandIn, a_link, key_id, open_envelope, owner_key, raw_public,
                               slot)
from test_ade_link_client import run
from test_ade_link_client_e2e_sensitive import machine, rules
from test_ade_link_history import _tool, _write


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path):
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "ADE_OWNER_KEYS", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))           # no pin outside the server, no Claude Code record
    # Claude Code's records of its processes, read from the password database's home: the test's own home here
    monkeypatch.setattr(ade_link, "_take_sessions_dir",
                        lambda: os.path.join(os.environ["HOME"], ".claude", "sessions"))
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)    # a retry waits for nothing
    monkeypatch.setattr(ade_link, "_moves_soon", lambda link: None)
    monkeypatch.setattr(ade_link, "_history_soon", lambda link, every=None: None)


@pytest.fixture
def ade():
    s = LinkStandIn()
    yield s
    s.close()


def held(d):
    with open(os.path.join(d, "e2e.json"), encoding="utf-8") as fh:
        return json.load(fh)


def keep(d, data):
    with open(os.path.join(d, "e2e.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def seqs(d):
    return [json.loads(s["payload"])["seq"] for s in held(d)["statements"]]


def gets(ade):
    return [p for m, p, _ in ade.requests if m == "GET" and p == "/api/ade/link/e2e"]


def opened(e, private):
    return open_envelope(e["text"], slot(ACCOUNT, LINK_ID, "event", e["kind"], e.get("mirror"), e.get("ref"),
                                         e["client_id"]), private)


# ---- a fork at the link's own statement, and the server's word ------------------------------------------------------

def test_a_fork_at_the_statement_the_link_holds_as_off_holds_rather_than_go_in_the_clear(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state("e2e-off")
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0 and ade.events[0]["text"] == "one"
    ade.owner.statements.clear()
    ade.owner.state()                                   # the ADE's statement 1 is a key now: a fork at the link's own
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "the ADE's statement 1 is not the one this link took" in out and seqs(d) == [1]
    code, _, err = run(capsys, "--dir", d, "post", "step", "two")
    assert code == 2 and "statement 1 is not the one this link took" in err and len(ade.posted()) == 1


def test_the_ade_s_word_that_its_newest_statement_is_a_key_at_the_link_s_own_seq_holds(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state("e2e-off")
    ade.say_e2e = True
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0           # read: off, in the clear
    ade.owner.statements.clear()
    ade.owner.state()                                                         # the ADE's statement 1 a key now
    assert run(capsys, "--dir", d, "post", "step", "two")[0] == 0           # within the minute; its answer says so
    assert held(d)["server"] == {"seq": 1, "on": True}
    code, _, err = run(capsys, "--dir", d, "post", "step", "three")
    assert code == 2 and "encryption may be on" in err and len(ade.posted()) == 2


def test_a_pair_command_naming_a_key_at_the_statement_the_link_holds_as_off_holds(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state("e2e-off")
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    with open(os.path.join(d, "e2e_anchor.json"), encoding="utf-8") as fh:
        anchor = json.load(fh)
    anchor["floor"] = {"seq": 1, "key_id": "0f" * 16}
    with open(os.path.join(d, "e2e_anchor.json"), "w", encoding="utf-8") as fh:
        json.dump(anchor, fh)
    assert ade_link._e2e_mode(ade_link.Link(d), refresh=False)[0] == "hold"


def test_a_fork_holds_though_the_ade_s_own_newest_turns_encryption_off(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    ade.owner.state("e2e-off")
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0 and ade.events[0]["text"] == "one"
    ade.owner.statements.pop()
    ade.owner.state("e2e-off")                          # another statement 2, off too: a fork all the same
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "the ADE's statement 2 is not the one this link took" in out
    code, _, err = run(capsys, "--dir", d, "post", "step", "two")
    assert code == 2 and len(ade.posted()) == 1


def test_an_answer_s_older_word_written_after_another_process_kept_a_newer_one_does_not_lower_it(ade, capsys,
                                                                                                  tmp_path,
                                                                                                  monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    real = ade_link._e2e_lock

    def another_process_first(link):
        data = held(d)
        data["server"] = {"seq": 3, "on": True}         # kept by another process while this one waited for the lock
        keep(d, data)
        return real(link)
    monkeypatch.setattr(ade_link, "_e2e_lock", another_process_first)
    ade_link._e2e_seen(ade_link.Link(d), {"e2e": {"seq": 1, "key_id": None}})
    assert held(d)["server"] == {"seq": 3, "on": True}


def test_a_409_s_word_is_kept_where_the_statements_cannot_be_read_again(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0                            # nothing stated yet
    ade.owner.state()
    ade.require = True
    real = ade.route
    monkeypatch.setattr(ade, "route", lambda method, path, body: (503, {"detail": "busy"}) if path.endswith("/e2e")
                        else real(method, path, body))
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 2 and len(ade.posted()) == 1   # in the clear: 409
    assert held(d)["server"] == {"seq": 1, "on": True}
    assert run(capsys, "--dir", d, "post", "step", "two")[0] == 2 and len(ade.posted()) == 1   # held before it goes


def test_the_ade_s_word_is_never_lowered_by_an_answer_that_names_an_older_statement(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0                            # no statement yet
    link = ade_link.Link(d)
    ade_link._e2e_seen(link, {"e2e": {"seq": 2, "key_id": "ab" * 16}})
    ade_link._e2e_seen(link, {"e2e": {"seq": 1, "key_id": None}})            # an answer sent before the other's
    assert held(d)["server"] == {"seq": 2, "on": True} and held(d)["fetched_at"] == 0
    ade_link._e2e_seen(link, {"e2e": {"seq": 2, "key_id": None}})            # the same statement: the newer word
    assert held(d)["server"] == {"seq": 2, "on": False}


def test_a_read_that_lists_less_than_the_ade_said_keeps_its_word_and_reads_again_at_the_next_send(ade, capsys,
                                                                                                  tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, first = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    ade_link._e2e_seen(ade_link.Link(d), {"e2e": {"seq": 2, "key_id": "ab" * 16}})
    assert run(capsys, "--dir", d, "e2e")[0] == 0                            # a read begun before statement 2 was there
    assert held(d)["server"] == {"seq": 2, "on": True} and held(d)["fetched_at"] == 0
    before = len(gets(ade))
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert len(gets(ade)) == before + 1 and opened(ade.events[0], first)["text"] == "x"


def test_a_read_kept_with_a_time_past_this_machine_s_clock_is_read_again_and_written_over(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    data = held(d)
    data["fetched_at"] = int(time.time()) + 3600                             # a clock that was set back since
    keep(d, data)
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert seqs(d) == [1] and opened(ade.events[0], key)["text"] == "x"


# ---- a statement that comes later than it could have been made and held -------------------------------------------

DAY = 86400


@pytest.mark.parametrize("purpose", ["e2e-off", "e2e-key"])
def test_a_statement_written_in_after_the_link_read_the_list_without_it_past_its_window_is_not_acted_on(
        ade, capsys, tmp_path, purpose):
    d = a_link(ade, tmp_path / "mac")
    _, first = ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0 and opened(ade.events[0], first)["text"] == "one"
    made = int(time.time()) - 3 * DAY                   # cancelled three days ago, and written in now
    ade.owner.state(purpose, created_at=made, expires_at=made + 600)
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert seqs(d) == [1, 2] and "came into the ADE's list after this link had read the list without it" in out
    code, _, err = run(capsys, "--dir", d, "post", "step", "two")
    assert code == 2 and "came into the ADE's list" in err and len(ade.posted()) == 1
    _, third = ade.owner.state()                        # a later statement chains through it
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1, 2, 3]
    assert run(capsys, "--dir", d, "post", "step", "three")[0] == 0 and opened(ade.events[1], third)["text"] == "three"


def test_a_statement_within_its_window_is_acted_on_however_long_ago_it_was_made(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    made = int(time.time()) - DAY                       # held a day (confirmed by the recovery code), stated just now
    _, key = ade.owner.state(created_at=made, expires_at=made + 600)
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and opened(ade.events[0], key)["text"] == "x"


def test_a_statement_s_life_counts_no_longer_than_the_server_takes_one_in(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    made = int(time.time()) - 3 * DAY
    ade.owner.state("e2e-off", created_at=made, expires_at=made + 30 * DAY)   # signed with a life the server refuses
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1, 2]
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "came into the ADE's list" in err


def test_a_read_dated_past_this_one_says_nothing_of_when_a_statement_came(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    data = held(d)
    data.update(read_at=int(time.time()) + 3 * DAY, fetched_at=0)      # the clock was set back since that read
    keep(d, data)
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and opened(ade.events[0], key)["text"] == "x"


def test_a_link_s_first_read_and_a_read_after_it_was_idle_act_on_what_came_meanwhile(ade, capsys, tmp_path):
    made = int(time.time()) - 30 * DAY
    _, first = ade.owner.state(created_at=made, expires_at=made + 600)
    d = a_link(ade, tmp_path / "mac")                   # paired a month after it: its first read
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0 and opened(ade.events[0], first)["text"] == "one"
    assert run(capsys, "--dir", d, "e2e")[0] == 0      # read again: when it first saw it stands
    assert run(capsys, "--dir", d, "post", "step", "again")[0] == 0
    data = held(d)
    data.update(read_at=int(time.time()) - 5 * DAY, fetched_at=0)     # then idle five days
    keep(d, data)
    made = int(time.time()) - 3 * DAY                   # a rotation while it was idle
    _, second = ade.owner.state(created_at=made, expires_at=made + 600)
    assert run(capsys, "--dir", d, "post", "step", "two")[0] == 0 and opened(ade.events[2], second)["text"] == "two"


def serving(ade, monkeypatch, listed):
    """The ADE's list of statements served as `listed` from now on; every other route as it is."""
    real = ade.route
    monkeypatch.setattr(ade, "route", lambda method, path, body: (
        (200, {"statements": list(listed)}) if path.endswith("/e2e") and method == "GET" else real(method, path, body)))
    return real


def test_another_item_served_at_a_statement_s_seq_is_not_its_first_sight(ade, capsys, tmp_path, monkeypatch):
    """A first sight is noted by the statement's content: an item the ADE served at seq 2 within a statement's window
    is not that statement's first sight, so the statement written in later, past its window, is not acted on."""
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    made = int(time.time()) - 10 * DAY                  # signed ten days ago, then cancelled
    data = held(d)
    data.update(read_at=made - 100, fetched_at=0)       # this link last read the list just before it was made
    keep(d, data)
    decoy = {"payload": json.dumps({"seq": 2, "created_at": made}), "credential": None}
    real = serving(ade, monkeypatch, [*ade.owner.statements, decoy])
    run(capsys, "--dir", d, "e2e")                      # another item at seq 2, read within the statement's window
    monkeypatch.setattr(ade, "route", real)
    ade.owner.state("e2e-off", created_at=made, expires_at=made + 600)    # written in now
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert seqs(d) == [1, 2] and "came into the ADE's list after this link had read the list without it" in out
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "came into the ADE's list" in err and not ade.posted()


def test_a_statement_dated_ahead_of_its_first_sight_is_not_acted_on_until_a_later_one_chains_through_it(
        ade, capsys, tmp_path):
    """A statement that names a time later than the read this link first saw it in, by more than the clock skew
    allowed, was dated ahead, as a time the ADE gave the page ahead of the clock would date it: not acted on, even at
    this link's first read, until a later statement chains through it."""
    ade.owner.state()
    ahead = int(time.time()) + 3650 * DAY               # the ADE gave the page a time ten years ahead
    ade.owner.state("e2e-off", created_at=ahead, expires_at=ahead + 600)
    d = a_link(ade, tmp_path / "mac")                   # its first read
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert seqs(d) == [1, 2] and "names a time later than this link first saw it" in out
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "names a time later than this link first saw it" in err and not ade.posted()
    _, third = ade.owner.state()                        # a later statement chains through it
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1, 2, 3]
    assert run(capsys, "--dir", d, "post", "step", "y")[0] == 0 and opened(ade.events[0], third)["text"] == "y"


def test_a_statement_dated_ahead_within_the_skew_allowed_is_acted_on(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    soon = int(time.time()) + ade_link.SKEW_AHEAD - 60  # a server's clock a little ahead of this machine's
    _, key = ade.owner.state(created_at=soon, expires_at=soon + 600)
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and opened(ade.events[0], key)["text"] == "x"


def test_items_served_past_a_statement_the_link_holds_do_not_push_out_its_first_sight(ade, capsys, tmp_path,
                                                                                         monkeypatch):
    """The first sights kept are the newest by seq, but never those of the statements this link holds: items the ADE
    serves past one held as late cannot drop when it was first seen, so it stays held once the ADE lists it no more."""
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0
    made = int(time.time()) - 3 * DAY
    ade.owner.state("e2e-off", created_at=made, expires_at=made + 600)    # cancelled three days ago, written in now
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert seqs(d) == [1, 2] and "came into the ADE's list" in out
    listed = list(ade.owner.statements)
    flood = [{"payload": json.dumps({"seq": 10 ** 6 + n}), "credential": None}
             for n in range(ade_link.E2E_SEEN_MOST + 1)]
    serving(ade, monkeypatch, listed + flood)
    run(capsys, "--dir", d, "e2e")                      # more items past it than the first sights kept
    seen = held(d)["seen"]
    assert ade_link._e2e_hash(listed[1]["payload"]) in seen and len(seen) == ade_link.E2E_SEEN_MOST + 2
    serving(ade, monkeypatch, listed[:1])               # then the list without it
    code, _, err = run(capsys, "--dir", d, "post", "step", "two")
    assert code == 2 and "came into the ADE's list" in err and len(ade.posted()) == 1


def test_items_served_past_a_late_statement_in_the_read_that_takes_it_do_not_push_out_its_first_sight(
        ade, capsys, tmp_path, monkeypatch):
    """The first sights are pruned once the read's statements are taken, never before: a late statement served with
    more items past it than are kept, in the read that takes it, is taken with its first sight and held."""
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0
    made = int(time.time()) - 3 * DAY
    ade.owner.state("e2e-off", created_at=made, expires_at=made + 600)    # cancelled three days ago, written in now
    flood = [{"payload": json.dumps({"seq": 10 ** 6 + n}), "credential": None}
             for n in range(ade_link.E2E_SEEN_MOST + 1)]
    serving(ade, monkeypatch, list(ade.owner.statements) + flood)
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert seqs(d) == [1, 2] and "came into the ADE's list" in out
    code, _, err = run(capsys, "--dir", d, "post", "step", "two")
    assert code == 2 and "came into the ADE's list" in err and len(ade.posted()) == 1


def test_the_first_sights_kept_beside_those_of_the_statements_held_are_the_newest_by_seq():
    held_text = json.dumps({"seq": 1})
    seen = {ade_link._e2e_hash(held_text): [5, None, 1]}
    for n in range(ade_link.E2E_SEEN_MOST + 1):         # the higher the seq, the earlier its first sight
        seen["%064x" % n] = [10 ** 6 - n, None, 2 + n]
    kept = ade_link._e2e_pruned({"seen": seen, "statements": [{"payload": held_text}]})["seen"]
    assert len(kept) == ade_link.E2E_SEEN_MOST + 1 and ade_link._e2e_hash(held_text) in kept
    assert "%064x" % 0 not in kept and "%064x" % ade_link.E2E_SEEN_MOST in kept


def test_a_first_sight_a_client_noted_by_seq_goes_to_the_statement_served_at_that_seq(ade, capsys, tmp_path):
    """e2e.json written by a client before first sights were noted by content keeps them by seq: the first payload
    served at that seq takes the first sight noted there, so a statement seen long ago is not taken for one seen now."""
    made = int(time.time()) - 10 * DAY
    _, key = ade.owner.state(created_at=made, expires_at=made + 600)
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    data = held(d)
    digest = ade_link._e2e_hash(data["statements"][0]["payload"])
    data.update(seen={"1": [made + 10, None]}, read_at=int(time.time()) - 60, fetched_at=0)
    keep(d, data)
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and opened(ade.events[0], key)["text"] == "x"
    assert held(d)["seen"] == {digest: [made + 10, None, 1]}


def test_the_late_rule_reads_a_first_sight_noted_by_seq_until_a_read_moves_it():
    made = int(time.time()) - 3 * DAY
    text = json.dumps({"seq": 2, "created_at": made, "expires_at": made + 600})
    now = int(time.time())
    assert "came into the ADE's list" in ade_link._e2e_late({"seen": {"2": [now, now - 60]}}, text)
    assert ade_link._e2e_late({"seen": {ade_link._e2e_hash(text): [now, now - 60, 2]}}, text) is not None
    assert ade_link._e2e_late({"seen": {ade_link._e2e_hash(text): [now, None, 2]}}, text) is None
    assert ade_link._e2e_late({"seen": {}}, text) is None


@pytest.mark.parametrize("key, noted, ok", [
    ("a" * 64, [1, None, 2], True),
    ("0f" * 32, [1, 2, 3], True),
    ("1", [1, None], True),                             # by seq, as a client before content was noted kept it
    ("12", [1, 2], True),
    ("a" * 64, [1, None], False),                       # by content, without its seq
    ("1", [1, None, 2], False),                         # by seq, with one
    ("A" * 64, [1, None, 2], False),
    ("a" * 63, [1, None, 2], False),
    ("a" * 65, [1, None, 2], False),
    ("a" * 64, [1, None, 0], False),
    ("a" * 64, [1, None, True], False),
    ("a" * 64, [1, None, "2"], False),
    ("a" * 64, ["1", None, 2], False),
    ("a" * 64, [1, "0", 2], False),
    ("a" * 64, [1, None, 2, 3], False),
    ("1", [1, None, 2, 3], False),
    ("x", [1, None], False),
    ("1", (1, None), False),
])
def test_a_first_sight_e2e_json_keeps_is_one_this_client_writes(key, noted, ok):
    assert bool(ade_link._e2e_seen_ok(key, noted)) is ok


# ---- the statements, page by page -----------------------------------------------------------------------------------

def statement_reads(ade):
    return [q for m, p, q in ade.queries if m == "GET" and p == "/api/ade/link/e2e"]


def test_the_statements_are_read_page_by_page_while_the_ade_says_more(ade, capsys, tmp_path):
    ade.e2e_page = ade_link.E2E_PAGE
    d = a_link(ade, tmp_path / "mac")
    for _ in range(ade_link.E2E_PAGE):
        ade.owner.state("e2e-off")
    _, key = ade.owner.state()                          # the newest, past the first page
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert seqs(d)[-1] == ade_link.E2E_PAGE + 1 and opened(ade.events[0], key)["text"] == "x"
    # the first page's request says the forms this link can seal, once
    seals = ",".join(str(f) for f in ade_link._e2e_seals())
    assert statement_reads(ade) == [{"after": "0", "limit": str(ade_link.E2E_PAGE), "e2e_seal": seals},
                                    {"after": str(ade_link.E2E_PAGE), "limit": str(ade_link.E2E_PAGE)}]


def test_a_page_that_says_more_and_does_not_move_on_is_no_list(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    real = ade.route
    monkeypatch.setattr(ade, "route", lambda method, path, body: (
        (200, {"statements": list(ade.owner.statements), "more": True}) if path.endswith("/e2e") and method == "GET"
        else real(method, path, body)))
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert len(statement_reads(ade)) == 2 and not os.path.exists(os.path.join(d, "e2e.json"))   # nothing taken


@pytest.mark.parametrize("page", [[], [{"payload": "not a statement", "credential": None}]])
def test_a_page_that_says_more_with_no_statement_to_go_on_from_is_no_list(ade, capsys, tmp_path, monkeypatch, page):
    """A page that says more but is empty, or whose last statement cannot be read for its seq, gives the link nothing
    to ask the next page after: the pages read before it are not taken for the whole."""
    ade.e2e_page = 1
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    ade.owner.state("e2e-off")
    real = ade.route
    monkeypatch.setattr(ade, "route", lambda method, path, body: (
        (200, {"statements": list(page), "more": True}) if path.endswith("/e2e") and method == "GET"
        and ade.query.get("after") != "0" else real(method, path, body)))
    run(capsys, "--dir", d, "e2e")
    assert len(statement_reads(ade)) == 2 and not os.path.exists(os.path.join(d, "e2e.json"))   # nothing taken


def test_a_later_page_the_ade_does_not_find_is_no_list(ade, capsys, tmp_path, monkeypatch):
    """Only the first page's 404 is an ADE without the route, which states none: a 404 for a later page leaves the
    list unread, never taken as one with no statements in it."""
    ade.e2e_page = 1
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    ade.owner.state("e2e-off")
    real = ade.route
    monkeypatch.setattr(ade, "route", lambda method, path, body: (
        (404, {"detail": "Not Found"}) if path.endswith("/e2e") and method == "GET" and ade.query.get("after") != "0"
        else real(method, path, body)))
    run(capsys, "--dir", d, "e2e")
    assert len(statement_reads(ade)) == 2 and not os.path.exists(os.path.join(d, "e2e.json"))   # nothing taken


def test_a_later_page_that_is_not_a_list_is_no_list(ade, capsys, tmp_path, monkeypatch):
    """The first page is never taken for the whole where the next one cannot be read as a page."""
    ade.e2e_page = 1
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    ade.owner.state("e2e-off")
    real = ade.route
    monkeypatch.setattr(ade, "route", lambda method, path, body: (
        (200, {"statements": None, "more": False}) if path.endswith("/e2e") and method == "GET"
        and ade.query.get("after") != "0" else real(method, path, body)))
    run(capsys, "--dir", d, "e2e")
    assert len(statement_reads(ade)) == 2 and not os.path.exists(os.path.join(d, "e2e.json"))   # nothing taken


def test_a_list_longer_than_the_pages_a_link_reads_is_not_taken_for_the_whole(ade, capsys, tmp_path, monkeypatch):
    ade.e2e_page = 1
    d = a_link(ade, tmp_path / "mac")
    for _ in range(3):
        ade.owner.state()
    monkeypatch.setattr(ade_link, "E2E_PAGES_MOST", 2)
    run(capsys, "--dir", d, "e2e")
    assert len(statement_reads(ade)) == 2 and not os.path.exists(os.path.join(d, "e2e.json"))   # nothing taken
    monkeypatch.setattr(ade_link, "E2E_PAGES_MOST", 3)
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1, 2, 3]


# ---- a link with no pinned key ------------------------------------------------------------------------------------

def test_a_link_with_no_pinned_key_reads_the_ade_s_word_and_holds_its_first_text(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac", pinned=False)
    ade.owner.state()                                   # turned on; the ADE does not refuse a text in the clear yet
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "no owner keys pinned" in err and ade.posted() == []
    assert held(d)["server"] == {"seq": 1, "on": True} and not held(d).get("statements")


def test_a_list_a_link_with_no_pinned_key_cannot_read_holds_it(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac", pinned=False)
    ade.owner.statements.append({"payload": "not a statement", "credential": None})
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 2 and ade.posted() == []
    assert held(d)["server"] == {"seq": 1, "on": True}


def test_the_word_read_unverified_is_the_highest_statement_s_and_on_for_what_cannot_be_read():
    def item(seq, purpose):
        return {"payload": json.dumps({"seq": seq, "purpose": purpose}), "credential": None}
    assert ade_link._e2e_unverified_word([item(2, "e2e-key"), item(1, "e2e-off")]) == {"seq": 2, "on": True}
    assert ade_link._e2e_unverified_word([item(1, "e2e-key"), item(2, "e2e-off")]) == {"seq": 2, "on": False}
    for bad in (item(1, "other"), item(0, "e2e-off"), item("1", "e2e-off"), {"payload": "{"}):
        assert ade_link._e2e_unverified_word([item(1, "e2e-off"), bad]) == {"seq": 2, "on": True}, bad


def test_a_link_with_no_pinned_key_sends_in_the_clear_where_the_ade_says_encryption_is_off(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac", pinned=False)
    ade.owner.state()
    ade.owner.state("e2e-off")
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and ade.events[0]["text"] == "x"
    assert held(d)["server"] == {"seq": 2, "on": False} and not held(d).get("statements")


def test_a_statement_read_before_the_link_could_take_it_keeps_its_first_sight(ade, capsys, tmp_path):
    """A link with no pinned key notes when it first saw each statement all the same, and keeps it: pinned later, it
    takes a statement the list held all along as one it saw then, not as one written in after it read the list."""
    made = int(time.time()) - 10 * DAY
    _, key = ade.owner.state(created_at=made, expires_at=made + 600)
    d = a_link(ade, tmp_path / "mac", pinned=False)
    run(capsys, "--dir", d, "e2e")                      # read, nothing taken: no key pinned to verify it by
    assert not held(d).get("statements") and list(held(d)["seen"]) == [ade_link._e2e_hash(
        ade.owner.statements[0]["payload"])]
    with open(os.path.join(d, "owner_keys.json"), "w") as fh:          # pinned later
        json.dump({"account": ACCOUNT, "keys": [owner_key(ade.owner.device)], "source": "pair",
                   "pinned_at": int(time.time())}, fh)
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and opened(ade.events[0], key)["text"] == "x"


def test_a_link_with_no_pinned_key_keeps_no_more_first_sights_than_it_may(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac", pinned=False)
    flood = [{"payload": json.dumps({"seq": 10 ** 6 + n}), "credential": None}
             for n in range(ade_link.E2E_SEEN_MOST + 1)]
    serving(ade, monkeypatch, flood)
    run(capsys, "--dir", d, "e2e")
    seen = held(d)["seen"]
    assert len(seen) == ade_link.E2E_SEEN_MOST and ade_link._e2e_hash(flood[0]["payload"]) not in seen


# ---- bound to the link's server and account -------------------------------------------------------------------------

def pair_again(capsys, d, url, ticket="PP-AAAAAAAA"):
    return run(capsys, "pair", "--url", url, "--ticket", ticket, "--name", "mac", "--kind", "local", "--dir", d)


def test_a_pair_run_again_reads_the_newest_pairing_its_key_made_and_sends_each_ticket_once(ade, capsys, tmp_path):
    # the stand-in reads where a key's pairing stands as the ADE does (GET /ask): by the ticket's digest, in upper case
    d = tmp_path / "mac"
    assert pair_again(capsys, d, ade.url, ticket="pp-aaaaaaaa")[0] == 0         # the ticket typed in lower case
    code, out, _ = pair_again(capsys, d, ade.url)                               # run again, as the page shows it
    assert code == 0 and "this session's key paired as mac before" in out
    assert pair_again(capsys, d, ade.url, ticket="PP-BBBBBBBB")[0] == 0         # another ticket: a pairing anew
    code, out, _ = pair_again(capsys, d, ade.url, ticket="PP-BBBBBBBB")         # run again: the newest pairing
    assert code == 0 and "this session's key paired as mac before" in out
    assert [p["ticket"] for p in ade.posted(path="/api/ade/link/pair")] == ["pp-aaaaaaaa", "PP-BBBBBBBB"]
    with open(os.path.join(d, "config.json"), encoding="utf-8") as fh:
        assert json.load(fh) == {"url": ade.url, "name": "mac", "kind": "local", "link_id": LINK_ID,
                                 "account": ACCOUNT}


@pytest.mark.parametrize("unbound", [False, True])
def test_a_directory_linked_again_to_another_server_holds_what_it_took_for_the_first(ade, capsys, tmp_path, unbound):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0 and opened(ade.events[0], key)["text"] == "one"
    if unbound:                                         # written before the binding was: its statements say whose
        for name in ("e2e.json", "e2e_anchor.json"):
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                value = json.load(fh)
            for k in ("deployment", "account"):
                value.pop(k)
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                json.dump(value, fh)
    other = LinkStandIn()
    try:
        code, _, err = pair_again(capsys, d, other.url)
        assert code == 0 and "another server or account" in err
        code, _, err = run(capsys, "--dir", d, "post", "step", "two")
        assert code == 2 and "another server or account" in err and other.posted() == []
        assert "e2e --reset drops it, on the owner's word, so this link reads the statements of the account" in err
        assert run(capsys, "--dir", d, "e2e", "--reset")[0] == 0               # on the owner's word
        assert run(capsys, "--dir", d, "post", "step", "three")[0] == 0 and other.events[0]["text"] == "three"
    finally:
        other.close()


def test_the_word_a_server_gave_is_not_kept_for_another_one_a_directory_is_linked_to_again(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac", pinned=False)
    ade.owner.state()
    ade.owner.state("e2e-off")
    ade.owner.state()
    ade.owner.state("e2e-off")
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0 and held(d)["server"] == {"seq": 4, "on": False}
    other = LinkStandIn()
    try:
        other.owner.state()                             # on, at a seq below the first server's word
        code, _, err = pair_again(capsys, d, other.url)
        assert code == 0
        code, _, err = run(capsys, "--dir", d, "post", "step", "two")
        assert code == 2 and other.posted() == []
    finally:
        other.close()


def test_the_word_kept_for_one_account_is_not_kept_for_another_a_directory_is_linked_for_again(ade, capsys, tmp_path,
                                                                                                monkeypatch):
    d = tmp_path / "mac"
    ade.owner.state()
    ade.owner.state("e2e-off")
    ade.owner.state()
    ade.owner.state("e2e-off")
    assert pair_again(capsys, d, ade.url)[0] == 0       # no key pinned: the account the pairing named, kept
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0 and held(d)["server"] == {"seq": 4, "on": False}
    monkeypatch.setattr(ade, "owner_keys", lambda: {"account": "acct-e2e-0002",
                                                    "passkeys": [owner_key(ade.owner.device)]})
    assert pair_again(capsys, d, ade.url, ticket="PP-BBBBBBBB")[0] == 0     # the other account's page's ticket
    ade.owner.statements.clear()
    ade.owner.state()                                   # the other account's: on, at a seq below the first one's word
    code, _, err = run(capsys, "--dir", d, "post", "step", "two")
    assert code == 2 and len(ade.posted()) == 1


def test_a_directory_linked_again_for_another_account_holds_what_it_took_for_the_first(ade, capsys, tmp_path,
                                                                                        monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0
    monkeypatch.setattr(ade, "owner_keys", lambda: {"account": "acct-e2e-0002",
                                                    "passkeys": [owner_key(ade.owner.device)]})
    code, _, err = pair_again(capsys, d, ade.url)
    assert code == 0 and "another server or account" in err
    with open(os.path.join(d, "config.json"), encoding="utf-8") as fh:
        assert json.load(fh)["account"] == "acct-e2e-0002"
    code, _, err = run(capsys, "--dir", d, "post", "step", "two")
    assert code == 2 and "another server or account" in err and len(ade.posted()) == 1


# ---- the mirror's place after a 409 -------------------------------------------------------------------------------

TURN = [{"type": "user", "uuid": "u1", "origin": {"kind": "human"}, "turnOrigin": "human",
         "timestamp": "2026-10-03T10:00:00Z", "message": {"role": "user", "content": [{"type": "text", "text": "go"}]}},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-10-03T10:00:05Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}}]


def stop(monkeypatch, capsys, d, path):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(path), "session_id": "s1"})))
    return run(capsys, "--dir", d, "mirror", "reply")


def test_a_409_whose_read_of_the_statements_runs_past_the_stop_s_time_loses_no_turn(ade, capsys, tmp_path,
                                                                                    monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    real, turned = ade.route, []

    def turned_on_after_the_first(method, path, body):
        answer = real(method, path, body)
        if path.endswith("/events") and not turned:
            turned.append(ade.owner.state())
            ade.require = True
        return answer
    monkeypatch.setattr(ade, "route", turned_on_after_the_first)
    gate, read = threading.Event(), threading.Event()

    def read_late(link, e, mode, timeout=None):
        # the statements' read after the 409 answers only once the Stop's time is up, with the key to seal to
        gate.wait(30)
        (_, private), = turned
        public = raw_public(private)
        read.set()
        return ("seal", public, key_id(public), ACCOUNT)
    monkeypatch.setattr(ade_link, "_e2e_refused", read_late)
    monkeypatch.setattr(ade_link, "MIRROR_DEADLINE", 1)
    # the mirror's own clock stands still: the thread's deadline never passes, only the hook's wait for it does
    monkeypatch.setattr(ade_link, "_mirror_clock", lambda: 0.0)
    path = tmp_path / "t.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in TURN))
    try:
        assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    finally:
        gate.set()
    assert read.wait(5)
    time.sleep(0.5)                                     # where the Stop's thread went on, its post would have come
    assert [e["text"] for e in ade.events] == ["go"]    # once the time is up nothing more is posted
    with open(os.path.join(d, "mirror.json"), encoding="utf-8") as fh:
        assert json.load(fh)["s1"] == "u1"                  # back to the end of what went: the rest is a later Stop's
    with open(os.path.join(d, "mirror.log"), encoding="utf-8") as fh:
        assert "what did not go is a later Stop's" in fh.read()


# ---- the sensitive judgement of posts, checkpoints and moves, failing closed and staying ----------------------------

@pytest.fixture
def ruled(tmp_path, ade, capsys, monkeypatch):
    """A link whose history.json names ~/vault sensitive, its History list read once: s-vault and its fork touched it,
    s-plain did not; the command runs in a plain folder."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    d = a_link(ade, tmp_path / "mac")
    machine(home)
    rules(d, {"projects": "all", "sensitive": ["~/vault"]})
    assert run(capsys, "--dir", d, "history")[0] == 0
    monkeypatch.chdir(home / "work" / "app")
    return d


def record(folder, pid, session, start=None):
    """Claude Code's record of process `pid` running `session`, with the process's own start unless `start` says."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": session,
                                                    "procStart": start or ade_link._process_start(pid)}))


def post(capsys, d, *argv):
    return run(capsys, "--dir", d, "post", *(argv or ("note", "the plan", "--to", "all")))


@pytest.fixture
def ancestors(monkeypatch):
    """This test's process stands for the post's parent (a Claude Code process, where a record names it); its own parent
    is the one above."""
    me = os.getpid()
    monkeypatch.setattr(os, "getppid", lambda: me)
    return me, ade_link._parent_of(me)


def test_a_record_that_cannot_be_confirmed_or_that_the_session_variable_contradicts_refuses_the_post(
        ruled, ade, capsys, tmp_path, monkeypatch, ancestors):
    me, _ = ancestors
    sessions = tmp_path / "home" / ".claude" / "sessions"
    record(sessions, me, "s-plain", start="Thu Jan  1 00:00:00 1970")      # not the process's start
    code, _, err = post(capsys, ruled)
    assert code == 2 and "cannot be confirmed as that process's" in err
    record(sessions, me, "s-plain")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-other")
    code, _, err = post(capsys, ruled)
    assert code == 2 and "CLAUDE_CODE_SESSION_ID names another session" in err
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert post(capsys, ruled)[0] == 0 and ade.events[0]["text"] == "the plan"


def test_the_session_variable_s_session_is_judged_where_no_record_names_one(ruled, ade, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-vault")
    code, _, err = post(capsys, ruled)
    assert code == 2 and "this session touched a folder history.json names sensitive" in err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-nowhere")                 # no transcript to judge
    code, _, err = post(capsys, ruled)
    assert code == 2 and "transcript cannot be found" in err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "../s-plain")               # not a session's id at all
    code, _, err = post(capsys, ruled)
    assert code == 2 and "CLAUDE_CODE_SESSION_ID names no session this client can read" in err and ade.events == []


def test_a_record_that_cannot_be_read_refuses_the_post(ruled, ade, capsys, tmp_path, ancestors):
    me, _ = ancestors
    sessions = tmp_path / "home" / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / f"{me}.json").write_text("{cut")
    code, _, err = post(capsys, ruled)
    assert code == 2 and f"Claude Code's record of process {me}" in err and "cannot be read" in err
    assert ade.events == []


def test_records_under_claude_config_dir_are_read_too(ruled, ade, capsys, tmp_path, monkeypatch, ancestors):
    me, _ = ancestors
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    record(tmp_path / "config" / "sessions", me, "s-vault")
    code, _, err = post(capsys, ruled)
    assert code == 2 and "this session touched a folder history.json names sensitive" in err


def test_a_record_planted_for_a_nearer_forebear_hides_none_further_up(ruled, ade, capsys, tmp_path, ancestors):
    me, parent = ancestors
    sessions = tmp_path / "home" / ".claude" / "sessions"
    record(sessions, me, "s-plain")                     # planted for the nearer one, naming a plain session
    record(sessions, parent, "s-vault")                 # the Claude Code process further up
    code, _, err = post(capsys, ruled)
    assert code == 2 and "this session touched a folder history.json names sensitive" in err and ade.events == []


def test_without_history_json_a_link_that_named_sensitive_folders_refuses_what_it_would_judge(ruled, ade, capsys,
                                                                                            tmp_path):
    os.unlink(os.path.join(ruled, "history.json"))
    code, _, err = post(capsys, ruled)
    assert code == 2 and "history.json is not there, and it named sensitive folders before" in err
    doc = tmp_path / "cp.json"
    doc.write_text(json.dumps({"doing": "the vault's plan"}))
    assert run(capsys, "--dir", ruled, "checkpoint", doc)[0] == 2 and ade.events == []


def test_a_history_json_that_named_sensitive_folders_once_read_is_one_whose_absence_refuses(ade, capsys, tmp_path):
    (tmp_path / "home" / "vault").mkdir(parents=True)
    d = a_link(ade, tmp_path / "mac")
    rules(d, {"projects": "all", "sensitive": ["~/vault"]})
    assert post(capsys, d)[0] == 0                      # read once by a post: no History run, no reading kept
    assert not os.path.exists(os.path.join(d, "history_sensitive.json"))
    os.unlink(os.path.join(d, "history.json"))
    code, _, err = post(capsys, d)
    assert code == 2 and "it named sensitive folders before" in err and len(ade.events) == 1


def test_a_link_that_never_named_sensitive_folders_posts_without_history_json(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    rules(d, {"projects": "all"})
    assert run(capsys, "--dir", d, "history")[0] == 0
    os.unlink(os.path.join(d, "history.json"))
    assert post(capsys, d)[0] == 0 and ade.events[0]["text"] == "the plan"


def test_a_session_found_touching_a_sensitive_folder_stays_so_once_the_folder_is_taken_out(ruled, ade, capsys,
                                                                                         tmp_path, monkeypatch):
    with open(os.path.join(ruled, "history_sensitive.json"), encoding="utf-8") as fh:
        assert set(json.load(fh)["touched"]) == {"s-vault", "s-fork", "s-in-vault"}
    rules(ruled, {"projects": "all", "conversations": True})                 # ~/vault no longer named
    monkeypatch.setattr(ade_link, "_posting_sessions", lambda: ({"s-vault"}, None))
    code, _, err = post(capsys, ruled)
    assert code == 2 and "this session touched a folder history.json names sensitive" in err
    assert run(capsys, "--dir", ruled, "history")[0] == 0
    assert set(r["id"] for r in ade.histories[-1]["sessions"]) == {"s-plain"}
    assert [t["session"] for t in ade.talks] == ["s-plain"]
    with open(os.path.join(ruled, "history_sensitive.json"), encoding="utf-8") as fh:
        assert set(json.load(fh)["touched"]) == {"s-vault", "s-fork", "s-in-vault"}     # kept as it was


def test_a_session_found_so_stays_so_without_its_transcript_or_any_folder_named(ruled, ade, capsys, tmp_path,
                                                                                monkeypatch):
    rules(ruled, {"projects": "all"})
    os.unlink(tmp_path / "home" / ".claude" / "projects" / "-app" / "s-vault.jsonl")
    monkeypatch.setattr(ade_link, "_posting_sessions", lambda: ({"s-vault"}, None))
    code, _, err = post(capsys, ruled)
    assert code == 2 and "this session touched a folder history.json names sensitive" in err and ade.events == []


def test_a_session_found_so_stays_so_once_the_sensitive_folder_named_is_another(ruled, ade, capsys, tmp_path):
    (tmp_path / "home" / "other").mkdir()
    rules(ruled, {"projects": "all", "sensitive": ["~/other"]})
    assert run(capsys, "--dir", ruled, "history")[0] == 0
    assert set(r["id"] for r in ade.histories[-1]["sessions"]) == {"s-plain"}


def test_a_sensitive_session_s_turn_is_held_once_the_folder_is_taken_out(ruled, ade, capsys, tmp_path, monkeypatch):
    rules(ruled, {"projects": "all"})
    path = tmp_path / "home" / ".claude" / "projects" / "-app" / "s-vault.jsonl"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(path), "session_id": "s-vault"})))
    assert run(capsys, "--dir", ruled, "mirror", "reply") == (0, "", "")
    assert ade.events == []


def mirror_stop(monkeypatch, capsys, d, path, session):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(path), "session_id": session})))
    return run(capsys, "--dir", d, "mirror", "reply")


def test_a_reading_kept_before_the_touched_list_was_still_holds_a_session_whose_entries_it_holds(ruled, ade, capsys,
                                                                                               tmp_path, monkeypatch):
    with open(os.path.join(ruled, "history_sensitive.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    del data["touched"]                                  # as the client before the touched list wrote it
    with open(os.path.join(ruled, "history_sensitive.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    path = tmp_path / "home" / ".claude" / "projects" / "-app" / "s-fork.jsonl"
    assert mirror_stop(monkeypatch, capsys, ruled, path, "s-fork") == (0, "", "")
    assert ade.events == []                              # it holds s-vault's first entry, and s-vault's reading touched


def test_a_sub_agent_that_touched_a_sensitive_folder_since_the_list_was_read_holds_its_session_s_turn(
        ruled, ade, capsys, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write(home / ".claude" / "projects" / "-app" / "s-plain" / "subagents" / "agent-1.jsonl", [
        dict(_tool("Read", {"file_path": str(home / "vault" / "notes.md")}, "2026-10-03T10:00:30Z",
                   str(home / "work" / "app")), isSidechain=True)])
    path = home / ".claude" / "projects" / "-app" / "s-plain.jsonl"
    assert mirror_stop(monkeypatch, capsys, ruled, path, "s-plain") == (0, "", "")
    assert ade.events == []


def test_a_record_of_sensitive_sessions_that_cannot_be_read_refuses_and_holds(ruled, ade, capsys, monkeypatch):
    with open(os.path.join(ruled, "history_sensitive.json"), "w", encoding="utf-8") as fh:
        fh.write("{cut")
    monkeypatch.setattr(ade_link, "_posting_sessions", lambda: ({"s-plain"}, None))
    code, _, err = post(capsys, ruled)
    assert code == 2 and "history_sensitive.json cannot be read" in err
    code, out, err = run(capsys, "--dir", ruled, "history")
    assert code != 0 and "history_sensitive.json cannot be read" in out + err


def test_a_move_of_a_sensitive_session_or_of_a_repository_holding_a_sensitive_folder_is_refused(ruled, tmp_path):
    link = ade_link.Link(ruled)
    home = tmp_path / "home"
    path = home / ".claude" / "projects" / "-app" / "s-vault.jsonl"
    with pytest.raises(ade_link.MoveFailed, match="it touched a folder history.json names sensitive"):
        ade_link._judged(link, str(path), "s-vault")
    rule, _ = ade_link._history_rule(link)
    with pytest.raises(ade_link.MoveFailed, match="is in, or holds, a folder history.json names sensitive"):
        ade_link._repository_fits(link, rule, str(home / "work" / "app"), str(home))


def test_a_move_whose_sensitive_judgement_cannot_be_told_is_refused(ruled, tmp_path):
    with open(os.path.join(ruled, "history_sensitive.json"), "w", encoding="utf-8") as fh:
        fh.write("{cut")
    path = tmp_path / "home" / ".claude" / "projects" / "-app" / "s-plain.jsonl"
    with pytest.raises(ade_link.MoveFailed, match="whether it touched a folder history.json names sensitive cannot "
                                                  "be told"):
        ade_link._judged(ade_link.Link(ruled), str(path), "s-plain")


def test_a_move_of_a_repository_a_sensitive_session_worked_in_is_refused(ruled, tmp_path):
    link = ade_link.Link(ruled)
    home = tmp_path / "home"
    top = str(home / "work")
    with open(os.path.join(ruled, "history_cache.json"), encoding="utf-8") as fh:
        cache = json.load(fh)
    (path,) = [p for p in cache["sessions"] if p.endswith("s-vault.jsonl")]
    cache["sessions"][path]["tops"] = [[os.path.realpath(top), os.path.realpath(top)]]
    with open(os.path.join(ruled, "history_cache.json"), "w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    rule, _ = ade_link._history_rule(link)
    with pytest.raises(ade_link.MoveFailed, match="a session that touched a folder history.json names sensitive "
                                                  "worked in its repository"):
        ade_link._repository_fits(link, rule, str(home / "work" / "app"), top)


def test_a_post_in_flight_when_the_stop_s_time_runs_out_keeps_the_place_it_may_have_taken(ade, capsys, tmp_path,
                                                                                         monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    real, gate, posts = ade_link.Link.request, threading.Event(), []

    def the_second_never_answers(self, method, path, body=None, *args, **kwargs):
        if path == "/events":
            posts.append(1)
            if len(posts) == 2:
                gate.wait(30)                           # in flight: it may yet be taken
                raise ade_link.Unreachable("no answer")
        return real(self, method, path, body, *args, **kwargs)
    monkeypatch.setattr(ade_link.Link, "request", the_second_never_answers)
    monkeypatch.setattr(ade_link, "MIRROR_DEADLINE", 1)
    path = tmp_path / "t.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in TURN))
    try:
        assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    finally:
        gate.set()
    with open(os.path.join(d, "mirror.json"), encoding="utf-8") as fh:
        assert json.load(fh)["s1"] == "a1"                 # kept past it: a later Stop never posts it twice
    with open(os.path.join(d, "mirror.log"), encoding="utf-8") as fh:
        assert "an answer never came" in fh.read()


# ---- what the server's check_text refuses in a question's words, and the mirror's notes ---------------------------

def test_the_marks_drawn_over_or_around_a_character_are_the_server_s_as_unicode_15_1_lists_them():
    from proofpane_open.ade import link_commands
    assert ade_link._DRAWN_OVER == link_commands.DRAWN_OVER
    assert {0x0336, 0x0338, 0x20DD, 0x20E0, 0x20E3, 0x20E4} <= ade_link._DRAWN_OVER and 0x20E1 not in ade_link._DRAWN_OVER


def test_marks_drawn_over_or_around_a_character_are_refused_in_a_question_and_escaped_where_the_client_prints(
        ade, capsys, tmp_path):
    for code in sorted(ade_link._DRAWN_OVER):
        mark = chr(code)
        assert ade_link._drawn_over("not" + mark + " now") == code and ade_link._hidden_char("not" + mark) is None
        assert ade_link._event_refusal("question", "is x =" + mark + " y?") == \
            f"The question holds U+{code:04X}, a mark drawn over or around the character before it, so it could " \
            "change how the text reads."
        assert ade_link._text_line("a" + mark + "b") == ade_link._flat("a" + mark + "b") == \
            "a" + ade_link._escape_char(mark) + "b"
        assert ade_link._clean("a" + mark + "b", 50) == "ab"
    for code in (0x0339, 0x20DC, 0x20E1, 0x20E7):      # marks beside them, which the server takes
        assert ade_link._drawn_over("a" + chr(code)) is None and not ade_link._hides(chr(code), ord("a"))
    # a question sealed to the owner is held to the rule before it is sealed: refused, nothing sent
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    code, _, err = run(capsys, "--dir", d, "post", "question", "Ship =" + chr(0x0338) + " now?", "--to", "owner")
    assert code == 2 and "U+0338" in err and ade.posted() == []


def test_a_question_that_begins_as_an_envelope_does_is_refused_before_it_is_sealed(ade, capsys, tmp_path):
    assert ade_link._event_refusal("question", "e2e1.ship it?") == \
        "The question begins e2e1., as an encrypted text does: one in the clear may not, so it is never read as one."
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    code, _, err = run(capsys, "--dir", d, "post", "question", "e2e1.ship it?", "--to", "owner")
    assert code == 2 and "begins e2e1." in err and ade.posted() == []


def test_every_mirrored_turn_names_its_session_though_the_last_list_did_not_register_it(ade, capsys, tmp_path,
                                                                                       monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    path = tmp_path / "t.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in TURN))
    assert stop(monkeypatch, capsys, d, path) == (0, "", "")
    assert [(e["text"], e.get("session")) for e in ade.events] == [("go", "s1"), ("Done.", "s1")]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(path), "session_id": "not one!"})))
    assert run(capsys, "--dir", d, "mirror", "reply") == (0, "", "")
    assert all("session" not in e for e in ade.events[2:])          # no session id this client reads: none named


def test_the_note_that_a_session_s_turns_are_not_mirrored_names_no_session(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    home = tmp_path / "home"
    work, grove = home / "work", home / "grove"
    work.mkdir(parents=True)
    grove.mkdir()
    rules(d, {"projects": "all", "except": ["~/grove"]})
    path = home / ".claude" / "projects" / "-work" / "s1.jsonl"
    _write(path, [dict(TURN[0], cwd=str(work)), _tool("Read", {"file_path": str(grove / "plan.md")},
                                                      "2026-10-03T10:00:03Z", str(work))])
    assert run(capsys, "--dir", d, "history")[0] == 0                       # read whole: withheld
    assert mirror_stop(monkeypatch, capsys, d, path, "s1") == (0, "", "")
    (note,) = ade.events
    assert note["text"].startswith("Session s1, or one whose entries it holds, touched a project history.json")
    assert "session" not in note


# ---- the remote command's guard -------------------------------------------------------------------------------------

def guarded(tmp_path, command):
    """The remote command guard's denial of one shell command while command 7 is carried out, or None."""
    work, link = tmp_path / "home" / "work", tmp_path / "home" / ".proofpane-ade" / "mac"
    work.mkdir(parents=True, exist_ok=True)
    link.mkdir(parents=True, exist_ok=True)
    hook = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(work), "session_id": "s-one"}
    return ade_link._judge_call(str(link), hook, [7], None)


def test_the_remote_command_guard_holds_e2e_with_an_option_and_lets_it_read(tmp_path):
    link, client = tmp_path / "home" / ".proofpane-ade" / "mac", os.path.abspath(ade_link.__file__)
    for command in (f"{client} --dir {link} e2e --reset", f"python3 {client} --dir {link} e2e --res",
                    f"{client} e2e --r --dir {link}", f"bash -c '{client} --dir {link} e2e --reset'",
                    f"{client} --dir {link} e2e $FLAG", f"{client} --dir {link} e2e -h"):
        reason = guarded(tmp_path, command)
        assert reason is not None and "the owner's 'e2e' with an option" in reason, command
    for command in (f"{client} --dir {link} e2e", f"{client} e2e --dir {link}", f"{client} e2e --dir={link}"):
        assert guarded(tmp_path, command) is None, command
    assert "e2e --reset" in ade_link.TAKE_GUARD


# ---- a lock that cannot be taken ------------------------------------------------------------------------------------

def unlockable(monkeypatch):
    """e2e.lock as a file system without locks has it: never taken, waited for or not; every other lock as it is."""
    real = ade_link._try_lock
    monkeypatch.setattr(ade_link, "_try_lock",
                        lambda link, name, wait=False: None if name == "e2e.lock" else real(link, name, wait))


def pair(capsys, ade, d, *more):
    return run(capsys, "pair", "--url", ade.url, "--ticket", "PP-AAAAAAAA", "--name", "mac", "--kind", "local",
               "--dir", d, "--keys", owner_key(ade.owner.device)["fingerprint"], *more)


def test_where_the_statements_lock_cannot_be_taken_a_read_is_not_kept_and_a_sealed_path_holds(ade, capsys, tmp_path,
                                                                                              monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    unlockable(monkeypatch)
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "e2e.lock cannot be taken" in err
    assert ade.posted() == [] and not os.path.exists(os.path.join(d, "e2e.json"))


def test_an_answer_s_word_is_not_written_without_the_statements_lock(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    before = held(d)
    unlockable(monkeypatch)
    ade_link._e2e_seen(ade_link.Link(d), {"e2e": {"seq": 3, "key_id": "ab" * 16}})
    assert held(d) == before


def test_a_key_the_ade_refused_is_not_written_without_the_lock_and_the_text_holds(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    unlockable(monkeypatch)
    ade.require = True
    ade.retired.add(key_id(raw_public(key)))            # the ADE refuses a text sealed to the key the link holds
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "e2e.lock cannot be taken" in err
    assert held(d).get("refused") is None and ade.events == []


def test_reset_drops_nothing_where_the_statements_lock_cannot_be_taken(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    unlockable(monkeypatch)
    code, _, err = run(capsys, "--dir", d, "e2e", "--reset")
    assert code == 1 and "nothing was dropped: e2e.lock cannot be taken" in err
    assert os.path.exists(os.path.join(d, "e2e.json")) and os.path.exists(os.path.join(d, "e2e_anchor.json"))


def test_a_pair_command_s_floor_is_not_written_without_the_statements_lock(ade, capsys, tmp_path, monkeypatch):
    unlockable(monkeypatch)
    code, out, err = pair(capsys, ade, tmp_path / "mac", "--e2e", "1:" + "0f" * 16)
    assert code == 0 and out.startswith("ADE: paired as mac") and "e2e.lock cannot be taken" in err
    assert not os.path.exists(tmp_path / "mac" / "e2e_anchor.json")


def test_pairing_and_e2e_say_the_link_holds_where_what_they_read_cannot_be_kept(ade, capsys, tmp_path, monkeypatch):
    ade.owner.state()
    unlockable(monkeypatch)
    code, out, err = pair(capsys, ade, tmp_path / "mac")
    assert code == 0 and "no key to seal to: e2e.lock cannot be taken" in err and "goes sealed" not in out
    code, out, _ = run(capsys, "--dir", tmp_path / "mac", "e2e")
    assert "this link has no key to seal to, so what goes sealed stays on this machine: e2e.lock cannot be taken" \
        in out


def test_pair_s_keys_are_checked_before_any_request(ade, capsys, tmp_path):
    with pytest.raises(SystemExit) as e:
        run(capsys, "pair", "--url", ade.url, "--ticket", "PP-AAAAAAAA", "--name", "mac", "--kind", "local",
            "--dir", tmp_path / "mac", "--keys", "not-a-fingerprint")
    assert e.value.code == 2 and "is not a passkey's fingerprint" in capsys.readouterr().err
    assert ade.requests == [] and not os.path.exists(tmp_path / "mac")
