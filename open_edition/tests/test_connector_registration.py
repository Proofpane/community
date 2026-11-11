"""Apps that register themselves with the Claude app connector (oauth.py, RFC 7591): a public client only, with no
secret, whose every redirect address is one the deployment allows, compared whole (a loopback one at any port), sent as
application/json; the unused ones pruned, and at a cap (per network, per wider network, in all) the oldest that can go
pushed out, never one a grant uses or a waiting request names, so registering is not refused for others'."""

import asyncio
import json

import pytest

from proofpane_open import oauth, store

CLAUDE = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR_REDIRECTS", raising=False)


def register(client, **body):
    return client.post("/oauth/register", json=dict({"redirect_uris": [CLAUDE], "client_name": "Claude"}, **body))


def clients():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_connector_clients ORDER BY created_at, rowid")]


def test_a_public_client_is_registered_with_no_secret(client):
    r = register(client)
    assert r.status_code == 201, r.text
    got = r.json()
    assert got["client_id"].startswith("ppcl-") and len(got["client_id"]) == 37
    assert got == {"client_id": got["client_id"], "client_id_issued_at": got["client_id_issued_at"],
                   "client_name": "Claude", "redirect_uris": [CLAUDE], "grant_types": ["authorization_code"],
                   "response_types": ["code"], "token_endpoint_auth_method": "none"}
    assert r.headers["cache-control"] == "no-store"
    [row] = clients()
    assert (row["id"], row["kind"], row["name"], json.loads(row["redirect_uris"])) == \
        (got["client_id"], "registered", "Claude", [CLAUDE])


def test_registration_and_token_requests_do_their_database_work_off_the_event_loop(client, monkeypatch):
    """Both wait for the database's write lock, which another connection may hold for seconds, and
    registering needs no sign-in: on the event loop, one such request stalled every other request meanwhile (a health
    check by 2.6 s, in one run), as /mcp's work, done off the loop, did not."""
    on_loop = {}

    def watched(name, real):
        def run(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                on_loop[name] = True
            except RuntimeError:            # a worker thread has no running loop
                on_loop[name] = False
            return real(*args, **kwargs)
        return run

    monkeypatch.setattr(oauth, "register", watched("register", oauth.register))
    monkeypatch.setattr(oauth, "token", watched("token", oauth.token))
    assert register(client).status_code == 201
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "ppcr_unknown"})
    assert r.status_code == 400                     # refused, by oauth.token itself, in its thread
    assert on_loop == {"register": False, "token": False}


