"""The session channel's two texts that are JSON (ade/link.py post_event): a round of the live view (a mirrored
note, mirror "live") and an item to look at (a prompt with look, in the clear while encryption is off). The page
parses each and shows the texts within. So each text and name within is held to the secret shapes as the text it is,
before the whole is: in the JSON a line break, a tab or a carriage return before a shape is a backslash and a
letter, and a shape that begins a word begins none. A keyed checkpoint's cases are in test_ade_link.py."""

import json
import sys
import time

import pytest

from proofpane_open import store
from proofpane_open.ade import connector, link
from test_ade_link import AFTER_A_BREAK, paired, post
from test_ade_link_prompts import owner  # noqa: F401  (a fixture)

LEADS = {"a line break": "key:\n", "a tab": "\t", "a carriage return": "\r", "two lines": "a\nb\n"}
ROUNDS = {
    "the session's words": lambda words: {"items": [["owner", "go"], ["session", words]]},
    "the owner's words": lambda words: {"items": [["owner", words]]},
    "the mirror's own word": lambda words: {"items": [["session", "done"], ["info", words + " and more"]]},
    "a call's line": lambda words: {"items": [["session", "ran"], ["calls", ["Bash: ls", words]]]},
}
ITEMS = {
    "a tool's input": lambda words: {"tool": "Bash", "input": {"command": words}},
    "deep in a tool's input": lambda words: {"tool": "Edit", "input": {"edits": [{"old": "x", "new": [words]}]}},
    "a name in a tool's input": lambda words: {"tool": "Bash", "input": {words: 1}},
    "the tool's name": lambda words: {"tool": words, "input": {}},
    "a question's option": lambda words: {"tool": "AskUserQuestion", "questions": [
        {"question": "which?", "options": [{"label": "one", "description": words}]}]},
    "what Claude Code raised": lambda words: {"event": "Notification", "words": {"message": words}},
}


def events(link_id):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT kind, mirror, text FROM ade_link_events WHERE link_id = ? ORDER BY "
                                            "seq", (link_id,))]


def round_of(mac, text):
    return post(mac, "note", text, mirror="live")


def item_of(mac, text):
    return post(mac, "prompt", text, deadline=int(time.time()) + 600, look=True)


def refused_for(r, secret, shape):
    assert r.status_code == 422 and shape in r.json()["detail"] and secret not in r.json()["detail"], r.text


@pytest.mark.parametrize("lead", sorted(LEADS))
@pytest.mark.parametrize("secret,shape", AFTER_A_BREAK)
def test_a_round_s_secret_is_refused_wherever_it_stands_in_an_item(client, owner, secret, shape, lead):  # noqa: F811
    """A round's items are held to the shapes as the texts they are: the owner's words, the session's, the mirror's
    own and each line of the calls. The same words as a note of their own were always refused."""
    mac = paired(client, owner)
    for where, made in ROUNDS.items():
        refused_for(round_of(mac, json.dumps(made(LEADS[lead] + secret))), secret, shape)
    assert events(mac.link_id) == []
    refused_for(post(mac, "note", LEADS[lead] + secret), secret, shape)


@pytest.mark.parametrize("lead", sorted(LEADS))
@pytest.mark.parametrize("secret,shape", AFTER_A_BREAK)
def test_an_item_to_look_at_s_secret_is_refused_wherever_it_stands(client, owner, secret, shape, lead):  # noqa: F811
    """An item to look at in the clear is the tool and its whole input, or what Claude Code raised: each text and
    each name within, at any depth, is held to the shapes as the text it is."""
    mac = paired(client, owner)
    for where, made in ITEMS.items():
        refused_for(item_of(mac, json.dumps(made(LEADS[lead] + secret))), secret, shape)
    assert events(mac.link_id) == []


def test_a_round_and_an_item_with_nothing_shaped_like_a_secret_are_kept_as_they_were(client, owner):  # noqa: F811
    mac = paired(client, owner)
    words = "first line\nsecond line\tand a tab"
    rounds = [json.dumps(made(words)) for made in ROUNDS.values()]
    items = [json.dumps(made(words)) for made in ITEMS.values()]
    for text in rounds:
        assert round_of(mac, text).status_code == 200, text
    for text in items:
        assert item_of(mac, text).status_code == 200, text
    kept = events(mac.link_id)
    assert [e["text"] for e in kept] == rounds + items
    assert [(e["kind"], e["mirror"]) for e in kept] == ([("note", "live")] * len(rounds)
                                                       + [("prompt", None)] * len(items))


def test_the_escapes_json_has_for_a_line_break_are_each_read(client, owner):  # noqa: F811
    """The text is read as the page parses it: a line break written as backslash n, or as its number."""
    mac = paired(client, owner)
    secret, shape = AFTER_A_BREAK[2]
    for escape in ("\\n", "\\u000a", "\\u000A", "\\r\\n", "\\t", "\\u0009", "\\u2028"):
        text = '{"items": [["session", "note:' + escape + secret + '"]]}'
        assert json.loads(text)["items"][0][1].endswith(secret)
        refused_for(round_of(mac, text), secret, shape)
        item = '{"tool": "Bash", "input": {"command": "echo' + escape + secret + '"}}'
        refused_for(item_of(mac, item), secret, shape)
    assert events(mac.link_id) == []


