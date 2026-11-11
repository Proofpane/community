"""The owner's inbox, the server's side of a session's own prompts: a linked session posts a prompt to the owner as a
link event of kind prompt,
sealed when the owner can answer it and only then; the owner's signed answer, in a form of its own and within a budget
of its own, closes it for good and is never a command for the model; the session's hook claims that answer through its
link, with take's checks; a prompt closes by the owner's answer, its own session's report or its deadline, and by
nothing else (the owner's Close hides it and leaves it open: tests/test_inbox_close_server.py; an older release's
Close closed it); and the owner's devices are told of one as of a question, in words that name nothing.
Encryption is turned on as the page does it (tests/test_ade_e2e.py's Keys), texts sealed as a link seals them, and the
owner's answers signed with a passkey of the tests' own software authenticator."""

import json
import time

import pytest

from conftest import signed_up
from proofpane_open import store
from proofpane_open.ade import link, link_commands, push
from test_ade_e2e import Keys, send_sealed
from test_ade_link import OTHER, paired, post
from test_ade_link_commands import account, event_row, payload, send, take
from test_ade_push import FCM, Device, PushService
from test_passkeys import Authenticator, add

MARK = "Zq7mark"                    # in the names and words the notices must not carry
SEALED_ONLY = "A prompt the owner can answer goes sealed: its text is the call."
READS_AS = "Your own words may not read as Approve, Deny or a choice of options: those are the buttons."
NOT_OPEN = "The ref is not an open prompt that session posted."
LOOK_ONLY = "An item to look at is answered where the session shows it."
CLOSED_ONLY_BY = "A prompt is closed only by the session that posted it, the owner, or its deadline."
EVENTS = ("PostToolUse", "PostToolUseFailure", "Stop", "StopFailure", "UserPromptSubmit")


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    r = add(client, owner, d)
    assert r.status_code == 200, r.text
    return d


@pytest.fixture
def keys(client, owner, device):
    """The account with end-to-end encryption on, as the page turns it on: what an answerable prompt needs."""
    k = Keys(client, owner, device)
    k.turn_on()
    return k


def prompt(keys, s, words="Bash: rm -rf build", look=False, deadline=None):
    """A prompt as the hook posts one: sealed to the owner, its client_id 32 hex; (its seq, its text as stored)."""
    r, _, text = send_sealed(keys, s, "prompt", words, look=look,
                             deadline=int(time.time()) + 1800 if deadline is None else deadline)
    assert r.status_code == 200, r.text
    return r.json()["seq"], text


def answer(client, owner, device, s, seq, text, words="Approve", **over):
    """The owner's signed answer to prompt `seq`, its digest that of `text` (the prompt's text as stored)."""
    return send(client, owner, device, payload(client, owner, "answer", s.link_id, words, ref=seq,
                                               digest=link_commands.text_digest(text), **over))


def answered(client, owner, device, s, seq, text, words="Approve", **over):
    r = answer(client, owner, device, s, seq, text, words, **over)
    assert r.status_code == 200, r.text
    return r.json()["seq"]


def report(s, seq, event="PostToolUse", **kw):
    """The session's own report on its prompt: a note of fixed text, nothing of the call."""
    return post(s, "note", f"reported: {event}", ref=seq, client_id=kw.pop("client_id", f"report:{seq}"), **kw)


def claim(s, seq, instance="hook-1"):
    return s.call("POST", f"/prompts/{seq}/claim", {"instance": instance})


def inbox(client, owner):
    r = client.get("/api/ade/link/sessions", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


def open_prompts(client, owner):
    return [p["seq"] for p in inbox(client, owner).get("prompts", [])]


def set_deadline(seq, at):
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET deadline_at = ? WHERE seq = ?", (at, seq))


def answers():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_link_events WHERE kind = 'answer'").fetchall()]


def owner_closed_before(seq, at=None):
    """Prompt `seq` closed by the owner's Close as an older release's ADE stored one:
    his note of fixed text to its session, client_id close:SEQ, and the prompt closed by it, closed_reason owner. No
    route makes one now; a store from before keeps them. The note's seq."""
    at = int(time.time()) if at is None else at
    with store.connect() as db:
        p = db.execute("SELECT user_id, link_id FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()
        name = db.execute("SELECT name FROM ade_links WHERE id = ?", (p["link_id"],)).fetchone()["name"]
        note = db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, ref, text, "
                          "needs_turn, received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (p["user_id"], link.OWNER, f"close:{seq}", "note", name, seq, link.CLOSED_ON_PAGE, 0,
                           at)).lastrowid
        db.execute("UPDATE ade_link_events SET closed_by = ?, closed_at = ?, closed_reason = 'owner' WHERE seq = ?",
                   (note, at, seq))
    return note


