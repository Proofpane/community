"""A unified diff, as `git diff` prints it, read into the file entries a Verify bundle lists.

Each file becomes {path, previous_path, status, patch, additions}. The patch is the file's hunks, from the first
`@@`, as GitHub's API gives it; `additions` counts added lines. Text that is not a `git diff` is refused rather than
guessed at, and the refusal names what is wrong with the line it stopped at: a diff copied from a chat or a web page
can arrive indented, with a non-breaking or invisible character in it, with a capital letter or a dash a phone put
there, or with line breaks that are not newlines. Git writes none of these.

Two things are removed without asking, because neither can be part of a diff: a byte order mark at the very start,
and a Markdown code fence around the whole text. Nothing else is changed. Every other line must be one git writes
where it stands: between a file's `diff --git` line and its first hunk, one of git's header lines; in a hunk, a line
that starts with a space, `+`, `-`, a backslash or `@@`, or an empty one. Before that was checked, damage in copying
changed what was read without a word: a hunk header with a non-breaking space before it made a file's changes
disappear, and a damaged `diff --git` line made its file part of the file before it, so a diff that changed code
could be read as changing only a README.
"""

import re
import unicodedata

_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
# the rest of what git writes between `diff --git` and a file's first hunk (git-diff(1), "extended header lines",
# less the four `parse` reads: new and deleted file mode, rename from and to), and the line that stands in for hunks
# when a binary file changed
_FILE_HEADER = re.compile(
    r"^(?:old mode |new mode |copy from |copy to |similarity index |dissimilarity index |index |--- |\+\+\+ "
    r"|Binary files )")
_BINARY_PATCH = "GIT binary patch"  # with --binary: data lines follow, up to the next file
_FENCE = re.compile(r"^(`{3,}|~{3,})[^`]*$")
_CLOSING_FENCE = re.compile(r"^(`{3,}|~{3,})$")
# line breaks other than "\n" that str.splitlines knows; git ends every line it writes with "\n"
_OTHER_BREAKS = "\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_NAMES = {" ": "a space", "\t": "a tab", "\r": "a carriage return (U+000D)", "\u00a0": "a non-breaking space (U+00A0)"}


class DiffError(ValueError):
    pass


