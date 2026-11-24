"""A session linked through the Claude app connector (ade/connector_link.py; queue 4.325, CONNECTOR-LINK): a session
already running names itself in a call and shows on the owner's page under that name, as its own words; it posts its
steps, notes, questions, waitings and dones and keeps a checkpoint, for the page. It holds no key, no hold of its own
and no pin, so it takes nothing: nothing stored through the link is given back to a session, nothing the owner writes
on the page reaches it through the connector but fixed words, and no command can be addressed to it. A name a session
linked with its own key takes is that one's. Linking is off for an app until the owner turns it on with the
account's confirmation."""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager

import pytest

from conftest import signed_up
from connector_fixtures import confirmation, connected
from proofpane_open import config, oauth, passkeys, store
from proofpane_open.ade import connector, connector_link, e2e_keys, link, proposals
from test_ade_link import paired, post as link_post, revoke
from test_ade_link_commands import device, payload, send  # noqa: F401  (device is a fixture)

OTHER = "other@example.test"
LINK_TOOLS = ["link_session", "link_post", "link_checkpoint"]
# the ten tools the connector had before this one, in the order tools/list gives them, and the sha256 of their
# definitions as tools/list gives them (name, title, description, schemas, annotations), computed at the base commit
TEN = ["list_sessions", "read_conversation", "list_pending", "read_proposal", "propose_instruction", "propose_answer",
       "propose_approval", "propose_stop", "propose_resume", "withdraw_proposal"]
TEN_SHA256 = "d9255772c5a827b40fa9609caf3d057a44dbbfa058ed89cc7a6efcc1dbd1c166"


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    oauth._failed.clear()
    connector._ws_cache.clear()


@pytest.fixture
def owner(client):
    return signed_up(client)


def grant_of(token):
    return oauth.authenticate("Bearer " + token["access_token"])


def let_link(client, headers, grant_id, on=True):
    """The owner turns linking on for one connected app, with the account's confirmation made for that change, or off
    with the sign-in alone."""
    body = {"on": on}
    if on:
        body["confirmation"] = confirmation(client, headers, f"connector-links:{grant_id}")
    return client.put(f"/api/connector/grants/{grant_id}/links", headers=headers, json=body)


@pytest.fixture
def app(client, owner):
    """An app the owner connected and let sessions link through, as oauth.authenticate gives its grant."""
    t = connected(client, owner)
    r = let_link(client, owner, t["grant"])
    assert r.status_code == 200, r.text
    return grant_of(t)


def call(app, name, **args):
    result = connector.call(app, {"name": name, "arguments": args}, int(time.time()))
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result


def ok(app, name, **args):
    result = call(app, name, **args)
    assert result["isError"] is False, result
    return result["structuredContent"]


def refused(app, name, **args):
    result = call(app, name, **args)
    assert result["isError"] is True, result
    return result["structuredContent"]["said"]


