"""The owner inbox's Close, the server's part: in Community's owner inbox, the owner's Close of a
session's prompt hides it without giving it up. It leaves his open list and his notifier, the server keeping when it
was hidden and by which account, and it stays open: no note goes to its session, nothing is decided or closed, the
prompt hook goes on waiting, and an answer is taken until the prompt's deadline, as for any open prompt. His list of
closed prompts (GET /api/ade/link/prompts/closed) holds the prompts hidden and still open, which he may still answer,
and those closed in the last 24 hours, newest first, each saying how it ended and never an answer's words. A
question's Close and a proposal's are as they were (tests/test_ade_link.py, tests/test_ade_link_proposals.py).
Encryption is turned on as the page does it, texts sealed as a link seals them and the owner's answers signed with the
tests' own passkey, as in tests/test_ade_link_prompts.py, whose fixtures and helpers these cases use."""

import json
import time

import pytest

from conftest import signed_up
from proofpane_open import store
from proofpane_open.ade import link
from test_ade_e2e import Keys, send_sealed
from test_ade_link import OTHER, paired
from test_ade_link_commands import account, event_row, nonce
from test_ade_link_prompts import device, keys, owner  # noqa: F401  (fixtures)
from test_ade_link_prompts import (NOT_OPEN, answer, answered, answers, claim, inbox, owner_closed_before, prompt,
                                   report, set_deadline)
from test_ade_link_proposals import allowed, proposed
from test_passkeys import Authenticator, add

WORDS = "Not now: run the tests first"          # the owner's own words, which no list of closed prompts carries
NOT_OPEN_PROMPT = "That is not an open prompt."


def hide(client, owner, seq):
    """The owner's Close of prompt `seq`, as the page sends it: its answer is the prompt's own seq and when it was
    hidden, and nothing else. Its hidden_at."""
    r = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert r.status_code == 200, r.text
    assert sorted(r.json()) == ["hidden_at", "seq"] and r.json()["seq"] == seq, r.json()
    return r.json()["hidden_at"]


