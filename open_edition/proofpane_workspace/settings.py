"""The Claude Code settings every `claude` session in a workspace runs with: the first gates (gate.py), one
PreToolUse hook on every tool; the status line that records the session's quota (statusline.py); and the hooks that
say what the session is doing for the page's list (activity.py: working, waiting, idle).

Each hook is given as a program and its arguments (`command`, the interpreter, and `args`), never as a command line.
Claude Code 2.1.274 runs a hook given as a line through a shell, wrapped in CLAUDE_CODE_SHELL_PREFIX when that is set,
and a settings file it loads for a session (a repository's .claude/settings.json, a worktree's settings.local.json,
the user's ~/.claude/settings.json) may set it in its `env`, to a program that does not run its argument: the gate
would then not run, and its refusals stop with nothing to show it. A hook given with `args` it starts directly, with no
shell and no prefix. Claude Code's settings declare `args` from 2.1.139, the first release whose do; what an older one
does with a hook given so was not measured, and from that reading of its settings alone it is taken not to run the
gate. The image's claude is 2.1.274 and its updater takes no older build; under the local
provider, the claude is the one on the PATH, whatever its version. The interpreter is given by its full path (the
image's /usr/bin/python3, the local provider's own sys.executable), since a settings file's `env` may set PATH too.
The status line is not a hook and has no such form (no `args` in its settings, in 2.1.274 or 2.1.287): it stays a
line, which a prefix set so would stop, and with it only the quota it records.

They are made here, by one function, for the two places that hold them:

- In a workspace machine (the fly provider), the image's managed settings, /etc/claude-code/managed-settings.json,
  owned by root. Claude Code applies managed settings "above every other level, so no user, project, local, or
  `--settings` value overrides them", and "hooks configured in managed settings still run unless `disableAllHooks`
  is also set there" (https://code.claude.com/docs/en/managed-settings, https://code.claude.com/docs/en/hooks-guide,
  read 2026-09-28). The program is installed by root and cannot update itself, so the managed settings also turn its
  own updater off; a new version comes with a rebuilt image, or from the workspace's updater, which installs a build
  on the volume and puts it in use once it passes a check (claude_update.py). Their `env` also sets the loader
  variables empty (LOADER_ENV), and bare mode off (SIMPLE_ENV, below).
- Under the local provider, the `--settings` file the agent starts `claude` with. That level sits above the user's and
  the project's settings but, unlike managed settings, a `disableAllHooks` from below turns its hooks off, since
  Claude Code "reads the value left after settings precedence applies". So this file sets `disableAllHooks` to false:
  a repository's own .claude/settings.json cannot switch the gate off. Its `env` sets the loader variables empty, as
  the managed settings' does (LOADER_ENV), and bare mode off (SIMPLE_ENV). A session there runs as the same user as
  the agent and can still edit this file (gate.py says what the gate is not).

A claude terminal is also given, at each of its starts, the control window's map sources (map_settings: the sub-agent
status line, and the activity hook on each tool call and on each sub-agent's start and end), in a
settings file of the session's own that holds whichever of the two above it is given, and these beside it
(workspace._with_map), only where the version that start read takes each. Neither place above carries them: each is
written once, for whatever claude a session later runs. The same start gives the skills hook (skills_settings:
activity.py --skills on a `Skill` call that ran or failed and on a typed command's expansion, each in
the background), and each of the owner's own chats is given it too (mcp.with_hooks, once its start has read the
version: workspace._chat_start), not the controller's chat or an agent's run.

In a hosted workspace the same two places give each claude terminal and each of the owner's
own chats the approval hook (ade_hook_settings: open_edition/ade_hook.py on Bash, with this deployment's address and
the workspace's hook token from ADE_HOOK_FOLDER), so their gated commands wait for this deployment's approval queue; and
unlike the map's sources and the skills hook, a start that cannot be given it is refused (with_held, holds), never
made without it. Neither place above carries it, since it is not for an agent's run, a replay or a task's attempt.

Both set CLAUDE_CODE_SIMPLE to "0" (SIMPLE_ENV). At "1" it is Claude Code's bare mode, as `--bare` is: no
hook a settings file gives runs then, the gate and the activity hooks among them, and no SDK callback either (read in
2.1.274's code: its hook table is empty in bare mode). A project's or a local settings file's `env` cannot set it
(2.1.274 drops it from their `env`, read), but the user's own ~/.claude/settings.json can, which a chat or a terminal
session may write (the gate refuses a `.claude` write only in an agent's run), and so can a `--settings` file. A
value at a level above the user's outranks it: the managed settings' `env` is applied after every other level's
(read), and a `--settings` file's sits above the user's, the reasoning a task's attempt rests on for its own
(task_attempt.ATTEMPT_ENV). That reasoning was read in 2.1.274's code, the image's own build. Whether "0"
there undoes a user's "1" in a real build is shown only by the image's Docker check
(tests/test_workspace_image_docker.py), run on a Mac: on the image's 2.1.274, and on
the stable 2.1.287 where the check is given that build; a stand-in that reads settings in 2.1.274's order shows it for
an agent's own run (tests/test_workspace_settings.py). An agent's own run and a chat run the build the workspace's
updater keeps once it has installed one, and the local provider the claude of the owner's own computer: the check
measures neither a later build the updater installs nor that claude, whatever its build. Where an organisation's
server-managed settings replace the image's managed settings (a Team or Enterprise seat), the managed pin is not
there at all: in the image
nothing then keeps a user's "1" from turning bare mode on, on a seat whose agents' runs and chats already run without
the image's gate (the manual says so); under the local provider the --settings file's pin still does. Neither pin
reaches `claude --bare` on a command line, which a session's own command may run: a session's own programs are not
held against (the manual says so). A hosted workspace's claude terminals and the owner's chats carry both pins all the
same, in the `env` of the approval hook's settings they are given (ade_hook_settings), which a seat's
server-managed settings do not replace.

    python -m proofpane_workspace.settings --managed [--python P] [--dir D]    print the image's managed settings
    python -m proofpane_workspace.settings --out FILE [--python P] [--dir D]   write the local provider's file
    python -m proofpane_workspace.settings --gemini [--out FILE] ...            Gemini CLI's system settings
    python -m proofpane_workspace.settings --codex ...                          Codex's managed requirements
    python -m proofpane_workspace.settings --grok ...                           Grok Build's enforced requirements
    python -m proofpane_workspace.settings --qwen [--out FILE] ...              Qwen Code's system settings

The managed settings also keep Claude Code's mods out. A mod is a plugin whose code Claude Code runs in its own
process with the user's permissions (https://code.claude.com/docs/en/plugins/mods/overview, read 2026-10-12); deny
rules and managed hooks, the gate among them, apply to tool calls, not to a mod's own file and process calls
(https://code.claude.com/docs/en/plugins/mods/admin, read 2026-10-12). Only an organisation's own mods should run in
a workspace machine, and the workspace has none, so its managed settings refuse the code of every other one: a mod a
user installs, one a repository's settings enable, one named with --plugin-dir, one Claude writes during a session.
The rest of such a plugin (its skills, commands, agents, MCP servers and settings hooks) still loads. Two settings do
it, one for each kind of build, and they are not equally strong (below). From 2.1.286 the built-in guard, which Claude
Code loads ahead of every user's mod on a machine with managed settings, reads `allowManagedModsOnly` from the managed
settings' `pluginConfigs` under `cc-plugin-sec-default@builtin` and refuses those mods when it is true. Builds 2.1.274
to 2.1.285 (2.1.279 is not on npm) have no such option: a mod loads only when CLAUDE_CODE_ENABLE_FUNCTION_HOOKS is true
or, unset, when a feature flag Claude Code fetches for the account says so, so the managed `env` sets the variable to
"0". 2.1.286 reads both; 2.1.287 ignores the variable. Each of these was seen in that build's code, not measured. The
variable is the weaker of the two: it does not hold in every launch the guard's option holds in, so the stronger setting
needs a claude of 2.1.286 or later, which the workspace's own updater installs when its mode is automatic, the default,
and puts in use only once the build passes its check (claude_update.py; the stable build was 2.1.287 on 2026-10-12). In
2.1.274 the variable also keeps the mods built into Claude Code off; from 2.1.277 they load whatever it says, each under
a switch of its own. Like the gate beside them, neither setting applies on a seat whose organisation has server-managed
settings, which are then the managed tier in place of this file. The local provider's file carries neither: the guard
reads its options from managed settings only, the variable would reach only builds before 2.1.287, and a gate in a
--settings file does not hold against a mod in any case. A person who wants users' mods refused on their own machine
sets the guard's option in that machine's managed settings.

Gemini CLI takes the same gate and activity hooks (gemini_settings), from its system settings: in a workspace machine
/etc/gemini-cli/settings.json, owned by root, which "act as overrides, taking precedence over all other settings
files" (Gemini CLI 0.62.0's configuration reference). Two limits read from Gemini 0.62.0's own code bound where this
reaches it: Gemini loads its system-settings hooks only in
a folder it trusts (an untrusted folder exits headless, or runs with no settings hooks), and loads a system-settings
file only when it is root-owned (loadSystemFile's uid-0 check). So under the fly provider (root's
/etc/gemini-cli/settings.json) the gate reaches a trusted Gemini session; under the local provider the file the agent
would name with GEMINI_CLI_SYSTEM_SETTINGS_PATH is the user's, which Gemini ignores, so Gemini there runs with no gate
(best-effort by design, as the whole gate is under the local provider: the same user runs the agent and the session).
They keep hooks on (`hooksConfig.enabled`, which a user's `false` does not
override). They cannot keep a hook from being left out: Gemini CLI 0.62.0 unites every file's `hooksConfig.disabled`,
so the user's settings, or a trusted repository's, naming the gate switch it off (measured). So
the workspace does not start gemini while a settings file it reads names the gate (workspace.gemini_gate_switched_off),
and the gate refuses a call that names it. They also name the loader variables under `advanced.excludedEnvVars`
(gemini_settings), so a trusted folder's project `.env` cannot hand the gate's interpreter an `LD_PRELOAD` directly;
that key's merge strategy is union, so a repository can only add to the list, never take a name out. The list does not
reach a `.gemini/.env`, so each hook's own `env` also sets the loader variables empty. Neither reaches what a shell
startup file the hook's `bash -c` reads could re-set, which the gate does not close (it is not a sandbox).

Codex takes them too (codex_requirements), as managed hooks in its requirements file, /etc/codex/requirements.toml in
a workspace machine, owned by root: hooks from it are "marked as managed, trusted by policy, and can't be disabled
from the user hook browser", and a user's or a repository's hooks run beside them, never in their place. That alone
does not keep them on: "Hooks are enabled by default. To turn them off in config.toml, set: [features] hooks =
false", which turns managed hooks off too, from the user's config, a flag, or a repository's own .codex/config.toml
in a project no one trusted (measured with Codex 0.163.0 by `codex exec`, each of the three alone, a flag being
`-c features.hooks=false` or `--disable hooks`; interactive, in a start with no flags, whose turns run in the background
server codex starts, the user's config alone did it too, and a project's own config waits there until its folder is
trusted, read). So the file also pins hooks on, as Codex's hooks page says to: "To enforce managed hooks even for users
who disabled hooks locally, pin [features].hooks = true in requirements.toml alongside [hooks]" (read 2026-10-02; Codex
gives the gate the call as Claude Code does, `Bash` with its command, and takes Claude Code's deny answer).
Grok Build takes them as enforced hooks (grok_requirements), from root's
/etc/grok/requirements.toml: hooks there "carry a [policy] badge", cannot be disabled, and their source cannot be
removed (Grok Build's own hooks guide, 1.0.46's and 1.0.62's); it gives the call with camelCase names and takes Claude
Code's deny answer, ranking it above its own. Under the local provider Codex and Grok Build run with the user's own
configuration, without these.

Qwen Code takes them as Claude Code does (qwen_settings), from its system settings: /etc/qwen-code/settings.json in a
workspace machine, owned by root, which "override user and project settings" (Qwen Code 0.24.7's configuration
reference); under the local provider a file the agent names to it with QWEN_CODE_SYSTEM_SETTINGS_PATH. Its hooks are
Claude Code's (a PreToolUse call with `tool_name` and `tool_input`, the same deny answer), and its `disableAllHooks`
turns every hook off, the system's too, from whichever settings say it last: so these say `false`, over a user's or a
repository's `true` (measured with Qwen Code 0.24.7: without it, a repository's .qwen/settings.json turned the gate
off; with it, the repository's own hook ran and so did the gate).

The app runs this as a process, as it runs the agent, and never imports it.
"""

