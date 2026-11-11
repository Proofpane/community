"""A running linked session made the Manager.
Its first part, the holder: the owner chooses one of the sessions linked to the ADE as the Manager, and
signs it with a passkey, a stage-2a payload of the purpose `manager` naming that link (`to`), the digest of the
settings it holds the role under (cap, models, efforts, wait for my go) and when it expires, at most a week on. There is
one holder at a time, an account or a link: a link that holds the role takes it from the account, whose workspace's
chat is told the Manager is off while it holds, and choosing an account again, or changing the settings it holds the
role under, ends its hold; taking the role back needs only the sign-in. A link not heard from within two of its
intervals is listed, marked silent, and not offered; a link whose hook does not run, a cloud session among them, is
offered with the words that Proofpane holds none of that session's own calls.

Its second part, the goals: while a link holds the role, each goal is a
stage-2a instruction to it that the owner signs, kept as the owner's request word for word and as the holder's
instruction, so the server cannot make a goal up; the holder reads its packet from a route of its own, its goals each
with its signed instruction, and a request the owner never signed to it given as no instruction.

Its third part, the acts: the holder plans, starts, reports a task's
state and asks the owner through the app's checked routes, each act checked before the link does it and refused with
why where a check fails: a plan from a goal signed to it in that goal's words, never with a share; a start on its own
machine within the Manager's bounds (on, the models and efforts allowed, the cap), kept as its own report, which the
workspace's record neither ends nor changes."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from proofpane_open import store
from proofpane_open.ade import controller  # noqa: F401  puts the open packages on sys.path
from proofpane_open.ade import tasks, workspaces
from proofpane_core.canonical import canonicalize
from test_ade_link import owner, paired, revoke  # noqa: F401  the fixture
from test_passkeys import Authenticator, add, as_step
from test_controller_managers import ws  # noqa: F401  the fixture: the workspace's managers' route stood in for

C = "/api/ade/controller"
WEEK = 7 * 86400


@pytest.fixture(autouse=True)
def workspace(monkeypatch):
    """The owner has a workspace, as on the hosted site (the local provider gives an admin one, and runs none here);
    the case without one takes it away."""
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    r = add(client, owner, d)
    assert r.status_code == 200, r.text
    return d


def offered(client, owner):
    r = client.get(f"{C}/holders", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


def holder_payload(client, owner, link_id, **over):
    """A holder's payload as the page composes it around a nonce the server gave: the link chosen, the digest of the
    settings it holds the role under as the offer gives it, and a week's life; `over` replaces a field."""
    n = client.post("/api/ade/link/nonce", headers=owner, json={"purpose": "manager"})
    assert n.status_code == 200, n.text
    n = n.json()
    now = n["server_time"]
    p = {"v": 2, "deployment": n["deployment"], "purpose": "manager", "account": n["account"], "nonce": n["nonce"],
         "id": uuid.uuid4().hex, "to": link_id, "settings": offered(client, owner)["settings_digest"],
         "created_at": now, "expires_at": now + WEEK}
    p.update(over)
    return p


def sign_holder(client, owner, device, p, signed=None):
    """POST the holder `p` with `device`'s answer to the step challenge of `signed`'s canonical bytes (`p`'s own by
    default), as the page asks for it."""
    return client.post(f"{C}/holder", headers=owner,
                       json={"payload": p, "credential": device.get(as_step(canonicalize(signed or p)))})


def held(client, owner):
    r = client.get(C, headers=owner)
    assert r.status_code == 200, r.text
    return r.json().get("holder")


def made_holder(client, owner, device, link):
    p = holder_payload(client, owner, link.link_id)
    r = sign_holder(client, owner, device, p)
    assert r.status_code == 200, r.text
    return p


def turned_on():
    """The Manager turned on in the store, as the On switch leaves it (one account in these cases)."""
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET state = 'on'")


def journal_kinds(client, owner):
    r = client.get(f"{C}/journal", headers=owner)
    assert r.status_code == 200, r.text
    return [line["kind"] for line in r.json()["events"]]


