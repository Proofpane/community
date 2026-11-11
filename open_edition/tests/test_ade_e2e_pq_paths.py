"""What links send a post-quantum account, as the server takes it:
events and History rows in form 2 under a post-quantum key, never in a form their key does not take; a
conversation sealed whole under a key of its own (form 3 beside a form-2 key), kept and served with that key; what a
link says it can seal; sealing in place to a post-quantum key; and the texts still sealed to a classical key, counted.
Envelopes are opened by the page's side written for the tests (e2e_pq_page_side.py)."""

import json
import os
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import e2e_pq_page_side as page
from proofpane_open import store
from proofpane_open.ade import e2e, e2e_keys, holds
from proofpane_core.canonical import canonicalize
from test_ade_e2e import enrol, history, passkey_of, rows, sealed_row, send_sealed, talk_messages, view
from test_ade_e2e import device, keys, notices, owner, second  # noqa: F401  (fixtures)
from test_ade_e2e_envelope import b64u, raw_public, unb64u
from test_ade_e2e_pq_server import Upgrade, pq_on, pq_revocation
from test_ade_e2e_pq_server import pq  # noqa: F401  (fixture)
from test_ade_link import paired, post


def relabelled(envelope, key_id):
    """`envelope` with the key_id its header names replaced by `key_id`: what a link that seals in the wrong form for a
    key would send (the server reads the form alone)."""
    raw = bytearray(unb64u(envelope[len(e2e.PREFIX):]))
    raw[1:17] = bytes.fromhex(key_id)
    return e2e.PREFIX + b64u(bytes(raw))


def event2(pq, s, kind, words, seed=None, client_id=None, **kw):
    client_id = client_id or uuid.uuid4().hex
    seed = seed or pq.key()
    slot = e2e.event_slot(pq.account, s.link_id, kind, kw.get("mirror"), kw.get("ref"), client_id)
    text = e2e.seal(page.public_of(seed), slot, {"text": words, "session": kw.get("session"),
                                                "said_at": kw.get("said_at")})
    return post(s, kind, text, client_id=client_id, **kw), client_id, text


def talk_of(pq, s, sid, said, earlier=0, seed=None):
    """A conversation as a link seals one for a post-quantum key: (the body it sends, its key's 32 bytes)."""
    now = int(time.time())
    messages = [{"who": who, "text": text, "at": now - 100 + i, "ms": (now - 100 + i) * 1000 - 5}
                for i, (who, text) in enumerate(said)]
    key, key_id, sealed = e2e.seal_talk(page.public_of(seed or pq.key()), pq.account, s.link_id, sid, earlier,
                                        messages)
    tk = page.open_talk_key(seed or pq.key(), key, e2e.talk_key_slot(pq.account, s.link_id, sid, len(said), earlier))
    return {"session": sid, "messages": sealed, "earlier": earlier, "key": key, "key_id": key_id}, tk


def listed_sealed(pq, s, sid="s1"):
    """The link lists one session, its row sealed in form 2, conversations going."""
    slot = e2e.history_slot(pq.account, s.link_id, sid, False)
    row = {"id": sid, "withheld": False, "sealed": e2e.seal(page.public_of(pq.key()), slot, {"title": "t"})}
    r = history(s, [row], conversations=True)
    assert r.status_code == 200 and r.json()["listed"] == 1, r.text


# ---- events and History rows ----------------------------------------------------------------------------------------

def test_a_post_quantum_account_takes_events_and_rows_in_form_2(client, owner, pq):
    mac = paired(client, owner)
    pq_on(pq)
    r, client_id, text = event2(pq, mac, "step", "sealed words")
    assert r.status_code == 200, r.text
    (event,) = rows("ade_link_events", client_id=client_id)
    slot = e2e.event_slot(pq.account, mac.link_id, "step", None, None, client_id)
    assert page.open_envelope2(pq.key(), event["text"], slot) == {"text": "sealed words", "session": None,
                                                                   "said_at": None}
    listed_sealed(pq, mac)
    (row,) = rows("ade_link_history", link_id=mac.link_id)
    assert page.open_envelope2(pq.key(), row["sealed"], e2e.history_slot(pq.account, mac.link_id, "s1", False)) == \
        {"title": "t"}
    # a conversation's own form goes nowhere else
    tk = os.urandom(32)
    form3 = e2e.seal_talk_message(tk, e2e.event_slot(pq.account, mac.link_id, "step", None, None, "a" * 32),
                                  {"text": "x"})
    r = post(mac, "step", form3, client_id="a" * 32)
    assert r.status_code == 422 and "form 1 or 2" in r.json()["detail"], r.text


