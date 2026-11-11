"""The ADE: a channel directory read into the values the board shows.

A channel is a directory that a cloud session and a local session share on one private branch: the instructions
(`NEXT.md` and a handoff), each session's log, the questions put to the owner, their answers, the window files that let
a held step be sent, release files, and `STOP`. `read_channel` takes the directory's files as names and texts and
returns plain dictionaries. It fetches nothing and decides nothing about time: which session wrote a file and when
comes from the repository's commits (`commits.py`), and what is late, open or unread is worked out by the board
(`board.py`) against the time it is given.

The files are written by people and by models, in English and in Chinese, and their shapes drift. So the reader goes
by structure, not wording: a log line is `- <UTC time> · ...`, a window is one `## GO:` heading and one `window:`
line, a question carries an id such as `W-1` or `Q-003`, an answer is `<id>: <text>`. Where a word has to be
recognised (a heartbeat, a processed section, a withdrawn question, an unread handoff), both the English word and the
Chinese one the owner's sessions write are known. The Chinese words and the full-width punctuation around them are
built from their code points, as the repository's other tests do, so this source stays in English. What cannot be
placed is kept as text and never dropped: an unrecognised log line becomes a `step` whose text is the whole line.
"""

import re
from datetime import datetime, timezone


def _chars(*code_points):
    return "".join(chr(c) for c in code_points)


TIME = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z"
_TIME = re.compile(TIME)
DOT = chr(0x00B7)                 # the middle dot the logs put between fields
SEP = " " + DOT + " "
MAX_FILE = 2 * 1024 * 1024        # a channel file larger than this is not read
SECTION_BODY = 1500               # characters of a section kept for the conversation view

# Full-width punctuation, which Chinese text uses around an id and after it.
FW_LPAREN, FW_RPAREN, FW_COLON, FW_COMMA = chr(0xFF08), chr(0xFF09), chr(0xFF1A), chr(0xFF0C)

# Words the owner's sessions write in Chinese, each with its English meaning.
ZH_HEARTBEAT = _chars(0x5FC3, 0x8DF3)                             # "heartbeat"
ZH_PROCESSED = _chars(0x5DF2, 0x5904, 0x7406)                     # "processed"
ZH_WITHDRAWN = _chars(0x64A4, 0x56DE)                             # "withdrawn"
ZH_UNREAD = _chars(0x6709, 0x672A, 0x8BFB, 0x66F4, 0x65B0)        # "has an unread update"
ZH_NOT_UNREAD = _chars(0x65E0, 0x672A, 0x8BFB, 0x66F4, 0x65B0)    # "no unread update"
ZH_EVERY = _chars(0x6BCF)                                          # "every"
ZH_MINUTES = _chars(0x5206, 0x949F)                                # "minutes"

_ROLES = (
    ("signature", re.compile(r".+\.sig\.json")),
    ("stop", re.compile(r"STOP(?:\.md|\.txt)?")),
    ("window", re.compile(r"GO(?:-\d+)?\.md")),
    ("release", re.compile(r"RELEASE(?:-\d+)?\.md")),
    ("instructions", re.compile(r"(?:NEXT|HANDOFF[A-Za-z0-9_-]*)\.md")),
    ("answers", re.compile(r"[A-Za-z0-9_-]*answers[A-Za-z0-9_-]*\.md", re.I)),
    ("questions", re.compile(r"[A-Za-z0-9_-]*questions[A-Za-z0-9_-]*\.md", re.I)),
    ("log", re.compile(r"(?:[A-Za-z0-9_-]+-)?log\.md", re.I)),
    ("readme", re.compile(r"README\.md")),
)

_OPEN = "[(" + FW_LPAREN + "]"
_CLOSE = "[)" + FW_RPAREN + "]"
_NOT_CLOSE = "[^)" + FW_RPAREN + "]"
_COLON = "[:" + FW_COLON + "]"

