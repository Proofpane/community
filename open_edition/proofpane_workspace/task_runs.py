"""A task's attempts as the workspace runs them:
the app queues an attempt as an agent's run whose trigger is
{kind: task, id: the task's id} with the attempt message beside it, and the workspace
runs it with task_attempt.Attempt in place of the run's chat, in the task's own worktree.

**Where a task lives.** agents/<agent>/tasks/<task>/: its worktree (`work`, on the branch pp/task-<task>, cut at its
first attempt from the agent's repository, or at a teammate's acceptance from the offered commit: its checkpoint 0),
and its record (task.json), signed with the records' key as a run's record is, holding what the workspace alone
knows of the task between attempts: its checkpoint 0, the places its watch looks at and how they were at the last
attempt's end (or the task's start), the pushes it was asked to make, and the head an owner's keep or discard left.
What an attempt did (its fence, checkpoints, effects, end state) is in its run's signed record, which the app copies;
the next attempt's stale fence and its checkpoints so far are read from those records.

**The watch between attempts.** The places a task's attempt is watched at:
git's own places for the task's worktree (its .git link, the repository's config, hooks and info
folder, the worktree's own record in the repository, the user's git settings and every file they include, and the
file core.excludesFile names), the programs' places an agent's run is watched at but the files the workspace writes
itself (the chats' wrappers, the update's record, the accounts' record, the agents' records, which change between
attempts by the workspace's own hand), each Claude Code account's .claude.json by its MCP servers and its identity,
the instruction files (CLAUDE.md, CLAUDE.local.md, AGENTS.md) in every folder above the worktree, and the files outside
the worktree that the instruction files a next run loads import with @path, for up to four hops. They are kept
with how they were, so that a place added later (an account added between attempts) is taken as it is when first seen,
and none counts as a change only because the list grew. The look at each attempt's end becomes the next one's
baseline, whatever that attempt's end was: a change it saw has put the task before the owner already. An attempt
that ended before its own look at its start (check 4) keeps the baseline as its start's look found it, so a change
after that look, its precheck's among them, is found by the next one. An owner's route that keeps a look (a return
taken, the worktree kept, whatever the keep commits, or dropped) keeps the look its own watch compared before its git
ran. Every baseline is a look something compared, never one taken after it (_keep_seen).

**Which claude.** A task's attempt runs the image's own claude (the workspace's claude_floor, at
task_shapes.TASK_CLAUDE_VERSION), never the newer build the updater keeps in use for the agent's other runs; under the
local provider, the Agent SDK's bundled one at the workspace's pin, never the PATH's. PP_TASK_CLAUDE in the
workspace agent's own environment replaces it, for the tests' stand-in alone; nothing a session or a run does reaches
that environment.

**Its environment and settings.** As an agent's run's (the account's folder, the run's folders and mark, PP_REPLAY
where it applies), with task_attempt.ATTEMPT_ENV and WORKSPACE_ENV in it and in its own --settings env, and the agent's
author for its commits (PP_AGENT_AUTHOR_NAME and PP_AGENT_AUTHOR_EMAIL). Its --settings are the workspace's own claude
settings (under the local provider, the gate in exec form) with that env; for a key, the key's helper
(providers.helper_command, the key never in an environment) and its provider's address pinned, Anthropic's
https://api.anthropic.com for an Anthropic key.

**The routes' work**: the backends a task can run on, an account's identity read by a
frozen start, a task's attempts, the owner's keep or discard of a worktree, a task's removal, a hand-off's push of a
recorded commit by its hash after a secret-shape scan, a fetch for an offer's view, an acceptance or a return, and the
owner's checks on a returned commit. Every git the workspace runs for a task runs after the watched places were looked
at and found as they were (a change: 409, and no git), with RUN_GIT's pins."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import select
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from . import agents as agentry, budget_check, processes, providers, task_attempt as attempts
from . import task_shapes as shapes, watch

TASK_FILE = "task.json"             # beside a task's run: the attempt message as the app sent it (agents.KEPT_FILES)
RECORD = "task.json"                # in a task's folder: its own record, signed
MESSAGE_KEYS = ("id", "step", "attempt", "fence", "backend", "identity", "chosen_by", "reserve", "cap_usd", "check",
                "protected", "external", "repo_slug", "repo_address", "packet_b64", "packet_digest")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}$")
_WORD = re.compile(r"^[a-z_]{1,24}$")
MAX_CHECK = 2000                    # a step's check, as the app keeps it
MAX_ADDRESS = 500
ANTHROPIC = "https://api.anthropic.com"
# the files the workspace itself writes between a task's attempts, which a task's watch leaves out (an agent's run's
# watch, which looks only during the run, keeps them)
WRITTEN_HERE = ("chat-claude", "caps-claude", "check-claude", "claude-update.json", "agent-accounts.json")
# the parts of an account's .claude.json a task's watch reads: its MCP servers (as an agent's run's watch reads them),
# and the identity it is signed in as
IDENTITY_KEYS = (("oauthAccount",), ("userID",))
# the most a read of one account's identity waits for another read of the same account (`TaskRuns.identity`): the 60
# seconds task_attempt.read_identity gives its frozen start (a test holds the two equal), a step of the read and not its
# whole deadline (a read can last 30 + 60 + 60 + 60 seconds, and the SDK's close besides), since a read that has not
# ended by then is slow or hung; and so far inside the app's deadline for the call (proofpane_open/ade/tasks.py,
# READ_DEADLINE, 120 seconds) that the refusal reaches the page, and a read that waited has time left to read in the
# usual case
IDENTITY_WAIT = 60
INSTRUCTIONS = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
IMPORT_WALK = 100000                # a worktree's entries looked through for its instruction files' imports, at most
PURPOSES = ("view", "accept", "return")
FETCHED = "refs/proofpane/fetched/"
# an acceptance into a repository whose origin is not the offer's address (_origin_or_409): the app's own words for an
# agent on another repository (task_offers._receiving), which it compares by name
NOT_THE_TASKS = "That agent's repository is not this task's."
# the branch a push may name besides the task's own: another task's, where a received task's work goes to the owner's
# task's branch (what the teammate's side pushes)
TASK_REF = re.compile(r"refs/heads/pp/task-[0-9a-f]{32}")
_STEP_NUMBER = re.compile(r"[1-9][0-9]{0,3}")       # a step's number as a JSON object's key (the checks' `checks`)
# the session channel's secret shapes (proofpane_open/ade/link.py, SECRET_SHAPES), which the workspace cannot import:
# tests/test_task_attempt.py holds the two the same
SECRET_SHAPES = (
    ("an Anthropic key (sk-ant-)", re.compile(r"\bsk-ant-", re.ASCII)),
    ("a key (sk- and 16 or more characters)", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}", re.ASCII)),
    ("a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)", re.compile(r"\bgh[pousr]_", re.ASCII)),
    ("a GitHub token (github_pat_)", re.compile(r"\bgithub_pat_", re.ASCII)),
    ("a Fly token (FlyV1)", re.compile(r"\bFlyV1 ", re.ASCII)),
    ("an AWS key (AKIA)", re.compile(r"\bAKIA", re.ASCII)),
    ("a private key (-----BEGIN ... PRIVATE KEY)", re.compile(r"-----BEGIN[^\n]*PRIVATE KEY", re.ASCII)),
    ("an ADE hook token (adeh_)", re.compile(r"\badeh_", re.ASCII)),
    ("an ADE ticket (PP-)", re.compile(r"\bPP-[A-Z2-7]{8}\b", re.ASCII | re.IGNORECASE)),
    ("a Stripe key (sk_live_, rk_live_)", re.compile(r"\b[rs]k_live_", re.ASCII)),
    ("a Slack token (xoxb-, xoxp-, ...)", re.compile(r"\bxox[abprs]-", re.ASCII)),
    ("a Google API key (AIza)", re.compile(r"\bAIza[A-Za-z0-9_-]{30}", re.ASCII)),
    ("a GitLab token (glpat-)", re.compile(r"\bglpat-", re.ASCII)),
    ("an npm token (npm_)", re.compile(r"\bnpm_[A-Za-z0-9]{36}", re.ASCII)),
    ("a signed web token (eyJ...)", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.", re.ASCII)),
)
MAX_SCAN = 64 * 1024 * 1024         # of each of a push's scan's git commands' output, read at most so much and one
                                    # byte, which ends the command (_git_output)
SCAN_SECONDS = 120                  # a push's scan, its git commands together
_READ = 64 * 1024                   # one read of a scan's git command's output, at most: a pipe's whole buffer
_PIPE_PIECE = getattr(select, "PIPE_BUF", 512)   # one write of a git command's input: what a pipe takes whole
_SELECTOR = getattr(selectors, "PollSelector", selectors.SelectSelector)  # as subprocess's own: no FD_SETSIZE limit
FINDINGS_MAX = 20                   # the findings a push's scan reports at most, of those not lifted
LIFTED_MAX = 1000                   # the lifted findings a push names at most (the app's task_offers.LIFTS_PASSED):
                                    # 1,000 ids of 32 hex fit a request body (agent.MAX_BODY, 64 KiB) with room left
_FINDING = re.compile(r"[0-9a-f]{32}")
_KEY_HEX = re.compile(r"[0-9a-f]{64}")
_OBJECT = re.compile(rb"[0-9a-f]{40}")
# a hunk's header with --unified=0: where the new file's lines it holds begin (a second "@@" ends it, then any words)
_HUNK = re.compile(r"@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,[0-9]+)? @@")
# what holds a push that is no finding: the scan did not read it, so it has no id and is never lifted
UNREAD = "a change the scan could not read"
TOO_LARGE = "a change too large to scan"
BINARY = "a file the scan could not read"
_SCAN_KEY = os.urandom(32)          # a scan given no key (the tests' own calls): its ids are this process's alone


# the two shapes whose patterns try each place they could begin at on to its line's end, so that one long line of such
# places took a time that grows as its length squared (81 s for one 1 MiB
# line of "-eyJ" and ten letters, 2.18 s for 200 KiB of "-----BEGIN "): _Pem and _Jwt read them in linear time
_PEM = r"-----BEGIN[^\n]*PRIVATE KEY"
_JWT = r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\."
# ASCII white space: where a value glued to one the scan found ends (secret_matches)
_SPACE = re.compile(r"[ \t\n\r\f\v]")
# a signed web token's parts are of these characters: a run of them, and the last character before a place that is none
# of them, after which a run of them ending at that place begins
_TOKEN_RUN = re.compile(r"[A-Za-z0-9_-]*")
_RUN_BEGINS = re.compile(r"[^A-Za-z0-9_-](?=[A-Za-z0-9_-]*\Z)")
_EVERY = 1024                       # a long reading looks at the scan's time once in so many of its steps


class _OutOfTime(Exception):
    """A scan's time ran out while it read a text (secret_matches, given a deadline)."""


class _Pattern:
    """A shape that its own pattern reads in time linear in the text (one of a fixed length; one that takes a class 16
    or more times is read as one that takes it 16 times, which begins where it does): each place it begins at from
    `pos` on, before `end`, where the text read in place ends (its line's "\\n", or the text's end)."""

    def __init__(self, pattern, text, end):
        self.pattern, self.text, self.end = pattern, text, end

    def first(self, pos):
        m = self.pattern.search(self.text, pos, self.end)
        return (m.start(), m.end()) if m else None


class _Pem:
    """-----BEGIN[^\\n]*PRIVATE KEY, read once for each line of the text rather than once for each place it could begin
    at: a place counts where "PRIVATE KEY" stands ten or more characters on from it before the line's end, and its
    match ends after the last of those, as the pattern's [^\\n]* takes it."""

    def __init__(self, text, end, deadline):
        self.text, self.end, self.deadline = text, end, deadline
        self.at, self.stop, self.last = 0, -1, -1       # a place looked at, its line's end, that line's last "PRIVATE KEY"

    def first(self, pos):
        text, end, steps = self.text, self.end, 0
        while pos < end:
            at = text.find("-----BEGIN", pos, end)
            if at < 0:
                return None
            if not self.at <= at < self.stop:
                stop = text.find("\n", at, end)
                self.at, self.stop = at, end if stop < 0 else stop
                self.last = text.rfind("PRIVATE KEY", at + 10, self.stop)
            if self.last >= at + 10:
                return at, self.last + 11
            pos, steps = self.stop + 1, steps + 1           # no place on in this line counts
            if steps % _EVERY == 0 and self.deadline is not None and time.monotonic() > self.deadline:
                raise _OutOfTime
        return None


class _Jwt:
    """eyJ...: a signed web token's shape, read where its two parts meet (".eyJ") rather than once for each place it
    could begin at: a place counts where a run of [A-Za-z0-9_-] holds it with ten or more of them after it, the run
    ends at ".eyJ", and ten or more of them and "." follow; its match ends at that ".". The pattern's runs take each run
    whole, since "." is none of them, so it matches there and nowhere else. `begins` is \\beyJ, or eyJ for a value glued
    to another (secret_matches)."""

    def __init__(self, begins, text, end, deadline):
        self.begins, self.text, self.end, self.deadline = begins, text, end, deadline
        self.dot, self.low, self.high, self.close = -1, 0, -1, 0    # a ".eyJ" looked at, its places' range, the match's end

    def first(self, pos):
        text, end, steps = self.text, self.end, 0
        while True:
            if pos <= self.high:
                m = self.begins.search(text, max(pos, self.low), self.high + 3)
                if m is not None:
                    return m.start(), self.close
                pos = self.high + 1
            dot = text.find(".eyJ", max(pos + 13, self.dot + 1), end)
            if dot < 0:
                return None
            run = _TOKEN_RUN.match(text, dot + 1, end).end()
            low, self.dot = max(pos, self.dot + 1), dot
            if run - (dot + 4) >= 10 and run < end and text[run] == ".":
                gap = _RUN_BEGINS.search(text, low, dot)
                self.low, self.high, self.close = gap.start() + 1 if gap else low, dot - 13, run + 1
            else:
                self.high = -1
            steps += 1
            if steps % _EVERY == 0 and self.deadline is not None and time.monotonic() > self.deadline:
                raise _OutOfTime


def _shape_reading(pattern):
    """How secret_matches reads a shape of SECRET_SHAPES in time linear in its text: ("pem",), ("jwt", begins, glued)
    or ("pattern", begins, glued), where `glued` is the shape without its first \\b, which a value glued to another
    begins by. A pattern of another kind that could try a place on to its line's end (an unbounded repeat) has no such
    reading, and is refused when the workspace loads rather than read slowly."""
    text = pattern.pattern
    if text == _PEM:
        return ("pem",)
    if text == _JWT:
        return ("jwt", re.compile(r"\beyJ", re.ASCII), re.compile(r"eyJ", re.ASCII))
    text = text.replace("{16,}", "{16}")
    if re.search(r"[*+?]|\{[0-9]*,", text):
        raise ValueError(f"no reading in linear time of the secret shape {pattern.pattern!r}")
    return ("pattern", re.compile(text, pattern.flags),
            re.compile(text[2:] if text.startswith(r"\b") else text, pattern.flags))


_READINGS = tuple(_shape_reading(pattern) for _, pattern in SECRET_SHAPES)
_PEM_ORDER = next(order for order, how in enumerate(_READINGS) if how[0] == "pem")
# what begins any shape, glued or not, and what begins one where its own pattern finds it (with its \b): _holds_place
_ANY_GLUED = re.compile("|".join(
    "-----BEGIN" if how[0] == "pem" else "eyJ" if how[0] == "jwt" else
    ("(?i:%s)" if how[2].flags & re.IGNORECASE else "(?:%s)") % how[2].pattern for how in _READINGS), re.ASCII)
_ANY = re.compile("|".join(
    "-----BEGIN" if how[0] == "pem" else
    ("(?i:%s)" if how[1].flags & re.IGNORECASE else "(?:%s)") % how[1].pattern for how in _READINGS), re.ASCII)


def _holds_place(text, start, end, deadline):
    """Whether `text` from `start` to `end` holds a place where a shape's own pattern begins (with its \\b): each place
    a shape without its \\b begins (_ANY_GLUED) is tried for one with it (_ANY). A text with none holds no place, since
    a glued value counts only after one (secret_matches), and is read no further: a first look built from the shapes
    without their \\b let words such as "risk-assessment-framework" through to every shape's reading, and a scan of
    ordinary text took twice as long, and one search of a text by _ANY
    alone took 8 to 11 s for 64 MiB. Past `deadline`, looked at once in so many places tried, it raises _OutOfTime."""
    m, tried = _ANY_GLUED.search(text, start, end), 0
    while m is not None:
        if _ANY.match(text, m.start(), end):
            return True
        tried += 1
        if tried % _EVERY == 0 and deadline is not None and time.monotonic() > deadline:
            raise _OutOfTime
        m = _ANY_GLUED.search(text, m.start() + 1, end)
    return False


