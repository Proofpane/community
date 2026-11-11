"""The deployment's own Caddyfile, adapted by Caddy and run on this computer in front of the app. The session channel's
body cap is Caddy's (the app reads a body whole before its handler runs): it refuses a 65 KB body to a link route,
passes a smaller one, and leaves other routes alone; a move's transcript and bundle, which their routes stream, pass up
to 32 MiB; a link's PUT of its History list or of a conversation, which grow once sealed (end-to-end encryption), up to
2 MiB. And the client's address reaches the app: the one a PROXY
protocol header names (Fly's proxy sends one, fly.toml), never one the client claims, and a connection without a
header is served too. Test-only changes to the adapted configuration: the app's port as the upstream, local ports, no
admin endpoint, and no certificate authority installed into this computer's trust store. Skipped without a Caddy
binary (PP_TEST_CADDY, or caddy on the PATH)."""

import json
import os
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from fly_fixtures import _wait_port, caddy_binary, free_port
from test_ade_link_commands import device, owner  # noqa: F401  fixtures
from test_ade_link_moves import workspace  # noqa: F401  fixture

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))

pytestmark = pytest.mark.skipif(caddy_binary() is None, reason="no Caddy binary (PP_TEST_CADDY or caddy on the PATH)")


@pytest.fixture
def app_port(client):
    import uvicorn
    from proofpane_open.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    yield server.servers[0].sockets[0].getsockname()[1]
    server.should_exit = True
    thread.join(10)


def _start_caddy(app_port, tmp_path):
    """Caddy with the image's Caddyfile on two free ports, the app's port as its upstream: (port, process)."""
    port, http_port = free_port(), free_port()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path),
           "XDG_CONFIG_HOME": str(tmp_path), "OPEN_EDITION_DOMAIN": f"localhost:{port}"}
    adapted = subprocess.run([caddy_binary(), "adapt", "--config", os.path.join(ROOT, "Caddyfile"), "--adapter",
                              "caddyfile"], env=env, capture_output=True, text=True, check=True).stdout
    config = json.loads(adapted)
    text = json.dumps(config)
    assert text.count('"dial": "127.0.0.1:8000"') == 1
    config = json.loads(text.replace('"dial": "127.0.0.1:8000"', f'"dial": "127.0.0.1:{app_port}"'))
    config["admin"] = {"disabled": True}
    config["apps"]["http"]["http_port"] = http_port
    config["apps"]["http"]["https_port"] = port
    config["apps"]["pki"] = {"certificate_authorities": {"local": {"install_trust": False}}}
    path = tmp_path / "caddy.json"
    path.write_text(json.dumps(config))
    return port, subprocess.Popen([caddy_binary(), "run", "--config", str(path)], env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE)


@pytest.fixture
def caddy(app_port, tmp_path):
    for attempt in range(3):
        port, proc = _start_caddy(app_port, tmp_path)
        try:
            _wait_port(port, proc, "Caddy")
            break
        except RuntimeError as e:
            # a port free when chosen, taken before Caddy listened on it (it listens on every address): two others
            if "address already in use" not in str(e) or attempt == 2:
                raise
    try:
        deadline = time.time() + 30          # the local authority issues localhost's certificate after the listen
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


def post(port, path, size, method="POST", headers=None, data=None):
    """`size` bytes (or `data`) through Caddy: the answer's status."""
    # Caddy's own local authority signed the certificate; what is checked here is the cap, not the chain
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"https://localhost:{port}{path}", data=b"x" * size if data is None else data,
                                 method=method, headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=60, context=context) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_caddy_refuses_a_65_kb_body_to_a_link_route_and_nothing_else(caddy):
    assert post(caddy, "/api/ade/link/events", 65 * 1000) == 413
    assert post(caddy, "/api/ade/link/pair", 65 * 1000) == 413
    assert post(caddy, "/api/ade/link/events", 60 * 1000) == 401        # the app's answer: it is not signed
    assert post(caddy, "/api/auth/login", 65 * 1000) not in (413, 502)   # another route is the app's to judge


