"""A session that waits on the owner tells them. An open
`waiting` post to the owner or to all that refers to no command is told to them once, in the fixed words of a kind
of its own, never its words or its session's name, until that link posts again or they close it on the page; a
`waiting` that refers to a command (a take's renewal, "working on it") is told as before, never pushed, and so is a
note."""

import json
import re
import subprocess
import time

import pytest

from proofpane_open import store
from proofpane_open.ade import link, push
from test_ade_link import owner, paired, post, revoke  # noqa: F401  the fixture
from test_ade_link_commands import command, take
from test_ade_link_page import NODE, NOTICES, read
from test_passkeys import Authenticator, add

WAITS = ("A session waits for you", "Open Linked sessions.")


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    r = add(client, owner, d)
    assert r.status_code == 200, r.text
    return d


def account(client, owner):
    return client.get("/api/auth/me", headers=owner).json()["id"]


def told(client, owner):
    """The session channel's items for the owner's notifier: {key: kind}."""
    return dict(link.alerts(account(client, owner)))


def waiting(s, text="Waiting for your word on the release.", **kw):
    r = post(s, "waiting", text, **kw)
    assert r.status_code == 200, r.text
    return r.json()["seq"]


def test_a_waiting_to_the_owner_that_refers_to_no_command_is_told_in_its_own_fixed_words(client, owner):
    mac = paired(client, owner, name="payroll-mac")

    seq = waiting(mac)

    assert told(client, owner).get(f"link-waiting:{seq}") == "link-waiting"
    words = [n for k, n in push.items_for(account(client, owner), [], int(time.time()))
             if k == f"link-waiting:{seq}"]
    assert [(n.title, n.body, n.url) for n in words] == [WAITS + ("/app/#channel",)]
    assert "payroll" not in json.dumps([n.title + n.body for n in words]).lower()


def test_a_waiting_to_all_is_told_and_one_to_another_session_is_not(client, owner):
    mac = paired(client, owner, name="mac")
    other = paired(client, owner, name="other")
    paired(client, owner, name="cloud")

    to_all = waiting(mac, to="all")
    to_cloud = waiting(other, to="cloud")

    got = told(client, owner)
    assert (got.get(f"link-waiting:{to_all}"), f"link-waiting:{to_cloud}" in got) == ("link-waiting", False), got


def test_a_waiting_that_refers_to_a_command_it_took_is_not_pushed(client, owner, device):
    """A take's renewal ("working on it") says the session is on it, not that it waits on the owner."""
    mac = paired(client, owner, name="mac")
    seq, _ = command(client, owner, device, mac.link_id)
    assert take(mac, seq).status_code == 200

    renewal = waiting(mac, link.WORKING, ref=seq)

    assert not [k for k in told(client, owner) if k.startswith("link-waiting:")], renewal


def test_a_note_to_the_owner_is_not_pushed(client, owner):
    mac = paired(client, owner, name="mac")

    r = post(mac, "note", "Waiting for your word on the release.")

    assert r.status_code == 200, r.text
    assert not [k for k in told(client, owner) if k.startswith("link-waiting:")]


def test_the_links_next_post_ends_the_item_and_another_links_does_not(client, owner):
    mac = paired(client, owner, name="mac")
    other = paired(client, owner, name="other")
    seq = waiting(mac)

    assert post(other, "step", "something else").status_code == 200
    assert f"link-waiting:{seq}" in told(client, owner)
    assert post(mac, "step", "back at it").status_code == 200

    assert f"link-waiting:{seq}" not in told(client, owner)


def test_a_waiting_of_a_link_since_revoked_is_not_told(client, owner):
    """A control: a revoked link can be answered no more, so what it said it waits on is told to no device."""
    mac = paired(client, owner, name="mac")
    seq = waiting(mac)

    assert revoke(client, owner, mac.link_id).status_code == 200

    assert f"link-waiting:{seq}" not in told(client, owner)


def test_the_owner_closes_it_on_the_page_and_its_session_is_told_in_the_fixed_words(client, owner):
    mac = paired(client, owner, name="mac")
    seq = waiting(mac)

    r = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)

    assert r.status_code == 200, r.text
    assert f"link-waiting:{seq}" not in told(client, owner)
    again = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert again.json() == r.json()
    note = mac.call("GET", "/feed", query={"after": 0}).json()["events"]
    assert [e["text"] for e in note if e.get("ref") == seq] == [link.CLOSED_ON_PAGE]


def test_the_page_lists_each_open_waiting_and_counts_it_among_what_waits(client, owner):
    mac = paired(client, owner, name="mac")
    seq = waiting(mac)

    view = client.get("/api/ade/link/sessions", headers=owner).json()
    summary = client.get("/api/ade/summary", headers=owner).json()

    assert [(w["seq"], w["link_id"]) for w in view.get("waitings", [])] == [(seq, mac.link_id)]
    assert summary["links"]["alerts"] == 1, summary["links"]


