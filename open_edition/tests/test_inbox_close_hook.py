"""The owner inbox's Close, the prompt hook's part (open_edition/ade_link.py permission): in
Community's owner inbox, Close hides a session's prompt without giving it up, so a hidden prompt still reads open and
the hook keeps waiting on it; a prompt nobody answers by its deadline is refused with fixed words that name the
deadline, and the turn goes on, instead of the prompt being left to its window. The ADE's close at the deadline
refuses it. Where the hook's own clock reaches the deadline first (the ADE's may run behind), its last reads, about a
second apart while the hook's bounded time allows, decide: an answer is claimed and decided, the deadline's close (or
a prompt no longer open with no close recorded yet) refuses, a prompt still open or a read that fails is read again,
and any other close, a refusal of the read or the end of its time prints nothing. A failure never refuses, the hook's
clock alone never refuses, and nothing allows by itself. The hook runs as test_ade_link_permission.py runs it: against
its stand-in of the ADE's link routes, with its clock and its sleep."""

import io
import json
import os
import sys
import threading
import time

import pytest

import ade_link
from test_ade_link_permission import SESSION, asking, decision, hook, ran, rig, signed, unanswered  # noqa: F401

# the clock's start in the cases that read the words whole: the deadline the hook posts, thirty minutes on, is
# 2026-12-31 23:59:59 UTC, a second before a new minute, day and year
DECEMBER = 1798759799
REFUSED_IN_DECEMBER = {"behavior": "deny", "message": "Not answered in the owner's list by its deadline, "
                                                      "2026-12-31 23:59 UTC: refused, and the turn goes on."}
OPEN = {"kind": "prompt", "closed_reason": None, "open": True}      # as an open prompt reads, a hidden one included


def by_the_clock(rig, *pasts):
    """The ADE's view of the prompt as the hook's clock goes: open while the clock is before the deadline the hook
    posted; from the deadline on, `pasts` in turn, one to each read and the last to every read after: each a view
    (the prompt's seq filled in where it names none), or the exception the read raises."""
    gone = []

    def view(seq, n):
        if rig.clock.t < rig.ade.events[seq]["deadline"]:
            return dict(OPEN, seq=seq)
        gone.append(n)
        past = pasts[min(len(gone), len(pasts)) - 1]
        if isinstance(past, BaseException):
            raise past
        return dict({"seq": seq, "kind": "prompt"}, **past) if isinstance(past, dict) else past
    rig.ade.view = view


def timed(rig, monkeypatch):
    """The hook's clock and the timeout it gave (as Link.request takes it) at each of its reads of the prompt."""
    seen = []
    real = rig.ade.request

    def request(method, path, body=None, **k):
        if method == "GET" and path.startswith("/events/"):
            seen.append((rig.clock.t, k.get("timeout") or ade_link.TIMEOUT))
        return real(method, path, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: request(*a, **k))
    return seen


def clocked(rig):
    """The hook's clock at each of its reads of the prompt, as the reads come."""
    times = []
    rig.ade.on_read = lambda seq, n: times.append(rig.clock.t)
    return times


def kept(rig):
    """The one item prompts.json keeps, as the hook left it."""
    (item,) = json.loads((rig.dir / "prompts.json").read_text())
    return item


def grace():
    """PROMPT_LAST_GRACE, the seconds past the deadline the hook's last reads go on: asserted to be there, so a case
    that needs it fails on its own assertion where the hook has none."""
    value = getattr(ade_link, "PROMPT_LAST_GRACE", None)
    assert isinstance(value, int) and value > 0, value
    return value


def behind(rig, seconds):
    """The ADE's view of the prompt with its clock `seconds` behind the hook's: open until its own clock reaches the
    deadline, closed by it from then on, as its read past a deadline records the close."""
    def view(seq, n):
        due = rig.clock.t - seconds >= rig.ade.events[seq]["deadline"]
        return {"seq": seq, "kind": "prompt", "closed_reason": "deadline" if due else None, "open": not due}
    rig.ade.view = view


def no_report_reaches_the_ade(rig, monkeypatch):
    """The session's reports go nowhere: their post meets no answer, so the ADE keeps the prompt as it was."""
    real = rig.ade.request

    def request(method, path, body=None, **k):
        if (method, path) == ("POST", "/events") and body.get("kind") == "note":
            raise ade_link.Unreachable("no answer")
        return real(method, path, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: request(*a, **k))


