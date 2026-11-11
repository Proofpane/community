"""The deployment's own Caddyfile in front of the app, for the Claude app connector: Caddy refuses a body over 64 KB to
the MCP endpoint and to the OAuth routes (the app reads them whole), passes a smaller one to the app, and the metadata
documents reach the app at their well-known paths. And the PROXY protocol header, which names the client every count
by network is kept by, is taken only from a private IPv4 address or loopback, where Fly's proxy connects from: from
any other address it is ignored. Caddy is run as test_ade_link_caddy.py runs it; skipped without a Caddy binary
(PP_TEST_CADDY, or caddy on the PATH)."""

import ipaddress
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request

import pytest

from fly_fixtures import _wait_port, caddy_binary, free_port
from test_ade_link_caddy import ROOT, _start_through, app_port, caddy, post      # noqa: F401 (fixtures)

pytestmark = pytest.mark.skipif(caddy_binary() is None, reason="no Caddy binary (PP_TEST_CADDY or caddy on the PATH)")


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")


def get(port, path):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(f"https://localhost:{port}{path}", timeout=60, context=context) as r:
        return r.status, json.loads(r.read())


def test_caddy_caps_the_connector_s_bodies_at_64_kb(caddy):
    assert post(caddy, "/mcp", 65 * 1000) == 413
    assert post(caddy, "/mcp", 60 * 1000) == 401                     # the app's answer: no access token
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert post(caddy, "/oauth/token", 65 * 1000, headers=form) == 413
    assert post(caddy, "/oauth/token", 60 * 1000, headers=form) == 400   # the app's answer: no grant_type in it
    assert post(caddy, "/oauth/register", 65 * 1000) == 413


def test_the_cap_is_on_the_endpoint_and_the_oauth_routes_alone(own_home):
    env = {"PATH": os.environ["PATH"], "HOME": own_home, "OPEN_EDITION_DOMAIN": "localhost:8443"}
    adapted = json.loads(subprocess.run([caddy_binary(), "adapt", "--config", os.path.join(ROOT, "Caddyfile"),
                                         "--adapter", "caddyfile"], env=env, capture_output=True, text=True,
                                        check=True).stdout)
    caps = [(r["match"], r["handle"][0]["max_size"])
            for r in adapted["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0]["routes"]
            if r["handle"][0]["handler"] == "request_body"]
    assert [c for c in caps if any("/mcp" in m.get("path", []) for m in c[0])] == \
        [([{"path": ["/mcp", "/oauth/*"]}], 64000)]


def test_the_metadata_documents_reach_the_app_through_caddy(caddy):
    status, resource = get(caddy, "/.well-known/oauth-protected-resource/mcp")
    assert status == 200 and resource["resource"] == "https://open.example.test/mcp"
    status, server = get(caddy, "/.well-known/oauth-authorization-server")
    assert status == 200 and server["issuer"] == "https://open.example.test"


def adapted(home, domain="localhost:8443"):
    env = {"PATH": os.environ["PATH"], "HOME": home, "OPEN_EDITION_DOMAIN": domain}
    return json.loads(subprocess.run([caddy_binary(), "adapt", "--config", os.path.join(ROOT, "Caddyfile"),
                                      "--adapter", "caddyfile"], env=env, capture_output=True, text=True,
                                     check=True).stdout)


def proxy_protocol(config):
    [wrapper] = [w for w in config["apps"]["http"]["servers"]["srv0"]["listener_wrappers"]
                 if w["wrapper"] == "proxy_protocol"]
    return wrapper


def test_a_proxy_header_is_taken_only_from_a_private_ipv4_address_or_loopback(own_home):
    """Fly's proxy reaches the machine at its private IPv4 address (Fly's documentation), from an address the
    documentation does not give: every private IPv4 range, and loopback for this machine's own, and no IPv6 range, over
    which the organisation's other machines reach this one."""
    wrapper = proxy_protocol(adapted(own_home))
    assert wrapper["allow"] == ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]
    assert all(ipaddress.ip_network(n).version == 4 and ipaddress.ip_network(n).is_private for n in wrapper["allow"])
    # from anywhere else a header is ignored: Caddy's default, which the Caddyfile names
    assert wrapper.get("fallback_policy", "IGNORE") == "IGNORE"
    with open(os.path.join(ROOT, "Caddyfile"), encoding="utf-8") as fh:
        assert "fallback_policy ignore" in fh.read()


@pytest.fixture
def caddy_trusting_another_network(app_port, tmp_path, own_home):
    """The deployment's Caddyfile with only 10.0.0.0/8 allowed, so this machine's own connection, from 127.0.0.1, comes
    from an address it does not allow: its port."""
    port, http_port = free_port(), free_port()
    config = json.loads(json.dumps(adapted(own_home, f"localhost:{port}")).replace(
        '"dial": "127.0.0.1:8000"', f'"dial": "127.0.0.1:{app_port}"'))
    proxy_protocol(config)["allow"] = ["10.0.0.0/8"]
    config["admin"] = {"disabled": True}
    config["apps"]["http"]["http_port"], config["apps"]["http"]["https_port"] = http_port, port
    config["apps"]["pki"] = {"certificate_authorities": {"local": {"install_trust": False}}}
    path = tmp_path / "caddy.json"
    path.write_text(json.dumps(config))
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path),
           "XDG_CONFIG_HOME": str(tmp_path)}
    proc = subprocess.Popen([caddy_binary(), "run", "--config", str(path)], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    try:
        _wait_port(port, proc, "Caddy")
        deadline = time.time() + 30
        while True:
            try:
                post(port, "/api/health", 0)
                break
            except urllib.error.URLError:
                assert time.time() < deadline, "Caddy served no certificate for localhost"
                time.sleep(0.2)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_a_header_from_an_address_not_allowed_is_ignored_and_the_connection_s_own_kept(caddy_trusting_another_network):
    port = caddy_trusting_another_network
    assert _start_through(port, "PROXY TCP4 203.0.113.7 10.0.0.1 51000 443\r\n") == "127.0.0.1"
    assert _start_through(port) == "127.0.0.1"
