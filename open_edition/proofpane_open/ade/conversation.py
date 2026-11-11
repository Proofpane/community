"""The conversation view of the ADE: a channel read into two transcripts, the cloud session's and the local
session's, oldest first, for a page laid out like a chat with a coding agent.

It is rebuilt from what the sessions wrote on the channel branch: the handoff's sections, and the windows and
releases the cloud session wrote; the log lines, questions and the owner's answers on the side of the session on their
machine. It is not a mirror of either session's screen, and the page says so; a cloud session's own page on
claude.ai is linked from its commits' `Claude-Session:` trailer for that.

Items, each with a `time` (UTC, to the minute, or None when nothing dates it):

    {"kind": "message", "title", "text", "who", "file"}         a handoff section; a log step
    {"kind": "tool", "name", "arg", "detail", "who", "file"}    a read; a window or a release the cloud wrote
    {"kind": "heartbeats", "first", "count", "state", "unread"} consecutive heartbeats of one log, as one line
    {"kind": "waiting", "text", "ids", "who", "file"}           a log's WAITING FOR line
    {"kind": "done", "text", "who", "file"}                     a log's DONE line
    {"kind": "question", "id", "text", "answer", "state"}       a question, with the owner's answer when given

A handoff section is dated by the commit that wrote it: sections are matched, newest first, to the file's commits
whose time of day is within half an hour of the time the heading gives, never going back past a commit already used.
A section older than the commits read, or whose heading gives no time, keeps its place in the file but no date: it
sorts with the dated section before it, since a handoff is written in order.
"""

import os

LIMIT = 60          # items kept per pane; older ones are counted in `earlier`
MATCH_MINUTES = 30  # how far a heading's time of day may be from its commit's


def _sort_key(time):
    """A sortable form of a channel time (`...T04:05Z`) or a commit time (`...T04:05:09Z`); None sorts first."""
    if not time:
        return ""
    return time if time.count(":") == 2 else time[:-1] + ":00Z"


def _minute(time):
    return time[:16] + "Z" if time else None


