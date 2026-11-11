"""The pairing session's side of a pairing that waits (ade_link.py pair --ticket),
against the app served on a local port: it pairs saying it waits, prints its key's fingerprint and when the ADE links it,
polls, says once a minute that it still waits, and once linked writes its directory and pins the owner keys the pair
command names, as pair always did. Cancelled or not made, it says which and exits 2; with no outcome by the hold's end
and a minute more, 3. Stopped while it waited and run again in the same directory, it reads where its key's pairing
stands first: linked, it finishes; still waiting, it waits on; otherwise it pairs anew, and a used ticket is refused.
Time passes at the client's waits: its sleep is the test's, and moves the clock the client, the ADE and every signature
read."""

import hashlib
import json
import re
import time

import pytest

import ade_link
from proofpane_open import store
from proofpane_open.ade import holds, link_asks
from test_ade_link import at_once, confirmed, revoke, ticket
from test_ade_link_ask_client import waits  # noqa: F401  (a fixture)
from test_ade_link_client import link_row, run, served  # noqa: F401  (served: a fixture)
from test_ade_link_client_commands import device, no_inbox, owner  # noqa: F401  fixtures

WAITING = ("ADE: the ADE links it at {} unless the owner cancels it; waiting (if this is stopped, run the same command "
           "again).")
NOT_READ = ("ADE: where this session's pairing stands could not be read ({}): run the same command again; the ticket is "
            "not sent twice.\n")
STILL = "ADE: still waiting: linked at {}."


def pairs(capsys, served, t, d, *more, name="mac", kind="local"):
    return run(capsys, "pair", "--url", served, "--ticket", t, "--name", name, "--kind", kind, "--dir", d, *more)


def fingerprint_in(d):
    link = ade_link.Link(str(d))
    return hashlib.sha256(link.public_key(link.key())).hexdigest()[:32]


def utc(ts):
    return time.strftime("%H:%M:%S UTC", time.gmtime(ts))


def the_pairing(client, owner):
    (one,) = client.get("/api/ade/link/sessions", headers=owner).json()["asks"]
    return one


def recording(monkeypatch, fail=None):
    """The client's requests, as (method, path), from here on; `fail`, when given, is run before each and may raise in
    its place (an ADE that answers nothing, say)."""
    sent, real = [], ade_link.Link.request

    def recorded(self, method, path, *args, **kwargs):
        sent.append((method, path))
        if fail is not None:
            fail(method, path)
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", recorded)
    return sent


def stopped_while_it_waits(capsys, served, waits, t, d, *more):
    """A pair with ticket `t` stopped at its second wait, as a coding tool's command timeout stops one shorter than the
    hold: its pairing made and waiting, its directory holding the key and the ticket's mark."""
    def stopped():
        raise SystemExit(124)

    waits["at"][len(waits["slept"]) + 2] = stopped
    with pytest.raises(SystemExit):
        pairs(capsys, served, t, d, *more)
    capsys.readouterr()
    assert (d / "key").exists() and (d / ade_link.PAIRING).exists()


def test_it_waits_through_the_hold_saying_when_and_links_as_pair_did(client, owner, served, device, waits, capsys,
                                                                     tmp_path):
    t = client.post("/api/ade/link/tickets", headers=owner,
                    json={"confirmation": confirmed(client, owner)}).json()
    d = tmp_path / "links" / "mac"
    start = waits["now"]
    code, out, err = pairs(capsys, served, t["ticket"], d, "--keys", ",".join(t["keys"]))
    assert code == 0, err
    made = link_row("mac")
    at = utc(start + link_asks.HOLD_DEFAULT)
    lines = out.splitlines()
    assert lines[:5] == [
        f"ADE: paired as mac (local); this session's key fingerprint is {fingerprint_in(d)}",
        WAITING.format(at), STILL.format(at), STILL.format(at),            # once a minute, while it waits
        f"ADE: linked as mac (link {made['id']}); directory {d}"]
    assert lines[5].startswith("ADE: pinned ") and "1 owner key(s) pinned" in lines[6]
    # it polled every few seconds until the hold ended, three minutes after it paired, and asked nothing else
    assert set(waits["slept"]) == {ade_link.ASK_EVERY}
    assert link_asks.HOLD_DEFAULT <= sum(waits["slept"]) <= link_asks.HOLD_DEFAULT + ade_link.ASK_EVERY
    # the directory, as pair writes it, and a link whose key signs
    assert json.loads((d / "config.json").read_text()) == {"url": served, "name": "mac", "kind": "local",
                                                           "link_id": made["id"], "account": made["user_id"]}
    assert re.fullmatch(r"[0-9a-f]{32}", (d / "instance").read_text())
    assert run(capsys, "--dir", d, "heartbeat")[0] == 0