_LOG_LINE = re.compile(r"^- (" + TIME + r") " + DOT + r" (.*)$")
_HEX = re.compile(r"[0-9a-f]{7,40}")
_PATH_AT = re.compile(r"([A-Za-z0-9_./-]+\.md)(?:\s*@\s*([0-9a-f]{7,40})\b|\s*" + _OPEN + r"\s*([0-9a-f]{7,40})\b)?")
_WAITING = re.compile(r"WAITING FOR ([A-Z][A-Z]+)")
_WAITING_PREFIX = re.compile(r"^WAITING FOR [A-Z]+\s*" + _COLON + r"\s*")
_EVERY_MINUTES = re.compile(r"heartbeat every (\d+) min", re.I)
_ZH_EVERY_MINUTES = re.compile(ZH_HEARTBEAT + ZH_EVERY + r"\s*(\d+)\s*" + ZH_MINUTES)

_QID = r"[A-Z]{1,3}-\d{1,4}|[A-Z]"
_QIDS_IN_TEXT = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{1,3}-\d{1,4})(?![0-9])")
_Q_HEADING = re.compile(r"^#{2,4} (" + _QID + r")\s*" + DOT + r"\s*(.*)$")
_Q_WAITING = re.compile(r"^WAITING FOR ([A-Z][A-Z ]*?)\s*" + _OPEN + r"\s*(" + _QID + r")(?![A-Za-z0-9-])(" +
                        _NOT_CLOSE + r"*)" + _CLOSE + r"\s*" + _COLON + r"?\s*(.*)$")
_WITHDRAWN = re.compile(r"^\s*-\s*\*\*(" + _QID + r")\s*[:," + FW_COLON + FW_COMMA + r"]\s*(?:withdrawn|" +
                        ZH_WITHDRAWN + ")", re.I)
_ANSWER = re.compile(r"^(" + _QID + r")\s*(?:" + _OPEN + _NOT_CLOSE + r"*" + _CLOSE + r")?\s*" + _COLON +
                     r"\s*(.+?)\s*$")

_GO_HEADING = re.compile(r"^## GO: (.+?)\s*$")
_WINDOW_LINE = re.compile(r"^window: (" + TIME + r") to (" + TIME + r")\s*$")
_KEY_FILE = re.compile(r"^key file: (.+?)\s*$")
_RELEASE_HEADING = re.compile(r"^## RELEASE: (.+?)\s*$")
_AT_UTC = re.compile(r"\bat (\d{1,2}):(\d{2}) UTC\b")


def parse_time(text):
    """A UTC time as the channel writes it, `2026-09-27T04:05Z` or with seconds, as an aware datetime; else None."""
    if not isinstance(text, str) or not _TIME.fullmatch(text):
        return None
    fmt = "%Y-%m-%dT%H:%M:%SZ" if text.count(":") == 2 else "%Y-%m-%dT%H:%MZ"
    try:
        return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def role_of(name):
    """What a file in a channel directory is, from its name alone, or None for a file the ADE does not read."""
    for role, pattern in _ROLES:
        if pattern.fullmatch(name):
            return role
    return None


