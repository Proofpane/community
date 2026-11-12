"""The signing window, the server's side (proofpane_open/ade/link_commands.py and the routes): a delegation the owner's
passkey signs opens a signing window for one link for 300 or 900 seconds; an instruction their page signs under it with
the delegated key is stored and served for the session to verify itself; each early end ends the window and withdraws
what no session has taken."""

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

from conftest import signed_up
from fido_fixtures import add_hardware
from fido_fixtures import maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import auth, passkeys, store
from proofpane_open.ade import link, link_commands      # puts the open packages on sys.path first
from proofpane_core.canonical import canonicalize
from test_ade_link import HOST, OTHER, Session, at_once, paired, raw, revoke, ticket
from test_ade_link_commands import DROP, account, assertion, device, event_row, nonce, owner, payload  # noqa: F401
from test_passkeys import add, remove, sign_out_everywhere

DIGEST_TAG = b"proofpane-delegation/1"
SIGNED_TAG = b"proofpane-delegated/1"
DELEGATIONS = "/api/ade/link/delegations"
COMMANDS = "/api/ade/link/commands"


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def page_key():
    key = ec.generate_private_key(ec.SECP256R1())
    return key, key.public_key().public_bytes(serialization.Encoding.DER,
                                              serialization.PublicFormat.SubjectPublicKeyInfo)


def page_signed(key, p):
    r, s = decode_dss_signature(key.sign(SIGNED_TAG + b"\x00" + canonicalize(p), ec.ECDSA(hashes.SHA256())))
    return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def fingerprint(session):
    return hashlib.sha256(raw(session.key)).hexdigest()[:32]


def digest(d):
    return hashlib.sha256(DIGEST_TAG + b"\x00" + canonicalize(d)).hexdigest()


def declare(session, features="delegation-1"):
    """The session's read of its state, saying what its client takes (or nothing)."""
    r = session.call("GET", "/state", query={"features": features} if features else None)
    assert r.status_code == 200, r.text
    return r.json()


def delegation(client, owner, session, life=300, spki=None, **over):
    key, made = page_key()
    now = int(time.time())
    d = {"v": 2, "deployment": HOST, "purpose": "delegation", "account": account(client, owner),
         "nonce": nonce(client, owner, "delegation"), "id": uuid.uuid4().hex,
         "delegate": {"kind": "page-key", "alg": -7, "key": b64u(spki or made)},
         "sessions": [{"id": session.link_id, "fingerprint": fingerprint(session), "kinds": ["instruction"]}],
         "created_at": now, "expires_at": now + life}
    for k, v in over.items():
        if v is DROP:
            d.pop(k, None)
        else:
            d[k] = v
    return d, key


def open_window(client, owner, device, d, signed=None):
    return client.post(DELEGATIONS, headers=owner, json={"payload": d, "credential": assertion(device, signed or d)})


def opened(client, owner, device, session, life=300):
    declare(session)
    d, key = delegation(client, owner, session, life)
    r = open_window(client, owner, device, d)
    assert r.status_code == 200, r.text
    assert r.json()["digest"] == digest(d)
    return d, key


def windowed(client, owner, session, d, n=1, text="run the tests", **over):
    p = payload(client, owner, "instruction", session.link_id, text, expires_at=d["expires_at"])
    p.update({"delegation": digest(d), "n": n})
    for k, v in over.items():
        p[k] = v
    return p


def send(client, owner, key, d, p, sig=None):
    return client.post(COMMANDS, headers=owner,
                       json={"payload": p, "delegated": {"digest": digest(d), "sig": sig or page_signed(key, p)}})


def sent(client, owner, key, d, p):
    r = send(client, owner, key, d, p)
    assert r.status_code == 200, r.text
    return r.json()["seq"]


def window_row(d):
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_link_delegations WHERE digest = ?", (digest(d),)).fetchone()
        return dict(row) if row else None


def withdrawn(seq):
    return event_row(seq)["withdrawn_at"] is not None


# ---- opening ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("life,status", [(300, 200), (900, 200), (299, 422), (301, 422), (600, 422), (901, 422)])
def test_a_window_opens_for_5_or_15_minutes_and_no_other_length(client, owner, device, life, status):
    mac = paired(client, owner)
    declare(mac)
    d, _ = delegation(client, owner, mac, life)
    r = open_window(client, owner, device, d)
    assert r.status_code == status, r.text
    if status == 200:
        kept = window_row(d)
        assert (kept["link_id"], kept["expires_at"] - kept["created_at"], kept["ended_at"], kept["last_n"]) == (
            mac.link_id, life, None, 0)
        assert r.json() == {"digest": digest(d), "expires_at": d["expires_at"]}
    else:
        assert window_row(d) is None


