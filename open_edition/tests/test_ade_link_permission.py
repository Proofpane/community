"""The owner's inbox, the linked session's side (open_edition/ade_link.py permission): the hook of a session's own
prompts, run as Claude Code runs it (`permission`, its input on stdin), against a
stand-in of the ADE's link routes that this test scripts (the server's prompt routes are tested against the app
itself, in the end-to-end tests). The text the hook seals is opened as the page opens it (e2e_link_stand_in), and the
owner's answers are signed by the tests' software passkey and checked by the client against the keys it pinned, so
everything the hook decides on is the client's own check. Time is a clock the test moves."""

import copy
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import ade_link
import hook_templates
from e2e_link_stand_in import ACCOUNT, LINK_ID, key_id, open_envelope, owner_key, raw_public, slot
from proofpane_core.canonical import canonicalize
from test_passkeys import Authenticator, as_step

URL = "http://127.0.0.1:9"                     # never reached: every request goes to the stand-in below
HOST = "127.0.0.1:9"
SESSION = "5d0e8a3c-1f2b-4c6d-9e7f-0a1b2c3d4e5f"
PROGRAM = "/absolute/path/to/venv/bin/python"
TRAMPOLINE = "import runpy, sys; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
NEW_EVENTS = ("PermissionRequest", "PostToolUse", "PostToolUseFailure", "Stop", "StopFailure", "UserPromptSubmit",
              "Notification", "PostModelSwitch")


class Clock:
    def __init__(self):
        self.t = float(int(time.time()))

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(seconds, 0.001)


class ADE:
    """The ADE's link routes as the hook uses them: posts kept by seq (a repeated client_id answered with its first
    seq), a prompt's view (open until a report, an owner's close or the script says otherwise), and its claim, which
    the test scripts with the digest of the text the hook posted."""

    def __init__(self):
        self.requests = []
        self.events = {}
        self.closed = {}
        self.next = 41
        self.view = None            # (seq, how many reads of it so far) -> a view, or None for the default
        self.claim = None           # (seq, digest of the posted text) -> an answer, or raises
        self.on_read = None         # called at each read of a prompt, before it is answered

    def request(self, method, path, body=None, query=None, key=None, timeout=None, raw=None):
        self.requests.append((method, path, copy.deepcopy(body)))
        if method == "POST" and path == "/events":
            for seq, b in self.events.items():
                if b["client_id"] == body["client_id"]:
                    return {"seq": seq}
            seq, self.next = self.next, self.next + 1
            self.events[seq] = body
            if body.get("kind") == "note" and str(body.get("client_id", "")).startswith("report:"):
                self.closed.setdefault(body["ref"], "report:" + body["text"].split(": ", 1)[1])
            return {"seq": seq}
        if method == "GET" and path.startswith("/events/"):
            seq = int(path.rsplit("/", 1)[1])
            reads = len([r for r in self.requests if r[:2] == ("GET", path)])
            if self.on_read is not None:
                self.on_read(seq, reads)
            given = self.view(seq, reads) if self.view is not None else None
            if given is not None:
                return given
            closed = self.closed.get(seq)
            return {"seq": seq, "kind": "prompt", "closed_reason": closed, "open": closed is None}
        if method == "POST" and path.startswith("/prompts/") and path.endswith("/claim"):
            seq = int(path.split("/")[2])
            digest = "sha256:" + hashlib.sha256(self.events[seq]["text"].encode("utf-8")).hexdigest()
            return self.claim(seq, digest)
        raise AssertionError(f"the hook asked for {method} {path}")

    def posts(self, kind=None):
        return [b for m, p, b in self.requests if (m, p) == ("POST", "/events") and (kind is None or b["kind"] == kind)]

    def asked(self, method, start):
        return [p for m, p, _ in self.requests if m == method and p.startswith(start)]


class Rig:
    pass


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A link's directory as pair and pin leave it, the owner's passkey pinned; the account's content key, which the
    link seals to (its mode, which a test may change); the ADE's stand-in; the clock; and the session's working
    folder."""
    for name in ("CLAUDE_PROJECT_DIR", "ADE_LINK_DIR", "CLAUDE_CODE_MESSAGING_SOCKET", "ADE_OWNER_KEYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    r = Rig()
    r.dir = tmp_path / "home" / ".proofpane-ade" / "mac"
    r.dir.mkdir(parents=True)
    (r.dir / "config.json").write_text(json.dumps({"url": URL, "name": "mac", "link_id": LINK_ID,
                                                   "account": ACCOUNT}))
    ade_link.Link(str(r.dir)).key(make=True)
    r.device = Authenticator(alg=-7, rp="127.0.0.1", origin=URL)
    r.other = Authenticator(alg=-8, rp="127.0.0.1", origin=URL)            # a passkey the link has not pinned
    (r.dir / "owner_keys.json").write_text(json.dumps({"account": ACCOUNT, "keys": [owner_key(r.device)],
                                                      "source": "pair", "pinned_at": int(time.time())}))
    r.private = X25519PrivateKey.generate()
    public = raw_public(r.private)
    r.mode = ("seal", public, key_id(public), ACCOUNT)
    monkeypatch.setattr(ade_link, "_e2e_mode", lambda link, *a, **k: r.mode)
    monkeypatch.setattr(ade_link, "_sensitive_poster", lambda link: (False, None))
    r.ade = ADE()
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: r.ade.request(*a, **k))
    r.clock = Clock()
    monkeypatch.setattr(ade_link, "wall", r.clock, raising=False)       # the prompt's clock (absent before it)
    monkeypatch.setattr(ade_link, "sleep", r.clock.sleep)
    r.work = tmp_path / "work"
    r.work.mkdir()
    return r


def hook(rig, monkeypatch, capsys, given):
    """The hook run as Claude Code runs it: `permission`, its input on stdin. (exit code, stdout, stderr); an exit
    argparse takes is its code."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(given)))
    try:
        code = ade_link.main(["--dir", str(rig.dir), "permission"])
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


def asking(rig, tool="Bash", tool_input=None, **over):
    given = {"hook_event_name": "PermissionRequest", "session_id": SESSION, "transcript_path": "/x/t.jsonl",
             "cwd": str(rig.work), "permission_mode": "default", "tool_name": tool,
             "tool_input": {"command": "npm test", "description": "Run the tests"} if tool_input is None
             else tool_input,
             "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": tool}], "behavior": "allow",
                                         "destination": "localSettings"}]}
    given.update(over)
    return given


def opened(rig, body):
    """The text of a prompt the hook posted sealed, opened as the page opens it, with its slot rebuilt."""
    value = open_envelope(body["text"], slot(ACCOUNT, LINK_ID, "event", "prompt", None, None, body["client_id"]),
                          rig.private)
    assert value["session"] is None and value["said_at"] is None
    return value["text"]


def signed(rig, seq, digest, text, device=None, **over):
    """The owner's answer to prompt `seq`, signed with a passkey over its canonical payload, as the claim returns it."""
    now = int(rig.clock.t)
    p = {"v": 2, "deployment": HOST, "purpose": "answer", "account": ACCOUNT, "nonce": uuid.uuid4().hex,
         "id": uuid.uuid4().hex, "to": LINK_ID, "text": text, "ref": seq, "digest": digest, "created_at": now,
         "expires_at": now + 3600}
    p.update(over)
    raw = canonicalize(p)
    return {"seq": 900, "payload": raw.decode("utf-8"), "signature": (device or rig.device).get(as_step(raw)),
            "passkey_id": "pk-1", "taken_at": now}


def answered(rig, text, **over):
    """The ADE's script for a prompt the owner answered with `text`: its first read says so, and its claim gives the
    answer signed over the text the hook posted (or as `over` changes it)."""
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "answer", "open": False}
    rig.ade.claim = lambda seq, digest: signed(rig, seq, over.pop("digest", digest), text, **over)


