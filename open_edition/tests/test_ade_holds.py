"""Approvals that wait (ade/holds.py), with a kind of the tests' own: a hold takes effect only at its time, by the
server's clock, and only once; its delay comes from its kind's setting; cancelling before its time keeps it from ever
taking effect, and after it is too late, unless settling could not reach it; its notice goes at once to the account's
devices, saying when it takes effect, made from a read that waits on no writer, and how many devices it went toward is
kept after; one hold that cannot be applied keeps neither the others nor the read that settles them; and one whose
apply breaks ends failed, what it held released, never tried again and again."""

import sqlite3
import time

import pytest

from conftest import signed_up
from proofpane_open import config, store
from proofpane_open.ade import holds, push
from test_ade_push import FCM, Device, PushService
from test_passkeys import Authenticator, add, remove, sign_out_everywhere

NOW = 1_790_000_000
KIND = "test.thing"


@pytest.fixture
def kind(monkeypatch):
    """A kind that records what it was asked to do; its hold's subject is a key of `done`, and a subject named in
    `refuse` cannot take effect, `crash` raises (having written first, which must not be kept); `after` gives a
    subject's calls for once its transaction commits; `released` records what a broken hold released, with a row of
    its own, and a subject in `release_crash` raises there too."""
    seen = {"applied": [], "cancelled": [], "released": [], "refuse": set(), "crash": set(), "release_crash": set(),
            "after": {}}

    def apply(db, hold, now):
        hold["after"].extend(seen["after"].get(hold["subject"], ()))
        if hold["subject"] in seen["crash"]:
            db.execute("INSERT INTO ade_push_seen (user_id, key, sent_at) VALUES (?,?,?)",
                       (hold["user_id"], "half done:" + hold["subject"], now))
            raise RuntimeError("a bug, with words of what it carries")
        if hold["subject"].startswith("cancels:"):          # as a cancel would land while settle works
            db.execute("UPDATE ade_holds SET cancelled_at = ? WHERE subject = ?", (now, hold["subject"][8:]))
        if hold["subject"] in seen["refuse"]:
            return "It could not be done."
        db.execute("INSERT INTO ade_push_seen (user_id, key, sent_at) VALUES (?,?,?)",     # a write, in its transaction
                   (hold["user_id"], "applied:" + hold["subject"], now))
        seen["applied"].append((hold["subject"], now))
        return None

    def cancelled(db, hold, now):
        seen["cancelled"].append((hold["subject"], now))

    def released(db, hold, now):
        db.execute("INSERT INTO ade_push_seen (user_id, key, sent_at) VALUES (?,?,?)",
                   (hold["user_id"], "released:" + hold["subject"], now))
        if hold["subject"] in seen["release_crash"]:
            raise RuntimeError("the release broke too")
        seen["released"].append((hold["subject"], now))

    monkeypatch.setattr(holds, "_KINDS", dict(holds._KINDS))
    # an approval that names no passkey, as a request to link's: a lost device's steps cancel its holds
    holds.register(KIND, apply, cancelled, setting="PP_TEST_HOLD", default=120, unsigned=True, released=released)
    return seen


def held(subject, user="u1", at=NOW, **kw):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return holds.place(db, user, KIND, subject, f"{subject} was approved", at, **kw)


def applied_rows():
    with store.connect() as db:
        return sorted(r[0] for r in db.execute("SELECT key FROM ade_push_seen"))


def test_a_hold_takes_effect_at_its_time_and_once(kind):
    h = held("a")
    assert h["takes_effect_at"] == NOW + 120 and h["done_at"] is None and h["cancelled_at"] is None
    assert holds.settle(now=NOW + 119) == [] and kind["applied"] == []
    assert holds.running("u1", now=NOW + 119) == [h]
    assert holds.settle(now=NOW + 120) == [h["id"]]
    assert kind["applied"] == [("a", NOW + 120)] and applied_rows() == ["applied:a"]
    assert holds.settle(now=NOW + 500) == [] and len(kind["applied"]) == 1           # once
    assert holds.running("u1", now=NOW + 500) == []
    with store.connect() as db:
        assert holds.get(db, h["id"])["done_at"] == NOW + 120


