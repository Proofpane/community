"""A chat with Claude Code in a session's worktree (the ADE's own chat page
over Claude Code, the fastest stable way), through the Claude Agent SDK.

One `ClaudeSDKClient` per chat session drives Claude Code in stream-JSON mode, in the session's worktree, with Claude
Code's own system prompt and settings. It runs the image's own `claude`, not the copy the SDK carries (which the image
leaves out), so the program, its version and the managed settings with the gates are the terminal sessions' own. The
SDK starts `claude` with the whole environment of the process it runs in, and the agent's holds names no session is
given (under the local provider, the app's own secrets), so the SDK is handed a small wrapper in place of `claude`
(`write_wrapper`) that starts the real one in an environment built from nothing but the names a session's programs
get.

What the chat says is kept, one event per line, in the session's `chat.jsonl`, which a page reads from any point and
then follows live; a tool call that Claude Code would ask about waits for the page's answer. The SDK's own session id
is kept too, so a chat goes on after the workspace restarts: the next message resumes it.

Events, each {"seq", "time", "kind", ...}:
    user      {"text"}                                  what the owner sent
    text      {"text"}                                  what Claude said
    tool      {"id", "name", "summary"}                 a tool call
    tool_end  {"id", "error", "summary"}                its result
    ask       {"id", "name", "summary", "for_session"}  a tool call waiting for the owner's answer
    answered  {"id", "decision"}                        the answer: allow, allow_session or deny
    result    {"subtype", "error", "cost", "basis", "total", "tokens", "duration_ms", "turns"}   a turn's end; its
                                                        cost is the turn's own (basis "turn"), with claude's running
                                                        total and its tokens so far (see _base); a log from an
                                                        older release has the running total as its cost (see _note)
    status    {"state"}                                 idle, running, needs_you, stopped or failed
    error     {"text"}                                  what went wrong, when the SDK or claude failed
    mcp       {"servers", "held", "strict", "stray"}    at each start of claude: the MCP servers it has (mcp.py), the
                                                        repository's held back until approved, and why none may run
    mcp_change {"change", "name", "line"}               the owner approved, stopped using, added or removed one
    queued    {"text"}                                  an agent run's message sent while it worked: delivered when
                                                        the turn ends (agents.py)
    question  {"text"}                                  an agent run's question for the owner (its ask_owner tool)
    notice    {"text"}                                  what the workspace did in an agent run's start (agents.py): a
                                                        server left out, its tools changed since they were accepted
    rate_limit {"status", "type", "resets_at", "utilization"}   the plan's rate-limit state changed (the SDK's
                                                        RateLimitEvent): allowed_warning nearing it, rejected at it
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

from . import claude_cli, mcp as mcps, watch

MAX_SEND = 32 * 1024          # what the owner sends at once
MAX_TEXT = 40_000             # an event's text beyond this is cut: an event fits one frame to the app (256 KB)
SUMMARY = 200                 # a tool's argument or result, one line
MAX_READ = 2000               # events given at once
MAX_DENIALS = 50              # refused calls named in a turn's result
MAX_READ_BYTES = 1_000_000    # and at most this much of the log; the page asks again from where it got to
STDERR_KEPT = 20              # the last lines claude wrote to stderr, for a failure's message
MAX_QUEUED = 5000             # events waiting for one page; one that falls further behind is dropped and reads again
STREAM_EVERY = 0.05           # what claude is writing reaches the pages at most this often, gathered in between
STATES = ("idle", "running", "needs_you", "stopped", "failed")
ANSWERED = 2.0                # a question answered no is given this long to reach claude before its interrupt
MCP_TIMEOUT = 30              # claude's answer about its MCP servers
# the first build seen to bring a session's running total back when it resumes one (2.1.277; 2.1.274 does not, and
# the workspace runs one or the other: the image's own, or the current stable after an update)
RESTORES_FROM = (2, 1, 277)
NO_MCP = {"off": [], "strict": None, "servers": []}
# Claude Code's question tool wants its own interactive picker, which a chat does not have: left out, Claude asks in
# its text, and the owner answers with a message
NOT_IN_A_CHAT = ("AskUserQuestion",)
DECISIONS = ("allow", "allow_session", "deny")
# a hosted workspace's chat whose start the approval hook could not be added to: not started, since a
# start without it would let its gated commands through on the chat's own answer
HELD_REFUSED = ("The chat could not be given the approval hook, which holds the Bash commands on its list (a git "
                "push, a gh pr merge or release, a deploy, a publish, an rm -rf and the others) for your approval "
                "under Approvals, so it was not started.")

# The wrapper keeps the names it is given (write_wrapper) and drops every other.
WRAPPER = '''#!{python} -I
# Written by the workspace agent (proofpane_workspace/chat.py): the Claude Agent SDK starts the program it is given with
# the agent's whole environment; this starts the real claude with the names below only, in an environment built from
# nothing, as a terminal session's programs are.
import os, shutil, sys
keep = {keep!r}
env = {{k: v for k, v in os.environ.items() if k in keep}}
claude = {claude!r} or shutil.which("claude", path=env.get("PATH"))
if claude is None:
    sys.exit("claude is not installed in this workspace.")
# an agent's run on a Mac (agents.py sets the name, the file this process writes its id to, and it is not passed on):
# its claude, this process once it has started it, leads a session of its own, whose every process the run's end
# ends whatever its program (processes.py)
told = os.environ.get("PP_AGENT_SESSION")
if told:
    try:
        os.setsid()
        if os.path.lexists(told):
            os.unlink(told)
        # made new and read by its owner alone, never written through a link put there since (O_EXCL fails it)
        with os.fdopen(os.open(told, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass
os.execve(claude, [claude] + sys.argv[1:], env)
'''



def _write_private(path, value):
    """Write value as JSON to a new file only its owner reads: whatever was at the path (a file, a link) goes first,
    and the file is made new, never opened through a link to somewhere else."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)          # O_EXCL: a link there fails it
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh)