import argparse
import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_TIMEOUT = 10
ACTIVITY_TIMEOUT = 5
# Every script a program runs for the workspace starts Python with -E, -s and -X utf8: no PYTHON* variable, which a
# repository's own settings may give a program's hooks, and no site-packages of the user's, where one file written from
# a session (a usercustomize.py, or a .pth file) would run inside every later hook, of every session and every program,
# and could end the gate before it answers (measured in the image, Python 3.13, 2026-10-02). -X utf8 is Python's UTF-8
# mode, in which the interpreter's start takes no character set from the locale: a settings
# file's `env` may name the locale (LC_ALL, LC_CTYPE, LANG), and under one whose character set Python has no codec for
# (hy_AM.ARMSCII-8, built with localedef) Python 3.13 ends before any of a hook's code runs, with "Fatal Python error:
# init_fs_encoding" and exit 1, which holds nothing; with -X utf8 the same hook starts and answers (measured on Linux,
# with glibc 2.39). PYTHONUTF8 would do the same, but -E makes Python ignore it. providers.helper_command starts Claude
# Code's key helper the same way.
ISOLATED = ("-E", "-s", "-X", "utf8")
# The loader variables, each of which can make a program load a library before its own code runs (glibc's LD_*, and
# macOS's dyld's DYLD_*): a settings file Claude Code loads for a session may set them in its `env`, and a hook started
# directly, as the gate is, gets them, so a library the repository carries could end the gate's interpreter before it
# answers. On a Mac that is new with the exec form: the line form ran a hook under /bin/sh, which the system's
# protection starts without DYLD_* variables. Both files here set each one empty, at the level that outranks a
# repository's settings (the managed settings, and the local provider's --settings); that an empty value there holds
# against a project's is measured by the image's Docker check, a test for each level, run before the release. Gemini
# CLI's system settings set them empty in each hook's own `env` (gemini_settings). The workspace passes none of them on
# to what it starts (workspace.PASS_ENV), so empty is what a session had already.
# glibc's GCONV_PATH and LOCPATH belong here too: they name where glibc loads
# its character-conversion modules, which are libraries, and its locale files from, and a program converts text in its
# locale's character set before its own code runs, so a repository's own pair would load a library of its own into
# the hook's interpreter the same way. Empty, glibc reads its own places alone. The locale's names themselves (LANG,
# LC_ALL and the others) stay the person's: setting them empty would change every command a session runs. What they
# could do to a hook is kept from it otherwise: the hooks read their input as UTF-8 bytes whatever the locale says
# (ade_hook.main, ade_link._hook_input), and each hook's interpreter starts with -X utf8 (ISOLATED here, and the owner's
# blocks), since a locale whose character set Python has no codec for ends the interpreter's start before any of the
# hook's code runs (ISOLATED says what was measured).
LOADER_ENV = ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
              "DYLD_FRAMEWORK_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_FALLBACK_FRAMEWORK_PATH",
              "DYLD_VERSIONED_LIBRARY_PATH", "DYLD_VERSIONED_FRAMEWORK_PATH", "DYLD_IMAGE_SUFFIX", "GCONV_PATH",
              "LOCPATH")
