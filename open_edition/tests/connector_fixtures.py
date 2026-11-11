"""What the Claude app connector's tests share: an app registered, PKCE, an authorization taken through the owner's
consent, and the confirmation that consent takes."""

import base64
import hashlib
import secrets
import urllib.parse

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "https://open.example.test/mcp"
ISSUER = "https://open.example.test"
PASSWORD = "correct horse battery"                  # conftest's signed_up
DROP = object()


def pkce():
    """(verifier, challenge): a code verifier and its S256 challenge, as a client makes them."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client, redirect=CLAUDE, name="Claude"):
    r = client.post("/oauth/register", json={"redirect_uris": [redirect], "client_name": name,
                                             "grant_types": ["authorization_code", "refresh_token"]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def params(app, challenge, redirect=CLAUDE, **over):
    """An authorization request's parameters for the client `app`, as the Claude app sends them; `over` replaces one,
    or drops it (DROP)."""
    p = {"response_type": "code", "client_id": app, "redirect_uri": redirect, "state": "st-1",
         "code_challenge": challenge, "code_challenge_method": "S256", "scope": "ade.read ade.propose offline_access",
         "resource": RESOURCE}
    for key, value in over.items():
        if value is DROP:
            p.pop(key, None)
        else:
            p[key] = value
    return p


def authorize(client, app, challenge, redirect=CLAUDE, **over):
    return client.get("/oauth/authorize", params=params(app, challenge, redirect, **over), follow_redirects=False)


def request_id(response):
    """The request id in a redirect to the consent pane."""
    assert response.status_code == 302, response.text
    where = response.headers["location"]
    assert where.startswith(ISSUER + "/app/#connect="), where
    return where.split("#connect=", 1)[1]


def query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def confirmation(client, headers, change):
    """A confirmation from the account's password, made for `change` (passkeys.CHANGE), the one change it is spent
    on."""
    r = client.post("/api/account/confirm", headers=headers, json={"password": PASSWORD, "change": change})
    assert r.status_code == 200, r.text
    return r.json()["confirmation"]


def turn_on(client, headers):
    r = client.put("/api/connector/account", headers=headers,
                   json={"on": True, "confirmation": confirmation(client, headers, "connector-on")})
    assert r.status_code == 200, r.text


def account_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["id"]


def allow(client, headers, rid, propose=True, confirm=True, account=None):
    """Allow as the pane does: the confirmation, and the account the pane showed (the one signed in, unless given)."""
    body = {"propose": propose, "account": account or account_id(client, headers)}
    if confirm:
        body["confirmation"] = confirmation(client, headers,
                                            f"connector-allow:{rid}:{'propose' if propose else 'read'}")
    return client.post(f"/api/connector/requests/{rid}/allow", headers=headers, json=body)


def allowed(client, headers, redirect=CLAUDE, propose=True, scope="ade.read ade.propose offline_access"):
    """An app registered and allowed by the account whose sign-in is `headers` (its switch turned on first):
    {client_id, verifier, code, redirect_uri, redirect, hold, grant}."""
    client_id = register(client, redirect)
    verifier, challenge = pkce()
    rid = request_id(authorize(client, client_id, challenge, redirect, scope=scope))
    turn_on(client, headers)
    r = allow(client, headers, rid, propose)
    assert r.status_code == 200, r.text
    got = r.json()
    return dict(got, client_id=client_id, verifier=verifier, code=query(got["redirect"])["code"],
                redirect_uri=redirect)


def form(given, **over):
    for key, value in over.items():
        if value is DROP:
            given.pop(key, None)
        else:
            given[key] = value
    return given


def exchange(client, got, **over):
    """The app's exchange of its code, as the Claude app makes it; `over` replaces a field, or drops it (DROP)."""
    return client.post("/oauth/token", data=form({"grant_type": "authorization_code", "code": got["code"],
                                                  "client_id": got["client_id"], "redirect_uri": got["redirect_uri"],
                                                  "code_verifier": got["verifier"], "resource": RESOURCE}, **over))


def refresh(client, got, token, **over):
    return client.post("/oauth/token", data=form({"grant_type": "refresh_token", "refresh_token": token,
                                                  "client_id": got["client_id"], "resource": RESOURCE}, **over))


def connected(client, headers, **kw):
    """An app allowed and its code exchanged: what allowed() gives, and the token response."""
    got = allowed(client, headers, **kw)
    r = exchange(client, got)
    assert r.status_code == 200, r.text
    return dict(got, **r.json())


def bearer(token):
    return {"Authorization": "Bearer " + token}