class ChatError(ValueError):
    pass


class ChatStopped(ChatError):
    """The chat was stopped while its claude started: that claude is ended, and nothing failed."""


# besides a session's own (workspace.PASS_ENV): the terminal's type, the session's two, the two the SDK sets, the
# folder of the account the chat runs on (accounts.py), when it is not the default, and an agent run's (agents.py):
# that it is one, for the gate, its agent, its mark (processes.py), background tasks off, so a result is a turn's
# real end, and its agent's compaction point when it has one
WRAPPER_NAMES = ("TERM", "COLORTERM", "PP_SESSION_ID", "PP_SESSION_DIR", "CLAUDE_CODE_ENTRYPOINT",
                 "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CONFIG_DIR", "PP_AGENT_RUN", "PP_AGENT_ID", "PP_AGENT_RUN_ID",
                 "PP_AGENT_DIR", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
                 "PP_REPLAY", "PP_CONTROLLER_CHAT")


def write_wrapper(path, names, claude=None):
    """Write the wrapper the SDK starts in place of claude, readable and runnable by the agent's user only: the claude
    named (the build in use, claude_update.py), or the one on the environment's PATH."""
    keep = sorted(set(names) | set(WRAPPER_NAMES))
    text = WRAPPER.format(python=sys.executable, keep=keep, claude=claude)
    # a file of its own for each writer, put in place whole: chats starting together each write the wrapper again
    # (workspace._chat_start), from threads, and a name they shared would let one's write tear another's
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o700)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    watch.wrote(path)
    return path


