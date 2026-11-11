"""Start the workspace agent.

    python -m proofpane_workspace --root DIR --secret-file FILE [--port N] [--idle-exit SECONDS]
                                  [--idle-exit-after-runs SECONDS]
    PP_WORKSPACE_SECRET=... python -m proofpane_workspace --root DIR [--port N] [--idle-exit SECONDS]

The secret comes from a file, which is read and then deleted so that nothing but the agent's own memory holds it
afterwards (the local provider, and a workspace machine): a command line is readable by every user, and the
environment a process started with by the programs its user runs, even once the process has removed it. Or, for the
tests, from the environment, where it is removed before anything else runs so no program the agent starts inherits
it. On Linux the agent's memory is closed to those programs too (keep_memory_private). The agent listens on 127.0.0.1
only. With --port 0, the default, the system chooses the port, and the agent writes it with its process id to
DIR/agent.json, readable by this user only, for the app that started it.

With --idle-exit, the agent stops once nothing has happened for that many seconds: no input to or output from any
terminal, and no request that changes anything (reading a list does not count, so an open page does not keep a
machine running). Stopping hangs up every program it started. In a workspace machine that ends the machine, which
the fly provider starts again when the user comes back. With --idle-exit-after-runs as well, an agent that has served
no page since it started (only the app's own mirroring of the agents, and their runs) stops that many seconds after its
last run ends, with none live or waiting: a machine started for a schedule's or a hook's run costs its run and a
little more, not the idle time.

Whatever its options, the agent stops in the same way once the program that started it is gone: the app, or the tests,
ended without stopping it (killed outright, say). Left to init, it would answer nobody and run on for good, and the
app's next start would start another on the same root. PP_WORKSPACE_PARENT is that program's process id, which the
local provider and the tests give, so a program gone before the agent began to look is seen too; without it, the agent
watches the parent it has as it starts.

PP_WORKSPACE_CLAUDE_SETTINGS names a settings file every `claude` session is started with (`--settings`): the
local provider's gates. In a workspace machine the gates are in the image's managed settings instead, which a
session cannot override.

With --claude-updates (a workspace machine's start.sh too), Claude Code is kept current: at the start and once a day,
unless its mode is manual, Claude Code's own `install stable` runs, and a new build is used once it passes its check
(claude_update.py).

With --link-repos (the local provider's, never a workspace machine's), a repository already on this machine may be added
where it is (POST /repos/link): its sessions' worktrees are made from it.

With --claude-first-run (a workspace machine's start.sh, never the local provider, whose HOME is a person's own),
Claude Code's first-run choices are made in this user's ~/.claude.json before anything starts (first_run.py), so a
`claude` in a terminal opens at its prompt.

PP_WORKSPACE_ADE_HOOK names a folder (a workspace machine's start.sh gives it, never the local provider): every claude
terminal and the owner's own chats are then given open_edition/ade_hook.py as a PreToolUse hook on Bash, with this
deployment's address and the workspace's hook token from that folder, so the Bash commands on the hook's list (a git
push, a gh pr merge or release, a deploy, a publish, an rm -rf and the others: ade_hook.GATE) wait for the owner's
approval in the deployment's queue; none of them starts without it, and an agent's run, a replay and a task's attempt
are not given it (settings.ade_hook_settings, workspace.ade_hook_settings). It is removed from the
environment before anything runs, as the settings files' names are.
"""

import argparse
import asyncio
import concurrent.futures
import json
import os
import socket
import sys
import threading
import time

import uvicorn

from .agent import make_app
from .agents import records_key
from .workspace import MAX_SESSIONS, Workspace

MAX_SECRET_FILE = 4096
PR_SET_DUMPABLE = 4             # linux/prctl.h
# the threads every asyncio.to_thread of the agent's runs on (its requests' git, a program's status, a file's read): the
# event loop's default pool, of this size whatever the machine's CPUs. Python's own is min(32, CPUs + 4), five on a
# workspace machine's one CPU, though what it is given waits on programs and files rather than computing: a burst of the
# page's reads, each holding a thread while `claude auth status` ran (up to half a minute), filled five.
# 32 is the most Python's own takes on any machine
REQUEST_THREADS = 32