def test_an_envelope_in_a_form_its_key_does_not_take_is_refused(client, owner, pq, keys):
    """Form 1 is sealed to a classical key and form 2 to a post-quantum one: an event naming a key in the other form is
    refused, and such a History row left out; never read the other way."""
    mac = paired(client, owner)
    pq_on(pq)
    hybrid_id = e2e.key_id(page.public_of(pq.key()))
    client_id = uuid.uuid4().hex
    form1 = e2e.seal(raw_public(X25519PrivateKey.generate()),
                     e2e.event_slot(pq.account, mac.link_id, "step", None, None, client_id), {"text": "x"})
    r = post(mac, "step", relabelled(form1, hybrid_id), client_id=client_id)
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.OTHER_FORM, r.text
    slot = e2e.history_slot(pq.account, mac.link_id, "s1", False)
    row = {"id": "s1", "withheld": False, "sealed": relabelled(e2e.seal(raw_public(X25519PrivateKey.generate()), slot,
                                                                        {"title": "t"}), hybrid_id)}
    r = history(mac, [row])
    assert r.status_code == 200 and (r.json()["listed"], r.json()["left_out"]) == (0, 1), r.text


def test_a_form_2_envelope_under_a_classical_key_is_refused(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    classical_id = e2e.key_id(raw_public(keys.key()))
    client_id = uuid.uuid4().hex
    form2 = e2e.seal(page.public_of(os.urandom(32)),
                     e2e.event_slot(keys.account, mac.link_id, "step", None, None, client_id), {"text": "x"})
    r = post(mac, "step", relabelled(form2, classical_id), client_id=client_id)
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.OTHER_FORM, r.text
    assert send_sealed(keys, mac, "step")[0].status_code == 200          # its own form goes


# ---- conversations under a key of their own ----------------------------------------------------------------------------

def test_a_conversation_sealed_whole_is_kept_with_its_key_and_served_with_it(client, owner, pq):
    mac = paired(client, owner)
    pq_on(pq)
    listed_sealed(pq, mac)
    said = [("owner", "what is the plan?"), ("session", "the plan is " + chr(0x1F600))]
    body, tk = talk_of(pq, mac, "s1", said, earlier=3)
    r = mac.call("PUT", "/history/talk", body)
    assert r.status_code == 200 and r.json()["messages"] == 2, r.text
    got = client.get(f"/api/ade/link/history/{mac.link_id}/s1", headers=owner).json()
    assert (got["key"], got["key_id"], got["earlier"]) == (body["key"], body["key_id"], 3)
    assert page.talk_key_id(tk) == got["key_id"]
    for i, m in enumerate(got["messages"]):
        slot = e2e.talk_slot(pq.account, mac.link_id, "s1", i, 2, 3, m["who"], m["at"], m.get("ms"))
        assert page.open_talk_message(tk, m["text"], slot) == {"text": said[i][1]}
    # sent again under a fresh key of its own: that key and its id are served, not the first's
    said.append(("owner", "and then?"))
    again, tk2 = talk_of(pq, mac, "s1", said, earlier=3)
    assert mac.call("PUT", "/history/talk", again).status_code == 200
    got = client.get(f"/api/ade/link/history/{mac.link_id}/s1", headers=owner).json()
    assert (got["key"], got["key_id"]) == (again["key"], again["key_id"]) and got["key_id"] != body["key_id"]
    slot = e2e.talk_slot(pq.account, mac.link_id, "s1", 2, 3, 3, got["messages"][2]["who"], got["messages"][2]["at"],
                         got["messages"][2].get("ms"))
    assert page.open_talk_message(tk2, got["messages"][2]["text"], slot) == {"text": "and then?"}
    # sent again without a key: refused while encryption is on; in the clear once it is off, and the key goes too
    r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": [{"who": "owner", "text": "x"}]})
    assert r.status_code == 409 and r.json()["e2e"] == "required", r.text
    off = pq.statement("e2e-off", retires=None)[0]
    r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": off, "kem": "x-wing"})
    assert r.status_code == 200, r.text
    holds.settle(pq.account, now=r.json()["hold"]["takes_effect_at"])
    r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": [{"who": "owner", "text": "x"}]})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/ade/link/history/{mac.link_id}/s1", headers=owner).json()
    assert (got["key"], got["key_id"], [m["text"] for m in got["messages"]]) == (None, None, ["x"])


