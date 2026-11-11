#!/usr/bin/env python3
"""A Claude Code PreToolUse hook that holds a session's gated commands for the owner's approval in the ADE of
Proofpane Community.

One file, standard library only, so it can be copied to the machine a session runs on. Wire it in the repository's
.claude/settings.local.json (`ade_hook.settings.json` beside this file is the block to add, its `env` and
`disableAllHooks` included; `ade_link_and_hook.settings.json` holds it and the stop's block in one), as a program and
its arguments, a form Claude Code's settings declare from 2.1.139 and which Claude Code 2.1.274 starts with no shell
(proofpane_workspace/settings.py), so that in 2.1.274 a CLAUDE_CODE_SHELL_PREFIX that a settings file the session
loads sets cannot wrap it. Its interpreter is named by its own path and started with -X utf8, Python's UTF-8 mode:
under a locale whose character set Python has no codec for, which a settings file can name, the interpreter ends
before this file runs, with exit 1, which holds nothing, and in UTF-8 mode it does not (measured on Linux with the
three of glibc 2.39's supported locales of that kind; not on a Mac):

    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/absolute/path/to/python3",
                    "args": ["-I", "-X", "utf8", "/absolute/path/to/open_edition/ade_hook.py"], "timeout": 90}]}]

Beside it the block gives a command line that only checks that the interpreter is an executable file and this file is
there, and exits 2 when either is not, which Claude Code takes as a denial: a hook it cannot start is no decision, so
without the line a path mistyped would let every gated command go on.

Settings, from the environment. The block's `env` sets each of them, the first two to the owner's values and the
others empty, which here means the default, so a repository's own settings cannot set them:

    PROOFPANE_ADE_URL          the ADE, such as https://ade.example.org (HTTPS; plain HTTP only to this machine)
    PROOFPANE_ADE_TOKEN_FILE   the file holding the hook token the owner created for this session in the ADE
                               (default ~/.proofpane-ade/hook-token); PROOFPANE_ADE_TOKEN may hold it instead
    PROOFPANE_ADE_GATE         a regular expression: a Bash command it finds is held, as is one GATE (below) finds, so
                               it can only add to GATE; empty, GATE alone holds
    PROOFPANE_ADE_WINDOWS      a channel directory in the session's checkout: a command sent in a window (it ends
                               ` # go <start>`) is held only while a window file there starts at <start> and has not
                               ended, and its request lasts until that window's end
    PROOFPANE_ADE_WAIT         seconds to wait for the owner in one call: 50 by default and at most (WAIT), since a
                               hook the client kills at its `timeout` prints nothing, and the client reads that as allow;
                               once a command is known to be gated, the hook has until DEADLINE (75) seconds from its
                               start, and at that deadline denies it
    PROOFPANE_ADE_PROXY        an HTTP proxy, http://host:port, through which an https ADE is reached; none by default
    PROOFPANE_ADE_CA_FILE      a file of the certificate authorities trusted to have issued the ADE's certificate; by
                               default those in the places the interpreter's OpenSSL was built to look

To reach the ADE the hook uses no proxy the environment names (HTTPS_PROXY, ALL_PROXY, NO_PROXY and the others urllib
reads) and no certificates SSL_CERT_FILE or SSL_CERT_DIR name. A settings file's `env` can set those, and through a
proxy and a certificate authority of a repository's own, an answer that seemed to come from the ADE, an approval
included, could come from the repository.

What it answers for a Bash command:

    not gated                            nothing: the command goes on as it would without this hook
    gated, approved                      allow; the approval is used up by this run
    gated, denied, expired or used       deny, with the owner's words or the reason
    gated, no answer yet                 deny, saying the request stays open: sending the same command again finds
                                         the same request, so an approval given in between is picked up
    gated, but the ADE cannot be reached, or the hook has no URL or token
                                         deny: a gated command is never let through because the check failed
    gated, and no answer within DEADLINE seconds of the hook's start
                                         deny, saying so: what the hook does once it knows the command is gated (its
                                         reads of a window file and the token file, its requests) runs in a thread of
                                         its own given the time left before that deadline, and what still runs then
                                         (a pipe or a file system that does not answer, a name lookup no socket timeout
                                         bounds) is left behind, the process leaving through os._exit once it has
                                         answered. The search of the command by the owner's own PROOFPANE_ADE_GATE
                                         comes before, and is not bounded: a search that runs past the deadline and
                                         finds the command ends in this denial, but one that runs past the block's
                                         `timeout` lets the command go on, so a pattern of the owner's that a command
                                         can make slow to search is a way past what it adds (GATE is searched first,
                                         and what it finds is held whatever the owner's pattern does)

Anything that goes wrong before the hook knows whether a command is gated lets it go on, so a broken hook does not
trap a session on commands the owner did not gate. Two things are denied instead: input the hook cannot read as one
JSON object, empty input included (main), since a command that cannot be read cannot be judged; and a
PROOFPANE_ADE_GATE that is not a regular expression, which denies every Bash command, since what it was meant to add
cannot be told.

The ADE ships no block for Codex's own configuration, so neither this hook nor the link's stop hook (ade_link.py)
holds a Codex session's commands. Codex runs managed hooks, giving each a Bash call as Claude Code does and taking
Claude Code's deny answer, which is how the workspace's own gate reaches it in a workspace machine
(proofpane_workspace/settings.py records it, with what was measured with Codex 0.163.0); nothing measured says a
project's or a user's Codex configuration would run a block such as this one, none is given to it here, and its
sessions keep their own approval policy.
"""

