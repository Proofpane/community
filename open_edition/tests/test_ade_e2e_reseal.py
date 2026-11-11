"""Re-sealing what a post-quantum account stores sealed to a classical key:
GET /api/ade/e2e/reseal pages through the stored form-1 texts, each with the slot it is bound to (a conversation whole,
with what its slots are made of); POST /api/ade/e2e/reseal swaps a batch, all or none, only where what is stored is
what was read, each new value in form 2 under the newest key (a conversation in form 3 beside its key in form 2), with
the recovery code's proof made for that very batch; each swap logged by its hashes alone, the devices told, the
database compacted. The page's part is played here from the design's words: form 1 opened (test_ade_e2e_envelope),
forms 2 and 3 sealed with ade/e2e's sealers (held to the vectors by their own tests) and opened by e2e_pq_page_side."""

import hashlib
import json
import os
import time

import pytest

import e2e_pq_page_side as page
from proofpane_open import store
from proofpane_open.ade import e2e, e2e_keys, holds, link, link_commands, push
from proofpane_core.canonical import canonicalize
from test_ade_e2e import compacted, enrol, history, passkey_of, rows, sealed_row, send_sealed, talk_messages, \
    turned_off, view
from test_ade_e2e import device, keys, notices, owner, second  # noqa: F401  (fixtures)
from test_ade_e2e_envelope import open_envelope, raw_public
from test_ade_e2e_pq_paths import relabelled
from test_ade_e2e_pq_server import Upgrade, hybrid_proof, pq_on
from test_ade_e2e_pq_server import pq  # noqa: F401  (fixture)
from test_ade_link import paired
from test_ade_link_commands import assertion, payload

KEM = {"kem": "x-wing"}


class Stored:
    """A classical account that stored texts in form 1, then was upgraded to post-quantum keys: `classical`, the
    content key they were sealed to; `up`, the upgrade (its content key's seed and its recovery seed)."""

    def __init__(self, client, owner, keys, device, second):
        self.client, self.owner, self.keys = client, owner, keys
        self.mac = paired(client, owner)
        keys.turn_on()
        enrol(keys, second, device)
        self.classical = keys.key()
        r, self.step, _ = send_sealed(keys, self.mac, "step", "built it")
        assert r.status_code == 200, r.text
        r, self.question, _ = send_sealed(keys, self.mac, "question", "may I?", to="owner")
        assert r.status_code == 200, r.text
        listed = [sealed_row(keys, self.mac, "s1", {"title": "t", "project": "~/p", "branch": "main",
                                                     "repositories": []}),
                  sealed_row(keys, self.mac, "s2", {"project": "~/q"}, withheld=True)]
        assert history(self.mac, listed, conversations=True).status_code == 200
        self.said = [("owner", "how is it?"), ("session", "green")]
        messages = talk_messages(keys, self.mac, "s1", self.said, earlier=2)
        r = self.mac.call("PUT", "/history/talk", {"session": "s1", "messages": messages, "earlier": 2})
        assert r.status_code == 200, r.text
        self.up = Upgrade(keys, [(keys.passkey, device), (passkey_of(client, owner, second), second)])
        body = self.up.body()
        hold = self.up.send(body).json()["hold"]
        holds.settle(keys.account, now=hold["takes_effect_at"])
        self.up.taken(body)
        self.key_id = body["statement"]["payload"]["key_id"]

    def seq(self, client_id):
        return rows("ade_link_events", client_id=client_id)[0]["seq"]

    def page(self, after=None):
        r = self.client.get("/api/ade/e2e/reseal", headers=self.owner, params={"after": after} if after else None)
        assert r.status_code == 200, r.text
        return r.json()

    def every(self):
        items, after = [], None
        while True:
            got = self.page(after)
            items += got["items"]
            if got["next"] is None:
                return items
            after = got["next"]

    def again(self, item, seed=None):
        """One item sealed again as the page seals it: opened with the classical key and the slot it came with (a
        conversation's slots made from its value), sealed with the same slot to the newest key."""
        public = page.public_of(seed or self.up.seed)
        old = hashlib.sha256(item["value"].encode() if isinstance(item["value"], str)
                             else canonicalize(item["value"])).hexdigest()
        if isinstance(item["value"], str):
            new = e2e.seal(public, item["slot"], open_envelope(self.classical, item["value"], item["slot"]))
        else:
            t = item["value"]
            count = len(t["messages"])
            clear = [dict(m, text=open_envelope(self.classical, m["text"], e2e.talk_slot(
                self.keys.account, t["link_id"], t["session_id"], i, count, t["earlier"], m["who"], m.get("at"),
                m.get("ms")))["text"]) for i, m in enumerate(t["messages"])]
            key, key_id, sealed = e2e.seal_talk(public, self.keys.account, t["link_id"], t["session_id"],
                                                t["earlier"], clear)
            new = {"messages": sealed, "key": key, "key_id": key_id}
        return {"where": item["where"], "old_sha256": old, "new": new}

    def send(self, swaps, proof=None, **over):
        """POST /reseal with the recovery code's proof made for this batch (or `proof`)."""
        digest = digest_of(swaps)
        if proof is None:
            proof = hybrid_proof(self.client, self.owner, self.up.recovery_seed, "reseal", digest=digest)
        body = dict({"items": swaps, "recovery_proof": proof}, **KEM)
        body.update(over)
        return self.client.post("/api/ade/e2e/reseal", headers=self.owner, json=body)


