"""The usage of the workspace's sessions, reported to the Proofpane organisation the workspace is paired with:
one row per model reply,
posted by the workspace agent itself and never by a program in a session, so that an agent's run and a task's attempt,
which are given no plugin (core_plugin.py), are reported as the owner's own terminals and chats are.

**While paired, and only then.** Each pass reads the pairing first (core_plugin.read:
`~/.proofpane/proofpane-core/config.json`, its `cloud_url`, `device_id` and `token`); with none, it reads nothing else,
writes nothing and posts nothing. A reply is posted only when its own time is at or after the pairing's (`paired_at`,
where the record gives one) and the session's start (its record's `created_at`: a conversation moved or teleported into
a session brings replies made elsewhere), and within the last KEEP seconds (Claude Code removes its transcripts after a
month, runtree.py). A reply whose line gives no time is not posted, since nothing then says whose it is.

**What is read.** Only what the workspace already reads for a session's usage (usage.py, transcripts.py), and only for
`claude`, `codex` and chat sessions, agents' runs, tasks' attempts, the controller's chats and its attempts: a session
of `gemini`, `opencode`, `grok`, `qwen`, `kimi`, `mcode`, a shell or an agent of the person's own is never read for
this.
- A `claude` terminal: every conversation Claude Code keeps for its worktree
  (`<its account's folder>/projects/<slug>/*.jsonl`) and each one's sub-agents
  (`<slug>/<conversation>/subagents/agent-*.jsonl`), the lines whose `cwd` is the worktree or a folder in it. A claude
  a person starts in the session's shell on the same account writes there too, and is counted as the session's.
- A chat, an agent's run, a task's attempt, the controller's chat and an attempt of a requirement's manager: the
  conversations its own chat log names (`chat.jsonl`'s `session` events: the Agent SDK's session ids), each with its
  sub-agents, in its worktree's folder of its account (a task's attempt on a key: that key's own Claude Code folder,
  task_runs.key_folder). A run's worktree can be its agent's, shared by its runs, so a run is read by its own
  conversations alone.
- A `codex` terminal: the conversation files Codex keeps
  (`<CODEX_HOME>/sessions/<year>/<month>/<day>/rollout-*.jsonl`, from the day before the session's start), those whose
  first line names the worktree. Each model call is a `token_usage_record` line of its own (Codex 0.163.0, usage.py); a
  file of a Codex that writes none gives no row.
- A one-turn chat (agents.one_turn: the judge, the critic and the pairwise judge of a run; the business skills' review,
  its critic and its probe, skill_improve.py, which leave no transcript, `--no-session-persistence`): the Agent SDK's
  own messages as the turn ran, its ResultMessage's usage and session id, and its reply's message id. One row per turn.
A reply of Claude's is one request: written on several lines as it streams, each with the same message id and the same
usage (transcripts.py), it is one reply, kept by its message id (else its request id). A line with neither id, a
`<synthetic>` reply and one whose four counts are all nought are not replies the vendor made, and post nothing.

**What leaves the machine, to where, when.** One POST to `<cloud_url>/api/v1/mcp/event`, the product's own route for a
paired device, for each reply: `{"device_id", "token", "vendor": "ade", "agent": "default", "event_type", "payload"}`,
as the plugin's hook posts its rows (core_plugin/hooks/proofpane_hook.py). A redirect is refused, never followed.
Claude's replies, a chat's and a one-turn chat's go as `mcp.subscription.usage`, whose payload holds the one reply:
`count: 1`, `records` (its request id, the session's id, the model, its four token counts and its time) and `totals`
(the four counts), the shape the product prices at ingest. Codex's go as `codex.session.turn`: the model, its input
tokens (which, as OpenAI counts them, include the cached), output, cached and reasoning tokens and the time at the top
of the payload, in the daemon's own names for them.
Every payload carries, at its top:
- `request_id`: the reply's request id from its line (Claude Code's `requestId`); where a line has none, the reply's
  message id; a Codex call's `response_id`; a one-turn chat's request id from its transcript where the turn left one
  (the judges'), else its reply's message id as the SDK gave it (unique to that reply, and the id a transcript keys a
  reply by), else the SDK's session id, a conversation of its own for each turn. The product makes it the row's turn
  id; this module never sends a `turn_id` of its own.
- `session_id`: the ADE's own session id for a terminal, a chat or the controller's (the workspace's session), and for
  an agent's run or a task's attempt its run's id, which every route serves as a session; for a one-turn chat, which
  the ADE has no session for, the Agent SDK's session id.
- `ade_kind`: `terminal`, `chat` (the owner's, and the controller's own chat), `agent_run`, `task_attempt`,
  `controller_attempt` (an attempt of a requirement's manager), `improve_run` (the improve loop's review and sandbox
  runs) or `one_turn`.
- `ade_started_by`, where the workspace knows it: `person` for the owner's terminals and chats, a run the owner started
  by hand and a task's attempt the owner pressed; `controller` for a terminal the owner's controller started, its own
  chat and its attempts; `schedule` and `hook` for a run they started. Left out for any other (a run another run
  dispatched, a replay, an evaluation, a re-verification, the improve loop's, a task's attempt the relay or Auto-resume
  queued, a one-turn chat).
- `ade_agent_id` and `ade_run_id` for a run and an attempt, and for a one-turn chat made for one (a judge's: the run it
  judges); `ade_task_id`, a task's attempt's task, the ADE task's own id (task_shapes.TASK_ID, 32 hexadecimal), never a
  run's; `ade_trigger_id`, the id of the schedule or the hook that started a run.
- `ade_sign_in`: `subscription` where the account's windows were seen (a claude terminal's status line, which a
  subscriber's replies carry and a key's do not, statusline.py; a chat's or a run's rate-limit events, the plan's; for
  a codex terminal, its account's windows, usage.codex_limits); `provider`, with `ade_provider` naming it, for a claude
  terminal on another provider's model (providers.py) and a task's attempt on another provider's key; `own_key` for a
  task's attempt on its own Anthropic key (providers.KEY_ONLY); else `unknown`.
- `claude_account` for a Claude Code session: `default` or the added account's id, as the plugin's rows name it (a
  session on another provider's model `default`, as there); none for Codex's and for an attempt on a key.
- `client`: `claude_code`, or `codex`.
- `ade_cost_est_usd` on a one-turn chat's row alone: the Agent SDK's own estimate of that turn's cost (its
  ResultMessage's `total_cost_usd`), beside the counts the product prices itself.
  An agent's run, a task's attempt and a controller's attempt carry none: their estimate is the run's, known at its
  end, while their rows go as their replies are made, and the product keeps the first row of each request id.
Never the words of a prompt or a reply, a tool's input or output, a file's path or content, a token of a sign-in, a
key or anything of the transcripts but the counts, the ids, the model and the times; and nothing of a session of
another program. The token goes only in the body of the POST to the paired address, as the plugin's own rows carry it.

**When.** A pass runs on a thread of its own, so a post never holds a session up: QUIET seconds after a session's last
activity (a terminal's output, a chat's event: the end of a turn, an agent's run's or an attempt's end among them), or
LONGEST seconds after its first where it never goes quiet; at once for a one-turn chat's row; and once at the agent's
start for every session (catch_up), so that what a restart left unposted is posted then. No fixed schedule: a pass
posts every reply its session's transcripts hold that the record does not, so nothing is lost to a restart, and a reply
made in the seconds before the agent stopped is posted at its next start (nothing is posted at its stop, which waits on
no network).

**Each reply once.** What was posted, or given up, is kept in a record of the workspace's own beside the pairing's:
`~/.proofpane/proofpane-core/usage-sent.log` (0600; a line `<the reply's time> <key>`, ` d` after a drop's as first
written), appended as each post is answered, read back at the agent's start, and now and then written anew with the keys
of the last KEEP seconds alone; and `usage-state.json` beside it (0600): the tries of each reply refused so far, the
drops, the counts, and the one-turn rows not yet posted (one kept while another pairing held is let go unposted). A
reply is never posted again once its key is there, whatever reads its transcript again, and the product keeps one record
per request id as well. The gate refuses a tool call that names the folder, as it does the pairing's
(gate.py), but a session's programs run as the workspace's user: one can change this record (a key added keeps a reply
from being posted; the file removed posts the record's replies again, which the product then keeps once), as it can
change the transcripts the counts come from. The record is the workspace's own word, as the plugin's hooks are
cooperative.

**A refused post.** A post Proofpane does not take (an answer other than 2xx, a redirect, or no answer within TIMEOUT
seconds) is tried again at a later pass, RETRY_AFTER seconds on, the session's later replies waiting behind it; after
TRIES tries in all, across the agent's restarts, it is dropped: its key written as a drop, so it is never tried again,
and the drop kept in `usage-state.json` (when, the reply's request id, session and kind, the kind of row, the tries, and
what Proofpane answered or that it did not answer; never the address or the token), which the page reads through GET
/proofpane's `usage` (Workspace.core_status). No reply is tried forever.

What it does not do: read the usage of any other program; post the cost the SDK estimated (the product prices the
tokens itself); post a linked session's usage; ask Proofpane whether a session may start.
Standard library only; the app never imports it.
"""