def test_a_conversation_beside_its_key_is_all_form_3_naming_that_key(client, owner, pq):
    mac = paired(client, owner)
    pq_on(pq)
    listed_sealed(pq, mac)
    said = [("owner", "a"), ("session", "b")]
    body, tk = talk_of(pq, mac, "s1", said)
    other, _ = talk_of(pq, mac, "s1", said)
    form1 = talk_messages(pq, mac, "s1", said, private=X25519PrivateKey.generate())
    form2 = [dict(m, text=e2e._seal_hybrid(page.public_of(pq.key()), "x", canonicalize({"text": "y"})))
             for m in body["messages"]]
    short_key = e2e._seal_hybrid(page.public_of(pq.key()), "x", os.urandom(31))
    for case, bad, words in (
            ("a message naming another conversation's key", dict(body, messages=[body["messages"][0],
                                                                                  other["messages"][1]]),
             "names that key"),
            ("a message in the clear", dict(body, messages=body["messages"] + [{"who": "owner", "text": "x"}]),
             "no message in the clear"),
            ("messages in form 1", dict(body, messages=form1), "form 3"),
            ("messages in form 2", dict(body, messages=form2), "form 3"),
            ("a key of 31 bytes", dict(body, key=short_key), "exactly 1197 bytes"),
            ("a key in form 1", dict(body, key=e2e.seal(raw_public(X25519PrivateKey.generate()), "x", {"a": 1})),
             "form 2"),
            ("a key_id not 32 hex", dict(body, key_id="X" * 32), "key_id"),
            ("a key_id and no key", {k: v for k, v in body.items() if k != "key"}, "conversation's key")):
        r = mac.call("PUT", "/history/talk", bad)
        assert r.status_code == 422 and words in r.json()["detail"], (case, r.text)
    # form-3 messages with no key beside them
    r = mac.call("PUT", "/history/talk", {k: v for k, v in body.items() if k not in ("key", "key_id")})
    assert r.status_code == 422 and "form 1" in r.json()["detail"], r.text
    # a key sealed to a key the account never stated
    relabel = relabelled(body["key"], e2e.key_id(raw_public(X25519PrivateKey.generate())))
    r = mac.call("PUT", "/history/talk", dict(body, key=relabel))
    assert r.status_code == 422 and "never stated" in r.json()["detail"], r.text
    assert mac.call("PUT", "/history/talk", body).status_code == 200