def test_settle_keeps_to_the_account_it_is_given(kind):
    mine, theirs = held("a", user="u1"), held("b", user="u2")
    assert holds.settle("u1", now=NOW + 120) == [mine["id"]]
    assert [s for s, _ in kind["applied"]] == ["a"]
    assert holds.settle(now=NOW + 120) == [theirs["id"]]


def test_the_delay_is_the_kind_s_setting_when_it_is_whole_seconds_up_to_a_day(kind, monkeypatch):
    for value, expected in (("0", 0), ("30", 30), (" 600 ", 600), ("86400", 86400), ("86401", 120), ("-5", 120),
                            ("1.5", 120), ("3m", 120), ("", 120), (chr(0x0663), 120)):         # an Arabic-Indic three
        monkeypatch.setenv("PP_TEST_HOLD", value)
        assert holds.delay(KIND) == expected, value
    monkeypatch.delenv("PP_TEST_HOLD")
    assert holds.delay(KIND) == 120
    monkeypatch.setenv("PP_TEST_HOLD", "45")
    assert held("x")["takes_effect_at"] == NOW + 45          # read at each approval


def test_a_hold_cancelled_before_its_time_never_takes_effect(kind):
    h = held("a")
    got = holds.cancel("u1", h["id"], now=NOW + 119)
    assert got["cancelled_at"] == NOW + 119 and kind["cancelled"] == [("a", NOW + 119)]
    assert holds.settle(now=NOW + 10_000) == [] and kind["applied"] == [] and applied_rows() == []
    assert holds.cancel("u1", h["id"], now=NOW + 200) == got and len(kind["cancelled"]) == 1     # as the first
    assert holds.running("u1", now=NOW) == []


def test_at_its_time_a_hold_has_taken_effect_and_is_past_cancelling(kind):
    h = held("a")
    with pytest.raises(holds.HoldError) as late:
        holds.cancel("u1", h["id"], now=NOW + 120)            # nothing settled it yet: cancel settles first
    assert late.value.status == 409 and "it has taken effect" in str(late.value)
    assert kind["applied"] == [("a", NOW + 120)] and kind["cancelled"] == []


def test_a_hold_is_cancelled_by_its_own_account_alone(kind):
    h = held("a")
    for who, hold_id in (("u2", h["id"]), ("u1", "no-such-hold")):
        with pytest.raises(holds.HoldError) as refused:
            holds.cancel(who, hold_id, now=NOW)
        assert refused.value.status == 404
    assert kind["cancelled"] == []


def test_one_that_cannot_take_effect_keeps_why_and_one_that_breaks_keeps_neither_the_others_nor_the_read(kind):
    kind["refuse"].add("no")
    kind["crash"].add("bug")
    no, bug, fine = held("no"), held("bug", at=NOW + 1), held("fine", at=NOW + 2)
    assert holds.settle(now=NOW + 200) == [no["id"], bug["id"], fine["id"]]
    with store.connect() as db:
        assert holds.get(db, no["id"])["failed"] == "It could not be done."
        assert holds.get(db, bug["id"])["failed"] == holds.BROKE
    with pytest.raises(holds.HoldError) as late:
        holds.cancel("u1", no["id"], now=NOW + 300)
    assert "it could not take effect (It could not be done)" in str(late.value)
    assert [s for s, _ in kind["applied"]] == ["fine"]