# ---- the deadline refuses -------------------------------------------------------------------------------------------

def test_the_ade_s_close_at_the_deadline_refuses_the_prompt_with_words_naming_the_deadline_and_the_turn_goes_on(
        rig, monkeypatch, capsys):
    """The ADE's read records the deadline's close: the hook refuses at that read, with fixed words that name the
    deadline it posted as YYYY-MM-DD HH:MM UTC, and no interrupt, so the turn goes on as it does on the owner's own
    words. Nothing is claimed."""
    rig.clock.t = float(DECEMBER)
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "deadline" if n > 1 else None,
                                   "open": n <= 1}
    code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
    assert code == 0 and err == ""
    got = decision(out)
    assert got == REFUSED_IN_DECEMBER and "interrupt" not in got
    (posted,) = rig.ade.posts()
    assert posted["deadline"] == DECEMBER + 1800
    assert len(rig.ade.asked("GET", "/events/")) == 2 and rig.ade.asked("POST", "/prompts/") == []
    assert rig.clock.t < posted["deadline"]              # taken at the read that showed it, not waited out


@pytest.mark.parametrize("last", ["the deadline's close", "no close recorded yet"])
def test_past_the_hook_s_own_clock_a_last_read_that_shows_the_deadline_s_close_or_no_close_yet_refuses_the_prompt(
        rig, monkeypatch, capsys, last):
    """The prompt reads open until the hook's own clock reaches the deadline. Then one more read: one that shows the
    prompt closed by its deadline, or no longer open with no close recorded yet (the ADE records it at its next read),
    refuses it with the words that name the deadline."""
    rig.clock.t = float(DECEMBER)
    by_the_clock(rig, {"the deadline's close": {"closed_reason": "deadline", "open": False},
                       "no close recorded yet": {"closed_reason": None, "open": False}}[last])
    times = clocked(rig)
    code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
    assert code == 0 and err == ""
    assert decision(out) == REFUSED_IN_DECEMBER
    deadline = DECEMBER + 1800
    assert len([t for t in times if t < deadline]) == 360            # read every 5 s for its thirty minutes
    assert [t for t in times if t >= deadline] == [deadline]         # and once more when its clock reached the deadline
    assert rig.ade.asked("POST", "/prompts/") == []


def test_a_refusal_at_the_deadline_ends_the_item_as_the_deadline_s_so_a_later_run_of_the_call_is_still_reported(
        rig, monkeypatch, capsys):
    """The window may have let the call run first, its answer coming before the deadline and the run ending after it,
    and the hook is never told which answer won. So the refusal at the deadline ends the item with an end of its own,
    "deadline", which a later report of the call still reports, as "none" is: only a denial on the owner's verified
    answer ("deny") keeps the call's report from its item. The call's PostToolUse posts its report for the prompt's
    seq."""
    by_the_clock(rig, {"closed_reason": "deadline", "open": False})
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    assert code == 0 and decision(out) == unanswered(posted["deadline"])
    item = kept(rig)
    assert (item["seq"], item["ended"], item["waiting"]) == (41, "deadline", None)
    ran(rig, monkeypatch, asking(rig))
    assert [(b["ref"], b["text"]) for b in rig.ade.posts("note")] == [(41, "reported: PostToolUse")]
    assert json.loads((rig.dir / "prompts.json").read_text()) == []


def test_through_permission_quietly_the_refusal_is_what_is_printed_at_the_deadline(rig, monkeypatch, capsys):
    """The hook as the link's block runs it on a PermissionRequest: at the deadline it prints one line, Claude Code's
    decision for the permission request, the refusal with the words that name the deadline, and exits 0."""
    rig.clock.t = float(DECEMBER)
    by_the_clock(rig, {"closed_reason": "deadline", "open": False})
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(asking(rig))))
    assert ade_link.permission_quietly(str(rig.dir)) == 0
    out, err = capsys.readouterr()
    assert err == "" and out.endswith("\n") and out.count("\n") == 1
    assert json.loads(out) == {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                      "decision": REFUSED_IN_DECEMBER}}