def test_held_for_no_time_it_is_linked_as_it_pairs(client, owner, served, waits, capsys, tmp_path):
    d = tmp_path / "mac"
    with at_once():
        code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert code == 0, err
    assert out.splitlines() == [
        f"ADE: paired as mac (local); this session's key fingerprint is {fingerprint_in(d)}",
        f"ADE: linked as mac (link {link_row('mac')['id']}); directory {d}"]
    assert waits["slept"] == []


def test_cancelled_it_says_so_and_exits_2_with_nothing_written(client, owner, served, waits, capsys, tmp_path):
    def cancels():
        r = client.post(f"/api/ade/holds/{the_pairing(client, owner)['hold_id']}/cancel", headers=owner)
        assert r.status_code == 200, r.text

    waits["at"][1] = cancels
    d = tmp_path / "mac"
    code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert (code, err) == (2, "ADE: not linked: the owner cancelled the pairing before it took effect.\n")
    assert not (d / "config.json").exists() and len(waits["slept"]) == 1


def test_not_made_it_says_why_and_exits_2(client, owner, served, waits, capsys, tmp_path):
    def the_name_is_taken():
        with at_once():
            assert pairs(capsys, served, ticket(client, owner), tmp_path / "other")[0] == 0

    waits["at"][1] = the_name_is_taken
    d = tmp_path / "mac"
    code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert code == 2 and not (d / "config.json").exists()
    assert err == ("ADE: not linked: the pairing could not be made (The account already has a live link named "
                   "mac.).\n")


def test_with_no_outcome_by_the_hold_s_end_and_a_minute_more_it_exits_3(client, owner, served, waits, capsys,
                                                                       tmp_path, monkeypatch):
    monkeypatch.setattr(holds, "settle", lambda *a, **k: [])        # an ADE that never makes the link

    def stuck():                            # a wait whose end moves on fails here, rather than never ending
        raise AssertionError("it still waits, 200 polls on")

    waits["at"][200] = stuck
    d = tmp_path / "mac"
    code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert (code, err) == (3, "ADE: not linked: no outcome within the time this client waits.\n")
    waited = sum(waits["slept"])
    assert link_asks.HOLD_DEFAULT + ade_link.ASK_GRACE <= waited < link_asks.HOLD_DEFAULT + ade_link.ASK_GRACE + 10
    assert not (d / "config.json").exists()