def test_a_shape_s_own_characters_written_as_json_s_escapes_are_read(client, owner):  # noqa: F811
    """JSON may write any character of a text as its number. A shape written so is no shape in the JSON and is one in
    the text the page shows: its first letter, a sign within it, the space the Fly shape ends with."""
    mac = paired(client, owner)
    written = [("\\u0073k-ant-" + "api03-abc", "sk-ant-"), ("sk\\u002dant-" + "api03-abc", "sk-ant-"),
               ("FlyV1\\u0020" + "fm2_x", "FlyV1"), ("gh\\u0070_" + "x", "ghp_"), ("\\u0041KIA" + "xyz", "AKIA")]
    for escaped, shape in written:
        text = '{"items": [["session", "' + escaped + '"]]}'
        secret = json.loads(text)["items"][0][1]
        assert secret not in text and "\\" not in secret
        refused_for(round_of(mac, text), secret, shape)
        refused_for(item_of(mac, '{"tool": "Bash", "input": {"command": "' + escaped + '"}}'), secret, shape)
        refused_for(item_of(mac, '{"tool": "Bash", "input": {"' + escaped + '": 1}}'), secret, shape)
    assert events(mac.link_id) == []


def test_a_text_under_a_name_the_page_reads_past_is_read_all_the_same(client, owner):  # noqa: F811
    """The page shows a round's items and an item's tool and input, and reads past any other name. The ADE would keep
    what stands under one, so that is held to the shapes too, wherever it stands and whatever the whole is."""
    mac = paired(client, owner)
    secret, shape = AFTER_A_BREAK[2]
    words, good = "note:\n" + secret, [["session", "all good"]]
    for text in (json.dumps({"items": good, "more": words}), json.dumps({"more": {"deeper": [words]}, "items": good}),
                 json.dumps([{"items": good}, words])):
        refused_for(round_of(mac, text), secret, shape)
    for text in (json.dumps({"tool": "Bash", "input": {"command": "ls"}, "more": words}),
                 json.dumps({"more": [words], "tool": "Bash", "input": {}})):
        refused_for(item_of(mac, text), secret, shape)
    assert events(mac.link_id) == []


def test_a_text_that_is_one_json_text_is_read_as_that_text(client, owner):  # noqa: F811
    """JSON may be a single text, with no list or object around it: the text within is read all the same."""
    mac = paired(client, owner)
    secret, shape = AFTER_A_BREAK[2]
    for send in (round_of, item_of):
        refused_for(send(mac, json.dumps("note:\n" + secret)), secret, shape)
        assert send(mac, json.dumps("first line\nsecond line")).status_code == 200
    assert len(events(mac.link_id)) == 2


def test_a_name_given_twice_has_each_of_its_values_read(client, owner):  # noqa: F811
    """The page reads the last of two values given under one name. Both are in the text that is kept, so both are
    held to the shapes, whichever comes last."""
    mac = paired(client, owner)
    secret, shape = AFTER_A_BREAK[2]
    safe, shaped = '[["session", "all good"]]', '[["session", "note:\\n' + secret + '"]]'
    for text in ('{"items": ' + safe + ', "items": ' + shaped + '}',
                 '{"items": ' + shaped + ', "items": ' + safe + '}'):
        assert isinstance(json.loads(text), dict)
        refused_for(round_of(mac, text), secret, shape)
    for text in ('{"tool": "Bash", "input": {"a": "ok", "a": "x\\n' + secret + '"}}',
                 '{"tool": "Bash", "input": {"a": "x\\n' + secret + '", "a": "ok"}}'):
        refused_for(item_of(mac, text), secret, shape)
    assert events(mac.link_id) == []


def test_a_long_number_does_not_stop_the_reading(client, owner):  # noqa: F811
    """Python reads no whole number of more digits than its limit, and says so as it says a text is no JSON; the page
    reads any. So a number is not read at all here, and the texts beside it are."""
    mac = paired(client, owner)
    secret, shape = AFTER_A_BREAK[2]
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        digits = "1" * 1000
        with pytest.raises(ValueError):
            json.loads(digits)
        refused_for(round_of(mac, '{"n": ' + digits + ', "items": [["session", "note:\\n' + secret + '"]]}'),
                    secret, shape)
        refused_for(item_of(mac, '{"tool": "Bash", "input": {"n": -' + digits + 'e5, "c": "x\\n' + secret + '"}}'),
                    secret, shape)
        assert round_of(mac, '{"n": ' + digits + ', "items": [["session", "all good"]]}').status_code == 200
    finally:
        sys.set_int_max_str_digits(limit)
    assert [e["mirror"] for e in events(mac.link_id)] == ["live"]