def test_a_hold_whose_apply_breaks_ends_failed_releases_what_it_held_and_is_never_tried_again(kind, capsys):
    """An apply that raises would otherwise be tried at every settle, never ending, and, past its time, not cancelled
    either: whatever it held (the one change of encryption that may wait, say) held for good."""
    kind["crash"].add("bug")
    bug = held("bug")
    assert holds.settle(now=NOW + 120) == [bug["id"]]
    with store.connect() as db:
        ended = holds.get(db, bug["id"])
    assert (ended["done_at"], ended["failed"], ended["cancelled_at"]) == (NOW + 120, holds.BROKE, None)
    assert kind["released"] == [("bug", NOW + 120)]
    assert applied_rows() == ["released:bug"]                       # what the apply wrote before it broke: not kept
    err = capsys.readouterr().err
    assert bug["id"] in err and KIND in err and "RuntimeError" in err and "what it carries" not in err
    kind["crash"].clear()
    assert holds.settle(now=NOW + 500) == [] and kind["applied"] == []
    with pytest.raises(holds.HoldError) as late:
        holds.cancel("u1", bug["id"], now=NOW + 500)
    assert late.value.status == 409 and str(late.value) == (
        f"Its time came at {holds.utc(NOW + 120)}: it could not take effect "
        "(The server failed as it applied it, so it never took effect: make it again).")
    assert holds.running("u1", now=NOW + 500) == []


def test_a_release_that_breaks_too_still_ends_the_hold(kind):
    kind["crash"].add("bug")
    kind["release_crash"].add("bug")
    bug = held("bug")
    assert holds.settle(now=NOW + 120) == [bug["id"]]
    with store.connect() as db:
        assert holds.get(db, bug["id"])["failed"] == holds.BROKE
    assert kind["released"] == [] and applied_rows() == []            # the release's own write undone with it


@pytest.mark.parametrize("meanwhile", ["cancelled_at", "done_at"])
def test_a_broken_hold_another_request_ended_meanwhile_is_left_as_that_ended_it(kind, monkeypatch, meanwhile):
    """Between the apply that broke and the hold's ending failed, in a transaction of its own, a cancel or another
    settle may end the hold: it is left as that ended it, and what it held is not released again."""
    kind["crash"].add("bug")
    bug = held("bug")
    real = holds._broke

    def landing(hold_id, now):
        with store.connect() as db:
            db.execute(f"UPDATE ade_holds SET {meanwhile} = ? WHERE id = ?", (NOW + 100, hold_id))
        return real(hold_id, now)

    monkeypatch.setattr(holds, "_broke", landing)
    assert holds.settle(now=NOW + 120) == []
    with store.connect() as db:
        got = holds.get(db, bug["id"])
    assert (got[meanwhile], got["failed"], kind["released"]) == (NOW + 100, None, [])
    assert got["cancelled_at" if meanwhile == "done_at" else "done_at"] is None


def test_a_hold_the_store_kept_from_its_apply_is_left_for_a_later_settle(kind, monkeypatch):
    """What breaks before the kind's apply runs (the store busy) is no break of the action: the hold is tried again."""
    h = held("a")
    real, refused = store.connect, []

    def connect():
        import contextlib

        @contextlib.contextmanager
        def busy():
            with real() as db:
                class Busy:
                    def execute(self, sql, *args):
                        if sql.startswith("BEGIN IMMEDIATE") and not refused:
                            refused.append(sql)
                            raise sqlite3.OperationalError("database is locked")
                        return db.execute(sql, *args)
                yield Busy()
        return busy()

    monkeypatch.setattr(store, "connect", connect)
    assert holds.settle(now=NOW + 120) == [] and refused
    monkeypatch.setattr(store, "connect", real)
    with store.connect() as db:
        assert holds.get(db, h["id"])["done_at"] is None
    assert holds.settle(now=NOW + 121) == [h["id"]] and kind["applied"] == [("a", NOW + 121)]


def test_a_hold_past_its_time_that_settling_could_not_reach_is_cancelled(kind, monkeypatch):
    """A cancel is never refused because a settle failed: one past its time and not ended is cancelled, and never takes
    effect."""
    h = held("a")
    real = holds.settle
    monkeypatch.setattr(holds, "settle", lambda user_id=None, now=None: [])           # as when the store was busy
    got = holds.cancel("u1", h["id"], now=NOW + 300)
    assert got["cancelled_at"] == NOW + 300 and kind["cancelled"] == [("a", NOW + 300)]
    monkeypatch.setattr(holds, "settle", real)
    assert holds.settle(now=NOW + 400) == [] and kind["applied"] == []
    assert holds.cancel("u1", h["id"], now=NOW + 500) == got                         # a second cancel, as the first