def test_a_read_in_the_wait_showing_the_prompt_not_open_with_no_close_goes_on_to_the_last_reads_which_refuse(
        rig, monkeypatch, capsys):
    """The ADE's read records the deadline's close (close_due) and then views the prompt on a fresh clock: where the
    deadline's second passes between the two, a read while the hook waits shows the prompt not open with no close
    recorded. That is the ADE's deadline, not the end of the prompt: the hook goes on to its last reads, and refuses on
    the one that shows the deadline's close, its own clock still before the deadline."""
    rig.clock.t = float(DECEMBER)
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "deadline" if n > 2 else None,
                                   "open": n <= 1}
    times = clocked(rig)
    code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
    assert code == 0 and err == ""
    assert decision(out) == REFUSED_IN_DECEMBER
    assert len(times) == 3 and all(t < DECEMBER + 1800 for t in times), times
    assert rig.ade.asked("POST", "/prompts/") == []


def test_the_refusal_s_words_name_the_deadline_s_minute_in_utc_in_a_zone_that_is_not_utc(rig, monkeypatch, capsys):
    """The words name the deadline's minute in UTC whatever zone the hook's process runs in. TZ is set here to a zone
    five hours and forty-five minutes ahead of UTC (a POSIX rule, which needs no zone files), time.tzset() takes it, and
    both are restored after the case: in that zone the deadline reads 2027-01-01 05:44, so words written in local time
    would name it wrongly even on a machine whose own zone is UTC."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is not available here")
    before = os.environ.get("TZ")
    os.environ["TZ"] = "NPT-5:45"
    time.tzset()
    try:
        assert time.strftime("%Y-%m-%d %H:%M", time.localtime(DECEMBER + 1800)) == "2027-01-01 05:44"  # the zone took
        rig.clock.t = float(DECEMBER)
        rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "deadline" if n > 1 else None,
                                       "open": n <= 1}
        code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()
    assert code == 0 and err == ""
    assert decision(out) == REFUSED_IN_DECEMBER


@pytest.mark.parametrize("when", ["while the hook slept", "while the hook read"])
def test_a_read_in_the_wait_showing_the_deadline_s_close_refuses_nothing_once_a_report_took_the_item_out(
        rig, monkeypatch, capsys, when):
    """The call ran (the window answered the prompt): the session's report took its item out of prompts.json, and its
    post never reached the ADE, which then closes the prompt by its deadline. The hook checks that it still holds the
    item after each read and before acting on it, so it refuses nothing, whether the report came while it slept
    between two reads or while it made the read that shows the close."""
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "deadline" if n > 1 else None,
                                   "open": n <= 1}
    no_report_reaches_the_ade(rig, monkeypatch)
    given = asking(rig)
    reported = []

    def report():
        if not reported:
            reported.append(rig.clock.t)
            ran(rig, monkeypatch, given)            # its report drops the item; its post goes nowhere
    if when == "while the hook slept":
        def waits(seconds):
            rig.clock.sleep(seconds)
            report()
        monkeypatch.setattr(ade_link, "sleep", waits)
    else:
        rig.ade.on_read = lambda seq, n: report() if n == 2 else None
    code, out, _ = hook(rig, monkeypatch, capsys, given)
    (posted,) = rig.ade.posts()
    assert reported and (code, out) == (0, "")
    assert rig.clock.t < posted["deadline"] and len(rig.ade.asked("GET", "/events/")) == 2
    assert json.loads((rig.dir / "prompts.json").read_text()) == []


def test_a_last_read_showing_the_deadline_s_close_refuses_nothing_once_a_report_took_the_item_out_as_it_read(
        rig, monkeypatch, capsys):
    """The same at the last reads: the session's report takes the item out, its post going nowhere, while the hook
    makes its first read past the deadline, which shows the deadline's close. The hook checks the item after the
    read, before acting on it, and refuses nothing."""
    by_the_clock(rig, {"closed_reason": "deadline", "open": False})
    no_report_reaches_the_ade(rig, monkeypatch)
    given = asking(rig)
    reported = []

    def as_it_reads(seq, n):
        if not reported and rig.clock.t >= rig.ade.events[seq]["deadline"]:
            reported.append(rig.clock.t)
            ran(rig, monkeypatch, given)
    rig.ade.on_read = as_it_reads
    code, out, _ = hook(rig, monkeypatch, capsys, given)
    (posted,) = rig.ade.posts()
    assert reported == [posted["deadline"]] and (code, out) == (0, "")
    assert json.loads((rig.dir / "prompts.json").read_text()) == []


# ---- an answer is still taken; anything else at the deadline prints nothing ----------------------------------------

@pytest.mark.parametrize("text, wanted", [
    ("Approve", {"behavior": "allow"}),
    ("Deny", {"behavior": "deny", "message": "Denied in the owner's list", "interrupt": True}),
    ("Not this one: run the unit tests first.", {"behavior": "deny",
                                                 "message": "Not this one: run the unit tests first."}),
])
def test_an_answer_that_came_just_before_the_deadline_is_claimed_at_the_last_read_and_decided(rig, monkeypatch,
                                                                                             capsys, text, wanted):
    """The owner answered between the hook's last read before the deadline and the deadline: the last read shows the
    answer's close, and the hook claims the answer, checks it and decides on it as before the deadline."""
    by_the_clock(rig, {"closed_reason": "answer", "open": False})
    rig.ade.claim = lambda seq, digest: signed(rig, seq, digest, text)
    times = clocked(rig)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    assert code == 0 and decision(out) == wanted
    assert len([t for t in times if t >= posted["deadline"]]) == 1
    assert rig.ade.asked("POST", "/prompts/") == ["/prompts/41/claim"]
    assert kept(rig)["ended"] == wanted["behavior"]


