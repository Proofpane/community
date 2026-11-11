"""Texts of this tree that speak of the tree itself say what holds once it is published: the signing package (oss/sign)
is in it, and scripts/cjk.sh's first line names the folder that holds no CJK text, which this test reads whole."""

import os
import re

from conftest import ROOT

# the characters scripts/cjk.sh counts as CJK, the same ranges
CJK = re.compile("[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef\U00020000-\U0003ffff]")
SKIPPED = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", "target"}


def _text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_no_text_says_the_signing_package_is_not_yet_published():
    for parts in (("README.md",), ("web", "index.html"), ("ui", "src", "components", "AccountDialog.tsx")):
        text = re.sub(r"\s+", " ", _text(*parts))
        assert "oss/sign" in text and "not yet published" not in text, "/".join(parts)


def test_the_cjk_check_names_the_folder_that_holds_none_and_it_holds_none():
    first = _text("scripts", "cjk.sh").split("\n")[1]
    assert first.startswith("# open_edition/ holds no Chinese or other CJK text"), first
    found = []
    for top, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED)
        for name in sorted(files):
            path = os.path.join(top, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            if CJK.search(text):
                found.append(os.path.relpath(path, ROOT))
    assert found == []
