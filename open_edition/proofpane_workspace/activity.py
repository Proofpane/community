"""What a session's `claude`, `gemini`, `codex`, `grok` or `qwen` is doing, from its own hooks, for the page's session
list: working (a prompt went in, or a tool finished), waiting (a Notification: it needs a permission, or it has
waited for input; or Codex's PermissionRequest, from whose call the note is made), idle (its turn ended: Claude
Code's, Codex's, Grok Build's and Qwen Code's Stop, Gemini CLI's AfterAgent). Each event writes the session's
activity.json, which the agent reads with the session (workspace).

Beside the note (`message`, the program's words or the command it would run, for the list on the page), it keeps the
tool the hook named (`tool`) and the kind of notification (`kind`), names only: a notification on a phone's lock screen
or a shared screen says those, never the note, which can carry a secret the agent put in a command.

    python activity.py working|waiting|idle [--gemini]    run as a hook, the hook's JSON on stdin
    python activity.py --map                              the control window's map's hook (below)
    python activity.py --limit                            a controller's session's limit hook (below)
    python activity.py --skills                           the skills hook (below)

With --map (given to a claude terminal at each start by the version that start read,
settings.map_settings), it keeps what the map shows of a session's tool calls and sub-agents, in the session's own
folder, and nothing of its state. PostToolUse and PostToolUseFailure (a call that failed: from 2.1.119, in PostToolUse's
place) append a line to calls.jsonl: the time, the agent the call was made in (its `agent_id`, from 2.1.69; none for the
session's own), the tool's name, what the call names (target: a file's path, or a command's program, never the rest of
the command or any other argument, and nothing where the reader cannot tell which word is the program, as
runtree._target keeps it) and whether it failed. SubagentStart and SubagentStop append one to subagents.jsonl: the time,
start or stop, the agent's id and its type, never `last_assistant_message` (hooks page, read 2026-10-17). Each file is
bounded: past MAX_CALLS or MAX_AGENT_EVENTS bytes it is cut to its newest half, so a long session's calls keep the
newest, and hooks running at once may lose a line to a cut (display only). A link where a file goes is not followed.
SubagentStop also fires for Claude Code's own agents (prompt suggestions, /btw), which never started as sub-agents: the
map (sessionmap.py) leaves out a stop no start or status line came for.

With --limit (given only to a session the owner's controller started,
settings.controller_settings), it appends a line to limits.jsonl in the session's folder: the time, and the kind, the
StopFailure's `error` (rate_limit: a turn stopped at the account's limit) or the Notification's type
(quota_auto_resume_fired, _stale, _disabled: Claude Code's own wait at the limit ended, by going on, by waiting for
Enter, or without going on); nothing else of the event, which carries the error's text. The file is bounded as the
map's are. The controller reads it to mark the session waiting for its account's reset, and to know Claude Code went
on with it by itself, so that it never tells it to go on a second time.

With --skills (given to a claude terminal at each start, and to each of the owner's
chats once its start has read the claude's version, settings.skills_settings), it counts the skills a session calls: each
`Skill` call that reached PostToolUse or PostToolUseFailure (a call Claude chose), and each UserPromptExpansion of a
typed command that is a skill the session has (one the person typed), appends one line to skills.jsonl in the session's
folder: an id the hook makes, the time, the skill's name (as the model wrote it, the way the Skill tool takes it,
trimmed with one leading `/` off: `tool_response.commandName` where the call's result gives it, which Claude Code
2.1.304 fills with the model's own text and not the name it resolved, else the call's `skill` the same way; the typed
command's `command_name`; kept only where it fits NAME), its kind (`core`: the proofpane-core plugin's own; `business`:
a folder of the session's worktree's .claude/skills/ that neither the organisation's managed skills nor the user's own
hold under that name, since Claude Code runs those first; `other`), which path (`claude` or `typed`), whether the call
failed (PostToolUseFailure, an interruption included: `is_interrupt` is not read; null for a typed one, whose outcome no
hook gives) and the version it used, read as the hook runs: for a business skill the commit that last changed its folder
(`git log -1`), `uncommitted` where the folder has changes not committed (never given a commit), `unknown` where git did
not answer in GIT_TIMEOUT seconds or failed; for a core skill the plugin's own release and a sha256 of the skill's
folder (`<release>+<digest>`, `+cut` where the folder is past DIGEST_MOST bytes, `+unknown` where it could not be read);
none for any other. Never a call's arguments. A typed command that is not a skill the session has (a custom command, a
bundled skill, one of another plugin) is not counted: a typed expansion does not say which it was. A name that does not
fit NAME (a nested skill's path, say) is not counted,
and a core skill the model names without its `proofpane-core:` prefix is counted as the core skill, under its full name
(Claude Code 2.1.304 gives a plugin skill whose frontmatter name has no colon that name as an alias, and runs a call by
it unless a skill of that exact name comes first, a project's, the account's or the organisation's, which is then the
one counted; a name two plugins answer to is refused, and no hook sees that). A call that failed is counted
by the same rule as one that ran: in 2.1.304 a call whose name no skill has is refused before any hook runs, so a
failure names a skill that exists (read in that build's code; no older build was read). The file is bounded as the map's
are. The workspace takes each session's new lines into a record of its own, which the gate refuses to a session's tool
calls (skillcalls.py), and serves them (GET /skills).

It is display, not governance: the session runs as the user that can also write this file, so what it says is the
session's own word about itself, and the page shows it as that. A hook here never blocks or changes anything: it
prints nothing for Claude Code and an empty object for Gemini CLI, which reads its hooks' output as JSON, and it ends
0 whatever happens, so a broken file or a missing variable cannot stop a turn.
"""

