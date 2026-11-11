"""End-to-end encryption of what linked sessions send, through its real parts at once: the app served on a local port
(ade/e2e.py, ade/e2e_keys.py, ade/e2e_routes.py), and the machine's link client as the owner runs it (ade_link.py pair
--e2e, post, history, mirror, show, e2e). The page's side is written here in Python from the page's own code
(ui/src/e2eCrypto.ts, e2eKeys.ts): it makes the account's keys as the design says (the recovery
code, the master key, the roster, the credential's key pair with its anchor, the content key pair, the statement),
turns encryption on, rotates and revokes through the real routes, unlocks the keys from what the server then serves
(the credential's own wrap, the grant, the chain back to the anchor, the roster, the content keys' wraps) and opens
each sealed text with its slot rebuilt from what the server serves beside it. That Python is held to the page's vector
file byte for byte (test_the_page_s_constructions_here_are_the_page_s_vectors), so the keys made here are the page's.
Nothing stands in but the owner's passkey, the tests' software authenticator, and its PRF output, which the server
never sees: the test makes one."""

import base64
import glob
import hashlib
import hmac
import io
import json
import os
import re
import sys
import threading

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import ade_link
from conftest import signed_up
from e2e_link_stand_in import NotOpened, b64u, key_id, open_bytes, open_envelope, raw_public, slot, unb64u
from proofpane_open import config, store  # noqa: F401  (store puts the open packages on sys.path first)
from proofpane_open.ade import e2e_keys, holds, link_commands
from proofpane_core.canonical import canonicalize
from test_ade_link import at_once
from test_ade_link_client import run, served  # noqa: F401  (served: a fixture)
from test_ade_link_history import _said, _typed, _write
from test_passkeys import Authenticator, add, as_step, confirmed

V = "proofpane-e2e/1"
PASSWORD = "correct horse battery"
VECTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors")
ENVELOPE = re.compile(r"e2e1\.[A-Za-z0-9_-]+")
SESSION = "5d0e8a3c-1f2b-4c6d-9e7f-0a1b2c3d4e5f"           # Claude Code's id for the session listed and mirrored
TITLE = "Release prep"
ASKED = "May I tag the release candidate?"
NOTED = "The release notes are drafted."
FIRST = [("Is the release branch ready?", "2026-10-03T09:00:00Z"), ("Ready: 214 tests pass.", "2026-10-03T09:01:00Z")]
LATER = [("And the changelog?", "2026-10-03T09:05:00Z"), ("Written, in CHANGELOG.md.", "2026-10-03T09:06:00Z")]
# every word the link sends on a sealed path, none of which a copy of the database may hold
WORDS = (TITLE, ASKED, NOTED) + tuple(t for t, _ in FIRST + LATER)


# ---- the page's side, from ui/src/e2eCrypto.ts --------------------------------------------------------------------

def hkdf(ikm, info, length=32):
    """HKDF-SHA256 with an empty salt (WebCrypto's empty salt is RFC 5869's zeros), as the page derives each key."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info.encode("utf-8")).derive(ikm)


def gcm_seal(key, plain, aad, nonce=None):
    """AES-256-GCM as the page keeps every wrap: nonce (12 bytes) || ciphertext || tag, in base64url."""
    nonce = os.urandom(12) if nonce is None else nonce
    return b64u(nonce + AESGCM(key).encrypt(nonce, plain, aad.encode("utf-8")))


def gcm_open(key, blob, aad):
    raw = unb64u(blob)
    return AESGCM(key).decrypt(raw[:12], raw[12:], aad.encode("utf-8"))


def seal_bytes(public, plain, aad, ephemeral=None, nonce=None):
    """The envelope's construction over raw bytes, as a grant seals a master key's 32 bytes."""
    ephemeral = X25519PrivateKey.generate() if ephemeral is None else ephemeral
    nonce = os.urandom(12) if nonce is None else nonce
    epk = raw_public(ephemeral)
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(public))
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + public, info=b"proofpane-e2e/1 content").derive(shared)
    header = b"\x01" + hashlib.sha256(public).digest()[:16] + epk + nonce
    return "e2e1." + b64u(header + AESGCM(k).encrypt(nonce, plain, header + aad.encode("utf-8")))


def pkcs8(private):
    return private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())


def private_of(der):
    return serialization.load_der_private_key(der, None)


def mk_id(account, mk):
    """A master key's id: HKDF(MK, "mk_id|" + account), 16 bytes, as 32 hex. It names the key and commits to it."""
    return hkdf(mk, f"{V} mk_id|{account}", 16).hex()


def subkey(account, mk, purpose):
    """The master key is used only through these: content-wrap, chain and roster."""
    return hkdf(mk, f"{V} {purpose}|{account}")


def credential_aad(account, credential_id):
    return f"{V} credential|{account}|{credential_id}"


def grant_aad(account, to, mkid):
    return f"{V}|{account}|grant|{to}|{mkid}"


def chain_aad(account, newer, older):
    return f"{V} chain|{account}|{newer}|{older}"


def roster_aad(account, mkid):
    return f"{V} roster|{account}|{mkid}"


def content_key_aad(account, kid, mkid):
    return f"{V} content key|{account}|{kid}|{mkid}"


def anchor_aad(account):
    return f"{V} recovery anchor|{account}"


def credential_wrap(account, credential_id, prf, ck, anchor, nonce=None):
    """A credential's own wrap: {"anchor": mk_id, "ck": its PKCS#8 in base64url} as the page writes it, as bytes, under
    HKDF of the passkey's PRF output."""
    kek = hkdf(prf, credential_aad(account, credential_id))
    plain = b'{"anchor":"' + anchor.encode("ascii") + b'","ck":"' + b64u(pkcs8(ck)).encode("ascii") + b'"}'
    return gcm_seal(kek, plain, credential_aad(account, credential_id), nonce)


def open_credential_wrap(account, credential_id, prf, wrapped):
    """(its anchor, its private key), read in exactly the form the page writes."""
    plain = gcm_open(hkdf(prf, credential_aad(account, credential_id)), wrapped, credential_aad(account, credential_id))
    m = re.fullmatch(rb'\{"anchor":"([0-9a-f]{32})","ck":"([A-Za-z0-9_-]+)"\}', plain)
    assert m, plain
    return m.group(1).decode("ascii"), private_of(unb64u(m.group(2).decode("ascii")))


def recovery_keys(account, code):
    """rk = HKDF(the code's 20 bytes, "recovery|" + account), an X25519 private key; and RK."""
    rk = X25519PrivateKey.from_private_bytes(hkdf(code, f"{V} recovery|{account}"))
    return rk, raw_public(rk)


def recovery_anchor(account, code, mkid, nonce=None):
    return gcm_seal(hkdf(code, anchor_aad(account)), canonicalize({"anchor": mkid}), anchor_aad(account), nonce)


def recovery_text(code):
    """The code as it is shown once: base32 (RFC 4648, no padding), in groups of four."""
    text = base64.b32encode(code).decode("ascii").rstrip("=")
    return " ".join(text[i:i + 4] for i in range(0, len(text), 4))


def recovery_bytes(text):
    return base64.b32decode(re.sub(r"[\s-]+", "", text).upper())


