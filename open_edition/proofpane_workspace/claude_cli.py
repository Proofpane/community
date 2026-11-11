"""What the installed Claude Code offers a chat, read from the binary itself (when claude updates, the UI's content
updates too): its version from `claude --version`, its effort levels and
permission modes from `claude --help`, and its models from the Claude Agent SDK's handshake with it (`models` in what
it answers; the help gives examples only): 2.1.277 offers Fable, which the image's 2.1.274 does not. Without a
handshake the models are a short list of aliases, beside any full name.

`ultracode` is an effort Claude Code takes from 2.1.203 on (xhigh, with Claude free to fan out sub-agents) without
listing it in its help (2.1.274 accepts it without a word and warns at an unknown one), so it is added by that rule.
`dontAsk` is not offered: it answers every question no, and a chat has someone to ask. The CLI's `manual` is the
mode a chat already calls `default` (the SDK's name for it).

When a text does not parse, the lists known to work with the image's own claude stay: the floor.
"""

import re
import subprocess
import time

FLOOR_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultracode")
FLOOR_MODES = ("default", "acceptEdits", "plan", "auto", "bypassPermissions")
MODEL_ALIASES = ("fable", "opus", "sonnet", "haiku")
ULTRACODE_FROM = (2, 1, 203)
NOT_OFFERED = ("dontAsk",)
TIMEOUT = 30

_VERSION = re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b")
# `claude --version`'s own line, as Claude Code prints it ("2.1.274 (Claude Code)"): a line of its own, so that a
# wrapper or a shim that prints something first (an address, its own version) is not read for Claude Code's
_CLI_VERSION = re.compile(r"^[ \t]*(\d+)\.(\d+)\.(\d+) \(Claude Code\)[ \t]*\r?$", re.M)
# an option's own description only: never into the next option's
_EFFORTS = re.compile(r"--effort <[a-z]+>\s+(?:(?! --[a-z])[^(])*\(([^)]*)\)")
_MODES = re.compile(r"--permission-mode <[a-z]+>\s+(?:(?! --[a-z])[^(])*\(choices:\s*([^)]*)\)")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}")


