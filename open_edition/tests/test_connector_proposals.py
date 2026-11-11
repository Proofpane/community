"""Proposals (ade/proposals.py, ade/connector.py): what a connected app proposes is recorded and does nothing else; each
answer says it is not done; the limits; the states, expired and void read from time and from what the proposal is for;
the owner's refusal with words the app reads back; the notifier's item without the words; and no session ever reads a
proposal."""

import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from conftest import signed_up
from connector_fixtures import connected
from proofpane_open import oauth, store
from proofpane_open.ade import approvals, connector, e2e, link, proposals, push
from test_ade_link import paired, post as link_post
from test_passkeys import Authenticator, add, confirmed

CONTENT_KEY = X25519PrivateKey.generate().public_key().public_bytes_raw()


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    oauth._failed.clear()
    connector._ws_cache.clear()


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    assert add(client, owner, d).status_code == 200
    return d


@pytest.fixture
def app(client, owner):
    return oauth.authenticate("Bearer " + connected(client, owner)["access_token"])


@pytest.fixture
def mac(client, owner):
    return paired(client, owner, name="mac")


def tool(app, name, **args):
    result = connector.call(app, {"name": name, "arguments": args}, int(time.time()))
    return result["structuredContent"], result["isError"]


def propose(app, name, **args):
    got, error = tool(app, name, **args)
    assert error is False, got
    return got


def event_count():
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ade_link_events").fetchone()[0]


def test_an_instruction_is_recorded_and_nothing_is_sent(client, owner, device, app, mac):
    before = event_count()
    got = propose(app, "propose_instruction", session="link:" + mac.link_id, text="run the tests", why="they broke")
    assert got["done"] is False and got["proposal"]["state"] == "proposed"
    assert got["said"] == ("Not done. It waits for the owner on the ADE's page, at https://open.example.test/app/"
                           "#proposals: until they sign it with a passkey, the session has been told nothing.")
    assert got["data_rule"] == connector.DATA_RULE
    p = got["proposal"]
    assert (p["kind"], p["target"], p["session"], p["text"], p["why"], p["app"]) == \
        ("instruction", "link:" + mac.link_id, "mac", "run the tests", "they broke", "Claude")
    assert len(p["id"]) == 32 and event_count() == before


def test_a_stop_is_recorded_and_the_session_runs_on(client, owner, app, mac):
    got = propose(app, "propose_stop", session="link:" + mac.link_id)
    assert got["said"].endswith("until they accept it, the session runs on.")
    assert link_row(mac.link_id)["stopped_at"] is None


