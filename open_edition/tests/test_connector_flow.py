"""The Claude app connector end to end: the app served on a local port, and a stand-in for the Claude app
(mcp_client_stand_in.py, standard library only) doing what Claude does over HTTP, as a proxy in front of the app would
carry it (the stand-in names the ADE by its public address and reaches the local port). The owner's part is the page's,
through its routes: read the request with the sign-in, turn the account's switch on and allow with the password, sign
a proposal with the tests' software passkey, revoke."""

import threading
import time

import pytest

from conftest import signed_up
from connector_fixtures import ISSUER, RESOURCE, allow, turn_on
from mcp_client_stand_in import CALLBACK, MODERN, StandIn
from proofpane_open import oauth
from proofpane_open.ade import connector, holds
from test_ade_link import paired
from test_ade_link_commands import payload, send, take
from test_passkeys import Authenticator, add


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    oauth._failed.clear()
    connector._ws_cache.clear()


@pytest.fixture
def served(client):
    """The app on a local port: its address. Its public address stays the tests' own (https://open.example.test)."""
    import uvicorn
    from proofpane_open.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True
    thread.join(10)


@pytest.fixture
def owner(client):
    return signed_up(client)


def connect(client, owner, claude):
    """Claude connects, and the owner allows it on the page: the allow's answer. The pane's read goes over the port,
    from this machine's address, as the authorization did: the same network."""
    claude.discover()
    claude.register()
    consent = claude.authorize()
    assert consent.startswith(ISSUER + "/app/#connect="), consent
    rid = consent.split("#connect=", 1)[1]
    r = claude.request("GET", f"{ISSUER}/api/connector/requests/{rid}", headers={"Authorization": owner["Authorization"]})
    assert r.status == 200, r.body
    view = r.json()
    assert (view["client_name"], view["returns_to"], view["loopback"]) == ("Claude", "claude.ai", False)
    assert (view["asked_from"], view["viewer"], view["same_network"]) == ("127.0.0.1", "127.0.0.1", True)
    turn_on(client, owner)
    r = allow(client, owner, rid)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["redirect"].startswith(CALLBACK + "?")
    tokens = claude.callback(got["redirect"])
    assert tokens["token_type"] == "Bearer" and set(tokens["scope"].split()) == {"ade.read", "ade.propose",
                                                                                 "offline_access"}
    return got


def test_claude_connects_reads_proposes_and_is_revoked(client, owner, served):
    device = Authenticator()
    assert add(client, owner, device).status_code == 200
    mac = paired(client, owner, name="mac")
    claude = StandIn(RESOURCE, served)
    assert 'scope="ade.read ade.propose"' in claude.discover()
    got = connect(client, owner, claude)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]

    # until the connection's hold ends, the tools are listed and each says when it takes effect
    assert claude.initialize()["protocolVersion"] == "2025-11-25"
    tools = [t["name"] for t in claude.post("tools/list").json()["result"]["tools"]]
    assert {"list_sessions", "read_proposal", "propose_instruction"} <= set(tools)
    waiting = claude.tool("list_sessions")
    assert waiting["isError"] is False and waiting["structuredContent"]["waiting"] is True
    assert waiting["structuredContent"]["said"].startswith("This connection takes effect at ")
    holds.settle(user_id, now=got["hold"]["takes_effect_at"])

    # then it reads, in either era of the protocol
    listed = claude.tool("list_sessions")["structuredContent"]
    assert [(s["id"], s["name"]) for s in listed["links"]] == [("link:" + mac.link_id, "mac")]
    discovered = claude.post("server/discover", modern=True)
    assert discovered.status == 200 and MODERN in discovered.json()["result"]["supportedVersions"]
    assert claude.tool("list_sessions", modern=True)["structuredContent"]["links"] == listed["links"]

    # what it proposes waits for the owner, who signs it with a passkey, the payload's id the proposal's
    proposed = claude.tool("propose_instruction", session="link:" + mac.link_id, text="run the tests",
                           why="the build is red")["structuredContent"]
    pid = proposed["proposal"]["id"]
    assert proposed["done"] is False
    waiting = client.get("/api/connector/proposals", headers=owner).json()["proposals"]
    assert [(p["id"], p["app"], p["why"]) for p in waiting] == [(pid, "Claude", "the build is red")]
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id, "run the tests", id=pid))
    assert r.status_code == 200, r.text
    seq = r.json()["seq"]
    read = claude.tool("read_proposal", proposal=pid)["structuredContent"]
    assert read["done"] is True and read["proposal"]["seq"] == seq
    assert take(mac, seq).status_code == 200

    events = client.get(f"/api/connector/grants/{got['grant']}/events", headers=owner).json()["events"]
    assert {e["net"] for e in events if e["kind"] in ("mcp", "tool")} == {"127.0.0.1"}

    # Revoke on the page: the next call is refused, naming where to connect again, and so is the refresh token
    assert client.delete(f"/api/connector/grants/{got['grant']}", headers=owner).status_code == 200
    r = claude.post("tools/list")
    assert r.status == 401 and r.headers["WWW-Authenticate"].startswith('Bearer error="invalid_token", '
                                                                        'resource_metadata="')
    assert claude.refresh().status == 400

    # connected again: a refresh rotates the tokens, and the old refresh token used again ends the connection, the
    # new tokens of the refresh too
    got = connect(client, owner, claude)
    holds.settle(user_id, now=got["hold"]["takes_effect_at"])
    old = claude.tokens["refresh_token"]
    assert claude.refresh().status == 200
    assert claude.tool("list_sessions")["isError"] is False
    again = claude.refresh(old)
    assert again.status == 400 and again.json()["error"] == "invalid_grant"
    assert claude.post("tools/list").status == 401
    [app] = [a for a in client.get("/api/connector/grants", headers=owner).json()["grants"] if a["id"] == got["grant"]]
    assert (app["state"], app["revoked_why"]) == ("revoked", "a refresh token was used twice")


def test_a_refusal_goes_back_to_claude_and_no_token_is_given(client, owner, served):
    claude = StandIn(RESOURCE, served)
    claude.discover()
    claude.register()
    rid = claude.authorize().split("#connect=", 1)[1]
    r = client.post(f"/api/connector/requests/{rid}/refuse", headers=owner)
    assert r.status_code == 200
    back = claude.callback(r.json()["redirect"])
    assert back["error"] == "access_denied" and "code" not in back


def test_the_endpoint_answers_only_post_and_no_page_s_origin(client, owner, served):
    claude = StandIn(RESOURCE, served)
    connect(client, owner, claude)
    for method in ("GET", "DELETE"):
        r = claude.request(method, RESOURCE, headers={"Authorization": "Bearer " + claude.tokens["access_token"]})
        assert r.status == 405 and r.headers["Allow"] == "POST"
    r = claude.request("POST", RESOURCE, b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
                       {"Content-Type": "application/json", "Origin": "https://evil.example",
                        "Authorization": "Bearer " + claude.tokens["access_token"]})
    assert r.status == 403