def decision(out):
    """The one line the hook printed, as Claude Code reads it."""
    lines = out.splitlines()
    assert len(lines) == 1, out
    got = json.loads(lines[0])
    assert set(got) == {"hookSpecificOutput"} and got["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
    return got["hookSpecificOutput"]["decision"]


def unanswered(deadline):
    """The hook's refusal of a prompt nobody answered by its deadline (unix seconds): fixed
    words that name the deadline as YYYY-MM-DD HH:MM UTC, and no interrupt, so the turn goes on."""
    when = datetime.datetime.fromtimestamp(deadline, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {"behavior": "deny", "message": f"Not answered in the owner's list by its deadline, {when}: refused, and "
                                           "the turn goes on."}


# ---- a permission prompt, answered in the owner's list ------------------------------------------------------------

def test_a_permission_prompt_goes_sealed_whole_and_an_approve_signed_over_its_text_allows_it(rig, monkeypatch, capsys):
    answered(rig, "Approve")
    started = int(rig.clock.t)
    code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
    assert code == 0 and err == ""
    assert decision(out) == {"behavior": "allow"}
    (posted,) = rig.ade.posts()
    deadline = started + 1800
    assert posted["deadline"] == deadline
    assert set(posted) == {"client_id", "kind", "to", "text", "deadline", "look"}
    assert posted["kind"] == "prompt" and posted["to"] == "owner" and posted["look"] is False
    assert posted["text"].startswith("e2e1.") and len(posted["client_id"]) == 32
    item = json.loads(opened(rig, posted))
    # the whole input, as Claude Code gave it, and nothing of the suggestions to allow the tool from now on
    assert item == {"tool": "Bash", "input": {"command": "npm test", "description": "Run the tests"},
                    "session_id": SESSION, "agent_id": None, "deadline": deadline}
    assert opened(rig, posted) == canonicalize(item).decode("utf-8")              # the client's own canonical form
    assert rig.ade.asked("GET", "/events/") == ["/events/41"] and rig.ade.asked("POST", "/prompts/") == \
        ["/prompts/41/claim"]
    claimed = [b for m, p, b in rig.ade.requests if p == "/prompts/41/claim"][0]
    assert set(claimed) == {"instance"} and len(claimed["instance"]) <= 100
    # kept in the link's directory for the session's report: never the input itself
    kept = json.loads((rig.dir / "prompts.json").read_text())
    assert [(e["seq"], e["session"], e["agent"], e["tool"], e["deadline"]) for e in kept] == \
        [(41, SESSION, None, "Bash", posted["deadline"])]
    assert "npm test" not in (rig.dir / "prompts.json").read_text()


def test_the_deadline_is_thirty_minutes_and_at_it_the_hook_refuses_the_prompt_naming_it(rig, monkeypatch, capsys):
    """This case once held that the hook ended at the deadline with nothing printed,
    which left the prompt to its window for the turn to wait on. Now a prompt nobody answered by its deadline is
    refused with words that name the deadline, and the turn goes on. The stand-in reads the prompt open until the
    hook's clock reaches the deadline, and closed by it from then on, as the ADE's read past a deadline records it
    (test_inbox_close_hook.py holds the rest)."""
    started = int(rig.clock.t)

    def by_the_clock(seq, n):
        due = rig.clock.t >= rig.ade.events[seq]["deadline"]
        return {"seq": seq, "kind": "prompt", "closed_reason": "deadline" if due else None, "open": not due}
    rig.ade.view = by_the_clock
    code, out, err = hook(rig, monkeypatch, capsys, asking(rig))
    (posted,) = rig.ade.posts()
    assert code == 0 and err == "" and decision(out) == unanswered(posted["deadline"])
    assert posted["deadline"] == started + ade_link.PROMPT_WAIT == started + 1800
    reads = rig.ade.asked("GET", "/events/")
    assert 300 <= len(reads) <= 361 and rig.ade.asked("POST", "/prompts/") == []      # read every 5 s, never claimed
    assert rig.clock.t >= posted["deadline"]


@pytest.mark.parametrize("text, wanted", [
    ("Deny", {"behavior": "deny", "message": "Denied in the owner's list", "interrupt": True}),
    ("Not this one: run the unit tests first.", {"behavior": "deny",
                                                 "message": "Not this one: run the unit tests first."}),
])
def test_deny_denies_with_the_fixed_words_and_ends_the_turn_and_his_own_words_deny_with_them(rig, monkeypatch, capsys,
                                                                                             text, wanted):
    answered(rig, text)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert code == 0 and decision(out) == wanted


@pytest.mark.parametrize("text", ["approve", "Approve.", "APPROVED", "approved!", "deny", "Denied.", "1", "1,2",
                                  "1, 2", "2,1"])
def test_words_that_read_as_a_button_and_numbers_on_a_permission_prompt_print_nothing(rig, monkeypatch, capsys, text):
    answered(rig, text)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert (code, out) == (0, "")
    assert rig.ade.asked("POST", "/prompts/") == ["/prompts/41/claim"]                # claimed, checked, not acted on


@pytest.mark.parametrize("change", ["digest", "ref", "to", "unpinned", "expired", "account", "deployment"])
def test_an_answer_for_another_text_prompt_session_or_signer_prints_nothing(rig, monkeypatch, capsys, change):
    over = {"digest": {"digest": "sha256:" + "0" * 64}, "ref": {"ref": 42}, "to": {"to": "b" * 32},
            "unpinned": {"device": rig.other},
            "expired": {"created_at": int(rig.clock.t) - 7200, "expires_at": int(rig.clock.t) - 3600},
            "account": {"account": "acct-other"}, "deployment": {"deployment": "elsewhere.example"}}[change]
    answered(rig, "Approve", **over)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert (code, out) == (0, "")


@pytest.mark.parametrize("closed", ["owner", "report:PostToolUse", "report:Stop", "deadline"])
def test_a_close_by_the_owner_or_the_session_s_report_prints_nothing_the_deadline_s_refuses_and_none_is_claimed(
        rig, monkeypatch, capsys, closed):
    """The deadline's close once printed nothing here, as the owner's close and the
    session's report do, which left the prompt to its window. Now it refuses the prompt with words that name the
    deadline, and the turn goes on; the owner's close and the report still print nothing. None is claimed."""
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": closed if n > 1 else None,
                                   "open": n <= 1}
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    if closed == "deadline":
        (posted,) = rig.ade.posts()
        assert code == 0 and decision(out) == unanswered(posted["deadline"])
    else:
        assert (code, out) == (0, "")
    assert len(rig.ade.asked("GET", "/events/")) == 2 and rig.ade.asked("POST", "/prompts/") == []


@pytest.mark.parametrize("why", ["expired", "taken", "deadline", "stopped", "closed"])
def test_a_claim_the_ade_refuses_prints_nothing(rig, monkeypatch, capsys, why):
    answered(rig, "Approve")

    def refused(seq, digest):
        raise ade_link.Refused(409, "not claimed", {"why": why})
    rig.ade.claim = refused
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert (code, out) == (0, "")


# ---- a question, answered by its options' numbers ----------------------------------------------------------------

QUESTIONS = {"questions": [
    {"question": "Which database?", "header": "Database", "multiSelect": False,
     "options": [{"label": "Postgres", "description": "the one in production"}, {"label": "SQLite"}]},
    {"question": "Which checks?", "header": "Checks", "multiSelect": True,
     "options": [{"label": "unit"}, {"label": "lint", "description": "ruff"}, {"label": "types"}]}],
    "metadata": {"source": "the plan"}}


def test_a_question_s_options_are_numbered_across_questions_and_numbers_answer_it_with_the_input_as_posted(
        rig, monkeypatch, capsys):
    given = copy.deepcopy(QUESTIONS)
    answered(rig, "2,3,5")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig, "AskUserQuestion", given))
    assert code == 0
    got = decision(out)
    # the question as posted, every field of it as it was, and the labels the numbers name as its answers
    assert got == {"behavior": "allow", "updatedInput": dict(QUESTIONS, answers={"Which database?": "SQLite",
                                                                                 "Which checks?": "unit, types"})}
    item = json.loads(opened(rig, rig.ade.posts()[0]))
    assert item["input"] == QUESTIONS
    assert item["questions"] == [
        {"question": "Which database?", "header": "Database", "multiSelect": False,
         "options": [{"number": 1, "label": "Postgres", "description": "the one in production"},
                     {"number": 2, "label": "SQLite"}]},
        {"question": "Which checks?", "header": "Checks", "multiSelect": True,
         "options": [{"number": 3, "label": "unit"}, {"number": 4, "label": "lint", "description": "ruff"},
                     {"number": 5, "label": "types"}]}]
    kept = json.loads((rig.dir / "prompts.json").read_text())
    assert kept[0]["numbering"] == [[1, 2], [3, 4, 5]]


@pytest.mark.parametrize("text", ["2,6", "1,2,3", "3", "1", "Approve", "2,3,5,9", "02,3"])
def test_a_number_not_posted_two_for_one_answer_a_question_left_or_approve_prints_nothing(rig, monkeypatch, capsys,
                                                                                          text):
    answered(rig, text)
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig, "AskUserQuestion", copy.deepcopy(QUESTIONS)))
    assert (code, out) == (0, "")


def test_deny_on_a_question_denies_it_and_ends_the_turn(rig, monkeypatch, capsys):
    answered(rig, "Deny")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig, "AskUserQuestion", copy.deepcopy(QUESTIONS)))
    assert code == 0 and decision(out) == {"behavior": "deny", "message": "Denied in the owner's list",
                                           "interrupt": True}


# ---- what the list can only show -----------------------------------------------------------------------------------

def _look_cases(rig):
    client = str(rig.dir.parent / "ade_link.py")
    return {
        "an agent's prompt": asking(rig, agent_id="a-1f2b", agent_type="general-purpose"),
        "a plan approval": asking(rig, "ExitPlanMode", {"plan": "1. do it"}),
        "a question with a field for text": asking(rig, "AskUserQuestion", {"questions": [
            {"question": "Which name?", "header": "Name", "multiSelect": False, "options": []}]}),
        "a push to main": asking(rig, tool_input={"command": "git push origin main"}),
        "pin --add": asking(rig, tool_input={"command": f"python3 {client} --dir {rig.dir} pin --add "
                                                        + "0" * 32}),
        "an edit to Claude Code's settings": asking(rig, "Write", {"file_path": str(rig.work / ".claude" /
                                                                                "settings.json"),
                                                                   "content": "{}"}),
        "a deploy": asking(rig, tool_input={"command": "fly deploy"}),
    }


