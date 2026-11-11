"""A proposal is done only by the owner's own word on the page: an instruction, an answer or a resume signed with a
passkey as the command it proposes, the signed payload's id the proposal's; a stop accepted with the sign-in; a held
command's answer given on the command, an approval as proposed with the account's confirmation (a hardware key's while
high security is on), a denial with the sign-in. A payload naming a proposal that does not wait, or that proposes
another command, is refused."""

import time

import pytest

from conftest import signed_up
from connector_fixtures import authorize, confirmation, connected, exchange, pkce, query, register, request_id
from fido_fixtures import maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import oauth, passkeys, store
from proofpane_open.ade import approvals, connector, link, proposals
from proofpane_open.ade.link_commands import text_digest
from test_ade_link import paired, post as link_post
from test_ade_link_commands import payload, send, take
from test_high_security import keys, word  # noqa: F401  (keys: a fixture)
from test_high_security import on as high_security
from test_passkeys import Authenticator, add


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    oauth._failed.clear()
    connector._ws_cache.clear()


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    assert add(client, owner, d).status_code == 200
    return d


@pytest.fixture
def app(client, owner):
    return oauth.authenticate("Bearer " + connected(client, owner)["access_token"])


@pytest.fixture
def mac(client, owner):
    return paired(client, owner, name="mac")


def propose(app, name, **args):
    result = connector.call(app, {"name": name, "arguments": args}, int(time.time()))
    assert result["isError"] is False, result
    return result["structuredContent"]["proposal"]["id"]


def read(app, pid):
    return connector.call(app, {"name": "read_proposal", "arguments": {"proposal": pid}},
                          int(time.time()))["structuredContent"]


def commands():
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ade_link_events WHERE link_id = 'owner'").fetchone()[0]


def test_an_instruction_is_signed_as_its_command_and_its_session_takes_it(client, owner, device, app, mac):
    pid = propose(app, "propose_instruction", session="link:" + mac.link_id, text="run the tests")
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id, "run the tests", id=pid))
    assert r.status_code == 200, r.text
    seq = r.json()["seq"]
    got = read(app, pid)
    assert got["done"] is True and got["proposal"]["state"] == "sent" and got["proposal"]["seq"] == seq
    assert got["said"].startswith("Done: the owner signed it at ")
    assert got["said"].endswith(f"stored as command [{seq}] to the session. What the session does with it is its own "
                                "word: read its conversation.")
    assert got["proposal"]["session"] == "mac"
    assert got["proposal"]["command"] == {"seq": seq, "state": "queued"}
    assert take(mac, seq).status_code == 200


def test_the_owner_may_change_the_words_and_the_app_is_told(client, owner, device, app, mac):
    pid = propose(app, "propose_instruction", session="link:" + mac.link_id, text="run the tests")
    send(client, owner, device, payload(client, owner, "instruction", mac.link_id, "run the fast tests", id=pid))
    got = read(app, pid)
    assert got["proposal"]["edited"] is True and "with words they changed" in got["said"]


@pytest.mark.parametrize("how", ["refused", "withdrawn", "expired", "void"])
def test_a_proposal_that_does_not_wait_cannot_be_signed(client, owner, device, app, mac, how):
    pid = propose(app, "propose_instruction", session="link:" + mac.link_id, text="run the tests")
    with store.connect() as db:
        db.execute("UPDATE ade_proposals SET state = ? WHERE id = ?", (how, pid)) if how != "expired" else \
            db.execute("UPDATE ade_proposals SET expires_at = 1 WHERE id = ?", (pid,))
    before = commands()
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id, "run the tests", id=pid))
    assert r.status_code == 409 and how in r.json()["detail"]
    assert commands() == before


def test_a_payload_naming_a_proposal_of_another_command_is_refused(client, owner, device, app, mac):
    other = paired(client, owner, name="cloud", kind="cloud")
    pid = propose(app, "propose_instruction", session="link:" + mac.link_id, text="run the tests")
    r = send(client, owner, device, payload(client, owner, "instruction", other.link_id, "run the tests", id=pid))
    assert r.status_code == 409 and "another command" in r.json()["detail"]
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    r = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "per user", id=pid, ref=seq,
                                            digest=text_digest("which cap?")))
    assert r.status_code == 409 and "another command" in r.json()["detail"]
    assert read(app, pid)["proposal"]["state"] == "proposed"


def test_a_proposed_stop_is_not_signed_as_an_instruction_to_its_session(client, owner, device, app, mac):
    pid = propose(app, "propose_stop", session="link:" + mac.link_id)
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id, "run the tests", id=pid))
    assert r.status_code == 409 and "another command" in r.json()["detail"]
    assert commands() == 0 and read(app, pid)["proposal"]["state"] == "proposed"