def available():
    """Whether the Claude Agent SDK is installed here."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True


def _cut(text, n):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= n else text[:n] + " ..."


def _line(text):
    return _cut(" ".join(str(text).split()), SUMMARY)


def summarize(name, args):
    """One line for a tool call: its command, path, pattern or address, else its first text argument."""
    args = args if isinstance(args, dict) else {}
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        if isinstance(args.get(key), str) and args[key].strip():
            return _line(args[key])
    for value in args.values():
        if isinstance(value, str) and value.strip():
            return _line(value)
    return ""


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _server(s):
    """One MCP server as claude reported it, in what the page is shown: its name, where it comes from, its state, why
    it failed, how it runs, and how many tools it gives."""
    config = s.get("config") if isinstance(s.get("config"), dict) else {}
    tools = s.get("tools") if isinstance(s.get("tools"), list) else []
    info = s.get("serverInfo") if isinstance(s.get("serverInfo"), dict) else {}
    # each tool as claude names it (with its annotations; claude does not report a tool's description), and the
    # server's own name and version: what an agent run's change watch compares (agents.tools_digest)
    tool_list = [{"name": _line(str(t.get("name", ""))),
                  "annotations": t.get("annotations") if isinstance(t.get("annotations"), dict) else {}}
                 for t in tools[:500] if isinstance(t, dict)]
    return dict(mcps.shown(config), name=_line(s.get("name", "")), scope=_line(s.get("scope") or ""),
                status=_line(s.get("status") or ""), error=_line(s.get("error") or "") or None, tools=len(tools),
                tool_list=tool_list, server_info={"name": _line(str(info.get("name") or "")),
                                                  "version": _line(str(info.get("version") or ""))})


def _tokens(m):
    """(the turn's own tokens, from its usage; every model's tokens so far, from its model usage), None where claude
    gave none."""
    usage = m.usage if isinstance(getattr(m, "usage", None), dict) else None
    models = m.model_usage if isinstance(getattr(m, "model_usage", None), dict) else None
    turn = sum(int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                 "cache_creation_input_tokens")) if usage else None
    so_far = sum(int(v.get(k) or 0) for v in models.values() if isinstance(v, dict)
                 for k in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")) \
        if models else None
    return turn, so_far


def read_events(path, after=0, limit=MAX_READ, budget=MAX_READ_BYTES):
    """(the events after `after` in a chat's log, in order, at most `limit` of them and about `budget` bytes, and
    whether there are more after them)."""
    out, size = [], 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and isinstance(ev.get("seq"), int) and ev["seq"] > after:
                    if len(out) >= limit or (out and size + len(line) > budget):
                        return out, True
                    out.append(ev)
                    size += len(line)
    except FileNotFoundError:
        pass
    return out, False


def _last(path):
    """(the last seq, the SDK's session id) in a chat's log."""
    seq, sdk = 0, None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and isinstance(ev.get("seq"), int):
                    seq = max(seq, ev["seq"])
                    if ev.get("kind") == "session" and isinstance(ev.get("sdk_session"), str):
                        sdk = ev["sdk_session"]
    except FileNotFoundError:
        pass
    return seq, sdk


class Chat:
    """One session's chat. Made and used inside the event loop that serves the page."""

    def __init__(self, session_id, session_dir, cwd, env, wrapper, settings=None, listeners=None, on_activity=None,
                 chosen=None, mcp_plan=None, run=None, own_tools=None, before_start=None):
        self.session_id = session_id
        # why claude may not start now, or None (workspace._chat_start: one below the gate's floor, which also writes
        # the wrapper again to name the claude it checked), asked at each of its starts, so one replaced while the chat
        # is open is seen at its next start
        self.before_start = before_start or (lambda: None)
        # a plain chat's own tools (agents.chat_tools): the workspace's server, to start agents' runs, when its
        # settings let it; worked out from its settings at each start of its claude
        self.own_tools = own_tools
        # the owner's controller's chat (controller.py): no built-in tool at all (claude's --tools "", so its
        # own tools are all it has), no settings read from files, its first message as large as its brief and packet,
        # and never its conversation read back from its transcript at a start (`resumes`: the account's folder, which
        # any session's programs can write); every other chat keeps claude's own tools, the user's, project's and
        # local settings, MAX_SEND, and its conversation at each start again
        self.base_tools = None
        self.setting_sources = ["user", "project", "local"]
        self.send_most = MAX_SEND
        self.resumes = True
        # an agent's run (agents.py): settings of its own merged into claude's, the workspace's MCP server, its allow
        # rules, its messages kept while a turn works, and what moves the run's state as the chat's moves
        run = run or {}
        self.settings_extra = run.get("settings_extra") or {}
        self.mcp_servers = run.get("mcp_servers") or {}
        # a run's own servers (their secret headers and variables in them) go to claude as this file's, never on its
        # command line, where every process on the machine can read them; the SDK's own server stays inline
        self.mcp_file = run.get("mcp_file")
        self.allowed_tools = list(run.get("allowed_tools") or [])
        self.queue_input = bool(run)
        self.on_turn_end = run.get("on_turn_end") or (lambda ev, delivering: None)
        self.on_state = run.get("on_state") or (lambda state: None)
        # claude started for a run, before its message: what to drop (its change watch); a run's servers are never
        # turned off or on, which claude would keep for every chat on the repository
        self.on_connected = run.get("on_connected")
        self.is_run = bool(run)
        # the plugin folders its claude is given at this start (workspace._chat_start, core_plugin.py): set by
        # before_start, so read once it has answered
        self.plugins = []
        # the skills hook's settings its claude is given at this start (workspace._chat_start,
        # settings.skills_settings): set by before_start from the version it read, so added once it has answered, as
        # the plugins are; none for an agent's run and for the controller's chat
        self.skills = {}
        # the approval hook's settings (settings.ade_hook_settings): set by before_start for the owner's
        # own chats in a hosted workspace, and none for an agent's run and the controller's chat; once set, a start
        # that cannot be given them is refused, never made without them (_connect)
        self.held = {}
        # a run's hard stops for each start of its claude (its steps and dollars left), and its rate-limit events
        self.options = run.get("options") or (lambda: {})
        self.context_window = None  # the model's window, as its last result named it
        self.on_rate_limit = run.get("on_rate_limit")
        self.on_tool = run.get("on_tool") or (lambda name, given: None)     # each tool call, for the live rules
        # a task's attempt (task_attempt.py): each message of the SDK's stream as it comes, before the chat reads it;
        # and the chat's claude gone, with whether its own stream ended (claude's channel closed) or the chat ended it
        self.on_message = run.get("on_message")
        self.on_closed = run.get("on_closed")
        self.queued = []
        self.delivering = None
        self.last_error = None
        # its model, effort and permission mode, as the page chose them (workspace.chat_settings); none, Claude Code's
        # own; a new effort waits for claude's next start, which is the only time it takes one
        self.chosen = dict(chosen or {})
        self.restart = False
        # which MCP servers claude may start (mcp.plan), asked at each of its starts; and what it said it has
        self.mcp_plan = mcp_plan or (lambda: NO_MCP)
        self.mcp_last = None
        self.mcp_removed = set()    # (scope, name) removed while claude runs: it still lists them, off, until it stops
        self.mcp_fresh = set()      # names just added from the page: claude's next start turns them on if it kept them off
        self.path = os.path.join(session_dir, "chat.jsonl")
        self.cwd, self.env, self.wrapper, self.settings = cwd, env, wrapper, settings
        self.listeners = listeners if listeners is not None else set()
        self.on_activity = on_activity or (lambda: None)
        self.seq, self.sdk_session = _last(self.path)
        self.state = "stopped"
        self.client = None
        self.connecting = None      # the client being connected: a stop meanwhile leaves it to the connect to end it
        self.reader = None
        self.pending = {}           # an ask's id -> (the future its answer resolves, the CLI's suggestions)
        self.asks = {}              # an ask's id -> what the page shows of it while it waits
        self.stderr = []
        self.cost = 0.0
        # claude's total_cost_usd is a running total: within one of its processes, and, from a later build than the
        # image's (2.1.277, seen on 2026-09-29), over the session too, brought back when claude resumes it after a
        # normal exit (2.1.274 starts over). A turn's own cost is what it added (_base)
        self.process_cost = None    # the current claude's running total; None until its first result
        self.claude_version = None  # the current claude's, from its init message
        self.seen = []              # (tokens, running total) at each result, from the log and as they come
        self._last_old = 0.0        # the last running total read from a log from before
        self.last_text = None
        self.last_words = None              # its last text whole, as this process wrote it: never read from the log
        self.last_at = None
        # what claude is writing, as it writes it (_stream): the content block it began last, counted while this agent
        # runs; how much of that block's text the pages have, in UTF-16 units as a page counts; what is not sent yet
        self.block = 0
        self.sent = 0
        self.unsent = ""
        self.flush_at = None
        for ev in read_events(self.path, 0, sys.maxsize, sys.maxsize)[0]:
            self._note(ev)

    # the log

    def _note(self, ev):
        if ev.get("kind") == "result" and ev.get("basis") == "turn":
            if isinstance(ev.get("cost"), (int, float)):
                self.cost += ev["cost"]
            if isinstance(ev.get("total"), (int, float)):
                self.seen.append((ev.get("tokens") if isinstance(ev.get("tokens"), int) else None, ev["total"]))
        elif ev.get("kind") == "result" and isinstance(ev.get("cost"), (int, float)):
            # a log from before: claude's running total, which starts over when claude does (the total drops)
            total = ev["cost"]
            self.cost += total - self._last_old if total >= self._last_old else total
            self._last_old = total
            self.seen.append((None, total))
        if ev.get("kind") in ("text", "user") and isinstance(ev.get("text"), str):
            self.last_text = _line(ev["text"])
        if ev.get("kind") == "error" and isinstance(ev.get("text"), str):
            self.last_error = _line(ev["text"])
        self.last_at = ev.get("time")

    def _emit(self, kind, **fields):
        self._flush()                       # what was streamed reaches a page before what is logged after it
        self.seq += 1
        ev = {"seq": self.seq, "time": int(time.time()), "kind": kind, **fields}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        if kind == "text" and isinstance(fields.get("text"), str):
            self.last_words = fields["text"]
        self._note(ev)
        self.on_activity()
        for q in list(self.listeners):
            if q.qsize() >= MAX_QUEUED:
                self.listeners.discard(q)
                q.put_nowait(None)          # it fell behind: the page reads the log again from where it was
            else:
                q.put_nowait((self.session_id, ev))
        return ev

    # what claude is writing, as it writes it: sent to the pages listening now, never to the log, where each block is
    # written whole when it ends, as before. Text is gathered and sent at most every STREAM_EVERY seconds; each piece
    # says its block and where in the block it starts, so a page that joined in the middle of one, or missed a piece,
    # knows to wait for the logged text instead

    def _stream(self, event):
        kind = event.get("type") if isinstance(event, dict) else None
        if kind == "content_block_start":
            self._flush()
            self.block += 1
            self.sent = 0
            b = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            if b.get("type") in ("thinking", "redacted_thinking"):
                self._delta(thinking=True)
            elif b.get("type") in ("tool_use", "server_tool_use"):
                self._delta(tool=_line(str(b.get("name") or "")))
        elif kind == "content_block_delta":
            d = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            if d.get("type") == "text_delta" and isinstance(d.get("text"), str) and d["text"]:
                self.unsent += d["text"]
                if self.flush_at is None:
                    self.flush_at = asyncio.get_running_loop().call_later(STREAM_EVERY, self._flush)

    def _flush(self):
        if self.flush_at is not None:
            self.flush_at.cancel()
            self.flush_at = None
        if self.unsent:
            text, self.unsent = self.unsent, ""
            self._delta(offset=self.sent, text=text)
            self.sent += len(text.encode("utf-16-le")) // 2

    def _delta(self, **fields):
        d = {"after": self.seq, "block": self.block, **fields}
        for q in list(self.listeners):
            if q.qsize() < MAX_QUEUED:      # a page that has fallen behind misses it and waits for the log
                q.put_nowait((self.session_id, d, "delta"))

    def _state(self, state):
        if state != self.state:
            self.state = state
            self._emit("status", state=state)
            self.on_state(state)

    def note(self, kind, **fields):
        """An event of the run's own in the chat's log (agents.py): its question for the owner."""
        return self._emit(kind, **fields)

    def view(self):
        return {"state": self.state, "seq": self.seq, "pending": sorted(self.pending), "cost": round(self.cost, 6),
                "chosen": dict(self.chosen), "queued": len(self.queued),
                "last_text": self.last_text, "last_at": self.last_at,
                "asks": [self.asks[k] for k in sorted(self.asks) if k in self.pending]}

    # talking to claude

    async def _connect(self):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        # the repository's MCP servers held back but the approved ones, named on the command line, where its own
        # settings cannot turn them on; none at all when a .mcp.json could not be read (mcp.py)
        try:
            plan = self.mcp_plan()
            settings = mcps.settings_value(self.settings, [] if plan["strict"] else plan["off"], self.settings_extra)
        except (OSError, ValueError) as e:
            raise ChatError(f"The chat's MCP servers could not be worked out: {e}") from None
        servers, allowed = dict(self.mcp_servers), list(self.allowed_tools)
        if self.own_tools is not None:
            own = self.own_tools(dict(self.chosen)) or {}
            servers.update(own.get("mcp_servers") or {})
            allowed += own.get("allowed_tools") or []
        extra = {}
        if self.mcp_file:
            filed = {n: s for n, s in servers.items() if not (isinstance(s, dict) and s.get("type") == "sdk")}
            servers = {n: s for n, s in servers.items() if n not in filed}
            if filed:
                # measured on the image's Claude Code 2.1.274: a second --mcp-config naming a file is taken beside
                # the SDK's own, and the file's values are on no command line
                try:
                    _write_private(self.mcp_file, {"mcpServers": filed})
                except OSError as e:
                    raise ChatError(f"The run's MCP servers could not be written for its claude: "
                                    f"{e.strerror or e}") from None
                extra["mcp-config"] = self.mcp_file
        options = ClaudeAgentOptions(
            cwd=self.cwd, cli_path=self.wrapper, env=dict(self.env),
            system_prompt={"type": "preset", "preset": "claude_code"}, setting_sources=list(self.setting_sources),
            settings=settings, strict_mcp_config=bool(plan["strict"]),
            permission_mode=self.chosen.get("permission_mode", "default"),
            model=self.chosen.get("model"), effort=self.chosen.get("effort"), can_use_tool=self._ask,
            resume=self.sdk_session if self.resumes else None, disallowed_tools=list(NOT_IN_A_CHAT), stderr=self._err,
            include_partial_messages=True, mcp_servers=servers, allowed_tools=allowed, extra_args=extra,
            **({"tools": list(self.base_tools)} if self.base_tools is not None else {}), **self.options())
        client = self.client = self.connecting = ClaudeSDKClient(options)
        refused = None
        try:
            # why the claude it would start may not (workspace._chat_start: one below the gate's floor), asked first,
            # in a thread since it reads claude's version and writes the wrapper, and with the client already the
            # chat's, so that a stop meanwhile is a stop during the connect: found below, with nothing started
            refused = await asyncio.to_thread(self.before_start)
            if not refused and self.client is client:
                # Proofpane's core, as before_start found it for this start (the SDK's own option: --plugin-dir)
                options.plugins = [{"type": "local", "path": p} for p in self.plugins]
                # the skills hook, from the same read: its hooks added after the ones the settings value holds
                options.settings = mcps.with_hooks(options.settings, self.skills)
                if self.held:
                    # the approval hook: added as the skills hook is, but a start it cannot be added to
                    # is refused, where the skills hook's start goes on without it
                    held = mcps.with_held(options.settings, self.held)
                    if held is None:
                        refused = HELD_REFUSED
                    options.settings = held
            if not refused and self.client is client:
                await client.connect()
        except Exception as e:  # noqa: BLE001  the page is told why, and the next message tries again
            if self.client is client:
                self.client = None
            raise ChatError(self._why(e)) from None
        finally:
            self.connecting = None
        if refused and self.client is client:
            self.client = None
            raise ChatError(refused)
        if self.client is not client:
            # stopped while claude started (the SDK asks its version, then spawns it, then waits for its answer): a
            # stop then could end nothing, and a disconnect in the middle of the SDK's connect would leave the claude
            # it spawned running, so the stop is kept to here, where that claude is ended
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise ChatStopped("The chat was stopped while claude started.")
        self.process_cost = None            # a new claude: what it brought back is read from its first result
        self.reader = asyncio.get_running_loop().create_task(self._read(client))
        self.mcp_removed = set()
        await self._mcp_started(plan)

    async def _mcp_started(self, plan):
        """What claude says it has, written into the chat's log when there is anything to say. A repository's server
        that no .mcp.json read here declares (so not held back by name) is turned off at once and said; claude keeps
        that for the repository. One approved on the page that claude has off (a choice it kept from before, from a
        terminal or an earlier turn-off) is turned on: the approval is the owner's word now; so is one the owner just
        added from the page (an earlier Remove turned servers off), and no other."""
        servers = await self.mcp_status() or []
        if self.is_run:
            self._emit("mcp", servers=[{k: s[k] for k in ("name", "scope", "status")} for s in servers],
                       held=list(plan["off"]), strict=plan["strict"], stray=[])
            return
        allowed = {s["name"] for s in plan["servers"] if s["approved"]}
        stray, back = [], []
        for s in servers:
            if s["scope"] == "project" and s["name"] not in allowed and s["status"] != "disabled":
                stray.append(s["name"])
                await self.mcp_off(s["name"])
            elif s["status"] == "disabled" and (s["scope"] == "project" and s["name"] in allowed or
                                                 s["scope"] in ("user", "local") and s["name"] in self.mcp_fresh):
                back.append(s["name"])
                await self.mcp_on(s["name"])
        self.mcp_fresh = set()
        if back:
            servers = await self.mcp_status() or []
        if servers or plan["off"] or plan["strict"] or stray:
            self._emit("mcp", servers=[{k: s[k] for k in ("name", "scope", "status")} for s in servers],
                       held=list(plan["off"]), strict=plan["strict"], stray=stray)

    async def mcp_status(self):
        """The MCP servers the running claude has, as it says (the SDK's get_mcp_status), else what it said last."""
        if self.client is not None:
            try:
                got = await asyncio.wait_for(self.client.get_mcp_status(), MCP_TIMEOUT)
            except Exception:  # noqa: BLE001  what it said last stands
                got = None
            if isinstance(got, dict) and isinstance(got.get("mcpServers"), list):
                self.mcp_last = [_server(s) for s in got["mcpServers"] if isinstance(s, dict)]
        return self.mcp_last

    async def mcp_off(self, name):
        """Turn one of the running claude's MCP servers off now (claude keeps that for the repository)."""
        await self._toggle(name, False)

    async def mcp_on(self, name):
        await self._toggle(name, True)

    async def _toggle(self, name, on):
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.toggle_mcp_server(name, on), MCP_TIMEOUT)
            except Exception:  # noqa: BLE001  the next start decides
                pass

    def _base(self, total, turn_tokens, tokens):
        """What a claude just started brought back of its session's spend, read from its first result. Its tokens so
        far (every model's, restored ones too) against the turn's own (all of the turn's calls; seen with 2.1.274 and
        2.1.277): equal, it started fresh, and brought back nothing. Otherwise what it brought back is the session as
        it stood at a result the chat logged: the one whose tokens so far are the ones brought back, else the last one
        when those are at least its own (tokens counted apart, a sub-agent's say), else nothing. A build seen not to
        bring anything back brings nothing back: a turn's usage leaves out its sub-agents while its model usage counts
        them, so a first turn with a big sub-agent could otherwise look like a session brought back."""
        if self.claude_version is not None and self.claude_version < RESTORES_FROM:
            return 0.0
        if not isinstance(tokens, int) or not isinstance(turn_tokens, int) or tokens <= turn_tokens:
            return 0.0
        back = tokens - turn_tokens
        for then, total_then in reversed(self.seen):
            if then == back:
                return total_then
        if self.seen:
            then, total_then = self.seen[-1]
            if (then is None or back >= then) and total_then <= total:
                return total_then
        return 0.0

    def _err(self, line):
        self.stderr = (self.stderr + [str(line).rstrip()])[-STDERR_KEPT:]

    def _why(self, e):
        tail = " ".join(x for x in self.stderr[-3:] if x)
        return _cut(f"Claude Code could not start: {type(e).__name__}: {e}" + (f" ({tail})" if tail else ""), 1000)

    async def send(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ChatError("Say something to send.")
        if len(text.encode("utf-8")) > self.send_most:
            raise ChatError(f"A message is at most {self.send_most // 1024} KB.")
        if self.state in ("running", "needs_you"):
            if self.queue_input:
                # an agent's run: kept, and sent when the turn ends
                self.queued.append(text)
                self._emit("queued", text=_cut(text, MAX_TEXT))
                return self.view()
            raise ChatError("Claude is still working on the last message: wait for it, or stop it.")
        if self.restart and self.client is not None:
            await self.stop()               # a new effort: claude starts again with it, and with the conversation
        self.restart = False
        self._emit("user", text=_cut(text, MAX_TEXT))
        # working from here, while claude starts too: a message sent meanwhile waits its turn (a run's) or is refused,
        # as one sent while claude works, and never reaches a claude still connecting
        self._state("running")
        if self.client is None:
            try:
                await self._connect()
                if self.on_connected is not None:
                    drop = await self.on_connected(self)
                    if drop:
                        # started again without them (or strict, as the run's plan now says), before anything is sent
                        await self.stop()
                        for name in drop:
                            self.mcp_servers.pop(name, None)
                        await self._connect()
                        await self.on_connected(self)
            except ChatStopped:
                self._state("stopped")
                raise
            except ChatError as e:
                self._emit("error", text=str(e))
                self._state("failed")
                raise
        try:
            await self.client.query(text)
        except Exception as e:  # noqa: BLE001
            await self._lost(e)
            raise ChatError(self._why(e)) from None
        return self.view()

    async def _read(self, client):
        from claude_agent_sdk import AssistantMessage, RateLimitEvent, ResultMessage, StreamEvent, SystemMessage, UserMessage
        from claude_agent_sdk import TextBlock, ToolResultBlock, ToolUseBlock
        try:
            async for m in client.receive_messages():
                if self.on_message is not None:
                    self.on_message(m)
                if isinstance(m, StreamEvent):
                    if m.parent_tool_use_id is None:        # a sub-agent's writing is logged when it ends, as before
                        self._stream(m.event)
                elif isinstance(m, RateLimitEvent):
                    info = m.rate_limit_info
                    self._emit("rate_limit", status=str(info.status), type=str(info.rate_limit_type or ""),
                               resets_at=info.resets_at, utilization=info.utilization)
                    if self.on_rate_limit is not None:
                        # with what the event says of paid usage past the plan (usage credits): its own status, its
                        # reset, why it is off, and whether this account is on it now (the CLI's isUsingOverage)
                        raw = info.raw if isinstance(getattr(info, "raw", None), dict) else {}
                        self.on_rate_limit({"status": info.status, "rate_limit_type": info.rate_limit_type,
                                            "resets_at": info.resets_at, "utilization": info.utilization,
                                            "overage_status": info.overage_status,
                                            "overage_resets_at": info.overage_resets_at,
                                            "overage_disabled_reason": info.overage_disabled_reason,
                                            "is_using_overage": raw.get("isUsingOverage") is True})
                elif isinstance(m, SystemMessage):
                    sdk = m.data.get("session_id") if isinstance(m.data, dict) else None
                    if m.subtype == "init" and isinstance(m.data, dict):
                        version = claude_cli.parse_version(str(m.data.get("claude_code_version") or ""))
                        self.claude_version = version or self.claude_version
                    if m.subtype == "init" and isinstance(sdk, str) and sdk != self.sdk_session:
                        self.sdk_session = sdk
                        self._emit("session", sdk_session=sdk, model=_line(m.data.get("model", "")))
                elif isinstance(m, AssistantMessage):
                    for b in m.content:
                        if isinstance(b, TextBlock) and b.text.strip():
                            self._emit("text", text=_cut(b.text, MAX_TEXT))
                        elif isinstance(b, ToolUseBlock):
                            self._emit("tool", id=b.id, name=_line(b.name), summary=summarize(b.name, b.input))
                            self.on_tool(b.name, b.input)
                elif isinstance(m, UserMessage) and isinstance(m.content, list):
                    for b in m.content:
                        if isinstance(b, ToolResultBlock):
                            self._emit("tool_end", id=b.tool_use_id, error=bool(b.is_error),
                                       summary=_line(_result_text(b.content)))
                elif isinstance(m, ResultMessage):
                    total, cost = m.total_cost_usd, None
                    turn_tokens, tokens = _tokens(m)
                    windows = [u.get("contextWindow") for u in (m.model_usage or {}).values()
                               if isinstance(u, dict) and isinstance(u.get("contextWindow"), int)] \
                        if isinstance(m.model_usage, dict) else []
                    self.context_window = max(windows) if windows else self.context_window
                    if isinstance(total, (int, float)):
                        if self.process_cost is None:
                            self.process_cost = self._base(total, turn_tokens, tokens)
                        cost = round(total - self.process_cost if total >= self.process_cost else total, 10)
                        self.process_cost = total
                    # the calls the turn refused (the permission mode's, a hook's such as the gate's, an answer's
                    # no), each by its id, as 2.1.274 lists them in permission_denials (metrics.py)
                    denials = [{"id": str(d.get("tool_use_id") or "")[:80], "name": _line(d.get("tool_name") or "")}
                               for d in (m.permission_denials or []) if isinstance(d, dict)][:MAX_DENIALS]
                    ev = self._emit("result", subtype=_line(m.subtype), error=bool(m.is_error), cost=cost,
                                    basis="turn", total=total if isinstance(total, (int, float)) else None,
                                    tokens=tokens, duration_ms=m.duration_ms, turns=m.num_turns,
                                    api_ms=m.duration_api_ms, denials=denials)
                    self._state("idle")
                    delivering = bool(self.queued) and not m.is_error
                    # the run's word on what was kept (an agent's run: its steps): "hold" keeps it unsent (the run
                    # ends), "restart" sends it to a claude started again (with the steps the run has left)
                    told = self.on_turn_end(ev, delivering)
                    if delivering and told != "hold":
                        self.restart = self.restart or told == "restart"
                        self.delivering = asyncio.get_running_loop().create_task(self._deliver())
            await self._lost(None, ended=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            await self._lost(e, ended=True)

    async def _deliver(self):
        """A message kept while the turn worked, sent now that it has ended."""
        if self.queued and self.state == "idle":
            text = self.queued.pop(0)
            try:
                await self.send(text)
            except ChatError:
                pass

    async def _lost(self, e, ended=False):
        """claude ended or failed: its asks are void, and the next message starts it again with the conversation.
        `ended`: its own stream ended (claude closed its channel, or failed), not the chat stopping it."""
        for fut, _ in self.pending.values():
            if not fut.done():
                fut.cancel()
        self.pending.clear()
        self.asks.clear()
        client, self.client = self.client, None
        if e is not None:
            self._emit("error", text=self._why(e))
        if client is not None and client is not self.connecting:     # one being connected: its connect ends it
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._state("failed" if e is not None else "stopped")
        if self.on_closed is not None and client is not None:
            self.on_closed(e, ended)

    async def _ask(self, tool_name, tool_input, context):
        """Claude Code's permission prompt: wait for the page's answer."""
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        ask_id = context.tool_use_id or f"ask-{self.seq + 1}"
        fut = asyncio.get_running_loop().create_future()
        self.pending[ask_id] = (fut, list(context.suggestions or []))
        ev = self._emit("ask", id=ask_id, name=_line(tool_name), summary=summarize(tool_name, tool_input),
                        for_session=bool(context.suggestions))
        self.asks[ask_id] = {k: ev[k] for k in ("id", "name", "summary", "for_session", "time")}
        self._state("needs_you")
        try:
            decision = await fut
        except asyncio.CancelledError:
            return PermissionResultDeny(message="The chat stopped before it was answered.", interrupt=True)
        finally:
            self.pending.pop(ask_id, None)
            self.asks.pop(ask_id, None)
            if not self.pending and self.state == "needs_you":
                self._state("running")
        if decision == "stop":
            return PermissionResultDeny(message="Stopped from the ADE's chat.", interrupt=True)
        if decision == "deny":
            return PermissionResultDeny(message="The owner said no, from the ADE's chat.")
        if decision == "allow_session":
            updates = []
            for s in context.suggestions or []:
                s.destination = "session"       # for this conversation only, never written into the repository
                updates.append(s)
            return PermissionResultAllow(updated_permissions=updates or None)
        return PermissionResultAllow()

    def answer(self, ask_id, decision):
        if decision not in DECISIONS:
            raise ChatError("An answer is allow, allow_session or deny.")
        entry = self.pending.get(ask_id)
        if entry is None:
            raise ChatError("That question is no longer waiting for an answer.")
        fut, _ = entry
        self._emit("answered", id=ask_id, decision=decision)
        if not fut.done():
            fut.set_result(decision)
        return self.view()

    async def interrupt(self):
        """Stop the turn: a question waiting for an answer is answered no, with the SDK's own interrupt, then claude is
        told to stop whatever else it is doing."""
        if self.client is None or self.state not in ("running", "needs_you"):
            raise ChatError("Claude is not working on anything to stop.")
        answered = False
        for ask_id, (fut, _) in list(self.pending.items()):
            if not fut.done():
                self._emit("answered", id=ask_id, decision="stop")
                fut.set_result("stop")
                answered = True
        if answered:
            # setting the answer only schedules it: the SDK writes it to claude after this goes on, so the interrupt
            # waits for it (under load it went first, and claude never heard the no)
            deadline = asyncio.get_running_loop().time() + ANSWERED
            while self.pending and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
        await self.client.interrupt()
        return self.view()

    async def choose(self, chosen):
        """New settings: the model and the permission mode reach a running claude at once; a new effort, and starting
        agents' runs turned on or off, from the next message, when claude starts again with the conversation."""
        before, self.chosen = self.chosen, dict(chosen)
        self._emit("settings", model=self.chosen.get("model"), effort=self.chosen.get("effort"),
                   permission_mode=self.chosen.get("permission_mode"),
                   may_dispatch=bool(self.chosen.get("may_dispatch")))
        if self.client is not None:
            try:
                if before.get("model") != self.chosen.get("model"):
                    await self.client.set_model(self.chosen.get("model"))
                mode = self.chosen.get("permission_mode", "default")
                if before.get("permission_mode", "default") != mode:
                    await self.client.set_permission_mode(mode)
            except Exception as e:  # noqa: BLE001  the page is told why, and the next message starts claude again
                await self._lost(e)
                raise ChatError(self._why(e)) from None
            if before.get("effort") != self.chosen.get("effort") or \
                    bool(before.get("may_dispatch")) != bool(self.chosen.get("may_dispatch")):
                self.restart = True
        return self.view()

    async def stop(self):
        """End claude; the conversation stays in its log and resumes with the next message."""
        if self.reader is not None:
            self.reader.cancel()
            self.reader = None
        await self._lost(None)
        return self.view()
