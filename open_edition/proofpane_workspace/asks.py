"""Agents that ask the person for a session: what a session's own token may do, and the asks waiting for the person.
Standard library only; held in this process's memory, so a token and an ask die with the workspace agent.

A session's program gets a token of its own when it starts (`PP_ASK_TOKEN`), and the `proofpane` command uses it.
The token names its session and grants reading the sessions on that session's own repository (each one's status and
diff), and asking for a new session there; never approving one, nor anything else of the agent's. An ask is a request
the person answers in the app: until then nothing starts. A session started from an ask may not ask in turn (no chains
of agents starting agents). Each session may have MAX_OPEN_A_SESSION asks open, the workspace MAX_OPEN; an ask not
answered in ASK_TTL seconds is gone.

Every program of the workspace runs as one user, so a program could read another's environment (on Linux, its
/proc/<pid>/environ) and ask in that session's name: who asked is the token's word, not a proof. What the person
decides on is the ask itself, shown whole: the agent asked for, the base, the task and, for Claude Code alone, the
effort asked for, which is a request: the session starts with the effort the person chooses at Start.
"""

import hashlib
import json
import re
import secrets
import threading
import time
import unicodedata

MAX_OPEN_A_SESSION = 3
MAX_OPEN = 10
ASK_TTL = 24 * 3600
MAX_TASK = 4000
# what a session may ask to start: the terminal agents (a shell, a chat or a sign-in is the person's own to start)
AGENTS = ("claude", "codex", "gemini", "opencode", "grok", "qwen", "kimi", "mcode")
# the one setting an ask may name: an effort, for a Claude Code session alone, one of those the installed
# claude offers (workspace.capabilities, what the workspace keeps of it, which while it is the floor is read again in
# the background at most once a minute, the ask refused meanwhile: workspace.ask_offered), and none while its help has
# not been read for what it takes
# (claude_cli.capabilities' `floor`, set when its efforts or its permission modes did not parse, when the claude the
# PATH gives may be one that does not take the flag; and when its version could not be read, wherever the workspace
# reads it, claude_cli.offered: a start's read of the claude it runs, and the workspace's own). CLAUDE_ONLY also
# refuses an effort
# the person chooses at Start for another agent's ask (workspace._ask_settings); the rest of the check is
# check_effort's, below, which refuses the ask's own effort when the agent asks and the person's choice at Start alike:
# at Start against what the claude that starts offers, read once the new worktree is made, in UNKNOWN_EFFORTS or naming
# that claude's levels, the Start then refused and the ask waiting again.
CLAUDE_ONLY = "Only a Claude Code session takes an effort from an ask."
UNKNOWN_EFFORTS = ("The workspace could not read its Claude Code's version, or from its help which efforts, or "
                   "which permission modes, it takes, so a session started from an ask takes no effort now: leave its "
                   "effort to Claude Code's default.")
# what follows UNKNOWN_EFFORTS when the agent's ask is refused so (check_effort): what it is checked
# against then is what the workspace keeps of its claude, which it reads again in the background, at most once a minute
# (workspace.refresh_capabilities), so the same ask may be taken a minute later. A Start reads the claude that starts,
# itself, and says UNKNOWN_EFFORTS alone
READ_AGAIN = "The workspace reads its Claude Code again at most once a minute, so you may ask for it again in a minute."
# what workspace.ask_offered gives, in place of what its claude offers, for a claude ask that names an effort while the
# workspace keeps nothing of its claude (since the workspace agent started, or since an update of Claude Code emptied
# what it kept) and a read of it is under way: the first read, made by one ask at a time or by the page, or a read in the
# background. check_effort then refuses the agent's ask at once, in BEING_READ, rather than wait for the read in a
# thread of the agent's, so that one session's asks cannot fill those threads. A Start never passes it, and never says
# those words
READING = object()
BEING_READ = ("The workspace is reading which efforts its Claude Code takes, so it cannot take an effort on an ask "
              "yet: ask for it again in a minute, or leave its effort to Claude Code's default.")
# what a later start of such a session says when it gives no effort it would otherwise give, each with the effort in
# place of {}: the claude that start runs, read at that start in the session's worktree with its environment
# (workspace._offered_by), does not list it among its levels, or its version or its help could not be read for them
# (the floor, as above) or nothing of it could be read (workspace.start_effort), as a claude updated
# or replaced since the session started may make it. A first Start does not say them: it is refused (check_effort)
NO_LONGER_OFFERED = "Claude Code no longer offers effort {}: the session started at its default."
NOT_READ_NOW = ("The workspace could not read its Claude Code's version, or from its help which efforts, or which "
                "permission modes, it takes, so the session started at Claude Code's default effort, not at {}.")