def test_a_proposed_answer_is_not_signed_as_the_answer_to_another_question(client, owner, device, app, mac):
    first = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    second = link_post(mac, "question", "which region?", to="owner").json()["seq"]
    pid = propose(app, "propose_answer", question=f"question:{first}", text="per user")
    r = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "per user", id=pid, ref=second,
                                            digest=text_digest("which region?")))
    assert r.status_code == 409 and "another command" in r.json()["detail"]
    assert read(app, pid)["proposal"]["state"] == "proposed"


def test_a_proposal_gives_one_command(client, owner, device, app, mac):
    pid = propose(app, "propose_instruction", session="link:" + mac.link_id, text="run the tests")
    assert send(client, owner, device, payload(client, owner, "instruction", mac.link_id, "run the tests",
                                               id=pid)).status_code == 200
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id, "run the tests", id=pid))
    assert r.status_code == 409


def test_an_answer_is_signed_as_its_answer_and_closes_the_question(client, owner, device, app, mac):
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    pid = propose(app, "propose_answer", question=f"question:{seq}", text="per user")
    r = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "per user", id=pid, ref=seq,
                                            digest=text_digest("which cap?")))
    assert r.status_code == 200, r.text
    assert read(app, pid)["done"] is True
    with store.connect() as db:
        assert db.execute("SELECT closed_by FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()[0] == \
            r.json()["seq"]


def test_a_resume_is_signed_as_its_resume(client, owner, device, app, mac):
    client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner)
    time.sleep(1.1)                                     # a resume is made after the stop it lifts
    pid = propose(app, "propose_resume", session="link:" + mac.link_id)
    p = payload(client, owner, "resume", mac.link_id, id=pid)
    r = send(client, owner, device, p, path=f"/api/ade/link/links/{mac.link_id}/resume")
    assert r.status_code == 200, r.text
    got = read(app, pid)
    assert got["done"] is True and "hook lifts its stop when it verifies it" in got["said"]


def test_a_stop_is_accepted_with_the_sign_in_alone(client, owner, app, mac):
    pid = propose(app, "propose_stop", session="link:" + mac.link_id)
    other = signed_up(client, "other@example.com")
    assert client.post(f"/api/connector/proposals/{pid}/accept", headers=other).status_code == 404
    r = client.post(f"/api/connector/proposals/{pid}/accept", headers=owner)
    assert r.status_code == 200 and r.json()["state"] == "sent"
    with store.connect() as db:
        assert db.execute("SELECT stopped_at FROM ade_links WHERE id = ?", (mac.link_id,)).fetchone()[0] is not None
    assert read(app, pid)["said"].startswith("Done: the owner accepted it, and the session was stopped at ")
    assert client.post(f"/api/connector/proposals/{pid}/accept", headers=owner).status_code == 409


def test_only_a_stop_is_accepted_so(client, owner, device, app, mac):
    pid = propose(app, "propose_instruction", session="link:" + mac.link_id, text="run the tests")
    r = client.post(f"/api/connector/proposals/{pid}/accept", headers=owner)
    assert r.status_code == 409 and "Only a stop" in r.json()["detail"]
    assert commands() == 0


def held(client, owner, command="git push origin main"):
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    token_id, token = approvals.create_token(user_id, "mac session")
    identity = {"id": token_id, "user_id": user_id, "name": "mac session"}
    return identity, approvals.request(identity, command)


def test_a_held_command_s_proposed_answer_takes_the_account_s_confirmation(client, owner, app):
    identity, r = held(client, owner)
    pid = propose(app, "propose_approval", approval="approval:" + r["id"], decision="approve")
    got = client.post(f"/api/ade/approvals/{r['id']}", headers=owner, json={"approve": True, "proposal": pid})
    # a held command's words, as on its own Approve: a page that sends none is from an older release
    assert got.status_code == 403 and got.json()["detail"] == approvals.NEEDS_CONFIRM_TO_APPROVE
    assert approvals.read_for_hook(identity, r["id"])["state"] == "pending"
    assert read(app, pid)["proposal"]["state"] == "proposed"
    got = client.post(f"/api/ade/approvals/{r['id']}", headers=owner,
                      json={"approve": True, "proposal": pid,
                            "confirmation": confirmation(client, owner, f"request-approve:{r['id']}")})
    assert got.status_code == 200 and got.json()["state"] == "approved"
    answer = read(app, pid)
    assert answer["done"] is True and answer["said"].startswith("Done: the owner approved the command once at ")
    seen = approvals.read_for_hook(identity, r["id"])
    assert seen["state"] == "approved" and seen["words"] is None             # no words but the owner's own


