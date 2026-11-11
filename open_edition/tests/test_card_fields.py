"""A link's card shows what its checkpoint says, and a card with no
checkpoint shows the newest step its session posted, or its live view's newest line, marked as that. So the owner's view
gives each link its newest step and its live view's newest round, its own as its newest event is: the timeline's shape
of each, or none."""

import contextlib
import time

from proofpane_open import store
from test_ade_link import paired, post
from test_ade_link import owner  # noqa: F401  the fixture
from test_ade_link_live_server import _live, _note
from test_connector_tools import history, row


def _view(client, owner_headers, s):
    got = client.get("/api/ade/link/sessions", headers=owner_headers)
    assert got.status_code == 200, got.text
    return next(x for x in got.json()["links"] if x["id"] == s.link_id)


def test_the_owner_s_view_gives_each_link_its_newest_step_and_its_live_round(client, owner):
    mac = paired(client, owner)
    seen = _view(client, owner, mac)
    assert seen.get("last_step", "absent") is None and seen.get("last_live", "absent") is None
    assert post(mac, "step", "built the image").status_code == 200
    assert post(mac, "step", "ran the tests").status_code == 200
    assert post(mac, "note", _live(1), mirror="live").status_code == 200
    assert post(mac, "note", "said there", mirror="session").status_code == 200   # newer, mirrored, not a round
    assert post(mac, "note", "a note of its own").status_code == 200        # newer, neither a step nor a round
    seen = _view(client, owner, mac)
    assert (seen["last_step"]["kind"], seen["last_step"]["text"]) == ("step", "ran the tests")
    assert (seen["last_live"]["mirror"], seen["last_live"]["text"]) == ("live", _live(1))
    assert seen["last_event"]["text"] == "a note of its own"


def test_a_registered_session_s_round_is_under_that_session_not_its_link_s_own(client, owner):
    # as the link's newest event is its own: a session the link registered has its rounds under it
    mac = paired(client, owner)
    history(mac, [row("s1", "Work", "~/code", registered=True)])
    at = int(time.time()) * 1000
    assert post(mac, "note", _live(1), mirror="live", session="s1", said_at=at).status_code == 200
    assert _view(client, owner, mac).get("last_live", "absent") is None
    assert post(mac, "note", _live(2), mirror="live").status_code == 200   # the link's own round
    assert _view(client, owner, mac)["last_live"]["text"] == _live(2)


def test_a_round_a_stop_posted_again_whole_says_so_on_the_owner_s_view(client, owner):
    # the page's conversation leaves out a round its session's Stop posted again whole, the Stop's
    # note saying its words; the owner's view says so of the round a card would show, so the card says them once
    mac = paired(client, owner)
    t = (int(time.time()) - 60) * 1000
    seq = _note(mac, _live(1), "live", "s9", t)                     # s9 not registered: the link's own conversation
    seen = _view(client, owner, mac)["last_live"]
    assert seen["seq"] == seq and "posted_again" not in seen
    _note(mac, "live 1", "session", "s9", t + 1000, whole_since=t)  # its Stop, which posted the turn's words whole
    seen = _view(client, owner, mac)["last_live"]
    assert seen["seq"] == seq and seen.get("posted_again") is True


def test_the_owner_s_view_reads_the_account_s_events_once_for_its_cards(client, owner, monkeypatch):
    # the page polls this view; how many events each link posted, its newest, its newest step and
    # its live view's newest round come of one pass over the account's events, not one pass each
    mac = paired(client, owner)
    assert post(mac, "step", "built the image").status_code == 200
    assert post(mac, "note", _live(1), mirror="live").status_code == 200
    reads, real = [], store.connect

    @contextlib.contextmanager
    def traced(*args, **kw):
        with real(*args, **kw) as db:
            db.set_trace_callback(reads.append)
            yield db

    monkeypatch.setattr(store, "connect", traced)
    seen = _view(client, owner, mac)
    assert (seen["events"], seen["last_step"]["text"], seen["last_live"]["mirror"]) == (2, "built the image", "live")
    passes = [s for s in reads if "ade_link_events" in s and "GROUP BY" in s.upper()]
    assert len(passes) == 1, passes
