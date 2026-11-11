"""A session that is working is never shown as silent. A link's silence
is counted from the latest request it signs, of any kind: a live round, a post, a checkpoint, a turn's end, as well as
a heartbeat (`last_seen_at`, which `authenticate` moves once a request's signature holds, or the heartbeat's own time
where that is later). A request whose signature fails moves nothing. The owner's view of the links, the notifier's
`link-silent` items, the other sessions' view and a resume's lines all read that one time."""

import time

from proofpane_open.ade import link
from test_ade_link import paired, row
from test_ade_link import owner  # noqa: F401  the fixture
from test_ade_link_live_server import _live

MINUTE = 60


def _at(monkeypatch, at):
    """The server's clock at `at`, as the link tests set it."""
    monkeypatch.setattr(link.time, "time", lambda: at)


def _round(s, at, n):
    """A live round the session signs at `at`: a mirrored note, mirror "live", and no heartbeat."""
    r = s.call("POST", "/events", {"client_id": f"live-{n}-{at}", "kind": "note", "text": _live(n), "mirror": "live"},
               ts=at)
    assert r.status_code == 200, r.text


def _view(client, owner_headers, s):
    got = client.get("/api/ade/link/sessions", headers=owner_headers)
    assert got.status_code == 200, got.text
    return next(x for x in got.json()["links"] if x["id"] == s.link_id)


def _silent_items(user_id, at):
    return [key for key, _ in link.alerts(user_id, now=at) if key.startswith("link-silent:")]


def test_a_link_sending_live_rounds_and_no_heartbeat_is_not_silent(client, owner, monkeypatch):
    t0 = int(time.time())
    mac = paired(client, owner)
    for n, minutes in enumerate((10, 20, 30)):
        _at(monkeypatch, t0 + minutes * MINUTE)
        _round(mac, t0 + minutes * MINUTE, n)
    at = t0 + 31 * MINUTE                                     # past two of a local link's 15-minute intervals
    _at(monkeypatch, at)
    seen = _view(client, owner, mac)
    assert seen["silent"] is False
    assert seen.get("last_heard_at") == t0 + 30 * MINUTE      # the last round's own time
    assert _silent_items(row(mac.link_id)["user_id"], at) == []


def test_a_link_sending_nothing_for_two_intervals_is_silent_and_the_notifier_says_so(client, owner, monkeypatch):
    t0 = int(time.time())
    mac = paired(client, owner)
    _at(monkeypatch, t0 + 10 * MINUTE)
    _round(mac, t0 + 10 * MINUTE, 0)
    user_id = row(mac.link_id)["user_id"]
    _at(monkeypatch, t0 + 39 * MINUTE)                        # 29 minutes after its last round
    assert _view(client, owner, mac)["silent"] is False
    assert _silent_items(user_id, t0 + 39 * MINUTE) == []
    _at(monkeypatch, t0 + 41 * MINUTE)                        # 31 minutes after it
    seen = _view(client, owner, mac)
    assert seen["silent"] is True and seen.get("last_heard_at") == t0 + 10 * MINUTE
    assert _silent_items(user_id, t0 + 41 * MINUTE) == [f"link-silent:{mac.link_id}"]


def test_a_request_whose_signature_fails_moves_nothing(client, owner, monkeypatch):
    t0 = int(time.time())
    mac = paired(client, owner)
    _at(monkeypatch, t0 + 10 * MINUTE)
    _round(mac, t0 + 10 * MINUTE, 0)
    before = row(mac.link_id)
    at = t0 + 41 * MINUTE
    _at(monkeypatch, at)
    bad = mac.call("POST", "/heartbeat", {}, ts=at, sign_body=b"{}x")     # its key, a signature not over this body
    skewed = mac.call("POST", "/heartbeat", {}, ts=at - 3600)  # a signature an hour off the server's clock
    assert bad.status_code == 401 and skewed.status_code == 401
    after = row(mac.link_id)
    assert (after["last_seen_at"], after["last_heartbeat_at"]) == (before["last_seen_at"], before["last_heartbeat_at"])
    assert _view(client, owner, mac)["silent"] is True


def test_a_heartbeat_later_than_the_last_request_seen_is_the_time_heard(client, owner, monkeypatch):
    # authenticate moves last_seen_at at most once a minute, and a heartbeat moves last_heartbeat_at to its own time:
    # the later of the two is when the link was last heard
    t0 = int(time.time())
    mac = paired(client, owner)
    _at(monkeypatch, t0 + 10 * MINUTE)
    _round(mac, t0 + 10 * MINUTE, 0)
    _at(monkeypatch, t0 + 10 * MINUTE + 30)
    assert mac.call("POST", "/heartbeat", {}, ts=t0 + 10 * MINUTE + 30).status_code == 200
    assert row(mac.link_id)["last_seen_at"] == t0 + 10 * MINUTE              # within the minute: not moved again
    _at(monkeypatch, t0 + 40 * MINUTE + 15)                                   # 30 min 15 s after the round alone
    seen = _view(client, owner, mac)
    assert seen.get("last_heard_at") == t0 + 10 * MINUTE + 30 and seen["silent"] is False


