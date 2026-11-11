"""The diff reader, and the rule that keeps the open edition and the paid backend apart."""

import ast
import os
import sys

import pytest

from conftest import DIFF, ROOT

PRODUCT = os.path.normpath(os.path.join(ROOT, ".."))


def test_a_git_diff_is_read_into_files():
    from proofpane_open import diff
    files = diff.parse(DIFF)
    assert [(f["path"], f["status"], f["additions"]) for f in files] == [
        ("src/app.py", "modified", 1), ("tests/test_app.py", "added", 2)]
    assert files[0]["patch"].startswith("@@ -1,2 +1,2 @@") and files[0]["patch"].endswith("+    return 2")
    assert "+++" not in files[1]["patch"]


def test_renames_deletions_and_crlf():
    from proofpane_open import diff
    text = ("diff --git a/old.py b/new.py\r\nsimilarity index 90%\r\nrename from old.py\r\nrename to new.py\r\n"
            "@@ -1 +1 @@\r\n-a\r\n+b\r\n"
            "diff --git a/gone.txt b/gone.txt\ndeleted file mode 100644\n--- a/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
    files = diff.parse(text)
    assert files[0] == {"path": "new.py", "previous_path": "old.py", "status": "renamed",
                        "patch": "@@ -1 +1 @@\n-a\n+b", "additions": 1}
    assert files[1]["status"] == "removed" and files[1]["additions"] == 0


def test_a_file_without_hunks_has_no_patch():
    from proofpane_open import diff
    files = diff.parse("diff --git a/bin.png b/bin.png\nBinary files a/bin.png and b/bin.png differ\n")
    assert files == [{"path": "bin.png", "previous_path": None, "status": "modified", "patch": None, "additions": 0}]


@pytest.mark.parametrize("text", ["", "\n\n", "hello", "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", None, 5,
                                  "commit 0a1b2c\nAuthor: x\n\n    message\n\n" + DIFF])
def test_what_is_not_a_git_diff_is_refused(text):
    from proofpane_open import diff
    with pytest.raises(diff.DiffError):
        diff.parse(text)


BASE_REFUSAL = "This is not a git diff: it should start with a 'diff --git a/... b/...' line."


@pytest.mark.parametrize("wrap", ["```\n{}```\n", "```diff\n{}```", "~~~~\n{}~~~~\n", "\n\n```patch\n{}\n```\n\n",
                                  "```\n{}", "\ufeff{}", "\ufeff```\n{}```\n"])
def test_a_fence_or_a_byte_order_mark_around_a_diff_is_taken_off(wrap):
    from proofpane_open import diff
    assert diff.parse(wrap.format(DIFF)) == diff.parse(DIFF)


def test_a_closing_fence_must_start_its_line():
    from proofpane_open import diff
    text = "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1,2 +1,2 @@\n-a\n+b\n ```\n"
    for wrapped in ("```\n" + text, "```\n" + text + "```\n"):
        assert diff.parse(wrapped)[0]["patch"] == "@@ -1,2 +1,2 @@\n-a\n+b\n ```"


@pytest.mark.parametrize("text, cause", [
    (" " + DIFF, "line 1 starts with a space, so the diff was indented"),
    ("\t" + DIFF, "line 1 starts with a tab"),
    ("\u00a0" + DIFF, "line 1 starts with a non-breaking space (U+00A0), which copying from a web page"),
    ("\n".join(" " + line for line in DIFF.split("\n")), "line 1 starts with a space"),
    ("   ```\n" + DIFF + "```\n", "line 1 starts with a space"),
    ("\u200b" + DIFF, "line 1 contains U+200B ZERO WIDTH SPACE"),
    ("\n\n" + DIFF.replace("diff --git", "diff\u00a0--git", 1), "line 3 contains a non-breaking space (U+00A0)"),
    (DIFF.replace("--git", "\u2014git", 1), "line 1 has U+2014 EM DASH where git writes two hyphens"),
    (DIFF.replace("--git", "\u2013git", 1), "U+2013 EN DASH"),
    ("D" + DIFF[1:], "line 1 starts with a capital letter"),
    ("\x1b[1m" + DIFF, "line 1 contains a terminal's colour codes (U+001B)"),
])
def test_copy_damage_before_the_first_file_is_refused_with_its_cause(text, cause):
    from proofpane_open import diff
    with pytest.raises(diff.DiffError) as e:
        diff.parse(text)
    assert str(e.value).startswith(BASE_REFUSAL[:-1] + ", and ") and cause in str(e.value)


@pytest.mark.parametrize("text", ["Here it is:\n```\n" + DIFF + "```\n", "hello", "commit 0a1b2c\n\n" + DIFF,
                                  "Title:\tfix a typo\n" + DIFF, "```a`b\n" + DIFF])
def test_text_before_a_diff_is_refused_without_a_cause_it_does_not_have(text):
    from proofpane_open import diff
    with pytest.raises(diff.DiffError) as e:
        diff.parse(text)
    assert str(e.value) == BASE_REFUSAL


@pytest.mark.parametrize("sep, name", [("\r", "a carriage return (U+000D)"), ("\u2028", "U+2028 LINE SEPARATOR"),
                                       ("\u2029", "U+2029 PARAGRAPH SEPARATOR"), ("\x85", "U+0085")])
def test_line_breaks_other_than_newlines_are_refused(sep, name):
    from proofpane_open import diff
    for text in (DIFF.replace("\n", sep), "\n\n" + DIFF.replace("\n", sep) + "\n"):
        with pytest.raises(diff.DiffError) as e:
            diff.parse(text)
        assert f"its lines end with {name}" in str(e.value) and "not a newline" in str(e.value)


@pytest.mark.parametrize("text, message", [
    (DIFF.replace("\n@@ -1,2", "\n\u00a0@@ -1,2", 1),
     "Line 5 is not one git writes before a file's changes: it starts with a non-breaking space (U+00A0)"),
    ("```\n" + DIFF.replace("\n@@ -1,2", "\n\u00a0@@ -1,2", 1) + "```\n",
     "Line 6 is not one git writes before a file's changes: it starts with a non-breaking space (U+00A0)"),
    (DIFF.replace("new file mode", "New file mode", 1), "Line 10 is not one git writes before a file's changes."),
    ("diff --git a/x b/x\nhello\n@@ -1 +1 @@\n-a\n+b\n", "Line 2 is not one git writes before a file's changes."),
    ("diff --git a/x b/x\n```\n@@ -1 +1 @@\n-a\n+b\n",
     "Line 2 is not one git writes before a file's changes: it is a Markdown code fence"),
])
def test_a_line_git_does_not_write_before_a_hunk_is_refused_not_skipped(text, message):
    from proofpane_open import diff
    with pytest.raises(diff.DiffError) as e:
        diff.parse(text)
    assert str(e.value).startswith(message)


def test_other_line_breaks_inside_a_file_s_lines_are_its_content():
    from proofpane_open import diff
    text = "diff --git a/a.js b/a.js\n--- a/a.js\n+++ b/a.js\n@@ -1 +1 @@\n-x\f\n+var s = '\u2028\r';\n"
    assert diff.parse(text)[0]["patch"] == "@@ -1 +1 @@\n-x\f\n+var s = '\u2028\r';"


@pytest.mark.parametrize("text, message", [
    (DIFF.replace("\ndiff --git a/tests", "\n\u00a0diff --git a/tests", 1),
     "Line 9 is not one git writes in a file's changes: it starts with a non-breaking space (U+00A0)"),
    (DIFF.replace("\ndiff --git a/tests", "\nDiff --git a/tests", 1),
     "Line 9 is not one git writes in a file's changes: it starts with a capital letter"),
    (DIFF + "Let me know what you think.\n", "Line 16 is not one git writes in a file's changes."),
    (DIFF + "```\n", "Line 16 is not one git writes in a file's changes: it is a Markdown code fence"),
    ("```\n" + DIFF + "```python\n", "Line 17 is not one git writes in a file's changes: it is a Markdown code fence"),
])
def test_a_line_git_does_not_write_in_a_hunk_is_refused_not_read_as_the_file_s(text, message):
    from proofpane_open import diff
    with pytest.raises(diff.DiffError) as e:
        diff.parse(text)
    assert str(e.value).startswith(message)


def test_the_lines_git_writes_in_a_hunk_are_read():
    from proofpane_open import diff
    text = ("diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n x\n\n-y\n+\ufeffz\n"
            "\\ No newline at end of file\n@@ -9 +9 @@\n-p\n+q\n")
    assert diff.parse(text)[0]["patch"] == ("@@ -1,3 +1,3 @@\n x\n\n-y\n+\ufeffz\n\\ No newline at end of file\n"
                                            "@@ -9 +9 @@\n-p\n+q")


def test_every_line_git_writes_before_a_hunk_is_read():
    from proofpane_open import diff
    text = ("diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
            "diff --git a/a.txt b/b.txt\nsimilarity index 100%\ncopy from a.txt\ncopy to b.txt\n"
            "diff --git a/c.txt b/c.txt\ndissimilarity index 80%\nindex 1111111..2222222 100644\n--- a/c.txt\t\n"
            "+++ b/c.txt\t\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --git a/img.png b/img.png\nindex 1111111..2222222 100644\nGIT binary patch\nliteral 5\n"
            "McmZ?wbhEHbGn\n\nliteral 0\nHcmV?d00001\n\n"
            "diff --git a/z.txt b/z.txt\nindex 3333333..4444444 100644\n--- a/z.txt\n+++ b/z.txt\n@@ -1 +1 @@\n-x\n+y\n")
    files = diff.parse(text)
    assert [(f["path"], f["patch"], f["additions"]) for f in files] == [
        ("run.sh", None, 0), ("b.txt", None, 0), ("c.txt", "@@ -1 +1 @@\n-a\n+b", 1), ("img.png", None, 0),
        ("z.txt", "@@ -1 +1 @@\n-x\n+y", 1)]


def test_the_reader_s_own_source_holds_no_character_it_warns_about():
    # the escapes, not the characters: once, all eleven `\u` escapes in it were written as the characters themselves,
    # four that cannot be seen and seven that look like a hyphen
    with open(os.path.join(ROOT, "proofpane_open", "diff.py"), encoding="utf-8") as fh:
        assert fh.read().isascii()


def test_the_diff_box_is_not_changed_by_a_phone():
    with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as fh:
        page = fh.read()
    box = next(tag for tag in page.split("<") if tag.startswith('textarea name="diff"'))
    for attribute in ('autocapitalize="off"', 'autocorrect="off"', 'spellcheck="false"'):
        assert attribute in box


def _imports(top):
    for root, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".venv", "venv", "__pycache__", ".next")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read(), path)
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    yield os.path.relpath(path, PRODUCT), name.split(".")[0]