def grant(account, public, mk, to, mkid, ephemeral=None, nonce=None):
    return seal_bytes(public, mk, grant_aad(account, to, mkid), ephemeral, nonce)


def chain_link(account, newer, older, nonce=None):
    return gcm_seal(subkey(account, newer, "chain"), older, chain_aad(account, mk_id(account, newer),
                                                                      mk_id(account, older)), nonce)


def seal_roster(account, mk, credentials, recovery, nonce=None):
    plain = canonicalize({"credentials": [{"credential_id": c["credential_id"], "passkey_id": c["passkey_id"],
                                           "public_key": c["public_key"]} for c in credentials],
                          "recovery": recovery})
    return gcm_seal(subkey(account, mk, "roster"), plain, roster_aad(account, mk_id(account, mk)), nonce)


def wrap_content_key(account, mk, ek, nonce=None):
    kid = key_id(raw_public(ek))
    return gcm_seal(subkey(account, mk, "content-wrap"), pkcs8(ek), content_key_aad(account, kid, mk_id(account, mk)),
                    nonce)


def compacted(timeout=30):
    """The compactions begun after a sealing in place (e2e_keys.compact_soon), each in a thread of its own, finished."""
    for worker in threading.enumerate():
        if worker.name == "ade-e2e-compact":
            worker.join(timeout)
            assert not worker.is_alive(), "a compaction did not finish"


def flag(stated):
    """What the page writes in the pair command's --e2e from the ticket's answer (Channel.tsx, e2eFlag)."""
    if stated is None:
        return "0:none"
    return f"{stated['seq']}:{'off' if stated['key_id'] is None else stated['key_id']}"