def test_a_window_opens_only_for_a_link_whose_client_says_it_takes_one(client, owner, device):
    mac = paired(client, owner)
    d, _ = delegation(client, owner, mac)
    assert open_window(client, owner, device, d).status_code == 409          # never said
    declare(mac)
    declare(mac, features=None)                                              # an old client, or a second screen
    d, _ = delegation(client, owner, mac)
    r = open_window(client, owner, device, d)
    assert r.status_code == 409 and window_row(d) is None, r.text
    declare(mac)
    d, _ = delegation(client, owner, mac)
    assert open_window(client, owner, device, d).status_code == 200


def _ed25519_spki():
    return Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


@pytest.mark.parametrize("change,status", [
    (lambda d, s: d["sessions"][0].update(fingerprint="0" * 32), 409),
    (lambda d, s: d["sessions"][0].update(id="0" * 32), 404),
    (lambda d, s: d["sessions"][0].update(kinds=["instruction", "answer"]), 422),
    (lambda d, s: d["sessions"].append(dict(d["sessions"][0])), 422),
    (lambda d, s: d["delegate"].update(key=b64u(_ed25519_spki())), 422),
    (lambda d, s: d["delegate"].update(kind="link"), 422),
    (lambda d, s: d.update(extra="x"), 422),
], ids=["another key", "no such link", "another kind", "two sessions", "not P-256", "not a page key", "a field more"])
def test_a_window_is_for_one_live_link_s_key_and_instructions_alone(client, owner, device, change, status):
    mac = paired(client, owner)
    declare(mac)
    d, _ = delegation(client, owner, mac)
    change(d, mac)
    r = open_window(client, owner, device, d)
    assert r.status_code == status, r.text
    assert window_row(d) is None


def test_a_revoked_link_or_another_account_s_opens_no_window(client, owner, device):
    mac = paired(client, owner)
    declare(mac)
    assert revoke(client, owner, mac.link_id).status_code == 200
    d, _ = delegation(client, owner, mac)
    assert open_window(client, owner, device, d).status_code == 404
    other = signed_up(client, OTHER)
    theirs = paired(client, other, name="theirs")
    declare(theirs)
    d, _ = delegation(client, owner, theirs)
    assert open_window(client, owner, device, d).status_code == 404 and window_row(d) is None


def test_a_window_takes_the_passkey_s_word_over_its_every_byte(client, owner, device):
    mac = paired(client, owner)
    declare(mac)
    d, _ = delegation(client, owner, mac)
    assert open_window(client, owner, device, d, signed=dict(d, expires_at=d["expires_at"] + 600)).status_code != 200
    assert client.post(DELEGATIONS, headers=owner, json={"payload": d}).status_code == 422    # no assertion at all
    assert window_row(d) is None


def test_high_security_on_opens_no_window(client, owner, device):
    mac = paired(client, owner)
    declare(mac)
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (account(client, owner),))
    d, _ = delegation(client, owner, mac)
    r = open_window(client, owner, device, d)
    assert r.status_code == 403 and window_row(d) is None, r.text


def test_high_security_on_opens_no_window_for_a_hardware_key_either(client, owner, metadata, maker):
    """A hardware key passes every check a command's signature meets while high security is on, so the window's own
    refusal is what stops its opening; the device passkey of the case above is stopped as well by the last of those
    checks, the hardware keys' alone, with the same 403 (were that the only case to open a window under high security, a
    version with the window's refusal removed would pass)."""
    key, r = add_hardware(client, owner, maker)
    assert r.json()["tier"] == "hardware", r.text
    mac = paired(client, owner)
    declare(mac)
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (account(client, owner),))
    d, _ = delegation(client, owner, mac)
    r = open_window(client, owner, key, d)
    assert r.status_code == 403 and "no window opens" in r.json()["detail"] and window_row(d) is None, r.text


# ---- an instruction under a window ---------------------------------------------------------------------------------