def test_off_it_is_not_there(client, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR")
    assert register(client).status_code == 404
    assert clients() == []


@pytest.mark.parametrize("uri", [CLAUDE, "http://localhost:3118/callback", "http://127.0.0.1:55001/callback",
                                 "http://localhost/callback"])
def test_the_claude_app_s_callback_and_claude_code_s_loopback_at_any_port_are_taken(client, uri):
    r = register(client, redirect_uris=[uri])
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("uri", [
    "https://evil.example/callback",                     # not on the deployment's list
    "http://claude.ai/api/mcp/auth_callback",            # http, not on loopback
    "https://claude.ai/api/mcp/auth_callback#x",         # a fragment
    "https://claude.ai/api/mcp/auth_callback/",          # not the same string
    "https://claude.ai/api/mcp/auth_callback?x=1",
    "HTTPS://CLAUDE.AI/api/mcp/auth_callback",           # compared whole: another spelling is another address
    "https://user@claude.ai/api/mcp/auth_callback",
    "http://localhost:3118/elsewhere",                   # loopback, but another path
    "http://[::1]:3118/callback",                        # a loopback host the list does not name
    "http://localhost.evil.example/callback",
    "https://claude.ai:443/api/mcp/auth_callback",
    "",
])
def test_any_other_redirect_address_is_refused(client, uri):
    r = register(client, redirect_uris=[uri])
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_redirect_uri"
    assert clients() == []


def test_one_address_refused_refuses_the_registration(client):
    r = register(client, redirect_uris=[CLAUDE, "https://evil.example/callback"])
    assert (r.status_code, r.json()["error"]) == (400, "invalid_redirect_uri")


def test_the_deployment_s_list_is_its_setting(client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR_REDIRECTS", " https://app.example/cb , http://example.test/cb ")
    assert register(client, redirect_uris=["https://app.example/cb"]).status_code == 201
    assert register(client, redirect_uris=[CLAUDE]).status_code == 400
    # an entry that cannot be a redirect address at all (http not on loopback) is left out of the list
    assert register(client, redirect_uris=["http://example.test/cb"]).status_code == 400


@pytest.mark.parametrize("body, error", [
    ({"redirect_uris": []}, "invalid_redirect_uri"),
    ({"redirect_uris": [CLAUDE] * 6}, "invalid_redirect_uri"),
    ({"redirect_uris": CLAUDE}, "invalid_redirect_uri"),
    ({"redirect_uris": [7]}, "invalid_redirect_uri"),
    ({"token_endpoint_auth_method": "client_secret_basic"}, "invalid_client_metadata"),
    ({"token_endpoint_auth_method": "client_secret_post"}, "invalid_client_metadata"),
    ({"grant_types": ["implicit"]}, "invalid_client_metadata"),
    ({"grant_types": ["refresh_token"]}, "invalid_client_metadata"),
    ({"grant_types": ["authorization_code", "client_credentials"]}, "invalid_client_metadata"),
    ({"response_types": ["token"]}, "invalid_client_metadata"),
    ({"client_name": "Claude\u202e"}, "invalid_client_metadata"),
    ({"client_name": "x" * 61}, "invalid_client_metadata"),
    ({"client_name": ""}, "invalid_client_metadata"),
])
def test_what_the_deployment_does_not_take_is_refused_naming_why(client, body, error):
    r = register(client, **body)
    assert (r.status_code, r.json()["error"]) == (400, error), r.text
    assert r.json()["error_description"]


# quotation marks, apostrophes and what passes for one, from their code points
QUOTES = ['"', "'", "`"] + [chr(c) for c in (
    0xB4, 0xAB, 0xBB, 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E, 0x2039, 0x2032, 0x2033, 0x2B9, 0x2BA, 0x2BB,
    0x2BC, 0x2EE, 0x5F4, 0x275E, 0x2E42, 0xFF02, 0xFF07, 0xFF40, 0x3003, 0x300C, 0x300F, 0x301E, 0xFE42, 0xFF63,
    # caught by one rule each: initial and final quotation punctuation not named a quotation mark, a geresh, a
    # reversed and a turned comma, and an accent written apart
    0x2E02, 0x2E1D, 0x5F3, 0x2BD, 0x2E41, 0x2E32, 0x2CA)]


@pytest.mark.parametrize("quote", QUOTES)
def test_a_name_holding_a_quotation_mark_or_a_look_alike_is_refused(client, quote):
    """A notice and the pane put the app's name between quotation marks: one inside the name could close them and go on
    as words of the ADE's own."""
    r = register(client, client_name=f"Claude{quote} was refused; nothing changed")
    assert (r.status_code, r.json()["error"]) == (400, "invalid_client_metadata"), r.text
    assert "no quotation mark" in r.json()["error_description"] and clients() == []


@pytest.mark.parametrize("name", ["Claude", "Café & Co. - v2.1 (beta): ñ ü ç ł ß",
                                  "Claude Code", "A*B=C, 50% [ok] {x} <y> / \\ | ~ # @ ! ?",
                                  # two marks on one letter, in Latin and in Thai; Devanagari's signs, from code points
                                  "q" + chr(0x323) + chr(0x307) + " app",
                                  "".join(map(chr, (0xE17, 0xE35, 0xE48, 0xE17, 0xE33))),
                                  "".join(map(chr, (0x939, 0x93F, 0x902, 0x926, 0x940)))])
def test_ordinary_names_are_taken(client, name):
    assert register(client, client_name=name).json()["client_name"] == name


# names an app's words could not hold, from their code points: 54 marks stacked under one letter,
# overlays striking letters through, an enclosing mark, a name of Hangul fillers and one of blank Braille patterns,
# which are drawn as nothing, and a name beginning with a mark, which is drawn on the quotation mark before it (a
# spacing mark too)
DRAWN_OTHERWISE = {
    "stacked": "Claude" + chr(0x359) * 54,
    "overlay": "Cl" + chr(0x336) + "a" + chr(0x336) + "ude",
    "enclosing": "Cl" + chr(0x489) + "aude",
    "filler": chr(0x3164) * 20,
    "braille": chr(0x2800) * 20,
    "mark first": chr(0x301) + "Claude",
    "spacing mark first": chr(0x903) + "Claude",
}


@pytest.mark.parametrize("name", DRAWN_OTHERWISE.values(), ids=DRAWN_OTHERWISE.keys())
def test_a_name_not_drawn_as_itself_is_refused(client, name):
    """The pane draws an app's name right above its warnings, and the signing review right above the words being
    signed: a stack of marks is drawn over the lines around it, and a name drawn as nothing reads as nothing. The
    name is held to what the app's words are held to."""
    r = register(client, client_name=name)
    assert (r.status_code, r.json()["error"]) == (400, "invalid_client_metadata"), r.text
    assert "no character that is not shown as itself" in r.json()["error_description"] and clients() == []


def test_two_marks_on_a_letter_are_taken_and_a_third_is_not(client):
    """The line is where an app's words draw it (link_commands.MARKS_MAX)."""
    two = "Claude q" + chr(0x323) + chr(0x307)
    assert register(client, client_name=two).json()["client_name"] == two
    assert register(client, client_name=two + chr(0x330)).status_code == 400


def test_a_refresh_grant_may_be_asked_for_and_a_nameless_client_is_an_app(client):
    r = client.post("/oauth/register", json={"redirect_uris": [CLAUDE],
                                             "grant_types": ["refresh_token", "authorization_code"]})
    assert r.status_code == 201
    assert (r.json()["grant_types"], r.json()["client_name"]) == (["authorization_code", "refresh_token"], "An app")


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b"null"])
def test_a_body_that_is_not_a_json_object_is_refused(client, body):
    r = client.post("/oauth/register", content=body, headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()["error"]) == (400, "invalid_client_metadata")