def test_a_conversation_s_key_under_a_classical_key_is_a_form_its_key_does_not_take(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    assert history(mac, [sealed_row(keys, mac, "s1", {"title": "t"})], conversations=True).status_code == 200
    now = int(time.time())
    key, key_id, sealed = e2e.seal_talk(page.public_of(os.urandom(32)), keys.account, mac.link_id, "s1", 0,
                                        [{"who": "owner", "text": "a", "at": now}])
    body = {"session": "s1", "messages": sealed, "key": relabelled(key, e2e.key_id(raw_public(keys.key()))),
            "key_id": key_id}
    r = mac.call("PUT", "/history/talk", body)
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.OTHER_FORM, r.text


def test_a_conversation_s_key_is_sealed_to_a_key_not_retired_while_encryption_is_on(client, owner, pq):
    mac = paired(client, owner)
    old = pq_on(pq)
    listed_sealed(pq, mac)
    rev, signed, seed = pq_revocation(pq)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=rev)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    holds.settle(pq.account, now=hold["takes_effect_at"])
    pq.took(signed, seed)
    pq.mk_id = rev["mk_id"]
    stale, _ = talk_of(pq, mac, "s1", [("owner", "a")], seed=old)
    with store.connect() as db:          # retired as of the hold's end, by the settle's clock: so for the server's now
        db.execute("UPDATE ade_e2e_statements SET retired_at = 1 WHERE retired_at IS NOT NULL")
    listed_sealed(pq, mac)
    r = mac.call("PUT", "/history/talk", stale)
    assert r.status_code == 409 and r.json()["e2e"] == "required", r.text
    fresh, _ = talk_of(pq, mac, "s1", [("owner", "a")])
    assert mac.call("PUT", "/history/talk", fresh).status_code == 200


def test_a_conversation_s_key_counts_toward_its_total(client, owner, pq, monkeypatch):
    from proofpane_open.ade import link
    mac = paired(client, owner)
    pq_on(pq)
    listed_sealed(pq, mac)
    body, _ = talk_of(pq, mac, "s1", [("owner", "x" * 100)])
    size = sum(len(unb64u(m["text"][5:])) for m in body["messages"]) + e2e.TALK_KEY_SEALED
    monkeypatch.setattr(link, "TALK_SEALED_TOTAL", size)
    assert mac.call("PUT", "/history/talk", body).status_code == 200
    monkeypatch.setattr(link, "TALK_SEALED_TOTAL", size - 1)
    r = mac.call("PUT", "/history/talk", body)
    assert r.status_code == 422 and "bytes" in r.json()["detail"], r.text


# ---- what a link says it can seal --------------------------------------------------------------------------------------

def test_a_link_says_what_it_can_seal_and_the_page_reads_it(client, owner, pq):
    mac = paired(client, owner)
    pq_on(pq)
    assert view(client, owner)["links"][0]["seals"] is None
    listed = [{"id": "s1", "withheld": False, "sealed": e2e.seal(page.public_of(pq.key()), e2e.history_slot(
        pq.account, mac.link_id, "s1", False), {"title": "t"})}]
    assert history(mac, listed, e2e_seal=[3, 1, 2]).status_code == 200
    assert view(client, owner)["links"][0]["seals"] == [1, 2, 3]
    assert history(mac, listed, e2e_seal=[1]).status_code == 200
    assert view(client, owner)["links"][0]["seals"] == [1]
    assert history(mac, listed).status_code == 200                       # a list that says nothing changes nothing
    assert view(client, owner)["links"][0]["seals"] == [1]
    # and no value but a list: a number or a boolean, which cannot be read item by item, and an object or a text,
    # whose keys or characters are no forms
    for bad in ([], [2, 3], [1, 1], [1, 4], ["1"], [True], "1,2,3", [1, 2, 3, 3], [1, 2], [1, 3], [[1]], [{"a": 1}],
                [1, [2]], [1.0], 1, 3, True, 1.5, {"1": 1}, {}):
        r = history(mac, listed, e2e_seal=bad)
        assert r.status_code == 422 and "e2e_seal" in r.json()["detail"], (bad, r.text)


