"""Proofpane's own Claude Code hook, as the proofpane-core plugin gives it to a session the workspace starts: what the
session does reaches the owner's Proofpane organisation, the one the workspace was paired with, as a Claude Code on a
person's own computer reaches it through the Proofpane daemon's hook (which this client follows; it is not that hook,
which the workspace's machine does not hold, and it imports nothing of the daemon's). Standard library only, Python 3.8
or later, one file, so that it runs with whatever python3 the session's PATH gives.

What it does, by the event Claude Code gives it on its standard input:

- PreToolUse, before every tool call, one of the two it waits for (the plugin's hooks.json gives it 90 seconds, and it
  answers within BUDGET and ROW_WAIT of them whatever Proofpane does). A command that matches the daemon's own list of
  dangerous ones (`rm -rf /`, a fork bomb, `curl ... | sh`, `~/.ssh`, the keychain, and the rest), or a path field that
  does, is refused, the daemon's own words given as the reason. So is a call the workspace's own gate refuses
  (proofpane_workspace/gate.py, beside this plugin's folder, loaded and asked here as its hook is asked, under the same
  python3, never leaving a compiled file beside it; where it is not there or does not load, nothing stands in its place
  here): where the gate's hook runs, that changes nothing, and where an organisation's server-managed settings replace
  the workspace image's and the gate's hook with them, the gate's rules still hold in the sessions given this plugin,
  but for a command too long to check (below); and no such call is held, so no approval is asked in Proofpane for a call
  the gate refuses. A command that matches one of the owner's holds (the patterns kept with the pairing, none unless the
  owner set some; matched anywhere in it, ignoring case, as written or with its quotes taken out) waits for the owner's
  approval in Proofpane, as the daemon holds one in its tray: an approval request, then its answer asked for every 1.5
  seconds for up to 60. The request names the call and the session that asks (the call's id, the session's id, account
  and folder) above the command, so the approver sees it and Proofpane, which tells requests apart by their tool and
  preview, never gives one call's approval or refusal to another; a command too long for the preview is shown from a
  little before the part the hold that held it matched (with its quotes taken out, where only that matched), under a
  line giving its length and the first 16 hex digits of its sha256. Refused, expired or not answered in time, the call
  is refused, and so it is when the hold has not ended by BUDGET seconds, whatever kept it (a name that does not
  resolve, a Proofpane that does not answer). Approved, nothing is answered, and the session's own permission mode
  decides, as it would without this hook. Proofpane not reached, or refusing the device's token (revoked, paused or
  replaced by a new one there), Claude Code asks the person itself, as the daemon falls back to its native ask, the
  reason saying which; and so it does where a check breaks on a call a hold names. The checks are patterns, run in this
  process's main thread under a timer, which stops a match where no other thread could (pre_tool_use): a call whose
  checks have not ended by BUDGET seconds (a hold's pattern, or the daemon's, slow on a command built for it) is
  refused, its row saying so. A command longer than MAX_CHECKED, its path fields with it, and a call too large for this
  hook to read or not JSON, get no check and are asked about in Claude Code's own prompt: so where server-managed
  settings removed the gate's own hook, a call that long that the gate would refuse is asked about, not refused.
- It never answers "allow". In Claude Code an allow skips the permission prompt, which would let through what the
  session's own mode holds; and all the hooks of a call run side by side, the most restrictive answer winning (deny,
  then defer, then ask, then allow: hooks reference, read 2026-10-18), so the gate's refusal stands whatever this hook
  answers.
- UserPromptSubmit, before each prompt goes on, the other one it waits for: an entry of its own, which hooks.json starts
  with the argument `budget-check` (CHECK) and gives 10 seconds, asks Proofpane's budget check (the product's route at
  `<cloud>/api/v1/devices/budget-check`) for the session's kind of spend (spend_kind, below), the device's id and token
  in the body and nothing else, a redirect refused, and waits for its answer CHECK_WAIT seconds at most, in a thread of
  its own, so that neither a slow answer nor a name slow to resolve keeps the prompt longer (prompt_check). Where
  Proofpane answers that this kind of spend has no room (`allowed` false, whether or not it could read every budget),
  the prompt is held: Claude Code's decision to block, in the top-level form the daemon's own hook holds a prompt with,
  its reason the product's own words, the kind's `message`. With room, nothing is answered and the prompt goes on as it
  was. Where the check cannot be made (no answer in CHECK_WAIT seconds, Proofpane not reached, a redirect, the device
  refused, any answer but a 2xx JSON object in the check's shape, or a check that breaks here), the prompt goes on and a
  systemMessage tells the person so and why, as the product's own callers let a call through when its budget cannot be
  read; and where Proofpane could not read every budget (`checked` false) and found room in what it read, the prompt
  goes on, the systemMessage naming what it could not read. Unpaired, nothing is asked. A prompt too large for this hook
  to read, or not JSON, is checked all the same, the check needing nothing of it. The prompt's row is the other
  UserPromptSubmit entry's (below), which Claude Code does not wait for; it runs a prompt's two side by side. A chat is
  given the plugin as a terminal is, so its messages come to the same check, which no test here runs through a chat.
- A row for Proofpane's audit, `claude_code.hook.<event>` posted to `<cloud>/api/v1/mcp/event` as the daemon posts
  one, with the workspace's session and account, and the prompt's id where Claude Code gives one (which Proofpane
  does not read: it files a row under its request id alone): each prompt
  (UserPromptSubmit); each reply (Stop: the turn's own `last_assistant_message`, which Claude Code gives a Stop hook
  from 2.1.47, with the transcript's request id, model and thinking only where the transcript's last reply has its
  words, whole, spaces and line ends aside, since the transcript can lag the turn; the transcript's last turn where no
  message is given); each PreToolUse it refused, at BUDGET too, or left Claude Code to
  ask about (one the owner approved is in Proofpane's own record of the approval already); and each PostToolUse of a
  tool that is not one of QUIET_TOOLS, its input and its result with it, and each PostToolUseFailure of one, posted as
  a PostToolUse row that says the call failed, with its error. Every tool but the quiet ones gets a row, one a later
  Claude Code adds among them, where the daemon names the ones that do (a list of its own, which Claude Code's own
  tools have outgrown: Agent, Monitor, PowerShell, Workflow and Artifact are not in it). Text goes through the daemon's
  secret patterns before it leaves, its first MAX_REDACTED characters, then is cut to 4000 characters; a PreToolUse
  row is made under the same timer as the checks. The rows of UserPromptSubmit, PostToolUse,
  PostToolUseFailure and Stop are posted by async hooks, which do not hold the session while they post (a prompt's by
  the entry beside its check's, so that posting never delays it); a PreToolUse row is posted after its answer is
  decided, the hook ending at most ROW_WAIT seconds later whether Proofpane took it or not.

The session's kind of spend (spend_kind) is read from the environment Claude Code starts the hook in: `own_key` where
it carries a key of its own (ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN, not empty), an address other than Anthropic's
own API in ANTHROPIC_BASE_URL (another provider's endpoint, as providers.py pins one and the workspace puts it in the
launch environment of a session on that provider's model, or a gateway's), or a switch that sends Claude Code's calls
to Amazon Bedrock, Google Vertex AI or Microsoft Foundry (CLAUDE_CODE_USE_BEDROCK, CLAUDE_CODE_USE_VERTEX or
CLAUDE_CODE_USE_FOUNDRY, set to 1, true, yes or on); `subscription`, the person's own Claude sign-in, otherwise. What
it cannot tell from there: a key an `apiKeyHelper` in a settings file gives, which is in no environment (a session on
another provider's model reads `own_key` all the same, by its endpoint); whether Claude Code uses an ANTHROPIC_API_KEY
it finds in place of a sign-in; and whether a Claude sign-in is a subscription's or an Anthropic Console account's,
which is billed as a key is (`subscription` either way). The check's third kind, `gateway`, a task's attempt through
the organisation's egress gateway, is never asked for: a task's attempt is given no plugin
(proofpane_workspace/core_plugin.py).

The pairing it reads is the workspace's record, `~/.proofpane/proofpane-core/config.json` in the workspace user's home,
written by the workspace agent (proofpane_workspace/core_plugin.py) in the daemon's own form (cloud_url, device_id,
token), with the owner's holds beside them. Read at each event, held only while this process runs, never written. No
record, or one that does not read as one, and the hook does nothing at all: it answers nothing and sends nothing.

What it is not: a wall against the session. The session's own programs run as the same user, can read the record by a
path the workspace's gate does not recognise, and can post to Proofpane in the device's name; a repository's own
settings, or the user's own, can put another python3 first on the PATH this hook is started from, or turn the plugin
off. The hooks are cooperative, as the daemon's are on a person's own computer. It fails open but for the holds, and
for them only as far as this process runs: a check that breaks while deciding, or a Proofpane that cannot be reached,
refuses nothing a hold does not name and asks about what one does; a call it cannot read, or too long to check, is
asked about whatever it is, and one whose checks do not end in time is refused; but a python3 that cannot start this
hook, a timer it cannot set (no SIGALRM, as on Windows, where the workspace starts no session), or a fault outside its
decision, lets every call go on, a held one too, as Claude Code lets a call
past a hook that fails (`onFailure: "block"`, which would stop it, came in Claude Code 2.1.295). The budget check
before a prompt is as cooperative, and fails open too: it holds a prompt only where Proofpane answers that the
session's kind of spend has no room, and lets it go on, saying so, wherever the check cannot be made; a python3 that
cannot start the check's entry, or a Claude Code that stops it, lets the prompt go on with nothing of the check said;
and a session of another program has no such hook. A row that cannot be posted is dropped, with no local copy kept
(the daemon keeps one, and refuses a changing call whose record it cannot write; this does not).
"""

