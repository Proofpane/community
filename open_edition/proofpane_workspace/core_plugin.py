"""Proofpane's core joined as a Claude Code plugin: `core_plugin/`, the `proofpane-core`
plugin shipped beside this package, given to each claude terminal and each chat the owner starts here with
`--plugin-dir`, once the owner has paired the workspace with their Proofpane organisation; until then nothing of it is
given and nothing changes. An agent's run and a task's attempt are not given it: a run may be a replay, which must not
act outside the machine (gate.py's replay rules), and a task's attempt starts frozen, with no settings of its own and
the workspace's MCP server alone (task_attempt.py), which a plugin's hooks, skills and server would undo. One pairing
serves the whole workspace: the sessions of every Claude Code account in it report to the one organisation, each row
naming its account (an added account's id, else `default`, which a session on another provider's model is named too).
Beside the plugin's rows, the workspace agent itself posts each reply's token counts to the same organisation, through
this pairing, for the claude, codex and chat sessions, the agents' runs and the tasks' attempts alike, the plugin given
or not (usage_report.py says what it sends and when).

What the plugin carries (core_plugin/): two core skills, `/proofpane-core:ai-governance-intake` and
`/proofpane-core:ai-risk-assessment`, the governance pack's own (their text as the pack has it, a name and a description
added for Claude Code); Proofpane's own Claude Code hook, as a small client of its own
(core_plugin/hooks/proofpane_hook.py, which says what it sends, how it holds a call, that it also asks the workspace's
gate, and how it asks Proofpane's budget check before each prompt and holds a prompt the check refuses); and, as a
second plugin beside it (core_plugin/daemon/, `proofpane-core-daemon`), Proofpane's MCP server, the daemon's own
governed tools, each call put first to the organisation's Proofpane policy for the agent named `default`
(core_plugin/daemon/mcp.py says what that refuses): named only on the person's own machine (the local provider) and only
where the daemon is found where its installer puts it first (DAEMONS; one run from elsewhere, from its Mac app's own
folder say, is not looked for), so that a session never lists a server that cannot start; its launcher switches off the
daemon's import of the machine's other MCP servers into the organisation's registry and its poll of that registry
(core_plugin/daemon/mcp.py says why). A workspace machine has no daemon (its image holds the workspace agent and this
plugin, nothing of daemon/), so there the MCP server is never given, and the page says so.

The pairing, as the daemon pairs a computer (`airgov_daemon pair`): the owner makes a pairing code in their Proofpane
organisation and gives it here with Proofpane's address (https, or http to this machine for a Proofpane run here); the
workspace exchanges it at `<address>/api/v1/devices/pair` for a device and its token, and keeps them in its own record,
`~/.proofpane/proofpane-core/config.json` in the workspace user's home (0600, its folder 0700), in the daemon's own
form, with the owner's holds beside them. Never in a session's folder or in claude's environment, never given back to
the page, which learns the device's name and id, the address, when it was paired and the holds. That is not the least
exposure the workspace could give the token. A relay in the agent, holding it in the agent's memory alone, as the
workspace holds its secret for signing a run's record and the asks switch, would let a session post rows and approval
requests through it, and not take the token itself to Proofpane's other device routes, to another machine, or past the
workspace's life. It is not built: the daemon's MCP server, on the person's own machine, reads the token from this
record, and whether a session's program could read the agent's memory on the workspace's machine was not measured. The
plugin's hook reads it there at each event; the daemon's MCP server, where it is given, is pointed at its folder
(AIRGOV_CONFIG_DIR, which the commands of that server's own tools are run with); the gate refuses a tool call that names
the folder, as it does the Linear key's file. What a program of the session can still do: it runs as the same user, so
it can read the record by a path the gate does not recognise (a glob, a variable), and use the token as the device it
is: post rows and approval requests to that organisation, and reach whatever else Proofpane's device routes give a
paired device (its document store and its MCP registry among them), until the device is revoked in Proofpane. Once it is
revoked or paused there, or given a new token there, which never reaches the workspace (nor the daemon), every hold goes
to Claude Code's own prompt, saying Proofpane refused the device, and every row is dropped; the page still says paired
until the owner unpairs here. A paused device is resumed in Proofpane, but a revoked one cannot be restored there
(Proofpane revokes with no route back), nor an old token brought back once its grace is past (five minutes unless the
rotation set another), so after either the owner unpairs here and pairs again with a new code. The hooks are
cooperative, as the daemon's are on a person's own computer, and a repository's own settings, or the user's own (the
account folder's `settings.json`, which an owner's session can write), can turn the plugin off (`"enabledPlugins":
{"proofpane-core@inline": false}`, plugins loading page) or, in a workspace machine, whose managed settings do not pin
`disableAllHooks`, its hooks; the page cannot see any of these. The budget check before each prompt is the plugin's
hook, so it is cooperative the same way, and a session of another program has no such hook. Unpairing removes the
record here, and with it the plugin from every start after; it does not reach a session already running, whose hooks
find no record from their next event and do nothing, and whose Proofpane MCP server on the person's own machine,
which reads the pairing at each call, then runs the daemon's tools with no approval and no audit until that claude
starts again. Nor does it revoke the device, which only an admin of the organisation does, in Proofpane (Devices), as
the daemon's own `unpair` revokes nothing either. Pairing again makes a new device and leaves the old one until it is
revoked there.

Where an organisation's managed settings set `disableSideloadFlags`, Claude Code exits at `--plugin-dir` ("--plugin-dir
is disabled by your organization's managed settings (disableSideloadFlags)", plugins troubleshooting page, read
2026-10-18), and it refuses the Agent SDK's `plugins` option the same way (plugins org page). So before each start the
workspace reads what it can of the managed settings that start will apply (managed): the machine's managed settings
files and their drop-in folder, a Mac's managed preferences, and the cache of the organisation's server-managed settings
in the start's account folder (`remote-settings.json`, server-managed settings page: "the cache at
~/.claude/remote-settings.json"; in an added account's folder by the same rule, which was read, not measured). A `true`
in any of them, whichever Claude Code would rank first, leaves the plugin out of that start, and the page says so. What
this cannot see: an organisation's settings fetched for the first time at that very start, before any cache exists, and
a policy a helper program computes. A terminal whose claude then exits says Claude Code's own words there, and a later
start reads the cache once Claude Code has written it; a chat says Claude Code's error, and where the organisation's
settings need someone's approval (a hook, an `env`), a chat, being an Agent SDK run, does not write the cache (from
Claude Code 2.1.207, server-managed settings page), so on that account each chat start can meet the same refusal until
a terminal session there has approved the settings once. The cache is a file in the
account's folder, which a session can write: a `true` a session put there leaves the plugin out as the organisation's
would, and the page cannot tell them apart. `allowManagedHooksOnly` and `disableAllHooks` are read the same way: under
either, Claude Code loads the plugin but runs none of its hooks, so the page says nothing of those sessions' hooks
reaches the owner's Proofpane (Proofpane's MCP server, where it is given, is no hook and still reports).

Claude Code takes `--plugin-dir` from 2.1.74 at the latest (the changelog's first line naming it), a hook given as a
program and its arguments from 2.1.139 (settings.py), async hooks from 2.1.23 at the latest and the PostToolUseFailure
event from 2.1.119 at the latest (the changelog's first lines naming them); the workspace starts no claude older than
2.1.139 (workspace.CLAUDE_ARGS_FLOOR, read at each start), so every claude it starts takes all four, and no other read
of the version is made for the plugin (TAKES_IT_FROM).
`disableSideloadFlags` exists from 2.1.193; an older claude ignores it, and the plugin is still left out where a source
sets it.

Standard library only; the app runs the agent as a process and never imports this.
"""