@pytest.mark.parametrize("case", ["an agent's prompt", "a plan approval", "a question with a field for text",
                                  "a push to main", "pin --add", "an edit to Claude Code's settings", "a deploy"])
def test_what_the_list_can_only_show_is_posted_at_once_to_look_at_and_the_hook_ends_printing_nothing(
        rig, monkeypatch, capsys, case):
    given = _look_cases(rig)[case]
    answered(rig, "Approve")                    # an answer there would allow: the hook never asks for one
    code, out, _ = hook(rig, monkeypatch, capsys, given)
    assert (code, out) == (0, "")
    (posted,) = rig.ade.posts()
    assert posted["kind"] == "prompt" and posted["look"] is True and posted["text"].startswith("e2e1.")
    item = json.loads(opened(rig, posted))
    assert item["tool"] == given["tool_name"] and item["input"] == given["tool_input"]     # Claude Code's own words
    assert item["agent_id"] == given.get("agent_id")
    assert rig.ade.asked("GET", "/events/") == [] and rig.ade.asked("POST", "/prompts/") == []


@pytest.mark.parametrize("command", ["echo \u200bhi", "printf 'a\x07b'", "cat \u202egnp.exe", "echo é",
                                     "echo " + "x" * 4100, "export KEY=sk-ant-api03-" + "a" * 40])
def test_a_text_past_the_rules_is_never_cut_or_posted_and_goes_as_the_tool_s_name_alone(rig, monkeypatch, capsys,
                                                                                          command):
    answered(rig, "Approve")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig, tool_input={"command": command}))
    assert (code, out) == (0, "")
    (posted,) = rig.ade.posts()
    assert posted["look"] is True
    item = json.loads(opened(rig, posted))
    assert item == {"tool": "Bash", "session_id": SESSION, "agent_id": None, "deadline": posted["deadline"]}
    assert rig.ade.asked("GET", "/events/") == []


def test_on_an_account_that_does_not_seal_only_the_tool_s_name_goes_in_the_clear_and_nothing_where_sealing_is_held(
        rig, monkeypatch, capsys):
    rig.mode = ("clear",)
    answered(rig, "Approve")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig, tool_input={"command": "psql postgres://u:pw@db/x"}))
    assert (code, out) == (0, "")
    (posted,) = rig.ade.posts()
    assert posted["text"] == "Bash" and posted["look"] is True and rig.ade.asked("GET", "/events/") == []
    rig.mode = ("hold", "the ADE says statement 2 names a key, and this link has not taken it")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert (code, out) == (0, "") and len(rig.ade.posts()) == 1                         # nothing more went


def test_with_no_owner_keys_pinned_a_prompt_is_an_item_to_look_at(rig, monkeypatch, capsys):
    (rig.dir / "owner_keys.json").unlink()
    answered(rig, "Approve")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert (code, out) == (0, "")
    (posted,) = rig.ade.posts()
    assert posted["look"] is True and rig.ade.asked("GET", "/events/") == []


# ---- reports: what closes an item, from this session alone --------------------------------------------------------

def report(rig, monkeypatch, capsys, event, **fields):
    given = {"hook_event_name": event, "session_id": SESSION, "transcript_path": "/x/t.jsonl", "cwd": str(rig.work)}
    given.update(fields)
    return hook(rig, monkeypatch, capsys, given)


def test_the_session_s_post_tool_use_closes_its_item_with_a_report_that_carries_nothing_of_the_call(
        rig, monkeypatch, capsys):
    """The window answered first: the call ran, its PostToolUse reports it while the hook waits, and the hook, finding
    its prompt closed by that report, ends with nothing printed."""
    given = asking(rig)

    def the_window_answers(seq, reads):
        if reads == 1:
            done = dict(given, hook_event_name="PostToolUse", tool_response={"stdout": "12 passed"},
                        tool_use_id="toolu_01")
            del done["permission_suggestions"]
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(done)))
            assert ade_link.main(["--dir", str(rig.dir), "permission"]) == 0
    rig.ade.on_read = the_window_answers
    code, out, _ = hook(rig, monkeypatch, capsys, given)
    assert (code, out) == (0, "")
    assert rig.ade.posts("note") == [{"client_id": "report:41", "kind": "note", "to": "owner", "ref": 41,
                                      "text": "reported: PostToolUse"}]
    assert rig.ade.asked("POST", "/prompts/") == [] and json.loads((rig.dir / "prompts.json").read_text()) == []


def test_a_report_from_another_session_or_agent_closes_nothing_and_asks_nothing(rig, monkeypatch, capsys):
    given = asking(rig, agent_id="a-1f2b")
    assert hook(rig, monkeypatch, capsys, given)[:2] == (0, "")                 # an agent's: kept, posted to look at
    before = len(rig.ade.requests)
    for other in ({"session_id": "a1b2c3d4-0000-4000-8000-000000000000", "agent_id": "a-1f2b"},
                  {"agent_id": "a-other"}, {}):
        fields = dict({"tool_name": "Bash", "tool_input": given["tool_input"], "tool_response": {}}, **other)
        if "agent_id" not in other:
            fields.pop("agent_id", None)
        assert report(rig, monkeypatch, capsys, "PostToolUse", **fields)[:2] == (0, "")
    assert report(rig, monkeypatch, capsys, "Stop", stop_hook_active=False)[:2] == (0, "")  # the main thread's turn
    assert len(rig.ade.requests) == before                                                   # no request at all
    assert report(rig, monkeypatch, capsys, "PostToolUse", tool_name="Bash", tool_input={"command": "npm test -x"},
                  agent_id="a-1f2b")[:2] == (0, "")                                          # another input
    assert len(rig.ade.requests) == before
    assert report(rig, monkeypatch, capsys, "PostToolUseFailure", tool_name="Bash", tool_input=given["tool_input"],
                  agent_id="a-1f2b", error="exit 1")[:2] == (0, "")
    assert rig.ade.posts("note") == [{"client_id": "report:41", "kind": "note", "to": "owner", "ref": 41,
                                      "text": "reported: PostToolUseFailure"}]
    assert json.loads((rig.dir / "prompts.json").read_text()) == []


def test_a_question_is_closed_by_its_report_whose_input_carries_the_answers(rig, monkeypatch, capsys):
    given = asking(rig, "AskUserQuestion", copy.deepcopy(QUESTIONS), agent_id="a-1f2b")
    hook(rig, monkeypatch, capsys, given)
    ran = dict(copy.deepcopy(QUESTIONS), answers={"Which database?": "SQLite", "Which checks?": "unit"})
    assert report(rig, monkeypatch, capsys, "PostToolUse", tool_name="AskUserQuestion", tool_input=ran,
                  agent_id="a-1f2b", tool_response={})[:2] == (0, "")
    assert rig.ade.posts("note") == [{"client_id": "report:41", "kind": "note", "to": "owner", "ref": 41,
                                      "text": "reported: PostToolUse"}]


@pytest.mark.parametrize("event", ["Stop", "StopFailure", "UserPromptSubmit"])
def test_the_turn_s_end_or_a_new_prompt_closes_the_main_thread_s_items_only(rig, monkeypatch, capsys, event):
    hook(rig, monkeypatch, capsys, asking(rig, "ExitPlanMode", {"plan": "p"}))           # the main thread's, 41
    hook(rig, monkeypatch, capsys, asking(rig, agent_id="a-1f2b"))                       # an agent's, 42
    extra = {"Stop": {"stop_hook_active": False}, "StopFailure": {"error": "server_error"},
             "UserPromptSubmit": {"prompt": "go on"}}[event]
    assert report(rig, monkeypatch, capsys, event, **extra)[:2] == (0, "")
    assert [n["ref"] for n in rig.ade.posts("note")] == [41]
    assert rig.ade.posts("note")[0] == {"client_id": "report:41", "kind": "note", "to": "owner", "ref": 41,
                                        "text": f"reported: {event}"}
    kept = json.loads((rig.dir / "prompts.json").read_text())
    assert [e["seq"] for e in kept if e["event"] == "PermissionRequest"] == [42]


def test_a_report_that_comes_before_the_seq_is_known_is_posted_by_the_poster(rig, monkeypatch, capsys):
    given = asking(rig, "ExitPlanMode", {"plan": "p"})
    real = rig.ade.request

    def the_turn_ends_first(method, path, body=None, **kw):
        if (method, path) == ("POST", "/events") and body.get("kind") == "prompt":
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop",
                                                                      "session_id": SESSION})))
            assert ade_link.main(["--dir", str(rig.dir), "permission"]) == 0
        return real(method, path, body, **kw)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: the_turn_ends_first(*a, **k))
    assert hook(rig, monkeypatch, capsys, given)[:2] == (0, "")
    assert rig.ade.posts("note") == [{"client_id": "report:41", "kind": "note", "to": "owner", "ref": 41,
                                      "text": "reported: Stop"}]
    assert json.loads((rig.dir / "prompts.json").read_text()) == []


