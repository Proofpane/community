"""The second screen's commands: ade_link.py signer pair and status, and sign, run
as the owner runs them on their Mac, against the app served on a local port. The owner's security key is the tests'
own (tests/ctap_soft.py), asked through python-fido2's real client, and the owner's terminal a test's stand-in
(test_ade_link_sign_core.Term): the terminal and the key listing are the only parts replaced, in this process, as the
core's tests replace them. A step signed on the Mac goes to the server's own routes and is taken by a session's own
take, which verifies it offline."""

import json
import os
import stat
import time
import types

import pytest

import ade_link
import ctap_soft
from proofpane_open import store
from proofpane_open.ade import signers
from test_ade_link import confirmed
from test_ade_link_client import link_row, pair, run, served  # noqa: F401  (served: a fixture)
from test_ade_link_sign_core import Term, at_a_session, composed, owner, page_adds, text_of  # noqa: F401  (fixtures)

PIN = "1234"                                    # the soft key's own PIN (ctap_soft.SoftKey)


class MacTerm(Term):
    """The owner's terminal for a whole command: Term's, and the lines typed for an instruction's words."""

    def lines(self, prompt, end, most):
        self.out.append(("lines", prompt))
        got = []
        while True:
            line = self.answers.pop(0)
            if line == end:
                return got
            got.append(line)


@pytest.fixture
def mac(monkeypatch, at_a_session):
    """The owner's Mac: a security key, the only one its USB lists, and the owner's terminal, whose answers a test sets
    (mac.typing) before it signs. A command that would sign without them fails the test."""
    box = types.SimpleNamespace(key=ctap_soft.SoftKey(), term=None)
    monkeypatch.setattr(ade_link, "_sign_devices", lambda: [ctap_soft.device(box.key)])

    def terminal(folder=None):
        assert box.term is not None, "sign reached a terminal the test did not set"
        return box.term

    def typing(*answers):
        box.term = MacTerm(list(answers), box.key)
        return box.term
    box.typing = typing
    monkeypatch.setattr(ade_link, "sign_terminal", terminal)
    return box


def home():
    return os.environ["HOME"]


def signer_folder(served):
    return os.path.join(home(), ".proofpane-ade", ".signer", served.split("://", 1)[1].replace(":", "_"))


def signer_ticket(client, owner):
    r = client.post("/api/ade/signers/tickets", headers=owner, json={"confirmation": confirmed(client, owner, "signer-ticket")})
    assert r.status_code == 200, r.text
    return r.json()


