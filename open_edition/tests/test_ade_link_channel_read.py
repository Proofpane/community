"""The session channel's last readings (queue 4.331). An item to look at in the clear and a round of the live view are
JSON the page parses (ade/link.py post_event; test_ade_link_texts_within.py has the shapes' reading of each text
within). Here: the rule on characters that do not show themselves reads each text within an item, where the JSON
writes such a character as an escape; a text nested past a bound is refused before it is parsed; the whole is read as
the server would write it as well as posted, so a key's shape with its parts in two texts, in the order the server
writes them, is refused whatever stands between them; and the connector's reader takes a round it cannot read as the
page does."""

import json

import pytest

from connector_fixtures import connected
from proofpane_open import oauth
from proofpane_open.ade import connector, link, link_commands
from test_ade_link import paired, post
from test_ade_link_prompts import owner  # noqa: F401  (a fixture)
from test_ade_link_texts_within import ITEMS, events, item_of, round_of

# texts the rule refuses, each for one of its clauses; JSON writes every one of them in ASCII, as escapes
RULED = {
    "a bidi override": "echo \u202edlrow olleh",
    "a zero-width space": "rm -rf \u200bbuild",
    "a line separator": "first\u2028second",
    "a soft hyphen": "pass\u00adword",
    "a control character": "ring\u0007now",
    "a carriage return, a letter in JSON": "one\rtwo",
    "a lone surrogate": "half\ud800only",
    "a mark drawn over": "a =\u0338 b",
    "a text not in normal form C": "cafe\u0301",
    "a selector with nothing before it": "\ufe0fwords",
}
KEY_FIRST, KEY_REST = "-----BEGIN RSA", "PRIVATE KEY-----"
KEY_SHAPE = "PRIVATE KEY"


def said(text, what="prompt"):
    """The rule's own words for `text` as a text of its own."""
    with pytest.raises(link.LinkError) as refused:
        link_commands.check_text(text, what)
    return str(refused.value)


# ---- the rule on characters that do not show themselves, on each text within an item ------------------------------

@pytest.mark.parametrize("which", sorted(RULED))
def test_an_item_s_text_the_rule_refuses_is_refused_wherever_it_stands(client, owner, which):  # noqa: F811
    """An item to look at is what the owner reads before a call runs. The JSON writes a character that does not show
    itself as an escape, a backslash and plain characters, so the rule read on the JSON saw none. Each text and each
    name within is held to the rule as the text it is, and refused in the words the rule has for that text."""
    mac = paired(client, owner)
    text = RULED[which]
    for where, made in ITEMS.items():
        posted = json.dumps(made(text))
        assert posted.isascii(), where
        r = item_of(mac, posted)
        assert r.status_code == 422 and r.json()["detail"] == said(text), (where, r.text)
    assert events(mac.link_id) == []


def test_the_rule_reads_both_values_of_a_name_given_twice(client, owner):  # noqa: F811
    """The page's parser keeps the last value of a name given twice, and the text that is kept holds both. The rule
    reads each, as the shapes do: the first as much as the last."""
    mac = paired(client, owner)
    hidden = RULED["a bidi override"]
    for first, last in ((hidden, "ok"), ("ok", hidden)):
        text = '{"tool": "Bash", "input": {"a": ' + json.dumps(first) + ', "a": ' + json.dumps(last) + "}}"
        assert text.isascii() and json.loads(text)["input"] == {"a": last}
        r = item_of(mac, text)
        assert r.status_code == 422 and r.json()["detail"] == said(hidden), (first, r.text)
    assert events(mac.link_id) == []


