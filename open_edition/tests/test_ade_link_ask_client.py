"""The asking session's side (ade_link.py pair --ask), against the app served on a local port: it makes its key pair as
pair does, asks with a request signed by it, prints the key's fingerprint and where to approve it, then waits saying
nothing more; linked, it writes its directory as pair does and pins the owner keys the ADE gives, printing them;
refused, expired, cancelled or not made, it says which and exits 2. The owner acts, and time passes, at the client's
waits: its sleep is the test's, and moves the clock the client, the ADE and every signature read."""

import hashlib
import json
import os
import re
import stat
import time

import pytest

import ade_link
from proofpane_open import store
from proofpane_open.ade import link_asks
from test_ade_link import Session, at_once, confirmed, ticket
from test_ade_link_client import link_row, run, served  # noqa: F401  (served: a fixture)
from test_ade_link_client_commands import command, device, no_inbox, owner  # noqa: F401  fixtures
from test_passkeys import Authenticator, add

REF = "https://claude.ai/code/session_01ABC"
FIRST = "ADE: approve it in the ADE's Linked sessions, on the request that shows this fingerprint; waiting up to 30 minutes"


@pytest.fixture
def waits(monkeypatch):
    """The client's waits, as the test plays them: each moves the shared clock by the seconds asked, then runs what is
    queued for that wait (by its number, from 1). {now, slept: the seconds of each wait, at: {n: what happens}}."""
    t = {"now": int(time.time()), "slept": [], "at": {}}
    monkeypatch.setattr(time, "time", lambda: t["now"])
    monkeypatch.setattr(ade_link, "clock", lambda: t["now"])

    def sleep(seconds):
        t["slept"].append(seconds)
        t["now"] += seconds
        happen = t["at"].pop(len(t["slept"]), None)
        if happen:
            happen()

    monkeypatch.setattr(ade_link, "sleep", sleep)
    return t


def address(client, owner):
    return client.get("/api/ade/link/address", headers=owner).json()["address"]


def the_ask(client, owner):
    (seen,) = client.get("/api/ade/link/sessions", headers=owner).json()["asks"]
    return seen


def approves(client, owner):
    def act():
        ask_id = the_ask(client, owner)["id"]
        r = client.post(f"/api/ade/link/asks/{ask_id}/approve", headers=owner,
                        json={"confirmation": confirmed(client, owner, f"link-approve:{ask_id}")})
        assert r.status_code == 200, r.text
    return act


def asking(capsys, served, addr, d, *more, name="cloud", kind="cloud"):
    return run(capsys, "pair", "--url", served, "--ask", addr, "--name", name, "--kind", kind, "--ref", REF,
               "--dir", d, *more)


def fingerprint_in(d):
    link = ade_link.Link(str(d))
    return hashlib.sha256(link.public_key(link.key())).hexdigest()[:32]


def account_keys(client, owner):
    return [k["fingerprint"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]]


