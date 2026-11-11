"""A cloud session's request approved as the replacement of the live cloud link of its name (ade/link_asks.py), through
the app: the page is told which link a waiting request may replace; the approval names that link, and takes the
confirmation and the hold any approval takes; the hold and its notice say what it replaces and that the old link stops
then; when the hold ends the old link is revoked as a revoke from the page revokes it, everything a revoke ends ending
with it, and the new link is made in the same transaction; each race at the hold's end; Cancel; and the refusals: a
local link, a local request, a key a live link has, a link that is not the live one of that name, another account's."""

import pytest
from fastapi.testclient import TestClient

from conftest import signed_up
from proofpane_open import passkeys, store
from proofpane_open.ade import holds, link, link_asks, moves
from test_ade_link import Session, b64u, confirmed, paired, post, raw, revoke, row, ticket
from test_ade_link_asks import OTHER, address, ask, asks, at, fingerprint, links, owner, phone, poll  # noqa: F401
from test_ade_link_commands import command, device, take  # noqa: F401  (device: a fixture)
from test_ade_link_moves import (BUNDLE, SESSION, TRANSCRIPT, kept, move_row, no_wait, put, say_done,  # noqa: F401
                                 signed_move, workspace)


def approve(client, owner, ask_id, replace=None, confirmation=True):
    # made for this request and the link it replaces, as the page asks for it
    made_for = f"link-approve:{ask_id}" + (f":{replace}" if replace else "")
    body = {"confirmation": confirmed(client, owner, made_for)} if confirmation is True else (
        {"confirmation": confirmation} if confirmation else {})
    if replace is not None:
        body["replace"] = replace
    return client.post(f"/api/ade/link/asks/{ask_id}/approve", headers=owner, json=body)