# Gemini CLI's own defaults for the variables a project `.env` may not set (its DEFAULT_EXCLUDED_ENV_VARS, read in
# 0.62.0's bundle). The workspace's Gemini system settings list these together with LOADER_ENV under
# advanced.excludedEnvVars, so a trusted folder's project `.env` cannot hand the gate's interpreter a loader variable
# (gemini_settings says why this reaches only a project `.env`, and what covers a `.gemini/.env`).
GEMINI_DEFAULT_EXCLUDED_ENV_VARS = ("DEBUG", "DEBUG_MODE", "GEMINI_CLI_IDE_SERVER_STDIO_COMMAND",
                                    "GEMINI_CLI_IDE_SERVER_STDIO_ARGS")
# The variables whose values decide whether a hook runs at all and how it is started: CLAUDE_CODE_SHELL_PREFIX wraps a
# hook given as a command line in a program of its own, and CLAUDE_CODE_SIMPLE at 1 is bare mode, which runs no hook
# from a settings file. A task's attempt sets them so (task_attempt.ATTEMPT_ENV is built from this), and so do the
# owner's blocks for their own sessions (ade_link.settings.json, ade_hook.settings.json and the two in one,
# ade_link_and_hook.settings.json), whose `env` a test holds to this and LOADER_ENV.
HOOK_ENV = {"CLAUDE_CODE_SHELL_PREFIX": "", "CLAUDE_CODE_SIMPLE": "0"}
# Bare mode off, in the `env` of every claude session's settings the workspace makes (claude_settings): the managed
# settings of a workspace machine and the local provider's --settings file (the module's docstring says why). The
# value is HOOK_ENV's, which a task's attempt pins too.
SIMPLE_ENV = {"CLAUDE_CODE_SIMPLE": HOOK_ENV["CLAUDE_CODE_SIMPLE"]}
# OpenSSL's configuration file, which OpenSSL reads before any of a program's own code runs, whatever Python's -I says,
# and which can bound the protocol versions a handshake may use: the owner's blocks set it empty, which loads none, so
# that a repository's settings cannot keep the hooks' requests from the ADE (a stop hook that cannot reach the ADE,
# with no stop recorded, lets the call go). Only the owner's blocks set it; a task's attempt reaches nothing over TLS
# from a hook of its own.
OPENSSL_ENV = {"OPENSSL_CONF": ""}
# glibc's resolver reads RES_OPTIONS from the environment, and with `attempts:0` a name lookup sends no query and fails
# at once, so a repository's settings could keep the stop hook from the ADE the same way. Empty, it acts as unset: the
# same queries and the same answers (measured with
# glibc 2.39, against a DNS stand-in of the test's own; not measured on a Mac, whose resolver is not glibc's). So the
# owner's blocks set it empty, as OPENSSL_CONF. LOCALDOMAIN is not set here: empty, it is not unset, since it takes the
# place of resolv.conf's search list (measured: a short name the search list finds is then not found), so it would
# change every command a session runs; nor HOSTALIASES, which glibc reads only for a name with no dot (measured: a
# dotted name, as an ADE address is, was asked as itself under either, with resolv.conf at its default ndots of 1).
# Where resolv.conf's ndots is above the dots in the ADE's name, glibc asks the search list first, which LOCALDOMAIN
# replaces: with ndots:5, a domain LOCALDOMAIN named answered for the ADE's name (measured), so there a repository's
# settings could choose where the stop hook's request goes. The manual names both,
# with OPENSSL_ia32cap and OPENSSL_armcap, as not covered.
RESOLVER_ENV = {"RES_OPTIONS": ""}
# The built-in guard's id, the one under which Claude Code reads its options from 2.1.286 (the module's docstring)
MODS_GUARD = "cc-plugin-sec-default@builtin"
# The control window's map of a session's agents and their tool calls:
# the sub-agents Claude Code shows a `subagentStatusLine` command (subagentline.py), and the activity hook given each
# tool call and each sub-agent's start and end (activity.py --map). Each goes only to a claude whose version is known
# to take it (map_settings), from the first version Claude Code's changelog names it in (read 2026-10-17, by offset):
# its settings page (read the same day) says a value the schema rejects makes a user, project or local settings file
# a Settings Error, which can leave the whole file out, and says nothing of a --settings file, which under the local
# provider holds the gate. An unknown hook event name is only a Settings Warning there, that entry left out.
SUBAGENT_STATUS_LINE_FROM = (2, 1, 214)   # "Added reasoning effort to the `subagentStatusLine` payload": the first
#                                           naming it; the key is older, by how much the changelog does not say (the
#                                           statusline page gives its `effort` from 2.1.213, which the changelog does
#                                           not list), so the first naming is taken
SUBAGENT_HOOKS_FROM = (2, 0, 43)          # "Added the `SubagentStart` hook event"; SubagentStop is older (1.0.41), and
#                                           carries agent_id from 2.0.42
TOOL_CALLS_FROM = (2, 1, 69)              # "Added `agent_id` (for subagents) and `agent_type` ... to hook events", so
#                                           a sub-agent's own tool call is told from the session's
TOOL_FAILURES_FROM = (2, 1, 119)          # the first naming PostToolUseFailure ("PostToolUse and PostToolUseFailure
#                                           hook inputs now include duration_ms"), which a failed call fires in
#                                           PostToolUse's place
ASYNC_HOOKS_FROM = (2, 1, 23)             # the first naming a hook run in the background ("Fixed pending async hooks
#                                           not being cancelled ..."): an async hook "can't block or control Claude's
#                                           behavior" (hooks page), so the map's hooks, display alone, never hold a turn
#                                           and never block a sub-agent's stop, even if their script is gone (a missing
#                                           script ends Python 2, which a synchronous SubagentStop reads as "keep going")
# A session the owner's controller started is given two more things, each by the
# first version Claude Code's changelog names it in (read 2026-10-19), so that the controller sees it stop at its
# account's limit and is the one that continues it: activity.py --limit on StopFailure with the matcher rate_limit
# (a turn ended by an API error, here the limit), and on the Notification types quota_auto_resume_fired, _stale and
# _disabled; and `autoContinueAtUsageLimit` off, which from 2.1.234 has an interactive session on a claude.ai
# subscription wait at the limit and go on by itself at the reset ("Claude Code now continues your session
# automatically when a claude.ai usage limit resets; turn it off in /config"): the controller continues its sessions
# itself, oldest first and within its pacing, and a session Claude Code went on with (the owner chose the wait in its
# terminal: quota_auto_resume_fired) is never told to go on a second time (controller.py)
STOP_FAILURE_FROM = (2, 1, 78)            # "Added `StopFailure` hook event that fires when the turn ends due to an API
#                                           error (rate limit, auth failure, etc.)"; its matcher is an exact match of
#                                           letters, digits, _ and | (hooks page)
AUTO_CONTINUE_FROM = (2, 1, 234)          # the automatic wait at a usage limit, its setting and its three
#                                           quota_auto_resume_* notification types (hooks page: "require Claude Code
#                                           v2.1.234 or later")
QUOTA_KINDS = ("quota_auto_resume_fired", "quota_auto_resume_stale", "quota_auto_resume_disabled")
# The skills hook: activity.py --skills on a call of the `Skill` tool that ran
# (PostToolUse, and PostToolUseFailure for one that failed, each with the matcher `Skill`) and on the expansion of a
# command the person typed (UserPromptExpansion, no matcher: the names are the projects' own). Each is in the
# background, so that a missing script never holds a call, and above all never blocks a typed command: a hook on
# UserPromptExpansion that exits 2 blocks the expansion, and Python ends 2 for a script that is not there. A build with
# no background hook (below ASYNC_HOOKS_FROM) is given none of them. Each version below was read in the installed
# 2.1.304's code and in the published packages' (2026-10-20).
SKILL_CALLS_FROM = (2, 0, 35)             # a Skill call's input names the skill in `skill` (it was `command` before it)
SKILL_TYPED_FROM = (2, 1, 116)            # UserPromptExpansion: in the code from 2.1.116 (2.1.114, the build before it on
#                                           npm, has none), in no changelog line; PostToolUseFailure's is TOOL_FAILURES_FROM