# ---- posting a prompt ------------------------------------------------------------------------------------------------

def test_a_prompt_reaches_the_owner_s_list_sealed_with_its_look_flag_and_its_deadline(client, owner, keys):
    mac = paired(client, owner)
    t = int(time.time())
    seq, text = prompt(keys, mac, "Bash: git push origin main", deadline=t + 1800)
    look_seq, look_text = prompt(keys, mac, "ExitPlanMode", look=True, deadline=t + 600)
    view = inbox(client, owner)
    got = [(p["seq"], p["kind"], p["text"], p["look"], p["link_id"], p["deadline_at"], p["from"], p["to"])
           for p in view.get("prompts", [])]
    assert got == [(seq, "prompt", text, False, mac.link_id, t + 1800, "mac", "owner"),
                   (look_seq, "prompt", look_text, True, mac.link_id, t + 600, "mac", "owner")]
    assert text.startswith("e2e1.") and view["questions"] == []       # a prompt is none of the session's questions
    row = event_row(seq)
    assert (row["needs_turn"], row["look"], row["closed_reason"], row["deadline_at"]) == (0, 0, None, t + 1800)
    assert event_row(look_seq)["look"] == 1
    # the session's own poll reads it open, with what closes it still empty
    polled = mac.call("GET", f"/events/{seq}").json()
    assert (polled.get("open"), polled.get("deadline_at"), polled.get("look"), polled.get("closed_reason"),
            polled.get("closed_at"), polled.get("closed_by")) == (True, t + 1800, False, None, None, None)


def test_a_prompt_the_owner_can_answer_goes_sealed(client, owner, device):
    """On an account without encryption as on one with it: an answerable prompt's text is the call, and only the owner
    may read it."""
    mac = paired(client, owner)
    r = post(mac, "prompt", "Bash: rm -rf build", deadline=int(time.time()) + 1800)
    assert r.status_code == 422 and r.json()["detail"] == SEALED_ONLY
    r = post(mac, "prompt", "Bash: rm -rf build", deadline=int(time.time()) + 1800, look=False)
    assert r.status_code == 422 and r.json()["detail"] == SEALED_ONLY
    Keys(client, owner, device).turn_on()
    r = post(mac, "prompt", "Bash: rm -rf build", deadline=int(time.time()) + 1800)
    assert r.status_code == 422 and r.json()["detail"] == SEALED_ONLY
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE kind = 'prompt'").fetchone()[0] == 0


def test_an_item_to_look_at_goes_in_the_clear_only_while_encryption_is_off(client, owner, device):
    mac = paired(client, owner)
    t = int(time.time())
    r = post(mac, "prompt", "ExitPlanMode", deadline=t + 600, look=True)
    assert r.status_code == 200, r.text
    shown = [(p["seq"], p["text"], p["look"]) for p in inbox(client, owner).get("prompts", [])]
    assert shown == [(r.json()["seq"], "ExitPlanMode", True)]
    # held to the rules a question's words are held to, which the owner's answer would carry the digest of
    bad = post(mac, "prompt", "Bash\u202e", deadline=t + 600, look=True)
    assert bad.status_code == 422 and "U+202E" in bad.json()["detail"]
    keys = Keys(client, owner, device)
    keys.turn_on()
    refused = post(mac, "prompt", "Notification: Session paused", deadline=t + 600, look=True)
    assert refused.status_code == 409 and refused.json().get("e2e") == "required", refused.text
    seq, _ = prompt(keys, mac, "Notification: Session paused", look=True, deadline=t + 600)
    assert event_row(seq)["look"] == 1


