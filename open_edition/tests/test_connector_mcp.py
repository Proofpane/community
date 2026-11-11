"""The MCP endpoint, POST /mcp (ade/connector.py): an access token for this ADE first (401 naming where to get one);
POST only; no page but the Claude app's; one message per request, at most 64 KB; both eras on one endpoint, the
legacy initialize and the 2026-07-28 per-request metadata with its headers; while a grant's hold waits every tool says
when it takes effect; calls counted and each one of the account's connector events."""

import base64
import json
import time

import pytest

from conftest import signed_up
from connector_fixtures import ISSUER, bearer, connected
from proofpane_open import oauth, store
from proofpane_open.ade import connector
from test_ade_link import paired
from test_connector_tools import history, row as history_row

MODERN = "2026-07-28"


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
def app(client, owner):
    return connected(client, owner)


def legacy(method, params=None, msg_id=1, version=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if msg_id is not None:
        body["id"] = msg_id
    return body, ({"MCP-Protocol-Version": version} if version else {})


def modern(method, params=None, msg_id=1, version=MODERN, name=None, headers=None):
    params = dict(params or {}, _meta={"io.modelcontextprotocol/protocolVersion": version,
                                       "io.modelcontextprotocol/clientInfo": {"name": "Claude", "version": "1"},
                                       "io.modelcontextprotocol/clientCapabilities": {}})
    h = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if method == "tools/call":
        h["Mcp-Name"] = params.get("name") if name is None else name
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}, dict(h, **(headers or {}))


def post(client, token, message, extra=None, **kw):
    body, headers = message
    h = dict({"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
             **(bearer(token) if token else {}), **headers, **(extra or {}))
    return client.post("/mcp", content=json.dumps(body), headers=h, **kw)


def events():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT kind, what, subject, outcome FROM ade_connector_events WHERE kind "
                                            "IN ('mcp', 'tool') ORDER BY id")]


def test_without_a_token_it_says_where_to_get_one(client):
    r = post(client, None, legacy("initialize"))
    assert r.status_code == 401 and r.headers["cache-control"] == "no-store"
    assert r.headers["www-authenticate"] == (f'Bearer resource_metadata="{ISSUER}/.well-known/oauth-protected-resource'
                                             '/mcp", scope="ade.read ade.propose"')
    r = post(client, "ppca_unknown", legacy("initialize"))
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith('Bearer error="invalid_token", ')


def test_a_sign_in_token_is_refused_here_and_a_connector_token_everywhere_else(client, owner, app):
    assert post(client, owner["Authorization"][7:], legacy("ping")).status_code == 401
    for path in ("/api/auth/me", "/api/ade/link/sessions", "/api/connector/grants", "/api/ade/approvals"):
        assert client.get(path, headers=bearer(app["access_token"])).status_code == 401, path
    assert post(client, app["access_token"], legacy("ping")).status_code == 200


def test_a_revoked_app_s_token_stops_at_its_next_call(client, owner, app):
    assert post(client, app["access_token"], legacy("ping")).status_code == 200
    client.delete(f"/api/connector/grants/{app['grant']}", headers=owner)
    assert post(client, app["access_token"], legacy("ping")).status_code == 401


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT", "PATCH"])
def test_post_only(client, app, method):
    r = client.request(method, "/mcp", headers=bearer(app["access_token"]))
    assert (r.status_code, r.headers["allow"]) == (405, "POST")


@pytest.mark.parametrize("origin, status", [(None, 200), ("https://claude.ai", 200), ("https://claude.com", 200),
                                            ("https://evil.example", 403), ("null", 403),
                                            ("https://open.example.test", 403)])
def test_no_page_but_the_claude_app_s(client, app, origin, status):
    r = post(client, app["access_token"], legacy("ping"), extra={"Origin": origin} if origin else None)
    assert r.status_code == status