def ask_row(ask_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_link_asks WHERE id = ?", (ask_id,)).fetchone())


def timeline(client, owner, link_id):
    r = client.get(f"/api/ade/link/timeline/{link_id}", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()["events"]


def replaced_notes():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_link_events WHERE client_id LIKE 'replaced:%'")]


def expired_cloud(client, owner, at, name="cloud"):
    """The cloud link of a session that has since expired: linked, then heard from ten minutes later."""
    old = paired(client, owner, name, kind="cloud")
    old.linked_at = at[0]
    at[0] += 600
    assert old.call("POST", "/heartbeat", {}).status_code == 200
    old.seen_at = at[0]
    at[0] += 3000
    return old


def on_a_live_link(client, owner, s, name, now):
    """The asking key of `s` put on a live link named `name`: its id. No route does that while the
    key's request is open, since a ticket's pairing of such a key is refused (409), which this asserts first; the
    checks an approval and the end of its hold make of the key stand all the same, so the link is made by hand."""
    r = Session(client, key=s.key).pair(ticket(client, owner), name=name, kind="cloud")
    assert r.status_code == 409 and r.json()["detail"] == "This key waits to be linked already: wait for that to end."
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    with store.connect() as db:
        return link.make_link(db, user_id, name, "cloud", b64u(raw(s.key)), None, now)


def held(client, owner, at, old, **kw):
    """A cloud session asking under the old link's name, approved as its replacement: (the session, the request, its
    hold)."""
    s, _ = ask(client, address(client, owner), name=old.name, **kw)
    (seen,) = [a for a in asks(client, owner) if a["fingerprint"] == fingerprint(s)]
    got = approve(client, owner, seen["id"], replace=old.link_id)
    assert got.status_code == 200, got.text
    return s, seen, got.json()["hold"]


# ---- what the page is told ------------------------------------------------------------------------------------------

def test_the_page_is_told_the_live_cloud_link_a_waiting_cloud_request_may_replace(client, owner, at):
    old = expired_cloud(client, owner, at)
    ask(client, address(client, owner))
    ask(client, address(client, owner), name="elsewhere")
    shown = {a["name"]: a for a in asks(client, owner)}
    assert shown["cloud"]["replaces"] == {"id": old.link_id, "name": "cloud", "created_at": old.linked_at,
                                          "last_seen_at": old.seen_at, "fingerprint": fingerprint(old),
                                          "revoked_at": None}
    assert shown["elsewhere"]["replaces"] is None             # no live link has its name


# ---- approving, the hold, the end of the hold -----------------------------------------------------------------------

def test_a_cloud_request_approved_as_a_replacement_takes_the_name_when_the_hold_ends_and_the_old_key_is_refused(
        client, owner, at):
    old = expired_cloud(client, owner, at)
    s, _ = ask(client, address(client, owner))
    (seen,) = asks(client, owner)
    c = confirmed(client, owner, f"link-approve:{seen['id']}:{old.link_id}")
    plain = approve(client, owner, seen["id"], confirmation=c)       # as it is, the name is taken, as before
    assert plain.status_code == 409 and "already has a live link named cloud" in plain.json()["detail"]
    # made for approving without a replacement, a confirmation approves no replacement
    r = approve(client, owner, seen["id"], replace=old.link_id,
                confirmation=confirmed(client, owner, f"link-approve:{seen['id']}"))
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER
    got = approve(client, owner, seen["id"], replace=old.link_id, confirmation=c)
    assert got.status_code == 200, got.text
    hold = got.json()["hold"]
    assert (hold["kind"], hold["takes_effect_at"]) == (link_asks.HOLD, at[0] + link_asks.HOLD_DEFAULT)
    (shown,) = asks(client, owner)
    assert shown["state"] == "held" and shown["replaces"]["id"] == old.link_id
    assert shown["replaces"]["revoked_at"] is None
    assert poll(s).json()["status"] == "approved"
    at[0] = hold["takes_effect_at"] - 1
    assert poll(s).json()["status"] == "approved" and [x["id"] for x in links()] == [old.link_id]
    assert old.call("POST", "/heartbeat", {}).status_code == 200            # the old link works until then
    at[0] += 1
    done = poll(s).json()
    (made,) = links()
    assert done["status"] == "linked" and done["link_id"] == made["id"] != old.link_id
    assert (made["name"], made["kind"], made["public_key"]) == ("cloud", "cloud", b64u(raw(s.key)))
    assert row(old.link_id)["revoked_at"] == hold["takes_effect_at"]
    assert (ask_row(seen["id"])["state"], ask_row(seen["id"])["replaces"]) == ("linked", old.link_id)
    at[0] += 61
    refused = old.call("POST", "/heartbeat", {})
    assert refused.status_code == 401 and "No live link has this key" in refused.json()["detail"]
    assert s.call("POST", "/heartbeat", {}).status_code == 200
    assert [x["name"] for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"]] == ["cloud"]


@pytest.mark.parametrize("asked, held", [("Cloud", "cloud"), ("my cloud", "my  cloud")])
def test_a_replacement_keeps_the_name_exactly_and_one_spelt_otherwise_is_refused(client, owner, at, asked, held):
    """Messages are addressed by exact name, so a replacement keeps the link's name as it is: a request whose name
    reads as a live cloud link's (link.name_key) but differs in case or spacing may not replace it, and approving it as
    it is stays refused, as any request under a live link's name; asked under the link's own name, it may."""
    old = expired_cloud(client, owner, at)
    if held != "cloud":
        with store.connect() as db:                     # a name kept from before two spaces together were refused
            db.execute("UPDATE ade_links SET name = ? WHERE id = ?", (held, old.link_id))
    ask(client, address(client, owner), name=asked)
    (seen,) = asks(client, owner)
    assert seen["replaces"] is None
    c = confirmed(client, owner)
    r = approve(client, owner, seen["id"], replace=old.link_id, confirmation=c)
    assert r.status_code == 409 and r.json()["detail"] == (
        f"The live link is named “{held}”, which differs from this request's name only in case or spacing" + (
            ": to replace it, the session asks under the link's own name." if held == "cloud" else
            ", and a name spaced so cannot be asked for: revoke the link first, or refuse the request."))
    r = approve(client, owner, seen["id"], confirmation=c)
    assert r.status_code == 409 and r.json()["detail"] == f"The account already has a live link named {held}."
    assert client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": c}).status_code == 200
    assert asks(client, owner)[0]["state"] == "pending" and row(old.link_id)["revoked_at"] is None
    if held == "cloud":
        ask(client, address(client, owner), name=held)             # under the link's own name
        (exact,) = [a for a in asks(client, owner) if a["name"] == held]
        assert exact["replaces"]["id"] == old.link_id


def test_a_local_link_of_a_name_that_reads_as_the_request_s_is_not_replaced(client, owner, at):
    """A local link is never replaced, whatever the spelling, and approving under its name is refused, naming it as it
    is."""
    paired(client, owner, "Mac", kind="local")
    ask(client, address(client, owner), name="MAC", kind="local")
    (mac,) = [a for a in asks(client, owner) if a["name"] == "MAC"]
    assert mac["replaces"] is None
    r = approve(client, owner, mac["id"])
    assert r.status_code == 409 and r.json()["detail"] == "The account already has a live link named Mac."


def test_the_revoke_is_kept_in_the_account_s_events_as_part_of_the_replacement(client, owner, at):
    old = expired_cloud(client, owner, at)
    s, seen, hold = held(client, owner, at, old)
    at[0] = hold["takes_effect_at"]
    new = poll(s).json()["link_id"]
    (note,) = [e for e in timeline(client, owner, new) if e["from"] == "owner"]
    assert (note["kind"], note["to"], note["to_id"], note["needs_turn"]) == ("note", "cloud", new, False)
    assert note["text"] == (f"Linked in place of the link named cloud (linked {holds.utc(old.linked_at)}, key "
                            f"{fingerprint(old)}): the owner approved the replacement, and that link was revoked as "
                            "this one was made.")
    # the new session reads it, as it reads anything after its cursor; and it was posted as the old link went
    assert [e["seq"] for e in s.call("GET", "/feed").json()["events"]] == [note["seq"]]
    assert replaced_notes()[0]["received_at"] == row(old.link_id)["revoked_at"] == hold["takes_effect_at"]


def test_a_replacement_takes_the_confirmation_any_approval_takes_and_a_rule_on_it_covers_both(client, owner, at,
                                                                                            monkeypatch):
    old = expired_cloud(client, owner, at)
    ask(client, address(client, owner))
    (seen,) = asks(client, owner)
    r = approve(client, owner, seen["id"], replace=old.link_id, confirmation=None)
    assert r.status_code == 403 and r.json()["detail"] == link.LINK_NEEDS_CONFIRM
    assert asks(client, owner)[0]["state"] == "pending"

    def stricter(db, user_id, confirmation, now, subject):
        raise link.LinkError(403, "Only a hardware key confirms that.")

    monkeypatch.setattr(link, "_use_confirmation", stricter)
    r = approve(client, owner, seen["id"], replace=old.link_id)
    assert r.status_code == 403 and r.json()["detail"] == "Only a hardware key confirms that."
    assert asks(client, owner)[0]["state"] == "pending" and links()[0]["id"] == old.link_id


@pytest.mark.parametrize("case", ["a local link", "a local request", "a key a live link has", "another cloud link",
                                  "no link", "another account's", "another account's, none of its own", "no live link"])
def test_a_replace_is_refused_unless_it_names_the_live_cloud_link_of_the_request_s_name(client, owner, at, case):
    """Refused, the request waits as it was and the confirmation is left for another use; the page is told it may
    replace nothing, or the link it may replace, which is not the one named."""
    words, may = None, None
    if case == "a local link":
        local = paired(client, owner, "cloud", kind="local")
        ask(client, address(client, owner))
        replace, words = local.link_id, "The live link named cloud is a local one, which is never replaced"
    elif case == "a local request":
        cloud = paired(client, owner, "mac", kind="cloud")
        ask(client, address(client, owner), name="mac", kind="local")
        replace, words = cloud.link_id, "Only a cloud session's request replaces a link, and this one is local"
    elif case == "a key a live link has":
        old = paired(client, owner, "cloud", kind="cloud")
        s, _ = ask(client, address(client, owner))
        on_a_live_link(client, owner, s, "elsewhere", at[0])
        replace, words = old.link_id, "This key is already on another live link."
    elif case in ("another cloud link", "no link"):
        old = paired(client, owner, "cloud", kind="cloud")
        elsewhere = paired(client, owner, "elsewhere", kind="cloud")
        ask(client, address(client, owner))
        replace = elsewhere.link_id if case == "another cloud link" else "0" * 32
        words, may = "The live link named cloud is not the one this approval replaces", old.link_id
    elif case == "another account's":
        theirs = paired(client, signed_up(client, OTHER), "cloud", kind="cloud")
        mine = paired(client, owner, "cloud", kind="cloud")
        ask(client, address(client, owner))
        replace, words, may = theirs.link_id, "The live link named cloud is not the one", mine.link_id
    elif case == "another account's, none of its own":
        theirs = paired(client, signed_up(client, OTHER), "cloud", kind="cloud")
        ask(client, address(client, owner))
        replace, words = theirs.link_id, "No live link is named cloud"
    else:
        ask(client, address(client, owner))
        replace, words = "0" * 32, "No live link is named cloud, so there is none to replace"
    (seen,) = asks(client, owner)
    assert (seen["replaces"] or {}).get("id") == may
    c = confirmed(client, owner)
    r = approve(client, owner, seen["id"], replace=replace, confirmation=c)
    assert r.status_code == 409 and words in r.json()["detail"], r.text
    assert asks(client, owner)[0]["state"] == "pending"
    assert client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": c}).status_code == 200


def test_the_hold_says_what_it_replaces_and_its_notice_only_that_a_link_waits_to_replace_another(client, owner, phone,
                                                                                                at):
    device_, service = phone
    old = expired_cloud(client, owner, at)
    service.posts.clear()                   # the old link's pairing told its own notice: not this test's
    from proofpane_open.main import app
    s, _ = ask(TestClient(app, client=("203.0.113.9", 4000)), address(client, owner))
    (seen,) = asks(client, owner)
    got = approve(client, owner, seen["id"], replace=old.link_id).json()
    hold = got["hold"]
    assert hold["summary"] == "Replacing the link “cloud” (cloud) was approved"
    # the page shows the link it replaces, its key, the new key and where it asked from
    (shown,) = asks(client, owner)
    assert (shown["replaces"]["id"], shown["replaces"]["fingerprint"]) == (old.link_id, fingerprint(old))
    assert (shown["fingerprint"], shown["asked_from"]) == (fingerprint(s), "203.0.113.9")
    # the notice, in fixed words: a link waits to replace another, and when; no name, key or address
    from proofpane_open.ade import push
    (told,) = [device_.decrypt(b) for _, _, b in service.posts]
    assert got["told"] == 1 and told == {
        "title": "A link waits to replace another",
        "body": f"Takes effect at {holds.utc(hold['takes_effect_at'])} unless you cancel it.",
        "tag": push.words("held-link", key="hold:" + hold["id"], at=0).tag, "url": "/app/#channel"}


def holding(client, owner, device, env, old):
    """What the old link holds that a revoke ends: its History list, a conversation and a mirrored note, a move sent
    whole (its hold of no seconds, no_wait; the workspace not running, so it waits uploaded with its files kept), and a
    command queued to it. (the move's seq, the command's seq)."""
    r = old.call("PUT", "/history", {"sessions": [{"id": SESSION, "title": "Work on it", "registered": True}],
                                     "conversations": True})
    assert r.status_code == 200, r.text
    r = old.call("PUT", "/history/talk", {"session": SESSION, "messages": [{"who": "owner", "text": "go on"}]})
    assert r.status_code == 200, r.text
    assert post(old, "note", "said there", mirror="session", session=SESSION).json()["session"] == SESSION
    seq, _ = signed_move(client, owner, device, old)
    assert take(old, seq).status_code == 200
    assert put(old, seq, "transcript", TRANSCRIPT).status_code == 200
    assert put(old, seq, "bundle", BUNDLE).status_code == 200
    assert say_done(old, seq).json()["state"] == "uploaded" and kept(env, seq) != {}
    queued, _ = command(client, owner, device, old.link_id)
    return seq, queued


def test_at_the_hold_s_end_the_old_link_ends_as_a_revoke_ends_one_and_the_new_link_is_made(client, owner, device,
                                                                                         workspace, env, at, no_wait):
    workspace.running = False
    old = expired_cloud(client, owner, at)
    seq, queued = holding(client, owner, device, env, old)
    s, seen, hold = held(client, owner, at, old)
    at[0] = hold["takes_effect_at"]
    new = poll(s).json()["link_id"]
    assert row(old.link_id)["revoked_at"] == hold["takes_effect_at"]
    with store.connect() as db:
        for table in ("ade_link_history", "ade_link_history_talks"):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE link_id = ?", (old.link_id,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE link_id = ? AND session_id IS NOT NULL",
                          (old.link_id,)).fetchone()[0] == 0
    assert (move_row(seq)["state"], move_row(seq)["reason"]) == ("failed", moves.REVOKED)
    assert kept(env, seq) == {}                                        # deleted once the transaction committed
    assert take(old, queued).status_code == 401                         # taken by no one: not the old key
    assert queued not in [e["seq"] for e in s.call("GET", "/feed", query={"after": 0}).json()["events"]]
    assert queued not in [e["seq"] for e in timeline(client, owner, new)]   # nor the link of its name now
    assert [x["id"] for x in links()] == [new]


def test_the_old_link_and_the_new_one_change_together_or_not_at_all(client, owner, device, workspace, env, at,
                                                                   monkeypatch, no_wait):
    """The revoke and the making of the new link are one transaction: when making the new link breaks, the old one
    stands with all it holds, its moves' files too; and the hold ends failed (holds.BROKE), the request with it, which
    is never tried again and again: its session asks anew."""
    workspace.running = False
    old = expired_cloud(client, owner, at)
    seq, queued = holding(client, owner, device, env, old)
    s, seen, hold = held(client, owner, at, old)
    real = link.make_link

    def broken(*args, **kwargs):
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(link, "make_link", broken)
    at[0] = hold["takes_effect_at"]
    answer = poll(s).json()
    assert (answer["status"], answer["detail"]) == ("failed", holds.BROKE)
    assert row(old.link_id)["revoked_at"] is None and [x["id"] for x in links()] == [old.link_id]
    assert move_row(seq)["state"] == "uploaded" and kept(env, seq) != {}
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == holds.BROKE
        assert db.execute("SELECT COUNT(*) FROM ade_link_history WHERE link_id = ?", (old.link_id,)).fetchone()[0] == 1
    monkeypatch.setattr(link, "make_link", real)
    assert poll(s).json()["status"] == "failed" and row(old.link_id)["revoked_at"] is None
    assert move_row(seq)["state"] == "uploaded" and kept(env, seq) != {}


# ---- each race at the hold's end ------------------------------------------------------------------------------------

@pytest.mark.parametrize("meanwhile", ["revoked", "renamed", "taken", "local now", "key taken", "recased"])
def test_what_changed_while_the_hold_ran_is_checked_again_when_it_ends(client, owner, at, meanwhile):
    """The old link is revoked only while it is still live, still has the name exactly and is still a cloud link;
    revoked meanwhile (or no longer of that name), the new link is made if the name is free; another link on the name,
    the old one under it spelt otherwise, or a link on the new key, leaves the request failed with why, and nothing
    revoked."""
    old = expired_cloud(client, owner, at)
    s, seen, hold = held(client, owner, at, old)
    other = None
    if meanwhile in ("revoked", "taken"):
        at[0] += 5
        assert revoke(client, owner, old.link_id).status_code == 200
        revoked_at = at[0]
        if meanwhile == "taken":
            other = paired(client, owner, "cloud", kind="cloud")
    elif meanwhile in ("renamed", "local now", "recased"):
        with store.connect() as db:
            db.execute({"renamed": "UPDATE ade_links SET name = 'cloud-before' WHERE id = ?",
                        "local now": "UPDATE ade_links SET kind = 'local' WHERE id = ?",
                        "recased": "UPDATE ade_links SET name = 'Cloud' WHERE id = ?"}[meanwhile], (old.link_id,))
    else:
        other = Session(client, key=s.key)              # the asking key, on a live link under another name
        other.link_id = on_a_live_link(client, owner, s, "elsewhere", at[0])
    at[0] = hold["takes_effect_at"]
    got = poll(s).json()
    if meanwhile in ("revoked", "renamed"):
        assert got["status"] == "linked"
        assert row(got["link_id"])["name"] == "cloud" and replaced_notes() == []
        if meanwhile == "revoked":
            assert row(old.link_id)["revoked_at"] == revoked_at           # the owner's own revoke, untouched
        else:
            assert row(old.link_id)["revoked_at"] is None and row(old.link_id)["name"] == "cloud-before"
        return
    assert got["status"] == "failed"
    assert got["detail"] == ("This key is already on another live link." if meanwhile == "key taken" else
                             "The account already has a live link named Cloud." if meanwhile == "recased" else
                             "The account already has a live link named cloud.")
    (shown,) = asks(client, owner)
    assert shown["state"] == "failed" and shown["replaces"]["id"] == old.link_id and shown["failed"] == got["detail"]
    if meanwhile != "taken":
        assert row(old.link_id)["revoked_at"] is None                   # left as it was
    if other is not None:
        assert row(other.link_id)["revoked_at"] is None
    assert replaced_notes() == []


def test_cancelling_a_replacement_revokes_nothing_and_makes_nothing(client, owner, at):
    old = expired_cloud(client, owner, at)
    s, seen, hold = held(client, owner, at, old)
    at[0] = hold["takes_effect_at"] - 1
    r = client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner)
    assert r.status_code == 200 and r.json()["cancelled_at"] == at[0]
    assert poll(s).json()["status"] == "cancelled" and asks(client, owner) == []
    at[0] += 3600
    holds.settle(now=at[0])
    assert poll(s).json()["status"] == "cancelled"
    assert [x["id"] for x in links()] == [old.link_id] and row(old.link_id)["revoked_at"] is None
    assert old.call("POST", "/heartbeat", {}).status_code == 200 and replaced_notes() == []
