"""Strips of words at a phone's width, 360 and 390 pixels: the strip that says Claude Code is not signed in in the
workspace grows with its words, so they wrap inside it and never over the bar above it or the row below, its button
whole and on the screen beside them, with either of its two sentences; the Agents panel's header keeps its name, its
counts (on one line) and its buttons inside it, its buttons on a row of their own where they do not fit; a dialog's
title row holds a title as long as an agent's name makes it, spaced or not, its close button on the screen; the command
palette's rows hold its longest actions, at a desktop's width too; the New session form's Task row keeps its pill naming
a pull request on one line and its buttons inside the row, and a History row its columns; all of them are as they were
at a desktop's width but the palette's longest row, which holds its two lines now, and the New session form's From row
is one row there too, as is a History row in the Linked sessions panel's 560 pixels. The keys under a Claude Code
session's terminal on a desktop stay inside the box they sit in, across the page and in a pane 220 pixels wide, wrapping
there, every button's name whole and Stop among them, one row at a desktop's width. And no element of a fixed height
that holds words (a class such as h-9, h-[36px] or size-8, or index.css's .btn, .icon-btn, .input, .kbd, .pill or
.palette-item), on the pages the layout harness draws (those, a Mac's session in the History list, the chat box idle and
busy, a linked session's conversation as a wide and a narrow panel draw it, its card with its More menu open, and a
change of two files with long paths), lets anything in it reach past its top or bottom, or past the right of the box it
sits in. Measured in a headless Chromium that takes taps: ui/scripts/measure-fit.mjs builds ui/layout.html, which draws
them as the app draws them with fixed data and no server, and prints what each measures.

And on a screen that takes taps, every field that takes text on those pages, on the sign-in page, in the Files panel's
code editor with its search panel open (its text, an area edited in place, counted as a field), in the New session
form as it opens and in Linked sessions' form for linking a session computes to 16 pixels or more: iOS zooms a page in
when a smaller one takes focus, and keeps the zoom after the page changes, as found on the phone app's first
TestFlight build (PHONE-TEST); on a screen without touch, a desktop's, the fields keep their own sizes. And on a
phone upright, with a 59-pixel status bar and a 34-pixel home indicator, each full-screen layer's close controls stand
clear of them, so that a layer can be closed (tests/test_safe_area.py); upright and on its side (59 pixels at either
side and a 21-pixel home indicator), no control and no toast on a full-screen layer or at the screen's edges (the
chat's box, the title bar, a terminal's keys, the toasts, a failed part's line, a program's Start it again, the
sign-in page) lies in a bar or at a side; and the phone's sheet shows its own background under both bars.

Skipped where no browser is named: OPEN_EDITION_PLAYWRIGHT, the playwright package's folder, and OPEN_EDITION_CHROMIUM,
a Chromium executable to drive."""

import json
import os
import shutil
import subprocess

import pytest

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui")
PLAYWRIGHT = os.environ.get("OPEN_EDITION_PLAYWRIGHT")
CHROMIUM = os.environ.get("OPEN_EDITION_CHROMIUM")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    not (PLAYWRIGHT and CHROMIUM and NODE and os.path.isdir(os.path.join(UI, "node_modules"))),
    reason="no headless browser named (OPEN_EDITION_PLAYWRIGHT, OPEN_EDITION_CHROMIUM) or no node_modules")

# each page, and how many elements of a fixed height that hold words it draws at least, so that an empty list of
# those that spill means they were read: the strips mended by a utility class grow and leave the count, while the
# palette's rows and the pills stay in it by their index.css class
PAGES = {"banner": 1, "bannerwho": 1, "agents": 5, "modal": 0, "modalword": 0, "palette": 3, "newsession": 6,
         "history": 4, "diff": 5, "chat": 4, "chatbusy": 5, "link": 4, "linknarrow": 4, "card": 5, "cardmenu": 8,
         "deskkeys": 10, "deskkeyspane": 10}


