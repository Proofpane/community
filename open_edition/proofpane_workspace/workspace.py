"""A workspace: repositories, and sessions, each a git worktree on its own branch with a program in a terminal.

Under the workspace's root:

    repos/<name>/                 a clone, made here from an https address or by the user in a shell
    sessions/<id>/session.json    the session: repository, program, task, base commit, branch
    sessions/<id>/work/           its worktree, on the branch pp/<id>
    moves/<seq>/                  the files the app sent for a session moved from a linked machine, until it starts
                                  (moves.py)

A session's program is `claude`, `codex` or a shell, found on the PATH and run as installed, with nothing of its
own sign-in touched: the user signs in inside the terminal, in the program's own flow, and the program keeps its
credentials in its own files, which nothing here reads. The environment a program gets is built from a short list
of names (PASS_ENV), never copied whole, so a secret the agent or the app that started it holds is not passed on.

GitHub the same way: a `github` session runs GitHub's own CLI sign-in (`gh auth login`, a one-time code the user
enters at github.com) and `gh auth setup-git`, after which git in the workspace clones the user's private
repositories through gh's credential helper, and `github()` lists them by asking gh. gh keeps its token in its own
file on the user's volume; nothing here reads it. A program in a session runs as the same user and can use it too,
to push as well as to clone; the gate refuses the calls that name that file or print the token, and a fine-grained
token with read-only access, given to `gh auth login --with-token`, keeps the workspace from pushing at all.

A shell or a `github` session may start without a repository: it runs in the workspace's root, and has no change to
finish.

Finishing a session commits what its worktree holds and reports the base, the head and the diff between them, for
the app to verify. The program keeps running; a later finish reports everything since the same base.
"""

import asyncio
import collections
import concurrent.futures
import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from urllib.parse import urlsplit

from . import accounts as accounting, agents as agentry, asks as asking, chat as chats, claude_cli, claude_update, core_plugin as core, first_run, gitplaces, mcp, moves, ports, providers, sessionmap, statusline, terminals, usage, watch
from . import budget_check, usage_report
from . import settings as settings_made

PASS_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ", "TMPDIR",
            "SSH_AUTH_SOCK")
PROGRAMS = ("claude", "codex", "gemini", "opencode", "grok", "qwen", "kimi", "mcode", "shell", "github", "claude-sign-in",
            "chat", "custom", "codex-sign-in")
# the programs whose every start asks Proofpane's budget check while the workspace is paired (budget_check.py),
# at a session's first start (prepare) and at each start again (claude_floor_for): every program that
# runs an agent, the terminal agents the page offers (AGENTS) and an agent of the person's own (`custom`), since the
# ADE asks before every session starts and a session of any of them may spend on a
# model. Its kind of spend is budget_check.session_kind's: a subscription's for each, the workspace not knowing at a
# start what a program is billed as, but a claude terminal on another provider's model, which is its own key's. Not a
# shell, nor a sign-in (SIGN_INS): neither runs an agent, and neither spends anything at its start. A chat asks it at
# each start of its claude instead (_chat_start)
BUDGET_CHECKED = ("claude", "codex", "gemini", "opencode", "grok", "qwen", "kimi", "mcode", "custom")
# an agent of the person's own (the app's custom_agents.py): its command as they wrote it, split as a shell would but
# never run by one, and how its task reaches it
CUSTOM_NAME = 40
CUSTOM_COMMAND = 500
CUSTOM_ARGS = 32
CUSTOM_TASK = ("argument", "typed")
# terminal agents that take the task as their first prompt on the command line and stay interactive: claude, codex,
# gemini, grok (Grok Build) and mcode (MiniMax Code) as their last argument; opencode after --prompt (its own last
# argument is a folder); qwen (Qwen Code) after -i, its prompt that stays interactive (a bare one runs once and exits).
# kimi (Kimi Code 2.1.1) takes none: its -p runs once and exits, so it starts at its prompt and the page types the task
PROMPTED = {"claude": None, "codex": None, "gemini": None, "opencode": "--prompt", "grok": None, "qwen": "-i",
            "mcode": None}
# what the page offers to start in a terminal
AGENTS = ("claude", "codex", "gemini", "opencode", "grok", "qwen", "kimi", "mcode")
WITHOUT_REPOSITORY = ("shell", "github", "claude-sign-in", "codex-sign-in")
# the programs' own sign-ins, which the page shows a link, a code or the program's words from, and types a code into
SIGN_INS = ("github", "claude-sign-in", "codex-sign-in")
OWN_SIGN_IN = ("On your own machine the program's own sign-in is yours, as in your terminal: sign out there, in the "
               "program itself.")
# gh signs in with nothing to read from the terminal: with its input from /dev/null it asks no question (a question
# drawn before any terminal is attached waits for the terminal's answers, and a terminal that attaches later answers
# them into it as keystrokes, which ended the sign-in with status 1 on 2026-09-28), and prints the one-time code and
# github.com/login/device, then waits for the device to be authorized; git's credentials are set up after it.
GITHUB_SIGN_IN = ("gh auth login --hostname github.com --git-protocol https --web </dev/null"
                  " && gh auth setup-git --hostname github.com"
                  " && echo && echo 'GitHub is signed in for this workspace. This terminal can be closed.'")
GITHUB_TIMEOUT = 30
# the line gh's sign-in prints with the one-time code it waits on, the code in bold: the page shows the code, so the
# sign-in needs no terminal; gh's token stays in its own configuration, never read here
DEVICE_CODE = re.compile(rb"one-time code: (?:\x1b\[[0-9;]*m)*([A-Z0-9]{4}-[A-Z0-9]{4})(?![A-Za-z0-9-])")
# Claude Code's own sign-in, driven from the page, for a person new to it: it prints the
# page to sign in at and waits for the code that page gives, which the Workspace section passes to it here. What it
# gets it keeps in its own credentials file on the volume, which the gate keeps sessions from reading: the agent and the
# app see the link and the code, never a token, and no session's environment carries one.
CLAUDE_SIGN_IN = ("claude", "auth", "login", "--claudeai")
CLAUDE_SIGN_IN_URL = re.compile(rb"https://claude\.(?:com/cai|ai)/oauth/authorize\?[A-Za-z0-9._~%&=+:/-]{1,4000}")
SIGN_IN_CODE = re.compile(r"[A-Za-z0-9._~#-]{8,1024}")
CLAUDE_TIMEOUT = 30
# An account's Claude Code status, asked of `claude auth status` (up to CLAUDE_TIMEOUT), is asked once
# at a time per account and its answer kept this many seconds, shared by GET /claude and GET /accounts, which the
# pages ask every few seconds; and after a sign-in's code is typed, the account's status is read every
# SIGN_IN_WATCH_EVERY seconds for its first minute and every SIGN_IN_WATCH_LATER after it, for SIGN_IN_WATCH_FOR at
# most, until Claude Code says this sign-in signed it in, the sign-in then ended SIGN_IN_SETTLE seconds after
CLAUDE_STATUS_KEPT = 3.0
SIGN_IN_WATCH_EVERY = 2.0
SIGN_IN_WATCH_LATER = 10.0
SIGN_IN_WATCH_FOR = 600
SIGN_IN_SETTLE = 2.0
# Claude Code's settings declare `args`, a hook given as a program and its arguments, from 2.1.139, the first release
# whose do; what an older one does with the gate given so (settings.py) was not measured, and from that reading alone
# it is taken not to run it. So the workspace starts no claude
# terminal, chat or agent's run on one older than this, on one whose version it cannot read, or with none, asking at
# each start where and as that start runs it (claude_floor_refusal): under the local provider the claude is whichever
# the PATH gives, and in a workspace machine the build in use (claude_update.py says which build that is).
CLAUDE_ARGS_FLOOR = (2, 1, 139)
_EMAIL = re.compile(r"[^@\s]{1,200}@[^@\s]{1,200}")
_PUSHED = re.compile(r"\d{4}-\d{2}-\d{2}T")
_WORD = re.compile(r"[A-Za-z0-9._ -]{1,40}")
GITHUB_PAGE = 100
MAX_GITHUB_PAGES = 10      # the 1000 most recently pushed are listed; the address box clones any other
MAX_SESSIONS = 12
MAX_TASK = 4000
MAX_TITLE = 120          # a session's name on the page (rename)
ACTIVITY_FILE = 64 * 1024        # a session's activity.json, read: activity.py writes a few hundred bytes
READ_VERSION_FOR = 600           # seconds a claude's version read for a start is kept for it (_with_map)
SKILLS_EVERY = 2                 # seconds between the takes of the skill calls that reads of the list start (skillcalls.py)
# a session's own map file at most: _with_map writes no larger, and _on_provider and the map (sessionmap.given) read
# it back no larger, so a file given is one the map says was given
SETTINGS_FILE = sessionmap.MAX_SETTINGS
# a hosted workspace's claude terminal whose settings file does not carry the approval hook: not started,
# since a start without it would let its gated commands through on Claude Code's own asking
HELD_TERMINAL_REFUSED = ("The claude terminal could not be given the approval hook, which holds the Bash commands on "
                         "its list (a git push, a gh pr merge or release, a deploy, a publish, an rm -rf and the "
                         "others) for your approval under Approvals: its settings file could not be written. It was "
                         "not started.")
# and a program of the owner's own that starts Claude Code, in a hosted workspace: not started
CUSTOM_CLAUDE_REFUSED = ("In a hosted workspace a program of your own that is Claude Code is not started, since it "
                         "would not hold its gated commands for your approval: start a Claude Code session instead.")
MAX_RECORD = 1024 * 1024        # a session's record read at most: the workspace writes far less (a task, MAX_TASK)
MAX_SHELLS = 4          # the shells a session may have, beside its program (shell_start)
# a session continued in a new one, by the owner's press: the programs the new one may run, and
# how much of what the workspace observed of the old one it reports (observed), the rest counted
HANDOVER_PROGRAMS = ("claude", "codex", "chat")
MAX_OBSERVED_COMMITS = 50
MAX_OBSERVED_FILES = 200
_CONVERSATION = moves.SESSION           # a conversation's id, as Claude Code names its files
_ACTIVITY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")    # a tool's or a notification kind's name (activity.py)
# a chat's settings, chosen when it is made and changed while it runs (the model, the effort and the permission mode,
# as the Claude app has them), each one Claude Code's own: an alias or a
# model's full name; its effort levels; its permission modes but dontAsk, which answers nothing a chat could ask. The
# levels and the modes are read from the installed claude (claude_cli.py); these are the floor
CHAT_MODES = claude_cli.FLOOR_MODES
CHAT_EFFORTS = claude_cli.FLOOR_EFFORTS
_MODEL = re.compile(r"[a-z][a-z0-9.-]{0,63}(?:\[1m\])?")
# an effort's name as Claude Code's help gives its levels (claude_cli reads them so): what start_effort and launch take
# from the app for a session started from an ask, a value of --effort and never a flag of its own, so a dash
# is no part of it
_EFFORT = re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}")
_CONTROLLED_MODEL = re.compile(r"[A-Za-z0-9._:\[\]-]{1,100}")    # as the controller's side gives one (controller.py)
# how often, at most, what the workspace keeps of its claude (capabilities) is read again in the background while it is
# the floor and an ask names an effort (Workspace.refresh_capabilities): such an ask is refused at
# once, its words saying to ask again in a minute (asks.READ_AGAIN)
CAPABILITIES_REFRESH_EVERY = 60
# a file from the page, into a session's worktree where its program reads it (as the Claude app takes one): under
# UPLOADS, which the repository's exclude file keeps out of
# every commit; a name kept when it is plain, else made plain, its extension kept when that is
UPLOADS = ".proofpane-uploads"
MAX_UPLOAD = 10 * 1024 * 1024
ASK_LOG_TAIL = 256 * 1024       # what is read of a session's record of its asks (ask_log): its end
_UPLOAD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,99}")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}$")
MAX_DIFF = 1024 * 1024
# each session's change size in the list (+added -removed, files): worked out off the request that lists them, one
# session at a time, and kept this many seconds before it is worked out again, while the session runs and once it ended
SIZE_FRESH_RUNNING = 10
SIZE_FRESH_ENDED = 120
# and at least this many times as long as it took to work out: a large repository, whose size takes a second, is not
# worked out again every few seconds while someone looks at the list (its share of a core stays near 1/SIZE_PATIENCE)
SIZE_PATIENCE = 30
MAX_VIEW = 512 * 1024       # a file the page shows; larger ones are named, not sent
MAX_IMAGE = 10 * 1024 * 1024    # an image the page shows (image)
# the images the page shows, by their names' endings, and what they are sent as; nothing else is sent as bytes
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".avif": "image/avif", ".bmp": "image/bmp", ".ico": "image/x-icon",
               ".svg": "image/svg+xml"}
# and a PDF, which the page shows in the browser's own viewer
SHOWN_TYPES = dict(IMAGE_TYPES, **{".pdf": "application/pdf"})
SHIP_TIMEOUT = 180          # a push, or gh opening a pull request
MAX_PR_TITLE = 200
MAX_PR_BODY = 64 * 1024
PR_URL = re.compile(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/pull/[0-9]+")
_ISSUE_URL = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/issues/[0-9]+$")
MAX_ISSUES = 100            # a repository's open issues listed at once, and its open pull requests
MAX_PULL = 10 ** 7          # a pull request's number
MAX_PATHS = 20000           # a worktree's files the page searches by name; past this the list is cut, and says so
MAX_QUERY = 200             # what a search of a worktree's text looks for (search)
MAX_MATCHES = 400           # the matching lines it gives back; past this it stops, and says so
MAX_MATCH_LINE = 400        # each line cut to this many characters
SEARCH_TIMEOUT = 15         # seconds a search may take before it is stopped
ISSUE_TEXT = 8000           # an issue's text given to the page, enough to start a session's task from
MERGE_METHODS = ("squash", "merge", "rebase")
PR_FIELDS = ("number,title,state,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,mergedAt,"
             "baseRefName,author")
# the person's own review of a pull request, as gh pr review takes it (pr_review)
REVIEW_FLAGS = {"approve": "--approve", "request_changes": "--request-changes", "comment": "--comment"}
# a review's words, at most: GitHub takes 65,536 characters, and the agent a request of 64 KB, in which this many fit
# whatever they are
MAX_REVIEW = 15000
# a check run's conclusion, as GitHub reports it: what counts as passed and as failed; anything else is still running
_PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAILED = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE", "ERROR"}
# what reviewers and checks said of a shipped session's pull request, for its agent (pr_feedback)
FEEDBACK_TEXT = 4000        # a comment's words given to the page
FEEDBACK_HUNK = 12          # the lines of code a review comment is on, from the end of its diff hunk
FEEDBACK_LOG = 6000         # the part of a failed job's log given to the page, the part that says why
FEEDBACK_JOBS = 4           # the failed jobs whose logs are read
FEEDBACK_LOG_BYTES = 64 * 1024 * 1024   # a job's log is read up to this much, as it comes, and never held whole
FEEDBACK_LOG_SECONDS = 60   # and for at most this long
FEEDBACK_LINE = 500         # each line of a log's excerpt cut to this many characters, its start kept
FEEDBACK_AUTHORS = 10       # commenters whose permission on the repository is asked, at most
# anyone at all can comment on a pull request of a public repository, so what each wrote is marked with whether they
# can write to it: the repository's owner, or a member or collaborator whose permission GitHub gives as admin or write
# (asked of GitHub, which tells only someone who can push); never a bot, which may quote the pull request itself
_OWNER = "OWNER"
_ASK_PERMISSION = {"MEMBER", "COLLABORATOR"}
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_PR_PARTS = re.compile(r"^https://([A-Za-z0-9.-]+)/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/([0-9]+)$")
_JOB_URL = re.compile(r"^https://([A-Za-z0-9.-]+)/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/actions/runs/[0-9]+/job/([0-9]+)"
                      r"(?:[?#/].*)?$")
_LOG_STAMP = re.compile(r"^\ufeff?[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z ?")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_ESCAPE = re.compile(r"\x1b[@-Z\\-_]?")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
FEEDBACK_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      isCrossRepository
      reviewThreads(last: 60) {
        nodes { isResolved isOutdated path line originalLine
          comments(first: 12) { nodes { author { __typename login } authorAssociation body url createdAt diffHunk } } }
      }
      reviews(last: 30) { nodes { author { __typename login } authorAssociation state body url submittedAt } }
      comments(last: 30) { nodes { author { __typename login } authorAssociation body url createdAt } }
    }
  }
}"""
MAX_LIST = 5000             # entries of one folder the page lists
CLONE_TIMEOUT = 600
PARTIAL_CLONE = ".cloning-"     # a clone not yet whole, in the repositories' folder (clone)
MAX_LINK_PATH = 4096            # a folder's path, for link
GIT_TIMEOUT = 120

_ID = re.compile(r"^[0-9a-f]{12}$")
_GROUP = re.compile(r"^[a-z0-9]{8,32}$")
# a cloud session of the person's Claude account, continued here by Claude Code's own teleport: its id as claude.ai
# shows it, or a claude.ai/code link to it
_TELEPORT_ID = re.compile(r"^(?:session|cse)_[A-Za-z0-9_-]{1,100}$")
_TELEPORT_URL = re.compile(r"^https://claude\.ai/code/((?:session|cse)_[A-Za-z0-9_-]{1,100})/?(?:[?#][^\s]*)?$")


def teleport_id(value):
    """The cloud session teleport continues, from what the person gave: its id, from the id or a claude.ai/code link;
    "" for none, which opens Claude Code's own picker of the account's cloud sessions. WorkspaceError for anything
    else: it goes to claude as one argument of its own, but only once it is one of these. The id a cloud session sees
    itself by (CLAUDE_CODE_REMOTE_SESSION_ID, cse_...) is claude.ai's session_ with the same rest, which teleport
    takes."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if not isinstance(value, str) or len(value) > 300:
        raise WorkspaceError("A cloud session is named by its id (session_... or cse_...) or its claude.ai/code link.")
    v = value.strip()
    m = _TELEPORT_URL.match(v)
    if _TELEPORT_ID.match(v) or m:
        v = m.group(1) if m else v
        return "session_" + v[len("cse_"):] if v.startswith("cse_") else v
    raise WorkspaceError("A cloud session is named by its id (session_... or cse_...) or its claude.ai/code link.")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_URL_PATH = re.compile(r"^/[A-Za-z0-9._~/-]+$")