# ---- Claude Code's own words: items to look at ---------------------------------------------------------------------

@pytest.mark.parametrize("event, fields", [
    ("Notification", {"message": "Session paused", "title": "Claude Code", "notification_type": "permission_prompt"}),
    ("StopFailure", {"error": "rate_limit", "last_assistant_message": "API Error: 429"}),
    ("PostModelSwitch", {"from_model": "claude-a", "to_model": "claude-b"}),
])
def test_a_pause_an_api_error_and_a_model_switch_are_items_to_look_at_with_claude_code_s_words(
        rig, monkeypatch, capsys, event, fields):
    code, out, _ = report(rig, monkeypatch, capsys, event, **fields)
    assert (code, out) == (0, "")
    (posted,) = rig.ade.posts("prompt")
    assert posted["look"] is True
    item = json.loads(opened(rig, posted))
    assert item == {"event": event, "words": fields, "session_id": SESSION, "agent_id": None,
                    "deadline": posted["deadline"]}
    rig.mode = ("clear",)
    report(rig, monkeypatch, capsys, event, **fields)
    clear = rig.ade.posts("prompt")[-1]
    assert clear["text"] == {"Notification": "Notification: Claude Code; Session paused",
                             "StopFailure": "StopFailure: rate_limit",
                             "PostModelSwitch": "PostModelSwitch: claude-a; claude-b"}[event]
    # each is the session's own main-thread item: its next turn's report closes it (a second StopFailure reports
    # the first before it posts its own)
    report(rig, monkeypatch, capsys, "UserPromptSubmit", prompt="go on")
    prompts = sorted(seq for seq, b in rig.ade.events.items() if b["kind"] == "prompt")
    assert len(prompts) == 2 and sorted(n["ref"] for n in rig.ade.posts("note")) == prompts
    assert json.loads((rig.dir / "prompts.json").read_text()) == []


# ---- the watcher and take pass over a prompt's answer -------------------------------------------------------------

def test_the_watcher_never_wakes_the_model_for_an_answer_to_a_prompt(rig, monkeypatch, capsys):
    served = [{"seq": 77, "time": "2026-11-02T00:00:00Z", "kind": "answer", "from": "owner", "to": "mac", "ref": 41,
               "text": "Approve", "needs_turn": True, "closed_by": None, "for_prompt": True}]

    def request(self, method, path, body=None, query=None, **k):
        return {} if path == "/heartbeat" else {"events": served if not (query or {}).get("after") else [],
                                                "through": 77, "head": 77}
    monkeypatch.setattr(ade_link.Link, "request", request)
    monkeypatch.setattr(ade_link, "clock", rig.clock)
    try:
        code = ade_link.main(["--dir", str(rig.dir), "watch", "--max", "60"])
    except SystemExit as e:
        code = e.code
    out, _ = capsys.readouterr()
    assert (code, out) == (0, "")
    assert (rig.dir / "announced_seq").read_text() == "77"                  # passed over, not read again each round


def test_take_refuses_an_answer_to_a_prompt_and_never_asks_to_take_it(rig, monkeypatch, capsys):
    """Even one that would verify as an answer to a question this session asked: the ADE's word that it answers a
    prompt is enough for take to leave it to the hook."""
    question = "May I tag the release?"
    (rig.dir / "posted.json").write_text(json.dumps({"41": {"digest": "sha256:" + hashlib.sha256(
        question.encode()).hexdigest(), "text": question}}))
    answer = signed(rig, 41, "sha256:" + hashlib.sha256(question.encode()).hexdigest(), "Approve")
    payload = json.loads(answer["payload"])
    event = {"seq": 77, "time": "2026-11-02T00:00:00Z", "kind": "answer", "from": "owner", "to": "mac", "ref": 41,
             "text": "Approve", "needs_turn": True, "closed_by": None, "client_id": payload["id"],
             "payload": answer["payload"], "signature": answer["signature"], "for_prompt": True}
    asked = []

    def request(self, method, path, body=None, **k):
        asked.append((method, path))
        if path == "/events/77":
            return event
        if path == "/take":
            return {"taken_at": int(time.time())}
        raise AssertionError(path)
    monkeypatch.setattr(ade_link.Link, "request", request)
    monkeypatch.setattr(ade_link, "_taking_session", lambda: None)
    try:
        code = ade_link.main(["--dir", str(rig.dir), "take", "77"])
    except SystemExit as e:
        code = e.code
    out, _ = capsys.readouterr()
    assert code == 2 and "an answer to a prompt is the prompt hook's to claim, never take's" in out
    assert ("POST", "/take") not in asked and "TAKEN BY YOU" not in out


# ---- the settings blocks ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ade_link.settings.json", "ade_link_and_hook.settings.json"])
def test_the_blocks_give_every_event_of_the_prompts_to_one_program_and_the_prompt_its_own_timeout(name):
    hooks = hook_templates.template(name)["hooks"]
    for event in NEW_EVENTS:
        assert event in hooks, (name, event)
        mine = [h for b in hooks[event] for h in b["hooks"] if "args" in h and h["args"][-1] == "permission"]
        assert len(mine) == 1, (name, event)
        (h,) = mine
        assert hook_templates.program(h) == [PROGRAM, "-I", "-X", "utf8", "-c", TRAMPOLINE,
                                             f"{hook_templates.HOME}/.proofpane-ade/ade_link.py", "--dir",
                                             f"{hook_templates.HOME}/.proofpane-ade/NAME", "permission"]
        assert "onFailure" not in h and h["type"] == "command"
        assert h["timeout"] == (1860 if event == "PermissionRequest" else 30)
    (block,) = hooks["PermissionRequest"]
    assert block["matcher"] == "*" and hooks["PermissionRequest"][0]["hooks"][0]["timeout"] == \
        ade_link.PROMPT_HOOK_TIMEOUT > ade_link.HOOK_DEADLINE + ade_link.PROMPT_WAIT + ade_link.PROMPT_LAST_GRACE
    for event in ("PostToolUse", "PostToolUseFailure"):
        assert [b["matcher"] for b in hooks[event]] == ["*"]
    (stop,) = hooks["Stop"]
    assert stop["hooks"][0]["command"].endswith(" turn end || true")          # beside turn end, which stays first
    assert hook_templates.program(stop["hooks"][1])[-1] == "permission"