def read_channel(name, files):
    """The channel directory `name`, whose files are `files` ({file name: text}), read into a dictionary.

    Files the ADE does not read (scripts, data) are listed under `other`. A file too large to read is named in
    `problems`, as is anything a window or release file lacks; nothing raises for a file's content."""
    out = {"name": name, "files": [], "other": [], "readme": None, "instructions": [], "logs": [], "windows": [],
           "releases": [], "questions": [], "answers": [], "signatures": [], "stop": None, "problems": []}
    for file_name in sorted(files):
        text = files[file_name]
        role = role_of(file_name)
        if role is None:
            out["other"].append(file_name)
            continue
        if not isinstance(text, str):
            out["problems"].append(f"{file_name} is not text")
            continue
        if len(text.encode("utf-8")) > MAX_FILE:
            out["problems"].append(f"{file_name} is larger than {MAX_FILE // (1024 * 1024)} MB and was not read")
            continue
        out["files"].append({"name": file_name, "role": role})
        if role == "readme":
            out["readme"] = read_readme(text)
        elif role == "instructions":
            out["instructions"].append(read_instructions(file_name, text))
        elif role == "log":
            out["logs"].append(read_log(file_name, text))
        elif role == "window":
            out["windows"].append(read_window(file_name, text))
        elif role == "release":
            out["releases"].append(read_release(file_name, text))
        elif role == "questions":
            out["questions"].extend(read_questions(file_name, text))
        elif role == "answers":
            out["answers"].extend(read_answers(file_name, text))
        elif role == "signature":
            out["signatures"].append(file_name)
        elif role == "stop":
            out["stop"] = {"file": file_name, "text": text.strip()[:2000]}
    for window in out["windows"]:
        out["problems"].extend(f"{window['file']}: {p}" for p in window["problems"])
    for release in out["releases"]:
        out["problems"].extend(f"{release['file']}: {p}" for p in release["problems"])
    _answer_questions(out["questions"], out["answers"])
    return out


def _lines(text):
    return text.replace("\r\n", "\n").split("\n")


def read_readme(text):
    """The first `#` heading and the first paragraph after it."""
    title, paragraph, started = None, [], False
    for line in _lines(text):
        if title is None:
            if line.startswith("# "):
                title = line[2:].strip()
            continue
        if not line.strip():
            if started:
                break
            continue
        if line.startswith("#"):
            break
        started = True
        paragraph.append(line.strip())
    return {"title": title, "summary": " ".join(paragraph)[:1000] or None}


def read_instructions(file_name, text):
    """`NEXT.md` or a handoff: its title and its `##` sections, in the order written. A handoff grows by sections
    ("Added by the cloud session at 21:52 UTC: ..."), so its last section is what was added last. Each section keeps
    the time of day its heading gives (`at`, "21:52", or None) and the start of its text (`body`), which the
    conversation view shows as the cloud session's message."""
    title, sections, lines = None, [], _lines(text)
    for number, line in enumerate(lines, 1):
        if line.startswith("# ") and title is None:
            title = line[2:].strip()
        elif line.startswith("## "):
            heading = line[3:].strip()
            at = _AT_UTC.search(heading)
            sections.append({"line": number, "heading": heading,
                             "at": f"{int(at.group(1)):02d}:{at.group(2)}" if at else None})
    for i, section in enumerate(sections):
        end = sections[i + 1]["line"] - 1 if i + 1 < len(sections) else len(lines)
        body = "\n".join(lines[section["line"]:end]).strip()
        section["body"] = body if len(body) <= SECTION_BODY else body[:SECTION_BODY].rstrip() + " [...]"
    return {"file": file_name, "title": title, "sections": sections,
            "last_section": sections[-1]["heading"] if sections else None}


def read_log(file_name, text):
    """A session's log: one entry per `- <UTC time> · ...` line; any other line (the header, notes) is skipped.

    An entry's `kind` is `heartbeat` (the line's first field after an optional commit is the word heartbeat),
    `read` (the text starts `read <file>`), `done` (a field starts `DONE:`), `waiting` (a stop: `WAITING FOR
    <NAME>`), or `step` for anything else. `commit` is the short commit a line names first, where it names one."""
    entries = []
    for number, line in enumerate(_lines(text), 1):
        entry = read_log_line(line)
        if entry is not None:
            entry["line"] = number
            entries.append(entry)
    return {"file": file_name, "entries": entries}