def test_a_linked_session_is_made_the_manager_by_the_owners_signature(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    assert held(client, owner) is None
    listed = offered(client, owner)
    assert [(x["id"], x["name"], x["offered"]) for x in listed["links"]] == [(lane.link_id, "Lane1", True)]

    p = made_holder(client, owner, device, lane)

    h = held(client, owner)
    assert (h["link"], h["name"], h["until"], h["payload"]) == (lane.link_id, "Lane1", p["expires_at"], p["id"])
    assert "holder" in journal_kinds(client, owner)


def test_a_holder_signed_over_other_bytes_is_refused(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    p = holder_payload(client, owner, lane.link_id)
    other = dict(p, to=uuid.uuid4().hex)

    r = sign_holder(client, owner, device, p, signed=other)

    assert r.status_code in (400, 401, 403, 422), r.text
    assert held(client, owner) is None


def test_a_holder_is_not_parked_for_a_second_screen_which_signs_none(client, owner):
    """The relay parks what a signer signs (ade_link.py's SIGN_PURPOSES), and no signer shows or signs a holder, which
    the page signs with a passkey in the browser: one parked would hold one of the account's ten places and one of its
    network's three until its nonce ended, as a task's grant would."""
    lane = paired(client, owner, name="Lane1")
    p = holder_payload(client, owner, lane.link_id)

    r = client.post("/api/ade/sign-requests", headers=owner, json={"payload": p})

    assert r.status_code == 422 and "purpose" in r.json()["detail"], r.text
    assert client.get("/api/ade/sign-requests", headers=owner).json()["requests"] == []


def test_a_holder_payload_whose_to_names_none_of_the_owners_live_links_is_refused(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    from conftest import signed_up
    stranger = signed_up(client, email="ml-stranger@example.com")
    theirs = paired(client, stranger, name="theirs")
    gone = paired(client, owner, name="gone")
    r = revoke(client, owner, gone.link_id)
    assert r.status_code == 200, r.text

    for to in (theirs.link_id, gone.link_id, uuid.uuid4().hex):
        r = sign_holder(client, owner, device, holder_payload(client, owner, to))
        assert r.status_code in (403, 404, 409, 422), (to, r.text)
    assert held(client, owner) is None
    assert lane.link_id                                             # the owner's own link stays unchosen


def test_a_holder_lives_a_week_at_most(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    p = holder_payload(client, owner, lane.link_id)
    p["expires_at"] = p["created_at"] + WEEK + 1

    r = sign_holder(client, owner, device, p)

    assert r.status_code == 422, r.text
    assert held(client, owner) is None


def test_an_expired_holder_holds_nothing(client, owner, device):
    """Read by the app's own functions at a time past its expiry: a sign-in a week old has expired itself."""
    lane = paired(client, owner, name="Lane1")
    p = made_holder(client, owner, device, lane)
    from proofpane_open.ade import manager_link
    with store.connect() as db:
        user = {"id": db.execute("SELECT user_id FROM ade_controllers").fetchone()[0]}
        db.execute("UPDATE ade_controllers SET state = 'on' WHERE user_id = ?", (user["id"],))
    later = p["expires_at"] + 1

    assert controller.view(user, now=p["expires_at"] - 1)["holder"].get("live") is True   # a control
    assert controller.view(user, now=later)["holder"].get("live") is False
    assert manager_link.offer(user, now=later)["holder"].get("live") is False
    with store.connect() as db:
        assert controller.holder_of(controller._row(db, user["id"]), later) is None
        # and the account's chat is not the Manager again of itself: a change of holder is the owner's
        assert controller.mirror_body(db, user["id"], now=later)[0]["state"] == "off"


def test_a_holder_signed_under_other_settings_than_the_current_ones_is_refused(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    p = holder_payload(client, owner, lane.link_id, settings="sha256:" + "0" * 64)

    r = sign_holder(client, owner, device, p)

    assert r.status_code == 409, r.text
    assert held(client, owner) is None


def test_one_holder_at_a_time_a_new_link_supersedes_the_old(client, owner, device):
    a = paired(client, owner, name="Lane1")
    b = paired(client, owner, name="Lane2")
    made_holder(client, owner, device, a)

    made_holder(client, owner, device, b)

    assert held(client, owner)["link"] == b.link_id


def test_while_a_link_holds_the_role_the_workspaces_chat_is_told_the_manager_is_off(client, owner, device):
    """Never two holders at once: the account's workspace chat, the other kind of holder, is told it holds nothing."""
    from proofpane_open.ade import controller

    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)

    with store.connect() as db:
        user_id = db.execute("SELECT user_id FROM ade_controllers").fetchone()[0]
        db.execute("UPDATE ade_controllers SET state = 'on' WHERE user_id = ?", (user_id,))
        body, _, _ = controller.mirror_body(db, user_id)

    assert body["state"] == "off", body["state"]


def test_choosing_an_account_again_ends_a_links_hold(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)

    r = client.put(C, headers=owner, json={"account": None})

    assert r.status_code == 200, r.text
    assert held(client, owner) is None


def test_a_change_of_the_settings_a_link_holds_the_role_under_ends_its_hold(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)

    r = client.put(C, headers=owner, json={"cap": 3})

    assert r.status_code == 200, r.text
    assert held(client, owner) is None


def test_control_a_change_of_the_managers_own_chat_model_leaves_a_links_hold(client, owner, device):
    """A control: the account's chat model is no setting a link holds the role under."""
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)

    r = client.put(C, headers=owner, json={"chat_model": "sonnet"})

    assert r.status_code == 200, r.text
    assert held(client, owner)["link"] == lane.link_id


def test_taking_the_role_back_needs_only_the_sign_in(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)

    r = client.request("DELETE", f"{C}/holder", headers=owner)

    assert r.status_code == 200, r.text
    assert held(client, owner) is None
    assert journal_kinds(client, owner).count("holder") == 2         # given, and taken back


def test_a_silent_link_is_listed_marked_and_not_offered(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    long_ago = int(time.time()) - 86400
    with store.connect() as db:
        db.execute("UPDATE ade_links SET created_at = ?, last_seen_at = ?, last_heartbeat_at = ? WHERE id = ?",
                   (long_ago, long_ago, long_ago, lane.link_id))

    listed = offered(client, owner)["links"]
    r = sign_holder(client, owner, device, holder_payload(client, owner, lane.link_id))

    assert [(x["id"], x["silent"], x["offered"]) for x in listed] == [(lane.link_id, True, False)]
    assert r.status_code == 409, r.text
    assert held(client, owner) is None


def test_a_cloud_session_is_offered_with_the_words_that_proofpane_holds_none_of_its_own_calls(client, owner, device):
    cloud = paired(client, owner, name="cloud", kind="cloud")

    listed = offered(client, owner)["links"]
    made_holder(client, owner, device, cloud)

    assert [(x["offered"], "holds none of" in (x.get("notice") or "")) for x in listed] == [(True, True)]
    assert "holds none of" in (held(client, owner).get("notice") or "")


def test_without_a_workspace_a_linked_session_is_made_the_manager_all_the_same(client, owner, device, monkeypatch):
    """No workspace, sign-in or clone is needed. Where the deployment runs no workspace, the holder's
    routes answer, and the Manager's once the account has a live link to make the Manager (the page then offers it)
    or has had one as its holder (what it did stays shown); with neither there is no Manager
    there, as before."""
    monkeypatch.delenv("OPEN_EDITION_ADE", raising=False)
    assert client.get(C, headers=owner).status_code == 404        # no link, no holder, no workspace: as before
    lane = paired(client, owner, name="Lane1")
    first = client.get(C, headers=owner)                          # a live link: the page can offer it
    assert (first.status_code, first.json().get("controller")) == (200, None), first.text

    made_holder(client, owner, device, lane)

    assert held(client, owner)["link"] == lane.link_id
    assert "holder" in journal_kinds(client, owner)
    r = client.request("DELETE", f"{C}/holder", headers=owner)
    assert r.status_code == 200, r.text
    assert client.get(C, headers=owner).status_code == 200        # its link still live: what it did stays shown
    revoke(client, owner, lane.link_id)
    assert client.get(C, headers=owner).status_code == 200        # it had a holder: what it did stays shown


def admins_only(self, user):
    """The hosted site's refusal of an account its workspace is not for (fly_provider.check_user's words)."""
    raise workspaces.WorkspaceAccess(403, "On this deployment a workspace is for admins only, for now.")


def test_where_the_deployment_keeps_the_workspace_from_the_account_a_link_is_no_way_round_it(client, owner, device,
                                                                                           monkeypatch):
    """Where the deployment keeps the workspace from this account (manager_user; on the
    hosted site, an admin's alone), the Manager is not this account's there, through a link or not: its routes and the
    holder's refuse it, as the Manager's did before; taking the role back needs only the sign-in still."""
    lane = paired(client, owner, name="Lane1")
    p = holder_payload(client, owner, lane.link_id)               # composed while the account had a workspace
    monkeypatch.setattr(workspaces.LocalProvider, "check_user", admins_only)

    listed = client.get(f"{C}/holders", headers=owner)
    signed = sign_holder(client, owner, device, p)

    assert (listed.status_code, signed.status_code) == (403, 403), (listed.text, signed.text)
    assert client.get(C, headers=owner).status_code == 403
    assert client.request("DELETE", f"{C}/holder", headers=owner).status_code == 200


def test_with_a_link_holding_the_role_the_manager_is_turned_on_without_an_account(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)

    r = client.post(f"{C}/on", headers=owner)

    assert r.status_code == 200, r.text
    assert r.json()["controller"]["state"] == "on"


def test_taking_the_role_back_turns_the_manager_off_and_the_accounts_chat_does_not_take_it_up(client, owner, device):
    """Taking the role back only removes authority: the account's chat in the workspace does not become the Manager
    again of itself, until the owner turns the Manager on."""
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    turned_on()

    r = client.request("DELETE", f"{C}/holder", headers=owner)

    assert r.status_code == 200, r.text
    got = client.get(C, headers=owner).json()
    assert (got["holder"], got["controller"]["state"]) == (None, "off")
    with store.connect() as db:
        user_id = db.execute("SELECT user_id FROM ade_controllers").fetchone()[0]
        assert controller.mirror_body(db, user_id)[0]["state"] == "off"


def test_a_change_of_the_settings_it_holds_the_role_under_turns_the_manager_off(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    turned_on()

    r = client.put(C, headers=owner, json={"models": ["sonnet"]})

    assert r.status_code == 200, r.text
    assert (r.json()["holder"], r.json()["controller"]["state"]) == (None, "off")


def test_revoking_the_link_that_holds_the_role_ends_its_hold(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    turned_on()

    r = revoke(client, owner, lane.link_id)

    assert r.status_code == 200, r.text
    got = client.get(C, headers=owner).json()
    assert (got["holder"], got["controller"]["state"]) == (None, "off")
    assert journal_kinds(client, owner).count("holder") == 2


def test_while_a_link_holds_the_role_no_attempt_is_queued_for_a_sub_goals_own_manager(client, owner, device, ws):
    """Never two holders at once: a sub-goal's own manager runs in the workspace, on the account, as the account's chat
    does, so the app queues no attempt of one while a link holds the role; a control, once the hold is cleared."""
    from proofpane_open import auth
    from test_controller_managers import attempts, managed, on, sync

    user = auth.user(client.get("/api/auth/me", headers=owner).json()["id"])
    on(user)
    managed(user)
    made_holder(client, owner, device, paired(client, owner, name="Lane1"))
    for _ in range(3):
        sync(user, ws)
    assert attempts(user) == []

    with store.connect() as db:                         # the control: the hold cleared, the Manager left on
        db.execute("UPDATE ade_controllers SET holder_link = NULL WHERE user_id = ?", (user["id"],))
    for _ in range(2):
        sync(user, ws)
    assert len(attempts(user)) == 1


def test_a_change_of_holder_reads_in_the_journal_in_the_apps_own_words(client, owner, device):
    """Fixed words, never the bare kind, nor the link's name, which is no word of the app's."""
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    assert client.request("DELETE", f"{C}/holder", headers=owner).status_code == 200

    r = client.get(f"{C}/journal", headers=owner)

    assert r.status_code == 200, r.text
    assert [e["words"] for e in r.json()["events"] if e["kind"] == "holder"] == [
        "A linked session holds the Manager's role no longer: the owner took the role back",
        "You made a linked session the Manager, with a passkey"]


# ---- the goals a linked Manager is sent, each signed ----------------------------------------------------------------

GOAL = "Make the README say hello.\n\nKeep the tests passing."


def goal_payload(client, owner, link_id, text=GOAL, **over):
    """A goal as the goal box signs it while a link holds the role: a stage-2a instruction to the holder, its words
    the goal's."""
    n = client.post("/api/ade/link/nonce", headers=owner, json={"purpose": "instruction"})
    assert n.status_code == 200, n.text
    n = n.json()
    now = n["server_time"]
    p = {"v": 2, "deployment": n["deployment"], "purpose": "instruction", "account": n["account"],
         "nonce": n["nonce"], "id": uuid.uuid4().hex, "to": link_id, "text": text, "created_at": now,
         "expires_at": now + 3600}
    p.update(over)
    return p


def send_goal(client, owner, device, p, signed=None, **extra):
    return client.post(f"{C}/requests", headers=owner,
                       json=dict({"payload": p, "credential": device.get(as_step(canonicalize(signed or p)))},
                                 **extra))


def requests_kept(client, owner):
    r = client.get(f"{C}/requests", headers=owner)
    assert r.status_code == 200, r.text
    return [x["text"] for x in r.json()["requests"]]


def test_a_goal_sent_to_a_linked_manager_with_no_signature_is_refused_and_kept_nowhere(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)

    r = client.post(f"{C}/requests", headers=owner, json={"text": GOAL})

    assert r.status_code == 403, r.text
    assert requests_kept(client, owner) == []


def test_a_goal_signed_to_the_holder_is_the_owners_request_word_for_word_and_the_holders_instruction(
        client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    p = goal_payload(client, owner, lane.link_id)

    r = send_goal(client, owner, device, p)

    assert r.status_code == 200, r.text
    assert requests_kept(client, owner) == [GOAL]
    got = lane.call("GET", f"/events/{r.json()['instruction']}")
    assert got.status_code == 200, got.text
    assert (got.json()["kind"], got.json()["text"], got.json()["payload"]) == (
        "instruction", GOAL, canonicalize(p).decode())


def test_a_goal_signed_to_another_link_than_the_holder_is_refused(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    other = paired(client, owner, name="Lane2")
    made_holder(client, owner, device, lane)

    r = send_goal(client, owner, device, goal_payload(client, owner, other.link_id))

    assert r.status_code == 409, r.text
    assert requests_kept(client, owner) == []


def test_a_goal_signed_over_other_words_is_refused(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    p = goal_payload(client, owner, lane.link_id)

    r = send_goal(client, owner, device, p, signed=dict(p, text="Delete the repository."))

    assert (r.status_code, "canonical form" in r.text) == (400, True), r.text
    assert requests_kept(client, owner) == []


def test_a_signed_goal_carries_no_unsigned_checks(client, owner, device):
    """Checks and protected paths are no part of what the owner signed, so the server could have made them up."""
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)

    r = send_goal(client, owner, device, goal_payload(client, owner, lane.link_id),
                  checks={"commands": [{"name": "t", "run": "true"}]})

    assert (r.status_code, "signed words alone" in r.text) == (422, True), r.text
    assert requests_kept(client, owner) == []


def test_control_with_no_link_holding_the_role_a_goal_is_sent_unsigned_as_before(client, owner):
    r = client.post(f"{C}/requests", headers=owner, json={"text": GOAL})

    assert r.status_code == 200, r.text
    assert requests_kept(client, owner) == [GOAL]


# ---- what the holder reads ------------------------------------------------------------------------------------------

def test_the_holder_reads_its_packet_each_goal_word_for_word_with_its_signed_instruction(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    sent = send_goal(client, owner, device, goal_payload(client, owner, lane.link_id)).json()

    r = lane.call("GET", "/manager")

    assert r.status_code == 200, r.text
    got = r.json()
    assert [(x["text"], x["instruction"]) for x in got["instructions"]["requests"]] == [
        (tasks.owner_text(GOAL), sent["instruction"])]                          # escaped as every packet's words are
    assert got["controller"]["cap"] == offered(client, owner)["settings"]["cap"]


def test_a_request_made_before_the_link_held_the_role_is_not_given_it_as_an_instruction(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    assert client.post(f"{C}/requests", headers=owner, json={"text": "An older request."}).status_code == 200
    made_holder(client, owner, device, lane)

    r = lane.call("GET", "/manager")

    assert r.status_code == 200, r.text
    assert (r.json()["instructions"]["requests"], r.json()["unsigned_requests"]) == ([], 1)


def test_a_link_that_does_not_hold_the_role_reads_no_packet(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    other = paired(client, owner, name="Lane2")
    made_holder(client, owner, device, lane)

    assert other.call("GET", "/manager").status_code == 403
    assert lane.call("GET", "/manager").status_code == 200          # a control


def test_a_holder_whose_hold_ended_is_sent_no_goal_and_reads_no_packet(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET holder_until = ?", (int(time.time()) - 1,))

    r = send_goal(client, owner, device, goal_payload(client, owner, lane.link_id))

    assert r.status_code == 409, r.text
    assert lane.call("GET", "/manager").status_code == 403
    assert requests_kept(client, owner) == []


# ---- what the holder does: its acts through the app's checked routes ------------------------------------------------

PART = "Make the README say hello."


def holding(client, owner, device, on=True, take=True):
    """A link holding the role, a goal signed to it and taken by it, as a session takes one before it plans from it,
    and the Manager on: (link, the goal's request)."""
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    sent = send_goal(client, owner, device, goal_payload(client, owner, lane.link_id))
    assert sent.status_code == 200, sent.text
    if take:
        took = lane.call("POST", "/take", {"seq": sent.json()["instruction"], "instance": "case"})
        assert took.status_code == 200, took.text
    if on:
        assert client.post(f"{C}/on", headers=owner).status_code == 200
    return lane, sent.json()


def act(lane, **a):
    return lane.call("POST", "/manager/acts", dict({"id": uuid.uuid4().hex}, **a))


def planned(lane, request, words=PART, **over):
    rid = uuid.uuid4().hex
    r = act(lane, **dict({"kind": "plan", "request": request["id"], "requirement": rid, "words": words,
                          "title": "Say hello", "repos": ["demo"]}, **over))
    return r, rid


def started(lane, rid, **over):
    tid = uuid.uuid4().hex
    r = act(lane, **dict({"kind": "start", "task": tid, "requirement": rid, "repo": "demo", "model": "opus",
                          "effort": "max"}, **over))
    return r, tid


def test_a_plan_from_a_goal_signed_to_the_holder_in_its_words_is_taken_as_the_linked_managers(client, owner, device):
    lane, request = holding(client, owner, device)

    r, rid = planned(lane, request)

    assert r.status_code == 200, r.text
    got = client.get(f"{C}/requirements/{rid}", headers=owner)
    assert got.status_code == 200, got.text
    lines = client.get(f"{C}/journal", headers=owner).json()["events"]
    plan = next(e for e in lines if e["kind"] == "plan")
    assert plan["payload"]["by"] == lane.link_id


def test_a_plan_whose_words_are_not_the_goals_word_for_word_is_refused(client, owner, device):
    lane, request = holding(client, owner, device)

    r, rid = planned(lane, request, words="Make the README")          # a part of a paragraph, not a whole one

    assert r.status_code == 409, r.text
    assert client.get(f"{C}/requirements/{rid}", headers=owner).status_code == 404


def test_a_plan_from_a_request_never_signed_to_the_holder_is_refused(client, owner, device):
    lane = paired(client, owner, name="Lane1")
    older = client.post(f"{C}/requests", headers=owner, json={"text": PART})
    assert older.status_code == 200, older.text
    made_holder(client, owner, device, lane)
    assert client.post(f"{C}/on", headers=owner).status_code == 200

    r, rid = planned(lane, older.json())

    assert r.status_code == 409, r.text
    assert client.get(f"{C}/requirements/{rid}", headers=owner).status_code == 404


def test_a_linked_manager_plans_no_share_for_a_sub_goals_own_manager(client, owner, device):
    """Holding the role gives no reach over other sessions: a share would."""
    lane, request = holding(client, owner, device)

    r, rid = planned(lane, request, share=1)

    assert r.status_code == 409, r.text
    assert client.get(f"{C}/requirements/{rid}", headers=owner).status_code == 404


def test_a_start_within_the_bounds_is_kept_as_the_links_own_report(client, owner, device):
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)

    r, tid = started(lane, rid)

    assert r.status_code == 200, r.text
    [task] = client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"]
    assert (task["id"], task["reported_by"]) == (tid, lane.link_id)


def test_a_start_with_a_model_or_an_effort_the_owner_did_not_allow_is_refused(client, owner, device):
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)

    for over in ({"model": "sonnet"}, {"effort": "low"}):
        r, tid = started(lane, rid, **over)
        assert r.status_code == 409, (over, r.text)
    assert client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"] == []


def test_a_start_past_the_cap_is_refused(client, owner, device):
    lane, request = holding(client, owner, device)
    assert client.put(C, headers=owner, json={"cap": 1}).status_code == 200
    made_holder(client, owner, device, lane)                       # the settings changed: signed again
    assert client.post(f"{C}/on", headers=owner).status_code == 200
    _, rid = planned(lane, request)
    first, _ = started(lane, rid)
    assert first.status_code == 200, first.text

    r, _ = started(lane, rid)

    assert r.status_code == 409, r.text
    assert len(client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"]) == 1


def test_a_start_while_the_manager_is_off_or_stopped_is_refused(client, owner, device):
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    assert client.post(f"{C}/stop", headers=owner).status_code == 200

    r, _ = started(lane, rid)

    assert r.status_code == 409, r.text


def test_a_link_that_does_not_hold_the_role_has_its_acts_refused(client, owner, device):
    lane, request = holding(client, owner, device)
    other = paired(client, owner, name="Lane2")

    r, _ = planned(other, request)

    assert r.status_code == 403, r.text


def test_the_holders_report_of_its_tasks_state_is_kept_as_its_own(client, owner, device):
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    _, tid = started(lane, rid)

    r = act(lane, kind="state", task=tid, state="done")

    assert r.status_code == 200, r.text
    [task] = client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"]
    assert (task["state"], task["reported_by"]) == ("done", lane.link_id)


def test_the_holder_asks_the_owner_through_the_session_channel_whose_answer_comes_signed(client, owner, device):
    """An answer to the Manager's own question is a decision made with the sign-in alone,
    which a linked Manager would take as the owner's; it asks in the session channel, where his answer is signed."""
    lane, request = holding(client, owner, device)
    qid = uuid.uuid4().hex

    r = act(lane, kind="question", question=qid, text="Which README?")

    assert (r.status_code, "post question" in r.text) == (422, True), r.text
    assert client.get(C, headers=owner).json()["questions"] == []


def test_a_workspace_page_neither_ends_nor_changes_the_holders_own_task(client, owner, device):
    """The link's task runs on its own machine, so no page of the workspace's record lists it: a page whose states are
    read in full would otherwise end it, and a page naming it would change it."""
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    r, tid = started(lane, rid)
    assert r.status_code == 200, r.text
    with store.connect() as db:
        user_id = db.execute("SELECT user_id FROM ade_controllers").fetchone()[0]

    controller.take(user_id, {"tasks": [], "actions": []})                    # a page listing no session open
    controller.take(user_id, {"tasks": [{"id": tid, "state": "failed"}],
                              "actions": [{"id": uuid.uuid4().hex, "kind": "stop", "task": tid}]})

    [task] = client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"]
    assert (task["state"], task["stopped_at"], task["reported_by"]) == ("starting", None, lane.link_id)


def test_the_holders_packet_says_its_tasks_run_on_its_own_machine_only(client, owner, device):
    """A linked Manager's start in the workspace is not built, so its tasks run on its own machine only, and its
    packet says so."""
    lane, _ = holding(client, owner, device)

    r = lane.call("GET", "/manager")

    assert r.status_code == 200, r.text
    assert "own machine" in (r.json().get("where") or "")


# ---- a linked Manager's acts, goals and hold, case by case ----------------------------------------------------------

def user_of(client, owner):
    return client.get("/api/auth/me", headers=owner).json()["id"]


def record_plan(rid, request_id, at, **over):
    """A plan as the workspace's record gives one (controller.take)."""
    return dict({"id": uuid.uuid4().hex, "kind": "plan", "at": at, "requirement": rid, "request": request_id,
                 "title": "Say hello", "words": PART, "acceptance": None, "repos": ["demo"]}, **over)


def record_start(tid, rid, at):
    return {"id": uuid.uuid4().hex, "kind": "start", "at": at, "task": tid, "requirement": rid, "session": None,
            "repo": "demo", "branch": None, "base": None, "account": "default", "program": "claude", "model": "opus",
            "effort": "max"}


def test_a_refused_act_sent_again_by_its_id_is_refused_again(client, owner, device):
    """A refused act's id is marked seen, refused; sent again it reads as refused, never as taken."""
    lane, request = holding(client, owner, device)
    r, rid = planned(lane, request)
    assert r.status_code == 200, r.text
    body = {"id": uuid.uuid4().hex, "kind": "start", "task": uuid.uuid4().hex, "requirement": rid, "repo": "demo",
            "model": "sonnet", "effort": "max"}

    first = lane.call("POST", "/manager/acts", body)
    again = lane.call("POST", "/manager/acts", body)

    assert (first.status_code, again.status_code) == (409, 409), (first.text, again.text)
    assert "a model the owner did not allow" in (again.json().get("why") or ""), again.text
    assert client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"] == []


def test_while_a_link_holds_the_role_the_workspaces_record_plans_and_asks_nothing_and_its_start_is_flagged(
        client, owner, device):
    """Two holders at once: until the workspace takes the mirror that says off, its chat's record could
    still plan, ask and start. While a link holds the role, the record's plans and questions are refused, and a start
    it made then is kept, flagged."""
    from test_controller import own
    uid = user_of(client, owner)
    own(uid)
    assert client.put(C, headers=owner, json={"account": "default"}).status_code == 200
    older = client.post(f"{C}/requests", headers=owner, json={"text": PART})
    assert older.status_code == 200, older.text
    now = int(time.time())
    before = uuid.uuid4().hex
    assert controller.take(uid, {"actions": [record_plan(before, older.json()["id"], now)]}, now=now)["taken"] == 1
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    assert client.post(f"{C}/on", headers=owner).status_code == 200
    later, tid = int(time.time()) + 1, uuid.uuid4().hex

    out = controller.take(uid, {"actions": [
        record_plan(uuid.uuid4().hex, older.json()["id"], later),
        {"id": uuid.uuid4().hex, "kind": "question", "at": later, "question": uuid.uuid4().hex, "text": "Which?"},
        record_start(tid, before, later)]}, now=later + 1)

    assert (out["refused"], out["flagged"]) == (2, 1), out
    with store.connect() as db:
        flag = db.execute("SELECT flag FROM ade_controller_tasks WHERE id = ?", (tid,)).fetchone()["flag"]
    assert "while a linked session held the Manager's role" in (flag or ""), flag


def test_a_linked_managers_start_is_refused_for_a_requirement_not_planned_from_its_own_goals(client, owner, device):
    """A holder starts only for what it planned from goals signed to it, and never for a requirement a
    sub-goal's own manager keeps."""
    uid = user_of(client, owner)
    older = client.post(f"{C}/requests", headers=owner, json={"text": PART})
    assert older.status_code == 200, older.text
    now = int(time.time())
    managed, plain = uuid.uuid4().hex, uuid.uuid4().hex
    out = controller.take(uid, {"actions": [record_plan(managed, older.json()["id"], now, share=1),
                                            record_plan(plain, older.json()["id"], now)]}, now=now + 1)
    assert out["taken"] == 2, out
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    assert client.post(f"{C}/on", headers=owner).status_code == 200

    kept, _ = started(lane, managed)
    other, _ = started(lane, plain)

    assert (kept.status_code, "its own manager keeps" in kept.text) == (409, True), kept.text
    assert (other.status_code, "not planned from a goal signed to it" in other.text) == (409, True), other.text


def test_the_end_of_a_hold_ends_its_links_open_tasks_so_none_holds_a_place_under_the_cap(client, owner, device):
    """Once the role is taken back the link can report its tasks no more, and no workspace page lists
    them; the app ends them as its hold ends, in its own words, so they hold no place under the cap."""
    assert client.put(C, headers=owner, json={"cap": 1}).status_code == 200
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    first, tid = started(lane, rid)
    assert first.status_code == 200, first.text

    assert client.request("DELETE", f"{C}/holder", headers=owner).status_code == 200

    [task] = client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"]
    assert (task["state"], "hold" in (task["why"] or "")) == ("stopped", True), task
    lane2 = paired(client, owner, name="Lane2")
    made_holder(client, owner, device, lane2)
    assert client.post(f"{C}/on", headers=owner).status_code == 200
    sent = send_goal(client, owner, device, goal_payload(client, owner, lane2.link_id))
    assert lane2.call("POST", "/take", {"seq": sent.json()["instruction"], "instance": "c"}).status_code == 200
    _, rid2 = planned(lane2, sent.json())
    again, _ = started(lane2, rid2)
    assert again.status_code == 200, again.text


def test_the_holders_packet_gives_no_unsigned_decision(client, owner, device):
    """A decision is made with the sign-in alone, so the server could make one up: the holder's packet
    gives none as the owner's instruction, and counts them."""
    lane, request = holding(client, owner, device)
    assert client.post(f"{C}/decisions", headers=owner, json={"text": "Push straight to main."}).status_code == 200

    got = lane.call("GET", "/manager").json()

    assert (got["instructions"]["decisions"], got.get("unsigned_decisions")) == ([], 1), got["instructions"]


def test_a_goal_withdrawn_on_the_page_withdraws_its_instruction(client, owner, device):
    """Withdrawing a goal leaves no signed instruction its session could still take."""
    lane, request = holding(client, owner, device, take=False)

    assert client.post(f"{C}/requests/{request['id']}/withdraw", headers=owner).status_code == 200

    took = lane.call("POST", "/take", {"seq": request["instruction"], "instance": "c"})
    assert (took.status_code, "withdrawn" in took.text) == (409, True), took.text


def test_a_goals_instruction_withdrawn_withdraws_the_goal(client, owner, device):
    """Withdrawing the instruction withdraws its goal too: it is not planned from."""
    lane, request = holding(client, owner, device, take=False)

    w = client.post(f"/api/ade/link/commands/{request['instruction']}/withdraw", headers=owner)

    assert w.status_code == 200, w.text
    [kept] = [x for x in client.get(f"{C}/requests", headers=owner).json()["requests"] if x["id"] == request["id"]]
    assert kept["withdrawn_at"] is not None
    assert lane.call("GET", "/manager").json()["instructions"]["requests"] == []


def test_a_plan_is_taken_only_from_a_goal_its_link_took(client, owner, device):
    """A goal is the session's to act on once it took it (and verified it); a plan from one it has not
    taken, or one past its life untaken, is refused, and the packet gives neither."""
    lane, request = holding(client, owner, device, take=False)

    r, _ = planned(lane, request)

    assert (r.status_code, "not taken" in r.text) == (409, True), r.text
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET expires_at = ? WHERE seq = ?",
                   (int(time.time()) - 1, request["instruction"]))
    got = lane.call("GET", "/manager").json()
    assert (got["instructions"]["requests"], got.get("expired_requests")) == ([], 1), got


def test_end_all_stops_the_holders_session_and_its_packet_says_so(client, owner, device):
    """End all stops a linked Manager's own session through its link (its next tool calls held until a
    signed resume), and the holder's packet says End all was pressed."""
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    r, _ = started(lane, rid)
    assert r.status_code == 200, r.text

    assert client.post(f"{C}/end-all", headers=owner).status_code == 200

    with store.connect() as db:
        stopped = db.execute("SELECT stopped_at FROM ade_links WHERE id = ?", (lane.link_id,)).fetchone()[0]
    assert stopped is not None
    assert lane.call("GET", "/state").json()["stopped"] is True
    got = lane.call("GET", "/manager")
    assert (got.status_code, isinstance(got.json().get("end_all"), int)) == (200, True), got.text


def test_renewing_the_holders_own_hold_reads_as_a_renewal(client, owner, device):
    """The same link signed again renews its hold; its journal line does not say another's place."""
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    made_holder(client, owner, device, lane)

    words = [e["words"] for e in client.get(f"{C}/journal", headers=owner).json()["events"] if e["kind"] == "holder"]

    assert words[0] == "You renewed a linked session's hold on the Manager's role, with a passkey", words


def test_a_goal_sent_again_under_a_stored_id_is_refused_not_failed(client, owner, device):
    """A goal whose payload id is stored already answers 409, as a command does, never 500."""
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    p = goal_payload(client, owner, lane.link_id)
    assert send_goal(client, owner, device, p).status_code == 200
    quiet = TestClient(client.app, raise_server_exceptions=False)      # a server error answered, as a browser sees it

    again = send_goal(quiet, owner, device, goal_payload(client, owner, lane.link_id, id=p["id"]))

    assert again.status_code == 409, again.text


def test_a_packet_the_app_cannot_build_answers_the_holder_in_words_not_500(client, owner, device, monkeypatch):
    """Where the app cannot build the holder's packet (controller.PacketError), its read answers 503 with
    the app's words, never a server error."""
    lane, _ = holding(client, owner, device)

    def unbuilt(db, user_id, now=None):
        raise controller.PacketError("The packet would be past its most.")
    monkeypatch.setattr(controller, "build_packet", unbuilt)
    lane.client = TestClient(client.app, raise_server_exceptions=False)

    r = lane.call("GET", "/manager")

    assert (r.status_code, "past its most" in r.text) == (503, True), r.text


def test_with_a_hold_that_ended_the_on_switch_says_what_to_do(client, owner, device):
    """The On switch, with a kept hold that ended, refuses and says what to do."""
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET holder_until = ?", (int(time.time()) - 1,))

    r = client.post(f"{C}/on", headers=owner)

    assert (r.status_code, "sign it again" in r.text) == (409, True), r.text


def test_an_attempt_never_sent_is_ended_off_once_a_link_is_kept_as_the_holder(env, ws):
    """A branch with no case: an attempt of a sub-goal's own manager that the app never sent is ended,
    `off`, once a linked session is kept as the Manager's holder, and never sent, as with the Manager turned off."""
    from test_controller_managers import T0, attempts, managed, on, sync
    user = {"id": "u1"}
    on(user)
    managed(user)
    sync(user, ws)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        c = controller._row(db, user["id"])
        controller._queue(db, user["id"], c["version"], False, T0 + 50)
    [a] = attempts(user)
    assert (a["state"], a["sent_at"]) == ("queued", None)
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET holder_link = 'a-link', holder_until = ? WHERE user_id = ?",
                   (int(time.time()) + 3600, user["id"]))

    sync(user, ws)

    [a] = attempts(user)
    assert (a["state"], a["outcome"], a["cause"]) == ("ended", "stopped", "off")
    assert all(b["attempt"] is None for b in ws.managers)


def test_another_links_hold_in_its_place_ends_the_old_links_open_tasks(client, owner, device):
    """The role given to another link: the old holder's open tasks are ended, as at any end of its hold."""
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    first, _ = started(lane, rid)
    assert first.status_code == 200, first.text
    lane2 = paired(client, owner, name="Lane2")

    made_holder(client, owner, device, lane2)

    [task] = client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"]
    assert (task["state"], "another linked session" in (task["why"] or "")) == ("stopped", True), task


def test_a_plan_from_a_goal_whose_instruction_the_owner_withdrew_is_refused_so(client, owner, device):
    """A plan from a goal the owner withdrew is refused, in words that say so."""
    lane, request = holding(client, owner, device, take=False)
    assert client.post(f"/api/ade/link/commands/{request['instruction']}/withdraw", headers=owner).status_code == 200

    r, _ = planned(lane, request)

    assert (r.status_code, "withdrew" in r.text) == (409, True), r.text


def test_a_refused_act_sent_again_reads_its_own_why_not_the_newest_refusals(client, owner, device):
    """The why an act sent again reads is its own refusal's, found by its id, not the newest of its kind."""
    lane, request = holding(client, owner, device)
    r, rid = planned(lane, request)
    assert r.status_code == 200, r.text
    body = {"id": uuid.uuid4().hex, "kind": "start", "task": uuid.uuid4().hex, "requirement": rid, "repo": "demo",
            "model": "sonnet", "effort": "max"}
    other = dict(body, id=uuid.uuid4().hex, task=uuid.uuid4().hex, model="opus", effort="low")
    assert lane.call("POST", "/manager/acts", body).status_code == 409
    assert lane.call("POST", "/manager/acts", other).status_code == 409

    again = lane.call("POST", "/manager/acts", body)

    assert (again.status_code, again.json().get("why")) == (409, "a model the owner did not allow"), again.text


def test_once_its_hold_ends_the_workspaces_record_plans_again(client, owner, device):
    """A control: the record's actions are refused only while a link was kept as the holder at their time;
    once the owner takes the role back, its chat may plan again."""
    from test_controller import own
    uid = user_of(client, owner)
    own(uid)
    assert client.put(C, headers=owner, json={"account": "default"}).status_code == 200
    older = client.post(f"{C}/requests", headers=owner, json={"text": PART})
    assert older.status_code == 200, older.text
    lane = paired(client, owner, name="Lane1")
    made_holder(client, owner, device, lane)
    assert client.request("DELETE", f"{C}/holder", headers=owner).status_code == 200
    later = int(time.time()) + 5

    out = controller.take(uid, {"actions": [record_plan(uuid.uuid4().hex, older.json()["id"], later)]}, now=later + 1)

    assert (out["taken"], out["refused"]) == (1, 0), out


def test_renewing_its_own_hold_leaves_the_links_tasks_open(client, owner, device):
    """A control: the same link signed again renews its hold, and its tasks stay open, its own reports."""
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    first, _ = started(lane, rid)
    assert first.status_code == 200, first.text

    made_holder(client, owner, device, lane)

    [task] = client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"]
    assert task["state"] == "starting", task


def test_the_role_given_to_another_link_after_an_ended_hold_ends_the_old_links_tasks(client, owner, device):
    """A hold that expired is kept, holding nothing, until the owner acts; giving the role to another link
    then ends the old link's open tasks, as giving it while the hold was live does."""
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    first, _ = started(lane, rid)
    assert first.status_code == 200, first.text
    with store.connect() as db:
        db.execute("UPDATE ade_controllers SET holder_until = ?", (int(time.time()) - 1,))
    lane2 = paired(client, owner, name="Lane2")

    made_holder(client, owner, device, lane2)

    [task] = client.get(f"{C}/requirements/{rid}", headers=owner).json()["tasks"]
    assert (task["state"], "another linked session" in (task["why"] or "")) == ("stopped", True), task


def test_end_alls_journal_line_says_the_holders_session_was_stopped(client, owner, device):
    """End all's line in the journal says it stopped the session that holds the role."""
    holding(client, owner, device)

    assert client.post(f"{C}/end-all", headers=owner).status_code == 200

    words = [e["words"] for e in client.get(f"{C}/journal", headers=owner).json()["events"]
             if e["kind"] == "controller.end_all"]
    assert words and words[0].endswith("and the linked session that holds its role stopped"), words


def test_the_journal_says_the_app_ended_the_holders_task_as_its_hold_ended(client, owner, device):
    """The line for each task the app ended at a hold's end says so, in the app's own words."""
    lane, request = holding(client, owner, device)
    _, rid = planned(lane, request)
    first, _ = started(lane, rid)
    assert first.status_code == 200, first.text

    assert client.request("DELETE", f"{C}/holder", headers=owner).status_code == 200

    words = [e["words"] for e in client.get(f"{C}/journal", headers=owner).json()["events"]
             if e["kind"] == "task.ended"]
    assert words == ["The app ended the linked session's task as its hold on the Manager's role ended: the owner took "
                     "the role back"], words