def test_a_number_that_is_none_of_json_s_does_not_stop_the_reading(client, owner):  # noqa: F811
    """Python reads NaN and the infinities, which JSON has not and the page refuses; so does the connector's reader
    of a round (connector.live_said), which gives its items to a connected app. So a round or an item that holds one
    is read here too, and its texts within are held to the shapes."""
    mac = paired(client, owner)
    secret, shape = AFTER_A_BREAK[2]
    for constant in ("NaN", "Infinity", "-Infinity"):
        shaped = '{"n": ' + constant + ', "items": [["session", "note:\\n' + secret + '"]]}'
        assert connector.live_said(shaped) == [("session", "note:\n" + secret)]
        refused_for(round_of(mac, shaped), secret, shape)
        refused_for(item_of(mac, '{"tool": "Bash", "input": {"n": ' + constant + ', "c": "x\\n' + secret + '"}}'),
                    secret, shape)
        assert round_of(mac, '{"n": ' + constant + ', "items": [["session", "all good"]]}').status_code == 200
    assert [e["mirror"] for e in events(mac.link_id)] == ["live"] * 3


def test_a_text_the_ade_cannot_read_for_its_depth_is_refused(client, owner, monkeypatch):  # noqa: F811
    """A text nested past the bound is refused before it is parsed (test_ade_link_channel_read.py). Were one within
    the bound still too deep for Python to read, it is refused, not kept unread: the page might read it."""
    mac = paired(client, owner)
    deep = "[" * 60 + "]" * 60
    assert round_of(mac, deep).status_code == 200                                     # read, and holding no text

    def too_deep(text):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(link, "_parsed", too_deep)
    for r in (round_of(mac, '{"items": [["session", "all good"]]}'), item_of(mac, '{"tool": "Bash", "input": {}}')):
        assert r.status_code == 422 and "nested deeper" in r.json()["detail"], r.text
    # a note that is neither is not parsed at all
    assert post(mac, "note", "plain words").status_code == 200
    assert [e["text"] for e in events(mac.link_id)] == [deep, "plain words"]


def test_a_text_that_is_no_json_is_read_as_the_text_it_is_and_no_other_way(client, owner):  # noqa: F811
    """A round or an item that is no JSON is not parsed by the page either, which shows it as it is or says it
    could not be read. It is held to the shapes as any text is: a shape after a line break is refused; one after a
    backslash and the letter n, two characters, begins no word, in a round as in any note."""
    mac = paired(client, owner)
    secret, shape = AFTER_A_BREAK[2]
    for send in (round_of, item_of):
        refused_for(send(mac, "no round:\n" + secret), secret, shape)
        refused_for(send(mac, '{"items": [["session", "x"]]} and then\n' + secret), secret, shape)
    kept = ["not a round at all", "a backslash and n:\\n" + secret,
            '{"items": [["session", "x\\n' + secret + '"]]} more']
    for text in kept:
        with pytest.raises(ValueError):
            json.loads(text)
        assert round_of(mac, text).status_code == 200, text
    assert post(mac, "note", kept[1]).status_code == 200
    assert [e["text"] for e in events(mac.link_id)] == kept + [kept[1]]


def test_the_whole_is_still_read_and_only_a_round_or_an_item_is_parsed(client, owner):  # noqa: F811
    """A shape whose parts stand in two texts of a round shows only in the whole, which is read as before. And a text
    that is neither a round nor an item is not parsed: a note whose words are JSON is the text it is."""
    mac = paired(client, owner)
    two = {"items": [["session", "-----BEGIN OPENSSH"], ["session", "PRIVATE KEY-----"]]}
    for r in (round_of(mac, json.dumps(two)), item_of(mac, json.dumps({"tool": "Bash", "input": two}))):
        assert r.status_code == 422 and "PRIVATE KEY" in r.json()["detail"], r.text
    secret, shape = AFTER_A_BREAK[2]
    quoted = json.dumps({"items": [["session", "note:\n" + secret]]})
    for mirror in (None, "session", "step"):
        assert post(mac, "note", quoted, **({"mirror": mirror} if mirror else {})).status_code == 200, mirror
    assert post(mac, "step", quoted).status_code == 200
    assert [e["text"] for e in events(mac.link_id)] == [quoted] * 4


def test_nothing_is_parsed_before_the_text_s_size_is_held(client, owner, monkeypatch):  # noqa: F811
    """A text longer than MAX_TEXT is refused for its size, and is not parsed first."""
    mac = paired(client, owner)
    seen = []
    parsed = link._parsed
    monkeypatch.setattr(link, "_parsed", lambda text: seen.append(len(text)) or parsed(text))
    long = json.dumps({"items": [["session", "x" * link.MAX_TEXT]]})
    for r in (round_of(mac, long), item_of(mac, long)):
        assert r.status_code == 422 and "at most" in r.json()["detail"], r.text
    assert seen == []
    fits = json.dumps({"items": [["session", "x" * (link.MAX_TEXT - 30)]]})
    assert len(fits) <= link.MAX_TEXT and round_of(mac, fits).status_code == 200 and seen == [len(fits)]