import functools
import json
import math
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# The hook's start, for the deadline: taken once this file's imports are done, so the interpreter's own start and those
# imports come before it, a fraction of a second, which the block's `timeout` of 90 leaves 15 for. The __main__ block,
# at the end of this file, gives it to main.
STARTED = time.monotonic()

GATE = (r"\bgit\s+push\b|\bgh\s+(?:pr\s+merge|release)\b"
        r"|\bfly(?:ctl)?\s+(?:deploy|secrets|ssh|apps\s+destroy|volumes\s+destroy|machines?\s+destroy)\b"
        r"|\b(?:npm\s+publish|twine\s+upload|docker\s+push)\b|\bterraform\s+(?:apply|destroy)\b"
        r"|\bkubectl\s+(?:apply|delete)\b|\brm\s+-\w*(?:rf|fr)\w*\b")
_GATE = re.compile(GATE)
GO = re.compile(r"#\s*go\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)\s*$")
WINDOW = re.compile(r"^window: (\S+) to (\S+)\s*$", re.M)
URL = re.compile(r"https://[A-Za-z0-9.-]+(?::\d+)?|http://(?:127\.0\.0\.1|localhost):\d+")
PROXY = re.compile(r"http://([A-Za-z0-9.-]+:\d+)/?")     # PROOFPANE_ADE_PROXY: an HTTP proxy, no password
POLL = 2.0
HTTP_TIMEOUT = 8
WAIT = 50.0               # the longest one call waits for the owner: PROOFPANE_ADE_WAIT may shorten it, not lengthen it
# The deadline for what the hook does once it knows a command is gated, counted from its start (STARTED), inside the
# block's `timeout` of 90: the wait's cap alone did not bound it, since it is counted from
# the first request's return; a request is bounded only per socket operation, not as a whole, and not in its name
# lookup, which takes no timeout; and a read of the token file or a window file takes none either (a pipe in its place,
# or a file system that does not answer). So all of that runs in a thread of its own given the time left (decide).
DEADLINE = 75.0


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "Held by the ADE: " + reason}}


def _allow(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "permissionDecisionReason": "Approved in the ADE: " + reason}}


def _time(text):
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ" if text.count(":") == 2 else "%Y-%m-%dT%H:%MZ"
        return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


class Late(Exception):
    """The run's deadline came before the hook had its answer."""


def _late():
    """The denial past the deadline: a gated command is never let through because the hook ran out of time."""
    return _deny(f"the hook had no answer within {DEADLINE:g} seconds of its start; nothing was let through. Send the "
                 "same command again to ask again.")