@pytest.fixture(scope="module")
def strips(tmp_path_factory):
    out = tmp_path_factory.mktemp("fit")
    run = subprocess.run([NODE, "scripts/measure-fit.mjs", PLAYWRIGHT, CHROMIUM, str(out)], cwd=UI,
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("width", [360, 390])
@pytest.mark.parametrize("which", ["banner", "bannerwho"])
def test_the_sign_in_strip_grows_with_its_words_at_a_phones_width(strips, which, width):
    got = strips[f"banner.{which}.{width}"]
    assert got["lines"] >= 2, got                       # the sentence does wrap at this width: the case reaches it
    assert got["highest"] >= got["top"] - 0.5, got      # nothing over the bar above
    assert got["lowest"] <= got["bottom"] + 0.5, got    # nor over the row below
    assert got["right"] <= got["screen"] + 0.5, got     # its words and its button within the screen
    assert got["cut"] <= 0, got                         # the button's name whole


@pytest.mark.parametrize("which", ["banner", "bannerwho"])
def test_at_a_desktops_width_the_sign_in_strip_is_as_it_was(strips, which):
    got = strips[f"banner.{which}.1280"]
    assert got["lines"] == 1, got                       # one line
    assert abs(got["height"] - 36) <= 0.5, got          # the old fixed height, h-9: it grows, it never shrinks


@pytest.mark.parametrize("width", [360, 390])
def test_the_agents_header_holds_its_words_and_buttons_at_a_phones_width(strips, width):
    got = strips[f"agents.header.{width}"]
    assert got["highest"] >= got["top"] - 0.5, got      # nothing over what is above it
    assert got["lowest"] <= got["bottom"] + 0.5, got    # nor over the list below
    assert got["right"] <= got["screen"] + 0.5, got     # its buttons within the screen
    assert got["countLines"] == 1, got                  # its counts on one line, not a column of single words


def test_at_a_desktops_width_the_agents_header_is_as_it_was(strips):
    got = strips["agents.header.1280"]
    assert got["rows"] == 1, got                        # one row
    assert abs(got["height"] - 48) <= 0.5, got          # the old fixed height, h-12


@pytest.mark.parametrize("width", [360, 390])
def test_a_dialogs_title_row_holds_a_title_as_long_as_an_agents_name_at_a_phones_width(strips, width):
    got = strips[f"modal.title.{width}"]
    assert got["lines"] >= 3, got                       # the title does take three lines here: the case reaches it
    assert got["highest"] >= got["top"] - 0.5, got      # nothing over the dialog's edge above
    assert got["lowest"] <= got["bottom"] + 0.5, got    # nor over its body below
    assert got["right"] <= got["screen"] + 0.5, got     # its close button within the screen


def test_at_a_desktops_width_a_dialogs_title_row_is_as_it_was(strips):
    got = strips["modal.title.1280"]
    assert abs(got["height"] - 44) <= 0.5, got          # the old fixed height, h-11, for a title of two lines or one


@pytest.mark.parametrize("width", [360, 390])
def test_a_dialogs_title_row_breaks_a_name_with_no_space_in_it_at_a_phones_width(strips, width):
    got = strips[f"modalword.title.{width}"]
    assert got["lines"] >= 3, got                       # broken onto lines, not run out of the row
    assert got["highest"] >= got["top"] - 0.5, got
    assert got["lowest"] <= got["bottom"] + 0.5, got
    assert got["right"] <= got["screen"] + 0.5, got     # its close button within the screen


def test_at_a_desktops_width_the_palettes_rows_hold_their_labels(strips):
    got = strips["palette.items.1280"]
    assert len(got) == 3 and max(r["lines"] for r in got) >= 2, got    # its three actions, the longest on two lines
    for r in got:
        assert r["wordsTop"] >= r["top"] - 0.5 and r["wordsBottom"] <= r["bottom"] + 0.5, r     # each label in its row
        assert r["lines"] > 1 or abs(r["height"] - 36) <= 0.5, r      # a row of one line as it was, 36 px


@pytest.mark.parametrize("width", [360, 390])
def test_the_new_session_forms_task_row_keeps_its_pill_and_buttons_inside_it_at_a_phones_width(strips, width):
    got = strips[f"newsession.taskrow.{width}"]
    assert got["right"] <= got["edge"] + 0.5, got       # nothing past the row's edge or the screen's: it wraps instead
    assert got["lines"] == 1, got                       # the pill's words on one line


@pytest.mark.parametrize("width", [360, 390])
def test_a_history_rows_columns_stay_inside_it_at_a_phones_width(strips, width):
    got = strips[f"history.row.{width}"]
    assert got["right"] <= got["edge"] + 0.5, got       # its three columns within the row and the screen: it wraps


def test_at_a_desktops_width_the_new_session_forms_rows_and_a_history_row_are_one_row_as_before(strips):
    assert strips["newsession.taskrow.1280"]["rows"] == 1, strips["newsession.taskrow.1280"]
    assert strips["newsession.taskrow.1280"]["lines"] == 1, strips["newsession.taskrow.1280"]
    assert strips["newsession.fromrow.1280"]["rows"] == 1, strips["newsession.fromrow.1280"]    # field, note, button
    assert strips["history.row.1280"]["rows"] == 1, strips["history.row.1280"]
    assert strips["historypanel.row.1280"]["rows"] == 1, strips["historypanel.row.1280"]     # in the 560 px panel


@pytest.mark.parametrize("width", [360, 390, 1280])
@pytest.mark.parametrize("which", ["deskkeys", "deskkeyspane"])
def test_the_keys_under_a_desktops_terminal_stay_inside_their_box_with_stop_whole(strips, which, width):
    got = strips[f"{which}.row.{width}"]
    assert got["parts"] == 12, got                      # nine keys, Attach, the state line and Stop, all drawn
    assert got["right"] <= got["edge"] + 0.5, got       # nothing past the page or the pane: the row wraps instead
    assert got["stopRight"] <= got["edge"] + 0.5, got   # Stop on the screen, inside the pane
    assert got["cut"] <= 0, got                         # every button's name whole


def test_at_a_desktops_width_the_keys_under_a_terminal_are_one_row(strips):
    assert strips["deskkeys.row.1280"]["rows"] == 1, strips["deskkeys.row.1280"]
    assert strips["deskkeyspane.row.1280"]["rows"] >= 2, strips["deskkeyspane.row.1280"]    # the case reaches a wrap


@pytest.mark.parametrize("width", [360, 390])
@pytest.mark.parametrize("which", sorted(PAGES))
def test_no_element_of_a_fixed_height_lets_its_words_out_at_a_phones_width(strips, which, width):
    got = strips[f"strips.{which}.{width}"]
    assert len(got) >= PAGES[which], got                # the measure reached what the page draws
    out = [s for s in got if s["past"] > 1 or s["wide"] > 1]      # out of its top or bottom, or past its box's right
    assert out == [], out


# each page that draws a field that takes text, and how many it draws at least, so that a list with none under 16
# pixels means they were read: the sign-in page's email and password, the chat box's three pickers and its text box, a
# linked session's conversation's text box and its expiry, the New session form's repository and task (and, as it
# opens, before a pull request is tapped, its Start from), the palette's search, the Agents panel's select, the code
# editor's text with its search panel's Find and Replace, and the name of a session being linked
FIELDS = {"signin": 2, "chat": 4, "chatbusy": 4, "link": 2, "linknarrow": 2, "newsession": 2, "palette": 1,
          "agents": 1, "newsessionfrom": 3, "editor": 3, "linkform": 1}


@pytest.mark.parametrize("width", [360, 390])
def test_the_measure_draws_on_a_screen_whose_pointer_is_a_finger(strips, width):
    assert strips[f"pointer.{width}"] is True           # (pointer: coarse) holds there, as on a phone


@pytest.mark.parametrize("width", [360, 390])
@pytest.mark.parametrize("which", sorted(FIELDS))
def test_every_field_that_takes_text_is_16px_or_more_on_a_touch_screen(strips, which, width):
    got = strips[f"fields.{which}.{width}"]
    assert len(got) >= FIELDS[which], got               # the measure reached the page's fields
    small = [f for f in got if f["size"] < 16]          # iOS zooms the page in when one of these takes focus
    assert small == [], small


@pytest.mark.parametrize("which", ["signin", "chat", "newsession", "palette", "editor", "linkform"])
def test_on_a_screen_without_touch_the_fields_keep_their_own_sizes(strips, which):
    assert strips["pointer.fine"] is False              # a desktop's pointer: (pointer: coarse) does not hold
    got = strips[f"fields.fine.{which}"]
    assert len(got) >= FIELDS[which], got
    assert [f for f in got if f["size"] >= 16] == [], got     # the touch screen's mend reaches none of them


# each full-screen layer the harness draws, on a phone of 390 by 844 with a status bar of 59 pixels and a home indicator
# of 34 (the safe-area override): Linked sessions on its list and on linking a session, the Agents panel, the Calendar,
# the Progress board, Approvals, a dialog and the phone's list of sessions in its sheet; Linked sessions' Close was
# found under the status bar on the phone app's TestFlight build, out of reach (tests/test_safe_area.py)
LAYERS = ["channel", "linkform", "agents", "calendar", "board", "approvals", "modal", "sheet"]
# and what stands at the screen's edges outside them: the chat's box, the title bar, a terminal's keys at the foot, the
# app's two toasts, a failed part's inline line, a program's Start it again, and the sign-in page
EDGES = ["chat", "titlebar", "keybar", "toast", "notice", "crash", "stopped", "signin"]
SAFE = LAYERS + ["palette"] + EDGES
# the phone upright, and on its side: the four insets each draws under
HELD = {"safe": {"top": 59, "right": 0, "bottom": 34, "left": 0},
        "side": {"top": 0, "right": 59, "bottom": 21, "left": 59}}


def test_the_measure_draws_under_a_phones_bars_upright_and_on_its_side(strips):
    for held, inset in HELD.items():
        for which in SAFE:
            assert strips[f"{held}.{which}"]["inset"] == inset, (held, which)


@pytest.mark.parametrize("which", LAYERS)
def test_each_full_screen_layers_close_controls_stand_clear_of_the_status_bar_and_home_indicator(strips, which):
    got = strips[f"safe.{which}"]
    assert got["closes"], got                           # the layer's Close was found
    top, bottom = got["inset"]["top"], got["screen"]["height"] - got["inset"]["bottom"]
    under = [c for c in got["closes"] if c["top"] < top - 0.5 or c["bottom"] > bottom + 0.5]
    assert under == [], got


# a control in a box that scrolls passes under a bar as the box scrolls, as a list's rows do, but must clear the top
# with the box scrolled to its start and the foot with it scrolled to its end; a box that scrolls up and down moves
# nothing out of a side, nor one that scrolls sideways out of a bar (the sign-in page on a phone's side scrolls, its
# last control passing under the home indicator, and resting 32 pixels above the foot once scrolled there)
def _out(got):
    """The controls and toasts of a page that lie in a bar or at a side: as they show, or, on an axis a box above them
    scrolls, where they stand with it scrolled to its start (for the top, or the left) and its end (the foot, the
    right)."""
    inset, width, height = got["inset"], got["screen"]["width"], got["screen"]["height"]

    def at(c, edge, scrolled):
        return c["at"][edge] if c["at"][scrolled] is None else c["at"][scrolled]
    return [c for c in got["controls"] if at(c, "top", "start") < inset["top"] - 0.5
            or at(c, "bottom", "end") > height - inset["bottom"] + 0.5 or at(c, "left", "startX") < inset["left"] - 0.5
            or at(c, "right", "endX") > width - inset["right"] + 0.5]


@pytest.mark.parametrize("held", HELD)
@pytest.mark.parametrize("which", SAFE)
def test_no_control_or_toast_lies_in_a_bar_or_at_a_side_upright_or_on_its_side(strips, which, held):
    got = strips[f"{held}.{which}"]
    assert got["controls"], got                         # the page's controls were found
    assert _out(got) == [], (held, which, got["inset"], _out(got))


# the rule's own probes (layoutHarness.tsx), boxes over the whole screen that scroll with forty rows of buttons: one
# with no padding at its foot, whose last row rests in the home indicator with the box scrolled to its end; and one
# whose rows reach the screen's sides, in the sides a phone on its side leaves, however the box scrolls
def test_the_rule_finds_a_box_that_scrolls_with_no_padding_at_its_foot(strips):
    assert _out(strips["safe.scrollfoot"]) and _out(strips["side.scrollfoot"]), strips["safe.scrollfoot"]


def test_the_rule_finds_a_control_at_a_side_in_a_box_that_scrolls(strips):
    assert _out(strips["side.scrollside"]), strips["side.scrollside"]


def test_the_sheet_shows_its_own_background_under_the_status_bar_and_over_the_home_indicator(strips):
    assert strips["safe.sheet"]["bands"] == {"top": True, "bottom": True}


def test_the_command_palette_stands_clear_of_the_status_bar(strips):
    got = strips["safe.palette"]
    assert got["box"] and got["box"]["top"] >= got["inset"]["top"] - 0.5, got
