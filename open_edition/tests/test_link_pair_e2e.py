"""A pairing that waits, while the account's end-to-end encryption is on.
With encryption on, removing a passkey and turning high security on go through a revocation,
held as every change to encryption is. The revocation's placement is the removal's: it voids the unused tickets the
passkeys it removes confirmed (every ticket no hardware key confirmed, when it turns high security on), and cancels each
approval or pairing still held, so a pairing a lost passkey's ticket made links nothing while the revocation waits. A
settle that runs after both a revocation's end and a pairing's applies the revocation as of its own time, cancelling
the holds that ended after it; and the end of a pairing applies high security's rule, as pair does."""

from fido_fixtures import add_hardware, maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import passkeys, store
from proofpane_open.ade import e2e_keys, holds, link, link_asks
from test_ade_e2e import Keys, device, enrol, keys, owner, passkey_of, revocation, second  # noqa: F401  (fixtures)
from test_ade_link import Session, ticket
from test_passkeys import Authenticator, add, confirmed


def poll(s):
    """Where the session's pairing stands, as its client reads it (past the poll interval)."""
    with store.connect() as db:
        db.execute("UPDATE ade_link_asks SET last_poll_at = NULL")
    r = s.call("GET", "/ask")
    assert r.status_code == 200, r.text
    return r.json()


def ticket_by(client, owner, device):
    """A ticket for a new link, confirmed with `device`'s passkey, made for a ticket for a new session."""
    r = client.post("/api/ade/link/tickets", headers=owner,
                    json=confirmed(client, owner, device, change="link-ticket:new"))
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def signer_ticket_by(client, owner, device=None):
    """A ticket to pair a signer (ade/signers.py), confirmed with `device`'s passkey, or the password, made for one: the
    revocation's placement voids it as it voids a session's ticket (link.void_tickets)."""
    r = client.post("/api/ade/signers/tickets", headers=owner,
                    json=confirmed(client, owner, device, change="signer-ticket"))
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def signer_pairs(client, ticket):
    from test_ade_signers import Signer
    return Signer(client).pair(ticket).status_code


def live_links():
    with store.connect() as db:
        return [r["name"] for r in db.execute("SELECT name FROM ade_links WHERE revoked_at IS NULL ORDER BY name")]


def test_a_revocation_that_removes_a_lost_passkey_ends_its_pairings_and_tickets_as_it_is_placed(client, owner, keys,
                                                                                                 device, second):
    keys.turn_on()
    enrol(keys, second, device)
    gone = passkey_of(client, owner, second)["id"]
    # whoever holds the lost device (`second`) and a sign-in pairs with a ticket its passkey confirmed, and keeps another
    thief = Session(client)
    got = thief.pair(ticket_by(client, owner, second), name="thief").json()
    assert got["status"] == "approved", got
    spare = ticket_by(client, owner, second)
    mine = ticket_by(client, owner, device)                  # the owner's own, confirmed with the passkey that stays
    spare_signer, mine_signer = signer_ticket_by(client, owner, second), signer_ticket_by(client, owner, device)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[gone])[0])
    assert r.status_code == 200, r.text
    revoking = r.json()["hold"]
    assert revoking["takes_effect_at"] > revoking["approved_at"]          # the revocation itself waits
    # as it is placed: the pairing cancelled, the lost passkey's spare ticket void, the owner's own ticket untouched;
    # a signer's ticket alike
    assert poll(thief)["status"] == "cancelled"
    assert Session(client).pair(spare, name="later").status_code == 403
    assert Session(client).pair(mine, name="mine").json()["status"] == "approved"
    assert signer_pairs(client, spare_signer) == 403 and signer_pairs(client, mine_signer) == 200
    (left,) = [h for h in client.get("/api/ade/holds", headers=owner).json()["holds"] if h["id"] == revoking["id"]]
    assert left["cancelled_at"] is None                                   # the revocation is no approval it cancels
    holds.settle(keys.account, now=got["takes_effect_at"])
    assert "thief" not in live_links()


def test_a_revocation_that_removes_nothing_leaves_a_pairing_and_its_tickets_as_they_are(client, owner, keys, device):
    keys.turn_on()
    waiting = Session(client)
    assert waiting.pair(ticket_by(client, owner, device), name="mac").json()["status"] == "approved"
    spare = ticket_by(client, owner, device)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys)[0])     # a new master key, no more
    assert r.status_code == 200, r.text
    assert poll(waiting)["status"] == "approved"
    assert Session(client).pair(spare, name="studio").json()["status"] == "approved"


def test_a_revocation_that_turns_high_security_on_ends_the_tickets_no_hardware_key_confirmed(client, owner, metadata,
                                                                                            maker):
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    assert r.json()["tier"] == "hardware", r.text
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    waiting = Session(client)
    assert waiting.pair(ticket(client, owner), name="mac").json()["status"] == "approved"   # the password's ticket
    spare = ticket(client, owner)
    by_key = ticket_by(client, owner, yubikey)
    spare_signer, by_key_signer = signer_ticket_by(client, owner), signer_ticket_by(client, owner, yubikey)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, high_security=True)[0])
    assert r.status_code == 200, r.text
    assert poll(waiting)["status"] == "cancelled"
    assert Session(client).pair(spare, name="later").status_code == 403
    assert Session(client).pair(by_key, name="studio").json()["status"] == "approved"
    # a signer's ticket alike, although high security is not on until the revocation takes effect
    assert not passkeys.high_security(keys.account)
    assert signer_pairs(client, spare_signer) == 403 and signer_pairs(client, by_key_signer) == 200


