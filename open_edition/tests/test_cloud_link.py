"""CLOUD-LINK (queue 4.317): a session on claude.ai links to the ADE from the one block the page gives, or is told the
one setting that stops it.

The block, as the page gives it (tests/vectors/cloud_link_page.json, which the page's own case,
ui/src/components/CloudLink.test.tsx, checks the page writes word for word), is run here as written, by a shell whose
HOME is an empty folder, with the link client's sha256 and the ticket's address and ticket filled in as the page fills
them. Against the app served on a local port it fetches the client the app serves, checks it, puts it at
~/.proofpane-ade/ade_link.py and links the session; behind a proxy that answers 403 to the CONNECT for the ADE's host it
prints one fixed sentence naming the host and the setting and fetches nothing while it is refused; a client whose sha256
is not the one
the page carries is refused in one fixed sentence, and nothing runs; a python3 that cannot import cryptography is told
so before anything is fetched. The link client's setup and pair say the same sentence where a proxy refuses the ADE's
host, for a session on claude.ai, and one that names no claude.ai setting for a session on a machine. The ADE serves its
own client at the one path the block fetches, with no sign-in, and the image's page is built from the client the image
serves.

CLOUD-LINK-3 (queue 4.320): one paste is enough. Where the proxy refuses the ADE's host the block says once, in the line
it prints then, the setting and that it waits; it tries again every ten seconds while its ticket lives (the ticket's
end the page writes into it) and, once the proxy lets the host through, goes on to fetch, check and link with no second
paste; where the ticket has too little time left for another try and a link, it says in one line to make a new ticket
(with no word of waiting where it cannot wait). A failure other than the refusal, or an answer from the ADE that is not
the client, ends it at once, as before. So the card's two steps can come in either order."""

import hashlib
import http.server
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

import pytest

import ade_link
from test_ade_link import at_once
from test_ade_link_client import owner, served, ticket  # noqa: F401  (fixtures: the app on a port, its owner)
from test_ade_link_transport import Tunnel, pki, served_tls  # noqa: F401  (fixtures: the app over TLS, its CA)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
VECTORS = os.path.join(HERE, "vectors", "cloud_link_page.json")
CLIENT_FILE = os.path.join(ROOT, "ade_link.py")
LOCAL, REMOTE, HOST = "http://127.0.0.1:8765", "https://ade.example.test", "ade.example.test"
SHA_WRITTEN = "@CLIENT_SHA256@"                # where the vectors file writes the client's sha256
SESSION = "cse_01" + "A" * 16                 # the session's id, as claude.ai gives its shell
REF = "https://claude.ai/code/session_" + SESSION[len("cse_"):]


@pytest.fixture(scope="module")
def page():
    with open(VECTORS, encoding="utf-8") as fh:
        return json.load(fh)


def client_bytes():
    with open(CLIENT_FILE, "rb") as fh:
        return fh.read()


def given(page, url):
    """The block the page gives for `url`, with the sha256 its build carries: that of open_edition/ade_link.py."""
    block = page["blocks"][url]
    assert block.count(SHA_WRITTEN) == 1
    return block.replace(SHA_WRITTEN, hashlib.sha256(client_bytes()).hexdigest())


def at(page, url, real_ticket):
    """The block the page gives for this ticket's answer from the app at `url`: its address and its ticket."""
    block = given(page, LOCAL)
    assert block.count(page["answer"]["ticket"]) == 1
    return block.replace(LOCAL, url).replace(page["answer"]["ticket"], real_ticket)


def waits(page):
    """What the block says after the setting where the proxy refuses the ADE's host and it can wait (CLOUD-LINK-3):
    the vectors file's, as ui/src/cloudLink.ts writes it."""
    return page["block_waits"]


def ended(page, host):
    """What the block says where the ticket has too little time left for another try and a link."""
    return page["ticket_ended"].format(host=host)


def until(page, block, deadline):
    """The block with the ticket's end, which the page writes into it from the ticket's answer (`expires_at`), put at
    `deadline`, seconds since the epoch."""
    written = f"UNTIL = {page['answer']['expires_at']}\n"
    assert block.count(written) == 1
    return block.replace(written, f"UNTIL = {int(deadline)}\n")


