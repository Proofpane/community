"""The slash menu at a phone's width: the chat box's controls sit on one line
at a phone's width, 360 and 390 pixels, in the workspace's chat (its settings and its message box) and in a linked
session's box, nothing in the row but its controls, none cut off past the row's edge or the screen's and none squeezed
below a tap's width; and in each box at those widths the slash menu opens on a tap of its button, an entry is a tap, the
entry's modal sits within the screen with nothing to scroll sideways, and a tap outside closes it (these cases cover
the button at phone width). Measured in a headless Chromium that takes taps:
ui/scripts/measure-layout.mjs builds ui/layout.html, which draws the boxes as the app draws them with fixed data and no
server, and prints what each row and each modal measures.

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


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    out = tmp_path_factory.mktemp("layout")
    run = subprocess.run([NODE, "scripts/measure-layout.mjs", PLAYWRIGHT, CHROMIUM, str(out)], cwd=UI,
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("width", [360, 390])
@pytest.mark.parametrize("row", ["chat.settings", "chat.input", "chatbusy.input", "link.controls", "card.controls"])
def test_a_chat_boxs_controls_sit_on_one_line_at_a_phones_width(rows, row, width):
    got = rows[f"{row}.{width}"]
    assert got["height"] <= got["tallest"] + 1, got     # one line
    assert got["others"] == 0, got                      # nothing in it but controls: a note goes under it
    assert got["right"] <= got["edge"] + 1, got         # none past the row's edge or the screen's, cut off
    assert got["narrowest"] >= 24, got                  # none squeezed below a tap's width


@pytest.mark.parametrize("width", [360, 390])
def test_a_busy_chats_row_is_measured_with_stop_and_the_microphone_in_it(rows, width):
    assert rows[f"chatbusy.buttons.{width}"] == {"stop": 1, "mic": 1}


@pytest.mark.parametrize("width", [360, 390])
def test_the_settings_buttons_keep_their_names_where_only_their_icons_show(rows, width):
    assert rows[f"chat.named.{width}"] == {"agents": 1, "connectors": 1}


def test_at_a_desktops_width_the_settings_row_in_a_narrow_pane_wraps_rather_than_running_past_it(rows):
    got = rows["pane.settings.1280"]
    assert got["others"] == 0, got
    assert got["right"] <= got["edge"] + 1, got
    assert got["narrowest"] >= 24, got


@pytest.mark.parametrize("width", [360, 390])
@pytest.mark.parametrize("entry", ["account", "lanes", "map"])
@pytest.mark.parametrize("box", ["chat", "link"])
def test_the_slash_menu_is_a_tap_at_a_phones_width_and_its_modal_reads_there(rows, box, entry, width):
    got = rows[f"{box}.menu.{entry}.{width}"]
    assert got.get("button"), got                       # the slash button beside the box
    assert got.get("option") and got.get("dialog"), got  # its menu on a tap, and the entry's modal on a tap
    assert got["left"] >= 0 and got["right"] <= got["screen"], got    # the modal within the screen
    assert got["sideways"] <= 1, got                    # its words wrapped: nothing to scroll sideways
    assert got["closed"], got                           # a tap outside closes it