def link_row(link_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_links WHERE id = ?", (link_id,)).fetchone())


def held_command(client, owner, command="git push origin main"):
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    token_id, _ = approvals.create_token(user_id, "mac session")
    return approvals.request({"id": token_id, "user_id": user_id, "name": "mac session"}, command)


def test_an_approval_s_answer_is_recorded_and_the_command_still_waits(client, owner, app):
    r = held_command(client, owner)
    got = propose(app, "propose_approval", approval="approval:" + r["id"], decision="approve", waits_hours=24)
    assert "the command waits" in got["said"] and got["proposal"]["decision"] == "approve"
    assert approvals.list_for_owner(client.get("/api/auth/me", headers=owner).json()["id"])[0]["state"] == "pending"
    with store.connect() as db:
        until = db.execute("SELECT expires_at FROM ade_proposals").fetchone()[0]
    assert until == r["until"]                    # never past its request's end


def test_an_answer_to_a_question_the_owner_was_asked(client, owner, device, app, mac):
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    got = propose(app, "propose_answer", question=f"question:{seq}", text="per user")
    assert got["proposal"]["question"] == "which cap?" and got["proposal"]["target"] == f"question:{seq}"
    paired(client, owner, name="peer")
    peer = link_post(mac, "question", "to a peer", to="peer").json()["seq"]
    got, error = tool(app, "propose_answer", question=f"question:{peer}", text="x")
    assert error and "open question" in got["said"]
    # a handle is question: and 1 to 18 ASCII digits, exactly: anything else, a digit of another script too, is no
    # question, refused as one, never an error of the server's
    for handle in (f"question:{seq}".replace(str(seq)[-1], "²"), f"question:{seq}".replace(str(seq)[-1], "٣"),
                   "question:" + "1" * 19, f" question:{seq}", f"question:{seq} ", f"question:+{seq}", "question:",
                   f"question:{seq}\n"):
        got, error = tool(app, "propose_answer", question=handle, text="x")
        assert error and "open question" in got["said"], handle


def test_a_sealed_question_cannot_be_answered_here(client, owner, device, app, mac):
    seq = link_post(mac, "question", "placeholder", to="owner").json()["seq"]
    envelope = e2e.seal(CONTENT_KEY, "proofpane-e2e/1|u|l|test", {"text": "q", "session": None, "said_at": None})
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET text = ? WHERE seq = ?", (envelope, seq))
    got, error = tool(app, "propose_answer", question=f"question:{seq}", text="per user")
    assert error and "end-to-end encrypted" in got["said"]


@pytest.mark.parametrize("args, words", [({"text": "e2e1.AAAA"}, "begins e2e1."), ({"why": "e2e1.AAAA"}, "begins e2e1."),
                                         ({"text": "e2e1." + "A" * 140}, "begins e2e1.")])
def test_words_shaped_as_an_envelope_are_refused(client, owner, device, app, mac, args, words):
    """Text in the clear that begins e2e1. would be read everywhere as sealed: an app's words and reason may not."""
    got, error = tool(app, "propose_instruction", **dict({"session": "link:" + mac.link_id, "text": "run it"}, **args))
    assert error and words in got["said"], got
    assert proposal_count() == 0


def test_an_answer_is_void_once_its_question_is_sealed_and_never_carries_the_envelope(client, owner, device, app,
                                                                                     mac):
    """Turning encryption on seals an open question in place. A proposed answer to it would show the page an envelope,
    and be refused when signed (its digest is the session's own, not the server's): it is void at the next read, and
    no view of it carries the envelope."""
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    pid = propose(app, "propose_answer", question=f"question:{seq}", text="per user")["proposal"]["id"]
    envelope = e2e.seal(CONTENT_KEY, "proofpane-e2e/1|u|l|test", {"text": "which cap?", "session": None,
                                                                   "said_at": None})
    with store.connect() as db:                         # as e2e_keys.seal_in_place writes it
        db.execute("UPDATE ade_link_events SET text = ?, sealed_in_place = 1 WHERE seq = ?", (envelope, seq))
    v = proposals.read(app["user_id"], pid)
    assert (v["state"], v["said"]) == ("void", "its question was encrypted: the owner answers it on the ADE's page")
    assert v["question"] is None and v["question_encrypted"] is True
    assert client.get("/api/connector/proposals", headers=owner).json()["proposals"] == []
    assert "e2e1." not in json.dumps(read(app, pid))


def test_without_a_passkey_nothing_to_be_signed_can_be_proposed(client, owner, app, mac):
    for name, args in (("propose_instruction", {"session": "link:" + mac.link_id, "text": "x"}),
                       ("propose_resume", {"session": "link:" + mac.link_id})):
        got, error = tool(app, name, **args)
        assert error and "no passkey" in got["said"], name
    assert tool(app, "propose_stop", session="link:" + mac.link_id)[1] is False


@pytest.mark.parametrize("args, words", [
    ({"text": "run\u202e it"}, "U+202E"), ({"text": "ghp_secretvalue"}, "GitHub token"), ({"text": ""}, "not empty"),
    ({"text": "x" * 4001}, "at most 4000"), ({"session": "link:" + "0" * 32}, "no live linked session"),
    ({"session": "mac"}, "no live linked session"), ({"why": "w" * 501}, "why is one line"),
    ({"why": "my key sk-ant-123"}, "Anthropic key"), ({"text": "use ppca_abcdefgh1234"}, "connector token"),
    ({"why": "it was ppcr_abcdefgh1234"}, "connector token"), ({"waits_hours": 25}, "waits_hours"),
    ({"waits_hours": 0}, "waits_hours"), ({"waits_hours": True}, "waits_hours"),
    # what the owner reads is what they sign: no stack of marks drawn over the lines around it, no mark drawn over or
    # around a character, and a reason of one line held to the words' own rules
    ({"text": "run x" + chr(0x0301) * 3}, "U+0078 with more than 2 marks"),
    ({"text": chr(0x0301) * 3 + "run"}, "U+0301 with more than 2 marks"),
    ({"text": "keep it" + chr(0x20E3)}, "U+20E3"), ({"text": "do no" + chr(0x0336) + "t merge"}, "U+0336"),
    ({"why": "two\nlines"}, "why is one line"), ({"why": "one\rline"}, "why is one line"),
    ({"why": "a" + chr(0x200B) + "b"}, "U+200B"), ({"why": "it is x" + chr(0x0338)}, "U+0338"),
    ({"why": "x" + chr(0x0301) * 3}, "more than 2 marks"), ({"why": "e" + chr(0x0301)}, "normal form C"),
])
def test_an_instruction_is_held_to_the_command_rules_before_it_is_recorded(client, owner, device, app, mac, args,
                                                                           words):
    full = dict({"session": "link:" + mac.link_id, "text": "run the tests"}, **args)
    got, error = tool(app, "propose_instruction", **full)
    assert error and words in got["said"], got
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_proposals").fetchone()[0] == 0


def test_words_with_marks_within_the_bound_and_an_empty_reason_are_taken(client, owner, device, app, mac):
    two = "x" + chr(0x0323) + chr(0x0301)                    # a dot below and an acute: two marks, in their order
    p = propose(app, "propose_instruction", session="link:" + mac.link_id, text="name it " + two, why="  ")
    assert p["proposal"]["text"] == "name it " + two and p["proposal"]["why"] is None
    p = propose(app, "propose_stop", session="link:" + mac.link_id, why="")
    assert p["proposal"]["why"] is None


def test_a_stop_of_a_stopped_session_or_a_resume_of_a_running_one_is_refused(client, owner, device, app, mac):
    got, error = tool(app, "propose_resume", session="link:" + mac.link_id)
    assert error and got["said"] == "That session is not stopped."              # fixed words: no session's name
    client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner)
    got, error = tool(app, "propose_stop", session="link:" + mac.link_id)
    assert error and got["said"] == "That session is stopped already."
    assert tool(app, "propose_resume", session="link:" + mac.link_id)[1] is False