class Page:
    """The owner's page: the account's keys made, turned on, rotated and revoked, a passkey added and anchors renewed
    as e2eKeys.ts does, through the real routes; unlocked from what GET /api/ade/e2e serves, as openRing does; and each
    sealed text opened. Each passkey's PRF output for a salt (`prf`) is made here: the server never sees one."""

    def __init__(self, client, owner, device):
        self.client, self.owner, self.device = client, owner, device
        self.account = client.get("/api/auth/me", headers=owner).json()["id"]
        self.credential_id = b64u(device.credential_id)
        self.secrets = {self.credential_id: os.urandom(32)}
        self.devices = {self.credential_id: device}         # each enrolled passkey's authenticator, for its own touch
        self.code = os.urandom(20)
        self.mk = None

    def prf(self, credential_id, salt):
        """A passkey's PRF output for a salt, as its authenticator gives it: HMAC-SHA256 under a secret of its own."""
        return hmac.new(self.secrets[credential_id], salt, hashlib.sha256).digest()

    def view(self):
        r = self.client.get("/api/ade/e2e", headers=self.owner)          # settles a change whose hold has ended
        assert r.status_code == 200, r.text
        return r.json()

    def passkey_id(self):
        return next(p["id"] for p in self.view()["passkeys"] if p["credential_id"] == self.credential_id)

    def statement(self, key=None, retires="all", purpose="e2e-key"):
        """The next statement, chained as nextLink checks the served ones, signed with the passkey."""
        served_ = self.view()["statements"]
        prev = ""
        for n, s in enumerate(served_):
            payload = json.loads(s["payload"])
            assert (s["seq"], payload["seq"], payload["prev"]) == (n + 1, n + 1, prev)
            assert canonicalize(payload).decode("utf-8") == s["payload"]
            prev = hashlib.sha256(s["payload"].encode("utf-8")).hexdigest()
        n = self.client.post("/api/ade/link/nonce", headers=self.owner, json={"purpose": purpose}).json()
        p = {"v": 2, "deployment": n["deployment"], "purpose": purpose, "account": n["account"], "nonce": n["nonce"],
             "id": os.urandom(16).hex(), "seq": len(served_) + 1, "prev": prev, "created_at": n["server_time"],
             "expires_at": n["server_time"] + 600}
        assert n["account"] == self.account
        if purpose == "e2e-key":
            p.update(key=b64u(key), key_id=key_id(key), retires=retires)
        credential = self.device.get(as_step(canonicalize(p)))
        return {"payload": p, "credential": credential}

    def turn_on(self, seal=True):
        """In the design's order: the recovery code first, then the master key, the content key pair, the
        credential's pair and the statement; the first touch signs it and wraps the credential's key under the PRF,
        the second opens that wrap again; the recovery grant opened with the rk just derived, and the content key's
        wrap with its subkey, before anything is sent; then one request with everything."""
        account = self.account
        rk, rk_public = recovery_keys(account, self.code)
        self.mk = os.urandom(32)
        mkid = mk_id(account, self.mk)
        ek = X25519PrivateKey.generate()
        ck = X25519PrivateKey.generate()
        statement = self.statement(raw_public(ek), retires="all")
        salt = os.urandom(32)
        prf = self.prf(self.credential_id, salt)
        wrapped = credential_wrap(account, self.credential_id, prf, ck, mkid)
        anchor, again = open_credential_wrap(account, self.credential_id, prf, wrapped)           # the second touch
        assert anchor == mkid and raw_public(again) == raw_public(ck)
        grants = [{"to": self.credential_id, "sealed": grant(account, raw_public(ck), self.mk, self.credential_id,
                                                              mkid)},
                  {"to": "recovery", "sealed": grant(account, rk_public, self.mk, "recovery", mkid)}]
        passkey_id = self.passkey_id()
        roster = seal_roster(account, self.mk, [{"credential_id": self.credential_id, "passkey_id": passkey_id,
                                                 "public_key": b64u(raw_public(ck))}], b64u(rk_public))
        content = {"key_id": key_id(raw_public(ek)), "public_key": b64u(raw_public(ek)), "mk_id": mkid,
                   "wrapped": wrap_content_key(account, self.mk, ek)}
        assert open_bytes(grants[1]["sealed"], grant_aad(account, "recovery", mkid), rk) == self.mk
        assert raw_public(private_of(gcm_open(subkey(account, self.mk, "content-wrap"), content["wrapped"],
                                              content_key_aad(account, content["key_id"], mkid)))) == raw_public(ek)
        body = {"statement": statement, "content_key": content, "mk_id": mkid,
                "credential": {"passkey_id": passkey_id, "salt": b64u(salt),
                               "public_key": b64u(raw_public(ck)), "wrapped": wrapped},
                "recovery": {"public_key": b64u(rk_public), "anchor": recovery_anchor(account, self.code, mkid)},
                "grants": grants, "roster": roster, "seal": seal}
        r = self.client.post("/api/ade/e2e/turn-on", headers=self.owner, json=body)
        assert r.status_code == 200 and r.json()["hold"], r.text           # held, as every change is
        view = self.view()                                                  # its hold (no time here) ends at this read
        assert view["on"] is True and view["newest"]["key_id"] == key_id(raw_public(ek)), view
        compacted()                         # the database's compaction after the sealing runs in a thread of its own
        sealed = {k: v for k, v in view["sealed"].items() if k != "at"} if seal and view["sealed"] else None
        return {"seq": view["newest"]["seq"], "key_id": view["newest"]["key_id"], "sealed": sealed}, \
            key_id(raw_public(ek))

    def rotate(self):
        """A routine rotation: a new content key wrapped under the current master key, and its statement (retires
        grace). Held, and settled at once here (the hold is 0)."""
        ek = X25519PrivateKey.generate()
        content = {"key_id": key_id(raw_public(ek)), "public_key": b64u(raw_public(ek)),
                   "mk_id": mk_id(self.account, self.mk), "wrapped": wrap_content_key(self.account, self.mk, ek)}
        r = self.client.post("/api/ade/e2e/rotate", headers=self.owner,
                             json={"statement": self.statement(raw_public(ek), retires="grace"), "content_key": content})
        assert r.status_code == 200, r.text
        assert self.view()["newest"]["key_id"] == content["key_id"]                 # its hold ended: taken
        return content["key_id"]

    def revoke(self):
        """A revocation with a new recovery code: a new master key chained from the current one,
        granted to the credentials the roster names and to the new code's key; every content key wrapped again under
        it, a new content key, and a statement that retires every earlier key at once. Held, and settled at once."""
        account, view = self.account, self.view()
        old, old_id = self.mk, view["mk_id"]
        roster = json.loads(gcm_open(subkey(account, old, "roster"), view["roster"]["sealed"], roster_aad(account,
                                                                                                         old_id)))
        new = os.urandom(32)
        new_id = mk_id(account, new)
        code = os.urandom(20)
        _, rk_public = recovery_keys(account, code)
        held = self.unlock()
        ek = X25519PrivateKey.generate()
        grants = [{"to": c["credential_id"], "sealed": grant(account, unb64u(c["public_key"]), new, c["credential_id"],
                                                             new_id)} for c in roster["credentials"]]
        grants.append({"to": "recovery", "sealed": grant(account, rk_public, new, "recovery", new_id)})
        body = {"statement": self.statement(raw_public(ek), retires="all"),
                "content_key": {"key_id": key_id(raw_public(ek)), "public_key": b64u(raw_public(ek)), "mk_id": new_id,
                                "wrapped": wrap_content_key(account, new, ek)},
                "mk_id": new_id, "chain": {"previous": old_id, "wrapped": chain_link(account, new, old)},
                "grants": grants, "roster": seal_roster(account, new, roster["credentials"], b64u(rk_public)),
                "content_keys": [{"key_id": k["key_id"], "wrapped": wrap_content_key(account, new, held[k["key_id"]])}
                                 for k in view["content_keys"]],
                "remove": [], "high_security": False,
                "recovery": {"public_key": b64u(rk_public), "anchor": recovery_anchor(account, code, new_id)}}
        r = self.client.post("/api/ade/e2e/revoke", headers=self.owner, json=body)
        assert r.status_code == 200, r.text
        self.mk, self.code = new, code
        assert self.view()["mk_id"] == new_id                                      # its hold ended: taken
        return body["content_key"]["key_id"]

    def master(self):
        """The current master key, opened from its grant to this page's own passkey's credential, as withMasterBytes
        opens it."""
        account, view = self.account, self.view()
        to = self.credential_id
        row = next(c for c in view["credentials"] if c["credential_id"] == to)
        _, private = open_credential_wrap(account, to, self.prf(to, unb64u(row["salt"])), row["wrapped"])
        (g,) = [g for g in view["grants"] if g["mk_id"] == view["mk_id"] and g["to"] == to]
        return open_bytes(g["sealed"], grant_aad(account, to, view["mk_id"]), private)

    def revocation(self, remove, with_code=False):
        """A revocation that removes the passkeys `remove` (their ids) and keeps the recovery code, as Revoke makes one
        from the keys this page's own passkey unlocks: a new master key chained from the current one, granted to the
        roster's credentials that stay and to the recovery key the roster names; every content key wrapped again under
        it; a new content key and its statement, signed with this page's passkey. With `with_code`, the recovery code's
        proof is asked for after that touch and sent with it, as signAndSendWithCode does. The server's answer."""
        account, view = self.account, self.view()
        old, old_id = self.master(), view["mk_id"]
        held = self.unlock()
        roster = json.loads(gcm_open(subkey(account, old, "roster"), view["roster"]["sealed"], roster_aad(account,
                                                                                                         old_id)))
        kept = [c for c in roster["credentials"] if c["passkey_id"] not in remove]
        new = os.urandom(32)
        new_id = mk_id(account, new)
        ek = X25519PrivateKey.generate()
        grants = [{"to": c["credential_id"], "sealed": grant(account, unb64u(c["public_key"]), new, c["credential_id"],
                                                             new_id)} for c in kept]
        grants.append({"to": "recovery", "sealed": grant(account, unb64u(roster["recovery"]), new, "recovery", new_id)})
        body = {"statement": self.statement(raw_public(ek), retires="all"),                      # the touch
                "content_key": {"key_id": key_id(raw_public(ek)), "public_key": b64u(raw_public(ek)), "mk_id": new_id,
                                "wrapped": wrap_content_key(account, new, ek)},
                "mk_id": new_id, "chain": {"previous": old_id, "wrapped": chain_link(account, new, old)},
                "grants": grants, "roster": seal_roster(account, new, kept, roster["recovery"]),
                "content_keys": [{"key_id": k["key_id"], "wrapped": wrap_content_key(account, new, held[k["key_id"]])}
                                 for k in view["content_keys"]],
                "remove": list(remove), "high_security": False}
        if with_code:
            body["recovery_proof"] = self.proof("revoke")
        return self.client.post("/api/ade/e2e/revoke", headers=self.owner, json=body)

    def proof(self, purpose):
        """The recovery code's proof for one change of `purpose`, as recoveryProof makes it: the server's fresh X25519
        key and nonce, answered with the HMAC the code's rk makes."""
        rk, _ = recovery_keys(self.account, self.code)
        r = self.client.post("/api/ade/e2e/recovery-proof", headers=self.owner, json={"purpose": purpose})
        assert r.status_code == 200, r.text
        c = r.json()
        key = hkdf(rk.exchange(X25519PublicKey.from_public_bytes(unb64u(c["server_key"]))), f"{V} recovery proof")
        return {"challenge_id": c["challenge_id"],
                "proof": b64u(hmac.new(key, unb64u(c["nonce"]) + purpose.encode("utf-8"), hashlib.sha256).digest())}

    def enrol(self, device):
        """A passkey of the account added to the keys: its key pair wrapped under its PRF output
        for a salt of its own, with the current master key as its anchor; the master key sealed to it; the roster
        rewritten to name it, with the added passkey's own confirmation (its second touch, over the challenge derived
        for e2e-enrol-own); confirmed with a touch of this page's own passkey, which unlocks the keys already (never
        the one being added). Held, and settled at once. Its credential id."""
        account, view = self.account, self.view()
        cid = b64u(device.credential_id)
        self.secrets[cid] = os.urandom(32)
        self.devices[cid] = device
        passkey_id = next(p["id"] for p in view["passkeys"] if p["credential_id"] == cid)
        mkid = view["mk_id"]
        roster = json.loads(gcm_open(subkey(account, self.mk, "roster"), view["roster"]["sealed"],
                                     roster_aad(account, mkid)))
        ck, salt = X25519PrivateKey.generate(), os.urandom(32)
        mine = {"credential_id": cid, "passkey_id": passkey_id, "public_key": b64u(raw_public(ck))}
        own = confirmed(self.client, self.owner, device, purpose="e2e-enrol-own")["confirmation"]
        body = {"credential": {"passkey_id": passkey_id, "salt": b64u(salt), "public_key": b64u(raw_public(ck)),
                               "wrapped": credential_wrap(account, cid, self.prf(cid, salt), ck, mkid),
                               "confirmation": own},
                "mk_id": mkid, "grant": grant(account, raw_public(ck), self.mk, cid, mkid),
                "roster": seal_roster(account, self.mk, roster["credentials"] + [mine], roster["recovery"]),
                **confirmed(self.client, self.owner, self.device, purpose="e2e-enrol-touch")}
        r = self.client.post("/api/ade/e2e/credentials", headers=self.owner, json=body)
        assert r.status_code == 200 and r.json()["hold"], r.text
        assert cid in [c["credential_id"] for c in self.view()["credentials"]]      # its hold ended: taken
        return cid

    def renew_anchors(self):
        """Renewing anchors as RenewAnchors does: each credential's own wrap opened with its passkey's PRF output for
        the salt the server serves, and made again under the output for a fresh salt, naming the current master key;
        all sent in one request with their fresh salts, confirmed with a touch of this page's own passkey, and each
        other passkey's anchor with a confirmation of its own touch. Held, and settled at once. {credential_id: its
        fresh salt}."""
        account, view = self.account, self.view()
        anchors, salts = [], {}
        for c in view["credentials"]:
            cid = c["credential_id"]
            _, ck = open_credential_wrap(account, cid, self.prf(cid, unb64u(c["salt"])), c["wrapped"])
            fresh = os.urandom(32)
            own = {} if cid == self.credential_id else confirmed(self.client, self.owner, self.devices[cid],
                                                                 purpose="e2e-anchor-touch")
            anchors.append(dict({"passkey_id": c["passkey_id"], "salt": b64u(fresh),
                                 "wrapped": credential_wrap(account, cid, self.prf(cid, fresh), ck, view["mk_id"])},
                                **own))
            salts[cid] = b64u(fresh)
        r = self.client.post("/api/ade/e2e/anchors", headers=self.owner,
                             json={"anchors": anchors, "mk_id": view["mk_id"],
                                   **confirmed(self.client, self.owner, self.device, purpose="e2e-anchor-touch")})
        assert r.status_code == 200 and r.json()["hold"], r.text
        assert {c["credential_id"]: c["salt"] for c in self.view()["credentials"]} == salts    # its hold ended: taken
        return salts

    def unlock(self, code=None, credential_id=None):
        """openRing, from what the server serves: the credential's own wrap opened with its passkey's PRF output for
        the salt served with it (or the recovery key from the code and its anchor), the grant of the master key the
        server says is current, the chain walked back to the anchor with no fork, the roster naming the key that
        unlocked, and every content key opened. The page's own passkey's credential unless `credential_id` names
        another. {key_id: private key}."""
        account, view = self.account, self.view()
        if code is None:
            to = credential_id or self.credential_id
            row = next(c for c in view["credentials"] if c["credential_id"] == to)
            anchor, private = open_credential_wrap(account, to, self.prf(to, unb64u(row["salt"])), row["wrapped"])
            assert b64u(raw_public(private)) == row["public_key"]
        else:
            private, rk_public = recovery_keys(account, code)
            assert b64u(rk_public) == view["recovery"]["public_key"]
            anchor = json.loads(gcm_open(hkdf(code, anchor_aad(account)), view["recovery"]["anchor"],
                                         anchor_aad(account)))["anchor"]
            to = "recovery"
        current = view["mk_id"]
        (g,) = [g for g in view["grants"] if g["mk_id"] == current and g["to"] == to]
        mk = open_bytes(g["sealed"], grant_aad(account, to, current), private)
        assert mk_id(account, mk) == current
        path, at, used = {current: mk}, current, []
        while at != anchor:
            (link,) = [c for c in view["chain"] if c["mk_id"] == at]
            older = gcm_open(subkey(account, path[at], "chain"), link["wrapped"], chain_aad(account, at,
                                                                                            link["previous"]))
            assert mk_id(account, older) == link["previous"] and link["previous"] not in path
            path[link["previous"]], at = older, link["previous"]
            used.append(link)
        assert not [c for c in view["chain"] if c not in used and c["previous"] in path]          # no fork
        assert view["roster"]["mk_id"] == current
        roster = json.loads(gcm_open(subkey(account, mk, "roster"), view["roster"]["sealed"], roster_aad(account,
                                                                                                        current)))
        if code is not None:
            assert roster["recovery"] == b64u(raw_public(private))
        else:
            assert any(c["credential_id"] == to and c["public_key"] == b64u(raw_public(private))
                       for c in roster["credentials"])
        keys = {}
        for k in view["content_keys"]:
            ek = private_of(gcm_open(subkey(account, path[k["mk_id"]], "content-wrap"), k["wrapped"],
                                     content_key_aad(account, k["key_id"], k["mk_id"])))
            assert b64u(raw_public(ek)) == k["public_key"] and key_id(raw_public(ek)) == k["key_id"]
            keys[k["key_id"]] = ek
        return keys


