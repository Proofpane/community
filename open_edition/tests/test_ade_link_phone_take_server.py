"""PHONE-TAKE (queue 4.326), the server's part: a linked session's heartbeat may carry, beside what its client read of
its own session, the owner keys it pinned (pins, as its History list reports them, and noted in the same place) and
what its owner allowed it to take on his Approve from the page (phone_take: the kinds of step, the owner keys whose
Approve counts, by their fingerprints, and when he allowed them, never the session they are for). The owner's view of
the links serves both, so that the page says, as the owner signs, whether the session pinned the passkey he signs
with and whether an Approve may let it run the step it asks about. A heartbeat refused in any part changes nothing; a
keeper's stays {}; a store made before the column gets it."""

import sqlite3
import time

import pytest

from proofpane_open import store
from proofpane_open.ade import e2e_keys
from proofpane_open.ade import link as server
from test_ade_e2e import device, keys  # noqa: F401  (fixtures)
from test_ade_link import paired, row
from test_ade_link import owner  # noqa: F401  the fixture

# the kinds of step, in the order the session's pin keeps them (ade_link.py pin --phone-take) and the server serves
# them: a forced push and a recursive or forced delete stay at the machine, never taken from the page
KINDS = ("deploy", "push", "send", "write")
WORDS = "A heartbeat is {} or any of {reported, pins, phone_take}."        # the task's own words for the refusal
KEEPER_WORDS = "A keeper's heartbeat is {}."
# the server's words for a phone_take out of shape, each naming what is wrong
TAKE_SHAPE = "A heartbeat's phone_take is null or {kinds, keys, at}."
TAKE_KINDS = "A heartbeat's phone_take's kinds are one or more of deploy, push, send and write, each once."
TAKE_KEYS = ("A heartbeat's phone_take's keys are the fingerprints of the owner keys whose Approve counts: one to 20, "
             "each 32 lowercase hex characters, none twice.")
TAKE_AT = ("A heartbeat's phone_take's at is when it was allowed: a whole number of unix seconds above 0, at most 300 "
           "ahead of the server's clock.")
PIN_A, PIN_B = "a" * 32, "0123456789abcdef" * 2          # two fingerprints, as check_pins takes them; B sorts first
TWENTY = [f"{i:032x}" for i in range(20)]               # the most an allowance names: an account's passkeys'
USAGE = {"context_used": 1000, "context_window": 200000}
TASKS = [{"text": "the README", "state": "completed"}]
NOT_SERVED = "not served"


def allowed(kinds, at, keys=(PIN_A,)):
    """A heartbeat's phone_take: the kinds the owner allowed, the keys whose Approve counts, and when."""
    return {"kinds": list(kinds), "keys": list(keys), "at": at}


def beat(s, body, **kw):
    return s.call("POST", "/heartbeat", body, **kw)


def again(s):
    """The link's last heartbeat moved two minutes back, so that its next one is taken (one a minute)."""
    with store.connect() as db:
        db.execute("UPDATE ade_links SET last_heartbeat_at = last_heartbeat_at - 120 WHERE id = ?", (s.link_id,))


def card(client, owner, s):
    """The link as the owner's view gives it (GET /api/ade/link/sessions)."""
    r = client.get("/api/ade/link/sessions", headers=owner)
    assert r.status_code == 200, r.text
    (found,) = [x for x in r.json()["links"] if x["id"] == s.link_id]
    return found


def served(client, owner, s):
    """The link's pins and phone_take as the owner's view serves them; NOT_SERVED for a key it does not serve."""
    found = card(client, owner, s)
    return found.get("pins", NOT_SERVED), found.get("phone_take", NOT_SERVED)


def e2e_pins(client, owner, s):
    """The link's pins as the account's encryption view gives them (GET /api/ade/e2e), which reads the place PUT
    /history notes them in."""
    r = client.get("/api/ade/e2e", headers=owner)
    assert r.status_code == 200, r.text
    (found,) = [x for x in r.json()["links"] if x["link_id"] == s.link_id]
    return found["pins"]