@pytest.mark.parametrize("path, kind", [("/oauth/register", "application/json"),
                                        ("/oauth/token", "application/x-www-form-urlencoded"),
                                        ("/oauth/anything", "text/plain")])
def test_the_app_reads_at_most_64_kb_of_an_oauth_request(client, path, kind):
    """Caddy holds the OAuth routes to 64 KB; the app holds them to it as well, for a deployment run without Caddy."""
    assert client.post(path, content=b"x" * (oauth.BODY_MAX + 1), headers={"Content-Type": kind}).status_code == 413
    assert client.post(path, content=b"x" * oauth.BODY_MAX, headers={"Content-Type": kind}).status_code != 413
    assert oauth.BODY_MAX == 64 * 1024


def test_a_route_of_the_app_s_own_still_takes_its_own_size(client):
    r = client.post("/api/auth/login", content=b'{"email": "a@b.c", "password": "' + b"x" * 100_000 + b'"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code != 413


@pytest.mark.parametrize("kind", [None, "text/plain", "application/x-www-form-urlencoded",
                                  "multipart/form-data; boundary=x", "application/jsonx"])
def test_a_registration_is_sent_as_application_json(client, kind):
    """A page of another site can make its visitors' browsers send a body of any words without asking first only as
    text/plain, a form or multipart: a registration is application/json, which makes the browser ask first."""
    headers = {} if kind is None else {"Content-Type": kind}
    r = client.post("/oauth/register", content=json.dumps({"redirect_uris": [CLAUDE]}), headers=headers)
    assert (r.status_code, r.json()["error"]) == (400, "invalid_client_metadata")
    assert clients() == []
    r = client.post("/oauth/register", content=json.dumps({"redirect_uris": [CLAUDE]}),
                    headers={"Content-Type": "Application/JSON; charset=utf-8"})
    assert r.status_code == 201, r.text


def test_a_page_of_another_site_is_refused_when_its_browser_asks_first(client):
    r = client.options("/oauth/register", headers={"Origin": "https://elsewhere.example",
                                                   "Access-Control-Request-Method": "POST",
                                                   "Access-Control-Request-Headers": "content-type"})
    assert "access-control-allow-origin" not in r.headers


NOW = 1_000_000


def reg(network, now):
    return oauth.register({"redirect_uris": [CLAUDE]}, client=network, now=now)["client_id"]


def use(client_id):
    """A grant uses the client."""
    with store.connect() as db:
        db.execute("INSERT INTO ade_connector_grants (id, user_id, client_id, client_name, redirect_uri, scopes, "
                   "resource, allowed_at, ends_at) VALUES (?, 'u', ?, 'Claude', ?, 'ade.read', 'r', 1, 2)",
                   ("g-" + client_id, client_id, CLAUDE))


def wait_on(client_id, now):
    """A request to allow the client, made at `now` and waiting until ten minutes later."""
    with store.connect() as db:
        db.execute("INSERT INTO ade_connector_requests (id, client_id, redirect_uri, code_challenge, scopes, asked_net, "
                   "created_at, expires_at) VALUES (?, ?, ?, 'x', 'ade.read', 'n', ?, ?)",
                   ("r-" + client_id, client_id, CLAUDE, now, now + 600))


def test_at_a_network_s_cap_its_own_oldest_that_can_go_makes_room(monkeypatch):
    """The Claude app may register from one network for every user, so a network's registrations past its cap push
    out its own oldest that no app uses and no waiting request names, and nobody's registration is refused for them."""
    monkeypatch.setattr(oauth, "REGISTRATIONS_PER_NETWORK", 3)
    claude = "160.79.104.10"
    used, midway = reg(claude, NOW), reg(claude, NOW + 1)
    use(used)                                             # connected: not counted, never pushed out
    wait_on(midway, NOW + 1)                              # midway through being allowed: counted, not pushed out
    later = [reg(claude, NOW + 2 + n) for n in range(4)]
    assert {c["id"] for c in clients()} == {used, midway, *later[2:]}
    elsewhere = reg("198.51.100.7", NOW + 9)                # another network's own
    assert elsewhere in {c["id"] for c in clients()} and len(clients()) == 5


def test_a_registration_is_refused_only_when_none_of_its_network_s_can_go(monkeypatch):
    monkeypatch.setattr(oauth, "REGISTRATIONS_PER_NETWORK", 2)
    for n in range(2):
        wait_on(reg("203.0.113.5", NOW + n), NOW + n)
    with pytest.raises(oauth.OAuthError) as e:
        reg("203.0.113.5", NOW + 2)
    assert e.value.status == 429 and "midway through being allowed" in e.value.description
    newer = reg("203.0.113.5", NOW + 601)                 # the first request has ended: its app can go
    assert len(clients()) == 2 and newer in {c["id"] for c in clients()}


def test_a_request_answered_names_its_app_no_longer(monkeypatch):
    """A request names its app only until it is answered: once refused, before its ten minutes are over, its app can go
    at a cap as any app no connection uses can."""
    monkeypatch.setattr(oauth, "REGISTRATIONS_PER_NETWORK", 1)
    first = reg("203.0.113.5", NOW)
    wait_on(first, NOW)
    with store.connect() as db:
        db.execute("UPDATE ade_connector_requests SET answer = 'refused', answered_at = ? WHERE client_id = ?",
                   (NOW + 1, first))
    second = reg("203.0.113.5", NOW + 2)
    assert [c["id"] for c in clients()] == [second]


def test_a_wider_network_s_registrations_are_held_as_its_own_network_s_are(monkeypatch):
    monkeypatch.setattr(oauth, "REGISTRATIONS_PER_WIDE_NETWORK", 3)
    ids = [reg(f"203.0.113.{n}", NOW + n) for n in range(4)]             # one /24, an address each
    assert [c["id"] for c in clients()] == ids[1:]
    assert {c["wide"] for c in clients()} == {"203.0.113.0/24"}
    v6 = [reg(f"2001:db8:0:{n:x}::5", NOW + 10 + n) for n in range(4)]   # one /48, a /64 each
    assert [c["id"] for c in clients()] == ids[1:] + v6[1:]


def test_at_the_cap_in_all_the_oldest_of_the_wider_network_that_has_the_most_goes(monkeypatch):
    monkeypatch.setattr(oauth, "UNUSED_REGISTRATIONS", 4)
    mine = reg("192.0.2.9", NOW)                                          # the oldest, from a network of its own
    flood = [reg(f"198.51.100.{n}", NOW + 1 + n) for n in range(3)]       # three, from one /24
    newest = reg("203.0.113.5", NOW + 10)
    assert {c["id"] for c in clients()} == {mine, *flood[1:], newest}


def test_an_unused_registration_goes_after_a_day():
    old = reg("203.0.113.5", NOW)
    kept = reg("203.0.113.5", NOW)
    use(kept)
    newer = reg("203.0.113.6", NOW + 86400)
    assert {c["id"] for c in clients()} == {kept, newer} and old