import hashlib
import http.client
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

RECORD = os.path.join(".proofpane", "proofpane-core", "config.json")       # in the workspace user's home
MAX_INPUT = 4 * 1024 * 1024         # what Claude Code gives on stdin, read at most
MAX_CHECKED = 64 * 1024             # a call's command and paths, checked against patterns at most (pre_tool_use)
MAX_REDACTED = 64 * 1024            # a text's first characters, redacted at most (redact)
MAX_RECORD = 64 * 1024
CAP = 4000                          # a text field, after redaction (as the daemon cuts it)
TIMEOUT = 6.0                       # one request to Proofpane (as long as the daemon gives one)
HOLD_WAIT = 60.0                    # a held call waits this long for the owner's answer (as the daemon's does)
HOLD_POLL = 1.5                     # and asks for it this often (as the daemon's hook does)
HOLD_TTL = 300                      # the approval request's own life in Proofpane (the daemon's hook asks the same)
PREVIEW = 2000                      # the command shown to the approver (cut where the daemon's hook cuts it)
# A PreToolUse answer is decided within BUDGET seconds whatever Proofpane does, and the hook ends at most ROW_WAIT
# seconds after: under the 90 hooks.json gives it, since Claude Code lets a call through a hook it had to stop. BUDGET
# is the bound, held by a timer over the checks and a thread over a hold's wait (pre_tool_use), not by the timeouts a
# hold's requests are given: those add up to 73.5 (its request's TIMEOUT, HOLD_WAIT, a last poll's TIMEOUT begun just
# before the end, a HOLD_POLL), which BUDGET is above so that an answer given at the last moment is taken, but urllib's
# timeout covers each socket operation, not a whole request, and nothing a name lookup takes.
BUDGET = 75.0
ROW_WAIT = 8.0
MAX_HOLDS = 20
MAX_HOLD = 200
EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")      # the four Proofpane's ingest accepts
HOOKED = EVENTS + ("PostToolUseFailure",)       # what hooks.json gives (Claude Code has it from 2.1.119 at the latest)
POSTED_AS = {"PostToolUseFailure": "PostToolUse"}   # a failed call's row, posted as a type the ingest accepts
USER_AGENT = "proofpane-core-hook/1"
GATE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "proofpane_workspace", "gate.py")    # the workspace's gate, beside this plugin's folder
# Proofpane's budget check before a prompt (prompt_check): the argument hooks.json starts the check's entry with, and
# the product's route (POST <cloud>/api/v1/devices/budget-check). A prompt waits for its answer CHECK_WAIT seconds at
# most; hooks.json gives the entry 10, so that Claude Code, which lets a prompt go on with nothing said when it has to
# stop a hook, never stops this one first.
CHECK = "budget-check"
CHECK_PATH = "/api/v1/devices/budget-check"
CHECK_WAIT = 5.0
KEY_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")      # a key of the session's own (spend_kind)
CLOUD_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
SWITCHED_ON = ("1", "true", "yes", "on")
ANTHROPIC_API = "api.anthropic.com"
# what a check that could not read every budget names (budget_check.py's `unread`), in words
UNREAD = {"scopes": "the person's and the department's budgets", "organisation_cap": "the organisation's monthly cap"}