def test_a_late_settle_applies_a_revocation_as_of_its_own_time(client, owner, metadata, maker, monkeypatch):
    """The revocation that turns high security on ends first; a pairing by the password's ticket, placed while it waited,
    ends after it; one settle after both applies the revocation, which cancels the pairing as it would have on time."""
    import time
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    assert r.json()["tier"] == "hardware", r.text
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, high_security=True)[0])
    assert r.status_code == 200, r.text
    revoking = r.json()["hold"]
    s, t = Session(client), ticket(client, owner)          # made after the placement: no ticket it voided
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 30)  # the session pairs half a minute later
    got = s.pair(t, name="mac").json()
    assert got["status"] == "approved" and got["takes_effect_at"] > revoking["takes_effect_at"]
    holds.settle(keys.account, now=got["takes_effect_at"])
    assert passkeys.high_security(keys.account)
    assert poll(s)["status"] == "cancelled" and "mac" not in live_links()


def test_a_late_settle_applies_a_passkey_s_removal_as_of_its_own_time(client, owner, keys, device, second, monkeypatch):
    """A revocation that removes a passkey ends first; a pairing placed while it waited ends after it; one settle after
    both cancels the pairing, as the removal would have on time, though its ticket's passkey stays."""
    import time
    keys.turn_on()
    enrol(keys, second, device)
    r = client.post("/api/ade/e2e/revoke", headers=owner,
                    json=revocation(keys, remove=[passkey_of(client, owner, second)["id"]])[0])
    assert r.status_code == 200, r.text
    revoking = r.json()["hold"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + revoking["takes_effect_at"] - revoking["approved_at"] - 30)
    s = Session(client)
    got = s.pair(ticket_by(client, owner, device), name="mac").json()     # the passkey that stays confirmed it
    assert got["status"] == "approved" and got["takes_effect_at"] > revoking["takes_effect_at"]
    holds.settle(keys.account, now=got["takes_effect_at"])
    assert poll(s)["status"] == "cancelled" and live_links() == []


def test_the_end_of_a_pairing_applies_high_security_s_rule(client, owner, metadata, maker):
    """High security on at the pairing's end, by whatever came between, and a ticket no hardware key confirmed: not
    linked. So for a ticket the password confirmed, for one a hardware key confirmed that is no longer counted one,
    and for one a device passkey confirmed that is counted a hardware key since: what confirmed it then decides."""
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    assert r.json()["tier"] == "hardware", r.text
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    # three pairings, each by a ticket confirmed another way, all made while high security was off
    pairings = {how: Session(client) for how in ("password", "YubiKey", "phone")}
    ends = [pairings["password"].pair(ticket(client, owner), name="mac").json()["takes_effect_at"],
            pairings["YubiKey"].pair(ticket_by(client, owner, yubikey), name="studio").json()["takes_effect_at"],
            pairings["phone"].pair(ticket_by(client, owner, phone), name="laptop").json()["takes_effect_at"]]
    # then high security on, with the pairings still held (no way through the app does it), the YubiKey reported as no
    # hardware key, and the phone's passkey counted one
    account = passkeys_owner(client, owner)
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (account,))
        db.execute("UPDATE passkeys SET tier = 'device' WHERE id = ?", (passkey_of(client, owner, yubikey)["id"],))
        db.execute("UPDATE passkeys SET tier = 'hardware' WHERE id = ?", (passkey_of(client, owner, phone)["id"],))
    holds.settle(account, now=max(ends))
    for how, s in pairings.items():
        answer = poll(s)
        assert (answer["status"], answer["detail"]) == ("failed", link.TICKET_NOT_HARDWARE), how
    assert live_links() == []


def test_the_end_of_a_pairing_by_a_hardware_key_s_ticket_links_it_while_high_security_is_on(client, owner, metadata,
                                                                                           maker):
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    assert r.json()["tier"] == "hardware", r.text
    s = Session(client)
    got = s.pair(ticket_by(client, owner, yubikey), name="studio").json()
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (passkeys_owner(client, owner),))
    holds.settle(passkeys_owner(client, owner), now=got["takes_effect_at"])
    assert poll(s)["status"] == "linked" and live_links() == ["studio"]


def passkeys_owner(client, owner):
    return client.get("/api/auth/me", headers=owner).json()["id"]


def test_the_hold_kinds_a_revocation_s_placement_cancels_are_those_a_lost_device_s_steps_do(client, owner, keys,
                                                                                             device, second):
    """The kinds cancel_all reaches: unsigned, with no guard. A change to encryption is neither, so a revocation's
    placement never cancels another change to encryption that waits, nor itself."""
    reached = [kind for kind, k in holds._KINDS.items() if k["unsigned"] and k["guard"] is None]
    assert link_asks.HOLD in reached and link_asks.HOLD_HIGH in reached
    assert not {e2e_keys.HOLD, e2e_keys.HOLD_RECOVERY, e2e_keys.HOLD_LONG} & set(reached)
