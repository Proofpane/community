"""What the link client's end-to-end encryption tests share: a stand-in of the server's side of the link routes this
client sends a sealed text on (the statements, events, the History list and conversations, pairing, where a key's
pairing stands, and the owner's keys), and a Python implementation of the page's side that opens an envelope, written
from the design without the client's own code. The stand-in checks no signature and keeps everything in
memory; where the design has the server refuse a text in the clear (409, e2e required) or under a key retired for
sealing, it does so when told to."""

import base64
import hashlib
import http.server
import json
import os
import re
import threading
import time
import urllib.parse
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import ade_link
from proofpane_open import paths  # noqa: F401  (puts the open packages, proofpane_core's among them, on sys.path)
from proofpane_core.canonical import canonicalize  # noqa: E402
from test_passkeys import Authenticator, as_step  # noqa: E402

ACCOUNT = "acct-e2e-0001"
LINK_ID = "0123456789abcdef0123456789abcdef"
ENVELOPE = re.compile(r"e2e1\.[A-Za-z0-9_-]+")
SEALED_KINDS = ("step", "question", "waiting", "done", "note")
FIXED = ("working on it",)


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def raw_public(private):
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(public):
    return hashlib.sha256(public).hexdigest()[:32]


# ---- the opening side, as the page has it ---------------------------------------------------------------------------

class NotOpened(Exception):
    pass


def slot(account, link_id, *parts):
    """The slot, built here from the design's words: "proofpane-e2e/1|" + account + "|" + link_id + "|" + the path's
    part, each empty field as empty text and a number in decimal."""
    return "proofpane-e2e/1|" + account + "|" + link_id + "|" + "|".join("" if p is None else str(p) for p in parts)


def body_of(envelope):
    """The bytes of an envelope in its strict form, or NotOpened: the pattern, a body whose length is not 1 modulo 4,
    and the bytes encoding again to the body exactly."""
    if not isinstance(envelope, str) or not ENVELOPE.fullmatch(envelope):
        raise NotOpened("not an envelope's form")
    body = envelope[len("e2e1."):]
    if len(body) % 4 == 1:
        raise NotOpened("a body of a length base64url never has")
    raw = unb64u(body)
    if b64u(raw) != body:
        raise NotOpened("a body that does not encode again to itself")
    if len(raw) < 77:
        raise NotOpened("shorter than its header and tag")
    return raw


def open_bytes(envelope, aad_slot, private):
    """The plaintext bytes of an envelope sealed to `private` (an X25519 private key) and bound to `aad_slot`."""
    raw = body_of(envelope)
    header, sealed = raw[:61], raw[61:]
    public = raw_public(private)
    if header[0] != 1:
        raise NotOpened("a header of another version")
    if header[1:17] != hashlib.sha256(public).digest()[:16]:
        raise NotOpened("sealed to another key")
    epk, nonce = header[17:49], header[49:61]
    shared = private.exchange(X25519PublicKey.from_public_bytes(epk))
    if not any(shared):
        raise NotOpened("an all-zero secret")
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + public, info=b"proofpane-e2e/1 content").derive(shared)
    try:
        return AESGCM(k).decrypt(nonce, sealed, header + aad_slot.encode("utf-8"))
    except Exception:  # noqa: BLE001  InvalidTag: another slot, or the bytes changed
        raise NotOpened("the tag fails") from None


def open_envelope(envelope, aad_slot, private):
    """The plaintext of an envelope, a JSON object read strictly from UTF-8, whose bytes are its RFC 8785 form."""
    plain = open_bytes(envelope, aad_slot, private)
    try:
        value = json.loads(plain.decode("utf-8"))
    except ValueError:                                  # not UTF-8 (UnicodeDecodeError is one), or not JSON
        raise NotOpened("not JSON in UTF-8") from None
    if not isinstance(value, dict) or canonicalize(value) != plain:
        raise NotOpened("not a JSON object in its canonical form")
    return value


# ---- the owner's keys and statements -------------------------------------------------------------------------------