def keep_memory_private():
    """On Linux, close this process's memory and environment to the programs it starts, which run as its user while
    its memory holds the secret: once it is not dumpable, /proc/PID/mem and /proc/PID/environ are closed to a process
    of the same user without CAP_SYS_PTRACE (a session has none), and so is a debugger's attach. A program the agent
    starts is dumpable again from its own start (execve), so nothing of a session's changes. Elsewhere it does nothing:
    a Mac has no such call. True when it was done."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes
        return ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def read_secret_file(path):
    """The secret in `path`, which is then deleted; a file that cannot be removed is refused, since it would stay."""
    with open(path, encoding="utf-8") as fh:
        secret = fh.read(MAX_SECRET_FILE + 1)
    os.unlink(path)
    if len(secret) > MAX_SECRET_FILE:
        raise SystemExit("the secret file is too large")
    return secret.strip()


def listening_socket(port):
    """A socket on 127.0.0.1 that already takes connections. agent.json, written after it, is the local provider's
    sign that the agent is up, so a connection made in the moment before uvicorn starts serving must wait in the
    queue: without listening first, Linux refuses it and macOS holds it back."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    return sock


async def exit_when_idle(server, workspace, idle, check=None, after_runs=0):
    """Ask the server to stop once the workspace has had no activity for `idle` seconds; and, with `after_runs`, once
    `after_runs` seconds have passed since its last run ended with no page's activity since this agent started, no run
    live and none waiting: a machine started for an agent's run alone (by a schedule or a hook) costs its run and two
    minutes, not the idle half hour."""
    check = check or min(30.0, max(0.05, min(idle, after_runs or idle) / 4))
    while not server.should_exit:
        await asyncio.sleep(check)
        agents = workspace.agents
        # the owner's controller at work (controller.py): on, with a session it started open, one waiting for its
        # account's reset among them, or its chat working; a machine stopped meanwhile would continue no session at
        # its reset, since nothing starts it again then
        controller = getattr(workspace, "controller", None)
        busy = controller is not None and controller.busy()
        if after_runs and agents.ended_at is not None and not workspace.page_seen and not agents.live_runs() \
                and not agents.queued() and not busy and time.monotonic() - agents.ended_at >= after_runs:
            server.should_exit = True
            return
        # an agent's run that works or waits for an answer is activity, up to its time limit (agents.py), though it
        # writes nothing while it waits or sits in one long tool call
        if workspace.agents.live_runs() or busy:
            workspace.touch()
        if time.monotonic() - workspace.last_activity >= idle:
            server.should_exit = True
            return


def started_by():
    """The process whose end stops this agent: the one PP_WORKSPACE_PARENT names (the program that started it, which
    says so, and is then seen to be gone even if it went before the agent looked), else this agent's parent now."""
    given = os.environ.pop("PP_WORKSPACE_PARENT", "").strip()
    return int(given) if given.isdigit() and int(given) > 0 else os.getppid()


async def exit_with_parent(server, parent, check=1.0):
    """Ask the server to stop once `parent` is no longer this agent's parent: the program that started it ended without
    stopping it, and the agent is init's now (or a subreaper's), with nobody to answer."""
    while not server.should_exit:
        if os.getppid() != parent:
            server.should_exit = True
            return
        await asyncio.sleep(check)


async def serve(server, sock, workspace, idle, after_runs=0, parent=None):
    # the loop's default pool, REQUEST_THREADS, set before anything runs on it
    asyncio.get_running_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(REQUEST_THREADS, thread_name_prefix="pp-agent"))
    watchers = [asyncio.ensure_future(exit_when_idle(server, workspace, idle, after_runs=after_runs))] if idle else []
    if parent is not None:
        watchers.append(asyncio.ensure_future(exit_with_parent(server, parent)))
    try:
        await server.serve(sockets=[sock])
    finally:
        for watcher in watchers:
            watcher.cancel()