def _shell(tmp_path, env=None, python=sys.executable):
    """What run_block and start_block run the block with: its environment, its HOME and its folder."""
    home, bin_dir, work = tmp_path / "home", tmp_path / "bin", tmp_path / "work"
    for folder in (home, bin_dir, work):
        folder.mkdir()
    python3 = bin_dir / "python3"
    python3.write_text(f"#!/bin/sh\nexec {shlex.quote(str(python))} \"$@\"\n")
    python3.chmod(0o755)
    environ = {"HOME": str(home), "PATH": os.pathsep.join((str(bin_dir), "/usr/bin", "/bin")),
               "CLAUDE_CODE_REMOTE_SESSION_ID": SESSION, "ADE_LINK_KEEP": "0"}
    environ.update(env or {})
    return environ, home, work


def run_block(tmp_path, block, env=None, python=sys.executable):
    """The block run as written by /bin/sh, in a folder of its own, its HOME an empty folder and python3 on its PATH
    `python` (by default the Python that runs these tests, which has cryptography, as the claude.ai session's must); no
    proxy unless `env` names one; and, as every test here has it (conftest), ADE_LINK_KEEP=0, so the link starts no
    keeper."""
    environ, home, work = _shell(tmp_path, env, python)
    running = subprocess.Popen(["/bin/sh", "-c", block], cwd=work, env=environ, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = running.communicate(timeout=180)
    finally:
        stop(running)
    return subprocess.CompletedProcess(running.args, running.returncode, out, err), home


def stop(running):
    """The block's whole process group ended: the shell, the block's python and a setup it started, which killing the
    shell alone would leave waiting."""
    try:
        os.killpg(running.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    running.wait()


def first_line(running, within=60):
    """The first line the started block prints on its standard error, which a claude.ai session reads as it comes;
    the test fails, rather than waits on, a block that says nothing within `within` seconds."""
    got = []
    reader = threading.Thread(target=lambda: got.append(running.stderr.readline()), daemon=True)
    reader.start()
    reader.join(within)
    assert got, f"the block said nothing within {within} seconds"
    return got[0]


def start_block(tmp_path, block, env=None):
    """The block started as run_block runs it, as a claude.ai session runs it in the background, so that what it prints
    is read as it comes."""
    environ, home, work = _shell(tmp_path, env)
    running = subprocess.Popen(["/bin/sh", "-c", block], cwd=work, env=environ, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    return running, home


def links(client, owner, name):
    return [v for v in client.get("/api/ade/link/sessions", headers=owner).json()["links"] if v["name"] == name]


class _Refusing(http.server.BaseHTTPRequestHandler):
    """A proxy that answers every CONNECT with its server's `code`: 403 as a claude.ai environment's refuses a host its
    network access leaves out, 502 as one that cannot reach it. Each request's first line kept, and its time."""

    def do_CONNECT(self):  # noqa: N802  (http.server's name)
        self.server.seen.append(self.requestline)
        self.server.times.append(time.monotonic())
        self.send_response(self.server.code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_CONNECT

    def log_message(self, *args):
        pass


def _answering(code):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Refusing)
    server.seen, server.times, server.code = [], [], code
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def refusing_proxy():
    server = _answering(403)
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def failing_proxy():
    server = _answering(502)
    yield server
    server.shutdown()
    server.server_close()


class _NoClient(http.server.BaseHTTPRequestHandler):
    """An ADE that answers 404 at every path, the client's among them, each request's time kept."""

    def do_GET(self):  # noqa: N802  (http.server's name)
        self.server.times.append(time.monotonic())
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def no_client():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _NoClient)
    server.times = []
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def connects(proxy):
    return [line.split(" ")[1] for line in proxy.seen if line.startswith("CONNECT ")]


class _RefusingUntilSet(Tunnel):
    """A proxy that refuses every CONNECT with a 403, as a claude.ai environment's does for a host its network access
    leaves out, until `refusing` is turned off, as the person changes the environment's setting: from then on it pipes
    the bytes both ways, as Tunnel does. Every request line is kept in `asked`."""

    def __init__(self):
        self.refusing = True                  # before the first connection can come
        self.refusals = 0
        super().__init__()

    def _one(self, conn):
        if not self.refusing:
            return super()._one(conn)
        with conn:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            method, target = head.decode("latin-1").split("\r\n")[0].split(" ")[:2]
            self.asked.append((method, target, None))
            self.refusals += 1                # counted before the answer, which the block's first line follows
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")


def refused(proxy):
    """How many CONNECTs the proxy has refused so far."""
    return proxy.refusals


# ---- the block, run as the page gives it -------------------------------------------------------------------------

def test_the_page_s_block_links_a_claude_ai_session_from_an_empty_home(tmp_path, page, client, owner, served):
    with at_once():             # linked as it pairs: the wait is test_ade_link_pair_wait.py's
        done, home = run_block(tmp_path, at(page, served, ticket(client, owner)))
    assert done.returncode == 0, done.stderr
    ade_home = home / ".proofpane-ade"
    assert (ade_home / "ade_link.py").read_bytes() == client_bytes()        # where SETUP_A.md installs it
    assert f"ADE: linked as {page['name']} (link " in done.stdout
    assert "ADE: a cloud session reads no .claude/settings.local.json, so no hook was written" in done.stdout
    config = json.loads((ade_home / page["name"] / "config.json").read_text())
    assert config["kind"] == "cloud" and config["url"] == served
    [linked] = links(client, owner, page["name"])
    assert linked["kind"] == "cloud" and linked["session_ref"] == REF


def test_behind_a_proxy_that_refuses_the_ade_s_host_the_block_says_the_setting_once_waits_and_ends_with_its_ticket(
        tmp_path, page, refusing_proxy):
    # the ticket ends 45 seconds on, and the block leaves a try 10 seconds and the link 20 before its end: started
    # within 14 seconds (the shell, python3 and its imports, at the Mac's lowest priority), it tries two or three times,
    # 10 seconds apart, and then says the ticket has too little time left
    block = until(page, given(page, REMOTE), time.time() + 45)
    started = time.monotonic()
    done, home = run_block(tmp_path, block, {"HTTPS_PROXY": refusing_proxy.url, "https_proxy": refusing_proxy.url})
    said = "ADE: " + ade_link.HOST_REFUSED.format(host=HOST)        # the client's own sentence, word for word
    assert said == "ADE: " + page["host_refused"].format(host=HOST)
    assert done.returncode != 0 and done.stdout == ""
    # the setting and that it waits, said once however often it tries, then one line to make a new ticket
    assert done.stderr == f"{said} {waits(page)}\nADE: {ended(page, HOST)}\n"
    assert not os.path.exists(home / ".proofpane-ade")              # nothing installed, setup never run
    tries = connects(refusing_proxy)
    assert tries in ([f"{HOST}:443"] * 2, [f"{HOST}:443"] * 3), tries       # it tried again on its own
    gaps = [b - a for a, b in zip(refusing_proxy.times, refusing_proxy.times[1:])]
    assert all(9.5 <= gap <= 12.5 for gap in gaps), gaps                    # every 10 seconds
    assert time.monotonic() - started < 60


def test_with_too_little_of_the_ticket_left_to_wait_the_block_says_the_setting_alone_then_to_make_a_new_ticket(
        tmp_path, page, refusing_proxy):
    # 20 seconds left: no room for another try and the link, so no word of waiting, and no wait
    block = until(page, given(page, REMOTE), time.time() + 20)
    done, home = run_block(tmp_path, block, {"HTTPS_PROXY": refusing_proxy.url, "https_proxy": refusing_proxy.url})
    ended_at = time.monotonic()
    said = "ADE: " + ade_link.HOST_REFUSED.format(host=HOST)
    assert done.returncode != 0 and done.stdout == ""
    assert done.stderr == f"{said}\nADE: {ended(page, HOST)}\n"
    assert not os.path.exists(home / ".proofpane-ade")
    assert connects(refusing_proxy) == [f"{HOST}:443"]
    assert ended_at - refusing_proxy.times[0] < 9                   # it did not sleep after its try


def test_a_proxy_that_cannot_reach_the_ade_ends_the_block_at_once_with_no_word_of_the_setting(tmp_path, page,
                                                                                           failing_proxy):
    # a 502 to the CONNECT is not the environment's refusal: no setting would help, so the block neither says one nor
    # waits, as before CLOUD-LINK-3
    done, home = run_block(tmp_path, given(page, REMOTE),
                           {"HTTPS_PROXY": failing_proxy.url, "https_proxy": failing_proxy.url})
    ended_at = time.monotonic()
    assert done.returncode != 0 and done.stdout == ""
    assert done.stderr == (f"ADE: no link client from {REMOTE}/api/ade/link/client (OSError); nothing was installed or "
                           "run.\n")
    assert not os.path.exists(home / ".proofpane-ade")
    assert connects(failing_proxy) == [f"{HOST}:443"]
    assert ended_at - failing_proxy.times[0] < 9                    # no wait after its try


def test_an_ade_that_answers_the_client_s_path_with_an_error_ends_the_block_at_once(tmp_path, page, no_client):
    done, home = run_block(tmp_path, given(page, LOCAL).replace(LOCAL, no_client.url))
    ended_at = time.monotonic()
    assert done.returncode != 0 and done.stdout == ""
    assert done.stderr == (f"ADE: no link client from {no_client.url}/api/ade/link/client: HTTP 404; nothing was "
                           "installed or run.\n")
    assert not os.path.exists(home / ".proofpane-ade")
    assert len(no_client.times) == 1 and ended_at - no_client.times[0] < 9      # one request, no wait after it


def test_the_ticket_s_answer_gives_its_end_as_a_whole_number_which_the_block_waits_until(client, owner):
    # the page writes expires_at into the block, and gives no block where it is not a whole positive number
    confirmation = client.post("/api/account/confirm", headers=owner,
                               json={"password": "correct horse battery", "change": "link-ticket:new"}).json()
    answer = client.post("/api/ade/link/tickets", headers=owner,
                         json={"confirmation": confirmation["confirmation"]}).json()
    assert type(answer["expires_at"]) is int and answer["expires_at"] > time.time()


def test_pasted_before_the_setting_the_block_waits_and_goes_on_by_itself_once_the_host_is_let_through(
        tmp_path, page, client, owner, served_tls, pki):
    # the app over TLS on a local port, behind a proxy that refuses its host until the setting is changed
    proxy = _RefusingUntilSet()
    host = "127.0.0.1"
    block = given(page, REMOTE).replace(REMOTE, served_tls).replace(HOST, host)
    assert block.count(page["answer"]["ticket"]) == 1
    block = until(page, block.replace(page["answer"]["ticket"], ticket(client, owner)), time.time() + 300)
    running = None
    try:
        with at_once():         # linked as it pairs: the wait is test_ade_link_pair_wait.py's
            running, home = start_block(tmp_path, block, {"HTTPS_PROXY": proxy.url, "https_proxy": proxy.url,
                                                          "SSL_CERT_FILE": pki.ca})
            first = first_line(running)                # the setting, and that it waits,
            assert first == f"ADE: {ade_link.HOST_REFUSED.format(host=host)} {waits(page)}\n"
            assert refused(proxy) == 1                 # said at the first refusal, not a later one
            with pytest.raises(subprocess.TimeoutExpired):
                running.wait(timeout=3)                # it waits: it has not stopped
            deadline = time.monotonic() + 30           # refused once more, so that "said once" means something
            while refused(proxy) < 2:
                assert time.monotonic() < deadline, proxy.asked
                time.sleep(0.2)
            proxy.refusing = False                     # the person changes the environment's setting
            out, err = running.communicate(timeout=120)
    finally:
        if running is not None:
            stop(running)
        proxy.close()
    assert running.returncode == 0, first + err
    assert (first + err).count(waits(page)) == 1 and "too little time" not in err   # said once, though refused twice
    ade_home = home / ".proofpane-ade"
    assert (ade_home / "ade_link.py").read_bytes() == client_bytes()        # fetched, checked and put in place
    assert f"ADE: linked as {page['name']} (link " in out
    [linked] = links(client, owner, page["name"])
    assert linked["kind"] == "cloud" and linked["session_ref"] == REF


def test_a_client_whose_sha256_is_not_the_page_s_is_refused_and_nothing_runs(tmp_path, page, client, owner, served,
                                                                              monkeypatch):
    from proofpane_open.ade import link_client
    served_bytes = link_client.client_bytes()
    monkeypatch.setattr(link_client, "client_bytes", lambda: served_bytes + b"\n# not the client the page carries\n")
    done, home = run_block(tmp_path, at(page, served, ticket(client, owner)))
    assert done.returncode != 0 and done.stdout == ""
    assert done.stderr == "ADE: " + page["client_changed"] + "\n"
    assert not os.path.exists(home / ".proofpane-ade")
    assert links(client, owner, page["name"]) == []                   # no pairing was sent


def test_a_python3_that_cannot_import_cryptography_is_told_so_before_anything_is_fetched(tmp_path, page,
                                                                                       refusing_proxy):
    bare = tmp_path / "bare"                    # a Python with nothing but its standard library
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(bare)], check=True, timeout=120)
    done, home = run_block(tmp_path, given(page, REMOTE),
                           {"HTTPS_PROXY": refusing_proxy.url, "https_proxy": refusing_proxy.url},
                           python=bare / "bin" / "python3")
    assert done.returncode != 0 and done.stdout == ""
    assert done.stderr == "ADE: " + page["no_cryptography"] + "\n"
    assert not os.path.exists(home / ".proofpane-ade")
    assert refusing_proxy.seen == []                                 # nothing fetched