class Racing:
    """The owner's page's requests, each one preceded by a thief's script (`act`): the worst a script that keeps placing
    revocations can do, acting between any two requests the owner makes."""

    def __init__(self, client, act):
        self.client, self.act = client, act

    def get(self, *args, **kwargs):
        self.act()
        return self.client.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        self.act()
        return self.client.post(*args, **kwargs)


def opened(keys, envelope, aad_slot):
    """A sealed text opened as openWith does: by the key its header names, bound to its slot."""
    assert isinstance(envelope, str) and ENVELOPE.fullmatch(envelope), envelope
    return open_envelope(envelope, aad_slot, keys[unb64u(envelope[5:])[1:17].hex()])


def sealed_by(envelope):
    return unb64u(envelope[5:])[1:17].hex()


# ---- the rig ------------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch):
    """The owner's machine: its own home and Claude Code folder, none of the session's own variables, no History run
    or look for moves started in a process of its own, and a change to encryption held for no time (one that waits a
    day too: a revocation that makes a new recovery code)."""
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "ADE_OWNER_KEYS", "ADE_LINK_DIR",
                 "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "machine" / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "machine" / "claude"))
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "0")
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_RECOVERY_HOLD", "0")
    monkeypatch.setattr(ade_link, "_history_soon", lambda link, every=None: None)
    monkeypatch.setattr(ade_link, "_moves_soon", lambda link: None)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    return home


@pytest.fixture
def page(client, served):  # noqa: F811  (served: the imported fixture)
    owner = signed_up(client)
    device = Authenticator(rp="127.0.0.1", origin=served)
    assert add(client, owner, device).status_code == 200
    return Page(client, owner, device)


@pytest.fixture
def wire(monkeypatch):
    """What the link client sends, as it sends it: (method, path, body, the answer's status)."""
    sent, real = [], ade_link.Link.request

    def recording(self, method, path, body=None, *args, **kwargs):
        try:
            answer = real(self, method, path, body, *args, **kwargs)
        except ade_link.Refused as e:
            sent.append((method, path, body, e.status))
            raise
        sent.append((method, path, body, 200))
        return answer
    monkeypatch.setattr(ade_link.Link, "request", recording)
    return sent


