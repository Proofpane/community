"""One Claude Code conversation's transcript, read only: what an agent's run is measured from.

Claude Code writes a conversation to `<its folder>/projects/<slug>/<session>.jsonl`, the slug its working directory
with every character that is not a letter or a digit written as `-` (usage.py), and each sub-agent's to
`<slug>/<session>/subagents/agent-<id>.jsonl`. A model's reply is written on several lines as it streams, each with the
same `message.id` and the same usage, so a reply, one request, is counted once, by its id (then its `requestId`); a
line with neither is counted as itself. The sub-agents' files are counted too, since a turn's own usage leaves their
requests out.

Only regular files are read, at most MAX_BYTES of them, and one that cannot be read is passed over.
"""

import json
import os
import re
import stat

from .usage import slug

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
MAX_BYTES = 64 * 1024 * 1024
MAX_SUBAGENTS = 500
_SESSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,79}$")
_SUBAGENT = re.compile(r"^agent-[0-9A-Za-z_-]{1,80}\.jsonl$")


def _regular(path):
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def files(worktree, claude_dir, session):
    """The conversation's own transcript and its sub-agents', as (path, size) of the regular files there."""
    if not isinstance(session, str) or not _SESSION.match(session):
        return []
    folder = os.path.join(claude_dir, "projects", slug(os.path.realpath(worktree)))
    out = []
    size = _regular(os.path.join(folder, session + ".jsonl"))
    if size is not None:
        out.append((os.path.join(folder, session + ".jsonl"), size))
    subagents = os.path.join(folder, session, "subagents")
    try:
        names = sorted(n for n in os.listdir(subagents) if _SUBAGENT.match(n))[:MAX_SUBAGENTS]
    except OSError:
        names = []
    for name in names:
        size = _regular(os.path.join(subagents, name))
        if size is not None:
            out.append((os.path.join(subagents, name), size))
    return out


def subagents_left_out(worktree, claude_dir, session):
    """How many of the conversation's sub-agents' files `files` leaves out, past the MAX_SUBAGENTS it takes (the first
    by name): 0 where none is, for a reader that says when it read less than there is (sessionmap.turns' `cut`)."""
    if not isinstance(session, str) or not _SESSION.match(session):
        return 0
    subagents = os.path.join(claude_dir, "projects", slug(os.path.realpath(worktree)), session, "subagents")
    try:
        return max(0, sum(1 for n in os.listdir(subagents) if _SUBAGENT.match(n)) - MAX_SUBAGENTS)
    except OSError:
        return 0


def session_usage(worktree, claude_dir, session):
    """{requests, <each of TOKEN_FIELDS>, bytes}: the conversation's requests, each counted once, with its
    sub-agents'; and the bytes its files take."""
    totals = dict.fromkeys(TOKEN_FIELDS, 0)
    seen, read = set(), 0
    found = files(worktree, claude_dir, session)
    for path, size in found:
        if read + size > MAX_BYTES:
            break
        read += size
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for number, line in enumerate(fh):
                    try:
                        row = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    message = row.get("message") if isinstance(row, dict) else None
                    usage = message.get("usage") if isinstance(message, dict) else None
                    if row.get("type") != "assistant" or not isinstance(usage, dict):
                        continue
                    key = message.get("id") or row.get("requestId") or (path, number)
                    if key in seen:
                        continue
                    seen.add(key)
                    for f in TOKEN_FIELDS:
                        value = usage.get(f)
                        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                            totals[f] += value
        except OSError:
            continue
    return dict(totals, requests=len(seen), bytes=sum(size for _, size in found))


def remove_session(worktree, claude_dir, session):
    """Remove the conversation's transcript and its sub-agents' folder (an agent's storage cap, agents.py)."""
    if not isinstance(session, str) or not _SESSION.match(session):
        return
    folder = os.path.join(claude_dir, "projects", slug(os.path.realpath(worktree)))
    own = os.path.join(folder, session + ".jsonl")
    if _regular(own) is not None:
        os.unlink(own)
    sub = os.path.join(folder, session)
    if os.path.isdir(sub) and not os.path.islink(sub):
        import shutil
        shutil.rmtree(sub, ignore_errors=True)