def test_the_limits_on_proposals_waiting_and_made(client, owner, app, mac, monkeypatch):
    monkeypatch.setattr(proposals, "WAITING_PER_LINK", 2)
    other = paired(client, owner, name="cloud", kind="cloud")
    for _ in range(2):
        propose(app, "propose_stop", session="link:" + mac.link_id)
    got, error = tool(app, "propose_stop", session="link:" + mac.link_id)
    assert error and "for this session wait already" in got["said"]
    monkeypatch.setattr(proposals, "WAITING_PER_APP", 3)
    propose(app, "propose_stop", session="link:" + other.link_id)
    got, error = tool(app, "propose_stop", session="link:" + other.link_id)
    assert error and "As many proposals wait" in got["said"]
    monkeypatch.setattr(proposals, "WAITING_PER_APP", 100)
    monkeypatch.setattr(proposals, "WAITING_PER_LINK", 100)
    monkeypatch.setattr(proposals, "MADE_PER_HOUR", 4)
    propose(app, "propose_stop", session="link:" + other.link_id)
    got, error = tool(app, "propose_stop", session="link:" + other.link_id)
    assert error and "an hour" in got["said"]


def test_one_proposal_waits_for_a_held_command_at_a_time(client, owner, app):
    """The page shows one proposal on a held command's item: a second one for that command, from this app or
    another, waits for the first to be answered, withdrawn or gone. Another command takes its own."""
    r = held_command(client, owner)
    first = propose(app, "propose_approval", approval="approval:" + r["id"], decision="approve")["proposal"]["id"]
    other = oauth.authenticate("Bearer " + connected(client, owner)["access_token"])
    for who, decision in ((app, "deny"), (other, "approve")):
        got, error = tool(who, "propose_approval", approval="approval:" + r["id"], decision=decision)
        assert error and "for this command waits already" in got["said"]
    assert propose(app, "propose_approval", approval="approval:" + held_command(client, owner, "make it")["id"],
                   decision="deny")["proposal"]["state"] == "proposed"
    propose(app, "withdraw_proposal", proposal=first)
    assert propose(other, "propose_approval", approval="approval:" + r["id"],
                   decision="deny")["proposal"]["state"] == "proposed"