def parse_version(text):
    """(major, minor, patch) from `claude --version`, or None."""
    m = _VERSION.search(text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def cli_version(claude, env, cwd):
    """`claude --version` read where a decision rests on it (the workspace's floor, workspace.py): (version, None), or
    (None, why), why "timeout" when it did not answer within TIMEOUT seconds and "unreadable" when it could not be run
    or printed no line of its own (_CLI_VERSION) on its standard output."""
    try:
        p = subprocess.run([claude, "--version"], cwd=cwd, env=env, capture_output=True, timeout=TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "unreadable"
    m = _CLI_VERSION.search(p.stdout.decode("utf-8", "replace"))
    return (tuple(int(x) for x in m.groups()), None) if m else (None, "unreadable")


def parse_help(text):
    """(efforts, modes) read from `claude --help`, each None when its line is not found or gives nothing plain."""
    flat = " ".join((text or "").split())
    efforts = modes = None
    m = _EFFORTS.search(flat)
    if m:
        found = [w.strip() for w in m.group(1).split(",")]
        efforts = found if found and all(_WORD.fullmatch(w) for w in found) else None
    m = _MODES.search(flat)
    if m:
        found = re.findall(r'"([^"]*)"', m.group(1))
        if found and all(_WORD.fullmatch(w) for w in found):
            found = ["default" if w == "manual" else w for w in found if w not in NOT_OFFERED]
            modes = (["default"] if "default" in found else []) + [w for w in found if w != "default"]
    return efforts, modes


def capabilities(version_text, help_text, now=None):
    """What a chat may choose with this claude; `floor` says the help did not parse and the known lists stand."""
    version = parse_version(version_text)
    efforts, modes = parse_help(help_text)
    floor = efforts is None or modes is None
    efforts = list(efforts) if efforts is not None else [e for e in FLOOR_EFFORTS if e != "ultracode"]
    if (version is None or version >= ULTRACODE_FROM) and "ultracode" not in efforts:
        efforts.append("ultracode")
    return {"claude_version": ".".join(str(x) for x in version) if version else None, "efforts": efforts,
            "permission_modes": list(modes) if modes is not None else list(FLOOR_MODES),
            "model_aliases": list(MODEL_ALIASES), "floor": floor,
            "discovered_at": int(now if now is not None else time.time())}


def models_of(info):
    """The models a handshake's answer offers, as [{value, name}], less `default` (a chat's own empty choice), or None."""
    found = info.get("models") if isinstance(info, dict) else None
    if not isinstance(found, list):
        return None
    out = []
    for m in found:
        value, name = (m.get("value"), m.get("displayName")) if isinstance(m, dict) else (None, None)
        if isinstance(value, str) and value != "default" and re.fullmatch(r"[a-z][a-z0-9.-]{0,63}(?:\[1m\])?", value):
            out.append({"value": value, "name": name if isinstance(name, str) and name.strip() else value})
    return out or None


def handshake(wrapper, cwd, timeout=60):
    """The Claude Agent SDK's handshake with the claude a wrapper starts (no message is sent, nothing is signed in for
    it): what claude answers, or an exception saying why it did not. With no MCP server at all: claude starts every
    server of a .mcp.json it finds in `cwd` or above it even with no setting sources given (mcp.py), and a handshake
    needs none."""
    import asyncio
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    async def run():
        client = ClaudeSDKClient(ClaudeAgentOptions(cli_path=wrapper, cwd=cwd, strict_mcp_config=True))
        try:
            await asyncio.wait_for(client.connect(), timeout)
            return await asyncio.wait_for(client.get_server_info(), timeout) or {}
        finally:
            try:
                await asyncio.wait_for(client.disconnect(), timeout)
            except Exception:  # noqa: BLE001  it is going away either way
                pass
    return asyncio.run(run())


def run_text(claude, env, cwd, *args):
    try:
        p = subprocess.run([claude, *args], cwd=cwd, env=env, capture_output=True, timeout=TIMEOUT,
                           stdin=subprocess.DEVNULL)
        return p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def offered(claude, env, cwd):
    """What `claude`, run in `cwd` with `env`, offers (capabilities), with no SDK handshake: its version read as
    cli_version reads it, Claude Code's own line alone, so a line a wrapper or a shim prints before it, with a version
    of its own, is not taken for Claude Code's; then its help (run_text, bounded by TIMEOUT too, nothing when it does
    not answer). A version it cannot read, in time or at all, gives the floor, its help then not read, since whether
    that claude takes ultracode, which capabilities adds by its version, cannot be told. The read of a claude's levels
    that an effort is checked against in the workspace: a start's of the claude it runs (workspace._offered_by), and
    the workspace's own of its claude (discover). Not the workspace's only read of a claude's version and help:
    claude_update.check reads a candidate build's version itself, loosely (parse_version, the first x.y.z it prints),
    and its help (parse_help), to decide whether that build may be used."""
    version, _ = cli_version(claude, env, cwd)
    if version is None:
        return dict(capabilities("", ""), floor=True)
    return capabilities(".".join(str(x) for x in version), run_text(claude, env, cwd, "--help"))


def discover(claude, env, cwd, wrapper=None):
    """Run the installed claude for its version and help (offered: a version not read is the floor, its help then not
    read), and (given the wrapper that starts it) the SDK's handshake for its models, made whatever
    the version read gives; its capabilities (the floor for what cannot be read)."""
    caps = offered(claude, env, cwd)
    models = None
    if wrapper is not None:
        try:
            models = models_of(handshake(wrapper, cwd))
        except Exception:  # noqa: BLE001  the aliases stand
            models = None
    caps["models"] = models or [{"value": m, "name": m.capitalize()} for m in MODEL_ALIASES]
    caps["model_aliases"] = [m["value"] for m in caps["models"]]
    return caps