def digest_of(swaps):
    """The batch's digest as the agreed words give it, written here apart from the server's: the hex sha256 of the RFC
    8785 JSON of [[where, old_sha256, sha256 of new], ...], new's of its UTF-8 or its RFC 8785 JSON."""
    def sha(v):
        return hashlib.sha256(v.encode("utf-8") if isinstance(v, str) else canonicalize(v)).hexdigest()
    return hashlib.sha256(canonicalize([[s["where"], s["old_sha256"], sha(s["new"])] for s in swaps])).hexdigest()


@pytest.fixture
def stored(client, owner, keys, device, second):
    s = Stored(client, owner, keys, device, second)
    yield s
    compacted()


# ---- reading and swapping --------------------------------------------------------------------------------------------

def test_the_page_seals_again_what_is_stored_classical_and_the_server_swaps_it(stored, notices, monkeypatch):
    s, account = stored, stored.keys.account
    monkeypatch.setattr(e2e_keys, "RESEAL_PAGE", 2)                         # three pages
    begun = []
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: begun.append(1))  # run below, by hand
    compacted()                                                             # the upgrade's, done
    assert rows("ade_e2e_state", user_id=account)[0]["compaction_due"] is None
    items = s.every()
    mac = s.mac.link_id
    assert [i["where"] for i in items] == [
        {"table": "events", "id": s.seq(s.step), "field": "text"},           # the open question is left out
        {"table": "rows", "id": f"{mac}:s1", "field": "sealed"},
        {"table": "rows", "id": f"{mac}:s2", "field": "sealed"},
        {"table": "talks", "id": f"{mac}:s1"}]
    assert items[0]["slot"] == e2e.event_slot(account, mac, "step", None, None, s.step)
    assert [i["slot"] for i in items[1:3]] == [e2e.history_slot(account, mac, "s1", False),
                                              e2e.history_slot(account, mac, "s2", True)]
    talk = items[3]
    assert "slot" not in talk and set(talk["value"]) == {"link_id", "session_id", "earlier", "messages"}
    assert (talk["value"]["link_id"], talk["value"]["session_id"], talk["value"]["earlier"]) == (mac, "s1", 2)
    swaps = [s.again(i) for i in items]
    r = s.send(swaps)
    assert r.status_code == 200 and r.json() == {"swapped": 4}, r.text
    (event,) = rows("ade_link_events", client_id=s.step)
    assert page.open_envelope2(s.up.seed, event["text"], items[0]["slot"]) == {"text": "built it", "session": None,
                                                                               "said_at": None}
    listed = {r["session_id"]: r for r in rows("ade_link_history", link_id=mac)}
    assert page.open_envelope2(s.up.seed, listed["s2"]["sealed"], items[2]["slot"]) == {"project": "~/q"}
    (t,) = rows("ade_link_history_talks", link_id=mac)
    tk = page.open_talk_key(s.up.seed, t["key"], e2e.talk_key_slot(account, mac, "s1", 2, 2))
    assert page.talk_key_id(tk) == t["key_id"]
    for i, m in enumerate(json.loads(t["messages"])):
        before = talk["value"]["messages"][i]
        assert {k: v for k, v in m.items() if k != "text"} == {k: v for k, v in before.items() if k != "text"}
        assert page.open_talk_message(tk, m["text"], e2e.talk_slot(account, mac, "s1", i, 2, 2, m["who"], m["at"],
                                                                   m["ms"])) == {"text": s.said[i][1]}
    # each swap logged by its hashes alone; the question still open left as it was, and counted
    log = rows("ade_e2e_reseal_log", user_id=account)
    assert [(json.loads(x["place"]), x["old_sha256"]) for x in log] == [(w["where"], w["old_sha256"]) for w in swaps]
    assert [x["new_sha256"] for x in log] == [hashlib.sha256(
        w["new"].encode() if isinstance(w["new"], str) else canonicalize(w["new"])).hexdigest() for w in swaps]
    assert not any("built it" in json.dumps(x) for x in log)
    assert view(s.client, s.owner)["classical_texts"] == {"events": 1, "rows": 0, "talks": 0, "total": 1}
    assert s.every() == []
    # told to the account's devices, and the database due for compaction, which begins once the batch has committed
    assert [n.title for n in notices][-1] == "Stored texts were sealed again"
    assert notices[-1].body.startswith("4 sealed again to your post-quantum key, with your recovery code.")
    assert begun == [1] and rows("ade_e2e_state", user_id=account)[0]["compaction_due"] is not None
    assert e2e_keys.compact_if_due() is True
    assert rows("ade_e2e_state", user_id=account)[0]["compaction_due"] is None


def test_an_open_question_its_link_sealed_waits_until_it_is_closed(stored):
    s = stored
    q = s.seq(s.question)
    where = {"table": "events", "id": q, "field": "text"}
    assert where not in [i["where"] for i in s.every()]
    (row,) = rows("ade_link_events", client_id=s.question)
    item = {"where": where, "value": row["text"], "slot": e2e.event_slot(s.keys.account, s.mac.link_id, "question",
                                                                          None, None, s.question)}
    r = s.send([s.again(item)])
    assert r.status_code == 404 and r.json()["detail"] == e2e_keys.RESEAL_GONE, r.text
    # one the server sealed in place is answered over its words, not its envelope: it goes while open
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET sealed_in_place = 1 WHERE seq = ?", (q,))
    assert where in [i["where"] for i in s.every()]
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET sealed_in_place = 0 WHERE seq = ?", (q,))
    link.close_question(s.keys.account, q)
    (got,) = [i for i in s.every() if i["where"] == where]
    assert got["slot"] == item["slot"]
    assert s.send([s.again(got)]).json() == {"swapped": 1}