_URL = re.compile(r"^(https://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?|http://(?:127\.0\.0\.1|localhost|\[::1\])"
                  r"(?::[0-9]{1,5})?)/?$")
_DEVICE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{16,512}$")
_SESSION = re.compile(r"^[0-9a-f]{12}$")
_ACCOUNT = re.compile(r"[/\\]agent-accounts[/\\]([0-9a-f]{12})[/\\]claude[/\\]?$")   # an added account's folder
_PRE_TOOL_USE = re.compile(rb'"hook_event_name"\s*:\s*"PreToolUse"')
_PROMPT = re.compile(rb'"hook_event_name"\s*:\s*"UserPromptSubmit"')

# The daemon's secret patterns, the same list its own hook holds, redacted before anything leaves
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.=]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
]

# The daemon's dangerous commands, the same list its own hook holds, screened in a command's text and in a call's path
# fields, never in a file's content: a document that describes a command does not run it
DANGER_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/(?:\s|$)"),
    re.compile(r"\brm\s+-rf\s+~(?:/|\s|$)"),
    re.compile(r":\(\)\s*\{"),
    re.compile(r"\bsudo\s+rm\b"),
    re.compile(r"(curl|wget)\b[^\n|]*\|\s*(sh|bash|zsh)\b"),
    re.compile(r"~/\.ssh"),
    re.compile(r"~/\.aws"),
    re.compile(r"\bsecurity\s+.*keychain", re.IGNORECASE),
    re.compile(r"login\.keychain", re.IGNORECASE),
]
DANGER = "Blocked by Proofpane: dangerous command"
GATE_SAYS = "Refused by the workspace's gate: "
PATH_FIELDS = ("file_path", "notebook_path", "path", "cwd", "project_path")

