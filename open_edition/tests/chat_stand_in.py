"""A stand-in for Claude Code in the stream-JSON mode the Claude Agent SDK drives, answering as the real one does (the
SDK 0.2.154's own parser and control protocol): the SDK's initialize request, then for each user message a system
init, the assistant's messages and a result, each message's stream events first when it is given
--include-partial-messages. "run <command>" asks permission for a Bash call (can_use_tool) and waits for the answer
(its settings' and its plugins' command hooks run first where a test made $HOME/stand-in-command-hooks, below);
"wait" (or "wait <seconds>", 30 by default) works until it is interrupted; "sleep <seconds>" works that long and
ends its turn well; "stream <words>" thinks, then writes the words a few at a time; "ask <question>"
calls the SDK's in-process server's ask_owner tool (an agent's run, agents.py) as the real one calls an SDK server, over
the control channel's mcp_message; "subagent <words>" hands the words to a sub-agent, which reports inside the turn when
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS is set and after the turn's result otherwise, as the real one's background
sub-agents do; "search <words>" and "propose <name>" call the run's search_mcp and propose_mcp tools as ask_owner is
called, and say what they gave; "agents", "dispatch <name>|<message>" (or "dispatch*<n> ..." for n of them),
"dispatchwait <name>|<message>|<seconds>", "dispatchsend <name>|<message>|<more>|<seconds>" (started, sent more once it
works, then waited for), "waitrun <run>|<seconds>" and "sendrun <run>|<message>" call the workspace's list_agents,
run_agent, wait_run and send_to_run (agents.py), allowed by --allowedTools as the others are, and say what the last gave;
"readnamed" calls Read on each path the message's second paragraph names (an eval's question), in order;
"readitems" reads the re-verify items its message names and says how many there are;
"fact <subject>|<relation>|<object>|<valid_at>|<source>", "lookup <subject>|<relation>" and "retract <id>|<reason>"
call the run's record_fact, lookup_fact and retract_fact; "seefact <seen>|..." first has a Bash call show <seen>, and
"payloadfact ..." first reads the hook's delivery the message names (with Read; "catpayloadfact" with a Bash cat),
then records the fact; "memory <op> ..." reads
("read <path>") or writes ("write <path>=<text>"; several, joined by "&") files of the agent's memory folder (the
autoMemoryDirectory of its --settings), each with a Read or a Write call;
"repeat <n> <command>" makes the same Bash call n times, and "subrepeat <n> <command>" has a sub-agent make it; a message that begins "Grade this run" (the judge's) is
answered with grades, high when its final message says GOOD and low on task_fit when it says BAD; one that begins
"Your output failed its acceptance check" with a JSON object that passes;
"ctool <name>|<arguments as JSON>" calls the owner's controller's tool <name> (proofpane_workspace/controller.py) as
ask_owner is called, allowed by --allowedTools as the others are, and says what it gave; an attempt of a requirement's
manager (its first message the workspace's brief, then its packet) calls,
in its one turn, each "manager: ctool <name>|<arguments>" line of its requirement's words, as the packet gives them,
works on at a "manager: wait" line for a minute or until interrupted, and says what each gave; "compact" writes a
compact_boundary system message, as the real one does when it compacts the conversation, and says so;
"fail" (or "fail <subtype>") ends its turn with an error result; "steps <n>" is a turn
of n steps, ended at --max-turns as the real one ends it; "ratelimit <status> <type>" sends a rate-limit event (with
the attempt's words after it, below: overage=<status>, resets=, using); anything
else is answered in words. Any of these after
"[command] " first leaves the command running in the background, as a Bash call's `&` would; after "later <seconds> ",
works that long first, in the same turn. Each reply is also
written to the conversation's transcript, as Claude Code writes it, on two lines with one message id, and a
sub-agent's to its own file. `auth status` answers as
signed in unless the test signs it out. Each start is written down (its arguments, its environment's names, where it
runs) for the tests to read, in $HOME/stand-in-runs.jsonl, and each change of its model, permission mode or an MCP
server in $HOME/stand-in-controls.jsonl.

Its MCP servers are the real one's as the image showed them (proofpane_workspace/mcp.py): the ones given on its command
line (an agent's run's, agents.py); every server of each .mcp.json in its folder and above it, whether approved or not,
less the ones `disabledMcpjsonServers` names in its `--settings`; and the servers `claude mcp add` gave the workspace
($HOME/.claude.json: its user scope, and its local scope under the repository's own folder), less the ones
`deniedMcpServers` names; only the given ones under `--strict-mcp-config`. `mcp add` and `mcp
remove` write and remove them, with the real one's words and exit codes. A server turned off or on (the SDK's
toggle) is kept, as the real one keeps it, for every chat on the repository (disabledMcpServers under its folder in
$HOME/.claude.json, seen with 2.1.274 and 2.1.277). A test may add STRAY above this file: one more server it reports
from the repository, which no .mcp.json declares.

Its account is the one a test writes in stand-in-account.json in the account's folder (CLAUDE_CONFIG_DIR, else
~/.claude), with that file's "restart" object laid over it in a process started with --resume, or the defaults: the
initialize answer's account (a key's, with no email, organisation or plan, when its --settings name an apiKeyHelper;
ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN in the session's environment, below, giving its apiKeySource and
tokenSource), the apiKeySource of each turn's system init (apiKeyHelper, else ANTHROPIC_API_KEY when that environment
has it, else none), and `auth status`'s email, plan word, orgId (a list of them giving one to each `auth status` in
turn, the last repeated) and orgName (with no such file `auth status` answers as above; with "auth_unreadable" it
answers words that are not JSON); a first_answer there is an
action (below) it does at the start of the process's first turn, which it then ends. It reads its settings as the real one does: the user,
project and local files --setting-sources names (all three when it is not given), its --settings, and a policy tier,
$HOME/stand-in-server-managed.json, else $HOME/stand-in-managed.json. Their env, laid over its own environment in that
order, the later winning, is the environment its Bash calls and command hooks run with, and each start writes down what
of it the tests ask about, with the sources read, bare mode (--bare, or CLAUDE_CODE_SIMPLE), the advisor, auto memory,
and whether it is a task's attempt and frozen. Where the SDK registered hook callbacks in its initialize request, it
calls them over the control channel as 2.1.274 does, an error answer taken as no decision: the UserPromptSubmit ones at
each turn's start, and for each client tool call, after the assistant message holding it (each assistant message then
has an id), every PreToolUse one whose matcher matches, in the order registered, even after one denied; a deny refuses
the call, which does not run and is listed in the turn's permission_denials, and an allow with no deny skips the
permission step; then its work and the PostToolUse ones, or
the PostToolUseFailure ones when it failed, then its result. In bare mode it calls none, and its calls just run. A test
that makes $HOME/stand-in-command-hooks also has the PreToolUse command hooks of its settings run after the callbacks
(none under the policy tier's disableAllHooks, the policy tier's alone under its allowManagedHooksOnly or where the
other sources' disableAllHooks, the later winning, is true; in exec form when a hook gives args, else behind
CLAUDE_CODE_SHELL_PREFIX or /bin/sh), each
written to $HOME/stand-in-hooks.jsonl, and after them those of each plugin a --plugin-dir names (its hooks/hooks.json,
${CLAUDE_PLUGIN_ROOT} put in as the plugin's folder and given to the hook in its environment, as the real one does;
left out wherever a source's hooks other than the policy tier's are), each refusing as a settings hook refuses; a
command hook's allow with no deny skips the permission step as a callback's does (the approval hook of a hosted
workspace answers so once the owner has approved), unless a hook or callback answered ask, the most
restrictive answer winning as the real one's does. Each such call is written to
$HOME/stand-in-calls.jsonl.

A task's attempt (PP_TASK_ATTEMPT, proofpane_workspace/task_attempt.py) goes its own way, and writes each message it is
given to $HOME/stand-in-received.jsonl first, as any frozen start (PP_FROZEN, an account's identity read among them)
does. Its calls go through the permission step as the real one's do: allowed
by --allowedTools (a name, Bash(<command>) or Bash(<prefix>:*)), or Read, Glob, Grep and LS, they run; otherwise its
permission mode decides (dontAsk refuses, bypassPermissions runs, acceptEdits runs an edit), and else it asks with
can_use_tool. Frozen (PP_FROZEN), it keeps its first message in $HOME/stand-in-first-<session>.txt and acknowledges as
the frozen directives of the account file or of $HOME/stand-in-attempt.json say: the calls it tries first, a delay
(seconds), then task_state and resume_ack, right, wrong once or twice, not at all, or not before it exits; a later message (the
workspace's reminder) acknowledges again. Resumed with "Resume check passed: go on.", it does the actions of its first
message's first paragraph, separated by " ;; ", and any later message's first paragraph the same way: bash, write,
append, read, edit, glob, grep, tool, save, complete, state, ask and mcp calls (an Edit of a string the file does not
hold and an unknown tool are refused before any hook, as the real one refuses them; a Write over a file it has not
read runs, with its hooks, as 2.1.274 ran the one measured); ratelimit, sleep, endconv
(EndConversation, which no hook sees, and the turn's end), servertool (a web search's block) and close (gone mid-turn);
the modifiers nopre, onepre (only the first
PreToolUse callback called), lastpre (only the last), lostdeny, unseen, nohook and inflight, before a call; batch (one
message's calls: once the workspace's interrupt has reached it, each later one does not run and gets an error result,
with no hook, or, marked inflight, after its PreToolUse callbacks, as 2.1.274 did for an interrupt sent from the first
call's PreToolUse callback; an unseen call of the batch waits for that interrupt) and parallel (two calls run at once);
skiphooks and noups as its first action; and anything else it says in words. skiphooks makes a turn with no
UserPromptSubmit whose calls still call their PreToolUse callbacks, as a turn dispatched with its submission hooks
skipped would come: no turn of 2.1.274 was measured coming so, a turn sent as a command having come with
UserPromptSubmit, so the attempt's rule it tests, that
such a turn ends nothing, stays a defence.

Written into a test's bin directory as `claude`, run with the tests' own python."""

import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time

args = sys.argv[1:]
# a test may write CONFIG above this file: its version, whether its help and its handshake work, and the build its
# `install stable` puts on the home's volume (claude_update.py), as Claude Code's own does
CONFIG = globals().get("CONFIG", {})
VERSION = CONFIG.get("VERSION", "2.1.274")
if CONFIG.get("VERSION_FILE"):
    # a shim, as a version manager's is: the same file whichever build it runs, its version read from where it points
    with open(CONFIG["VERSION_FILE"]) as fh:
        VERSION = fh.read().strip()
if CONFIG.get("VERSION_PIN"):
    # a version manager that chooses its build by the folder it runs in (mise and asdf read their pin upward from
    # there): the nearest file of that name, from the folder it was started in up, names the version it runs
    _here = os.path.realpath(os.getcwd())
    while True:
        if os.path.isfile(os.path.join(_here, CONFIG["VERSION_PIN"])):
            with open(os.path.join(_here, CONFIG["VERSION_PIN"])) as fh:
                VERSION = fh.read().strip()
            break
        if os.path.dirname(_here) == _here:
            break
        _here = os.path.dirname(_here)
if args[:1] in (["-v"], ["--version"]):
    if CONFIG.get("VERSION_ASKS"):
        # where its version was asked, and with the session's own names or without them
        with open(CONFIG["VERSION_ASKS"], "a") as fh:
            fh.write(json.dumps({"cwd": os.path.realpath(os.getcwd()), "env": {
                k: os.environ.get(k) for k in ("PP_SESSION_ID", "PP_SESSION_DIR", "CLAUDE_CONFIG_DIR")}}) + "\n")
    if CONFIG.get("VERSION_DELAY"):
        # slow to say its version, as a claude can be: the SDK asks it inside its connect, before it starts claude
        with open(os.path.join(os.environ.get("HOME", "/tmp"), "stand-in-version-asked"), "a") as fh:
            fh.write(f"{os.getpid()}\n")
        time.sleep(CONFIG["VERSION_DELAY"])
    print(f"{VERSION} (Claude Code)")
    sys.exit(0)
if args[:1] == ["--help"] and CONFIG.get("HELP_DELAY"):
    # slow to say its help, as a claude can be (a start reads it before it starts the session)
    time.sleep(CONFIG["HELP_DELAY"])
if args[:1] == ["--help"] and not CONFIG.get("HELP_OK", True):
    print("Usage: claude [options] [command] [prompt]")
    sys.exit(0)