import http.client
import json
import os
import platform
import plistlib
import pwd
import re
import socket
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.normpath(os.path.join(HERE, "..", "core_plugin"))
DAEMON_PLUGIN = os.path.join(PLUGIN, "daemon")
NAME = "proofpane-core"
RECORD = os.path.join(".proofpane", "proofpane-core")         # in the workspace user's home
CONFIG = "config.json"
DEFAULT_CLOUD = "https://api.proofpane.com"
TIMEOUT = 20
MAX_HOLDS = 20
MAX_HOLD = 200

# the Claude Code that takes what the plugin gives (the module's docstring)
PLUGIN_DIR_FROM = (2, 1, 74)
EXEC_HOOKS_FROM = (2, 1, 139)
ASYNC_HOOKS_FROM = (2, 1, 23)
FAILURE_HOOKS_FROM = (2, 1, 119)
TAKES_IT_FROM = max(PLUGIN_DIR_FROM, EXEC_HOOKS_FROM, ASYNC_HOOKS_FROM, FAILURE_HOOKS_FROM)
SIDELOAD_FROM = (2, 1, 193)

# where the Proofpane daemon installs itself, in its installer's order; core_plugin/daemon/mcp.py's own list
DAEMONS = ("/opt/proofpane/airgov_daemon", "/opt/homebrew/bin/airgov_daemon", "/usr/local/bin/airgov_daemon",
           "~/.local/bin/airgov_daemon")

