"""How the approval hook (open_edition/ade_hook.py) reaches the ADE: never through a proxy the environment names, nor
trusting certificates SSL_CERT_FILE or SSL_CERT_DIR name, since a settings file's `env`, a repository's own among them,
can set those, and through its own proxy and certificate authority a repository could answer for the ADE, an approval
included. Only PROOFPANE_ADE_PROXY and PROOFPANE_ADE_CA_FILE choose a proxy and certificates, and the approval block's
`env` sets both (empty unless the owner fills them). Against servers of the test's own on this machine: an ADE over
TLS with a certificate a test authority issued, the same ADE over plain HTTP, and a proxy that tunnels CONNECT. And how
long a run may take: against an ADE that leaves the request pending, reached by a name whose
lookup a stand-in makes slow, the run ends in a denial within its deadline; and, run as the hook runs, in a process of
its own, so do runs whose token file or window file is a pipe no one writes to."""

import datetime
import http.server
import ipaddress
import json
import os
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest

import ade_hook

ENV_NAMES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
             "SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE", "REQUEST_METHOD")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # the machine running the tests may set proxy and certificate variables of its own: each test sets what it names
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """A test certificate authority and a server certificate it issued for 127.0.0.1, as files."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    folder = tmp_path_factory.mktemp("pki")
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Proofpane test authority")])

    def usage(**on):
        names = ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment", "key_agreement",
                 "key_cert_sign", "crl_sign", "encipher_only", "decipher_only")
        return x509.KeyUsage(**{n: on.get(n, False) for n in names})

    def built(subject, public, ca):
        b = (x509.CertificateBuilder().subject_name(subject).issuer_name(ca_name).public_key(public)
             .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
             .not_valid_after(now + datetime.timedelta(days=1))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False))
        if ca:
            return b.add_extension(usage(key_cert_sign=True, crl_sign=True), critical=True)
        return (b.add_extension(usage(digital_signature=True), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                               critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                               critical=False))

    ca = built(ca_name, ca_key.public_key(), True).sign(ca_key, hashes.SHA256())
    cert = built(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]), key.public_key(),
                 False).sign(ca_key, hashes.SHA256())
    paths = SimpleNamespace(ca=str(folder / "ca.pem"), cert=str(folder / "server.pem"), key=str(folder / "server.key"))
    (folder / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (folder / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                          serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    return paths


class _Ade(http.server.BaseHTTPRequestHandler):
    """The ADE's hook route, as far as these tests need it: every request recorded, every answer an approval."""

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.seen.append((self.command, self.path, self.headers.get("Authorization")))
        self.server.hosts.append(self.headers.get("Host"))     # the site a server such as Caddy routes by
        body = json.dumps({"id": "r1", "state": "approved", "words": "by the test's own ADE"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *args):
        pass


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def ade(pki):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ade)
    server.seen, server.hosts = [], []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pki.cert, pki.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    _serve(server)
    server.hostport = f"127.0.0.1:{server.server_address[1]}"
    server.url = "https://" + server.hostport
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def plain_ade():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ade)
    server.seen, server.hosts = [], []
    _serve(server)
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()


class _Tunnel(socketserver.BaseRequestHandler):
    """A proxy's CONNECT: the target recorded with the request's head, then the bytes passed both ways."""

    def handle(self):
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 65536:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            head += chunk
        text, rest = head.split(b"\r\n\r\n", 1)
        self.server.seen.append(text.decode("latin-1"))
        method, target = text.decode("latin-1").split(" ", 2)[:2]
        if method != "CONNECT":
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        host, port = target.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=10) as upstream:
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            ends = [self.request, upstream]
            while True:
                readable, _, _ = select.select(ends, [], [], 10)
                if not readable:
                    return
                for end in readable:
                    data = end.recv(65536)
                    if not data:
                        return
                    (upstream if end is self.request else self.request).sendall(data)


@pytest.fixture
def proxy():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Tunnel)
    server.daemon_threads = True
    server.seen = []
    _serve(server)
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    server.targets = lambda: [h.split(" ", 2)[1] for h in server.seen if h.startswith("CONNECT ")]
    yield server
    server.shutdown()
    server.server_close()


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s1", "cwd": "/w"}