CLAIM_FAILURES = {
    "the deadline passed before the claim": ade_link.Refused(409, "not claimed", {"why": "deadline"}),
    "still open": ade_link.Refused(409, "not claimed", {"why": "open"}),
    "a 502": ade_link.Refused(502, "HTTP 502"),
    "no answer": ade_link.Unreachable("no answer"),
}


@pytest.mark.parametrize("why", list(CLAIM_FAILURES))
def test_an_answer_the_last_reads_show_whose_claim_does_not_take_prints_nothing_and_refuses_nothing(
        rig, monkeypatch, capsys, why):
    """An answer is the owner's, never the deadline's: where its claim at the last reads is refused (the ADE's
    deadline passed first) the hook prints nothing at once, and where it is to be made again (the ADE reads it open,
    or gives a 5xx or no answer) the answer is read and claimed again while the hook's time allows, then nothing is
    printed, as before the deadline."""
    by_the_clock(rig, {"closed_reason": "answer", "open": False})
    failure = CLAIM_FAILURES[why]

    def refused(seq, digest):
        raise failure
    rig.ade.claim = refused
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert (code, out) == (0, "")
    claims = rig.ade.asked("POST", "/prompts/")
    if why == "the deadline passed before the claim":
        assert claims == ["/prompts/41/claim"]
    else:
        assert len(claims) >= 2 and set(claims) == {"/prompts/41/claim"}
    assert kept(rig)["ended"] == "none"


@pytest.mark.parametrize("why", ["still open", "a 502", "no answer"])
def test_an_answer_whose_claim_is_to_be_made_again_at_the_last_reads_is_claimed_again_and_decided(rig, monkeypatch,
                                                                                                capsys, why):
    """The answer's first claim past the deadline comes back to be made again: the hook reads the prompt again about
    a second on, claims the answer again, and decides on it."""
    by_the_clock(rig, {"closed_reason": "answer", "open": False})
    claimed = []

    def again_then_taken(seq, digest):
        claimed.append(rig.clock.t)
        if len(claimed) == 1:
            raise CLAIM_FAILURES[why]
        return signed(rig, seq, digest, "Approve")
    rig.ade.claim = again_then_taken
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    assert code == 0 and decision(out) == {"behavior": "allow"}
    assert len(claimed) == 2 and claimed[0] == posted["deadline"] and 0.5 <= claimed[1] - claimed[0] <= 1.5
    assert kept(rig)["ended"] == "allow"