_HOST = re.compile(r"^[A-Za-z0-9.-]+$")
_GITHUB = re.compile(r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")
ADDRESS_MOST = 500


def origin_fields(url):
    """A repository's `slug` and `address` as its listing gives them, from its origin's URL:
    on GitHub, in the forms `github` matches, owner/name and its https address; another https
    origin of at most ADDRESS_MOST characters, with a host and no user information, query or fragment, that URL and no
    slug; else neither. No user information of an origin is put in either."""
    url = url.strip() if isinstance(url, str) else ""
    m = _GITHUB.match(url)
    if m:
        return m.group(1), f"https://github.com/{m.group(1)}.git"
    if not 0 < len(url) <= ADDRESS_MOST or not url.startswith("https://") or "?" in url or "#" in url \
            or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None, None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None, None
    if not host or "@" in parts.netloc:
        return None, None
    return None, url



def _check(c):
    """One of a pull request's checks as GitHub reports it, a check run or a commit status: {name, state, url}, its
    state passed, failed or running; None for anything else."""
    if not isinstance(c, dict):
        return None
    if "context" in c or c.get("__typename") == "StatusContext":
        name, s = c.get("context"), str(c.get("state") or "").upper()
        state = "passed" if s == "SUCCESS" else "failed" if s in ("FAILURE", "ERROR") else "running"
        link = c.get("targetUrl")
    else:
        name, done = c.get("name"), str(c.get("status") or "").upper() == "COMPLETED"
        conclusion = str(c.get("conclusion") or "").upper()
        state = "running" if not done else "passed" if conclusion in _PASSED else "failed" if conclusion in _FAILED \
            else "running"
        link = c.get("detailsUrl")
    return {"name": (name if isinstance(name, str) and name else "a check")[:120], "state": state,
            "url": link if isinstance(link, str) and link.startswith("https://") and len(link) < 2000 else None}


def pr_view(pr):
    """What the page shows of a pull request gh described (PR_FIELDS)."""
    checks = [x for x in map(_check, pr.get("statusCheckRollup") or []) if x]
    word = lambda k: pr[k] if isinstance(pr.get(k), str) and pr[k] else None  # noqa: E731
    return {"number": pr["number"] if isinstance(pr.get("number"), int) and not isinstance(pr["number"], bool) else None,
            "title": (word("title") or "")[:300], "state": word("state"), "draft": pr.get("isDraft") is True,
            "mergeable": word("mergeable"), "merge_state": word("mergeStateStatus"), "review": word("reviewDecision"),
            "merged_at": word("mergedAt"), "base": word("baseRefName"),
            "author": (pr["author"].get("login") or None) if isinstance(pr.get("author"), dict)
            and isinstance(pr["author"].get("login"), str) else None,
            "checks": {"passed": sum(c["state"] == "passed" for c in checks),
                       "failed": sum(c["state"] == "failed" for c in checks),
                       "running": sum(c["state"] == "running" for c in checks), "items": checks[:40]}}

class LogExcerpt:
    """The part of a failed GitHub Actions job's log that says why it failed, read a line at a time (`feed`), so that a
    log of any length is never held whole: the step around each of its first three errors (`##[error]`), from where
    that step began (`##[group]Run`, at most 80 lines before) to just after the error; or, with no error marked, its
    last 80 lines before GitHub's own cleanup. Timestamps, colours, the log's markup and every other control character
    are dropped, each line is cut to FEEDBACK_LINE characters (its start kept), and each part to its share of `most`
    characters from its start, so that its error stays."""

    BEFORE, AFTER, ERRORS = 80, 2, 3

    def __init__(self, most=FEEDBACK_LOG):
        self.most = most
        self.n = 0                                              # lines kept for reading so far
        self.recent = collections.deque(maxlen=self.BEFORE)     # (number, text, a step began) of the lines before
        self.tail = collections.deque(maxlen=self.BEFORE)       # the last lines before the cleanup: for no error
        self.kept = {}                                          # number -> text of the lines the excerpt shows
        self.errors = 0
        self.after = 0
        self.cleanup = False

    def feed(self, raw):
        line = _LOG_STAMP.sub("", raw.rstrip("\r\n"))
        line = _CONTROL.sub("", _ESCAPE.sub("", _ANSI.sub("", _ANSI_OSC.sub("", line)))).rstrip()
        if line.startswith(("##[endgroup]", "##[debug]")):
            return
        step, error = line.startswith("##[group]Run "), "##[error]" in line
        for mark, word in (("##[group]", ""), ("##[error]", "error: "), ("##[warning]", "warning: "),
                           ("##[notice]", "notice: ")):
            line = line.replace(mark, word)
        if len(line) > FEEDBACK_LINE:
            line = line[:FEEDBACK_LINE - 1] + "\u2026"
        self.n += 1
        if line.startswith("Post job cleanup"):
            self.cleanup = True
        if self.after:
            if self.cleanup or step:
                self.after = 0
            else:
                self.kept.setdefault(self.n, line)
                self.after -= 1
        if error and not self.cleanup and self.errors < self.ERRORS:
            self.errors += 1
            before = list(self.recent)
            began = next((i for i in range(len(before) - 1, -1, -1) if before[i][2]), 0)
            for number, text, _ in before[began:]:
                self.kept.setdefault(number, text)
            self.kept[self.n] = line
            self.after = self.AFTER
        if not self.cleanup:
            self.tail.append((self.n, line))
        self.recent.append((self.n, line, step))

    def text(self):
        if self.kept:
            parts, run = [], []
            for number in sorted(self.kept):
                if run and number != run[-1] + 1:
                    parts.append(run)
                    run = []
                run.append(number)
            parts.append(run)
            texts = ["\n".join(self.kept[n] for n in part).strip("\n") for part in parts]
        else:
            texts = ["\n".join(text for _, text in self.tail).strip("\n")]
        texts = [t for t in texts if t]
        if not texts:
            return ""
        budget = max(200, self.most // len(texts))
        return "\n\u2026\n".join("\u2026" + t[-(budget - 1):] if len(t) > budget else t for t in texts)


def failure_excerpt(log, most=FEEDBACK_LOG):
    """LogExcerpt over a log held whole (a test's, a small one)."""
    excerpt = LogExcerpt(most)
    for line in log.splitlines():
        excerpt.feed(line)
    return excerpt.text()


class WorkspaceError(ValueError):
    """A request the user can correct, shown as it is."""


class NotFound(WorkspaceError):
    pass


class GitHeld(WorkspaceError):
    """A git the workspace would run where git's places changed while an agent's run worked (gitplaces.py): refused,
    with the owner's words."""


class Conflict(WorkspaceError):
    """A change made to something that has changed since the person last saw it."""


def first_prompt(task):
    """The task as a program's first prompt: one command-line argument, which a leading "-" would make an option."""
    text = task.strip()
    return " " + text if text.startswith("-") else text


def check_id(session_id):
    if not isinstance(session_id, str) or not _ID.match(session_id):
        raise NotFound("No such session.")
    return session_id


def check_name(name):
    if not isinstance(name, str) or not _NAME.match(name) or name.endswith(".git") or ".." in name:
        raise WorkspaceError("A repository's name is letters, digits, dots, dashes and underscores.")
    return name


def check_url(url):
    """An https address to clone from, with no user name, password, query or fragment in it; returns the name the
    clone gets. Only https: a file or ssh address would let a clone read other places on the workspace's machine."""
    if not isinstance(url, str) or len(url) > 500:
        raise WorkspaceError("Give the repository's https address.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise WorkspaceError("That is not an https address.") from None
    host = parts.hostname or ""
    if (parts.scheme != "https" or "@" in parts.netloc or parts.query or parts.fragment or not _HOST.match(host)
            or port not in (None, 443) or not _URL_PATH.match(parts.path) or "/../" in parts.path + "/"
            or "/./" in parts.path + "/"):
        raise WorkspaceError("Give the repository's https address, with no user name or password in it.")
    last = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return check_name(last[:-4] if last.endswith(".git") else last)


def program_env(extra=None):
    """The environment a program or git runs with: the names in PASS_ENV, from the agent's own environment, and
    the terminal's type. Nothing else passes."""
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    if sys.platform == "darwin":
        # macOS's own bash, a login shell for someone who never moved to zsh, opens every shell with a notice
        # that zsh is the default now: a shell in a worktree opens at its prompt instead
        env["BASH_SILENCE_DEPRECATION_WARNING"] = "1"
    env.update(extra or {})
    return env


# git the workspace runs for an agent's run (agents.py), and every git of its own but the owner's own actions
# (_git_env): no hook, no fsmonitor and no signing program, whatever the repository's settings say, since a run could
# have written them; watch.py names any change to them at the run's end, and gitplaces.py checks them before each git.
# A signing program (the one a signature's form names: gpg.program or gpg.openpgp.program, gpg.x509.program,
# gpg.ssh.program, and gpg.ssh.defaultKeyCommand for an SSH key) starts where git signs a commit, a push or a tag, or
# checks a signature for a log, a reflog or a show (log.showSignature, or a format's %G) or a merge
# (merge.verifySignatures). commit.gpgSign, push.gpgSign and log.showSignature are pinned off; this git makes no tag,
# starts no merge, names the format of each log and reflog it runs and shows no commit
_RUN_PAIRS = (("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"), ("commit.gpgSign", "false"),
              ("push.gpgSign", "false"), ("log.showSignature", "false"))
RUN_GIT = gitplaces.config_env(_RUN_PAIRS)
# the git commands that reach a remote: every other runs with every transport refused (a lazy fetch's among them)
NETWORK_VERBS = ("clone", "fetch", "push", "pull", "ls-remote")
# each workspace's record of git's places (gitplaces.Checker), by its root as given and as its real path: the one a
# git's folder is in checks it (check_places)
_PLACES = {}
_GUARDED = set()                # the folders whose worktrees are agents' runs': git there runs as RUN_GIT says
ADOPT_EVERY = 5                 # seconds between a read's looks at whether a teleport session's work is there to take
ADOPT_FETCH = 30                # seconds the fetch of what it follows may take, in the adoption's own thread
# the task the page gives a teleport session when none is typed (ui/src/components/NewSession.tsx); such a session,
# never renamed, is named after the branch teleport checked out once its work is taken (Workspace._adopt)
TELEPORT_TASK = "Continued from claude.ai"


def _created(view):
    """A session's place in the list (Workspace.sessions): its created_at, a number; one that is not, after them."""
    at = view.get("created_at")
    return (0, at) if isinstance(at, (int, float)) else (1, 0)


def _finite_float(text):
    """A JSON number with a fraction or an exponent as a float, refused where it is none that JSON can carry: Python
    reads 1e400 as infinity, which no answer can render, so a record holding one failed the list for every session."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("A number JSON cannot carry.")
    return value


def _no_json_constant(name):
    """NaN, Infinity and -Infinity, which Python's json reads and JSON has no name for: refused, as _finite_float."""
    raise ValueError(f"{name} is not JSON.")


def _new_file(path, mode):
    """A file of this process's own at `path`, made anew, open to write: whatever was there before is taken away first,
    and the open makes a new file or fails, never following a link nor waiting on a FIFO a session's programs put
    there (they can write the session's folder), which a plain open would wait on, on the agent's event loop.
    An OSError when the folder will not take it."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode),
                     "w", encoding="utf-8")


def _workspace_settings(path):
    """The settings in a --settings file of the workspace's own (the local provider's gate settings, which its operator
    names, PP_WORKSPACE_CLAUDE_SETTINGS), for a session's own to be made from (_with_map, _on_provider): read as the
    workspace always read it, a link followed and whatever its size, only never waiting on a FIFO; None when it cannot
    be read. A session's own file is read as the map reads a session's files instead (sessionmap.read_json: its
    programs can replace it with a link or a FIFO); read so, a link to this one, or
    one past 1 MB, would refuse a session that started before."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0))
    except OSError:
        return None
    # looked at before it is wrapped: wrapping a folder raises, and would leave the descriptor open
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    try:
        with os.fdopen(fd, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None


def _teleport_pending(record):
    """A session teleport started whose work it has not yet taken as its own (Workspace._adopt)."""
    return "teleport" in record and not record.get("adopted") and bool(record.get("repo"))


def _teleport_title(record):
    """The name a session teleport started takes once its work is taken: the branch teleport checked
    out, where the session was never renamed and is still under the page's own task for it (TELEPORT_TASK), and was
    not named so before (`named_after_branch`: a name cleared by Rename since puts its task back, as Rename says);
    else None."""
    came = record.get("teleported_from")
    if record.get("adopted") and came and not record.get("title") and not record.get("named_after_branch") \
            and (record.get("task") or "").strip() == TELEPORT_TASK:
        return came[:MAX_TITLE]
    return None


def _name_after_branch(record):
    """A teleport session's record given the name _teleport_title says it takes, if any, once (a change for
    _update)."""
    title = _teleport_title(record)
    if title:
        record.update(title=title, named_after_branch=True)


def guard(folder):
    """Every git the workspace runs in `folder` or under it, from now on, as for an agent's run (RUN_GIT)."""
    _GUARDED.add(os.path.realpath(folder))


def _config_pairs(env):
    """The settings GIT_CONFIG_COUNT gives in a git environment, in order: [(key, value)]."""
    try:
        count = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        count = 0
    return [(env[f"GIT_CONFIG_KEY_{i}"], env.get(f"GIT_CONFIG_VALUE_{i}", "")) for i in range(count)
            if f"GIT_CONFIG_KEY_{i}" in env]


def _with(env, extra):
    """git's environment `env` with `extra`'s added: extra's GIT_CONFIG_ settings after env's, never in their place."""
    if not extra:
        return env
    out = {k: v for k, v in {**env, **extra}.items()
           if k != "GIT_CONFIG_COUNT" and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    out.update(gitplaces.config_env(_config_pairs(env) + _config_pairs(extra)))
    return out


def root_of(path):
    """The folder of the workspace whose folder `path` is in, as its record of git's places is kept (_PLACES), or
    None."""
    for p in (os.path.abspath(path), os.path.realpath(path)):
        root = max((r for r in list(_PLACES) if p == r or p.startswith(r + os.sep)), key=len, default=None)
        if root is not None:
            return root
    return None


def _places_for(cwd):
    """The record of git's places (gitplaces.Checker) of the workspace whose folder `cwd` is in, or that links to it
    (the desktop app's repository, where Land's merge runs), or None."""
    root = root_of(cwd)
    if root is not None:
        return _PLACES.get(root)
    return next((c for c in set(_PLACES.values()) if c.covers(cwd)), None)


def check_places(cwd):
    """Before a git of the workspace's in `cwd`, or a program that runs git there (gh): GitHeld where git's places
    there, or outside every repository, changed while an agent's run worked (gitplaces.py). The record, or None."""
    checker = _places_for(cwd)
    if checker is not None:
        try:
            checker.check_at(cwd)
        except gitplaces.Held as e:
            raise GitHeld(str(e)) from None
    return checker


def _git_env(cwd=None, own=False, network=False):
    """git's environment. With `cwd`, git looks for its repository there and no higher (GIT_CEILING_DIRECTORIES): every
    git the workspace runs is at a repository's top or a worktree's, and one git cannot read (its .git broken, or half
    made) must fail, not be taken for a repository around the workspace; and git's places there are checked first
    (check_places). Every git as RUN_GIT says, with lazy fetches off and every transport refused but for a command that
    reaches a remote (`network`); but the owner's own actions (`own`: New session, Finish, Update, Land and Ship) run
    as their own git would, the repository's hooks and its promisor among it, in a repository whose places the check
    accepts, outside an agent's worktree (guard)."""
    extra = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    guarded = False
    checker = None
    if cwd is not None:
        # from where git really is: a repository added from this machine is a link, and git looks from its folder
        real = os.path.realpath(cwd)
        extra["GIT_CEILING_DIRECTORIES"] = os.path.dirname(real)
        guarded = any(real == g or real.startswith(g + os.sep) for g in _GUARDED)
        checker = check_places(cwd)
    if guarded or not own:
        extra["GIT_NO_LAZY_FETCH"] = "1"
        extra.update(gitplaces.config_env(list(_RUN_PAIRS) + ([] if network else [("protocol.allow", "never")])))
    elif checker is not None and checker.hooks_relative(cwd):
        # a hooks folder named by a relative path is in the worktree, among the files a commit brings, which a run can
        # choose by moving a branch: the owner's own action runs no hooks from it
        extra.update(gitplaces.config_env([("core.hooksPath", "/dev/null")]))
    return program_env(extra)


def _gh_env(cwd):
    """gh's environment, run in `cwd`: the workspace's own sign-in and never a prompt; and the git gh runs there runs
    as the workspace's own reads do (no hook, no fsmonitor, no signing, no lazy fetch), after git's places there are
    checked (check_places)."""
    check_places(cwd)
    return program_env({"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "GIT_NO_LAZY_FETCH": "1",
                        **gitplaces.config_env(_RUN_PAIRS)})


def git(*args, cwd, timeout=GIT_TIMEOUT, ok=(0,), input=None, env=None, own=False, errors="replace"):
    """Run git and return (exit code, output as text); a code not in `ok` is an error naming git's own message. `env`
    adds to git's own environment (a temporary index, for one); `own` is for the owner's own actions (_git_env).
    `errors` is how the output is decoded: "surrogateescape" keeps a name that is not UTF-8 as the filesystem's own,
    as os.scandir gives it (a continued session's worktree check)."""
    verb = next((a for a in args if not a.startswith("-") and "=" not in a), "")
    try:
        base = _git_env(cwd, own=own, network=verb in NETWORK_VERBS)
        p = subprocess.run(["git", *args], cwd=cwd, env=_with(base, env), capture_output=True, timeout=timeout,
                           input=input if input is not None else b"")
    except subprocess.TimeoutExpired:
        raise WorkspaceError(f"git {verb} took longer than {timeout} seconds.") from None
    out = p.stdout.decode("utf-8", errors)
    if p.returncode not in ok:
        message = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise WorkspaceError(f"git {verb} failed: {message[-1] if message else 'no message'}")
    return p.returncode, out


NOT_FOLLOWED = "That is not a file the page shows (a link is not followed)."
# a stop of a task's attempt's own session (Workspace.stop): refused, the task's own Stop named
ATTEMPT_STOP = "This session is an attempt of a task: stop it with the task's own Stop."


def _folder_beneath(work, parts):
    """A descriptor of the folder `parts` names in the worktree `work` (the worktree itself for none), reached one
    folder at a time, each opened from the one above it without following a link: whatever a session's program swaps
    on the way after the path was looked at, no part of it is a link followed. The caller closes it."""
    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            below = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = below
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_regular(work, parts, limit):
    """At most `limit` bytes and one more of the regular file `parts` names in the worktree `work`, as it is when
    opened: a look at the path before (lstat, realpath) can be answered by a path a session's program then changes, so
    no part of the way is a link followed (_folder_beneath), the open never waits on a pipe with no writer, and what it
    opened must be a regular file."""
    try:
        folder = _folder_beneath(work, parts[:-1])
    except OSError:
        raise WorkspaceError(NOT_FOLLOWED) from None
    try:
        return _read_in(folder, parts[-1], limit)
    finally:
        os.close(folder)


def _read_in(folder, name, limit):
    """At most `limit` bytes and one more of the regular file `name` in the open folder `folder`: not a link, never
    waited on (a pipe), and a regular file as opened."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=folder)
    except OSError:
        raise WorkspaceError(NOT_FOLLOWED) from None
    # looked at before it is wrapped: wrapping a folder raises, and would leave the descriptor open
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise WorkspaceError("That is not a file.")
    with os.fdopen(fd, "rb") as fh:
        return fh.read(limit + 1)


def _parents(path):
    """The folders above `path`, nearest first, up to the root."""
    while True:
        up = os.path.dirname(path)
        if up == path:
            return
        yield up
        path = up


def _unseen(text):
    """Whether `text` holds what a person would not see as text (control, format, private-use or unassigned)."""
    return any(unicodedata.category(c) in ("Cc", "Cf", "Co", "Cn", "Cs", "Zl", "Zp") for c in text)


def _found(program):
    """Whether a program named as an agent's command names it is there to start: by its full path, or by its name on
    the PATH its session gets; a relative path is not."""
    if not isinstance(program, str) or not program or "\0" in program or "\n" in program or len(program) > 500:
        return False
    if os.path.isabs(program):
        return os.path.isfile(program) and os.access(program, os.X_OK)
    if os.sep in program:
        return False
    return shutil.which(program, path=program_env().get("PATH")) is not None


def _screen_text(backlog, lines=14, most=1600):
    """The last lines a program wrote to its terminal, as plain text: its colours, links' markup and every other control
    character taken out."""
    text = backlog[-8192:].decode("utf-8", "replace") if isinstance(backlog, (bytes, bytearray)) else str(backlog)
    text = _CONTROL.sub("", _ESCAPE.sub("", _ANSI.sub("", _ANSI_OSC.sub("", text.replace("\r\n", "\n")))))
    kept = [line.rstrip() for line in text.split("\n") if line.strip()][-lines:]
    return "\n".join(kept)[-most:]


def _last_lines(text, lines=4, most=600):
    """The last few lines of what git or gh said, joined: where it says why it stopped."""
    said = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    return (" / ".join(said[-lines:]) or "no message")[-most:]


GATE_HOOK_NAME = "proofpane-gate"      # the gate's name in Gemini CLI's and Qwen Code's settings (settings.py)


def gemini_gate_switched_off(home, work):
    """The settings file of the user's or of the repository's that names the gate for Gemini CLI to leave out (its
    `hooksConfig.disabled`, which it unites across every file, so the system settings cannot take a name back out), or
    None. Read as text: a file that names the gate anywhere is taken to, whatever its form."""
    for path in (os.path.join(home, ".gemini", "settings.json"), os.path.join(work, ".gemini", "settings.json")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if GATE_HOOK_NAME in fh.read(1024 * 1024):
                    return path
        except OSError:
            continue
    return None


_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")     # a commit's id, SHA-1 or SHA-256


def _numstat(text):
    """git diff --numstat -z's records: [{path, added, removed}], a binary file's counts None, a rename by its new
    path."""
    files = []
    parts = text.split("\0")
    i = 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if not rec:
            continue
        added, removed, path = rec.split("\t", 2)
        if path == "" and i + 1 < len(parts):      # a rename: its old and new paths follow
            path = parts[i + 1]
            i += 2
        files.append({"path": path, "added": None if added == "-" else int(added),
                      "removed": None if removed == "-" else int(removed)})
    return files


# the settings the worktree check of a continued session reads the files under, whatever the repository's own say:
# git compares each file's mode, where core.fileMode false would have it not, and each name as
# it is written, where core.ignoreCase true would take a hook's readme.md for the commit's README.md; a line ending
# never makes it refuse to read a file (core.safecrlf), so what a hook did is counted rather than git's refusal said.
# core.ignoreStat is pinned false too: without it `add --all` marks each entry it takes as unchanged, and status, asked
# next, trusts the mark; those entries were read from the files that moment, so no test can tell it from its absence
# (a version that drops it passes the tests), and the pin keeps the check from resting on that
_CHECK_PINS = ("-c", "core.ignoreStat=false", "-c", "core.fileMode=true", "-c", "core.ignoreCase=false",
               "-c", "core.safecrlf=false")
# the settings that say how git writes a committed file into a worktree and reads it back (its line endings, a link as
# a link), each with git's own default where the repository sets none: the check reads the files under the values the
# repository had before the worktree was made and its hooks ran (_reading_pins), so a hook that changes one to hide
# what it wrote is not followed, while a repository whose owner set one is read as its own checkout wrote it (else a
# hook that turned off core.symlinks and made a committed link a file, or turned on core.autocrlf and wrote a
# committed file with other line endings, would leave a worktree the check passed). Each is read as
# git itself takes it: core.autocrlf a boolean or "input", core.symlinks a boolean, a name with no value true for both,
# and core.eol as its text (an autocrlf with no value, which git takes as true, read as unset would have a worktree
# git wrote under true refused)
_READING_SETTINGS = (("core.autocrlf", "false", "--type=bool-or-str"), ("core.eol", "native", None),
                     ("core.symlinks", "true", "--type=bool"))
# the check's answer when git could not read the worktree and the filesystem showed nothing else
UNREADABLE = "unreadable"
# the most a small file of git's own that the check reads may hold (a .git file, a worktree's gitdir or commondir)
_GIT_FILE_LIMIT = 4096


def _reading_pins(path):
    """The values of _READING_SETTINGS git gives the checkout at `path`, each as `-c name=value`, for the worktree check
    of a session continued in a new one to read its files under: read in the repository before its worktree is made,
    and in the new worktree once it is, where they must be the same."""
    pins = []
    for name, default, kind in _READING_SETTINGS:
        code, value = git("config", *((kind,) if kind else ()), "--get", name, cwd=path, ok=(0, 1))
        pins += ["-c", f"{name}={value.strip() if code == 0 and value.strip() else default}"]
    return tuple(pins)


def _small_git_file(folder, name):
    """The text of `name`, a small file of git's own in the folder `folder` (a .git file, a worktree's gitdir or its
    commondir), read as _read_in reads a file: the folder and the file no link, the open never waiting on a pipe, a
    regular file as opened, of at most _GIT_FILE_LIMIT bytes; None where it is not so (a gitdir made a pipe would
    hold the check, and the press with it, for as long as nothing wrote to it)."""
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        data = _read_in(fd, name, _GIT_FILE_LIMIT)
    except (WorkspaceError, OSError):
        return None
    finally:
        os.close(fd)
    return os.fsdecode(data) if len(data) <= _GIT_FILE_LIMIT else None


def _own_git_dir(work, common):
    """The worktree's own git folder, as `git worktree add` made it: the worktree a folder, not a
    link, and its `.git` a file naming one folder of the repository's worktrees (under `common`, the repository's git
    folder), which names this `.git` back (its gitdir) and `common` as the repository whose objects, branches and
    settings it shares (its commondir: a hook that pointed it at a repository of its own would have the new session
    commit there). Each file read as _small_git_file reads it. None when it
    is not so: a `.git` folder, a link, a file naming another repository's, a folder sharing another repository, or a
    file of these that is not a small regular file, which the new session's programs would take for theirs."""
    dot = os.path.join(work, ".git")
    text = _small_git_file(work, ".git")
    if text is None or not text.startswith("gitdir: ") or not text.endswith("\n") or text.count("\n") != 1:
        return None
    admin = os.path.realpath(os.path.join(work, text[len("gitdir: "):-1]))
    if os.path.dirname(admin) != os.path.realpath(os.path.join(common, "worktrees")):
        return None
    back, shared = _small_git_file(admin, "gitdir"), _small_git_file(admin, "commondir")
    if back is None or shared is None or os.path.realpath(os.path.join(admin, back.strip())) != os.path.realpath(dot):
        return None
    return admin if os.path.realpath(os.path.join(admin, shared.strip())) == os.path.realpath(common) else None


def _unlisted(work, sha, args):
    """What the worktree holds beside the commit that git does not list, read from the filesystem itself:
    git skips an empty folder, any entry named `.git` and the folder that holds one, and every entry that is
    neither a file, a link nor a folder, and it asks a submodule's own index and settings what that holds. Each path
    counted: an entry that is not a file, a link or a folder (a pipe, a socket, a device); a folder the commit does not
    hold that holds nothing; a folder that holds a `.git`, counted as itself (a repository of its own); a submodule's
    folder that holds anything, counted as itself (a new worktree leaves it empty); a file or a link the commit does not
    hold (git lists most of these too, and a path in both counts once); and a file whose content another name shares (a
    hard link), which a write by that name would change after the check. The worktree's own `.git` is _own_git_dir's;
    a file the commit holds and the worktree lacks is git's to count. Names are compared as the filesystem gives them,
    git's own read as its bytes are (decoded with stand-ins for what is not
    UTF-8, two such names would be one)."""
    _, listing = git(*args, "ls-tree", "-r", "-t", "-z", "--full-tree", sha, cwd=work, errors="surrogateescape")
    blobs, trees, gitlinks = set(), set(), set()
    for entry in listing.split("\0"):
        if entry:
            meta, _, path = entry.partition("\t")
            kind = meta.split(" ")[1]
            (trees if kind == "tree" else gitlinks if kind == "commit" else blobs).add(path)
    found, stack = set(), [""]
    while stack:
        rel = stack.pop()
        with os.scandir(os.path.join(work, rel) if rel else work) as entries:
            listed = list(entries)
        for e in listed:
            if not rel and e.name == ".git":
                continue
            path = f"{rel}/{e.name}" if rel else e.name
            mode = e.stat(follow_symlinks=False)
            if stat.S_ISDIR(mode.st_mode):
                inside = os.listdir(e.path)
                if path in gitlinks:
                    if inside:
                        found.add(path)
                elif path not in trees and not inside:
                    found.add(path)
                elif any(n.lower() == ".git" for n in inside):
                    found.add(path)
                else:
                    stack.append(path)
            elif stat.S_ISREG(mode.st_mode):
                if path not in blobs or mode.st_nlink > 1:
                    found.add(path)
            elif stat.S_ISLNK(mode.st_mode):
                if path not in blobs:
                    found.add(path)
            else:
                found.add(path)
    return found


def _kept_out(path):
    """What the repository's own checkout at `path` keeps out of its worktree: the paths its index marks skip-worktree
    (git ls-files -t's "S") that are not there, as a sparse set leaves them, and as `git worktree add` leaves them in a
    new worktree too, since it copies the sparse set of the checkout it runs in; a file the owner only marked, which is
    there, is not kept out (else a hook that marked such a file in the new
    worktree and removed it there would pass). Read before a continued session's worktree is made and its repository's
    hooks run, names as the filesystem gives them; an empty set where git cannot say (a repository
    with no index)."""
    code, out = git("ls-files", "-t", "-z", cwd=path, ok=(0, 128), errors="surrogateescape")
    if code != 0:
        return set()
    return {entry[2:] for entry in out.split("\0")
            if entry.startswith("S ") and not os.path.lexists(os.path.join(path, entry[2:]))}


def _worktree_folders(common):
    """The names of the folders under the repository's git folder `common` that hold one worktree's own git folder
    each (`worktrees`); none where it has none."""
    try:
        return set(os.listdir(os.path.join(common, "worktrees")))
    except OSError:
        return set()


def _differs_from(work, sha, branch, common, kept_out=frozenset(), pins=()):
    """The worktree check of a session continued in a new one: None while `work` holds exactly the commit
    `sha` on the branch `branch` and nothing beside it; else what is not so, in a few words that name no file (a name is
    text a program or the repository chose), or UNREADABLE when git could not read it and nothing else showed. What the
    repository's own hooks did as the worktree was made shows here, as far as it is there when the check reads it; a
    program that runs as the workspace's user (the old session's, which is left running, or one a hook left behind) can
    still write there afterwards, and nothing outside the worktree is read. In order:
    the worktree's `.git` is the file `git worktree add` wrote, naming its own folder of the
    repository's worktrees (`common` is the repository's git folder), and every git below is given that folder and this
    worktree by name, as a task's checkpoint does (task_attempt.build_tree), so no setting of the repository's
    (core.worktree among them) points it elsewhere, while the git a session's programs run there, given nothing, must
    take this worktree for its own; its HEAD is the branch it was made on, at the commit; the filesystem itself holds
    nothing git does not list (_unlisted); the files are read into a temporary index made from the commit
    under _CHECK_PINS, and that tree compared with the commit's; git's status, asked against the same
    index, names what its tree cannot hold, a file git ignores among it, the tree's comparison a second
    reading of what status lists as staged; the worktree's own index must hold the commit's entries, a change staged
    there being one a commit in the new session would take. A file the worktree lacks is not counted when
    nothing but git's status shows it, as a file of the commit that is not there, its own index marks it skip-worktree,
    and the repository's own checkout kept it out too before the worktree was made (`kept_out`, _kept_out), as a
    repository its owner keeps sparse lacks it; one a hook's sparse set or mark keeps out is counted, and so
    is any change of the worktree's own index for it, staged where a commit would take it.
    Each path counts once. The files are read under `pins`, the line-ending and link settings the repository had
    before the worktree was made (_reading_pins), and through its own attributes, as git reads them: a filter,
    a line-ending rule or `ident` that gives the commit back from what a hook wrote is not seen,
    and a hook can set such an attribute. A GitHeld, git's places changed while an agent's run worked, is raised as
    it is, its words the owner's to read. Every git below reads the commit's own objects, never what a
    replace ref (`git replace`, in the repository's git folder, which a hook can write) gives in their place, as a
    task's checkpoint is read (task_runs.py); and the line-ending and link settings git gives the worktree itself must
    be the ones read before it was made, since its programs read its files under those (else
    a hook that replaced the commit with one holding its own words, and wrote them, would pass; so would a file read
    under the main checkout's own config.worktree, which the new worktree does not have)."""
    admin = _own_git_dir(work, common)
    if admin is None:
        return "its git folder is not the one it was made with"
    # the git the session's programs run there, given nothing (as they are), must take this worktree for its own: a
    # setting of the repository's (core.worktree, which extensions.worktreeConfig lets a worktree have) would have their
    # git, and a Finish, see another; a git that cannot say is a worktree that could not be read (the caller's except)
    _, top = git("rev-parse", "--show-toplevel", cwd=work)
    if os.path.realpath(top.strip()) != os.path.realpath(work):
        return "its settings name another work tree"
    # what that git reads the files under, there: a hook's change of the new worktree's own config.worktree (which git
    # copies from the main checkout's as it makes the worktree), or an include for the new branch alone, can give it
    # values the reading before the worktree did not; the words name both, since an owner's include that
    # applies to some branches or worktrees alone, where it gives the new worktree another value, refuses every
    # continue
    if pins and _reading_pins(work) != tuple(pins):
        return ("git reads its files under other line-ending or link settings than its repository's checkout, as when "
                "one is changed in the new worktree or set by an include of git's settings that applies to some "
                "branches or worktrees alone")
    args = (f"--git-dir={admin}", f"--work-tree={work}", "--no-replace-objects")
    _, head = git(*args, "rev-parse", "HEAD", cwd=work)
    if head.strip() != sha:
        return "its HEAD is another commit"
    code, ref = git(*args, "symbolic-ref", "-q", "HEAD", cwd=work, ok=(0, 1))
    if code != 0 or ref.strip() != f"refs/heads/{branch}":
        return "its HEAD is not the branch it was made on"
    found = _unlisted(work, sha, args)
    folder = tempfile.mkdtemp(prefix="pp-continued-")
    reading = (*_CHECK_PINS, *pins)
    try:
        index = {"GIT_INDEX_FILE": os.path.join(folder, "index")}
        git(*args, *reading, "read-tree", sha, cwd=work, env=index)
        git(*args, *reading, "add", "--all", cwd=work, env=index)
        _, tree = git(*args, *reading, "write-tree", cwd=work, env=index)
        _, out = git(*args, "--no-optional-locks", *reading, "status", "--porcelain", "-z", "--no-renames",
                     "--untracked-files=all", "--ignored", "--ignore-submodules=none", cwd=work, env=index,
                     errors="surrogateescape")
        _, changed = git(*args, "diff-tree", "-r", "--no-renames", "--name-only", "-z", sha, tree.strip(), cwd=work,
                         errors="surrogateescape")
        _, staged = git(*args, "diff-index", "--cached", "--no-renames", "--name-only", "-z",
                        "--ignore-submodules=none", sha, cwd=work, errors="surrogateescape")
        _, tagged = git(*args, "ls-files", "-t", "-z", cwd=work, errors="surrogateescape")
    except GitHeld:
        raise
    except WorkspaceError:
        # git refused to read what is there (a pipe where a file was, a repository with no commit, a name git keeps
        # for itself): what the filesystem showed is said, else that it could not be read; never git's own words,
        # which may name a file
        if not found:
            return UNREADABLE
        changed = staged = out = tagged = ""
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    # an entry of status's is "XY <path>", a folder it names whole (an ignored repository of its own) ending in "/",
    # which the filesystem's reading of the same folder does not (else it would count twice)
    listed = {entry[3:].rstrip("/"): entry[:2] for entry in out.split("\0") if entry[3:]}
    shown = found | {p for p in changed.split("\0") if p} | {p for p in staged.split("\0") if p} \
        | {p for p, xy in listed.items() if xy != " D"}
    # what nothing but status shows, a file of the commit that is not there: not counted where a sparse set keeps it out
    lacking = {p for p, xy in listed.items() if xy == " D" and p not in shown}
    sparse = {entry[2:] for entry in tagged.split("\0") if entry.startswith("S ")}
    count = len(shown) + len({p for p in lacking
                              if not (p in sparse and p in kept_out and not os.path.lexists(os.path.join(work, p)))})
    if count:
        return (f"{count} file{'' if count == 1 else 's'} {'differs' if count == 1 else 'differ'} from the commit "
                "(changed, new, removed or ignored)")
    return None


def _uncommitted(work):
    """Whether the worktree holds anything its last commit does not (changed, new and not ignored, or deleted): what a
    Finish would commit. Asked without git's optional locks, so the session's own index is not written."""
    _, out = git("--no-optional-locks", "status", "--porcelain", "--untracked-files=normal", cwd=work)
    return bool(out.strip())


def _size(files):
    """A change's size from its files (_numstat): {files, added, removed}, a binary file a file with no lines."""
    return {"files": len(files), "added": sum(f["added"] or 0 for f in files),
            "removed": sum(f["removed"] or 0 for f in files)}


class Workspace:
    def __init__(self, root, claude_settings=None, max_sessions=MAX_SESSIONS, max_diff=MAX_DIFF,
                 claude_first_run=False, claude_updates=False, gemini_settings=None, qwen_settings=None,
                 link_repos=False, records_key=None, ade_hook=None):
        self.root = os.path.abspath(root)
        # the folder of the approval hook's two parts (settings.ADE_HOOK_FOLDER, given by a workspace
        # machine's start.sh), or None: under the local provider, which is left as it is. With it, every claude
        # terminal and the owner's own chats are given the hook (ade_hook_settings), and none starts without it
        self.ade_hook = ade_hook
        # what the agents' runs' records are signed with (agents.records_key), made from the workspace's secret
        self.records_key = records_key
        # whether a repository already on this machine may be added where it is (link): only where the workspace runs
        # on the person's own machine, as the local provider's does; a workspace machine has no folder of theirs
        self.link_repos = link_repos
        # Gemini CLI's and Qwen Code's system settings (settings.gemini_settings, qwen_settings), named to each of their
        # sessions when they are not at the program's own system path: under the local provider; a workspace machine
        # has them in /etc/gemini-cli and /etc/qwen-code
        self.gemini_settings = gemini_settings
        self.qwen_settings = qwen_settings
        self.repos_dir = os.path.join(self.root, "repos")
        self.sessions_dir = os.path.join(self.root, "sessions")
        # more than one sign-in to Claude Code or Codex, each in a folder of its own (accounts.py)
        self.accounts = accounting.Accounts(self.root)
        os.makedirs(self.repos_dir, exist_ok=True)
        os.makedirs(self.sessions_dir, exist_ok=True)
        # Claude Code's first-run choices (first_run.py), in a workspace machine only: its HOME is the workspace
        # user's, where under the local provider it is a person's own
        self.claude_first_run = claude_first_run
        if claude_first_run:
            repos = [os.path.realpath(os.path.join(self.repos_dir, n)) for n in sorted(os.listdir(self.repos_dir))
                     if os.path.isdir(os.path.join(self.repos_dir, n))]
            first_run.seed(os.path.expanduser("~"), os.path.realpath(self.root), repos)
        self.claude_settings = claude_settings
        # each Claude Code account's organisation as its `auth status` last gave it (claude()), for the task backends
        # list (task_runs.py), which gives it beside the email and plan; kept apart so claude()'s answer stays as it was
        self.orgs = {}
        self._capabilities = None       # what the installed claude offers a chat, read once (claude_cli.py)
        # moved on each time the build in use changes (update_claude): a read begun before is not kept
        self._capabilities_gen = 0
        self._capabilities_lock = threading.Lock()
        # one read of them at a time: each writes the same wrapper file, and two at once trip over each other's
        self._discover_lock = threading.Lock()
        # what is kept, read again in the background while it is the floor and an ask names an effort, one read at a
        # time (refresh_capabilities): the thread reading it, and when the last such read began
        self._refreshing = None
        self._refresh_began = None
        # an ask is making the first read itself, while nothing is kept (ask_offered): one ask at a
        # time makes it, and any other ask that names an effort meanwhile is refused at once
        self._ask_reading = False
        # the claude every session, chat and check starts: in a workspace machine (--claude-updates) the image's own,
        # or the newer build that passed its check (claude_update.py); elsewhere, the one on the PATH, as before
        self.claude_updates = claude_updates
        self._update_lock = threading.Lock()
        self._mcp_lock = threading.Lock()       # the approvals of the repositories' MCP servers (mcp.py)
        self._record_lock = threading.Lock()    # a session's record is read, changed and written under it (_update)
        self._adopt_locks = {}                  # by session: one adoption of teleport's work at a time (_adopt)
        self._adopt_next = {}                   # by session: when a read next starts one (_adopted)
        self._adopt_fetched = set()             # the sessions whose adoption fetched what they follow once already
        self._fetch_locks = {}                  # by repository: one fetch of it at a time (update_fetch)
        self._save_lock = threading.Lock()      # a file saved from the page: its check and its write (save)
        self.updating = False
        self.update_state = claude_update.load(self.root) if claude_updates else None
        self.claude_floor = shutil.which("claude", path=program_env().get("PATH")) if claude_updates else None
        self.claude_path = self.claude_floor
        if claude_updates:
            # the build the state names, only when it is one in Claude Code's versions folder that the state says passed
            # the check as it now is (claude_update.in_use, file system calls only; claude_update.py says what this
            # holds against); else the image's own, and the next update round checks the build again
            taken = claude_update.in_use(self.update_state, os.path.expanduser("~"))
            if taken:
                self.claude_path = taken
            elif self.update_state["active"]:
                self.update_state.update(active=None, checked=None, last={
                    "at": int(time.time()), "ok": False, "version": None, "why": claude_update.SET_ASIDE,
                    "changed": False})
        self.max_sessions = max_sessions
        self.max_diff = max_diff
        self.terminals = {}
        self.shells = {}                # a session's own shells in its worktree, by number: {session: {n: Terminal}}
        self.chats = {}                 # a chat session's Chat (chat.py), made when it is first asked for
        self.chat_listeners = set()     # the queues of the pages following every chat live
        self._chat_wrapper = None
        self._chosen = {}               # each chat's settings as the owner chose them: what its chat starts with
        self.page_seen = False          # a page's activity since this agent started (page)
        self.last_activity = time.monotonic()
        # agents that ask the person for sessions (asks.py), when the person lets them: off until then; the agent's
        # own address, which a session's `proofpane` calls, is set once it listens (__main__)
        self.asks = asking.Asks()
        self.ask_url = None
        # each session's repository and the session whose ask it came from, as the person's request made it (prepare):
        # what a token is bound to, never read back from the session's record, which its program can write
        self._origins = {}
        # the effort each Claude Code session started from an ask was started with, as the person chose it at Start
        # (none kept for Claude Code's default): what each later start of it is given (start_effort), never
        # read back from its record, which its programs can write. The app keeps its own copy (its record of the
        # answer), as it keeps a session's account, and sends it with every start; it decides each start for which
        # this copy holds none: one after this agent has started again, and every later start of a session the person
        # started at Claude Code's default, the app's copy then saying none too. A first Start whose claude does not
        # offer the effort the person chose is refused, so a session started from an ask holds the person's choice
        self._efforts = {}
        # the model each session the owner's controller started runs (controller.py), given as --model at each
        # of its starts, as its effort is (a terminal session takes none otherwise); from the controller's own record
        # after this agent starts again, never from the session's, which its programs can write
        self._models = {}
        # the version of a claude the gate's floor read for a start, and when, by (that claude, the folder it starts
        # in), until that start's launch takes it (_with_map): the map's sources are given by the version this start
        # runs, never by one read before. A read for a chat, or for an agent's run (which is not given the skills hook),
        # is taken by _chat_start; one for a task's attempt is never taken: it is dropped once older than
        # READ_VERSION_FOR (claude_floor_refusal), and none older is used
        self._read_versions = {}
        # the sessions the list or the map left out, said once each (_say_left_out)
        self._left_out_said = set()
        # the sign-ins this agent started, as the person asked (prepare): only theirs are shown as one (a link, a code,
        # the program's words) or take a code. A session's record says which program it runs, and its programs can
        # write it: one that called itself a sign-in and ran a real one in its own terminal would otherwise put that
        # sign-in's genuine page before the person, and get what they signed in with. Kept here only: a sign-in does
        # not outlive the agent that started it.
        self._sign_ins = {}
        # each account's Claude Code status (claude): the answer kept, with when its read began and
        # when it came; the read under way, with when it began, which a read of the same account joins; and when the
        # workspace last changed the account's sign-in (a sign-out, a code typed, a sign-in ended, the account
        # removed), before which nothing kept or under way is given again. The sign-ins watched after their code (part
        # (c)), by id.
        self._status_lock = threading.Lock()
        self._status_kept = {}
        self._status_under_way = {}
        self._status_changed = {}
        self._status_last = {}          # each account's last answer, however old: what a sign-in's code is typed over
        self._sign_in_watches = {}
        # the session each move started, by the move's seq (prepare): the app's second try at a move gets that session,
        # never a second one (moved_already). Kept here only, for the same reason: a session's record, which names its
        # move, can be written by its programs, and is never taken for this agent's word
        self._moves = {}
        # each session's change size as last worked out (_size_of): {session: (when, size or None, seconds it took)},
        # the sessions waiting for theirs, and whether the thread that works them out runs
        self._sizes = {}
        self._sizes_wanted = set()
        self._sizer = False
        self._sizing = None             # the session whose size is being worked out now
        self._size_lock = threading.Lock()
        # agents (agents.py): chats kept under a name and run again; each run is a chat session whose folder is in
        # its agent's, so the session list never shows it and every session route serves it
        self.agents = agentry.Agents(self)
        # git's places, checked before every git of the workspace's own (gitplaces.py): kept in a file of the root,
        # sealed with a key made from its secret; in memory alone where it has none, as at a first start
        self.places = gitplaces.Checker(self.root if records_key else None, records_key or secrets.token_bytes(32),
                                        suspects=self.agents.suspects, hold=self.agents.hold_for_places,
                                        known=self.agents.known, good=self._known_git_change,
                                        folders=(self.repos_dir, self.sessions_dir, self.agents.root))
        _PLACES[self.root] = _PLACES[os.path.realpath(self.root)] = self.places
        self.agents.stopped_since(self.places.started)
        # the owner's controller (controller.py): its record, which the app reads, and the owner's side, which
        # the app sends; its chat and the sessions it starts are the workspace's own, as every session is
        from . import controller as control
        self.controller = control.Controller(self)
        # the skills its sessions called (skillcalls.py): taken from each session's own file into a
        # record of the workspace's own, which the gate refuses a session's tool call that names; the thread that takes
        # them after a read of the list, and whether it runs
        from . import skillcalls
        self.skill_calls = skillcalls.SkillCalls(os.path.join(self.root, "skills"))
        self._skills_lock = threading.Lock()
        self._take_lock = threading.Lock()      # one take_skill_calls at a time (the thread's and the route's)
        self._skills_running = False
        self._skills_again = False
        self._skills_next = 0.0
        self.places.first_look(self._git_tops(), trusted=not self.agents.stopped_at_start)
        # the improve loop's proposals for the repositories' business skills, and the bar on the starts it makes
        # unattended (skill_improve.py)
        from . import skill_improve
        self.skill_improve = skill_improve.SkillImprove(self)
        # the usage its sessions report to the owner's Proofpane organisation once the workspace is paired
        # (usage_report.py): read on a thread of its own, after a session's activity and at the agent's
        # start, never by a session's own program
        self.usage_report = usage_report.Reporter(self)
        # what a start's budget check said where it could not be made, or not all of it read (budget_check.py),
        # by the session it was asked for, until that start's answer and its log say it (launch,
        # _chat_send): set or cleared by each check, taken once, kept in this process's memory only
        self._budget_notes = {}
        self._budget_lock = threading.Lock()

    def _git_tops(self):
        """Each folder there now that the workspace runs git in: its repositories, its sessions' worktrees and its
        agents' (gitplaces.Checker.first_look)."""
        folders = [os.path.join(self.repos_dir, n) for n in sorted(os.listdir(self.repos_dir)) if _NAME.match(n)]
        folders += [os.path.join(self.sessions_dir, n, "work") for n in sorted(os.listdir(self.sessions_dir))]
        return folders + self.agents.worktrees()

    def _git_made(self, top, how=None):
        """git runs in `top`, a folder the workspace has just made or added: known to the record of git's places as
        having the git folder it has now; and, for a repository new to the workspace (`how`), its places accepted as
        they are."""
        found = self.places.expect(top)
        if found is not None and how:
            self.places.accept(found[1], how)

    def _known_git_change(self, common, changes, found):
        """Whether a change of git's places is one the workspace knows for harmless (gitplaces.Checker): GitHub's
        sign-in (gh auth setup-git) setting git's credential helper for github.com to gh itself, or taking it away."""
        if common != gitplaces.USER:
            return False
        gh = shutil.which("gh", path=program_env().get("PATH"))
        if not gh:
            return False
        allowed = {"", f"!{gh} auth git-credential", f"!{os.path.realpath(gh)} auth git-credential"}
        for change in changes:
            if change not in ("config credential.https://github.com.helper",
                              "config credential.https://gist.github.com.helper"):
                return False
            if not set(json.loads(found.get(change) or "[]")) <= allowed:
                return False
        return bool(changes)

    def page(self):
        """A page did something here since this agent started (not the app's own mirroring of the agents)."""
        self.page_seen = True

    def touch(self):
        """Something happened: terminal input or output, or a request that changes something (__main__'s idle exit)."""
        self.last_activity = time.monotonic()

    def activity_of(self, session_id):
        """What a session's terminal or chat calls at its activity (its output, its chat's events: a turn's end among
        them): the agent's activity (touch), and the session's usage read again once it is quiet (usage_report.py)."""
        def on_activity():
            self.touch()
            self.usage_report.nudge(session_id)
        return on_activity

    # agents that ask for sessions (asks.py, command.py)

    def asks_on(self):
        """Whether the person lets this workspace's agents ask for sessions: off until they turn it on. Held in this
        process's memory only, never in a file: a session's programs run as this agent's user and could write one.
        The app keeps the person's choice and gives it again to an agent that started since (workspace_routes)."""
        return self.asks.enabled

    def set_asks_on(self, on):
        """Turn agents' asks on or off (the app's call, with the workspace's secret); turned off, the asks waiting
        and the programs' tokens go with it, at once (asks.Asks.set_enabled)."""
        if not isinstance(on, bool):
            raise WorkspaceError("Say on or off.")
        self.asks.set_enabled(on)
        return self.asks_view()

    def asks_view(self):
        """The person's view: whether asks are on, and each ask waiting, with the session that asked."""
        out = []
        for ask in self.asks.waiting():
            try:
                record = self._read(ask["from"])
                by = {"id": record["id"], "title": record.get("title") or record["task"].split("\n")[0][:200],
                      "program": record["program"]}
            except (NotFound, ValueError):
                by = {"id": ask["from"], "title": None, "program": None}
            try:
                label = self.label(ask["repo"])
            except WorkspaceError:
                label = ask["repo"]         # its repository is gone: starting it will say so
            out.append(dict(ask, by=by, label=label))
        return {"on": self.asks_on(), "asks": out}

    def _command_launcher(self):
        """The `proofpane` command a session's program finds on its PATH: command.py, standard library only, run by the
        agent's own Python. Written each time, so it names this Python and this command.py. (Not `pp`: macOS has a
        /usr/bin/pp of its own.)"""
        bin_dir = os.path.join(self.root, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "command.py")
        path = os.path.join(bin_dir, "proofpane")
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(script)} \"$@\"\n")
        os.chmod(path + ".tmp", 0o755)
        os.replace(path + ".tmp", path)
        return bin_dir

    def asker(self, token):
        """What a token was issued for ({"session", "repo", "may_ask"}), while asks are on and its session's program
        runs; else None."""
        bound = self.asks.bound_to(token)
        if bound is None:
            return None
        term = self.terminals.get(bound["session"])
        return bound if term is not None and term.running else None

    def _ask_state(self, view):
        """A session's state as `proofpane` says it, and what it waits for, by name: never a command."""
        if view.get("chat"):
            state = {"running": "working", "needs_you": "waiting", "idle": "idle"}.get(view["chat"].get("state"),
                                                                                       "stopped")
            return state, ("the person" if state == "waiting" else None)
        if view["running"]:
            act = view.get("activity") or {}
            state = act.get("state") if act.get("state") in ("working", "waiting", "idle") else "running"
            waits = (f"the person: {act['tool']}" if isinstance(act.get("tool"), str) and act["tool"] else "the person") \
                if state == "waiting" else None
            return state, waits
        if view.get("exit_code") is None:
            return "stopped", None
        return ("done" if view["exit_code"] == 0 else "failed"), None

    def _repo_of(self, view):
        """A session's repository as it was made (prepare), else as its record says (one made before this agent)."""
        origin = self._origins.get(view["id"])
        return origin["repo"] if origin is not None else view["repo"]

    def _ask_line(self, view, bound):
        state, waits = self._ask_state(view)
        origin = self._origins.get(view["id"])
        return {"id": view["id"], "program": view["program"], "state": state, "waits_for": waits,
                "title": view.get("title") or None, "task": view["task"][:2000], "branch": view.get("branch"),
                "asked_by": origin["asked_by"] if origin is not None else view.get("asked_by"),
                "you": view["id"] == bound["session"]}

    def ask_sessions(self, bound):
        """The sessions `proofpane sessions` lists: those on the repository the token was issued for, and none other."""
        repo = bound["repo"]
        return {"sessions": [self._ask_line(v, bound) for v in self.sessions() if repo and self._repo_of(v) == repo]}

    def _same_repository(self, bound, session_id):
        try:
            view = self.session(check_id(session_id))
        except NotFound:
            view = None
        if view is None or not bound["repo"] or self._repo_of(view) != bound["repo"]:
            raise NotFound("No such session on this session's repository.")
        return view

    def ask_session(self, bound, session_id):
        return self._ask_line(self._same_repository(bound, session_id), bound)

    def ask_diff(self, bound, session_id):
        view = self._same_repository(bound, session_id)
        return self.changes(view["id"])

    def ask_offered(self, program, effort):
        """What an ask needs read of the installed claude before it is taken: what that claude offers, as the
        workspace keeps it (capabilities, the list a chat's effort is checked against), for a Claude Code ask that names
        an effort, and None for any other. While what is kept is the floor (or not what capabilities gives), the ask is
        refused with it at once, in words saying to ask again in a minute (asks.READ_AGAIN), and the claude is read
        again in the background (refresh_capabilities), at most once a minute, its read kept for the asks after it
        (not again in each such ask's request, which could outlast `proofpane ask`'s
        wait). While nothing is kept yet, at the agent's start or after an update of Claude Code emptied it
        (_use_claude), one ask at a time makes the first read, here, through capabilities(), and waits for it, for as
        long as claude's --version, its --help and the SDK's handshake take; an ask that meets a read under way then,
        that first read (an ask's, or one the page's card or another caller of capabilities() makes) or a read in the
        background, gets asks.READING in place of what claude offers, and is refused at once in asks.BEING_READ,
        starting no read of its own (were every such
        ask to wait for the read, each in a thread of the agent's, one session's asks could fill them). So the agent's
        route asks this off the event loop, then reads the asking session's binding again and takes the ask (ask) with
        nothing awaited between: a session removed during the read, its token revoked and its asks withdrawn, then asks
        nothing."""
        if program != "claude" or effort in (None, ""):
            return None
        first = False
        with self._capabilities_lock:
            if self._capabilities is None:
                if self._ask_reading or self._discover_lock.locked():
                    return asking.READING
                self._ask_reading = first = True
        try:
            offered = self.capabilities()
        finally:
            if first:
                with self._capabilities_lock:
                    self._ask_reading = False
        if not isinstance(offered, dict) or offered.get("floor"):
            self.refresh_capabilities()
        return offered

    @contextlib.contextmanager
    def taking_ask(self, bound, program, task, base=None, effort=None):
        """While an ask is being taken: the agent's route holds this from receiving the ask until
        `ask` has recorded it or refused it, ask_offered's read among the waits, and the same ask from the same session
        meanwhile, its program, task, base and effort the same as `ask` would record them, is refused in words saying
        that the first is still being taken and that `proofpane asks` shows it once it is, but never one the workspace
        refuses, so asking again then gives the reason (asks.BEING_TAKEN): a `proofpane ask` that
        gave up waiting, and an agent that asks again, are not given two asks for one."""
        try:
            key = self.asks.start_taking(bound, program, task, base, effort)
        except asking.AskError as e:
            raise WorkspaceError(str(e)) from None
        try:
            yield
        finally:
            self.asks.done_taking(key)

    def ask(self, bound, program, task, base=None, effort=None, offered=None):
        """An ask from a session's agent (`bound`: what its token was issued for) for a new session on its repository,
        waiting for the person; on the asking session's record (asks.jsonl), as its answer will be. An effort it names
        is checked against `offered`, what ask_offered gave of the installed claude as the workspace keeps it,
        and refused while that is None or the floor, and at once while it is asks.READING, a read
        of it under way; nothing here reads claude, so the agent's
        route calls this in the event loop, right after it reads
        the asking session's binding again. The checks are here and in asks.py, whatever program posted the ask."""
        try:
            ask = self.asks.ask(bound, program, task, base, effort, offered)
        except asking.AskError as e:
            raise WorkspaceError(str(e)) from None
        self._ask_log(bound["session"], {"event": "asked", "ask": ask["id"], "program": ask["program"],
                                         "task": ask["task"], "base": ask["base"], "effort": ask["effort"]})
        return ask

    def asks_of(self, bound):
        """The asking session's own asks, oldest first: each waiting for the person, starting (the person's Start holds
        it, and puts it back to wait again if it fails: asks.Asks.take, not saying "gone"
        meanwhile; and their refusal holds it so too, until the refusal is written), started (with
        the session it started), refused, or gone (not answered within a day, or withdrawn). From the asks waiting and
        being started, read before and again after the session's own record of them (asks.jsonl): an ask is held before
        its asking is written, and an answer, a Start's or a refusal, is written before the ask is let go, so neither a
        new ask nor one being answered reads as gone between the two. Each says the effort it
        asked for (`effort`) and, once started, the effort its session started with (`started_effort`), which is the
        person's choice: kept apart, so the asking agent can tell whether it got what it asked for; none in
        either is the program's own default. `started_effort` is what the session's first start gave it, the person's
        choice, since a first Start whose claude does not offer that choice is refused; a later
        start that gives no effort says so to the person, in its answer, and not here."""
        before = self.asks.states_of(bound["session"])
        log = self.ask_log(bound["session"])
        after = self.asks.states_of(bound["session"])
        answered = {e["ask"]: e for e in log if e["event"] in ("started", "refused")}
        out = []
        for e in log:
            if e["event"] != "asked" or not isinstance(e.get("ask"), str):
                continue
            done = answered.get(e["ask"])
            state = after.get(e["ask"]) or before.get(e["ask"]) or (done["event"] if done else "gone")
            started = done if done and done["event"] == "started" else None
            out.append({"id": e["ask"], "program": e.get("program"), "task": (e.get("task") or "")[:2000],
                        "base": e.get("base"), "effort": e.get("effort"), "at": e["at"], "state": state,
                        "session": started.get("session") if started else None,
                        "started_effort": started.get("started_effort") if started else None})
        return {"asks": out}

    def ask_answered(self, ask, event, session=None, effort=None):
        """The person's answer to an ask, on the asking session's record: `started` (with the session it started, and
        the effort it started with: none for the program's own default) or `refused`. Written first, and only
        then is the ask, taken by a Start or by the person's refusal, no longer held as being started
        (asks.Asks.answered), so the asking session reads it as starting until the answer is
        written, and the answer after."""
        entry = {"event": event, "ask": ask["id"], "program": ask["program"], "session": session}
        if event == "started":
            entry["started_effort"] = effort
        self._ask_log(ask["from"], entry)
        self.asks.answered(ask["id"])

    def effort_of(self, session_id):
        """The effort a Claude Code session started from an ask was started with, as this agent keeps it:
        None for Claude Code's default, for any other session, and for one started before this agent started."""
        return self._efforts.get(session_id)

    def _ask_log(self, session_id, entry):
        try:
            with open(os.path.join(self._dir(session_id), "asks.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(entry, at=int(time.time())), ensure_ascii=False) + "\n")
        except OSError:
            pass            # the session is gone: its asks went with it

    def ask_log(self, session_id, limit=200):
        """What this session's agent asked for and how the person answered (asks.jsonl), newest last."""
        out = []
        try:
            with open(os.path.join(self._dir(session_id), "asks.jsonl"), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - ASK_LOG_TAIL))    # its last part only: a session's program could grow it
                lines = fh.read().decode("utf-8", "replace").splitlines()
                if size > ASK_LOG_TAIL:
                    lines = lines[1:]                   # the first line read is a part of one
                lines = lines[-limit:]
        except OSError:
            return out
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and isinstance(e.get("at"), int) and e.get("event") in ("asked", "started", "refused"):
                out.append({k: e.get(k) for k in ("at", "event", "ask", "program", "task", "base", "session", "effort",
                                                  "started_effort")})
        return out

    # repositories

    def _repo_path(self, name):
        path = os.path.join(self.repos_dir, check_name(name))
        if not os.path.exists(os.path.join(path, ".git")) or name not in os.listdir(self.repos_dir):
            raise NotFound(f"This workspace has no repository {name}.")
        return path

    def repos(self):
        out = []
        for name in sorted(os.listdir(self.repos_dir)):
            path = os.path.join(self.repos_dir, name)
            if _NAME.match(name) and os.path.islink(path) and not os.path.exists(os.path.join(path, ".git")):
                # added from this machine, its folder moved or gone: listed, so the person can take it out
                out.append({"name": name, "label": f"workspace/{name}", "branch": None, "head": None,
                            "linked": os.readlink(path), "missing": True, "slug": None, "address": None})
                continue
            if not _NAME.match(name) or not os.path.exists(os.path.join(path, ".git")) or agentry.is_scratch(name):
                continue
            # one that git cannot read (its HEAD missing, say), or that goes while it is read (taken out meanwhile), is
            # left out, not the listing failed for it
            try:
                code, head = git("rev-parse", "--verify", "--quiet", "HEAD", cwd=path, ok=(0, 1, 128))
                whole, branch = git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=path, ok=(0, 1, 128))
                if code == 128 or whole == 128:
                    continue
                # whether it has a remote to push to, and whether that is GitHub's: Ship pushes there and opens its pull
                # request on GitHub, so without one (or with another) only Finish is offered
                there, url = git("remote", "get-url", "origin", cwd=path, ok=(0, 1, 2, 128))
            except GitHeld as e:
                # its slug and address unknown while no git may read its origin (in each
                # entry, null where there is none)
                out.append({"name": name, "label": f"workspace/{name}", "branch": None, "head": None,
                            "linked": os.path.realpath(path) if os.path.islink(path) else None, "held": str(e),
                            "slug": None, "address": None})
                continue
            except (OSError, WorkspaceError):
                continue
            origin = there == 0 and bool(url.strip())
            # its slug and address, which the app records for a task on it (a hand-off pushes there)
            slug, address = origin_fields(url) if origin else (None, None)
            out.append({"name": name, "label": self.label(name), "branch": branch.strip() or None,
                        "head": head.strip() if code == 0 else None,
                        "linked": os.path.realpath(path) if os.path.islink(path) else None,
                        "origin": origin, "github": origin and bool(_GITHUB.match(url.strip())),
                        "slug": slug, "address": address})
        return out

    def link(self, path):
        """Add a repository already on this machine, where it is: a symbolic link in the repositories' folder, named
        after its folder, so a session makes its worktree and its branch pp/<id> from it as `git worktree` does, and
        nothing else in it is changed. Its own folder, never one inside it, nor a worktree of another (its .git is a
        file), nor anything in the workspace. Adding one already added gives it again."""
        if not self.link_repos:
            raise WorkspaceError("Only the desktop app's workspace, on your own machine, adds a repository from a "
                                 "folder: here, clone it.")
        if not isinstance(path, str) or not path.strip() or len(path) > MAX_LINK_PATH or "\x00" in path:
            raise WorkspaceError("Give the repository's folder by its full path.")
        given = os.path.expanduser(path.strip())
        if not os.path.isabs(given):
            raise WorkspaceError("Give the repository's folder by its full path, from / (or ~).")
        real = os.path.realpath(given)
        if os.path.exists(real) and not os.path.isdir(real):
            raise WorkspaceError(f"{given} is a file: give the folder of the repository it is in.")
        if not os.path.isdir(real):
            raise WorkspaceError(f"There is no folder {given} on this machine.")
        root = os.path.realpath(self.root)
        if real == root or real.startswith(root + os.sep) or root.startswith(real + os.sep):
            raise WorkspaceError("That folder holds the workspace, or is in it: add a repository from elsewhere.")
        if not os.path.isdir(os.path.join(real, ".git")):
            # inside a repository: never the one around it on its own (a home folder can be one), but named, to
            # choose; found by its .git folder above, with no git run outside the folder given
            around = next((p for p in _parents(real) if os.path.isdir(os.path.join(p, ".git"))), None)
            if around is not None and not os.path.exists(os.path.join(real, ".git")):
                raise WorkspaceError(f"That folder is inside the repository at {around}: add that folder, which holds "
                                     "its .git.")
            raise WorkspaceError("That folder is not a repository's own: choose the folder that holds its .git "
                                 "folder (a worktree's .git is a file, and is not added).")
        code, top = git("rev-parse", "--show-toplevel", cwd=real, ok=(0, 128))
        try:
            same = code == 0 and os.path.samefile(top.strip(), real)
        except OSError:
            same = False
        if not same:
            raise WorkspaceError("git does not read that folder as a repository.")
        real = os.path.realpath(top.strip())       # as git names it: its case as on disk, whatever was typed
        for name in sorted(os.listdir(self.repos_dir)):
            linked = os.path.join(self.repos_dir, name)
            if os.path.islink(linked) and os.path.realpath(linked) == real:
                return {"name": name, "label": self.label(name), "linked": real}
        # a repository with no commit at all (one on a branch of none yet, beside branches with commits, is taken: a
        # session can start from one of those)
        if not git("rev-list", "-n", "1", "--all", cwd=real, ok=(0, 128))[1].strip():
            raise WorkspaceError("That repository has no commit yet: a session starts from one, so commit something "
                                 "first.")
        base = re.sub(r"[^A-Za-z0-9._-]+|\.{2,}", "-", os.path.basename(real)).strip(".-_")
        while base.lower().endswith(".git"):
            base = base[:-4].strip(".-_")
        base = base[:90].strip(".-_") or "repository"
        for n in range(1, 100):
            name = base if n == 1 else f"{base}-{n}"
            try:
                os.symlink(real, os.path.join(self.repos_dir, check_name(name)))
            except FileExistsError:
                continue
            self._git_made(os.path.join(self.repos_dir, name), "added from this machine by its owner")
            return {"name": name, "label": self.label(name), "linked": real}
        raise WorkspaceError(f"This workspace has too many repositories named {base}.")

    def unlink(self, name):
        """Take a repository added from this machine out of the workspace: its link only, never its folder, and only
        once it has no sessions, whose worktrees are made from it."""
        path = os.path.join(self.repos_dir, check_name(name))
        if name not in os.listdir(self.repos_dir):
            raise NotFound(f"This workspace has no repository {name}.")
        if not os.path.islink(path):
            raise WorkspaceError(f"{name} was not added from a folder on this machine, so it is not taken out here.")
        using = [r["id"] for r in self.sessions() if r.get("repo") == name]
        if using:
            raise WorkspaceError(f"{name} has {len(using)} session{'s' if len(using) > 1 else ''}: remove "
                                 f"{'them' if len(using) > 1 else 'it'} first.")
        os.unlink(path)
        self.places.forget(path)
        with self._mcp_lock:
            # its MCP servers' approvals go with it: the next folder given its name is another's code
            approvals = mcp.load_approvals(self.root)
            if approvals.pop(name, None) is not None:
                mcp.save_approvals(self.root, approvals)
        return {"unlinked": name}

    def clone(self, url):
        """Clone a repository by its https address, into a folder no listing shows (a name starting with a dot is never
        a repository's), moved into place when it is whole: a clone half made is never listed, nor a session started
        from it, and one that fails leaves nothing. Folders of clones that never finished are swept away first."""
        name = check_url(url)
        dest = os.path.join(self.repos_dir, name)
        if os.path.lexists(dest):
            raise WorkspaceError(f"This workspace already has a repository named {name}.")
        for left in os.listdir(self.repos_dir):
            path = os.path.join(self.repos_dir, left)
            try:
                if left.startswith(PARTIAL_CLONE) and time.time() - os.lstat(path).st_mtime > 2 * CLONE_TIMEOUT:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass
        part = tempfile.mkdtemp(prefix=f"{PARTIAL_CLONE}{name}-", dir=self.repos_dir)
        try:
            git("-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "clone", "--quiet",
                "--no-recurse-submodules", "--", url.strip(), part, cwd=self.repos_dir, timeout=CLONE_TIMEOUT)
            try:
                os.rename(part, dest)
            except OSError:
                if os.path.exists(dest):
                    raise WorkspaceError(f"This workspace already has a repository named {name}.") from None
                raise WorkspaceError(f"The clone of {name} could not be put in place.") from None
        except BaseException:
            shutil.rmtree(part, ignore_errors=True)
            raise
        self._git_made(dest, "cloned by the workspace")
        return {"name": name, "label": self.label(name)}

    def label(self, name):
        """The repository as a manifest names it: owner/name when its origin is on GitHub, else workspace/<name>."""
        code, url = git("config", "--get", "remote.origin.url", cwd=self._repo_path(name), ok=(0, 1))
        m = _GITHUB.match(url.strip()) if code == 0 else None
        return m.group(1) if m else f"workspace/{name}"

    def github(self):
        """{"installed", "user", and, when signed in, "repos": the user's repositories, most recently pushed first,
        each with the day it was last pushed and whether this workspace has a clone of it, and "capped": whether there
        may be more than the MAX_GITHUB_PAGES pages listed}, all asked of gh, which uses its own sign-in."""
        if shutil.which("gh", path=program_env().get("PATH")) is None:
            return {"installed": False, "user": None, "repos": [], "capped": False}

        def gh(*args):
            try:
                p = subprocess.run(["gh", *args], cwd=self.root, env=_gh_env(self.root), capture_output=True,
                                   timeout=GITHUB_TIMEOUT, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                return None
            return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None

        login = (gh("api", "user", "--jq", ".login") or "").strip()
        if not login or not _NAME.match(login):
            return {"installed": True, "user": None, "repos": [], "capped": False}
        # a page at a time, as the API gives them: an account can have more than a page
        listed, capped = [], False
        for page in range(1, MAX_GITHUB_PAGES + 1):
            try:
                got = json.loads(gh("api", "-X", "GET", "user/repos", "-f", f"per_page={GITHUB_PAGE}",
                                    "-f", "sort=pushed", "-f", "affiliation=owner,collaborator,organization_member",
                                    "-f", f"page={page}") or "[]")
            except ValueError:
                got = []
            if not isinstance(got, list):
                break
            listed += got
            if len(got) < GITHUB_PAGE:
                break
        else:
            capped = True
        have = {r["name"] for r in self.repos()}
        repos = []
        for r in listed:
            if not isinstance(r, dict) or not isinstance(r.get("full_name"), str):
                continue
            url = f"https://github.com/{r['full_name']}.git"
            try:
                name = check_url(url)
            except WorkspaceError:
                continue
            pushed = r.get("pushed_at")
            repos.append({"full_name": r["full_name"], "private": bool(r.get("private")), "url": url,
                          "description": (r.get("description") or "")[:200], "cloned": name in have,
                          "pushed": pushed[:10] if isinstance(pushed, str) and _PUSHED.match(pushed) else None})
        return {"installed": True, "user": login, "repos": repos, "capped": capped}

    def _claude(self):
        """The claude to start: the build in use, or the one on the PATH; None when there is none."""
        if self.claude_updates:
            return self.claude_path
        return shutil.which("claude", path=program_env().get("PATH"))

    def claude_floor_refusal(self, claude, cwd, env):
        """Why `claude`, started in `cwd` with `env`, starts no claude terminal, chat or agent's run here, or None: none
        there, one older than CLAUDE_ARGS_FLOOR, or one whose version cannot be read, since such a one is taken not to
        run the gate (an older one's behaviour was not measured: CLAUDE_ARGS_FLOOR's comment). Its version is read where
        and as the start runs it, in its folder with its environment, since a
        version manager's shim chooses its build by the folder it runs in (mise and asdf read a pin upward from there,
        which a repository or a session can carry), and at each call, never kept: what a path names can change under it
        (a shim, an install that keeps a file's times), so a claude replaced while the workspace runs is seen at its
        next start. It blocks for as long as `claude --version` takes, so it is asked off the event loop. Against a
        session itself it proves nothing: a program of the session runs as the workspace's user and can put any file
        where the claude is, printing any version."""
        floor = claude_update.version_text(CLAUDE_ARGS_FLOOR)
        if claude is None:
            return "Claude Code is not installed in this workspace."
        version, why = claude_cli.cli_version(claude, env, cwd)
        if version is not None:
            # for the start this read is for, which takes it once at its launch (_with_map), never a later one's; a
            # read for a chat or an agent's run is taken by _chat_start, one for a task's attempt never is, so one older
            # than READ_VERSION_FOR is dropped at each read
            now = time.monotonic()
            for key, (_, at) in list(self._read_versions.items()):
                if now - at > READ_VERSION_FOR:
                    self._read_versions.pop(key, None)
            self._read_versions[(claude, cwd)] = (version, now)
        # what the workspace cannot tell is the version, not that a build runs the gate: from the floor on, Claude
        # Code's settings declare the form the gate is given in, which is read, not measured
        if why == "timeout":
            return (f"Claude Code did not say its version within {claude_cli.TIMEOUT} seconds, so the workspace cannot "
                    f"tell that it is {floor} or later, the first whose settings declare the form the workspace's gate "
                    "is given in: try again.")
        if version is None:
            return (f"Claude Code's version could not be read, so the workspace cannot tell that it is {floor} or "
                    "later, the first whose settings declare the form the workspace's gate is given in: update Claude "
                    "Code, then start it again.")
        if version < CLAUDE_ARGS_FLOOR:
            return (f"Claude Code {claude_update.version_text(version)} is older than {floor}, the first whose settings "
                    "declare the form the workspace's gate is given in (a program and its arguments); what an older "
                    "one does with the gate given so was not tried, so it may not run it: update Claude Code, then "
                    "start it again.")
        return None

    def _claude_start_env(self, session_id, program, model_provider=None):
        """What a session's claude starts with, as launch and the chat give it (its own names, its account's folder,
        another provider's names), for the floor's read of its version."""
        extra = {"PP_SESSION_ID": session_id, "PP_SESSION_DIR": self._dir(session_id)}
        extra.update(self._account_env({"id": session_id, "program": program}))
        if program == "claude" and model_provider in providers.CATALOG:
            extra.update(providers.CATALOG[model_provider]["env"])
        return program_env(extra)

    def claude_floor_for(self, session_id):
        """For a session's start again: (why it is refused, or None; the claude checked). A session of a program that
        runs an agent (BUDGET_CHECKED) asks Proofpane's budget check first (budget_check.py), so that
        every start again asks it, the owner's controller's continue among them: one it refuses is refused in the
        product's own words, before the floor's read runs anything; what its answer says of a check that could not be
        made, or not all of it read, is kept for launch's answer (keep_budget_note). A claude terminal's claude is then
        read in the session's worktree with its environment (claude_floor_refusal); any other program has no floor to
        read, and is answered (None, None) unless its check refused it. The start route asks it off the event loop and
        before the agent's lock, so a slow read holds up no other request, refuses the start from its answer, and has
        launch, which runs in the loop and asks nothing, start the claude checked."""
        try:
            record = self._read(session_id)
        except Exception:  # noqa: BLE001  launch, which follows, says what is wrong with the session
            return None, None
        if record["program"] not in BUDGET_CHECKED:
            return None, None
        refused, note = self._budget_refusal(budget_check.session_kind(record["program"], record.get("model_provider")))
        self.keep_budget_note(session_id, note)
        if refused:
            return refused, None
        if record["program"] != "claude":
            return None, None
        claude = self._claude()
        cwd = self._work(session_id) if record["repo"] else self.root
        return self.claude_floor_refusal(
            claude, cwd, self._claude_start_env(session_id, "claude", record.get("model_provider"))), claude

    @staticmethod
    def _offered_by(claude, cwd, env):
        """What `claude`, run in `cwd` with `env` as a start runs it, offers:
        claude_cli.capabilities of its version and its `--help`, with no SDK handshake (a start passes no model, so the
        models are not wanted). The version is read as the gate's floor reads it, by claude_cli.cli_version, which takes
        Claude Code's own line alone, so a line a wrapper or a shim prints before it, with a version of its own, is not
        taken for it; and one it cannot read, no answer within claude_cli.TIMEOUT or no line of Claude Code's own, gives
        the floor (`floor` true), its help not read, since whether that claude takes ultracode, which claude_cli adds by
        its version, cannot be told. The help is read with claude_cli.run_text, bounded by
        claude_cli.TIMEOUT too, and gives nothing when it does not answer. Read where and as the start runs it, in its
        folder with its environment, for the reason claude_floor_refusal reads the version so (a version manager's shim
        chooses its build by the folder it runs in), and at each call, never kept: what a path names can change under
        it. None when there is no claude to read. It blocks for as long as the two take, so it is asked off the event
        loop. The read is claude_cli.offered's, which the workspace's own read of its claude makes too
        (claude_cli.discover)."""
        if not claude:
            return None
        return claude_cli.offered(claude, env, cwd)

    @staticmethod
    def _offered_effort(effort, offered):
        """For a later start (start_effort): (`effort`, None) when `offered` (_offered_by) lists it among its levels
        and its version and its help were read for them, else (None, the words saying so): asks.NOT_READ_NOW while its
        `floor` holds or nothing was read, asks.NO_LONGER_OFFERED when the level is not listed."""
        if not isinstance(offered, dict) or offered.get("floor"):
            return None, asking.NOT_READ_NOW.format(effort)
        if effort not in (offered.get("efforts") or ()):
            return None, asking.NO_LONGER_OFFERED.format(effort)
        return effort, None

    def _chat_start(self, chat):
        """At each start of a chat's claude, an agent's run's among them (chat.py, before_start, in a thread): why it
        may not start, or None. The claude a start takes now (_claude) is checked where the chat runs it, in its folder
        with its environment (claude_floor_refusal), and the chat wrapper written again to name it, so the claude
        checked is the one the chat starts, however long the chat has been open, unless the wrapper's other writers (a
        chat's making, a judge's turn, the updater) write it between the two, each naming the claude a start takes at
        that moment; that wrapper is one of the places an agent's run's watch looks at (agents.py). None there is
        refused, so the wrapper never falls back to a claude the PATH gives later. Asked here, not where the chat is
        made, where its log is read, so a refusal stops a start, not a reading. A chat of the owner's controller's
        starts its claude once, for its brief and packet, never again on its old conversation (controller.py).
        Proofpane's budget check is asked before the floor's read runs anything (budget_check.py), for
        the owner's own chats and the controller's: one it refuses is refused in the product's own words, and what its
        answer says of a check that could not be made, or not all of it read, is kept for the message that started it
        (keep_budget_note, _chat_send). An agent's run asks it at its own start (agents._start), an attempt of a
        requirement's manager at its (controller.keep_attempts), and neither again here."""
        if self.controller.is_chat(chat.session_id):
            refused = self.controller.start_refusal(chat.session_id)
            if refused:
                return refused
        if not chat.is_run and not self.controller.is_attempt(chat.session_id):
            refused, note = self._budget_refusal(budget_check.SUBSCRIPTION)
            self.keep_budget_note(chat.session_id, note)
            if refused:
                return refused
        claude = self._claude()
        refused = self.claude_floor_refusal(claude, chat.cwd, chat.env)
        if refused:
            return refused
        # Proofpane's core (core_plugin.py) for the owner's own chat, read at each start as a terminal's is; never an
        # agent's run's
        chat.plugins = [] if chat.is_run else self.core_plugins(
            chat.env.get("CLAUDE_CONFIG_DIR") or accounting.default_folder("claude"))
        if self.controller.is_chat(chat.session_id):
            # the owner's controller's: its core, never the daemon's companion, whose MCP server's tools open paths
            # (it has no MCP server but its own, whether or not a claude's --strict-mcp-config leaves a plugin's out)
            chat.plugins = [p for p in chat.plugins if p != core.DAEMON_PLUGIN]
        # the skills hook for each of the owner's own chats, by the version this start's check just
        # read: a chat's options are made before the check, so its settings take the hook here, as its plugins do, and
        # the check's read is taken now, as a terminal's start takes its own (_with_map). Not an agent's run (counted at
        # its end by metrics.py) and not the controller's chat, which has no built-in tool, so no `Skill` call, and
        # which a typed skill would not count for either. These two are the whole of the exclusion: a third kind of chat
        # is given the hook unless its builder adds it here
        version, at = self._read_versions.pop((claude, chat.cwd), (None, 0))
        if time.monotonic() - at > READ_VERSION_FOR:
            version = None
        chat.skills = {} if chat.is_run or self.controller.is_chat(chat.session_id) else settings_made.skills_settings(
            sys.executable, os.path.dirname(os.path.abspath(__file__)), version)
        # the approval hook for each of the owner's own chats in a hosted workspace, read at each start:
        # never an agent's run, a replay among them, which keeps its unattended mode, no one being there to approve,
        # and never the controller's chat, which has no built-in tool to hold. Told apart here, where the chat starts,
        # and never by the hook from its environment, which a repository's settings can set (gate.py reads a run's
        # marker so, where claiming to be a run only adds refusals); a task's attempt never comes here
        # (task_attempt.py)
        chat.held = {} if chat.is_run or self.controller.is_chat(chat.session_id) else (self.ade_hook_settings() or {})
        chats.write_wrapper(self._chat_wrapper, PASS_ENV, claude=claude)
        return None

    def core_status(self):
        """What the page shows of Proofpane's core (core_plugin.status): the pairing as the owner may see it, what the
        managed settings leave out for the default Claude Code account and each one added, and the usage reported
        (`usage`: the rows posted and dropped, and the last drops with why, from usage_report.py's record)."""
        folders = {accounting.DEFAULT: accounting.default_folder("claude")}
        for account in self.accounts.all()["accounts"]:
            if account["kind"] == "claude":
                folders[account["id"]] = self.accounts.folder(account)
        out = core.status(os.path.expanduser("~"), bool(self.link_repos), folders)
        # what the usage reporting has done (usage_report.py): rows posted and dropped, the last drops with why
        out["usage"] = self.usage_report.status()
        return out

    def core_plugins(self, account_folder):
        """The plugin folders a claude start with this account folder is given, each named with --plugin-dir
        (core_plugin.given: none until the workspace is paired with the owner's Proofpane organisation, none where a
        managed setting refuses the flag, the daemon's companion only on the person's own machine): launch's for a
        terminal, _chat_start's for a chat. File reads only, so it is asked where the start is made; and none at all
        when anything in that reading fails, since a session can write some of what it reads (an account's cache),
        and a start goes on without the plugin rather than not at all."""
        try:
            return core.given(os.path.expanduser("~"), account_folder, bool(self.link_repos))[0]
        except Exception:  # noqa: BLE001  the start goes on, without the plugin
            return []

    # Proofpane's budget check before a start (budget_check.py)

    def budget(self, kind):
        """Proofpane's word on a start of this kind of spend (budget_check.check), for the pairing kept in this agent's
        home, read as the plugin's is (core_plugins): nothing is asked while the workspace is not paired. Blocking, for
        budget_check.WAIT seconds at most, so a start made on the event loop asks it in a thread. Never raises."""
        return budget_check.check(os.path.expanduser("~"), kind)

    def _budget_refusal(self, kind):
        """A start's budget check (budget): (Proofpane's own words for a start it refuses, or None; what the start says
        where the check could not be made or not every budget was read, or None)."""
        word = self.budget(kind)
        return (None if word.allowed else word.message), word.note

    def keep_budget_note(self, session_id, note):
        """What this start of `session_id` says of its budget check (None, nothing), for its answer and its log to say
        (launch, _chat_send), which take it once; each check sets it anew, so a start refused after its check (the
        gate's floor, the sessions' limit) leaves nothing a later start says."""
        with self._budget_lock:
            if note:
                self._budget_notes[session_id] = note
            else:
                self._budget_notes.pop(session_id, None)

    def take_budget_note(self, session_id):
        with self._budget_lock:
            return self._budget_notes.pop(session_id, None)

    def _status_key(self, account):
        return accounting.DEFAULT if account in (None, accounting.DEFAULT) else account

    def _status_changed_now(self, account):
        """The workspace changed `account`'s Claude sign-in (a sign-out, a code typed, a sign-in ended, the account
        removed): no status kept from before it, nor any read begun before it, is given again (claude)."""
        key = self._status_key(account)
        with self._status_lock:
            self._status_changed[key] = time.monotonic()
            self._status_kept.pop(key, None)

    def claude(self, account=None, fresh=False):
        """{"installed", "signed_in", "email", "method", "subscription"}, as Claude Code's own status says (an
        account's, with `account`, accounts.py): _claude_status's answer, asked once at a time per account.
        A read of an account while one of it is under way joins that one and takes its answer,
        and an answer is kept CLAUDE_STATUS_KEPT seconds from when it came, however long its read took, and given to
        the reads in that time, so that GET /claude and GET /accounts, which the pages ask every few seconds, each look
        not waiting for the last, run the program once however many ask at once. Neither is given once the workspace
        has changed the account's sign-in since its read began (_status_changed_now): a read that would join one begun
        before then waits for it, and then reads anew. With `fresh` (a session's start, which the app checks so), the
        read is as one after such a change: never a kept answer, nor one begun before it, but still one at a time, so
        that a sign-in made outside the workspace (a terminal's own) is seen at once. Times are the monotonic clock's,
        which a change of the machine's own clock does not move."""
        if fresh:
            self._status_changed_now(account)
        key = self._status_key(account)
        while True:
            with self._status_lock:
                changed = self._status_changed.get(key, float("-inf"))
                kept = self._status_kept.get(key)
                if kept is not None and kept[0] > changed and time.monotonic() - kept[1] < CLAUDE_STATUS_KEPT:
                    return dict(kept[2])
                under_way = self._status_under_way.get(key)
                if under_way is None:
                    mine = self._status_under_way[key] = (time.monotonic(), concurrent.futures.Future())
                    break
            began, future = under_way
            try:
                got = future.result()
            except Exception:  # noqa: BLE001  its own reader was told; this one reads anew, once that one has ended
                continue
            with self._status_lock:
                if began > self._status_changed.get(key, float("-inf")):
                    return dict(got)
            # begun before the workspace last changed the account, it may be an answer from before: read anew
        began, future = mine
        try:
            got = self._claude_status(account)
        except BaseException as e:
            with self._status_lock:
                if self._status_under_way.get(key) is mine:
                    del self._status_under_way[key]
            future.set_exception(e)
            raise
        with self._status_lock:
            if self._status_under_way.get(key) is mine:
                del self._status_under_way[key]
            self._status_kept[key] = (began, time.monotonic(), got)
            self._status_last[key] = got
        future.set_result(got)
        return dict(got)

    def _claude_status(self, account=None):
        """{"installed", "signed_in", "email", "method", "subscription"}, as Claude Code's own status says (an
        account's, with `account`, accounts.py); its status names no token, and only these fields, each checked, are
        passed on. A status it does not give in time is not known: signed_in None, never a sign-out, so nothing is held
        back, or sent to sign in again, for a slow answer (one it gives that is not its status is a sign-out). Asked
        through claude, never by itself: claude asks it once at a time per account."""
        claude = self._claude()
        if claude is None:
            return {"installed": False, "signed_in": False, "email": None, "method": None, "subscription": None}
        env = program_env(self.accounts.env(self._account_named(account, "claude")))
        try:
            p = subprocess.run([claude, "auth", "status", "--json"], cwd=self.root, env=env,
                               capture_output=True, timeout=CLAUDE_TIMEOUT, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"installed": True, "signed_in": None, "email": None, "method": None, "subscription": None}
        try:
            status = json.loads(p.stdout.decode("utf-8", "replace") or "{}")
        except ValueError:
            status = {}
        status = status if isinstance(status, dict) else {}

        def field(name, pattern):
            value = status.get(name)
            return value if isinstance(value, str) and pattern.fullmatch(value) else None

        org = status.get("orgName")
        self.orgs[account or accounting.DEFAULT] = org if isinstance(org, str) and 0 < len(org) <= 200 and not any(
            unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") for c in org) else None
        return {"installed": True, "signed_in": status.get("loggedIn") is True, "email": field("email", _EMAIL),
                "method": field("authMethod", _WORD), "subscription": field("subscriptionType", _WORD)}

    def claude_sign_out(self, account=None):
        """Claude Code's own sign-out, which deletes its credentials file (on a Mac, its keychain item); the file is
        then made sure gone, so a sign-out that could not reach Anthropic still ends the sign-in here. The page is then
        told from the probe. With `account`, that account's."""
        claude = self._claude()
        if claude is None:
            raise WorkspaceError("claude is not installed in this workspace.")
        on = self._account_named(account, "claude")
        if on is None and self.link_repos:
            raise WorkspaceError(OWN_SIGN_IN)
        try:
            subprocess.run([claude, "auth", "logout"], cwd=self.root, env=program_env(self.accounts.env(on)),
                           capture_output=True, timeout=CLAUDE_TIMEOUT, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            pass
        folder = self.accounts.folder(on) if on else accounting.default_folder("claude")
        try:
            os.remove(os.path.join(folder, ".credentials.json"))
        except FileNotFoundError:
            pass
        self._status_changed_now(account)       # the page is told what the program says after it, never what was kept
        return self.claude(account)

    def codex_status(self, account=None):
        """{"installed", "signed_in", "method"}: whether Codex (an account's, with `account`) is signed in, and with
        ChatGPT or an API key, from its own `codex login status`, whose words are not passed on (a key's names part of
        it). One it does not give in time is not known: signed_in None, never a sign-out."""
        if shutil.which("codex", path=program_env().get("PATH")) is None:
            return {"installed": False, "signed_in": False, "method": None}
        env = program_env(self.accounts.env(self._account_named(account, "codex")))
        try:
            p = subprocess.run(["codex", "login", "status"], cwd=self.root, env=env, capture_output=True,
                               timeout=CLAUDE_TIMEOUT, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return {"installed": True, "signed_in": None, "method": None}
        said = (p.stdout + p.stderr).decode("utf-8", "replace")
        signed = p.returncode == 0 and "Logged in" in said
        method = ("chatgpt" if "ChatGPT" in said else "api_key" if "API key" in said else None) if signed else None
        return {"installed": True, "signed_in": signed, "method": method}

    def codex_sign_out(self, account=None):
        """Codex's own sign-out (`codex logout`), an account's with `account`; its auth.json is then made sure gone."""
        on = self._account_named(account, "codex")
        if on is None and self.link_repos:
            raise WorkspaceError(OWN_SIGN_IN)
        if shutil.which("codex", path=program_env().get("PATH")) is not None:
            try:
                subprocess.run(["codex", "logout"], cwd=self.root, env=program_env(self.accounts.env(on)),
                               capture_output=True, timeout=CLAUDE_TIMEOUT, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                pass
        folder = self.accounts.folder(on) if on else accounting.default_folder("codex")
        try:
            os.remove(os.path.join(folder, "auth.json"))
        except FileNotFoundError:
            pass
        return self.codex_status(account)

    # accounts (accounts.py): more than one sign-in to Claude Code or to Codex, each in a folder of its own

    def _account_named(self, account_id, kind):
        """The account an id names, of `kind`; None for the default (None or "default"); refused otherwise."""
        if account_id in (None, accounting.DEFAULT):
            return None
        account = self.accounts.get(account_id) if isinstance(account_id, str) else None
        if account is None or account["kind"] != kind:
            raise WorkspaceError("No such account.")
        return account

    def _bind(self, session_id, account_id):
        """A new session's account, kept in the accounts' record before anything of the session can run."""
        if account_id:
            try:
                self.accounts.bind(session_id, account_id)
            except accounting.AccountError as e:
                raise WorkspaceError(str(e)) from None

    def _account(self, account_id):
        account = self.accounts.get(account_id)
        if account is None:
            raise WorkspaceError("This session's account was removed: start a new session on another.")
        return account

    def _session_account(self, record):
        """The account a session runs on, from the accounts' record (never the session's own, which its programs can
        write), of its program's kind; None for the program's own."""
        if not record or accounting.KIND_OF.get(record["program"]) is None:
            return None
        try:
            account_id = self.accounts.of(record["id"], strict=True)
        except accounting.AccountError as e:
            raise WorkspaceError(str(e)) from None
        if not account_id:
            return None
        account = self._account(account_id)
        if account["kind"] != accounting.KIND_OF.get(record["program"]):
            raise WorkspaceError("This session's account is not one of its program's.")
        return account

    def _account_env(self, record):
        """What a session's program starts with for its account: the account's folder; nothing for the default."""
        return self.accounts.env(self._session_account(record))

    def _account_folder(self, record, kind):
        """Where a session's program keeps its sign-in and its conversations: its account's folder, else the
        program's own."""
        account = self._session_account(record)
        return self.accounts.folder(account) if account else accounting.default_folder(kind)

    def _account_ready(self, account_id, repo_path=None):
        """An account's Claude Code opens without its first-run wizard (first_run.py: the account's folder is the
        workspace's, under either provider). In a workspace machine it trusts the workspace and the session's
        repository, as the machine's own claude does; on the person's own machine it asks about each folder, as their
        own Claude Code does, before a repository's settings and hooks apply."""
        account = self._account(account_id)
        if account["kind"] == "claude":
            folder = self.accounts.folder(account)
            if self.claude_first_run:
                first_run.seed(folder, os.path.realpath(self.root), [os.path.realpath(repo_path)] if repo_path else [])
            else:
                first_run.onboarded(folder)

    def _records(self):
        out = []
        for sid in os.listdir(self.sessions_dir):
            if _ID.match(sid):
                try:
                    out.append(self._read(sid))
                except (NotFound, ValueError):
                    continue
        return out

    def accounts_view(self, status=True, fresh=False):
        """Claude Code's and Codex's accounts, each kind's own first: its id ("default" for the program's own), its
        name, whether and how it is signed in, how many sessions run on it, and its windows (5-hour, 7-day) as its
        programs last recorded them (Claude Code's status line in its sessions, Codex's own conversation files); and
        the account new sessions of each kind start on. Without `status`, the programs are not asked (each account's
        `status` is None): what the title bar's windows need, often. With `fresh`, each Claude Code account's status
        is read anew, never the answer kept for the pages' looks (claude): what a task's backends
        need (task_runs.backends)."""
        data = self.accounts.all()
        records = self._records()
        rows_of = {kind: [{"id": accounting.DEFAULT, "name": "Default", "created_at": None}] +
                   [{"id": a["id"], "name": a["name"], "created_at": a["created_at"]}
                    for a in data["accounts"] if a["kind"] == kind] for kind in accounting.KINDS}
        # each account's own status, asked of its program at once: each can take seconds
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            asked = {(kind, row["id"]): pool.submit(self.claude, row["id"], fresh) if kind == "claude"
                     else pool.submit(self.codex_status, row["id"])
                     for kind, rows in rows_of.items() for row in rows} if status else {}
        out = {}
        bound = data["sessions"]
        for kind in accounting.KINDS:
            rows = rows_of[kind]
            for row in rows:
                on = None if row["id"] == accounting.DEFAULT else row["id"]
                theirs = [r for r in records if accounting.KIND_OF.get(r.get("program")) == kind
                          and bound.get(r["id"]) == on and r["program"] not in ("claude-sign-in", "codex-sign-in")]
                row["sessions"] = len(theirs)
                row["status"] = asked[(kind, row["id"])].result() if status else None
                if kind == "claude":
                    windows = [statusline.read(self._dir(r["id"])) for r in theirs]
                    windows = [w for w in windows if w and (w.get("five_hour") or w.get("seven_day"))]
                    newest = max(windows, key=lambda w: w.get("at") or 0, default=None)
                else:
                    gone = on and self.accounts.get(on) is None          # removed while this view was made
                    folder = None if gone else self.accounts.folder(self.accounts.get(on)) if on \
                        else accounting.default_folder("codex")
                    newest = usage.codex_limits(folder, since=int(time.time()) - 8 * 86400) if folder else None
                row["usage"] = {k: newest.get(k) for k in ("at", "five_hour", "seven_day")} if newest else None
            out[kind] = {"accounts": rows}
        # read last: the programs' status takes seconds, and a choice made meanwhile is the one to say
        active = self.accounts.all()["active"]
        for kind in accounting.KINDS:
            out[kind]["active"] = active[kind] or accounting.DEFAULT
        return out

    def account_add(self, kind, name):
        """A new account of Claude Code or Codex, named by the person, not signed in yet (its sign-in does that)."""
        try:
            account = self.accounts.add(kind, name)
        except accounting.AccountError as e:
            raise WorkspaceError(str(e)) from None
        self._account_ready(account["id"])
        return account

    def account_remove(self, account_id):
        """Sign an account out (so nothing of its sign-in stays behind: on a Mac, its keychain item) and forget it and
        its folder; refused while a session runs on it."""
        account = self.accounts.get(account_id) if isinstance(account_id, str) else None
        if account is None:
            raise NotFound("No such account.")
        using = self.account_users(account_id)
        if using:
            raise WorkspaceError(f"{len(using)} session{'s' if len(using) > 1 else ''} run{'' if len(using) > 1 else 's'} "
                                 f"on {account['name']}: remove {'them' if len(using) > 1 else 'it'} first.")
        # signed out first, and removed only once its program says so: removing its folder alone would leave a sign-in
        # behind that nothing here shows (on a Mac, a keychain item named after the folder)
        if account["kind"] == "claude":
            if self._claude() is None:
                raise WorkspaceError("Claude Code is not installed here, so its sign-in cannot be ended first: the "
                                     "account is kept.")
            after = self.claude_sign_out(account_id)
        else:
            after = self.codex_sign_out(account_id)
        if after.get("signed_in") is not False:
            raise WorkspaceError(f"{account['name']} could not be signed out, so it is kept: try again.")
        self.accounts.remove(account_id)
        self._status_changed_now(account_id)
        return {"removed": account_id}

    def account_users(self, account_id):
        """The sessions that run on an account, as the accounts' record binds them. Its sign-ins are not among them:
        the page lists none of them, and they go with it (account_sign_ins)."""
        bound = self.accounts.all()["sessions"]
        # an agent's run, a task's attempt among them, is bound too while it works or waits (agents.py)
        runs = [r for r in self.agents.runs(states=agentry.LIVE)] if hasattr(self, "agents") else []
        return [r for r in self._records() + runs if bound.get(r["id"]) == account_id
                and r["program"] not in ("claude-sign-in", "codex-sign-in")]

    def account_sign_ins(self, account_id):
        """The sign-in sessions of an account, finished or still waiting for a code, which go with it when it is
        removed."""
        bound = self.accounts.all()["sessions"]
        return [r["id"] for r in self._records() if bound.get(r["id"]) == account_id
                and r["program"] in ("claude-sign-in", "codex-sign-in")]

    def account_choose(self, kind, account_id):
        """The account new sessions of `kind` start on: an id, or "default" for the program's own."""
        try:
            self.accounts.choose(kind, account_id)
        except accounting.AccountError as e:
            raise WorkspaceError(str(e)) from None
        return {"kind": kind, "active": account_id or accounting.DEFAULT}

    async def send_sign_in_code(self, session_id, code):
        """Type the code Claude's sign-in page gave into the running Claude sign-in, as its prompt asks: only that
        program takes one, and only a code's own characters, so nothing else can be typed through here. Then the sign-in
        is watched until Claude Code says the account is signed in (_end_once_signed_in)."""
        record = self._read(session_id)
        term = self.terminals.get(session_id)
        if record["program"] != "claude-sign-in" or self._sign_ins.get(session_id) != "claude-sign-in" or term is None \
                or not term.running:
            raise WorkspaceError("Only a running Claude sign-in takes a code.")
        if not isinstance(code, str) or not SIGN_IN_CODE.fullmatch(code.strip()):
            raise WorkspaceError("That is not a sign-in code: paste the code Claude's page shows, as it is.")
        account = self.accounts.all()["sessions"].get(session_id)
        # what the account was before the code, so that one signed in already is ended only once its sign-in has
        # changed it, never at once for what it was before: its credentials file's stamp, and its status as last read
        # (nothing asked of the program here: the code is typed at once, as it always was)
        stamp = self._credentials_stamp(account)
        with self._status_lock:
            before = dict(self._status_last.get(self._status_key(account)) or {})
        await term.write(code.strip().encode("ascii") + b"\r")
        self._status_changed_now(account)
        # one watch per run of the sign-in: a code typed again into the same run is watched by the watch it has; one
        # typed into a run started again (Stop, then Start again) is watched anew, the earlier run's watch let go
        watched = self._sign_in_watches.get(session_id)
        if watched is None or watched[0] is not term:
            if watched is not None:
                watched[1].cancel()
            self._sign_in_watches[session_id] = (term, asyncio.get_running_loop().create_task(
                self._end_once_signed_in(session_id, term, account, before, stamp)))
        return self._view(record)

    def _credentials_stamp(self, account):
        """The account's Claude Code credentials file as it is now, (its modification time, its size), or None where
        it has none: a sign-in that goes through writes it anew, a code refused leaves it as it was.
        None too where Claude Code keeps no such file (on a Mac, its keychain)."""
        try:
            on = self._account_named(account, "claude")
            folder = self.accounts.folder(on) if on else accounting.default_folder("claude")
            st = os.stat(os.path.join(folder, ".credentials.json"))
        except (OSError, WorkspaceError, accounting.AccountError):
            return None
        return (st.st_mtime_ns, st.st_size)

    async def _end_once_signed_in(self, session_id, term, account, before, stamp):
        """After a sign-in's code: the account's status read through claude (one read at a time per
        account, its answer shared), every SIGN_IN_WATCH_EVERY seconds for the first minute and every
        SIGN_IN_WATCH_LATER after it, and the sign-in's program and session ended, as its Stop ends them (stop), once
        Claude Code says the account is signed in by this sign-in: where the account had a credentials file before the
        code (`stamp`), once that file has been written anew since; else, where its status said it was signed in before
        the code (`before`), once it says so otherwise (another address, method or plan); else once it says signed in.
        It is ended SIGN_IN_SETTLE seconds after, so that what the program writes after the credentials is written
        first. The real Claude Code may go on after a sign-in that succeeded (a key it waits for, a screen after it):
        what it does then was never seen, so the page's "signed in" rests on its own status, not on the program's end.
        The watch is of one run, `term`, the one the code was typed into: once the session's terminal is no longer that
        run (Stop, then Start again) or that run has ended by itself, in the settle too, the watch ends, marking the
        account's status to be read anew, and stops nothing; a watch that fails, or runs SIGN_IN_WATCH_FOR, ends
        nothing. A credentials file gone for a moment while it is read (written anew another way) is no write yet."""

        def ours():
            return self.terminals.get(session_id) is term and term.running

        def by_this(got):
            if got.get("signed_in") is not True:
                return False
            if stamp is not None:
                now = self._credentials_stamp(account)
                return now is not None and now != stamp
            if before.get("signed_in") is True:
                return any(got.get(k) != before.get(k) for k in ("email", "method", "subscription"))
            return True
        try:
            began = time.monotonic()
            while time.monotonic() - began < SIGN_IN_WATCH_FOR:
                await asyncio.sleep(SIGN_IN_WATCH_EVERY if time.monotonic() - began < 60 else SIGN_IN_WATCH_LATER)
                if not ours():
                    self._status_changed_now(account)
                    return
                if by_this(await asyncio.to_thread(self.claude, account)):
                    await asyncio.sleep(SIGN_IN_SETTLE)
                    if ours():
                        await self.stop(session_id)
                    else:
                        self._status_changed_now(account)
                    return
        except Exception:  # noqa: BLE001  the sign-in is left as it is, its Stop the person's
            return
        finally:
            if self._sign_in_watches.get(session_id, (None,))[0] is term:
                self._sign_in_watches.pop(session_id, None)

    async def end_sign_ins(self, program, account):
        """One Claude sign-in per account at a time: before a Claude sign-in starts, each one still
        running on the account it starts on (accounts.for_session, as prepare finds it) is stopped as its Stop stops
        it, so that a second try, or a second press, leaves no program of the first running and the page's link and
        code are one sign-in's. The ids stopped; none for another program, or an account prepare will refuse."""
        if program != "claude-sign-in":
            return []
        try:
            on = self.accounts.for_session(program, account)
        except accounting.AccountError:
            return []
        return await self._end_sign_ins_on(program, on)

    async def end_sign_ins_beside(self, session_id):
        """Before a Claude sign-in is started again (its Start again, POST /sessions/{id}/start): each other one still
        running on its account stopped, as end_sign_ins stops them before a new one. The ids
        stopped; none for a session that is no Claude sign-in of this agent's."""
        if self._sign_ins.get(session_id) != "claude-sign-in":
            return []
        return await self._end_sign_ins_on("claude-sign-in", self.accounts.all()["sessions"].get(session_id),
                                           keep=session_id)

    async def _end_sign_ins_on(self, program, on, keep=None):
        bound = self.accounts.all()["sessions"]
        ended = []
        for sid, kind in list(self._sign_ins.items()):
            term = self.terminals.get(sid)
            if sid == keep or kind != program or term is None or not term.running or bound.get(sid) != on:
                continue
            await self.stop(sid)
            ended.append(sid)
        return ended

    # sessions

    def _dir(self, session_id):
        run = self.agents.run_dir(check_id(session_id)) if hasattr(self, "agents") else None
        return run or os.path.join(self.sessions_dir, check_id(session_id))

    def _work(self, session_id):
        """The session's worktree: an agent's run works in its agent's (agents.py)."""
        if hasattr(self, "agents") and self.agents.is_run(session_id):
            return self.agents.work(session_id)
        return os.path.join(self._dir(session_id), "work")

    def _read(self, session_id):
        if hasattr(self, "agents") and self.agents.is_run(check_id(session_id)):
            # an agent's run: held as agents.py holds it, on its agent's branch
            try:
                record = self.agents.read(session_id)
            except (FileNotFoundError, NotADirectoryError):
                raise NotFound("No such session.") from None
            record["branch"] = self.agents.branch(session_id) if record.get("repo") else None
            return record
        try:
            # never waiting on a FIFO a session's programs put where its record goes (they can write it, below): the
            # list and the map read every record every few seconds, on the agent's event loop;
            # a record that is not a regular file is one that cannot be read
            # and never a terminal a link names made the agent's own (Linux gives a session leader with none the first
            # it opens)
            fd = os.open(os.path.join(self._dir(session_id), "session.json"),
                         os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0))
        except (FileNotFoundError, NotADirectoryError):
            raise NotFound("No such session.") from None
        except OSError as e:
            # a link to itself, a record its programs took the reading of away: one that cannot be read, as the rest
            # (either would fail the list and the map for every session)
            raise ValueError(f"This session's record cannot be read: {e.strerror}.") from None
        try:
            # looked at before it is wrapped: wrapping a folder raises, and would leave the descriptor open
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("This session's record is not a file.")
            with os.fdopen(fd, "rb") as fh:
                fd = None
                data = fh.read(MAX_RECORD + 1)
            if len(data) > MAX_RECORD:
                raise ValueError("This session's record is larger than any the workspace writes.")
            # no number JSON cannot carry: the list's answer, which renders the record, would fail for every session
            record = json.loads(data.decode("utf-8"), parse_float=_finite_float, parse_constant=_no_json_constant)
        except (OSError, RecursionError):
            raise ValueError("This session's record cannot be read.") from None
        finally:
            if fd is not None:
                os.close(fd)
        if not isinstance(record, dict):
            raise ValueError("This session's record is not an object.")
        # a session's programs can write its record: the commit it started from, which git is given, is a commit's
        # id or nothing (never an option such as --output=), and a session whose base is nothing refuses what needs it;
        # its branch, which a push names on origin, is the one a session is ever given, pp/<its id>, whatever is written;
        # and its id is its folder's, so that one record never names, nor is written as, another session (else
        # the map would list a planted id twice, and a rename write the other session's record)
        record["id"] = session_id
        base = record.get("base")
        if base is not None and not (isinstance(base, str) and _OBJECT_ID.fullmatch(base)):
            record["base"] = None
        record["branch"] = f"pp/{session_id}" if record.get("repo") else None
        return record

    @staticmethod
    def _base(record):
        """The commit a session started from, for git; refused when its record no longer names one."""
        if not record.get("base"):
            raise WorkspaceError("This session's record no longer names the commit it started from (it was changed): "
                                 "remove the session.")
        return record["base"]

    def upload(self, session_id, name, data, to_chat=False):
        """Put a file from the page into the session's worktree, under UPLOADS; its path there, its name and size. A
        file attached to a chat's message (`to_chat`) is also noted in the chat; one dropped on a terminal is not."""
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has no worktree to put a file in.")
        if not isinstance(data, bytes) or not data:
            raise WorkspaceError("The file is empty.")
        if len(data) > MAX_UPLOAD:
            raise WorkspaceError(f"A file is at most {MAX_UPLOAD // (1024 * 1024)} MB.")
        given = os.path.basename(name.replace("\\", "/")) if isinstance(name, str) else ""
        if not _UPLOAD_NAME.fullmatch(given):
            ext = _EXTENSION.search(given)
            given = "upload-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + (ext.group(0) if ext else "")
        work = self._work(session_id)
        folder = os.path.join(work, UPLOADS)
        os.makedirs(folder, exist_ok=True)
        # the folder ignores itself (and what is in it), so a finish never commits it, and nothing is written to the
        # repository's own exclude, which a repository added from this machine shares with the person's checkout
        ignore = os.path.join(folder, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write("*\n")
        stem, ext = os.path.splitext(given)
        chosen, n = given, 1
        while True:
            try:
                fd = os.open(os.path.join(folder, chosen), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                break
            except FileExistsError:
                n += 1
                chosen = f"{stem}-{n}{ext}"
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        out = {"path": f"{UPLOADS}/{chosen}", "name": chosen, "size": len(data)}
        if to_chat and record["program"] == "chat":
            self.chat(session_id)._emit("upload", **out)
        return out

    def capabilities(self):
        """What the installed claude offers a chat: its version, effort levels, permission modes and models
        (claude_cli.py), read once, and again after the build in use changes (update_claude). A read begun before the
        build changed is not kept, but read again: kept, the old build's answer would stand for the new one until the
        next update or restart. One read at a time: another waiting for it takes its answer."""
        while True:
            with self._capabilities_lock:
                if self._capabilities is not None:
                    return self._capabilities
            with self._discover_lock:
                with self._capabilities_lock:
                    if self._capabilities is not None:      # read by the one before, while this one waited
                        return self._capabilities
                    gen = self._capabilities_gen
                got = self._discover_capabilities()
            with self._capabilities_lock:
                if gen == self._capabilities_gen:
                    self._capabilities = got
                    return got

    def _discover_capabilities(self):
        """One read of what the installed claude offers (claude_cli.discover: its version read as Claude Code's own
        line, as the gate's floor and a start read it, one not read being the floor), its SDK
        handshake through a wrapper naming the build in use when the SDK is there."""
        claude = self._claude() or "claude"
        wrapper = (chats.write_wrapper(os.path.join(self.root, "caps-claude"), PASS_ENV, claude=self._claude())
                   if chats.available() else None)
        return claude_cli.discover(claude, program_env(), self.root, wrapper=wrapper)

    def refresh_capabilities(self):
        """Read what the installed claude offers again, in the background, and keep what that read gives in place of
        what is kept, the floor or not. An ask's own check reads what is kept (ask_offered), and
        while that is the floor, as a first read whose help got nothing leaves it, the ask is refused at once and this
        is started, rather than a read in the ask's request, so the floor no longer holds until the agent
        starts again. One at a time, and none within CAPABILITIES_REFRESH_EVERY seconds of the last one's start. It
        reads as capabilities() reads, by _discover_capabilities under the same lock (the build in use, and the SDK's
        handshake for the models), and like capabilities() keeps nothing when the build in use changed while it read
        (_use_claude moves _capabilities_gen). It runs in a thread of its own, which nothing awaits: that thread, when
        one is started, else None."""
        with self._capabilities_lock:
            now = time.monotonic()
            if self._refreshing is not None or (self._refresh_began is not None
                                                and now - self._refresh_began < CAPABILITIES_REFRESH_EVERY):
                return None
            self._refresh_began = now
            thread = self._refreshing = threading.Thread(target=self._refresh, name="pp-capabilities-refresh",
                                                          daemon=True)
        try:
            thread.start()
        except RuntimeError:            # no thread to be had: an ask that meets the floor later tries again
            with self._capabilities_lock:
                self._refreshing = None
            return None
        return thread

    def _refresh(self):
        """refresh_capabilities' read, in its own thread."""
        try:
            with self._discover_lock:
                with self._capabilities_lock:
                    gen = self._capabilities_gen
                got = self._discover_capabilities()
            with self._capabilities_lock:
                if gen == self._capabilities_gen:
                    self._capabilities = got
        except Exception:  # noqa: BLE001  what is kept stands, and an ask that meets the floor later tries again
            pass
        finally:
            with self._capabilities_lock:
                self._refreshing = None

    # Claude Code kept current (claude_update.py)

    def update_info(self):
        """What the page shows: whether updates run here, their mode, the image's version and the one in use, whether
        one runs now, and the last attempt."""
        if not self.claude_updates:
            return {"enabled": False}
        env = program_env()
        floor = claude_cli.parse_version(claude_cli.run_text(self.claude_floor, env, self.root, "--version")) \
            if self.claude_floor else None
        now = self.capabilities().get("claude_version")
        return {"enabled": True, "mode": self.update_state["mode"], "floor_version": claude_update.version_text(floor),
                "version": now, "updating": self.updating, "last_check": self.update_state["last_check"],
                "last": self.update_state["last"]}

    def update_mode(self, mode):
        if not self.claude_updates:
            raise WorkspaceError("Claude Code is kept current in a workspace machine only.")
        if mode not in claude_update.MODES:
            raise WorkspaceError("Updates are automatic or manual.")
        self.update_state["mode"] = mode
        claude_update.save(self.root, self.update_state)
        return self.update_info()

    def _use_claude(self, path):
        """The claude every start takes from now on (update_claude): the chat wrapper names it, and what it offers is
        read again."""
        self.claude_path = path
        if self._chat_wrapper is not None:
            chats.write_wrapper(self._chat_wrapper, PASS_ENV, claude=path)
        with self._capabilities_lock:
            self._capabilities_gen += 1
            self._capabilities = None

    def update_claude(self):
        """Claude Code's own `install stable`, then the check; on a pass, the chats, terminals and the pages' choices use
        the new build from their next start, and the other versions go. When the install finds the build in use still
        the newest, that build is checked too unless it is the image's own or the state says it passed the check as it
        now is; one that fails is set aside for the image's own. A build outside Claude Code's versions folder is not
        used. After a build is refused, or an install that does not finish, ~/.local/bin/claude points at the build in
        use again, or goes, and every other version in that folder is removed, the refused one too when it is there
        (claude_update.restore). One at a time; the state says how it went."""
        if not self.claude_updates:
            raise WorkspaceError("Claude Code is kept current in a workspace machine only.")
        if not self._update_lock.acquire(blocking=False):
            return self.update_info()
        self.updating = True
        try:
            home, env, state = os.path.expanduser("~"), program_env(), self.update_state
            floor = claude_cli.parse_version(claude_cli.run_text(self.claude_floor, env, self.root, "--version")) \
                if self.claude_floor else None
            target = claude_update.install(self.claude_path, env, self.root, home)
            at = int(time.time())
            state["last_check"] = at
            before = self.claude_path
            same = bool(target and before) and os.path.realpath(target) == os.path.realpath(before)
            if target is None:
                # an install that linked its build and then did not finish (its time ran out, say) would leave that
                # build linked, unchecked: the link points at the build in use again and the other versions go
                claude_update.restore(home, self.claude_path)
                state["last"] = {"at": at, "ok": False, "version": None, "why": "Claude Code's install did not finish"}
            elif same and (before == self.claude_floor
                           or claude_update.in_use(state, home) == os.path.realpath(before)):
                state["last"] = {"at": at, "ok": True, "version": self.capabilities().get("claude_version"),
                                 "why": None, "changed": False}
            elif not claude_update.is_native(home, target):
                # the next start would not take it up (claude_update.in_use): not used now either
                claude_update.restore(home, before)
                state["last"] = {"at": at, "ok": False, "version": None, "why": claude_update.OUTSIDE,
                                 "changed": False}
            else:
                ok, version, why, _ = claude_update.check(
                    target, floor, env, self.root,
                    lambda path: chats.write_wrapper(os.path.join(self.root, "check-claude"), PASS_ENV, claude=path))
                if ok:
                    state["active"], state["checked"] = target, claude_update.CHECK_FORM
                    if not same:
                        self._use_claude(target)
                    claude_update.remove_others(home, target)
                else:
                    if same:        # the build in use fails the check as it now is: the image's own from now on
                        state["active"] = state["checked"] = None
                        self._use_claude(self.claude_floor)
                    claude_update.restore(home, self.claude_path)
                state["last"] = {"at": at, "ok": ok, "version": version, "why": why,
                                 "changed": self.claude_path != before}
            claude_update.save(self.root, state)
        finally:
            self.updating = False
            self._update_lock.release()
        return self.update_info()

    def chat_settings(self, given):
        """A chat's settings, checked against what the installed claude offers: only what is given, an empty value
        meaning Claude Code's own default."""
        if given in (None, {}):
            return {}
        if not isinstance(given, dict) or set(given) - {"model", "effort", "permission_mode", "may_dispatch"}:
            raise WorkspaceError("A chat's settings are its model, its effort, its permission mode and whether it may "
                                 "start agents' runs.")
        out = {}
        if given.get("may_dispatch") not in (None, True, False):
            raise WorkspaceError("Whether a chat may start agents' runs is true or false.")
        if given.get("may_dispatch") is True:
            out["may_dispatch"] = True          # off is the default, so off is not kept
        model, effort, mode = given.get("model"), given.get("effort"), given.get("permission_mode")
        if model not in (None, ""):
            if not isinstance(model, str) or not _MODEL.fullmatch(model):
                raise WorkspaceError("Name a model as Claude Code does: an alias, such as opus or sonnet, or its "
                                     "full name.")
            out["model"] = model
        offered = self.capabilities() if effort not in (None, "") or mode not in (None, "") else None
        if effort not in (None, ""):
            if not isinstance(effort, str) or effort not in offered["efforts"]:
                raise WorkspaceError("An effort is one of " + ", ".join(offered["efforts"]) + ".")
            out["effort"] = effort
        if mode not in (None, ""):
            if not isinstance(mode, str) or mode not in offered["permission_modes"]:
                raise WorkspaceError("A permission mode is one of " + ", ".join(offered["permission_modes"]) + ".")
            out["permission_mode"] = mode
        return out

    def _ask_settings(self, settings, program):
        """The settings the person gave at Start for an agent's ask, checked: ({"effort": E}, E), or ({},
        None) for none. An effort is the one setting such a session takes, for claude alone: none given and "" are
        Claude Code's default, and any other value must have the shape of a level's name (_EFFORT), a falsy one given
        never taken as none. Whether the claude that starts offers it is not asked here but once the session's
        worktree is made (_prepare), where and as that claude starts, and never against what the workspace's own claude
        offers; one it does not offer refuses the Start there. Any other
        setting is checked as a chat's is, then refused."""
        if not isinstance(settings, dict):
            return self.chat_settings(settings), None       # none ({} or None), or refused in a chat's words
        rest = {k: v for k, v in settings.items() if k != "effort"}
        effort = settings.get("effort")
        if self.chat_settings(rest):
            raise WorkspaceError("A session started from an ask takes an effort and no other setting: a model and a "
                                 "permission mode are a chat's alone.")
        if effort in (None, ""):
            return {}, None
        if program != "claude":
            raise WorkspaceError(asking.CLAUDE_ONLY)
        if not isinstance(effort, str) or not _EFFORT.fullmatch(effort):
            raise WorkspaceError("An effort is the name of one of Claude Code's levels.")
        return {"effort": effort}, effort

    def _update(self, session_id, change):
        """Change a session's record as it is on disk now, not a copy read before a slow step (a commit, a push, gh),
        so what another request changed meanwhile (a rename, a ship) is kept. `change` edits the record in place."""
        with self._record_lock:
            record = self._read(session_id)
            change(record)
            self._write(record)
            return record

    def rename(self, session_id, title):
        """A name for the session, shown in place of its task's first line; an empty one puts that back. Only a label:
        the task, the agent's first prompt and what Finish and Ship write from the task stay as they were."""
        if not isinstance(title, str) or len(title) > MAX_TITLE:
            raise WorkspaceError(f"A session's name is text, at most {MAX_TITLE} characters.")
        title = " ".join(title.split())

        def change(record):
            if title:
                record["title"] = title
            else:
                record.pop("title", None)
        self._update(session_id, change)
        return self.session(session_id)

    @staticmethod
    def _provider_key(provider):
        if not providers.keys(os.path.expanduser("~")).get(provider):
            raise WorkspaceError(f"Add your {providers.CATALOG[provider]['label']} API key in Connections first.")

    def _on_provider(self, session_id, provider, argv):
        """argv for a claude session on another provider's model: Claude Code takes the key from `apiKeyHelper` in a
        settings file of the session's own (with the settings this start already gives it in it: the local provider's
        gate settings, when there are some, and the map's sources, when _with_map gave them), so the key is never in
        the session's environment, which every command it runs inherits."""
        self._provider_key(provider)
        settings = {}
        named = argv[argv.index("--settings") + 1] if "--settings" in argv[:-1] else None
        if named:
            # the session's own (_with_map's), which its programs can replace with a link or a FIFO, read as the map
            # reads a session's files; else the workspace's own, as it always was (_workspace_settings)
            own = named == os.path.join(self._dir(session_id), sessionmap.MAP_SETTINGS)
            settings = sessionmap.read_json(named, SETTINGS_FILE) if own else _workspace_settings(named)
            if not isinstance(settings, dict):
                raise WorkspaceError("The workspace's own settings for claude could not be read.")
        here = os.path.dirname(os.path.abspath(providers.__file__))
        settings["apiKeyHelper"] = providers.helper_command(sys.executable, here, provider)
        # the endpoint and the models pinned here too: a settings file's env overwrites the launch environment, and a
        # repository's own .claude settings may set ANTHROPIC_BASE_URL (Claude Code's settings reference), which would
        # send the key elsewhere; a --settings file outranks a repository's, a user's and a local one
        settings["env"] = dict(settings.get("env") or {}, **providers.CATALOG[provider]["env"])
        try:
            # strict JSON, as Claude Code may read it: a file it refused would take the gate and the key helper with it
            text = json.dumps(settings, allow_nan=False)
        except ValueError:
            raise WorkspaceError("The workspace's own settings for claude hold a number JSON has no name for (such as "
                                 "1e400): the session was not started.") from None
        path = os.path.join(self._dir(session_id), "model-settings.json")
        tmp = path + ".tmp"
        with _new_file(tmp, 0o600) as fh:
            fh.write(text)
        os.replace(tmp, path)
        argv = list(argv)
        if "--settings" in argv:
            argv[argv.index("--settings") + 1] = path     # the gate's settings are in this file too
        else:
            argv[1:1] = ["--settings", path]
        return argv

    def own_settings(self, session_id, argv):
        """Whether a claude terminal's argv names the session's own settings file (_with_map wrote it)."""
        path = os.path.join(self._dir(session_id), sessionmap.MAP_SETTINGS)
        at = argv.index("--settings") + 1 if "--settings" in argv else len(argv)
        return at < len(argv) and argv[at] == path

    def ade_hook_settings(self):
        """The approval hook's settings for this workspace's claude terminals and the owner's own chats
        (settings.ade_hook_settings), or None where the workspace was given no folder for it (the local provider). The
        address is read from the folder at each start (_ade_url); the token never is, its file's path alone going in
        the settings. A folder that holds no address or no token still gives the hook, which then refuses each gated
        command, saying why, rather than let one through on the session's own answer."""
        if not self.ade_hook:
            return None
        here = os.path.dirname(os.path.abspath(__file__))
        return settings_made.ade_hook_settings(os.path.join(here, settings_made.ADE_HOOK_START), sys.executable,
                                               os.path.join(os.path.dirname(here), "ade_hook.py"), self.ade_hook,
                                               self._ade_url())

    def _ade_url(self):
        """The deployment's address in the approval hook's folder, or "": read without waiting, a regular file only, at
        most 2 KB, so nothing put where the address was can hold the agent's loop."""
        try:
            fd = os.open(os.path.join(self.ade_hook, settings_made.ADE_URL_FILE),
                         os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0))
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    return os.read(fd, 2048).decode("utf-8").strip()
            finally:
                os.close(fd)
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    def held(self):
        """Whether this workspace's claude terminals and the owner's own chats hold their gated commands for the
        deployment's approval queue, for the page to say so only where it is so (programs): "held", its
        hook, its start and the hook's two parts all there (an address the hook takes, and a token file that is not
        empty, never read here); "refused", given the folder but missing one of them, where each gated command is
        refused rather than held for an approval; "none", given no folder (the local provider, or a machine whose
        image has no hook)."""
        if not self.ade_hook:
            return "none"
        here = os.path.dirname(os.path.abspath(__file__))
        start = os.path.join(here, settings_made.ADE_HOOK_START)
        try:
            token = os.stat(os.path.join(self.ade_hook, settings_made.ADE_TOKEN_FILE))
            whole = (stat.S_ISREG(token.st_mode) and token.st_size > 0 and os.access(start, os.X_OK)
                     and os.path.isfile(start) and os.path.isfile(os.path.join(os.path.dirname(here), "ade_hook.py")))
        except OSError:
            whole = False
        return "held" if whole and settings_made.ADE_URL.fullmatch(self._ade_url()) else "refused"

    def _runs_claude(self, argv):
        """Whether `argv` starts Claude Code as its program: a program named claude, or the file this
        workspace's claude is. One started through another program, a shell or a script, is not seen here."""
        if not argv or not isinstance(argv[0], str):
            return False
        if os.path.basename(argv[0]) == "claude":
            return True
        path = program_env().get("PATH")
        found, mine = shutil.which(argv[0], path=path), self._claude() or shutil.which("claude", path=path)
        return bool(found and mine and os.path.realpath(found) == os.path.realpath(mine))

    def _holds_ade_hook(self, argv):
        """Whether a claude terminal's argv names a settings file that carries the approval hook: the
        session's own (_with_map's), or the one _on_provider made from it for another provider's model."""
        held = self.ade_hook_settings()
        if held is None or "--settings" not in argv[:-1]:
            return False
        return settings_made.holds(sessionmap.read_json(argv[argv.index("--settings") + 1], SETTINGS_FILE), held)

    def _with_map(self, session_id, argv, cwd, controller=False):
        """A claude terminal's argv at a start (prepare's command for its first, launch's for each later one) with the
        control window's map sources it takes (settings.map_settings: the sub-agent status line, and the activity hook
        on each tool call and each sub-agent's start and end), by the version the gate's floor read for this start
        (claude_floor_refusal, in `cwd`, of argv[0]): in a settings file of the session's own (sessionmap.MAP_SETTINGS),
        which holds what the --settings it was given holds (under the local provider, the gates and the status line) and
        these beside it, and is named in that file's place; a session on another provider's model then has its key
        helper and address added in its own file (_on_provider, which runs after this and starts from it). In a
        workspace machine, where the gates are the machine's managed settings, the file holds these alone. The
        workspace-wide settings (the image's, the local provider's file) are written once for whatever claude a session
        later runs, so the version is read where it is known: here, at each start, so a session started after an update
        gets what its new claude takes, and one whose worktree pins an older build gets nothing that build would refuse
        (settings.py says why that matters: a value refused can leave the whole file out, and with it the gate).

        Nothing is added when no version was read for this start, when the version takes none of them, when the
        settings it was given cannot be read as settings to add to (a file that cannot be read is never replaced by one
        that holds less), or when the session's file would pass SETTINGS_FILE, as large as _on_provider reads it back
        (the settings it was given are the workspace's own, of any size, _workspace_settings): argv is as it was, and the
        session's file goes, so the map says its sources were not given (sessionmap). The version is taken once: a
        later start reads its own. A session the owner's controller started (`controller`) is given the controller's
        limit hooks and setting beside them (settings.controller_settings), by the same version.

        In a hosted workspace (ade_hook) the file is written whatever the version takes of these, with the approval
        hook in it (settings.with_held), and where it cannot be, launch refuses the start: the hook is
        never one more thing a start goes on without."""
        version, at = self._read_versions.pop((argv[0], cwd), (None, 0))
        if time.monotonic() - at > READ_VERSION_FOR:
            version = None
        path = os.path.join(self._dir(session_id), sessionmap.MAP_SETTINGS)
        here = os.path.dirname(os.path.abspath(__file__))
        given = {}
        if "--settings" in argv:
            at = argv.index("--settings") + 1
            if at < len(argv) and argv[at] == path:
                return argv             # given already (prepare's command): never taken away from under it
            given = _workspace_settings(argv[at]) if at < len(argv) else None    # never the session's own, above
        made = settings_made.with_map(given, sys.executable, here, version, controller, skills=True) \
            if given is not None else None
        held = self.ade_hook_settings()
        if held is not None:
            # the approval hook, after what the map gave, or after what was given where the version took
            # nothing of the map's: never left out for that
            made = settings_made.with_held(made if made is not None else given, held) if given is not None else None
        try:
            # ASCII, so its length is its size; and strict JSON, as Claude Code may read it: a number the workspace's
            # own file gives that Python reads as infinity (1e400) is no file to give
            text = json.dumps(made, allow_nan=False) if made is not None else ""
        except ValueError:
            made, text = None, ""
        wanted = held is not None or settings_made.map_settings(sys.executable, here, version) or (
            controller and settings_made.controller_settings(sys.executable, here, version)) or \
            settings_made.skills_settings(sys.executable, here, version)
        if made is None or not wanted or len(text) > SETTINGS_FILE:
            with contextlib.suppress(OSError):
                os.unlink(path)
            return argv
        tmp = path + ".tmp"
        try:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except OSError:
            # the session's folder would not take it (a full disk, or a folder its programs put where it goes): the
            # start goes on as it was, with nothing of the map's, and no file left to say it was given
            for leftover in (tmp, path):
                with contextlib.suppress(OSError):
                    os.unlink(leftover)
            return argv
        argv = list(argv)
        if "--settings" in argv:
            argv[argv.index("--settings") + 1] = path
        else:
            argv[1:1] = ["--settings", path]
        return argv

    def join_group(self, session_id, group):
        """Join a session to a group, to be compared with sessions started later on its task (the page's "Try with
        another agent"): a session with no group takes the one named, and keeps it; one already compared with others
        keeps its own. A group is a label, nothing more."""
        if not isinstance(group, str) or not _GROUP.match(group):
            raise WorkspaceError("A group is 8 to 32 lowercase letters and digits.")
        refused = []

        def change(record):
            if record.get("group") in (None, group):
                record["group"] = group
            else:
                refused.append(record["group"])
        self._update(session_id, change)
        if refused:
            raise WorkspaceError("This session is already compared with others: start the new ones in its group.")
        return self.session(session_id)

    def _write(self, record):
        if self.agents.is_run(record["id"]):
            self.agents._write(record)          # with the next change number, which the app's poll reads
            return
        if record["id"] in self._chosen:        # a chat's settings as the owner chose them, whatever was read
            if self._chosen[record["id"]]:
                record["chat_settings"] = dict(self._chosen[record["id"]])
            else:
                record.pop("chat_settings", None)
        path = os.path.join(self._dir(record["id"]), "session.json")
        # through a file made anew (_new_file): the list writes a record as it adopts a session, on the event loop
        with _new_file(path + ".tmp", 0o666) as fh:
            json.dump(record, fh, indent=1)
        os.replace(path + ".tmp", path)

    def chosen_changed(self):
        """The chats whose records name other settings than the owner chose (kept here since this agent started):
        changed by something other than the workspace."""
        out = []
        for session_id, chosen in list(self._chosen.items()):
            try:
                written = self._read(session_id).get("chat_settings") or {}      # as the list reads it
            except (NotFound, OSError, ValueError):
                continue
            if written != chosen:
                out.append(session_id)
        return sorted(out)

    def running_programs(self):
        """Programs running now: terminals', sessions' shells' and chats' (a chat's claude runs while it is
        connected)."""
        return (sum(1 for t in self.terminals.values() if t.running)
                + sum(1 for byn in self.shells.values() for t in byn.values() if t.running)
                + sum(1 for c in self.chats.values() if c.client is not None))

    def _view(self, record):
        # a `checks` key a session's programs wrote into its own record is no answer's: a session's checks come from the
        # controller's own record, by the session's own read alone
        record = {k: v for k, v in record.items() if k != "checks"}
        if record["program"] == "chat":
            account = self.accounts.of(record["id"])
            try:
                view = self.chat(record["id"]).view()
            except WorkspaceError as e:
                # its account gone, or not one of claude's: listed all the same, to be seen and removed, never started
                return dict(record, account=account, running=False, exit_code=None, ended_at=None, device_code=None,
                            sign_in_url=None, sign_in=False, sign_in_text=None, chat=None, activity=None,
                            shells=self._shells_view(record["id"]), error=str(e))
            running = view["state"] in ("idle", "running", "needs_you")
            return dict(record, account=account, running=running, exit_code=None, ended_at=None, device_code=None,
                        sign_in_url=None, sign_in=False, sign_in_text=None, chat=view, activity=None,
                        shells=self._shells_view(record["id"]), diff=self._size_of(record, running))
        term = self.terminals.get(record["id"])
        # from the program itself, whether or not a terminal is attached: its exit is seen as it happens
        running = bool(term and term.running)
        ended = term is not None and not running
        exit_code, ended_at = (term.exit_code, term.ended_at) if ended else (None, None)
        if term is None:
            # not run since the agent started: how it ended before, from the screen it saved, if it did
            saved = terminals.SavedScreen.meta(self._screen_path(record["id"]))
            if saved is not None:
                exit_code, ended_at = saved["exit_code"], saved["ended_at"]
        # a sign-in this agent started: the one kind of session whose output the page shows as a sign-in
        sign_in = self._sign_ins.get(record["id"]) == record["program"]
        record = dict(record, account=self.accounts.of(record["id"]))      # the accounts' record's, never its own
        codes = DEVICE_CODE.findall(term.backlog) if sign_in and running and record["program"] == "github" else []
        urls = CLAUDE_SIGN_IN_URL.findall(term.backlog) if sign_in and running and record["program"] == "claude-sign-in" \
            else []
        # Codex's own sign-in says where to go and what code to give there in its own words: shown as it said them; and a
        # sign-in that ended, Claude's or Codex's, what it said last (a code that was not taken, say)
        said = _screen_text(term.backlog) if sign_in and term is not None and (
            record["program"] == "codex-sign-in" or (record["program"] == "claude-sign-in" and not running)) else None
        return dict(record, running=running, exit_code=exit_code, ended_at=ended_at, sign_in=sign_in,
                    device_code=codes[-1].decode("ascii") if codes else None,
                    sign_in_url=urls[-1].decode("ascii") if urls else None, sign_in_text=said, chat=None,
                    activity=self._activity(record["id"], term) if running else None,
                    shells=self._shells_view(record["id"]), diff=self._size_of(record, running))

    # each session's change size, for the list

    def _size_of(self, record, running):
        """The session's change against where it started, in size, as last worked out: {files, added, removed}, None
        until it is (and for a session with no repository, or a sign-in). One older than SIZE_FRESH_* is worked out
        again, in the background: listing the sessions never waits on git."""
        sid = record["id"]
        if not record.get("repo") or record["program"] in SIGN_INS:
            return None
        with self._size_lock:
            kept = self._sizes.get(sid)
            fresh = max(SIZE_FRESH_RUNNING if running else SIZE_FRESH_ENDED, SIZE_PATIENCE * (kept[2] if kept else 0))
            if (kept is None or time.monotonic() - kept[0] > fresh) and sid != self._sizing:
                self._sizes_wanted.add(sid)
                if not self._sizer:
                    self._sizer = True
                    threading.Thread(target=self._work_sizes, name="pp-sizes", daemon=True).start()
            return kept[1] if kept else None

    def _work_sizes(self):
        """Work out the sizes wanted, one at a time, until none is; a session gone or unreadable is kept as None until
        it is due again, so one that fails is not tried on every listing."""
        while True:
            with self._size_lock:
                if not self._sizes_wanted:
                    self._sizer = False
                    return
                sid = self._sizing = self._sizes_wanted.pop()
            began = time.monotonic()
            try:
                size = self._change_size(sid)
            except Exception:  # noqa: BLE001  a size is a nicety: a session it cannot be worked out for shows none
                size = None
            with self._size_lock:
                kept = self._sizes.get(sid)
                # a session removed meanwhile is not kept a size, nor is one read since this began (changes()) overwritten
                if os.path.isdir(self._dir(sid)) and (kept is None or kept[0] < began):
                    self._sizes[sid] = (time.monotonic(), size, time.monotonic() - began)
                self._sizing = None

    def _change_size(self, session_id):
        """The size of what changes() shows: files changed, lines added and removed (a binary file counts as a file);
        against the base a finished update's merge left, as changes() reads it (_settled)."""
        record = self._read(session_id)
        work = self._work(session_id)
        if not record["repo"] or not os.path.isdir(work):
            return None
        record = self._settled(session_id)
        if not record.get("base"):
            return None
        with self._worktree_index(work) as env:
            _, numstat = git("diff", "--no-ext-diff", "--no-textconv", "--numstat", "-z", "-M", record["base"],
                             cwd=work, env=env)
        return dict(_size(_numstat(numstat)), uncommitted=_uncommitted(work))

    @staticmethod
    @contextlib.contextmanager
    def _worktree_index(work):
        """An environment whose index knows every file of the worktree, new ones included and ignored ones not, for a
        diff of the worktree against a commit: a copy of its index with the new files added as intended (git add
        --all --intent-to-add), so what the session itself staged, and its next commit, are as they were, and nothing
        is written to the repository's objects (but the empty blob, once): a file is read where it is, never stored."""
        _, real = git("rev-parse", "--git-path", "index", cwd=work)
        real = real.strip()
        if not os.path.isabs(real):
            real = os.path.join(work, real)
        with tempfile.TemporaryDirectory(prefix="pp-changes-") as tmp:
            index = os.path.join(tmp, "index")
            if os.path.exists(real):
                # its times kept: git trusts an entry whose file looks unchanged unless the entry is as new as the index
                # file, so a fresh copy's newer time would hide an edit that kept a file's size in the same second
                shutil.copy2(real, index)
            env = {"GIT_INDEX_FILE": index}
            git("add", "--all", "--intent-to-add", cwd=work, env=env)
            yield env

    def _shells_view(self, session_id):
        """The session's shells opened since the agent began, by number: when each started (which tells one shell in a
        place from the next), whether it runs, else how it ended."""
        out = []
        for n, sh in sorted((self.shells.get(session_id) or {}).items()):
            running = sh.running
            out.append({"n": n, "running": running, "started_at": sh.started_at,
                        "exit_code": None if running else sh.exit_code, "ended_at": None if running else sh.ended_at})
        return out

    def _activity(self, session_id, term):
        """What the session's program said it is doing, through its hooks (activity.py), since this run of it started:
        its state, its note, and the names of the tool and the kind of notification, which are what a notification may
        say; None when it has said nothing (a shell, opencode, kimi, mcode, or an agent that has not had a turn yet). The session's
        own word, shown as that."""
        # read as the map reads a session's files (sessionmap.read_json): never a link followed, never a FIFO waited
        # on, since the session's programs can put either where the file goes and the list is asked every few seconds
        got = sessionmap.read_json(os.path.join(self._dir(session_id), "activity.json"), ACTIVITY_FILE)
        if not isinstance(got, dict) or got.get("state") not in ("working", "waiting", "idle"):
            return None
        at = got.get("at")
        if not isinstance(at, int) or at < term.started_at - 1:
            return None
        out = {"state": got["state"], "at": at}
        if isinstance(got.get("message"), str):
            out["message"] = got["message"][:200]
        for name in ("tool", "kind"):
            if isinstance(got.get(name), str) and _ACTIVITY_NAME.fullmatch(got[name]):
                out[name] = got[name]
        return out

    def sessions(self):
        """Every session, oldest first. One whose record cannot be read is left out (_read: none there, one that is no
        regular file, cannot be opened or decoded, or is not an object), and so is one whose record its own programs
        wrote with a field no view can be made of, said once on stderr, so that one session never takes the list, nor
        the map made from it, away from the others; one whose created_at is not a number comes after the rest
        (a record holding [], a created_at of "soon", a link to itself,
        one that could not be opened or was nested too deep would each fail both for every session)."""
        out = []
        for sid in sorted(os.listdir(self.sessions_dir)):
            if _ID.match(sid):
                try:
                    out.append(self._view(self._adopted(sid, self._read(sid))))
                except (NotFound, ValueError):
                    continue
                except (AttributeError, KeyError, TypeError) as e:
                    self._say_left_out("sessions", sid, e)
        self._skills_poke()
        return sorted(out, key=_created)

    def _say_left_out(self, where, session_id, error):
        """Say on stderr that the list or the map left a session out, once for each session and kind of error: the
        page asks every few seconds, and the line would repeat into the agent's log without end."""
        key = (where, session_id, type(error).__name__)
        if key not in self._left_out_said:
            self._left_out_said.add(key)
            sys.stderr.write(f"{where}: session {session_id!r} left out: {type(error).__name__}\n")

    def session(self, session_id, checks=False):
        """A session as the routes on one session answer it. With `checks`, its own read (`GET /sessions/{id}` alone):
        one the owner's controller started carries its task's last round of checks, each result with its command's
        name and its output's last bytes marked as a program's, from the controller's own record,
        and any other null. No other answer carries them: the list
        (sessions) is read every few seconds, and the answers of a stop, a start or a title are not where the owner
        reads a check's output."""
        view = self._view(self._adopted(session_id, self._read(session_id)))
        if checks:
            view["checks"] = self.controller.checks_of(session_id)
        self._skills_poke()
        return view

    # the skills its sessions called (skillcalls.py)

    def take_skill_calls(self):
        """Take the new lines of every session's skills.jsonl into the workspace's record and write it once; how many lines
        were added. A session whose record cannot be read is not taken from (the list leaves it out as well), and its
        place in the record is kept; a session that is gone has its place let go, its lines staying with where they were
        made. Each line is kept with its session's repository and the controller's task for it, where it has one. One at a
        time: a pass that listed the sessions before another began would let go the place of a session the other took."""
        with self._take_lock:
            live, added = set(), 0
            try:
                names = sorted(os.listdir(self.sessions_dir))
            except OSError:
                return 0
            for sid in names:
                if not _ID.match(sid):
                    continue
                live.add(sid)
                try:
                    record = self._read(sid)
                except (NotFound, ValueError):
                    continue
                added += self.skill_calls.take(sid, self._dir(sid), record.get("repo"),
                                               self.controller.task_id_of(sid), flush=False)
            self.skill_calls.forget(live)
            self.skill_calls.flush()
            return added

    def skills_page(self, since):
        """GET /skills?since=N: every session's new lines taken first (a page that is closed reads no session, so the app's
        own read is what takes them then), then the lines after the number `since` (skillcalls.SkillCalls.page)."""
        self.take_skill_calls()
        return self.skill_calls.page(since)

    def _skills_poke(self):
        """Ask for the take of the skill calls after a read of a session or of the list: done in a thread of its own, at
        most one at a time and at most every SKILLS_EVERY seconds, so that a read never waits on it (the list is asked
        every few seconds, on the agent's event loop). A read that comes while the last take is within its seconds is
        not lost: the take runs once they are over. A thread that cannot be started (the system refuses one) loses that
        read's poke and says so once, and the next read asks again: the taking is not switched off for the process."""
        with self._skills_lock:
            self._skills_again = True
            if self._skills_running:
                return
            self._skills_running = True
        try:
            threading.Thread(target=self._skills_work, name="pp-skills", daemon=True).start()
        except Exception as e:  # noqa: BLE001  no thread could be started: the next read asks again
            with self._skills_lock:
                self._skills_running = False
            self._say_left_out("skills", "-", e)

    def _skills_work(self):
        try:
            while True:
                with self._skills_lock:
                    wait = self._skills_next - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                with self._skills_lock:
                    if not self._skills_again:
                        self._skills_running = False
                        return
                    self._skills_again = False
                    self._skills_next = time.monotonic() + SKILLS_EVERY
                try:
                    self.take_skill_calls()
                except Exception as e:  # noqa: BLE001  a take that fails is tried again at the next read, and said once
                    self._say_left_out("skills", "-", e)
        except BaseException:
            with self._skills_lock:
                self._skills_running = False
            raise

    def _adopted(self, session_id, record):
        """A session teleport started takes the cloud session's work on its own branch, once (_adopt). A read of its
        record starts that in a thread of its own, at most every ADOPT_EVERY seconds, so a read never waits on git (a
        page polls the list every few seconds, on the agent's event loop); returns the record as it is. One whose work
        was taken before such a session was named after its branch is named so here, once."""
        if _teleport_title(record):
            return self._update(session_id, _name_after_branch)
        if not _teleport_pending(record):
            return record
        now = time.monotonic()
        with self._record_lock:
            if self._adopt_next.get(session_id, 0) > now:
                return record
            self._adopt_next[session_id] = now + ADOPT_EVERY
        threading.Thread(target=self._adopt, args=(session_id,), daemon=True).start()
        return record

    def _adopt(self, session_id, wait=False):
        """The adoption itself, one at a time per session (a second look while one is under way returns at once, or,
        with `wait`, waits for it): when teleport has checked out another branch in the session's worktree and the
        session's own (pp/<id>) has nothing beyond its base, its own branch is moved to that commit and checked out in
        its place, and its base becomes the fork point of that work and what the session follows (fetched first, once
        and bounded, so a clone whose own branch is behind origin's does not count origin's newer commits as the
        session's work), else of that work and where it started. Its git runs with no hook, no fsmonitor and no signing
        program (RUN_GIT): a repository's settings are not the person's word, and a hook could make a checkout that
        happened look as if it had not. A session's branch is only ever pp/<id> (_read): what a push names is never a
        name its programs could write; the branch teleport came from is kept as `teleported_from`, a label, and a
        session still under the page's own task for it (TELEPORT_TASK) and never renamed takes that branch as its name,
        so that two such sessions are told apart in the list; a name the person gave, as its task or by Rename, stays.
        Anything that goes wrong leaves the session as it was, for a later look."""
        with self._record_lock:
            lock = self._adopt_locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=wait):
            return
        try:
            record = self._read(session_id)
            if not _teleport_pending(record):
                return
            work = self._work(session_id)
            if not os.path.isdir(work):
                return
            own = record["branch"]
            code, head = git("symbolic-ref", "--quiet", "HEAD", cwd=work, ok=(0, 1, 128), env=RUN_GIT)
            head = head.strip()
            if code != 0 or not head.startswith("refs/heads/") or head == f"refs/heads/{own}":
                return
            code, ahead = git("rev-list", "--count", f"{record['base']}..refs/heads/{own}", cwd=work, ok=(0, 128),
                              env=RUN_GIT)
            if code != 0 or ahead.strip() != "0":
                return
            fork = self._fork_point(session_id, record, work)
            if fork is None:
                return                              # no history in common: not this repository's work
            git("checkout", "-q", "-B", own, cwd=work, ok=(0, 1, 128), env=RUN_GIT)
            code, now_on = git("symbolic-ref", "--quiet", "HEAD", cwd=work, ok=(0, 1, 128), env=RUN_GIT)
            if code != 0 or now_on.strip() != f"refs/heads/{own}":
                return                              # not done (a checkout of teleport's under way): a later look

            def adopt(r):
                if not r.get("adopted"):
                    r.update(base=fork, adopted=True, teleported_from=head[len("refs/heads/"):][:200])
                    _name_after_branch(r)
            self._update(session_id, adopt)
        except (WorkspaceError, NotFound, OSError, ValueError, KeyError):
            pass                                    # a later look tries again
        finally:
            lock.release()

    def _fork_point(self, session_id, record, work):
        """Where the work in `work` forked from what the session follows (_followed), fetched from origin first, once
        per session, in at most ADOPT_FETCH seconds and as the repository's own fetch is (one at a time); else where it
        forked from the session's base. None when it has history in common with neither."""
        targets = []
        try:
            ref, _, refspec = self._followed(record)
            targets.append(ref)
        except (WorkspaceError, NotFound):
            refspec = None
        if refspec and session_id not in self._adopt_fetched:
            fetching = self._fetch_locks.setdefault(record["repo"], threading.Lock())
            if fetching.acquire(timeout=ADOPT_FETCH):
                try:
                    self._adopt_fetched.add(session_id)     # tried: a fetch the lock kept from running is tried again
                    git("fetch", "--quiet", "--no-tags", "origin", refspec, cwd=self._repo_path(record["repo"]),
                        ok=(0, 1, 128), timeout=ADOPT_FETCH, env=RUN_GIT)
                except WorkspaceError:
                    pass                            # origin not reached in time: what it last fetched stands
                finally:
                    fetching.release()
        for target in targets + [record["base"]]:
            code, found = git("merge-base", "HEAD", target, cwd=work, ok=(0, 1, 128), env=RUN_GIT)
            if code == 0 and found.strip():
                return found.strip()
        return None

    def _teleport_held(self, session_id, record):
        """Why a session teleport started cannot be finished or shipped yet, or None: its worktree is on another
        branch than its own, the one teleport checked out, and that work has not been taken as the session's (_adopt).
        Finishing would commit on that branch, and shipping would push it, with its base where it started, under the
        session's own name."""
        if not _teleport_pending(record):
            return None
        work = self._work(session_id)
        code, head = git("symbolic-ref", "--quiet", "HEAD", cwd=work, ok=(0, 1, 128), env=RUN_GIT)
        head = head.strip()
        if code == 0 and head == f"refs/heads/{record['branch']}":
            return None
        if code != 0 or not head.startswith("refs/heads/"):
            return ("Its worktree is on no branch, so the work Claude Code's teleport brought cannot be taken as the "
                    f"session's own ({record['branch']}): check out its own branch in the session's terminal, or start "
                    "a new session from claude.ai.")
        where = head[len("refs/heads/"):]
        code, ahead = git("rev-list", "--count", f"{record['base']}..refs/heads/{record['branch']}", cwd=work,
                          ok=(0, 128), env=RUN_GIT)
        if code == 0 and ahead.strip() not in ("", "0"):
            return (f"Its worktree is on {where}, which Claude Code's teleport checked out, and the session's own "
                    f"branch ({record['branch']}) has commits of its own, so that work is not taken as the session's: "
                    "nothing of it is finished or shipped. Start a new session from claude.ai to continue it.")
        return (f"Its worktree is on {where}, which Claude Code's teleport checked out, and that work has not been "
                f"taken as the session's own ({record['branch']}) yet: it is taken once teleport is done. Until then "
                "there is nothing of the session's to finish or ship.")

    # chats (chat.py): Claude Code through the Agent SDK, in the session's worktree

    def chat(self, session_id):
        record = self._read(session_id)
        if record["program"] != "chat":
            raise WorkspaceError("That session is not a chat.")
        c = self.chats.get(session_id)
        if c is None:
            # written again at each chat's start: a program of any session could have changed it since, and it is
            # what every chat starts
            self._chat_wrapper = chats.write_wrapper(os.path.join(self.root, "chat-claude"), PASS_ENV,
                                                     claude=self._claude())
            watch.wrote(self._chat_wrapper)
            if session_id not in self._chosen and not self.agents.is_run(session_id):
                self._chosen[session_id] = dict(record.get("chat_settings") or {})
            run = self.agents.chat_options(session_id) if self.agents.is_run(session_id) else None
            env = program_env({"PP_SESSION_ID": session_id, "PP_SESSION_DIR": self._dir(session_id),
                               **self._account_env(record), **(run["env"] if run else {})})
            c = chats.Chat(session_id, self._dir(session_id), self._work(session_id), env,
                           self._chat_wrapper, settings=self.claude_settings, listeners=self.chat_listeners,
                           on_activity=self.activity_of(session_id),
                           chosen=run["chat_settings"] if run else self._chosen.get(session_id),
                           mcp_plan=run["mcp_plan"] if run else (lambda: self.mcp_plan(session_id)), run=run,
                           own_tools=None if run else (lambda chosen: self.agents.chat_tools(session_id, chosen)))
            if not run and self.controller.is_chat(session_id):
                # the owner's controller's chat (controller.py): its six tools alone, no built-in tool, no MCP server
                # of a repository's, its compaction and its rate-limit events seen
                self.controller.configure(c)
            c.before_start = lambda c=c: self._chat_start(c)     # its claude checked where and as the chat runs it
            self.chats[session_id] = c
        return c

    def chat_log(self, session_id, after=0):
        c = self.chat(session_id)
        events, more = chats.read_events(c.path, after)
        return dict(c.view(), events=events, more=more)

    async def chat_choose(self, session_id, given):
        """New settings for a chat: each one given replaces its own, an empty one goes back to Claude Code's default,
        and the rest stay. Written down with the session, so they last across a stop or a restart."""
        chosen = self.chat_settings(given)
        if self._read(session_id)["program"] != "chat":
            raise WorkspaceError("That session is not a chat.")
        if self.controller.is_attempt(session_id):
            # an attempt of a requirement's manager runs as its manager set it, to its own end
            #
            from . import controller as control
            raise WorkspaceError(control.NO_CONTROL)

        def change(record):
            # from what the owner chose last, as kept here, never from the record, which a program could have changed
            now = dict(self._chosen.get(session_id, record.get("chat_settings") or {}))
            for key in given or {}:
                if key in chosen:
                    now[key] = chosen[key]
                else:
                    now.pop(key, None)
            self._chosen[session_id] = now
            record["chat_settings"] = now
        if self.agents.is_run(session_id):
            raise WorkspaceError("A run's settings are its agent's: change them on the agent.")
        self._update(session_id, change)
        # as kept here: an empty choice is written as none (_write), so the record may have no settings at all
        return await self.chat(session_id).choose(dict(self._chosen[session_id]))

    async def chat_send(self, session_id, text, sender="owner"):
        """A message to a chat: the owner's, a run's first ("start"), or one another run or chat sent a run (its id:
        agents.send_to); a run counts the owner's and the others' apart. A run's, under its send lock (agents.py). The
        owner's message to an attempt's chat of a requirement's manager is refused: its packet is its instructions, and
        the workspace sends its first message as a sender of its own."""
        if sender == "owner" and self.controller.is_attempt(session_id):
            from . import controller as control
            raise WorkspaceError(control.NO_MESSAGE)
        if self.agents.is_run(session_id):
            async with self.agents.send_lock(session_id):
                return await self._chat_send(session_id, text, sender)
        return await self._chat_send(session_id, text, sender)

    async def _chat_send(self, session_id, text, sender):
        asked = None
        if self.agents.is_run(session_id):
            asked = self.agents.may_send(session_id)        # never once the run has ended
            if self.agents.task_runs.is_task(session_id):
                # a task's attempt: kept while it is frozen and given after its restart, else at its turn's end
                if not isinstance(text, str) or not text.strip():
                    raise WorkspaceError("Say something to send.")
                self.agents.task_runs.message(session_id, text)
                self.agents.sent(session_id, sender, answering=asked)
                return self.chat(session_id).view()
        c = self.chat(session_id)
        if c.client is None and self.running_programs() >= self.max_sessions:
            raise WorkspaceError(f"At most {self.max_sessions} programs run at once in a workspace: stop one first.")
        before = self._read(session_id)
        view = await c.send(text)
        # a closed chat given a message runs again, so it is back in the list, as a terminal session started again is
        self._back_in_the_list(session_id, before)
        if asked is not None:
            self.agents.sent(session_id, sender, answering=asked)
        note = self.take_budget_note(session_id)
        if note:
            # what the budget check of the start this message made said (budget_check.py): in the chat's own log, where
            # its page reads it, and in this message's answer; an agent's run's and a manager's attempt's are kept for
            # their first message, which starts their claude
            c.note("notice", text=note)
            view = dict(view, budget_notice=note)
        return view

    def chat_answer(self, session_id, ask_id, decision):
        if not isinstance(ask_id, str):
            raise WorkspaceError("Name the question to answer.")
        return self.chat(session_id).answer(ask_id, decision)

    async def chat_interrupt(self, session_id):
        if self.controller.is_attempt(session_id):
            # an attempt's turn is never ended by the page's interrupt
            from . import controller as control
            raise WorkspaceError(control.NO_CONTROL)
        return await self.chat(session_id).interrupt()

    # a chat's MCP servers (mcp.py): a repository's run once the owner approves them, bound to their configuration

    def _approved(self, repo):
        with self._mcp_lock:
            return {tuple(pair) for pair in mcp.load_approvals(self.root).get(repo, [])}

    def mcp_plan(self, session_id):
        if self.agents.is_run(session_id):
            return self.chat(session_id).mcp_plan()
        record = self._read(session_id)
        return mcp.plan(self._work(session_id), self._approved(record["repo"]))

    def _mcp_chat(self, session_id, changing=True):
        record = self._read(session_id)
        if record["program"] != "chat":
            raise WorkspaceError("That session is not a chat.")
        if changing and self.agents.is_run(session_id):
            # a run's servers are its agent's, given at its start, and never turned off or on (agents.py)
            raise WorkspaceError("A run's MCP servers are its agent's: change them on the agent's tools.")
        return record, self.chat(session_id)

    async def mcp_view(self, session_id):
        """A chat's MCP servers: the ones its repository declares, each approved or not (with how it runs), why none
        may run if so, and what the running claude says it has (or said last)."""
        record, c = self._mcp_chat(session_id, changing=False)
        plan = await asyncio.to_thread(self.mcp_plan, session_id)
        live = [dict(s, removed=(s["scope"], s["name"]) in c.mcp_removed) for s in await c.mcp_status() or []]
        return {"repo": record["repo"], "servers": plan["servers"], "strict": plan["strict"],
                "live": live if c.mcp_last is not None else None, "running": c.client is not None,
                "restart": c.restart}

    async def mcp_approve(self, session_id, name, fingerprint):
        """Approve one of the repository's servers as the page showed it (its fingerprint): claude starts it from the
        chat's next message. It replaces an earlier approval of that name, so what is approved is what the page shows
        (an old configuration coming back would otherwise run again untapped)."""
        record, c = self._mcp_chat(session_id)
        plan = await asyncio.to_thread(self.mcp_plan, session_id)
        server = next((x for x in plan["servers"] if x["name"] == name), None)
        if server is None:
            raise WorkspaceError("The repository declares no such server now.")
        if server["conflict"]:
            raise WorkspaceError("Two .mcp.json files define that server differently: it cannot be approved.")
        if not isinstance(fingerprint, str) or fingerprint != server["fingerprint"]:
            raise WorkspaceError("That server changed since the page showed it: look at it again.")
        with self._mcp_lock:
            approvals = mcp.load_approvals(self.root)
            pairs = [p for p in approvals.get(record["repo"], []) if p[0] != name] + [[name, fingerprint]]
            approvals[record["repo"]] = pairs[-mcp.MAX_APPROVALS:]
            mcp.save_approvals(self.root, approvals)
        c.restart = True
        c._emit("mcp_change", change="approved", name=chats._line(name), line=server["line"])
        return await self.mcp_view(session_id)

    async def _mcp_end(self, c):
        """A server stopped or removed: claude ends it now when it is idle (its conversation resumes with the next
        message), else with the next message. Not by turning it off: claude keeps that choice for every chat on the
        repository (~/.claude.json, seen with 2.1.274 and 2.1.277), and a server approved or added again would stay
        off."""
        if c.client is not None and c.state == "idle":
            await c.stop()
        else:
            c.restart = True

    async def mcp_revoke(self, session_id, name):
        """Stop using one of the repository's servers: ended now, or with the next message, and held back after."""
        record, c = self._mcp_chat(session_id)
        if not isinstance(name, str):
            raise WorkspaceError("Name the server.")
        with self._mcp_lock:
            approvals = mcp.load_approvals(self.root)
            approvals[record["repo"]] = [p for p in approvals.get(record["repo"], []) if p[0] != name]
            mcp.save_approvals(self.root, approvals)
        await self._mcp_end(c)
        c._emit("mcp_change", change="stopped", name=chats._line(name), line="")
        return await self.mcp_view(session_id)

    def _claude_mcp(self, repo, argv, record=None):
        try:
            p = subprocess.run(argv, cwd=self._repo_path(repo), env=program_env(self._account_env(record)),
                               capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise WorkspaceError(f"claude mcp did not run: {type(e).__name__}.") from None
        if p.returncode != 0:
            said = (p.stdout + p.stderr).decode("utf-8", "replace").strip().splitlines()
            raise WorkspaceError(chats._line(said[-1] if said else f"claude mcp failed ({p.returncode})."))

    async def mcp_add(self, session_id, spec):
        """Add a server with Claude Code's own `claude mcp add`: for this repository (its local scope) or for every
        repository in the workspace (its user scope); claude starts it from the chat's next message."""
        record, c = self._mcp_chat(session_id)
        try:
            argv = mcp.add_argv(self._claude() or "claude", spec)
        except mcp.McpError as e:
            raise WorkspaceError(str(e)) from None
        await asyncio.to_thread(self._claude_mcp, record["repo"], argv, record)
        c.mcp_fresh.add(spec["name"])
        c.restart = True
        c._emit("mcp_change", change="added", name=chats._line(spec["name"]),
                line=chats._line(spec.get("url") or spec.get("command") or ""))
        return await self.mcp_view(session_id)

    async def mcp_remove(self, session_id, name, scope):
        """Remove a server that `claude mcp add` gave this repository or the workspace."""
        record, c = self._mcp_chat(session_id)
        if not isinstance(name, str) or not mcp.NAME.fullmatch(name):
            raise WorkspaceError("That server's name cannot be removed from here: remove it with claude mcp remove.")
        if scope not in ("local", "user"):
            raise WorkspaceError("Only a server added for this repository or for the workspace is removed here.")
        await asyncio.to_thread(self._claude_mcp, record["repo"],
                                [self._claude() or "claude", "mcp", "remove", f"--scope={scope}", name], record)
        c.mcp_removed.add((scope, name))
        await self._mcp_end(c)
        c._emit("mcp_change", change="removed", name=name, line="")
        return await self.mcp_view(session_id)

    def usage(self, session_id):
        """What a session's `claude` wrote down of its tokens (usage.py), and the last status its status line recorded
        (statusline.py: the quota, for a subscriber, once the program has had a reply), or None; for a `codex` session,
        its tokens and the account's windows from Codex's own conversation files; a session of another program has
        neither."""
        record = self._read(session_id)
        if record["program"] == "codex":
            work = self._work(session_id)
            got = usage.codex_usage(work, self._account_folder(record, "codex"), since=record["created_at"])
            return dict(got, session=session_id, program="codex", available=True)
        if record["program"] not in ("claude", "chat"):
            return {"session": session_id, "program": record["program"], "available": False,
                    "reason": f"usage is read from claude's and codex's own files; a {record['program']} session "
                              "writes none here"}
        work = self._work(session_id)
        return dict(usage.claude_usage(work, self._account_folder(record, "claude")), session=session_id,
                    program=record["program"], available=True, status=statusline.read(self._dir(session_id)))

    # the control window's map

    def map_runs(self, views):
        """Each session's run as its terminal says it now, for map (sessionmap.agents): {id: (when this run started,
        a time with its fraction, or None when it is not running; when it ended, or None)}. Asked on the event loop
        with the views (the route), so a session's state and its run are read at the same moment. The fraction is
        what tells an agent last seen in the second this run started, stamped in whole seconds, from one of this run's:
        a sighting stamped in that second is taken as the earlier run's, since a claude takes longer than what is left
        of that second to start and begin an agent (compared in whole seconds, an earlier run's agent would show
        running)."""
        runs = {}
        for view in views:
            term = self.terminals.get(view.get("id"))
            ended = view.get("ended_at")
            ended = int(ended) if isinstance(ended, (int, float)) and not isinstance(ended, bool) else None
            started = getattr(term, "started", getattr(term, "started_at", None)) \
                if term is not None and term.running else None
            runs[view.get("id")] = (float(started) if isinstance(started, (int, float)) else None, ended)
        return runs

    def map(self, views=None, runs=None):
        """GET /map: every session the page's list shows (a closed one and a sign-in left out, as the list leaves them,
        so the two never disagree), oldest first, each with its state, account, model, effort, tokens in context and
        agents, as sessionmap.session_row reads them from its view and its folder; cheap enough to ask every few
        seconds (the same views the list is made of, and each session's own small files). `views` are sessions()'s,
        which the route makes on the event loop, where a chat's object is made (chat.py), and passes here to read the
        files off it. Each session's run is given as its terminal says it: when this run started, or, not running, when
        it ended, so an agent its program ended with it is not shown running (sessionmap._agent). A session whose own
        record cannot be read into a row is left out, never the map."""
        now = int(time.time())
        views = self.sessions() if views is None else views
        runs = self.map_runs(views) if runs is None else runs
        rows = []
        for view in views:
            if sessionmap.left_out(view):
                continue
            live_since, ended = runs.get(view.get("id"), (None, None))
            try:
                rows.append(sessionmap.session_row(view, self._dir(view["id"]), now, live_since, ended))
            except Exception as e:  # noqa: BLE001  one session's row never takes the map down; said where it is read
                self._say_left_out("map", view.get("id"), e)
                continue
        return {"at": now, "sessions": rows}

    def session_map(self, session_id):
        """GET /sessions/{id}/map: one session's tool calls and turns (sessionmap.session_calls), cut to 64 KB; its
        conversation the one its claude last spoke from (_own_conversation), found by its worktree in its account's
        folder, as its usage is. A session whose account was removed has its turns not read."""
        record = self._read(session_id)
        claude_dir = conversation = None
        if record["program"] in sessionmap.CLAUDE:
            try:
                claude_dir = self._account_folder(record, "claude")
                conversation = self._own_conversation(session_id)
            except WorkspaceError:
                claude_dir = conversation = None
        return sessionmap.session_calls(session_id, record["program"], self._dir(session_id), self._work(session_id),
                                        claude_dir, conversation)

    def terminal(self, session_id):
        """The session's terminal, running or finished; else the screen it saved before the agent last stopped; else
        None. A finished one still gives what the program last wrote and its exit code, so a viewer that attaches after
        a quick program ended (GitHub's sign-in when gh is already signed in, a program that fails at once) is shown
        why, not an empty terminal; and a workspace that slept shows each program's last screen."""
        self._read(session_id)
        return self.terminals.get(session_id) or terminals.SavedScreen.load(self._screen_path(session_id))

    def _screen_path(self, session_id):
        return os.path.join(self._dir(session_id), "screen")

    # a session's shells: the person's own, in the session's worktree, beside its program

    @staticmethod
    def _shell_number(n):
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_SHELLS:
            raise WorkspaceError(f"A session's shells are numbered 1 to {MAX_SHELLS}.")
        return n

    def shell(self, session_id, n):
        """The session's shell `n`, running or ended, else None (not opened since the agent began)."""
        self._read(session_id)
        return (self.shells.get(session_id) or {}).get(self._shell_number(n))

    def shell_start(self, session_id, cols=100, rows=30, n=None):
        """Open a shell in the session's worktree, beside its program (an agent or a chat), to run its tests, start its
        app, or look at what it did: up to MAX_SHELLS a session. `n` opens that one again once it has ended; without
        it, the first free place, else the first whose shell has ended. A shell is the person's own, as a shell
        session is; a claude or a gemini started in it has the workspace's gate, and what the gate refuses there is
        the session's. Must run in the event loop that serves its terminal. Gives the session and the shell's number."""
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has no worktree to open a shell in.")
        mine = self.shells.setdefault(session_id, {})
        if n is not None:
            n = self._shell_number(n)
            if n in mine and mine[n].running:
                return self.session(session_id), n
        else:
            free = [k for k in range(1, MAX_SHELLS + 1) if k not in mine]
            ended = [k for k in sorted(mine) if not mine[k].running]
            if not free and not ended:
                raise WorkspaceError(f"A session has at most {MAX_SHELLS} shells: close one first.")
            n = (free or ended)[0]
        argv = self.argv("shell")
        if self.running_programs() >= self.max_sessions:
            raise WorkspaceError(f"At most {self.max_sessions} programs run at once in a workspace: stop one first.")
        # PP_SHELL: what runs here is the person's, not the session's program: a claude started in it is gated as the
        # session's (PP_SESSION_DIR), but does not speak for the session's state (activity.py)
        env = program_env({"PP_SESSION_ID": session_id, "PP_SESSION_DIR": self._dir(session_id), "PP_SHELL": str(n)})
        mine[n] = terminals.Terminal(argv, self._work(session_id), env, cols, rows,
                                     on_activity=self.touch)
        return self.session(session_id), n

    async def shell_stop(self, session_id, n):
        sh = self.shell(session_id, n)
        if sh is not None:
            await sh.stop()
        return self.session(session_id)

    async def shell_close(self, session_id, n):
        """Hang the shell up if it runs, and let its place go: its tab is closed."""
        sh = self.shell(session_id, n)
        if sh is not None:
            await sh.stop()
            mine = self.shells.get(session_id, {})
            if mine.get(n) is sh:          # not a shell opened in its place meanwhile
                mine.pop(n)
        return self.session(session_id)

    async def _stop_shells(self, session_id):
        for sh in list((self.shells.pop(session_id, None) or {}).values()):
            await sh.stop()

    def argv(self, program):
        if program not in PROGRAMS:
            raise WorkspaceError("A session runs claude, codex, gemini, opencode, grok, qwen, kimi, mcode, a shell, a "
                                 "chat, or GitHub's or Claude's sign-in.")
        if program == "custom":
            raise WorkspaceError("An agent of your own starts from its command: choose it by name.")
        if program == "shell":
            shell = os.environ.get("SHELL", "")
            argv = [shell if os.path.isabs(shell) and os.access(shell, os.X_OK) else "/bin/sh", "-l"]
        elif program == "github":
            if shutil.which("gh", path=program_env().get("PATH")) is None:
                raise WorkspaceError("gh is not installed in this workspace.")
            argv = ["/bin/sh", "-c", GITHUB_SIGN_IN]
        elif program == "claude-sign-in":
            argv = [self._claude() or "claude", *CLAUDE_SIGN_IN[1:]]
        elif program == "codex-sign-in":
            # Codex's own sign-in: on the person's own machine its browser's; in a workspace machine, which has none,
            # a link and a one-time code to open on any device
            argv = ["codex", "login"] + ([] if self.link_repos else ["--device-auth"])
        elif program == "chat":
            if not chats.available():
                raise WorkspaceError("A chat needs the Claude Agent SDK, which this workspace does not have yet.")
            argv = [self._claude() or "claude"]     # the chat runs claude through the SDK (chat.py); here if it is
        else:
            argv = [(self._claude() or "claude") if program == "claude" else program]
            if program == "claude" and self.claude_settings:
                argv += ["--settings", self.claude_settings]
        if shutil.which(argv[0], path=program_env().get("PATH")) is None:
            raise WorkspaceError(f"{argv[0]} is not installed in this workspace.")
        return argv

    def _custom(self, custom):
        """An agent of the person's own, as the app gives it: {name, argv, task}, its program found where the
        workspace's programs are, or WorkspaceError saying what is wrong."""
        if not isinstance(custom, dict):
            raise WorkspaceError("Name the agent of your own to start.")
        name, command, task = custom.get("name"), custom.get("command"), custom.get("task")
        if not isinstance(name, str) or not name.strip() or len(name) > CUSTOM_NAME or _unseen(name):
            raise WorkspaceError(f"An agent's name is 1 to {CUSTOM_NAME} characters of plain text.")
        if not isinstance(command, str) or not command.strip() or len(command) > CUSTOM_COMMAND or _unseen(command):
            raise WorkspaceError(f"Its command is one line of 1 to {CUSTOM_COMMAND} characters.")
        if task not in CUSTOM_TASK:
            raise WorkspaceError("Its task goes in as its last argument, or is typed at its prompt.")
        try:
            argv = shlex.split(command)
        except ValueError:
            raise WorkspaceError("Its command has a quote that is not closed.") from None
        if not argv or len(argv) > CUSTOM_ARGS or argv[0].startswith("-"):
            raise WorkspaceError(f"Its command is a program and at most {CUSTOM_ARGS - 1} arguments.")
        if os.sep in argv[0] and not os.path.isabs(argv[0]):
            raise WorkspaceError("Give its program by name, as the workspace's PATH finds it, or by its full path.")
        if not _found(argv[0]):
            raise WorkspaceError(f"{argv[0]} is not installed in this workspace.")
        return {"name": " ".join(name.split()), "argv": argv, "task": task}

    def prepare(self, repo, program, task, base=None, settings=None, group=None, model_provider=None, pull=None,
                asked_by=None, custom=None, account=None, teleport=None, moved=None, continues=None, controlled=None):
        """Make a session's worktree and record (blocking: git runs here); the program is started by `launch`. A chat
        may carry its settings (`chat_settings`), which only a chat has, with one exception: a Claude Code
        session started from an agent's ask (`asked_by`) takes an effort, the one the person chose at Start, and gives
        it to its claude as --effort at every start, this one before its first prompt (launch gives it later ones). It
        must have a level's shape (_ask_settings), and it is taken only when the claude this start runs offers it, read
        once the worktree is made, in the worktree and with the start's environment, as the gate's floor is,
        and checked as the ask's own effort is (asks.check_effort): one that claude does not list, or any
        while what it offers is claude_cli's floor (its version, or its help's levels or permission modes, not read),
        refuses the Start in the words the ask's own check says, and the session is unmade as the floor's refusal
        unmakes one, so nothing of it is left and the agent's Start route puts the ask back. Against
        the asking session that read proves nothing either, as a later start's does not (start_effort): its programs can
        commit a version manager's pin to a base the ask may name, its own branch for one, and so choose which claude
        the first Start reads and runs; the level given is still the person's. The
        ask's own check, when the agent asks and no worktree exists, is against what the workspace keeps of its claude
        (asks.py, ask_offered), whose refusal at the floor adds asks.READ_AGAIN, words a Start does not say.
        A model, a permission mode or any other setting is
        still refused on such a session, as it is on any session other than a chat, and any setting at all on a
        session the person starts themselves, a terminal claude among them. `group` names the sessions started together
        on one task (the page fans a task out to several agents), so they can be compared; it is a label, nothing
        more. `model_provider` runs a claude session on another provider's model (providers.py), with the person's own
        key for it. `pull` starts it from a pull request's head on GitHub instead of a base (`_fetch_pull`), to review
        it or carry it on. `account` is the Claude Code or Codex account it starts on (accounts.py): an id, "default",
        or None for the one new sessions of its kind start on; another provider's model uses no account. `teleport`
        (a cloud session's id or link, or "" for Claude Code's own picker) starts claude with --teleport, which checks
        the repository, fetches the cloud session's branch, checks it out in the fresh worktree and loads its whole
        conversation: the session then takes that branch as its own (_adopted). `moved` (a Claude Code session moved
        here from a linked machine, as the app hands it: moves.py) starts claude with --resume and that session's own
        conversation, in a worktree of the repository whose origin is the machine's, at the commit the machine was on,
        with the work it had not committed laid on the working tree (_move_commits, _lay_move); the files the app sent
        for it go once it is started or refused. `continues` ({session, head}, the app's) makes it a session
        continued from another, cut from the commit that one's Finish made, checked before its program starts
        (_continues, _differs_from). `controlled` ({model, effort, prompt}) is a session the owner's controller starts
        (controller.py): a Claude Code terminal, on no other provider, given
        --model and its effort, the effort checked as an ask's is, and its first prompt the controller's three marked
        parts in place of the task, which is its name in the list; its map settings carry the controller's limit hooks
        (_with_map). A session of a program that runs an agent (BUDGET_CHECKED: a terminal agent's or the person's own;
        not a shell's, a sign-in's or a chat's) asks Proofpane's budget check while the workspace is paired
        (budget_check.py), before anything of it is made: one it refuses is refused in the product's own
        words, and a check that could not be made, or not all read, is said in launch's answer (`budget_notice`). The
        agent's routes and the controller call this under the agent's lock, so a Proofpane slow to answer holds what
        waits on that lock (another start, a removal, a clone, a finish, an account's change) for budget_check.WAIT
        seconds at most."""
        if controlled is not None:
            if program != "claude" or model_provider is not None or moved is not None or teleport is not None \
                    or continues is not None or asked_by or settings:
                raise WorkspaceError("A Manager's session is a Claude Code terminal, started as it is, no more.")
            return self._prepare(repo, program, task, base, settings, group, model_provider, pull, asked_by, custom,
                                 account, teleport, controlled=controlled)
        if moved is None:
            return self._prepare(repo, program, task, base, settings, group, model_provider, pull, asked_by, custom,
                                 account, teleport, continues=continues)
        try:
            try:
                move = moves.check(moved)
            except moves.MoveError as e:
                raise WorkspaceError(str(e)) from None
            return self._prepare(repo, program, task, base, settings, group, model_provider, pull, asked_by, custom,
                                 account, teleport, move, continues=continues)
        finally:
            moves.discard(self.root, moves.check_seq(moved.get("id")) if isinstance(moved, dict) else None)

    def _prepare(self, repo, program, task, base, settings, group, model_provider, pull, asked_by, custom, account,
                 teleport, move=None, continues=None, controlled=None):
        """prepare, with its move checked (moves.check), or None."""
        cloud = None
        transcript = None
        if continues is not None:
            continues = self._continues(continues, repo, program, base, pull, model_provider, teleport, move)
        if move is not None:
            if teleport is not None:
                raise WorkspaceError("A session is moved here or teleported, not both.")
            if program != "claude" or model_provider is not None:
                raise WorkspaceError("A moved session is continued in Claude Code: a claude session, on no other "
                                     "provider.")
            if pull is not None or base not in (None, ""):
                raise WorkspaceError("A moved session brings its own commit: it starts from the one the machine was "
                                     "on.")
            found = self._repository_with(move["remote"])
            if found is None:
                raise WorkspaceError("The repository is not in the workspace: clone it there first.")
            if repo not in (None, "") and repo != found:
                raise WorkspaceError(f"A moved session's repository is the one whose origin is the machine's: {found}.")
            repo = found
            transcript = moves.take(self.root, move["id"], "transcript")
            if not transcript:
                raise WorkspaceError("The moved session's conversation has not reached the workspace: its transcript "
                                     "was not sent.")
        if teleport is not None:
            if program != "claude" or model_provider is not None:
                raise WorkspaceError("A cloud session is continued in Claude Code, signed in with its claude.ai "
                                     "account: a claude session, on no other provider.")
            if pull is not None or base not in (None, ""):
                raise WorkspaceError("A cloud session brings its own branch: it starts from the repository's head.")
            if repo in (None, ""):
                raise WorkspaceError("A cloud session is continued in a checkout of its repository: choose it.")
            cloud = teleport_id(teleport)
        if model_provider is not None:
            if program != "claude" or model_provider not in providers.CATALOG:
                raise WorkspaceError("Another provider's model runs in Claude Code: a claude session on Kimi K3, "
                                     "MiniMax M3 or DeepSeek.")
            self._provider_key(model_provider)
        if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK:
            raise WorkspaceError(f"Say what the session is for, in at most {MAX_TASK} characters.")
        if group is not None and (not isinstance(group, str) or not _GROUP.match(group)):
            raise WorkspaceError("A group is 8 to 32 lowercase letters and digits.")
        own = self._custom(custom) if program == "custom" else None
        argv = list(own["argv"]) if own else self.argv(program)
        try:
            on = None if model_provider else self.accounts.for_session(program, account)
        except accounting.AccountError as e:
            raise WorkspaceError(str(e)) from None
        if controlled is not None:
            # the controller's model and effort (controller.py checks them against the owner's), the effort's
            # shape here and, once the worktree is made, whether the claude that starts offers it (below)
            chosen, effort = {}, controlled.get("effort")
            model = controlled.get("model")
            if not isinstance(effort, str) or not _EFFORT.fullmatch(effort):
                raise WorkspaceError("An effort is the name of one of Claude Code's levels.")
            # the owner's side's form for a model (the app's, a provider's id with capitals or a colon among them),
            # given as one argument, --model=<name>, so that no name reads as a flag
            if not isinstance(model, str) or model.startswith("-") or not _CONTROLLED_MODEL.fullmatch(model):
                raise WorkspaceError("Name a model as Claude Code does.")
        elif asked_by and program != "chat":
            # only a chat has settings to choose, but for the one exception: an effort, for a Claude Code
            # session started from an ask, as the person chose it at Start. Here it is checked for a level's shape
            # alone; whether the claude that starts offers it is read once the worktree is made, below, where and as
            # that claude starts, and never against what the workspace's own claude offers, and
            # one it does not offer refuses the Start there
            chosen, effort = self._ask_settings(settings, program)
        else:
            chosen, effort = self.chat_settings(settings), None
            if chosen and program != "chat":
                raise WorkspaceError("Only a chat has a model, an effort and a permission mode to choose.")
        if self.running_programs() >= self.max_sessions:
            raise WorkspaceError(f"At most {self.max_sessions} programs run at once in a workspace: stop one first.")
        # Proofpane's budget check for a session of a program that runs an agent (BUDGET_CHECKED; budget_check.py),
        # once the request is one the workspace takes and before anything of the session is made (its
        # record, its worktree, a claude's floor's read): refused in the product's own words, nothing made; what its
        # answer says of a check that could not be made, or not all of it read, kept for launch's answer below
        # (keep_budget_note). A chat asks it at each start of its claude (_chat_start)
        refused, budget_note = self._budget_refusal(budget_check.session_kind(program, model_provider)) \
            if program in BUDGET_CHECKED else (None, None)
        if refused:
            raise WorkspaceError(refused)
        if repo in (None, ""):
            if program not in WITHOUT_REPOSITORY:
                raise WorkspaceError(f"A {program} session works in a repository: clone one first.")
            sid = secrets.token_hex(6)
            self._bind(sid, on)
            os.makedirs(self._dir(sid))
            record = {"id": sid, "repo": None, "program": program, "task": task.strip(), "base": None,
                      "branch": None, "created_at": int(time.time()), "finished": None}
            if on:
                self._account_ready(on)
            self._write(record)
            if program in SIGN_INS:
                self._sign_ins[sid] = program
            return record, argv
        path = self._repo_path(repo)
        if move is not None:
            self._move_commits(path, repo, move)
            base = move["head"]
        if pull is not None:
            if base not in (None, ""):
                raise WorkspaceError("A session starts from a pull request or from a base, not both.")
            base = self._fetch_pull(path, repo, pull)
        base = "HEAD" if base in (None, "") else base
        if not isinstance(base, str) or not _REF.match(base) or ".." in base or base.endswith((".lock", "/")):
            raise WorkspaceError("Start from a branch, a tag or a commit of the repository.")
        code, sha = git("rev-parse", "--verify", "--quiet", "--end-of-options", base + "^{commit}", cwd=path,
                        ok=(0, 1))
        if code != 0:
            raise WorkspaceError(f"The repository has no commit {base} to start from.")
        # a controller's start, the one prepare the bar on starts covers (skill_improve.py):
        # steps 1 and 2 on the commit resolved, before the worktree is written,
        # the walk going only as far as the commits the controller's walk before its lock read; a refusal records no
        # task, and the controller gives it as its tool call's error
        bar = getattr(self, "skill_improve", None) if controlled is not None else None
        if bar is not None:
            why = bar.check_start(repo, commit=sha.strip(), kind="controller")
            if why:
                raise WorkspaceError(why)
        # what a continued session's check takes from the repository as it is before the worktree is made and its hooks
        # run: what its own checkout keeps out, the one skip-worktree set the check excuses; the
        # line-ending and link settings the files are read under; its git folder; and the worktrees it has, so that a
        # refusal removes only the one made for this session
        kept_out, pins, common, worktrees = set(), (), None, None
        if continues is not None:
            kept_out, pins = _kept_out(path), _reading_pins(path)
            _, common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path)
            common = common.strip()
            worktrees = _worktree_folders(common)
        sid = secrets.token_hex(6)
        self._bind(sid, on)
        os.makedirs(self._dir(sid))
        work = self._work(sid)
        try:
            # a continued session's worktree is written from the commit's own objects, never from what a replace ref
            # gives in their place, which its check does not read either
            # a controller's with the HISTORY guards too, which a post-checkout hook it runs inherits,
            # in a repository the record of skill proposals names; in any other, as today (skill_improve.guards)
            history = None
            if bar is not None and bar.guards(repo):
                from . import skill_improve
                history = skill_improve.with_history({})
            git(*(("--no-replace-objects",) if continues is not None else ()), "worktree", "add", "--quiet", "-b",
                f"pp/{sid}", work, sha.strip(), cwd=path, own=True, env=history)
        except WorkspaceError:
            shutil.rmtree(self._dir(sid), ignore_errors=True)
            if on:
                self.accounts.unbind(sid)
            raise
        self._git_made(work)
        if bar is not None:
            # step 3: the files the checkout wrote, its hooks having run, as Claude Code will read them
            why = bar.check_start(repo, work=work, kind="controller")
            if why:
                with contextlib.suppress(Exception):
                    self._unmake(sid, path, work, on)
                raise WorkspaceError(why)
        if move is not None:
            try:
                self._lay_move(work, move, transcript, on)
            except BaseException:
                with contextlib.suppress(Exception):    # what stopped it is what is said, whatever the clean-up meets
                    self._unmake(sid, path, work, on)
                raise
        if program in ("claude", "chat"):
            # a claude terminal or a chat starts no claude below the gate's floor: asked of the claude its start runs
            # (the terminal starts this very argv), in the worktree and with the start's environment, so once the
            # worktree is made and a move laid on it, since a pin there can choose the build (claude_floor_refusal);
            # refused, the session is unmade, so nothing of it is left
            try:
                start_env = self._claude_start_env(sid, program, model_provider)
                refused = self.claude_floor_refusal(argv[0], work, start_env)
                if not refused and effort:
                    # the person's choice at an ask's Start, checked as the ask's own effort is (asks.check_effort)
                    # against what this claude offers, read here as the floor's version is, in the new worktree with
                    # the start's environment: one it does not offer there, or any while what it
                    # offers is claude_cli's floor, refuses the Start in that check's words, and the session is unmade
                    # below, as the floor's refusal unmakes it
                    try:
                        asking.check_effort(effort, self._offered_by(argv[0], work, start_env))
                    except asking.AskError as e:
                        refused = str(e)
            except BaseException:
                with contextlib.suppress(Exception):
                    self._unmake(sid, path, work, on)
                raise
            if refused:
                with contextlib.suppress(Exception):
                    self._unmake(sid, path, work, on)
                raise WorkspaceError(refused)
        if self.claude_first_run:
            # a worktree is trusted through its repository: the session's claude, or one started in its shell, opens
            # at its prompt (written once per repository)
            first_run.trust(os.path.expanduser("~"), os.path.realpath(path))
        record = {"id": sid, "repo": repo, "program": program, "task": task.strip(), "base": sha.strip(),
                  "branch": f"pp/{sid}", "created_at": int(time.time()), "finished": None}
        base_ref = self._start_ref(path, base, pull)
        if base_ref:
            record["base_ref"] = base_ref       # what it started from, to bring it up to date with (update)
        if pull is not None:
            record["pull"] = pull
            record["pull_url"] = f"https://github.com/{self.label(repo)}/pull/{pull}"
        if chosen:
            record["chat_settings"] = chosen
        if program == "chat":
            self._chosen[sid] = dict(chosen or {})
        if group:
            record["group"] = group
        if model_provider:
            record["model_provider"] = model_provider
        if cloud is not None:
            record["teleport"] = cloud              # "" for the picker; its branch is taken once it is checked out
        if move is not None:
            # where it came from (the branch a label only: the session's own is pp/<id>), and its conversation, which
            # its claude resumes whenever it starts (_own_conversation)
            record["moved"] = {"id": move["id"], "session": move["session"], "branch": move["branch"],
                               "head": move["head"], "changes": move["changes"]}
        if asked_by:
            record["asked_by"] = asked_by       # started from that session's ask, by the person (asks.py)
        if own:
            record["custom"] = own              # its command as started, and again when started once more
        if on:
            self._account_ready(on, path)
        if continues is not None:
            # the one check a session continued in a new one has: a terminal program or a chat has no tool
            # to say it read what it was given, so what is checked is that its worktree is the commit it was cut from.
            # Whatever fails in it unmakes the new session as a difference does, in words that name no file (else
            # a git that refused would leave the session made, and say git's own last line)
            held = None
            try:
                differs = _differs_from(work, sha.strip(), f"pp/{sid}", common, kept_out, pins)
            except GitHeld as e:
                # git's places changed while an agent's run worked: refused in its own words, which say what to do
                # (not only that the worktree could not be read)
                held, differs = e, UNREADABLE
            except Exception:  # noqa: BLE001  any failure of the check refuses the session
                differs = UNREADABLE
            if differs:
                left = False
                with contextlib.suppress(Exception):
                    left = self._unmake(sid, path, work, on, worktrees, common)
                # a branch git could not remove is said, whatever stopped git: git's places held as the check read,
                # and also held only once the check was done, or git not answering in time
                said = (f" The new session's branch, pp/{sid}, was left in the repository, since git could not remove "
                        "it.") if left else ""
                if held is not None:
                    raise GitHeld(f"{held}{said}") if said else held
                if differs == UNREADABLE:
                    raise WorkspaceError("The new session's worktree could not be read against the commit it was cut "
                                         f"from. It was not started.{said}")
                raise WorkspaceError(f"The new session's worktree is not the commit it was cut from: {differs}. It was "
                                     f"not started.{said}")
            record["continued_from"] = continues
        self._write(record)
        self._origins[sid] = {"repo": repo, "asked_by": asked_by}
        if controlled is not None:
            self._models[sid] = controlled["model"]
        if move is not None:
            self._moves[move["id"]] = sid
        if effort:
            # an ask's session, its effort given once at this first start, before its first prompt: launch gives it at
            # each later start, from this copy (or the app's), never from the record, which its programs can write
            self._efforts[sid] = effort
            argv = argv + ["--effort", effort]
        if controlled is not None:
            argv = argv + [f"--model={controlled['model']}"]    # one argument: a name never read as a flag
        if cloud is not None:
            # the cloud session's own conversation is what claude opens with: the task is the session's name here
            argv = argv + ["--teleport"] + ([cloud] if cloud else [])
        elif move is not None:
            # so is a moved session's, brought from the machine
            argv = argv + ["--resume", move["session"]]
        elif program in PROMPTED:
            # the task is the agent's first prompt, as a chat's is its first message: each takes one on its command
            # line and stays interactive; one argument, no shell, so its text is only text
            flag = PROMPTED[program]
            prompt = controlled["prompt"] if controlled is not None else task
            argv = argv + ([flag] if flag else []) + [first_prompt(prompt)]
        elif own and own["task"] == "argument":
            argv = argv + [first_prompt(task)]          # an agent of the person's own, as they said it takes one
        if program == "claude":
            # the control window's map: its sources this claude takes, by the version the floor read above for this
            # start (_with_map); launch adds nothing to this command, and gives them to the commands it builds itself
            argv = self._with_map(sid, argv, work, controller=controlled is not None)
        self.keep_budget_note(sid, budget_note)
        return record, argv

    # a session continued in a new one, by the owner's press

    def _continues(self, continues, repo, program, base, pull, model_provider, teleport, move):
        """A new session that continues another, as the app names it ({session, head}): checked before anything is
        made. It runs Claude Code, a chat or Codex on no other provider's model, in the old session's repository, from
        the commit `head`, which is on the old session's branch and nothing else; its account is chosen as any
        session's is."""
        if not isinstance(continues, dict) or not isinstance(continues.get("session"), str) \
                or not _ID.match(continues["session"]) or not isinstance(continues.get("head"), str) \
                or not _OBJECT_ID.fullmatch(continues["head"]):
            raise WorkspaceError("A session continued in a new one names the session and the commit it starts from.")
        if program not in HANDOVER_PROGRAMS or model_provider is not None:
            raise WorkspaceError("A session is continued in Claude Code, a chat or Codex, on one of your accounts.")
        if pull is not None or teleport is not None or move is not None or base != continues["head"]:
            raise WorkspaceError("A session continued in a new one starts from the commit its Finish made, and from "
                                 "nothing else.")
        if self.agents.is_run(continues["session"]):
            raise WorkspaceError("An agent's run is not continued in a new session.")
        old = self._read(continues["session"])
        if not old.get("repo") or old["repo"] != repo:
            raise WorkspaceError("A session is continued in its own repository.")
        code, _ = git("merge-base", "--is-ancestor", continues["head"], f"refs/heads/{old['branch']}",
                      cwd=self._repo_path(repo), ok=(0, 1, 128))
        if code != 0:
            raise WorkspaceError("That commit is not on the session's branch.")
        return {"session": continues["session"], "head": continues["head"]}

    def observed(self, session_id, head):
        """What the workspace observed of a session's work, for a new session that continues it: the
        commits on its branch from its base to `head`, oldest first, by id alone (a commit's message is text a program
        may have written), and the names of the files those commits changed, as git gives them (a name is text a
        program chose, which the app marks as data). At most MAX_OBSERVED_COMMITS ids and MAX_OBSERVED_FILES names, the
        rest counted. Read only. {session, base, head, commits, more_commits, files, more_files}"""
        # an agent's run is refused before its record is read, as prepare refuses one
        if self.agents.is_run(session_id):
            raise WorkspaceError("An agent's run is not continued in a new session.")
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has no work to continue.")
        if not isinstance(head, str) or not _OBJECT_ID.fullmatch(head):
            raise WorkspaceError("Name the commit by its full id.")
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        base = self._base(record)
        # the commits read as git holds them, never what a replace ref gives in their place, as the new session's
        # worktree is written and checked (a replacement of the head would name
        # a file the commit does not hold in the first prompt's list)
        mine = ("--no-replace-objects",)
        if git(*mine, "merge-base", "--is-ancestor", head, f"refs/heads/{record['branch']}", cwd=work,
               ok=(0, 1, 128))[0] != 0:
            raise WorkspaceError("That commit is not on the session's branch.")
        if git(*mine, "merge-base", "--is-ancestor", base, head, cwd=work, ok=(0, 1, 128))[0] != 0:
            raise WorkspaceError("That commit does not come after the session's base.")
        _, ids = git(*mine, "rev-list", "--reverse", f"{base}..{head}", cwd=work)
        commits = [i for i in ids.split() if _OBJECT_ID.fullmatch(i)]
        _, out = git(*mine, "-c", "core.quotepath=off", "diff", "--name-only", "-z", "--no-renames", base, head,
                     cwd=work)
        names = [n for n in out.split("\0") if n]
        return {"session": session_id, "base": base, "head": head, "commits": commits[:MAX_OBSERVED_COMMITS],
                "more_commits": max(0, len(commits) - MAX_OBSERVED_COMMITS), "files": names[:MAX_OBSERVED_FILES],
                "more_files": max(0, len(names) - MAX_OBSERVED_FILES)}

    # a Claude Code session moved here from a linked machine (moves.py)

    def move_file(self, seq, name, data):
        """Keep a file the app sent for a move, its transcript or its bundle, until the session it came for starts or
        is refused. {move, file, size}"""
        seq = moves.check_seq(seq)
        if seq is None or name not in moves.FILES:
            raise NotFound("No such file of a move.")
        if not isinstance(data, bytes) or not data:
            raise WorkspaceError(f"The move's {name} is empty.")
        if len(data) > moves.MAX_FILE:
            raise WorkspaceError(f"A move's {name} is at most {moves.MAX_FILE // (1024 * 1024)} MiB.")
        try:
            moves.store(self.root, seq, name, data)
        except moves.MoveError as e:
            raise WorkspaceError(str(e)) from None
        return {"move": seq, "file": name, "size": len(data)}

    def moved_already(self, moved):
        """The session a move started already, for the app's second try at a move whose first answer did not reach it
        (its request ran out of time, say): one move makes one session. None when it started none. Only this agent's
        own word is taken (_moves); a move that a session's record alone says it started (one made before this agent
        started, or a record its programs wrote), or whose session was removed since, is refused, never answered with
        that session. The files sent again go, either way."""
        seq = moves.check_seq(moved.get("id")) if isinstance(moved, dict) else None
        if seq is None:
            return None
        sid = self._moves.get(seq)
        if sid is not None and os.path.isdir(self._dir(sid)):
            moves.discard(self.root, seq)
            return sid
        claimed = sid or next((r["id"] for r in self._records() if isinstance(r.get("moved"), dict)
                               and r["moved"].get("id") == seq), None)
        if claimed is None:
            return None
        moves.discard(self.root, seq)
        raise WorkspaceError(f"This move started session {claimed} already: one move makes one session.")

    def _repository_with(self, remote):
        """The workspace's repository whose origin is the same as `remote`, compared as one name (moves.remote_name):
        the first by name when two are; None when none is."""
        wanted = moves.remote_name(remote)
        if wanted is None:
            return None
        for name in sorted(os.listdir(self.repos_dir)):
            path = os.path.join(self.repos_dir, name)
            if not _NAME.match(name) or agentry.is_scratch(name) or not os.path.exists(os.path.join(path, ".git")):
                continue
            try:
                code, url = git("config", "--get", "remote.origin.url", cwd=path, ok=(0, 1))
            except (OSError, WorkspaceError):
                continue
            if code == 0 and moves.remote_name(url.strip()) == wanted:
                return name
        return None

    def _move_commits(self, path, repo, move):
        """Make a move's head, and its changes, commits of the repository: from the bundle the app sent when there is
        one (the commits it builds on that the repository lacks fetched from origin first), else from origin, which has
        them. Refused, saying why, when they cannot be had: the bundle not sent, not one git can take, or built on a
        commit found nowhere; or one of them in neither the bundle nor origin; or the changes not a commit on head."""
        wanted = [move["head"]] + ([move["changes"]] if move["changes"] else [])
        if move["bundle"]:
            # read once, whole, through the descriptor opened on it (moves.take), and given to git on its standard
            # input: no path of it is opened again, which a session's program could have swapped for a pipe meanwhile
            bundle = moves.take(self.root, move["id"], "bundle")
            if bundle is None:
                raise WorkspaceError("The moved session's commits have not reached the workspace: its bundle was not "
                                     "sent.")
            needs = moves.prerequisites(bundle)
            self._fetch_commits(path, repo, needs)
            lacking = self._lacks(path, needs)
            if lacking:
                raise WorkspaceError(f"The moved session's bundle builds on commit {lacking[0][:12]}, which is neither "
                                     "in the repository nor on its origin.")
            try:
                # every object in it, whatever its refs are named: none of them is made a ref here
                git("bundle", "unbundle", "-", cwd=path, input=bundle)
            except WorkspaceError:
                raise WorkspaceError("The moved session's bundle is not one git can take into its repository.") \
                    from None
        self._fetch_commits(path, repo, wanted)
        missing = self._lacks(path, wanted)
        if missing:
            raise WorkspaceError(f"The moved session's commit {missing[0][:12]} is neither in what was sent nor on the "
                                 "repository's origin.")
        if move["changes"]:
            # a commit of its own on head, as the machine makes it, whose tree is laid on head's (_lay_move)
            _, parent = git("rev-parse", "--verify", "--quiet", f"{move['changes']}^1", cwd=path, ok=(0, 1, 128))
            second = git("rev-parse", "--verify", "--quiet", f"{move['changes']}^2", cwd=path, ok=(0, 1, 128))[0]
            if parent.strip() != move["head"] or second == 0:
                raise WorkspaceError("The work not committed is not a commit on head.")

    @staticmethod
    def _lacks(path, commits):
        """Those of `commits` that are not commits of the repository at `path`."""
        return [c for c in commits
                if git("rev-parse", "--verify", "--quiet", f"{c}^{{commit}}", cwd=path, ok=(0, 1, 128))[0] != 0]

    def _fetch_commits(self, path, repo, commits):
        """Fetch from the repository's origin those of `commits` it lacks: by their ids, then, if any is still missing
        (a host may not give a commit asked for by its id), every branch of origin's. One fetch of a repository at a
        time; what is missing after it is for the caller to say."""
        if not self._lacks(path, commits):
            return
        with self._fetch_locks.setdefault(repo, threading.Lock()):
            missing = self._lacks(path, commits)
            if missing:
                git("fetch", "--quiet", "--no-tags", "origin", *missing, cwd=path, ok=(0, 1, 128), timeout=SHIP_TIMEOUT)
            if self._lacks(path, commits):
                git("fetch", "--quiet", "--no-tags", "origin", cwd=path, ok=(0, 1, 128), timeout=SHIP_TIMEOUT)

    def _lay_move(self, work, move, transcript, account_id):
        """A moved session's work and conversation in its new worktree, which is at its head: its changes, when there
        are some, laid on the working tree and not committed, so its files are added, changed and deleted as they were
        on the machine and the index is left at head; and its transcript in the folder where Claude Code keeps the
        worktree's conversations, the account's the session runs on, under the session's id, with each entry's cwd in
        the machine's folder made the worktree's (moves.rewrite)."""
        if move["changes"]:
            # the index and the files to the changes' tree, then the index alone back to head: each file as a checkout
            # writes it (its attributes, its line endings), never a patch over the files
            git("read-tree", "-u", "--reset", move["changes"], cwd=work)
            git("reset", "--quiet", cwd=work)
        real = os.path.realpath(work)
        account = self.accounts.get(account_id) if account_id else None
        folder = self.accounts.folder(account) if account else accounting.default_folder("claude")
        try:
            moves.write_whole(os.path.join(folder, "projects", usage.slug(real), move["session"] + ".jsonl"),
                              moves.rewrite(transcript, move["cwd"], real))
        except OSError as e:
            raise WorkspaceError("The moved session's conversation could not be put where Claude Code keeps it: "
                                 f"{e.strerror or e}.") from None

    def _unmake(self, sid, path, work, account_id, worktrees=None, common=None):
        """A session half made gone: its worktree, its branch, its folder and its account's binding, each step taken
        whatever git said at the one before (a git refused where git's places changed still leaves no folder or
        binding). `worktrees`, the names of the repository's worktree folders (under `common`, its git folder) before
        the session's worktree was made, is given for a continued session its check refused:
        a worktree folder made since that names a place in this session's folder, or names none
        that can be read (a pipe, which any git reading the repository's worktrees would wait on), is this session's,
        and is removed here, a locked one too, with the session's own folder and the worktree in it; never git's prune,
        which removed every worktree it could not find, the owner's own among them. git's remove is not run for such a
        session at all: it reads every worktree of the repository, each
        one's gitdir and its HEAD among what it reads, and would wait out its whole timeout on either made a pipe, the
        owner's own worktree's among them. Any other session half made,
        git removes, forced twice, a locked one too. True when the session's branch is left, git having failed to
        remove it, whatever stopped it (its places changed while an agent's run worked, which hold the whole
        repository, or git did not answer in time)."""
        mine = []
        if worktrees is not None and common:
            folder = os.path.realpath(self._dir(sid))
            for name in sorted(_worktree_folders(common)):
                admin = os.path.join(common, "worktrees", name)
                named = _small_git_file(admin, "gitdir")
                place = os.path.realpath(os.path.join(admin, named.strip())) if named is not None else None
                if name not in worktrees and (place is None or place.startswith(folder + os.sep)):
                    mine.append(admin)
        else:
            with contextlib.suppress(WorkspaceError):
                git("worktree", "remove", "--force", "--force", work, cwd=path, ok=(0, 128))
        for admin in mine:
            shutil.rmtree(admin, ignore_errors=True)
        self.places.forget(work)
        left = False
        try:
            git("update-ref", "-d", f"refs/heads/pp/{sid}", cwd=path, ok=(0, 1, 128))
        except WorkspaceError:
            left = True
        shutil.rmtree(self._dir(sid), ignore_errors=True)
        if account_id:
            self.accounts.unbind(sid)
        return left

    def start_effort(self, session_id, effort=None, claude=None):
        """For a claude terminal started again: (the effort this start gives it, or None; the words saying
        why it gives none it would otherwise give, or None). The effort is this agent's own copy of the one a session
        started from an ask was started with, and, where this agent keeps none, `effort`, the app's copy (its record
        of the answer), which must then have the shape of a level's name; never the session's record, which its
        programs can write. This agent's copy comes first, so an app's copy is not read while it is there, whatever
        its shape. This agent keeps none for a session the person started at Claude Code's
        default, and the app's copy then says none too, so it is the app's copy that decides each later start of it;
        and none after this agent has started again, when the app's copy gives the effort. Whichever copy gives it, it
        is given only while the claude this start runs offers it, read at this start (_offered_by):
        `claude`, the one the start route checked for the gate's floor (claude_floor_for; else the claude a start
        takes now), run in the session's worktree with the environment the session's claude starts with, for its
        `--version` and its `--help`, and nothing of it kept, so a claude replaced under a running agent, or one a
        version manager's pin in the worktree chooses, is the one read. One it does not list, or any while its version
        or its help was not read for them (`floor`) or nothing could be read, is
        not given: the session starts at Claude Code's default and the start says so in these words, and neither copy
        is changed, so a later start whose claude offers it again gives it again. Against a session itself this read
        proves nothing, as claude_floor_refusal's does not: the session's programs run as the workspace's user and can
        write a version manager's pin in its worktree that chooses another build, and so decide whether a later start
        gives the level; which level is given comes only from the two copies, which they cannot write.
        The reads block for as long as each takes, up to claude_cli.TIMEOUT, so the agent's start route asks this
        off the event loop and before its lock, after claude_floor_for, and the app waits for a start for as long as
        the three reads can take (its START_WAIT)."""
        own = self._efforts.get(session_id)
        if own is None:
            if effort in (None, ""):
                effort = None
            elif not isinstance(effort, str) or not _EFFORT.fullmatch(effort):
                raise WorkspaceError("An effort is the name of one of Claude Code's levels.")
        try:
            record = self._read(session_id)
        except Exception:  # noqa: BLE001  launch, which follows, says what is wrong with the session
            return None, None
        given = own or effort
        if record["program"] != "claude" or not given:
            return None, None
        cwd = self._work(session_id) if record["repo"] else self.root
        env = self._claude_start_env(session_id, "claude", record.get("model_provider"))
        return self._offered_effort(given, self._offered_by(claude or self._claude(), cwd, env))

    def launch(self, session_id, argv=None, cols=100, rows=30, claude=None, effort=None, prompt=None):
        """Start the session's program in its worktree; must run in the event loop that serves its terminal. `claude`,
        for a claude terminal started again, is the claude the start route checked (claude_floor_for), started in place
        of the one a start would take now. `effort`, for a claude terminal started again, is the effort this start
        gives it, as start_effort chose it off the event loop: this agent's copy or the app's, and only one
        the claude this start runs offered when start_effort read it, in the session's worktree, just before this
        start; it is given once, before the flags that pick the conversation up. This agent's
        memory keeps no effort for a session started at Claude Code's default, so the app's record, which then holds
        none too, decides each of its later starts (start_effort). A command built by prepare (`argv`) has its effort
        already, and is given none here. A session the owner's controller started is given its model at each later
        start too (_models), and `prompt`, the one line the controller's continue_task sends after its account's
        reset (controller.FIXED_LINE), goes last, after the flags that pick its conversation up; it may not ask. A
        terminal's view carries `budget_notice` where this start's budget check could not be made, or could not read
        every budget (prepare's, claude_floor_for's: budget_check.py): its words, once; a terminal keeps no log of its
        own beside its program's screen, so the answer is where it is said."""
        if effort in (None, ""):
            effort = None
        elif not isinstance(effort, str) or not _EFFORT.fullmatch(effort):
            raise WorkspaceError("An effort is the name of one of Claude Code's levels.")
        record = self._read(session_id)
        if record["program"] == "chat":
            self.chat(session_id)       # its claude starts with the first message, through the SDK
            self._back_in_the_list(session_id, record)
            return self.session(session_id)
        running = self.terminals.get(session_id)
        if running is not None and running.running:
            raise WorkspaceError("The session's program is already running.")
        built = argv is None            # a command prepare built has the map's sources already (_prepare)
        if argv is None:
            argv = list(record["custom"]["argv"]) if record["program"] == "custom" and isinstance(
                record.get("custom"), dict) and isinstance(record["custom"].get("argv"), list) else \
                self.argv(record["program"])
            if record["program"] == "claude" and claude:
                argv[0] = claude
            if record["program"] == "claude" and effort:
                # an ask's session started again: its effort at this start too, once, before the flags that
                # pick its conversation up, as start_effort chose it
                argv = argv + ["--effort", effort]
            if record["program"] == "claude" and self._models.get(session_id):
                argv = argv + [f"--model={self._models[session_id]}"]     # a controller's session's (prepare)
            if self.running_programs() >= self.max_sessions:
                raise WorkspaceError(f"At most {self.max_sessions} programs run at once in a workspace.")
            if record["program"] == "claude" and record["repo"]:
                # started again: claude picks up its own conversation, the one its hooks named (activity.py), and not
                # one a claude in the session's shell had in the same worktree; else the worktree's latest
                own = self._own_conversation(session_id)
                if own:
                    argv = argv + ["--resume", own]
                elif self._has_transcript(session_id):
                    argv = argv + ["--continue"]
            if record["program"] == "claude" and prompt:
                argv = argv + [first_prompt(prompt)]
            # opencode is not continued: its --continue takes the repository's last session, which may be another
            # worktree's, and errs when there is none (seen with 1.18.34); its own session list reopens one
        if record["program"] == "claude":
            # Proofpane's core (core_plugin.py), at every start of a claude terminal, a new one's and one started
            # again alike: the plugin once the workspace is paired, unless a managed setting this start applies
            # refuses --plugin-dir; the flags before every other argument, the first prompt last
            argv = argv[:1] + core.plugin_args(self.core_plugins(self._account_folder(record, "claude"))) + argv[1:]
        extra = {"PP_SESSION_ID": session_id, "PP_SESSION_DIR": self._dir(session_id)}
        extra.update(self._account_env(record))
        cwd = self._work(session_id) if record["repo"] else self.root
        if record["program"] == "claude" and built:
            # the control window's map: its sources this claude takes, by the version its floor read for this start
            # (claude_floor_for); a first start's command, prepare's, was given them there. A session the owner's
            # controller watches (open in its record) is given its limit hooks and setting too, and never started
            # without them, on which its wait and its continue rest; one that ended is the owner's to run as any
            watched = self.controller.watching(session_id)
            argv = self._with_map(session_id, argv, cwd, controller=watched)
            if watched and not self.own_settings(session_id, argv):
                raise WorkspaceError("The owner's Manager's session could not be given its limit hooks (its "
                                     "settings file): it is not started.")
        if record["program"] == "claude" and record.get("model_provider") in providers.CATALOG:
            # another provider's model: its endpoint and model names, from the vendor's own instructions; the key comes
            # from the settings file only
            argv = self._on_provider(session_id, record["model_provider"], argv)
            extra.update(providers.CATALOG[record["model_provider"]]["env"])
        if record["program"] == "claude" and self.ade_hook and not self._holds_ade_hook(argv):
            # a hosted workspace's claude terminal, a first start's and a later one's alike, the owner's controller's
            # among them, starts with the approval hook in the settings file it is named or not at all
            raise WorkspaceError(HELD_TERMINAL_REFUSED)
        if record["program"] == "custom" and self.ade_hook and self._runs_claude(argv):
            # and a program of the owner's own that is Claude Code is not started at all: it would not hold, and a
            # session can write its own record so (_read), to start its next claude past the check above
            raise WorkspaceError(CUSTOM_CLAUDE_REFUSED)
        if record["program"] == "gemini":
            # Gemini CLI unites every settings file's `hooksConfig.disabled` (0.62.0), so a name the user's or the
            # repository's settings put there switches the gate off, whatever the system settings say: it does not
            # start while one of them does
            said = gemini_gate_switched_off(os.path.expanduser("~"), cwd)
            if said:
                raise WorkspaceError(f"Gemini CLI's settings in {said} switch the workspace's gate off "
                                     f"(hooksConfig.disabled names {GATE_HOOK_NAME}): take it out to start Gemini CLI.")
            # no browser here: Gemini's sign-in with Google prints its page and waits for the code in the terminal
            extra["NO_BROWSER"] = "true"
            if self.gemini_settings:
                extra["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = self.gemini_settings
        if record["program"] == "grok":
            # no browser here either: Grok Build signs in with a device code it prints; and it does not update itself,
            # being root's (a new version comes with a new image)
            extra["GROK_LOGIN_DEVICE_FLOW"] = "1"
            extra["GROK_DISABLE_AUTOUPDATER"] = "1"
        if record["program"] == "qwen":
            # no browser here: Qwen Code's sign-in prints the page to open (its system settings keep it from updating);
            # and its safe and bare modes, which turn every hook off, are off: set here, a repository's .env cannot
            # turn them on (measured with Qwen Code 0.24.7)
            extra["NO_BROWSER"] = "true"
            extra["QWEN_CODE_SAFE_MODE"] = "0"
            extra["QWEN_CODE_SIMPLE"] = "0"
            if self.qwen_settings:
                extra["QWEN_CODE_SYSTEM_SETTINGS_PATH"] = self.qwen_settings
        if record["program"] == "kimi":
            # Kimi Code does not update itself (no check, no install: its own switch, 2.1.1); a new version comes with
            # a new image
            extra["KIMI_CODE_NO_AUTO_UPDATE"] = "1"
        if record["program"] == "mcode":
            # MiniMax Code reports each step of a turn to MiniMax for its evaluation unless this is set (0.6.1)
            extra["MCODE_DISABLE_EVAL_CAPTURE"] = "1"
        self.asks.revoke(session_id)            # a token lives as long as the program it was given to
        origin = self._origins.get(session_id) or {"repo": record["repo"], "asked_by": record.get("asked_by")}
        # a session the owner's controller started may not ask, as one started from an ask may not:
        # by the controller's record, never the session's own
        may_ask = not origin["asked_by"] and not self.controller.started(session_id)
        token = self.asks.issue(session_id, origin["repo"], may_ask) \
            if origin["repo"] and record["program"] in asking.AGENTS and self.ask_url else None
        if token:
            # the person lets agents ask: this program's own token, the agent's address, and `proofpane` (asks.py),
            # after the PATH's own folders, so nothing a session writes there comes before them; Claude Code and Codex
            # are told so in their instructions, before their other arguments
            extra["PP_ASK_TOKEN"] = token
            extra["PP_ASK_URL"] = self.ask_url
            extra["PATH"] = program_env().get("PATH", "") + os.pathsep + self._command_launcher()
            if record["program"] == "claude":
                argv = argv[:1] + ["--append-system-prompt", asking.NOTE] + argv[1:]
            elif record["program"] == "codex":
                argv = argv[:1] + ["-c", "developer_instructions=" + json.dumps(asking.NOTE)] + argv[1:]
        env = program_env(extra)
        # a sign-in's screen holds a one-time code or link, and a shell session's is where a person types and reads
        # their own secrets (`gh auth token`): neither is kept past the agent; an agent's is, as its transcript is
        save = None if record["program"] in ("github", "claude-sign-in", "codex-sign-in", "shell") \
            else self._screen_path(session_id)
        self.terminals[session_id] = terminals.Terminal(argv, cwd, env, cols, rows,
                                                        on_activity=self.activity_of(session_id), save=save)
        self._back_in_the_list(session_id, record)
        view = self.session(session_id)
        note = self.take_budget_note(session_id) if record["program"] in BUDGET_CHECKED else None
        return dict(view, budget_notice=note) if note else view

    def _back_in_the_list(self, session_id, record):
        """A closed session that runs again, started or given a message, is back in the page's list, as an
        open would put it: its mark, as `record` was read before the start, taken away once the start is done, so a
        start or a message refused leaves it closed."""
        if record.get("closed"):
            self._update(session_id, lambda r: r.pop("closed", None))

    def programs(self, also=()):
        """Which terminal agents this workspace has, for the page to offer only what will start, and whether its claude
        terminals and chats hold their gated commands (held); and of `also`, the programs of the person's
        own agents (custom_agents.py), whether each is found as its session would find it."""
        path = program_env().get("PATH")
        out = {"programs": {p: shutil.which((self._claude() or "claude") if p == "claude" else p, path=path) is not None
                            for p in AGENTS},
               # whether its claude terminals and chats hold their gated commands for the approval queue
               "held": self.held()}
        if also:
            out["also"] = {name: _found(name) for name in also}
        return out

    def _own_conversation(self, session_id):
        """The conversation the session's own claude last spoke from, as its hooks recorded it (activity.json's
        `program_session`), else the one it was moved here with (its record's `moved`), when Claude Code still has its
        transcript in this worktree's folder; else None."""
        got = sessionmap.read_json(os.path.join(self._dir(session_id), "activity.json"), ACTIVITY_FILE)
        own = got.get("program_session") if isinstance(got, dict) else None
        record = self._read(session_id)
        moved = record.get("moved") if isinstance(record.get("moved"), dict) else {}
        work = os.path.realpath(self._work(session_id))
        folder = os.path.join(self._account_folder(record, "claude"), "projects", usage.slug(work))
        for named in (own, moved.get("session")):
            if isinstance(named, str) and _CONVERSATION.match(named) and os.path.isfile(
                    os.path.join(folder, named + ".jsonl")):
                return named
        return None

    def _has_transcript(self, session_id):
        """Whether Claude Code has a conversation for this session's worktree (usage.py says where it keeps them):
        `--continue` with none refuses to start."""
        work = os.path.realpath(self._work(session_id))
        folder = os.path.join(self._account_folder(self._read(session_id), "claude"), "projects", usage.slug(work))
        try:
            return any(n.endswith(".jsonl") for n in os.listdir(folder))
        except OSError:
            return False

    async def stop(self, session_id):
        self._read(session_id)
        if session_id in self.agents.task_runs.live:
            # a task's attempt's chat, which task_runs registers as the run's session: stopped here, the attempt would
            # stay working with no claude until its time limit, and an end of the attempt here would not be the owner's
            # Stop, so its task would go on to a Next on... or Continue on... pressed before; 409, nothing
            # stopped
            raise agentry.TaskConflict(ATTEMPT_STOP)
        self.controller.stopping(session_id)        # a controller's session: stopped, not failed (controller.py)
        term = self.terminals.get(session_id)
        if term is not None:
            await term.stop()
        c = self.chats.get(session_id)
        if c is not None:
            await c.stop()
        return self.session(session_id)

    async def close(self, session_id):
        """Close a session, like stop running plus hide, not delete and not archive:
        its program stopped as stop stops it, a task's attempt's chat refused as there, its own shells stopped as Stop
        in their tabs stops one (their places kept), and its record marked `closed`, which the page leaves out of its
        list. Nothing else changes: its worktree, branch and conversation stay, and it is no archive of its own.
        Opening it again (reopen), starting it again (launch), or a message to a closed chat, takes the mark away. The
        mark comes first, so that every list read while the program stops (a grace of seconds) leaves it out, and an
        open in that time is kept; a stop refused, or failing, takes it away again. A task's attempt's is refused
        before anything is written: its record is its run's, which the attempt's own thread writes too."""
        self._read(session_id)
        if session_id in self.agents.task_runs.live:
            raise agentry.TaskConflict(ATTEMPT_STOP)
        self._update(session_id, lambda record: record.__setitem__("closed", True))
        try:
            await self.stop(session_id)
            for sh in list((self.shells.get(session_id) or {}).values()):
                await sh.stop()
        except BaseException:
            self._update(session_id, lambda record: record.pop("closed", None))
            raise
        return self.session(session_id)

    def reopen(self, session_id):
        """A closed session back in the page's list; nothing else of it changes."""
        self._update(session_id, lambda record: record.pop("closed", None))
        return self.session(session_id)

    def finish(self, session_id, author_name, author_email):
        """Commit the worktree (blocking) and report {branch, base, head, committed, diff or too_large, files}."""
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has no change to finish.")
        if self.agents.is_run(session_id):
            return self._finish_run(session_id, record)
        work = self._work(session_id)
        record = self._settled(session_id)
        if record.get("merging"):
            raise WorkspaceError("A merge from its branch is under way: resolve its conflicts and commit it, or give it "
                                 "up, first.")
        busy = self._own_work_under_way(work, record["branch"], branch_check=False)
        if busy:
            raise WorkspaceError(f"Its worktree is {busy}: let its agent finish or undo that first, or its conflict "
                                 "markers would be committed.")
        held = self._teleport_held(session_id, record)
        if held:
            raise WorkspaceError(held)
        self.controller.finishing(session_id)       # a task's end judged on the head before this commit (controller.py)
        git("add", "--all", cwd=work)
        code, _ = git("diff", "--cached", "--quiet", cwd=work, ok=(0, 1))
        committed = code == 1
        if committed:
            ident = []
            if git("config", "--get", "user.email", cwd=work, ok=(0, 1))[0] != 0:
                ident = ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}"]
            title, _, rest = record["task"].partition("\n")
            message = title.strip()[:200] + "\n\n" + (rest.strip() + "\n\n" if rest.strip() else "")
            message += f"Proofpane-Session: {session_id}\n"
            git(*ident, "commit", "--quiet", "--file", "-", cwd=work, input=message.encode("utf-8"), own=True)
        _, head = git("rev-parse", "HEAD", cwd=work)
        head = head.strip()
        # names past ASCII as they are, not as \ooo escapes (a name git still quotes, the page and Verify read back)
        _, diff = git("-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", "--no-textconv", self._base(record),
                      head, cwd=work)
        _, names = git("diff", "--name-only", "-z", self._base(record), head, cwd=work)
        files = len([n for n in names.split("\0") if n])
        too_large = len(diff.encode("utf-8")) > self.max_diff
        self._update(session_id, lambda r: r.update(finished={"head": head, "at": int(time.time())}))
        return {"session": session_id, "repo": record["repo"], "label": self.label(record["repo"]),
                "task": record["task"], "branch": record["branch"], "base": record["base"], "head": head,
                "committed": committed, "files": files, "too_large": too_large,
                "diff": None if too_large else diff}

    def _finish_run(self, session_id, record):
        """An agent's run is finished from what it committed at its end, its base to its head: nothing more is
        committed here, since its worktree is the agent's and may already hold the next run's work."""
        run = record["run"]
        if run["state"] not in agentry.ENDED or not run.get("head"):
            raise WorkspaceError("This run is still under way: finish it once it has ended.")
        work = self._work(session_id)
        base, head = self._base(record), run["head"]
        _, diff = git("-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", "--no-textconv", base, head,
                      cwd=work)
        _, names = git("diff", "--name-only", "-z", base, head, cwd=work)
        files = len([n for n in names.split("\0") if n])
        too_large = len(diff.encode("utf-8")) > self.max_diff
        self._update(session_id, lambda r: r.update(finished={"head": head, "at": int(time.time())}))
        return {"session": session_id, "repo": record["repo"], "label": self.label(record["repo"]),
                "task": record["task"], "branch": record["branch"], "base": base, "head": head,
                "committed": False, "files": files, "too_large": too_large, "diff": None if too_large else diff}

    def changes(self, session_id):
        """The session's change against where it started, as the person reviewing it needs it: committed or not, new
        files included and ignored ones not, renames found. {base, branch, head, diff (None when larger than max_diff),
        too_large, files: [{path, added, removed}]}, a binary file's counts None.

        Read only: the worktree is diffed against where it started through a temporary copy of its index that knows
        its new files (_worktree_index), so what the session itself staged, and its next commit, are as they were, and
        nothing is written to the repository's objects."""
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has no change to show.")
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        record = self._settled(session_id)
        _, head = git("rev-parse", "HEAD", cwd=work)
        with self._worktree_index(work) as env:
            _, diff = git("-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", "--no-textconv",
                          "-M", self._base(record), cwd=work, env=env)
            _, numstat = git("diff", "--no-ext-diff", "--no-textconv", "--numstat", "-z", "-M", self._base(record),
                             cwd=work, env=env)
        files = _numstat(numstat)
        with self._size_lock:       # what the person just saw is the size the list shows
            kept = self._sizes.get(session_id)
            self._sizes[session_id] = (time.monotonic(), dict(_size(files), uncommitted=_uncommitted(work)),
                                       kept[2] if kept else 0)
        too_large = len(diff.encode("utf-8")) > self.max_diff
        return {"session": session_id, "base": record["base"], "branch": record["branch"], "head": head.strip(),
                "diff": None if too_large else diff, "too_large": too_large, "files": files}

    # bringing a session up to date with the branch it started from

    def _start_ref(self, path, base, pull):
        """The ref a session starts from, in full, to bring it up to date with later (update): its pull request's head
        on GitHub (refs/pull/N/head); for HEAD, the repository's current branch; for a branch's name, that branch, its
        own (refs/heads/...) before origin's (refs/remotes/...), so "origin/main" names origin's; None for a tag or
        a commit, which then follows the repository's current branch."""
        if pull is not None:
            return f"refs/pull/{pull}/head"
        if base == "HEAD":
            code, ref = git("symbolic-ref", "--quiet", "HEAD", cwd=path, ok=(0, 1, 128))
            return ref.strip() if code == 0 and ref.strip().startswith("refs/heads/") else None
        for ref in (base, f"refs/heads/{base}", f"refs/remotes/{base}"):
            if ref.startswith("refs/") and git("show-ref", "--verify", "--quiet", ref, cwd=path, ok=(0, 1, 128))[0] == 0:
                return ref
        return None

    def _followed(self, record):
        """What a session is brought up to date with: (the ref in full, the name the person is shown, whether it is
        fetched from origin first, and how). The ref it started from, else the repository's current branch. A branch
        of a clone is origin's (the clone's own does not move); one of a repository added from this machine is the
        person's own, as it is in their folder; origin's branch, fetched; a pull request's head, fetched again."""
        path = self._repo_path(record["repo"])
        ref = record.get("base_ref")
        if ref and not ref.startswith("refs/"):
            ref = f"refs/heads/{ref}"           # a record from before refs were kept in full
        if not ref:
            code, current = git("symbolic-ref", "--quiet", "HEAD", cwd=path, ok=(0, 1, 128))
            ref = current.strip() if code == 0 and current.strip().startswith("refs/heads/") else None
        if not ref:
            raise WorkspaceError("This session did not start from a branch, and its repository is on none: there is "
                                 "no branch to bring it up to date with.")
        m = re.fullmatch(r"refs/pull/([0-9]+)/head", ref)
        if m:
            return f"refs/proofpane/pull/{m.group(1)}", f"#{m.group(1)}", f"+{ref}:refs/proofpane/pull/{m.group(1)}"
        has_origin = git("remote", "get-url", "origin", cwd=path, ok=(0, 1, 2, 128))[0] == 0
        if ref.startswith("refs/remotes/origin/"):
            name = ref[len("refs/remotes/origin/"):]
            return ref, f"origin/{name}", f"+refs/heads/{name}:{ref}" if has_origin else None
        name = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        if has_origin and not os.path.islink(os.path.join(self.repos_dir, record["repo"])):
            return f"refs/remotes/origin/{name}", f"origin/{name}", f"+refs/heads/{name}:refs/remotes/origin/{name}"
        return ref, name, None

    def land(self, session_id):
        """Bring a finished session's branch into the branch it follows, in a repository added from the person's
        machine, where a pull request is not the way: a fast-forward only, as git merge --ff-only makes one, so no
        merge commit is made and nothing is left to resolve. Refused while the session has anything not committed
        (Finish first), once that branch has moved on since (bring the session up to date first), while a worktree
        that has that branch checked out has changes of its own, and for a repository cloned into the workspace (whose
        branches are its remote's: Ship). Where the branch is checked out, its files move forward with it, as a
        fast-forward moves them. Recorded on the session as `landed`. {into, head, commits}"""
        record = self._settled(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has nothing to merge.")
        if not self.link_repos or not os.path.islink(os.path.join(self.repos_dir, record["repo"])):
            raise WorkspaceError("Only a repository added from this machine is merged here; one cloned into the "
                                 "workspace is shipped, as a pull request.")
        path = self._repo_path(record["repo"])
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        if record.get("merging"):
            raise WorkspaceError("A merge from its branch is under way: resolve its conflicts and commit it, or give it "
                                 "up, first.")
        if _uncommitted(work):
            raise WorkspaceError("It has changes not committed yet: Finish it first, which commits and verifies them.")
        # what is merged is what the last Finish committed and verified, and nothing its agent committed since
        finished = (record.get("finished") or {}).get("head")
        if not finished:
            raise WorkspaceError("It is not finished yet: Finish it first, which commits and verifies its change.")
        _, now = git("rev-parse", "HEAD", cwd=work)
        if now.strip() != finished:
            raise WorkspaceError("It has commits since its last Finish, which were not verified: Finish it again first.")
        ref, shown, _ = self._followed(record)
        if not ref.startswith("refs/heads/") or git("symbolic-ref", "--quiet", ref, cwd=path, ok=(0, 1, 128))[0] != 1:
            raise WorkspaceError(f"It follows {shown}, not a branch of this repository's own: merge it yourself.")
        code, tip = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=path, ok=(0, 1, 128))
        if code != 0:
            raise WorkspaceError(f"There is no {shown} here to merge it into.")
        tip = tip.strip()
        head = finished
        if git("merge-base", "--is-ancestor", head, tip, cwd=path, ok=(0, 1, 128))[0] == 0:
            return {"into": shown, "head": tip, "commits": 0}           # in it already
        if git("merge-base", "--is-ancestor", tip, head, cwd=path, ok=(0, 1, 128))[0] != 0:
            raise WorkspaceError(f"{shown} has moved on since this session started: bring the session up to date first "
                                 f"(Update), then merge it.")
        _, n = git("rev-list", "--count", f"{tip}..{head}", cwd=path)
        _, listed = git("worktree", "list", "--porcelain", cwd=path)
        where = next((line[len("worktree "):] for block in listed.split("\n\n") if f"branch {ref}" in block.splitlines()
                      for line in block.splitlines() if line.startswith("worktree ")), None)
        if where is None:
            # checked out nowhere: the branch moves, from the tip read, and only from it
            git("update-ref", "--no-deref", "-m", f"Proofpane: {record['branch']} merged", ref, head, tip, cwd=path)
        else:
            if _uncommitted(where):
                raise WorkspaceError(f"{where} has {shown} checked out, with changes of its own: commit them or put them "
                                     f"aside first, so a merge moves only what this session did.")
            p = subprocess.run(["git", "merge", "--ff-only", "--quiet", head], cwd=where, env=_git_env(where, own=True),
                               capture_output=True, timeout=GIT_TIMEOUT, input=b"")
            if p.returncode != 0:
                raise WorkspaceError(f"git could not move {shown} forward: "
                                     + _last_lines((p.stderr or p.stdout).decode("utf-8", "replace")))
        self._update(session_id, lambda r: r.update(landed={"into": shown, "head": head, "at": int(time.time())}))
        return {"into": shown, "head": head, "commits": int(n.strip() or 0)}

    def update_fetch(self, session_id):
        """Fetch what a session is brought up to date with, when it is origin's: outside the agent's lock, since a
        fetch can take minutes and only moves a ref of origin's, and one repository at a time. A note when origin
        could not be reached (the last fetch stands, and is said to); refused when origin has no such branch now."""
        record = self._read(session_id)
        if not record.get("repo"):
            raise WorkspaceError("A session without a repository has nothing to bring up to date.")
        ref, shown, refspec = self._followed(record)
        if not refspec:
            return None
        path = self._repo_path(record["repo"])
        with self._fetch_locks.setdefault(record["repo"], threading.Lock()):
            p = subprocess.run(["git", "fetch", "--quiet", "--no-tags", "origin", refspec], cwd=path,
                               env=_git_env(path, network=True), capture_output=True, timeout=SHIP_TIMEOUT, input=b"")
        if p.returncode == 0:
            return None
        said = p.stderr.decode("utf-8", "replace")
        if "couldn't find remote ref" in said or "could not find remote ref" in said:
            raise WorkspaceError(f"origin has no {shown} now: there is nothing there to bring the session up to date "
                                 "with.")
        if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=path, ok=(0, 1, 128))[0] != 0:
            raise WorkspaceError(f"{shown} could not be fetched from origin: {_last_lines(said)}")
        return f"origin could not be reached, so {shown} is as it was last fetched"

    def _own_work_under_way(self, work, branch, branch_check=True):
        """Where the session's worktree is, in words, when it is in something an update did not start: the agent's own
        merge, rebase, cherry-pick or revert, or files left unmerged; and, with `branch_check`, a branch other than its
        own. None when it is on its own branch with nothing under way."""
        for name, what in (("MERGE_HEAD", "a merge"), ("CHERRY_PICK_HEAD", "a cherry-pick"),
                           ("REVERT_HEAD", "a revert")):
            if git("rev-parse", "--verify", "--quiet", name, cwd=work, ok=(0, 1, 128))[0] == 0:
                return f"in the middle of {what}"
        for name in ("rebase-merge", "rebase-apply"):
            _, where = git("rev-parse", "--git-path", name, cwd=work)
            where = where.strip()
            if os.path.exists(where if os.path.isabs(where) else os.path.join(work, where)):
                return "in the middle of a rebase"
        _, unmerged = git("ls-files", "-u", cwd=work)
        if unmerged.strip():
            return "in the middle of files left unmerged"
        code, head = git("symbolic-ref", "--quiet", "HEAD", cwd=work, ok=(0, 1, 128))
        if branch_check and (code != 0 or head.strip() != f"refs/heads/{branch}"):
            return f"on a branch other than its own, {branch}"
        return None

    def _merging(self, session_id, record, work):
        """A merge an update started that is not done: {id, ref, target, at, conflicts}, or None. One done since (by
        its agent, or the person, with a commit) makes the commit it merged the session's base, so its change is
        again only what it did; one given up (git merge --abort) leaves the base as it was."""
        m = record.get("merging")
        if not m:
            return None
        if git("rev-parse", "--verify", "--quiet", "MERGE_HEAD", cwd=work, ok=(0, 1, 128))[0] == 0:
            _, names = git("diff", "--name-only", "--diff-filter=U", "-z", cwd=work)
            return dict(m, conflicts=[n for n in names.split("\0") if n])
        done = git("merge-base", "--is-ancestor", m["target"], "HEAD", cwd=work, ok=(0, 1, 128))[0] == 0

        def settle(r):
            if (r.get("merging") or {}).get("id") == m.get("id"):
                r.pop("merging")
                if done:
                    r["base"] = m["target"]
        self._update(session_id, settle)
        return None

    def _settled(self, session_id):
        """The session's record, a merge its update started and its agent finished (or gave up) since settled first:
        what reads its base (its changes, a finish, a ship, a discard) reads the base that merge left; a session
        teleport started has taken its branch first, when it can (_adopt, waited for)."""
        record = self._read(session_id)
        if _teleport_pending(record):
            self._adopt(session_id, wait=True)
            record = self._read(session_id)
        work = self._work(session_id)
        if record.get("merging") and os.path.isdir(work):
            self._merging(session_id, record, work)
            record = self._read(session_id)
        return record

    def update_status(self, session_id):
        """How far behind what it follows a session is, as last known here (no fetch): {ref, behind, merging}; ref
        None, with why, when there is nothing it can be brought up to date with."""
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has nothing to bring up to date.")
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        merging = self._merging(session_id, record, work)
        try:
            ref, shown, _ = self._followed(self._read(session_id))
        except (WorkspaceError, NotFound) as e:
            return {"ref": None, "behind": 0, "merging": merging, "why": str(e)}
        if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=work, ok=(0, 1, 128))[0] != 0:
            return {"ref": shown, "behind": 0, "merging": merging}       # not fetched yet: an update fetches it
        _, n = git("rev-list", "--count", f"HEAD..{ref}", cwd=work)
        return {"ref": shown, "behind": int(n.strip() or 0), "merging": merging}

    def update(self, session_id, author_name, author_email, note=None):
        """Bring a session up to date with what it follows (_followed; fetched before, by update_fetch, whose `note`
        this passes on): what it has not committed is committed first, then that is merged into its branch.

        Only when that contains the commit the session started from: otherwise its other changes (a pull request's,
        a branch's rewritten history) would be counted as the session's. Not while its worktree is in the middle of
        something of its agent's own, which the update would commit half done. Merged cleanly, the commit merged
        becomes the session's base, so its change shows only what it did. Stopped by conflicts, the merge is left as
        git leaves one, for its agent or the person to resolve and commit, or for update_abort to give up; a merge
        that stopped for another reason (a hook that refused its commit) is given up at once, and git's words said."""
        record = self._settled(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has nothing to bring up to date.")
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        if record.get("merging"):
            raise WorkspaceError("A merge from its branch is under way: resolve its conflicts and commit it, or give it "
                                 "up, first.")
        busy = self._own_work_under_way(work, record["branch"])
        if busy:
            raise WorkspaceError(f"Its worktree is {busy}: let its agent finish or undo that first.")
        ref, shown, _ = self._followed(record)
        code, target = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=work, ok=(0, 1, 128))
        if code != 0:
            raise WorkspaceError(f"There is no {shown} here to bring the session up to date with.")
        target = target.strip()
        base = self._base(record)
        if git("merge-base", "--is-ancestor", base, target, cwd=work, ok=(0, 1, 128))[0] != 0:
            raise WorkspaceError(f"{shown} does not have the commit this session started from ({base[:7]}), so bringing "
                                 f"it up to date would count {shown}'s other changes as the session's.")
        _, n = git("rev-list", "--count", f"HEAD..{target}", cwd=work)
        behind = int(n.strip() or 0)
        if behind == 0:
            if target != base:
                # its agent merged it already: the base catches up, so its change is again only its own
                self._update(session_id, lambda r: r.update(base=target))
            return {"updated": False, "ref": shown, "behind": 0, "note": note, "merging": None}
        ident = []
        if git("config", "--get", "user.email", cwd=work, ok=(0, 1))[0] != 0:
            ident = ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}"]
        self.controller.finishing(session_id)       # a task's end judged on the head before this merge (controller.py)
        git("add", "--all", cwd=work)
        if git("diff", "--cached", "--quiet", cwd=work, ok=(0, 1))[0] == 1:
            git(*ident, "commit", "--quiet", "--file", "-", cwd=work, own=True,
                input=f"Work in progress, before bringing it up to date with {shown}\n".encode("utf-8"))
        merging = {"id": secrets.token_hex(4), "ref": shown, "target": target, "at": int(time.time())}
        p = subprocess.run(["git", *ident, "merge", "--quiet", "--no-edit", "--no-ff", "-m",
                            f"Merge {shown} into {record['branch']}", target], cwd=work, env=_git_env(work, own=True),
                           capture_output=True, timeout=GIT_TIMEOUT, input=b"")
        if p.returncode == 0:
            self._update(session_id, lambda r: r.update(base=target))
            return {"updated": True, "ref": shown, "behind": 0, "merged": behind, "base": target, "note": note,
                    "merging": None}
        said = _last_lines((p.stderr or p.stdout).decode("utf-8", "replace"))
        in_merge = git("rev-parse", "--verify", "--quiet", "MERGE_HEAD", cwd=work, ok=(0, 1, 128))[0] == 0
        _, unmerged = git("ls-files", "-u", cwd=work)
        if in_merge and unmerged.strip():
            self._update(session_id, lambda r: r.update(merging=merging))
            return {"updated": False, "ref": shown, "behind": behind, "note": note,
                    "merging": self._merging(session_id, self._read(session_id), work)}
        if in_merge:
            git("merge", "--abort", cwd=work, ok=(0, 128))
        raise WorkspaceError(f"The merge of {shown} did not go through: {said}")

    def update_abort(self, session_id):
        """Give up a merge an update started: the session is as it was before it (its work committed, if it was not).
        One its agent finished meanwhile is not undone: it is settled, and said so."""
        record = self._read(session_id)
        work = self._work(session_id)
        if not record.get("merging") or not os.path.isdir(work):
            raise WorkspaceError("No merge from its branch is under way.")
        if git("rev-parse", "--verify", "--quiet", "MERGE_HEAD", cwd=work, ok=(0, 1, 128))[0] != 0:
            self._merging(session_id, record, work)
            return {"aborted": False, "finished": "merging" not in self._read(session_id)}
        git("merge", "--abort", cwd=work)
        self._update(session_id, lambda r: r.pop("merging", None))
        return {"aborted": True}

    def ports(self, session_id):
        """The ports the session's app is served on, for its preview (ports.py): what listens on this machine from a
        process working in its worktree."""
        self._read(session_id)
        return {"session": session_id, "ports": ports.listening(self._work(session_id))}

    def gate_log(self, session_id, limit=200):
        """What the gate refused in this session (gate.py's gate.jsonl): [{at, tool, reason}], newest last, at most
        `limit`. The session's own record of its refusals, shown as that; with what its agent asked for (ask_log)."""
        self._read(session_id)
        out = []
        try:
            with open(os.path.join(self._dir(session_id), "gate.jsonl"), encoding="utf-8") as fh:
                lines = fh.readlines()[-limit:]
        except OSError:
            return {"session": session_id, "refusals": [], "asks": self.ask_log(session_id)}
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and isinstance(e.get("at"), int):
                out.append({"at": e["at"], "tool": e.get("tool") if isinstance(e.get("tool"), str) else None,
                            "reason": str(e.get("reason") or "")[:400]})
        return {"session": session_id, "refusals": out, "asks": self.ask_log(session_id)}

    # shipping: the session's branch pushed, and a pull request for it

    def _gh(self, args, cwd, input="", timeout=GITHUB_TIMEOUT):
        """gh, with the workspace's own sign-in and never a prompt: (exit code, output, error output) as text."""
        if shutil.which("gh", path=program_env().get("PATH")) is None:
            raise WorkspaceError("gh is not installed in this workspace.")
        try:
            p = subprocess.run(["gh", *args], cwd=cwd, env=_gh_env(cwd), capture_output=True, timeout=timeout,
                               input=input.encode("utf-8"))
        except subprocess.TimeoutExpired:
            raise WorkspaceError(f"gh took longer than {timeout} seconds.") from None
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    def ship(self, session_id, title=None, body=None, base=None, draft=False):
        """Push the session's branch to its repository's origin and open a pull request for it with gh: {branch,
        commits, url, existing}. What goes is what is committed on the branch (Finish commits the worktree); a branch
        with nothing beyond where it started is refused. A pull request that already exists for the branch is given
        back, not opened twice. The push uses git's credentials, which GitHub's sign-in in the workspace set up
        (gh auth setup-git); the page passes nothing secret. Recorded on the session as `shipped`."""
        record = self._settled(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has nothing to ship.")
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        if title is not None and (not isinstance(title, str) or len(title) > MAX_PR_TITLE):
            raise WorkspaceError(f"A pull request's title is text, at most {MAX_PR_TITLE} characters.")
        if body is not None and (not isinstance(body, str) or len(body) > MAX_PR_BODY):
            raise WorkspaceError("A pull request's description is text, at most 64 KB.")
        if base not in (None, "") and (not isinstance(base, str) or not _REF.match(base) or ".." in base
                                       or base.endswith((".lock", "/"))):
            raise WorkspaceError("Name the branch the pull request goes into.")
        held = self._teleport_held(session_id, record)
        if held:
            raise WorkspaceError(held)
        _, count = git("rev-list", "--count", f"{self._base(record)}..HEAD", cwd=work)
        commits = int(count.strip() or 0)
        if commits == 0:
            raise WorkspaceError("Nothing to ship yet: Finish the session first, which commits its worktree.")
        code, origin = git("remote", "get-url", "origin", cwd=work, ok=(0, 2, 128))
        if code != 0 or not origin.strip():
            raise WorkspaceError("The repository has no origin to push to.")
        branch = record["branch"]
        git("push", "--quiet", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}", cwd=work, timeout=SHIP_TIMEOUT,
            own=True)
        first, _, rest = record["task"].partition("\n")
        title = (title or first).strip()[:MAX_PR_TITLE] or f"Proofpane session {session_id}"
        text = (body if isinstance(body, str) else rest).strip()
        text = (text + "\n\n" if text else "") + f"Proofpane-Session: {session_id}\n"
        args = ["pr", "create", "--head", branch, "--title", title, "--body-file", "-"]
        if base:
            args += ["--base", base]
        if draft:
            args.append("--draft")
        code, out, err = self._gh(args, cwd=work, input=text, timeout=SHIP_TIMEOUT)
        found = PR_URL.search(out + "\n" + err)
        existing = code != 0 and found is not None and "already exists" in err
        if code != 0 and not existing:
            # what was done, and what was not: the branch is pushed, so a second try only opens the pull request
            said = [line for line in (err or out).strip().splitlines() if line.strip()]
            raise WorkspaceError(f"Pushed {branch} to origin, but the pull request was not opened: "
                                 + (said[-1] if said else f"gh ended with status {code}"))
        if found is None:
            raise WorkspaceError("gh opened no pull request it named.")
        self._update(session_id, lambda r: r.update(shipped={"url": found.group(0), "at": int(time.time()),
                                                            "commits": commits}))
        return {"session": session_id, "branch": branch, "commits": commits, "url": found.group(0), "existing": existing}

    # GitHub, with the workspace's own sign-in (gh): a repository's open issues, to start a session from one; and a
    # shipped session's pull request as GitHub has it now, and merging it

    @staticmethod
    def _gh_failed(what, code, out, err):
        said = [line for line in (err or out).strip().splitlines() if line.strip()]
        return WorkspaceError(f"gh could not {what}: " + (said[-1] if said else f"status {code}"))

    @staticmethod
    def _json(out):
        try:
            return json.loads(out or "null")
        except ValueError:
            raise WorkspaceError("gh answered with something other than JSON.") from None

    def issues(self, name, search=None, limit=50):
        """A repository's open issues on GitHub as gh lists them, newest first: [{number, title, body, url, labels,
        author, updated_at}], each body cut at ISSUE_TEXT. `search` is GitHub's own search, within the repository."""
        path = self._repo_path(name)
        if self.label(name).startswith("workspace/"):
            raise WorkspaceError("This repository's origin is not on GitHub: it has no issues to list here.")
        try:
            limit = max(1, min(int(limit), MAX_ISSUES))
        except (TypeError, ValueError):
            raise WorkspaceError("A limit is a number.") from None
        args = ["issue", "list", "--repo", self.label(name), "--state", "open", "--limit", str(limit),
                "--json", "number,title,body,url,labels,author,updatedAt"]
        if search:
            if not isinstance(search, str) or len(search) > 200 or "\0" in search:
                raise WorkspaceError("A search is at most 200 characters.")
            args += ["--search", search]
        code, out, err = self._gh(args, cwd=path)
        if code != 0:
            raise self._gh_failed("list the issues", code, out, err)
        raw = self._json(out)
        found = []
        for i in raw if isinstance(raw, list) else []:
            if not isinstance(i, dict) or not isinstance(i.get("number"), int) or isinstance(i.get("number"), bool) \
                    or not isinstance(i.get("title"), str):
                continue
            body = i["body"] if isinstance(i.get("body"), str) else ""
            author = i["author"] if isinstance(i.get("author"), dict) else {}
            found.append({
                "number": i["number"], "title": i["title"][:300], "body": body[:ISSUE_TEXT], "cut": len(body) > ISSUE_TEXT,
                "url": i["url"] if isinstance(i.get("url"), str) and _ISSUE_URL.match(i["url"]) else None,
                "labels": [x["name"][:50] for x in (i.get("labels") or []) if isinstance(x, dict)
                           and isinstance(x.get("name"), str)][:10],
                "author": author["login"][:100] if isinstance(author.get("login"), str) else None,
                "updated_at": i["updatedAt"] if isinstance(i.get("updatedAt"), str) else None})
        return {"repo": name, "issues": found}

    def pulls(self, name, search=None, limit=50):
        """A repository's open pull requests on GitHub as gh lists them, newest first, to start a session from one:
        [{number, title, body, cut, url, branch, base, author, draft, updated_at}], each body cut at ISSUE_TEXT.
        `search` is GitHub's own search, within the repository."""
        path = self._repo_path(name)
        if self.label(name).startswith("workspace/"):
            raise WorkspaceError("This repository's origin is not on GitHub: it has no pull requests to list here.")
        try:
            limit = max(1, min(int(limit), MAX_ISSUES))
        except (TypeError, ValueError):
            raise WorkspaceError("A limit is a number.") from None
        # origin's, the repository a pull request is then fetched and reviewed from: gh alone would prefer an
        # `upstream` remote, which a fork added from this machine often has
        args = ["pr", "list", "--repo", self.label(name), "--state", "open", "--limit", str(limit),
                "--json", "number,title,body,url,headRefName,baseRefName,author,isDraft,updatedAt"]
        if search:
            if not isinstance(search, str) or len(search) > 200 or "\0" in search:
                raise WorkspaceError("A search is at most 200 characters.")
            args += ["--search", search]
        code, out, err = self._gh(args, cwd=path)
        if code != 0:
            raise self._gh_failed("list the pull requests", code, out, err)
        raw = self._json(out)
        found = []
        for p in raw if isinstance(raw, list) else []:
            if not isinstance(p, dict) or not isinstance(p.get("number"), int) or isinstance(p.get("number"), bool) \
                    or not 1 <= p["number"] <= MAX_PULL or not isinstance(p.get("title"), str):
                continue
            body = p["body"] if isinstance(p.get("body"), str) else ""
            author = p["author"] if isinstance(p.get("author"), dict) else {}
            text = lambda key, most: p[key][:most] if isinstance(p.get(key), str) else None
            found.append({
                "number": p["number"], "title": p["title"][:300], "body": body[:ISSUE_TEXT], "cut": len(body) > ISSUE_TEXT,
                "url": p["url"] if isinstance(p.get("url"), str) and PR_URL.fullmatch(p["url"]) else None,
                "branch": text("headRefName", 250), "base": text("baseRefName", 250),
                "author": author["login"][:100] if isinstance(author.get("login"), str) else None,
                "draft": p.get("isDraft") is True,
                "updated_at": p["updatedAt"] if isinstance(p.get("updatedAt"), str) else None})
        return {"repo": name, "pulls": found}

    def _fetch_pull(self, path, name, number):
        """A pull request's head, fetched from the repository's origin on GitHub (refs/pull/N/head, which GitHub keeps
        for every pull request, one from a fork too) into refs/proofpane/pull/N, as the base a session starts from.
        With the workspace's own sign-in, as a push is."""
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= MAX_PULL:
            raise WorkspaceError("A pull request is a number.")
        if self.label(name).startswith("workspace/"):
            raise WorkspaceError("This repository's origin is not on GitHub: it has no pull requests to start from.")
        ref = f"refs/proofpane/pull/{number}"
        try:
            git("fetch", "--quiet", "--no-tags", "origin", f"+refs/pull/{number}/head:{ref}", cwd=path,
                timeout=SHIP_TIMEOUT)
        except WorkspaceError as e:
            raise WorkspaceError(f"Pull request #{number} could not be fetched from GitHub: {e}") from None
        return ref

    def _shipped(self, session_id):
        record = self._read(session_id)
        url = (record.get("shipped") or {}).get("url")
        if not isinstance(url, str) or not PR_URL.fullmatch(url):
            raise WorkspaceError("This session has not been shipped: it has no pull request.")
        work = self._work(session_id)
        return url, work if os.path.isdir(work) else self._repo_path(record["repo"])

    def _feedback_pull(self, session_id):
        """The pull request whose feedback a session's agent gets, and where gh runs for it: the one the session
        shipped as; else the one it started from (`pull`), in its repository's origin on GitHub."""
        record = self._read(session_id)
        number = record.get("pull")
        if (record.get("shipped") or {}).get("url") or not isinstance(number, int) or isinstance(number, bool) \
                or not 1 <= number <= MAX_PULL or not record.get("repo"):
            return self._shipped(session_id)
        label = self.label(record["repo"])
        if label.startswith("workspace/"):
            raise WorkspaceError("This repository's origin is not on GitHub: its pull request has no feedback here.")
        work = self._work(session_id)
        return f"https://github.com/{label}/pull/{number}", work if os.path.isdir(work) else self._repo_path(record["repo"])

    def _the_pull(self, session_id, url, shipped=False):
        """The session's pull request and where gh runs for it, when `url` (the one the app recorded, as it shipped
        the session or started it from one) is the one its record names: a session's programs can change its record,
        and the app's word is the one that holds. `url` None: the record's alone (the agent's own tests)."""
        mine, cwd = self._shipped(session_id) if shipped else self._feedback_pull(session_id)
        if url is not None and url != mine:
            raise WorkspaceError("This session's record names another pull request than the app knows for it: it is "
                                 "not shown, merged or reviewed.")
        return mine, cwd

    def pull_request(self, session_id, url=None):
        """A session's pull request as GitHub has it now (the one it shipped as, else the one it started from): its
        state, its review, its checks (passed, failed and still running) and whether it can be merged."""
        url, cwd = self._the_pull(session_id, url)
        code, out, err = self._gh(["pr", "view", url, "--json", PR_FIELDS], cwd=cwd)
        if code != 0:
            raise self._gh_failed("read the pull request", code, out, err)
        pr = self._json(out)
        if not isinstance(pr, dict):
            raise WorkspaceError("gh answered with something other than a pull request.")
        return dict(pr_view(pr), url=url, session=session_id)

    def pr_review(self, session_id, event, body, url):
        """The person's own review of the pull request a session started from (or shipped as), on GitHub, with the
        workspace's gh sign-in: approve it, ask for changes, or comment, in their words (asking for changes and a
        comment need some). `url` is the pull request the page showed them: the review goes to that one only, and is
        refused if the session's is another now, so it lands where the person decided. Recorded as `reviewed`."""
        flag = REVIEW_FLAGS.get(event)
        if flag is None:
            raise WorkspaceError("A review approves, asks for changes, or comments.")
        if not isinstance(body, str) or len(body) > MAX_REVIEW or "\0" in body:
            raise WorkspaceError(f"A review's words are text, at most {MAX_REVIEW} characters.")
        body = body.strip()
        if event != "approve" and not body:
            raise WorkspaceError("Say what to change, or what the comment is.")
        mine, cwd = self._feedback_pull(session_id)
        if url != mine:
            raise WorkspaceError("This session's pull request is not the one shown: read it again, then review.")
        code, out, err = self._gh(["pr", "review", mine, flag, *(["--body-file", "-"] if body else [])], cwd=cwd,
                                  input=body, timeout=SHIP_TIMEOUT)
        if code != 0:
            raise self._gh_failed("review the pull request", code, out, err)
        self._update(session_id, lambda r: r.update(reviewed={"event": event, "url": mine, "at": int(time.time())}))
        return {"reviewed": event, "url": mine}

    def merge(self, session_id, method="squash", auto=False, url=None):
        """Merge a shipped session's pull request with gh by `method` (squash, merge or rebase); with `auto`, once
        what the repository requires has passed (it must allow auto-merge). The branches stay. Recorded on the session
        as `shipped.merge`; gives back the pull request as GitHub then has it."""
        if method not in MERGE_METHODS:
            raise WorkspaceError("A merge is a squash, a merge or a rebase.")
        url, cwd = self._the_pull(session_id, url, shipped=True)
        code, out, err = self._gh(["pr", "merge", url, f"--{method}", *(["--auto"] if auto else [])], cwd=cwd,
                                  timeout=SHIP_TIMEOUT)
        if code != 0:
            raise self._gh_failed("merge the pull request", code, out, err)
        self._update(session_id, lambda r: r.update(shipped=dict(r.get("shipped") or {}, merge={
            "method": method, "auto": bool(auto), "at": int(time.time())})))
        return self.pull_request(session_id, url)

    def pr_feedback(self, session_id, url=None):
        """What reviewers and checks said of a shipped session's pull request, for its agent to act on: the review
        threads not yet resolved, each with the code it is on; reviews and comments with words in them; and each check
        that failed, with the part of its job's log that says why when it ran in GitHub Actions (LogExcerpt).

        Anyone can comment on a public repository's pull request, and a fork's pull request runs the fork's own
        workflows, so each item says whether it can be taken as from someone who can write to the repository
        (`trusted`): a comment by the repository's owner, or by a member or collaborator whose permission GitHub gives
        as admin or write (which GitHub tells only someone who can push); never a bot's, which may quote the pull
        request itself; and a check only when the pull request is not a fork's. The page ticks only those for the
        agent, and the person chooses. The pull request is the one the session shipped as, or, before it is shipped,
        the one it started from; `url` the one the app recorded (_the_pull). {session, url, number, fork, items,
        resolved}, `resolved` the threads left out."""
        url, cwd = self._the_pull(session_id, url)
        parts = _PR_PARTS.match(url)
        if parts is None:
            raise WorkspaceError("This session's pull request is not on GitHub.")
        host, owner, name, number = parts.group(1), parts.group(2), parts.group(3), int(parts.group(4))
        on_host = [] if host == "github.com" else ["--hostname", host]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            # -f, not -F, for the names: -F would send a name of digits (2048) as a number
            asked = pool.submit(self._gh, ["api", "graphql", *on_host, "-f", "query=" + FEEDBACK_QUERY,
                                           "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}"], cwd)
            viewed = pool.submit(self._gh, ["pr", "view", url, "--json", "statusCheckRollup"], cwd)
            code, out, err = asked.result()
            if code != 0:
                raise self._gh_failed("read the pull request's reviews", code, out, err)
            reviews = self._json(out)
            code, out, err = viewed.result()
            if code != 0:
                raise self._gh_failed("read the pull request's checks", code, out, err)
            rollup = self._json(out)
        pr = ((reviews if isinstance(reviews, dict) else {}).get("data") or {})
        pr = ((pr.get("repository") if isinstance(pr, dict) else None) or {}).get("pullRequest")
        if not isinstance(pr, dict):
            raise WorkspaceError("GitHub did not give this pull request's reviews.")
        fork = pr.get("isCrossRepository") is not False

        def nodes(of, key):
            got = (of.get(key) if isinstance(of, dict) else None) or {}
            got = got.get("nodes") if isinstance(got, dict) else None
            return [n for n in got if isinstance(n, dict)] if isinstance(got, list) else []

        def link(value):
            return value if isinstance(value, str) and value.startswith(f"https://{host}/") and len(value) < 2000 \
                else None

        def words(node):
            body = node.get("body") if isinstance(node.get("body"), str) else ""
            return body.strip()[:FEEDBACK_TEXT]

        def when(node, key):
            return node[key] if isinstance(node.get(key), str) and len(node[key]) < 40 else None

        def who(node):
            author = node.get("author") if isinstance(node.get("author"), dict) else {}
            login = author.get("login") if isinstance(author.get("login"), str) and author["login"] else "ghost"
            said = node.get("authorAssociation")
            return {"author": login[:100], "bot": author.get("__typename") == "Bot",
                    "association": said if isinstance(said, str) and len(said) < 40 else None}

        items, resolved = [], 0
        for t in nodes(pr, "reviewThreads"):
            if t.get("isResolved") is True:
                resolved += 1
                continue
            said = [dict(who(c), body=words(c)) for c in nodes(t, "comments") if words(c)]
            if not said:
                continue
            first = nodes(t, "comments")[0]
            line = next((t[k] for k in ("line", "originalLine") if isinstance(t.get(k), int)
                         and not isinstance(t[k], bool) and t[k] > 0), None)
            hunk = first.get("diffHunk") if isinstance(first.get("diffHunk"), str) else ""
            items.append({"kind": "thread", "path": t["path"][:500] if isinstance(t.get("path"), str) else None,
                          "line": line, "outdated": t.get("isOutdated") is True, "url": link(first.get("url")),
                          "hunk": "\n".join(hunk.splitlines()[-FEEDBACK_HUNK:])[:FEEDBACK_TEXT],
                          "comments": said, "at": when(first, "createdAt")})
        items.sort(key=lambda i: (i["path"] or "", i["line"] or 0))
        for r in nodes(pr, "reviews"):
            state = r.get("state") if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED") else None
            if state and words(r):
                items.append(dict(who(r), kind="review", state=state, body=words(r), url=link(r.get("url")),
                                  at=when(r, "submittedAt")))
        for c in nodes(pr, "comments"):
            if words(c):
                items.append(dict(who(c), kind="comment", body=words(c), url=link(c.get("url")),
                                  at=when(c, "createdAt")))

        # whose permission to ask: members and collaborators (owners are trusted, and the rest are not), a few at most
        authors = [a for i in items for a in (i["comments"] if i["kind"] == "thread" else [i])]
        ask = []
        for a in authors:
            if not a["bot"] and a["association"] in _ASK_PERMISSION and _LOGIN.match(a["author"]) \
                    and a["author"] not in ask and len(ask) < FEEDBACK_AUTHORS:
                ask.append(a["author"])

        checks = [x for x in map(_check, (rollup or {}).get("statusCheckRollup") or [] if isinstance(rollup, dict)
                                 else []) if x and x["state"] == "failed"]
        jobs, listed = [], 0
        for c in checks:
            job = _JOB_URL.match(c["url"] or "")
            if job is None:
                jobs.append((None, None))
            elif job.group(1) != host or (job.group(2).lower(), job.group(3).lower()) != (owner.lower(), name.lower()):
                jobs.append((None, "Its job is not in the pull request's own repository, so its log is not read."))
            elif listed >= FEEDBACK_JOBS:
                jobs.append((None, f"Only the first {FEEDBACK_JOBS} failed jobs' logs are read."))
            else:
                listed += 1
                jobs.append((job, None))

        def permission(login):
            code, out, _ = self._gh(["api", *on_host, f"repos/{owner}/{name}/collaborators/{login}/permission"], cwd)
            got = self._json(out) if code == 0 else None
            return isinstance(got, dict) and got.get("permission") in ("admin", "write")

        def read_log(job):
            return self._gh_log(["api", *on_host, f"repos/{owner}/{name}/actions/jobs/{job.group(4)}/logs"], cwd)

        with concurrent.futures.ThreadPoolExecutor(max_workers=FEEDBACK_JOBS + 2) as pool:
            asked = {login: pool.submit(permission, login) for login in ask}
            logs = [pool.submit(read_log, job) if job else None for job, _ in jobs]
            writers = set()
            for login, future in asked.items():
                try:
                    if future.result():
                        writers.add(login)
                except WorkspaceError:
                    pass

            def trusted(a):
                return not a["bot"] and (a["association"] == _OWNER or a["author"] in writers)

            for i in items:
                if i["kind"] == "thread":
                    for c in i["comments"]:
                        c["trusted"] = trusted(c)
                    i["trusted"] = all(c["trusted"] for c in i["comments"])
                else:
                    i["trusted"] = trusted(i)
            for c, (job, why), future in zip(checks, jobs, logs):
                log = None
                if future is not None:
                    try:
                        log, why = future.result()
                    except WorkspaceError as e:
                        why = str(e)[:300]
                items.append({"kind": "check", "name": c["name"], "url": c["url"], "log": log or None,
                              "log_error": why, "trusted": not fork})
        return {"session": session_id, "url": url, "number": number, "fork": fork, "items": items,
                "resolved": resolved}

    def _gh_log(self, args, cwd):
        """A job's log, through gh, read a line at a time and never held whole (LogExcerpt), for at most
        FEEDBACK_LOG_SECONDS and FEEDBACK_LOG_BYTES, each line's first 8 KB kept: (its excerpt, None), or (None, why
        there is none)."""
        if shutil.which("gh", path=program_env().get("PATH")) is None:
            raise WorkspaceError("gh is not installed in this workspace.")
        try:
            env = _gh_env(cwd)
        except GitHeld as e:
            return None, str(e)[:300]
        excerpt, read, held, stopped = LogExcerpt(), 0, b"", None
        late = threading.Event()
        with tempfile.TemporaryFile() as err:
            try:
                p = subprocess.Popen(["gh", *args], cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=err, env=env)
            except OSError as e:
                return None, f"gh could not be started: {e}"[:300]

            def too_late():
                late.set()
                p.kill()

            timer = threading.Timer(FEEDBACK_LOG_SECONDS, too_late)
            timer.start()
            try:
                while True:
                    chunk = p.stdout.readline(65536)
                    if not chunk:
                        break
                    read += len(chunk)
                    held += chunk[:max(0, 8192 - len(held))]
                    if chunk.endswith(b"\n"):
                        excerpt.feed(held.decode("utf-8", "replace"))
                        held = b""
                    if read > FEEDBACK_LOG_BYTES:
                        stopped = "The log is longer than the workspace reads; this is from its first part."
                        break
                if held:
                    excerpt.feed(held.decode("utf-8", "replace"))
            finally:
                timer.cancel()
                if p.poll() is None:
                    p.kill()
                p.stdout.close()
                p.wait()
            if stopped is None and late.is_set():
                if not read:
                    return None, "GitHub did not send this job's log in time."
                stopped = "GitHub sent the log too slowly to read it all; this is what came in time."
            elif stopped is None and p.returncode != 0:
                err.seek(0)
                said = [line for line in err.read(65536).decode("utf-8", "replace").splitlines() if line.strip()]
                said = said[-1] if said else f"gh stopped with status {p.returncode}."
                if "HTTP 410" in said:
                    said = "GitHub no longer keeps this job's log."
                elif "HTTP 404" in said:
                    said = "GitHub has no log for this job."
                return None, _CONTROL.sub("", _ANSI.sub("", said))[:300]
        text = excerpt.text()
        if not text:
            return None, "Its log is empty." if not stopped else stopped
        return (f"{text}\n({stopped})" if stopped else text), None

    def discard(self, session_id, rel):
        """Put one file back as it was where the session started (its base): a file the session changed or deleted is
        restored, one it added is removed. In the worktree only: what the session committed stays in its history, and
        its next Finish commits the file as it now is. {path, restored} or {path, removed}."""
        work, parts, path = self._work_path(session_id, rel)
        if not parts:
            raise WorkspaceError("Name a file in the session's worktree.")
        rel = "/".join(parts)
        base = self._base(self._settled(session_id))
        code, _ = git("cat-file", "-e", f"{base}:{rel}", cwd=work, ok=(0, 1, 128))
        if code == 0:
            # git restores it, and will not write through a folder that is a link (it says "beyond a symbolic link")
            git("checkout", base, "--", rel, cwd=work)
            return {"path": rel, "restored": True}
        if not os.path.lexists(path):
            raise NotFound("No such file in the session's worktree.")
        if not self._within(work, os.path.dirname(path)):
            raise WorkspaceError("That path leads outside the session's worktree.")
        if os.path.isdir(path) and not os.path.islink(path):
            raise WorkspaceError("Name a file, not a folder.")
        os.unlink(path)
        git("rm", "--cached", "--quiet", "--ignore-unmatch", "--", rel, cwd=work)
        return {"path": rel, "removed": True}

    # the worktree's files, for the page to browse (read only)

    def _work_path(self, session_id, rel):
        """A path the page names in the session's worktree: (worktree, its parts, the path). Relative, no "..", never
        into .git; whether it stays inside the worktree once links are resolved is checked by each caller."""
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has no files to show.")
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        if not isinstance(rel, str) or len(rel) > 4096 or "\0" in rel:
            raise WorkspaceError("Name a path in the session's worktree.")
        parts = [x for x in rel.split("/") if x not in ("", ".")]
        if ".." in parts or (parts and parts[0] == ".git"):
            raise WorkspaceError("That path is not one the page may show.")
        return work, parts, os.path.join(work, *parts)

    @staticmethod
    def _within(work, path):
        real, top = os.path.realpath(path), os.path.realpath(work)
        return real == top or real.startswith(top + os.sep)

    def paths(self, session_id):
        """Every file git sees in the session's worktree, tracked or new and not ignored, for the page to find one by
        name: {session, paths, truncated}, sorted. Read only; a file deleted from the worktree is not listed."""
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has no files to search.")
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        _, out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=work)
        found = sorted({p for p in out.split("\0") if p and not p.startswith(".git/")
                        and os.path.lexists(os.path.join(work, p))})
        return {"session": session_id, "paths": found[:MAX_PATHS], "truncated": len(found) > MAX_PATHS}

    def search(self, session_id, query, case=False, regex=False):
        """The lines of the session's worktree that match `query`, found with git grep among the files git sees there,
        tracked or new and not ignored, text files only: {session, query, matches: [{path, line, column, text, cut}],
        truncated}. At most MAX_MATCHES lines (then it stops: `truncated`), each cut to MAX_MATCH_LINE characters.
        `query` is literal text, or with `regex` a POSIX extended expression; `case` matches case. Read only; the query
        is one argument to git, never through a shell, and a search that runs past SEARCH_TIMEOUT is stopped."""
        record = self._read(session_id)
        if not record["repo"]:
            raise WorkspaceError("A session without a repository has no files to search.")
        if (not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY
                or any(c in query for c in "\0\n\r")):
            raise WorkspaceError(f"Say what to look for: one line of at most {MAX_QUERY} characters.")
        work = self._work(session_id)
        if not os.path.isdir(work):
            raise NotFound("This session's worktree is gone.")
        argv = ["git", "grep", "-n", "-I", "-z", "--column", "--no-color", "--untracked", "-E" if regex else "-F",
                *([] if case else ["-i"]), "-e", query, "--"]
        p = subprocess.Popen(argv, cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=_git_env(work))
        late = threading.Event()

        def stop():
            late.set()
            p.kill()
        timer = threading.Timer(SEARCH_TIMEOUT, stop)
        timer.start()
        matches, truncated = [], False
        try:
            for raw in p.stdout:
                parts = raw.rstrip(b"\n").split(b"\0", 3)
                if len(parts) != 4 or not parts[1].isdigit() or not parts[2].isdigit():
                    continue
                text = parts[3].decode("utf-8", "replace")
                matches.append({"path": parts[0].decode("utf-8", "replace"), "line": int(parts[1]),
                                "column": int(parts[2]), "text": text[:MAX_MATCH_LINE],
                                "cut": len(text) > MAX_MATCH_LINE})
                if len(matches) >= MAX_MATCHES:
                    truncated = True
                    break
        finally:
            timer.cancel()
            if truncated and p.poll() is None:
                p.kill()
            err = p.stderr.read(4096)
            p.stdout.close()
            p.stderr.close()
            code = p.wait()
        if late.is_set() and not truncated:
            raise WorkspaceError(f"The search took longer than {SEARCH_TIMEOUT} seconds: look for something narrower.")
        if code not in (0, 1) and not truncated:
            said = [line for line in err.decode("utf-8", "replace").splitlines() if line.strip()]
            if regex and said and re.match(r"fatal: (-e option|command line), '", said[-1]):
                # git says which option and which text before why ("fatal: -e option, 'a(b': parentheses not balanced";
                # "fatal: command line, 'a(b': Unmatched ( or \(" where git uses the C library's): the person needs
                # only why, and how to look for the text itself
                raise WorkspaceError(f"That is not a regular expression git can read ({said[-1].rsplit(': ', 1)[-1][:120]}). "
                                     "Turn off the regular expression to look for the text as typed.")
            raise WorkspaceError("git could not search: " + (said[-1][:200] if said else f"status {code}"))
        return {"session": session_id, "query": query, "matches": matches, "truncated": truncated}

    def files(self, session_id, rel=""):
        """A folder of the worktree: {path, entries: [{name, type: dir, file or link, size}], truncated}; folders
        first. A link is listed as one and never followed, nor is any folder on the way to it (_folder_beneath); a
        folder that resolves outside the worktree is refused."""
        work, parts, path = self._work_path(session_id, rel)
        if not self._within(work, path):
            raise WorkspaceError("That folder leads outside the session's worktree.")
        try:
            folder = _folder_beneath(work, parts)
        except FileNotFoundError:
            raise NotFound("No such folder in the session's worktree.") from None
        except OSError:
            # a link opened not to be followed, as a folder, is ENOTDIR on Linux and ELOOP on a Mac: one answer for both
            raise WorkspaceError("That is not a folder the page opens (a link is not followed).") from None
        entries = []
        try:
            with os.scandir(folder) as it:
                for e in it:
                    if not parts and e.name == ".git":
                        continue
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    kind = "link" if e.is_symlink() else "dir" if e.is_dir(follow_symlinks=False) else "file"
                    entries.append({"name": e.name, "type": kind, "size": st.st_size if kind == "file" else None})
        finally:
            os.close(folder)
        entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
        return {"session": session_id, "path": "/".join(parts), "entries": entries[:MAX_LIST],
                "truncated": len(entries) > MAX_LIST}

    def file(self, session_id, rel):
        """One file of the worktree: {path, size, binary, too_large, link, text}; text only for a regular file within
        MAX_VIEW that is not binary. A link is reported, not read through."""
        work, parts, path = self._work_path(session_id, rel)
        if not parts:
            raise WorkspaceError("Name a file.")
        if not self._within(work, os.path.dirname(path)):
            raise WorkspaceError("That file is outside the session's worktree.")
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise NotFound("No such file in the session's worktree.") from None
        out = {"session": session_id, "path": "/".join(parts), "size": st.st_size, "binary": False,
               "too_large": False, "link": stat.S_ISLNK(st.st_mode), "text": None}
        if out["link"]:
            return out
        if not stat.S_ISREG(st.st_mode):
            raise WorkspaceError("That is not a file.")
        if st.st_size > MAX_VIEW:
            out["too_large"] = True
            return out
        data = _read_regular(work, parts, MAX_VIEW)
        if len(data) > MAX_VIEW:
            out["too_large"] = True
            return out
        if b"\0" in data[:8192]:
            out["binary"] = True
            return out
        out["text"] = data.decode("utf-8", "replace")
        out["sha"] = hashlib.sha256(data).hexdigest()
        try:
            data.decode("utf-8")
            out["editable"] = True
        except UnicodeDecodeError:
            out["editable"] = False     # saving what the page shows would change the bytes it could not read
        return out

    def image(self, session_id, rel):
        """An image of the worktree, or a PDF, for the page to show: (its bytes, its type). Only the kinds in
        SHOWN_TYPES, by the file's name; a regular file within the worktree and MAX_IMAGE, never read through a link."""
        work, parts, path = self._work_path(session_id, rel)
        if not parts:
            raise WorkspaceError("Name a file.")
        kind = SHOWN_TYPES.get(os.path.splitext(parts[-1])[1].lower())
        if kind is None:
            raise WorkspaceError("Only an image or a PDF is shown this way.")
        if not self._within(work, os.path.dirname(path)):
            raise WorkspaceError("That file is outside the session's worktree.")
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise NotFound("No such file in the session's worktree.") from None
        if not stat.S_ISREG(st.st_mode):
            raise WorkspaceError("That is not a file the page shows (a link is not followed).")
        if st.st_size > MAX_IMAGE:
            raise WorkspaceError(f"The file is larger than {MAX_IMAGE // (1024 * 1024)} MB, the most the page shows.")
        return _read_regular(work, parts, MAX_IMAGE)[:MAX_IMAGE], kind

    def save(self, session_id, rel, text, base):
        """Write one file of the worktree from the page's editor: `text` in place of what is there, only if what is there
        is still what the page opened (`base`, its sha256 from `file`), so what the agent wrote meanwhile is not
        written over unseen (Conflict). A regular file already in the worktree, never through a link; its mode kept;
        replaced whole, never half written. Gives the file back as `file` does, as it was written."""
        work, parts, path = self._work_path(session_id, rel)
        if not parts:
            raise WorkspaceError("Name a file.")
        if not isinstance(text, str):
            raise WorkspaceError("A file's text is text.")
        data = text.encode("utf-8")
        if len(data) > MAX_VIEW:
            raise WorkspaceError(f"A file saved from the page is at most {MAX_VIEW // 1024} KB.")
        if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{64}", base):
            raise WorkspaceError("Say which version of the file this replaces.")
        if not self._within(work, os.path.dirname(path)):
            raise WorkspaceError("That file is outside the session's worktree.")
        gone = "No such file in the session's worktree: it may have been deleted."
        with self._save_lock:       # the check and the write are one step among the page's saves
            # every step through one descriptor of the file's folder, reached without following a link
            # (_folder_beneath): a folder swapped for a link after the look above is never read or written through
            try:
                folder = _folder_beneath(work, parts[:-1])
            except FileNotFoundError:
                raise NotFound(gone) from None
            except OSError:
                raise WorkspaceError(NOT_FOLLOWED) from None
            name = parts[-1]
            try:
                try:
                    st = os.stat(name, dir_fd=folder, follow_symlinks=False)
                except FileNotFoundError:
                    raise NotFound(gone) from None
                if stat.S_ISLNK(st.st_mode):
                    raise WorkspaceError("That is a link: the page does not write through it.")
                if not stat.S_ISREG(st.st_mode):
                    raise WorkspaceError("That is not a file.")
                # read as the Files tab reads: a pipe swapped in here would hold every save behind the lock
                now = _read_in(folder, name, MAX_VIEW)
                if hashlib.sha256(now).hexdigest() != base:
                    raise Conflict("The file changed since you opened it (the agent may have written it).")
                tmp = f".pp-save-{secrets.token_hex(8)}"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(data)
                        os.fchmod(fh.fileno(), stat.S_IMODE(st.st_mode))
                    os.replace(tmp, name, src_dir_fd=folder, dst_dir_fd=folder)
                except BaseException:
                    try:
                        os.unlink(tmp, dir_fd=folder)
                    except OSError:
                        pass
                    raise
            finally:
                os.close(folder)
            written = hashlib.sha256(data).hexdigest()
        # what was written, not the file read again: what the agent writes after this is the next save's to find
        return {"session": session_id, "path": "/".join(parts), "size": len(data), "binary": False, "too_large": False,
                "link": False, "text": text, "sha": written, "editable": True}

    async def remove(self, session_id):
        """Stop the program and the session's shells, and remove the worktree. Its branch stays in the repository with
        every commit on it, unless nothing at all can be lost with it (_drop_empty_branch: never moved from where the
        session started, which another ref reaches): a repository added from the person's machine is theirs, and a
        branch left for each session they tried and removed is clutter in it. {removed, branch, branch_kept}."""
        if self.agents.is_run(session_id):
            raise WorkspaceError("A run is kept with its agent: retire the agent to remove its worktree.")
        record = self._read(session_id)
        await self.stop(session_id)
        await self._stop_shells(session_id)
        # the skill calls its program made and no read took yet, before its folder goes (skillcalls.py: a count can miss
        # calls, and this keeps it from missing these); a failure of it never stops the removal
        with contextlib.suppress(Exception):
            await asyncio.to_thread(lambda: self.skill_calls.take(
                session_id, self._dir(session_id), record.get("repo"), self.controller.task_id_of(session_id)))
        self.asks.revoke(session_id)
        self.asks.withdraw_from(session_id)
        self._origins.pop(session_id, None)
        self._efforts.pop(session_id, None)
        self._models.pop(session_id, None)
        self._sign_ins.pop(session_id, None)
        try:
            self.accounts.unbind(session_id)
        except accounting.AccountError:
            pass            # an unreadable record of the accounts is left as it is; the session goes all the same
        self.terminals.pop(session_id, None)
        self.chats.pop(session_id, None)
        with self._size_lock:
            self._sizes.pop(session_id, None)
        work = self._work(session_id)
        try:
            path = self._repo_path(record["repo"]) if record["repo"] else None
        except NotFound:
            path = None
        admin = self._worktree_admin(path, work) if path is not None else None
        if path is not None and os.path.exists(work):
            git("worktree", "remove", "--force", work, cwd=path, ok=(0, 128))
        self.places.forget(work)
        shutil.rmtree(self._dir(session_id), ignore_errors=True)
        if admin and os.path.isdir(admin):
            # what git could not remove of its record (its folder was gone): that record alone, never a prune, which
            # would take every worktree of the person's whose folder is away (on a disk not mounted, say)
            shutil.rmtree(admin, ignore_errors=True)
        # whether its branch stays; None for a session that had none (a shell without a repository)
        kept = None if record["branch"] is None else not self._drop_empty_branch(path, record, session_id)
        return {"removed": session_id, "branch": record["branch"], "branch_kept": kept}

    @staticmethod
    def _drop_empty_branch(path, record, session_id):
        """Delete a removed session's own branch only where nothing can be lost with it; whether it went. The branch
        this workspace made for it (pp/<id>), never moved since it was made (its reflog holds that one entry: an
        attempt the agent made and undid is there too, recoverable until the branch goes), still at the commit the
        session started from, that commit reachable from another ref, and checked out in no worktree. Every check reads
        the one tip, and the delete is of that tip only (update-ref's old value): a commit made meanwhile keeps it. Any
        doubt, or git failing, keeps it."""
        branch, base = record.get("branch"), record.get("base")
        if path is None or branch != f"pp/{session_id}" or not base:
            return False
        ref = f"refs/heads/{branch}"
        try:
            code, tip = git("rev-parse", "--verify", "--quiet", ref, cwd=path, ok=(0, 1, 128))
            tip = tip.strip()
            if code != 0 or tip != base:
                return False
            # a branch made to point at another (ref: refs/heads/main, written by a session's program) is kept: deleting
            # it would delete what it points at
            if git("symbolic-ref", "--quiet", ref, cwd=path, ok=(0, 1, 128))[0] != 1:
                return False
            code, log = git("reflog", "show", "--format=%H %gs", ref, cwd=path, ok=(0, 128))
            entries = [line for line in log.splitlines() if line.strip()]
            if code != 0 or len(entries) != 1 or not entries[0].startswith(f"{tip} branch: Created from "):
                return False
            # never one checked out in a worktree (git branch -d refuses those too; update-ref would not)
            code, listed = git("worktree", "list", "--porcelain", cwd=path, ok=(0, 128))
            if code != 0 or f"branch {ref}" in listed.splitlines():
                return False
            code, alone = git("rev-list", "--count", tip, "--not", f"--exclude={ref}", "--all", cwd=path, ok=(0, 128))
            if code != 0 or alone.strip() != "0":
                return False
            return git("update-ref", "--no-deref", "-d", ref, tip, cwd=path, ok=(0, 1, 128))[0] == 0
        except WorkspaceError:
            return False

    @staticmethod
    def _worktree_admin(path, work):
        """The repository's own record of a session's worktree (<common>/worktrees/<name>): the one whose gitdir is
        the worktree's .git, or None."""
        _, common = git("rev-parse", "--git-common-dir", cwd=path, ok=(0, 128))
        common = common.strip()
        if not common:
            return None
        records = os.path.join(common if os.path.isabs(common) else os.path.join(path, common), "worktrees")
        mine = os.path.realpath(os.path.join(work, ".git"))
        try:
            names = os.listdir(records)
        except OSError:
            return None
        for name in names:
            try:
                with open(os.path.join(records, name, "gitdir"), encoding="utf-8") as fh:
                    if os.path.realpath(fh.read().strip()) == mine:
                        return os.path.join(records, name)
            except OSError:
                continue
        return None