def script_line(python, directory, script, *args):
    """The shell line that runs `script` of `directory` with the interpreter `python`, isolated as above."""
    return " ".join([shlex.quote(python), *ISOLATED, shlex.quote(os.path.join(directory, script)), *args])


def script_args(directory, script, *args):
    """The arguments an interpreter is started with to run `script` of `directory`, isolated as above."""
    return [*ISOLATED, os.path.join(directory, script), *args]


def claude_settings(python, directory, managed):
    """The settings, as a dict, running gate.py, activity.py and statusline.py from `directory` with the interpreter
    `python`, each hook as the interpreter and its arguments (the module's docstring says why), their `env` setting
    the loader variables empty and bare mode off; as managed settings they also refuse the code of every mod that is
    not the organisation's, as far as the claude reading them can (the module's docstring says which build reads which
    of the two settings, and that they are not equally strong)."""
    def hook(script, *args, timeout):
        return {"type": "command", "command": python, "args": script_args(directory, script, *args), "timeout": timeout}

    def activity(state):
        # activity.py: what the session is doing, for the page's list; it never blocks and ends 0
        return {"hooks": [hook("activity.py", state, timeout=ACTIVITY_TIMEOUT)]}

    out = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [hook("gate.py", timeout=HOOK_TIMEOUT)]}],
                     "UserPromptSubmit": [activity("working")],
                     "PostToolUse": [dict(activity("working"), matcher="*")],
                     "Notification": [activity("waiting")],
                     "Stop": [activity("idle")]},
           "statusLine": {"type": "command", "command": script_line(python, directory, "statusline.py")}}
    pins = {**SIMPLE_ENV, **{name: "" for name in LOADER_ENV}}
    if managed:
        # mods: the guard's option from 2.1.286, the early-access switch off for the builds before 2.1.287 (the
        # module's docstring)
        out["pluginConfigs"] = {MODS_GUARD: {"options": {"allowManagedModsOnly": True}}}
        out["env"] = {"DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "0", **pins}
    else:
        out["env"] = pins
        out["disableAllHooks"] = False
    return out


def map_settings(python, directory, version):
    """The control window's map sources (the constants above) that a claude of `version` (major, minor, patch) takes, as
    settings beside claude_settings': the sub-agent status line (subagentline.py, a command line as the status line is:
    it has no form with `args` either), and activity.py --map on each tool call, on each failed one and on each
    sub-agent's start and end, each hook as the interpreter and its arguments. Each runs in the background (async), so
    that no call waits on it, but a sub-agent's start: background hooks are not ordered, each stamping its own time, so
    a short sub-agent's stop could be written before its start and leave it running on the map; Claude Code waits for a
    SubagentStart hook before the sub-agent's first request (the context such a hook adds goes in "before its first
    prompt", and it cannot block the sub-agent: its exit 2 only shows stderr, hooks page, read 2026-10-18), so the start
    is written before anything the sub-agent does, its stop included, at the cost of one short process each time that
    hook fires, which is at a sub-agent's start, when one is resumed, and each time an agent team's teammate takes a new
    message (the same page). Claude Code times out only that one: it does not time
    out a background hook (the same page), so activity.py's --map bounds its own work. Above the gate's floor
    (workspace.CLAUDE_ARGS_FLOOR, 2.1.139) every part but the sub-agent status line is always given: their gates are
    kept for what a start below the floor never reaches. Nothing for a version not known: a part is given only where the
    version is read. The workspace writes the image's managed settings and the local provider's --settings file once,
    for every claude a session may later run, whose version can differ by session (a version manager's pin in a
    worktree, the workspace's updater, the person's own updates), so it gives these at each start of a claude terminal,
    by the version that start read (workspace._with_map)."""
    if not isinstance(version, tuple) or len(version) != 3 or not all(type(n) is int for n in version):
        return {}

    def mapping(background=True):
        hook = {"type": "command", "command": python, "args": script_args(directory, "activity.py", "--map"),
                "timeout": ACTIVITY_TIMEOUT}
        if background and version >= ASYNC_HOOKS_FROM:
            hook["async"] = True
        return {"hooks": [hook]}

    hooks = {}
    if version >= TOOL_CALLS_FROM:
        hooks["PostToolUse"] = [dict(mapping(), matcher="*")]
    if version >= TOOL_FAILURES_FROM:
        hooks["PostToolUseFailure"] = [dict(mapping(), matcher="*")]
    if version >= SUBAGENT_HOOKS_FROM:
        hooks["SubagentStart"] = [mapping(background=False)]
        hooks["SubagentStop"] = [mapping()]
    out = {"hooks": hooks} if hooks else {}
    if version >= SUBAGENT_STATUS_LINE_FROM:
        out["subagentStatusLine"] = {"type": "command", "command": script_line(python, directory, "subagentline.py")}
    return out


def controller_settings(python, directory, version):
    """What a session the owner's controller started is given beside the map's (the constants above), for a claude of
    `version`: activity.py --limit on a turn stopped at the account's limit (StopFailure, rate_limit) and on Claude
    Code's own automatic wait's notifications, each in the background; and its automatic continuing at the reset
    turned off. Nothing for a version not known."""
    if not isinstance(version, tuple) or len(version) != 3 or not all(type(n) is int for n in version):
        return {}

    def limit(matcher):
        hook = {"type": "command", "command": python, "args": script_args(directory, "activity.py", "--limit"),
                "timeout": ACTIVITY_TIMEOUT}
        if version >= ASYNC_HOOKS_FROM:
            hook["async"] = True
        return {"matcher": matcher, "hooks": [hook]}

    hooks, out = {}, {}
    if version >= STOP_FAILURE_FROM:
        hooks["StopFailure"] = [limit("rate_limit")]
    if version >= AUTO_CONTINUE_FROM:
        hooks["Notification"] = [limit(kind) for kind in QUOTA_KINDS]
        out["autoContinueAtUsageLimit"] = False
    if hooks:
        out["hooks"] = hooks
    return out


def skills_settings(python, directory, version):
    """The skills hook (the constants above) that a claude of `version` takes, as settings beside map_settings': the hook
    activity.py --skills on a `Skill` call that ran, on one that failed, and on a typed command's expansion, each as the
    interpreter and its arguments and in the background (async), each from the build that has its event and no earlier
    than a build with background hooks. Nothing for a version not known."""
    if not isinstance(version, tuple) or len(version) != 3 or not all(type(n) is int for n in version):
        return {}
    if version < ASYNC_HOOKS_FROM:
        return {}

    def skills(matcher=None):
        hook = {"type": "command", "command": python, "args": script_args(directory, "activity.py", "--skills"),
                "timeout": ACTIVITY_TIMEOUT, "async": True}
        return dict({"matcher": matcher} if matcher else {}, hooks=[hook])

    hooks = {}
    if version >= SKILL_CALLS_FROM:
        hooks["PostToolUse"] = [skills("Skill")]
        if version >= TOOL_FAILURES_FROM:
            hooks["PostToolUseFailure"] = [skills("Skill")]
    if version >= SKILL_TYPED_FROM:
        hooks["UserPromptExpansion"] = [skills()]
    return {"hooks": hooks} if hooks else {}


def with_map(given, python, directory, version, controller=False, skills=False):
    """Settings `given` (a dict: a --settings file's, or none) with map_settings' for `version` added, and, for a
    session the owner's controller started (`controller`), controller_settings', and, for a claude terminal (`skills`:
    workspace._with_map), skills_settings' too: each of their hooks after the ones `given` has for its event, the
    sub-agent status line, and the controller's setting. None when `given` is not settings this can add to (a `hooks`
    that is not an object, or an event's that is not a list), so that a start never gives a file that holds less than
    `given` did."""
    if not isinstance(given, dict):
        return None
    out = json.loads(json.dumps(given))
    extra = map_settings(python, directory, version)
    hooks = out.get("hooks", {})
    if not isinstance(hooks, dict):
        return None
    more = controller_settings(python, directory, version) if controller else {}
    skill = skills_settings(python, directory, version) if skills else {}
    for event, groups in [*extra.get("hooks", {}).items(), *more.get("hooks", {}).items(),
                          *skill.get("hooks", {}).items()]:
        if not isinstance(hooks.get(event, []), list):
            return None
        hooks[event] = list(hooks.get(event, [])) + groups
    if hooks:
        out["hooks"] = hooks
    if "subagentStatusLine" in extra:
        out["subagentStatusLine"] = extra["subagentStatusLine"]
    if "autoContinueAtUsageLimit" in more:
        out["autoContinueAtUsageLimit"] = more["autoContinueAtUsageLimit"]
    return out


# A hosted workspace's claude terminals and chats hold their gated Bash commands for this deployment's approval queue:
# open_edition/ade_hook.py, which the image keeps as root's beside the agent's package
# (workspace_image/Dockerfile), with this deployment's address and the workspace's hook token from the folder start.sh
# puts them in (the fly provider writes them as two boot files). Its timeout is the owner's block's
# (ade_hook.settings.json): the hook's deadline of 75 seconds fits inside it, 15 to spare.
ADE_HOOK_FOLDER = "/run/proofpane/ade"
ADE_URL_FILE = "ade-url"
ADE_TOKEN_FILE = "ade-hook-token"
ADE_HOOK_TIMEOUT = 90
# The program that starts it, beside this file (root's in the image): a program of its own, so that no other hook's
# command is the hold's own (Claude Code runs a handler defined the same in several settings files once, and whether
# one started by the same program with other arguments counts as the same was not measured: the manual). It starts
# the hook by `env -i` with the address and the token's file alone, and holds the
# call (exit 2) where /usr/bin/env, the interpreter or the hook is not there, since a hook that cannot start lets it go.
ADE_HOOK_START = "ade_hook_start"
# The addresses the hook takes (ade_hook.URL, which a test holds this to), for the agent to say whether the address its
# machine was given is one (Workspace.held)
ADE_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::\d+)?|http://(?:127\.0\.0\.1|localhost):\d+")


