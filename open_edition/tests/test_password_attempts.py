"""Wrong passwords, counted (auth.py): per account and per network, over a window, at sign-in and at the
confirmation a change to how the account signs in takes (passkeys.confirm). Past the count no password is checked."""

import threading
import time

import pytest

from conftest import ADMIN, signed_up
from proofpane_open import auth

PASSWORD = "correct horse battery"          # conftest's signed_up


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def address(monkeypatch):
    """The address the next requests come from, as the server sees it."""
    at = {"now": "203.0.113.7"}
    monkeypatch.setattr("proofpane_open.main._client", lambda request: at["now"])
    return at


def login(client, password):
    return client.post("/api/auth/login", json={"email": ADMIN, "password": password})


def test_ten_wrong_passwords_from_one_network_refuse_the_next_unchecked(client, owner, address, monkeypatch):
    for _ in range(auth.PASSWORD_PER_NETWORK):
        assert login(client, "wrong horse battery").status_code == 400
    checked = []
    real = auth.check_password
    monkeypatch.setattr(auth, "check_password", lambda *a: checked.append(1) or real(*a))
    r = login(client, PASSWORD)                                      # even the right one
    assert r.status_code == 429 and "Too many wrong passwords" in r.json()["detail"]
    assert 1 <= int(r.headers["Retry-After"]) <= auth.PASSWORD_WINDOW
    assert checked == []                                             # no scrypt run for it
    address["now"] = "198.51.100.2"                                  # another network: the account is under its count
    assert login(client, PASSWORD).status_code == 200


