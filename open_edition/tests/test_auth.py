"""Accounts come only from one-time invitation links bound to an address; tokens are HMAC-signed and expire."""

import os
import sqlite3
import time

import pytest

from conftest import ADMIN, SECRET, signed_up


def test_a_password_is_hashed_with_scrypt_and_checks():
    from proofpane_open import auth
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$16384$8$1$") and "correct" not in stored
    assert auth.check_password("correct horse battery", stored)
    assert not auth.check_password("correct horse batterx", stored)
    assert auth.hash_password("correct horse battery") != stored  # a fresh salt each time


@pytest.mark.parametrize("bad", ["short", "", "x" * 257, None, 12345678901])
def test_passwords_outside_the_limits_are_refused(bad):
    from proofpane_open import auth
    with pytest.raises(auth.AuthError):
        auth.hash_password(bad)


def test_a_malformed_stored_hash_never_checks():
    from proofpane_open import auth
    for stored in ("", "bcrypt$x", "scrypt$1$2$3", "scrypt$16384$8$1$!!$!!", None):
        assert not auth.check_password("anything at all", stored)


def test_a_token_names_its_user_until_it_expires():
    from proofpane_open import auth, config
    now = 1_800_000_000
    token = auth.issue_token("u1", now=now)
    assert auth.read_token(token, now=now) == "u1"
    assert auth.read_token(token, now=now + config.TOKEN_TTL_SECONDS - 1) == "u1"
    assert auth.read_token(token, now=now + config.TOKEN_TTL_SECONDS) is None


def test_a_token_is_refused_if_any_part_changes_or_the_secret_does(monkeypatch):
    from proofpane_open import auth
    import base64
    import json
    token = auth.issue_token("u1")
    payload, mac = token.split(".")
    forged = base64.urlsafe_b64encode(json.dumps({"sub": "admin", "exp": 9_999_999_999}).encode()).rstrip(b"=").decode()
    for bad in (forged + "." + mac, payload + "." + mac[:-2] + ("AA" if mac[-2:] != "AA" else "BB"), payload,
                token + ".x", "", None, 5, "a.b"):
        assert auth.read_token(bad) is None, bad
    monkeypatch.setenv("OPEN_EDITION_TOKEN_SECRET", "s" * 40)
    assert auth.read_token(token) is None


def test_the_app_refuses_a_short_or_missing_secret(monkeypatch):
    from proofpane_open import config
    monkeypatch.setenv("OPEN_EDITION_TOKEN_SECRET", "x" * 31)
    with pytest.raises(RuntimeError):
        config.token_secret()
    monkeypatch.delenv("OPEN_EDITION_TOKEN_SECRET")
    with pytest.raises(RuntimeError):
        config.token_secret()


def test_an_invitation_works_once_and_only_for_its_address(client):
    from proofpane_open import auth
    token = auth.create_invitation("Bob@Example.org ", "test")
    assert auth.invitation_email(token) == "bob@example.org"
    user_id, email = auth.register(token, "a good long password")
    assert email == "bob@example.org"
    assert auth.invitation_email(token) is None
    with pytest.raises(auth.AuthError):
        auth.register(token, "a good long password")
    assert auth.login("bob@example.org", "a good long password") == user_id


def test_an_invitation_expires():
    from proofpane_open import auth, config
    now = 1_800_000_000
    token = auth.create_invitation("late@example.org", "test", now=now)
    assert auth.invitation_email(token, now=now + config.INVITE_TTL_SECONDS - 1) == "late@example.org"
    assert auth.invitation_email(token, now=now + config.INVITE_TTL_SECONDS) is None
    with pytest.raises(auth.AuthError):
        auth.register(token, "a good long password", now=now + config.INVITE_TTL_SECONDS)


def test_the_token_is_stored_only_as_its_hash(env):
    from proofpane_open import auth, config
    token = auth.create_invitation("c@example.org", "test")
    with sqlite3.connect(config.db_path()) as db:
        rows = db.execute("SELECT token_hash FROM invitations").fetchall()
    assert rows and all(token not in r[0] for r in rows)


def test_no_invitation_for_an_address_that_has_an_account():
    from proofpane_open import auth
    auth.register(auth.create_invitation("d@example.org", "test"), "a good long password")
    with pytest.raises(auth.AuthError):
        auth.create_invitation("d@example.org", "test")


def test_two_invitations_for_one_address_give_one_account():
    from proofpane_open import auth
    t1 = auth.create_invitation("e@example.org", "test")
    t2 = auth.create_invitation("e@example.org", "test")
    auth.register(t1, "a good long password")
    with pytest.raises(auth.AuthError):
        auth.register(t2, "another long password")


def test_login_gives_one_answer_for_a_wrong_email_or_a_wrong_password():
    from proofpane_open import auth
    auth.register(auth.create_invitation("f@example.org", "test"), "a good long password")
    messages = set()
    for email, password in (("f@example.org", "wrong password!"), ("nobody@example.org", "a good long password"),
                            ("not an email", "a good long password")):
        with pytest.raises(auth.AuthError) as e:
            auth.login(email, password)
        messages.add(str(e.value))
    assert messages == {"Wrong email or password."}


def test_admin_comes_from_the_setting_not_from_the_account(monkeypatch):
    from proofpane_open import auth
    uid, _ = auth.register(auth.create_invitation(ADMIN, "test"), "a good long password")
    assert auth.user(uid)["admin"] is True
    monkeypatch.setenv("OPEN_EDITION_ADMINS", "someone@else.org")
    assert auth.user(uid)["admin"] is False


def test_the_database_is_created_readable_by_its_owner_only(env):
    from proofpane_open import config, store
    previous = os.umask(0o022)
    try:
        with store.connect():
            pass
    finally:
        os.umask(previous)
    assert os.stat(config.db_path()).st_mode & 0o777 == 0o600
