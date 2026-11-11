"""Post-quantum keys, the link client's side (open_edition/ade_link.py):
a version-3 statement taken, its key held to its form; never a classical key stated after a post-quantum one,
also once e2e.json is lost; events and History rows sealed in form 2, a conversation whole under a key of its own
(form 3 beside its key in form 2); the forms the link says it can seal; a machine without ML-KEM holding what goes
sealed rather than sealing; what `e2e` and pairing say of the key; and the second screen's reading of a version-3
statement. Against the stand-in of the server's side (e2e_link_stand_in) and the page's openers written from the
design (e2e_pq_page_side), not from the client's code."""

import hashlib
import json
import os
import sys
import types

import pytest

import ade_link
import e2e_pq_page_side as page
from e2e_link_stand_in import ACCOUNT, LINK_ID, LinkStandIn, a_link, b64u, open_envelope, owner_key, slot
from test_ade_link_client import run
from test_ade_link_client_e2e import event_slot, held, seqs
from test_ade_link_client_e2e_history import rules, sessions, talk_slot
from test_ade_link_history import _said, _typed, _write

MLKEM_MODULE = "cryptography.hazmat.primitives.asymmetric.mlkem"
POST_QUANTUM = "post-quantum (X-Wing: ML-KEM-768 with X25519)"


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
    monkeypatch.delenv("ADE_OWNER_KEYS", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    monkeypatch.setattr(ade_link, "_moves_soon", lambda link: None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def ade():
    s = LinkStandIn()
    yield s
    s.close()


@pytest.fixture
def no_mlkem(monkeypatch):
    """A machine whose cryptography has no ML-KEM: its module cannot be imported, and the client asks again."""
    monkeypatch.setitem(sys.modules, MLKEM_MODULE, None)
    monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)


def hybrid(ade, retires="all", seed=None, **over):
    """A version-3 statement of a post-quantum key (from `seed`, else a fresh one), signed by the pinned passkey and
    kept as the server's newest: (statement, seed)."""
    seed = os.urandom(32) if seed is None else seed
    s, _ = ade.owner.state(public=page.public_of(seed), v=3, retires=retires, **over)
    return s, seed


def kid_of(statement):
    return json.loads(statement["payload"])["key_id"]


def with_coefficient(public, n, value):
    """`public` with the n-th 12-bit coefficient of its encapsulation key's 1,152 packed bytes set to `value`: two
    coefficients in each three bytes, the first in the low 12 bits."""
    raw, i = bytearray(public), 3 * (n // 2)
    if n % 2 == 0:
        raw[i], raw[i + 1] = value & 0xFF, (raw[i + 1] & 0xF0) | (value >> 8)
    else:
        raw[i + 1], raw[i + 2] = (raw[i + 1] & 0x0F) | ((value & 0x0F) << 4), value >> 4
    return bytes(raw)


def coefficient(public, n):
    i = 3 * (n // 2)
    return public[i] | (public[i + 1] & 0x0F) << 8 if n % 2 == 0 else public[i + 1] >> 4 | public[i + 2] << 4


# ---- statements of version 3 ----------------------------------------------------------------------------------------

def test_a_post_goes_sealed_in_form_2_to_the_post_quantum_key_of_a_version_3_statement(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()                                       # a classical key first, as an account upgraded later
    s, seed = hybrid(ade)
    code, _, err = run(capsys, "--dir", d, "post", "step", "built the list")
    assert code == 0, err
    (e,) = ade.events
    raw = page.body_of(e["text"])
    assert raw[0] == 2 and raw[1:17].hex() == kid_of(s) and len(raw) == 1149 + len(
        ade_link._e2e_plain({"text": "built the list", "session": None, "said_at": None})) + 16
    assert page.open_envelope2(seed, e["text"], event_slot(e)) == {"text": "built the list", "session": None,
                                                                    "said_at": None}
    with pytest.raises(page.NotOpened):                     # bound to its slot, as form 1 is
        page.open_envelope2(seed, e["text"], event_slot(dict(e, kind="question")))
    assert seqs(d) == [1, 2] and json.loads(held(d)["statements"][1]["payload"])["v"] == 3


def test_e2e_and_pairing_say_whether_the_key_is_classical_or_post_quantum(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    first, _ = ade.owner.state(retires="all")
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert code == 0 and f"ADE: key {kid_of(first)}, the newest this link took, is classical (X25519)" in out
    second, _ = hybrid(ade)
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert f"goes sealed to key {kid_of(second)}, from statement 2" in out
    assert f"ADE: key {kid_of(second)}, the newest this link took, is {POST_QUANTUM}" in out
    code, out, err = run(capsys, "pair", "--url", ade.url, "--ticket", "PP-AAAAAAAA", "--name", "mac", "--kind",
                         "local", "--dir", tmp_path / "new", "--keys", owner_key(ade.owner.device)["fingerprint"],
                         "--e2e", f"2:{kid_of(second)}")
    assert code == 0, err
    assert f"goes sealed to key {kid_of(second)} (statement 2, signed by passkey " in out
    assert f"; the key is {POST_QUANTUM}" in out


def test_e2e_says_whether_this_machine_can_seal_to_a_post_quantum_key_before_any_is_stated(ade, capsys, tmp_path,
                                                                                          monkeypatch):
    """The owner runs `ade_link.py e2e` on each machine before the upgrade:
    it says whether this machine can seal to a post-quantum key whatever key, if any, it holds."""
    d = a_link(ade, tmp_path / "mac")
    code, out, _ = run(capsys, "--dir", d, "e2e")                       # no statement at all
    assert code == 0 and "ADE: post-quantum keys: this machine can seal to them (its cryptography does ML-KEM)" in out
    ade.owner.state(retires="all")                                      # a classical account, encryption on
    monkeypatch.setitem(sys.modules, MLKEM_MODULE, None)
    monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert code == 0 and f"ADE: post-quantum keys: {ade_link.E2E_NO_MLKEM}" in out
    hybrid(ade)                                                         # once it holds one, its hold says it once
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert out.count(ade_link.E2E_NO_MLKEM) == 1


@pytest.mark.parametrize("why", ["no ML-KEM", "a statement not taken"])
def test_pairing_says_what_goes_sealed_stays_until_it_has_a_key_only_where_a_key_would_end_the_hold(
        ade, capsys, tmp_path, monkeypatch, why):
    """A link paired to a post-quantum key on a machine without ML-KEM holds until its cryptography can seal, which a
    key the ADE gives does not change, so pairing does not say "until it has one"; held for want of a key, it does."""
    s, _ = hybrid(ade, device=ade.owner.other if why == "a statement not taken" else None)
    if why == "no ML-KEM":
        monkeypatch.setitem(sys.modules, MLKEM_MODULE, None)
        monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)
    code, _, err = run(capsys, "pair", "--url", ade.url, "--ticket", "PP-AAAAAAAA", "--name", "mac", "--kind",
                       "local", "--dir", tmp_path / "new", "--keys", owner_key(ade.owner.device)["fingerprint"],
                       "--e2e", f"1:{kid_of(s)}")
    assert code == 0, err
    if why == "no ML-KEM":
        assert (f"this link holds a post-quantum key, which this machine cannot seal to: {ade_link.E2E_NO_MLKEM}. What "
                "goes sealed stays on this machine; ade_link.py e2e says more") in err
    else:
        assert "What goes sealed stays on this machine until it has one; ade_link.py e2e says more" in err


def test_a_classical_key_after_a_post_quantum_one_signed_by_an_unpinned_passkey_is_not_said_to_wait_for_a_pin(
        ade, capsys, tmp_path):
    """Pinning the passkey that signed it would not make it taken: it is never taken, and the link says so, not that
    it is taken once that passkey is pinned."""
    d = a_link(ade, tmp_path / "mac")
    hybrid(ade)
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    ade.owner.state(retires="all", device=ade.owner.other)          # a classical key, by a passkey not pinned
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "the ADE's statement 2 names a classical key after a post-quantum one, which this link never takes" in out
    assert "pin --add" not in out and seqs(d) == [1]


@pytest.mark.parametrize("off_between", [False, True])
def test_a_classical_key_stated_after_a_post_quantum_one_is_never_taken(ade, capsys, tmp_path, off_between):
    d = a_link(ade, tmp_path / "mac")
    _, seed = hybrid(ade)
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    if off_between:
        ade.owner.state("e2e-off")                          # an off between does not lift the rule
    ade.owner.state(retires="all")                          # a classical key, signed by the pinned passkey
    hybrid(ade)                                             # and a post-quantum key chained through it
    bad = 3 if off_between else 2
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert f"the ADE's statement {bad} names a classical key after a post-quantum one, which this link never takes" \
        in out
    assert seqs(d) == list(range(1, bad))                   # what came before it, and none past it
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    if off_between:                                         # it may be on: held, never in the clear
        assert code == 2 and ade.posted() == [] and "names a classical key after a post-quantum one" in err
    else:                                                   # sealed to the post-quantum key it holds
        assert code == 0 and page.open_envelope2(seed, ade.events[0]["text"], event_slot(ade.events[0]))["text"] == "x"


def test_once_e2e_json_is_lost_the_anchor_still_keeps_a_classical_key_out(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    _, seed = hybrid(ade)
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1, 2]
    os.unlink(os.path.join(d, "e2e.json"))
    ade.owner.state(retires="all")                          # the server's statement 3: a classical key
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "the ADE's statement 3 names a classical key after a post-quantum one" in out and seqs(d) == [1, 2]
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert page.open_envelope2(seed, ade.events[0]["text"], event_slot(ade.events[0]))["text"] == "x"


def test_once_e2e_json_is_lost_the_anchor_keeps_out_a_chain_rolled_back_to_a_classical_key(ade, capsys, tmp_path):
    """The ADE rolled back to before the post-quantum statement, and a classical key stated in its place (by a page
    that never saw it): the chain it serves holds no post-quantum key at all, so only the anchor, which names the
    statement this link took, keeps the classical key out."""
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()                                       # 1: classical
    hybrid(ade)                                             # 2: post-quantum
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1, 2]
    os.unlink(os.path.join(d, "e2e.json"))
    del ade.owner.statements[1:]
    ade.owner.state(retires="all")                          # 2 again: a classical key, signed by the pinned passkey
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "the ADE's statement 2 is not the one this link took last" in out
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and ade.events == []


def test_a_link_before_this_release_holds_after_the_upgrade(ade, capsys, tmp_path, monkeypatch):
    """A link from an older release reads version 2 alone: it keeps the classical key it took, its
    next envelope is refused once the upgrade retired that key, and it holds from then, never in the clear."""
    d = a_link(ade, tmp_path / "mac")
    first, _ = ade.owner.state(retires="all")
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    monkeypatch.setattr(ade_link, "_versions", lambda purpose: (ade_link.PAYLOAD_VERSION,))     # its reading
    hybrid(ade)                                             # the upgrade: a version-3 key, retiring every earlier one
    ade.require, ade.retired = True, {kid_of(first)}
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    code, _, err = run(capsys, "--dir", d, "post", "step", "y")
    assert code == 2 and len(ade.events) == 1
    assert f"the ADE no longer takes key {kid_of(first)}, the newest this link took, and a statement the ADE gives " \
           "does not read as one" in err


@pytest.mark.parametrize("what, said", [
    ("a classical key after a post-quantum one", "holds a classical key stated after a post-quantum one"),
    ("an off of version 3", "statements do not chain as this client took them")])
def test_an_e2e_json_this_client_never_writes_is_damaged(ade, capsys, tmp_path, what, said):
    d = a_link(ade, tmp_path / "mac")
    hybrid(ade)
    if what == "an off of version 3":
        ade.owner.state("e2e-off", v=3)
    else:
        ade.owner.state(retires="all")
    kept = {"statements": [{"payload": s["payload"], "credential": s["credential"], "signer": None}
                           for s in ade.owner.statements], "deployment": ade.owner.host, "account": ACCOUNT}
    with open(os.path.join(d, "e2e.json"), "w") as fh:     # as no client writes it
        json.dump(kept, fh)
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and ade.posted() == [] and said in err


@pytest.mark.parametrize("n, value, taken", [(0, 3328, True), (0, 3329, False), (767, 3328, True),
                                             (767, 3329, False), (1, 4095, False), (766, 3329, False)])
def test_a_version_3_key_is_taken_only_where_each_coefficient_is_below_q(ade, capsys, tmp_path, n, value, taken):
    public = with_coefficient(page.public_of(os.urandom(32)), n, value)
    assert coefficient(public, n) == value and len(public) == 1216
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state(public=public, v=3, retires="all")
    code, out, _ = run(capsys, "--dir", d, "e2e")
    if taken:
        assert seqs(d) == [1]
    else:
        assert held(d)["statements"] == [] and "does not read as one" in out
    assert ade_link._e2e_hybrid_key_ok(public) is taken
    # the library reads it alike, where it does ML-KEM (FIPS 203's check of an encapsulation key)
    from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM768PublicKey
    if taken:
        MLKEM768PublicKey.from_public_bytes(public[:1184])
    else:
        with pytest.raises(ValueError):
            MLKEM768PublicKey.from_public_bytes(public[:1184])


@pytest.mark.parametrize("x25519, taken", [(bytes(32), False), (bytes([1]) + bytes(31), False), (None, True)])
def test_a_version_3_key_is_taken_only_where_its_x25519_half_agrees_on_a_secret(ade, capsys, tmp_path, x25519, taken):
    """The key check covers both halves: an X25519 half that agrees on nothing with any
    key (a point of low order: zero, one) fails it, whoever signed it, so this link never takes a key it could not seal
    to, and holds rather than fail at each send."""
    public = page.public_of(os.urandom(32))
    if x25519 is not None:
        public = public[:1184] + x25519
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state(public=public, v=3, retires="all")
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert ade_link._e2e_hybrid_key_ok(public) is taken
    if taken:
        assert seqs(d) == [1]
    else:
        assert held(d)["statements"] == [] and "does not read as one" in out
        assert run(capsys, "--dir", d, "post", "step", "x")[0] == 2 and ade.posted() == []


@pytest.mark.parametrize("what", ["version 3, a classical key", "version 2, a post-quantum key",
                                  "version 4, a post-quantum key", "version 3, one byte short",
                                  "version 3, an off"])
def test_a_statement_whose_version_is_not_its_key_s_is_not_taken(ade, capsys, tmp_path, what):
    d = a_link(ade, tmp_path / "mac")
    pq = page.public_of(os.urandom(32))
    if what == "version 3, a classical key":
        ade.owner.state(v=3, retires="all")
    elif what == "version 2, a post-quantum key":
        ade.owner.state(public=pq, retires="all")
    elif what == "version 4, a post-quantum key":
        ade.owner.state(public=pq, v=4, retires="all")
    elif what == "version 3, one byte short":
        ade.owner.state(public=pq[:-1], v=3, retires="all")
    else:
        ade.owner.state("e2e-off", v=3)
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert held(d)["statements"] == [] and "does not read as one" in out
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 2 and ade.posted() == []


def test_a_command_of_version_3_is_not_one(ade, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    link, keys = ade_link.Link(d), {"account": ACCOUNT}
    now = 1791511200
    p = {"v": 3, "deployment": ade.owner.host, "purpose": "instruction", "account": ACCOUNT, "nonce": "n",
         "id": "a" * 32, "to": LINK_ID, "text": "do it", "created_at": now, "expires_at": now + 600}
    text = ade_link.canonical(p).decode("utf-8")
    with pytest.raises(ade_link.NotVerified) as e:
        ade_link._payload(link, keys, text, ("instruction",), now)
    assert e.value.why == "fields"
    assert ade_link._payload(link, keys, ade_link.canonical(dict(p, v=2)).decode("utf-8"), ("instruction",), now)


# ---- a machine without ML-KEM ---------------------------------------------------------------------------------------

def test_the_link_tells_by_trying_whether_it_can_seal_to_a_post_quantum_key(monkeypatch):
    monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)
    assert ade_link._e2e_mlkem() is True and ade_link._e2e_seals() == [1, 2, 3]
    monkeypatch.setitem(sys.modules, MLKEM_MODULE, None)
    assert ade_link._e2e_mlkem() is True                    # asked once, then remembered
    monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)
    assert ade_link._e2e_mlkem() is False and ade_link._e2e_seals() == [1]
    with pytest.raises(ade_link.E2ENoMLKEM) as e:
        ade_link._e2e_envelope(page.public_of(os.urandom(32)), "slot", b"{}")
    assert str(e.value) == ade_link.E2E_NO_MLKEM and isinstance(e.value, ValueError)
    # a module that imports, and encapsulates to sizes ML-KEM-768 never gives, is no ML-KEM either
    wrong = types.ModuleType(MLKEM_MODULE)
    wrong.MLKEM768PrivateKey = types.SimpleNamespace(generate=lambda: types.SimpleNamespace(
        public_key=lambda: types.SimpleNamespace(encapsulate=lambda: (bytes(32), bytes(1087)))))
    monkeypatch.setitem(sys.modules, MLKEM_MODULE, wrong)
    monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)
    assert ade_link._e2e_mlkem() is False
    # nor one that imports on an OpenSSL without the algorithm, as pyca's module does there
    from cryptography.exceptions import UnsupportedAlgorithm

    def unsupported():
        raise UnsupportedAlgorithm("ML-KEM is not supported by this backend.")
    missing = types.ModuleType(MLKEM_MODULE)
    missing.MLKEM768PrivateKey = types.SimpleNamespace(generate=unsupported)
    monkeypatch.setitem(sys.modules, MLKEM_MODULE, missing)
    monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)
    assert ade_link._e2e_mlkem() is False and ade_link._e2e_seals() == [1]