def test_the_rule_reads_an_item_s_texts_for_their_characters_and_asks_no_more_of_them(client, owner):  # noqa: F811
    """What the rule asks of the whole it does not ask of a text within: a text within may be empty, or white space
    alone, or begin as a sealed text does. A line break and a tab are a text's own. A round is not held to the rule,
    as before, and a text that is no JSON holds no text within."""
    mac = paired(client, owner)
    kept = [json.dumps({"tool": "Edit", "input": {"old_string": "", "new_string": "\n", "path": "a\tb\nc"}}),
            json.dumps({"tool": "Bash", "input": {"command": "grep e2e1. notes", "pattern": "e2e1.x", "": " "}}),
            json.dumps({"tool": "Bash", "input": {"command": "echo caf\u00e9 \U0001f600 " + chr(0x4E2D)}}),
            # an escape's own six characters, the backslash doubled in the JSON: the command's text, no character
            '{"tool": "Bash", "input": {"command": "printf \'\\\\u202e\' | xxd"}}',
            json.dumps({"tool": "Bash", "input": {"command": "echo \u26a0\ufe0f careful"}}),     # a selector after one
            "Bash"]
    for text in kept:
        assert item_of(mac, text).status_code == 200, text
    hidden = RULED["a bidi override"]
    rounds = [json.dumps({"items": [["session", hidden]]}),
              json.dumps({"items": [["session", hidden]]}, ensure_ascii=False)]
    for text in rounds:
        assert round_of(mac, text).status_code == 200, text
    # no JSON: the escape is six characters of the text itself, and the character itself is refused as it was
    plain = "a tool's words \\u202e and no more"
    assert item_of(mac, plain).status_code == 200
    r = item_of(mac, "a tool's words \u202e and no more")
    assert r.status_code == 422 and "U+202E" in r.json()["detail"], r.text
    assert [e["text"] for e in events(mac.link_id)] == kept + rounds + [plain]


# ---- a text's nesting, held before it is parsed -------------------------------------------------------------------

