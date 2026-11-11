"""The checks of a business skill's SKILL.md and of a proposal for it, and of the counts and the objects the app is
sent."""

import hashlib

import pytest

from proofpane_workspace import shapes
from proofpane_workspace.shapes import ShapeError

SKILL = "---\nname: demo\ndescription: Says hello to the user by name\n---\nSay hello.\nThen stop.\n"


def skill(*lines, body="Say hello.\n"):
    return "---\n" + "".join(line + "\n" for line in lines) + "---\n" + body


def refused(text, words):
    with pytest.raises(ShapeError) as e:
        shapes.check_business_skill(text)
    assert words in str(e.value), str(e.value)


# ---- the frontmatter, in the subset every build reads alike --------------------------------------------------------

def test_a_skill_in_the_subset_is_read_as_its_keys_values_and_body():
    read = shapes.check_business_skill(skill("name: demo", "description: Says hello, then stops (once)",
                                             "user-invocable: false", "when_to_use: \"When asked to greet...\"",
                                             body="Body\n"))
    assert read["keys"] == ["name", "description", "user-invocable", "when_to_use"]
    assert read["values"] == {"name": "demo", "description": "Says hello, then stops (once)", "user-invocable": False,
                              "when_to_use": "When asked to greet..."}
    assert read["body"] == "Body\n"


@pytest.mark.parametrize("text, words", [
    ("\ufeff---\nname: demo\ndescription: Hi\n---\nx\n", "begins with exactly ---"),
    ("--- \nname: demo\ndescription: Hi\n---\nx\n", "begins with exactly ---"),
    ("---\r\nname: demo\ndescription: Hi\n---\nx\n", "carriage return"),
    ("---\nname: demo\ndescription: Hi\n---", "ends at a line that is exactly ---"),
    ("---\nname: demo\ndescription: Hi\n--- \nx\n", "ends at a line that is exactly ---"),
    ("---\nname: demo\ndescription: Hi\n--- \n---\nx\n", "holds ---"),
    ("---\nname: demo\ndescription: Hi\nx\n", "ends at a line that is exactly ---"),
    ("---\n---\nx\n", "holds its description"),
    ("---\nname: demo\n---\nx\n", "holds its description"),
])
def test_the_frontmatter_s_bounds_are_the_subset_s(text, words):
    refused(text, words)


@pytest.mark.parametrize("line, words", [
    ("description:Hi", "one colon, one space"),
    ("description:  Hi", "outside the subset"),
    ("description: Hi ", "outside the subset"),
    ("description: Hi  there", "outside the subset"),
    ("description: Hi.. there", "outside the subset"),
    ("description: Wait...", "outside the subset"),
    ("description: Wait... then", "outside the subset"),
    ("description: 5 ways", "outside the subset"),
    ("description: .inf", "outside the subset"),
    ("description: null", "outside the subset"),
    ("description: True", "outside the subset"),
    ("description: true", "outside the subset"),
    ("description: Hi # comment", "outside the subset"),
    ("description: Hi: there", "one colon, one space"),
    ("description: Hi & there", "outside the subset"),
    ("description: \"Hi # there\"", "outside the subset"),
    ("description: \"Hi: there\"", "one colon, one space"),
    ("description: \"Hi & there\"", "outside the subset"),
    ("description: \"Say \\\"hi\\\"\"", "outside the subset"),
    ("description: \"\"", "outside the subset"),
    ("description: 'Hi'", "outside the subset"),
    ("description: [hi]", "outside the subset"),
    ("description: |", "outside the subset"),
    ("description: Caf\u00e9", "outside printable ASCII"),
    ("description: Hi\tthere", "outside printable ASCII"),
    ("description: Hi---there", "holds ---"),
    (" description: Hi", "not a key, one colon"),
    ("# a comment", "not a key, one colon"),
    ("", "not a key, one colon"),
])
def test_a_value_outside_the_subset_is_refused(line, words):
    refused(skill("name: demo", line) if not line.startswith("description") else skill(line), words)


