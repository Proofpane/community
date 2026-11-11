"""The first gates: a Claude Code PreToolUse hook that every `claude` session
in a workspace runs, and every `qwen` (Qwen Code, whose hooks are Claude Code's); the same rules as a Gemini CLI
BeforeTool hook for every `gemini` session (`--gemini`: its call and its answer in Gemini's own form), and as a
PreToolUse hook for every `codex` and every `grok` (Grok Build): Codex
gives the call and takes the answer in Claude Code's form, its shell tool `Bash` with the command as a string; Grok
Build gives it with camelCase names (`toolName`, `toolInput`, its shell tool `run_terminal_command`) and takes Claude
Code's answer, which it ranks above its own. Refusing five things:

- a tool call that names the programs' own sign-in: Claude Code's `~/.claude/.credentials.json` (and, where the
  workspace is a Mac, the keychain item Claude Code keeps there instead), Codex's `~/.codex/auth.json` (and
  `$CODEX_HOME/auth.json`, as a session on another Codex account has its home), Gemini CLI's
  token files in `~/.gemini/`, OpenCode's `~/.local/share/opencode/auth.json` and `mcp-auth.json`, Grok Build's
  `~/.grok/auth.json` and `mcp_credentials.json`, Qwen Code's `~/.qwen/oauth_creds.json` and `mcp-oauth-tokens.json`,
  Kimi Code's `~/.kimi-code/credentials/` (the whole folder) and `config.toml` (which holds any API key it is given),
  MiniMax Code's `~/.minimax/auth/` (the whole folder), `config.yaml` (its API key, in the clear) and
  `codex-auth.json`, and `~/.pi/agent/auth.json` (the pi agent MiniMax Code builds on keeps its keys there), the
  person's own keys for other providers' models (`~/.proofpane/model-keys.json`) and for Linear
  (`~/.proofpane/linear-key.json`), the workspace's pairing with the owner's Proofpane organisation and the daemon's
  files beside it (the folder `~/.proofpane/proofpane-core/`, core_plugin.py), on a workspace machine the hook token
  its sessions ask this deployment's approval queue with (the folder `/run/proofpane/ade/`, and the token's name,
  `ade-hook-token`: workspace_image/start.sh; refused in words of their own, ADE_TOKEN, and only where
  that folder is, so the local provider's sessions, which have no such token, are refused nothing more), the record of
  the workspace's other Claude Code and Codex accounts and of the
  account each session runs on (`agent-accounts.json`, accounts.py; their own sign-ins are the files above, in each
  account's folder, and the rest of a folder is the program's own, where Claude Code writes its plans, memory and saved
  tool output), and GitHub's CLI's
  `~/.config/gh/hosts.yml`, or runs the gh command that prints its token (`gh auth token`, `gh auth status` with
  `-t`), so that an agent in a session does not read or copy out the user's sign-in;
- a force push, in the spellings git takes: `-f`, `--force`, `--force-with-lease`, `--force-if-includes`, or a `+`
  before a refspec;
- the gate itself switched off: a call that names it (`proofpane-gate`, the name Gemini CLI's `hooksConfig.disabled`
  would list), or a command that starts a program without it (Gemini CLI's or Qwen Code's system settings moved
  elsewhere, Qwen Code's safe or bare mode);
- the workspace's record of the git settings it accepted (`git-places.json`, gitplaces.py), which only the workspace
  writes; its record of what the owner's controller did (`controller-record.json`, controller.py), which holds
  the owner's requests and which only the workspace writes; and its record of the skills its sessions called
  (`skill-calls.json`, skillcalls.py), taken from each session's own file, which only the workspace
  writes. Each rule is on the file's name, in any string of the call (a path, a command, the text a `Write` puts in a
  file, an `Edit`'s `new_string`, a search pattern): a tool call that spells it is refused (as written, or as a shell
  reads its words), so a session cannot write a file whose text names one of them, nor search for it; and a glob, an
  operation on its folder (`rm -rf`, `mv`) or a program that builds the name is not caught. The skills record's rule
  ignores case, since a volume that does not tell cases apart (macOS by default) names the file by any spelling; the
  other two are on the exact spelling, so on such a volume `GIT-PLACES.json` passes them. A
  program a session runs can write the record as well, as every program of the workspace runs as one user: what it
  holds is for display.
- the folder the workspace runs a controller's task's checks in (`controller-checks`, controller.py), named as a whole
  name, in any case, since a Mac's volume takes `Controller-Checks` for it: a name it is only a part of
  (`controller-checksum`, `my-controller-checks`) is not refused. A rule by name refuses what names the folder: a call
  that reaches it without its name (a glob, a variable the shell would expand, a link a program made) is not refused,
  and what a program a session runs does is not gated.

In a replay (a run started again to measure it, or the improve loop's: PP_REPLAY in its environment), which must not
act on the world, more: a command naming a program that reaches outside the machine or publishes (gh, curl, wget, ssh,
scp, rsync, nc, the clouds' and the hosts' command lines, terraform and others: _REPLAY_PROGRAMS), `git push`, a
package's publish or deploy, `docker push`, or an interpreter's inline code that opens the network, however the command
spells them (quotes, backslashes and braces read as the shell reads them); the WebFetch and WebSearch tools; and every
MCP server's tool and connector. It reads a command's words and is not a sandbox: a script run through an allowed
command, or a name put together while it runs, is not seen. A run that made such a call itself is not replayable
(metrics.py).

And for an agent's run (agents.py), which nobody may be watching, six more, in every permission mode (the gate is a
PreToolUse hook, which runs before the mode's own decision): a call naming `~/.claude.json` (Claude Code's settings
and its MCP servers', read or written); a write, an edit or a command naming a `.mcp.json` or anything in a `.claude`
folder (settings, skills, agents and hooks: a project skill's hooks and allowed tools are used by an SDK run without
workspace trust, so a run that wrote one could widen its own next run); a `claude mcp` command; a call naming Gemini
CLI's `~/.gemini/trustedFolders.json` or a `.gemini/settings.json`, or a write, an edit or a command naming anything in
a `.gemini` folder (the same reason, for Gemini: these decide which folders it trusts, what its hooks run and what
environment it loads; a project `.env` is ordinary work and not refused); and a path inside the
workspace's agents' folder other than the run's own worktree, its own folder and its agent's memory (where Claude
writes its auto memory); and a path inside the workspace's repositories' folder or its sessions' folder, or, in the
desktop app, a repository's own folder that its link there leads to: another repository, or an owner's session,
where the workspace's own git and the owner's chats run (gitplaces.py checks git's settings there besides). A run is
known by the PP_AGENT_RUN the workspace sets in its environment, or, should a hook's environment not carry it, by its
worktree being an agent's. The owner's own chats are unchanged.

The command rules hold for whatever tool runs a command, not only the shell tools: Grok Build's `monitor` runs one and
gives its output to the model, and Codex's `write_stdin` types into one that runs. Every rule reads a command's words
as the shell reads them too (shell_words): its variables put in from the run's environment ($NAME, ${NAME}), then its
quotes and backslashes taken out, so `cat "$CLAUDE_CONFIG_DIR"/his''tory.jsonl` is read as the path it opens.

And for a task's attempt (PP_TASK_ATTEMPT in its environment, the workspace's agents.py and
task_attempt.py, which also gives these rules to the attempt's claude as a callback of its own): while it is frozen
(PP_FROZEN, its resume check not yet passed) every call but its two tools, task_state and resume_ack; and, at every
point, a call naming a path in any account's folder of the workspace (the default's ~/.claude, every added account's
under the workspace's agent-accounts/, and its own CLAUDE_CONFIG_DIR), but for reading, with Read, Glob or Grep alone,
its own session's saved tool output (the tool-results beside its transcript, which Claude Code names to the model) and
its own account's skills (which the workspace links from the default's folder, accounts.py). Those two are matched on
the path as it resolves (every link followed, `..` taken out), never on the call's text: a write to either, a shell
command naming either, a path that climbs out of either or through a link planted in it, and another session's saved
output are refused as every other path there is. A path is read where the call names it: a path field as written and
with its variables put in, a Glob's or a Grep's pattern from the call's own path, each word of a command, and inside a
word, in a command as written and as the shell reads it and in any string an MCP server's tool is given (a folder's
path written anywhere in it, as in `dd if=...` or a brace's list, or a part of a word, an option's letters taken off,
that resolves into a folder); not in a file's content, which names no path the call opens, nor in the workspace's own
tools' words. And, read in all those ways, a path where the workspace's own git and the owner's chats run, which an
agent's run is refused: the workspace's repositories' folder, its sessions' folder, and, in the desktop
app, a repository's own folder that its link there leads to (git_folders), whether or not its environment says it is
an agent's run.

It reads the tool call from standard input and prints Claude Code's deny answer, or nothing, which lets the call go
on as it would without this hook. When it cannot read the call, it denies; and a call the program says it gave only
in part (Grok Build's `toolInputTruncated`) is one it cannot read, whatever its tool, since its rules on names read
every string a call carries, a file's text too. It needs only the standard library and runs inside the workspace, so
it imports nothing of the app's.

What it is not: a sandbox. It reads what a tool call says; a command that reaches the same file by a path it does not
spell out (a glob, a variable, a script it wrote first) is not recognised. A repository's own settings for a program (Claude Code's project hooks,
say) run as the workspace user, as its code does when an agent runs its tests: they are not tool calls. The
programs start this with Python's -E, -s and -X utf8 (settings.ISOLATED), so a variable a repository's settings set,
or a file a session wrote in the user's home, does not run inside it, and a locale a settings file names whose
character set Python has no codec for does not end its interpreter before it answers (measured on Linux with three
such glibc locales, not on a Mac). In the local provider the session runs as
the same user as the agent and could change this file. Separation between users is the fly provider's, where each
user has a machine of their own and the gate and its settings belong to the image's root. OpenCode has no hooks for a
gate to use.
"""