def _reader(order, text, end, glued, deadline):
    how = _READINGS[order]
    if how[0] == "pem":
        return _Pem(text, end, deadline)
    if how[0] == "jwt":
        return _Jwt(how[2] if glued else how[1], text, end, deadline)
    return _Pattern(how[2] if glued else how[1], text, end)


def secret_shape(text):
    """The name of the first of SECRET_SHAPES that `text` holds, or None: where its pattern finds it, read in time
    linear in the text (_READINGS)."""
    if not _holds_place(text, 0, len(text), None):
        return None
    for order, (name, _) in enumerate(SECRET_SHAPES):
        if _reader(order, text, len(text), False, None).first(0) is not None:
            return name
    return None


def secret_matches(text, start=0, end=None, deadline=None):
    """Each place `text` holds a secret shape from `start` to `end` (a line read in place), in the order they stand, as
    it is read: (its column, counted from 1 at `start`, and the shape's name), once for each place, named by the first
    of SECRET_SHAPES that begins there (an Anthropic key is also a key of sk- and 16 or more characters: one value, one
    finding). A place is where a shape's pattern begins, every one, one inside another's match among them;
    and, from just after where such a place begins,
    inside its match too, to the first ASCII white space after its match (a run a value found in it carries on to the
    first after its own match), where a shape begins with a letter, a digit or an underscore before it, which its
    pattern's \\b passes over: a value glued to one the scan found is found too, so a lift of the one never lets the
    other out. A value glued to anything else begins no place, as its pattern has it.
    Each shape is read in time linear in the text (_READINGS); the places are yielded as found, so a scan that has the
    findings it reports reads no further; and past `deadline` (time.monotonic's),
    looked at before each place, before each search a shape's reading makes and while a long reading runs, it raises
    _OutOfTime. One search by one pattern, which runs in C, is
    not looked into: it is linear in what it reads."""
    end = len(text) if end is None else end
    for pos, _, order, _ in _places(text, start, end, deadline):
        yield pos - start + 1, SECRET_SHAPES[order][0]


def _places(text, start, end, deadline):
    """secret_matches' places, each as (where it begins in `text`, where its match ends, its shape's order, and where
    the value it is found in ends: the first ASCII white space after the matches of it and of each value found in it
    so far)."""
    if not _holds_place(text, start, end, deadline):
        return
    count, never = len(SECRET_SHAPES), (end, end)
    begins = [_reader(order, text, end, False, deadline) for order in range(count)]
    glued = [None] * count
    nexts, glued_nexts = [(-1, 0)] * count, [(-1, 0)] * count
    pos = hot = start
    while True:
        if deadline is not None and time.monotonic() > deadline:
            raise _OutOfTime
        best, order = never, None
        if pos < hot:
            # within a value the scan found, before the white space that ends it: every shape's next place counts
            for i in range(count):
                if glued_nexts[i][0] < pos:
                    if deadline is not None and time.monotonic() > deadline:
                        raise _OutOfTime            # before each reading's search
                    if glued[i] is None:
                        glued[i] = _reader(i, text, end, True, deadline)
                    glued_nexts[i] = glued[i].first(pos) or never
                if glued_nexts[i][0] < best[0]:
                    best, order = glued_nexts[i], i
            if best[0] >= hot:
                pos = hot
                continue
        else:
            for i in range(count):
                if nexts[i][0] < pos:
                    if deadline is not None and time.monotonic() > deadline:
                        raise _OutOfTime
                    nexts[i] = begins[i].first(pos) or never
                if nexts[i][0] < best[0]:
                    best, order = nexts[i], i
            if order is None:
                return
        if best[1] >= hot:                  # a match that ends within the value the scan found ends it nowhere later
            space = _SPACE.search(text, best[1], end)
            hot = space.start() if space else end
        yield best[0], best[1], order, hot
        pos = best[0] + 1


# git's C quoting of a path (quote_c_style): these after a backslash, and a byte as three octal digits
_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
_C_ESCAPE = re.compile(rb'\\([abtnvfr"\\]|[0-7]{3})')


def _unquoted(text, deadline=None):
    """A path as a patch's header gives it, read back to the path itself: as it is, or, where git quoted it (a tab, a
    newline, a quote, a backslash, a control character or, under the default core.quotePath, a byte past ASCII), out
    of its double quotes with each escape read back to its byte (surrogateescape keeps a byte that is not UTF-8). A
    shape that a quoted path hides from \\b (the "\\t" before "ghp_") is so found and hidden like any other. Its
    escapes are found by one substitution, which calls back into Python for each, and past `deadline`
    (time.monotonic's), looked at once in so many escapes, it raises _OutOfTime: a loop over its characters took 3.4 s
    for a path of 16 MiB with no look at the scan's time, and the
    substitution takes about as long on a path of escapes (the fifth measured 2.73 s against 2.87), but stops at the
    deadline."""
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        return text
    count = 0

    def byte(escape):
        nonlocal count
        count += 1
        if count % _EVERY == 0 and deadline is not None and time.monotonic() > deadline:
            raise _OutOfTime
        code = escape.group(1)
        return bytes((_C_ESCAPES[chr(code[0])] if len(code) == 1 else int(code, 8) & 0xFF,))
    return _C_ESCAPE.sub(byte, text[1:-1].encode("utf-8", "surrogateescape")).decode("utf-8", "surrogateescape")


_HIDDEN = chr(0x2026)               # what a path says in place of each part of it the scan hides (an ellipsis)
_CONTROLS = {code: "\\x%02x" % code for code in (*range(32), 127)}   # each control character, as shown() writes it
SHOWN_MOST = 1024                   # a path's characters a scan's answer says at most, the last of them _HIDDEN where
                                    # it is cut (the app keeps 300: task_offers._clean)


def masked(path, deadline=None):
    """A path as a scan names it: each part of it (between its "/"s) that holds any of a value the scan finds in the
    whole path as _HIDDEN, a value running from where its shape begins (_places, a value glued to another among them) to
    the first ASCII white space after its match, and a private key's from its header to the path's end, since its body
    follows its header's line (a body of base64 holds "/"s). Hiding only a
    part that held a shape by its own pattern named a value glued to one in an earlier part,
    and the rest of a token whose characters can hold a "/" (FlyV1's). The parts are found by
    git's own "/"s around each value, never listed, so a path of millions of parts takes no more than its own length.
    What it leaves may hold a shape it made (a part hidden between "-----BEGIN" and "PRIVATE KEY" took a line break with
    it), which shown, its one caller, looks for in what it says. None for none. Past `deadline` (time.monotonic's) it
    raises _OutOfTime, as secret_matches does."""
    if path is None:
        return None
    said, done, first, reach = [], 0, None, 0   # the parts being hidden: where the first begins, where their values end

    def hide():
        last = path.find("/", reach - 1)        # the end of the last part a value reaches, which ends before its "/"
        last = len(path) if last < 0 else last
        said.append(path[done:first])
        said.append(_HIDDEN + ("/" + _HIDDEN) * path.count("/", first, last))
        return last
    for pos, _, order, ends in _places(path, 0, len(path), deadline):
        if order == _PEM_ORDER:
            ends = len(path)                    # a private key's body follows its header's line: hidden to the end
        if first is not None and (pos < reach or path.find("/", reach - 1, pos) < 0):
            reach = max(reach, ends)            # in a part already being hidden
            continue
        if first is not None:
            done = hide()
        first, reach = path.rfind("/", done, pos) + 1, ends
    if first is None:
        return path
    done = hide()
    said.append(path[done:])
    return "".join(said)


def shown(path, deadline=None):
    """A path as a scan's answer says it (its findings' `file`, and what it could not read): masked, then each byte
    that is not UTF-8 and each control character written as \\xNN, so that every reader can carry it (a JSON answer
    encodes UTF-8 alone), and _HIDDEN whole if what that leaves still
    holds a shape. None for none. A scan says each path once, under its deadline (_Findings.said): a path git gives
    can run to megabytes, and one of 16 MiB took 14 to 17 s to say with no look at the scan's time;
    and it says SHOWN_MOST characters of it at most, the last of them _HIDDEN where it is cut, cut
    once its values are hidden and again once its bytes are written out (each \\xNN is four),
    so a path of megabytes neither runs to megabytes in each of its findings nor takes seconds to write
    out."""
    if path is None:
        return None
    text = masked(path, deadline)[:SHOWN_MOST + 1]       # cut first, so little more than it is written out
    text = text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace").translate(_CONTROLS)
    if len(text) > SHOWN_MOST:                          # and again, as each byte written out is four characters
        text = text[:SHOWN_MOST - 1] + _HIDDEN
    return _HIDDEN if next(_places(text, 0, len(text), deadline), None) is not None else text


_DIGEST_PART = 1 << 20              # a line's characters encoded at a time for its digest


def line_digest(text, start=0, end=None):
    """The digest a finding's id names its text by (`text` from `start` to `end`, a line read in place): SHA-256 of
    git's bytes as the scan read them (surrogateescape gave each byte that is not UTF-8 a character of its own, which
    encoding gives back), as 64 hex. A scan makes it once for a text however many values it holds,
    and encodes a megabyte of characters at a time, never a long line whole."""
    end = len(text) if end is None else end
    digest = hashlib.sha256()
    for at in range(start, end, _DIGEST_PART):
        digest.update(text[at:min(at + _DIGEST_PART, end)].encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def finding_id(key, kind, path, text, shape, column, digest=None):
    """A finding's id: HMAC-SHA256 under `key` of its kind, its path, its text's digest (line_digest; `digest`, where
    the scan made it already), its shape and its column, cut to 32 hex. Two texts that differ in any byte have two ids,
    and two values on one text have two; the commit is not in it, nor the line's number, so the same value at the same
    place of a line that reads the same in the same file is one finding, in whichever commit adds it and on whichever
    line of the file it stands."""
    raw = json.dumps([kind, path or "", digest or line_digest(text), shape, column],
                     separators=(",", ":")).encode("ascii")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[:32]


class _Findings:
    """One push's scan's findings: those not lifted, up to FINDINGS_MAX, each once; and the lifted ids it met, each
    once, in the order met. Past `deadline` (time.monotonic's), the scan's time is out: what it was reading holds the
    push as unread, as a git command that runs out of the time does."""

    def __init__(self, lifted, key, deadline=None):
        self.lifted, self.key, self.deadline = frozenset(lifted), key, deadline
        self.found, self.passed, self._seen, self.out_of_time = [], [], set(), False
        self.paths = []                     # each finding's path as git gave it, which its `file` may hide part of
        self._said = {}                     # each path as its findings' `file` says it

    def late(self):
        """Whether the scan's time is out, which holds the push as unread."""
        if self.deadline is not None and time.monotonic() > self.deadline:
            self.out_of_time = True
        return self.out_of_time

    def said(self, path):
        """`path` as a finding's `file` or what the scan could not read says it (shown), once for each path, under
        the scan's deadline: past it, _OutOfTime."""
        if path not in self._said:
            self._said[path] = shown(path, self.deadline)
        return self._said[path]

    def add(self, kind, path, text, commit, line=None, start=0, end=None, place=None):
        """Each place `text` holds a secret shape from `start` to `end` (secret_matches), a finding of its own, with
        `line` (a line's number) and `place` (its path's place among those its commit changes), as _scan_all says; True
        once the scan has as many findings as it reports, or once its time is out, which secret_matches looks at before
        each place and while it reads."""
        digest = None
        try:
            for column, shape in secret_matches(text, start, end, self.deadline):
                if digest is None:
                    digest = line_digest(text, start, end)
                fid = finding_id(self.key, kind, path, None, shape, column, digest)
                if fid in self.lifted:
                    if fid not in self._seen:
                        self._seen.add(fid)
                        self.passed.append(fid)
                elif fid not in self._seen:
                    self._seen.add(fid)
                    self.found.append({"kind": kind, "shape": shape, "file": self.said(path), "line": line,
                                       "place": place, "column": column, "commit": commit, "finding": fid})
                    self.paths.append(path)
                    if len(self.found) >= FINDINGS_MAX:
                        return True
        except _OutOfTime:
            self.out_of_time = True
            return True
        return False

    def result(self, unread=None):
        """The scan's answer: what it found, or, where it could not read something or its time is out as it answers,
        that alone. It looks at the clock itself, so no answer given past the scan's time holds what the scan had
        found; one given as the scan had all it reports, or had read its last line, did."""
        if unread is None and self.late():
            unread = {"shape": UNREAD, "file": None}
        if unread is not None:
            return {"unread": unread, "findings": [], "passed": []}
        return {"unread": None, "findings": self.found, "passed": self.passed}


def _git_output(args, *, cwd, env, timeout, limit, input=None):
    """Run `args`, one of a push's scan's git commands (_scan_all), and read its output through a pipe, `limit` bytes
    and one at most: a CompletedProcess whose stdout is what was read. One byte past `limit` is enough to know the
    output is too large, so a command with more to say is ended there (killed, unless it has ended already, and waited
    for), and the rest of its output is never read: its status then says nothing of an output read only in part (no
    command's whole output is in memory before MAX_SCAN is looked at, whatever
    its size). A command still running `timeout` seconds after it starts is ended the same way and
    subprocess.TimeoutExpired raised, as subprocess.run does. `input`, where given, is written to the command as its
    output is read, one piece a pipe takes whole at a time and only when the pipe has room for it, so neither waits on
    the other: diff-tree --stdin and cat-file --batch answer each line as they read it, at many times its length, and a
    pipe holds only so much. A command that ends, or shuts its input, before it has read all of it is read to its end
    all the same; one whose output ends first has its input closed. With no `input` its input is /dev/null; what it
    says on its standard error goes to /dev/null too, neither read nor passed on."""
    deadline = time.monotonic() + timeout
    given = memoryview(input) if input is not None else None
    out = bytearray()
    with subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL if given is None else subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        try:
            with _SELECTOR() as sel:
                sel.register(proc.stdout, selectors.EVENT_READ)
                if given is not None:
                    sel.register(proc.stdin, selectors.EVENT_WRITE)
                sent, reading = 0, True
                while reading:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    for key, _ in sel.select(left):
                        if key.fileobj is proc.stdin:
                            try:
                                sent += os.write(key.fd, given[sent:sent + _PIPE_PIECE])
                            except BrokenPipeError:
                                sent = len(given)           # it ended, or shut its input: its output is still read
                            if sent >= len(given):
                                sel.unregister(proc.stdin)
                                _shut(proc.stdin)
                        elif reading:
                            piece = os.read(key.fd, min(_READ, limit + 1 - len(out)))
                            out += piece
                            reading = bool(piece) and len(out) <= limit
            if len(out) > limit:
                proc.kill()                         # never read on; Popen's own exit waits for it
            else:
                if given is not None:
                    _shut(proc.stdin)               # where its output ended before all its input was written
                proc.wait(timeout=max(deadline - time.monotonic(), 0))
        except BaseException:
            proc.kill()                             # then Popen's own exit waits for it
            raise
    return subprocess.CompletedProcess(args, proc.returncode, bytes(out), None)


def _shut(stream):
    """Close a command's input, which it may have shut already."""
    try:
        stream.close()
    except BrokenPipeError:
        pass


def bundled_claude():
    """The Agent SDK's bundled claude, at the workspace's pin (proofpane_workspace/requirements.txt), or None."""
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(claude_agent_sdk.__file__)), "_bundled", "claude")
    return path if os.path.isfile(path) else None


def _int(value, least=1):
    return isinstance(value, int) and not isinstance(value, bool) and value >= least


def _strings(value, most, each):
    return isinstance(value, list) and len(value) <= most and all(
        isinstance(v, str) and 0 < len(v) <= each and not agentry._CONTROL.search(v) for v in value)


