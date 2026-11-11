"""The budget check, the workspace's side: while the workspace is paired with the owner's Proofpane organisation, the
workspace agent asks the
product's budget check (/api/v1/devices/budget-check) before each start of a terminal agent's session (the
person's own agent among them, test_workspace_budget_programs.py), of a chat's claude, of an agent's run, of a task's
attempt and of an attempt of a requirement's manager. A start the check
refuses is refused in the product's own words and starts nothing; a product that does not answer lets the start go on,
and the start says so; an answer that could not read every budget still refuses where its kind says so; an agent's
run's Agent SDK budget is the smaller of its own and the room the check left. Proofpane is the stand-in
(proofpane_stand_in.py) and every program a stand-in too: the terminals' claude and codex below, the chat stand-in for
agents' runs and tasks' attempts, and, in this process, a stand-in for the Agent SDK's client that never starts a
claude; no real claude, codex or Proofpane, and nothing beyond this machine."""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import types

import pytest

from proofpane_stand_in import DEVICE, TOKEN, Proofpane, budget_answer, posted, proofpane  # noqa: F401  (a fixture)
from test_controller_managers_workspace import _closes, _sends, managed, taken
from test_controller_workspace import _Term, on, plan, ws  # noqa: F401  (ws is a fixture)
from test_task_attempt import TASK, message
from workspace_fixtures import Agent, make_repo

CHECK = "/api/v1/devices/budget-check"
# the product's own words for a person's spent budget, as the stand-in gives them (budget_check._words)
REFUSED = budget_answer(refused=("subscription",))["kinds"]["subscription"]["message"]
WAIT = 5                        # the default wait for the check, in seconds (budget_check.WAIT)
NOT_MADE = "Proofpane's budget check could not be made: {}. This start went on without it."
# the workspace's own words for a refusal Proofpane gave none for (budget_check.NO_WORDS)
NO_WORDS = "Proofpane's budget check refused this start's kind of spend ({}), and gave no words for it."
UNREAD_SCOPES = ("Proofpane could not read the person's and the department's budgets, so this start was not checked "
                 "against them.")
AGENT = "b4b4b4b4b4b4"
CHAT_STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
ENDED = ("done", "failed", "blocked", "stopped", "skipped")
# a terminal's claude or codex as these tests run it: claude's version where the gate's floor asks it, and otherwise
# each start written down in the home, under the program's name, by its session, then running until stopped
TERMINAL = '''import os, sys, time
if sys.argv[1:2] in (["-v"], ["--version"]):
    print("2.1.274 (Claude Code)")
    sys.exit(0)
with open(os.path.join(os.environ["HOME"], "started-" + os.path.basename(sys.argv[0])), "a") as fh:
    fh.write(os.environ.get("PP_SESSION_ID", "") + "\\n")
while True:
    time.sleep(0.1)
'''
# The wait a start made in this process (ws) gives the check, in every case of this file: far past any stall a loaded
# machine gives the stand-in's answer, so such a case's outcome is the answer's and not the clock's. The staging
# repository's CI run 43094086232 (shard 11, 2026-11-11) stalled one past the module's 5 seconds. The cases that run
# the workspace agent as a process of its own (w) keep the module's WAIT, the wait's own case among them.
NO_STALL_WAIT = 60.0


@pytest.fixture(autouse=True)
def _a_wait_no_stall_reaches(monkeypatch):
    from proofpane_workspace import budget_check
    monkeypatch.setattr(budget_check, "WAIT", NO_STALL_WAIT)