OPEN_PACKAGES = {"proofpane_open", "proofpane_core", "proofpane_verify", "proofpane_sign", "verify_pr"}


def test_the_open_edition_imports_nothing_from_the_paid_backend():
    found = [(p, n) for p, n in _imports(ROOT) if n in ("app", "backend")]
    assert found == []


def test_the_paid_backend_imports_nothing_from_the_open_edition_or_the_open_packages():
    # the enterprise build never depends on the open packages
    backend = os.path.join(PRODUCT, "backend", "app")
    if not os.path.isdir(backend):
        pytest.skip("no backend/ beside open_edition/: this tree does not hold the paid backend")
    found = [(p, n) for p, n in _imports(backend) if n in OPEN_PACKAGES]
    assert found == []


def test_the_backend_walk_is_skipped_where_this_tree_has_no_backend(monkeypatch, tmp_path):
    """Where no backend/ is beside open_edition/ (the published tree holds none), the walk above skips and says
    why: a walk of a missing folder reads nothing and would pass."""
    monkeypatch.setattr(sys.modules[__name__], "PRODUCT", str(tmp_path))
    with pytest.raises(pytest.skip.Exception):
        test_the_paid_backend_imports_nothing_from_the_open_edition_or_the_open_packages()


def test_the_boundary_scanner_can_fail(tmp_path):
    (tmp_path / "x.py").write_text("import app.main\nfrom proofpane_sign import composite\nfrom . import y\n")
    names = sorted(n for _, n in _imports(str(tmp_path)))
    assert names == ["app", "proofpane_sign"]