def keep_claude_current(workspace, first=True, every=3600):
    """At the start, then once a day while the machine runs (claude_update.EVERY), unless the mode is manual."""
    from . import claude_update
    while True:
        if workspace.update_state["mode"] == "automatic" and (first or claude_update.due(workspace.update_state)):
            try:
                workspace.update_claude()
            except Exception as e:  # noqa: BLE001  the floor stays; the next round tries again
                print(f"claude update: {type(e).__name__}: {e}", flush=True)
        first = False
        time.sleep(every)


def main(argv=None):
    parent = started_by()
    keep_memory_private()
    secret = os.environ.pop("PP_WORKSPACE_SECRET", "")
    parser = argparse.ArgumentParser(prog="proofpane_workspace")
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--secret-file")
    parser.add_argument("--idle-exit", type=float, default=0, metavar="SECONDS")
    parser.add_argument("--idle-exit-after-runs", type=float, default=0, metavar="SECONDS")
    parser.add_argument("--claude-first-run", action="store_true")
    parser.add_argument("--claude-updates", action="store_true")
    parser.add_argument("--link-repos", action="store_true")
    args = parser.parse_args(argv)
    if os.getppid() != parent:
        # its secret file goes too: reading it is what deletes it, and under the local provider it also holds the key
        # the runs' records are signed with, the same at every start
        if args.secret_file:
            try:
                os.unlink(args.secret_file)
            except OSError:
                pass
        raise SystemExit("the program that started the agent is gone")
    given_key = None
    if args.secret_file:
        if secret:
            raise SystemExit("give the secret in the environment or in a file, not both")
        # the secret, and under the local provider a second line: the key its runs' records are signed with, the same
        # at each start where the secret is new (records_key otherwise makes it from the secret, which holds)
        secret, _, rest = read_secret_file(args.secret_file).partition("\n")
        secret, rest = secret.strip(), rest.strip()
        if rest.startswith("records-key="):
            try:
                given_key = bytes.fromhex(rest[len("records-key="):])
            except ValueError:
                raise SystemExit("the records key is hex") from None
            if len(given_key) < 32:
                raise SystemExit("the records key is at least 32 bytes")
    if args.idle_exit < 0:
        raise SystemExit("--idle-exit is a number of seconds")
    os.umask(0o077)
    os.makedirs(args.root, exist_ok=True)
    settings = os.environ.pop("PP_WORKSPACE_CLAUDE_SETTINGS", "") or None
    gemini = os.environ.pop("PP_WORKSPACE_GEMINI_SETTINGS", "") or None
    qwen = os.environ.pop("PP_WORKSPACE_QWEN_SETTINGS", "") or None
    ade_hook = os.environ.pop("PP_WORKSPACE_ADE_HOOK", "") or None
    workspace = Workspace(args.root, claude_settings=settings, gemini_settings=gemini, qwen_settings=qwen,
                          max_sessions=int(os.environ.pop("PP_WORKSPACE_MAX_SESSIONS", MAX_SESSIONS)),
                          claude_first_run=args.claude_first_run, claude_updates=args.claude_updates,
                          link_repos=args.link_repos, ade_hook=ade_hook,
                          records_key=given_key or (records_key(secret) if secret else None))
    if args.claude_updates:
        threading.Thread(target=keep_claude_current, args=(workspace,), name="claude-updates", daemon=True).start()
    app = make_app(workspace, secret)
    sock = listening_socket(args.port)
    workspace.ask_url = f"http://127.0.0.1:{sock.getsockname()[1]}"       # what a session's `proofpane` calls (asks.py)
    info = os.path.join(args.root, "agent.json")
    with open(info + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"port": sock.getsockname()[1], "pid": os.getpid()}, fh)
    os.replace(info + ".tmp", info)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, ws="websockets",
                                           ws_max_size=256 * 1024, lifespan="on",
                                           timeout_graceful_shutdown=5))
    asyncio.run(serve(server, sock, workspace, args.idle_exit, args.idle_exit_after_runs, parent=parent))


if __name__ == "__main__":
    main()
