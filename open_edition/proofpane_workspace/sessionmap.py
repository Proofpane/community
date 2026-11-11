"""The control window's map of a workspace's sessions: every session with its agents (GET /map), and one session's
tool calls and turns (GET /sessions/{id}/map). Read only, from each session's own files and its conversation's
transcript, never by asking the session's program.

Where each part comes from, each the session's own word about itself, for display (as activity.json is):

- A session's state is the page's list's (ui/src/store.ts sessionState, written out here as state_of) from the same
  view of the session, so the map and the list never disagree; its account is the accounts' record's (null for a
  program that runs on none, and for a claude on another provider's model, which runs on the person's key); its
  model, effort and tokens in context are its status line's (statusline.py; a chat's claude, run through the Agent
  SDK, runs no status line, and its model and effort are the ones it was started with).
- Its agents (level 4) are the sub-agent status line's (subagentline.py: name or description, type, status, start,
  model, effort, tokens) joined, by the agent's id, which is the same in both (subagentline.py says where that was
  read), with the activity hook's starts and stops (activity.py --map: so an agent that ran between two of the line's
  ticks is seen, and its end). `agents_read` says whether they were read: "yes"; "version" where the session's last
  start gave it no sub-agent status line, its Claude Code being older than settings.SUBAGENT_STATUS_LINE_FROM;
  "missing" where its start gave it none of the map's sources (the map's settings file is not there: a session last
  started before this, a chat, which is given none, or a start whose settings could not be read); "no" for a session
  that is not Claude Code's. Where it is not "yes", `agents` is empty.
- Its tool calls (level 5) are the activity hook's (calls.jsonl), each under the agent it was made in.
- Its turns are its transcript's, read as runtree reads a run's (a turn begins at each message to the conversation,
  each request counted once, by its message id), from the conversation the session's claude last spoke from
  (workspace._own_conversation), its sub-agents' files beside it (transcripts.files); only the last MAX_TRANSCRIPT
  bytes of each file are read, and a file's turns are kept read until it changes.

No field carries a message's text, a prompt, a reply, a tool's input beyond its target, or `last_assistant_message`:
an agent's name is its name, else its description, both a model's words, cut to subagentline.NAME characters and
shown only in the map (never in a notification, a push or the page's title).
"""

import json
import os
import re
import stat
import threading
import time

from . import accounts as accounting, activity, statusline, subagentline, transcripts

MAX_ANSWER = 64 * 1024          # a session's calls and turns, as runtree.MAX_TREE cuts a run's tree
MAX_AGENTS = 50                 # a session's agents in GET /map: its running ones first, then the newest
MAX_CALL_AGENTS = 200           # the agents GET /sessions/{id}/map lists calls for
MAX_SETTINGS = 256 * 1024
MAX_TRANSCRIPT = 4 * 1024 * 1024
MAP_SETTINGS = "map-settings.json"      # what the session's last start gave its claude (workspace._with_map)
ENDED = subagentline.ENDED
CLAUDE = ("claude", "chat")
SIGN_INS = ("github", "claude-sign-in", "codex-sign-in")
_TOOL = activity.NAME
_AGENT_ID = activity.AGENT_ID
_FILE = re.compile(r"agent-([0-9A-Za-z_-]{1,80})\.jsonl$")


def _tail(path, most):
    """(the last `most` bytes of a regular file, whether its start was left out), or (None, False) for none: no file,
    a link, a FIFO or another kind, which a session's programs can put where a file goes. Never follows a link, never
    waits on a FIFO."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None, False
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, False
        start = max(0, st.st_size - most)
        os.lseek(fd, start, os.SEEK_SET)
        chunks, left = [], most
        while left > 0:
            got = os.read(fd, min(left, 1 << 20))
            if not got:
                break
            chunks.append(got)
            left -= len(got)
        return b"".join(chunks), start > 0
    except OSError:
        return None, False
    finally:
        os.close(fd)


def _rows(data, cut):
    """The JSON objects of a file of lines, the first line left out when the file's start was (it is a part of one)."""
    lines = data.split(b"\n")
    if cut:
        lines = lines[1:]
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _lines(path, most):
    data, cut = _tail(path, most)
    return None if data is None else _rows(data, cut)


def _whole(value, low=0):
    return value if type(value) is int and value >= low else None


def read_json(path, most):
    """A small JSON file of a session's folder, read as _tail reads one (never a link followed, never a FIFO waited
    on), or None: none there, past `most` bytes, or not JSON. For the workspace's reads the map's poll reaches
    (activity.json, a conversation's id)."""
    data, cut = _tail(path, most + 1)
    if data is None or cut or len(data) > most:
        return None
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None