def paired(page, served, capsys, tmp_path, name, keys=True, e2e=True):  # noqa: F811
    """A link paired as the page's pair command says: --keys, the account's passkeys pinned as it links, and --e2e, the
    account's newest statement as the ticket's answer gives it."""
    confirmation = page.client.post("/api/account/confirm", headers=page.owner,
                                    json={"password": PASSWORD, "change": "link-ticket:new"}).json()["confirmation"]
    t = page.client.post("/api/ade/link/tickets", headers=page.owner, json={"confirmation": confirmation}).json()
    d = tmp_path / "machine" / "links" / name
    argv = ["pair", "--url", served, "--ticket", t["ticket"].lower(), "--name", name, "--kind", "local", "--dir", d]
    argv += ["--keys", ",".join(t["keys"])] if keys else []
    argv += ["--e2e", flag(t["e2e"])] if e2e else []
    with at_once():                         # linked as it pairs: the wait is test_ade_link_pair_wait.py's
        code, out, err = run(capsys, *argv)
    assert code == 0, (out, err)
    return d, json.loads((d / "config.json").read_text())["link_id"], out, err


def conversation(home, entries):
    """The session's transcript where Claude Code keeps it, in a project history.json lists."""
    work = home / "work" / "app"
    work.mkdir(parents=True, exist_ok=True)
    folder = home.parent / "claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(work))
    _write(folder / f"{SESSION}.jsonl", entries)
    return folder / f"{SESSION}.jsonl", str(work)


def turns(cwd, pairs, title=None):
    out = [{"type": "ai-title", "aiTitle": title}] if title else []
    for (asked, at), (said, said_at) in zip(pairs[::2], pairs[1::2]):
        out += [_typed(asked, at, cwd), _said(said, said_at)]
    return out


def say(capsys, d, *argv):
    return run(capsys, "--dir", d, *argv)


def stop(monkeypatch, capsys, d, path):
    """Claude Code's Stop hook for the session: `mirror reply`, its input on stdin."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(path), "session_id": SESSION})))
    return say(capsys, d, "mirror", "reply")


def sends_everything(capsys, monkeypatch, home, d):
    """From the link: a note and a question to the owner, a History list with its conversation, and a mirrored turn.
    The question's seq."""
    (d / "history.json").write_text(json.dumps({"projects": ["~/work"], "conversations": True}))
    path, cwd = conversation(home, turns(str(home / "work" / "app"), FIRST, TITLE))
    code, out, err = say(capsys, d, "post", "note", NOTED)
    assert code == 0, (out, err)
    code, out, err = say(capsys, d, "post", "question", ASKED, "--to", "owner")
    assert code == 0, (out, err)
    asked = int(out.split()[-1])
    code, out, err = say(capsys, d, "history")
    assert code == 0 and "1 conversation(s) sent" in out, (out, err)
    _write(path, turns(cwd, LATER), mode="a")
    assert stop(monkeypatch, capsys, d, path)[0] == 0
    return asked


def stored(link_id):
    """What the server stores of a link's words: its events, its History rows, its conversations' messages."""
    with store.connect() as db:
        events = [dict(r) for r in db.execute("SELECT * FROM ade_link_events WHERE link_id = ? ORDER BY seq",
                                              (link_id,))]
        rows = [dict(r) for r in db.execute("SELECT * FROM ade_link_history WHERE link_id = ?", (link_id,))]
        talks = [dict(r) for r in db.execute("SELECT * FROM ade_link_history_talks WHERE link_id = ?", (link_id,))]
    return events, rows, [m for t in talks for m in json.loads(t["messages"])]


def all_sealed(link_id):
    """Every text the server stores of what the link sent on a sealed path is an envelope, and nothing else."""
    events, rows, messages = stored(link_id)
    assert events and rows and messages
    for e in events:
        assert ENVELOPE.fullmatch(e["text"]), e
    for r in rows:
        assert ENVELOPE.fullmatch(r["sealed"]) and (r["title"], r["project"], r["branch"], r["repositories"]) == (
            "", None, None, None), r
    for m in messages:
        assert ENVELOPE.fullmatch(m["text"]), m


def no_words_on_disk():
    """None of the words the link sent is in the database's files: the file, its write-ahead log, its shared memory."""
    for path in glob.glob(config.db_path() + "*"):
        with open(path, "rb") as fh:
            data = fh.read()
        for words in WORDS:
            assert words.encode("utf-8") not in data, (os.path.basename(path), words)


def opens_everything(page, keys, link_id):
    """Every sealed text the owner's page is served of the link opens with the page's keys, its slot rebuilt from what
    is served beside it, to what the link sent. The served views: the link's timeline (its own events, its mirrored
    notes among them, since no start hook registered the session), the History list, and the conversation."""
    client, owner, account = page.client, page.owner, page.account
    events = client.get(f"/api/ade/link/timeline/{link_id}", headers=owner).json()["events"]
    got = {}
    for e in events:
        if e["from_id"] != link_id:
            continue
        value = opened(keys, e["text"], slot(account, link_id, "event", e["kind"], e.get("mirror"), e["ref"],
                                             e["client_id"]))
        # a mirrored note names its session; the server tags it with that session only where the link's list has it
        # registered (none here), and else serves it with none, the session kept beside it
        served = e.get("session")
        assert (value["session"] == served or (served is None and e.get("mirror") and value["session"] == SESSION)) \
            and value["said_at"] == e.get("said_at"), (value, e)
        got[(e["kind"], e.get("mirror"))] = value["text"]
    assert got == {("note", None): NOTED, ("question", None): ASKED, ("note", "owner"): LATER[0][0],
                   ("note", "session"): LATER[1][0]}
    (row,) = [r for r in client.get("/api/ade/link/history", headers=owner).json()["sessions"]
              if r["link_id"] == link_id]
    assert (row["id"], row["withheld"], row["title"], row["project"]) == (SESSION, False, "", None)
    assert opened(keys, row["sealed"], slot(account, link_id, "history", SESSION, 0)) == {
        "title": TITLE, "project": "~/work/app", "branch": "main", "repositories": []}
    with pytest.raises(NotOpened):                                  # its slot says it is listed, not withheld
        opened(keys, row["sealed"], slot(account, link_id, "history", SESSION, 1))
    talk = client.get(f"/api/ade/link/history/{link_id}/{SESSION}", headers=owner).json()
    count = len(talk["messages"])
    assert [opened(keys, m["text"], slot(account, link_id, "talk", SESSION, i, count, talk["earlier"], m["who"],
                                         m.get("at"), m.get("ms")))
            for i, m in enumerate(talk["messages"])] == [{"text": t} for t, _ in FIRST]


