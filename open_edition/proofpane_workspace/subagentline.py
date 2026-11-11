"""The sub-agent status line a `claude` terminal of the workspace runs (settings.map_settings, from Claude Code 2.1.214):
the agents of the session, for the control window's map, read only.

Claude Code runs a `subagentStatusLine` command with the sub-agents its agent panel shows, on standard input: the base
hook fields and a `tasks` array, one entry a sub-agent, with its `id`, `name` (when it has one), `type`, `agentType`
(from 2.1.293), `status` (`running`, `completed`, `failed`, `killed`), `description`, `startTime` (milliseconds),
`model`, `effort` (from 2.1.213) and `tokenCount` among others (https://code.claude.com/docs/en/statusline, "Subagent
status lines" and "Task fields", read 2026-10-17). It renders a line written back for a row in that row's place, and
its own row for a row none is written for: this prints nothing, so Claude Code keeps its own rows.

The page says it runs "once per refresh tick", and that the status line's own updates can go quiet while the main
session is idle, a coordinator waiting on background sub-agents; not whether the sub-agent line's ticks go quiet then
too. Read in 2.1.301's code (not measured): it runs on a timer of its own, 300 ms after the rows change and every 5
seconds while any row is shown, whatever the main session does; with no row shown it does not run at all, so a session
whose agents never showed has no file here yet. A row's `id` is the agent's own id there, the one the SubagentStart
and SubagentStop hooks give as `agent_id` (a sub-agent's task is made with its agent id as its id), so this file and
the activity hook's (activity.py --map) speak of the same agents.

It keeps, in the session's own folder (subagents.json), each agent it has seen: its id; its name, or else its
description, cut to NAME characters (a model's words, kept only for the map, which shows them as Claude Code's own
map does, never in a notification); its type and agent type, its status, when it started, its model, its effort and
its token count, as last given; when this line first saw it end (`ended_at`); and when it last saw it (`seen_at`). An
agent no longer shown keeps its last row, so the map still has an agent Claude Code has taken out of its panel; the
oldest are dropped past MAX_AGENTS. Nothing else of the input is kept: not the session's id, its paths, a row's
`label` or `cwd`. Outside a workspace session (PP_SESSION_DIR unset), and for a claude started in one of a session's
shells (PP_SHELL, which does not speak for the session, as activity.py says), it writes nothing. What it keeps is the
session's own word about itself, as status.json is: for display.

It uses the standard library only: it runs with whichever interpreter its settings name (the workspace agent's own,
settings.map_settings), which need hold nothing else.
"""

import json
import os
import re
import stat
import sys
import time