def pair(home, cloud):
    """The workspace's pairing as core_plugin.pair writes it."""
    folder = os.path.join(str(home), ".proofpane", "proofpane-core")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "config.json"), "w") as fh:
        json.dump({"cloud_url": cloud, "device_id": DEVICE, "token": TOKEN, "name": "ws",
                   "paired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60)), "holds": []}, fh)


def checks():
    """The bodies of the budget checks Proofpane was asked, in order."""
    return posted(CHECK)


def quiet():
    """The stand-in answering as it does by default: every kind of spend allowed, no limit counted."""
    Proofpane.budget, Proofpane.redirect, Proofpane.revoked = None, None, False
    Proofpane.garbled, Proofpane.deep, Proofpane.slow = (), (), ()


def worktrees(root):
    """The worktrees of the workspace's repository `demo`: its own checkout, and one more for each session or run."""
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=os.path.join(root, "repos", "demo"),
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.startswith("worktree ")]


class Ws:
    """A workspace agent (workspace_fixtures.Agent) with a repository `demo`, whose claude and codex are TERMINAL; with
    `chat`, its claude is the chat stand-in, which an agent's run and a task's attempt start through the Agent SDK
    where it is installed, so that a start past the check ends however this machine lets it."""

    def __init__(self, tmp_path, chat=False):
        self.bin = tmp_path / "bin"
        self.bin.mkdir(exist_ok=True)
        for program in ("claude", "codex"):
            code = open(CHAT_STAND_IN).read() if chat and program == "claude" else TERMINAL
            (self.bin / program).write_text(f"#!{sys.executable}\n" + code)
            (self.bin / program).chmod(0o755)
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self.root = str(tmp_path / "ws")
        make_repo(os.path.join(self.root, "repos", "demo"))
        self.agent = Agent(self.root, str(self.home), {"PATH": f"{self.bin}:{os.environ['PATH']}",
                                                      "PP_TASK_CLAUDE": str(self.bin / "claude")})
        self.http = self.agent.http

    def stop(self):
        self.agent.close()

    def start_session(self, program="claude", **more):
        return self.http.post("/sessions", json=dict({"repo": "demo", "program": program,
                                                      "task": "Look at the open issues"}, **more))

    def started(self):
        """{program: the sessions it was started for, once each start}: every start of a terminal's program."""
        out = {}
        for name in sorted(os.listdir(self.home)):
            if name.startswith("started-"):
                out[name[len("started-"):]] = (self.home / name).read_text().split()
        return out

    def wait_started(self, program, sid, n=1, timeout=15):
        deadline = time.time() + timeout
        while self.started().get(program, []).count(sid) < n:
            assert time.time() < deadline, (program, sid, self.started())
            time.sleep(0.05)

    def sessions(self):
        """The sessions the workspace holds a folder for (a record, a worktree): their ids."""
        return sorted(n for n in os.listdir(os.path.join(self.root, "sessions")) if re.fullmatch(r"[0-9a-f]{12}", n))

    def put_agent(self, **over):
        body = dict({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo",
                     "author": {"name": "Ana", "email": "ana@example.org"}}, **over)
        r = self.http.put(f"/agents/{AGENT}", json=body)
        assert r.status_code == 200, r.text

    def queue(self, body):
        r = self.http.post(f"/agents/{AGENT}/runs", json=body)
        assert r.status_code == 200, r.text
        return r.json()["run_id"]

    def until_ended(self, run_id, timeout=60):
        deadline = time.time() + timeout
        while True:
            (view,) = [v for v in self.http.get("/agents/runs?since=0").json()["runs"] if v["run_id"] == run_id]
            if view["state"] in ENDED:
                return view
            assert time.time() < deadline, view
            time.sleep(0.05)


@pytest.fixture
def w(tmp_path):
    space = Ws(tmp_path)
    yield space
    space.stop()


@pytest.fixture
def runs(tmp_path):
    space = Ws(tmp_path, chat=True)
    yield space
    space.stop()


# ---- a claude or codex terminal --------------------------------------------------------------------------------------

def test_a_spent_scope_refuses_a_claude_and_a_codex_terminal_in_the_product_s_words_and_makes_nothing(w, proofpane):
    pair(w.home, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    for program in ("claude", "codex"):
        r = w.start_session(program)
        assert (r.status_code, r.json().get("detail")) == (400, REFUSED), (program, r.text)
    time.sleep(0.5)
    assert w.sessions() == [] and w.http.get("/sessions").json()["sessions"] == []      # no record, no worktree
    assert len(worktrees(w.root)) == 1
    assert w.started() == {}                                                             # no program started
    assert len(checks()) == 2


def test_a_refusal_that_gives_no_words_refuses_the_start_in_the_workspace_s_own_and_makes_nothing(w, proofpane):
    # a refusal stands whatever else its answer holds, as the plugin's hook reads one, and a
    # refusal Proofpane gave no words for is said in the workspace's own (budget_check.NO_WORDS), naming the kind
    pair(w.home, proofpane)
    room = budget_answer()
    for words in (None, "", "   "):
        refusal = dict(room["kinds"]["subscription"], allowed=False, message=words)
        Proofpane.budget = dict(room, kinds=dict(room["kinds"], subscription=refusal))
        r = w.start_session("claude")
        assert (r.status_code, r.json().get("detail")) == (400, NO_WORDS.format("subscription")), (words, r.text)
    time.sleep(0.5)
    assert w.sessions() == [] and w.http.get("/sessions").json()["sessions"] == []      # no record, no worktree
    assert len(worktrees(w.root)) == 1
    assert w.started() == {}                                                             # no program started
    assert len(checks()) == 3


def test_a_spent_scope_refuses_a_terminal_started_again_and_leaves_its_program_stopped(w, proofpane):
    pair(w.home, proofpane)
    made = {}
    for program in ("claude", "codex"):
        r = w.start_session(program)
        assert r.status_code == 200, r.text
        made[program] = r.json()["id"]
        w.wait_started(program, made[program])
        assert w.http.post(f"/sessions/{made[program]}/stop").status_code == 200
    Proofpane.budget = budget_answer(refused=("subscription",))
    for program, sid in made.items():
        r = w.http.post(f"/sessions/{sid}/start", json={})
        assert (r.status_code, r.json().get("detail")) == (400, REFUSED), (program, r.text)
    time.sleep(0.5)
    for program, sid in made.items():
        assert w.http.get(f"/sessions/{sid}").json()["running"] is False
        assert w.started()[program] == [sid]                                # started once, never again


def test_control_with_room_a_claude_and_a_codex_terminal_start_and_say_nothing_of_the_check(w, proofpane):
    pair(w.home, proofpane)
    Proofpane.budget = budget_answer(room=0.0)          # every kind allowed, its room spent to the cent: it goes on
    for program in ("claude", "codex"):
        r = w.start_session(program)
        assert r.status_code == 200, r.text
        view = r.json()
        assert view["running"] is True and "budget_notice" not in view, view
        w.wait_started(program, view["id"])


def test_control_unpaired_nothing_is_asked_and_every_start_goes_on(w, proofpane):
    Proofpane.budget = budget_answer(refused=("subscription", "own_key", "gateway"))     # were it asked, all refused
    for program in ("claude", "codex"):
        r = w.start_session(program)
        assert r.status_code == 200 and "budget_notice" not in r.json(), r.text
        sid = r.json()["id"]
        w.wait_started(program, sid)
        assert w.http.post(f"/sessions/{sid}/stop").status_code == 200
        r = w.http.post(f"/sessions/{sid}/start", json={})
        assert r.status_code == 200 and "budget_notice" not in r.json(), r.text
        w.wait_started(program, sid, n=2)
    assert Proofpane.asked == []


def test_each_terminal_start_asks_the_check_with_the_device_and_its_token_alone(w, proofpane):
    pair(w.home, proofpane)
    sid = w.start_session("claude").json()["id"]
    w.wait_started("claude", sid)
    w.http.post(f"/sessions/{sid}/stop")
    assert w.http.post(f"/sessions/{sid}/start", json={}).status_code == 200
    assert w.start_session("codex").status_code == 200
    assert checks() == [{"device_id": DEVICE, "token": TOKEN}] * 3
    assert {a["agent"] for a in Proofpane.asked if a["path"] == CHECK} == {"proofpane-workspace-budget/1"}


def test_a_product_that_does_not_answer_lets_the_start_go_on_and_its_answer_says_why(w, proofpane):
    pair(w.home, proofpane)
    room = budget_answer()
    kind = room["kinds"]["subscription"]
    cases = [
        ("down", {"budget": "down"}, "Proofpane answered 503"),
        ("revoked", {"revoked": True}, "Proofpane answered 401"),
        ("redirect", {"redirect": proofpane + "/elsewhere"},
         "Proofpane answered with a redirect, which is not followed"),
        ("garbled", {"garbled": (CHECK,)}, "Proofpane's answer could not be read (BadStatusLine)"),
        ("too deep", {"deep": (CHECK,)}, "Proofpane's answer is not a budget check's"),
        ("an empty answer", {"budget": {}}, "Proofpane's answer is not a budget check's"),
        ("its kind left out", {"budget": dict(room, kinds={})}, "Proofpane's answer is not a budget check's"),
        ("checked not a truth", {"budget": dict(room, checked="yes")}, "Proofpane's answer is not a budget check's"),
        ("allowed not a truth", {"budget": dict(room, kinds={"subscription": dict(kind, allowed="no")})},
         "Proofpane's answer is not a budget check's"),
        ("room not a number", {"budget": dict(room, kinds={"subscription": dict(kind, room_usd="5")})},
         "Proofpane's answer is not a budget check's"),
    ]
    for label, modes, why in cases:
        quiet()
        for name, value in modes.items():
            setattr(Proofpane, name, value)
        before = len(Proofpane.asked)
        r = w.start_session("claude")
        assert r.status_code == 200, (label, r.text)
        view = r.json()
        assert view["running"] is True and view.get("budget_notice") == NOT_MADE.format(why), (label, view)
        # one request, the check's: no redirect followed, nothing asked again
        assert [a["path"] for a in Proofpane.asked[before:]] == [CHECK], (label, Proofpane.asked[before:])
        w.wait_started("claude", view["id"])
        assert w.http.post(f"/sessions/{view['id']}/stop").status_code == 200


def test_a_product_slower_than_the_wait_lets_the_start_go_on_once_the_wait_is_over_and_says_so(w, proofpane,
                                                                                             monkeypatch):
    pair(w.home, proofpane)
    monkeypatch.setattr(Proofpane, "SLOW", WAIT + 10)
    Proofpane.slow = (CHECK,)
    began = time.monotonic()
    r = w.start_session("claude")
    took = time.monotonic() - began
    assert r.status_code == 200, r.text
    assert r.json().get("budget_notice") == NOT_MADE.format(f"no answer came in {WAIT} seconds"), r.json()
    # it waited for the check, and went on before the stand-in's answer came: what the start does past the wait is the
    # machine's, and took it to 7.05 seconds in all once on a loaded Mac (2026-11-12), past the 2 seconds over the wait
    # this case once allowed it
    assert WAIT - 0.5 < took < Proofpane.SLOW, took


def test_a_check_that_could_not_read_every_budget_still_refuses_where_its_kind_says_so(w, proofpane):
    pair(w.home, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",), checked=False, unread=("organisation_cap",))
    r = w.start_session("claude")
    assert (r.status_code, r.json().get("detail")) == (400, REFUSED), r.text
    Proofpane.budget = budget_answer(checked=False, unread=("scopes",))
    r = w.start_session("claude")
    assert r.status_code == 200, r.text
    assert r.json().get("budget_notice") == UNREAD_SCOPES, r.json()


def test_a_claude_terminal_on_another_provider_s_model_is_checked_as_its_own_key_s_spend(w, proofpane):
    pair(w.home, proofpane)
    assert w.http.put("/model-providers/kimi", json={"key": "sk-kimi-" + "k" * 24}).status_code == 200
    Proofpane.budget = budget_answer(refused=("own_key",))
    r = w.start_session("claude", model_provider="kimi")
    assert (r.status_code, r.json().get("detail")) == (400, REFUSED), r.text
    assert w.start_session("claude").status_code == 200            # a sign-in's start: a subscription's, with room
    Proofpane.budget = budget_answer(refused=("subscription",))
    r = w.start_session("claude", model_provider="kimi")
    assert r.status_code == 200, r.text                             # its own key's spend has room


# ---- an agent's run --------------------------------------------------------------------------------------------------

def test_a_spent_scope_fails_an_agent_s_run_in_the_product_s_words_before_anything_of_it_is_made(runs, proofpane):
    pair(runs.home, proofpane)
    runs.put_agent(precheck='touch "$HOME/precheck-ran"')
    Proofpane.budget = budget_answer(refused=("subscription",))
    ended = runs.until_ended(runs.queue({"trigger": {"kind": "manual"}}))
    assert (ended["state"], ended["reason"], ended["cause"]) == ("failed", REFUSED, "start"), ended
    assert not (runs.home / "precheck-ran").exists()               # its precheck never ran
    assert len(worktrees(runs.root)) == 1                          # nor was a worktree made for it
    assert not (runs.home / "stand-in-runs.jsonl").exists()        # nor a claude started
    assert checks() == [{"device_id": DEVICE, "token": TOKEN}]


def test_control_with_room_an_agent_s_run_goes_on_past_the_check(runs, proofpane):
    pair(runs.home, proofpane)
    runs.put_agent(precheck='touch "$HOME/precheck-ran"')
    ended = runs.until_ended(runs.queue({"trigger": {"kind": "manual"}}))
    assert ended["reason"] != REFUSED, ended
    assert (runs.home / "precheck-ran").exists()                   # it went past the check to its precheck


# ---- a task's attempt ------------------------------------------------------------------------------------------------

def attempt_body(**over):
    return {"trigger": {"kind": "task", "prompt": None, "id": TASK}, "task": message(**over)}


def task_work(space):
    return os.path.join(space.root, "agents", AGENT, "tasks", TASK, "work")


def test_a_spent_scope_fails_a_task_s_attempt_in_the_product_s_words_before_its_worktree_is_made(runs, proofpane):
    pair(runs.home, proofpane)
    runs.put_agent()
    Proofpane.budget = budget_answer(refused=("subscription",))
    ended = runs.until_ended(runs.queue(attempt_body()))
    assert (ended["state"], ended["reason"], ended["cause"]) == ("failed", REFUSED, "start"), ended
    assert not os.path.exists(task_work(runs)) and len(worktrees(runs.root)) == 1
    assert not (runs.home / "stand-in-runs.jsonl").exists()


def test_a_task_s_attempt_on_its_own_key_is_checked_as_its_own_key_s_spend(runs, proofpane):
    pair(runs.home, proofpane)
    runs.put_agent()
    assert runs.http.put("/model-providers/anthropic", json={"key": "sk-ant-test-" + "a" * 24}).status_code == 200
    key = {"backend": {"kind": "key", "provider": "anthropic"}, "identity": None, "cap_usd": 5}
    Proofpane.budget = budget_answer(refused=("own_key",))
    ended = runs.until_ended(runs.queue(attempt_body(**key)))
    assert (ended["state"], ended["reason"], ended["cause"]) == ("failed", REFUSED, "start"), ended
    assert not os.path.exists(task_work(runs))
    Proofpane.budget = budget_answer(refused=("subscription",))
    ended = runs.until_ended(runs.queue(attempt_body(fence=2, attempt=2, **key)))
    assert ended["reason"] != REFUSED, ended
    assert os.path.exists(task_work(runs))                         # its key's spend has room: its worktree made


def test_control_with_room_a_task_s_attempt_goes_on_past_the_check_to_its_worktree(runs, proofpane):
    pair(runs.home, proofpane)
    runs.put_agent()
    ended = runs.until_ended(runs.queue(attempt_body()))
    assert ended["reason"] != REFUSED, ended
    assert os.path.exists(task_work(runs))


# ---- in this process: a chat, an agent's run's Agent SDK budget, and the owner's controller --------------------------
# The controller runs only where the Agent SDK is (controller.cannot_run), and a chat's claude starts through it: here
# the stand-in below takes its place, so these run on a machine without it, and never start a claude on one with it.

class _Client:
    """The Agent SDK's client in this process, in place of the real one: what each chat's claude would be started with,
    whether it was connected and what it was sent; nothing comes back until it is disconnected, and no claude is
    started."""

    made = []

    def __init__(self, options):
        self.options, self.connected, self.queries = options, False, []
        self.closed = asyncio.Event()
        _Client.made.append(self)

    async def connect(self):
        self.connected = True

    async def query(self, text):
        self.queries.append(text)

    async def receive_messages(self):
        await self.closed.wait()
        if False:
            yield None

    async def get_mcp_status(self):
        return {"mcpServers": []}

    async def toggle_mcp_server(self, name, on):
        return None

    async def interrupt(self):
        return None

    async def disconnect(self):
        self.closed.set()


class _Options:
    def __init__(self, **given):
        self.__dict__.update(given)


@pytest.fixture
def sdk(monkeypatch):
    """The Agent SDK as this process imports it (chat.py, agents.py): its options, the client above, its message types
    (which nothing here sends), and its in-process MCP server and tool, made as inert values. Its clients, in order."""
    module = types.ModuleType("claude_agent_sdk")
    module.ClaudeAgentOptions, module.ClaudeSDKClient = _Options, _Client
    for name in ("AssistantMessage", "RateLimitEvent", "ResultMessage", "StreamEvent", "SystemMessage", "UserMessage",
                 "TextBlock", "ToolResultBlock", "ToolUseBlock", "PermissionResultAllow", "PermissionResultDeny"):
        setattr(module, name, type(name, (), {}))
    module.create_sdk_mcp_server = lambda name, version="1.0.0", tools=None: {"type": "sdk", "name": name,
                                                                                "instance": None}
    module.tool = lambda name, description, schema, *a, **k: (lambda fn: fn)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    _Client.made = []
    yield _Client.made
    _Client.made = []


def chat_said(space, text):
    """A chat made in the workspace `space` and sent its first message, which starts its claude: (the words of the
    ChatError it raised, or None; the answer of the send, or None), its claude stopped after."""
    from proofpane_workspace import chat as chats
    record, _ = space.prepare("demo", "chat", text)

    async def main():
        space.launch(record["id"])
        try:
            return None, await space.chat_send(record["id"], text)
        except chats.ChatError as e:
            return str(e), None
        finally:
            c = space.chats.get(record["id"])
            if c is not None and c.client is not None:
                await c.stop()
    try:
        return record["id"], asyncio.run(main())
    finally:
        space.usage_report.stop()


def test_a_spent_scope_refuses_a_chat_s_claude_in_the_product_s_words_and_never_connects_it(ws, tmp_path, proofpane,
                                                                                            sdk):
    pair(tmp_path / "wshome", proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    sid, (said, _) = chat_said(ws, "Say hello")
    assert said == REFUSED
    assert [(c.connected, c.queries) for c in sdk] == [(False, [])]        # no claude connected, nothing sent to it
    events = ws.chat_log(sid)["events"]
    assert [e["text"] for e in events if e["kind"] == "error"] == [REFUSED]
    assert checks() == [{"device_id": DEVICE, "token": TOKEN}]


def test_a_chat_whose_check_could_not_be_made_starts_and_says_so_in_its_log_and_its_answer(ws, tmp_path, proofpane,
                                                                                         sdk):
    pair(tmp_path / "wshome", proofpane)
    Proofpane.budget = "down"
    sid, (said, view) = chat_said(ws, "Say hello")
    note = NOT_MADE.format("Proofpane answered 503")
    assert said is None and [(c.connected, c.queries) for c in sdk] == [(True, ["Say hello"])]
    assert view.get("budget_notice") == note, view
    assert [e["text"] for e in ws.chat_log(sid)["events"] if e["kind"] == "notice"] == [note]


def test_a_chat_s_check_answered_after_a_stall_past_the_module_s_wait_is_said_as_that_answer(ws, tmp_path, proofpane,
                                                                                            sdk, monkeypatch):
    """The case above with a stall made certain: the stand-in answers 503 only after the module's wait and half a second
    more. The staging repository's CI run 43094086232 (shard 11, 2026-11-11) said "no answer came in 5 seconds" for
    the case above, its stand-in answering 503. The chat still says Proofpane answered 503."""
    pair(tmp_path / "wshome", proofpane)
    monkeypatch.setattr(Proofpane, "SLOW", WAIT + 0.5)
    Proofpane.budget, Proofpane.slow = "down", (CHECK,)
    sid, (said, view) = chat_said(ws, "Say hello")
    note = NOT_MADE.format("Proofpane answered 503")
    assert said is None and view.get("budget_notice") == note, view
    assert [e["text"] for e in ws.chat_log(sid)["events"] if e["kind"] == "notice"] == [note]


def test_control_a_chat_with_room_or_unpaired_starts_and_says_nothing_of_the_check(ws, tmp_path, proofpane, sdk):
    for paired in (False, True):
        if paired:
            pair(tmp_path / "wshome", proofpane)
        sid, (said, view) = chat_said(ws, f"Say hello {paired}")
        assert said is None and sdk[-1].connected and "budget_notice" not in view, view
        assert not [e for e in ws.chat_log(sid)["events"] if e["kind"] == "notice"]
        if not paired:
            assert Proofpane.asked == []


@pytest.mark.parametrize("room, own, given", [(0.5, 2.0, 0.5), (None, 2.0, 2.0), (5.0, 2.0, 2.0), (0.0, 2.0, 0.01),
                                              (0.75, None, 0.75)])
def test_an_agent_s_run_s_sdk_budget_is_the_smaller_of_its_own_and_the_room_its_check_left(ws, tmp_path, proofpane,
                                                                                         sdk, room, own, given):
    pair(tmp_path / "wshome", proofpane)
    Proofpane.budget = budget_answer(room={"subscription": room})
    ws.agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "max_budget_usd": own})
    ws.skill_improve.proposals.take_word(0)
    run_id = ws.agents.queue(AGENT)["run_id"]

    async def main():
        await ws.agents.startup()
        deadline = time.monotonic() + 30
        while not any(c.connected for c in sdk):
            assert time.monotonic() < deadline, ws.agents.read(run_id)["run"]
            await asyncio.sleep(0.05)
        return sdk[-1].options          # its claude left connected: the run's end is not this test's
    try:
        options = asyncio.run(main())
    finally:
        ws.usage_report.stop()
    got = getattr(options, "max_budget_usd", None)
    assert got is not None and got == pytest.approx(given), got


def test_a_spent_scope_ends_a_manager_s_attempt_in_the_product_s_words_before_its_chat_is_made(ws, tmp_path, proofpane,
                                                                                             monkeypatch, sdk):
    pair(tmp_path / "wshome", proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    _, requirement = managed(ws)
    attempt = taken(ws, requirement)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    asyncio.run(ws.controller.keep_attempts())
    assert (attempt["state"], attempt.get("outcome"), attempt.get("cause"), attempt.get("note")) == \
        ("ended", "failed", "start", REFUSED), attempt
    assert sent == [] and attempt.get("session") is None and ws.controller.record["attempt_sessions"] == []
    assert checks() == [{"device_id": DEVICE, "token": TOKEN}]


def test_control_with_room_a_manager_s_attempt_starts(ws, tmp_path, proofpane, monkeypatch, sdk):
    pair(tmp_path / "wshome", proofpane)
    _, requirement = managed(ws)
    attempt = taken(ws, requirement)
    sent, _ = _sends(ws, monkeypatch), _closes(ws, monkeypatch)
    asyncio.run(ws.controller.keep_attempts())
    assert attempt["state"] == "running" and [s[0] for s in sent] == [attempt["session"]], attempt


def test_a_spent_scope_refuses_the_controller_s_start_of_a_task_in_the_product_s_words_and_starts_no_terminal(
        ws, tmp_path, proofpane, sdk):
    pair(tmp_path / "wshome", proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    m = on(ws)
    requirement = plan(ws, m)
    from proofpane_workspace import controller as ctl
    try:
        said = asyncio.run(ws.controller.start_task({"requirement": requirement, "repo": "demo",
                                                     "task": "Edit README.md so its first line is hello."}))
    except ctl.ControllerError as e:
        said = str(e)
    assert said == REFUSED
    assert _Term.seen == [] and ws.controller.record["tasks"] == {}