def test_wait_dots_are_taken_double_quoted():
    for value in ("Wait...", "Wait... then", "..."):
        read = shapes.check_business_skill(skill(f"description: \"{value}\""))
        assert read["values"]["description"] == value


def test_a_key_twice_is_refused():
    refused(skill("description: Hi", "description: Ho"), "a second time")


def test_booleans_are_for_the_two_boolean_keys_alone():
    assert shapes.check_business_skill(skill("description: Hi", "disable-model-invocation: true"))["values"][
        "disable-model-invocation"] is True
    assert shapes.check_business_skill(skill("description: Hi", "user-invocable: yes"))["values"][
        "user-invocable"] == "yes"
    read = shapes.check_business_skill(skill("description: Hi", "user-invocable: \"true\""))
    assert read["values"]["user-invocable"] == "true"
    refused(skill("description: Hi", "name: true"), "outside the subset")
    refused(skill("description: true"), "outside the subset")
    refused(skill("description: Hi", "when_to_use: false"), "outside the subset")


# ---- the keys ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["allowed-tools", "hooks", "shell", "disallowed-tools", "disallowedTools", "background",
                                 "fallback", "metadata", "created_by", "improved_by", "tools", "Name"])
def test_a_key_outside_the_allow_list_keeps_the_skill_from_proposal(key):
    refused(skill("description: Hi", f"{key}: Bash"), "not one a business skill may hold")


@pytest.mark.parametrize("line", ["context: fork", "agent: reviewer", "context: inline"])
def test_context_and_agent_keep_the_skill_from_proposal(line):
    refused(skill("description: Hi", line), "not one a business skill may hold")
    assert "context" not in shapes.BUSINESS_KEYS and "agent" not in shapes.BUSINESS_KEYS
    assert "context" not in shapes.BUSINESS_NOTE_KEYS and "agent" not in shapes.BUSINESS_NOTE_KEYS


def test_the_lists_hold_together():
    assert set(shapes.BUSINESS_NOTE_KEYS) <= set(shapes.BUSINESS_KEYS)
    assert set(shapes.BUSINESS_TEXT_KEYS) <= set(shapes.BUSINESS_KEYS)
    assert not set(shapes.BUSINESS_TEXT_KEYS) & set(shapes.BUSINESS_NOTE_KEYS)
    assert set(shapes.BUSINESS_BOOLEAN_KEYS) <= set(shapes.BUSINESS_NOTE_KEYS)
    assert shapes.BUSINESS_NOTE_KEYS == ("model", "effort", "disable-model-invocation", "user-invocable", "paths")
    assert shapes.NEWEST_READ == tuple(int(p) for p in shapes.BUILDS_READ[-1].split("."))
    for key in shapes.BUSINESS_KEYS:
        shapes.check_business_skill(skill("description: Hi", f"{key}: Value") if key != "description"
                                    else skill("description: Value"))


# ---- the constructs and the substitutions --------------------------------------------------------------------------

@pytest.mark.parametrize("body, words", [
    ("Run !`date` now.\n", "inline"),
    ("Run!`date`.\n", "inline"),
    ("```!\ndate\n```\n", "as a block"),
    ("Read @notes.md first.\n", "mention"),
    ("@notes.md\n", "mention"),
    ("Ask (@reviewer) first.\n", "mention"),
    ("Mail \u00e9@x then.\n", "mention"),
    ("Think: ULTRAthink hard.\n", "keyword"),
    ("Do it ultracode style.\n", "keyword"),
    ("Caf\u00e9ultrathink.\n", "keyword"),
])
def test_each_construct_anywhere_keeps_the_skill_from_proposal(body, words):
    refused(skill("description: Hi", body=body), words)


def test_a_command_block_after_another_fence_is_refused():
    refused(skill("description: Hi", body="```\ncode\n```\nThen:\n```!\ndate\n```\n"), "as a block")


def test_a_construct_in_the_frontmatter_is_refused_too():
    with pytest.raises(ShapeError):
        shapes.check_business_skill(skill("description: Use ultrathink"))
    assert shapes.business_constructs("description: \"Ask a@b\"") == []


