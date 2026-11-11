"""The Claude app connector's authorization server: OAuth 2.1 with this deployment as its own authorization server, for
the MCP endpoint at /mcp (ade/connector.py). It is off unless OPEN_EDITION_CONNECTOR is `on`; off, its routes answer
404 and no token works, but nothing is revoked: on again, a grant still in effect works again.

- The issuer is the public address's origin (OPEN_EDITION_PUBLIC_URL's scheme, host and a port that is not the default,
  lowercase, with no trailing slash), and the resource is the issuer and /mcp. Both come from the public address,
  never from a request's Host header, so changing the address ends every connection, as it ends passkeys and links.
- The protected resource metadata (RFC 9728) and the authorization server metadata (RFC 8414) say where each endpoint
  is; the second says a client may name itself by a Client ID Metadata Document, with "none" among the token endpoint's
  methods (the Claude app uses a document only when both are there), or register itself.
- A client may register itself (RFC 7591, POST /oauth/register, with no authorization): a public client only, with no
  secret, whose redirect addresses are each on the deployment's list (OPEN_EDITION_CONNECTOR_REDIRECTS, by default the
  Claude app's callback and Claude Code's loopback ones), so whoever registers, a sign-in returns only there. A
  redirect address is https, or http on this machine's loopback, and is compared whole, as a string, but for a
  loopback address's port, which may be any (OAuth 2.1, 8.4.2). A registration is sent as application/json, so a page
  of another site cannot make a visitor's browser send one without asking first (a CORS preflight, which only the
  app's own origins pass). One no grant uses is kept a day. Registering is never refused for others' registrations:
  the Claude app may register from one network for every user, so a count that refused would let anyone who
  registers through Claude, or from a network of their own, keep everyone else from registering. At a cap the oldest
  registration that can go, goes: one no grant uses and no waiting request names (a request names its app until it
  is answered, so an app midway through being allowed is never pushed out). The caps: REGISTRATIONS_PER_NETWORK
  kept from one network, its oldest going; REGISTRATIONS_PER_WIDE_NETWORK from one wider network (an IPv4 /24, an
  IPv6 /48), likewise; and UNUSED_REGISTRATIONS in all, past which the oldest of the wider network that has the most
  goes. Only when none can go is a registration refused (429).
- Or a client names itself by a Client ID Metadata Document (draft-ietf-oauth-client-id-metadata-document): its
  client_id is an https URL, which the ADE reads only when the URL's host is one it trusts
  (OPEN_EDITION_CONNECTOR_CLIENT_HOSTS, by default claude.ai), so it never fetches what a stranger names. Reads are
  counted before they are made, DOCUMENT_READS_PER_NETWORK an hour asked from one network and
  DOCUMENT_READS_PER_WIDE_NETWORK from one wider network, and DOCUMENT_READS in all of documents not known here. One
  known here, held, read here before (the DOCUMENTS_KNOWN read most lately are remembered past pruning) or named in
  OPEN_EDITION_CONNECTOR_CLIENT_DOCUMENTS (by default Claude Code's), draws on none of DOCUMENT_READS, which others'
  reads of any number of paths that do not exist can spend; when its network's count refuses to read again one held
  but stale, the one held is used. So reads asked from other networks cannot keep an app known here from connecting;
  one never read here, they can for up to an hour. A read goes only to port 443 of a host whose every address is
  global, and connects to the address checked, so no second lookup sends it elsewhere; it follows no redirect, reads
  at most DOCUMENT_MAX bytes, and takes at most DOCUMENT_WAIT seconds in all, the lookup included. The document's
  client_id must be its own URL; it holds no secret and asks for none; its redirect addresses and name are held to the
  rules a registration's are. It is kept as long as its Cache-Control says, from DOCUMENT_FRESH_MIN to
  DOCUMENT_FRESH_MAX seconds. A read that fails keeps no client: its failure is kept DOCUMENT_FAILED_KEPT seconds, in
  this process's memory, and an ask meanwhile is answered with it.
- /oauth/authorize is a GET, which a page of another site can make its visitors' browsers send (an image, a frame, a
  prefetch): a request the browser says is not a top-level navigation (its Sec-Fetch-Mode not navigate, or its
  Sec-Fetch-Dest not document, when it sends them), or says is made ahead of use (any Sec-Purpose, as a prefetch or a
  prerender sends, or the older Purpose), gets the ADE's own page, and nothing is read or kept for it. Then it checks
  the client and its redirect address; either wrong, it says so on its own page and redirects nowhere (OAuth 2.1,
  2.3.5).
  Anything else wrong goes back to the redirect address with the error, the state and the issuer (RFC 9207). A request
  that holds is kept for REQUEST_TTL seconds: at most REQUESTS_PER_NETWORK wait from one network and
  REQUESTS_PER_WIDE_NETWORK from one wider network, and with REQUESTS_WAITING waiting in all a new one pushes out the
  oldest of the wider network that has the most waiting, so a flood from many networks pushes out its own and refuses
  no one. It keeps how the browser said it arrived (its Sec-Fetch-Site and the Referer's origin): the pane warns when
  that was not a cross-site navigation from the app's own site, as an authorize address forwarded to the owner, opened
  in the owner's own browser, is not. It is bound to the browser that made it: that browser is given a cookie of its
  own for the request (browser_cookie, a random value that carries no sign-in), and the request keeps the value's
  sha256; the pane warns when the browser showing it does not hold that cookie, so a #connect=ID forwarded to the
  owner is warned of from the owner's own network too. The browser is sent to the app at
  /app/#connect=ID: the ADE's sign-in is a
  token in the page, never a cookie, so only the page can say who is signed in. There the owner reads what asks and
  what it may do, and allows it, with the confirmation a ticket takes (link._use_confirmation: one of the account's
  passkeys or its password, so a rule on which keys may confirm covers this too), or refuses it. Allowing needs the
  account's own switch on (Connected apps), which takes the same confirmation to turn on; turning it off takes the
  sign-in and revokes every grant.
- Allowing makes a grant and a code at once (the app needs its tokens within seconds), and holds the grant
  (ade/holds.py, kind `connector`, OPEN_EDITION_ADE_CONNECTOR_HOLD seconds, 180 unless set): it takes effect only when
  the hold ends, the account's devices are told at once when, and cancelling the hold revokes it, as a lost device's
  steps do (removing a passkey, signing out everywhere), since allowing is confirmed, not signed. The grant keeps the
  passkey whose confirmation allowed it (confirmed_by; none for the password's), and removing that passkey revokes
  it, in effect or waiting (passkeys_going): at once while end-to-end encryption is off, and while it is on as the
  revocation that removes the passkey is placed, not when that revocation's hold ends (ade/e2e_keys.py), so a
  connection allowed from a lost device ends with that device's passkey. Signing out everywhere ends no connection
  in effect, since nothing says the device it ends allowed one: it answers with the connections allowed in the last
  RECENT_DAYS days, each with Revoke on the page, and the account's devices are told that connected apps keep working
  (by no app's name). A code is CODE_PREFIX and 32 random bytes, kept as its sha256, good for CODE_TTL seconds and
  once.
- The token endpoint (POST /oauth/token, form-encoded, public clients naming their client_id) exchanges a code, with
  its PKCE verifier (S256), its client, its redirect address and the resource, for an access token (ACCESS_TTL
  seconds) and, where offline_access was allowed, a refresh token (REFRESH_TTL seconds); neither outlives its grant,
  which ends GRANT_TTL seconds after it was allowed. Each is 32 random bytes, kept only as its sha256. A refresh uses
  its token up and gives a new pair (rotation). A refresh token presented again, however soon, ends the grant, as a
  code presented twice does, and the owner is told: the server cannot tell the app from whoever copied the token, so
  it keeps neither's tokens (RFC 9700, 4.14.2). A token request's code or refresh token is found before any write lock
  is taken, its shape checked first. Only a request whose code or refresh token this ADE did not give, or is another
  app's, is counted, in this process's memory: by the client it names, by its network, and, for another app's, by the
  connection it belongs to; past FAILED_PER_CLIENT, FAILED_PER_NETWORK or FAILED_PER_GRANT in FAILED_WINDOW seconds
  such a request is refused (429). A request with its own app's code or refresh token is never refused for those
  counts: the Claude app names every user's connection by one client_id and calls from Anthropic's addresses, so
  whoever sends codes of their own making through Claude's callback fills that client's and that network's counts, and
  refuses nobody's real exchange or refresh. At most FAILED_KEYS counts are kept, the one counted least lately going
  first.
- What is kept past its use is cleared on a timer (prune, every PRUNE_EVERY seconds, in a thread of this process),
  never inside a request's write lock: codes and tokens a day after their end, connector events after EVENTS_KEPT,
  requests a day after they ended, registrations and documents no grant uses, the counts in this process's memory
  that have left their windows, and the apps' proposals as the notifier's round prunes them (ade/proposals.py), so
  they are pruned where that round does not run (OPEN_EDITION_ADE_PUSH_EVERY 0).
- At /mcp an access token is found by its hash with its grant: unexpired, not revoked, not ended, for this ADE's
  resource. A grant whose hold has not ended answers, but takes effect only then (ade/connector.py says what its tools
  answer meanwhile); one whose time has come takes effect at its next use, holds being settled first. A token refused
  for its grant's sake is one of the grant's events, at most once each REFUSED_EVERY seconds.
"""

import base64
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import unicodedata
import urllib.parse

from . import auth, config, store
from .ade import holds

SCOPES = ("ade.read", "ade.propose")
OFFLINE = "offline_access"
LOOPBACK = ("127.0.0.1", "::1", "localhost")
SETTING = "OPEN_EDITION_CONNECTOR"
MCP_PATH = "/mcp"
REDIRECTS_SETTING = "OPEN_EDITION_CONNECTOR_REDIRECTS"
DEFAULT_REDIRECTS = ("https://claude.ai/api/mcp/auth_callback", "http://localhost/callback",
                     "http://127.0.0.1/callback")
CLIENT_PREFIX = "ppcl-"
REDIRECTS_PER_CLIENT = 5
REDIRECT_MAX = 500
REGISTRATIONS_PER_NETWORK = 30      # kept that no grant uses, from one network
REGISTRATIONS_PER_WIDE_NETWORK = 100  # ... from one wider network
UNUSED_REGISTRATIONS = 1000         # ... in all
KEEP_UNUSED = 86400
NAME_RULE = ("1 to 60 letters, marks, digits, punctuation, symbols and plain spaces, beginning with no mark, with no "
             "quotation mark, apostrophe or anything that looks like one, no character that is not shown as itself, no "
             "mark drawn over or around the character before it, and at most 2 marks on one character")