def signer_row(signer_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_signers WHERE id = ?", (signer_id,)).fetchone())


def paired_mac(client, owner, served, capsys, name="MacBook"):
    """This Mac paired as a signer: its config."""
    code, out, err = run(capsys, "signer", "pair", "--url", served, "--ticket", signer_ticket(client, owner)["ticket"],
                         "--name", name)
    assert code == 0, err
    with open(os.path.join(signer_folder(served), "config.json")) as fh:
        return json.load(fh)


def a_link_here(client, owner, served, capsys, name="mac"):
    """A session's link in this Mac's own folder of links, paired and its owner's keys pinned: its folder."""
    d = pair(client, owner, served, capsys, os.path.join(home(), ".proofpane-ade", name), name)
    assert run(capsys, "--dir", d, "pin")[0] == 0
    return d


def park(client, owner, p, **extra):
    r = client.post("/api/ade/sign-requests", headers=owner, json=dict({"payload": p}, **extra))
    assert r.status_code == 200, r.text
    return r.json()


def card(client, owner, name="mac"):
    return next(x for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"] if x["name"] == name)


def facts(view):
    return {k: view[k] for k in ("marked", "relayed_by")}


# ---- signer pair and signer status ----------------------------------------------------------------------------------

def test_signer_pair_keeps_a_key_of_its_own_and_the_address_given(client, owner, served, capsys, mac):
    made = signer_ticket(client, owner)
    code, out, err = run(capsys, "signer", "pair", "--url", served + "/", "--ticket", made["ticket"].lower(),
                         "--name", "MacBook")
    assert code == 0, err
    folder = signer_folder(served)
    config = json.load(open(os.path.join(folder, "config.json")))
    assert config["url"] == served and config["account"] == made["account"] and config["name"] == "MacBook"
    assert f"this Mac is the signer MacBook (signer {config['signer_id']})" in out
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(os.path.dirname(folder)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(os.path.join(folder, "key")).st_mode) == 0o600
    # the server holds the public half of the key kept here, and nothing else of it
    signer = ade_link.Signer(folder)
    assert signer_row(config["signer_id"])["public_key"] == ade_link.b64u(signer.public_key(signer.key()))
    assert signer.request("GET", "/owner-keys")["account"] == made["account"]
    # a link's own folder is never a signer's: a link's name cannot begin with a dot
    assert os.path.basename(os.path.dirname(folder)) == ".signer"
    # paired again with a new ticket: a new key, a new signer; the one before is still the server's until revoked
    again = paired_mac(client, owner, served, capsys)
    assert again["signer_id"] != config["signer_id"] and signer_row(config["signer_id"])["revoked_at"] is None


def test_a_used_ticket_pairs_nothing_and_what_was_kept_stays(client, owner, served, capsys, mac):
    made = signer_ticket(client, owner)
    assert run(capsys, "signer", "pair", "--url", served, "--ticket", made["ticket"], "--name", "MacBook")[0] == 0
    kept = open(os.path.join(signer_folder(served), "config.json")).read()
    code, out, err = run(capsys, "signer", "pair", "--url", served, "--ticket", made["ticket"], "--name", "Other")
    assert code == 2 and "refused (403)" in err
    assert open(os.path.join(signer_folder(served), "config.json")).read() == kept


@pytest.mark.parametrize("argv, words", [
    (["--ticket", "PP-AAAAAAA"], "a ticket is PP- and eight"),
    (["--ticket", "PP-AAAAAAA1"], "a ticket is PP- and eight"),
    (["--ticket", "PP-AAAAAAAA", "--name", "two  spaces"], "a signer's name is"),
    (["--ticket", "PP-AAAAAAAA", "--name", ".hidden"], "a signer's name is"),
    (["--ticket", "PP-AAAAAAAA", "--name", "x" * 61], "a signer's name is"),
])
def test_signer_pair_refuses_what_it_cannot_send_before_asking_the_ade(client, owner, served, capsys, mac,
                                                                         monkeypatch, argv, words):
    monkeypatch.setattr(ade_link.Link, "request", lambda *a, **kw: pytest.fail("the ADE was asked"))
    code, out, err = run(capsys, "signer", "pair", "--url", served, *argv)
    assert code == 2 and words in err and not os.path.exists(signer_folder(served))


def test_an_address_a_signer_cannot_sign_for_is_refused(capsys, mac):
    for url in ("ftp://ade.example.com", "https://ade.example.com:99999", "https://äde.example.com"):
        code, out, err = run(capsys, "signer", "pair", "--url", url, "--ticket", "PP-AAAAAAAA")
        assert code == 2 and "nothing was paired" in err, url
    assert not os.path.exists(os.path.join(home(), ".proofpane-ade", ".signer"))


def test_an_answer_for_another_address_or_account_is_kept_nowhere(served, capsys, mac, monkeypatch):
    for answer in ({"signer_id": "a" * 32, "account": "b" * 32, "url": "https://elsewhere.example"},
                   {"signer_id": "a" * 32, "account": "not an account", "url": served},
                   {"signer_id": "x", "account": "b" * 32, "url": served}, [], None):
        monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, answer=answer, **kw: answer)
        code, out, err = run(capsys, "signer", "pair", "--url", served, "--ticket", "PP-AAAAAAAA")
        assert code == 2 and "nothing was kept here" in err and "revoke it there" in err
        assert not os.path.exists(signer_folder(served))


def test_signer_status_says_live_then_revoked(client, owner, served, capsys, mac):
    assert run(capsys, "signer", "status")[0] == 2                 # none paired
    config = paired_mac(client, owner, served, capsys)
    code, out, _ = run(capsys, "signer", "status")
    assert code == 0 and out.strip().endswith(": live") and f"of account {config['account']} at {served}" in out
    assert client.delete(f"/api/ade/signers/{config['signer_id']}", headers=owner).status_code == 200
    code, out, _ = run(capsys, "signer", "status", "--url", served)
    assert code == 4 and "revoked, or not known to the ADE; pair this Mac again" in out


# ---- sign: a request the owner's page parked ------------------------------------------------------------------------

def test_sign_signs_the_request_the_owner_names_by_its_code_and_the_page_sends_it_on(client, owner, served, capsys,
                                                                                    mac):
    credential_id, _ = page_adds(client, owner, mac.key, served)
    d = a_link_here(client, owner, served, capsys)
    paired_mac(client, owner, served, capsys)
    p = composed(client, owner, served, "instruction", link_row("mac")["id"], text="Run the tests, then stop.")
    got = park(client, owner, p)
    term = mac.typing(got["code"][:4].lower(), "sign", PIN)
    code, out, err = run(capsys, "sign")
    assert code == 0, (out, err, term.said())
    said = term.said()
    assert "  | Run the tests, then stop." in said                           # the words, shown here
    assert any("this Mac's link" in line and "\"mac\"" in line for line in said)  # the target, by this Mac's record
    assert f"Signed request {got['code']} and handed it over: your page sends it on." in said
    # the page collects it, checks the payload is the one it parked, and sends both to its own route
    collected = client.get(f"/api/ade/sign-requests/{got['id']}", headers=owner).json()
    assert collected["state"] == "signed" and collected["signer"] == "MacBook"
    assert json.loads(collected["payload"]) == p and collected["credential"]["id"] == credential_id
    r = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": collected["credential"]})
    assert r.status_code == 200, r.text
    seq = r.json()["seq"]
    assert facts(next(c for c in card(client, owner)["commands"] if c["seq"] == seq)) == \
        {"marked": True, "relayed_by": "MacBook"}
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 0 and "TAKEN BY YOU AND VERIFIED FROM THE OWNER: " in out and "Run the tests, then stop." in out


def test_sign_with_a_code_takes_that_request_alone_and_names_the_rest_as_not_the_owner_s(client, owner, served, capsys,
                                                                                       mac):
    page_adds(client, owner, mac.key, served)
    a_link_here(client, owner, served, capsys)
    paired_mac(client, owner, served, capsys)
    to = link_row("mac")["id"]
    first = park(client, owner, composed(client, owner, served, "instruction", to, text="The first."))
    second = park(client, owner, composed(client, owner, served, "instruction", to, text="The second."))
    term = mac.typing("sign", PIN)
    assert run(capsys, "sign", second["code"])[0] == 0
    assert any(line.startswith("Not the request you came to sign: ") for line in term.said())
    states = {r["id"]: r["state"] for r in client.get("/api/ade/sign-requests", headers=owner).json()["requests"]}
    assert states == {first["id"]: "waiting", second["id"]: "signed"}


def test_bare_sign_waits_for_a_request_and_ends_when_its_time_does(client, owner, served, capsys, mac, monkeypatch):
    page_adds(client, owner, mac.key, served)
    a_link_here(client, owner, served, capsys)
    paired_mac(client, owner, served, capsys)
    clock = types.SimpleNamespace(now=0.0, reads=0)
    real = ade_link.Signer.request

    def counted(self, method, path, *a, **kw):
        clock.reads += path == "/requests"
        return real(self, method, path, *a, **kw)

    def sleep(seconds):
        assert seconds == ade_link.SIGN_EVERY
        clock.now += seconds
    monkeypatch.setattr(ade_link.Signer, "request", counted)
    monkeypatch.setattr(ade_link, "clock", lambda: clock.now)
    monkeypatch.setattr(ade_link, "sleep", sleep)
    monkeypatch.setattr(signers, "POLL_EVERY", 0)                     # the server's floor: here the clock is the test's
    term = mac.typing()
    code, out, err = run(capsys, "sign")
    assert code == 2 and clock.now == ade_link.SIGN_WAIT
    assert clock.reads == ade_link.SIGN_WAIT // ade_link.SIGN_EVERY + 1
    assert any("Waiting up to 300 seconds for your page to park a request" in line for line in term.said())
    assert term.said()[-1] == "ADE: No request was parked for this Mac's signature in the time sign waits. Nothing " \
                              "was signed."
    # a request the page parks while sign waits is the one it reads, once
    clock.now, clock.reads = 0.0, 0
    p = composed(client, owner, served, "instruction", link_row("mac")["id"], text="Parked while it waited.")

    def parks_on_the_third(seconds):
        sleep(seconds)
        if clock.now == 3 * ade_link.SIGN_EVERY:
            park(client, owner, p)
    monkeypatch.setattr(ade_link, "sleep", parks_on_the_third)
    code_of = ade_link.sign_code(text_of(p).encode())
    mac.typing(code_of[:4], "sign", PIN)
    assert run(capsys, "sign")[0] == 0 and clock.reads == 4


def test_a_read_the_ade_refuses_as_too_soon_is_waited_out(client, owner, served, capsys, mac, monkeypatch):
    paired_mac(client, owner, served, capsys)
    clock = types.SimpleNamespace(now=0.0)

    def sleep(seconds):
        clock.now += seconds
    monkeypatch.setattr(ade_link, "clock", lambda: clock.now)
    monkeypatch.setattr(ade_link, "sleep", sleep)
    monkeypatch.setattr(signers, "POLL_EVERY", 3600)                  # every read after the first: too soon (429)
    term = mac.typing()
    assert run(capsys, "sign")[0] == 2
    assert term.said()[-1].startswith("ADE: No request was parked for this Mac's signature in the time sign waits.")


def test_what_the_ade_says_of_the_keys_and_the_requests_binds_nothing_it_names_wrong(client, owner, served, capsys,
                                                                                  mac, monkeypatch):
    page_adds(client, owner, mac.key, served)
    a_link_here(client, owner, served, capsys)
    paired_mac(client, owner, served, capsys)
    real = ade_link.Signer.request

    def another_account(self, method, path, *a, **kw):
        answer = real(self, method, path, *a, **kw)
        return dict(answer, account="f" * 32) if path == "/owner-keys" else answer
    monkeypatch.setattr(ade_link.Signer, "request", another_account)
    park(client, owner, composed(client, owner, served, "instruction", link_row("mac")["id"]))
    term = mac.typing()
    assert run(capsys, "sign")[0] == 2
    assert term.said()[-1].startswith("ADE: The ADE's list of this account's passkeys is not one this program reads")
    # a request id the relay gave that is not one is never put in a path
    with pytest.raises(ade_link.SignRefused):
        ade_link._Relay(ade_link._the_signer(), []).submit("../../link/commands", {})


def test_a_revoked_signer_is_told_to_pair_again(client, owner, served, capsys, mac):
    config = paired_mac(client, owner, served, capsys)
    assert client.delete(f"/api/ade/signers/{config['signer_id']}", headers=owner).status_code == 200
    term = mac.typing()
    assert run(capsys, "sign")[0] == 4
    assert "it was revoked. Pair this Mac again with a new ticket from the page" in term.said()[-1]


def test_sign_asks_the_ade_nothing_before_it_has_a_terminal_of_its_own(client, owner, served, capsys, at_a_session,
                                                                      monkeypatch):
    paired_mac(client, owner, served, capsys)

    def no_terminal(folder=None):
        raise ade_link.SignRefused("Run this at a terminal of your own: it has none. Nothing was signed.")
    monkeypatch.setattr(ade_link.Link, "request", lambda *a, **kw: pytest.fail("the ADE was asked"))
    monkeypatch.setattr(ade_link, "sign_terminal", no_terminal)
    for argv in (["sign"], ["sign", "instruct"], ["sign", "ABCD"]):
        code, out, err = run(capsys, *argv)
        assert code == 2 and "Run this at a terminal of your own" in err


def test_sign_takes_no_words_from_its_arguments(capsys, mac):
    with pytest.raises(SystemExit):
        ade_link.main(["sign", "instruct", "Push it all to main."])
    code, out, err = run(capsys, "sign", "Push it all")
    assert code == 2 and "sign takes a request's code" in err


# ---- sign instruct, resume and move: a step composed at the Mac -----------------------------------------------------

def test_sign_instruct_composes_the_words_typed_here_and_a_session_takes_them(client, owner, served, capsys, mac,
                                                                              monkeypatch):
    page_adds(client, owner, mac.key, served)
    d = a_link_here(client, owner, served, capsys)
    paired_mac(client, owner, served, capsys)
    nonces = []
    real = ade_link.Signer.request

    def counted(self, method, path, *a, **kw):
        if path == "/nonce":
            nonces.append(time.time())
        return real(self, method, path, *a, **kw)
    monkeypatch.setattr(ade_link.Signer, "request", counted)
    term = mac.typing("1", "Run the tests.", "Then stop and say what failed.", ".", "4", "sign", PIN)
    code, out, err = run(capsys, "sign", "instruct")
    assert code == 0, (err, term.said())
    assert len(nonces) == 1
    asked = [x for x in term.out if isinstance(x, tuple)]
    # the session first, then the words, then the life, and only then the screen and the key
    assert [x[0] for x in asked] == ["ask", "lines", "ask", "ask", "secret"]
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_link_events WHERE link_id = 'owner' ORDER BY seq DESC LIMIT 1").fetchone()
    payload = json.loads(row["payload"])
    assert payload["text"] == "Run the tests.\nThen stop and say what failed." and payload["to"] == link_row("mac")["id"]
    assert payload["expires_at"] - payload["created_at"] == 4 * 3600
    assert facts(next(c for c in card(client, owner)["commands"] if c["seq"] == row["seq"])) == \
        {"marked": True, "relayed_by": "MacBook"}
    code, out, _ = run(capsys, "--dir", d, "take", row["seq"])
    assert code == 0 and "Then stop and say what failed." in out


def test_words_the_server_would_refuse_are_refused_before_a_nonce_is_asked(client, owner, served, capsys, mac,
                                                                          monkeypatch):
    page_adds(client, owner, mac.key, served)
    a_link_here(client, owner, served, capsys)
    paired_mac(client, owner, served, capsys)
    real = ade_link.Signer.request
    monkeypatch.setattr(ade_link.Signer, "request", lambda self, method, path, *a, **kw:
                        pytest.fail("a nonce was asked") if path == "/nonce" else real(self, method, path, *a, **kw))
    for words in ("Run it\u202e", "", "Use token ghp_" + "a" * 36):
        term = mac.typing("1", words, ".", "1")
        assert run(capsys, "sign", "instruct")[0] == 2
        assert term.said()[-1].startswith("ADE: These words cannot be signed: "), term.said()
    term = mac.typing("1", "Fine words.", ".", "3")
    assert run(capsys, "sign", "instruct")[0] == 2
    assert term.said()[-1] == "ADE: A command expires in 1, 4, 8 or 24 hours. Nothing was signed."


def test_sign_resume_lifts_a_stop_and_the_session_s_hook_takes_it(client, owner, served, capsys, mac):
    page_adds(client, owner, mac.key, served)
    d = a_link_here(client, owner, served, capsys)
    paired_mac(client, owner, served, capsys)
    link_id = link_row("mac")["id"]
    stopped = client.post(f"/api/ade/link/links/{link_id}/stop", headers=owner).json()["stopped_at"]
    term = mac.typing("1", "sign", PIN)
    code, out, err = run(capsys, "sign", "resume")
    assert code == 0, (err, term.said())
    assert link_row("mac")["stopped_at"] is None
    resume = {"payload": link_row("mac")["resume_payload"], "signature": json.loads(link_row("mac")["resume_signature"])}
    assert json.loads(resume["payload"])["created_at"] > stopped
    assert ade_link.verify_resume(ade_link.Link(d), resume, after=stopped) == json.loads(resume["payload"])["id"]
    assert card(client, owner)["resume_signed"]["relayed_by"] == "MacBook"


def test_sign_resume_offers_only_the_stopped_sessions(client, owner, served, capsys, mac):
    page_adds(client, owner, mac.key, served)
    a_link_here(client, owner, served, capsys)
    paired_mac(client, owner, served, capsys)
    term = mac.typing()
    assert run(capsys, "sign", "resume")[0] == 2
    assert term.said()[-1] == "ADE: No linked session is stopped. Nothing was signed."


def test_sign_move_offers_this_mac_s_sessions_and_signs_the_one_chosen(client, owner, served, capsys, mac,
                                                                       monkeypatch, tmp_path):
    page_adds(client, owner, mac.key, served)
    a_link_here(client, owner, served, capsys)
    pair(client, owner, served, capsys, tmp_path / "not-on-this-mac", "elsewhere")     # a link of another machine's
    paired_mac(client, owner, served, capsys)
    projects = os.path.join(home(), ".claude", "projects")
    for folder, sid, when in (("-work-a", "older-one", 100), ("-work-b", "newer-one", 200)):
        os.makedirs(os.path.join(projects, folder), exist_ok=True)
        path = os.path.join(projects, folder, sid + ".jsonl")
        open(path, "w").write("{}\n")
        os.utime(path, (when, when))
    sent = []
    real = ade_link.Signer.request

    def watched(self, method, path, body=None, *a, **kw):
        if path == "/commands":
            sent.append(body["payload"])
        return real(self, method, path, body, *a, **kw)
    monkeypatch.setattr(ade_link.Signer, "request", watched)
    term = mac.typing("1", "1", "sign", PIN)
    code = run(capsys, "sign", "move")[0]
    said = term.said()
    assert not any("elsewhere" in line for line in said)              # only this Mac's own links move its sessions
    assert said.index(next(x for x in said if "newer-one" in x)) < said.index(next(x for x in said if "older-one" in x))
    # the move was signed for the newest session and sent; the ADE refuses it, as it refuses the page's, where the
    # hosted workspace is not on or the link's History list does not have the session
    assert [p["session"] for p in sent] == ["newer-one"] and sent[0]["purpose"] == "move"
    assert code == 2 and said[-1].startswith("ADE: refused (4")
