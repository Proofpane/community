"""A run's tree and its context, read from its transcript and its sub-agents' (transcripts.files), as the run's pane
shows them.

The tree: each turn (a message to the run: its first, an answer, a queued input) with its requests and tokens, and
each tool call with its name, what it names (a file's path, or a command's program, never the rest of the command, an
address, a query or a prompt), how long it took and whether it failed; a sub-agent nested under the call that started
it, with its type, its own calls and tokens. No message's text: the tree is copied into the app. Measured on Claude Code 2.1.274 in
the workspace image (2026-10-03): a sub-agent's file is `<session>/subagents/agent-<id>.jsonl`, beside an
`agent-<id>.meta.json` naming the call that started it (`toolUseId`) and its depth (`spawnDepth`). At a run's end a
compact copy, at most MAX_TREE bytes (names, paths, times and counts, no contents), is kept in the run's folder as
tree.json, since Claude Code removes its transcripts after a month.

The context: the context at a request is its input, cache-read and cache-creation tokens (each request once, by its
message id). The run's record keeps the model's window (its last result's contextWindow), the compaction point (the
agent's own, else Claude Code's: about 967,000 tokens on a model of a million), the peak and its share of that point,
and each compaction: measured on 2.1.274, a `system` line of subtype `compact_boundary` with `compactMetadata`
{trigger, preTokens, durationMs} and no tokens after (taken from the next request), the summary on the next `user`
line, marked `isCompactSummary`. More compactions than the agent's `max_compactions` mark the run fragmented: it
finished on summaries of its own past. Its retention is the share of its first message's identifiers (paths, ids,
addresses, quoted names; at most NEEDLES) its last summary still names, each lost one listed.
"""

import datetime
import io
import json
import os
import re
import shlex

from . import transcripts

MAX_TREE = 64 * 1024
MAX_TEXT = 120
NEEDLES = 50
NATIVE_MILLION = 1_000_000
COMPACTS_AT = 967_000           # Claude Code's own point on a model with a native window of a million
_PATH = re.compile(r"(?<![\w@])(?:[\w.-]+/)+[\w.-]+|\b[\w-]+\.(?:py|ts|tsx|js|json|md|toml|yaml|yml|go|rs|java|sql|sh)\b")
_URL = re.compile(r"https?://[^\s)>\]\"']+")
_ID = re.compile(r"(?<![\w-])(?:#\d{1,7}|[0-9a-f]{7,40})(?![\w-])")
_QUOTED = re.compile(r"[\"`]([^\"`\n]{3,80})[\"`]")


def _when(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


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


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SCAN = 4096                                                      # a command is read for its program only this far
_PROGRAM_PATH = re.compile(r"[A-Za-z0-9_.+/~${}-]+")             # a program's path, $VENV/bin/x included
_PROGRAM_NAME = re.compile(r"[A-Za-z0-9_.+~][A-Za-z0-9_.+~-]*")  # its last part: a plain name, not first a "-"
_UNSAFE = frozenset("`;&|<>()")    # in an assignment: a substitution or another command, whose words shlex splits


def _target(name, given):
    """What a tool call names, without its contents: a file's path, or the program a command runs (its first word
    past any VAR=value, as its last part); never the rest of a command, an address, a pattern, a query or a prompt,
    which can carry a key or a person's words. The command's words are read as a shell reads them (shlex), so a value
    quoted in an assignment (TOKEN="a b" ./run) is never taken for the program; a command whose words a shell may
    split otherwise names nothing (split on spaces, the second half of that value was the target;
    `KEY=abc;mysql -pS3cret` gave `-pS3cret`)."""
    if not isinstance(given, dict):
        return ""
    for key in ("file_path", "path", "notebook_path"):
        value = given.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:MAX_TEXT]
    command = given.get("command")
    if isinstance(command, str):
        return _program(command)[:40]
    return ""