import hashlib
import http.client
import json
import math
import os
import re
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import accounts as accounting, core_plugin as core, providers, statusline, transcripts, usage
from . import task_shapes as shapes

EVENT_PATH = "/api/v1/mcp/event"
CLAUDE_EVENT = "mcp.subscription.usage"
CODEX_EVENT = "codex.session.turn"
USER_AGENT = "proofpane-workspace-usage/1"
SENT = "usage-sent.log"
STATE = "usage-state.json"
TIMEOUT = 6.0                           # one post, at most


def _seconds(name, default):
    """A wait the tests make short (the agent's own environment, never a session's); a deployment sets none."""
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if 0 < value <= 3600 else default


QUIET = _seconds("PP_WORKSPACE_USAGE_QUIET", 5.0)       # a session read this long after its last activity
RETRY_AFTER = _seconds("PP_WORKSPACE_USAGE_RETRY", 30.0)   # a refused post's next try, at the soonest
LONGEST = 300.0                         # a session never quiet is read this long after its first activity
IDLE = 60.0                             # the thread ends after this long with nothing to do (_wake starts it again)
TRIES = 3                               # a refused post's tries in all, then it is dropped
KEEP = 35 * 86400                       # a reply this much older than now is not posted, and its key let go
DROPS_KEPT = 50
DROPS_SHOWN = 5
WAITING_MOST = 500                      # one-turn rows not yet posted, at most
MAX_FILES = 200                         # a session's files read in one pass, at most
MAX_BYTES = 64 * 1024 * 1024            # read in one pass, at most; the rest at the next
MAX_LINE = 32 * 1024 * 1024             # a longer line is passed over
MAX_LOG = 64 * 1024 * 1024              # a chat log read in one pass, at most
MAX_KNOWN = 20000                       # files whose reading is remembered between passes, at most
MAX_STATE = 4 * 1024 * 1024
MAX_SENT = 64 * 1024 * 1024
CLAUDE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
STARTED_BY = {"manual": "person", "schedule": "schedule", "hook": "hook"}
LOOP_KINDS = ("review", "sandbox")      # improve.LOOP_KINDS, the improve loop's runs
EVERYONE = ""                           # a due key of its own: the one-turn rows waiting, tried again
_ID = re.compile(r"[0-9a-f]{12}")
_IDENT = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_KEY = re.compile(rb"[A-Za-z0-9_.:#-]{1,240}")
_CONVERSATION = re.compile(r"[0-9A-Za-z][0-9A-Za-z_-]{0,79}")      # transcripts.py's own shape of a session id
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, never followed: the device's token goes in the body."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "a redirect, not followed", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _text(value, most=200):
    return value[:most] if isinstance(value, str) and value and "\0" not in value else None


