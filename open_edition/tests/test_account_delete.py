"""Deleting an account (queue 4.327, as ruled on 2026-11-22): a deletion waits, as design D's D15 has it, its delay the
deletion's own setting (OPEN_EDITION_ADE_ACCOUNT_DELETE_HOLD, whole seconds from a day to thirty days, else seven
days), which ade/holds.py's place takes from its caller while its MOST_DELAY bounds every other kind as before; it is
asked for with a confirmation of its own subject, and cancelled with another. It is placed from the page with a fresh
confirmation by account_holds' rule for its maker (a passkey, the password while the account has none), never on the
desktop app's own account, which has nothing to confirm with; its devices are told it waits; the page reads it; only a
credential that counted when it was placed cancels it, with a confirmation made for that cancel, never the sign-in
alone. At its time the account is gone, its devices told, and the device it was placed from can read that it is done
by the hold's id alone. The operator places the same hold, its out-of-band check typed and kept, never at once. What
the deletion removes and keeps, table by table: tests/test_account_delete_rows.py."""

import json
import time

import pytest

from conftest import ADMIN, signed_up
from fido_fixtures import maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import __main__ as command
from proofpane_open import account_holds, passkeys, store
from proofpane_open.ade import holds, push
from test_ade_push import FCM, Device, PushService
from test_high_security import ids, keys, on, owner  # noqa: F401  (keys and owner: fixtures)
from test_passkeys import Authenticator, add, confirmed, remove, sign_out_everywhere

NOW = 1_790_000_000
WEEK, DAY, MONTH = 604_800, 86_400, 2_592_000


# 1. the delay: the deletion's own bound, read at each placement
@pytest.mark.parametrize("raw,want", [(None, WEEK), (str(DAY), DAY), (str(MONTH), MONTH), ("700000", 700_000),
                                      (str(DAY - 1), WEEK), (str(MONTH + 1), WEEK), ("", WEEK), ("3.5", WEEK),
                                      ("1e6", WEEK), (" 90000", 90_000), ("-90000", WEEK), ("0", WEEK),
                                      ("086400", DAY), ("86400\n", DAY), ("12345678", WEEK)])
def test_the_delay_is_the_deletions_own_setting_within_its_bound(monkeypatch, raw, want):
    from proofpane_open import account_delete
    if raw is None:
        monkeypatch.delenv(account_delete.SETTING, raising=False)
    else:
        monkeypatch.setenv(account_delete.SETTING, raw)
    assert account_delete.SETTING == "OPEN_EDITION_ADE_ACCOUNT_DELETE_HOLD"
    assert account_delete.delay() == want


# 2. ade/holds.py's place takes a delay its caller computes; MOST_DELAY and every other kind's delay unchanged
@pytest.fixture
def test_kind(monkeypatch):
    monkeypatch.setattr(holds, "_KINDS", dict(holds._KINDS))
    holds.register("test.deletion-delay", lambda db, hold, now: None, setting="PP_TEST_DELETE_DELAY", default=120)
    return "test.deletion-delay"


def test_place_takes_a_delay_from_its_caller(test_kind):
    with store.connect() as db:
        given = holds.place(db, "u-1", test_kind, "s-1", "a thing", NOW, seconds=WEEK)
        default = holds.place(db, "u-1", test_kind, "s-2", "a thing", NOW)
    assert given["takes_effect_at"] == NOW + WEEK
    assert default["takes_effect_at"] == NOW + 120


def test_most_delay_and_the_settings_bound_are_unchanged(test_kind, monkeypatch):
    assert holds.MOST_DELAY == DAY
    monkeypatch.setenv("PP_TEST_DELETE_DELAY", str(DAY + 1))
    assert holds.delay(test_kind) == 120


# 3. the confirmations' subjects
def test_the_deletion_and_its_cancel_have_subjects_of_their_own():
    assert passkeys.change_subject("account-delete") == "account-delete"
    hold = "0123456789abcdef0123456789abcdef"
    assert passkeys.change_subject("account-delete-cancel:" + hold) == "account-delete-cancel:" + hold
    for bad in ("account-delete:" + hold, "account-delete-cancel:", "account-delete-cancel:" + hold[:-1],
                "account-delete-cancel:" + hold.upper()):
        with pytest.raises(passkeys.PasskeyError):
            passkeys.change_subject(bad)