def done_at_elsewhere(hold_id):
    """A hold's done_at as another connection reads it: set there only once the transaction that set it committed."""
    with store.connect() as db:
        return db.execute("SELECT done_at FROM ade_holds WHERE id = ?", (hold_id,)).fetchone()[0]


def test_what_an_action_leaves_for_after_runs_once_its_transaction_has_committed(kind):
    """Its calls for after (deleting files, say) run once the hold's transaction has committed, each on its own, so one
    that fails keeps neither the next nor the settle; a hold whose apply broke runs none: it ends failed."""
    a, b, bug = held("a"), held("b", at=NOW + 1), held("bug", at=NOW + 2)
    ran = []

    def broken():
        raise OSError("the disk is full")

    kind["after"] = {"a": [lambda: ran.append(("a", done_at_elsewhere(a["id"]))), broken, lambda: ran.append("a2")],
                     "b": [lambda: ran.append(("b", done_at_elsewhere(b["id"])))],
                     "bug": [lambda: ran.append("bug")]}
    kind["crash"].add("bug")
    assert holds.settle(now=NOW + 500) == [a["id"], b["id"], bug["id"]]
    assert ran == [("a", NOW + 500), "a2", ("b", NOW + 500)]
    assert done_at_elsewhere(bug["id"]) == NOW + 500


def test_a_hold_ended_before_its_time_never_takes_effect_and_says_when_it_ended(kind):
    gone, cancelled, fine = held("gone"), held("cancelled"), held("fine")
    holds.cancel("u1", cancelled["id"], now=NOW + 5)
    with store.connect() as db:
        holds.end(db, [gone["id"], cancelled["id"]], "Its key was removed.", NOW + 10)
    with store.connect() as db:
        ended, left = holds.get(db, gone["id"]), holds.get(db, cancelled["id"])
    assert (ended["done_at"], ended["failed"], ended["cancelled_at"]) == (NOW + 10, "Its key was removed.", None)
    assert (left["cancelled_at"], left["done_at"], left["failed"]) == (NOW + 5, None, None)        # a cancel stands
    assert [h["id"] for h in holds.running("u1", now=NOW + 10)] == [fine["id"]]
    assert holds.settle(now=NOW + 500) == [fine["id"]] and [s for s, _ in kind["applied"]] == ["fine"]
    with pytest.raises(holds.HoldError) as late:
        holds.cancel("u1", gone["id"], now=NOW + 20)
    assert late.value.status == 409 and str(late.value) == (
        f"It ended at {holds.utc(NOW + 10)}, before its time: it could not take effect (Its key was removed).")
    assert kind["cancelled"] == [("cancelled", NOW + 5)]


def test_settling_checks_each_hold_again_in_its_own_transaction(kind):
    first, then = held("cancels:b"), held("b", at=NOW + 1)
    assert holds.settle(now=NOW + 500) == [first["id"]]       # b was due when settle began, and cancelled before its turn
    assert [s for s, _ in kind["applied"]] == ["cancels:b"]
    with store.connect() as db:
        assert holds.get(db, then["id"])["done_at"] is None


def test_a_hold_of_a_kind_this_process_does_not_know_is_left_alone(kind, monkeypatch):
    h = held("a")
    monkeypatch.setattr(holds, "_KINDS", {})
    assert holds.settle(now=NOW + 500) == []
    with store.connect() as db:
        assert holds.get(db, h["id"])["done_at"] is None


