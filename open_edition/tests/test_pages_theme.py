"""The older pages (sign-in, invitations, the verifier, the console, the terminal) in the app's own palette: Proofpane's
dark one, or its "bright" one when the person chose light in the app, read before the page shows."""

import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
NODE = shutil.which("node")


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("page", ["index.html", "console.html", "terminal.html"])
def test_each_page_reads_the_theme_in_its_head_before_it_shows(page):
    text = read(page)
    head = text[:text.index("</head>")]
    assert '<link rel="stylesheet" href="style.css">\n<script src="theme.js"></script>' in head


def test_every_colour_is_the_palette_s():
    css = read("style.css")
    palette, rest = css.split('color-scheme: light; }', 1)
    assert ":root {" in palette and ':root[data-theme="bright"] {' in palette
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", rest) == [], "a colour outside the palette"


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's script")
@pytest.mark.parametrize("stored,light,expected", [
    ("light", False, "bright"), ("dark", True, "dark"), ("system", True, "bright"), ("system", False, "dark"),
    (None, True, "dark"), ("throws", True, "dark")])
def test_the_choice_made_in_the_app_is_the_page_s(tmp_path, stored, light, expected):
    probe = tmp_path / "probe.js"
    probe.write_text(
        f"const stored = {json.dumps(stored)};\n"
        "globalThis.localStorage = { getItem: () => { if (stored === 'throws') throw new Error('no'); return stored; } };\n"
        f"globalThis.matchMedia = () => ({{ matches: {json.dumps(light)} }});\n"
        "globalThis.document = { documentElement: { dataset: {} } };\n"
        + read("theme.js") + "\nconsole.log(document.documentElement.dataset.theme);\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected
