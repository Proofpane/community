"""An agent's ask for a session may name its effort: `proofpane ask --effort LEVEL`, for a Claude Code
session alone, checked by the workspace against what its claude offers; the person's choice at Start, which is what the
session starts with, the ask being a request, checked as the ask's own effort is against what the claude that starts
offers, read in the new worktree, and refused when that claude does not offer it, the ask waiting again;
and --effort given once at every start of that session, from the workspace's memory and the app's own record,
never from the session's folder, and at a later start only while the claude that start runs offers it, read at that
start in the session's worktree. The rule otherwise stands: no setting on a session the person
starts themselves. First the workspace's own pieces, its starts kept and never run, some through the agent's own routes
in this process; then through the app's routes, with a real workspace agent behind them, a stand-in claude that writes
down each start (chat_stand_in.py) and a stand-in codex whose session asks."""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time

import httpx
import pytest

from conftest import signed_up
from proofpane_workspace import agent, asks, usage
from proofpane_workspace import workspace as wsmod
from proofpane_workspace.workspace import Workspace, WorkspaceError
from test_workspace_app import _started, local  # noqa: F401  (local: fixture)
from workspace_fixtures import ROOT, SECRET, git, make_repo

W = "/api/ade/workspace"
HERE = os.path.dirname(os.path.abspath(__file__))
COMMAND = os.path.join(ROOT, "proofpane_workspace", "command.py")
# what the installed claude offers, as claude_cli reads it from the stand-ins' help (2.1.274, with ultracode added)
OFFERED = {"claude_version": "2.1.274", "efforts": ["low", "medium", "high", "xhigh", "max", "ultracode"],
           "permission_modes": ["default", "acceptEdits", "auto", "bypassPermissions", "plan"], "floor": False}
ASKER = "0123456789ab"          # the session whose agent asked, in the workspace's own tests
TASK = "Review the parser"
OTHER_SETTING = "A session started from an ask takes an effort and no other setting"
MINE = {"repo": "demo", "program": "claude", "task": "Mine"}          # a terminal claude the person starts themselves
# what a later start says when it gives no effort it would otherwise give (asks.NO_LONGER_OFFERED, asks.NOT_READ_NOW),
# and what an ask, or a Start, is told while what its claude offers is not known
# (asks.UNKNOWN_EFFORTS), written out here as the person reads them, so the words are pinned and a build without them
# fails these tests one by one rather than failing to load the file. NOT_READ and UNKNOWN name the version too: a
# version not read is the floor, as the help's levels or permission modes not read are
NO_LONGER = "Claude Code no longer offers effort {}: the session started at its default."
NOT_READ = ("The workspace could not read its Claude Code's version, or from its help which efforts, or which "
            "permission modes, it takes, so the session started at Claude Code's default effort, not at {}.")
UNKNOWN = ("The workspace could not read its Claude Code's version, or from its help which efforts, or which "
           "permission modes, it takes, so a session started from an ask takes no effort now: leave its effort to "
           "Claude Code's default.")
# what an ask that names an effort is told while what the workspace keeps of its claude is the floor: UNKNOWN, then that
# the workspace reads its claude again at most once a minute (asks.READ_AGAIN); a Start says UNKNOWN
UNKNOWN_ASKED = UNKNOWN + (" The workspace reads its Claude Code again at most once a minute, so you may ask for it "
                           "again in a minute.")
# what a first Start is told of an effort the claude that starts does not list (asks.check_effort), at 2.1.274, whose
# help lists the five levels and to which claude_cli adds ultracode, and at 2.1.150, before ultracode
NOT_ONE = "An effort is one of low, medium, high, xhigh, max, ultracode."
NOT_ONE_OLDER = "An effort is one of low, medium, high, xhigh, max."
# what the same ask, asked again while the first is still being taken, is told (asks.BEING_TAKEN),
# and that an ask the workspace refuses is never listed, so asking again then gives the reason
BEING_TAKEN = ("The same ask from this session, for the same agent, task, base and effort, is still being taken: "
               "`proofpane asks` shows it once it is. An ask the workspace refuses is never listed there, so if it does "
               "not appear once the workspace has answered the first, asking again gives the reason.")
# what an ask that names an effort is told while the workspace keeps nothing of its claude and a read of it is under way
# (asks.BEING_READ), rather than waiting for that read
BEING_READ = ("The workspace is reading which efforts its Claude Code takes, so it cannot take an effort on an ask "
              "yet: ask for it again in a minute, or leave its effort to Claude Code's default.")


def _effort(argv):
    """Each value a command gives --effort, in order: none, one, or a build's mistake of more."""
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--effort"]


# ---- the workspace's own pieces, its starts kept and never run -------------------------------------------------------

class _Started:
    """terminals.Terminal's place in these tests: the command each start would run, kept, and nothing run."""
    seen = []

    def __init__(self, argv, cwd, env, cols=100, rows=30, on_activity=None, save=None):
        _Started.seen.append(list(argv))
        self.running, self.exit_code, self.ended_at, self.backlog = False, 0, 0, bytearray()


# what the workspace's own stand-in claude says to `--help`: the two parts of Claude Code's help that claude_cli reads,
# its levels and its permission modes, as tests/chat_stand_in.py says them (2.1.274's; ultracode is added by rule)
HELP = ("  --effort <level>                      Effort level for the current session\n"
        "                                        (low, medium, high, xhigh, max)\n"
        "  --permission-mode <mode>              Permission mode to use for the session\n"
        "                                        (choices: \"acceptEdits\", \"auto\",\n"
        "                                        \"bypassPermissions\", \"manual\",\n"
        "                                        \"dontAsk\", \"plan\")\n")
VERSION = "2.1.274 (Claude Code)\n"


def _says_claude(folder):
    """A stand-in claude that says to `--version` and `--help` what the files `version` and `help` in `folder` hold
    (a test writes them, as a claude updated, pinned or broken would answer), writes down each time it is asked
    (`asked`: its first argument, the folder it runs in and the session it is told it starts for), and otherwise
    waits (a start reads the claude it runs, where and as it runs it). Each `--version` and each
    `--help` is counted (`version-n`, `help-n`), and one whose count `version-silent` or `help-silent` lists says
    nothing, after the seconds `version-silent-sleep` or `help-silent-sleep` holds, if any (_silence: a read that gets
    no answer, or none in time)."""
    os.makedirs(folder, exist_ok=True)
    for name, text in (("version", VERSION), ("help", HELP)):
        with open(os.path.join(folder, name), "w") as fh:
            fh.write(text)
    return ("#!/bin/sh\n"
            f"SAYS='{folder}'\n"
            'printf "%s|%s|%s\\n" "$1" "$(pwd -P)" "${PP_SESSION_ID-}" >> "$SAYS/asked"\n'
            'case "$1" in\n'
            "  -v|--version) what=version ;;\n"
            "  --help) what=help ;;\n"
            "  *) exec sleep 600 ;;\n"
            "esac\n"
            'n=$(( $(cat "$SAYS/$what-n" 2>/dev/null || echo 0) + 1 ))\n'
            'echo "$n" > "$SAYS/$what-n"\n'
            'if [ -f "$SAYS/$what-silent" ] && grep -qx "$n" "$SAYS/$what-silent"; then\n'
            '  if [ -f "$SAYS/$what-silent-sleep" ]; then exec sleep "$(cat "$SAYS/$what-silent-sleep")"; fi\n'
            "  exit 0\n"
            "fi\n"
            'exec cat "$SAYS/$what"\n')


def _no_handshake(wrapper, cwd, timeout=60):
    """The SDK's handshake, in place of the real one in these tests: the stand-in claude answers nothing but --version
    and --help, so the models are Claude Code's aliases (claude_cli.discover)."""
    raise RuntimeError("no handshake here")


def _refreshed(timeout=60):
    """Each read of what claude offers that a workspace is running in the background (Workspace.refresh_capabilities),
    waited for to its end."""
    for thread in [t for t in threading.enumerate() if t.name == "pp-capabilities-refresh"]:
        thread.join(timeout)
        assert not thread.is_alive(), "a read in the background did not end"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace whose claude says its version and its help as the real one does (the gate's floor asks the first,
    and a start reads both for what that claude offers) from the files in `ws.says`, which a test
    may write, and does nothing else; whose own claude offers OFFERED (`capabilities()`, what an ask is checked
    against), unless a test takes that away for the workspace's own read, which makes no SDK handshake; and whose
    starts are kept, never run. A read of claude it runs in the background ends before the test does."""
    bin_dir = tmp_path / "wsbin"
    bin_dir.mkdir()
    says = str(tmp_path / "says")
    for name, script in (("claude", _says_claude(says)), ("codex", "#!/bin/sh\nsleep 600\n")):
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)
    home = tmp_path / "wshome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(wsmod.terminals, "Terminal", _Started)
    monkeypatch.setattr(wsmod.claude_cli, "handshake", _no_handshake)
    _Started.seen = []
    w = Workspace(str(tmp_path / "ws"))
    make_repo(os.path.join(w.repos_dir, "demo"))
    w.capabilities = lambda: dict(OFFERED)
    w.home = home
    w.says = says
    yield w
    _refreshed()


def _say(w, version=None, help=None):
    """What the workspace's claude says from now on to `--version` and `--help`: each as given, the rest as before."""
    for name, text in (("version", version), ("help", help)):
        if text is not None:
            with open(os.path.join(w.says, name), "w") as fh:
                fh.write(text)


def _silence(w, what, calls, sleep=None):
    """From now on, the workspace's claude says nothing to the calls of `what` ("version" or "help") numbered in
    `calls`, counted from 1 again here, after `sleep` seconds when given: a read of it that gets no answer, or none in
    time."""
    with open(os.path.join(w.says, f"{what}-silent"), "w") as fh:
        fh.write("".join(f"{n}\n" for n in calls))
    for name, text in ((f"{what}-n", None), (f"{what}-silent-sleep", sleep)):
        path = os.path.join(w.says, name)
        if text is None:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
        else:
            with open(path, "w") as fh:
                fh.write(f"{text}\n")


def _asked_of(w):
    """Each time the workspace's claude was asked, in order: (its first argument, the folder it ran in, the session
    it was told it starts for)."""
    try:
        with open(os.path.join(w.says, "asked")) as fh:
            return [tuple(line.rstrip("\n").split("|")) for line in fh if line.strip()]
    except FileNotFoundError:
        return []


def _made(w):
    """What making a session leaves, to compare before and after a refused Start: the repository's worktrees and its
    branches, the sessions' folders, and the sessions bound to an account (a Start refused for its
    effort leaves none of them, as one the gate's floor refuses leaves none)."""
    repo = os.path.join(w.repos_dir, "demo")
    return (git("worktree", "list", "--porcelain", cwd=repo), git("branch", "--list", cwd=repo),
            sorted(os.listdir(w.sessions_dir)), w.accounts.all()["sessions"])


def _on_an_account(w):
    """Claude Code's new sessions start on an account of the workspace's own, so that making one binds it (its id)."""
    account = w.accounts.add("claude", "Work")
    w.accounts.choose("claude", account["id"])
    return account["id"]


def _asked(w, effort="max", program="claude"):
    """A session started from an ask, as the agent's ask_start prepares it: the person's choice as its settings."""
    return w.prepare("demo", program, TASK, None, {"effort": effort}, asked_by=ASKER)


def _through_agent(w, method, path, body=None, token=None):
    """One request to the workspace agent's own routes (agent.make_app over `w`), in this process and on no port, with
    its secret or, given, a session's own token: the answer."""
    async def go():
        headers = {"Authorization": "Bearer " + (token or SECRET)}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(w, SECRET)),
                                     base_url="http://agent", headers=headers) as c:
            return await c.request(method, path, json=body)
    return asyncio.run(go())


def _start_again(w, sid, effort=None):
    """A start again as the app asks the agent for one (POST /sessions/{id}/start, its own copy as `effort` when it has
    one): the start's answer; the command it started is _Started.seen[-1]."""
    return _through_agent(w, "POST", f"/sessions/{sid}/start", {} if effort is None else {"effort": effort})


class _Running:
    """The asking session's program, running: a token is good only while its program runs (Workspace.asker)."""
    running = True


def _unreadable():
    raise RuntimeError("claude could not be read")


def test_the_effort_is_given_once_at_the_first_start_before_the_task_and_once_at_each_later_one(ws):
    record, argv = _asked(ws)
    assert _effort(argv) == ["max"] and argv[-1] == TASK and argv.index("--effort") == len(argv) - 3
    assert ws.effort_of(record["id"]) == "max"
    ws.launch(record["id"], argv)
    assert _Started.seen[-1] == argv                        # launch adds nothing to the command prepare built
    _start_again(ws, record["id"])                          # started again: launch builds the command, and gives it
    assert _effort(_Started.seen[-1]) == ["max"] and TASK not in _Started.seen[-1]
    # with a conversation of its worktree's to pick up: once, beside --continue
    folder = os.path.join(str(ws.home), ".claude", "projects", usage.slug(os.path.realpath(ws._work(record["id"]))))
    os.makedirs(folder)
    open(os.path.join(folder, "c.jsonl"), "w").close()
    _start_again(ws, record["id"])
    assert _effort(_Started.seen[-1]) == ["max"] and _Started.seen[-1][-1] == "--continue"


def test_a_later_start_takes_the_workspace_s_copy_then_the_app_s_and_never_the_session_s_record(ws):
    record, _ = _asked(ws)
    path = os.path.join(ws._dir(record["id"]), "session.json")
    with open(path) as fh:
        written = json.load(fh)
    written.update(effort="low", chat_settings={"effort": "low"})        # what its programs could write there
    with open(path, "w") as fh:
        json.dump(written, fh)
    _start_again(ws, record["id"], "high")
    assert _effort(_Started.seen[-1]) == ["max"]            # its own copy, made as the person chose
    ws._efforts.clear()                                     # an agent started since keeps none of its own
    _start_again(ws, record["id"], "high")
    assert _effort(_Started.seen[-1]) == ["high"]           # the app's
    _start_again(ws, record["id"])
    assert _effort(_Started.seen[-1]) == []                 # neither: Claude Code's default, never the record's
    _start_again(ws, record["id"], "")
    assert _effort(_Started.seen[-1]) == []