def test_a_question_closed_by_an_answer_not_yet_taken_waits_until_its_link_takes_one(stored):
    """An answer its link has not taken may still be withdrawn or expire, and the question opens again: sealed again
    meanwhile, it could never be answered (an answer's digest is of the envelope its link keeps, and the server checks
    it against the envelope as stored). So it waits until it is closed for good: by an answer taken."""
    s = stored
    q = s.seq(s.question)
    where = {"table": "events", "id": q, "field": "text"}
    (row,) = rows("ade_link_events", client_id=s.question)
    item = {"where": where, "value": row["text"], "slot": e2e.event_slot(s.keys.account, s.mac.link_id, "question",
                                                                          None, None, s.question)}

    def answer():
        p = payload(s.client, s.owner, "answer", s.mac.link_id, "yes", ref=q,
                    digest=link_commands.text_digest(row["text"]))
        r = s.client.post("/api/ade/link/commands", headers=s.owner,
                          json={"payload": p, "credential": assertion(s.keys.device, p)})
        assert r.status_code == 200, r.text
        return r.json()["seq"]
    first = answer()
    with store.connect() as db:
        assert not link.question_open(db, rows("ade_link_events", client_id=s.question)[0], int(time.time()))
    assert where not in [i["where"] for i in s.every()]
    r = s.send([s.again(item)])
    assert r.status_code == 404 and r.json()["detail"] == e2e_keys.RESEAL_GONE, r.text
    link_commands.withdraw(s.keys.account, first)           # open again, and answered over the envelope its link keeps
    second = answer()
    assert where not in [i["where"] for i in s.every()]
    r = s.mac.call("POST", "/take", {"seq": second, "instance": "i1"})
    assert r.status_code == 200, r.text
    (got,) = [i for i in s.every() if i["where"] == where]
    assert s.send([s.again(got)]).json() == {"swapped": 1}


@pytest.mark.parametrize("table", ["events", "talks"])
def test_an_event_or_a_conversation_changed_since_it_was_read_is_not_swapped(stored, table):
    s = stored
    (item,) = [i for i in s.every() if i["where"]["table"] == table]
    swap = s.again(item)
    with store.connect() as db:                     # what is stored there changes, still in form 1
        if table == "events":
            db.execute("UPDATE ade_link_events SET text = ? WHERE client_id = ?",
                       (e2e.seal(raw_public(s.classical), item["slot"], {"text": "other", "session": None,
                                                                         "said_at": None}), s.step))
        else:
            t = item["value"]
            db.execute("UPDATE ade_link_history_talks SET earlier = ? WHERE link_id = ? AND session_id = ?",
                       (t["earlier"] + 1, t["link_id"], t["session_id"]))
    r = s.send([swap])
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.RESEAL_CHANGED, r.text
    assert rows("ade_e2e_reseal_log") == []


def test_a_batch_is_all_or_none_and_swaps_only_what_is_stored_as_it_was_read(stored):
    s = stored
    items = s.every()
    swaps = [s.again(i) for i in items]
    # what is stored at one of them changed since it was read (its link sent the row again): nothing is swapped
    again = e2e.seal(raw_public(s.classical), e2e.history_slot(s.keys.account, s.mac.link_id, "s2", True),
                     {"project": "~/elsewhere"})
    with store.connect() as db:
        db.execute("UPDATE ade_link_history SET sealed = ? WHERE session_id = 's2'", (again,))
    r = s.send(swaps)
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.RESEAL_CHANGED, r.text
    assert rows("ade_link_events", client_id=s.step)[0]["text"] == items[0]["value"]
    assert rows("ade_link_history", session_id="s1")[0]["sealed"] == items[1]["value"]
    assert rows("ade_e2e_reseal_log") == []
    # what is not stored in form 1 there, or never was
    gone = dict(swaps[0], where={"table": "events", "id": 999999, "field": "text"})
    r = s.send([gone])
    assert r.status_code == 404 and r.json()["detail"] == e2e_keys.RESEAL_GONE, r.text
    r = s.send([swaps[1]])
    assert r.status_code == 200 and r.json() == {"swapped": 1}
    r = s.send([swaps[1]])                                          # sealed again already: no longer one to seal
    assert r.status_code == 404, r.text


@pytest.mark.parametrize("case", [
    "a where of another table", "a where with a field more", "an event's id as text", "a row's id with no session",
    "a talk with a field", "old_sha256 not hex", "a text sealed in form 1", "a text sealed to another key",
    "a row longer than a withheld one holds", "a conversation's message moved", "a conversation's time changed",
    "a conversation one message short", "a message in form 2", "a message under another key_id",
    "a conversation key to another key", "a conversation key of 31 bytes", "a conversation in a text's place",
    "a text in a conversation's place", "an item twice", "no items", "a text in form 1 naming the newest key",
    "a row in form 1 naming the newest key", "a message with a field less", "a message with a field more"])