def parse(text):
    if not isinstance(text, str):
        raise DiffError("The diff must be text.")
    text = text.replace("\r\n", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    inner = text.strip()
    if "\n" not in inner:
        odd = next((c for c in inner if c in _OTHER_BREAKS), None)
        if odd is not None:
            raise DiffError(f"This is not a git diff as git writes it: its lines end with {_name(odd)}, not a "
                            "newline. Copy the diff again.")
    files = []
    current = None
    in_hunks = in_binary = False
    for number, line in _numbered(text):
        paths = _header_paths(line)
        if paths:
            old, new = paths
            current = {"path": new, "previous_path": None, "status": "modified", "patch": [], "additions": 0}
            files.append(current)
            if old != new:
                current["previous_path"], current["status"] = old, "renamed"
            in_hunks = in_binary = False
            continue
        if current is None:
            if line.strip():
                reason = _describe(line)
                raise DiffError("This is not a git diff: it should start with a 'diff --git a/... b/...' line" +
                                (f", and line {number} {reason}." if reason else "."))
            continue
        if in_binary:
            continue
        if not in_hunks:
            if line.startswith("@@"):
                in_hunks = True
                current["patch"].append(line)
            elif line.startswith("new file mode"):
                current["status"] = "added"
            elif line.startswith("deleted file mode"):
                current["status"] = "removed"
            elif line.startswith("rename from "):
                current["previous_path"], current["status"] = _unquoted(line[len("rename from "):]), "renamed"
            elif line.startswith("rename to "):
                current["path"] = _unquoted(line[len("rename to "):])
            elif line == _BINARY_PATCH:
                in_binary = True
            elif line.strip() and not _FILE_HEADER.match(line):
                reason = _describe(line)
                raise DiffError(f"Line {number} is not one git writes before a file's changes" +
                                (f": it {reason}." if reason else "."))
            continue
        if line and line[0] not in " +-\\" and not line.startswith("@@"):
            reason = _describe(line)
            raise DiffError(f"Line {number} is not one git writes in a file's changes" +
                            (f": it {reason}." if reason else "."))
        current["patch"].append(line)
        if line.startswith("+"):
            current["additions"] += 1
    if not files:
        raise DiffError("The diff is empty.")
    for f in files:
        while f["patch"] and f["patch"][-1] == "":
            f["patch"].pop()
        f["patch"] = "\n".join(f["patch"]) if f["patch"] else None
    return files


def _quoted(text, start):
    """A name git wrote in double quotes, from text[start] (the opening quote), with its C escapes (\\" \\\\ \\t, and
    \\ooo for each byte of a character git quotes) read back: (the name, where the text goes on after it), or None
    when it is not one."""
    if text[start:start + 1] != '"':
        return None
    out, i = bytearray(), start + 1
    while i < len(text):
        c = text[i]
        if c == '"':
            return out.decode("utf-8", "replace"), i + 1
        if c != "\\":
            out += c.encode("utf-8", "surrogatepass")
            i += 1
            continue
        e = text[i + 1:i + 2]
        if e in _C_ESCAPES:
            out.append(_C_ESCAPES[e])
            i += 2
        elif len(text) >= i + 4 and all(d in "01234567" for d in text[i + 1:i + 4]) and int(text[i + 1:i + 4], 8) < 256:
            out.append(int(text[i + 1:i + 4], 8))
            i += 4
        else:
            return None
    return None


def _unquoted(name):
    """A name from a rename line or a header, as it is, or read back from git's quotes when it is in them."""
    got = _quoted(name, 0)
    return got[0] if got and got[1] == len(name) else name


def _header_paths(line):
    """The two paths of a file's `diff --git` line: (as it was, as it is), each without its a/ or b/; None for any
    other line. Git puts a name in quotes when it holds a quote, a backslash or a control character (and, unless
    core.quotepath is off, any character past ASCII); one that holds " b/" is read as git wrote it when both names
    are the same, as they are for any file not renamed (a renamed one's names are on its rename lines too)."""
    if not line.startswith("diff --git "):
        return None
    rest = line[len("diff --git "):]
    first = _quoted(rest, 0)
    if first:
        old, at = first
        if rest[at:at + 1] != " ":
            return None
        second = _quoted(rest, at + 1)
        if second and second[1] == len(rest):
            new = second[0]
        elif not second and rest.startswith("b/", at + 1):
            new = rest[at + 1:]
        else:
            return None
    elif rest.endswith('"') and ' "b/' in rest:
        at = rest.rindex(' "b/')
        second = _quoted(rest, at + 1)
        if not second or second[1] != len(rest):
            return None
        old, new = rest[:at], second[0]
    else:
        half = (len(rest) - 1) // 2
        if len(rest) % 2 == 1 and rest[half] == " " and rest[:half][2:] == rest[half + 1:][2:]:
            old, new = rest[:half], rest[half + 1:]
        else:
            m = _HEADER.match(line)
            if not m:
                return None
            return m.group(1), m.group(2)
    if not old.startswith("a/") or not new.startswith("b/") or len(old) < 3 or len(new) < 3:
        return None
    return old[2:], new[2:]


def _numbered(text):
    """The text's lines, numbered from 1, without a Markdown code fence that opens the text and one that closes it.
    A closing fence starts its line: a context line that shows a fence has a space before it, and git writes no line
    that starts with a backtick or a tilde."""
    lines = list(enumerate(text.split("\n"), 1))
    filled = [i for i, (_, line) in enumerate(lines) if line.strip()]
    if not filled:
        return lines
    opening = _FENCE.match(lines[filled[0]][1].rstrip())
    if not opening:
        return lines
    end = len(lines)
    last = filled[-1]
    if last != filled[0] and _CLOSING_FENCE.match(lines[last][1].rstrip()):
        end = last
    return lines[filled[0] + 1:end]


def _describe(line):
    """What in a refused line git would not have written, as the rest of a sentence, or "" when nothing stands out.
    Nothing of the line itself is quoted: it may hold what the diff should not show."""
    if line[:1].isspace():
        if line[0] in " \t":
            return (f"starts with {_name(line[0])}, so the diff was indented when it was copied: paste it without "
                    "the indent")
        return (f"starts with {_name(line[0])}, which copying from a web page or a chat can add: copy the diff "
                "again, with a copy button if there is one")
    for c in line:
        if c == "\x1b":
            return "contains a terminal's colour codes (U+001B): run git diff with --no-color, or copy it again"
        if c in _DASHES:
            return (f"has {_name(c)} where git writes two hyphens, which a phone does to typed text: paste the "
                    "diff instead of typing it")
        if c not in " \t" and (c.isspace() or unicodedata.category(c) in ("Cc", "Cf")):
            return (f"contains {_name(c)}, which copying from a web page or a chat can add: copy the diff again, "
                    "with a copy button if there is one")
    if line[:4].lower() == "diff" and line[:4] != "diff":
        return "starts with a capital letter, which a phone does to typed text: paste the diff instead of typing it"
    if _FENCE.match(line.strip()):
        return "is a Markdown code fence: paste only the lines inside it"
    return ""


def _name(c):
    if c in _NAMES:
        return _NAMES[c]
    return "U+%04X %s" % (ord(c), unicodedata.name(c, "a control character"))