import json
import os
import re
import shlex
import sys
import time

CREDENTIAL_FILES = (".claude/.credentials.json", ".codex/auth.json", ".config/gh/hosts.yml",
                    # Gemini CLI's sign-in and tokens (0.62.0), and OpenCode's providers' keys (1.18.34)
                    ".gemini/oauth_creds.json", ".gemini/gemini-credentials.json", ".gemini/google_accounts.json",
                    ".gemini/mcp-oauth-tokens.json", ".gemini/a2a-oauth-tokens.json",
                    ".local/share/opencode/auth.json", ".local/share/opencode/mcp-auth.json",
                    # Grok Build's sign-in and its MCP servers' (1.0.62), and Qwen Code's (0.24.7)
                    ".grok/auth.json", ".grok/mcp_credentials.json", ".qwen/oauth_creds.json",
                    ".qwen/mcp-oauth-tokens.json",
                    # the person's own keys for other providers' models, and the helper that gives one to claude; and
                    # their Linear key (linear.py)
                    ".proofpane/model-keys.json", ".proofpane/linear-key.json",
                    # Kimi Code's settings, which hold any API key it is given (2.1.1); MiniMax Code's settings, which
                    # hold its API key in the clear, and its sign-in to a ChatGPT account (0.6.1); the pi agent's keys,
                    # which MiniMax Code is built on
                    ".kimi-code/config.toml", ".minimax/config.yaml", ".minimax/codex-auth.json", ".pi/agent/auth.json",
                    # the Community desktop app's own: the secret its sign-ins are signed with and its database (its
                    # data folder also holds the workspace's worktrees, so the folder itself is not closed), and the
                    # same in the folder `proofpane_open.desktop --open` uses
                    "Library/Application Support/com.proofpane.community/token-secret",
                    "Library/Application Support/com.proofpane.community/open_edition.db",
                    ".proofpane-community/token-secret", ".proofpane-community/open_edition.db")
# folders that hold nothing but sign-ins: Kimi Code's (2.1.1: a `<name>.json` each) and MiniMax Code's (0.6.1: its
# auth.json and auth-state.json, in a folder for each environment and region); and what an agent on the Community
# desktop app's own Mac must not reach: the app's web data (a hosted workspace's sign-in was once kept there), and
# Proofpane Tray's folder, whose socket approves a device's sign-in (the tray answers the signed app alone; this is a
# second wall, for a program the gate sees)
CREDENTIAL_FOLDERS = (".kimi-code/credentials", ".minimax/auth", "Library/WebKit/com.proofpane.community",
                      "Library/HTTPStorages/com.proofpane.community", "Library/Application Support/com.airgov.tray",
                      # the workspace's pairing with the owner's Proofpane organisation (core_plugin.py): its device
                      # token, and the daemon's own files where its MCP server runs in that name
                      ".proofpane/proofpane-core")
