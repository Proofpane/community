"""Agents that ask the person for a session (proofpane_workspace/asks.py): a session's token names it and nothing
else, and dies with its program; an ask waits for the person, within bounds, and a session started from one may not
ask in turn."""

import pytest

from proofpane_workspace import asks


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def bound(**over):
    """What a token was issued for (asks.Asks.bound_to): its session, its repository, and whether it may ask."""
    return dict({"session": "0123456789ab", "repo": "demo", "may_ask": True}, **over)


def on(**kw):
    a = asks.Asks(**kw)
    a.set_enabled(True)
    return a


def test_a_token_names_its_session_its_repository_and_nothing_else_and_dies_with_its_program():
    a = on()
    t1 = a.issue("0123456789ab", "demo", True)
    t2 = a.issue("ba9876543210", "private", False)
    assert a.bound_to(t1) == {"session": "0123456789ab", "repo": "demo", "may_ask": True}
    assert a.bound_to(t2) == {"session": "ba9876543210", "repo": "private", "may_ask": False}
    for other in ("", "x" * 10, "x" * 300, t1[:-1] + ("A" if t1[-1] != "A" else "B"), None, 7):
        assert a.session_of(other) is None, other
    # a new token for a session replaces its old one; a program's end takes it away
    t3 = a.issue("0123456789ab", "demo", True)
    assert a.session_of(t1) is None and a.session_of(t3) == "0123456789ab"
    a.revoke("0123456789ab")
    assert a.session_of(t3) is None and a.session_of(t2) == "ba9876543210"


def test_off_no_token_is_issued_no_ask_taken_and_turning_it_off_clears_both_at_once():
    a = asks.Asks()
    assert a.enabled is False and a.issue("0123456789ab", "demo", True) is None
    with pytest.raises(asks.AskError, match="turned asks off"):
        a.ask(bound(), "codex", "x")
    a.set_enabled(True)
    t = a.issue("0123456789ab", "demo", True)
    ask = a.ask(bound(), "codex", "x")
    a.set_enabled(False)
    assert a.bound_to(t) is None and a.waiting() == []
    a.put_back(ask)                      # a start that failed after the switch went off: the ask stays gone
    a.set_enabled(True)
    assert a.waiting() == [] and a.bound_to(t) is None     # on again: the old token is still nothing


def test_an_ask_waits_for_the_person_whole_and_is_answered_once():
    a = on()
    ask = a.ask(bound(), "codex", "  Write the tests for the parser  ", base="main")
    assert {k: ask[k] for k in ("from", "repo", "program", "task", "base")} == {
        "from": "0123456789ab", "repo": "demo", "program": "codex", "task": "Write the tests for the parser", "base": "main"}
    assert [w["id"] for w in a.waiting()] == [ask["id"]]
    taken = a.take(ask["id"], starting=True)        # taken by the person's Start
    assert taken == ask and a.take(ask["id"]) is None and a.waiting() == []
    a.put_back(taken)                     # its start failed: it is there to be answered again
    assert [w["id"] for w in a.waiting()] == [ask["id"]]


@pytest.mark.parametrize("program, task, base, b, said", [
    ("shell", "x", None, bound(), "terminal agents"),
    ("chat", "x", None, bound(), "terminal agents"),
    ("codex", "  ", None, bound(), "what the session is for"),
    ("codex", "x" * (asks.MAX_TASK + 1), None, bound(), "what the session is for"),
    ("codex", "x", "--upload-pack=evil", bound(), "Name the base"),
    ("codex", "x", "a b", bound(), "Name the base"),
    ("codex", "x", "main..HEAD", bound(), "Name the base"),
    ("codex", "x", "main" + chr(0x202E), bound(), "Name the base"),
    ("codex", "x", None, bound(repo=None), "on a repository"),
    ("codex", "x", None, bound(may_ask=False), "may not ask for another"),
])
def test_an_ask_that_is_not_one_is_refused_with_why(program, task, base, b, said):
    with pytest.raises(asks.AskError, match=said):
        on().ask(b, program, task, base=base)