# 4. placing it: POST /api/account/delete, with a confirmation made for it, by account_holds' rule for its maker
DELETE = "/api/account/delete"
CANCEL = "/api/ade/holds/{}/cancel"
DONE = "/api/account/deletion/{}"


def me(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def word(client, h, change, device=None):
    """{"confirmation": ...} made for `change`: by `device`, or by the password."""
    return confirmed(client, h, device, change=change)


def place(client, h, body):
    return client.post(DELETE, headers=h, json=body)


def cancel(client, h, hold_id, body):
    return client.post(CANCEL.format(hold_id), headers=h, json=body)


def pending_row(user_id):
    with store.connect() as db:
        return db.execute("SELECT * FROM account_pending WHERE user_id = ?", (user_id,)).fetchone()


def deletions(user_id):
    from proofpane_open import account_delete
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_holds WHERE user_id = ? AND kind = ? ORDER BY approved_at",
                                            (user_id, account_delete.KIND))]


@pytest.fixture
def service(monkeypatch):
    """The push service, played by a test; a notice is sent at once here instead of from a thread of its own."""
    played = PushService()
    monkeypatch.setattr(push, "_default_post", played)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    return played


def subscribed(client, h):
    device = Device()
    push.subscribe(me(client, h), FCM, device.p256dh, push.b64u(device.auth))
    return device


def heard(device, service):
    return [(m["title"], m["body"], m["url"]) for m in (device.decrypt(body) for _, _, body in service.posts)]


def test_a_passkeys_confirmation_places_a_deletion_that_waits_its_delay(client):
    h = signed_up(client)
    user = me(client, h)
    device = Authenticator()
    assert add(client, h, device).status_code == 200
    (key,) = client.get("/api/account/passkeys", headers=h).json()["passkeys"]
    r = place(client, h, word(client, h, "account-delete", device))
    assert r.status_code == 200, r.text
    d = r.json()["deletion"]
    assert d["takes_effect_at"] - d["approved_at"] == WEEK and d["made_with"] == "passkey" and d["checked"] is None
    assert d["told"] == 0                                       # how many devices its notice went toward: none here
    assert sorted(d["cancel_by"]) == sorted(["password", key["id"]])          # the standard tier: all that counts
    (hold,) = deletions(user)
    assert (hold["id"], hold["takes_effect_at"], hold["cancelled_at"], hold["done_at"]) == (
        d["id"], d["takes_effect_at"], None, None)
    row = pending_row(user)
    assert (row["hold_id"], row["kind"], row["maker"]) == (d["id"], account_holds.DELETE, key["id"])


def test_with_no_passkey_the_password_places_it(client):
    h = signed_up(client)
    user = me(client, h)
    r = place(client, h, word(client, h, "account-delete"))
    assert r.status_code == 200, r.text
    d = r.json()["deletion"]
    assert d["made_with"] == "password" and d["cancel_by"] == ["password"]
    assert pending_row(user)["maker"] == account_holds.PASSWORD