def test_a_batch_in_another_form_is_refused_whole(stored, case):
    s = stored
    items = {i["where"]["table"] + (i["where"]["id"][-2:] if i["where"]["table"] != "events" else ""): i
             for i in s.every()}
    event, row, withheld, talk = items["events"], items["rowss1"], items["rowss2"], items["talkss1"]
    good = [s.again(i) for i in (event, row, withheld, talk)]
    e, r_, w, t = (dict(x) for x in good)
    other = page.public_of(os.urandom(32))
    tn = dict(t["new"])
    if case == "a where of another table":
        e["where"] = dict(e["where"], table="proposals")
    elif case == "a where with a field more":
        e["where"] = dict(e["where"], index=0)
    elif case == "an event's id as text":
        e["where"] = dict(e["where"], id=str(e["where"]["id"]))
    elif case == "a row's id with no session":
        r_["where"] = dict(r_["where"], id=s.mac.link_id)
    elif case == "a talk with a field":
        t["where"] = dict(t["where"], field="messages")
    elif case == "old_sha256 not hex":
        e["old_sha256"] = e["old_sha256"].upper()
    elif case == "a text sealed in form 1":
        e["new"] = e2e.seal(raw_public(s.classical), event["slot"], {"text": "x", "session": None, "said_at": None})
    elif case == "a text sealed to another key":
        e["new"] = e2e.seal(other, event["slot"], {"text": "x", "session": None, "said_at": None})
    elif case == "a row longer than a withheld one holds":
        w["new"] = e2e.seal(page.public_of(s.up.seed), withheld["slot"], {"project": "p" * e2e.HISTORY_WITHHELD_PLAIN})
    elif case == "a conversation's message moved":
        tn["messages"] = list(reversed(tn["messages"]))
    elif case == "a conversation's time changed":
        tn["messages"] = [dict(tn["messages"][0], at=tn["messages"][0]["at"] + 1)] + tn["messages"][1:]
    elif case == "a conversation one message short":
        tn["messages"] = tn["messages"][:1]
    elif case == "a message in form 2":
        tn["messages"] = [dict(m, text=e2e.seal(page.public_of(s.up.seed), "x", {"text": "y"}))
                          for m in tn["messages"]]
    elif case == "a message under another key_id":
        other_talk = s.again(talk)["new"]
        tn["messages"] = [tn["messages"][0], other_talk["messages"][1]]
    elif case == "a conversation key to another key":
        tn["key"] = e2e.seal_talk_key(other, "x", os.urandom(32))
    elif case == "a conversation key of 31 bytes":
        tn["key"] = e2e._seal_hybrid(page.public_of(s.up.seed), "x", os.urandom(31))
    elif case == "a conversation in a text's place":
        e["new"] = tn
    elif case == "a text in a conversation's place":
        tn = e["new"]
    elif case == "a text in form 1 naming the newest key":     # the form refused, whatever key its header names
        e["new"] = relabelled(e2e.seal(raw_public(s.classical), event["slot"], {"text": "x", "session": None,
                                                                                "said_at": None}), s.key_id)
    elif case == "a row in form 1 naming the newest key":
        r_["new"] = relabelled(e2e.seal(raw_public(s.classical), row["slot"], {"title": "x"}), s.key_id)
    elif case == "a message with a field less":
        tn["messages"] = [{k: v for k, v in tn["messages"][0].items() if k != "ms"}] + tn["messages"][1:]
    elif case == "a message with a field more":
        tn["messages"] = [dict(tn["messages"][0], x=1)] + tn["messages"][1:]
    t["new"] = tn
    batch = [e, r_, w, t]
    if case == "an item twice":
        batch = [e, r_, dict(e)]
    if case == "no items":
        batch = []
    r = s.send(batch, proof={"challenge_id": "x", "proof": "y"}) if not batch else s.send(batch)
    assert r.status_code == 422, (case, r.text)
    assert rows("ade_e2e_reseal_log") == [] and rows("ade_link_events", client_id=s.step)[0]["text"] == event["value"]


def test_a_text_sealed_in_place_past_its_path_s_cap_is_sealed_again_no_longer_than_it_was(stored):
    """Sealing in place seals an event's text in the clear as it was (4,000 characters of control characters come to
    24,000 bytes as JSON), past the 16,400 bytes a link's envelope holds: sealed again, it may hold as many bytes as
    it did, and no more."""
    s = stored
    (item,) = [i for i in s.every() if i["where"]["table"] == "events"]
    value = {"text": "\x01" * 4000, "session": None, "said_at": None}
    assert len(canonicalize(value)) > e2e.EVENT_PLAIN
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET text = ?, sealed_in_place = 1 WHERE client_id = ?",
                   (e2e.seal(raw_public(s.classical), item["slot"], value), s.step))
    (item,) = [i for i in s.every() if i["where"]["table"] == "events"]
    swap = s.again(item)
    # one byte more than it held is past it
    longer = dict(swap, new=e2e.seal(page.public_of(s.up.seed), item["slot"], dict(value, text=value["text"] + "a")))
    assert len(canonicalize(dict(value, text=value["text"] + "a"))) == len(canonicalize(value)) + 1
    r = s.send([longer])
    assert r.status_code == 422 and "is not sealed again in its form" in r.json()["detail"], r.text
    assert s.send([swap]).json() == {"swapped": 1}
    assert page.open_envelope2(s.up.seed, rows("ade_link_events", client_id=s.step)[0]["text"], item["slot"]) == value