# Claude Code's managed settings on a machine (managed settings page, read 2026-10-18): the file and its drop-in folder
# in the system folder (Linux and WSL, then a Mac), and a Mac's managed preferences, the machine's and the user's
MANAGED_FOLDERS = ("/etc/claude-code", "/Library/Application Support/ClaudeCode")
PREFERENCES = "/Library/Managed Preferences"
PREFERENCES_FILE = "com.anthropic.claudecode.plist"
REMOTE = "remote-settings.json"
# tests only: the folders read in place of the machine's, each as a managed folder that may also hold the preferences
MANAGED_ENV = "PP_CLAUDE_MANAGED"
KEYS = ("disableSideloadFlags", "allowManagedHooksOnly", "disableAllHooks")
MAX_READ = 1024 * 1024

_CLOUD = re.compile(r"^(https://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?|http://(?:127\.0\.0\.1|localhost|\[::1\])"
                    r"(?::[0-9]{1,5})?)/?$")
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,38}[A-Z0-9]$")
_DEVICE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{16,512}$")
_WHEN = re.compile(r"^[0-9T:.+Z-]{10,40}$")


class CoreError(ValueError):
    """What the owner can put right, or what Proofpane said: shown as it is."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, never followed: the pairing code goes in the body, and a redirect would carry the next
    request where it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Proofpane answered with a redirect, not followed.", headers,
                                     fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def record_dir(home):
    return os.path.join(home, RECORD)


def config_path(home):
    return os.path.join(record_dir(home), CONFIG)


def read(home):
    """The pairing kept, as written (cloud_url, device_id, token, name, paired_at, holds), or None: no file, one that
    cannot be read, or one whose fields are not what pair writes."""
    try:
        with open(config_path(home), "rb") as fh:
            got = json.loads(fh.read(MAX_READ).decode("utf-8"))
    except Exception:  # noqa: BLE001  as _json: whatever is there, a start goes on
        return None
    if not isinstance(got, dict):
        return None
    if not (isinstance(got.get("cloud_url"), str) and _CLOUD.match(got["cloud_url"])
            and isinstance(got.get("device_id"), str) and _DEVICE.match(got["device_id"])
            and isinstance(got.get("token"), str) and _TOKEN.match(got["token"])):
        return None
    holds = got.get("holds") if isinstance(got.get("holds"), list) else []
    return {"cloud_url": got["cloud_url"], "device_id": got["device_id"], "token": got["token"],
            "name": got["name"][:120] if isinstance(got.get("name"), str) else None,
            "paired_at": got["paired_at"] if isinstance(got.get("paired_at"), str) and _WHEN.match(got["paired_at"])
            else None,
            "holds": [h for h in holds if isinstance(h, str)][:MAX_HOLDS]}


def _write(home, value):
    folder = record_dir(home)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(folder), 0o700)
    os.chmod(folder, 0o700)
    path = config_path(home)
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=1)
    os.replace(tmp, path)


def check_cloud(address):
    """Proofpane's address as kept: https with no path, or http to this machine; its trailing slash taken off."""
    address = address.strip() if isinstance(address, str) else address
    if not isinstance(address, str) or not _CLOUD.match(address):
        raise CoreError("Proofpane's address is https://, a host and a port at most (https://api.proofpane.com), or "
                        "http:// to this machine for a Proofpane run here.")
    return address.rstrip("/")