def test_a_proposed_denial_is_accepted_with_the_sign_in_alone(client, owner, app):
    identity, r = held(client, owner)
    pid = propose(app, "propose_approval", approval="approval:" + r["id"], decision="deny")
    got = client.post(f"/api/ade/approvals/{r['id']}", headers=owner, json={"approve": False, "proposal": pid})
    assert got.status_code == 200 and got.json()["state"] == "denied"
    answer = read(app, pid)
    assert answer["done"] is True and answer["said"].startswith("Done: the owner denied the command at ")
    assert approvals.read_for_hook(identity, r["id"])["state"] == "denied"


def test_answering_the_other_way_refuses_the_proposal_and_needs_no_confirmation(client, owner, app):
    identity, r = held(client, owner)
    pid = propose(app, "propose_approval", approval="approval:" + r["id"], decision="approve")
    got = client.post(f"/api/ade/approvals/{r['id']}", headers=owner, json={"approve": False, "proposal": pid})
    assert got.status_code == 200 and got.json()["state"] == "denied"
    answer = read(app, pid)
    assert answer["proposal"]["state"] == "refused" and "answered the command the other way" in answer["said"]


def test_a_proposal_of_another_command_is_refused_on_this_one(client, owner, app):
    _, first = held(client, owner, "git push")
    _, second = held(client, owner, "make release")
    pid = propose(app, "propose_approval", approval="approval:" + first["id"], decision="deny")
    got = client.post(f"/api/ade/approvals/{second['id']}", headers=owner, json={"approve": False, "proposal": pid})
    assert got.status_code == 409
    assert proposals.read(app["user_id"], pid)["state"] == "proposed"


def test_a_held_command_is_answered_only_with_its_own_waiting_approval_proposal(client, owner, app, mac):
    """bind_decision refuses (409, and the command stays waiting) a proposal that is not an approval's answer (a stop
    named as the proposal), one that no longer waits, and one whose time ended while the command still waits."""
    identity, r = held(client, owner)
    stop = propose(app, "propose_stop", session="link:" + mac.link_id)
    got = client.post(f"/api/ade/approvals/{r['id']}", headers=owner, json={"approve": False, "proposal": stop})
    assert got.status_code == 409 and "not one for this command" in got.json()["detail"]
    assert approvals.read_for_hook(identity, r["id"])["state"] == "pending"
    for done in ("refused", "withdrawn"):
        pid = propose(app, "propose_approval", approval="approval:" + r["id"], decision="deny")
        with store.connect() as db:
            db.execute("UPDATE ade_proposals SET state = ? WHERE id = ?", (done, pid))
        got = client.post(f"/api/ade/approvals/{r['id']}", headers=owner, json={"approve": False, "proposal": pid})
        assert got.status_code == 409 and f"is {done}, not waiting" in got.json()["detail"], done
        assert approvals.read_for_hook(identity, r["id"])["state"] == "pending"
    now = int(time.time())
    _, longer = held(client, owner, "make release")
    with store.connect() as db:                         # a command that waits two hours, its proposal one
        db.execute("UPDATE ade_requests SET until = ? WHERE id = ?", (now + 7200, longer["id"]))
    pid = propose(app, "propose_approval", approval="approval:" + longer["id"], decision="deny", waits_hours=1)
    expires = proposals.read(app["user_id"], pid)["expires_at"]
    with pytest.raises(proposals.ProposalError) as e:
        approvals.decide(app["user_id"], longer["id"], False, proposal=pid, now=expires)
    assert e.value.status == 409 and "is expired, not waiting" in str(e.value)
    assert approvals.decide(app["user_id"], longer["id"], False, now=expires)["state"] == "denied"


def test_a_stop_whose_time_ended_is_not_accepted(client, owner, app, mac):
    pid = propose(app, "propose_stop", session="link:" + mac.link_id, waits_hours=1)
    expires = proposals.read(app["user_id"], pid)["expires_at"]
    with pytest.raises(proposals.ProposalError) as e:
        proposals.accept_stop(app["user_id"], pid, now=expires)
    assert e.value.status == 409 and "is expired, not waiting" in str(e.value)
    with store.connect() as db:
        assert db.execute("SELECT stopped_at FROM ade_links WHERE id = ?", (mac.link_id,)).fetchone()[0] is None
    assert proposals.accept_stop(app["user_id"], propose(app, "propose_stop", session="link:" + mac.link_id),
                                 now=expires - 1)["state"] == "sent"