def test_a_windowed_instruction_is_stored_and_served_for_the_session_to_verify(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    p = windowed(client, owner, mac, d)
    seq = sent(client, owner, key, d, p)
    row = event_row(seq)
    assert (row["kind"], row["to_id"], row["delegation"], row["payload"]) == (
        "instruction", mac.link_id, digest(d), canonicalize(p).decode())
    assert row["passkey_id"] == window_row(d)["passkey_id"] and row["passkey_id"]
    got = mac.call("GET", f"/events/{seq}").json()
    assert set(got["signature"]) == {"delegation", "sig"}
    assert got["signature"]["delegation"]["payload"] == canonicalize(d).decode()
    assert got["signature"]["delegation"]["credential"]["id"] == b64u(device.credential_id)
    assert window_row(d)["last_n"] == 1
    state = declare(mac)
    assert state["delegations_open"] == {digest(d): [[1, seq]]} and state["high_security"] is False


@pytest.mark.parametrize("how,status", [
    ("n not the next", 409), ("another key", 422), ("made before the window", 422), ("its own end", 422),
    ("a minute left no longer", 422), ("another link", 409), ("an unknown window", 404),
    ("another window named", 422)])
def test_a_windowed_instruction_is_refused_unless_it_is_the_window_s_next(client, owner, device, how, status):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    d, key = opened(client, owner, device, mac)
    p = windowed(client, owner, mac, d)
    sig = None
    if how == "n not the next":
        p["n"] = 2
    elif how == "another key":
        sig = page_signed(page_key()[0], p)
    elif how == "made before the window":
        p["created_at"] = d["created_at"] - 1
    elif how == "its own end":
        p["expires_at"] = d["expires_at"] - 1
    elif how == "a minute left no longer":
        with store.connect() as db:                 # the window's end brought to 59 seconds after this instruction
            db.execute("UPDATE ade_link_delegations SET expires_at = ? WHERE digest = ?",
                       (p["created_at"] + 59, digest(d)))
        p["expires_at"] = p["created_at"] + 59
    elif how == "another link":
        p["to"] = cloud.link_id
    elif how == "another window named":
        # the instruction names a window it is not sent under, signed by the page key over that name (without this case,
        # a version that stores such an instruction would pass)
        p["delegation"] = "1" * 64
    r = client.post(COMMANDS, headers=owner, json={"payload": p, "delegated": {
        "digest": "0" * 64 if how == "an unknown window" else digest(d), "sig": sig or page_signed(key, p)}})
    assert r.status_code == status, r.text
    if how == "n not the next":
        assert r.json()["why"] == "delegation_n" and r.json()["last_n"] == 0
    assert window_row(d)["last_n"] == 0


def test_a_window_that_ended_or_expired_stores_nothing_more(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    with store.connect() as db:
        db.execute("UPDATE ade_link_delegations SET expires_at = ? WHERE digest = ?", (int(time.time()) - 1, digest(d)))
    r = send(client, owner, key, d, windowed(client, owner, mac, d))
    assert r.status_code == 410 and r.json()["why"] == "delegation_ended", r.text
    d, key = opened(client, owner, device, mac)
    assert client.post(f"{DELEGATIONS}/{digest(d)}/end", headers=owner).status_code == 200
    r = send(client, owner, key, d, windowed(client, owner, mac, d))
    assert r.status_code == 410 and r.json()["why"] == "delegation_ended", r.text


def test_high_security_turned_on_refuses_what_a_window_would_store(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    with store.connect() as db:                     # as a revocation of the encryption keys turns it on: no end called
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (account(client, owner),))
    r = send(client, owner, key, d, windowed(client, owner, mac, d))
    assert r.status_code == 403, r.text
    assert declare(mac)["high_security"] is True


def test_a_proposal_is_signed_with_the_passkey_even_while_a_window_is_open(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    pid, now = uuid.uuid4().hex, int(time.time())
    with store.connect() as db:                      # an open proposal of the session's, as the server stores one
        db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, needs_turn, "
                   "received_at, deadline_at, for_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (account(client, owner), mac.link_id, pid, "proposal", "owner", "run the tests", 0, now,
                    now + 86400, mac.link_id))
    p = windowed(client, owner, mac, d, id=pid, text="Proposed by mac:\nrun the tests")
    r = send(client, owner, key, d, p)
    assert r.status_code == 409 and "passkey" in r.json()["detail"], r.text


def test_the_owner_s_hourly_budget_counts_windowed_instructions(client, owner, device, monkeypatch):
    monkeypatch.setattr(link_commands, "OWNER_PER_HOUR", 2)
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)       # the opening counts as one
    sent(client, owner, key, d, windowed(client, owner, mac, d, n=1))
    r = send(client, owner, key, d, windowed(client, owner, mac, d, n=2))
    assert r.status_code == 429, r.text