def ade_start_check(start):
    """The command line beside the approval hook, in the owner's block's form (ade_hook.settings.json): it only checks
    that `start` is an executable file, and holds every Bash command (exit 2, which Claude Code takes as a denial) when
    it is not, since a hook that cannot start is no decision. A line of its own, the path in it, so that it is no other
    hook's either."""
    return (f"s={shlex.quote(start)}; [ -f \"$s\" ] && [ -x \"$s\" ] && exit 0; echo \"the approval hook cannot start: "
            f"$s is not an executable file, so every Bash command is held.\" >&2; exit 2")


def ade_hook_settings(start, python, hook, folder, url):
    """The settings that make a hosted workspace's claude terminal or chat hold its gated Bash commands for this
    deployment's approval queue: `hook` (ade_hook.py) as a PreToolUse hook on Bash, started by `start`
    (ADE_HOOK_START) with `python`, the deployment's address `url` and the file of the workspace's hook token in
    `folder`, which it gives the hook by `env -i` and nothing else, so that nothing of the session's environment
    reaches it, which a repository's or a user's settings can set: no other token, address, pattern, proxy,
    certificate or wait, and no OpenSSL or resolver setting of theirs; beside it the start check (ade_start_check);
    their `env`, the pins the image's managed settings carry (HOOK_ENV, and LOADER_ENV empty), which a seat whose
    organisation's server-managed settings replace those does not have, so that a user's or a repository's settings
    can neither turn bare mode on, where no settings hook runs, nor load a library into the start; and
    disableAllHooks false. A --settings value outranks a repository's and a user's in each. The token itself
    is never in them: its file's path alone."""
    token = os.path.join(folder, ADE_TOKEN_FILE)
    run = {"type": "command", "command": start, "args": [python, hook, url, token], "timeout": ADE_HOOK_TIMEOUT}
    check = {"type": "command", "command": ade_start_check(start), "timeout": HOOK_TIMEOUT}
    return {"env": {**HOOK_ENV, **{n: "" for n in LOADER_ENV}},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [run, check]}]}, "disableAllHooks": False}