def test_a_prompt_hook_whose_client_cannot_run_never_holds_and_one_that_runs_prints_nothing(tmp_path):
    """Python exits 2 when it cannot open its script, which a Stop or UserPromptSubmit hook takes as a hold and a
    PermissionRequest hook as a denial: so the program runs the client through runpy, which exits 1 then, a failure
    that holds nothing. With the client there and no link's directory, every event exits 0 printing nothing."""
    blocks = hook_templates.template("ade_link.settings.json")["hooks"].get("PermissionRequest")
    assert blocks, "the block gives no PermissionRequest hook"
    (block,) = blocks
    home = tmp_path / "home"
    (home / ".proofpane-ade").mkdir(parents=True)
    argv = hook_templates.argv(block["hooks"][0], sys.executable, str(home))
    for event in NEW_EVENTS:
        r = subprocess.run(argv, input=json.dumps({"hook_event_name": event, "session_id": SESSION}),
                           capture_output=True, text=True, timeout=60, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
        assert r.returncode == 1 and r.stdout == "", (event, r.returncode, r.stderr)
    shutil.copy(ade_link.__file__, home / ".proofpane-ade" / "ade_link.py")
    for event in NEW_EVENTS:
        r = subprocess.run(argv, input=json.dumps({"hook_event_name": event, "session_id": SESSION}),
                           capture_output=True, text=True, timeout=60, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), (event, r.stderr)
    plain = [a for a in argv if a not in ("-c", TRAMPOLINE)]
    os.unlink(home / ".proofpane-ade" / "ade_link.py")
    r = subprocess.run(plain, input="{}", capture_output=True, text=True, timeout=60,
                       env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 2                                       # the control: run plainly, it would have held


# ---- a linked session's proposal -------------------------------------------------------------------------------------

def command(capsys, *argv):
    try:
        code = ade_link.main([str(a) for a in argv])
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


def test_propose_seals_the_file_s_words_to_the_owner_for_the_session_named_and_prints_its_seq(rig, capsys, tmp_path):
    words = tmp_path / "proposal.txt"
    words.write_text("Run the migration check on the staging copy,\nthen reply with the counts.")
    code, out, err = command(capsys, "--dir", rig.dir, "propose", "--to", "cloud", "--file", words)
    assert (code, out) == (0, "ADE: proposed as seq 41\n"), err
    (posted,) = rig.ade.posts()
    assert set(posted) == {"client_id", "kind", "for", "text"} and posted["kind"] == "proposal"
    assert posted["for"] == "cloud" and len(posted["client_id"]) == 32
    value = open_envelope(posted["text"], slot(ACCOUNT, LINK_ID, "event", "proposal", None, None,
                                               posted["client_id"]), rig.private)
    assert value == {"text": words.read_text(), "session": None, "said_at": None}


@pytest.mark.parametrize("why", ["clear", "hidden", "control", "secret"])
def test_propose_refuses_locally_an_account_that_does_not_seal_and_words_past_the_rules(rig, capsys, tmp_path, why):
    words = tmp_path / "proposal.txt"
    words.write_text({"clear": "ship it", "hidden": "ship it\u200b now", "control": "ship\x1b[2J it",
                      "secret": "use sk-ant-api03-" + "a" * 40}[why])
    if why == "clear":
        rig.mode = ("clear",)
    code, out, err = command(capsys, "--dir", rig.dir, "propose", "--to", "cloud", "--file", words)
    assert code == 2 and out == "" and rig.ade.requests == []
    assert {"clear": "its words go to the owner only sealed", "hidden": "The proposal holds U+200B",
            "control": "The proposal holds a control character (U+001B)",
            "secret": "shaped like a secret"}[why] in err, err


# ---- end to end: the hook against the app served on a local port --------------------------------------------------
#
# The account's encryption turned on as the page turns it on, a session linked with the owner's passkey pinned and the
# account's statement taken (test_e2e_end_to_end's Page and paired), the hook run as Claude Code runs it, and the
# owner acting in the page while the hook waits: at its first wait between two reads of its prompt.

from conftest import signed_up  # noqa: E402
from proofpane_open import store  # noqa: E402
from proofpane_open.ade import link_commands  # noqa: E402
from test_ade_link_client import run, served  # noqa: E402,F401  (served: a fixture)
from test_e2e_end_to_end import Page, opened as page_opened, paired  # noqa: E402
from test_passkeys import add  # noqa: E402


class Owner:
    """The owner at the page: the account, its passkey and keys, and the link the session paired."""


@pytest.fixture
def owner_page(client, served, tmp_path, monkeypatch, capsys):  # noqa: F811
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "ADE_OWNER_KEYS", "ADE_LINK_DIR",
                 "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "machine" / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "machine" / "claude"))
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "0")
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_RECOVERY_HOLD", "0")
    monkeypatch.setattr(ade_link, "_history_soon", lambda link, every=None: None)
    monkeypatch.setattr(ade_link, "_moves_soon", lambda link: None)
    o = Owner()
    o.client, o.headers = client, signed_up(client)
    o.device = Authenticator(rp="127.0.0.1", origin=served)
    assert add(client, o.headers, o.device).status_code == 200
    o.page = Page(client, o.headers, o.device)
    o.page.turn_on()
    o.dir, o.link_id, out, err = paired(o.page, served, capsys, tmp_path, "mac")
    assert "goes sealed to key" in out, (out, err)
    o.work = tmp_path / "work"
    o.work.mkdir()
    o.clock = Clock()
    monkeypatch.setattr(ade_link, "wall", o.clock, raising=False)
    o.acts = []                             # what the owner (or the window) does at the hook's waits, in turn
    o.sent = []

    def waits(seconds):
        o.clock.sleep(seconds)
        if o.acts:
            o.acts.pop(0)()
    monkeypatch.setattr(ade_link, "sleep", waits)
    real = ade_link.Link.request

    def recording(self, method, path, body=None, *a, **k):
        o.sent.append((method, path))
        return real(self, method, path, body, *a, **k)
    monkeypatch.setattr(ade_link.Link, "request", recording)
    return o


def at_the_hook(o, monkeypatch, capsys, given):
    """The hook run as Claude Code runs it, for the linked session's directory."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(given)))
    try:
        code = ade_link.main(["--dir", str(o.dir), "permission"])
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


def prompt_asking(o, tool="Bash", tool_input=None, **over):
    given = {"hook_event_name": "PermissionRequest", "session_id": SESSION, "transcript_path": "/x/t.jsonl",
             "cwd": str(o.work), "permission_mode": "default", "tool_name": tool,
             "tool_input": {"command": "npm test", "description": "Run the tests"} if tool_input is None
             else tool_input}
    given.update(over)
    return given


def inbox(o):
    r = o.client.get("/api/ade/link/sessions", headers=o.headers)
    assert r.status_code == 200, r.text
    return r.json()


def the_prompt(o):
    """The newest prompt in the owner's list, as the page reads it."""
    prompts = inbox(o).get("prompts") or []
    assert prompts, "no prompt in the owner's list"
    return prompts[-1]


def opened_item(o, p):
    """A prompt's text opened as the page opens it, with the account's keys and its slot rebuilt from the view."""
    value = page_opened(o.page.unlock(), p["text"], slot(o.page.account, o.link_id, "event", "prompt", None, None,
                                                         p["client_id"]))
    return json.loads(value["text"])


def owner_answers(o, seq, text, words, **over):
    """The owner's answer, signed with his passkey as the page signs it: its ref the prompt and its digest that of the
    prompt's text as stored. The route's answer."""
    n = o.client.post("/api/ade/link/nonce", headers=o.headers, json={"purpose": "answer"}).json()
    p = {"v": 2, "deployment": n["deployment"], "purpose": "answer", "account": o.page.account, "nonce": n["nonce"],
         "id": uuid.uuid4().hex, "to": o.link_id, "ref": seq, "digest": link_commands.text_digest(text),
         "text": words, "created_at": n["server_time"], "expires_at": n["server_time"] + 3600}
    p.update(over)
    return o.client.post("/api/ade/link/commands", headers=o.headers,
                         json={"payload": p, "credential": o.device.get(as_step(canonicalize(p)))})


def row(seq):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_link_events WHERE seq = ?", (seq,)).fetchone())


def set_deadline(seq, at):
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET deadline_at = ? WHERE seq = ?", (at, seq))


@pytest.mark.parametrize("words, wanted", [
    ("Approve", {"behavior": "allow"}),
    ("Run the unit tests first, then ask again.", {"behavior": "deny",
                                                   "message": "Run the unit tests first, then ask again."}),
    ("Deny", {"behavior": "deny", "message": "Denied in the owner's list", "interrupt": True}),
])
def test_a_permission_prompt_reaches_the_list_sealed_whole_and_only_the_owner_s_signed_answer_decides_it(
        owner_page, monkeypatch, capsys, words, wanted):
    """The owner's answers. The owner's Close, which in an older release ended the hook with the prompt left to the
    window, now hides the prompt and the hook goes on waiting: test_inbox_close_end_to_end.py checks it, answered from
    the list of closed prompts and left to its deadline."""
    o = owner_page
    seen = {}

    def the_owner():
        p = the_prompt(o)
        seen.update(p, item=opened_item(o, p))
        r = owner_answers(o, p["seq"], p["text"], words)
        assert r.status_code == 200, r.text
    o.acts.append(the_owner)
    code, out, err = at_the_hook(o, monkeypatch, capsys, prompt_asking(o))
    assert code == 0 and err == ""
    # it reached the list within one read: sealed, the session's own report, marked to be answered from the list
    assert seen["look"] is False and seen["link_id"] == o.link_id and seen["from"] == "mac"
    assert seen["text"].startswith("e2e1.") and "npm test" not in seen["text"]
    assert seen["item"] == {"tool": "Bash", "input": {"command": "npm test", "description": "Run the tests"},
                            "session_id": SESSION, "agent_id": None, "deadline": seen["deadline_at"]}
    assert decision(out) == wanted
    answer_row = row(row(seen["seq"])["closed_by"])
    assert row(seen["seq"])["closed_reason"] == "answer" and answer_row["taken_by"] == o.link_id


@pytest.mark.parametrize("case", ["a hidden character", "a control character", "pin --add", "a push to main",
                                  "an edit to Claude Code's settings"])
def test_a_text_past_the_rules_and_a_step_kept_in_person_are_items_to_look_at(owner_page, monkeypatch, capsys,
                                                                              case):
    o = owner_page
    client = str(o.dir.parent / "ade_link.py")
    given = {"a hidden character": prompt_asking(o, tool_input={"command": "cat note\u200b.txt"}),
             "a control character": prompt_asking(o, tool_input={"command": "printf 'a\x1b[2Jb'"}),
             "pin --add": prompt_asking(o, tool_input={"command": f"python3 {client} --dir {o.dir} pin --add "
                                                                  + "1" * 32}),
             "a push to main": prompt_asking(o, tool_input={"command": "git push origin main"}),
             "an edit to Claude Code's settings": prompt_asking(o, "Edit", {
                 "file_path": str(o.work / ".claude" / "settings.local.json"), "old_string": "a",
                 "new_string": "b"})}[case]
    o.acts.append(lambda: pytest.fail("the hook waited on an item to look at"))
    code, out, _ = at_the_hook(o, monkeypatch, capsys, given)
    assert (code, out) == (0, "")
    p = the_prompt(o)
    assert p["look"] is True and [m for m, path in o.sent if path.startswith("/events/")] == []
    item = opened_item(o, p)
    if case in ("a hidden character", "a control character"):
        # its text is not posted: the tool's name alone, never a cut or an escape of the call
        assert item == {"tool": "Bash", "session_id": SESSION, "agent_id": None, "deadline": p["deadline_at"]}
    else:
        assert item["input"] == given["tool_input"]
    r = owner_answers(o, p["seq"], p["text"], "Approve")
    assert r.status_code == 409 and r.json()["detail"] == "An item to look at is answered where the session shows it."


QUESTION = {"questions": [
    {"question": "Which database?", "header": "Database", "multiSelect": False,
     "options": [{"label": "Postgres", "description": "production's"}, {"label": "SQLite"}]},
    {"question": "Which checks?", "header": "Checks", "multiSelect": False,
     "options": [{"label": "unit"}, {"label": "lint"}]}]}


@pytest.mark.parametrize("words", ["1,3", "1,5", "1"])
def test_a_question_s_options_are_numbered_and_only_numbers_it_posted_answer_it(owner_page, monkeypatch,
                                                                               capsys, words):
    o = owner_page
    seen = {}

    def the_owner():
        p = the_prompt(o)
        seen.update(item=opened_item(o, p))
        r = owner_answers(o, p["seq"], p["text"], words)
        assert r.status_code == 200, r.text
    o.acts.append(the_owner)
    code, out, _ = at_the_hook(o, monkeypatch, capsys, prompt_asking(o, "AskUserQuestion", copy.deepcopy(QUESTION)))
    assert code == 0
    assert [[(op["number"], op["label"]) for op in q["options"]] for q in seen["item"]["questions"]] == \
        [[(1, "Postgres"), (2, "SQLite")], [(3, "unit"), (4, "lint")]]
    if words == "1,3":
        assert decision(out) == {"behavior": "allow", "updatedInput": dict(
            QUESTION, answers={"Which database?": "Postgres", "Which checks?": "unit"})}
    else:
        assert out == ""                    # 5 was never posted; 1 leaves a question unanswered


def test_a_question_with_a_field_for_text_is_an_item_to_look_at(owner_page, monkeypatch, capsys):
    o = owner_page
    given = prompt_asking(o, "AskUserQuestion", {"questions": [{"question": "Which name?", "header": "Name",
                                                                 "multiSelect": False, "options": []}]})
    assert at_the_hook(o, monkeypatch, capsys, given)[:2] == (0, "")
    assert the_prompt(o)["look"] is True


def test_a_prompt_s_answer_is_the_hook_s_never_the_model_s_and_one_not_claimed_in_time_is_not_delivered(
        owner_page, monkeypatch, capsys):
    o = owner_page
    seen = {}

    def the_owner_answers_late():
        p = the_prompt(o)
        r = owner_answers(o, p["seq"], p["text"], "Approve")
        assert r.status_code == 200, r.text
        seen.update(seq=p["seq"], answer=r.json()["seq"])
        set_deadline(p["seq"], int(time.time()) - 1)        # its deadline passes before the hook reads it again
    o.acts.append(the_owner_answers_late)
    code, out, _ = at_the_hook(o, monkeypatch, capsys, prompt_asking(o))
    assert (code, out) == (0, "")
    answer_row = row(seen["answer"])
    assert answer_row["taken_at"] is None and answer_row["needs_turn"] == 0       # recorded as given, not delivered
    assert row(seen["seq"])["closed_reason"] == "answer"                           # and the prompt never reopens
    assert seen["seq"] not in [p["seq"] for p in inbox(o).get("prompts") or []]
    # the watcher does not wake the model for it, and take refuses it, here and at the ADE
    clock = Clock()
    monkeypatch.setattr(ade_link, "clock", clock)
    monkeypatch.setattr(ade_link, "sleep", clock.sleep)
    code, out, err = run(capsys, "--dir", o.dir, "watch", "--max", 60, "--every", 30)
    assert code == 0 and f"[{seen['answer']}]" not in out and "need your turn" not in out, (out, err)
    code, out, _ = run(capsys, "--dir", o.dir, "take", seen["answer"])
    assert code == 2 and "an answer to a prompt is the prompt hook's to claim, never take's" in out
    link = ade_link.Link(str(o.dir))
    with pytest.raises(ade_link.Refused) as refused:
        link.request("POST", "/take", {"seq": seen["answer"], "instance": "x" * 32})
    assert refused.value.status == 409 and refused.value.body.get("why") == "prompt"
    assert row(seen["answer"])["taken_at"] is None


@pytest.mark.parametrize("asked", ["a permission prompt", "a question"])
def test_the_session_s_report_closes_its_item_and_an_answer_after_it_is_refused(owner_page, monkeypatch,
                                                                                 capsys, asked):
    o = owner_page
    given = prompt_asking(o) if asked == "a permission prompt" else \
        prompt_asking(o, "AskUserQuestion", copy.deepcopy(QUESTION))
    seen = {}

    def the_window_answers_first():
        p = the_prompt(o)
        seen.update(p)
        ran = dict(given, hook_event_name="PostToolUse", tool_response={"ok": True}, tool_use_id="toolu_01")
        if asked == "a question":
            ran["tool_input"] = dict(given["tool_input"], answers={"Which database?": "SQLite",
                                                                   "Which checks?": "lint"})
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(ran)))
        assert ade_link.main(["--dir", str(o.dir), "permission"]) == 0
    o.acts.append(the_window_answers_first)
    code, out, _ = at_the_hook(o, monkeypatch, capsys, given)
    assert (code, out) == (0, "")
    assert row(seen["seq"])["closed_reason"] == "report:PostToolUse"
    with store.connect() as db:
        reports = db.execute("SELECT * FROM ade_link_events WHERE ref = ? AND kind = 'note'", (seen["seq"],)).fetchall()
    assert [(r["client_id"], r["text"], r["link_id"]) for r in reports] == \
        [(f"report:{seen['seq']}", "reported: PostToolUse", o.link_id)]
    r = owner_answers(o, seen["seq"], seen["text"], "Approve")
    assert r.status_code == 409 and r.json()["detail"] == "The ref is not an open prompt that session posted."