import io
import itertools
import json
import os
import re
import shlex
import stat
import sys
import time

# hashlib, secrets and subprocess are imported where the skills hook uses them, not here: every hook of the workspace runs
# this script on a tool call's path, and importing the three costs about 13 ms of each run (measured, 40 runs)

STATES = ("working", "waiting", "idle")
MESSAGE = 200
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")      # a tool's or a notification kind's name, no more
CALLS = "calls.jsonl"
AGENT_EVENTS = "subagents.jsonl"
MAX_CALLS = 512 * 1024
MAX_AGENT_EVENTS = 128 * 1024
LIMITS = "limits.jsonl"
MAX_LIMITS = 16 * 1024
LIMIT_KINDS = ("rate_limit", "quota_auto_resume_fired", "quota_auto_resume_stale", "quota_auto_resume_disabled")
MAX_MAP_EVENT = 16 * 1024 * 1024     # a hook's input read for --map: a tool's response is in it
AGENT_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.:@-]{0,127}")
TARGET = 120            # a path, at most (runtree.MAX_TEXT)
PROGRAM = 40            # a command's program, at most
SCAN = 4096             # a command is read for its program only this far
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PATH = re.compile(r"[A-Za-z0-9_.+/~${}-]+")              # a program's path, $VENV/bin/x included
_NAME = re.compile(r"[A-Za-z0-9_.+~][A-Za-z0-9_.+~-]*")   # its last part: a plain name, not first a "-"
_UNSAFE = frozenset("`;&|<>()")    # in an assignment: a substitution or another command, whose words shlex splits
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# the skills hook (below)
SKILLS = "skills.jsonl"
MAX_SKILLS = 512 * 1024
CORE = "proofpane-core"         # the core plugin's name (core_plugin.NAME): its skills are called <it>:<skill>
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.normpath(os.path.join(HERE, "..", "core_plugin"))     # core_plugin.PLUGIN, written out as target() is
GIT_TIMEOUT = 2                 # seconds each git read of a skill folder's version may take
FRONT = 4096                    # a SKILL.md is read this far for its frontmatter `name`
FOLDERS = 300                   # a skills directory's folders read for a frontmatter `name`, at most
ENTRIES = 2000                  # ...taken, by name, from the first this many entries the directory lists
DIGEST_MOST = 256 * 1024        # a core skill's folder is read this far for its digest (more: `+cut`)
DIGEST_FILES = 200              # ...and this many files
# git's settings for a read the workspace itself makes (workspace._RUN_PAIRS and the pair its `_git_env` adds for a
# command that reaches no remote), given as `-c`, since this script runs alone: no hook, no fsmonitor, no signing, no
# transport. A test holds the two to the same pairs.
_GIT_PAIRS = (("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"), ("commit.gpgSign", "false"),
              ("push.gpgSign", "false"), ("log.showSignature", "false"), ("protocol.allow", "never"))
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_RELEASE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,31}")
_SKILL_FILE = "SKILL.md"
# a frontmatter line `name: value`. The caller cuts the blanks off the value: a lazy `(.*?)[ \t]*$` backtracks
# quadratically on a long run of them (a 4 KB file of blanks took 40 ms, 300 such files 12 s)
_NAME_LINE = re.compile(r"name[ \t]*:[ \t]*(.*)$")