def with_held(given, held):
    """Settings `given` (a dict) with the approval hook's settings `held` (ade_hook_settings) added: its PreToolUse
    group after the ones `given` has, its `env` over `given`'s, and disableAllHooks false. None when `given` is not
    settings this can add to (a `hooks` or an `env` that is not an object, or a PreToolUse that is not a list): the
    start is then refused, never made without the hook, unlike with_map's."""
    if not isinstance(given, dict):
        return None
    out = json.loads(json.dumps(given))
    hooks, env = out.get("hooks", {}), out.get("env", {})
    if not isinstance(hooks, dict) or not isinstance(env, dict):
        return None
    for event, groups in held["hooks"].items():
        if not isinstance(hooks.get(event, []), list):
            return None
        hooks[event] = list(hooks.get(event, [])) + list(groups)
    out["hooks"] = hooks
    out["env"] = {**env, **held.get("env", {})}
    out["disableAllHooks"] = False
    return out


def holds(settings, held):
    """Whether `settings` (a dict) carry the approval hook as `held` gives it: each of its groups as it is, each of its
    `env` pins at its value, and disableAllHooks false."""
    if not isinstance(settings, dict) or settings.get("disableAllHooks") is not False:
        return False
    env = settings.get("env")
    if not isinstance(env, dict) or any(env.get(k) != v for k, v in held.get("env", {}).items()):
        return False
    hooks = settings.get("hooks")
    return isinstance(hooks, dict) and all(isinstance(hooks.get(event), list) and all(g in hooks[event] for g in groups)
                                           for event, groups in held["hooks"].items())


