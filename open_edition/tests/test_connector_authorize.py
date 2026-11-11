"""Connecting an app (oauth.py): /oauth/authorize checks the client and its redirect address before anything else and
never redirects to one that does not hold; the rest goes back to the app with the error, the state and the issuer; a
request that holds is kept and the browser sent to the app's consent pane, where the owner allows it, with the
account's confirmation and its switch on, or refuses it. Allowing holds the grant, and cancelling the hold revokes
it."""

import hashlib

import pytest

from conftest import signed_up
from connector_fixtures import (CLAUDE, DROP, ISSUER, allow, allowed, authorize, confirmation, pkce, query, register,
                                request_id, turn_on)
from proofpane_open import oauth, passkeys, store
from proofpane_open.ade import holds


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR_REDIRECTS", raising=False)
    monkeypatch.delenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", raising=False)


@pytest.fixture
def owner(client):
    return signed_up(client)


def rows(table):
    with store.connect() as db:
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]


def test_a_request_that_holds_is_kept_and_the_browser_sent_to_the_consent_pane(client):
    client_id = register(client)
    _, challenge = pkce()
    r = authorize(client, client_id, challenge)
    rid = request_id(r)
    assert len(rid) == 32 and r.headers["x-frame-options"] == "DENY"
    [row] = rows("ade_connector_requests")
    assert (row["id"], row["client_id"], row["redirect_uri"], row["state"], row["code_challenge"], row["scopes"]) == \
        (rid, client_id, CLAUDE, "st-1", challenge, "ade.read ade.propose offline_access")
    assert row["expires_at"] - row["created_at"] == 600 and row["answer"] is None


@pytest.mark.parametrize("over", [{"client_id": "ppcl-" + "0" * 32}, {"client_id": DROP},
                                  {"redirect_uri": "https://evil.example/callback"},
                                  {"redirect_uri": "http://localhost:3118/callback"}, {"redirect_uri": DROP}])
def test_a_client_or_redirect_address_that_does_not_hold_gets_the_ade_s_page_and_no_redirect(client, over):
    client_id = register(client)          # its list is Claude's callback alone
    r = authorize(client, client_id, pkce()[1], **over)
    assert r.status_code == 400 and "location" not in r.headers
    assert r.headers["content-type"].startswith("text/html") and r.headers["x-frame-options"] == "DENY"
    assert "The app was not connected" in r.text and "Nothing was sent to the app" in r.text
    assert rows("ade_connector_requests") == []


def test_the_error_page_says_why_and_escapes_it(client, monkeypatch):
    monkeypatch.setattr(oauth, "client_for", lambda *a: (_ for _ in ()).throw(oauth.ClientError("<b>no</b> app")))
    r = authorize(client, "x", pkce()[1])
    assert "&lt;b&gt;no&lt;/b&gt; app" in r.text and "<b>no</b>" not in r.text


@pytest.mark.parametrize("over, error", [
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"code_challenge": DROP}, "invalid_request"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge_method": DROP}, "invalid_request"),
    ({"code_challenge": "short"}, "invalid_request"),
    ({"code_challenge": "a" * 42 + "="}, "invalid_request"),
    ({"resource": DROP}, "invalid_target"),
    ({"resource": "https://elsewhere.example/mcp"}, "invalid_target"),
    ({"resource": "https://open.example.test/api"}, "invalid_target"),
    ({"scope": "ade.read admin"}, "invalid_scope"),
])
def test_the_rest_goes_back_to_the_app_with_the_error_the_state_and_the_issuer(client, over, error):
    r = authorize(client, register(client), pkce()[1], **over)
    assert r.status_code == 302
    got = query(r.headers["location"])
    assert r.headers["location"].startswith(CLAUDE + "?")
    assert (got["error"], got["state"], got["iss"]) == (error, "st-1", ISSUER) and got["error_description"]
    assert rows("ade_connector_requests") == []


def test_an_over_long_state_goes_back_without_it_and_a_repeated_parameter_is_refused(client):
    client_id = register(client)
    got = query(authorize(client, client_id, pkce()[1], state="s" * 501).headers["location"])
    assert got["error"] == "invalid_request" and "state" not in got
    url = "/oauth/authorize?" + "&".join(f"{k}={v}" for k, v in (("client_id", client_id), ("redirect_uri", CLAUDE),
                                                                 ("state", "a"), ("state", "b")))
    assert query(client.get(url, follow_redirects=False).headers["location"])["error"] == "invalid_request"