def kept(client, owner, s):
    """All a heartbeat may change: its presence (either kind), its report and when, the pins and the phone_take."""
    r = row(s.link_id)
    return (r["last_heartbeat_at"], r["keeper_heard_at"], r["reported"], r["reported_at"]) + served(client, owner, s)


# ---- pins -----------------------------------------------------------------------------------------------------------

def test_a_heartbeat_s_pins_are_noted_where_the_history_list_s_are_and_served_with_the_link(client, owner):
    mac = paired(client, owner)
    r = beat(mac, {"pins": [PIN_A, PIN_B]})
    assert r.status_code == 200, r.text
    assert r.json() == {"interval_min": 15, "kind": "local"}                  # its answer, as any heartbeat's
    assert served(client, owner, mac)[0] == [PIN_B, PIN_A]                     # as check_pins keeps them: sorted
    assert e2e_pins(client, owner, mac) == [PIN_B, PIN_A]                      # the one place, which the e2e view reads
    # the History list's pins, sent after, are what /sessions serves: both routes write that one place
    assert mac.call("PUT", "/history", {"sessions": [], "pins": [PIN_A]}).status_code == 200
    assert served(client, owner, mac)[0] == [PIN_A] == e2e_pins(client, owner, mac)
    again(mac)
    assert beat(mac, {"pins": [PIN_B, PIN_B]}).status_code == 200              # each once, as check_pins keeps them
    assert served(client, owner, mac)[0] == [PIN_B] == e2e_pins(client, owner, mac)


def test_pins_reported_empty_are_noted_as_none_pinned_not_as_unknown(client, owner):
    """[] says the session pinned none of the owner's passkeys, so it refuses every signed command; null says nothing
    is known. A link's first report of [] is served as [], and a later [] replaces what it pinned before."""
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    assert beat(cloud, {"pins": []}).status_code == 200
    assert served(client, owner, cloud)[0] == [] == e2e_pins(client, owner, cloud)
    assert beat(mac, {"pins": [PIN_A]}).status_code == 200
    again(mac)
    assert beat(mac, {"pins": []}).status_code == 200
    assert served(client, owner, mac)[0] == [] == e2e_pins(client, owner, mac)


def test_pins_null_or_left_out_report_nothing_and_leave_what_was_reported(client, owner):
    """A link that never reported its pins is served null, and so is one whose heartbeats carry null (no report, as
    check_pins reads it) or no pins; once reported, either leaves them as they were, as a History list without pins
    does."""
    mac = paired(client, owner)
    assert served(client, owner, mac)[0] is None
    assert beat(mac, {"pins": None}).status_code == 200
    assert served(client, owner, mac)[0] is None and e2e_pins(client, owner, mac) is None
    again(mac)
    assert beat(mac, {"pins": [PIN_A]}).status_code == 200
    for body in ({"pins": None}, {}, {"phone_take": None}, {"reported": {"usage": USAGE}}):
        again(mac)
        assert beat(mac, body).status_code == 200, body
        assert served(client, owner, mac)[0] == [PIN_A], body


# ---- phone_take -----------------------------------------------------------------------------------------------------

def test_phone_take_is_kept_served_replaced_and_cleared_by_null(client, owner):
    mac = paired(client, owner)
    at = int(time.time()) - 600
    assert served(client, owner, mac)[1] is None                                # never reported
    take = allowed(["push", "send"], at)
    assert beat(mac, {"phone_take": take}).status_code == 200
    assert served(client, owner, mac)[1] == take
    for body in ({}, {"reported": {"usage": USAGE}}, {"pins": [PIN_A]}):       # left out: as it was
        again(mac)
        assert beat(mac, body).status_code == 200, body
        assert served(client, owner, mac)[1] == take, body
    again(mac)
    later = allowed(KINDS, at + 60, [PIN_B, PIN_A])
    assert beat(mac, {"phone_take": later}).status_code == 200                 # another replaces it, keys and all
    assert served(client, owner, mac)[1] == later
    again(mac)
    assert beat(mac, {"phone_take": None}).status_code == 200                   # null: none, which clears it
    assert served(client, owner, mac)[1] is None
    assert row(mac.link_id)["phone_take"] is None


