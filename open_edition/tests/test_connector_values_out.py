"""What leaves through the connector of a value that is no text (ade/connector.py: outward, outward_cut). A keyed
session's checkpoint gives list_sessions its `doing` and `waiting_on`, and either may be a list or an object; such a
value is written as Python writes it, and there a line break, a tab or a carriage return before a secret's shape is a
backslash and a letter, so that the shape begins no word. Each of its texts and names is therefore read for a shape as
the text it is, at any depth, before the value is written. The filter's cases for a text are in
test_connector_tools.py."""

import json
import time

import pytest

from conftest import signed_up
from connector_fixtures import connected
from proofpane_open import oauth, store
from proofpane_open.ade import connector, link
from test_ade_link import paired
from test_connector_link import SHAPED

SECRETS = {"a GitHub token": "ghp_" + "0123456789abcdef", "a connector token": "ppca_" + "0123456789abcdef"}
LEADS = {"a line break": "\n", "a tab": "\t", "a carriage return": "\r", "two lines": "\n\n", "a space": " ",
         "nothing": ""}
WHERE = {
    "a list": lambda words: [words],
    "an object": lambda words: {"step": words},
    "an object's name": lambda words: {words: 1},
    "a list in an object": lambda words: {"steps": ["first", [words]]},
    "five deep": lambda words: [{"a": [{"b": [words]}]}],
    "a text": lambda words: words,
}


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    oauth._failed.clear()
    connector._ws_cache.clear()


def kept(mac, document):
    """A checkpoint in the store as a PUT kept it before each of its texts was read as a text (the route refuses such
    a one now: tests/test_ade_link.py)."""
    with store.connect() as db:
        db.execute("UPDATE ade_links SET checkpoint = ?, checkpoint_version = 1, checkpoint_at = ? WHERE id = ?",
                   (json.dumps(document, ensure_ascii=False, separators=(",", ":")), int(time.time()), mac.link_id))


def listed(client, owner):
    app = oauth.authenticate("Bearer " + connected(client, owner)["access_token"])
    got = connector.call(app, {"name": "list_sessions", "arguments": {}}, int(time.time()))
    assert got["isError"] is False
    return got["structuredContent"]


@pytest.mark.parametrize("secret", sorted(SECRETS))
@pytest.mark.parametrize("lead", sorted(LEADS))
@pytest.mark.parametrize("where", sorted(WHERE))
def test_no_part_of_a_kept_checkpoint_that_is_shaped_like_a_secret_is_given_to_an_app(client, secret, lead, where):
    owner = signed_up(client)
    mac = paired(client, owner)
    value = WHERE[where]("deploy with the token:" + LEADS[lead] + SECRETS[secret])
    kept(mac, {"doing": value, "waiting_on": value})
    answer = listed(client, owner)
    assert SECRETS[secret] not in json.dumps(answer, ensure_ascii=False)
    [given] = answer["links"]
    for field in ("doing", "waiting_on"):
        assert "[left out: " in given[field], given[field]


def test_a_kept_checkpoint_s_list_or_object_with_no_such_part_is_given_as_it_was(client):
    owner = signed_up(client)
    mac = paired(client, owner)
    doing, waiting = ["first", {"then": ["second\nline", 3, None, True]}], {"on": "a review", "n": 2.5}
    kept(mac, {"doing": doing, "waiting_on": waiting})
    [given] = listed(client, owner)["links"]
    assert given["doing"] == "['first', {'then': ['second\\nline', 3, None, True]}]"
    assert given["waiting_on"] == "{'on': 'a review', 'n': 2.5}"


@pytest.mark.parametrize("lead", sorted(LEADS))
def test_a_value_that_is_no_text_leaves_cut_with_its_texts_read_too(lead):
    """No tool gives a cut value that is no text today; the filter's cut way out reads one as its whole way does."""
    secret = SECRETS["a GitHub token"]
    for value in (["token:" + LEADS[lead] + secret], {"k": {"token:" + LEADS[lead] + secret: 1}}):
        shown, cut = connector.outward_cut(value, 400)
        assert secret not in shown and "[left out: " in shown and cut is False
        text, sealed, cut = connector.readable(value, 400)
        assert secret not in text and sealed is False and cut is False
        short, cut = connector.outward_cut(value, 12)
        assert secret not in short and len(short) <= 12 and cut is True


