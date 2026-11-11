"""Proofpane's budget check before each prompt:
the plugin's hook, started by a UserPromptSubmit entry of its own that Claude Code waits for, asks the product's
check for the session's kind of spend, holds the prompt in the product's own words where that kind has no room, and
lets it go on otherwise; a check that cannot be made lets the prompt go on and says so; unpaired, nothing is asked; the
prompt's row is still posted by the other entry, in the background. Proofpane is the stand-in that answers as
the product's budget check does (tests/proofpane_stand_in.py), and a local one here whose answer comes a
byte at a time (_Trickle); no real Proofpane, no network beyond this machine, no real claude.

The hook is run as the check's entry starts it, with its argument (CHECK_ARG): in its own process (_start), or in this
one (_check) where a test changes something of it. A hook with no check of its own ignores the argument, as the script
does when it is started with one, and is run here as it would be then, so that each test of the check fails on its own
assertion against such a hook."""

import importlib.util
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT
from proofpane_stand_in import DEVICE, TOKEN, Proofpane, budget_answer, posted, proofpane  # noqa: F401  a fixture
from proofpane_workspace import providers, settings as workspace_settings

PLUGIN = os.path.join(ROOT, "core_plugin")
HOOK = os.path.join(PLUGIN, "hooks", "proofpane_hook.py")
HOOKS_JSON = os.path.join(PLUGIN, "hooks", "hooks.json")
CHECK_ARG = "budget-check"                  # the argument the check's entry starts the hook with
CHECK_PATH = "/api/v1/devices/budget-check"
ROW = "/api/v1/mcp/event"
WAIT = 5.0                                  # the check's wait, at most, by default
PROMPT = {"session_id": "cc-1", "transcript_path": "/w/t.jsonl", "cwd": "/w", "permission_mode": "default",
          "hook_event_name": "UserPromptSubmit", "prompt": "hello"}
GOES_ON = "Proofpane's budget check could not be made ({}), so this prompt goes on unchecked."
# the names that say what a session spends, cleared from this process's own environment for each run in it: the run's
# own may have some (one was set in the session these tests were written in)
SPEND = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
         "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load(HOOK, "proofpane_hook_prompt_check")


def _record(home, cloud):
    folder = os.path.join(home, ".proofpane", "proofpane-core")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "config.json"), "w") as fh:
        json.dump({"cloud_url": cloud, "device_id": DEVICE, "token": TOKEN, "name": "ws", "paired_at": None,
                   "holds": []}, fh)


def _words(kind):
    """The product's words for a spent budget of `kind`, as the stand-in gives them (budget_check._words)."""
    return budget_answer(refused=(kind,))["kinds"][kind]["message"]


def _check(home, payload=None, env=None, raw=None):
    """The hook's main as the check's entry starts it, in this process: what it printed, as JSON, or None."""
    out = io.StringIO()
    old = dict(os.environ)
    try:
        for name in list(os.environ):
            if name in SPEND or name.startswith("ANTHROPIC_") or \
                    name in ("CLAUDE_CODE_ENTRYPOINT", "PP_SESSION_ID", "CLAUDE_CONFIG_DIR"):
                os.environ.pop(name, None)
        os.environ["HOME"] = str(home)
        os.environ.update(env or {})
        data = raw if raw is not None else json.dumps(payload or PROMPT).encode()
        given = {"mode": CHECK_ARG} if "mode" in inspect.signature(hook.main).parameters else {}
        assert hook.main(stdin=io.BytesIO(data), stdout=out, home=str(home), **given) == 0
    finally:
        os.environ.clear()
        os.environ.update(old)
    return json.loads(out.getvalue()) if out.getvalue() else None