def read(app, proposal_id):
    return propose(app, "read_proposal", proposal=proposal_id)


def test_a_proposal_expires_at_its_time(client, owner, app, mac):
    p = propose(app, "propose_stop", session="link:" + mac.link_id, waits_hours=1)["proposal"]
    user_id = app["user_id"]
    expires = proposals.read(user_id, p["id"])["expires_at"]      # read from the proposal, not the clock again
    assert proposals.read(user_id, p["id"], now=expires - 1)["state"] == "proposed"
    v = proposals.read(user_id, p["id"], now=expires)
    assert v["state"] == "expired"
    assert proposals.said(v, "https://h").startswith("Not done: its time ended at ")


def test_a_proposal_is_void_once_what_it_was_for_has_gone(client, owner, device, app, mac):
    stop = propose(app, "propose_stop", session="link:" + mac.link_id)["proposal"]["id"]
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    answer = propose(app, "propose_answer", question=f"question:{seq}", text="per user")["proposal"]["id"]
    r = held_command(client, owner)
    approval = propose(app, "propose_approval", approval="approval:" + r["id"], decision="deny")["proposal"]["id"]
    client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert read(app, answer)["proposal"]["state"] == "void"
    assert "question was answered or closed" in read(app, answer)["said"]
    approved = client.post(f"/api/ade/approvals/{r['id']}", headers=owner,
                           json={"approve": True, **confirmed(client, owner, change=f"request-approve:{r['id']}")})
    assert approved.status_code == 200, approved.text
    assert read(app, approval)["said"] == "Not done: the request was answered, or its time ended. Nothing was sent."
    client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner)
    assert read(app, stop)["said"] == "Not done: the session was stopped another way. Nothing was sent."


def test_revoking_the_app_voids_its_waiting_proposals(client, owner, app, mac):
    p = propose(app, "propose_stop", session="link:" + mac.link_id)["proposal"]["id"]
    client.delete(f"/api/connector/grants/{app['id']}", headers=owner)
    v = proposals.read(app["user_id"], p)
    assert (v["state"], v["said"]) == ("void", "its app was disconnected")


def grant_set(grant_id, **columns):
    with store.connect() as db:
        db.execute("UPDATE ade_connector_grants SET " + ", ".join(f"{k} = ?" for k in columns) + " WHERE id = ?",
                   (*columns.values(), grant_id))


def proposal_count():
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ade_proposals").fetchone()[0]