KEY = "a private key (-----BEGIN ... PRIVATE KEY)"


def containers(words, alone):
    """A text in a list, an object, an object's name and five deep; and the same with `alone` in its place."""
    return (([words, "next"], [alone, "next"]), ({"k": words, "n": 1}, {"k": alone, "n": 1}),
            ({words: 1}, {alone: 1}), ([{"a": [{"b": [words]}]}], [{"a": [{"b": [alone]}]}]))


@pytest.mark.parametrize("shape", sorted(set(SHAPED) - {KEY}))
@pytest.mark.parametrize("lead", sorted(LEADS))
def test_each_text_of_a_value_that_is_no_text_is_read_once_as_the_text_it_is(shape, lead):
    """Every shape that begins a word (test_connector_link.py's table but the private key), in a list, an object, an
    object's name and five deep: what leaves is the value written with each text as the filter gives that text alone.
    So it holds no part of the shape, the value is whole around it, and the shape is marked once, never a mark inside
    a mark (a mark names its shape, and nine of the shapes are a prefix that their own name holds)."""
    words = "note:" + LEADS[lead] + SHAPED[shape] + " and more"
    alone = connector._left_out(words)
    assert SHAPED[shape] not in alone and alone.count("[left out: ") == 1 and alone.endswith(" and more")
    for value, written in containers(words, alone):
        assert connector.outward(value) == connector._escaped(str(written))[0]


def as_it_was(value):
    """What left of a value that is no text before its texts were read each as a text: Python's writing of it, read
    once whole."""
    return connector._escaped(connector._left_out(str(value)))[0]


@pytest.mark.parametrize("lead", sorted(LEADS))
def test_a_private_key_in_a_value_that_is_no_text_is_left_out_as_it_was(lead):
    """A private key's first line begins no word, so no line break hides it, and what follows it is left out to its
    END line, or to the end where there is none: of the whole value as Python writes it, which is more than of its
    text. That is as it was."""
    words = "note:" + LEADS[lead] + SHAPED[KEY] + " and more"
    for value, _ in containers(words, None):
        assert SHAPED[KEY] not in as_it_was(value) and "and more" not in as_it_was(value)
        assert connector.outward(value) == as_it_was(value)


def test_a_shape_that_only_the_whole_value_shows_is_left_out_as_it_was():
    """A private key's first line is `-----BEGIN` and, later on that line, `PRIVATE KEY`. Written as Python writes it
    a list is one line, so two of its texts can show together what neither shows alone, as the JSON of a checkpoint
    can, which is read whole for that when it is put. And a key kept as a list of its lines has its body in texts
    of their own; on that one line its first line reads on to the last `PRIVATE KEY`, its END line's, so all that
    follows is left out. What was left out of such a value is left out still."""
    two = ["-----BEGIN RSA", "PRIVATE KEY-----", "MIIEvQIBADANBgkq"]
    assert all(connector._left_out(text) == text for text in two)                # no text of it is shaped like one
    assert "MIIEvQIBADANBgkq" not in as_it_was(two) and "[left out: a private key" in as_it_was(two)
    assert connector.outward(two) == as_it_was(two)
    lines = ["a key:", "-----BEGIN RSA PRIVATE KEY-----", "MIIEvQIBADANBgkq", "hkiG9w0BAQEFAASC",
             "-----END RSA PRIVATE KEY-----", "kept"]
    assert "MIIEvQIBADANBgkq" not in as_it_was(lines) and "hkiG9w0BAQEFAASC" not in as_it_was(lines)
    assert as_it_was(lines) == "['a key:', '[left out: a private key (-----BEGIN ... PRIVATE KEY)]"
    assert connector.outward(lines) == as_it_was(lines)