def _whole(value):
    """A positive whole number as it is (a count, a time in seconds); anything else 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _epoch(text):
    """A line's ISO time (`...Z` or with an offset, any fraction) in Unix seconds, or None."""
    if not isinstance(text, str) or len(text) > 64:
        return None
    text = text.strip().replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(?:\.(\d+))?(.*)$", text)
    if m:
        text = m.group(1) + (f".{m.group(2)[:6].ljust(6, '0')}" if m.group(2) else "") + m.group(3)
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when.timestamp()


def _key(kind, ident):
    """A reply's key in the record: its kind and its id, or the id's digest where it is not a plain one."""
    if _IDENT.fullmatch(ident):
        return f"{kind}:{ident}"
    return f"{kind}#" + hashlib.sha256(ident.encode("utf-8", "replace")).hexdigest()[:32]


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _within(path, work, seen):
    """Whether a line's `cwd` is the worktree or a folder in it (resolved once a pass for each `cwd`)."""
    if not isinstance(path, str) or not path or "\0" in path:
        return False
    real = seen.get(path)
    if real is None:
        real = seen[path] = os.path.realpath(path)
    return real == work or real.startswith(work + os.sep)


def _regular(path):
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _open(path):
    """A regular file opened for reading, never through a link and never waiting on a FIFO; None otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
    except OSError:
        os.close(fd)
        return None
    return os.fdopen(fd, "rb")


def _read_private(path, most):
    fh = _open(path)
    if fh is None:
        return b""
    with fh:
        return fh.read(most)


def _lines(fh, offset, budget):
    """(the offset each complete line starts at, the line) from `offset` on, at most `budget` bytes; a line longer
    than MAX_LINE is passed over whole, and a last one not yet ended is left for a later read."""
    fh.seek(offset)
    while budget > 0:
        line = fh.readline(MAX_LINE + 1)
        if not line:
            return
        if not line.endswith(b"\n"):
            if len(line) <= MAX_LINE:
                return                          # still being written
            skipped = len(line)
            while True:                         # past its end, unread
                more = fh.readline(MAX_LINE + 1)
                if not more:
                    return
                skipped += len(more)
                if more.endswith(b"\n"):
                    break
            yield offset, None, skipped
            offset += skipped
            budget -= skipped
            continue
        yield offset, line, len(line)
        offset += len(line)
        budget -= len(line)


def claude_reply(raw, work, seen):
    """One of Claude Code's transcript lines as a reply of this worktree's ({key, request_id, model, ts, epoch, and the
    four counts}), or None: never its words."""
    if raw is None or b'"assistant"' not in raw:
        return None
    try:
        row = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(row, dict) or row.get("type") != "assistant":
        return None
    message = row.get("message")
    use = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(use, dict) or not _within(row.get("cwd"), work, seen):
        return None
    rid, mid = _text(row.get("requestId")), _text(message.get("id"))
    if not (rid or mid):
        return None
    model = _text(message.get("model"))
    counts = {f: _whole(use.get(f)) for f in CLAUDE_FIELDS}
    if model == "<synthetic>" or not any(counts.values()):
        return None
    ts = _text(row.get("timestamp"), 64)
    return dict(counts, key=_key("claude", mid or rid), request_id=rid or mid, model=model, ts=ts, epoch=_epoch(ts))


def claude_payload(attribution, reply):
    """`mcp.subscription.usage`'s payload for one reply."""
    counts = {f: reply[f] for f in CLAUDE_FIELDS}
    record = dict(counts, request_id=reply["request_id"], session_id=attribution.get("session_id"),
                  model=reply["model"], ts=reply["ts"])
    return dict(attribution, request_id=reply["request_id"], count=1, records=[record], totals=counts)


def codex_reply(row, known, offset):
    """A Codex `token_usage_record` line as one call ({key, request_id, model, ts, epoch, and its counts}), or None."""
    payload = row.get("payload")
    use = payload.get("usage") if isinstance(payload, dict) else None
    if row.get("type") != "token_usage_record" or not isinstance(use, dict):
        return None
    rid = _text(payload.get("response_id"))
    ident = rid or f"{known.get('conversation') or 'conversation'}@{offset}"
    counts = {"input_tokens": _whole(use.get("input_tokens")), "output_tokens": _whole(use.get("output_tokens")),
              "cache_read_input_tokens": _whole(use.get("cached_input_tokens")),
              "reasoning_tokens": _whole(use.get("reasoning_output_tokens"))}
    if not any(counts.values()):
        return None
    ts = _text(row.get("timestamp"), 64)
    return dict(counts, key=_key("codex", ident), request_id=rid or ident, model=known.get("model"), ts=ts,
                epoch=_epoch(ts))


def codex_payload(attribution, reply):
    """`codex.session.turn`'s payload for one call, in the daemon's own field names."""
    return dict(attribution, request_id=reply["request_id"], model=reply["model"],
                input_tokens=reply["input_tokens"], output_tokens=reply["output_tokens"],
                cache_read_input_tokens=reply["cache_read_input_tokens"], reasoning_tokens=reply["reasoning_tokens"],
                ts=reply["ts"])


