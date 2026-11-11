"""The tasks' confirmation purposes and their notices' words.

The purposes. A change to an agent's task that the sign-in alone does not make takes a confirmation made for that one
change: passkeys.CHANGE names each as its kind and one id, 32 lowercase hex, so a confirmation asked for one account,
one offer, one custody request or one task is spent on nothing else. Each of the nine, with its id, is one of CHANGE's
forms; no other shape of it is (bare, nothing after its colon, an id of another length or case, a second id, a letter
added to its name, two of them run together, or a name beside one); every older form still is; and through
the app a confirmation made for one, with the password or with a passkey, is spent on that change once, and never on
another of the nine with the same id, nor on the same kind with another id.

The words: the relay's and the teammate's notices are content-free. Each of the fourteen new kinds has its own fixed
words in push.WORDS; takes a time where it tells of a hold, and no other fact; has no -now of its own, since its hold's
notice is a callable that gives held-other-now for a hold of no time; and, made with a key that holds a task's title,
goal and step, a backend, an account's email and a teammate's address, carries none of them, as a device reads it too:
its tag is a keyed digest of the key. Which kind each sender names, and when, is for the senders' own tests.
"""

import hashlib
import hmac
import itertools
import re
import secrets

import pytest

from conftest import signed_up
from proofpane_open import config, passkeys
from proofpane_open.ade import holds, push
from test_ade_push import FCM, Device, PushService
from test_passkeys import PASSWORD, Authenticator, add, confirmed

ID = "0123456789abcdef0123456789abcdef"
OTHER_ID = "fedcba9876543210fedcba9876543210"

# the nine: each takes one id, and task-secret-lift two, the task's and a digest of the findings it lifts
# (task_offers.lift_subject)
PURPOSES = ("task-account-own", "task-cross", "task-custody-cross", "task-offer", "task-custody", "task-custody-accept",
            "task-custody-confirm", "task-account-record", "task-secret-lift")
DIGEST = "abcdef0123456789" * 2


def form(purpose, i=ID):
    """A purpose's change for the id `i`: task-secret-lift's with the digest of its findings after it."""
    return f"{purpose}:{i}:{DIGEST}" if purpose == "task-secret-lift" else f"{purpose}:{i}"
# every form CHANGE took before the nine, with an id where it takes one
RELEASE_59 = ("passkey-add", f"passkey-remove:{ID}", "high-security-on", "high-security-off", "sign-out-everywhere",
              "link-ticket:new", f"link-ticket:{ID}", f"link-revoke:{ID}", f"link-approve:{ID}",
              f"link-approve:{ID}:{OTHER_ID}", "connector-on", f"connector-allow:{ID}:propose",
              f"connector-allow:{ID}:read", f"request-approve:{ID}", "signer-ticket", f"e2e-upgrade:{ID}")


def taken(change):
    """`change` is one of CHANGE's forms, whole, and change_subject gives it back as it is."""
    assert passkeys.CHANGE.fullmatch(change), change
    assert passkeys.change_subject(change) == change


def refused(change):
    """`change` is none of CHANGE's forms, and change_subject refuses it as the confirmation routes do: 422, in
    BAD_CHANGE's words."""
    assert not passkeys.CHANGE.fullmatch(change), change
    with pytest.raises(passkeys.PasskeyError) as no:
        passkeys.change_subject(change)
    assert no.value.status == 422 and str(no.value) == passkeys.BAD_CHANGE, change


def other_shapes(purpose):
    """Each shape of `purpose` that is not its form, by what is wrong with it (those the module's docstring lists, with
    an id one short, a newline after it, and its name's last hyphen left out or written as an underscore besides)."""
    last = purpose.rfind("-")
    tail = f":{DIGEST}" if purpose == "task-secret-lift" else ""        # its form's second id
    return {"bare": purpose, "nothing after its colon": f"{purpose}:", "a 12-hex id": f"{purpose}:{ID[:12]}{tail}",
            "a 31-hex id": f"{purpose}:{ID[:31]}{tail}", "a 33-hex id": f"{purpose}:{ID}0{tail}",
            "an uppercase id": f"{purpose}:{ID.upper()}{tail}", "an id more": f"{purpose}:{ID}{tail}:{OTHER_ID}",
            "a letter after its name": f"{purpose}x:{ID}{tail}", "a letter before its name": f"x{purpose}:{ID}{tail}",
            "a newline after it": f"{purpose}:{ID}{tail}\n",
            "a hyphen left out": f"{purpose[:last]}{purpose[last + 1:]}:{ID}{tail}",
            "a hyphen as an underscore": f"{purpose[:last]}_{purpose[last + 1:]}:{ID}{tail}"}