def test_the_password_never_places_it_while_the_account_has_a_passkey(client):
    from proofpane_open import account_delete
    h = signed_up(client)
    user = me(client, h)
    assert add(client, h, Authenticator()).status_code == 200
    r = place(client, h, word(client, h, "account-delete"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == account_delete.PASSKEY_FIRST
    assert deletions(user) == [] and pending_row(user) is None


def test_under_high_security_a_hardware_key_places_it_and_the_hardware_keys_alone_cancel_it(client, owner, keys):
    on(client, owner, keys)
    named = ids(client, owner)
    user = me(client, owner)
    r = place(client, owner, word(client, owner, "account-delete", keys["a"]))
    assert r.status_code == 200, r.text
    d = r.json()["deletion"]
    assert sorted(d["cancel_by"]) == sorted([named["YubiKey A"], named["YubiKey B"]])   # not the phone, not the password
    assert json.loads(pending_row(user)["placed"])["tier"] == account_holds.HIGH
    r = cancel(client, owner, d["id"], word(client, owner, "account-delete-cancel:" + d["id"], keys["b"]))
    assert r.status_code == 200, r.text


def test_the_desktop_apps_own_account_is_refused_in_words_of_its_own(client):
    from proofpane_open import account_delete
    h = signed_up(client)
    user = me(client, h)
    with store.connect() as db:
        db.execute("UPDATE users SET password_hash = '' WHERE id = ?", (user,))
    r = place(client, h, {})
    assert r.status_code == 412, r.text
    assert r.json()["detail"] == account_delete.DESKTOP
    assert deletions(user) == [] and pending_row(user) is None


@pytest.mark.parametrize("change", [None, "sign-out-everywhere", "account-delete-cancel:" + "0" * 32])
def test_it_takes_a_confirmation_made_for_it(client, change):
    h = signed_up(client)
    user = me(client, h)
    r = place(client, h, {} if change is None else word(client, h, change))
    assert r.status_code == 403, r.text
    assert deletions(user) == [] and pending_row(user) is None


def test_a_second_deletion_is_refused_while_one_waits(client):
    h = signed_up(client)
    user = me(client, h)
    first = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    r = place(client, h, word(client, h, "account-delete"))
    assert r.status_code == 409, r.text
    assert [x["id"] for x in deletions(user)] == [first["id"]] and pending_row(user)["hold_id"] == first["id"]


def test_its_setting_moves_its_delay_within_its_bound(client, monkeypatch):
    from proofpane_open import account_delete
    monkeypatch.setenv(account_delete.SETTING, str(DAY))
    h = signed_up(client)
    assert client.get(DELETE, headers=h).json()["waits"] == DAY          # what the page says before it is placed
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    assert d["takes_effect_at"] - d["approved_at"] == DAY


def test_the_accounts_devices_are_told_it_waits_in_fixed_words(client, service):
    h = signed_up(client)
    device = subscribed(client, h)
    r = place(client, h, word(client, h, "account-delete")).json()
    at = holds.utc(r["deletion"]["takes_effect_at"])
    assert heard(device, service) == [("Deleting your account waits",
                                       f"Deleted at {at} unless you cancel it in Account.", "/app/#account")]
    assert r["told"] == 1 and r["deletion"]["told"] == 1        # kept on the hold, so every read of it says so


# 5. the page reads the deletion that waits, and how long one placed now would wait
def test_the_page_reads_the_deletion_that_waits(client):
    h = signed_up(client)
    assert client.get(DELETE, headers=h).json() == {"deletion": None, "waits": WEEK}
    placed = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    assert client.get(DELETE, headers=h).json() == {"deletion": placed, "waits": WEEK}
    other = signed_up(client, email="bo@example.com")
    assert client.get(DELETE, headers=other).json() == {"deletion": None, "waits": WEEK}


def test_a_read_at_its_time_finds_the_account_gone_and_answers_as_to_a_sign_in_that_ended(client, monkeypatch):
    from proofpane_open import account_delete
    monkeypatch.setenv(account_delete.SETTING, str(DAY))
    h = signed_up(client)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    later = d["takes_effect_at"] + 1
    monkeypatch.setattr(time, "time", lambda: later)            # the notifier has not settled it: the read does
    r = client.get(DELETE, headers=h)
    assert r.status_code == 401, r.text                         # not {"deletion": null}, which a page reads as cancelled
    assert client.get(DONE.format(d["id"])).json() == {"deleted_at": later}


def corrupt(user_id):
    """The waiting change's sets in a form account_holds never writes (a hand's edit, a later release's form)."""
    with store.connect() as db:
        db.execute("UPDATE account_pending SET placed = replace(placed, 'true', '1') WHERE user_id = ?", (user_id,))


def test_a_waiting_deletion_read_back_wrong_is_said_on_the_page_in_words(client):
    h = signed_up(client)
    user = me(client, h)
    place(client, h, word(client, h, "account-delete"))
    corrupt(user)
    r = client.get(DELETE, headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == ("A deletion of this account waits, but it could not be read back as it was written: "
                                  "ask this deployment's operator.")


def test_a_waiting_deletion_read_back_wrong_does_not_take_effect(client):
    h = signed_up(client)
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    corrupt(user)
    holds.settle(now=d["takes_effect_at"])
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user,)).fetchone()[0] == 1
    (hold,) = deletions(user)
    assert hold["failed"] == ("The deletion's waiting change could not be read back as it was written, so it did not "
                              "take effect.")


def test_the_deletions_own_round_applies_one_due_with_nothing_else_running(client):
    from proofpane_open import account_delete
    h = signed_up(client)
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    account_delete.round_once(now=d["takes_effect_at"] - 1)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user,)).fetchone()[0] == 1
    account_delete.round_once(now=d["takes_effect_at"])          # the notifier off, the account never back
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user,)).fetchone()[0] == 0