@pytest.mark.parametrize("resource", ["HTTPS://Open.Example.TEST/mcp", "https://open.example.test/mcp/",
                                      "https://open.example.test:443/mcp"])
def test_the_resource_is_taken_in_any_of_its_canonical_forms(client, resource):
    request_id(authorize(client, register(client), pkce()[1], resource=resource))


def test_no_scope_asks_for_both_and_read_is_always_there(client):
    client_id = register(client)
    request_id(authorize(client, client_id, pkce()[1], scope=DROP))
    request_id(authorize(client, client_id, pkce()[1], scope="ade.propose"))
    assert [r["scopes"] for r in rows("ade_connector_requests")] == ["ade.read ade.propose", "ade.read ade.propose"]


def request_params(client):
    return {"client_id": register(client), "redirect_uri": CLAUDE, "response_type": "code",
            "code_challenge": pkce()[1], "code_challenge_method": "S256", "resource": "https://open.example.test/mcp"}


def waiting_ids(now):
    return {r["id"] for r in rows("ade_connector_requests") if r["expires_at"] > now and r["answer"] is None}


def kept_id(answer):
    kind, where = answer
    assert kind == "page", where
    return where.split("#connect=")[1]


def test_requests_waiting_are_counted_per_network_and_per_wider_network(client, monkeypatch):
    p = request_params(client)
    monkeypatch.setattr(oauth, "REQUESTS_PER_NETWORK", 2)
    assert oauth.authorize(p, "203.0.113.5", now=1_000_000)[0] == "page"
    assert oauth.authorize(p, "203.0.113.5", now=1_000_000)[0] == "page"
    kind, words = oauth.authorize(p, "203.0.113.5", now=1_000_000)
    assert kind == "error" and "This network has 2 requests" in words
    assert oauth.authorize(p, "203.0.113.5", now=1_000_600)[0] == "page"        # the first two have expired
    monkeypatch.setattr(oauth, "REQUESTS_PER_WIDE_NETWORK", 3)
    assert oauth.authorize(p, "203.0.113.6", now=1_000_601)[0] == "page"
    assert oauth.authorize(p, "203.0.113.7", now=1_000_601)[0] == "page"         # the /24's third
    kind, words = oauth.authorize(p, "203.0.113.8", now=1_000_601)
    assert kind == "error" and "The wider network around this address (its /24, or its /48) has 3" in words
    assert oauth.authorize(p, "198.51.100.7", now=1_000_601)[0] == "page"        # another /24's own
    assert {r["asked_wide"] for r in rows("ade_connector_requests")} == {"203.0.113.0/24", "198.51.100.0/24"}


def test_at_the_cap_in_all_the_oldest_of_the_wider_network_with_the_most_waiting_goes(client, monkeypatch):
    """Requests to connect take no sign-in, so others could fill the deployment's places: at the cap a new one pushes
    out the oldest of the wider network that has the most waiting, and no one's own request is refused for it."""
    p = request_params(client)
    monkeypatch.setattr(oauth, "REQUESTS_WAITING", 4)
    mine = kept_id(oauth.authorize(p, "192.0.2.9", now=1_000_000))                 # the oldest, a network of its own
    flood = [kept_id(oauth.authorize(p, f"203.0.113.{n}", now=1_000_001 + n)) for n in range(3)]
    newest = kept_id(oauth.authorize(p, "198.51.100.7", now=1_000_010))
    assert waiting_ids(1_000_010) == {mine, *flood[1:], newest}
    assert flood[0] not in {r["id"] for r in rows("ade_connector_requests")}


@pytest.mark.parametrize("mode, dest", [("no-cors", "image"), ("navigate", "iframe"), ("navigate", "frame"),
                                        ("cors", "empty"), ("no-cors", None), (None, "image"), ("Navigate", "embed")])