def _session_dir(event):
    """The workspace's PP_SESSION_DIR, or, when a hook's environment was redacted, the session whose worktree the
    event came from (<session>/work, as gate.py finds it)."""
    folder = os.environ.get("PP_SESSION_DIR", "")
    if folder:
        return folder
    cwd = event.get("cwd") if isinstance(event, dict) else None
    if isinstance(cwd, str) and os.path.basename(os.path.normpath(cwd)) == "work":
        parent = os.path.dirname(os.path.normpath(cwd))
        if os.path.isfile(os.path.join(parent, "session.json")):
            return parent
    return ""


def _asking(event):
    """What a PermissionRequest asks, in words: the command it would run, or the tool and why."""
    tool = event.get("tool_name") if isinstance(event.get("tool_name"), str) else None
    given = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    command = given.get("command")
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    if isinstance(command, str) and command.strip():
        return f"Wants to run: {command}"
    why = given.get("description")
    if tool:
        return f"Wants to use {tool}" + (f": {why}" if isinstance(why, str) and why.strip() else "")
    return None


def record(state, event, directory=None):
    directory = directory if directory is not None else _session_dir(event)
    if state not in STATES or not directory or not os.path.isdir(directory):
        return False
    # a subagent's turn is not the session's (Grok Build marks its events `subagentType`): its end does not end the
    # session's turn, nor does its tool, finishing after the session's turn ended, start one; a wait is still the
    # session's, whoever asks
    if state != "waiting" and isinstance(event, dict) and (event.get("subagentType") or event.get("subagent_type")):
        return False
    out = {"state": state, "at": int(time.time())}
    if isinstance(event, dict):
        # the conversation the program speaks from, so that started again it picks up this one (workspace.launch)
        own = event.get("session_id")
        if isinstance(own, str) and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_-]{0,79}", own):
            out["program_session"] = own
        name = event.get("hook_event_name")
        if isinstance(name, str):
            out["event"] = name[:40]
        # names only, for notifications: Grok Build gives them in camelCase
        tool = event.get("tool_name") if "tool_name" in event else event.get("toolName")
        if isinstance(tool, str) and NAME.fullmatch(tool):
            out["tool"] = tool
        kind = event.get("notification_type") if "notification_type" in event else event.get("notificationType")
        if isinstance(kind, str) and NAME.fullmatch(kind):
            out["kind"] = kind
        message = event.get("message")
        if not (isinstance(message, str) and message.strip()) and name == "PermissionRequest":
            message = _asking(event)
        if isinstance(message, str) and message.strip():
            out["message"] = " ".join(message.split())[:MESSAGE]
    path = os.path.join(directory, "activity.json")
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    os.replace(tmp, path)
    return True


def target(name, given):
    """What a tool call names, without its contents, as runtree._target keeps it (that module is the package's and
    this script runs alone, so it is written out here, and a test holds the two to the same answers): a file's path,
    or the program a command runs (its first word past any VAR=value, as its last part); never the rest of a command,
    an address, a pattern, a query or a prompt."""
    if not isinstance(given, dict):
        return ""
    for key in ("file_path", "path", "notebook_path"):
        value = given.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:TARGET]
    command = given.get("command")
    if isinstance(command, str):
        return _program(command)[:PROGRAM]
    return ""


