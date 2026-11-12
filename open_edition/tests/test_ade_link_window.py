"""The signing window, the session's side (open_edition/ade_link.py): an instruction the owner's page signed under a
delegation their passkey signed.
Each case writes the event's row straight into the database, as the server writes one, so the session is seen holding
each limit itself, whatever the server stored; the server's word (the link's state) is the served app's."""

import base64
import hashlib
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

import ade_link
from proofpane_open import store
from proofpane_open.ade import link_commands  # noqa: F401  (puts the open packages on sys.path first)
from proofpane_core.canonical import canonicalize
from test_ade_link_client import link_row, run, served  # noqa: F401  (served: a fixture)
from test_ade_link_client_commands import (account, command, device, host, no_inbox, owner, payload,  # noqa: F401
                                           pinned_dir, pinned_fingerprint)
from test_passkeys import Authenticator, add, as_step

DIGEST_TAG = b"proofpane-delegation/1"      # what names a delegation
SIGNED_TAG = b"proofpane-delegated/1"       # what the page key signs before an instruction's bytes
SKEW = 60                                   # WINDOW_SKEW


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def own_fingerprint(d):
    link = ade_link.Link(str(d))
    return hashlib.sha256(link.public_key(link.key())).hexdigest()[:32]


def page_key():
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, spki


def key_fingerprint(spki):
    return hashlib.sha256(spki).hexdigest()[:32]


def digest(d):
    return hashlib.sha256(DIGEST_TAG + b"\x00" + canonicalize(d)).hexdigest()


def delegation(client, owner, served, d, life=300, start=None, key=None, **over):
    """A delegation for the link `mac` in directory `d`, opened `start` (10 seconds ago unless given), and its key."""
    start = int(time.time()) - 10 if start is None else start
    key, spki = key or page_key()
    mac = link_row("mac")
    dele = {"v": 2, "deployment": host(served), "purpose": "delegation", "account": account(client, owner),
            "nonce": "nonce-" + uuid.uuid4().hex, "id": uuid.uuid4().hex,
            "delegate": {"kind": "page-key", "alg": -7, "key": b64u(spki)},
            "sessions": [{"id": mac["id"], "fingerprint": own_fingerprint(d), "kinds": ["instruction"]}],
            "created_at": start, "expires_at": start + life}
    dele.update(over)
    return dele, (key, spki)


def instruction(client, owner, served, dele, n=1, text="run the tests", **over):
    mac = link_row("mac")
    p = payload(client, owner, served, "instruction", mac["id"], text, created_at=dele["created_at"] + 5,
                expires_at=dele["expires_at"])
    p.update({"delegation": digest(dele), "n": n})
    p.update(over)
    return p


def page_signed(key, p, tag=SIGNED_TAG):
    der = key.sign((tag + b"\x00" if tag else b"") + canonicalize(p), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def delegated(device, dele, keypair, p, **over):
    sig = {"delegation": {"payload": canonicalize(dele).decode("utf-8"),
                          "credential": device.get(as_step(canonicalize(dele)))},
           "sig": page_signed(keypair[0], p)}
    sig.update(over)
    return sig


def row(client, owner, p, signature, kind=None, withdrawn=False):
    """The event row the server writes for a delegated instruction (the delegation's digest beside it where the
    schema has that column)."""
    mac = link_row("mac")
    now = int(time.time())
    cols = ["user_id", "link_id", "client_id", "kind", "to_name", "ref", "text", "needs_turn", "received_at",
            "payload", "signature", "passkey_id", "to_id", "expires_at", "withdrawn_at"]
    vals = [account(client, owner), "owner", p["id"], kind or p["purpose"], mac["name"], p.get("ref"),
            p.get("text", ""), 1, now, canonicalize(p).decode("utf-8"), json.dumps(signature), "pk", mac["id"],
            p["expires_at"], now if withdrawn else None]
    with store.connect() as db:
        if "delegation" in {r[1] for r in db.execute("PRAGMA table_info(ade_link_events)")} and "delegation" in p:
            cols.append("delegation")
            vals.append(p["delegation"])
        return db.execute(f"INSERT INTO ade_link_events ({', '.join(cols)}) VALUES ({', '.join('?' * len(vals))})",
                          vals).lastrowid


def send(client, owner, served, device, d, n=1, dele=None, keypair=None, text="run the tests", **over):
    """One delegated instruction stored for `mac`: (seq, the delegation, its key pair, the instruction)."""
    if dele is None:
        dele, keypair = delegation(client, owner, served, d)
    p = instruction(client, owner, served, dele, n=n, text=text, **over)
    return row(client, owner, p, delegated(device, dele, keypair, p)), dele, keypair, p


def ended_on_server(client, owner, dele):
    """The delegation as the server keeps it once ended early, where the schema has its table."""
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ade_link_delegations'"
                          ).fetchone():
            return
        mac = link_row("mac")
        db.execute("INSERT INTO ade_link_delegations (digest, id, user_id, link_id, link_fingerprint, payload, "
                   "credential, passkey_id, created_at, expires_at, ended_at, ended_reason, last_n) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (digest(dele), dele["id"], account(client, owner), mac["id"], dele["sessions"][0]["fingerprint"],
                    canonicalize(dele).decode("utf-8"), "{}", "pk", dele["created_at"], dele["expires_at"],
                    int(time.time()), "end_now", 0))