# the tools whose PostToolUse gets no row: those that only read files or search them (Read, Glob, Grep, LSP), find
# tools or MCP resources, keep the session's own task and to-do lists, or change its mode or ask the person something
# (plan mode, AskUserQuestion), from the tools reference read 2026-10-18; LS, which that page does not list, and
# TaskOutput, which it lists as removed in 2.1.277, are kept for a Claude Code that still has them (the workspace
# starts any from 2.1.139). Every other tool gets a row: a Bash command that only reads
# (its output with it), a file written, a page fetched, MCP tools, and a tool a later Claude Code adds
QUIET_TOOLS = ("Read", "Glob", "Grep", "LS", "LSP", "ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool",
               "WaitForMcpServers", "TodoWrite", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskOutput",
               "ListAgents", "CronList", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, never followed: the device's token goes in a request's body and in an approval's
    query, and a redirect would carry it to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "a redirect, not followed", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def record_path(home=None):
    return os.path.join(home if home is not None else os.path.expanduser("~"), RECORD)


def read_record(home=None):
    """The pairing, {cloud, device_id, token, holds}, or None: no file, one that cannot be read, or one whose fields
    are not what the workspace writes."""
    try:
        with open(record_path(home), "rb") as fh:
            raw = fh.read(MAX_RECORD + 1)
        if len(raw) > MAX_RECORD:
            return None
        got = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(got, dict):
        return None
    cloud, device, token = got.get("cloud_url"), got.get("device_id"), got.get("token")
    if not (isinstance(cloud, str) and _URL.match(cloud) and isinstance(device, str) and _DEVICE.match(device)
            and isinstance(token, str) and _TOKEN.match(token)):
        return None
    holds = []
    given = got.get("holds")
    for pattern in (given if isinstance(given, list) else [])[:MAX_HOLDS]:
        if isinstance(pattern, str) and 0 < len(pattern) <= MAX_HOLD:
            try:
                holds.append((pattern, re.compile(pattern, re.IGNORECASE)))
            except re.error:
                continue            # the workspace keeps none that does not compile; one that does not is skipped
    return {"cloud": cloud.rstrip("/"), "device_id": device, "token": token, "holds": holds}


def redact(text, cap=CAP):
    """`text` with each secret pattern replaced, cut to `cap` characters after: (text, how many were replaced). Only
    its first MAX_REDACTED characters are read, which keeps the daemon's patterns to seconds whatever the text (its
    token pattern, the slowest, grows with the square of a run built to slow it: 3.7 seconds on 64 KB of one under
    Python 3.11.7, 3.0 under 3.13.3) and is far more than a row keeps: a
    key that begins inside what is kept is redacted whole unless it runs past MAX_REDACTED."""
    if not isinstance(text, str):
        return text, 0
    text = text[:MAX_REDACTED]
    n = 0
    for pattern in SECRET_PATTERNS:
        text, c = pattern.subn("[REDACTED]", text)
        n += c
    return text[:cap], n


def redact_value(value):
    """A field that may be text or an object (a tool's input or its response): an object as its JSON, redacted."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (dict, list)):
        try:
            dumped = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            dumped = str(value)
        return redact(dumped)
    return value, 0


def _command_and_paths(tool_input):
    """What a call runs (its command, as text) and the paths it names, one a line; never a file's content."""
    command, paths = "", ""
    if isinstance(tool_input, dict):
        c = tool_input.get("command")
        if isinstance(c, list):
            c = " ".join(str(x) for x in c)
        if isinstance(c, str):
            command = c
        for key in PATH_FIELDS:
            v = tool_input.get(key)
            if isinstance(v, str) and v:
                paths += v + "\n"
    elif isinstance(tool_input, str):
        command = tool_input
    elif tool_input is not None:
        command = str(tool_input)
    return command, paths


def dangerous(tool_input):
    command, paths = _command_and_paths(tool_input)
    return any((command and p.search(command)) or (paths and p.search(paths)) for p in DANGER_PATTERNS)


def held_by(tool_input, holds):
    """The first of the owner's holds that the call's command matches, or None: anywhere in it, ignoring case as the
    daemon's own patterns do, in the command as written or with its quotes and backslashes taken out, as the shell reads
    its words, so that `git "push"` and `git pu''sh` are held as `git push` is. A command that builds its words some
    other way (a variable, an alias, a script) is not held; the holds are cooperative."""
    command, _ = _command_and_paths(tool_input)
    if not command:
        return None
    unquoted = re.sub(r"[\"'\\]", "", command)
    for pattern, compiled in holds:
        if compiled.search(command) or compiled.search(unquoted):
            return pattern
    return None


def gate_refusal(payload, path=None):
    """Why the workspace's own gate refuses this call, asked as its hook is asked (gate.refusal), or None: also where
    the gate is not beside this plugin, or does not load, when nothing here stands in its place."""
    try:
        import importlib.util
        # no compiled file beside the gate: it may be inside a signed app (desktop/bundle.py compiles it for the app's
        # own Python), and python3 started with -E does not read PYTHONDONTWRITEBYTECODE
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("proofpane_workspace_gate", path or GATE)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        why = module.refusal(payload)
    except Exception:  # noqa: BLE001  a gate that cannot be asked here still runs as its own hook
        return None
    return why if isinstance(why, str) and why else None


def _call(req, timeout):
    """(the HTTP status, the answer as a dict): the status None where Proofpane was not reached at all (a network
    failure, an answer that is no HTTP), the answer None for anything but a 2xx JSON object (one cut short, not JSON,
    nested too deep), so that a held call falls back to Claude Code's ask, never through."""
    status = None
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            status = r.status
            got = json.loads(r.read(256 * 1024).decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError, RecursionError, http.client.HTTPException):
        return status, None
    return status, (got if isinstance(got, dict) else None)


def _post(url, body, timeout=TIMEOUT):
    """POST `body` as JSON: (status, answer), as _call says."""
    data = json.dumps(body).encode("utf-8")
    return _call(urllib.request.Request(url, data=data, method="POST",
                                        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}),
                 timeout)


def _get(url):
    return _call(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), TIMEOUT)


def request_approval(record, title, preview):
    """An approval request in the owner's Proofpane, and its answer waited for, as the daemon's own hook does it:
    "approved", "rejected", "expired", "timeout" (Proofpane was reached and nobody answered in time), "device"
    (Proofpane refused the device's token: revoked or paused there) or "error" (it was not reached, or did not take
    the request)."""
    base = record["cloud"]
    status, got = _post(base + "/api/v1/mcp/hitl/request", {
        "device_id": record["device_id"], "token": record["token"], "vendor": "claude_code",
        "tool_name": title[:80] or "Bash", "arguments_preview": preview[:PREVIEW], "ttl_seconds": HOLD_TTL})
    if status in (401, 403):
        return "device"
    request_id = got.get("request_id") if got else None
    if not isinstance(request_id, str) or not request_id:
        return "error"
    if got.get("status") in ("approved", "rejected"):
        return got["status"]            # an answer Proofpane already had (its consume-once approvals)
    query = urllib.parse.urlencode({"device_id": record["device_id"], "token": record["token"]})
    poll_url = f"{base}/api/v1/mcp/hitl/{urllib.parse.quote(request_id, safe='')}?{query}"
    deadline = time.monotonic() + HOLD_WAIT
    reached = False
    while time.monotonic() < deadline:
        status, answer = _get(poll_url)
        if status in (401, 403):
            return "device"
        if answer is not None:
            reached = True
            if answer.get("status") in ("approved", "rejected", "expired"):
                return answer["status"]
        time.sleep(HOLD_POLL)
    return "timeout" if reached else "error"