def _program(command):
    """The program a command runs, as activity._program reads it (written out there too, since that script runs alone; a
    test holds the two to the same answers): its first word past any VAR=value, as its last part, read as a shell
    splits words (shlex), lazily and only as far as that word, so a value quoted in an assignment is one word and a
    heredoc or anything after the program is never read; and of that word only what comes before its first space, so
    a quoted first word ("curl -H ..." args) never brings its arguments along. A `#` inside a word is part of it, as
    a shell reads it, never a comment that hides the rest of its line; the comment lines a command begins with, a
    comment after an assignment and a line continued after one are passed over, as a shell passes them over. Whether
    a word is an assignment is read from it as written, so a quoted name (`"A"=b`) is none, as to a shell.

    It names nothing where shlex and a shell are known to split the words differently, since the word it comes to can
    then be an argument, a key included (`KEY=abc;mysql -pS3cret` gave
    `-pS3cret`): when an assignment before it holds a substitution or another command (`$(`, a backtick, `;`, `&`, `|`,
    `<`, `>`, a parenthesis), or a backslash (shlex keeps one only where a quoted part held it, as `$'It\\'s'` does,
    which a shell reads otherwise), or a `${` or `$[` that nothing after it closes (a shell joins what that holds,
    spaces and all); when that word holds such a bracket too, or is not a plain
    program (a path of letters, digits and `_ . + ~ - $ { }`, whose last part is a name of the first five, not first a
    `-`); and when a quote does not close before it. A carriage return is part of a word, as a shell reads it, never a
    space between two.

    Only the command's first _SCAN characters are read: shlex grows a word a character at a time, at a cost of the
    square of its length (a 160 KB first word took 14 s, in the agent's own process). A word that ends what is read,
    when the command goes on past it, names nothing, since the cut may have shortened it."""
    text = _uncommented(command[:_SCAN])
    stream = io.StringIO(text)
    words = shlex.shlex(stream, posix=True)
    # a shell splits words at spaces, tabs and newlines; a carriage return is part of a word to it
    words.whitespace_split, words.commenters, words.whitespace = True, "", " \t\n"
    try:
        while True:
            at = stream.tell()
            word = words.get_token()
            if word is None:
                return ""
            raw = text[at:stream.tell()].lstrip(" \t\n")       # the word as written, and what ended it
            if raw.startswith("\\\n") and not word.strip():
                continue                                    # a line continued, which a shell reads past
            if raw.startswith("#"):
                if "'" in raw or '"' in raw:
                    return ""                               # a comment whose quote shlex read on past its line
                if not raw.endswith("\n"):
                    stream.readline()                       # a comment, to its line's end, as a shell reads one
                continue
            if _ASSIGNMENT.match(raw):
                if _UNSAFE.intersection(word) or "\\" in word or _unclosed(word):
                    return ""
                continue
            first = word.split()
            path = first[0].strip("'\"()") if first else ""
            name = os.path.basename(path)
            plain = _PROGRAM_PATH.fullmatch(path) and _PROGRAM_NAME.fullmatch(name)
            if _unclosed(word) or not plain:
                return ""
            return name if len(command) <= _SCAN or _followed(words) else ""
    except ValueError:
        return ""


def _uncommented(text):
    """`text` past the comment lines it begins with: there a `#` begins a word, and a shell reads the rest of its
    line as a comment (a model sometimes begins a command with one); anywhere else a `#` is read as part of its
    word (_program)."""
    while True:
        text = text.lstrip(" \t\n")
        if not text.startswith("#"):
            return text
        end = text.find("\n")
        if end < 0:
            return ""
        text = text[end + 1:]


def _unclosed(word):
    """Whether `word` (as shlex gives it) holds a `${` or a `$[` that nothing after it closes: a shell joins what such a
    bracket holds into one word, spaces and all, where shlex splits it (`A=${B:-a b}`); a `}` or a `]` before it closes
    nothing (`A=}${B:-a key}` balanced a count over the whole word)."""
    waiting = []
    for at, ch in enumerate(word):
        if ch in "{[" and at and word[at - 1] == "$":
            waiting.append("}" if ch == "{" else "]")
        elif waiting and ch == waiting[-1]:
            waiting.pop()
    return bool(waiting)


def _followed(words):
    """Whether a word follows the one `words` (a shlex) read last; one whose quote does not close in what is read
    counts, since it began after that one ended."""
    try:
        return next(words, None) is not None
    except ValueError:
        return True