@pytest.mark.parametrize("change", [
    {"revoked_at": 1, "revoked_why": "the owner revoked it"},           # revoked since its token was checked
    {"ends_at": 1},                                                     # its 90 days over
    {"active_at": None},                                                # still waiting on its hold
    {"scopes": "ade.read offline_access"},                              # not allowed to propose
])
def test_a_proposal_is_made_only_while_its_app_s_connection_is_in_effect(client, owner, app, mac, change):
    """make reads the grant again in the transaction that records the proposal: one revoked (a revoke voids the
    proposals already made in its own transaction, so one made as it is made would outlive it), ended, not in effect,
    or without the propose scope makes nothing, whatever its token said when the call came."""
    assert app["active"] is True and "ade.propose" in app["scopes"]
    grant_set(app["id"], **change)
    with pytest.raises(proposals.ProposalError) as e:
        proposals.make(app, "stop", {"session": "link:" + mac.link_id})
    assert e.value.status == 403 and "not in effect, or may not propose" in str(e.value)
    assert proposal_count() == 0


def test_a_waiting_proposal_is_void_once_its_app_is_revoked_or_its_connection_ends(client, owner, app, mac):
    first = propose(app, "propose_stop", session="link:" + mac.link_id)["proposal"]["id"]
    grant_set(app["id"], revoked_at=int(time.time()), revoked_why="the owner revoked it")    # as a revoke in a race
    v = proposals.read(app["user_id"], first)
    assert (v["state"], v["said"]) == ("void", "its app was disconnected")
    second = oauth.authenticate("Bearer " + connected(client, owner)["access_token"])
    p = propose(second, "propose_stop", session="link:" + mac.link_id)["proposal"]["id"]
    grant_set(second["id"], ends_at=int(time.time()) - 1)
    v = proposals.read(app["user_id"], p)
    assert (v["state"], v["said"]) == ("void", "its app's connection ended")
    assert proposals.said(v, "https://h") == "Not done: its app's connection ended. Nothing was sent."


def test_the_owner_refuses_with_words_the_app_reads_back(client, owner, app, mac):
    p = propose(app, "propose_stop", session="link:" + mac.link_id)["proposal"]["id"]
    r = client.post(f"/api/connector/proposals/{p}/refuse", headers=owner, json={"words": "not now"})
    assert r.status_code == 200 and r.json()["state"] == "refused"
    got = read(app, p)
    assert got["done"] is False and got["said"].startswith("Not done: the owner refused it at ")
    assert got["said"].endswith(". Nothing was sent. The owner's words are in refused_with.")
    assert "not now" not in got["said"] and got["proposal"]["refused_with"] == "not now"
    assert got["data_rule"] == connector.DATA_RULE and "said" not in got["proposal"]
    assert client.post(f"/api/connector/proposals/{p}/refuse", headers=owner).status_code == 409
    r = client.post(f"/api/connector/proposals/{p}/refuse", headers=signed_up(client, "other@example.com"))
    assert r.status_code == 404
    for words in ("two\nlines", "x" * 301, "ghp_secretvalue", "ppcc_abcdefgh1234"):
        q = propose(app, "propose_stop", session="link:" + paired(client, owner, name=f"m{len(words)}").link_id)
        assert client.post(f"/api/connector/proposals/{q['proposal']['id']}/refuse", headers=owner,
                           json={"words": words}).status_code == 422