def test_the_notice_says_in_fixed_words_that_an_approval_waits_and_when_at_once_to_each_device(client, kind,
                                                                                                 monkeypatch):
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    phone, laptop = Device(), Device()
    push.subscribe(user_id, FCM, phone.p256dh, push.b64u(phone.auth))
    push.subscribe(user_id, FCM + "-2", laptop.p256dh, push.b64u(laptop.auth))
    service = PushService()
    spawned = []
    monkeypatch.setattr(push, "_spawn", lambda work: (spawned.append(work), work()))
    h = held("a", user=user_id)
    assert holds.tell(h, now=NOW, post=service) == 2 and len(spawned) == 1        # sent apart from the request
    for device, (_, _, body) in zip((phone, laptop), service.posts):
        # what was approved (its summary) is the page's; the notice says an approval waits, and when
        assert device.decrypt(body) == {"title": "An approval waits",
                                        "body": "Takes effect at 2026-09-21 14:15:20 UTC unless you cancel it.",
                                        "tag": push.words("held-other", key="hold:" + h["id"], at=0).tag,
                                        "url": "/app/"}
    with store.connect() as db:
        assert holds.get(db, h["id"])["told"] == 2                             # the page says how many heard


def test_the_notice_goes_before_its_count_is_kept_and_its_send_waits_on_nothing(client, kind, monkeypatch):
    """The notice is made from a read and sent with no transaction open, so the send's own write of when it was sent
    waits on no lock; how many devices it went toward is kept after, and another connection reads none while it goes.
    Sent inside a transaction, as an older release sent it, a send run at once waited out the 10-second busy timeout and
    failed, and when the device was sent to was never kept."""
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    phone, service = Device(), PushService()
    push.subscribe(user_id, FCM, phone.p256dh, push.b64u(phone.auth))
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    h, read = held("a", user=user_id), []

    def post(url, headers, body):
        with store.connect() as db:                         # what another connection sees as the notice goes
            read.append(holds.get(db, h["id"])["told"])
        return service(url, headers, body)

    started = time.monotonic()
    assert holds.tell(h, now=NOW, post=post) == 1
    assert time.monotonic() - started < 5                   # no wait on the store's lock
    assert read == [None] and len(service.posts) == 1
    (sub,) = push.subscriptions(user_id)
    assert (sub["last_sent_at"], sub["failures"]) == (NOW, 0)
    with store.connect() as db:
        assert holds.get(db, h["id"])["told"] == 1


def test_a_store_another_connection_holds_for_writing_delays_the_notice_no_more_than_a_read(client, kind,
                                                                                            monkeypatch):
    """Another connection holds the store for writing as the approval commits (e2e_keys' compaction runs VACUUM; a
    busy writer holds it for a while): the notice is made from a read, which waits on no writer, and sent all the same.
    How many devices it went toward needs a write, so it is then not kept, and the page shows none. Before, the notice
    was made only once a write transaction had begun, so a store held past the busy timeout lost it, with nothing kept
    and nothing to tell of it again."""
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    phone, service = Device(), PushService()
    push.subscribe(user_id, FCM, phone.p256dh, push.b64u(phone.auth))
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    h = held("a", user=user_id)
    real = sqlite3.connect
    # a busy timeout of a moment rather than 10 seconds, so the test does not wait it out
    monkeypatch.setattr(sqlite3, "connect", lambda path, timeout=10, **kw: real(path, timeout=0.2, **kw))
    writer = real(config.db_path(), timeout=0.2)
    writer.execute("BEGIN IMMEDIATE")
    try:
        assert holds.tell(h, now=NOW, post=service) == 1
    finally:
        writer.rollback()
        writer.close()
    assert [phone.decrypt(body)["title"] for _, _, body in service.posts] == ["An approval waits"]
    with store.connect() as db:
        assert holds.get(db, h["id"])["told"] is None