@pytest.mark.parametrize("then", ["no answer", "busy", "the same answer again"])
def test_an_ade_that_answers_no_more_or_alike_ends_the_wait_at_the_hold_s_end_and_a_minute_more(
        client, owner, served, waits, capsys, tmp_path, monkeypatch, then):
    """The wait's end is set by the ADE's first answer: no poll left unanswered, and no answer that says the same, keeps
    the client waiting past it."""
    real, first = ade_link.Link.request, []

    def stalls(self, method, path, *args, **kwargs):
        if method == "GET" and path == "/ask":
            if then == "no answer":
                raise ade_link.Unreachable("connection refused")
            if then == "busy":
                raise ade_link.Refused(503, "The ADE is busy.")
            return dict(first[0])                   # its time and all, as an ADE whose clock stood still
        first.append(real(self, method, path, *args, **kwargs))
        return first[0]

    def stuck():
        raise AssertionError("it still waits, 200 polls on")

    monkeypatch.setattr(ade_link.Link, "request", stalls)
    waits["at"][200] = stuck
    code, out, err = pairs(capsys, served, ticket(client, owner), tmp_path / "mac")
    assert (code, err) == (3, "ADE: not linked: no outcome within the time this client waits.\n")
    waited = sum(waits["slept"])
    assert link_asks.HOLD_DEFAULT + ade_link.ASK_GRACE <= waited < link_asks.HOLD_DEFAULT + ade_link.ASK_GRACE + 10


def test_a_first_answer_that_says_its_hold_is_over_still_waits_a_minute_for_the_link(client, owner, served, waits,
                                                                                    capsys, tmp_path, monkeypatch):
    monkeypatch.setenv(link_asks.HOLD_SETTING, "30")
    real = ade_link.Link.request

    def late(self, method, path, *args, **kwargs):
        answer = real(self, method, path, *args, **kwargs)
        if path == "/pair":                                         # by its word, the hold ended ten minutes ago
            answer = dict(answer, server_time=answer["takes_effect_at"] + 600)
        return answer

    monkeypatch.setattr(ade_link.Link, "request", late)
    d = tmp_path / "mac"
    code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert code == 0, err
    assert out.splitlines()[-1] == f"ADE: linked as mac (link {link_row('mac')['id']}); directory {d}"
    assert 30 <= sum(waits["slept"]) <= 30 + ade_link.ASK_EVERY


