"""The ADE app (ui/) keeps the old pages' rules: nothing the server or a person sends becomes HTML, the sign-in token
never rides in a URL, and nothing is fetched from another site."""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.normpath(os.path.join(HERE, "..", "ui"))
SRC = os.path.join(UI, "src")


def _sources():
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for fn in files:
            if fn.endswith((".ts", ".tsx")):
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8") as fh:
                    yield os.path.relpath(path, UI), fh.read()


def test_there_is_an_app_to_check():
    assert any(name.endswith("App.tsx") for name, _ in _sources())


def test_nothing_becomes_html():
    for name, text in _sources():
        for sink in ("dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                     "eval(", "new Function("):
            assert sink not in text, f"{name}: {sink}"


def test_markdown_is_rendered_without_raw_html():
    with open(os.path.join(UI, "package.json"), encoding="utf-8") as fh:
        deps = json.load(fh)
    every = {**deps.get("dependencies", {}), **deps.get("devDependencies", {})}
    assert "rehype-raw" not in every
    for name, text in _sources():
        assert "skipHtml={false}" not in text and "rehypeRaw" not in text, name


def test_the_token_never_rides_in_a_url():
    for name, text in _sources():
        assert not re.search(r"[?&]token=", text), name
        for m in re.finditer(r"wsUrl\(([^)]*)\)", text):
            assert "token" not in m.group(1), f"{name}: {m.group(0)}"


def test_nothing_is_fetched_from_another_site():
    for name, text in _sources():
        assert not re.search(r"fetch\(\s*['\"`]https?://", text), name
        assert not re.search(r"new WebSocket\(\s*['\"`]wss?://", text), name