def _walk(path):
    """(turns, the conversation's own numbers, its compactions, its first message): one file of the conversation."""
    turns, tools, seen, compactions = [], {}, set(), []
    numbers = {"requests": 0, "peak": 0, "last": 0, "tokens": dict.fromkeys(transcripts.TOKEN_FIELDS, 0)}
    first = None
    current = None
    pending_post = None
    for row in _rows(path):
        kind, at = row.get("type"), _when(row.get("timestamp"))
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if kind == "system" and row.get("subtype") == "compact_boundary":
            meta = row.get("compactMetadata") if isinstance(row.get("compactMetadata"), dict) else {}
            entry = {"at": at, "trigger": str(meta.get("trigger") or ""), "pre_tokens": meta.get("preTokens"),
                     "post_tokens": meta.get("postTokens"), "summary": None}
            compactions.append(entry)
            pending_post = entry if entry["post_tokens"] is None else None
            continue
        if kind == "user":
            content = message.get("content")
            if row.get("isCompactSummary"):
                if compactions:
                    compactions[-1]["summary"] = _text(content)
                continue
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
                if isinstance(content, list) else []
            for b in results:
                tool = tools.get(b.get("tool_use_id"))
                if tool is not None and at is not None and tool.get("_at") is not None:
                    tool["ms"] = int((at - tool["_at"]) * 1000)
                    tool["error"] = b.get("is_error") is True
            text = _text(content)
            if not results and text.strip():
                if first is None:
                    first = text
                current = {"at": at, "text": "", "requests": 0,         # its words stay in the chat, not here
                           "tokens": dict.fromkeys(transcripts.TOKEN_FIELDS, 0), "tools": []}
                turns.append(current)
            continue
        if kind != "assistant":
            continue
        if current is None:
            current = {"at": at, "text": "", "requests": 0, "tokens": dict.fromkeys(transcripts.TOKEN_FIELDS, 0),
                       "tools": []}
            turns.append(current)
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
        key = message.get("id") or row.get("requestId")
        if usage is not None and (key is None or key not in seen):
            if key is not None:
                seen.add(key)
            numbers["requests"] += 1
            current["requests"] += 1
            for f in transcripts.TOKEN_FIELDS:
                value = usage.get(f)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    numbers["tokens"][f] += value
                    current["tokens"][f] += value
            context = sum(usage.get(f) or 0 for f in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens") if isinstance(usage.get(f), int))
            numbers["last"] = context
            numbers["peak"] = max(numbers["peak"], context)
            if pending_post is not None:
                pending_post["post_tokens"] = context
                pending_post = None
        for b in message.get("content") if isinstance(message.get("content"), list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in tools:
                tool = {"id": b.get("id"), "name": str(b.get("name") or "")[:80],
                        "target": _target(b.get("name"), b.get("input")), "ms": None, "error": False, "_at": at}
                tools[b.get("id")] = tool
                current["tools"].append(tool)
    for tool in tools.values():
        tool.pop("_at", None)
    return turns, numbers, compactions, first, tools


def _subagents(files):
    """The sub-agents' files with their meta: [(path, meta)], shallowest first."""
    out = []
    for path, _ in files[1:]:
        meta = {}
        try:
            with open(path[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as fh:
                got = json.load(fh)
            meta = got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            pass
        out.append((path, meta))
    return sorted(out, key=lambda pm: pm[1].get("spawnDepth") if isinstance(pm[1].get("spawnDepth"), int) else 99)


def _listable(needle):
    return bool(_PATH.fullmatch(needle) or _ID.fullmatch(needle)) and not _URL.match(needle)


def needles(first):
    """The identifiers of a run's first message: paths, addresses, ids, quoted names; at most NEEDLES."""
    found = []
    for pattern in (_URL, _PATH, _ID):
        found += pattern.findall(first or "")
    found += _QUOTED.findall(first or "")
    out = []
    for n in found:
        n = n.strip().rstrip(".,;:")
        if len(n) >= 3 and n not in out:
            out.append(n)
    return out[:NEEDLES]


def read(worktree, claude_dir, session, autocompact_window=None, max_compactions=2, model_window=None):
    """{tree, context} of a run's conversation; None when it has no transcript."""
    files = transcripts.files(worktree, claude_dir, session)
    if not files or not files[0][0].endswith(session + ".jsonl"):
        return None
    turns, numbers, compactions, first, tools = _walk(files[0][0])
    nested = []
    for path, meta in _subagents(files):
        sub_turns, sub_numbers, _, _, sub_tools = _walk(path)
        node = {"type": str(meta.get("agentType") or "")[:40], "description": "",
                "depth": meta.get("spawnDepth") if isinstance(meta.get("spawnDepth"), int) else None,
                "requests": sub_numbers["requests"], "tokens": sub_numbers["tokens"],
                "tools": [t for turn in sub_turns for t in turn["tools"]]}
        parent = tools.get(meta.get("toolUseId"))
        if parent is not None:
            parent["agent"] = node
        else:
            nested.append(node)
        tools.update(sub_tools)
    tree = {"turns": turns, "requests": numbers["requests"], "tokens": numbers["tokens"]}
    if nested:
        tree["unplaced"] = nested
    window = model_window if isinstance(model_window, int) and model_window > 0 else None
    point = autocompact_window or (COMPACTS_AT if window and window >= NATIVE_MILLION else window)
    wanted = needles(first)
    last_summary = next((c["summary"] for c in reversed(compactions) if c["summary"]), None)
    kept = [n for n in wanted if last_summary is not None and n in last_summary]
    context = {"model_window": window, "compaction_window": point, "peak_tokens": numbers["peak"],
               "peak_pct": round(numbers["peak"] / point, 4) if point else None, "last_tokens": numbers["last"],
               "compactions": len(compactions),
               "compaction_log": [{k: c[k] for k in ("at", "trigger", "pre_tokens", "post_tokens")}
                                  for c in compactions][-20:],
               "fragmented": len(compactions) > max_compactions,
               "retention": round(len(kept) / len(wanted), 4) if wanted and last_summary is not None else None,
               # the lost ones named when a path or an id: an address or a quoted name can carry a key or words
               "lost": [n for n in wanted if n not in kept and _listable(n)][:NEEDLES] if last_summary is not None
               else []}
    return {"tree": compact(tree), "context": context}


def compact(tree, most=MAX_TREE):
    """The tree within `most` bytes as JSON: a turn's calls cut to fewer, the rest counted, until it fits."""
    cap = 400
    while True:
        out = json.loads(json.dumps(tree))
        for turn in out["turns"]:
            _cut(turn, cap)
        if len(json.dumps(out)) <= most or cap <= 1:
            return out
        cap //= 2


def _cut(holder, cap):
    tools = holder.get("tools") or []
    if len(tools) > cap:
        holder["more"] = len(tools) - cap
        holder["tools"] = tools[:cap]
    for tool in holder["tools"]:
        if isinstance(tool.get("agent"), dict):
            _cut(tool["agent"], max(1, cap // 2))