def test_a_window_opened_in_the_hour_counts_against_a_command_the_passkey_signs(client, owner, device, monkeypatch):
    """The opening is one of the owner's commands in the hour for a command the passkey signs too, not only for one
    sent under the window."""
    from test_ade_link_commands import send as passkey_send
    monkeypatch.setattr(link_commands, "OWNER_PER_HOUR", 1)
    mac = paired(client, owner)
    opened(client, owner, device, mac)                # the opening is the budget's one
    r = passkey_send(client, owner, device, payload(client, owner, "instruction", mac.link_id))
    assert r.status_code == 429 and "at most 1 commands an hour" in r.json()["detail"], r.text


def test_the_room_every_command_the_passkey_signs_is_checked_in_counts_a_window_opened(client, owner, device,
                                                                                         monkeypatch):
    """check_room is where a command the passkey signs and a Manager's goal (ade/manager_link.py) are counted, so the
    window's opening is counted there and not beside one caller alone."""
    monkeypatch.setattr(link_commands, "OWNER_PER_HOUR", 1)
    mac = paired(client, owner)
    opened(client, owner, device, mac)
    now = int(time.time())
    with store.connect() as db:
        target = db.execute("SELECT * FROM ade_links WHERE id = ?", (mac.link_id,)).fetchone()
        with pytest.raises(link.LinkError) as refused:
            link_commands.check_room(db, account(client, owner), target, now)
    assert refused.value.status == 429 and "at most 1 commands an hour" in str(refused.value)


def test_a_window_opened_on_one_session_counts_against_a_command_the_passkey_signs_to_another(client, owner, device,
                                                                                               monkeypatch):
    """The hour's budget is the owner's, counted by account: a window opened on one session is one of the hour's
    commands for a command to any other session."""
    from test_ade_link_commands import send as passkey_send
    monkeypatch.setattr(link_commands, "OWNER_PER_HOUR", 1)
    mac = paired(client, owner)
    other = paired(client, owner, name="other")
    opened(client, owner, device, mac)
    r = passkey_send(client, owner, device, payload(client, owner, "instruction", other.link_id))
    assert r.status_code == 429 and "at most 1 commands an hour" in r.json()["detail"], r.text


def test_a_window_ended_before_its_end_still_counts_in_the_hour_it_was_opened(client, owner, device, monkeypatch):
    """A window the owner ended early was opened in the hour all the same, and stays one of its commands, as a command
    withdrawn does."""
    from test_ade_link_commands import send as passkey_send
    monkeypatch.setattr(link_commands, "OWNER_PER_HOUR", 1)
    mac = paired(client, owner)
    d, _ = opened(client, owner, device, mac)
    link_commands.end_delegation(account(client, owner), digest(d))
    assert window_row(d)["ended_at"] is not None
    r = passkey_send(client, owner, device, payload(client, owner, "instruction", mac.link_id))
    assert r.status_code == 429 and "at most 1 commands an hour" in r.json()["detail"], r.text


def test_a_window_is_not_opened_past_the_owner_s_hourly_budget(client, owner, device, monkeypatch):
    """Opening a window is itself one of the owner's commands in the hour: past the budget the opening is refused, and
    no window is kept."""
    from test_ade_link_commands import send as passkey_send
    monkeypatch.setattr(link_commands, "OWNER_PER_HOUR", 1)
    mac = paired(client, owner)
    declare(mac)
    assert passkey_send(client, owner, device, payload(client, owner, "instruction", mac.link_id)).status_code == 200
    d, _ = delegation(client, owner, mac)
    r = open_window(client, owner, device, d)
    assert r.status_code == 429 and "at most 1 commands an hour" in r.json()["detail"], r.text
    assert window_row(d) is None


# ---- what ends a window -------------------------------------------------------------------------------------------

def _ended(d):
    kept = window_row(d)
    return kept["ended_at"] is not None


