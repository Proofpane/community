"""Proofpane's budget check, asked by the workspace agent before it starts work, for the Proofpane organisation the
workspace is paired with: whether the person, their department and the organisation have room for the kind of spend
a start uses, and, where they have none, the product's own words for it.

**While paired, and only then.** Each check reads the pairing first (core_plugin.read:
`~/.proofpane/proofpane-core/config.json`, its `cloud_url`, `device_id` and `token`); with none, it asks nothing, sends
nothing, and the start goes on as it did before this module.

**What leaves the machine, to where, when.** One POST to `<cloud_url>/api/v1/devices/budget-check`, the product's own
route for a paired device, before each start the workspace asks it for (below), and nothing else: its body is
`{"device_id", "token"}` alone, the pairing's, never a session's id, its task, its repository, a prompt, an account's
name or a key. The token goes only in that body, as the plugin's rows and the usage rows carry it (usage_report.py). A
redirect is refused, never followed. The start waits for the answer WAIT seconds at most in all: the request runs on a
thread of its own, which the start leaves behind once WAIT has passed, so a Proofpane that answers slowly, a byte at a
time, or never, holds a start no longer. That thread reads the answer's body as it comes, and gives up one not all come
WAIT seconds after it asked, closing its connection; a read under way then ends within WAIT seconds more, urllib's
timeout for each (_body), so a body sent a byte at a time keeps neither the thread nor the connection for long. The
answer's headers, and the lines that frame an answer sent in chunks, are not bounded so, since only the paired Proofpane
could send them a byte at a time. Each start asks anew, and no answer is kept between starts: what a start says of its
answer (its note, the words of a refusal) is written where that start's own words go (its log, its run's record, its
attempt's note; a terminal's, its answer alone), and nothing else of the answer is. The agent's memory keeps a start's
note until that start's answer and its log have said it (Workspace.keep_budget_note), and an agent's run's room until
the run ends.

**What it gives the start** (Word): for the one kind of spend the start uses, whether it may start (`allowed`), the room
left where a blocking limit counts that kind (`room_usd`, a number of dollars; None where none counts it), and the
words for a start it refuses (`message`, which the start says verbatim). Whether a start is refused is read by the rule
the plugin's hook reads a prompt's by (core_plugin/hooks/proofpane_hook.py, prompt_check, _kind_answer): an answer is
the route's only as a JSON object whose `checked` is true or false and whose `kinds` holds the start's kind as an object
whose `allowed` is true or false. Where `allowed` is false, the start is refused whatever else the answer holds: in the
kind's `message` where that is text with more than spaces in it, as Proofpane gave it, and otherwise in words of the
workspace's own that name the kind (NO_WORDS); its room is the kind's `room_usd` where that is in its shape below, and
otherwise None. Where `allowed` is true, the workspace reads more of the answer than the hook does, and the rest must
be in the route's shape too: `unread` a list of names, the kind's `room_usd` a number at least 0 that a float holds
(not an integer too large for one) or null, and its `message` text or null; an answer with room that is not is one the
check could not be made from (below). Both let such a start go on: the hook, which reads only whether its prompt is
refused and whether every budget was read, says no more than that. The answer's other fields (`organisation`,
`person_id`, `department_id`, the kind's `refused_by`, the other kinds) are not read.

**When Proofpane does not answer** (no answer within WAIT seconds, a connection that fails, an answer other than 2xx, a
redirect, or one that is not the shape above), the start goes on, and says the check could not be made and why
(`note`), as the product's own callers let a call through when its check cannot read. An answer that says
it could not read every budget (`checked` false) still refuses where its kind says `allowed: false`; otherwise the
start goes on, and says what Proofpane could not read (its `unread`).

**The starts asked, and the kind of spend of each**, from what the workspace knows at that start:
- a session of a program that runs an agent (`claude`, `codex`, `gemini`, `opencode`, `grok`, `qwen`, `kimi`, `mcode`,
  or the person's own agent), at its first start (Workspace.prepare) and at each start again
  (Workspace.claude_floor_for), and a chat at each start of its claude (Workspace._chat_start): `subscription`, but a
  claude terminal on another provider's model with the person's own key for it (providers.CATALOG), which is
  `own_key`. A shell's session and a sign-in's, which spend nothing, are not asked.
- an agent's run (agents._start), on an account: `subscription`.
- a task's attempt (task_runs.start): `own_key` on a key, its own Anthropic key (providers.KEY_ONLY) or another
  provider's (task_shapes.KEY_PROVIDERS); `subscription` on an account.
- an attempt of a requirement's manager (controller.keep_attempts), on the controller's account: `subscription`, asked
  for the attempt that starts, and asked again under the start lock where the word in hand was for no attempt or for
  another, or was asked longer ago than controller.ATTEMPT_WORD_SECONDS; and the claude terminals the controller and
  the attempts start, as any claude terminal's start.
A start on an account is taken as a subscription's because the workspace cannot tell at that start what the account
is billed as: an account is a sign-in the person made in Claude Code or in Codex (accounts.py), for which the workspace
holds no key; what it is billed as is the vendor's to know, and the workspace learns only later that it is a
subscriber's, from the account's windows that its replies carry (statusline.py, usage.codex_limits), never at the start.
The usage rows say `unknown` until those windows are seen (usage_report.py); the check, which must name one of the
product's three kinds, names `subscription`. No start is `gateway`: no setting of the workspace's points a task's
attempt, or anything else, at the organisation's egress gateway (an attempt's environment refuses
`ANTHROPIC_BASE_URL`, task_shapes.REFUSED_ENV, and a provider's key goes to the vendor's own address, providers.py), so
the kind waits for that setting.

What it does not do: stop a session already running (a session's own model calls never pass through the workspace,
and only a start is asked about); cap a task's attempt or a manager's attempt while it runs (only an agent's run takes
the room, as its Agent SDK budget: agents.py, chat_options); count, price or report usage (usage_report.py); refuse a
start Proofpane did not refuse. Standard library only; the app never imports it.
"""