FIRST_READS = {
    "still open, as the ADE reads it while its clock is behind": {"closed_reason": None, "open": True},
    "the ADE unreachable": ade_link.Unreachable("no answer"),
    "a 502": ade_link.Refused(502, "HTTP 502"),
    "a 429": ade_link.Refused(429, "Too many requests."),
    "a view of another event": {"seq": 99, "closed_reason": None, "open": True},
}
THEN = {
    "the deadline's close": {"closed_reason": "deadline", "open": False},
    "no close recorded yet": {"closed_reason": None, "open": False},
}


@pytest.mark.parametrize("then", list(THEN))
@pytest.mark.parametrize("first", list(FIRST_READS))
def test_a_last_read_that_shows_the_prompt_open_or_fails_is_made_again_and_the_deadline_s_close_then_refuses(
        rig, monkeypatch, capsys, first, then):
    """The ADE's clock may run behind the hook's, so its first read past the deadline can still show the prompt open,
    and a read can fail: the hook reads again about a second on, and refuses on the read that shows the deadline's
    close, or the prompt no longer open with no close recorded yet."""
    rig.clock.t = float(DECEMBER)
    by_the_clock(rig, FIRST_READS[first], THEN[then])
    times = clocked(rig)
    code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
    assert code == 0 and err == ""
    assert decision(out) == REFUSED_IN_DECEMBER
    deadline = DECEMBER + 1800
    past = [t for t in times if t >= deadline]
    assert len(past) == 2 and past[0] == deadline and 0.5 <= past[1] - past[0] <= 1.5, past
    assert rig.ade.asked("POST", "/prompts/") == []


EVERY_READ = {           # a prompt still open as the ADE's clock behind reads it: test_..._behind_past_the_grace_...
    "with no word of open": ({"closed_reason": None},),
    "the ADE unreachable": (ade_link.Unreachable("no answer"),),
    "a 502": (ade_link.Refused(502, "HTTP 502"),),
    "a 503": (ade_link.Refused(503, "HTTP 503"),),
    "a 429": (ade_link.Refused(429, "Too many requests."),),
    "a view of another event": ({"seq": 99, "closed_reason": "deadline", "open": False},),
    "an answer that is not an object": (["deadline"],),
    "open and unreachable in turn": ({"closed_reason": None, "open": True}, ade_link.Unreachable("no answer")) * 6,
}


@pytest.mark.parametrize("case", list(EVERY_READ))
def test_with_every_last_read_open_or_failing_until_the_hook_s_time_ends_nothing_is_printed(rig, monkeypatch,
                                                                                           capsys, case):
    """A refusal is never given on a failure or on the hook's clock alone: where every read past the deadline shows
    the prompt still open, fails (no answer, a 5xx, a 429) or shows another event, the hook reads again while the
    wait's bound (the deadline and PROMPT_LAST_GRACE) leaves more than PROMPT_LAST_SPARE, each read's timeout within
    what is left, and then prints nothing, which leaves the prompt where Claude Code shows it."""
    by_the_clock(rig, *EVERY_READ[case])
    reads = timed(rig, monkeypatch)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    deadline = posted["deadline"]
    end = deadline + grace() - ade_link.PROMPT_LAST_SPARE                  # the bound, less its spare
    assert (code, out) == (0, "")
    past = [(t, timeout) for t, timeout in reads if t >= deadline]
    assert len(past) >= 3, past                                             # read again, and again
    assert all(0 < timeout and t + timeout <= end for t, timeout in past), (past, end)
    assert end - 1.5 <= past[-1][0] < end, (past, end)                      # until the bound left only its spare
    assert rig.ade.asked("POST", "/prompts/") == []
    assert kept(rig)["ended"] == "none"


@pytest.mark.parametrize("refusal", [ade_link.Refused(403, "Not this link's event."),
                                     ade_link.Refused(401, "No such link.")], ids=["a refusal", "a refusal of the link"])
def test_a_last_read_the_ade_refuses_ends_the_hook_at_once_printing_nothing(rig, monkeypatch, capsys, refusal):
    """A refusal of the read (a link the ADE no longer takes) ends the hook as it does before the deadline: at once,
    with nothing printed, and never a refusal of the prompt."""
    by_the_clock(rig, refusal, {"closed_reason": "deadline", "open": False})
    times = clocked(rig)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    assert (code, out) == (0, "")
    assert len([t for t in times if t >= posted["deadline"]]) == 1
    assert rig.ade.asked("POST", "/prompts/") == []
    assert kept(rig)["ended"] == "none"


