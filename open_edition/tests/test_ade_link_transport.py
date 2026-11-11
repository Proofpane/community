"""How the session channel's client reaches the ADE (open_edition/ade_link.py: _transport, Link.opener): pair records
the proxy and the CA file and folder its own environment put in effect, and every later request takes those, never the
ones the environment of its own run names. The hooks run in the session's environment, which a repository's own
.claude/settings.json can set (its `env`): there a proxy that answers nothing, or a CA file or folder that holds
nothing, would keep each request of the stop hook from the ADE, and with no stop recorded no answer is an allow, so a
stop made after that would never be seen. Against the app served over TLS on a local port, with a certificate authority
of the test's own; a run that stands for a hook, or for a command a session runs, is a process of its own given only
the environment a test names, so that nothing this process cached (urllib's own opener is built once) stands in for
what a run reads."""

import base64
import datetime
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import types

import pytest

import ade_link
from conftest import signed_up
from test_ade_link import at_once
from test_ade_link_ask_client import address, approves, asking, waits  # noqa: F401  (waits: a fixture)
from test_ade_link_client import link_row, pair, run, served  # noqa: F401  (served: a fixture)
from test_ade_link_sign import signer_folder, signer_ticket

READ = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}})


@pytest.fixture(autouse=True)
def nothing_in_effect(monkeypatch, tmp_path):
    """This process's environment names no proxy and no CA file or folder (the container a test runs in may), so that
    what a pairing here records is what the test sets; no inbox, no pin held outside the server, a home of its own."""
    for name in list(os.environ):
        if name.lower().endswith("_proxy") or name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            monkeypatch.delenv(name)
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "ADE_OWNER_KEYS", "ADE_LINK_DIR"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def pki(tmp_path):
    """A certificate authority of the test's own, and a certificate it signed for 127.0.0.1: their files."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "The transport tests' own CA")])
    usage = dict(content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False,
                 encipher_only=False, decipher_only=False)
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
          .not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, key_cert_sign=True, crl_sign=True, **usage),
                         critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False, **usage),
                           critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    folder = tmp_path / "pki"
    folder.mkdir()
    files = types.SimpleNamespace(folder=folder, ca=str(folder / "ca.pem"), cert=str(folder / "cert.pem"),
                                  key=str(folder / "key.pem"), empty_file=str(folder / "empty.pem"),
                                  empty_dir=str(folder / "empty"))
    for path, data in ((files.ca, ca.public_bytes(serialization.Encoding.PEM)),
                       (files.cert, leaf.public_bytes(serialization.Encoding.PEM)),
                       (files.key, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption())),
                       (files.empty_file, b"")):
        with open(path, "wb") as fh:
            fh.write(data)
    os.mkdir(files.empty_dir)
    return files


@pytest.fixture
def served_tls(client, monkeypatch, pki):
    """The app served over TLS on a local port, with the certificate the test's own authority signed."""
    import uvicorn
    from proofpane_open.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning",
                                           ssl_certfile=pki.cert, ssl_keyfile=pki.key))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    url = f"https://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", url)      # the host every signature covers
    yield url
    server.should_exit = True
    thread.join(10)


class Tunnel:
    """A proxy on a local port that takes CONNECT, as an HTTPS proxy does, and pipes the bytes both ways: each request
    line and Proxy-Authorization header it was sent, in order."""

    def __init__(self):
        self.asked = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn):
        import select
        with conn:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            head, rest = head.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            method, target = lines[0].split(" ")[:2]
            auth = [x.split(":", 1)[1].strip() for x in lines[1:] if x.lower().startswith("proxy-authorization:")]
            self.asked.append((method, target, auth[0] if auth else None))
            if method != "CONNECT":
                conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
                return
            host, port = target.rsplit(":", 1)
            with socket.create_connection((host, int(port)), timeout=10) as up:
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                try:
                    if rest:
                        up.sendall(rest)
                    while True:
                        ready, _, _ = select.select([conn, up], [], [], 30)
                        if not ready:
                            return
                        for s in ready:
                            data = s.recv(65536)
                            if not data:
                                return
                            (up if s is conn else conn).sendall(data)
                except OSError:                 # one end closed without a goodbye: the tunnel ends with it
                    return

    def close(self):
        self.sock.close()


