"""A stand-in for the Claude app's side of the connector, standard library only: what Claude does to add a custom
connector and use it, as the MCP authorization specification asks of a client. A POST without a token; the 401 and
the protected resource metadata it names; the authorization server's metadata; registration (RFC 7591) with Claude's
callback; the authorization with PKCE (S256), its state and its resource; the browser's return to the callback, its
state and issuer checked (RFC 9207); the code exchanged; MCP requests with the access token, in either era; and a
refresh. It reaches the ADE at `dial` (the app on a local port) while it names it by its public address, as a proxy in
front of the app does, and it follows no redirect: each one is the test's to read."""

import base64
import hashlib
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
MODERN = "2026-07-28"
LEGACY = "2025-11-25"
_META = "io.modelcontextprotocol/protocolVersion"


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Answer:
    """One HTTP answer: its status, headers and body."""

    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)


class StandIn:
    """One client of one ADE: `server_url` is the address the owner pastes into Claude (the ADE's public /mcp)."""

    def __init__(self, server_url, dial, name="Claude"):
        self.server_url = server_url
        parts = urllib.parse.urlsplit(server_url)
        self.public = f"{parts.scheme}://{parts.netloc}"
        self.dial = dial.rstrip("/")
        self.name = name
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.issuer = self.meta = self.client_id = self.verifier = self.state = self.tokens = None
        self.version = None
        self.next_id = 0

    # ---- HTTP ------------------------------------------------------------------------------------------------------

    def _local(self, url):
        if url != self.public and not url.startswith(self.public + "/"):
            raise AssertionError(f"the client was sent off the ADE's address: {url}")
        return self.dial + url[len(self.public):]

    def request(self, method, url, body=None, headers=None):
        req = urllib.request.Request(self._local(url), data=body, method=method, headers=headers or {})
        try:
            with self.opener.open(req, timeout=30) as r:
                return Answer(r.status, r.headers, r.read())
        except urllib.error.HTTPError as e:
            return Answer(e.code, e.headers, e.read())

    # ---- connecting ------------------------------------------------------------------------------------------------

    def discover(self):
        """A POST without a token, as Claude's first: the 401's WWW-Authenticate names the protected resource
        metadata, whose resource must be the address pasted; the authorization server's metadata, at the issuer it
        names, must name that issuer and S256. The 401's challenge."""
        r = self.request("POST", self.server_url, b'{"jsonrpc": "2.0", "id": 0, "method": "ping"}',
                         {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        assert r.status == 401, (r.status, r.body)
        challenge = r.headers["WWW-Authenticate"]
        where = re.search(r'resource_metadata="([^"]+)"', challenge)
        assert where, challenge
        prm = self.request("GET", where.group(1))
        assert prm.status == 200, prm.body
        resource = prm.json()
        assert resource["resource"] == self.server_url, resource
        self.issuer = resource["authorization_servers"][0]
        got = self.request("GET", self.issuer + "/.well-known/oauth-authorization-server")
        assert got.status == 200, got.body
        self.meta = got.json()
        assert self.meta["issuer"] == self.issuer
        assert "S256" in self.meta["code_challenge_methods_supported"]
        assert self.meta["authorization_response_iss_parameter_supported"] is True
        return challenge

    def register(self):
        """Dynamic client registration, as a public client with Claude's callback: its client_id."""
        body = {"client_name": self.name, "redirect_uris": [CALLBACK], "grant_types": ["authorization_code",
                "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}
        r = self.request("POST", self.meta["registration_endpoint"], json.dumps(body).encode(),
                         {"Content-Type": "application/json"})
        assert r.status == 201, r.body
        got = r.json()
        assert "client_secret" not in got
        self.client_id = got["client_id"]
        return self.client_id

    def authorize(self, scope="ade.read ade.propose offline_access"):
        """The authorization request the browser opens, with a new verifier and state: where the ADE sends the
        browser (a 302's Location)."""
        self.verifier = b64u(secrets.token_bytes(32))
        self.state = b64u(secrets.token_bytes(16))
        query = {"response_type": "code", "client_id": self.client_id, "redirect_uri": CALLBACK, "state": self.state,
                 "code_challenge": b64u(hashlib.sha256(self.verifier.encode()).digest()),
                 "code_challenge_method": "S256", "scope": scope, "resource": self.server_url}
        r = self.request("GET", self.meta["authorization_endpoint"] + "?" + urllib.parse.urlencode(query))
        assert r.status == 302, (r.status, r.body)
        return r.headers["Location"]

    def callback(self, url):
        """The browser back at Claude's callback: its state and issuer checked, then the code exchanged. The token
        answer; for an error, the callback's parameters."""
        assert url.startswith(CALLBACK + "?"), url
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert q.get("state") == self.state, q
        assert q.get("iss") == self.issuer, q
        if "error" in q:
            return q
        r = self.token({"grant_type": "authorization_code", "code": q["code"], "redirect_uri": CALLBACK,
                        "client_id": self.client_id, "code_verifier": self.verifier, "resource": self.server_url})
        assert r.status == 200, r.body
        self.tokens = r.json()
        return self.tokens

    def token(self, form):
        return self.request("POST", self.meta["token_endpoint"], urllib.parse.urlencode(form).encode(),
                            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})

    def refresh(self, refresh_token=None):
        """A refresh with the newest refresh token (or the one given): the answer; the tokens kept when it is 200."""
        r = self.token({"grant_type": "refresh_token", "refresh_token": refresh_token or self.tokens["refresh_token"],
                        "client_id": self.client_id, "resource": self.server_url})
        if r.status == 200:
            self.tokens = r.json()
        return r

    # ---- MCP -------------------------------------------------------------------------------------------------------

    def post(self, method, params=None, notification=False, modern=False, token=None):
        """One JSON-RPC message to the MCP endpoint with the access token. Modern (2026-07-28): the version in the
        message's _meta and the headers that repeat it. Legacy: the version initialize settled, in its header."""
        params = dict(params or {})
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Authorization": "Bearer " + (token or self.tokens["access_token"])}
        if modern:
            params["_meta"] = dict(params.get("_meta", {}), **{_META: MODERN})
            headers.update({"MCP-Protocol-Version": MODERN, "Mcp-Method": method})
            if method == "tools/call":
                headers["Mcp-Name"] = params["name"]
        elif self.version:
            headers["MCP-Protocol-Version"] = self.version
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            self.next_id += 1
            msg["id"] = self.next_id
        return self.request("POST", self.server_url, json.dumps(msg).encode(), headers)

    def initialize(self):
        """The legacy handshake: initialize, then the initialized notification. initialize's result."""
        r = self.post("initialize", {"protocolVersion": LEGACY, "capabilities": {},
                                     "clientInfo": {"name": "stand-in", "version": "1"}})
        assert r.status == 200, r.body
        result = r.json()["result"]
        self.version = result["protocolVersion"]
        assert self.post("notifications/initialized", notification=True).status == 202
        return result

    def tool(self, name, modern=False, **arguments):
        """A tool call: its result."""
        r = self.post("tools/call", {"name": name, "arguments": arguments}, modern=modern)
        assert r.status == 200, (r.status, r.body)
        return r.json()["result"]
