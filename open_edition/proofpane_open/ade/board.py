"""The ADE: a connected repository read into what the owner sees.

`build_board` resolves the branch's head once and reads everything at that commit: each channel directory (through
`channel.read_channel`), the commits that touched each channel file, and, for a repository whose commits are
verified, the branch's latest commits. Then it works out, against the time it is given:

- windows: open, upcoming, ended, withdrawn or invalid, and who wrote each window file. Until windows are signed,
  a window file written by anyone but a cloud session or the ADE is flagged: the check the local session
  makes by hand today, that only the cloud session wrote it;
- instructions: when each last changed and by whom, when a log last says it was read, and so whether an update has
  gone unread. A log in one directory may read a handoff in another, so every log on the board is searched;
- logs: the last line, the last heartbeat, the heartbeat interval (declared in a heartbeat, else the median gap
  between recent heartbeats), and silence: two intervals without a new line raise an alert, the check-in the cloud
  session schedules by hand today. Only a log that is being watched can fall silent: the
  one with the board's latest heartbeat, or one waiting at a stop, and neither when its last line says DONE. A log
  a task finished with, or one the heartbeat moved away from, is shown, not alarmed on;
- the stop a log is at: its latest `WAITING FOR` line with no later step, read or DONE after it;
- the questions still open, and the answers given;
- sessions: every cloud session, agent session, ADE and person the commits name, and when each was last seen.

Nothing here writes, and nothing is fetched twice for one board.
"""

import os
import statistics
from datetime import timedelta

from . import actions, channel, conversation, sources

FILE_HISTORY = 10      # commits read per channel file
BRANCH_HISTORY = 30    # commits read from a verified branch
DEFAULT_INTERVAL = 30  # minutes, when a log neither declares nor shows its heartbeat interval
RECENT = 15            # log entries shown per log
WINDOW_WRITERS = ("cloud", "session", "ade")
_HISTORY_ROLES = ("window", "release", "instructions", "answers", "questions", "log", "stop")