def test_the_deadline_closes_an_item_and_the_hook_refuses_it_with_words_naming_the_deadline(owner_page,
                                                                                             monkeypatch,
                                                                                             capsys):
    """This case once held that the deadline's close printed nothing, which left
    the prompt to its window. Now the hook refuses it with words that name the deadline it posted, and the turn goes
    on. The ADE's deadline is moved past here, so the read that follows records its close; nothing is claimed."""
    o = owner_page
    seen = {}

    def no_one_answers():
        p = the_prompt(o)
        seen.update(p)
        set_deadline(p["seq"], int(time.time()) - 1)
    o.acts.append(no_one_answers)
    code, out, err = at_the_hook(o, monkeypatch, capsys, prompt_asking(o))
    assert code == 0 and err == "" and decision(out) == unanswered(seen["deadline_at"])
    assert row(seen["seq"])["closed_reason"] == "deadline"
    assert ("POST", f"/prompts/{seen['seq']}/claim") not in o.sent


def test_an_agent_s_prompt_is_posted_at_once_and_another_session_s_report_closes_nothing_of_this_one_s(
        owner_page, monkeypatch, capsys, served, tmp_path):  # noqa: F811
    o = owner_page
    o.acts.append(lambda: pytest.fail("the hook waited on an agent's prompt"))
    agent = prompt_asking(o, agent_id="a-1f2b", agent_type="general-purpose")
    assert at_the_hook(o, monkeypatch, capsys, agent)[:2] == (0, "")
    p = the_prompt(o)
    assert p["look"] is True and [path for m, path in o.sent if path.startswith(("/events/", "/prompts/"))] == []
    main = prompt_asking(o, "ExitPlanMode", {"plan": "1. ship it"})
    assert at_the_hook(o, monkeypatch, capsys, main)[:2] == (0, "")
    plan = the_prompt(o)
    assert plan["seq"] != p["seq"] and plan["look"] is True
    # another Claude Code session served by the same link's directory: its call and its turn's end close nothing here
    other = "b2c3d4e5-0000-4000-8000-000000000000"
    before = len(o.sent)
    for event, fields in (("PostToolUse", {"tool_name": "Bash", "tool_input": agent["tool_input"],
                                           "agent_id": "a-1f2b", "tool_response": {}}),
                          ("Stop", {"stop_hook_active": False}), ("UserPromptSubmit", {"prompt": "go"})):
        given = dict({"hook_event_name": event, "session_id": other, "cwd": str(o.work)}, **fields)
        assert at_the_hook(o, monkeypatch, capsys, given)[:2] == (0, "")
    assert len(o.sent) == before
    assert {q["seq"] for q in inbox(o)["prompts"]} >= {p["seq"], plan["seq"]}
    # another link's note naming the prompt closes nothing either
    cloud, _, _, _ = paired(o.page, served, capsys, tmp_path, "cloud")
    code, _, err = run(capsys, "--dir", cloud, "post", "note", "reported: Stop", "--ref", plan["seq"])
    assert code == 2 and "closed only by the session that posted it" in err
    assert plan["seq"] in {q["seq"] for q in inbox(o)["prompts"]}
    # this session's own turn ends: its main thread's item closes, the agent's stays for the agent's own report
    stop = {"hook_event_name": "Stop", "session_id": SESSION, "cwd": str(o.work), "stop_hook_active": False}
    assert at_the_hook(o, monkeypatch, capsys, stop)[:2] == (0, "")
    assert row(plan["seq"])["closed_reason"] == "report:Stop" and row(p["seq"])["closed_reason"] is None