def test_ending_a_window_that_already_expired_writes_nothing_and_answers_its_own_end(client, owner, device):
    """End now on a window past its end writes nothing, and its answer is the
    window's own end, not the time it was asked."""
    mac = paired(client, owner)
    d, _ = opened(client, owner, device, mac)
    later = d["expires_at"] + 30
    assert link_commands.end_delegation(account(client, owner), digest(d), now=later) == {"ended_at": d["expires_at"]}
    assert window_row(d)["ended_at"] is None


def test_a_signer_posts_nothing_under_a_signing_window(client, owner, device):
    """The signer's relay takes a step the owner signed with a passkey there, never
    a body that names a window, which would reach the window's own post as a second door."""
    from test_ade_signers import paired_signer
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    p = windowed(client, owner, mac, d)
    signer = paired_signer(client, owner)
    r = signer.call("POST", "/commands", {"payload": p, "credential": {},
                                          "delegated": {"digest": digest(d), "sig": page_signed(key, p)}})
    assert r.status_code == 422, r.text
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE client_id = ?", (p["id"],)).fetchone()[0] == 0
    assert window_row(d)["last_n"] == 0


def test_the_relay_parks_no_delegation_for_a_second_screen_which_signs_none(client, owner, device):
    """A delegation is signed with a passkey in the tab that opens the window, and no signer signs one (ade_link.py's
    SIGN_PURPOSES), so the relay parks none, which would hold one of the account's ten places and one of its network's
    three until its nonce ended, as it parks no task's grant. The window still opens with the passkey under that
    nonce."""
    mac = paired(client, owner)
    declare(mac)
    d, _ = delegation(client, owner, mac)
    r = client.post("/api/ade/sign-requests", headers=owner, json={"payload": d})
    assert r.status_code == 422 and "purpose" in r.json()["detail"], r.text
    assert "delegation" not in r.json()["detail"], r.text
    assert client.get("/api/ade/sign-requests", headers=owner).json()["requests"] == []
    assert open_window(client, owner, device, d).status_code == 200


def test_end_now_ends_the_window_and_withdraws_what_no_session_took(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    r = client.post(f"{DELEGATIONS}/{digest(d)}/end", headers=owner)
    assert r.status_code == 200, r.text
    assert _ended(d) and window_row(d)["ended_reason"] == "end_now" and withdrawn(seq)
    state = declare(mac)
    assert digest(d) in state["delegations_ended"] and state["delegations_open"] == {}
    other = signed_up(client, OTHER)
    assert client.post(f"{DELEGATIONS}/{digest(d)}/end", headers=other).status_code == 404


def test_a_new_window_for_the_link_ends_the_one_before(client, owner, device):
    mac = paired(client, owner)
    first, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, first, windowed(client, owner, mac, first))
    second, _ = opened(client, owner, device, mac)
    assert _ended(first) and not _ended(second) and withdrawn(seq)


def test_the_link_s_end_ends_its_windows(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    assert revoke(client, owner, mac.link_id).status_code == 200
    assert _ended(d) and withdrawn(seq)


def test_a_stop_ends_the_link_s_windows_and_withdraws_what_no_session_took(client, owner, device):
    # A stop ends the stopped link's signing window too, as End now does: a session resumed after it takes nothing
    # queued under the old window
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    assert client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).status_code == 200
    assert _ended(d) and window_row(d)["ended_reason"] == "stopped" and withdrawn(seq)
    state = declare(mac)
    assert digest(d) in state["delegations_ended"] and state["delegations_open"] == {}


def test_a_second_stop_ends_a_window_opened_after_the_first(client, owner, device):
    # a window opens while the session is stopped (open_delegation asks for a live link, not a running one), and the
    # next stop ends it as the first would have: each stop ends the link's windows before it answers that the link was
    # stopped already
    mac = paired(client, owner)
    assert client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).status_code == 200
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    assert not _ended(d) and not withdrawn(seq)
    assert client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).status_code == 200
    assert _ended(d) and window_row(d)["ended_reason"] == "stopped" and withdrawn(seq)


def test_a_stop_ends_only_the_stopped_link_s_windows(client, owner, device):
    mac, pc = paired(client, owner), paired(client, owner, name="pc")
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    assert client.post(f"/api/ade/link/links/{pc.link_id}/stop", headers=owner).status_code == 200
    assert not _ended(d) and not withdrawn(seq)