if args[:2] == ["install", "stable"]:
    new = CONFIG.get("INSTALLS")
    if new:
        home = os.environ["HOME"]
        folder = os.path.join(home, ".local", "share", "claude", "versions")
        os.makedirs(folder, exist_ok=True)
        build = os.path.join(folder, new["VERSION"])
        own = open(__file__).read().split("\n", 2)
        with open(build, "w") as fh:
            fh.write(own[0] + "\nCONFIG = " + repr(new) + "\n" + own[2])
        os.chmod(build, 0o755)
        os.makedirs(os.path.join(home, ".local", "bin"), exist_ok=True)
        link = os.path.join(home, ".local", "bin", "claude")
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(build, link)
    print("Claude Code successfully installed!")
    sys.exit(0)
if args[:1] == ["--help"]:
    # the two lines of Claude Code's help that say what a chat may choose (claude_cli.py reads them)
    print("  --effort <level>                      Effort level for the current session\n"
          "                                        (low, medium, high, xhigh, max)\n"
          "  --permission-mode <mode>              Permission mode to use for the session\n"
          "                                        (choices: \"acceptEdits\", \"auto\",\n"
          "                                        \"bypassPermissions\", \"manual\",\n"
          "                                        \"dontAsk\", \"plan\")")
    sys.exit(0)
if "-p" in args or "--print" in args:
    # print mode, as the updater's probe starts a build (claude_update.args_probe): the --settings file's `env` taken
    # over the process's, then its UserPromptSubmit command hooks run before any request, each in exec form when it
    # gives args, unless the build is one from before 2.1.139 (CONFIG's HOOK_ARGS false), which starts a hook's program
    # alone, as a line, or one that wraps a hook given as a program in the shell prefix as it wraps a line (CONFIG's
    # HOOK_PREFIX_WRAPS); then the request to its API, which the probe points at a closed port, fails
    given = {}
    if "--settings" in args and args.index("--settings") + 1 < len(args):
        try:
            with open(args[args.index("--settings") + 1]) as fh:
                given = json.load(fh)
        except (OSError, ValueError):
            given = {}
    hook_env = dict(os.environ, **{k: str(v) for k, v in (given.get("env") or {}).items()})
    prefix = hook_env.get("CLAUDE_CODE_SHELL_PREFIX") or ""
    for group in (given.get("hooks") or {}).get("UserPromptSubmit") or []:
        for hook in group.get("hooks") or []:
            if hook.get("type") != "command":
                continue
            if isinstance(hook.get("args"), list) and CONFIG.get("HOOK_ARGS", True):
                argv = [hook["command"]] + [str(a) for a in hook["args"]]
                if prefix and CONFIG.get("HOOK_PREFIX_WRAPS"):
                    argv = [prefix] + argv
            else:
                argv = ["/bin/sh", "-c", (prefix + " " if prefix else "") + hook["command"]]
            subprocess.run(argv, input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "probe"}),
                           text=True, timeout=30, env=hook_env)
    print("API Error: Connection error.", file=sys.stderr)
    sys.exit(1)
def _claude_json():
    try:
        # in an account's folder when Claude Code is given one (CLAUDE_CONFIG_DIR), as the real one keeps it
        with open(os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("HOME", "/tmp"),
                               ".claude.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _repository():
    """The repository a folder belongs to, as Claude Code keys its local scope: a worktree's is its main one's."""
    p = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True,
                       text=True)
    return os.path.realpath(os.path.dirname(p.stdout.strip())) if p.returncode == 0 else os.path.realpath(os.getcwd())


if args[:2] == ["mcp", "add"] or args[:2] == ["mcp", "remove"]:
    opts = {"env": {}, "headers": {}}
    rest = []
    words = args[2:]
    for i, a in enumerate(words):
        if a == "--":
            rest += words[i:]
            break
        if a.startswith("--") and "=" in a:
            key, value = a[2:].split("=", 1)
            if key == "env":
                k, v = value.split("=", 1)
                opts["env"][k] = v
            elif key == "header":
                k, v = value.split(":", 1)
                opts["headers"][k.strip()] = v.strip()
            else:
                opts[key] = value
        else:
            rest.append(a)
    scope = opts.get("scope", "local")
    data = _claude_json()
    home_file = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("HOME", "/tmp"), ".claude.json")
    where = data.setdefault("mcpServers", {}) if scope == "user" else \
        data.setdefault("projects", {}).setdefault(_repository(), {}).setdefault("mcpServers", {})
    name = rest[0] if rest else ""
    if args[1] == "remove":
        if name not in where:
            print(f'No MCP server named "{name}" in {scope} scope', file=sys.stderr)
            sys.exit(1)
        del where[name]
    else:
        if name in where:
            print(f"MCP server {name} already exists in {scope} config", file=sys.stderr)
            sys.exit(1)
        if opts.get("transport") in ("http", "sse"):
            where[name] = dict({"type": opts["transport"], "url": rest[1]},
                               **({"headers": opts["headers"]} if opts["headers"] else {}))
        else:
            where[name] = {"type": "stdio", "command": rest[2], "args": rest[3:], "env": opts["env"]}
    with open(home_file, "w") as fh:
        json.dump(data, fh)
    print(f"File modified: {home_file}")
    sys.exit(0)


def _account_folder():
    """The account's folder, as Claude Code keeps it: CLAUDE_CONFIG_DIR, else ~/.claude."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ.get("HOME", "/tmp"), ".claude")


def _resumed():
    """Whether this process was started with --resume (a task's attempt's unfreezing restart)."""
    return "--resume" in args or any(a.startswith("--resume=") for a in args)


def _account():
    """What a test wrote of the account in stand-in-account.json in the account's folder (a JSON object), or None; its
    "restart" object, in a process started with --resume and only there, laid over the rest."""
    try:
        with open(os.path.join(_account_folder(), "stand-in-account.json"), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict):
        return None
    restart = got.pop("restart", None)
    if isinstance(restart, dict) and _resumed():
        got.update(restart)
    return got


if args[:2] == ["auth", "status"]:
    # Claude Code's own status, as the agent reads it: signed in, unless a test wrote SIGNED_OUT (a path) above this
    # file and made that file; with an account file, its email, plan word, orgId and orgName, or signed out when it says
    signed_out = globals().get("SIGNED_OUT")
    signed_in = not (signed_out and os.path.exists(signed_out))
    account = _account()
    if account is None:
        print(json.dumps(dict({"loggedIn": signed_in, "authMethod": "claude.ai" if signed_in else "none"},
                              **({"email": "stand-in@example.com", "subscriptionType": "max"} if signed_in else {}))))
    elif account.get("auth_unreadable"):
        print("Something went wrong reading the sign-in.")
    elif not signed_in or account.get("signed_out"):
        print(json.dumps({"loggedIn": False, "authMethod": "none"}))
    else:
        org_id = account.get("org_id", "org-stand-in")
        if isinstance(org_id, list) and org_id:
            # one orgId for each `auth status` in turn, the last one repeated (the folder signed in again meanwhile),
            # counted in the account's folder
            counted = os.path.join(_account_folder(), "stand-in-auth-count")
            try:
                with open(counted, encoding="utf-8") as fh:
                    n = int(fh.read() or 0)
            except (OSError, ValueError):
                n = 0
            with open(counted, "w", encoding="utf-8") as fh:
                fh.write(str(n + 1))
            org_id = org_id[min(n, len(org_id) - 1)]
        status = {"loggedIn": True, "authMethod": "claude.ai",
                  "email": account.get("auth_email", account.get("email", "stand-in@example.com")),
                  "subscriptionType": account.get("auth_plan", "max"), "orgId": org_id,
                  "orgName": account.get("org_name", account.get("organization"))}
        print(json.dumps({k: v for k, v in status.items() if v is not None}))
    sys.exit(0)
HOME = os.environ.get("HOME", "/tmp")


def given_servers():
    """The servers of every --mcp-config it was given, each a JSON object or a file's path, as the real one takes
    them (measured on 2.1.274: a second one, a file, is taken beside the first)."""
    out = {}
    for i, a in enumerate(args):
        if a != "--mcp-config" or i + 1 >= len(args):
            continue
        value = args[i + 1]
        try:
            if not value.lstrip().startswith("{"):
                MCP_FILES.append({"path": value, "mode": os.stat(value).st_mode & 0o777})
            got = json.loads(value) if value.lstrip().startswith("{") else json.load(open(value))
            out.update(got.get("mcpServers") or {})
        except (OSError, ValueError, AttributeError):
            continue
    return out


MCP_FILES = []
# each stand-in that runs leaves its pid here, so a test can count the ones alive at once
os.makedirs(os.path.join(HOME, "stand-in-alive"), exist_ok=True)
open(os.path.join(HOME, "stand-in-alive", str(os.getpid())), "w").close()


def _json_value(value):
    """A settings value as Claude Code takes one: a JSON object, or the path of a file holding one; {} otherwise."""
    try:
        if value.lstrip().startswith("{"):
            got = json.loads(value)
        else:
            with open(value, encoding="utf-8") as fh:
                got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _setting_sources():
    """The setting sources --setting-sources names (the SDK gives it as --setting-sources=<list>, empty for none), or
    None when it is not given, when all three are read."""
    for i, a in enumerate(args):
        if a.startswith("--setting-sources="):
            value = a.split("=", 1)[1]
        elif a == "--setting-sources" and i + 1 < len(args):
            value = args[i + 1]
        else:
            continue
        return [s.strip() for s in value.split(",") if s.strip()]
    return None


# its settings, as the real one reads them: the user, project and local files the sources name, its --settings, and the
# policy tier (a test's server-managed settings, which replace the managed file the image has); each source's env is
# laid over its own environment in that order, the later winning, as is each setting
SOURCES = _setting_sources()
LAYERS = []                                 # (source, its settings), in the order they apply
for _source, _path in (("user", os.path.join(_account_folder(), "settings.json")),
                       ("project", os.path.join(os.getcwd(), ".claude", "settings.json")),
                       ("local", os.path.join(os.getcwd(), ".claude", "settings.local.json"))):
    if (SOURCES is None or _source in SOURCES) and os.path.exists(_path):
        LAYERS.append((_source, _json_value(_path)))
if "--settings" in args and args.index("--settings") + 1 < len(args):
    LAYERS.append(("flag", _json_value(args[args.index("--settings") + 1])))
MANAGED = None                              # "server", "file", or None: where its policy tier came from
for _kind, _name in (("server", "stand-in-server-managed.json"), ("file", "stand-in-managed.json")):
    if os.path.exists(os.path.join(HOME, _name)):
        MANAGED = _kind
        LAYERS.append(("policy", _json_value(os.path.join(HOME, _name))))
        break
EFFECTIVE = dict(os.environ)                # the session's environment: its own, each source's env laid over it
SETTINGS = {}                               # the settings in effect
for _source, _layer in LAYERS:
    SETTINGS.update(_layer)
    if isinstance(_layer.get("env"), dict):
        EFFECTIVE.update({str(k): v if isinstance(v, str) else json.dumps(v)
                          for k, v in _layer["env"].items() if v is not None})
BARE = "--bare" in args or EFFECTIVE.get("CLAUDE_CODE_SIMPLE") == "1"       # no hook of any kind is called
FROZEN = os.environ.get("PP_FROZEN") == "1"                 # a task's attempt's frozen start
ATTEMPT = os.environ.get("PP_TASK_ATTEMPT") == "1"          # a task's attempt (task_attempt.py)
ADVISOR = SETTINGS.get("advisorModel")
# what of the session's environment each start writes down
RECORDED = ("CLAUDE_CODE_SHELL_PREFIX", "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_CODE_SIMPLE",
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "LD_PRELOAD", "LD_AUDIT",
            "LD_LIBRARY_PATH", "SHELL", "PATH", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR")
RESUME = next((a.split("=", 1)[1] for a in args if a.startswith("--resume=")), None) or (
    args[args.index("--resume") + 1] if "--resume" in args else None)     # the SDK gives it as --resume=<id>
import uuid  # noqa: E402
SID = RESUME or "5e55104f" + str(uuid.uuid4())[8:]          # a new conversation's own id, as the real one makes it


# each start's record carries its session, so that a test can tell which start made a conversation another resumes
# (the first file a frozen start leaves in $HOME is every account's)
with open(os.path.join(HOME, "stand-in-runs.jsonl"), "a") as fh:
    fh.write(json.dumps({"args": args, "env": sorted(os.environ), "cwd": os.getcwd(), "version": VERSION,
                         "mcp": {k: v for k, v in given_servers().items() if v.get("type") != "sdk"},
                         "mcp_files": MCP_FILES,
                         "effective": {n: EFFECTIVE[n] for n in RECORDED if n in EFFECTIVE},
                         "setting_sources": SOURCES, "bare": BARE, "managed": MANAGED, "advisor_model": ADVISOR,
                         "advisor_on": bool(ADVISOR) and EFFECTIVE.get("CLAUDE_CODE_DISABLE_ADVISOR_TOOL") != "1",
                         "auto_memory": EFFECTIVE.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") != "1",
                         "frozen": FROZEN, "attempt": ATTEMPT, "session": SID}) + "\n")
# opted in by a test ($HOME/stand-in-env-values): each start's whole environment, values with its names, for a test
# that looks for what must not be in it
if os.path.exists(os.path.join(HOME, "stand-in-env-values")):
    with open(os.path.join(HOME, "stand-in-env.jsonl"), "a") as fh:
        fh.write(json.dumps(dict(os.environ)) + "\n")
if not CONFIG.get("HANDSHAKE_OK", True):
    sys.exit("this build cannot speak to the SDK")


def _settings():
    if "--settings" not in args:
        return {}
    value = args[args.index("--settings") + 1]
    try:
        return json.loads(value) if value.lstrip().startswith("{") else json.load(open(value))
    except (OSError, ValueError):
        return {}


def _passed():
    """The servers given on its command line (--mcp-config) but the SDK's own, as the real one lists them (scope
    "dynamic"); a test names a server's tools, version and status in its environment: STAND_IN_TOOLS (comma-separated),
    STAND_IN_VERSION and STAND_IN_STATUS (a server needing a sign-in is "needs-auth", with no tools)."""
    servers = given_servers()
    out = []
    for name, config in servers.items():
        if not isinstance(config, dict) or config.get("type") == "sdk":
            continue
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        status = env.get("STAND_IN_STATUS") or "connected"
        tools = [t for t in (env.get("STAND_IN_TOOLS") or "echo").split(",") if t] if status == "connected" else []
        out.append({"name": name, "status": status, "scope": "dynamic", "config": config,
                    "serverInfo": {"name": name, "version": env.get("STAND_IN_VERSION") or "1.0"},
                    "tools": [{"name": t, "annotations": {}} for t in tools]})
    return out


def mcp_servers():
    """Its MCP servers, as the real one's: the ones given on its command line; the .mcp.json files' from its folder
    upward, less the ones named off, with the workspace's user and local ones, less the ones its settings deny by name
    (deniedMcpServers, measured on 2.1.274: a denied one is not listed at all); only the given ones in strict mode."""
    if "--strict-mcp-config" in args:
        return _passed()
    off = set(_settings().get("disabledMcpjsonServers") or [])
    denied = {d.get("serverName") for d in _settings().get("deniedMcpServers") or [] if isinstance(d, dict)}
    out, seen, here = [], set(), os.path.realpath(os.getcwd())
    while True:
        try:
            with open(os.path.join(here, ".mcp.json")) as fh:
                declared = json.load(fh).get("mcpServers") or {}
        except (OSError, ValueError, AttributeError):
            declared = {}
        for name, config in declared.items():
            if name not in off and name not in seen:
                seen.add(name)
                out.append({"name": name, "status": "connected", "scope": "project", "config": config,
                            "tools": [{"name": "echo"}]})
        if os.path.dirname(here) == here:
            break
        here = os.path.dirname(here)
    data = _claude_json()
    for name, config in (data.get("mcpServers") or {}).items():
        out.append({"name": name, "status": "connected", "scope": "user", "config": config, "tools": []})
    local = (data.get("projects") or {}).get(_repository(), {}).get("mcpServers") or {}
    for name, config in local.items():
        out.append({"name": name, "status": "connected", "scope": "local", "config": config, "tools": []})
    if globals().get("STRAY"):
        out.append({"name": STRAY, "status": "connected", "scope": "project",
                    "config": {"type": "stdio", "command": "stray"}, "tools": []})
    kept_off = set((data.get("projects") or {}).get(_repository(), {}).get("disabledMcpServers") or [])
    for server in out:
        if server["name"] in kept_off:
            server["status"] = "disabled"
    return _passed() + [s for s in out if s["name"] not in denied]


def keep_toggle(name, on):
    """A server turned off or on is kept for the repository, as the real one keeps it."""
    home_file = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("HOME", "/tmp"), ".claude.json")
    data = _claude_json()
    entry = data.setdefault("projects", {}).setdefault(_repository(), {})
    off = [n for n in entry.get("disabledMcpServers") or [] if n != name] + ([] if on else [name])
    entry["disabledMcpServers"] = off
    with open(home_file, "w") as fh:
        json.dump(data, fh)


MCP = mcp_servers()

lock = threading.Lock()
answers = {}                  # a control request's id -> [threading.Event, its response]
interrupted = threading.Event()
turns = queue.Queue()
counter = [0]
counting = threading.Lock()   # the counter is taken under it: two calls of a parallel batch ask at once


def send(obj):
    with lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def ask(request):
    with counting:
        counter[0] += 1
        rid = f"stand_in_req_{counter[0]}"
        slot = answers[rid] = [threading.Event(), None]
    send({"type": "control_request", "request_id": rid, "request": request})
    while not slot[0].wait(0.05):
        if interrupted.is_set():
            slot[0].wait(1.0)          # an answer sent as the interrupt came still arrives, as it does to the real one
            return slot[1]
    return slot[1]


# with --include-partial-messages, the API's own stream events come first, as Claude Code passes them on: the block's
# start, its text in pieces, its end; then the whole message, as without
PARTIAL = "--include-partial-messages" in args
streamed = [0]


def stream(event, parent=None):
    streamed[0] += 1
    send({"type": "stream_event", "uuid": f"stand-in-stream-{streamed[0]}", "session_id": SID,
          "parent_tool_use_id": parent, "event": event})


CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ.get("HOME", "/tmp"), ".claude")
PROJECT = os.path.join(CLAUDE_DIR, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()))
USAGE = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2}
replies = [0]


def stamp():
    """Now, as Claude Code writes a transcript line's time."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"