# ---- a prompt the hook holds is not posted twice; a second-screen pin ------------------------------------------------
#
# Measured on 2026-11-02 (Claude Code 2.1.316): six seconds after a permission prompt no hook has answered,
# the Notification hook runs, notification_type "permission_prompt", message "Claude needs your permission to use
# TOOL". The prompt hook waits without answering, so every prompt it holds in the list gets one.

OTHER_SESSION = "c3d4e5f6-0000-4000-8000-000000000000"
BASH_NOTICE = "Claude needs your permission to use Bash"


def notified(rig, monkeypatch, message, session=SESSION, kind="permission_prompt"):
    """Claude Code's Notification hook for `session`, run as Claude Code runs it, inside whatever the test is in."""
    given = {"hook_event_name": "Notification", "session_id": session, "transcript_path": "/x/t.jsonl",
             "cwd": str(rig.work), "notification_type": kind, "message": message, "title": "Claude Code"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(given)))
    ade_link.main(["--dir", str(rig.dir), "permission"])


def notices(rig):
    """The items to look at posted for Notifications, as (session, message), opened as the page opens them."""
    out = []
    for b in rig.ade.posts("prompt"):
        item = json.loads(opened(rig, b))
        if item.get("event") == "Notification":
            out.append((item["session_id"], item["words"]["message"]))
    return out


@pytest.mark.parametrize("also", ["an ask that started no hook", "Session paused", "another session's ask",
                                  "while the prompt is still being posted"])
def test_the_notification_of_a_prompt_the_hook_holds_is_not_posted_twice_and_no_other_is_held_back(
        rig, monkeypatch, capsys, also):
    given = asking(rig)
    real = rig.ade.request

    def posting(method, path, body=None, **kw):
        if also == "while the prompt is still being posted" and (method, path) == ("POST", "/events") \
                and body.get("kind") == "prompt" and body.get("look") is False:
            notified(rig, monkeypatch, BASH_NOTICE)            # before the hook knows its prompt's seq
        return real(method, path, body, **kw)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, *a, **k: posting(*a, **k))

    def while_it_waits(seq, reads):
        if reads != 1:
            return
        if also != "while the prompt is still being posted":
            notified(rig, monkeypatch, BASH_NOTICE)            # Claude Code's notice of the prompt the hook holds
        if also == "Session paused":
            notified(rig, monkeypatch, "Session paused")
        elif also == "another session's ask":
            notified(rig, monkeypatch, BASH_NOTICE, session=OTHER_SESSION)
    rig.ade.on_read = while_it_waits
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "owner" if n > 1 else None,
                                   "open": n <= 1}
    assert hook(rig, monkeypatch, capsys, given)[:2] == (0, "")
    # the prompt is in the list once, to answer: no second item to look at repeats it
    assert (SESSION, BASH_NOTICE) not in notices(rig)
    assert [b["look"] for b in rig.ade.posts("prompt")][0] is False
    if also == "an ask that started no hook":
        # the call ran: its report drops the item; then an ask that starts no hook notifies, and is posted
        ran = dict(given, hook_event_name="PostToolUse", tool_response={}, tool_use_id="toolu_01")
        assert hook(rig, monkeypatch, capsys, ran)[:2] == (0, "")
        notified(rig, monkeypatch, "Claude needs your permission to use WebFetch")
        assert notices(rig) == [(SESSION, "Claude needs your permission to use WebFetch")]
    elif also == "Session paused":
        assert notices(rig) == [(SESSION, "Session paused")]
    elif also == "another session's ask":
        assert notices(rig) == [(OTHER_SESSION, BASH_NOTICE)]
    else:
        assert notices(rig) == []


def test_a_look_item_or_an_agent_s_prompt_does_not_hold_back_the_notification_of_the_same_session(rig, monkeypatch,
                                                                                                    capsys):
    """Only a prompt the owner can answer in the list repeats the notice: an item to look at (a push to main), and an
    agent's prompt, leave the session's Notification to be posted."""
    assert hook(rig, monkeypatch, capsys, asking(rig, tool_input={"command": "git push origin main"}))[:2] == (0, "")
    notified(rig, monkeypatch, BASH_NOTICE)
    assert hook(rig, monkeypatch, capsys, asking(rig, agent_id="a-1f2b"))[:2] == (0, "")
    notified(rig, monkeypatch, BASH_NOTICE)
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "owner" if n > 1 else None,
                                   "open": n <= 1}
    rig.ade.on_read = lambda seq, reads: notified(rig, monkeypatch, BASH_NOTICE) if reads == 1 else None
    assert hook(rig, monkeypatch, capsys, asking(rig))[:2] == (0, "")
    assert notices(rig) == [(SESSION, BASH_NOTICE), (SESSION, BASH_NOTICE)]          # the third one held back


def test_with_a_second_screen_pin_a_prompt_is_an_item_to_look_at_and_the_hook_ends_at_once(rig, monkeypatch, capsys):
    """The page signs in a browser, and a session whose pin takes only what was signed through ade_link.py sign
    (pin --second-screen on) verifies no such answer: its prompts are offered as items to look at, never as buttons
    that cannot work."""
    keys = json.loads((rig.dir / "owner_keys.json").read_text())
    (rig.dir / "owner_keys.json").write_text(json.dumps(dict(keys, second_screen=True)))
    answered(rig, "Approve")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert (code, out) == (0, "")
    (posted,) = rig.ade.posts()
    assert posted["look"] is True
    assert rig.ade.asked("GET", "/events/") == [] and rig.ade.asked("POST", "/prompts/") == []
    assert json.loads(opened(rig, posted))["input"] == {"command": "npm test", "description": "Run the tests"}
    assert "the owner's keys sign on a second screen here" in (rig.dir / "prompt.log").read_text()


# ---- a hook that ended holds nothing back; a call's report finds the prompt that let it run; the hook's time is
# bounded; a tool the guard cannot judge is only shown -------------------------------------------------------------


def ran(rig, monkeypatch, given, event="PostToolUse"):
    """Claude Code's report hook for the call `given` asked about, run inside whatever the test is in."""
    report = dict(given, hook_event_name=event, tool_response={"stdout": "ok"}, tool_use_id="toolu_0" + event[-1])
    report.pop("permission_suggestions", None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(report)))
    ade_link.main(["--dir", str(rig.dir), "permission"])


@pytest.mark.parametrize("how", ["his own words", "Deny", "Approve", "the owner's close", "a claim the ADE refused"])
def test_a_hook_that_has_ended_holds_back_no_notification_of_its_session(rig, monkeypatch, capsys, how):
    """Once the hook has printed its decision or ended with nothing printed, its item stays for the session's report
    but stands for no prompt the hook holds: a later ask of the same session that starts no hook (one Claude Code
    keeps for the person, or a request relayed for remote execution) notifies, and that notice is posted."""
    if how == "the owner's close":
        rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "owner", "open": False}
    else:
        answered(rig, {"his own words": "Not that one: run the unit tests first."}.get(how, how))
    if how == "a claim the ADE refused":
        def refused(seq, digest):
            raise ade_link.Refused(409, "not claimed", {"why": "expired"})
        rig.ade.claim = refused
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    printed = {"his own words": "deny", "Deny": "deny", "Approve": "allow"}.get(how)
    assert (decision(out)["behavior"] if printed else out) == (printed or "")
    rig.clock.t += 60                           # the turn goes on; an ask that starts no hook shows a minute later
    notified(rig, monkeypatch, BASH_NOTICE)
    assert notices(rig) == [(SESSION, BASH_NOTICE)]


def test_a_hook_that_was_killed_holds_back_no_notification(rig, monkeypatch, capsys):
    """An item whose hook went without ending it (Claude Code stopped it) holds nothing back: what holds a notice is a
    hook still running, not an item left in prompts.json."""
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    rig.ade.on_read = lambda seq, reads: None
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "owner" if n > 1 else None,
                                   "open": n <= 1}
    assert hook(rig, monkeypatch, capsys, asking(rig))[:2] == (0, "")
    items = json.loads((rig.dir / "prompts.json").read_text())
    (item,) = items
    item.update(waiting=gone.pid, ended=None)               # as a hook leaves it when it is killed while it waits
    (rig.dir / "prompts.json").write_text(json.dumps(items))
    notified(rig, monkeypatch, BASH_NOTICE)
    assert notices(rig) == [(SESSION, BASH_NOTICE)]