def test_a_request_the_browser_says_is_not_a_page_of_its_own_is_neither_kept_nor_read(client, monkeypatch, mode,
                                                                                     dest):
    """A GET that a page of another site makes its visitors' browsers send (an image, a frame) keeps nothing, and reads
    no app's description, so it fills no count of theirs."""
    read = []
    monkeypatch.setattr(oauth, "client_for", lambda *a: read.append(a))
    p = request_params(client)
    headers = {k: v for k, v in (("Sec-Fetch-Mode", mode), ("Sec-Fetch-Dest", dest)) if v is not None}
    r = client.get("/oauth/authorize", params=p, headers=headers, follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers and "page of its own" in r.text
    assert rows("ade_connector_requests") == [] and read == []


@pytest.mark.parametrize("purpose", [{"Sec-Purpose": "prefetch"}, {"Sec-Purpose": "prefetch;prerender"},
                                     {"Sec-Purpose": "Prefetch; anonymous-client-ip"}, {"Sec-Purpose": " prerender "},
                                     {"Sec-Purpose": "a-purpose-to-come"}, {"Purpose": "prefetch"},
                                     {"Purpose": "Prefetch"}, {"Purpose": "preview"}])
def test_a_request_made_ahead_of_use_is_neither_kept_nor_read(client, monkeypatch, purpose):
    """A page's speculation rules have its visitors' browsers prefetch or prerender an address as a
    top-level navigation, saying so in Sec-Purpose (or, in older browsers, Purpose): such a request keeps nothing,
    reads no app's description and sets no cookie, so it fills no count of the visitor's network."""
    read = []
    monkeypatch.setattr(oauth, "client_for", lambda *a: read.append(a))
    headers = dict({"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "cross-site"},
                   **purpose)
    r = client.get("/oauth/authorize", params=request_params(client), headers=headers, follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers and "page of its own" in r.text
    assert "ahead of a click" in r.text and "set-cookie" not in r.headers
    assert rows("ade_connector_requests") == [] and read == []


@pytest.mark.parametrize("headers", [{}, {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
                                     {"Sec-Fetch-Mode": "navigate"}, {"Sec-Fetch-Dest": "document"},
                                     {"Sec-Fetch-Mode": " Navigate ", "Sec-Fetch-Dest": "Document"},
                                     {"Sec-Fetch-Mode": "", "Sec-Fetch-Dest": ""},
                                     {"Sec-Fetch-Mode": "navigate", "Sec-Purpose": "", "Purpose": " "}])
def test_a_top_level_navigation_or_a_browser_that_says_nothing_is_kept(client, headers):
    r = client.get("/oauth/authorize", params=request_params(client), headers=headers, follow_redirects=False)
    request_id(r)


def test_the_consent_pane_reads_what_asks(client, owner):
    rid = request_id(authorize(client, register(client), pkce()[1]))
    r = client.get(f"/api/connector/requests/{rid}", headers=owner)
    assert r.status_code == 200, r.text
    got = r.json()
    assert {k: got[k] for k in ("account", "account_on", "client_name", "described_at", "returns_to", "loopback",
                                "scopes", "hold_seconds", "lasts_days")} == \
        {"account": "admin@example.com", "account_on": False, "client_name": "Claude", "described_at": None,
         "returns_to": "claude.ai", "loopback": False, "scopes": ["ade.read", "ade.propose", "offline_access"],
         "hold_seconds": 180, "lasts_days": 90}
    assert client.get(f"/api/connector/requests/{rid}").status_code == 401           # the sign-in first
    assert client.get("/api/connector/requests/" + "0" * 32, headers=owner).status_code == 404


def test_a_loopback_redirect_is_said_to_be_one_with_its_port(client, owner):
    rid = request_id(authorize(client, register(client, "http://localhost/callback"), pkce()[1],
                               redirect="http://localhost:3118/callback"))
    got = client.get(f"/api/connector/requests/{rid}", headers=owner).json()
    assert (got["returns_to"], got["loopback"], got["port"]) == ("localhost", True, 3118)
    rid = request_id(authorize(client, register(client), pkce()[1]))
    assert client.get(f"/api/connector/requests/{rid}", headers=owner).json()["port"] is None


def test_the_pane_says_where_a_request_came_from_and_whether_it_is_this_browser_s_network(client, owner):
    """A forwarded #connect=ID: someone else's browser asked, and the owner's opens the pane. The pane says so, by the
    addresses the server saw (its network as device sign-ins compare them)."""
    p = {"client_id": register(client), "redirect_uri": CLAUDE, "response_type": "code", "code_challenge": pkce()[1],
         "code_challenge_method": "S256", "resource": "https://open.example.test/mcp", "state": "s"}
    rid = oauth.authorize(p, "203.0.113.5")[1].split("#connect=")[1]
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"], "email": "admin@example.com"}
    for viewer, same, shown in (("203.0.113.5", True, "203.0.113.5"), ("198.51.100.7", False, "198.51.100.7"),
                                ("testclient", None, None)):
        got = oauth.request_view(user, rid, viewer=viewer)
        assert (got["asked_from"], got["viewer"], got["same_network"]) == ("203.0.113.5", shown, same), viewer
    v6 = oauth.authorize(p, "2001:db8::5")[1].split("#connect=")[1]
    assert oauth.request_view(user, v6, viewer="2001:db8::77")["same_network"] is True       # one /64
    unread = oauth.authorize(p, "testclient")[1].split("#connect=")[1]
    assert oauth.request_view(user, unread, viewer="203.0.113.5")["same_network"] is None


@pytest.mark.parametrize("headers, site, came_from, from_app", [
    ({"Sec-Fetch-Site": "cross-site", "Referer": "https://claude.ai/project/1"}, "cross-site", "https://claude.ai", True),
    ({"Sec-Fetch-Site": "Cross-Site", "Referer": "HTTPS://Claude.AI:443/x?y#z"}, "cross-site", "https://claude.ai", True),
    ({"Sec-Fetch-Site": "cross-site", "Referer": "https://mail.example/inbox"}, "cross-site", "https://mail.example",
     False),
    ({"Sec-Fetch-Site": "cross-site", "Referer": "https://claude.ai.evil.example/"}, "cross-site",
     "https://claude.ai.evil.example", False),
    ({"Sec-Fetch-Site": "cross-site"}, "cross-site", None, False),
    ({"Sec-Fetch-Site": "none"}, "none", None, False),           # pasted, or opened from an app: as a forwarded address
    ({"Sec-Fetch-Site": "same-origin", "Referer": "https://open.example.test/app/"}, "same-origin",
     "https://open.example.test", False),
    ({"Referer": "https://claude.ai/"}, None, "https://claude.ai", False),
    ({}, None, None, False),
    ({"Sec-Fetch-Site": "elsewhere", "Referer": "not an address"}, None, None, False),
])
def test_the_pane_says_whether_the_request_came_as_a_navigation_from_the_app_s_own_site(client, owner, headers, site,
                                                                                       came_from, from_app):
    """An authorize address forwarded to the owner opens in the owner's own browser, on the owner's own network, so the
    network says nothing of it: the request keeps how the browser said it arrived (Sec-Fetch-Site, the Referer's
    origin), and only a cross-site navigation from the origin the app's sign-in returns to is taken as the app's."""
    r = client.get("/oauth/authorize", params=request_params(client), headers=headers, follow_redirects=False)
    got = client.get(f"/api/connector/requests/{request_id(r)}", headers=owner).json()
    assert (got["fetch_site"], got["came_from"], got["from_app_page"]) == (site, came_from, from_app)


def test_a_request_is_bound_to_the_browser_that_made_it_by_a_cookie_of_its_own(client, owner):
    """A #connect=ID forwarded from the owner's own network, of a request started from Claude's own page, showed no
    warning. The browser that makes a request is given a cookie of its own for it, which carries no
    sign-in; the request keeps the value's sha256; the pane says whether its browser holds it."""
    r = client.get("/oauth/authorize", params=request_params(client),
                   headers={"Sec-Fetch-Site": "cross-site", "Referer": "https://claude.ai/"}, follow_redirects=False)
    rid = request_id(r)
    [set_cookie] = r.headers.get_list("set-cookie")
    name, _, rest = set_cookie.partition("=")
    value, *attributes = [part.strip() for part in rest.split(";")]
    assert name == "__Host-ppc-" + rid and len(value) >= 43
    assert {a.lower() for a in attributes} == {"httponly", "max-age=600", "path=/", "samesite=lax", "secure"}
    [row] = [x for x in rows("ade_connector_requests") if x["id"] == rid]
    assert row["browser"] == hashlib.sha256(value.encode()).hexdigest() and value not in str(row)

    def seen(cookie=None):
        client.cookies.clear()
        headers = dict(owner, **({"Cookie": cookie} if cookie else {}))
        return client.get(f"/api/connector/requests/{rid}", headers=headers).json()
    assert (seen(f"{name}={value}")["same_browser"], seen(f"{name}={value}")["from_app_page"]) == (True, True)
    assert seen()["same_browser"] is False                                         # forwarded: the cookie is not here
    assert seen(f"{name}={value[:-1]}{'A' if value[-1] != 'A' else 'B'}")["same_browser"] is False
    other = request_id(client.get("/oauth/authorize", params=request_params(client), follow_redirects=False))
    assert seen(f"__Host-ppc-{other}={value}")["same_browser"] is False            # another request's cookie


def test_a_forwarded_request_is_told_from_the_owner_s_own_network_too(client, owner):
    """The same network, and a request that came from Claude's own page; only the browser tells it."""
    p = request_params(client)
    came = {"site": "cross-site", "referer": "https://claude.ai/"}
    rid = oauth.authorize(p, "203.0.113.5", came=came, browser=oauth.browser_digest("theirs"))[1].split("#connect=")[1]
    user = {"id": client.get("/api/auth/me", headers=owner).json()["id"], "email": "admin@example.com"}
    forwarded = oauth.request_view(user, rid, viewer="203.0.113.5", browser="mine")
    assert (forwarded["same_network"], forwarded["from_app_page"], forwarded["same_browser"]) == (True, True, False)
    assert oauth.request_view(user, rid, viewer="203.0.113.5")["same_browser"] is False
    assert oauth.request_view(user, rid, viewer="203.0.113.5", browser="theirs")["same_browser"] is True
    unbound = oauth.authorize(p, "203.0.113.5", came=came)[1].split("#connect=")[1]       # kept with no cookie
    assert oauth.request_view(user, unbound, viewer="203.0.113.5", browser="mine")["same_browser"] is None


def test_allow_is_bound_to_the_account_the_pane_showed(client, owner):
    rid = request_id(authorize(client, register(client), pkce()[1]))
    turn_on(client, owner)
    other = signed_up(client, "other@example.com")
    shown = client.get(f"/api/connector/requests/{rid}", headers=owner).json()
    assert shown["account_id"] == client.get("/api/auth/me", headers=owner).json()["id"]
    for account in (client.get("/api/auth/me", headers=other).json()["id"], ""):
        r = allow(client, owner, rid, account=account) if account else client.post(
            f"/api/connector/requests/{rid}/allow", headers=owner,
            json={"propose": True, "confirmation": confirmation(client, owner, f"connector-allow:{rid}:propose")})
        assert r.status_code == 409 and r.json()["detail"] == oauth.SHOWN_ANOTHER
    assert rows("ade_connector_grants") == []
    assert allow(client, owner, rid, account=shown["account_id"]).status_code == 200


def test_the_account_s_switch_takes_its_confirmation_on_and_the_sign_in_off(client, owner):
    r = client.put("/api/connector/account", headers=owner, json={"on": True})
    assert r.status_code == 403 and "Confirm it is you first" in r.json()["detail"]
    assert client.get("/api/connector", headers=owner).json()["account_on"] is False
    turn_on(client, owner)
    assert client.get("/api/connector", headers=owner).json()["account_on"] is True
    r = client.put("/api/connector/account", headers=owner, json={"on": False})
    assert r.json() == {"on": False, "revoked": 0}
    assert client.get("/api/connector", headers=owner).json()["account_on"] is False


def test_allowing_needs_the_account_s_switch_and_its_confirmation(client, owner):
    rid = request_id(authorize(client, register(client), pkce()[1]))
    r = allow(client, owner, rid)
    assert r.status_code == 409 and "does not let apps connect" in r.json()["detail"]
    turn_on(client, owner)
    r = allow(client, owner, rid, confirm=False)
    assert r.status_code == 403 and "Confirm it is you first" in r.json()["detail"]
    assert rows("ade_connector_grants") == [] and rows("ade_holds") == []
    assert allow(client, owner, rid).status_code == 200


def test_allowing_is_bound_to_whether_the_app_may_propose_as_the_pane_showed_it(client, owner):
    """The confirmation names the request and whether the allow lets the app propose (ade.propose), so one made
    while the pane showed "read only" grants no proposing, and the other way, and is left unused."""
    rid = request_id(authorize(client, register(client), pkce()[1], scope="ade.read ade.propose offline_access"))
    turn_on(client, owner)
    me = client.get("/api/auth/me", headers=owner).json()["id"]
    for shown, sent in (("read", True), ("propose", False)):
        word = confirmation(client, owner, f"connector-allow:{rid}:{shown}")
        r = client.post(f"/api/connector/requests/{rid}/allow", headers=owner,
                        json={"propose": sent, "account": me, "confirmation": word})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER, (shown, r.text)
    assert rows("ade_connector_grants") == []
    word = confirmation(client, owner, f"connector-allow:{rid}:read")
    r = client.post(f"/api/connector/requests/{rid}/allow", headers=owner,
                    json={"propose": False, "account": me, "confirmation": word})
    assert r.status_code == 200, r.text
    (grant,) = rows("ade_connector_grants")
    assert "ade.propose" not in grant["scopes"].split()


def test_allowing_makes_a_held_grant_and_a_code_kept_as_its_hash(client, owner):
    got = allowed(client, owner)
    q = query(got["redirect"])
    assert got["redirect"].startswith(CLAUDE + "?") and (q["state"], q["iss"]) == ("st-1", ISSUER)
    assert q["code"].startswith("ppcc_") and len(q["code"]) == 48
    [grant] = rows("ade_connector_grants")
    assert (grant["id"], grant["client_id"], grant["client_name"], grant["redirect_uri"], grant["scopes"],
            grant["resource"]) == (got["grant"], got["client_id"], "Claude", CLAUDE,
                                   "ade.read ade.propose offline_access", "https://open.example.test/mcp")
    assert grant["ends_at"] - grant["allowed_at"] == 90 * 86400 and grant["active_at"] is None
    [hold] = rows("ade_holds")
    assert (hold["kind"], hold["subject"], hold["id"]) == ("connector", grant["id"], grant["hold_id"])
    assert hold["takes_effect_at"] - hold["approved_at"] == 180 == grant["takes_effect_at"] - grant["allowed_at"]
    assert hold["summary"] == oauth.ALLOWED_TITLE and hold["detail"] is None         # its notice: fixed words
    [code] = rows("ade_connector_tokens")
    assert code["kind"] == "code" and code["expires_at"] - code["created_at"] == 60
    assert code["hash"] == oauth.token_hash(q["code"]) and q["code"] not in str(code)
    [request] = rows("ade_connector_requests")
    assert (request["answer"], request["grant_id"]) == ("allowed", grant["id"])
    assert [e["kind"] for e in rows("ade_connector_events")] == ["allowed"]


def test_the_hold_s_setting_and_propose_cleared(client, owner, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "30")
    allowed(client, owner, propose=False)
    [grant] = rows("ade_connector_grants")
    assert grant["scopes"] == "ade.read offline_access" and grant["takes_effect_at"] - grant["allowed_at"] == 30


def _held_notices(monkeypatch):
    """The notices of held connections, (title, body), as the account's devices are sent them: not the switch's."""
    from proofpane_open.ade import push
    told = []
    monkeypatch.setattr(push, "notice", lambda user_id, message, now=None, post=None:
                        message.title != "Letting apps connect was turned on"
                        and told.append((message.title, message.body)) or 1)
    return told


def test_with_no_hold_the_notice_says_it_is_in_effect_and_offers_no_cancel(client, owner, monkeypatch):
    told = _held_notices(monkeypatch)
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    allowed(client, owner)
    [(title, body)] = told
    assert title == "An app was allowed to connect" and body.startswith("Approved at ") and "cancel" not in body
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "180")
    allowed(client, owner)
    assert told[1][0] == "An app's connection waits" and told[1][1].startswith("Takes effect at ")
    assert told[1][1].endswith("unless you cancel it.")


def test_the_notice_says_nothing_of_the_app_and_the_page_names_it(client, owner, monkeypatch):
    """What an app calls itself is its own word, which can be made to read as the ADE's: it goes in no notice, whose
    words are the ADE's own (ade/push.py words), and the page names it as the app's own word."""
    told = _held_notices(monkeypatch)
    rid = request_id(authorize(client, register(client, name="Nothing changed. All is well"), pkce()[1]))
    turn_on(client, owner)
    assert allow(client, owner, rid).status_code == 200
    [(title, body)] = told
    assert title == "An app's connection waits" and "Nothing changed" not in body and "claude.ai" not in body
    assert rows("ade_holds")[0]["summary"] == oauth.ALLOWED_TITLE == "An app was allowed to connect to your ADE"
    assert rows("ade_connector_grants")[0]["client_name"] == "Nothing changed. All is well"


def test_a_request_is_answered_once(client, owner):
    rid = request_id(authorize(client, register(client), pkce()[1]))
    turn_on(client, owner)
    assert allow(client, owner, rid).status_code == 200
    assert allow(client, owner, rid).status_code == 404
    assert client.post(f"/api/connector/requests/{rid}/refuse", headers=owner).status_code == 404


def test_refusing_sends_the_app_access_denied(client, owner):
    rid = request_id(authorize(client, register(client), pkce()[1]))
    r = client.post(f"/api/connector/requests/{rid}/refuse", headers=owner)
    q = query(r.json()["redirect"])
    assert (q["error"], q["state"], q["iss"]) == ("access_denied", "st-1", ISSUER)
    assert rows("ade_connector_requests")[0]["answer"] == "refused"
    turn_on(client, owner)
    assert allow(client, owner, rid).status_code == 404


def test_the_grant_is_the_account_s_that_allowed_it(client, owner):
    other = signed_up(client, "other@example.com")
    rid = request_id(authorize(client, register(client), pkce()[1]))
    turn_on(client, other)
    assert allow(client, other, rid).status_code == 200
    [grant] = rows("ade_connector_grants")
    assert grant["user_id"] == client.get("/api/auth/me", headers=other).json()["id"]


def test_an_expired_request_cannot_be_allowed(client, owner, monkeypatch):
    rid = request_id(authorize(client, register(client), pkce()[1]))
    with store.connect() as db:
        db.execute("UPDATE ade_connector_requests SET expires_at = 1")
    turn_on(client, owner)
    assert allow(client, owner, rid).status_code == 404


def test_the_hold_ending_makes_it_take_effect_and_cancelling_it_revokes_it(client, owner):
    got = allowed(client, owner)
    [grant] = rows("ade_connector_grants")
    holds.settle(now=grant["takes_effect_at"] - 1)
    assert rows("ade_connector_grants")[0]["active_at"] is None
    holds.settle(now=grant["takes_effect_at"])
    assert rows("ade_connector_grants")[0]["active_at"] == grant["takes_effect_at"]
    second = allowed(client, owner)
    r = client.post(f"/api/ade/holds/{second['hold']['id']}/cancel", headers=owner)
    assert r.status_code == 200, r.text
    again = {g["id"]: g for g in rows("ade_connector_grants")}[second["grant"]]
    assert again["revoked_at"] is not None and again["revoked_why"] == "it was cancelled before it took effect"
    assert again["active_at"] is None


def test_turning_the_switch_off_revokes_every_grant_and_its_hold(client, owner):
    allowed(client, owner)
    r = client.put("/api/connector/account", headers=owner, json={"on": False})
    assert r.json() == {"on": False, "revoked": 1}
    [grant] = rows("ade_connector_grants")
    assert grant["revoked_why"] == "apps were turned off for the account"
    assert rows("ade_holds")[0]["cancelled_at"] is not None
    holds.settle(now=grant["takes_effect_at"] + 1)
    assert rows("ade_connector_grants")[0]["active_at"] is None


def test_setup_says_when_the_deployment_s_connector_is_off(client, owner, monkeypatch):
    got = client.get("/api/connector", headers=owner).json()
    assert got["on"] is True and got["url"] == "https://open.example.test/mcp" and got["client_hosts"] == ["claude.ai"]
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR")
    assert client.get("/api/connector", headers=owner).json() == {"on": False}
    assert client.get("/oauth/authorize", follow_redirects=False).status_code == 404
    assert client.put("/api/connector/account", headers=owner, json={"on": False}).status_code == 404
    assert confirmation(client, owner, "connector-on")