def transcribe(content, path=None, mid=None):
    """One reply in the transcript, on two lines with its id (`mid`, the message's own, when it has one) and its time,
    as a streamed reply is written."""
    replies[0] += 1
    path = path or os.path.join(PROJECT, SID + ".jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {"type": "assistant", "cwd": os.getcwd(), "sessionId": SID, "requestId": f"req_{SID[:8]}_{replies[0]}",
           "timestamp": stamp(),
           "message": {"id": mid or f"msg_{SID[:8]}_{replies[0]}", "role": "assistant", "usage": USAGE,
                       "content": content}}
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n" + json.dumps(row) + "\n")


def transcribe_result(tool_id, output, error=False, path=None):
    """A tool's result in the transcript, on a user line with its time, as Claude Code writes it."""
    path = path or os.path.join(PROJECT, SID + ".jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {"type": "user", "cwd": os.getcwd(), "sessionId": SID, "timestamp": stamp(),
           "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": output,
                                                     "is_error": error}]}}
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n")


def say(text, pieces=None, pause=0.0):
    mid = new_mid() if wired() else None
    if PARTIAL:
        stream({"type": "message_start", "message": {"id": mid or "msg_stand_in", "type": "message",
                                                      "role": "assistant", "model": "stand-in", "content": []}})
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        for piece in pieces or [text]:
            stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}})
            time.sleep(pause)
        stream({"type": "content_block_stop", "index": 0})
    message = {"role": "assistant", "model": "stand-in", "content": [{"type": "text", "text": text}]}
    send({"type": "assistant", "session_id": SID, "parent_tool_use_id": None,
          "message": dict({"id": mid}, **message) if mid else message})
    transcribe([{"type": "text", "text": text}], mid=mid)
    if PARTIAL:
        stream({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}})
        stream({"type": "message_stop"})


# like the real one's: a running total (with its tokens in and out) within this process, which 2.1.274 starts over at
# each start and 2.1.277 brings back when it resumes a session it left normally (a test writes RESTORES in CONFIG);
# EXTRA, in CONFIG, is tokens counted in its model usage and not in a turn's usage, as a sub-agent's are (the SDK's cost
# page: a turn's usage is its main loop's only), and EXTRA_RESUMED the same in a resumed claude only
SPENT = [0.0, 0, 0]
SAVED = os.path.join(os.environ.get("HOME", "/tmp"), "stand-in-saved-" + SID + ".json")
if CONFIG.get("RESTORES") and RESUME and os.path.exists(SAVED):
    SPENT = json.load(open(SAVED))


DENIALS = []           # the calls refused in this turn, as 2.1.274 lists them in its result's permission_denials


def result(subtype="success", error=False, turns=1):
    SPENT[0] = round(SPENT[0] + 0.0123, 6)
    SPENT[1] += 10 + CONFIG.get("EXTRA", 0) + (CONFIG.get("EXTRA_RESUMED", 0) if RESUME else 0)
    SPENT[2] += 5
    denials, DENIALS[:] = list(DENIALS), []
    return {"type": "result", "subtype": subtype, "duration_ms": 5, "duration_api_ms": 4, "is_error": error,
            "num_turns": turns, "session_id": SID, "total_cost_usd": SPENT[0], "result": "",
            "usage": {"input_tokens": 10, "output_tokens": 5}, "permission_denials": denials,
            "modelUsage": {"stand-in": {"inputTokens": SPENT[1], "outputTokens": SPENT[2], "costUSD": SPENT[0]}}}


def sdk_server():
    """The name of the SDK's in-process server, from --mcp-config, as the SDK gives it."""
    if "--mcp-config" not in args:
        return None
    try:
        servers = json.loads(args[args.index("--mcp-config") + 1]).get("mcpServers") or {}
    except (ValueError, AttributeError):
        return None
    return next((name for name, config in servers.items() if isinstance(config, dict) and config.get("type") == "sdk"),
                None)


mcp_ready = [False]
mcp_starting = threading.Lock()


def call_tool(name, arguments):
    """A tool of the SDK's in-process server, called as the real one calls it: an initialize, its notice, then
    tools/call, each a control request carrying the JSON-RPC message. The tool's text, or why it failed."""
    return _mcp_call(name, arguments)[0]


def _mcp_call(name, arguments):
    """call_tool's text, and whether the tool failed (its isError, or no result at all)."""
    server = sdk_server()
    if server is None:
        return "no SDK server", True
    with mcp_starting:
        if not mcp_ready[0]:
            ask({"subtype": "mcp_message", "server_name": server, "message": {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "stand-in", "version": "0"}}}})
            ask({"subtype": "mcp_message", "server_name": server,
                 "message": {"jsonrpc": "2.0", "method": "notifications/initialized"}})
            mcp_ready[0] = True
    with counting:
        counter[0] += 1
        rpc = 100 + counter[0]
    got = ask({"subtype": "mcp_message", "server_name": server, "message": {
        "jsonrpc": "2.0", "id": rpc, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}}}) or {}
    response = (got.get("mcp_response") or {}) if isinstance(got, dict) else {}
    result = response.get("result") or {}
    text = " ".join(c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)) or \
        json.dumps(response.get("error"))
    return text, bool(result.get("isError")) or "result" not in response


def tool_turn(name, tool_input, output, error=False, run=None):
    """A call whose result is `output` (`error` when it failed); `run`, when given, does its work first and gives them.
    Where the SDK registered hook callbacks, the call goes through them (Call, below), and `run` only once they let
    it."""
    if hooked():
        call = Call(name, tool_input, run=lambda c: run() if run else (output, error), permit=None)
        announce([call])
        handle(call)
        return call.id
    if run is not None:
        output, error = run()
    counter[0] += 1
    tid = f"toolu_stand_in_{counter[0]}"
    send({"type": "assistant", "session_id": SID, "parent_tool_use_id": None,
          "message": {"role": "assistant", "model": "stand-in", "content": [
              {"type": "tool_use", "id": tid, "name": name, "input": tool_input}]}})
    transcribe([{"type": "tool_use", "id": tid, "name": name, "input": tool_input}])
    send({"type": "user", "session_id": SID, "parent_tool_use_id": None,
          "message": {"role": "user", "content": [
              {"type": "tool_result", "tool_use_id": tid, "content": output, "is_error": error}]}})
    transcribe_result(tid, output, error)
    return tid


def own_tool(tool, arguments):
    """One of the workspace's tools to start agents' runs, called as the real one calls it, and put in the transcript;
    None when --allowedTools does not allow it."""
    allowed = args[args.index("--allowedTools") + 1].split(",") if "--allowedTools" in args else []
    if f"mcp__proofpane__{tool}" not in allowed:
        return None
    if hooked():
        # through the SDK's callbacks first; one a hook refused is as one not allowed
        call = Call(f"mcp__proofpane__{tool}", arguments, run=lambda c: _mcp_call(tool, c.input), permit=None)
        announce([call])
        handle(call)
        return call.text if call.ran else None
    answer = call_tool(tool, arguments)
    tool_turn(f"mcp__proofpane__{tool}", arguments, answer)
    return answer