def _t(text):
    return channel.parse_time(text) if text else None


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _minutes(delta):
    return int(delta.total_seconds() // 60)


def _minute(dt):
    return dt.replace(second=0, microsecond=0) if dt else None


class _Sessions:
    """Every session the board's commits name, each commit counted once."""

    def __init__(self):
        self.by_id, self.shas = {}, set()

    def note(self, commit):
        if commit["sha"] in self.shas:
            return
        self.shas.add(commit["sha"])
        s = commit["session"]
        rec = self.by_id.setdefault(s["id"], {"id": s["id"], "kind": s["kind"], "name": s["name"], "url": s["url"],
                                              "last_seen": None, "commits": 0})
        rec["commits"] += 1
        if commit["time"] and (rec["last_seen"] is None or commit["time"] > rec["last_seen"]):
            rec["last_seen"] = commit["time"]

    def listed(self):
        return sorted(self.by_id.values(), key=lambda s: s["last_seen"] or "", reverse=True)


def _commit_view(c):
    return {"sha": c["sha"], "short": c["short"], "time": c["time"], "subject": c["subject"][:300],
            "session": {k: c["session"][k] for k in ("id", "kind", "name", "url")}}


def build_board(conn, source, now, verifications=None, public_key=None):
    """The board for `conn` ({branch, dirs, verify}) read through `source` at the branch's head, judged at `now`
    (an aware UTC datetime). `verifications` maps a commit's full id to its stored Verify result.
    `public_key` is the deployment's: with it, each window, answers file and STOP is shown as signed, unsigned, or
    signed in a way that does not hold."""
    head = source.head(conn["branch"])
    board = {"repo": source.label, "branch": conn["branch"], "head": head, "now": _iso(now), "channels": [],
             "commits": [], "sessions": [], "alerts": []}
    sessions = _Sessions()
    read = []
    for directory in conn["dirs"]:
        read.append(_read_directory(source, head, directory, sessions))
    board["conversation"] = conversation.build(read)  # before the board judges: every log entry, every commit
    all_logs = [(r["name"], log) for r in read for log in r["logs"]]
    basenames = [os.path.basename(sources.join(r["name"], f["name"])) for r in read for f in r["files"]]
    interval = min([_interval(log["entries"]) or DEFAULT_INTERVAL for _, log in all_logs] or [DEFAULT_INTERVAL])
    beats = [(e["time"], d, log["file"]) for d, log in all_logs for e in log["entries"] if e["kind"] == "heartbeat"]
    live = max(beats)[1:] if beats else None
    for ch in read:
        board["channels"].append(_judge(ch, now, all_logs, basenames, interval, live, board["alerts"]))
        _signatures(ch, public_key, source, conn["branch"], board["alerts"])
    for _, log in all_logs:
        log.pop("entries", None)  # every log has been searched for reads; the board shows `recent`
    if conn.get("verify"):
        for c in source.branch_commits(head, BRANCH_HISTORY):
            sessions.note(c)
            view = _commit_view(c)
            view["merge"] = len(c["parents"]) != 1
            view["verification"] = (verifications or {}).get(c["sha"])
            board["commits"].append(view)
    board["sessions"] = sessions.listed()
    return board


def _read_directory(source, head, directory, sessions):
    names = source.list_dir(head, directory)
    files, problems = {}, []
    for name in names:
        if channel.role_of(name) is None:
            continue
        try:
            files[name] = source.read_file(head, sources.join(directory, name))
        except sources.SourceError as e:
            problems.append(f"{name}: {e}")
    ch = channel.read_channel(directory, files)
    ch["texts"] = files
    ch["other"] = sorted(n for n in names if channel.role_of(n) is None)
    ch["problems"] = problems + ch["problems"]
    ch["history"] = {}
    for f in ch["files"]:
        if f["role"] in _HISTORY_ROLES:
            history = source.file_commits(head, sources.join(directory, f["name"]), FILE_HISTORY)
            for c in history:
                sessions.note(c)
            ch["history"][f["name"]] = history
    return ch


def _signatures(ch, public_key, source, branch, alerts):
    """Mark each window, answers file and STOP with whether its `.sig.json` holds, then drop the texts."""
    texts = ch.pop("texts")
    if public_key is None:
        return
    repo = source.label if getattr(source, "kind", None) == "github" else None

    def state(name, kind):
        sig = texts.get(actions.sig_path(name))
        if sig is None:
            return {"state": "unsigned"}
        ok, detail = actions.check_file(kind, texts.get(name, ""), sig, public_key, repo, branch,
                                        sources.join(ch["name"], name))
        if ok:
            return {"state": "signed", "by": detail["by"], "at": detail["signed_at"]}
        alerts.append({"level": "warn", "channel": ch["name"], "kind": "signature", "file": name,
                       "text": f"{name}'s signature does not hold ({detail}): a session will not act on it."})
        return {"state": "bad", "reason": detail}

    for w in ch["windows"]:
        w["signature"] = state(w["file"], "window")
    ch["answers_signatures"] = {f["name"]: state(f["name"], "answers") for f in ch["files"] if f["role"] == "answers"}
    if ch["stop"]:
        ch["stop"]["signature"] = state(ch["stop"]["file"], "stop")


def _window_status(w, now):
    if w["problems"]:
        return "invalid"
    start, end = _t(w["start"]), _t(w["end"])
    if start == end:
        return "withdrawn"
    if now < start:
        return "upcoming"
    return "open" if now < end else "ended"


def _interval(entries):
    """A log's heartbeat interval in minutes: the latest one a heartbeat declares, else the median gap between its
    last seven heartbeats, else None."""
    beats = [e for e in entries if e["kind"] == "heartbeat"]
    for e in reversed(beats):
        if e.get("every_minutes"):
            return e["every_minutes"]
    times = [_t(e["time"]) for e in beats[-7:]]
    gaps = [_minutes(b - a) for a, b in zip(times, times[1:]) if b > a]
    return int(statistics.median(gaps)) if gaps else None


def _current_stop(entries):
    """The latest `waiting` entry that nothing but heartbeats followed, or None."""
    for e in reversed(entries):
        if e["kind"] == "waiting":
            return e
        if e["kind"] in ("step", "read", "done"):
            return None
    return None


def _finished(entries):
    """Whether a log's last line that is not a heartbeat says DONE."""
    for e in reversed(entries):
        if e["kind"] != "heartbeat":
            return e["kind"] == "done"
    return False


def _related(a, b):
    """Two directories where one is the other or inside it: a log in `x/validation` reads the `NEXT.md` in `x`."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _reads_of(path, directory, all_logs, basenames):
    """Every read entry, in any log on the board, that names the file `path` (in `directory`)."""
    base = os.path.basename(path)
    unique = basenames.count(base) == 1
    found = []
    for log_dir, log in all_logs:
        for e in log["entries"]:
            if e["kind"] != "read":
                continue
            for r in e["reads"]:
                named = r["path"]
                same = named == path or ((named == base or named.endswith("/" + base))
                                         and (unique or _related(log_dir, directory)))
                if same:
                    found.append((e, r))
    return found


def _judge(ch, now, all_logs, basenames, interval, live, alerts):
    directory = ch["name"]
    history = ch.pop("history")

    def last(name):
        h = history.get(name) or []
        return h[0] if h else None

    def alert(level, text, kind, file=None):
        # `kind` and `file` name the alert steadily, so a page can tell a new one from the same one read again
        alerts.append({"level": level, "channel": directory, "text": text, "kind": kind, "file": file})

    for f in ch["files"]:
        c = last(f["name"])
        f["last_change"] = _commit_view(c) if c else None

    for w in ch["windows"]:
        w["status"] = _window_status(w, now)
        writers = {}
        for c in history.get(w["file"]) or []:
            writers[c["session"]["id"]] = c["session"]
        w["writers"] = [{"kind": s["kind"], "name": s["name"]} for s in writers.values()]
        odd = [s["name"] for s in writers.values() if s["kind"] not in WINDOW_WRITERS]
        w["writer_problem"] = (f"also written by {', '.join(odd)}; a window file is written only by a cloud session "
                               "or the ADE") if odd else None
        if w["writer_problem"]:
            alert("warn", f"{w['file']}: {w['writer_problem']}.", "window-writer", w["file"])
        if w["status"] == "open":
            left = _minutes(_t(w["end"]) - now)
            w["minutes_left"] = left
            alert("info", f"Window '{w['name']}' ({w['file']}) is open for {left} more minutes.", "window-open",
                  w["file"])
        elif w["status"] == "invalid":
            alert("warn", f"{w['file']} is not a valid window: {'; '.join(w['problems'])}.", "window-invalid",
                  w["file"])

    for r in ch["releases"]:
        c = last(r["file"])
        r["written_by"] = c["session"]["name"] if c else None

    for log in ch["logs"]:
        entries = log["entries"]
        every = _interval(entries)
        c = last(log["file"])
        signs = [t for t in (_t(entries[-1]["time"]) if entries else None, _t(c["time"]) if c else None) if t]
        last_sign = max(signs) if signs else None
        beats = [e for e in entries if e["kind"] == "heartbeat"]
        log.update(writer=c["session"]["name"] if c else None, interval_minutes=every,
                   last_line_at=entries[-1]["time"] if entries else None,
                   last_heartbeat_at=beats[-1]["time"] if beats else None,
                   last_heartbeat_unread=beats[-1].get("unread") if beats else None,
                   silent_minutes=_minutes(now - last_sign) if last_sign else None,
                   waiting=_current_stop(entries), entry_count=len(entries),
                   recent=[dict(e, text=e["text"][:400]) for e in reversed(entries[-RECENT:])])
        effective = every or DEFAULT_INTERVAL
        finished = _finished(entries)
        log["live"] = (directory, log["file"]) == live
        log["finished"] = finished
        watched = (log["live"] or log["waiting"]) and not finished
        if watched and last_sign is not None and now - last_sign > timedelta(minutes=2 * effective):
            alert("alert", f"No new line in {log['file']} for {log['silent_minutes']} minutes; its heartbeat is every "
                           f"{effective}. The machine may be asleep or the wait may have ended: only you can look.",
                  "silence", log["file"])
        if log["waiting"]:
            alert("warn", f"{log['file']}: waiting for {log['waiting']['waiting_for']} since "
                          f"{log['waiting']['time']}.", "waiting", log["file"])

    for ins in ch["instructions"]:
        c = last(ins["file"])
        path = sources.join(directory, ins["file"])
        reads = _reads_of(path, directory, all_logs, basenames)
        latest = max(reads, key=lambda er: er[0]["time"]) if reads else None
        ins["changed"] = _commit_view(c) if c else None
        ins["read_at"] = latest[0]["time"] if latest else None
        ins["read_commit"] = latest[1]["commit"] if latest else None
        changed_at = _minute(_t(c["time"])) if c else None
        read_at = _t(latest[0]["time"]) if latest else None
        read_names_it = bool(latest and latest[1]["commit"] and c and c["sha"].startswith(latest[1]["commit"]))
        ins["unread"] = bool(changed_at and all_logs and not read_names_it and (read_at is None or read_at < changed_at))
        if ins["unread"]:
            waited = _minutes(now - changed_at)
            ins["unread_minutes"] = waited
            if waited > interval:
                alert("alert", f"{ins['file']} changed {waited} minutes ago ({c['short']}, {c['session']['name']}) "
                               "and no log says it was read since.", "unread", ins["file"])

    open_questions = [q for q in ch["questions"] if q["open"]]
    if open_questions:
        alert("info", f"{len(open_questions)} question(s) wait for an answer: "
                      f"{', '.join(q['id'] for q in open_questions)}.", "questions")
    if ch["stop"]:
        c = last(ch["stop"]["file"])
        ch["stop"]["written_by"] = c["session"]["name"] if c else None
        alert("alert", f"{ch['stop']['file']} is present: the sessions in this channel are to stop.", "stop",
              ch["stop"]["file"])
    for p in ch["problems"]:
        if not any(p.startswith(w["file"] + ":") for w in ch["windows"]):
            alert("warn", p, "problem")
    return ch