def test_caddy_lets_a_move_s_files_through_to_32_mib_and_no_other_link_route_past_64_kb(caddy, client, owner, device,
                                                                                        workspace, monkeypatch,
                                                                                        own_home):
    """A move's transcript and bundle (proofpane_open/ade/moves.py) are streamed by their routes and are each at most
    32 MiB, in Caddy's cap and the app's alike; everything else under the channel stays at 64 KB. Each file goes
    through Caddy at the whole 32 MiB, signed as the link's client signs it, so the app reads it to its last byte and
    keeps it: an answer the app gives before it reads a body (a refusal) would show nothing of Caddy's cap."""
    from proofpane_open.ade import moves
    from test_ade_link_moves import _signed_headers, move_row, taken_move
    monkeypatch.setenv(moves.HOLD_SETTING, "0")         # the move stored at once: its hold is tested apart
    s, seq = taken_move(client, owner, device)
    for what in ("transcript", "bundle"):
        signed = _signed_headers(s, "PUT", f"/moves/{seq}/{what}", b"x" * moves.FILE_MAX)
        assert post(caddy, f"/api/ade/link/moves/{seq}/{what}", moves.FILE_MAX, "PUT", signed) == 200
    row = move_row(seq)
    assert (row["transcript_size"], row["bundle_size"]) == (moves.FILE_MAX, moves.FILE_MAX)
    # past 64 KB, every other route under the channel is refused by Caddy, the request signed or not
    signed = _signed_headers(s, "POST", f"/moves/{seq}/done", b"x" * 65000)
    assert post(caddy, f"/api/ade/link/moves/{seq}/done", 65000, headers=signed) == 413
    assert post(caddy, f"/api/ade/link/moves/{seq}/transcript/x", 65000, "PUT", signed) == 413
    env = {"PATH": os.environ["PATH"], "HOME": own_home, "OPEN_EDITION_DOMAIN": "localhost:8443"}
    adapted = json.loads(subprocess.run([caddy_binary(), "adapt", "--config", os.path.join(ROOT, "Caddyfile"),
                                         "--adapter", "caddyfile"], env=env, capture_output=True, text=True,
                                        check=True).stdout)
    caps = {json.dumps(r["match"], sort_keys=True): r["handle"][0]["max_size"]
            for r in adapted["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0]["routes"]
            if r["handle"][0]["handler"] == "request_body"}
    files = {"path": ["/api/ade/link/moves/*/transcript", "/api/ade/link/moves/*/bundle"]}
    history = {"method": ["PUT"], "path": ["/api/ade/link/history", "/api/ade/link/history/talk"]}
    statements = {"method": ["POST"], "path": ["/api/ade/link/e2e"]}
    assert caps == {json.dumps([files], sort_keys=True): moves.FILE_MAX,
                    json.dumps([history], sort_keys=True): 2 * 1024 * 1024,
                    json.dumps([statements], sort_keys=True): 256 * 1024,
                    json.dumps([{"path": ["/api/ade/link/*"], "not": [files, history, statements]}],
                               sort_keys=True): 64000,
                    # the second screen's (test_caddy_refuses_a_65_kb_body_to_a_second_screen_route)
                    json.dumps([{"path": SECOND_SCREEN}], sort_keys=True): 64000,
                    # the Claude app connector's (test_connector_caddy.py)
                    json.dumps([{"path": ["/mcp", "/oauth/*"]}], sort_keys=True): 64000}


SECOND_SCREEN = ["/api/ade/signer/*", "/api/ade/signers", "/api/ade/signers/*", "/api/ade/sign-requests",
                 "/api/ade/sign-requests/*"]


def test_caddy_refuses_a_65_kb_body_to_a_second_screen_route(caddy):
    """A step parked for a signer, an assertion a signer hands back and a step a signer composed are small, and the app
    reads each whole (proofpane_open/ade/signers.py): Caddy refuses one past 64 KB, on the page's routes and the
    signer's alike."""
    for path in ("/api/ade/sign-requests", "/api/ade/signer/requests/x", "/api/ade/signer/pair",
                 "/api/ade/signer/commands", "/api/ade/signers/tickets"):
        assert post(caddy, path, 65 * 1000) == 413, path
    assert post(caddy, "/api/ade/sign-requests", 60 * 1000) == 401       # the app's answer: no sign-in
    assert post(caddy, "/api/ade/signer/commands", 60 * 1000) == 401     # the app's answer: not signed


def test_caddy_lets_a_link_s_history_list_and_conversation_through_to_2_mib_and_nothing_else(caddy, client):
    """Sealed (end-to-end encryption, proofpane_open/ade/e2e.py), a History list and a conversation grow by a third: a
    link's PUT of either passes Caddy up to 2 MiB, and the app reads as much (main.MAX_BODY_HISTORY); another method on
    those paths, and every other link route, stays at 64 KB."""
    import hashlib
    from conftest import signed_up
    from test_ade_link import paired
    s = paired(client, signed_up(client))
    kid = hashlib.sha256(s.key.public_key().public_bytes_raw()).hexdigest()[:16]
    named = {"Authorization": f"ADE-Link key_id={kid}, ts={int(time.time())}, sig={'A' * 86}"}
    for path in ("/api/ade/link/history", "/api/ade/link/history/talk"):
        assert post(caddy, path, 1536 * 1024, "PUT", named) == 401, path     # through Caddy and read whole: the app's
        assert post(caddy, path, 2 * 1024 * 1024 + 1, "PUT", named) == 413, path
        assert post(caddy, path, 65 * 1000, "POST", named) == 413, path
    assert post(caddy, "/api/ade/link/history/talk/x", 65 * 1000, "PUT", named) == 413


def test_caddy_lets_the_statements_a_link_sends_in_through_to_256_kib_and_nothing_else(caddy, client, owner):
    """After a restore, a link sends in the account's statements the server lacks, up to 50 at once
    (proofpane_open/ade/e2e_keys.py, UPLOAD_MAX), each with its passkey's answer: 50 signed with an RSA key, the largest
    answer, pass Caddy and are taken, twice over within its cap of 256 KiB; past it, Caddy refuses; another method on
    that path stays at 64 KB. The RSA key turned encryption on first, so it unlocks the keys: a statement sent in counts
    only if such a passkey signed it (UPLOAD_STAYING, 403 otherwise)."""
    from proofpane_open.ade import e2e_keys
    from test_ade_e2e import Keys
    from test_ade_link import paired
    from test_ade_link_moves import _signed_headers
    from test_passkeys import Authenticator, add
    rsa = Authenticator(alg=-257)
    assert add(client, owner, rsa).status_code == 200
    keys = Keys(client, owner, rsa)
    keys.turn_on()                      # statement 1 on the server; the 50 sent in are the 50 after it
    statements = []
    for _ in range(e2e_keys.UPLOAD_MAX):
        signed, private = keys.statement(retires="all")
        keys.took(signed, private)
        statements.append({"payload": keys.statements[-1].decode(), "credential": signed["credential"]})
    body = json.dumps({"statements": statements}).encode()
    assert 2 * len(body) < 256 * 1024, len(body)
    s = paired(client, owner)
    assert post(caddy, "/api/ade/link/e2e", 0, headers=_signed_headers(s, "POST", "/e2e", body), data=body) == 200
    assert client.get("/api/ade/e2e", headers=owner).json()["pending"]["change"] == "upload"   # all 50, waiting
    named = {"Authorization": f"ADE-Link key_id={'0' * 16}, ts={int(time.time())}, sig={'A' * 86}"}
    assert post(caddy, "/api/ade/link/e2e", 256 * 1024, headers=named) == 401      # through Caddy: the app's answer
    assert post(caddy, "/api/ade/link/e2e", 256 * 1024 + 1, headers=named) == 413
    assert post(caddy, "/api/ade/link/e2e", 65 * 1000, "PUT", named) == 413


def _start_through(port, proxy_line=None, forwarded=None):
    """POST /api/auth/device/start through Caddy, the connection opened with `proxy_line` (a PROXY protocol header, as
    Fly's proxy sends one) before TLS, if given: the address the app saw, as it answers."""
    import socket
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection(("127.0.0.1", port), timeout=15)
    if proxy_line:
        raw.sendall(proxy_line.encode("ascii"))
    with context.wrap_socket(raw, server_hostname="localhost") as tls:
        body = b'{"name": "Phone"}'
        extra = f"X-Forwarded-For: {forwarded}\r\n" if forwarded else ""
        tls.sendall((f"POST /api/auth/device/start HTTP/1.1\r\nHost: localhost:{port}\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(body)}\r\n{extra}Connection: close\r\n\r\n").encode("ascii") + body)
        answer = b""
        while chunk := tls.recv(65536):
            answer += chunk
    head, _, rest = answer.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200"), head[:200]
    if b"chunked" in head.lower():
        size, _, rest = rest.partition(b"\r\n")
        rest = rest[:int(size, 16)]
    return json.loads(rest)["from"]


def test_the_app_sees_the_client_s_address_that_fly_s_proxy_names(caddy):
    # with the PROXY protocol header Fly's proxy puts first (fly.toml, proxy_proto), the client's address
    assert _start_through(caddy, "PROXY TCP4 203.0.113.7 10.0.0.1 51000 443\r\n") == "203.0.113.7"
    assert _start_through(caddy, "PROXY TCP6 2001:db8::7 2001:db8::1 51000 443\r\n") == "2001:db8::7"
    # a client's own X-Forwarded-For is not believed
    assert _start_through(caddy, "PROXY TCP4 203.0.113.7 10.0.0.1 51000 443\r\n", forwarded="198.51.100.66") == "203.0.113.7"
    # a connection without the header (as Fly's TCP check makes) is served, with the address it came from
    assert _start_through(caddy) == "127.0.0.1"