# 6. cancelling it: a credential that counted when it was placed, with a confirmation made for that cancel
def test_a_counting_credential_cancels_it_with_a_confirmation_made_for_that_cancel(client):
    h = signed_up(client)
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    r = cancel(client, h, d["id"], word(client, h, "account-delete-cancel:" + d["id"]))
    assert r.status_code == 200, r.text
    assert r.json()["cancelled_at"] is not None and pending_row(user) is None
    holds.settle(now=d["takes_effect_at"] + 1)
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.get(DELETE, headers=h).json()["deletion"] is None
    assert client.get(DONE.format(d["id"])).status_code == 404


@pytest.mark.parametrize("given", [None, "account-delete", "account-delete-cancel:" + "f" * 32, "sign-out-everywhere"])
def test_the_sign_in_alone_or_a_confirmation_made_for_anything_else_cancels_nothing(client, given):
    h = signed_up(client)
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    r = cancel(client, h, d["id"], {} if given is None else word(client, h, given))
    assert r.status_code == 403, r.text
    (hold,) = deletions(user)
    assert hold["cancelled_at"] is None and pending_row(user)["hold_id"] == d["id"]


def test_a_passkey_added_while_it_waits_cannot_cancel_it_and_the_password_that_counted_can(client):
    from proofpane_open import account_delete
    h = signed_up(client)
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    device = Authenticator()
    assert add(client, h, device).status_code == 200
    r = cancel(client, h, d["id"], word(client, h, "account-delete-cancel:" + d["id"], device))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == account_delete.NOT_A_CANCELLER
    assert pending_row(user)["hold_id"] == d["id"]
    r = cancel(client, h, d["id"], word(client, h, "account-delete-cancel:" + d["id"]))
    assert r.status_code == 200, r.text


def test_a_deletion_whose_waiting_change_is_gone_is_neither_cancelled_nor_takes_effect(client):
    h = signed_up(client)
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    with store.connect() as db:                                 # its waiting change removed by hand, its hold left
        db.execute("DELETE FROM account_pending WHERE user_id = ?", (user,))
    r = cancel(client, h, d["id"], word(client, h, "account-delete-cancel:" + d["id"]))
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "The deletion no longer waits."
    holds.settle(now=d["takes_effect_at"])
    assert client.get("/api/auth/me", headers=h).status_code == 200
    (hold,) = deletions(user)
    assert (hold["done_at"], hold["failed"]) == (d["takes_effect_at"], "The deletion no longer waits.")
    assert client.get(DONE.format(d["id"])).status_code == 404


def test_signing_out_everywhere_or_removing_a_passkey_leaves_it_waiting(client):
    h = signed_up(client)
    user = me(client, h)
    device = Authenticator()
    assert add(client, h, device).status_code == 200
    (key,) = client.get("/api/account/passkeys", headers=h).json()["passkeys"]
    d = place(client, h, word(client, h, "account-delete", device)).json()["deletion"]
    r = sign_out_everywhere(client, h)
    assert r.status_code == 200, r.text
    h = {"Authorization": "Bearer " + r.json()["token"]}
    assert remove(client, h, key["id"]).status_code == 200
    (hold,) = deletions(user)
    assert (hold["cancelled_at"], hold["done_at"]) == (None, None) and pending_row(user)["hold_id"] == d["id"]


# 7. at its time: the account is gone, and its devices are told
def test_at_its_time_the_account_is_gone_and_signs_in_no_more(client):
    h = signed_up(client)
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    holds.settle(now=d["takes_effect_at"] - 1)
    assert client.get("/api/auth/me", headers=h).status_code == 200
    holds.settle(now=d["takes_effect_at"])
    assert client.get("/api/auth/me", headers=h).status_code == 401
    r = client.post("/api/auth/login", json={"email": ADMIN, "password": "correct horse battery"})
    assert r.status_code != 200 and "token" not in r.json()
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user,)).fetchone()[0] == 0


