"""How the Docker case of an ask's effort reads what claude says (tests/effort_words.py), checked here on made-up words,
where the case cannot run: every level's name the case knows, the unknown level's among them, is written as one marker;
the warning is the lines claude's start at a level it does not know says that name that level itself, as a word (not
those naming any level), but those its start without --effort says and those that every level's own first start says;
and a level fails when a start at it says a line of that warning that names the level itself, as a word. Each world
below is what claude might say, the case's verdict on it the one it must give."""

import effort_words as ew

LEVELS = ("low", "medium", "high", "xhigh", "max", "ultracode")
STARTS = ("first", "resume", "continue")
RESULT = '{"type": "result", "session_id": "0f0e0d0c-0b0a-4908-8706-050403020100", "result": "done"}'
UNKNOWN_SAID = "Unknown effort {}: the default is used."


def said(*lines):
    """What a start says on its two streams: its JSON result, then `lines`."""
    return "\n".join((RESULT,) + lines)


def judged(at, unknown, plain=()):
    """What the Docker case asserts, on a world where every start at `level` (its first, with --resume and with
    --continue) says the lines `at(level)` gives beside its result, the start at the unknown level says the lines
    `unknown`, and the start without --effort says the lines `plain`, none unless given: (the warning, the starts that
    said it of their own level), the case failing when the first is empty or the second is not."""
    runs = {level: {start: said(*at(level)) for start in STARTS} for level in LEVELS}
    warned = ew.warning(said(*unknown), said(*plain), {level: runs[level]["first"] for level in LEVELS})
    return warned, ew.failures(warned, runs)


def test_a_level_warned_of_as_the_unknown_one_fails_though_the_warning_names_a_variable_in_capitals():
    # world A: max is in truth ignored, and claude warns of it in the words it warns of notalevel, words naming a
    # variable whose name ends in _LEVEL. A marker that was LEVEL itself, put back as the level, would change that
    # name too: the warning would never be found at max, and the case would pass
    line = "Warning: effort {} is not one Claude Code knows: set CLAUDE_CODE_EFFORT_LEVEL to one it does."
    warned, failed = judged(lambda level: [line.format(level)] if level == "max" else [], [line.format(ew.UNKNOWN)])
    assert warned == {line.format(ew.MARK)}
    assert failed == [("max", start, [line.format("max")]) for start in STARTS]


def test_a_level_s_name_echoed_at_every_start_is_not_the_warning():
    # world B: claude takes every level and echoes the one it was given at every start, notalevel's too, where it also
    # warns: the echo, which every level's first start says of its own name, is not the warning, and no level fails,
    # where an earlier reading failed at every level it takes
    warned, failed = judged(lambda level: [f"Effort: {level}"],
                            [f"Effort: {ew.UNKNOWN}", UNKNOWN_SAID.format(ew.UNKNOWN)])
    assert warned == {UNKNOWN_SAID.format(ew.MARK)}
    assert failed == []


def test_a_start_at_the_unknown_level_that_names_it_nowhere_gives_no_warning():
    # claude says something new at notalevel, but nothing that names the level: a variable's name in capitals, which a
    # marker of LEVEL would take for the level, and its folder's name. The warning is empty, which the case fails on,
    # rather than passing on words that cannot tell a level it takes from one it ignores
    warned, failed = judged(lambda level: [], ["Note: CLAUDE_CODE_EFFORT_LEVEL is not set.",
                                               "Working in /home/ws/measure/unknown"])
    assert warned == set() and failed == []


def test_a_warning_said_at_the_unknown_level_alone_fails_no_level():
    # the plain world: claude warns at notalevel alone, and says nothing more at any level it takes (a control: an
    # earlier reading gave the same)
    warned, failed = judged(lambda level: [], [UNKNOWN_SAID.format(ew.UNKNOWN)])
    assert warned == {UNKNOWN_SAID.format(ew.MARK)} and failed == []


def test_a_line_naming_the_unknown_level_that_the_start_without_effort_says_too_is_not_the_warning():
    # claude names notalevel at every start, the one without --effort among them, where it was not given, so the line
    # does not answer the effort given (a setting an earlier start left behind could make it say so). It is not the
    # warning, which is then empty, and the case fails on that rather than passing on a line every start says (a
    # control: an earlier reading also left out what the start without --effort says; a reading that did not would pass
    # this world having measured nothing)
    seen = f"Profile: {ew.UNKNOWN}"
    warned, failed = judged(lambda level: [seen], [seen], plain=[seen])
    assert warned == set() and failed == []