def test_a_prompt_whose_call_ran_is_never_refused_at_the_deadline_though_its_report_never_reached_the_ade(
        rig, monkeypatch, capsys):
    """The call ran as the hook's clock reached the deadline (the window answered the prompt): the session's report
    took the item out of prompts.json, and its post never reached the ADE, which then closes the prompt by its
    deadline. The hook no longer holds the item, as it checks before each read, and refuses nothing."""
    by_the_clock(rig, {"closed_reason": "deadline", "open": False})
    real = rig.ade.request

    def no_report(method, path, body=None, **k):
        if (method, path) == ("POST", "/events") and body.get("kind") == "note":
            raise ade_link.Unreachable("no answer")
        return real(method, path, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: no_report(*a, **k))
    given = asking(rig)
    reported = []

    def waits(seconds):
        rig.clock.sleep(seconds)
        if not reported and rig.clock.t >= rig.ade.posts()[0]["deadline"]:
            reported.append(rig.clock.t)
            ran(rig, monkeypatch, given)            # its report drops the item; its post goes nowhere
    monkeypatch.setattr(ade_link, "sleep", waits)
    code, out, _ = hook(rig, monkeypatch, capsys, given)
    assert reported and (code, out) == (0, "")
    assert json.loads((rig.dir / "prompts.json").read_text()) == [] and rig.ade.posts("note") == []


@pytest.mark.parametrize("closed", ["owner", "report:PostToolUse", "report:Stop"])
@pytest.mark.parametrize("when", ["in the wait", "at the last read"])
def test_an_older_close_by_the_owner_or_a_report_prints_nothing_in_the_wait_or_at_the_last_read(rig, monkeypatch,
                                                                                               capsys, closed, when):
    """A close the owner made before Close only hid a prompt (closed_reason owner), and the session's report, decide
    nothing, whether the hook reads it while it waits or at its last read: it is never taken for the deadline."""
    if when == "in the wait":
        rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": closed if n > 1 else None,
                                       "open": n <= 1}
    else:
        by_the_clock(rig, {"closed_reason": closed, "open": False})
    times = clocked(rig)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    assert (code, out) == (0, "")
    assert rig.ade.asked("POST", "/prompts/") == []
    assert len([t for t in times if t >= posted["deadline"]]) == (0 if when == "in the wait" else 1)
    assert len(times) == (2 if when == "in the wait" else 361)


def test_a_prompt_that_stays_open_as_a_hidden_one_reads_keeps_the_hook_waiting_until_an_answer_comes_which_it_takes(
        rig, monkeypatch, capsys):
    """The owner hid the prompt: it still reads open, so the hook keeps waiting, ten minutes here, and takes the answer
    that then comes."""
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "answer" if n > 120 else None,
                                   "open": n <= 120}
    rig.ade.claim = lambda seq, digest: signed(rig, seq, digest, "Approve")
    times = clocked(rig)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    assert code == 0 and decision(out) == {"behavior": "allow"}
    assert len(times) == 121 and rig.ade.asked("POST", "/prompts/") == ["/prompts/41/claim"]
    assert times[-1] - times[0] == 600 and rig.clock.t < posted["deadline"]


# ---- the ADE's clock behind the session's: the last reads' grace -----------------------------------------------------

def test_the_last_reads_grace_fits_inside_the_hook_s_timeout_in_the_settings():
    """The PermissionRequest hook's timeout, as the link's block in the settings gives it, leaves room for what the
    hook's run takes: its input read before its deadline is fixed (HOOK_DEADLINE), the whole wait (PROMPT_WAIT), the
    last reads' grace past the deadline (PROMPT_LAST_GRACE), at which the wait's bound ends it, a claim included, and
    10 seconds for Python to start and the client to compile. (A claim's TIMEOUT adds nothing: the bound ends it.)"""
    path = os.path.join(os.path.dirname(os.path.abspath(ade_link.__file__)), "ade_link.settings.json")
    with open(path, encoding="utf-8") as fh:
        (block,) = json.load(fh)["hooks"]["PermissionRequest"]
    timeout = block["hooks"][0]["timeout"]
    assert timeout == ade_link.PROMPT_HOOK_TIMEOUT
    assert ade_link.HOOK_DEADLINE + ade_link.PROMPT_WAIT + grace() + 10 < timeout