@pytest.fixture
def tunnel():
    t = Tunnel()
    yield t
    t.close()


def closed_port():
    """A local address nothing listens on: a proxy there answers nothing (the connection is refused)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def poisoned(pki, **more):
    """An environment a repository's own settings could give the session, and through it every hook and every command
    the session runs: a proxy that answers nothing for https and for any scheme, and a CA file and folder that hold
    nothing."""
    dead = closed_port()
    return dict({"HTTPS_PROXY": dead, "https_proxy": dead, "ALL_PROXY": dead, "all_proxy": dead,
                 "SSL_CERT_FILE": pki.empty_file, "SSL_CERT_DIR": pki.empty_dir}, **more)


def client_run(*argv, env, stdin=""):
    """ade_link.py run as Claude Code runs a hook (isolated, a process of its own), with only `env` beside PATH and
    the test's HOME."""
    return subprocess.run([sys.executable, "-I", ade_link.__file__, *map(str, argv)], input=stdin,
                          capture_output=True, text=True, timeout=60,
                          env=dict({"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"]}, **env))


def plain_urllib(url, env):
    """What urllib's own defaults make of `env` (urlopen, as this client used), beside PATH and the test's HOME: None
    when it reaches `url`, else why not."""
    probe = ("import sys, urllib.request\n"
             "try:\n"
             "    urllib.request.urlopen(sys.argv[1], timeout=10).read()\n"
             "except Exception as e:\n"
             "    print(type(e).__name__)\n")
    r = subprocess.run([sys.executable, "-I", "-c", probe, url], capture_output=True, text=True, timeout=60,
                       env=dict({"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"]}, **env))
    return r.stdout.strip() or None


def denied(r):
    assert r.returncode == 0, r.stderr
    return bool(r.stdout.strip()) and json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def config(d):
    return json.loads((d / "config.json").read_text())


def target(url):
    return url.split("://", 1)[1]


def test_the_environment_given_keeps_urllib_s_own_defaults_from_the_ade(served_tls, pki):
    """The control: each half of what poisoned() sets keeps a request made with urllib's defaults from the ADE, the
    proxy alone and the CA file and folder alone, while the CA the test's own authority made reaches it."""
    health = served_tls + "/api/health"
    assert plain_urllib(health, {"SSL_CERT_FILE": pki.ca}) is None
    dead = poisoned(pki)
    assert plain_urllib(health, {k: dead[k] for k in ("HTTPS_PROXY", "https_proxy")}
                        | {"SSL_CERT_FILE": pki.ca}) == "URLError"
    assert plain_urllib(health, {k: dead[k] for k in ("SSL_CERT_FILE", "SSL_CERT_DIR")}) == "URLError"


def test_pair_records_what_its_own_environment_put_in_effect_and_its_own_requests_take_it(
        client, owner, served_tls, pki, tunnel, capsys, tmp_path, monkeypatch):
    """The CA file named relative to where pair ran is recorded absolute; a folder setting that lists two, each; the
    https proxy, which carried pair's own requests; and nothing the environment names that does not apply: a proxy for
    another scheme, or one no_proxy leaves out for the ADE's host."""
    monkeypatch.chdir(pki.folder)
    here = os.getcwd()                                  # as the process names it (a Mac's /var is /private/var)
    monkeypatch.setenv("SSL_CERT_FILE", "ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", f"empty{os.pathsep}{pki.folder}")
    monkeypatch.setenv("https_proxy", tunnel.url)
    monkeypatch.setenv("http_proxy", closed_port())
    d = pair(client, owner, served_tls, capsys, tmp_path / "mac", "mac")
    assert {k: v for k, v in config(d).items() if k in ade_link.TRANSPORT} == {
        "proxy": tunnel.url, "ca_file": os.path.join(here, "ca.pem"),
        "ca_dir": f"{os.path.join(here, 'empty')}{os.pathsep}{pki.folder}"}
    assert tunnel.asked and set(tunnel.asked) == {("CONNECT", target(served_tls), None)}
    asked = len(tunnel.asked)
    monkeypatch.setenv("no_proxy", "127.0.0.1")         # left out for the ADE's host: nothing recorded, nothing carried
    d = pair(client, owner, served_tls, capsys, tmp_path / "cloud", "cloud", "cloud")
    assert "proxy" not in config(d) and len(tunnel.asked) == asked