def test_phone_take_s_kinds_are_served_in_the_table_s_order_and_its_keys_as_given(client, owner):
    """Its kinds in the order the session's pin keeps them; its keys as the session named them, neither sorted (PIN_B
    sorts first) nor made fewer."""
    mac = paired(client, owner)
    at = int(time.time()) - 600
    assert beat(mac, {"phone_take": allowed(["write", "send", "deploy"], at, [PIN_A, PIN_B])}).status_code == 200
    assert served(client, owner, mac)[1] == {"kinds": ["deploy", "send", "write"], "keys": [PIN_A, PIN_B], "at": at}


def test_phone_take_names_one_key_or_as_many_as_an_account_s_passkeys(client, owner):
    mac = paired(client, owner)
    at = int(time.time()) - 600
    for keys in ([PIN_B], TWENTY, list(reversed(TWENTY))):
        assert beat(mac, {"phone_take": allowed(["deploy"], at, keys)}).status_code == 200, keys
        assert served(client, owner, mac)[1] == allowed(["deploy"], at, keys), keys
        again(mac)


def test_phone_take_s_at_may_be_ahead_of_the_server_s_clock_by_300_seconds_and_no_more(client, owner, monkeypatch):
    """`at` is when the owner allowed it, by the session's machine's clock, which may be ahead of the server's within
    the signatures' skew; further ahead it is refused."""
    mac = paired(client, owner)
    t0 = int(time.time())
    monkeypatch.setattr(server.time, "time", lambda: t0)
    r = beat(mac, {"phone_take": allowed(["push"], t0 + 301)}, ts=t0)
    assert (r.status_code, r.json().get("detail")) == (422, TAKE_AT), r.text
    assert row(mac.link_id)["last_heartbeat_at"] is None
    r = beat(mac, {"phone_take": allowed(["push"], t0 + 300)}, ts=t0)
    assert r.status_code == 200, r.text
    assert served(client, owner, mac)[1] == allowed(["push"], t0 + 300)


# ---- refusals -------------------------------------------------------------------------------------------------------

def taking(**over):
    """A phone_take of its shape but for what `over` puts in its place (its kinds, keys or at), made from the server's
    clock."""
    return lambda now: dict(allowed(["push"], now - 600), **over)