CLIENT_HOSTS_SETTING = "OPEN_EDITION_CONNECTOR_CLIENT_HOSTS"
DEFAULT_CLIENT_HOSTS = ("claude.ai",)
DOCUMENT_MAX = 5 * 1024
DOCUMENT_WAIT = 5                 # seconds for a read in all, its lookup included
DOCUMENT_URL_MAX = 300
DOCUMENT_REDIRECT_URIS = 10       # the redirect addresses one document may list
DOCUMENT_FRESH_MIN, DOCUMENT_FRESH_MAX, DOCUMENT_FRESH = 300, 86400, 3600
DOCUMENT_READS_PER_NETWORK = 10   # reads of documents an hour asked from one network
DOCUMENT_READS_PER_WIDE_NETWORK = 30  # ... from one wider network
DOCUMENT_READS = 100              # reads of documents not known here, an hour, in all
DOCUMENT_FAILED_KEPT = 300        # seconds a read that failed is kept, its words said again without a read
DOCUMENTS_SETTING = "OPEN_EDITION_CONNECTOR_CLIENT_DOCUMENTS"
DEFAULT_DOCUMENTS = ("https://claude.ai/oauth/claude-code-client-metadata",)   # Claude Code's, as Anthropic gives it
DOCUMENTS_KNOWN = 1000            # documents read here before, kept past pruning, the least lately read going first
_document_reads = []              # (time, network, wider network, whether it drew on DOCUMENT_READS) of each read in
                                  # the last hour, in memory
_document_failures = {}           # url -> (until, why), likewise
_documents_lock = threading.Lock()
REQUEST_TTL = 600
REQUESTS_PER_NETWORK = 10           # waiting at once
REQUESTS_PER_WIDE_NETWORK = 30      # ... from one wider network
REQUESTS_WAITING = 1000             # ... in all, past which the oldest of the wider network with the most goes
STATE_MAX = 500
BODY_MAX = 64 * 1024                # a request's body at /oauth/*, held to in the app (main.py) as at /mcp
CODE_PREFIX = "ppcc_"
CODE_TTL = 60
GRANT_TTL = 90 * 86400
HOLD = "connector"
HOLD_SETTING = "OPEN_EDITION_ADE_CONNECTOR_HOLD"
HOLD_DEFAULT = 180
ACCESS_PREFIX, REFRESH_PREFIX = "ppca_", "ppcr_"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
REFRESHES_PER_HOUR = 60
# failed token requests counted (only those naming a code or refresh token this ADE did not give, or another app's)
FAILED_PER_CLIENT = 20              # in the window, naming one client_id
FAILED_PER_NETWORK = 60             # ... from one network, whatever client each names
FAILED_PER_GRANT = 20               # ... presenting one connection's code or refresh token as another app's
FAILED_WINDOW = 600
FAILED_KEYS = 10000                 # counts kept at once: past it, the one counted least lately goes
REFUSED_EVERY = 60                  # seconds between two events of one grant's token refused
SEEN_EVERY = 60                     # how often a grant's last use is written
EVENTS_KEPT = 90 * 86400
PRUNE_EVERY = 600                   # seconds between two clearings of what is kept past its use (prune)
RECENT_DAYS = 7                     # the connections signing out everywhere answers with: those allowed in these days
KEEP_WORKING = "Connected apps keep working until you revoke them in Connected apps."
REMOVED_WHY = "the passkey that allowed it was removed"
REVOCATION_WHY = "a change to end-to-end encryption removes the passkey that allowed it"
_REGISTERED_ID = re.compile(r"ppcl-[0-9a-f]{32}")        # each of these: fullmatch, never match
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_BEARER = re.compile(r"Bearer (\S{1,200})")
# the shape of every code and refresh token this ADE gives (new_token: its prefix and 32 bytes in base64url)
_SHAPES = {"code": re.compile(re.escape(CODE_PREFIX) + r"[A-Za-z0-9_-]{43}"),
           "refresh": re.compile(re.escape(REFRESH_PREFIX) + r"[A-Za-z0-9_-]{43}")}
# ("client" | "network" | "grant", which) -> times of failed requests in the window, the one counted latest last
_failed = {}
_failed_lock = threading.Lock()
ENDED = ("This request has ended: it was answered, ten minutes passed, or the ADE had too many waiting. Start "
         "connecting again in the app.")
NOT_A_PAGE = ("This address opens only as a page of its own: a page that loads it inside itself, as an image or a "
              "frame, or loads it ahead of a click, connects no app.")
ALLOWED_TITLE = "An app was allowed to connect to your ADE"     # its hold's summary: no word of the app's own
ACCOUNT_OFF = ("This account does not let apps connect: turn that on in Account, Connected apps, then connect again "
               "from the app.")
SHOWN_ANOTHER = ("This pane showed another account than the one signed in now: open the request again, and read which "
                 "account it connects.")
# approving a held command as an app proposed goes through confirm_in too, but a missing confirmation there is told in
# a held command's words (ade/approvals.py: NEEDS_CONFIRM_TO_APPROVE)
NEEDS_CONFIRM = ("Confirm it is you first, with one of this account's passkeys or its password: a sign-in alone does "
                 "not connect an app or let apps connect.")


class OAuthError(Exception):
    """A refusal at the token or registration endpoint, as RFC 6749 and RFC 7591 write one: its status, `error` and
    `error_description`."""

    def __init__(self, status, error, description):
        super().__init__(description)
        self.status, self.error, self.description = status, error, description


class ClientError(Exception):
    """Why the client an authorization names cannot be used: shown on the ADE's own page, never sent to a redirect
    address the ADE could not trust."""


class ConnectorError(Exception):
    """A refusal on the page's routes, with its HTTP status."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


class Unauthorized(Exception):
    """No working access token at /mcp (401); `given`: a token came, and does not work."""

    def __init__(self, given):
        super().__init__("A working access token is needed.")
        self.given = given


def _now(now):
    return int(now if now is not None else time.time())


def enabled():
    """Whether the connector is on: OPEN_EDITION_CONNECTOR is `on`. Read at each request, so a test sets it per test."""
    return os.environ.get(SETTING, "").strip() == "on"


def check_config():
    """Refuse to start with the connector on and a public address that is not https, but on this machine's loopback
    (the tests'): every authorization endpoint must be served over https (OAuth 2.1, 1.5)."""
    if not enabled():
        return
    parts = urllib.parse.urlsplit(config.public_url())
    if parts.scheme != "https" and (parts.hostname or "") not in LOOPBACK:
        raise RuntimeError("OPEN_EDITION_CONNECTOR is on, so OPEN_EDITION_PUBLIC_URL must be https (http only on this "
                           "machine's loopback)")


def canonical(uri):
    """`uri` as RFC 8707 and the MCP authorization spec compare a resource: lowercase scheme and host, no default port,
    no trailing slash. None for anything that is not an absolute http or https URI, or that has a query, a fragment or
    a user."""
    if not isinstance(uri, str) or not uri or len(uri) > 2000:
        return None
    try:
        parts = urllib.parse.urlsplit(uri)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http") or not parts.hostname or parts.query or parts.fragment or "#" in uri \
            or parts.username is not None or parts.password is not None:
        return None
    host = parts.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    netloc = host if port in (None, {"https": 443, "http": 80}[scheme]) else f"{host}:{port}"
    return f"{scheme}://{netloc}{parts.path.rstrip('/')}"


def issuer():
    """The authorization server's issuer: the public address's origin, as canonical() writes it."""
    parts = urllib.parse.urlsplit(config.public_url())
    return canonical(f"{parts.scheme}://{parts.netloc}")


def resource():
    """The MCP endpoint's resource identifier, which every token is bound to: the issuer and /mcp."""
    return issuer() + MCP_PATH


def protected_resource_metadata():
    """RFC 9728's document for /mcp, served at /.well-known/oauth-protected-resource/mcp and at the root form."""
    return {"resource": resource(), "authorization_servers": [issuer()], "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"], "resource_name": "Proofpane Community ADE"}


def authorization_server_metadata():
    """RFC 8414's document, served at /.well-known/oauth-authorization-server. offline_access is listed here, not in
    the resource's scopes, as the MCP authorization spec asks: a refresh token is the client's need, not the
    resource's."""
    base = issuer()
    return {"issuer": base, "authorization_endpoint": base + "/oauth/authorize",
            "token_endpoint": base + "/oauth/token", "registration_endpoint": base + "/oauth/register",
            "scopes_supported": list(SCOPES) + [OFFLINE],
            "response_types_supported": ["code"], "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": True, "authorization_response_iss_parameter_supported": True}


# ---- redirect addresses ------------------------------------------------------------------------------------------

def redirect_ok(uri):
    """Whether `uri` can be a redirect address at all: https, or http on this machine's loopback; absolute, with a
    host, no user and no fragment, at most REDIRECT_MAX characters."""
    if not isinstance(uri, str) or not 0 < len(uri) <= REDIRECT_MAX or "#" in uri:
        return False
    try:
        parts = urllib.parse.urlsplit(uri)
        parts.port
    except ValueError:
        return False
    if not parts.hostname or parts.username is not None or parts.password is not None:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK)


def _loopback(parts):
    return parts.scheme == "http" and parts.hostname in LOOPBACK


def same_redirect(listed, given):
    """Whether `given` is the redirect address `listed`: the same string, or, for two loopback addresses, the same but
    for their ports (OAuth 2.1, 8.4.2: a native app's port is chosen when it runs)."""
    if given == listed:
        return True
    a, b = urllib.parse.urlsplit(listed), urllib.parse.urlsplit(given)
    return _loopback(a) and _loopback(b) and (a.hostname, a.path, a.query) == (b.hostname, b.path, b.query)


def deployment_redirects():
    """The addresses a sign-in may return to on this deployment: OPEN_EDITION_CONNECTOR_REDIRECTS, comma-separated, or
    else the Claude app's callback and Claude Code's loopback ones; an entry that cannot be one is left out."""
    raw = os.environ.get(REDIRECTS_SETTING)
    given = DEFAULT_REDIRECTS if raw is None else tuple(u.strip() for u in raw.split(",") if u.strip())
    return tuple(u for u in given if redirect_ok(u))


def redirect_allowed(uri, listed):
    """Whether a sign-in may return to `uri`: an address at all, on the client's own list `listed`, and on the
    deployment's."""
    return redirect_ok(uri) and any(same_redirect(u, uri) for u in listed) \
        and any(same_redirect(u, uri) for u in deployment_redirects())