# ---- the purposes ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("purpose", PURPOSES)
def test_each_purpose_with_its_id_is_a_change_a_confirmation_is_made_for(purpose):
    taken(form(purpose))
    taken(form(purpose, secrets.token_hex(16)))        # an id made as the tracks make them


def test_a_lift_s_change_names_the_digest_of_the_findings_it_lifts():
    # made for the task alone, a confirmation for some findings once lifted
    # others; the task's id alone is no change now, nor a digest in another shape
    taken(f"task-secret-lift:{ID}:{DIGEST}")
    for change in (f"task-secret-lift:{ID}", f"task-secret-lift:{ID}:", f"task-secret-lift:{ID}:{DIGEST[:31]}",
                   f"task-secret-lift:{ID}:{DIGEST.upper()}", f"task-secret-lift:{ID}{DIGEST}"):
        refused(change)


@pytest.mark.parametrize("why", sorted(other_shapes("")))
@pytest.mark.parametrize("purpose", PURPOSES)
def test_no_other_shape_of_a_purpose_is_one(purpose, why):
    taken(form(purpose))                                # the control: its own form is one
    refused(other_shapes(purpose)[why])


@pytest.mark.parametrize("first,second", list(itertools.product(PURPOSES, repeat=2)))
def test_two_purposes_run_together_are_none(first, second):
    taken(form(first))
    taken(form(second, OTHER_ID))
    refused(form(first) + form(second, OTHER_ID))


def test_names_beside_the_purposes_are_none():
    """task-account and task-custody- begin some of the nine; the holds' kinds (task.offer, task.account_own,
    task.crossing, task.cross) and the first spelling of the purposes (task.custody_accept) are names a route could
    pass by mistake, and a regular expression with an unescaped dot would take them."""
    for purpose in ("task-account-own", "task-account-record", "task-cross", "task-custody", "task-custody-cross",
                    "task-custody-accept", "task-custody-confirm", "task-offer", "task-secret-lift"):
        taken(form(purpose))
    for change in (f"task-account:{ID}", f"task-custody-{ID}", f"task:{ID}", f"task-:{ID}", f"task.offer:{ID}",
                   f"task.account_own:{ID}", f"task.crossing:{ID}", f"task.cross:{ID}", f"task-custody_accept:{ID}",
                   f"task_offer:{ID}"):
        refused(change)


@pytest.mark.parametrize("change", RELEASE_59)
def test_every_older_form_is_still_one(change):
    for purpose in PURPOSES:                            # the control: this CHANGE has the nine purposes beside them
        assert passkeys.CHANGE.fullmatch(form(purpose)), purpose
    taken(change)