def check_message(given):
    """The attempt message's task, held to its shapes; AgentError otherwise. Its packet
    is decoded; whether it is the one the app meant is the attempt's own check (task_attempt.check_spec)."""
    if not isinstance(given, dict):
        raise agentry.AgentError("A task's attempt carries its task.")
    missing = [k for k in MESSAGE_KEYS if k not in given]
    if missing:
        raise agentry.AgentError(f"A task's attempt lacks {', '.join(missing)}.")
    out = {k: given[k] for k in MESSAGE_KEYS}
    if not (isinstance(out["id"], str) and shapes.TASK_ID.fullmatch(out["id"])):
        raise agentry.AgentError("A task's id is 32 lowercase hex digits.")
    for name in ("step", "attempt", "fence"):
        if not _int(out[name]):
            raise agentry.AgentError(f"A task's {name} is a whole number from 1.")
    if out["step"] > shapes.STEPS_MAX:
        raise agentry.AgentError(f"A task has at most {shapes.STEPS_MAX} steps.")
    if not attempts.is_backend(out["backend"]):
        raise agentry.AgentError("A task's backend is an account (default or its id) or a key (its provider).")
    identity = out["identity"]
    if out["backend"]["kind"] == "account":
        if not (isinstance(identity, dict) and isinstance(identity.get("email"), str)
                and isinstance(identity.get("plan"), str)
                and all(isinstance(identity.get(k), (str, type(None))) for k in ("organization", "org_id"))):
            raise agentry.AgentError("An account's attempt carries the identity its owner confirmed.")
        out["identity"] = {k: identity.get(k) for k in ("email", "organization", "plan", "org_id")}
    elif identity is not None:
        raise agentry.AgentError("A key's attempt carries no account's identity.")
    if not (isinstance(out["chosen_by"], str) and _WORD.match(out["chosen_by"])):
        raise agentry.AgentError("A task's attempt says who chose its backend.")
    if not isinstance(out["reserve"], bool):
        raise agentry.AgentError("A task's reserve is true or false.")
    cap = out["cap_usd"]
    if cap is not None and not (isinstance(cap, (int, float)) and not isinstance(cap, bool) and 0 < cap <= 1000):
        raise agentry.AgentError("A task's cap is a number of US dollars, more than 0 and at most 1000, or none.")
    if out["backend"]["kind"] == "key" and cap is None:
        raise agentry.AgentError("An attempt on a key is pressed with a cap.")
    if out["check"] is not None and not (isinstance(out["check"], str) and 0 < len(out["check"]) <= MAX_CHECK
                                         and "\x00" not in out["check"]):
        raise agentry.AgentError(f"A step's check is a command of at most {MAX_CHECK} characters, or none.")
    for name in ("protected", "external"):
        if not _strings(out[name], shapes.LIST_MAX, shapes.LIST_ITEM_MAX):
            raise agentry.AgentError(f"A task's {name} is at most {shapes.LIST_MAX} lines of plain text.")
    if out["repo_slug"] is not None and not (isinstance(out["repo_slug"], str) and _SLUG.match(out["repo_slug"])):
        raise agentry.AgentError("A task's repository is owner/name, or none.")
    address = out["repo_address"]
    if address is not None and not (isinstance(address, str) and 0 < len(address) <= MAX_ADDRESS
                                    and not address.startswith("-") and not agentry._CONTROL.search(address)):
        raise agentry.AgentError("A task's address is its repository's address, or none.")
    try:
        packet = base64.b64decode(out["packet_b64"] or "", validate=True)
    except (TypeError, ValueError):
        packet = b""
    if not packet or len(packet) > shapes.PACKET_MAX:
        raise agentry.AgentError("A task's attempt carries its packet, at most 49,152 bytes.")
    if not (isinstance(out["packet_digest"], str) and shapes.DIGEST.fullmatch(out["packet_digest"])):
        raise agentry.AgentError("A packet's digest is 64 lowercase hex digits.")
    return out


def _resume_refusal(message, known):
    """The resume check's first two on a checked message alone, with task_attempt.check_spec's words and
    the check each names, or None: a fence not higher than `known` (the highest the workspace started for the task, or
    None), a packet whose tagged digest is not the one the app sent, or one that is not a resume packet. A start runs
    them before anything of the attempt's; check_spec runs them again on the spec."""
    if known is not None and message["fence"] <= known:
        return attempts.SpecError("This attempt's fence is not higher than one the workspace already started for the "
                                  "task.", "fence", {"fence": message["fence"], "known": known})
    packet = base64.b64decode(message["packet_b64"])
    served = shapes.tagged_digest(shapes.RESUME_TAG, packet)
    if served != message["packet_digest"]:
        return attempts.SpecError("The packet this workspace received is not the one the app sent.", "packet",
                                  {"sent": message["packet_digest"], "served": served})
    try:
        parsed = json.loads(packet.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict) or parsed.get("schema") != shapes.PACKET_SCHEMA:
        return attempts.SpecError("The packet is not a resume packet.", "packet", {"served": served})
    return None