def _answer(decision, reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def _asker(payload):
    """The first line of a held call's preview: the call (Claude Code's tool_use_id), the session that asks (the
    workspace's, else Claude Code's own id), its account and its folder."""
    own = os.environ.get("PP_SESSION_ID") or ""
    session = own if _SESSION.match(own) else str(payload.get("session_id") or "?")[:80]
    call = str(payload.get("tool_use_id") or "?")[:80]
    return f"[call {call}, session {session}, account {_account()}, in {str(payload.get('cwd') or '?')[:300]}]"


def _preview(payload, command, holds, hold=None):
    """What the approver is shown, redacted and at most PREVIEW characters: the asker's line, then the command. A
    command too long for that is shown from a little before the part the hold that held it (`hold`) matched, in the
    command as written or, where only that matched, with its quotes and backslashes taken out, then from its start,
    under a line giving its length and the first 16 hex digits of its sha256, so that the approver sees what is held,
    and Proofpane, which tells approvals apart by their preview, never takes two long commands that show the same parts
    for one. Each part is cut from the command already redacted, so no cut leaves part of a secret."""
    head = _asker(payload)
    text, _ = redact(command, cap=len(command) + 1)
    if len(head) + 1 + len(text) <= PREVIEW:
        return redact(head + "\n" + text, PREVIEW)[0]
    compiled = next((c for p, c in holds if p == hold), None)
    shown, where, at = text, "", 0
    if compiled is not None:
        found = compiled.search(text)
        if found is None:
            unquoted = re.sub(r"[\"'\\]", "", text)
            found = compiled.search(unquoted)
            if found is not None:
                shown, where = unquoted, " with its quotes taken out"
        at = found.start() if found is not None else 0
    digest = hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()[:16]
    note = (f"[a command of {len(command)} characters, sha256 {digest}...: shown from a little before the part a hold "
            f"matched{where}, then from its start]")
    return redact("\n".join((head, note, shown[max(0, at - 100):at + 800], "[...]", text)), PREVIEW)[0]


def refusal(payload):
    """A call refused outright, (answer, kind), or None: kind "danger" (one of the daemon's dangerous commands, looked
    for first) or "gate" (one the workspace's gate refuses)."""
    tool_input = payload.get("tool_input")
    if dangerous(tool_input):
        return _answer("deny", DANGER), "danger"
    why = gate_refusal(payload)
    if why is not None:
        return _answer("deny", GATE_SAYS + why), "gate"
    return None


def _broke(hold):
    """A check that broke refuses nothing, but a call one of the owner's holds names is asked about, never let
    through: (answer or None, kind)."""
    if not hold:
        return None, None
    return _answer("ask", f"Proofpane's hook could not decide this held call (hold: {hold}): approve it here "
                          "instead."), "ask"


def approval(record, hold, title, preview, approve=None):
    """A held call's answer and what happened: (answer or None, kind), kind "refused" (not approved: refused, expired or
    not answered in time), "ask" (Proofpane not reached, or refusing the device: Claude Code asks) or "approved"
    (nothing answered). Never an allow. Asked of `approve`, request_approval unless given, looked up at each call. The
    approver sees which call of which session asks (`preview`, _preview's), and Proofpane, which tells approvals apart
    by a request's tool and its preview, keeps each call's apart: no call's approval or refusal is another's."""
    approve = approve or request_approval
    outcome = approve(record, title, preview)
    if outcome == "approved":
        return None, "approved"         # the session's own permission mode decides, as without this hook
    if outcome in ("rejected", "expired", "timeout"):
        return _answer("deny", f"Proofpane: not approved ({outcome})."), "refused"
    if outcome == "device":
        return _answer("ask", f"Proofpane refused this workspace's device, revoked, paused or given a new token there "
                              f"(Devices), so this could not be held for approval (hold: {hold}): approve it here "
                              "instead. A paused device is resumed there; one revoked or given a new token is paired "
                              "again, with a new code."), "ask"
    return _answer("ask", f"Proofpane could not be reached to hold this for approval (hold: {hold}): approve it "
                          "here instead."), "ask"


def _surface():
    entry = (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "").strip()
    low = entry.lower()
    if "vscode" in low or "vs-code" in low:
        return "vscode", entry
    if low in ("cli", ""):
        return "terminal", entry
    return low[:40], entry[:80]


def _tail(path, size):
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        end = fh.tell()
        fh.seek(max(0, end - size))
        return fh.read().decode("utf-8", "replace")


def last_turn(path):
    """(the last assistant turn's reply, its thinking blocks, its request id, its model, its reply as written), read
    from the transcript's last 2 MB only, never the whole file; the first reply redacted and cut, the last neither,
    for comparing here and never sent. Each None, or 0, where it is not found."""
    reply, thinking, request_id, model = None, 0, None, None
    try:
        text = _tail(path, 2 * 1024 * 1024)
    except (OSError, TypeError, ValueError):
        return reply, thinking, request_id, model, None
    for line in text.splitlines():
        if '"assistant"' not in line:
            continue
        try:
            o = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(o, dict) or (o.get("type") or o.get("role")) != "assistant":
            continue
        r = o.get("requestId") or o.get("request_id")
        if isinstance(r, str) and r:
            request_id = r[:200]
        msg = o.get("message")
        m = msg.get("model") if isinstance(msg, dict) else None
        if isinstance(m, str) and m:
            model = m[:200]
        content = msg.get("content") if isinstance(msg, dict) else o.get("content")
        if isinstance(content, list):
            words = " ".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and isinstance(b.get("text"), str)).strip()
            blocks = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "thinking")
            if words:
                reply = words
            if blocks:
                thinking = blocks
        elif isinstance(content, str) and content:
            reply = content
    return (redact(reply)[0] if reply else None), thinking, request_id, model, reply


def _account():
    """The workspace's account the session runs on: an added account's id, from the folder Claude Code was given
    (CLAUDE_CONFIG_DIR, agent-accounts/<id>/claude), else "default"."""
    found = _ACCOUNT.search(os.environ.get("CLAUDE_CONFIG_DIR") or "")
    return found.group(1) if found else "default"


