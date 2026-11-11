"""A session's usage, read only: the tokens its `claude` reported, summed from
the transcripts Claude Code writes, never by using a sign-in or asking any service.

Claude Code keeps a project's transcripts in `~/.claude/projects/<slug>/<session>.jsonl` (in an account's own folder,
`<its CLAUDE_CONFIG_DIR>/projects/...`, accounts.py), where the slug is the
working directory with every character that is not a letter or a digit written as `-`. Each line that records a
model's reply carries `message.usage`; the same reply can be written on several lines as it streams, so a reply is
counted once, by its message id. Only lines whose `cwd` is the session's worktree are counted, so another project
with the same slug is not.

A `codex` session's, the same way, from the conversation files Codex writes under its home
(`sessions/<year>/<month>/<day>/rollout-<time>-<id>.jsonl`, Codex 0.163.0): the first line names the working directory
(`session_meta`); each model call's tokens are a `token_usage_record` of their own; and each `token_count` event
carries the rate limits the model's last answer reported, the account's windows (the 5-hour and the weekly one), which
a subscriber's answers carry and an API key's do not. The calls are summed, not the conversation's running totals: a
forked conversation's file starts its running total from its parent's (measured with Codex 0.163.0: a parent of two
calls and a fork of one, 15 tokens each, gave 30 and 45), so the totals would count the parent twice.

Only regular files are read (a FIFO or a link named like a transcript would block the reader or reach another file),
and one that cannot be read is passed over, never a reason for the others not to be counted.

Not read: a subscription's remaining quota beyond what the programs wrote down, which nothing here can read without the
sign-in. What is shown is what the program wrote down, not a bill.

Nothing here leaves the machine. Once the workspace is paired with a Proofpane organisation, usage_report.py reads the
same transcripts, reply by reply, and posts each reply's counts there (what it sends, and when, is said there).
"""

import json
import os
import re
import stat
import threading
import time
from datetime import datetime

FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
MAX_FILES = 200
MAX_BYTES = 64 * 1024 * 1024


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def claude_usage(worktree, claude_dir):
    """{messages, transcripts, <each of FIELDS>} for the `claude` that ran in `worktree`, from the transcripts in
    `claude_dir` (`~/.claude`, or an account's own folder)."""
    worktree = os.path.realpath(worktree)
    folder = os.path.join(claude_dir, "projects", slug(worktree))
    totals = dict.fromkeys(FIELDS, 0)
    seen, files, read = set(), 0, 0
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".jsonl"))[:MAX_FILES]
    except (FileNotFoundError, NotADirectoryError):
        names = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            got = _regular(path)
            if got is None:
                continue
            size = got[1]
            if read + size > MAX_BYTES:
                break
            read += size
            with open(path, encoding="utf-8", errors="replace") as fh:
                counted = False
                for number, line in enumerate(fh):
                    try:
                        row = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    message = row.get("message")
                    usage = message.get("usage") if isinstance(message, dict) else None
                    if row.get("type") != "assistant" or not isinstance(usage, dict):
                        continue
                    if not isinstance(row.get("cwd"), str) or "\0" in row["cwd"] \
                            or os.path.realpath(row["cwd"]) != worktree:
                        continue
                    # a line with no id of its own is counted once as itself, never merged with another
                    key = message.get("id") or row.get("requestId") or row.get("uuid") or (name, number)
                    if key in seen:
                        continue
                    seen.add(key)
                    counted = True
                    for f in FIELDS:
                        value = usage.get(f)
                        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                            totals[f] += value
                files += counted
        except Exception:  # noqa: BLE001  one transcript that cannot be read is passed over, not the others
            continue
    return dict(totals, messages=len(seen), transcripts=files)


CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
CODEX_WINDOWS = {300: "five_hour", 10080: "seven_day"}     # Codex's windows, named as the status line names Claude's
META_LINE = 1024 * 1024      # a conversation file's first line, its meta, which holds Codex's instructions
MAX_KNOWN = 4096             # conversation files remembered between calls, a few numbers each
_read_so_far = {}            # path -> what was read of that file: the files only grow, so a call reads on from there


def _regular(path):
    """(inode, size) of a regular file of its own, or None for a link, a FIFO, a device or a folder."""
    st = os.lstat(path)
    return (st.st_ino, st.st_size) if stat.S_ISREG(st.st_mode) else None


def _row(line):
    try:
        row = json.loads(line)
    except (ValueError, RecursionError):
        return {}
    return row if isinstance(row, dict) else {}


def _epoch(text):
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except (AttributeError, TypeError, ValueError):
        return None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _rollouts(codex_home, since=None):
    """Codex's conversation files, newest first (their names begin with the time each began); with `since`, only
    those in the day folders from the day before it on (the folders are named by the machine's own day)."""
    root = os.path.join(codex_home, "sessions")
    first = time.strftime("%Y/%m/%d", time.gmtime(since - 86400)) if since else None
    found = []
    for folder, _dirs, names in os.walk(root):
        day = os.path.relpath(folder, root).replace(os.sep, "/")
        if first and day.count("/") == 2 and day < first:
            continue
        found.extend(os.path.join(folder, n) for n in names if n.startswith("rollout-") and n.endswith(".jsonl"))
    return sorted(found, key=os.path.basename, reverse=True)