def _within(seconds, fn):
    """(finished, value, error) for fn run in a thread of its own for at most `seconds`, as ade_link._within does;
    finished is False when it is still running then. It is left running, as a daemon, and ends with the process, which
    leaves through os._exit once it has answered (the end of this file): unlike a signal, this bounds what is blocked
    in C, such as a name lookup that does not answer or the read of a pipe no one writes to."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001  handed to the caller
            box["error"] = e

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(max(seconds, 0))
    if worker.is_alive():
        return False, None, None
    return True, box.get("value"), box.get("error")


class BadGate(ValueError):
    """PROOFPANE_ADE_GATE is set but is not a regular expression."""


def gated(command, env):
    """(True, window start or None) when the command is held, else (False, None). It is held when GATE finds it or the
    gate pattern the owner set (PROOFPANE_ADE_GATE) does, so that pattern can only add to GATE; one that does not
    compile raises BadGate. (Before, the owner's pattern took GATE's place once it found twenty sample commands, one
    for each of GATE's alternatives, and one that found those and less than GATE let through what GATE holds, such as
    `git push origin feature`.)"""
    go = GO.search(command)
    if go:
        return True, go.group(1)
    own = env.get("PROOFPANE_ADE_GATE") or ""
    try:
        pattern = re.compile(own) if own else None
    except re.error:
        raise BadGate() from None
    return bool(_GATE.search(command) or (pattern is not None and pattern.search(command))), None


def window_end(start, env, now):
    """The end of the window file under PROOFPANE_ADE_WINDOWS that starts at `start`: (end text, None), or (None,
    why the command may not be sent in that window). Without the setting, (None, None): no window is checked."""
    directory = env.get("PROOFPANE_ADE_WINDOWS")
    if not directory:
        return None, None
    try:
        names = sorted(n for n in os.listdir(directory) if re.fullmatch(r"GO(?:-\d+)?\.md", n))
    except OSError:
        return None, f"the window directory {directory} cannot be read"
    for name in names:
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                lines = WINDOW.findall(fh.read())
        except OSError:
            continue
        if len(lines) == 1 and lines[0][0] == start:
            end = _time(lines[0][1])
            if end is None:
                return None, f"{name} has a window line with a time that cannot be read"
            if now >= end:
                return None, f"the window in {name} ended at {lines[0][1]}"
            return lines[0][1], None
    return None, f"no window file in {directory} starts at {start}"


def _tls(env):
    """The TLS context the ADE's certificate is checked with: the certificates in the file PROOFPANE_ADE_CA_FILE names,
    else those in the places this interpreter's OpenSSL was built to look (get_default_verify_paths's openssl_cafile and
    openssl_capath), never those SSL_CERT_FILE or SSL_CERT_DIR name. Given places, create_default_context loads them
    and nothing else. With neither place there, it trusts no certificate, and a gated command is denied."""
    ca = env.get("PROOFPANE_ADE_CA_FILE") or ""
    if ca:
        return ssl.create_default_context(cafile=ca)
    paths = ssl.get_default_verify_paths()
    cafile = paths.openssl_cafile if paths.openssl_cafile and os.path.isfile(paths.openssl_cafile) else None
    capath = paths.openssl_capath if paths.openssl_capath and os.path.isdir(paths.openssl_capath) else None
    if cafile or capath:
        return ssl.create_default_context(cafile=cafile, capath=capath)
    return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _call(method, url, token, body=None, env=None, timeout=HTTP_TIMEOUT):
    """One request to the ADE: through PROOFPANE_ADE_PROXY when it names a proxy and the ADE is https, else straight
    there, never through a proxy the environment names, and over TLS checked with _tls. `timeout` bounds each socket
    operation, not the request as a whole, nor its name lookup (decide bounds those)."""
    env = os.environ if env is None else env
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "proofpane-ade-hook"})
    proxy = PROXY.fullmatch(env.get("PROOFPANE_ADE_PROXY") or "")
    if proxy and request.type == "https":
        # the request names the ADE as its Host: set_proxy makes request.host the proxy's, and urllib takes the Host
        # from it when the request is opened, so a server that routes by the Host (Caddy) would answer for none
        request.add_unredirected_header("Host", request.host)
        request.set_proxy(proxy.group(1), "http")       # a CONNECT tunnel: the proxy sees the host, never the request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPSHandler(context=_tls(env)))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait(env):
    """The seconds one call waits for the owner: PROOFPANE_ADE_WAIT, WAIT when it is empty, and never more than WAIT
    or less than 0. None when it is not a number."""
    try:
        wait = float(env.get("PROOFPANE_ADE_WAIT") or WAIT)
    except ValueError:
        return None
    return None if math.isnan(wait) else min(max(wait, 0.0), WAIT)


def _token(env):
    if env.get("PROOFPANE_ADE_TOKEN"):
        return env["PROOFPANE_ADE_TOKEN"].strip()
    path = env.get("PROOFPANE_ADE_TOKEN_FILE") or os.path.expanduser("~/.proofpane-ade/hook-token")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def decide(payload, env=None, call=None, sleep=time.sleep, clock=time.monotonic, now=None, started=None):
    """The hook's answer for one PreToolUse payload: a dict to print, or None to say nothing. Each request goes
    through `call`, by default _call with this env. Once the command is known to be gated, all the hook does runs in a
    thread of its own given the time left before the deadline, DEADLINE seconds from `started` (the __main__ block
    gives main the hook's start, which main passes on; by default, this call's own), and each request is given that
    time as its timeout too."""
    env = os.environ if env is None else env
    call = call or functools.partial(_call, env=env)
    began = clock() if started is None else started
    try:
        if payload.get("tool_name") != "Bash":
            return None
        command = (payload.get("tool_input") or {}).get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        held, start = gated(command, env)
    except BadGate:
        return _deny("PROOFPANE_ADE_GATE is not a regular expression, so no command can be judged; fix it or unset it.")
    except Exception:  # noqa: BLE001  before gating is known, never trap the session
        return None
    if not held:
        return None
    deadline = began + DEADLINE

    def ask(method, url, token, body=None):
        """One request through `call`, given the time left before the deadline as its timeout (at most HTTP_TIMEOUT,
        which bounds each socket operation of the request, not the request); with none left, Late. The request as a
        whole is bounded with the rest of the gated part, below."""
        left = deadline - clock()
        if left <= 0:
            raise Late()
        return call(method, url, token, body, timeout=min(HTTP_TIMEOUT, left))

    def answer():
        """The answer for the gated command: whatever goes wrong, it is denied, never let through."""
        try:
            moment = now or datetime.now(timezone.utc)
            until = None
            if start is not None:
                until, why = window_end(start, env, moment)
                if why:
                    return _deny(why + "; nothing was sent.")
            base = (env.get("PROOFPANE_ADE_URL") or "").rstrip("/")
            token = _token(env)
            if not URL.fullmatch(base) or not token:
                return _deny("the hook has no ADE address (PROOFPANE_ADE_URL, HTTPS) or no hook token; nothing was "
                             "sent.")
            if env.get("PROOFPANE_ADE_PROXY") and not PROXY.fullmatch(env["PROOFPANE_ADE_PROXY"]):
                return _deny("PROOFPANE_ADE_PROXY is not http://host:port; nothing was sent.")
            wait = _wait(env)
            if wait is None:
                return _deny("PROOFPANE_ADE_WAIT is not a number of seconds; nothing was sent.")
            view = ask("POST", base + "/api/ade/hook/requests", token, {
                "command": command, "tool": "Bash", "session": payload.get("session_id"), "cwd": payload.get("cwd"),
                "until": until})
            end = clock() + wait                    # the wait for the owner, counted from the request's return
            while True:
                state = view.get("state")
                # a poll starts only while its pause and one request's timeout (HTTP_TIMEOUT, a bound on each socket
                # operation, not on the request) fit before the deadline: so a run the ADE slowed down still answers
                # that the request stays open, unless one of its requests takes longer, which ends in the late denial
                if state == "pending" and clock() < end and clock() + POLL + HTTP_TIMEOUT <= deadline:
                    sleep(POLL)
                    view = ask("GET", base + "/api/ade/hook/requests/" + view["id"], token)
                    continue
                if state == "approved":
                    return _allow(view.get("words") or "approved once, for this run")
                if state == "pending":
                    until_text = datetime.fromtimestamp(int(view["until"]),
                                                        tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    return _deny(f"no answer yet. The request stays open until {until_text}; send the same command "
                                 "again to pick up an approval.")
                if state == "denied":
                    return _deny("the owner denied it" + (f": {view['words']}" if view.get("words") else "."))
                if state == "used":
                    return _deny("that approval was already used by an earlier run; send it again for a new one.")
                return _deny(f"the request is {state}.")
        except Late:
            return _late()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return _deny("the ADE refused the hook token (revoked, or not this deployment's); nothing was sent.")
            return _deny(f"the ADE answered {e.code}; nothing was sent.")
        except Exception as e:  # noqa: BLE001  gated: anything that goes wrong denies
            return _deny(f"the ADE could not be asked ({type(e).__name__}); nothing was sent.")

    # From here on the command is gated. All of it, the reads of a window file and the token file as well as each
    # request, runs in a thread of its own given the time left before the deadline (_within), so a pipe no one writes
    # to, or a file system that does not answer, in place of either file cannot hold the hook past it any more than a
    # request can; with no time left, nothing is started.
    left = deadline - clock()
    if left <= 0:
        return _late()
    finished, value, error = _within(left, answer)
    if not finished:
        return _late()
    if error is not None:           # only what answer does not catch, which is no Exception
        raise error
    return value


def main(started=None):
    """Claude Code's PreToolUse input, read as bytes and parsed as JSON in UTF-8, never through the character set of the
    locale, which a settings file's `env` can name; input that is not one JSON object is denied, since a command that
    cannot be read cannot be judged (before, it was let through), and empty input is no JSON object (before, it was
    read as an empty one, which names no tool, and let through). The deadline counts from `started`, the hook's start,
    which the __main__ block gives (STARTED); without one, from this call, so an in-process caller, a test among them,
    is not held to the time since this file was imported."""
    started = time.monotonic() if started is None else started
    try:
        payload = json.loads(getattr(sys.stdin, "buffer", sys.stdin).read())
        if not isinstance(payload, dict):
            raise ValueError("not one JSON object")
    except Exception:  # noqa: BLE001  unreadable input: denied, never let through
        out = _deny("the hook could not read what Claude Code sent, so the command cannot be judged; nothing was sent.")
    else:
        out = decide(payload, started=started)
    if out is not None:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    code = main(STARTED)
    # out through os._exit once what was printed is flushed, as ade_link leaves: what the deadline left running in a
    # thread (a name lookup that does not answer, the read of a pipe) cannot hold the process past its answer
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(code if isinstance(code, int) else 0)