def read_log_line(line):
    """One log line as a dictionary, or None when the line is not a log entry."""
    m = _LOG_LINE.match(line.rstrip())
    if not m:
        return None
    fields = m.group(2).split(SEP)
    entry = {"time": m.group(1), "commit": None, "kind": "step", "label": None, "text": m.group(2).strip()}
    if len(fields) > 1 and _HEX.fullmatch(fields[0].strip()):
        entry["commit"] = fields[0].strip()
        fields = fields[1:]
    text = SEP.join(fields).strip()
    first = fields[0].strip()
    if first.lower() == "heartbeat" or first == ZH_HEARTBEAT:
        body = SEP.join(fields[1:]).strip()
        entry.update(kind="heartbeat", text=body, state=fields[1].strip() if len(fields) > 1 else "",
                     unread=_unread(body), every_minutes=_every_minutes(body))
        return entry
    entry["text"] = text
    if text.startswith("read "):
        entry["kind"] = "read"
        entry["reads"] = _reads(text)
        return entry
    done = next((i for i, f in enumerate(fields) if f.strip().startswith("DONE:")), None)
    if done is not None:
        entry.update(kind="done", label=first if done > 0 else None, text=SEP.join(fields[done:]).strip())
        return entry
    waiting = _WAITING.search(text)
    if waiting:
        entry.update(kind="waiting", waiting_for=waiting.group(1), ids=sorted(set(_QIDS_IN_TEXT.findall(text))))
        if len(fields) > 1 and "WAITING FOR" not in first:
            entry.update(label=first, text=SEP.join(fields[1:]).strip())
        return entry
    if len(fields) > 1:
        entry.update(label=first, text=SEP.join(fields[1:]).strip())
    return entry


def _reads(text):
    """The files a read line names, each once, with the commit named right after it (`file @ abc1234` or
    `file (abc1234`). A file named again later in the line without a commit is the same read, not another."""
    reads, seen = [], {}
    for path, at, paren in _PATH_AT.findall(text[:600]):
        commit = at or paren or None
        if path in seen:
            if commit and seen[path]["commit"] is None:
                seen[path]["commit"] = commit
            continue
        seen[path] = {"path": path, "commit": commit}
        reads.append(seen[path])
    return reads


def _unread(text):
    """Whether a heartbeat says the instructions have an update not yet read: True, False, or None if it does not
    say. "No unread" is looked for first, since it contains "unread"."""
    lowered = text.lower()
    if "no unread" in lowered or ZH_NOT_UNREAD in text:
        return False
    if "unread update" in lowered or ZH_UNREAD in text:
        return True
    return None


def _every_minutes(text):
    m = _EVERY_MINUTES.search(text) or _ZH_EVERY_MINUTES.search(text)
    return int(m.group(1)) if m else None


def read_window(file_name, text):
    """A window file: exactly one `## GO: <name>` heading and exactly one `window: <start> to <end>` line, both
    times in UTC. A file with more or fewer of either, or a line that starts `window:` in another form, is invalid:
    `problems` says why, and the board treats the window as closed. A window whose end is its start was withdrawn."""
    headings, windows, odd, key_file = [], [], 0, None
    for line in _lines(text):
        m = _GO_HEADING.match(line)
        if m:
            headings.append(m.group(1))
            continue
        m = _WINDOW_LINE.match(line)
        if m:
            windows.append((m.group(1), m.group(2)))
            continue
        if line.startswith("window:"):
            odd += 1
            continue
        m = _KEY_FILE.match(line)
        if m and key_file is None:
            key_file = m.group(1)
    problems = []
    if len(headings) != 1:
        problems.append(f"it has {len(headings)} '## GO:' headings; a window file has exactly one")
    if odd:
        problems.append("a line starts 'window:' but is not 'window: <UTC time> to <UTC time>'")
    if len(windows) != 1:
        problems.append(f"it has {len(windows)} window lines; a window file has exactly one")
    start = end = None
    if len(windows) == 1:
        start, end = windows[0]
        if parse_time(start) is None or parse_time(end) is None:
            problems.append("its window line has a time that does not exist")
        elif parse_time(end) < parse_time(start):
            problems.append("its window ends before it starts")
    return {"file": file_name, "name": headings[0] if len(headings) == 1 else None, "start": start, "end": end,
            "key_file": key_file, "problems": problems}