def test_a_link_says_what_it_can_seal_as_it_reads_the_statements(client, owner, pq):
    """Every link reads the statements (GET /e2e), one that sends no History list, or holds it, too: the forms its
    first page's request says it can seal are kept as its list's are; a read that says none changes nothing."""
    mac = paired(client, owner)
    pq_on(pq)
    assert view(client, owner)["links"][0]["seals"] is None

    def read(**query):
        return mac.call("GET", "/e2e", query=dict({"after": 0, "limit": 10}, **query))
    r = read(e2e_seal="1,2,3")
    assert r.status_code == 200 and len(r.json()["statements"]) == 1, r.text
    assert view(client, owner)["links"][0]["seals"] == [1, 2, 3]
    assert read(e2e_seal="1").status_code == 200 and view(client, owner)["links"][0]["seals"] == [1]
    assert read().status_code == 200 and view(client, owner)["links"][0]["seals"] == [1]
    assert read(e2e_seal="3,1,2").status_code == 200 and view(client, owner)["links"][0]["seals"] == [1, 2, 3]
    for bad in ("", "1,2", "2,3", "1,3", "1,1", "1,2,3,3", "1,2,3,4", "4", "01,2,3", "1,2,3,", ",1", "1, 2,3", "1;2;3",
                "[1,2,3]", "a", "1,2," + "3" * 3):
        r = read(e2e_seal=bad)
        assert r.status_code == 422 and r.json()["detail"] == e2e_keys.SEALS_FORM, (bad, r.text)
        assert view(client, owner)["links"][0]["seals"] == [1, 2, 3], bad


# ---- sealing in place to a post-quantum key --------------------------------------------------------------------------

def test_a_post_quantum_turn_on_seals_in_place_in_forms_2_and_3(client, owner, pq):
    mac = paired(client, owner)
    now = int(time.time())
    assert history(mac, [{"id": "s1", "title": "SEALME title", "project": "~/SEALME"},
                         {"id": "s2", "title": "SEALME two"}], conversations=True).status_code == 200
    said = [{"who": "owner", "text": "SEALME asked", "at": now - 5}, {"who": "session", "text": "SEALME said",
                                                                       "at": now}]
    assert mac.call("PUT", "/history/talk", {"session": "s1", "messages": said, "earlier": 2}).status_code == 200
    client_id = uuid.uuid4().hex
    assert post(mac, "step", "SEALME step", client_id=client_id).status_code == 200
    # a conversation holding a message sealed already (to a key of before) and one in the clear: never a mix
    mixed = [{"who": "owner", "text": e2e.seal(raw_public(X25519PrivateKey.generate()), "x", {"text": "a"}),
              "at": now}, {"who": "session", "text": "SEALME mixed", "at": now}]
    with store.connect() as db:
        db.execute("INSERT INTO ade_link_history_talks (user_id, link_id, session_id, messages, earlier, updated_at) "
                   "VALUES (?,?,?,?,?,?)", (pq.account, mac.link_id, "s2", json.dumps(mixed), 0, now))
    got, seed = pq.turn_on()
    assert got["sealed"] == {"events": 1, "left": 0, "rows": 2, "talks": 1, "dropped": 1}
    (event,) = rows("ade_link_events", client_id=client_id)
    assert page.open_envelope2(seed, event["text"], e2e.event_slot(pq.account, mac.link_id, "step", None, None,
                                                                    client_id))["text"] == "SEALME step"
    listed = {r["session_id"]: r for r in rows("ade_link_history", link_id=mac.link_id)}
    assert page.open_envelope2(seed, listed["s1"]["sealed"], e2e.history_slot(pq.account, mac.link_id, "s1", False))[
        "title"] == "SEALME title"
    (talk,) = rows("ade_link_history_talks", link_id=mac.link_id)
    assert talk["session_id"] == "s1"
    tk = page.open_talk_key(seed, talk["key"], e2e.talk_key_slot(pq.account, mac.link_id, "s1", 2, 2))
    assert page.talk_key_id(tk) == talk["key_id"]
    for i, m in enumerate(json.loads(talk["messages"])):
        slot = e2e.talk_slot(pq.account, mac.link_id, "s1", i, 2, 2, m["who"], m["at"], m.get("ms"))
        assert page.open_talk_message(tk, m["text"], slot) == {"text": said[i]["text"]}