def row(payload, kind=None):
    """What Proofpane's audit is given for this event: the daemon's payload (build_payload), names, ids and redacted
    text only, with where it came from."""
    event = payload.get("hook_event_name")
    out = {"client": "claude_code", "captured_via": "hook", "via": "proofpane-core"}
    redacted = 0
    if event == "UserPromptSubmit":
        out["prompt"], redacted = redact(str(payload.get("prompt") or ""))
    elif event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        if isinstance(payload.get("tool_name"), str):
            out["tool_name"] = payload["tool_name"][:200]
        value, n = redact_value(payload.get("tool_input"))
        if value:
            out["tool_input"] = value
            redacted += n
        if event == "PostToolUse":
            value, n = redact_value(payload.get("tool_response"))
            if value:
                out["tool_response"] = value
                redacted += n
        elif event == "PostToolUseFailure":
            # a call that failed, posted as a PostToolUse row (Proofpane's ingest takes no other type) that says so
            out["tool_failed"] = True
            value, n = redact_value(payload.get("error"))
            if value:
                out["error"] = value
                redacted += n
            if payload.get("is_interrupt") is True:
                out["is_interrupt"] = True
    transcript = payload.get("transcript_path")
    reply = None
    # a Stop's own last message, where Claude Code gives it (from 2.1.47), is this turn's final text; the transcript can
    # lag the turn, so what it says of its last turn (the reply, the request id, the model, the thinking) is taken only
    # where that message is not given, or where the transcript's last reply has its words, whole, spaces and line ends
    # aside, so that a row's reply goes with another turn's request id (Proofpane files a row under it) only where two
    # turns end in the same words. The Stop's prompt_id names its prompt, and every row carries it, but no line of the
    # transcript is known to, so the words are what is compared. The thinking count is that of the transcript's last
    # turn with thinking, which can be an earlier one's, as the daemon's is
    said = payload.get("last_assistant_message") if event == "Stop" else None
    said = said if isinstance(said, str) else None
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop") and isinstance(transcript, str) \
            and transcript:
        reply, thinking, request_id, model, written = last_turn(transcript)
        same = said is None or " ".join((written or "").split()) == " ".join(said.split())
        if event == "Stop":
            out["has_transcript"] = True
            if thinking and same:
                out["thinking_blocks"] = thinking
                out["reasoning_redacted"] = True
        if request_id and same:
            out["request_id"] = request_id
        if model and same:
            out["model"] = model
    if event == "Stop":
        if said is not None:                    # this turn's own, an empty one empty, never the turn before's
            reply = redact(said)[0] if said.strip() else None
        if reply:
            out["reply"] = reply
    if redacted:
        out["dlp_redactions"] = redacted
    out["surface"], entry = _surface()
    if entry:
        out["entrypoint"] = entry
    if kind == "danger":
        out["blocked"] = True
        out["block_reason"] = DANGER
    elif kind == "gate":
        out["blocked"] = True
        out["block_reason"] = "Refused by the workspace's gate"
    elif kind == "refused":
        out["blocked"] = True
        out["block_reason"] = "Not approved in Proofpane"
        out["hitl_requested"] = True
    elif kind == "ask":
        out["hitl_requested"] = True
        out["decision"] = "ask"
    elif kind == "unread":              # a command too long to check: Claude Code's own ask, no approval requested
        out["decision"] = "ask"
        out["unchecked"] = "command longer than the hook checks"
    elif kind == "unfinished":          # checks that did not end in time: refused, no approval requested
        out["blocked"] = True
        out["block_reason"] = "Not checked in time"
    for key in ("session_id", "prompt_id", "cwd", "tool_use_id"):     # prompt_id: absent before the first prompt
        if isinstance(payload.get(key), str):
            out[key] = payload[key][:500]
    duration = payload.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
        out["duration_ms"] = int(duration)
    own = os.environ.get("PP_SESSION_ID") or ""
    if _SESSION.match(own):
        out["workspace_session"] = own          # the workspace's session, beside Claude Code's own id
    out["workspace_account"] = _account()
    return out


def wanted(event, tool_name, kind=None):
    """Whether this event gets a row: the daemon's "signal" capture, a PreToolUse only when it was refused or asked
    about (an approved one being Proofpane's own record already), and a PostToolUse or a PostToolUseFailure of any tool
    but QUIET_TOOLS."""
    if event in ("UserPromptSubmit", "Stop"):
        return True
    if event == "PreToolUse":
        return kind in ("danger", "gate", "refused", "ask", "unread", "unfinished")
    if event in ("PostToolUse", "PostToolUseFailure"):
        return bool(tool_name) and tool_name not in QUIET_TOOLS
    return False


def _body(record, event, payload, kind=None):
    """The row as Proofpane's audit takes it (`<cloud>/api/v1/mcp/event`), in the daemon's hook's form."""
    return {"device_id": record["device_id"], "token": record["token"], "vendor": "claude_code",
            "event_type": "claude_code.hook." + POSTED_AS.get(event, event), "payload": row(payload, kind)}


def send(record, event, payload, kind=None, timeout=TIMEOUT):
    """The row posted to Proofpane's audit, as the daemon's hook posts it; whether Proofpane took it."""
    return _post(record["cloud"] + "/api/v1/mcp/event", _body(record, event, payload, kind), timeout)[1] is not None


def _quietly(*call):
    try:
        call[0](*call[1:])
    except Exception:  # noqa: BLE001  a row that cannot be sent is dropped
        pass


class _Late(BaseException):
    """Work not ended in its time (_within): a BaseException, so that no `except Exception` on its way, the gate's own
    included, takes it for a check that broke and lets the call go on."""


def _within(seconds, work):
    """work()'s value, or _Late raised once `seconds` have passed. Python's matcher holds the interpreter while it
    runs, so no other thread can stop a pattern slow to match; but it looks for signals as it goes, and Python runs
    their handlers in the main thread, so an interval timer's SIGALRM stops one there. Where no timer can be set (no
    SIGALRM, as on Windows, where the workspace starts no session; or a thread other than the main one), work() runs
    with no bound."""
    on = [True]

    def late(signum, frame):
        if on[0]:
            raise _Late()

    try:
        before = signal.signal(signal.SIGALRM, late)
    except (AttributeError, ValueError):
        return work()
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, max(seconds, 0.001))
            value = work()
            on[0] = False
            return value
        finally:
            on[0] = False
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, before if before is not None else signal.SIG_DFL)