def test_an_answer_naming_a_hold_longer_than_any_a_deployment_sets_is_waited_on_no_longer(client, owner, served, waits,
                                                                                          capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(ade_link, "HOLD_MOST", 100)             # the longest hold there is, made short here
    real = ade_link.Link.request

    def endless(self, method, path, *args, **kwargs):
        answer = real(self, method, path, *args, **kwargs)
        if path == "/pair":                                     # by its word, a hold of ten days
            answer = dict(answer, takes_effect_at=answer["server_time"] + 10 * 86400)
        return answer

    monkeypatch.setattr(ade_link.Link, "request", endless)
    code, out, err = pairs(capsys, served, ticket(client, owner), tmp_path / "mac")
    assert (code, err) == (3, "ADE: not linked: no outcome within the time this client waits.\n")
    assert 100 + ade_link.ASK_GRACE <= sum(waits["slept"]) < 100 + ade_link.ASK_GRACE + ade_link.ASK_EVERY


@pytest.mark.parametrize("when", ["while it waits", "once it is linked"])
def test_stopped_and_run_again_under_another_name_it_takes_nothing_for_that_name(client, owner, served, waits, capsys,
                                                                                 tmp_path, monkeypatch, when):
    def stopped():
        raise SystemExit(124)

    t = ticket(client, owner)
    waits["at"][2] = stopped
    d = tmp_path / "mac"
    with pytest.raises(SystemExit):
        pairs(capsys, served, t, d)
    capsys.readouterr()
    if when == "once it is linked":
        waits["now"] += link_asks.HOLD_DEFAULT
        client.get("/api/ade/link/sessions", headers=owner)       # linked, as mac
    sent = recording(monkeypatch)
    code, out, err = pairs(capsys, served, t, d, name="another")    # its pairing is mac's, not that name's
    assert (code, err) == (2, "ADE: this session's key paired by this ticket under another name (mac): run the same "
                              "command with that name; not linked.\n")
    assert sent == [("GET", "/ask")] and not (d / "config.json").exists()


def test_an_approved_answer_without_its_times_is_not_read(client, owner, served, waits, capsys, tmp_path, monkeypatch):
    real = ade_link.Link.request

    def without_times(self, method, path, *args, **kwargs):
        answer = real(self, method, path, *args, **kwargs)
        return {k: v for k, v in answer.items() if k != "server_time"} if path == "/pair" else answer

    monkeypatch.setattr(ade_link.Link, "request", without_times)
    code, out, err = pairs(capsys, served, ticket(client, owner), tmp_path / "mac")
    assert (code, err) == (3, "ADE: the ADE gave an answer this client cannot read; not linked.\n")


def test_a_poll_refused_ends_the_wait_saying_so(client, owner, served, waits, capsys, tmp_path, monkeypatch):
    real = ade_link.Link.request

    def refused(self, method, path, *args, **kwargs):
        if method == "GET" and path == "/ask":
            raise ade_link.Refused(401, "No request was made with this key: ask first.")
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", refused)
    code, out, err = pairs(capsys, served, ticket(client, owner), tmp_path / "mac")
    assert (code, err) == (4, "ADE: refused (401): No request was made with this key: ask first.\n")
    assert waits["slept"] == [ade_link.ASK_EVERY]


@pytest.mark.parametrize("when", ["while it waits", "once it is linked"])
def test_stopped_and_run_again_it_finishes_without_pairing_anew(client, owner, served, waits, capsys, tmp_path,
                                                                monkeypatch, when):
    def stopped():                  # a coding tool's command timeout, shorter than the hold: the client is stopped
        raise SystemExit(124)

    t = ticket(client, owner)
    waits["at"][2] = stopped
    d = tmp_path / "mac"
    with pytest.raises(SystemExit):
        pairs(capsys, served, t, d)
    capsys.readouterr()
    assert not (d / "config.json").exists() and (d / "key").exists()
    if when == "once it is linked":
        waits["now"] += link_asks.HOLD_DEFAULT
        client.get("/api/ade/link/sessions", headers=owner)       # the page's read: the hold has ended, the link made
    sent = recording(monkeypatch)
    code, out, err = pairs(capsys, served, t.lower(), d)            # the same ticket again, typed in lower case
    assert code == 0, err
    made = link_row("mac")
    assert ("POST", "/pair") not in sent and sent[0] == ("GET", "/ask")
    lines = out.splitlines()
    assert lines[0] == f"ADE: this session's key paired as mac before; its fingerprint is {fingerprint_in(d)}"
    assert lines[-1] == f"ADE: linked as mac (link {made['id']}); directory {d}"
    assert json.loads((d / "config.json").read_text())["link_id"] == made["id"]
    assert not (d / ade_link.PAIRING).exists()                      # its pairing has ended: its mark goes


@pytest.mark.parametrize("before", ["a revoked link of that name", "the live link it links again"])
def test_stopped_and_run_again_where_a_config_json_was_it_finishes_without_pairing_anew(client, owner, served, waits,
                                                                                       capsys, tmp_path, monkeypatch,
                                                                                       before):
    """A directory that held a link before (the page's line names no --dir, so the name's own directory is used
    again, and Link again on the same machine runs there): the run again reads its pairing all the same."""
    d = tmp_path / "mac"
    with at_once():
        assert pairs(capsys, served, ticket(client, owner), d)[0] == 0
    old = link_row("mac")
    if before == "a revoked link of that name":
        assert revoke(client, owner, old["id"]).status_code == 200
        t = ticket(client, owner)
    else:
        t = ticket(client, owner, old["id"])                         # Link again: the same machine, the same key
    stopped_while_it_waits(capsys, served, waits, t, d)
    assert json.loads((d / "config.json").read_text())["link_id"] == old["id"]
    waits["now"] += link_asks.HOLD_DEFAULT
    sent = recording(monkeypatch)
    code, out, err = pairs(capsys, served, t, d)
    assert code == 0, err
    made = link_row("mac")
    assert (made["id"] == old["id"]) is (before == "the live link it links again")
    assert sent[0] == ("GET", "/ask") and ("POST", "/pair") not in sent
    assert out.splitlines()[-1] == f"ADE: linked as mac (link {made['id']}); directory {d}"
    assert json.loads((d / "config.json").read_text())["link_id"] == made["id"]
    assert run(capsys, "--dir", d, "heartbeat")[0] == 0


@pytest.mark.parametrize("first", ["no answer", "busy", "asked too soon"])
def test_run_again_whose_read_is_not_answered_reads_again_and_never_sends_its_ticket_twice(client, owner, served, waits,
                                                                                         capsys, tmp_path, monkeypatch,
                                                                                         first):
    t = ticket(client, owner)
    d = tmp_path / "mac"
    stopped_while_it_waits(capsys, served, waits, t, d)
    failed = []

    def once(method, path):
        if (method, path) == ("GET", "/ask") and not failed:
            failed.append(path)
            raise {"no answer": ade_link.Unreachable("connection refused"),
                   "busy": ade_link.Refused(503, "The ADE is busy."),
                   "asked too soon": ade_link.Refused(429, "Ask at most once every 2 seconds.")}[first]

    sent = recording(monkeypatch, once)
    code, out, err = pairs(capsys, served, t, d)
    assert code == 0, err
    assert sent[:2] == [("GET", "/ask"), ("GET", "/ask")] and ("POST", "/pair") not in sent
    assert out.splitlines()[-1] == f"ADE: linked as mac (link {link_row('mac')['id']}); directory {d}"


def test_run_again_just_after_a_poll_meets_the_ade_s_own_limit_and_reads_again(client, owner, served, waits, capsys,
                                                                             tmp_path, monkeypatch):
    real = ade_link.Link.request

    def stopped_after_a_poll(self, method, path, *args, **kwargs):     # Ctrl-C just after a poll
        answer = real(self, method, path, *args, **kwargs)
        if (method, path) == ("GET", "/ask"):
            raise SystemExit(130)
        return answer

    monkeypatch.setattr(ade_link.Link, "request", stopped_after_a_poll)
    t = ticket(client, owner)
    d = tmp_path / "mac"
    with pytest.raises(SystemExit):
        pairs(capsys, served, t, d)
    capsys.readouterr()
    monkeypatch.setattr(ade_link.Link, "request", real)
    refused = []

    def limited(method, path):
        refused.append((method, path))

    sent = recording(monkeypatch, limited)
    code, out, err = pairs(capsys, served, t, d)                    # in the same second as that poll: 429 at first
    assert code == 0, err
    assert sent[:2] == [("GET", "/ask"), ("GET", "/ask")] and ("POST", "/pair") not in sent
    assert waits["slept"][1] == ade_link.ASK_EVERY                   # read again a poll's time later


def test_run_again_whose_reads_are_never_answered_exits_3_saying_to_run_it_again(client, owner, served, waits, capsys,
                                                                                tmp_path, monkeypatch):
    t = ticket(client, owner)
    d = tmp_path / "mac"
    stopped_while_it_waits(capsys, served, waits, t, d)

    def none(method, path):
        raise ade_link.Unreachable("connection refused")

    sent = recording(monkeypatch, none)
    code, out, err = pairs(capsys, served, t, d)
    assert (code, err) == (3, NOT_READ.format("no answer: connection refused"))
    assert sent == [("GET", "/ask")] * ade_link.READ_TRIES and (d / ade_link.PAIRING).exists()


def test_a_directory_that_never_sent_this_ticket_pairs_with_it_though_its_read_is_not_answered(client, owner, served,
                                                                                               waits, capsys, tmp_path,
                                                                                               monkeypatch):
    d = tmp_path / "mac"
    with at_once():
        assert pairs(capsys, served, ticket(client, owner), d)[0] == 0      # a key, and a link it had before
    assert revoke(client, owner, link_row("mac")["id"]).status_code == 200
    # and the mark of another ticket's pairing, left by a pair stopped before it ended
    (d / ade_link.PAIRING).write_text(json.dumps({"ticket": ade_link._ticket_digest("PP-AAAAAAAA")}))

    def busy_read(method, path):
        if (method, path) == ("GET", "/ask"):
            raise ade_link.Refused(503, "The ADE is busy.")

    sent = recording(monkeypatch, busy_read)
    with at_once():
        code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert code == 0, err
    assert sent[:2] == [("GET", "/ask"), ("POST", "/pair")] and waits["slept"] == []      # one read, then it pairs


def test_a_pairing_refused_leaves_no_mark_of_its_ticket(client, owner, served, waits, capsys, tmp_path):
    t = ticket(client, owner)
    with at_once():
        assert pairs(capsys, served, t, tmp_path / "first")[0] == 0
    d = tmp_path / "second"
    code, out, err = pairs(capsys, served, t, d)                    # used already, from another directory
    assert code == 2 and "refused (403)" in err
    assert (d / "key").exists() and not (d / ade_link.PAIRING).exists()


def test_run_again_once_its_link_was_revoked_says_so_and_writes_nothing(client, owner, served, waits, capsys, tmp_path,
                                                                       monkeypatch):
    t = ticket(client, owner)
    d = tmp_path / "mac"
    stopped_while_it_waits(capsys, served, waits, t, d)
    waits["now"] += link_asks.HOLD_DEFAULT
    client.get("/api/ade/link/sessions", headers=owner)              # linked, then the owner revokes it
    assert revoke(client, owner, link_row("mac")["id"]).status_code == 200
    sent = recording(monkeypatch)
    code, out, err = pairs(capsys, served, t, d)
    assert (code, err) == (2, "ADE: not linked: the link it made is no longer live with this key (revoked since, or "
                              "moved to another key).\n")
    assert sent == [("GET", "/ask")] and not (d / "config.json").exists() and not (d / ade_link.PAIRING).exists()


def test_a_pairing_whose_answer_was_lost_says_to_run_it_again_and_run_again_it_follows_it(client, owner, served,
                                                                                          waits, capsys, tmp_path,
                                                                                          monkeypatch):
    real = ade_link.Link.request

    def lost(self, method, path, *args, **kwargs):              # the pairing arrived; its answer did not
        answer = real(self, method, path, *args, **kwargs)
        if path == "/pair":
            raise ade_link.Unreachable("connection reset")
        return answer

    monkeypatch.setattr(ade_link.Link, "request", lost)
    t = ticket(client, owner)
    d = tmp_path / "mac"
    code, out, err = pairs(capsys, served, t, d)
    assert (code, err) == (3, f"ADE: no answer from {served}: connection reset. The pairing may have been made: run the "
                              "same command again, which reads where it stands first.\n")
    assert (d / ade_link.PAIRING).exists() and not (d / "config.json").exists()
    monkeypatch.setattr(ade_link.Link, "request", real)
    sent = recording(monkeypatch)
    code, out, err = pairs(capsys, served, t, d)
    assert code == 0, err
    assert sent[0] == ("GET", "/ask") and ("POST", "/pair") not in sent


@pytest.mark.parametrize("end", ["cancelled", "not made"])
def test_run_again_after_its_pairing_ended_it_says_how_and_a_new_ticket_pairs_anew(client, owner, served, waits, capsys,
                                                                                 tmp_path, monkeypatch, end):
    def cancels():
        client.post(f"/api/ade/holds/{the_pairing(client, owner)['hold_id']}/cancel", headers=owner)

    t = ticket(client, owner)
    d = tmp_path / "mac"
    stopped_while_it_waits(capsys, served, waits, t, d)
    if end == "cancelled":
        cancels()
        said = "ADE: not linked: the owner cancelled the pairing before it took effect.\n"
    else:
        with at_once():                                             # its name taken meanwhile
            assert pairs(capsys, served, ticket(client, owner), tmp_path / "other")[0] == 0
        waits["now"] += link_asks.HOLD_DEFAULT
        said = ("ADE: not linked: the pairing could not be made (The account already has a live link named mac.).\n")
    sent = recording(monkeypatch)
    code, out, err = pairs(capsys, served, t, d)                    # the same command again: it says how it ended
    assert (code, err) == (2, said) and sent == [("GET", "/ask")]
    assert not (d / ade_link.PAIRING).exists() and not (d / "config.json").exists()
    if end == "not made":
        return
    with at_once():                                                 # a new ticket pairs the same key anew
        code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert code == 0 and out.splitlines()[-1].startswith("ADE: linked as mac (link "), err
    assert sent[1:3] == [("GET", "/ask"), ("POST", "/pair")]


def test_an_older_ade_that_links_as_it_pairs_is_taken_at_its_word(client, owner, served, waits, capsys, tmp_path,
                                                                 monkeypatch):
    """An older release's pair links at once and answers with the link alone, no status: a client that took that for
    no answer left a live link its directory could never use."""
    real = ade_link.Link.request

    def as_before(self, method, path, *args, **kwargs):
        answer = real(self, method, path, *args, **kwargs)
        return {"link_id": answer["link_id"], "owner_keys": answer["owner_keys"]} if path == "/pair" else answer

    monkeypatch.setattr(ade_link.Link, "request", as_before)
    d = tmp_path / "mac"
    with at_once():                                         # an ADE that holds no pairing links as older releases did
        code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert code == 0, err
    made = link_row("mac")
    assert out.splitlines()[1:3] == [
        "ADE: the ADE linked it as it paired (an older ADE, which makes no pairing wait).",
        f"ADE: linked as mac (link {made['id']}); directory {d}"]
    assert json.loads((d / "config.json").read_text())["link_id"] == made["id"]
    assert run(capsys, "--dir", d, "heartbeat")[0] == 0


@pytest.mark.parametrize("answer", [{"owner_keys": {}}, {"link_id": "not a link id", "owner_keys": {}},
                                    {"link_id": 7, "owner_keys": {}}])
def test_an_answer_with_no_status_and_no_link_is_not_read(client, owner, served, waits, capsys, tmp_path, monkeypatch,
                                                          answer):
    real = ade_link.Link.request

    def odd(self, method, path, *args, **kwargs):
        got = real(self, method, path, *args, **kwargs)
        return dict(answer) if path == "/pair" else got

    monkeypatch.setattr(ade_link.Link, "request", odd)
    d = tmp_path / "mac"
    code, out, err = pairs(capsys, served, ticket(client, owner), d)
    assert (code, err) == (3, "ADE: the ADE gave an answer this client cannot read; not linked.\n")
    assert not (d / "config.json").exists()


def test_refused_its_ticket_it_prints_its_own_fingerprint_beside_the_waiting_pairing_s(client, owner, served, waits,
                                                                                      capsys, tmp_path):
    """Someone who saw the ticket paired first: the session it was meant for is told whose pairing waits, by its key's
    fingerprint, and prints its own, so the owner can tell the card on the page is not this session's."""
    t = ticket(client, owner)
    stopped_while_it_waits(capsys, served, waits, t, tmp_path / "theirs")      # paired first, and waiting
    theirs = fingerprint_in(tmp_path / "theirs")
    d = tmp_path / "mac"
    code, out, err = pairs(capsys, served, t, d)
    assert code == 2 and out == ""
    assert err == (f"ADE: refused (403): The ticket was used already, by a session whose pairing waits, with the key "
                   f"whose fingerprint is {theirs}: if that is not this session's, cancel it in Linked sessions.\n"
                   f"ADE: this session's key fingerprint is {fingerprint_in(d)}: a pairing the page shows with another "
                   "is not this session's.\n")


def test_a_poll_whose_answer_never_ends_is_no_answer_and_the_wait_ends_on_time(client, owner, served, waits, capsys,
                                                                                tmp_path, monkeypatch):
    """An ADE that trickles its answer to a poll, or a name lookup that never answers, holds no poll past the time a
    poll is given: the last answer stands, and the wait ends at the end its first answer set."""
    import threading
    monkeypatch.setattr(ade_link, "POLL_DEADLINE", 0.2)
    monkeypatch.setattr(ade_link, "ASK_GRACE", 0)
    monkeypatch.setenv(link_asks.HOLD_SETTING, "10")
    real, released = ade_link.Link.request, threading.Event()

    def never_ends(self, method, path, *args, **kwargs):
        if (method, path) == ("GET", "/ask"):
            released.wait(30)
            raise ade_link.Unreachable("released at the test's end")
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", never_ends)
    began = time.monotonic()
    try:
        code, out, err = pairs(capsys, served, ticket(client, owner), tmp_path / "mac")
    finally:
        released.set()
    assert (code, err) == (3, "ADE: not linked: no outcome within the time this client waits.\n")
    assert time.monotonic() - began < 10                      # two polls of 0.2 seconds each, not two of 30
    assert sum(waits["slept"]) == 10


@pytest.mark.parametrize("answer_takes, then", [(30, "no answer in the time left"), (0.3, "linked")])
def test_near_the_wait_s_end_a_poll_is_given_the_time_left_and_a_second_at_least(client, owner, served, waits, capsys,
                                                                                  tmp_path, monkeypatch, answer_takes,
                                                                                  then):
    """A poll is given no more than the time left before the wait's end, though a second at least: a poll that never
    ends is left after that second, not after POLL_DEADLINE; one that answers within it is read."""
    import threading
    monkeypatch.setattr(ade_link, "ASK_GRACE", 0)
    monkeypatch.setenv(link_asks.HOLD_SETTING, "2")            # its end passed by the first poll, five seconds on
    real, released = ade_link.Link.request, threading.Event()

    def slow(self, method, path, *args, **kwargs):
        if (method, path) == ("GET", "/ask"):
            released.wait(answer_takes)
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", slow)
    began = time.monotonic()
    try:
        code, out, err = pairs(capsys, served, ticket(client, owner), tmp_path / "mac")
    finally:
        released.set()
    if then == "linked":
        assert code == 0 and out.splitlines()[-1].startswith("ADE: linked as mac (link "), err
    else:
        assert (code, err) == (3, "ADE: not linked: no outcome within the time this client waits.\n")
        assert time.monotonic() - began < 10                  # a second, not the poll's twenty


@pytest.mark.parametrize("far", [10 ** 17, 2 ** 63])
def test_a_hold_end_past_what_this_platform_s_clock_can_say_is_an_answer_it_cannot_read(client, owner, served, waits,
                                                                                         capsys, tmp_path, monkeypatch,
                                                                                         far):
    real = ade_link.Link.request

    def far_off(self, method, path, *args, **kwargs):
        answer = real(self, method, path, *args, **kwargs)
        return dict(answer, takes_effect_at=far) if path == "/pair" else answer

    monkeypatch.setattr(ade_link.Link, "request", far_off)
    code, out, err = pairs(capsys, served, ticket(client, owner), tmp_path / "mac")
    assert (code, err) == (3, "ADE: the ADE gave an answer this client cannot read; not linked.\n")


def test_the_pairing_it_sends_says_it_waits(client, owner, served, waits, capsys, tmp_path, monkeypatch):
    bodies, real = [], ade_link.Link.request

    def recorded(self, method, path, body=None, *args, **kwargs):
        if path == "/pair":
            bodies.append(body)
        return real(self, method, path, body, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", recorded)
    with at_once():
        assert pairs(capsys, served, ticket(client, owner), tmp_path / "mac")[0] == 0
    (body,) = bodies
    assert body["waits"] is True and set(body) == {"ticket", "name", "kind", "public_key", "session_ref", "waits"}
    with store.connect() as db:
        assert db.execute("SELECT via FROM ade_link_asks").fetchone()["via"] == "ticket"
