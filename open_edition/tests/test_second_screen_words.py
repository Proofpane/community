"""The second screen's words. A signature's marker is the signing
program's own word, which any program that reaches the key can write and which says nothing of what was shown. So
nothing may say that a step was signed or shown on the owner's Mac, that they saw the words, that the second screen
verified anything, or, as a fact, that a step was signed outside a browser; and the manual keeps the limits it states,
so that taking one out is a change a test sees."""

import os
import re
from conftest import manual_or_skip

HERE = os.path.dirname(os.path.abspath(__file__))
README = os.path.normpath(os.path.join(HERE, "..", "MANUAL.md"))
UI_SRC = os.path.normpath(os.path.join(HERE, "..", "ui", "src"))

# what must never be said, of a step or of the second screen, in any case
NEVER = ("signed on your mac", "shown on your mac", "you saw these words", "verified by the second screen",
         "the browser is safe")
# "outside a browser" only as the signing program's word, or as what a program can pretend
OUTSIDE = re.compile(r"outside a browser", re.I)
OUTSIDE_AS_A_WORD = ("says it was signed ", "as if ")
# what the manual says of its own limits, each of which taking out would make it claim more
LIMITS = ("40 bits prove nothing",
          "no security key and no part of WebAuthn tells anyone which program asked for a signature",
          "does not make signing in a browser safe",
          "it says nothing of what was shown",
          "A prompt the owner did not start is not theirs",
          "a server can still make a change only it checks (linking a session) without any signature",
          "it cannot use a platform passkey")


def _flat(text):
    return " ".join(text.split())


def _readme():
    manual_or_skip()
    with open(README, encoding="utf-8") as fh:
        return _flat(fh.read())


def _section(text):
    start = text.index("### The second screen:")
    end = text.find(" ### ", start + 1)
    return text[start:end if end > 0 else len(text)]


def never_said(text):
    """What `text` says that the second screen may never say: each forbidden phrase found, and each "outside a browser"
    not given as the signing program's word."""
    low = text.lower()
    found = [p for p in NEVER if p in low]
    for m in OUTSIDE.finditer(text):
        before = text[max(0, m.start() - 40):m.start()].lower()
        if not before.endswith(OUTSIDE_AS_A_WORD):
            found.append(text[max(0, m.start() - 40):m.end()])
    return found


def test_the_manual_says_nothing_of_the_second_screen_it_may_not():
    assert never_said(_readme()) == []


def _page_sources():
    """The page's own source (ui/src), but its tests: what it may say of a step, as it is written there."""
    for root, _dirs, files in os.walk(UI_SRC):
        for name in sorted(files):
            if name.endswith((".ts", ".tsx")) and ".test." not in name:
                yield os.path.join(root, name)


def test_the_page_says_nothing_of_the_second_screen_it_may_not():
    said, read = {}, {}
    for path in _page_sources():
        with open(path, encoding="utf-8") as fh:
            read[os.path.relpath(path, UI_SRC)] = text = _flat(fh.read())
        if never_said(text):
            said[os.path.relpath(path, UI_SRC)] = never_said(text)
    assert said == {}
    # the page's words were read where they are: the marker's, as the signing program's word, and the Mac's request
    assert "says it was signed outside a browser" in read["secondScreen.ts"]
    assert "ade_link.py sign" in read[os.path.join("components", "SignOnMac.tsx")]


def test_the_manual_keeps_the_limits_it_states():
    section = _section(_readme())
    for limit in LIMITS:
        assert limit in section, limit


def test_the_rule_finds_what_it_is_for():
    # each forbidden way of saying it is found, in any case and across a line's end; the program's word is not
    assert never_said("This step was Signed on your\nMac.".replace("\n", " ")) == ["signed on your mac"]
    assert never_said("You saw these words.") == ["you saw these words"]
    assert never_said("Verified by the second screen.") == ["verified by the second screen"]
    assert never_said("The browser is safe.") == ["the browser is safe"]
    assert never_said("Shown on your Mac.") == ["shown on your mac"]
    assert len(never_said("It was signed outside a browser.")) == 1
    assert len(never_said("Signed outside a browser, on a Mac.")) == 1
    assert never_said("The program says it was signed outside a browser.") == []
    assert never_said("A compromised browser can sign as if outside a browser.") == []