def _post_row(record, payload, kind, row_wait):
    """A PreToolUse row, made in this thread within `row_wait` seconds (its text redacted under a timer, as the checks
    are) and posted from a thread of its own in what is left of them: a row not made or not posted by then is
    dropped."""
    ends = time.monotonic() + row_wait
    try:
        body = _within(row_wait, lambda: _body(record, "PreToolUse", payload, kind))
    except (_Late, Exception):  # noqa: BLE001  a row that cannot be made in time is dropped
        return
    late = threading.Thread(target=_quietly, args=(_post, record["cloud"] + "/api/v1/mcp/event", body), daemon=True)
    late.start()
    late.join(max(0.0, ends - time.monotonic()))


def pre_tool_use(payload, record, budget=None, row_wait=None):
    """PreToolUse's answer, decided within `budget` seconds (BUDGET), then its row made and posted within `row_wait`
    more (ROW_WAIT), so that the hook ends before Claude Code's timeout, which would let the call through.

    The checks come first, in this thread: whether one of the owner's holds names the call, the daemon's dangerous
    commands, the workspace's gate, and what the approver of a held call is shown. They are patterns, run under a timer
    (_within), which stops one slow to match where no other thread could: a call whose checks have not ended by the
    budget is refused, and its row says so. A check that breaks refuses nothing, but a call one of the owner's holds
    names is then asked about, never let through. A command longer than MAX_CHECKED is asked about before any check
    runs, which keeps the daemon's patterns and the gate to seconds on what is checked: on 64 KB built to slow it, the
    daemon's pipe pattern, its slowest, took 5.2 seconds under Python 3.11.7 and 3.2 under 3.13.3, measured on a Mac,
    and the gate took 0.13 seconds on 64 KB of base64 that took it 21 seconds at 1 MB. A held call's
    approval is then asked for and waited for in a thread of its own, so that nothing Proofpane does, nor a name that
    takes long to resolve, keeps the hook past the budget: a hold not decided by then is refused, as one nobody answered
    in time is."""
    budget = BUDGET if budget is None else budget
    row_wait = ROW_WAIT if row_wait is None else row_wait
    ends = time.monotonic() + budget
    tool_input = payload.get("tool_input")
    command, paths = _command_and_paths(tool_input)
    if len(command) + len(paths) > MAX_CHECKED:
        _post_row(record, payload, "unread", row_wait)
        return _answer("ask", "This call's command is longer than Proofpane's hook checks "
                              f"({MAX_CHECKED // 1024} KB): approve it here instead.")
    state = {}

    def checks():
        # whether a hold names the call is found first, so that a check that breaks after it still asks about it
        try:
            state["held"] = held_by(tool_input, record["holds"])
        except Exception:  # noqa: BLE001
            state["held"] = None
        state["refused"] = refusal(payload)
        if state["refused"] is None and state["held"] is not None:
            # what the approver is shown, made here, since it runs the hold's pattern and the secret patterns again
            state["title"] = redact(command, PREVIEW)[0][:80]
            state["preview"] = _preview(payload, command, record["holds"], state["held"])

    try:
        _within(budget, checks)
    except _Late:
        _post_row(record, payload, "unfinished", row_wait)
        return _answer("deny", "Proofpane's hook could not finish checking this call in time.")
    except Exception:  # noqa: BLE001  a check that breaks refuses nothing, but a held call is still asked about
        state["refused"] = _broke(state.get("held"))
    if state["refused"] is not None or state["held"] is None:
        answer, kind = state["refused"] or (None, None)
    else:
        # held: the owner's approval, asked for and waited for in a thread of its own
        decided = threading.Event()

        def work():
            try:
                state["answer"] = approval(record, state["held"], state["title"], state["preview"])
            except Exception:  # noqa: BLE001  a broken approval refuses nothing, but the held call is still asked about
                state["answer"] = _broke(state["held"])
            decided.set()

        threading.Thread(target=work, daemon=True).start()
        if decided.wait(max(0.0, ends - time.monotonic())):
            answer, kind = state["answer"]
        else:                           # a hold Proofpane did not answer in time
            answer, kind = _answer("deny", "Proofpane: not approved (timeout)."), "refused"
    if wanted("PreToolUse", "", kind):
        _post_row(record, payload, kind, row_wait)
    return answer


def spend_kind(env=None):
    """The session's kind of spend, as Proofpane's budget check names it, read from the environment the hook runs in
    (the module's docstring says what that can and cannot tell): "own_key" where it carries a key of its own, an
    address other than Anthropic's own API, or a switch to a cloud provider's Claude; else "subscription"."""
    env = os.environ if env is None else env
    if any((env.get(name) or "").strip() for name in KEY_NAMES):
        return "own_key"
    if any((env.get(name) or "").strip().lower() in SWITCHED_ON for name in CLOUD_SWITCHES):
        return "own_key"
    address = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    if address:
        try:
            host = urllib.parse.urlsplit(address).hostname
        except ValueError:          # an address no URL parser reads, an IPv6 host's unclosed bracket say
            host = None
        if host != ANTHROPIC_API:
            return "own_key"
    return "subscription"


def _unchecked(why):
    """A prompt let go on with its check not made: the person is told so, and why."""
    return {"systemMessage": f"Proofpane's budget check could not be made ({why}), so this prompt goes on unchecked."}


def _kind_answer(answer, kind):
    """(allowed, checked, the kind's own answer) from a budget check's answer, or None for one not in the check's shape
    (budget_check.py): a JSON object whose `checked` is true or false and whose `kinds` names `kind` with `allowed` true
    or false. An answer read only in part holds nothing."""
    if not isinstance(answer, dict) or not isinstance(answer.get("checked"), bool):
        return None
    kinds = answer.get("kinds")
    entry = kinds.get(kind) if isinstance(kinds, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("allowed"), bool):
        return None
    return entry["allowed"], answer["checked"], entry