import http.client
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request

from . import core_plugin as core, providers

CHECK_PATH = "/api/v1/devices/budget-check"
WAIT = 5.0                              # seconds a start waits for Proofpane's answer, at most
USER_AGENT = "proofpane-workspace-budget/1"
MAX_ANSWER = 64 * 1024                  # an answer longer than this is not one the route gives
SUBSCRIPTION, OWN_KEY, GATEWAY = KINDS = ("subscription", "own_key", "gateway")     # as the product names them
NOT_MADE = "Proofpane's budget check could not be made: {}. This start went on without it."
NOT_A_CHECK = "Proofpane's answer is not a budget check's"
# a refusal's words where Proofpane gave none, as the plugin's hook words one (its prompt_check), for a start
NO_WORDS = "Proofpane's budget check refused this start's kind of spend ({}), and gave no words for it."
UNREAD = "Proofpane could not read {}, so this start was not checked against {}."
# what the route names in `unread`, in words, and whether the words are of more than one thing
UNREAD_WORDS = {"scopes": ("the person's and the department's budgets", True),
                "organisation_cap": ("the organisation's monthly cap", False)}
_NAME = re.compile(r"[a-z][a-z_]{0,39}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, never followed: the device's token goes in the body, which a redirect would carry on."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "a redirect, not followed", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


class Word:
    """Proofpane's word on one start, for the kind of spend it uses (the module's docstring): `allowed`, `room_usd`,
    `message` (the words of a refusal: the product's, or NO_WORDS where it gave none; always text with more than
    spaces in it), `note` (what the start says where the check could not be made or not every budget was read), and
    `paired` (whether the workspace is paired, which is when anything is asked)."""

    __slots__ = ("kind", "paired", "allowed", "room_usd", "message", "note")

    def __init__(self, kind, paired=False, allowed=True, room_usd=None, message=None, note=None):
        self.kind, self.paired, self.allowed = kind, paired, allowed
        self.room_usd, self.message, self.note = room_usd, message, note

    def __repr__(self):
        return (f"Word(kind={self.kind!r}, paired={self.paired}, allowed={self.allowed}, room_usd={self.room_usd!r}, "
                f"message={self.message!r}, note={self.note!r})")


def session_kind(program, model_provider=None):
    """A session's start's kind of spend (the module's docstring): `own_key` for a claude terminal on another
    provider's model, `subscription` for any other."""
    return OWN_KEY if program == "claude" and model_provider in providers.CATALOG else SUBSCRIPTION


def attempt_kind(backend):
    """A task's attempt's kind of spend: `own_key` on a key (its message's backend `{"kind": "key", "provider"}`),
    `subscription` on an account."""
    return OWN_KEY if isinstance(backend, dict) and backend.get("kind") == "key" else SUBSCRIPTION


def check(home, kind):
    """Proofpane's word on a start that spends `kind` (one of KINDS), for the workspace whose pairing is kept in `home`:
    blocking, WAIT seconds at most, so a start on the event loop asks it in a thread. Never raises but on a kind that is
    not one of KINDS: an answer whose reading breaks is read as one not of the route's shape."""
    if kind not in KINDS:
        raise ValueError(f"A kind of spend is one of {', '.join(KINDS)}.")
    record = core.read(home)
    if record is None:
        return Word(kind)                       # not paired: nothing asked, the start goes on
    answer, why = _ask(record)
    if answer is None:
        return Word(kind, paired=True, note=NOT_MADE.format(why))
    try:
        got = _read(answer, kind)
        if got is not None:
            allowed, room, message, checked, unread = got
            if not allowed:
                return Word(kind, paired=True, allowed=False, room_usd=room, message=message)
            return Word(kind, paired=True, room_usd=room, note=None if checked else _unread_note(unread))
    except Exception:  # noqa: BLE001  an answer whose reading breaks is not one of the route's shape
        pass
    return Word(kind, paired=True, note=NOT_MADE.format(NOT_A_CHECK))


def _ask(record):
    """(Proofpane's answer, parsed, None), or (None, why there is none): the request on a thread of its own, waited
    for WAIT seconds in all."""
    got = []
    thread = threading.Thread(target=lambda: got.append(_exchange(record)), name="budget-check", daemon=True)
    thread.start()
    thread.join(WAIT)
    if not got:
        return None, f"no answer came in {WAIT:g} seconds"
    return got[0]


def _exchange(record):
    """One POST of the check and its answer: (the answer, parsed, None), or (None, why it is not one). Never raises.
    Its answer's body is read against WAIT seconds counted from its start, as _ask waits for the whole (_body):
    urllib's timeout is each socket operation's, not the whole request's, so a body that came a byte at a time would
    otherwise keep this thread and its connection for as long as its bytes kept coming. One not all come by then is
    given up, and its connection closed with the answer."""
    deadline = time.monotonic() + WAIT
    body = {"device_id": record["device_id"], "token": record["token"]}
    request = urllib.request.Request(record["cloud_url"].rstrip("/") + CHECK_PATH,
                                     data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with _OPENER.open(request, timeout=WAIT) as answer:     # closed as the block ends, a body given up or not
            raw = _body(answer, deadline)
            status = answer.status
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            return None, "Proofpane answered with a redirect, which is not followed"
        return None, f"Proofpane answered {e.code}"
    except http.client.HTTPException as e:
        return None, f"Proofpane's answer could not be read ({type(e).__name__})"
    except (urllib.error.URLError, OSError, ValueError) as e:
        # the reason's kind only: an error's own text can carry the address
        why = e.reason if isinstance(e, urllib.error.URLError) and isinstance(e.reason, (str, OSError)) else e
        if isinstance(why, TimeoutError):
            return None, f"no answer came in {WAIT:g} seconds"
        return None, f"Proofpane could not be reached ({type(why).__name__})"
    except Exception as e:  # noqa: BLE001  whatever else the request meets is no answer
        return None, f"Proofpane could not be reached ({type(e).__name__})"
    if raw is None:
        return None, f"no answer came in {WAIT:g} seconds"
    if not 200 <= status < 300:
        return None, f"Proofpane answered {status}"
    if len(raw) > MAX_ANSWER:
        return None, NOT_A_CHECK
    try:
        return json.loads(raw.decode("utf-8")), None
    except (ValueError, RecursionError):
        return None, NOT_A_CHECK


def _body(answer, deadline):
    """The answer's body, MAX_ANSWER + 1 bytes of it at most, read a piece at a time as each comes (read1), or None
    where it has not all come by `deadline`: no read is begun after it, and a read under way when it passes waits
    WAIT seconds at most, the socket's own timeout. The lines that frame an answer sent in chunks are read whole
    within a read, by http.client, and are not bounded so."""
    pieces, size = [], 0
    while size <= MAX_ANSWER and not answer.isclosed():
        if time.monotonic() >= deadline:
            return None
        piece = answer.read1(MAX_ANSWER + 1 - size)
        if not piece:
            break
        pieces.append(piece)
        size += len(piece)
    return b"".join(pieces)


def _read(answer, kind):
    """(allowed, room_usd, message, checked, unread) for `kind`, from an answer of the route's shape (the module's
    docstring), or None for one that is not. A refusal stands whatever else the answer holds, as the plugin's hook
    reads one: its words the kind's where it gave some and NO_WORDS where it gave none, its room None where the kind's
    is off its shape, and its `unread` not read (None)."""
    if not isinstance(answer, dict):
        return None
    checked, kinds = answer.get("checked"), answer.get("kinds")
    if not isinstance(checked, bool) or not isinstance(kinds, dict):
        return None
    mine = kinds.get(kind)
    if not isinstance(mine, dict) or not isinstance(mine.get("allowed"), bool):
        return None
    shaped, room = _room(mine.get("room_usd"))
    message = mine.get("message")
    if not mine["allowed"]:
        words = message if isinstance(message, str) and message.strip() else NO_WORDS.format(kind)
        return False, room, words, checked, None
    unread = answer.get("unread")
    if not shaped or not (message is None or isinstance(message, str)) or not isinstance(unread, list) \
            or not all(isinstance(u, str) for u in unread):
        return None
    return True, room, message, checked, unread


def _room(value):
    """A kind's `room_usd` as read: (True, None) for null; (True, its dollars, a float) for a number at least 0 that a
    float holds; and (False, None) for anything else, an infinite float and an integer too large for a float among
    them."""
    if value is None:
        return True, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, None
    try:
        dollars = float(value)
    except OverflowError:                       # an integer too large for a float
        return False, None
    return (True, dollars) if math.isfinite(dollars) and dollars >= 0 else (False, None)


def _unread_note(unread):
    """What the start says of a check that could not read every budget: what Proofpane named in `unread`, in words
    (UNREAD_WORDS), a name it has no words for as the name itself."""
    named = [UNREAD_WORDS.get(u, (u.replace("_", " "), False)) for u in dict.fromkeys(unread) if _NAME.fullmatch(u)]
    if not named:
        return UNREAD.format("every budget", "them")
    many = len(named) > 1 or named[0][1]
    return UNREAD.format(" and ".join(words for words, _ in named), "them" if many else "it")
