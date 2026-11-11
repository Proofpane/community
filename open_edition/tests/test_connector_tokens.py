"""A connected app's tokens (oauth.py): a code exchanged once, with its PKCE verifier, client, redirect address and
resource; access and refresh tokens kept only as hashes; each refresh rotating its token; a refresh token presented
again ending the connection, however soon, and the owner told; a code or refresh token found before any write lock, and
only a request naming one the ADE did not give, or another app's, counted (by app, network and connection) and
refused, so others' failures never refuse an app's own; the access token found with its grant at /mcp, its hold
settled when due, and a token refused for its grant's sake an event at most once a minute."""

import os
import time

import pytest

from conftest import signed_up
from connector_fixtures import (CLAUDE, DROP, RESOURCE, allowed, connected, exchange, refresh)
from proofpane_open import config, oauth, store
from proofpane_open.ade import push


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.delenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", raising=False)
    oauth._failed.clear()


@pytest.fixture
def told(monkeypatch):
    """Every notice the account's devices are sent at once, stood in: its title, body and page."""
    sent = []
    monkeypatch.setattr(push, "notice", lambda user_id, message, now=None, post=None:
                        sent.append({"title": message.title, "body": message.body, "url": message.url}) or 1)
    return sent


@pytest.fixture
def owner(client):
    return signed_up(client)


def grant():
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_connector_grants").fetchone())


def events():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT kind, what, subject, outcome FROM ade_connector_events ORDER BY "
                                            "id")]


def test_a_code_is_exchanged_for_an_access_and_a_refresh_token(client, owner):
    got = allowed(client, owner)
    r = exchange(client, got)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    t = r.json()
    assert t["access_token"].startswith("ppca_") and t["refresh_token"].startswith("ppcr_")
    assert (t["token_type"], t["expires_in"], t["scope"]) == ("Bearer", 3600, "ade.read ade.propose offline_access")
    assert grant()["connected_at"] is not None
    assert [e["kind"] for e in events()] == ["allowed", "connected"]


def test_no_code_or_token_is_kept_as_given(client, owner):
    t = connected(client, owner)
    again = refresh(client, t, t["refresh_token"]).json()
    with open(config.db_path(), "rb") as fh:
        held = fh.read()
    for wal in (config.db_path() + "-wal",):
        if os.path.exists(wal):
            held += open(wal, "rb").read()
    for secret in (t["code"], t["access_token"], t["refresh_token"], again["access_token"], again["refresh_token"]):
        assert secret.encode() not in held
    with store.connect() as db:
        kept = {r["hash"] for r in db.execute("SELECT hash FROM ade_connector_tokens")}
    assert oauth.token_hash(again["access_token"]) in kept and oauth.token_hash(t["code"]) in kept


def test_without_offline_access_there_is_no_refresh_token(client, owner):
    t = connected(client, owner, scope="ade.read ade.propose")
    assert "refresh_token" not in t and t["scope"] == "ade.read ade.propose"


def test_a_code_used_twice_revokes_what_it_made_and_tells_the_owner(client, owner, told):
    got = allowed(client, owner)
    assert exchange(client, got).status_code == 200
    r = exchange(client, got)
    assert (r.status_code, r.json()["error"]) == (400, "invalid_grant")
    assert grant()["revoked_why"] == "its code was used twice"
    # in fixed words, never the app's name: the page names it (Connected apps)
    assert told[-1] == {"title": "A connected app was disconnected", "body": "Open Connected apps.", "url": "/app/#apps"}
    assert events()[-1] == {"kind": "reuse", "what": "code", "subject": None, "outcome": "refused"}


