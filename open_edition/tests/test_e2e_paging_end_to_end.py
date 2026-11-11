"""The link reads the owner's statements from the server itself a page at a time (GET /api/ade/link/e2e with `after`
and `limit`, answered with `more`): a chain longer than one page, each statement signed with the owner's passkey and
chained to the one before, stored as the server stores one it has taken. The rig is test_e2e_end_to_end's: the
owner's page through the real routes, the link client paired as the page's pair command says."""

import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import ade_link
from e2e_link_stand_in import b64u, key_id, raw_public
from proofpane_core.canonical import canonicalize
from proofpane_open import store
from proofpane_open.ade import e2e_keys
from test_ade_link_client import run, served  # noqa: F401  (served: a fixture)
from test_e2e_end_to_end import ENVELOPE, machine, page, paired, sealed_by, wire  # noqa: F401  (fixtures)
from test_passkeys import as_step

pytestmark = pytest.mark.skipif(not hasattr(e2e_keys, "FOR_LINK_PAGE"),
                                reason="this server serves every statement at once")


def chain(page, count):
    """`count` statements more, each a new key that retires the one before after its grace, chained to the account's
    newest and signed with the owner's passkey, written as the server writes one whose hold has ended. The newest
    key's id."""
    n = page.client.post("/api/ade/link/nonce", headers=page.owner, json={"purpose": "e2e-key"}).json()
    passkey_id = page.passkey_id()
    with store.connect() as db:
        top = db.execute("SELECT seq, payload FROM ade_e2e_statements WHERE user_id = ? ORDER BY seq DESC LIMIT 1",
                         (page.account,)).fetchone()
        seq, prev = top["seq"], hashlib.sha256(top["payload"].encode("utf-8")).hexdigest()
        for _ in range(count):
            seq += 1
            public = raw_public(X25519PrivateKey.generate())
            p = {"v": 2, "deployment": n["deployment"], "purpose": "e2e-key", "account": n["account"],
                 "nonce": os.urandom(16).hex(), "id": os.urandom(16).hex(), "seq": seq, "prev": prev,
                 "created_at": n["server_time"], "expires_at": n["server_time"] + 600, "key": b64u(public),
                 "key_id": key_id(public), "retires": "grace"}
            payload = canonicalize(p).decode("utf-8")
            credential = page.device.get(as_step(canonicalize(p)))
            db.execute("INSERT INTO ade_e2e_statements (user_id, seq, id, purpose, key_id, public_key, retires, "
                       "payload, credential, passkey_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (page.account, seq, p["id"], "e2e-key", p["key_id"], p["key"], "grace", payload,
                        json.dumps(credential), passkey_id, n["server_time"]))
            prev = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return p["key_id"]


def test_a_link_reads_every_page_of_a_chain_longer_than_one_and_seals_to_its_newest_key(page, served, capsys,
                                                                                        tmp_path, monkeypatch, wire):
    page.turn_on()
    newest = chain(page, e2e_keys.FOR_LINK_PAGE)                    # one statement past the server's first page
    total = e2e_keys.FOR_LINK_PAGE + 1
    pages, real = [], e2e_keys.for_link

    def for_link(link_row, now=None, after=0, limit=e2e_keys.FOR_LINK_PAGE, seals=None):
        answer = real(link_row, now, after=after, limit=limit, seals=seals)
        pages.append((after, limit, len(answer["statements"]), answer["more"], seals))
        return answer
    monkeypatch.setattr(e2e_keys, "for_link", for_link)
    d, link_id, out, err = paired(page, served, capsys, tmp_path, "mac")
    assert f"goes sealed to key {newest} (statement {total}, signed by passkey " in out, (out, err)
    # the first page's request says the forms the link can seal, the next does not
    assert pages[:2] == [(0, ade_link.E2E_PAGE, ade_link.E2E_PAGE, True, "1,2,3"),
                         (ade_link.E2E_PAGE, ade_link.E2E_PAGE, 1, False, None)]
    with open(os.path.join(d, "e2e.json"), encoding="utf-8") as fh:
        assert [json.loads(s["payload"])["seq"] for s in json.load(fh)["statements"]] == list(range(1, total + 1))
    code, out, err = run(capsys, "--dir", d, "post", "note", "sealed past the first page")
    assert code == 0, (out, err)
    (sent,) = [b for m, p, b, s in wire if (m, p) == ("POST", "/events")]
    assert ENVELOPE.fullmatch(sent["text"]) and sealed_by(sent["text"]) == newest