def listed(client, headers):
    r = client.get("/api/connector/links", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["links"]


def rows(table="ade_connector_links"):
    with store.connect() as db:
        return [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def strings(value):
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


# ---- the ten tools, as they were -------------------------------------------------------------------------------------

def _definitions(tools):
    return hashlib.sha256(json.dumps(tools, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def test_the_ten_tools_are_listed_first_and_defined_as_they_were():
    """A guard, which holds before the change too: the ten tools' definitions, byte for byte as tools/list gave them
    at the base (their sha256, computed there), and their order."""
    tools = connector.tools_for(["ade.read", "ade.propose"])
    assert [t["name"] for t in tools[:10]] == TEN
    assert _definitions(tools[:10]) == TEN_SHA256


def test_the_link_tools_follow_the_ten_for_an_app_that_may_propose_and_only_for_one(client, owner):
    tools = connector.tools_for(["ade.read", "ade.propose"])
    assert [t["name"] for t in tools] == TEN + LINK_TOOLS
    for t in tools[10:]:
        assert set(t) == {"name", "title", "description", "inputSchema", "outputSchema", "annotations"}
        assert t["inputSchema"]["required"][0] == "session" and t["outputSchema"]["required"] == ["said"]
        assert t["inputSchema"]["additionalProperties"] is False
    # an app allowed only to read lists and calls only what reads: a link's tools write
    assert [t["name"] for t in connector.tools_for(["ade.read"])] == TEN[:4]
    reads = grant_of(connected(client, owner, propose=False))
    for name in LINK_TOOLS:
        with pytest.raises(connector.NeedsScope):
            connector.call(reads, {"name": name, "arguments": {"session": "s"}}, int(time.time()))
    assert rows() == []


# ---- who may link: off until the owner turns it on for the app -------------------------------------------------------

def test_linking_is_off_for_an_app_until_the_owner_turns_it_on_with_a_confirmation(client, owner):
    t = connected(client, owner)
    grant = grant_of(t)
    for name, args in (("link_session", {}), ("link_post", {"kind": "step", "text": "t"}),
                       ("link_checkpoint", {"document": {}, "expected_version": 0})):
        assert refused(grant, name, session="one", **args) == connector_link.LINKS_OFF
    assert rows() == [] and listed(client, owner) == []
    [shown] = client.get("/api/connector/grants", headers=owner).json()["grants"]
    assert shown["links"] is False
    # the sign-in alone does not turn it on, nor a confirmation made for another change, nor one made for another app
    r = client.put(f"/api/connector/grants/{t['grant']}/links", headers=owner, json={"on": True})
    assert r.status_code == 403 and r.json()["detail"] == oauth.NEEDS_CONFIRM
    for change in ("connector-on", f"connector-links:{'0' * 32}"):
        r = client.put(f"/api/connector/grants/{t['grant']}/links", headers=owner,
                       json={"on": True, "confirmation": confirmation(client, owner, change)})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER, r.text
    assert refused(grant, "link_session", session="one") == connector_link.LINKS_OFF
    r = let_link(client, owner, t["grant"])
    assert r.status_code == 200 and r.json()["links"] is True
    assert client.get("/api/connector/grants", headers=owner).json()["grants"][0]["links"] is True
    assert ok(grant, "link_session", session="one")["session"]["new"] is True
    # turned on, it is one of the app's events, by its kind alone
    with store.connect() as db:
        kinds = [r["kind"] for r in db.execute("SELECT kind FROM ade_connector_events ORDER BY id")]
    assert "links-on" in kinds


def test_turning_linking_off_takes_the_sign_in_alone_and_ends_the_sessions_linked_through_the_app(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_post", session="one", kind="step", text="reading")
    r = let_link(client, owner, app["id"], on=False)
    assert r.status_code == 200 and r.json()["links"] is False
    assert listed(client, owner) == []
    [row] = rows()
    assert row["ended_at"] is not None and row["ended_why"] == "linking was turned off for its app"
    assert refused(app, "link_post", session="one", kind="step", text="more") == connector_link.LINKS_OFF
    assert rows("ade_connector_link_events") == []          # an ended session keeps none of what it posted


def test_only_the_account_whose_app_it_is_turns_linking_on_for_it(client, owner, app):
    other = signed_up(client, OTHER)
    oauth.set_account(client.get("/api/auth/me", headers=other).json()["id"], True,
                      confirmation(client, other, "connector-on"))
    r = client.put(f"/api/connector/grants/{app['id']}/links", headers=other,
                   json={"on": False})
    assert r.status_code == 404
    r = client.put(f"/api/connector/grants/{app['id']}/links", headers=other,
                   json={"on": True, "confirmation": confirmation(client, other, f"connector-links:{app['id']}")})
    assert r.status_code == 404
    assert ok(app, "link_session", session="one")["session"]["new"] is True


def test_an_app_that_may_only_read_is_not_let_link(client, owner):
    t = connected(client, owner, propose=False)
    r = let_link(client, owner, t["grant"])
    assert r.status_code == 409 and r.json()["detail"] == connector_link.READS_ONLY


def test_revoking_the_app_ends_the_sessions_linked_through_it(client, owner, app):
    ok(app, "link_session", session="one")
    assert client.delete(f"/api/connector/grants/{app['id']}", headers=owner).status_code == 200
    [row] = rows()
    assert row["ended_at"] is not None and row["ended_why"] == "its app was disconnected"
    assert listed(client, owner) == []


def test_a_connection_still_waiting_on_its_hold_links_nothing(client, owner, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "180")
    t = connected(client, owner)
    assert let_link(client, owner, t["grant"]).status_code == 200
    got = ok(grant_of(t), "link_session", session="one")
    assert got["waiting"] is True and got["said"].startswith("This connection takes effect at ")
    assert rows() == []


def test_a_connection_revoked_since_its_token_was_checked_links_nothing(client, owner, app):
    assert client.delete(f"/api/connector/grants/{app['id']}", headers=owner).status_code == 200
    assert refused(app, "link_session", session="one") == connector_link.NOT_IN_EFFECT
    assert rows() == []


def test_the_app_as_it_stands_now_decides_whatever_its_token_s_check_said(client, owner, monkeypatch):
    """The connection is read again in the call's own transaction: one that waits on its hold, one past its end and one
    that may no longer propose link and post nothing, though the grant the token's check gave says it is in effect."""
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "180")
    t = connected(client, owner)
    assert let_link(client, owner, t["grant"]).status_code == 200
    said = dict(grant_of(t), active=True)                   # as a check made a moment before would have it
    now = int(time.time())
    for work in (lambda: connector_link.session(said, "one", now),
                 lambda: connector_link.post(said, "one", "step", "t", None, now),
                 lambda: connector_link.checkpoint(said, "one", {}, 0, now)):
        with pytest.raises(connector_link.Refused) as e:
            work()
        assert str(e.value) == connector_link.NOT_IN_EFFECT
    with store.connect() as db:                             # in effect, but past its end; then no longer proposing
        db.execute("UPDATE ade_connector_grants SET active_at = ?, connected_at = ?, ends_at = ?", (now, now, now - 1))
    with pytest.raises(connector_link.Refused) as e:
        connector_link.session(said, "one", now)
    assert str(e.value) == connector_link.NOT_IN_EFFECT
    with store.connect() as db:
        db.execute("UPDATE ade_connector_grants SET ends_at = ?, scopes = 'ade.read'", (now + 3600,))
    with pytest.raises(connector_link.Refused) as e:
        connector_link.session(said, "one", now)
    assert str(e.value) == connector_link.NOT_IN_EFFECT
    with store.connect() as db:
        db.execute("UPDATE ade_connector_grants SET scopes = 'ade.read ade.propose'")
    assert connector_link.session(said, "one", now)["new"] is True
    assert rows()[0]["name"] == "one"


# ---- a call names which session it comes for -------------------------------------------------------------------------

@pytest.mark.parametrize("name", [None, "", " ", 7, ["one"], "x" * 61, "-dash", "owner", "ALL", "two  spaces",
                                  "ends ", "tab\there", "quo\"te", "it's", "caf\u00e9", "a\u202eb", "<b>",
                                  "key sk-ant-api03-SECRETVALUE"])
def test_a_call_that_names_no_session_or_one_no_link_may_take_is_refused(client, owner, app, name):
    for tool, args in (("link_session", {}), ("link_post", {"kind": "step", "text": "t"}),
                       ("link_checkpoint", {"document": {}, "expected_version": 0})):
        if name is not None:
            args = dict(args, session=name)
        said = refused(app, tool, **args)
        assert "SECRETVALUE" not in said
    assert rows() == [] and listed(client, owner) == []


def test_the_owner_s_page_shows_the_session_under_the_name_its_call_gave_as_its_own_words(client, owner, app):
    got = ok(app, "link_session", session="Billing fix 2")
    assert got["session"] == {"new": True, "linked_at": got["session"]["linked_at"]}
    assert got["rule"] == connector_link.RULE and got["said"] == connector_link.LINKED_NEW
    [shown] = listed(client, owner)
    # the name as the call gave it, said to be the session's own word, and the app that carried it, by the app's own
    assert shown["name"] == "Billing fix 2" and shown["named_by"] == "session"
    assert shown["app"] == {"id": app["id"], "name": "Claude"}
    assert shown["heard_at"] is not None and shown["checkpoint"] is None and shown["questions"] == []
    assert shown["stop_asked_at"] is None and shown["last"] is None and shown["posts"] == 0
    # asked again, it is the same session, not a new one
    again = ok(app, "link_session", session="Billing fix 2")
    assert again["session"]["new"] is False and again["said"] == connector_link.LINKED
    assert [r["id"] for r in rows()] == [shown["id"]]


def test_one_name_is_one_session_whatever_its_case_and_its_spaces_first_given(client, owner, app):
    first = ok(app, "link_session", session="My Session")
    for spelled in ("my session", "MY SESSION"):
        assert ok(app, "link_session", session=spelled)["session"] == dict(first["session"], new=False)
    ok(app, "link_post", session="my session", kind="step", text="one step")
    [shown] = listed(client, owner)
    assert shown["name"] == "My Session" and shown["posts"] == 1


def test_a_name_a_session_linked_with_its_own_key_holds_is_refused(client, owner, app):
    paired(client, owner, name="mac")
    for name in ("mac", "MAC", "Mac"):
        assert refused(app, "link_session", session=name) == connector_link.NAME_IS_A_LINK
    assert rows() == []


@pytest.mark.parametrize("first", LINK_TOOLS)
def test_a_session_linked_with_its_own_key_takes_the_name_whenever_it_links(client, owner, app, first):
    """One name is one session across both ways of linking, and the one with a key wins: a session linked through an
    app ends when a session linked with its own key takes its name, off the page at once, its posts and checkpoint
    going at its next call, whichever tool that is, or on the timer, whichever comes first."""
    ok(app, "link_session", session="mac")
    ok(app, "link_post", session="mac", kind="question", text="q")
    ok(app, "link_checkpoint", session="mac", document={"doing": "d"}, expected_version=0)
    ok(app, "link_session", session="other")
    keyed = paired(client, owner, name="Mac")
    assert {r["name"]: r for r in rows()}["mac"]["ended_at"] is None        # the page not read yet, nor a call made
    args = {"link_session": {}, "link_post": {"kind": "step", "text": "t"},
            "link_checkpoint": {"document": {"doing": "e"}, "expected_version": 1}}
    assert refused(app, first, session="mac", **args[first]) == connector_link.NAME_IS_A_LINK
    gone = {r["name"]: r for r in rows()}["mac"]
    assert gone["ended_at"] is not None and gone["ended_why"] == connector_link.BY_KEY
    assert gone["checkpoint"] is None and gone["checkpoint_version"] == 0
    assert rows("ade_connector_link_events") == []
    # while the keyed link holds the name, every call under it is told whose the name is: none says it can link again
    for tool in LINK_TOOLS:
        assert refused(app, tool, session="mac" if tool != "link_session" else "MAC", **args[tool]) \
            == connector_link.NAME_IS_A_LINK, tool
    assert [s["name"] for s in listed(client, owner)] == ["other"] and len(rows()) == 2
    # the other is untouched, and the name is free again once the keyed link is revoked
    assert ok(app, "link_post", session="other", kind="step", text="t")["again"] is False
    assert revoke(client, owner, keyed.link_id).status_code == 200
    # the name free again, a post is told why its link ended and that link_session may link it again, which says
    # so or why not: it is not promised a link that another app of the account, or the app's own limit, may refuse
    assert refused(app, "link_post", session="mac", kind="step", text="t") \
        == connector_link.ENDED.format(why=connector_link.BY_KEY)
    assert connector_link.ENDED == ("This session's link ended ({why}). link_session may link it again, if the owner "
                                    "asks you to: its answer says so, or why not.")
    assert ok(app, "link_session", session="mac")["session"]["new"] is True


def test_the_page_s_read_ends_a_session_whose_name_a_keyed_link_took(client, owner, app):
    """Not merely left off the list: ended as the list is read, so that it is not listed again, with what it posted,
    should the keyed link be revoked before it calls or the timer runs."""
    ok(app, "link_session", session="mac")
    ok(app, "link_post", session="mac", kind="step", text="its old step")
    ok(app, "link_checkpoint", session="mac", document={"doing": "d"}, expected_version=0)
    ok(app, "link_session", session="other")
    keyed = paired(client, owner, name="MAC")
    assert [s["name"] for s in listed(client, owner)] == ["other"]
    gone = {r["name"]: r for r in rows()}["mac"]
    assert gone["ended_at"] is not None and gone["ended_why"] == connector_link.BY_KEY and gone["checkpoint"] is None
    assert rows("ade_connector_link_events") == []
    assert revoke(client, owner, keyed.link_id).status_code == 200
    assert [s["name"] for s in listed(client, owner)] == ["other"]
    # the summary's read is the page's too
    ok(app, "link_session", session="mac")
    again = paired(client, owner, name="mac")
    assert client.get("/api/ade/summary", headers=owner).status_code == 200
    # read here, before the page's own list is asked for again: it is the summary's read that ended the second
    assert [r["ended_why"] for r in rows() if r["name"] == "mac"] == [connector_link.BY_KEY] * 2
    assert [s["name"] for s in listed(client, owner)] == ["other"]
    assert again.link_id


def test_the_timer_ends_a_session_whose_name_a_keyed_link_took_and_one_whose_app_is_gone(client, owner, app):
    ok(app, "link_session", session="mac")
    ok(app, "link_post", session="mac", kind="step", text="t")
    ok(app, "link_session", session="kept")
    t = connected(client, owner)
    assert let_link(client, owner, t["grant"]).status_code == 200
    second = grant_of(t)
    ok(second, "link_session", session="orphan")
    ok(second, "link_post", session="orphan", kind="step", text="t")
    paired(client, owner, name="MAC")                   # a name is one whatever its case, on the timer too
    with store.connect() as db:                         # its app's connection gone whole, not only revoked or ended
        db.execute("DELETE FROM ade_connector_tokens WHERE grant_id = ?", (second["id"],))
        db.execute("DELETE FROM ade_connector_grants WHERE id = ?", (second["id"],))
    oauth.prune()
    state = {r["name"]: r for r in rows()}
    assert state["mac"]["ended_why"] == connector_link.BY_KEY and state["mac"]["ended_at"] is not None
    assert state["orphan"]["ended_why"] == connector_link.BY_APP_END and state["orphan"]["ended_at"] is not None
    assert state["kept"]["ended_at"] is None and rows("ade_connector_link_events") == []
    assert [s["name"] for s in listed(client, owner)] == ["kept"]


def test_a_name_held_through_another_app_of_the_account_is_not_shared(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_post", session="one", kind="note", text="the first app's own")
    t = connected(client, owner)
    assert let_link(client, owner, t["grant"]).status_code == 200
    second = grant_of(t)
    assert refused(second, "link_session", session="One") == connector_link.NAME_TAKEN
    assert refused(second, "link_post", session="one", kind="note", text="x") == connector_link.NOT_LINKED
    assert refused(second, "link_checkpoint", session="one", document={}, expected_version=0) \
        == connector_link.NOT_LINKED
    assert len(rows()) == 1 and len(rows("ade_connector_link_events")) == 1


def test_a_post_and_a_checkpoint_need_the_session_linked_first(client, owner, app):
    assert refused(app, "link_post", session="one", kind="step", text="t") == connector_link.NOT_LINKED
    assert refused(app, "link_checkpoint", session="one", document={"doing": "x"}, expected_version=0) \
        == connector_link.NOT_LINKED
    assert rows() == [] and rows("ade_connector_link_events") == []


# ---- what a session posts --------------------------------------------------------------------------------------------

def test_a_session_posts_its_steps_notes_waitings_questions_and_dones(client, owner, app):
    ok(app, "link_session", session="one")
    seqs = []
    for kind in ("step", "note", "waiting", "question", "done"):
        got = ok(app, "link_post", session="one", kind=kind, text=f"a {kind}\nof two lines")
        assert got["said"] == connector_link.POSTED and got["rule"] == connector_link.RULE and got["kind"] == kind
        assert ("id" in got) == (kind == "question") and got.get("id", f"ask:{got['seq']}") == f"ask:{got['seq']}"
        seqs.append(got["seq"])
    assert seqs == sorted(seqs) and len(set(seqs)) == 5
    [shown] = listed(client, owner)
    assert shown["posts"] == 5 and shown["last"]["kind"] == "done" and shown["last"]["text"] == "a done\nof two lines"
    assert [(q["seq"], q["text"]) for q in shown["questions"]] == [(seqs[3], "a question\nof two lines")]
    posts = client.get(f"/api/connector/links/{shown['id']}/posts", headers=owner).json()
    assert [p["kind"] for p in posts["posts"]] == ["done", "question", "waiting", "note", "step"]
    assert posts["before"] is None


@pytest.mark.parametrize("args, words", [
    ({"kind": "instruction", "text": "t"}, "kind is one of"),
    ({"kind": "prompt", "text": "t"}, "kind is one of"),
    ({"kind": None, "text": "t"}, "kind is one of"),
    ({"kind": "step"}, "not empty"),
    ({"kind": "step", "text": "   "}, "not empty"),
    ({"kind": "step", "text": 7}, "not empty"),
    ({"kind": "step", "text": "x" * 4001}, "at most 4000 characters"),
    ({"kind": "note", "text": "e2e1.AAAA"}, "begins e2e1."),
    ({"kind": "note", "text": "a\u202eb"}, "U+202E"),
    ({"kind": "question", "text": "a\u200bb"}, "U+200B"),
    ({"kind": "step", "text": "bell\x07"}, "control character"),
    ({"kind": "step", "text": "a\u0336b"}, "drawn over"),
    ({"kind": "step", "text": "x\u0301\u0301\u0301"}, "more than 2 marks"),
    ({"kind": "step", "text": "see ghp_abcdefSECRETVALUE"}, "shaped like a GitHub token"),
    ({"kind": "step", "text": "token ppca_" + "a" * 43}, "shaped like an ADE connector token"),
    ({"kind": "step", "text": "t", "to": "mac"}, "takes only"),
    ({"kind": "step", "text": "t", "ref": "ask:1"}, "Only a done or a note"),
    ({"kind": "done", "text": "t", "ref": 1}, "ref is one of this session's own questions' ids"),
    ({"kind": "done", "text": "t", "ref": "ask:0"}, "ref is one of this session's own questions' ids"),
    ({"kind": "done", "text": "t", "ref": "question:1"}, "ref is one of this session's own questions' ids"),
    ({"kind": "done", "text": "t", "ref": "ask:" + "9" * 19}, "ref is one of this session's own questions' ids"),
    ({"kind": "done", "text": "t", "ref": "ask:" + "9" * 40}, "ref is one of this session's own questions' ids"),
    ({"kind": "done", "text": "t", "ref": "ask:1\n"}, "ref is one of this session's own questions' ids"),
    ({"kind": "done", "text": "t", "ref": "ask:" + "9" * 18}, "ref is not its own open question"),
])
def test_a_post_that_does_not_hold_is_refused_and_nothing_is_stored(client, owner, app, args, words):
    ok(app, "link_session", session="one")
    said = refused(app, "link_post", session="one", **args)
    assert words in said and "SECRETVALUE" not in said
    assert rows("ade_connector_link_events") == []


def test_a_done_or_a_note_closes_the_session_s_own_question_and_no_other(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_session", session="two")
    mine = ok(app, "link_post", session="one", kind="question", text="which branch?")
    theirs = ok(app, "link_post", session="two", kind="question", text="which region?")
    assert "its own open question" in refused(app, "link_post", session="one", kind="done", text="found it",
                                              ref=theirs["id"])
    assert "its own open question" in refused(app, "link_post", session="one", kind="done", text="found it",
                                              ref="ask:999999")
    closing = ok(app, "link_post", session="one", kind="done", text="found it: main", ref=mine["id"])
    one, two = sorted(listed(client, owner), key=lambda s: s["name"])
    assert one["questions"] == [] and [q["seq"] for q in two["questions"]] == [theirs["seq"]]
    assert "its own open question" in refused(app, "link_post", session="one", kind="note", text="again",
                                              ref=mine["id"])
    [q] = [e for e in rows("ade_connector_link_events") if e["seq"] == mine["seq"]]
    assert q["closed_by"] == f"post:{closing['seq']}" and q["closed_at"] is not None


def test_the_same_post_sent_again_at_once_is_stored_once(client, owner, app):
    ok(app, "link_session", session="one")
    first = ok(app, "link_post", session="one", kind="step", text="building")
    again = ok(app, "link_post", session="one", kind="step", text="building")
    assert again["seq"] == first["seq"] and again["again"] is True and first["again"] is False
    other = ok(app, "link_post", session="one", kind="step", text="testing")
    assert other["seq"] != first["seq"] and len(rows("ade_connector_link_events")) == 2


def test_the_checkpoint_is_one_document_rewritten_against_the_version_last_seen(client, owner, app):
    first = ok(app, "link_session", session="one")
    assert first["checkpoint"] == {"version": 0, "at": None}
    doc = {"doing": "the billing fix", "next": ["tests", "a pull request"], "waiting_on": None, "notes": {"n": 1}}
    got = ok(app, "link_checkpoint", session="one", document=doc, expected_version=0)
    assert got == {"said": connector_link.KEPT, "rule": connector_link.RULE, "version": 1}
    read = ok(app, "link_session", session="one")["checkpoint"]
    assert set(read) == {"version", "at"} and read["version"] == 1 and read["at"] is not None
    # written against a version it no longer is: refused, with the version held alone, and nothing changes
    result = call(app, "link_checkpoint", session="one", document={"doing": "other"}, expected_version=0)
    assert result["isError"] is True
    assert result["structuredContent"] == {"said": connector_link.CHANGED, "version": 1}
    assert ok(app, "link_checkpoint", session="one", document={"doing": "tests"}, expected_version=1)["version"] == 2
    [shown] = listed(client, owner)
    assert shown["checkpoint"] == {"doing": "tests"} and shown["checkpoint_version"] == 2


@pytest.mark.parametrize("args, words", [
    ({"document": "text", "expected_version": 0}, "a JSON object"),
    ({"document": ["a"], "expected_version": 0}, "a JSON object"),
    ({"document": {}, "expected_version": "0"}, "a JSON object"),
    ({"document": {}, "expected_version": True}, "a JSON object"),
    ({"document": {}}, "a JSON object"),
    ({"document": {"notes": "x" * 4001}, "expected_version": 0}, "at most 4000 bytes"),
    ({"document": {"doing": "key sk-ant-api03-SECRETVALUE"}, "expected_version": 0}, "shaped like an Anthropic key"),
    ({"document": {"doing": "a\u202eb"}, "expected_version": 0}, "U+202E"),
    ({"document": {"a\u200bkey": 1}, "expected_version": 0}, "U+200B"),
    ({"document": {"doing": "bell\x07"}, "expected_version": 0}, "control character"),
    ({"document": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}, "expected_version": 0}, "nested"),
    ({"document": "DEEP", "expected_version": 0}, "nested"),
    ({"document": {"n": float("nan")}, "expected_version": 0}, "numbers JSON has"),
    ({"document": {str(n): n for n in range(401)}, "expected_version": 0}, "at most 400 values"),
])
def test_a_checkpoint_that_does_not_hold_is_refused_and_nothing_changes(client, owner, app, args, words):
    ok(app, "link_session", session="one")
    if args.get("document") == "DEEP":              # 3,000 objects inside one another: far past a JSON encoder's own
        deep = {}
        for _ in range(3000):
            deep = {"a": deep}
        args = dict(args, document=deep)
    said = refused(app, "link_checkpoint", session="one", **args)
    assert words in said and "SECRETVALUE" not in said
    assert rows()[0]["checkpoint"] is None and rows()[0]["checkpoint_version"] == 0


def test_the_limits_are_counted_from_the_tables(client, owner, app, monkeypatch):
    monkeypatch.setattr(connector_link, "LINKS_PER_APP", 2)
    monkeypatch.setattr(connector_link, "OPEN_QUESTIONS", 2)
    monkeypatch.setattr(connector_link, "POSTS_PER_HOUR", 5)
    ok(app, "link_session", session="one")
    ok(app, "link_session", session="two")
    assert "at most 2 sessions" in refused(app, "link_session", session="three")
    for n in range(2):
        ok(app, "link_post", session="one", kind="question", text=f"question {n}")
    assert "at most 2 questions open" in refused(app, "link_post", session="one", kind="question", text="a third")
    for n in range(3):
        ok(app, "link_post", session="one", kind="step", text=f"step {n}")
    assert "at most 5 posts an hour" in refused(app, "link_post", session="one", kind="step", text="a sixth")
    # another session's hour is its own
    ok(app, "link_post", session="two", kind="step", text="its own")


def test_a_session_s_posts_are_kept_to_a_bound_the_oldest_going_first_but_never_an_open_question(client, owner, app,
                                                                                                 monkeypatch):
    monkeypatch.setattr(connector_link, "POSTS_KEPT", 4)
    ok(app, "link_session", session="one")
    asked = ok(app, "link_post", session="one", kind="question", text="still open")
    for n in range(6):
        ok(app, "link_post", session="one", kind="step", text=f"step {n}")
    kept = [(e["kind"], e["text"]) for e in rows("ade_connector_link_events")]
    assert kept == [("question", "still open"), ("step", "step 3"), ("step", "step 4"), ("step", "step 5")]
    assert [q["seq"] for q in listed(client, owner)[0]["questions"]] == [asked["seq"]]


# ---- what a session reads: nothing stored, its own or anyone's -------------------------------------------------------

_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ASK = re.compile(r"ask:[1-9][0-9]*")
ANSWER_KEYS = {"said", "rule", "session", "new", "linked_at", "checkpoint", "version", "at", "questions", "id",
               "asked_at", "state", "closed_at", "stop", "posts", "seq", "kind", "again"}


def fixed_words():
    """Every word of its own the module can put in an answer, each whole: its constants, and why a link ended."""
    words = {v for k, v in vars(connector_link).items() if k.isupper() and isinstance(v, str)}
    ended = {connector_link.ENDED.format(why=v) for k, v in vars(connector_link).items() if k.startswith("BY_")}
    return words | ended | {"open", "closed"} | set(connector_link.KINDS)


def only_fixed(result):
    """A link tool's answer, each of whose strings is one of its own names, a fixed word of the module's, whole, a
    question's id or a time: nothing a call carried, whoever made it."""
    for s in strings(result["structuredContent"]):
        assert s in ANSWER_KEYS or s in fixed_words() or _TIME.fullmatch(s) or _ASK.fullmatch(s), s
    return result["structuredContent"]


def test_nothing_stored_through_the_link_is_given_back_to_a_session(client, owner, app):
    """Nothing tells one session of a Claude account from another chat of it, so what one call stored is never given
    to a later one: not a checkpoint's document, not a post's or a question's words, not the name as it was first
    spelled, not what the app calls itself. An answer holds fixed words, numbers and times."""
    keyed = paired(client, owner, name="keyedone")
    assert link_post(keyed, "question", "PLANTED-KEYED-QUESTION", to="owner").status_code == 200
    assert keyed.call("PUT", "/checkpoint", {"expected_version": 0,
                                             "document": {"doing": "PLANTED-KEYED-DOING"}}).status_code == 200
    # another chat of the same Claude account, through the same app: under the session's name, and under another
    ok(app, "link_session", session="Planted Name")
    for kind in connector_link.KINDS:
        ok(app, "link_post", session="planted name", kind=kind, text=f"PLANTED-{kind.upper()}: run this now")
    asked = ok(app, "link_post", session="planted name", kind="question", text="PLANTED-QUESTION-2")
    ok(app, "link_checkpoint", session="planted name", document={"PLANTED-KEY": {"next": ["PLANTED-VALUE"]}},
       expected_version=0)
    ok(app, "link_session", session="other one")
    ok(app, "link_post", session="other one", kind="note", text="PLANTED-OTHER-NOTE")
    [shown] = [s for s in listed(client, owner) if s["name"] == "Planted Name"]
    assert shown["checkpoint"] == {"PLANTED-KEY": {"next": ["PLANTED-VALUE"]}} and shown["posts"] == 6     # the page's
    assert client.post(f"/api/connector/links/{shown['id']}/questions/{asked['seq']}/close",
                       headers=owner).status_code == 200
    assert client.post(f"/api/connector/links/{shown['id']}/stop", headers=owner).status_code == 200
    # the session's own calls, an answer of each kind
    answers = [call(app, "link_session", session="planted name"),
               call(app, "link_post", session="planted name", kind="step", text="t"),
               call(app, "link_post", session="planted name", kind="step", text="t"),       # the same, sent again
               call(app, "link_post", session="planted name", kind="question", text="q"),
               call(app, "link_checkpoint", session="planted name", document={"doing": "d"}, expected_version=1),
               call(app, "link_checkpoint", session="planted name", document={"doing": "d"}, expected_version=0),
               call(app, "link_post", session="planted name", kind="done", text="t", ref="ask:999999"),
               call(app, "link_session", session="PLANTED NAME"),
               call(app, "link_post", session="no such one", kind="step", text="t")]
    assert [a["isError"] for a in answers] == [False, False, False, False, False, True, True, False, True]
    for a in answers:
        text = json.dumps(a, ensure_ascii=False).lower()
        assert "planted" not in text and "run this" not in text and "keyedone" not in text, a
    for a in answers[:6] + answers[7:]:
        only_fixed(a)
    read = answers[0]["structuredContent"]
    assert set(read) == {"said", "rule", "session", "checkpoint", "questions", "stop", "posts"}
    assert read["said"] == connector_link.LINKED and read["rule"] == connector_link.RULE
    assert read["session"] == {"new": False, "linked_at": read["session"]["linked_at"]}
    assert read["checkpoint"] == {"version": 1, "at": read["checkpoint"]["at"]} and read["posts"] == 6
    first, second = read["questions"]
    assert first == {"id": first["id"], "asked_at": first["asked_at"], "state": "open"}
    assert second == {"id": asked["id"], "asked_at": second["asked_at"], "state": "closed",
                      "closed_at": second["closed_at"], "said": connector_link.CLOSED_SAID}
    assert read["stop"] == {"asked_at": read["stop"]["asked_at"], "said": connector_link.STOP_SAID}
    # a version no longer the one held: the version held, and no word of the document; and, the owner having asked
    # this session to stop, the stop and the rule, in their fixed words, as a call that is taken carries them
    assert answers[5]["structuredContent"] == {"said": connector_link.CHANGED, "version": 2, "stop": read["stop"],
                                               "rule": connector_link.RULE}
    # and the words say so, each time: what was stored is the page's, not a later call's
    for word in ("nothing stored", "no instruction but one, in fixed words: that the owner asks this session to stop",
                 "only in this chat"):
        assert word in connector_link.RULE, word
    assert "another" in connector_link.LINKED and "name" in connector_link.LINKED


def test_a_refusal_carries_numbers_alone_whatever_the_module_hands_it(client, owner, app, monkeypatch):
    """The tool's own guard, under the module's: of what a refusal carries beside its words, only a number leaves."""
    ok(app, "link_session", session="one")

    def refuse(grant, name, document, expected_version, now=None):
        raise connector_link.Refused(connector_link.CHANGED, version=3, document={"doing": "PLANTED"}, note="PLANTED",
                                     flag=True, words=["PLANTED"])
    monkeypatch.setattr(connector_link, "checkpoint", refuse)
    result = call(app, "link_checkpoint", session="one", document={}, expected_version=0)
    assert result["isError"] is True and result["structuredContent"] == {"said": connector_link.CHANGED, "version": 3}


def test_another_account_s_session_of_the_same_name_is_another_session(client, owner, app):
    other = signed_up(client, OTHER)
    t = connected(client, other)
    assert let_link(client, other, t["grant"]).status_code == 200
    theirs = grant_of(t)
    ok(app, "link_session", session="shared name")
    ok(app, "link_post", session="shared name", kind="question", text="OWNER-A-QUESTION")
    ok(app, "link_checkpoint", session="shared name", document={"doing": "OWNER-A-DOING"}, expected_version=0)
    got = ok(theirs, "link_session", session="shared name")
    assert got["session"]["new"] is True and got["questions"] == [] and got["posts"] == 0
    assert got["checkpoint"] == {"version": 0, "at": None}
    assert "OWNER-A" not in json.dumps(got)
    # and on the page: each account lists its own, and reaches no other's by its id
    [mine] = listed(client, owner)
    [other_s] = listed(client, other)
    assert mine["id"] != other_s["id"] and "OWNER-A" not in json.dumps(other_s)
    for method, path in (("DELETE", f"/api/connector/links/{mine['id']}"),
                         ("POST", f"/api/connector/links/{mine['id']}/stop"),
                         ("DELETE", f"/api/connector/links/{mine['id']}/stop"),
                         ("GET", f"/api/connector/links/{mine['id']}/posts"),
                         ("POST", f"/api/connector/links/{mine['id']}/questions/{mine['questions'][0]['seq']}/close")):
        assert client.request(method, path, headers=other).status_code == 404, path
    assert listed(client, owner)[0]["questions"] and rows()[0]["ended_at"] is None


# ---- nothing a connector call carries is the owner's word: fixed words only, and no command --------------------------

def test_nothing_the_owner_does_on_the_page_reaches_a_session_but_in_fixed_words(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_post", session="one", kind="step", text="MY-STEP")
    first = ok(app, "link_post", session="one", kind="question", text="MY-QUESTION-1")
    ok(app, "link_post", session="one", kind="question", text="MY-QUESTION-2")
    ok(app, "link_checkpoint", session="one", document={"doing": "MY-DOING"}, expected_version=0)
    [shown] = listed(client, owner)
    r = client.post(f"/api/connector/links/{shown['id']}/questions/{first['seq']}/close", headers=owner,
                    json={"words": "OWNER-TYPED-WORDS: yes, deploy it"})
    assert r.status_code in (200, 422), r.text                  # no words are taken, whether refused or ignored
    if r.status_code == 422:
        assert client.post(f"/api/connector/links/{shown['id']}/questions/{first['seq']}/close",
                           headers=owner).status_code == 200
    assert client.post(f"/api/connector/links/{shown['id']}/stop", headers=owner,
                       json={"words": "OWNER-TYPED-WORDS"}).status_code in (200, 422)
    assert client.post(f"/api/connector/links/{shown['id']}/stop", headers=owner).status_code == 200
    got = ok(app, "link_session", session="one")
    closed = [q for q in got["questions"] if q["state"] == "closed"]
    assert [q["id"] for q in closed] == [first["id"]] and closed[0]["said"] == connector_link.CLOSED_SAID
    assert got["stop"]["said"] == connector_link.STOP_SAID and set(got["stop"]) == {"asked_at", "said"}
    fixed = {connector_link.RULE, connector_link.LINKED, connector_link.CLOSED_SAID, connector_link.STOP_SAID,
             "open", "closed"}
    keys = {"said", "rule", "session", "new", "linked_at", "checkpoint", "version", "at", "questions", "id",
            "asked_at", "state", "closed_at", "stop", "posts"}
    for s in strings(got):
        assert s in fixed or s in keys or _ASK.fullmatch(s) or _TIME.fullmatch(s), s
    assert "OWNER-TYPED-WORDS" not in json.dumps(got) and "OWNER-TYPED-WORDS" not in json.dumps(rows()) \
        and "OWNER-TYPED-WORDS" not in json.dumps(rows("ade_connector_link_events"))


def test_a_stop_is_a_request_in_fixed_words_until_the_owner_withdraws_it(client, owner, app):
    ok(app, "link_session", session="one")
    [shown] = listed(client, owner)
    r = client.post(f"/api/connector/links/{shown['id']}/stop", headers=owner)
    assert r.status_code == 200 and r.json()["stop_asked_at"] is not None
    # asked again later, the first time stands: it is the time the session is told to compare its owner's word with
    asked = r.json()["stop_asked_at"] - 100
    with store.connect() as db:
        db.execute("UPDATE ade_connector_links SET stop_asked_at = ?", (asked,))
    assert client.post(f"/api/connector/links/{shown['id']}/stop", headers=owner).json()["stop_asked_at"] == asked
    assert listed(client, owner)[0]["stop_asked_at"] == asked
    for tool, args in (("link_session", {}), ("link_post", {"kind": "step", "text": "t"}),
                       ("link_checkpoint", {"document": {}, "expected_version": 0})):
        got = ok(app, tool, session="one", **args)
        assert got["stop"]["said"] == connector_link.STOP_SAID
    r = client.delete(f"/api/connector/links/{shown['id']}/stop", headers=owner)
    assert r.status_code == 200 and r.json()["stop_asked_at"] is None
    assert ok(app, "link_session", session="one")["stop"] is None
    assert "stop" not in ok(app, "link_post", session="one", kind="step", text="t2")


def test_no_command_can_be_addressed_to_a_session_linked_through_the_connector(
        client, owner, app, device):  # noqa: F811
    ok(app, "link_session", session="one")
    asked = ok(app, "link_post", session="one", kind="question", text="may I deploy?")
    [shown] = listed(client, owner)
    sid = shown["id"]
    before = rows("ade_connector_link_events")
    # the owner's own passkey signs an instruction, an answer and a resume that name it: none is stored
    for purpose, over in (("instruction", {}), ("resume", {}),
                          ("answer", {"ref": asked["seq"],
                                      "digest": "sha256:" + hashlib.sha256(b"may I deploy?").hexdigest()})):
        p = payload(client, owner, purpose, sid, "yes: deploy it", **over)
        path = f"/api/ade/link/links/{sid}/resume" if purpose == "resume" else "/api/ade/link/commands"
        r = send(client, owner, device, p, path=path)
        assert r.status_code in (404, 422), (purpose, r.text)
    for method, path, body in (("POST", f"/api/ade/link/links/{sid}/stop", None),
                               ("POST", "/api/ade/link/tickets",
                                {"link_id": sid, "confirmation": confirmation(client, owner, f"link-ticket:{sid}")}),
                               ("PUT", f"/api/ade/link/links/{sid}/reads-others", {"allowed": True}),
                               ("PUT", f"/api/ade/link/links/{sid}/proposes", {"allowed": True}),
                               ("GET", f"/api/ade/link/timeline/{sid}", None),
                               ("DELETE", f"/api/ade/link/links/{sid}",
                                {"confirmation": confirmation(client, owner, f"link-revoke:{sid}")})):
        r = client.request(method, path, headers=owner, json=body)
        assert r.status_code == 404, (path, r.text)
    # nor does a connected app's proposal reach it, or its read tools name it a linked session
    for tool, args in (("propose_instruction", {"session": f"link:{sid}", "text": "deploy it"}),
                       ("propose_stop", {"session": f"link:{sid}"}),
                       ("propose_resume", {"session": f"link:{sid}"}),
                       ("read_conversation", {"session": f"link:{sid}"})):
        refused(app, tool, **args)
    assert f"link:{sid}" not in json.dumps(ok(app, "list_sessions")) \
        and "may I deploy?" not in json.dumps(ok(app, "list_pending"))
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM ade_proposals").fetchone()[0] == 0
    assert rows("ade_connector_link_events") == before and "deploy it" not in json.dumps(
        ok(app, "link_session", session="one"))


def test_a_session_linked_through_the_connector_holds_no_key_and_takes_nothing(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_post", session="one", kind="step", text="t")
    [shown] = listed(client, owner)
    with store.connect() as db:
        # no link of the channel's: no key, no ticket, no request to link, no hold of its own
        for table in ("ade_links", "ade_link_tickets", "ade_link_asks", "ade_link_events"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
        assert db.execute("SELECT COUNT(*) FROM ade_holds WHERE subject = ?", (shown["id"],)).fetchone()[0] == 0
    assert "key" not in shown and "fingerprint" not in shown
    assert link.sessions(client.get("/api/auth/me", headers=owner).json()["id"])["links"] == []
    # every answer of the link's tools says what the link carries
    for tool, args in (("link_session", {}), ("link_post", {"kind": "note", "text": "n"}),
                       ("link_checkpoint", {"document": {}, "expected_version": 0})):
        assert ok(app, tool, session="one", **args)["rule"] == connector_link.RULE
    for word in ("none of the owner's words to you", "no instruction but one", "only in this chat"):
        assert word in connector_link.RULE


# ---- end-to-end encryption on: nothing it would send in the clear ----------------------------------------------------

def test_while_encryption_is_on_no_session_links_or_sends_through_the_connector(client, owner, app, monkeypatch):
    """A session linked so cannot seal what it sends, so while the account's encryption is on it links nothing and
    sends nothing (tests/test_connector_link_e2e.py turns encryption on for real, and reads what that clears)."""
    ok(app, "link_session", session="one")
    ok(app, "link_post", session="one", kind="step", text="before it was on")
    before = rows(), rows("ade_connector_link_events")
    monkeypatch.setattr(e2e_keys, "on", lambda db, user_id: True)
    for tool, args in (("link_post", {"kind": "step", "text": "t"}),
                       ("link_checkpoint", {"document": {"doing": "x"}, "expected_version": 0}),
                       ("link_session", {})):
        assert refused(app, tool, session="one", **args) == connector_link.E2E_ON
    assert refused(app, "link_session", session="a new one") == connector_link.E2E_ON
    assert (rows(), rows("ade_connector_link_events")) == before


# ---- the owner's page: remove, close, and what is cleared ------------------------------------------------------------

def test_remove_takes_it_off_the_list_with_the_sign_in_alone_and_its_name_is_free_again(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_post", session="one", kind="question", text="q")
    [shown] = listed(client, owner)
    r = client.delete(f"/api/connector/links/{shown['id']}", headers=owner)
    assert r.status_code == 200 and r.json() == {"removed": shown["id"]}
    assert client.delete(f"/api/connector/links/{shown['id']}", headers=owner).status_code == 404
    assert listed(client, owner) == []
    # its next post is told why its link ended, and links nothing by itself
    assert refused(app, "link_post", session="one", kind="step", text="t") \
        == connector_link.ENDED.format(why=connector_link.BY_OWNER)
    assert listed(client, owner) == [] and rows("ade_connector_link_events") == []
    again = ok(app, "link_session", session="one")
    assert again["session"]["new"] is True and again["questions"] == [] and again["posts"] == 0
    assert listed(client, owner)[0]["id"] != shown["id"]


def test_the_owner_closes_a_question_once_and_only_an_open_one_of_that_session(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_session", session="two")
    q = ok(app, "link_post", session="one", kind="question", text="q")
    step = ok(app, "link_post", session="one", kind="step", text="s")
    one, two = sorted(listed(client, owner), key=lambda s: s["name"])
    for sid, seq in ((two["id"], q["seq"]), (one["id"], step["seq"]), (one["id"], 999999), ("0" * 32, q["seq"])):
        assert client.post(f"/api/connector/links/{sid}/questions/{seq}/close", headers=owner).status_code == 404
    r = client.post(f"/api/connector/links/{one['id']}/questions/{q['seq']}/close", headers=owner)
    assert r.status_code == 200 and r.json()["seq"] == q["seq"] and r.json()["closed_at"] is not None
    assert client.post(f"/api/connector/links/{one['id']}/questions/{q['seq']}/close",
                       headers=owner).status_code == 404
    [row] = [e for e in rows("ade_connector_link_events") if e["seq"] == q["seq"]]
    assert row["closed_by"] == "owner"


def test_what_ended_and_what_is_not_heard_is_cleared(client, owner, app, monkeypatch):
    ok(app, "link_session", session="gone")
    ok(app, "link_post", session="gone", kind="step", text="t")
    ok(app, "link_session", session="quiet")
    ok(app, "link_session", session="live")
    gone, live, quiet = sorted(rows(), key=lambda r: r["name"])
    assert client.delete(f"/api/connector/links/{gone['id']}", headers=owner).status_code == 200
    now = int(time.time())
    with store.connect() as db:
        db.execute("UPDATE ade_connector_links SET heard_at = ?, created_at = ? WHERE id = ?",
                   (now - connector_link.IDLE_ENDS - 10, now - connector_link.IDLE_ENDS - 10, quiet["id"]))
    connector_link.prune(now)
    state = {r["name"]: r for r in rows()}
    assert state["gone"]["ended_at"] is not None and state["live"]["ended_at"] is None
    assert state["quiet"]["ended_at"] == now and state["quiet"]["ended_why"] == "not heard for 30 days"
    # a day after it ended, an ended one goes with its posts
    connector_link.prune(now + connector_link.ENDED_KEPT + 10)
    assert [r["name"] for r in rows()] == ["live"] and rows("ade_connector_link_events") == []
    oauth.prune(now)                                    # the connector's own clearing runs it


def test_each_call_is_one_of_the_app_s_events_by_its_tool_s_name_and_never_its_words(client, owner, app, monkeypatch):
    t = connected(client, owner)
    assert let_link(client, owner, t["grant"]).status_code == 200

    def post(name, args):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        r = client.post("/mcp", headers={"Authorization": "Bearer " + t["access_token"],
                                         "MCP-Protocol-Version": "2025-11-25"}, json=body)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
        return r.json()["result"]

    assert post("link_session", {"session": "WORDS-OF-THE-NAME"})["isError"] is False
    assert post("link_post", {"session": "WORDS-OF-THE-NAME", "kind": "step", "text": "WORDS-OF-THE-STEP"})[
        "isError"] is False
    events = client.get(f"/api/connector/grants/{t['grant']}/events", headers=owner).json()["events"]
    tools = [(e["kind"], e["what"], e["outcome"]) for e in events if e["kind"] == "tool"]
    assert tools == [("tool", "link_post", "ok"), ("tool", "link_session", "ok")]
    assert "WORDS-OF" not in json.dumps(events)


def test_the_linked_sessions_stamp_moves_as_a_session_linked_through_an_app_changes(client, owner, app):
    """The page reads the panel's lists again when the summary's stamp for the linked sessions moves (ade/summary.py):
    it moves as a session links through an app, posts, and is removed, and is the digest of the channel's own list,
    as it was, while none is linked so. The counts stay the channel's own."""
    from proofpane_open.ade import summary

    def read():
        r = client.get("/api/ade/summary", headers=owner)
        assert r.status_code == 200, r.text
        return r.json()

    def channel():
        got = client.get("/api/ade/link/sessions", headers=owner).json()
        return summary.stamp({k: v for k, v in got.items() if k != "server_time"})

    none = read()
    assert none["stamps"]["links"] == channel() and none["links"] == {"live": 0, "alerts": 0}
    ok(app, "link_session", session="one")
    linked = read()
    assert linked["stamps"]["links"] not in (none["stamps"]["links"], channel())
    assert read()["stamps"]["links"] == linked["stamps"]["links"]           # nothing changed: the same stamp
    ok(app, "link_post", session="one", kind="question", text="q")
    posted = read()
    assert posted["stamps"]["links"] != linked["stamps"]["links"] and posted["links"] == {"live": 0, "alerts": 0}
    assert client.delete(f"/api/connector/links/{listed(client, owner)[0]['id']}", headers=owner).status_code == 200
    assert read()["stamps"]["links"] == none["stamps"]["links"] == channel()


def test_the_server_s_instructions_say_what_the_link_s_tools_carry():
    assert connector.INSTRUCTIONS.startswith("This server is the owner's Proofpane ADE. You can read their coding "
                                             "sessions")
    assert "link_session" in connector.INSTRUCTIONS
    assert "That link carries your words to the owner's page and none of theirs to you" in connector.INSTRUCTIONS
    said = connector.INSTRUCTIONS + " ".join(t["description"] for t in connector.TOOLS if t["name"] in LINK_TOOLS)
    for promise in ("gives back", "give back", "given back to", "after a compaction", "its own checkpoint, questions"):
        assert promise not in said, promise
    assert "is not given back" in said and "your own notes" in said


# ---- the narrower guards: each condition of each statement, and each refusal, has a case that notices its loss ------
# (found by narrowing each in turn, by script: a cold read's samples first, then every one the module has)

def two_apps(client, owner, app):
    t = connected(client, owner)
    assert let_link(client, owner, t["grant"]).status_code == 200
    return app, grant_of(t)


def test_each_session_is_told_of_its_own_questions_and_no_other_s(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_session", session="two")
    mine = [ok(app, "link_post", session="one", kind="question", text=f"one's {n}")["id"] for n in range(2)]
    theirs = ok(app, "link_post", session="two", kind="question", text="two's")["id"]
    assert [q["id"] for q in ok(app, "link_session", session="one")["questions"]] == mine
    assert [q["id"] for q in ok(app, "link_session", session="two")["questions"]] == [theirs]
    assert ok(app, "link_session", session="one")["posts"] == 2 and ok(app, "link_session", session="two")["posts"] == 1


def test_a_question_its_own_post_closed_is_not_told_as_closed_by_the_owner(client, owner, app):
    ok(app, "link_session", session="one")
    own = ok(app, "link_post", session="one", kind="question", text="closed by its own done")
    by_owner = ok(app, "link_post", session="one", kind="question", text="closed on the page")
    still = ok(app, "link_post", session="one", kind="question", text="still open")
    ok(app, "link_post", session="one", kind="done", text="found it", ref=own["id"])
    [shown] = listed(client, owner)
    assert client.post(f"/api/connector/links/{shown['id']}/questions/{by_owner['seq']}/close",
                       headers=owner).status_code == 200
    told = ok(app, "link_session", session="one")["questions"]
    # the owner's fixed words are said only of what the owner did: the one its own post closed is not told at all
    assert [(q["id"], q["state"], q.get("said")) for q in told] == [
        (by_owner["id"], "closed", connector_link.CLOSED_SAID), (still["id"], "open", None)]
    # and a day on, the one the owner closed is told no more
    later = connector.call(app, {"name": "link_session", "arguments": {"session": "one"}},
                           int(time.time()) + connector_link.CLOSED_SHOWN + 60)["structuredContent"]["questions"]
    assert [q["id"] for q in later] == [still["id"]]


def test_each_card_lists_its_own_posts_and_shows_its_own_newest(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_session", session="two")
    ok(app, "link_post", session="one", kind="step", text="one's first")
    ok(app, "link_post", session="two", kind="step", text="two's only")
    ok(app, "link_post", session="one", kind="note", text="one's newest")
    one, two = sorted(listed(client, owner), key=lambda s: s["name"])
    assert (one["posts"], one["last"]["text"]) == (2, "one's newest")
    assert (two["posts"], two["last"]["text"]) == (1, "two's only")
    read = lambda sid: [p["text"] for p in client.get(f"/api/connector/links/{sid}/posts",  # noqa: E731
                                                      headers=owner).json()["posts"]]
    assert read(one["id"]) == ["one's newest", "one's first"] and read(two["id"]) == ["two's only"]
    # a page of them begins before the number given, and is its own still
    first = client.get(f"/api/connector/links/{one['id']}/posts?before={one['last']['seq']}", headers=owner).json()
    assert [p["text"] for p in first["posts"]] == ["one's first"]


def test_ending_or_trimming_one_session_leaves_the_others_posts(client, owner, app, monkeypatch):
    for name in ("one", "two", "three"):
        ok(app, "link_session", session=name)
        ok(app, "link_post", session=name, kind="step", text=f"{name}'s step")
        ok(app, "link_checkpoint", session=name, document={"doing": name}, expected_version=0)
    by_name = {s["name"]: s for s in listed(client, owner)}
    assert client.delete(f"/api/connector/links/{by_name['one']['id']}", headers=owner).status_code == 200
    assert sorted(e["text"] for e in rows("ade_connector_link_events")) == ["three's step", "two's step"]
    kept = {s["name"]: s for s in listed(client, owner)}
    assert sorted(kept) == ["three", "two"] and kept["two"]["checkpoint"] == {"doing": "two"}
    # one that posts past what is kept loses its own oldest, never another's
    monkeypatch.setattr(connector_link, "POSTS_KEPT", 2)
    for n in range(3):
        ok(app, "link_post", session="two", kind="step", text=f"two's later {n}")
    assert sorted(e["text"] for e in rows("ade_connector_link_events")) == [
        "three's step", "two's later 1", "two's later 2"]
    # and asking one to stop, or closing its question, is of that one alone
    assert client.post(f"/api/connector/links/{by_name['two']['id']}/stop", headers=owner).status_code == 200
    state = {s["name"]: s["stop_asked_at"] for s in listed(client, owner)}
    assert state["two"] is not None and state["three"] is None
    assert "stop" in ok(app, "link_post", session="two", kind="note", text="n")
    assert "stop" not in ok(app, "link_post", session="three", kind="note", text="n")


def test_a_closed_question_is_trimmed_as_any_post_and_only_a_question_is_closed_by_a_ref(client, owner, app,
                                                                                        monkeypatch):
    ok(app, "link_session", session="one")
    step = ok(app, "link_post", session="one", kind="step", text="a step")
    # a done that refers to a post that is no question closes nothing: it is refused
    assert "its own open question" in refused(app, "link_post", session="one", kind="done", text="d",
                                              ref=f"ask:{step['seq']}")
    assert rows("ade_connector_link_events")[0]["closed_at"] is None
    asked = ok(app, "link_post", session="one", kind="question", text="closed soon")
    ok(app, "link_post", session="one", kind="done", text="found", ref=asked["id"])
    still = ok(app, "link_post", session="one", kind="question", text="still open")
    monkeypatch.setattr(connector_link, "POSTS_KEPT", 2)
    for n in range(2):
        ok(app, "link_post", session="one", kind="note", text=f"note {n}")
    # past what is kept, the question that was closed went with the oldest; the open one stays
    assert [(e["kind"], e["text"]) for e in rows("ade_connector_link_events")] == [
        ("question", "still open"), ("note", "note 1")]
    assert [q["seq"] for q in listed(client, owner)[0]["questions"]] == [still["seq"]]


def at(app, when, name, **args):
    """A link tool called at a time of the case's choosing: its answer, whole."""
    return connector.call(app, {"name": name, "arguments": args}, when)


def test_each_limit_counts_only_what_it_names(client, owner, app, monkeypatch):
    monkeypatch.setattr(connector_link, "LINKS_PER_APP", 2)
    monkeypatch.setattr(connector_link, "OPEN_QUESTIONS", 2)
    monkeypatch.setattr(connector_link, "POSTS_PER_HOUR", 7)
    first, second = two_apps(client, owner, app)
    # the sessions linked at once through one app: another app's are not its own, and an ended one is not linked
    ok(first, "link_session", session="a")
    ok(first, "link_session", session="b")
    assert "at most 2 sessions" in refused(first, "link_session", session="c")
    ok(second, "link_session", session="c")
    ok(second, "link_session", session="d")
    gone = {s["name"]: s for s in listed(client, owner)}["a"]
    assert client.delete(f"/api/connector/links/{gone['id']}", headers=owner).status_code == 200
    assert ok(first, "link_session", session="e")["session"]["new"] is True
    assert "at most 2 sessions" in refused(first, "link_session", session="f")
    # the questions a session has open: not another session's, not its posts that are no question, not a closed one
    for n in range(2):
        ok(second, "link_post", session="c", kind="question", text=f"another session's {n}")
    ok(first, "link_post", session="b", kind="step", text="no question")
    ok(first, "link_post", session="b", kind="note", text="no question either")
    closed = ok(first, "link_post", session="b", kind="question", text="closed at once")
    ok(first, "link_post", session="b", kind="done", text="found", ref=closed["id"])
    ok(first, "link_post", session="b", kind="question", text="open 1")
    ok(first, "link_post", session="b", kind="question", text="open 2")           # the sixth of its hour
    assert "at most 2 questions open" in refused(first, "link_post", session="b", kind="question", text="open 3")
    # the posts of an hour: the hour before does not count
    ok(first, "link_post", session="b", kind="step", text="the seventh")
    now = int(time.time())
    assert "at most 7 posts an hour" in at(first, now + 5, "link_post", session="b", kind="step",
                                           text="an eighth")["structuredContent"]["said"]
    later = at(first, now + 3700, "link_post", session="b", kind="step", text="an hour on")
    assert later["isError"] is False, later


def test_turning_linking_on_or_off_for_one_app_is_of_that_app_alone(client, owner):
    other = signed_up(client, OTHER)
    a1, a2, b1 = connected(client, owner), connected(client, owner), connected(client, other)
    state = lambda: {g["id"]: g["links_at"] is not None for g in rows("ade_connector_grants")}  # noqa: E731
    assert let_link(client, owner, a1["grant"]).status_code == 200
    assert state() == {a1["grant"]: True, a2["grant"]: False, b1["grant"]: False}
    assert refused(grant_of(a2), "link_session", session="one") == connector_link.LINKS_OFF
    assert refused(grant_of(b1), "link_session", session="one") == connector_link.LINKS_OFF
    assert let_link(client, owner, a2["grant"]).status_code == 200
    assert let_link(client, other, b1["grant"]).status_code == 200
    ok(grant_of(a2), "link_session", session="its own")
    ok(grant_of(b1), "link_session", session="its own")
    assert let_link(client, owner, a1["grant"], on=False).status_code == 200
    assert state() == {a1["grant"]: False, a2["grant"]: True, b1["grant"]: True}
    assert [r["ended_at"] for r in rows()] == [None, None]


def test_turning_linking_off_or_revoking_one_app_ends_its_own_live_sessions_alone(client, owner, app):
    first, second = two_apps(client, owner, app)
    for grant, names in ((first, ("removed", "of the first")), (second, ("of the second",))):
        for name in names:
            ok(grant, "link_session", session=name)
            ok(grant, "link_post", session=name, kind="step", text=f"{name}: a step")
    removed = {s["name"]: s for s in listed(client, owner)}["removed"]
    assert client.delete(f"/api/connector/links/{removed['id']}", headers=owner).status_code == 200
    with store.connect() as db:                         # removed a while ago, so a later ending would show
        db.execute("UPDATE ade_connector_links SET ended_at = ended_at - 500 WHERE id = ?", (removed["id"],))
    was = {r["name"]: r for r in rows()}["removed"]
    assert let_link(client, owner, first["id"], on=False).status_code == 200
    state = {r["name"]: r for r in rows()}
    assert state["of the first"]["ended_why"] == connector_link.BY_SWITCH
    assert state["of the second"]["ended_at"] is None
    assert state["removed"] == was                      # one that had ended is not ended again
    assert [e["text"] for e in rows("ade_connector_link_events")] == ["of the second: a step"]
    assert client.delete(f"/api/connector/grants/{second['id']}", headers=owner).status_code == 200
    state = {r["name"]: r for r in rows()}
    assert state["of the second"]["ended_why"] == connector_link.BY_REVOKE and state["removed"] == was
    assert state["of the first"]["ended_why"] == connector_link.BY_SWITCH
    # and a name never linked is told so, whatever of the app's has ended
    assert refused(first, "link_post", session="never linked", kind="step", text="t") in (
        connector_link.LINKS_OFF, connector_link.NOT_LINKED)
    assert let_link(client, owner, first["id"]).status_code == 200
    assert refused(first, "link_post", session="never linked", kind="step", text="t") == connector_link.NOT_LINKED
    assert refused(first, "link_post", session="of the first", kind="step", text="t") \
        == connector_link.ENDED.format(why=connector_link.BY_SWITCH)


def test_the_timer_ends_what_a_revoke_did_not_and_ends_nothing_twice(client, owner, app):
    first, second = two_apps(client, owner, app)
    ok(first, "link_session", session="kept")
    ok(second, "link_session", session="removed")
    ok(second, "link_session", session="left live")
    removed = {s["name"]: s for s in listed(client, owner)}["removed"]
    assert client.delete(f"/api/connector/links/{removed['id']}", headers=owner).status_code == 200
    now = int(time.time())
    with store.connect() as db:                         # revoked, by a write that ended none of its sessions
        db.execute("UPDATE ade_connector_links SET ended_at = ? WHERE id = ?", (now - 500, removed["id"]))
        db.execute("UPDATE ade_connector_grants SET revoked_at = ? WHERE id = ?", (now - 5, second["id"]))
    oauth.prune(now)
    state = {r["name"]: r for r in rows()}
    assert (state["left live"]["ended_at"], state["left live"]["ended_why"]) == (now, connector_link.BY_APP_END)
    assert (state["removed"]["ended_at"], state["removed"]["ended_why"]) == (now - 500, connector_link.BY_OWNER)
    assert state["kept"]["ended_at"] is None


def test_a_session_is_heard_when_it_calls_and_no_other_is_for_that(client, owner, app):
    ok(app, "link_session", session="one")
    ok(app, "link_session", session="two")
    was = {r["name"]: r["heard_at"] for r in rows()}
    later = int(time.time()) + 10 * connector_link.HEARD_EVERY
    assert at(app, later, "link_post", session="one", kind="step", text="t")["isError"] is False
    heard = {r["name"]: r["heard_at"] for r in rows()}
    assert heard == {"one": later, "two": was["two"]}
    # a refused call is not one it is heard by, where it is refused inside its transaction, after the session was
    # found and its hearing written: what the call wrote goes with its refusal (a version not held; a ref that is no
    # open question)
    much_later = later + 5 * connector_link.HEARD_EVERY
    stale = at(app, much_later, "link_checkpoint", session="two", document={"doing": "d"}, expected_version=7)
    assert stale["isError"] is True and stale["structuredContent"]["said"] == connector_link.CHANGED
    assert at(app, much_later, "link_post", session="one", kind="done", text="d", ref="ask:999999")["isError"] is True
    assert {r["name"]: r["heard_at"] for r in rows()} == heard
    # link_session called again, and a checkpoint, are calls it is heard by too
    again = later + 20 * connector_link.HEARD_EVERY
    assert at(app, again, "link_session", session="two")["isError"] is False
    assert {r["name"]: r["heard_at"] for r in rows()} == {"one": later, "two": again}
    last = again + 20 * connector_link.HEARD_EVERY
    kept = at(app, last, "link_checkpoint", session="one", document={"doing": "d"}, expected_version=0)
    assert kept["isError"] is False
    assert {r["name"]: r["heard_at"] for r in rows()} == {"one": last, "two": again}
    # and within a minute of the last time written, nothing is written again
    assert at(app, last + connector_link.HEARD_EVERY - 1, "link_post", session="one", kind="note",
              text="n")["isError"] is False
    assert {r["name"]: r["heard_at"] for r in rows()}["one"] == last


def test_the_confirmation_that_turns_linking_on_is_used_up(client, owner):
    t = connected(client, owner)
    path = f"/api/connector/grants/{t['grant']}/links"
    given = confirmation(client, owner, f"connector-links:{t['grant']}")
    assert client.put(path, headers=owner, json={"on": True, "confirmation": given}).status_code == 200
    assert client.put(path, headers=owner, json={"on": False}).status_code == 200
    again = client.put(path, headers=owner, json={"on": True, "confirmation": given})
    assert again.status_code == 403, again.text
    assert rows("ade_connector_grants")[0]["links_at"] is None
    assert refused(grant_of(t), "link_session", session="one") == connector_link.LINKS_OFF


def test_linking_is_not_turned_on_for_an_app_whose_connection_is_over(client, owner):
    revoked, ended = connected(client, owner), connected(client, owner)
    assert client.delete(f"/api/connector/grants/{revoked['grant']}", headers=owner).status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_connector_grants SET ends_at = ? WHERE id = ?", (int(time.time()) - 10, ended["grant"]))
    for t in (revoked, ended):
        r = let_link(client, owner, t["grant"])
        assert r.status_code == 404 and r.json()["detail"] == connector_link.NO_APP, r.text
    assert [g["links_at"] for g in rows("ade_connector_grants")] == [None, None]


def test_the_timer_ends_the_sessions_of_a_connection_past_its_end(client, owner, app):
    first, second = two_apps(client, owner, app)
    ok(first, "link_session", session="kept")
    ok(second, "link_session", session="over")
    ok(second, "link_post", session="over", kind="step", text="t")
    now = int(time.time())
    with store.connect() as db:                         # past its 90 days: not revoked, and its row is still there
        db.execute("UPDATE ade_connector_grants SET ends_at = ? WHERE id = ?", (now - 10, second["id"]))
    assert refused(second, "link_post", session="over", kind="step", text="t") == connector_link.NOT_IN_EFFECT
    oauth.prune(now)
    state = {r["name"]: r for r in rows()}
    assert state["over"]["ended_at"] == now and state["over"]["ended_why"] == connector_link.BY_APP_END
    assert state["kept"]["ended_at"] is None and rows("ade_connector_link_events") == []


def test_why_a_session_ended_is_told_to_its_own_app_alone(client, owner, app):
    first, second = two_apps(client, owner, app)
    ok(first, "link_session", session="one")
    [shown] = listed(client, owner)
    assert client.delete(f"/api/connector/links/{shown['id']}", headers=owner).status_code == 200
    assert refused(second, "link_post", session="one", kind="step", text="t") == connector_link.NOT_LINKED
    assert refused(second, "link_checkpoint", session="one", document={}, expected_version=0) \
        == connector_link.NOT_LINKED
    assert refused(first, "link_post", session="one", kind="step", text="t") \
        == connector_link.ENDED.format(why=connector_link.BY_OWNER)


@pytest.mark.parametrize("tool, args", [
    ("link_post", {"kind": "step", "text": "t"}),
    ("link_checkpoint", {"document": {"doing": "d"}, "expected_version": 0}),
])
@pytest.mark.parametrize("name", ["one ", " one", "ONE  ", "one\n"])
def test_a_post_and_a_checkpoint_hold_the_name_to_its_rule_though_it_reads_as_a_linked_one(client, owner, app, tool,
                                                                                           args, name):
    """A name is told apart with its case folded and its spaces trimmed, so a name that breaks the rule can read as a
    linked session's: the rule is checked at every call, not only where a session links."""
    ok(app, "link_session", session="one")
    before = rows(), rows("ade_connector_link_events")
    said = refused(app, tool, session=name, **args)
    assert said.startswith("session is the name this session gives itself.") and "A link's name" in said, said
    assert (rows(), rows("ade_connector_link_events")) == before


def mark(code):
    return chr(code)


@pytest.mark.parametrize("document, words", [
    ({"doing": "a" + mark(0x336) + "b"}, "drawn over"),
    ({"a" + mark(0x336): 1}, "drawn over"),
    ({"doing": "x" + mark(0x301) * 3}, "more than 2 marks"),
    ({"doing": "e" + mark(0x301)}, "normal form C"),
    ({"doing": ["a", {"b": mark(0xD800)}]}, "lone surrogate"),
    ({"doing": "token ppca_" + "a" * 43}, "shaped like an ADE connector token"),
    ({"next": [float("inf")]}, "numbers JSON has"),
])
def test_a_checkpoint_s_names_and_texts_each_show_themselves(client, owner, app, document, words):
    ok(app, "link_session", session="one")
    said = refused(app, "link_checkpoint", session="one", document=document, expected_version=0)
    assert words in said, said
    assert rows()[0]["checkpoint"] is None and rows()[0]["checkpoint_version"] == 0



# ---- the cold read's findings (the record's section 6): each case failing before its change ------------------------

def test_the_page_s_read_answers_though_the_ending_it_found_due_cannot_be_written_yet(client, owner, app, monkeypatch):
    """The read is a read: where the ending it found due cannot take the database's write lock, the list is still
    given, without the session whose name a keyed link took, and the ending waits for the next read, call or
    clearing."""
    import sqlite3
    ok(app, "link_session", session="mac")
    ok(app, "link_session", session="other")
    paired(client, owner, name="mac")

    def locked(user_id, ids, now):
        raise sqlite3.OperationalError("database is locked")
    real = connector_link._yield_all
    monkeypatch.setattr(connector_link, "_yield_all", locked)
    assert [s["name"] for s in listed(client, owner)] == ["other"]
    assert client.get("/api/ade/summary", headers=owner).status_code == 200
    assert {r["name"]: r for r in rows()}["mac"]["ended_at"] is None        # not yet
    monkeypatch.setattr(connector_link, "_yield_all", real)
    assert [s["name"] for s in listed(client, owner)] == ["other"]
    assert {r["name"]: r for r in rows()}["mac"]["ended_why"] == connector_link.BY_KEY


def test_an_hour_s_posts_are_refused_past_the_limit_at_the_constants_the_module_has(client, owner, app):
    """The hour's posts are counted from the posts kept, so the posts kept must always hold the hour's: the limit and
    the questions that are never trimmed, together, are no more than what is kept."""
    assert connector_link.POSTS_PER_HOUR + connector_link.OPEN_QUESTIONS <= connector_link.POSTS_KEPT
    ok(app, "link_session", session="one")
    now = int(time.time())
    kept = connector_link.POSTS_KEPT
    for n in range(kept):                               # as many older posts as are kept, each from before the hour,
        connector_link.post(app, "one", "step", f"older {n}", now=now - 7200 - (kept - n) * 13)   # 277 to an hour
    for n in range(connector_link.POSTS_PER_HOUR):
        connector_link.post(app, "one", "step", f"of the hour {n}", now=now)
    said = at(app, now + 1, "link_post", session="one", kind="step", text="one too many")["structuredContent"]["said"]
    assert said == f"A session posts at most {connector_link.POSTS_PER_HOUR} posts an hour.", said
    assert len(rows("ade_connector_link_events")) == connector_link.POSTS_KEPT
    assert at(app, now + 3601, "link_post", session="one", kind="step", text="an hour on")["isError"] is False


@pytest.mark.parametrize("seq", [2 ** 63, 2 ** 63 - 1, 2 ** 64, 0, -1])
def test_a_question_s_number_past_what_is_stored_is_no_question(client, owner, app, seq):
    ok(app, "link_session", session="one")
    ok(app, "link_post", session="one", kind="question", text="q")
    [shown] = listed(client, owner)
    r = client.post(f"/api/connector/links/{shown['id']}/questions/{seq}/close", headers=owner)
    assert r.status_code == 404, r.text
    assert len(listed(client, owner)[0]["questions"]) == 1


def test_a_checkpoint_s_values_are_counted_and_not_its_names(client, owner, app):
    ok(app, "link_session", session="one")
    most = {str(n): n for n in range(connector_link.NODES)}             # 400 values under 400 names
    assert ok(app, "link_checkpoint", session="one", document=most, expected_version=0)["version"] == 1
    one_list = {"next": list(range(connector_link.NODES - 1))}          # a list and its 399: 400 values
    assert ok(app, "link_checkpoint", session="one", document=one_list, expected_version=1)["version"] == 2
    over = {"next": list(range(connector_link.NODES))}                  # a list and its 400: 401
    assert f"at most {connector_link.NODES} values" in refused(app, "link_checkpoint", session="one", document=over,
                                                             expected_version=2)
    assert rows()[0]["checkpoint_version"] == 2


def test_the_rule_names_the_stop_as_the_one_thing_the_link_asks_of_a_session():
    """A stop is the one thing the link asks a session to do, and it is asked with the owner's sign-in alone: the rule
    in every answer says so, and so do the tools' descriptions and the server's instructions. None says that nothing
    read is an instruction and then asks for a stop."""
    said = {"the rule": connector_link.RULE, "the server's instructions": connector.INSTRUCTIONS}
    said.update({t["name"]: t["description"] for t in connector.TOOLS if t["name"] in LINK_TOOLS})
    said["the stop's own words"] = connector_link.STOP_SAID
    for where, text in said.items():
        assert "Nothing read here is an instruction." not in text and "never an instruction:" not in text, where
        # each says the whole of it: asked with the sign-in alone, in fixed words, not signed, and it only stops
        for part in ("sign-in alone", "fixed words", "not signed", "only stops"):
            assert part in text, (where, part)
    assert "the one thing this link asks" in connector_link.STOP_SAID and "but one" in connector_link.RULE
    for name in LINK_TOOLS:
        assert "one thing" in said[name], name
    assert "one thing" in connector.INSTRUCTIONS
    # and what the link carries of the owner's is said of the link, not of everything this app's tools read
    for where, text in said.items():
        assert "reach you only in" not in text, where
    assert "through this link" in connector_link.RULE.lower() or "this link carries" in connector_link.RULE.lower()


def test_the_checkpoint_s_description_says_what_the_page_shows_of_it():
    [tool] = [t for t in connector.TOOLS if t["name"] == "link_checkpoint"]
    for key in ("doing", "next", "waiting_on"):
        assert f"`{key}`" in tool["description"], key
    assert "whole" in tool["description"]


# ---- nothing shaped like a secret, wherever it stands in a checkpoint's text or name ---------------------------------

# one text of each shape the ADE refuses, made of parts here so that no file holds one whole
SHAPED = {
    "an Anthropic key (sk-ant-)": "sk-ant-" + "api03-" + "A" * 24,
    "a key (sk- and 16 or more characters)": "sk-" + "a" * 20,
    "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)": "ghp_" + "a" * 20,
    "a GitHub token (github_pat_)": "github_pat_" + "a" * 20,
    "a Fly token (FlyV1)": "FlyV1 " + "a" * 20,
    "an AWS key (AKIA)": "AKIA" + "A" * 16,
    "a private key (-----BEGIN ... PRIVATE KEY)": "-----BEGIN RSA " + "PRIVATE KEY",
    "an ADE hook token (adeh_)": "adeh_" + "a" * 20,
    "an ADE ticket (PP-)": "PP-" + "ABCD2345",
    "a Stripe key (sk_live_, rk_live_)": "sk_live_" + "a" * 20,
    "a Slack token (xoxb-, xoxp-, ...)": "xoxb-" + "1" * 12,
    "a Google API key (AIza)": "AIza" + "a" * 30,
    "a GitLab token (glpat-)": "glpat-" + "a" * 20,
    "an npm token (npm_)": "npm_" + "a" * 36,
    "a signed web token (eyJ...)": "eyJ" + "a" * 10 + ".eyJ" + "a" * 10 + ".",
    "an ADE connector token (ppca_, ppcr_, ppcc_)": "ppca_" + "a" * 43,
}


def test_every_shape_the_ade_refuses_has_its_text_here():
    """The table above is the session channel's shapes and the connector's, each one: a shape added later fails here
    until its text is added, so the cases below run over every shape there is."""
    shapes = link.SECRET_SHAPES + connector.CONNECTOR_SHAPES
    assert sorted(SHAPED) == sorted(name for name, _ in shapes)
    for name, pattern in shapes:
        assert pattern.search(SHAPED[name]), name


@pytest.mark.parametrize("lead", ["", " ", "key:\n", "\t", "a\nb\n"])
@pytest.mark.parametrize("shape", sorted(SHAPED))
def test_a_checkpoint_holds_nothing_shaped_like_a_secret_wherever_it_stands(client, owner, app, shape, lead):
    """A checkpoint's texts and names are held to the shapes as the texts they are, not as the JSON that stores them:
    there a line break or a tab before a shape is written as a backslash and a letter, and the shape begins no word."""
    ok(app, "link_session", session="one")
    secret = lead + SHAPED[shape]
    for document in ({"notes": secret}, {"list": [{"deep": ["x", secret]}]}, {secret: 1}):
        said = refused(app, "link_checkpoint", session="one", document=document, expected_version=0)
        assert "shaped like" in said and SHAPED[shape] not in said, (shape, lead, said)
    assert rows()[0]["checkpoint"] is None and rows()[0]["checkpoint_version"] == 0
    assert listed(client, owner)[0]["checkpoint"] is None


def test_a_shape_whose_parts_stand_in_two_texts_of_a_checkpoint_is_refused_too(client, owner, app):
    """The whole document is still read as it is stored, beside each of its texts: a private key's first line cut in
    two texts is one line of its JSON."""
    ok(app, "link_session", session="one")
    said = refused(app, "link_checkpoint", session="one", expected_version=0,
                   document={"a": "-----BEGIN RSA", "b": "PRIVATE KEY-----"})
    assert "shaped like a private key" in said
    assert rows()[0]["checkpoint"] is None


# ---- a call that is refused is told of the stop too ------------------------------------------------------------------

def test_a_refused_call_is_told_the_owner_asked_it_to_stop_as_a_call_that_is_taken_is(
        client, owner, app, monkeypatch):
    """The session whose calls are refused is the one that most needs telling: past its hour's posts, against a
    version not held, with a ref that is no open question, or with words that do not hold, its answer carries the
    stop and the rule as a taken call's does. It carries neither while no stop is asked, nor for a name that is not
    its app's live session, nor once the owner has withdrawn the request."""
    monkeypatch.setattr(connector_link, "POSTS_PER_HOUR", 2)
    ok(app, "link_session", session="one")
    ok(app, "link_post", session="one", kind="question", text="q")
    ok(app, "link_post", session="one", kind="step", text="s")
    refusals = [("link_post", dict(kind="step", text="over the hour")),
                ("link_post", dict(kind="done", text="d", ref="ask:999999")),
                ("link_checkpoint", dict(document={"doing": "d"}, expected_version=7)),
                ("link_post", dict(kind="step", text="see ghp_" + "a" * 20)),
                ("link_post", dict(kind="nonsense", text="t")),
                ("link_post", dict(kind="step", text="t", to="mac")),           # an argument the tool does not take
                ("link_checkpoint", dict(document="no object", expected_version=0))]
    for name, args in refusals:
        got = call(app, name, session="one", **args)
        assert got["isError"] is True and not {"stop", "rule"} & set(got["structuredContent"]), (name, args)
    [shown] = listed(client, owner)
    assert client.post(f"/api/connector/links/{shown['id']}/stop", headers=owner).status_code == 200
    asked = rows()[0]["stop_asked_at"]
    for name, args in refusals:
        got = call(app, name, session="one", **args)
        assert got["isError"] is True, (name, args)
        assert got["structuredContent"].get("stop") == {"asked_at": connector._iso(asked),
                                                        "said": connector_link.STOP_SAID}, (name, args)
        assert got["structuredContent"].get("rule") == connector_link.RULE, (name, args)
    # a name that is not its app's live session is told of no stop: one never linked, and one no link may take
    for name in ("two", "-dash", None, 7):
        got = call(app, "link_post", session=name, kind="step", text="t")
        assert got["isError"] is True and "stop" not in got["structuredContent"], name
    # nor is another app of the account, calling under the name of this app's session, or another account's app
    _, other_app = two_apps(client, owner, app)
    theirs = signed_up(client, OTHER)
    t = connected(client, theirs)
    assert let_link(client, theirs, t["grant"]).status_code == 200
    for caller in (other_app, grant_of(t)):
        for tool, args in (("link_post", dict(kind="step", text="t")), ("link_checkpoint", dict(document={}))):
            got = call(caller, tool, session="one", **args)
            assert got["isError"] is True and not {"stop", "rule"} & set(got["structuredContent"]), (caller["id"], tool)
    # withdrawn, a refusal carries it no longer
    assert client.delete(f"/api/connector/links/{shown['id']}/stop", headers=owner).status_code == 200
    got = call(app, "link_post", session="one", kind="step", text="over the hour")
    assert got["isError"] is True and "stop" not in got["structuredContent"]


# ---- the read ends only a session whose name a keyed link still holds as it writes -----------------------------------

def test_the_page_s_read_does_not_end_a_session_whose_name_is_free_again_by_its_write(client, owner, app, monkeypatch):
    """The ending is made in a transaction of its own after the read, so it asks again, under its write lock, who
    holds the name: a keyed link revoked between the read and the write leaves the session and its posts as they
    were."""
    ok(app, "link_session", session="mac")
    ok(app, "link_post", session="mac", kind="step", text="kept, the name being free again")
    keyed = paired(client, owner, name="mac")
    real = connector_link._yield_all

    def between(user_id, ids, now):
        assert revoke(client, owner, keyed.link_id).status_code == 200
        return real(user_id, ids, now)
    monkeypatch.setattr(connector_link, "_yield_all", between)
    assert listed(client, owner) == []                  # the read found the name taken
    monkeypatch.setattr(connector_link, "_yield_all", real)
    assert {r["name"]: r["ended_why"] for r in rows()} == {"mac": None}
    assert [e["text"] for e in rows("ade_connector_link_events")] == ["kept, the name being free again"]
    assert [s["name"] for s in listed(client, owner)] == ["mac"]


# ---- each of the link's transactions holds the write lock from its first statement -----------------------------------

@pytest.fixture
def watched(monkeypatch):
    """connector_link's own connections, watched: at the first statement of each after its BEGIN, a second connection
    tries to begin a write. `seen`: for each connection that went on to write, (its first statement, whether the
    second writer was locked out)."""
    seen = []
    real = store.connect

    class Db:
        def __init__(self, db):
            self._db, self.first, self.wrote = db, None, False

        def execute(self, sql, *args):
            word = sql.lstrip().split(None, 1)[0].upper()
            if word != "BEGIN" and self.first is None:
                other = sqlite3.connect(config.db_path(), timeout=0)
                try:
                    other.execute("BEGIN IMMEDIATE")
                    other.rollback()
                    locked = False
                except sqlite3.OperationalError:
                    locked = True
                finally:
                    other.close()
                self.first = (" ".join(sql.split())[:50], locked)
            if word in ("INSERT", "UPDATE", "DELETE"):
                self.wrote = True
            return self._db.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._db, name)

    @contextmanager
    def connect(path=None):
        with real(path) as db:
            db = Db(db)
            yield db
            if db.wrote:
                seen.append(db.first)

    class Store:
        def __getattr__(self, name):
            return getattr(store, name)
    proxy = Store()
    proxy.connect = connect
    monkeypatch.setattr(connector_link, "store", proxy)
    return seen


def test_each_of_the_link_s_transactions_holds_the_write_lock_from_its_first_statement(client, owner, app, watched):
    """What a call reads of its app, its session and its limits is one transaction with what it writes: from its
    first statement no other connection can begin a write, so nothing (encryption turned on, a revoke, a removal)
    comes between a post's check and its INSERT, or between the page's look at a session and its ending."""
    def held(what):
        assert watched, f"{what} wrote nothing through connector_link"
        assert all(locked for _, locked in watched), (what, watched)
        watched.clear()
    ok(app, "link_session", session="one")
    held("link_session")
    asked = ok(app, "link_post", session="one", kind="question", text="q")
    held("link_post")
    ok(app, "link_checkpoint", session="one", document={"doing": "d"}, expected_version=0)
    held("link_checkpoint")
    [shown] = listed(client, owner)
    assert watched == []                                # a read alone takes no lock and writes nothing
    assert client.post(f"/api/connector/links/{shown['id']}/stop", headers=owner).status_code == 200
    held("stop")
    assert client.post(f"/api/connector/links/{shown['id']}/questions/{asked['seq']}/close",
                       headers=owner).status_code == 200
    held("close_question")
    ok(app, "link_session", session="mac")
    watched.clear()
    paired(client, owner, name="mac")
    assert [s["name"] for s in listed(client, owner)] == ["one"]
    held("the read's ending of a session whose name a keyed link took")
    assert client.delete(f"/api/connector/links/{shown['id']}", headers=owner).status_code == 200
    held("remove")
    assert let_link(client, owner, app["id"], on=False).status_code == 200
    held("linking turned off")
    assert let_link(client, owner, app["id"]).status_code == 200
    held("linking turned on")
    ok(app, "link_session", session="idle")
    watched.clear()
    assert connector_link.prune(int(time.time()) + connector_link.IDLE_ENDS + 1)["ended"] >= 1
    held("the timer's clearing")


# ---- guards a narrower version of which no case noticed (the last cold read's G7 to G11) -----------------------------

def test_the_owner_s_routes_for_these_sessions_answer_404_while_the_deployment_s_connector_is_off(client, owner, app,
                                                                                               monkeypatch):
    """Each of the seven routes of the page's part, not only its list: while the deployment's connector is off none
    reads, stops, closes, removes or switches anything, and the summary still answers."""
    ok(app, "link_session", session="one")
    asked = ok(app, "link_post", session="one", kind="question", text="q")
    [shown] = listed(client, owner)
    before = rows()
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR")
    base = f"/api/connector/links/{shown['id']}"
    for method, path, body in (("GET", "/api/connector/links", None), ("GET", f"{base}/posts", None),
                               ("POST", f"{base}/stop", None), ("DELETE", f"{base}/stop", None),
                               ("POST", f"{base}/questions/{asked['seq']}/close", None),
                               ("PUT", f"/api/connector/grants/{app['id']}/links", {"on": False}),
                               ("DELETE", base, None)):
        assert client.request(method, path, headers=owner, json=body).status_code == 404, (method, path)
    assert rows() == before and len(rows("ade_connector_link_events")) == 1
    assert client.get("/api/ade/summary", headers=owner).status_code == 200


@pytest.mark.parametrize("before", [2 ** 63, 2 ** 64, 10 ** 30, -5, 0, 1, 2 ** 62])
def test_a_session_s_posts_are_read_from_any_number_the_page_gives(client, owner, app, before):
    """`before` is held to what SQLite stores before it reaches the statement: a number past it is no error."""
    ok(app, "link_session", session="one")
    seq = ok(app, "link_post", session="one", kind="step", text="s")["seq"]
    [shown] = listed(client, owner)
    r = client.get(f"/api/connector/links/{shown['id']}/posts?before={before}", headers=owner)
    assert r.status_code == 200, r.text
    assert [e["seq"] for e in r.json()["posts"]] == ([seq] if before > seq else [])


@pytest.mark.parametrize("code", [0x7F, 0x80, 0x85, 0x9B, 0x9F])
def test_a_checkpoint_holds_no_delete_and_no_c1_control_character(client, owner, app, code):
    ok(app, "link_session", session="one")
    for document in ({"doing": "a" + chr(code) + "b"}, {"a" + chr(code): 1}):
        said = refused(app, "link_checkpoint", session="one", document=document, expected_version=0)
        assert said == f"The checkpoint holds a control character (U+{code:04X}).", said
    assert rows()[0]["checkpoint"] is None


# a request may be many times a checkpoint, and two of the shapes take the square of a text's length: what is read for
# a shape is bounded as a checkpoint is, before the document's own size is
LONG = 60_000
LONG_DOCUMENTS = {
    "one long text": {"doing": "a" * LONG},
    "one long name": {"a" * LONG: "x"},
    "a long text in a list": {"doing": ["short", "b" * LONG]},
    "a long name below the top": {"doing": {"c" * LONG: 1}},
    "many texts, each one a checkpoint could hold": {"doing": ["d" * 3000] * 20},
    "many names, each one a checkpoint could hold": {("e%02d" % i) + "e" * 2990: i for i in range(20)},
}


@pytest.mark.parametrize("linking", ["on", "off"])
@pytest.mark.parametrize("what", sorted(LONG_DOCUMENTS))
def test_no_more_of_a_checkpoint_than_a_checkpoint_may_hold_is_read_for_a_secret_s_shape(client, owner, monkeypatch,
                                                                                        linking, what):
    t = connected(client, owner)
    if linking == "on":
        assert let_link(client, owner, t["grant"]).status_code == 200
    given = []
    real = proposals._no_secret

    def spy(value, kind):
        given.append(len(value))
        return real(value, kind)
    monkeypatch.setattr(proposals, "_no_secret", spy)
    result = call(grant_of(t), "link_checkpoint", session="one", document=LONG_DOCUMENTS[what], expected_version=0)
    assert result["isError"] is True
    assert result["structuredContent"]["said"] == f"A checkpoint is at most {link.MAX_CHECKPOINT} bytes."
    # the names and texts given to the shapes before the refusal are together no longer than a checkpoint may be
    assert sum(given) <= link.MAX_CHECKPOINT, (max(given), sum(given))
