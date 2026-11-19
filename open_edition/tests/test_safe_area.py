"""The safe area in one place. The page runs under a phone's status bar and home indicator (its viewport has
viewport-fit=cover), so every full-screen layer keeps its controls clear of them; on the phone app's TestFlight build
Linked sessions' header, its Close among it, was found under the status bar, out of reach (PHONE-TEST).

The insets are read in one place, index.css (`--safe-top` and the rest, from env(safe-area-inset-*)), and given by its
two classes, `safe-pad`, for a panel whose own background runs under the bars, and `safe-margin`, for a panel that
floats over the page, or read as those variables. So no other file names env(safe-area-inset-*); every file that draws
a full-screen layer (`fixed inset-0`, or a strip `fixed inset-x-0 top-0`) uses one of the classes, its comments not
counted; and every class string that pins something to the screen's top or foot (`fixed` with `top-0` or `bottom-...`,
a toast) reads the safe area in that same string. Where each control then stands, upright and on its side, is measured
in a browser by tests/test_phone_fit_layout.py."""

import os
import re

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui", "src")
LAYER = re.compile(r"\bfixed inset-0\b|\bfixed inset-x-0 top-0\b")
CLASSES = re.compile(r"\bsafe-(?:pad|margin)\b")
# a comment: /* ... */, or // to the line's end where it does not follow a colon or a quote (an address's //)
COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"'\w])//[^\n]*", re.S)
STRING = re.compile(r"\"[^\"\n]*\"|`[^`]*`")
PINNED = re.compile(r"\bfixed\b(?=.*(?:\btop-0\b|\bbottom-[^\s\"`]+))", re.S)
READS = re.compile(r"\bsafe-(?:pad|margin)\b|--safe-(?:top|right|bottom|left)\b")


def _sources():
    for root, _dirs, names in os.walk(SRC):
        for name in names:
            if name.endswith((".ts", ".tsx", ".css")) and ".test." not in name and not name.endswith(".d.ts"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as f:
                    yield os.path.relpath(path, SRC), f.read()


def _code(text):
    """A source file's text without its comments."""
    return COMMENT.sub("", text)


def test_the_safe_area_insets_are_read_in_index_css_alone():
    named = sorted(rel for rel, text in _sources() if "env(safe-area-inset" in text and rel != "index.css")
    assert named == [], named
    with open(os.path.join(SRC, "index.css"), encoding="utf-8") as f:
        css = f.read()
    for side in ("top", "right", "bottom", "left"):
        assert f"env(safe-area-inset-{side}" in css, side
    assert re.search(r"\.safe-pad\s*\{", css) and re.search(r"\.safe-margin\s*\{", css)


def test_every_file_that_draws_a_full_screen_layer_keeps_it_clear_of_the_bars():
    code = {rel: _code(text) for rel, text in _sources() if rel.endswith(".tsx")}
    layers = sorted(rel for rel, text in code.items() if LAYER.search(text))
    assert len(layers) >= 8, layers             # the layers read: Channel, Agents, Calendar, Board, Approvals, ...
    bare = [rel for rel in layers if not CLASSES.search(code[rel])]
    assert bare == [], bare


def test_every_class_string_that_pins_something_to_the_top_or_foot_reads_the_safe_area():
    pinned = [(rel, s) for rel, text in _sources() if rel.endswith(".tsx")
              for s in STRING.findall(_code(text)) if PINNED.search(s)]
    assert len(pinned) >= 3, pinned             # the two toasts and a failed part's line at the foot, at least
    bare = [(rel, s[:80]) for rel, s in pinned if not READS.search(s)]
    assert bare == [], bare