def _decision(out):
    return out["hookSpecificOutput"]["permissionDecision"], out["hookSpecificOutput"]["permissionDecisionReason"]


def test_no_proxy_the_environment_names_is_used(monkeypatch, pki, ade, proxy):
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, proxy.url)
    # the control: urllib's own opener goes through the test's proxy, so the variables are in effect here
    trusting = ssl.create_default_context(cafile=pki.ca)
    with urllib.request.build_opener(urllib.request.HTTPSHandler(context=trusting)).open(
            ade.url + "/control", timeout=10) as answer:
        answer.read()
    assert proxy.targets() == [ade.hostport]
    env = {"PROOFPANE_ADE_URL": ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x", "PROOFPANE_ADE_CA_FILE": pki.ca}
    decision, _ = _decision(ade_hook.decide(bash("git push origin main"), env))
    assert decision == "allow"
    assert proxy.targets() == [ade.hostport]                    # the control's alone
    assert ade.seen[-1] == ("POST", "/api/ade/hook/requests", "Bearer adeh_x")


def test_certificates_ssl_cert_file_names_are_not_trusted_and_proofpane_ade_ca_file_s_are(monkeypatch, pki, ade):
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    # the control: the interpreter's own default context trusts the test's server through SSL_CERT_FILE
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(
            context=ssl.create_default_context())).open(ade.url + "/control", timeout=10) as answer:
        answer.read()
    env = {"PROOFPANE_ADE_URL": ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x"}
    asked = len(ade.seen)
    decision, reason = _decision(ade_hook.decide(bash("git push origin main"), env))
    assert decision == "deny" and "could not be asked" in reason
    assert len(ade.seen) == asked                               # nothing reached it: the certificate was refused first
    decision, _ = _decision(ade_hook.decide(bash("git push origin main"), dict(env, PROOFPANE_ADE_CA_FILE=pki.ca)))
    assert decision == "allow" and ade.seen[-1][:2] == ("POST", "/api/ade/hook/requests")


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl to name the authority's file by its hash")
def test_certificates_ssl_cert_dir_holds_are_not_trusted(monkeypatch, tmp_path, pki, ade):
    digest = subprocess.run(["openssl", "x509", "-hash", "-noout", "-in", pki.ca], capture_output=True, text=True,
                            check=True).stdout.strip()
    folder = tmp_path / "certs"
    folder.mkdir()
    shutil.copy(pki.ca, folder / f"{digest}.0")
    monkeypatch.setenv("SSL_CERT_DIR", str(folder))
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(
            context=ssl.create_default_context())).open(ade.url + "/control", timeout=10) as answer:
        answer.read()                                           # the control: in effect here
    decision, reason = _decision(ade_hook.decide(bash("git push origin main"),
                                                 {"PROOFPANE_ADE_URL": ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x"}))
    assert decision == "deny" and "could not be asked" in reason


def test_the_proxy_proofpane_ade_proxy_names_is_used_and_sees_only_a_tunnel(monkeypatch, pki, ade, proxy):
    # NO_PROXY, which urllib would read to go round a proxy, is the environment's too, and goes unread
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    env = {"PROOFPANE_ADE_URL": ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x", "PROOFPANE_ADE_CA_FILE": pki.ca,
           "PROOFPANE_ADE_PROXY": proxy.url}
    decision, _ = _decision(ade_hook.decide(bash("git push origin main"), env))
    assert decision == "allow"
    assert proxy.targets() == [ade.hostport]
    assert not any("adeh_x" in head or "hook/requests" in head for head in proxy.seen)   # the request, tunnelled
    assert ade.seen[-1] == ("POST", "/api/ade/hook/requests", "Bearer adeh_x")
    # the request through the tunnel names the ADE, not the proxy, as its Host: a server that routes by it (the
    # deployment's Caddy) answered one naming the proxy with an empty 200, and the hook could not read it
    assert ade.hosts == [ade.hostport], ade.hosts


def test_an_ade_on_this_machine_over_plain_http_is_reached_without_the_proxy(plain_ade, proxy):
    env = {"PROOFPANE_ADE_URL": plain_ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x", "PROOFPANE_ADE_PROXY": proxy.url}
    decision, _ = _decision(ade_hook.decide(bash("git push origin main"), env))
    assert decision == "allow" and proxy.seen == []
    assert plain_ade.seen[-1] == ("POST", "/api/ade/hook/requests", "Bearer adeh_x")


def test_left_empty_the_certificates_are_those_where_the_interpreter_s_openssl_looks(monkeypatch, tmp_path, pki, ade):
    # openssl_cafile and openssl_capath, which the interpreter was built with: here the test authority's file stands
    # in for the first, and cafile, the place the environment chose, is left out
    nowhere = str(tmp_path / "nowhere")
    built = ssl.DefaultVerifyPaths(None, None, "SSL_CERT_FILE", pki.ca, "SSL_CERT_DIR", nowhere)
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: built)
    env = {"PROOFPANE_ADE_URL": ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x", "PROOFPANE_ADE_CA_FILE": ""}
    assert _decision(ade_hook.decide(bash("git push origin main"), env))[0] == "allow"
    # neither place there: no certificate is trusted, and the gated command is denied
    none = ssl.DefaultVerifyPaths(None, None, "SSL_CERT_FILE", nowhere, "SSL_CERT_DIR", nowhere)
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: none)
    decision, reason = _decision(ade_hook.decide(bash("git push origin main"), env))
    assert decision == "deny" and "could not be asked" in reason


def test_a_certificate_file_that_cannot_be_read_denies(ade):
    env = {"PROOFPANE_ADE_URL": ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x", "PROOFPANE_ADE_CA_FILE": "/no/such/ca.pem"}
    decision, reason = _decision(ade_hook.decide(bash("git push origin main"), env))
    assert decision == "deny" and "could not be asked (FileNotFoundError)" in reason and ade.seen == []


class _Pending(http.server.BaseHTTPRequestHandler):
    """An ADE that leaves every request pending, an hour from now: every request recorded."""

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.seen.append((self.command, self.path))
        body = json.dumps({"id": "r1", "state": "pending", "until": int(time.time()) + 3600}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def pending_ade():
    """The ADE above over plain HTTP on this machine, named `localhost`, so that each request looks its name up."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pending)
    server.seen = []
    _serve(server)
    server.url = f"http://localhost:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def slow_lookups(monkeypatch):
    """socket.getaddrinfo as one whose lookups, from the one `after` names on (0: every one), each take up to 6 seconds
    before they answer as the real one does: what a name lookup that does not answer does to a request, which no
    socket timeout bounds. At the test's end every lookup still waiting fails at once, so no request is left behind."""
    real, released, count = socket.getaddrinfo, threading.Event(), [0]
    state = SimpleNamespace(after=0)

    def lookup(*args, **kwargs):
        count[0] += 1
        if count[0] > state.after and released.wait(6.0):
            raise socket.gaierror(socket.EAI_AGAIN, "ended by the test")
        return real(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    yield state
    released.set()


def _timed(env):
    """The hook's decision for a gated command, with its real _call, clock and pause, and how long it took."""
    began = time.monotonic()
    decision, reason = _decision(ade_hook.decide(bash("git push origin main"), env))
    return decision, reason, time.monotonic() - began


def _late(seconds):
    """The words of the denial past the deadline, for a deadline of `seconds`."""
    return f"the hook had no answer within {seconds} seconds of its start"


def test_a_name_lookup_that_does_not_answer_ends_in_a_denial_at_the_run_s_deadline(monkeypatch, pending_ade,
                                                                                  slow_lookups):
    """The deadline made short for the test (1.5 seconds): the first request's own lookup takes longer, and the hook
    answers at the deadline, counted from its start, with the denial that says so, the request never sent. (Not
    "within": the denial comes at the deadline, just past it.)"""
    monkeypatch.setattr(ade_hook, "DEADLINE", 1.5)
    decision, reason, took = _timed({"PROOFPANE_ADE_URL": pending_ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x"})
    assert decision == "deny" and _late("1.5") in reason, reason
    assert 1.5 <= took < 2.5, took
    assert pending_ade.seen == []


def test_a_poll_whose_lookup_does_not_answer_ends_in_a_denial_at_the_run_s_deadline(monkeypatch, pending_ade,
                                                                                   slow_lookups):
    """The request is sent, the ADE leaves it pending, and the poll's lookup takes longer than the time left: the hook
    answers at the deadline (2 seconds here, with a pause and a request timeout short enough for a poll to fit)."""
    monkeypatch.setattr(ade_hook, "DEADLINE", 2.0)
    monkeypatch.setattr(ade_hook, "POLL", 0.1)
    monkeypatch.setattr(ade_hook, "HTTP_TIMEOUT", 0.5)
    slow_lookups.after = 1                                   # the first lookup answers at once, every later one is slow
    decision, reason, took = _timed({"PROOFPANE_ADE_URL": pending_ade.url, "PROOFPANE_ADE_TOKEN": "adeh_x"})
    assert decision == "deny" and _late("2") in reason, reason
    assert 2.0 <= took < 3.0, took
    assert pending_ade.seen == [("POST", "/api/ade/hook/requests")]


def test_main_counts_the_run_s_deadline_from_the_start_it_is_given(monkeypatch, pending_ade):
    """main gives decide the start it is given (the __main__ block gives STARTED, the hook's start, as the last test here
    shows): given one a whole deadline back, it denies, saying so, and sends nothing. Given none, it counts from its own
    call, not from STARTED, the time this file was imported: here STARTED is set a whole deadline back, as a long
    in-process run would leave it, and the run still asks the ADE."""
    import io
    for name in ("PROOFPANE_ADE_GATE", "PROOFPANE_ADE_WINDOWS", "PROOFPANE_ADE_PROXY", "PROOFPANE_ADE_CA_FILE",
                 "PROOFPANE_ADE_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROOFPANE_ADE_URL", pending_ade.url)
    monkeypatch.setenv("PROOFPANE_ADE_TOKEN", "adeh_x")
    monkeypatch.setenv("PROOFPANE_ADE_WAIT", "0")
    monkeypatch.setattr(ade_hook, "STARTED", time.monotonic() - ade_hook.DEADLINE)

    def main_with(*started):
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(bash("git push")).encode())))
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        assert ade_hook.main(*started) == 0
        return _decision(json.loads(out.getvalue()))

    decision, reason = main_with(time.monotonic() - ade_hook.DEADLINE)
    assert decision == "deny" and _late("75") in reason, reason
    assert pending_ade.seen == []
    decision, reason = main_with()
    assert decision == "deny" and "no answer yet" in reason and pending_ade.seen == [("POST", "/api/ade/hook/requests")]


# The hook's own __main__ block, read from its source and run, in a process of its own, in the module's namespace once
# the module is imported, with DEADLINE and STARTED set as the test says: the block, not a copy of it, is what runs.
MAIN_BLOCK = r"""
import ast, os, sys, time
hook = sys.argv[1]
sys.path.insert(0, os.path.dirname(hook))
import ade_hook
ade_hook.DEADLINE = float(sys.argv[2])
ade_hook.STARTED = time.monotonic() - float(sys.argv[3])
with open(hook, encoding="utf-8") as fh:
    tree = ast.parse(fh.read())
(block,) = [n for n in tree.body if isinstance(n, ast.If) and ast.unparse(n.test) == "__name__ == '__main__'"]
exec(compile(ast.Module(body=block.body, type_ignores=[]), hook, "exec"), vars(ade_hook))
"""


def _main_block(env, payload, deadline, started_ago=0.0):
    """The __main__ block run as above, given `payload` on its input: its result and how long it took. A run that does
    not end is killed after 30 seconds, which fails the test."""
    began = time.monotonic()
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", MAIN_BLOCK, ade_hook.__file__, str(deadline),
                        str(started_ago)], input=json.dumps(payload), capture_output=True, text=True, env=env,
                       timeout=30)
    return r, time.monotonic() - began


# What scheduling may add to a run of the block here beyond the start measured beside it (_start_time): the pipe
# tests are held to the deadline and the interpreter's start, not only to 10 seconds.
MARGIN = 1.0


def _start_time(env):
    """How long the block takes when it answers at once, given a call that is not Bash, to which it says nothing: the
    interpreter's start, the imports and the exit, which every run of the block here takes too."""
    r, took = _main_block(env, {"tool_name": "Read", "tool_input": {"file_path": "/x"}}, 75)
    assert (r.returncode, r.stdout) == (0, ""), r.stderr
    return took


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a named pipe (os.mkfifo)")
@pytest.mark.parametrize("pipe", ["token file", "window file"])
def test_a_file_the_hook_reads_that_does_not_answer_ends_in_the_late_denial(tmp_path, pipe):
    """The token file, or the window file a command sent in a window names, is a pipe no one writes to, which a session's
    own user can put in its place: its read never ends, and the hook, its deadline
    made 1.5 seconds, still denies at it, saying so; the run takes the deadline and the interpreter's start, measured
    beside it (_start_time), and no more than MARGIN besides. Before the gated part ran in a thread of its own, the read
    held the hook until the client killed it, which lets the call go."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PROOFPANE_ADE_URL": "https://ade.example.test"}
    command = "git push origin main"
    if pipe == "token file":
        os.mkfifo(tmp_path / "hook-token")
        env["PROOFPANE_ADE_TOKEN_FILE"] = str(tmp_path / "hook-token")
    else:
        (tmp_path / "channel").mkdir()
        os.mkfifo(tmp_path / "channel" / "GO.md")
        env.update(PROOFPANE_ADE_WINDOWS=str(tmp_path / "channel"), PROOFPANE_ADE_TOKEN="adeh_x")
        command = "anything # go 2026-09-28T03:00Z"
    start = _start_time(env)
    r, took = _main_block(env, bash(command), 1.5)
    assert r.returncode == 0, r.stderr
    decision, reason = _decision(json.loads(r.stdout))
    assert decision == "deny" and _late("1.5") in reason, reason
    assert 1.5 <= took < start + 1.5 + MARGIN, (took, start)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a named pipe (os.mkfifo)")
def test_a_run_whose_start_was_part_of_a_deadline_back_has_only_the_time_left(tmp_path):
    """The deadline counts from the hook's start, not from where the gated part begins: the hook's start set 2 seconds
    back and the deadline made 3, the token file a pipe no one writes to, the late denial comes once the second left
    has passed, within the interpreter's start measured beside it and MARGIN, not 3 seconds on, as it would if the
    gated part were given a whole deadline anew. Time spent before the gated part, the owner's own gate search among
    it, would then push the run past the block's `timeout`."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PROOFPANE_ADE_URL": "https://ade.example.test",
           "PROOFPANE_ADE_TOKEN_FILE": str(tmp_path / "hook-token")}
    os.mkfifo(tmp_path / "hook-token")
    start = _start_time(env)
    r, took = _main_block(env, bash("git push origin main"), 3, started_ago=2)
    assert r.returncode == 0, r.stderr
    decision, reason = _decision(json.loads(r.stdout))
    assert decision == "deny" and _late("3") in reason, reason
    assert 1.0 <= took < start + 1.0 + MARGIN, (took, start)


def test_the_main_block_counts_the_deadline_from_the_hook_s_start(tmp_path):
    """The __main__ block gives main STARTED: with the hook's start set a whole deadline back, a gated command is given
    the late denial at once, within the interpreter's start measured beside it and MARGIN; started now (the control),
    the same run decides as it would, here for want of an ADE address. A block that called main() with no start would
    count from main's own call and give the control's answer both times."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    start = _start_time(env)
    r, took = _main_block(env, bash("git push origin main"), 75, started_ago=100)
    assert r.returncode == 0 and _late("75") in _decision(json.loads(r.stdout))[1], (r.stdout, r.stderr)
    assert took < start + MARGIN, (took, start)
    r, _ = _main_block(env, bash("git push origin main"), 75)
    assert r.returncode == 0 and "no ADE address" in _decision(json.loads(r.stdout))[1], (r.stdout, r.stderr)