@pytest.mark.parametrize("which", ["code", "refresh token"])
def test_a_used_code_or_refresh_token_ends_what_it_made_whichever_app_names_it(client, owner, which):
    """Whoever holds a used code or refresh token holds what the app held: named in another app's name it is still
    presented twice, not merely another app's, and is neither counted nor refused before it ends its connection."""
    t = connected(client, owner)
    other = "ppcl-" + "1" * 32
    if which == "code":
        r = exchange(client, t, client_id=other)
    else:
        assert refresh(client, t, t["refresh_token"]).status_code == 200
        r = refresh(client, t, t["refresh_token"], client_id=other)
    assert (r.status_code, r.json()["error"]) == (400, "invalid_grant")
    assert grant()["revoked_why"] == f"{'its code' if which == 'code' else 'a refresh token'} was used twice"
    assert oauth._failed == {}


@pytest.mark.parametrize("over, status, error", [
    ({"code_verifier": "x" * 43}, 400, "invalid_grant"),
    ({"code_verifier": "short"}, 400, "invalid_request"),
    ({"code_verifier": DROP}, 400, "invalid_request"),
    ({"redirect_uri": "http://localhost:3118/callback"}, 400, "invalid_grant"),
    ({"client_id": "ppcl-" + "1" * 32}, 401, "invalid_client"),
    ({"resource": "https://elsewhere.example/mcp"}, 400, "invalid_target"),
    ({"code": "ppcc_unknown"}, 400, "invalid_grant"),
    ({"grant_type": "client_credentials"}, 400, "unsupported_grant_type"),
    ({"grant_type": DROP}, 400, "unsupported_grant_type"),
])
def test_a_code_exchange_that_does_not_hold_is_refused_and_the_code_stays_unused(client, owner, over, status, error):
    got = allowed(client, owner)
    r = exchange(client, got, **over)
    assert (r.status_code, r.json()["error"]) == (status, error), r.text
    assert exchange(client, got).status_code == 200


@pytest.mark.parametrize("resource", [DROP, "HTTPS://OPEN.example.test/mcp/"])
def test_the_resource_may_be_left_out_or_given_in_another_canonical_form(client, owner, resource):
    assert exchange(client, allowed(client, owner), resource=resource).status_code == 200


def test_a_code_lives_sixty_seconds(client, owner):
    got = allowed(client, owner)
    form = {"grant_type": "authorization_code", "code": got["code"], "client_id": got["client_id"],
            "redirect_uri": CLAUDE, "code_verifier": got["verifier"]}
    with pytest.raises(oauth.OAuthError, match="expired"):
        oauth.token(form, now=int(time.time()) + 61)
    assert oauth.token(form)["access_token"]