def _known(path, ino, size):
    """What was read of one conversation file so far; a new file, or one replaced under its name (another inode, or
    shorter than was read), starts again."""
    known = _read_so_far.get(path)
    if known is None or known["ino"] != ino or size < known["offset"]:
        if len(_read_so_far) >= MAX_KNOWN:
            _read_so_far.clear()
        known = _read_so_far[path] = {"ino": ino, "offset": 0, "cwd": None, "calls": 0,
                                      "tokens": dict.fromkeys(CODEX_FIELDS, 0), "last_total": None, "limits": None}
    return known


def _read_meta(path, known):
    """The file's first line: the directory its conversation is in (its `session_meta`), "" for none; left unknown
    while the line is still being written."""
    with open(path, "rb") as fh:
        first = fh.readline(META_LINE)
    if not first.endswith(b"\n"):
        return
    row = _row(first.decode("utf-8", "replace"))
    meta = row.get("payload") if row.get("type") == "session_meta" else None
    cwd = meta.get("cwd") if isinstance(meta, dict) else None
    known["cwd"] = os.path.realpath(cwd) if isinstance(cwd, str) and cwd and "\0" not in cwd else ""
    known["offset"] = len(first)


def _read_on(path, known, budget):
    """Read on from where the last call stopped, whole lines only and at most `budget` bytes; returns the bytes read."""
    with open(path, "rb") as fh:
        fh.seek(known["offset"])
        data = fh.read(budget)
    end = data.rfind(b"\n") + 1
    if end == 0 and len(data) == budget:
        end = len(data)          # a line longer than the budget is passed over, not waited on
    for raw in data[:end].splitlines():
        if b'"token_usage_record"' not in raw and b'"token_count"' not in raw:
            continue
        row = _row(raw.decode("utf-8", "replace"))
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "token_usage_record" and isinstance(payload.get("usage"), dict):
            known["calls"] += 1
            for f in CODEX_FIELDS:
                known["tokens"][f] += _count(payload["usage"].get(f))
        elif row.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                known["last_total"] = info["total_token_usage"]
            at = _epoch(row.get("timestamp"))
            if isinstance(payload.get("rate_limits"), dict) and at is not None:
                known["limits"] = (at, payload["rate_limits"])
    known["offset"] += end
    return end


def _windows(newest):
    """The rate limits as the status line's windows ({at, five_hour, seven_day}), or None when they name neither."""
    if newest is None:
        return None
    at, limits = newest
    out = {"at": at}
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        minutes, used, resets = window.get("window_minutes"), window.get("used_percent"), window.get("resets_at")
        name = CODEX_WINDOWS.get(minutes) if isinstance(minutes, int) and not isinstance(minutes, bool) else None
        if name and isinstance(used, (int, float)) and not isinstance(used, bool):
            out[name] = {"used_percentage": float(used),
                         "resets_at": resets if isinstance(resets, int) and not isinstance(resets, bool) else None}
    return out if len(out) > 1 else None


# one reading of the conversation files at a time: each file's offset and sums (_read_so_far) are read on from where the
# last reading stopped, and two at once would count the same lines twice or pass some over
_reading = threading.Lock()


def codex_usage(worktree, codex_home, since=None):
    """{conversations, <each of CODEX_FIELDS>, status} for the `codex` that ran in `worktree`, from the conversation
    files under `codex_home` (with `since`, those from the day before it on): each model call's tokens, summed (a file
    with no record of its calls gives its last running total); and `status`, the account's windows as the newest count
    among them recorded them (the status line's shape), or None. Every file's first line is read once, to know whose
    it is; only this worktree's are read further, on from where the last call stopped, and only they count toward the
    bounds."""
    with _reading:
        return _codex_usage(worktree, codex_home, since)


def _codex_usage(worktree, codex_home, since):
    worktree = os.path.realpath(worktree)
    totals = dict.fromkeys(CODEX_FIELDS, 0)
    conversations, budget, newest = 0, MAX_BYTES, None
    for path in _rollouts(codex_home, since):
        try:
            got = _regular(path)
            if got is None:
                continue
            known = _known(path, *got)
            if known["cwd"] is None:
                _read_meta(path, known)
            if known["cwd"] != worktree:
                continue
            if got[1] > known["offset"] and budget > 0:
                budget -= _read_on(path, known, budget)
        except Exception:  # noqa: BLE001  one file that cannot be read is passed over, not the others
            continue
        conversations += 1
        own = known["tokens"] if known["calls"] else (known["last_total"] or {})
        for f in CODEX_FIELDS:
            totals[f] += _count(own.get(f))
        if known["limits"] and (newest is None or known["limits"][0] > newest[0]):
            newest = known["limits"]
        if conversations >= MAX_FILES:
            break
    return dict(totals, conversations=conversations, status=_windows(newest))


def codex_limits(codex_home, since=None, most=20):
    """An account's windows as the newest count in its most recent conversation files recorded them (the status
    line's shape), whatever worktree they were in, or None: what one Codex sign-in has used of its limits. The same
    reading on as codex_usage, so a file read by one is not read again by the other."""
    with _reading:
        return _codex_limits(codex_home, since, most)


def _codex_limits(codex_home, since, most):
    newest, budget = None, MAX_BYTES
    for path in _rollouts(codex_home, since)[:most]:
        try:
            got = _regular(path)
            if got is None:
                continue
            known = _known(path, *got)
            if known["cwd"] is None:
                _read_meta(path, known)
            if got[1] > known["offset"] and budget > 0:
                budget -= _read_on(path, known, budget)
        except Exception:  # noqa: BLE001  one file that cannot be read is passed over, not the others
            continue
        if known["limits"] and (newest is None or known["limits"][0] > newest[0]):
            newest = known["limits"]
    return _windows(newest)