_NAMED = re.compile(r"\.credentials\.json|\.codex/auth\.json|codex[/\\]+auth\.json|Claude Code-credentials"
                    # Codex's sign-in through its home's variable, which a session on another account has (accounts.py)
                    r"|CODEX_HOME\}?[\"']?[/\\]+[\"']?auth\.json"
                    r"|gh[/\\]+hosts\.yml|oauth_creds\.json|gemini-credentials\.json|google_accounts\.json"
                    r"|(?:mcp|a2a)-oauth-tokens\.json|opencode[/\\]+(?:mcp-)?auth\.json"
                    r"|\.grok[/\\]+(?:auth|mcp_credentials)\.json|model-keys\.json|model_key\.py|linear-key\.json"
                    r"|\.kimi-code[/\\]+(?:credentials|config\.toml)(?![\w.-])"
                    r"|\.minimax(?:-[\w-]+)?[/\\]+(?:auth|config\.yaml|codex-auth\.json)(?![\w.-])"
                    r"|\.pi[/\\]+agent[/\\]+auth\.json"
                    r"|com\.proofpane\.community[/\\]+(?:token-secret|open_edition\.db)"
                    r"|\.proofpane-community[/\\]+(?:token-secret|open_edition\.db)"
                    r"|WebKit[/\\]+com\.proofpane\.community|com\.airgov\.tray|community\.sock"
                    r"|agent-accounts\.json|\.proofpane[/\\]+proofpane-core(?![\w.-])")
# a hosted workspace's hook token for this deployment's approval queue: its name, and the folder start.sh
# puts it in, whole; refused only on a workspace machine, where that folder is (ADE_FOLDER, settings.ADE_HOOK_FOLDER)
_ADE_NAMED = re.compile(r"ade-hook-token|run[/\\]+proofpane[/\\]+ade(?![\w.-])")
ADE_FOLDER = "/run/proofpane/ade"
# Claude Code's and Codex's, Gemini CLI's and Qwen Code's, Grok Build's
SHELLS = ("Bash", "run_shell_command", "run_terminal_command")
# where any tool gives a command it runs or types (Grok Build's monitor, Codex's exec_command and write_stdin)
COMMAND_FIELDS = ("command", "cmd", "chars")
_KEYCHAIN = re.compile(r"\bsecurity\s+(?:find-generic-password|find-internet-password|dump-keychain|export)\b"
                       r"|\bgh\s+auth\s+token\b"
                       r"|\bgh\s+auth\s+status\b[^;&|\n]*\s(?:-t|--show-token)\b")
_GATE_NAMED = re.compile(r"proofpane-gate")
_PLACES_NAMED = re.compile(r"git-places\.json")
_CONTROLLER_NAMED = re.compile(r"controller-record\.json")
# the skills record is named whatever the case: a volume that does not tell cases apart (macOS by default) gives the
# same file under another spelling. The two rules above are on the exact spelling, as they were written.
_SKILLS_NAMED = re.compile(r"skill-calls\.json", re.IGNORECASE)
# the folder a controller's task's checks run in (controller.CHECKS_FOLDER), as a whole name and in any case
_CHECKS_NAMED = re.compile(r"(?<![\w.-])controller-checks(?![\w.-])", re.IGNORECASE)
_UNGATED = re.compile(r"\b(?:GEMINI_CLI_SYSTEM_SETTINGS_PATH|QWEN_CODE_SYSTEM_SETTINGS_PATH|QWEN_CODE_SAFE_MODE"
                      r"|QWEN_CODE_SIMPLE)\b|\bqwen\b[^;&|\n'\"]*\s--(?:safe-mode|bare)\b")
_PUSH = re.compile(r"\bgit\b(?:\s+-[cC]\s+\S+)*\s+push\b(?P<rest>[^;&|\n]*)")
# an agent's run (agents.py): Claude Code's write tools, and Gemini CLI's (write_file, replace), whose path is file_path
WRITERS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "write_file", "replace")
PATH_FIELDS = ("file_path", "path", "notebook_path")
_CLAUDE_JSON = re.compile(r"\.claude\.json(?![\w-])")
_MCP_JSON = re.compile(r"(?:^|[^\w.-])\.mcp\.json(?![\w-])|[/\\]\.mcp\.json(?![\w-])")
_CLAUDE_FOLDER = re.compile(r"(?:^|[^\w.-])\.claude(?:[/\\]|$|(?![\w.-]))")
_CLAUDE_MCP = re.compile(r"\bclaude\b[^;&|\n]*?\s+mcp\b")
# Gemini CLI's trusted folders and its settings, which decide which folders Gemini trusts and what its hooks run
# (`~/.gemini/trustedFolders.json`, and a `.gemini/settings.json`, the user's own or a repository's): refused in any
# string the call carries, read or written. Required `.gemini/` prefix, bounded as `_GEMINI_FOLDER` is so a folder
# merely ending in `.gemini` (`my.gemini/settings.json`) is the run's own work: Gemini reads only the one
# `~/.gemini/trustedFolders.json`, so a file named `trustedFolders.json` elsewhere changes no trust. A project `.env`
# is ordinary work and is not refused. (It reads every string the call carries, as `_CLAUDE_JSON` does, so content that
# names one of these files is refused too: a conservative case of the same rule, and an agent's run rarely writes it.)
_GEMINI_CONFIG = re.compile(r"(?:^|[^\w.-])\.gemini[/\\]+(?:trustedFolders|settings)\.json(?![\w.-])")
# and anything in a `.gemini` folder, written or named by a command, as for a `.claude` folder: Gemini also keeps there
# a `.env` that it loads in a trusted folder, which the exclusion list in its system settings does not reach
# (settings.gemini_settings)
_GEMINI_FOLDER = re.compile(r"(?:^|[^\w.-])\.gemini(?:[/\\]|$|(?![\w.-]))")
_AGENT_WORK = re.compile(r"^(?P<agent>.*[/\\]agents[/\\][0-9a-f]{12})[/\\](?:work|runs[/\\][0-9a-f]{12}[/\\]work)$")
_TOKEN = re.compile(r"[^\s;&|<>()`'\"=]+")
_FORCE = re.compile(r"(?:^|\s)(?:--force(?:-with-lease|-if-includes)?(?:=\S*)?|-[A-Za-z]*f[A-Za-z]*|\+\S+)(?=\s|$)")


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "Refused by the workspace's gate: " + reason}}


