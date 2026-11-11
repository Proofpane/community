"""The Claude app connector's switch and its metadata (oauth.py, ade/connector_routes.py): off, none of it answers; on,
the two documents say where each endpoint is, from the public address alone, never from the request."""

import pytest

from proofpane_open import oauth

ROUTES = ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource",
          "/.well-known/oauth-authorization-server")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")


def test_off_none_of_it_answers(client, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR", raising=False)
    assert [client.get(path).status_code for path in ROUTES] == [404, 404, 404]
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "yes")          # only "on" turns it on
    assert [client.get(path).status_code for path in ROUTES] == [404, 404, 404]


def test_the_resource_metadata_names_mcp_and_this_ade_as_its_only_server(client, on):
    for path in ROUTES[:2]:
        r = client.get(path)
        assert r.status_code == 200
        assert r.json() == {"resource": "https://open.example.test/mcp",
                            "authorization_servers": ["https://open.example.test"],
                            "scopes_supported": ["ade.read", "ade.propose"], "bearer_methods_supported": ["header"],
                            "resource_name": "Proofpane Community ADE"}


def test_the_server_metadata_offers_documents_registration_and_s256_only(client, on):
    m = client.get(ROUTES[2]).json()
    assert m == {"issuer": "https://open.example.test",
                 "authorization_endpoint": "https://open.example.test/oauth/authorize",
                 "token_endpoint": "https://open.example.test/oauth/token",
                 "registration_endpoint": "https://open.example.test/oauth/register",
                 "scopes_supported": ["ade.read", "ade.propose", "offline_access"],
                 "response_types_supported": ["code"], "response_modes_supported": ["query"],
                 "grant_types_supported": ["authorization_code", "refresh_token"],
                 "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"],
                 "client_id_metadata_document_supported": True, "authorization_response_iss_parameter_supported": True}


def test_the_addresses_come_from_the_public_address_whatever_the_request_says(client, on, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "https://ADE.Example.test:443/")
    m = client.get(ROUTES[2], headers={"Host": "elsewhere.example"}).json()
    assert m["issuer"] == "https://ade.example.test"
    assert m["token_endpoint"] == "https://ade.example.test/oauth/token"
    assert client.get(ROUTES[0]).json()["resource"] == "https://ade.example.test/mcp"
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "https://ade.example.test:8443")
    assert client.get(ROUTES[0]).json()["authorization_servers"] == ["https://ade.example.test:8443"]


def test_there_is_no_openid_document(client, on):
    r = client.get("/.well-known/openid-configuration")
    assert r.status_code == 404


def test_the_app_refuses_to_start_with_it_on_at_an_address_that_is_not_https(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "http://ade.example.test")
    with pytest.raises(RuntimeError, match="must be https"):
        oauth.check_config()
    for url in ("https://ade.example.test", "http://127.0.0.1:8000", "http://localhost:3000", "http://[::1]:8000"):
        monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", url)
        oauth.check_config()
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "http://ade.example.test")
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR")
    oauth.check_config()                                             # off: nothing to refuse


@pytest.mark.parametrize("given, want", [
    ("https://open.example.test/mcp", "https://open.example.test/mcp"),
    ("HTTPS://Open.Example.TEST/mcp", "https://open.example.test/mcp"),
    ("https://open.example.test:443/mcp/", "https://open.example.test/mcp"),
    ("https://open.example.test:8443/mcp", "https://open.example.test:8443/mcp"),
    ("http://127.0.0.1:80/mcp", "http://127.0.0.1/mcp"),
    ("https://open.example.test/MCP", "https://open.example.test/MCP"),          # a path's case is its own
    ("https://open.example.test/mcp#x", None), ("https://open.example.test/mcp#", None),
    ("https://open.example.test/mcp?x=1", None), ("https://user@open.example.test/mcp", None),
    ("open.example.test/mcp", None), ("ftp://open.example.test/mcp", None), ("", None), (None, None),
    ("https://open.example.test:99999/mcp", None),
])
def test_a_resource_is_compared_in_its_canonical_form(given, want):
    assert oauth.canonical(given) == want