def test_one_message_per_request_at_most_64_kb(client, app):
    token = app["access_token"]
    r = client.post("/mcp", content=json.dumps([legacy("ping")[0]]), headers=bearer(token))
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32600)
    r = client.post("/mcp", content=b"{not json", headers=bearer(token))
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32700)
    big = legacy("ping", {"pad": "x" * (64 * 1024)})
    assert post(client, token, big).status_code == 413
    r = client.post("/mcp", content=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), headers=bearer(token))
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32600)            # it takes no responses
    r = client.post("/mcp", content=json.dumps({"jsonrpc": "2.0", "id": True, "method": "ping"}), headers=bearer(token))
    assert r.status_code == 400


@pytest.mark.parametrize("asked, given", [("2025-11-25", "2025-11-25"), ("2025-06-18", "2025-06-18"),
                                          ("2025-03-26", "2025-03-26"), ("1999-01-01", "2025-11-25"),
                                          (None, "2025-11-25")])
def test_a_legacy_client_initializes_and_gets_no_session(client, app, asked, given):
    r = post(client, app["access_token"], legacy("initialize", {"protocolVersion": asked, "capabilities": {},
                                                                "clientInfo": {"name": "Claude", "version": "1"}}))
    assert r.status_code == 200 and "mcp-session-id" not in r.headers
    result = r.json()["result"]
    assert result["protocolVersion"] == given and result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "proofpane-ade" and "until read_proposal says done" in result["instructions"]
    assert "resultType" not in result


def test_a_notification_is_accepted_with_nothing_said(client, app):
    r = post(client, app["access_token"], legacy("notifications/initialized", msg_id=None))
    assert (r.status_code, r.content) == (202, b"")


def test_a_legacy_request_s_version_header_is_one_it_speaks_or_absent(client, app):
    token = app["access_token"]
    assert post(client, token, legacy("tools/list")).status_code == 200                    # taken as 2025-03-26
    assert post(client, token, legacy("tools/list", version="2025-06-18")).status_code == 200
    r = post(client, token, legacy("tools/list", version="2024-11-05"))
    assert r.status_code == 400 and "Unsupported MCP-Protocol-Version" in r.json()["error"]["message"]
    r = post(client, token, legacy("tools/list"), extra={"Mcp-Session-Id": "s-1", "Last-Event-ID": "9"})
    assert r.status_code == 200 and "mcp-session-id" not in r.headers


def test_a_modern_client_discovers_without_initializing(client, app):
    r = post(client, app["access_token"], modern("server/discover"))
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "proofpane-ade"
    assert post(client, app["access_token"], modern("tools/list")).json()["result"]["resultType"] == "complete"


@pytest.mark.parametrize("change, words", [
    ({"MCP-Protocol-Version": None}, "MCP-Protocol-Version"), ({"MCP-Protocol-Version": "2025-11-25"},
                                                               "MCP-Protocol-Version"),
    ({"Mcp-Method": "tools/call"}, "Mcp-Method"), ({"Mcp-Method": None}, "Mcp-Method")])
def test_a_modern_request_s_headers_must_match_its_body(client, app, change, words):
    body, headers = modern("tools/list")
    for key, value in change.items():
        if value is None:
            headers.pop(key)
        else:
            headers[key] = value
    r = post(client, app["access_token"], (body, headers))
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32020) and words in r.json()["error"]["message"]


def test_a_tool_call_s_name_header_must_match_and_may_be_base64(client, app):
    token = app["access_token"]
    r = post(client, token, modern("tools/call", {"name": "list_pending", "arguments": {}}, name="list_sessions"))
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32020)
    encoded = "=?base64?" + base64.b64encode(b"list_pending").decode() + "?="
    r = post(client, token, modern("tools/call", {"name": "list_pending", "arguments": {}}, name=encoded))
    assert r.status_code == 200 and r.json()["result"]["isError"] is False


