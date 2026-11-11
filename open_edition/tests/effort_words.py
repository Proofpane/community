"""What claude says at a start, read a line at a time, for the Docker case of an ask's effort
(tests/test_workspace_image_docker.py's
test_the_image_s_claude_takes_an_ask_s_effort_at_each_level_with_the_flags_a_start_gives), which cannot run without
Docker and the built image; tests/test_effort_words.py checks these on made-up words, where Docker is not. Standard
library only.

The case starts the image's claude at each level it takes, three times each, once without --effort and once at a level
it does not know (UNKNOWN). Its lines are compared with every level's name the case knows, UNKNOWN's among them,
written as one marker (MARK), so that two lines that differ only in which levels they name read the same. The warning is
the lines of the start at UNKNOWN that name UNKNOWN itself, where its name stands as a word, but those the start without
--effort says and those that every level's own first start says: an echo naming a level, its own or another (a default
claude reports, a level it maps another to), said at every start, is not the warning (with the start's own level
alone as the marker, such an echo would read differently at each level and be taken for the warning); nor is a line
of that start that does not name UNKNOWN, though it names another level, a notice naming one or a warning that lists the
levels without echoing the one given (were any line naming a level taken, such a line would pass the case on a claude
that ignores levels it does not know, and could fail a level it takes). A start at a level fails when it says a line of
the warning that names that level itself, as a word: claude saying of the level what it says of one it does not know.
What the reading cannot tell apart: a line said in the same words at some levels' starts and at UNKNOWN's, but not at
every level's first start, an echo left out at claude's default level for one, is a line of the warning, and those
levels fail, the case then failing loudly with what each start said printed, for a person to read."""

import functools
import re

# the level a start at a level claude does not know is given
UNKNOWN = "notalevel"
# what a level's name is written as in a line: built of NUL characters, which words() takes out of claude's own words
# before it writes a level's name so, so the marker cannot be claude's own (a plain capitalised word could be one of
# claude's own words, in a variable's name)
MARK = "\x00LEVEL\x00"
_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@functools.lru_cache(maxsize=None)
def _names(names):
    """A pattern for each name of `names` (a tuple) where it stands as a word, the longest tried first, so that a name
    inside another word (`max` in `maximum`, `high` in `xhigh`) is not taken for it."""
    return re.compile(r"\b(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")


def _line(raw):
    """A line of what claude said, as the reading compares it before any level's name is written as MARK: its NUL
    characters taken out (so the marker cannot be claude's own), stripped, and a session's id written as ID; None for an
    empty line or claude's JSON result."""
    line = raw.replace("\x00", "").strip()
    if not line or line.startswith("{"):
        return None
    return _SESSION_ID.sub("ID", line)


def _marked(line, names):
    """`line` (from _line) with each name of `names` where it stands as a word written as MARK, then every number as
    0."""
    if names:
        line = _names(tuple(sorted(set(names)))).sub(MARK, line)
    return re.sub(r"\d+", "0", line)


def words(said, levels=None):
    """What claude said beside its JSON result (`said`: its two streams, joined), a line at a time, each line stripped
    and its NUL characters taken out: a session's id written as ID, the name of each level in `levels` (one name, or
    several) where it stands as a word as MARK, and every number as 0, so that two starts that differ in those alone say
    the same."""
    names = (levels,) if isinstance(levels, str) else tuple(levels or ())
    return {_marked(line, names) for line in map(_line, said.splitlines()) if line is not None}


def warning(unknown, plain, firsts):
    """The warning claude gives at a level it does not know, every level's name the case knows written as MARK
    (`firsts`' levels and UNKNOWN): the lines its start at UNKNOWN said (`unknown`) that name UNKNOWN itself, where its
    name stands as a word, that the same start without
    --effort (`plain`) does not say, and that not every level's own first start says (`firsts`, {level: what its first
    start said}), so that an echo naming a level, its own or another, said at every start is not taken for it."""
    names = set(firsts) | {UNKNOWN}
    named = _names((UNKNOWN,))
    found = {_marked(line, names) for line in map(_line, unknown.splitlines())
             if line is not None and named.search(line)} - words(plain, names)
    every = None
    for said in firsts.values():
        mine = words(said, names)
        every = mine if every is None else every & mine
    return found - (every or set())


def said_as_unknown(warned, level, said, names):
    """The lines a start at `level` said (`said`) that are lines of the warning (`warned`) once each name of `names` is
    written as MARK, as warning wrote it, and that name `level` itself, where its name stands as a word (so that a line
    naming xhigh is not said of high, nor one holding "allowed" of low): claude saying
    of that level what it says of one it does not know. Each as the start said it, but for a session's id and its
    numbers, as words writes them."""
    own = _names((level,))
    return sorted({_marked(line, ()) for line in map(_line, said.splitlines())
                   if line is not None and own.search(line) and _marked(line, names) in warned})


def failures(warned, runs):
    """[(level, start, the lines of the warning it said)] for each start of each level in `runs` ({level: {start: what
    it said}}) that said any line of the warning of its own level, the levels of `runs` and UNKNOWN written as MARK, as
    warning writes them for the same levels: none when claude took every level."""
    names = set(runs) | {UNKNOWN}
    out = []
    for level, starts in runs.items():
        for start, said in starts.items():
            got = said_as_unknown(warned, level, said, names)
            if got:
                out.append((level, start, got))
    return out