def gemini_settings(python, directory):
    """Gemini CLI's system settings, as a dict: gate.py before every tool, in Gemini's form (--gemini), and activity.py
    on a prompt (BeforeAgent), after a tool, on a Notification and when a turn ends (AfterAgent). Gemini's timeouts are
    in milliseconds; each hook has a name, which is what `hooksConfig.disabled` would list.

    `advanced.excludedEnvVars` names the loader variables (LOADER_ENV) besides Gemini's own defaults
    (GEMINI_DEFAULT_EXCLUDED_ENV_VARS; Gemini merges DEBUG and DEBUG_MODE in from its schema default anyway, so naming
    these adds the other two). In a trusted folder Gemini loads the folder's `.env` into its own process and
    hands its hooks that environment (Gemini CLI 0.62.0, read: loadEnvironment, and sanitizeEnvironment returns the
    whole process environment when redaction is off, the default); its default exclusion list does not name a loader
    variable, so a trusted folder's `.env` setting `LD_PRELOAD` would reach the gate's `python3 -E -s`, which the
    dynamic linker preloads before the gate runs. This list is applied to a `.env` whose path holds no `.gemini`
    (Gemini's `isProjectEnvFile`, a substring test), not to a `.gemini/.env`, which Gemini also loads in a trusted
    folder, the home folder's included (findEnvFile); the key's merge strategy is union, so a repository cannot take a
    name back out, and the system settings file, where this is set, is loaded above the user's and the repository's
    (and, under the fly provider, is root-owned, which Gemini requires of a system settings file).

    So each hook also carries an `env` that sets every loader variable empty: Gemini's hook runner starts a hook with
    the process environment and then the hook's own `env` over it (0.62.0, read, not measured; its settings schema
    passes a hook's `env` through). This replaces a loader variable a `.env` set directly in Gemini's process; it does
    NOT reach a value a shell startup file sets, since the hook runs through `bash -c` and bash reads a startup file
    (`~/.bashrc`, or one a `.env` points it at) before the gate's interpreter. That shell-startup surface the gate
    does not close (it is not a sandbox). The gate also refuses an
    agent's run reading or changing Gemini's trusted-folder and settings files, or writing in a `.gemini` folder,
    which stops it planting a `.gemini/.env` or trusting a folder (gate.py)."""
    def hook(name, script, *args, timeout):
        line = script_line(python, directory, script, *args, "--gemini")
        return {"matcher": "*", "hooks": [{"name": name, "type": "command", "command": line, "timeout": timeout * 1000,
                                           "env": {variable: "" for variable in LOADER_ENV}}]}

    return {"hooksConfig": {"enabled": True, "disabled": []},
            "advanced": {"excludedEnvVars": [*GEMINI_DEFAULT_EXCLUDED_ENV_VARS, *LOADER_ENV]},
            "hooks": {"BeforeTool": [hook("proofpane-gate", "gate.py", timeout=HOOK_TIMEOUT)],
                      "BeforeAgent": [hook("proofpane-working", "activity.py", "working", timeout=ACTIVITY_TIMEOUT)],
                      "AfterTool": [hook("proofpane-working", "activity.py", "working", timeout=ACTIVITY_TIMEOUT)],
                      "Notification": [hook("proofpane-waiting", "activity.py", "waiting", timeout=ACTIVITY_TIMEOUT)],
                      "AfterAgent": [hook("proofpane-idle", "activity.py", "idle", timeout=ACTIVITY_TIMEOUT)]}}


def _hooks_toml(header, python, directory, groups):
    """Hooks as TOML, in the form Codex's and Grok Build's requirements files share: one `[[hooks.<Event>]]` group a
    hook, with its matcher (None for none), running a script of `directory` with `python`."""
    def hook(event, matcher, script, args, timeout):
        line = script_line(python, directory, script, *args)
        out = [f"[[hooks.{event}]]"] + ([f"matcher = {json.dumps(matcher)}"] if matcher is not None else []) + [
            f"[[hooks.{event}.hooks]]", 'type = "command"', f"command = {json.dumps(line)}", f"timeout = {timeout}"]
        return "\n".join(out)
    return "\n\n".join([header] + [hook(*g) for g in groups]) + "\n"