SIGN_IN = "the programs' own sign-in is not read, copied or changed from a session."
ADE_TOKEN = "the approval hook's token is not read, copied or changed from a session."
REPLAY = ("a replay does not act on the world: nothing that reaches outside the machine or publishes (gh, git push, curl, "
          "ssh, a publish or a deploy), no web fetch or search, and no MCP server's tool.")
REPLAY_TOOLS = ("WebFetch", "WebSearch")
# the one tool of the workspace's own a replay rule lets through: a review run's proposal, which only writes into the
# run's own record (improve.py)
REPLAY_OWN = ("mcp__proofpane__propose_change",)
_REPLAY_PROGRAMS = ("gh", "curl", "wget", "ssh", "scp", "sftp", "rsync", "ftp", "telnet", "nc", "ncat", "netcat", "socat",
                    "aws", "gcloud", "gsutil", "az", "kubectl", "helm", "fly", "flyctl", "heroku", "vercel", "netlify",
                    "terraform", "pulumi", "twine")
# a program named as a command word, in a command read as the shell reads its words (_words): after a separator, a
# folder (/usr/bin/gh) or an assignment (x=curl), and ended as one, so src/gh/notes.md is not one
_REPLAY_COMMAND = re.compile(r"(?:^|[;&|(`\s/=])(?:" + "|".join(_REPLAY_PROGRAMS) + r")(?=$|[\s;&|)`])")
# git's push as its subcommand, after any of git's options (-C dir, -c key=value, --opt[=value]), or an alias set to
# one; a package's publish or deploy, docker's push, with any words between; and an interpreter's inline code that
# opens the network
_REPLAY_SUB = re.compile(r"\bgit(?:\s+(?:-[Cc]\s+\S+|--?[\w-]+(?:=\S*)?))*\s+(?:push|send-email|request-pull)\b"
                         r"|\bgit\b[^;&|\n]*\balias\.[\w.-]+=\S*\b(?:push|send-email|request-pull)\b"
                         r"|\b(?:npm|yarn|pnpm|bun|cargo|poetry|gem|dotnet|mvn|gradle)\b[^;&|\n]*"
                         r"\b(?:publish|deploy|push|release)\b"
                         r"|\bdocker\b[^;&|\n]*\b(?:push|login)\b"
                         r"|\b(?:python[0-9.]*|node|ruby|perl|php|deno)\b[^;&|\n]*\s-[ce]\b[^;&|\n]*"
                         r"\b(?:socket|urllib|requests|httpx|http|https|fetch|net|LWP)\b")


def _words(command):
    """A command as the shell reads its words: quotes and backslashes gone (c''url, c\\url, "push"), and a brace or a
    comma as a space between words ({curl,-s,...})."""
    return re.sub(r"[{},]", " ", re.sub(r"[\"'\\]", "", command))