def test_the_page_script_never_writes_html():
    # every value from the server or the user goes in with textContent; a later edit must not bring innerHTML back
    with open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in script, sink
    with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as fh:
        page = fh.read()
    assert "<script>" not in page and "http://" not in page and "https://" not in page  # one local script, nothing fetched


def test_the_command_line_prints_a_working_invitation(capsys, client):
    from proofpane_open.__main__ import main
    assert main(["invite", "first@example.org"]) == 0
    link = capsys.readouterr().out.strip()
    assert link.startswith("https://open.example.test/#invite=")
    token = link.split("#invite=")[1]
    assert client.post("/api/auth/invitation", json={"token": token}).json() == {"email": "first@example.org"}
    assert main(["invite", "not an email"]) == 1
    assert main(["public-key"]) == 0
    assert '"id-MLDSA65-Ed25519-SHA512"' in capsys.readouterr().out


def test_a_file_s_name_is_read_back_as_git_wrote_it_quoted_or_with_spaces():
    from proofpane_open import diff
    lines = [
        "diff --git a/a b.txt b/a b.txt", "new file mode 100644", "--- /dev/null", "+++ b/a b.txt\t", "@@ -0,0 +1 @@", "+x",
        r'diff --git "a/q\"x.txt" "b/q\"x.txt"', "new file mode 100644", "--- /dev/null", r'+++ "b/q\"x.txt"',
        "@@ -0,0 +1 @@", "+x",
        r'diff --git "a/r\303\251sum\303\251.md" "b/r\303\251sum\303\251.md"', "deleted file mode 100644",
        r'--- "a/r\303\251sum\303\251.md"', "+++ /dev/null", "@@ -1 +0,0 @@", "-x",
        r'diff --git "a/tab\there.txt" "b/tab\there.txt"', "new file mode 100644", "--- /dev/null",
        r'+++ "b/tab\there.txt"', "@@ -0,0 +1 @@", "+x",
        "diff --git a/x b/y b/x b/y", "old mode 100644", "new mode 100755",        # " b/" in a name, not renamed
        "diff --git a/a b.txt b/moved b.txt", "similarity index 100%", "rename from a b.txt", "rename to moved b.txt",
        r'diff --git "a/q\"x.txt" b/plain.txt', "similarity index 100%", r'rename from "q\"x.txt"', "rename to plain.txt",
    ]
    got = [(f["previous_path"], f["path"], f["status"]) for f in diff.parse("\n".join(lines) + "\n")]
    assert got == [(None, "a b.txt", "added"), (None, 'q"x.txt', "added"), (None, "r\xe9sum\xe9.md", "removed"),
                   (None, "tab\there.txt", "added"), (None, "x b/y", "modified"),
                   ("a b.txt", "moved b.txt", "renamed"), ('q"x.txt', "plain.txt", "renamed")]
    # a quote git never closed, or an escape it never writes, is not a header
    for bad in (r'diff --git "a/x "b/x"', r'diff --git "a/\q" "b/\q"', 'diff --git "a/x" c/x'):
        with pytest.raises(diff.DiffError):
            diff.parse(bad + "\n@@ -1 +1 @@\n+x\n")