def answer(page, served, link_id, question, digest):  # noqa: F811
    """The owner's answer to a question, signed as the page signs one: {ref, digest} beside the words."""
    n = page.client.post("/api/ade/link/nonce", headers=page.owner, json={"purpose": "answer"}).json()
    p = {"v": 2, "deployment": n["deployment"], "purpose": "answer", "account": page.account, "nonce": n["nonce"],
         "id": os.urandom(16).hex(), "to": link_id, "ref": question, "digest": digest, "text": "yes, tag it",
         "created_at": n["server_time"], "expires_at": n["server_time"] + 3600}
    credential = page.device.get(as_step(canonicalize(p)))
    r = page.client.post("/api/ade/link/commands", headers=page.owner, json={"payload": p, "credential": credential})
    assert r.status_code == 200, r.text
    return r.json()["seq"]


def question_as_served(page, link_id, seq):
    return next(e for e in page.client.get(f"/api/ade/link/timeline/{link_id}", headers=page.owner).json()["events"]
                if e["seq"] == seq)


# ---- the cases ----------------------------------------------------------------------------------------------------

def test_what_a_link_sends_the_owner_is_stored_sealed_and_opens_with_the_page_s_keys(page, served, capsys, tmp_path,
                                                                                       monkeypatch, machine, wire):
    answer_on, kid = page.turn_on()
    assert answer_on == {"seq": 1, "key_id": kid, "sealed": {"events": 0, "left": 0, "rows": 0, "talks": 0}}
    d, link_id, out, _ = paired(page, served, capsys, tmp_path, "mac")
    assert f"goes sealed to key {kid} (statement 1, signed by passkey " in out
    asked = sends_everything(capsys, monkeypatch, machine, d)

    # stored: an envelope in place of every text, nothing in the clear, none of the words in the database's files
    all_sealed(link_id)
    no_words_on_disk()
    sent = [b for m, p, b, s in wire if (m, p) == ("POST", "/events")]
    assert len(sent) == 4 and all(ENVELOPE.fullmatch(b["text"]) for b in sent)
    assert all(ENVELOPE.fullmatch(b["sealed"]) and "title" not in b
               for m, p, body, s in wire if (m, p) == ("PUT", "/history") for b in body["sessions"])

    # opened with the page's keys, unlocked from what the server keeps (the passkey's PRF, or the recovery code)
    keys = page.unlock()
    assert set(keys) == {kid} and set(page.unlock(code=page.code)) == {kid}
    opens_everything(page, keys, link_id)

    # the owner answers the sealed question with the digest of its text as served, and the session verifies it
    q = question_as_served(page, link_id, asked)
    assert "sealed_in_place" not in q                              # its link sealed it
    digest = link_commands.text_digest(q["text"])
    seq = answer(page, served, link_id, asked, digest)
    code, out, err = say(capsys, d, "show", seq)
    assert code == 0 and "  | yes, tag it" in out and f'(the answer to your question [{asked}]: "{ASKED}")' in out, \
        (out, err)


def test_a_link_without_the_key_holds_what_goes_sealed_and_sends_none_of_it_in_the_clear(page, served, capsys,
                                                                                       tmp_path, monkeypatch, machine,
                                                                                       wire):
    _, kid = page.turn_on()
    # paired with the account's newest statement named (--e2e) but no owner key pinned: it can verify no statement
    d, link_id, _, err = paired(page, served, capsys, tmp_path, "mac", keys=False)
    assert "no key to seal to" in err
    del wire[:]
    (d / "history.json").write_text(json.dumps({"projects": ["~/work"], "conversations": True}))
    path, cwd = conversation(machine, turns(str(machine / "work" / "app"), FIRST, TITLE))
    code, out, err = say(capsys, d, "post", "note", NOTED)
    assert code == 2 and "not posted: encryption may be on" in err and "The words stay on this machine" in err
    code, out, err = say(capsys, d, "history")
    assert code == 2 and "nothing listed: encryption may be on" in out and "Nothing was sent" in out, (out, err)
    _write(path, turns(cwd, LATER), mode="a")
    assert stop(monkeypatch, capsys, d, path)[0] == 0
    # nothing went: no event, no list (an empty one would take the server's rows away), no conversation
    assert [(m, p) for m, p, b, s in wire if m in ("POST", "PUT") and p != "/heartbeat"] == []
    assert stored(link_id) == ([], [], [])
    assert "held: encryption" in (d / "mirror.log").read_text()

    # once the owner's keys are pinned it takes the statement, and what it held goes, sealed
    code, out, err = say(capsys, d, "pin")
    assert code == 0, (out, err)
    code, out, err = say(capsys, d, "e2e")
    assert code == 0 and f"goes sealed to key {kid}" in out, (out, err)
    assert say(capsys, d, "history")[0] == 0
    # the Stop that held kept its place from the start of what it read, so the next one posts all of it, sealed
    assert stop(monkeypatch, capsys, d, path)[0] == 0
    all_sealed(link_id)
    posts = [(b, s) for m, p, b, s in wire if (m, p) == ("POST", "/events")]
    assert len(posts) == 4 and all(ENVELOPE.fullmatch(b["text"]) and s == 200 for b, s in posts)
    keys = page.unlock()
    notes = page.client.get(f"/api/ade/link/timeline/{link_id}", headers=page.owner).json()["events"]
    assert sorted(opened(keys, e["text"], slot(page.account, link_id, "event", "note", e["mirror"], None,
                                               e["client_id"]))["text"] for e in notes) == \
        sorted(t for t, _ in FIRST + LATER)
    assert "no longer held: encryption" in (d / "mirror.log").read_text()


def test_the_ade_s_409_has_the_link_read_the_statements_again_and_seal(page, served, capsys, tmp_path, machine, wire):
    # a link paired and pinned before encryption was turned on, which read then that there is no statement
    d, link_id, _, _ = paired(page, served, capsys, tmp_path, "mac")
    assert json.loads((d / "e2e.json").read_text())["server"] == {"seq": 0, "on": False}
    _, kid = page.turn_on()
    del wire[:]
    code, out, err = say(capsys, d, "post", "note", NOTED)
    assert code == 0, (out, err)
    # the text went in the clear and was refused (409, e2e required); the statements were read again; it went sealed
    (clear, again, sealed) = [(m, p, b, s) for m, p, b, s in wire]
    assert (clear[:2], clear[2]["text"], clear[3]) == (("POST", "/events"), NOTED, 409)
    assert (again[:2], again[3]) == (("GET", "/e2e"), 200)
    assert (sealed[:2], sealed[3], sealed_by(sealed[2]["text"])) == (("POST", "/events"), 200, kid)
    assert sealed[2]["client_id"] == clear[2]["client_id"]          # the same event, sealed
    (event,) = stored(link_id)[0]
    assert event["text"] == sealed[2]["text"]
    assert opened(page.unlock(), event["text"], slot(page.account, link_id, "event", "note", None, None,
                                                     event["client_id"])) == {
        "text": NOTED, "session": None, "said_at": None}
    assert page.view()["links"][0]["last_required_at"] is not None     # the server told it, and says so