class TaskRuns:
    """The workspace's tasks: their attempts as runs of their agents (agents.py), their worktrees and records."""

    TASK_FILE = TASK_FILE
    check_message = staticmethod(check_message)

    def __init__(self, agents):
        self.agents = agents
        self.ws = agents.ws
        # the tests' stand-in, in place of the claude a task runs (module docstring)
        self.claude_path = os.environ.get("PP_TASK_CLAUDE") or None
        self.live = {}                      # run id -> its Attempt, while it runs
        self.ended = {}                     # run id -> set when its attempt has ended and its record is written
        self.task_of = {}                   # run id -> its task's id, for a task's runs
        self._identity_locks = {}           # an account's id ("default": the default one) -> its identity read's lock
        self._identity_guard = threading.Lock()

    # where things are

    def is_task(self, run_id):
        return run_id in self.task_of

    def folder(self, agent_id, task_id):
        if not (isinstance(task_id, str) and shapes.TASK_ID.fullmatch(task_id)):
            raise agentry.AgentNotFound("No such task.")
        return os.path.join(self.agents._agent_dir(agent_id), "tasks", task_id)

    def work(self, agent_id, task_id):
        return os.path.join(self.folder(agent_id, task_id), "work")

    @staticmethod
    def branch(task_id):
        return "pp/task-" + task_id

    def agent_of_task(self, task_id):
        """The agent a task of this workspace is under (the one whose folder holds it), else AgentNotFound."""
        if not (isinstance(task_id, str) and shapes.TASK_ID.fullmatch(task_id)):
            raise agentry.AgentNotFound("No such task.")
        found = [a for a in agentry._names(self.agents.root)
                 if agentry._ID.match(a) and os.path.isdir(os.path.join(self.agents.root, a, "tasks", task_id))]
        if len(found) != 1:
            raise agentry.AgentNotFound("No such task here.")
        return found[0]

    def claude(self):
        """The claude a task's attempt runs (module docstring)."""
        if self.claude_path:
            return self.claude_path
        if self.ws.claude_updates:
            return self.ws.claude_floor
        return bundled_claude()

    # the task's record

    def _read_record(self, agent_id, task_id):
        """The task's own record, as the workspace signed it; None when it has none; AgentError when it was changed."""
        path = os.path.join(self.folder(agent_id, task_id), RECORD)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            data = None
        record = agentry._verified(data, self.agents._key)
        if record is None or record.get("task_id") != task_id or record.get("agent_id") != agent_id:
            raise agentry.AgentError("This task's record here was changed by something other than the workspace.")
        return record

    def _write_record(self, record, durable=False):
        path = os.path.join(self.folder(record["agent_id"], record["task_id"]), RECORD)
        record = dict(record)
        record.pop("mac", None)
        record["seq"] = (record.get("seq") or 0) + 1
        text = agentry._canonical(dict(record, mac=agentry._mac(record, self.agents._key)))
        _write_file(path, text.encode("utf-8"), durable)
        return record

    def _change_record(self, agent_id, task_id, change, durable=False):
        with self.agents._lock:
            record = self._read_record(agent_id, task_id)
            if record is None:
                raise agentry.AgentNotFound("No such task here.")
            change(record)
            return self._write_record(record, durable)

    # the attempts' records

    def records_of(self, agent_id, task_id):
        """The signed records of the task's runs, in the order they were queued."""
        return [r for r in self.agents.runs(agent_id)
                if (r["run"].get("trigger") or {}).get("kind") == "task"
                and (r["run"].get("trigger") or {}).get("id") == task_id]

    def history(self, agent_id, task_id, run_id):
        """What the task's earlier attempts' records say for the next one: the highest fence the workspace started for
        it, and every checkpoint they completed."""
        known, checkpoints = None, []
        for r in self.records_of(agent_id, task_id):
            if r["id"] == run_id:
                continue
            task = r["run"].get("task") if isinstance(r["run"].get("task"), dict) else {}
            fence = task.get("fence")
            if r["run"].get("started_at") is not None and _int(fence):
                known = fence if known is None else max(known, fence)
            for entry in task.get("checkpoints") or []:
                if isinstance(entry, dict) and entry.get("state") == "complete":
                    checkpoints.append(entry)
        return known, checkpoints

    # the watch

    def git_places(self, work):
        """The places git's own settings name for the worktree, found with git, so read only where nothing watched has
        changed (a task's first attempt, or after the look found the places as kept): its common folder's config,
        hooks and info, its own folder's commondir, config.worktree and gitdir, every file its configuration
        includes, and the file core.excludesFile names."""
        from .workspace import git
        _, common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=work)
        common = os.path.realpath(common.strip())
        _, own = git("rev-parse", "--path-format=absolute", "--git-dir", cwd=work)
        own = os.path.realpath(own.strip())
        out = [os.path.join(common, "config"), os.path.join(common, "hooks"), os.path.join(common, "info"),
               os.path.join(own, "commondir"), os.path.join(own, "config.worktree"), os.path.join(own, "gitdir")]
        _, listed = git("config", "--list", "--show-origin", "--includes", "-z", cwd=work, ok=(0, 1))
        for origin in listed.split("\0")[0::2]:
            if origin.startswith("file:"):
                out.append(os.path.realpath(os.path.join(work, origin[len("file:"):])))
        code, named = git("config", "--get", "core.excludesFile", cwd=work, ok=(0, 1))
        if code == 0 and named.strip():
            out.append(os.path.realpath(os.path.expanduser(named.strip())))
        return list(dict.fromkeys(out))

    def key_folders(self):
        """Each key backend's own Claude Code folder (key_folder), made or not: their settings and instructions are
        what a key's attempt loads."""
        return [os.path.join(self.ws.accounts.dir, "keys", provider, "claude") for provider in shapes.KEY_PROVIDERS]

    def import_places(self, agent, task_id):
        """The places outside the task's worktree that the instruction files a next run's claude loads import with
        @path (attempts.outside_imports), read from disk: the worktree's at any depth, among its first
        IMPORT_WALK entries, those of every folder above it, and Claude Code's own CLAUDE.md in ~/.claude and every
        account's and key backend's folder."""
        work = self.work(agent["id"], task_id)
        home = os.path.realpath(os.path.expanduser("~"))
        sources, left = [], IMPORT_WALK
        for folder, dirs, files in os.walk(work):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            sources += [os.path.join(folder, name) for name in sorted(files) if name in INSTRUCTIONS]
            left -= len(dirs) + len(files)
            if left <= 0:
                break
        folder = os.path.dirname(work)
        while True:
            sources += [os.path.join(folder, name) for name in INSTRUCTIONS]
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
        claude_dirs = [os.path.join(home, ".claude")] + self.agents._account_folders("claude") + self.key_folders()
        sources += [os.path.join(d, "CLAUDE.md") for d in claude_dirs]
        return attempts.outside_imports(sources, work, home)

    def places(self, agent, task_id, git_places, imported=()):
        """The task's watched places (module docstring): paths, and {path, keys} for a JSON record's parts; git's own
        places as `git_places` gives them (git_places(), as the task's record keeps them), so no git runs here, every
        account's and key backend's folder there is now, and what its instruction files import from outside it as
        `imported` gives them (import_places(), as the record keeps them)."""
        work = self.work(agent["id"], task_id)
        home = os.path.realpath(os.path.expanduser("~"))
        gits = [os.path.join(work, ".git"), os.path.join(home, ".gitconfig"), os.path.join(home, ".config", "git")]
        gits += list(git_places)
        claude_dirs = [os.path.join(home, ".claude")] + self.agents._account_folders("claude") + self.key_folders()
        programs = [os.path.join(d, part) for d in claude_dirs for part in agentry.CLAUDE_PARTS]
        programs += [os.path.join(home, name) for name in agentry.SHELL_FILES + agentry.PROGRAM_FILES]
        programs += [os.path.join(d, "config.toml") for d in self.agents._account_folders("codex")]
        claude = self.claude()
        if claude and os.path.realpath(claude).startswith(home + os.sep):
            programs.append(os.path.realpath(claude))
        # the instruction files of every folder above the worktree, which its claude loads
        above, folder = [], os.path.dirname(work)
        while True:
            above += [os.path.join(folder, name) for name in INSTRUCTIONS]
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
        records = []
        for folder in claude_dirs:
            path = os.path.join(home if folder == claude_dirs[0] else folder, ".claude.json")
            records.append({"path": path, "keys": [list(k) for k in agentry.MCP_KEYS + IDENTITY_KEYS]})
        paths = [p for p in dict.fromkeys(gits + programs + above + list(imported))
                 if os.path.basename(p) not in WRITTEN_HERE or os.path.dirname(p) != self.ws.root]
        return paths + records

    def expected(self, kept, places, now):
        """The look the task's next attempt must find (module docstring): each place as it was kept, a place first
        seen now as it is now."""
        kept = kept if isinstance(kept, dict) else {}
        return {k: (kept[k] if k in kept else v) for k, v in _plain(now).items()}

    # making a task's worktree

    def _make(self, agent, task_id, commit=None):
        """The task's folder, worktree and record, made at its first attempt from the agent's repository's base (or at
        a teammate's acceptance from `commit`): the commit it was cut from is its checkpoint 0. Returns the record."""
        from .workspace import RUN_GIT, git
        folder = self.folder(agent["id"], task_id)
        work = self.work(agent["id"], task_id)
        repo = agent["repo"] or self.agents._scratch(agent["id"])
        path = self.ws._repo_path(repo)
        start = commit or agent["base_ref"] or "HEAD"
        if not commit and agent["keep_worktree"]:
            # cut from the agent's branch head: an agent that keeps its worktree works on a branch of its own
            # (agents.py, branch), whose head is where its work stands; one that does not starts each run at its base
            kept = f"refs/heads/pp/agent-{agent['id']}"
            code, _ = git("rev-parse", "--verify", "--quiet", kept, cwd=path, ok=(0, 1), env=RUN_GIT)
            if code == 0:
                start = kept
        code, sha = git("rev-parse", "--verify", "--quiet", "--end-of-options", start + "^{commit}", cwd=path,
                        ok=(0, 1), env=RUN_GIT)
        if code != 0:
            raise agentry.AgentError(f"The repository has no commit {start} to start the task from.")
        sha = sha.strip()
        os.makedirs(folder, exist_ok=True)
        branch = self.branch(task_id)
        exists, _ = git("rev-parse", "--verify", "--quiet", "refs/heads/" + branch, cwd=path, ok=(0, 1), env=RUN_GIT)
        if exists == 0:
            raise agentry.AgentError("The task's branch is there already: a task is made once.")
        # written with the HISTORY guards in a repository the record of skill proposals names, as today in any other
        # (skill_improve.py, guards), refusing nothing here: the bar is
        # check 5's, every attempt's, so that a refusal ends it blocked as every attempt's does
        from . import skill_improve
        bar = getattr(self.ws, "skill_improve", None)
        written = skill_improve.with_history(RUN_GIT) if bar is not None and bar.guards(repo) else RUN_GIT
        git("worktree", "add", "--quiet", "-b", branch, work, sha, cwd=path, env=written)
        # known to the record of git's places with the git folder it has now: another one there later is a change of
        # its repository (gitplaces.Checker.expect), as for an agent's own worktree
        self.agents._git_made(work)
        if self.ws.claude_first_run:
            from . import first_run
            first_run.trust(os.path.expanduser("~"), os.path.realpath(path))
        git_places = self.git_places(work)
        imported = self.import_places(agent, task_id)
        places = self.places(agent, task_id, git_places, imported)
        record = {"task_id": task_id, "agent_id": agent["id"], "repo": repo, "checkpoint0": sha,
                  "places": places, "git_places": git_places, "import_places": imported,
                  "look": _plain(attempts.look(places)),
                  "created_at": int(time.time()), "head": None, "pushes": {}, "seq": 0}
        return self._write_record(record, durable=True)

    # an attempt: its start, its records, its end

    async def start(self, run_id):
        """Start the task's attempt the run is (agents._start's for a task): a stale fence or a packet not the one sent
        refused first, then its worktree made at its first attempt, its look, its agent's precheck, its spec, and the
        attempt in a task of its own. An attempt refused before any claude starts ends the run as its refusal says.
        Proofpane's budget check (budget_check.py) is asked after the fence and the packet and before
        its worktree: one it refuses fails the run, `start`, in the product's own words; one that could not be made, or
        not all read, is said in the attempt's chat's log. An attempt's Agent SDK budget stays its own (its message's
        cap, else its agent's: spec), never bounded by the room the check leaves."""
        agents = self.agents
        agent = agents.agent(agents._index[run_id])
        if agent["retired"] or agent["paused"]:
            agents._if_queued(run_id, state="stopped", reason="the agent was paused or retired")
            return
        # the bar on starts (skill_improve.py): the app's word waited for
        # on the event loop, before the task's record is read or made; check 5 then reads only whether it came
        bar = getattr(self.ws, "skill_improve", None)
        if bar is not None:
            await bar.word()
        record = agents.read(run_id)
        try:
            message = json.loads(agents._kept_file(run_id, TASK_FILE, record) or b"null")
            message = check_message(message)
            task_id = message["id"]
            backend = message["backend"]
            account = backend["id"] if backend["kind"] == "account" else None
            on = self.ws.accounts.for_session("chat", account) if account is not None else None
            if account not in (None, "default") and on != account:
                raise agentry.AgentError("That account is not here.")
            # a stale fence and a packet not the one sent, or not a resume packet, are refused before anything of the
            # attempt's: the task's worktree made or its record written, the start's look, its git, its agent's
            # precheck, so such a message neither holds the agent for a change its look would find
            # nor runs the precheck
            known, _ = await asyncio.to_thread(self.history, agent["id"], task_id, run_id)
            refused = _resume_refusal(message, known)
            # Proofpane's budget check (budget_check.py), in a thread (it waits on the network): after
            # the fence and the packet, by which the app reads an attempt's end, and before anything of the attempt's
            # is made or run (the task's worktree at its first attempt, its record, its look, its precheck, its claude);
            # a stand-in for the workspace in a test may have none
            ask = getattr(self.ws, "budget", None)
            word = await asyncio.to_thread(ask, budget_check.attempt_kind(backend)) \
                if refused is None and ask is not None else None
            spent = word is not None and not word.allowed
            kept = None if refused is not None or spent else await asyncio.to_thread(self._kept_record, agent, task_id)
        except Exception as e:  # noqa: BLE001  the run fails with why, the next one goes on
            agents._if_queued(run_id, state="failed", reason=agentry._reason(e), cause="start")
            return
        if refused is not None:
            self._refused(run_id, refused)
            return
        if spent:
            # refused in the product's own words, as a start the workspace refuses before any claude is (`start`)
            agents._if_queued(run_id, state="failed", reason=word.message[:agentry.MAX_TEXT], cause="start")
            return
        self.ws._bind(run_id, on)
        # check 4's look, before the agent's precheck (a command that may run git) and before any git of the attempt's:
        # a change holds the agent and ends the run blocked, as the attempt's own check 4 would
        try:
            changed, expected, now = await asyncio.to_thread(self._watch_found, kept)
        except Exception as e:  # noqa: BLE001
            agents._if_queued(run_id, state="failed", reason=agentry._reason(e), cause="start")
            return
        if changed:
            self._refused(run_id, attempts.SpecError(
                CHANGED_SINCE, check="watch",
                found={"expected": attempts.look_digest(expected), "found": attempts.look_digest(now)}),
                hold=f"{CHANGED_SINCE[0].upper()}{CHANGED_SINCE[1:]}. {WATCH_HOLD}")
            agents._hold(agent["id"])
            # the attempt ended here, and what its look found is the next one's baseline (check 4: "as the previous
            # attempt's end found them"): once the owner has resumed the agent, the change is not found again; kept
            # only after the refusal and the hold, so that nothing could take it in unseen
            await asyncio.to_thread(self._keep_now, agent["id"], task_id, _plain(now))
            return
        # the look found the places as kept: git's own places found again (git runs only from here), and a place first
        # seen now (an account added since) kept as it is now, whatever ends this attempt before its end's own look
        # (its precheck saying no, a refusal before its claude): a change to it is found next time
        try:
            kept = await asyncio.to_thread(self._refresh, agent, task_id, kept, expected)
        except Exception as e:  # noqa: BLE001
            agents._if_queued(run_id, state="failed", reason=agentry._reason(e), cause="start")
            return
        # from here the attempt's own code may run (its precheck, then its claude): a suspect for a change of git's
        # places found meanwhile (agents.suspects, gitplaces.py), as an agent's run is from its precheck on; every end
        # after this point looks at them again (_places_at_end)
        agents._acting.add(run_id)
        code, said = None, ""
        if agent["precheck"] and agents._queued(run_id):
            code, said = await asyncio.to_thread(agents._precheck, run_id, agent)
        if code:
            places = await self._places_at_end(run_id, agent["id"])
            self._end_unstarted(run_id, agent["id"], places, state="skipped", cause=None,
                                reason=f"its precheck exited {code}" + (f": {said}" if said else ""))
            return
        try:
            spec = await asyncio.to_thread(self.spec, run_id, agent, message, kept, on)
            attempt = attempts.Attempt(spec, lambda r, durable: self.emit(run_id, r, durable))
            if bar is not None:
                # the bar's check for this attempt, run at check 5 and
                # again before its work phase: steps 1 and 2 on `rev` read in the worktree, step 3 on its files
                attempt.bar = (lambda repo, work: lambda rev: bar.check_start(repo, rev, work, "attempt", work))(
                    kept["repo"], spec["work"])
        except attempts.SpecError as e:
            places = await self._places_at_end(run_id, agent["id"])
            if places:
                self._end_unstarted(run_id, agent["id"], places)
            else:
                self._refused(run_id, e)
            return
        except Exception as e:  # noqa: BLE001
            places = await self._places_at_end(run_id, agent["id"])
            self._end_unstarted(run_id, agent["id"], places, state="failed", reason=agentry._reason(e),
                                cause="start")
            return
        now = int(time.time())

        def started(r):
            r.update(repo=kept["repo"], base=kept["checkpoint0"], branch=self.branch(task_id))
            r["run"].update(state="working", started_at=now, brief_version=agent["brief_version"],
                            task=attempt._record()["task"], backend=dict(backend))
        if not agents._if_queued(run_id, then=started):
            # stopped while it started: what its precheck started is ended, and git's places looked at, as at any end
            self._end_unstarted(run_id, agent["id"], await self._places_at_end(run_id, agent["id"]))
            return
        agents.working.add(run_id)
        self.live[run_id] = attempt
        self.ended[run_id] = asyncio.Event()
        # the attempt's chat is the run's: the page reads its log and answers its permission asks there; its activity
        # is the workspace's, and has the run's usage read again once it is quiet (usage_report.py)
        c = attempt.chat
        c.listeners = self.ws.chat_listeners
        c.on_activity = self.ws.activity_of(run_id) if hasattr(self.ws, "activity_of") else self.ws.touch
        self.ws.chats[run_id] = c
        if word is not None and word.note:
            # what its budget check said where it could not be made, or not every budget was read (budget_check.py): in
            # the attempt's chat's log, which its page reads, before anything of its claude's
            c.note("notice", text=word.note)
        agents._spawn(self._run(run_id, attempt, agent["id"], task_id))

    def _kept_record(self, agent, task_id):
        record = self._read_record(agent["id"], task_id)
        if record is None:
            if os.path.exists(self.work(agent["id"], task_id)):
                raise agentry.AgentError("The task's worktree is here without its record.")
            record = self._make(agent, task_id)
        if not os.path.isdir(self.work(agent["id"], task_id)):
            raise agentry.AgentError("The task's worktree is gone: remove the task.")
        # the places as they are for this attempt, whichever account it runs on: an account added since is looked
        # at from now on (expected() takes a place first seen now as it is now) and one removed is no longer compared;
        # git's own places as the record keeps them, since no git runs before the look (check 4)
        places = self._current_places(agent, task_id, record)
        if places != record["places"]:
            record = self._change_record(agent["id"], task_id, lambda rec: rec.update(places=places))
        return record

    def _current_places(self, agent, task_id, record):
        """The task's places as they are now, git's own and what its instruction files import as its record keeps them
        (a record from before git's were kept has them found with git, as then)."""
        git_places = record["git_places"] if "git_places" in record else \
            self.git_places(self.work(agent["id"], task_id))
        return self.places(agent, task_id, git_places, record.get("import_places") or ())

    def _refresh(self, agent, task_id, kept, expected):
        """After the start's look found the places as kept: git's own places, and what the instruction files import,
        found again (git runs only now, where nothing watched has changed), each place first seen now kept as it is now
        whatever ends this attempt, so a change to it is found next time, and the task's record written with them.
        Returns the record."""
        git_places = self.git_places(self.work(agent["id"], task_id))
        imported = self.import_places(agent, task_id)
        places = self.places(agent, task_id, git_places, imported)
        look = dict(expected)
        if places != kept["places"]:
            look.update({k: v for k, v in _plain(attempts.look(places)).items() if k not in look})
        if git_places == kept.get("git_places") and places == kept["places"] and \
                all((kept.get("look") or {}).get(k) == v for k, v in look.items()):
            return kept

        def change(record):
            record.update(git_places=git_places, import_places=imported, places=places,
                          look=dict(record.get("look") or {}, **look))
        return self._change_record(agent["id"], task_id, change)

    def _watch_found(self, kept):
        """(changed, expected, now): whether the task's watched places changed since its last attempt ended, what they
        are expected to be (each as its record kept it, a place first seen now as it is now), and what they are."""
        places = kept["places"]
        now = attempts.look(places)
        expected = self.expected(kept.get("look"), places, now)
        return bool(watch.changes(expected, _plain(now))), expected, now

    def _keep_now(self, agent_id, task_id, look):
        """`look` kept as the task's watch's next baseline (_kept_look); should that fail, the next check 4 compares
        with the look kept before, as after an attempt's end whose own look could not be kept."""
        try:
            self._change_record(agent_id, task_id, lambda record: record.update(look=_kept_look(record, look)))
        except Exception:  # noqa: BLE001
            pass

    def spec(self, run_id, agent, message, kept, account):
        """The attempt's spec, from the message, the agent, the task's record and its
        earlier attempts' records."""
        from .workspace import program_env
        agents = self.agents
        task_id = message["id"]
        packet = base64.b64decode(message["packet_b64"])
        try:
            parsed = json.loads(packet.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            parsed = {}
        parsed = parsed if isinstance(parsed, dict) else {}
        info = parsed.get("information") if isinstance(parsed.get("information"), dict) else {}
        instructions = parsed.get("instructions") if isinstance(parsed.get("instructions"), dict) else {}
        expect = info.get("expect") if isinstance(info.get("expect"), dict) else None
        if expect is not None:
            expect = {"head": expect.get("head"), "tree": expect.get("tree")}
        objective = (instructions.get("step") or {}).get("objective") if isinstance(instructions.get("step"), dict) \
            else None
        known, checkpoints = self.history(agent["id"], task_id, run_id)
        folder = agents._agent_dir(agent["id"])
        backend = message["backend"]
        if backend["kind"] == "key":
            account_env = {"CLAUDE_CONFIG_DIR": self.key_folder(backend["provider"])}
        else:
            account_env = self.ws.accounts.env(self.ws.accounts.get(account)) if account else {}
        env = program_env({"PP_SESSION_ID": run_id, "PP_SESSION_DIR": agents.run_dir(run_id), **account_env,
                           "PP_AGENT_RUN": "1", "PP_AGENT_ID": agent["id"], "PP_AGENT_DIR": folder,
                           processes.MARK: run_id,
                           "PP_AGENT_AUTHOR_NAME": agent["author"]["name"],
                           "PP_AGENT_AUTHOR_EMAIL": agent["author"]["email"]})
        if agent["autocompact_window"]:
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(agent["autocompact_window"])
        pins = dict(attempts.ATTEMPT_ENV, **{n: os.environ.get(n, "") for n in attempts.WORKSPACE_ENV})
        env.update(pins)
        for name in shapes.REFUSED_ENV:
            env.pop(name, None)
        settings = self._settings(message["backend"], pins)
        places = kept["places"]
        now = attempts.look(places)
        unattended = message["chosen_by"] != "owner"
        chosen = agent["chat_settings"]
        mode = agent["unattended_mode"] if unattended else chosen.get("permission_mode", "default")
        # on another vendor's key the model is that vendor's, pinned by its entry: the agent's is not passed
        model = chosen.get("model") if backend["kind"] == "account" or backend["provider"] == "anthropic" else None
        passed, _, _, _ = agents._mcp_for_run(run_id, agent)
        budget = message["cap_usd"] if message["cap_usd"] is not None else agent["max_budget_usd"]
        identity = message["identity"]
        return {
            "run_id": run_id, "task_id": task_id, "agent_id": agent["id"], "step": message["step"],
            "attempt": message["attempt"], "fence": message["fence"], "work": self.work(agent["id"], task_id),
            "branch": self.branch(task_id), "records": agents.run_dir(run_id), "packet": packet,
            "packet_digest": message["packet_digest"], "backend": dict(message["backend"]),
            "identity": {k: identity.get(k) for k in ("email", "organization", "plan")} if identity else None,
            "org_id": (identity or {}).get("org_id"), "claude": self.claude() or "",
            "claude_version": shapes.TASK_CLAUDE_VERSION, "env": env, "settings": settings,
            "allow": list(agent["allow"]), "permission_mode": mode, "tools": agents._own_tools(run_id),
            "mcp_servers": passed, "model": model, "effort": chosen.get("effort"),
            "brief": agent["brief"], "objective": objective if isinstance(objective, str) else "",
            "check": message["check"], "protected": list(message["protected"]),
            "external": list(message["external"]), "repo_slug": message["repo_slug"],
            "repo_address": message["repo_address"], "watch_places": places,
            "watch_digest": attempts.look_digest(self.expected(kept.get("look"), places, now)),
            "memory_dir": agents.memory(agent["id"]), "expect": expect, "checkpoint0": kept["checkpoint0"],
            "known_fence": known, "checkpoints_so_far": checkpoints, "max_turns": agent["limits"]["max_steps"],
            "max_seconds": agent["limits"]["max_run_seconds"], "max_budget_usd": budget,
            "unattended": unattended, "reserve_lifted": message["reserve"]}

    def key_folder(self, provider):
        """A key backend's own Claude Code folder (one per key backend, never signed in), so the helper's key is
        the only credential its attempt can use: in the accounts' folder, which the gate refuses every attempt as an
        account's, the agent's user's alone."""
        folder = os.path.join(self.ws.accounts.dir, "keys", provider, "claude")
        os.makedirs(folder, mode=0o700, exist_ok=True)
        return folder

    def _settings(self, backend, pins):
        """The attempt's own --settings: the workspace's claude settings (the gate in exec form under the local
        provider), the pins in its env, and for a key its helper and its provider's address."""
        settings = {}
        if self.ws.claude_settings:
            try:
                with open(self.ws.claude_settings, encoding="utf-8") as fh:
                    settings = json.load(fh)
            except (OSError, ValueError):
                raise agentry.AgentError("The workspace's own settings for claude could not be read.") from None
        env = dict(settings.get("env") or {})
        if backend["kind"] == "key":
            provider = backend["provider"]
            here = os.path.dirname(os.path.abspath(providers.__file__))
            settings["apiKeyHelper"] = providers.helper_command(sys.executable, here, provider)
            env.update((providers.CATALOG.get(provider) or providers.KEY_ONLY[provider])["env"])
        else:
            # an account's attempt names Anthropic's address too, so a repository's settings cannot send a sign-in's
            # token elsewhere (on an account as on Anthropic's key)
            env.update(providers.KEY_ONLY["anthropic"]["env"])
        env.update(pins)
        settings["env"] = env
        return settings

    def _refused(self, run_id, e, hold=None):
        """A spec the attempt refused before any claude started: a resume check's (stale fence, a changed packet, a
        claude not the version it must be) blocks the run, naming it; any other is the workspace's own failure. `hold`,
        where the refusal holds the agent, goes on the run's record for the app (_watch_hold)."""
        check = getattr(e, "check", None)

        def ended(r):
            run = r["run"]
            task = dict(run.get("task") or {})
            if check in shapes.RESUME_CHECKS:
                task["resume"] = {"outcome": "blocked", "check": check, "found": getattr(e, "found", {}), "acks": 0}
                run.update(state="blocked", cause="resume", reason=str(e)[:agentry.MAX_TEXT], task=task,
                           ended_at=int(time.time()))
            else:
                run.update(state="failed", cause="start", reason=str(e)[:agentry.MAX_TEXT], ended_at=int(time.time()))
            if hold:
                run["hold"] = hold
        if not self.agents._if_queued(run_id, then=ended):
            self.agents._change(run_id, ended)

    # git's places at an attempt's end (gitplaces.py, agents.suspects and agents._places_seen)

    async def _places_at_end(self, run_id, agent_id):
        """At an end of the attempt once its own code could run, as at an agent's run's end: what it started ended,
        its end kept to the moment (from then on nothing of it changes anything), and every place of git's looked at
        again, every folder git runs in there now among them (_look_at_folders). The hold's words for those held with
        its agent among their suspects, or None."""
        agents = self.agents
        try:
            await asyncio.to_thread(processes.end, run_id)
            agents._ended_now(run_id)
            await asyncio.to_thread(self._look_at_folders)
            return agentry._hold_reason([], [], [], await asyncio.to_thread(agents._places_seen, agent_id))
        except Exception as e:  # noqa: BLE001  the attempt ends however git's places could be read, and is held
            # it fails closed: where the end of what it started, the record of that end, or the look itself raises, its
            # agent is held with words saying the look could not be made, never as though nothing had changed. An
            # agent's run's end gives those words where its look raises (agents._places_seen); it does not catch its
            # processes.end or _ended_now raising, and there its end raises with the run's record left working and its
            # agent not held, which this end does not follow
            return agentry._hold_reason([], [], [], [(agentry.UNSEEN, "looked", [type(e).__name__])])

    def _look_at_folders(self):
        """Every folder git runs in there now (Workspace._git_tops) checked by the record of git's places
        (workspace.check_places), while the attempt is still a suspect: a repository the attempt or its precheck made
        in the workspace's folders, which the record did not know of, is recorded and decided now, and held with its
        agent, the hold kept in the record across the workspace's restarts. Looked at only later, after a restart
        above all (its suspects counted from that start), it would be taken as the owner's."""
        from .workspace import GitHeld, check_places
        try:
            tops = self.ws._git_tops()
        except Exception:  # noqa: BLE001  a workspace with no such folders (a test's stand-in) looks at none
            return
        for top in tops:
            try:
                check_places(top)
            except GitHeld:
                continue
            except Exception:  # noqa: BLE001  one folder that cannot be read stops no other's look
                continue

    def _hold_for_places(self, agent_id, places):
        """Its agent held where git's places were (_places_at_end): the hold's words are on the run's record, which the
        app copies to pause the agent (_watch_hold), so that the owner's resume lifts this hold and 59a's together."""
        if places:
            self.agents._hold(agent_id)

    def _end_unstarted(self, run_id, agent_id, places, **ended):
        """An end of the run inside `start`, once its own code could run and before its attempt was under way: its
        precheck saying no, its spec refused where git's places hold its agent (with none, `_refused` ends it), its
        spec or its attempt failing to be made, or a stop while it started. Not every end before its claude started:
        an attempt under way that ends before its claude starts keeps the end it made, in `_run`: blocked (`resume`)
        where its claude is not of the version it must be (`_refused_live`), or writing its packet or one of its own
        checks ended it; failed, `start` where its claude did not start, `limit` where its time limit came first, and
        `error` on an error nothing caught, or where it ended with no reason given; and stopped on a stop (`stop`), or
        blocked where the stop's cause is a resume check's; git's places' words in the hold on its record. The
        workspace's own stop, which cancels the attempt, leaves `_run` before it writes any end. Here, where git's
        places hold its agent (_places_at_end), the run is blocked by them (`watch`), whatever it stood at, their words
        its reason and its hold, as an agent's own run that did not start is (agents._start). The app copies the hold
        of a run that ends while it has it as queued, whatever its state, but of one it has as stopped (the owner's
        stop while it started) only when it turns blocked, `watch`: a hold on a record that stayed stopped would hold
        the agent here and never there. The hold is set, not added to: 59a's own hold may have written the same words
        on the record once it ended. Else the run ends as `ended` says while it is still queued, or stays as a stop
        meanwhile left it."""
        def left(r):
            if places:
                r["run"].update(state="blocked", cause="watch", reason=places, hold=places,
                                ended_at=r["run"].get("ended_at") or int(time.time()))
            elif ended and r["run"]["state"] == "queued":
                r["run"].update(ended_at=int(time.time()), **ended)
        self.agents._change(run_id, left)
        self._hold_for_places(agent_id, places)

    def emit(self, run_id, view, durable):
        """A change of the attempt's record (task_attempt's emit): its view onto the run's signed record, written; a
        durable one flushed, file and folder, before this returns."""
        def change(r):
            run = r["run"]
            run["task"] = view["task"]
            run["backend"] = view["backend"]
            run["rate_limits"] = view["rate_limits"] or None
            run["question"] = view.get("question")
            if view.get("last_words"):
                run["last_words"] = view["last_words"][:agentry.MAX_REPORT]
            if view["state"] in agentry.LIVE and run.get("state") in agentry.LIVE:
                run.update(state=view["state"], reason=view.get("reason"))
        with self.agents._lock:
            record = self.agents.read(run_id)
            change(record)
            self.agents._write(record, durable=durable)

    async def _run(self, run_id, attempt, agent_id, task_id):
        """The attempt, to its end: then its run's record ended as the attempt says, the look at its end kept as the
        task's watch's next baseline, its agent held when something watched changed, and the next run started."""
        agents = self.agents
        view = refused = failure = None
        try:
            view = await attempt.run()
        except attempts.SpecError as e:
            refused = e
        except Exception as e:  # noqa: BLE001
            failure = agentry._reason(e)
        try:
            # what the attempt started is ended (its _finish), and git's places looked at again, as at an agent's run's
            # end (_places_at_end): a change of them made while it worked, in another repository among them, which
            # the task's own watch does not look at, is held with its agent among the suspects and named in the hold
            # on its record beside the watch's, so that the owner's one resume lifts both
            places = await self._places_at_end(run_id, agent_id)
            hold = places
            if refused is not None:
                self._refused_live(run_id, refused, hold=places)
            elif failure is not None:
                def failed(r):
                    r["run"].update(state="failed", cause="error", reason=failure, ended_at=int(time.time()))
                    if places:
                        r["run"]["hold"] = places
                await asyncio.to_thread(agents._change, run_id, failed)
            if view is not None:
                ended_at = int(time.time())
                c = attempt.chat
                # something watched changed, during the attempt or between two: its agent held, as a run's is, whatever
                # else ended the attempt (an ungated end keeps its cause, and the change is still the owner's to see),
                # the hold on the run's record for the app (_watch_hold)
                hold = _with_places(_watch_hold(view), places)

                def ended(r):
                    run = r["run"]
                    run.update(state=view["state"], cause=view.get("cause") if view["state"] != "done" else None,
                               reason=view.get("reason"), task=view["task"], backend=view["backend"],
                               rate_limits=view["rate_limits"] or None, question=view.get("question"),
                               ended_at=ended_at, cost_est_usd=round(c.cost, 6), last_text=c.last_text,
                               head=(view["task"].get("end_state") or {}).get("head") or run.get("head"))
                    if view.get("last_words"):
                        run["last_words"] = view["last_words"][:agentry.MAX_REPORT]
                    if hold:
                        run["hold"] = hold
                await asyncio.to_thread(agents._change, run_id, ended)
            if hold:
                agents._hold(agent_id)
            try:
                await asyncio.to_thread(self._keep_seen, agent_id, task_id, getattr(attempt, "end_look", None))
            except Exception:  # noqa: BLE001  the next attempt's check 4 then compares with the look before
                pass
        finally:
            try:            # the attempt's own MCP servers file, should its end not have removed it (task_attempt.py)
                os.unlink(agents._servers_file(run_id))
            except (OSError, KeyError, agentry.AgentError):
                pass
            agents.working.discard(run_id)
            self.live.pop(run_id, None)
            agents.ended_at = time.monotonic()
            agents._ended.setdefault(run_id, asyncio.Event()).set()
            done = self.ended.get(run_id)
            if done is not None:
                done.set()
            agents.pump()

    def _refused_live(self, run_id, e, hold=None):
        check = getattr(e, "check", None)

        def ended(r):
            run = r["run"]
            task = dict(run.get("task") or {})
            task["resume"] = {"outcome": "blocked", "check": check, "found": getattr(e, "found", {}), "acks": 0}
            run.update(state="blocked", cause="resume", reason=str(e)[:agentry.MAX_TEXT], task=task,
                       ended_at=int(time.time()))
            if hold:
                run["hold"] = hold
        self.agents._change(run_id, ended)

    def _keep_seen(self, agent_id, task_id, seen):
        """A look something compared, kept as the task's watch's next baseline (_kept_look), never a look taken after
        it: what changes between that look and the next start's, by a process the last attempt left behind, is found
        there (check 4, "as the previous attempt's end found them").
        At an attempt's end it is the look its _finish took and compared with its start's; an attempt that ended
        before its own check 4 took neither, and nothing is kept, so the next start compares with the baseline as its
        start's look found it, and a change its precheck made after that look is found. After an owner's route that
        keeps one (a return taken, the worktree kept, whatever the keep commits, or dropped) it is the look its watch
        compared before its git ran (_watch_look_or_409). That git touches no watched place (git_places), a return's
        fetch starting no automatic gc (_bring), but one the repository's settings name inside the worktree (a file they
        include, or core.excludesFile), which its change of the worktree can change, and which the next start then
        finds changed."""
        if seen is None:
            return
        self._change_record(agent_id, task_id, lambda record: record.update(look=_kept_look(record, _plain(seen))))

    def stop(self, run_id, reason="stopped by you", cause=None):
        attempt = self.live.get(run_id)
        if attempt is not None:
            attempt.stop(cause, reason)

    async def wait_ended(self, run_id):
        done = self.ended.get(run_id)
        if done is not None:
            await done.wait()

    def message(self, run_id, text):
        attempt = self.live.get(run_id)
        if attempt is None:
            raise agentry.AgentError("This task's attempt is not running.")
        attempt.message(text)

    # a machine stop, at the workspace's next start

    def recover(self):
        """At the workspace's start, for each task's run the machine stopped under (agents._scan ended it blocked): its
        pending checkpoint or end state completed from the branch's head or dropped (task_attempt.complete_pending),
        and its calls left in flight settled, each by its class, git run only when the task's watched places are as
        its record kept them. Its record written again, which the app copies."""
        for record in self.agents.runs():
            run = record["run"]
            if (run.get("trigger") or {}).get("kind") != "task" or not isinstance(run.get("task"), dict):
                continue
            task = run["task"]
            pending = [e for e in task.get("checkpoints") or [] if isinstance(e, dict) and e.get("state") == "pending"]
            end = task.get("end_state") if isinstance(task.get("end_state"), dict) else None
            unsettled = [e for e in task.get("effects") or [] if isinstance(e, dict) and e.get("settled") is None
                         and not e.get("unseen") and e.get("kind") in shapes.OUTWARD + ("unclassified",)]
            if not pending and not (end and end.get("pending")) and not unsettled:
                continue
            task_id = run["trigger"]["id"]
            try:
                kept = self._read_record(record["agent_id"], task_id)
                if kept is None:
                    continue
                work = self.work(record["agent_id"], task_id)
                places = self._current_places(self.agents.agent(record["agent_id"]), task_id, kept)
                now = attempts.look(places)
                git_ok = attempts.look_digest(_plain(now)) == attempts.look_digest(
                    self.expected(kept.get("look"), places, now)) and self._places_allow(work)
            except Exception:  # noqa: BLE001  nothing of it is settled now: the owner resolves it
                continue
            branch = self.branch(task_id)
            for entry in pending:
                done = attempts.complete_pending(work, branch, entry) if git_ok else {"state": "dropped",
                                                                                     "head": None}
                entry.update(state=done["state"], head=done["head"])
            if end and end.get("pending"):
                done = attempts.complete_pending(work, branch, end) if git_ok else {"state": "dropped", "head": None}
                task["end_state"] = {"head": done["head"], "tree": end.get("tree"), "committed": True} \
                    if done["state"] == "complete" else None
            attempts.settle_effects(unsettled, work=work, git_ok=git_ok)
            for e in unsettled:
                if e.get("settled") is None:
                    e["settled"] = "unknown"
            self.agents._change(record["id"], lambda r, task=task: r["run"].update(task=task))

    # the routes

    def backends(self):
        """The backends a task can run on: each Claude Code account, the default first, and each key's provider. Each
        account's status is read anew, as a session's start reads it: the app keeps its readiness
        for the owner's choice, and its organisation is as auth status last gave it, so an answer kept for the pages'
        looks, from before a change made outside the workspace, is not theirs."""
        view = self.ws.accounts_view(status=True, fresh=True)["claude"]["accounts"]
        out = []
        for row in view:
            status = row.get("status") or {}
            usage = row.get("usage") or {}
            windows = [{"window": w, **(usage.get(w) if isinstance(usage.get(w), dict) else {}), "at": usage.get("at")}
                       for w in ("five_hour", "seven_day") if usage.get(w)]
            out.append({"ref": {"kind": "account", "id": row["id"]}, "label": row["name"],
                        "ready": status.get("signed_in") is True, "email": status.get("email"),
                        "plan": status.get("subscription"), "organization": self.ws.orgs.get(row["id"]),
                        "windows": windows})
        kept = providers.keys(os.path.expanduser("~"))
        for provider in shapes.KEY_PROVIDERS:
            label = (providers.CATALOG.get(provider) or providers.KEY_ONLY[provider])["label"]
            out.append({"ref": {"kind": "key", "provider": provider}, "label": label, "ready": provider in kept,
                        "email": None, "plan": None, "organization": None, "windows": []})
        return {"backends": out}

    def identity(self, account):
        """An account's identity for the owner to confirm (task_attempt.read_identity): 404 for no such account, 400
        when it is not signed in, 409 when another read of the same account is still running and this one cannot have
        its turn within IDENTITY_WAIT seconds. One account's identity is read by one start at a time: two
        reads at once are a sign-out and a sign-in seen half-way by one and not by the other, and a read slower than
        the app's deadline, which goes on here after the app has stopped waiting for it, overlapping the next page's."""
        if account in (None, "default"):
            env_extra, key = {}, "default"
        else:
            got = self.ws.accounts.get(account) if isinstance(account, str) else None
            if got is None or got["kind"] != "claude":
                raise agentry.AgentNotFound("No such account.")
            env_extra, key = self.ws.accounts.env(got), got["id"]
        with self._identity_guard:
            lock = self._identity_locks.setdefault(key, threading.Lock())
        if not lock.acquire(timeout=IDENTITY_WAIT):
            raise agentry.TaskConflict("Another read of this account's identity is still running: try again when it "
                                       "ends.")
        try:
            from .workspace import program_env
            claude = self.claude()
            if not claude:
                raise agentry.AgentError("The claude a task runs is not installed here.")
            env = program_env(env_extra)
            for name in shapes.REFUSED_ENV:
                env.pop(name, None)
            status = attempts._auth_status(claude, env, 30)
            if not isinstance(status, dict) or status.get("loggedIn") is not True:
                raise agentry.AgentError("That account is not signed in.")
            got = attempts.read_identity(claude, env)
            return {k: got[k] for k in ("report", "auth_status", "org_id", "mismatch")}
        finally:
            lock.release()

    def attempts_view(self, task_id):
        """The task's attempts this workspace knows, in the order they were queued, whether or not the task's folder is
        here yet: a first attempt has none until it starts, so one queued behind another run of its agent is listed
        too. They are the runs task_of holds for the task (set at a run's arrival, and from its record at the agent's
        start), in the order they were queued, each read as records_of reads one, a run whose record is gone left out as
        runs() leaves one out. {"attempts": []} where it knows none, which the app's poll reads as an attempt that never
        started here; AgentNotFound for a task id that is not one."""
        if not (isinstance(task_id, str) and shapes.TASK_ID.fullmatch(task_id)):
            raise agentry.AgentNotFound("No such task.")
        index = dict(self.agents._picked())     # every run this workspace has a record of, with when it was queued
        mine = [run_id for run_id, task in list(self.task_of.items()) if task == task_id]
        out = []
        for run_id in sorted(mine, key=lambda r: ((index.get(r) or {}).get("queued_at") or 0, r)):
            try:
                r = self.agents.read(run_id)
            except (agentry.AgentError, OSError, ValueError):
                continue
            out.append({"run_id": r["id"], "fence": (r["run"].get("task") or {}).get("fence"),
                        "state": r["run"].get("state")})
        return {"attempts": out}

    def _running(self, task_id):
        return [r for r, a in self.live.items() if self.task_of.get(r) == task_id]

    def _watch_or_409(self, agent_id, task_id):
        """The task's watched places, as they are now (an account removed since no longer compared, one added taken as
        it is), against the look its record kept: a change is 409, and no git runs. Returns the record."""
        return self._watch_look_or_409(agent_id, task_id)[0]

    def _watch_look_or_409(self, agent_id, task_id):
        """_watch_or_409's record, and the look it compared, which a route that keeps one (a return taken, the worktree
        kept or dropped) keeps once its git has run (_keep_seen)."""
        kept = self._read_record(agent_id, task_id)
        if kept is None:
            raise agentry.AgentNotFound("No such task here.")
        places = self._current_places(self.agents.agent(agent_id), task_id, kept)
        now = attempts.look(places)
        expected = self.expected(kept.get("look"), places, now)
        changed = watch.changes(expected, _plain(now))
        if changed:
            raise agentry.TaskConflict("The task's watched places changed since its last attempt ended: look, then "
                                       f"try again ({', '.join(changed[:5])}).")
        self._places_or_409(self.work(agent_id, task_id))
        return kept, now

    def _places_allow(self, where):
        """Whether the workspace's git may run in `where` now (workspace.check_places): not where git's
        places there, or outside every repository, changed while an agent's run worked, held until the owner resumes
        an agent held for them."""
        from .workspace import GitHeld, check_places
        try:
            check_places(where)
        except GitHeld:
            return False
        return True

    def _places_or_409(self, where):
        """A held place of git's (_places_allow) refuses a route's git as a changed watched place does: 409, with the
        hold's words, and no git runs."""
        from .workspace import GitHeld, check_places
        try:
            check_places(where)
        except GitHeld as e:
            raise agentry.TaskConflict(str(e)) from None

    def _latest_fence(self, agent_id, task_id):
        fences = [(r["run"].get("task") or {}).get("fence") for r in self.records_of(agent_id, task_id)]
        fences = [f for f in fences if _int(f)]
        return max(fences) if fences else None

    def worktree(self, task_id, body):
        """The owner's Resolve of the worktree, after the watch, in every case the resume check's check 5 sends the
        owner here (the worktree on another branch or none, at another head, or with other files).
        Keep: the worktree's files as they are now, built against the task's branch's own head as a
        checkpoint builds them and committed onto that head where they differ from it, wherever the worktree's HEAD is
        (on the branch, on another, or on none), and the worktree on that branch; refused only where that branch is
        gone. Discard: the task's branch and the worktree put at the head the app recorded, which must descend from the
        task's checkpoint 0. The new head is written into the task's last attempt's signed record, which the app copies
        as its end state."""
        from .workspace import Conflict, RUN_GIT, git
        body = body if isinstance(body, dict) else {}
        action, head = body.get("action"), body.get("head")
        if action not in shapes.WORKTREE_OUTCOMES:
            raise agentry.AgentError("Keep the worktree, or discard it.")
        if action == "discard" and not (isinstance(head, str) and shapes.COMMIT.fullmatch(head)):
            raise agentry.AgentError("Discarding goes back to the head the app recorded, a commit.")
        agent_id = self.agent_of_task(task_id)
        if self._running(task_id):
            raise agentry.TaskConflict("An attempt of this task is running.")
        fence = self._latest_fence(agent_id, task_id)
        if not _int(body.get("fence")) or body["fence"] != fence:
            raise Conflict("That is not the task's latest attempt.")
        record, seen = self._watch_look_or_409(agent_id, task_id)
        work = self.work(agent_id, task_id)
        ref = "refs/heads/" + self.branch(task_id)
        code, on = git("symbolic-ref", "-q", "HEAD", cwd=work, ok=(0, 1), env=RUN_GIT)
        on_branch = code == 0 and on.strip() == ref
        tip = attempts._commit_of(work, ref)            # the task's branch's head, wherever the worktree's HEAD is
        if action == "discard":
            code, _ = git("merge-base", "--is-ancestor", record["checkpoint0"], head, cwd=work, ok=(0, 1, 128),
                          env=RUN_GIT)
            if code != 0:
                raise agentry.TaskConflict("That head does not descend from the task's checkpoint 0.")
            # the worktree's HEAD onto the task's branch (where an agent's git left it on another branch or none, or the
            # branch is gone), then the branch, its index and its files at that head (reset moves the branch HEAD names)
            if not on_branch:
                git("symbolic-ref", "HEAD", ref, cwd=work, env=RUN_GIT)
            git("reset", "--quiet", "--hard", head, cwd=work, env=RUN_GIT)
            git("clean", "-fdq", cwd=work, env=RUN_GIT)
            tree = attempts._tree_of(work, head)
            result = {"head": head, "tree": tree, "committed": False}
        else:
            # kept on the task's branch, onto its own head wherever the worktree's HEAD is (on the branch, on another,
            # or on none), and built against that head: never onto a commit of another branch, nor with a path the
            # person's excludes match taken as such a commit has it,
            # so the task's history stays its own
            parent = tip
            if parent is None:
                raise agentry.TaskConflict("The task's branch is gone: discard it to the head the app recorded.")
            built = attempts.build_tree(attempts._git_dir(work), work, parent, excludes=attempts.exclude_files(work))
            if built["tree"] == attempts._tree_of(work, parent):
                commit, committed = parent, False
            else:
                author = self.agents.agent(agent_id)["author"]
                name, email = author["name"], author["email"]
                env = dict(RUN_GIT, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name,
                           GIT_COMMITTER_EMAIL=email)
                _, commit = git("commit-tree", built["tree"], "-p", parent, cwd=work, env=env,
                                input=f"Task {task_id}: kept by its owner\n".encode("utf-8"))
                commit, committed = commit.strip(), True
                git("update-ref", ref, commit, parent, cwd=work, env=RUN_GIT)
            if not on_branch:
                git("symbolic-ref", "HEAD", ref, cwd=work, env=RUN_GIT)
            # the index as the new head has it; the files are left as they are
            git("read-tree", "HEAD", cwd=work, env=RUN_GIT)
            result = {"head": commit, "tree": built["tree"], "committed": committed}
            if committed:
                result["left_out"] = built["left_out"]
        latest = [r for r in self.records_of(agent_id, task_id)
                  if (r["run"].get("task") or {}).get("fence") == fence][-1]

        def kept(r):
            task = dict(r["run"].get("task") or {})
            task["end_state"] = dict(result, resolved=action)
            r["run"]["task"] = task
            r["run"]["head"] = result["head"]
        written = self.agents._change(latest["id"], kept)
        self._change_record(agent_id, task_id, lambda rec: rec.update(head=result["head"]))
        self._keep_seen(agent_id, task_id, seen)
        return {"head": result["head"], "tree": result["tree"], "record_seq": written["run"]["seq"]}

    def delete(self, task_id, branch=False):
        """The task's worktree and folder gone (its runs' records stay); refused while an attempt runs. Its branch
        stays, as the owner's own deletion of a task keeps it, unless `branch` asks for it to go too: for a worktree an
        acceptance made that no task came of (task_offers._forget_worktree), the branch is
        deleted while it is still at the task's checkpoint 0, where nothing worked on it, and kept otherwise. With
        `branch`, the answer says whether it went."""
        from .workspace import RUN_GIT, git
        agent_id = self.agent_of_task(task_id)
        if self._running(task_id) or any(
                r["run"].get("state") in agentry.LIVE + ("queued",) for r in self.records_of(agent_id, task_id)):
            raise agentry.TaskConflict("An attempt of this task is queued or running.")
        record = self._read_record(agent_id, task_id)
        work = self.work(agent_id, task_id)
        if record is not None and os.path.isdir(work):
            try:
                git("worktree", "remove", "--force", work, cwd=self.ws._repo_path(record["repo"]), env=RUN_GIT)
            except Exception:  # noqa: BLE001  a worktree git no longer knows is removed as a folder
                pass
        out = {"removed": True}
        if branch:
            out["branch_removed"] = False
            if record is not None:
                try:
                    # deleted only from where it was made (update-ref's old value): a branch moved since stays
                    git("update-ref", "-d", "refs/heads/" + self.branch(task_id), record["checkpoint0"],
                        cwd=self.ws._repo_path(record["repo"]), env=RUN_GIT)
                    out["branch_removed"] = True
                except Exception:  # noqa: BLE001  a branch moved, or a repository git may not run in now, stays
                    pass
        self.agents._git_forget(work)
        shutil.rmtree(self.folder(agent_id, task_id), ignore_errors=True)
        return out

    def push(self, task_id, body):
        """A hand-off's push: after the watch, the branch's head checked against the one the app
        recorded, what the commits since `since` would carry scanned for secret shapes (_scan_all: the lines they add,
        the paths they add and the commit objects themselves, none of a value named), then the recorded commit pushed,
        by its hash, to the task's branch at the address given (the one recorded when the task was made), its intent
        kept first. `lifted` names by their ids the findings the person pushing has lifted,
        at most LIFTED_MAX: those hold nothing, and where the scan lets the push through, the answer's
        lifted_passed names, once each, those the range met ([] where it holds it). `findings_key`, 64 hex, is the key
        the app made for the person pushing, so that an id names the same finding however often this workspace's own
        secret is made again; without one, the workspace's own
        (findings_key()). A finding not lifted, or anything the scan could not read, pushes nothing: secret_shape names
        the first, as {shape, file}, and secret_findings each finding, up to FINDINGS_MAX (none for what the scan could
        not read, which no lift reaches). The push carries no tag: --no-follow-tags, whatever the owner's
        push.followTags says, which would have it carry an annotated tag on a commit it pushes, and a tag's message,
        which nothing scans. With `ref`, another task's branch, the commit goes there instead: a
        received task's work, pushed to the owner's task's branch (what the teammate's side pushes); the
        head checked is still the task's own branch's."""
        from .workspace import RUN_GIT, git
        body = body if isinstance(body, dict) else {}
        address, commit, since = body.get("address"), body.get("commit"), body.get("since")
        call_id, branch_head, target = body.get("call_id"), body.get("branch_head"), body.get("ref")
        lifted, key = body.get("lifted", []), body.get("findings_key")
        if not (isinstance(address, str) and 0 < len(address) <= MAX_ADDRESS and not address.startswith("-")
                and not agentry._CONTROL.search(address)):
            raise agentry.AgentError("A push goes to the address recorded for the task.")
        if target is not None and not (isinstance(target, str) and TASK_REF.fullmatch(target)):
            raise agentry.AgentError("A push goes to a task's branch.")
        for name, value in (("commit", commit), ("since", since), ("branch_head", branch_head)):
            if not (isinstance(value, str) and shapes.COMMIT.fullmatch(value)):
                raise agentry.AgentError(f"A push names its {name} by a commit's id.")
        if not (isinstance(call_id, str) and 0 < len(call_id) <= 200 and not agentry._CONTROL.search(call_id)):
            raise agentry.AgentError("A push names the effect it is.")
        if not (isinstance(lifted, list) and len(lifted) <= LIFTED_MAX
                and all(isinstance(f, str) and _FINDING.fullmatch(f) for f in lifted)):
            raise agentry.AgentError("A push names the findings lifted by their ids.")
        if key is not None and not (isinstance(key, str) and _KEY_HEX.fullmatch(key)):
            raise agentry.AgentError("A push names its findings' key as 64 hex.")
        agent_id = self.agent_of_task(task_id)
        if self._running(task_id):
            raise agentry.TaskConflict("An attempt of this task is running.")
        self._watch_or_409(agent_id, task_id)
        work = self.work(agent_id, task_id)
        ref = "refs/heads/" + self.branch(task_id)
        now = attempts._commit_of(work, ref)
        if now != branch_head:
            raise agentry.TaskConflict("The task's branch moved since the app recorded it: nothing is pushed.")
        code, _ = git("merge-base", "--is-ancestor", commit, now, cwd=work, ok=(0, 1, 128), env=RUN_GIT)
        if code != 0:
            raise agentry.TaskConflict("That commit is not on the task's branch: nothing is pushed.")
        scan = self._scan_all(work, since, commit, lifted, bytes.fromhex(key) if key else self.findings_key())
        to = target or ref
        held = scan["unread"] or (scan["findings"][0] if scan["findings"] else None)
        if held is not None:
            return {"pushed": False, "ref": to, "before": None, "after": None,
                    "secret_shape": {"shape": held["shape"], "file": held["file"]},
                    "secret_findings": scan["findings"], "lifted_passed": []}
        before = attempts.remote_ref(work, address, to)
        self._change_record(agent_id, task_id, lambda rec: rec["pushes"].update(
            {call_id: {"address": address, "ref": to, "commit": commit, "before": before, "at": int(time.time())}}),
            durable=True)
        git("push", "--quiet", "--no-verify", "--no-follow-tags", "--", address, f"{commit}:{to}", cwd=work,
            ok=(0, 1, 128), env=RUN_GIT, timeout=300)
        after = attempts.remote_ref(work, address, to)
        return {"pushed": after == commit, "ref": to, "before": before, "after": after, "secret_shape": None,
                "secret_findings": [], "lifted_passed": scan["passed"]}

    def findings_key(self):
        """The key of a push's findings' ids (finding_id) where the app gives none: made from this workspace's records'
        key, in no file a run can read (agents.records_key), and the same while its secret is (the fly provider makes
        a new one when it replaces the workspace's machine, so the app gives its own key with each push)."""
        return hmac.new(self.agents._key, b"proofpane secret findings", hashlib.sha256).digest()

    def _scan(self, work, since, commit):
        """The first thing that holds a push of the commits from `since` to `commit`, as {shape, file}, else None:
        _scan_all's, with nothing lifted."""
        got = TaskRuns._scan_all(work, since, commit)
        held = got["unread"] or (got["findings"][0] if got["findings"] else None)
        return {"shape": held["shape"], "file": held["file"]} if held is not None else None

    @staticmethod
    def _scan_all(work, since, commit, lifted=(), key=None):
        """What would hold a push of the commits from `since` to `commit`: {unread, findings, passed}. `findings` are
        the secret shapes found that `lifted` does not name, each once, up to FINDINGS_MAX, each {kind, shape, file,
        line, place, column, commit, finding}, one for each place a shape begins (secret_matches): the kind is "line",
        a line a commit adds, in `file`; "name", a path a commit adds, as `file`; or "commit", a line of a commit object
        (its message, its author or committer, or any other header: an encoding, a signature, a merged tag's own
        object), with no file. `file` is the path with each part that holds any of a value the scan finds in it hidden
        (masked), written so any reader can carry it and cut at SHOWN_MOST characters (shown); `line` is, for "line",
        the line's number in the file as the commit leaves it, and for "commit", the line's number in the commit object
        (as git cat-file commit shows it); `place` is, for "line" and "name", the path's place among those the commit
        changes: the number of the entry that first names it, as git diff-tree -r -m --no-renames
        --ignore-submodules=none --root lists the commit's entries, a merge's against each parent in turn, counted on
        through them. Each is counted from 1, so that two findings of one shape in one commit read apart, in one file or
        in two whose paths read the same once a part is hidden.
        `column` is where on its line or path the shape begins, counted from 1; `finding` is the
        finding's id under `key` (finding_id), which names its kind, path, text, shape and column and no commit, line or
        place. `passed` names, once each, the lifted ids the range met. `unread` is {shape, file} for what the scan
        could not read, which holds the push and is no finding: a git command that fails, an output longer than
        MAX_SCAN, a file git shows as binary, or the scan's time running out (SCAN_SECONDS, for its git commands and its
        reading of what they give, together: looked at before each git command, each line and path it reads and each
        place on a line, while a long reading runs, while it says a path, and as it answers);
        then nothing else is reported. One search of a line or a path by one
        pattern, which runs in C, is not looked into, nor one decoding of git's output: each is linear in what it reads,
        3.4 s at most for 64 MiB here (secret_matches). It holds one git command's output at a time, with that output's
        text, and copies no line it reads but a path's. Each command's output is read
        through a pipe, MAX_SCAN and one byte at most, and a command with more to say is ended there, unread past that
        byte (_git_output): an output longer than MAX_SCAN is known by that byte,
        whatever the command's status, which says nothing of an output read only in part. So a command that fails
        after saying more than MAX_SCAN holds the push as too large to scan, where before its failure was looked at
        first and held it as unread; either holds it.

        Each commit from `since` to `commit` is read, not only the change from the one to the other, so a secret one
        checkpoint wrote and a later one took out is read; a merge is read against each of its parents, and a root
        commit against nothing. Every file of the repository's own is read as text, one git would show as binary by its
        bytes or by an attribute the worktree sets among them, and every line a hunk adds is read whole, whatever it
        begins with and whatever characters it holds; a renamed file's lines are read as a new file's (--no-renames: git
        log finds renames by default, diff.renames set or not, and shows only the lines a renamed file changed).
        The commits are listed by git rev-list; each commit object is read
        whole as git keeps it (cat-file --batch), and each path a commit adds (diff-tree --stdin -z --no-renames, its
        status A: a renamed or copied file adds its new path) as its bytes, which a patch's own header quotes where a
        path holds a tab or a byte past ASCII and ends with a tab where it holds a space (each read back to the path),
        and which an empty new file has no "+++" line for. What is held other than as ASCII text (UTF-16, a compressed
        file, a message in another encoding) is not seen. No tag is read, which the push does not carry
        (--no-follow-tags), nor a commit before `since`, which the push
        carries where the remote does not hold it. The range rests on `since` and `commit` alone, which the route
        checks, never on a ref of the repository's, which nothing watches and which need not be the remote's the push
        goes to: no ref is left out, and a replace ref, which the push's own packing does not follow, does not stand in
        for a commit (--no-replace-objects, for each command). So the commits a task took in from its default branch are
        read too, and a secret shape one of them added, kept or not, holds the push, though the remote may hold it
        already, until it is lifted. And a merge read against its default branch's
        parent shows the lines and the paths the merge holds that the default branch does not, the task's own side's
        from before `since` among them, which no other commit of the range shows: a secret shape there holds every push
        whose range takes in a merge that kept it, though a later commit removes it, while the branch keeps that merge
        and until it is lifted: a branch put back to a commit from before the merge is clear of it
        (--diff-merges=first-parent would not show those lines in a merge whose first parent is the task's side, as in
        the task's own merge of its default branch).
        And a secret shape on a line a commit of the range shows as added holds the push, until
        it is lifted, whoever first wrote that line, one from before `since` among them: a line a commit moves within
        its file, where git's diff shows that line as the one moved rather than the lines it passes, as diff.algorithm
        chooses, a setting that does apply (the lines a, s, c, a, s, s, a of a file, where s is a line with a secret
        shape, made c, s, a, s, a, c: held under histogram, not under git's default, myers, minimal or patience),
        and every line of a file a commit renames, a plain git mv among them, whatever diff.renames says (--no-renames,
        above). How git's own settings
        would show a merge, a root commit, a submodule, a rename or the lines around a hunk's, name a patch's paths, or
        leave a submodule out (log.diffMerges,
        log.showRoot, diff.submodule, diff.renames, diff.context, diff.interHunkContext, diff.noprefix,
        diff.ignoreSubmodules, and submodule.<name>.ignore in the repository's config or in the .gitmodules its worktree
        or index holds) does not apply: the flags name each (--ignore-submodules=none: under ignore = all git left out a
        submodule whose path was a token, and moved the places of the paths after it).
        A submodule's change is read as the push carries it, the submodule's commit id
        (--submodule=short): under the owner's diff.submodule=diff git would show it as that repository's own diff, its
        files read with its own settings and any external diff, diff driver or textconv they name run in this git, and a
        file it showed as binary held the push for content the push does not carry.
        Its git runs as the workspace's own reads do (workspace._git_env: git's places checked
        first, RUN_GIT's pins, no lazy fetch, no transport), and runs no external diff, diff driver or textconv, the
        repository's own or a submodule's, and verifies no signature (--no-show-signature, whatever log.showSignature
        says, which RUN_GIT pins off too: a verification runs the program a signature's form names, gpg.program or
        gpg.openpgp.program, gpg.x509.program, or gpg.ssh.program)."""
        from .workspace import _git_env
        env = _git_env(work)
        deadline = time.monotonic() + SCAN_SECONDS
        found = _Findings(lifted, key or _SCAN_KEY, deadline)

        def run(args, given=None):
            # its output MAX_SCAN and one byte at most, so each caller looks at the length before the status, which
            # says nothing of an output read only in part (a command ended at that byte was killed there, if still
            # running)
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(args, 0)
            return _git_output(["git", "--no-replace-objects", *args], cwd=work, env=env, timeout=left, limit=MAX_SCAN,
                               input=given)

        def unread(shape, file=None):
            try:
                return found.result({"shape": shape, "file": found.said(file)})
            except _OutOfTime:
                found.out_of_time = True
                return found.result()

        try:
            p = run(["log", "--no-color", "--no-ext-diff", "--no-textconv", "--no-show-signature", "--text",
                     "--unified=0", "--inter-hunk-context=0", "--no-renames", "-p", "--diff-merges=separate", "--root",
                     "--submodule=short", "--ignore-submodules=none", "--src-prefix=a/", "--dst-prefix=b/",
                     "--format=%x00%H", "--end-of-options", f"{since}..{commit}", "--"])
        except subprocess.TimeoutExpired:
            return unread(UNREAD)
        if len(p.stdout) > MAX_SCAN:
            return unread(TOO_LARGE)
        if p.returncode != 0:
            return unread(UNREAD)
        # git's lines, each read in place, so that no line is copied but a path (any other break is in one line)
        out = p.stdout.decode("utf-8", "surrogateescape")
        del p
        at, current, hunk, number, pos, full = None, None, False, None, 0, False
        while pos <= len(out) and not full:
            if found.late():
                return found.result()
            stop = out.find("\n", pos)
            stop = len(out) if stop < 0 else stop
            if out.startswith("\x00", pos, stop):
                at, current, hunk = out[pos + 1:stop], None, False  # a commit's own line (%x00%H): no file's line begins so
            elif out.startswith("diff --git ", pos, stop):
                current, hunk = None, False          # a file's header: read up to its first hunk
            elif out.startswith("@@", pos, stop):
                # with no lines kept, which --unified=0 and --inter-hunk-context=0 show none of whatever git's settings
                # say, a hunk's added lines are the new file's from its
                # header's "+" on, in order
                hunk, head = True, _HUNK.match(out, pos, stop)
                number = int(head.group(1)) if head and int(head.group(1)) > 0 else None
            elif hunk:
                if out.startswith("+", pos, stop):   # an added line, "+++" or "++ " at its start among them
                    full = found.add("line", current, out, at, number, pos + 1, stop)
                    number = number + 1 if number else None
            elif out.startswith("+++ ", pos, stop):
                # git ends this header with a tab where its path holds a space, a tab no path it leaves unquoted holds;
                # a quoted path is read back to its bytes, so a shape in it is hidden
                line = out[pos:stop]
                try:
                    path = _unquoted(line[4:-1] if line.endswith("\t") else line[4:], deadline)
                except _OutOfTime:
                    found.out_of_time = True
                    return found.result()
                current = path[2:] if path.startswith("b/") else path
            elif out.startswith("Binary files ", pos, stop):
                return unread(BINARY, current or out[pos + len("Binary files "):stop])
            pos = stop + 1
        del out
        if found.out_of_time:
            return found.result()
        try:
            p = run(["rev-list", "--end-of-options", f"{since}..{commit}", "--"])
            if len(p.stdout) > MAX_SCAN:
                return unread(TOO_LARGE)
            if p.returncode != 0:
                return unread(UNREAD)
            commits = p.stdout.split()
            if not all(_OBJECT.fullmatch(c) for c in commits):
                return unread(UNREAD)
            if not commits:
                return found.result()
            listed = b"\n".join(commits) + b"\n"
            names = run(["diff-tree", "--stdin", "-r", "-z", "-m", "--root", "--no-renames", "--ignore-submodules=none",
                         "--no-abbrev", "--raw"], listed)
        except subprocess.TimeoutExpired:
            return unread(UNREAD)
        if len(names.stdout) > MAX_SCAN:
            return unread(TOO_LARGE)
        if names.returncode != 0:
            return unread(UNREAD)
        # each path a commit changes: "<commit>\0" before each block of its entries (a merge's once for each parent),
        # each entry ":<modes> <ids> <status>\0<path>\0", and a second path after a rename's or a copy's (--no-renames
        # makes none). Its place is the number of the entry that first names it, counted from 1 on through the
        # commit's blocks; a line's finding is given its path's place here, and each path the commit adds is read
        wanted = {}
        for f, path in zip(found.found, found.paths):
            if f["kind"] == "line":
                wanted.setdefault((f["commit"], path), []).append(f)
        out, pos, at, entry, first = names.stdout, 0, None, 0, {}
        del names
        while pos < len(out):
            if found.late():
                return found.result()
            stop = out.find(b"\0", pos)
            stop = len(out) if stop < 0 else stop
            part, pos = out[pos:stop], stop + 1
            if not part:
                continue
            if _OBJECT.fullmatch(part):
                if part.decode("ascii") != at:
                    at, entry, first = part.decode("ascii"), 0, {}
                continue
            if not part.startswith(b":") or at is None:
                return unread(UNREAD)
            status, paths = part.rsplit(b" ", 1)[-1][:1], []
            for _ in range(2 if status in (b"R", b"C") else 1):
                stop = out.find(b"\0", pos)
                stop = len(out) if stop < 0 else stop
                paths.append(out[pos:stop])
                pos = stop + 1
            if not all(paths):
                return unread(UNREAD)
            path = paths[-1].decode("utf-8", "surrogateescape")
            entry += 1
            place = first.setdefault(path, entry)
            for f in wanted.pop((at, path), ()):
                f["place"] = place
            if status == b"A" and not full:
                full = found.add("name", path, path, at, None, place=place)
        del out
        if found.out_of_time or full:
            return found.result()
        try:
            objects = run(["cat-file", "--batch"], listed)
        except subprocess.TimeoutExpired:
            return unread(UNREAD)
        if len(objects.stdout) > MAX_SCAN:
            return unread(TOO_LARGE)
        if objects.returncode != 0:
            return unread(UNREAD)
        # each commit object as git keeps it: "<id> commit <size>\n", its bytes, "\n"; its lines read in place
        out, pos = objects.stdout, 0
        del objects
        for c in commits:
            end = out.find(b"\n", pos)
            head = out[pos:end].split(b" ") if end >= 0 else []
            if len(head) != 3 or head[0] != c or head[1] != b"commit" or not head[2].isdigit():
                return unread(UNREAD)
            size = int(head[2])
            if out[end + 1 + size:end + 2 + size] != b"\n":
                return unread(UNREAD)
            text = str(memoryview(out)[end + 1:end + 1 + size], "utf-8", "surrogateescape")
            pos, at, number = end + 2 + size, 0, 1
            while at <= len(text):
                if found.late():
                    return found.result()
                stop = text.find("\n", at)
                stop = len(text) if stop < 0 else stop
                if found.add("commit", None, text, c.decode("ascii"), number, at, stop):
                    return found.result()
                at, number = stop + 1, number + 1
        if pos != len(out):
            return unread(UNREAD)
        return found.result()               # past its time, as one that ran out of it (result looks at the clock)

    def fetch(self, task_id, body):
        """A fetch: an offer's view, an acceptance (the new task's worktree made at the offered commit,
        its checkpoint 0, in a repository whose origin is the address) or a return (the returned commit, which must
        descend from the handed one, fast-forwarded onto the owner's task's branch), each after the watch. With no base,
        the list of the files a next run would load is compared, for the teammate's (a view where the task is not, or
        an acceptance), with the named agent's own start, or, for a view naming no agent, with the remote's default
        branch as last fetched, or else with nothing (_compare_with); for a view where the task is, custody's among
        them, with the repository's default branch as this workspace has it (_default_head), whichever agent it names.
        A view's descends and protected paths are measured against that default branch wherever it is. Returns
        {commit, descends, loaded_files, protected_touched, steps, tree}: tree the commit's tree once a return has put
        the worktree on it, else None."""
        from .workspace import RUN_GIT, git
        body = body if isinstance(body, dict) else {}
        address, commit, purpose = body.get("address"), body.get("commit"), body.get("purpose")
        base = body.get("base")
        protected = body.get("protected") or []
        if purpose not in PURPOSES:
            raise agentry.AgentError("A fetch is for an offer's view, an acceptance or a return.")
        if not (isinstance(address, str) and 0 < len(address) <= MAX_ADDRESS and not address.startswith("-")
                and not agentry._CONTROL.search(address)):
            raise agentry.AgentError("A fetch reads from the task's recorded address.")
        if not (isinstance(commit, str) and shapes.COMMIT.fullmatch(commit)):
            raise agentry.AgentError("A fetch names its commit by its id.")
        if base is not None and not (isinstance(base, str) and shapes.COMMIT.fullmatch(base)):
            raise agentry.AgentError("A fetch's base is a commit's id, or none.")
        if not _strings(protected, shapes.LIST_MAX, shapes.LIST_ITEM_MAX):
            raise agentry.AgentError("A task's protected paths are at most 20 lines of plain text.")
        named = body.get("agent")
        starter = None              # the teammate's agent whose own start their list is compared with (_compare_with)
        if purpose == "accept":
            agent = self.agents.agent(named)
            if os.path.exists(self.folder(agent["id"], task_id)):
                raise agentry.TaskConflict("This task is here already.")
            # git runs here in its repository: not while an agent on it is held for a change the owner has not looked
            # at, or paused, or has a run working there
            self._quiet(self._sharing(agent))
            path = self.ws._repo_path(agent["repo"] or self.agents._scratch(agent["id"]))
            where, agent_id, starter = path, agent["id"], agent
        elif purpose == "view" and not self._here(task_id):
            # an offer's view where the task is not, the teammate's workspace: in the repository of the
            # agent the body names, or else the repository here whose origin is the task's address. Custody's view
            # (task_offers) is of the owner's own task, whose recorded commit an attempt here made, so it takes the
            # branch below; were that task gone from here, the push that follows the view would refuse it (no such
            # task here), and custody would hand nothing over, whatever this view listed
            where = self._viewing_repo(named, address)
            agent_id = None
            starter = self.agents.agent(named) if named is not None else None
        else:
            agent_id = self.agent_of_task(task_id)
            if self._running(task_id):
                raise agentry.TaskConflict("An attempt of this task is running.")
            kept, seen = self._watch_look_or_409(agent_id, task_id)
            where = self.work(agent_id, task_id)
            if purpose == "view":
                # the default branch read in the repository, never in the task's worktree, whose HEAD is the task's own
                # branch; the agent the view names (custody's names the task's own) is not read
                path = self.ws._repo_path(kept["repo"])
        # nor where the record of git's places holds that repository (_places_or_409): git should not run there now, a
        # 409 that refuses an acceptance alone, as an agent on it held does
        self._places_or_409(where)
        if purpose == "accept":
            # the repository an acceptance fetches into is the task's own, its origin the offer's address however it is
            # written, not one that only has its name: checked before anything is fetched into it
            self._origin_or_409(where, address)
        self._bring(where, address, commit)
        against = loaded_base = base
        if purpose == "view" and not base:
            # what the offered commit descends from and the protected paths it touches, and its list where the task is
            # (custody's view), against the repository's default branch as this workspace has it, never
            # against the commit itself
            against = loaded_base = self._default_head(where if agent_id is None else path)
            if agent_id is None:
                # the teammate's list: against the named agent's own start,
                # or where no agent is named the remote's default branch as last fetched (_compare_with)
                loaded_base = self._compare_with(starter, where)
        elif purpose == "accept" and not base:
            # an acceptance names no base either, and the app holds it until the teammate has seen this same list
            # (task_offers' accept; against the commit itself it would be empty): its loaded files
            # against the receiving agent's own start, as the teammate's view naming that agent compares them, while
            # what it descends from and the protected paths it touches stay as no base gives them
            loaded_base = self._compare_with(starter, path)
        if purpose in ("view", "accept") and not base and loaded_base is None:
            # nothing to compare with (the named agent's start names no commit; no agent is named and the remote's
            # default branch was never fetched, as in a repository cloned while its remote was empty; or, where the task
            # is, neither origin's HEAD nor the checkout's names one): every file a next run would load that the offered
            # commit holds, against git's empty tree, never against the commit itself, which lists none;
            # what it descends from and the protected paths it touches are measured as above
            loaded_base = self._empty_tree(where)
        descends = True
        if against:
            code, _ = git("merge-base", "--is-ancestor", against, commit, cwd=where, ok=(0, 1, 128), env=RUN_GIT)
            descends = code == 0
        if purpose == "return" and not descends:
            raise agentry.TaskConflict("The returned commit does not descend from the one handed.")
        since = against or commit
        incoming = attempts.check_incoming(where, commit, since=since, protected=protected, base=loaded_base)
        steps = _steps_done(where, base, commit) if base else []
        tree = None
        if purpose == "accept":
            agent = self.agents.agent(agent_id)
            self._make(agent, task_id, commit)
        elif purpose == "return":
            work = self.work(agent_id, task_id)
            ref = "refs/heads/" + self.branch(task_id)
            head = attempts._commit_of(work, ref)
            code, _ = git("merge-base", "--is-ancestor", head, commit, cwd=work, ok=(0, 1, 128), env=RUN_GIT)
            if code != 0:
                raise agentry.TaskConflict("The task's branch moved past what was handed: nothing is taken.")
            git("update-ref", ref, commit, head, cwd=work, env=RUN_GIT)
            git("read-tree", "-u", "--reset", "HEAD", cwd=work, env=RUN_GIT)
            self._change_record(agent_id, task_id, lambda rec: rec.update(head=commit))
            self._keep_seen(agent_id, task_id, seen)
            # the tree the return left the worktree at, which the app keeps with the commit as the task's end state,
            # so that the next attempt's resume check expects both (its expect is {head, tree})
            tree = attempts._tree_of(work, commit)
        return {"commit": commit, "descends": descends, "loaded_files": incoming["loaded_changed"],
                "protected_touched": incoming["protected_touched"], "steps": steps, "tree": tree}

    def _here(self, task_id):
        """Whether the task is this workspace's (AgentNotFound for a task id that is not one)."""
        if not (isinstance(task_id, str) and shapes.TASK_ID.fullmatch(task_id)):
            raise agentry.AgentNotFound("No such task.")
        try:
            self.agent_of_task(task_id)
        except agentry.AgentNotFound:
            return False
        return True

    def _viewing_repo(self, agent_id, address):
        """The repository an offer is viewed in where its task is not: the named agent's, or else the one here whose
        origin is the task's address (the same repository however its address is written: scheme, user, a final .git
        or slash aside). AgentNotFound when there is none; TaskConflict where git should not run there now (_quiet),
        since the view's fetch runs git there, as an acceptance's does."""
        from .workspace import GitHeld, RUN_GIT, git
        if agent_id is not None:
            agent = self.agents.agent(agent_id)
            self._quiet(self._sharing(agent))
            return self.ws._repo_path(agent["repo"] or self.agents._scratch(agent["id"]))
        want = _repo_key(address)
        held = None
        for name in sorted(os.listdir(self.ws.repos_dir)):
            path = os.path.join(self.ws.repos_dir, name)
            if not os.path.isdir(os.path.join(path, ".git")):
                continue
            try:
                code, url = git("config", "--get", "remote.origin.url", cwd=path, ok=(0, 1), env=RUN_GIT)
            except GitHeld as e:
                # a repository the record of git's places holds, whose origin no git may read now: passed over, and
                # the 409 of git that should not run there (_places_or_409) should no other have the address
                held = held or e
                continue
            if code == 0 and url.strip() and _repo_key(url.strip()) == want:
                self._quiet(self._agents_on(name))
                return path
        if held is not None:
            raise agentry.TaskConflict(str(held))
        raise agentry.AgentNotFound("No repository here has the task's address.")

    def _sharing(self, agent):
        """The agents whose repository is `agent`'s (itself among them; itself alone on its own scratch one)."""
        return self._agents_on(agent["repo"]) if agent["repo"] else [agent]

    def _quiet(self, agents):
        """TaskConflict where git should not run in the repository `agents` work on now: one of them paused, or held
        for a change its owner has not looked at yet, or a run of one working, whose change the watch sees only at its
        end."""
        if any(a["paused"] for a in agents):
            raise agentry.TaskConflict("An agent on that repository is paused or held: resume it, then try again.")
        ids = {a["id"] for a in agents}
        if any(m["agent_id"] in ids for _, m in self.agents._picked(states=agentry.LIVE)):
            raise agentry.TaskConflict("A run on that repository is working: try again once it ends.")

    def _agents_on(self, repo):
        """The agents here whose repository is `repo`, by its name."""
        out = []
        for name in agentry._names(self.agents.root):
            if not agentry._ID.match(name):
                continue
            try:
                agent = self.agents.agent(name)
            except agentry.AgentError:
                continue
            if agent["repo"] == repo:
                out.append(agent)
        return out

    def _default_head(self, path):
        """The commit of the repository's default branch as this workspace has it: its origin's HEAD, else its own."""
        from .workspace import RUN_GIT, git
        for ref in ("refs/remotes/origin/HEAD", "HEAD"):
            code, sha = git("rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}", cwd=path,
                            ok=(0, 1), env=RUN_GIT)
            if code == 0 and sha.strip():
                return sha.strip()
        return None

    def _compare_with(self, agent, path):
        """The commit the teammate's list with no base is compared with, in the repository at `path`:
        the named agent's own start, its base_ref or the checkout's
        HEAD where it has none, as the repository here has it, as its own runs resolve it (agents.py, _prepare: neither
        fetches); where no agent is named, the remote's default branch as this workspace last fetched it (origin's
        HEAD), never the checkout's HEAD, which may be any branch or none; None where neither names a commit, so that
        the list holds every file a next run would load that the commit holds."""
        from .workspace import RUN_GIT, git
        ref = (agent["base_ref"] or "HEAD") if agent is not None else "refs/remotes/origin/HEAD"
        code, sha = git("rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}", cwd=path, ok=(0, 1),
                        env=RUN_GIT)
        return sha.strip() if code == 0 and sha.strip() else None

    @staticmethod
    def _empty_tree(path):
        """git's empty tree, by its id in the object format of the repository at `path`: git knows it whether or not it
        is stored, so nothing is written."""
        from .workspace import RUN_GIT, git
        _, tree = git("hash-object", "-t", "tree", "--stdin", cwd=path, env=RUN_GIT)
        return tree.strip()

    def _origin_or_409(self, path, address):
        """TaskConflict, in the app's own words, unless the repository at `path` has the address as its origin, however
        it is written (_repo_key, as a view where its task is not finds its repository): an acceptance fetches into the
        task's own repository, never into another that only has its name. The origin is read as _viewing_repo reads
        one; a repository the record of git's places holds is a 409 too, with the hold's words."""
        from .workspace import GitHeld, RUN_GIT, git
        try:
            code, url = git("config", "--get", "remote.origin.url", cwd=path, ok=(0, 1), env=RUN_GIT)
        except GitHeld as e:
            raise agentry.TaskConflict(str(e)) from None
        if code != 0 or not url.strip() or _repo_key(url.strip()) != _repo_key(address):
            raise agentry.TaskConflict(NOT_THE_TASKS)

    def _bring(self, where, address, commit):
        """The commit fetched from the address into the repository at `where`, by its hash; else the address's task
        branches fetched and the commit looked for there. AgentError when it is not brought."""
        from .workspace import RUN_GIT, git
        # no automatic gc after it (--no-auto-gc): its repack would write the common folder's info, a watched place,
        # where the route's watch has already looked and keeps that look (_keep_seen)
        code, _ = git("fetch", "--quiet", "--no-tags", "--no-auto-gc", "--", address, commit, cwd=where,
                      ok=(0, 1, 128), env=RUN_GIT, timeout=300)
        if code != 0:
            git("fetch", "--quiet", "--no-tags", "--no-auto-gc", "--", address, f"+refs/heads/pp/*:{FETCHED}*",
                cwd=where, ok=(0, 1, 128), env=RUN_GIT, timeout=300)
        if attempts._commit_of(where, commit) != commit:
            raise agentry.AgentError("That commit could not be fetched from the task's address: the sign-in here "
                                     "cannot read the repository, or the commit is not there.")

    def checks(self, task_id, body):
        """The owner's checks of the steps named, on a returned commit, each in a fresh detached checkout, in a process
        group of its own, as an attempt's step check runs (task_attempt): {results: [{n, exit, tail}]}. Each step's
        check is the one the body's `checks` gives for it, the owner's as the app keeps it (a step the owner never ran
        an attempt of, done on the teammate's side, has none here otherwise), else the one the task's attempts were
        given; a step with neither: 400."""
        body = body if isinstance(body, dict) else {}
        commit, steps = body.get("commit"), body.get("steps")
        given = body.get("checks") if body.get("checks") is not None else {}
        if not (isinstance(commit, str) and shapes.COMMIT.fullmatch(commit)):
            raise agentry.AgentError("The checks run on a commit, by its id.")
        if not (isinstance(steps, list) and 0 < len(steps) <= shapes.STEPS_MAX and all(_int(n) for n in steps)):
            raise agentry.AgentError("Name the steps whose checks run.")
        if not (isinstance(given, dict) and len(given) <= shapes.STEPS_MAX
                and all(isinstance(k, str) and _STEP_NUMBER.fullmatch(k) and isinstance(v, str)
                        and 0 < len(v) <= MAX_CHECK and "\x00" not in v for k, v in given.items())):
            raise agentry.AgentError("The owner's checks are each a step's number and its command.")
        agent_id = self.agent_of_task(task_id)
        known = {**self._checks_known(agent_id, task_id), **{int(k): v for k, v in given.items()}}
        missing = [n for n in steps if not known.get(n)]
        if missing:
            raise agentry.AgentError(f"No check is known here for step {missing[0]}.")
        if self._running(task_id):
            # an attempt's processes live until its end, and could write into a checkout while a check runs there
            raise agentry.TaskConflict("An attempt of this task is running.")
        self._watch_or_409(agent_id, task_id)
        work = self.work(agent_id, task_id)
        if attempts._commit_of(work, commit) != commit:
            raise agentry.AgentError("That commit is not in the task's repository here.")
        results = []
        for i, n in enumerate(steps):
            code, tail = _run_check(work, commit, known[n], agent_id, f"{task_id[:12]}.owner{i}",
                                    self.agents._agent_dir(agent_id))
            results.append({"n": n, "exit": code, "tail": tail})
        return {"results": results}

    def _checks_known(self, agent_id, task_id):
        """Each step's check, as the attempt messages the app sent for the task's runs gave it, the latest first."""
        out = {}
        for r in self.records_of(agent_id, task_id):
            try:
                message = json.loads(self.agents._kept_file(r["id"], TASK_FILE, r) or b"null")
            except (agentry.AgentError, ValueError):
                continue
            if isinstance(message, dict) and _int(message.get("step")) and isinstance(message.get("check"), str):
                out[message["step"]] = message["check"]
        return out


def _plain(look):
    """A look as JSON keeps it (tuples as lists), so a kept look and a new one compare."""
    return json.loads(json.dumps(look, default=str))


def _kept_look(record, look):
    """The task's next baseline: `look`, the places watched now, over the look kept before, so a place no longer
    watched (an account taken out of the accounts' record) keeps how it was last seen, and is compared with it should
    it come back, never taken as it is then."""
    return dict(record.get("look") or {}, **look)


WATCH_HOLD = "Look at what changed, then resume the agent."
CHANGED_SINCE = "the watched places changed since the task's last attempt ended"


def _watch_hold(view):
    """The hold of a task's attempt's agent, in the owner's words, or None: whether something watched changed, at the
    attempt's start, while it worked or in what it left, which holds its agent (agents._hold). It goes on the run's
    record as `hold`, as every hold of an agent's own run does (agents.py), since the app copies a run's `hold` to pause
    its agent, counted as not yet mirrored paused, so that the owner's resume lifts the hold here (agents.put). A hold
    left off the record kept the agent held here while the app showed it never paused: the owner's Resume lifted
    nothing, and every Continue was refused here until the owner paused the agent and resumed it again."""
    task = view["task"]
    changed = (task.get("watch") or {}).get("changed") or []
    if view.get("cause") == "watch":
        why = view.get("reason") or "the watched places changed while the attempt worked"
    elif (task.get("resume") or {}).get("check") == "watch":
        why = CHANGED_SINCE
    elif changed:
        why = "the watched places changed while the attempt worked"
    else:
        return None
    named = f" ({agentry._some(changed)})" if changed else ""
    return f"{why[0].upper()}{why[1:]}{named}. {WATCH_HOLD}"[:agentry.MAX_TEXT]


def _with_places(hold, places):
    """A hold's words with those of git's places (TaskRuns._places_at_end: agents._hold_reason's, which end as
    WATCH_HOLD does), with one closing sentence between them."""
    if not places:
        return hold
    if not hold:
        return places
    if hold.endswith(WATCH_HOLD):
        hold = hold[:-len(WATCH_HOLD)].rstrip()
    return f"{hold} {places}"[:agentry.MAX_TEXT]


_ADDRESS = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:[^@/]+@)?([^/:]+)[:/](.+)$")