def test_a_session_asks_waits_through_the_hold_and_links_as_pair_does(client, owner, served, device, waits, capsys,
                                                                     tmp_path):
    waits["at"][1] = approves(client, owner)
    d = tmp_path / "links" / "cloud"
    code, out, err = asking(capsys, served, address(client, owner), d)
    assert code == 0, err
    made = link_row("cloud")
    fp, (key,) = fingerprint_in(d), account_keys(client, owner)
    assert out.splitlines() == [
        f"ADE: asked to be linked as cloud (cloud); this session's key fingerprint is {fp}",
        FIRST,
        f"ADE: approved and linked as cloud (link {made['id']}); directory {d}",           # nothing in between
        f"ADE: pinned {key} (Ana's iPhone)",
        f"ADE: 1 owner key(s) pinned for link {made['id']}: commands signed with them are verified",
        "ADE: these owner keys are the ADE's word, given as it linked this session (trust on first use), not keys a "
        "page put in the command: compare each fingerprint with the account's list in Account",
        # how a cloud link stays heard: no keeper here, as ADE_LINK_KEEP=0 keeps every test from one
        "ADE: a cloud session runs no hook of the ADE's, and no background heartbeat keeps this link heard here "
        "(ADE_LINK_KEEP is 0 here): it is heard only when this session calls this client"]
    # it polled every few seconds until the hold ended, three minutes after the approval
    assert set(waits["slept"]) == {ade_link.ASK_EVERY}
    assert link_asks.HOLD_DEFAULT <= sum(waits["slept"]) <= link_asks.HOLD_DEFAULT + 2 * ade_link.ASK_EVERY
    # the directory, as pair writes it (the account the answer named, which e2e.json is bound to)
    assert json.loads((d / "config.json").read_text()) == {"url": served, "name": "cloud", "kind": "cloud",
                                                           "link_id": made["id"], "account": made["user_id"]}
    assert re.fullmatch(r"[0-9a-f]{32}", (d / "instance").read_text())
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700 and stat.S_IMODE(os.stat(d / "key").st_mode) == 0o600
    assert (made["kind"], made["session_ref"]) == ("cloud", REF)
    kept = json.loads((d / "owner_keys.json").read_text())
    assert kept["source"] == "server" and [k["fingerprint"] for k in kept["keys"]] == [key]
    # and it is a link: its key signs, and the owner's command verifies against the key it pinned
    assert run(capsys, "--dir", d, "heartbeat")[0] == 0
    seq = command(client, owner, served, device, made["id"])
    assert run(capsys, "--dir", d, "show", seq)[0] == 0


@pytest.mark.parametrize("ending, said", [
    ("refused", "ADE: not linked: the owner refused the request.\n"),
    ("cancelled", "ADE: not linked: the owner cancelled the approval before it took effect.\n"),
    ("expired", "ADE: not linked: the request expired before the owner answered it.\n"),
])
def test_refused_cancelled_or_expired_it_says_which_and_exits_2(client, owner, served, waits, capsys, tmp_path,
                                                               monkeypatch, ending, said):
    if ending == "expired":
        monkeypatch.setattr(ade_link, "ASK_EVERY", 60)          # fewer polls over the same half hour
    if ending == "refused":
        waits["at"][2] = lambda: client.post(f"/api/ade/link/asks/{the_ask(client, owner)['id']}/refuse", headers=owner)
    elif ending == "cancelled":
        waits["at"][1] = approves(client, owner)
        waits["at"][3] = lambda: client.post(f"/api/ade/holds/{the_ask(client, owner)['hold_id']}/cancel",
                                             headers=owner)
    d = tmp_path / "cloud"
    code, out, err = asking(capsys, served, address(client, owner), d)
    assert (code, err) == (2, said) and out.splitlines()[1:] == [FIRST]
    assert not (d / "config.json").exists() and (d / "key").exists()
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_links").fetchone()[0] == 0
    if ending == "expired":
        assert sum(waits["slept"]) == link_asks.ASK_TTL