def review():
    """A review run (the improve loop's): it reads its digest and proposes, as its agent's brief asks of it: SKILLTOOLS a
    skill with allowed-tools (refused), THREE three changes (the third refused), BAD the brief with GOOD for BAD (with
    SPECIFIC kept, which the stand-in critic calls specific); then it names the first run's last words it read."""
    folder = os.path.join(".proofpane-uploads", "review")
    try:
        with open(os.path.join(folder, "digest.json")) as fh:
            digest = json.load(fh)
    except (OSError, ValueError):
        say("no digest")
        return
    brief = (digest.get("current") or {}).get("brief") or ""
    runs = [r.get("run_id") for r in digest.get("runs") or [] if isinstance(r, dict)][:3]
    said = []
    if "SKILLTOOLS" in brief:
        said.append(own_tool("propose_change", {"target": "skill", "name": "labels", "hypothesis": "h",
                                                 "value": "---\nname: labels\ndescription: d\nallowed-tools: Bash(*)\n---\nx",
                                                 "evidence": {"runs": runs}}))
    if "THREE" in brief:
        for n in range(3):
            said.append(own_tool("propose_change", {"target": "effort", "value": "high", "hypothesis": f"try {n}",
                                                     "evidence": {"runs": runs}}))
    if "BAD" in brief:
        said.append(own_tool("propose_change", {"target": "brief", "value": brief.replace("BAD", "GOOD"),
                                                 "hypothesis": "Saying GOOD passes.",
                                                 "evidence": {"runs": runs, "metric": "judge task_fit"}}))
    first = ""
    if runs:
        try:
            with open(os.path.join(folder, "runs", runs[0] + ".json")) as fh:
                first = json.load(fh).get("final") or ""
        except (OSError, ValueError):
            first = "(unread)"
    say("Reviewed: " + " | ".join(s or "not allowed" for s in said) + " || first run said: " + first)


def pair_grades(whole):
    """The pairwise judge's grades: each output graded as one run is (GOOD 0.9, BAD a task_fit of 0.3, else 0.75)."""
    def grade(part):
        g = 0.9 if "GOOD" in part else 0.75
        return {"task_fit": 0.3 if "BAD" in part else g, "grounding": g, "completeness": g, "hygiene": g, "actionable": g}
    a = whole.split("OUTPUT A:", 1)[-1].split("OUTPUT B:", 1)[0]
    b = whole.split("OUTPUT B:", 1)[-1]
    return {"A": grade(a), "B": grade(b), "why": "stand-in pair"}


def together(text):
    """The words of an action with the workspace's tools to start agents' runs; None when one was not allowed."""
    parts = text.split(" ", 1)[1].split("|") if " " in text else []
    if text == "agents":
        return own_tool("list_agents", {})
    if text.startswith("waitrun "):
        return own_tool("wait_run", {"run_id": parts[0], "seconds": int(parts[1])})
    if text.startswith("sendrun "):
        return own_tool("send_to_run", {"run_id": parts[0], "message": parts[1]})
    m = re.match(r"^dispatch(\*\d+|wait2|wait|send)? ", text)
    if m.group(1) == "wait2":
        # two runs started, then a wait on each in the same turn: "dispatchwait2 <name>|<name>|<message>|<seconds>"
        ids = []
        for name in parts[:2]:
            got = own_tool("run_agent", {"name": name, "message": parts[2]})
            if got is None:
                return None
            ids.append(re.search(r"Run ([0-9a-f]{12}) queued", got).group(1))
        return "\n".join(own_tool("wait_run", {"run_id": i, "seconds": int(parts[3])}) for i in ids)
    times = int(m.group(1)[1:]) if m.group(1) and m.group(1).startswith("*") else 1
    said = []
    for _ in range(times):
        got = own_tool("run_agent", {"name": parts[0], "message": parts[1]})
        if got is None:
            return None
        said.append(got)
    child = re.search(r"Run ([0-9a-f]{12}) queued", said[-1])
    if m.group(1) == "wait" and child:
        answer = own_tool("wait_run", {"run_id": child.group(1), "seconds": int(parts[2])})
        if len(parts) > 3:
            time.sleep(float(parts[3]))         # "dispatchwait ...|<seconds>|<linger>": its turn goes on that long
        return answer
    if m.group(1) == "send" and child:
        for _ in range(300):            # until it works: a run takes a message only then
            state = json.loads(own_tool("wait_run", {"run_id": child.group(1), "seconds": 0}) or "{}")
            if state.get("state") in ("working", "waiting") or state.get("ended"):
                break
            time.sleep(0.2)
        own_tool("send_to_run", {"run_id": child.group(1), "message": parts[2]})
        return own_tool("wait_run", {"run_id": child.group(1), "seconds": int(parts[3])})
    return "\n".join(said)


def subagent_report(words):
    """The sub-agent's own transcript, and its report."""
    agent = f"a{SID[:6]}{counter[0]}"
    transcribe([{"type": "text", "text": "working on: " + words}],
               os.path.join(PROJECT, SID, "subagents", f"agent-{agent}.jsonl"))
    return "Sub-agent report: " + words


# ---- the SDK's hook callbacks, the permission step, and a task's attempt --------------------------------------------

HOOKS = {}                  # the SDK's hook callbacks, as its initialize request gave them: {event: [matcher, ...]}
MODE = [args[args.index("--permission-mode") + 1]
        if "--permission-mode" in args and args.index("--permission-mode") + 1 < len(args) else "default"]
TURN = {"quiet": False}     # this turn began with noups: none of its calls calls any callback
TURNS_SEEN = [0]            # the turns this process has begun
SESSION_RULES = []          # the allow rules an answer added for this session
ACKS = [0]                  # the resume_ack calls this process has made
RESUMED = "Resume check passed: go on."
OWN = "proofpane"           # the workspace's own in-process server, which a task's tools are on
TOOLS = ("Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "LS", "NotebookEdit", "WebFetch", "WebSearch",
         "TodoWrite", "EndConversation")
EDITS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
FREE = ("Read", "Glob", "Grep", "LS")       # run without asking
MODIFIERS = ("nopre", "onepre", "lastpre", "lostdeny", "unseen", "nohook", "inflight")
# a batch's later call once the workspace's interrupt has reached it: its result, the words printed (cut there) for
# 2.1.274's in a measured cut start; for another measured batch's later Reads none were printed
REJECTED = "The user doesn't want to proceed with this tool use. The tool use was rejected"


def hooked():
    """Whether the SDK registered any hook callback in its initialize request (no chat or agent's run does today)."""
    return any(isinstance(m, dict) and m.get("hookCallbackIds")
               for ms in HOOKS.values() if isinstance(ms, list) for m in ms)


def commanded():
    """Whether a test made $HOME/stand-in-command-hooks: the settings' and the plugins' PreToolUse command hooks run."""
    return os.path.exists(os.path.join(HOME, "stand-in-command-hooks"))


def wired():
    """A task's attempt, or a run whose SDK registered hook callbacks: its calls go through them, and every assistant
    message has an id."""
    return ATTEMPT or hooked()


def new_mid():
    """A new assistant message's id, unique across the starts of a session as the real one's are."""
    return "msg_stand_in_" + uuid.uuid4().hex[:20]


def _matches(matcher, name):
    """Whether a hook's matcher matches a tool's name, as Claude Code's does: none, "" or "*" every tool, else a
    regular expression matched against the whole name."""
    if matcher in (None, "", "*"):
        return True
    try:
        return re.fullmatch(str(matcher), name) is not None
    except re.error:
        return matcher == name


def callbacks(event, name=None):
    """The ids of the SDK's callbacks for `event`, in the order registered, each of a matcher that matches the tool
    `name` (every one, for an event of no tool)."""
    out = []
    for m in HOOKS.get(event) or []:
        if isinstance(m, dict) and (name is None or _matches(m.get("matcher"), name)):
            out += [c for c in m.get("hookCallbackIds") or [] if isinstance(c, str)]
    return out


def hook_input(event, **more):
    """A hook's input, as the real one gives it."""
    return dict({"session_id": SID, "transcript_path": os.path.join(PROJECT, SID + ".jsonl"), "cwd": os.getcwd(),
                 "permission_mode": MODE[0], "hook_event_name": event}, **more)


def call_back(cid, data, tool_use_id):
    """One of the SDK's callbacks, called over the control channel as the real one calls it; its answer, or None (no
    decision, as 2.1.274 takes a failed callback) when it answered with an error, or not at all."""
    got = ask({"subtype": "hook_callback", "callback_id": cid, "input": data, "tool_use_id": tool_use_id})
    return got if isinstance(got, dict) else None


def _deny(answer):
    """A PreToolUse answer's deny: its reason ("" when it gives none), or None when it does not deny."""
    out = answer.get("hookSpecificOutput") if isinstance(answer, dict) else None
    if isinstance(out, dict) and out.get("permissionDecision") == "deny":
        return str(out.get("permissionDecisionReason") or "")
    return None


def prompt_hooks(text):
    """A turn's UserPromptSubmit callbacks, with its message."""
    data = hook_input("UserPromptSubmit", prompt=text)
    for cid in callbacks("UserPromptSubmit"):
        call_back(cid, data, None)


def command_hooks():
    """The PreToolUse command hooks of its settings, as (source, entry), when a test made $HOME/stand-in-command-hooks:
    none under the policy tier's disableAllHooks; the policy tier's alone under its allowManagedHooksOnly, or where the
    disableAllHooks left after the other sources' precedence (user, project, local, then --settings, the later
    winning) is true, as the repository reads Claude Code (settings.py: "reads the value left after settings
    precedence applies"), so a --settings false keeps a user's or a repository's true from turning hooks off.
    Neither turns an SDK callback off."""
    if not commanded():
        return []
    policy = dict(LAYERS).get("policy") or {}
    if policy.get("disableAllHooks") is True:
        return []
    layers = LAYERS
    left = None                                 # the other sources' disableAllHooks, the later winning
    for source, s in LAYERS:
        if source != "policy" and "disableAllHooks" in s:
            left = s["disableAllHooks"]
    if policy.get("allowManagedHooksOnly") is True or left is True:
        layers = [(source, s) for source, s in LAYERS if source == "policy"]
    out = []
    for source, settings in layers:
        hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
        out += [(source, entry) for entry in hooks.get("PreToolUse") or [] if isinstance(entry, dict)]
    if layers is LAYERS:
        out += plugin_hooks()
    return out


def plugin_hooks():
    """The PreToolUse command hooks of each plugin a --plugin-dir names, as (source, entry), source "plugin:<folder>":
    its hooks/hooks.json read as the real one reads it, ${CLAUDE_PLUGIN_ROOT} put in as its folder in a hook's command
    and args, and its folder kept for the hook's environment."""
    out = []
    for i, a in enumerate(args):
        if a != "--plugin-dir" or i + 1 >= len(args):
            continue
        root = args[i + 1]
        try:
            with open(os.path.join(root, "hooks", "hooks.json"), encoding="utf-8") as fh:
                hooks = (json.load(fh) or {}).get("hooks") or {}
        except (OSError, ValueError):
            continue
        for entry in hooks.get("PreToolUse") or []:
            if not isinstance(entry, dict):
                continue
            entry = json.loads(json.dumps(entry).replace("${CLAUDE_PLUGIN_ROOT}", root.replace("\\", "\\\\")))
            entry["plugin_root"] = root
            out.append(("plugin:" + os.path.basename(root.rstrip("/")), entry))
    return out