def test_the_other_sessions_and_a_resume_read_the_time_heard(client, owner, monkeypatch):
    t0 = int(time.time())
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    assert client.put(f"/api/ade/link/links/{cloud.link_id}/reads-others", headers=owner,
                      json={"allowed": True}).status_code == 200
    _at(monkeypatch, t0 + 30 * MINUTE)
    _round(mac, t0 + 30 * MINUTE, 0)
    at = t0 + 31 * MINUTE
    _at(monkeypatch, at)
    got = cloud.call("GET", "/others", ts=at).json()
    (other,) = [x for x in got["sessions"] if x["name"] == "mac"]
    assert other["silent"] is False and other.get("last_heard") == link._iso(t0 + 30 * MINUTE)
    document = cloud.call("GET", "/resume", ts=at).json()["document"]
    assert f"mac (local), last heard {link._iso(t0 + 30 * MINUTE)}:" in document


def test_the_session_client_lists_the_others_by_when_they_were_last_heard(monkeypatch, tmp_path, capsys):
    # the time heard, as the server gives it; from a server before it, the heartbeat
    import ade_link
    answers = [{"rule": "r", "sessions": [{"name": "cloud", "kind": "cloud", "silent": False,
                                           "last_heartbeat": "2026-11-07T11:30:00Z",
                                           "last_heard": "2026-11-07T12:00:00Z", "checkpoint": None,
                                           "checkpoint_version": 0}]},
               {"rule": "r", "sessions": [{"name": "cloud", "kind": "cloud", "silent": True,
                                           "last_heartbeat": "2026-11-07T11:00:00Z", "checkpoint": None,
                                           "checkpoint_version": 0}]}]
    monkeypatch.setattr(ade_link.Link, "request", lambda self, method, path, query=None, body=None: answers.pop(0))
    monkeypatch.setattr(ade_link.Link, "__init__", lambda self, directory: setattr(self, "config", {"url": "x"}))
    assert ade_link.main(["--dir", str(tmp_path), "others"]) == 0
    assert "cloud (cloud), last heard 2026-11-07T12:00:00Z, checkpoint version 0: null" in capsys.readouterr().out
    assert ade_link.main(["--dir", str(tmp_path), "others"]) == 0
    assert ("cloud (cloud), last heard 2026-11-07T11:00:00Z, silent, checkpoint version 0: null"
            in capsys.readouterr().out)


def test_a_new_link_is_not_heard_until_it_signs_a_request_as_a_link(client, owner, monkeypatch):
    # pairing makes the link, and its first request as a link is when it is first heard; a
    # session that stopped while its pairing waited is "not heard from yet", and silent two intervals on
    t0 = int(time.time())
    _at(monkeypatch, t0)
    mac = paired(client, owner)
    seen = _view(client, owner, mac)
    assert seen["last_heard_at"] is None and seen["silent"] is False
    _at(monkeypatch, t0 + 120)
    assert mac.call("GET", "/feed", ts=t0 + 120).status_code == 200
    assert _view(client, owner, mac)["last_heard_at"] == t0 + 120


def test_a_heartbeat_ahead_of_the_server_s_clock_is_kept_at_the_server_s_time(client, owner, monkeypatch):
    # a heartbeat's own time is the session's, within the server's skew; it is kept at no
    # later than the server's clock, so a time heard is never ahead of it
    t0 = int(time.time())
    mac = paired(client, owner)
    _at(monkeypatch, t0 + 600)
    assert mac.call("POST", "/heartbeat", {}, ts=t0 + 600 + 299).status_code == 200
    assert row(mac.link_id)["last_heartbeat_at"] == t0 + 600
    assert _view(client, owner, mac)["last_heard_at"] == t0 + 600


def test_at_most_one_heartbeat_a_minute_by_the_server_s_clock_whatever_the_session_s(client, owner, monkeypatch):
    # a guard for the heartbeat kept at the server's time: its once a minute is counted by that time too, so a session
    # whose clock is ahead cannot beat more often
    t0 = int(time.time())
    mac = paired(client, owner)
    _at(monkeypatch, t0 + 600)
    assert mac.call("POST", "/heartbeat", {}, ts=t0 + 600 + 299).status_code == 200
    _at(monkeypatch, t0 + 630)
    assert mac.call("POST", "/heartbeat", {}, ts=t0 + 630 + 299).status_code == 429