def _repo_key(address):
    """A repository's address as one key however it is written: (host, path) with its scheme, user, a final .git and
    a final slash taken off (https://github.com/o/n.git and git@github.com:o/n are one); a local path, itself, with or
    without file://."""
    a = address.strip().rstrip("/")
    if a.endswith(".git"):
        a = a[:-4]
    if a.startswith("file://"):
        a = a[len("file://"):]
    if a.startswith("/"):
        return (None, os.path.normpath(a))
    m = _ADDRESS.match(a)
    return (m.group(1).lower(), m.group(2).strip("/")) if m else (None, a)


def _write_file(path, data, durable):
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        if durable:
            fh.flush()
            os.fsync(fh.fileno())
    os.replace(tmp, path)
    if durable:
        _sync_folder(os.path.dirname(path))


def _sync_folder(folder):
    fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


_STEP = re.compile(r"^Task [0-9a-f]{32}: step (\d+), checkpoint \d+ \(step\)$")


def _steps_done(where, base, commit):
    """The steps completed between `base` and `commit`, by their checkpoints' commit messages (task_attempt)."""
    from .workspace import RUN_GIT, git
    _, out = git("log", "--format=%s", f"{base}..{commit}", cwd=where, ok=(0, 128), env=RUN_GIT)
    found = sorted({int(m.group(1)) for line in out.splitlines() if (m := _STEP.match(line.strip()))})
    return found