def test_a_prompt_is_to_the_owner_refers_to_nothing_and_has_a_deadline_from_30_seconds_to_an_hour(client, owner,
                                                                                                   keys, monkeypatch):
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    t = int(time.time())
    # the ADE's clock held at t, so the bounds are read against it to the second (a request's own time stays within
    # the five minutes a signature allows)
    monkeypatch.setattr(link, "_now", lambda now=None: t if now is None else now)
    for over in ({"to": "cloud"}, {"to": "all"}, {"to": "mac"}, {"ref": 1}, {"deadline": t + 29},
                 {"deadline": t + 3601}, {"deadline": str(t + 600)}, {"deadline": True}, {"deadline": None},
                 {"look": "yes"}, {"look": 1}):
        kw = dict({"deadline": t + 600}, **over)
        if kw["deadline"] is None:
            del kw["deadline"]
        r, _, _ = send_sealed(keys, mac, "prompt", "Bash: ls", **kw)
        assert r.status_code == 422, (over, r.text)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE kind = 'prompt'").fetchone()[0] == 0
    for deadline in (t + 30, t + 3600):
        prompt(keys, mac, "Bash: ls", deadline=deadline)
    # a deadline and a look are a prompt's alone
    for kind, over in (("note", {"deadline": t + 600}), ("step", {"look": True}), ("question", {"look": False})):
        r, _, _ = send_sealed(keys, mac, kind, "words", to="owner", **over)
        assert r.status_code == 422, (kind, over, r.text)


def test_a_link_has_at_most_20_prompts_open(client, owner, keys):
    mac = paired(client, owner)
    seqs = [prompt(keys, mac, f"Bash: echo {i}")[0] for i in range(20)]
    r, _, _ = send_sealed(keys, mac, "prompt", "Bash: echo 21", deadline=int(time.time()) + 600)
    assert r.status_code == 429 and "20 prompts open" in r.json()["detail"]
    # its session's report closes one (the owner's Close only hides one, which stays open:
    # tests/test_inbox_close_server.py)
    assert report(mac, seqs[0], "Stop").status_code == 200
    prompt(keys, mac, "Bash: echo 21")                  # one closed, one more may wait


# ---- the owner's answer ----------------------------------------------------------------------------------------------

ACCEPTED = ["Approve", "Deny", "1", "2,5", "1,2,3", "7,8,9,10,999", "Not now: run the tests first",
            "approve it after lunch", "Deny it, and say why"]
REFUSED = ["approve", "Approve.", " deny ", "1, 2", "2,1", "1,1", "APPROVED!", "denied", "0", "1000", "3;4",
           "Approve\n"]


def test_an_answer_is_approve_deny_option_numbers_or_words_that_read_as_none_of_them(client, owner, device, keys):
    mac = paired(client, owner)
    for words in REFUSED:
        seq, text = prompt(keys, mac)
        r = answer(client, owner, device, mac, seq, text, words)
        assert r.status_code == 422 and r.json()["detail"] == READS_AS, (words, r.text)
        assert event_row(seq)["closed_reason"] is None and seq in open_prompts(client, owner)
    assert answers() == []
    for words in ACCEPTED:
        seq, text = prompt(keys, mac)
        a = answered(client, owner, device, mac, seq, text, words)
        row = event_row(a)
        assert (row["kind"], row["link_id"], row["to_id"], row["ref"], row["text"], row["needs_turn"],
                row["prompt_answer"]) == ("answer", "owner", mac.link_id, seq, words, 0, 1), words
        closed = event_row(seq)
        assert (closed["closed_reason"], closed["closed_by"]) == ("answer", a)
        assert seq not in open_prompts(client, owner)


def test_an_answer_carries_the_digest_of_the_prompt_s_text_as_stored(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac, "Bash: rm -rf build")
    for other in ("Bash: rm -rf build", text + "x", "e2e1.AAAA"):
        r = answer(client, owner, device, mac, seq, other)
        assert r.status_code == 409 and "digest" in r.json()["detail"], r.text
    assert answers() == [] and event_row(seq)["closed_reason"] is None
    answered(client, owner, device, mac, seq, text)