@pytest.mark.parametrize("hidden", [
    0x0000, 0x001B, 0x007F, 0x0085, 0x009B,                  # control characters
    0x061C, 0x200E, 0x200F, 0x202A, 0x202E, 0x2066, 0x2069,  # those that turn text's direction
    0x200B, 0x200C, 0x200D, 0x2060, 0x2064, 0xFEFF, 0x00AD,  # zero-width ones, the byte order mark, a soft hyphen
    0xE0041, 0xE007F,                                        # tag characters, which can spell out words unseen
    0xFE00, 0xFE0D, 0xE0100, 0xE01EF,                        # variation selectors but the emoji ones
    0x115F, 0x1160, 0x3164, 0xFFA0,                          # Hangul fillers, which show as nothing
    0x2028, 0x2029,                                          # line and paragraph separators
    0xE000, 0xF8FF, 0x10FFFD,                                # private use
    0x0378,                                                  # unassigned
])
def test_a_task_holding_what_the_person_would_not_see_is_refused(hidden):
    with pytest.raises(asks.AskError, match="A task is text that shows"):
        on().ask(bound(), "codex", "Review the parser." + chr(hidden) + " and more")


def test_a_task_in_any_script_with_its_line_breaks_tabs_and_emoji_is_taken():
    for task in ("Fix it\n\tplease", "caf" + chr(0xE9), chr(0x4E2D) + chr(0x6587), "done " + chr(0x2764) + chr(0xFE0F),
                 "x" + chr(0x1F600)):
        assert on().ask(bound(), "codex", task)["task"] == task.strip()


def test_asks_are_bounded_a_session_and_the_workspace_and_end_in_a_day():
    clock = Clock()
    a = on(clock=clock)
    for _ in range(asks.MAX_OPEN_A_SESSION):
        a.ask(bound(), "codex", "x")
    with pytest.raises(asks.AskError, match="asks waiting"):
        a.ask(bound(), "codex", "x")
    n = asks.MAX_OPEN_A_SESSION
    sessions = [f"{i:012x}" for i in range(1, 10)]
    for sid in sessions:
        if n >= asks.MAX_OPEN:
            break
        for _ in range(min(asks.MAX_OPEN_A_SESSION, asks.MAX_OPEN - n)):
            a.ask(bound(session=sid), "claude", "y")
            n += 1
    with pytest.raises(asks.AskError, match="as many asks"):
        a.ask(bound(session="fffffffffffe"), "codex", "z")
    clock.now += asks.ASK_TTL + 1
    assert a.waiting() == []
    a.ask(bound(), "codex", "again")


def test_a_session_that_is_gone_takes_its_asks_with_it():
    a = on()
    mine = a.ask(bound(), "codex", "x")
    theirs = a.ask(bound(session="ba9876543210"), "codex", "y")
    a.withdraw_from("0123456789ab")
    assert [w["id"] for w in a.waiting()] == [theirs["id"]] and a.take(mine["id"]) is None


# an ask the person's Start has taken: being started, neither waiting for the person nor gone, until
# that Start answers it or puts it back

def test_an_ask_a_start_holds_is_starting_until_it_is_answered_and_does_not_expire_meanwhile():
    # a Start can take minutes, its reads of claude among them: the asking session reads the ask as being started, not
    # as gone, and a day passing meanwhile does not take it, since only an ask waiting expires
    clock = Clock()
    a = on(clock=clock)
    ask = a.ask(bound(), "codex", "x")
    theirs = a.ask(bound(session="ba9876543210"), "codex", "y")
    assert a.states_of("0123456789ab") == {ask["id"]: "waiting"}
    taken = a.take(ask["id"], starting=True)
    assert taken == ask and a.take(ask["id"]) is None
    assert a.states_of("0123456789ab") == {ask["id"]: "starting"} and [w["id"] for w in a.waiting()] == [theirs["id"]]
    clock.now += asks.ASK_TTL + 1
    assert a.states_of("0123456789ab") == {ask["id"]: "starting"} and a.waiting() == []
    a.answered(ask["id"])                   # started: the Start's answer is written, and then the ask is let go
    assert a.states_of("0123456789ab") == {} and a.waiting() == []