def test_a_notice_whose_thread_cannot_start_is_kept_as_not_told_and_fails_nothing_around_it(client, kind,
                                                                                            monkeypatch):
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    phone, service = Device(), PushService()
    push.subscribe(user_id, FCM, phone.p256dh, push.b64u(phone.auth))

    def broken(work):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(push, "_spawn", broken)
    h = held("a", user=user_id)
    assert holds.tell(h, now=NOW, post=service) == -1       # never "sent toward 1 device" when none was sent
    with store.connect() as db:
        assert holds.get(db, h["id"])["told"] == -1
    # another sender's notice, as a route's after the change it tells of: none, and nothing raised
    assert push.notice(user_id, push.words("passkey-added"), NOW, service) is None
    assert service.posts == []


def test_a_kind_names_the_words_of_its_notice_or_makes_them_from_the_hold(client, kind, monkeypatch):
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    phone, service = Device(), PushService()
    push.subscribe(user_id, FCM, phone.p256dh, push.b64u(phone.auth))
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    holds._KINDS[KIND] = dict(holds._KINDS[KIND], notice="held-move")
    assert holds.tell(held("a", user=user_id), now=NOW, post=service) == 1
    asked = []

    def named(db, row, at_once):
        asked.append((row["subject"], at_once))
        return "held-e2e-code"
    holds._KINDS[KIND] = dict(holds._KINDS[KIND], notice=named)
    assert holds.tell(held("b", user=user_id), now=NOW, post=service) == 1
    monkeypatch.setenv("PP_TEST_HOLD", "0")
    holds._KINDS[KIND] = dict(holds._KINDS[KIND], notice="held-move")
    assert holds.tell(held("c", user=user_id), now=NOW, post=service) == 1
    told = [phone.decrypt(body) for _, _, body in service.posts]
    assert [(m["title"], m["url"]) for m in told] == [("A move waits", "/app/#channel"),
                                                      ("A change to encryption waits", "/app/#account"),
                                                      ("A move was signed", "/app/#channel")]
    assert told[1]["body"].endswith("Made with your recovery code: only the recovery code cancels it.")
    assert asked == [("b", False)]
    # words a kind names that are not a kind of notice send nothing, and the approval stands
    holds._KINDS[KIND] = dict(holds._KINDS[KIND], notice=lambda db, row, at_once: "a link named “mac”")
    d = held("d", user=user_id)
    assert holds.tell(d, now=NOW, post=service) == -1 and len(service.posts) == 3
    with store.connect() as db:
        assert holds.get(db, d["id"])["cancelled_at"] is None


def test_with_no_delay_the_notice_says_what_was_approved_to_take_effect_at_once_and_offers_no_cancel(client, kind,
                                                                                                    monkeypatch):
    """Told as it is placed, before it is applied (holds.settle), whose own checks may still refuse it: the words say
    it was approved, to take effect at once, never that it took effect."""
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    phone, service = Device(), PushService()
    push.subscribe(user_id, FCM, phone.p256dh, push.b64u(phone.auth))
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    monkeypatch.setenv("PP_TEST_HOLD", "0")
    kind["refuse"].add("a")
    h = held("a", user=user_id)
    assert h["takes_effect_at"] == NOW and holds.tell(h, now=NOW, post=service) == 1
    (told,) = [phone.decrypt(body) for _, _, body in service.posts]
    assert (told["title"], told["body"]) == ("An approval was given", "Approved at 2026-09-21 14:13:20 UTC, to take "
                                                                      "effect at once: this deployment holds no "
                                                                      "approval.")
    assert holds.settle(now=NOW) == [h["id"]] and kind["applied"] == []         # its apply refused it: words still true
    with store.connect() as db:
        assert holds.get(db, h["id"])["failed"] == "It could not be done."


def test_a_notice_that_cannot_go_never_undoes_the_approval_and_says_it_could_not(kind, monkeypatch):
    h = held("a")

    def broken(*args, **kwargs):
        raise OSError("the push services are down")

    monkeypatch.setattr(push, "notice", broken)
    assert holds.tell(h, now=NOW) == -1
    assert holds.running("u1", now=NOW) == [dict(h, told=-1)]