def _could_not_read(unread):
    """What a check that could not read every budget names, in words: each of its `unread` in UNREAD's words, a name
    the hook has none for as it came."""
    named = []
    for item in unread if isinstance(unread, list) else []:
        if isinstance(item, str) and item:
            words = UNREAD.get(item, item[:60])
            if words not in named:
                named.append(words)
    return " or ".join(named) or "all of its budgets"


def prompt_check(record, env=None):
    """UserPromptSubmit's answer from Proofpane's budget check for the session's kind of spend (spend_kind), waited for
    CHECK_WAIT seconds at most: a block, the product's own words its reason, where that kind of spend has no room
    (`allowed` false, whether or not `checked` is); where Proofpane could not read every budget and found room in what
    it read, a systemMessage naming what it could not read; with room, None, nothing to say. Where the check could not
    be made, the prompt goes on and a systemMessage says why (_unchecked). The request is made in a thread of its own,
    waited for no longer than the wait, since urllib's timeout covers each socket operation, not a whole request, and
    nothing a name lookup takes; a thread still waiting is left behind, and the process ends without it. Its socket is
    given a second past the wait: given the wait itself, it ran out with the wait, and on a loaded machine could be
    seen first, a product reached and slow then said to be not reached."""
    wait = CHECK_WAIT
    kind = spend_kind(env)
    got = {}
    done = threading.Event()

    def ask():
        try:
            got["status"], got["answer"] = _post(record["cloud"] + CHECK_PATH,
                                                 {"device_id": record["device_id"], "token": record["token"]},
                                                 wait + 1.0)
        except Exception:  # noqa: BLE001  a request that breaks is a check not made
            got["broke"] = True
        finally:
            done.set()

    threading.Thread(target=ask, daemon=True).start()
    if not done.wait(wait):
        return _unchecked(f"no answer within {wait:g} seconds")
    if got.get("broke"):
        return _unchecked("the hook broke while asking")
    status, answer = got["status"], got["answer"]
    if status is None:
        return _unchecked("Proofpane could not be reached")
    if status in (401, 403):
        return _unchecked("Proofpane refused this workspace's device: revoked, paused or given a new token there")
    if 300 <= status < 400:
        return _unchecked("Proofpane answered with a redirect, which is not followed")
    if not 200 <= status < 300:
        return _unchecked(f"Proofpane answered {status}")
    read = _kind_answer(answer, kind)
    if read is None:
        return _unchecked("Proofpane's answer could not be read")
    allowed, checked, entry = read
    if allowed is False:
        words = entry.get("message")
        return {"decision": "block", "reason": words if isinstance(words, str) and words.strip() else
                f"Proofpane's budget check refused this session's kind of spend ({kind}), and gave no words for it."}
    if checked is False:
        return {"systemMessage": f"Proofpane's budget check could not read {_could_not_read(answer.get('unread'))}, "
                                 "so this prompt goes on, checked against the rest only."}
    return None


def _prompt_check_main(raw, stdout, record):
    """The check's entry (CHECK): the budget check's answer for a prompt, written for Claude Code, and nothing for any
    other event. A prompt too large for the hook to read, or not JSON, is checked all the same, its event's name read
    from its start, where Claude Code writes it: the check needs nothing of the prompt. A check that breaks lets the
    prompt go on, and says so."""
    try:
        try:
            payload = json.loads(raw.decode("utf-8", "replace")) if raw and len(raw) <= MAX_INPUT else None
        except (ValueError, RecursionError):
            payload = None
        if isinstance(payload, dict):
            prompt = payload.get("hook_event_name") == "UserPromptSubmit"
        else:
            prompt = bool(_PROMPT.search(raw[:65536]))
        if not prompt:
            return 0
        answer = prompt_check(record)
    except Exception:  # noqa: BLE001  a check that breaks lets the prompt go on, and says so
        answer = _unchecked("the hook broke while checking")
    if answer is not None:
        stdout.write(json.dumps(answer))
        stdout.flush()
    return 0


def main(stdin=None, stdout=None, home=None, mode=None):
    """The hook, for the event Claude Code gives it on its standard input: started with CHECK (`mode`), the budget
    check of the prompt's entry that Claude Code waits for (_prompt_check_main); with no argument, every other entry."""
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout
    try:
        raw = stdin.read(MAX_INPUT + 1)         # read whole first, as Claude Code writes it, paired or not
    except OSError:
        raw = b""
    record = read_record(home)
    if record is None:
        return 0                        # not paired: nothing at all
    if mode == CHECK:
        return _prompt_check_main(raw, stdout, record)
    unread = len(raw) > MAX_INPUT
    try:
        payload = json.loads(raw.decode("utf-8", "replace")) if raw and not unread else {}
    except (ValueError, RecursionError):
        payload = None
    if (unread or payload is None) and _PRE_TOOL_USE.search(raw[:65536]):
        # a call too large to read, or not JSON, cannot be checked at all: asked about, never let through (Claude Code
        # writes the event's name before the tool's input); no row, there being nothing read to make one of
        stdout.write(json.dumps(_answer("ask", "Proofpane's hook could not read this call (too large, or not JSON), "
                                               "so it could not check it: approve it here instead.")))
        stdout.flush()
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name")
    if event not in HOOKED:
        return 0
    if event == "PreToolUse":
        answer = pre_tool_use(payload, record)
        if answer is not None:
            stdout.write(json.dumps(answer))
            stdout.flush()
        return 0
    tool_name = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
    if wanted(event, tool_name):
        try:
            send(record, event, payload)
        except Exception:  # noqa: BLE001  a row that cannot be sent is dropped
            pass
    return 0


if __name__ == "__main__":
    try:
        code = main(mode=sys.argv[1] if len(sys.argv) > 1 else None)
    except Exception:  # noqa: BLE001  never trap Claude Code
        code = 0
    sys.stdout.flush()
    os._exit(code)          # now, whatever a row's thread still waits on
