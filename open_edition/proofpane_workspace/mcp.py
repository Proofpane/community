"""A chat's MCP servers, as the Claude app's connectors are: MCP servers from the repository's `.mcp.json`, shown with
a way to add one.

Found first, with the image's `claude` 2.1.274 driven by the Claude Agent SDK 0.2.154 (a probe in the image on
2026-09-29): Claude Code starts every server in each `.mcp.json` it finds, in the chat's folder and in every folder above
it, without the approval it asks for in a terminal (where `claude mcp list` says "Pending approval"), and does so even
with no setting sources given, as the capabilities handshake gives none. A server is a program the workspace runs, or
an address it connects to, and its start is not a tool call, so the gates never see it. `enableAllProjectMcpServers:
false` does not hold one back; `disabledMcpjsonServers` does, and given on the command line (`--settings`) it held
against a repository's own settings turning every server on (`.claude/settings.json` and `settings.local.json` with
`enableAllProjectMcpServers` and `enabledMcpjsonServers`); but it goes by name, and a server it does not name starts.

So a chat names every such server, except the ones the owner approved for its repository on the page, each approval
bound to the server's exact configuration (its fingerprint): an entry that changes, and a name two files define
differently, stay off until approved again. A `.mcp.json` this cannot read leaves the chat with no MCP server at all
(strict: only the servers passed, and none are), rather than one it could not name. The servers Claude Code keeps for
the workspace itself (`claude mcp add`: its user scope, for every repository, and its local scope, for one, which are
the owner's own) and a Claude plan's claude.ai connectors run as Claude Code runs them. The capabilities handshake runs
with none (claude_cli.handshake).
"""

import hashlib
import json
import os
import re
import shlex
import stat

from . import settings as settings_made
from . import shapes

FILE = ".mcp.json"
MAX_FILE = 1024 * 1024
MERGED_MOST = 100 * 1024     # bytes of the merged settings given as one argument (Linux refuses one of 128 KB)
APPROVALS = "mcp-approvals.json"
MAX_APPROVALS = 200             # a repository's approvals, kept newest last
SHOWN = 400                     # a command line or an address, as the page shows it
# a server added from the page: its name, and what it may be
NAME = shapes.NAME
SCOPES = {"repository": "local", "workspace": "user"}
ENV_NAME = shapes.ENV_NAME
HEADER_NAME = shapes.HEADER_NAME
MAX_ADD = 8 * 1024              # a command line, an address, or the lines of its environment or headers


class McpError(ValueError):
    pass


def files(cwd):
    """Every `.mcp.json` Claude Code reads for a chat in `cwd`: in it and in each folder above it, nearest first, along
    the path as given and as it really is."""
    out = []
    for start in dict.fromkeys((os.path.abspath(cwd), os.path.realpath(cwd))):
        here = start
        while True:
            path = os.path.join(here, FILE)
            if os.path.lexists(path) and path not in out:
                out.append(path)
            up = os.path.dirname(here)
            if up == here:
                break
            here = up
    return out


