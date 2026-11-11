"""The status line every `claude` session in a workspace runs (settings.py): the session's quota, read only.

Claude Code runs a status-line command after each reply and passes it, on standard input, what it knows of the
session. For claude.ai Pro and Max subscribers, and only after the session's first reply, that includes
`rate_limits`: how much of the 5-hour and of the 7-day window is used, from 0 to 100, and when each resets, in Unix
seconds; behind a Claude apps gateway it can carry a `spend_limit` window too; a window is left out once it has reset
(https://code.claude.com/docs/en/statusline, read 2026-09-28). This script keeps those numbers, the model's name and
how full the context window is, in status.json in the session's directory, where the workspace agent reads them for
the page, and prints one short line for the terminal. For the control window's map it
also keeps the tokens in the context window at the last reply (`context_window.total_input_tokens`, which counts
cache reads and writes in, as `context_tokens`) and the session's effort (`effort.level`, the live one, from 2.1.119;
absent where the model takes none), neither of which the line shows (statusline page, read 2026-10-17).

It reads nothing but its standard input: no sign-in, no file of the program's. Outside a workspace session
(PP_SESSION_DIR unset), and for a claude started in one of a session's shells (PP_SHELL: it does not speak for the
session, as activity.py says, and runs on no account of the session's), it only
prints. It uses the standard library only, since it runs with the system Python. The workspace reads its file back as
it reads the map's (read): never through a link, never waiting on a FIFO a session's program put there.
"""

import json
import os
import re
import stat
import sys
import time

WINDOWS = ("five_hour", "seven_day", "spend_limit")
EFFORT = re.compile(r"[a-z]{1,16}")       # a level's name: low, medium, high, xhigh, max, or a later one's
MAX_INPUT = 256 * 1024
MAX_FILE = 16 * 1024
FILE = "status.json"


def _number(value, low, high):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
        return value
    return None


def _dict(value):
    return value if isinstance(value, dict) else {}


def clean(data, at=None):
    """What is kept of one status-line input, or of a status.json read back: {"at", and whichever of "model",
    "context_used", "context_tokens", "effort", and one {"used_percentage", "resets_at"} per rate-limit window is
    present and sensible}."""
    data = _dict(data)
    at = _number(data.get("at"), 1, 2 ** 40) if at is None else at
    out = {"at": int(at if at is not None else time.time())}
    model = _dict(data.get("model")).get("display_name", data.get("model"))
    if isinstance(model, str) and model.strip():
        out["model"] = model.strip()[:80]
    context = _dict(data.get("context_window"))
    used = _number(context.get("used_percentage", data.get("context_used")), 0, 100)
    if used is not None:
        out["context_used"] = used
    tokens = context.get("total_input_tokens", data.get("context_tokens"))
    if type(tokens) is int and 0 <= tokens <= 2 ** 40:
        out["context_tokens"] = tokens
    effort = _dict(data.get("effort")).get("level", data.get("effort"))
    if isinstance(effort, str) and EFFORT.fullmatch(effort):
        out["effort"] = effort
    limits = _dict(data.get("rate_limits")) or data
    for name in WINDOWS:
        window = _dict(limits.get(name))
        pct = _number(window.get("used_percentage"), 0, 1000)
        if pct is not None:
            out[name] = {"used_percentage": pct, "resets_at": _number(window.get("resets_at"), 1, 2 ** 40)}
    return out


def line(status):
    parts = []
    if "model" in status:
        parts.append(status["model"])
    if "context_used" in status:
        parts.append(f"context {status['context_used']:.0f}%")
    for name, label in (("five_hour", "5h"), ("seven_day", "7d"), ("spend_limit", "spend")):
        if name in status:
            parts.append(f"{label} {status[name]['used_percentage']:.0f}%")
    return " | ".join(parts)


def read(session_dir, dated=False):
    """The session's last status, cleaned again as it is read, or None when its program has written none, or what is
    there is no regular file of at most MAX_FILE bytes (opened without following a link, and without waiting on a
    FIFO: the session's programs can put either where the file goes, and the map asks every few seconds). With
    `dated`, None too for a file that does not give its own time, which this script always writes: one read back
    without it is dated at the read (clean), and the owner's controller takes the newest line by its time."""
    try:
        fd = os.open(os.path.join(session_dir, FILE), os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        raw = b""
        while len(raw) <= MAX_FILE:
            got = os.read(fd, MAX_FILE + 1 - len(raw))
            if not got:
                break
            raw += got
        if len(raw) > MAX_FILE:
            return None
        data = json.loads(raw.decode("utf-8", "replace"))
        if dated and _number(_dict(data).get("at"), 1, 2 ** 40) is None:
            return None
        return clean(data)
    except (OSError, ValueError, RecursionError):
        return None
    finally:
        os.close(fd)


def main():
    try:
        data = json.loads(sys.stdin.read(MAX_INPUT) or "{}")
    except ValueError:
        data = {}
    status = clean(data, at=int(time.time()))
    session_dir = os.environ.get("PP_SESSION_DIR", "")
    if session_dir and not os.environ.get("PP_SHELL") and os.path.isdir(session_dir):
        path = os.path.join(session_dir, FILE)
        try:
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(status, fh)
            os.replace(path + ".tmp", path)
        except OSError:
            pass
    sys.stdout.write(line(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