def test_text_without_a_construct_passes():
    body = "Mail me at user@example.com, cost $5, a ! and a `code span`.\nAn ultr-a word; then stop.\n"
    assert shapes.business_constructs(body) == []
    shapes.check_business_skill(skill("description: Hi", body=body))


def test_a_brace_left_open_on_its_line_keeps_the_skill_from_proposal():
    refused(skill("description: Hi", body="Use ${CLAUDE_SKILL_DIR\n}\n"), "no } closes")
    refused(skill("description: Hi", body="Use ${A} and ${B\n"), "no } closes")
    shapes.check_business_skill(skill("description: Hi", body="Use ${CLAUDE_SKILL_DIR}/x and ${A}.\n"))


def test_the_dollar_lines_are_every_line_holding_a_dollar_in_order():
    text = "a $1\nb\nc ${X}\n$\n"
    assert shapes.business_dollar_lines(text) == ["a $1", "c ${X}", "$"]


def test_a_skill_larger_than_max_skill_or_not_utf8_text_is_refused():
    big = skill("description: Hi", body="x" * shapes.MAX_SKILL)
    refused(big, "at most")
    wide = skill("description: Hi", body="\u00e9" * (shapes.MAX_SKILL // 2))
    assert len(wide) < shapes.MAX_SKILL < len(wide.encode("utf-8"))
    refused(wide, "at most")
    refused("---\ndescription: Hi\n---\n\ud800\n", "UTF-8")
    refused(b"---\ndescription: Hi\n---\n", "UTF-8")


# ---- a proposal against the current version (check_business_proposal) ----------------------------------------------

def test_a_proposal_may_change_the_description_when_to_use_and_the_body():
    current = skill("name: demo", "description: Says hello", "when_to_use: When greeted", "model: sonnet",
                    body="Say hello.\nCost $5.\n")
    proposed = skill("name: demo", "description: Says hello by name", "when_to_use: \"When greeted...\"",
                     "model: sonnet", body="Say hello by name.\nCost $5.\n")
    read = shapes.check_business_proposal(current, proposed)
    assert read["values"]["description"] == "Says hello by name"


@pytest.mark.parametrize("proposed, words", [
    (skill("name: other", "description: Says hello by name", body="Say hello.\nCost $5.\n"), "not name"),
    (skill("name: demo", "description: Says hello by name", "model: opus", body="Say hello.\nCost $5.\n"),
     "keeps the frontmatter's keys"),
    (skill("name: demo", "description: Says hello by name", "when_to_use: Now", body="Say hello.\nCost $5.\n"),
     "keeps the frontmatter's keys"),
    (skill("description: Says hello by name", "name: demo", body="Say hello.\nCost $5.\n"),
     "keeps the frontmatter's keys"),
    (skill("description: Says hello by name", body="Say hello.\nCost $5.\n"), "keeps the frontmatter's keys"),
    (skill("name: demo", "description: Says hello by name", body="Say hello.\nCost $6.\n"), "holds a $"),
    (skill("name: demo", "description: Says hello by name", body="Cost $5.\nSay hello.\nCost $5.\n"), "holds a $"),
    (skill("name: demo", "description: Says hello by name", body="Say hello.\n"), "holds a $"),
    (skill("name: demo", "description: Says hello", body="Say hello.\nCost $5.\n"), "changes the SKILL.md"),
    (skill("name: demo", "description: Says hello --- by name", body="Say hello.\nCost $5.\n"), "holds ---"),
    (skill("name: demo", "description: Says hello: by name", body="Say hello.\nCost $5.\n"), "one colon"),
    (skill("name: demo", "description: Says hello", body="Say !`date`.\nCost $5.\n"), "inline"),
])
def test_a_proposal_is_refused_where_it_changes_more_than_is_allowed(proposed, words):
    current = skill("name: demo", "description: Says hello", body="Say hello.\nCost $5.\n")
    with pytest.raises(ShapeError) as e:
        shapes.check_business_proposal(current, proposed)
    assert words in str(e.value), str(e.value)


def test_a_line_of_another_key_stays_byte_for_byte_even_where_its_value_reads_the_same():
    current = skill("name: demo", "description: Says hello")
    with pytest.raises(ShapeError) as e:
        shapes.check_business_proposal(current, skill("name: \"demo\"", "description: Says hello by name"))
    assert "not name" in str(e.value)


def test_lines_holding_a_dollar_may_not_change_places():
    current = skill("description: Hi", body="Pay $1 first.\nThen $2.\n")
    with pytest.raises(ShapeError) as e:
        shapes.check_business_proposal(current, skill("description: Hi there", body="Then $2.\nPay $1 first.\n"))
    assert "holds a $" in str(e.value)


def test_a_carriage_return_in_the_body_is_refused():
    refused(skill("description: Hi", body="Say hello.\r\nThen stop.\n"), "carriage return")


def test_a_named_substitution_with_a_dash_cannot_be_renamed():
    current = skill("description: Hi", body="Use $file-path here.\n")
    with pytest.raises(ShapeError):
        shapes.check_business_proposal(current, skill("description: Hi", body="Use $file-name here.\n"))


def test_a_current_version_outside_the_subset_gets_no_proposal():
    with pytest.raises(ShapeError):
        shapes.check_business_proposal(skill("description: Hi", "hooks: x"), skill("description: Ho", "hooks: x"))


# ---- the review's hypothesis ---------------------------------------------------------------------------------------

def test_a_hypothesis_is_one_line_with_no_construct():
    assert shapes.check_skill_hypothesis("  The description names when to greet.  ") == \
        "The description names when to greet."
    for bad in ("", "two\nlines", "x" * 301, "Use @notes", "Think ultra hard", "Run !`id`"):
        with pytest.raises(ShapeError):
            shapes.check_skill_hypothesis(bad)


# ---- the counts as the app sends them ------------------------------------------------------------------------------

def counts(**over):
    one = {field: 0 for field in shapes.SKILL_COUNTS_FIELDS}
    one.update(over)
    return one


def test_the_counts_are_the_fields_for_the_whole_and_each_version():
    given = {"whole": counts(calls=12, failed=3), "current": counts(calls=7), "before": None}
    assert shapes.check_skill_counts(given) == given


@pytest.mark.parametrize("given", [
    None, [], {"whole": counts()}, {"whole": counts(), "current": None, "before": None, "extra": None},
    {"whole": None, "current": None, "before": None},
    {"whole": counts(calls=-1), "current": None, "before": None},
    {"whole": counts(calls=True), "current": None, "before": None},
    {"whole": counts(calls=1.0), "current": None, "before": None},
    {"whole": counts(calls="1"), "current": None, "before": None},
    {"whole": dict(counts(), extra=1), "current": None, "before": None},
    {"whole": {k: v for k, v in counts().items() if k != "typed"}, "current": None, "before": None},
])
def test_counts_of_another_shape_are_refused(given):
    with pytest.raises(ShapeError):
        shapes.check_skill_counts(given)


def test_the_task_fields_are_skill_counts_ended():
    from proofpane_open.ade import skill_counts
    assert [f[len("tasks_"):] for f in shapes.SKILL_COUNTS_FIELDS if f.startswith("tasks_")][:-1] == \
        list(skill_counts.ENDED)


# ---- an object's id as git computes it -----------------------------------------------------------------------------

def test_object_ids_are_git_s_in_both_formats():
    data = b"hello\n"
    assert shapes.object_id("blob", data, "sha1") == "ce013625030ba8dba906f756967f9e9ca394464a"
    assert shapes.object_id("blob", data, "sha256") == hashlib.sha256(b"blob 6\0hello\n").hexdigest()
    for kind, data, fmt in (("tag", b"", "sha1"), ("blob", "text", "sha1"), ("blob", b"", "md5")):
        with pytest.raises(ShapeError):
            shapes.object_id(kind, data, fmt)