@pytest.mark.parametrize("given", ["--model=opus", "max high", "x" * 33, "1max", 7, 0, ["max"], {"effort": "max"},
                                   # letters and dashes alone, a dash first: a flag's shape, never a level's
                                   "--dangerously-skip-permissions", "-max"])
def test_an_effort_from_the_app_that_is_not_a_level_s_name_starts_nothing(ws, given):
    record, _ = _asked(ws)
    ws._efforts.clear()             # the app's copy the only one, as after this agent has started again (while
    #                                 this agent keeps its own, the app's is not read)
    r = _start_again(ws, record["id"], given)
    assert r.status_code == 400 and r.json()["detail"] == "An effort is the name of one of Claude Code's levels.", \
        r.text
    with pytest.raises(WorkspaceError, match="An effort is the name of one of Claude Code's levels."):
        ws.launch(record["id"], effort=given)               # and launch, given one, refuses it too
    assert _Started.seen == []


@pytest.mark.parametrize("given", ["--model=opus", "max high", "x" * 33, 7, ["max"], "--dangerously-skip-permissions"])
def test_a_later_start_takes_this_agent_s_own_copy_first_whatever_the_app_s_holds(ws, given):
    # an app's copy without a level's name's shape, which only an app
    # that stored whatever the workspace said could send, is not read while this agent keeps its own copy, so the
    # session still starts, at its own effort
    record, _ = _asked(ws)
    r = _start_again(ws, record["id"], given)
    assert r.status_code == 200 and "effort_notice" not in r.json(), r.text
    assert _effort(_Started.seen[-1]) == ["max"]


def test_launch_gives_no_effort_to_a_session_of_another_program(ws):
    # launch's own program check, which no route reaches with an effort for another program: given one directly, a
    # codex session's command holds no --effort
    record, argv = ws.prepare("demo", "codex", TASK, asked_by=ASKER)
    ws.launch(record["id"], effort="max")
    assert _Started.seen[-1][0].endswith("codex") and "--effort" not in _Started.seen[-1]


def test_a_session_of_another_program_is_given_no_effort_whatever_the_app_says(ws):
    record, argv = ws.prepare("demo", "codex", TASK, asked_by=ASKER)
    r = _start_again(ws, record["id"], "max")
    assert r.status_code == 200 and "effort_notice" not in r.json()
    assert "--effort" not in _Started.seen[-1] and ws.effort_of(record["id"]) is None


@pytest.mark.parametrize("settings", [None, {}, {"effort": ""}, {"effort": None}])
def test_no_effort_chosen_is_claude_code_s_default(ws, settings):
    record, argv = ws.prepare("demo", "claude", TASK, None, settings, asked_by=ASKER)
    assert "--effort" not in argv and ws.effort_of(record["id"]) is None
    _start_again(ws, record["id"])
    assert "--effort" not in _Started.seen[-1]


# what a claude says to `--help` when claude_cli finds no levels in it (its floor), and the help of a claude that lists
# three levels; and an older claude's version, before ultracode (claude_cli adds it from 2.1.203)
UNPARSED = "Usage: claude [options] [command] [prompt]\n"
HELP_THREE = HELP.replace("(low, medium, high, xhigh, max)", "(low, medium, high)")
OLDER_CLAUDE = "2.1.150 (Claude Code)\n"


@pytest.mark.parametrize("prepared, version, help, mine, app, said", [
    # started at max, then its claude's help not read for what it takes (the floor)
    ("max", None, UNPARSED, True, None, NOT_READ.format("max")),
    # started at ultracode, then an older claude that lists three levels, the agent started since (its own
    # copy gone) and the app's copy given
    ("ultracode", OLDER_CLAUDE, HELP_THREE, False, "ultracode", NO_LONGER.format("ultracode")),
    # what claude offers not read at all: it says nothing to --help
    ("max", None, "", True, None, NOT_READ.format("max")),
])
def test_a_later_start_gives_no_effort_its_claude_no_longer_offers_and_says_so(ws, prepared, version, help, mine, app,
                                                                              said):
    # what the claude says is read at each start: each case changes what it says, and nothing else
    record, _ = _asked(ws, effort=prepared)
    sid = record["id"]
    if not mine:
        ws._efforts.clear()
    _say(ws, version, help)
    r = _start_again(ws, sid, app)
    assert r.status_code == 200 and r.json()["effort_notice"] == said, r.text
    assert _effort(_Started.seen[-1]) == []                 # Claude Code's default
    # neither copy is changed: once its claude offers the level again, the next start gives it again, and says nothing
    assert ws.effort_of(sid) == (prepared if mine else None)
    _say(ws, VERSION, HELP)
    r = _start_again(ws, sid, app)
    assert r.status_code == 200 and "effort_notice" not in r.json(), r.text
    assert _effort(_Started.seen[-1]) == [prepared]