def _start(home, payload=None, env=None, args=None):
    """The hook in its own process, as Claude Code starts the check's entry (its interpreter and arguments, `args`
    where given): (what it printed, as JSON or None, the seconds it took)."""
    python = shutil.which("python3") or sys.executable
    argv = [python, *(args or [*workspace_settings.ISOLATED, HOOK, CHECK_ARG])]
    began = time.monotonic()
    p = subprocess.run(argv, input=json.dumps(payload or PROMPT).encode(), capture_output=True, timeout=60,
                       env={"PATH": os.environ["PATH"], "HOME": str(home), **(env or {})})
    took = time.monotonic() - began
    assert p.returncode == 0, p.stderr
    return (json.loads(p.stdout) if p.stdout else None), took


def _entries():
    with open(HOOKS_JSON) as fh:
        return json.load(fh)["hooks"]["UserPromptSubmit"]


def _given(args):
    return [a.replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN) for a in args]


# what hooks.json gives

def test_hooks_json_waits_for_the_check_in_an_entry_of_its_own_and_gives_it_more_than_its_wait(tmp_path, proofpane):
    entries = _entries()
    waited = [h for e in entries for h in e["hooks"] if not h.get("async")]
    assert len(waited) == 1, entries                     # one hook a prompt waits for: the check
    (check,) = waited
    (entry,) = [e for e in entries if check in e["hooks"]]
    assert entry == {"hooks": [check]}                   # alone in its entry, which no matcher narrows
    assert (check["type"], check["command"]) == ("command", "python3")
    assert check["args"] == [*workspace_settings.ISOLATED, "${CLAUDE_PLUGIN_ROOT}/hooks/proofpane_hook.py", CHECK_ARG]
    # the check's wait, and the time Claude Code gives the hook above it, so that Claude Code never stops the hook
    # first, which would let the prompt go on with nothing said to the person
    assert getattr(hook, "CHECK_WAIT", None) == WAIT
    assert check["timeout"] >= WAIT + 5
    # started as the entry gives it: a spent scope holds the prompt, and the check posts no row of its own
    _record(tmp_path, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    out, _ = _start(tmp_path, args=_given(check["args"]))
    assert out == {"decision": "block", "reason": _words("subscription")}
    assert posted(ROW) == []


def test_the_prompt_s_row_is_still_posted_in_the_background_by_an_entry_of_its_own(tmp_path, proofpane):
    # a control: the prompt's row is the entry Claude Code does not wait for, which asks no check whatever is spent
    rows = [h for e in _entries() for h in e["hooks"] if h.get("async") is True]
    assert len(rows) == 1
    (row,) = rows
    assert row["args"] == [*workspace_settings.ISOLATED, "${CLAUDE_PLUGIN_ROOT}/hooks/proofpane_hook.py"]
    _record(tmp_path, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    out, _ = _start(tmp_path, args=_given(row["args"]))
    assert out is None
    assert [b["event_type"] for b in posted(ROW)] == ["claude_code.hook.UserPromptSubmit"]
    assert posted(CHECK_PATH) == []


# what the check answers

def test_a_spent_scope_holds_the_prompt_in_the_product_s_own_words(tmp_path, proofpane):
    _record(tmp_path, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    assert _check(tmp_path) == {"decision": "block", "reason": _words("subscription")}
    assert len(posted(CHECK_PATH)) == 1 and posted(ROW) == []       # the row is the other entry's


@pytest.mark.parametrize("answer", [None, budget_answer(room=3.25), budget_answer(refused=("own_key", "gateway"))],
                         ids=["no limit counted", "room left", "another kind spent"])
def test_with_room_the_prompt_goes_on_unchanged(tmp_path, proofpane, answer):
    # a control: nothing answered, so Claude Code takes the prompt as it was
    _record(tmp_path, proofpane)
    Proofpane.budget = answer
    assert _check(tmp_path) is None


def test_unpaired_the_check_asks_nothing_and_the_prompt_goes_on(tmp_path, proofpane):
    # a control: with no pairing there is nowhere to ask and nothing to say, in this process or in its own
    assert _check(tmp_path) is None
    assert _start(tmp_path)[0] is None
    assert Proofpane.asked == []


def test_the_check_sends_the_device_s_id_and_token_and_nothing_else(tmp_path, proofpane):
    # the product's route takes exactly these two (budget_check.py's CheckBody, extra="forbid"), in the body only
    _record(tmp_path, proofpane)
    assert _check(tmp_path) is None
    asked = [a for a in Proofpane.asked if a["path"] == CHECK_PATH]
    assert [a["body"] for a in asked] == [{"device_id": DEVICE, "token": TOKEN}]
    assert all(a["method"] == "POST" for a in asked)


def test_a_check_that_could_not_read_every_budget_still_holds_what_it_read_as_spent(tmp_path, proofpane):
    _record(tmp_path, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",), checked=False, unread=("organisation_cap",))
    assert _check(tmp_path) == {"decision": "block", "reason": _words("subscription")}


@pytest.mark.parametrize("unread, what", [
    (("scopes",), "the person's and the department's budgets"),
    (("organisation_cap",), "the organisation's monthly cap"),
    (("scopes", "organisation_cap"), "the person's and the department's budgets or the organisation's monthly cap"),
    (("ledger",), "ledger"),                            # a name the hook has no words for, given as it came
    ((), "all of its budgets"),
])
def test_a_check_that_could_not_read_every_budget_lets_the_prompt_go_on_and_says_what(tmp_path, proofpane, unread,
                                                                                       what):
    _record(tmp_path, proofpane)
    Proofpane.budget = budget_answer(checked=False, unread=unread)
    assert _check(tmp_path) == {"systemMessage": f"Proofpane's budget check could not read {what}, so this prompt "
                                                 "goes on, checked against the rest only."}


@pytest.mark.parametrize("message", [None, "", "   ", 7])
def test_a_refusal_with_no_words_of_the_product_s_is_held_in_the_hook_s_own(tmp_path, proofpane, message):
    _record(tmp_path, proofpane)
    answer = budget_answer(refused=("subscription",))
    answer["kinds"]["subscription"]["message"] = message
    Proofpane.budget = answer
    assert _check(tmp_path) == {"decision": "block", "reason": "Proofpane's budget check refused this session's kind "
                                                                 "of spend (subscription), and gave no words for it."}


# a check that cannot be made: the prompt goes on, and the person is told

@pytest.mark.parametrize("how, why", [
    ("down", "Proofpane answered 503"),
    ("nothing listening", "Proofpane could not be reached"),
    ("no HTTP", "Proofpane could not be reached"),
    ("revoked", "Proofpane refused this workspace's device: revoked, paused or given a new token there"),
])
def test_a_product_that_does_not_answer_lets_the_prompt_go_on_and_says_so(tmp_path, proofpane, how, why):
    _record(tmp_path, "http://127.0.0.1:9" if how == "nothing listening" else proofpane)
    Proofpane.budget = "down" if how == "down" else budget_answer(refused=("subscription",))
    Proofpane.garbled = (CHECK_PATH,) if how == "no HTTP" else ()
    Proofpane.revoked = how == "revoked"
    assert _check(tmp_path) == {"systemMessage": GOES_ON.format(why)}


_ROOMY = budget_answer()["kinds"]


@pytest.mark.parametrize("answer", [
    {},
    {"checked": True, "kinds": []},
    {"checked": True, "kinds": {"own_key": _ROOMY["own_key"], "gateway": _ROOMY["gateway"]}},     # the kind not named
    {"checked": True, "kinds": {"subscription": "not allowed"}},
    {"checked": True, "kinds": {"subscription": {"allowed": None, "message": "spent"}}},
    {"checked": True, "kinds": {"subscription": {"allowed": "false", "message": "spent"}}},
    {"checked": True, "kinds": {"subscription": {"message": "spent"}}},
    {"checked": "yes", "kinds": {"subscription": {"allowed": False, "message": "spent"}}},
    {"kinds": {"subscription": {"allowed": False, "message": "spent"}}},
    "deep",
], ids=["empty", "kinds a list", "the kind not named", "the kind's answer a word", "allowed null", "allowed a word",
        "allowed missing", "checked a word", "checked missing", "nested too deep"])
def test_an_answer_not_in_the_check_s_shape_lets_the_prompt_go_on_and_says_so(tmp_path, proofpane, answer):
    _record(tmp_path, proofpane)
    if answer == "deep":
        Proofpane.deep = (CHECK_PATH,)          # json raises RecursionError for it, no ValueError
    else:
        Proofpane.budget = answer
    assert _check(tmp_path) == {"systemMessage": GOES_ON.format("Proofpane's answer could not be read")}


def test_a_redirect_is_refused_never_followed_and_the_prompt_goes_on(tmp_path, proofpane):
    elsewhere = ThreadingHTTPServer(("127.0.0.1", 0), Proofpane)
    threading.Thread(target=elsewhere.serve_forever, daemon=True).start()
    try:
        Proofpane.redirect = f"http://127.0.0.1:{elsewhere.server_address[1]}/caught"
        _record(tmp_path, proofpane)
        out = _check(tmp_path)
    finally:
        elsewhere.shutdown()
    assert out == {"systemMessage": GOES_ON.format("Proofpane answered with a redirect, which is not followed")}
    assert not [a for a in Proofpane.asked if a["path"] == "/caught"]


def test_a_check_slower_than_its_wait_lets_the_prompt_go_on_within_the_wait(tmp_path, proofpane, monkeypatch):
    _record(tmp_path, proofpane)
    monkeypatch.setattr(hook, "CHECK_WAIT", 0.5, raising=False)
    monkeypatch.setattr(Proofpane, "SLOW", 3)
    Proofpane.slow = (CHECK_PATH,)
    Proofpane.budget = budget_answer(refused=("subscription",))         # an answer that would hold, come too late
    began = time.monotonic()
    out = _check(tmp_path)
    took = time.monotonic() - began
    assert out == {"systemMessage": GOES_ON.format("no answer within 0.5 seconds")}
    assert 0.45 <= took < 0.5 + 1.0, took


class _Trickle(BaseHTTPRequestHandler):
    """A Proofpane whose answer to the check comes a byte every 0.2 seconds, each well inside any socket timeout the
    hook gives and the whole of it past the hook's wait: a wait that rested on the socket's own timeout would read it
    all. Any other path is answered 404 at once."""

    asked = []
    BODY = b'{"checked": true}'

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        _Trickle.asked.append((self.path, json.loads(self.rfile.read(n)) if n else None))
        if self.path != CHECK_PATH:
            raw = b'{"detail": "Not Found"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_Trickle.BODY)))
        self.end_headers()
        self.wfile.flush()
        try:
            for i in range(len(_Trickle.BODY)):
                time.sleep(0.2)
                self.wfile.write(_Trickle.BODY[i:i + 1])
                self.wfile.flush()
        except OSError:
            pass                                # the hook gone: nothing more to send


@pytest.fixture
def trickle():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Trickle.asked = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_an_answer_that_trickles_in_past_the_wait_is_not_waited_for(tmp_path, trickle, monkeypatch):
    _record(tmp_path, trickle)
    monkeypatch.setattr(hook, "CHECK_WAIT", 0.5, raising=False)
    began = time.monotonic()
    out = _check(tmp_path)
    took = time.monotonic() - began
    assert out == {"systemMessage": GOES_ON.format("no answer within 0.5 seconds")}
    assert took < 0.5 + 1.0, took
    assert [path for path, _ in _Trickle.asked] == [CHECK_PATH]


def test_the_hook_as_claude_code_starts_it_keeps_its_default_wait(tmp_path, proofpane, monkeypatch):
    # in its own process, which ends at the wait whatever its request's thread still waits on
    _record(tmp_path, proofpane)
    monkeypatch.setattr(Proofpane, "SLOW", WAIT + 4)
    Proofpane.slow = (CHECK_PATH,)
    Proofpane.budget = budget_answer(refused=("subscription",))
    out, took = _start(tmp_path)
    assert out == {"systemMessage": GOES_ON.format(f"no answer within {WAIT:g} seconds")}
    assert WAIT <= took < WAIT + 2, took


@pytest.mark.parametrize("where, why", [("spend_kind", "the hook broke while checking"),
                                        ("_post", "the hook broke while asking")])
def test_an_exception_inside_the_check_lets_the_prompt_go_on_and_says_so(tmp_path, proofpane, monkeypatch, where, why):
    # where the check breaks, in this thread (the kind of spend) or in the request's (the request), the prompt goes on
    # and the person is told; a spent scope would have held it. The request's own break is said as the request's: one
    # its thread left uncaught would reach this thread's catch only as a missing answer, and the thread's traceback
    # would go to the hook's standard error
    _record(tmp_path, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))

    def broken(*args, **kwargs):
        raise RuntimeError("a check that breaks")

    monkeypatch.setattr(hook, where, broken, raising=False)
    out = _check(tmp_path)
    assert isinstance(out, dict) and set(out) == {"systemMessage"}, out
    assert out == {"systemMessage": GOES_ON.format(why)}


# the session's kind of spend, and what the check is asked for

_ENDPOINTS = sorted(c["env"]["ANTHROPIC_BASE_URL"] for c in providers.CATALOG.values())


@pytest.mark.parametrize("env, kind", [
    ({}, "subscription"),
    ({"ANTHROPIC_API_KEY": "sk-ant-api03-" + "k" * 20}, "own_key"),
    ({"ANTHROPIC_AUTH_TOKEN": "a" * 24}, "own_key"),
    *[({"ANTHROPIC_BASE_URL": url}, "own_key") for url in _ENDPOINTS],     # each provider's endpoint (providers.py)
    ({"ANTHROPIC_BASE_URL": "https://gateway.example.org/anthropic"}, "own_key"),
    ({"ANTHROPIC_BASE_URL": providers.KEY_ONLY["anthropic"]["env"]["ANTHROPIC_BASE_URL"]}, "subscription"),
    ({"ANTHROPIC_BASE_URL": "https://API.anthropic.com/"}, "subscription"),
    ({"CLAUDE_CODE_USE_BEDROCK": "1"}, "own_key"),
    ({"CLAUDE_CODE_USE_VERTEX": "true"}, "own_key"),
    ({"CLAUDE_CODE_USE_FOUNDRY": "ON"}, "own_key"),
    ({"CLAUDE_CODE_USE_BEDROCK": "0"}, "subscription"),
    ({"ANTHROPIC_API_KEY": " ", "ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_BASE_URL": ""}, "subscription"),
])
def test_the_session_s_kind_of_spend_is_read_from_the_environment_it_runs_in(tmp_path, proofpane, env, kind):
    _record(tmp_path, proofpane)
    other = "own_key" if kind == "subscription" else "subscription"
    Proofpane.budget = budget_answer(refused=(kind,))
    assert _check(tmp_path, env=env) == {"decision": "block", "reason": _words(kind)}
    Proofpane.budget = budget_answer(refused=(other,))
    assert _check(tmp_path, env=env) is None


def test_the_check_answers_a_prompt_and_nothing_else(tmp_path, proofpane):
    # a control: the check's entry is a prompt's, and the hook started so for another event asks nothing
    _record(tmp_path, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    call = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls -la"},
            "session_id": "cc-1", "cwd": "/w", "tool_use_id": "toolu_1"}
    assert _check(tmp_path, payload=call) is None
    assert Proofpane.asked == []


def test_a_prompt_too_large_to_read_or_not_json_is_still_checked(tmp_path, proofpane):
    # the check needs nothing of the prompt, and Claude Code writes the event's name before it
    _record(tmp_path, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    head = b'{"session_id": "cc-1", "cwd": "/w", "hook_event_name": "UserPromptSubmit", "prompt": "'
    held = {"decision": "block", "reason": _words("subscription")}
    assert _check(tmp_path, raw=head + b"x" * (hook.MAX_INPUT + 10) + b'"}') == held
    assert _check(tmp_path, raw=head + b"cut short") == held