def check_holds(holds):
    """The owner's holds: at most MAX_HOLDS patterns, each a Python regular expression of at most MAX_HOLD characters,
    matched against a call's command; none holds nothing."""
    if holds in (None, ""):
        return []
    if isinstance(holds, str):
        holds = holds.splitlines()
    if not isinstance(holds, list) or not all(isinstance(h, str) for h in holds):
        raise CoreError("Holds are a list of patterns.")
    out = [h.strip() for h in holds if h.strip()]
    if len(out) > MAX_HOLDS:
        raise CoreError(f"At most {MAX_HOLDS} holds.")
    for h in out:
        if len(h) > MAX_HOLD:
            raise CoreError(f"A hold is at most {MAX_HOLD} characters.")
        try:
            re.compile(h)
        except re.error as e:
            raise CoreError(f"A hold is a regular expression: {h[:60]} does not read as one ({e}).") from None
        if _newer_syntax(h):
            raise CoreError(f"A hold may not use an atomic group or a possessive quantifier ({h[:60]}): they came "
                            "with Python 3.11, and the python3 a session runs the plugin's hook with may be older, "
                            "where the hold would hold nothing.")
        if _nested_repeat(h):
            raise CoreError(f"A hold may not repeat what itself repeats ({h[:60]}), as (a+)+ does: matching such a "
                            "pattern can take a time that grows exponentially with the command, and a call it is slow "
                            "on waits out the hook's budget and is then refused.")
    return out


_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")


def _ops(items, parser):
    """Every (op, argument) of a pattern's parse, those inside groups, branches and lookarounds with them."""
    for op, av in items:
        yield op, av
        for a in (av if isinstance(av, (list, tuple)) else (av,)):
            if isinstance(a, parser.SubPattern):
                yield from _ops(a, parser)
            elif isinstance(a, (list, tuple)):
                for b in a:
                    if isinstance(b, parser.SubPattern):
                        yield from _ops(b, parser)


def _nested_repeat(pattern):
    """Whether a pattern repeats something that itself repeats ((a+)+, (x*)*, (?:ab+)+c), the shape of the patterns
    whose match can take a time growing exponentially with the text. The hook's timer stops a match at its budget, but
    the call waits for it and is then refused, so the workspace refuses this shape when a hold is set, where the owner
    sees why. Not every slow pattern has it ((a|a)+$ has not, nor .*a.*b, whose time grows with a power of the text);
    the manual says so."""
    parser = getattr(re, "_parser", None)
    if parser is None:                  # before Python 3.11
        import sre_parse as parser
    try:
        # an outer repetition of at most one (?, {0,1}) cannot backtrack through its inner one more than once
        return any(str(op) in _REPEATS and av[1] > 1 and any(str(o) in _REPEATS for o, _ in _ops(av[2], parser))
                   for op, av in _ops(parser.parse(pattern), parser))
    except Exception:  # noqa: BLE001  a parse this Python cannot do is a pattern re.compile refused already
        return False


def _newer_syntax(pattern):
    """Whether a pattern this Python compiles uses what Python 3.11 brought to its regular expressions, an atomic group
    or a possessive quantifier, which the hook (Python 3.8 or later) could not compile and would skip: read from the
    pattern's parse (re._parser, from 3.11; an older Python cannot compile either, so it refuses them itself)."""
    parser = getattr(re, "_parser", None)
    if parser is None:
        return False
    found = []

    def walk(items):
        for op, av in items:
            if str(op) in ("ATOMIC_GROUP", "POSSESSIVE_REPEAT"):
                found.append(op)
            for a in (av if isinstance(av, (list, tuple)) else (av,)):
                if isinstance(a, parser.SubPattern):
                    walk(a)
                elif isinstance(a, (list, tuple)):
                    walk_any(a)

    def walk_any(items):
        for a in items:
            if isinstance(a, parser.SubPattern):
                walk(a)
            elif isinstance(a, (list, tuple)):
                walk_any(a)

    try:
        walk(parser.parse(pattern))
    except Exception:  # noqa: BLE001  a parse this Python cannot do is a pattern re.compile refused already
        return False
    return bool(found)


def device_name():
    return f"Proofpane Community workspace on {socket.gethostname()}"[:120]