def test_a_later_start_reads_what_its_claude_offers_off_the_event_loop_and_outside_the_lock(ws, monkeypatch):
    # a read of what claude offers can take a while (each up to claude_cli.TIMEOUT): meanwhile the agent answers a route
    # that takes its lock, and the start, once read, goes on
    record, _ = _asked(ws)
    reading = threading.Event()
    real = wsmod.claude_cli.run_text

    def slow(claude, env, cwd, *args):
        if args == ("--help",):
            reading.set()
            time.sleep(1.0)
        return real(claude, env, cwd, *args)

    monkeypatch.setattr(wsmod.claude_cli, "run_text", slow)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(ws, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            began = time.monotonic()
            started = asyncio.ensure_future(c.post(f"/sessions/{record['id']}/start", json={}))
            while not reading.is_set():
                assert not started.done(), started.result().text
                await asyncio.sleep(0.01)
            locked = await c.post("/repos/link", json={"path": "/nowhere"})
            return time.monotonic() - began, locked, await started

    took, locked, started = asyncio.run(go())
    assert locked.status_code == 400 and took < 0.5, (took, locked.text)
    assert started.status_code == 200 and _effort(_Started.seen[-1]) == ["max"], started.text


def test_a_later_start_gives_the_effort_its_claude_still_offers(ws):
    record, _ = _asked(ws, effort="ultracode")
    sid = record["id"]
    _say(ws, version="2.1.290 (Claude Code)\n")            # updated since, the level still offered
    seen = len(_asked_of(ws))
    r = _start_again(ws, sid)
    assert r.status_code == 200 and "effort_notice" not in r.json(), r.text
    assert _effort(_Started.seen[-1]) == ["ultracode"]
    # what decided it is the claude this start runs, read at this start where it runs it: its help read in the
    # session's worktree, for this session (without this the test would pass where no
    # start read the claude at all)
    assert ("--help", os.path.realpath(ws._work(sid)), sid) in _asked_of(ws)[seen:], _asked_of(ws)[seen:]


def test_a_later_start_reads_the_claude_it_runs_at_that_start_in_its_worktree_and_keeps_nothing(ws):
    # what a later start gives is read of the
    # claude it runs, at that start, where and as it runs it (its --version and its --help alone, in the session's
    # worktree, with the session's own names), and nothing of one start's read is kept for the next: a claude replaced
    # under the running agent, or one whose help could not be read at one start, is read again at the next
    record, _ = _asked(ws, effort="ultracode")
    sid = record["id"]
    work = os.path.realpath(ws._work(sid))
    seen = len(_asked_of(ws))
    r = _start_again(ws, sid)
    assert r.status_code == 200 and "effort_notice" not in r.json() and _effort(_Started.seen[-1]) == ["ultracode"]
    # the gate's floor reads its version, then this start what it offers: nothing else is asked of it, nowhere else
    assert _asked_of(ws)[seen:] == [("--version", work, sid), ("--version", work, sid), ("--help", work, sid)]
    for version, help, given, said in ((OLDER_CLAUDE, None, [], NO_LONGER.format("ultracode")),
                                       (VERSION, None, ["ultracode"], None),
                                       (None, UNPARSED, [], NOT_READ.format("ultracode")),
                                       (None, HELP, ["ultracode"], None)):
        _say(ws, version, help)
        r = _start_again(ws, sid)
        assert r.status_code == 200 and r.json().get("effort_notice") == said, (version, help, r.text)
        assert _effort(_Started.seen[-1]) == given, (version, help)


def test_a_later_start_reads_the_claude_the_gate_s_floor_checked(ws, tmp_path):
    # the claude whose levels decide is the one the start route checked for the gate's floor and launch starts
    # (claude_floor_for), not another a start would take now
    record, _ = _asked(ws, effort="ultracode")
    other = tmp_path / "otherbin"
    other.mkdir()
    (other / "claude").write_text(_says_claude(str(tmp_path / "other-says")))
    (other / "claude").chmod(0o755)
    with open(tmp_path / "other-says" / "version", "w") as fh:
        fh.write(OLDER_CLAUDE)                              # the checked one an older claude, without ultracode
    floor = ws.claude_floor_for
    ws.claude_floor_for = lambda sid: (floor(sid)[0], str(other / "claude"))
    r = _start_again(ws, record["id"])
    assert r.status_code == 200 and r.json()["effort_notice"] == NO_LONGER.format("ultracode"), r.text
    assert _Started.seen[-1][0] == str(other / "claude") and _effort(_Started.seen[-1]) == []


@pytest.mark.parametrize("chosen, version, help, refused", [
    # where the session starts, an older claude, without ultracode, while the workspace's own claude offers it
    # (OFFERED, what the ask is checked against): refused in that claude's levels
    ("ultracode", OLDER_CLAUDE, None, NOT_ONE_OLDER),
    # one whose help is read for no levels (its floor), the workspace's own claude's read for them
    ("max", None, UNPARSED, UNKNOWN),
    # a level of a level's shape that no claude lists, refused in the levels of the claude that starts
    ("turbo", None, None, NOT_ONE),
    # the claude that starts offers it: the session starts at it
    ("ultracode", None, None, None),
])
def test_the_first_start_gives_the_chosen_effort_only_when_the_claude_that_starts_offers_it(ws, chosen, version, help,
                                                                                          refused):
    # at the first Start, the person's choice is
    # checked against what the claude that starts offers, read in the new worktree with the start's environment once
    # that worktree is made. One that claude does not offer there, or any while what it offers is its floor, is refused
    # as the ask's own effort is (asks.check_effort), and the session is unmade as the gate's floor unmakes one: no
    # worktree, branch, folder or account's binding is left, no effort kept, and the ask waits again as the agent asked
    # it, rather than the session starting at Claude Code's default
    _say(ws, version, help)
    account = _on_an_account(ws)
    ws.set_asks_on(True)
    os.makedirs(ws._dir(ASKER))                             # where the asking session's record of its asks goes
    ask = ws.ask({"session": ASKER, "repo": "demo", "may_ask": True}, "claude", TASK)
    before = _made(ws)
    r = _through_agent(ws, "POST", f"/asks/{ask['id']}/start", {"effort": chosen})
    # the claude that starts was read in the new session's worktree, for that session: the gate's floor's version, then
    # its version and its help for what it offers
    (sid,) = {a[2] for a in _asked_of(ws)}
    work = os.path.realpath(ws._work(sid))
    assert _asked_of(ws) == [("--version", work, sid), ("--version", work, sid), ("--help", work, sid)]
    if refused:
        assert r.status_code == 400 and r.json()["detail"] == refused, r.text
        assert _Started.seen == [] and _made(ws) == before and ws._efforts == {}
        assert ws.asks.waiting() == [ask] and [e["event"] for e in ws.ask_log(ASKER)] == ["asked"]
        return
    assert r.status_code == 200 and r.json()["id"] == sid, r.text
    assert (r.json()["effort"], r.json().get("effort_notice")) == (chosen, None)
    assert _effort(_Started.seen[-1]) == [chosen] and _Started.seen[-1][-1] == TASK
    # the control: a Start that is not refused leaves its session, bound to the account, as `_made` reads it
    assert ws.effort_of(sid) == chosen and _made(ws) != before and ws.accounts.all()["sessions"] == {sid: account}
    with open(os.path.join(ws._dir(sid), "session.json")) as fh:
        assert json.load(fh).get("chat_settings") == {"effort": chosen}
    assert ws.asks.waiting() == [] and [e["started_effort"] for e in ws.ask_log(ASKER) if e["event"] == "started"] \
        == [chosen]


# the gate's floor's words for a claude older than 2.1.139 (here 2.1.100), and for one whose version is not read
# (Workspace.claude_floor_refusal)
OLDER_THAN_THE_FLOOR = ("Claude Code 2.1.100 is older than 2.1.139, the first whose settings declare the form the "
                        "workspace's gate is given in (a program and its arguments); what an older one does with the gate "
                        "given so was not tried, so it may not run it: update Claude Code, then start it again.")
VERSION_NOT_READ = ("Claude Code's version could not be read, so the workspace cannot tell that it is 2.1.139 or later, "
                    "the first whose settings declare the form the workspace's gate is given in: update Claude Code, "
                    "then start it again.")


@pytest.mark.parametrize("version, said", [("2.1.100 (Claude Code)\n", OLDER_THAN_THE_FLOOR), (None, VERSION_NOT_READ)])
def test_a_first_start_s_effort_is_not_read_of_a_claude_the_gate_s_floor_refuses(ws, version, said):
    # a first Start with an effort, on a claude the gate's floor refuses
    # (older than 2.1.139, or one whose version is not read, here at every --version), is refused in the floor's words,
    # and the effort's reads of that claude are not made: no second --version, no --help, the ask waiting and nothing
    # of the session left. A control: with the effort's check run
    # whatever the floor said, a Start would read claude twice more under the agent's lock and, where that check failed
    # too, say its words in place of the floor's: for a version not read, to leave the effort to Claude Code's default,
    # where the floor says to update Claude Code
    if version is None:
        _silence(ws, "version", [1, 2, 3])
    else:
        _say(ws, version=version)
    ws.set_asks_on(True)
    os.makedirs(ws._dir(ASKER))
    ask = ws.ask({"session": ASKER, "repo": "demo", "may_ask": True}, "claude", TASK)
    before = _made(ws)
    r = _through_agent(ws, "POST", f"/asks/{ask['id']}/start", {"effort": "max"})
    assert r.status_code == 400 and r.json()["detail"] == said, r.text
    assert [a[0] for a in _asked_of(ws)] == ["--version"], _asked_of(ws)
    assert _Started.seen == [] and _made(ws) == before and ws.asks.waiting() == [ask]


@pytest.mark.parametrize("program, settings, asked_by, said", [
    ("claude", {"effort": "max"}, None, "Only a chat has a model, an effort and a permission mode to choose."),
    ("codex", {"effort": "max"}, ASKER, asks.CLAUDE_ONLY),
    ("claude", {"model": "opus"}, ASKER, OTHER_SETTING),
    ("claude", {"effort": "max", "permission_mode": "plan"}, ASKER, OTHER_SETTING),
    ("claude", {"effort": "max", "may_dispatch": True}, ASKER, OTHER_SETTING),
    # without a level's name's shape: refused at Start whatever claude offers, before anything is made
    ("claude", {"effort": "max high"}, ASKER, "An effort is the name of one of Claude Code's levels."),
    # of that shape and not among the levels of the claude that starts, read once the worktree is made: refused there,
    # the session unmade (not refused against the workspace's own claude's levels, nor the session started at Claude
    # Code's default)
    ("claude", {"effort": "turbo"}, ASKER, NOT_ONE),
])
def test_prepare_refuses_every_setting_but_an_ask_s_effort_for_claude_and_makes_nothing(ws, program, settings,
                                                                                        asked_by, said):
    _on_an_account(ws)
    before = _made(ws)
    with pytest.raises(WorkspaceError) as refused:
        ws.prepare("demo", program, TASK, None, settings, asked_by=asked_by)
    assert str(refused.value).startswith(said)
    assert _made(ws) == before and os.listdir(ws.sessions_dir) == [] and ws._efforts == {}


def _asking_session(w):
    """The asking session's program running, with asks on: its own token, as `proofpane` is given it."""
    w.set_asks_on(True)
    w.terminals[ASKER] = _Running()
    os.makedirs(w._dir(ASKER), exist_ok=True)
    return w.asks.issue(ASKER, "demo", True)


def _ask(w, token, **body):
    """An ask posted to the agent's own /ask/sessions with a session's token, as `proofpane ask` posts it."""
    return _through_agent(w, "POST", "/ask/sessions", dict({"program": "claude", "task": TASK}, **body), token=token)


def test_no_effort_is_taken_while_it_is_not_known_which_efforts_its_claude_takes(ws):
    ws.capabilities = lambda: dict(OFFERED, floor=True)
    _say(ws, help=UNPARSED)
    # at Start, the claude that starts is read in the new worktree, and its help is read for no
    # levels there either: the Start is refused, and nothing of the session is left (not the session started at Claude
    # Code's default)
    before = _made(ws)
    with pytest.raises(WorkspaceError) as refused:
        _asked(ws)
    assert str(refused.value) == UNKNOWN and _made(ws) == before and ws._efforts == {}
    # when the agent asks: what the workspace keeps of its own claude is the floor, so the ask is refused at once, in
    # words saying that the workspace reads its claude again at most once a minute, and the read it starts in the
    # background, not in the ask's request, reads no levels either: that
    # read is of the workspace's own claude, whose help says none, and what it keeps, once the stub of what is kept is
    # taken away, is the floor
    token = _asking_session(ws)
    r = _ask(ws, token, effort="max")
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text
    _refreshed()
    del ws.capabilities
    kept = ws.capabilities()
    assert (kept["floor"], kept["claude_version"]) == (True, "2.1.274"), kept
    root = os.path.realpath(ws.root)
    assert [a for a in _asked_of(ws) if a[1] == root] == [("--version", root, ""), ("--help", root, "")]   # read once
    r = _ask(ws, token)
    assert r.status_code == 200 and r.json()["effort"] is None, r.text      # an ask that names none is taken
    record, argv = _asked(ws, effort="")
    assert "--effort" not in argv


# what a wrapper or a shim in front of Claude Code can print to `--version`: a line of its own, with a version of its
# own, before Claude Code's line (claude_cli._CLI_VERSION's comment), here before 2.1.150's, which takes no ultracode
WRAPPED = "wrapper 3.0.0\n2.1.150 (Claude Code)\n"


def test_the_effort_is_read_against_claude_code_s_own_version_line_as_the_gate_s_floor_reads_it(ws):
    # what a start reads of its claude takes Claude Code's own line
    # for its version, as the gate's floor does (claude_cli.cli_version), so a wrapper's line before it is not taken
    # for Claude Code's, and claude_cli adds ultracode by Claude Code's version alone. A first
    # Start at ultracode is refused, the ask waiting; and a session started at ultracode is given none at a later start
    _say(ws, version=WRAPPED)
    ws.set_asks_on(True)
    os.makedirs(ws._dir(ASKER))
    ask = ws.ask({"session": ASKER, "repo": "demo", "may_ask": True}, "claude", TASK)
    before = _made(ws)
    r = _through_agent(ws, "POST", f"/asks/{ask['id']}/start", {"effort": "ultracode"})
    assert _Started.seen == [] and r.status_code == 400 and r.json()["detail"] == NOT_ONE_OLDER, r.text
    assert ws.asks.waiting() == [ask] and _made(ws) == before
    # started at ultracode while its claude said Claude Code's line alone, then the wrapper in front of it again
    _say(ws, version=VERSION)
    record, _ = _asked(ws, effort="ultracode")
    _say(ws, version=WRAPPED)
    r = _start_again(ws, record["id"])
    assert r.status_code == 200 and _effort(_Started.seen[-1]) == [], (r.text, _Started.seen[-1])
    assert r.json().get("effort_notice") == NO_LONGER.format("ultracode"), r.text


@pytest.mark.parametrize("silent", ["nothing", "too late"])
def test_a_version_the_effort_s_read_cannot_read_is_the_floor(ws, monkeypatch, silent):
    # the gate's floor reads the claude's version, and then the
    # effort's own read reads it again (claude_cli.cli_version), which here gets nothing, or nothing within
    # claude_cli.TIMEOUT (its 30 seconds made 3 for the test). A version not read is the floor, since whether that
    # claude takes ultracode, which claude_cli adds by its version, cannot be told: a first Start's
    # effort is refused in the floor's words, the ask waiting, and a later start gives none, saying so in the floor's
    # words
    sleep = None
    if silent == "too late":
        monkeypatch.setattr(wsmod.claude_cli, "TIMEOUT", 3)
        sleep = 9
    ws.set_asks_on(True)
    os.makedirs(ws._dir(ASKER))
    ask = ws.ask({"session": ASKER, "repo": "demo", "may_ask": True}, "claude", TASK)
    before = _made(ws)
    _silence(ws, "version", [2], sleep)                     # the floor's read answers, the effort's own does not
    r = _through_agent(ws, "POST", f"/asks/{ask['id']}/start", {"effort": "max"})
    assert _Started.seen == [] and r.status_code == 400 and r.json()["detail"] == UNKNOWN, r.text
    assert ws.asks.waiting() == [ask] and _made(ws) == before
    # the gate's floor read the version, and the effort's own read read it and stopped there: a version not read is
    # the floor whatever the help says, so its help is not read, and the start waits for one read, not two
    assert [a[0] for a in _asked_of(ws)] == ["--version", "--version"], _asked_of(ws)
    # a session started at max while its claude answered every read, then started again with its effort's read of the
    # version getting nothing
    _silence(ws, "version", [])
    record, _ = _asked(ws)
    _silence(ws, "version", [2], sleep)
    r = _start_again(ws, record["id"])
    assert r.status_code == 200 and _effort(_Started.seen[-1]) == [], (r.text, _Started.seen[-1])
    assert r.json().get("effort_notice") == NOT_READ.format("max"), r.text


def test_what_the_workspace_keeps_of_its_claude_is_the_floor_when_its_version_is_not_read(ws, monkeypatch):
    # the read the workspace keeps (capabilities(), which the ask's check and the
    # page's card use) reads the version as the gate's floor and a start do (claude_cli.cli_version, by
    # claude_cli.offered): one it cannot read is the floor, its help not read, not every
    # level and ultracode. An ask naming ultracode is refused when asked, not taken for its Start to refuse.
    # The models are the SDK's handshake's still, whatever the version read gives
    del ws.capabilities
    monkeypatch.setattr(wsmod.claude_cli, "handshake",
                        lambda wrapper, cwd, timeout=60: {"models": [{"value": "opus", "displayName": "Opus"},
                                                                     {"value": "fable", "displayName": "Fable"}]})
    _silence(ws, "version", [1])
    kept = ws.capabilities()
    assert (kept["floor"], kept["claude_version"]) == (True, None)
    assert [a[0] for a in _asked_of(ws)] == ["--version"]          # its help not read
    assert (kept["models"], kept["model_aliases"]) == ([{"value": "opus", "name": "Opus"},
                                                       {"value": "fable", "name": "Fable"}], ["opus", "fable"])
    r = _ask(ws, _asking_session(ws), effort="ultracode")
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text


def test_what_the_workspace_keeps_of_its_claude_reads_claude_code_s_own_version_line(ws):
    # a wrapper's own version printed before Claude Code's line is not read for
    # Claude Code's (a read that took 3.0.0 would take ultracode with it). What the workspace keeps,
    # the page's card offers and a chat's effort is checked against is 2.1.150's levels, without ultracode, and an ask
    # naming ultracode is refused when asked, not taken for its Start to refuse
    del ws.capabilities
    _say(ws, version=WRAPPED)
    kept = ws.capabilities()
    assert (kept["claude_version"], kept["floor"]) == ("2.1.150", False)
    assert kept["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert _through_agent(ws, "GET", "/capabilities").json()["efforts"] == kept["efforts"]
    with pytest.raises(WorkspaceError, match=NOT_ONE_OLDER):
        ws.chat_settings({"effort": "ultracode"})
    assert ws.chat_settings({"effort": "max"}) == {"effort": "max"}
    r = _ask(ws, _asking_session(ws), effort="ultracode")
    assert r.status_code == 400 and r.json()["detail"] == NOT_ONE_OLDER, r.text


def test_an_ask_while_the_workspace_keeps_the_floor_is_refused_at_once_and_its_claude_read_again_in_the_background(
        ws, monkeypatch):
    # an ask's effort is checked against what the workspace keeps of its claude (capabilities(), here not
    # stubbed), and that is the floor, as a first read whose help got nothing leaves it. An ask naming an effort is
    # refused at once, saying to ask again in a minute, rather than the claude being read again in the ask's request,
    # for as long as its reads take, past `proofpane ask`'s wait. The ask starts one read in the background; another ask
    # meanwhile starts none (here with no minute between reads, so it is one read at a time that stops it); and once
    # that read has read the claude well, what it read is kept, and an ask is taken without reading claude
    monkeypatch.setattr(wsmod, "CAPABILITIES_REFRESH_EVERY", 0, raising=False)
    del ws.capabilities
    _silence(ws, "help", [1])                               # the first --help gets nothing, every later one answers
    assert ws.capabilities()["floor"] is True
    root = os.path.realpath(ws.root)
    reading, release = _slowed_help(monkeypatch)
    token = _asking_session(ws)
    began = time.monotonic()
    r = _ask(ws, token, effort="max")
    took = time.monotonic() - began
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text
    assert took < 1.0, took
    assert reading.wait(15), "no read was started in the background"
    began = time.monotonic()
    r = _ask(ws, token, task="Check it", effort="xhigh")
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED and time.monotonic() - began < 1.0, r.text
    release.set()
    _refreshed()
    # the workspace's first read, then the one in the background, alone: its version and its help, in its root
    assert _asked_of(ws) == [("--version", root, ""), ("--help", root, "")] * 2, _asked_of(ws)
    kept = ws.capabilities()
    assert (kept["floor"], kept["efforts"], kept["claude_version"]) == (False, OFFERED["efforts"], "2.1.274")
    assert kept["models"] == [{"value": m, "name": m.capitalize()} for m in ("fable", "opus", "sonnet", "haiku")]
    r = _ask(ws, token, task="Review it", effort="max")
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text
    assert len(_asked_of(ws)) == 4                          # taken against what is kept: claude not read for it


def test_the_workspace_reads_its_claude_again_at_most_once_a_minute_and_keeps_what_it_reads_floor_or_not(ws):
    # the reads in the background are bounded by time as well as one at a time: none starts within
    # CAPABILITIES_REFRESH_EVERY seconds, a minute, of the last one's start. What each reads is kept, the floor or not:
    # here a first read of 2.1.274 whose help got nothing, a read in the background of 2.1.280 whose help got nothing
    # again, kept so, and, a minute later, one whose help answers
    del ws.capabilities
    _silence(ws, "help", [1, 2])
    assert ws.capabilities()["floor"] is True
    _say(ws, version="2.1.280 (Claude Code)\n")             # updated since the first read
    token = _asking_session(ws)
    assert _ask(ws, token, effort="max").json()["detail"] == UNKNOWN_ASKED
    _refreshed()
    kept = ws.capabilities()
    assert (kept["claude_version"], kept["floor"]) == ("2.1.280", True)
    # within the minute: refused, and no read started
    assert _ask(ws, token, task="Check it", effort="max").json()["detail"] == UNKNOWN_ASKED
    _refreshed()
    assert len(_asked_of(ws)) == 4, _asked_of(ws)
    assert wsmod.CAPABILITIES_REFRESH_EVERY == 60
    ws._refresh_began -= wsmod.CAPABILITIES_REFRESH_EVERY   # a minute since the last read began
    assert _ask(ws, token, task="Review it", effort="max").json()["detail"] == UNKNOWN_ASKED
    _refreshed()
    assert len(_asked_of(ws)) == 6 and ws.capabilities()["floor"] is False, _asked_of(ws)
    r = _ask(ws, token, task="Review it again", effort="max")
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text


def test_an_ask_while_what_the_workspace_keeps_is_not_the_floor_reads_no_claude(ws):
    # an ask naming an effort reads nothing of claude while what the workspace keeps
    # is not the floor, neither in its request nor in the background, so it costs no read
    del ws.capabilities
    assert ws.capabilities()["floor"] is False              # the workspace's own read, its help answering
    token = _asking_session(ws)
    r = _ask(ws, token, effort="max")
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text
    _refreshed()
    assert [a[0] for a in _asked_of(ws)] == ["--version", "--help"], _asked_of(ws)


def test_the_read_in_the_background_is_of_the_build_in_use_on_a_workspace_machine(tmp_path, monkeypatch):
    # on a workspace machine the claude every start takes is the build in use
    # (Workspace._claude: an update's build that passed its check), not the image's own (claude_floor), and what the
    # workspace keeps, read again in the background, is read of that build
    image, build = tmp_path / "image", tmp_path / "build"
    for folder in (image, build):
        folder.mkdir()
        (folder / "claude").write_text(_says_claude(str(folder / "says")))
        (folder / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{image}:{os.environ['PATH']}")
    monkeypatch.setattr(wsmod.claude_cli, "handshake", _no_handshake)
    w = Workspace(str(tmp_path / "ws"), claude_updates=True)
    try:
        assert w.claude_floor == str(image / "claude")
        w._use_claude(str(build / "claude"))                # the build an update took into use
        w.says = str(build / "says")
        _silence(w, "help", [1])
        assert w.capabilities()["floor"] is True
        r = _ask(w, _asking_session(w), effort="max")
        assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text
        _refreshed()
        assert w.capabilities()["floor"] is False
        root = os.path.realpath(w.root)
        assert _asked_of(w) == [("--version", root, ""), ("--help", root, "")] * 2, _asked_of(w)
        w.says = str(image / "says")
        assert _asked_of(w) == []                           # the image's own claude was never read
    finally:
        _refreshed()


def test_a_read_in_the_background_that_an_update_of_claude_code_overtakes_is_not_kept(ws, monkeypatch):
    # like capabilities()'s own read, a read in the background begun before an update took another build into use
    # keeps nothing (_use_claude moves _capabilities_gen), so the next read is of the new build
    del ws.capabilities
    _silence(ws, "help", [1])
    assert ws.capabilities()["floor"] is True
    reading, release = _slowed_help(monkeypatch)
    token = _asking_session(ws)
    assert _ask(ws, token, effort="max").json()["detail"] == UNKNOWN_ASKED
    assert reading.wait(15), "no read was started in the background"
    ws._use_claude(ws.claude_path)                          # an update, meanwhile: what is kept is dropped
    _say(ws, version="2.1.290 (Claude Code)\n")
    release.set()
    _refreshed()
    assert ws.capabilities()["claude_version"] == "2.1.290"


def test_a_read_in_the_background_whose_thread_cannot_start_leaves_the_ask_refused_and_a_later_ask_reads(
        ws, monkeypatch):
    # no thread to be had for the read in the background. The ask
    # is refused in its own words all the same, 400 and not 500, and nothing is left as if a read ran, so a later ask
    # that meets the floor starts one, which keeps what it reads (a control)
    monkeypatch.setattr(wsmod, "CAPABILITIES_REFRESH_EVERY", 0)
    del ws.capabilities
    _silence(ws, "help", [1])                               # the first --help gets nothing, every later one answers
    assert ws.capabilities()["floor"] is True
    refused = []

    class NoThreadOnce(threading.Thread):
        def start(self):
            if self.name == "pp-capabilities-refresh" and not refused:
                refused.append(self.name)
                raise RuntimeError("can't start new thread")
            super().start()

    monkeypatch.setattr(threading, "Thread", NoThreadOnce)
    token = _asking_session(ws)
    r = _ask(ws, token, effort="max")
    assert refused and r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text
    _refreshed()
    assert ws.capabilities()["floor"] is True and len(_asked_of(ws)) == 2      # no read ran
    r = _ask(ws, token, task="Check it", effort="max")
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text
    _refreshed()
    assert ws.capabilities()["floor"] is False and len(_asked_of(ws)) == 4      # this one's read ran, and was kept


def test_a_read_in_the_background_that_raises_keeps_what_was_kept_and_a_later_ask_reads_again(ws, monkeypatch):
    # the read in the background raises (an OSError writing the wrapper it
    # starts, say). What is kept stays as it was, the floor, and nothing is left as if a read still ran, so a later ask
    # that meets the floor starts one, which keeps what it reads (a control)
    monkeypatch.setattr(wsmod, "CAPABILITIES_REFRESH_EVERY", 0)
    del ws.capabilities
    _silence(ws, "help", [1])
    kept = ws.capabilities()
    assert kept["floor"] is True
    real = ws._discover_capabilities
    raised = []

    def once():
        if not raised:
            raised.append(1)
            raise OSError("the wrapper could not be written")
        return real()

    ws._discover_capabilities = once
    token = _asking_session(ws)
    r = _ask(ws, token, effort="max")
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text
    _refreshed()
    assert raised == [1] and ws.capabilities() is kept     # what is kept, as it was
    r = _ask(ws, token, task="Check it", effort="max")
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text
    _refreshed()
    assert ws.capabilities()["floor"] is False             # the later read ran, and was kept
    r = _ask(ws, token, task="Review it", effort="max")
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text


# while the workspace keeps nothing of its claude (since the agent started, or since an update of Claude Code emptied
# what it kept), one ask at a time makes the first read, and an ask that names an effort and meets a read under way is
# refused at once (asks.BEING_READ): were each such ask to wait for the read in a thread of the agent's, one session's
# asks could hold them all

def test_an_ask_naming_an_effort_while_another_makes_the_first_read_is_refused_at_once_and_one_naming_none_taken(
        ws, monkeypatch):
    # nothing kept: the first ask that names an effort makes the first read, held here at claude's --help, and waits
    # for it. Another that names an effort meanwhile is refused at once, starting no read of its own; one that names
    # none is taken meanwhile; and once the read is let go, the first ask is decided against what it read
    del ws.capabilities                                     # the workspace's own read, not stubbed: nothing is kept
    reading, release = _slowed_help(monkeypatch)
    token = _asking_session(ws)
    root = os.path.realpath(ws.root)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(ws, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + token}) as c:
            try:
                first = asyncio.ensure_future(c.post("/ask/sessions", json={"program": "claude", "task": TASK,
                                                                           "effort": "max"}))
                while not reading.is_set():
                    assert not first.done(), first.result().text
                    await asyncio.sleep(0.01)
                # each answered while the first read is held: 20 seconds is a bound for a loaded machine, not a wait
                refused = await asyncio.wait_for(c.post("/ask/sessions", json={
                    "program": "claude", "task": "Check it", "effort": "xhigh"}), 20)
                plain = await asyncio.wait_for(c.post("/ask/sessions", json={"program": "claude", "task": "Note it"}),
                                               20)
                held = not first.done()
            finally:
                release.set()
            return refused, plain, held, await first

    refused, plain, held, first = asyncio.run(go())
    assert refused.status_code == 400 and refused.json()["detail"] == BEING_READ, refused.text
    assert plain.status_code == 200 and plain.json()["effort"] is None, plain.text
    assert held, "the first ask was answered before its read was let go"
    assert first.status_code == 200 and first.json()["effort"] == "max", first.text
    _refreshed()
    # one read of the claude, the first ask's: the ask refused meanwhile started none in the background
    assert _asked_of(ws) == [("--version", root, ""), ("--help", root, "")], _asked_of(ws)


def test_an_ask_that_may_not_be_made_is_refused_for_that_while_a_first_read_is_under_way(ws, monkeypatch):
    # an ask's refusals keep their order while a first read is under way (asks.Asks.ask: who may ask, which agent, then
    # its effort): a session started from an ask, which may not ask, is told so at once, not that the workspace is
    # reading its claude (a refusal of the read made before Asks.ask, in the route or in
    # Workspace.ask, would say the other)
    del ws.capabilities
    reading, release = _slowed_help(monkeypatch)
    token = _asking_session(ws)
    child = "ba9876543210"                  # a session started from an ask: its token may not ask
    ws.terminals[child] = _Running()
    os.makedirs(ws._dir(child))
    theirs = ws.asks.issue(child, "demo", False)

    async def go():
        app = agent.make_app(ws, SECRET)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                     headers={"Authorization": "Bearer " + token}) as c, \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                  headers={"Authorization": "Bearer " + theirs}) as d:
            try:
                first = asyncio.ensure_future(c.post("/ask/sessions", json={"program": "claude", "task": TASK,
                                                                           "effort": "max"}))
                while not reading.is_set():
                    assert not first.done(), first.result().text
                    await asyncio.sleep(0.01)
                refused = await asyncio.wait_for(d.post("/ask/sessions", json={
                    "program": "claude", "task": TASK, "effort": "max"}), 20)
                held = not first.done()
            finally:
                release.set()
            return refused, held, await first

    refused, held, first = asyncio.run(go())
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == "A session started from an ask may not ask for another.", refused.text
    assert held and first.status_code == 200 and first.json()["effort"] == "max", first.text