# what the session's last start gave its claude


def given(session_dir):
    """The settings the session's last start gave its claude for the map (MAP_SETTINGS), or None when it gave none."""
    data, cut = _tail(os.path.join(session_dir, MAP_SETTINGS), MAX_SETTINGS + 1)
    if data is None or cut or len(data) > MAX_SETTINGS:
        return None
    try:
        got = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None
    return got if isinstance(got, dict) else None


def _maps(made, event):
    """Whether settings `made` run activity.py --map on hook `event`."""
    groups = made.get("hooks", {}).get(event) if isinstance(made.get("hooks"), dict) else None
    for group in groups if isinstance(groups, list) else []:
        for hook in group.get("hooks", []) if isinstance(group, dict) and isinstance(group.get("hooks"), list) else []:
            args = hook.get("args") if isinstance(hook, dict) else None
            if isinstance(args, list) and args[-1:] == ["--map"] and any(
                    isinstance(a, str) and os.path.basename(a) == "activity.py" for a in args):
                return True
    return False


def _read(program, made, source):
    """`agents_read` or `calls_read` for a session of `program` whose last start gave `made` (given()); `source` says
    whether `made` gives the source read."""
    if program not in CLAUDE:
        return "no"
    if made is None:
        return "missing"
    return "yes" if source else "version"


# level 4: the session's agents


def _hooked(session_dir):
    """The activity hook's starts and stops, by agent: {id: {type, first, last_start, last_stop}}."""
    out = {}
    for e in _lines(os.path.join(session_dir, activity.AGENT_EVENTS), activity.MAX_AGENT_EVENTS) or []:
        at, agent = _whole(e.get("at"), 1), e.get("id")
        if at is None or e.get("event") not in ("start", "stop") or not isinstance(agent, str) \
                or not _AGENT_ID.fullmatch(agent):
            continue
        h = out.setdefault(agent, {"type": None, "first": None, "last_start": None, "last_stop": None})
        if isinstance(e.get("type"), str) and e["type"].strip() and h["type"] is None:
            h["type"] = e["type"][:subagentline.NAME]
        if e["event"] == "start":
            h["first"] = at if h["first"] is None else min(h["first"], at)
            h["last_start"] = at if h["last_start"] is None else max(h["last_start"], at)
        else:
            h["last_stop"] = at if h["last_stop"] is None else max(h["last_stop"], at)
    return out


def _agent(agent_id, line, hook, live_since=None, stopped_at=None):
    """One agent of the map, from the status line's row (`line`) and the activity hook's starts and stops (`hook`),
    either None. The newer word wins: the line's status as it last saw the agent, unless the hook saw it start after
    that (a run the line has not seen: running until its stop, then ended, how not said, null), or stop at or after
    that while the line saw it running (ended, null: a stop does not say how, and the two clocks are whole seconds, so
    a stop in the second the line saw it is taken as after it). Its end is the hook's stop, else the time the line
    first saw it ended, else none; there is none while it runs.

    An agent's program can end without either source saying so (the owner's Stop, a restart, a crash end its agents
    with no SubagentStop, and the line never shows them again), so an agent still running by both is running only while
    its session's program runs and was seen in this run of it: with the session not running (`live_since` None), it
    ended, how not said, at the program's end (`stopped_at`) where that is known and not before it was last seen;
    seen only before this run started (`live_since`), it ended in an earlier run, when not known."""
    line, hook = line or {}, hook or {}
    last_start, last_stop = hook.get("last_start"), hook.get("last_stop")
    stopped = last_stop if last_stop is not None and (last_start is None or last_stop >= last_start) else None
    status = line.get("status")
    seen = line.get("seen_at") or 0
    if not line:
        status = "running" if last_start is not None and stopped is None else None
    elif last_start is not None and last_start > seen:
        status = "running" if stopped is None else None
    elif stopped is not None and stopped >= seen and status == "running":
        status = None
    ended = None
    if status != "running":
        ended = stopped if stopped is not None else (line.get("ended_at") if status in ENDED else None)
    else:
        last_seen = max(t for t in (seen, last_start or 0))
        if live_since is None:
            status = None
            ended = stopped_at if type(stopped_at) is int and stopped_at >= last_seen else None
        elif last_seen < live_since:
            status = None
    return {"id": agent_id, "name": line.get("name"), "agent_type": line.get("agent_type") or hook.get("type"),
            "status": status, "started_at": line.get("started_at") or hook.get("first"), "ended_at": ended,
            "tokens": line.get("tokens"), "model": line.get("model"), "effort": line.get("effort")}