# ---- the link client's own setup and pair --------------------------------------------------------------------------

COMMANDS = [("setup", "--ticket", "PP-ABCDEFGH"), ("pair", "--ticket", "PP-ABCDEFGH"), ("pair", "--ask", "ask-" + "a" * 20)]


def behind(monkeypatch, tmp_path, proxy):
    for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, proxy.url)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)    # a retry would wait for nothing


def said_for(page, kind):
    """What setup and pair say where the proxy refuses the ADE's host: the page's sentence for a session on claude.ai,
    and for one on a machine words that name no claude.ai setting."""
    if kind == "cloud":
        return "ADE: " + page["host_refused"].format(host=HOST)
    return "ADE: " + ade_link.TUNNEL_REFUSED.format(host=HOST)


def run_command(tmp_path, page, command, kind):
    directory = tmp_path / "home" / ".proofpane-ade" / page["name"]
    where = ["--ref", REF] if kind == "cloud" else []
    if command[0] == "setup" and kind == "local":
        (tmp_path / "project").mkdir(exist_ok=True)                   # the folder a session on a machine runs in
        where = ["--folder", str(tmp_path / "project")]
        # the client where SETUP_A.md installs it, which setup checks its hooks can run before it sends anything
        installed = tmp_path / "home" / ".proofpane-ade" / "ade_link.py"
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(client_bytes())
    code = ade_link.main([command[0], "--url", REMOTE, *command[1:], "--name", page["name"], "--kind", kind, *where,
                          "--dir", str(directory)])
    return code, directory