def test_an_ask_past_its_day_is_gone_to_its_session_with_nothing_else_expiring_it():
    # the asking session's read of its asks (states_of, which Workspace.asks_of reads,
    # an ask in neither state and not answered being gone) expires those past ASK_TTL itself, so an ask not answered
    # within a day is gone to its session though nothing else has expired it, not the person's list, another ask or a
    # take (a control: a states_of that did not expire would read it as waiting, and `proofpane
    # ask --wait` would wait on)
    clock = Clock()
    a = on(clock=clock)
    ask = a.ask(bound(), "codex", "x")
    assert a.states_of("0123456789ab") == {ask["id"]: "waiting"}
    clock.now += asks.ASK_TTL + 1
    assert a.states_of("0123456789ab") == {}


def test_an_ask_a_failed_start_puts_back_waits_again_and_is_held_no_longer():
    # put back, it waits for the person as it was when taken, and nothing of the Start holds it: the person's refusal
    # then answers it, holding it as a Start does until the refusal is written, and it is then
    # neither waiting nor being started
    a = on()
    ask = a.ask(bound(), "claude", "x", effort="max", offered={"efforts": ["max"], "floor": False})
    taken = a.take(ask["id"], starting=True)
    a.put_back(dict(taken, effort="low"))   # what goes back is the ask as the agent asked it, not a caller's copy
    assert a.states_of("0123456789ab") == {ask["id"]: "waiting"} and a.waiting() == [ask]
    assert a.take(ask["id"], starting=True) == ask          # the person refuses it: held until the refusal is written
    assert a.states_of("0123456789ab") == {ask["id"]: "starting"} and a.waiting() == []
    a.answered(ask["id"])                   # the refusal is written, and then the ask is let go
    assert a.states_of("0123456789ab") == {} and a.waiting() == []
    a.put_back(ask)                         # nothing holds it: a put back of it does nothing
    assert a.waiting() == []


@pytest.mark.parametrize("how", ["withdrawn", "switched off and on"])
def test_an_ask_cleared_while_a_start_holds_it_is_not_put_back_when_that_start_fails(how):
    # its session gone (Workspace.remove withdraws its asks) or the switch turned off, while the person's Start held the
    # ask: a Start that fails afterwards does not make it wait again, from a session that is gone or holds no token;
    # another session's ask a Start holds is untouched by a withdrawal
    a = on()
    ask = a.ask(bound(), "codex", "x")
    theirs = a.ask(bound(session="ba9876543210"), "codex", "y")
    taken, held = a.take(ask["id"], starting=True), a.take(theirs["id"], starting=True)
    if how == "withdrawn":
        a.withdraw_from("0123456789ab")
        assert a.states_of("0123456789ab") == {} and a.states_of("ba9876543210") == {theirs["id"]: "starting"}
    else:
        a.set_enabled(False)
        a.set_enabled(True)
        assert a.states_of("0123456789ab") == {} and a.states_of("ba9876543210") == {}
    a.put_back(taken)
    a.put_back(held)
    assert [w["id"] for w in a.waiting()] == ([theirs["id"]] if how == "withdrawn" else [])


# an ask's effort: what the installed claude offers, as workspace.capabilities reads it (claude_cli)
OFFERED = {"efforts": ["low", "medium", "high", "xhigh", "max", "ultracode"], "floor": False}
# what an ask's refusal adds while that is not known: the workspace reads its claude again (asks.READ_AGAIN), written
# out so that a build without it fails the cases that say it, not the loading of this file
READ_AGAIN = "The workspace reads its Claude Code again at most once a minute, so you may ask for it again in a minute."