# what is refused: (the key, its value made from the server's clock, the refusal's words; None: check_pins' own)
REFUSED = {
    "an unknown key": ("notes", lambda now: "x", WORDS),
    "the session id, beside": ("session", lambda now: "6f1c2a90-0d3e-4b7a-9c1f-2e5d8a7b6c40", WORDS),
    "a misspelt key": ("phonetake", lambda now: None, WORDS),
    "pins not a list": ("pins", lambda now: PIN_A, None),
    "pins in capitals": ("pins", lambda now: [PIN_A.upper()], None),
    "pins too short": ("pins", lambda now: ["a" * 31], None),
    "pins not text": ("pins", lambda now: [1], None),
    "pins past the passkeys' most": ("pins", lambda now: [f"{i:032x}" for i in range(21)], None),
    "pins an object": ("pins", lambda now: {"a": PIN_A}, None),
    "an unknown kind": ("phone_take", taking(kinds=["reboot"]), TAKE_KINDS),
    "a forced push, which stays at the machine": ("phone_take", taking(kinds=["force-push"]), TAKE_KINDS),
    "a delete, which stays at the machine": ("phone_take", taking(kinds=["delete"]), TAKE_KINDS),
    "a forced push beside a push": ("phone_take", taking(kinds=["push", "force-push"]), TAKE_KINDS),
    "a kind in capitals": ("phone_take", taking(kinds=["Push"]), TAKE_KINDS),
    "a kind's phrase": ("phone_take", taking(kinds=["a deployment"]), TAKE_KINDS),
    "a kind twice": ("phone_take", taking(kinds=["push", "push"]), TAKE_KINDS),
    "no kind": ("phone_take", taking(kinds=[]), TAKE_KINDS),
    "kinds not a list": ("phone_take", taking(kinds="push"), TAKE_KINDS),
    "a kind null": ("phone_take", taking(kinds=["push", None]), TAKE_KINDS),
    "a kind a list": ("phone_take", taking(kinds=[["push"]]), TAKE_KINDS),
    "no key": ("phone_take", taking(keys=[]), TAKE_KEYS),
    "past the passkeys' most": ("phone_take", taking(keys=[f"{i:032x}" for i in range(21)]), TAKE_KEYS),
    "a key twice": ("phone_take", taking(keys=[PIN_A, PIN_B, PIN_A]), TAKE_KEYS),
    "a key in capitals": ("phone_take", taking(keys=[PIN_A.upper()]), TAKE_KEYS),
    "a key one short": ("phone_take", taking(keys=["a" * 31]), TAKE_KEYS),
    "a key one long": ("phone_take", taking(keys=["a" * 33]), TAKE_KEYS),
    "a key not hex": ("phone_take", taking(keys=["g" + "a" * 31]), TAKE_KEYS),
    "a key with a line break": ("phone_take", taking(keys=["a" * 32 + "\n"]), TAKE_KEYS),
    "a key not text": ("phone_take", taking(keys=[1]), TAKE_KEYS),
    "a key null": ("phone_take", taking(keys=[PIN_A, None]), TAKE_KEYS),
    "a key a list": ("phone_take", taking(keys=[[PIN_A]]), TAKE_KEYS),
    "keys a fingerprint": ("phone_take", taking(keys=PIN_A), TAKE_KEYS),
    "keys an object": ("phone_take", taking(keys={PIN_A: True}), TAKE_KEYS),
    "keys null": ("phone_take", taking(keys=None), TAKE_KEYS),
    "at past 300 s ahead": ("phone_take", lambda now: allowed(["push"], now + 400), TAKE_AT),
    "at as text": ("phone_take", lambda now: allowed(["push"], str(now - 600)), TAKE_AT),
    "at with a fraction": ("phone_take", lambda now: allowed(["push"], now - 599.5), TAKE_AT),
    "at true": ("phone_take", lambda now: allowed(["push"], True), TAKE_AT),
    "at null": ("phone_take", lambda now: allowed(["push"], None), TAKE_AT),
    "at zero": ("phone_take", lambda now: allowed(["push"], 0), TAKE_AT),
    "at below zero": ("phone_take", lambda now: allowed(["push"], 600 - now), TAKE_AT),
    "an extra key: the session": ("phone_take", lambda now: dict(allowed(["push"], now - 600), session="s1"),
                                  TAKE_SHAPE),
    "no keys: the older shape": ("phone_take", lambda now: {"kinds": ["push"], "at": now - 600}, TAKE_SHAPE),
    "kinds and keys alone": ("phone_take", lambda now: {"kinds": ["push"], "keys": [PIN_A]}, TAKE_SHAPE),
    "keys and at alone": ("phone_take", lambda now: {"keys": [PIN_A], "at": now - 600}, TAKE_SHAPE),
    "kinds alone": ("phone_take", lambda now: {"kinds": ["push"]}, TAKE_SHAPE),
    "at alone": ("phone_take", lambda now: {"at": now - 600}, TAKE_SHAPE),
    "an empty object": ("phone_take", lambda now: {}, TAKE_SHAPE),
    "a list": ("phone_take", lambda now: ["push"], TAKE_SHAPE),
    "a name": ("phone_take", lambda now: "push", TAKE_SHAPE),
    "true": ("phone_take", lambda now: True, TAKE_SHAPE),
}