def test_devices_that_cannot_be_read_are_told_apart_from_none(kind, monkeypatch):
    """How many devices a notice was sent toward is kept on the hold for the page: -1 when the account's devices could
    not be read, which the page says, apart from 0, an account with no device that turned notifications on."""
    real, broken = push.subscriptions, [True]

    def subscriptions(user_id):
        if broken[0]:
            raise OSError("the store is locked")
        return real(user_id)

    monkeypatch.setattr(push, "subscriptions", subscriptions)
    a = held("a")
    assert holds.tell(a, now=NOW) == -1
    broken[0] = False
    b = held("b")
    assert holds.tell(b, now=NOW) == 0
    with store.connect() as db:
        assert (holds.get(db, a["id"])["told"], holds.get(db, b["id"])["told"]) == (-1, 0)


def watched_store(monkeypatch, refuse=None):
    """store.connect watched: a list of, for each connection opened, the BEGINs run on it. A statement starting with
    `refuse` raises, the first time, as a store that could not be read does."""
    import contextlib
    real, opened, refused = store.connect, [], []

    def connect():
        @contextlib.contextmanager
        def watched():
            begun = []
            opened.append(begun)
            with real() as db:
                class Watched:
                    def execute(self, sql, *args):
                        if sql.strip().upper().startswith("BEGIN"):
                            begun.append(sql)
                        if refuse and sql.startswith(refuse) and not refused:
                            refused.append(sql)
                            raise sqlite3.OperationalError("disk I/O error")
                        return db.execute(sql, *args)
                yield Watched()
        return watched()

    monkeypatch.setattr(store, "connect", connect)
    return opened


def test_the_hold_is_read_with_no_write_lock_and_its_count_kept_after_in_a_write_of_its_own(kind, monkeypatch):
    """The hold the notice is made from is read with no write transaction begun, so no writer delays the notice;
    how many devices it went toward is kept after, in a connection of its own."""
    h = held("a")
    monkeypatch.setattr(push, "notice", lambda *args, **kwargs: 3)
    opened = watched_store(monkeypatch)
    assert holds.tell(h, now=NOW) == 3
    assert opened == [[], []]
    with store.connect() as db:
        assert holds.get(db, h["id"])["told"] == 3


def test_a_hold_that_could_not_be_read_for_its_notice_keeps_no_count_rather_than_minus_one(kind, monkeypatch):
    """-1 says the account's devices could not be read; when the store could not be read as the notice was made,
    nothing was sent and nothing is said, rather than a -1 kept for good."""
    h = held("a")
    sent = []
    monkeypatch.setattr(push, "notice", lambda *args, **kwargs: sent.append(1) or 3)
    real = store.connect
    watched_store(monkeypatch, refuse="SELECT * FROM ade_holds")
    assert holds.tell(h, now=NOW) == -1 and sent == []
    monkeypatch.setattr(store, "connect", real)
    with store.connect() as db:
        assert holds.get(db, h["id"])["told"] is None