def closed_list(client, headers):
    r = client.get("/api/ade/link/prompts/closed", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def fresh(client, owner):
    """A nonce for an answer, and its created_at taken once it is given: the answer route takes a created_at from two
    seconds before its nonce was given to 30 after, and the shared payload takes its time before it asks for the
    nonce, so on a loaded machine the two calls between could set them further apart than that."""
    n = nonce(client, owner, "answer")
    return {"nonce": n, "created_at": int(time.time())}


def event_count():
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ade_link_events").fetchone()[0]


def pinned(monkeypatch, at):
    """The ADE's clock held at at[0] (a list, so a case can move it on), as test_ade_link_prompts.py holds it."""
    monkeypatch.setattr(link, "_now", lambda now=None: at[0] if now is None else now)


def another_account(client):
    """A second account with encryption on and a linked session of its own: (its sign-in's headers, its keys, its
    session). Its passkey is of another kind than the first account's, whose test credential id is fixed."""
    other = signed_up(client, OTHER)
    their_device = Authenticator(-8)
    assert add(client, other, their_device).status_code == 200
    their_keys = Keys(client, other, their_device)
    their_keys.turn_on()
    return other, their_keys, paired(client, other, "x")


def links_stamp(client, headers):
    """The summary's stamp of the session channel's list (ade/summary.py: a digest of GET /api/ade/link/sessions but its
    server_time), whose move tells the page to read that list again, and its list of closed prompts with it."""
    r = client.get("/api/ade/summary", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["stamps"]["links"]


# ---- the owner's Close of a prompt hides it --------------------------------------------------------------------------

def test_close_hides_an_open_prompt_and_keeps_when_and_by_which_account(client, owner, keys, monkeypatch):
    mac = paired(client, owner)
    uid = account(client, owner)
    t = int(time.time())
    pinned(monkeypatch, [t])
    seq, _ = prompt(keys, mac)
    before = event_count()
    r = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert r.status_code == 200 and r.json() == {"seq": seq, "hidden_at": t}, r.text
    row = event_row(seq)
    assert (row["hidden_at"], row["hidden_by"]) == (t, uid)
    # and left open: nothing closed, no note to its session, no answer
    assert (row["closed_reason"], row["closed_by"], row["closed_at"]) == (None, None, None)
    assert event_count() == before and answers() == []


def test_a_second_close_answers_the_same_while_the_prompt_is_open(client, owner, keys, monkeypatch):
    mac = paired(client, owner)
    at = [int(time.time())]
    pinned(monkeypatch, at)
    seq, _ = prompt(keys, mac)
    first = hide(client, owner, seq)
    at[0] += 7
    again = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert again.status_code == 200 and again.json() == {"seq": seq, "hidden_at": first}, again.text
    assert event_row(seq)["hidden_at"] == first == at[0] - 7


@pytest.mark.parametrize("how", ["answer", "report", "deadline", "the owner's close in an older release"])
def test_a_close_of_a_prompt_no_longer_open_is_refused_and_changes_nothing(client, owner, device, keys, how):
    """Answered (after its Close, from the list of closed prompts), reported by its session, past its deadline, or
    closed by the owner's Close as an older release's closed one (closed_reason owner, with his note): a Close of it
    is refused, as a Close of a closed prompt always was."""
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    hidden = None
    if how == "the owner's close in an older release":
        owner_closed_before(seq)
    else:
        hidden = hide(client, owner, seq)
        if how == "answer":
            answered(client, owner, device, mac, seq, text, **fresh(client, owner))
        elif how == "report":
            assert report(mac, seq, "Stop").status_code == 200
        else:
            set_deadline(seq, int(time.time()) - 1)
            inbox(client, owner)                     # a read, which records the deadline's close
    before = event_row(seq)
    count = event_count()
    r = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert r.status_code == 409 and r.json()["detail"] == NOT_OPEN_PROMPT, r.text
    assert event_row(seq) == before and event_count() == count
    assert (before["closed_reason"], before["hidden_at"]) == (
        {"answer": "answer", "report": "report:Stop", "deadline": "deadline"}.get(how, "owner"), hidden)


# ---- a hidden prompt stays open --------------------------------------------------------------------------------------

def test_a_hidden_prompt_reads_to_its_hook_as_before_its_close_so_the_hook_goes_on_waiting(client, owner, keys):
    """The hook polls its prompt (GET /events/{seq}) and ends on any close: a hidden one reads to it exactly as it did
    before the Close, open and closed by nothing, and its claim says it is open, as for any prompt not yet answered."""
    mac = paired(client, owner)
    seq, _ = prompt(keys, mac)
    polled = mac.call("GET", f"/events/{seq}").json()
    hide(client, owner, seq)
    now = mac.call("GET", f"/events/{seq}").json()
    assert now == polled
    assert (now.get("open"), now.get("closed_reason"), now.get("closed_at"), now.get("closed_by")) == (
        True, None, None, None)
    r = claim(mac, seq)
    assert r.status_code == 409 and r.json().get("why") == "open", r.text


def test_a_hidden_prompt_is_answered_from_the_closed_list_until_its_deadline_and_its_hook_claims_the_answer(
        client, owner, device, keys):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    hidden = hide(client, owner, seq)
    (listed,) = [p for p in closed_list(client, owner)["prompts"] if p["seq"] == seq]
    assert listed["text"] == text                     # what the answer's digest is of
    a = answered(client, owner, device, mac, seq, listed["text"], "Approve", **fresh(client, owner))
    row = event_row(seq)
    assert (row["closed_reason"], row["closed_by"], row["hidden_at"]) == ("answer", a, hidden)
    got = claim(mac, seq)
    assert got.status_code == 200 and got.json()["seq"] == a, got.text
    assert json.loads(got.json()["payload"])["text"] == "Approve"
    # one nobody answers by its deadline is closed by it: an answer then is refused, and its hook claims nothing
    late, late_text = prompt(keys, mac)
    hide(client, owner, late)
    set_deadline(late, int(time.time()) - 1)
    r = answer(client, owner, device, mac, late, late_text, **fresh(client, owner))
    assert r.status_code == 409 and r.json()["detail"] == NOT_OPEN, r.text
    # a read that commits records the deadline's close (the refused answer's own transaction is undone with it)
    (ended,) = [p for p in closed_list(client, owner)["prompts"] if p["seq"] == late]
    assert (ended["ended"], ended["open"], ended["closed_reason"]) == ("deadline", False, "deadline")
    assert event_row(late)["closed_reason"] == "deadline"
    r = claim(mac, late)
    assert r.status_code == 409 and (r.json().get("why"), r.json().get("closed_reason")) == ("closed", "deadline")


def test_the_open_list_and_the_notifier_leave_out_a_hidden_prompt_and_nothing_else(client, owner, keys):
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    allowed(client, owner, mac)
    uid = account(client, owner)
    hidden, _ = prompt(keys, mac)
    shown, _ = prompt(keys, mac, "Bash: npm test")
    r, _, _ = send_sealed(keys, mac, "question", "Which branch?", to="owner")
    assert r.status_code == 200, r.text
    question = r.json()["seq"]
    proposal, _, _ = proposed(keys, mac, "cloud")
    hide(client, owner, hidden)
    view = inbox(client, owner)
    assert [p["seq"] for p in view["prompts"]] == [shown]
    assert [q["seq"] for q in view["questions"]] == [question]
    assert [p["seq"] for p in view["proposals"]] == [proposal]
    told = dict(link.alerts(uid))
    assert f"link-prompt:{hidden}" not in told and told.get(f"link-prompt:{shown}") == "link-question"
    assert (told.get(f"link-question:{question}"), told.get(f"link-proposal:{proposal}")) == (
        "link-question", "link-proposal")


def test_a_hidden_prompt_is_still_one_of_its_link_s_open_prompts(client, owner, keys):
    """Hidden, a prompt is still open, its hook still waiting on it, so it still counts among the 20 a link may have
    open; its session's report closes it, and makes room."""
    mac = paired(client, owner)
    seqs = [prompt(keys, mac, f"Bash: echo {i}")[0] for i in range(link.OPEN_PROMPTS_PER_LINK)]
    hide(client, owner, seqs[0])
    r, _, _ = send_sealed(keys, mac, "prompt", "Bash: echo 21", deadline=int(time.time()) + 600)
    assert r.status_code == 429 and "20 prompts open" in r.json()["detail"], r.text
    assert report(mac, seqs[0], "Stop").status_code == 200
    prompt(keys, mac, "Bash: echo 21")


# ---- the owner's list of closed prompts ------------------------------------------------------------------------------

def test_the_closed_list_says_how_each_prompt_ended_newest_first_and_never_an_answer_s_words(client, owner, device,
                                                                                              keys):
    mac = paired(client, owner)
    names = ("waiting", "look", "approve", "numbers", "deny", "words", "deadline", "report", "owner",
             "hidden then answered", "hidden then deadline", "a day ago", "open")
    made = {n: prompt(keys, mac, f"Bash: echo {n}", look=n == "look") for n in names}
    seq = {n: s for n, (s, _) in made.items()}
    for n in ("waiting", "hidden then answered", "hidden then deadline"):
        hide(client, owner, seq[n])
    # an item to look at holds no hook: its Close closes it, as Close did before it hid prompts
    closed_look = client.post(f"/api/ade/link/questions/{seq['look']}/close", headers=owner)
    assert closed_look.status_code == 200 and closed_look.json()["seq"] != seq["look"], closed_look.text
    for n, words in (("approve", "Approve"), ("numbers", "2,5"), ("deny", "Deny"), ("words", WORDS),
                     ("hidden then answered", "Approve")):
        answered(client, owner, device, mac, seq[n], made[n][1], words, **fresh(client, owner))
    assert report(mac, seq["report"], "PostToolUse").status_code == 200
    assert report(mac, seq["a day ago"], "Stop").status_code == 200
    owner_closed_before(seq["owner"])
    # when each was hidden and when it closed, set apart so the order is plain: a deadline's close is at the deadline
    t = int(time.time())
    when = {"hidden then answered": (t - 200, t - 5), "waiting": (t - 10, None),
            "hidden then deadline": (t - 300, t - 15), "approve": (None, t - 20), "numbers": (None, t - 30),
            "deny": (None, t - 40), "words": (None, t - 50), "deadline": (None, t - 60), "report": (None, t - 70),
            "owner": (None, t - 80), "look": (None, t - 90), "a day ago": (None, t - 86400 - 100)}
    with store.connect() as db:
        for n, (hidden_at, closed_at) in when.items():
            if hidden_at is not None:
                db.execute("UPDATE ade_link_events SET hidden_at = ? WHERE seq = ?", (hidden_at, seq[n]))
            if n in ("deadline", "hidden then deadline"):
                db.execute("UPDATE ade_link_events SET deadline_at = ? WHERE seq = ?", (closed_at, seq[n]))
            elif closed_at is not None:
                db.execute("UPDATE ade_link_events SET closed_at = ? WHERE seq = ?", (closed_at, seq[n]))
    got = closed_list(client, owner)
    order = ("hidden then answered", "waiting", "hidden then deadline", "approve", "numbers", "deny", "words",
             "deadline", "report", "owner", "look")
    ended = {"hidden then answered": "approved", "waiting": "waiting", "hidden then deadline": "deadline",
             "approve": "approved", "numbers": "approved", "deny": "refused", "words": "words", "deadline": "deadline",
             "report": "report", "owner": "closed", "look": "closed"}
    reason = {"approved": "answer", "refused": "answer", "words": "answer", "deadline": "deadline",
              "report": "report:PostToolUse", "closed": "owner", "waiting": None}
    # neither the prompt still open and not hidden, nor the one closed a day and more ago
    assert [p["seq"] for p in got["prompts"]] == [seq[n] for n in order]
    assert [(p["ended"], p["open"], p["hidden_at"], p["closed_at"], p["closed_reason"]) for p in got["prompts"]] == [
        (ended[n], ended[n] == "waiting", when[n][0], when[n][1], reason[ended[n]]) for n in order]
    # each as the open list gives a prompt, and when it was hidden and how it ended
    (shown,) = [p for p in inbox(client, owner)["prompts"] if p["seq"] == seq["open"]]
    for p in got["prompts"]:
        assert set(p) == set(shown) | {"hidden_at", "ended"}, sorted(set(p) ^ set(shown))
    waiting = got["prompts"][order.index("waiting")]
    assert (waiting["text"], waiting["link_id"], waiting["from"], waiting["look"], waiting["deadline_at"]) == (
        made["waiting"][1], mac.link_id, "mac", False, event_row(seq["waiting"])["deadline_at"])
    assert got["prompts"][order.index("look")]["look"] is True
    dumped = json.dumps(got)
    assert WORDS not in dumped and '"Approve"' not in dumped and '"Deny"' not in dumped and '"2,5"' not in dumped
    assert got["server_time"] >= t


def test_the_closed_list_keeps_a_closed_prompt_for_24_hours_from_its_close(client, owner, keys, monkeypatch):
    mac = paired(client, owner)
    uid = account(client, owner)
    kept, _ = prompt(keys, mac)
    gone, _ = prompt(keys, mac)
    for s in (kept, gone):
        assert report(mac, s, "Stop").status_code == 200
    t = int(time.time()) + 5
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET closed_at = ? WHERE seq = ?", (t - 86400, kept))
        db.execute("UPDATE ade_link_events SET closed_at = ? WHERE seq = ?", (t - 86401, gone))
    pinned(monkeypatch, [t])
    got = closed_list(client, owner)
    assert [p["seq"] for p in got["prompts"]] == [kept] and got["server_time"] == t
    assert link.closed_prompts(uid, now=t) == got


def test_the_closed_list_holds_every_hidden_prompt_still_waiting_and_the_newest_50_closed(client, owner, keys):
    """A busy day's closed prompts are not all served on every read of the list: it holds every hidden prompt still
    open (its link's 20 open bound those) and the newest 50 prompts closed in the window (CLOSED_SHOWN_MOST), says how
    many closed ones it left out (closed_more), and keeps its order, newest first."""
    mac = paired(client, owner)
    waiting, _ = prompt(keys, mac)
    hide(client, owner, waiting)
    closed = []
    for i in range(60):
        s, _ = prompt(keys, mac, f"Bash: echo {i}")
        assert report(mac, s, "Stop").status_code == 200
        closed.append(s)
    t = int(time.time())
    with store.connect() as db:          # each closed a second after the one before it, the hidden one before them all
        db.execute("UPDATE ade_link_events SET hidden_at = ? WHERE seq = ?", (t - 1000, waiting))
        for i, s in enumerate(closed):
            db.execute("UPDATE ade_link_events SET closed_at = ? WHERE seq = ?", (t - 100 + i, s))
    got = closed_list(client, owner)
    assert (len(got["prompts"]), got.get("closed_more")) == (51, 10)
    assert [p["seq"] for p in got["prompts"]] == closed[:9:-1] + [waiting]


def test_the_closed_list_says_it_left_none_out_when_it_holds_every_closed_one(client, owner, keys):
    mac = paired(client, owner)
    closed = [prompt(keys, mac, f"Bash: echo {i}")[0] for i in range(3)]
    for s in closed:
        assert report(mac, s, "Stop").status_code == 200
    got = closed_list(client, owner)
    assert got.get("closed_more") == 0 and sorted(p["seq"] for p in got["prompts"]) == sorted(closed)


def test_the_closed_list_is_the_signed_in_account_s_own(client, owner, keys):
    mac = paired(client, owner)
    mine = [prompt(keys, mac)[0] for _ in range(2)]
    hide(client, owner, mine[0])
    assert report(mac, mine[1], "Stop").status_code == 200
    other, their_keys, theirs = another_account(client)
    their = [prompt(their_keys, theirs)[0] for _ in range(2)]
    hide(client, other, their[0])
    assert report(theirs, their[1], "Stop").status_code == 200
    assert sorted(p["seq"] for p in closed_list(client, owner)["prompts"]) == sorted(mine)
    assert sorted(p["seq"] for p in closed_list(client, other)["prompts"]) == sorted(their)
    # another account's Close reaches none of them, and the list is the owner's sign-in's alone, never a session's
    r = client.post(f"/api/ade/link/questions/{mine[0]}/close", headers=other)
    assert r.status_code == 409, r.text
    assert client.get("/api/ade/link/prompts/closed").status_code == 401
    assert mac.call("GET", "/prompts/closed").status_code == 401


# ---- the open list names the hidden prompts that still wait ----------------------------------------------------------

def test_the_open_list_names_the_hidden_prompts_still_waiting_by_seq_alone_oldest_first(client, owner, keys):
    """Left out of its prompts, a hidden prompt still open is named in hidden_waiting, by its seq alone, oldest first:
    so the page tells nothing waiting in the open list from nothing waiting at all."""
    mac = paired(client, owner)
    first, shown, last = (prompt(keys, mac, f"Bash: echo {i}")[0] for i in range(3))
    hide(client, owner, last)
    hide(client, owner, first)
    view = inbox(client, owner)
    assert view.get("hidden_waiting") == [first, last]
    assert [p["seq"] for p in view["prompts"]] == [shown]


@pytest.mark.parametrize("how", ["deadline", "report", "answer"])
def test_a_hidden_prompt_is_named_as_waiting_no_longer_once_it_closes(client, owner, device, keys, how):
    mac = paired(client, owner)
    seq, text = prompt(keys, mac)
    hide(client, owner, seq)
    assert inbox(client, owner).get("hidden_waiting") == [seq]
    if how == "deadline":
        set_deadline(seq, int(time.time()) - 1)
    elif how == "report":
        assert report(mac, seq, "PostToolUse").status_code == 200
    else:
        answered(client, owner, device, mac, seq, text, "Deny", **fresh(client, owner))
    view = inbox(client, owner)
    assert view["hidden_waiting"] == [] and seq not in [p["seq"] for p in view["prompts"]]


def test_another_account_s_hidden_prompt_is_never_named_as_waiting(client, owner, keys):
    mac = paired(client, owner)
    mine = prompt(keys, mac)[0]
    hide(client, owner, mine)
    other, their_keys, theirs = another_account(client)
    their = prompt(their_keys, theirs)[0]
    hide(client, other, their)
    assert inbox(client, owner).get("hidden_waiting") == [mine]
    assert inbox(client, other).get("hidden_waiting") == [their]


@pytest.mark.parametrize("how", ["deadline", "report"])
def test_the_summary_s_links_stamp_moves_when_a_hidden_prompt_stops_waiting(client, owner, keys, monkeypatch, how):
    """The page reads the open list again, and its list of closed prompts with it, when the summary's links stamp
    moves. A hidden prompt's deadline shows nowhere else in that list, so hidden_waiting moves the stamp then; its
    session's report shows on its link's card as well (the card's count of events and its newest event), so this case
    also reads hidden_waiting itself. A note the link posts after the prompt is its card's newest event, so the hidden
    prompt's own view is not on the card."""
    monkeypatch.delenv("OPEN_EDITION_ADE", raising=False)          # no workspace, whose lists are stamped apart
    mac = paired(client, owner)
    seq, _ = prompt(keys, mac)
    hide(client, owner, seq)
    r, _, _ = send_sealed(keys, mac, "note", "still on it", to="owner")
    assert r.status_code == 200, r.text
    before, waiting = links_stamp(client, owner), inbox(client, owner).get("hidden_waiting")
    assert links_stamp(client, owner) == before                     # it moves only with the list
    if how == "deadline":
        set_deadline(seq, int(time.time()) - 1)
    else:
        assert report(mac, seq, "Stop").status_code == 200
    assert links_stamp(client, owner) != before
    assert (waiting, inbox(client, owner).get("hidden_waiting")) == ([seq], [])


# ---- an item to look at holds no hook: its Close closes it -----------------------------------------------------------

def test_the_close_of_an_item_to_look_at_closes_it_with_the_owner_s_note_and_frees_its_place(client, owner, keys):
    """An item to look at holds no hook (its hook ended as it posted it), so hiding one would keep nothing waiting: its
    Close closes it as Close did before it hid prompts, with the owner's note of fixed text to its session and
    closed_reason owner, and frees its place among its link's 20 open prompts; a second Close answers the same note."""
    mac = paired(client, owner)
    look, _ = prompt(keys, mac, "ExitPlanMode", look=True)
    for i in range(link.OPEN_PROMPTS_PER_LINK - 1):
        prompt(keys, mac, f"Bash: echo {i}")
    r, _, _ = send_sealed(keys, mac, "prompt", "Bash: echo 21", deadline=int(time.time()) + 600)
    assert r.status_code == 429 and "20 prompts open" in r.json()["detail"], r.text
    closed = client.post(f"/api/ade/link/questions/{look}/close", headers=owner)
    assert closed.status_code == 200, closed.text
    note = event_row(closed.json()["seq"])
    assert (note["link_id"], note["kind"], note["to_name"], note["ref"], note["text"], note["needs_turn"]) == (
        "owner", "note", "mac", look, link.CLOSED_ON_PAGE, 0)
    row = event_row(look)
    assert (row["closed_reason"], row["closed_by"], row["hidden_at"]) == ("owner", closed.json()["seq"], None)
    again = client.post(f"/api/ade/link/questions/{look}/close", headers=owner)
    assert again.status_code == 200 and again.json() == closed.json()
    prompt(keys, mac, "Bash: echo 21")                      # its place is free


def test_close_still_hides_an_answerable_prompt_beside_an_item_to_look_at_it_closes(client, owner, keys):
    mac = paired(client, owner)
    look, _ = prompt(keys, mac, "ExitPlanMode", look=True)
    asked, _ = prompt(keys, mac)
    hidden = hide(client, owner, asked)
    closed = client.post(f"/api/ade/link/questions/{look}/close", headers=owner)
    assert closed.status_code == 200 and closed.json()["seq"] != look, closed.text
    assert (event_row(look)["closed_reason"], event_row(asked)["closed_reason"], event_row(asked)["hidden_at"]) == (
        "owner", None, hidden)
    view = inbox(client, owner)
    assert (view["prompts"], view["hidden_waiting"]) == ([], [asked])


def test_hidden_waiting_never_names_an_item_to_look_at(client, owner, keys):
    """Close no longer hides an item to look at; one hidden all the same (by hand here, as a store an older release
    wrote could hold one) is still never named as waiting: nothing waits on it."""
    mac = paired(client, owner)
    uid = account(client, owner)
    look, _ = prompt(keys, mac, "ExitPlanMode", look=True)
    asked, _ = prompt(keys, mac)
    hide(client, owner, asked)
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET hidden_at = ?, hidden_by = ? WHERE seq = ?",
                   (int(time.time()), uid, look))
    assert inbox(client, owner)["hidden_waiting"] == [asked]