def test_a_token_request_is_form_encoded_with_each_parameter_once(client, owner):
    got = allowed(client, owner)
    r = client.post("/oauth/token", json={"grant_type": "authorization_code", "code": got["code"]})
    assert (r.status_code, r.json()["error"]) == (400, "invalid_request")
    body = (f"grant_type=authorization_code&code={got['code']}&code={got['code']}&client_id={got['client_id']}"
            f"&redirect_uri={CLAUDE}&code_verifier={got['verifier']}")
    r = client.post("/oauth/token", content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert "more than once" in r.json()["error_description"]


def test_a_refresh_rotates_its_token(client, owner):
    t = connected(client, owner)
    r = refresh(client, t, t["refresh_token"])
    assert r.status_code == 200, r.text
    n = r.json()
    assert n["refresh_token"] != t["refresh_token"] and n["access_token"] != t["access_token"]
    assert refresh(client, t, n["refresh_token"]).status_code == 200
    assert [e["kind"] for e in events()] == ["allowed", "connected", "refreshed", "refreshed"]


@pytest.mark.parametrize("later", [0, 1, 61, 3600])
def test_a_refresh_token_presented_again_however_soon_ends_the_connection_and_the_owner_is_told(client, owner, told,
                                                                                               later):
    """RFC 9700 (4.14.2): the server cannot tell the app from whoever copied its token, so neither keeps what the token
    made, the new tokens of the first refresh included."""
    t = connected(client, owner)
    now = int(time.time())
    n = oauth.token({"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": t["client_id"]},
                    now=now)
    with pytest.raises(oauth.OAuthError, match="used already") as e:
        oauth.token({"grant_type": "refresh_token", "refresh_token": t["refresh_token"],
                     "client_id": t["client_id"]}, now=now + later)
    assert e.value.error == "invalid_grant"
    assert grant()["revoked_why"] == "a refresh token was used twice"
    assert told[-1] == {"title": "A connected app was disconnected", "body": "Open Connected apps.", "url": "/app/#apps"}
    assert events()[-1] == {"kind": "reuse", "what": "refresh token", "subject": None, "outcome": "refused"}
    assert refresh(client, t, n["refresh_token"]).json()["error"] == "invalid_grant"
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate("Bearer " + n["access_token"], now=now + later)


def test_a_refresh_that_does_not_hold_is_refused(client, owner):
    t = connected(client, owner)
    for over, error in (({"client_id": "ppcl-" + "1" * 32}, "invalid_client"),
                        ({"resource": "https://elsewhere.example/mcp"}, "invalid_target"),
                        ({"scope": "ade.read admin"}, "invalid_scope"), ({"refresh_token": "ppcr_x"}, "invalid_grant"),
                        ({"refresh_token": DROP}, "invalid_request")):
        r = refresh(client, t, t["refresh_token"], **over)
        assert r.json()["error"] == error, (over, r.text)
    assert refresh(client, t, t["refresh_token"]).status_code == 200


def test_a_refresh_may_narrow_the_scope_and_keeps_its_refresh_token(client, owner):
    t = connected(client, owner)
    n = refresh(client, t, t["refresh_token"], scope="ade.read").json()
    assert n["scope"] == "ade.read" and n["refresh_token"]
    assert oauth.authenticate("Bearer " + n["access_token"])["scopes"] == ["ade.read"]


def test_a_refresh_token_lives_thirty_days(client, owner):
    t = connected(client, owner)
    base = {"grant_type": "refresh_token", "client_id": t["client_id"], "refresh_token": t["refresh_token"]}
    with pytest.raises(oauth.OAuthError, match="no longer works"):
        oauth.token(base, now=grant()["connected_at"] + 30 * 86400)
    assert oauth.token(base, now=grant()["connected_at"] + 30 * 86400 - 1)["refresh_token"]


def test_no_token_outlives_its_grant(client, owner, monkeypatch):
    monkeypatch.setattr(oauth, "REFRESH_TTL", 100 * 86400)
    t = connected(client, owner)
    g = grant()
    base = {"grant_type": "refresh_token", "client_id": t["client_id"]}
    late = oauth.token(dict(base, refresh_token=t["refresh_token"]), now=g["ends_at"] - 10)
    assert late["expires_in"] == 10
    with pytest.raises(oauth.OAuthError, match="no longer works"):
        oauth.token(dict(base, refresh_token=late["refresh_token"]), now=g["ends_at"])
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate("Bearer " + late["access_token"], now=g["ends_at"])


def test_refreshes_are_counted_per_grant_in_the_hour(client, owner, monkeypatch):
    monkeypatch.setattr(oauth, "REFRESHES_PER_HOUR", 2)
    t = connected(client, owner)
    token = t["refresh_token"]
    for _ in range(2):
        token = refresh(client, t, token).json()["refresh_token"]
    r = refresh(client, t, token)
    assert r.status_code == 429


def test_others_failures_never_refuse_an_app_s_own_code_or_refresh_token(client, owner, monkeypatch):
    """The Claude app names every user's connection by one client_id and calls from Anthropic's addresses: whoever
    sends codes of their own making through Claude's callback fills the counts of that client and that network, and
    still an app's own code and refresh token, named in that client's name from that network, go through."""
    monkeypatch.setattr(oauth, "FAILED_PER_CLIENT", 3)
    monkeypatch.setattr(oauth, "FAILED_PER_NETWORK", 3)
    got = allowed(client, owner)
    claude, now = "160.79.104.10", int(time.time())
    form = {"grant_type": "authorization_code", "client_id": got["client_id"], "redirect_uri": CLAUDE,
            "code_verifier": got["verifier"]}
    refused = []
    for code in ("garbage", "ppcc_" + "A" * 43, "ppcc_" + "B" * 43, "ppcc_" + "C" * 43, "garbage"):
        with pytest.raises(oauth.OAuthError) as e:
            oauth.token(dict(form, code=code), client=claude, now=now)
        refused.append(e.value.status)
    assert refused == [400, 400, 400, 429, 429]
    t = oauth.token(dict(form, code=got["code"]), client=claude, now=now)
    n = oauth.token({"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": got["client_id"]},
                    client=claude, now=now)
    assert n["access_token"] and n["refresh_token"]
    with pytest.raises(oauth.OAuthError) as e:                      # a refresh token it never gave: still refused
        oauth.token({"grant_type": "refresh_token", "refresh_token": "ppcr_" + "A" * 43,
                     "client_id": got["client_id"]}, client=claude, now=now)
    assert e.value.status == 429


def test_an_app_s_own_code_refused_for_another_reason_is_not_counted(client, owner, monkeypatch):
    monkeypatch.setattr(oauth, "FAILED_PER_CLIENT", 2)
    monkeypatch.setattr(oauth, "FAILED_PER_NETWORK", 2)
    got = allowed(client, owner)
    for _ in range(3):
        assert exchange(client, got, code_verifier="x" * 43).json()["error"] == "invalid_grant"
        assert exchange(client, got, redirect_uri="http://localhost:3118/callback").json()["error"] == "invalid_grant"
        assert exchange(client, got, resource="https://elsewhere.example/mcp").json()["error"] == "invalid_target"
    assert oauth._failed == {}
    assert exchange(client, got).status_code == 200


def test_unknown_codes_are_counted_per_app_named_from_any_network(monkeypatch):
    monkeypatch.setattr(oauth, "FAILED_PER_CLIENT", 3)
    bad = {"grant_type": "authorization_code", "code": "ppcc_" + "A" * 43, "client_id": "ppcl-" + "1" * 32,
           "redirect_uri": CLAUDE, "code_verifier": "v" * 43}
    for n in range(3):
        with pytest.raises(oauth.OAuthError) as e:
            oauth.token(bad, client=f"203.0.113.{n}", now=1_000_000)
        assert e.value.status == 400
    with pytest.raises(oauth.OAuthError) as e:
        oauth.token(bad, client="198.51.100.7", now=1_000_000)
    assert e.value.status == 429 and "naming this app" in e.value.description
    with pytest.raises(oauth.OAuthError) as e:                      # another app named has its own count
        oauth.token(dict(bad, client_id="ppcl-" + "2" * 32), client="198.51.100.7", now=1_000_000)
    assert e.value.status == 400


def test_a_code_or_refresh_token_named_as_another_app_s_is_counted_by_its_connection(client, owner, monkeypatch):
    """Another app's code or refresh token, unused, is refused as that (401) and counted by the connection it belongs
    to, whatever app each names; the connection's own app is never refused for it."""
    monkeypatch.setattr(oauth, "FAILED_PER_GRANT", 2)
    t = connected(client, owner)
    for n in range(2):
        r = refresh(client, t, t["refresh_token"], client_id=f"ppcl-{n:032d}")
        assert (r.status_code, r.json()["error"]) == (401, "invalid_client"), r.text
    r = refresh(client, t, t["refresh_token"], client_id="ppcl-" + "9" * 32)
    assert r.status_code == 429 and "with this connection's code or refresh token" in r.json()["error_description"]
    assert refresh(client, t, t["refresh_token"]).status_code == 200


def watch_store(monkeypatch):
    """store.connect watched: ([1 for each connection opened], [each BEGIN run])."""
    real, opened, begun = store.connect, [], []

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
            opened.append(1)
            with real() as db:
                yield Watched(db)
        return watched()

    monkeypatch.setattr(store, "connect", connect)
    return opened, begun


def test_a_code_or_refresh_token_is_found_before_any_write_lock(client, owner, monkeypatch):
    """A code is looked up before the write lock is taken, its shape checked first: a request with a code this ADE did
    not give costs one read at most, and one of a shape it never gives costs none."""
    got = allowed(client, owner)
    opened, begun = watch_store(monkeypatch)
    form = {"grant_type": "authorization_code", "client_id": got["client_id"], "redirect_uri": CLAUDE,
            "code_verifier": got["verifier"]}
    for code, reads in (("garbage", 0), ("ppcc_" + "A" * 43 + "=", 0), ("ppcc_" + "A" * 42, 0),
                        ("ppcc_" + "A" * 43, 1)):
        opened.clear()
        with pytest.raises(oauth.OAuthError):
            oauth.token(dict(form, code=code))
        assert (len(opened), begun) == (reads, []), code
    assert oauth.token(dict(form, code=got["code"]))["access_token"]
    assert begun == ["BEGIN IMMEDIATE"]


def test_failed_token_requests_are_counted_per_network_whatever_client_each_names(monkeypatch):
    monkeypatch.setattr(oauth, "FAILED_PER_NETWORK", 3)
    bad = {"grant_type": "refresh_token", "refresh_token": "ppcr_unknown"}
    for n in range(3):
        with pytest.raises(oauth.OAuthError) as e:
            oauth.token(dict(bad, client_id=f"ppcl-{n:032d}"), client="203.0.113.5", now=1_000_000)
        assert e.value.status == 400
    with pytest.raises(oauth.OAuthError) as e:
        oauth.token(dict(bad, client_id="ppcl-" + "9" * 32), client="203.0.113.5", now=1_000_000)
    assert e.value.status == 429 and "from this network" in e.value.description
    with pytest.raises(oauth.OAuthError) as e:                           # another network has its own count
        oauth.token(dict(bad, client_id="ppcl-" + "9" * 32), client="198.51.100.7", now=1_000_000)
    assert e.value.status == 400


def test_a_count_reads_its_own_window_and_counts_again_once_the_window_has_passed(monkeypatch):
    monkeypatch.setattr(oauth, "FAILED_PER_NETWORK", 2)
    bad = {"grant_type": "refresh_token", "refresh_token": "x", "client_id": "a"}
    for later in (0, 1):
        with pytest.raises(oauth.OAuthError) as e:
            oauth.token(bad, client="203.0.113.5", now=1_000_000 + later)
        assert e.value.status == 400
    with pytest.raises(oauth.OAuthError) as e:
        oauth.token(bad, client="203.0.113.5", now=1_000_002)
    assert e.value.status == 429
    with pytest.raises(oauth.OAuthError) as e:                      # the first has left the window
        oauth.token(bad, client="203.0.113.5", now=1_000_000 + oauth.FAILED_WINDOW)
    assert e.value.status == 400
    assert oauth._failed[("network", "203.0.113.5")] == [1_000_001, 1_000_000 + oauth.FAILED_WINDOW]


# ---- the access token at /mcp ---------------------------------------------------------------------------------------

def test_an_access_token_names_its_grant_held_until_its_time(client, owner):
    t = connected(client, owner)
    got = oauth.authenticate("Bearer " + t["access_token"])
    assert (got["id"], got["client_name"], got["scopes"], got["active"]) == \
        (t["grant"], "Claude", ["ade.read", "ade.propose", "offline_access"], False)
    assert got["takes_effect_at"] == grant()["takes_effect_at"]
    # at its time the hold is settled by the token's own use, the notifier or a page not needed
    later = oauth.authenticate("Bearer " + t["access_token"], now=got["takes_effect_at"])
    assert later["active"] is True and grant()["active_at"] == got["takes_effect_at"]


@pytest.mark.parametrize("header, given", [(None, False), ("", False), ("Basic abc", False), ("Bearer", False),
                                           ("Bearer ppca_unknown", True), ("Bearer x.y", True)])
def test_no_token_or_one_that_is_not_a_connector_s_is_unauthorized(header, given):
    with pytest.raises(oauth.Unauthorized) as e:
        oauth.authenticate(header)
    assert e.value.given is given


def test_a_sign_in_token_is_no_connector_token(client, owner):
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate(owner["Authorization"])


def test_an_account_deleted_from_the_store_by_hand_has_no_working_connector_token(client, owner):
    """The app deletes no account; one an operator deletes from the store reads nothing through its apps, and its
    refresh token gets nothing."""
    t = connected(client, owner)
    assert oauth.authenticate("Bearer " + t["access_token"])["id"] == t["grant"]
    with store.connect() as db:
        db.execute("DELETE FROM users WHERE id = ?", (grant()["user_id"],))
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate("Bearer " + t["access_token"])
    r = refresh(client, t, t["refresh_token"])
    assert (r.status_code, r.json()["error"]) == (400, "invalid_grant")
    assert grant()["revoked_at"] is None                    # nothing of the grant was written for it


def test_an_expired_or_revoked_token_or_one_for_another_address_stops_and_is_noted(client, owner, monkeypatch):
    t = connected(client, owner)
    header, now = "Bearer " + t["access_token"], int(time.time())
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "https://moved.example.test")
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate(header, now=now)
    assert events()[-1] == {"kind": "refused", "what": "token", "subject": "made for another address",
                            "outcome": "refused"}
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "https://open.example.test")
    with store.connect() as db:
        oauth.revoke_in(db, db.execute("SELECT * FROM ade_connector_grants").fetchone(), "the owner revoked it", now)
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate(header, now=now + oauth.REFUSED_EVERY)
    assert events()[-1]["subject"] == "revoked"
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate(header, now=now + 3700)
    assert events()[-1]["subject"] == "expired"