def test_a_message_sealed_again_holds_no_more_than_a_message_does(stored):
    s = stored
    (talk,) = [i for i in s.every() if i["where"]["table"] == "talks"]
    t, account, tk = talk["value"], s.keys.account, os.urandom(32)
    count = len(t["messages"])
    key = e2e.seal_talk_key(page.public_of(s.up.seed), e2e.talk_key_slot(account, t["link_id"], t["session_id"], count,
                                                                         t["earlier"]), tk)

    def sealed(texts):
        return [dict(m, text=e2e.seal_talk_message(tk, e2e.talk_slot(account, t["link_id"], t["session_id"], i, count,
                                                                     t["earlier"], m["who"], m.get("at"), m.get("ms")),
                                                   {"text": text}))
                for i, (m, text) in enumerate(zip(t["messages"], texts))]
    old = hashlib.sha256(canonicalize(t)).hexdigest()
    long = "x" * e2e.TALK_PLAIN                             # its JSON, {"text": ...}, is 11 bytes more than the cap
    swap = {"where": talk["where"], "old_sha256": old,
            "new": {"messages": sealed([long, "b"]), "key": key, "key_id": e2e.talk_key_id(tk)}}
    r = s.send([swap])
    assert r.status_code == 422 and "A message is not sealed again in its form" in r.json()["detail"], r.text
    swap["new"]["messages"] = sealed([long[:e2e.TALK_PLAIN - 11], "b"])
    assert s.send([swap]).json() == {"swapped": 1}


@pytest.mark.parametrize("total_above", [True, False])
def test_a_conversation_sealed_again_comes_to_its_total_or_to_what_it_holds_and_its_key(stored, monkeypatch,
                                                                                        total_above):
    """Its key counts toward what it comes to, which is at most link.TALK_SEALED_TOTAL, or what it holds now and its
    key's envelope where that is more (e2e_keys._talk_size): never past both."""
    s = stored
    (talk,) = [i for i in s.every() if i["where"]["table"] == "talks"]
    t, account, tk = talk["value"], s.keys.account, os.urandom(32)
    count = len(t["messages"])
    held = sum(len(e2e._raw(m["text"])) for m in t["messages"]) + e2e.TALK_KEY_SEALED
    cap = held + 500 if total_above else held - 500
    monkeypatch.setattr(link, "TALK_SEALED_TOTAL", cap)
    key = e2e.seal_talk_key(page.public_of(s.up.seed), e2e.talk_key_slot(account, t["link_id"], t["session_id"], count,
                                                                         t["earlier"]), tk)
    old = hashlib.sha256(canonicalize(t)).hexdigest()

    def swap(first):
        messages = [dict(m, text=e2e.seal_talk_message(tk, e2e.talk_slot(account, t["link_id"], t["session_id"], i,
                                                                         count, t["earlier"], m["who"], m.get("at"),
                                                                         m.get("ms")), {"text": text}))
                    for i, (m, text) in enumerate(zip(t["messages"], [first, "b"]))]
        return {"where": talk["where"], "old_sha256": old,
                "new": {"messages": messages, "key": key, "key_id": e2e.talk_key_id(tk)}}

    def size(sw):
        return sum(len(e2e._raw(m["text"])) for m in sw["new"]["messages"]) + e2e.TALK_KEY_SEALED
    pad = max(cap, held) - size(swap(""))
    assert pad > 0 and size(swap("x" * pad)) == max(cap, held)
    r = s.send([swap("x" * (pad + 1))])
    assert r.status_code == 422 and f"at most {max(cap, held)} bytes" in r.json()["detail"], r.text
    assert s.send([swap("x" * pad)]).json() == {"swapped": 1}


def test_a_conversation_sealed_in_place_past_its_total_is_sealed_again(client, owner, keys, device, second):
    """Sealing in place seals what a link sent in the clear as it was: 20 messages of 4,000 four-byte characters, which
    put_talk takes (80,000 characters), come to 321,760 bytes in form 1, past link.TALK_SEALED_TOTAL. Held to that
    total, such a conversation could never be sealed again and would stay classical for good: it may come to what it
    holds and its key's envelope."""
    mac = paired(client, owner)
    assert history(mac, [{"id": "s1", "title": "t"}], conversations=True).status_code == 200
    now = int(time.time())
    said = [{"who": "session", "text": chr(0x1F600) * 4000, "at": now - 50 + i} for i in range(20)]
    assert mac.call("PUT", "/history/talk", {"session": "s1", "messages": said}).status_code == 200
    keys.turn_on()                                                  # a classical turn-on seals it in place, form 1
    enrol(keys, second, device)
    (t,) = rows("ade_link_history_talks", session_id="s1")
    assert sum(len(e2e._raw(m["text"])) for m in json.loads(t["messages"])) > link.TALK_SEALED_TOTAL
    s = Stored.__new__(Stored)
    s.client, s.owner, s.keys, s.mac, s.classical = client, owner, keys, mac, keys.key()
    s.up = Upgrade(keys, [(keys.passkey, device), (passkey_of(client, owner, second), second)])
    body = s.up.body()
    holds.settle(keys.account, now=s.up.send(body).json()["hold"]["takes_effect_at"])
    s.up.taken(body)
    (talk,) = [i for i in s.every() if i["where"]["table"] == "talks"]
    swap = s.again(talk)
    assert sum(len(e2e._raw(m["text"])) for m in swap["new"]["messages"]) + e2e.TALK_KEY_SEALED > \
        link.TALK_SEALED_TOTAL
    r = s.send([swap])
    assert r.status_code == 200 and r.json() == {"swapped": 1}, r.text
    assert view(client, owner)["classical_texts"]["talks"] == 0
    compacted()