def agents(session_dir, most=MAX_AGENTS, live_since=0, stopped_at=None):
    """The session's agents (level 4), at most `most`: the running ones first, then the newest; oldest first. An agent
    the hook saw only stop (one of Claude Code's own: prompt suggestions, /btw) is left out. `live_since` is when the
    session's program started this run (None: it is not running; 0, the default, takes every sighting as this run's)
    and `stopped_at` when it ended, for _agent."""
    return _counted_agents(session_dir, most, live_since, stopped_at)[0]


def _counted_agents(session_dir, most=MAX_AGENTS, live_since=0, stopped_at=None):
    """agents', and how many past `most` it left out (a session row's agents_more: the cap is said, never silent)."""
    lines = {row["id"]: row for row in subagentline.read(session_dir) or []}
    hooked = _hooked(session_dir)
    ids = list(lines) + [a for a, h in hooked.items() if a not in lines and h["first"] is not None]
    found = [_agent(a, lines.get(a), hooked.get(a), live_since, stopped_at) for a in ids]
    chosen = sorted(found, key=lambda a: (a["status"] == "running", a["started_at"] or 0), reverse=True)[:most]
    return sorted(chosen, key=lambda a: (a["started_at"] is None, a["started_at"] or 0)), len(found) - len(chosen)


# level 3: a session


def _truthy(value):
    """Whether JavaScript takes `value` for true, as the page's list does: an object or a list, even an empty one, is."""
    return value is not None and value is not False and value != 0 and value != ""


def state_of(view):
    """A session's state as the page's list says it (ui/src/store.ts sessionState), from the same view of it:
    `starting` is the page's own, for a session it has not been given yet."""
    if view.get("program") == "chat":
        chat = view.get("chat") if isinstance(view.get("chat"), dict) else {}
        state = chat.get("state")
        if state == "running":
            return "working"
        if state == "needs_you" or len(chat.get("asks") or []) > 0:
            return "waiting"
        if _truthy(view.get("finished")):
            return "done"
        if state == "error":
            return "failed"
        return "idle"
    if _truthy(view.get("running")):
        said = view.get("activity").get("state") if isinstance(view.get("activity"), dict) else None
        return said if said in ("working", "waiting", "idle") else "running"
    if _truthy(view.get("finished")):
        return "done"
    code = view.get("exit_code")
    if code is not None:
        return "done" if code == 0 else "stopped" if code < 0 else "failed"
    return "stopped"


def left_out(view):
    """Whether the page's list leaves a session out (ui/src/store.ts isSignIn, and a closed one), as the map does: each
    field taken for true or false as JavaScript takes it."""
    if _truthy(view.get("closed")):
        return True
    return view.get("program") in SIGN_INS and (view.get("sign_in") is True or not _truthy(view.get("running")))


def _text(value):
    """A record's field as text, or None: a session's programs can write their own record (Workspace._read)."""
    return value if isinstance(value, str) else None


def _title(view):
    """The session's title as the list shows it (ui/src/store.ts toSession)."""
    title = _text(view.get("title")) or (_text(view.get("task")) or "").split("\n")[0].strip()
    return title or ("Shell" if view.get("program") == "shell" else _text(view.get("program")))


def _updated(view):
    """When it last changed, as the list says it (ui/src/store.ts toSession's updatedAt)."""
    chat = view.get("chat") if isinstance(view.get("chat"), dict) else {}
    finished = view.get("finished") if isinstance(view.get("finished"), dict) else {}
    activity_ = view.get("activity") if isinstance(view.get("activity"), dict) else {}
    for value in (view.get("ended_at"), activity_.get("at"), chat.get("last_at"), finished.get("at"),
                  view.get("created_at")):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return None


def _account(view):
    kind = accounting.KIND_OF.get(_text(view.get("program")))
    if kind is None or view.get("model_provider"):
        return None
    return {"kind": kind, "id": _text(view.get("account")) or accounting.DEFAULT}