def read_release(file_name, text):
    """A release file: exactly one `## RELEASE: <name>` line, the exact line a waiting session looks for."""
    names = [m.group(1) for m in map(_RELEASE_HEADING.match, _lines(text)) if m]
    problems = [] if len(names) == 1 else [f"it has {len(names)} '## RELEASE:' lines; a release file has one"]
    return {"file": file_name, "name": names[0] if len(names) == 1 else None, "problems": problems}


def _is_processed_heading(heading):
    return bool(re.search(r"\b(?:processed|answered)\b", heading, re.I)) or ZH_PROCESSED in heading


def read_questions(file_name, text):
    """Every question in a questions file, once each.

    A question is either a heading (`### Q-004 · <time> · WAITING FOR ANA: <what> · processed <time>`) or a
    paragraph that starts `WAITING FOR ANA (W-1): ...`. The same id again (a translation further down, an update)
    does not make a second question. A question is processed when its heading says so or when it sits under a `##`
    section named processed or answered; withdrawn when a bullet says `**W-1: withdrawn`."""
    questions, by_id, withdrawn = [], {}, set()
    section, processed_section = None, False
    lines = _lines(text)
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section = line[3:].strip()
            processed_section = _is_processed_heading(section)
        w = _WITHDRAWN.match(line)
        if w:
            withdrawn.add(w.group(1))
        q = None
        m = _Q_HEADING.match(line)
        if m:
            fields = [f.strip() for f in m.group(2).split(DOT)]
            asked = next((f for f in fields if _TIME.fullmatch(f)), None)
            done_field = next((f for f in fields if _is_processed_heading(f)), None)
            summary = next((f for f in fields if f != asked and f is not done_field), "")
            waiting = _WAITING.search(summary)
            done_time = _TIME.search(done_field) if done_field else None
            q = {"id": m.group(1), "asked_at": asked, "for": waiting.group(1) if waiting else None,
                 "summary": _WAITING_PREFIX.sub("", summary)[:500], "processed": bool(done_field) or processed_section,
                 "processed_at": done_time.group(0) if done_time else None, "section": section, "line": i + 1}
        else:
            m = _Q_WAITING.match(line)
            if m:
                paragraph = [m.group(4)]
                for later in lines[i + 1:]:
                    if not later.strip() or later.startswith("#"):
                        break
                    paragraph.append(later.strip())
                asked = _TIME.search(m.group(3))
                q = {"id": m.group(2), "asked_at": asked.group(0) if asked else None, "for": m.group(1).strip(),
                     "summary": " ".join(p for p in paragraph if p)[:500], "processed": processed_section,
                     "processed_at": None, "section": section, "line": i + 1}
        if q is None:
            continue
        q["file"] = file_name
        if q["id"] in by_id:
            earlier = by_id[q["id"]]
            earlier["processed"] = earlier["processed"] or q["processed"]
            earlier["processed_at"] = earlier["processed_at"] or q["processed_at"]
        else:
            by_id[q["id"]] = q
            questions.append(q)
    for q in questions:
        q["withdrawn"] = q["id"] in withdrawn
    return questions


def read_answers(file_name, text):
    """Every answer line, `<id>: <text>`, in the order written. Lines before the first `---` are the file's header
    (which may itself show the form, as in "one line each, for example `W-1: here, retry`") and are skipped when the
    file has such a line."""
    lines = _lines(text)
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() == "---"), 0)
    answers = []
    for number, line in enumerate(lines[start:], start + 1):
        m = _ANSWER.match(line.strip())
        if m:
            answers.append({"id": m.group(1), "text": m.group(2)[:2000], "file": file_name, "line": number})
    return answers


def _answer_questions(questions, answers):
    """Mark each question with the last answer given to its id, if any, and whether it is still open: not
    processed, not withdrawn and not answered."""
    last = {}
    for a in answers:
        last[a["id"]] = a
    for q in questions:
        a = last.get(q["id"])
        q["answer"] = {"text": a["text"], "file": a["file"], "line": a["line"]} if a else None
        q["open"] = not (q["processed"] or q["withdrawn"] or a)