@pytest.mark.parametrize("seconds", [10, 25])
def test_with_the_ade_s_clock_behind_within_the_grace_the_last_reads_refuse_when_it_reaches_the_deadline(
        rig, monkeypatch, capsys, seconds):
    """The ADE takes a session's requests with the two clocks up to 300 s apart (SKEW), and closes the prompt when its
    own clock reaches the deadline. The hook's last reads go on a second apart for PROMPT_LAST_GRACE past its own
    deadline: with the ADE's clock 10 or 25 s behind, the read that meets its close refuses the prompt."""
    rig.clock.t = float(DECEMBER)
    behind(rig, seconds)
    times = clocked(rig)
    code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
    assert code == 0 and err == ""
    assert decision(out) == REFUSED_IN_DECEMBER
    deadline = DECEMBER + 1800
    past = [t for t in times if t >= deadline]
    assert past[0] == deadline and past[-1] == deadline + seconds, past
    assert rig.ade.asked("POST", "/prompts/") == []


def test_with_the_ade_s_clock_behind_past_the_grace_the_last_reads_end_with_nothing_printed(rig, monkeypatch, capsys):
    """With the ADE's clock 40 s behind, past the grace, every read in the hook's time shows the prompt still open:
    the hook reads a second apart until the wait's bound leaves only PROMPT_LAST_SPARE, each read's timeout within what
    is left, and then prints nothing rather than refuse on its own clock."""
    rig.clock.t = float(DECEMBER)
    behind(rig, 40)
    reads = timed(rig, monkeypatch)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    deadline = DECEMBER + 1800
    end = deadline + grace() - ade_link.PROMPT_LAST_SPARE                  # the bound, less its spare
    assert (code, out) == (0, "")
    past = [(t, timeout) for t, timeout in reads if t >= deadline]
    assert all(0 < timeout and t + timeout <= end for t, timeout in past), (past, end)
    assert end - 1.5 <= past[-1][0] < end, (past, end)                      # until the bound left only its spare
    assert rig.ade.asked("POST", "/prompts/") == []
    assert kept(rig)["ended"] == "none"


def test_a_last_read_the_ade_answers_late_within_the_grace_still_refuses_in_real_time(rig, monkeypatch, capsys):
    """The hook's own bound (_prompt_request's, in real time) runs to the deadline and the grace too: a last read the
    ADE answers six real seconds late, with the deadline's close, still refuses. Here the wait is 2 s, PROMPT_POLL 1 s
    and the grace 10 s, so a bound of the deadline and PROMPT_POLL would have ended the hook three seconds in."""
    monkeypatch.setattr(ade_link, "PROMPT_WAIT", 2)
    monkeypatch.setattr(ade_link, "PROMPT_POLL", 1)
    monkeypatch.setattr(ade_link, "PROMPT_LAST_GRACE", 10, raising=False)
    gate = threading.Event()
    real = rig.ade.request

    def late(method, path, body=None, **k):
        if method == "GET" and path.startswith("/events/"):
            seq = int(path.rsplit("/", 1)[1])
            if rig.clock.t >= rig.ade.events[seq]["deadline"]:
                gate.wait(6)                    # six real seconds, then the ADE's answer
                return {"seq": seq, "kind": "prompt", "closed_reason": "deadline", "open": False}
        return real(method, path, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: late(*a, **k))
    started = time.monotonic()
    try:
        code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
        took = time.monotonic() - started
    finally:
        gate.set()
        time.sleep(0.3)                         # the waiting thread, released, ends while the stand-ins are in place
    (posted,) = rig.ade.posts()
    assert code == 0 and err == "" and decision(out) == unanswered(posted["deadline"])
    assert 6 <= took < 2 + 10, took


# ---- the last read is bounded inside the hook's time -----------------------------------------------------------------