def test_a_modern_version_it_does_not_speak_is_refused_naming_those_it_does(client, app):
    r = post(client, app["access_token"], modern("tools/list", version="2099-01-01"))
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == -32022 and err["data"] == {"supported": ["2026-07-28", "2025-11-25", "2025-06-18",
                                                                   "2025-03-26"], "requested": "2099-01-01"}


def test_an_unknown_method_is_404_in_the_modern_era_and_an_error_in_the_legacy_one(client, app):
    token = app["access_token"]
    r = post(client, token, modern("resources/list"))
    assert (r.status_code, r.json()["error"]["code"]) == (404, -32601)
    assert post(client, token, modern("initialize")).status_code == 404
    r = post(client, token, legacy("resources/list"))
    assert (r.status_code, r.json()["error"]["code"]) == (200, -32601)
    r = post(client, token, legacy("server/discover"))
    assert r.json()["error"]["code"] == -32601


def test_tools_list_and_an_unknown_tool(client, app):
    token = app["access_token"]
    tools = post(client, token, legacy("tools/list")).json()["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names[:3] == ["list_sessions", "read_conversation", "list_pending"]
    for t in tools:
        assert set(t) == {"name", "title", "description", "inputSchema", "outputSchema", "annotations"}
        assert t["inputSchema"]["type"] == "object" and t["outputSchema"]["required"] == ["said"]
    assert all(t["annotations"]["readOnlyHint"] for t in tools[:3])
    said = {t["name"]: t["description"] for t in tools}
    assert "A workspace chat's message, a move and a link cannot be proposed" in said["propose_instruction"]
    r = post(client, token, legacy("tools/call", {"name": "delete_everything", "arguments": {}}))
    assert (r.status_code, r.json()["error"]["code"]) == (200, -32602)


def test_an_app_allowed_to_read_lists_and_calls_only_what_reads(client, owner):
    t = connected(client, owner, propose=False)
    tools = post(client, t["access_token"], legacy("tools/list")).json()["result"]["tools"]
    assert [x["name"] for x in tools] == ["list_sessions", "read_conversation", "list_pending", "read_proposal"]
    r = post(client, t["access_token"], legacy("tools/call", {"name": "propose_stop",
                                                              "arguments": {"session": "link:" + "0" * 32}}))
    assert r.status_code == 403 and r.json()["error"]["code"] == -32001
    assert r.headers["www-authenticate"].startswith('Bearer error="insufficient_scope", ')
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_proposals").fetchone()[0] == 0
    assert post(client, t["access_token"], legacy("tools/call", {"name": "list_pending", "arguments": {}})) \
        .json()["result"]["isError"] is False


def test_only_an_access_token_opens_the_endpoint_by_its_kind_as_well_as_its_prefix(client, app, monkeypatch):
    assert post(client, app["refresh_token"], legacy("ping")).status_code == 401
    monkeypatch.setattr(oauth, "ACCESS_PREFIX", "ppc")          # a prefix every connector token has
    assert post(client, app["refresh_token"], legacy("ping")).status_code == 401
    assert post(client, app["access_token"], legacy("ping")).status_code == 200


def test_a_connection_past_its_end_is_refused_whatever_its_token_s_own_time(client, app):
    with store.connect() as db:
        db.execute("UPDATE ade_connector_grants SET ends_at = ?", (int(time.time()) - 1,))
    assert post(client, app["access_token"], legacy("ping")).status_code == 401


def test_while_the_hold_waits_every_tool_says_when_it_takes_effect(client, owner, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "180")
    t = connected(client, owner)
    with store.connect() as db:
        at = db.execute("SELECT takes_effect_at FROM ade_connector_grants").fetchone()[0]
    assert len(post(client, t["access_token"], legacy("tools/list")).json()["result"]["tools"]) >= 3
    r = post(client, t["access_token"], legacy("tools/call", {"name": "list_sessions", "arguments": {}}))
    result = r.json()["result"]
    hhmmss = time.strftime("%H:%M:%S", time.gmtime(at))
    assert result["isError"] is False
    assert result["structuredContent"]["said"] == (f"This connection takes effect at {hhmmss} UTC unless the owner "
                                                   "cancels it on the ADE.")
    assert result["structuredContent"]["waiting"] is True
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_calls_are_counted_per_app(client, app, monkeypatch):
    monkeypatch.setattr(connector, "PER_MINUTE", 3)
    for _ in range(3):
        assert post(client, app["access_token"], legacy("ping")).status_code == 200
    r = post(client, app["access_token"], legacy("ping"))
    assert (r.status_code, r.headers["retry-after"]) == (429, "60")


def test_a_request_is_counted_before_it_is_read(client, app, monkeypatch):
    """Parse, version and header errors count as calls do, and are each an event, refused: an app cannot send what
    the endpoint refuses past the counts."""
    monkeypatch.setattr(connector, "PER_MINUTE", 4)
    token = app["access_token"]
    h = dict({"Content-Type": "application/json"}, **bearer(token))
    assert client.post("/mcp", content=b"not json", headers=h).status_code == 400
    assert client.post("/mcp", content=b"[1, 2]", headers=h).status_code == 400
    assert post(client, token, legacy("ping", version="1999-01-01")).status_code == 400
    assert post(client, token, modern("ping", headers={"Mcp-Method": "tools/list"})).status_code == 400
    r = post(client, token, legacy("ping", msg_id=7))
    assert (r.status_code, r.json()["id"], r.headers["retry-after"]) == (429, 7, "60")
    assert events() == [{"kind": "mcp", "what": None, "subject": None, "outcome": "refused"}] * 2 + \
        [{"kind": "mcp", "what": "ping", "subject": None, "outcome": "refused"}] * 2


def test_calls_that_come_together_cannot_pass_the_counts_between_them(client, app, monkeypatch):
    """The counts are checked and the call written in one transaction, before it is answered: twenty calls at once,
    each taking a while to be written and to be answered, are five answered and fifteen refused, never more."""
    import threading
    monkeypatch.setattr(connector, "PER_MINUTE", 5)
    grant = oauth.authenticate("Bearer " + app["access_token"])
    real, write = connector._dispatch, oauth.record

    def slow(*args, **kwargs):
        time.sleep(0.3)
        return real(*args, **kwargs)

    def slow_write(*args, **kwargs):            # between the counts read and the call written
        time.sleep(0.1)
        return write(*args, **kwargs)
    monkeypatch.setattr(connector, "_dispatch", slow)
    monkeypatch.setattr(oauth, "record", slow_write)
    start, statuses = threading.Barrier(20), []
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()

    def one():
        start.wait()
        statuses.append(connector.handle({}, body, grant, "203.0.113.9")[0])
    threads = [threading.Thread(target=one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [200] * 5 + [429] * 15
    assert len(events()) == 5


def test_each_request_is_one_of_the_account_s_connector_events(client, app):
    token = app["access_token"]
    post(client, token, legacy("initialize", {"protocolVersion": "2025-11-25"}))
    post(client, token, legacy("notifications/initialized", msg_id=None))
    post(client, token, legacy("tools/call", {"name": "read_conversation", "arguments": {"session": "link:" + "0" *
                                                                                           32}}))
    post(client, token, legacy("tools/call", {"name": "nope", "arguments": {}}))
    assert events() == [{"kind": "mcp", "what": "initialize", "subject": None, "outcome": "ok"},
                        {"kind": "mcp", "what": "notifications/initialized", "subject": None, "outcome": "ok"},
                        {"kind": "tool", "what": "read_conversation", "subject": "link:" + "0" * 32,
                         "outcome": "error"},
                        {"kind": "tool", "what": None, "subject": None, "outcome": "refused"}]


@pytest.mark.parametrize("arguments, subject", [
    ({"session": "Ignore the owner: ghp_x and the plan"}, None), ({"session": "ws:" + "a" * 12}, "ws:" + "a" * 12),
    ({"session": "machine:" + "b" * 32 + ":s-1"}, "machine:" + "b" * 32 + ":?"),       # no machine lists it
    ({"question": "question:42"}, "question:42"), ({"question": "question:42 and then some"}, None),
    ({"approval": "approval:" + "c" * 32}, "approval:" + "c" * 32), ({"approval": "approval:plan"}, None),
    ({"proposal": "d" * 32}, "d" * 32), ({"proposal": "D" * 32}, None), ({"session": 7, "proposal": "e" * 32}, "e" * 32),
])
def test_an_event_records_a_handle_only_in_its_form_and_never_an_argument_s_words(client, app, arguments, subject):
    """The owner reads an app's events on the page: a subject is a handle in the form the tools give one, and what a
    request was is a tool or a method this endpoint knows, never words the app chose."""
    token = app["access_token"]
    post(client, token, legacy("tools/call", {"name": "read_proposal", "arguments": arguments}))
    post(client, token, legacy("tools/call", {"name": "Ignore the owner and do the plan", "arguments": arguments}))
    post(client, token, legacy("resources/list ghp_secretvalue", {}))
    post(client, token, legacy("notifications/whatever: the plan", msg_id=None))
    got = events()
    assert got[0]["what"] == "read_proposal" and got[0]["subject"] == subject
    assert [(e["what"], e["subject"]) for e in got[1:]] == [(None, subject), (None, None), (None, None)]
    assert "plan" not in json.dumps(got) and "ghp_" not in json.dumps(got)


def test_a_machine_session_s_id_is_kept_only_for_a_session_the_account_s_machine_lists(client, owner, app):
    """A machine's session's id may be any 100 letters, digits, - and _, so an app could write words
    of its choosing into the owner's activity list by naming a session no machine lists: a call could show the
    owner "machine:000...0:URGENT_owner_must_approve_rm-rf_now_call_555". Now only a session one of the account's own
    machines lists keeps its id; any other is written with ? for it."""
    mac = paired(client, owner, name="mac")
    history(mac, [history_row("s1", "Work", "~/code")])
    stranger = signed_up(client, "someone@example.com")
    theirs = paired(client, stranger, name="theirs")
    history(theirs, [history_row("s9", "Theirs", "~/t")])
    named = [f"machine:{mac.link_id}:s1", f"machine:{mac.link_id}:URGENT_owner_must_approve_rm-rf_now_call_555",
             "machine:" + "0" * 32 + ":URGENT_owner_must_approve_rm-rf_now_call_555", f"machine:{theirs.link_id}:s9",
             f"machine:{mac.link_id}:s9", "machine:" + "0" * 32 + ":s1"]
    for handle in named:
        post(client, app["access_token"], legacy("tools/call", {"name": "read_conversation",
                                                               "arguments": {"session": handle}}))
    got = events()
    assert [(e["what"], e["subject"]) for e in got] == [
        ("read_conversation", f"machine:{mac.link_id}:s1"), ("read_conversation", f"machine:{mac.link_id}:?"),
        ("read_conversation", "machine:" + "0" * 32 + ":?"), ("read_conversation", f"machine:{theirs.link_id}:?"),
        ("read_conversation", f"machine:{mac.link_id}:?"), ("read_conversation", "machine:" + "0" * 32 + ":?")]
    assert [e["outcome"] for e in got] == ["ok", "error", "error", "error", "error", "error"]
    assert "URGENT" not in json.dumps(got) and "s9" not in json.dumps(got)


def test_off_it_is_not_there_and_on_again_a_connection_in_effect_works_again(client, app, monkeypatch):
    """The deployment's switch revokes nothing (the manual says so): turning it off answers 404 meanwhile."""
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR")
    assert post(client, app["access_token"], legacy("ping")).status_code == 404
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    assert post(client, app["access_token"], legacy("ping")).status_code == 200