def half_sealed(client, owner, keys, device, second, clear):
    """A conversation a client sent half sealed, its first message in form 1 and then the messages `clear` in the
    clear, which put_talk takes while encryption is off on a classical account, kept so by a turn-on again that did
    not seal in place, and then the upgrade: (the account's re-seal, a Stored, with `half`, the key its first message
    was sealed to; the conversation's item as GET /reseal serves it). A link never sends one: it seals all or none."""
    mac = paired(client, owner)
    keys.turn_on()
    enrol(keys, second, device)
    half = keys.key()
    turned_off(keys)
    assert history(mac, [{"id": "s1", "title": "t"}], conversations=True).status_code == 200
    messages = talk_messages(keys, mac, "s1", [("owner", "how is it?")] + [("session", t) for t in clear],
                             private=half)
    messages[1:] = [dict(m, text=t) for m, t in zip(messages[1:], clear)]
    r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": messages})
    assert r.status_code == 200, r.text
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private),
                                                                  "seal": False})
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    keys.took(signed, private)
    s = Stored.__new__(Stored)
    s.client, s.owner, s.keys, s.mac, s.classical, s.half = client, owner, keys, mac, keys.key(), half
    s.up = Upgrade(keys, [(keys.passkey, device), (passkey_of(client, owner, second), second)])
    body = s.up.body()
    holds.settle(keys.account, now=s.up.send(body).json()["hold"]["takes_effect_at"])
    s.up.taken(body)
    assert view(client, owner)["classical_texts"]["talks"] == 1
    (talk,) = [i for i in s.every() if i["where"]["table"] == "talks"]
    assert [m["text"] for m in talk["value"]["messages"][1:]] == clear
    return s, talk


def half_sealed_again(s, talk, words=None):
    """`talk` sealed again as the page seals it: its sealed message opened with the key it was sealed to, its messages
    in the clear taken as they are (or `words`, in place of all of them), sealed whole to the newest key."""
    t, account = talk["value"], s.keys.account
    count = len(t["messages"])

    def opened(i, m):
        if not e2e.sealed(m["text"]):
            return m["text"]
        return open_envelope(s.half, m["text"], e2e.talk_slot(account, t["link_id"], t["session_id"], i, count,
                                                             t["earlier"], m["who"], m.get("at"), m.get("ms")))["text"]
    texts = words or [opened(i, m) for i, m in enumerate(t["messages"])]
    key, key_id, sealed = e2e.seal_talk(page.public_of(s.up.seed), account, t["link_id"], t["session_id"],
                                        t["earlier"], [dict(m, text=w) for m, w in zip(t["messages"], texts)])
    return {"where": talk["where"], "old_sha256": hashlib.sha256(canonicalize(t)).hexdigest(),
            "new": {"messages": sealed, "key": key, "key_id": key_id}}


def test_a_conversation_sent_half_sealed_is_sealed_again_whole_its_words_in_the_clear_however_long(
        client, owner, keys, device, second):
    """A message in the clear beside one in form 1 is sealed for the first time as the conversation is sealed again: it
    may hold its words however long they come to sealed. 4,100 four-byte characters, which put_talk takes, are 16,411
    bytes as {"text"}, past the 16,400 a message holds: held to that, such a conversation could never be sealed again
    and would stay classical for good."""
    long = chr(0x1F600) * link.TALK_TEXT
    assert len(canonicalize({"text": long})) == e2e.TALK_PLAIN + 11
    s, talk = half_sealed(client, owner, keys, device, second, [long, "green"])
    # no more than its words: one byte more than they come to, past the cap, is refused
    r = s.send([half_sealed_again(s, talk, ["how is it?", long + "x", "green"])])
    assert r.status_code == 422 and "A message is not sealed again in its form" in r.json()["detail"], r.text
    r = s.send([half_sealed_again(s, talk)])
    assert r.status_code == 200 and r.json() == {"swapped": 1}, r.text
    (t,) = rows("ade_link_history_talks", session_id="s1")
    stored, count = json.loads(t["messages"]), 3
    tk = page.open_talk_key(s.up.seed, t["key"], e2e.talk_key_slot(keys.account, s.mac.link_id, "s1", count, 0))
    assert [page.open_talk_message(tk, m["text"], e2e.talk_slot(keys.account, s.mac.link_id, "s1", i, count, 0,
                                                                 m["who"], m["at"], m.get("ms")))["text"]
            for i, m in enumerate(stored)] == ["how is it?", long, "green"]
    assert view(client, owner)["classical_texts"]["talks"] == 0
    compacted()


def test_a_message_in_the_clear_sealed_again_holds_what_any_message_holds(client, owner, keys, device, second):
    """Where its words come to less sealed, a message in the clear may hold what a message holds, 16,400 bytes, as one
    sealed in form 1 may: no more."""
    s, talk = half_sealed(client, owner, keys, device, second, ["green"])
    most = "x" * (e2e.TALK_PLAIN - 11)                          # {"text": ...} is 11 bytes more
    r = s.send([half_sealed_again(s, talk, ["how is it?", most + "x"])])
    assert r.status_code == 422 and "A message is not sealed again in its form" in r.json()["detail"], r.text
    r = s.send([half_sealed_again(s, talk, ["how is it?", most])])
    assert r.status_code == 200 and r.json() == {"swapped": 1}, r.text
    compacted()