# what the same ask, asked again while the first is still being taken, is told (Asks.start_taking):
# the agent's route reads what the installed claude offers before it takes an ask, which can outlast `proofpane ask`'s
# wait, and `proofpane asks` cannot show an ask until it is taken; nor ever one the workspace refuses, so the words say
# that asking again then gives the reason (words saying only that the ask would show are ones an agent could wait on
# for ever)
BEING_TAKEN = ("The same ask from this session, for the same agent, task, base and effort, is still being taken: "
               "`proofpane asks` shows it once it is. An ask the workspace refuses is never listed there, so if it does "
               "not appear once the workspace has answered the first, asking again gives the reason.")


# what an ask's task may not hold, as the person reads it in Approvals and the new agent is given it: control
# characters (a line break and a tab are text), which typed as a first prompt could act on a terminal; format
# characters (the ones that turn text's direction, the zero-width ones, soft hyphens, byte order marks, the tag
# characters, which can carry words no one sees); private-use, unassigned and lone surrogate code points; line and
# paragraph separators; the Hangul fillers, which show as nothing; and the variation selectors but the two that choose
# how an emoji shows. Everything the person cannot see, the agent could still read.
_HIDDEN = {0x115F, 0x1160, 0x3164, 0xFFA0}
_SELECTORS = set(range(0xFE00, 0xFE0E)) | set(range(0xE0100, 0xE01F0))
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def not_text(text):
    """Whether `text` holds a character the person would not see as text (above), but a line break or a tab."""
    for c in text:
        if c in "\n\t":
            continue
        cat = unicodedata.category(c)
        if cat in ("Cc", "Cf", "Co", "Cn", "Cs", "Zl", "Zp") or ord(c) in _HIDDEN or ord(c) in _SELECTORS:
            return True
    return False


def good_base(base):
    """A base as the workspace takes one (workspace.prepare): a branch, a tag or a commit, nothing else."""
    return isinstance(base, str) and bool(_REF.match(base)) and ".." not in base and not base.endswith((".lock", "/"))


# what an agent is told when the person lets it ask (workspace.launch): Claude Code as an appended system prompt, Codex
# as its developer instructions; the other agents are told by the person, if at all (`proofpane --help` says the rest)
NOTE = ("In this workspace the person lets you use the `proofpane` command in your terminal. `proofpane sessions` "
        "lists the sessions on this repository; `proofpane status ID`, `proofpane diff ID` and `proofpane wait ID` "
        "read one of them; `proofpane ask --agent AGENT --task TEXT [--base REF]` asks the person for a new session "
        "on this repository, with one of the agents " + ", ".join(AGENTS) + ", which starts only if the person starts "
        "it (with --agent claude, --effort LEVEL asks for one of Claude Code's effort levels, which the person may "
        "change; add --wait to wait for their answer and get the new session's id; `proofpane asks` lists your asks "
        "and their answers). Ask for one when part of the work can go on beside yours, or to have your work checked; "
        "not for each small step.")


class AskError(ValueError):
    """An ask refused, with the words the asking agent is shown."""


def check_effort(effort, offered, asked=False):
    """An effort named for a Claude Code session, checked against `offered`, what the claude it is for offers
    as claude_cli.capabilities reads it: refused (AskError) while that is not known, `offered` None (or not what
    capabilities gives) or its `floor` true, in UNKNOWN_EFFORTS, and naming the levels it lists when the effort is not
    one of them, a value given that is not text among them. One check for the ask's own effort, against what the
    workspace keeps of its claude, as workspace.ask_offered gives it when the agent asks (Asks.ask, `asked`: its
    refusal while that is not known says READ_AGAIN after UNKNOWN_EFFORTS, and while it is being
    read, `offered` READING, it is refused at once in BEING_READ), and for the person's choice at
    Start, against what the claude that starts offers, read once the new worktree is made (workspace._prepare), which
    never passes READING: a Start never says BEING_READ."""
    if asked and offered is READING:
        raise AskError(BEING_READ)
    if not isinstance(offered, dict) or offered.get("floor"):
        raise AskError(UNKNOWN_EFFORTS + (" " + READ_AGAIN if asked else ""))
    if not isinstance(effort, str) or effort not in (offered.get("efforts") or ()):
        raise AskError("An effort is one of " + ", ".join(offered.get("efforts") or ()) + ".")