_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:[:#%/][^}]*)?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def shell_words(command, env=None):
    """A command's words as the shell reads them: each variable ($NAME, ${NAME}, ${NAME:-...}) put in from `env` (the
    run's environment; empty for one it does not have), then split into words with their quotes and backslashes taken
    out (POSIX, as shlex reads it), each operator (; & | ( ) < >) a word of its own, and each word's braces expanded
    as bash expands them (_braces). A variable inside single quotes is put in too, and a quoted brace expanded, which a
    shell would not do: the gate reads more paths, never fewer. A command the reader cannot split (a quote left open) is
    read with every quote and backslash taken out (_words). Braces that make more than MAX_WORDS words raise
    TooManyWords, which every caller takes as a call it cannot read."""
    env = os.environ if env is None else env
    put = _VARIABLE.sub(lambda m: str(env.get(m.group(1) or m.group(2), "")), str(command))
    try:
        lexer = shlex.shlex(put, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        words = _words(put).split()
    made = [0]
    return [each for word in words for each in (_braces(word, made) if "{" in word else [word])]


MAX_WORDS = 4096                # the words a command's braces may make: past it the gate reads none of them


class TooManyWords(Exception):
    """A command whose braces make more than MAX_WORDS words (not a ValueError, so no reader falls back past it)."""


_SEQUENCE = re.compile(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?|([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?")


def _braces(word, made):
    """A word's brace expansions as bash makes them: its leftmost brace group with a comma at its top level, or a
    sequence ({1..3}, {01..10..3}, {a..e}), expanded, and each result again; a group that is neither is kept as it is
    (`{x}`), the groups inside it still expanded. `made` counts the words made; past MAX_WORDS, TooManyWords."""
    start = word.find("{")
    while start != -1:
        end = _closing(word, start)
        if end is None:
            break
        inner = word[start + 1:end]
        parts = _top_level(inner)
        choices = parts if len(parts) > 1 else _sequence(inner)
        if choices is not None:
            out = []
            for choice in choices:
                out += _braces(word[:start] + choice + word[end + 1:], made)
            return out
        start = word.find("{", start + 1)
    made[0] += 1
    if made[0] > MAX_WORDS:
        raise TooManyWords("a command's braces make more words than the gate reads")
    return [word]


def _closing(word, start):
    """The index of the brace that closes the one at `start`, or None."""
    depth = 0
    for i in range(start, len(word)):
        if word[i] == "{":
            depth += 1
        elif word[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _top_level(inner):
    """A brace group's contents split at its top-level commas."""
    parts, depth, last = [], 0, 0
    for i, ch in enumerate(inner):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(inner[last:i])
            last = i + 1
    return parts + [inner[last:]]


def _sequence(inner):
    """The words of a brace sequence, or None for a group that is not one; one longer than MAX_WORDS, TooManyWords."""
    m = _SEQUENCE.fullmatch(inner)
    if m is None:
        return None
    if m.group(1) is not None:
        first, last, step = int(m.group(1)), int(m.group(2)), abs(int(m.group(3) or 1)) or 1
        padded = any(g.lstrip("-").startswith("0") and len(g.lstrip("-")) > 1 for g in (m.group(1), m.group(2)))
        width = max(len(m.group(1)), len(m.group(2))) if padded else 0
    else:
        first, last, step = ord(m.group(4)), ord(m.group(5)), abs(int(m.group(6) or 1)) or 1
    if abs(last - first) // step + 1 > MAX_WORDS:
        raise TooManyWords("a command's braces make more words than the gate reads")
    values = range(first, last + 1, step) if first <= last else range(first, last - 1, -step)
    if m.group(1) is None:
        return [chr(v) for v in values]
    return [f"{v:0{width}d}" if width else str(v) for v in values]


def shell_line(words):
    """Words as one line again, for the rules that read a whole command: each word's own spaces made NULs, so that a
    quoted prompt ('add a --bare flag') stays one word and never reads as a flag or a second command."""
    return " ".join(re.sub(r"\s", "\x00", w) for w in words)


def replay_refuses(command):
    """Whether a replay may not run `command`, a shell command's text."""
    plain = _words(str(command))
    return bool(_REPLAY_COMMAND.search(plain) or _REPLAY_SUB.search(plain))


def acts_on_the_world(tool, tool_input):
    """Whether a call is one a replay may not make: a tool that reaches out, an MCP server's tool, or a command of
    REPLAY's."""
    if not isinstance(tool, str):
        return True
    if tool in REPLAY_OWN:
        return False
    if tool in REPLAY_TOOLS or tool.startswith("mcp__"):
        return True
    if tool in SHELLS and isinstance(tool_input, dict):
        command = tool_input.get("command") or ""
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        return replay_refuses(command)
    return False


AGENT_SETTINGS = ("an agent's run does not read or change Claude Code's settings, its MCP servers' or a .claude folder, "
                  "or add an MCP server: the owner does that from the page.")
AGENT_FOLDER = ("an agent's run works in its own worktree, its own folder and its agent's memory, not elsewhere in the "
                "agents' folder.")
AGENT_REPOS = ("an agent's run works in its own worktree, not in another repository of the workspace's or an owner's "
               "session, where the workspace's own git and the owner's chats run.")
AGENT_GEMINI = ("an agent's run does not read or change Gemini CLI's trusted folders or its settings, nor write in a "
                ".gemini folder or name one in a command: they decide which folders Gemini trusts, what its hooks run "
                "and what it loads, and the owner does that in their own sessions.")
PLACES_RECORD = "the workspace's record of the git settings it accepted is not read or changed from a session."
CONTROLLER_RECORD = ("the workspace's record of what the owner's Manager did is not read or changed from a "
                     "session.")
SKILLS_RECORD = "the workspace's record of the skills its sessions called is not read or changed from a session."
CHECKS_FOLDER = "the folder the workspace runs its checks in is not read or changed from a session."
GATE_OFF = "the workspace's gate is not switched off, or a program started without it, from a session."


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _is_credential_path(text, home):
    """Whether `text`, read as a path (after `~`, which is the session user's home, and any links), is one of the
    programs' credential files there, or one of their credential folders or anything in one."""
    if "\x00" in text or len(text) > 4096 or not text.startswith(("~", "/", ".")):
        return False
    path = os.path.join(home, text[2:]) if text == "~" or text.startswith("~/") else os.path.expanduser(text)
    path = os.path.realpath(path)
    if any(path == os.path.realpath(os.path.join(home, f)) for f in CREDENTIAL_FILES):
        return True
    for folder in CREDENTIAL_FOLDERS:
        folder = os.path.realpath(os.path.join(home, folder))
        if path == folder or path.startswith(folder + os.sep):
            return True
    return False


UNREADABLE = "the gate could not read this tool call."
CUT_SHORT = "the gate was given only part of this command, so it could not read it all."
CUT_SHORT_CALL = "the gate was given only part of this tool call, so it could not read it all."


def _call(payload):
    """The tool and its input, as Claude Code, Codex and Gemini CLI name them (snake case) or as Grok Build does."""
    tool = payload["tool_name"] if "tool_name" in payload else payload.get("toolName")
    given = payload["tool_input"] if "tool_input" in payload else payload.get("toolInput")
    return tool, given


def agent_run(payload, env=None):
    """{agent, work, run} for a call from an agent's run, else None: from the environment the workspace gave it, or,
    when a hook's environment does not carry that, from its working directory being an agent's worktree."""
    env = os.environ if env is None else env
    cwd = payload.get("cwd") if isinstance(payload, dict) and isinstance(payload.get("cwd"), str) else ""
    found = _AGENT_WORK.match(os.path.normpath(cwd)) if cwd else None
    if env.get("PP_AGENT_RUN") == "1" and env.get("PP_AGENT_DIR"):
        agent = os.path.normpath(env["PP_AGENT_DIR"])
    elif found:
        agent = found.group("agent")
    else:
        return None
    return {"agent": agent, "work": os.path.normpath(cwd) if cwd else os.path.join(agent, "work"),
            "run": os.path.normpath(env["PP_SESSION_DIR"]) if env.get("PP_SESSION_DIR") else None}


def _inside(path, folder):
    return path == folder or path.startswith(folder + os.sep)


def _agent_refusal(tool, tool_input, texts, commands, run, home):
    """Why a call from an agent's run is refused, or None. `commands` are its commands as written and as the shell
    reads them."""
    if any(_CLAUDE_JSON.search(t) for t in list(texts) + list(commands)):
        return AGENT_SETTINGS
    written = [tool_input.get(f) for f in PATH_FIELDS if tool in WRITERS and isinstance(tool_input.get(f), str)]
    if any(_MCP_JSON.search(t) or _CLAUDE_FOLDER.search(t) for t in written + commands):
        return AGENT_SETTINGS
    if any(_CLAUDE_MCP.search(c) for c in commands):
        return AGENT_SETTINGS
    # Gemini CLI's trust and settings files, in any string the call carries or any command, read or written; and
    # anything else in a `.gemini` folder, written or named by a command
    if any(_GEMINI_CONFIG.search(t) for t in list(texts) + list(commands)):
        return AGENT_GEMINI
    if any(_GEMINI_FOLDER.search(t) for t in written + commands):
        return AGENT_GEMINI
    # paths into the agents' folder: the ones the call gives, and the words of its commands that read as paths
    root = os.path.realpath(os.path.dirname(run["agent"]))
    allowed = [os.path.realpath(run["work"]), os.path.realpath(os.path.join(run["agent"], "memory"))]
    if run["run"]:
        allowed.append(os.path.realpath(run["run"]))
    # and into the workspace's repositories and its sessions' worktrees, and, in the desktop app, the folders its
    # repositories' links lead to: the workspace's own git, and the owner's chats, run there
    beside = os.path.dirname(root)
    repos = os.path.join(beside, "repos")
    others = [os.path.realpath(repos), os.path.realpath(os.path.join(beside, "sessions"))]
    try:
        others += [os.path.realpath(os.path.join(repos, n)) for n in os.listdir(repos)
                   if os.path.islink(os.path.join(repos, n))]
    except OSError:
        pass
    candidates = [tool_input.get(f) for f in PATH_FIELDS if isinstance(tool_input.get(f), str)]
    for command in commands:
        for word in _TOKEN.findall(command):
            # and a path glued to an option (`sort -o<file>`), read with the option's letters taken off, as a task's
            # attempt's words are (_names_inside, _OPTION); first, so the refusal names what the path reaches
            candidates += dict.fromkeys((_OPTION.sub("", word), word))
    for text in candidates:
        if not text or "\x00" in text or len(text) > 4096:
            continue
        if text.startswith("~"):
            text = os.path.join(home, text[2:]) if text == "~" or text.startswith("~/") else text
        path = os.path.realpath(os.path.join(run["work"], text))
        if any(_inside(path, folder) for folder in allowed):
            continue
        if _inside(path, root):
            return AGENT_FOLDER
        if any(_inside(path, folder) for folder in others):
            return AGENT_REPOS
    return None


# a task's attempt: its two tools while it is frozen (the workspace's task_shapes.FROZEN_TOOLS, named here
# again since this runs as a script of its own), and the tools that may read its two places of its account's folder
FROZEN_TOOLS = ("mcp__proofpane__task_state", "mcp__proofpane__resume_ack")
READERS = ("Read", "Glob", "Grep")
FROZEN = "a task's attempt is frozen until its resume check passes: it calls task_state and resume_ack, nothing else."
ACCOUNT_FOLDER = ("a task's attempt reads nothing in an account's folder (its sign-in, transcripts, history, pastes and "
                  "tasks) but its own session's saved tool output and its account's skills, and writes nothing there.")
_GLOB_CHARS = re.compile(r"[*?\[{]")


def task_attempt(env=None):
    """Whether the call is a task's attempt's: the workspace sets PP_TASK_ATTEMPT in its environment."""
    return (os.environ if env is None else env).get("PP_TASK_ATTEMPT") == "1"


def account_folders(env, home):
    """Every account's folder, each as given and as it resolves: the default's (~/.claude), all the workspace's added
    ones (its agent-accounts/, found from the run's agent folder, agents/<id>, two up), and the call's own
    (CLAUDE_CONFIG_DIR)."""
    folders = [os.path.join(home, ".claude")]
    if env.get("PP_AGENT_DIR"):
        folders.append(os.path.join(os.path.dirname(os.path.dirname(os.path.normpath(env["PP_AGENT_DIR"]))),
                                    "agent-accounts"))
    if env.get("CLAUDE_CONFIG_DIR"):
        folders.append(env["CLAUDE_CONFIG_DIR"])
    out = []
    for folder in folders:
        for each in (os.path.normpath(folder), os.path.realpath(folder)):
            if each not in out:
                out.append(each)
    return out


def git_folders(env):
    """The folders where the workspace's own git and the owner's chats run, which an agent's run is refused
    (_agent_refusal), each as given and as it resolves: the workspace's repositories' folder and its sessions'
    folder, found from the run's agent folder (agents/<id>, two up), and, in the desktop app, each repository's own
    folder that its link in the repositories' folder leads to. Empty without an agent folder to find them by."""
    if not env.get("PP_AGENT_DIR"):
        return []
    beside = os.path.dirname(os.path.dirname(os.path.normpath(env["PP_AGENT_DIR"])))
    repos = os.path.join(beside, "repos")
    folders = [repos, os.path.join(beside, "sessions")]
    try:
        folders += [os.path.join(repos, n) for n in sorted(os.listdir(repos)) if os.path.islink(os.path.join(repos, n))]
    except OSError:
        pass
    out = []
    for folder in folders:
        for each in (os.path.normpath(folder), os.path.realpath(folder)):
            if each not in out:
                out.append(each)
    return out


def _readable(payload, env, home):
    """The two places of its own account's folder a task's attempt may read, each as it resolves: its session's saved
    tool output (beside its transcript, which the call names: <folder>/projects/<project>/<session>/tool-results) when
    the transcript is in its own folder's projects, and its account's skills."""
    own = os.path.realpath(env.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"))
    places = [os.path.realpath(os.path.join(own, "skills"))]
    transcript = payload.get("transcript_path") if isinstance(payload, dict) else None
    session = payload.get("session_id") if isinstance(payload, dict) else None
    if isinstance(transcript, str) and isinstance(session, str) and re.fullmatch(r"[A-Za-z0-9-]{1,100}", session):
        real = os.path.realpath(transcript)
        if _inside(real, os.path.join(own, "projects")) and os.path.basename(real) == session + ".jsonl":
            places.append(os.path.join(os.path.dirname(real), session, "tool-results"))
    return places


def _task_paths(tool, tool_input, commands, env):
    """(the path texts a call names, the words of its commands that read as paths): its path fields, each as given
    (the tool opens it so) and with its variables put in, and a Glob's or a Grep's pattern and glob (up to the first
    wildcard), each joined to the call's own `path`, the folder the tool reads them from; and every word of its
    commands as the shell reads them."""
    named = []
    for f in PATH_FIELDS:
        if isinstance(tool_input.get(f), str):
            named.append(tool_input[f])
    if tool in ("Glob", "Grep"):
        base = tool_input.get("path") if isinstance(tool_input.get("path"), str) else None
        # a Glob's pattern and a Grep's glob name files; a Grep's pattern is a regular expression matched in what the
        # files hold, which names none (`..` is any two characters)
        for f in (("pattern",) if tool == "Glob" else ("glob",)):
            given = tool_input.get(f)
            if isinstance(given, str) and given:
                named.append(given if base is None or given.startswith(("/", "~", "$")) else
                             os.path.join(base, given))
    named = list(dict.fromkeys([form for t in named for form in (t, " ".join(shell_words(t, env)))]))
    words = [w for c in commands for w in shell_words(c, env)]
    return named, words


OWN_TOOLS = "mcp__proofpane__"          # the workspace's own in-process server's tools, which open no path
# the owner's controller's six (controller.TOOLS), in its chat alone (marked PP_CONTROLLER_CHAT, controller.configure):
# they open no path, and plan_requirement copies the owner's paragraphs word for word, whatever they name, so no rule on
# what a call names refuses them there. The mark is the environment's, which a session's own settings could also set
# for its own claude: there it would reach only an MCP server of that session's offering tools of these names
CONTROLLER_TOOLS = frozenset(OWN_TOOLS + t for t in ("plan_requirement", "start_task", "read_tree", "stop_task",
                                                     "continue_task", "ask_owner"))
_PART = re.compile(r"[^\s;&|<>()`'\"=:,@]+")
_OPTION = re.compile(r"^-{1,2}[A-Za-z0-9_]*(?=[/~.])")
MAX_PARTS = 2000


def _names_inside(texts, folders, home, work, also=("~/.claude",)):
    """Whether any of `texts` names a path in one of `folders` inside a word, where a whole word or a path field is not
    read (_task_refusal): a folder's path, as given or as it resolves, or one of `also` (`~/.claude` for the account
    folders), written anywhere in any of them (`dd if=<folder>/history.jsonl`, `open('<folder>/...')`), looked for in
    all of them first; or a part of one (between spaces, quotes, operators, `=`, `:`, `,` and `@`, an option's letters
    before it taken off: `-f../x`) that resolves into one. None where more than MAX_PARTS different parts could be
    paths: they are not all read, and the call is refused as one the gate cannot read (deny by default)."""
    spelled = [re.escape(f) for f in folders] + [re.escape(a) for a in also]
    if not spelled:
        return False
    named = re.compile(r"(?<![\w.-])(?:" + "|".join(spelled) + r")(?![\w.-])")
    texts = [t for t in texts if isinstance(t, str)]
    if any(named.search(text) for text in texts):
        return True
    resolved = set()
    for text in texts:
        for part in _PART.findall(text):
            for each in dict.fromkeys((part, _OPTION.sub("", part))):
                if not ("/" in each or each.startswith("~")) or len(each) > 4096 or "\x00" in each or \
                        each in resolved:
                    continue
                resolved.add(each)
                if len(resolved) > MAX_PARTS:
                    return None
                if each.startswith("~"):
                    each = os.path.join(home, each[2:]) if each == "~" or each.startswith("~/") else each
                path = os.path.join(work, each)
                if any(_inside(p, f) for p in (os.path.normpath(path), os.path.realpath(path)) for f in folders):
                    return True
    return False


def _task_refusal(payload, tool, tool_input, commands, env, home, work):
    """Why a task's attempt's call is refused, or None: any path in any account's folder, but what READERS read in
    its two readable places, matched on the resolved path (refusal has refused a frozen attempt's every other call
    first); and any path where the workspace's own git and the owner's chats run (git_folders), read the same ways."""
    folders = account_folders(env, home)
    gits = git_folders(env)
    readable = _readable(payload, env, home)
    named, words = _task_paths(tool, tool_input, commands, env)
    for text, from_field in [(t, True) for t in named] + [(w, False) for w in words]:
        if not text or "\x00" in text or len(text) > 4096:
            continue
        if text.startswith("~"):
            text = os.path.join(home, text[2:]) if text == "~" or text.startswith("~/") else text
        whole = os.path.normpath(os.path.join(work, text))
        prefix = _GLOB_CHARS.split(whole, 1)[0] if from_field and tool in ("Glob", "Grep") else whole
        forms = (os.path.normpath(prefix), os.path.realpath(prefix))
        if any(_inside(p, f) for p in forms for f in folders):
            if from_field and tool in READERS and any(_inside(forms[1], place) for place in readable) and \
                    not any(part == ".." for part in re.split(r"[/\\]", text)):
                continue
            return ACCOUNT_FOLDER
        if any(_inside(p, f) for p in forms for f in gits):
            return AGENT_REPOS
    # a path inside a word: in each command, as written and as the shell reads it, and in any string an MCP server's
    # tool is given (whose keys this does not know); not in a file's content, which names no path the call opens
    inside = list(commands) + [shell_line(shell_words(c, env)) for c in commands]
    if isinstance(tool, str) and tool.startswith("mcp__") and not tool.startswith(OWN_TOOLS):
        inside += list(_strings(tool_input))
    for spaces, why, also in ((folders, ACCOUNT_FOLDER, ("~/.claude",)), (gits, AGENT_REPOS, ())):
        found = _names_inside(inside, spaces, home, work, also)
        if found is None:
            return UNREADABLE
        if found:
            return why
    return None


def refusal(payload, home=None, env=None, hosted=None):
    """Why one tool call is refused, or None to let it go on: the rules, for Claude Code's call and Gemini CLI's,
    which both name the tool and give its input as an object. `env` is the environment of the program that made the
    call (this process's own, for a hook), whose variables a command's words are read with. `hosted`, whether this is
    a workspace machine whose sessions are given an approval hook's token, is read from the machine
    (ADE_FOLDER) when not given."""
    try:
        home = home or os.path.expanduser("~")
        env = os.environ if env is None else env
        hosted = os.path.isdir(ADE_FOLDER) if hosted is None else hosted
        tool, tool_input = _call(payload)
        if not isinstance(tool_input, dict):
            return UNREADABLE
        task = task_attempt(env)
        if task and env.get("PP_FROZEN") == "1" and tool not in FROZEN_TOOLS:
            return FROZEN
        texts = list(_strings(tool_input))
        names = [] if isinstance(tool, str) and tool in CONTROLLER_TOOLS and env.get("PP_CONTROLLER_CHAT") == "1" \
            else texts
        if any(_NAMED.search(t) or _is_credential_path(t, home) for t in names):
            return SIGN_IN
        if hosted and any(_ADE_NAMED.search(t) for t in names):
            return ADE_TOKEN
        if any(_GATE_NAMED.search(t) for t in names):
            return GATE_OFF
        if any(_PLACES_NAMED.search(t) for t in names):
            return PLACES_RECORD
        if any(_CONTROLLER_NAMED.search(t) for t in names):
            return CONTROLLER_RECORD
        if any(_SKILLS_NAMED.search(t) for t in names):
            return SKILLS_RECORD
        if any(_CHECKS_NAMED.search(t) for t in names):
            return CHECKS_FOLDER
        commands = []
        if tool in SHELLS:
            command = tool_input.get("command") or ""
            if isinstance(command, list):        # a command given as its words
                command = " ".join(str(c) for c in command)
            if not isinstance(command, str):
                return UNREADABLE
            commands.append(command)
        for key in COMMAND_FIELDS:               # a command any other tool runs or types: the same rules
            given = tool_input.get(key)
            if isinstance(given, list):
                given = " ".join(str(c) for c in given)
            if isinstance(given, str) and given not in commands:
                commands.append(given)
        # Grok Build 1.0.62 gives a cut input as the object with each long string cut, ending in a tag and
        # " [truncated]" (1.0.46 gave it as one cut string, refused above as unreadable): the rules above read every
        # string the call carries, a file's text too, so what was cut off could name what they refuse
        if payload.get("toolInputTruncated") is True or payload.get("tool_input_truncated") is True:
            return CUT_SHORT if commands else CUT_SHORT_CALL
        # each command as it was written and as the shell reads its words: a quote inside a word, a backslash or a
        # variable spells the same program and the same path
        words = [shell_words(c, env) for c in commands]
        read = commands + [w for w in (shell_line(ws) for ws in words) if w not in commands]
        if any(_is_credential_path(w, home) for ws in words for w in ws):
            return SIGN_IN
        for command in read:
            if _NAMED.search(command):
                return SIGN_IN
            if hosted and _ADE_NAMED.search(command):
                return ADE_TOKEN
            if _GATE_NAMED.search(command):
                return GATE_OFF
            if _PLACES_NAMED.search(command):
                return PLACES_RECORD
            if _CONTROLLER_NAMED.search(command):
                return CONTROLLER_RECORD
            if _SKILLS_NAMED.search(command):
                return SKILLS_RECORD
            if _CHECKS_NAMED.search(command):
                return CHECKS_FOLDER
            if _KEYCHAIN.search(command):
                return SIGN_IN
            if _UNGATED.search(command):
                return GATE_OFF
            for push in _PUSH.finditer(command):
                if _FORCE.search(push.group("rest")):
                    return "a force push rewrites a branch other sessions may rely on; push without forcing."
        if env.get("PP_REPLAY") == "1" and \
                (acts_on_the_world(tool, tool_input) or any(replay_refuses(c) for c in read)):
            return REPLAY
        run = agent_run(payload, env)
        if run is not None:
            why = _agent_refusal(tool, tool_input, texts, read, run, home)
            if why is not None:
                return why
        if task:
            work = run["work"] if run is not None else os.path.normpath(payload.get("cwd") or home)
            return _task_refusal(payload, tool, tool_input, commands, env, home, work)
        return None
    except Exception:  # noqa: BLE001  a gate that cannot read the call does not let it through
        return UNREADABLE


def decide(payload, home=None):
    """Claude Code's deny answer for one PreToolUse call, or None to let it go on."""
    why = refusal(payload, home)
    return None if why is None else _deny(why)


def gemini_answer(why):
    """Gemini CLI's answer to a BeforeTool call: a deny with its reason, which Gemini gives the model as the tool's
    error, or an empty object to let it go on (Gemini reads stdout as JSON)."""
    return {} if why is None else {"decision": "deny", "reason": "Refused by the workspace's gate: " + why}


def main(argv=None):
    gemini = "--gemini" in (sys.argv[1:] if argv is None else argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = None
    why = refusal(payload) if isinstance(payload, dict) else UNREADABLE
    out = gemini_answer(why) if gemini else (None if why is None else _deny(why))
    if out is not None:
        sys.stdout.write(json.dumps(out))
    if why is not None:
        record(payload, "Refused by the workspace's gate: " + why)
    return 0


def session_dir(payload):
    """The session's own folder: the one the workspace named (PP_SESSION_DIR), or, when the hook's environment was
    redacted, the folder whose worktree the call came from (<session>/work); None when neither is there."""
    folder = os.environ.get("PP_SESSION_DIR", "")
    if folder and os.path.isdir(folder):
        return folder
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if isinstance(cwd, str) and os.path.basename(os.path.normpath(cwd)) == "work":
        parent = os.path.dirname(os.path.normpath(cwd))
        if os.path.isfile(os.path.join(parent, "session.json")):
            return parent
    return None


def record(payload, reason):
    """Note a refusal in the session's own folder (gate.jsonl), for the page's activity: the tool and the reason, not
    what the call said. Best effort, after the answer is written, so a folder that cannot be written changes nothing
    about the refusal. The session runs as the user that can write this file, so it is the session's record."""
    folder = session_dir(payload)
    if not folder:
        return
    try:
        tool = _call(payload)[0] if isinstance(payload, dict) else None
        line = json.dumps({"at": int(time.time()), "tool": tool if isinstance(tool, str) else None,
                           "reason": reason if isinstance(reason, str) else ""})
        with open(os.path.join(folder, "gate.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line[:2000] + "\n")
    except Exception:  # noqa: BLE001  the refusal stands whether or not it was written down
        pass


if __name__ == "__main__":
    sys.exit(main())