def test_while_the_hook_waits_a_notice_naming_another_tool_is_posted(rig, monkeypatch, capsys):
    """The notice held back is the one that names the prompt the hook holds: Claude Code's "Claude needs your
    permission to use TOOL" for that tool. Another tool's ask, which started no hook, is posted."""
    write_notice = "Claude needs your permission to use Write"

    def while_it_waits(seq, reads):
        if reads == 1:
            notified(rig, monkeypatch, BASH_NOTICE)
            notified(rig, monkeypatch, write_notice)
    rig.ade.on_read = while_it_waits
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "owner" if n > 1 else None,
                                   "open": n <= 1}
    assert hook(rig, monkeypatch, capsys, asking(rig))[:2] == (0, "")
    assert notices(rig) == [(SESSION, write_notice)]


def test_a_call_s_report_closes_the_prompt_that_let_it_run_not_an_older_one_the_owner_denied(rig, monkeypatch,
                                                                                               capsys):
    """The owner denies a call in the list with his words, so the turn goes on and the call never runs; asked again,
    the same call is allowed in the window while the hook waits. Its report closes the second item, whose hook then
    ends, and leaves the denied one to its turn's end."""
    answered(rig, "Not yet: wait for the build to finish.")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert decision(out)["behavior"] == "deny"                   # item 41: denied in the list, its call never ran
    rig.ade.view = None
    rig.ade.claim = None
    rig.ade.on_read = lambda seq, reads: ran(rig, monkeypatch, asking(rig)) if (seq, reads) == (42, 1) else None
    rig.clock.t += 120
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))    # item 42, allowed in the window
    assert (code, out) == (0, "")
    assert [(b["ref"], b["text"]) for b in rig.ade.posts("note")] == [(42, "reported: PostToolUse")]
    assert len(rig.ade.asked("GET", "/events/42")) == 1           # its hook stopped waiting at the report
    stop = {"hook_event_name": "Stop", "session_id": SESSION, "cwd": str(rig.work), "stop_hook_active": False}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stop)))
    ade_link.main(["--dir", str(rig.dir), "permission"])
    assert [(b["ref"], b["text"]) for b in rig.ade.posts("note")][1:] == [(41, "reported: Stop")]


def test_a_call_the_list_denied_is_not_closed_by_a_later_run_of_the_same_call(rig, monkeypatch, capsys):
    """The owner denies a call with his words and the turn goes on; the same call later runs with no prompt (a rule
    the session was given allows it). Its report closes nothing the hook denied: the denied item waits for its turn's
    end."""
    answered(rig, "Not yet: wait for the build to finish.")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
    assert decision(out)["behavior"] == "deny"
    ran(rig, monkeypatch, asking(rig))
    assert rig.ade.posts("note") == []
    stop = {"hook_event_name": "Stop", "session_id": SESSION, "cwd": str(rig.work), "stop_hook_active": False}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stop)))
    ade_link.main(["--dir", str(rig.dir), "permission"])
    assert [(b["ref"], b["text"]) for b in rig.ade.posts("note")] == [(41, "reported: Stop")]


def test_a_call_s_report_closes_the_newest_prompt_of_the_call_when_an_older_one_was_closed_in_the_list(
        rig, monkeypatch, capsys):
    """The owner closes a prompt in the list, which decides nothing: the window denies it with words, and the same
    call, asked again, is allowed in the window while the hook waits. Of the two items its report closes the newer,
    the prompt that let the call run."""
    rig.ade.view = lambda seq, n: {"seq": seq, "kind": "prompt", "closed_reason": "owner", "open": False}
    assert hook(rig, monkeypatch, capsys, asking(rig))[:2] == (0, "")       # item 41: closed in the list
    rig.ade.view = None
    rig.ade.on_read = lambda seq, reads: ran(rig, monkeypatch, asking(rig)) if (seq, reads) == (42, 1) else None
    rig.clock.t += 120
    assert hook(rig, monkeypatch, capsys, asking(rig))[:2] == (0, "")       # item 42, allowed in the window
    assert [(b["ref"], b["text"]) for b in rig.ade.posts("note")] == [(42, "reported: PostToolUse")]


def test_an_agent_s_prompt_hook_ends_within_its_bound_when_the_ade_never_answers(rig, monkeypatch, capsys):
    """An agent's prompt shows in the window only once its hooks end, so the hook posts it and ends at once: a request
    that never answers (a name lookup blocked in C, which no socket timeout ends) holds it no longer than the bound
    every other event of the hook has."""
    monkeypatch.setattr(ade_link, "PROMPT_EVENT_DEADLINE", 2)
    gate = threading.Event()

    def blocked(self, *a, **k):
        gate.wait(15)
        raise ade_link.Unreachable("no answer")
    monkeypatch.setattr(ade_link.Link, "request", blocked)
    started = time.monotonic()
    try:
        code, out, _ = hook(rig, monkeypatch, capsys, asking(rig, agent_id="a-1f2b", agent_type="general-purpose"))
        took = time.monotonic() - started
    finally:
        gate.set()
        time.sleep(0.3)                         # the posting thread, released, ends while the stand-ins are in place
    assert (code, out) == (0, "")
    assert took < 8, took


def test_a_prompt_whose_reads_never_answer_ends_by_its_deadline(rig, monkeypatch, capsys):
    """The hook's wait ends at the prompt's deadline even when a read of the prompt never answers: it prints nothing,
    and it never outlives the time Claude Code gives it. The wait's bound runs
    to the deadline and PROMPT_LAST_GRACE, the last reads' grace past it, which is set to 3 s here."""
    monkeypatch.setattr(ade_link, "PROMPT_WAIT", 3)
    monkeypatch.setattr(ade_link, "PROMPT_LAST_GRACE", 3, raising=False)
    gate = threading.Event()
    real = rig.ade.request

    def reading(self, method, path, body=None, **k):
        if method == "GET" and path.startswith("/events/"):
            gate.wait(30)
            raise ade_link.Unreachable("no answer")
        return real(method, path, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", reading)
    started = time.monotonic()
    try:
        code, out, _ = hook(rig, monkeypatch, capsys, asking(rig))
        took = time.monotonic() - started
    finally:
        gate.set()
        time.sleep(0.3)
    assert (code, out) == (0, "")
    assert took < 3 + 3 + 5, took


UNJUDGED = {
    "an MCP tool that merges to main": ("mcp__github__merge_pull_request",
                                        {"owner": "o", "repo": "r", "pullNumber": 7, "merge_method": "squash"}),
    "a fetch that sends data out": ("WebFetch", {"url": "https://example.com/?q=notes", "prompt": "Read it"}),
    "a search that sends data out": ("WebSearch", {"query": "the plan"}),
    "launching another agent": ("Agent", {"description": "Look", "prompt": "Find it",
                                          "subagent_type": "general-purpose"}),
    "launching another agent (Task)": ("Task", {"description": "Look", "prompt": "Find it",
                                                "subagent_type": "general-purpose"}),
    "a tool that needs the person": ("PublishPlugin", {"name": "x"}),
}


@pytest.mark.parametrize("case", list(UNJUDGED))
def test_a_tool_the_guard_cannot_judge_is_only_shown_never_decided_from_the_list(rig, monkeypatch, capsys, case):
    """The guard judges a shell command by its words and a file tool by where it writes, and lets any other tool
    through: it cannot tell an MCP tool's merge to main, a fetch that sends data out or an agent launched from a step
    it may take on a remote word. Such a prompt is an item to look at, sealed whole, and an answer is never asked
    for."""
    tool, given = UNJUDGED[case]
    answered(rig, "Approve")                    # an answer there would allow: the hook never asks for one
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig, tool, given))
    assert (code, out) == (0, "")
    (posted,) = rig.ade.posts()
    assert posted["look"] is True and posted["text"].startswith("e2e1.")
    item = json.loads(opened(rig, posted))
    assert item["tool"] == tool and item["input"] == given
    assert rig.ade.asked("GET", "/events/") == [] and rig.ade.asked("POST", "/prompts/") == []
    assert "a tool the guard does not judge" in (rig.dir / "prompt.log").read_text()


@pytest.mark.parametrize("tool, given", [("Read", {"file_path": "/etc/hosts"}), ("Grep", {"pattern": "TODO"}),
                                         ("Glob", {"pattern": "**/*.py"})])
def test_a_tool_that_takes_no_step_the_guard_keeps_is_answered_from_the_list(rig, monkeypatch, capsys, tool, given):
    """The control: reading takes none of the steps the guard keeps for the owner at the machine, so its prompt is
    answered in the list as a shell command's or a file tool's is."""
    answered(rig, "Approve")
    code, out, _ = hook(rig, monkeypatch, capsys, asking(rig, tool, given))
    assert decision(out) == {"behavior": "allow"}
    assert rig.ade.posts()[0]["look"] is False