def test_a_session_that_asks_to_be_linked_records_them_as_pair_does(client, owner, served_tls, pki, waits, capsys,
                                                                    tmp_path, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    waits["at"][1] = approves(client, owner)
    d = tmp_path / "cloud"
    with at_once():
        code, out, err = asking(capsys, served_tls, address(client, owner), d)
    assert code == 0 and "approved and linked as cloud" in out, err
    assert {k: v for k, v in config(d).items() if k in ade_link.TRANSPORT} == {"ca_file": pki.ca}


def test_the_stop_hook_sees_the_owner_s_stop_whatever_proxy_and_ca_the_session_s_environment_names(
        client, owner, served_tls, pki, capsys, tmp_path, monkeypatch):
    """A pretool run given a proxy that answers nothing for https and for any scheme, and a CA file and
    folder that hold nothing, still reaches the ADE, with the proxy (none) and the CA source pair recorded, and sees
    the stop made there; it records it, and tells the ADE it saw it."""
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    d = pair(client, owner, served_tls, capsys, tmp_path / "home" / ".proofpane-ade" / "mac", "mac")
    mac = link_row("mac")
    r = client_run("--dir", d, "pretool", "--link", mac["id"], env=poisoned(pki), stdin=READ)
    assert not denied(r)
    assert client.post(f"/api/ade/link/links/{mac['id']}/stop", headers=owner).json()["stopped_at"]
    (d / "state").unlink()
    assert not (d / "stop_seen").exists()               # the stop is the ADE's alone: only a request seeing it holds
    r = client_run("--dir", d, "pretool", "--link", mac["id"], env=poisoned(pki), stdin=READ)
    assert denied(r) and (d / "stop_seen").exists()
    assert link_row("mac")["stop_acked_at"] is not None      # and its acknowledgement went the same way


def test_every_other_request_takes_what_pair_recorded_too(client, owner, served_tls, pki, capsys, tmp_path,
                                                          monkeypatch):
    """Not only the stop hook's: the SessionStart hook (resume) and any command a session runs (heartbeat here) run in
    the same environment, and reach the ADE the same way."""
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    d = pair(client, owner, served_tls, capsys, tmp_path / "mac", "mac")
    r = client_run("--dir", d, "resume", env=poisoned(pki))
    assert r.returncode == 0 and r.stdout.splitlines()[0] == ade_link.RULE, r.stdout
    assert any(line.startswith(ade_link.RESUME_MARK) for line in r.stdout.splitlines())
    r = client_run("--dir", d, "heartbeat", env=poisoned(pki))
    assert r.returncode == 0, r.stderr


def test_a_ca_folder_pair_recorded_is_read_whatever_the_environment_of_a_run_names(client, owner, served_tls, pki,
                                                                                    capsys, tmp_path, monkeypatch):
    """A CA folder setting alone, listing two folders as OpenSSL reads it (the second holding the test's authority by
    its subject's hash, as c_rehash names one), is recorded and read for each later request."""
    openssl = shutil.which("openssl") or pytest.skip("no openssl command to name a certificate by its subject's hash")
    hashed = tmp_path / "hashed"
    hashed.mkdir()
    name = subprocess.run([openssl, "x509", "-subject_hash", "-noout", "-in", pki.ca], capture_output=True, text=True,
                          timeout=60, check=True).stdout.strip()
    shutil.copy(pki.ca, hashed / f"{name}.0")
    monkeypatch.setenv("SSL_CERT_DIR", f"{pki.empty_dir}{os.pathsep}{hashed}")
    d = pair(client, owner, served_tls, capsys, tmp_path / "mac", "mac")
    assert {k: v for k, v in config(d).items() if k in ade_link.TRANSPORT} == {
        "ca_dir": f"{pki.empty_dir}{os.pathsep}{hashed}"}
    r = client_run("--dir", d, "heartbeat", env=poisoned(pki))
    assert r.returncode == 0, r.stderr


def test_a_recorded_proxy_carries_every_request_whatever_the_environment_says(client, owner, served_tls, pki, tunnel,
                                                                              capsys, tmp_path, monkeypatch):
    """A proxy pair recorded carries each later request, in a run whose environment names another proxy, one that
    answers nothing, and says no_proxy for every host: urllib's own handler, given the proxy, would send the request
    direct for a host no_proxy names."""
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    monkeypatch.setenv("https_proxy", tunnel.url)
    d = pair(client, owner, served_tls, capsys, tmp_path / "mac", "mac")
    asked = len(tunnel.asked)
    r = client_run("--dir", d, "heartbeat", env=poisoned(pki, NO_PROXY="*", no_proxy="*"))
    assert r.returncode == 0, r.stderr
    assert tunnel.asked[asked:] == [("CONNECT", target(served_tls), None)]


def test_a_recorded_proxy_s_user_and_password_are_sent_to_it_as_urllib_sends_them(client, owner, served_tls, pki,
                                                                                  tunnel, capsys, tmp_path,
                                                                                  monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    monkeypatch.setenv("https_proxy", tunnel.url.replace("://", "://ana:p%3Ass@"))     # a colon in it, quoted
    d = pair(client, owner, served_tls, capsys, tmp_path / "mac", "mac")
    assert run(capsys, "--dir", d, "heartbeat")[0] == 0
    assert set(tunnel.asked) == {("CONNECT", target(served_tls), "Basic " + base64.b64encode(b"ana:p:ss").decode())}


def test_a_link_paired_before_goes_direct_whatever_proxy_the_environment_names(client, owner, served, capsys,
                                                                               tmp_path, monkeypatch):
    """A link whose config.json records nothing of its transport (every link paired before pair recorded one, and one
    over http with no http proxy in effect, whatever its environment names for https) sends its requests direct."""
    monkeypatch.setenv("https_proxy", closed_port())
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ca.pem"))
    d = pair(client, owner, served, capsys, tmp_path / "mac", "mac")
    assert not set(config(d)) & set(ade_link.TRANSPORT)
    dead = closed_port()
    r = client_run("--dir", d, "heartbeat", env={"HTTP_PROXY": dead, "http_proxy": dead, "ALL_PROXY": dead})
    assert r.returncode == 0, r.stderr


def test_a_link_paired_before_verifies_against_the_ca_this_python_was_built_with_not_the_environment_s(
        client, owner, served_tls, pki, capsys, tmp_path, monkeypatch):
    """With no CA file or folder recorded, the ones this Python's OpenSSL was built with (stood in for here: the test's
    own authority in their place), whatever SSL_CERT_FILE and SSL_CERT_DIR name; and with those naming the right one
    and the built-in ones not (no file there, a folder that holds none, as on many a Linux), nothing verifies."""
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    d = pair(client, owner, served_tls, capsys, tmp_path / "mac", "mac")
    kept = config(d)
    del kept["ca_file"]
    (d / "config.json").write_text(json.dumps(kept))

    def built_with(cafile, capath):
        def paths():                                    # as ssl.get_default_verify_paths() reads them, the build's own
            named_file = os.environ.get("SSL_CERT_FILE", cafile)
            named_dir = os.environ.get("SSL_CERT_DIR", capath)
            return ssl.DefaultVerifyPaths(named_file if os.path.isfile(named_file) else None,
                                          named_dir if os.path.isdir(named_dir) else None,
                                          "SSL_CERT_FILE", cafile, "SSL_CERT_DIR", capath)
        monkeypatch.setattr(ssl, "get_default_verify_paths", paths)
    built_with(pki.ca, pki.empty_dir)
    monkeypatch.setenv("SSL_CERT_FILE", pki.empty_file)
    monkeypatch.setenv("SSL_CERT_DIR", pki.empty_dir)
    assert run(capsys, "--dir", d, "heartbeat")[0] == 0
    built_with(str(pki.folder / "none.pem"), pki.empty_dir)
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    monkeypatch.delenv("SSL_CERT_DIR")
    code, _, err = run(capsys, "--dir", d, "heartbeat")
    assert code == 3 and "CERTIFICATE_VERIFY_FAILED" in err


def test_a_signer_records_them_too_and_its_requests_take_them(client, owner, served_tls, pki, capsys, monkeypatch):
    """signer pair, which the owner runs at a terminal of their own, records them as a link's pair does, and its later
    requests take them: signer status, in an environment that names a proxy answering nothing and empty CA sources."""
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    code, _, err = run(capsys, "signer", "pair", "--url", served_tls, "--ticket",
                       signer_ticket(client, owner)["ticket"], "--name", "MacBook")
    assert code == 0, err
    with open(os.path.join(signer_folder(served_tls), "config.json"), encoding="utf-8") as fh:
        kept = json.load(fh)
    assert {k: v for k, v in kept.items() if k in ade_link.TRANSPORT} == {"ca_file": pki.ca}
    r = subprocess.run([sys.executable, "-I", ade_link.__file__, "signer", "status"], capture_output=True, text=True,
                       timeout=60, env=dict({"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"]}, **poisoned(pki)))
    assert r.returncode == 0 and r.stdout.rstrip().endswith(": live"), (r.stdout, r.stderr)


# a configuration that allows no protocol Python will use: at most TLS 1.1, where Python's contexts start at 1.2 (the
# hosted ADE's Caddy takes 1.3 alone, so at most 1.2 is enough there). OpenSSL takes its file from OPENSSL_CONF, which
# a settings file's `env` can name, before any of the client's code runs and whatever Python's -I says
NO_PROTOCOL = """openssl_conf = openssl_init

[openssl_init]
ssl_conf = ssl_sect

[ssl_sect]
system_default = system_default_sect

[system_default_sect]
MaxProtocol = TLSv1.1
"""


def test_an_openssl_configuration_the_environment_names_keeps_the_stop_hook_from_the_ade_and_the_blocks_pin_it_empty(
        client, owner, served_tls, pki, capsys, tmp_path, monkeypatch):
    """What a recorded transport cannot reach: OPENSSL_CONF. A run given a configuration that allows no protocol cannot
    reach the ADE, and with no stop recorded it lets the call go, so a stop made there is not seen (the hazard, the
    test's control). The owner's blocks set it empty (settings.OPENSSL_ENV), which loads none: the same run, with the
    value the blocks give, sees the stop and holds the call."""
    from proofpane_workspace import settings
    monkeypatch.setenv("SSL_CERT_FILE", pki.ca)
    d = pair(client, owner, served_tls, capsys, tmp_path / "home" / ".proofpane-ade" / "mac", "mac")
    mac = link_row("mac")
    assert client.post(f"/api/ade/link/links/{mac['id']}/stop", headers=owner).json()["stopped_at"]
    conf = tmp_path / "no-protocol.cnf"
    conf.write_text(NO_PROTOCOL)
    if (d / "state").exists():
        (d / "state").unlink()
    r = client_run("--dir", d, "pretool", "--link", mac["id"], env={"OPENSSL_CONF": str(conf)}, stdin=READ)
    assert not denied(r) and not (d / "stop_seen").exists()
    if (d / "state").exists():
        (d / "state").unlink()
    r = client_run("--dir", d, "pretool", "--link", mac["id"], env=dict(settings.OPENSSL_ENV), stdin=READ)
    assert denied(r) and (d / "stop_seen").exists()


ADE_NAME = "ade.example.test"


class _Dns:
    """A DNS server of the test's own, on port 53 of a loopback address of its own (127.53.x.y): an A record of
    127.0.0.1 for ADE_NAME, no address of another kind for it, and no such name for any other; every name asked for is
    recorded."""

    def __init__(self):
        import random
        self.asked, self.sock = [], socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(20):
            self.address = f"127.53.{random.randint(1, 254)}.{random.randint(1, 254)}"
            try:
                self.sock.bind((self.address, 53))
                break
            except OSError as e:
                why = e
        else:
            self.sock.close()
            raise why
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        import struct
        while True:
            try:
                data, peer = self.sock.recvfrom(4096)
            except OSError:
                return
            try:
                at, labels = 12, []
                while data[at]:
                    labels.append(data[at + 1:at + 1 + data[at]].decode("ascii"))
                    at += 1 + data[at]
                name, kind, question = ".".join(labels), struct.unpack(">H", data[at + 1:at + 3])[0], data[12:at + 5]
            except (IndexError, UnicodeDecodeError, struct.error):
                continue
            self.asked.append(name)
            head = data[:2] + (b"\x81\x80" if name == ADE_NAME else b"\x81\x83")      # an answer, or no such name
            if name == ADE_NAME and kind == 1:
                record = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + bytes([127, 0, 0, 1])
                self.sock.sendto(head + struct.pack(">HHHH", 1, 1, 0, 0) + question + record, peer)
            else:
                self.sock.sendto(head + struct.pack(">HHHH", 1, 0, 0, 0) + question, peer)

    def close(self):
        self.sock.close()


@pytest.fixture
def own_resolver(tmp_path, own_home):
    """A resolv.conf naming the test's own DNS server, which a run sees as its /etc/resolv.conf in a mount namespace of
    its own (unshare -m, its mount private to it), and the server. Skipped, saying why, where a name cannot be looked up
    without the network so: a namespace needs root and unshare, the server port 53, and the system's hosts lookup dns,
    reached through that resolv.conf, which one lookup of ADE_NAME through the namespace shows before the test runs (a
    hosts line that asks systemd-resolved first, say, reads neither it nor RES_OPTIONS)."""
    if os.geteuid() != 0 or shutil.which("unshare") is None or shutil.which("mount") is None:
        pytest.skip("needs root, unshare and mount to give a run a resolv.conf of the test's own")
    try:
        with open("/etc/nsswitch.conf", encoding="utf-8") as fh:
            uses_dns = any(line.split(":", 1)[0].strip() == "hosts" and "dns" in line.split(":", 1)[1].split()
                           for line in fh if ":" in line)
    except OSError:
        uses_dns = False
    if not uses_dns:
        pytest.skip("this system's hosts lookup does not ask DNS (/etc/nsswitch.conf)")
    try:
        dns = _Dns()
    except OSError as e:
        pytest.skip(f"a DNS server of the test's own cannot listen on port 53 here: {e}")
    conf = tmp_path / "resolv.conf"
    conf.write_text(f"nameserver {dns.address}\noptions timeout:1 attempts:1\n")
    tried = subprocess.run(["unshare", "-m", "--propagation", "private", "sh", "-c",
                            'mount --bind "$0" /etc/resolv.conf && cat /etc/resolv.conf', str(conf)],
                           capture_output=True, text=True, timeout=30)
    if tried.returncode != 0 or dns.address not in tried.stdout:
        dns.close()
        pytest.skip(f"a mount namespace of the run's own is refused here: {tried.stderr.strip()[:200]}")
    lookup = ("import socket, sys\n"
              "print(socket.getaddrinfo(sys.argv[1], 443, socket.AF_INET, socket.SOCK_STREAM)[0][4][0])")
    looked = subprocess.run(["unshare", "-m", "--propagation", "private", "sh", "-c",
                             'mount --bind "$0" /etc/resolv.conf && exec "$@"', str(conf),
                             sys.executable, "-I", "-c", lookup, ADE_NAME], capture_output=True, text=True, timeout=30,
                            env={"PATH": "/usr/bin:/bin", "HOME": own_home})
    if looked.stdout.strip() != "127.0.0.1" or ADE_NAME not in dns.asked:
        dns.close()
        pytest.skip(f"a lookup of {ADE_NAME} here does not reach the test's own DNS server through that resolv.conf "
                    f"(it gave {(looked.stdout.strip() or looked.stderr.strip())[-200:]!r})")
    del dns.asked[:]
    yield types.SimpleNamespace(conf=str(conf), dns=dns)
    dns.close()


def resolved_run(own, *argv, env, stdin=""):
    """client_run, its own /etc/resolv.conf the test's, so that the ADE's name is looked up through the test's DNS."""
    return subprocess.run(["unshare", "-m", "--propagation", "private", "sh", "-c",
                           'mount --bind "$0" /etc/resolv.conf && exec "$@"', own.conf,
                           sys.executable, "-I", "-X", "utf8", ade_link.__file__, *map(str, argv)], input=stdin,
                          capture_output=True, text=True, timeout=60,
                          env=dict({"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"]}, **env))