def test_claude_s_own_words_cannot_hold_the_marker():
    # NUL characters claude prints are taken out of its words before a level's name is written as the marker, so a line
    # of claude's own that held the marker's characters names no level, and the warning is empty
    warned, failed = judged(lambda level: [], [ew.MARK + " is not set.", "\x00plain\x00 words"])
    assert warned == set() and failed == []
    assert ew.words(said("a\x00b " + ew.MARK)) == {"ab LEVEL"}


def test_an_echo_that_names_the_level_and_its_default_is_not_the_warning():
    # world W1: claude takes every level and, at every start, the unknown level's
    # included, echoes the level it was given and its default, and warns at the unknown level alone. Every level's name
    # is written alike, so the echo reads the same at every start and is not the warning, and no level fails (a reading
    # that wrote the start's own level alone read the echo differently at high, whose default is its own name, and
    # failed every level)
    echo = "Effort: {} (default: high)"
    warned, failed = judged(lambda level: [echo.format(level)],
                            [echo.format(ew.UNKNOWN), UNKNOWN_SAID.format(ew.UNKNOWN)])
    assert warned == {UNKNOWN_SAID.format(ew.MARK)} and failed == []


def test_an_echo_of_another_level_than_the_one_given_is_not_the_warning():
    # world W4: claude echoes ultracode as the level it maps it to, xhigh, and every
    # other level, the unknown one's included, as its own name, and warns at the unknown level alone: no level fails
    # (a reading that wrote the start's own level alone failed low, medium, high, xhigh and max)
    warned, failed = judged(lambda level: ["Effort: " + ("xhigh" if level == "ultracode" else level)],
                            ["Effort: " + ew.UNKNOWN, UNKNOWN_SAID.format(ew.UNKNOWN)])
    assert warned == {UNKNOWN_SAID.format(ew.MARK)} and failed == []


# a warning that lists the levels claude knows
LISTED = "Unknown effort {}; valid efforts: low, medium, high, xhigh, max."


def test_a_level_warned_of_in_words_that_list_the_levels_fails():
    # world W2: claude warns of max in the words it warns of the unknown level, words that list its levels; max fails,
    # at each of its starts. A reading that compares the warning, its levels' names written as the marker, with a start's
    # words in which the start's own level alone is passes here, having measured
    # nothing (a control: a reading that writes the start's own level alone fails max too)
    warned, failed = judged(lambda level: [LISTED.format(level)] if level == "max" else [], [LISTED.format(ew.UNKNOWN)])
    assert len(warned) == 1
    assert failed == [("max", start, [LISTED.format("max")]) for start in STARTS]


def test_a_warning_that_lists_the_levels_said_at_the_unknown_level_alone_fails_no_level():
    # world W3: the same warning at the unknown level alone, every level taken (a control: a reading that writes the
    # start's own level alone gave the same)
    warned, failed = judged(lambda level: [], [LISTED.format(ew.UNKNOWN)])
    assert len(warned) == 1 and failed == []


def test_a_line_of_the_warning_that_names_another_level_is_not_said_of_this_one():
    # what the case asks is whether claude says of a level what it says of one it does not know: a start's line in the
    # warning's words that names another level, and not the start's own, is not that, and fails no level (the reading
    # writes every level's name alike before lines are compared, and asks a line of the warning to name the
    # start's own level; a control: a reading that put the level's own name back where the warning named the
    # unknown one gave the same)
    warned, failed = judged(lambda level: [UNKNOWN_SAID.format(ew.UNKNOWN)] if level == "high" else [],
                            [UNKNOWN_SAID.format(ew.UNKNOWN)])
    assert warned == {UNKNOWN_SAID.format(ew.MARK)} and failed == []


def test_a_level_s_name_inside_another_word_names_no_level():
    # a level's name is written as the marker only where it stands as a word: words at the unknown level that name no
    # level but hold a level's name inside another word (highest, allowed, maximum) are not the warning, which is then
    # empty, and the case fails on that rather than pass on a warning no start of a level could say (a control: a
    # reading that wrote the unknown level alone there gave the same)
    warned, failed = judged(lambda level: [], ["Using the highest effort allowed, at most the maximum."])
    assert warned == set() and failed == []


def test_words_writes_a_session_s_id_its_numbers_and_the_level_alike_and_skips_the_result():
    # what a start says, read a line at a time beside its JSON result: two starts that differ in a session's id, a
    # number or the level given alone say the same (a control: an earlier reading did the same)
    text = said("", "  Resumed 0f0e0d0c-0b0a-4908-8706-050403020100 at max after 12 turns  ", "{not read")
    assert ew.words(text, "max") == {f"Resumed ID at {ew.MARK} after 0 turns"}
    assert ew.words(text) == {"Resumed ID at max after 0 turns"}