@pytest.mark.parametrize("key,value,words", list(REFUSED.values()), ids=list(REFUSED))
def test_a_heartbeat_refused_in_any_part_changes_nothing(client, owner, key, value, words):
    """Refused whole, as a refused report is: neither its presence nor anything it carries is kept, its good parts
    beside the refused one among them, on a link that holds nothing yet and on one that holds all of them."""
    mac = paired(client, owner)
    now = int(time.time())
    if words is None:                                          # pins out of shape: refused in check_pins' own words
        with pytest.raises(server.LinkError) as refused:
            e2e_keys.check_pins(value(now))
        words = str(refused.value)
    # its good parts, each other than what the link comes to hold, so that keeping any of them would show
    good = {"reported": {"usage": dict(USAGE, context_used=1)}, "pins": [PIN_B],
            "phone_take": allowed(["deploy"], now - 60, [PIN_B])}
    r = beat(mac, dict(good, **{key: value(now)}))
    assert (r.status_code, r.json().get("detail")) == (422, words), r.text
    assert kept(client, owner, mac) == (None, None, None, None, None, None)
    held = {"reported": {"usage": USAGE}, "pins": [PIN_A], "phone_take": allowed(["push"], now - 600)}
    first = beat(mac, held)
    assert first.status_code == 200, first.text
    again(mac)
    before = kept(client, owner, mac)
    r = beat(mac, dict(good, **{key: value(now)}))
    assert (r.status_code, r.json().get("detail")) == (422, words), r.text
    assert kept(client, owner, mac) == before


def test_a_heartbeat_refused_for_encryption_notes_neither_and_both_go_in_the_clear(client, owner, keys):
    """A report's lists in the clear while encryption is on refuse the heartbeat (409), its pins and phone_take
    with it; neither is words, so both are taken in the clear."""
    mac = paired(client, owner)
    keys.turn_on()
    now = int(time.time())
    take = allowed(["push"], now - 600)
    r = beat(mac, {"reported": {"tasks": TASKS}, "pins": [PIN_A], "phone_take": take})
    assert r.status_code == 409 and r.json().get("e2e") == "required", r.text
    assert kept(client, owner, mac) == (None, None, None, None, None, None)
    r = beat(mac, {"reported": {"usage": USAGE}, "pins": [PIN_A], "phone_take": take})
    assert r.status_code == 200, r.text
    assert served(client, owner, mac) == ([PIN_A], take)


def test_a_keeper_s_beat_stays_empty_and_leaves_pins_and_phone_take(client, owner, monkeypatch):
    """A keeper's beat (via=keeper) says only that the session's process runs: one carrying pins or phone_take, null
    or not, is refused and moves nothing, and the one it may send leaves what the link's own beats reported."""
    t0 = int(time.time())
    monkeypatch.setattr(server.time, "time", lambda: t0)
    cloud = paired(client, owner, "cloud", "cloud")
    held = {"pins": [PIN_A], "phone_take": allowed(["push"], t0 - 600)}
    r = beat(cloud, held, ts=t0)
    assert r.status_code == 200, r.text
    for body in ({"pins": []}, {"pins": None}, {"phone_take": None}, {"phone_take": allowed(["send"], t0, [PIN_B])},
                 held):
        r = beat(cloud, body, query={"via": "keeper"}, ts=t0)
        assert (r.status_code, r.json().get("detail")) == (422, KEEPER_WORDS), body
        assert row(cloud.link_id)["keeper_heard_at"] is None, body
        assert served(client, owner, cloud) == ([PIN_A], held["phone_take"]), body
    taken = beat(cloud, {}, query={"via": "keeper"}, ts=t0)
    assert taken.status_code == 200 and taken.json() == {"interval_min": 30, "kind": "cloud"}
    assert row(cloud.link_id)["keeper_heard_at"] == t0
    assert served(client, owner, cloud) == ([PIN_A], held["phone_take"])