def test_a_conversation_s_message_sealed_in_place_past_the_cap_is_sealed_again_no_longer_than_it_was(
        client, owner, keys, device, second):
    """As for an event (above): a message in the clear sealed in place as it was, 4,000 control characters, which
    put_talk takes, come to 24,011 bytes as {"text"}, past the 16,400 a message holds: sealed again, it may hold as many
    bytes as it did, and no more."""
    mac = paired(client, owner)
    assert history(mac, [{"id": "s1", "title": "t"}], conversations=True).status_code == 200
    words = "\x01" * 4000
    said = [{"who": "session", "text": words, "at": int(time.time())}]
    assert mac.call("PUT", "/history/talk", {"session": "s1", "messages": said}).status_code == 200
    keys.turn_on()                                                  # sealed in place, form 1
    enrol(keys, second, device)
    s = Stored.__new__(Stored)
    s.client, s.owner, s.keys, s.mac, s.classical = client, owner, keys, mac, keys.key()
    s.up = Upgrade(keys, [(keys.passkey, device), (passkey_of(client, owner, second), second)])
    body = s.up.body()
    holds.settle(keys.account, now=s.up.send(body).json()["hold"]["takes_effect_at"])
    s.up.taken(body)
    (talk,) = [i for i in s.every() if i["where"]["table"] == "talks"]
    assert len(e2e._raw(talk["value"]["messages"][0]["text"])) - e2e.OVERHEAD == len(canonicalize({"text": words})) \
        == 24011
    s.half = s.classical
    r = s.send([half_sealed_again(s, talk, [words + "a"])])
    assert r.status_code == 422 and "A message is not sealed again in its form" in r.json()["detail"], r.text
    r = s.send([half_sealed_again(s, talk)])
    assert r.status_code == 200 and r.json() == {"swapped": 1}, r.text
    compacted()


@pytest.mark.parametrize("pad, swapped", [(32, True), (33, False)])
def test_a_conversation_sent_half_sealed_comes_to_what_its_messages_come_to_sealed(client, owner, keys, device,
                                                                                   second, monkeypatch, pad,
                                                                                   swapped):
    """Its messages in the clear count toward what it may come to as form 3 holds their words ({"text"} as RFC 8785
    bytes and the form's 45), its sealed one as it decodes: so the same words fit, a message in form 1 being 32 bytes
    shorter in form 3, and 32 bytes more fit exactly, 33 do not."""
    clear = ["caf" + chr(0xE9) + " " + chr(0x1F600) + ' "q"\t' + chr(1), "green"]
    s, talk = half_sealed(client, owner, keys, device, second, clear)
    t = talk["value"]
    held = len(e2e._raw(t["messages"][0]["text"])) + sum(45 + len(canonicalize({"text": c})) for c in clear) \
        + e2e.TALK_KEY_SEALED
    monkeypatch.setattr(link, "TALK_SEALED_TOTAL", 1000)
    assert held > 1000
    same = half_sealed_again(s, talk)
    assert sum(len(e2e._raw(m["text"])) for m in same["new"]["messages"]) + e2e.TALK_KEY_SEALED == held - 32
    swap = half_sealed_again(s, talk, ["how is it?" + "x" * pad] + clear)
    r = s.send([swap])
    if swapped:
        assert r.status_code == 200 and r.json() == {"swapped": 1}, r.text
        assert view(client, owner)["classical_texts"]["talks"] == 0
    else:
        assert r.status_code == 422 and f"at most {held} bytes" in r.json()["detail"], r.text
        assert view(client, owner)["classical_texts"]["talks"] == 1
    compacted()


# ---- the recovery code's proof, made for that very batch -------------------------------------------------------------

def test_a_batch_takes_the_recovery_code_s_proof_made_for_it_alone(stored):
    s = stored
    items = s.every()
    swaps = [s.again(i) for i in items[:2]]
    r = s.send(swaps, proof=None, recovery_proof=None)
    assert r.status_code == 422 and "recovery proof" in r.json()["detail"], r.text
    for case, proof in (
            ("made for another batch", hybrid_proof(s.client, s.owner, s.up.recovery_seed, "reseal",
                                                    digest=digest_of(swaps[:1]))),
            ("made for another purpose", hybrid_proof(s.client, s.owner, s.up.recovery_seed, "revoke")),
            ("made with another code", hybrid_proof(s.client, s.owner, os.urandom(32), "reseal",
                                                    digest=digest_of(swaps)))):
        r = s.send(swaps, proof=proof)
        assert r.status_code == 403, (case, r.text)
    assert rows("ade_e2e_reseal_log") == []
    proof = hybrid_proof(s.client, s.owner, s.up.recovery_seed, "reseal", digest=digest_of(swaps))
    assert s.send(swaps, proof=proof).json() == {"swapped": 2}
    r = s.send(swaps, proof=proof)                                  # used up: refused before the batch is read
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_STALE, r.text