# the warning names the unknown level itself, as a word: a reading that took as the warning any line of the unknown
# level's start that named any level passed the case on a claude that ignores levels it does not know, and could fail
# a level it takes

def test_a_level_ignored_and_warned_of_in_words_that_echo_no_level_given_gives_no_warning():
    # world U: claude does not take ultracode, and warns of it, as of the unknown level, in words that list the levels
    # it knows and echo no level given. The line does not name the unknown level, so it is not the warning, which is
    # empty, and the case fails loudly. A reading that took any line naming a level for the warning took it, and passed:
    # ultracode, which the line does not name, was never caught
    line = "Unknown effort level; valid levels: low, medium, high, xhigh, max."
    warned, failed = judged(lambda level: [line] if level == "ultracode" else [], [line])
    assert warned == set() and failed == []


# a notice claude says from some start on, and so at the unknown level's start, the case's last
NOTICE = "Tip: run with --effort max for hard tasks"


def test_a_notice_naming_another_level_at_the_unknown_level_s_start_is_not_the_warning():
    # world R2: claude says nothing new at the unknown level but that notice, which names a level and not the unknown
    # one: the warning is empty, and the case fails loudly. A reading that took any line naming a level for the warning
    # took the notice for it and passed, on a claude that ignores a level it does not know
    warned, failed = judged(lambda level: [], [NOTICE])
    assert warned == set() and failed == []


def test_a_notice_said_at_a_level_s_starts_and_at_the_unknown_level_s_fails_no_level():
    # world R1: claude takes every level and warns at the unknown level in words naming it, and says the notice at the
    # unknown level's start and at max's and ultracode's starts too. The notice is not the warning, and no level fails.
    # A reading that took any line naming a level for the warning took the notice for a line of it and failed max,
    # which the notice names, at each of its starts
    warned, failed = judged(lambda level: [NOTICE] if level in ("max", "ultracode") else [],
                            [NOTICE, UNKNOWN_SAID.format(ew.UNKNOWN)])
    assert warned == {UNKNOWN_SAID.format(ew.MARK)} and failed == []


def test_an_echo_left_out_at_the_default_level_is_taken_for_the_warning_and_the_levels_that_echo_it_fail():
    # world D: claude echoes the level it is given at every level but its default, high, and the raw value at the
    # unknown level, beside a warning naming it. The echo is not said at every level's first start, so it is a line of
    # the warning, and low, medium, xhigh, max and ultracode fail at each of their starts. A limit of the reading: a
    # line said in the same words at some levels' starts and at the unknown level's cannot be told from the warning said
    # of those levels, so the case fails loudly, what each start said printed in its record, rather than pass on what it
    # cannot tell (the earlier readings gave the same verdict)
    echo = "Effort level set to {}"
    warned, failed = judged(lambda level: [] if level == "high" else [echo.format(level)],
                            [echo.format(ew.UNKNOWN), UNKNOWN_SAID.format(ew.UNKNOWN)])
    assert warned == {echo.format(ew.MARK), UNKNOWN_SAID.format(ew.MARK)}
    assert failed == [(level, start, [echo.format(level)]) for level in LEVELS if level != "high" for start in STARTS]


def test_a_line_of_the_warning_naming_another_level_inside_a_word_is_not_said_of_this_one():
    # a start fails on a line of the warning only where the line names the start's
    # own level as a word. High's starts say a line of the warning that names xhigh, and low's one that holds low's
    # name inside another word, allowed: no level fails. A check of the start's own level that does not ask for its name
    # to stand as a word fails high and low here
    line = "Effort {} is not allowed: the default is used."
    said_at = {"high": [line.format("xhigh")], "low": [line.format("max")]}
    warned, failed = judged(lambda level: said_at.get(level, []), [line.format(ew.UNKNOWN)])
    assert warned == {line.format(ew.MARK)} and failed == []


def test_the_unknown_level_s_name_inside_another_word_at_its_start_is_not_the_warning():
    # the unknown level's name only inside another word at its start, a file named for the value given, does not name
    # it: the line is not the warning, which is empty, and the case fails loudly. A reading that took a line holding the
    # unknown level's name anywhere would pass here, on a line no start of a level could say of that level
    warned, failed = judged(lambda level: [], [f"Wrote /home/ws/.claude/effort_{ew.UNKNOWN}.json"])
    assert warned == set() and failed == []