MAX_INPUT = 256 * 1024
MAX_FILE = 512 * 1024       # above the most MAX_AGENTS rows of bounded fields can take (about 280 KB)
MAX_AGENTS = 200
NAME = 80
FILE = "subagents.json"
STATUSES = ("running", "completed", "failed", "killed")
ENDED = ("completed", "failed", "killed")
_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.:@-]{0,127}")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/ -]{0,79}")      # a type's or a model's name
_EFFORT = re.compile(r"[a-z]{1,16}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _text(value, most):
    """Text as it is shown: one line, no control characters, at most `most` characters; None for no text."""
    if not isinstance(value, str):
        return None
    value = " ".join(_CONTROL.sub(" ", value).split())[:most]
    return value or None


def _whole(value, low=0, high=2 ** 53):
    return value if type(value) is int and low <= value <= high else None


def _row(task, now):
    """What is kept of one entry of `tasks`, or None for one with no usable id."""
    if not isinstance(task, dict):
        return None
    agent_id = task.get("id")
    if not isinstance(agent_id, str) or not _ID.fullmatch(agent_id):
        return None
    if task.get("type") not in (None, "local_agent"):
        return None             # the one kind the page documents; another's row is not taken for an agent's
    name = _text(task.get("name"), NAME) or _text(task.get("description"), NAME)
    kind = task.get("type") if isinstance(task.get("type"), str) and _WORD.fullmatch(task["type"]) else None
    agent_type = _text(task.get("agentType"), NAME)
    status = task.get("status") if task.get("status") in STATUSES else None
    start = _whole(task.get("startTime"), 1)
    model = _text(task.get("model"), NAME)
    effort = task.get("effort")
    if isinstance(effort, str):
        effort = effort if _EFFORT.fullmatch(effort) else None
    else:
        effort = _whole(effort)                 # a token budget
    return {"id": agent_id, "name": name, "type": kind, "agent_type": agent_type, "status": status,
            "started_at": start // 1000 if start else None, "model": model, "effort": effort,
            "tokens": _whole(task.get("tokenCount")), "seen_at": now, "ended_at": None}


def _kept(row):
    """A row of the file as read back, cleaned again; None for one that is not a row."""
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not _ID.fullmatch(row["id"]):
        return None
    out = {"id": row["id"], "name": _text(row.get("name"), NAME),
           "type": row.get("type") if isinstance(row.get("type"), str) and _WORD.fullmatch(row["type"]) else None,
           "agent_type": _text(row.get("agent_type"), NAME),
           "status": row.get("status") if row.get("status") in STATUSES else None,
           "started_at": _whole(row.get("started_at"), 1), "model": _text(row.get("model"), NAME),
           "effort": row.get("effort") if (isinstance(row.get("effort"), str) and _EFFORT.fullmatch(row["effort"]))
           else _whole(row.get("effort")),
           "tokens": _whole(row.get("tokens")), "seen_at": _whole(row.get("seen_at"), 1),
           "ended_at": _whole(row.get("ended_at"), 1)}
    return out


def read(session_dir):
    """The agents this line has kept for the session, cleaned again as they are read: [row], or None when it has kept
    none (no file, one too large, or not a regular file). Opened without following a link and without waiting on a
    FIFO, since a session's programs can put anything where the file goes."""
    try:
        fd = os.open(os.path.join(session_dir, FILE), os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = None
            data = json.loads(fh.read(MAX_FILE + 1).decode("utf-8", "replace"))
    except (OSError, ValueError, RecursionError):
        return None
    finally:
        if fd is not None:
            os.close(fd)
    rows = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    return [r for r in (_kept(x) for x in rows[:MAX_AGENTS]) if r is not None]


def merge(kept, tasks, now):
    """The rows to keep once a tick shows `tasks`: each shown agent's row from this tick, with when this line first
    saw it end kept (or now, if this tick is the first to show it ended); each agent not shown, as it was; the newest
    MAX_AGENTS by when they started (else when they were last seen)."""
    rows = {r["id"]: r for r in kept or []}
    for task in tasks if isinstance(tasks, list) else []:
        row = _row(task, now)
        if row is None:
            continue
        before = rows.get(row["id"])
        if row["status"] in ENDED:
            row["ended_at"] = before["ended_at"] if before and before.get("ended_at") and \
                before.get("status") in ENDED else now
        rows[row["id"]] = row
    ordered = sorted(rows.values(), key=lambda r: (r.get("started_at") or r.get("seen_at") or 0), reverse=True)
    return ordered[:MAX_AGENTS]


def main():
    try:
        data = json.loads(sys.stdin.read(MAX_INPUT) or "{}")
    except (ValueError, RecursionError):
        data = {}
    session_dir = os.environ.get("PP_SESSION_DIR", "")
    if not session_dir or os.environ.get("PP_SHELL") or not os.path.isdir(session_dir):
        return 0
    try:
        now = int(time.time())
        rows = merge(read(session_dir), data.get("tasks") if isinstance(data, dict) else None, now)
        path = os.path.join(session_dir, FILE)
        tmp = path + ".tmp"             # Claude Code runs one of these at a time: one name, never one left a run
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return 0
            json.dump({"at": now, "agents": rows}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001  a line that fails must not show: Claude Code keeps its own rows
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