@pytest.mark.parametrize("step", ["removing a passkey", "signing out everywhere"])
def test_a_lost_device_s_steps_cancel_the_account_s_holds_whose_time_has_not_come(client, kind, step):
    """The manual's steps for a lost device each cancel every hold of the account still running whose approval names
    no passkey, so one made with a sign-in and a confirmation taken from that device never takes effect; one whose time
    has come has taken effect and stays so (or, its apply broken, has ended failed); a kind a passkey signs is left to
    its own rule; another account's holds are untouched."""
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    other_id = client.get("/api/auth/me", headers=signed_up(client, "other@example.com")).json()["id"]
    assert add(client, owner, Authenticator()).status_code == 200
    (passkey,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    holds.register("test.signed", lambda db, hold, now: None)              # one a passkey signs, as a move is
    now = int(time.time())
    kind["crash"].add("broken")
    due, broken = held("due", user=user_id, at=now - 200), held("broken", user=user_id, at=now - 200)
    running, theirs = held("running", user=user_id, at=now), held("theirs", user=other_id, at=now)
    with store.connect() as db:
        signed = holds.place(db, user_id, "test.signed", "s", "s was approved", now)
    r = remove(client, owner, passkey["id"]) if step == "removing a passkey" else sign_out_everywhere(client, owner)
    assert r.status_code == 200, r.text
    assert [s for s, _ in kind["applied"]] == ["due"] and [s for s, _ in kind["cancelled"]] == ["running"]
    with store.connect() as db:
        assert holds.get(db, running["id"])["cancelled_at"] is not None
        assert holds.get(db, theirs["id"])["cancelled_at"] is None and holds.get(db, due["id"])["done_at"] is not None
        assert holds.get(db, broken["id"])["cancelled_at"] is None and holds.get(db, broken["id"])["failed"] == \
            holds.BROKE
        assert holds.get(db, signed["id"])["cancelled_at"] is None


def test_the_routes_list_and_cancel_the_account_s_own_holds(client, kind):
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    other = signed_up(client, "other@example.com")
    h = held("a", user=user_id, at=int(time.time()))
    assert [x["id"] for x in client.get("/api/ade/holds", headers=owner).json()["holds"]] == [h["id"]]
    assert client.get("/api/ade/holds", headers=other).json()["holds"] == []
    assert client.post(f"/api/ade/holds/{h['id']}/cancel", headers=other).status_code == 404
    assert client.post(f"/api/ade/holds/{h['id']}/cancel").status_code == 401           # the sign-in, and only it
    r = client.post(f"/api/ade/holds/{h['id']}/cancel", headers=owner)
    assert r.status_code == 200 and r.json()["cancelled_at"] and kind["cancelled"]
    assert client.get("/api/ade/holds", headers=owner).json()["holds"] == []


def test_a_kind_with_a_guard_is_cancelled_only_through_it(client, kind):
    """A kind that asks more than the sign-in to cancel (register: guard), as a change to end-to-end encryption does:
    the route hands its guard the cancel's body; a refusal leaves the hold as it was, nothing of it cancelled; and a
    lost device's steps cancel no such hold, even one whose approval names no passkey."""
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    given, cancelled = [], []

    def guard(db, hold, body, now):
        given.append(body)
        if body.get("confirmation") != "right":
            raise holds.HoldError(403, "Not without the right word.")

    holds.register("test.guarded", lambda db, hold, now: None, lambda db, hold, now: cancelled.append(hold["id"]),
                   default=120, unsigned=True, guard=guard)
    with store.connect() as db:
        h = holds.place(db, user_id, "test.guarded", "g", "g was approved", int(time.time()))
    for body in (None, {"confirmation": "wrong"}):
        r = client.post(f"/api/ade/holds/{h['id']}/cancel", headers=owner, json=body)
        assert r.status_code == 403 and r.json()["detail"] == "Not without the right word.", r.text
    r = sign_out_everywhere(client, owner)
    assert r.status_code == 200, r.text
    owner = {"Authorization": "Bearer " + r.json()["token"]}
    assert [x["id"] for x in client.get("/api/ade/holds", headers=owner).json()["holds"]] == [h["id"]]
    assert cancelled == []
    r = client.post(f"/api/ade/holds/{h['id']}/cancel", headers=owner, json={"confirmation": "right"})
    assert r.status_code == 200 and r.json()["cancelled_at"], r.text
    assert cancelled == [h["id"]]
    assert given == [{}, {"confirmation": "wrong", "recovery_proof": None},
                     {"confirmation": "right", "recovery_proof": None}]


def test_the_notifier_s_round_applies_a_hold_whose_time_has_come_with_nothing_reading_it(kind):
    held("a")
    push.tick(now=NOW + 119, post=PushService(), build=lambda conn: {"alerts": []})
    assert kind["applied"] == []
    push.tick(now=NOW + 120, post=PushService(), build=lambda conn: {"alerts": []})
    assert kind["applied"] == [("a", NOW + 120)]