def test_a_text_nested_past_the_bound_is_refused_before_it_is_parsed(client, owner, monkeypatch):  # noqa: F811
    """A round or an item nests a few deep. One nested past the bound a request's own body is held to is refused,
    and is not parsed first: the parse nests as deep as the text does."""
    from proofpane_open import main
    mac = paired(client, owner)
    seen = []
    parsed = link._parsed
    monkeypatch.setattr(link, "_parsed", lambda text: seen.append(text) or parsed(text))
    most = main.MAX_NESTING
    deep = "[" * (most + 1) + "]" * (most + 1)
    item = '{"tool":"Bash","input":' + "[" * most + "]" * most + "}"       # the object is one more
    mixed = '{"items":[["session","x"]],"more":' + '{"a":[' * (most // 2) + "1" + "]}" * (most // 2) + "}"
    for r in (round_of(mac, deep), item_of(mac, item), round_of(mac, mixed), round_of(mac, "[" * 2000 + "]" * 2000)):
        assert r.status_code == 422 and "nested deeper" in r.json()["detail"], r.text
    assert seen == [] and events(mac.link_id) == []
    fits = "[" * most + "]" * most
    fits_item = '{"tool":"Bash","input":' + "[" * (most - 1) + "]" * (most - 1) + "}"
    assert round_of(mac, fits).status_code == 200 and item_of(mac, fits_item).status_code == 200
    assert seen and set(seen) == {fits, fits_item}


def test_the_bound_is_read_from_a_text_s_brackets_whether_or_not_it_is_json(client, owner):  # noqa: F811
    """The bound is read before the parse, so of a text that would not have parsed too: a text of these two kinds
    whose brackets nest past it is refused as one that is JSON is, and one at the bound is kept as the text it is."""
    from proofpane_open import main
    mac = paired(client, owner)
    most = main.MAX_NESTING
    for r in (round_of(mac, "[" * (most + 1)), item_of(mac, "Notification: " + "{" * (most + 1))):
        assert r.status_code == 422 and "nested deeper" in r.json()["detail"], r.text
    kept = ["[" * most, "Notification: " + "{" * most]
    assert round_of(mac, kept[0]).status_code == 200 and item_of(mac, kept[1]).status_code == 200
    assert [e["text"] for e in events(mac.link_id)] == kept


def test_a_bracket_within_a_text_is_no_nesting(client, owner):  # noqa: F811
    """The bound counts arrays and objects, not the brackets a text within holds, escaped quotation marks and all
    (of a text that is no JSON it counts every bracket outside a quoted run: the case before this one)."""
    mac = paired(client, owner)
    words = "[" * 300 + ' "quoted" \\ ' + "{" * 300
    for text in (json.dumps({"items": [["session", words]]}), json.dumps({"items": [["calls", [words, "]" * 300]]]})):
        assert round_of(mac, text).status_code == 200, text
    assert item_of(mac, json.dumps({"tool": "Bash", "input": {"command": words, words: "[[["}})).status_code == 200
    assert len(events(mac.link_id)) == 3


def test_the_bound_is_the_body_s_own_and_is_read_as_the_body_s_is():
    """One bound for a request's body and for the JSON a text of it holds, read the same way, without parsing."""
    from proofpane_open import main
    assert link.MAX_NESTING == main.MAX_NESTING == 64
    most = link.MAX_NESTING
    texts = ["", "x", "[]", "{}", "[" * most + "]" * most, "[" * (most + 1), "{\"a\":" * (most + 1) + "1",
             "[" * most + '"' + "[" * 500 + '"' + "]" * most, "[" * 40 + "]" * 40 + "[" * 40 + "]" * 40,
             '["' + "\\\"[" * 200 + '"]', "[" * 30 + '"never closed [[[[' + "[" * 60, "]" * 100 + "[" * 100,
             '"' + "[" * 100, "[" * 33 + "{" * 32, "[" * 32 + "{" * 32 + '"\\\\"' + "[", "\\" + "[" * 70]
    for text in texts:
        assert link._nests_too_deep(text) == main.nests_too_deep(text.encode()), text
    assert [link._nests_too_deep(t) for t in texts[4:7]] == [False, True, True]


# ---- the whole, as the server would write it ----------------------------------------------------------------------

def test_a_key_s_shape_split_between_a_name_and_its_value_is_refused_whatever_is_between(client, owner):  # noqa: F811
    """The page writes an item's input again as JSON, a name and its value on one line. A private key's shape with
    its first words in a name and the rest in that name's value was refused only where the two stood on one line of
    the text as posted, with no character of either written as an escape. The whole is read as the server would write
    it too, as a checkpoint's is."""
    mac = paired(client, owner)
    one_line = '{"tool": "Bash", "input": {"' + KEY_FIRST + '": "' + KEY_REST + '"}}'
    between = {
        "nothing, on one line": one_line,
        "a line break after the colon": one_line.replace('": "' + KEY_REST, '":\n"' + KEY_REST),
        "a line break before the colon": one_line.replace(KEY_FIRST + '": "', KEY_FIRST + '"\n: "'),
        "a carriage return and a line break": one_line.replace('": "' + KEY_REST, '":\r\n\t"' + KEY_REST),
        "the value's space written as an escape": one_line.replace("PRIVATE KEY", "PRIVATE\\u0020KEY"),
        "the name's first dash written as an escape": one_line.replace('"-----BEGIN', '"\\u002d----BEGIN'),
        "the value's first letter written as an escape": one_line.replace('"PRIVATE', '"\\u0050RIVATE'),
    }
    for how, text in between.items():
        assert json.loads(text)["input"] == {KEY_FIRST: KEY_REST}, how
        r = item_of(mac, text)
        assert r.status_code == 422 and KEY_SHAPE in r.json()["detail"], (how, r.text)
    # a round's two items on two lines of the text as posted, the same
    spread = '{"items": [["session", "' + KEY_FIRST + '"],\n["session", "' + KEY_REST + '"]]}'
    r = round_of(mac, spread)
    assert r.status_code == 422 and KEY_SHAPE in r.json()["detail"], r.text
    assert events(mac.link_id) == []


def test_of_a_name_given_twice_the_whole_is_written_with_its_last_value(client, owner):  # noqa: F811
    """The page's parser keeps the last value of a name given twice, and the page writes that one beside the name; so
    the whole as the server writes it holds the last. And the whole as posted is read still, where the first stands:
    a round's here, which nothing else reads whole (an item's is read once more, by the rule on characters)."""
    mac = paired(client, owner)
    last = '{"tool": "Bash", "input": {"' + KEY_FIRST + '": "x", "' + KEY_FIRST + '":\n"' + KEY_REST + '"}}'
    assert json.loads(last)["input"] == {KEY_FIRST: KEY_REST}
    r = item_of(mac, last)
    assert r.status_code == 422 and KEY_SHAPE in r.json()["detail"], r.text
    posted = '{"items": [["session", "' + KEY_FIRST + '"]], "items": [["session", "x"]], "more": "' + KEY_REST + '"}'
    assert KEY_FIRST not in json.dumps(json.loads(posted))
    r = round_of(mac, posted)
    assert r.status_code == 422 and KEY_SHAPE in r.json()["detail"], r.text
    assert events(mac.link_id) == []


def test_the_whole_as_the_server_writes_it_refuses_no_more_than_a_shape(client, owner):  # noqa: F811
    """Half a key's shape is no shape, in a name or a value, however the text is written; a number, a name given
    twice and NaN are written without stopping the reading; and a text that is no JSON is not written at all."""
    mac = paired(client, owner)
    kept = ['{"tool": "Bash", "input": {"' + KEY_FIRST + '":\n"public words"}}',
            '{"tool": "Bash", "input": {"n": 1e400, "big": ' + "9" * 400 + ', "nan": NaN, "a": "x", "a": "y"}}',
            '{"tool": "Bash",\n "input": {"command": "ls -la",\n  "description": "list"}}',
            '{"tool": "Bash", "input": {"k": "' + KEY_REST + '"}}',
            '{"tool": "Bash", "input": {"k": "' + KEY_FIRST + '",\n"m": "words"}}',
            '{"tool": "Bash", "input": {"' + KEY_FIRST + '":']                 # cut short: no JSON
    for text in kept:
        assert item_of(mac, text).status_code == 200, text
    assert [e["text"] for e in events(mac.link_id)] == kept


# ---- the connector's reader ---------------------------------------------------------------------------------------

UNREADABLE = ['{"items": [[["x"], "words"]]}', '{"items": [[{"who": "session"}, "words"]]}',
              '{"items": [["session", "fine"], [[], "words"]]}', '{"items": [[["calls"], ["ls"]]]}',
              '{"items": [[1, "words"]]}', '{"items": [[null, "words"]]}', '{"items": [[true, "words"]]}',
              '{"items": [["session", ["words"]]]}', '{"items": [["calls", "ls"]]}', '{"items": [["calls", [1]]]}',
              '{"items": [["session", "a", "b"]]}', '{"items": [["nobody", "words"]]}', '{"items": []}',
              '{"items": "words"}', '{"other": 1}', '[["session", "words"]]', '"words"', "7", "null", "no JSON"]


@pytest.mark.parametrize("text", UNREADABLE)
def test_the_connector_s_reader_takes_a_round_it_cannot_read_as_one(text):
    """A round that is not a round's items is the mirror's word that it could not be read, as the page says it
    (Channel.tsx liveItems), whatever its items are: never an error."""
    assert connector.live_said(text) == [("info", connector.LIVE_UNREAD)]


def test_a_round_the_connector_reads_is_read_as_it_was():
    assert connector.live_said('{"items": [["owner", "go"], ["calls", ["Bash: ls"]], ["session", "done"], '
                               '["info", "left out"]]}') == [("owner", "go"), ("session", "done"),
                                                              ("info", "left out")]


def test_a_conversation_with_a_round_the_connector_cannot_read_is_still_given(client, owner, monkeypatch):  # noqa: F811
    """A round whose item's first part is a list, and one whose is an object, are kept, as the page takes each for a
    round it could not read. The connector gives the conversation with that word in the place of each, and does not
    fail the read."""
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    oauth._failed.clear()
    connector._ws_cache.clear()
    app = oauth.authenticate("Bearer " + connected(client, owner)["access_token"])
    mac = paired(client, owner)
    for text in ('{"items": [["session", "before"]]}', UNREADABLE[0], UNREADABLE[1],
                 '{"items": [["session", "after"]]}'):
        assert post(mac, "note", text, to="owner", mirror="live").status_code == 200, text
    result = connector.call(app, {"name": "read_conversation", "arguments": {"session": f"link:{mac.link_id}"}}, 0)
    assert result["isError"] is False, result
    assert [m["text"] for m in result["structuredContent"]["messages"]] == ["before", connector.LIVE_UNREAD,
                                                                           connector.LIVE_UNREAD, "after"]