def test_without_ml_kem_a_post_quantum_key_holds_every_sealed_path_and_a_classical_one_seals(ade, capsys, tmp_path,
                                                                                            no_mlkem):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state(retires="all")                 # a classical account: form 1, as before
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert open_envelope(ade.events[0]["text"], event_slot(ade.events[0]), key)["text"] == "x"
    s, _ = hybrid(ade)
    assert run(capsys, "--dir", d, "e2e")[0] == 0           # read now, not at the minute's end
    code, _, err = run(capsys, "--dir", d, "post", "step", "y")
    assert code == 2 and len(ade.events) == 1
    assert ("encryption may be on for this account, and this link holds a post-quantum key, which this machine "
            f"cannot seal to: {ade_link.E2E_NO_MLKEM}. The words stay on this machine") in err
    assert "install a cryptography wheel of 48.0.0 or later from PyPI" in ade_link.E2E_NO_MLKEM
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert ("ADE: encryption: this link holds a post-quantum key, which this machine cannot seal to, so what goes "
            f"sealed stays on this machine: {ade_link.E2E_NO_MLKEM}") in out
    assert f"ADE: key {kid_of(s)}, the newest this link took, is {POST_QUANTUM}" in out
    assert run(capsys, "--dir", d, "post", "--to", "all", "step", "z")[0] == 0       # to all: never sealed, as before
    assert [e["text"] for e in ade.events] == [ade.events[0]["text"], "z"]