def test_an_ask_naming_an_effort_while_the_page_s_card_makes_the_first_read_is_refused_at_once(ws, monkeypatch):
    # the first read made by capabilities() itself, as the page's card asks for it (GET /capabilities), held at claude's
    # --help: an ask that names an effort meanwhile is refused at once, rather than waiting for that read; once
    # the read is let go, what it read is kept, and the same ask is taken without reading claude
    del ws.capabilities
    reading, release = _slowed_help(monkeypatch)
    token = _asking_session(ws)
    root = os.path.realpath(ws.root)

    async def go():
        app = agent.make_app(ws, SECRET)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                     headers={"Authorization": "Bearer " + SECRET}) as person, \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                  headers={"Authorization": "Bearer " + token}) as asker:
            try:
                card = asyncio.ensure_future(person.get("/capabilities"))
                while not reading.is_set():
                    assert not card.done(), card.result().text
                    await asyncio.sleep(0.01)
                refused = await asyncio.wait_for(asker.post("/ask/sessions", json={
                    "program": "claude", "task": TASK, "effort": "max"}), 20)
                held = not card.done()
            finally:
                release.set()
            return refused, held, await card

    refused, held, card = asyncio.run(go())
    assert refused.status_code == 400 and refused.json()["detail"] == BEING_READ, refused.text
    assert held, "the page's read ended before the ask was answered"
    assert card.status_code == 200 and card.json()["floor"] is False, card.text
    r = _ask(ws, token, effort="max")
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text
    _refreshed()
    assert _asked_of(ws) == [("--version", root, ""), ("--help", root, "")], _asked_of(ws)


def test_asks_naming_an_effort_posted_together_during_a_held_first_read_leave_one_waiting(ws, monkeypatch):
    # one session posts asks that name an effort together, each with its
    # own task, while nothing is kept and the first read is held at claude's --help, more of them than the agent's
    # default threads (asyncio.to_thread's). Were each to wait for the read in one of those threads, a plain
    # ask from another session would wait behind them. One of them makes the read and waits for it, every other is
    # answered at once, refused, and the other session's plain ask is answered meanwhile
    del ws.capabilities
    reading, release = _slowed_help(monkeypatch)
    token = _asking_session(ws)
    other = "ba9876543210"
    ws.terminals[other] = _Running()
    os.makedirs(ws._dir(other))
    theirs = ws.asks.issue(other, "demo", True)
    n = min(32, (os.cpu_count() or 1) + 4) + 4

    async def go():
        app = agent.make_app(ws, SECRET)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                     headers={"Authorization": "Bearer " + token}) as c, \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                  headers={"Authorization": "Bearer " + theirs}) as d:
            try:
                posted = [asyncio.ensure_future(c.post("/ask/sessions", json={
                    "program": "claude", "task": f"Part {i} of the work", "effort": "max"})) for i in range(n)]
                deadline = time.monotonic() + 30            # a bound for a loaded machine, not a wait
                # the read reaches claude's --help after its --version, which may come after the others are answered
                # (it starts a program, they do not: on a Mac, a while after), so the two are waited for together
                while (sum(p.done() for p in posted) < n - 1 or not reading.is_set()) and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                waiting = [p for p in posted if not p.done()]
                assert reading.is_set() and len(waiting) == 1, f"{len(waiting)} of {n} asks wait on the first read"
                answered = [p.result() for p in posted if p.done()]
                plain = await asyncio.wait_for(d.post("/ask/sessions", json={"program": "codex", "task": TASK}), 20)
                assert not waiting[0].done(), "the read was not held while the others were answered"
            finally:
                release.set()
            return answered, plain, await waiting[0]

    answered, plain, last = asyncio.run(go())
    assert [(r.status_code, r.json()["detail"]) for r in answered] == [(400, BEING_READ)] * (n - 1), \
        [r.text for r in answered]
    assert plain.status_code == 200 and plain.json()["program"] == "codex", plain.text
    assert last.status_code == 200 and last.json()["effort"] == "max", last.text
    assert sorted((a["from"], a["effort"] or "") for a in ws.asks.waiting()) == [(ASKER, "max"), (other, "")]


def test_a_first_read_an_ask_makes_that_fails_leaves_none_under_way_for_the_next_ask(ws):
    # the first read an ask makes raises (an OSError writing the wrapper it starts, say): that ask fails, and the next
    # ask that names an effort is not refused as meeting a read under way, but makes the first read itself and is
    # taken (a control: it holds where there is no such refusal at all)
    del ws.capabilities
    real = ws._discover_capabilities
    raised = []

    def once():
        if not raised:
            raised.append(1)
            raise OSError("the wrapper could not be written")
        return real()

    ws._discover_capabilities = once
    token = _asking_session(ws)
    with pytest.raises(OSError):
        _ask(ws, token, effort="max")
    r = _ask(ws, token, task="Check it", effort="max")
    assert raised == [1] and r.status_code == 200 and r.json()["effort"] == "max", r.text


