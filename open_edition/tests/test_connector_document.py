"""Apps named by a Client ID Metadata Document (oauth.py): read only from a trusted host, never from what a stranger
names; on port 443 of a global address only, connecting to the address checked; reads counted per network and in
all before they are made; no redirect followed; at most 5 KB; 5 seconds in all, the lookup included; checked as the
draft says; kept as long as its cache headers say, within bounds; a failure kept a while, never as a client."""

import http.client
import http.server
import json
import threading
import time

import pytest

from proofpane_open import oauth, store

LOOKUP = oauth._addresses               # the lookup itself, which StandIn stands in for

URL = "https://claude.ai/oauth/test-client"
CLAUDE = "https://claude.ai/api/mcp/auth_callback"


def document(**over):
    doc = {"client_id": URL, "client_name": "Claude", "redirect_uris": [CLAUDE], "grant_types": ["authorization_code",
           "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}
    for key, value in over.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    return doc


class StandIn:
    """The network, stood in: what each fetch answers, the addresses a host resolves to, and every fetch asked (with
    the address it was made at and the time left it)."""

    def __init__(self, monkeypatch, body=None, status=200, cache="max-age=600", addresses=("160.79.104.10",)):
        self.body = json.dumps(document()).encode() if body is None else body
        self.status, self.cache, self.addresses, self.fetched, self.at = status, cache, list(addresses), [], []
        monkeypatch.setattr(oauth, "fetch_document", self.fetch)
        monkeypatch.setattr(oauth, "_addresses", lambda host, deadline: self.addresses)

    def fetch(self, url, address, deadline):
        self.fetched.append(url)
        self.at.append((address, deadline - time.monotonic()))
        return self.status, self.cache, self.body


@pytest.fixture(autouse=True)
def hosts(monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR_CLIENT_HOSTS", raising=False)
    oauth._document_reads.clear()
    oauth._document_failures.clear()


def kept():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_connector_clients")]


def test_a_document_from_a_trusted_host_is_read_checked_and_kept(monkeypatch):
    net = StandIn(monkeypatch)
    got = oauth.client_for(URL, now=1_000_000)
    assert got == {"id": URL, "kind": "document", "name": "Claude", "redirect_uris": [CLAUDE], "host": "claude.ai"}
    assert net.fetched == [URL]
    [row] = kept()
    assert (row["kind"], row["fresh_until"]) == ("document", 1_000_600)


def test_a_host_not_trusted_is_never_fetched(monkeypatch):
    net = StandIn(monkeypatch)
    for url in ("https://evil.example/client.json", "https://claude.ai.evil.example/c", "https://sub.claude.ai/c"):
        with pytest.raises(oauth.ClientError, match="only from claude.ai"):
            oauth.document_client(url)
    assert net.fetched == []
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR_CLIENT_HOSTS", "App.Example.test, other.example")
    with pytest.raises(oauth.ClientError):
        oauth.document_client(URL)
    net.body = json.dumps(document(client_id="https://app.example.test/c")).encode()
    assert oauth.document_client("https://app.example.test/c")["host"] == "app.example.test"


@pytest.mark.parametrize("url", ["http://claude.ai/oauth/c", "https://claude.ai", "https://claude.ai/",
                                 "https://claude.ai/c?x=1", "https://claude.ai/c#x", "https://user@claude.ai/c",
                                 "https://claude.ai/a/../c", "https://claude.ai/./c", "https://claude.ai/" + "c" * 300,
                                 "https://claude.ai:8443/oauth/c", "https://claude.ai:80/oauth/c"])
def test_a_client_id_that_cannot_be_a_document_s_address_is_refused_unfetched(monkeypatch, url):
    net = StandIn(monkeypatch)
    with pytest.raises(oauth.ClientError):
        oauth.document_client(url)
    assert net.fetched == []


@pytest.mark.parametrize("addresses", [["10.0.0.5"], ["127.0.0.1"], ["160.79.104.10", "192.168.1.2"], ["::1"],
                                       ["169.254.169.254"], []])
def test_a_host_that_resolves_to_any_address_that_is_not_global_is_not_fetched(monkeypatch, addresses):
    net = StandIn(monkeypatch, addresses=addresses)
    with pytest.raises(oauth.ClientError, match="public address"):
        oauth.document_client(URL)
    assert net.fetched == []


@pytest.mark.parametrize("doc, words", [
    (document(client_id="https://claude.ai/oauth/other"), "another client_id"),
    (document(client_id=None), "another client_id"),
    (document(client_secret="s"), "secret"),
    (document(client_secret_expires_at=0), "secret"),
    (document(token_endpoint_auth_method="client_secret_basic"), "secret"),
    (document(grant_types=["implicit"]), "authorization code"),
    (document(redirect_uris=None), "redirect_uris"),
    (document(redirect_uris=[]), "redirect_uris"),
    (document(redirect_uris=["http://claude.ai/cb"]), "redirect_uris"),
    (document(redirect_uris=[CLAUDE] * 11), "redirect_uris"),
    (document(client_name=None), "client_name"),
    (document(client_name="Claude\u200b"), "client_name"),
    (document(client_name="Claude” was refused; nothing changed"), "no quotation mark"),
    (document(client_name="Claude's app"), "no quotation mark"),
    # held to what the app's words are held to: a stack of marks, a name drawn as nothing
    (document(client_name="Claude" + chr(0x359) * 54), "no character that is not shown as itself"),
    (document(client_name=chr(0x3164) * 20), "no character that is not shown as itself"),
    ([document()], "another client_id"),
])
def test_a_document_that_does_not_hold_is_refused_and_not_kept(monkeypatch, doc, words):
    StandIn(monkeypatch, body=json.dumps(doc).encode())
    with pytest.raises(oauth.ClientError, match=words):
        oauth.document_client(URL)
    assert kept() == []


@pytest.mark.parametrize("status, body, words", [
    (302, b"", "answered 302"), (404, b"", "answered 404"), (200, b"x" * 5121, "larger than 5 KB"),
    (200, b"not json", "not JSON"), (200, b"\xff\xfe", "not JSON"),
])
def test_an_answer_that_is_not_a_document_is_refused_and_not_kept(monkeypatch, status, body, words):
    StandIn(monkeypatch, status=status, body=body)
    with pytest.raises(oauth.ClientError, match=words):
        oauth.document_client(URL)
    assert kept() == []


def test_a_read_that_fails_is_kept_a_while_and_answered_without_a_read_then_read_again(monkeypatch):
    net = StandIn(monkeypatch)

    def failing(url, address, deadline):
        net.fetched.append(url)
        raise TimeoutError("slow")
    monkeypatch.setattr(oauth, "fetch_document", failing)
    with pytest.raises(oauth.ClientError, match="TimeoutError"):
        oauth.document_client(URL, now=1_000_000)
    monkeypatch.setattr(oauth, "fetch_document", net.fetch)
    with pytest.raises(oauth.ClientError, match="TimeoutError"):            # kept: no read
        oauth.document_client(URL, now=1_000_000 + oauth.DOCUMENT_FAILED_KEPT - 1)
    assert net.fetched == [URL] and kept() == []
    assert oauth.document_client(URL, now=1_000_000 + oauth.DOCUMENT_FAILED_KEPT)["name"] == "Claude"
    assert net.fetched == [URL, URL]
    net.status = 404
    with pytest.raises(oauth.ClientError, match="answered 404"):                # another URL's failure is its own
        oauth.document_client(URL + "2", now=1_000_000)


def test_reads_are_counted_per_network_and_in_all_before_they_are_made(monkeypatch):
    net = StandIn(monkeypatch, status=404)                  # each one fails, so each ask is a read of its own
    monkeypatch.setattr(oauth, "DOCUMENT_READS_PER_NETWORK", 2)
    monkeypatch.setattr(oauth, "DOCUMENT_READS", 3)
    for n in range(2):
        with pytest.raises(oauth.ClientError, match="answered 404"):
            oauth.document_client(f"{URL}{n}", now=1_000_000, client="203.0.113.5")
    with pytest.raises(oauth.ClientError, match="at most 2 times an hour for one network"):
        oauth.document_client(URL + "x", now=1_000_000, client="203.0.113.5")
    with pytest.raises(oauth.ClientError, match="answered 404"):
        oauth.document_client(URL + "y", now=1_000_000, client="198.51.100.7")
    with pytest.raises(oauth.ClientError, match="at most 3 times an hour, and has"):
        oauth.document_client(URL + "z", now=1_000_000, client="192.0.2.9")
    assert len(net.fetched) == 3
    with pytest.raises(oauth.ClientError, match="answered 404"):                # an hour later
        oauth.document_client(URL + "x", now=1_003_601, client="203.0.113.5")


def test_reads_are_counted_per_wider_network_too(monkeypatch):
    net = StandIn(monkeypatch, status=404)
    monkeypatch.setattr(oauth, "DOCUMENT_READS_PER_WIDE_NETWORK", 2)
    for n in range(2):                                                          # one /24, an address each
        with pytest.raises(oauth.ClientError, match="answered 404"):
            oauth.document_client(f"{URL}{n}", now=1_000_000, client=f"203.0.113.{n}")
    with pytest.raises(oauth.ClientError, match=r"at most 2 times an hour for one wider network \(an IPv4 /24"):
        oauth.document_client(URL + "x", now=1_000_000, client="203.0.113.9")
    with pytest.raises(oauth.ClientError, match="answered 404"):                # another /24's own
        oauth.document_client(URL + "y", now=1_000_000, client="198.51.100.7")
    assert len(net.fetched) == 3


@pytest.mark.parametrize("cap", ["DOCUMENT_READS_PER_NETWORK", "DOCUMENT_READS_PER_WIDE_NETWORK"])
def test_when_a_count_refuses_to_read_a_held_document_again_the_one_held_is_used(monkeypatch, cap):
    """Reads from its own network can fill that network's counts: an app this ADE knows still connects, by the document
    held, stale. (The count in all a document known here never draws on: the next test.)"""
    net = StandIn(monkeypatch)
    assert oauth.document_client(URL, now=1_000_000, client="203.0.113.5")["name"] == "Claude"
    monkeypatch.setattr(oauth, cap, 1)
    net.body = json.dumps(document(client_name="Claude two")).encode()
    assert oauth.document_client(URL, now=1_000_600, client="203.0.113.5")["name"] == "Claude"     # stale, held
    assert net.fetched == [URL]
    with pytest.raises(oauth.ClientError, match="times an hour"):              # one never read is refused
        oauth.document_client(URL + "2", now=1_000_600, client="203.0.113.5")
    assert oauth.document_client(URL, now=1_003_601, client="203.0.113.5")["name"] == "Claude two"
    p = {"client_id": URL, "redirect_uri": CLAUDE, "response_type": "code", "code_challenge": "A" * 43,
         "code_challenge_method": "S256", "resource": "https://open.example.test/mcp"}
    monkeypatch.setattr(oauth, cap, 0)
    assert oauth.authorize(p, "203.0.113.5", now=1_007_202)[0] == "page"      # stale again, and allowed by it


class Documents:
    """The network, stood in address by address: the document given for each URL, and 404 for any other."""

    def __init__(self, monkeypatch, docs):
        self.docs, self.fetched = dict(docs), []
        monkeypatch.setattr(oauth, "fetch_document", self.fetch)
        monkeypatch.setattr(oauth, "_addresses", lambda host, deadline: ["160.79.104.10"])

    def fetch(self, url, address, deadline):
        self.fetched.append(url)
        doc = self.docs.get(url)
        return (200, "max-age=600", json.dumps(doc).encode()) if doc else (404, "", b"")


def spend_the_count_in_all(now):
    """30 paths that do not exist from each of four /24s, an address each, so no
    network's own count stops them; each a read of its own that fails. The first 100 are read, the rest refused."""
    for n in range(4):
        for i in range(30):
            with pytest.raises(oauth.ClientError):
                oauth.document_client(f"https://claude.ai/x/{n}-{i}", now=now, client=f"203.0.{113 + n}.{i}")


def known():
    with store.connect() as db:
        return [r["url"] for r in db.execute("SELECT url FROM ade_connector_documents_known ORDER BY read_at, url")]


def test_a_document_read_here_before_draws_on_no_count_in_all_even_once_its_client_is_pruned(monkeypatch):
    """Any path on a trusted host is a client_id, and each failed read is a read of its own: four
    /24s spent the hour's 100 reads on 120 paths that do not exist, and then an app this ADE had known, its client
    pruned, could not be read for the rest of the hour. A document read here before draws on none of the 100, however
    long ago; one never read here still does."""
    net = Documents(monkeypatch, {URL: document()})
    assert oauth.document_client(URL, now=1_000_000, client="192.0.2.1")["name"] == "Claude"
    assert known() == [URL]
    later = 1_000_600 + oauth.KEEP_UNUSED
    with store.connect() as db:
        oauth._prune_clients(db, later)
    assert kept() == [] and known() == [URL]                   # the client went; that it was read here did not
    spend_the_count_in_all(later)
    assert len([u for u in net.fetched if "/x/" in u]) == 100
    net.docs[URL] = document(client_name="Claude two")
    assert oauth.document_client(URL, now=later + 10, client="198.51.100.7")["name"] == "Claude two"
    assert net.fetched[-1] == URL
    with pytest.raises(oauth.ClientError, match="at most 100 times an hour, and has"):
        oauth.document_client(URL + "-never-read", now=later + 10, client="198.51.100.8")


def test_a_document_held_from_before_the_list_draws_on_no_count_in_all(monkeypatch):
    """A client held from before the list of documents read here (a store made by an earlier release) is known here
    too: once stale, it is read again whatever the count in all says, and then listed."""
    net = Documents(monkeypatch, {URL: document(client_name="Claude two")})
    with store.connect() as db:
        db.execute("INSERT INTO ade_connector_clients (id, kind, name, redirect_uris, created_at, fresh_until) VALUES "
                   "(?, 'document', 'Claude', ?, 999000, 999600)", (URL, json.dumps([CLAUDE])))
    spend_the_count_in_all(1_000_000)
    assert oauth.document_client(URL, now=1_000_010, client="198.51.100.7")["name"] == "Claude two"
    assert net.fetched[-1] == URL and known() == [URL]


def test_claude_code_s_document_or_those_the_operator_names_draw_on_no_count_in_all(monkeypatch):
    """Claude Code's document, whose address Anthropic gives, draws on none of the 100 even the first time it is read;
    OPEN_EDITION_CONNECTOR_CLIENT_DOCUMENTS names others instead, and empty names none."""
    code = "https://claude.ai/oauth/claude-code-client-metadata"
    other = "https://claude.ai/oauth/hosted-app"
    loopback = ["http://localhost/callback", "http://127.0.0.1/callback"]
    net = Documents(monkeypatch, {code: document(client_id=code, redirect_uris=loopback),
                                  other: document(client_id=other, client_name="Hosted")})
    assert oauth.client_documents() == (code,)
    spend_the_count_in_all(1_000_000)
    assert oauth.document_client(code, now=1_000_010, client="198.51.100.7")["redirect_uris"] == loopback
    with pytest.raises(oauth.ClientError, match="at most 100 times an hour, and has"):
        oauth.document_client(other, now=1_000_010, client="198.51.100.7")
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR_CLIENT_DOCUMENTS", f" {other} ,, https://claude.ai/oauth/third")
    assert oauth.client_documents() == (other, "https://claude.ai/oauth/third")
    assert oauth.document_client(other, now=1_000_020, client="198.51.100.7")["name"] == "Hosted"
    with store.connect() as db:                                # Claude Code's, named no longer and not yet known
        db.execute("DELETE FROM ade_connector_clients")
        db.execute("DELETE FROM ade_connector_documents_known")
    with pytest.raises(oauth.ClientError, match="at most 100 times an hour, and has"):
        oauth.document_client(code, now=1_000_020, client="198.51.100.7")
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR_CLIENT_DOCUMENTS", "")
    assert oauth.client_documents() == ()
    assert net.fetched.count(code) == 1 and net.fetched.count(other) == 1


def test_a_read_of_a_document_known_here_takes_no_place_in_the_count_in_all(monkeypatch):
    """The count in all is of reads of documents not known here only: a known one's read leaves its place to them."""
    code = "https://claude.ai/oauth/claude-code-client-metadata"
    net = Documents(monkeypatch, {code: document(client_id=code, redirect_uris=["http://localhost/callback"]),
                                  URL: document()})
    monkeypatch.setattr(oauth, "DOCUMENT_READS", 1)
    oauth.document_client(code, now=1_000_000, client="192.0.2.1")
    assert oauth.document_client(URL, now=1_000_000, client="198.51.100.7")["name"] == "Claude"
    with pytest.raises(oauth.ClientError, match="at most 1 times an hour, and has"):
        oauth.document_client(URL + "2", now=1_000_000, client="198.51.100.8")
    assert net.fetched == [code, URL]


def test_a_document_known_here_still_counts_against_its_own_network(monkeypatch):
    """Drawing on no count in all, a document known here is still counted against the network that asks for it, so no
    one network can have it read over and over."""
    net = Documents(monkeypatch, {URL: document()})
    monkeypatch.setattr(oauth, "DOCUMENT_READS_PER_NETWORK", 1)
    oauth.document_client(URL, now=1_000_000, client="192.0.2.1")
    with store.connect() as db:
        db.execute("DELETE FROM ade_connector_clients")        # pruned: none held to fall back on
    with pytest.raises(oauth.ClientError, match="at most 1 times an hour for one network"):
        oauth.document_client(URL, now=1_000_700, client="192.0.2.1")
    assert oauth.document_client(URL, now=1_000_700, client="198.51.100.7")["name"] == "Claude"
    assert net.fetched == [URL, URL]


def test_the_documents_known_here_are_the_ones_read_most_lately(monkeypatch):
    """At most DOCUMENTS_KNOWN are remembered: the one read least lately goes first, and a read moves one to the end."""
    urls = [URL + str(n) for n in range(3)]
    Documents(monkeypatch, {u: document(client_id=u) for u in urls})
    monkeypatch.setattr(oauth, "DOCUMENTS_KNOWN", 2)
    oauth.document_client(urls[0], now=1_000_000)
    oauth.document_client(urls[1], now=1_000_001)
    oauth.document_client(urls[0], now=1_000_700)              # stale, read again: now the latest
    oauth.document_client(urls[2], now=1_000_701)
    assert known() == [urls[0], urls[2]]


def test_an_authorization_s_read_is_counted_by_the_network_it_came_from(monkeypatch):
    net = StandIn(monkeypatch, status=404)
    monkeypatch.setattr(oauth, "DOCUMENT_READS_PER_NETWORK", 1)
    p = {"redirect_uri": CLAUDE, "response_type": "code", "code_challenge": "A" * 43, "code_challenge_method": "S256",
         "resource": "https://open.example.test/mcp"}
    assert "answered 404" in oauth.authorize(dict(p, client_id=URL + "1"), "203.0.113.5", now=1_000_000)[1]
    assert "for one network" in oauth.authorize(dict(p, client_id=URL + "2"), "203.0.113.5", now=1_000_000)[1]
    assert "answered 404" in oauth.authorize(dict(p, client_id=URL + "3"), "198.51.100.7", now=1_000_000)[1]
    assert len(net.fetched) == 2


def test_a_pinned_connection_is_made_at_the_address_given(monkeypatch):
    made = []

    def connect(where, timeout=None, source=None):
        made.append(where)
        raise OSError("not made here")
    monkeypatch.setattr(oauth.socket, "create_connection", connect)
    conn = oauth._pinned(http.client.HTTPSConnection("claude.ai", 443, timeout=1), "160.79.104.10")
    with pytest.raises(OSError):
        conn.connect()
    assert made == [("160.79.104.10", 443)]


def test_a_read_goes_to_the_address_checked_and_its_lookup_counts_in_its_time(monkeypatch):
    net = StandIn(monkeypatch)
    monkeypatch.setattr(oauth, "_addresses", LOOKUP)                # the lookup itself, with its deadline
    looked = []

    def resolve(host):
        looked.append(host)
        time.sleep(0.5)
        return ["160.79.104.12", "160.79.104.13"]
    monkeypatch.setattr(oauth, "_resolve", resolve)
    assert oauth.document_client(URL)["name"] == "Claude"
    [(address, left)] = net.at
    assert looked == ["claude.ai"] and address == "160.79.104.12"
    assert left <= oauth.DOCUMENT_WAIT - 0.45                      # one deadline for the lookup and the read


def test_a_lookup_that_takes_longer_than_a_read_s_time_is_refused_in_that_time(monkeypatch):
    net = StandIn(monkeypatch)
    monkeypatch.setattr(oauth, "_addresses", LOOKUP)
    monkeypatch.setattr(oauth, "DOCUMENT_WAIT", 1)
    monkeypatch.setattr(oauth, "_resolve", lambda host: time.sleep(3) or ["160.79.104.10"])
    began = time.monotonic()
    with pytest.raises(oauth.ClientError, match="could not be looked up"):
        oauth.document_client(URL)
    assert time.monotonic() - began < 2.5 and net.fetched == []


@pytest.mark.parametrize("cache, fresh", [("max-age=600", 600), ("max-age=10", 300), ("max-age=99999999", 86400),
                                          ("", 3600), ("no-store", 300), ("public, max-age=7200, immutable", 7200),
                                          ("no-cache, max-age=7200", 300)])
def test_a_document_is_kept_as_its_cache_headers_say_within_bounds(monkeypatch, cache, fresh):
    StandIn(monkeypatch, cache=cache)
    oauth.document_client(URL, now=1_000_000)
    assert kept()[0]["fresh_until"] == 1_000_000 + fresh


def test_a_kept_document_is_used_until_stale_then_read_again(monkeypatch):
    net = StandIn(monkeypatch)
    oauth.document_client(URL, now=1_000_000)
    net.body = json.dumps(document(client_name="Claude two")).encode()
    assert oauth.document_client(URL, now=1_000_599)["name"] == "Claude"
    assert oauth.document_client(URL, now=1_000_600)["name"] == "Claude two"
    assert net.fetched == [URL, URL]


def test_a_registered_client_is_found_by_its_id_and_a_stranger_s_is_not():
    got = oauth.register({"redirect_uris": [CLAUDE], "client_name": "Claude"})
    assert oauth.client_for(got["client_id"]) == {"id": got["client_id"], "kind": "registered", "name": "Claude",
                                                  "redirect_uris": [CLAUDE], "host": None}
    for other in ("ppcl-" + "0" * 32, "ppcl-x", None, 7, URL.replace("https", "http")):
        with pytest.raises(oauth.ClientError):
            oauth.client_for(other)


# ---- the real fetch, against a server on this machine --------------------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/doc")
            self.end_headers()
            return
        if self.path == "/slow":                 # a body sent a byte at a time, each well within a read's timeout
            self.send_response(200)
            self.send_header("Content-Length", "20")
            self.end_headers()
            for _ in range(20):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.2)
            return
        body = {"/big": b"x" * 10000, "/host": self.headers["Host"].encode()}.get(self.path, b'{"ok": true}')
        self.send_response(200)
        self.send_header("Cache-Control", "max-age=900")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def plain(port, name="claude.ai"):
    """A plain connection for `name`, pinned to this machine's server, as fetch_document pins its TLS one."""
    return oauth._pinned(http.client.HTTPConnection(name, port, timeout=5), "127.0.0.1")


def test_the_read_follows_no_redirect_and_reads_at_most_its_limit(local):
    assert oauth._get(plain(local), "/moved")[0] == 302
    assert oauth._get(plain(local), "/doc") == (200, "max-age=900", b'{"ok": true}')
    assert len(oauth._get(plain(local), "/big")[2]) == oauth.DOCUMENT_MAX + 1


def test_a_read_connects_to_the_address_checked_under_its_host_s_name(local):
    """No second lookup: the connection is made at the address given, and the host's name is what it asks for."""
    assert oauth._get(plain(local), "/host")[2] == f"claude.ai:{local}".encode()


def test_a_read_that_trickles_is_cut_at_its_time_in_all(local):
    began = time.monotonic()
    with pytest.raises(TimeoutError):
        oauth._within(began + 1, lambda: oauth._get(plain(local), "/slow"))
    assert time.monotonic() - began < 1.6