def test_a_token_after_a_line_break_and_a_private_key_s_lines_in_one_value_are_both_left_out():
    token = SHAPED["a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"]
    value = ["token:\n" + token + " then", "-----BEGIN RSA PRIVATE KEY-----", "MIIEvQIBADANBgkq",
             "-----END RSA PRIVATE KEY-----", "kept"]
    out = connector.outward(value)
    assert token not in out and "MIIEvQIBADANBgkq" not in out
    assert out == ("['token:\\n[left out: a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)] then', "
                   "'[left out: a private key (-----BEGIN ... PRIVATE KEY)]")


# ---- a private key's first line that another shape's mark would take ------------------------------------------------

BODY, END = "MIIEvQIBADANBgkq", "-----END RSA PRIVATE KEY-----"
WORD_SHAPES = sorted(set(SHAPED) - {KEY})


def all_from_the_first_line_left_out(value, shape):
    """What leaves of `value` holds neither the key's body, nor what follows it, nor the other shape."""
    out = connector.outward(value)
    assert BODY not in out and "and more" not in out and SHAPED[shape] not in out, out
    assert "[left out: " in out
    assert connector.outward_cut(value, 4000) == (out, False)
    return out


def test_the_one_shape_that_begins_no_word_is_a_private_key_s_first_line():
    """The first reading of a value that is no text looks for every shape that begins a word, and stops where a
    private key's first line begins, which it knows by how that line begins. A shape added to either table that
    begins no word fails here until that reading is taught it."""
    every = tuple(link.SECRET_SHAPES) + connector.CONNECTOR_SHAPES
    assert len(every) == len(SHAPED) == len(WORD_SHAPES) + 1
    [(shape, pattern)] = [(shape, pattern) for shape, pattern in every if not pattern.pattern.startswith(r"\b")]
    assert shape == KEY and pattern.pattern.startswith(connector._KEY_BEGINS)
    assert SHAPED[KEY].startswith(connector._KEY_BEGINS)


@pytest.mark.parametrize("shape", WORD_SHAPES)
def test_a_private_key_s_first_line_in_the_word_of_another_shape_is_still_found(shape):
    """A shape that begins a word is left out to the end of its word, and a private key's first line can stand in
    that word: "id=AKIA...,key=-----BEGIN RSA PRIVATE KEY-----" and the key's lines after it. Read as a text, the two
    are left out together, to the key's END line. In a list or an object the whole value is still read for the key,
    and all from its first line on is left out, as it was before each text was read as a text."""
    words = SHAPED[shape] + ",key=" + SHAPED[KEY] + "-----\n" + BODY + "\n" + END + "\nand more"
    assert BODY not in connector._left_out(words) and SHAPED[shape] not in connector._left_out(words)
    for value, _ in containers(words, None):
        assert BODY not in as_it_was(value) and "and more" not in as_it_was(value)
        all_from_the_first_line_left_out(value, shape)


@pytest.mark.parametrize("shape", WORD_SHAPES)
def test_the_words_that_end_a_private_key_s_first_line_in_the_word_of_another_shape_are_still_found(shape):
    """The same where the other shape's word holds the words PRIVATE KEY, with no END line after."""
    words = "-----BEGIN " + SHAPED[shape] + "-PRIVATE KEY-----\n" + BODY + " and more"
    for value, _ in containers(words, None):
        assert BODY not in as_it_was(value) and "and more" not in as_it_was(value)
        all_from_the_first_line_left_out(value, shape)


@pytest.mark.parametrize("shape", WORD_SHAPES)
def test_a_key_kept_as_its_lines_after_such_a_first_line_is_left_out(shape):
    """A key kept as a list of its lines, or as an object's values, its first line in the word of another shape: the
    body is in texts of their own, and a name can hold the first line too."""
    first = SHAPED[shape] + ",key=" + SHAPED[KEY] + "-----"
    for value in ([first, BODY, END, "and more"], {"a": first, "b": BODY, "c": END, "d": "and more"},
                  {first: BODY, "end": END, "then": "and more"}, [{"a": [first]}, [BODY, END], {"z": "and more"}]):
        assert BODY not in as_it_was(value) and "and more" not in as_it_was(value)
        all_from_the_first_line_left_out(value, shape)