# what may pass for a quotation mark, beside the quotation punctuation itself (Unicode's Pi and Pf): the words of the
# names of quotation marks, apostrophes, primes, turned and reversed commas, corner brackets, the ditto mark and the
# Hebrew geresh, and the accents written apart that look like quotes
_QUOTE_WORDS = ("QUOTATION", "APOSTROPHE", "PRIME", "TURNED COMMA", "REVERSED COMMA", "CORNER BRACKET", "DITTO MARK",
                "GERESH", "GERSHAYIM")
# grave and acute accents, written apart: ASCII's, Latin-1's, the modifier letters', the fullwidth one, Greek's
_QUOTE_ACCENTS = "".join(map(chr, (0x60, 0xB4, 0x2CA, 0x2CB, 0x2DD, 0x2F5, 0x2F6, 0xFF40, 0x1FEF, 0x1FFD)))


def _quote_like(c):
    """Whether `c` is a quotation mark, or could pass for one around a name."""
    if unicodedata.category(c) in ("Pi", "Pf") or c in _QUOTE_ACCENTS:
        return True
    name = unicodedata.name(c, "")
    return any(word in name for word in _QUOTE_WORDS)


def _drawn_as_itself(name):
    """Whether a name is drawn as what it says, as an app's words must be (proposals._words): no character that is not
    shown as itself (link_commands.hidden_character: the Hangul fillers and the blank Braille pattern among them), no
    mark drawn over or around the character before it (drawn_over), at most MARKS_MAX marks on one character
    (stacked), and no mark first, which would be drawn on the quotation mark before the name. A name the rest of the
    rule allows then has something to show: it begins with a letter, digit, punctuation or symbol."""
    from .ade import link_commands
    return (link_commands.hidden_character(name) is None and link_commands.drawn_over(name) is None
            and link_commands.stacked(name) is None and unicodedata.category(name[0])[0] != "M")


def client_name(name):
    """What a client calls itself, as it is kept and shown: the rule a device's name follows (auth.device_name), since
    the owner reads it as the client's own word beside what the server knows; no quotation mark, apostrophe or
    look-alike (_quote_like), since the pane and the page's words put the name between quotation marks, and one inside
    could close them and go on as words of the ADE's own (`Claude” was refused; nothing changed`); and drawn as itself
    (_drawn_as_itself), since the pane draws it right above its warnings and the signing review right above the words
    being signed, where a stack of marks is drawn over the lines around it and a name drawn as nothing reads as
    nothing."""
    try:
        name = auth.device_name(name)
    except auth.AuthError:
        raise OAuthError(400, "invalid_client_metadata", f"A client_name is {NAME_RULE}.") from None
    if any(_quote_like(c) for c in name) or not _drawn_as_itself(name):
        raise OAuthError(400, "invalid_client_metadata", f"A client_name is {NAME_RULE}.")
    return name


# ---- clients that register themselves (RFC 7591) -------------------------------------------------------------------

# a registered client no grant uses (the column names the table's own row)
_UNUSED = ("kind = 'registered' AND NOT EXISTS (SELECT 1 FROM ade_connector_grants g WHERE "
           "g.client_id = ade_connector_clients.id)")
# one that can be pushed out at a cap: unused, and named by no request still waiting (one parameter: now)
_EVICTABLE = (_UNUSED + " AND NOT EXISTS (SELECT 1 FROM ade_connector_requests r WHERE r.client_id = "
              "ade_connector_clients.id AND r.answer IS NULL AND r.expires_at > ?)")


def _prune_clients(db, now):
    """Registrations no grant uses, after a day; documents no grant uses, a day after they were last read."""
    db.execute(f"DELETE FROM ade_connector_clients WHERE {_UNUSED} AND created_at <= ?", (now - KEEP_UNUSED,))
    db.execute("DELETE FROM ade_connector_clients WHERE kind = 'document' AND fresh_until <= ? AND NOT EXISTS "
               "(SELECT 1 FROM ade_connector_grants g WHERE g.client_id = ade_connector_clients.id)",
               (now - KEEP_UNUSED,))


def _counted(client, wide=False):
    """The network a request is counted by (auth.network_of), or with `wide` the wider one around it (an IPv4 /24, an
    IPv6 /48, which one holder of many addresses usually has); for an address the server cannot read (a test client),
    a count of its own."""
    net = auth.network_of(client, wide=wide)
    return net if net is not None else "unread:" + str(client or "")[:200]


def _push_out(db, now, where="1", args=()):
    """Inside the caller's transaction: the oldest registration matching `where` (SQL, with `args`) that can go
    (_EVICTABLE) is deleted. Whether one was."""
    row = db.execute(f"SELECT id FROM ade_connector_clients WHERE {_EVICTABLE} AND {where} ORDER BY created_at, rowid "
                     "LIMIT 1", (now, *args)).fetchone()
    if row is not None:
        db.execute("DELETE FROM ade_connector_clients WHERE id = ?", (row["id"],))
    return row is not None


def register(data, client="", now=None):
    """A client registers itself: {redirect_uris, client_name?, token_endpoint_auth_method?, grant_types?,
    response_types?}, from the address `client`. Returns what RFC 7591 answers with 201: the new client_id and what
    was registered; there is no secret. OAuthError for anything this deployment does not take."""
    now = _now(now)
    if not isinstance(data, dict):
        raise OAuthError(400, "invalid_client_metadata", "A registration is a JSON object.")
    uris = data.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= REDIRECTS_PER_CLIENT \
            or not all(isinstance(u, str) for u in uris):
        raise OAuthError(400, "invalid_redirect_uri", f"redirect_uris is a list of 1 to {REDIRECTS_PER_CLIENT} "
                                                      "addresses.")
    allowed = deployment_redirects()
    for uri in uris:
        if not redirect_ok(uri) or not any(same_redirect(u, uri) for u in allowed):
            raise OAuthError(400, "invalid_redirect_uri", "This ADE returns a sign-in only to the addresses its "
                                                          f"operator allows, not to {uri[:200]}.")
    if data.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError(400, "invalid_client_metadata", "This ADE takes public clients only: "
                                                         "token_endpoint_auth_method is none, and there is no secret.")
    grant_types = data.get("grant_types", ["authorization_code"])
    if not isinstance(grant_types, list) or "authorization_code" not in grant_types \
            or not all(g in ("authorization_code", "refresh_token") for g in grant_types):
        raise OAuthError(400, "invalid_client_metadata", "grant_types are authorization_code, and refresh_token if "
                                                         "wanted.")
    if data.get("response_types", ["code"]) != ["code"]:
        raise OAuthError(400, "invalid_client_metadata", 'response_types is ["code"].')
    name = client_name(data.get("client_name", "An app"))
    counted, widely, client_id = _counted(client), _counted(client, wide=True), CLIENT_PREFIX + secrets.token_hex(16)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _prune_clients(db, now)
        # at a cap, a network's own oldest that can go makes room: never refused for others' registrations
        for column, value, most, which in (
                ("net", counted, REGISTRATIONS_PER_NETWORK, "this network"),
                ("wide", widely, REGISTRATIONS_PER_WIDE_NETWORK, "the wider network around this address")):
            if db.execute(f"SELECT COUNT(*) FROM ade_connector_clients WHERE {_UNUSED} AND {column} = ?",
                          (value,)).fetchone()[0] >= most and not _push_out(db, now, f"{column} = ?", (value,)):
                raise OAuthError(429, "invalid_request", f"Each of the {most} apps {which} registered and has not "
                                                         "connected is midway through being allowed: try again in ten "
                                                         "minutes.")
        # and in all, the oldest of the wider network that has the most that can go
        if db.execute(f"SELECT COUNT(*) FROM ade_connector_clients WHERE {_UNUSED}").fetchone()[0] \
                >= UNUSED_REGISTRATIONS:
            most = db.execute(f"SELECT wide FROM ade_connector_clients WHERE {_EVICTABLE} GROUP BY wide ORDER BY "
                              "COUNT(*) DESC, MIN(created_at) LIMIT 1", (now,)).fetchone()
            if most is None or not _push_out(db, now, "wide IS ?", (most["wide"],)):
                raise OAuthError(429, "invalid_request", f"This ADE keeps {UNUSED_REGISTRATIONS} apps registered and "
                                                         "not yet connected, each midway through being allowed: try "
                                                         "again in ten minutes.")
        db.execute("INSERT INTO ade_connector_clients (id, kind, name, redirect_uris, created_at, net, wide) VALUES "
                   "(?,?,?,?,?,?,?)", (client_id, "registered", name, json.dumps(uris), now, counted, widely))
    return {"client_id": client_id, "client_id_issued_at": now, "client_name": name, "redirect_uris": uris,
            "grant_types": sorted(set(grant_types)), "response_types": ["code"], "token_endpoint_auth_method": "none"}


# ---- clients named by a Client ID Metadata Document ----------------------------------------------------------------

def client_hosts():
    """The hosts whose documents the ADE reads: OPEN_EDITION_CONNECTOR_CLIENT_HOSTS, comma-separated, or claude.ai."""
    raw = os.environ.get(CLIENT_HOSTS_SETTING)
    given = DEFAULT_CLIENT_HOSTS if raw is None else raw.split(",")
    return tuple(h.strip().lower() for h in given if h.strip())


def document_url_ok(url):
    """Whether `url` can be a document's client_id (the draft's section 3): https, on port 443, with a host and a path,
    and no user, query, fragment, or dot segment; at most DOCUMENT_URL_MAX characters."""
    if not isinstance(url, str) or not 0 < len(url) <= DOCUMENT_URL_MAX or "#" in url or "?" in url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return parts.scheme == "https" and port in (None, 443) and bool(parts.hostname) and parts.username is None \
        and parts.password is None and parts.path not in ("", "/") \
        and not any(s in (".", "..") for s in parts.path.split("/"))


def _within(deadline, work):
    """work() in a thread of its own, waited for until `deadline` (by time.monotonic()): what it returns, or what it
    raises; TimeoutError once the deadline passes (the thread ends on its own, at its sockets' timeouts)."""
    done = {}

    def run():
        try:
            done["value"] = work()
        except BaseException as e:  # noqa: BLE001  raised again in the caller
            done["error"] = e
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(max(0.0, deadline - time.monotonic()))
    if thread.is_alive():
        raise TimeoutError(f"no answer within {DOCUMENT_WAIT} seconds")
    if "error" in done:
        raise done["error"]
    return done["value"]