def test_an_account_is_held_past_its_count_from_any_network(client, owner, address):
    for net in ("203.0.113.7", "203.0.113.8"):
        address["now"] = net
        for _ in range(auth.PASSWORD_PER_ACCOUNT // 2):
            assert login(client, "wrong horse battery").status_code == 400
    address["now"] = "198.51.100.2"
    assert login(client, PASSWORD).status_code == 429
    # an IPv6 network is its /64: a neighbour in it is the same network
    auth._failures.clear()
    address["now"] = "2001:db8::1"
    for _ in range(auth.PASSWORD_PER_NETWORK):
        login(client, "wrong horse battery")
    address["now"] = "2001:db8::ffff"
    assert login(client, PASSWORD).status_code == 429
    address["now"] = "2001:db8:0:1::1"
    assert login(client, PASSWORD).status_code == 200


def test_the_count_leaves_with_the_window_and_a_right_password_clears_it(client, owner, address, monkeypatch):
    for _ in range(auth.PASSWORD_PER_NETWORK - 1):
        login(client, "wrong horse battery")
    assert login(client, PASSWORD).status_code == 200                # clears this account's and network's count
    for _ in range(auth.PASSWORD_PER_NETWORK - 1):
        assert login(client, "wrong horse battery").status_code == 400
    assert login(client, PASSWORD).status_code == 200
    for _ in range(auth.PASSWORD_PER_NETWORK):
        login(client, "wrong horse battery")
    assert login(client, PASSWORD).status_code == 429
    later = auth.time.time() + auth.PASSWORD_WINDOW + 1
    monkeypatch.setattr(auth.time, "time", lambda: later)
    assert login(client, PASSWORD).status_code == 200


def test_an_address_that_names_no_account_is_counted_too(client, owner, address):
    for _ in range(auth.PASSWORD_PER_NETWORK):
        assert client.post("/api/auth/login", json={"email": "nobody@example.org",
                                                    "password": "x" * 10}).status_code == 400
    assert login(client, PASSWORD).status_code == 429                # the network's count, whatever account


def test_an_address_with_no_account_is_held_at_the_same_count_as_one_with(client, owner, address):
    """Across networks, the address's own count holds both at its twenty-first: the answer never tells them apart."""
    for email in (ADMIN, "nobody@example.org"):
        auth._failures.clear()
        answers = []
        for n in range(auth.PASSWORD_PER_ACCOUNT + 1):
            address["now"] = f"203.0.113.{n % 3}"                    # three networks, none past its own ten
            answers.append(client.post("/api/auth/login", json={"email": email, "password": "x" * 10}).status_code)
        assert answers == [400] * auth.PASSWORD_PER_ACCOUNT + [429], email
    kept = [k for k in auth._failures if k[0] == "account"]
    assert kept and all("@" not in k[1] for k in kept)                 # what was typed is kept as a digest only
    assert all(e[1] is None or "@" not in e[1] for entries in auth._failures.values() for e in entries)


def test_a_check_that_waits_too_long_for_its_turn_is_answered_busy_and_not_counted(client, owner, address, monkeypatch):
    monkeypatch.setattr(auth, "SCRYPT_WAIT", 0.2)
    for _ in range(auth.SCRYPT_AT_ONCE):
        auth._scrypt.acquire()
    answers = []
    try:
        # in a thread joined with a timeout: a check that waited for a turn without end would fail here, not hang
        t = threading.Thread(target=lambda: answers.extend(login(client, "wrong horse battery")
                                                           for _ in range(auth.PASSWORD_PER_NETWORK + 1)))
        t.start()
        t.join(timeout=20)
        assert not t.is_alive(), "a check waited for its turn without end"
    finally:
        for _ in range(auth.SCRYPT_AT_ONCE):
            auth._scrypt.release()
    assert [r.status_code for r in answers] == [429] * (auth.PASSWORD_PER_NETWORK + 1)
    r = answers[0]
    assert r.json()["detail"].startswith("Too many passwords are being checked at once") and r.headers["Retry-After"] == "2"
    assert auth._failures == {}                                       # none of them, never checked, was counted
    assert login(client, PASSWORD).status_code == 200


def test_a_right_password_takes_back_the_account_s_count_too(client, owner, address):
    """19 wrong over three networks, the right one, then 20 more wrong over three others: all checked, and the next
    refused, since the right one took the account's count back."""
    for n in range(auth.PASSWORD_PER_ACCOUNT - 1):
        address["now"] = f"203.0.113.{n % 3}"
        assert login(client, "wrong horse battery").status_code == 400
    address["now"] = "203.0.113.9"
    assert login(client, PASSWORD).status_code == 200
    for n in range(auth.PASSWORD_PER_ACCOUNT):
        address["now"] = f"198.51.100.{n % 3}"
        assert login(client, "wrong horse battery").status_code == 400
    address["now"] = "192.0.2.1"
    assert login(client, "wrong horse battery").status_code == 429


def test_the_confirmation_and_signing_in_share_the_account_s_count(client, owner, address):
    for n in range(auth.PASSWORD_PER_ACCOUNT // 2):
        address["now"] = f"203.0.113.{n % 3}"
        assert client.post("/api/account/confirm", headers=owner, json={"password": "wrong horse battery"}) \
            .status_code == 403
    for n in range(auth.PASSWORD_PER_ACCOUNT // 2):
        address["now"] = f"198.51.100.{n % 3}"
        assert login(client, "wrong horse battery").status_code == 400
    address["now"] = "192.0.2.1"
    assert login(client, PASSWORD).status_code == 429


def test_the_confirmation_counts_and_is_held_as_signing_in_is(client, owner, address):
    for _ in range(auth.PASSWORD_PER_NETWORK):
        r = client.post("/api/account/confirm", headers=owner, json={"password": "wrong horse battery"})
        assert r.status_code == 403
    r = client.post("/api/account/confirm", headers=owner, json={"password": PASSWORD})
    assert r.status_code == 429 and r.json()["detail"].endswith("or confirm with a passkey.")
    r = login(client, PASSWORD)                                      # one count for both doors
    assert r.status_code == 429 and r.json()["detail"].endswith("or sign in with a passkey.")
    address["now"] = "198.51.100.2"
    r = client.post("/api/account/confirm", headers=owner, json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["confirmation"]


def test_attempts_made_at_once_are_counted_as_they_arrive(client, owner, monkeypatch):
    checked = []
    real = auth.check_password

    def slow(password, stored):
        checked.append(1)
        time.sleep(0.05)
        return real(password, stored)

    monkeypatch.setattr(auth, "check_password", slow)
    barrier = threading.Barrier(40)
    answers = []

    def guess():
        barrier.wait()
        try:
            auth.login(ADMIN, "wrong horse battery", "203.0.113.7")
        except auth.TooManyAttempts:
            answers.append(429)
        except auth.AuthError:
            answers.append(400)

    threads = [threading.Thread(target=guess) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert (answers.count(400), answers.count(429), len(checked)) == (10, 30, 10)


def test_at_most_a_few_passwords_are_checked_at_once(client, owner, monkeypatch):
    running, most, lock = [0], [0], threading.Lock()

    def slow(password, stored):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.1)
        with lock:
            running[0] -= 1
        return False

    monkeypatch.setattr(auth, "check_password", slow)
    barrier = threading.Barrier(20)

    def guess(n):
        barrier.wait()
        try:
            auth.login(ADMIN, "wrong horse battery", f"203.0.113.{n % 4}")      # four networks: all 20 counted in
        except auth.AuthError:
            pass

    threads = [threading.Thread(target=guess, args=(n,)) for n in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == auth.SCRYPT_AT_ONCE


def test_a_right_password_takes_back_only_its_own_attempts_from_the_network(client, owner, address):
    signed_up(client, "second@example.com")
    for _ in range(auth.PASSWORD_PER_NETWORK - 1):
        assert login(client, "wrong horse battery").status_code == 400
    r = client.post("/api/auth/login", json={"email": "second@example.com", "password": PASSWORD})
    assert r.status_code == 200                                      # the other account's own sign-in
    assert login(client, "wrong horse battery").status_code == 400   # the network's tenth
    assert login(client, PASSWORD).status_code == 429                # its guesses still count


def test_an_address_with_no_account_is_checked_against_a_hash_of_nothing(client, owner, monkeypatch):
    seen = []
    real = auth.check_password
    monkeypatch.setattr(auth, "check_password", lambda password, stored: seen.append(stored) or real(password, stored))
    r = client.post("/api/auth/login", json={"email": "nobody@example.org", "password": "x" * 10})
    assert r.status_code == 400 and len(seen) == 1 and seen[0].startswith("scrypt$")