def session_row(view, session_dir, now=None, live_since=0, stopped_at=None):
    """One session of GET /map, from its view (Workspace._view) and its folder; `live_since` and `stopped_at` say when
    its program started this run and when it ended (agents)."""
    program = _text(view.get("program"))
    status = statusline.read(session_dir) if program == "claude" else None
    status = status or {}
    chosen = (view.get("chat") or {}).get("chosen") if program == "chat" and isinstance(view.get("chat"), dict) else None
    chosen = chosen if isinstance(chosen, dict) else {}
    made = given(session_dir) if program == "claude" else None
    read = _read(program, made, made is not None and isinstance(made.get("subagentStatusLine"), dict))
    found, more = _counted_agents(session_dir, live_since=live_since, stopped_at=stopped_at) if read == "yes" \
        else ([], 0)
    return {"id": view.get("id"), "title": _title(view), "program": program, "repo": _text(view.get("repo")),
            "branch": _text(view.get("branch")), "account": _account(view),
            "model": status.get("model") or chosen.get("model"), "effort": status.get("effort") or chosen.get("effort"),
            "state": state_of(view), "started_at": _whole(view.get("created_at")), "updated_at": _updated(view),
            "context_tokens": status.get("context_tokens"), "agents_read": read, "agents": found, "agents_more": more}


# level 5 and the turns: one session's calls


def calls(session_dir):
    """The activity hook's calls (calls.jsonl), oldest first: [{agent, tool, target, failed, at}]."""
    out = []
    for e in _lines(os.path.join(session_dir, activity.CALLS), activity.MAX_CALLS) or []:
        at, tool, agent = _whole(e.get("at"), 1), e.get("tool"), e.get("agent")
        if at is None or not isinstance(tool, str) or not _TOOL.fullmatch(tool):
            continue
        if agent is not None and not (isinstance(agent, str) and _AGENT_ID.fullmatch(agent)):
            continue
        target = e.get("target") if isinstance(e.get("target"), str) else ""
        out.append({"agent": agent, "tool": tool, "target": target[:activity.TARGET] or None,
                    "failed": e.get("failed") is True, "at": at})
    return out


_turns_kept = {}            # a transcript's path -> (its identity when read, its turns, whether its start was cut)
_turns_lock = threading.Lock()
MAX_KEPT = 1024             # above transcripts.MAX_SUBAGENTS, so one conversation's files are all kept read


def _said(content):
    """A message's text, as runtree._text reads it, but taking only text that is text: a line a session's program
    wrote out of shape counts as no text, never fails the read."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                        and isinstance(b.get("text"), str))
    return ""


def walk(rows):
    """A conversation file's turns, as runtree._walk counts them (a turn begins at each message to the conversation:
    a user line with text that is no tool's result and no compaction's summary; a reply is one request, counted once
    by its message id, then its request id): [{requests, input_tokens, output_tokens}], input counting cache reads
    and writes in, as the status line's context does."""
    turns, seen, current = [], set(), None
    for row in rows:
        kind = row.get("type")
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if kind == "user":
            if row.get("isCompactSummary"):
                continue
            content = message.get("content")
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
                if isinstance(content, list) else []
            if not results and _said(content).strip():
                current = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
                turns.append(current)
            continue
        if kind != "assistant":
            continue
        if current is None:
            current = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
            turns.append(current)
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
        key = message.get("id") or row.get("requestId")
        key = key if isinstance(key, str) else None     # a line a session's program wrote out of shape: as itself
        if usage is None or (key is not None and key in seen):
            continue
        if key is not None:
            seen.add(key)
        current["requests"] += 1
        for field in transcripts.TOKEN_FIELDS:
            value = usage.get(field)
            if type(value) is int and value > 0:
                current["output_tokens" if field == "output_tokens" else "input_tokens"] += value
    return turns


def _turns_of(path):
    """(turns, whether the file's start was left out) of one transcript, read again only once it changed."""
    try:
        st = os.lstat(path)
    except OSError:
        return None, False
    if not stat.S_ISREG(st.st_mode):
        return None, False
    identity = (st.st_ino, st.st_size, st.st_mtime_ns)
    with _turns_lock:
        kept = _turns_kept.get(path)
    if kept is not None and kept[0] == identity:
        return kept[1], kept[2]
    data, cut = _tail(path, MAX_TRANSCRIPT)
    if data is None:
        return None, False
    try:
        turns = walk(_rows(data, cut))
    except Exception:  # noqa: BLE001  a transcript a session's program wrote out of shape is not read, never a 500
        return None, False
    with _turns_lock:
        if len(_turns_kept) >= MAX_KEPT and path not in _turns_kept:
            _turns_kept.pop(next(iter(_turns_kept)))
        _turns_kept[path] = (identity, turns, cut)
    return turns, cut