def _program(command):
    """The program a command runs: its first word past any VAR=value, as its last part, read as a shell splits words
    (shlex), lazily and only as far as that word, so a value quoted in an assignment is one word and a heredoc or
    anything after the program is never read; and of that word only what comes before its first space, so a quoted
    first word ("curl -H ..." args) never brings its arguments along. A `#` inside a word is part of it, as a shell
    reads it, never a comment that hides the rest of its line; the comment lines a command begins with, a comment
    after an assignment and a line continued after one are passed over, as a shell passes them over. Whether a word is
    an assignment is read from it as written, so a quoted name (`"A"=b`) is none, as to a shell.

    It names nothing where shlex and a shell are known to split the words differently, since the word it comes to can
    then be an argument, a key included (`KEY=abc;mysql -pS3cret` gave
    `-pS3cret`): when an assignment before it holds a substitution or another command (`$(`, a backtick, `;`, `&`, `|`,
    `<`, `>`, a parenthesis), or a backslash (shlex keeps one only where a quoted part held it, as `$'It\\'s'` does,
    which a shell reads otherwise), or a `${` or `$[` that nothing after it closes (a shell joins what that holds,
    spaces and all); when that word holds such a bracket too, or is not a plain
    program (a path of letters, digits and `_ . + ~ - $ { }`, whose last part is a name of the first five, not first a
    `-`); and when a quote does not close before it. A carriage return is part of a word, as a shell reads it, never a
    space between two.

    Only the command's first SCAN characters are read: shlex grows a word a character at a time, at a cost of the
    square of its length (a 160 KB first word took 14 s). A word that ends what is read, when the command goes on past
    it, names nothing, since the cut may have shortened it."""
    text = _uncommented(command[:SCAN])
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
            if _unclosed(word) or not (_PATH.fullmatch(path) and _NAME.fullmatch(name)):
                return ""
            return name if len(command) <= SCAN or _followed(words) else ""
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


def _trim(path, keep):
    """Cut a file of lines to its last `keep` bytes, a whole line first: read and written as the append writes (no
    link followed, no FIFO waited on), through a file of this process's own, which a trim that fails takes away; and
    one another hook left when it was ended mid-trim is taken away too, once a minute old. (Claude Code ends a
    synchronous hook at its timeout, and kills the background ones still running when a -p session ends; it does not
    time out a background hook in an interactive one, so a hook here is bounded only by its own reads: at most
    MAX_MAP_EVENT of input, SCAN of a command, and the two files' bounds.)"""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return
        size = os.fstat(fh.fileno()).st_size
        fh.seek(max(0, size - keep))
        tail = fh.read(keep)
    cut = tail.find(b"\n")
    tail = tail[cut + 1:] if cut >= 0 else b""
    folder, name = os.path.split(path)
    for left in os.listdir(folder):
        if left.startswith(name + ".") and left.endswith(".tmp"):
            try:
                if time.time() - os.lstat(os.path.join(folder, left)).st_mtime > 60:
                    os.unlink(os.path.join(folder, left))
            except OSError:
                pass
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(tail)
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):        # a trim that failed leaves nothing of its own behind
            os.unlink(tmp)