def _send(record, event_type, payload):
    """One row posted to the paired organisation: (whether Proofpane took it, what it answered where it did not)."""
    body = {"device_id": record["device_id"], "token": record["token"], "vendor": "ade", "agent": "default",
            "event_type": event_type, "payload": payload}
    request = urllib.request.Request(record["cloud_url"].rstrip("/") + EVENT_PATH,
                                     data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as answer:
            answer.read(64 * 1024)
            status = answer.status
    except urllib.error.HTTPError as e:
        return False, f"Proofpane answered {e.code}"
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        # the reason's kind only: an error's own text can carry the address
        why = e.reason if isinstance(e, urllib.error.URLError) and isinstance(e.reason, (str, OSError)) else e
        return False, f"Proofpane did not answer ({type(why).__name__})"
    if 200 <= status < 300:
        return True, None
    return False, f"Proofpane answered {status}"


def _state(got):
    """The record's state as read, held to its shapes."""
    got = got if isinstance(got, dict) else {}
    tries = got.get("tries") if isinstance(got.get("tries"), dict) else {}
    drops = got.get("drops") if isinstance(got.get("drops"), list) else []
    waiting = got.get("waiting") if isinstance(got.get("waiting"), list) else []
    return {"posted": _whole(got.get("posted")), "dropped": _whole(got.get("dropped")),
            "tries": {k: v for k, v in tries.items() if isinstance(k, str) and _whole(v)},
            "drops": [d for d in drops if isinstance(d, dict)][-DROPS_KEPT:],
            "waiting": [w for w in waiting if isinstance(w, dict) and isinstance(w.get("key"), str)
                        and isinstance(w.get("payload"), dict) and w.get("event_type") == CLAUDE_EVENT]
            [-WAITING_MOST:]}


def _read_state(path):
    try:
        return _state(json.loads(_read_private(path, MAX_STATE).decode("utf-8") or "null"))
    except (ValueError, RecursionError):
        return _state(None)


class _Source:
    """What one session's usage is read from: its kind of transcript (claude or codex), what its rows say of it, its
    worktree, its program's folder, the time before which no reply is its own, and, for a chat, its conversations."""

    def __init__(self, kind, attribution, work, folder, born, conversations=None):
        self.kind = kind
        self.attribution = attribution
        self.work = os.path.realpath(work)
        self.folder = folder
        self.born = born
        self.conversations = conversations


class Reporter:
    """The workspace's reporter: nudged by its sessions' activity, it reads and posts on a thread of its own."""

    def __init__(self, ws, home=None):
        self.ws = ws
        # the home the pairing and this record are in, as the agent started: never a later process's change of HOME
        self.home = home or os.path.expanduser("~")
        self._cond = threading.Condition()
        self._due = {}              # session id -> (its first activity unread, when it is read), monotonic
        self._turns = []            # one-turn rows given since the last pass
        self._everyone = False      # every session read at the next pass (catch_up)
        self._thread = None
        self._closed = False
        self._pass = threading.Lock()   # one pass at a time
        self._cursors = {}          # a transcript's path -> how far it was read, its identity (and Codex's meta)
        self._logs = {}             # a chat log's path -> how far it was read, its conversations, its rate limits seen
        self._done = None           # key -> its reply's time: the record, read at the first pass while paired
        self._data = None           # the state, read with it
        self._changed = False
        self._budget = 0
        self._pruned = 0.0

    # ---- what the workspace calls ----------------------------------------------------------------------------------

    def nudge(self, session_id):
        """A session's activity: it is read QUIET seconds after its last, or LONGEST after its first. Cheap: called on
        every piece of a terminal's output."""
        now = time.monotonic()
        with self._cond:
            if self._closed:
                return
            first = self._due.get(session_id, (now, None))[0]
            self._due[session_id] = (first, min(now + QUIET, first + LONGEST))
            self._wake()

    def catch_up(self):
        """At the agent's start: every session read once (what a restart left unposted)."""
        with self._cond:
            if self._closed:
                return
            self._everyone = True
            self._wake()

    def one_turn(self, folder, account_id, model, reply_id, result, limited=False):
        """A one-turn chat's row, from the Agent SDK's result (agents.one_turn): posted at once. Never raises."""
        try:
            row = self._turn_row(folder, account_id, model, reply_id, result, limited)
        except Exception:  # noqa: BLE001  a row that cannot be made is not made; the turn goes on
            return
        if row is None:
            return
        with self._cond:
            if self._closed or len(self._turns) >= WAITING_MOST:
                return
            self._turns.append(row)
            self._wake()

    def stop(self):
        """No pass after this one (the tests' in-process workspaces)."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(TIMEOUT + 5)

    def status(self):
        """What the page shows of it (GET /proofpane's `usage`): the rows posted and dropped since the record began, the
        last drops with why, and the rows waiting for another try; read from the record's file, never the network."""
        got = _read_state(os.path.join(core.record_dir(self.home), STATE))
        return {"posted": got["posted"], "dropped": got["dropped"], "drops": got["drops"][-DROPS_SHOWN:],
                "waiting": len(got["tries"]) + len(got["waiting"])}

    # ---- the thread ------------------------------------------------------------------------------------------------

    def _wake(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="usage-report", daemon=True)
            self._thread.start()
        self._cond.notify_all()

    def _run(self):
        while True:
            with self._cond:
                while True:
                    if self._closed:
                        return
                    now = time.monotonic()
                    ready = sorted(s for s, (_, at) in self._due.items() if at <= now)
                    if ready or self._turns or self._everyone:
                        break
                    soonest = min((at for _, at in self._due.values()), default=None)
                    if soonest is not None:
                        self._cond.wait(max(0.01, soonest - now))
                    elif not self._cond.wait(IDLE) and not (self._due or self._turns or self._everyone):
                        self._thread = None         # nothing to do for a while: the next nudge starts another
                        return
                for s in ready:
                    del self._due[s]
                turns, self._turns = self._turns, []
                everyone, self._everyone = self._everyone, False
            try:
                self.report([s for s in ready if s != EVERYONE], turns, everyone)
            except Exception as e:  # noqa: BLE001  a pass that breaks is said (its kind only); the next one tries again
                print(f"usage report: {type(e).__name__}", file=sys.stderr, flush=True)

    def _later(self, sessions, after):
        """Read these sessions again `after` seconds from now, unless their activity has them read sooner."""
        at = time.monotonic() + after
        with self._cond:
            for s in sessions:
                first, due = self._due.get(s, (at, at))
                self._due[s] = (first, min(due, at))
            self._cond.notify_all()

    # ---- a pass ----------------------------------------------------------------------------------------------------

    def report(self, sessions=(), turns=(), everyone=False):
        """One pass, on whatever thread calls it: the one-turn rows waiting, then each session's new replies, each
        posted once; {"paired": False} where the workspace is not paired, when nothing is read, written or posted."""
        with self._pass:
            record = core.read(self.home)
            if record is None:
                return {"paired": False}
            self._load()
            now = time.time()
            floor = max(_epoch(record.get("paired_at")) or 0, now - KEEP)
            self._budget = MAX_BYTES
            for row in turns:
                self._keep_waiting(row, floor)
            if everyone:
                sessions = sorted(set(sessions) | set(self._every_session(floor)))
            if len(self._cursors) > MAX_KNOWN:
                self._cursors.clear()           # read again from their starts: what was posted is not posted again
            refused, again = not self._post_waiting(record, floor), []
            for n, sid in enumerate(sessions):
                if refused:
                    again += list(sessions[n:])
                    break
                got = self._report_session(record, sid, floor)
                if got == "refused":
                    refused = True
                    again += list(sessions[n:])
                    break
                if got == "more":
                    self._later([sid], QUIET)
            self._save()
            if refused:
                self._later(again + ([EVERYONE] if self._data["waiting"] else []), RETRY_AFTER)
            return {"paired": True, "posted": self._data["posted"], "dropped": self._data["dropped"]}

    def _report_session(self, record, sid, floor):
        """"ok", "refused" (a post was refused: the rest waits for the next try), or "more" (the pass's bytes ran
        out)."""
        try:
            sources = self._sources(sid)
        except Exception:  # noqa: BLE001  a session that cannot be read now (gone, its account removed) is passed over
            return "ok"
        for source in sources:
            cut = max(floor, source.born)
            for path in self._files(source):
                got = self._read_claude(record, source, path, cut) if source.kind == "claude" else \
                    self._read_codex(record, source, path, cut)
                if got != "ok":
                    return got
        return "ok"

    def _files(self, source):
        """The files a session's usage is read from (the module's docstring), at most MAX_FILES."""
        out = []
        if source.kind == "codex":
            try:
                return usage._rollouts(source.folder, source.born or None)[:MAX_FILES * 10]
            except OSError:
                return []
        names = source.conversations
        if names is None:
            folder = os.path.join(source.folder, "projects", usage.slug(source.work))
            try:
                names = sorted(n[:-len(".jsonl")] for n in os.listdir(folder) if n.endswith(".jsonl"))
            except OSError:
                names = []
        for name in names:
            out += [path for path, _ in transcripts.files(source.work, source.folder, name)]
            if len(out) >= MAX_FILES:
                break
        return out[:MAX_FILES]

    def _cursor(self, path, st, fresh):
        known = self._cursors.get(path)
        if known is None or known["ino"] != st.st_ino or st.st_size < known["offset"]:
            known = self._cursors[path] = dict(fresh, ino=st.st_ino, offset=0)
        return known

    def _read_claude(self, record, source, path, cut):
        st = _regular(path)
        if st is None or st.st_mtime < cut - 60:       # nothing written since the cut: no reply of it is posted
            return "ok"
        known = self._cursor(path, st, {})
        if st.st_size <= known["offset"]:
            return "ok"
        fh = _open(path)
        if fh is None:
            return "ok"
        seen = {}
        with fh:
            for start, raw, size in _lines(fh, known["offset"], self._budget):
                self._budget -= size
                reply = claude_reply(raw, source.work, seen)
                if reply is not None and reply["key"] not in self._done and reply["epoch"] is not None \
                        and reply["epoch"] >= cut:
                    if not self._post(record, CLAUDE_EVENT, claude_payload(source.attribution, reply), reply["key"],
                                      reply["epoch"]):
                        return "refused"                # read from this line again at the next try
                known["offset"] = start + size
        return "more" if self._budget <= 0 else "ok"

    def _read_codex(self, record, source, path, cut):
        st = _regular(path)
        if st is None:
            return "ok"
        known = self._cursor(path, st, {"cwd": None, "conversation": None, "model": None})
        fh = None
        if known["cwd"] is None:
            fh = _open(path)
            if fh is None:
                return "ok"
            first = fh.readline(usage.META_LINE)
            if not first.endswith(b"\n"):
                fh.close()
                return "ok"                             # its first line still being written
            try:
                row = json.loads(first.decode("utf-8", "replace"))
            except (ValueError, RecursionError):
                row = None
            meta = row.get("payload") if isinstance(row, dict) and row.get("type") == "session_meta" else None
            meta = meta if isinstance(meta, dict) else {}
            cwd = meta.get("cwd")
            known["cwd"] = os.path.realpath(cwd) if isinstance(cwd, str) and cwd and "\0" not in cwd else ""
            known["conversation"] = _text(meta.get("id"))
            known["model"] = _text(meta.get("model"))
            known["offset"] = len(first)
        if not known["cwd"] or not (known["cwd"] == source.work or known["cwd"].startswith(source.work + os.sep)) \
                or st.st_size <= known["offset"] or st.st_mtime < cut - 60:
            if fh is not None:
                fh.close()
            return "ok"
        fh = fh or _open(path)
        if fh is None:
            return "ok"
        with fh:
            for start, raw, size in _lines(fh, known["offset"], self._budget):
                self._budget -= size
                if raw is not None and (b'"turn_context"' in raw or b'"token_usage_record"' in raw):
                    try:
                        row = json.loads(raw.decode("utf-8", "replace"))
                    except (ValueError, RecursionError):
                        row = None
                    row = row if isinstance(row, dict) else {}
                    if row.get("type") == "turn_context" and isinstance(row.get("payload"), dict):
                        known["model"] = _text(row["payload"].get("model")) or known["model"]
                    reply = codex_reply(row, known, start)
                    if reply is not None and reply["key"] not in self._done and reply["epoch"] is not None \
                            and reply["epoch"] >= cut:
                        if not self._post(record, CODEX_EVENT, codex_payload(source.attribution, reply),
                                          reply["key"], reply["epoch"]):
                            return "refused"
                known["offset"] = start + size
        return "more" if self._budget <= 0 else "ok"

    # ---- one post, and the record ----------------------------------------------------------------------------------

    def _post(self, record, event_type, payload, key, epoch):
        """Posted, or dropped at its last try: True (handled, never posted again); refused for now: False."""
        ok, why = _send(record, event_type, payload)
        data = self._data
        if ok:
            data["tries"].pop(key, None)
            data["posted"] += 1
            self._mark(key, epoch)
            self._changed = True
            return True
        tries = data["tries"].get(key, 0) + 1
        self._changed = True
        if tries < TRIES:
            data["tries"][key] = tries
            return False
        data["tries"].pop(key, None)
        data["dropped"] += 1
        data["drops"] = (data["drops"] + [{"at": int(time.time()), "event_type": event_type,
                                           "request_id": payload.get("request_id"),
                                           "session_id": payload.get("session_id"), "ade_kind": payload.get("ade_kind"),
                                           "tries": tries, "why": why}])[-DROPS_KEPT:]
        self._mark(key, epoch, dropped=True)
        return True

    def _paths(self):
        folder = core.record_dir(self.home)
        return folder, os.path.join(folder, SENT), os.path.join(folder, STATE)

    def _load(self):
        """The record, read once while paired: the keys of the replies posted or dropped within KEEP, and the state."""
        if self._done is not None:
            if time.time() - self._pruned > 86400:
                self._prune()
            return
        _, sent, state = self._paths()
        done, lines = {}, 0
        horizon = time.time() - KEEP
        for line in _read_private(sent, MAX_SENT).split(b"\n"):
            parts = line.split()
            if len(parts) < 2 or not parts[0].isdigit() or not _KEY.fullmatch(parts[1]):
                continue
            lines += 1
            if int(parts[0]) >= horizon:
                done[parts[1].decode("ascii")] = int(parts[0])
        self._done = done
        self._data = _read_state(state)
        self._pruned = time.time()
        if lines > 2 * len(done) + 1000:
            self._rewrite_sent()

    def _prune(self):
        horizon = time.time() - KEEP
        self._done = {k: at for k, at in self._done.items() if at >= horizon}
        self._pruned = time.time()
        self._rewrite_sent()

    def _rewrite_sent(self):
        """The log written anew with the keys kept (made new, never through a link)."""
        folder, sent, _ = self._paths()
        text = "".join(f"{at} {key}\n" for key, at in self._done.items())
        self._write_new(sent, text.encode("ascii"))

    def _write_new(self, path, data):
        folder = os.path.dirname(path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def _mark(self, key, epoch, dropped=False):
        """A reply posted, or dropped, kept in the log at once: a restart after it never posts it again."""
        at = int(epoch) if isinstance(epoch, (int, float)) else int(time.time())
        self._done[key] = at
        _, sent, _ = self._paths()
        try:
            fd = os.open(sent, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW, 0o600)
        except OSError:
            return                                  # kept in memory for this agent's life
        with os.fdopen(fd, "ab") as fh:
            fh.write(f"{at} {key}{' d' if dropped else ''}\n".encode("ascii"))

    def _save(self):
        if not self._changed:
            return
        _, _, state = self._paths()
        try:
            self._write_new(state, json.dumps(self._data, separators=(",", ":")).encode("utf-8"))
            self._changed = False
        except OSError:
            pass                                    # kept in memory, written at the next pass

    # ---- one-turn chats --------------------------------------------------------------------------------------------

    def _turn_row(self, folder, account_id, model, reply_id, result, limited):
        """A one-turn chat's waiting row ({key, epoch, event_type, payload, look}), or None where its result names no
        tokens. The request id is looked up in its transcript by the pass, where the turn left one (look)."""
        use = getattr(result, "usage", None)
        use = use if isinstance(use, dict) else {}
        counts = {f: _whole(use.get(f)) for f in CLAUDE_FIELDS}
        session = _text(getattr(result, "session_id", None))
        rid = _text(reply_id) or session
        if not rid or not any(counts.values()):
            return None
        account = account_id if isinstance(account_id, str) and _ID.fullmatch(account_id) else None
        attribution = {"session_id": session, "ade_kind": "one_turn", "client": "claude_code",
                       "claude_account": account or accounting.DEFAULT,
                       "ade_sign_in": "subscription" if limited else "unknown"}
        attribution.update(self._judged(folder))
        estimate = getattr(result, "total_cost_usd", None)
        if isinstance(estimate, (int, float)) and not isinstance(estimate, bool) and math.isfinite(estimate) \
                and estimate >= 0:
            attribution["ade_cost_est_usd"] = round(float(estimate), 6)     # the SDK's own estimate of the turn
        payload = claude_payload(attribution, dict(counts, request_id=rid, model=_text(model), ts=_now_iso()))
        claude_dir = os.path.join(self.ws.accounts.dir, account, "claude") if account else \
            os.path.join(self.home, ".claude")
        return {"key": _key("turn", rid), "epoch": int(time.time()), "event_type": CLAUDE_EVENT, "payload": payload,
                "look": [claude_dir, os.path.realpath(folder), session, _text(reply_id)]}

    def _judged(self, folder):
        """The agent and run a one-turn chat's folder belongs to (a judge's, a critic's, a pair's: agents/<agent>/runs/
        <run>/...), or nothing."""
        agents = getattr(self.ws, "agents", None)
        root = getattr(agents, "root", None)
        if not root:
            return {}
        parts = os.path.relpath(os.path.realpath(folder), os.path.realpath(root)).split(os.sep)
        if len(parts) >= 3 and _ID.fullmatch(parts[0]) and parts[1] == "runs" and _ID.fullmatch(parts[2]):
            return {"ade_agent_id": parts[0], "ade_run_id": parts[2]}
        return {}

    def _keep_waiting(self, row, floor):
        """A one-turn row kept in the record until it is posted (or dropped), its request id taken from its transcript
        where the turn left one; none from before the pairing."""
        look = row.pop("look", None)
        if row["epoch"] < floor:
            return
        found = self._transcript_request(*look) if isinstance(look, list) and len(look) == 4 else None
        if found:
            row["payload"]["request_id"] = found
            row["payload"]["records"][0]["request_id"] = found
        if row["key"] in self._done or any(w["key"] == row["key"] for w in self._data["waiting"]):
            return
        self._data["waiting"] = (self._data["waiting"] + [row])[-WAITING_MOST:]
        self._changed = True

    @staticmethod
    def _transcript_request(claude_dir, folder, session, reply_id):
        """The request id of the reply `reply_id` in the turn's own transcript, where it left one (a judge's)."""
        if not session or not reply_id or not _CONVERSATION.fullmatch(session):
            return None
        fh = _open(os.path.join(claude_dir, "projects", usage.slug(folder), session + ".jsonl"))
        if fh is None:
            return None
        with fh:
            for _, raw, _ in _lines(fh, 0, 4 * 1024 * 1024):
                if raw is None or reply_id.encode("utf-8") not in raw:
                    continue
                try:
                    row = json.loads(raw.decode("utf-8", "replace"))
                except (ValueError, RecursionError):
                    continue
                message = row.get("message") if isinstance(row, dict) else None
                if isinstance(message, dict) and message.get("id") == reply_id and _text(row.get("requestId")):
                    return _text(row["requestId"])
        return None

    def _post_waiting(self, record, floor):
        """The one-turn rows waiting, oldest first: True when none is left waiting on a refusal. A row from before the
        pairing (one kept while another pairing held) is let go unposted."""
        for row in list(self._data["waiting"]):
            epoch = row.get("epoch")
            if row["key"] not in self._done and isinstance(epoch, int) and epoch >= floor and \
                    not self._post(record, row["event_type"], row["payload"], row["key"], epoch):
                return False
            self._data["waiting"] = [w for w in self._data["waiting"] if w["key"] != row["key"]]
            self._changed = True
        return True

    # ---- whose a session is ----------------------------------------------------------------------------------------

    def _every_session(self, floor):
        """Every session and run a pass at the start reads: the workspace's sessions, and the runs not ended before the
        floor."""
        out = []
        try:
            out += [n for n in os.listdir(self.ws.sessions_dir) if _ID.fullmatch(n)]
        except OSError:
            pass
        agents = getattr(self.ws, "agents", None)
        meta = dict(getattr(agents, "_meta", None) or {})
        for run_id in list(getattr(agents, "_index", None) or {}):
            ended = (meta.get(run_id) or {}).get("ended_at")
            if isinstance(ended, int) and not isinstance(ended, bool) and ended < floor:
                continue
            out.append(run_id)
        return out

    def _claude_folder(self, record):
        """(the Claude Code folder a session's program keeps its transcripts in, its account as a row names it)."""
        account = self.ws._session_account(record)
        if account is not None:
            return self.ws.accounts.folder(account), account["id"]
        return os.path.join(self.home, ".claude"), accounting.DEFAULT

    def _chat_log(self, path):
        """(the conversations a chat's log names, whether it saw a rate-limit event), read on from where the last pass
        stopped."""
        st = _regular(path)
        if st is None:
            return [], False
        known = self._logs.get(path)
        if known is None or known["ino"] != st.st_ino or st.st_size < known["offset"]:
            known = self._logs[path] = {"ino": st.st_ino, "offset": 0, "conversations": [], "limited": False}
        if st.st_size > known["offset"]:
            fh = _open(path)
            if fh is not None:
                with fh:
                    for start, raw, size in _lines(fh, known["offset"], MAX_LOG):
                        if raw is not None and (b'"session"' in raw or b'"rate_limit"' in raw):
                            try:
                                ev = json.loads(raw.decode("utf-8", "replace"))
                            except (ValueError, RecursionError):
                                ev = None
                            ev = ev if isinstance(ev, dict) else {}
                            sdk = ev.get("sdk_session")
                            if ev.get("kind") == "session" and isinstance(sdk, str) and _CONVERSATION.fullmatch(sdk) \
                                    and sdk not in known["conversations"]:
                                known["conversations"].append(sdk)
                            if ev.get("kind") == "rate_limit":
                                known["limited"] = True
                        known["offset"] = start + size
        return list(known["conversations"]), known["limited"]

    def _sources(self, sid):
        """What a session's usage is read from, and what its rows say of it (the module's docstring); [] for a session
        of another program."""
        ws = self.ws
        agents = getattr(ws, "agents", None)
        if agents is not None and agents.is_run(sid):
            return self._run_sources(sid)
        record = ws._read(sid)
        program = record.get("program")
        if program not in ("claude", "codex", "chat"):
            return []
        work = ws._work(sid)
        born = _whole(record.get("created_at"))
        controller = getattr(ws, "controller", None)
        if program == "codex":
            account = ws._session_account(record)
            folder = ws.accounts.folder(account) if account is not None else \
                (os.environ.get("CODEX_HOME") or os.path.join(self.home, ".codex"))
            limits = usage.codex_limits(folder, since=born or None)
            attribution = {"session_id": sid, "ade_kind": "terminal", "ade_started_by": "person",
                           "ade_sign_in": "subscription" if limits else "unknown", "client": "codex"}
            return [_Source("codex", attribution, work, folder, born)]
        folder, account = self._claude_folder(record)
        attribution = {"session_id": sid, "client": "claude_code", "claude_account": account}
        if program == "claude":
            attribution["ade_kind"] = "terminal"
            attribution["ade_started_by"] = "controller" if controller is not None and controller.started(sid) \
                else "person"
            provider = record.get("model_provider")
            if provider in providers.CATALOG:
                attribution.update(ade_sign_in="provider", ade_provider=provider, claude_account=accounting.DEFAULT)
            else:
                windows = statusline.read(ws._dir(sid)) or {}
                attribution["ade_sign_in"] = "subscription" if ("five_hour" in windows or "seven_day" in windows) \
                    else "unknown"
            return [_Source("claude", attribution, work, folder, born)]
        if controller is not None and controller.is_attempt(sid):
            attribution.update(ade_kind="controller_attempt", ade_started_by="controller")
        elif controller is not None and controller.is_chat(sid):
            attribution.update(ade_kind="chat", ade_started_by="controller")
        else:
            attribution.update(ade_kind="chat", ade_started_by="person")
        conversations, limited = self._chat_log(os.path.join(ws._dir(sid), "chat.jsonl"))
        attribution["ade_sign_in"] = "subscription" if limited else "unknown"
        return [_Source("claude", attribution, work, folder, born, conversations)]

    def _run_sources(self, run_id):
        """An agent's run's or a task's attempt's: its record as the workspace signed it (agents.read), its own
        conversations, its agent's or its task's worktree."""
        ws = self.ws
        agents = ws.agents
        record = agents.read(run_id)
        run = record.get("run") if isinstance(record.get("run"), dict) else {}
        trigger = run.get("trigger") if isinstance(run.get("trigger"), dict) else {}
        kind = trigger.get("kind")
        attribution = {"session_id": run_id, "client": "claude_code"}
        agent_id = record.get("agent_id")
        if isinstance(agent_id, str) and _ID.fullmatch(agent_id):
            attribution["ade_agent_id"] = agent_id
        attribution["ade_run_id"] = run_id
        conversations, limited = self._chat_log(os.path.join(agents.run_dir(run_id), "chat.jsonl"))
        sign_in = "subscription" if limited else "unknown"
        if kind == "task":
            attribution["ade_kind"] = "task_attempt"
            task_id = trigger.get("id")
            if isinstance(task_id, str) and shapes.TASK_ID.fullmatch(task_id):
                attribution["ade_task_id"] = task_id            # the task's own id, never the run's
            if self._chosen_by(run_id, record) == "owner":
                attribution["ade_started_by"] = "person"
            backend = run.get("backend") if isinstance(run.get("backend"), dict) else {}
            provider = backend.get("provider")
            if backend.get("kind") == "key" and provider in shapes.KEY_PROVIDERS:
                folder = os.path.join(ws.accounts.dir, "keys", provider, "claude")     # task_runs.key_folder's
                attribution["ade_sign_in"] = "own_key" if provider == "anthropic" else "provider"
                if provider != "anthropic":
                    attribution["ade_provider"] = provider
            else:
                folder, attribution["claude_account"] = self._claude_folder(record)
                attribution["ade_sign_in"] = sign_in
        else:
            attribution["ade_kind"] = "improve_run" if kind in LOOP_KINDS else "agent_run"
            if kind in STARTED_BY:
                attribution["ade_started_by"] = STARTED_BY[kind]
            source = trigger.get("id")
            if isinstance(source, str) and _ID.fullmatch(source):
                attribution["ade_trigger_id"] = source
            folder, attribution["claude_account"] = self._claude_folder(record)
            attribution["ade_sign_in"] = sign_in
        born = _whole(record.get("created_at")) or _whole(run.get("queued_at"))
        return [_Source("claude", attribution, agents.work(run_id), folder, born, conversations)]

    def _chosen_by(self, run_id, record):
        """Who queued a task's attempt, as its message kept beside the run says (task_runs.TASK_FILE), or None."""
        try:
            got = json.loads(self.ws.agents._kept_file(run_id, "task.json", record) or b"null")
        except Exception:  # noqa: BLE001  a message that cannot be read says nothing of it
            return None
        return got.get("chosen_by") if isinstance(got, dict) else None