def _platform():
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def _ask(url, body):
    """One POST to Proofpane: its answer, or CoreError with what Proofpane or the network said."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "proofpane-workspace"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            got = json.loads(r.read(256 * 1024).decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise CoreError("Proofpane answered with a redirect, which is not followed.") from None
        try:
            said = json.loads(e.read(65536).decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            said = None
        detail = said.get("detail") if isinstance(said, dict) else None
        detail = detail[:300] if isinstance(detail, str) else None
        if e.code in (400, 404):
            raise CoreError(f"Proofpane did not take the code: {detail or e.code}. Make a new one in Proofpane "
                            "(Devices) and give it within ten minutes.") from None
        raise CoreError(f"Proofpane answered {e.code}{': ' + detail if detail else ''}.") from None
    except (urllib.error.URLError, OSError, ValueError, RecursionError, http.client.HTTPException) as e:
        # the reason's kind only: an error's own text can carry what was sent; an answer nested too deep for json
        # (RecursionError) is no answer, as one cut short is
        why = e.reason if isinstance(e, urllib.error.URLError) and isinstance(e.reason, (str, OSError)) else e
        raise CoreError(f"Proofpane could not be reached ({type(why).__name__}).") from None
    if not isinstance(got, dict):
        raise CoreError("Proofpane answered with something that is not a pairing.")
    return got


def pair(home, code, cloud_url=None, holds=None):
    """Pair the workspace: the code exchanged for a device and its token, kept in the record (replacing one kept);
    the page is given the status, never the token."""
    code = code.strip().upper() if isinstance(code, str) else code
    if not isinstance(code, str) or not _CODE.match(code):
        raise CoreError("A pairing code is the one Proofpane shows (Devices, Pair a device), such as ABC-DEF-GHJ.")
    cloud = check_cloud(cloud_url or DEFAULT_CLOUD)
    holds = check_holds(holds)
    got = _ask(cloud + "/api/v1/devices/pair", {"code": code, "name": device_name(), "platform": _platform()})
    device, token = got.get("device_id"), got.get("token")
    if not (isinstance(device, str) and _DEVICE.match(device) and isinstance(token, str) and _TOKEN.match(token)):
        raise CoreError("Proofpane's answer held no device and token the workspace can keep.")
    name = got.get("name") if isinstance(got.get("name"), str) else device_name()
    when = got.get("paired_at") if isinstance(got.get("paired_at"), str) and _WHEN.match(got["paired_at"]) else \
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write(home, {"cloud_url": cloud, "device_id": device, "token": token, "name": name[:120], "paired_at": when,
                  "holds": holds})


def set_holds(home, holds):
    """The owner's holds changed, the pairing kept."""
    kept = read(home)
    if kept is None:
        raise CoreError("Pair the workspace with Proofpane first.")
    _write(home, dict(kept, holds=check_holds(holds)))


def unpair(home):
    """The record removed: every start after this one is given nothing of the plugin. The daemon's own files beside it
    (its MCP server's) are left as they are."""
    try:
        os.unlink(config_path(home))
    except FileNotFoundError:
        pass