def test_the_old_beats_are_taken_as_before_and_leave_pins_and_phone_take(client, owner):
    """{} and {reported} are heartbeats as they were: presence, and the report kept with when it came, the link's
    pins and phone_take left as its last beat to carry them reported them."""
    mac = paired(client, owner)
    take = allowed(["push", "write"], int(time.time()) - 600, [PIN_A, PIN_B])
    assert beat(mac, {"pins": [PIN_A], "phone_take": take}).status_code == 200
    again(mac)
    r = beat(mac, {})
    assert r.status_code == 200 and r.json() == {"interval_min": 15, "kind": "local"}, r.text
    assert served(client, owner, mac) == ([PIN_A], take)
    again(mac)
    r = beat(mac, {"reported": {"usage": USAGE}})
    assert r.status_code == 200 and r.json() == {"interval_min": 15, "kind": "local"}, r.text
    got = card(client, owner, mac)
    assert (got["reported"], got["reported_at"]) == ({"usage": USAGE}, row(mac.link_id)["last_heartbeat_at"])
    assert (got["pins"], got["phone_take"]) == ([PIN_A], take)


def test_at_most_one_heartbeat_a_minute_whatever_it_carries(client, owner):
    mac = paired(client, owner)
    take = allowed(["push"], int(time.time()) - 600)
    assert beat(mac, {"pins": [PIN_A], "phone_take": take}).status_code == 200
    before = kept(client, owner, mac)
    r = beat(mac, {"pins": [PIN_B], "phone_take": None})
    assert r.status_code == 429, r.text
    assert kept(client, owner, mac) == before


def test_an_older_heartbeat_that_ends_last_leaves_the_newer_one_s_pins_and_phone_take(client, owner):
    """Both are written only where the heartbeat's presence moved, as its report is: of two in flight at once, the
    older one, ending last, leaves the newer one's."""
    mac = paired(client, owner)
    held = row(mac.link_id)                     # as two of its heartbeats in flight at once both read it
    now = int(time.time())
    newer = allowed(["push"], now - 60)
    server.heartbeat(dict(held, request_ts=now), {"pins": [PIN_A], "phone_take": newer}, now)
    server.heartbeat(dict(held, request_ts=now - 30), {"pins": [PIN_B], "phone_take": None}, now)
    assert served(client, owner, mac) == ([PIN_A], newer)


# ---- the column -----------------------------------------------------------------------------------------------------

def test_a_store_made_before_phone_take_gains_its_column_and_keeps_its_links(tmp_path):
    """ade_links.phone_take is added (store.ADDED_COLUMNS), so that a store made before it gains it once and keeps its
    links as they were, none with a phone_take until its heartbeat reports one."""
    path = str(tmp_path / "before.db")
    with store.connect(path) as db:
        have = [r[1] for r in db.execute("PRAGMA table_info(ade_links)")]
        db.execute("INSERT INTO ade_links (id, user_id, name, kind, public_key, key_id, interval_min, created_at, "
                   "reported) VALUES ('L1', 'u1', 'mac', 'local', 'pk', 'k1', 15, 5, '{\"usage\":{}}')")
    assert have.count("phone_take") == 1, have
    old = sqlite3.connect(path)
    old.execute("ALTER TABLE ade_links DROP COLUMN phone_take")
    old.commit()
    assert "phone_take" not in [r[1] for r in old.execute("PRAGMA table_info(ade_links)")]
    old.close()
    with store.connect(path) as db:
        have = [r[1] for r in db.execute("PRAGMA table_info(ade_links)")]
        link_row = dict(db.execute("SELECT * FROM ade_links WHERE id = 'L1'").fetchone())
    assert have.count("phone_take") == 1, have
    assert (link_row["name"], link_row["reported"], link_row["phone_take"]) == ("mac", '{"usage":{}}', None)