def test_an_ask_naming_an_effort_when_an_update_empties_what_is_kept_during_a_read_in_the_background_is_refused(
        ws, monkeypatch):
    # an update of Claude Code that lands while a read in the background holds the read's lock empties what the
    # workspace keeps, so an ask that names an effort then would gain nothing by waiting for that read, which is not
    # kept, before making its own. It meets a read under way, and is refused at once; once the read
    # in the background has ended, the next such ask makes the first read of the new build itself and is taken
    del ws.capabilities
    _silence(ws, "help", [1])                               # the first --help gets nothing, every later one answers
    assert ws.capabilities()["floor"] is True
    reading, release = _slowed_help(monkeypatch)
    token = _asking_session(ws)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(ws, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + token}) as c:
            try:
                floor = await c.post("/ask/sessions", json={"program": "claude", "task": TASK, "effort": "max"})
                assert reading.wait(15), "no read was started in the background"
                ws._use_claude(ws.claude_path)                  # an update, meanwhile: what is kept is dropped
                refused = await asyncio.wait_for(c.post("/ask/sessions", json={
                    "program": "claude", "task": "Check it", "effort": "max"}), 20)
            finally:
                release.set()
            return floor, refused

    floor, refused = asyncio.run(go())
    assert floor.status_code == 400 and floor.json()["detail"] == UNKNOWN_ASKED, floor.text
    assert refused.status_code == 400 and refused.json()["detail"] == BEING_READ, refused.text
    _refreshed()
    r = _ask(ws, token, task="Review it", effort="max")
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text
    # the first read, the one in the background, which kept nothing, and the first read of the new build
    assert [a[0] for a in _asked_of(ws)] == ["--version", "--help"] * 3, _asked_of(ws)


def test_what_claude_offers_is_read_for_a_claude_ask_that_names_an_effort_alone(ws):
    read = []
    ws.capabilities = lambda: read.append(1) or dict(OFFERED)
    token = _asking_session(ws)
    assert _ask(ws, token).status_code == 200
    r = _ask(ws, token, program="codex", effort="max")
    assert r.status_code == 400 and r.json()["detail"] == asks.CLAUDE_ONLY, r.text
    assert read == []
    r = _ask(ws, token, effort="max")
    assert r.status_code == 200 and r.json()["effort"] == "max" and read == [1], r.text


def test_an_ask_whose_session_goes_while_its_claude_is_read_is_not_taken(ws):
    # through the agent's own route: a first read of what claude offers that takes a
    # while, and meanwhile what removing the asking session does in the event loop (Workspace.remove: its token revoked,
    # its asks withdrawn). The route reads claude first and the session's binding after: nothing is asked
    token = _asking_session(ws)
    reading = threading.Event()

    def slow():
        reading.set()
        time.sleep(1.0)
        return dict(OFFERED)

    ws.capabilities = slow

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(ws, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + token}) as c:
            asked = asyncio.ensure_future(c.post("/ask/sessions", json={"program": "claude", "task": TASK,
                                                                       "effort": "max"}))
            while not reading.is_set():
                assert not asked.done(), asked.result().text
                await asyncio.sleep(0.01)
            ws.asks.revoke(ASKER)
            ws.asks.withdraw_from(ASKER)
            return await asked

    r = asyncio.run(go())
    assert r.status_code == 401, r.text
    assert ws.asks.waiting() == [] and ws.ask_log(ASKER) == []


def test_an_ask_is_taken_with_nothing_awaited_after_its_session_is_read_again(ws):
    # the removal comes in the event loop at the first moment after the route reads the asking session's binding
    # again, and taking the ask takes a moment: taken with nothing awaited between, the ask is taken before the removal
    # and withdrawn with it, and none waits from a session that is gone; taken after an await (in a thread, say), the
    # removal comes first and the ask is left waiting
    token = _asking_session(ws)
    reads = []
    real_asker, real_ask = ws.asker, ws.asks.ask

    def asker(given):
        reads.append(given)
        if len(reads) == 2:                 # the route's second read, after the body and after claude was read
            asyncio.get_running_loop().call_soon(lambda: (ws.asks.revoke(ASKER), ws.asks.withdraw_from(ASKER)))
        return real_asker(given)

    def slow_ask(*args, **kwargs):
        time.sleep(0.2)
        return real_ask(*args, **kwargs)

    ws.asker, ws.asks.ask = asker, slow_ask
    r = _ask(ws, token, effort="max")
    assert len(reads) == 2 and r.status_code == 200, r.text
    assert ws.asks.waiting() == []
    assert [e["event"] for e in ws.ask_log(ASKER)] == ["asked"]


def test_the_same_ask_while_the_first_is_being_taken_is_refused_and_one_ask_waits(ws, monkeypatch):
    # a read of what the workspace's claude offers takes
    # a while, and the asking agent asks again before the first ask is taken, when `proofpane asks` cannot show it yet.
    # The same ask (the task as the ask keeps it, without the space around it) is refused, saying that the first is
    # still being taken; another ask of the session's naming no effort is taken; and once the first is taken, the same
    # ask may come again, as a second ask the agent means. The read is the first, made by the first
    # ask with nothing kept and held here until the others have been answered: another ask of the session's that names
    # an effort (the same at another effort, or from another base) and the same ask from another session are not
    # refused as the same ask, but at once, in words saying that the workspace is reading its claude (asks.BEING_READ),
    # not made to wait for the read and taken
    monkeypatch.setattr(asks, "MAX_OPEN_A_SESSION", 10)         # room for every ask this test makes
    token = _asking_session(ws)
    other = "ba9876543210"                  # another session on the repository whose agent asks the same
    ws.terminals[other] = _Running()
    os.makedirs(ws._dir(other))
    theirs = ws.asks.issue(other, "demo", True)
    reading, release = threading.Event(), threading.Event()

    def slow():
        reading.set()
        release.wait(30)
        return dict(OFFERED)

    ws.capabilities = slow
    same = {"program": "claude", "task": TASK, "effort": "max"}

    async def go():
        app = agent.make_app(ws, SECRET)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                     headers={"Authorization": "Bearer " + token}) as c, \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                  headers={"Authorization": "Bearer " + theirs}) as d:
            try:
                first = asyncio.ensure_future(c.post("/ask/sessions", json=same))
                while not reading.is_set():
                    assert not first.done(), first.result().text
                    await asyncio.sleep(0.01)
                again = await c.post("/ask/sessions", json=dict(same, task=TASK + "\n", base=None))
                listed = await c.get("/ask/asks")
                others = await asyncio.wait_for(asyncio.gather(
                    c.post("/ask/sessions", json={"program": "claude", "task": "Check it"}),
                    c.post("/ask/sessions", json=dict(same, effort="high")),
                    c.post("/ask/sessions", json=dict(same, base="main")),
                    d.post("/ask/sessions", json=same)), 20)
            finally:
                release.set()
            return await first, again, listed, others

    first, again, listed, others = asyncio.run(go())
    assert again.status_code == 400 and again.json()["detail"] == BEING_TAKEN, again.text
    assert listed.json()["asks"] == []                      # what `proofpane asks` would show meanwhile
    assert [r.status_code for r in [first, others[0]]] == [200] * 2, [r.text for r in [first, *others]]
    assert [(r.status_code, r.json()["detail"]) for r in others[1:]] == [(400, BEING_READ)] * 3, \
        [r.text for r in others]
    assert sorted((a["from"], a["task"], a["base"] or "", a["effort"] or "") for a in ws.asks.waiting()) == [
        (ASKER, "Check it", "", ""), (ASKER, TASK, "", "max")]
    r = _ask(ws, token, effort="max")                       # once the first is taken, the same may come again
    assert r.status_code == 200, r.text
    assert len([a for a in ws.asks.waiting() if a["from"] == ASKER and a["task"] == TASK and a["effort"] == "max"
                and not a["base"]]) == 2


def _while_taken(w, token, first, again):
    """`first` posted to the agent's /ask/sessions with the asking session's token and held while it is being taken,
    its read of what claude offers slowed (Workspace.ask_offered itself, which an ask that names no effort reaches too,
    where it reads nothing), and `again` posted meanwhile: (the answers to `first` and to `again`)."""
    reading = threading.Event()
    real = w.ask_offered

    def slow(program, effort):
        if program == first["program"] and effort == first.get("effort") and not reading.is_set():
            reading.set()
            time.sleep(1.5)
        return real(program, effort)

    w.ask_offered = slow

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(w, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + token}) as c:
            held = asyncio.ensure_future(c.post("/ask/sessions", json=first))
            while not reading.is_set():
                assert not held.done(), held.result().text
                await asyncio.sleep(0.01)
            other = await c.post("/ask/sessions", json=again)
            assert not held.done(), "the first ask was taken before the second was asked"
            return await held, other

    return asyncio.run(go())


def test_another_agent_s_ask_for_the_same_task_and_base_is_taken_while_one_is_being_taken(ws):
    # what makes an ask the same ask (Asks._same_ask) holds its agent too, so an ask
    # of Codex for the task and the base of a Claude Code ask being taken is another ask, taken (a control: a key
    # without the agent fails it)
    token = _asking_session(ws)
    first, other = _while_taken(ws, token, {"program": "claude", "task": TASK}, {"program": "codex", "task": TASK})
    assert other.status_code == 200 and other.json()["program"] == "codex", other.text
    assert first.status_code == 200 and first.json()["program"] == "claude", first.text
    assert sorted(a["program"] for a in ws.asks.waiting()) == ["claude", "codex"]


@pytest.mark.parametrize("first", [{"effort": ""}, {}])
def test_the_same_ask_naming_no_effort_either_way_is_refused_while_the_first_is_being_taken(ws, first):
    # none and "" are the same effort to the guard, and an ask that names no effort
    # is guarded as one that names one is, so the same ask asked again with no effort while the first, naming "" or
    # none, is being taken is refused, and one ask waits (controls: a key that
    # keeps the effort as given, or a guard for an ask that names an effort alone, fails one of them)
    token = _asking_session(ws)
    held, again = _while_taken(ws, token, dict({"program": "claude", "task": TASK}, **first),
                               {"program": "claude", "task": TASK})
    assert again.status_code == 400 and again.json()["detail"] == BEING_TAKEN, again.text
    assert held.status_code == 200 and held.json()["effort"] is None, held.text
    assert [(a["task"], a["effort"]) for a in ws.asks.waiting()] == [(TASK, None)]


def test_an_ask_refused_is_no_longer_being_taken(ws):
    # the ask's place among those being taken goes however it ends: refused, the same ask asked again is refused for
    # what it is, not as one still being taken
    token = _asking_session(ws)
    for _ in range(2):
        r = _ask(ws, token, effort="turbo")
        assert r.status_code == 400 and r.json()["detail"] == \
            "An effort is one of low, medium, high, xhigh, max, ultracode.", r.text
    assert ws.asks.waiting() == []


def test_leave_to_start_agents_runs_at_start_is_refused_by_the_agent_s_own_route(ws):
    # what the app now passes on rather than drops, as the agent's route takes
    # it: true refused, the ask waiting and nothing made; false the default, and the session starts
    ws.set_asks_on(True)
    ask = ws.ask({"session": ASKER, "repo": "demo", "may_ask": True}, "claude", TASK)
    r = _through_agent(ws, "POST", f"/asks/{ask['id']}/start", {"may_dispatch": True})
    assert r.status_code == 400 and r.json()["detail"].startswith(OTHER_SETTING), r.text
    assert [w["id"] for w in ws.asks.waiting()] == [ask["id"]] and os.listdir(ws.sessions_dir) == []
    r = _through_agent(ws, "POST", f"/asks/{ask['id']}/start", {"may_dispatch": False})
    assert r.status_code == 200 and r.json()["effort"] is None, r.text
    assert "--effort" not in _Started.seen[-1] and ws.asks.waiting() == []


# an ask the person's Start holds: starting, for the session that asked, until that Start answers it
# or puts it back

def _slowed_help(monkeypatch):
    """claude's --help, wherever the workspace reads it (claude_cli.run_text), held until the test lets it go, as a
    claude slow to answer is: (the event set when such a read begins, the event that lets it go). A first Start reads it
    in the new worktree for the effort the person chose."""
    reading, release = threading.Event(), threading.Event()
    real = wsmod.claude_cli.run_text

    def slow(claude, env, cwd, *args):
        if args == ("--help",):
            reading.set()
            release.wait(30)
        return real(claude, env, cwd, *args)

    monkeypatch.setattr(wsmod.claude_cli, "run_text", slow)
    return reading, release


def _while_started(w, token, ask_id, body, reading, release):
    """The person's Start of an ask (`body`, through the agent's own route) and, while it reads claude's --help, the
    asking session's /ask/asks with its token and the person's /asks: (the asking session's asks then, the person's
    then, Start's answer, the asking session's asks after it)."""
    async def go():
        app = agent.make_app(w, SECRET)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                     headers={"Authorization": "Bearer " + SECRET}) as person, \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent",
                                  headers={"Authorization": "Bearer " + token}) as asker:
            started = asyncio.ensure_future(person.post(f"/asks/{ask_id}/start", json=body))
            while not reading.is_set():
                assert not started.done(), started.result().text
                await asyncio.sleep(0.01)
            during = (await asker.get("/ask/asks")).json()["asks"]
            waiting = (await person.get("/asks")).json()["asks"]
            release.set()
            answer = await started
            return during, waiting, answer, (await asker.get("/ask/asks")).json()["asks"]

    reading.clear()
    release.clear()
    return asyncio.run(go())