@pytest.mark.skipif(NODE is None, reason="node is needed to run the page's script")
def test_the_old_pages_notifier_tells_the_same_item_in_the_same_words(tmp_path):
    """The old page's own notifications (web/app.js), as tests/test_ade_link_page.py runs them: an open waiting is
    told in the server's words for it, never its words or its session's name."""
    script = read("web", "app.js")
    start = script.index('const TITLE = "Proofpane Community";')
    words = script[start:script.index("function notifyState()", start)]
    items = re.search(r"  ade\.linkItems = .*?\)\)\);\n", script, re.S).group(0)
    probe = tmp_path / "notices.js"
    probe.write_text(NOTICES + words + items + "attention();\n"
                     "process.stdout.write(JSON.stringify({ title: document.title, shown }));\n")
    data = {"pending": [], "alerts": [], "links": [{"id": "L1", "name": "payroll-mac", "silent": False}],
            "questions": [],
            "waitings": [{"seq": 9, "to": "owner", "from": "payroll-mac", "link_id": "L1",
                          "text": "Waiting on Initech's memo"}]}
    run = subprocess.run([NODE, str(probe), json.dumps(data), "shows"], capture_output=True, text=True, check=True)
    got = json.loads(run.stdout)

    assert [(n["title"], n["body"]) for n in got["shown"]] == [WAITS]
    shown = json.dumps(got["shown"]).lower()
    assert "payroll" not in shown and "initech" not in shown and ":9" not in shown


def test_the_clients_post_says_to_ask_the_owner_with_a_question(capsys):
    """The client's post usage: a waiting to the owner says only that the session is blocked on them; anything they
    are asked is a question to them."""
    import ade_link
    with pytest.raises(SystemExit):
        ade_link.main(["post", "--help"])
    out = capsys.readouterr().out

    assert "question --to owner" in " ".join(out.split()), out


# ---- what ends a waiting's item, how it is read, who may close it, and the help -------------------

def test_what_the_session_does_not_post_itself_leaves_it_told(client, owner, device):
    """The link's mirrored notes (the turn's reply its hook mirrors at the turn's end) and a take's renewal
    say nothing new of its waiting on the owner: the item stays told; a post of its own ends it."""
    mac = paired(client, owner, name="mac")
    cmd, _ = command(client, owner, device, mac.link_id)
    assert take(mac, cmd).status_code == 200
    seq = waiting(mac)

    assert post(mac, "note", "I posted a waiting to the owner and stopped.", mirror="session").status_code == 200
    assert waiting(mac, link.WORKING, ref=cmd)

    assert f"link-waiting:{seq}" in told(client, owner)


def test_the_newest_post_is_read_within_its_account(client, owner):
    """The account is the boundary, not the link's id alone: a row of another account carrying the link's
    id ends nothing."""
    mac = paired(client, owner, name="mac")
    seq = waiting(mac)
    with store.connect() as db:          # a state the routes never make, as tests/test_ade_link.py guards the cards
        db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, received_at) "
                   "VALUES ('someone-else', ?, 'x1', 'step', 'all', 'not yours', 1)", (mac.link_id,))

    assert f"link-waiting:{seq}" in told(client, owner)


def test_the_open_waitings_are_read_through_an_index_of_each_links_own_posts():
    """The newest-post test walks no link's history: it is read through an index of each link's own posts
    by account and seq."""
    with store.connect() as db:
        plan = " ".join(r[-1] for r in db.execute(
            f"EXPLAIN QUERY PLAN SELECT e.seq FROM ade_link_events e WHERE e.user_id = ? AND {link.WAITING_OPEN}",
            ("u",)).fetchall())

    assert "ade_link_events_own" in plan, plan


def test_the_owners_close_of_a_waiting_wakes_no_session(client, owner):
    """A waiting asked nothing, so its Close is no turn for its session: the note is in its feed, waking
    nothing."""
    mac = paired(client, owner, name="mac")
    seq = waiting(mac)

    r = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)

    assert r.status_code == 200, r.text
    with store.connect() as db:
        woke = db.execute("SELECT needs_turn FROM ade_link_events WHERE seq = ?", (r.json()["seq"],)).fetchone()[0]
    assert woke == 0


def test_a_waiting_ended_or_to_another_session_is_not_the_owners_to_close(client, owner):
    """An ended waiting, and one to another session, are not open waitings of the owner's: 409."""
    mac = paired(client, owner, name="mac")
    paired(client, owner, name="cloud")
    ended = waiting(mac)
    assert post(mac, "step", "back at it").status_code == 200
    theirs = waiting(mac, to="cloud")

    first = client.post(f"/api/ade/link/questions/{ended}/close", headers=owner)
    second = client.post(f"/api/ade/link/questions/{theirs}/close", headers=owner)

    assert (first.status_code, second.status_code) == (409, 409), (first.text, second.text)


def test_a_revoked_links_waiting_is_not_closed(client, owner):
    """A revoked link's waiting is not the owner's to close: a note to its old name could wake a new link
    given that name."""
    mac = paired(client, owner, name="mac")
    seq = waiting(mac)
    assert revoke(client, owner, mac.link_id).status_code == 200

    r = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)

    assert r.status_code == 409, r.text


def test_the_clients_post_help_says_a_waiting_says_only_the_session_is_blocked(capsys):
    """The help says what a waiting is, and the file's own usage says to ask with a question."""
    import ade_link
    with pytest.raises(SystemExit):
        ade_link.main(["post", "--help"])
    out = " ".join(capsys.readouterr().out.split())

    assert "says only that this session is blocked on them" in out, out
    assert "question --to owner" in " ".join((ade_link.__doc__ or "").split())