def test_the_devices_it_had_are_told_it_is_done_once_it_has_committed(client, service):
    h = signed_up(client)
    user = me(client, h)
    device = subscribed(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    service.posts.clear()
    there = []
    sent = push._default_post

    def post_after_commit(*args, **kwargs):     # what another connection reads as the notice is sent
        with store.connect() as db:
            there.append(db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user,)).fetchone()[0])
        return sent(*args, **kwargs)
    push._default_post = post_after_commit
    try:
        holds.settle(now=d["takes_effect_at"])
    finally:
        push._default_post = sent
    assert there == [0]                         # sent once the deletion had committed, never inside it
    at = holds.utc(d["takes_effect_at"])
    assert heard(device, service) == [("Your account was deleted",
                                       f"Deleted at {at}. Signing in with it no longer works.", "/app/")]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_push_subscriptions WHERE user_id = ?", (user,)).fetchone()[0] == 0


# 8. the device it was placed from reads that it is done, by the hold's id alone
def test_the_device_reads_that_it_is_done_by_the_holds_id_alone(client):
    h = signed_up(client)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    assert client.get(DONE.format(d["id"])).status_code == 404               # it waits: nothing is said
    holds.settle(now=d["takes_effect_at"])
    r = client.get(DONE.format(d["id"]))
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted_at": d["takes_effect_at"]}
    with store.connect() as db:
        columns = [c[1] for c in db.execute("PRAGMA table_info(account_deletions)")]
        rows = [tuple(x) for x in db.execute("SELECT * FROM account_deletions")]
    assert columns == ["hold_id", "deleted_at"] and rows == [(d["id"], d["takes_effect_at"])]
    for other in ("0" * 32, "not-a-hold"):
        assert client.get(DONE.format(other)).status_code == 404


# 9. the operator's command: the same hold, the out-of-band check typed and kept with it, never at once
CHECK = "Asked by email from the address on file on 2026-11-22; confirmed by a reply to that address."


def test_the_operator_places_the_same_hold_its_typed_check_kept_with_it(client, capsys):
    h = signed_up(client, email="ana@example.com")
    user = me(client, h)
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK, "--untold"]) == 0
    said = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    (hold,) = deletions(user)
    assert said == {"hold": hold["id"], "takes_effect_at": hold["takes_effect_at"], "told": 0}    # no device to tell
    seen = client.get(DELETE, headers=h).json()["deletion"]
    assert (seen["made_with"], seen["checked"]) == ("admin", CHECK)    # the owner reads the check it was placed on
    assert hold["takes_effect_at"] - hold["approved_at"] == WEEK and CHECK in hold["summary"]
    row = pending_row(user)
    assert row["maker"] == account_holds.ADMIN and json.loads(row["body"]) == {"checked": CHECK}
    holds.settle(now=hold["takes_effect_at"] - 1)
    assert client.get("/api/auth/me", headers=h).status_code == 200          # never at once
    r = cancel(client, h, hold["id"], word(client, h, "account-delete-cancel:" + hold["id"]))
    assert r.status_code == 200, r.text                                       # the owner's to cancel


def test_the_operators_command_tells_the_accounts_devices_before_it_ends(client, monkeypatch):
    played = PushService()

    def slow(*args, **kwargs):              # a push service that takes its time: the command waits for it all the same
        time.sleep(0.5)
        return played(*args, **kwargs)
    monkeypatch.setattr(push, "_default_post", slow)            # from a thread of its own, which the command waits for
    h = signed_up(client, email="ana@example.com")
    device = subscribed(client, h)
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK]) == 0
    (hold,) = deletions(me(client, h))
    assert heard(device, played) == [("Deleting your account waits", f"Deleted at {holds.utc(hold['takes_effect_at'])} "
                                      "unless you cancel it in Account.", "/app/#account")]


