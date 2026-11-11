"""Who made a commit. The ADE knows a session by its commits: the author, and a trailer that names the
session. Four kinds follow from what is written today:

- a cloud session: a trailer naming it, such as `Claude-Session: https://claude.ai/code/session_...` (any trailer
  whose key ends in `Session` is read the same way);
- a local session that names itself: `Local-Session: <its id>`, so that two sessions on one machine, which commit
  under the same person's name, can be told apart;
- the ADE itself: a `Proofpane-ADE:` trailer, on a commit it made for the owner (an answer, a window, a stop);
- an agent session on someone's machine: no session trailer, but a co-author trailer that names an agent, such as
  `Co-Authored-By: Claude ... <noreply@anthropic.com>`, on a commit made under that person's name;
- a person: none of these.

A missing trailer does not prove that a person wrote a commit: an agent can commit without one. The kind is what the
commit says about itself, and the board shows it as that, not as a fact about who typed it.
"""

import re
from datetime import datetime, timezone

_TRAILER = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s*(.+?)\s*$")
_AGENT = re.compile(r"claude|codex|anthropic|openai|chatgpt|copilot|gemini", re.I)
ADE_TRAILER = "Proofpane-ADE"


def trailers(message):
    """The `Key: value` lines of a commit message's last paragraph, in order. A message of one paragraph has none."""
    paragraphs = [p for p in (message or "").replace("\r\n", "\n").strip().split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        return []
    out = []
    for line in paragraphs[-1].strip().split("\n"):
        m = _TRAILER.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def utc(text):
    """An ISO 8601 time with any offset, as UTC in the form `2026-09-27T14:12:08Z`; None if it cannot be read."""
    if not isinstance(text, str) or not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_of(author_name, author_email, trailer_list):
    """{id, kind, name, url} for the session or person a commit says made it."""
    for key, value in trailer_list:
        if key.lower() == "local-session":
            return {"id": "local:" + value, "kind": "local", "name": f"local session {value[:8]}", "url": None}
        if key.lower().endswith("session"):
            short = value.rstrip("/").rsplit("/", 1)[-1][-6:]
            kind = "cloud" if "claude.ai/code/" in value else "session"
            return {"id": value, "kind": kind, "name": f"{kind} session {short}",
                    "url": value if value.startswith("https://") else None}
    for key, value in trailer_list:
        if key == ADE_TRAILER:
            return {"id": "ade:" + value, "kind": "ade", "name": f"the ADE for {author_name or author_email}",
                    "url": None}
    if any(key.lower() == "co-authored-by" and _AGENT.search(value) for key, value in trailer_list):
        return {"id": "agent:" + (author_email or author_name or "?").lower(), "kind": "agent",
                "name": f"agent session as {author_name or author_email}", "url": None}
    return {"id": "person:" + (author_email or author_name or "?").lower(), "kind": "person",
            "name": author_name or author_email or "unknown", "url": None}


def make_commit(sha, parents, author_name, author_email, time, message):
    """A commit as the board uses it, from its parts; the session is derived from the author and trailers."""
    message = (message or "").replace("\r\n", "\n")
    subject, _, body = message.strip().partition("\n")
    trailer_list = trailers(message)
    return {"sha": sha, "short": sha[:7], "parents": list(parents or []), "author_name": author_name or "",
            "author_email": author_email or "", "time": utc(time), "subject": subject.strip(),
            "message": message.strip(), "trailers": trailer_list,
            "session": session_of(author_name, author_email, trailer_list)}


def from_github(item):
    """A commit from an item of GitHub's REST API (`GET /repos/{owner}/{repo}/commits`)."""
    commit = item.get("commit") or {}
    author = commit.get("author") or {}
    return make_commit(item.get("sha", ""), [p.get("sha", "") for p in item.get("parents") or []],
                       author.get("name"), author.get("email"), author.get("date"), commit.get("message"))