def find_daemon(home):
    """The first of DAEMONS that is a file this user can run, or None."""
    for place in DAEMONS:
        path = os.path.join(home, place[2:]) if place.startswith("~/") else place
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _json(path):
    """A settings file's JSON, or None for anything that is not there or does not read as JSON: any error, since a
    session can write some of these files (an account's cache) and one that raised here would stop every start."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_READ + 1)
        if len(raw) > MAX_READ:
            return None
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001  a file nested too deep, say (RecursionError)
        return None


def _plist(path):
    """A managed preferences file, or None, on the same terms as _json."""
    try:
        with open(path, "rb") as fh:
            return plistlib.load(fh)
    except Exception:  # noqa: BLE001  an XML one that does not parse raises expat's own error
        return None


def _anywhere(value, key, depth=0):
    """Whether `key` is set to true in `value`, at any depth (the cache's own shape is not documented)."""
    if depth > 8:
        return False
    if isinstance(value, dict):
        if value.get(key) is True:
            return True
        return any(_anywhere(v, key, depth + 1) for v in value.values())
    if isinstance(value, list):
        return any(_anywhere(v, key, depth + 1) for v in value)
    return False


def _user():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        return None


def machine_settings(env=None):
    """The machine's managed settings, as [(kind, settings)], kind "file" or "preferences": each folder's
    managed-settings.json and its managed-settings.d/*.json (in the order Claude Code merges them), and a Mac's managed
    preferences, the machine's and this user's. PP_CLAUDE_MANAGED (tests only) names the folders read in their place."""
    env = os.environ if env is None else env
    given = env.get(MANAGED_ENV)
    folders = [f for f in given.split(os.pathsep) if f] if given else list(MANAGED_FOLDERS)
    out = []
    for folder in folders:
        files = [os.path.join(folder, "managed-settings.json")]
        drop = os.path.join(folder, "managed-settings.d")
        try:
            files += [os.path.join(drop, n) for n in sorted(os.listdir(drop))
                      if n.endswith(".json") and not n.startswith(".")]
        except OSError:
            pass
        for path in files:
            got = _json(path)
            if isinstance(got, dict):
                out.append(("file", got))
        if given:
            got = _plist(os.path.join(folder, PREFERENCES_FILE))
            if isinstance(got, dict):
                out.append(("preferences", got))
    if not given:
        user = _user()
        for path in [os.path.join(PREFERENCES, PREFERENCES_FILE)] + \
                ([os.path.join(PREFERENCES, user, PREFERENCES_FILE)] if user else []):
            got = _plist(path)
            if isinstance(got, dict):
                out.append(("preferences", got))
    return out


def managed(config_dir, machine=None):
    """{key: [kind, ...]} for each of KEYS that a managed source a start with this account folder applies sets to true:
    the machine's ("file", "preferences") and the account's cached organisation settings ("organisation")."""
    machine = machine_settings() if machine is None else machine
    out = {key: [] for key in KEYS}
    for kind, settings in machine:
        for key in KEYS:
            if settings.get(key) is True and kind not in out[key]:
                out[key].append(kind)
    cached = _json(os.path.join(config_dir, REMOTE)) if config_dir else None
    for key in KEYS:
        if cached is not None and _anywhere(cached, key):
            out[key].append("organisation")
    return out


def present():
    """Whether the plugin is where the workspace runs from: its manifest and its hooks there."""
    return os.path.isfile(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")) and \
        os.path.isfile(os.path.join(PLUGIN, "hooks", "hooks.json"))


def given(home, config_dir, local, machine=None):
    """The plugin folders a claude start with this account folder is given, each named with --plugin-dir, and why none
    when none ("not paired", "missing", "refused"): proofpane-core once paired, where no managed source sets
    disableSideloadFlags; with the daemon's companion beside it on the person's own machine (`local`) where the daemon
    is found."""
    if read(home) is None:
        return [], "not paired"
    if not present():
        return [], "missing"
    if managed(config_dir, machine)["disableSideloadFlags"]:
        return [], "refused"
    out = [PLUGIN]
    if local and find_daemon(home):
        out.append(DAEMON_PLUGIN)
    return out, None


def plugin_args(dirs):
    """The command line's part for the folders `given` names: --plugin-dir and its folder, one pair each (a claude
    from 2.1.76 takes one folder a flag)."""
    out = []
    for d in dirs:
        out += ["--plugin-dir", d]
    return out


def skills():
    """The core skills the plugin carries, by name."""
    try:
        return sorted(n for n in os.listdir(os.path.join(PLUGIN, "skills"))
                      if os.path.isfile(os.path.join(PLUGIN, "skills", n, "SKILL.md")))
    except OSError:
        return []


def status(home, local, accounts):
    """What the page shows: whether the workspace is paired and as which device, never the token; what each start is
    given; and, for each Claude Code account (`accounts`, {id: its folder}), what the managed settings leave out."""
    kept = read(home)
    out = {"paired": kept is not None, "plugin": present(), "name": NAME, "skills": skills()}
    if kept is not None:
        out.update(cloud_url=kept["cloud_url"], device_id=kept["device_id"], device_name=kept["name"],
                   paired_at=kept["paired_at"], holds=kept["holds"])
    if not local:
        out["mcp"] = {"given": False, "why": "machine"}         # a workspace machine has no Proofpane daemon
    elif find_daemon(home) is None:
        out["mcp"] = {"given": False, "why": "no daemon"}
    else:
        out["mcp"] = {"given": kept is not None, "why": None if kept is not None else "not paired"}
    machine = machine_settings()
    out["accounts"] = []
    for account, folder in accounts.items():
        found = managed(folder, machine)
        if any(found.values()):
            out["accounts"].append({"account": account, "refused": found["disableSideloadFlags"],
                                    "hooks_off": sorted(set(found["allowManagedHooksOnly"] +
                                                            found["disableAllHooks"]))})
    return out