def test_turning_on_seals_in_place_what_links_sent_in_the_clear_before(page, served, capsys, tmp_path, monkeypatch,
                                                                       machine):
    d, link_id, out, _ = paired(page, served, capsys, tmp_path, "mac")
    assert "encryption is off for this account" in out
    asked = sends_everything(capsys, monkeypatch, machine, d)
    events, rows, messages = stored(link_id)
    assert {e["text"] for e in events} == {NOTED, ASKED} | {t for t, _ in LATER}       # in the clear, as sent
    assert rows[0]["title"] == TITLE and {m["text"] for m in messages} == {t for t, _ in FIRST}

    answer_on, kid = page.turn_on(seal=True)
    assert answer_on["sealed"] == {"events": 4, "left": 0, "rows": 1, "talks": 1}
    all_sealed(link_id)
    no_words_on_disk()                                  # the database rebuilt and its log emptied after
    keys = page.unlock()
    opens_everything(page, keys, link_id)

    # a question still open when encryption was turned on: its session keeps the digest of the words it asked in the
    # clear, so the page signs that one (the view says the server sealed it), and the session verifies the answer
    q = question_as_served(page, link_id, asked)
    assert q["sealed_in_place"] is True
    words = opened(keys, q["text"], slot(page.account, link_id, "event", "question", None, None, q["client_id"]))
    seq = answer(page, served, link_id, asked, link_commands.text_digest(words["text"]))
    code, out, err = say(capsys, d, "show", seq)
    assert code == 0 and f'(the answer to your question [{asked}]: "{ASKED}")' in out, (out, err)


def test_the_link_follows_a_rotation_and_a_revocation_and_seals_to_the_new_key(page, served, capsys, tmp_path,
                                                                               machine, wire):
    _, first = page.turn_on()
    d, link_id, _, _ = paired(page, served, capsys, tmp_path, "mac")

    def post(words):
        code, out, err = say(capsys, d, "post", "note", words)
        assert code == 0, (out, err)
        return stored(link_id)[0][-1]

    assert sealed_by(post("one")["text"]) == first
    second = page.rotate()
    # the first key is retired an hour after: the next text may still go to it, and the answer says statement 2 is
    # newer, so the one after goes to the new key
    assert sealed_by(post("two")["text"]) in (first, second)
    assert sealed_by(post("three")["text"]) == second
    third = page.revoke()
    # every earlier key is retired at once: the link's text to the second is refused (409), and it reads the
    # statements again and seals to the third
    del wire[:]
    assert sealed_by(post("four")["text"]) == third
    assert [s for m, p, b, s in wire if (m, p) == ("POST", "/events")] == [409, 200]
    assert sealed_by([b for m, p, b, s in wire if (m, p) == ("POST", "/events")][0]["text"]) == second
    held = json.loads((d / "e2e.json").read_text())["statements"]
    assert [json.loads(s["payload"])["key_id"] for s in held] == [first, second, third]

    # the page, unlocked again from what the revocation left (the new master key, chained back to the credential's
    # anchor, and every content key wrapped again under it), and by the new recovery code, opens every text
    for keys in (page.unlock(), page.unlock(code=page.code)):
        assert set(keys) == {first, second, third}
        texts = [opened(keys, e["text"], slot(page.account, link_id, "event", "note", None, None, e["client_id"]))
                 ["text"] for e in stored(link_id)[0]]
        assert texts == ["one", "two", "three", "four"]


def test_renewed_anchors_keep_their_fresh_salts_and_each_renewed_passkey_unlocks(page, served, machine):  # noqa: F811
    """Two passkeys enrolled, then a revocation, so each one's own record names a master key that is no longer
    current; renewing anchors writes each record again under the key its passkey's PRF output gives for a fresh salt,
    naming the current master key. The server keeps each fresh salt with its record and serves it, so each renewed
    passkey unlocks from what the server serves, with no chain left to walk."""
    other = Authenticator(alg=-8, rp="127.0.0.1", origin=served)
    r = add(page.client, page.owner, other, name="Second", confirm_with=page.device)   # while encryption is off
    assert r.status_code == 200, r.text
    _, first = page.turn_on()
    second = page.enrol(other)
    third = page.revoke()
    view = page.view()
    before = {c["credential_id"]: c["salt"] for c in view["credentials"]}
    assert set(before) == {page.credential_id, second}
    for cid in before:                                    # each anchor names the master key before the revocation
        row = next(c for c in view["credentials"] if c["credential_id"] == cid)
        anchor, _ = open_credential_wrap(page.account, cid, page.prf(cid, unb64u(row["salt"])), row["wrapped"])
        assert anchor != view["mk_id"]
    salts = page.renew_anchors()
    view = page.view()
    for c in view["credentials"]:
        assert c["salt"] == salts[c["credential_id"]] != before[c["credential_id"]]
        anchor, _ = open_credential_wrap(page.account, c["credential_id"],
                                         page.prf(c["credential_id"], unb64u(c["salt"])), c["wrapped"])
        assert anchor == view["mk_id"]
    for cid in (page.credential_id, second):
        assert set(page.unlock(credential_id=cid)) == {first, third}, cid


def test_with_the_recovery_code_the_owner_s_revocation_stands_against_a_stolen_passkey_s_script(page, served, machine,
                                                                                             monkeypatch):
    """A stolen passkey (Phone) and the owner's (Laptop) are each other's only other passkey, so each may cancel a
    revocation of itself the other signs, and take its place ((iii)). The thief's script places a revocation of the
    owner's passkey whenever none of its own waits, acting between any two of the owner's requests: the owner's plain
    revocation, and cancelling the thief's with the recovery code and then revoking, are each taken the place of before
    their hold ends. The owner's revocation sent with the code's proof is code-backed, which only the code cancels:
    every revocation the script places after it is refused, the stolen passkey cannot cancel it, and when its hold ends
    the stolen passkey is gone."""
    phone = Authenticator(alg=-8, rp="127.0.0.1", origin=served)                # the passkey the thief holds
    assert add(page.client, page.owner, phone, name="Phone", confirm_with=page.device).status_code == 200
    page.turn_on()
    stolen = page.enrol(phone)
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "180")                     # changes wait from here on
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_RECOVERY_HOLD", "86400")
    mine = page.passkey_id()
    phones = next(p["id"] for p in page.view()["passkeys"] if p["credential_id"] == stolen)
    thief = Page(page.client, page.owner, phone)
    thief.secrets = page.secrets                                                # the stolen passkey's PRF, as it gives it
    tried = []

    def script():
        pending = thief.view()["pending"]
        if pending is not None and pending["made_by"] == phones:
            return                                                              # one of its own waits
        tried.append(thief.revocation([mine]).status_code)

    def replaced(hold):
        with store.connect() as db:
            return holds.get(db, hold["id"])["failed"] == e2e_keys.REPLACED

    script()
    assert tried == [200]
    pending = page.view()["pending"]
    assert (sorted(pending["cancel_by"]), pending["cancel_by_code"], pending["by_code"]) == \
        (sorted([mine, phones]), True, False)
    raw = page.client
    page.client = Racing(raw, script)
    # the owner's plain revocation takes the thief's place, and the script's next takes the place of the owner's
    for _ in range(2):
        r = page.revocation([phones])
        assert r.status_code == 200, r.text
        ours = r.json()["hold"]
        page.view()                                                             # the script acts before it
        assert replaced(ours)
    # cancelling the thief's with the code, then revoking: the same
    waiting = page.view()["pending"]["id"]
    r = page.client.post(f"/api/ade/holds/{waiting}/cancel", headers=page.owner,
                         json={"recovery_proof": page.proof("cancel")})
    assert r.status_code == 200, r.text
    r = page.revocation([phones])
    assert r.status_code == 200, r.text
    ours = r.json()["hold"]
    page.view()
    assert replaced(ours) and all(status == 200 for status in tried)
    # with the code's proof, the owner's revocation takes the waiting one's place, and stands
    r = page.revocation([phones], with_code=True)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    placed = len(tried)
    for _ in range(3):
        page.view()                                                             # the thief's script acts before each
    assert tried[placed:] == [409] * 3
    pending = page.view()["pending"]
    assert (pending["id"], pending["cancel_by"], pending["by_code"]) == (hold["id"], [], True)
    page.client = raw
    r = raw.post(f"/api/ade/holds/{hold['id']}/cancel", headers=page.owner,
                 json=confirmed(raw, page.owner, phone, purpose="e2e-cancel"))
    assert r.status_code == 403, r.text
    holds.settle(page.account, now=hold["takes_effect_at"] + 1)                 # its day has passed
    view = page.view()
    assert [p["id"] for p in view["passkeys"]] == [mine], view["passkeys"]
    assert [c["credential_id"] for c in view["credentials"]] == [page.credential_id]
    assert set(page.unlock()) == {k["key_id"] for k in view["content_keys"]}    # the owner's keys, under the new master