def test_an_ask_names_an_effort_for_claude_one_its_claude_offers_and_keeps_it_on_the_ask():
    a = on()
    ask = a.ask(bound(), "claude", "Review it", effort="max", offered=OFFERED)
    assert ask["effort"] == "max" and [w["effort"] for w in a.waiting()] == ["max"]
    assert a.ask(bound(), "claude", "Review it", effort="ultracode", offered=OFFERED)["effort"] == "ultracode"
    # none named, or "", is none, for any agent, and needs nothing of what claude offers
    assert a.ask(bound(session="ba9876543210"), "claude", "Review it")["effort"] is None
    assert a.ask(bound(session="ba9876543210"), "codex", "Review it", effort="")["effort"] is None
    # an ask taken out by a Start and put back keeps its own effort
    taken = a.take(ask["id"], starting=True)
    a.put_back(taken)
    assert next(w for w in a.waiting() if w["id"] == ask["id"])["effort"] == "max"


@pytest.mark.parametrize("program, effort, offered, said", [
    ("codex", "max", OFFERED, asks.CLAUDE_ONLY),
    ("gemini", "low", OFFERED, asks.CLAUDE_ONLY),
    ("codex", "max", None, asks.CLAUDE_ONLY),
    ("claude", "max", dict(OFFERED, floor=True), asks.UNKNOWN_EFFORTS + " " + READ_AGAIN),
    ("claude", "max", None, asks.UNKNOWN_EFFORTS + " " + READ_AGAIN),
    ("claude", "turbo", OFFERED, "An effort is one of low, medium, high, xhigh, max, ultracode."),
    ("claude", "MAX", OFFERED, "An effort is one of"),
    ("claude", "max ", OFFERED, "An effort is one of"),
    ("claude", ["max"], OFFERED, "An effort is one of"),
    ("claude", 3, OFFERED, "An effort is one of"),
    ("claude", 0, OFFERED, "An effort is one of"),         # given, though falsy: refused, never taken as none
    ("claude", [], OFFERED, "An effort is one of"),
    ("codex", False, OFFERED, asks.CLAUDE_ONLY),
])
def test_an_effort_that_is_not_one_claude_offers_or_for_another_agent_is_refused(program, effort, offered, said):
    a = on()
    with pytest.raises(asks.AskError) as refused:
        a.ask(bound(), program, "Review it", effort=effort, offered=offered)
    assert str(refused.value).startswith(said) and a.waiting() == []


# what an ask that names an effort is told while the workspace keeps nothing of its claude and a read of it is under way
# (asks.BEING_READ), written out so that a build without it fails this test, not the loading of the file
BEING_READ = ("The workspace is reading which efforts its Claude Code takes, so it cannot take an effort on an ask "
              "yet: ask for it again in a minute, or leave its effort to Claude Code's default.")


def test_an_effort_while_its_claude_is_being_read_is_refused_at_once_when_asked_and_never_at_start():
    # workspace.ask_offered gives asks.READING while the workspace keeps nothing of its claude and a read of it is under
    # way: the agent's ask is refused in BEING_READ, after who may ask and which agent, and before its task, as any
    # refusal of its effort is. The person's choice at Start
    # is checked against what the claude that starts offers, never READING, and its check never says those words: given
    # READING, or nothing known, it says UNKNOWN_EFFORTS alone
    a = on()
    with pytest.raises(asks.AskError) as refused:
        a.ask(bound(), "claude", "Review it", effort="max", offered=asks.READING)
    assert str(refused.value) == BEING_READ and a.waiting() == []
    for who, program, task, said in ((bound(may_ask=False), "claude", "Review it", "A session started from an ask"),
                                     (bound(), "turbo", "Review it", "Ask for one of the terminal agents"),
                                     (bound(), "codex", "Review it", asks.CLAUDE_ONLY),
                                     (bound(), "claude", "", BEING_READ)):
        with pytest.raises(asks.AskError) as refused:
            a.ask(who, program, task, effort="max", offered=asks.READING)
        assert str(refused.value).startswith(said), (program, task, str(refused.value))
    for offered in (asks.READING, None):
        with pytest.raises(asks.AskError) as refused:
            asks.check_effort("max", offered)
        assert str(refused.value) == asks.UNKNOWN_EFFORTS