def _resolve(host):
    """Every address `host` resolves to."""
    return [info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


def _addresses(host, deadline):
    """Every address `host` resolves to, looked up before `deadline` (a lookup counts in a read's time)."""
    return _within(deadline, lambda: _resolve(host))


def _all_global(addresses):
    """Whether every one of `addresses` is a global one: never a private, loopback or other special-use one (the
    draft's section 8.6), so a name that resolves inward cannot make the ADE fetch from inside."""
    try:
        return bool(addresses) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses)
    except ValueError:
        return False


def _pinned(conn, address):
    """`conn` made to connect at `address`, the one its host's name was checked to resolve to, so no second lookup
    can send it elsewhere; the name is still what its Host header and, over TLS, the certificate are for."""
    conn._create_connection = lambda where, timeout=None, source=None: socket.create_connection(
        (address, where[1]), timeout, source)
    return conn


def _get(conn, path):
    """(status, Cache-Control, body) of a GET of `path` on `conn`: a redirect answered as it came, never followed (the
    draft's section 5), and at most DOCUMENT_MAX + 1 bytes read."""
    try:
        conn.request("GET", path, headers={"Accept": "application/json", "User-Agent": "Proofpane-ADE"})
        r = conn.getresponse()
        if r.status != 200:
            return r.status, "", b""
        return r.status, r.getheader("Cache-Control", ""), r.read(DOCUMENT_MAX + 1)
    finally:
        conn.close()


def fetch_document(url, address, deadline):
    """(status, Cache-Control, body) of a GET of `url` over TLS at `address`, its host's address as checked, all of it
    before `deadline`, the lookup's deadline (_read_document)."""
    parts = urllib.parse.urlsplit(url)
    conn = _pinned(http.client.HTTPSConnection(parts.hostname, 443, timeout=max(0.1, deadline - time.monotonic()),
                                               context=ssl.create_default_context()), address)
    try:
        return _within(deadline, lambda: _get(conn, parts.path))
    except TimeoutError:
        conn.close()                    # its thread's read ends with the socket
        raise


def _count_read(client, now, shared=True):
    """Count a read of a document asked from the address `client` before it is made: refused (ClientError) past
    DOCUMENT_READS_PER_NETWORK an hour from one network, DOCUMENT_READS_PER_WIDE_NETWORK from one wider network, or,
    for a read that is `shared` (of a document not known here), DOCUMENT_READS such reads an hour in all."""
    net, wide = _counted(client), _counted(client, wide=True)
    with _documents_lock:
        _document_reads[:] = [r for r in _document_reads if r[0] > now - 3600]
        for at, value, most, which in ((1, net, DOCUMENT_READS_PER_NETWORK, "one network"),
                                       (2, wide, DOCUMENT_READS_PER_WIDE_NETWORK, "one wider network (an IPv4 /24, an "
                                                                                  "IPv6 /48)")):
            if sum(1 for r in _document_reads if r[at] == value) >= most:
                raise ClientError(f"This ADE reads app descriptions at most {most} times an hour for {which}, and has "
                                  "for this one: try again later.")
        if shared and sum(1 for r in _document_reads if r[3]) >= DOCUMENT_READS:
            raise ClientError(f"This ADE reads app descriptions at most {DOCUMENT_READS} times an hour, and has: try "
                              "again later.")
        _document_reads.append((now, net, wide, shared))


def client_documents():
    """The documents read whenever they are stale, drawing on none of DOCUMENT_READS, as one known here is:
    OPEN_EDITION_CONNECTOR_CLIENT_DOCUMENTS, exact client_ids, comma-separated, or Claude Code's."""
    raw = os.environ.get(DOCUMENTS_SETTING)
    given = DEFAULT_DOCUMENTS if raw is None else raw.split(",")
    return tuple(u.strip() for u in given if u.strip())


def _failed_read(url, now):
    """Why a read of `url` failed, while that is kept (DOCUMENT_FAILED_KEPT seconds), else None."""
    with _documents_lock:
        for key in [k for k, (until, _) in _document_failures.items() if until <= now]:
            del _document_failures[key]
        kept = _document_failures.get(url)
    return kept[1] if kept else None


def _fresh_for(cache_control):
    """How long a document is kept: its max-age, held between DOCUMENT_FRESH_MIN and DOCUMENT_FRESH_MAX; the least for
    one that asks not to be kept; DOCUMENT_FRESH without either."""
    cc = (cache_control or "").lower()
    if "no-store" in cc or "no-cache" in cc:
        return DOCUMENT_FRESH_MIN
    m = re.search(r"(?:^|,)\s*max-age\s*=\s*(\d{1,10})\s*(?:,|$)", cc)
    return min(max(int(m.group(1)), DOCUMENT_FRESH_MIN), DOCUMENT_FRESH_MAX) if m else DOCUMENT_FRESH


def _client_view(row):
    return {"id": row["id"], "kind": row["kind"], "name": row["name"],
            "redirect_uris": json.loads(row["redirect_uris"]),
            "host": urllib.parse.urlsplit(row["id"]).hostname if row["kind"] == "document" else None}


def document_client(url, now=None, client=""):
    """The client a Client ID Metadata Document at `url` describes, from a trusted host only, read and checked as the
    draft says and kept until it is stale; a read asked from the address `client`, counted before it is made (one
    known here, held, read before or named in client_documents(), by its networks' counts only), and when a count
    refuses to read again one held but stale, the one held. A read that fails is not kept as a client, and its failure
    is kept a while instead, so the next ask for it is answered without a read. ClientError says why it cannot be
    used."""
    now = _now(now)
    if not document_url_ok(url):
        raise ClientError("The app's client_id is not an address a description can be read from: https on port 443, "
                          "with a path, and no query, fragment, user or dot segment.")
    host = urllib.parse.urlsplit(url).hostname
    if host not in client_hosts():
        raise ClientError(f"This ADE reads app descriptions only from {', '.join(client_hosts()) or 'no host'}, not "
                          f"from {host}.")
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_connector_clients WHERE id = ? AND kind = 'document'", (url,)).fetchone()
        known = row is not None or url in client_documents() or db.execute(
            "SELECT 1 FROM ade_connector_documents_known WHERE url = ?", (url,)).fetchone() is not None
    if row is not None and row["fresh_until"] > now:
        return _client_view(row)
    failed = _failed_read(url, now)
    if failed:
        raise ClientError(failed)
    try:
        # one held, read here before or named by the operator draws on none of the shared count, which others' reads
        # of any number of paths that do not exist can spend; its network's counts still hold
        _count_read(client, now, shared=not known)
    except ClientError:
        if row is not None:
            return _client_view(row)        # held, stale: its own network's reads keep no app known here away
        raise
    try:
        name, uris, cache_control = _read_document(url, host)
    except ClientError as e:
        with _documents_lock:
            _document_failures[url] = (now + DOCUMENT_FAILED_KEPT, str(e))
        raise
    with store.connect() as db:
        db.execute("INSERT INTO ade_connector_clients (id, kind, name, redirect_uris, created_at, fresh_until) VALUES "
                   "(?, 'document', ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET name = excluded.name, redirect_uris = "
                   "excluded.redirect_uris, fresh_until = excluded.fresh_until",
                   (url, name, json.dumps(uris), now, now + _fresh_for(cache_control)))
        db.execute("INSERT INTO ade_connector_documents_known (url, read_at) VALUES (?, ?) ON CONFLICT (url) DO UPDATE "
                   "SET read_at = excluded.read_at", (url, now))
        db.execute("DELETE FROM ade_connector_documents_known WHERE url NOT IN (SELECT url FROM "
                   "ade_connector_documents_known ORDER BY read_at DESC, url LIMIT ?)", (DOCUMENTS_KNOWN,))
        row = db.execute("SELECT * FROM ade_connector_clients WHERE id = ?", (url,)).fetchone()
    return _client_view(row)


def _read_document(url, host):
    """A document read and checked: (name, redirect_uris, Cache-Control). Its host's name is looked up and its every
    address checked, the read goes to the address checked, and the lookup and the read together take at most
    DOCUMENT_WAIT seconds."""
    deadline = time.monotonic() + DOCUMENT_WAIT
    try:
        addresses = _addresses(host, deadline)
    except Exception as e:  # noqa: BLE001  a name that cannot be looked up names no client
        raise ClientError(f"{host} could not be looked up ({type(e).__name__}).") from None
    if not _all_global(addresses):
        raise ClientError(f"{host} does not resolve to a public address, so its description is not read.")
    try:
        status, cache_control, body = fetch_document(url, addresses[0], deadline)
    except Exception as e:  # noqa: BLE001  a description that cannot be read is no client, whatever went wrong
        raise ClientError(f"The app's description at {url} could not be read ({type(e).__name__}).") from None
    if status != 200:
        raise ClientError(f"The app's description at {url} answered {status}.")
    if len(body) > DOCUMENT_MAX:
        raise ClientError(f"The app's description is larger than {DOCUMENT_MAX // 1024} KB.")
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ClientError("The app's description is not JSON.") from None
    if not isinstance(doc, dict) or doc.get("client_id") != url:
        raise ClientError("The app's description names another client_id than its own address.")
    if "client_secret" in doc or "client_secret_expires_at" in doc \
            or doc.get("token_endpoint_auth_method", "none") != "none":
        raise ClientError("The app's description holds or asks for a client secret; this ADE takes public clients "
                          "only.")
    grants = doc.get("grant_types", ["authorization_code"])
    if not isinstance(grants, list) or "authorization_code" not in grants:
        raise ClientError("The app's description does not ask for the authorization code grant.")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= DOCUMENT_REDIRECT_URIS \
            or not all(redirect_ok(u) for u in uris):
        raise ClientError(f"The app's description's redirect_uris are not 1 to {DOCUMENT_REDIRECT_URIS} addresses, "
                          "each https or http on loopback.")
    try:
        name = client_name(doc.get("client_name"))
    except OAuthError:
        raise ClientError(f"The app's description's client_name is not {NAME_RULE}.") from None
    return name, uris, cache_control


def client_for(client_id, now=None, client=""):
    """The client an authorization names: one named by its document (an https URL; a read asked from the address
    `client`), or one that registered itself here. ClientError says why it cannot be used."""
    if isinstance(client_id, str) and client_id.startswith("https://"):
        return document_client(client_id, now, client)
    row = None
    if isinstance(client_id, str) and _REGISTERED_ID.fullmatch(client_id):
        with store.connect() as db:
            row = db.execute("SELECT * FROM ade_connector_clients WHERE id = ? AND kind = 'registered'",
                             (client_id,)).fetchone()
    if row is None:
        raise ClientError("No app is registered here with that client_id: an app that registered more than a day ago "
                          "without connecting registers again.")
    return _client_view(row)