def run_command_hooks(call, data):
    """The settings' PreToolUse command hooks that match the call, each run as Claude Code runs one (in exec form when
    it gives args, else its command line behind CLAUDE_CODE_SHELL_PREFIX, or /bin/sh -c), with its input on stdin, the
    session's environment, in its folder, for at most its timeout, and written to $HOME/stand-in-hooks.jsonl; the
    reason of the first deny (an exit 2's stderr, or a JSON answer's), or None."""
    reason = None
    for source, entry in command_hooks():
        if not _matches(entry.get("matcher"), call.name):
            continue
        for hook in entry.get("hooks") or []:
            if not isinstance(hook, dict) or hook.get("type") != "command" or not isinstance(hook.get("command"), str):
                continue
            prefix = EFFECTIVE.get("CLAUDE_CODE_SHELL_PREFIX") or ""
            if isinstance(hook.get("args"), list):
                argv = [hook["command"]] + [str(a) for a in hook["args"]]
            elif prefix.strip():
                try:
                    argv = shlex.split(prefix) + [hook["command"]]
                except ValueError:                  # a prefix no shell could read: taken whole
                    argv = [prefix, hook["command"]]
            else:
                argv = ["/bin/sh", "-c", hook["command"]]
            limit = hook.get("timeout")
            limit = limit if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0 else 60
            code, why = None, None
            env = dict(EFFECTIVE, CLAUDE_PLUGIN_ROOT=entry["plugin_root"]) if entry.get("plugin_root") else EFFECTIVE
            try:
                p = subprocess.run(argv, input=json.dumps(data), capture_output=True, text=True, errors="replace",
                                   env=env, cwd=os.getcwd(), timeout=limit)
                code = p.returncode
                if code == 2:
                    why = p.stderr.strip()
                else:
                    try:
                        answer = json.loads(p.stdout)
                        why = _deny(answer)
                        out = answer.get("hookSpecificOutput") if isinstance(answer, dict) else None
                        if why is None and isinstance(out, dict) and out.get("permissionDecision") == "allow":
                            call.hook_allowed = True    # skips the permission step, as a callback's allow does
                        if why is None and isinstance(out, dict) and out.get("permissionDecision") == "ask":
                            call.hook_asked = True      # which an allow does not skip: the most restrictive wins
                    except ValueError:
                        why = None
            except (OSError, subprocess.SubprocessError):
                pass
            call.hooks_run.append(argv)
            with open(os.path.join(HOME, "stand-in-hooks.jsonl"), "a") as fh:
                fh.write(json.dumps({"sid": SID, "argv": argv, "source": source, "exit": code,
                                     "denied": why is not None}) + "\n")
            if why is not None and reason is None:
                reason = why
    return reason


class Call:
    """One client tool call: its block, what its hooks and the permission step decided, and how it ended."""

    def __init__(self, name, given, mod=None, run=None, permit="rules", check=None):
        self.name, self.input, self.mod = name, given, mod
        self.id = "toolu_stand_in_" + uuid.uuid4().hex[:20]     # unique across the starts of a session
        self.run = run              # its work: (call) -> (its result's text, whether it failed)
        self.permit = permit        # its permission step: "rules" (the real one's), "ask" (it always asks), None (none)
        self.check = check          # (call) -> why Claude Code refuses it before any hook, or None
        self.pre, self.post, self.hooks_run = [], None, []
        self.denied = self.ran = self.error = self.lost = self.hook_allowed = self.hook_asked = False
        self.refused, self.text = None, ""


def quiet(call):
    """No hook of any kind for the call: bare mode, a turn of noups, an unseen call, or EndConversation (2.1.274
    exempts it)."""
    return BARE or TURN["quiet"] or call.mod == "unseen" or call.name == "EndConversation"