def _minutes_of_day(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _near(commit_time, at):
    """Whether a commit's time of day is within MATCH_MINUTES of a heading's `at`, across midnight too."""
    gap = abs(_minutes_of_day(commit_time[11:16]) - _minutes_of_day(at))
    return min(gap, 24 * 60 - gap) <= MATCH_MINUTES


def _date_sections(sections, history):
    """{section index: commit} for the sections of a handoff that the commits in `history` (newest first) date."""
    dated, used = {}, 0
    for index in range(len(sections) - 1, -1, -1):
        at = sections[index].get("at")
        if not at:
            continue
        for j in range(used, len(history)):
            if history[j]["time"] and _near(history[j]["time"], at):
                dated[index] = history[j]
                used = j + 1
                break
    return dated


def _cloud_items(channels):
    items = []
    for ch in channels:
        history = ch.get("history") or {}
        for ins in ch["instructions"]:
            path = os.path.join(ch["name"], ins["file"])
            commits = history.get(ins["file"]) or []
            if not ins["file"].startswith("HANDOFF"):
                c = commits[0] if commits else None
                items.append({"kind": "tool", "name": "Instructions", "arg": path,
                              "detail": "\n".join(s["heading"] for s in ins["sections"]),
                              "time": _minute(c["time"]) if c else None, "who": c["session"]["name"] if c else None,
                              "file": path})
                continue
            dated = _date_sections(ins["sections"], commits)
            carried = None  # an undated section sorts with the dated one before it, so the file's order holds
            for index, section in enumerate(ins["sections"]):
                c = dated.get(index)
                carried = c["time"] if c else carried
                items.append({"kind": "message", "title": section["heading"], "text": section.get("body", ""),
                              "time": _minute(c["time"]) if c else None, "at": section.get("at"),
                              "who": c["session"]["name"] if c else None, "file": path, "_sort": carried})
        for w in ch["windows"]:
            path = os.path.join(ch["name"], w["file"])
            c = (history.get(w["file"]) or [None])[0]
            arg = (w["name"] or w["file"]) + (f" · {w['start']} to {w['end']}" if w["start"] else "")
            items.append({"kind": "tool", "name": "Window", "arg": arg, "detail": "; ".join(w["problems"]) or None,
                          "time": _minute(c["time"]) if c else None, "who": c["session"]["name"] if c else None,
                          "file": path})
        for r in ch["releases"]:
            path = os.path.join(ch["name"], r["file"])
            c = (history.get(r["file"]) or [None])[0]
            items.append({"kind": "tool", "name": "Release", "arg": r["name"] or r["file"],
                          "detail": "; ".join(r["problems"]) or None, "time": _minute(c["time"]) if c else None,
                          "who": c["session"]["name"] if c else None, "file": path})
    return items


def _local_items(channels):
    items, waiting_times = [], {}
    for ch in channels:
        history = ch.get("history") or {}
        for log in ch["logs"]:
            path = os.path.join(ch["name"], log["file"])
            c = (history.get(log["file"]) or [None])[0]
            who = c["session"]["name"] if c else None
            group = None
            for e in log["entries"]:
                if e["kind"] == "heartbeat":
                    if group is None:
                        group = {"kind": "heartbeats", "first": e["time"], "count": 0, "who": who, "file": path}
                        items.append(group)
                    group.update(time=e["time"], count=group["count"] + 1, state=e.get("state", ""),
                                 unread=e.get("unread"))
                    continue
                group = None
                if e["kind"] == "read":
                    items.append({"kind": "tool", "name": "Read",
                                  "arg": ", ".join(r["path"] + (" @ " + r["commit"] if r["commit"] else "")
                                                   for r in e["reads"]) or e["text"][:120],
                                  "detail": e["text"], "time": e["time"], "who": who, "file": path})
                elif e["kind"] == "waiting":
                    items.append({"kind": "waiting", "text": e["text"], "ids": e.get("ids", []),
                                  "waiting_for": e.get("waiting_for"), "time": e["time"], "who": who, "file": path})
                    for qid in e.get("ids", []):
                        waiting_times.setdefault(qid, e["time"])
                elif e["kind"] == "done":
                    items.append({"kind": "done", "text": e["text"], "time": e["time"], "who": who, "file": path})
                else:
                    items.append({"kind": "message", "title": e.get("label"), "text": e["text"], "time": e["time"],
                                  "who": who, "file": path})
    for ch in channels:
        for q in ch["questions"]:
            state = "open" if q["open"] else ("answered" if q["answer"] else
                                              ("withdrawn" if q["withdrawn"] else "processed"))
            items.append({"kind": "question", "id": q["id"], "text": q["summary"], "for": q.get("for"),
                          "answer": q["answer"]["text"] if q["answer"] else None, "state": state,
                          "time": q["asked_at"] or waiting_times.get(q["id"]),
                          "file": os.path.join(ch["name"], q["file"])})
    return items


def _pane(items):
    """Oldest first, the last LIMIT of them, and how many came before. An item with no date sorts first, unless it
    carries the time of the item it follows (`_sort`); among equal times the order found holds."""
    ordered = sorted(enumerate(items), key=lambda pair: (_sort_key(pair[1].get("_sort") or pair[1]["time"]), pair[0]))
    items = [item for _, item in ordered]
    for item in items:
        item.pop("_sort", None)
        if item["time"] and item["time"].count(":") == 2:
            item["time"] = _minute(item["time"])
    earlier = max(0, len(items) - LIMIT)
    return {"items": items[earlier:], "earlier": earlier}


def build(channels):
    """The two panes for channels read by `board._read_directory`, before the board judges them: their logs still
    hold every entry and their commits are still attached."""
    return {"cloud": _pane(_cloud_items(channels)), "local": _pane(_local_items(channels))}