@pytest.mark.parametrize("kind", ["cloud", "local"])
@pytest.mark.parametrize("command", COMMANDS)
def test_setup_and_pair_say_where_a_proxy_refuses_the_ade_s_host(tmp_path, monkeypatch, capsys, page, refusing_proxy,
                                                                 command, kind):
    behind(monkeypatch, tmp_path, refusing_proxy)
    code, directory = run_command(tmp_path, page, command, kind)
    out, err = capsys.readouterr()
    assert code == 2 and err.splitlines().count(said_for(page, kind)) == 1, err
    assert "no answer from" not in err and "linked as" not in out
    assert kind == "cloud" or "claude.ai" not in err
    assert connects(refusing_proxy) == [f"{HOST}:443"]                # asked once, nothing sent through
    assert not os.path.exists(directory / "pairing.json")             # no ticket counted as sent


@pytest.mark.parametrize("command", COMMANDS[1:])
def test_a_directory_that_holds_a_key_is_told_at_its_first_read_and_sends_nothing_after(tmp_path, monkeypatch, capsys,
                                                                                       page, refusing_proxy, command):
    behind(monkeypatch, tmp_path, refusing_proxy)
    run_command(tmp_path, page, command, "cloud")                     # makes the key, and is refused
    capsys.readouterr()
    del refusing_proxy.seen[:]
    # run again, the directory holds the key: where its pairing stands is read first (or where its ask stands), and
    # that refusal is said, with no pairing or ask sent after it
    code, _ = run_command(tmp_path, page, command, "cloud")
    out, err = capsys.readouterr()
    assert code == 2 and err.splitlines().count(said_for(page, "cloud")) == 1, err
    assert connects(refusing_proxy) == [f"{HOST}:443"]