def test_a_re_pair_that_moves_the_link_to_a_new_key_ends_its_windows(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    fresh = Session(client)
    with at_once():
        r = fresh.pair(ticket(client, owner, mac.link_id), name="ignored", kind="cloud", ref="new-ref")
    assert r.json()["status"] == "linked", r.text
    assert _ended(d) and withdrawn(seq)
    # the declaration was the old key's client's, so none is kept for the new one
    with store.connect() as db:
        row = db.execute("SELECT features, features_at FROM ade_links WHERE id = ?", (mac.link_id,)).fetchone()
    assert row["features"] is None and row["features_at"] is None
    listed = client.get("/api/ade/link/sessions", headers=owner).json()["links"]
    assert [x.get("features") for x in listed if x["id"] == mac.link_id] == [[]]


def test_removing_the_passkey_that_opened_a_window_ends_it_and_withdraws_its_instructions(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    r = remove(client, owner, window_row(d)["passkey_id"])
    assert r.status_code == 200, r.text
    assert _ended(d) and withdrawn(seq)


def test_high_security_turned_on_ends_every_window_by_the_path_both_ways_take(client, owner, device):
    """set_high_security and the encryption revocation both turn it on through high_security_in."""
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        passkeys.high_security_in(db, account(client, owner), int(time.time()))
    assert _ended(d) and withdrawn(seq)


def test_sign_out_everywhere_ends_the_account_s_windows(client, owner, device):
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    seq = sent(client, owner, key, d, windowed(client, owner, mac, d))
    r = sign_out_everywhere(client, owner)
    assert r.status_code == 200, r.text
    assert _ended(d) and withdrawn(seq)


# ---- what the page reads -----------------------------------------------------------------------------------------

def test_the_links_list_says_which_link_takes_a_window_and_its_open_one(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    declare(cloud, features=None)
    d, key = opened(client, owner, device, mac)
    sent(client, owner, key, d, windowed(client, owner, mac, d))
    links = {x["id"]: x for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    assert links[mac.link_id].get("features") == ["delegation-1"] and links[cloud.link_id].get("features") == []
    assert links[mac.link_id].get("delegation") == {"digest": digest(d), "created_at": d["created_at"],
                                                "expires_at": d["expires_at"], "last_n": 1, "untaken": 1}
    assert "delegation" in links[cloud.link_id] and links[cloud.link_id]["delegation"] is None


def test_the_links_list_says_which_window_each_command_was_sent_in_and_how_it_ended(client, owner, device):
    """What the page's history line reads: a command sent under a window carries that window's
    digest, times and end; one the owner signed with their passkey carries none."""
    mac = paired(client, owner)
    d, key = opened(client, owner, device, mac)
    under = sent(client, owner, key, d, windowed(client, owner, mac, d))
    own = client.post(COMMANDS, headers=owner, json=(lambda p: {"payload": p, "credential": assertion(device, p)})(
        payload(client, owner, "instruction", mac.link_id, "with my passkey"))).json()["seq"]
    assert client.post(f"{DELEGATIONS}/{digest(d)}/end", headers=owner).status_code == 200
    links = {x["id"]: x for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    commands = {c["seq"]: c for c in links[mac.link_id]["commands"]}
    window = commands[under].get("delegation")
    assert isinstance(window, dict) and window.get("ended_at") is not None and commands[under]["withdrawn_at"]
    assert {k: window.get(k) for k in ("digest", "created_at", "expires_at", "ended_reason")} == {
        "digest": digest(d), "created_at": d["created_at"], "expires_at": d["expires_at"], "ended_reason": "end_now"}
    assert "delegation" not in commands[own]


def test_the_state_keeps_only_words_it_reads_and_lists_an_early_end_for_17_minutes(client, owner, device):
    mac = paired(client, owner)
    declare(mac)
    declare(mac, features="Not A Word")                   # not words this server reads: what it kept stays
    links = {x["id"]: x for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    assert links[mac.link_id].get("features") == ["delegation-1"]
    d, _ = opened(client, owner, device, mac)
    assert client.post(f"{DELEGATIONS}/{digest(d)}/end", headers=owner).status_code == 200
    assert digest(d) in declare(mac)["delegations_ended"]
    with store.connect() as db:
        db.execute("UPDATE ade_link_delegations SET ended_at = ? WHERE digest = ?",
                   (int(time.time()) - 17 * 60 - 1, digest(d)))
    assert digest(d) not in declare(mac)["delegations_ended"]