def test_linked_and_revoked_before_it_read_linked_it_says_so_and_exits_2(client, owner, served, waits, capsys, tmp_path):
    def made_and_revoked():
        (made,) = client.get("/api/ade/link/sessions", headers=owner).json()["links"]  # the page's read makes it
        r = client.request("DELETE", f"/api/ade/link/links/{made['id']}", headers=owner,
                           json={"confirmation": confirmed(client, owner, f"link-revoke:{made['id']}")})
        assert r.status_code == 200, r.text

    waits["at"][1] = approves(client, owner)
    waits["at"][1 + link_asks.HOLD_DEFAULT // ade_link.ASK_EVERY] = made_and_revoked     # the hold's end, before a poll
    d = tmp_path / "cloud"
    code, out, err = asking(capsys, served, address(client, owner), d)
    assert (code, err) == (2, "ADE: not linked: the link it made is no longer live with this key (revoked since, or "
                              "moved to another key).\n")
    assert not (d / "config.json").exists()


def test_an_address_no_account_has_waits_and_expires_as_one_never_answered(client, owner, served, waits, capsys,
                                                                          tmp_path, monkeypatch):
    monkeypatch.setattr(ade_link, "ASK_EVERY", 60)              # fewer polls over the same half hour
    code, out, err = asking(capsys, served, "ask-" + "a" * 20, tmp_path / "cloud")
    assert code == 2 and err == "ADE: not linked: the request expired before the owner answered it.\n"
    assert out.splitlines()[1] == FIRST and sum(waits["slept"]) == link_asks.ASK_TTL


def test_an_approval_whose_hold_ends_past_the_thirty_minutes_is_waited_for(client, owner, served, device, waits,
                                                                          capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(ade_link, "ASK_EVERY", 60)              # fewer polls over the same half hour
    last = (link_asks.ASK_TTL - 30) // ade_link.ASK_EVERY       # a wait in the request's last minute
    waits["at"][last] = approves(client, owner)
    code, out, err = asking(capsys, served, address(client, owner), tmp_path / "cloud")
    assert code == 0, err
    assert sum(waits["slept"]) > link_asks.ASK_TTL and "approved and linked as cloud" in out


def test_a_cloud_session_back_under_a_live_cloud_link_s_name_replaces_it_and_the_old_key_is_refused(
        client, owner, served, waits, capsys, tmp_path):
    before = tmp_path / "before"            # the session that expired, linked with a ticket
    with at_once():                         # linked as it pairs, with no wait of the test's clock
        code, out, err = run(capsys, "pair", "--url", served, "--ticket", ticket(client, owner), "--name", "cloud",
                             "--kind", "cloud", "--dir", before)
    assert code == 0, err
    old = link_row("cloud")
    assert run(capsys, "--dir", before, "heartbeat")[0] == 0

    def approves_replacing():
        ask_id = the_ask(client, owner)["id"]
        r = client.post(f"/api/ade/link/asks/{ask_id}/approve", headers=owner,
                        json={"confirmation": confirmed(client, owner, f"link-approve:{ask_id}:{old['id']}"),
                              "replace": old["id"]})
        assert r.status_code == 200, r.text

    waits["at"][1] = approves_replacing
    d = tmp_path / "after"
    code, out, err = asking(capsys, served, address(client, owner), d)
    assert code == 0, err
    made = link_row("cloud")
    assert made["id"] != old["id"]
    assert out.splitlines()[2] == f"ADE: approved and linked as cloud (link {made['id']}); directory {d}"
    assert link_asks.HOLD_DEFAULT <= sum(waits["slept"]) <= link_asks.HOLD_DEFAULT + 2 * ade_link.ASK_EVERY
    code, out, err = run(capsys, "--dir", before, "heartbeat")
    assert code == 4 and err == "ADE: refused (401): No live link has this key: pair again.\n"
    assert run(capsys, "--dir", d, "heartbeat")[0] == 0


@pytest.mark.parametrize("again_as", ["cloud", "another"])
def test_a_session_whose_client_stopped_before_it_read_linked_is_linked_by_running_it_again(
        client, owner, served, waits, capsys, tmp_path, monkeypatch, again_as):
    def timed_out():                # a coding tool's command timeout, shorter than the hold: the client is stopped
        raise SystemExit(124)

    waits["at"][1] = approves(client, owner)
    # the hold ends 180 seconds after the approval at the first wait: stopped at the wait after which it would read it
    waits["at"][1 + link_asks.HOLD_DEFAULT // ade_link.ASK_EVERY] = timed_out
    d = tmp_path / "cloud"
    with pytest.raises(SystemExit):
        asking(capsys, served, address(client, owner), d)
    capsys.readouterr()
    assert not (d / "config.json").exists() and (d / "key").exists()
    client.get("/api/ade/link/sessions", headers=owner)         # the page's read: the hold has ended, the link is made
    made = link_row("cloud")
    sent, real = [], ade_link.Link.request

    def recorded(self, method, path, *args, **kwargs):
        sent.append((method, path))
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", recorded)
    code, out, err = asking(capsys, served, address(client, owner), d, name=again_as)
    if again_as != "cloud":         # the key's link is under another name: not taken for this one
        assert code == 2 and "already on a live link" in err and not (d / "config.json").exists()
        return
    assert code == 0, err
    # where its request stood; nothing asked anew (beside the read of the owner's encryption keys pairing makes)
    assert sent[0] == ("GET", "/ask") and set(sent[1:]) <= {("GET", "/e2e")}
    assert out.splitlines()[:2] == [
        f"ADE: this session's key asked to be linked as cloud before; its fingerprint is {fingerprint_in(d)}",
        f"ADE: approved and linked as cloud (link {made['id']}); directory {d}"]
    assert json.loads((d / "config.json").read_text()) == {"url": served, "name": "cloud", "kind": "cloud",
                                                           "link_id": made["id"], "account": made["user_id"]}
    monkeypatch.setattr(ade_link.Link, "request", real)
    assert run(capsys, "--dir", d, "heartbeat")[0] == 0


def test_run_again_after_a_refusal_it_asks_anew_with_its_key(client, owner, served, waits, capsys, tmp_path,
                                                            monkeypatch):
    sent, real = [], ade_link.Link.request

    def recorded(self, method, path, *args, **kwargs):
        sent.append((method, path))
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", recorded)
    waits["at"][1] = lambda: client.post(f"/api/ade/link/asks/{the_ask(client, owner)['id']}/refuse", headers=owner)
    d = tmp_path / "cloud"
    assert asking(capsys, served, address(client, owner), d)[0] == 2
    assert sent[0] == ("POST", "/ask")                  # a new key asks at once: it has no request to read
    del sent[:]
    waits["at"][len(waits["slept"]) + 1] = approves(client, owner)
    code, out, err = asking(capsys, served, address(client, owner), d)
    assert code == 0, err
    assert sent[:2] == [("GET", "/ask"), ("POST", "/ask")]     # its request was refused: it asks anew
    assert out.splitlines()[0] == ("ADE: asked to be linked as cloud (cloud); this session's key fingerprint is "
                                   f"{fingerprint_in(d)}")
    with store.connect() as db:
        assert [r[0] for r in db.execute("SELECT state FROM ade_link_asks ORDER BY created_at, rowid")] == \
            ["refused", "linked"]


def test_a_file_that_cannot_be_written_after_config_json_is_said_as_linked_but(client, owner, served, waits, capsys,
                                                                              tmp_path, monkeypatch):
    real = ade_link.Link.keep

    def keep(self, name, value):
        if name == "instance":
            raise OSError("the disk is full")
        return real(self, name, value)

    monkeypatch.setattr(ade_link.Link, "keep", keep)
    waits["at"][1] = approves(client, owner)
    d = tmp_path / "cloud"
    code, out, err = asking(capsys, served, address(client, owner), d)
    made = link_row("cloud")
    assert (code, err) == (1, f"ADE: linked as cloud (link {made['id']}), but a file of the link's directory could not "
                              f"be written after its config.json (OSError): mend {d}, then pin the owner's keys with "
                              "pin.\n")
    assert json.loads((d / "config.json").read_text())["link_id"] == made["id"]


@pytest.mark.parametrize("where", ["file", "environment"])
def test_a_pin_held_outside_the_server_names_the_keys_it_leaves_out_and_where_to_add_them(
        client, owner, served, device, waits, capsys, tmp_path, monkeypatch, where):
    if where == "file":
        home = tmp_path / "home"
        (home / ".proofpane-ade").mkdir(parents=True)
        (home / ".proofpane-ade" / "owner-keys").write_text("a" * 32 + "\n")
        monkeypatch.setenv("HOME", str(home))
        pin = f"the owner-keys pin {home / '.proofpane-ade' / 'owner-keys'}"
    else:
        monkeypatch.setenv("ADE_OWNER_KEYS", "a" * 32)
        pin = "the owner-keys pin in ADE_OWNER_KEYS"
    (key,) = account_keys(client, owner)
    waits["at"][1] = approves(client, owner)
    code, out, err = asking(capsys, served, address(client, owner), tmp_path / "cloud")
    assert code == 0, err
    said = out.splitlines()
    assert f"ADE: not pinned {key}: {pin} does not list it; add it there to pin it" in said
    assert (f"ADE: nothing was pinned: {pin} lists none of the keys the ADE gives; add a key's fingerprint there to "
            "pin it.") in said
    assert "pin --add" not in out


def test_a_name_taken_while_the_approval_waits_is_said_and_exits_2(client, owner, served, waits, capsys, tmp_path):
    def a_ticket_takes_the_name():          # linked as it pairs (a pairing's own wait: test_ade_link_pair_wait.py)
        with at_once():
            r = Session(client).pair(ticket(client, owner), name="cloud", kind="cloud", host=served.split("://", 1)[1])
        assert r.json()["status"] == "linked", r.text

    waits["at"][1] = approves(client, owner)
    waits["at"][2] = a_ticket_takes_the_name
    code, out, err = asking(capsys, served, address(client, owner), tmp_path / "cloud")
    assert code == 2 and err.startswith("ADE: not linked: the owner approved it, and the link could not be made (")
    assert "already has a live link named cloud" in err


def test_with_keys_named_it_pins_those_alone_as_a_pair_command_from_the_page_does(client, owner, served, device,
                                                                                 waits, capsys, tmp_path):
    second = Authenticator(alg=-8, rp="127.0.0.1", origin=served)
    assert add(client, owner, second, confirm_with=device).status_code == 200
    first, other = account_keys(client, owner)
    waits["at"][1] = approves(client, owner)
    d = tmp_path / "cloud"
    code, out, err = asking(capsys, served, address(client, owner), d, "--keys", first)
    assert code == 0, err
    kept = json.loads((d / "owner_keys.json").read_text())
    assert kept["source"] == "pair" and [k["fingerprint"] for k in kept["keys"]] == [first]
    assert "trust on first use" not in out and f"pinned {other}" not in out


def test_polls_the_ade_refuses_for_their_pace_or_cannot_answer_are_waited_through(client, owner, served, device,
                                                                                 waits, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(ade_link, "ASK_EVERY", 1)                # under the ADE's two seconds: every other is a 429
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECT_HOLD", "20")    # a short hold: fewer polls
    real = ade_link.Link.request
    seen = []

    def flaky(self, method, path, *args, **kwargs):
        if method == "GET" and len(seen) % 5 == 4:
            seen.append("no answer")
            raise ade_link.Unreachable("no route")
        try:
            got = real(self, method, path, *args, **kwargs)
        except ade_link.Refused as e:
            seen.append(e.status)
            raise
        seen.append(200)
        return got

    monkeypatch.setattr(ade_link.Link, "request", flaky)
    waits["at"][1] = approves(client, owner)
    code, out, err = asking(capsys, served, address(client, owner), tmp_path / "cloud")
    assert code == 0, err
    assert "approved and linked as cloud" in out and 429 in seen and "no answer" in seen
    assert 20 <= sum(waits["slept"]) < link_asks.HOLD_DEFAULT


def test_with_no_answer_at_all_it_gives_up_after_the_time_it_waits(client, owner, served, waits, capsys, tmp_path,
                                                                  monkeypatch):
    real = ade_link.Link.request

    def silent(self, method, path, *args, **kwargs):
        if method == "GET":
            raise ade_link.Unreachable("no route")
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", silent)
    code, out, err = asking(capsys, served, address(client, owner), tmp_path / "cloud")
    assert (code, err) == (3, "ADE: not linked: no outcome within the time this client waits.\n")
    assert sum(waits["slept"]) == ade_link.ASK_WAIT


@pytest.mark.parametrize("then", ["no answer", "busy", "the same answer again"])
def test_approved_and_then_answered_no_more_or_alike_it_gives_up_at_the_hold_s_end_and_a_minute_more(
        client, owner, served, waits, capsys, tmp_path, monkeypatch, then):
    """The first answer that names the hold's end sets the wait's end, once: no poll left unanswered after it, and no
    answer that says the same each time, keeps the client waiting past it. In older releases each turn of the wait moved
    the end on by its own length while the last answer stood, so it never came."""
    monkeypatch.setattr(ade_link, "ASK_WAIT", 60)                # the request's own time short: the hold's end counts
    real, approved = ade_link.Link.request, []

    def stalls(self, method, path, *args, **kwargs):
        if method == "GET" and path == "/ask" and approved:
            if then == "no answer":
                raise ade_link.Unreachable("no route")
            if then == "busy":
                raise ade_link.Refused(503, "The ADE is busy.")
            return dict(approved[0])                             # its time and all, as an ADE whose clock stood still
        got = real(self, method, path, *args, **kwargs)
        if isinstance(got, dict) and got.get("status") == "approved":
            approved.append(got)
        return got

    def stuck():
        raise AssertionError("it still waits, 200 polls on")

    monkeypatch.setattr(ade_link.Link, "request", stalls)
    waits["at"][1] = approves(client, owner)                     # approved at the end of the first wait
    waits["at"][200] = stuck
    code, out, err = asking(capsys, served, address(client, owner), tmp_path / "cloud")
    assert (code, err) == (3, "ADE: not linked: no outcome within the time this client waits.\n")
    end = ade_link.ASK_EVERY + link_asks.HOLD_DEFAULT + ade_link.ASK_GRACE
    assert end <= sum(waits["slept"]) < end + ade_link.ASK_EVERY and len(approved) == 1


def test_an_approved_answer_without_its_times_does_not_set_the_end_and_the_next_that_names_them_does(
        client, owner, served, waits, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(ade_link, "ASK_WAIT", 60)                # the request's own time short: the hold's end counts
    real, approved = ade_link.Link.request, []

    def first_without(self, method, path, *args, **kwargs):
        got = real(self, method, path, *args, **kwargs)
        if isinstance(got, dict) and got.get("status") == "approved":
            approved.append(got)
            if len(approved) == 1:                               # the first says it is approved, and not when
                return {k: v for k, v in got.items() if k != "server_time"}
        return got

    monkeypatch.setattr(ade_link.Link, "request", first_without)
    waits["at"][1] = approves(client, owner)
    code, out, err = asking(capsys, served, address(client, owner), tmp_path / "cloud")
    assert code == 0, err
    assert "approved and linked as cloud" in out and len(approved) > 2
    assert link_asks.HOLD_DEFAULT <= sum(waits["slept"]) <= link_asks.HOLD_DEFAULT + 2 * ade_link.ASK_EVERY


def test_an_answer_this_client_cannot_read_is_one_line_never_a_traceback(client, owner, served, waits, capsys,
                                                                         tmp_path, monkeypatch):
    real = ade_link.Link.request

    def odd(self, method, path, *args, **kwargs):
        return {"status": ["linked"], "link_id": "0" * 32} if method == "GET" else real(self, method, path, *args,
                                                                                         **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", odd)
    code, out, err = asking(capsys, served, address(client, owner), tmp_path / "cloud")
    assert (code, err) == (3, "ADE: the ADE gave an answer this client cannot read; not linked.\n")
    assert not (tmp_path / "cloud" / "config.json").exists()


def test_an_ask_the_ade_refuses_is_said_at_once_and_nothing_waits(client, owner, served, waits, capsys, tmp_path):
    code, out, err = asking(capsys, served, "ask-not-an-address", tmp_path / "cloud")
    assert code == 2 and "refused (422)" in err and "not an ask address" in err and out == ""
    assert waits["slept"] == []


def test_ticket_and_ask_are_one_or_the_other(capsys, tmp_path):
    with pytest.raises(SystemExit):
        ade_link.main(["pair", "--url", "https://x.example", "--ticket", "PP-AAAAAAAA", "--ask", "ask-" + "a" * 20,
                       "--name", "n", "--kind", "cloud", "--dir", str(tmp_path / "x")])
    with pytest.raises(SystemExit):
        ade_link.main(["pair", "--url", "https://x.example", "--name", "n", "--kind", "cloud", "--dir",
                       str(tmp_path / "x")])
    capsys.readouterr()