# ---- the ADE's side ------------------------------------------------------------------------------------------------

def test_the_ade_serves_its_own_client_at_the_path_the_block_fetches_with_no_sign_in(page):
    from fastapi.testclient import TestClient
    from proofpane_open.main import app
    fetched = re.search(r'^URL = "https://ade\.example\.test(/[^"]+)"$', page["blocks"][REMOTE], re.M).group(1)
    answer = TestClient(app).get(fetched)                             # no Authorization header
    assert answer.status_code == 200 and answer.content == client_bytes()
    assert answer.headers["content-type"].startswith("text/x-python")


def test_the_image_s_page_is_built_beside_the_client_the_image_serves():
    # the page's build reads ../ade_link.py, from ui/, for the sha256 it carries (ui/vite.config.ts); the image serves
    # the open_edition folder's (COPY open_edition), so its page stage takes that same file, beside ui/, before it builds
    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
        text = fh.read()
    stage = text[text.index(" AS ui"):text.index("\nFROM python")]
    copies = [line.split()[1:] for line in stage.splitlines() if line.startswith("COPY ")]
    assert ["open_edition/ui/", "./"] in copies                       # the page's sources, in /src/ui (WORKDIR)
    assert ["open_edition/ade_link.py", "/src/"] in copies
    assert stage.index("COPY open_edition/ade_link.py /src/") < stage.index("RUN npm run build")