def test_a_turn_on_that_drops_a_conversation_alone_is_due_for_compaction(client, owner, pq, monkeypatch):
    """A conversation a post-quantum turn-on deletes (one holding a message sealed already and one in the clear, which
    it cannot seal whole) had its clear words written over as much as one it seals: the database is due for compaction
    though nothing else was sealed."""
    begun = []
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: begun.append(1))      # left due, to be seen
    mac = paired(client, owner)
    now = int(time.time())
    mixed = [{"who": "owner", "text": e2e.seal(raw_public(X25519PrivateKey.generate()), "x", {"text": "a"}),
              "at": now}, {"who": "session", "text": "SEALME mixed", "at": now}]
    with store.connect() as db:
        db.execute("INSERT INTO ade_link_history_talks (user_id, link_id, session_id, messages, earlier, updated_at) "
                   "VALUES (?,?,?,?,?,?)", (pq.account, mac.link_id, "s2", json.dumps(mixed), 0, now))
    got, _ = pq.turn_on()
    assert got["sealed"] == {"events": 0, "left": 0, "rows": 0, "talks": 0, "dropped": 1}
    assert rows("ade_link_history_talks", link_id=mac.link_id) == []
    assert begun == [1] and rows("ade_e2e_state", user_id=pq.account)[0]["compaction_due"] is not None


# ---- what is still sealed to a classical key ---------------------------------------------------------------------------

def test_a_post_quantum_account_counts_the_texts_still_sealed_to_a_classical_key(client, owner, keys, device, second):
    mac = paired(client, owner)
    keys.turn_on()
    enrol(keys, second, device)
    assert view(client, owner)["classical_texts"] is None                # a classical account counts none
    assert send_sealed(keys, mac, "step")[0].status_code == 200
    assert send_sealed(keys, mac, "note")[0].status_code == 200
    assert history(mac, [sealed_row(keys, mac, "s1", {"title": "t"})], conversations=True).status_code == 200
    messages = talk_messages(keys, mac, "s1", [("owner", "a"), ("session", "b")])
    assert mac.call("PUT", "/history/talk", {"session": "s1", "messages": messages}).status_code == 200
    up = Upgrade(keys, [(keys.passkey, device), (passkey_of(client, owner, second), second)])
    body = up.body()
    hold = up.send(body).json()["hold"]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    up.taken(body)
    v = view(client, owner)
    assert v["pq"] is True
    assert v["classical_texts"] == {"events": 2, "rows": 1, "talks": 1, "total": 4}
    assert e2e_keys._form1(messages[0]["text"]) and not e2e_keys._form1(body["grants"][0]["sealed"])


def test_a_post_quantum_account_takes_no_new_text_in_form_1_with_encryption_on_or_off(client, owner, keys, device,
                                                                                    second):
    """The upgrade retires every classical key for good: a text sealed to one is
    refused as sealed to a key no longer taken (409, e2e required), on every path, with encryption on and off alike. A
    link from an older release reads no version-3 statement, so it never learns that encryption went off and goes on
    sealing to its classical key: refused, it holds, and nothing it seals after the upgrade stays classical."""
    mac = paired(client, owner)
    keys.turn_on()
    enrol(keys, second, device)
    classical = keys.key()
    up = Upgrade(keys, [(keys.passkey, device), (passkey_of(client, owner, second), second)])
    body = up.body()
    holds.settle(keys.account, now=up.send(body).json()["hold"]["takes_effect_at"])
    up.taken(body)

    def form_1_refused():
        r, _, _ = send_sealed(keys, mac, "step", "after the upgrade", private=classical)
        assert r.status_code == 409 and r.json()["e2e"] == "required", r.text
        r = history(mac, [sealed_row(keys, mac, "s1", {"title": "t"}, private=classical)], conversations=True)
        assert r.status_code == 409 and r.json()["e2e"] == "required", r.text
        messages = talk_messages(keys, mac, "s1", [("owner", "a")], private=classical)
        r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": messages})
        assert r.status_code == 409 and r.json()["e2e"] == "required", r.text
    form_1_refused()
    signed, _ = keys.statement("e2e-off", retires=None)
    r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": signed, "kem": "x-wing"})
    assert r.status_code == 200, r.text
    holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"])
    assert view(client, owner)["on"] is False
    form_1_refused()
    assert view(client, owner)["classical_texts"] == {"events": 0, "rows": 0, "talks": 0, "total": 0}
    assert post(mac, "step", "in the clear while off").status_code == 200
