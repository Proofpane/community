#!/usr/bin/env python3
"""proofpane: what an agent in a Proofpane workspace may do with the sessions on its own repository, when the person
has turned it on. Run in a session's terminal; it talks to the workspace agent with the session's own token.

    proofpane sessions                        the sessions on this repository: id, agent, state, task
    proofpane status ID                       one session's state, and what it waits for
    proofpane diff ID                         a session's change against where it started
    proofpane wait ID [--timeout SECONDS]     wait until a session has finished its turn, waits, or ended
    proofpane ask --agent AGENT --task TEXT [--base REF] [--effort LEVEL] [--wait [--timeout SECONDS]]
                                              ask the person for a new session on this repository; it starts only
                                              if the person starts it, in the app; --effort, with --agent claude
                                              alone, asks for one of Claude Code's effort levels, which the person
                                              may change; with --wait, wait for their answer and say the session's id
                                              when they start it, and the effort it started with; a workspace that
                                              does not answer in time may take the ask all the same, so this says
                                              to look at `proofpane asks` and, if the ask is not there, to ask
                                              again: the same ask asked while the first is still being taken is
                                              refused, saying so, and an ask the workspace refused is refused again,
                                              with its reason
    proofpane asks                            this session's asks: waiting, starting (being started by the person, and
                                              waiting again if that start fails), started (and as which session),
                                              refused, or gone; for a claude ask, the effort asked for ("none": it
                                              named none) and, once started, the effort its session first started
                                              with ("default": Claude Code's own); a later start that gives no effort
                                              says so to the person, not here

Add --json for what the workspace said, as it said it. Standard library only.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

AGENTS = ("claude", "codex", "gemini", "opencode", "grok", "qwen", "kimi", "mcode")
SETTLED = ("idle", "done", "failed", "stopped")
# how long an ask waits for the workspace to take it: an ask that names an effort and makes the workspace's first read of
# what its Claude Code offers itself, one ask at a time while the workspace keeps nothing of it, can take
# longer, and the workspace may take that ask after this has given up; any other ask that names an
# effort while a read is under way is refused at once
ASK_TIMEOUT = 30


class CommandError(Exception):
    pass


class TimedOut(CommandError):
    """The workspace was reached and did not answer in time: what was asked may still be done there."""


def _call(method, path, body=None, timeout=30):
    url, token = os.environ.get("PP_ASK_URL", ""), os.environ.get("PP_ASK_TOKEN", "")
    if not url or not token:
        raise CommandError("this works in a session of a workspace whose person has let agents ask for sessions "
                           "(PP_ASK_URL and PP_ASK_TOKEN are not set here).")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise CommandError(detail or f"the workspace answered {e.code}.") from None
    except TimeoutError:
        # the request was sent and no answer came in time: urllib raises the timeout itself for that
        raise TimedOut(f"the workspace did not answer within {timeout:g} seconds.") from None
    except (urllib.error.URLError, OSError) as e:
        # a connection, or the sending of the request, that timed out comes as a URLError whose reason is the timeout:
        # the workspace never took the request in, so it is not said to have it
        raise CommandError(f"the workspace could not be reached: {e}") from None


# the states of an ask the person has not answered yet (Workspace.asks_of): waiting for them, or being started by them,
# which a Start that fails puts back to wait again
OPEN = ("waiting", "starting")


def _answer(ask_id, timeout):
    """The person's answer to an ask, waited for: the ask as `proofpane asks` gives it, once it is neither waiting nor
    being started (OPEN)."""
    deadline = time.monotonic() + max(1.0, timeout)
    while True:
        mine = next((a for a in _call("GET", "/ask/asks").get("asks", []) if a.get("id") == ask_id), None)
        if mine is not None and mine.get("state") not in OPEN:
            return mine
        if time.monotonic() > deadline:
            raise CommandError(f"no answer from the person after {int(timeout)} seconds; `proofpane asks` shows it "
                               "when there is one.")
        time.sleep(5)


def _line(s):
    task = (s.get("title") or s.get("task") or "").split("\n")[0]
    state = s.get("state", "?") + (f" (waits for {s['waits_for']})" if s.get("waits_for") else "")
    return f"{s['id']}  {s.get('program', '?'):<8} {state:<10} {task[:90]}"


def _efforts(a):
    """A claude ask's efforts as the workspace gave them, in words: the one it asked for ("none" when it named
    none) and, once started, the one its session started with, the person's choice ("default": Claude Code's own), so
    the asking agent sees whether it got what it asked for. "" for an ask of another agent, which names none."""
    if a.get("program") != "claude":
        return ""
    said = f"effort asked: {a.get('effort') or 'none'}"
    if a.get("state") == "started":
        said += f", started: {a.get('started_effort') or 'default'}"
    return f"({said})"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofpane", description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="what the workspace said, as JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sessions", help="the sessions on this repository")
    for name in ("status", "diff"):
        sub.add_parser(name).add_argument("id")
    w = sub.add_parser("wait")
    w.add_argument("id")
    w.add_argument("--timeout", type=float, default=1800.0)
    a = sub.add_parser("ask")
    a.add_argument("--agent", required=True, choices=AGENTS)
    a.add_argument("--task", required=True)
    a.add_argument("--base")
    # checked by the workspace, which knows the levels its Claude Code offers, and refuses one for any other agent
    a.add_argument("--effort", help="with --agent claude: one of Claude Code's effort levels, which the person may "
                                    "change before it starts")
    a.add_argument("--wait", action="store_true", help="wait for the person's answer")
    a.add_argument("--timeout", type=float, default=3600.0)
    # what `proofpane asks` says of a claude ask's effort: the one its session first started with;
    # a later start that gives none says so to the person, in the app, and never here
    asks_help = ("this session's asks and their answers: starting, while the person is starting it (it waits again if "
                 "that start fails); for a claude ask, the effort asked for and the one its session first started "
                 "with; a later start that gives no effort says so to the person, not to you")
    sub.add_parser("asks", help=asks_help, description=asks_help)
    args = parser.parse_args(argv)
    out = sys.stdout
    try:
        if args.command == "sessions":
            got = _call("GET", "/ask/sessions")
            if args.json:
                out.write(json.dumps(got, indent=1) + "\n")
            else:
                for s in got.get("sessions", []):
                    out.write(_line(s) + ("  (this session)" if s.get("you") else "") + "\n")
        elif args.command == "status":
            got = _call("GET", f"/ask/sessions/{args.id}")
            out.write(json.dumps(got, indent=1) + "\n" if args.json else _line(got) + "\n")
        elif args.command == "diff":
            got = _call("GET", f"/ask/sessions/{args.id}/diff", timeout=60)
            if args.json:
                out.write(json.dumps(got, indent=1) + "\n")
            elif got.get("too_large"):
                out.write(f"The change is too large to show here ({len(got.get('files') or [])} files).\n")
            else:
                out.write(got.get("diff") or "No change yet.\n")
        elif args.command == "wait":
            deadline = time.monotonic() + max(1.0, args.timeout)
            while True:
                got = _call("GET", f"/ask/sessions/{args.id}")
                if got.get("state") in SETTLED or got.get("waits_for"):
                    break
                if time.monotonic() > deadline:
                    raise CommandError(f"still {got.get('state')} after {int(args.timeout)} seconds.")
                time.sleep(5)
            out.write(json.dumps(got, indent=1) + "\n" if args.json else _line(got) + "\n")
        elif args.command == "ask":
            asked = {"program": args.agent, "task": args.task, "base": args.base}
            if args.effort is not None:
                asked["effort"] = args.effort
            try:
                got = _call("POST", "/ask/sessions", asked, timeout=ASK_TIMEOUT)
            except TimedOut as e:
                # the workspace may take it all the same, once its read of claude ends, and `proofpane asks` shows it
                # only once it is taken; an ask it refused meanwhile, its claude not offering the effort, say, is
                # never shown there, so the agent is told to ask again when the ask is not there: the same ask, while
                # the first is still being taken, is refused in words saying so, and one refused is refused again with
                # its reason; the same ask once the first is taken is not refused for being the same (Asks.start_taking:
                # it is a second ask, the agent's to mean), so a first taken between the agent's look and its asking
                # again leaves two, and the words say so; words to wait and look again could wait for ever
                raise CommandError(f"{e} It may still take your ask: look at `proofpane asks`, and if it is not there, "
                                   "ask again. The same ask, asked while the first is still being taken, is refused in "
                                   "words saying so, and an ask the workspace refused is refused again, with its "
                                   "reason; but if the first was taken after you looked, the same ask is not refused "
                                   "for being the same.") from None
            if args.wait:
                got = _answer(got["id"], args.timeout)
                if args.json:
                    out.write(json.dumps(got, indent=1) + "\n")
                elif got["state"] == "started":
                    said = _efforts(got)
                    out.write(f"Started by the person as session {got['session']}{' ' + said if said else ''}: "
                              f"`proofpane wait {got['session']}` waits for its turn to end.\n")
                elif got["state"] == "refused":
                    out.write("The person refused it.\n")
                else:
                    out.write("It is gone: not answered within a day, or withdrawn.\n")
                return 0 if got["state"] == "started" else 3
            if args.json:
                out.write(json.dumps(got, indent=1) + "\n")
            else:
                out.write(f"Asked ({got['id']}): the person decides in the app whether it starts. "
                          "`proofpane asks` says when they have.\n")
        elif args.command == "asks":
            got = _call("GET", "/ask/asks")
            if args.json:
                out.write(json.dumps(got, indent=1) + "\n")
            else:
                for a in got.get("asks", []):
                    task = (a.get("task") or "").split("\n")[0][:80]
                    said = _efforts(a)
                    out.write(f"{a['id']}  {a['state']:<8} {a.get('program') or '?':<8} {a.get('session') or '-':<12} "
                              f"{task}{'  ' + said if said else ''}\n")
    except CommandError as e:
        sys.stderr.write(f"proofpane: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