def test_a_token_refused_again_and_again_is_one_event_a_minute(client, owner):
    """An app that goes on with a token that no longer works fills none of the account's events."""
    t = connected(client, owner)
    header, now = "Bearer " + t["access_token"], int(time.time())
    client.delete(f"/api/connector/grants/{t['grant']}", headers=owner)
    for later in range(0, oauth.REFUSED_EVERY, 7):
        with pytest.raises(oauth.Unauthorized):
            oauth.authenticate(header, now=now + later)
    assert [e["subject"] for e in events() if e["kind"] == "refused"] == ["revoked"]
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate(header, now=now + oauth.REFUSED_EVERY)
    assert [e["subject"] for e in events() if e["kind"] == "refused"] == ["revoked", "revoked"]


def test_a_grant_s_last_use_is_written_at_most_once_a_minute(client, owner):
    t = connected(client, owner)
    now = int(time.time())
    oauth.authenticate("Bearer " + t["access_token"], client="203.0.113.5", now=now)
    oauth.authenticate("Bearer " + t["access_token"], client="203.0.113.6", now=now + 59)
    assert (grant()["last_used_at"], grant()["last_net"]) == (now, "203.0.113.5")
    oauth.authenticate("Bearer " + t["access_token"], client="203.0.113.6", now=now + 60)
    assert (grant()["last_used_at"], grant()["last_net"]) == (now + 60, "203.0.113.6")


def test_a_grant_revoked_while_its_hold_waits_never_takes_effect(client, owner):
    t = connected(client, owner)
    with store.connect() as db:
        oauth.revoke_in(db, db.execute("SELECT * FROM ade_connector_grants").fetchone(), "the owner revoked it",
                        int(time.time()))
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate("Bearer " + t["access_token"], now=grant()["takes_effect_at"])
    assert grant()["active_at"] is None


def test_the_code_s_redirect_address_must_be_the_one_it_was_given_for(client, owner):
    got = allowed(client, owner, redirect="http://localhost/callback")
    assert exchange(client, got, redirect_uri="http://localhost:9999/callback").json()["error"] == "invalid_grant"
    assert exchange(client, got, redirect_uri="http://localhost/callback").status_code == 200
    assert RESOURCE