@pytest.mark.parametrize("shape", WORD_SHAPES)
def test_no_text_or_name_after_a_private_key_s_first_line_is_marked_before_the_whole_is_read(shape):
    """A first line in one text and the words that end it in a later text, or a later name, in the word of another
    shape: the whole value shows the key, and the later text is not marked first, in the order the value is
    written, a name before its value."""
    later = SHAPED[shape] + "-PRIVATE KEY-----"
    for value in (["-----BEGIN RSA", later, BODY, "and more"], [["-----BEGIN RSA"], {"k": [later]}, BODY, "and more"],
                  {"first": "-----BEGIN RSA", later: BODY, "last": "and more"},
                  {"a": {"first": ["-----BEGIN RSA"]}, "b": {later: [BODY]}, "c": "and more"}):
        assert BODY not in as_it_was(value) and "and more" not in as_it_was(value)
        all_from_the_first_line_left_out(value, shape)


@pytest.mark.parametrize("shape", WORD_SHAPES)
def test_the_first_reading_stops_at_the_first_of_two_such_beginnings(shape):
    """A text can hold the words that begin the line twice. The whole reading finds the key from the first of them,
    so the first reading stops at the first: read up to a later one, a shape between the two would be marked, and
    could take the words that end the first line."""
    words = "-----BEGIN " + SHAPED[shape] + "-PRIVATE KEY-----\n" + BODY + " -----BEGIN and more"
    for value, _ in containers(words, None):
        assert BODY not in as_it_was(value) and "and more" not in as_it_was(value)
        all_from_the_first_line_left_out(value, shape)


def test_a_shape_that_runs_past_a_private_key_s_first_line_is_left_out_whole():
    """A shape that begins before the first line and is told only with what stands in that line: a connector token is
    eight characters after its prefix, and here three of them stand before `-----BEGIN`, after a line break. The text
    read alone leaves it out from where it begins. So does the first reading, which reads only what stands before the
    line and looks for each shape in the whole of the text; the whole reading does not find it, since the line break
    before it is written as a backslash and a letter."""
    begun = "ppca_abc"
    for lead in ("\n", "\t", "\r"):
        words = "note" + lead + begun + SHAPED[KEY] + "-----\n" + BODY + " and more"
        assert connector._left_out("note" + lead + begun) == "note" + lead + begun      # no token yet, read by itself
        assert begun not in connector._left_out(words) and BODY not in connector._left_out(words)
        for value, _ in containers(words, None):
            assert begun in as_it_was(value) and BODY not in as_it_was(value)
            out = connector.outward(value)
            assert begun not in out and BODY not in out and "and more" not in out, out
            assert connector.outward_cut(value, 4000) == (out, False)


def test_what_stands_before_a_private_key_s_first_line_is_read_as_texts_and_marked_beside_it():
    token, key_id = SHAPED["a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"], SHAPED["an AWS key (AKIA)"]
    value = ["note:\n" + token + " then", "id=" + key_id + ",key=" + SHAPED[KEY] + "-----\n" + BODY, "and more"]
    assert connector.outward(value) == (
        "['note:\\n[left out: a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)] then', 'id=[left out: an AWS key "
        "(AKIA)][left out: a private key (-----BEGIN ... PRIVATE KEY)]")
    # in the text that holds the first line, a shape after a line break before it is read as of a text
    value = {"k": "token:\n" + token + " then\n" + SHAPED[KEY] + "-----\n" + BODY, "z": "and more"}
    assert connector.outward(value) == (
        "{'k': 'token:\\n[left out: a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)] then\\n[left out: a private key "
        "(-----BEGIN ... PRIVATE KEY)]")


@pytest.mark.parametrize("shape", WORD_SHAPES)
@pytest.mark.parametrize("lead", sorted(LEADS))
def test_a_value_that_only_looks_like_it_holds_a_private_key_s_first_line_has_every_text_read(shape, lead):
    """`-----BEGIN` with no PRIVATE KEY after it, and PRIVATE KEY only before it, are no private key's first line:
    nothing is left to the whole reading, and each text after is read as the text it is."""
    words = "note:" + LEADS[lead] + SHAPED[shape] + " and more"
    alone = connector._left_out(words)
    assert SHAPED[shape] not in alone and alone.endswith(" and more")
    for before in (["-----BEGIN the list"], ["PRIVATE KEY comes first", "then -----BEGIN"]):
        for value, written in ((before + [words], before + [alone]), ({"a": before, words: [words]},
                                                                      {"a": before, alone: [alone]})):
            assert connector.outward(value) == connector._escaped(str(written))[0]