def test_an_ask_a_start_holds_is_starting_for_its_session_then_waiting_again_or_started(ws, monkeypatch):
    # while a first Start runs, its reads of claude among them, the asking session's /ask/asks says the ask is
    # starting, not gone (said gone, `proofpane ask --wait` would end there, exit 3, though a refused Start then puts
    # the ask back), and not waiting for the person either (their list does not show it);
    # after a refused Start it waits again, and after a Start that starts it is started
    token = _asking_session(ws)
    ask_id = _ask(ws, token, effort="max").json()["id"]
    reading, release = _slowed_help(monkeypatch)
    during, waiting, answer, after = _while_started(ws, token, ask_id, {"effort": "turbo"}, reading, release)
    assert [(a["id"], a["state"]) for a in during] == [(ask_id, "starting")] and waiting == []
    assert answer.status_code == 400 and answer.json()["detail"] == NOT_ONE, answer.text
    assert [(a["id"], a["state"], a["effort"]) for a in after] == [(ask_id, "waiting", "max")]
    during, waiting, answer, after = _while_started(ws, token, ask_id, {"effort": "max"}, reading, release)
    assert [(a["id"], a["state"]) for a in during] == [(ask_id, "starting")] and waiting == []
    assert answer.status_code == 200, answer.text
    assert [(a["id"], a["state"], a["session"], a["started_effort"]) for a in after] == [
        (ask_id, "started", answer.json()["id"], "max")]


@pytest.mark.parametrize("how", ["withdrawn", "switched off and on"])
def test_an_ask_cleared_while_its_start_runs_is_not_put_back_when_that_start_is_refused(ws, monkeypatch, how):
    # a Start that fails puts its ask back, but not one cleared while it ran: its session gone, its asks withdrawn as
    # Workspace.remove withdraws them, or the person's switch turned off and on again (put back, it would wait from a
    # session that was gone, or whose program held no token)
    token = _asking_session(ws)
    ask_id = _ask(ws, token, effort="max").json()["id"]
    reading, release = _slowed_help(monkeypatch)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(ws, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as person:
            started = asyncio.ensure_future(person.post(f"/asks/{ask_id}/start", json={"effort": "turbo"}))
            while not reading.is_set():
                assert not started.done(), started.result().text
                await asyncio.sleep(0.01)
            if how == "withdrawn":
                ws.asks.revoke(ASKER)
                ws.asks.withdraw_from(ASKER)
            else:
                for on in (False, True):
                    assert (await person.post("/asks/settings", json={"on": on})).status_code == 200
            release.set()
            return await started

    answer = asyncio.run(go())
    assert answer.status_code == 400 and answer.json()["detail"] == NOT_ONE, answer.text
    assert ws.asks.waiting() == [] and _through_agent(ws, "GET", "/asks").json()["asks"] == []


@pytest.mark.parametrize("meanwhile", ["asked", "answered"])
def test_an_ask_read_while_it_is_asked_or_answered_is_never_gone(ws, meanwhile):
    # the asking session's asks are read from the asks held, waiting or being started, before and again after its
    # record of them: an ask is held before its asking is written, and a Start's answer is written before the ask is
    # let go, so neither a new ask nor one being answered reads as gone between the two reads. Each
    # is made to happen while the record is read: an ask asked just before, and a Start's answer written just after
    ws.set_asks_on(True)
    os.makedirs(ws._dir(ASKER))
    bound = {"session": ASKER, "repo": "demo", "may_ask": True}
    if meanwhile == "answered":
        ask = ws.ask(bound, "codex", TASK)
        ws.asks.take(ask["id"], starting=True)
    real = ws.ask_log

    def read_meanwhile(session_id, limit=200):
        if meanwhile == "asked":
            ws.ask(bound, "codex", TASK)
            return real(session_id, limit)
        got = real(session_id, limit)
        ws.ask_answered(ask, "started", "fedcba987654")
        return got

    ws.ask_log = read_meanwhile
    assert [a["state"] for a in ws.asks_of(bound)["asks"]] == ["waiting" if meanwhile == "asked" else "starting"]


def _states_when_written(w, bound, event):
    """What the asking session reads of its asks (asks_of) at the moment an answer `event` is written on its record,
    before the write: a list, one entry for each such answer written from now on."""
    seen = []
    real = w._ask_log

    def written(session_id, entry):
        if entry.get("event") == event:
            seen.append([(a["id"], a["state"]) for a in w.asks_of(bound)["asks"]])
        real(session_id, entry)

    w._ask_log = written
    return seen


def test_at_the_moment_a_start_s_answer_is_written_its_session_reads_the_ask_as_starting(ws):
    # Workspace.ask_answered writes a Start's answer on the asking session's record
    # first, and only then lets the ask go (asks.Asks.answered), so a read of the session's asks finds it starting,
    # then started, never gone. At the moment the answer is written, it is still starting (a control:
    # an ask_answered that let the ask go first would leave a moment in which it reads as gone, and
    # `proofpane ask --wait` would end there, exit 3, for a Start that succeeds)
    ws.set_asks_on(True)
    os.makedirs(ws._dir(ASKER))
    bound = {"session": ASKER, "repo": "demo", "may_ask": True}
    ask = ws.ask(bound, "codex", TASK)
    seen = _states_when_written(ws, bound, "started")
    r = _through_agent(ws, "POST", f"/asks/{ask['id']}/start", {})
    assert r.status_code == 200, r.text
    assert seen == [[(ask["id"], "starting")]]
    assert [(a["id"], a["state"]) for a in ws.asks_of(bound)["asks"]] == [(ask["id"], "started")]


def test_a_refusal_holds_the_ask_until_it_is_written_so_its_session_reads_starting_then_refused(ws):
    # a refusal that took the ask with no hold and wrote the refusal after it would let a read of the asking
    # session's asks between the two find it gone, and `proofpane ask --wait` end there,
    # exit 3, saying it was not answered within a day or was withdrawn. The refusal holds the ask as a Start does
    # (asks.Asks.take with `starting`) until the refusal is written: at that moment the session reads it as starting,
    # then as refused, and it is held no longer
    ws.set_asks_on(True)
    os.makedirs(ws._dir(ASKER))
    bound = {"session": ASKER, "repo": "demo", "may_ask": True}
    ask = ws.ask(bound, "codex", TASK)
    seen = _states_when_written(ws, bound, "refused")
    r = _through_agent(ws, "POST", f"/asks/{ask['id']}/refuse")
    assert r.status_code == 200 and r.json()["asks"] == [], r.text
    assert seen == [[(ask["id"], "starting")]]
    assert [(a["id"], a["state"]) for a in ws.asks_of(bound)["asks"]] == [(ask["id"], "refused")]
    assert ws.asks.states_of(ASKER) == {} and ws.asks.waiting() == []


# ---- through the app's routes, with a real workspace agent -----------------------------------------------------------

def _stand_ins(tmp_path, monkeypatch, config=None):
    """claude: chat_stand_in.py, which says its version, its help (the efforts and modes claude_cli reads) and its
    sign-in as the real one does, writes each start down ($HOME/stand-in-runs.jsonl: its arguments, where it runs) and
    waits; `config` is a CONFIG of its own above it. codex: a session's program that writes its token and the agent's
    address where the test reads them, then waits."""
    bin_dir = tmp_path / "effortbin"
    bin_dir.mkdir()
    with open(os.path.join(HERE, "chat_stand_in.py")) as fh:
        stand_in = fh.read()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + (f"CONFIG = {config!r}\n" if config else "") + stand_in)
    (bin_dir / "codex").write_text("#!/bin/sh\n"
                                   'printf "%s\\n%s\\n" "${PP_ASK_TOKEN-unset}" "${PP_ASK_URL-unset}" > '
                                   '"$PP_SESSION_DIR/a.tmp"\n'
                                   'mv "$PP_SESSION_DIR/a.tmp" "$PP_SESSION_DIR/ask.env"\n'
                                   "sleep 600\n")
    for name in ("claude", "codex"):
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")


def _claude_as(tmp_path, config):
    """The stand-in claude written again in its place with a CONFIG of its own (none: as _stand_ins wrote it): Claude
    Code changed under the workspace, as an update, or another build the PATH gives, changes it."""
    with open(os.path.join(HERE, "chat_stand_in.py")) as fh:
        stand_in = fh.read()
    path = tmp_path / "effortbin" / "claude"
    path.write_text(f"#!{sys.executable}\n" + (f"CONFIG = {config!r}\n" if config else "") + stand_in)
    path.chmod(0o755)


class _Asking:
    """The person's workspace through the app, asks on, with a codex session whose agent asks (its own token)."""

    def __init__(self, client, h, env, tmp_path):
        self.client, self.h, self.home = client, h, tmp_path / "home"
        self.root, _ = _started(client, h, env)
        assert client.post(f"{W}/asks/settings", headers=h, json={"on": True}).json()["on"] is True
        r = client.post(f"{W}/sessions", headers=h, json={"repo": "demo", "program": "codex", "task": "Fix it"})
        assert r.status_code == 200, r.text
        self.sid = r.json()["id"]
        given = self.root / "sessions" / self.sid / "ask.env"
        deadline = time.time() + 15
        while not given.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.token, self.url = given.read_text().split("\n")[:2]

    def ask(self, **body):
        """An ask posted with the codex session's token to the workspace's /ask/sessions, as `proofpane` posts it."""
        return httpx.post(self.url + "/ask/sessions", headers={"Authorization": "Bearer " + self.token},
                          json=dict({"program": "claude", "task": TASK}, **body), timeout=90)

    def waiting(self):
        return self.client.get(f"{W}/asks", headers=self.h).json()["asks"]

    def sessions(self):
        return {s["id"] for s in self.client.get(f"{W}/sessions", headers=self.h).json()["sessions"]}

    def made(self):
        """What making a session leaves in the workspace, beside the list: the repository's
        worktrees and branches, and the sessions' folders."""
        repo = str(self.root / "repos" / "demo")
        return (git("worktree", "list", "--porcelain", cwd=repo), git("branch", "--list", cwd=repo),
                sorted(os.listdir(self.root / "sessions")))

    def start(self, ask_id, body):
        return self.client.post(f"{W}/asks/{ask_id}/start", headers=self.h, json=body)

    def starts(self, sid, n=1, wait=20):
        """At least `n` starts of session `sid`'s claude, each its arguments as the stand-in wrote them down, in order;
        fewer when no more came within `wait` seconds."""
        work = os.path.realpath(str(self.root / "sessions" / sid / "work"))
        path = self.home / "stand-in-runs.jsonl"
        deadline = time.time() + wait
        while True:
            # whole lines only: the stand-in appends a line of about 2 KB at each start while this reads, and the end of
            # a line it is still writing is not yet JSON; that line is read whole at the next poll
            text = path.read_text() if path.exists() else ""
            runs = [json.loads(line) for line in text[:text.rfind("\n") + 1].splitlines()]
            mine = [r["args"] for r in runs if os.path.realpath(r["cwd"]) == work]
            if len(mine) >= n or time.time() > deadline:
                return mine
            time.sleep(0.05)

    def stop(self, sid):
        assert self.client.post(f"{W}/sessions/{sid}/stop", headers=self.h).status_code == 200
        deadline = time.time() + 15
        while self.client.get(f"{W}/sessions/{sid}", headers=self.h).json()["running"]:
            assert time.time() < deadline, "the session did not stop"
            time.sleep(0.1)

    def start_again(self, sid):
        r = self.client.post(f"{W}/sessions/{sid}/start", headers=self.h, json={})
        assert r.status_code == 200, r.text

    def workspace_again(self):
        """The workspace stopped and started again: its agent is a new process, which keeps none of the old one's."""
        assert self.client.post(f"{W}/stop", headers=self.h).status_code == 200
        r = self.client.post(f"{W}/start", headers=self.h)
        assert r.status_code == 200 and r.json()["running"] is True, r.text


@pytest.fixture
def asking(client, local, env, tmp_path, monkeypatch):
    _stand_ins(tmp_path, monkeypatch)
    return _Asking(client, signed_up(client), env, tmp_path)


def test_the_starts_are_read_a_whole_line_at_a_time_and_a_line_still_being_written_at_the_next_poll(tmp_path):
    # _Asking.starts reads the stand-in's record of its starts while the stand-in may still be writing a line of it:
    # whole lines alone are read, and one still being written is read at a later poll, once it is whole (the part of a
    # line is not JSON)
    asking = object.__new__(_Asking)
    asking.root, asking.home = tmp_path / "ws", tmp_path / "home"
    work = asking.root / "sessions" / ASKER / "work"
    work.mkdir(parents=True)
    asking.home.mkdir()
    line = json.dumps({"args": ["--effort", "max", TASK], "cwd": str(work)})
    path = asking.home / "stand-in-runs.jsonl"
    path.write_text(line + "\n" + line[:40])
    assert asking.starts(ASKER, 2, wait=0.2) == [["--effort", "max", TASK]]
    path.write_text(line + "\n" + line + "\n")
    assert asking.starts(ASKER, 2, wait=0.2) == [["--effort", "max", TASK]] * 2


def test_an_ask_s_effort_started_as_asked_is_given_once_at_its_first_start_and_once_at_each_later_one(asking):
    r = asking.ask(effort="max")
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text
    (waiting,) = asking.waiting()
    assert (waiting["program"], waiting["effort"], waiting["task"]) == ("claude", "max", TASK)
    started = asking.start(waiting["id"], {"effort": "max"})
    assert started.status_code == 200, started.text
    sid = started.json()["id"]
    (first,) = asking.starts(sid)
    assert _effort(first) == ["max"] and first[-1] == TASK and first.index("--effort") == len(first) - 3
    asking.stop(sid)
    asking.start_again(sid)
    again = asking.starts(sid, 2)[1]
    assert _effort(again) == ["max"] and TASK not in again
    assert len(asking.starts(sid, 3, wait=1)) == 2          # one start each time, no more
    # the agent started since keeps none of its own: the app's record of the answer, as Start wrote it, is what this
    # start is given
    asking.workspace_again()
    asking.start_again(sid)
    assert _effort(asking.starts(sid, 3)[2]) == ["max"]
    assert len(asking.starts(sid, 4, wait=1)) == 3