def test_an_answer_names_the_session_that_posted_the_prompt(client, owner, device, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    seq, text = prompt(keys, mac)
    r = answer(client, owner, device, cloud, seq, text)
    assert r.status_code == 409 and r.json()["detail"] == NOT_OPEN
    assert answers() == [] and event_row(seq)["closed_reason"] is None


def test_an_item_to_look_at_is_never_answered_from_the_list(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac, "ExitPlanMode", look=True)
    for words in ("Approve", "Deny", "1", "go ahead"):
        r = answer(client, owner, device, mac, seq, text, words)
        assert r.status_code == 409 and r.json()["detail"] == LOOK_ONLY, r.text
    assert answers() == [] and seq in open_prompts(client, owner)


def test_an_answer_after_the_session_s_report_or_the_deadline_is_refused(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    assert report(mac, seq, "PostToolUse").status_code == 200        # the window answered it first
    r = answer(client, owner, device, mac, seq, text)
    assert r.status_code == 409 and r.json()["detail"] == NOT_OPEN
    late, late_text = prompt(keys, mac)
    set_deadline(late, int(time.time()) - 1)
    r = answer(client, owner, device, mac, late, late_text)
    assert r.status_code == 409 and r.json()["detail"] == NOT_OPEN
    assert answers() == []


# ---- what closes a prompt, and nothing else --------------------------------------------------------------------------

def test_the_session_reports_its_own_prompt_closed_with_a_fixed_text_in_the_clear(client, owner, keys):
    mac = paired(client, owner)
    for event in EVENTS:
        seq, _ = prompt(keys, mac)
        r = report(mac, seq, event)                      # in the clear, while encryption is on
        assert r.status_code == 200, (event, r.text)
        note = event_row(r.json()["seq"])
        assert (note["kind"], note["text"], note["ref"], note["client_id"], note["needs_turn"]) == (
            "note", f"reported: {event}", seq, f"report:{seq}", 0)
        row = event_row(seq)
        assert (row["closed_reason"], row["closed_by"]) == (f"report:{event}", r.json()["seq"])
        assert seq not in open_prompts(client, owner)


def test_a_report_is_exactly_its_fixed_text_by_a_note_naming_the_prompt(client, owner):
    """On an account without encryption, so that each text below is read by these rules and not refused first as
    words in the clear: an item to look at, posted in the clear, closes the same way an answerable prompt does."""
    mac = paired(client, owner)
    r = post(mac, "prompt", "ExitPlanMode", deadline=int(time.time()) + 600, look=True)
    assert r.status_code == 200, r.text
    seq = r.json()["seq"]
    for kind, text, client_id in (("done", "reported: Stop", f"report:{seq}"),
                                  ("waiting", "reported: Stop", f"report:{seq}"),
                                  ("note", "reported: Something", f"report:{seq}"),
                                  ("note", "reported: Stop ", f"report:{seq}"),
                                  ("note", "reported: stop", f"report:{seq}"),
                                  ("note", "ran it", f"report:{seq}"),
                                  ("note", "reported: Stop", "a" * 32),
                                  ("note", "reported: Stop", f"report:{seq + 1}"),
                                  ("note", "reported: Stop", "report:x")):
        r = post(mac, kind, text, ref=seq, client_id=client_id)
        assert r.status_code == 422, (kind, text, client_id, r.text)
    # report:SEQ is a report's alone, whose text stays in the clear: never another note's
    assert post(mac, "note", "reported: Stop", client_id=f"report:{seq}").status_code == 422
    assert event_row(seq)["closed_reason"] is None and seq in open_prompts(client, owner)
    r = report(mac, seq, "UserPromptSubmit")
    assert r.status_code == 200 and event_row(seq)["closed_reason"] == "report:UserPromptSubmit", r.text


def test_another_session_s_note_closes_no_prompt(client, owner, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    seq, _ = prompt(keys, mac)
    r = post(cloud, "note", "reported: PostToolUse", ref=seq, client_id=f"report:{seq}")
    assert r.status_code == 422 and r.json()["detail"] == CLOSED_ONLY_BY, r.text
    for kind in ("note", "done"):                         # its own words, sealed as on this account they go
        r, _, _ = send_sealed(keys, cloud, kind, "done with it", ref=seq, to="owner")
        assert r.status_code == 422 and r.json()["detail"] == CLOSED_ONLY_BY, r.text
    assert event_row(seq)["closed_reason"] is None and seq in open_prompts(client, owner)


def test_the_owner_s_close_decides_nothing_and_carries_no_words(client, owner, device, keys):
    """This test once held that Close closed a prompt (closed_reason owner,
    with the owner's note of fixed text to its session), which ended the prompt hook and left the prompt to the
    session's own window, where nobody may be, and to no list of the owner's. Now Close hides the prompt from his open
    list and leaves it open: no note, no answer, nothing closed; it still decides nothing and carries no words, its hook
    goes on waiting, and an answer is still taken until its deadline (tests/test_inbox_close_server.py)."""
    mac = paired(client, owner)
    seq, _ = prompt(keys, mac)
    with store.connect() as db:
        newest = db.execute("SELECT MAX(seq) FROM ade_link_events").fetchone()[0]
    r = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert r.status_code == 200, r.text
    # the prompt's own seq and when it was hidden: no note was made, to its session or anyone
    assert sorted(r.json()) == ["hidden_at", "seq"] and r.json()["seq"] == seq, r.json()
    with store.connect() as db:
        assert db.execute("SELECT MAX(seq) FROM ade_link_events").fetchone()[0] == newest
    row = event_row(seq)
    assert (row["closed_reason"], row["closed_by"], row["closed_at"]) == (None, None, None)
    assert answers() == [] and seq not in open_prompts(client, owner)
    again = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert again.status_code == 200 and again.json() == r.json()
    waits = claim(mac, seq)                              # nothing decided: its hook goes on waiting
    assert waits.status_code == 409 and waits.json().get("why") == "open", waits.text


def test_the_deadline_closes_a_prompt_and_says_so(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    past = int(time.time()) - 5
    set_deadline(seq, past)
    assert seq not in open_prompts(client, owner)
    row = event_row(seq)
    assert (row["closed_reason"], row["closed_at"], row["closed_by"]) == ("deadline", past, None)
    polled = mac.call("GET", f"/events/{seq}").json()
    assert (polled.get("open"), polled.get("closed_reason")) == (False, "deadline")
    r = answer(client, owner, device, mac, seq, text)
    assert r.status_code == 409 and r.json()["detail"] == NOT_OPEN
    assert client.post(f"/api/ade/link/questions/{seq}/close", headers=owner).status_code == 409


def test_an_answer_that_expires_untaken_keeps_its_prompt_closed_and_asks_no_look(client, owner, device, keys):
    mac = paired(client, owner)
    uid = account(client, owner)
    seq, text = prompt(keys, mac, deadline=int(time.time()) + 3600)
    a = answered(client, owner, device, mac, seq, text, "Deny")
    t = event_row(a)["received_at"]
    # untaken for longer than a command is told of (link_commands.UNTAKEN_AFTER): nothing needs a look
    assert [k for k, _ in link.alerts(uid, t + 400) if k.endswith(f":{a}")] == []
    with store.connect() as db:                         # its life over, and no hook claimed it
        db.execute("UPDATE ade_link_events SET expires_at = ? WHERE seq = ?", (int(time.time()) - 1, a))
    assert seq not in open_prompts(client, owner)
    row = event_row(seq)
    assert (row["closed_reason"], row["closed_by"]) == ("answer", a)
    again = answer(client, owner, device, mac, seq, text, "Approve")
    assert again.status_code == 409 and again.json()["detail"] == NOT_OPEN


# ---- an answer is the hook's, never the model's ----------------------------------------------------------------------

def test_a_prompt_s_answer_wakes_no_turn_is_marked_and_is_never_taken(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    a = answered(client, owner, device, mac, seq, text)
    assert event_row(a)["needs_turn"] == 0
    assert a not in [e["seq"] for e in mac.call("GET", "/feed", query={"turn": 1}).json()["events"]]
    (shown,) = [e for e in mac.call("GET", "/feed").json()["events"] if e["seq"] == a]
    assert shown.get("for_prompt") is True
    assert mac.call("GET", f"/events/{a}").json().get("for_prompt") is True
    r = take(mac, a)
    assert r.status_code == 409 and r.json().get("why") == "prompt", r.text
    assert event_row(a)["taken_at"] is None
    document = mac.call("GET", "/resume").json()["document"]
    assert "Commands to you" not in document and f"[{a}] answer" not in document
    # the line the session reads of it, among what it has not acked, never tells it to take it
    (shown,) = [line for line in document.splitlines() if line.startswith(f"[{a}] ")]
    assert "take" in shown and f"take {a}" not in shown and "prompt hook claims it" in shown


def test_the_hook_claims_its_prompt_s_answer_once_and_only_once_it_is_answered(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    r = claim(mac, seq)
    assert r.status_code == 409 and r.json().get("why") == "open", r.text
    a = answered(client, owner, device, mac, seq, text, "2,3")
    polled = mac.call("GET", f"/events/{seq}").json()
    assert (polled.get("open"), polled.get("closed_reason"), polled.get("closed_by")) == (False, "answer", a)
    r = claim(mac, seq)
    assert r.status_code == 200, r.text
    got = r.json()
    row = event_row(a)
    assert (got["seq"], got["payload"], got["signature"], got["passkey_id"], got["taken_at"]) == (
        a, row["payload"], json.loads(row["signature"]), row["passkey_id"], row["taken_at"])
    assert json.loads(got["payload"])["text"] == "2,3" and json.loads(got["payload"])["ref"] == seq
    assert (row["taken_by"], row["taken_instance"]) == (mac.link_id, "hook-1")
    took = event_row_by_client(mac.link_id, f"take:{a}")
    assert (took["kind"], took["ref"], took["needs_turn"], took["text"]) == (
        "take", a, 0, link_commands.text_digest(row["payload"]))
    again = claim(mac, seq)
    assert again.status_code == 200 and again.json() == got
    other = claim(mac, seq, instance="hook-2")
    assert other.status_code == 409 and other.json().get("why") == "taken"


def event_row_by_client(link_id, client_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_link_events WHERE link_id = ? AND client_id = ?",
                               (link_id, client_id)).fetchone())


def test_a_stopped_session_s_hook_claims_nothing(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    a = answered(client, owner, device, mac, seq, text)
    assert client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).status_code == 200
    r = claim(mac, seq)
    assert r.status_code == 409 and r.json().get("why") == "stopped", r.text
    assert event_row(a)["taken_at"] is None


@pytest.mark.parametrize("how", ["owner", "report", "deadline"])
def test_a_prompt_closed_without_an_answer_gives_the_hook_nothing(client, owner, keys, how):
    mac = paired(client, owner)
    seq, _ = prompt(keys, mac)
    if how == "owner":
        # the owner's Close as an older release closed a prompt, which a store from then keeps (now it hides
        # one and leaves it open: tests/test_inbox_close_server.py)
        owner_closed_before(seq)
    elif how == "report":
        assert report(mac, seq, "Stop").status_code == 200
    else:
        set_deadline(seq, int(time.time()) - 1)
    r = claim(mac, seq)
    assert r.status_code == 409 and r.json().get("why") == "closed", r.text
    assert r.json().get("closed_reason") == {"owner": "owner", "report": "report:Stop", "deadline": "deadline"}[how]


@pytest.mark.parametrize("gone", ["deadline", "expired", "withdrawn"])
def test_an_answer_past_the_prompt_s_deadline_or_its_own_life_is_not_claimed(client, owner, device, keys, gone):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    a = answered(client, owner, device, mac, seq, text)
    if gone == "deadline":
        set_deadline(seq, int(time.time()) - 1)
    elif gone == "expired":
        with store.connect() as db:
            db.execute("UPDATE ade_link_events SET expires_at = ? WHERE seq = ?", (int(time.time()) - 1, a))
    else:
        assert client.post(f"/api/ade/link/commands/{a}/withdraw", headers=owner).status_code == 200
    r = claim(mac, seq)
    assert r.status_code == 409 and r.json().get("why") == gone, r.text
    assert event_row(a)["taken_at"] is None


def test_only_the_session_that_posted_a_prompt_claims_its_answer(client, owner, device, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    seq, text = prompt(keys, mac)
    answered(client, owner, device, mac, seq, text)
    for s, ref in ((cloud, seq), (mac, seq + 10_000), (mac, 2 ** 64)):
        r = claim(s, ref)
        assert r.status_code == 404 and r.json()["detail"] == "No prompt of this session has that seq.", r.text
    asked, _, _ = send_sealed(keys, mac, "question", "Which branch?", to="owner")
    assert asked.status_code == 200, asked.text
    r = claim(mac, asked.json()["seq"])                  # a question is not a prompt
    assert r.status_code == 404 and r.json()["detail"] == "No prompt of this session has that seq."
    bad = mac.call("POST", f"/prompts/{seq}/claim", {"instance": "x" * 101})
    assert bad.status_code == 422


# ---- budgets ---------------------------------------------------------------------------------------------------------

def test_prompt_answers_have_a_budget_of_their_own_outside_the_owner_s_commands(client, owner, device, keys):
    mac = paired(client, owner)
    uid = account(client, owner)
    first, first_text = prompt(keys, mac)
    second, second_text = prompt(keys, mac)
    now = int(time.time())
    with store.connect() as db:                         # 59 prompt answers in the hour, untaken and unexpired
        for i in range(link_commands.PROMPT_ANSWERS_PER_HOUR - 1):
            db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, ref, text, "
                       "needs_turn, received_at, to_id, expires_at, prompt_answer) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (uid, "owner", f"pa{i}", "answer", "mac", None, "Approve", 0, now, mac.link_id, now + 3600, 1))
    answered(client, owner, device, mac, first, first_text)                             # the 60th
    r = answer(client, owner, device, mac, second, second_text)
    assert r.status_code == 429 and r.json()["detail"] == "The owner answers at most 60 prompts an hour."
    assert event_row(second)["closed_reason"] is None
    # neither in the owner's 60 commands an hour nor in a session's 50 waiting to be taken: 59 instructions in the hour
    # beside 60 prompt answers, and the 60th instruction still goes
    with store.connect() as db:
        for i in range(link_commands.OWNER_PER_HOUR - 1):
            db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, needs_turn, "
                       "received_at, to_id, expires_at, withdrawn_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (uid, "owner", f"h{i}", "instruction", "mac", "t", 1, now, mac.link_id, now + 3600, now))
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id))
    assert r.status_code == 200, r.text
    with store.connect() as db:                         # an hour on, the prompt answers' budget is back
        db.execute("UPDATE ade_link_events SET received_at = received_at - 3601 WHERE prompt_answer = 1")
    answered(client, owner, device, mac, second, second_text)


# ---- what the owner is told, and where -------------------------------------------------------------------------------

def test_a_prompt_is_told_as_a_question_is_in_words_that_name_nothing(client, owner, keys, monkeypatch):
    mac = paired(client, owner, name=f"mac {MARK}")
    uid = account(client, owner)
    device, service = Device(), PushService()
    monkeypatch.setattr(push, "_default_post", service)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    push.subscribe(uid, FCM, device.p256dh, push.b64u(device.auth))
    push._board_cache.clear()
    seq, _ = prompt(keys, mac, f"Bash: rm -rf {MARK}")
    look, _ = prompt(keys, mac, f"ExitPlanMode {MARK}", look=True)
    alerts = dict(link.alerts(uid))
    assert alerts.get(f"link-prompt:{seq}") == "link-question" and alerts.get(f"link-prompt:{look}") == "link-question"
    push.tick(post=service, build=lambda conn: {"alerts": []})
    told = [device.decrypt(b) for _, _, b in service.posts]
    assert [(m["title"], m["body"], m["url"]) for m in told] == [
        ("A question waits for you", "Open Linked sessions to read it.", "/app/#channel")] * 2
    assert MARK.lower() not in json.dumps(told).lower()
    assert client.post(f"/api/ade/link/questions/{seq}/close", headers=owner).status_code == 200
    assert f"link-prompt:{seq}" not in dict(link.alerts(uid))


def test_the_timeline_shows_a_prompt_its_answer_its_take_and_why_it_closed(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    a = answered(client, owner, device, mac, seq, text)
    assert claim(mac, seq).status_code == 200
    events = {e["seq"]: e for e in client.get(f"/api/ade/link/timeline/{mac.link_id}", headers=owner).json()["events"]}
    assert (events[seq]["kind"], events[seq].get("closed_reason"), events[seq].get("closed_by")) == (
        "prompt", "answer", a)
    assert events[a]["kind"] == "answer" and events[a].get("for_prompt") is True and events[a]["state"] == "taken"


def test_another_account_reads_none_of_it(client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    other = signed_up(client, OTHER)
    assert client.get("/api/ade/link/sessions", headers=other).json().get("prompts") == []
    assert client.post(f"/api/ade/link/questions/{seq}/close", headers=other).status_code == 409
    theirs = paired(client, other, "x")
    assert claim(theirs, seq).status_code == 404
    assert seq in open_prompts(client, owner)