@pytest.mark.parametrize("argv,said", [
    (["delete-account", "ana@example.com", "--checked", "   "], "check"),
    (["delete-account", "nobody@example.com", "--checked", CHECK], "No such account"),
    (["delete-account", "not an address", "--checked", CHECK], "Nothing was placed"),
    (["delete-account", "ana@example.com", "--checked", "asked\u2028by email"], "printable characters"),
    (["delete-account", "ana@example.com", "--checked", "asked \u202eliame yb"], "printable characters"),
    (["delete-account", "ana@example.com", "--checked", "asked\u00a0by email"], "no non-breaking space"),
    (["delete-account", "ana@example.com", "--checked", "asked\u200bby email"], "no zero-width space"),
])
def test_the_operators_command_refuses_without_a_check_or_an_account(client, capsys, argv, said):
    h = signed_up(client, email="ana@example.com")
    assert command.main(argv) == 1
    assert said in capsys.readouterr().err
    assert deletions(me(client, h)) == []


def test_the_operators_command_takes_its_check_or_does_not_run(client, capsys):
    h = signed_up(client, email="ana@example.com")
    with pytest.raises(SystemExit) as stopped:
        command.main(["delete-account", "ana@example.com"])
    assert stopped.value.code == 2 and "--checked" in capsys.readouterr().err        # argparse: a required option
    assert deletions(me(client, h)) == []


def test_the_operators_command_applies_no_deletion_itself_and_says_one_is_due(client, capsys, monkeypatch):
    from proofpane_open import account_delete
    monkeypatch.setenv(account_delete.SETTING, str(DAY))
    h = signed_up(client, email="ana@example.com")
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    later = d["takes_effect_at"] + 1
    monkeypatch.setattr(time, "time", lambda: later)
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK]) == 1
    assert f"is due since {holds.utc(d['takes_effect_at'])}" in capsys.readouterr().err
    with store.connect() as db:                                 # the server's round applies it, not the command
        assert db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user,)).fetchone()[0] == 1


def test_the_operator_places_none_that_nothing_could_cancel_or_while_one_waits(client, capsys):
    h = signed_up(client, email="ana@example.com")
    user = me(client, h)
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK, "--untold"]) == 0
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK, "--untold"]) == 1      # one waits
    assert len(deletions(user)) == 1
    bo = signed_up(client, email="bo@example.com")
    with store.connect() as db:                                     # an account with nothing to cancel with
        db.execute("UPDATE users SET password_hash = '' WHERE id = ?", (me(client, bo),))
    assert command.main(["delete-account", "bo@example.com", "--checked", CHECK, "--untold"]) == 1
    assert deletions(me(client, bo)) == []


def test_the_operator_places_none_that_no_device_would_hear_unless_told_to(client, capsys):
    h = signed_up(client, email="ana@example.com")
    user = me(client, h)
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK]) == 1
    assert "No device of the account has notifications on" in capsys.readouterr().err
    assert deletions(user) == []
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK, "--untold"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out.strip().splitlines()[-1])["told"] == 0 and "told no device" in err
    assert len(deletions(user)) == 1


def test_the_operators_command_says_a_busy_store_in_one_line(client, capsys, monkeypatch):
    import sqlite3
    from proofpane_open import account_delete
    signed_up(client, email="ana@example.com")

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(account_delete, "place_by_operator", busy)
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert err == ["Nothing was placed: the store was busy; run it again."]


def test_the_operators_command_says_another_store_fault_as_one(client, capsys, monkeypatch):
    import sqlite3
    from proofpane_open import account_delete
    signed_up(client, email="ana@example.com")

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(account_delete, "place_by_operator", broken)
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK]) == 1
    assert capsys.readouterr().err.strip().splitlines() == [
        "Nothing was placed: the store could not be written (unable to open database file)."]


def test_the_operators_command_says_a_broken_store_as_one_line(client, capsys, monkeypatch):
    import sqlite3
    from proofpane_open import account_delete
    signed_up(client, email="ana@example.com")

    def broken(*args, **kwargs):
        raise sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(account_delete, "place_by_operator", broken)
    assert command.main(["delete-account", "ana@example.com", "--checked", CHECK]) == 1
    assert capsys.readouterr().err.strip().splitlines() == [
        "Nothing was placed: the store could not be written (file is not a database)."]