# ---- authorization: the request, the owner's consent, the code -----------------------------------------------------

def token_hash(token):
    """What a code or token is kept as: its sha256 in hex (each is 32 random bytes, so no slower hash is needed)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(prefix):
    return prefix + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def with_query(uri, params):
    """`uri` with `params` added to its query, a value of None left out."""
    parts = urllib.parse.urlsplit(uri)
    extra = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None])
    query = parts.query + ("&" if parts.query and extra else "") + extra
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def requested_scopes(raw):
    """The scopes an authorization asks for, in this order, ade.read always among them; none asked is ade.read and
    ade.propose; None for a scope this ADE does not have."""
    if raw is None or raw == "":
        return list(SCOPES)
    if not isinstance(raw, str) or any(s not in SCOPES + (OFFLINE,) for s in raw.split()):
        return None
    return ["ade.read"] + [s for s in ("ade.propose", OFFLINE) if s in raw.split()]


def record(db, user_id, grant_id, kind, what=None, subject=None, outcome="ok", net=None, now=None):
    """Inside the caller's transaction: one of the account's connector events."""
    db.execute("INSERT INTO ade_connector_events (user_id, grant_id, at, kind, what, subject, outcome, net) VALUES "
               "(?,?,?,?,?,?,?,?)", (user_id, grant_id, _now(now), kind, what, subject, outcome, net))


def _header(came, name):
    """A header's value as `came` holds it, in lowercase; None when the browser sent none, or sent it empty."""
    value = (came or {}).get(name)
    return (value.strip().lower() or None) if isinstance(value, str) else None


FETCH_SITES = ("cross-site", "same-site", "same-origin", "none")      # what a browser's Sec-Fetch-Site says
BROWSER_COOKIE = "__Host-ppc-"      # and a request's id: the cookie that binds it to the browser that made it


def browser_cookie(request_id):
    """The name of the cookie /oauth/authorize sets in the browser that made a request (connector_routes): __Host-
    keeps it to this origin, sent only over https, at /; it lives as long as the request (REQUEST_TTL)."""
    return BROWSER_COOKIE + request_id