def test_the_session_starts_with_the_person_s_choice_and_none_given_is_never_the_ask_s(asking):
    started = {}
    for choice in ({}, {"effort": ""}, {"effort": "low"}, {"effort": "ultracode"}):
        ask_id = asking.ask(effort="max").json()["id"]
        r = asking.start(ask_id, choice)
        assert r.status_code == 200, (choice, r.text)
        started[json.dumps(choice)] = r.json()["id"]
    efforts = {choice: _effort(asking.starts(sid)[0]) for choice, sid in started.items()}
    assert efforts == {"{}": [], '{"effort": ""}': [], '{"effort": "low"}': ["low"],
                       '{"effort": "ultracode"}': ["ultracode"]}
    # each later start, as the person chose (a page from before this, which sent {}, gave the default)
    for choice, sid in started.items():
        asking.stop(sid)
        asking.start_again(sid)
        assert _effort(asking.starts(sid, 2)[1]) == efforts[choice], choice


def test_a_later_start_takes_its_effort_from_copies_the_session_cannot_write(asking):
    from proofpane_open import store
    ask_id = asking.ask(effort="max").json()["id"]
    sid = asking.start(ask_id, {"effort": "low"}).json()["id"]        # the person's choice, not the ask's
    asking.starts(sid)
    path = asking.root / "sessions" / sid / "session.json"

    def written_there():
        """What the session's programs could write in its record, its folder being theirs."""
        record = json.loads(path.read_text())
        record.update(effort="xhigh", chat_settings={"effort": "xhigh"})
        path.write_text(json.dumps(record))

    def started_again(n):
        written_there()
        asking.start_again(sid)
        return _effort(asking.starts(sid, n)[n - 1])

    def app_copy(effort):
        with store.connect() as db:
            db.execute("UPDATE ade_ask_answers SET effort = ? WHERE session = ?", (effort, sid))

    asking.stop(sid)
    assert started_again(2) == ["low"]                      # the workspace's copy and the app's agree
    asking.stop(sid)
    app_copy(None)
    assert started_again(3) == ["low"]                      # the workspace's own, with the app's gone
    app_copy("low")
    asking.workspace_again()
    assert started_again(4) == ["low"]                      # an agent started since keeps none: the app's
    app_copy(None)
    asking.workspace_again()
    assert started_again(5) == []                           # neither: Claude Code's default, never the record's


@pytest.mark.parametrize("after, level, given, said", [
    # a claude older than 2.1.203, which does not take ultracode (claude_cli adds it from that version on)
    ({"VERSION": "2.1.150"}, "ultracode", [], NO_LONGER.format("ultracode")),
    # one whose help does not parse, so what it takes is not known (claude_cli's floor)
    ({"HELP_OK": False}, "max", [], NOT_READ.format("max")),
    # the same claude, which still offers it
    (None, "max", ["max"], None),
])
def test_through_the_app_a_later_start_gives_the_effort_only_while_its_claude_offers_it(asking, tmp_path, after,
                                                                                       level, given, said):
    # the session started at its level, then Claude Code changed under the
    # workspace and the workspace started again, so the app's record is what this start is given; the start's answer,
    # as the app passes it back, says when it gave none, and the app's record is left as it was
    from proofpane_open import store
    ask_id = asking.ask(effort=level).json()["id"]
    sid = asking.start(ask_id, {"effort": level}).json()["id"]
    assert _effort(asking.starts(sid)[0]) == [level]
    asking.stop(sid)
    _claude_as(tmp_path, after)
    asking.workspace_again()
    r = asking.client.post(f"{W}/sessions/{sid}/start", headers=asking.h, json={})
    assert r.status_code == 200, r.text
    assert r.json().get("effort_notice") == said
    assert _effort(asking.starts(sid, 2)[1]) == given
    with store.connect() as db:
        assert [row["effort"] for row in db.execute("SELECT effort FROM ade_ask_answers WHERE session = ?",
                                                    (sid,))] == [level]


def test_through_the_app_a_claude_replaced_under_the_running_agent_is_read_at_the_next_start(asking, tmp_path):
    # the claude rewritten as an older one while the agent
    # runs, the workspace not started again, as Claude Code's own updater or a downgrade changes the build under a
    # running agent of the local provider; each start reads the claude it runs
    ask_id = asking.ask(effort="ultracode").json()["id"]
    sid = asking.start(ask_id, {"effort": "ultracode"}).json()["id"]
    assert _effort(asking.starts(sid)[0]) == ["ultracode"]
    for n, config, given, said in ((2, {"VERSION": "2.1.150"}, [], NO_LONGER.format("ultracode")),
                                   (3, None, ["ultracode"], None)):
        asking.stop(sid)
        _claude_as(tmp_path, config)
        r = asking.client.post(f"{W}/sessions/{sid}/start", headers=asking.h, json={})
        assert r.status_code == 200 and r.json().get("effort_notice") == said, (config, r.text)
        assert _effort(asking.starts(sid, n)[n - 1]) == given, config


def test_through_the_app_each_start_reads_the_claude_a_pin_in_the_worktree_chooses(client, local, env, tmp_path,
                                                                                  monkeypatch):
    # a version manager that chooses Claude Code's build
    # by the folder it runs in (mise and asdf read a pin upward from there), which a repository or a session can carry.
    # What the workspace's own claude offers, read in its root, is what the ask is checked against; what decides at a
    # start is the claude that start runs, read in the session's worktree at that start
    _stand_ins(tmp_path, monkeypatch, config={"VERSION_PIN": ".claude-pin"})
    asking = _Asking(client, signed_up(client), env, tmp_path)
    ask_id = asking.ask(effort="ultracode").json()["id"]
    sid = asking.start(ask_id, {"effort": "ultracode"}).json()["id"]
    assert _effort(asking.starts(sid)[0]) == ["ultracode"]
    # a pin written in the session's worktree, as its programs could write one: an older claude there, without
    # ultracode; taken out, the claude before it again
    pin = asking.root / "sessions" / sid / "work" / ".claude-pin"
    for n, write, given, said in ((2, True, [], NO_LONGER.format("ultracode")), (3, False, ["ultracode"], None)):
        asking.stop(sid)
        if write:
            pin.write_text("2.1.150\n")
        else:
            pin.unlink()
        r = asking.client.post(f"{W}/sessions/{sid}/start", headers=asking.h, json={})
        assert r.status_code == 200 and r.json().get("effort_notice") == said, (write, r.text)
        assert _effort(asking.starts(sid, n)[n - 1]) == given, write
    # a repository that pins it: the ask, checked against the workspace's own claude, is taken; at Start the claude
    # that starts there, read in the new worktree, does not offer the level, so the Start is refused in that claude's
    # levels, the ask waiting and nothing of the session left (not the session started at Claude
    # Code's default), and Start at a level that claude offers then starts it
    repo = str(asking.root / "repos" / "demo")
    with open(os.path.join(repo, ".claude-pin"), "w") as fh:
        fh.write("2.1.150\n")
    git("add", ".claude-pin", cwd=repo)
    git("commit", "-q", "-m", "pin Claude Code", cwd=repo)
    asked = asking.ask(effort="ultracode")
    assert asked.status_code == 200 and asked.json()["effort"] == "ultracode", asked.text
    before = (asking.sessions(), asking.made())
    r = asking.start(asked.json()["id"], {"effort": "ultracode"})
    assert r.status_code == 400 and r.json()["detail"] == NOT_ONE_OLDER, r.text
    assert [w["id"] for w in asking.waiting()] == [asked.json()["id"]] and (asking.sessions(), asking.made()) == before
    r = asking.start(asked.json()["id"], {"effort": "max"})
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text
    assert _effort(asking.starts(r.json()["id"])[0]) == ["max"]


def test_the_app_waits_for_a_start_as_long_as_the_workspace_s_reads_of_its_claude_can_take(asking, monkeypatch):
    # the workspace reads the claude a start runs before it starts it, up
    # to three reads (the gate's floor's --version, then --version and --help for what it offers), each bounded by
    # claude_cli.TIMEOUT; the app waits for the start longer than the three together
    from proofpane_open.ade import workspace_routes
    from proofpane_workspace import claude_cli
    real = workspace_routes.client.call
    waited = []

    def call(endpoint, method, path, body=None, timeout=30):
        if method == "POST" and path.startswith("/sessions/") and path.endswith("/start"):
            waited.append(timeout)
        return real(endpoint, method, path, body, timeout)

    ask_id = asking.ask(effort="max").json()["id"]
    sid = asking.start(ask_id, {"effort": "max"}).json()["id"]
    asking.starts(sid)
    asking.stop(sid)
    monkeypatch.setattr(workspace_routes.client, "call", call)
    asking.start_again(sid)
    assert len(waited) == 1 and waited[0] > 3 * claude_cli.TIMEOUT, waited
    assert waited == [workspace_routes.START_WAIT]


def _waits(monkeypatch, path_starts):
    """The timeouts the app gives its calls to the workspace for a start whose path begins with `path_starts`, as they
    are made: a list the test reads."""
    from proofpane_open.ade import workspace_routes
    real = workspace_routes.client.call
    waited = []

    def call(endpoint, method, path, body=None, timeout=30):
        if method == "POST" and path.startswith(path_starts) and path.endswith("/start"):
            waited.append(timeout)
        return real(endpoint, method, path, body, timeout)

    monkeypatch.setattr(workspace_routes.client, "call", call)
    return waited


def test_the_app_waits_as_long_for_a_start_again_of_a_session_it_keeps_no_effort_for(asking, monkeypatch):
    # the workspace reads the claude of every claude terminal it starts again (the
    # gate's floor), and of one it keeps an effort for (the agent's own copy, after a first Start whose app record was
    # not written, say) what it offers, so the app waits START_WAIT whether or not it holds an effort itself: here for a
    # claude terminal the person started, for which it holds none (a control: a wait
    # given START_WAIT only when the app holds an effort fails it)
    from proofpane_open.ade import workspace_routes
    r = asking.client.post(f"{W}/sessions", headers=asking.h, json=MINE)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    asking.starts(sid)
    asking.stop(sid)
    waited = _waits(monkeypatch, "/sessions/")
    asking.start_again(sid)
    assert waited == [workspace_routes.START_WAIT], waited


def test_the_app_waits_for_a_first_start_from_an_ask_as_long_as_its_reads_of_claude_can_take(asking, monkeypatch):
    # a first Start from an ask makes the session, which the app waits FINISH_WAIT
    # for as it waits for any session's making, and, for an effort chosen, reads the claude that starts twice more
    # inside the workspace agent's lock, its version and its help, each bounded by claude_cli.TIMEOUT: the app waits
    # for any first Start from an ask ASK_START_WAIT, FINISH_WAIT and those two reads (not FINISH_WAIT alone)
    from proofpane_open.ade import workspace_routes
    from proofpane_workspace import claude_cli
    asked = [asking.ask(effort="max").json()["id"], asking.ask(task="Check it").json()["id"]]
    waited = _waits(monkeypatch, "/asks/")
    r = asking.start(asked[0], {"effort": "max"})
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text
    r = asking.start(asked[1], {})                          # one with no effort chosen is waited for as long
    assert r.status_code == 200 and r.json()["effort"] is None, r.text
    assert waited == [workspace_routes.FINISH_WAIT + 2 * claude_cli.TIMEOUT] * 2, waited
    assert waited == [workspace_routes.ASK_START_WAIT] * 2


def test_through_the_app_a_start_whose_claude_is_slow_to_read_is_waited_for_and_says_what_it_gave(asking, tmp_path):
    # the reads of the claude a start runs take longer
    # than 30 seconds here: about 33, the gate's floor's --version, then --version and --help,
    # each 11 seconds, within claude_cli.TIMEOUT, and its help names no levels. The start happens, at Claude Code's
    # default, and its answer, saying why, reaches the page, not a 502 from the app
    ask_id = asking.ask(effort="max").json()["id"]
    sid = asking.start(ask_id, {"effort": "max"}).json()["id"]
    assert _effort(asking.starts(sid)[0]) == ["max"]
    asking.stop(sid)
    asking.workspace_again()
    _claude_as(tmp_path, {"VERSION_DELAY": 11, "HELP_DELAY": 11, "HELP_OK": False})
    began = time.monotonic()
    r = asking.client.post(f"{W}/sessions/{sid}/start", headers=asking.h, json={})
    took = time.monotonic() - began
    assert r.status_code == 200 and r.json().get("effort_notice") == NOT_READ.format("max"), (took, r.text)
    assert took > 30, took
    assert _effort(asking.starts(sid, 2)[1]) == []


def test_the_app_keeps_as_its_copy_only_an_effort_of_a_level_s_shape(asking, monkeypatch):
    # what the workspace says a session started with is kept as the
    # app's copy only when it has a level's name's shape, as the workspace's agent takes one, else none, so the app
    # never sends a later start an effort the workspace would refuse
    from proofpane_open import store
    from proofpane_open.ade import workspace_routes
    real = workspace_routes.client.call

    def odd(endpoint, method, path, body=None, timeout=30):
        got = real(endpoint, method, path, body, timeout)
        if method == "POST" and path.startswith("/asks/") and path.endswith("/start"):
            return dict(got, effort="max high")
        return got

    ask_id = asking.ask(effort="max").json()["id"]
    monkeypatch.setattr(workspace_routes.client, "call", odd)
    sid = asking.start(ask_id, {"effort": "max"}).json()["id"]
    monkeypatch.setattr(workspace_routes.client, "call", real)
    with store.connect() as db:
        assert [row["effort"] for row in db.execute("SELECT effort FROM ade_ask_answers WHERE session = ?",
                                                    (sid,))] == [None]
    assert _effort(asking.starts(sid)[0]) == ["max"]
    asking.stop(sid)
    asking.start_again(sid)
    assert _effort(asking.starts(sid, 2)[1]) == ["max"]
    # the shape is the workspace's own: the app cannot import the workspace's agent, so its copy of the pattern is held
    # to it here
    assert workspace_routes._EFFORT.pattern == wsmod._EFFORT.pattern