def test_a_link_says_what_it_can_seal_without_a_history_list(page, served, capsys, tmp_path, wire):
    """The link client says the envelope forms it can seal each time it reads the owner's statements, so the page
    knows them for a link that sends no History list: this one, paired with no
    history.json, has sent none."""
    page.turn_on()
    _, link_id, _, _ = paired(page, served, capsys, tmp_path, "mac")
    assert not any(p == "/history" for _, p, _, _ in wire) and any(p == "/e2e" for _, p, _, _ in wire)
    (seen,) = [entry for entry in page.view()["links"] if entry["link_id"] == link_id]
    assert seen["seals"] == ade_link._e2e_seals() == [1, 2, 3]


def test_the_page_s_constructions_here_are_the_page_s_vectors():
    """The page's side written here makes, from the page's vector file's fixed keys and nonces, exactly the bytes the
    page made (ui/src/e2eVectors.test.ts wrote them): the credential's own wrap, both grants, the chain, the roster,
    the content key's wrap, the recovery key and its anchor; and opens each of the page's envelopes."""
    v = _vectors("e2e_v1_page.json")
    account = v["account"]
    fixed = lambda raw: X25519PrivateKey.from_private_bytes(unb64u(raw))      # noqa: E731

    w = v["credential_wrap"]
    assert w["aad"] == credential_aad(account, w["credential_id"]) == w["credential_info"]
    assert credential_wrap(account, w["credential_id"], unb64u(w["prf"]), fixed(w["private"]), w["anchor"],
                           unb64u(w["nonce"])) == w["nonce_and_wrapped"]
    anchor, ck = open_credential_wrap(account, w["credential_id"], unb64u(w["prf"]), w["nonce_and_wrapped"])
    assert anchor == w["anchor"] and b64u(pkcs8(ck)) == w["pkcs8"]
    for g in (v["grant"], v["recovery_grant"]):
        mk = unb64u(g["mk"])
        assert mk_id(account, mk) == g["mk_id"] and g["slot"] == grant_aad(account, g["to"], g["mk_id"])
        assert grant(account, unb64u(g["public"]), mk, g["to"], g["mk_id"], fixed(g["ephemeral_private"]),
                     unb64u(g["nonce"])) == g["envelope"]
        assert open_bytes(g["envelope"], g["slot"], fixed(g["private"])) == mk
    c = v["chain"]
    assert b64u(subkey(account, unb64u(c["mk"]), "chain")) == c["subkey"]
    assert chain_link(account, unb64u(c["mk"]), unb64u(c["previous_mk"]), unb64u(c["nonce"])) == c["nonce_and_wrapped"]
    r = v["roster"]
    assert b64u(subkey(account, unb64u(r["mk"]), "roster")) == r["subkey"]
    assert seal_roster(account, unb64u(r["mk"]), r["plaintext"]["credentials"], r["plaintext"]["recovery"],
                       unb64u(r["nonce"])) == r["nonce_and_wrapped"]
    k = v["content_key_wrap"]
    assert b64u(subkey(account, unb64u(k["mk"]), "content-wrap")) == k["subkey"]
    ek = private_of(unb64u(k["plaintext_pkcs8"]))
    assert wrap_content_key(account, unb64u(k["mk"]), ek, unb64u(k["nonce"])) == k["nonce_and_wrapped"]
    rec = v["recovery"]
    code = recovery_bytes(rec["code"])
    assert b64u(code) == rec["code_bytes"] and recovery_text(code) == rec["code"]
    rk, rk_public = recovery_keys(account, code)
    assert (b64u(rk.private_bytes_raw()), b64u(rk_public)) == (rec["private"], rec["public"])
    assert recovery_anchor(account, code, rec["anchor"]["mk_id"], unb64u(rec["anchor"]["nonce"])) == \
        rec["anchor"]["nonce_and_wrapped"]
    content = fixed(v["content_key"]["private"])
    assert key_id(raw_public(content)) == v["content_key"]["key_id"]
    for case in v["envelopes"]:
        assert open_envelope(case["envelope"], case["slot"], content) == case["plaintext"], case["name"]
        assert canonicalize(case["plaintext"]).decode("utf-8") == case["plaintext_text"], case["name"]


def test_the_link_client_s_vectors_open_here_and_the_client_still_makes_them():
    """The link client's vector file: each envelope the client makes again from its fixed key and nonce, byte for byte,
    and the page's side here opens; each one it refuses is refused here; its grants and its content key's wrap are the
    page's constructions too."""
    v = _vectors("e2e_v1.json")
    account = v["account"]
    fixed = lambda raw: X25519PrivateKey.from_private_bytes(unb64u(raw))      # noqa: E731
    content = fixed(v["content_key"]["private"])
    public = raw_public(content)
    for case in v["envelopes"]:
        assert ade_link.e2e_seal(public, case["slot"], case["plaintext"], fixed(case["ephemeral_private"]),
                                 unb64u(case["nonce"])) == case["envelope"], case["name"]
        assert open_envelope(case["envelope"], case["slot"], content) == case["plaintext"], case["name"]
    for case in v["refused"]:
        with pytest.raises(NotOpened):
            open_envelope(case["envelope"], case["slot"], content)
            pytest.fail(case["why"])
    for g in (v["grant"], v["recovery_grant"]):
        mk = unb64u(g["mk"])
        assert grant(account, unb64u(g["public"]), mk, g["to"], g["mk_id"], fixed(g["ephemeral_private"]),
                     unb64u(g["nonce"])) == g["envelope"]
        assert open_bytes(g["envelope"], g["slot"], fixed(g["private"])) == mk
    k = v["content_key_wrap"]
    assert wrap_content_key(account, unb64u(k["mk"]), private_of(unb64u(k["plaintext_pkcs8"])),
                            unb64u(k["nonce"])) == k["nonce_and_wrapped"]


def _vectors(name):
    with open(os.path.join(VECTORS, name), encoding="utf-8") as fh:
        return json.load(fh)