def browser_digest(value):
    """What a request keeps of its cookie's value: the sha256, so its row alone cannot make a cookie that matches. None
    for no value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if isinstance(value, str) and value else None


def _origin(url):
    """The origin of `url` (an absolute http or https one, as canonical() writes it), or None."""
    try:
        parts = urllib.parse.urlsplit(url) if isinstance(url, str) and len(url) <= 2000 else None
    except ValueError:
        return None
    return canonical(f"{parts.scheme}://{parts.netloc}") if parts and parts.netloc else None


def authorize(params, client="", now=None, repeated=False, came=None, browser=None):
    """What /oauth/authorize does with a request's parameters, from the address `client`: ("page", the consent pane's
    address) once the request is kept; ("back", the redirect address with an error) when the client and its redirect
    address hold but the rest does not; ("error", why) when the client or the redirect address does not hold, which
    is never redirected to, or when the browser says the request is not a top-level navigation, or one made ahead of
    use. `repeated`: a parameter came more than once. `came`: what the browser said of the request ({mode, dest,
    site, referer, purpose, legacy_purpose}: its Sec-Fetch-Mode, Sec-Fetch-Dest and Sec-Fetch-Site, its Referer, and
    its Sec-Purpose and Purpose, each None when it sent none); the request kept keeps its Sec-Fetch-Site and the
    Referer's origin, which the consent pane compares with the app's (request_view).
    `browser`: the sha256 of the value of the cookie the route sets in the browser that made the request
    (browser_digest), kept for the pane to compare."""
    now = _now(now)
    # before any client is read: an image or a frame on another site's page asks nothing of the ADE; nor does a
    # prefetch or a prerender that a page's speculation rules have its visitors' browsers make, which comes as a
    # top-level navigation but says it is made ahead of use: a Sec-Purpose of any value, or the older Purpose
    if _header(came, "mode") not in (None, "navigate") or _header(came, "dest") not in (None, "document") \
            or _header(came, "purpose") is not None or _header(came, "legacy_purpose") is not None:
        return "error", NOT_A_PAGE
    redirect_uri, state = params.get("redirect_uri"), params.get("state")
    try:
        found = client_for(params.get("client_id"), now, client)
    except ClientError as e:
        return "error", str(e)
    if not isinstance(redirect_uri, str) or not redirect_allowed(redirect_uri, found["redirect_uris"]):
        return "error", ("The app asked for its sign-in to return to an address that is not on its own list, or that "
                         "this ADE does not allow, so nothing is sent there.")

    def back(error, description):
        kept = state if isinstance(state, str) and len(state) <= STATE_MAX else None
        return "back", with_query(redirect_uri, {"error": error, "error_description": description, "state": kept,
                                                 "iss": issuer()})
    if repeated:
        return back("invalid_request", "A parameter was given more than once.")
    if params.get("response_type") != "code":
        return back("unsupported_response_type", "This ADE gives an authorization code only.")
    if state is not None and (not isinstance(state, str) or len(state) > STATE_MAX):
        return back("invalid_request", f"A state is at most {STATE_MAX} characters.")
    challenge = params.get("code_challenge")
    if params.get("code_challenge_method") != "S256" or not isinstance(challenge, str) \
            or not _CHALLENGE.fullmatch(challenge):
        return back("invalid_request", "PKCE is required: code_challenge_method S256, and a code_challenge of 43 "
                                       "base64url characters.")
    if canonical(params.get("resource")) != resource():
        return back("invalid_target", f"The resource is this ADE's MCP endpoint, {resource()}.")
    scopes = requested_scopes(params.get("scope"))
    if scopes is None:
        return back("invalid_scope", "This ADE's scopes are ade.read, ade.propose and offline_access.")
    counted, widely, request_id = _counted(client), _counted(client, wide=True), secrets.token_hex(16)
    site = _header(came, "site") if _header(came, "site") in FETCH_SITES else None
    came_from = _origin((came or {}).get("referer"))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        waiting = "answer IS NULL AND expires_at > ?"
        for column, value, most, which in (
                ("asked_net", counted, REQUESTS_PER_NETWORK, "This network"),
                ("asked_wide", widely, REQUESTS_PER_WIDE_NETWORK, "The wider network around this address (its /24, "
                                                                  "or its /48)")):
            if db.execute(f"SELECT COUNT(*) FROM ade_connector_requests WHERE {column} = ? AND {waiting}",
                          (value, now)).fetchone()[0] >= most:
                return "error", (f"{which} has {most} requests to connect an app waiting, as many as it may: answer "
                                 "them, or wait ten minutes.")
        # at the cap in all, the oldest of the wider network that has the most waiting goes: a flood from many
        # networks pushes out its own, and refuses no one
        if db.execute(f"SELECT COUNT(*) FROM ade_connector_requests WHERE {waiting}", (now,)).fetchone()[0] \
                >= REQUESTS_WAITING:
            most = db.execute(f"SELECT asked_wide FROM ade_connector_requests WHERE {waiting} GROUP BY asked_wide "
                              "ORDER BY COUNT(*) DESC, MIN(created_at) LIMIT 1", (now,)).fetchone()
            db.execute(f"DELETE FROM ade_connector_requests WHERE id = (SELECT id FROM ade_connector_requests WHERE "
                       f"{waiting} AND asked_wide IS ? ORDER BY created_at, rowid LIMIT 1)", (now, most["asked_wide"]))
        db.execute("INSERT INTO ade_connector_requests (id, client_id, redirect_uri, state, code_challenge, scopes, "
                   "asked_from, asked_net, asked_wide, created_at, expires_at, fetch_site, came_from, browser) VALUES "
                   "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (request_id, found["id"], redirect_uri, state, challenge, " ".join(scopes),
                    client if auth.network_of(client) is not None else None, counted, widely, now,
                    now + REQUEST_TTL, site, came_from, browser))
    return "page", f"{issuer()}/app/#connect={request_id}"


def _waiting_request(db, request_id, now):
    if not isinstance(request_id, str) or not _HEX32.fullmatch(request_id):
        raise ConnectorError(404, ENDED)
    row = db.execute("SELECT * FROM ade_connector_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None or row["answer"] is not None or row["expires_at"] <= now:
        raise ConnectorError(404, ENDED)
    found = db.execute("SELECT * FROM ade_connector_clients WHERE id = ?", (row["client_id"],)).fetchone()
    if found is None:
        raise ConnectorError(404, ENDED)
    return row, found


def _account_on(db, user_id):
    row = db.execute("SELECT connector_on FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row is not None and row["connector_on"])


def confirm_in(db, user_id, confirmation, now, subject):
    """Inside the change's own transaction: use up the account's confirmation, through the code a ticket's goes through
    (link._use_confirmation), so a rule on which keys may confirm (high security's) covers the connector too: allowing
    an app, turning the account's switch on, and approving a held command as an app proposed (ade/approvals.py). It
    must be made for `subject`, that change (passkeys.CHANGE). Returns what it used (passkeys.Confirmed: its kind and
    the passkey that made it, None for the password), or None when the account has nothing to confirm with."""
    from .ade import link
    try:
        return link._use_confirmation(db, user_id, confirmation, now, subject)
    except link.LinkError as e:
        raise ConnectorError(e.status, NEEDS_CONFIRM if str(e) == link.LINK_NEEDS_CONFIRM else str(e)) from None


def request_view(user, request_id, viewer="", now=None, browser=None):
    """What the consent pane shows of a request waiting for an answer: the account it would connect (its id, which
    Allow sends back) and whether that account lets apps connect; what the app calls itself; where its description was
    read (a document's) and where its sign-in returns (a loopback one's port too); the scopes asked; when it asked and
    from what address, as the server saw it, and whether that is the network of the pane's own browser (`viewer`), as
    device_signin_view says it (None when either address is unknown); whether the pane's browser is the one that made
    the request, which holds the cookie (browser_cookie) whose value `browser` is (same_browser: None for a request
    kept with no cookie; a #connect=ID forwarded to the owner shows in another browser, whatever its network); how its
    browser said it arrived (its Sec-Fetch-Site and the Referer's origin) and whether that was a cross-site navigation
    from a page of the app's own site, the origin its sign-in returns to (from_app_page: an address forwarded to the
    owner opens in the owner's own browser, on the owner's own network, and arrives as no such navigation); how long a
    connection waits before it takes effect and how many days it lasts. 404 for one unknown, answered or expired."""
    now = _now(now)
    with store.connect() as db:
        row, found = _waiting_request(db, request_id, now)
        on = _account_on(db, user["id"])
    redirect = urllib.parse.urlsplit(row["redirect_uri"])
    mine, theirs = auth.network_of(viewer), auth.network_of(row["asked_from"])
    return {"id": row["id"], "account": user["email"], "account_id": user["id"], "account_on": on,
            "client_name": found["name"],
            "described_at": urllib.parse.urlsplit(found["id"]).hostname if found["kind"] == "document" else None,
            "returns_to": redirect.hostname, "loopback": redirect.hostname in LOOPBACK,
            "port": redirect.port if redirect.hostname in LOOPBACK else None, "scopes": row["scopes"].split(),
            "asked_at": row["created_at"], "asked_from": row["asked_from"],
            "viewer": viewer if mine is not None else None,
            "same_network": None if mine is None or theirs is None else mine == theirs,
            "same_browser": None if row["browser"] is None
            else secrets.compare_digest(browser_digest(browser) or "", row["browser"]),
            "fetch_site": row["fetch_site"], "came_from": row["came_from"],
            "from_app_page": row["fetch_site"] == "cross-site" and row["came_from"] is not None
            and row["came_from"] == _origin(row["redirect_uri"]),
            "expires_at": row["expires_at"], "hold_seconds": holds.delay(HOLD), "lasts_days": GRANT_TTL // 86400}


def allow(user, request_id, confirmation=None, propose=True, now=None, client="", account=None):
    """The owner allows a waiting request, with the account's confirmation, while the account lets apps connect: a
    grant held (HOLD) until its time, and a code for the app, in one transaction; the account's devices are told at
    once. `account` is the account the pane showed (request_view's account_id): another than the one signed in now
    (the pane loaded before a sign-in changed) is refused. `propose` False leaves ade.propose out. {redirect: the app's
    address with the code, the state and the issuer, hold, grant}."""
    now = _now(now)
    if account != user["id"]:
        raise ConnectorError(409, SHOWN_ANOTHER)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row, found = _waiting_request(db, request_id, now)
        if not _account_on(db, user["id"]):
            raise ConnectorError(409, ACCOUNT_OFF)
        # made for this request, and for whether it lets the app propose, as the pane showed it
        used = confirm_in(db, user["id"], confirmation, now,
                          f"connector-allow:{row['id']}:{'propose' if propose else 'read'}")
        scopes = [s for s in row["scopes"].split() if propose or s != "ade.propose"]
        grant_id, code = secrets.token_hex(16), new_token(CODE_PREFIX)
        # the passkey whose confirmation allowed it, so removing that passkey revokes it (passkeys_going)
        db.execute("INSERT INTO ade_connector_grants (id, user_id, client_id, client_name, document_host, "
                   "redirect_uri, scopes, resource, allowed_at, ends_at, confirmed_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (grant_id, user["id"], found["id"], found["name"],
                    urllib.parse.urlsplit(found["id"]).hostname if found["kind"] == "document" else None,
                    row["redirect_uri"], " ".join(scopes), resource(), now, now + GRANT_TTL,
                    used.passkey_id if used else None))
        hold = holds.place(db, user["id"], HOLD, grant_id, ALLOWED_TITLE, now)
        db.execute("UPDATE ade_connector_grants SET hold_id = ?, takes_effect_at = ? WHERE id = ?",
                   (hold["id"], hold["takes_effect_at"], grant_id))
        db.execute("INSERT INTO ade_connector_tokens (hash, grant_id, kind, created_at, expires_at, code_challenge, "
                   "redirect_uri) VALUES (?, ?, 'code', ?, ?, ?, ?)",
                   (token_hash(code), grant_id, now, now + CODE_TTL, row["code_challenge"], row["redirect_uri"]))
        db.execute("UPDATE ade_connector_requests SET answer = 'allowed', user_id = ?, grant_id = ?, answered_at = ? "
                   "WHERE id = ?", (user["id"], grant_id, now, row["id"]))
        record(db, user["id"], grant_id, "allowed", net=auth.network_of(client), now=now)
    told = holds.tell(hold, now)
    return {"redirect": with_query(row["redirect_uri"], {"code": code, "state": row["state"], "iss": issuer()}),
            "hold": dict(hold, told=told), "grant": grant_id}


def refuse(user, request_id, now=None):
    """The owner refuses a waiting request: {redirect: the app's address with access_denied, the state and the
    issuer}."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row, _ = _waiting_request(db, request_id, now)
        db.execute("UPDATE ade_connector_requests SET answer = 'refused', user_id = ?, answered_at = ? WHERE id = ?",
                   (user["id"], now, row["id"]))
    return {"redirect": with_query(row["redirect_uri"], {"error": "access_denied", "error_description": "The owner "
                                                         "refused it.", "state": row["state"], "iss": issuer()})}


# ---- tokens --------------------------------------------------------------------------------------------------------

_NEEDED = {"code": (("code", "client_id", "redirect_uri", "code_verifier"),
                     "A code exchange gives code, client_id, redirect_uri and code_verifier."),
           "refresh": (("refresh_token", "client_id"), "A refresh gives refresh_token and client_id.")}
_WHOSE = {"client": "naming this app", "network": "from this network",
          "grant": "with this connection's code or refresh token"}


def _prune_failures(now):
    """Under _failed_lock: the keys with nothing in the window go."""
    for key in [k for k, times in _failed.items() if times[-1] <= now - FAILED_WINDOW]:
        del _failed[key]


def _found(kind, given):
    """A code or refresh token (`kind`) as a token request names it, looked up with no write lock taken: {used_at,
    grant_id, client_id: its connection's app}, or None for one this ADE did not give, which a shape this ADE never
    gives is without a lookup, or one of an account no longer there."""
    if not _SHAPES[kind].fullmatch(given):
        return None
    with store.connect() as db:
        row = db.execute("SELECT t.used_at, t.grant_id, g.client_id FROM ade_connector_tokens t JOIN "
                         "ade_connector_grants g ON g.id = t.grant_id JOIN users u ON u.id = g.user_id WHERE t.hash = ? "
                         "AND t.kind = ?", (token_hash(given), kind)).fetchone()
    return dict(row) if row is not None else None


def _count_failure(client_id, client, grant_id, now):
    """A token request that names a code or refresh token this ADE did not give, or another app's: counted by the
    client it names, by the network it came from (the address `client`) and, for another app's, by the connection
    (`grant_id`) it belongs to. Refused (429), and not counted again, when one of those has FAILED_PER_CLIENT,
    FAILED_PER_NETWORK or FAILED_PER_GRANT in the window. A count is kept last once counted, and past FAILED_KEYS counts
    the first goes: a flood of new names can clear the counts it pushes out, which lets through more of what is refused
    as unknown anyway, and never refuses anything else."""
    keys = [(("client", client_id[:300]), FAILED_PER_CLIENT), (("network", _counted(client)), FAILED_PER_NETWORK)]
    if grant_id is not None:
        keys.append((("grant", grant_id), FAILED_PER_GRANT))
    with _failed_lock:
        for key, most in keys:
            if sum(1 for t in _failed.get(key, ()) if t > now - FAILED_WINDOW) >= most:
                raise OAuthError(429, "invalid_request", f"Too many failed token requests {_WHOSE[key[0]]}: try "
                                                         "again in ten minutes.")
        for key, _ in keys:
            _failed[key] = [t for t in _failed.pop(key, ()) if t > now - FAILED_WINDOW] + [now]
        while len(_failed) > FAILED_KEYS:
            del _failed[next(iter(_failed))]


def _issue(db, grant, scopes, now):
    """Inside the caller's transaction: an access token for `scopes`, and a refresh token when the grant has
    offline_access, neither past the grant's end. Returns the token response."""
    access = new_token(ACCESS_PREFIX)
    until = min(now + ACCESS_TTL, grant["ends_at"])
    db.execute("INSERT INTO ade_connector_tokens (hash, grant_id, kind, created_at, expires_at, scopes) VALUES "
               "(?, ?, 'access', ?, ?, ?)", (token_hash(access), grant["id"], now, until, " ".join(scopes)))
    out = {"access_token": access, "token_type": "Bearer", "expires_in": until - now, "scope": " ".join(scopes)}
    if OFFLINE in grant["scopes"].split():
        refresh = new_token(REFRESH_PREFIX)
        db.execute("INSERT INTO ade_connector_tokens (hash, grant_id, kind, created_at, expires_at) VALUES "
                   "(?, ?, 'refresh', ?, ?)", (token_hash(refresh), grant["id"], now,
                                               min(now + REFRESH_TTL, grant["ends_at"])))
        out["refresh_token"] = refresh
    return out


def _tell_disconnected(grant, now=None):
    """Tell the account's devices at once, by Web Push, that a connection was revoked for its code or a refresh token
    used twice: in fixed words (push.words), never what the app calls itself."""
    from .ade import push
    push.notice(grant["user_id"], push.words("app-disconnected", key="connector:" + grant["id"]), now)


def _s256(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def prune(now=None):
    """What the connector keeps past its use, cleared: codes and tokens a day after their end, connector events older
    than EVENTS_KEPT, requests a day after they ended, registrations and documents no grant uses (_prune_clients); in
    this process's memory, the failed token requests and the reads of documents that have left their windows and the
    failed reads kept no longer; and the apps' proposals, as the notifier's round prunes them (proposals.prune: one past
    its time written expired, the words of one decided a day ago cleared, one decided 90 days ago deleted), since that
    round does not run where OPEN_EDITION_ADE_PUSH_EVERY is 0, and their words would be kept in the clear for good. Run
    on a timer (start), each in a transaction of its own, never in a request's write lock: every count reads its own
    window, so nothing waits on it."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM ade_connector_tokens WHERE expires_at <= ?", (now - 86400,))
        db.execute("DELETE FROM ade_connector_events WHERE at <= ?", (now - EVENTS_KEPT,))
        db.execute("DELETE FROM ade_connector_requests WHERE expires_at <= ?", (now - KEEP_UNUSED,))
        _prune_clients(db, now)
    with _failed_lock:
        _prune_failures(now)
    with _documents_lock:
        _document_reads[:] = [r for r in _document_reads if r[0] > now - 3600]
        for key in [k for k, (until, _) in _document_failures.items() if until <= now]:
            del _document_failures[key]
    from .ade import proposals
    proposals.prune(now)


def start(every=PRUNE_EVERY):
    """Run prune in a thread of this process, every `every` seconds, while it serves (the app's lifespan; the tests,
    which never enter it, call prune themselves)."""
    def loop():
        while True:
            time.sleep(every)
            try:
                prune()
            except Exception as e:  # noqa: BLE001  clearing never takes the app down; the next round tries again
                print(f"oauth: a prune failed: {type(e).__name__}: {e}", file=sys.stderr)
    worker = threading.Thread(target=loop, name="oauth-prune", daemon=True)
    worker.start()
    return worker


def token(form, client="", now=None):
    """The token endpoint, for a form already read into {name: value} from the address `client`: a code exchanged,
    or a refresh token used. Returns the token response; OAuthError as RFC 6749 (5.2) says. Its code or refresh token
    is found before the write lock is taken (_found): one this ADE did not give, or one another app's that is unused, is
    counted (_count_failure) and refused, and only such a request is ever refused for a count, so the counts of a
    client and a network that many people share refuse none of their own codes and refresh tokens. A used one goes on,
    whoever names it, to end what it made."""
    now = _now(now)
    kind = {"authorization_code": "code", "refresh_token": "refresh"}.get(form.get("grant_type"))
    if kind is None:
        raise OAuthError(400, "unsupported_grant_type", "This ADE takes authorization_code and refresh_token.")
    needed, said = _NEEDED[kind]
    if not all(isinstance(form.get(k), str) and form.get(k) for k in needed):
        raise OAuthError(400, "invalid_request", said)
    found = _found(kind, form[needed[0]])
    if found is None or (found["used_at"] is None and found["client_id"] != form["client_id"]):
        _count_failure(form["client_id"], client, found and found["grant_id"], now)
        what = "That code" if kind == "code" else "That refresh token"
        if found is None:
            raise OAuthError(400, "invalid_grant", f"{what} is not one this ADE gave.")
        raise OAuthError(401, "invalid_client", f"{what} is another app's.")
    return _exchange(form, client, now) if kind == "code" else _refresh(form, client, now)


def _exchange(form, client, now):
    code, client_id, redirect_uri, verifier = (form[k] for k in _NEEDED["code"][0])
    if not _VERIFIER.fullmatch(verifier):
        raise OAuthError(400, "invalid_request", "A code_verifier is 43 to 128 unreserved characters.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM ade_connector_tokens WHERE hash = ? AND kind = 'code'",
                         (token_hash(code),)).fetchone()
        grant = row and db.execute("SELECT * FROM ade_connector_grants WHERE id = ?", (row["grant_id"],)).fetchone()
        if not grant:
            raise OAuthError(400, "invalid_grant", "That code is not one this ADE gave.")
        if row["used_at"] is not None:
            # a code presented again: what it made is revoked (OAuth 2.1, 4.1.2)
            first = revoke_in(db, grant, "its code was used twice", now)
            record(db, grant["user_id"], grant["id"], "reuse", "code", outcome="refused", net=auth.network_of(client),
                   now=now)
            answer = None
        else:
            if row["expires_at"] <= now:
                raise OAuthError(400, "invalid_grant", "That code has expired.")
            if client_id != grant["client_id"]:
                raise OAuthError(401, "invalid_client", "That code is another app's.")
            if redirect_uri != row["redirect_uri"] or _s256(verifier) != row["code_challenge"]:
                raise OAuthError(400, "invalid_grant", "The redirect_uri or the code_verifier is not the code's.")
            if form.get("resource") is not None and canonical(form["resource"]) != grant["resource"]:
                raise OAuthError(400, "invalid_target", f"The resource is this ADE's MCP endpoint, {resource()}.")
            if grant["revoked_at"] is not None:
                raise OAuthError(400, "invalid_grant", "That connection was revoked.")
            db.execute("UPDATE ade_connector_tokens SET used_at = ? WHERE hash = ?", (now, row["hash"]))
            db.execute("UPDATE ade_connector_grants SET connected_at = COALESCE(connected_at, ?) WHERE id = ?",
                       (now, grant["id"]))
            answer = _issue(db, grant, grant["scopes"].split(), now)
            record(db, grant["user_id"], grant["id"], "connected", net=auth.network_of(client), now=now)
    if answer is None:
        if first:
            _tell_disconnected(grant, now)
        raise OAuthError(400, "invalid_grant", "That code was used already; what it made is revoked.")
    return answer


def _refresh(form, client, now):
    refresh, client_id, scope = form["refresh_token"], form["client_id"], form.get("scope")
    net = auth.network_of(client)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM ade_connector_tokens WHERE hash = ? AND kind = 'refresh'",
                         (token_hash(refresh),)).fetchone()
        grant = row and db.execute("SELECT * FROM ade_connector_grants WHERE id = ?", (row["grant_id"],)).fetchone()
        if not grant:
            raise OAuthError(400, "invalid_grant", "That refresh token is not one this ADE gave.")
        if row["used_at"] is not None:
            # presented again: by the app or by whoever copied it, the server cannot tell which, so neither keeps
            # what the token made (RFC 9700, 4.14.2)
            first = revoke_in(db, grant, "a refresh token was used twice", now)
            record(db, grant["user_id"], grant["id"], "reuse", "refresh token", outcome="refused", net=net, now=now)
            answer = None
        else:
            if row["expires_at"] <= now or grant["revoked_at"] is not None or grant["ends_at"] <= now:
                raise OAuthError(400, "invalid_grant", "That refresh token no longer works: connect the app again.")
            if client_id != grant["client_id"]:
                raise OAuthError(401, "invalid_client", "That refresh token is another app's.")
            if form.get("resource") is not None and canonical(form["resource"]) != grant["resource"]:
                raise OAuthError(400, "invalid_target", f"The resource is this ADE's MCP endpoint, {resource()}.")
            granted = grant["scopes"].split()
            asked = granted if scope is None else str(scope).split()
            if not asked or any(s not in granted for s in asked):
                raise OAuthError(400, "invalid_scope", "A refresh may ask for the scopes allowed, or fewer.")
            if db.execute("SELECT COUNT(*) FROM ade_connector_events WHERE grant_id = ? AND kind = 'refreshed' AND "
                          "at > ?", (grant["id"], now - 3600)).fetchone()[0] >= REFRESHES_PER_HOUR:
                raise OAuthError(429, "invalid_request", f"An app refreshes at most {REFRESHES_PER_HOUR} times an "
                                                         "hour.")
            db.execute("UPDATE ade_connector_tokens SET used_at = ? WHERE hash = ?", (now, row["hash"]))
            answer = _issue(db, grant, [s for s in granted if s in asked], now)
            record(db, grant["user_id"], grant["id"], "refreshed", net=net, now=now)
    if answer is None:
        if first:
            _tell_disconnected(grant, now)
        raise OAuthError(400, "invalid_grant", "That refresh token was used already; the connection it belongs to is "
                                               "revoked.")
    return answer


def authenticate(authorization, client="", now=None):
    """The grant the access token in an Authorization header names, at /mcp: {id, user_id, client_name, scopes (the
    token's), active, takes_effect_at}. Unauthorized when none came, or one that does not work: unknown, expired, its
    grant revoked or ended, made for another public address of this ADE, or of an account no longer there (the app
    deletes no account, but one deleted from the store by hand reads nothing through its apps)."""
    now = _now(now)
    m = _BEARER.fullmatch(authorization or "")
    if m is None:
        raise Unauthorized(False)
    if not m.group(1).startswith(ACCESS_PREFIX):
        raise Unauthorized(True)
    net = auth.network_of(client)
    with store.connect() as db:
        row = db.execute("SELECT t.expires_at AS token_expires, t.scopes AS token_scopes, g.* FROM "
                         "ade_connector_tokens t JOIN ade_connector_grants g ON g.id = t.grant_id JOIN users u ON u.id = "
                         "g.user_id WHERE t.hash = ? AND t.kind = 'access'", (token_hash(m.group(1)),)).fetchone()
    if row is None:
        raise Unauthorized(True)
    why = ("expired" if row["token_expires"] <= now else "revoked" if row["revoked_at"] is not None
           else "ended" if row["ends_at"] <= now else "made for another address"
           if canonical(row["resource"]) != resource() else None)
    if why:
        with store.connect() as db:
            # one event a grant each REFUSED_EVERY seconds: an app that goes on with a token that no longer works fills
            # nothing
            if db.execute("SELECT 1 FROM ade_connector_events WHERE grant_id = ? AND kind = 'refused' AND what = "
                          "'token' AND at > ?", (row["id"], now - REFUSED_EVERY)).fetchone() is None:
                record(db, row["user_id"], row["id"], "refused", "token", subject=why, outcome="refused", net=net,
                       now=now)
        raise Unauthorized(True)
    active = row["active_at"] is not None
    if not active and row["takes_effect_at"] is not None and row["takes_effect_at"] <= now:
        holds.settle(row["user_id"], now)
        with store.connect() as db:
            again = db.execute("SELECT active_at, revoked_at FROM ade_connector_grants WHERE id = ?",
                               (row["id"],)).fetchone()
        if again["revoked_at"] is not None:
            raise Unauthorized(True)
        active = again["active_at"] is not None
    if row["last_used_at"] is None or now - row["last_used_at"] >= SEEN_EVERY:
        with store.connect() as db:
            db.execute("UPDATE ade_connector_grants SET last_used_at = ?, last_net = ? WHERE id = ?",
                       (now, net, row["id"]))
    return {"id": row["id"], "user_id": row["user_id"], "client_name": row["client_name"],
            "scopes": (row["token_scopes"] or "").split(), "active": active, "takes_effect_at": row["takes_effect_at"]}


# ---- the account's switch, and ending a grant -----------------------------------------------------------------------

def account(user_id):
    """{on: whether the account lets apps connect}."""
    with store.connect() as db:
        return {"on": _account_on(db, user_id)}


def set_account(user_id, on, confirmation=None, now=None):
    """Turn the account's switch on, with its confirmation, or off, with the sign-in alone, which revokes every grant
    of the account. Turned on, the account's devices are told at once, as of a change to how it signs in: a sign-in
    and a confirmation taken from a lost device could turn it on. {on, revoked: how many grants were revoked}."""
    now = _now(now)
    turned, live = False, []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if on:
            turned = not _account_on(db, user_id)
            if turned:
                confirm_in(db, user_id, confirmation, now, "connector-on")
                db.execute("UPDATE users SET connector_on = 1 WHERE id = ?", (user_id,))
        else:
            db.execute("UPDATE users SET connector_on = 0 WHERE id = ?", (user_id,))
            live = db.execute("SELECT * FROM ade_connector_grants WHERE user_id = ? AND revoked_at IS NULL",
                              (user_id,)).fetchall()
            for grant in live:
                revoke_in(db, grant, "apps were turned off for the account", now)
    if turned:
        from .ade import push
        push.notice(user_id, push.words("apps-on"), now)
    return {"on": bool(on), "revoked": len(live)}


def revoke_in(db, grant, why, now):
    """Inside the caller's transaction: end a grant at once. Its tokens stop at their next use, each being looked up
    with its grant; its hold, while it waits, is cancelled; its waiting proposals are void. False when it had ended
    already."""
    from .ade import proposals
    done = db.execute("UPDATE ade_connector_grants SET revoked_at = ?, revoked_why = ? WHERE id = ? AND revoked_at IS "
                      "NULL", (now, why, grant["id"])).rowcount
    if not done:
        return False
    if grant["hold_id"]:
        db.execute("UPDATE ade_holds SET cancelled_at = ? WHERE id = ? AND cancelled_at IS NULL AND done_at IS NULL",
                   (now, grant["hold_id"]))
    proposals.void_grant(db, grant["id"], now)
    record(db, grant["user_id"], grant["id"], "revoked", subject=why, now=now)
    return True


def passkeys_going(db, user_id, passkey_ids, why, now):
    """Inside the caller's transaction, as passkeys of the account go (removed, or a revocation that removes them
    placed): every connection one of them allowed (its confirmed_by), in effect or waiting on its hold, revoked with
    `why` (revoke_in, which cancels a waiting one's hold with it). A lost device's passkey, removed, leaves no
    connection it allowed. The ids revoked."""
    ids = [p for p in passkey_ids if p]
    if not ids:
        return []
    rows = db.execute(f"SELECT * FROM ade_connector_grants WHERE user_id = ? AND revoked_at IS NULL AND ends_at > ? AND "
                      f"confirmed_by IN ({','.join('?' * len(ids))}) ORDER BY allowed_at, id",
                      (user_id, now, *ids)).fetchall()
    return [g["id"] for g in rows if revoke_in(db, g, why, now)]


def recent_grants(user_id, now=None):
    """The account's connections not ended (connecting, waiting or in effect) that were allowed in the last RECENT_DAYS
    days, newest first, as Connected apps lists them: what signing out everywhere answers with, since nothing says the
    device it ends did not allow one of them."""
    now = _now(now)
    return [g for g in grants_for(user_id, now)["grants"]
            if g["state"] in ("connecting", "waiting", "active") and g["allowed_at"] > now - RECENT_DAYS * 86400]


def lost_device_words(revoked=0):
    """What removing a passkey says of connected apps: how many connections it allowed were revoked, and, while the
    connector is on, that the others keep working until revoked in Connected apps."""
    said = [f"{revoked} app connection{'s' if revoked != 1 else ''} it allowed {'were' if revoked != 1 else 'was'} "
            "revoked."] if revoked else []
    if enabled():
        said.append("Other connected apps keep working until you revoke them in Connected apps." if revoked
                    else KEEP_WORKING)
    return " ".join(said)


def signed_out_words(recent):
    """What signing out everywhere says of connected apps, while the connector is on: they keep working until revoked
    in Connected apps, and the ones allowed in the last RECENT_DAYS days (`recent`, recent_grants), each by the name it
    calls itself."""
    if not enabled():
        return ""
    if not recent:
        return KEEP_WORKING
    names = ", ".join(f"\N{LEFT DOUBLE QUOTATION MARK}{g['client_name']}\N{RIGHT DOUBLE QUOTATION MARK}"
                      for g in recent[:3]) + (f" and {len(recent) - 3} more" if len(recent) > 3 else "")
    return (f"Connected apps keep working until you revoke them in Connected apps; allowed in the last {RECENT_DAYS} "
            f"days: {names}.")


# ---- Connected apps -------------------------------------------------------------------------------------------------

LISTED = 50
ENDED_SHOWN = 30 * 86400            # how long a connection that ended is still listed
EVENTS_PAGE = 50


def _state(row, now):
    """Where a grant stands: connecting (its code not exchanged yet), waiting (its hold not ended), active, ended
    (its 90 days over) or revoked."""
    if row["revoked_at"] is not None:
        return "revoked"
    if row["ends_at"] <= now:
        return "ended"
    if row["connected_at"] is None:
        return "connecting"
    return "active" if row["active_at"] is not None else "waiting"


def grant_view(row, now):
    return {"id": row["id"], "client_name": row["client_name"],
            "returns_to": urllib.parse.urlsplit(row["redirect_uri"]).hostname, "described_at": row["document_host"],
            "scopes": row["scopes"].split(), "state": _state(row, now), "allowed_at": row["allowed_at"],
            "connected_at": row["connected_at"], "takes_effect_at": row["takes_effect_at"], "hold_id": row["hold_id"],
            "ends_at": row["ends_at"], "revoked_at": row["revoked_at"], "revoked_why": row["revoked_why"],
            "last_used_at": row["last_used_at"], "last_net": row["last_net"],
            "proposals_waiting": row["waiting"] if "waiting" in row.keys() else 0}


def grants_for(user_id, now=None):
    """The account's connected apps as Connected apps lists them, newest first: every live one, however many, and the
    LISTED newest of those that ended in the last ENDED_SHOWN seconds, so connections that ended never push a live one
    off the list, where its Revoke is; but not one whose app never collected its code. A hold whose time has come is
    settled first, so the list says what holds now."""
    now = _now(now)
    holds.settle(user_id, now)
    shown = ("SELECT g.*, (SELECT COUNT(*) FROM ade_proposals p WHERE p.grant_id = g.id AND p.state = 'proposed' AND "
             "p.expires_at > ?) AS waiting FROM ade_connector_grants g WHERE g.user_id = ? AND (g.connected_at IS NOT "
             "NULL OR g.allowed_at > ?) AND ")
    newest = " ORDER BY g.allowed_at DESC, g.id"
    with store.connect() as db:
        live = db.execute(shown + "g.revoked_at IS NULL AND g.ends_at > ?" + newest,
                          (now, user_id, now - CODE_TTL, now)).fetchall()
        ended = db.execute(shown + "(g.revoked_at IS NOT NULL OR g.ends_at <= ?) AND COALESCE(g.revoked_at, g.ends_at) > ?"
                           + newest + " LIMIT ?", (now, user_id, now - CODE_TTL, now, now - ENDED_SHOWN,
                                                  LISTED)).fetchall()
    rows = sorted(live + ended, key=lambda r: (-r["allowed_at"], r["id"]))
    return {"grants": [grant_view(r, now) for r in rows]}


def revoke(user_id, grant_id, now=None):
    """The owner revokes one of the account's grants, with the sign-in alone (a forged revoke can only disconnect): it
    ends at once, its hold too while it waits. 404 for one not the account's; a second revoke answers as the
    first."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM ade_connector_grants WHERE id = ? AND user_id = ?",
                         (grant_id, user_id)).fetchone()
        if row is None:
            raise ConnectorError(404, "No such app.")
        revoke_in(db, row, "the owner revoked it", now)
        return grant_view(db.execute("SELECT * FROM ade_connector_grants WHERE id = ?", (grant_id,)).fetchone(), now)


def events_for(user_id, grant_id, before=None, limit=EVENTS_PAGE):
    """One grant's connector events, newest first, a page at a time: {events, before: where the next page starts, or
    None}. 404 for a grant not the account's."""
    limit = max(1, min(int(limit), EVENTS_PAGE))
    upto = 2 ** 62 if before is None else max(0, min(int(before), 2 ** 62))     # a number past it is no id
    with store.connect() as db:
        if db.execute("SELECT 1 FROM ade_connector_grants WHERE id = ? AND user_id = ?",
                      (grant_id, user_id)).fetchone() is None:
            raise ConnectorError(404, "No such app.")
        rows = db.execute("SELECT * FROM ade_connector_events WHERE grant_id = ? AND user_id = ? AND id < ? ORDER BY "
                          "id DESC LIMIT ?", (grant_id, user_id, upto, limit)).fetchall()
    return {"events": [{"id": r["id"], "at": r["at"], "kind": r["kind"], "what": r["what"], "subject": r["subject"],
                        "outcome": r["outcome"], "net": r["net"]} for r in rows],
            "before": rows[-1]["id"] if len(rows) == limit else None}


def _hold_ended(db, hold, now):
    """A connection's hold has ended: it takes effect, unless it was revoked meanwhile."""
    grant = db.execute("SELECT * FROM ade_connector_grants WHERE id = ? AND user_id = ?",
                       (hold["subject"], hold["user_id"])).fetchone()
    if grant is None or grant["revoked_at"] is not None:
        return "The connection was revoked before it took effect."
    db.execute("UPDATE ade_connector_grants SET active_at = ? WHERE id = ?", (now, grant["id"]))
    record(db, grant["user_id"], grant["id"], "effective", now=now)
    return None


def _hold_cancelled(db, hold, now):
    """A connection's hold was cancelled, by the owner's Cancel or by a lost device's steps (holds.cancel_all): the
    grant is revoked."""
    grant = db.execute("SELECT * FROM ade_connector_grants WHERE id = ? AND user_id = ?",
                       (hold["subject"], hold["user_id"])).fetchone()
    if grant is not None:
        revoke_in(db, grant, "it was cancelled before it took effect", now)


def _hold_broke(db, hold, now):
    """A connection's hold whose taking effect broke (holds.settle): the grant is revoked, so its tokens stop saying it
    takes effect, and the owner connects it again."""
    grant = db.execute("SELECT * FROM ade_connector_grants WHERE id = ? AND user_id = ?",
                       (hold["subject"], hold["user_id"])).fetchone()
    if grant is not None:
        revoke_in(db, grant, "it could not take effect", now)


# allowing an app is confirmed, not signed: the grant keeps the passkey that confirmed it, and removing that passkey
# revokes it in effect or waiting (passkeys_going); but a sign-in taken from a lost device names no passkey, and the
# password names none, so a lost device's steps (removing a passkey, signing out everywhere) also cancel every
# connection still waiting on its hold, whichever confirmed it
holds.register(HOLD, _hold_ended, _hold_cancelled, setting=HOLD_SETTING, default=HOLD_DEFAULT, unsigned=True,
               released=_hold_broke, notice="held-app")