def _run_check(work, commit, check, agent_id, mark, agent_dir):
    """A check in a fresh detached checkout of `commit`, at most CHECK_SECONDS, in a process group of its own ended
    after: (exit, its output's last CHECK_TAIL bytes). The checkout is under a folder made new in the agent's folder
    (`agent_dir`), with a name nothing knows before, as an attempt's own check's is (task_attempt): a fixed one could
    be planted, a link to elsewhere among what."""
    from .workspace import RUN_GIT, git, program_env
    holder = tempfile.mkdtemp(prefix="owner-check-", dir=agent_dir)
    folder = os.path.join(holder, "work")
    try:
        git("worktree", "add", "--quiet", "--detach", folder, commit, cwd=work, env=RUN_GIT)
    except BaseException:
        shutil.rmtree(holder, ignore_errors=True)
        raise
    try:
        env = program_env({"PP_AGENT_RUN": "1", "PP_AGENT_ID": agent_id, processes.MARK: mark})
        try:
            p = subprocess.Popen(["/bin/sh", "-c", check], cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            return 127, f"the check could not start: {e.strerror or e}"
        try:
            out, _ = p.communicate(timeout=shapes.CHECK_SECONDS)
            code = p.returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            out, _ = p.communicate()
            code = 124
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        processes.end(mark, 0.5)
        return code, out[-shapes.CHECK_TAIL:].decode("utf-8", "replace")
    finally:
        try:
            git("worktree", "remove", "--force", folder, cwd=work, ok=(0, 128), env=RUN_GIT)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(holder, ignore_errors=True)
        try:
            git("worktree", "prune", cwd=work, ok=(0, 128), env=RUN_GIT)
        except Exception:  # noqa: BLE001
            pass