@pytest.mark.parametrize("change, payload_over, words", [
    ({"state": "refused"}, {}, "That proposal is refused"),
    ({"state": "withdrawn"}, {}, "That proposal is withdrawn"),
    ({"state": "void"}, {}, "That proposal is void"),
    ({"expires_at": 1}, {}, "That proposal is expired"),
    ({}, {"purpose": "resume"}, "a proposal of another command"),
    ({}, {"to": "0" * 32}, "a proposal of another command"),
    ({}, {"ref": 999}, "a proposal of another command"),
])
def test_bind_signed_refuses_a_payload_its_proposal_does_not_propose(client, owner, device, app, mac, change,
                                                                     payload_over, words):
    """Each refusal of bind_signed, read directly: a proposal that does not wait, and one of another command (another
    purpose, session or question), refuse the payload whose id is theirs (409), nothing changed."""
    seq = link_post(mac, "question", "which cap?", to="owner").json()["seq"]
    pid = propose(app, "propose_answer", question=f"question:{seq}", text="per user")
    if change:
        with store.connect() as db:
            db.execute("UPDATE ade_proposals SET " + ", ".join(f"{k} = ?" for k in change) + " WHERE id = ?",
                       (*change.values(), pid))
    payload = dict({"id": pid, "to": mac.link_id, "ref": seq}, **payload_over)
    purpose = payload.pop("purpose", "answer")
    with store.connect() as db:
        with pytest.raises(link.LinkError) as e:
            proposals.bind_signed(db, app["user_id"], payload, purpose, int(time.time()))
    assert e.value.status == 409 and words in str(e.value)
    with store.connect() as db:
        assert proposals.bind_signed(db, app["user_id"], {"id": "f" * 32}, "answer", int(time.time())) is None


def test_with_high_security_on_a_hardware_key_allows_an_app_and_approves_as_proposed(client, owner, keys):
    """The connector's confirmations go through the code a ticket's goes through (link._use_confirmation), so high
    security's rule holds for them: the account's switch, Allow, and approving a held command as an app proposed."""
    # confirmations of the password and a phone's passkey, made while it was off (once it is on, neither is made)
    by_password, by_phone = word(client, owner), word(client, owner, keys["phone"])
    high_security(client, owner, keys)
    app_id = register(client)
    verifier, challenge = pkce()
    rid = request_id(authorize(client, app_id, challenge))
    for given in (by_password, by_phone):
        r = client.put("/api/connector/account", headers=owner, json={"on": True, "confirmation": given})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE
    r = client.put("/api/connector/account", headers=owner,
                   json={"on": True, "confirmation": word(client, owner, keys["a"], change="connector-on")})
    assert r.status_code == 200, r.text
    me = client.get("/api/auth/me", headers=owner).json()["id"]
    for given in (by_password, by_phone):
        r = client.post(f"/api/connector/requests/{rid}/allow", headers=owner,
                        json={"propose": True, "confirmation": given, "account": me})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE
    r = client.post(f"/api/connector/requests/{rid}/allow", headers=owner,
                    json={"propose": True, "account": me,
                          "confirmation": word(client, owner, keys["b"], change=f"connector-allow:{rid}:propose")})
    assert r.status_code == 200, r.text
    got = {"code": query(r.json()["redirect"])["code"], "client_id": app_id, "verifier": verifier,
           "redirect_uri": "https://claude.ai/api/mcp/auth_callback"}
    app = oauth.authenticate("Bearer " + exchange(client, got).json()["access_token"])
    identity, req = held(client, owner)
    pid = propose(app, "propose_approval", approval="approval:" + req["id"], decision="approve")
    for given, words in ((by_password, passkeys.NEEDS_HARDWARE), (by_phone, passkeys.NEEDS_HARDWARE),
                         (None, approvals.NEEDS_HARDWARE_TO_APPROVE)):
        r = client.post(f"/api/ade/approvals/{req['id']}", headers=owner,
                        json={"approve": True, "proposal": pid, "confirmation": given})
        assert r.status_code == 403 and r.json()["detail"] == words, given
    assert approvals.read_for_hook(identity, req["id"])["state"] == "pending"
    r = client.post(f"/api/ade/approvals/{req['id']}", headers=owner,
                    json={"approve": True, "proposal": pid,
                          "confirmation": word(client, owner, keys["a"], change=f"request-approve:{req['id']}")})
    assert r.status_code == 200 and r.json()["state"] == "approved"


def test_turning_high_security_on_revokes_a_connection_still_waiting_on_its_hold(client, owner, keys, monkeypatch):
    """A connection allowed with the password (or any confirmation, which names no passkey) and still waiting is one of
    the approvals turning high security on cancels: it is revoked; one already in effect stays."""
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    in_effect = connected(client, owner)
    assert oauth.authenticate("Bearer " + in_effect["access_token"])["active"] is True
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "180")
    waiting = connected(client, owner)
    high_security(client, owner, keys)
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate("Bearer " + waiting["access_token"])
    with store.connect() as db:
        why = db.execute("SELECT revoked_why FROM ade_connector_grants WHERE id = ?", (waiting["grant"],)).fetchone()[0]
    assert why == "it was cancelled before it took effect"
    assert oauth.authenticate("Bearer " + in_effect["access_token"])["id"] == in_effect["grant"]