# ---- the value is written part by part, each part where it was written --------------------------------------------

class Odd:
    """A part that is none of what a checkpoint's JSON gives, written with the words it was made with."""

    def __init__(self, written):
        self.written = written

    def __repr__(self):
        return self.written


def nested(depth):
    value = "x"
    for _ in range(depth):
        value = [value]
    return value


PLAIN = (
    [], {}, [[]], [{}], {"a": []}, {"a": {}}, [[], {}, [[]]], [[[]], [[], []]],
    [1, 2.5, -0.0, 1e+30, True, False, None],
    ["it's", 'say "hi"', "both ' and \"", "back\\slash", ""],
    ["tab\there", "line\nbreak", "\x00\x7f\u200b\u00e9\U0001f600", "\r"],
    {"k": {"k": {"k": ["deep", {"er": [1, [2, [3]]]}]}}},
    {"it's": 'a "name"', "a\nb": {"c\td": None}},
    {1: "a", None: "b", (1, "two"): "c", 2.5: ["d"]},
    [(1, "two"), Odd("<odd>"), {Odd("<name>"): [Odd("<value>")]}],
    nested(200),
)


@pytest.mark.parametrize("n", range(len(PLAIN)))
def test_a_value_with_nothing_shaped_like_a_secret_is_written_as_python_writes_it(n):
    """A list or an object leaves as Python writes it: its brackets, commas and colons, each text and name in the
    quotes and with the escapes Python gives it, each other part as Python writes that part."""
    value = PLAIN[n]
    assert connector._written(value) == str(value)
    assert connector.outward(value) == connector._escaped(str(value))[0]


def test_two_names_marked_alike_are_two_names_each_with_its_value():
    token = SHAPED["an ADE connector token (ppca_, ppcr_, ppcc_)"]
    mark = "[left out: an ADE connector token (ppca_, ppcr_, ppcc_)]"
    value = {token + "A": "first value kept", "between": 1, token + "B": ["second value kept"]}
    assert connector.outward(value) == (f"{{'{mark}': 'first value kept', 'between': 1, "
                                        f"'{mark}': ['second value kept']}}")


@pytest.mark.parametrize("shape", WORD_SHAPES)
def test_a_name_written_like_a_mark_made_before_the_first_line_stays_where_it_was_written(shape):
    """A name after a private key's first line can be the very words of a mark made before the line. Were the value
    made again as an object, the two would be one name, standing where the first stood, with the later one's value:
    a part from after the first line written before it, where the whole reading no longer takes it with the key.
    Written part by part, each part stays where it was written."""
    mark = "[left out: " + shape + "]"
    for value in ({SHAPED[shape]: "x", "k": "-----BEGIN RSA", mark: "PRIVATE KEY-----", "z": BODY, "more": "and more"},
                  {SHAPED[shape]: "x", "k": ["-----BEGIN RSA PRIVATE KEY-----"], mark: BODY, "more": "and more"},
                  [{SHAPED[shape]: 1, "k": "-----BEGIN RSA"}, {mark: ["PRIVATE KEY-----", BODY]}, "and more"]):
        assert BODY not in as_it_was(value) and "and more" not in as_it_was(value)
        all_from_the_first_line_left_out(value, shape)


def test_the_line_s_first_words_written_by_a_part_that_is_no_text_stop_the_first_reading_too():
    """No part of a checkpoint but a text or a name is written with `-----BEGIN`. Were another part so written, the
    first line the whole reading finds would begin there, and the first reading stops there as at a text."""
    shape = "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"
    later = SHAPED[shape] + "-PRIVATE KEY-----"
    for value in ([Odd("-----BEGIN RSA"), later, BODY, "and more"],
                  {Odd("-----BEGIN RSA"): [later], "z": BODY, "more": "and more"}):
        assert BODY not in as_it_was(value) and "and more" not in as_it_was(value)
        all_from_the_first_line_left_out(value, shape)