def test_resolver_options_the_environment_names_keep_the_stop_hook_from_the_ade_and_the_blocks_pin_them_empty(
        client, owner, capsys, tmp_path, monkeypatch, own_resolver):
    """glibc's resolver reads RES_OPTIONS, and with
    `attempts:0` a lookup sends no query and fails, so a run of the stop hook that must look the ADE's name up cannot
    reach it, and with no stop recorded it lets the call go: a stop made there is not seen (the hazard, the test's
    control). The owner's blocks set it empty (settings.RESOLVER_ENV), which acts as unset: the same run, with the
    value the blocks give, asks the test's DNS, reaches the ADE and holds the call. A name the resolver looks up needs a
    DNS server, which a test without the network has only through a mount namespace of the run's own (own_resolver),
    so where that is refused the test is skipped. The app is served on 127.0.0.1 under the name ADE_NAME (its public
    address, which every signature covers), and pairing, in this process, finds the name there too."""
    import uvicorn
    from proofpane_open.main import app
    from proofpane_workspace import settings
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    try:
        port = server.servers[0].sockets[0].getsockname()[1]
        url = f"http://{ADE_NAME}:{port}"
        monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", url)
        real = socket.getaddrinfo
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda host, *args, **kw: real("127.0.0.1" if host == ADE_NAME else host, *args, **kw))
        d = pair(client, owner, url, capsys, tmp_path / "home" / ".proofpane-ade" / "mac", "mac")
        mac = link_row("mac")
        assert client.post(f"/api/ade/link/links/{mac['id']}/stop", headers=owner).json()["stopped_at"]
        if (d / "state").exists():
            (d / "state").unlink()
        r = resolved_run(own_resolver, "--dir", d, "pretool", "--link", mac["id"], env={"RES_OPTIONS": "attempts:0"},
                         stdin=READ)
        assert not denied(r) and not (d / "stop_seen").exists() and own_resolver.dns.asked == [], r.stderr
        if (d / "state").exists():
            (d / "state").unlink()
        r = resolved_run(own_resolver, "--dir", d, "pretool", "--link", mac["id"], env=dict(settings.RESOLVER_ENV),
                         stdin=READ)
        assert denied(r) and (d / "stop_seen").exists(), r.stderr
        assert ADE_NAME in own_resolver.dns.asked
    finally:
        server.should_exit = True
        thread.join(10)