def stored(pid):
    with store.connect() as db:
        row = db.execute("SELECT state, text, why, said, decided_at FROM ade_proposals WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def test_a_decided_proposal_s_words_are_cleared_after_a_day_and_its_row_after_90(client, owner, device, app, mac):
    """A proposal's words, its reason and the owner's words on it are in the clear: kept the day its app may read them
    back, then cleared by the notifier's round; its row goes after 90 days. One that waits keeps its words."""
    pid = propose(app, "propose_instruction", session="link:" + mac.link_id, text="the plan", why="it is late")
    pid = pid["proposal"]["id"]
    waits = propose(app, "propose_stop", session="link:" + paired(client, owner, name="m2").link_id, why="loops",
                    waits_hours=24)["proposal"]["id"]
    assert client.post(f"/api/connector/proposals/{pid}/refuse", headers=owner,
                       json={"words": "not this"}).status_code == 200
    decided = stored(pid)["decided_at"]
    assert proposals.prune(decided + proposals.WORDS_KEPT - 1) == {"expired": 0, "cleared": 0, "deleted": 0}
    assert stored(pid)["text"] == "the plan" and stored(waits)["why"] == "loops"
    push.tick(now=decided + proposals.WORDS_KEPT)              # the notifier's round prunes
    assert {k: stored(pid)[k] for k in ("state", "text", "why", "said")} == \
        {"state": "refused", "text": None, "why": None, "said": None}
    got = read(app, pid)
    assert got["proposal"]["text"] is None and "refused_with" not in got["proposal"] and "not this" not in str(got)
    # the other's day of waiting ended with that round: it is expired, and keeps its words a day from its end
    assert stored(waits)["state"] == "expired" and stored(waits)["why"] == "loops"
    assert proposals.prune(decided + proposals.ROWS_KEPT - 1)["deleted"] == 0
    assert proposals.prune(decided + proposals.ROWS_KEPT)["deleted"] == 1 and stored(pid) is None


def test_a_proposal_that_expired_unread_loses_its_words_a_day_after(client, owner, app, mac):
    pid = propose(app, "propose_stop", session="link:" + mac.link_id, why="it loops", waits_hours=1)["proposal"]["id"]
    expires = int(time.time()) + 3600
    assert proposals.prune(expires)["expired"] == 1
    assert stored(pid)["state"] == "expired" and stored(pid)["decided_at"] <= expires and stored(pid)["why"] == "it loops"
    assert proposals.prune(stored(pid)["decided_at"] + proposals.WORDS_KEPT)["cleared"] == 1
    assert stored(pid)["why"] is None


def test_turning_encryption_on_clears_the_words_of_every_decided_proposal(client, owner, device, app, mac):
    """forget_decided, which turning encryption on calls in its own transaction: the decided lose their words at once,
    an answer whose question sealing in place has just sealed is settled void first and loses them too, and one that
    waits keeps them."""
    refused = propose(app, "propose_stop", session="link:" + mac.link_id, why="it loops")["proposal"]["id"]
    client.post(f"/api/connector/proposals/{refused}/refuse", headers=owner, json={"words": "no"})
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    answer = propose(app, "propose_answer", question=f"question:{seq}", text="per user", why="asked")["proposal"]["id"]
    waits = propose(app, "propose_instruction", session="link:" + mac.link_id, text="keep me")["proposal"]["id"]
    envelope = e2e.seal(CONTENT_KEY, "proofpane-e2e/1|u|l|test", {"text": "which cap?", "session": None,
                                                                   "said_at": None})
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE ade_link_events SET text = ?, sealed_in_place = 1 WHERE seq = ?", (envelope, seq))
        assert proposals.forget_decided(db, app["user_id"], int(time.time())) == 2
    assert [stored(p)["text"] or stored(p)["why"] or stored(p)["said"] for p in (refused, answer)] == [None, None]
    assert stored(answer)["state"] == "void"
    assert stored(waits)["text"] == "keep me"


def test_refuse_all_of_one_app(client, owner, app, mac):
    for _ in range(2):
        propose(app, "propose_stop", session="link:" + mac.link_id)
    r = client.post(f"/api/connector/grants/{app['id']}/refuse-all", headers=owner, json={})
    assert r.json() == {"refused": 2}
    assert client.get("/api/connector/proposals", headers=owner).json()["proposals"] == []


def test_an_app_reads_and_withdraws_only_its_own_and_only_those_that_still_wait(client, owner, app, mac):
    p = propose(app, "propose_stop", session="link:" + mac.link_id, why="it loops")["proposal"]["id"]
    second = oauth.authenticate("Bearer " + connected(client, owner)["access_token"])
    got, error = tool(second, "read_proposal", proposal=p)
    assert error and "No such proposal" in got["said"] and "it loops" not in json.dumps(got)
    [brief] = propose(second, "list_pending")["proposals"]                  # listed as waiting, without its words
    assert (brief["id"], brief["by_this_app"]) == (p, False) and "it loops" not in json.dumps(brief)
    assert propose(app, "read_proposal", proposal=p)["proposal"]["why"] == "it loops"
    got, error = tool(second, "withdraw_proposal", proposal=p)
    assert error and "No such proposal" in got["said"]
    got = propose(app, "withdraw_proposal", proposal=p)
    assert got["proposal"]["state"] == "withdrawn" and "this app withdrew it" in got["said"]
    got, error = tool(app, "withdraw_proposal", proposal=p)
    assert error and "is withdrawn" in got["said"]


def test_the_page_lists_what_waits_and_list_pending_says_whose(client, owner, app, mac):
    p = propose(app, "propose_stop", session="link:" + mac.link_id, why="it loops")["proposal"]["id"]
    [listed] = client.get("/api/connector/proposals", headers=owner).json()["proposals"]
    assert (listed["id"], listed["kind"], listed["app"], listed["why"]) == (p, "stop", "Claude", "it loops")
    got = propose(app, "list_pending")
    assert [(q["id"], q["by"], q["by_this_app"]) for q in got["proposals"]] == [(p, "Claude", True)]
    client.post(f"/api/connector/proposals/{p}/refuse", headers=owner)
    assert [d["id"] for d in propose(app, "list_pending")["decided"]] == [p]


def test_an_answer_carrying_a_connector_token_is_refused(client, owner, device, app, mac):
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    got, error = tool(app, "propose_answer", question=f"question:{seq}", text="per user, ppca_abcdefgh1234")
    assert error and "connector token" in got["said"], got


def test_with_none_waiting_the_page_s_list_takes_no_write_lock(client, owner, app, mac, monkeypatch):
    begun = []
    real = store.connect

    class Watched:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if sql.strip().upper().startswith("BEGIN"):
                begun.append(sql)
            return self.db.execute(sql, *args)

    def connect():
        import contextlib

        @contextlib.contextmanager
        def watched():
            with real() as db:
                yield Watched(db)
        return watched()

    monkeypatch.setattr(store, "connect", connect)
    assert proposals.waiting(app["user_id"]) == []
    assert begun == []
    monkeypatch.setattr(store, "connect", real)
    p = propose(app, "propose_stop", session="link:" + mac.link_id)["proposal"]["id"]
    monkeypatch.setattr(store, "connect", connect)
    assert [v["id"] for v in proposals.waiting(app["user_id"])] == [p]
    assert begun == ["BEGIN IMMEDIATE"]


def test_the_notifier_tells_of_each_proposal_once_without_its_words(client, owner, device, app, mac):
    propose(app, "propose_instruction", session="link:" + mac.link_id, text="rm -rf the secret plans")
    items = [(k, m) for k, m in push.items_for(app["user_id"], [], int(time.time())) if k.startswith("proposal:")]
    [(key, message)] = items
    # in fixed words: never its words, the app's name or the session it is for (tests/test_push_words.py)
    assert (message.title, message.body, message.url) == ("A proposal waits for you", "Open Approvals.",
                                                          "/app/#proposals")
    assert "secret plans" not in repr(message) and "Claude" not in repr(message) and "mac" not in repr(message)


def test_no_session_reads_a_proposal(client, owner, device, app, mac):
    propose(app, "propose_instruction", session="link:" + mac.link_id, text="the proposed words")
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    propose(app, "propose_answer", question=f"question:{seq}", text="the proposed answer")
    feed = mac.call("GET", "/feed").json()
    resume = mac.call("GET", "/resume").json()
    for said in (json.dumps(feed), json.dumps(resume)):
        assert "proposed words" not in said and "proposed answer" not in said
    assert link.question_open