def test_without_ml_kem_a_history_run_on_a_post_quantum_account_sends_no_list(ade, capsys, tmp_path, home, no_mlkem):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    hybrid(ade)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 2 and ade.histories == [] and ade.talks == []
    assert f"this link holds a post-quantum key, which this machine cannot seal to: {ade_link.E2E_NO_MLKEM}" in out


# ---- the History list -----------------------------------------------------------------------------------------------

def test_the_history_list_says_which_forms_the_link_can_seal(ade, capsys, tmp_path, home, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all"})
    assert run(capsys, "--dir", d, "history")[0] == 0 and ade.histories[0]["e2e_seal"] == [1, 2, 3]
    monkeypatch.setitem(sys.modules, MLKEM_MODULE, None)
    monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)
    assert run(capsys, "--dir", d, "history")[0] == 0 and ade.histories[1]["e2e_seal"] == [1]


def seals_said(ade):
    """The forms each read of the statements said this link can seal, the first page's alone (`e2e_seal`)."""
    return [q.get("e2e_seal") for m, p, q in ade.queries if m == "GET" and p == "/api/ade/link/e2e"]


def test_every_read_of_the_statements_says_which_forms_the_link_can_seal(ade, capsys, tmp_path, monkeypatch):
    """Not with the History list alone: a link that sends none (no history.json here) still says what it can seal each
    time it reads the statements, at `e2e`, before a post to the owner, so the page knows it for every updated link."""
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state(retires="all")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert ade.histories == [] and seals_said(ade) and set(seals_said(ade)) == {"1,2,3"}