def test_the_app_records_the_effort_the_workspace_said_it_started_with_not_the_choice_it_sent(asking, monkeypatch):
    # a workspace agent from before this (a workspace image older than the app) takes no effort at Start and says
    # none: the app records none, as it records the account the workspace says, so no later start is given one
    from proofpane_open.ade import workspace_routes
    real = workspace_routes.client.call

    def older(endpoint, method, path, body=None, timeout=30):
        if method == "POST" and path.startswith("/asks/") and path.endswith("/start"):
            got = real(endpoint, method, path, {k: v for k, v in (body or {}).items() if k != "effort"}, timeout)
            return {k: v for k, v in got.items() if k != "effort"}
        return real(endpoint, method, path, body, timeout)

    ask_id = asking.ask(effort="max").json()["id"]
    monkeypatch.setattr(workspace_routes.client, "call", older)
    sid = asking.start(ask_id, {"effort": "low"}).json()["id"]
    monkeypatch.setattr(workspace_routes.client, "call", real)
    assert _effort(asking.starts(sid)[0]) == []
    asking.workspace_again()                                # the agent updated since: it keeps none of the old one's
    asking.start_again(sid)
    assert _effort(asking.starts(sid, 2)[1]) == []


OLDER = ("The session started at Claude Code's default effort, not at low, the effort you chose. This workspace's "
         "agent is older than the app and takes no effort at Start.")


@pytest.mark.parametrize("agent_is, chosen, said", [
    ("older", {"effort": "low"}, OLDER),
    ("older", {}, None),                    # nothing chosen: nothing to tell
    ("older", {"effort": ""}, None),
    ("current", {"effort": "low"}, None),
    # one that says another effort than the one chosen, as none does: said too, with the effort it said
    ("other", {"effort": "low"}, "The session started at high effort, not at low, the effort you chose."),
])
def test_the_person_is_told_when_the_workspace_does_not_say_it_started_at_the_effort_they_chose(asking, monkeypatch,
                                                                                              agent_is, chosen, said):
    # a workspace agent from before this (the effort dropped both
    # ways), and the app's answer to Start, which the page shows
    from proofpane_open.ade import workspace_routes
    real = workspace_routes.client.call

    def answering(endpoint, method, path, body=None, timeout=30):
        if method == "POST" and path.startswith("/asks/") and path.endswith("/start") and agent_is != "current":
            got = real(endpoint, method, path, {k: v for k, v in (body or {}).items() if k != "effort"}, timeout)
            return {k: v for k, v in got.items() if k != "effort"} if agent_is == "older" else dict(got, effort="high")
        return real(endpoint, method, path, body, timeout)

    ask_id = asking.ask(effort="max").json()["id"]
    monkeypatch.setattr(workspace_routes.client, "call", answering)
    r = asking.start(ask_id, chosen)
    assert r.status_code == 200, r.text
    assert r.json().get("effort_notice") == said


@pytest.mark.parametrize("chosen, says, told", [
    # another effort than the one chosen: the person is told, in the app's words
    ("low", "high", "The session started at high effort, not at low, the effort you chose."),
    # the one chosen, without a level's name's shape, which a workspace that took it would say: no copy of it either,
    # and nothing said, not "started at max high effort, not at max high"
    ("max high", "max high", None),
])
def test_the_app_keeps_no_copy_of_an_effort_the_workspace_says_that_is_not_the_person_s_choice(asking, monkeypatch,
                                                                                               chosen, says, told):
    # what the workspace says a session started with is kept as the app's copy only
    # when it is the effort the person chose and has a level's name's shape, else none (not any value of that
    # shape), so a later start the app's copy decides gives no effort the person did not choose
    from proofpane_open import store
    from proofpane_open.ade import workspace_routes
    real = workspace_routes.client.call

    def saying(endpoint, method, path, body=None, timeout=30):
        if method == "POST" and path.startswith("/asks/") and path.endswith("/start"):
            got = real(endpoint, method, path, {k: v for k, v in (body or {}).items() if k != "effort"}, timeout)
            return dict(got, effort=says)
        return real(endpoint, method, path, body, timeout)

    ask_id = asking.ask(effort="max").json()["id"]
    monkeypatch.setattr(workspace_routes.client, "call", saying)
    r = asking.start(ask_id, {"effort": chosen})
    monkeypatch.setattr(workspace_routes.client, "call", real)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    with store.connect() as db:
        assert [row["effort"] for row in db.execute("SELECT effort FROM ade_ask_answers WHERE session = ?",
                                                    (sid,))] == [None]
    assert r.json().get("effort_notice") == told
    # started with none (the workspace was sent none), so the workspace keeps none either: the app's copy decides a
    # later start, and it holds none
    assert _effort(asking.starts(sid)[0]) == []
    asking.stop(sid)
    asking.start_again(sid)
    assert _effort(asking.starts(sid, 2)[1]) == []


def test_an_effort_its_claude_does_not_offer_is_refused_when_asked_and_at_start(asking):
    r = asking.ask(effort="turbo")
    assert r.status_code == 400 and r.json()["detail"] == NOT_ONE, r.text
    ask_id = asking.ask(effort="max").json()["id"]
    before = (asking.sessions(), asking.made())
    # at Start it is checked as it is when asked, against what the claude that starts offers, read in the new worktree
    # (not against the workspace's own claude, nor the session started
    # at Claude Code's default): refused there, the ask still waits as the agent asked it, the person's choice not
    # written into it, and nothing of the session is left
    r = asking.start(ask_id, {"effort": "turbo"})
    assert r.status_code == 400 and r.json()["detail"] == NOT_ONE, r.text
    assert [(w["id"], w["effort"]) for w in asking.waiting()] == [(ask_id, "max")]
    assert (asking.sessions(), asking.made()) == before
    # answered as it may be, it starts at the level chosen
    r = asking.start(ask_id, {"effort": "max"})
    assert r.status_code == 200 and r.json()["effort"] == "max", r.text
    assert _effort(asking.starts(r.json()["id"])[0]) == ["max"]


@pytest.mark.parametrize("program, asked, body, said", [
    ("codex", {}, {"effort": "max"}, asks.CLAUDE_ONLY),
    ("claude", {"effort": "max"}, {"effort": "low", "model": "opus"}, OTHER_SETTING),
    ("claude", {"effort": "max"}, {"model": "opus"}, OTHER_SETTING),
    ("claude", {"effort": "max"}, {"effort": "low", "permission_mode": "plan"}, OTHER_SETTING),
    ("claude", {"effort": "max"}, {"permission_mode": "plan"}, OTHER_SETTING),
    # leave to start agents' runs, refused as the agent refuses it, not dropped by the app
    ("claude", {"effort": "max"}, {"may_dispatch": True}, OTHER_SETTING),
    ("claude", {"effort": "max"}, {"effort": "low", "may_dispatch": True}, OTHER_SETTING),
])
def test_at_start_every_setting_but_a_claude_ask_s_effort_is_refused_and_the_ask_waits(asking, program, asked, body,
                                                                                       said):
    ask_id = asking.ask(program=program, **asked).json()["id"]
    before = asking.sessions()
    r = asking.start(ask_id, body)
    assert r.status_code == 400 and r.json()["detail"].startswith(said), r.text
    assert [(w["id"], w["effort"]) for w in asking.waiting()] == [(ask_id, asked.get("effort"))]
    assert asking.sessions() == before
    # answered as it may be, it starts
    r = asking.start(ask_id, {})
    assert r.status_code == 200 and r.json()["program"] == program, r.text


def test_an_effort_on_another_agent_s_ask_is_refused_by_the_workspace_whatever_posts_it(asking):
    r = asking.ask(program="codex", effort="max")
    assert r.status_code == 400 and r.json()["detail"] == asks.CLAUDE_ONLY, r.text
    env = dict(os.environ, PP_ASK_TOKEN=asking.token, PP_ASK_URL=asking.url)
    run = subprocess.run([sys.executable, COMMAND, "ask", "--agent", "codex", "--effort", "max", "--task", TASK],
                         env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 1 and asks.CLAUDE_ONLY in run.stderr, run
    assert asking.waiting() == []


def test_a_terminal_claude_the_person_starts_takes_no_setting(asking):
    before = asking.sessions()
    r = asking.client.post(f"{W}/sessions", headers=asking.h, json=dict(MINE, chat_settings={"effort": "max"}))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Only a chat has a model, an effort and a permission mode to choose."
    assert asking.sessions() == before
    r = asking.client.post(f"{W}/sessions", headers=asking.h, json=MINE)
    assert r.status_code == 200, r.text
    assert _effort(asking.starts(r.json()["id"])[0]) == []


def test_a_start_is_given_no_effort_the_app_recorded_for_another_person(asking):
    # the app's record is read for the person starting the session alone: another person's answer naming a session of
    # the same id is not this session's
    from proofpane_open import store
    r = asking.client.post(f"{W}/sessions", headers=asking.h, json=MINE)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    asking.starts(sid)
    with store.connect() as db:
        db.execute("INSERT INTO ade_ask_answers (user_id, ask_id, from_session, program, answer, session, effort, at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("someone-else", "a" * 12, "b" * 12, "claude", "started", sid,
                                                       "max", int(time.time())))
    asking.stop(sid)
    asking.start_again(sid)
    assert _effort(asking.starts(sid, 2)[1]) == []


def test_the_asking_agent_reads_the_effort_it_asked_for_and_the_one_its_session_started_with(asking):
    env = dict(os.environ, PP_ASK_TOKEN=asking.token, PP_ASK_URL=asking.url)
    waited = subprocess.Popen([sys.executable, COMMAND, "--json", "ask", "--agent", "claude", "--effort", "max",
                               "--task", TASK, "--wait", "--timeout", "120"], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 30
        while not asking.waiting():
            assert time.time() < deadline and waited.poll() is None, waited.stderr.read() if waited.poll() else ""
            time.sleep(0.1)
        (first,) = asking.waiting()
        sid = asking.start(first["id"], {"effort": "low"}).json()["id"]
        out, err = waited.communicate(timeout=60)
    finally:
        if waited.poll() is None:
            waited.kill()
            waited.wait()
    got = json.loads(out)
    assert (got["state"], got["session"], got["effort"], got["started_effort"]) == ("started", sid, "max", "low"), err
    second = asking.ask().json()["id"]
    assert asking.start(second, {}).status_code == 200
    asking.ask(effort="high")
    listed = json.loads(subprocess.run([sys.executable, COMMAND, "--json", "asks"], env=env, capture_output=True,
                                       text=True, timeout=60).stdout)["asks"]
    assert [(a["effort"], a["state"], a["started_effort"]) for a in listed] == [
        ("max", "started", "low"), (None, "started", None), ("high", "waiting", None)]
    lines = subprocess.run([sys.executable, COMMAND, "asks"], env=env, capture_output=True, text=True,
                           timeout=60).stdout.splitlines()
    assert [line.rsplit("  ", 1)[1] for line in lines] == [
        "(effort asked: max, started: low)", "(effort asked: none, started: default)", "(effort asked: high)"]


def test_a_session_continued_in_a_new_one_takes_no_effort_from_the_ask_of_the_one_it_continues(asking):
    # Continue in a new session starts a session of the person's own: the old session's effort stays its own
    ask_id = asking.ask(effort="max").json()["id"]
    old = asking.start(ask_id, {"effort": "xhigh"}).json()["id"]
    asking.starts(old)
    r = asking.client.post(f"{W}/sessions/{old}/continue", headers=asking.h, json={"program": "claude", "task": TASK})
    assert r.status_code == 200, r.text
    new = r.json()["session"]["id"]
    assert _effort(asking.starts(new)[0]) == []
    asking.stop(new)
    asking.start_again(new)
    assert _effort(asking.starts(new, 2)[1]) == []
    asking.stop(old)
    asking.start_again(old)
    assert _effort(asking.starts(old, 2)[1]) == ["xhigh"]


def test_no_effort_is_taken_while_its_claude_does_not_say_which_it_takes(client, local, env, tmp_path, monkeypatch):
    # a claude whose help does not parse (claude_cli's floor): the claude the PATH gives may not take the flag
    _stand_ins(tmp_path, monkeypatch, config={"HELP_OK": False})
    asking = _Asking(client, signed_up(client), env, tmp_path)
    assert client.get(f"{W}/capabilities", headers=asking.h).json()["floor"] is True
    # asked: what the workspace keeps is the floor, so the ask is refused at once, saying that the workspace reads its
    # claude again at most once a minute; the read it starts in the background, not in the ask's request, says no
    # levels either
    r = asking.ask(effort="max")
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN_ASKED, r.text
    ask_id = asking.ask().json()["id"]
    before = (asking.sessions(), asking.made())
    # at Start, the claude that starts is read, in the new worktree, for its levels, and its help says none there
    # either: the Start is refused, the ask still waiting and nothing of the session left
    # (not the session started at Claude Code's default)
    r = asking.start(ask_id, {"effort": "max"})
    assert r.status_code == 400 and r.json()["detail"] == UNKNOWN, r.text
    assert [w["id"] for w in asking.waiting()] == [ask_id]
    assert (asking.sessions(), asking.made()) == before
    r = asking.start(ask_id, {})
    assert r.status_code == 200 and r.json()["effort"] is None, r.text
    assert _effort(asking.starts(r.json()["id"])[0]) == []


def test_the_app_keeps_the_effort_in_its_record_of_answers_and_a_store_made_before_gets_the_column(tmp_path):
    import sqlite3
    from proofpane_open import store

    def columns(path):
        with store.connect(path) as conn:
            return [row[1] for row in conn.execute("PRAGMA table_info(ade_ask_answers)")]

    path = str(tmp_path / "s.db")
    assert columns(path).count("effort") == 1
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE ade_ask_answers DROP COLUMN effort")      # as a store made before asks named an effort
    db.commit()
    db.close()
    assert columns(path).count("effort") == 1