def read(path):
    """The servers one `.mcp.json` declares, {name: its configuration}; McpError when it cannot be read as JSON."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE + 1)
    except OSError as e:
        raise McpError(f"{path} could not be read ({e.strerror or type(e).__name__})") from None
    if len(raw) > MAX_FILE:
        raise McpError(f"{path} is larger than {MAX_FILE // 1024} KB")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise McpError(f"{path} is not JSON") from None
    servers = doc.get("mcpServers") if isinstance(doc, dict) else None
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        raise McpError(f"{path}: its mcpServers is not an object")
    return {str(k): v for k, v in servers.items()}


def declared(cwd):
    """The names the `.mcp.json` files for `cwd` declare, and whether one could not be read: what an agent's run holds
    back when it is not strict (agents.py)."""
    names, unreadable = set(), False
    for path in files(cwd):
        try:
            names.update(read(path))
        except McpError:
            unreadable = True
    return sorted(names), unreadable


def approved_config(cwd, approved, name):
    """A repository server's configuration, for an agent's run that names it: the one its `.mcp.json` files give it,
    alike wherever it is declared, when the owner approved that configuration in the chat panel; None otherwise, or when
    a file cannot be read."""
    found = []
    for path in files(cwd):
        try:
            servers = read(path)
        except McpError:
            return None
        if name in servers:
            found.append(servers[name])
    prints = {fingerprint(c) for c in found}
    if len(prints) != 1 or (name, next(iter(prints))) not in approved:
        return None
    return found[0]


def inherited(claude_json, repository):
    """The servers Claude Code keeps for the workspace itself, by name: its user scope, and its local scope under the
    repository's folder (`claude mcp add`). What a run that is not strict would start beside its own."""
    try:
        with open(claude_json, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    user = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    entry = projects.get(repository) if isinstance(projects.get(repository), dict) else {}
    local = entry.get("mcpServers") if isinstance(entry.get("mcpServers"), dict) else {}
    return sorted(set(user) | set(local))


def fingerprint(config):
    """A server's configuration, exactly: what an approval is bound to."""
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def shown(config):
    """What the page shows of a server: how it runs (a command line, or an address) and the names of its environment
    or its headers, never their values, which may be secrets."""
    if not isinstance(config, dict):
        return {"kind": "unknown", "line": ""}
    kind = config.get("type")
    if "command" in config and kind in (None, "stdio"):
        args = config.get("args") if isinstance(config.get("args"), list) else []
        line = shlex.join([str(config.get("command"))] + [str(a) for a in args])
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        return {"kind": "command", "line": line[:SHOWN], "names": sorted(str(k) for k in env)[:20]}
    if kind in ("http", "sse") or "url" in config:
        headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
        return {"kind": str(kind or "http"), "line": str(config.get("url", ""))[:SHOWN],
                "names": sorted(str(k) for k in headers)[:20]}
    return {"kind": str(kind)[:20] if kind else "unknown", "line": ""}


def plan(cwd, approved):
    """How a chat in `cwd` starts claude: {"off": the names held back, "strict": why no server may run, else None,
    "servers": each declared server as the page shows it}. `approved` holds the (name, fingerprint) pairs the owner
    approved for its repository."""
    found, unreadable = {}, []
    for path in files(cwd):
        try:
            servers = read(path)
        except McpError as e:
            unreadable.append(str(e))
            continue
        for name, config in servers.items():
            found.setdefault(name, []).append((path, config, fingerprint(config)))
    here = os.path.realpath(cwd)
    out, off = [], []
    for name in sorted(found):
        defs = found[name]
        prints = {fp for _, _, fp in defs}
        conflict = len(prints) > 1
        on = not conflict and (name, defs[0][2]) in approved
        if not on:
            off.append(name)
        where = [("this repository" if os.path.dirname(os.path.realpath(p)) == here else p) for p, _, _ in defs]
        out.append(dict(shown(defs[0][1]), name=name, files=where, approved=on, conflict=conflict,
                        fingerprint=None if conflict else defs[0][2]))
    return {"off": off, "strict": "; ".join(unreadable) or None, "servers": out}


def settings_value(base, off, extra=None):
    """The `--settings` a chat's claude is given: the workspace's own settings (a file's path, or none) with the
    servers held back named, and an agent run's own settings (agents.py: its memory's folder) over them; the path
    alone when there is nothing to add."""
    if not off and not extra:
        return base
    merged = {}
    if base:
        with open(base, encoding="utf-8") as fh:
            merged = json.load(fh)
        if not isinstance(merged, dict):
            raise McpError("the workspace's Claude Code settings are not an object")
    if off:
        held = merged.get("disabledMcpjsonServers")
        merged["disabledMcpjsonServers"] = sorted(set(held if isinstance(held, list) else []) | set(off))
    merged.update(extra or {})
    return json.dumps(merged, ensure_ascii=False)


def _read_json(path):
    """The JSON a regular file holds (at most MAX_FILE bytes of it), read without waiting: a FIFO a program of a session
    puts where the workspace's settings file was is no file, and a blocking open would hold the agent's loop."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        with os.fdopen(os.dup(fd), "rb") as fh:
            data = fh.read(MAX_FILE + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_FILE:
        raise ValueError("too large")
    return json.loads(data.decode("utf-8"))


def with_hooks(value, extra):
    """A chat's `--settings` value (`settings_value`'s answer: a settings file's path, the JSON text of settings, or
    none) with the hooks of `extra` ({"hooks": {event: [group, ...]}}) added after the ones it has for each event, as
    JSON text; `value` as it was when `extra` holds none, or when `value` cannot be read as settings to add to (a file
    that cannot be read, or not an object, a `hooks` that is not an object or an event's that is not a list): a start
    never gets settings that hold less than it was to get. The merged text is one argument of the start, so it is given
    only up to MERGED_MOST bytes: past that, `value` as it was (the chat starts without these hooks, as it did before
    them)."""
    added = extra.get("hooks") if isinstance(extra, dict) else None
    if not isinstance(added, dict) or not added:
        return value
    try:
        if not value:
            merged = {}
        elif value.lstrip().startswith("{"):
            merged = json.loads(value)
        else:
            merged = _read_json(value)
    except (OSError, ValueError, RecursionError, AttributeError):
        return value
    if not isinstance(merged, dict) or not isinstance(merged.get("hooks", {}), dict):
        return value
    hooks = merged.get("hooks", {})
    for event, groups in added.items():
        if not isinstance(hooks.get(event, []), list):
            return value
        hooks[event] = list(hooks.get(event, [])) + list(groups)
    merged["hooks"] = hooks
    try:
        text = json.dumps(merged, ensure_ascii=False, allow_nan=False)
    except ValueError:
        return value
    if len(text.encode("utf-8", "replace")) > MERGED_MOST:
        return value                # settings that large are one argument too long to start a claude with
    return text


def with_held(value, held):
    """A chat's `--settings` value (as with_hooks takes it) with the approval hook's settings `held` added
    (settings.with_held), as JSON text, or None when that cannot be: `value` unreadable or not settings to
    add to, or the merged text past MERGED_MOST. Never `value` as it was, as with_hooks gives it: a hosted workspace's
    chat does not start without the hook (chat.py, _connect)."""
    try:
        if not value:
            given = {}
        elif value.lstrip().startswith("{"):
            given = json.loads(value)
        else:
            given = _read_json(value)
    except (OSError, ValueError, RecursionError, AttributeError):
        return None
    merged = settings_made.with_held(given, held)
    if merged is None:
        return None
    try:
        text = json.dumps(merged, ensure_ascii=False, allow_nan=False)
    except ValueError:
        return None
    if len(text.encode("utf-8", "replace")) > MERGED_MOST:
        return None
    return text


def load_approvals(root):
    """{repository: [[name, fingerprint], ...]}, from the workspace's own file; a file that cannot be read approves
    nothing."""
    try:
        with open(os.path.join(root, APPROVALS), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(repo): [[n, f] for n, f in (pair for pair in pairs if isinstance(pair, list) and len(pair) == 2)
                        if isinstance(n, str) and isinstance(f, str)]
            for repo, pairs in data.items() if isinstance(pairs, list)}


def save_approvals(root, approvals):
    path = os.path.join(root, APPROVALS)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(approvals, fh, indent=1)
    os.replace(path + ".tmp", path)


def add_argv(claude, spec):
    """`claude mcp add` for a server the owner adds from the page: {name, scope: repository or workspace, command or
    url (with transport: http, the default, or sse for an older server), lines: NAME=value (a command's environment)
    or Name: value (an address's headers)}. The command line is split
    as a shell would split it and never run through one. Every option is given in its `--name=value` form, before the
    server's name: claude 2.1.274's `--env` and `--header` take several values each, and in the spaced form the name
    after them is taken as one more (checked in the image)."""
    spec = spec if isinstance(spec, dict) else {}
    name, scope = spec.get("name"), spec.get("scope")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise McpError("A server's name is letters, digits, '.', '_' and '-', starting with a letter or a digit, at "
                       "most 64.")
    if scope not in SCOPES:
        raise McpError("A server is added for this repository or for the whole workspace.")
    command, url, lines = spec.get("command"), spec.get("url"), spec.get("lines") or ""
    if not isinstance(lines, str) or len(lines) > MAX_ADD:
        raise McpError("Its environment or headers are text, at most 8 KB.")
    given = [x.strip() for x in lines.splitlines() if x.strip()]
    argv = [claude, "mcp", "add", f"--scope={SCOPES[scope]}"]
    if isinstance(url, str) and url.strip() and not command:
        url = url.strip()
        if len(url) > MAX_ADD or not re.fullmatch(r"https?://[^\s]+", url):
            raise McpError("An address starts with https:// (or http://) and has no spaces.")
        for line in given:
            key, sep, value = line.partition(":")
            if not sep or not HEADER_NAME.fullmatch(key.strip()) or not value.strip():
                raise McpError("Each header is one line: Name: value.")
            argv.append(f"--header={key.strip()}: {value.strip()}")
        transport = spec.get("transport") or "http"
        if transport not in ("http", "sse"):
            raise McpError("An address is reached over http or, for an older server, sse.")
        return argv + [f"--transport={transport}", name, url]
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_ADD or url:
        raise McpError("Give the command that starts the server, or its address, not both.")
    try:
        words = shlex.split(command)
    except ValueError:
        raise McpError("The command line has an unclosed quote.") from None
    for line in given:
        key, sep, value = line.partition("=")
        if not sep or not ENV_NAME.fullmatch(key.strip()):
            raise McpError("Each environment line is NAME=value.")
        argv.append(f"--env={key.strip()}={value}")
    return argv + [name, "--"] + words