def test_a_link_that_holds_for_want_of_ml_kem_says_it_can_seal_form_1_alone(ade, capsys, tmp_path, home, no_mlkem):
    """The link the page most needs to name: on a post-quantum account it holds every sealed path, its History list
    too, yet each read of the statements says it can seal form 1 alone."""
    d = a_link(ade, tmp_path / "mac")
    hybrid(ade)
    sessions(home)
    rules(d, {"projects": "all"})
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    run(capsys, "--dir", d, "history")
    assert run(capsys, "--dir", d, "post", "step", "y")[0] == 2
    assert ade.histories == [] and ade.events == []
    assert seals_said(ade) and set(seals_said(ade)) == {"1"}


def test_rows_go_in_form_2_and_a_conversation_whole_under_a_key_of_its_own(ade, capsys, tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    s, seed = hybrid(ade)
    ade.require = True
    code, out, err = run(capsys, "--dir", d, "history")
    assert code == 0, out + err
    (body,) = ade.histories
    rows = {r["id"]: r for r in body["sessions"]}
    assert all(page.body_of(r["sealed"])[0] == 2 and page.body_of(r["sealed"])[1:17].hex() == kid_of(s)
               for r in rows.values())
    assert page.open_envelope2(seed, rows["s-new"]["sealed"], slot(ACCOUNT, LINK_ID, "history", "s-new", 0)) == {
        "title": "the History list", "project": "~/work/app", "branch": "main", "repositories": []}
    assert page.open_envelope2(seed, rows["s-touched"]["sealed"], slot(ACCOUNT, LINK_ID, "history", "s-touched", 1)) \
        == {"project": "~/work/app"}
    (talk,) = ade.talks
    count = len(talk["messages"])
    assert len(page.body_of(talk["key"])) == ade_link.E2E_TALK_KEY_SEALED == 1197
    tk = page.open_talk_key(seed, talk["key"], slot(ACCOUNT, LINK_ID, "talk-key", "s-new", count, talk["earlier"]))
    assert page.talk_key_id(tk) == talk["key_id"]
    assert all(page.body_of(m["text"])[0] == 3 and page.body_of(m["text"])[1:17].hex() == talk["key_id"]
               for m in talk["messages"])
    assert [page.open_talk_message(tk, m["text"], talk_slot(talk, i, m)) for i, m in enumerate(talk["messages"])] == [
        {"text": "how is the build?"}, {"text": "Checking.\n\nGreen: 12 passed."}]
    with pytest.raises(page.NotOpened):                     # the key is bound to its list: its count and earlier
        page.open_talk_key(seed, talk["key"], slot(ACCOUNT, LINK_ID, "talk-key", "s-new", count + 1, talk["earlier"]))
    with pytest.raises(page.NotOpened):                     # and a message to its place in it
        page.open_talk_message(tk, talk["messages"][0]["text"], talk_slot(talk, 1, talk["messages"][0]))


def test_a_history_run_cuts_a_conversation_by_its_key_and_its_form_3_messages(ade, capsys, tmp_path, home,
                                                                              monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    hybrid(ade)
    last = ade_link.E2E_TALK_OVERHEAD + len(ade_link._e2e_plain({"text": "Checking.\n\nGreen: 12 passed."}))
    # room for the key and the newest message alone: in form 1, with no key, both would go
    monkeypatch.setattr(ade_link, "E2E_TALK_TOTAL", ade_link.E2E_TALK_KEY_SEALED + last)
    assert run(capsys, "--dir", d, "history")[0] == 0
    (talk,) = ade.talks
    assert len(talk["messages"]) == 1 and talk["earlier"] == 1


def test_a_conversation_whose_key_is_refused_goes_again_under_the_newest_key(ade, capsys, tmp_path, home):
    """The owner states a new key between a list and its conversations: the conversation's key, sealed to the content
    key just retired, is refused (409, as the server refuses it), and the next run sends it again under the newest key,
    never in the clear."""
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    first, _ = hybrid(ade)
    ade.require = True
    newest = {}

    def rotated():
        s, newest["seed"] = hybrid(ade)
        ade.retired = {kid_of(first)}
    ade.after_history = rotated
    assert run(capsys, "--dir", d, "history")[0] == 0
    talks = [p for m, p, _ in ade.requests if m == "PUT" and p == "/api/ade/link/history/talk"]
    assert len(talks) == 1 and ade.talks == []                      # the one sent, refused for its key
    assert run(capsys, "--dir", d, "history")[0] == 0
    (talk,) = ade.talks
    tk = page.open_talk_key(newest["seed"], talk["key"], slot(ACCOUNT, LINK_ID, "talk-key", "s-new",
                                                              len(talk["messages"]), talk["earlier"]))
    assert page.talk_key_id(tk) == talk["key_id"]


def test_a_classical_conversation_is_cut_by_its_form_1_messages_alone(ade, capsys, tmp_path, home, monkeypatch):
    """On a classical account each message is form 1, 77 bytes beside its plaintext, and no key is counted: with 40
    messages, counted as form 3 under a key the cut would keep 40 x 32 - 1,197 bytes more than the server takes."""
    d = a_link(ade, tmp_path / "mac")
    work = home / "work" / "app"
    work.mkdir(parents=True)
    path = home / ".claude" / "projects" / "-app" / "s-long.jsonl"
    entries = []
    for i in range(20):
        entries += [_typed(f"question {i:02d}", f"2026-10-03T10:{i:02d}:00Z", str(work)),
                    _said(f"answer {i:02d}", f"2026-10-03T10:{i:02d}:30Z")]
    _write(path, entries)
    rules(d, {"projects": "all", "conversations": True})
    st = os.stat(path)
    whole = ade_link._talk(str(path), st.st_size, st.st_ino, sealed=True, hybrid=False)["messages"]
    assert len(whole) == 40
    total = sum(ade_link.E2E_OVERHEAD + len(ade_link._e2e_plain({"text": m["text"]})) for m in whole)
    monkeypatch.setattr(ade_link, "E2E_TALK_TOTAL", total - 1)     # the oldest message must go
    ade.owner.state(retires="all")
    ade.require = True
    assert run(capsys, "--dir", d, "history")[0] == 0
    (talk,) = ade.talks
    assert sum(len(page.body_of(m["text"])) for m in talk["messages"]) <= total - 1
    assert len(talk["messages"]) == 39 and talk["earlier"] == 1 and talk.get("key") is None


def test_the_link_counts_a_conversation_as_the_server_does(ade, tmp_path):
    """Its sizes are the server's (ade/e2e.py, ade/link.py), and each form-3 message it seals reads there as long as
    the link counted it: a cut the link makes at its total is one the server takes."""
    from proofpane_open.ade import e2e as server_e2e, link as server_link
    assert ade_link.E2E_TALK_OVERHEAD == server_e2e.OVERHEADS[server_e2e.FORM_TALK] == 45
    assert ade_link.E2E_OVERHEAD == server_e2e.OVERHEADS[server_e2e.FORM_X25519] == 77
    assert ade_link.E2E_TALK_KEY_SEALED == server_e2e.TALK_KEY_SEALED == 1197
    assert ade_link.E2E_TALK_TOTAL == server_link.TALK_SEALED_TOTAL
    d = a_link(ade, tmp_path / "mac")
    link, seed = ade_link.Link(d), os.urandom(32)
    texts = ["a", "the plan " + chr(0x1F600), "x" * 500]
    talk = {"messages": [{"who": "owner", "text": t, "at": 1790000000 + i} for i, t in enumerate(texts)],
            "earlier": 0}
    sent = ade_link._e2e_talk(link, "s1", talk, ("seal", page.public_of(seed), "k" * 32, ACCOUNT))
    assert server_e2e.read_talk_key(sent["key"]).length == ade_link.E2E_TALK_KEY_SEALED
    for m, t in zip(sent["messages"], texts):
        got = server_e2e.read(m["text"], server_e2e.TALK_PLAIN, (server_e2e.FORM_TALK,))
        assert got.length == ade_link.E2E_TALK_OVERHEAD + len(ade_link._e2e_plain({"text": t}))


def test_each_list_of_a_conversation_goes_under_a_fresh_key(ade, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    link, seed = ade_link.Link(d), os.urandom(32)
    mode = ("seal", page.public_of(seed), "k" * 32, ACCOUNT)
    talk = {"messages": [{"who": "owner", "text": "a", "at": 1790000000}], "earlier": 0}
    one, two = (ade_link._e2e_talk(link, "s1", talk, mode) for _ in range(2))
    assert one["key_id"] != two["key_id"] and one["key"] != two["key"]
    for sent in (one, two):
        tk = page.open_talk_key(seed, sent["key"], slot(ACCOUNT, LINK_ID, "talk-key", "s1", 1, 0))
        assert page.talk_key_id(tk) == sent["key_id"] and sent["session"] == "s1" and sent["earlier"] == 0
        m = sent["messages"][0]
        assert {k: v for k, v in m.items() if k != "text"} == {"who": "owner", "at": 1790000000}
        assert page.open_talk_message(tk, m["text"], slot(ACCOUNT, LINK_ID, "talk", "s1", 0, 1, 0, "owner",
                                                          1790000000, None)) == {"text": "a"}


def test_a_conversation_to_a_post_quantum_key_is_cut_counting_its_key_and_its_form_3_messages(home, monkeypatch):
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    w = str(home)
    _write(path, [_typed("one", "2026-10-03T10:00:00Z", w), _said("x" * 3000, "2026-10-03T10:01:00Z"),
                  _typed("two", "2026-10-03T10:02:00Z", w), _said("three", "2026-10-03T10:03:00Z")])
    st = os.stat(path)

    def kept(total, hybrid):
        monkeypatch.setattr(ade_link, "E2E_TALK_TOTAL", total)
        got = ade_link._talk(str(path), st.st_size, st.st_ino, sealed=True, hybrid=hybrid)
        return [m["text"] for m in got["messages"]], got["earlier"]

    form3 = {t: ade_link.E2E_TALK_OVERHEAD + len(ade_link._e2e_plain({"text": t})) for t in ("two", "three")}
    two = ade_link.E2E_TALK_KEY_SEALED + form3["three"] + form3["two"]
    assert kept(two, True) == (["two", "three"], 2)                     # the key and two messages: the total
    assert kept(two - 1, True) == (["three"], 3)
    assert kept(ade_link.E2E_TALK_KEY_SEALED + form3["three"] - 1, True) == ([], 4)     # the key counts
    assert kept(two - 1, False) == (["two", "three"], 2)                # form 1: no key, 77 bytes a message


# ---- the link's own vectors, for the page's opener ------------------------------------------------------------------

LINK_VECTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors", "e2e_v2_link.json")


def link_vectors():
    """What the link seals to a post-quantum key as it posts, made by its own functions (_e2e_event, _e2e_row,
    _e2e_talk) at random (pyca has no derandomized encapsulation) to a content key made from a fixed seed: the shape of
    the server's own vectors (e2e_v2.json, tests/test_ade_e2e_envelope_pq.py) but the recovery proof, which a link never
    makes, so that the page's opener opens them as it opens those (where once the vectors the page opened
    were the server's alone). Each text says where it goes (`where`), from which its slot is made."""
    account, link_id = "acct-e2e-link", "fedcba9876543210fedcba9876543210"
    seed = hashlib.sha256(b"proofpane-e2e/2 link vectors: content key").digest()
    pk = page.public_of(seed)
    key_id = hashlib.sha256(pk).digest()[:16].hex()
    mode, link = ("seal", pk, key_id, account), types.SimpleNamespace(link_id=link_id)
    events = [
        ("event: a step to the owner", {"kind": "step", "text": "Sealed by the link.", "client_id": "0" * 28 + "f101"}),
        ("event: a mirrored note, in other scripts and with what JSON escapes",
         {"kind": "note", "mirror": "session", "session": "s-0009", "said_at": 1790000000321,
          "text": "caf" + chr(0xE9) + " " + chr(0x3BB) + " " + chr(0x1F600) + ' "q" \\ tab\there\nnext' + chr(1)
                  + chr(0x7F) + " end", "client_id": "0" * 28 + "f102"}),
        ("event: a reply to the command it took", {"kind": "done", "ref": 41, "text": "done: 3 passed",
                                                   "client_id": "0" * 28 + "f103"}),
    ]
    envelopes = []
    for name, body in events:
        value = {"text": body["text"], "session": body.get("session"), "said_at": body.get("said_at")}
        where = {"path": "event", "kind": body["kind"], "mirror": body.get("mirror"), "ref": body.get("ref"),
                 "client_id": body["client_id"]}
        envelopes.append({"name": name, "where": where, "slot": ade_link.e2e_slot(
            account, link_id, "event", where["kind"], where["mirror"], where["ref"], where["client_id"]),
            "plaintext": value, "plaintext_text": ade_link._e2e_plain(value).decode("utf-8"),
            "envelope": ade_link._e2e_event(link, body, mode)["text"]})
    rows = [
        ("history: a listed row", {"id": "s-0009", "withheld": False, "title": "Sealed by the link",
                                   "project": "~/work/link", "branch": "main", "repositories": ["~/work/link"]}),
        ("history: a withheld row, its project alone", {"id": "s-0010", "withheld": True, "title": "",
                                                        "project": "~/private", "branch": None, "repositories": []}),
    ]
    for name, row in rows:
        value = {"project": row["project"]} if row["withheld"] else {
            "title": row["title"], "project": row["project"], "branch": row["branch"],
            "repositories": row["repositories"]}
        where = {"path": "history", "session": row["id"], "withheld": row["withheld"]}
        envelopes.append({"name": name, "where": where, "slot": ade_link.e2e_slot(
            account, link_id, "history", row["id"], 1 if row["withheld"] else 0), "plaintext": value,
            "plaintext_text": ade_link._e2e_plain(value).decode("utf-8"),
            "envelope": ade_link._e2e_row(link, row, mode)["sealed"]})
    session, earlier = "s-0009", 4
    said = [{"who": "owner", "text": "and the tests?", "at": 1790000003, "ms": 1790000002500},
            {"who": "session", "text": "All 58 pass.", "at": None, "ms": None}]
    sent = ade_link._e2e_talk(link, session, {"earlier": earlier, "messages": said}, mode)
    count = len(said)
    talk = {"session": session, "count": count, "earlier": earlier, "key_id": sent["key_id"],
            "key_slot": ade_link.e2e_slot(account, link_id, "talk-key", session, count, earlier),
            "key_envelope": sent["key"],
            "messages": [{"who": m["who"], "at": m["at"], "ms": m["ms"],
                          "slot": ade_link.e2e_slot(account, link_id, "talk", session, i, count, earlier, m["who"],
                                                    m["at"], m["ms"]),
                          "plaintext": {"text": m["text"]},
                          "plaintext_text": ade_link._e2e_plain({"text": m["text"]}).decode("utf-8"),
                          "envelope": out["text"]} for i, (m, out) in enumerate(zip(said, sent["messages"]))]}
    return {
        "about": "End-to-end encryption, post-quantum: what the link client seals as it posts to a "
                 "post-quantum account, made at random by its own sealing functions (ade_link.py's _e2e_event, "
                 "_e2e_row and _e2e_talk, with pyca's ML-KEM-768 and X25519 and hashlib's SHA3-256; pyca has no "
                 "derandomized encapsulation), for the page's opener (ui/src/e2eCrypto.ts) to open as it opens the "
                 "server's (e2e_v2.json). Written again whenever the link's sealing changes, from open_edition: "
                 "PP_E2E_VECTORS_WRITE=1 python -m pytest tests/test_ade_link_client_e2e_pq.py -k "
                 "own_post_quantum_vectors, which also checks this file and a set made at once. Bytes are base64url "
                 "without padding; the content key is made from its seed as X-Wing makes keys. Envelopes are form 2, "
                 "each bound to its slot, made from where it goes (where); the conversation's key is a form-2 envelope "
                 "of a key the link drew for this list, bound to key_slot and named by key_id, its messages form 3.",
        "version": 2, "account": account, "link_id": link_id,
        "content_key": {"seed": ade_link.b64u(seed), "public": ade_link.b64u(pk), "key_id": key_id},
        "envelopes": envelopes, "talk": talk,
    }


def _check_link_vectors(v):
    """Each text opens on the page's side (e2e_pq_page_side, written from the design) at its slot, which is the slot
    the server makes for where it goes (ade/e2e.py), and is in the form the server reads on its path."""
    from proofpane_open.ade import e2e as server
    account, link_id = v["account"], v["link_id"]
    seed, pk = page.unb64u(v["content_key"]["seed"]), page.unb64u(v["content_key"]["public"])
    assert page.public_of(seed) == pk and server.key_id(pk) == v["content_key"]["key_id"]
    assert [e["where"]["path"] for e in v["envelopes"]] == ["event", "event", "event", "history", "history"]
    for e in v["envelopes"]:
        w = e["where"]
        if w["path"] == "event":
            assert e["slot"] == server.event_slot(account, link_id, w["kind"], w["mirror"], w["ref"], w["client_id"])
            most = server.EVENT_PLAIN
        else:
            assert e["slot"] == server.history_slot(account, link_id, w["session"], w["withheld"])
            most = server.HISTORY_WITHHELD_PLAIN if w["withheld"] else server.HISTORY_PLAIN
        assert server.read(e["envelope"], most, (server.FORM_HYBRID,)).key_id == v["content_key"]["key_id"], e["name"]
        assert page.open_bytes2(seed, e["envelope"], e["slot"]) == e["plaintext_text"].encode("utf-8"), e["name"]
        assert server.plaintext(e["plaintext"]) == e["plaintext_text"].encode("utf-8"), e["name"]
    t = v["talk"]
    assert t["key_slot"] == server.talk_key_slot(account, link_id, t["session"], t["count"], t["earlier"])
    assert server.read_talk_key(t["key_envelope"]).key_id == v["content_key"]["key_id"]
    tk = page.open_talk_key(seed, t["key_envelope"], t["key_slot"])
    assert page.talk_key_id(tk) == t["key_id"]
    assert len(t["messages"]) == t["count"] > 0
    for i, m in enumerate(t["messages"]):
        assert m["slot"] == server.talk_slot(account, link_id, t["session"], i, t["count"], t["earlier"], m["who"],
                                             m["at"], m["ms"])
        assert server.read(m["envelope"], server.TALK_PLAIN, (server.FORM_TALK,)).key_id == t["key_id"]
        assert page.open_talk_message(tk, m["envelope"], m["slot"]) == m["plaintext"]
        assert server.plaintext(m["plaintext"]) == m["plaintext_text"].encode("utf-8")


def test_the_link_s_own_post_quantum_vectors_open_on_the_page_s_side():
    """The committed file, which the page's opener is to open as it opens the server's, opens here; and a set the link
    makes now opens the same way, so a change to the link's sealing that the page could not open fails here."""
    if os.environ.get("PP_E2E_VECTORS_WRITE"):
        with open(LINK_VECTORS, "w", encoding="utf-8") as fh:
            json.dump(link_vectors(), fh, indent=1, ensure_ascii=True)
            fh.write("\n")
    assert os.path.exists(LINK_VECTORS), "tests/vectors/e2e_v2_link.json is missing: the page's opener reads it"
    with open(LINK_VECTORS, encoding="utf-8") as fh:
        _check_link_vectors(json.load(fh))
    _check_link_vectors(link_vectors())


# ---- the second screen ----------------------------------------------------------------------------------------------

def test_the_second_screen_reads_a_version_3_statement_and_says_its_key_is_post_quantum():
    import test_ade_link_sign_core as core
    pq = page.public_of(os.urandom(32))
    p = core.payload("e2e-key", v=3, key=b64u(pq), key_id=hashlib.sha256(pq).hexdigest()[:32])
    got, _ = core.read(p)
    assert got["v"] == 3
    rows = core.screen("e2e-key", v=3, key=b64u(pq), key_id=hashlib.sha256(pq).hexdigest()[:32])
    assert f"  Key          {POST_QUANTUM}   (signed)" in rows
    assert "  Key          classical (X25519)   (signed)" in core.screen("e2e-key")
    assert core.refusal(core.payload("e2e-key", v=3)) == "It is not a statement of the encryption keys in its form."
    assert core.refusal(core.payload("e2e-key", v=4, key=b64u(pq),
                                     key_id=hashlib.sha256(pq).hexdigest()[:32])) == "Its v is not 2 or 3."
    assert core.refusal(core.payload("e2e-off", v=3)) == "Its v is not 2."
    assert core.refusal(core.payload("instruction", v=3)) == "Its v is not 2."
