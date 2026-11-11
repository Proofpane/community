"""A run's numbers at its end, each from a source a reader can check: its transcript
and its sub-agents' files (each request counted once, by its message id; each tool call paired with its result by its
id, with the two lines' times), its chat log (chat.jsonl: each turn's result with its duration_ms, its duration_api_ms
and the calls it refused; the owner's permission asks and their answers), and its record (its times, the runs it
started, what was sent to it, its questions). Measured on Claude Code 2.1.274 in the workspace image (2026-10-03): a
call refused by the permission mode (dontAsk), by a PreToolUse hook that denies (as the workspace's gate does) or by the
SDK's can_use_tool is in that turn's result's permission_denials, by its tool_use_id, and its result is an error; so a
refused call is counted as refused, not as an error.

Durations are also kept as counts on fixed buckets, OpenTelemetry's default boundaries (a run's in seconds, a tool
call's in milliseconds), so the app can add days together and read a percentile from the sum: a day's own percentiles
cannot be combined.

The retrieval numbers are a proxy, and named so: the sources a run surfaced are the files its Read calls read and the
paths its Grep and Glob calls listed, in the order each first appeared (an order of appearance, not a retriever's
ranking); a call of an MCP server's search tool is counted, its results' ids are not read."""

import datetime
import json
import os
import re

from . import claims, gate, transcripts
from .shapes import BUCKETS, MAX_SOURCE, MAX_SOURCES, TOGETHER, add_counts, bucket_counts, exact, percentile  # noqa: F401

MAX_TOOLS = 100
MAX_ANSWER = 2000
GRANTS = ("allow", "allow_session")      # an answer that let the call run, once or for the session
MAX_RESULTS = 2 * 1024 * 1024           # of a run's tool results' text, what its claims are checked against
MAX_PAYLOAD = 4096                      # of a hook's delivery, what the premise watch reads
MAX_CHAT_LOG = 32 * 1024 * 1024
RETRIEVAL = ("Read", "Grep", "Glob")
_PATH_LINE = re.compile(r"^(?:\.{0,2}/)?[\w.@+-]+(?:/[\w.@+-]+)*$")
_GREP_LINE = re.compile(r"^(.+?):\d+[:-]")


def union_seconds(intervals, holes=()):
    """The time the intervals cover, each moment once, less what the holes cover of it: seconds."""
    merged = []
    for start, end in sorted((a, b) for a, b in intervals if a is not None and b is not None and b > a):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    holes = sorted((a, b) for a, b in holes if a is not None and b is not None and b > a)
    total = 0.0
    for a, b in merged:
        total += b - a
        for h_start, h_end in holes:
            total -= max(0.0, min(b, h_end) - max(a, h_start))
    return round(max(0.0, total), 3)