def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Asks:
    """The tokens of the running programs and the asks waiting for the person, behind one lock, with the switch the
    person turns (`enabled`): off, no token is issued and no ask is taken, and turning it off clears both at once.

    Each token carries, from when it was issued, the repository its session works on and whether that session may ask
    (one started from an ask may not): never read again from the session's files, which its program can write."""

    def __init__(self, clock=time.time):
        self._lock = threading.Lock()
        self._tokens = {}       # sha256 of a token -> {"session", "repo", "may_ask"}
        self._asks = {}         # ask id -> the ask
        self._taking = set()    # the asks being taken, each as _same_ask writes it (start_taking)
        # ask id -> the ask, taken out by the person's Start, which has neither answered it nor put it back (take):
        # not waiting for the person, and not gone either, since a Start that fails puts it back;
        # or by their refusal, until the refusal is written
        self._starting = {}
        self._clock = clock
        self.enabled = False

    def set_enabled(self, on):
        """The person's switch. Off, every token and every waiting ask goes, in one step, and so does every ask a Start
        holds, which a Start that fails afterwards does not put back."""
        with self._lock:
            self.enabled = bool(on)
            if not self.enabled:
                self._tokens.clear()
                self._asks.clear()
                self._starting.clear()

    # tokens

    def issue(self, session_id, repo, may_ask):
        """A new token for a session's program, which replaces any it had, bound to its repository and to whether it
        may ask; None while the switch is off."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            if not self.enabled:
                return None
            for digest, bound in list(self._tokens.items()):
                if bound["session"] == session_id:
                    del self._tokens[digest]
            self._tokens[_digest(token)] = {"session": session_id, "repo": repo, "may_ask": bool(may_ask)}
        return token

    def revoke(self, session_id):
        """The session's program has ended, or the session is gone: its token is no longer anything."""
        with self._lock:
            for digest, bound in list(self._tokens.items()):
                if bound["session"] == session_id:
                    del self._tokens[digest]

    def bound_to(self, token):
        """What a token was issued for, {"session", "repo", "may_ask"}, or None for anything else (or while off)."""
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            return None
        with self._lock:
            got = self._tokens.get(_digest(token)) if self.enabled else None
            return dict(got) if got else None

    def session_of(self, token):
        """The session a token names, or None."""
        got = self.bound_to(token)
        return got["session"] if got else None

    # asks

    def _expire(self):
        now = self._clock()
        for ask_id, ask in list(self._asks.items()):
            if now - ask["at"] > ASK_TTL:
                del self._asks[ask_id]

    def ask(self, bound, program, task, base=None, effort=None, offered=None):
        """An ask from the session a token was issued for (`bound`, from bound_to) for a new session on its
        repository: the ask, waiting for the person. `effort`, none or "" for none, is taken for a Claude
        Code session alone, and only as one of the efforts `offered` (what the installed claude offers, as the
        workspace keeps it, workspace.ask_offered) lists while that is known (its `floor` false); with `offered` None
        it is not known, and an effort is refused (check_effort, which the person's choice at Start is checked by too,
        the ask's refusal then saying READ_AGAIN too); with `offered` READING it is being read, and
        an effort is refused at once in BEING_READ."""
        if not bound.get("may_ask"):
            raise AskError("A session started from an ask may not ask for another.")
        if program not in AGENTS:
            raise AskError("Ask for one of the terminal agents: " + ", ".join(AGENTS) + ".")
        if effort not in (None, ""):
            if program != "claude":
                raise AskError(CLAUDE_ONLY)
            check_effort(effort, offered, asked=True)
        if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK:
            raise AskError(f"Say what the session is for, in at most {MAX_TASK} characters.")
        if not_text(task):
            raise AskError("A task is text that shows: no control or format characters (but line breaks and tabs), "
                           "none that turn its direction, and none that do not show.")
        if base is not None and not good_base(base):
            raise AskError("Name the base as a branch, a tag or a commit of the repository.")
        if not bound.get("repo"):
            raise AskError("Only a session on a repository may ask for another on it.")
        with self._lock:
            if not self.enabled:
                raise AskError("The person has turned asks off.")
            self._expire()
            mine = sum(1 for a in self._asks.values() if a["from"] == bound["session"])
            if mine >= MAX_OPEN_A_SESSION:
                raise AskError(f"This session has {mine} asks waiting for the person already.")
            if len(self._asks) >= MAX_OPEN:
                raise AskError("The workspace has as many asks waiting for the person as it keeps.")
            ask_id = secrets.token_hex(6)
            ask = {"id": ask_id, "from": bound["session"], "repo": bound["repo"], "program": program,
                   "task": task.strip(), "base": base, "effort": effort or None, "at": int(self._clock())}
            self._asks[ask_id] = ask
            return dict(ask)

    @staticmethod
    def _same_ask(bound, program, task, base, effort):
        """What makes an ask the same ask as another: its session, its program, its task, its base
        and its effort, as `ask` records them (the task without the space around it, none and "" the same effort)."""
        return json.dumps([bound.get("session"), program, task.strip() if isinstance(task, str) else task, base,
                           None if effort in (None, "") else effort], sort_keys=True, default=repr)

    def start_taking(self, bound, program, task, base=None, effort=None):
        """An ask from the session `bound` names is being taken, from the agent's route receiving it until `ask` has
        recorded it or refused it: its key, for done_taking. The same ask while that one is still
        being taken is refused (BEING_TAKEN): the route reads what the installed claude offers before it takes an ask,
        which can outlast `proofpane ask`'s wait, and an agent that asks again meanwhile is not given two asks for one.
        Another ask from that session, or the same ask once the first is taken, is not stopped here."""
        key = self._same_ask(bound, program, task, base, effort)
        with self._lock:
            if key in self._taking:
                raise AskError(BEING_TAKEN)
            self._taking.add(key)
        return key

    def done_taking(self, key):
        """That ask is taken, refused or given up: the same ask may come again."""
        with self._lock:
            self._taking.discard(key)

    def waiting(self):
        """The asks waiting for the person, oldest first."""
        with self._lock:
            self._expire()
            return sorted((dict(a) for a in self._asks.values()), key=lambda a: (a["at"], a["id"]))

    def take(self, ask_id, starting=False):
        """The ask, taken out to be answered; None when there is no such ask (answered already, or gone). Taken for the
        person's Start (`starting`), it is held as being started until that Start answers it (answered) or puts it back
        (put_back), and the asking session reads it as being started meanwhile, not as gone (states_of); a Start can
        take minutes, its reads of claude among them. Held so, it does not expire, being no longer
        among the asks waiting, which alone _expire reads. The person's refusal takes it so too, and holds it only until
        the refusal is written, so the asking session reads it as being started until then, and then as refused, never
        as gone between the two."""
        with self._lock:
            self._expire()
            ask = self._asks.pop(ask_id, None)
            if ask and starting:
                self._starting[ask_id] = dict(ask)
            return dict(ask) if ask else None

    def put_back(self, ask):
        """An ask whose Start failed goes back to be answered again, as it was when that Start took it (the copy take
        holds, whatever the caller's copy now says), and is no longer held. Nothing goes back while the switch is off,
        nor when the ask is no longer held because its session's asks were withdrawn, or the switch turned off, while
        it was being started (put back, it would wait again from a session that was gone, or whose program held no
        token)."""
        with self._lock:
            held = self._starting.pop(ask["id"], None)
            if self.enabled and held is not None:
                self._asks[held["id"]] = held

    def answered(self, ask_id):
        """The person has answered the ask, by a Start or a refusal (Workspace.ask_answered, which writes the answer
        first): it is no longer held as being started."""
        with self._lock:
            self._starting.pop(ask_id, None)

    def states_of(self, session_id):
        """The asks of a session that the person has not answered, by id: "waiting" while it waits for them, and
        "starting" while their Start holds it, or their refusal until it is written (take); read under one lock, so an
        ask moving from one to the other is always found in one. An ask in neither is
        answered, or gone."""
        with self._lock:
            self._expire()
            out = {ask_id: "starting" for ask_id, ask in self._starting.items() if ask["from"] == session_id}
            out.update({ask_id: "waiting" for ask_id, ask in self._asks.items() if ask["from"] == session_id})
            return out

    def withdraw_from(self, session_id):
        """The asks of a session that is gone go with it, those a Start holds among them, so a Start that fails
        afterwards does not put one back."""
        with self._lock:
            for held in (self._asks, self._starting):
                for ask_id, ask in list(held.items()):
                    if ask["from"] == session_id:
                        del held[ask_id]
