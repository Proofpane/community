"""A device approved with a passkey, while the account's end-to-end encryption is on.
With encryption on, removing a passkey goes through a revocation, held as every change to encryption is; its
placement is the removal, as for tickets and pairings (tests/test_link_pair_e2e.py): a device whose approval the passkey
it removes confirmed is refused if it has not signed in, and its sign-in ended if it has, as the revocation is placed,
not when its hold ends, so a lost passkey signs no device in meanwhile."""

from fido_fixtures import add_hardware, maker, metadata  # noqa: F401  (fixtures)
from test_ade_e2e import Keys, device, enrol, keys, owner, passkey_of, revocation, second  # noqa: F401  (fixtures)
from test_device_approval import approve, me, poll, start, word_for
from test_passkeys import Authenticator, add


def test_a_revocation_that_removes_a_passkey_ends_the_devices_it_approved_as_it_is_placed(client, owner, keys, device,
                                                                                          second):
    keys.turn_on()
    enrol(keys, second, device)
    gone = passkey_of(client, owner, second)["id"]
    thief, waiting, mine = start(client, "Thief"), start(client, "Thief 2"), start(client, "Mine")
    for asked, by in ((thief, second), (waiting, second), (mine, device)):
        assert approve(client, owner, asked["code"], word_for(client, owner, asked["code"], by)).status_code == 200
    thief_token, my_token = poll(client, thief)["token"], poll(client, mine)["token"]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[gone])[0])
    assert r.status_code == 200, r.text
    assert r.json()["hold"]["takes_effect_at"] > r.json()["hold"]["approved_at"]       # the revocation itself waits
    assert me(client, thief_token).status_code == 401
    assert poll(client, waiting) == {"status": "refused", "withdrawn": True}
    assert me(client, my_token).status_code == 200      # approved with the passkey that stays


def test_a_revocation_that_turns_high_security_on_refuses_the_devices_no_hardware_key_approved(client, owner, metadata,
                                                                                              maker):
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    assert r.json()["tier"] == "hardware", r.text
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    by_password, by_phone, by_key = (start(client, n) for n in ("Password", "Phone", "Key"))
    for asked, by in ((by_password, None), (by_phone, phone), (by_key, yubikey)):
        assert approve(client, owner, asked["code"], word_for(client, owner, asked["code"], by)).status_code == 200
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, high_security=True)[0])
    assert r.status_code == 200, r.text
    assert poll(client, by_password) == poll(client, by_phone) == {"status": "refused", "withdrawn": True}
    assert poll(client, by_key)["status"] == "approved"
