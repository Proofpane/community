"""The manual's section on the skills the sessions called, read by the tests that hold its numbers to the code (a number
the manual states and no test reads back can move without a test failing)."""

import os
import re

from conftest import ROOT, manual_or_skip

WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}


def text(*parts):
    """A file of the repository's `open_edition` folder, its blanks run into one."""
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return re.sub(r"\s+", " ", fh.read())


def section():
    manual_or_skip()
    readme = text("MANUAL.md")
    start = readme.index("## The skills the sessions called")
    return readme[start:readme.index(" ## ", start + 10)]


def stated(pattern, where=None):
    """The one number a phrase of the section (or of `where`) states, in digits (commas allowed) or in words."""
    found = re.findall(pattern, section() if where is None else where)
    assert len(found) == 1, (pattern, found)
    one = found[0]
    return int(one.replace(",", "")) if one[0].isdigit() else WORDS[one]