def announce(calls):
    """Step 1: the assistant message holding the calls' blocks (a batch's in one message, with one id), and its reply
    in the transcript."""
    blocks = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
    mid = new_mid()
    if PARTIAL:
        for i, b in enumerate(blocks):
            stream({"type": "content_block_start", "index": i,
                    "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            stream({"type": "content_block_stop", "index": i})
    send({"type": "assistant", "session_id": SID, "parent_tool_use_id": None,
          "message": {"id": mid, "role": "assistant", "model": "stand-in", "content": blocks}})
    transcribe(blocks, mid=mid)


def pre_hooks(call):
    """Step 2: every PreToolUse callback that matches the call, in the order registered, even after one denied, then
    the settings' command hooks; the reason of the first deny, or None."""
    data = hook_input("PreToolUse", tool_name=call.name, tool_input=call.input, tool_use_id=call.id)
    reason = None
    ids = callbacks("PreToolUse", call.name)
    # onepre: the others lost on their way; lastpre: all but the last lost
    for cid in ids[:1] if call.mod == "onepre" else ids[-1:] if call.mod == "lastpre" else ids:
        call.pre.append(cid)
        answer = call_back(cid, data, call.id)
        why = _deny(answer)
        if why is not None and reason is None:
            reason = why
        out = answer.get("hookSpecificOutput") if isinstance(answer, dict) else None
        if isinstance(out, dict) and out.get("permissionDecision") == "allow":
            call.hook_allowed = True        # a callback's allow skips the permission step, as the real one's does
        if isinstance(out, dict) and out.get("permissionDecision") == "ask":
            call.hook_asked = True          # and one hook's ask keeps it, whatever another allowed
    why = run_command_hooks(call, data)
    return reason if reason is not None else why


def _rules():
    given = args[args.index("--allowedTools") + 1].split(",") \
        if "--allowedTools" in args and args.index("--allowedTools") + 1 < len(args) else []
    return [r.strip() for r in given if r.strip()] + SESSION_RULES


def allowed(call):
    """Whether an allow rule lets the call run: its tool's name (an MCP tool's mcp__<server>__<tool>), or for Bash
    Bash(<command>) or Bash(<prefix>:*)."""
    command = str(call.input.get("command", "")) if isinstance(call.input, dict) else ""
    for rule in _rules():
        if rule == call.name:
            return True
        m = re.fullmatch(r"Bash\((.*)\)", rule, re.S)
        if m and call.name == "Bash" and (command == m.group(1) or
                                         m.group(1).endswith(":*") and command.startswith(m.group(1)[:-2])):
            return True
    return False


def permitted(call):
    """Step 3, the permission step, as Claude Code's: None when the call runs, else (why it is refused, whether the
    answer also stops the turn)."""
    if call.permit is None:
        return None
    if call.permit == "rules":
        if call.name in FREE or allowed(call):
            return None
        if MODE[0] == "dontAsk":
            return f"Permission to use {call.name} has been denied because Claude Code is running in don't ask mode", \
                False
        if MODE[0] == "bypassPermissions" or MODE[0] == "acceptEdits" and call.name in EDITS:
            return None
    rule = {"toolName": call.name}
    if call.name == "Bash" and isinstance(call.input, dict):
        rule["ruleContent"] = str(call.input.get("command", ""))
    answer = ask({"subtype": "can_use_tool", "tool_name": call.name, "input": call.input, "tool_use_id": call.id,
                  "permission_suggestions": [{"type": "addRules", "rules": [rule], "behavior": "allow",
                                              "destination": "localSettings"}]})
    if answer is not None:
        with open(os.path.join(HOME, "stand-in-answers.jsonl"), "a") as fh:
            fh.write(json.dumps(answer) + "\n")
    if answer is None or answer.get("interrupt"):          # a deny that also stops the turn, as the CLI does
        return str((answer or {}).get("message") or "Interrupted by the user"), True
    if answer.get("behavior") != "allow":
        return str(answer.get("message") or ""), False
    if isinstance(answer.get("updatedInput"), dict):
        call.input = answer["updatedInput"]
    for update in answer.get("updatedPermissions") or []:
        if isinstance(update, dict) and update.get("type") == "addRules" and update.get("behavior") == "allow":
            for r in update.get("rules") or []:
                if isinstance(r, dict) and isinstance(r.get("toolName"), str):
                    SESSION_RULES.append(r["toolName"] + (f"({r['ruleContent']})" if r.get("ruleContent") else ""))
    return None


def _refuse(call, why, text, listed):
    """The call refused (`why`: before_hooks, interrupt, hook or permission), with `text` its result; `listed` puts it
    in the turn's permission_denials."""
    call.refused, call.text, call.error, call.denied = why, text, True, listed
    if listed:
        DENIALS.append({"tool_name": call.name, "tool_use_id": call.id, "tool_input": call.input})


def prepare(call):
    """Steps 2 and 3 for an announced call: "run" when it is to run, else "refused", or "stop" when a permission answer
    also stopped the turn, its result set."""
    if call.mod == "nohook":
        _refuse(call, "interrupt", "Interrupted by the user", False)     # a call an interrupt cut off
        return "refused"
    why = call.check(call) if call.check else None
    if why is not None:
        _refuse(call, "before_hooks", why, False)
        return "refused"
    if not quiet(call) and call.mod != "nopre":
        reason = pre_hooks(call)
        if reason is not None and call.mod == "lostdeny":
            call.lost = True                # the deny lost on its way back: the call goes on as if allowed
        elif reason is not None:
            _refuse(call, "hook", "Refused by a hook: " + reason if reason else "Refused by a hook.", True)
            return "refused"
    if quiet(call) or call.mod in ("nopre", "lostdeny") or (call.hook_allowed and not call.hook_asked):
        return "run"                        # past its hooks without them, or a hook allowed it: no permission step
    refusal = permitted(call)
    if refusal is None:
        return "run"
    _refuse(call, "permission", refusal[0], not refusal[1])
    return "stop" if refusal[1] else "refused"


def execute(call):
    """Step 4's call: its work done, and its result's text and whether it failed kept."""
    try:
        text, failed = call.run(call) if call.run else ("", False)
    except Exception as e:  # noqa: BLE001  a tool's failure is its result, as the real one's is
        text, failed = f"{type(e).__name__}: {e}", True
    call.ran, call.text, call.error = True, "" if text is None else str(text), bool(failed)


def post_hooks(call):
    """Step 4's callbacks: the PostToolUse ones that match the call, or the PostToolUseFailure ones when it failed."""
    event = "PostToolUseFailure" if call.error else "PostToolUse"
    if call.error:
        data = hook_input(event, tool_name=call.name, tool_input=call.input, tool_use_id=call.id, error=call.text,
                          is_interrupt=False)
    else:
        data = hook_input(event, tool_name=call.name, tool_input=call.input, tool_use_id=call.id,
                          tool_response={"stdout": call.text, "stderr": "", "interrupted": False}
                          if call.name == "Bash" else call.text)
    for cid in callbacks(event, call.name):
        call_back(cid, data, call.id)
        call.post = event


def deliver(call):
    """Step 5: the call written down in $HOME/stand-in-calls.jsonl, then the user message with its result."""
    with open(os.path.join(HOME, "stand-in-calls.jsonl"), "a") as fh:
        fh.write(json.dumps({"sid": SID, "id": call.id, "name": call.name, "input": call.input, "pre": call.pre,
                             "post": call.post, "denied": call.denied, "ran": call.ran, "error": call.error,
                             "command_hooks": call.hooks_run, "refused": call.refused, "lost": call.lost}) + "\n")
    send({"type": "user", "session_id": SID, "parent_tool_use_id": None,
          "message": {"role": "user", "content": [
              {"type": "tool_result", "tool_use_id": call.id, "content": call.text, "is_error": call.error}]}})
    transcribe_result(call.id, call.text, call.error)


def handle(call):
    """An announced call through the rest of its steps to its result; False when a permission answer stopped the
    turn."""
    state = prepare(call)
    if state == "run":
        execute(call)
        if not quiet(call):
            post_hooks(call)
    deliver(call)
    return state != "stop"


def batch(calls):
    """The calls of one batch: their blocks in one message, then each through its steps in order. Once the workspace's
    interrupt has reached it, each later call does not run and gets an error result, the rejection's words (printed for
    the cut start only): 2.1.274 was measured doing that for
    an interrupt sent from the first call's PreToolUse callback, the one moment measured, and the stand-in does it for
    an interrupt at any moment, the attempt's batch rule needing it at another. With no hook of any
    kind, as the cut start's later Bash had none, or, marked inflight, after its PreToolUse callbacks (none in bare mode
    or a turn of noups), as a measured batch's later Reads had theirs, though the stand-in keeps no order between those
    callbacks and its answer to the interrupt; no post callback either way, since it did not run. False when the turn
    ended: a permission answer stopped it, the calls after it cut off, or the workspace's interrupt cut the batch."""
    announce(calls)
    for i, call in enumerate(calls):
        if interrupted.is_set():
            if call.mod == "inflight" and not quiet(call):
                pre_hooks(call)
            _refuse(call, "interrupt", REJECTED, False)
            deliver(call)
            continue
        if not handle(call):
            for rest in calls[i + 1:]:
                _refuse(rest, "interrupt", "Interrupted by the user", False)
                deliver(rest)
            return False
        if call.mod == "unseen" and i + 1 < len(calls):
            # the workspace's interrupt for the unseen call lands before the batch's next call: the calls it cuts
            # through (the order a test of them asks for; a slower interrupt finds the next call at its hooks)
            interrupted.wait(10)
    return not interrupted.is_set()


def parallel(first, second):
    """Two calls of one batch run at once, as Claude Code runs concurrency-safe calls: the first's PreToolUse callbacks
    and permission step, then its work started; meanwhile the second through its steps whole; then the first's end,
    its post callbacks and its result. False when a permission answer stopped the turn."""
    announce([first, second])
    state = prepare(first)
    if state == "stop":
        deliver(first)
        _refuse(second, "interrupt", "Interrupted by the user", False)
        deliver(second)
        return False
    running = threading.Thread(target=execute, args=(first,), daemon=True) if state == "run" else None
    if running is not None:
        running.start()
    go = handle(second)
    if running is not None:
        running.join()
        if not quiet(first):
            post_hooks(first)
    deliver(first)
    return go


# ---- the tools a task's attempt calls, and its actions --------------------------------------------------------------

def known(name):
    """Whether it has the tool: one of Claude Code's own (but the ones --disallowedTools names), or a tool of an MCP
    server it has (the SDK's own, or one it lists)."""
    given = args[args.index("--disallowedTools") + 1].split(",") \
        if "--disallowedTools" in args and args.index("--disallowedTools") + 1 < len(args) else []
    if not isinstance(name, str) or name in [t.strip() for t in given]:
        return False
    if name in TOOLS:
        return True
    server, _, tool = name[5:].partition("__") if name.startswith("mcp__") else ("", "", "")
    servers = {s.get("name") for s in MCP if isinstance(s, dict)} | {sdk_server()}
    return bool(server and tool) and server in servers


def check(call):
    """Why Claude Code refuses the call before any hook (its input's checks), or None: an unknown tool, an Edit of a
    file that does not exist or of a string it does not hold. Not a Write over a file it has not read: 2.1.274 ran the
    one measured, an existing file not read first, with both PreToolUse callbacks and a PostToolUse,
    though its source holds a refusal of a Write not read for some case not measured."""
    if not known(call.name):
        return f"No such tool available: {call.name}"
    path = str(call.input.get("file_path") or "") if isinstance(call.input, dict) else ""
    if call.name == "Edit":
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                held = fh.read()
        except OSError:
            return "File does not exist."
        if str(call.input.get("old_string", "")) not in held:
            return "String to replace not found in file."
    return None


def shell(command):
    """A Bash call's command run as the real one runs it here: in its folder, with the session's environment, by its
    SHELL when that can run, else /bin/sh; its output and exit code."""
    sh = EFFECTIVE.get("SHELL")
    argv = [sh, "-c", command] if sh and os.path.isfile(sh) and os.access(sh, os.X_OK) else ["/bin/sh", "-c", command]
    try:
        p = subprocess.run(argv, cwd=os.getcwd(), env=EFFECTIVE, capture_output=True, text=True, errors="replace",
                           stdin=subprocess.DEVNULL, timeout=120)
    except subprocess.TimeoutExpired:
        return "Command timed out after 2m 0.0s", 124
    except OSError as e:
        return str(e), 127
    return (p.stdout + p.stderr).strip(), p.returncode


def work(call):
    """A call's work, as Claude Code's tool does it: (its result's text, whether it failed)."""
    name, given = call.name, call.input if isinstance(call.input, dict) else {}
    path = str(given.get("file_path") or "")
    if name == "Bash":
        output, code = shell(str(given.get("command", "")))
        return (output, False) if code == 0 else (f"Exit code {code}\n{output}", True)
    if name == "Read":
        if not os.path.isfile(path):
            return "File does not exist.", True
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, False
    if name in ("Write", "Edit", "MultiEdit"):
        existed = os.path.exists(path)
        if name == "Write":
            text = str(given.get("content", ""))
        else:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for e in (given.get("edits") or []) if name == "MultiEdit" else [given]:
                old, new = str(e.get("old_string", "")), str(e.get("new_string", ""))
                text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return (f"The file {path} has been updated successfully." if existed else
                f"File created successfully at: {path}"), False
    if name == "Glob":
        import glob
        found = sorted(os.path.normpath(f) for f in glob.glob(
            os.path.join(str(given.get("path") or os.getcwd()), str(given.get("pattern", ""))), recursive=True))
        return "\n".join(found) if found else "No files found", False
    if name == "Grep":
        try:
            pattern = re.compile(str(given.get("pattern", "")))
        except re.error as e:
            return f"Invalid regular expression: {e}", True
        base = str(given.get("path") or os.getcwd())
        files = [base] if os.path.isfile(base) else []
        for folder, folders, names in os.walk(base):
            folders[:] = sorted(f for f in folders if f != ".git")
            files += [os.path.join(folder, n) for n in sorted(names)]
        found = []
        for f in files:
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    if pattern.search(fh.read()):
                        found.append(os.path.normpath(f))
            except OSError:
                continue
        return (f"Found {len(found)} files\n" + "\n".join(found)) if found else "No files found", False
    if name == "LS":
        where = str(given.get("path") or os.getcwd())
        return "\n".join(sorted(os.listdir(where))), False
    if name.startswith("mcp__"):
        server, _, tool = name[5:].partition("__")
        return _mcp_call(tool, given) if server == sdk_server() else ("ok", False)
    if name == "EndConversation":
        return "The conversation has ended.", False
    return "ok", False                      # WebFetch, WebSearch, TodoWrite, NotebookEdit: done, with no network


def work_call(name, given, mod=None):
    """A call of a task's attempt's: its input checked as the real one checks it, its work done, and the real one's
    permission step."""
    return Call(name, given, mod=mod, run=work, permit="rules", check=check)


def own_shell(call):
    """The "run <command>" word's call's work, as that word has always run it."""
    p = subprocess.run(str(call.input.get("command", "")), shell=True, capture_output=True, text=True)
    return (p.stdout + p.stderr).strip(), p.returncode != 0


def _object(text):
    try:
        got = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def _here(path):
    """A path an action names: under its folder unless it is absolute, nothing in it resolved."""
    return os.path.join(os.getcwd(), path.strip())


def _number(text, default=0.0):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def parse_call(text):
    """The call an action makes, its modifier with it, or None when it makes none."""
    mod = None
    word, _, rest = text.strip().partition(" ")
    if word in MODIFIERS:
        mod = word
        word, _, rest = rest.strip().partition(" ")
    if word == "bash":
        return work_call("Bash", {"command": rest}, mod)
    if word in ("write", "append") and "=" in rest:
        path, body = rest.split("=", 1)
        path = _here(path)
        if word == "append" and os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read() + body
        return work_call("Write", {"file_path": path, "content": body}, mod)
    if word == "read":
        return work_call("Read", {"file_path": _here(rest)}, mod)
    if word == "edit" and "=>" in rest.partition("=")[2]:
        path, _, change = rest.partition("=")
        old, _, new = change.partition("=>")
        return work_call("Edit", {"file_path": _here(path), "old_string": old, "new_string": new}, mod)
    if word == "glob":
        return work_call("Glob", {"pattern": rest}, mod)
    if word == "grep":
        pattern, _, where = rest.partition(" ")
        return work_call("Grep", {"pattern": pattern, "path": _here(where) if where.strip() else os.getcwd()}, mod)
    if word == "tool":
        name, _, given = rest.strip().partition(" ")
        return work_call(name, _object(given), mod)
    if word == "mcp":
        tool, _, given = rest.strip().partition(" ")
        return work_call(f"mcp__{OWN}__{tool}", _object(given), mod)
    own = {"save": ("save_progress", {"note": rest}), "complete": ("complete_step", {"note": rest}),
           "state": ("task_state", {}), "ask": ("ask_owner", {"question": rest})}.get(word)
    return work_call(f"mcp__{OWN}__{own[0]}", own[1], mod) if own else None


def rate_limit(words):
    """A rate-limit event, as 2.1.274 sends one: "<status> <type> [resets=<seconds from now>|resets=none]
    [overage=<status>] [overage_resets=<seconds from now>] [overage_reason=<reason>] [using]"."""
    parts = words.split()
    info = {"status": parts[0] if parts else "allowed", "rateLimitType": parts[1] if len(parts) > 1 else "five_hour",
            "resetsAt": int(time.time()) + 3600, "utilization": 0.95}
    for part in parts[2:]:
        if part == "resets=none":
            info.pop("resetsAt", None)
        elif part.startswith("resets="):
            info["resetsAt"] = int(time.time()) + int(_number(part[7:]))
        elif part.startswith("overage="):
            info["overageStatus"] = part[8:]
        elif part.startswith("overage_resets="):
            info["overageResetsAt"] = int(time.time()) + int(_number(part[15:]))
        elif part.startswith("overage_reason="):
            info["overageDisabledReason"] = part[15:]
        elif part == "using":
            info["isUsingOverage"] = True
    send({"type": "rate_limit_event", "uuid": str(uuid.uuid4()), "session_id": SID, "rate_limit_info": info})


def server_tool():
    """An assistant message with a server tool's block and its result's (a web search, run on Anthropic's side), which
    no hook of any kind sees: the SDK takes the first as a ServerToolUseBlock and passes over the second."""
    sid = "srvtoolu_stand_in_" + uuid.uuid4().hex[:20]
    content = [{"type": "server_tool_use", "id": sid, "name": "web_search", "input": {"query": "x"}},
               {"type": "web_search_tool_result", "tool_use_id": sid,
                "content": [{"type": "web_search_result", "url": "https://example.com/", "title": "x",
                             "encrypted_content": "", "page_age": None}]}]
    mid = new_mid()
    send({"type": "assistant", "session_id": SID, "parent_tool_use_id": None,
          "message": {"id": mid, "role": "assistant", "model": "stand-in", "content": content}})
    transcribe(content, mid=mid)


def gone():
    """Gone at once, mid-turn: its output flushed and closed, the control channel with it, as a claude that died."""
    with lock:
        try:
            sys.stdout.flush()
            os.close(sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        os._exit(0)


def alone(call):
    """A call in a message of its own, through its steps: False when the turn ended with it (a permission answer
    stopped it, or the workspace interrupted), its error result sent."""
    announce([call])
    if not handle(call) or interrupted.is_set():
        send(result("error_during_execution", True))
        return False
    return True


def act(action):
    """One action of a script; True when it ended the turn (its result sent)."""
    action = action.strip()
    word, _, rest = action.partition(" ")
    if word in ("batch", "parallel"):
        calls = [c for c in (parse_call(item) for item in rest.split(" || ")) if c is not None]
        if not calls:
            return False
        go = parallel(*calls) if word == "parallel" and len(calls) == 2 else batch(calls)
        if not go:
            send(result("error_during_execution", True))
        return not go
    if word == "ratelimit":
        rate_limit(rest)
    elif word == "sleep":
        interrupted.wait(_number(rest))
    elif word == "endconv":
        call = work_call("EndConversation", {})
        announce([call])
        handle(call)
        send(result())
        return True
    elif word == "servertool":
        server_tool()
    elif word == "close":
        gone()
    elif word not in ("skiphooks", "noups"):
        call = parse_call(action)
        if call is None:
            say(action)                     # anything else, in words
            return False
        announce([call])
        if not handle(call):
            send(result("error_during_execution", True))
            return True
    return False


def perform(actions):
    """A script's actions, in order; True when one of them ended the turn, or the workspace interrupted it (its error
    result sent)."""
    for action in actions:
        if interrupted.is_set():
            break
        if act(action):
            return True
    if interrupted.is_set():
        send(result("error_during_execution", True))
        return True
    return False


def first_answer():
    """The account file's first_answer: an action done at the start of the process's first turn, which it then ends;
    True when there is one."""
    action = (_account() or {}).get("first_answer")
    if not isinstance(action, str) or not action.strip():
        return False
    if not act(action):
        send(result())
    return True


def directives():
    """The frozen start's directives: the account file's frozen object, else $HOME/stand-in-attempt.json's."""
    own = (_account() or {}).get("frozen")
    if isinstance(own, dict):
        return own
    try:
        with open(os.path.join(HOME, "stand-in-attempt.json"), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got["frozen"] if isinstance(got, dict) and isinstance(got.get("frozen"), dict) else {}


def tried(name):
    """A call the frozen start tries first: Bash `ls`, a Read of README.md, Glob `*` or a Write of x.txt (in its
    folder), Grep and LS of its folder, and any other tool with no input."""
    here = os.getcwd()
    given = {"Bash": {"command": "ls"}, "Read": {"file_path": os.path.join(here, "README.md")},
             "Glob": {"pattern": "*"}, "Write": {"file_path": os.path.join(here, "x.txt"), "content": "x"},
             "Grep": {"pattern": "x", "path": here}, "LS": {"path": here}}.get(name, {})
    return work_call(name, given)


def _at(obj, *keys):
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def ack_fields(packet):
    """resume_ack's arguments, right, from the resume packet task_state gave."""
    return {"nonce": packet.get("nonce"), "task": _at(packet, "instructions", "task", "id"),
            "step": _at(packet, "instructions", "step", "n"),
            "checkpoint": _at(packet, "information", "checkpoint", "id"),
            "done_steps": [s.get("n") for s in _at(packet, "information", "steps_done") or [] if isinstance(s, dict)],
            "done_effects": [e.get("id") for e in _at(packet, "information", "effects", "done") or []
                             if isinstance(e, dict)],
            "next_step": _at(packet, "next", "step")}


def wrong(field, value):
    """A value of resume_ack's `field` other than the packet's, of the type its schema takes (a string field "x", a
    step 100 more, a list [999], or ["999"] for the effects' ids, which are strings)."""
    if field in ("step", "next_step"):
        return (value if isinstance(value, int) else 0) + 100
    if field == "done_steps":
        return [999]
    if field == "done_effects":
        return ["999"]
    return "x"


def acknowledge(right, directive):
    """One resume_ack call: wrong in the directive's field the first time (and the second too, with twice), else
    right; None when the turn ended with it."""
    n, ACKS[0] = ACKS[0], ACKS[0] + 1
    given = dict(right)
    field = directive.get("wrong")
    if isinstance(field, str) and field in given and (n == 0 or n == 1 and directive.get("twice")):
        given[field] = directive["value"] if "value" in directive else wrong(field, right[field])
    call = work_call(f"mcp__{OWN}__resume_ack", given)
    return call if alone(call) else None


def close_turn(text):
    """A turn's short last words and its result, or its error result when the workspace interrupted it."""
    if interrupted.is_set():
        send(result("error_during_execution", True))
        return
    say(text)
    send(result())


def frozen(first):
    """The frozen start's turn: on the first, the calls its directives try; then (on every turn) the acknowledgement
    they say; then a short text and the turn's result."""
    d = directives()
    ack = d.get("ack", "right")
    directive = ack if isinstance(ack, dict) else {}
    if first:
        for name in d.get("try") or []:
            if not alone(tried(str(name))):
                return
        interrupted.wait(_number(d.get("delay")))       # a frozen start slow to read the task's state
    if ack == "none" and not first:
        return close_turn("I leave the task's state as it is.")
    state = work_call(f"mcp__{OWN}__task_state", {})
    if not alone(state):
        return
    if ack == "exit":
        gone()
    if ack == "none":
        return close_turn("I read the task's state.")
    try:
        packet = json.loads(state.text) if state.ran and not state.error else None
    except ValueError:
        packet = None
    if not isinstance(packet, dict):
        return close_turn("The task's state could not be read.")
    right = ack_fields(packet)
    call = acknowledge(right, directive)
    if call is None:
        return
    if isinstance(directive.get("wrong"), str) and call.ran and "does not match" in call.text:
        if acknowledge(right, directive) is None:
            return
    close_turn("I acknowledged the task's state.")


def attempt_turn(text, first):
    """A turn of a task's attempt: frozen, as its directives say; resumed with RESUMED as its first message, the script
    its first message's first paragraph is; any other message, its own first paragraph's."""
    try:
        actions = []
        if FROZEN and first:
            with open(os.path.join(HOME, f"stand-in-first-{SID}.txt"), "w", encoding="utf-8") as fh:
                fh.write(text)
        elif not FROZEN:
            script = text
            if first and RESUME and text == RESUMED:
                try:
                    with open(os.path.join(HOME, f"stand-in-first-{SID}.txt"), encoding="utf-8") as fh:
                        script = fh.read()
                except OSError:
                    script = ""
            actions = [a.strip() for a in re.split(r"\n\s*\n", script, 1)[0].split(" ;; ") if a.strip()]
        ups = not BARE
        if actions and actions[0] in ("skiphooks", "noups"):
            ups, TURN["quiet"] = False, actions[0] == "noups"
            actions = actions[1:]
        send(start_message())
        if ups:
            prompt_hooks(text)
        if first and first_answer():
            return
        if FROZEN:
            frozen(first)
        elif not perform(actions):
            say("Worked.")
            send(result())
    except Exception:  # noqa: BLE001  the stand-in's own failure ends its turn, said on stderr, and never hangs it
        import traceback
        traceback.print_exc()
        try:
            send(result("error_during_execution", True))
        except Exception:  # noqa: BLE001
            pass


def api_key_source():
    """The system init's apiKeySource: apiKeyHelper when its --settings name one, ANTHROPIC_API_KEY when the session's
    environment has that, else none; the account file's init_api_key_source instead when it has one (null: none
    reported)."""
    account = _account() or {}
    if "init_api_key_source" in account:
        return account["init_api_key_source"]
    flag = _settings()
    if isinstance(flag, dict) and flag.get("apiKeyHelper"):
        return "apiKeyHelper"
    return "ANTHROPIC_API_KEY" if EFFECTIVE.get("ANTHROPIC_API_KEY") else "none"


def start_message():
    """A turn's system init, as the real one's, with the credential source it reports."""
    source = api_key_source()
    return dict({"type": "system", "subtype": "init", "session_id": SID, "model": "stand-in", "cwd": os.getcwd(),
                 "tools": ["Bash"], "claude_code_version": VERSION},
                **({"apiKeySource": source} if source is not None else {}))


def account_answer():
    """The initialize answer's account, as the real one reports it: the account file's, else the defaults (a key's,
    with no email, organisation or plan, when its --settings name an apiKeyHelper; an apiKeySource of ANTHROPIC_API_KEY
    and a tokenSource of CLAUDE_CODE_OAUTH_TOKEN when the session's environment has those); None when the file says
    no_account."""
    account = _account() or {}
    if account.get("no_account"):
        return None
    flag = _settings()
    key = isinstance(flag, dict) and bool(flag.get("apiKeyHelper"))
    got = {"email": None if key else "stand-in@example.com", "organization": None,
           "plan": None if key else "Claude Max",
           "token_source": "CLAUDE_CODE_OAUTH_TOKEN" if EFFECTIVE.get("CLAUDE_CODE_OAUTH_TOKEN") else None,
           "api_key_source": "apiKeyHelper" if key else "ANTHROPIC_API_KEY" if EFFECTIVE.get("ANTHROPIC_API_KEY")
           else None, "api_provider": "firstParty"}
    got.update({k: account[k] for k in got if k in account})
    out = {"email": got["email"], "organization": got["organization"], "subscriptionType": got["plan"],
           "tokenSource": got["token_source"], "apiKeySource": got["api_key_source"],
           "apiProvider": got["api_provider"]}
    if account.get("no_email"):
        out.pop("email")
    return {k: v for k, v in out.items() if v is not None}


def turn(text):
    interrupted.clear()
    TURN["quiet"] = False
    TURNS_SEEN[0] += 1
    if ATTEMPT:
        attempt_turn(text, TURNS_SEEN[0] == 1)
        return
    send(start_message())
    if not BARE:
        prompt_hooks(text)          # the SDK's UserPromptSubmit callbacks, where it registered any
    if TURNS_SEEN[0] == 1 and first_answer():
        return
    # an agent's first message is its brief and then paragraphs of its own: the brief says what to do
    whole, text = text, text.split("\n\n", 1)[0]
    if text.startswith("[") and "] " in text:
        # "[command] what to do": a process left running in the background first, as a Bash call's `&`
        command, text = text[1:].split("] ", 1)
        subprocess.Popen(command, shell=True, start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    if text.startswith("later "):
        seconds, text = text[6:].split(" ", 1)
        interrupted.wait(float(seconds))
    if text == "hello":
        say("Hello from the stand-in" + (f", resumed {RESUME}" if RESUME else ""))
    elif text.startswith("run ") and (hooked() or commanded()):
        # through the SDK's callbacks: the PreToolUse ones, then the ask as below, the command, the post ones; or, in a
        # chat with none, through its settings' and its plugins' command hooks first, where a test made
        # $HOME/stand-in-command-hooks, as the real one runs those before its permission step
        call = Call("Bash", {"command": text[4:]}, run=own_shell, permit="ask")
        announce([call])
        if not handle(call):
            send(result("error_during_execution", True))
            return
        say("done")
    elif text.startswith("run "):
        command = text[4:]
        counter[0] += 1
        tid = f"toolu_stand_in_{counter[0]}"
        if PARTIAL:
            stream({"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": tid,
                                                                                 "name": "Bash", "input": {}}})
            stream({"type": "content_block_stop", "index": 0})
        send({"type": "assistant", "session_id": SID, "parent_tool_use_id": None,
              "message": {"role": "assistant", "model": "stand-in", "content": [
                  {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}]}})
        transcribe([{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}])
        answer = ask({"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": command},
                      "tool_use_id": tid, "permission_suggestions": [
                          {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": command}],
                           "behavior": "allow", "destination": "localSettings"}]})
        if answer is not None:
            with open(os.path.join(HOME, "stand-in-answers.jsonl"), "a") as fh:
                fh.write(json.dumps(answer) + "\n")
        if answer is None or answer.get("interrupt"):          # a deny that also stops the turn, as the CLI does
            send(result("error_during_execution", True))
            return
        if answer.get("behavior") == "allow":
            p = subprocess.run(command, shell=True, capture_output=True, text=True)
            output, error = (p.stdout + p.stderr).strip(), p.returncode != 0
        else:
            output, error = answer.get("message", ""), True
            DENIALS.append({"tool_name": "Bash", "tool_use_id": tid, "tool_input": {"command": command}})
        send({"type": "user", "session_id": SID, "parent_tool_use_id": None,
              "message": {"role": "user", "content": [
                  {"type": "tool_result", "tool_use_id": tid, "content": output, "is_error": error}]}})
        transcribe_result(tid, output, error)
        say("done")
    elif text.startswith("stream "):
        # thinks first, writes in small pieces with a pause between, and a sub-agent's writing goes by on the side
        words = text[7:]
        if PARTIAL:
            stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
            stream({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hm"}})
            stream({"type": "content_block_stop", "index": 0})
            stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                   parent="toolu_sub")
            stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "aside"}},
                   parent="toolu_sub")
        say(words, pieces=[words[i:i + 3] for i in range(0, len(words), 3)], pause=0.02)
    elif text.startswith("sleep "):
        interrupted.wait(float(text[6:]))
        say("slept")
    elif text == "wait" or text.startswith("wait "):
        interrupted.wait(int(text[5:]) if text.startswith("wait ") else 30)
        send(result("error_during_execution", True))
        return
    elif text.startswith("ask ") and hooked():
        # through the SDK's callbacks, then the ask when no rule allows it, as the real one asks for any tool
        rules = args[args.index("--allowedTools") + 1].split(",") if "--allowedTools" in args else []
        call = Call("mcp__proofpane__ask_owner", {"question": text[4:]},
                    run=lambda c: _mcp_call("ask_owner", c.input),
                    permit=None if "mcp__proofpane__ask_owner" in rules else "ask")
        announce([call])
        if not handle(call) or not call.ran:
            send(result("error_during_execution", True))
            return
        say("I asked the owner and wait for their answer.")
    elif text.startswith(("search ", "propose ")) and hooked():
        # as below, through own_tool, whose call goes through the SDK's callbacks
        tool = "search_mcp" if text.startswith("search ") else "propose_mcp"
        given = text.split(" ", 1)[1]
        answer = own_tool(tool, {"query": given} if tool == "search_mcp" else {"name": given, "why": "it needs it"})
        if answer is None:
            send(result("error_during_execution", True))
            return
        say(answer)
    elif text.startswith("ask "):
        question = text[4:]
        allowed = args[args.index("--allowedTools") + 1].split(",") if "--allowedTools" in args else []
        if "mcp__proofpane__ask_owner" not in allowed:
            # not allowed by a rule: the real one asks, as for any tool
            got = ask({"subtype": "can_use_tool", "tool_name": "mcp__proofpane__ask_owner",
                       "input": {"question": question}, "tool_use_id": "toolu_stand_in_ask", "permission_suggestions": []})
            if not got or got.get("behavior") != "allow":
                send(result("error_during_execution", True))
                return
        answer = call_tool("ask_owner", {"question": question})
        tool_turn("mcp__proofpane__ask_owner", {"question": question}, answer)
        say("I asked the owner and wait for their answer.")
    elif text.startswith(("search ", "propose ")):
        # an agent run's own tools for MCP servers (agents.py), allowed by its rules as ask_owner is
        tool = "search_mcp" if text.startswith("search ") else "propose_mcp"
        allowed = args[args.index("--allowedTools") + 1].split(",") if "--allowedTools" in args else []
        if f"mcp__proofpane__{tool}" not in allowed:
            send(result("error_during_execution", True))
            return
        given = text.split(" ", 1)[1]
        arguments = {"query": given} if tool == "search_mcp" else {"name": given, "why": "it needs it"}
        answer = call_tool(tool, arguments)
        tool_turn(f"mcp__proofpane__{tool}", arguments, answer)
        say(answer)
    elif text.startswith("wait_run, for the run "):
        # a run's wait on its run over, its answer the run's next message: the stand-in says the answer, after a
        # moment's work, as the real one takes
        interrupted.wait(1)
        say(text.split("\n", 1)[1])
    elif text == "agents" or re.match(r"^(dispatch(\*\d+|wait2|wait|send)?|waitrun|sendrun) ", text):
        answer = together(text)
        if answer is None:
            send(result("error_during_execution", True))
            return
        say(answer)
    elif re.match(r"^(fact|seefact|payloadfact|catpayloadfact|lookup|retract) ", text):
        word, rest = text.split(" ", 1)
        parts = rest.split("|")
        if word == "seefact":
            tool_turn("Bash", {"command": "cat status"}, parts.pop(0))
        if word in ("payloadfact", "catpayloadfact"):
            named = re.search(r"\.proofpane-uploads/trigger-[0-9a-f]{12}\.json", whole)
            path = os.path.join(os.getcwd(), named.group(0)) if named else ""
            if word == "payloadfact":
                tool_turn("Read", {"file_path": path}, open(path).read() if named else "")
            else:                                   # read with a shell command, as a model may
                tool_turn("Bash", {"command": f"cat {named.group(0) if named else ''}"},
                          open(path).read() if named else "")
        if word == "lookup":
            tool, arguments = "lookup_fact", {"subject": parts[0], "relation": parts[1] if len(parts) > 1 else ""}
        elif word == "retract":
            tool, arguments = "retract_fact", {"id": parts[0], "reason": parts[1] if len(parts) > 1 else ""}
        else:
            parts += [""] * 5
            tool, arguments = "record_fact", {"subject": parts[0], "relation": parts[1], "object": parts[2],
                                              "valid_at": parts[3], "source": parts[4]}
        answer = own_tool(tool, arguments)
        if answer is None:
            send(result("error_during_execution", True))
            return
        say(answer)
    elif text.startswith("work "):
        # one call, then its words and the skill folders it was given (a sandbox arm's, an agent's)
        tool_turn("Bash", {"command": "npm test"}, "ok")
        skills = sorted(os.listdir(os.path.join(".claude", "skills"))) if os.path.isdir(os.path.join(".claude", "skills")) else []
        window = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
        say("Worked: " + text[5:] + (" (skills: " + ", ".join(skills) + ")" if skills else "") +
            (f" (window: {window})" if window else ""))
    elif text.startswith("Review this agent"):
        review()
    elif text.startswith("Does this proposed change to an agent"):
        say(json.dumps({"specific": "SPECIFIC" in whole, "why": "stand-in critic"}))
    elif text.startswith("Grade two outputs of an agent"):
        say(json.dumps(pair_grades(whole)))
    elif text.startswith("fetch "):
        tool_turn("WebFetch", {"url": text[6:], "prompt": "read it"}, "a page")
        say("fetched")
    elif text.startswith("repeat "):
        times, command = text[7:].split(" ", 1)
        for _ in range(int(times)):
            tool_turn("Bash", {"command": command}, "same")
        say("repeated")
    elif text.startswith("Grade this run"):
        final = whole.split("FINAL MESSAGE:", 1)[-1]
        grade = 0.9 if "GOOD" in final else 0.75
        say(json.dumps({"task_fit": 0.3 if "BAD" in final else grade, "grounding": grade, "completeness": grade,
                        "hygiene": grade, "actionable": grade, "why": "stand-in grades"}))
    elif text.startswith("Your output failed its acceptance check"):
        say('Done again: {"labels": ["bug"], "risk_tier": "low"}')
    elif text.startswith("memory "):
        folder = json.loads(args[args.index("--settings") + 1]).get("autoMemoryDirectory", "") if "--settings" in args else ""
        for op in text[7:].split("&"):
            op = op.strip()
            if op.startswith("read "):
                path = os.path.join(folder, op[5:])
                tool_turn("Read", {"file_path": path}, open(path).read() if os.path.exists(path) else "no such file")
            elif op.startswith("write ") and "=" in op:
                rel, body = op[6:].split("=", 1)
                path = os.path.join(folder, rel)

                def write(path=path, body=body):    # first, or once the SDK's callbacks let the call through
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w") as fh:
                        fh.write(body)
                    return "written", False
                tool_turn("Write", {"file_path": path, "content": body}, "written", run=write)
        say("memory done")
    elif text == "readitems":
        # a re-verify run reads the items its message names, and says how many and the first one's path
        named = re.search(r"\.proofpane-uploads/reverify-[0-9a-f]{12}\.json", whole)
        path = os.path.join(os.getcwd(), named.group(0)) if named else ""
        body = open(path).read() if named and os.path.exists(path) else ""
        tool_turn("Read", {"file_path": path}, body)
        items = json.loads(body)["items"] if body else []
        say(f"{len(items)} items, the first {items[0].get('path') if items else 'none'}")
    elif text == "readnamed":
        question = whole.split("\n\n")[1] if "\n\n" in whole else ""
        for token in question.split():
            token = token.strip(".,;:")
            if "/" in token and "." in token:
                tool_turn("Read", {"file_path": os.path.join(os.getcwd(), token)}, "the file's text")
        say("I read what it named.")
    elif text.startswith("subrepeat "):
        # a sub-agent that makes the same Bash call n times, inside the turn: its calls reach the SDK as whole
        # assistant messages whose parent is the Agent call (measured on 2.1.274), never as stream events
        times, command = text[10:].split(" ", 1)
        counter[0] += 1
        agent_tid = f"toolu_stand_in_{counter[0]}"
        send({"type": "assistant", "session_id": SID, "parent_tool_use_id": None,
              "message": {"role": "assistant", "model": "stand-in", "content": [
                  {"type": "tool_use", "id": agent_tid, "name": "Agent",
                   "input": {"description": "repeat", "prompt": command}}]}})
        for _ in range(int(times)):
            counter[0] += 1
            tid = f"toolu_stand_in_{counter[0]}"
            send({"type": "assistant", "session_id": SID, "parent_tool_use_id": agent_tid,
                  "message": {"role": "assistant", "model": "stand-in", "content": [
                      {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}]}})
            send({"type": "user", "session_id": SID, "parent_tool_use_id": agent_tid,
                  "message": {"role": "user", "content": [
                      {"type": "tool_result", "tool_use_id": tid, "content": "ok", "is_error": False}]}})
        send({"type": "user", "session_id": SID, "parent_tool_use_id": None,
              "message": {"role": "user", "content": [
                  {"type": "tool_result", "tool_use_id": agent_tid, "content": "Sub-agent report: done",
                   "is_error": False}]}})
        say("The sub-agent reported.")
    elif text.startswith("subagent "):
        words = text[9:]
        if os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") == "1":
            tool_turn("Agent", {"description": words, "prompt": words}, subagent_report(words))
            say("The sub-agent reported.")
        else:
            # in the background: launched, the turn ends, and the report comes in a later turn of its own
            tool_turn("Agent", {"description": words, "prompt": words, "run_in_background": True},
                      "Launched in the background.")
            say("A sub-agent works on it in the background.")
            send(result())
            time.sleep(0.5)
            send(start_message())
            say(subagent_report(words))
    elif text == "fail" or text.startswith("fail "):
        say("Something went wrong.")
        send(result(text[5:] if text.startswith("fail ") else "error_during_execution", True))
        return
    elif text.startswith("steps "):
        # a turn of so many steps, ended where --max-turns says, as the real one ends it
        steps = int(text[6:])
        most = int(args[args.index("--max-turns") + 1]) if "--max-turns" in args else None
        say("working")
        if most is not None and steps > most:
            send(result("error_max_turns", True, turns=most))
        else:
            send(result(turns=steps))
        return
    elif text.startswith("The workspace: you are the manager of one of the owner's requirements"):
        # an attempt of a requirement's manager: its packet after the brief, the owner's words of its requirement
        # escaped there (a line feed as \x0a), each line of them that begins "manager: ctool " a call, in order
        packet = json.loads(whole.split("\n\n", 1)[1])
        lines = packet["instructions"]["words"]["text"].replace("\\x0a", "\n").split("\n")
        gave = []
        for line in lines:
            if line.startswith("manager: ctool "):
                name, _, given = line[len("manager: ctool "):].partition("|")
                arguments = json.loads(given or "{}")
                answer, failed = _mcp_call(name, arguments)
                tool_turn(f"mcp__proofpane__{name}", arguments, answer, error=failed)
                gave.append(("refused: " if failed else "") + answer)
            elif line.strip() == "manager: wait":
                interrupted.wait(60)            # works on, as an attempt does, until interrupted or ended
        say(" | ".join(gave) or "Nothing to do.")
    elif text.startswith("ctool "):
        name, _, given = text[6:].partition("|")
        allowed = args[args.index("--allowedTools") + 1].split(",") if "--allowedTools" in args else []
        if f"mcp__proofpane__{name}" not in allowed:
            send(result("error_during_execution", True))
            return
        arguments = json.loads(given or "{}")
        answer, failed = _mcp_call(name, arguments)
        tool_turn(f"mcp__proofpane__{name}", arguments, answer, error=failed)
        say(("refused: " if failed else "") + answer)
    elif text == "compact":
        send({"type": "system", "subtype": "compact_boundary", "session_id": SID, "uuid": str(uuid.uuid4()),
              "compact_metadata": {"trigger": "auto", "pre_tokens": 1000}})
        say("Compacted.")
    elif text.startswith("ratelimit "):
        rate_limit(text[10:])
        say("noted")
    else:
        say("You said: " + whole)
    send(result())


working = [False]          # a turn under way


def worker():
    while True:
        item = turns.get()
        working[0] = True
        try:
            turn(item)
        finally:
            working[0] = False


threading.Thread(target=worker, daemon=True).start()
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("type") == "control_request":
        sub = msg["request"].get("subtype")
        if sub == "interrupt":
            interrupted.set()
            with open(os.path.join(HOME, "stand-in-order.jsonl"), "a") as fh:
                fh.write(json.dumps("interrupt") + "\n")
        if sub in ("set_model", "set_permission_mode", "mcp_toggle"):     # changed while it runs
            with open(os.path.join(HOME, "stand-in-controls.jsonl"), "a") as fh:
                fh.write(json.dumps(msg["request"]) + "\n")
        if sub == "set_permission_mode" and isinstance(msg["request"].get("mode"), str):
            MODE[0] = msg["request"]["mode"]
        if sub == "mcp_toggle":
            keep_toggle(msg["request"].get("serverName"), bool(msg["request"].get("enabled")))
            for server in MCP:
                if server["name"] == msg["request"].get("serverName"):
                    server["status"] = "connected" if msg["request"].get("enabled") else "disabled"
        if sub == "initialize" and isinstance(msg["request"].get("hooks"), dict):
            HOOKS.update(msg["request"]["hooks"])     # its hook callbacks, kept as the real one keeps them
        if sub == "initialize" and CONFIG.get("INIT_DELAY"):
            time.sleep(CONFIG["INIT_DELAY"])          # a claude slow to start, as a real one can be
        answer = dict({"commands": []}, **({"models": CONFIG["MODELS"]} if "MODELS" in CONFIG else {}))
        reported = account_answer() if sub == "initialize" else None
        if reported is not None:
            answer["account"] = reported              # the account it runs on, as the real one reports it
        response = answer if sub == "initialize" else {"mcpServers": MCP} if sub == "mcp_status" else {}
        send({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"],
                                                          "response": response}})
    elif msg.get("type") == "control_response":
        r = msg.get("response", {})
        with open(os.path.join(HOME, "stand-in-order.jsonl"), "a") as fh:
            fh.write(json.dumps("answer") + "\n")
        slot = answers.get(r.get("request_id"))
        if slot is not None:
            slot[1] = r.get("response")
            slot[0].set()
    elif msg.get("type") == "user":
        content = msg["message"]["content"]
        if ATTEMPT or FROZEN:               # a task's attempt, or a frozen start, writes down each message first
            with open(os.path.join(HOME, "stand-in-received.jsonl"), "a") as fh:
                fh.write(json.dumps({"sid": SID, "frozen": FROZEN,
                                     "text": content if isinstance(content, str) else json.dumps(content)}) + "\n")
        turns.put(content if isinstance(content, str) else json.dumps(content))
# stdin closed: the turn it was given is answered first, as the real one answers a one-shot query whose input the SDK
# ends as soon as it is written; a turn that only waits ("wait", "sleep", "later") ends at once, as at an interrupt
interrupted.set()
ends_at = time.time() + 30
while (working[0] or not turns.empty()) and time.time() < ends_at:
    time.sleep(0.02)
# a normal exit, where the session's totals are saved
if CONFIG.get("RESTORES"):
    with open(SAVED, "w") as fh:
        json.dump(SPENT, fh)