@pytest.mark.parametrize("by", ("password", "passkey"))
@pytest.mark.parametrize("purpose", PURPOSES)
def test_a_confirmation_made_for_a_purpose_is_spent_on_that_change_alone(client, purpose, by):
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    device = None
    if by == "passkey":
        device = Authenticator()
        assert add(client, owner, device).status_code == 200
    change = form(purpose)
    # the page asks for one by naming its change: a shape that is not one is refused before anything is asked, and
    # the change is taken (an account with no passkey has none to offer, 409)
    for path, more in (("/api/account/confirm/options", {"purpose": "confirm"}),
                       ("/api/account/confirm", {"password": PASSWORD})):
        r = client.post(path, headers=owner, json=dict(more, change=f"{purpose}:"))
        assert r.status_code == 422 and r.json()["detail"] == passkeys.BAD_CHANGE, path
    r = client.post("/api/account/confirm/options", headers=owner, json={"purpose": "confirm", "change": change})
    assert r.status_code == (409 if device is None else 200), r.text
    word = confirmed(client, owner, device, change=change)["confirmation"]
    # given for another change, it is refused and left unused: another of the nine with the same id, and this one
    # with another id, as the new changes' routes will spend it (passkeys.use_confirmation); and an older change,
    # through its own route
    others = [form(p) for p in PURPOSES if p != purpose] + [form(purpose, OTHER_ID)]
    if purpose == "task-secret-lift":                   # the same task, other findings
        others.append(f"{purpose}:{ID}:{OTHER_ID}")
    for other in others:
        with pytest.raises(passkeys.PasskeyError) as no:
            passkeys.use_confirmation(user_id, word, other)
        assert no.value.status == 403 and str(no.value) == passkeys.CONFIRM_OTHER, other
    r = client.post("/api/account/sign-out-everywhere", headers=owner, json={"confirmation": word})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER
    assert client.get("/api/auth/me", headers=owner).status_code == 200      # nothing was signed out
    # on its own change it is spent, once
    made_by = None if device is None else client.get("/api/account/passkeys", headers=owner).json()["passkeys"][0]["id"]
    assert passkeys.use_confirmation(user_id, word, change) == passkeys.Confirmed(passkeys.CONFIRMED, made_by)
    with pytest.raises(passkeys.PasskeyError) as again:
        passkeys.use_confirmation(user_id, word, change)
    assert again.value.status == 403 and str(again.value) == passkeys.CONFIRM_STALE


# ---- the words -------------------------------------------------------------------------------------------------------

WAITS = "Takes effect at {at} unless you cancel it."        # push.WAITS, the words of every change held but custody's
# the fourteen kinds and their words: (title, body, the page it opens)
NEW_WORDS = {
    "task-continued": ("A task of yours continued", "Open Agents.", "/app/#agents"),
    "task-relayed": ("A task of yours continued on another of your accounts", "Open Agents.", "/app/#agents"),
    "task-crossed": ("A task of yours continued in another organisation", "Open Agents.", "/app/#agents"),
    "task-needs-you": ("A task of yours needs a decision", "Open Agents.", "/app/#agents"),
    "task-offered": ("A teammate offered you a task", "Open Agents.", "/app/#agents"),
    "task-handed": ("A task was handed to you", "Open Agents.", "/app/#agents"),
    "task-returned": ("A task of yours came back from your teammate", "Open Agents.", "/app/#agents"),
    "custody-asked": ("A teammate asks you to be their custodian", "Open Account.", "/app/#account"),
    "custody-suspended": ("Your custody was suspended", "Open Account.", "/app/#account"),
    "held-account": ("An account waits to join your tasks' relay", WAITS, "/app/"),
    "held-task-cross": ("A task waits to cross organisations again", WAITS, "/app/#agents"),
    "held-task-crossing": ("A task's custody crossing waits", WAITS, "/app/#agents"),
    "held-task-offer": ("An offer of a task waits", WAITS, "/app/#agents"),
    "held-task-custody": ("A task of yours goes to your custodian", "Goes to them at {at} unless you cancel it.",
                          "/app/#agents"),
}
# the kinds that tell of a hold, the only ones whose words take a time
HELD = ("held-account", "held-task-cross", "held-task-crossing", "held-task-offer", "held-task-custody")
AT = 1_790_000_000
SESSION = "0123456789ab"
# what a sender's key could hold, each marked so that one in a notice is seen
MARKED = {"a task's title": "Zq7r60 title of the task", "its goal": "Zq7r60 goal: fix the sign-in form",
          "a step's objective": "Zq7r60 step: run the tests", "a backend's label": "Zq7r60 Max account",
          "an account's email": "zq7r60.account@example.test", "a teammate's address": "zq7r60.mate@example.test"}
KEY = f"task:{ID}:" + "|".join(MARKED.values())


def facts(kind):
    """What `kind`'s words take: a time for a hold's, nothing for any other."""
    return {"at": AT} if kind in HELD else {}