def taken(seq):
    with store.connect() as db:
        return db.execute("SELECT taken_at FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()[0]


def records(d):
    path = d / "delegations.json"
    return json.loads(path.read_text()) if path.exists() else {}


# ---- taken ----------------------------------------------------------------------------------------------------------

def test_an_instruction_under_the_owner_s_delegation_is_taken_and_says_under_which(client, owner, served, device,
                                                                                  capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, dele, (_, spki), p = send(client, owner, served, device, d, text="run the tests\nthen stop")
    before = int(time.time())
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    after = int(time.time())
    lines = out.splitlines()
    assert code == 0, out
    when = ade_link._when
    said = (f"TAKEN BY YOU AND VERIFIED FROM THE OWNER, UNDER HIS DELEGATION: instruction {p['id']} to mac, made "
            f"{when(p['created_at'])}, expires {when(p['expires_at'])}, delegation {digest(dele)[:8]} (opened "
            f"{when(dele['created_at'])} by passkey {pinned_fingerprint(d)}, ends {when(dele['expires_at'])}, page key "
            f"{key_fingerprint(spki)}), instruction 1 of it, taken at ")   # the delegation's end too
    assert lines[1] in {f"{said}{when(t)}; [{seq}] is the ADE's number for it; {ade_link._follow(2)}"
                        for t in range(before, after + 1)}
    assert lines[2:5] == ["  | run the tests", "  | then stop", f"END {p['id']}: 2 lines"]
    kept = records(d)[digest(dele)]
    assert (kept["highest"], kept["passkey"], kept["key"], kept["start"], kept["end"]) == (
        1, pinned_fingerprint(d), key_fingerprint(spki), dele["created_at"], dele["expires_at"])
    assert taken(seq) is not None


def test_the_rule_says_a_delegation_opens_no_deploy_or_merge_window():
    assert "a delegation opens no deploy or merge window" in ade_link.RULE2


def test_the_rule_says_an_instruction_under_the_owner_s_delegation_is_his_as_any_other_is():
    # the rule's sentence, in lower case, so the head's marker UNDER HIS DELEGATION is in a take's output only where a
    # take printed one under a delegation
    assert "One it prints as taken under his delegation is the owner's as any other is" in ade_link.RULE2
    assert "UNDER HIS DELEGATION" not in ade_link.RULE2


def test_a_command_signed_with_the_passkey_is_still_taken_as_before(client, owner, served, device, capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq = command(client, owner, served, device, link_row("mac")["id"])
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 0 and "TAKEN BY YOU AND VERIFIED FROM THE OWNER: instruction " in out


# ---- refused, each limit -------------------------------------------------------------------------------------------

SHAPE = "is not of the form this session reads"
KINDS = "covers instructions alone"
LINK = "the owner's delegation is for another session"
LIFE = "lasts longer than 15 minutes, or less than one"
TIMES = "times are not within the owner's delegation"
SIGNATURE = "the page key's signature under the owner's delegation does not verify"


def _ed25519_spki():
    return Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


# each: (its name, what changes in the delegation, the instruction or the signature, the words of the refusal);
# a change to the signature is made after it is signed, every other before, so the passkey and the key sign it
AFTER = {"no sig", "a field more in the signature"}
REFUSED = [
    ("no sig", lambda x: x["sig"].pop("sig"), SHAPE),
    ("a field more in the signature", lambda x: x["sig"].update(more="x"), SHAPE),
    ("a field more in the delegation", lambda x: x["dele"].update(more="x"), SHAPE),
    ("the delegate a link", lambda x: x["dele"]["delegate"].update(kind="link"), SHAPE),
    ("the delegate's alg EdDSA", lambda x: x["dele"]["delegate"].update(alg=-8), SHAPE),
    ("the key not P-256", lambda x: x["dele"]["delegate"].update(key=b64u(_ed25519_spki())), SHAPE),
    ("two sessions", lambda x: x["dele"]["sessions"].append(dict(x["dele"]["sessions"][0])), SHAPE),
    ("a field more in the session", lambda x: x["dele"]["sessions"][0].update(name="mac"), SHAPE),
    ("kinds more than instruction", lambda x: x["dele"]["sessions"][0].update(kinds=["instruction", "answer"]),
     KINDS),
    ("kinds an answer", lambda x: x["dele"]["sessions"][0].update(kinds=["answer"]), KINDS),
    ("no kinds", lambda x: x["dele"]["sessions"][0].update(kinds=[]), KINDS),
    ("another link", lambda x: x["dele"]["sessions"][0].update(id="0" * 32), LINK),
    ("another key of it", lambda x: x["dele"]["sessions"][0].update(fingerprint="0" * 32), LINK),
    ("another delegation named", lambda x: x["p"].update(delegation="0" * 64), LINK),
    ("another deployment", lambda x: x["dele"].update(deployment="ade.example.com"), "for another deployment"),
    ("another account", lambda x: x["dele"].update(account="someone-else"), "names another account"),
    ("901 seconds", lambda x: x["dele"].update(expires_at=x["dele"]["created_at"] + 901), LIFE),
    ("59 seconds", lambda x: x["dele"].update(expires_at=x["dele"]["created_at"] + 59), LIFE),
    ("made before the delegation", lambda x: x["p"].update(created_at=x["dele"]["created_at"] - 1), TIMES),
    ("less than a minute left", lambda x: x["p"].update(created_at=x["dele"]["expires_at"] - 59), TIMES),
    ("its own end", lambda x: x["p"].update(expires_at=x["dele"]["expires_at"] - 1), TIMES),
    ("n zero", lambda x: x["p"].update(n=0), SHAPE),
]


@pytest.mark.parametrize("name,change,words", REFUSED, ids=[i for i, _, _ in REFUSED])
def test_each_limit_of_the_delegation_is_the_session_s_own(client, owner, served, device, capsys, tmp_path, name,
                                                           change, words):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d)
    p = instruction(client, owner, served, dele)
    x = {"dele": dele, "p": p}
    named = digest(dele)
    if name not in AFTER:
        change(x)
    if p["delegation"] == named:
        # the instruction names the delegation as changed, so each refusal is its own check's and not the name's (else
        # a changed link id would be refused only because the name no longer matched)
        p["delegation"] = digest(dele)
    x["sig"] = delegated(device, dele, keypair, p)
    if name in AFTER:
        change(x)
    seq = row(client, owner, p, x["sig"])
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code != 0 and words in out, out
    assert taken(seq) is None and digest(dele) not in records(d)


def test_a_delegation_signed_by_a_passkey_this_session_has_not_pinned_is_refused(client, owner, served, device,
                                                                                  capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    other = Authenticator(alg=-8, rp="127.0.0.1", origin=served)
    assert add(client, owner, other).status_code == 200          # the account's, after the pin: not pinned here
    seq, *_ = send(client, owner, served, other, d)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 7 and "signed by a passkey this session has not pinned" in out
    assert taken(seq) is None


@pytest.mark.parametrize("how", ["another key", "the bare bytes", "other words"])
def test_the_page_key_s_signature_is_checked_over_the_tag_and_the_bytes(client, owner, served, device, capsys,
                                                                        tmp_path, how):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d)
    p = instruction(client, owner, served, dele)
    sig = delegated(device, dele, keypair, p)
    sig["sig"] = {"another key": lambda: page_signed(page_key()[0], p),
                  "the bare bytes": lambda: page_signed(keypair[0], p, tag=None),
                  "other words": lambda: page_signed(keypair[0], dict(p, text="delete everything"))}[how]()
    seq = row(client, owner, p, sig)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 7 and SIGNATURE in out, out
    assert taken(seq) is None


# ---- the session's own clock --------------------------------------------------------------------------------------

def test_a_delegation_not_yet_started_by_this_session_s_clock_is_refused_for_now(client, owner, served, device,
                                                                                capsys, tmp_path, monkeypatch):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d, start=int(time.time()) + 120)
    seq, *_ = send(client, owner, served, device, d, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code != 0 and "the owner's delegation starts at " in out and "this session's clock reads " in out
    assert taken(seq) is None and records(d) == {}
    # for now and no more: the claim says a take of it can come, and one made once
    # this session's clock reaches the start takes it
    said = []
    claimed = ade_link._claim(ade_link.Link(str(d)), seq, ade_link.COMMAND_KINDS, lambda text, err=False: said.append(text))
    assert claimed[:2] == (7, False), said
    later = time.time() + 130

    class Clock:
        def __getattr__(self, name):
            return getattr(time, name)

        def time(self):
            return later
    monkeypatch.setattr(ade_link, "time", Clock())
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 0 and "UNDER HIS DELEGATION" in out and taken(seq) is not None, out


@pytest.mark.parametrize("ago,verified", [(SKEW + 1, False), (SKEW - 5, True)])
def test_a_delegation_ends_a_minute_after_its_end_by_this_session_s_clock(client, owner, served, device, capsys,
                                                                         tmp_path, ago, verified):
    """By the session's own check (show's, which take's is): the server refuses a take once the instruction's
    expires_at has passed by its clock, so a session takes after the end only from a server that lets it."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    now = int(time.time())
    dele, keypair = delegation(client, owner, served, d, start=now - 300 - ago)
    seq, *_ = send(client, owner, served, device, d, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "show", seq)
    if verified:
        assert code == 0 and "PREVIEW, VERIFIED FROM THE OWNER, UNDER HIS DELEGATION, NOT TAKEN" in out, out
    else:
        assert code == 7 and "the owner's delegation ended " in out and taken(seq) is None, out


def test_after_the_claim_an_instruction_is_acted_on_only_within_the_delegation_s_minute(client, owner, served,
                                                                                       device, capsys, tmp_path,
                                                                                       monkeypatch):
    """The ADE answers the take 91 seconds later by this session's clock: past the delegation's end and its minute,
    though within Stage 2a's 120 seconds of grace."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d, start=int(time.time()) - 270)      # 30 seconds left
    seq, *_ = send(client, owner, served, device, d, dele=dele, keypair=keypair)
    real = time

    class Clock:
        offset = 0

        def __getattr__(self, name):
            return getattr(real, name)

        def time(self):
            return real.time() + self.offset

    clock = Clock()
    request = ade_link.Link.request

    def late(self, method, path, *a, **kw):
        answer = request(self, method, path, *a, **kw)
        if method == "POST" and path == "/take":
            clock.offset = 91
        return answer
    monkeypatch.setattr(ade_link, "time", clock)
    monkeypatch.setattr(ade_link.Link, "request", late)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 2 and "not acted on" in out and "UNDER HIS DELEGATION" not in out, out


# ---- what only another kind of event carries ----------------------------------------------------------------------

def test_a_delegated_signature_on_an_answer_is_refused_by_take(client, owner, served, device, capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d)
    p = instruction(client, owner, served, dele, purpose="answer", ref=1, digest="0" * 64)
    seq = row(client, owner, p, delegated(device, dele, keypair, p), kind="answer")
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code != 0 and KINDS in out, out


def test_a_delegated_signature_on_a_move_is_refused_where_a_move_is_verified(client, owner, served, device, capsys,
                                                                             tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d)
    p = instruction(client, owner, served, dele, purpose="move", session="s1")
    p.pop("text")
    seq = row(client, owner, p, delegated(device, dele, keypair, p), kind="move")
    link = ade_link.Link(str(d))
    with pytest.raises(ade_link.NotVerified) as e:
        ade_link.verify_command(link, ade_link._fetch_event(link, seq), kinds=ade_link.MOVE_KINDS, expiry=False)
    assert e.value.why == "delegation_kinds"


def test_an_answer_or_a_resume_signed_under_a_delegation_never_verifies(client, owner, served, device, capsys,
                                                                       tmp_path):
    """A guard: each has its own verifier, which reads only its purpose's fields and a passkey's answer."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    link = ade_link.Link(str(d))
    dele, keypair = delegation(client, owner, served, d)
    answer = instruction(client, owner, served, dele, purpose="answer", ref=7, digest="0" * 64)
    with pytest.raises(ade_link.NotVerified) as e:
        ade_link.verify_prompt_answer(link, {"seq": 7, "digest": "0" * 64},
                                      {"seq": 9, "payload": canonicalize(answer).decode(),
                                       "signature": delegated(device, dele, keypair, answer)})
    assert e.value.why in ("fields", "client_data")
    resume = instruction(client, owner, served, dele, purpose="resume", text="")
    assert ade_link.verify_resume(link, {"payload": canonicalize(resume).decode(),
                                         "signature": delegated(device, dele, keypair, resume)},
                                  resume["created_at"] - 1) is None


# ---- the server's word ----------------------------------------------------------------------------------------------

def test_high_security_on_refuses_every_delegated_instruction(client, owner, served, device, capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, *_ = send(client, owner, served, device, d)
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (account(client, owner),))
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code != 0 and "has high security on" in out and taken(seq) is None, out


def test_a_delegation_the_server_says_was_ended_is_refused(client, owner, served, device, capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, dele, *_ = send(client, owner, served, device, d)
    ended_on_server(client, owner, dele)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code != 0 and "the owner's delegation ended on the ADE's word" in out and taken(seq) is None, out


# A stop ends the link's signing window, and the session holds that itself, whatever the server says: an instruction
# under a delegation that began at or before a stop this session saw is not taken. The server here says the delegation
# still holds; the stop is the session's own.
STOPPED = "the owner stopped this session at"


def _resumed_after_stop(d, at):
    """This directory's record of a stop at `at` that a resume lifted, as the hook writes it (resumes_used.json)."""
    (d / "resumes_used.json").write_text(json.dumps([{"id": "r-1", "for": at, "mark": "0123456789abcdef"}]))


def test_a_delegation_begun_before_a_stop_the_session_saw_is_refused_after_the_resume(client, owner, served, device,
                                                                                      capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, dele, *_ = send(client, owner, served, device, d)
    _resumed_after_stop(d, dele["created_at"] + 1)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 7 and STOPPED in out and taken(seq) is None, out


def test_a_delegation_begun_before_the_stop_the_session_holds_is_refused(client, owner, served, device, capsys,
                                                                       tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, dele, *_ = send(client, owner, served, device, d)
    (d / "stop_seen").write_text(f"{dele['created_at'] + 1} 0123456789abcdef")
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 7 and STOPPED in out and taken(seq) is None, out


def test_a_delegation_begun_at_the_second_of_a_stop_is_refused(client, owner, served, device, capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, dele, *_ = send(client, owner, served, device, d)
    _resumed_after_stop(d, dele["created_at"])
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 7 and STOPPED in out and taken(seq) is None, out


def test_a_delegation_begun_after_the_last_stop_is_taken(client, owner, served, device, capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, dele, *_ = send(client, owner, served, device, d)
    _resumed_after_stop(d, dele["created_at"] - 1)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 0 and STOPPED not in out and taken(seq) is not None, out


def test_an_instruction_under_a_delegation_of_the_page_s_fifteen_minutes_is_taken(client, owner, served, device,
                                                                                  capsys, tmp_path):
    # the page's longer window, 900 seconds, DELEGATION_LIFE_MAX itself: the session takes an instruction under it
    # (a `<` for the second `<=` of the delegation's life would refuse every one)
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d, life=ade_link.DELEGATION_LIFE_MAX)
    assert dele["expires_at"] - dele["created_at"] == 900
    seq, *_ = send(client, owner, served, device, d, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 0 and taken(seq) is not None, out


def test_a_record_of_resumes_that_cannot_be_read_takes_no_delegated_instruction(client, owner, served, device, capsys,
                                                                               tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, dele, *_ = send(client, owner, served, device, d)
    (d / "resumes_used.json").write_text("not a record")
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 2 and "record of the resumes that lifted its stops cannot be read" in out and taken(seq) is None, out


def test_a_withdrawn_delegated_instruction_is_not_taken(client, owner, served, device, capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d)
    p = instruction(client, owner, served, dele)
    seq = row(client, owner, p, delegated(device, dele, keypair, p), withdrawn=True)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code != 0 and "withdrawn, on the ADE's word" in out and "UNDER HIS DELEGATION" not in out, out


def test_a_session_pinned_for_the_second_screen_takes_no_delegated_instruction(client, owner, served, device,
                                                                              capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    code, out, err = run(capsys, "--dir", d, "pin", "--second-screen", "on")
    assert code == 0, err
    seq, *_ = send(client, owner, served, device, d)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 7 and "signed by the owner's page under his delegation" in out and taken(seq) is None, out


# ---- order, waiting, supersession and replay ------------------------------------------------------------------------

ORDER = "taken in the order the page sent them"
WAIT = "of the same delegation comes before it"


def test_a_later_instruction_waits_for_an_earlier_one_still_to_be_taken(client, owner, served, device, capsys,
                                                                        tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    first, dele, keypair, _ = send(client, owner, served, device, d, n=1)
    second, *_ = send(client, owner, served, device, d, n=2, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", second)
    assert code != 0 and WAIT in out and f"[{first}]" in out and taken(second) is None, out
    assert run(capsys, "--dir", d, "take", first)[0] == 0
    code, out, _ = run(capsys, "--dir", d, "take", second)
    assert code == 0 and "instruction 2 of it, taken at " in out, out
    assert records(d)[digest(dele)]["highest"] == 2


def test_an_earlier_instruction_delivered_after_a_later_one_was_taken_is_refused_for_good(client, owner, served,
                                                                                         device, capsys, tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    dele, keypair = delegation(client, owner, served, d)
    third, *_ = send(client, owner, served, device, d, n=3, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", third)
    assert code == 0 and "instruction 3 of it (1 to 2 were not taken here), taken at " in out, out
    late, *_ = send(client, owner, served, device, d, n=2, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", late)
    assert code == 7 and ORDER in out and taken(late) is None, out


def test_a_take_asked_and_not_answered_holds_the_ones_after_it(client, owner, served, device, capsys, tmp_path,
                                                               monkeypatch):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    first, dele, keypair, _ = send(client, owner, served, device, d, n=1)
    request = ade_link.Link.request

    def lost(self, method, path, *a, **kw):
        if method == "POST" and path == "/take":
            raise ade_link.Unreachable("the answer was lost")
        return request(self, method, path, *a, **kw)
    monkeypatch.setattr(ade_link.Link, "request", lost)
    assert run(capsys, "--dir", d, "take", first)[0] == 3                # no answer: take it again
    monkeypatch.setattr(ade_link.Link, "request", request)
    with store.connect() as db:                                           # the server did take it meanwhile
        db.execute("UPDATE ade_link_events SET taken_at = ?, taken_by = ? WHERE seq = ?",
                   (int(time.time()), link_row("mac")["id"], first))
    second, *_ = send(client, owner, served, device, d, n=2, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", second)
    assert code != 0 and WAIT in out and f"[{first}]" in out and taken(second) is None, out


def test_an_older_delegation_is_refused_once_a_newer_one_was_taken_under(client, owner, served, device, capsys,
                                                                         tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    now = int(time.time())
    old, old_keys = delegation(client, owner, served, d, start=now - 100)
    new, new_keys = delegation(client, owner, served, d, start=now - 50)
    newer, *_ = send(client, owner, served, device, d, dele=new, keypair=new_keys)
    assert run(capsys, "--dir", d, "take", newer)[0] == 0
    older, *_ = send(client, owner, served, device, d, dele=old, keypair=old_keys)
    code, out, _ = run(capsys, "--dir", d, "take", older)
    assert code == 7 and "opened a newer delegation" in out and taken(older) is None, out


def test_a_delegated_instruction_served_again_under_another_seq_is_a_replay(client, owner, served, device, capsys,
                                                                            tmp_path, monkeypatch):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    first, *_ = send(client, owner, served, device, d)
    assert run(capsys, "--dir", d, "take", first)[0] == 0
    # the server's unique client_id keeps a second row out; a server that lies serves the first under another seq
    real, again = ade_link._fetch_event, first + 1000
    monkeypatch.setattr(ade_link, "_fetch_event",
                        lambda link, seq: dict(real(link, first), seq=again) if int(seq) == again else real(link, seq))
    # show's check too, which reads no lock: without it the copy is previewed as verified
    code, out, _ = run(capsys, "--dir", d, "show", again)
    assert code == 7 and f"it is a copy of [{first}]" in out and "UNDER HIS DELEGATION" not in out, out
    code, out, _ = run(capsys, "--dir", d, "take", again)
    assert code == 7 and f"it is a copy of [{first}]" in out, out


def test_a_record_of_delegations_that_cannot_be_read_refuses_the_take(client, owner, served, device, capsys,
                                                                      tmp_path):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, *_ = send(client, owner, served, device, d)
    (d / "delegations.json").write_text("not json")
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code != 0 and "cannot be read" in out and "UNDER HIS DELEGATION" not in out and taken(seq) is None, out
    # its words say what waits, which a passkey's command does not
    assert "no instruction under a delegation is taken from this directory until it can be" in out, out
    other = command(client, owner, served, device, link_row("mac")["id"])
    assert run(capsys, "--dir", d, "take", other)[0] == 0


# ---- under take's lock: a take asked and not answered, the delegation's end, the record, the state ------------------

def _lose_the_take(monkeypatch):
    request = ade_link.Link.request

    def lost(self, method, path, *a, **kw):
        if method == "POST" and path == "/take":
            raise ade_link.Unreachable("the answer was lost")
        return request(self, method, path, *a, **kw)
    monkeypatch.setattr(ade_link.Link, "request", lost)
    return request


def test_a_take_asked_and_not_answered_then_withdrawn_holds_none_after_it(client, owner, served, device, capsys,
                                                                         tmp_path, monkeypatch):
    """The owner withdraws an instruction whose take went unanswered (the server never had it), and sends
    the next in the same window; the next is taken, since a withdrawn instruction is never taken (withdraw and take
    are each a conditional write), and the take of the withdrawn one says withdrawn without clearing what it held."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    first, dele, keypair, _ = send(client, owner, served, device, d, n=1)
    request = _lose_the_take(monkeypatch)
    assert run(capsys, "--dir", d, "take", first)[0] == 3                # no answer: take it again
    monkeypatch.setattr(ade_link.Link, "request", request)
    assert client.post(f"/api/ade/link/commands/{first}/withdraw", headers=owner).status_code == 200
    assert run(capsys, "--dir", d, "take", first)[0] != 0                # withdrawn, on the ADE's word
    second, *_ = send(client, owner, served, device, d, n=2, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", second)
    assert code == 0 and taken(second) is not None and taken(first) is None, out
    assert records(d)[digest(dele)]["asked"] is None and records(d)[digest(dele)]["highest"] == 2


def test_a_take_asked_and_not_answered_of_one_the_ade_holds_no_more_holds_none_after_it(client, owner, served, device,
                                                                                       capsys, tmp_path, monkeypatch):
    """The ADE holds no more an instruction whose take went unanswered (as an ADE restored from a backup taken before
    it), and answers 404 for it; the next of the window is taken, since this directory never printed the first and the
    ADE can no longer take it (without this case, a version that does not read a 404 or 410 as gone would pass; no
    answer of this server is a 410 for an event, so that half is pinned by no case)."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    first, dele, keypair, _ = send(client, owner, served, device, d, n=1)
    request = _lose_the_take(monkeypatch)
    assert run(capsys, "--dir", d, "take", first)[0] == 3                # no answer: take it again
    monkeypatch.setattr(ade_link.Link, "request", request)
    with store.connect() as db:
        db.execute("DELETE FROM ade_link_events WHERE seq = ?", (first,))
    second, *_ = send(client, owner, served, device, d, n=2, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", second)
    assert code == 0 and taken(second) is not None, out
    assert records(d)[digest(dele)]["asked"] is None and records(d)[digest(dele)]["highest"] == 2


@pytest.mark.parametrize("cut", ["after taken_ids.json", "after accepted"])
def test_a_take_recorded_here_and_not_marked_done_is_settled_from_the_record(client, owner, served, device, capsys,
                                                                            tmp_path, cut):
    """The take was recorded here and the directory stopped before its delegation's record was marked done: right after
    taken_ids.json, which take writes first (accepted and asking.json as before the ADE's answer), or with accepted
    written as well; the next take reads the take from this directory's own record and goes on (with the second as this
    case's only state, a version that does not read taken_ids.json would pass)."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    first, dele, keypair, p = send(client, owner, served, device, d, n=1)
    assert run(capsys, "--dir", d, "take", first)[0] == 0
    kept = records(d)
    kept[digest(dele)].update(highest=0, asked={"n": 1, "seq": first})   # as if cut short after taken_ids.json
    (d / "delegations.json").write_text(json.dumps(kept))
    if cut == "after taken_ids.json":
        (d / "accepted").write_text(json.dumps([s for s in json.loads((d / "accepted").read_text()) if s != first]))
        asking = json.loads((d / "asking.json").read_text()) if (d / "asking.json").exists() else {}
        (d / "asking.json").write_text(json.dumps(dict(asking, **{p["id"]: first})))
    second, *_ = send(client, owner, served, device, d, n=2, dele=dele, keypair=keypair)
    code, out, _ = run(capsys, "--dir", d, "take", second)
    assert code == 0 and taken(second) is not None, out
    assert records(d)[digest(dele)]["highest"] == 2 and records(d)[digest(dele)]["asked"] is None


def test_a_take_recorded_here_for_a_later_instruction_refuses_an_earlier_one_by_the_order(client, owner, served,
                                                                                         device, capsys, tmp_path,
                                                                                         monkeypatch):
    """The order once an asked take is settled: a server that hid instruction 1 from the state let 2 be taken first, and
    that take was recorded here and cut short before its delegation's record was marked; instruction 1 is then refused,
    as any is once a later one of its delegation was taken (without this case, a version with the order's check after
    the settling taken out would pass)."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    first, dele, keypair, _ = send(client, owner, served, device, d, n=1)
    second, *_ = send(client, owner, served, device, d, n=2, dele=dele, keypair=keypair, text="the second")
    request = ade_link.Link.request

    def hides_the_first(self, method, path, *a, **kw):
        answer = request(self, method, path, *a, **kw)
        if method == "GET" and path == "/state" and isinstance(answer, dict):
            opens = answer.get("delegations_open") or {}
            answer = dict(answer, delegations_open={k: [p for p in v if p[1] != first] for k, v in opens.items()})
        return answer
    monkeypatch.setattr(ade_link.Link, "request", hides_the_first)
    assert run(capsys, "--dir", d, "take", second)[0] == 0
    monkeypatch.setattr(ade_link.Link, "request", request)
    kept = records(d)
    kept[digest(dele)].update(highest=0, asked={"n": 2, "seq": second})   # as if cut short after taken_ids.json
    (d / "delegations.json").write_text(json.dumps(kept))
    code, out, _ = run(capsys, "--dir", d, "take", first)
    assert code == 7 and "was taken already" in out and taken(first) is None, out
    assert records(d)[digest(dele)]["highest"] == 2 and records(d)[digest(dele)]["asked"] is None


def test_a_take_asked_for_a_later_instruction_says_so_and_not_that_it_comes_first(client, owner, served, device,
                                                                                 capsys, tmp_path):
    """The take still to be answered is of instruction 3; taking 1 is refused for now, and the
    words say the take of 3 was asked, never that 3 comes before 1."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    first, dele, keypair, _ = send(client, owner, served, device, d, n=1)
    third, *_ = send(client, owner, served, device, d, n=3, dele=dele, keypair=keypair, text="the third")
    (d / "delegations.json").write_text(json.dumps({digest(dele): {
        "start": dele["created_at"], "end": dele["expires_at"], "highest": 0, "asked": {"n": 3, "seq": third}}}))
    code, out, _ = run(capsys, "--dir", d, "take", first)
    assert code != 0 and taken(first) is None, out
    assert f"instruction 3 ([{third}])" in out and "was asked and not answered" in out and WAIT not in out, out


def test_the_end_of_the_delegation_is_read_again_under_take_s_lock(client, owner, served, device, capsys, tmp_path,
                                                                    monkeypatch):
    """Verified 55 seconds after the end by this session's clock, and the read of the link's state takes
    20 seconds more; the take is refused before the ADE is asked, never taken there to be left undone here."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    now = int(time.time())
    dele, keypair = delegation(client, owner, served, d, start=now - 290)          # ends 10 seconds from now
    seq, *_ = send(client, owner, served, device, d, dele=dele, keypair=keypair)
    clock = {"t": float(dele["expires_at"] + 55)}

    class Clock:
        def __getattr__(self, name):
            return getattr(time, name)

        def time(self):
            return clock["t"]
    monkeypatch.setattr(ade_link, "time", Clock())
    request = ade_link.Link.request

    def slow(self, method, path, *a, **kw):
        if method == "GET" and path == "/state":
            clock["t"] += 20
        return request(self, method, path, *a, **kw)
    monkeypatch.setattr(ade_link.Link, "request", slow)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 7 and "the owner's delegation ended " in out and taken(seq) is None, out


def test_a_delegation_past_its_end_leaves_the_record_but_the_newest(client, owner, served, device, capsys, tmp_path):
    """The record of delegations keeps a delegation only while an instruction under it could still be taken
    (its end and the minute after), and the newest one whatever its end, which the supersession rule reads."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    now = int(time.time())
    old = {"start": now - 2000, "end": now - 1700, "highest": 3, "asked": None}
    (d / "delegations.json").write_text(json.dumps({"0" * 64: old}))
    seq, dele, *_ = send(client, owner, served, device, d)
    assert run(capsys, "--dir", d, "take", seq)[0] == 0
    assert set(records(d)) == {digest(dele)}


@pytest.mark.parametrize("state", [[], {}, {"high_security": False, "delegations_ended": []},
                                   {"high_security": False, "delegations_open": {}}])
def test_a_state_without_what_a_delegation_needs_refuses_the_take_for_now(client, owner, served, device, capsys,
                                                                          tmp_path, monkeypatch, state):
    """A state that is not an object, or lacks one of the three fields a delegated take reads, is one this
    session cannot read: the take is refused for now, never read as high security off and nothing ended."""
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    seq, *_ = send(client, owner, served, device, d)
    request = ade_link.Link.request

    def bare(self, method, path, *a, **kw):
        return state if (method, path) == ("GET", "/state") else request(self, method, path, *a, **kw)
    monkeypatch.setattr(ade_link.Link, "request", bare)
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 3 and "is not one this client reads" in out and taken(seq) is None, out


# ---- what the session tells the server, and the bytes --------------------------------------------------------------

@pytest.mark.parametrize("second_screen", [False, True])
def test_a_session_says_it_takes_delegations_on_each_read_of_its_state(client, owner, served, device, capsys,
                                                                       tmp_path, monkeypatch, second_screen):
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    if second_screen:
        assert run(capsys, "--dir", d, "pin", "--second-screen", "on")[0] == 0
    asked = []
    request = ade_link.Link.request

    def seen(self, method, path, body=None, query=None, *a, **kw):
        asked.append((method, path, query))
        return request(self, method, path, body, query, *a, **kw)
    monkeypatch.setattr(ade_link.Link, "request", seen)
    run(capsys, "--dir", d, "status")
    reads = [q for m, p, q in asked if (m, p) == ("GET", "/state")]
    assert reads, asked
    if second_screen:
        assert all(not q or "features" not in q for q in reads)
    else:
        assert all(q == {"features": "delegation-1"} for q in reads)


def test_a_link_paired_before_its_kind_was_kept_still_says_it_takes_delegations_as_it_learns_it(
        client, owner, served, device, capsys, tmp_path, monkeypatch):
    """The read of its state by which a link paired before pair kept its kind learns it says what this client takes,
    as every read of its state does: a read that said nothing would clear the ADE's record of it, and the page would
    offer no signing window until the next read that said it."""
    monkeypatch.delenv("ADE_LINK_KEEP", raising=False)              # as outside the tests: the keeper's look runs
    d = pinned_dir(client, owner, served, capsys, tmp_path)
    assert run(capsys, "--dir", d, "status")[0] == 0
    config = json.loads((d / "config.json").read_text())
    config.pop("kind", None)                                        # as a link paired before pair kept it
    (d / "config.json").write_text(json.dumps(config))
    asked = []
    request = ade_link.Link.request

    def seen(self, method, path, body=None, query=None, *a, **kw):
        asked.append((method, path, query))
        return request(self, method, path, body, query, *a, **kw)
    monkeypatch.setattr(ade_link.Link, "request", seen)
    run(capsys, "--dir", d, "heartbeat")
    reads = [q for m, p, q in asked if (m, p) == ("GET", "/state")]
    with store.connect() as db:
        kept = db.execute("SELECT features FROM ade_links WHERE id = ?", (link_row("mac")["id"],)).fetchone()[0]
    assert reads and all(q == {"features": "delegation-1"} for q in reads) and json.loads(kept) == ["delegation-1"], \
        (reads, kept)


def test_the_worked_example_holds_byte_for_byte():
    """The worked example: the delegation's canonical bytes, its passkey challenge and its digest, and the length
    and sha256 of what the page key signs for the first instruction under it."""
    spki = ec.derive_private_key(0x5157494E444F57, ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    dele = {"v": 2, "deployment": "ade.example.com", "purpose": "delegation", "account": "acct-example",
            "nonce": "nonce-example", "id": "d" * 32, "delegate": {"kind": "page-key", "alg": -7, "key": b64u(spki)},
            "sessions": [{"id": "7" * 32, "fingerprint": hashlib.sha256(bytes(range(32))).hexdigest()[:32],
                          "kinds": ["instruction"]}], "created_at": 1794326400, "expires_at": 1794326700}
    raw = ade_link.canonical(dele)
    assert len(raw) == 497 and raw == canonicalize(dele)
    assert ade_link.step_challenge(raw).hex() == "22f63dce768d06f8ed4ba69c856d36f5383d66d075a9a9ed610a055b46c3686d"
    named = getattr(ade_link, "delegation_digest", None)
    assert callable(named) and named(raw) == "15249fa6d400ff144d4e5362d9280593b3fbbc784d8551c7e42466dd6ef6980b"
    first = {"v": 2, "deployment": "ade.example.com", "purpose": "instruction", "account": "acct-example",
             "nonce": "nonce-example-2", "id": "a" * 32, "to": "7" * 32, "text": "Run the tests.",
             "created_at": 1794326440, "expires_at": 1794326700, "delegation": named(raw), "n": 1}
    signed = getattr(ade_link, "DELEGATED_TAG", b"") + b"\x00" + ade_link.canonical(first)
    assert len(signed) == 373
    assert hashlib.sha256(signed).hexdigest() == "c6099ce84add41d34bc2eb983896b58c65d4b31e195605f589f18cfd41663faf"