CODEX_HOOKS_ON = ("\n# Hooks on, whatever a user's config, a flag or a repository's .codex/config.toml says: without\n"
                  "# this pin their [features] hooks = false turns the managed hooks above off too.\n"
                  "[features]\nhooks = true\n")


def codex_requirements(python, directory):
    """Codex's requirements file, as TOML text: gate.py before every tool (Codex gives the call, and takes the answer,
    in Claude Code's form), and activity.py on a prompt and after a tool (working), when it is about to ask the person
    (PermissionRequest: waiting), and when a turn ends (Stop: idle). activity.py prints nothing, which Codex would
    otherwise add to the model's context, and decides nothing, so Codex asks as it would. It ends with Codex's pin
    that keeps hooks on (CODEX_HOOKS_ON)."""
    return _hooks_toml(
        "# Proofpane: the workspace's gate and each session's activity, for every codex in this workspace. Managed\n"
        "# hooks, trusted by policy and pinned on (made by proofpane_workspace.settings --codex).",
        python, directory, [
            ("PreToolUse", "*", "gate.py", (), HOOK_TIMEOUT),
            ("UserPromptSubmit", None, "activity.py", ("working",), ACTIVITY_TIMEOUT),
            ("PostToolUse", "*", "activity.py", ("working",), ACTIVITY_TIMEOUT),
            ("PermissionRequest", "*", "activity.py", ("waiting",), ACTIVITY_TIMEOUT),
            ("Stop", None, "activity.py", ("idle",), ACTIVITY_TIMEOUT)]) + CODEX_HOOKS_ON


def grok_requirements(python, directory):
    """Grok Build's requirements file, as TOML text: gate.py before every tool (Grok Build gives the call with camelCase
    names, which gate.py reads, and takes Claude Code's answer), and activity.py on a prompt and after a tool (working),
    on a permission or an idle prompt (its Notification of those kinds: waiting), and when a turn ends (idle): Stop when
    it completes, StopFailure on an API error, StopCancelled when it is cut short (an interrupt, a declined permission,
    the turn limit), each of which Grok fires instead of the others. activity.py prints nothing and decides nothing."""
    return _hooks_toml(
        "# Proofpane: the workspace's gate and each session's activity, for every grok in this workspace. Enforced\n"
        "# hooks: root's, not to be disabled or removed (made by proofpane_workspace.settings --grok).",
        python, directory, [
            ("PreToolUse", "*", "gate.py", (), HOOK_TIMEOUT),
            ("UserPromptSubmit", None, "activity.py", ("working",), ACTIVITY_TIMEOUT),
            ("PostToolUse", "*", "activity.py", ("working",), ACTIVITY_TIMEOUT),
            ("Notification", "permission_prompt|idle_prompt", "activity.py", ("waiting",), ACTIVITY_TIMEOUT),
            ("Stop", None, "activity.py", ("idle",), ACTIVITY_TIMEOUT),
            ("StopFailure", None, "activity.py", ("idle",), ACTIVITY_TIMEOUT),
            ("StopCancelled", None, "activity.py", ("idle",), ACTIVITY_TIMEOUT)])


def qwen_settings(python, directory):
    """Qwen Code's system settings, as a dict: hooks on whatever another settings file says (`disableAllHooks`), gate.py
    before every tool, and activity.py on a prompt and after a tool (working), on a permission or an idle prompt (its
    Notification of those kinds: waiting), and when a turn ends (idle: Stop, or StopFailure when an API error or a
    loop ends it instead), in Claude Code's form, which is Qwen's; and no update of its own (a new version comes with a
    new image). Its command hooks' timeouts are in seconds."""
    def hook(name, script, *args, timeout):
        line = script_line(python, directory, script, *args)
        return {"name": name, "type": "command", "command": line, "timeout": timeout}

    return {"disableAllHooks": False,
            "general": {"enableAutoUpdate": False},
            "hooks": {"PreToolUse": [{"matcher": "*",
                                      "hooks": [hook("proofpane-gate", "gate.py", timeout=HOOK_TIMEOUT)]}],
                      "UserPromptSubmit": [{"hooks": [hook("proofpane-working", "activity.py", "working",
                                                           timeout=ACTIVITY_TIMEOUT)]}],
                      "PostToolUse": [{"matcher": "*", "hooks": [hook("proofpane-working", "activity.py", "working",
                                                                      timeout=ACTIVITY_TIMEOUT)]}],
                      "Notification": [{"matcher": "permission_prompt|idle_prompt",
                                        "hooks": [hook("proofpane-waiting", "activity.py", "waiting",
                                                       timeout=ACTIVITY_TIMEOUT)]}],
                      "Stop": [{"hooks": [hook("proofpane-idle", "activity.py", "idle", timeout=ACTIVITY_TIMEOUT)]}],
                      "StopFailure": [{"hooks": [hook("proofpane-idle", "activity.py", "idle",
                                                      timeout=ACTIVITY_TIMEOUT)]}]}}


def write(path, settings):
    """Write the settings to `path`, readable by this user only, replacing what was there in one step."""
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=1)
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofpane_workspace.settings")
    parser.add_argument("--managed", action="store_true", help="the image's managed settings")
    parser.add_argument("--gemini", action="store_true", help="Gemini CLI's system settings")
    parser.add_argument("--codex", action="store_true", help="Codex's managed requirements (TOML)")
    parser.add_argument("--grok", action="store_true", help="Grok Build's enforced requirements (TOML)")
    parser.add_argument("--qwen", action="store_true", help="Qwen Code's system settings")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dir", default=HERE)
    parser.add_argument("--out", help="write the settings to this file instead of printing them")
    args = parser.parse_args(argv)
    if args.codex or args.grok:
        sys.stdout.write((codex_requirements if args.codex else grok_requirements)(args.python, args.dir))
        return 0
    if args.gemini:
        settings = gemini_settings(args.python, args.dir)
    elif args.qwen:
        settings = qwen_settings(args.python, args.dir)
    else:
        settings = claude_settings(args.python, args.dir, args.managed)
    if args.out:
        write(args.out, settings)
    else:
        json.dump(settings, sys.stdout, indent=1)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