def _when(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _rows(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _relative(path, worktree):
    """A source as a suite names it: under the worktree, relative to it; else as it was given."""
    if not isinstance(path, str) or not path.strip():
        return None
    path = os.path.normpath(path.strip())
    if os.path.isabs(path):
        for base in {os.path.realpath(worktree), os.path.normpath(worktree)}:
            if path.startswith(base + os.sep):
                return path[len(base) + 1:][:MAX_SOURCE]
    return path[:MAX_SOURCE]


def _listed(text):
    """The paths a Grep or Glob result lists: each line that is a path, or a match line's path."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        m = _GREP_LINE.match(line)
        candidate = m.group(1) if m else line
        if candidate and _PATH_LINE.match(candidate) and ("/" in candidate or "." in candidate):
            out.append(candidate)
    return out


def _read_transcripts(worktree, claude_dir, session):
    """(each call, each request's usage, the sources in the order they first appeared, the main conversation's last
    words) from the run's transcript and its sub-agents' files."""
    calls, requests, appeared, last_words, said = {}, {}, [], "", []
    for index, (path, _) in enumerate(transcripts.files(worktree, claude_dir, session)):
        for number, row in enumerate(_rows(path)):
            kind, at = row.get("type"), _when(row.get("timestamp"))
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            content = message.get("content")
            if kind == "assistant":
                usage = message.get("usage")
                key = message.get("id") or row.get("requestId") or (path, number)
                if isinstance(usage, dict):
                    requests.setdefault(key, usage)         # a reply written on several lines is one request
                for b in content if isinstance(content, list) else []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and isinstance(b.get("id"), str) and b["id"] not in calls:
                        given = b.get("input") if isinstance(b.get("input"), dict) else {}
                        calls[b["id"]] = {"id": b["id"], "name": str(b.get("name") or "")[:80], "start": at,
                                          "end": None, "error": False, "path": given.get("file_path"),
                                          "given": given}
                    elif b.get("type") == "text" and index == 0 and str(b.get("text") or "").strip():
                        last_words = b["text"]
            elif kind == "user" and isinstance(content, list):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    call = calls.get(b.get("tool_use_id"))
                    if call is None or call["end"] is not None:
                        continue
                    call["end"], call["error"] = at, b.get("is_error") is True
                    if call["error"]:
                        continue
                    if sum(len(x) for x in said) < MAX_RESULTS:
                        said.append(_text_of(b.get("content")))
                    found = [call["path"]] if call["name"] == "Read" else \
                        _listed(_text_of(b.get("content"))) if call["name"] in ("Grep", "Glob") else []
                    for source in found:
                        source = _relative(source, worktree)
                        if source:
                            appeared.append((at if at is not None else 0.0, len(appeared), source))
    sources = []
    for _, _, source in sorted(appeared):
        if source not in sources and len(sources) < MAX_SOURCES:
            sources.append(source)
    return list(calls.values()), list(requests.values()), sources, last_words, "\n".join(said)


def _chat_log(path):
    """(each turn's result event, the owner's permission asks as {at, answered_at, decision}, the run's first message)
    from the chat log."""
    results, asks, first = [], {}, None
    try:
        if os.path.getsize(path) > MAX_CHAT_LOG:
            return results, [], None
    except OSError:
        return results, [], None
    for ev in _rows(path):
        kind = ev.get("kind")
        if kind == "user" and first is None:
            first = ev.get("text") if isinstance(ev.get("text"), str) else ""
        elif kind == "result":
            results.append(ev)
        elif kind == "ask" and isinstance(ev.get("id"), str):
            asks[ev["id"]] = {"at": ev.get("time"), "answered_at": None, "decision": None}
        elif kind == "answered" and ev.get("id") in asks:
            asks[ev["id"]].update(answered_at=ev.get("time"), decision=ev.get("decision"))
    return results, list(asks.values()), first


def _seconds(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else 0


def read(worktree, claude_dir, session, chat_path, run, payload=None, first=None):
    """The run's numbers, from its sources (above) and its record's `run` part (with its ended_at); `payload`, a
    hook's delivery (its bytes, as the workspace checked them), which the premise watch reads with the run's first
    message: `first`, as the workspace kept it, else its chat log's."""
    calls, requests, sources, last_words, said = _read_transcripts(worktree, claude_dir, session) if session \
        else ([], [], [], "", "")
    results, asks, logged = _chat_log(chat_path)
    first = first if isinstance(first, str) else logged
    refused = {d.get("id") for r in results for d in (r.get("denials") or []) if isinstance(d, dict)}
    answered = [a for a in asks if a["answered_at"] is not None]
    holes = [(a["at"], a["answered_at"]) for a in answered]
    started, ended, queued = run.get("started_at"), run.get("ended_at"), run.get("queued_at")
    whole = lambda a, b: b - a if isinstance(a, int) and isinstance(b, int) and b >= a else None  # noqa: E731
    time = {"wall": whole(started, ended), "queue_wait": whole(queued, started),
            "active": round(sum(_seconds(r.get("duration_ms")) for r in results) / 1000, 3),
            "model": round(sum(_seconds(r.get("api_ms")) for r in results) / 1000, 3),
            "tool": union_seconds([(c["start"], c["end"]) for c in calls], holes),
            "waiting_owner": _seconds(run.get("waiting_s"))}

    tools, servers, durations = {}, {}, {}
    for call in calls:
        name = call["name"] or "(unnamed)"
        if name not in tools and len(tools) >= MAX_TOOLS:
            name = "(more)"
        t = tools.setdefault(name, {"calls": 0, "errors": 0, "refused": 0})
        t["calls"] += 1
        if call["id"] in refused:
            t["refused"] += 1
        elif call["error"]:
            t["errors"] += 1
        if call["start"] is not None and call["end"] is not None and call["id"] not in refused:
            durations.setdefault(name, []).append(max(0.0, (call["end"] - call["start"]) * 1000))
        if name.startswith("mcp__") and name.count("__") >= 2:
            s = servers.setdefault(name.split("__")[1][:80], {"calls": 0, "errors": 0, "refused": 0})
            s["calls"] += 1
            if call["id"] in refused:
                s["refused"] += 1
            elif call["error"]:
                s["errors"] += 1
    for name, t in tools.items():
        ms = durations.get(name, [])
        t.update(p50_ms=exact(ms, 0.5), p95_ms=exact(ms, 0.95), hist=bucket_counts(ms))

    read_tokens = sum(_seconds(u.get("cache_read_input_tokens")) for u in requests)
    all_input = sum(_seconds(u.get(k)) for u in requests
                    for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    retrieval = sum(1 for c in calls if c["name"] in RETRIEVAL or (c["name"].startswith("mcp__") and "search" in
                                                                     c["name"].split("__")[-1].lower()))
    cited = [s for s in sources if s in last_words or (len(os.path.basename(s)) >= 4 and os.path.basename(s) in
                                                       last_words)]
    trigger = run.get("trigger") if isinstance(run.get("trigger"), dict) else {}
    return {
        "time": time,
        "wall_hist": bucket_counts([time["wall"]]) if time["wall"] is not None else bucket_counts([]),
        "calls": sum(t["calls"] for t in tools.values()), "errors": sum(t["errors"] for t in tools.values()),
        "refused": sum(t["refused"] for t in tools.values()), "tools": tools, "servers": servers,
        "together": {
            "dispatches_sent": _seconds(run.get("dispatches")),
            "dispatches_received": 1 if trigger.get("kind") == "dispatch" else 0,
            "messages_sent": _seconds(run.get("messages_sent")),
            "messages_received": _seconds(run.get("messages_received")),
            "questions": _seconds(run.get("questions")), "answered": _seconds(run.get("answered")),
            "permission_asks": len(asks), "granted": sum(1 for a in answered if a["decision"] in GRANTS),
            "denied": sum(1 for a in answered if a["decision"] not in GRANTS),
            "owner_turns": _seconds(run.get("owner_turns"))},
        "cache": {"read": read_tokens, "input": all_input, "requests": len(requests),
                  "ratio": round(read_tokens / all_input, 4) if all_input else None,
                  "cold_requests": sum(1 for u in requests if not _seconds(u.get("cache_read_input_tokens")))},
        "retrieval": {"calls": retrieval, "surfaced": len(sources), "cited": len(cited), "sources": sources},
        # an eval's run: its last reply, which a stale question is scored on (the app's suites.py)
        "answer": last_words[:MAX_ANSWER] if trigger.get("kind") == "eval" else None,
        # its final message's unbacked claims, its first message's premise, whether it can be replayed (no call of a
        # kind the replay gate refuses), how unattended it was, its servers' health, its fan-out
        "claims": _claims(last_words, said),
        "premise": _premise(first, payload),
        "replayable": bool(calls) and not any(gate.acts_on_the_world(c["name"], c["given"]) for c in calls),
        # an intervention: a permission ask, a question for the owner (its answer is part of it), or a turn of the
        # owner's that answered nothing
        "autonomy": {"unattended": not (asks or run.get("questions") or run.get("owner_turns")),
                     "interventions": len(asks) + _seconds(run.get("questions")) +
                     max(0, _seconds(run.get("owner_turns")) - _seconds(run.get("answered")))},
        "mcp_health": _mcp_health(run),
        "dispatch": {"fan_out": _seconds(run.get("dispatches")), "waited_s": _seconds(run.get("waited_s"))},
    }


def _claims(last_words, said):
    got = claims.unbacked(last_words, said)
    return {"flagged": got["flagged"], "standing": [{"word": s["word"], "text": s["text"]} for s in got["standing"]][:5]}


def _premise(first, payload):
    text = (first or "") + ("\n" + payload[:MAX_PAYLOAD].decode("utf-8", "replace") if payload else "")
    got = claims.premise(text)
    return got if got["flagged"] else {"flagged": False}


def _mcp_health(run):
    """Its MCP servers as claude reported them at its start: those needing a sign-in, failed or turned off, and the
    ones whose tools changed since the owner accepted them."""
    servers = run.get("mcp") if isinstance(run.get("mcp"), list) else []
    out = {"needs_auth": [], "failed": [], "disabled": []}
    for s in servers:
        status = s.get("status") if isinstance(s, dict) else None
        key = {"needs-auth": "needs_auth", "failed": "failed", "disabled": "disabled"}.get(status)
        if key and isinstance(s.get("name"), str):
            out[key].append(s["name"][:80])
    out["tool_changes"] = len(run.get("mcp_changed") or []) if isinstance(run.get("mcp_changed"), list) else 0
    return out