def keyed(key):
    """The tag words makes for `key`: an HMAC under a key derived from the token secret for tags alone."""
    derived = hmac.new(config.token_secret(), b"proofpane push notice tag v1", hashlib.sha256).digest()
    return "ade-" + hmac.new(derived, key.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def shows_nothing_of(text):
    """Nothing the key held is in `text`: no mark, no id, no address."""
    seen = text.lower()
    for what, mark in MARKED.items():
        assert mark.lower() not in seen, what
    assert "zq7r60" not in seen and ID not in seen and "@" not in seen and "example.test" not in seen


@pytest.mark.parametrize("kind", sorted(NEW_WORDS))
def test_each_new_kind_has_its_own_fixed_words(kind):
    assert push.WORDS.get(kind) == NEW_WORDS[kind]
    title, body, url = NEW_WORDS[kind]
    made = push.words(kind, **facts(kind))
    assert (made.title, made.body, made.url) == (title, body.format(at=holds.utc(AT)), url)


@pytest.mark.parametrize("kind", sorted(NEW_WORDS))
def test_a_new_kind_takes_a_time_where_it_tells_of_a_hold_and_no_other_fact(kind):
    assert kind in push.WORDS
    title, body, url = push.WORDS[kind]
    assert re.findall(r"\{([^{}]*)\}", title + url) == []
    assert re.findall(r"\{([^{}]*)\}", body) == (["at"] if kind in HELD else [])
    push.words(kind, **facts(kind))                     # its own facts make it
    for given in ({"count": 3}, {"session": SESSION}, dict(facts(kind), count=3), dict(facts(kind), session=SESSION),
                  {} if kind in HELD else {"at": AT}):
        with pytest.raises(ValueError):
            push.words(kind, **given)


@pytest.mark.parametrize("kind", sorted(NEW_WORDS))
def test_a_new_kind_s_notice_carries_nothing_of_what_it_is_about(kind):
    assert kind in push.WORDS
    made = push.words(kind, key=KEY, **facts(kind))
    for field in (made.title, made.body, made.url, made.tag):
        shows_nothing_of(field)
    # the tag is a digest of the key under the deployment's own key: never the key, nor the key's plain digest, which
    # one could find by trying keys
    assert made.tag == keyed(KEY) and re.fullmatch("ade-[0-9a-f]{16}", made.tag)
    assert made.tag != "ade-" + hashlib.sha256(KEY.encode("utf-8")).hexdigest()[:16]


def test_a_device_reads_each_new_notice_and_nothing_of_what_it_is_about():
    assert set(NEW_WORDS) <= set(push.WORDS), sorted(set(NEW_WORDS) - set(push.WORDS))
    device, service = Device(), PushService()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=AT)
    (sub,) = push.subscriptions("u1")
    for kind in sorted(NEW_WORDS):
        assert push.send(sub, push.words(kind, key=KEY, **facts(kind)), AT, service) == 201
    read = [device.decrypt(body) for _, _, body in service.posts]
    assert [(m["title"], m["body"], m["url"]) for m in read] == [
        (title, body.format(at=holds.utc(AT)), url) for _, (title, body, url) in sorted(NEW_WORDS.items())]
    for m in read:
        assert set(m) == {"title", "body", "tag", "url"} and m["tag"] == keyed(KEY)
        shows_nothing_of(repr(m))


@pytest.mark.parametrize("kind", sorted(NEW_WORDS))
def test_no_new_kind_has_a_now_of_its_own(kind):
    # a hold of no time is told in held-other-now's words, by its kind's callable notice:
    # test_push_words.py pins the kinds that end in -now
    assert kind in push.WORDS and kind + "-now" not in push.WORDS
    assert "held-other-now" in push.WORDS


@pytest.mark.parametrize("kind", sorted(NEW_WORDS))
def test_a_new_kind_s_words_name_no_one(kind):
    assert kind in push.WORDS
    for text in push.WORDS[kind]:
        assert "@" not in text and not re.search("[0-9]", text), text
        assert set(re.findall(r"\{[^{}]*\}", text)) <= {"{at}"}, text