def owner_key(device, name="Ana's iPhone"):
    """A passkey as owner_keys.json pins it."""
    der = device.key.public_key().public_bytes(serialization.Encoding.DER,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
    return {"credential_id": b64u(device.credential_id), "alg": device.alg, "public_key": b64u(der),
            "name": name, "fingerprint": hashlib.sha256(der).hexdigest()[:32]}


class Owner:
    """The owner's side: passkeys, content keys, and statements signed and chained as the page signs them."""

    def __init__(self, url):
        self.url = url
        self.host = url.split("://", 1)[1]
        self.device = Authenticator(alg=-7, rp="127.0.0.1", origin=url)
        self.other = Authenticator(alg=-8, rp="127.0.0.1", origin=url)       # a passkey the link has not pinned
        self.keys = []                                                       # (private, public, key_id), in order
        self.statements = []                                                 # as the server keeps them

    def content_key(self):
        private = X25519PrivateKey.generate()
        public = raw_public(private)
        self.keys.append((private, public, key_id(public)))
        return private, public

    def private(self, kid):
        return next(p for p, _, k in self.keys if k == kid)

    def payload(self, purpose, seq=None, prev=None, public=None, retires="grace", **over):
        seq = len(self.statements) + 1 if seq is None else seq
        if prev is None:
            prev = hashlib.sha256(self.statements[seq - 2]["payload"].encode()).hexdigest() if seq > 1 else ""
        now = int(time.time())
        p = {"v": 2, "deployment": self.host, "purpose": purpose, "account": ACCOUNT, "nonce": uuid.uuid4().hex,
             "id": uuid.uuid4().hex, "seq": seq, "prev": prev, "created_at": now, "expires_at": now + 600}
        if purpose == "e2e-key":
            p.update(key=b64u(public), key_id=key_id(public), retires=retires)
        p.update(over)
        return p

    def signed(self, payload, device=None):
        raw = canonicalize(payload)
        credential = (device or self.device).get(as_step(raw))
        return {"payload": raw.decode("utf-8"), "credential": credential}

    def state(self, purpose="e2e-key", device=None, keep=True, retires="grace", **over):
        """A statement, the next in the chain unless `over` says otherwise, signed by the pinned passkey (or
        `device`); kept as the server's newest where `keep`. With a new key for a key's statement: (statement,
        private key)."""
        private = public = None
        if purpose == "e2e-key" and "public" not in over:
            private, public = self.content_key()
        public = over.pop("public", public)
        s = self.signed(self.payload(purpose, public=public, retires=retires, **over), device)
        if keep:
            self.statements.append(s)
        return s, private


# ---- the server's side --------------------------------------------------------------------------------------------

class LinkStandIn:
    """The link routes a sealed text goes on, held in memory. `require`: refuse a text in the clear on a sealed path
    (and an envelope under a key in `retired`) with a 409 while the newest statement names a key.
    `e2e_route` False: an ADE without the statements' routes (404). `say_e2e`: answers carry the newest statement.
    `e2e_page`, a number: the statements are served a page at a time, as the server pages them (those after the query's
    `after`, at most `limit` and at most this many, with `more` while others follow); None: all at once, `more`
    absent, as a server before paging served them."""

    def __init__(self):
        self.requests, self.events, self.histories, self.talks, self.uploads = [], [], [], [], []
        self.require, self.e2e_route, self.say_e2e, self.retired = False, True, False, set()
        self.e2e_page, self.query, self.queries = None, {}, []
        self.owner = None
        self.next_seq = 100
        self.refuse_next_post = []                    # statuses the next event posts answer with, in turn
        self.after_history = None                     # run once a list is taken: a change between it and its talks
        self.pairings, self.auth = {}, None           # each key's newest pairing, by the key_id it signs with
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                split = urllib.parse.urlsplit(self.path)
                outer.query = dict(urllib.parse.parse_qsl(split.query))
                outer.auth = self.headers.get("Authorization")
                outer.queries.append((self.command, split.path, outer.query))
                outer.requests.append((self.command, split.path, body))
                status, content = outer.route(self.command, split.path, body)
                data = json.dumps(content).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _answer

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.owner = Owner(self.url)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # what the routes see

    def newest(self):
        """The newest statement's payload, or None."""
        return json.loads(self.owner.statements[-1]["payload"]) if self.owner.statements else None

    def word(self):
        n = self.newest()
        return None if n is None else {"seq": n["seq"], "key_id": n.get("key_id")}

    def on(self):
        n = self.newest()
        return n is not None and n["purpose"] == "e2e-key"

    def required(self):
        n = self.newest() or {}
        return 409, {"detail": "Encryption is required.", "e2e": "required", "seq": n.get("seq"),
                     "key_id": n.get("key_id")}

    def refuses(self, text):
        """Whether a text on a sealed path is refused: in the clear while required, or under a retired key."""
        if not self.require or not self.on():
            return False
        if not isinstance(text, str) or not ENVELOPE.fullmatch(text):
            return True
        # the key an envelope names, in its bytes 1 to 16 in each form (a form-3 message's, its conversation key's id,
        # never a retired content key's); read without body_of's form-1 floor, which a short form-3 message is under
        return unb64u(text[len("e2e1."):])[1:17].hex() in self.retired

    def said(self, content):
        return dict(content, e2e=self.word()) if self.say_e2e else content

    def route(self, method, path, body):
        path = path[len("/api/ade/link"):] if path.startswith("/api/ade/link") else path
        data = json.loads(body or b"{}")
        if path == "/e2e":
            if not self.e2e_route:
                return 404, {"detail": "Not Found"}
            if method == "GET":
                if self.e2e_page is None:
                    return 200, {"statements": list(self.owner.statements)}
                after = int(self.query.get("after", 0))
                limit = min(int(self.query.get("limit", self.e2e_page)), self.e2e_page)
                later = [s for s in self.owner.statements if json.loads(s["payload"])["seq"] > after]
                return 200, {"statements": later[:limit], "more": len(later) > limit}
            self.uploads.append(data)
            self.owner.statements.extend(data["statements"])
            return 200, {"taken": len(data["statements"])}
        if path == "/events" and method == "POST":
            if self.refuse_next_post:
                status = self.refuse_next_post.pop(0)
                return status, {"detail": f"HTTP {status}"}
            to = data.get("to")
            sealed_path = data.get("kind") in SEALED_KINDS and to in (None, "owner", "mac") \
                and not (data.get("text") in FIXED and data.get("kind") == "waiting")
            if sealed_path and self.refuses(data.get("text")):
                return self.required()
            first = next((e for e in self.events if e["client_id"] == data.get("client_id")), None)
            if first is not None:
                return 200, self.said({"seq": first["seq"], "session": None})
            self.next_seq += 1
            self.events.append(dict(data, seq=self.next_seq))
            return 200, self.said({"seq": self.next_seq, "session": data.get("session")})
        if path == "/history" and method == "PUT":
            for row in data.get("sessions", []):
                if self.require and self.on() and ("sealed" not in row or self.refuses(row["sealed"])):
                    return self.required()
            self.histories.append(data)
            if self.after_history is not None:
                then, self.after_history = self.after_history, None
                then()
            return 200, self.said({"listed": len(data.get("sessions", [])), "left_out": 0, "noted": [],
                                   "talks": sorted(t["session"] for t in self.talks)})
        if path == "/history/talk" and method == "PUT":
            # a conversation under a key of its own is refused as the server refuses it (ade/link.py): its key sealed
            # to a retired content key (409), or a message beside it in the clear or of another form (422)
            if data.get("key") is not None:
                if self.refuses(data["key"]):
                    return self.required()
                if any(not isinstance(m.get("text"), str) or not ENVELOPE.fullmatch(m["text"])
                       or unb64u(m["text"][len("e2e1."):])[:1] != b"\x03" for m in data.get("messages", [])):
                    return 422, {"detail": "A conversation sealed under a key of its own holds form 3 alone."}
            if any(self.refuses(m.get("text")) for m in data.get("messages", [])):
                return self.required()
            self.talks.append(data)
            return 200, self.said({"kept": data.get("session"), "messages": len(data.get("messages", []))})
        if path == "/pair" and method == "POST":
            # as the ADE answers a pairing whose hold is none (a pairing waits, ade/link.py pair); the
            # pairing kept as its key's newest, which GET /ask reads, its ticket as the ADE keeps one: the first half
            # of the sha256 of the ticket in upper case (link._ticket_hash)
            raw = unb64u(data.get("public_key") or "")
            ticket = str(data.get("ticket") or "").strip().upper()
            self.pairings[hashlib.sha256(raw).hexdigest()[:16]] = {
                "public_key": raw, "name": data.get("name"),
                "ticket": hashlib.sha256(ticket.encode("utf-8")).hexdigest()[:32]}
            return 200, {"status": "linked", "link_id": LINK_ID, "name": data.get("name"),
                         "owner_keys": self.owner_keys()}
        if path == "/ask" and method == "GET":
            # where the newest request made with the key that signs it stands, as link_asks.poll answers it: none
            # made, 401; a ticket's pairing, its name and ticket, linked (this stand-in's one link, never moved)
            m = re.search(r"key_id=([0-9a-f]+)", self.auth or "")
            kept = self.pairings.get(m.group(1)) if m else None
            if kept is None:
                return 401, {"detail": "No request was made with this key: ask first."}
            return 200, {"fingerprint": hashlib.sha256(kept["public_key"]).hexdigest()[:32],
                         "server_time": int(time.time()), "name": kept["name"], "ticket": kept["ticket"],
                         "status": "linked", "link_id": LINK_ID, "owner_keys": self.owner_keys()}
        if path == "/owner-keys":
            return 200, self.owner_keys()
        if path == "/state":
            return 200, self.said({"link_id": LINK_ID, "name": "mac", "stopped": False, "stopped_at": None,
                                   "resume": None})
        return 404, {"detail": "Not Found"}

    def owner_keys(self):
        return {"account": ACCOUNT, "passkeys": [owner_key(self.owner.device)]}

    def posted(self, method="POST", path="/api/ade/link/events"):
        return [json.loads(b) for m, p, b in self.requests if m == method and p == path]


def a_link(stand_in, directory, pinned=True, name="mac"):
    """A link's directory as pair and pin leave it: config.json, a key, and the owner's passkey pinned."""
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "config.json"), "w") as fh:
        json.dump({"url": stand_in.url, "name": name, "link_id": LINK_ID}, fh)
    ade_link.Link(str(directory)).key(make=True)
    if pinned:
        with open(os.path.join(directory, "owner_keys.json"), "w") as fh:
            json.dump({"account": ACCOUNT, "keys": [owner_key(stand_in.owner.device)], "source": "pair",
                       "pinned_at": int(time.time())}, fh)
    return str(directory)