def turns(worktree, claude_dir, conversation):
    """(turns by agent, whether a file's start was left out), or (None, False) when the conversation's transcript is
    not where Claude Code keeps it (usage.py, transcripts.py): [(agent id or None, [turn])], the session's own first."""
    if not worktree or not claude_dir or not conversation:
        return None, False
    found = transcripts.files(worktree, claude_dir, conversation)
    if not found or not found[0][0].endswith(conversation + ".jsonl"):
        return None, False
    # sub-agents' files past the ones transcripts.files takes are left out too, and said
    out, cut = [], transcripts.subagents_left_out(worktree, claude_dir, conversation) > 0
    for n, (path, _) in enumerate(found):
        agent = None
        if n:
            m = _FILE.search(os.path.basename(path))
            if not m:
                continue
            agent = m.group(1)
        got, was_cut = _turns_of(path)
        if got is None:
            if not n:
                return None, False          # the conversation itself not read: its turns are "missing"
            cut = True                      # a sub-agent's file left out is said by `cut`
            continue
        out.append((agent, got))
        cut = cut or was_cut
    return out, cut


def size(out):
    """`out`'s bytes as the agent sends it (Starlette's JSONResponse: UTF-8, no spaces)."""
    return len(json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def fit(answer, groups, most=MAX_ANSWER):
    """`answer` with its calls and turns cut until it is at most `most` bytes as the agent sends it, halving a cap as
    runtree.compact cuts a run, but keeping each holder's newest calls where compact keeps a turn's first (the map
    shows what a session does now): each holder's calls cut to its newest `cap`, the rest counted in its `more`, and
    each agent's turns to its last `cap`, until it fits. Still past `most` with one call and one turn a holder, the
    sub-agents' turns are left out, the oldest first, then the agents, the last listed first, then the session's own
    turns, the oldest first: the session's own calls and its newest turns are the last to go. `cut` says whether
    anything was left out, here or before (a transcript read from its end or not read, an agent's calls past
    MAX_CALL_AGENTS). `groups` is the turns by agent."""
    cap = 400
    while True:
        out = json.loads(json.dumps(answer))
        cut = answer["cut"]
        for holder in [out["main"], *out["agents"]]:
            if len(holder["calls"]) > cap:
                holder["more"] += len(holder["calls"]) - cap
                holder["calls"] = holder["calls"][-cap:]
                cut = True
        out["turns"] = []
        for agent, kept in groups:
            if len(kept) > cap:
                kept, cut = kept[-cap:], True
            out["turns"] += [dict(t, agent=agent) for t in kept]
        out["cut"] = cut
        if size(out) <= most or cap <= 1:
            break
        cap //= 2
    while size(out) > most and (out["agents"] or out["turns"]):
        theirs = next((n for n, t in enumerate(out["turns"]) if t["agent"] is not None), None)
        if theirs is not None:
            out["turns"].pop(theirs)
        elif out["agents"]:
            out["agents"].pop()
        else:
            out["turns"].pop(0)
        out["cut"] = True
    return out


def session_calls(session_id, program, session_dir, worktree, claude_dir, conversation, now=None):
    """GET /sessions/{id}/map: the session's calls (the session's own under `main`, each agent's under it) and its
    turns, cut to MAX_ANSWER bytes (fit)."""
    made = given(session_dir) if program == "claude" else None
    calls_read = _read(program, made, made is not None and _maps(made, "PostToolUse"))
    found = calls(session_dir) if calls_read == "yes" else []
    known = [a["id"] for a in agents(session_dir, MAX_CALL_AGENTS)] if calls_read == "yes" else []
    by_agent = {a: [] for a in known}
    main = []
    dropped = False
    for c in found:
        call = {k: c[k] for k in ("tool", "target", "failed", "at")}
        if c["agent"] is None:
            main.append(call)
        elif c["agent"] in by_agent or len(by_agent) < MAX_CALL_AGENTS:
            by_agent.setdefault(c["agent"], []).append(call)
        else:
            dropped = True              # an agent's past MAX_CALL_AGENTS: said by `cut`, as anything left out is
    groups, cut = (None, False) if program not in CLAUDE else turns(worktree, claude_dir, conversation)
    answer = {"at": int(now if now is not None else time.time()), "session": session_id, "calls_read": calls_read,
              "main": {"calls": main, "more": 0},
              "agents": [{"id": a, "calls": c, "more": 0} for a, c in by_agent.items()],
              "turns_read": "no" if program not in CLAUDE else "yes" if groups is not None else "missing",
              "turns": [], "cut": cut or dropped}
    return fit(answer, groups or [])