def test_the_last_read_is_given_a_timeout_that_ends_it_inside_the_hook_s_wait(rig, monkeypatch, capsys):
    """_prompt_request bounds the wait at the deadline and PROMPT_LAST_GRACE. A read before the deadline is bounded by
    it, but by a second at least, so one begun half a second before it ends half a second past it: each last read,
    the first begun then, is given a timeout that still ends inside the bound, less PROMPT_LAST_SPARE. Each read here
    takes its whole timeout; none past the deadline answers, and nothing is printed."""
    reads = []
    real = rig.ade.request

    def slow(method, path, body=None, **k):
        if method == "GET" and path.startswith("/events/"):
            seq = int(path.rsplit("/", 1)[1])
            deadline = rig.ade.events[seq]["deadline"]
            start, timeout = rig.clock.t, k.get("timeout") or ade_link.TIMEOUT   # as Link.request takes it
            reads.append((start, timeout, deadline))
            rig.clock.t += timeout
            if start >= deadline:
                raise ade_link.Unreachable("timed out")
            return dict(OPEN, seq=seq)
        return real(method, path, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: slow(*a, **k))
    first = []

    def waits(seconds):
        if not first:                       # its first wait ends half a second before the deadline
            first.append(seconds)
            rig.clock.t = reads[0][2] - 0.5
        else:
            rig.clock.sleep(seconds)
    monkeypatch.setattr(ade_link, "sleep", waits)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    deadline = reads[0][2]
    end = deadline + grace() - ade_link.PROMPT_LAST_SPARE                  # the bound, less its spare
    assert (code, out) == (0, "")
    before = [(s, t) for s, t, _ in reads if s < deadline]
    past = [(s, t) for s, t, _ in reads if s >= deadline]
    assert before[-1] == (deadline - 0.5, 1)                          # the read before it ends half a second past it
    assert len(past) >= 2 and past[0][0] > deadline, past             # read again, the first begun past the deadline
    assert all(0 < t and s + t <= end for s, t in past), (past, end)


def test_with_no_time_left_in_the_hook_s_wait_no_last_read_is_made_and_nothing_is_printed(rig, monkeypatch, capsys):
    """The machine slept through the deadline and its grace (a laptop's lid closed), and the hook's wait woke after
    them: its bound leaves no time for a read whose answer could count, so it makes none and prints nothing, though
    the ADE would show the deadline's close. It never refuses on its clock alone."""
    past_the_grace = grace()
    times = clocked(rig)
    by_the_clock(rig, {"closed_reason": "deadline", "open": False})
    first = []

    def waits(seconds):
        if not first:                       # its first wait ends past the deadline and the grace
            first.append(seconds)
            rig.clock.t = rig.ade.posts()[0]["deadline"] + past_the_grace
        else:
            rig.clock.sleep(seconds)
    monkeypatch.setattr(ade_link, "sleep", waits)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    assert (code, out) == (0, "")
    assert first and [t for t in times if t >= posted["deadline"]] == [] and len(times) == 1


def test_a_last_read_that_never_answers_ends_the_hook_within_its_bound_printing_nothing(rig, monkeypatch, capsys):
    """A last read blocked where no socket timeout ends it (a name lookup that never answers) is ended by the hook's
    own bound, the deadline and PROMPT_LAST_GRACE (3 s here): the hook prints nothing, and never outlives the time
    Claude Code gives it."""
    monkeypatch.setattr(ade_link, "PROMPT_WAIT", 2)
    monkeypatch.setattr(ade_link, "PROMPT_LAST_GRACE", 3, raising=False)
    gate = threading.Event()
    real = rig.ade.request
    past = []

    def reading(method, path, body=None, **k):
        if method == "GET" and path.startswith("/events/") \
                and rig.clock.t >= rig.ade.events[int(path.rsplit("/", 1)[1])]["deadline"]:
            past.append(rig.clock.t)
            gate.wait(30)
            raise ade_link.Unreachable("no answer")
        return real(method, path, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: reading(*a, **k))
    started = time.monotonic()
    try:
        code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
        took = time.monotonic() - started
        blocked = list(past)                    # counted before the gate frees the blocked read: the thread the hook
        # left behind then reads on, which the hook's own process, left through os._exit, would never let it do
    finally:
        gate.set()
        time.sleep(0.3)                         # the waiting thread, released, ends while the stand-ins are in place
    assert (code, out) == (0, "")
    assert len(blocked) == 1                    # it made its last read, which never answered
    assert took < 2 + 3 + 3, took