def test_a_proof_for_re_sealing_names_its_batch_and_no_other_proof_names_one(stored):
    s = stored
    r = s.client.post("/api/ade/e2e/recovery-proof", headers=s.owner, json={"purpose": "reseal"})
    assert r.status_code == 422, r.text
    r = s.client.post("/api/ade/e2e/recovery-proof", headers=s.owner, json={"purpose": "revoke", "digest": "a" * 64})
    assert r.status_code == 422, r.text
    r = s.client.post("/api/ade/e2e/recovery-proof", headers=s.owner, json={"purpose": "reseal", "digest": "A" * 64})
    assert r.status_code == 422, r.text


def test_the_digest_is_the_page_s_binding_of_the_batch():
    swaps = [{"where": {"table": "events", "id": 7, "field": "text"}, "old_sha256": "a" * 64, "new": "e2e1.Ag"},
             {"where": {"table": "talks", "id": "0" * 32 + ":s1"}, "old_sha256": "b" * 64,
              "new": {"messages": [{"who": "owner", "text": "e2e1.Aw", "at": 1}], "key": "e2e1.Ag",
                      "key_id": "c" * 32}}]
    assert e2e_keys.reseal_digest(swaps) == digest_of(swaps)
    # the bytes themselves, so neither side's canonical writer can drift without the other
    text = ('[[{"field":"text","id":7,"table":"events"},"' + "a" * 64 + '","'
            + hashlib.sha256(b"e2e1.Ag").hexdigest() + '"],[{"id":"' + "0" * 32 + ':s1","table":"talks"},"' + "b" * 64
            + '","' + hashlib.sha256(('{"key":"e2e1.Ag","key_id":"' + "c" * 32 + '","messages":[{"at":1,"text":'
                                      '"e2e1.Aw","who":"owner"}]}').encode()).hexdigest() + '"]]')
    assert e2e_keys.reseal_digest(swaps) == hashlib.sha256(text.encode()).hexdigest()


# ---- what re-seals nothing -------------------------------------------------------------------------------------------

def test_a_classical_account_seals_nothing_again(client, owner, keys):
    keys.turn_on()
    r = client.get("/api/ade/e2e/reseal", headers=owner)
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.RESEAL_NOT_PQ, r.text
    r = client.post("/api/ade/e2e/reseal", headers=owner, json={"items": [], "recovery_proof": None})
    assert r.status_code == 422, r.text                             # its form first: no items
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json={"purpose": "reseal", "digest": "a" * 64})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.RESEAL_NOT_PQ, r.text


def test_an_account_whose_flag_says_classical_seals_nothing_again_to_a_post_quantum_key_stated_last(stored):
    """A database put back to before the upgrade keeps the version-3 statement a link sends in after it, while its own
    flag says classical: its roster and grants are the backup's, so the page could not open what would
    be sealed to that key. Nothing is sealed again until the account is upgraded (is_pq), whatever key is stated last."""
    s = stored
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_state SET pq = 0 WHERE user_id = ?", (s.keys.account,))
        assert len(e2e_keys.newest(db, s.keys.account)["public_key"]) > 43         # a post-quantum key, stated last
    r = s.client.get("/api/ade/e2e/reseal", headers=s.owner)
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.RESEAL_NOT_PQ, r.text


def test_a_post_quantum_account_turned_off_seals_nothing_again(client, owner, pq):
    pq_on(pq)
    signed, _ = pq.statement("e2e-off", retires=None)
    r = client.post("/api/ade/e2e/off", headers=owner, json=dict({"statement": signed}, **KEM))
    assert r.status_code == 200, r.text
    holds.settle(pq.account, now=r.json()["hold"]["takes_effect_at"])
    r = client.get("/api/ade/e2e/reseal", headers=owner)
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.RESEAL_OFF, r.text


def test_a_batch_without_kem_is_an_older_page_s(stored):
    from proofpane_open import passkeys
    s = stored
    swaps = [s.again(i) for i in s.every()[:1]]
    r = s.send(swaps, kem=None)
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text


@pytest.mark.parametrize("after", ["x", "3:1", "0:0", "0:01", "1:" + "0" * 32, "2:" + "0" * 31 + ":s1",
                                   "1:" + "0" * 32 + ":", "0:" + "9" * 17])
def test_a_page_s_after_is_the_next_the_page_before_gave(stored, after):
    r = stored.client.get("/api/ade/e2e/reseal", headers=stored.owner, params={"after": after})
    assert r.status_code == 422, (after, r.text)


def test_a_page_is_cut_by_its_values_bytes_past_its_first_item(stored, monkeypatch):
    s = stored
    monkeypatch.setattr(e2e_keys, "RESEAL_PAGE_BYTES", 1)
    first = s.page()
    assert len(first["items"]) == 1 and first["next"] is not None   # the first goes whatever its size
    assert len(s.every()) == 4
    monkeypatch.setattr(e2e_keys, "RESEAL_PAGE_BYTES", 512 * 1024)
    monkeypatch.setattr(e2e_keys, "RESEAL_CHUNK", 1)                  # each table read a row at a time
    assert [i["where"] for i in s.every()] == [i["where"] for i in s.page()["items"]]


def test_the_notice_s_words_say_only_how_many(stored):
    n = push.words("e2e-resealed", count=3)
    assert n.title == "Stored texts were sealed again" and n.body.startswith("3 sealed again") and n.url == \
        "/app/#account"
    with pytest.raises(ValueError):
        push.words("e2e-resealed")