def _append(directory, name, entry, most):
    """One line on a file of the session's folder, the file cut to its newest half once it passes `most` bytes."""
    path = os.path.join(directory, name)
    line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    # no link followed, and no FIFO waited on (one with no reader refuses the open): a session's programs can put
    # either where the file goes; only a regular file is written
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return
        os.write(fd, line)
        size = os.fstat(fd).st_size
    finally:
        os.close(fd)
    if size > most:
        _trim(path, most // 2)


def map_record(event, directory=None):
    """Keep what the map shows of one hook event (the module's docstring, --map): a tool call, or a sub-agent's start
    or end. True when a line was written."""
    if not isinstance(event, dict):
        return False
    directory = directory if directory is not None else _session_dir(event)
    if not directory or not os.path.isdir(directory):
        return False
    name = event.get("hook_event_name")
    agent = event.get("agent_id")
    if agent is not None and not (isinstance(agent, str) and AGENT_ID.fullmatch(agent)):
        return False            # an agent's that is not an id: not filed as the session's own either
    at = int(time.time())
    if name in ("PostToolUse", "PostToolUseFailure"):
        tool = event.get("tool_name")
        if not isinstance(tool, str) or not NAME.fullmatch(tool):
            return False
        call = {"at": at, "agent": agent, "tool": tool, "target": target(tool, event.get("tool_input")),
                "failed": name == "PostToolUseFailure"}
        _append(directory, CALLS, call, MAX_CALLS)
        return True
    if name in ("SubagentStart", "SubagentStop") and agent is not None:
        kind = event.get("agent_type")
        if isinstance(kind, str):
            kind = " ".join(_CONTROL.sub(" ", kind).split())[:80] or None
        else:
            kind = None
        _append(directory, AGENT_EVENTS, {"at": at, "event": "start" if name == "SubagentStart" else "stop",
                                          "id": agent, "type": kind}, MAX_AGENT_EVENTS)
        return True
    return False


def limit_record(event, directory=None):
    """Keep one limit hook event (the module's docstring, --limit): a StopFailure's error or a Notification's type,
    when it is one of LIMIT_KINDS. True when a line was written."""
    if not isinstance(event, dict):
        return False
    directory = directory if directory is not None else _session_dir(event)
    if not directory or not os.path.isdir(directory):
        return False
    name = event.get("hook_event_name")
    kind = event.get("error") if name == "StopFailure" else event.get("notification_type") \
        if name == "Notification" else None
    if kind not in LIMIT_KINDS:
        return False
    _append(directory, LIMITS, {"at": int(time.time()), "kind": kind}, MAX_LIMITS)
    return True


# the skills hook


def _regular(path):
    """Whether `path` names a regular file (a link followed: a skill's folder can be one), asked without opening it, so a
    FIFO a session's programs put there never holds the hook."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


def _read_file(path, most):
    """The first `most` bytes of a regular file, or None: not one (a FIFO is never waited on) or unreadable."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, left = [], most
        while left > 0:
            got = os.read(fd, min(left, 1 << 16))
            if not got:
                break
            chunks.append(got)
            left -= len(got)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _front_name(path):
    """The `name` a SKILL.md's frontmatter gives its skill (a line `name: x` between its opening `---` and the next),
    read from its first FRONT bytes, or None. A line only at the start of a line is a key; a quoted value is unquoted."""
    data = _read_file(path, FRONT)
    if data is None:
        return None
    lines = data.decode("utf-8", "replace").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        found = _NAME_LINE.match(line)
        if found:
            value = found.group(1).rstrip(" \t")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return None


def _holds(skills, name):
    """The folder of `skills` (a directory of skill folders) whose skill `name` names, or None: a folder of that name
    that holds a SKILL.md, else a folder whose SKILL.md's frontmatter `name` is it (the first FOLDERS by name, of the
    first ENTRIES the directory lists, are looked at): Claude Code calls a skill by either. What this reads is bounded
    by construction: at most FOLDERS files of FRONT bytes, and a pattern that takes linear time."""
    if _regular(os.path.join(skills, name, _SKILL_FILE)):
        return name
    try:
        with os.scandir(skills) as entries:
            folders = sorted(entry.name for entry in itertools.islice(entries, ENTRIES))[:FOLDERS]
    except OSError:
        return None
    for folder in folders:
        if _front_name(os.path.join(skills, folder, _SKILL_FILE)) == name:
            return folder
    return None


def _shadows():
    """The directories of skills Claude Code runs before a project's of the same name ("Enterprise over personal, and
    personal over project", skills page): the managed settings directory's `.claude/skills` (macOS, Linux and Windows
    each have their own, managed settings page) and the user's own, the account's folder (CLAUDE_CONFIG_DIR) or ~/.claude."""
    managed = {"darwin": "/Library/Application Support/ClaudeCode",
               "win32": "C:\\Program Files\\ClaudeCode"}.get(sys.platform, "/etc/claude-code")
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return [os.path.join(managed, ".claude", "skills"), os.path.join(config, "skills")]


def _skill_of(name, work, plugin, shadows):
    """(kind, folder) of the skill `name` a session called: ("core", its folder's name in the plugin's skills) for a
    `proofpane-core:` name the plugin holds; ("business", its folder's name) where the session's worktree `work` holds
    it under .claude/skills/ and none of `shadows` holds that name (Claude Code runs those first, so the call was not
    the project's); ("core", its folder) also for a name with no colon that the plugin holds and neither the worktree
    nor `shadows` hold: Claude Code gives a plugin skill whose frontmatter name has no colon that name as an alias (both
    of the plugin's skills have one) and resolves a call by it, after a skill of that exact name (the cases before);
    ("other", None) for any other."""
    if name.startswith(CORE + ":"):
        rest = name[len(CORE) + 1:]
        folder = _holds(os.path.join(plugin, "skills"), rest) if NAME.fullmatch(rest) else None
        return ("core", folder) if folder is not None else ("other", None)
    folder = _holds(os.path.join(work, ".claude", "skills"), name) if work else None
    if folder is not None:
        return ("other", None) if any(_holds(d, name) is not None for d in shadows) else ("business", folder)
    if ":" not in name:
        bare = _holds(os.path.join(plugin, "skills"), name)
        if bare is not None and NAME.fullmatch(f"{CORE}:{bare}") \
                and not any(_holds(d, name) is not None for d in shadows):
            return "core", bare
    return "other", None


def _git(work, *args):
    """git's output (text) for a read in `work`, or None: it failed, or did not answer in GIT_TIMEOUT seconds. Run with
    the pairs the workspace's own reads run with (_GIT_PAIRS, as -c), no optional locks (so a commit the session makes
    at that moment does not meet this read's index lock), literal pathspecs, no prompt, no lazy fetch, a ceiling at the
    worktree's own folder, and the C locale, as workspace._git_env sets them. Not its other guards: the session's own
    environment and its repository's own settings still reach this git, as they reach every program the session runs,
    which the line's use, display, allows."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C", GIT_NO_LAZY_FETCH="1",
               GIT_CEILING_DIRECTORIES=os.path.dirname(os.path.realpath(work)))
    flags = [part for key, value in _GIT_PAIRS for part in ("-c", f"{key}={value}")]
    import subprocess
    try:
        done = subprocess.run(["git", *flags, "--no-optional-locks", "--literal-pathspecs", *args], cwd=work, env=env,
                              capture_output=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None


def _business_version(work, folder):
    """The version a business skill's call used: the commit that last changed its folder, `uncommitted` while the folder
    has changes not committed (a modified or new file in it: never given a commit), `unknown` where git failed or did not
    answer in time, or has no commit for the folder."""
    where = f".claude/skills/{folder}"
    changed = _git(work, "status", "--porcelain", "--", where)
    if changed is None:
        return "unknown"
    if changed.strip():
        return "uncommitted"
    commit = (_git(work, "log", "-1", "--format=%H", "--", where) or "").strip()
    return commit if _COMMIT.fullmatch(commit) else "unknown"


def _release(plugin):
    """The core plugin's `version`, from its manifest, or `unknown`."""
    data = _read_file(os.path.join(plugin, ".claude-plugin", "plugin.json"), 16 * 1024)
    try:
        value = json.loads(data.decode("utf-8", "replace")).get("version") if data is not None else None
    except (ValueError, RecursionError, AttributeError):
        value = None
    return value if isinstance(value, str) and _RELEASE.fullmatch(value) else "unknown"


def _digest(folder):
    """The sha256 (hex) of a core skill's folder: each file below it, by its path below the folder, its name and its
    contents, sorted by path; `cut` where more than DIGEST_FILES files or DIGEST_MOST bytes would be read; `unknown`
    where it cannot be read. No link is followed, and what is not a regular file is left out."""
    if not os.path.isdir(folder):
        return "unknown"
    found = []
    try:
        for top, dirs, files in os.walk(folder):
            dirs.sort()
            for name in files:
                path = os.path.join(top, name)
                st = os.lstat(path)
                if stat.S_ISREG(st.st_mode):
                    found.append((os.path.relpath(path, folder).replace(os.sep, "/"), path, st.st_size))
                    if len(found) > DIGEST_FILES:
                        return "cut"
    except OSError:
        return "unknown"
    if sum(size for _, _, size in found) > DIGEST_MOST:
        return "cut"
    import hashlib
    digest = hashlib.sha256()
    for rel, path, size in sorted(found):
        data = _read_file(path, size)
        if data is None or len(data) != size:
            return "unknown"
        digest.update(rel.encode("utf-8", "surrogateescape") + b"\0" + data + b"\0")
    return digest.hexdigest()


def _core_version(plugin, folder):
    """The version a core skill's call used: the plugin's release and a digest of the skill's folder, `<release>+<digest>`
    (`+cut`, `+unknown`): the plugin's release is 1.0.0 and nothing changes it from one release to the next, so the
    digest is what tells two apart."""
    return f"{_release(plugin)}+{_digest(os.path.join(plugin, 'skills', folder))}"


def _called(event):
    """The skill's name for a call Claude chose, as the model wrote it and the Skill tool takes it (trimmed, one leading
    `/` off): `tool_response.commandName` where the call's result gives it and it fits NAME (Claude Code 2.1.304 puts
    the model's own text there, not the name it resolved: a bare name stays bare), else the call's own `skill` the same
    way. A call that failed has no result, so its name is always the second, and the two outcomes follow one rule."""
    response = event.get("tool_response")
    named = response.get("commandName") if isinstance(response, dict) else None
    if isinstance(named, str) and NAME.fullmatch(named):
        return named
    given = event.get("tool_input")
    asked = given.get("skill") if isinstance(given, dict) else None
    if isinstance(asked, str):
        asked = asked.strip()
        return asked[1:] if asked.startswith("/") else asked
    return None


def skills_record(event, directory=None, plugin=PLUGIN, shadows=None):
    """Count one hook event for the skills hook (the module's docstring, --skills): a `Skill` call that reached
    PostToolUse or PostToolUseFailure, or the expansion of a typed command that is a skill the session has. True when a
    line was written. `plugin` and `shadows` are where the core plugin and the skills Claude Code runs first are looked
    for (the defaults; tests give their own)."""
    if not isinstance(event, dict):
        return False
    directory = directory if directory is not None else _session_dir(event)
    if not directory or not os.path.isdir(directory):
        return False
    name = event.get("hook_event_name")
    if name in ("PostToolUse", "PostToolUseFailure"):
        if event.get("tool_name") != "Skill":
            return False
        skill, path, failed = _called(event), "claude", name == "PostToolUseFailure"
    elif name == "UserPromptExpansion":
        if event.get("expansion_type") != "slash_command":
            return False
        skill, path, failed = event.get("command_name"), "typed", None
    else:
        return False
    if not isinstance(skill, str) or not NAME.fullmatch(skill):
        return False
    work = os.path.join(directory, "work")
    work = work if os.path.isdir(work) else None
    kind, folder = _skill_of(skill, work, plugin, _shadows() if shadows is None else shadows)
    if kind == "core" and not skill.startswith(CORE + ":"):
        skill = f"{CORE}:{folder}"      # called by its bare name: the one skill, under the name a typed call gives it
    if kind == "other" and path == "typed":
        # a typed expansion does not say a skill from a custom command: only skills of the session's are counted
        return False
    version = _business_version(work, folder) if kind == "business" else _core_version(plugin, folder) \
        if kind == "core" else None
    import secrets
    _append(directory, SKILLS, {"id": secrets.token_hex(8), "at": int(time.time()), "skill": skill, "kind": kind,
                                "path": path, "failed": failed, "version": version}, MAX_SKILLS)
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    gemini = "--gemini" in argv
    mapping = "--map" in argv
    limiting = "--limit" in argv
    skilling = "--skills" in argv
    argv = [a for a in argv if a not in ("--gemini", "--map", "--limit", "--skills")]
    try:
        # a call's event carries the tool's whole response, often far past the state's 64 KB: read whole for --map
        raw = sys.stdin.read(MAX_MAP_EVENT if mapping or skilling else 64 * 1024) if not sys.stdin.isatty() else ""
        try:
            event = json.loads(raw) if raw.strip() else {}
        except ValueError:
            event = {}
        # a program a person started in one of the session's shells (PP_SHELL) does not speak for the session
        if not os.environ.get("PP_SHELL"):
            if mapping:
                map_record(event)
            elif skilling:
                skills_record(event)
            elif limiting:
                limit_record(event)
            else:
                record(argv[0] if argv else "", event)
    except Exception:  # noqa: BLE001  a hook that fails must not fail the turn
        pass
    if gemini:
        sys.stdout.write("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
